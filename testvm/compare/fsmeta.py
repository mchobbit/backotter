#!/usr/bin/env python3
"""Record and compare file metadata, to check backup-and-restore round trips.

Usage:
  fsmeta.py record ROOT OUT.json       record every file under ROOT (as root)
  fsmeta.py compare BEFORE.json AFTER.json [--json OUT.json]

A record holds, per path: type, permission bits, owner, mtime and atime,
size and SHA-256 of regular files, symlink target, device number, hard-link
group, every extended attribute (SELinux labels, file capabilities, POSIX ACLs
and user/trusted attributes included), inode flags (immutable, append-only,
case-insensitive, ...), project ID, ZFS's DOS attributes (on ZFS only), and
whether a regular file has holes.
Record a restored tree on a file system mounted noatime: reading it would
change the atimes being recorded.  ROOT may span several filesystems
(e.g. / and /boot): the walk crosses into whatever is mounted below it.

compare prints, per kind of metadata, how many paths differ, with examples.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import sys

FS_IOC_GETFLAGS = 0x80086601
FS_IOC_FSGETXATTR = 0x801C581F
# FS_FL_USER_VISIBLE, plus three that chattr(1) sets but the kernel leaves
# out of it: FS_NOCOW_FL (btrfs's C), FS_PROJINHERIT_FL (P) and
# FS_CASEFOLD_FL (F).
FS_FL_USER_VISIBLE = 0x0003DFFF | 0x00800000 | 0x20000000 | 0x40000000
# Set by the filesystem itself, not restorable: ext4's hashed-directory index.
FLAGS_FS_INTERNAL = 0x00001000
FLAG_NAMES = {0x10: "immutable", 0x20: "append", 0x40: "nodump", 0x80: "noatime",
              0x00800000: "nocow", 0x40000000: "casefold"}
ZFS_IOC_GETDOSFLAGS = 0x80088301
# The DOS attributes BackOtter keeps (linux.zfs_dos_flags): readonly, hidden,
# system, archive, nounlink, offline, sparse.  Immutable, append-only and
# nodump are inode flags as well.
ZFS_DOS_KEPT = 0x0000302F00000000
ZFS_ARCHIVE = 0x0000000800000000
DOS_NAMES = {0x100000000: "dos readonly", 0x200000000: "dos hidden", 0x400000000: "dos system",
             0x2000000000: "dos nounlink", 0x100000000000: "dos offline", 0x200000000000: "dos sparse"}
SEEK_HOLE = 4
TYPES = {stat.S_IFREG: "file", stat.S_IFDIR: "dir", stat.S_IFLNK: "symlink",
         stat.S_IFCHR: "chardev", stat.S_IFBLK: "blockdev", stat.S_IFIFO: "fifo",
         stat.S_IFSOCK: "socket"}


def xattr_kind(name: str) -> str:
    if name == "security.selinux":
        return "selinux"
    if name == "security.capability":
        return "capability"
    if name.startswith("system.posix_acl_"):
        return "acl"
    if name.startswith("user."):
        return "user-xattr"
    if name.startswith("trusted."):
        return "trusted-xattr"
    return "other-xattr"


def inode_flags(path: str, is_dir: bool) -> int | None:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_DIRECTORY if is_dir else 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        buf = bytearray(8)
        fcntl.ioctl(fd, FS_IOC_GETFLAGS, buf)
        return int.from_bytes(buf[:4], "little") & FS_FL_USER_VISIBLE
    except OSError:
        return None
    finally:
        os.close(fd)


def project_id(path: str, is_dir: bool) -> int | None:
    """The project ID (FS_IOC_FSGETXATTR's fsx_projid), where there is one."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_DIRECTORY if is_dir else 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        buf = bytearray(28)
        fcntl.ioctl(fd, FS_IOC_FSGETXATTR, buf)
        return int.from_bytes(buf[12:16], "little")
    except OSError:
        return None
    finally:
        os.close(fd)


def zfs_devices() -> set[int]:
    """The st_dev of every mounted ZFS file system: ZFS's own ioctl numbers may
    mean something else to another file system's driver."""
    devs = set()
    with open("/proc/self/mounts") as f:
        for line in f:
            _, mp, fstype = line.split()[:3]
            if fstype == "zfs":
                try:
                    devs.add(os.stat(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), mp)).st_dev)
                except OSError:
                    pass
    return devs


