#!/usr/bin/env python3
"""A rescue image's inputs, their digests, its tree listing and its provenance
(docs/design/img-speed.md §3, C7).

Usage: img_inputs.py digest PLATFORM        the three digests and the input list, as JSON
       img_inputs.py moved START.json       exit 3 (and say what) when the inputs moved since START
       img_inputs.py identity PLATFORM      the builder guest's identity, as one JSON line
       img_inputs.py lint                   every stick/ file is an input or listed as none
       img_inputs.py tree-list [--no-owner] a tar on stdin -> the tree listing on stdout
       img_inputs.py provenance ...         images/P/provenance.json (img-one.sh's; --parent DIR: a refresh's)
       img_inputs.py parent --platform P --dir DIR       C4/C5 on a refresh's parent set (JSON line, or exit 3)
       img_inputs.py same-base --provenance F --iso I --slim S [--image IMG]   C5, build-backotter-layer.sh's
       img_inputs.py layout [--platform P] IMG          the image's layout, as listing lines (C2, C3)
       img_inputs.py refresh-check --platform P --parent DIR --dir DIR   C2 and C3 (exit 3 refuses)

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
import glob
import hashlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import uuid

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
    def __init__(self, readonly=False):
        self.path = env("BKO_DIGEST_CACHE", os.path.join(work(), ".img-digest-cache.json"))
        self.dirty = False
        self.readonly = readonly
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
        if not self.dirty or self.readonly:
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


def slim_entry(sl, cache):
    """slim's part of the base: its directory's name and the digest of its files' SHA-256."""
    files = {}
    for n in SLIM_FILES:
        try:
            files[n] = cache.sha256(os.path.join(sl, n))
        except OSError:
            files[n] = None
    return {"path": os.path.basename(os.path.realpath(sl)), "manifest": digest_of(files)}


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


CROSS_ABLE = ("freebsd", "netbsd", "illumos", "openbsd", "dragonfly")   # (img-speed.md §8.1: haiku, linux never)


def holt_build_of(platform, sha):
    """(holt_build, holt_code) of the bko-holt whose sha256 is SHA (img-speed.md I12, §8.3):
    the HoltTest2 build it is, by T2_HOST_ROOT/holt-bin/CODE/build-PLATFORM.json (or a copy
    under CODE/retired/, a build of a kind the set no longer ships): "cross" for holt-cross.sh's
    (its kind "cross"), "vm" for build-holt.sh's, and that CODE.  img-refresh-holt.sh copies
    CODE/PLATFORM/bko-holt, so the binary is one of these.  linux and haiku are VM builds
    whatever is found; (None, None) when no record names SHA."""
    if not sha:
        return None, None
    hb = os.path.join(env("T2_HOST_ROOT", "/bkoscratch/test2"), "holt-bin")
    name = f"build-{platform}.json"
    found = None
    for pat in (os.path.join(hb, "*", name), os.path.join(hb, "*", "retired", "*", name)):
        for jp in sorted(glob.glob(pat)):
            j = read_json(jp)
            if not isinstance(j, dict) or j.get("sha256") != sha:
                continue
            code = os.path.relpath(jp, hb).split(os.sep)[0]
            if re.fullmatch(r"[0-9a-f]{64}", code):
                found = ("cross" if j.get("kind") == "cross" else "vm", code)
                break
        if found:
            break
    if platform not in CROSS_ABLE:
        return "vm", (found or (None, None))[1]
    return found or (None, None)


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
        base["slim"] = slim_entry(sl, cache)
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
    swaps = [s for s in swaps if s["tree"] != "layout"]   # the image's layout, never in a tree listing
    keyed = [(ln.split("\t")[0], ln.split("\t")[1], ln) for ln in listing_lines]
    def is_swap(tree, path):
        return any(s["tree"] == tree and fnmatch.fnmatchcase(path, s["path"]) for s in swaps)
    kept = [ln for t, p, ln in keyed if not is_swap(t, p)]
    missing = [f"{s['tree']}:{s['path']}" for s in swaps if not s["optional"]
               and not any(t == s["tree"] and fnmatch.fnmatchcase(p, s["path"]) for t, p, _ in keyed)]
    trees = sorted({t for t, _, _ in keyed})
    return hashlib.sha256("".join(x + "\n" for x in kept).encode()).hexdigest(), missing, trees


