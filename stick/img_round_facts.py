#!/usr/bin/env python3
"""One build host's facts for an image round (docs/design/img-speed.md §5.2 step 1).

img_round.py (on the Mac) sends this file over ssh, after pick-host's room probe, and reads
the one "facts JSON" line it prints.  It reads only: it never touches images/, and writes
nothing but img_inputs.py's hash cache (BKO_DIGEST_CACHE), as img-digest.sh does.

What it gives, per platform: img_inputs.py's three digests (the host's own stick/, media,
builder, host tools, ISO and slim), the host's current image (images/P, not prev-*) with its
provenance, done.exit, the test image's SHA-256 (its ImgTest2 pin's while the pin is the
image's, else hashed through the cache) and the release's SHA256SUMS line, the RAM-disk
reservation img-lanes.sh would ask (images/P/peak-kib), and whether this stick/ can refresh the
platform yet (I3, I4).  And the host's bko-bin and holt-bin SHA256SUMS digests (C1, C10), and
each platform's binaries' sizes (bin_sizes: whether a VM platform's refresh fits, §4.2).

From outside: S (the stick/ to read, default BKO_WORK/stick), BKO_WORK, BKO_IMG_DIR (default
BKO_WORK/images), BKO_BIN, BKO_HOLT_BIN, T2_HOST_ROOT (the pins, default /bkoscratch/test2),
BKO_DIGEST_CACHE, SL and ISO, as img_inputs.py and img-lanes.sh read them.
"""

import glob
import hashlib
import importlib.util
import json
import math
import os
import re
import socket
import sys

HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def env(name, default):
    v = os.environ.get(name)
    return v if v else default


