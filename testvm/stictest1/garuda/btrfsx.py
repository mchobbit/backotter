#!/usr/bin/env python3
"""btrfs facts for garuda's check (the guest has no compsize), by ioctl:

  btrfsx.py super DEV              the superblock's features, one JSON line
                                   (btrfs inspect-internal dump-super)
  btrfsx.py mkfs DEV LISTALL       mkfs.btrfs options for a file system made
                                   like DEV's (its features, checksum, node
                                   and sector size), one per line; LISTALL:
                                   a file holding `mkfs.btrfs -O list-all`'s
                                   output, the names this mkfs knows
  btrfsx.py files ROOT NAME...     per NAME under ROOT, one line:
                                   NAME FLAGS PROPERTY ENCODED/EXTENTS
                                   (FS_IOC_GETFLAGS in hex, btrfs.compression
                                   or -, FIEMAP's extents with
                                   FIEMAP_EXTENT_ENCODED: compressed ones)
  btrfsx.py subvols ROOT NAME...   per NAME: NAME subvol-ro, subvol-rw or dir
                                   (a subvolume's root is inode 256;
                                   BTRFS_IOC_SUBVOL_GETFLAGS's read-only bit)
  btrfsx.py placeholders ROOT      every directory under ROOT (relative to
                                   it) that is btrfs's placeholder for a
                                   nested subvolume inside a snapshot: inode
                                   2 (BTRFS_EMPTY_SUBVOL_DIR_OBJECTID) on a
                                   file system whose statfs type is btrfs's.
                                   btrfs stores nothing there: new_simple_dir()
                                   makes it in memory with the current time,
                                   so its times are when it was looked up
"""
import fcntl, json, os, re, struct, subprocess, sys

# The superblock's feature bits (fs/btrfs/ctree.h, btrfs-progs kernel-shared).
INCOMPAT = {0x1: "MIXED_BACKREF", 0x2: "DEFAULT_SUBVOL", 0x4: "MIXED_GROUPS", 0x8: "COMPRESS_LZO",
            0x10: "COMPRESS_ZSTD", 0x20: "BIG_METADATA", 0x40: "EXTENDED_IREF", 0x80: "RAID56",
            0x100: "SKINNY_METADATA", 0x200: "NO_HOLES", 0x400: "METADATA_UUID", 0x800: "RAID1C34",
            0x1000: "ZONED", 0x2000: "EXTENT_TREE_V2", 0x4000: "RAID_STRIPE_TREE", 0x10000: "SIMPLE_QUOTA"}
COMPAT_RO = {0x1: "FREE_SPACE_TREE", 0x2: "FREE_SPACE_TREE_VALID", 0x4: "VERITY", 0x8: "BLOCK_GROUP_TREE"}
# mkfs.btrfs -O names of the bits mkfs chooses; "off" ones are disabled
# (^name) when DEV lacks them, so that a newer mkfs's defaults don't add them.
MKFS = [("incompat", 0x4, "mixed-bg", False), ("incompat", 0x40, "extref", True),
        ("incompat", 0x100, "skinny-metadata", True), ("incompat", 0x200, "no-holes", True),
        ("incompat", 0x4000, "raid-stripe-tree", True), ("incompat", 0x10000, "squota", True),
        ("compat_ro", 0x1, "free-space-tree", True), ("compat_ro", 0x8, "block-group-tree", True)]


def superblock(dev):
    out = subprocess.run(["btrfs", "inspect-internal", "dump-super", dev], capture_output=True, text=True).stdout
    def num(k):
        m = re.search(r"^" + k + r"\s+(0x[0-9a-fA-F]+|[0-9]+)", out, re.M)
        return int(m[1], 0) if m else None
    m = re.search(r"^csum_type\s+\S+\s+\((\S+)\)", out, re.M)
    s = {"incompat": num("incompat_flags"), "compat_ro": num("compat_ro_flags"), "csum": m[1] if m else None,
         "nodesize": num("nodesize"), "sectorsize": num("sectorsize")}
    s["incompat_names"] = [n for b, n in sorted(INCOMPAT.items()) if (s["incompat"] or 0) & b]
    s["compat_ro_names"] = [n for b, n in sorted(COMPAT_RO.items()) if (s["compat_ro"] or 0) & b]
    return s


