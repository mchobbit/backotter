#!/usr/bin/env python3
"""nodecopy.py [--nodes] SRC DST: copy the tree SRC to DST (made, must not
exist) as root, with every extended attribute (trusted.* and security.*
included: cp's libattr leaves some out), owners, modes, hard links and
times (mtime and atime, ns), not crossing into other file systems.

--nodes DIRS: only what overlayfs and containers storage keep in
metadata: device nodes (the whiteouts, char 0:0, among them) and any
entry with a trusted.* xattr (a regular one with its contents), with the directories above each (their own metadata too), in
at most DIRS directories: the trusted.* entries and other device nodes
first, then the whiteouts in walk order while their directories fit.
The guest's /var/lib/abroot/storage is ~220,000 entries and 7.6 GiB, its
~7,000 whiteouts in ~31,000 directories, more than the check's node
listing (one restic call a directory) gets through in time.

Prints "COUNT entries copied (WHITEOUTS whiteouts, TRUSTED with trusted.*)"."""
import os, stat, sys

args = sys.argv[1:]
nodes = args[0] == "--nodes"
maxdirs = 0
if nodes:
    maxdirs, args = int(args[1]), args[2:]
src, dst = (os.path.abspath(a) for a in args)


def xattrs(p):
    try:
        return {n: os.getxattr(p, n, follow_symlinks=False) for n in os.listxattr(p, follow_symlinks=False)}
    except OSError:
        return {}


def keep(p, st, xs):
    if not nodes:
        return True
    if stat.S_ISCHR(st.st_mode) or stat.S_ISBLK(st.st_mode):
        return True
    return any(n.startswith("trusted.") for n in xs)


dev = os.lstat(src).st_dev
chosen = {}          # relative path -> (lstat, xattrs)
first, later = [], []
for dp, dns, fns in os.walk(src):
    dns.sort(); fns.sort()
    dns[:] = [d for d in dns if os.lstat(os.path.join(dp, d)).st_dev == dev]
    for n in [""] + dns + fns:
        p = os.path.join(dp, n) if n else dp
        st = os.lstat(p)
        if stat.S_ISSOCK(st.st_mode):
            continue
        xs = xattrs(p)
        if keep(p, st, xs) or p == src:
            wh = stat.S_ISCHR(st.st_mode) and st.st_rdev == 0 and not any(x.startswith("trusted.") for x in xs)
            (later if nodes and wh else first).append((os.path.relpath(p, src), st, xs, nodes and wh))
ndirs = 0
for rel, st, xs, budgeted in first + later:
    up, r = [], rel
    while r not in ("", "."):
        r = os.path.dirname(r) or "."
        if r in chosen:
            break
        up.append(r)
    if maxdirs and budgeted and ndirs + len(up) > maxdirs:
        continue
    chosen[rel] = (st, xs)
    for r in up:
        q = os.path.join(src, r)
        chosen[r] = (os.lstat(q), xattrs(q)); ndirs += 1
links = {}
wh = tr = 0
for rel in sorted(chosen, key=lambda r: (r != ".", r.count("/"), r)):
    st, xs = chosen[rel]
    p, q = os.path.join(src, rel), os.path.normpath(os.path.join(dst, rel))
    m = st.st_mode
    if stat.S_ISDIR(m):
        os.makedirs(q, exist_ok=True)
    elif not stat.S_ISDIR(m) and st.st_nlink > 1 and (st.st_dev, st.st_ino) in links:
        os.link(links[(st.st_dev, st.st_ino)], q)
        continue
    elif stat.S_ISREG(m):
        with open(p, "rb") as a, open(q, "wb") as b:
            while True:
                c = a.read(1 << 20)
                if not c:
                    break
                b.write(c)
    elif stat.S_ISLNK(m):
        os.symlink(os.readlink(p), q)
    else:
        os.mknod(q, m, st.st_rdev)
        if stat.S_ISCHR(m) and st.st_rdev == 0:
            wh += 1
    if not stat.S_ISDIR(m) and st.st_nlink > 1:
        links[(st.st_dev, st.st_ino)] = q
    os.lchown(q, st.st_uid, st.st_gid)
    if not stat.S_ISLNK(m):
        os.chmod(q, stat.S_IMODE(m))
    for n, v in xs.items():
        os.setxattr(q, n, v, follow_symlinks=False)
    tr += any(n.startswith("trusted.") for n in xs)
# Times last, deepest first: making an entry changes its directory's mtime.
for rel in sorted(chosen, key=lambda r: -r.count("/")):
    st, _ = chosen[rel]
    os.utime(os.path.normpath(os.path.join(dst, rel)), ns=(st.st_atime_ns, st.st_mtime_ns), follow_symlinks=False)
print(f"{len(chosen)} entries copied ({wh} whiteouts, {tr} with trusted.*)")