def dos_flags(path: str, is_dir: bool) -> int | None:
    """ZFS's DOS attributes (ZFS_IOC_GETDOSFLAGS), the ones BackOtter keeps."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_DIRECTORY if is_dir else 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        buf = bytearray(8)
        fcntl.ioctl(fd, ZFS_IOC_GETDOSFLAGS, buf)
        return int.from_bytes(buf, "little") & ZFS_DOS_KEPT
    except OSError:
        return None
    finally:
        os.close(fd)


def content(path: str, st: os.stat_result) -> tuple[str, bool]:
    """SHA-256 of the file, and whether it has holes.  A preallocated file's
    unwritten extents look like holes to SEEK_HOLE, but count in st_blocks."""
    h = hashlib.sha256()
    size = st.st_size
    with open(path, "rb", buffering=0) as f:
        try:
            holes = (size > 0 and st.st_blocks * 512 < size
                     and os.lseek(f.fileno(), 0, SEEK_HOLE) < size)
        except OSError:
            holes = False
        os.lseek(f.fileno(), 0, os.SEEK_SET)
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest(), holes


def record(root: str) -> dict[str, dict]:
    entries: dict[str, dict] = {}
    inodes: dict[tuple[int, int], list[str]] = {}
    zfs = zfs_devices()
    # "." is ROOT itself: its owner, mode and labels matter as much as any
    # file's (an unlabeled / stops an SELinux system from booting), and a
    # restore into an existing mount point easily misses them.
    walk = [(root, [], ["."])] + [(d, dn, fn) for d, dn, fn in os.walk(root)]
    for dirpath, dirnames, filenames in walk:
        for name in dirnames + filenames:
            path = os.path.normpath(os.path.join(dirpath, name))
            rel = os.path.relpath(path, root)
            st = os.lstat(path)
            kind = TYPES.get(stat.S_IFMT(st.st_mode), "unknown")
            e = {"type": kind, "mode": stat.S_IMODE(st.st_mode),
                 "uid": st.st_uid, "gid": st.st_gid, "mtime": st.st_mtime_ns,
                 "atime": st.st_atime_ns}
            if kind == "file":
                e["size"] = st.st_size
                e["blocks"] = st.st_blocks
                e["sha256"], e["holes"] = content(path, st)
            elif kind == "symlink":
                e["target"] = os.readlink(path)
            elif kind in ("chardev", "blockdev"):
                e["rdev"] = [os.major(st.st_rdev), os.minor(st.st_rdev)]
            try:
                e["xattrs"] = {n: os.getxattr(path, n, follow_symlinks=False).hex()
                               for n in sorted(os.listxattr(path, follow_symlinks=False))}
            except OSError:
                e["xattrs"] = {}
            if kind in ("file", "dir"):
                e["flags"] = inode_flags(path, kind == "dir")
                e["projid"] = project_id(path, kind == "dir")
                e["dosflags"] = dos_flags(path, kind == "dir") if st.st_dev in zfs else None
            if kind != "dir" and st.st_nlink > 1:
                inodes.setdefault((st.st_dev, st.st_ino), []).append(rel)
            entries[rel] = e
    for group in inodes.values():
        for rel in group:
            entries[rel]["links"] = sorted(group)
    return entries


def differences(before: dict[str, dict], after: dict[str, dict]) -> dict[str, list[str]]:
    diff: dict[str, list[str]] = {}

    def note(kind: str, path: str) -> None:
        diff.setdefault(kind, []).append(path)

    for path in sorted(before.keys() - after.keys()):
        note("missing", path)
    for path in sorted(after.keys() - before.keys()):
        note("extra", path)
    for path in sorted(before.keys() & after.keys()):
        b, a = before[path], after[path]
        if b["type"] != a["type"]:
            note("type", path)
            continue
        kind = b["type"]
        if b["mode"] != a["mode"]:
            note("mode", path)
        if (b["uid"], b["gid"]) != (a["uid"], a["gid"]):
            note("owner", path)
        if b["mtime"] != a["mtime"]:
            note({"dir": "mtime (dirs)", "symlink": "mtime (symlinks)"}.get(kind, "mtime"), path)
        if "atime" in b and "atime" in a and b["atime"] != a["atime"]:
            note({"dir": "atime (dirs)", "symlink": "atime (symlinks)"}.get(kind, "atime"), path)
        if kind == "file":
            if (b["size"], b["sha256"]) != (a["size"], a["sha256"]):
                note("content", path)
            if b["holes"] and not a["holes"]:
                note("holes lost", path)
            if a["holes"] and not b["holes"]:
                note("holes gained", path)
            # Fully allocated before (a swap file, a preallocated file), not
            # after: swapon refuses such a file even where SEEK_HOLE finds no
            # hole.
            if ("blocks" in b and "blocks" in a and b["size"] > 0
                    and b["blocks"] * 512 >= b["size"] and a["blocks"] * 512 < a["size"]):
                note("allocation lost", path)
        if b.get("target") != a.get("target"):
            note("symlink target", path)
        if b.get("rdev") != a.get("rdev"):
            note("device number", path)
        if b.get("links") != a.get("links"):
            note("hard links", path)
        bx, ax = b["xattrs"], a["xattrs"]
        for name in sorted(bx.keys() | ax.keys()):
            if bx.get(name) != ax.get(name):
                note(xattr_kind(name), path)
        bf, af = b.get("flags"), a.get("flags")
        if bf is not None and af is not None and (bf ^ af) & ~FLAGS_FS_INTERNAL:
            note("inode flags", path)
        if b.get("projid") and b.get("projid") != a.get("projid"):
            note("project ID", path)
        # Like the inode flags: only where both sides have them (a restore
        # onto another file system than ZFS warns that it can't set them).
        bd, ad = b.get("dosflags"), a.get("dosflags")
        if bd is not None and ad is not None and bd != ad:
            note("ZFS DOS flags", path)
    return diff


# Shown in this order; anything else after.
ORDER = ["missing", "extra", "type", "content", "mode", "owner", "mtime", "mtime (dirs)",
         "mtime (symlinks)", "atime", "atime (dirs)", "atime (symlinks)", "symlink target",
         "device number", "hard links", "selinux", "capability", "acl", "user-xattr",
         "trusted-xattr", "other-xattr", "inode flags", "project ID", "ZFS DOS flags",
         "holes lost", "holes gained", "allocation lost"]


def coverage(before: dict[str, dict]) -> dict[str, int]:
    """How many paths in the original carry each kind of interesting metadata."""
    c: dict[str, int] = {}
    for e in before.values():
        c[e["type"]] = c.get(e["type"], 0) + 1
        for name in e["xattrs"]:
            c[xattr_kind(name)] = c.get(xattr_kind(name), 0) + 1
        if "links" in e:
            c["hard-linked"] = c.get("hard-linked", 0) + 1
        if e.get("holes"):
            c["with holes"] = c.get("with holes", 0) + 1
        if e.get("projid"):
            c["project ID"] = c.get("project ID", 0) + 1
        if e.get("atime") is not None and e["atime"] != e["mtime"]:
            c["atime != mtime"] = c.get("atime != mtime", 0) + 1
        for bit, name in FLAG_NAMES.items():
            if e.get("flags") and e["flags"] & bit:
                c[name] = c.get(name, 0) + 1
        for bit, name in DOS_NAMES.items():
            if e.get("dosflags") and e["dosflags"] & bit:
                c[name] = c.get(name, 0) + 1
        if e.get("dosflags") is not None and not e["dosflags"] & ZFS_ARCHIVE:
            c["dos noarchive"] = c.get("dos noarchive", 0) + 1
        if e["mode"] & 0o7000:
            c["setuid/setgid/sticky"] = c.get("setuid/setgid/sticky", 0) + 1
    return c


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("root")
    r.add_argument("out")
    c = sub.add_parser("compare")
    c.add_argument("before")
    c.add_argument("after")
    c.add_argument("--json")
    args = ap.parse_args()

    if args.cmd == "record":
        with open(args.out, "w") as f:
            json.dump(record(args.root), f)
        return 0

    with open(args.before) as f:
        before = json.load(f)
    with open(args.after) as f:
        after = json.load(f)
    diff = differences(before, after)
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"paths": len(before), "coverage": coverage(before),
                       "differences": {k: {"count": len(v), "examples": v[:5]}
                                       for k, v in diff.items()}}, f, indent=1)
    if not diff:
        print(f"identical: {len(before)} paths")
    for kind in sorted(diff, key=lambda k: (ORDER.index(k) if k in ORDER else 99, k)):
        paths = diff[kind]
        print(f"{kind:18} {len(paths):6}  e.g. {', '.join(repr(p) for p in paths[:3])}")
    return 1 if diff else 0


if __name__ == "__main__":
    sys.exit(main())
