#!/usr/bin/env python3
"""Judge garuda's check (inner.sh's logs): judge.py LOGDIR.  One
PASS/FAIL/LIMIT line per need, results-guest.json beside them."""
import json, os, re, sys
L = sys.argv[1]
def rd(n, d=""):
    try: return open(os.path.join(L, n), errors="replace").read()
    except OSError: return d
def js(n):
    try: return json.load(open(os.path.join(L, n)))
    except Exception: return None
status = dict(l.split() for l in rd("status").splitlines() if len(l.split()) == 2)
lay = dict(kv.split("=", 1) for kv in rd("env.txt").splitlines()[0].split() if "=" in kv) if rd("env.txt") else {}
ROOTSV, ESPN = lay.get("ROOTSV", "@"), lay.get("ESPN", "boot/efi")
SUBS = [tuple(s.split(":", 1)) for s in lay.get("SUBS", "").split(",") if ":" in s]   # (mount point, subvolume)
src = js("src.json") or {}
TREE = "up1/up2/tree"
ESP = f"{TREE}/{ESPN}"
SNAP = f"{TREE}/.snapshots/1/snapshot"
ABOVE = {".", "up1", "up1/up2"}
DIRS = ["rr", "uu", "ru", "ur"]
NAMES = {"rr": "restic->restic", "uu": "rustic->rustic", "ru": "restic->rustic", "ur": "rustic->restic",
         "over-restic": "restic over existing", "over-rustic": "rustic over existing"}
KINDS = {  # need -> fsmeta difference kinds
    "contents, types, sizes, symlink targets, names": ["missing", "extra", "type", "content", "symlink target"],
    "permission bits incl. setuid/setgid/sticky": ["mode"],
    "ownership incl. ids without names": ["owner"],
    "mtime and atime (ns) of files, dirs, symlinks (FAT: 2 s, date-only atimes)": ["mtime", "mtime (dirs)", "mtime (symlinks)", "atime", "atime (dirs)", "atime (symlinks)"],
    "device nodes (incl. rdev 0) and FIFOs": ["device number"],
    "hard links (the snapshot's files share inode numbers with the live tree's, on a device of their own)": ["hard links"],
    "file capabilities (security.capability, v2 and v3)": ["capability"],
    "POSIX ACLs access + default (system.posix_acl_*)": ["acl"],
    "user.* xattrs": ["user-xattr"],
    "trusted.* xattrs": ["trusted-xattr"],
    "other xattrs (security.bko, btrfs.compression)": ["other-xattr", "selinux"],
    "inode flags (i, a, d, A, S, D; btrfs c, m, C)": ["inode flags"],
    "project IDs": ["project ID"],
    "sparse files and allocation (S3-S5)": ["holes lost", "holes lost (sub-4K)", "holes gained", "allocation lost"],
}
out = []
def line(v, need, detail): out.append((v, need, detail))
recs = DIRS + ["over-restic", "over-rustic"]
full = {d: js(d + ".json") for d in recs}
from importlib.machinery import SourceFileLoader
fsmeta = SourceFileLoader("fsmeta", os.path.join(os.path.dirname(__file__), "fsmeta.py")).load_module()
# btrfs's placeholder directories in the snapshot (inner.sh's
# placeholders: inode 2, where a nested subvolume sits in it).  btrfs stores
# nothing for them: new_simple_dir() makes each in memory with the current
# time, so their times are when they were looked up.  Only their times are
# left out, everywhere (each restore, the backups' atimes, the two
# snapshots' nodes), and named in one LIMIT; their existence, type, mode,
# owner and the rest are judged as any path's.  With none, nothing changes.
PHREL = sorted(l.strip() for l in rd("placeholders").splitlines() if l.strip())
PH = {f"{TREE}/{r}" for r in PHREL}
TIMES = {"mtime", "mtime (dirs)", "mtime (symlinks)", "atime", "atime (dirs)", "atime (symlinks)"}
excused = {}   # where -> the time kinds left out there
def unph(where, dd):
    if dd is None: return None
    res = {}
    for k, ps in dd.items():
        keep = [p for p in ps if not (k in TIMES and p in PH)]
        if len(keep) != len(ps): excused.setdefault(where, set()).add(k)
        if keep: res[k] = keep
    return res
