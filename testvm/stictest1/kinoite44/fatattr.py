#!/usr/bin/env python3
"""fatattr.py ROOT OUT.json: the FAT attributes (FAT_IOCTL_GET_ATTRIBUTES:
readonly 1, hidden 2, system 4, archive 0x20) of every file and directory
under ROOT on vfat, by path relative to ROOT."""
import fcntl, json, os, struct, sys
root, out = sys.argv[1], sys.argv[2]
res = {}
for dp, dns, fns in os.walk(root):
    for n in [""] + dns + fns:
        p = os.path.join(dp, n) if n else dp
        rel = os.path.relpath(p, root)
        try:
            fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
            try: res[rel] = struct.unpack("I", fcntl.ioctl(fd, 0x80047210, b"\0" * 4))[0]
            finally: os.close(fd)
        except OSError as e: res[rel] = f"error {e.errno}"
json.dump(res, open(out, "w"), indent=0, sort_keys=True)