def first_line(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.readline().strip()
    except OSError:
        return None


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def file_sha(path):
    """The SHA-256 of a small file (a SHA256SUMS), or None."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def sums_img(path):
    """SHA256SUMS' .img line: (name, sha256), or (None, None)."""
    try:
        for line in open(path, encoding="utf-8", errors="replace"):
            w = line.split()
            if len(w) >= 2 and HEX64.match(w[0]) and w[-1].lstrip("*").endswith(".img"):
                return w[-1].lstrip("*").lstrip("./"), w[0]
    except OSError:
        pass
    return None, None


def reserve_gib(d, plat):
    """img-lanes.sh's RAM-disk figure for PLAT: 1.1 x the last peak + 0.25 GiB, else 6 (linux 1),
    and at least the test image + 0.5 GiB (the boot tests' copy in the RAM disk, img-speed.md §9)."""
    k = first_line(os.path.join(d, "peak-kib"))
    if k and k.isdigit() and int(k) > 0:
        g = round(int(k) * 1.1 / 1048576 + 0.25, 1)
    else:
        g = 1.0 if plat == "linux" else 6.0
    tests = sorted(glob.glob(os.path.join(d, "test", "*.img")))
    try:
        b = os.stat(tests[-1]).st_size if tests else 0
    except OSError:
        b = 0
    if b:
        g = max(g, math.ceil((b / 1073741824 + 0.5) * 10 - 1e-9) / 10)
    return g


def pin_sha(host_root, plat, path):
    """The ImgTest2 pin's SHA-256 of PATH while the pin is that very file (source.json's stat)."""
    pdir = os.path.join(host_root, "img-pin", plat)
    src = read_json(os.path.join(pdir, "source.json")) or {}
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not src or os.path.realpath(str(src.get("path"))) != os.path.realpath(path) \
            or (src.get("size"), src.get("mtime_ns"), src.get("inode")) != (st.st_size, st.st_mtime_ns, st.st_ino):
        return None
    sl = (first_line(os.path.join(pdir, "rescuer.img.sha256")) or "").split()
    return sl[0] if sl and HEX64.match(sl[0]) else None


def refresh_ready(stick):
    """Whether this stick/ has each platform's refresh (§4; built in I3 and I4): Linux's
    img-one.sh and build-backotter-layer.sh take --refresh-from, a VM platform's build.sh has a
    refresh subcommand and img-one.sh passes --refresh-from."""
    def has(path, pat):
        try:
            return re.search(pat, open(path, encoding="utf-8", errors="replace").read(), re.M) is not None
        except OSError:
            return False
    one = has(os.path.join(stick, "img-one.sh"), r"--refresh-from")
    out = {"linux": one and has(os.path.join(stick, "build-backotter-layer.sh"), r"--refresh-from")}
    for p in ("freebsd", "netbsd", "openbsd", "dragonfly", "illumos", "haiku"):
        out[p] = one and has(os.path.join(stick, p, "build.sh"), r"^\s*refresh\)")
    return out


def image(II, cache, images, host_root, plat):
    """images/PLAT's current image, or None when there is none."""
    d = os.path.join(images, plat)
    if not os.path.isdir(d):
        return None
    out = {"done_exit": None, "provenance": None, "test_sha256": None, "release_sha256": None,
           "reserve_gib": reserve_gib(d, plat), "why": None}
    done = first_line(os.path.join(d, "done.exit"))
    out["done_exit"] = int(done) if done and done.lstrip("-").isdigit() else None
    rel_name, rel_sha = sums_img(os.path.join(d, "SHA256SUMS"))
    out["release_name"], out["release_sha256"] = rel_name, rel_sha
    tests = sorted(n for n in os.listdir(os.path.join(d, "test")) if n.endswith(".img")) \
        if os.path.isdir(os.path.join(d, "test")) else []
    out["test_name"] = tests[-1] if tests else None
    pv = read_json(os.path.join(d, "provenance.json"))
    if pv is not None:
        pv.get("base", {}).pop("trees", None)   # (the listing's summary: not the planner's)
        out["provenance"] = pv
    if not tests:
        out["why"] = "no test image (a build moved it away)"
        return out
    tp = os.path.join(d, "test", tests[-1])
    want = ((pv or {}).get("test") or {}).get("file")
    if pv is not None and want and os.path.normpath(want) != os.path.join("test", tests[-1]):
        out["why"] = f"its provenance names {want}, and the test image is test/{tests[-1]}"
        return out
    s = pin_sha(host_root, plat, tp)
    if s is None and pv is not None:
        try:
            s = cache.sha256(tp)
        except OSError as e:
            out["why"] = f"can't read test/{tests[-1]}: {e}"
    out["test_sha256"] = s
    return out


def main():
    work = env("BKO_WORK", "/home/m/backotter-work")
    stick = env("S", os.path.join(work, "stick"))
    images = env("BKO_IMG_DIR", os.path.join(work, "images"))
    host_root = env("T2_HOST_ROOT", "/bkoscratch/test2")
    f = {"host": socket.gethostname().split(".")[0], "stick": stick, "images": images, "errors": [],
         "sets": {"bko-bin": file_sha(os.path.join(env("BKO_BIN", os.path.join(work, "bko-bin")), "SHA256SUMS")),
                  "holt-bin": file_sha(os.path.join(env("BKO_HOLT_BIN", os.path.join(work, "holt-bin")),
                                                    "SHA256SUMS"))},
         "digests": {}, "images": {}, "refresh": refresh_ready(stick), "bin_sizes": {}}
    os.environ["S"] = stick
    try:
        spec = importlib.util.spec_from_file_location("img_inputs", os.path.join(stick, "img_inputs.py"))
        II = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(II)
    except (OSError, ImportError, SyntaxError) as e:
        f["errors"].append(f"no {stick}/img_inputs.py ({type(e).__name__}: {e}): sync stick/ there")
        print("facts " + json.dumps(f, sort_keys=True))
        return 0
    cache = II.Cache()
    bb, hb = env("BKO_BIN", os.path.join(work, "bko-bin")), env("BKO_HOLT_BIN", os.path.join(work, "holt-bin"))
    for p in II.PLATFORMS:
        f["bin_sizes"][p] = {n: os.path.getsize(x) for n, x in (
            ("restic-bko", os.path.join(bb, p, "restic-bko")), ("rustic-bko", os.path.join(bb, p, "rustic-bko")),
            ("bko-holt", os.path.join(hb, p, "bko-holt"))) if os.path.isfile(x)}
        try:
            f["digests"][p] = II.digest(p, cache)
        except Exception as e:   # (one platform's failure leaves the others standing)
            f["digests"][p] = {"error": f"{type(e).__name__}: {e}"}
        try:
            im = image(II, cache, images, host_root, p)
        except OSError as e:
            im = {"why": f"{type(e).__name__}: {e}"}
        if im is not None:
            f["images"][p] = im
    cache.save()
    print("facts " + json.dumps(f, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