diffs = {d: (unph(NAMES[d], fsmeta.differences(src, full[d])) if full[d] is not None else None) for d in full}
def tree_paths(ps): return [p for p in ps if p == TREE or p.startswith(TREE + "/")]
def under(p, sub): return p == sub or p.startswith(sub + "/")
sockets = {p for p in src if src[p]["type"] == "socket"}
skips = [l.split("skipped: ", 1)[1] for l in rd("fixture.err").splitlines() if "skipped: " in l]
for need, kinds in KINDS.items():
    if need == "project IDs" and not any(e.get("projid") for p, e in src.items() if p.startswith(TREE)):
        line("LIMIT", need, "btrfs and FAT take no project ID (chattr -p refused): " + ("; ".join(s for s in skips if "-p" in s) or "none set"))
        continue
    bad = []
    for d in DIRS:
        if diffs[d] is None: bad.append(f"{NAMES[d]}: no record"); continue
        for k in kinds:
            ps = [p for p in tree_paths(diffs[d].get(k, [])) if not (k == "missing" and p in sockets)]
            if ps: bad.append(f"{NAMES[d]}: {k} {len(ps)} e.g. {ps[:3]!r}")
    n = sum(1 for p in src if p.startswith(TREE))
    line("FAIL" if bad else "PASS", need, "; ".join(bad)[:900] if bad else
         f"4 directions, {n} source paths on btrfs (@, {len(SUBS)} mounted subvolumes, a nested snapshot) and the ESP (vfat /{ESPN})")
# Sparse: marks and blocks.
sp = [p for p, e in src.items() if p.startswith(TREE) and e["type"] == "file" and e.get("holes")]
nodes = {t: rd(f"nodes-{t}.txt") for t in ("restic", "rustic")}
bad = []
for t in nodes:
    names = {x.split("\t")[0]: x for x in nodes[t].splitlines()}
    for p in sp:
        name = p[len(TREE) + 1:]
        if "unix.sparse" not in names.get(name, ""): bad.append(f"{t} snapshot: {name} not marked unix.sparse")
for d in DIRS:
    for p in sp:
        a = (full[d] or {}).get(p)
        if a and a["blocks"] > src[p]["blocks"] + 8: bad.append(f"{NAMES[d]}: {p} blocks {a['blocks']} > {src[p]['blocks']}")
line("FAIL" if bad or not sp else "PASS", "sparse marks (unix.sparse) and blocks no more than the source's",
     "; ".join(bad)[:900] if bad else f"{len(sp)} sparse files (the snapshot's copies among them) marked by both tools, blocks <= source's in 4 directions")
# Sockets (the live tree's and the snapshot's copy of it).
bad = []
rel = {p[len(TREE) + 1:] for p in sockets}
for t in nodes:
    if any(x.split("\t")[0] in rel for x in nodes[t].splitlines()): bad.append(f"socket in {t} snapshot")
for d in DIRS:
    if full[d] and sockets & set(full[d]): bad.append(f"{NAMES[d]} made a socket")
    if "socket" in rd(f"restore-{d}.log"): bad.append(f"{NAMES[d]} mentions the socket")
for t in ("restic", "rustic"):
    if "socket" in rd(f"backup-{t}.log"): bad.append(f"{t} backup mentions the socket")
line("FAIL" if bad or f"{TREE}/socket" not in src else "PASS", "sockets skipped (decision)",
     "; ".join(bad) if bad else (f"{len(sockets)} sockets in the source; in no snapshot, no restore, no message" if sockets else "no socket made"))
