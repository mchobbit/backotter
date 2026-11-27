import os, stat, struct, sys, pwd, grp, collections
O_XATTR = 0x4000
S_IFDOOR = 0xD000; S_IFPORT = 0xE000
def nvbools(b):
    out = {}; off = 12
    while off + 12 <= len(b):
        size, _, namelen = struct.unpack_from(">III", b, off)
        if size == 0: break
        name = b[off+12:off+12+namelen].decode()
        p = off + 12 + (namelen + 3)//4*4
        if p + 12 > len(b): break
        typ, nelem = struct.unpack_from(">II", b, p)
        if typ == 21:   # boolean_value
            out[name] = struct.unpack_from(">I", b, p+8)[0]
        elif name == "crtime":
            out[name] = 1
        else:
            out[name] = "t%d" % typ
        off += size
    return out
C = collections.Counter(); ex = collections.defaultdict(list)
def note(k, p):
    C[k] += 1
    if len(ex[k]) < 3: ex[k].append(p)
users = {u.pw_uid for u in pwd.getpwall()}; groups = {g.gr_gid for g in grp.getgrall()}
seen_dev = set()
for root in sys.argv[1:]:
    try: rdev = os.lstat(root).st_dev
    except OSError: continue
    if rdev in seen_dev: continue
    seen_dev.add(rdev)
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if os.lstat(os.path.join(d, x)).st_dev == rdev]
        for n in ["."] * (d == root) + dirs + files:
            p = os.path.join(d, n)
            try: st = os.lstat(p)
            except OSError: continue
            m = st.st_mode; t = stat.S_IFMT(m)
            C["total"] += 1
            if t == stat.S_IFCHR: note("chr", p)
            elif t == stat.S_IFBLK: note("blk", p)
            elif t == stat.S_IFIFO: note("fifo", p)
            elif t == stat.S_IFSOCK: note("sock", p)
            elif t == S_IFDOOR: note("door", p)
            elif t == S_IFPORT: note("port", p)
            elif t == stat.S_IFLNK: C["symlink"] += 1
            if t == stat.S_IFREG and st.st_nlink > 1: note("hardlinked", p)
            if t == stat.S_IFREG and st.st_size > 65536 and st.st_blocks * 512 + 131072 < st.st_size: note("sparse", p)
            if m & 0o4000: note("setuid", p)
            if m & 0o2000: note("setgid", p)
            if m & 0o1000: note("sticky", p)
            if st.st_uid not in users: note("uid-noname", "%s uid=%d" % (p, st.st_uid))
            if st.st_gid not in groups: note("gid-noname", "%s gid=%d" % (p, st.st_gid))
            if st.st_uid > 2147483647 or st.st_gid > 2147483647: note("ephemeral-id", p)
            if t in (stat.S_IFREG, stat.S_IFDIR) or t == stat.S_IFLNK:
                try:
                    fd = os.open(p, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
                except OSError:
                    continue
                try:
                    a = os.open(".", os.O_RDONLY | O_XATTR, dir_fd=fd)
                    names = os.listdir(a)
                    for x in names:
                        if x in ("SUNWattr_ro", "SUNWattr_rw"): continue
                        note("xattr:" + x, p)
                    if "SUNWattr_rw" in names:
                        v = os.open("SUNWattr_rw", os.O_RDONLY | O_XATTR, dir_fd=fd)
                        b = os.read(v, 65536); os.close(v)
                        try: nv = nvbools(b)
                        except struct.error: nv = {}; C["nv-parse-err"] += 1
                        if "crtime" in nv: C["crtime"] += 1
                        for k, val in nv.items():
                            if k in ("archive", "av_modified"):
                                if val == 0: note("sysattr-off:" + k, p)
                            elif val == 1 and k != "crtime": note("sysattr:" + k, p)
                            elif isinstance(val, str): C["nv:%s:%s" % (k, val)] += 1
                    os.close(a)
                except OSError as e:
                    C["xattr-err:%s" % e.errno] += 1
                finally:
                    os.close(fd)
for k in sorted(C):
    print(k, C[k], " ".join(ex.get(k, [])))
