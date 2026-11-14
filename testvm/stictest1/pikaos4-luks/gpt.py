#!/usr/bin/env python3
"""gpt.py DISK: the GPT of DISK read from the disk itself (the guest has
no sfdisk: PikaOS installs gdisk, not util-linux's fdisk package), one
line "gpt SECTOR" then one line per used entry:
    N START SIZE TYPE ATTRS NAME
N the partition number, START and SIZE in 512-byte sectors, TYPE the
type GUID in upper case, ATTRS the attribute bits in hex, NAME the GPT
name (UTF-16LE, "-" when empty).  Exits 1 when there's no GPT."""
import struct, sys, uuid

disk = sys.argv[1]
with open(disk, "rb") as f:
    sec = 512
    f.seek(sec)
    h = f.read(92)
    if h[:8] != b"EFI PART":
        sys.exit(f"gpt.py: no GPT on {disk}")
    lba, n, size = struct.unpack_from("<QII", h, 72)
    f.seek(lba * sec)
    t = f.read(n * size)
print(f"gpt {sec}")
for i in range(n):
    e = t[i * size:(i + 1) * size]
    ty = uuid.UUID(bytes_le=e[:16])
    if ty.int == 0:
        continue
    first, last, attrs = struct.unpack_from("<QQQ", e, 32)
    name = e[56:128].decode("utf-16-le", "replace").rstrip("\0") or "-"
    print(i + 1, first, last - first + 1, str(ty).upper(), hex(attrs), name.replace(" ", "_"))