a = sys.argv[1:]
if not a:
    sys.exit(__doc__)
if a[0] == "super":
    print(json.dumps(superblock(a[1]), sort_keys=True))
elif a[0] == "mkfs":
    s = superblock(a[1])
    known = {l.split()[0] for l in open(a[2], errors="replace") if l.strip() and not l.startswith(" ")}
    feats = []
    for word, bit, name, off in MKFS:
        if name not in known:
            continue
        if (s[word] or 0) & bit: feats.append(name)
        elif off: feats.append("^" + name)
    opts = ["-O", ",".join(feats)] if feats else []
    if s["csum"]: opts += ["--csum", s["csum"]]
    if s["nodesize"]: opts += ["-n", str(s["nodesize"])]
    if s["sectorsize"]: opts += ["-s", str(s["sectorsize"])]
    print("\n".join(opts))
elif a[0] == "files":
    FS_IOC_GETFLAGS, FS_IOC_FIEMAP = 0x80086601, 0xC020660B
    FIEMAP_FLAG_SYNC, LAST, ENCODED, N = 0x1, 0x1, 0x8, 256
    for name in a[2:]:
        p = os.path.join(a[1], name)
        try:
            fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NOATIME", 0))
        except OSError as e:
            print(f"{name} - - error-{e.errno}"); continue
        try:
            flags = struct.unpack("l", fcntl.ioctl(fd, FS_IOC_GETFLAGS, b"\0" * 8))[0] & 0xffffffff
            try: prop = os.getxattr(p, "btrfs.compression").decode() or "-"
            except OSError: prop = "-"
            enc = tot = 0
            if not os.path.isdir(p):
                start, done = 0, False
                while not done:
                    buf = bytearray(struct.pack("QQIIII", start, 2**64 - 1 - start, FIEMAP_FLAG_SYNC, 0, N, 0) + b"\0" * 56 * N)
                    fcntl.ioctl(fd, FS_IOC_FIEMAP, buf, True)
                    n = struct.unpack_from("I", buf, 20)[0]
                    if n == 0: break
                    for i in range(n):
                        lo, ph, ln, _, _, fl = struct.unpack_from("QQQQQI", buf, 32 + 56 * i)
                        tot += 1; enc += bool(fl & ENCODED); start = lo + ln
                        if fl & LAST: done = True
            print(f"{name} {flags:#x} {prop} {enc}/{tot}")
        finally:
            os.close(fd)
elif a[0] == "subvols":
    BTRFS_IOC_SUBVOL_GETFLAGS = 0x80089419
    for name in a[2:]:
        p = os.path.join(a[1], name)
        try:
            st = os.lstat(p)
        except OSError as e:
            print(f"{name} error-{e.errno}"); continue
        if st.st_ino != 256:
            print(f"{name} dir"); continue
        fd = os.open(p, os.O_RDONLY | os.O_DIRECTORY)
        try:
            f = struct.unpack("Q", fcntl.ioctl(fd, BTRFS_IOC_SUBVOL_GETFLAGS, b"\0" * 8))[0]
            print(f"{name} subvol-{'ro' if f & 2 else 'rw'}")
        except OSError as e:
            print(f"{name} subvol-error-{e.errno}")
        finally:
            os.close(fd)
elif a[0] == "placeholders":
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    BTRFS_SUPER_MAGIC, EMPTY_SUBVOL_DIR = 0x9123683E, 2
    def fstype(p):
        buf = ctypes.create_string_buffer(256)   # struct statfs; f_type (a long) first
        return struct.unpack_from("l", buf, 0)[0] & 0xffffffff if libc.statfs(os.fsencode(p), buf) == 0 else None
    root = a[1]
    for dp, dns, fns in os.walk(root):
        for n in dns:
            p = os.path.join(dp, n)
            try: st = os.lstat(p)
            except OSError: continue
            if st.st_ino == EMPTY_SUBVOL_DIR and fstype(p) == BTRFS_SUPER_MAGIC:
                print(os.path.relpath(p, root))
else:
    sys.exit(__doc__)
