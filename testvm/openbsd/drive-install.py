#!/usr/bin/env python3
"""Run OpenBSD's installer in QEMU and point it at a response file.

Usage: drive-install.py LOG RESPONSE_URL TIMEOUT -- QEMU_COMMAND...

QEMU_COMMAND must put the guest's serial console on stdio.  At the
installer's first question this answers "autoinstall", gives RESPONSE_URL
when asked where the response file is, and waits for the install to finish
and the guest to reboot (QEMU_COMMAND should include -no-reboot).  Every byte
from the console goes to LOG.  Exits 0 if the installer reported success.
"""

from __future__ import annotations

import os
import re
import select
import subprocess
import sys
import time

START = re.compile(rb"\(I\)nstall, \(U\)pgrade, \(A\)utoinstall or \(S\)hell\? ")
WHERE = re.compile(rb"Response file location\? (\[[^]\r\n]*\] )?")
DONE = re.compile(rb"CONGRATULATIONS!")
# The installer drops to a shell prompt when a step fails.  A full file
# system while unpacking the sets, though, it only reports, and carries on
# to "CONGRATULATIONS!".
FAILED = re.compile(rb"failed; check|^# $|file system full", re.MULTILINE)


def main() -> int:
    log_path, url, timeout = sys.argv[1], sys.argv[2], float(sys.argv[3])
    if sys.argv[4] != "--":
        sys.exit("usage: drive-install.py LOG RESPONSE_URL TIMEOUT -- QEMU_COMMAND...")
    deadline = time.monotonic() + timeout
    asked_where = 0
    ok = False
    buf = b""
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(sys.argv[5:], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        assert proc.stdin is not None and proc.stdout is not None

        def send(text: str) -> None:
            proc.stdin.write(text.encode() + b"\n")
            proc.stdin.flush()

        try:
            while time.monotonic() < deadline:
                ready, _, _ = select.select([proc.stdout], [], [], 1.0)
                if not ready:
                    continue
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    break   # QEMU exited: the guest rebooted, or powered off
                log.write(chunk)
                log.flush()
                buf = (buf + chunk)[-65536:]
                if m := START.search(buf):
                    send("a")
                elif m := WHERE.search(buf):
                    asked_where += 1
                    if asked_where > 2:
                        print("drive-install: the installer can't fetch the response file", file=sys.stderr)
                        return 1
                    send(url)
                elif m := DONE.search(buf):
                    ok = True
                elif m := FAILED.search(buf):
                    print("drive-install: the install failed", file=sys.stderr)
                    return 1
                if m:
                    buf = buf[m.end():]
            else:
                print("drive-install: timed out", file=sys.stderr)
                return 1
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
