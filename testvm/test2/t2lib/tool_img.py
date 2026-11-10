"""t2-host tool img: what ImgTest2 does on a test host (contract 2.2).

    t2-host tool img pin PLATFORM...     pin each platform's --test image

The pin is HOST_ROOT/img-pin/PLATFORM/: rescuer.img, a hard link or a reflink of the
--test image that a later build's move can't touch and that a running test keeps by
its inode; rescuer.img.sha256; source.json ({path, size, mtime_ns, inode, sha256} of the
image it was pinned from, plus release_same on Linux); restic-bko, rustic-bko and
bko-holt, the binaries images/PLATFORM/bko-bin.sha256 records; bins-stale, only when
they no longer can be had.  Nothing under images/, bko-bin/ or holt-bin/ is written.

Prints one JSON object {PLATFORM: result}:
    ok        true, or false with why (a refusal: a build under way, no image, an error)
    sha256, how ("current", "link" or "reflink"), source, bins, stale [names]
    release_same (Linux): {same: true|false|null, why}
Exit 0 once every platform has a result; 2 on bad usage.
"""

import errno
import fcntl
import glob
import hashlib
import json
import os
import subprocess
import sys
import tempfile

PLATFORMS = ("linux", "freebsd", "netbsd", "openbsd", "dragonfly", "illumos", "haiku")
STALE_FILE = "bins-stale"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_atomic(path, text):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


# --- where a file is on another mount of its filesystem -------------------------------------------

def parse_mountinfo(text):
    """[{dev, root, mount}] of /proc/self/mountinfo's lines, in order (a later mount at a
    path hides an earlier one)."""
    out = []
    for line in text.splitlines():
        f = line.split(" - ", 1)[0].split()
        if len(f) < 5:
            continue
        un = lambda s: s.replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")  # noqa: E731
        out.append({"dev": f[2], "root": un(f[3]), "mount": un(f[4])})
    return out


def mount_of(path, mounts):
    """The mount that holds PATH (a resolved path): the longest mount point over it."""
    best = None
    for m in mounts:
        mp = m["mount"].rstrip("/") or "/"
        if path == mp or path.startswith(mp.rstrip("/") + "/"):
            if best is None or len(mp) >= len(best["mount"].rstrip("/") or "/"):
                best = m
    return best


def path_on_mount(src, dst_dir, mounts):
    """SRC's path through the mount that holds DST_DIR, when both are mounts of one
    filesystem and SRC is inside what that mount shows; else None.  /home/m/backotter-work
    and /bkoscratch are two mounts of one XFS, so link() between them is EXDEV, while
    /bkoscratch/work/images/... is the same inode."""
    src = os.path.realpath(src)
    a, b = mount_of(src, mounts), mount_of(os.path.realpath(dst_dir), mounts)
    if a is None or b is None or a["dev"] != b["dev"]:
        return None
    if a["mount"] == b["mount"]:
        return src
    inside = os.path.join(a["root"], os.path.relpath(src, a["mount"]))
    root = b["root"].rstrip("/") or "/"
    if root != "/" and not (inside == root or inside.startswith(root + "/")):
        return None
    return os.path.normpath(os.path.join(b["mount"], os.path.relpath(inside, root)))


def read_mountinfo():
    with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as f:
        return parse_mountinfo(f.read())


