#!/usr/bin/env python3
"""SticTest1 haiku6, guest side: fixture, dumps, compares, snapshot checks.
Runs on Haiku, beside attrs.py.

  ck.py make TOP          build the fixture: TOP/anc1/anc2/src (+ TOP/nr for the non-root run)
  ck.py dump DIR OUT      one JSON line per node of DIR (DIR itself as ".")
  ck.py cmp A B NAME      compare two dumps, print "need<TAB>detail" per difference
  ck.py mutate DIR        change a restored tree for the restore-over test
  ck.py tree TREEJSON SRCDUMP   check the snapshot's nodes (rustic cat tree output)
  ck.py anc A B           compare the ancestors' metadata (dump lines of anc1, anc2)
"""
import base64, hashlib, json, os, stat, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import attrs  # noqa: E402

code = attrs.code
XATR = code("XATR")
T1 = 1_600_000_000  # base time, seconds


def setattr_(p, name, t, data):
    attrs.write(p, name, code(t) if isinstance(t, str) else t, data)


def copy_real(src, dst):
    """A real file's or directory's typed attributes onto dst."""
    a = attrs.attributes(src) or {}
    for n, (t, d) in a.items():
        attrs.write(dst, n, t, d)
    return len(a)


def make(top):
    anc1 = os.path.join(top, "anc1")
    anc2 = os.path.join(anc1, "anc2")
    src = os.path.join(anc2, "src")
    os.makedirs(anc2)
    setattr_(anc1, "Anc:note", "CSTR", b"ancestor one\0")
    setattr_(anc2, "user.anc", "XATR", b"ancestor two xattr")
    # attrs.py's tree: many types, empty/huge attrs, xattrs, dir/symlink attrs, 0444, unicode, fifo
    attrs.make(src)
    s = lambda *p: os.path.join(src, *p)
    real = []
    # Real attributes from the guest's own files (copied onto fixture files).
    for i, (rp, kind) in enumerate([
            ("/boot/home/Desktop", "dir"),
            ("/boot/home", "dir"),
            ("/boot/system/apps/StyledEdit", "file"),
            ("/boot/system/apps/Terminal", "file"),
            ("/boot/home/config/settings/Tracker/TrackerSettings", "file"),
            ("/boot/home/config/settings/Deskbar/settings", "file"),
            ("/boot/system/data/licenses/MIT", "file"),
            ("/boot/home/config/settings/beos_mime/text/plain", "file"),
            ("/boot/system/settings/network/hosts", "file")]):
        if not os.path.exists(rp):
            continue
        dp = s("real%d_%s" % (i, os.path.basename(rp)))
        if kind == "dir":
            os.mkdir(dp)
        else:
            with open(rp, "rb") as f, open(dp, "wb") as o:
                o.write(f.read())
        try:
            real.append((rp, copy_real(rp, dp)))
        except Exception as e:
            real.append((rp, "error %s" % e))
    # Ownership, set-id, sticky
    for name, mode, uid, gid in [("noname.txt", 0o640, 4400, 4401), ("setuid", 0o4755, 0, 0),
                                 ("setgid", 0o2750, 4400, 4401), ("both", 0o6711, 65534, 65534),
                                 ("bigid", 0o600, 70000, 1234567)]:
        p = s(name)
        with open(p, "w") as f:
            f.write(name + "\n")
        try:
            os.chown(p, uid, gid)
        except OSError:
            os.chown(p, 4400, 4401)
        os.chmod(p, mode)
    os.mkdir(s("sticky"))
    os.chmod(s("sticky"), 0o1777)
    os.mkdir(s("sgiddir"))
    os.chown(s("sgiddir"), 4400, 4401)
    os.chmod(s("sgiddir"), 0o2775)
    # Hard links
    with open(s("hl_a"), "w") as f:
        f.write("hard link\n")
    setattr_(s("hl_a"), "HL:attr", "CSTR", b"linked\0")
    try:
        os.link(s("hl_a"), s("hl_b"))
        os.mkdir(s("hldir"))
        os.link(s("hl_a"), s("hldir", "hl_c"))
    except OSError as e:
        print("hardlink: %s" % e, file=sys.stderr)
    # Sparse: data, a 40 MiB hole, data
    fd = os.open(s("sparse.bin"), os.O_CREAT | os.O_WRONLY, 0o644)
    os.write(fd, b"head" * 1024)
    os.lseek(fd, 40 << 20, 0)
    os.write(fd, b"tail")
    os.close(fd)
    with open(s("big.bin"), "wb") as f:
        f.write(os.urandom(3 << 20))
    open(s("empty.txt"), "w").close()
    # Symlinks: relative, absolute, dangling, own owner and times, own attrs (attrs.py's "link")
    os.symlink("plain.txt", s("rel-link"))
    os.symlink("/boot/home", s("abs-link"))
    os.symlink("does/not/exist", s("dangling"))
    setattr_(s("dangling"), "user.sl", "XATR", b"xattr on a symlink")
    try:
        os.lchown(s("rel-link"), 4402, 4403)
    except OSError as e:
        print("lchown: %s" % e, file=sys.stderr)
    # Names
    try:
        with open(os.path.join(src.encode(), b"bad\xff\xfename"), "w") as f:
            f.write("non-utf8\n")
    except OSError as e:
        print("non-utf8 name: %s" % e, file=sys.stderr)
    with open(s("sp ace;$(x)\t'q'"), "w") as f:
        f.write("odd\n")
    os.mkdir(s("emptydir"))
    d = s("deep")
    for i in range(40):
        d = os.path.join(d, "d%02d" % i)
    os.makedirs(d)
    with open(os.path.join(d, "leaf"), "w") as f:
        f.write("leaf\n")
    # Times: every node, bottom-up, distinct ns values
    n = 0
    for dp, dns, fns in os.walk(top, topdown=False):
        for name in fns + dns + ([""] if dp == top else []):
            p = os.path.join(dp, name) if name else dp
            n += 1
            at = (T1 + n * 7) * 10**9 + 123456789 + n
            mt = (T1 + n * 11) * 10**9 + 987654321 - n
            try:
                os.utime(p, ns=(at, mt), follow_symlinks=False)
            except OSError as e:
                print("utime %s: %s" % (p, e), file=sys.stderr)
    # The non-root tree: owned by 4400, its own attrs, a 0444 file with an attr
    nr = os.path.join(top, "nr")
    os.mkdir(nr)
    with open(os.path.join(nr, "f.txt"), "w") as f:
        f.write("non-root\n")
    setattr_(os.path.join(nr, "f.txt"), "NR:attr", "CSTR", b"nr\0")
    setattr_(os.path.join(nr, "f.txt"), "user.nr", "XATR", b"nr xattr")
    with open(os.path.join(nr, "ro.txt"), "w") as f:
        f.write("ro\n")
    setattr_(os.path.join(nr, "ro.txt"), "NR:ro", "CSTR", b"ro\0")
    os.mkdir(os.path.join(nr, "sg"))
    os.symlink("f.txt", os.path.join(nr, "ln"))
    for dp, dns, fns in os.walk(nr, topdown=False):
        for name in fns + dns + ([""] if dp == nr else []):
            p = os.path.join(dp, name) if name else dp
            os.lchown(p, 4400, 4400)
    os.chmod(os.path.join(nr, "ro.txt"), 0o444)
    os.chmod(os.path.join(nr, "sg"), 0o2770)
    for i, (dp, dns, fns) in enumerate(os.walk(nr, topdown=False)):
        for j, name in enumerate(fns + dns + ([""] if dp == nr else [])):
            p = os.path.join(dp, name) if name else dp
            os.utime(p, ns=((T1 + 1000 + i * 31 + j) * 10**9 + 111, (T1 + 2000 + i * 31 + j) * 10**9 + 222),
                     follow_symlinks=False)
    print(json.dumps({"real": real}))


