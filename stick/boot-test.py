#!/usr/bin/env python3
"""Boot a BackOtter test ISO in QEMU and check the live system from inside.

Usage: boot-test.py ISO {uefi-secureboot,uefi,bios} [--log FILE] [--timeout S]

Watches the first serial port (make-test-iso.sh puts a kernel console there),
logs in as casper's live user, runs a few checks, and powers off.  Exits 0 if
the system came up and every check passed.

Needs: qemu-system-x86_64 with KVM; for the UEFI modes, OVMF (Debian/Ubuntu
package "ovmf").  uefi-secureboot uses OVMF's Secure Boot build with
Microsoft's keys enrolled, so it boots the ISO's signed shim and kernel the
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

OVMF = "/usr/share/OVMF"
FIRMWARE = {
    "uefi-secureboot": ("OVMF_CODE_4M.secboot.fd", "OVMF_VARS_4M.ms.fd"),
    "uefi": ("OVMF_CODE_4M.fd", "OVMF_VARS_4M.fd"),
}
# Set by the casper.conf inside the server ISO's initrd (the installer layer's
# /etc/casper.conf says "ubuntu", but that copy isn't the one casper reads).
LIVE_USER = "ubuntu-server"
PROMPT = re.compile(rb"[$#] $")
LOGIN = re.compile(rb"login: ")
FAILURE = re.compile(rb"Kernel panic|\(initramfs\) |BusyBox v[0-9]")
# Computed by the guest, so the command's own echo can't match it.
DONE = re.compile(rb"BO-42-DONE\r?\n")

# Each check prints one line: "BO-CHECK <name> <ok|FAIL> <detail>".
CHECKS = r"""
check() { n=$1; shift; if out=$(sh -c "$*" 2>&1); then r=ok; else r=FAIL; fi
          echo "BO-CHECK $n $r $(echo "$out" | tr '\n' ' ' | cut -c1-200)"; }
check kernel 'uname -r'
check layers 'grep -o "layerfs-path=[^ ]*" /proc/cmdline'
check rootfs 'findmnt -n -o FSTYPE /'
check secureboot 'od -An -t u1 /sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c 2>/dev/null | awk "{print (\$5==1 ? \"enabled\" : \"disabled\")}" | grep . || echo "no EFI (BIOS boot)"'
check snapd-gone '! dpkg-query -W -f="\${db:Status-Status}" snapd 2>/dev/null | grep -q "^installed"'
check llvm-gone '! ls /usr/lib/x86_64-linux-gnu/libLLVM* >/dev/null 2>&1'
check arm-firmware-gone 'test ! -e /usr/lib/firmware/qcom/x1e80100 && test ! -e /usr/lib/firmware/nvidia/tegra194'
check modem-firmware-kept 'ls -d /usr/lib/firmware/qcom/sdx61'
check switch-firmware 'ls /usr/lib/firmware/mellanox 2>/dev/null | wc -l | sed "s/$/ mellanox files/"'
check tools 'for c in python3 lvm mdadm cryptsetup sgdisk mkfs.ext4 mkfs.xfs mkfs.btrfs ssh; do command -v $c >/dev/null || { echo missing $c; exit 1; }; done; echo all present'
check python 'python3 -c "import sys; print(sys.version.split()[0])"'
check dpkg-audit 'test -z "$(dpkg --audit)"'
check failed-units 'n=$(systemctl --failed --no-legend | wc -l); systemctl --failed --no-legend | cut -c1-60; test $n -eq 0'
check memory 'free -m | awk "/Mem:/{print \$3 \" MB used of \" \$2}"'
echo BO-$((6 * 7))-DONE
"""


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


def qemu_command(iso: str, mode: str, tmp: str) -> list[str]:
    cmd = [
        "qemu-system-x86_64", "-enable-kvm", "-cpu", "host", "-smp", "2", "-m", "4096",
        "-display", "none", "-serial", "stdio", "-monitor", "none",
        "-drive", f"file={iso},media=cdrom,readonly=on",
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


def run(iso: str, mode: str, log_path: str, timeout: float, user: str) -> bool:
    with tempfile.TemporaryDirectory() as tmp, open(log_path, "wb") as log:
        proc = subprocess.Popen(qemu_command(iso, mode, tmp),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        con = Console(proc, log)
        start = time.monotonic()
        try:
            got = con.expect(LOGIN, FAILURE, timeout=timeout)
            if got == 1:
                print(f"{mode}: boot FAILED (see {log_path})")
                return False
            boot_s = time.monotonic() - start
            try:
                # make-test-iso.sh's test layer logs root in by itself.
                con.expect(PROMPT, timeout=20)
                as_root = True
            except TimeoutError:
                con.send(user + "\n")
                if con.expect(re.compile(rb"Password: $"), PROMPT, timeout=30) == 0:
                    con.send("\n")   # casper's live user has an empty password
                    con.expect(PROMPT, timeout=30)
                as_root = False
            con.send("export TERM=dumb; stty -echo" + ("" if as_root else "; sudo -i") + "\n")
            con.expect(PROMPT, timeout=30)
            # A line at a time: the guest's tty buffers only 4 KB of input.
            for line in CHECKS.strip().splitlines():
                con.send(line + "\n")
                time.sleep(0.1)
            con.expect(DONE, timeout=120)
            results = re.findall(rb"BO-CHECK (\S+) (ok|FAIL) ?(.*?)\r?\n",
                                 log_contents(log_path))
            print(f"{mode}: reached login in {boot_s:.0f} s")
            all_ok = bool(results)
            for name, status, detail in results:
                all_ok &= status == b"ok"
                print(f"  {status.decode():4} {name.decode():20} {detail.decode(errors='replace').strip()}")
            con.send("poweroff\n")
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


def log_contents(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("iso")
    ap.add_argument("mode", choices=["uefi-secureboot", "uefi", "bios"])
    ap.add_argument("--log", help="serial log file (default: boot-test-MODE.log)")
    ap.add_argument("--timeout", type=float, default=300, help="seconds to reach login")
    ap.add_argument("--user", default=LIVE_USER, help=f"live user (default: {LIVE_USER})")
    args = ap.parse_args()
    ok = run(args.iso, args.mode, args.log or f"boot-test-{args.mode}.log",
             args.timeout, args.user)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
