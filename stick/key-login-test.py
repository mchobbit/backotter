#!/usr/bin/env python3
"""Boot a FreeBSD rescue image in QEMU and check its key access over SSH.

Usage: key-login-test.py IMG {uefi,bios} [--no-keys] [--port N] [--log FILE]
                         [--timeout S] [--platform freebsd]

The key-login case of boot-test.py (docs/design/freebsd-bs1.md, F1), in a file
of its own: boot-test.py drives the serial console, this drives SSH.  IMG may
be a --test or a release image: nothing here types at the console.

With keys (the default): a copy of IMG gets a fresh client key and a fresh
ed25519 host key from stick/add-ssh-key.sh --host-key, boots from a USB stick
on the platform's controller (boot-test.py's qemu_command) with a user-mode
NIC whose port N forwards to the guest's 22, and the checks are:

  key-login      backotter@ logs in by that key, with StrictHostKeyChecking
                 against a known_hosts holding only the pinned host key
  one-host-key   the guest has /etc/ssh/ssh_host_ed25519_key alone, and it is
                 the pinned one; ssh-keyscan offers no other type
  mdo-root       "mdo id -u" prints 0
  root-refused   root@ is refused even holding an authorized key (one put in
                 /root/.ssh through mdo for this check)
  status         /var/run/backotter/remote-access says remote access is ON

With --no-keys: IMG's copy as it is (no authorized_keys).  Once the console's
menu is on the serial port, port N must give no SSH banner for 30 s: sshd
is off, as on an image without key access.  (boot-test.py's checks, among
them sshd-off-at-boot, cover the rest of "as before".)

Exits 0 when every check passed.  Needs what boot-test.py needs, plus ssh,
ssh-keygen, ssh-keyscan, and add-ssh-key.sh's sfdisk and mtools.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("boot_test", os.path.join(HERE, "boot-test.py"))
boot_test = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(boot_test)

ALIAS = "bko-key-login-test"
MENU = re.compile(rb"Choose \[1-4\]: ")


def qemu(img: str, platform: str, mode: str, tmp: str, port: int, log: str) -> subprocess.Popen:
    cmd = boot_test.qemu_command(img, platform, mode, "usb", tmp)
    cmd[cmd.index("-serial") + 1] = f"file:{log}"
    cmd += ["-nic", f"user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:{port}-:22"]
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=open(log + ".qemu", "wb"))


def read(path: str) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return b""


def banner(port: int) -> bytes:
    """What port gives in 5 s: b"" when nothing (QEMU's forward accepts, then closes)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.settimeout(5)
            return s.recv(64)
    except OSError:
        return b""


def with_keys(img: str, platform: str, mode: str, port: int, log: str, timeout: float, tmp: str) -> bool:
    k = os.path.join(tmp, "keys")
    os.mkdir(k, 0o700)
    for name in ("id_ed25519", "ssh_host_ed25519_key"):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"{ALIAS}-{name}",
                        "-f", os.path.join(k, name)], check=True)
    hostpub = open(os.path.join(k, "ssh_host_ed25519_key.pub")).read().split()
    kh = os.path.join(k, "known_hosts")
    with open(kh, "w") as f:
        f.write(f"{ALIAS} {hostpub[0]} {hostpub[1]}\n")
    copy = os.path.join(tmp, "stick.img")
    shutil.copyfile(img, copy)
    subprocess.run([os.path.join(HERE, "add-ssh-key.sh"), "--host-key", os.path.join(k, "ssh_host_ed25519_key"),
                    copy, os.path.join(k, "id_ed25519.pub")], check=True, stdout=subprocess.DEVNULL)
    opts = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", f"HostKeyAlias={ALIAS}",
            "-o", f"UserKnownHostsFile={kh}", "-o", "StrictHostKeyChecking=yes", "-o", "LogLevel=ERROR",
            "-o", "IdentitiesOnly=yes", "-i", os.path.join(k, "id_ed25519"), "-p", str(port)]

    def ssh(who: str, command: str, t: float = 60) -> subprocess.CompletedProcess:
        return subprocess.run(["ssh", *opts, f"{who}@127.0.0.1", command], capture_output=True,
                              text=True, timeout=t, stdin=subprocess.DEVNULL)

    proc = qemu(copy, platform, mode, tmp, port, log)
    results: list[tuple[str, bool, str]] = []
    try:
        start = time.monotonic()
        r = None
        while time.monotonic() - start < timeout:
            if proc.poll() is not None:
                break
            if boot_test.FAILURE.search(read(log)):
                break
            try:
                r = ssh("backotter", "id -un", 20)
            except subprocess.TimeoutExpired:
                r = None
            if r is not None and r.returncode == 0:
                break
            time.sleep(5)
        ok = r is not None and r.returncode == 0 and r.stdout.strip() == "backotter"
        detail = (f"in {time.monotonic() - start:.0f} s, by key, pinned host key" if ok else
                  (r.stderr.strip()[-200:] if r is not None else "no answer"))
        results.append(("key-login", ok, detail))
        if ok:
            r = ssh("backotter", "ls /etc/ssh/ssh_host_*_key; cat /etc/ssh/ssh_host_ed25519_key.pub")
            files = [l for l in r.stdout.split() if l.startswith("/etc/ssh/ssh_host_")]
            pub = r.stdout.split()
            scan = subprocess.run(["ssh-keyscan", "-p", str(port), "127.0.0.1"], capture_output=True,
                                  text=True, timeout=60)
            types = sorted({l.split()[1] for l in scan.stdout.splitlines() if l and not l.startswith("#")})
            good = files == ["/etc/ssh/ssh_host_ed25519_key"] and hostpub[1] in pub and types == ["ssh-ed25519"]
            results.append(("one-host-key", good, f"keys {files}, offered {types}"))
            r = ssh("backotter", "mdo id -u")
            results.append(("mdo-root", r.returncode == 0 and r.stdout.strip() == "0",
                            (r.stdout + r.stderr).strip()[-200:]))
            r = ssh("backotter", "mdo sh -c 'install -d -m 700 /root/.ssh && "
                                 "install -m 600 /home/backotter/.ssh/authorized_keys /root/.ssh/authorized_keys' && echo placed")
            placed = r.stdout.strip() == "placed"
            r = ssh("root", "echo logged-in")
            refused = r.returncode != 0 and "logged-in" not in r.stdout and "Permission denied" in r.stderr
            results.append(("root-refused", placed and refused,
                            ("refused, holding an authorized key" if placed and refused else
                             f"placed={placed} rc={r.returncode} {(r.stdout + r.stderr).strip()[-150:]}")))
            r = ssh("backotter", "mdo cat /var/run/backotter/remote-access")
            results.append(("status", "Remote access is ON" in r.stdout, r.stdout.strip().splitlines()[0] if r.stdout.strip() else r.stderr.strip()[-200:]))
            try:
                ssh("backotter", "mdo shutdown -p now", 30)
            except subprocess.TimeoutExpired:
                pass
            try:
                proc.wait(timeout=90)
            except subprocess.TimeoutExpired:
                pass
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    return report(mode, "keys", results, log)