def kind(m):
    for k, f in (("dir", stat.S_ISDIR), ("file", stat.S_ISREG), ("symlink", stat.S_ISLNK),
                 ("fifo", stat.S_ISFIFO), ("chr", stat.S_ISCHR), ("blk", stat.S_ISBLK), ("sock", stat.S_ISSOCK)):
        if f(m):
            return k
    return "?"


def dump(top, out):
    top = os.fsencode(top)
    rows = []
    inos = {}
    for dp, dns, fns in os.walk(top):
        for name in dns + fns + ([b""] if dp == top else []):
            p = os.path.join(dp, name) if name else dp
            st = os.lstat(p)
            rel = os.path.relpath(p, top).decode("utf-8", "surrogateescape")
            k = kind(st.st_mode)
            e = {"path": rel, "kind": k, "mode": oct(st.st_mode & 0o7777), "uid": st.st_uid,
                 "gid": st.st_gid, "mtime": st.st_mtime_ns, "atime": st.st_atime_ns}
            if k == "file":
                with open(p, "rb") as f:
                    e["data"] = hashlib.sha256(f.read()).hexdigest()[:16]
                e["size"] = st.st_size
                e["sparse"] = bool(st.st_size and st.st_blocks * 512 < st.st_size)
                if st.st_nlink > 1:
                    e["hl"] = inos.setdefault(st.st_ino, rel)
                    e["nlink"] = st.st_nlink
            elif k == "symlink":
                e["target"] = os.readlink(p).decode("utf-8", "surrogateescape")
            if k in ("chr", "blk"):
                e["rdev"] = st.st_rdev
            try:
                a = attrs.attributes(p.decode("utf-8", "surrogateescape"))
            except Exception as ex:
                a = "error %s" % ex
            if isinstance(a, dict):
                e["typed"] = {n: ["%08x" % t, len(d), hashlib.sha256(d).hexdigest()[:16]]
                              for n, (t, d) in sorted(a.items()) if t != XATR}
                e["xattr"] = {n: [len(d), hashlib.sha256(d).hexdigest()[:16]]
                              for n, (t, d) in sorted(a.items()) if t == XATR}
            else:
                e["typed"] = e["xattr"] = a
            rows.append(e)
    # hard-link groups by first path in sorted order
    rows.sort(key=lambda e: e["path"])
    first = {}
    for e in rows:
        if "hl" in e:
            e["hl"] = first.setdefault(e["hl"], e["path"])
    with open(out, "w") as f:
        for e in rows:
            f.write(json.dumps(e, sort_keys=True) + "\n")