# Exit status and messages.
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
bad = []
for d in DIRS:
    if diffs[d] is None: continue
    ks = sorted({k for k, ps in diffs[d].items() for p in ps if p in ABOVE})
    if ks: bad.append(f"{NAMES[d]}: {ks}")
line("FAIL" if bad else "PASS", "metadata of the directories above the backed-up path",
     "; ".join(bad) if bad else "up1, up1/up2 and /mnt/src restored exactly in 4 directions (owner 1234:5678, 0710/1755, xattrs, ACL, times)")
# Snapshot identity.
# rt_node_diff's verdict, and when it found differences, the nodes compared
# again without the placeholders' times (mtime, atime, ctime) alone.
nd = rd("nodes.diff").strip()
n = len(nodes["restic"].splitlines())
def nodemap(t):
    m = {}
    for x in nodes[t].splitlines():
        f = x.split("\t")
        m[f[0]] = {kv.split("=", 1)[0]: kv.split("=", 1)[1] if "=" in kv else "" for kv in f[1:]}
    return m
nbad, nph = [], 0
if status.get("nodes") != "0":
    na, nb = nodemap("restic"), nodemap("rustic")
    for name in sorted(set(na) | set(nb)):
        x, y = dict(na.get(name, {"(none)": ""})), dict(nb.get(name, {"(none)": ""}))
        if name in PHREL:
            t = {k for k in set(x) | set(y) if k in ("mtime", "atime", "ctime") and x.get(k) != y.get(k)}
            if t: nph += 1; excused.setdefault("the two snapshots' nodes", set()).update(t)
            for k in ("mtime", "atime", "ctime"): x.pop(k, None); y.pop(k, None)
        if x != y: nbad.append(f"{name}: " + ", ".join(f"{k} {x.get(k)} vs {y.get(k)}" for k in sorted(set(x) | set(y)) if x.get(k) != y.get(k)))
line("FAIL" if nbad or n < 50 or (status.get("nodes") != "0" and not nph) else "PASS", "the two tools' snapshots are node-identical",
     (f"only {n} nodes decoded: {rd('nodes-restic.err')[:300]}" if n < 50 else
      "; ".join(nbad)[:1200] if nbad else
      "; ".join(x.strip()[:300] for x in nd.splitlines())[:1200] if status.get("nodes") != "0" and not nph else
      f"{n} nodes at and below the backed-up path, identical" + (f" but the times of {nph} placeholder directories (a LIMIT below)" if nph else "")))
# Backups on Garuda's own mounts: btrfs noatime (where nothing moves an
# atime, a symlink's either: no readlink exception) and the ESP's own.
def mounts(n):  # findmnt TARGET FSTYPE OPTIONS lines
    return [l.split(None, 2) for l in rd(n).splitlines() if len(l.split(None, 2)) == 3]
ra, ea = rd("atime-root").strip(), rd("atime-esp").strip()
ms = mounts("mopts-backup")
mbad = [m[0] for m in ms if (m[1] == "btrfs" and ra not in m[2].split(",")) or (m[1] == "vfat" and ea and ea not in m[2].split(","))]
sa = js("src-after.json")
d = unph("the backups (source before and after)", fsmeta.differences(src, sa)) if sa is not None and src else None
line("FAIL" if d or mbad or len(ms) < 2 or d is None or ra != "noatime" else "PASS",
     "backups on the guest's own mounts (btrfs noatime, the ESP's) leave every atime alone, symlinks' too",
     json.dumps(d)[:600] if d else (f"not on the guest's atime options: {mbad[:3]}" if mbad or len(ms) < 2 else
     ("no comparison" if d is None else
      f"the guest's / is {ra or '?'}, not noatime" if ra != "noatime" else
      f"{len(ms)} mounts (btrfs {ra}, the ESP {ea or '?'}): identical before and after both backups, symlinks' atimes too")))
