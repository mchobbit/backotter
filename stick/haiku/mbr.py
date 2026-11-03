#!/usr/bin/env python3
"""Write an MBR partition table into a disk image, for haiku/build.sh.

Usage: mbr.py IMAGE TYPE:START:SECTORS[:active] ...

Up to four partitions, in order, TYPE in hex (eb, ef, 0e), START and SECTORS
in 512-byte sectors.  Only the table and the boot signature are written: the
boot code (writembr's) and the rest of the image are left as they are.  The
CHS fields are all ones (LBA only), as for any partition past cylinder 1023;
Haiku's loaders and kernel read only the LBA fields.
"""

import struct
import sys


def main() -> int:
    if len(sys.argv) < 3 or len(sys.argv) > 6:
        print(__doc__.splitlines()[2], file=sys.stderr)
        return 2
    table = b""
    for spec in sys.argv[2:]:
        fields = spec.split(":")
        if len(fields) not in (3, 4) or (len(fields) == 4 and fields[3] != "active"):
            print(f"mbr.py: bad partition {spec!r}", file=sys.stderr)
            return 2
        ptype, start, sectors = int(fields[0], 16), int(fields[1]), int(fields[2])
        if not (0 < ptype < 256 and 0 < start and 0 < sectors and start + sectors < 2**32):
            print(f"mbr.py: partition {spec!r} out of range", file=sys.stderr)
            return 2
        status = 0x80 if len(fields) == 4 else 0
        chs = b"\xfe\xff\xff"
        table += struct.pack("<B3sB3sII", status, chs, ptype, chs, start, sectors)
    table = table.ljust(64, b"\0")
    with open(sys.argv[1], "r+b") as f:
        f.seek(446)
        f.write(table + b"\x55\xaa")
    return 0


if __name__ == "__main__":
    sys.exit(main())