NEED = {"kind": "contents", "data": "contents", "size": "contents", "target": "symlinks",
        "mode": "modes", "uid": "owners", "gid": "owners", "mtime": "mtimes", "atime": "atimes",
        "typed": "typed-attrs", "xattr": "xattrs", "hl": "hardlinks", "nlink": "hardlinks",
        "sparse": "sparse", "rdev": "devices"}


def load(p):
    with open(p) as f:
        return {e["path"]: e for e in map(json.loads, f)}


def cmp(a, b, label, skip=""):
    try:
        A, B = load(a), load(b)
    except (OSError, ValueError) as ex:
        print("harness\t%s: dump missing or broken: %s" % (label, ex))
        return
    if len(A) < 3 or len(B) < 3:
        print("harness\t%s: dumps too short (%d, %d entries)" % (label, len(A), len(B)))
    skip = set(skip.split(",")) if skip else set()
    out = []
    for p in sorted(set(A) | set(B)):
        if p not in B and A[p]["kind"] == "sock":
            continue
        if p not in B:
            out.append(("contents", "%s: %s missing from %s" % (label, p, b)))
            continue
        if p not in A:
            out.append(("contents", "%s: %s extra in %s" % (label, p, b)))
            continue
        x, y = A[p], B[p]
        for k in sorted(set(x) | set(y)):
            if k == "path" or k in skip:
                continue
            if k in ("mtime", "atime") and x["kind"] == "fifo":
                if x.get(k) != y.get(k):
                    out.append(("fifo-times", "%s: %s %s %s -> %s" % (label, p, k, x.get(k), y.get(k))))
                continue
            if x.get(k) != y.get(k):
                need = NEED.get(k, k)
                if x["kind"] == "symlink" and k in ("mtime", "atime", "uid", "gid", "typed", "xattr"):
                    need = "symlink-" + need
                out.append((need, "%s: %s %s %s -> %s" % (label, p, k, x.get(k), y.get(k))))
    for n, d in out:
        print("%s\t%s" % (n, d))