# Restore over existing.
bad = []
for t in ("restic", "rustic"):
    k = f"over-{t}"
    if status.get(f"restore-{k}") != "0": bad.append(f"{NAMES[k]} exit {status.get('restore-' + k)}: {rd('restore-' + k + '.log')[-300:]!r}")
    m = [x for x in rd(f"restore-{k}.log").splitlines() if re.search(r"(?i)warn|error|fail|ignor", x)]
    if m: bad.append(f"{NAMES[k]}: {len(m)} messages: {m[:3]}")
    dd = diffs.get(k)
    if dd is None: bad.append(f"{NAMES[k]}: no record"); continue
    ks = {kk: tree_paths(ps) for kk, ps in dd.items() if tree_paths(ps) and not (kk == "missing" and set(tree_paths(ps)) <= sockets)}
    if ks: bad.append(f"{NAMES[k]}: " + "; ".join(f"{kk} {v[:3]}" for kk, v in ks.items()))
line("FAIL" if bad else "PASS", "restore over existing immutable/append-only files (and an immutable dir)",
     "; ".join(bad)[:900] if bad else "changed immutable file, immutable dir, appended append-only and nocow files, compressed immutable file: both tools put back contents, xattrs, mode and flags, exit 0, no warning")
line("LIMIT", "birth times (statx btime on btrfs and vfat)", "Linux can't set a birth time: neither tool keeps one on Linux (restic BACKOTTER.md, unix.birthtime)")
# btrfs per-file options and how the data is stored: NAME FLAGS PROPERTY ENC/EXTENTS.
def btr(n): return {l.split()[0]: l.split()[1:] for l in rd(n + ".btrfs").splitlines() if len(l.split()) == 4}
sb = btr("src")
bad = []
for d in DIRS:
    db = btr(d)
    if not db: bad.append(f"{NAMES[d]}: no listing"); continue
    for f, v in sb.items():
        if db.get(f, [None, None])[:2] != v[:2]: bad.append(f"{NAMES[d]}: {f} {db.get(f)} (source {v})")
if not sb: bad.append("no source listing")
line("FAIL" if bad else "PASS", "btrfs per-file options (compression property zstd, zstd:5, lzo, zlib, dir inheritance; c, m, C nocow, dir inheritance)",
     "; ".join(bad)[:900] if bad else f"{len(sb)} files: flags and btrfs.compression as the source's in 4 directions ({', '.join(k + '=' + v[0] + '/' + v[1] for k, v in list(sb.items())[:5])}...)")
def enc(v):  # "3/16" -> all, none, some, or ?
    try: e, t = (int(x) for x in v[2].split("/"))
    except Exception: return "?"
    return "none" if e == 0 else ("all" if e == t else "some")
COMPRESSED = ["compress-zstd", "compress-lzo", "compress-zlib", "compress-zstd-level", "compress-dir/inherited",
              "compress-flag", "compress-immutable", "compress-mount"]
PLAIN = ["nocompress", "nocow", "nocow-dir/inherited"]
bad, lim = [], []
for f in COMPRESSED + PLAIN:
    want = "all" if f in COMPRESSED else "none"
    if enc(sb.get(f, [])) != want: bad.append(f"source: {f} {sb.get(f, ['?'] * 3)[2]} encoded extents, not {want}")
for d in DIRS:
    db = btr(d)
    for f in COMPRESSED + PLAIN:
        e = enc(db.get(f, []))
        if e == enc(sb.get(f, [])): continue
        # m (nocompress) is set in the late flag pass (S7), after the data:
        # on a compress=zstd target the data goes in compressed.
        (lim if f == "nocompress" and e != "?" else bad).append(f"{NAMES[d]}: {f} {db.get(f, ['?'] * 3)[2]}")
