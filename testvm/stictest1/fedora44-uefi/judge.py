#!/usr/bin/env python3
"""Judge fedora44-uefi's check: one PASS/FAIL/LIMIT line per need, results.json."""
import json, os, re, sys
L0, FS = sys.argv[1], sys.argv[2]
L = os.path.join(L0, FS)
def rd0(n, d=""):
    try: return open(os.path.join(L0, n), errors="replace").read()
    except OSError: return d
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
    "other xattrs (security.bko; btrfs.compression)": ["other-xattr"],
    ("inode flags (i a d A S D C c m)" if FS == "btrfs" else "inode flags (i a d A S D P)"): ["inode flags"],
    "project IDs (and P inheritance)": ["project ID"],
    "sparse files and allocation (S3-S5)": ["holes lost", "holes lost (sub-4K)", "holes gained", "allocation lost"],
}
if FS == "vfat":
    KINDS = {
        "contents, types, sizes, names (long, odd, deep, empty)": ["missing", "extra", "type", "content", "symlink target"],
        "permission bits (the mount's fmask/dmask; readonly)": ["mode"],
        "ownership (the mount's uid/gid)": ["owner"],
        "mtime (2 s) and atime (date) of files and dirs": ["mtime", "mtime (dirs)", "atime", "atime (dirs)"],
        "SELinux labels (the mount's)": ["selinux"],
        "xattrs (none on FAT)": ["user-xattr", "trusted-xattr", "other-xattr", "capability", "acl"],
    }
out = []
def line(v, need, detail): out.append((v, f"[{FS}] {need}", detail))
cmp = {d: js(d + ".cmp.json") for d in DIRS + ["over-restic", "over-rustic"]}
full = {d: js(d + ".json") for d in DIRS + ["over-restic", "over-rustic"]}
from importlib.machinery import SourceFileLoader
fsmeta = SourceFileLoader("fsmeta", os.path.join(os.path.dirname(os.path.abspath(__file__)), "fsmeta.py")).load_module()
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
            ps = [p for p in tree_paths(diffs[d].get(k, [])) if not (k == "missing" and p == socket)
                  and not (k == "selinux" and "security.selinux" not in src[p]["xattrs"])]
            if ps: bad.append(f"{NAMES[d]}: {k} {len(ps)} e.g. {ps[:3]!r}")
    n = sum(1 for p, e in src.items() if p.startswith(TREE))
    if need.startswith("project IDs") and not any(e.get("projid") for e in src.values()):
        line("LIMIT", need, f"{FS} here takes no project ID (chattr -p refused: " + ("btrfs has none" if FS == "btrfs" else "refused") + "); not applicable"); continue
    line("FAIL" if bad else "PASS", need, "; ".join(bad)[:900] if bad else f"4 directions, {n} source paths")
# Nodes without security.selinux (a btrfs subvolume's root: the kernel
# labels none) restored under enforcing: the new file gets the policy's
# label, which no one may remove (an OS limit); the restore must not fail.
unl = FS != "vfat" and [p for p, e in src.items() if p.startswith(TREE) and e["type"] in ("dir", "file") and "security.selinux" not in e["xattrs"]]
if unl:
    bad = []
    for d in DIRS + ["over-restic", "over-rustic"]:
        m = [x for x in rd(f"restore-{d}.log").splitlines() if "security.selinux" in x]
        if m: bad.append(f"{NAMES[d]} exit {status.get('restore-' + d)}: {m[0][:250]}")
    line("FAIL" if bad else "PASS", "restore of nodes without security.selinux (unlabeled btrfs subvolume root) under enforcing",
         ("; ".join(bad)[:900] if bad else "no restore complains") + f" ({len(unl)} such nodes: {unl[:3]}; each comes back with the policy's label, which SELinux lets no one remove: OS limit)")
