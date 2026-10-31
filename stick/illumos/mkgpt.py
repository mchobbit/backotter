#!/usr/bin/env python3
"""Write the illumos rescue image's GPT onto a blank image file.

Usage: mkgpt.py IMAGE ROOT_SECTORS CONFIG_SECTORS

Lays out, in 512-byte sectors, 1 MiB aligned:

  1  EFI system partition, 34 MiB (as on OmniOS's own USB image)
  2  Solaris boot (V_BOOT), 1 MiB: gptzfsboot, BIOS's second stage
  3  Solaris root (V_ROOT), ROOT_SECTORS: UFS with /boot, the kernel and the
     boot archive
  4  Microsoft basic data, CONFIG_SECTORS: BackOtter's config partition

The first three are OmniOS's usb-dd layout, in its order; the config
partition comes after them.  IMAGE is created, or truncated (or grown) to
fit, its contents kept.  Writes
the protective MBR's 0xEE entry too; installboot -m later puts pmbr's boot
code in front of it and keeps the table.  Run by build.sh in the build VM:
illumos' fmthard can't write a basic data partition's type.
"""

from __future__ import annotations

import os
import struct
import sys
import uuid
import zlib

SECTOR = 512
ALIGN = 2048                   # 1 MiB
ENTRIES = 128
ENTRY_SIZE = 128
TABLE_SECTORS = ENTRIES * ENTRY_SIZE // SECTOR      # 32

ESP = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
V_BOOT = "6A82CB45-1DD2-11B2-99A6-080020736631"
V_ROOT = "6A85CF4D-1DD2-11B2-99A6-080020736631"
BASIC_DATA = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"


def align(n: int) -> int:
    return (n + ALIGN - 1) // ALIGN * ALIGN


def entry(type_guid: str, first: int, count: int, name: str) -> bytes:
    return struct.pack("<16s16sQQQ72s", uuid.UUID(type_guid).bytes_le, uuid.uuid4().bytes_le,
                       first, first + count - 1, 0, name.encode("utf-16-le"))


def header(current: int, backup: int, first_usable: int, last_usable: int, disk_guid: bytes,
           table_lba: int, table_crc: int) -> bytes:
    fields = [b"EFI PART", 0x00010000, 92, 0, 0, current, backup, first_usable, last_usable,
              disk_guid, table_lba, ENTRIES, ENTRY_SIZE, table_crc]
    fmt = "<8sIIIIQQQQ16sQIII"
    crc = zlib.crc32(struct.pack(fmt, *fields))
    fields[3] = crc
    return struct.pack(fmt, *fields).ljust(SECTOR, b"\0")


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    path, root_sectors, config_sectors = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    parts = []
    start = ALIGN
    for type_guid, count, name in ((ESP, 34 * ALIGN, "EFI system"),
                                   (V_BOOT, ALIGN, "boot"),
                                   (V_ROOT, root_sectors, "root"),
                                   (BASIC_DATA, config_sectors, "BACKOTTER")):
        parts.append(entry(type_guid, start, count, name))
        start = align(start + count)
    last_usable = start - 1
    total = start + TABLE_SECTORS + 1           # the backup table and header
    table = b"".join(parts).ljust(ENTRIES * ENTRY_SIZE, b"\0")
    table_crc = zlib.crc32(table)
    disk_guid = uuid.uuid4().bytes_le
    first_usable = 2 + TABLE_SECTORS

    # The protective MBR: one 0xEE entry over the whole disk (or as much of it
    # as 32 bits reach).
    mbr = bytearray(SECTOR)
    struct.pack_into("<B3sB3sII", mbr, 446, 0, b"\x00\x02\x00", 0xEE, b"\xff\xff\xff",
                     1, min(total - 1, 0xFFFFFFFF))
    mbr[510:512] = b"\x55\xaa"

    with os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o644), "r+b") as f:
        f.truncate(total * SECTOR)
        f.seek(0)
        f.write(mbr)
        f.write(header(1, total - 1, first_usable, last_usable, disk_guid, 2, table_crc))
        f.write(table)
        f.seek((total - 1 - TABLE_SECTORS) * SECTOR)
        f.write(table)
        f.write(header(total - 1, 1, first_usable, last_usable, disk_guid,
                       total - 1 - TABLE_SECTORS, table_crc))
        f.flush()
        os.fsync(f.fileno())
    # build.sh reads the layout from here: number, first sector, sectors.
    for i, p in enumerate(parts, 1):
        first, last = struct.unpack_from("<QQ", p, 32)
        print(i, first, last - first + 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