line("FAIL" if bad else ("LIMIT" if lim else "PASS"), "data stored compressed as the source's (FIEMAP encoded extents) on the compress=zstd targets",
     "; ".join(bad)[:900] if bad else
     (f"the {len(COMPRESSED)} compressed files compressed and nocow ones not in 4 directions; but nocompress (m) restored compressed: "
      f"{'; '.join(lim)[:400]}: the late flag pass (S7, both BACKOTTER.md) sets m after the data is written, and the target is mounted compress=zstd as Garuda's; its contents and its flag m are the source's" if lim else
      f"{len(COMPRESSED)} files compressed ({', '.join(f + ' ' + sb[f][2] for f in COMPRESSED[:3] if f in sb)}...), {len(PLAIN)} not (m, C), in the source and in 4 directions"))
# The file compressed by the mount option alone.
f = "compress-mount"; p = f"{TREE}/{f}"
bad = []
def hexint(x):
    try: return int(x, 16)
    except (TypeError, ValueError): return None
fl = hexint(sb.get(f, [None])[0])
if sb.get(f, ["?", "?"])[1] != "-" or fl is None or fl & 0x4 or enc(sb.get(f, [])) != "all":   # FS_COMPR_FL: c
    bad.append(f"source: {sb.get(f)}: not compressed by the mount alone")
for d in DIRS:
    v = btr(d).get(f)
    if not v or v[1] != "-" or v[0] != sb.get(f, [None])[0] or enc(v) != "all": bad.append(f"{NAMES[d]}: {v}")
    if diffs[d] and any(p in diffs[d].get(k, []) for k in ("content", "missing", "other-xattr", "inode flags")): bad.append(f"{NAMES[d]}: {p} differs")
line("FAIL" if bad or p not in src else "PASS", "a file compressed by the mount's compress=zstd alone, restored compressed with its contents, no property or c flag added",
     "; ".join(bad)[:600] if bad else f"{src[p]['size']} bytes, {sb[f][2]} extents encoded in the source; in 4 directions the same contents, encoded, flags {sb[f][0]}, no btrfs.compression")
# Nested subvolumes: the writable one and snapper's read-only snapshot.
def subv(n): return dict(l.split(None, 1) for l in rd(n + ".subvols").splitlines() if len(l.split()) == 2)
ss = subv("src")
sv = [p for p in src if under(p, TREE + "/subvol")]
bad = [f"{NAMES[d]}: {k}" for d in DIRS if diffs[d] for k, ps in diffs[d].items() for p in ps if p in sv]
bad += [f"{NAMES[d]}: subvol is {subv(d).get('subvol', '?')}" for d in DIRS if subv(d).get("subvol") != "dir"]
line("FAIL" if bad or not sv or ss.get("subvol") != "subvol-rw" else "PASS", "nested btrfs subvolume in the backed-up tree (backed up across, restored as a directory)",
     "; ".join(bad)[:600] if bad else (f"{len(sv)} paths (owner 1234:5678, 0750, user xattr, times) restored as a plain directory in 4 directions" if sv and ss.get("subvol") == "subvol-rw" else f"no subvolume made ({ss.get('subvol')})"))
sn = [p for p in src if under(p, SNAP)]
bad = []
if ss.get(".snapshots/1/snapshot") != "subvol-ro" or ss.get(".snapshots") != "subvol-rw":
    bad.append(f"source: .snapshots {ss.get('.snapshots')}, .snapshots/1/snapshot {ss.get('.snapshots/1/snapshot')}")
live, old = src.get(f"{TREE}/snap-changed", {}), src.get(f"{SNAP}/{TREE}/snap-changed", {})
if not old or live.get("sha256") == old.get("sha256") or f"{SNAP}/{TREE}/snap-deleted" not in src or f"{TREE}/snap-deleted" in src:
    bad.append("source: the snapshot doesn't differ from the live tree (snap-changed, snap-deleted)")
