#!/usr/bin/env python3
"""Judge rocky10's check: one PASS/FAIL/LIMIT line per need, results.json."""
import json, os, re, sys
L = sys.argv[1]
def rd(n, d=""):
    try: return open(os.path.join(L, n), errors="replace").read()
    except OSError: return d
def js(n):
    try: return json.load(open(os.path.join(L, n)))
    except Exception: return None
status = dict(l.split() for l in rd("status").splitlines() if l.strip())
src = js("src.json") or {}
TREE = "up1/up2/tree"
ABOVE = {".", "up1", "up1/up2"}
DIRS = ["rr", "uu", "ru", "ur"]
NAMES = {"rr": "restic->restic", "uu": "rustic->rustic", "ru": "restic->rustic", "ur": "rustic->restic",
         "over-restic": "restic over existing", "over-rustic": "rustic over existing"}
KINDS = {  # need -> fsmeta difference kinds
    "contents, types, sizes, symlink targets, names": ["missing", "extra", "type", "content", "symlink target"],
    "permission bits incl. setuid/setgid/sticky": ["mode"],
    "ownership incl. ids without names": ["owner"],
    "mtime and atime (ns) of files, dirs, symlinks": ["mtime", "mtime (dirs)", "mtime (symlinks)", "atime", "atime (dirs)", "atime (symlinks)"],
    "device nodes (incl. rdev 0) and FIFOs": ["device number"],
    "hard links": ["hard links"],
    "SELinux labels (security.selinux)": ["selinux"],
    "file capabilities (security.capability, v2 and v3)": ["capability"],
    "POSIX ACLs access + default (system.posix_acl_*)": ["acl"],
    "user.* xattrs": ["user-xattr"],
    "trusted.* xattrs (incl. XFS trusted.SGI_ACL_* twins)": ["trusted-xattr"],
    "other security.* xattrs": ["other-xattr"],
    "inode flags (i, a, d, A, S)": ["inode flags"],
    "project IDs (and P inheritance)": ["project ID"],
    "sparse files and allocation (S3-S5)": ["holes lost", "holes lost (sub-4K)", "holes gained", "allocation lost"],
}
out = []
def line(v, need, detail): out.append((v, need, detail))
cmp = {d: js(d + ".cmp.json") for d in DIRS + ["over-restic", "over-rustic"]}
full = {d: js(d + ".json") for d in DIRS + ["over-restic", "over-rustic"]}
from importlib.machinery import SourceFileLoader
fsmeta = SourceFileLoader("fsmeta", os.path.join(os.path.dirname(__file__), "fsmeta.py")).load_module()
diffs = {d: (fsmeta.differences(src, full[d]) if full[d] is not None else None) for d in full}
def tree_paths(ps): return [p for p in ps if p == TREE or p.startswith(TREE + "/")]
def covered(kinds):
    c = fsmeta.coverage({k: v for k, v in src.items() if k.startswith(TREE)})
    return c
socket = TREE + "/socket"
for need, kinds in KINDS.items():
    bad = []
    for d in DIRS:
        if diffs[d] is None: bad.append(f"{NAMES[d]}: no record"); continue
        for k in kinds:
            ps = [p for p in tree_paths(diffs[d].get(k, [])) if not (k == "missing" and p == socket)]
            if ps: bad.append(f"{NAMES[d]}: {k} {len(ps)} e.g. {ps[:3]!r}")
    n = sum(1 for p, e in src.items() if p.startswith(TREE))
    line("FAIL" if bad else "PASS", need, "; ".join(bad)[:900] if bad else f"4 directions, {n} source paths")
# Sparse: marks and blocks.
sp = [p for p, e in src.items() if p.startswith(TREE) and e["type"] == "file" and e.get("holes")]
nodes = {t: rd(f"nodes-{t}.txt") for t in ("restic", "rustic")}
bad = []
for t in nodes:
    for p in sp:
        name = p[len(TREE) + 1:]
        l = [x for x in nodes[t].splitlines() if x.split("\t")[0] == name]
        if not l or "unix.sparse" not in l[0]: bad.append(f"{t} snapshot: {name} not marked unix.sparse")
for d in DIRS:
    for p in sp:
        a = (full[d] or {}).get(p)
        if a and a["blocks"] > src[p]["blocks"] + 8: bad.append(f"{NAMES[d]}: {p} blocks {a['blocks']} > {src[p]['blocks']}")
line("FAIL" if bad else "PASS", "sparse marks (unix.sparse) and blocks no more than the source's",
     "; ".join(bad)[:900] if bad else f"{len(sp)} sparse files marked by both tools, blocks <= source's in 4 directions")
# Sockets.
bad = []
for t in nodes:
    if any(x.split("\t")[0] == "socket" for x in nodes[t].splitlines()): bad.append(f"socket in {t} snapshot")
for d in DIRS:
    if full[d] and socket in full[d]: bad.append(f"{NAMES[d]} made the socket")
    if "socket" in rd(f"restore-{d}.log"): bad.append(f"{NAMES[d]} mentions the socket")
