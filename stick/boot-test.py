#!/usr/bin/env python3
"""Boot a BackOtter image in QEMU and check the live system from inside.

Usage: boot-test.py IMG {uefi-secureboot,uefi,bios} [--media {usb,cdrom}]
                     [--log FILE] [--timeout S] [--manifest FILE] [--arch {amd64,arm64}]

Watches the first serial port (an image from "make-img.sh --test" boots with a
console there), picks "Shell" in BackOtter's console menu, runs the checks,
and powers off.  Exits 0 if the system came up and every check passed.  It
can also log in at a plain login prompt, for images without BackOtter's
layer, such as the stock Ubuntu ISO.

By default the image is a 16 GB USB stick, as if written to one raw: writes go
to a throwaway copy-on-write overlay, and the config partition checks run.
--media cdrom attaches it as a CD instead (as a BMC's virtual media may),
which has no partitions.

--manifest FILE also types PLATFORM/manifest.sh after the checks, and writes
its "BKO-MF KEY VALUE" lines to FILE as a JSON object (ImgTest2's image
manifest: testvm/test2/t2lib/img.py compares them with the last one).

Needs: qemu-system-x86_64 and qemu-img, with KVM; for the UEFI modes, OVMF (Debian/Ubuntu
package "ovmf").  uefi-secureboot uses OVMF's Secure Boot build with
Microsoft's keys enrolled, so it boots the image's signed shim and kernel the
way a stock PC with Secure Boot on would.

--arch arm64 (the default on an aarch64 host) boots an arm64 image instead:
qemu-system-aarch64's virt machine with KVM, AAVMF (package "qemu-efi-aarch64";
uefi-secureboot is its build with Microsoft's keys), a USB stick on xHCI or a
SCSI CD, and the NIC without a boot ROM.  ARM64 has no BIOS mode.  On a
mixed-core host, run it where QEMU's CPUs are one core type (Bigfoot's
bko.slice): see docs/design/arm64.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLATFORMS = ("linux", "freebsd", "netbsd", "openbsd", "dragonfly", "illumos", "haiku")
OVMF = "/usr/share/OVMF"
FIRMWARE = {
    "uefi-secureboot": ("OVMF_CODE_4M.secboot.fd", "OVMF_VARS_4M.ms.fd"),
    "uefi": ("OVMF_CODE_4M.fd", "OVMF_VARS_4M.fd"),
}
# arm64's firmware, as testvm/vm-run takes it (docs/design/arm64.md, A0 and A1).
AAVMF = "/usr/share/AAVMF"
FIRMWARE_ARM64 = {
    "uefi-secureboot": ("AAVMF_CODE.ms.fd", "AAVMF_VARS.ms.fd"),
    "uefi": ("AAVMF_CODE.no-secboot.fd", "AAVMF_VARS.fd"),
}
ARCHES = ("amd64", "arm64")
# QEMU hardware for platforms that can't use the default, q35 with qemu-xhci:
# DragonFly 6.4's xhci enumerates no QEMU USB device (plain, behind a hub, or
# UAS), and on q35 its interrupts go astray (AHCI timeouts, then a hang just
# before it mounts root).  It boots on the older PC machine with EHCI.
# FreeBSD 15.1 reads QEMU's USB stick on xHCI garbled (umass: "bad CSW
# signature") unless kern.maxphys is cut to 64 KiB, and the root never
# mounts; on EHCI it boots as it would from a real stick.  illumos runs on
# the PC machine, as the omnios58 test guest does.
MACHINE = {"dragonfly": "pc", "illumos": "pc"}
USB_CONTROLLER = {"dragonfly": "usb-ehci", "freebsd": "usb-ehci"}
# Bytes a second to type at the serial console, for platforms that drop input
# coming faster than a real line would bring it: QEMU's serial port has no
# speed of its own, and DragonFly's sio buffers only a few dozen characters
# between interrupts at the 9600 baud it runs at.
TYPING_RATE = {"dragonfly": 960}
# What wakes a console that went quiet.  NetBSD 11.1's com(4) on QEMU's 16550 now and then
# misses the port's interrupt (the netbsd-uefi flake, img-speed I4, 2026-11-27: about 1 boot
# in 125 with four guests on a busy host, BIOS and UEFI alike): the guest's output, or the
# rest of a line typed to it, then waits until another byte reaches the port, and the check
# timed out with the guest idle.  After QUIET_S seconds of silence the console gets ^T, the
# tty's status character: the kernel prints the foreground command (name, pid, wait channel:
# a real hang is then named in the log) and the shell reads nothing.  While waiting for a
# prompt it gets Enter instead: a fresh prompt, as when login's syslog line from ttyE1 lands
# just after the shell's and the prompt is no longer the last thing printed.  Never before
# BackOtter's menu: a key in the boot loader's countdown would stop it.
NUDGE = {"netbsd": "\x14"}
QUIET_S = 15
# How the checks end, where "shutdown -p now" doesn't power off.
POWEROFF = {"linux": "poweroff", "illumos": "poweroff", "haiku": "shutdown -q"}
# Set by the casper.conf inside the server ISO's initrd (the installer layer's
# /etc/casper.conf says "ubuntu", but that copy isn't the one casper reads).
LIVE_USER = "ubuntu-server"
PROMPT = re.compile(rb"[$#] $")
MENU = re.compile(rb"Choose \[1-4\]: ")
LOGIN = re.compile(rb"login: ")
# Linux, then the BSDs' panics (at a line's start, or after NetBSD's
# timestamp: not NetBSD's "ddb.onpanic: 1 -> 0"), debugger and root-device
# prompts.
FAILURE = re.compile(rb"Kernel panic|\(initramfs\) |BusyBox v[0-9]|(?:^|\] )panic: |^db> |mountroot> |root device: ", re.M)
# Computed by the guest, so the command's own echo can't match it.
DONE = re.compile(rb"BKO-42-DONE\r?\n")

# Each check prints one line: "BKO-CHECK <name> <ok|FAIL> <detail>".  The
# checks themselves are per platform: PLATFORM/checks.sh, then, on a USB
# stick, PLATFORM/config-checks.sh (the config partition).
CHECK_FUNCTION = r"""
check() { n=$1; shift; if out=$(sh -c "$*" 2>&1); then r=ok; else r=FAIL; fi
          echo "BKO-CHECK $n $r $(echo "$out" | tr '\n' ' ' | cut -c1-200)"; }
