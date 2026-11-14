#!/usr/bin/env python3
"""The guest has no sfdisk (elementary's live system leaves out fdisk), so
the GPT is read and copied here, in Python.

  gptcopy.py dump DEV-or-IMAGE
      "gpt FIRST-LBA LAST-LBA SECTOR-SIZE", then one line per partition:
      START SIZE TYPE-GUID NAME ATTRS (sectors; the type upper-case as
      sfdisk prints it; NAME "-" if empty, spaces as "_"; ATTRS in hex, "-"
      if 0).  No ids: the disk's and partitions' GUIDs are fresh in a copy.
  gptcopy.py copy SRC DST
      DST (a disk or an image of SRC's size) given SRC's GPT: a protective
      MBR, the primary and backup headers and entry arrays with SRC's
      first/last usable LBA, entry count and size, each partition's start,
      end, type, name and attributes; fresh disk and partition GUIDs.  The
      kernel is told nothing (blockdev --rereadpt, partx or losetup -P).
"""
import os, struct, sys, uuid, zlib

SS = 512
HDR = struct.Struct("<8sIIIIQQQQ16sQIII")   # 92 bytes


def size(f):
    f.seek(0, os.SEEK_END)
    return f.tell()


def read(dev):
    with open(dev, "rb") as f:
        n = size(f) // SS
        f.seek(SS)
        h = HDR.unpack(f.read(HDR.size))
        sig, rev, hsz, _crc, _r, my, alt, first, last, guid, elba, nent, esz, _ecrc = h
        if sig != b"EFI PART":
            raise SystemExit(f"gptcopy: no GPT on {dev}")
        f.seek(elba * SS)
        raw = f.read(nent * esz)
    parts = []
    for i in range(nent):
        e = raw[i * esz:(i + 1) * esz]
        t, u, s, end, attrs = struct.unpack_from("<16s16sQQQ", e)
        if t == b"\0" * 16:
            continue
        name = e[56:128].decode("utf-16-le").rstrip("\0")
        parts.append((i, t, s, end, attrs, name))
    return dict(sectors=n, rev=rev, hsz=hsz, first=first, last=last, nent=nent, esz=esz), parts


def dump(dev):
    h, parts = read(dev)
    print("gpt", h["first"], h["last"], SS)
    for _i, t, s, end, attrs, name in parts:
        print(s, end - s + 1, str(uuid.UUID(bytes_le=t)).upper(), name.replace(" ", "_") or "-", hex(attrs) if attrs else "-")


def copy(src, dst):
    h, parts = read(src)
    ent = bytearray(h["nent"] * h["esz"])
    for i, t, s, end, attrs, name in parts:
        e = struct.pack("<16s16sQQQ", t, uuid.uuid4().bytes_le, s, end, attrs) + name.encode("utf-16-le")[:72].ljust(72, b"\0")
        ent[i * h["esz"]:i * h["esz"] + len(e)] = e
    ecrc = zlib.crc32(ent) & 0xffffffff
    esec = (len(ent) + SS - 1) // SS
    fd = os.open(dst, os.O_RDWR)
    with os.fdopen(fd, "r+b") as f:
        n = size(f) // SS
        if n != h["sectors"]:
            raise SystemExit(f"gptcopy: {dst} has {n} sectors, {src} {h['sectors']}")
        disk = uuid.uuid4().bytes_le

        def header(my, alt, elba):
            b = HDR.pack(b"EFI PART", h["rev"], HDR.size, 0, 0, my, alt, h["first"], h["last"], disk, elba, h["nent"], h["esz"], ecrc)
            b = HDR.pack(b"EFI PART", h["rev"], HDR.size, zlib.crc32(b) & 0xffffffff, 0, my, alt, h["first"], h["last"], disk, elba, h["nent"], h["esz"], ecrc)
            return b.ljust(SS, b"\0")
        mbr = bytearray(SS)
        mbr[446:462] = struct.pack("<B3sB3sII", 0, b"\x00\x02\x00", 0xEE, b"\xff\xff\xff", 1, min(n - 1, 0xffffffff))
        mbr[510:512] = b"\x55\xaa"
        for off, data in ((0, bytes(mbr)), (SS, header(1, n - 1, 2)), (2 * SS, bytes(ent)),
                          ((n - 1 - esec) * SS, bytes(ent)), ((n - 1) * SS, header(n - 1, 1, n - 1 - esec))):
            f.seek(off)
            f.write(data)
        f.flush()
        os.fsync(f.fileno())


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "dump":
        dump(sys.argv[2])
    elif len(sys.argv) == 4 and sys.argv[1] == "copy":
        copy(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit(__doc__)
