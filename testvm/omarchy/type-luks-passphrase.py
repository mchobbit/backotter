#!/usr/bin/env python3
"""Type a guest's LUKS passphrase on its keyboard through QEMU's monitor.

Usage: type-luks-passphrase.py MONITOR_SOCKET PASSPHRASE_FILE [DELAY...]

Omarchy asks for the passphrase on its screen (Plymouth), not on the serial
console, so a headless boot can only answer it the way a person would: by
key presses.  Nothing tells this script when the prompt is up, so it types
the passphrase and Enter after each DELAY (seconds since it started; default
25, 50 and 80).  Typed again after the disk is unlocked, the keys land at a
login prompt, which is harmless; typed before the prompt, they are lost, and
a later round answers it.  The passphrase must be lower-case letters and
digits, which sendkey names as themselves.
"""

from __future__ import annotations

import socket
import sys
import time


def main() -> int:
    sock_path, pass_path = sys.argv[1], sys.argv[2]
    delays = [float(d) for d in sys.argv[3:]] or [25, 50, 80]
    passphrase = open(pass_path).read().strip()
    if not passphrase.isalnum() or passphrase.lower() != passphrase:
        sys.exit("type-luks-passphrase: the passphrase must be lower-case letters and digits")
    start = time.monotonic()
    # vm-run makes its overlays before it starts QEMU, which on a busy host
    # takes a while.
    for _ in range(600):
        try:
            mon = socket.socket(socket.AF_UNIX)
            mon.connect(sock_path)
            break
        except OSError:
            time.sleep(0.2)
    else:
        sys.exit(f"type-luks-passphrase: no QEMU monitor at {sock_path}")
    mon.settimeout(1)
    for delay in delays:
        time.sleep(max(0.0, start + delay - time.monotonic()))
        for key in list(passphrase) + ["ret"]:
            mon.sendall(f"sendkey {key}\n".encode())
            time.sleep(0.08)
        try:
            while mon.recv(4096):
                pass
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
