#!/usr/bin/env python3
"""A rescue image's inputs, their digests, its tree listing and its provenance
(docs/design/img-speed.md §3, C7).

Usage: img_inputs.py digest PLATFORM        the three digests and the input list, as JSON
       img_inputs.py moved START.json       exit 3 (and say what) when the inputs moved since START
       img_inputs.py identity PLATFORM      the builder guest's identity, as one JSON line
       img_inputs.py lint                   every stick/ file is an input or listed as none
       img_inputs.py tree-list [--no-owner] a tar on stdin -> the tree listing on stdout
       img_inputs.py provenance ...         images/P/provenance.json (img-one.sh's)

stick/img-digest.sh runs `digest`.  `digest` and `identity` read only: they
never touch images/, and write nothing but their hash cache (BKO_DIGEST_CACHE,
default BKO_WORK/.img-digest-cache.json, by temp name and rename; best effort).

From outside, as img-one.sh has them: BKO_WORK (default /home/m/backotter-work),
S (the stick/ to read, default BKO_WORK/stick; its parent holds testvm/),
BKO_BIN, BKO_HOLT_BIN, SL and ISO (Linux's slim and source ISO).  For tests:
BKO_IMG_HOST_TOOLS (a JSON object that replaces dpkg's versions).

The input list is stick/img-inputs.tsv: PLATFORMS GLOB KIND [REASON], where
PLATFORMS is `all`, `vm` (the six VM platforms), `-` (none), or names joined by
commas; KIND is base, tests or none.  For each platform the last row whose glob
matches a file decides its kind, so a later `none` or `tests` row takes a file
out of an earlier glob.  Globs are relative to the repo root (S's parent) and
`**` crosses directories.

The tree listing (C2): one line per entry, tab-separated: TREE, PATH, TYPE (f d
l c b p), MODE (octal, 4 digits), OWNER (uid:gid, or - with --no-owner), SIZE,
SHA256 (files; a link's target; a device's major,minor), EXTRA (file flags and
xattrs, as tar recorded them, or -).  A tar member ./TREE/PATH is TREE's PATH;
a hard link is listed as the file it links to.  Sorted, C order.
"""

from __future__ import annotations

import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tarfile

PLATFORMS = ("linux", "freebsd", "netbsd", "openbsd", "dragonfly", "illumos", "haiku")
VM = PLATFORMS[1:]
KINDS = ("base", "tests", "none")
SCHEMA = 1
# The packages whose tools write image bytes on the host (§3.2, D15): config.img
# everywhere, the ISO and the layer's squashfs on Linux.
HOST_TOOLS_ALL = ("dosfstools", "mtools")
HOST_TOOLS_LINUX = ("xorriso", "squashfs-tools")
# Recorded in provenance's tests, never in a digest: they differ host by host.
TEST_TOOLS = ("qemu-system-x86", "ovmf")
SLIM_FILES = ("ubuntu-server-minimal.squashfs", "ubuntu-server-minimal.manifest",
              "ubuntu-server-minimal.size", "vmlinuz", "initrd")


class Refused(Exception):
    pass


def env(name, default=None):
    v = os.environ.get(name)
    return v if v else default


def work():
    return env("BKO_WORK", "/home/m/backotter-work")


def stick_dir():
    return env("S", os.path.join(work(), "stick"))


def repo_root():
    return os.path.dirname(os.path.realpath(stick_dir()))


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_of(obj):
    return hashlib.sha256(canon(obj).encode()).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# --- The hash cache: big files (media, the ISO, slim, a qcow2) by (size, mtime, inode) ---

class Cache:
    def __init__(self):
        self.path = env("BKO_DIGEST_CACHE", os.path.join(work(), ".img-digest-cache.json"))
        self.dirty = False
        try:
            with open(self.path, encoding="utf-8") as f:
                self.d = json.load(f)
            if not isinstance(self.d, dict):
                self.d = {}
        except (OSError, ValueError):
            self.d = {}

    def sha256(self, path):
        real = os.path.realpath(path)
        st = os.stat(real)
        key = [st.st_size, st.st_mtime_ns, st.st_ino]
        e = self.d.get(real)
        if isinstance(e, dict) and e.get("stat") == key and isinstance(e.get("sha256"), str):
            return e["sha256"]
        s = sha256_file(real)
        if os.stat(real).st_mtime_ns == st.st_mtime_ns:
            self.d[real] = {"stat": key, "sha256": s}
            self.dirty = True
        return s

    def save(self):
        if not self.dirty:
            return
        try:
            tmp = f"{self.path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.d, f, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError:
            pass