for d in DIRS:
    if diffs[d] is None: continue
    ks = sorted({k for k, ps in diffs[d].items() for p in ps if (under(p, SNAP) or under(p, TREE + "/.snapshots")) and not (k == "missing" and p in sockets)})
    if ks: bad.append(f"{NAMES[d]}: {ks}")
    v = subv(d)
    if v.get(".snapshots/1/snapshot") != "dir" or v.get(".snapshots") != "dir": bad.append(f"{NAMES[d]}: restored as {v}")
line("FAIL" if bad or len(sn) < 100 else "PASS", "a nested, unmounted read-only snapshot of @ (snapper's .snapshots/1/snapshot, as Garuda's) restored as a directory tree with its contents",
     "; ".join(bad)[:900] if bad or len(sn) < 100 else
     f"{len(sn)} paths of the read-only snapshot (a device of its own, its live tree's files changed and deleted since) and .snapshots/1/info.xml "
     "restored as plain directories in 4 directions with every need above; the snapshot itself is bko-holt load's (shared btrfs snapshots)")
# The guest's other subvolumes and its ESP, mounted inside the backed-up
# path: restored onto each target's own.
def cnt(n): return {l.split()[0]: l.split()[1:] for l in rd(n + ".counts").splitlines() if len(l.split()) == 3}
sc = cnt("src")
def inside(m, what):
    sub = f"{TREE}/{m}"
    nb = sum(1 for p in src if under(p, sub))
    bad = []
    for d in DIRS:
        if diffs[d] is None: continue
        ks = sorted({k for k, ps in diffs[d].items() for p in ps if under(p, sub)})
        if ks: bad.append(f"{NAMES[d]}: {ks}")
        c = cnt(d).get(m)
        if not c or not sc.get(m) or c[0] != sc[m][0]: bad.append(f"{NAMES[d]}: {c[0] if c else '?'} paths on the target's own, source {sc.get(m, ['?'])[0]}")
        elif c[1] == sc[m][1] or not c[1].startswith("/dev/vdb"): bad.append(f"{NAMES[d]}: on {c[1]} (source {sc[m][1]})")
    return bad, nb
bad, tot = [], 0
for m, s in SUBS:
    b, nb = inside(m, s); bad += [f"{m} ({s}): {x}" for x in b]; tot += nb
    if not sc.get(m, [None, ""])[1].endswith(f"[/{s}]"): bad.append(f"{m}: source not on {s} ({sc.get(m)})")
line("FAIL" if bad or tot < 6 * len(SUBS) or not SUBS else "PASS", "the guest's other subvolumes (" + ", ".join(s for m, s in SUBS) + ") mounted inside the backed-up path, restored onto each target's own",
     "; ".join(bad)[:900] if bad else f"{tot} paths on {len(SUBS)} subvolumes (their roots with the guest's modes, owners and times) restored onto each target's own, mounted before the restore, in 4 restores; no difference")
b, nb = inside(ESPN, "ESP")
line("FAIL" if b or nb < 30 else "PASS", f"the ESP (vfat, a partition) mounted inside the backed-up path, as /{ESPN}",
     "; ".join(b)[:900] if b or nb < 30 else f"{nb} paths restored onto each target's own ESP partition, mounted before the restore, in 4 restores; no difference")
def fatcmp(a, b):
    a, b = js(a), js(b)
    if a is None or b is None: return ["no FAT record"]
    h = lambda v: hex(v) if isinstance(v, int) else v
    return [f"{p} {h(a[p])}->{h(b.get(p))}" for p in sorted(a) if b.get(p) != a[p]] + [f"{p} not in the source" for p in sorted(set(b) - set(a))]
bad = []
for d in DIRS:
    fd = fatcmp("src.fat.json", f"{d}.fat.json")
    if fd: bad.append(f"{NAMES[d]}: {len(fd)} differ, e.g. {fd[:4]}")