# --- The image's layout (C2, C3): partition tables, boot code, the ESP, El Torito ------

EFI_TYPE = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
SECTOR = 512


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _range_sha(f, start, size):
    h = hashlib.sha256()
    f.seek(start)
    left = size
    while left > 0:
        b = f.read(min(left, 1 << 20))
        if not b:
            break
        h.update(b)
        left -= len(b)
    return h.hexdigest()


def catalog_view(data):
    """The El Torito boot catalog with its load RBAs (positions) zeroed: the validation entry,
    each entry's platform, media, segment, system type and sector count stay."""
    b = bytearray(data)
    for i in range(32, len(b) - 31, 32):
        if b[i] in (0x90, 0x91) or b[i:i + 32] == bytes(32):   # a section header, or nothing
            continue
        b[i + 8:i + 12] = bytes(4)
    return bytes(b)


def layout(img, rewritten=(), xorriso=None, eltorito=True):
    """IMG's layout as listing lines (tree `layout`, PATH, VALUE), sorted: the MBR's entries and
    signature (on a disk with no GPT, each slice's bytes but for REWRITTEN's); the GPT's header
    and entries, each partition's bytes but for REWRITTEN's (partition numbers); the system
    area (boot code) with the partition tables masked; and with ELTORITO (Linux's hybrid ISO;
    the VM platforms' images have none) the El Torito boot images, the catalog with its
    positions masked.  Positions (start and end LBAs) and the random ids (GUIDs) are never
    listed: a refresh that rewrites one partition moves those after it, and draws new ids (C2,
    D2)."""
    out = []
    with open(img, "rb") as f:
        sysarea = bytearray(f.read(32768))
        mbr = bytes(sysarea[:SECTOR])
        gpt = sysarea[SECTOR:SECTOR + 8] == b"EFI PART"
        for i in range(4):
            e = mbr[446 + 16 * i:462 + 16 * i]
            if len(e) == 16 and e[4]:
                line = f"status={e[0]:02x} type={e[4]:02x}"
                if not gpt:
                    # An MBR disk (FreeBSD's): each slice's bytes, as the GPT's partitions'
                    # below; a rewritten FreeBSD slice keeps its first 8 KiB, the boot
                    # blocks and the BSD label, which a refresh never touches.
                    first, n = struct.unpack_from("<II", e, 8)
                    if i + 1 in rewritten:
                        line += " rewritten"
                        if e[4] == 0xA5:
                            out.append(f"/mbr/part/{i + 1}/boot\tsha256={_range_sha(f, first * SECTOR, 16 * SECTOR)}")
                    else:
                        line += f" size={n * SECTOR} sha256={_range_sha(f, first * SECTOR, n * SECTOR)}"
                out.append(f"/mbr/part/{i + 1}\t{line}")
        out.append(f"/mbr/signature\t{mbr[510:512].hex()}")
        sysarea[440:446] = bytes(6)      # the disk signature: an id
        sysarea[446:510] = bytes(64)     # the MBR's entries, listed above
        h = bytes(sysarea[SECTOR:SECTOR + 92])
        if h[:8] == b"EFI PART":
            rev, hsize = struct.unpack_from("<II", h, 8)
            entries_lba, n, esize = struct.unpack_from("<QII", h, 72)
            out.append(f"/gpt/header\trevision={rev:08x} header_size={hsize} entries={n} entry_size={esize}")
            sysarea[SECTOR:2 * SECTOR] = bytes(SECTOR)
            lo, hi = entries_lba * SECTOR, entries_lba * SECTOR + n * esize
            if lo < len(sysarea):
                sysarea[lo:min(hi, len(sysarea))] = bytes(min(hi, len(sysarea)) - lo)
            f.seek(lo)
            ents = f.read(n * esize)
            for i in range(n):
                e = ents[i * esize:(i + 1) * esize]
                if len(e) < 128 or e[:16] == bytes(16):
                    continue
                typ = str(uuid.UUID(bytes_le=e[:16]))
                first, last, attrs = struct.unpack_from("<QQQ", e, 32)
                name = e[56:128].decode("utf-16-le", "replace").split("\x00")[0]
                line = f"type={typ} attrs={attrs:016x} name={esc(name)}"
                if i + 1 in rewritten:
                    line += " rewritten"
                else:
                    size = (last - first + 1) * SECTOR
                    line += f" size={size} sha256={_range_sha(f, first * SECTOR, size)}"
                out.append(f"/gpt/part/{i + 1}\t{line}")
        out.append(f"/system-area\tsha256={_sha(bytes(sysarea))}")
    if not eltorito:
        return sorted("layout\t" + x for x in out)
    xorriso = xorriso or env("BKO_XORRISO", "xorriso")
    with tempfile.TemporaryDirectory(prefix="bko-layout.") as t:
        try:
            r = subprocess.run([xorriso, "-osirrox", "on", "-indev", img, "-extract_boot_images", t + "/"],
                               capture_output=True, text=True)
        except OSError as e:
            raise Refused(f"no xorriso to read {img}'s boot images: {e}")
        if r.returncode != 0:
            raise Refused(f"xorriso couldn't extract {img}'s boot images: {(r.stderr or r.stdout).strip()[-300:]}")
        for n in sorted(os.listdir(t)):
            if n == "systemarea.img":      # the system area, above
                continue
            with open(os.path.join(t, n), "rb") as g:
                data = g.read()
            if n == "eltorito_catalog.img":
                data = catalog_view(data)
            out.append(f"/eltorito/{n}\tsize={len(data)} sha256={_sha(data)}")
    return sorted("layout\t" + x for x in out)


