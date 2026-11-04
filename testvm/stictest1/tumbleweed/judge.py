#!/usr/bin/env python3
"""Judge tumbleweed's check on one file system: judge.py LOGDIR FS TOPLOGDIR."""
import json, os, re, sys
L, FS, LB = sys.argv[1], sys.argv[2], sys.argv[3]
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
    "trusted.* xattrs": ["trusted-xattr"],
    "other xattrs (security.bko, btrfs.compression)": ["other-xattr"],
    "inode flags (i, a, d, A, S, D; btrfs c, m, C)": ["inode flags"],
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
unl = set()
for need, kinds in KINDS.items():
    if need.startswith("project IDs") and not any(e.get("projid") for p, e in src.items() if p.startswith(TREE)):
        line("LIMIT", need, f"{FS} takes no project ID here (chattr -p fails: btrfs has none; /boot's ext4 has no project feature): " + ("; ".join(l for l in rd("fixture.err").splitlines() if "-p" in l) or "none set"))
        continue
    bad = []
    for d in DIRS:
        if diffs[d] is None: bad.append(f"{NAMES[d]}: no record"); continue
        for k in kinds:
            ps = [p for p in tree_paths(diffs[d].get(k, [])) if not (k == "missing" and p == socket)]
            if k == "selinux":  # an inode the source has without a label (a new btrfs subvolume's root)
                unl.update(p for p in ps if "security.selinux" not in src[p]["xattrs"])
                ps = [p for p in ps if "security.selinux" in src[p]["xattrs"]]
            if ps: bad.append(f"{NAMES[d]}: {k} {len(ps)} e.g. {ps[:3]!r}")
    n = sum(1 for p, e in src.items() if p.startswith(TREE))
    line("FAIL" if bad else "PASS", need, "; ".join(bad)[:900] if bad else f"4 directions, {n} source paths")
if unl:
    line("LIMIT", "an inode without security.selinux (a new btrfs subvolume's root: btrfs labels none)",
         f"{sorted(unl)}: every restore labels it (the kernel labels each new inode under SELinux; removing security.selinux is refused, EACCES); judged under 'exit 0 without warnings'")
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
    bad.append(f"{NAMES[d]}: {ks}")  # since round 16 both tools store them with their real metadata
line("FAIL" if bad else "PASS", "metadata of the directories above the backed-up path",
     "; ".join(bad) if bad else f"up1, up1/up2 and /mnt/{FS}-src restored exactly in 4 directions (owner 1234:5678, 0710/1755, label, xattrs, ACL, times)")
# Snapshot identity.
nd = rd("nodes.diff").strip()
n = len(nodes["restic"].splitlines())
line("FAIL" if status.get("nodes") != "0" or n < 50 else "PASS", "the two tools' snapshots are node-identical",
     ("; ".join(x.strip()[:300] for x in nd.splitlines())[:1200] if status.get("nodes") != "0" else f"{n} nodes at and below the backed-up path, identical") if n >= 50 else f"only {n} nodes decoded: {rd('nodes-restic.err')[:300]}")
# Backup leaves the source alone.
sa = js("src-after.cmp.json") or {}
d = sa.get("differences", {})
line("FAIL" if d else "PASS", "backup leaves the source's metadata (atimes) alone", json.dumps(d)[:600] if d else "identical before and after both backups")
# Restore over existing.
bad = []
for t in ("restic", "rustic"):
    k = f"over-{t}"
    if status.get(f"restore-{k}") != "0": bad.append(f"{NAMES[k]} exit {status.get('restore-' + k)}: {rd('restore-' + k + '.log')[-300:]!r}")
    m = [x for x in rd(f"restore-{k}.log").splitlines() if re.search(r"(?i)warn|error|fail|ignor", x)]
    if m: bad.append(f"{NAMES[k]}: {len(m)} messages: {m[:3]}")
    dd = diffs.get(k)
    if dd is None: bad.append(f"{NAMES[k]}: no record"); continue
    ks = {kk: [p for p in tree_paths(ps) if not (kk == "selinux" and p in unl)] for kk, ps in dd.items()
          if tree_paths(ps) and not (kk == "missing" and tree_paths(ps) == [socket])}
    ks = {kk: v for kk, v in ks.items() if v}
    if ks: bad.append(f"{NAMES[k]}: " + "; ".join(f"{kk} {v[:3]}" for kk, v in ks.items()))