"""
# The manifest's lines (--manifest): "BKO-MF <key> <value>", one per mf, "-" for
# what its command didn't print.  (Its stdin is /dev/null: a command that reads
# one mustn't eat what is typed next.)
MANIFEST_FUNCTION = r"""
mf() { n=$1; shift; out=$(sh -c "$*" 2>/dev/null </dev/null); echo "BKO-MF $n $(echo "${out:--}" | tr '\n' ' ' | cut -c1-200)"; }
"""
DONE_LINE = "echo BKO-$((6 * 7))-DONE"


class Console:
    """The guest's serial port, read through QEMU's stdout."""

    def __init__(self, proc: subprocess.Popen[bytes], log, rate: int | None = None,
                 nudge: str | None = None) -> None:
        self.proc = proc
        self.log = log
        self.buf = b""
        self.rate = rate
        self.nudge = nudge    # NUDGE's: sent after QUIET_S s of silence
        self.notes: list[str] = []

    def expect(self, *patterns: re.Pattern[bytes], timeout: float, nudge: str | None = None) -> int:
        """Wait until one of the patterns matches; return its index.  On a console with a
        nudge (NUDGE's), each QUIET_S s of silence sends it (or this wait's NUDGE; "" none),
        and self.notes says so."""
        deadline = time.monotonic() + timeout
        assert self.proc.stdout is not None
        nudge = (self.nudge if nudge is None else nudge) if self.nudge else None
        last = time.monotonic()   # the last byte heard, or the last nudge
        nudged = 0
        answered = True           # something heard since the first nudge
        while True:
            for i, pat in enumerate(patterns):
                m = pat.search(self.buf)
                if m:
                    self.buf = self.buf[m.end():]
                    if nudged:
                        self.notes.append(f"the console was quiet {QUIET_S:g} s waiting for {pat.pattern!r}: "
                                          f"nudged {nudged} time(s) with {nudge!r}, then it answered")
                    return i
            now = time.monotonic()
            left = deadline - now
            if left <= 0:
                why = f"no match for {[p.pattern for p in patterns]}"
                if nudged and not answered:
                    why += f"; the console stayed silent through {nudged} nudge(s) with {nudge!r}"
                    self.notes.append(why)
                raise TimeoutError(why)
            if nudge and now - last >= QUIET_S:
                self.send(nudge)
                if not nudged:
                    answered = False
                nudged += 1
                last = now
            ready, _, _ = select.select([self.proc.stdout], [], [], max(0.01, min(left, 1.0, QUIET_S)))
            if ready:
                chunk = os.read(self.proc.stdout.fileno(), 65536)
                if not chunk:
                    raise EOFError("QEMU exited")
                self.log.write(chunk)
                self.log.flush()
                self.buf = (self.buf + chunk)[-65536:]
                last = time.monotonic()
                answered = True

    def send(self, text: str) -> None:
        assert self.proc.stdin is not None
        data = text.encode()
        step = 16 if self.rate else len(data)
        for i in range(0, len(data), step):
            self.proc.stdin.write(data[i:i + step])
            self.proc.stdin.flush()
            if self.rate:
                time.sleep(step / self.rate)


def host_arch() -> str:
    """The build host's architecture, in Debian's names: what --arch defaults to."""
    return "arm64" if os.uname().machine in ("aarch64", "arm64") else "amd64"


def qemu_command_arm64(img: str, mode: str, media: str, tmp: str) -> list[str]:
    """arm64's QEMU: the virt machine with the host's GIC and CPU, AAVMF in flash (its code
    read-only, a copy of its variables), as vm-run boots an arm64 guest."""
    if mode not in FIRMWARE_ARM64:
        raise ValueError(f"arm64 has no {mode} mode: uefi or uefi-secureboot")
    cmd = [
        "qemu-system-aarch64", "-enable-kvm", "-machine", "virt,gic-version=host", "-cpu", "host",
        "-smp", "2", "-m", "4096", "-display", "none", "-serial", "stdio", "-monitor", "none",
        # virt's default NIC wants iPXE's efi-virtio.rom; the image never network-boots.
        "-netdev", "user,id=net0", "-device", "virtio-net-pci,netdev=net0,romfile=",
    ]
    if media == "cdrom":
        # virt has no IDE: the CD is on virtio-scsi.
        cmd += [
            "-device", "virtio-scsi-pci,id=scsi0",
            "-drive", f"if=none,id=cd,file={img},format=raw,media=cdrom,readonly=on",
            "-device", "scsi-cd,bus=scsi0.0,drive=cd,bootindex=1",
        ]
    else:
        overlay = os.path.join(tmp, "stick.qcow2")
        subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", "-F", "raw",
                        "-b", os.path.abspath(img), overlay, "16G"], check=True)
        cmd += [
            "-device", "qemu-xhci,id=usb",
            "-drive", f"if=none,id=stick,format=qcow2,file={overlay}",
            "-device", "usb-storage,bus=usb.0,drive=stick,bootindex=1",
        ]
    code, vars_template = FIRMWARE_ARM64[mode]
    vars_copy = os.path.join(tmp, "AAVMF_VARS.fd")
    shutil.copy(os.path.join(AAVMF, vars_template), vars_copy)
    return cmd + [
        "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={os.path.join(AAVMF, code)}",
        "-drive", f"if=pflash,format=raw,unit=1,file={vars_copy}",
    ]


def qemu_command(img: str, platform: str, mode: str, media: str, tmp: str,
                 arch: str = "amd64") -> list[str]:
    if arch == "arm64":
        if platform != "linux":
            raise ValueError(f"arm64 has no {platform} image: linux only")
        return qemu_command_arm64(img, mode, media, tmp)
    cmd = [
        "qemu-system-x86_64", "-enable-kvm", "-cpu", "host", "-smp", "2", "-m", "4096",
        "-display", "none", "-serial", "stdio", "-monitor", "none",
    ]
    if media == "cdrom":
        cmd += ["-drive", f"file={img},media=cdrom,readonly=on"]
    else:
        # A stick bigger than the image, like a real one: the image's backup
        # GPT header then isn't at the end of the disk.
        overlay = os.path.join(tmp, "stick.qcow2")
        subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", "-F", "raw",
                        "-b", os.path.abspath(img), overlay, "16G"], check=True)
        cmd += [
            "-device", USB_CONTROLLER.get(platform, "qemu-xhci") + ",id=usb",
            "-drive", f"if=none,id=stick,format=qcow2,file={overlay}",
            "-device", "usb-storage,bus=usb.0,drive=stick,bootindex=1",
        ]
    machine = MACHINE.get(platform, "q35")
    if mode == "bios":
        return cmd + ["-machine", machine]
    code, vars_template = FIRMWARE[mode]
    vars_copy = os.path.join(tmp, "OVMF_VARS.fd")
    shutil.copy(os.path.join(OVMF, vars_template), vars_copy)
    # SMM, and flash only SMM may write, for Secure Boot's firmware alone:
    # OVMF_CODE_4M.fd has no SMM, and given them it keeps its variables in RAM.
    if mode == "uefi-secureboot":
        cmd += ["-machine", f"{machine},smm=on", "-global", "driver=cfi.pflash01,property=secure,value=on"]
    else:
        cmd += ["-machine", machine]
    cmd += [
        "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={os.path.join(OVMF, code)}",
        "-drive", f"if=pflash,format=raw,unit=1,file={vars_copy}",
    ]
    return cmd


def run(img: str, platform: str, mode: str, media: str, log_path: str, timeout: float,
        user: str, manifest: str | None = None, arch: str = "amd64") -> bool:
    with tempfile.TemporaryDirectory() as tmp, open(log_path, "wb") as log:
        proc = subprocess.Popen(qemu_command(img, platform, mode, media, tmp, arch),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        con = Console(proc, log, TYPING_RATE.get(platform), NUDGE.get(platform))
        start = time.monotonic()
        try:
            got = con.expect(MENU, LOGIN, FAILURE, timeout=timeout, nudge="")
            if got == 2:
                print(f"{mode}: boot FAILED (see {log_path})")
                return False
            boot_s = time.monotonic() - start
            if got == 1:
                # Either agetty's "login: root (automatic login)" on the way
                # to BackOtter's menu or a root shell, or a real login prompt.
                try:
                    got = con.expect(MENU, PROMPT, timeout=20, nudge="")
                except TimeoutError:
                    got = None
            as_root = True
            if got == 0:
                con.send("1\n")   # BackOtter's console: "Shell"
                con.expect(PROMPT, timeout=30, nudge="\r")
            elif got is None:
                con.send(user + "\n")
                if con.expect(re.compile(rb"Password: $"), PROMPT, timeout=30) == 0:
                    con.send("\n")   # casper's live user has an empty password
                    con.expect(PROMPT, timeout=30)
                as_root = False
            con.send("export TERM=dumb; stty -echo" + ("" if as_root else "; sudo -i") + "\n")
            con.expect(PROMPT, timeout=30, nudge="\r")
            # A line at a time, and after a check only once it has printed its
            # result: the guest's tty buffers little input (4 KB on Linux, 1 KB
            # on NetBSD), and drops what's typed beyond that while a slow
            # check runs.
            script = CHECK_FUNCTION + check_script(platform, media, arch)
            if manifest:
                script += MANIFEST_FUNCTION + manifest_script(platform)
            script += DONE_LINE
            for line in script.strip().splitlines():
                con.send(line + "\n")
                m = re.match(r"check (\S+) ", line)
                mf = re.match(r"mf (\S+) ", line)
                if m:
                    con.expect(re.compile(rb"BKO-CHECK " + re.escape(m[1].encode()) + rb" .*\n"),
                               timeout=120)
                elif mf:
                    con.expect(re.compile(rb"BKO-MF " + re.escape(mf[1].encode()) + rb" .*\n"),
                               timeout=120)
                else:
                    time.sleep(0.1)
            con.expect(DONE, timeout=120)
            results = re.findall(rb"BKO-CHECK (\S+) (ok|FAIL) ?(.*?)\r?\n",
                                 log_contents(log_path))
            print(f"{mode} ({media}): reached login in {boot_s:.0f} s")
            all_ok = bool(results)
            for name, status, detail in results:
                all_ok &= status == b"ok"
                print(f"  {status.decode():4} {name.decode():20} {detail.decode(errors='replace').strip()}")
            for note in con.notes:
                print(f"  note: {note}")
            if manifest:
                fields = manifest_fields(log_contents(log_path))
                write_manifest(manifest, fields)
                print(f"manifest: {len(fields)} fields in {manifest}")
            # OpenBSD has no poweroff; Linux's, illumos' and Haiku's shutdown
            # have no -p (Haiku's -q: without asking the desktop's applications).
            con.send(POWEROFF.get(platform, "shutdown -p now") + "\n")
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                pass
            return all_ok
        except (TimeoutError, EOFError) as e:
            for note in con.notes:
                if note != str(e):
                    print(f"  note: {note}")
            print(f"{mode}: {e} (see {log_path})")
            return False
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


ARCH_LINE = re.compile(r"\[(amd64|arm64)\] ")


def check_script(platform: str, media: str, arch: str = "amd64") -> str:
    """PLATFORM/checks.sh, and PLATFORM/config-checks.sh on a USB stick,
    without their leading comments (not every shell takes typed comments).
    A line that starts "[ARCH] " is one architecture's: typed without that
    prefix for ARCH's image, and left out for the other's."""
    names = ["checks.sh"] + (["config-checks.sh"] if media == "usb" else [])
    parts = []
    for name in names:
        with open(os.path.join(HERE, platform, name), encoding="utf-8") as f:
            lines = f.read().splitlines()
        while lines and (lines[0].startswith("#") or not lines[0].strip()):
            lines.pop(0)
        kept = []
        for line in lines:
            m = ARCH_LINE.match(line)
            if m and m[1] != arch:
                continue
            kept.append(line[m.end():] if m else line)
        parts.append("\n".join(kept))
    return "\n".join(parts) + "\n"


def manifest_script(platform: str) -> str:
    """PLATFORM/manifest.sh, without its leading comments."""
    with open(os.path.join(HERE, platform, "manifest.sh"), encoding="utf-8") as f:
        lines = f.read().splitlines()
    while lines and (lines[0].startswith("#") or not lines[0].strip()):
        lines.pop(0)
    return "\n".join(lines) + "\n"


def manifest_fields(log: bytes) -> dict[str, str]:
    """The serial log's "BKO-MF KEY VALUE" lines, as {KEY: VALUE}.  A prompt, and on Linux
    OSC 3008 sequences, come before the marker on its line; a KEY starting with $ is the
    function's own body, echoed while it was typed."""
    fields = {}
    for key, value in re.findall(rb"BKO-MF ([^\s$]\S*) ?(.*?)\r?$", log, re.M):
        fields[key.decode(errors="replace")] = value.decode(errors="replace").strip()
    return fields