def layout_rewritten(platform, root=None):
    """The partition numbers img-swap.tsv's layout rows say a refresh rewrites: /gpt/part/N, or
    /mbr/part/N on a disk with no GPT (whose slices layout() hashes instead)."""
    out = set()
    for r in swap_rows(platform, root):
        m = re.fullmatch(r"/(?:gpt|mbr)/part/(\d+)", r["path"]) if r["tree"] == "layout" else None
        if m:
            out.add(int(m.group(1)))
    return out


def signed(line):
    """Whether a listing line (a tree's or the layout's) is a signed object of C3: the El Torito
    boot images, the EFI partition (Canonical's shim and GRUB), casper/vmlinuz and initrd."""
    tree, path = line.split("\t")[0], line.split("\t")[1]
    if tree == "layout":
        return path.startswith("/eltorito/eltorito_img") or f"type={EFI_TYPE} " in line
    return tree == "iso" and path in ("/casper/vmlinuz", "/casper/initrd")


# --- A refresh (§4.1, C2-C5) -------------------------------------------------------------

CADENCE_DAYS = 14      # D3, as img_round.py's


def base_view(inputs):
    """The base inputs a refresh compares: all but the media (img_round.py's base_view)."""
    return {k: v for k, v in (inputs or {}).items() if k != "media"}