# --- stick/img-inputs.tsv -------------------------------------------------------------

def tsv_path(root=None):
    return os.path.join(root or repo_root(), "stick", "img-inputs.tsv")


def load_rows(root=None):
    rows = []
    with open(tsv_path(root), encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            cols = line.split("\t")
            if len(cols) < 3:
                raise Refused(f"img-inputs.tsv:{n}: want PLATFORMS, GLOB, KIND [REASON], tab-separated")
            plats, glob, kind = cols[0].strip(), cols[1].strip(), cols[2].strip()
            reason = cols[3].strip() if len(cols) > 3 else ""
            if kind not in KINDS:
                raise Refused(f"img-inputs.tsv:{n}: kind {kind!r} is not one of {', '.join(KINDS)}")
            if kind == "none" and not reason:
                raise Refused(f"img-inputs.tsv:{n}: a none row needs its reason")
            names = expand_platforms(plats, n)
            rows.append({"line": n, "platforms": names, "glob": glob, "kind": kind, "reason": reason})
    return rows


def expand_platforms(spec, n=0):
    if spec == "-":
        return ()
    out = []
    for p in spec.split(","):
        p = p.strip()
        if p == "all":
            out += PLATFORMS
        elif p == "vm":
            out += VM
        elif p in PLATFORMS:
            out.append(p)
        else:
            raise Refused(f"img-inputs.tsv:{n}: no platform {p!r}")
    return tuple(dict.fromkeys(out))


def glob_re(glob):
    """GLOB as a regex over repo-relative paths: * and ? stay within a directory, ** crosses."""
    i, out = 0, []
    while i < len(glob):
        c = glob[i]
        if glob.startswith("**/", i):
            out.append("(?:.*/)?"); i += 3; continue
        if glob.startswith("**", i):
            out.append(".*"); i += 2; continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out) + r"\Z")


