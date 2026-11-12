#!/usr/bin/env python3
"""Give back the space a qcow2 image holds in clusters it no longer uses.

Usage: qcow2-punch.py [--dry-run] IMAGE...

A qcow2 file keeps a reference count for every cluster.  One whose count is
0 belongs to nothing (no guest data, no snapshot, no metadata): qemu reuses
it later, but until then the host's file system still stores whatever it
held.  That is most of what the test guests' images grew by: blocks an
update replaced, freed when vm-update retook the fresh-install snapshot but
never given back, and space from copies that lost the file's holes.  This
punches a hole (fallocate PUNCH_HOLE, the file's size kept) in every run of
such clusters that the host still stores.  No referenced cluster is touched,
so the disk a guest sees, and every snapshot of it, stay as they are; only
the file's bytes in the freed clusters become zeros, so its SHA-256 changes
(testvm/vm-update's image-state: update it after).

It refuses a vm-store object (testvm/vm-store), an image that qemu-img
check finds errors in (a refcount that is too low would mark a used cluster
free), one marked dirty or corrupt, one
with an external data file, and one that anything has open: qemu-img must be
able to open it for writing, which a VM, an overlay's reader or another
qemu-img prevents.  It reads the reference counts itself, while holding that
open, so nothing can change them meanwhile.

Prints, per image, the bytes the host stored before and after.
"""
import ctypes
import ctypes.util
import json
import os
import struct
import subprocess
import sys

FALLOC_FL_KEEP_SIZE, FALLOC_FL_PUNCH_HOLE = 0x01, 0x02
libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
libc.fallocate.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_long, ctypes.c_long]


def die(msg):
    sys.exit(f"qcow2-punch: {msg}")


def in_store(path):
    """Whether PATH is a vm-store object: its realpath lies in (or below) some
    ROOT/objects/ whose ROOT holds store.json (by path, not by permission, so
    root is refused too)."""
    d = os.path.dirname(os.path.realpath(path))
    while d not in ("/", ""):
        if os.path.basename(d) == "objects" and os.path.isfile(os.path.join(os.path.dirname(d), "store.json")):
            return True
        d = os.path.dirname(d)
    return False


def stored(path):
    return os.stat(path).st_blocks * 512


def refcounts(f):
    """The cluster size and a function from cluster index to its refcount."""
    hdr = f.read(104)
    magic, version = struct.unpack(">4sI", hdr[:8])
    if magic != b"QFI\xfb":
        raise ValueError("not a qcow2 image")
    cluster_bits = struct.unpack(">I", hdr[20:24])[0]
    rt_off, rt_clusters = struct.unpack(">QI", hdr[48:60])
    order = 4
    if version >= 3:
        incompat = struct.unpack(">Q", hdr[72:80])[0]
        if incompat & 0b111:  # dirty, corrupt, external data file
            raise ValueError(f"incompatible features {incompat:#x} (dirty, corrupt or an external data file)")
        order = struct.unpack(">I", hdr[96:100])[0]
    cs = 1 << cluster_bits
    bits = 1 << order
    per_block = cs * 8 // bits
    f.seek(rt_off)
    table = struct.unpack(f">{rt_clusters * cs // 8}Q", f.read(rt_clusters * cs))
    cache = {}

    def block(i):
        if i not in cache:
            off = table[i] & 0x00FFFFFFFFFFFE00 if i < len(table) else 0
            if not off:
                cache[i] = None
            else:
                f.seek(off)
                raw = f.read(cs)
                if bits >= 8:
                    fmt = {8: "B", 16: "H", 32: "I", 64: "Q"}[bits]
                    cache[i] = struct.unpack(f">{per_block}{fmt}", raw)
                else:
                    vals, mask = [], (1 << bits) - 1
                    for byte in raw:  # sub-byte refcounts: low bits first
                        for k in range(8 // bits):
                            vals.append((byte >> (k * bits)) & mask)
                    cache[i] = vals
        return cache[i]

    def count(c):
        b = block(c // per_block)
        return 0 if b is None else b[c % per_block]

    return cs, count


def free_runs(path):
    """(offset, length) of the runs of unused clusters the host still stores."""
    size = os.path.getsize(path)
    fd = os.open(path, os.O_RDONLY)
    with os.fdopen(fd, "rb") as f:
        cs, count = refcounts(f)
        runs, start = [], None
        for c in range((size + cs - 1) // cs + 1):
            free = c * cs < size and count(c) == 0
            if free and start is None:
                start = c
            elif not free and start is not None:
                runs.append((start * cs, min(c * cs, size) - start * cs))
                start = None
        # Only what the host still stores: skip runs that are holes already.
        out = []
        for off, length in runs:
            try:
                data = os.lseek(f.fileno(), off, os.SEEK_DATA)
            except OSError:
                continue
            if data < off + length:
                out.append((data, off + length - data))
        return out


def punch(path, dry):
    # Before anything opens it for writing (qemu-io below, then O_WRONLY).
    if in_store(path):
        die(f"{path} is a vm-store object: it is never written")
    r = subprocess.run(["qemu-img", "check", "--output=json", path], capture_output=True, text=True)
    try:
        chk = json.loads(r.stdout)
    except json.JSONDecodeError:
        die(f"{path}: qemu-img check: {r.stderr.strip() or 'no output'} (in use?)")
    if chk.get("check-errors") or chk.get("corruptions") or r.returncode not in (0, 3):
        die(f"{path}: qemu-img check found errors: {r.stdout.strip()} {r.stderr.strip()}")
    # Held open read-write for the whole run: nothing else can open it to
    # write (or read it as a backing file) until it's done.
    holder = subprocess.Popen(["qemu-io", "-f", "qcow2", path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
    holder.stdin.write("help\n")
    holder.stdin.flush()
    line = holder.stdout.readline()
    if holder.poll() is not None or not line:
        err = holder.stderr.read().strip()
        die(f"{path}: can't open it for writing (in use?): {err}")
    try:
        before = stored(path)
        runs = free_runs(path)
        total = sum(length for _, length in runs)
        if not dry:
            fd = os.open(path, os.O_WRONLY)
            try:
                for off, length in runs:
                    if libc.fallocate(fd, FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE, off, length) != 0:
                        e = ctypes.get_errno()
                        die(f"{path}: fallocate at {off}: {os.strerror(e)}")
                os.fsync(fd)
            finally:
                os.close(fd)
        after = stored(path)
    finally:
        holder.stdin.close()
        holder.wait()
    r = subprocess.run(["qemu-img", "check", path], capture_output=True, text=True)
    if r.returncode not in (0, 3):
        die(f"{path}: qemu-img check after: {r.stdout.strip()} {r.stderr.strip()}")
    gib = 1 << 30
    verb = "would free" if dry else "freed"
    print(f"{path}: stored {before / gib:.2f} GiB, {len(runs)} unused runs, {verb} {total / gib:.2f} GiB"
          + ("" if dry else f", now {after / gib:.2f} GiB"))


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    paths = [a for a in args if a != "--dry-run"]
    if not paths:
        sys.exit(__doc__)
    for p in paths:
        try:
            punch(p, dry)
        except ValueError as e:
            die(f"{p}: {e}")


if __name__ == "__main__":
    main()