def same_base(prov_path, iso, slim, image=None):
    """C5 as build-backotter-layer.sh --refresh-from checks it: the parent's provenance names
    ISO's SHA-256 and SLIM's manifest, and (given IMAGE) IMAGE is one of its two images.  The
    hash cache is read, never written (the script runs as root)."""
    pv = read_json(prov_path)
    if pv is None:
        raise Refused(f"no provenance at {prov_path}: an image from before I1 gets a full build")
    if pv.get("schema") != SCHEMA or pv.get("platform") != "linux":
        raise Refused(f"{prov_path} is not a schema-{SCHEMA} provenance of a Linux image")
    cache = Cache(readonly=True)
    inp = (pv.get("base") or {}).get("inputs") or {}
    bad = []
    try:
        got = cache.sha256(iso)
    except OSError as e:
        raise Refused(f"can't read {iso}: {e}")
    if inp.get("iso") != got:
        bad.append(f"the ISO is {got[:12]}, the parent's was {str(inp.get('iso'))[:12]}")
    sl = slim_entry(slim, cache)
    if (inp.get("slim") or {}).get("manifest") != sl["manifest"]:
        bad.append(f"slim is {sl['path']} ({sl['manifest'][:12]}), the parent's was "
                   f"{(inp.get('slim') or {}).get('path')} ({str((inp.get('slim') or {}).get('manifest'))[:12]})")
    if image is not None:
        try:
            h = cache.sha256(image)
        except OSError as e:
            raise Refused(f"can't read {image}: {e}")
        if h not in ((pv.get("release") or {}).get("sha256"), (pv.get("test") or {}).get("sha256")):
            bad.append(f"{image} ({h[:12]}) is neither image of {prov_path}")
    if bad:
        raise Refused("not the parent's base (C5): " + "; ".join(bad) + ": build it in full")
    return pv


def parent_check(platform, pdir, today=None, cadence=None):
    """C4's and C5's part a host can check, before a refresh from the set in PDIR: its
    provenance, its two images as that records them, its base (less the media) the one that
    would be built now, and its OS epoch within the cadence (D3).  That the test image passed
    ImgTest2 and the +img panel is the planner's to check (img_round.py, the Mac's ledger)."""
    prov_path = os.path.join(pdir, "provenance.json")
    pv = read_json(prov_path)
    if pv is None:
        raise Refused(f"no provenance in {pdir}: an image from before I1 gets a full build")
    if pv.get("schema") != SCHEMA or pv.get("platform") != platform:
        raise Refused(f"{prov_path} is not a schema-{SCHEMA} provenance of {platform}")
    cache = Cache()
    out = {"dir": os.path.realpath(pdir), "provenance_sha256": sha256_file(prov_path), "mode": pv.get("mode")}
    for which in ("release", "test"):
        e = pv.get(which) or {}
        f = os.path.join(pdir, e.get("file") or "")
        if not e.get("file") or not os.path.isfile(f):
            raise Refused(f"the parent's {which} image {e.get('file')} is not in {pdir}")
        if cache.sha256(f) != e.get("sha256"):
            raise Refused(f"the parent's {which} image {e.get('file')} is not the one its provenance records")
        out[which] = {"file": e["file"], "sha256": e["sha256"], "image_id": e.get("image_id")}
    now = digest(platform, cache)
    a, b = base_view((pv.get("base") or {}).get("inputs")), base_view(now["base"]["inputs"])
    if a != b:
        ch = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        if "stick" in ch:
            sa, sb = a.get("stick") or {}, b.get("stick") or {}
            ch[ch.index("stick")] = "stick/ (" + ", ".join(sorted(k for k in set(sa) | set(sb)
                                                                  if sa.get(k) != sb.get(k))[:6]) + ")"
        raise Refused("the base changed since the parent (C5): " + ", ".join(ch) + ": build it in full")
    base = pv.get("base") or {}
    epoch = base.get("os_epoch")
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    try:
        age = (today - datetime.date.fromisoformat(str(epoch))).days
    except ValueError:
        raise Refused("the parent's provenance has no OS epoch: build it in full")
    cadence = CADENCE_DAYS if cadence is None else cadence
    if age > cadence:
        raise Refused(f"the parent's OS epoch {epoch} is {age} days old, past the cadence (D3: {cadence}): build it in full")
    if platform != "linux":
        why = room_why(pv, bin_sizes(platform))
        if why:
            raise Refused(why)
    out.update(os_epoch=epoch, os_manifest=base.get("os_manifest"), age_days=age,
               bins_digest=(pv.get("bins") or {}).get("digest"))
    return out