def mutate(top):
    """Change a restored tree: attrs (attrs.py mutate), modes, owners, times, contents, extra/missing files."""
    attrs.mutate(top)
    j = lambda *p: os.path.join(top, *p)
    os.chmod(j("noname.txt"), 0o666)
    os.chown(j("noname.txt"), 1, 1)
    os.utime(j("big.bin"), ns=(1_700_000_000 * 10**9, 1_700_000_000 * 10**9))
    with open(j("folder", "inner.dat"), "r+b") as f:
        f.write(b"changed!")
    with open(j("extra-file"), "w") as f:
        f.write("not in the snapshot\n")
    os.remove(j("empty.txt"))
    # a hard-linked file changed through one of its names
    if os.path.exists(j("hl_b")):
        with open(j("hl_b"), "a") as f:
            f.write("more\n")
    attrs.write(j("hl_a"), "HL:attr", code("CSTR"), b"other\0")
    if os.path.isdir(j("real0_Desktop")):
        for n in list((attrs.attributes(j("real0_Desktop")) or {}))[:1]:
            attrs.remove(j("real0_Desktop"), n)


def tree(treejson, srcdump):
    """Every node's attributes in the snapshot follow BACKOTTER.md's naming."""
    src = load(srcdump)
    bad = []
    seen = 0
    with open(treejson) as f:
        nodes = json.load(f)
    for path, node in nodes.items():
        if path.startswith("!error"):
            bad.append("%s: %s" % (path, node["error"]))
            continue
        e = src.get(path)
        if e is None:
            continue
        seen += 1
        ga = node.get("generic_attributes") or {}
        xa = {x["name"]: base64.b64decode(x.get("value") or "") for x in (node.get("extended_attributes") or [])}
        if "unix.birthtime" in ga:
            bad.append("%s: unix.birthtime stored" % path)
        if e["kind"] in ("file", "dir", "symlink"):
            if "haiku.attributes" not in ga:
                bad.append("%s: no haiku.attributes" % path)
                continue
            ha = ga["haiku.attributes"]
            if isinstance(ha, str):
                ha = json.loads(base64.b64decode(ha))
            got = {}
            for n, v in ha.items():
                d = base64.b64decode(v.get("value") or "")
                if v["type"] == XATR:
                    bad.append("%s: XATR %s inside haiku.attributes" % (path, n))
                got[n] = ["%08x" % v["type"], len(d), hashlib.sha256(d).hexdigest()[:16]]
            if got != e["typed"]:
                bad.append("%s: haiku.attributes %s != source %s" % (path, got, e["typed"]))
            gx = {n: [len(d), hashlib.sha256(d).hexdigest()[:16]] for n, d in xa.items()}
            if gx != e["xattr"]:
                bad.append("%s: extended_attributes %s != source %s" % (path, gx, e["xattr"]))
            names = [x["name"] for x in (node.get("extended_attributes") or [])]
            if names != sorted(names, key=lambda s: s.encode()):
                bad.append("%s: xattrs not in byte order %s" % (path, names))
        else:
            if "haiku.attributes" in ga or xa:
                bad.append("%s: %s has attributes in the snapshot" % (path, e["kind"]))
    for b in bad:
        print("naming\t%s" % b)
    print("checked\t%d nodes" % seen)


def treewalk(rustic, snap, root, out):
    """{relpath: node} of the snapshot under root (rustic cat tree, one call per directory)."""
    nodes = {}
    def walk(rel):
        spec = "%s:%s" % (snap, root + ("/" + rel if rel else ""))
        r = subprocess.run([rustic, "cat", "tree", spec], capture_output=True)
        if r.returncode:
            nodes["!error " + rel] = {"error": r.stderr.decode(errors="replace")[-400:]}
            return
        for n in json.loads(r.stdout)["nodes"]:
            p = (rel + "/" if rel else "") + n["name"]
            nodes[p] = n
            if n["type"] == "dir":
                walk(p)
    walk("")
    with open(out, "w") as f:
        json.dump(nodes, f)
    print("treewalk\t%d nodes" % len(nodes))


def anc(a, b):
    A, B = load(a), load(b)
    for p in ("anc1", "anc1/anc2"):
        x, y = A.get(p), B.get(p)
        if not y:
            print("ancestors\t%s missing" % p)
            continue
        for k in ("mode", "uid", "gid", "mtime", "typed", "xattr"):
            if x.get(k) != y.get(k):
                print("ancestors\t%s %s %s -> %s" % (p, k, x.get(k), y.get(k)))


if __name__ == "__main__":
    c, args = sys.argv[1], sys.argv[2:]
    {"make": make, "dump": dump, "cmp": cmp, "mutate": mutate, "tree": tree, "anc": anc, "treewalk": treewalk}[c](*args)
