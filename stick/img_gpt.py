#!/usr/bin/env python3
"""A VM platform's image's GPT, on the host (docs/design/img-speed.md §4.2, D2).

    img_gpt.py part IMG N    partition N's first sector and its size in sectors (the MBR's
                             on a disk with no GPT: FreeBSD's)
    img_gpt.py ids IMG       the disk's GUID and each partition's, one "N GUID" a line (0: the disk)
    img_gpt.py new-ids IMG   new random GUIDs for the disk and every partition, in both tables

make-img-bsd.sh --refresh-from runs it once the refreshed image is out of the VM: `part`
for where config.img goes (it writes the new one there with dd), and `new-ids` on the
platforms whose full builds draw random GPT GUIDs (platform.conf's GPT_IDS=random: NetBSD's
`gpt create`/`add`, illumos' mkgpt.py), so that a refresh shares no id with its parent and
two sticks plugged in at once never collide.  `new-ids` checks both headers' and both entry
arrays' CRC32s first and refuses (exit 1, nothing written) a table that doesn't check; it
then writes the same new GUIDs into the primary and the backup, with their CRCs.  A sector
is 512 bytes.  Exit 0, 1 on a refusal, 2 on bad usage.
"""

import os
import struct
import sys
import uuid
import zlib

SECTOR = 512


class Refused(Exception):
    pass


def _header(f, lba):
    f.seek(lba * SECTOR)
    h = f.read(SECTOR)
    if h[:8] != b"EFI PART":
        raise Refused(f"no GPT header at sector {lba}")
    size = struct.unpack_from("<I", h, 12)[0]
    if not 92 <= size <= SECTOR:
        raise Refused(f"the GPT header at sector {lba} says it is {size} bytes")
    crc = struct.unpack_from("<I", h, 16)[0]
    z = bytearray(h[:size])
    z[16:20] = bytes(4)
    if zlib.crc32(bytes(z)) & 0xFFFFFFFF != crc:
        raise Refused(f"the GPT header at sector {lba} fails its CRC")
    cur, alt = struct.unpack_from("<QQ", h, 24)
    elba, n, esize, ecrc = struct.unpack_from("<QIII", h, 72)
    if cur != lba or esize < 128 or n == 0 or n * esize > 1 << 20:
        raise Refused(f"the GPT header at sector {lba} is not one this tool reads")
    f.seek(elba * SECTOR)
    ents = f.read(n * esize)
    if len(ents) != n * esize or zlib.crc32(ents) & 0xFFFFFFFF != ecrc:
        raise Refused(f"the GPT entries of the header at sector {lba} fail their CRC")
    return {"raw": bytearray(h), "size": size, "alt": alt, "entries_lba": elba, "n": n, "esize": esize,
            "entries": bytearray(ents)}


def read(img):
    """The primary and the backup GPT of IMG, each checked."""
    with open(img, "rb") as f:
        p = _header(f, 1)
        b = _header(f, p["alt"])
    if p["entries"] != b["entries"]:
        raise Refused("the primary and backup GPT entries differ")
    return p, b


def parts(hdr):
    """[(number, first, last, type GUID, unique GUID)] of the used entries."""
    out = []
    for i in range(hdr["n"]):
        e = hdr["entries"][i * hdr["esize"]:(i + 1) * hdr["esize"]]
        if e[:16] == bytes(16):
            continue
        first, last = struct.unpack_from("<QQ", e, 32)
        out.append((i + 1, first, last, str(uuid.UUID(bytes_le=bytes(e[:16]))), str(uuid.UUID(bytes_le=bytes(e[16:32])))))
    return out


def mbr_part(img, n):
    """Partition N (1-4) of the MBR of IMG, a disk with no GPT (FreeBSD's): (first, sectors)."""
    with open(img, "rb") as f:
        m = f.read(SECTOR)
    if len(m) < SECTOR or m[510:512] != b"\x55\xaa":
        raise Refused(f"{img} has neither a GPT nor an MBR")
    if any(m[446 + 16 * i + 4] == 0xEE for i in range(4)):
        raise Refused(f"{img}'s MBR is a GPT's protective one, and its GPT is gone")
    if not 1 <= n <= 4 or not m[446 + 16 * (n - 1) + 4]:
        raise Refused(f"no partition {n} in {img}'s MBR")
    first, size = struct.unpack_from("<II", m, 446 + 16 * (n - 1) + 8)
    if not first or not size:
        raise Refused(f"partition {n} of {img}'s MBR is empty")
    return first, size


def part(img, n):
    """Partition N's (first, sectors): the GPT's, or the MBR's on a disk with no GPT."""
    with open(img, "rb") as f:
        f.seek(SECTOR)
        if f.read(8) != b"EFI PART":
            return mbr_part(img, n)
    p, _ = read(img)
    for num, first, last, _, _ in parts(p):
        if num == n:
            return first, last - first + 1
    raise Refused(f"no partition {n} in {img}'s GPT")


def ids(img):
    p, _ = read(img)
    return [(0, str(uuid.UUID(bytes_le=bytes(p["raw"][56:72]))))] + [(x[0], x[4]) for x in parts(p)]


def _finish(hdr, ents):
    h = hdr["raw"]
    struct.pack_into("<I", h, 88, zlib.crc32(bytes(ents)) & 0xFFFFFFFF)
    struct.pack_into("<I", h, 16, 0)
    struct.pack_into("<I", h, 16, zlib.crc32(bytes(h[:hdr["size"]])) & 0xFFFFFFFF)
    return h


def new_ids(img, gen=uuid.uuid4):
    """New GUIDs for the disk and every used entry, the same in both tables.  Returns ids()."""
    p, b = read(img)
    disk = gen().bytes_le
    ents = bytearray(p["entries"])
    for i in range(p["n"]):
        e = slice(i * p["esize"], (i + 1) * p["esize"])
        if ents[e][:16] != bytes(16):
            ents[i * p["esize"] + 16:i * p["esize"] + 32] = gen().bytes_le
    with open(img, "r+b") as f:
        for hdr in (p, b):
            hdr["raw"][56:72] = disk
            h = _finish(hdr, ents)
            f.seek(hdr["entries_lba"] * SECTOR)
            f.write(ents)
            f.seek((1 if hdr is p else p["alt"]) * SECTOR)
            f.write(h)
        f.flush()
        os.fsync(f.fileno())
    read(img)       # both check again
    return ids(img)


def main(argv=None):
    a = sys.argv[1:] if argv is None else argv
    try:
        if len(a) == 3 and a[0] == "part" and a[2].isdigit():
            first, size = part(a[1], int(a[2]))
            print(first, size)
        elif len(a) == 2 and a[0] == "ids":
            for n, g in ids(a[1]):
                print(n, g)
        elif len(a) == 2 and a[0] == "new-ids":
            for n, g in new_ids(a[1]):
                print(n, g)
        else:
            print(__doc__.split("\n\n")[1], file=sys.stderr)
            return 2
    except (Refused, OSError) as e:
        print(f"img_gpt: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