fa = js("src.fat.json") or {}
line("FAIL" if bad or len(fa) < 30 else "PASS", "FAT attributes (readonly, hidden, system, archive) on files and dirs, the guest's ESP's among them",
     "; ".join(bad)[:900] if bad or len(fa) < 30 else f"{len(fa)} paths, attributes {sorted({hex(v) for v in fa.values() if isinstance(v, int)})}, identical in 4 restores")
# Real examples from the guest's own files.
want = [f"{TREE}/real/{f}" for f in ("passwd", "su", "mount", "shadow", "fstab", "systemd", "snapper-root")]
want += [f"{TREE}/boot/grub/grub.cfg", f"{TREE}/var/log/journal", f"{ESP}/real/esp/EFI"]
miss = [p for p in want if p not in src]
nreal = sum(1 for p in src if under(p, f"{TREE}/real") or under(p, f"{ESP}/real") or under(p, f"{TREE}/boot/grub")
            or under(p, f"{TREE}/var/log/journal") or (p.startswith(f"{TREE}/") and p.split("/")[3] in ("root", "srv") and "bko-" not in p))
line("FAIL" if miss else "PASS", "real examples from the guest's / (set-id, shadow, snapper, pacman, GRUB), its subvolumes and ESP",
     f"missing {miss}" if miss else f"{nreal} paths: set-id passwd/su/mount, shadow, fstab, snapper's root config"
     + (" and a snapshot's info.xml" if f"{TREE}/real/snapper-info.xml" in src else "") + ", /boot/grub, the journal on @log, /etc/skel on @root, /srv on @srv, the ESP's loaders")
sk = [s for s in skips if "chattr -p" not in s]
line("FAIL" if sk else "PASS", "fixture made in full", f"harness: skipped {sk[:6]}" if sk else "no fixture item skipped but chattr -p (fixture.err)")
# The scratch disk made like the guest's.
def sup(n):
    try: return json.loads(rd(n))
    except Exception: return {}
g, s = sup("super-root"), sup("super-src")
C = 0x8 | 0x10   # COMPRESS_LZO, COMPRESS_ZSTD: set by the data written, not by mkfs
like = lambda x: (None if x.get("incompat") is None else x["incompat"] & ~C, x.get("compat_ro"), x.get("csum"), x.get("nodesize"), x.get("sectorsize"))
okf = bool(g) and like(g) == like(s) and bool((g.get("incompat") or 0) & 0x10) and bool((s.get("incompat") or 0) & 0x10)
want = sorted([ROOTSV] + [s_ for m, s_ in SUBS])
have = sorted(l.strip() for l in rd("subvols-src").splitlines() if l.strip())
mr, ms_ = rd("mopts-root").strip(), rd("mopts-src").strip()
sr, ss_ = rd("mopts-subs-root").strip().splitlines(), rd("mopts-subs-src").strip().splitlines()
garuda = {"noatime", "compress=zstd:3"} <= set(mr.split(",")) or {"noatime", "compress=zstd"} <= set(mr.split(","))
ok = okf and want == have and mr == ms_ and sr == ss_ and garuda
line("PASS" if ok else "FAIL", "scratch btrfs made and mounted like / (features, checksum, node size; @ and the subvolumes; noatime,compress=zstd)",
     f"superblock: guest {g.get('incompat_names')} {g.get('compat_ro_names')} {g.get('csum')} {g.get('nodesize')}/{g.get('sectorsize')}"
     + (f"; scratch {s.get('incompat_names')} {s.get('compat_ro_names')} {s.get('csum')} {s.get('nodesize')}/{s.get('sectorsize')}" if like(g) != like(s) else "; scratch the same (COMPRESS_ZSTD in both; COMPRESS_* aside, which the data written sets)")
     + f"; subvolumes {have}" + (f" (the guest's {want})" if want != have else "")
     + f"; / mounted {mr}" + (f", scratch {ms_}" if mr != ms_ else "") + ("" if sr == ss_ else f"; subvolumes mounted {sr} vs {ss_}")
     + ("" if garuda else "; the guest's / isn't noatime,compress=zstd") + (f"; {rd('mkfs-btrfs.log').strip()[:200]}" if rd("mkfs-btrfs.log").strip() else ""))
