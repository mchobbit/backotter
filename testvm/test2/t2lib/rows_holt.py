"""HoltTest ledger rows: what one ht1.sh, ht1-bsd.sh or ht1-offline.sh run
did, built on the host that ran it (contract 2.3 and R5.4, rulings R7, R14,
R17 and R26).

ht1-lib.sh's ht_end runs, as the run ends (README "Where a run works, and
what it keeps"):

    T2_WORK=W timeout -k 5 30 python3 -B T/test2/t2-host row holt \\
        --k K | --lost | --refused  --r R --run RUN --gv GV --driver ht1.sh --rc RC --code CODE

and t2-host calls build(argv, hostctx), appends the row and prints its id.
build() reads K's records (report.txt, logs/manifest.txt, logs/phases.tsv,
logs/kept.txt; R's when K isn't there), T2_CELLS's entry for GV (HoltTest2's
host cells.json: word, rule, reasons, planned key, attempt), the copy's
T2-COPY.json (or a synced tree's .backotter-commit), and measures the cell's
inputs from the tree the run ran from and this host's facts (keys.measure
over testvm/test2/holt/spec.json).  It writes nothing.

A run with no T2_CELLS is a HoltTest1 run: rule test1, no planned key.
holt.run writes the rows of runs that got none (a refusal before ht_begin, a
killed or swept run: R7) with backfill_row().

Verdicts and classes (for the stop rules):
    PASS, pass         RESULT: PASS, exit 0
    FLAKY-FAIL, fail   the same on attempt 2 (the rerun after a FAIL)
    PASS, known-flake  the same on attempt 2 when T2_CELLS marks the first's FAILs a known flake
    PASS, pass         the same on attempt 2 when T2_CELLS says attempt 1 made its one-time
                       rescuer (rerun_why rescuer-made: a PASS that couldn't count, not a FAIL)
    FAIL, fail         a check failed
    HARNESS, harness   refused (exit 2), stopped over its reservation before its RESULT line, no RESULT line
    LOST, lost         swept (the run died), ended by a signal, its records not copied to K
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

from . import keys as _keys
from . import facts_host as _facts

try:   # P1's helpers; the fallbacks below keep this module usable without them
    from . import file_sha256, parse_utc, short_host, utc_now, work_dir, host_root
except ImportError:   # pragma: no cover
    file_sha256 = parse_utc = short_host = utc_now = work_dir = host_root = None

SPEC = "testvm/test2/holt/spec.json"
HOLT_BIN = "/bkoscratch/holttest1d/bin"
RESCUERS = "/bkoscratch/holttest1d/rescuer"
DRIVERS = {"ht1.sh": "linux", "ht1-bsd.sh": "bsd", "ht1-offline.sh": "offline"}
VARIANTS = ("bigger", "oneshot", "degraded", "img", "smaller", "fitbigger", "nearfull", "refuse")
# HoltTest1's refusal variants (ht1.sh, ht1-offline.sh): source and rebuild are the whole run.
REFUSALS = ("nearfull", "refuse")
ALL_PHASES = ["source", "backup", "rebuild", "verify"]
# Settings that change what a run checks or with what: such a run's row never counts (R7).
DEBUG_ENV = ("HT_QUICK", "HT_THROUGH", "HT_KEEP", "HT_FAIL_AT", "HT_RESCUER", "HT_RESCUER_IMG",
             "HT_LIMIT_KIB", "HT_FREE_KIB", "HT_NVRAM")
# The same for one value only: HT_BOOT_EACH_DISK=0 turns the boot of each disk alone off (=1
# only adds it, on a source of one ESP: a plain run's checks and more).
DEBUG_ENV_VALUES = {"HT_BOOT_EACH_DISK": ("0",)}
GUEST_FILES = ("vm-run-options", "ssh-user", "console-passphrase", "luks-passphrase", "zfs-passphrase", "no-ssh")
MAX_FAILS, MAX_LEN = 20, 200


# --- small helpers -------------------------------------------------------------------

def _now():
    return utc_now() if utc_now else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _epoch(s):
    if parse_utc:
        return parse_utc(s)
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    except (TypeError, ValueError):
        return None


def _sha(path):
    if file_sha256:
        try:
            return file_sha256(path)
        except OSError:
            return None
    import hashlib
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _read(path, limit=4 << 20):
    try:
        with open(path, "rb") as f:
            return f.read(limit).decode("utf-8", "replace")
    except OSError:
        return None


def _first_line(path):
    t = _read(path, 1 << 16)
    return t.splitlines()[0].strip() if t and t.splitlines() else None


def _cut(s, n=MAX_LEN):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n - 1] + "…"


def _host():
    if short_host:
        return short_host()
    import socket
    return socket.gethostname().split(".")[0]


def cell_of(gv, variant=None):
    """holt:GUEST[+VARIANT] of a run directory's name GV (GUEST or GUEST-VARIANT).
    VARIANT (the manifest's HT_VARIANT) settles it; else a known suffix does."""
    if variant:
        suffix = "-" + variant
        g = gv[:-len(suffix)] if gv.endswith(suffix) else gv
        return f"holt:{g}+{variant}"
    for v in VARIANTS:
        if gv.endswith("-" + v):
            return f"holt:{gv[:-len(v) - 1]}+{v}"
    return f"holt:{gv}"


def gv_of(cell):
    """The run directory's name of holt:GUEST[+VARIANT]: GUEST or GUEST-VARIANT."""
    name = cell[5:] if cell.startswith("holt:") else cell
    g, _, v = name.partition("+")
    return f"{g}-{v}" if v else g


# --- the records ----------------------------------------------------------------------

def parse_manifest(text):
    """logs/manifest.txt (ht1-lib.sh's ht_manifest) as a dict."""
    m = {"env": {}, "sha256": {}, "images": {}, "host": None, "driver": None, "mode": None, "work": None,
         "records": None, "reserved": None, "seeded": None, "tree": None, "rustic": None, "version": None}
    for line in (text or "").splitlines():
        if line.startswith("host "):
            m["host"] = line[5:]
        elif line.startswith("driver "):
            mm = re.match(r"driver (\S+) mode (\S+) work (\S+) records (\S+) reserved (\S+)(?: \(pid \d+\))?"
                          r"(?: seeded from (\S+))?", line)
            if mm:
                m.update(driver=mm.group(1), mode=mm.group(2), work=mm.group(3), records=mm.group(4),
                         reserved=mm.group(5), seeded=mm.group(6))
        elif line.startswith("env "):
            k, _, v = line[4:].partition("=")
            m["env"][k] = v
        elif line.startswith("rustic-bko "):
            mm = re.match(r"rustic-bko (\S+) ([0-9a-f]{64}) (\S+) \((.*)\)$", line)
            if mm:
                m["rustic"] = {"plat": mm.group(1), "sha256": mm.group(2), "src": mm.group(3), "id": mm.group(4)}
        elif line.startswith("tree "):
            m["tree"] = line[5:].strip()
        elif line.startswith("sha256 "):
            parts = line.split(" ", 2)
            if len(parts) == 3 and re.fullmatch(r"[0-9a-f]{64}", parts[1]):
                m["sha256"][parts[2]] = parts[1]
        elif line.startswith("image "):
            mm = re.match(r"image (\S+) (\d+) (\d+) (\d+)(?: sha256-file ([0-9a-f]{64}))?$", line)
            if mm:
                m["images"][mm.group(1)] = {"size": int(mm.group(2)), "mtime": int(mm.group(3)),
                                            "inode": int(mm.group(4)), "sha256_file": mm.group(5)}
        elif line.startswith("version "):
            m["version"] = line[8:]
    return m


def parse_phases(text):
    """logs/phases.tsv: [{phase, start_utc, seconds, peak_disk_kib, peak_anon_kib, reserved_gib, mode, fails}]."""
    rows, head = [], None
    for line in (text or "").splitlines():
        f = line.split("\t")
        if head is None:
            head = f
            continue
        if len(f) != len(head):
            continue
        r = dict(zip(head, f))
        for k in ("seconds", "peak_disk_kib", "fails"):
            try:
                r[k] = int(r.get(k))
            except (TypeError, ValueError):
                r[k] = None
        try:
            r["peak_anon_kib"] = int(r.get("peak_anon_kib"))
        except (TypeError, ValueError):
            r["peak_anon_kib"] = None
        rows.append(r)
    return rows


# Check names, from the PASS and FAIL lines the drivers print (ht1.sh, ht1-bsd.sh,
# ht1-offline.sh, and ht1-lib.sh's own): first match wins.
NEEDS = [
    (r"source boots", "source-boots"),
    (r"source's arrays degraded|no array of the source is degraded", "source-degraded"),
    (r"dump (on the live source|of the source's disks)", "source-dump"),
    (r"(the source mounted read-only|mount the source|the mount script|the source's pool \S+ imported)", "backup-mount"),
    (r"rustic-bko backup", "backup"),
    (r"(fsmeta record of the source|the listing of the source's files)", "source-record"),
    (r"(rescuer up|rescuer boots$|rescuer setup|the rescuer's restart|no rescuer with the backup)", "rescuer"),
    (r"load refuse-nomap", "refuse-nomap"),
    (r"load refuse-root", "refuse-root"),
    (r"load refuse-decoy", "refuse-decoy"),
    (r"load dry-partition", "dry-run"),
    (r"(nothing written without --commit|written without --commit)", "no-write-without-commit"),
    (r"(first and last MiB as they were|a refusal or the dry run wrote)", "first-last-mib"),
    (r"load (partition|assemble|format|mount|restore|boot|close|oneshot)\b", "stage:{1}"),
    (r"status after the one-command rebuild", "status:oneshot"),
    (r"status after (\w+)", "status:{1}"),
    (r"the decoy (lost its filesystem|changed)", "decoy"),
    (r"the layout lists", "layout-disks"),
    (r"(fsmeta: |mtree: )", "fsmeta"),
    (r"(mount the restored|rescuer boots for the mtree)", "verify-mount"),
    (r"the restored pool \S+ imported", "verify-pool"),
    (r"the restored disk boots by itself", "boots-by-itself"),
    (r"restored arrays", "degraded-arrays"),
    (r"boot state", "boot-state"),
    (r"health: ", "health"),
    (r"dump of the restored", "restored-dump"),
    (r"the dumps (agree|differ)", "dump-judge"),
    (r"HT_FAIL_AT=", "test-hook"),
    (r"HT_THROUGH=", "test-hook"),
]
_NEEDS = [(re.compile(p), n) for p, n in NEEDS]
_RANK = {"SKIP": 0, "PASS": 1, "DIFF": 2, "LIMIT": 3, "LIMIT-NEW": 4, "FAIL": 5}


def need_of(text):
    """The need a PASS or FAIL line's text checks (a fallback slug for the rest)."""
    for rx, name in _NEEDS:
        m = rx.search(text)
        if m:
            return name.replace("{1}", m.group(1)) if "{1}" in name else name
    slug = re.sub(r"[^a-z0-9]+", "-", re.split(r"[:(]", text, 1)[0].lower()).strip("-")[:40]
    return "other:" + (slug or "check")


def parse_report(text):
    """report.txt: {needs, counts, fails, result, start, notes, header, phases (the ones asked for)}."""
    needs, counts, fails, notes = {}, {"PASS": 0, "FAIL": 0, "LIMIT": 0}, [], []
    result = start = header = phases = None
    for line in (text or "").splitlines():
        if line.startswith("HoltTest1 ") and header is None:
            header = line
            m = re.search(r", (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ), phases: (.*)$", line)
            start = m.group(1) if m else None
            phases = m.group(2).split() if m else None
        elif line.startswith(("PASS ", "FAIL ", "LIMIT ")):
            v, _, rest = line.partition(" ")
            counts[v] += 1
            n = need_of(rest)
            if _RANK[v] > _RANK.get(needs.get(n), -1):
                needs[n] = v
            if v == "FAIL" and len(fails) < MAX_FAILS:
                fails.append(_cut(rest))
        elif line.startswith("RESULT: "):
            result = line
        elif line.startswith("note: "):
            notes.append(line[6:])
    return {"needs": needs, "counts": counts, "fails": fails, "result": result, "start": start,
            "notes": notes, "header": header, "phases": phases}


def classify(rep, kept, rc, code, attempt=1, flake=False, refused=False, lost=False, rerun_why=None):
    """(verdict, class, why) of a run (see the module's docstring)."""
    if refused:
        return "HARNESS", "harness", "refused before any phase"
    if lost:
        return "LOST", "lost", "the run's records couldn't be copied to K"
    kept = kept or ""
    result = (rep or {}).get("result") or ""
    if kept.startswith("swept:"):
        return "LOST", "lost", "the run died: swept (" + _cut(kept, 120) + ")"
    if result == "RESULT: PASS" and code == 0:
        # (A run stopped over its reservation only after its RESULT line keeps that verdict,
        # as ht_end says: every check ran.  The overrun is a note.)
        late = ("overran its reservation after its RESULT line: " + _cut(kept.partition("harness: ")[2], 120)) \
            if kept.startswith("overrun:") else None
        if attempt >= 2:
            if rerun_why == "rescuer-made":
                return "PASS", "pass", "; ".join(x for x in ("attempt 1 made the one-time rescuer", late) if x)
            if flake:
                return "PASS", "known-flake", "; ".join(x for x in ("attempt 2 passed; attempt 1's FAILs are a known flake", late) if x)
            return "FLAKY-FAIL", "fail", "; ".join(x for x in ("attempt 2 passed after attempt 1 failed", late) if x)
        return "PASS", "pass", late
    if kept.startswith("overrun:") or "; harness: " in result:
        return "HARNESS", "harness", "harness: " + _cut(kept.partition("harness: ")[2] or result, 160)
    if rc is not None and rc >= 128:
        return "LOST", "lost", f"ended by a signal (exit {rc})"
    if not result:
        return "HARNESS", "harness", "no RESULT line"
    return "FAIL", "fail", None


# --- where the run ran from -----------------------------------------------------------

def tree_of(driver_path):
    """The copy root (the directory holding testvm/) of a driver's path."""
    if not driver_path:
        return None
    p = os.path.realpath(driver_path) if os.path.exists(driver_path) else driver_path
    d = os.path.dirname(os.path.dirname(os.path.dirname(p)))   # ROOT/testvm/holttest1d/ht1.sh
    return d or None


def copy_info(root, cells_copy=None, hostctx=None):
    """(commit, committed, dirty, copy id) of the tree at ROOT: the host
    context's tree_info() (P1's), else a private copy's T2-COPY.json, else a
    synced tree's testvm/.backotter-commit (R28's rule: clean counts, .dirty
    never does).  An unknown commit is None."""
    ti = getattr(hostctx, "tree_info", None)
    if callable(ti) and root:
        try:
            t = ti(root)
            if isinstance(t, dict) and "committed" in t:
                return t.get("commit"), bool(t.get("committed")), t.get("dirty"), t.get("copy") or "unknown"
        except Exception:   # the stand-in below
            pass
    info = None
    if root:
        t = _read(os.path.join(root, "T2-COPY.json"))
        if t:
            try:
                info = json.loads(t)
            except ValueError:
                info = None
    if info is None and cells_copy and root and os.path.realpath(cells_copy.get("path") or "") == os.path.realpath(root):
        info = cells_copy
    if isinstance(info, dict) and info.get("commit"):
        dirty = info.get("dirty")
        committed = isinstance(dirty, dict) and not dirty
        return info["commit"], committed, (dirty if isinstance(dirty, dict) else None), info.get("id") or "copy"
    stamp = _first_line(os.path.join(root, "testvm", ".backotter-commit")) if root else None
    if stamp:
        if stamp.endswith(".dirty"):
            return stamp[:-len(".dirty")], False, None, "synced"
        return stamp, True, {}, "synced"
    return None, False, None, "unknown"


def load_cells(path, gv):
    """(cells.json, its entry for GV) from T2_CELLS, or (None, None)."""
    if not path:
        return None, None
    t = _read(path)
    if t is None:
        return None, None
    try:
        doc = json.loads(t)
    except ValueError:
        return None, None
    return doc, ((doc.get("cells") or {}).get(gv) if isinstance(doc, dict) else None)


# --- facts (this host's), for keys.measure ---------------------------------------------

def _dpkg_version(pkg):
    try:
        r = subprocess.run(["dpkg-query", "-W", "-f=${Version}", pkg], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _versions():
    v = {"pkgs": {}}
    try:
        v["kernel"] = os.uname().release
    except AttributeError:
        v["kernel"] = None
    v["zfs"] = _first_line("/sys/module/zfs/version")
    try:
        r = subprocess.run(["qemu-system-x86_64", "--version"], capture_output=True, text=True, timeout=5)
        v["qemu"] = r.stdout.splitlines()[0].strip() if r.returncode == 0 and r.stdout else None
    except (OSError, subprocess.SubprocessError, IndexError):
        v["qemu"] = None
    v["ovmf"] = _dpkg_version("ovmf")
    return v


def _image_state(gdir):
    t = _read(os.path.join(gdir, "image-state"))
    if not t:
        return None
    try:
        return json.loads(t)
    except ValueError:
        return None


def _vm_options(gdir):
    t = _read(os.path.join(gdir, "vm-run-options")) or ""
    return t.split()


def guest_facts(work, guest, imgsums=None):
    """One guest's facts as keys.py reads them: generation, files, firmware, disks, imgsum."""
    gdir = os.path.join(work, "vms", guest)
    if not os.path.isdir(gdir):
        return None
    st = _image_state(gdir) or {}
    files = {}
    for f in GUEST_FILES:
        p = os.path.join(gdir, f)
        files[f] = _sha(p) if os.path.isfile(p) else "absent"
    opts = _vm_options(gdir)
    disks = []
    img = os.path.join(gdir, guest + ".qcow2")
    if not os.path.isfile(img):
        cands = sorted(x for x in os.listdir(gdir) if x.endswith(".qcow2"))
        img = os.path.join(gdir, cands[0]) if cands else None
    if img:
        disks.append(img)
    for i, o in enumerate(opts):
        if o == "--disk" and i + 1 < len(opts):
            d = opts[i + 1]
            disks.append(d if d.startswith("/") else os.path.join(gdir, d))
    out = {"generation": st.get("generation"), "updates": st.get("updates"), "files": files,
           "firmware": "uefi" if "--uefi" in opts else "bios", "disks": [], "imgsum": None,
           "fw": _facts.fw_of(opts), "stat": _facts.stat_of(gdir),
           "fingerprint": _facts.fingerprint_of(gdir, st.get("generation"))}
    for d in disks:
        try:
            s = os.stat(d)
            out["disks"].append({"path": d, "size": s.st_size, "mtime": int(s.st_mtime), "mtime_ns": s.st_mtime_ns,
                                 "inode": s.st_ino})
        except OSError:
            out["disks"].append({"path": d, "size": None, "mtime": None, "mtime_ns": None, "inode": None})
    if not out["generation"] and imgsums is not None:
        out["imgsum"] = _cached_imgsum(imgsums, out["disks"])
    return out


def _imgsum_cache(hroot):
    """HOST_ROOT/cache/imgsum.json (t2-host imgsum's), as {path: entry}."""
    t = _read(os.path.join(hroot, "cache", "imgsum.json")) if hroot else None
    if not t:
        return {}
    try:
        d = json.loads(t)
    except ValueError:
        return {}
    if isinstance(d, dict) and isinstance(d.get("entries"), dict):
        d = d["entries"]
    return d if isinstance(d, dict) else {}


def _cache_hit(cache, path):
    e = cache.get(path)
    if not isinstance(e, dict):
        return None
    try:
        s = os.stat(path)
    except OSError:
        return None
    if (e.get("size"), e.get("mtime"), e.get("inode")) != (s.st_size, int(s.st_mtime), s.st_ino):
        return None
    v = e.get("sum") or e.get("imgsum") or e.get("img")
    if not isinstance(v, str):
        return None
    return v if v.startswith("img:") else "img:" + v


def _cached_imgsum(cache, disks):
    """img: of a guest's disks from the cache (one disk: its entry; several: a
    cache entry under the joined paths), else None: no image is ever hashed here."""
    if len(disks) == 1:
        return _cache_hit(cache, disks[0]["path"])
    joined = "+".join(d["path"] for d in disks)
    hit = cache.get(joined)
    if isinstance(hit, dict) and all(_cache_hit(cache, d["path"]) for d in disks):
        v = hit.get("sum") or hit.get("imgsum")
        if isinstance(v, str):
            return v if v.startswith("img:") else "img:" + v
    return None


def _set_facts(setdir):
    """A rustic-bko set's facts: sums from SHA256SUMS, digests from OS/NAME.digest."""
    sums, digests = {}, {}
    for line in (_read(os.path.join(setdir, "SHA256SUMS")) or "").splitlines():
        f = line.split()
        if len(f) == 2 and re.fullmatch(r"[0-9a-f]{64}", f[0]):
            n = f[1].lstrip("*")
            n = n[2:] if n.startswith("./") else n
            sums[n] = f[0]
    for n in list(sums):
        if n.endswith("-bko"):
            for line in (_read(os.path.join(setdir, n + ".digest")) or "").splitlines():
                if line.startswith("digest "):
                    digests[n] = line[len("digest "):].strip()
    return {"sums": sums, "digests": digests, "build_info": _first_line(os.path.join(setdir, "BUILD-INFO"))}


def _dir_digest(path):
    """dir: of a host directory (keys.py's form), or None when it isn't there."""
    if not os.path.isdir(path):
        return None
    entries = {}
    for dp, dn, fn in os.walk(path):
        dn.sort()
        for f in sorted(fn):
            ap = os.path.join(dp, f)
            rel = os.path.relpath(ap, path).replace(os.sep, "/")
            entries[rel] = ("->" + os.readlink(ap)) if os.path.islink(ap) else _sha(ap)
    import hashlib
    lines = "".join(sorted(f"{r}\t{v}\n" for r, v in entries.items()))
    return "dir:" + hashlib.sha256(lines.encode()).hexdigest()


def local_facts(work, hroot, guests, sets, entry, shared=()):
    """The facts keys.measure needs for one holt cell, read on this host (a
    stand-in for facts_host.py's, in its shape): versions, these guests, the
    rescuers and bin/'s packages, the sets, the shared plumbing files and the
    entry's host inputs.  It hashes no disk image (imgsums come from the cache)."""
    cache = _imgsum_cache(hroot)
    f = {"host": _host(), "t": _now(), "versions": _versions(), "guests": {}, "sets": {},
         "rescuers": {"images": {}, "bin": {}}, "plumbing": {}, "host_inputs": {}}
    if entry.get("bins_from_image"):
        f["images"] = _facts.images_part(os.environ.get("T2_IMAGES") or os.path.join(work, "images"), hroot)
    for g in guests:
        gf = guest_facts(work, g, cache)
        if gf is not None:
            f["guests"][g] = gf
    for name, d in sets.items():
        if d and os.path.isdir(d):
            f["sets"][name] = _set_facts(d)
    try:
        for n in sorted(os.listdir(RESCUERS)):
            if n.endswith((".qcow2", ".img", ".raw")):
                p = os.path.join(RESCUERS, n)
                s = _first_line(p + ".sha256")
                f["rescuers"]["images"][n] = {"sha256": s.split()[0] if s else None,
                                              "imgsum": _cache_hit(cache, p)}
    except OSError:
        pass
    try:
        for n in sorted(os.listdir(HOLT_BIN)):
            p = os.path.join(HOLT_BIN, n)
            if os.path.isfile(p) and (n.endswith(".deb") or n == "bcachefs"):
                f["rescuers"]["bin"][n] = _sha(p)
    except OSError:
        pass
    for s in shared:
        p = os.path.join(work, s[len("shared:"):])
        f["plumbing"][s] = _sha(p) if os.path.exists(p) else None
    for item in (entry.get("host_inputs") or []):
        path = item["path"] if isinstance(item, dict) else item
        ap = os.path.join(work, path[len("~/backotter-work/"):]) if path.startswith("~/backotter-work/") else \
            os.path.expanduser(path)
        f["host_inputs"][path] = _dir_digest(ap)
    return f


def host_facts(hostctx, work, hroot, guests, sets, entry, shared):
    """This host's facts: the host context's (P1's probe), else local_facts()."""
    want = {"guests": sorted(guests), "sets": sorted(x for x in sets if x), "plumbing": sorted(shared),
            "host_inputs": sorted((i["path"] if isinstance(i, dict) else i) for i in (entry.get("host_inputs") or []))}
    if entry.get("bins_from_image"):
        want["images"] = True
    for attr in ("facts", "get_facts"):
        v = getattr(hostctx, attr, None)
        try:
            if callable(v):
                v = v(want)
        except Exception:   # a probe that fails: the local stand-in below
            v = None
        if isinstance(v, dict) and v.get("guests") is not None:
            return v, "hostctx"
    return local_facts(work, hroot, guests, sets, entry, shared), "local"


# --- the row ------------------------------------------------------------------------------

def _args(argv):
    a = list(argv)
    while a and a[0] in ("row", "holt"):
        a.pop(0)
    p = argparse.ArgumentParser(prog="t2-host row holt", add_help=False)
    p.add_argument("--k")
    p.add_argument("--r")
    p.add_argument("--run", required=True)
    p.add_argument("--gv", required=True)
    p.add_argument("--driver", default="ht1.sh")
    p.add_argument("--rc", type=int, default=None)
    p.add_argument("--code", type=int, default=None)
    p.add_argument("--lost", action="store_true")
    p.add_argument("--refused", action="store_true")
    p.add_argument("--work")
    p.add_argument("--host-root")
    p.add_argument("--now")   # tests: the time the row is written
    return p.parse_args(a)


def _set_of(rustic_src, work):
    """(name, dir) of the set a rustic-bko came from, as facts_host.py names sets:
    bko-bin, roundNAME (~/backotter-work/roundNAME/bin), else the directory's path."""
    if not rustic_src:
        return "bko-bin", os.path.join(work, "bko-bin")
    d = os.path.dirname(os.path.dirname(rustic_src))
    if os.path.basename(d) == "bin" and os.path.basename(os.path.dirname(d)).startswith("round"):
        return os.path.basename(os.path.dirname(d)), d
    if os.path.basename(d) == "bko-bin":
        return "bko-bin", d
    return d, d


def _holt_sums(d):
    """{plat: sha256} from a bko-holt set's SHA256SUMS (bko-holt is linux's)."""
    out = {}
    for line in (_read(os.path.join(d, "SHA256SUMS")) or "").splitlines():
        f = line.split()
        if len(f) == 2 and re.fullmatch(r"[0-9a-f]{64}", f[0]):
            n = f[1].lstrip("*")
            n = n[2:] if n.startswith("./") else n
            if n == "bko-holt":
                out["linux"] = f[0]
            elif n.endswith("/bko-holt"):
                out[n[:-len("/bko-holt")]] = f[0]
    return out


def holt_used(man, plat, holt_file, holt_bin):
    """The sha256 of the bko-holt the run used, from its manifest (ht_manifest's
    "sha256 HASH HOLT" line): HT_HOLT's file, HT_HOLT_BIN's or bin/'s for PLAT,
    else the first bko-holt that isn't the share's copy."""
    sub = "bko-holt" if plat == "linux" else f"{plat}/bko-holt"
    for p in ([holt_file] if holt_file else []) + [os.path.join(holt_bin or HOLT_BIN, sub)]:
        if p and p in man["sha256"]:
            return man["sha256"][p]
    for p, h in man["sha256"].items():
        if p.endswith("/bko-holt") and "/share/" not in p:
            return h
    return None


def _rustic_commits(setdir, plat):
    """meta.bin's commit lines for PLAT/rustic-bko (R2: never keyed)."""
    out = {}
    for line in (_read(os.path.join(setdir, plat, "rustic-commit")) or "").splitlines():
        f = line.split()
        if f and re.fullmatch(r"[0-9a-f]{40}", f[0]):
            out["rustic"] = f[0]
        elif len(f) >= 2 and f[0] == "rustic_core" and re.fullmatch(r"[0-9a-f]{40}", f[1]):
            out["rustic_core"] = f[1]
    return out


def build(argv, hostctx=None):
    """The row of one HoltTest run, from its records (see the module docstring)."""
    a = _args(argv)
    work = a.work or getattr(hostctx, "work", None) or (work_dir() if work_dir else os.path.expanduser("~/backotter-work"))
    hroot = a.host_root or getattr(hostctx, "host_root", None) or (host_root() if host_root else "/bkoscratch/test2")
    now = a.now or _now()
    notes, unmeasured = [], []

    # K's records (R's while K isn't there); none for a refusal or a lost run, whose R
    # may still hold a dead run's (ht1-lib.sh keeps that for the sweep).
    kdir = None
    if not (a.refused or a.lost):
        kdir = a.k if a.k and os.path.isdir(a.k) else (a.r if a.r and os.path.isdir(a.r) else a.k)
    rep = parse_report(_read(os.path.join(kdir, "report.txt")) if kdir else None)
    man = parse_manifest(_read(os.path.join(kdir, "logs", "manifest.txt")) if kdir else None)
    phases = parse_phases(_read(os.path.join(kdir, "logs", "phases.tsv")) if kdir else None)
    kept = _first_line(os.path.join(kdir, "logs", "kept.txt")) if kdir else None

    # The run's HT_ settings: its manifest's (as it started), else the environment
    # the driver runs this in (a refusal has no manifest yet).
    env = dict((k, v) for k, v in os.environ.items() if k.startswith("HT_")) if not man["driver"] else {}
    env.update(man["env"])
    variant = env.get("HT_VARIANT") or None
    cells_path = os.environ.get("T2_CELLS")
    cells, ce = load_cells(cells_path, a.gv)
    cell = (ce or {}).get("cell") or cell_of(a.gv, variant)
    guest = cell[5:].partition("+")[0]
    attempt = int((ce or {}).get("attempt") or 1)
    if cells_path and ce is None:
        notes.append(f"{a.gv} not in T2_CELLS ({cells_path}): a HoltTest1 row")
    t2_run = os.environ.get("T2_RUN") if ce else None

    # The tree it ran from, and its commit.
    driver_path = man["driver"]
    root = tree_of(driver_path) or os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    commit, committed, dirty, copy_id = copy_info(root, (cells or {}).get("copy") if cells else None, hostctx)

    # The spec entry and the cell's inputs.
    src = _keys.TreeSource(root)
    specs = _keys.Specs(src)
    sf = specs.get("holt")
    entry, kind = {}, DRIVERS.get(a.driver)
    if sf is None:
        notes.append(f"no {SPEC} in {root}")
    else:
        try:
            entry = sf.entry(guest, cell.partition("+")[2] or None)
            kind = entry.get("kind") or kind
        except KeyError:
            notes.append(f"no spec entry for {guest} in {SPEC}")
    plat = entry.get("plat") or (man["rustic"] or {}).get("plat") or "linux"
    rustic_src = (man["rustic"] or {}).get("src")
    set_name, set_dir = _set_of(rustic_src, work)
    is_img = variant == "img"   # the image's own binaries: facts.images, by bins_from_image
    loose_rustic = not is_img and (env.get("HT_RUSTIC") or ("no set" in ((man["rustic"] or {}).get("id") or "")))
    rescuer_guests = [x for x in (entry.get("rescuer") or []) if isinstance(x, str) and not x.startswith(("@", "bin/"))
                      and not x.endswith((".qcow2", ".img", ".raw"))]
    shared = [x for x in (sf.data.get("plumbing") or []) if isinstance(x, str) and x.startswith("shared:")] if sf else []
    facts, facts_by = host_facts(hostctx, work, hroot, [guest] + rescuer_guests, {set_name: set_dir}, entry, shared)

    # bko-holt: code:CODE for a HoltTest2 build (HT_HOLT_BIN=HOST_ROOT/holt-bin/CODE), else the
    # sha256 of what the run used (R14).
    holt_file = env.get("HT_HOLT")
    holt_bin = env.get("HT_HOLT_BIN")
    used = holt_used(man, plat, holt_file, holt_bin)
    opts = {"rustic_set": set_name}
    code = None
    if is_img:
        pass
    elif not holt_file and holt_bin and re.fullmatch(r"[0-9a-f]{64}", os.path.basename(holt_bin.rstrip("/"))):
        code = os.path.basename(holt_bin.rstrip("/"))
        want = _holt_sums(holt_bin).get(plat)
        if want and used and want != used:
            unmeasured.append(f"bko-holt {used[:12]} isn't {holt_bin}'s {want[:12]} (SHA256SUMS)")
        elif not want:
            # (A CODE directory made by hand, or one a run left before its SHA256SUMS: the
            # code: key would vouch for a binary nothing checked.)
            unmeasured.append(f"{holt_bin} has no SHA256SUMS line for {plat}/bko-holt")
        # (R26: the set's binary now, against what the run took at its start.)
        now_sha = _sha(os.path.join(holt_bin, "bko-holt" if plat == "linux" else f"{plat}/bko-holt"))
        if used and now_sha and now_sha != used:
            unmeasured.append(f"{holt_bin}'s {plat} bko-holt changed during the run")
        opts["holt_code"] = code
    else:
        opts["holt_sha256"] = {plat: used} if used else {}
        if not used and not a.refused and not a.lost:
            unmeasured.append("no bko-holt sha256 in logs/manifest.txt")

    try:
        m = _keys.measure(cell, src, facts, specs, opts)
    except (KeyError, ValueError) as e:
        notes.append(f"inputs not measured: {e}")
        m = {"inputs": {}, "key": _keys.key(cell, {}), "groups": _keys.groups({}), "spec": None,
             "notes": [], "unknown": ["all"]}
    if is_img and used:     # the pin's bko-holt and the key's (the image's own) must be one build
        keyed = ((m.get("inputs") or {}).get("bin") or {}).get(f"{plat}/bko-holt")
        if keyed and keyed != f"sha256:{used}":
            unmeasured.append(f"bko-holt {used[:12]} isn't the image's {str(keyed)[-64:][:12]} (bko-bin.sha256)")
    inputs = m["inputs"]
    if loose_rustic and man["rustic"]:
        b = inputs.setdefault("bin", {})
        b[f"{plat}/rustic-bko"] = "sha256:" + man["rustic"]["sha256"]
        b[f"{plat}/rustic-bko#digest"] = "absent"
        notes.append("rustic-bko from HT_RUSTIC, no set")
    key_, groups_ = _keys.key(cell, inputs), _keys.groups(inputs)
    notes += [n for n in m.get("notes") or [] if n not in notes]
    unmeasured += m.get("unknown") or []

    # An input that changed while the run ran (its manifest is from the start).
    changed = []
    for p, h in man["sha256"].items():
        rel = os.path.relpath(p, root).replace(os.sep, "/") if root and p.startswith(root.rstrip("/") + "/") else None
        if rel:
            for g in ("harness", "judge", "plumbing"):
                v = (inputs.get(g) or {}).get(rel)
                if v and v.startswith("sha256:") and v[7:] != h:
                    changed.append(rel)
        elif p.startswith(HOLT_BIN + "/") and (p.endswith(".deb") or p.endswith("/bcachefs")):
            v = (inputs.get("rescuer") or {}).get("bin/" + os.path.basename(p))
            if v and v.startswith("sha256:") and v[7:] != h:
                changed.append(p)
    if man["rustic"] and not loose_rustic:
        v = (inputs.get("bin") or {}).get(f"{plat}/rustic-bko")
        if v and v.startswith("sha256:") and v[7:] != man["rustic"]["sha256"]:
            changed.append(f"{plat}/rustic-bko")
    for p, im in man["images"].items():
        if "/vms/" in p:
            try:
                s = os.stat(p)
                if (s.st_size, int(s.st_mtime), s.st_ino) != (im["size"], im["mtime"], im["inode"]):
                    changed.append(p)
            except OSError:
                changed.append(p)
        elif im.get("sha256_file"):
            name = rescuer_name(p)
            v = (inputs.get("rescuer") or {}).get(name)
            if v and v.startswith("sha256:") and v[7:] != im["sha256_file"]:
                changed.append(p)

    # Verdict, needs.
    verdict, klass, why = classify(rep, kept, a.rc, a.code, attempt, bool((ce or {}).get("flake")),
                                   a.refused, a.lost, (ce or {}).get("rerun_why"))
    # The one-time rescuer copies this run made (ht1-bsd.sh's note): the plan keyed them
    # missing, so holt.run reruns the cell at their sums.
    made = [m.group(1) for m in (re.match(r"made the one-time rescuer (/\S+)", n) for n in rep["notes"]) if m]
    needs = {} if (a.refused or a.lost) else rep["needs"]
    if verdict in ("HARNESS", "LOST") and not a.refused and not a.lost:
        needs = dict(needs, harness="FAIL")

    # Valid: the planned key, nothing changed, everything measured, a plain run.
    invalid = []
    planned = (ce or {}).get("planned_key")
    if planned and planned != key_:
        diff = []
        pg = (ce or {}).get("planned_groups") or {}
        diff = [g for g in sorted(groups_) if pg and pg.get(g) != groups_[g]]
        invalid.append("key differs from the plan's" + (f" ({', '.join(diff)})" if diff else ""))
    if changed:
        invalid.append("changed during the run: " + ", ".join(sorted(set(changed))[:5]))
    if unmeasured:
        invalid.append("not measured: " + ", ".join(unmeasured[:5]))
    dbg = [k for k in DEBUG_ENV if env.get(k)] + [k for k, vs in DEBUG_ENV_VALUES.items() if env.get(k) in vs]
    if dbg:
        invalid.append("set for debugging or tests: " + " ".join(f"{k}={env[k]}" for k in dbg))
    if man["seeded"]:
        invalid.append("its state came from an earlier attempt (" + man["seeded"] + ")")
    # The tree's commit as the run began (its manifest's tree line: the copy's COMMIT, or a
    # synced tree's stamp) against now's: another sync during the run changed what it ran.
    if man["tree"] and commit:
        was, _, wd = man["tree"].partition(".")
        if not (commit.startswith(was) or was.startswith(commit)) or (wd == "dirty") == bool(committed):
            invalid.append(f"the tree changed during the run (tree {man['tree']} at its start, "
                           f"{commit[:7]}{'' if committed else '.dirty'} now)")
    asked = rep["phases"]
    whole = ["source", "rebuild"] if cell.partition("+")[2] in REFUSALS else ALL_PHASES
    if asked is not None and asked != whole:
        invalid.append("phases " + " ".join(asked) + (", not source and rebuild" if whole != ALL_PHASES else ", not all four"))
    elif asked is None and not a.refused and not a.lost:
        invalid.append("the phases it ran are unknown (no HoltTest1 header line in report.txt)")
    if not a.refused and not a.lost and not man["driver"]:
        invalid.append("no logs/manifest.txt")
    valid = not invalid

    start = rep["start"] or (phases[0]["start_utc"] if phases else None) or now
    t0, t1 = _epoch(start), _epoch(now)
    wall = int(t1 - t0) if (t0 is not None and t1 is not None and t1 >= t0) else sum(p["seconds"] or 0 for p in phases)
    peak = max([p["peak_disk_kib"] for p in phases if p["peak_disk_kib"] is not None] or [0]) or None
    anon = max([p["peak_anon_kib"] for p in phases if p["peak_anon_kib"] is not None] or [0]) or None
    host = _host()
    tag = os.environ.get("WEAR_TAG") or f"ht1:{a.run}:{a.gv}"

    # The dumps the run made (G2's re-judge and H1's corpus read them): name -> sha256.
    dumps = {}
    if kdir and os.path.isdir(os.path.join(kdir, "dumps")):
        for n in sorted(os.listdir(os.path.join(kdir, "dumps"))):
            if n.endswith(".json"):
                dumps[n] = _sha(os.path.join(kdir, "dumps", n))
    plan_sha = None
    if root:
        pname = "plan+bigger.txt" if cell.endswith("+bigger") else "plan.txt"
        plan_sha = _sha(os.path.join(root, "holt", "tests", "corpus", guest, pname))

    all_notes = [n for n in ([why] if why else []) + notes + invalid if n]
    # The origin (idempotency id): ht1:RUN:GV:A, as holt.run's backfill makes it too.  A
    # HoltTest1 run's adds its start, since a rerun of RUN/GV by hand is another run (K.N).
    origin = f"ht1:{a.run}:{a.gv}:{attempt}"
    if not ce:
        origin += ":" + re.sub(r"[-:]", "", start)
    row = {
        "v": 1,
        "t": now,
        "start": start,
        "host": host,
        "origin": origin,
        "family": "holt",
        "cell": cell,
        "kind": "run",
        "run": (t2_run or (cells or {}).get("run") or a.run) if ce else a.run,
        "word": ((cells or {}).get("word") or f"HoltTest2 {a.run}") if ce else f"HoltTest1 {a.run}",
        "rule": (ce or {}).get("rule") or "test1",
        "reasons": [_cut(r) for r in ((ce or {}).get("reasons") or [])][:20],
        "attempt": attempt,
        "key": key_,
        "groups": groups_,
        "inputs": inputs,
        "spec": m.get("spec"),
        "planned_key": planned or None,
        "valid": valid,
        "verdict": verdict,
        "class": klass,
        "needs": needs,
        "counts": rep["counts"],
        "fails": rep["fails"],
        "gm": round(wall / 60.0, 2),
        "wall_s": wall,
        "waits_s": 0,
        "wear": {tag: None},
        "ram_peak_kib": peak if (man["mode"] == "ram" or (phases and phases[0].get("mode") == "ram")) else None,
        "seed": None,
        "commit": commit,
        "committed": committed,
        "dirty": dirty,
        "copy": copy_id,
        "logs": f"{host}:{a.k or a.r or ''}",
        "meta": {
            "driver": a.driver,
            "kind": kind,
            "variant": variant,
            # (holt's commit only for a CODE build: a HoltTest1 run's bko-holt is bin/'s, a
            # build by hand from any commit, and the tree's commit isn't its source.)
            "holt": {"code": code, "commit": commit if code else None, "sha256": {plat: used} if used else {},
                     "bin_dir": holt_file or holt_bin or HOLT_BIN, "plan_sha256": plan_sha},
            "bin": {f"{plat}/rustic-bko": {"sha256": (man["rustic"] or {}).get("sha256"), "src": rustic_src,
                                          "set": set_name, "id": (man["rustic"] or {}).get("id"),
                                          "commits": _rustic_commits(set_dir, plat)}},
            "rescuer": {p: im.get("sha256_file") for p, im in man["images"].items() if "/vms/" not in p},
            "round": env.get("HT_ROUND"),
            "facts": facts_by,
            **({"img": _img_meta(plat, facts, hroot, t2_run)} if is_img else {}),
            "tree": man["tree"],
            "version": man["version"],
            "dumps": dumps,
        },
        "extra": {
            "result": rep["result"],
            "kept": kept,
            "rc": a.rc,
            "code": a.code,
            "phases": [[p["phase"], p["seconds"], p["peak_disk_kib"], p["peak_anon_kib"]] for p in phases],
            "peaks": {"disk_kib": peak, "anon_kib": anon, "reserved_gib": man["reserved"], "mode": man["mode"]},
        },
        "via": None,
        "note": _cut("; ".join(all_notes), 1000) if all_notes else None,
    }
    if ce and ce.get("rerun_of"):
        row["extra"]["rerun_of"] = ce["rerun_of"]
    if ce and ce.get("rerun_why"):
        row["extra"]["rerun_why"] = ce["rerun_why"]
    if made:
        row["extra"]["rescuer_made"] = made
    return row


def rescuer_name(path):
    """The rescuer group's name for a manifest image line: the +img pin
    (HOST_ROOT/img-pin/PLAT/rescuer.img) is keyed rescuer/img-PLAT (keys.py's @image), any
    other image rescuer/BASENAME."""
    m = re.search(r"/img-pin/([^/]+)/rescuer\.img\Z", path)
    return f"rescuer/img-{m.group(1)}" if m else "rescuer/" + os.path.basename(path)


def _img_meta(plat, facts, hroot, t2_run):
    """meta.img of a +img row: the pinned image's sha256 and, for a panel of ImgTest2 (run
    t2h-STAMP-img), the kernel of that run's boot-test manifest (HOST_ROOT/runs/t2i-STAMP/img/PLAT)."""
    pin = (((facts.get("images") or {}).get(plat) or {}).get("pin") or {})
    kernel = None
    m = re.fullmatch(r"t2h-(.+)-img(-r\d)?", t2_run or "")
    if m:
        t = _read(os.path.join(hroot, "runs", f"t2i-{m.group(1)}", "img", plat, "manifest.json"))
        try:
            kernel = (json.loads(t) if t else {}).get("kernel")
        except ValueError:
            kernel = None
    return {"plat": plat, "sha256": pin.get("sha256"), "manifest_kernel": kernel}


def backfill_row(host, run, ht1_run, gv, attempt, rc, kept, start, cells_entry=None, word=None, now=None,
                 copy=None, logs=None):
    """The row holt.run appends for a queue job that left no row (R7): LOST when
    it ended by a signal (rc >= 128) or was swept, else HARNESS.  It measured
    nothing, so it's valid: false (it never counts, but the stop rules and the
    report see it).  START: when the job began (the queue's done row: its end
    less its run_s)."""
    ce = cells_entry or {}
    cell = ce.get("cell") or cell_of(gv)
    kept = kept or ""
    if (rc is not None and rc >= 128) or kept.startswith("swept:"):
        verdict, klass = "LOST", "lost"
        why = "the run died (swept)" if kept.startswith("swept:") else f"ended by a signal (exit {rc})"
    else:
        verdict, klass = "HARNESS", "harness"
        why = f"no row from the driver (exit {rc}" + (f"; kept.txt: {_cut(kept, 80)}" if kept else "") + ")"
    planned = ce.get("planned_key")
    now = now or _now()
    copy = copy or {}
    commit = copy.get("commit")
    return {
        "v": 1, "t": now, "start": start or now, "host": host, "origin": f"ht1:{ht1_run}:{gv}:{attempt}",
        "family": "holt", "cell": cell, "kind": "run", "run": run, "word": word or f"HoltTest2 {run}",
        "rule": ce.get("rule") or "test1", "reasons": [_cut(r) for r in (ce.get("reasons") or [])][:20],
        "attempt": attempt, "key": _keys.key(cell, {}), "groups": _keys.groups({}),
        "inputs": {}, "spec": None, "planned_key": planned or None, "valid": False,
        "verdict": verdict, "class": klass, "needs": {"harness": "FAIL"}, "counts": {"PASS": 0, "FAIL": 0, "LIMIT": 0},
        "fails": [], "gm": 0.0, "wall_s": 0, "waits_s": 0, "wear": {f"ht1:{ht1_run}:{gv}": None},
        "ram_peak_kib": None, "seed": None, "commit": commit if commit and re.fullmatch(r"[0-9a-f]{7,40}", commit) else None,
        "committed": False,
        "dirty": None, "copy": copy.get("id") or "unknown", "logs": logs or f"{host}:",
        "meta": {"backfill": True}, "extra": {"rc": rc, "kept": kept or None}, "via": None,
        "note": _cut(why + "; written by holt.run: the driver wrote no row", 1000),
    }


def main(argv=None):   # pragma: no cover - t2-host is the entry point; this is for a look by hand
    row = build(sys.argv[1:] if argv is None else argv)
    print(json.dumps(row, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
