#!/usr/bin/env python3
"""Run bko-realworld-1a in QEMU, the way it runs on a laptop.

Usage: rw1a-qemu.py TEST_IMG GUEST_DIR WORK_DIR [--port N]

TEST_IMG is an image from "make-img.sh --test".  GUEST_DIR is a UEFI test
guest (vms/ubuntu26-uefi): GUEST_DIR/*.qcow2 is the internal disk, attached as
NVMe, with its writes going to an overlay in WORK_DIR.  The stick and the backup
drive are USB disks, each a stick or drive bigger than what's on it.

Three boots:
  refuse   two external drives: must stop, having changed nothing
  run      answers "no" once (must change nothing), then YES; must PASS
  restored the internal disk alone, with the NVRAM the run left: must boot
           and answer over SSH

Exits 0 if all of that held.  Nothing outside WORK_DIR is written.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import select
import shutil
import subprocess
import sys
import threading
import time

OVMF = "/usr/share/OVMF"
CODE = "OVMF_CODE_4M.fd"
VARS = "OVMF_VARS_4M.fd"
MENU = "Choose \\[1-4\\]:"
PROMPT = r"root@backotter[^\r\n]*[#$] $"
SSH = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
       "-o", "ConnectTimeout=5", "-o", "LogLevel=ERROR"]


class Console:
    running: list["Console"] = []

    def __init__(self, cmd: list[str], log_path: str):
        Console.running.append(self)
        self.log = open(log_path, "ab")
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.buf = ""

    def expect(self, *patterns: str, timeout: float) -> int:
        end = time.monotonic() + timeout
        while True:
            for i, p in enumerate(patterns):
                m = re.search(p, self.buf, re.M)
                if m:
                    self.buf = self.buf[m.end():]
                    return i
            left = end - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"none of {patterns} within {timeout:.0f} s; last output:\n{self.buf[-1500:]}")
            ready, _, _ = select.select([self.proc.stdout], [], [], min(left, 5))
            if ready:
                chunk = os.read(self.proc.stdout.fileno(), 65536)
                if not chunk:
                    raise EOFError("QEMU exited")
                self.log.write(chunk)
                self.log.flush()
                self.buf = (self.buf + chunk.decode("utf-8", "replace"))[-200000:]

    def drain(self) -> None:
        """Keeps reading the console in the background: a guest writing to a
        full pipe stops, and a later phase may not be watching it."""
        def read() -> None:
            while chunk := os.read(self.proc.stdout.fileno(), 65536):
                self.log.write(chunk)
                self.log.flush()
        threading.Thread(target=read, daemon=True).start()

    def send(self, text: str) -> None:
        self.proc.stdin.write(text.encode() + b"\n")
        self.proc.stdin.flush()

    def shell(self, command: str, timeout: float = 120) -> None:
        self.send(command)
        self.expect(PROMPT, timeout=timeout)

    def capture(self, command: str, timeout: float = 120) -> str:
        """Runs command, returning what it printed."""
        # The arithmetic keeps the terminal's echo of this line from matching,
        # and the shell may put escape sequences before the marker.
        self.buf = ""
        self.send(f"echo @@$((1))B; {command}; echo @@$((1))E")
        self.expect(r"@@1B", timeout=timeout)
        end = time.monotonic() + timeout
        while "@@1E" not in self.buf and time.monotonic() < end:
            ready, _, _ = select.select([self.proc.stdout], [], [], 1)
            if ready:
                chunk = os.read(self.proc.stdout.fileno(), 65536)
                self.log.write(chunk)
                self.buf += chunk.decode("utf-8", "replace")
        text, _, self.buf = self.buf.partition("@@1E")
        self.expect(PROMPT, timeout=30)
        return text.replace("\r", "").strip()

    def close(self, timeout: float = 90) -> None:
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.log.close()


def overlay(path: str, backing: str | None, size: str, fmt: str = "qcow2") -> None:
    cmd = ["qemu-img", "create", "-q", "-f", "qcow2"]
    if backing:
        cmd += ["-F", fmt, "-b", os.path.abspath(backing)]
    subprocess.run(cmd + [path, size], check=True)


def qemu(work: str, name: str, vars_file: str, disks: list[list[str]], port: int | None = None) -> list[str]:
    cmd = ["qemu-system-x86_64", "-enable-kvm", "-cpu", "host", "-smp", "4", "-m", "6144",
           "-display", "none", "-serial", "stdio", "-monitor", "none",
           "-machine", "q35,smm=on",
           "-global", "driver=cfi.pflash01,property=secure,value=on",
           "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={OVMF}/{CODE}",
           "-drive", f"if=pflash,format=raw,unit=1,file={vars_file}",
           "-device", "qemu-xhci,id=usb"]
    for d in disks:
        cmd += d
    if port:
        cmd += ["-netdev", f"user,id=n,hostfwd=tcp:127.0.0.1:{port}-:22", "-device", "virtio-net-pci,netdev=n"]
    else:
        cmd += ["-nic", "none"]
    return cmd


def usb(name: str, path: str, serial: str, boot: bool = False) -> list[str]:
    dev = f"usb-storage,bus=usb.0,drive={name},serial={serial}"
    return ["-drive", f"if=none,id={name},format=qcow2,file={path}", "-device", dev + (",bootindex=1" if boot else "")]


def nvme(path: str, boot: bool = False) -> list[str]:
    dev = "nvme,drive=internal,serial=BKOINTERNAL"
    return ["-drive", f"if=none,id=internal,format=qcow2,file={path}", "-device", dev + (",bootindex=1" if boot else "")]


def to_shell(con: Console) -> None:
    con.expect(MENU, timeout=300)
    con.send("1")
    con.expect(PROMPT, timeout=30)
    con.shell("stty -echo; export PS1='root@backotter# ' TERM=dumb")


def format_backup(con: Console, serial: str) -> None:
    dev = f"$(ls /dev/disk/by-id/usb-*{serial}*-0:0 | head -n 1)"
    con.shell(f"d={dev}; echo ',,L' | sfdisk -q --label gpt $d && udevadm settle && mkfs.ext4 -q -F -L {serial} ${{d}}-part1 && udevadm settle; echo formatted", 120)


def check(ok: bool, what: str, fails: list[str]) -> None:
    print(("  ok    " if ok else "  FAIL  ") + what, flush=True)
    if not ok:
        fails.append(what)


def restored_boot(a: argparse.Namespace, internal: str, vars_file: str, key: str, fails: list[str]) -> int:
    print("== restored: boots alone", flush=True)
    con = Console(qemu(a.work, "restored", vars_file, [nvme(internal, True)], a.port), f"{a.work}/console-restored.log")
    con.drain()
    up = False
    ssh = ["ssh", *SSH, "-i", key, "-p", str(a.port), "root@127.0.0.1"]
    end = time.monotonic() + 420
    while time.monotonic() < end and not up:
        # QEMU's user networking accepts before the guest is up: the timeout matters.
        r = subprocess.run(["timeout", "15", *ssh, "findmnt -n -o SOURCE /; systemctl is-system-running; ls /var/log/bko-holt; bootctl status 2>/dev/null | grep -i 'current boot' ; efibootmgr | head -n 5"],
                           capture_output=True, text=True)
        up = r.returncode == 0 and "/dev/nvme0n1" in r.stdout
        if not up:
            time.sleep(5)
    check(up, "the restored system boots and answers over SSH", fails)
    print(r.stdout, r.stderr, flush=True)
    subprocess.run(["timeout", "20", *ssh, "poweroff"], capture_output=True)
    con.close()
    print("FAILED: " + "; ".join(fails) if fails else "PASS", flush=True)
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("img")
    ap.add_argument("guest_dir")
    ap.add_argument("work")
    ap.add_argument("--port", type=int, default=2696)
    ap.add_argument("--restored-only", action="store_true",
                    help="only the last boot, of the disks an earlier run left in WORK_DIR")
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    guest = glob.glob(os.path.join(a.guest_dir, "*.qcow2"))
    guest = [g for g in guest if "overlay" not in os.path.basename(g)]
    if len(guest) != 1:
        sys.exit(f"want one qcow2 in {a.guest_dir}, found {guest}")
    key = os.path.join(a.guest_dir, "id_ed25519")
    stick = f"{a.work}/stick.qcow2"
    internal = f"{a.work}/internal.qcow2"
    vars_file = f"{a.work}/vars.fd"
    fails: list[str] = []
    if a.restored_only:
        return restored_boot(a, internal, vars_file, key, fails)
    shutil.copy(f"{OVMF}/{VARS}", vars_file)
    overlay(stick, a.img, "16G", "raw")
    overlay(internal, guest[0], "32G")
    for n in ("bk1", "bk2"):
        overlay(f"{a.work}/{n}.qcow2", None, "40G")
    fingerprint = lambda con: con.capture("sfdisk -d /dev/nvme0n1 | sha256sum; blkid -p /dev/nvme0n1p* | sha256sum")

    print("== refuse: two external drives", flush=True)
    con = Console(qemu(a.work, "refuse", vars_file, [usb("stick", stick, "BKOSTICK", True), nvme(internal),
                       usb("b1", f"{a.work}/bk1.qcow2", "BKOBACKUPA"), usb("b2", f"{a.work}/bk2.qcow2", "BKOBACKUPB")]),
                  f"{a.work}/console-refuse.log")
    to_shell(con)
    con.send("bko-realworld-1a; echo rc=$?")
    got = con.expect("several external drives", "Type YES", timeout=120)
    check(got == 0, "stops with two external drives", fails)
    if got == 1:
        con.send("no")
    con.expect(PROMPT, timeout=120)
    con.send("poweroff")
    con.close()

    print("== run", flush=True)
    con = Console(qemu(a.work, "run", vars_file, [usb("stick", stick, "BKOSTICK", True), nvme(internal),
                       usb("b1", f"{a.work}/bk1.qcow2", "BKOBACKUPA")]), f"{a.work}/console-run.log")
    to_shell(con)
    print(con.capture("lsblk -o NAME,SIZE,TRAN,FSTYPE,LABEL"), flush=True)
    parts = con.capture("lsblk -lnpo NAME,TYPE /dev/disk/by-id/usb-*BKOSTICK*-0:0 | grep -c part")
    check(parts.strip().splitlines()[-1].strip() == "3", "nopersistent: the stick still has 3 partitions", fails)
    format_backup(con, "BKOBACKUPA")
    before = fingerprint(con)
    con.send("bko-realworld-1a; echo rc=$?")
    con.expect("Type YES to go ahead:", timeout=300)
    con.send("no")
    con.expect(PROMPT, timeout=120)
    check(fingerprint(con) == before, "answering no changes nothing", fails)
    con.send("bko-realworld-1a; echo rc=$?")
    con.expect("Type YES to go ahead:", timeout=300)
    con.send("YES")
    got = con.expect("RESULT: PASS", "RESULT: FAIL[^\r\n]*", timeout=3600)
    check(got == 0, "bko-realworld-1a reports PASS", fails)
    con.expect(PROMPT, timeout=180)
    con.shell("mkdir -p /mnt/c && mount -L BACKOTTER /mnt/c")
    print(con.capture("cd /mnt/c/backotter/realworld && ls; d=$(ls -d 2* | tail -n 1); ls -la $d; cat $d/RESULT; du -sh .; df -h /mnt/c; wc -l $d/diff.txt; tail -n 8 $d/diff.txt; ls -la /mnt/c/backotter/bin; cd /"), flush=True)
    logs = con.capture("cd /mnt/c/backotter/realworld; d=$(ls -d 2* | tail -n 1); for f in RESULT run.log layout.json after.json lsblk-end.txt dmesg.txt journal.txt.gz rustic-backup.log.gz rustic-check.log.gz; do [ -s $d/$f ] && echo have:$f; done; cd /")
    for f in ("RESULT", "run.log", "layout.json", "after.json", "lsblk-end.txt", "dmesg.txt", "journal.txt.gz", "rustic-backup.log.gz", "rustic-check.log.gz"):
        check(f"have:{f}" in logs, f"log saved: {f}", fails)
    con.send("umount /mnt/c; poweroff")
    con.close()
    if got != 0:
        print("FAILED before the restored boot:", fails)
        return 1

    return restored_boot(a, internal, vars_file, key, fails)


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # A crash mustn't leave a guest running.
        for con in Console.running:
            if con.proc.poll() is None:
                con.proc.kill()
