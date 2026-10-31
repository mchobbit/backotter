#!/usr/bin/env python3
"""Make, change and dump trees of files with Haiku's typed attributes, for
roundtrip.sh.  Runs on Haiku, through ctypes and libroot's fs_*attr* calls
(Python's os module has none of them).

Usage: attrs.py make DIR     create the test tree in DIR (must not exist)
       attrs.py mutate DIR   change its attributes, as an in-place restore
                             must undo: one added, one removed, one with a
                             new type, one with a new value
       attrs.py dump DIR     one JSON line per entry, sorted: kind, mode,
                             mtime (not a FIFO's), content and every
                             attribute's type, size and hash, a symlink's own
                             included
"""

import ctypes
import errno
import hashlib
import json
import os
import stat
import sys

O_NOTRAVERSE = 0x2000           # Haiku's fcntl.h: open a symlink itself
O_NONBLOCK = 0x80
B_UNSUPPORTED = -0x80000000 + 0x6000 + 14     # Errors.h: B_STORAGE_ERROR_BASE + 14


def code(text):
    """A type code from its four characters, as Haiku writes them: 'MIMS'."""
    return int.from_bytes(text.encode(), "big")


class AttrInfo(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("size", ctypes.c_int64)]


root = ctypes.CDLL("libroot.so", use_errno=True)
root.fs_fopen_attr_dir.argtypes = [ctypes.c_int]
root.fs_fopen_attr_dir.restype = ctypes.c_void_p
root.fs_read_attr_dir.argtypes = [ctypes.c_void_p]
root.fs_read_attr_dir.restype = ctypes.c_void_p
root.fs_close_attr_dir.argtypes = [ctypes.c_void_p]
root.fs_stat_attr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.POINTER(AttrInfo)]
root.fs_read_attr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_int64,
                              ctypes.c_void_p, ctypes.c_size_t]
root.fs_read_attr.restype = ctypes.c_ssize_t
root.fs_write_attr.argtypes = root.fs_read_attr.argtypes
root.fs_write_attr.restype = ctypes.c_ssize_t
root.fs_remove_attr.argtypes = [ctypes.c_int, ctypes.c_char_p]
D_NAME = 26                     # struct dirent: dev_t x2, ino_t x2, ushort


def check(result, what):
    if result < 0:
        err = ctypes.get_errno()
        raise OSError(err, f"{what}: {os.strerror(err)}")
    return result


def node(path):
    """A descriptor for path itself, even a symlink or a FIFO."""
    return os.open(path, os.O_RDONLY | O_NOTRAVERSE | O_NONBLOCK)


def attributes(path):
    """{name: (type, data)} of path itself, or None where BFS keeps none
    (a FIFO)."""
    fd = node(path)
    try:
        d = root.fs_fopen_attr_dir(fd)
        if not d:
            if ctypes.get_errno() in (B_UNSUPPORTED, errno.EOPNOTSUPP, errno.ENOTSUP):
                return None
            check(-1, f"{path}: fs_fopen_attr_dir")
        names = []
        try:
            while True:
                ctypes.set_errno(0)
                entry = root.fs_read_attr_dir(d)
                if not entry:
                    if ctypes.get_errno():
                        check(-1, f"{path}: fs_read_attr_dir")
                    break
                names.append(ctypes.string_at(entry + D_NAME))
        finally:
            root.fs_close_attr_dir(d)
        out = {}
        for name in names:
            info = AttrInfo()
            check(root.fs_stat_attr(fd, name, ctypes.byref(info)), f"{path}: stat {name!r}")
            buf = ctypes.create_string_buffer(max(info.size, 1))
            n = check(root.fs_read_attr(fd, name, info.type, 0, buf, info.size),
                      f"{path}: read {name!r}")
            out[name.decode(errors="surrogateescape")] = (info.type, buf.raw[:n])
        return out
    finally:
        os.close(fd)


def write(path, name, type_code, data):
    fd = node(path)
    try:
        bname = name.encode()
        if not data:
            # A 0-byte write never reaches BFS, so it would create nothing:
            # make the attribute big enough for its own inode, then truncate.
            check(root.fs_write_attr(fd, bname, type_code, 0, b"\0" * 16384, 16384),
                  f"{path}: write {name}")
        check(root.fs_write_attr(fd, bname, type_code, 0, data, len(data)),
              f"{path}: write {name}")
    finally:
        os.close(fd)
    got = attributes(path).get(name)
    if got != (type_code, data):
        sys.exit(f"attrs.py: {path}: {name} came out as {got!r}")


