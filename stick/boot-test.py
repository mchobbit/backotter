#!/usr/bin/env python3
"""Boot a BackOtter image in QEMU and check the live system from inside.

Usage: boot-test.py IMG {uefi-secureboot,uefi,bios} [--media {usb,cdrom}]
                     [--log FILE] [--timeout S]

Watches the first serial port (an image from "make-img.sh --test" boots with a
console there), picks "Shell" in BackOtter's console menu, runs the checks,
and powers off.  Exits 0 if the system came up and every check passed.  It
can also log in at a plain login prompt, for images without BackOtter's
layer, such as the stock Ubuntu ISO.

By default the image is a 16 GB USB stick, as if written to one raw: writes go
to a throwaway copy-on-write overlay, and the config partition checks run.
--media cdrom attaches it as a CD instead (as a BMC's virtual media may),
which has no partitions.

Needs: qemu-system-x86_64 and qemu-img, with KVM; for the UEFI modes, OVMF (Debian/Ubuntu
package "ovmf").  uefi-secureboot uses OVMF's Secure Boot build with
Microsoft's keys enrolled, so it boots the image's signed shim and kernel the
way a stock PC with Secure Boot on would.
"""

from __future__ import annotations

import argparse
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLATFORMS = ("linux", "freebsd", "netbsd", "openbsd", "dragonfly")
OVMF = "/usr/share/OVMF"
FIRMWARE = {
    "uefi-secureboot": ("OVMF_CODE_4M.secboot.fd", "OVMF_VARS_4M.ms.fd"),
    "uefi": ("OVMF_CODE_4M.fd", "OVMF_VARS_4M.fd"),
}
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
DONE_LINE = "echo BKO-$((6 * 7))-DONE"


class Console:
    """The guest's serial port, read through QEMU's stdout."""

    def __init__(self, proc: subprocess.Popen[bytes], log) -> None:
        self.proc = proc
        self.log = log
        self.buf = b""

    def expect(self, *patterns: re.Pattern[bytes], timeout: float) -> int:
        """Wait until one of the patterns matches; return its index."""
        deadline = time.monotonic() + timeout
        assert self.proc.stdout is not None
        while True:
            for i, pat in enumerate(patterns):
                m = pat.search(self.buf)
                if m:
                    self.buf = self.buf[m.end():]
                    return i
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"no match for {[p.pattern for p in patterns]}")
            ready, _, _ = select.select([self.proc.stdout], [], [], min(left, 1.0))
            if ready:
                chunk = os.read(self.proc.stdout.fileno(), 65536)
                if not chunk:
                    raise EOFError("QEMU exited")
                self.log.write(chunk)
                self.log.flush()
                self.buf = (self.buf + chunk)[-65536:]

    def send(self, text: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(text.encode())
        self.proc.stdin.flush()


def qemu_command(img: str, mode: str, media: str, tmp: str) -> list[str]:
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
            "-device", "qemu-xhci,id=xhci",
            "-drive", f"if=none,id=stick,format=qcow2,file={overlay}",
            "-device", "usb-storage,bus=xhci.0,drive=stick,bootindex=1",
        ]
    if mode == "bios":
        return cmd + ["-machine", "q35"]
    code, vars_template = FIRMWARE[mode]
    vars_copy = os.path.join(tmp, "OVMF_VARS.fd")
    shutil.copy(os.path.join(OVMF, vars_template), vars_copy)
    cmd += [
        "-machine", "q35,smm=on",
        "-global", "driver=cfi.pflash01,property=secure,value=on",
        "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={os.path.join(OVMF, code)}",
        "-drive", f"if=pflash,format=raw,unit=1,file={vars_copy}",
    ]
    return cmd


def run(img: str, platform: str, mode: str, media: str, log_path: str, timeout: float,
        user: str) -> bool:
    with tempfile.TemporaryDirectory() as tmp, open(log_path, "wb") as log:
        proc = subprocess.Popen(qemu_command(img, mode, media, tmp),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        con = Console(proc, log)
        start = time.monotonic()
        try:
            got = con.expect(MENU, LOGIN, FAILURE, timeout=timeout)
            if got == 2:
                print(f"{mode}: boot FAILED (see {log_path})")
                return False
            boot_s = time.monotonic() - start
            if got == 1:
                # Either agetty's "login: root (automatic login)" on the way
                # to BackOtter's menu or a root shell, or a real login prompt.
                try:
                    got = con.expect(MENU, PROMPT, timeout=20)
                except TimeoutError:
                    got = None
            as_root = True
            if got == 0:
                con.send("1\n")   # BackOtter's console: "Shell"
                con.expect(PROMPT, timeout=30)
            elif got is None:
                con.send(user + "\n")
                if con.expect(re.compile(rb"Password: $"), PROMPT, timeout=30) == 0:
                    con.send("\n")   # casper's live user has an empty password
                    con.expect(PROMPT, timeout=30)
                as_root = False
            con.send("export TERM=dumb; stty -echo" + ("" if as_root else "; sudo -i") + "\n")
            con.expect(PROMPT, timeout=30)
            # A line at a time, and after a check only once it has printed its
            # result: the guest's tty buffers little input (4 KB on Linux, 1 KB
            # on NetBSD), and drops what's typed beyond that while a slow
            # check runs.
            script = CHECK_FUNCTION + check_script(platform, media) + DONE_LINE
            for line in script.strip().splitlines():
                con.send(line + "\n")
                m = re.match(r"check (\S+) ", line)
                if m:
                    con.expect(re.compile(rb"BKO-CHECK " + re.escape(m[1].encode()) + rb" .*\n"),
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
            # OpenBSD has no poweroff; Linux's shutdown has no -p.
            con.send(("poweroff" if platform == "linux" else "shutdown -p now") + "\n")
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                pass
            return all_ok
        except (TimeoutError, EOFError) as e:
            print(f"{mode}: {e} (see {log_path})")
            return False
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def check_script(platform: str, media: str) -> str:
    """PLATFORM/checks.sh, and PLATFORM/config-checks.sh on a USB stick,
    without their leading comments (not every shell takes typed comments)."""
    names = ["checks.sh"] + (["config-checks.sh"] if media == "usb" else [])
    parts = []
    for name in names:
        with open(os.path.join(HERE, platform, name), encoding="utf-8") as f:
            lines = f.read().splitlines()
        while lines and (lines[0].startswith("#") or not lines[0].strip()):
            lines.pop(0)
        parts.append("\n".join(lines))
    return "\n".join(parts) + "\n"


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
    args = ap.parse_args()
    ok = run(args.img, args.platform, args.mode, args.media,
             args.log or f"boot-test-{args.mode}-{args.media}.log", args.timeout, args.user)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