def copy_reflink(src, dst):
    """A copy that shares its blocks where the filesystem can."""
    subprocess.run(["cp", "--reflink=auto", "--", src, dst], check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def place(src, tmp, pin_dir, mounts=None, link=None, copy=None):
    """A new name TMP for SRC's bytes: a hard link through the path on PIN_DIR's own mount
    when it is the same file, else a reflink.  'link' or 'reflink'."""
    link = link or os.link
    copy = copy or copy_reflink
    try:
        if mounts is None:
            mounts = read_mountinfo()
        cand = path_on_mount(src, pin_dir, mounts)
        if cand:
            a, b = os.stat(src), os.stat(cand)
            if (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino):
                link(cand, tmp)
                return "link"
    except OSError as e:
        if e.errno not in (errno.EXDEV, errno.ENOENT, errno.EPERM, errno.EACCES, errno.EMLINK):
            raise
    copy(src, tmp)
    return "reflink"


# --- the pin -----------------------------------------------------------------------------------------

def images_dir(work, env):
    return os.path.abspath(os.path.expanduser(env.get("T2_IMAGES") or os.path.join(work, "images")))


def recorded_bins(idir):
    """{NAME: sha256} of images/PLAT/bko-bin.sha256, or None when it is missing."""
    try:
        with open(os.path.join(idir, "bko-bin.sha256"), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    out = {}
    for line in text.splitlines():
        p = line.split()
        if len(p) == 2:
            out[p[1].lstrip("*").replace("./", "", 1)] = p[0]
    return out


class Lock:
    """images/.lock-PLAT, shared and non-blocking: held by a build, or nothing."""

    def __init__(self, path):
        self.path, self.fd = path, None

    def __enter__(self):
        try:
            self.fd = os.open(self.path, os.O_RDONLY)
        except OSError:
            return True   # no such lock: no build has taken it yet
        try:
            fcntl.flock(self.fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def __exit__(self, *a):
        if self.fd is not None:
            os.close(self.fd)


def release_same(idir, test_img, run=None):
    """Linux's release and --test images must carry the same squashfs files: their
    md5sum.txt lines for them, read off the images with xorriso (which writes nothing to
    them).  {same: bool or null, why}."""
    run = run or subprocess.run
    rel = sorted(glob.glob(os.path.join(idir, "*.img")))
    if not rel:
        return {"same": None, "why": "no release image"}
    got = {}
    with tempfile.TemporaryDirectory() as td:
        for tag, img in (("release", rel[-1]), ("test", test_img)):
            out = os.path.join(td, tag)
            try:
                run(["xorriso", "-osirrox", "on", "-indev", img, "-extract", "/md5sum.txt", out],
                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                with open(out, encoding="utf-8") as f:
                    got[tag] = {p[1]: p[0] for p in (x.split() for x in f) if len(p) == 2 and p[1].endswith(".squashfs")}
            except OSError:
                return {"same": None, "why": f"no md5sum.txt from the {tag} image"}
    if not got["test"]:
        return {"same": None, "why": "no squashfs line in the test image's md5sum.txt"}
    if got["release"] != got["test"]:
        return {"same": False, "why": "the squashfs lines differ: "
                + ", ".join(sorted(k for k in set(got["release"]) | set(got["test"])
                                   if got["release"].get(k) != got["test"].get(k)))}
    return {"same": True, "why": f"{len(got['test'])} squashfs file(s) equal"}


def pin_bins(pdir, plat, want, work):
    """The recorded binaries, in the pin, each checked by its sha256.  [names missing]."""
    stale = []
    for name, sha in sorted(want.items()):
        pinned = os.path.join(pdir, name)
        if os.path.isfile(pinned) and sha256_file(pinned) == sha:
            continue
        src = (os.path.join(work, "holt-bin", plat, "bko-holt") if name == "bko-holt"
               else os.path.join(work, "bko-bin", plat, name))
        try:
            if sha256_file(src) != sha:
                stale.append(name)
                continue
        except OSError:
            stale.append(name)
            continue
        tmp = f"{pinned}.tmp.{os.getpid()}"
        with open(src, "rb") as a, open(tmp, "wb") as b:
            for chunk in iter(lambda: a.read(1 << 20), b""):
                b.write(chunk)
        os.chmod(tmp, 0o755)
        os.replace(tmp, pinned)
    return stale


def read_sha(path):
    """The 64 hex at the start of PATH, or None: empty after a crash, unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            words = f.read().split()
    except (OSError, ValueError):
        return None
    return words[0] if words and len(words[0]) == 64 else None


def source_of(img):
    s = os.stat(img)
    return {"path": img, "size": s.st_size, "mtime_ns": s.st_mtime_ns, "inode": s.st_ino}


def same_source(a, b):
    """source.json's A and the test image's B are one file (paths compare resolved, as
    facts_host.one_image does)."""
    if not a:
        return False
    return (os.path.realpath(str(a.get("path"))) == os.path.realpath(str(b.get("path")))
            and (a.get("size"), a.get("mtime_ns"), a.get("inode")) == (b["size"], b["mtime_ns"], b["inode"]))


def pin_one(plat, images, work, host_root, mounts=None, link=None, copy=None, xorriso=None):
    """One platform's pin (the module's doc): its result."""
    idir = os.path.join(images, plat)
    if not os.path.isfile(os.path.join(idir, "done.exit")):
        return {"ok": False, "why": "a build is under way (no done.exit)"}
    with Lock(os.path.join(images, f".lock-{plat}")) as free:
        if not free:
            return {"ok": False, "why": f"a build is under way (images/.lock-{plat} is held)"}
        tests = sorted(glob.glob(os.path.join(idir, "test", "*.img")))
        if not tests:
            return {"ok": False, "why": "no test image"}
        test = tests[-1]
        pdir = os.path.join(host_root, "img-pin", plat)
        os.makedirs(pdir, exist_ok=True)
        lk = os.open(os.path.join(os.path.dirname(pdir), f".lock-{plat}"), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(lk, fcntl.LOCK_EX)
            cur = source_of(test)
            sj = os.path.join(pdir, "source.json")
            rimg = os.path.join(pdir, "rescuer.img")
            shaf = rimg + ".sha256"
            old = read_json(sj)
            how, res = "current", {"ok": True, "source": None}
            sha = read_sha(shaf)
            if not (sha and same_source(old, cur) and os.path.isfile(rimg)):
                tmp = f"{rimg}.tmp.{os.getpid()}"
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                how = place(test, tmp, pdir, mounts=mounts, link=link, copy=copy)
                # (Until the new source.json lands no reader may take the old one as this pin's.)
                try:
                    os.unlink(sj)
                except OSError:
                    pass
                os.replace(tmp, rimg)
                try:   # (rename over another name of one inode does nothing, and TMP stays)
                    os.unlink(tmp)
                except OSError:
                    pass
                sha = sha256_file(rimg)
                write_atomic(shaf, f"{sha}  rescuer.img\n")
                old = None
            source = dict(cur, sha256=sha)
            if plat == "linux":
                # (Only an answer is kept: a comparison that failed is made again.)
                rs = (old or {}).get("release_same") or {}
                if rs.get("same") not in (True, False):
                    rs = release_same(idir, test, xorriso)
                source["release_same"] = rs
                res["release_same"] = rs
            write_atomic(sj, json.dumps(source, indent=1, sort_keys=True) + "\n")
            want = recorded_bins(idir)
            stale_f = os.path.join(pdir, STALE_FILE)
            if want is None:
                stale = ["bko-bin.sha256"]
            else:
                stale = pin_bins(pdir, plat, want, work)
            if stale:
                write_atomic(stale_f, "".join(n + "\n" for n in stale))
            else:
                try:
                    os.unlink(stale_f)
                except OSError:
                    pass
            res.update(sha256=sha, how=how, source=source, bins=want or {}, stale=stale)
            return res
        finally:
            os.close(lk)


def main(args, ctx):
    if len(args) < 2 or args[0] != "pin":
        sys.stderr.write("usage: t2-host tool img pin PLATFORM...\n")
        return 2
    bad = [p for p in args[1:] if p not in PLATFORMS]
    if bad:
        sys.stderr.write(f"t2-host tool img: not a platform: {' '.join(bad)}\n")
        return 2
    images = images_dir(ctx.work, ctx.env)
    out = {}
    for p in args[1:]:
        try:
            out[p] = pin_one(p, images, ctx.work, ctx.host_root)
        except Exception as e:   # noqa: BLE001 - one platform's error isn't the others'
            out[p] = {"ok": False, "why": f"{type(e).__name__}: {e}"}
    sys.stdout.write(json.dumps(out, sort_keys=True) + "\n")
    return 0