def write_manifest(path: str, fields: dict[str, str]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(fields, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def log_contents(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("img", metavar="IMG")
    ap.add_argument("mode", choices=["uefi-secureboot", "uefi", "bios"])
    ap.add_argument("--platform", choices=PLATFORMS, default="linux",
                    help="which checks to run: PLATFORM/checks.sh (default: linux)")
    ap.add_argument("--media", choices=["usb", "cdrom"], default="usb",
                    help="attach the image as a USB stick (default) or a CD")
    ap.add_argument("--log", help="serial log file (default: boot-test-MODE-MEDIA.log)")
    ap.add_argument("--timeout", type=float, default=300, help="seconds to reach login")
    ap.add_argument("--user", default=LIVE_USER, help=f"live user (default: {LIVE_USER})")
    ap.add_argument("--manifest", metavar="FILE",
                    help="also type PLATFORM/manifest.sh and write its fields to FILE (JSON)")
    ap.add_argument("--arch", choices=ARCHES, default=host_arch(),
                    help="the image's architecture (default: this host's, %(default)s)")
    args = ap.parse_args()
    # A stop (stick/boot-modes.sh's, or a job's) unwinds run(): its finally kills QEMU.  Once:
    # a second signal (the group's TERM after boot-modes.sh's) must not cut that finally short.
    # A signal ignored on entry (nohup's HUP) stays ignored, as it was before these handlers.
    def stop(n, _f):
        for s in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(s, signal.SIG_IGN)
        sys.exit(128 + n)
    for s in (signal.SIGTERM, signal.SIGHUP):
        if signal.getsignal(s) is not signal.SIG_IGN:
            signal.signal(s, stop)
    if args.arch == "arm64" and args.mode not in FIRMWARE_ARM64:
        ap.error(f"arm64 has no {args.mode} mode: uefi or uefi-secureboot")
    if args.arch == "arm64" and args.platform != "linux":
        ap.error(f"arm64 has no {args.platform} image: linux only")
    ok = run(args.img, args.platform, args.mode, args.media,
             args.log or f"boot-test-{args.mode}-{args.media}.log", args.timeout, args.user,
             args.manifest, args.arch)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