def find_parent(dirs, test_sha256=None):
    """The parent set: the one DIR, or the first of DIRS whose provenance names TEST_SHA256 as
    its test image."""
    if not test_sha256:
        if len(dirs) != 1:
            raise Refused("one parent set, or --test-sha256 to choose among several")
        return dirs[0]
    for d in dirs:
        if ((read_json(os.path.join(d, "provenance.json")) or {}).get("test") or {}).get("sha256") == test_sha256:
            return d
    raise Refused(f"no set here has a provenance whose test image is {test_sha256[:12]}")


def kib(n):
    return (int(n) + 1023) // 1024


def bin_sizes(platform):
    """The sizes of the binaries this build would swap in ({name: bytes}), as bins_of finds them."""
    b = env("BKO_BIN", os.path.join(work(), "bko-bin"))
    h = env("BKO_HOLT_BIN", os.path.join(work(), "holt-bin"))
    return {n: os.path.getsize(x) for n, x in (
        ("restic-bko", os.path.join(b, platform, "restic-bko")), ("rustic-bko", os.path.join(b, platform, "rustic-bko")),
        ("bko-holt", os.path.join(h, platform, "bko-holt"))) if os.path.isfile(x)}


def room_why(pv, sizes):
    """Why binaries of SIZES don't fit the parent whose provenance is PV, by its recorded room, or
    None (§4.2 step 4; img_round.py's room_why and stick/bsd/refresh-lib.sh's bko_fit, the same
    sum).  A parent that records no room is left to the VM's own check."""
    need = sum(kib(v) + kib(v) // 50 + 256 for v in sizes.values()) + 257
    for which in ("release", "test"):
        r = ((pv or {}).get("room") or {}).get(which)
        if isinstance(r, dict) and isinstance(r.get("free_kib"), int) and isinstance(r.get("bins_kib"), int) \
                and need > r["free_kib"] + r["bins_kib"]:
            return (f"the new binaries need {need} KiB, and the parent's {which} image has "
                    f"{r['free_kib'] + r['bins_kib']} (§4.2): build it in full")
    return None


def _bad_paths(a, b, n=8):
    """The paths of the lines in one listing and not the other, at most N, and how many."""
    d = sorted({"\t".join(x.split("\t")[:2]) for x in set(a) ^ set(b)})
    return d[:n], len(d)


def _kept(lines, swaps):
    swaps = [s for s in swaps if s["tree"] != "layout"]
    return [ln for ln in lines
            if not any(s["tree"] == ln.split("\t")[0] and fnmatch.fnmatchcase(ln.split("\t")[1], s["path"])
                       for s in swaps)]


def refresh_check(platform, pdir, cdir, layouts=None):
    """C2 and C3 for a refresh in CDIR of the set in PDIR, release against release and test
    against test: the OS file systems (the tree listings) equal but on the swap list, the
    layout (partition tables, boot code, ESP, El Torito) equal but for the rewritten
    partitions, positions and ids; no signed object changed; the swapped binaries the
    child's bko-bin.sha256 records; and the release and test children differing from each
    other exactly as their parents did.  LAYOUTS (tests) replaces layout().  Returns the
    report's lines; raises Refused with the first finding."""
    swaps = swap_rows(platform)
    rewritten = layout_rewritten(platform)
    pv = read_json(os.path.join(pdir, "provenance.json")) or {}
    lay = layouts or (lambda img: layout(img, rewritten, eltorito=platform == "linux"))
    rep, sets = [], {}
    for which, sub in (("release", ""), ("test", "test")):
        pl = read_listing(os.path.join(pdir, sub, "os-tree.txt"))
        cl = read_listing(os.path.join(cdir, sub, "os-tree.txt"))
        if pl is None:
            raise Refused(f"the parent has no {os.path.join(sub, 'os-tree.txt')}")
        if cl is None:
            raise Refused(f"the refresh has no {os.path.join(sub, 'os-tree.txt')} (its tree listing failed)")
        pimg = os.path.join(pdir, (pv.get(which) or {}).get("file") or "")
        imgs = sorted(x for x in os.listdir(os.path.join(cdir, sub)) if x.endswith(".img"))
        if not os.path.isfile(pimg) or len(imgs) != 1:
            raise Refused(f"no {which} image to compare (parent {pimg}, refresh {imgs})")
        play, clay = lay(pimg), lay(os.path.join(cdir, sub, imgs[0]))
        sets[which] = (pl + play, cl + clay)
        # C3 first: a signed object that moved is never the swap's.
        moved_signed = sorted({"\t".join(x.split("\t")[:2]) for x in set(pl + play) ^ set(cl + clay) if signed(x)})
        if moved_signed:
            raise Refused(f"C3: the {which} image's signed objects changed ({', '.join(moved_signed)}): "
                          "a refresh can't carry that: build it in full")
        for what, a, b in (("OS file systems", _kept(pl, swaps), _kept(cl, swaps)), ("layout", play, clay)):
            paths, n = _bad_paths(a, b)
            if n:
                raise Refused(f"C2: the {which} image's {what} differ from the parent's outside the swap list, "
                              f"{n} entr{'y' if n == 1 else 'ies'}: {', '.join(paths)}")
        rep.append(f"{which}: {len(cl)} entries and {len(clay)} layout lines as the parent's but the swap list; "
                   f"signed objects unchanged")
        # C1: the swapped binaries are the ones this build recorded.
        want = {}
        try:
            for line in open(os.path.join(cdir, "bko-bin.sha256"), encoding="utf-8"):
                h, _, n = line.strip().partition("  ")
                want[os.path.basename(n)] = h
        except OSError:
            raise Refused("the refresh has no bko-bin.sha256")
        for s in swaps:
            if s["kind"] != "bin":
                continue
            got = [x.split("\t") for x in cl if x.split("\t")[0] == s["tree"]
                   and fnmatch.fnmatchcase(x.split("\t")[1], s["path"])]
            name = os.path.basename(s["path"])
            if not got:
                if s["optional"]:
                    continue
                raise Refused(f"C1: the {which} image has no {s['tree']}:{s['path']}")
            if any(g[6] != want.get(name) for g in got):
                raise Refused(f"C1: the {which} image's {s['tree']}:{s['path']} is not the {name} of bko-bin.sha256")
    # The siblings: release and test differ as their parents did (the swap list and ids aside).
    pr, pt = sets["release"][0], sets["test"][0]
    cr, ct = sets["release"][1], sets["test"][1]
    def sib(r, t):
        return set(_kept(r, swaps)) ^ set(_kept(t, swaps))
    if sib(pr, pt) != sib(cr, ct):
        paths, n = _bad_paths(sib(pr, pt), sib(cr, ct))
        raise Refused(f"C2: the release and test refreshes don't differ as their parents did: {', '.join(paths)}")
    rep.append(f"release and test differ as their parents did ({len(sib(cr, ct))} lines)")
    return rep


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
    # A VM platform's room (make-img-bsd.sh's room.json beside each image, §4.2): the free space a
    # refresh of this image could swap new binaries into.
    room = None
    if a.platform != "linux":
        room = {w: read_json(os.path.join(d, sub, "room.json")) for w, sub in (("release", ""), ("test", "test"))}
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
    hbuild, hcode = holt_build_of(a.platform, now["bins"].get("bko-holt"))
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
    epoch, manifest, parent, mode = when[:10], trees["release"]["os_manifest"], None, "full"
    if a.parent:
        # A refresh (§3.1): its OS epoch and OS manifest are its parent's, which C2 has just
        # held its listing to; and the parent is named.
        ppath = os.path.join(a.parent, "provenance.json")
        pv = read_json(ppath)
        if pv is None:
            raise Refused(f"no parent provenance at {ppath}")
        pb = pv.get("base") or {}
        # The parent's OS manifest under today's swap list (its own, unless the list changed).
        plist = read_listing(os.path.join(a.parent, "os-tree.txt"))
        pman = os_manifest(plist, swaps)[0] if plist is not None else pb.get("os_manifest")
        if manifest != pman:
            raise Refused(f"the refresh's OS manifest {str(manifest)[:12]} is not its parent's {str(pman)[:12]}")
        epoch, mode = pb.get("os_epoch"), "refresh"
        parent = {"sha256": (pv.get("release") or {}).get("sha256"), "image_id": (pv.get("release") or {}).get("image_id"),
                  "test_sha256": (pv.get("test") or {}).get("sha256"),
                  "test_image_id": (pv.get("test") or {}).get("image_id"),
                  "provenance_sha256": sha256_file(ppath), "mode": pv.get("mode"),
                  "built": pv.get("built")}
    doc = {
        "schema": SCHEMA, "platform": a.platform, "mode": mode,
        "built": {"host": socket.gethostname().split(".")[0], "when": when, "run": a.run or None,
                  "stick_commit": commit, "stick_dirty": dirty},
        "release": release, "test": test,
        "base": {"digest": now["base"]["digest"], "inputs": now["base"]["inputs"],
                 "os_epoch": epoch, "os_manifest": manifest,
                 "trees": trees},
        "bins": dict(now["bins"], bko_bin_round=rnd, holt_commit=holt_commit, holt_code=holt_code or hcode,
                     holt_build=hbuild),
        "tests": dict(now["tests"], boot=boot, tools=dpkg_versions(TEST_TOOLS)),
        "parent": parent,
        "room": room,
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
    p.add_argument("--parent", help="a refresh's parent set (its directory)")
    q = sub.add_parser("parent")
    q.add_argument("--platform", required=True)
    q.add_argument("--dir", required=True, action="append",
                   help="the parent set; with --test-sha256, each candidate in order")
    q.add_argument("--test-sha256", help="the first --dir whose provenance names this test image")
    q = sub.add_parser("same-base")
    q.add_argument("--provenance", required=True)
    q.add_argument("--iso", required=True)
    q.add_argument("--slim", required=True)
    q.add_argument("--image")
    q = sub.add_parser("layout")
    q.add_argument("--platform", default="linux")
    q.add_argument("image")
    q = sub.add_parser("refresh-check")
    q.add_argument("--platform", required=True)
    q.add_argument("--parent", required=True)
    q.add_argument("--dir", required=True)
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
        elif a.cmd == "parent":
            print(canon(parent_check(a.platform, find_parent(a.dir, a.test_sha256))))
        elif a.cmd == "same-base":
            same_base(a.provenance, a.iso, a.slim, a.image)
        elif a.cmd == "layout":
            for line in layout(a.image, layout_rewritten(a.platform), eltorito=a.platform == "linux"):
                sys.stdout.write(line + "\n")
        elif a.cmd == "refresh-check":
            for line in refresh_check(a.platform, a.parent, a.dir):
                print(line)
        elif a.cmd == "provenance":
            doc = provenance(a)
            missing = doc["base"]["trees"]["release"].get("swap_missing")
            par = (f"a refresh of {str(doc['parent']['test_sha256'])[:12]}, " if doc["parent"] else "")
            print(f"provenance: {par}base {doc['base']['digest'][:12]}, bins {doc['bins']['digest'][:12]}, "
                  f"tests {doc['tests']['digest'][:12]}, os_manifest {str(doc['base']['os_manifest'])[:12]}"
                  + (f", swap list missing {', '.join(missing)}" if missing else ""))
    except Refused as e:
        print(f"img_inputs: {e}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
