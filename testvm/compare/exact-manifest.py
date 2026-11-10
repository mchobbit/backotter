#!/usr/bin/env python3
"""exact-manifest.py DIR: one line per entry below DIR, for exact-rt.sh to
compare a source tree with its restore: type, mode, owner, mtime and atime
(ns), size, SHA-256, symlink target, extended attributes, and the entry's
hard-link group (the first path with its inode).  Nested subvolumes'
stubs (empty directories with inode 2) are listed as directories."""
import hashlib
import os
import stat
import sys

root = sys.argv[1]
first = {}
rows = []
for dirpath, dirnames, filenames in os.walk(root):
    dirnames.sort()
    for name in sorted(dirnames + filenames):
        p = os.path.join(dirpath, name)
        rel = os.path.relpath(p, root)
        st = os.lstat(p)
        kind = stat.S_IFMT(st.st_mode)
        extra = ""
        if stat.S_ISREG(st.st_mode):
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for b in iter(lambda: f.read(1 << 20), b""):
                    h.update(b)
            extra = f"{st.st_size} {h.hexdigest()}"
            key = (st.st_dev, st.st_ino)
            group = first.setdefault(key, rel)
            extra += f" link={group}"
        elif stat.S_ISLNK(st.st_mode):
            extra = "-> " + os.readlink(p)
        try:
            xa = sorted((a, os.getxattr(p, a, follow_symlinks=False)) for a in os.listxattr(p, follow_symlinks=False))
        except OSError:
            xa = []
        atime = st.st_atime_ns if not stat.S_ISDIR(st.st_mode) else "-"
        rows.append(f"{rel} {kind:o} {stat.S_IMODE(st.st_mode):o} {st.st_uid}:{st.st_gid} m={st.st_mtime_ns} a={atime} {extra} {xa}")
print("\n".join(rows))