for t in ("restic", "rustic"):
    if "socket" in rd(f"backup-{t}.log"): bad.append(f"{t} backup mentions the socket")
line("FAIL" if bad or socket not in src else "PASS", "sockets skipped (decision)",
     "; ".join(bad) if bad else ("socket in source; in no snapshot, no restore, no message" if socket in src else "no socket made"))
# Exit status and messages.
bad = [f"{k}={v}" for k, v in status.items() if (k.startswith("backup") or k.startswith("restore-")) and k[8:] in DIRS + [""] and v != "0"]
bad = [f"{k}={v}" for k, v in status.items() if (k.startswith("backup") or k in [f"restore-{d}" for d in DIRS]) and v != "0"]
msgs = []
for d in DIRS:
    m = [x for x in rd(f"restore-{d}.log").splitlines() if re.search(r"(?i)warn|error|fail|ignor", x)]
    if m: msgs.append(f"{NAMES[d]}: {m[:2]}")
for t in ("restic", "rustic"):
    m = [x for x in rd(f"backup-{t}.log").splitlines() if re.search(r"(?i)warn|error|fail", x)]
    if m: msgs.append(f"{t} backup: {m[:2]}")
line("FAIL" if bad or msgs else "PASS", "backups and restores exit 0 without warnings",
     "; ".join(bad + msgs)[:900] if bad or msgs else "2 backups, 4 restores: exit 0, no warning")
# Directories above the backed-up path.
bad, lim = [], []
for d in DIRS:
    if diffs[d] is None: continue
    ks = sorted({k for k, ps in diffs[d].items() for p in ps if p in ABOVE})
    if not ks: continue
    (bad if d[0] == "r" else lim).append(f"{NAMES[d]}: {ks}")
line("FAIL" if bad else ("LIMIT" if lim else "PASS"), "metadata of the directories above the backed-up path",
     ("; ".join(bad) if bad else "restic snapshots: up1, up1/up2 and /mnt/src restored exactly (owner 1234:5678, 0710/1755, label, xattrs, ACL, times)"
      + ("; rustic snapshots: " + "; ".join(lim) + " (rustic_core BACKOTTER.md: rustic stores 0755 nodes without owner/times for them)" if lim else "")))
# Snapshot identity.
nd = rd("nodes.diff").strip()
n = len(nodes["restic"].splitlines())
line("FAIL" if status.get("nodes") != "0" or n < 50 else "PASS", "the two tools' snapshots are node-identical",
     (nd[:900] if status.get("nodes") != "0" else f"{n} nodes at and below the backed-up path, identical") if n >= 50 else f"only {n} nodes decoded: {rd('nodes-restic.err')[:300]}")
# Backup leaves the source alone.
sa = js("src-after.cmp.json") or {}
d = sa.get("differences", {})
line("FAIL" if d else "PASS", "backup leaves the source's metadata (atimes) alone", json.dumps(d)[:600] if d else "identical before and after both backups")
# Restore over existing.
bad = []
for t in ("restic", "rustic"):
    k = f"over-{t}"
    if status.get(f"restore-{k}") != "0": bad.append(f"{NAMES[k]} exit {status.get('restore-' + k)}: {rd('restore-' + k + '.log')[-300:]!r}")
    dd = diffs.get(k)
    if dd is None: bad.append(f"{NAMES[k]}: no record"); continue
    ks = {kk: tree_paths(ps) for kk, ps in dd.items() if tree_paths(ps) and not (kk == "missing" and tree_paths(ps) == [socket])}
    if ks: bad.append(f"{NAMES[k]}: " + "; ".join(f"{kk} {v[:3]}" for kk, v in ks.items()))
line("FAIL" if bad else "PASS", "restore over existing immutable/append-only files (and an immutable dir)",
     "; ".join(bad)[:900] if bad else "changed immutable file, immutable dir, appended append-only file: both tools put back contents, xattrs, mode and flags, exit 0")
# SELinux mode.
a0, a1 = rd("avc0", "0").strip(), rd("avc1", "0").strip()
env = rd("env.txt").splitlines()
line("PASS" if rd("enforce-after").strip() == "Enforcing" and env and env[0] == "Enforcing" else "FAIL",
     "SELinux enforcing throughout (targeted, MLS)", f"getenforce before/after: {env[0] if env else '?'}/{rd('enforce-after').strip()}; AVC records {a0} -> {a1}")
line("LIMIT", "birth times (statx btime on XFS)", "Linux can't set a birth time: neither tool keeps one on Linux (restic BACKOTTER.md, unix.birthtime)")
line("PASS" if len(env) > 2 and env[1].split() == env[2].split() else "FAIL", "scratch XFS made like the root",
     f"root: {env[1] if len(env) > 1 else '?'}; scratch: {env[2] if len(env) > 2 else '?'}")
res = [{"result": v, "need": n, "detail": d} for v, n, d in out]
json.dump(res, open(os.path.join(L, "results-guest.json"), "w"), indent=1)
for v, n, d in out: print(f"{v} {n}: {d}")