def remove(path, name):
    fd = node(path)
    try:
        check(root.fs_remove_attr(fd, name.encode()), f"{path}: remove {name}")
    finally:
        os.close(fd)


def make(top):
    os.mkdir(top)
    f = os.path.join(top, "plain.txt")
    with open(f, "w") as out:
        out.write("hello\n")
    for name, type_code, data in [
            ("BEOS:TYPE", "MIMS", b"text/plain\0"),
            ("Rating", "LONG", (5).to_bytes(4, "little")),
            ("Big", "LLNG", (1 << 40).to_bytes(8, "little")),
            ("Ratio", "DBLE", bytes.fromhex("182d4454fb210940")),
            ("Flag", "BOOL", b"\1"),
            ("Comment", "CSTR", "héllo wörld\0".encode()),
            ("Raw", "RAWT", bytes(range(256))),
            ("Empty", "CSTR", b""),
            ("Huge", "RAWT", bytes(i % 251 for i in range(20000))),
            ("Custom", "BKOT", b"BackOtter's own type"),
            ("user.note", "XATR", b"an xattr"),
            ("user.empty", "XATR", b"")]:
        write(f, name, code(type_code), data)

    d = os.path.join(top, "folder")
    os.mkdir(d)
    write(d, "Folder:attr", code("CSTR"), b"a directory's\0")
    with open(os.path.join(d, "inner.dat"), "wb") as out:
        out.write(os.urandom(100000))
    write(os.path.join(d, "inner.dat"), "BEOS:TYPE", code("MIMS"),
          b"application/octet-stream\0")

    link = os.path.join(top, "link")
    os.symlink("plain.txt", link)
    write(link, "Link:attr", code("CSTR"), b"the link's own\0")

    ro = os.path.join(top, "readonly.txt")
    with open(ro, "w") as out:
        out.write("read only\n")
    write(ro, "Note", code("CSTR"), b"on a 0444 file\0")
    os.chmod(ro, 0o444)

    u = os.path.join(top, "ünï cødé.txt")
    with open(u, "w") as out:
        out.write("unicode\n")
    write(u, "Ünïcødé:attr", code("CSTR"), "ä\0".encode())

    os.mkfifo(os.path.join(top, "fifo"))
    with open(os.path.join(top, "no-attrs.txt"), "w") as out:
        out.write("none\n")


def mutate(top):
    f = os.path.join(top, "plain.txt")
    os.chmod(os.path.join(top, "readonly.txt"), 0o644)
    write(f, "Extra", code("CSTR"), b"not in the snapshot\0")
    remove(f, "Rating")
    write(f, "Big", code("RAWT"), (1 << 40).to_bytes(8, "little"))   # new type
    write(f, "Comment", code("CSTR"), b"changed\0")                    # new value
    write(f, "user.extra", code("XATR"), b"extra xattr")
    write(os.path.join(top, "no-attrs.txt"), "Extra", code("CSTR"), b"x\0")
    write(os.path.join(top, "link"), "Link:attr", code("CSTR"), b"changed\0")
    os.chmod(os.path.join(top, "readonly.txt"), 0o444)


def dump(top):
    out = []
    for dirpath, dirnames, filenames in os.walk(top):
        # "" is top itself, once.
        for name in sorted(dirnames + filenames) + ([""] if dirpath == top else []):
            path = os.path.join(dirpath, name) if name else dirpath
            st = os.lstat(path)
            entry = {"path": os.path.relpath(path, top), "mode": oct(st.st_mode)}
            # Haiku can't set a FIFO's times (utimensat leaves them as they
            # are, without an error), so no restore can give it its mtime.
            if not stat.S_ISFIFO(st.st_mode):
                entry["mtime"] = st.st_mtime_ns
            if stat.S_ISREG(st.st_mode):
                with open(path, "rb") as f:
                    entry["data"] = hashlib.sha256(f.read()).hexdigest()[:16]
            elif stat.S_ISLNK(st.st_mode):
                entry["target"] = os.readlink(path)
            attrs = attributes(path)
            entry["attrs"] = None if attrs is None else {
                name: ["%08x" % t, len(data), hashlib.sha256(data).hexdigest()[:16]]
                for name, (t, data) in sorted(attrs.items())}
            out.append(entry)
    for entry in sorted(out, key=lambda e: e["path"]):
        print(json.dumps(entry, ensure_ascii=False, sort_keys=True))


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("make", "mutate", "dump"):
        sys.exit(__doc__)
    {"make": make, "mutate": mutate, "dump": dump}[sys.argv[1]](sys.argv[2])


if __name__ == "__main__":
    main()