def all_files(root, top):
    """Every file under ROOT/TOP, repo-relative, sorted (no __pycache__, .pyc, sync stamps)."""
    out = []
    base = os.path.join(root, top)
    if os.path.isfile(base):
        return [top]
    for d, dirs, files in os.walk(base):
        dirs[:] = sorted(x for x in dirs if x != "__pycache__")
        for f in files:
            if f.endswith(".pyc") or f in (".backotter-commit", ".DS_Store"):
                continue
            out.append(os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/"))
    return sorted(out)


def classify(rows, files):
    """{platform: {file: kind}} by the last matching row, and {file: [rows]} of every match."""
    kinds = {p: {} for p in PLATFORMS}
    matched = {f: [] for f in files}
    for r in rows:
        rx = glob_re(r["glob"])
        for f in files:
            if rx.match(f):
                matched[f].append(r)
                for p in r["platforms"]:
                    kinds[p][f] = r["kind"]
    return kinds, matched


def candidate_files(root, rows):
    """Every file under stick/, and those outside it (testvm/vm-run...) that a row names."""
    files = set(all_files(root, "stick"))
    for r in rows:
        g = r["glob"]
        if g.startswith("stick/"):
            continue
        if not any(ch in g for ch in "*?["):
            if os.path.isfile(os.path.join(root, g)):
                files.add(g)
        else:
            rx = glob_re(g)
            files.update(x for x in all_files(root, g.split("/")[0]) if rx.match(x))
    return sorted(files)


def inputs_of(platform, kind, root=None):
    root = root or repo_root()
    rows = load_rows(root)
    files = candidate_files(root, rows)
    kinds, _ = classify(rows, files)
    return [f for f in files if kinds[platform].get(f) == kind]


def lint(root=None):
    """The problems, as lines: a stick/ file no row names, a glob that names nothing, a
    BUILD_FILES entry that isn't its platform's base input."""
    root = root or repo_root()
    rows = load_rows(root)
    files = candidate_files(root, rows)
    kinds, matched = classify(rows, files)
    bad = []
    for f in files:
        if f.startswith("stick/") and not matched[f]:
            bad.append(f"{f}: not an input of any platform, and not listed as none (stick/img-inputs.tsv)")
    for r in rows:
        rx = glob_re(r["glob"])
        if not any(rx.match(f) for f in files):
            bad.append(f"img-inputs.tsv:{r['line']}: {r['glob']} names no file")
    for p in VM:
        conf = os.path.join(root, "stick", p, "platform.conf")
        try:
            text = open(conf, encoding="utf-8").read()
        except OSError:
            continue
        m = re.search(r"^BUILD_FILES=\(([^)]*)\)", text, re.M)
        for name in (m.group(1).split() if m else []):
            f = f"stick/{p}/{name}"
            if kinds[p].get(f) != "base":
                bad.append(f"{f}: in {p}'s BUILD_FILES, so it goes into the image, but it isn't {p}'s base input")
    return bad


# --- The builder guest's identity (§3.2) ------------------------------------------------

def platform_conf(platform):
    """BUILD_VM, BUILD_VM_IMAGE, BUILD_VM_MODE from stick/P/platform.conf (sourced by bash)."""
    conf = os.path.join(stick_dir(), platform, "platform.conf")
    if not os.path.isfile(conf):
        return None
    r = subprocess.run(["bash", "-c", '. "$1" >/dev/null 2>&1; printf "%s\\n" "$BUILD_VM" "$BUILD_VM_IMAGE" "$BUILD_VM_MODE"',
                        "bash", conf], capture_output=True, text=True)
    vals = r.stdout.split("\n")
    if r.returncode != 0 or len(vals) < 3 or not vals[0]:
        return None
    return {"vm": vals[0], "image": vals[1], "mode": vals[2]}


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def identity(platform, cache=None):
    """The builder's identity: a test guest's generation (while its qcow2 is still the one
    image-stat.json recorded at that generation, else the qcow2's SHA-256), or the FreeBSD
    builder's fingerprint file's SHA-256.  None for Linux."""
    if platform == "linux":
        return None
    conf = platform_conf(platform)
    if conf is None:
        return {"vm": None, "kind": "unknown", "why": f"no stick/{platform}/platform.conf"}
    gdir = os.path.join(work(), "vms", conf["vm"])
    out = {"vm": conf["vm"]}
    if conf["mode"] == "running":
        fp = os.path.join(gdir, "fingerprint")
        out["kind"] = "fingerprint"
        try:
            out["sha256"] = sha256_file(fp)
        except OSError:
            out["sha256"] = None
        return out
    disk = os.path.join(gdir, conf["image"])
    state = read_json(os.path.join(gdir, "image-state"))
    stat = read_json(os.path.join(gdir, "image-stat.json"))
    gen = (state or {}).get("generation")
    try:
        st = os.stat(disk)
    except OSError:
        out.update(kind="missing", why=f"no vms/{conf['vm']}/{conf['image']}")
        return out
    rec = ((stat or {}).get("disks") or {}).get(conf["image"]) or {}
    if gen and (stat or {}).get("generation") == gen and rec.get("size") == st.st_size \
            and rec.get("mtime_ns") == st.st_mtime_ns and rec.get("inode") == st.st_ino:
        out.update(kind="generation", generation=gen)
        return out
    cache = cache or Cache()
    out.update(kind="qcow2", sha256=cache.sha256(disk))
    return out


# --- The three digests (§3.2) ---------------------------------------------------------

def dpkg_versions(names):
    over = env("BKO_IMG_HOST_TOOLS")
    if over:
        d = json.loads(over)
        return {n: d.get(n) for n in names}
    out = {}
    for n in names:
        try:
            r = subprocess.run(["dpkg-query", "-W", "-f", "${Version}", n], capture_output=True, text=True)
            out[n] = r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
        except OSError:
            out[n] = None
    return out


def media_of(platform, cache):
    """img-media/P/MEDIA's list (what the last fetch set), hashed by content."""
    mdir = os.path.join(work(), "img-media", platform)
    try:
        names = [x.strip() for x in open(os.path.join(mdir, "MEDIA"), encoding="utf-8") if x.strip()]
    except OSError:
        return None
    out = {}
    for n in names:
        try:
            out[n] = cache.sha256(os.path.join(mdir, n))
        except OSError:
            out[n] = None
    return out


def linux_iso():
    sl = env("SL", os.path.join(work(), "server", "slim"))
    iso = env("ISO")
    if not iso:
        try:
            for line in open(os.path.join(sl, "report.txt"), encoding="utf-8"):
                if line.startswith("Source ISO:"):
                    iso = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
    return iso or os.path.join(work(), "server", "ubuntu-26.04.1-live-server-amd64.iso"), sl


def modes_of(root):
    """img-one.sh's MODES_linux / MODES_other lines: the boot modes it tests."""
    out = {}
    try:
        for line in open(os.path.join(root, "stick", "img-one.sh"), encoding="utf-8"):
            m = re.match(r'^MODES_(linux|other)="([^"]*)"', line)
            if m:
                out[m.group(1)] = m.group(2).split()
    except OSError:
        pass
    return out


def bins_of(platform):
    b = env("BKO_BIN", os.path.join(work(), "bko-bin"))
    h = env("BKO_HOLT_BIN", os.path.join(work(), "holt-bin"))
    out = {}
    for name, path in (("restic-bko", os.path.join(b, platform, "restic-bko")),
                       ("rustic-bko", os.path.join(b, platform, "rustic-bko")),
                       ("bko-holt", os.path.join(h, platform, "bko-holt"))):
        if os.path.isfile(path):
            out[name] = sha256_file(path)
    return out


def digest(platform, cache=None):
    if platform not in PLATFORMS:
        raise Refused(f"no platform {platform!r}")
    root = repo_root()
    cache = cache or Cache()
    stick = {f: sha256_file(os.path.join(root, f)) for f in inputs_of(platform, "base", root)}
    tools = HOST_TOOLS_ALL + (HOST_TOOLS_LINUX if platform == "linux" else ())
    base = {"stick": stick, "host_tools": dpkg_versions(tools)}
    if platform == "linux":
        iso, sl = linux_iso()
        try:
            base["iso"] = cache.sha256(iso)
        except OSError:
            base["iso"] = None
        files = {}
        for n in SLIM_FILES:
            try:
                files[n] = cache.sha256(os.path.join(sl, n))
            except OSError:
                files[n] = None
        base["slim"] = {"path": os.path.basename(os.path.realpath(sl)), "manifest": digest_of(files)}
    else:
        base["media"] = media_of(platform, cache)
        base["builder"] = identity(platform, cache)
    bins = bins_of(platform)
    tests_files = {f: sha256_file(os.path.join(root, f)) for f in inputs_of(platform, "tests", root)}
    modes = modes_of(root).get("linux" if platform == "linux" else "other")
    tests = {"files": tests_files, "modes": modes}
    cache.save()
    return {"platform": platform,
            "base": {"digest": digest_of(base), "inputs": base},
            "bins": dict(bins, digest=digest_of(bins)),
            "tests": dict(tests, digest=digest_of(tests))}


# --- The tree listing (C2) ------------------------------------------------------------

def esc(s):
    return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")


def tree_list(stream, no_owner=False):
    """The listing of a tar read from STREAM, as sorted lines."""
    lines, sums = [], {}
    with tarfile.open(fileobj=stream, mode="r|*") as t:
        for m in t:
            name = m.name
            while name.startswith("./"):
                name = name[2:]
            name = name.strip("/")
            if not name or name == ".":
                continue
            tree, _, path = name.partition("/")
            path = "/" + path
            extra = []
            for k, v in sorted((m.pax_headers or {}).items()):
                if k == "SCHILY.fflags" and v:
                    extra.append(f"flags={v}")
                elif k.startswith(("SCHILY.xattr.", "LIBARCHIVE.xattr.")):
                    n = k.split(".xattr.", 1)[1]
                    extra.append(f"xattr:{n}={hashlib.sha256(v.encode('utf-8', 'surrogateescape')).hexdigest()[:16]}")
            size, h = m.size, "-"
            if m.isreg():
                f = t.extractfile(m)
                hh = hashlib.sha256()
                for b in iter(lambda: f.read(1 << 20), b""):
                    hh.update(b)
                h, typ = hh.hexdigest(), "f"
                sums[(tree, path)] = (h, m.size)
            elif m.islnk():
                ln = m.linkname
                while ln.startswith("./"):
                    ln = ln[2:]
                lt, _, lp = ln.strip("/").partition("/")
                h, size = sums.get((lt, "/" + lp), ("?", 0))
                typ = "f"
                sums[(tree, path)] = (h, size)
            elif m.isdir():
                typ, size = "d", 0
            elif m.issym():
                typ, h, size = "l", esc(m.linkname), 0
            elif m.ischr() or m.isblk():
                typ, h, size = ("c" if m.ischr() else "b"), f"{m.devmajor},{m.devminor}", 0
            elif m.isfifo():
                typ, size = "p", 0
            else:
                typ, size = "?", 0
            owner = "-" if no_owner else f"{m.uid}:{m.gid}"
            lines.append("\t".join([esc(tree), esc(path), typ, f"{m.mode & 0o7777:04o}", owner, str(size), h,
                                    ",".join(extra) or "-"]))
    return sorted(lines)


# --- The swap list (§1.3) and the OS manifest -----------------------------------------

def swap_rows(platform, root=None):
    out = []
    with open(os.path.join(root or repo_root(), "stick", "img-swap.tsv"), encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.split("\t")
            if len(cols) >= 4 and platform in expand_platforms(cols[0].strip()):
                out.append({"tree": cols[1].strip(), "path": cols[2].strip(), "kind": cols[3].strip(),
                            "optional": len(cols) > 4 and cols[4].strip() == "optional"})
    return out


def os_manifest(listing_lines, swaps):
    """sha256 of the listing without the swap list's paths, and the swap check: each required
    swap path present in the listing."""
    keyed = [(ln.split("\t")[0], ln.split("\t")[1], ln) for ln in listing_lines]
    def is_swap(tree, path):
        return any(s["tree"] == tree and fnmatch.fnmatchcase(path, s["path"]) for s in swaps)
    kept = [ln for t, p, ln in keyed if not is_swap(t, p)]
    missing = [f"{s['tree']}:{s['path']}" for s in swaps if not s["optional"]
               and not any(t == s["tree"] and fnmatch.fnmatchcase(p, s["path"]) for t, p, _ in keyed)]
    trees = sorted({t for t, _, _ in keyed})
    return hashlib.sha256("".join(x + "\n" for x in kept).encode()).hexdigest(), missing, trees


# --- Provenance (§3.1) ----------------------------------------------------------------

def stick_commit():
    s = stick_dir()
    try:
        c = subprocess.run(["git", "-C", s, "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True)
        if c.returncode == 0 and c.stdout.strip():
            d = subprocess.run(["git", "-C", s, "status", "--porcelain", "--", "."], capture_output=True, text=True)
            return c.stdout.strip(), bool(d.stdout.strip())
    except OSError:
        pass
    try:
        line = open(os.path.join(s, ".backotter-commit"), encoding="utf-8").readline().strip()
    except OSError:
        return None, None
    return line.split(".")[0] or None, line.endswith(".dirty")


def image_entry(path, build_log):
    e = {"file": None, "sha256": None, "size": None, "image_id": None}
    if path and os.path.isfile(path):
        e.update(file=path, sha256=sha256_file(path), size=os.path.getsize(path))
    try:
        last = [x for x in open(build_log, encoding="utf-8", errors="replace") if x.startswith("Wrote ")][-1]
        m = re.search(r"image ([0-9a-f-]{36})", last)
        e["image_id"] = m.group(1) if m else None
    except (OSError, IndexError):
        pass
    return e


def read_listing(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [x.rstrip("\n") for x in f if x.strip()]
    except OSError:
        return None


def moved(start, now):
    """What of the build's inputs moved between START's digest and NOW's: the stick/ inputs,
    the builder's identity, the bins.  The media are left out: the build's own fetch sets them."""
    out = []
    a, b = start["base"]["inputs"], now["base"]["inputs"]
    if a.get("stick") != b.get("stick"):
        ch = sorted(k for k in set(a.get("stick", {})) | set(b.get("stick", {}))
                    if a.get("stick", {}).get(k) != b.get("stick", {}).get(k))
        out.append("stick/ inputs changed: " + ", ".join(ch))
    if a.get("builder") != b.get("builder"):
        out.append(f"the builder moved: {canon(a.get('builder'))} -> {canon(b.get('builder'))}")
    for k in ("iso", "slim", "host_tools"):
        if a.get(k) != b.get(k):
            out.append(f"{k} changed")
    if start["bins"] != now["bins"]:
        out.append("the binaries changed")
    return out


def provenance(a):
    d = a.dir
    now = digest(a.platform)
    if a.start:
        start = json.load(open(a.start, encoding="utf-8"))
        mv = moved(start, now)
        if mv:
            raise Refused("; ".join(mv))
    commit, dirty = stick_commit()
    rel = os.path.join(d, a.release) if a.release else None
    tst = os.path.join(d, a.test) if a.test else None
    release = image_entry(rel, os.path.join(d, "build.log"))
    test = image_entry(tst, os.path.join(d, "test", "build.log"))
    for e, f in ((release, a.release), (test, a.test)):
        e["file"] = f
    swaps = swap_rows(a.platform)
    trees = {}
    for which, listing in (("release", os.path.join(d, "os-tree.txt")), ("test", os.path.join(d, "test", "os-tree.txt"))):
        lines = read_listing(listing)
        if lines is None:
            trees[which] = {"os_manifest": None, "why": f"no {os.path.relpath(listing, d)}"}
            continue
        man, missing, names = os_manifest(lines, swaps)
        trees[which] = {"os_manifest": man, "entries": len(lines), "trees": names, "swap_missing": missing}
    test["os_manifest"] = trees["test"]["os_manifest"]
    hb = env("BKO_HOLT_BIN", os.path.join(work(), "holt-bin"))
    bb = env("BKO_BIN", os.path.join(work(), "bko-bin"))
    holt_commit = None
    try:
        c = open(os.path.join(hb, a.platform, "COMMIT"), encoding="utf-8").readline().strip()
        holt_commit = c if re.fullmatch(r"[0-9a-f]{40}", c) else None
    except OSError:
        pass
    holt_code = None
    try:
        c = open(os.path.join(hb, a.platform, "CODE"), encoding="utf-8").readline().strip()
        holt_code = c if re.fullmatch(r"[0-9a-f]{64}", c) else None
    except OSError:
        pass
    rnd = None
    try:
        m = re.search(r"round ([^\s,]+)", open(os.path.join(bb, "BUILD-INFO"), encoding="utf-8").readline())
        rnd = m.group(1) if m else None
    except OSError:
        pass
    boot = {}
    for kv in a.boot or []:
        k, _, v = kv.partition("=")
        boot[k] = int(v) if v.lstrip("-").isdigit() else v
    when = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = {
        "schema": SCHEMA, "platform": a.platform, "mode": "full",
        "built": {"host": socket.gethostname().split(".")[0], "when": when, "run": a.run or None,
                  "stick_commit": commit, "stick_dirty": dirty},
        "release": release, "test": test,
        "base": {"digest": now["base"]["digest"], "inputs": now["base"]["inputs"],
                 "os_epoch": when[:10], "os_manifest": trees["release"]["os_manifest"],
                 "trees": trees},
        "bins": dict(now["bins"], bko_bin_round=rnd, holt_commit=holt_commit, holt_code=holt_code, holt_build="vm"),
        "tests": dict(now["tests"], boot=boot, tools=dpkg_versions(TEST_TOOLS)),
        "parent": None,
    }
    out = os.path.join(d, "provenance.json")
    tmp = out + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, out)
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser(prog="img_inputs.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("digest").add_argument("platform")
    sub.add_parser("identity").add_argument("platform")
    sub.add_parser("lint")
    sub.add_parser("moved").add_argument("start")
    t = sub.add_parser("tree-list")
    t.add_argument("--no-owner", action="store_true")
    p = sub.add_parser("provenance")
    p.add_argument("--platform", required=True)
    p.add_argument("--dir", required=True)
    p.add_argument("--release")
    p.add_argument("--test")
    p.add_argument("--run")
    p.add_argument("--start", help="the digest JSON taken at the build's start")
    p.add_argument("--boot", action="append", help="MODE=RC")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "digest":
            print(json.dumps(digest(a.platform), indent=1, sort_keys=True))
        elif a.cmd == "identity":
            print(canon(identity(a.platform)))
        elif a.cmd == "moved":
            with open(a.start, encoding="utf-8") as f:
                start = json.load(f)
            mv = moved(start, digest(start["platform"]))
            if mv:
                raise Refused("; ".join(mv))
        elif a.cmd == "lint":
            bad = lint()
            for b in bad:
                print(b)
            return 1 if bad else 0
        elif a.cmd == "tree-list":
            for line in tree_list(sys.stdin.buffer, a.no_owner):
                sys.stdout.write(line + "\n")
        elif a.cmd == "provenance":
            doc = provenance(a)
            missing = doc["base"]["trees"]["release"].get("swap_missing")
            print(f"provenance: base {doc['base']['digest'][:12]}, bins {doc['bins']['digest'][:12]}, "
                  f"tests {doc['tests']['digest'][:12]}, os_manifest {str(doc['base']['os_manifest'])[:12]}"
                  + (f", swap list missing {', '.join(missing)}" if missing else ""))
    except Refused as e:
        print(f"img_inputs: {e}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
