#!/usr/bin/env python3
"""Press Enter on YaST pop-ups that AutoYaST can't be told to skip.

Usage: answer-popups.py LOG SERIAL_SOCKET PATTERN...

Tumbleweed's installer (2026-10) shows "No network running" while saving the
network settings and waits for OK, whatever the profile's <report> settings
say.  This watches the installer's serial console, LOG, and each time one of
the PATTERNs newly appears there, presses Enter on that console (QEMU's
serial port as a socket, SERIAL_SOCKET), where YaST reads its keys.  It runs
until the socket goes away (QEMU exited).
"""

from __future__ import annotations

import select
import socket
import sys
import time


def main() -> int:
    log, sock_path, patterns = sys.argv[1], sys.argv[2], [p.encode() for p in sys.argv[3:]]
    for _ in range(100):
        try:
            mon = socket.socket(socket.AF_UNIX)
            mon.connect(sock_path)
            break
        except OSError:
            time.sleep(0.2)
    else:
        sys.exit(f"answer-popups: no QEMU serial socket at {sock_path}")
    # Drain the socket as fast as QEMU writes to it: a full socket stalls the
    # guest's serial port, and with it the installer.
    seen = {p: 0 for p in patterns}
    last_check = 0.0
    while True:
        ready, _, _ = select.select([mon], [], [], 0.5)
        if ready:
            try:
                if mon.recv(65536) == b"":
                    return 0
            except OSError:
                return 0
        if time.monotonic() - last_check < 5:
            continue
        last_check = time.monotonic()
        try:
            text = open(log, "rb").read()
        except OSError:
            continue
        for p in patterns:
            n = text.count(p)
            if n > seen[p]:
                seen[p] = n
                print(f"answer-popups: pressing Enter on {p.decode()!r}", file=sys.stderr)
                try:
                    mon.sendall(b"\r")
                except OSError:
                    return 0


if __name__ == "__main__":
    sys.exit(main())