fe, fs_ = rd("fat-esp").strip(), rd("fat-src").strip()
me, mse = rd("mopts-esp").strip(), rd("mopts-srcesp").strip()
line("PASS" if fe and fe == fs_ and me == mse else "FAIL", f"scratch FAT ESP made and mounted like /{ESPN}",
     f"FAT type, sector and cluster size, FATs, reserved sectors: guest {fe or '?'}" + (f", scratch {fs_}" if fe != fs_ else ", the same")
     + f"; mount options {me or '?'}" + (f", scratch {mse}" if me != mse else ", the same"))
gp, sg = [l for l in rd("gpt-root").splitlines() if l], [l for l in rd("gpt-scratch").splitlines() if l]
types = lambda t: [l.split()[2] for l in t[1:] if len(l.split()) == 3]
gt, st_ = types(gp), types(sg)
ESPT = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
ok = gp[:1] == sg[:1] == ["gpt"] and gt and st_ and gt[0] == st_[0] == ESPT and gp[1].split()[1] == sg[1].split()[1] and set(st_) == set(gt)
line("PASS" if ok else "FAIL", "scratch partitions made like the guest's (GPT, ESPs of the guest's ESP's size, its / partition's type)",
     f"GPT: guest {' | '.join(gp)}; scratch {' | '.join(sg)}")
def rel(t, base):
    for b in (base + "/mnt/src", base):
        if t == b or t.startswith(b + "/"): return t[len(b):]
    return t
norm = lambda o: ",".join(x for x in o.split(",") if not x.startswith(("subvolid=", "subvol=")))
sm = {rel(m[0], "/mnt/src"): (m[1], norm(m[2])) for m in mounts("mopts-backup")}
bad = []
for d in DIRS:
    tm = {rel(m[0], f"/mnt/d{d}"): (m[1], norm(m[2])) for m in mounts(f"mopts-{d}")}
    if tm != sm: bad.append(f"{NAMES[d]}: " + "; ".join(f"{k} {tm.get(k)} (source {sm.get(k)})" for k in sorted(set(tm) | set(sm)) if tm.get(k) != sm.get(k))[:300])
line("FAIL" if bad or len(sm) < 2 + len(SUBS) else "PASS", "restore targets made and mounted as the source (@, the subvolumes, the ESP; the guest's options)",
     "; ".join(bad)[:900] if bad else f"{len(sm)} mounts per target, each as the source's: {sorted({v[1] for v in sm.values()})}")
line("LIMIT", "whole-disk needs (GPT, the ESP's loaders, btrfs subvolumes and snapshots as such, GRUB)",
     "out of scope: whole-disk (HoltTest1 recreates the GPT, the file systems, the subvolumes and snapper's read-only snapshots as such, and GRUB); here the scratch disk is made like the guest's and only its file systems' contents are restored")
if PH:
    np_ = sum(1 for p in PH if p in src)
    line("LIMIT", "times of btrfs's placeholder directories in a snapshot (where a nested subvolume sits)",
         f"{len(PH)} placeholders ({', '.join(PHREL[:4])}; {np_} in the record), inode 2 on btrfs: btrfs stores nothing for them, "
         "new_simple_dir() makes each in memory with the current time, so their times are when they were looked up and no archiver can keep them; "
         "only their mtime, atime and ctime are left out, their existence, type, mode and owner are judged; left out in "
         + ("; ".join(f"{w} ({', '.join(sorted(k))})" for w, k in excused.items()) or "nothing (no time differed)"))
res = [{"result": v, "need": n, "detail": d} for v, n, d in out]
json.dump(res, open(os.path.join(L, "results-guest.json"), "w"), indent=1)
for v, n, d in out: print(f"{v} {n}: " + d.replace("\n", "; "))