def no_keys(img: str, platform: str, mode: str, port: int, log: str, timeout: float, tmp: str) -> bool:
    copy = os.path.join(tmp, "stick.img")
    shutil.copyfile(img, copy)
    proc = qemu(copy, platform, mode, tmp, port, log)
    results: list[tuple[str, bool, str]] = []
    try:
        start = time.monotonic()
        while time.monotonic() - start < timeout and proc.poll() is None and not MENU.search(read(log)):
            time.sleep(2)
        up = bool(MENU.search(read(log)))
        results.append(("console", up, f"menu in {time.monotonic() - start:.0f} s" if up else "no menu"))
        if up:
            seen = b""
            for _ in range(6):
                seen = banner(port)
                if seen:
                    break
                time.sleep(5)
            results.append(("sshd-off", not seen.startswith(b"SSH-"),
                            f"port {port}: {'banner ' + seen.decode(errors='replace').strip() if seen else 'no banner in 30 s'}"))
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    return report(mode, "no keys", results, log)


def report(mode: str, case: str, results: list[tuple[str, bool, str]], log: str) -> bool:
    print(f"{mode} ({case}): serial log {log}")
    for name, ok, detail in results:
        print(f"  {'ok' if ok else 'FAIL':4} {name:14} {detail}")
    return bool(results) and all(ok for _, ok, _ in results)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("img", metavar="IMG")
    ap.add_argument("mode", choices=["uefi", "bios"])
    ap.add_argument("--platform", choices=["freebsd"], default="freebsd")
    ap.add_argument("--no-keys", action="store_true", help="the image as it is: sshd must stay off")
    ap.add_argument("--port", type=int, default=2297, help="the host port forwarded to the guest's 22")
    ap.add_argument("--log", help="serial log (default: key-login-MODE.log)")
    ap.add_argument("--timeout", type=float, default=400, help="seconds for the first login (or the menu)")
    args = ap.parse_args()
    log = os.path.abspath(args.log or f"key-login-{args.mode}{'-nokeys' if args.no_keys else ''}.log")
    with tempfile.TemporaryDirectory() as tmp:
        f = no_keys if args.no_keys else with_keys
        ok = f(args.img, args.platform, args.mode, args.port, log, args.timeout, tmp)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
