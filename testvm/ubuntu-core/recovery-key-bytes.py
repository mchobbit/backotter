#!/usr/bin/env python3
"""Turn an Ubuntu Core recovery key, as `snap recovery --show-keys` shows
it (eight 5-digit groups joined by dashes), into the 16 bytes that are the
LUKS2 passphrase: each group is one little-endian uint16 (snapd's
keys.RecoveryKey).  Reads the key from stdin (never argv, so it stays out
of ps and the shell's history), writes the 16 bytes to OUT (mode 600) and
prints only their SHA-256's first 12 hex digits.

Usage: recovery-key-bytes.py OUT <KEY_FILE
"""
import hashlib
import os
import re
import struct
import sys


def convert(text: str) -> bytes:
    text = text.strip()
    if not re.fullmatch(r"[0-9]{5}(-[0-9]{5}){7}", text):
        raise ValueError("not eight dash-separated groups of five digits")
    groups = [int(g) for g in text.split("-")]
    if any(g > 0xFFFF for g in groups):
        raise ValueError("a group is over 65535")
    return struct.pack("<8H", *groups)


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    try:
        key = convert(sys.stdin.read())
    except ValueError as e:
        sys.exit(f"recovery-key-bytes.py: {e}")
    fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    print(hashlib.sha256(key).hexdigest()[:12])
    return 0


if __name__ == "__main__":
    sys.exit(main())