# Sparse: marks and blocks.
sp = [p for p, e in src.items() if p.startswith(TREE) and e["type"] == "file" and e.get("holes")]
nodes = {t: rd(f"nodes-{t}.txt") for t in ("restic", "rustic")}
bad = []
for t in (nodes if FS != "vfat" else []):
    for p in sp:
        name = p[len(TREE) + 1:]
        l = [x for x in nodes[t].splitlines() if x.split("\t")[0] == name]
        if not l or "unix.sparse" not in l[0]: bad.append(f"{t} snapshot: {name} not marked unix.sparse")
for d in DIRS:
    for p in sp:
        a = (full[d] or {}).get(p)
        if a and a["blocks"] > src[p]["blocks"] + 8: bad.append(f"{NAMES[d]}: {p} blocks {a['blocks']} > {src[p]['blocks']}")
if FS != "vfat": line("FAIL" if bad else "PASS", "sparse marks (unix.sparse) and blocks no more than the source's",
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
if FS != "vfat": line("FAIL" if bad or socket not in src else "PASS", "sockets skipped (decision)",
     "; ".join(bad) if bad else ("socket in source; in no snapshot, no restore, no message" if socket in src else "no socket made"))
# Exit status and messages.
bad = [f"{k}={v}" for k, v in status.items() if (k.startswith("backup") or k.startswith("restore-")) and k[8:] in DIRS + [""] and v != "0"]
bad = [f"{k}={v}" for k, v in status.items() if (k.startswith("backup") or k in [f"restore-{d}" for d in DIRS]) and v != "0"]
msgs = []
# On FAT, security.selinux is the mount's label: a restore can't set it
# there, and each tool warns and exits 0 (restic BACKOTTER.md "Where it
# doesn't support extended attributes ... warns once per file"; rustic_core
# BACKOTTER.md "Metadata the target file system doesn't support").
selx = lambda x: FS == "vfat" and "security.selinux" in x and re.search(r"(?i)not supported", x)
nsel = {}
for d in DIRS:
    ls_ = rd(f"restore-{d}.log").splitlines()
    nsel[d] = sum(1 for x in ls_ if selx(x))
    m = [x for x in ls_ if re.search(r"(?i)warn|error|fail|ignor", x) and not selx(x)]
    if m: msgs.append(f"{NAMES[d]}: {m[:2]}")
for t in ("restic", "rustic"):
    m = [x for x in rd(f"backup-{t}.log").splitlines() if re.search(r"(?i)warn|error|fail", x)]
    if m: msgs.append(f"{t} backup: {m[:2]}")
if FS == "vfat":
    line("LIMIT", "security.selinux (the mount's label) on a FAT restore target",
         "not settable on FAT: warnings, exit 0, as documented: " + ", ".join(f"{NAMES[d]} {n} warnings (exit {status.get('restore-' + d)})" for d, n in nsel.items()))
line("FAIL" if bad or msgs else "PASS", "backups and restores exit 0 without warnings",
     "; ".join(bad + msgs)[:900] if bad or msgs else "2 backups, 4 restores: exit 0, no warning")
# Directories above the backed-up path.
bad, lim = [], []
for d in DIRS:
    if diffs[d] is None: continue
    ks = sorted({k for k, ps in diffs[d].items() for p in ps if p in ABOVE and not (FS == "vfat" and p == "." and "time" in k)})
    if not ks: continue
    # Both tools' snapshots must keep them (restic BACKOTTER.md, rustic
    # BACKOTTER.md: the directories above the backed-up path keep their own
    # metadata): a rustic snapshot's loss is a FAIL too.
    bad.append(f"{NAMES[d]}: {ks}")
line("FAIL" if bad else ("LIMIT" if FS == "vfat" else "PASS"), "metadata of the directories above the backed-up path",
     ("; ".join(bad) if bad else "both tools' snapshots, 4 directions: up1, up1/up2 and /mnt/src restored exactly (owner 1234:5678, 0710/1755, label, xattrs, ACL, times)")
     + ("; FAT's root directory (.) has no times (0): restored as a directory it gets FAT's earliest, 1980-01-01 (OS limit)" if FS == "vfat" else ""))
# Snapshot identity.
nd = rd("nodes.diff").strip()
n = len(nodes["restic"].splitlines())
# The directories above the backed-up path count too (/mnt, the source's
# root, up1, up1/up2): each tool's node of each, compared.
nda = rd("nodes-anc.diff").strip()
na = {t: len(rd(f"nodes-anc-{t}.txt").splitlines()) for t in ("restic", "rustic")}
why = []
if n < 50: why.append(f"only {n} nodes decoded: {rd('nodes-restic.err')[:300]}")
elif status.get("nodes") != "0": why.append(" ".join(nd[:700].split("\n")))
if na["restic"] != 4 or na["rustic"] != 4: why.append(f"ancestors: {na} of 4 nodes decoded: {(rd('nodes-anc-restic.err') + rd('nodes-anc-rustic.err'))[:200]}")
elif status.get("nodes-anc") != "0": why.append("ancestors: " + " ".join(nda[:500].split("\n")))
line("FAIL" if why else "PASS", "the two tools' snapshots are node-identical",
     "; ".join(why)[:900] if why else f"{n} nodes at and below the backed-up path and the 4 above it (/mnt, the source's root, up1, up1/up2), identical")
# Backup leaves the source alone.
sa = js("src-after.cmp.json") or {}
d = sa.get("differences", {})
line("FAIL" if d else "PASS", "backup leaves the source's metadata (atimes) alone", json.dumps(d)[:600] if d else "identical before and after both backups")
# Restore over existing.
bad = []
for t in (("restic", "rustic") if FS != "vfat" else ()):
    k = f"over-{t}"
    if status.get(f"restore-{k}") != "0": bad.append(f"{NAMES[k]} exit {status.get('restore-' + k)}: {rd('restore-' + k + '.log')[-300:]!r}")
    dd = diffs.get(k)
    if dd is None: bad.append(f"{NAMES[k]}: no record"); continue
    # As in the fresh restores: a node with no security.selinux in the
    # source (a nested btrfs subvolume root) gets the policy's label under
    # enforcing (OS limit; its own item, restore of nodes without
    # security.selinux).
    ks = {kk: [p for p in tree_paths(ps) if not (kk == "selinux" and "security.selinux" not in src.get(p, {}).get("xattrs", {}))]
          for kk, ps in dd.items()}
    ks = {kk: v for kk, v in ks.items() if v and not (kk == "missing" and v == [socket])}
    if ks: bad.append(f"{NAMES[k]}: " + "; ".join(f"{kk} {v[:3]}" for kk, v in ks.items()))
if FS != "vfat": line("FAIL" if bad else "PASS", "restore over existing immutable/append-only files (and an immutable dir)",
     "; ".join(bad)[:900] if bad else "changed immutable file, immutable dir, appended append-only file: both tools put back contents, xattrs, mode and flags, exit 0")
# SELinux mode.
a0, a1 = rd0("avc0", "0").strip(), rd0("avc1", "0").strip()
env = rd("env.txt").splitlines()
if FS == "btrfs":
    e0, e1 = rd0("enforce-before").strip(), rd0("enforce-after").strip()
    out.append(("PASS" if e0 == e1 == "Enforcing" else "FAIL", "SELinux enforcing throughout (targeted, MLS)",
                f"getenforce before/after: {e0}/{e1}; AVC records {a0} -> {a1}"))
line("LIMIT", f"birth times (statx btime on {FS})", "Linux can't set a birth time: neither tool keeps one on Linux (restic BACKOTTER.md, unix.birthtime)"
     + ("; FAT's creation time is Linux's ctime, which no one sets" if FS == "vfat" else ""))
line("PASS" if len(env) > 1 and env[0].split() == env[1].split() else "FAIL", "scratch file system made like the guest's " + {"btrfs": "/ (mount options)", "xfs": "/boot (xfs features)", "vfat": "ESP /boot/efi (mount options, FAT type)"}[FS],
     f"guest: {env[0] if env else '?'}; scratch: {env[1] if len(env) > 1 else '?'}")
if FS == "btrfs":
    bad = []
    for d in DIRS + ["over-restic", "over-rustic"]:
        if diffs.get(d) is None: bad.append(f"{NAMES[d]}: no record"); continue
        ps = sorted({(k, p) for k, pp in diffs[d].items() for p in tree_paths(pp)
                     if any(x in p for x in ("/compress", "/nocow", "/subvol", "/real/journal"))
                     and not (k == "selinux" and "security.selinux" not in src[p]["xattrs"])})
        if ps: bad.append(f"{NAMES[d]}: {ps[:4]}")
    opts = [p for p, e in src.items() if any(x in p for x in ("/compress", "/nocow", "/subvol", "/real/journal"))]
    cx = sum(1 for p in opts if "btrfs.compression" in src[p]["xattrs"])
    line("FAIL" if bad else "PASS", "btrfs per-file options (btrfs.compression zstd/lzo/zlib/no, c/m/C flags, nocow file with data, nested subvolume)",
         "; ".join(bad)[:900] if bad else f"{len(opts)} paths ({cx} with btrfs.compression) identical in 4 directions and both restores over existing")
if FS == "vfat":
    def fj(n):
        try: return json.load(open(os.path.join(L, n)))
        except Exception: return None
    sf = fj("src.fat.json") or {}
    sf = {p: v for p, v in sf.items() if p == TREE or p.startswith(TREE + "/")}
    kinds = sorted({v for v in sf.values() if isinstance(v, int)})
    bad = []
    for d in DIRS:
        df = fj(d + ".fat.json")
        if df is None: bad.append(f"{NAMES[d]}: no record"); continue
        ch = [f"{p[len(TREE)+1:] or '.'} {sf[p]:#x}->{df.get(p) if not isinstance(df.get(p), int) else hex(df.get(p))}" for p in sf if df.get(p) != sf[p]]
        if ch: bad.append(f"{NAMES[d]}: {len(ch)} changed, e.g. {ch[:6]}")
    line("FAIL" if bad or not sf else "PASS", "FAT attributes (readonly, hidden, system, archive set and cleared; the guest's ESP's)",
         ("; ".join(bad)[:900] if bad else f"{len(sf)} paths, attributes {[hex(k) for k in kinds]}, identical in 4 directions") if sf else "no FAT attributes recorded")
if FS == "xfs":
    # XFS's preallocated flag (p, 0x2) isn't one of a file's options: a
    # dense restore preallocates (S5) and so gives every file it, a sparse
    # one none (an accepted difference, both BACKOTTER.md, linux.xfs_options).
    def nop(l):
        l = re.sub(r"(fsxattr\.xflags = )0x([0-9a-fA-F]+)", lambda m: m.group(1) + hex(int(m.group(2), 16) & ~2), l)
        return re.sub(r"\[(.)p", r"[\1-", l)
    sx = [nop(l) for l in rd("src.xfsx").splitlines()]
    bad = []
    for d in DIRS:
        dx = [nop(l) for l in rd(d + ".xfsx").splitlines()]
        if not dx: bad.append(f"{NAMES[d]}: no record"); continue
        ch = [f"{a} -> {b}" for a, b in zip(sx, dx) if a != b]
        if ch: bad.append(f"{NAMES[d]}: {ch[:4]}")
    line("FAIL" if bad or not sx else "PASS", "XFS per-file options (fsxattr: extent size hints, nodefrag f, filestream S)",
         "; ".join(bad)[:900] if bad else f"source: {sx}; identical in 4 directions (but p, preallocated: S5, accepted)")
res = [{"result": v, "need": n, "detail": d} for v, n, d in out]
json.dump(res, open(os.path.join(L, "results-guest.json"), "w"), indent=1)
for v, n, d in out: print(f"{v} {n}: {d}")