line("FAIL" if bad else "PASS", "restore over existing immutable/append-only files (and an immutable dir)",
     "; ".join(bad)[:900] if bad else "changed immutable file, immutable dir, appended append-only file: both tools put back contents, xattrs, mode and flags, exit 0, no warning")
def rdb(n, d=""):
    try: return open(os.path.join(LB, n), errors="replace").read()
    except OSError: return d
env = rd("env.txt").splitlines()
if FS == "btrfs":
    a0, a1 = rd("avc-fixture", "0").strip(), rd("avc-end", "0").strip()
    eb, ea = rdb("enforce-before").strip(), rdb("enforce-after").strip()
    line("PASS" if eb == ea == "Enforcing" and a0 == a1 else "FAIL", "SELinux enforcing throughout (targeted, MLS), no AVC denial from the tools",
         f"getenforce before/after: {eb}/{ea}; AVC denials in dmesg after the fixture {a0}, after both file systems' backups and restores {a1}")
    line("LIMIT", "birth times (statx btime on btrfs and ext4)", "Linux can't set a birth time: neither tool keeps one on Linux (restic BACKOTTER.md, unix.birthtime)")
    # btrfs per-file options, as lsattr and btrfs.compression show them.
    sb = {l.split()[0]: l.split()[1:] for l in rd("src.btrfs").splitlines() if l.strip()}
    bad = []
    for d in DIRS:
        for l in rd(f"{d}.btrfs").splitlines():
            f = l.split()
            if f and sb.get(f[0]) != f[1:]: bad.append(f"{NAMES[d]}: {f[0]} {f[1:]} (source {sb.get(f[0])})")
    if not sb: bad.append("no source listing")
    line("FAIL" if bad else "PASS", "btrfs per-file options: compression property (zstd, zstd:5, lzo, zlib, dir inheritance), c, m, C (nocow, dir inheritance)",
         "; ".join(bad)[:900] if bad else f"{len(sb)} files: flags and btrfs.compression as the source's in 4 directions ({', '.join(k + '=' + '/'.join(v) for k, v in list(sb.items())[:5])}...)")
    sv = [p for p in src if p == TREE + "/subvol" or p.startswith(TREE + "/subvol/")]
    bad = [f"{NAMES[d]}: {k}" for d in DIRS if diffs[d] for k, ps in diffs[d].items() for p in ps if p in sv and not (k == "selinux" and p in unl)]
    line("FAIL" if bad or not sv else "PASS", "nested btrfs subvolume in the backed-up tree (backed up across, restored as a directory)",
         "; ".join(bad)[:600] if bad else (f"{len(sv)} paths (owner 1234:5678, 0750, user xattr, times) restored in 4 directions" if sv else "no subvolume made"))
line("PASS" if len(env) > 2 and env[1].split() == env[2].split() else "FAIL", f"scratch {FS} made like the guest's ({'/' if FS == 'btrfs' else '/boot'})",
     f"guest: {env[1] if len(env) > 1 else '?'}; scratch: {env[2] if len(env) > 2 else '?'}")
out = [(v, f"[{FS}] {n}", d) for v, n, d in out]
res = [{"result": v, "need": n, "detail": d} for v, n, d in out]
json.dump(res, open(os.path.join(L, "results-guest.json"), "w"), indent=1)
for v, n, d in out: print(f"{v} {n}: " + d.replace("\n", "; "))
