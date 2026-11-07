#!/usr/bin/env python3
"""Judge rocky10-lvm's check: one PASS/FAIL/LIMIT line per need, results.json."""
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
for t in ("restic", "rustic", "rel-restic", "rel-rustic"):
    m = [x for x in rd(f"backup-{t}.log").splitlines() if re.search(r"(?i)warn|error|fail", x)]
    if m: msgs.append(f"{t} backup: {m[:2]}")
line("FAIL" if bad or msgs else "PASS", "backups and restores exit 0 without warnings",
     "; ".join(bad + msgs)[:900] if bad or msgs else "4 backups (2 on noatime, 2 on relatime), 4 restores: exit 0, no warning")
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
# Each tool checks the other's repository.
bad = []
for k, who in (("check-restic-on-rustic", "restic check --read-data of rustic's repository"),
               ("check-rustic-on-restic", "rustic check --read-data of restic's repository")):
    m = [x for x in rd(k + ".log").splitlines() if re.search(r"(?i)^error|\[ERROR\]|\[WARN\]|fatal", x)]
    if status.get(k) != "0" or m: bad.append(f"{who}: exit {status.get(k)} {m[:2]}")
line("FAIL" if bad else "PASS", "each tool checks the other's repository",
     "; ".join(bad)[:900] if bad else "restic check --read-data of rustic's repository and rustic check --read-data of restic's: exit 0, no error")
# Backup leaves the source alone.
sa = js("src-after.cmp.json") or {}
d = sa.get("differences", {})
line("FAIL" if d else "PASS", "backup leaves the source's metadata (atimes) alone", json.dumps(d)[:600] if d else "identical before and after both backups")
def on_relatime(n):  # findmnt TARGET OPTIONS lines: each relatime, none noatime
    ms = [l.split(None, 1) for l in rd(n).splitlines() if l.strip()]
    bad = [m for m in ms if len(m) < 2 or "relatime" not in m[1].split(",") or "noatime" in m[1].split(",")]
    return (len(ms) >= 2 and not bad), f"{n}: {len(ms)} mounts" + (f", not on relatime: {bad[:2]}" if bad else "") + (rd("remount.err")[:200] and "; " + rd("remount.err")[:200])
# Restore over existing.
bad = []
for t in ("restic", "rustic"):
    k = f"over-{t}"
    if status.get(f"restore-{k}") != "0": bad.append(f"{NAMES[k]} exit {status.get('restore-' + k)}: {rd('restore-' + k + '.log')[-300:]!r}")
    dd = diffs.get(k)
    if dd is None: bad.append(f"{NAMES[k]}: no record"); continue
    ks = {kk: tree_paths(ps) for kk, ps in dd.items() if tree_paths(ps) and not (kk == "missing" and tree_paths(ps) == [socket])}
    if ks: bad.append(f"{NAMES[k]}: " + "; ".join(f"{kk} {v[:3]}" for kk, v in ks.items()))
    ok, why = on_relatime(f"mopts-rel-{k}")
    if not ok: bad.append(f"{NAMES[k]}: target not on relatime ({why})")
line("FAIL" if bad else "PASS", "restore over existing immutable/append-only files (and an immutable dir)",
     "; ".join(bad)[:900] if bad else "changed immutable file, immutable dir, appended append-only file: both tools put back contents, xattrs, mode and flags, exit 0 (targets on relatime, as the guest mounts)")
# SELinux mode.
a0, a1 = rd("avc0", "0").strip(), rd("avc1", "0").strip()
env = rd("env.txt").splitlines()
line("PASS" if rd("enforce-after").strip() == "Enforcing" and env and env[0] == "Enforcing" else "FAIL",
     "SELinux enforcing throughout (targeted, MLS)", f"getenforce before/after: {env[0] if env else '?'}/{rd('enforce-after').strip()}; AVC records {a0} -> {a1}")
line("LIMIT", "birth times (statx btime on XFS)", "Linux can't set a birth time: neither tool keeps one on Linux (restic BACKOTTER.md, unix.birthtime)")
# The scratch stack made like the guest's: XFS on a volume like /, XFS on
# a partition like /boot, the volume group like rl_rocky10-lvm, the GPT.
def same(a, b, what):
    x, y = rd(a).split(), rd(b).split()
    return (x == y and x), f"{what}: guest {' '.join(x) or '?'}; scratch {(' '.join(y) or '?') if x != y else 'the same'}"
ok1, d1 = same("feat-root", "feat-src", "xfs_info features")
ok2, d2 = same("mopts-root", "mopts-src", "mount options")
line("PASS" if ok1 and ok2 else "FAIL", "scratch XFS on a logical volume made and mounted like / (rl_rocky10-lvm/root)", f"{d1}; {d2}")
ok1 = rd("geom-boot").strip() and rd("geom-boot").strip() == rd("geom-srcboot").strip()
ok2, d2 = same("mopts-boot", "mopts-srcboot", "mount options")
ok3, d3 = same("feat-boot", "feat-srcboot", "features")
line("PASS" if ok1 and ok2 and ok3 else "FAIL", "scratch XFS on a 1 GiB partition made and mounted like /boot (vda2)",
     ("xfs_info identical (geometry and features: " + " ".join(rd("feat-boot").split()) + ")" if ok1 else
      f"xfs_info differs: {d3}; guest {rd('geom-boot')[:300]!r} scratch {rd('geom-srcboot')[:300]!r}") + f"; {d2}")
ok1, d1 = same("lvm-root", "lvm-src", "PV format, data offset, metadata areas/size; VG extent size, metadata copies, attributes; LV attributes, segment type, stripes, read-ahead, layout")
g, sg = rd("gpt-root").split("\n"), rd("gpt-scratch").split("\n")
types = lambda t: [l.split()[2] for l in t[1:] if len(l.split()) == 3]
ok2 = g[:1] == sg[:1] == ["gpt"] and types(g) and types(sg)[:2] == types(g)[:2] and types(sg)[-1] == types(g)[-1] \
    and [l.split()[:2] for l in g[1:3]] == [l.split()[:2] for l in sg[1:3]]
line("PASS" if ok1 and ok2 else "FAIL", "scratch partitions and volume group made like the guest's (lvm2 and sfdisk defaults)",
     f"{d1}; GPT: guest {' | '.join(x for x in g if x)}; scratch {' | '.join(x for x in sg if x)}")
# The /boot-like partition inside the tree: restored onto the target's own.
BOOT = TREE + "/boot"
nb = sum(1 for p in src if p == BOOT or p.startswith(BOOT + "/"))
bad = []
for d in DIRS:
    if diffs[d] is None: continue
    ks = sorted({k for k, ps in diffs[d].items() for p in ps if p == BOOT or p.startswith(BOOT + "/")})
    if ks: bad.append(f"{NAMES[d]}: {ks}")
    c0, c1 = rd("bootcount-src").strip(), rd(f"bootcount-{d}").strip()
    if not c0 or c0 != c1: bad.append(f"{NAMES[d]}: {c1 or '?'} paths on the target's partition, source {c0 or '?'}")
    if not re.fullmatch(r"/dev/vdb[3-6]", rd(f"bootdev-{d}").strip()): bad.append(f"{NAMES[d]}: grub2 on {rd(f'bootdev-{d}').strip()!r}")
real = [p for p in src if p.startswith(BOOT + "/") and not p[len(BOOT) + 1:].startswith("bko-")]
line("FAIL" if bad or nb < 50 else "PASS", "a second XFS (/boot-like, on a partition) mounted inside the backed-up path",
     "; ".join(bad)[:900] if bad or nb < 50 else
     f"{nb} paths ({len(real)} of them the guest's own /boot files) restored onto each target's own partition, mounted before the restore, in 4 directions; no difference")
# Real examples from the guest's LVM-backed /, /boot and /etc/lvm.
lv = [p for p in src if p.startswith(TREE + "/real/etc-lvm")]
want = [TREE + "/real/etc-lvm/backup", TREE + "/real/etc-lvm/archive", TREE + "/real/etc-lvm/devices/system.devices", TREE + "/real/etc-lvm/lvm.conf"]
miss = [p for p in want if p not in src] + ([BOOT + "/grub2"] if BOOT + "/grub2" not in src else [])
def label_type(e):
    try: return bytes.fromhex((e.get("xattrs") or {})["security.selinux"]).rstrip(b"\0").decode().split(":")[2]
    except Exception: return None
lab = sorted({label_type(src[p]) for p in lv} - {None})
line("FAIL" if miss or status.get("copy-etc-lvm") != "0" else "PASS", "real examples from the guest's LVM-backed /, its /boot and /etc/lvm",
     f"missing {miss}" if miss else f"{len(lv)} paths of /etc/lvm (configuration, the VG's metadata backup and archive, devices file{'; labels ' + ', '.join(lab) if lab else ''}), {len(real)} of /boot, the rest of real/ from /")
# Backups on /'s own relatime leave the atimes alone (and the source and
# its /boot-like partition were really on relatime, not noatime, for them).
relok, reld = on_relatime("mopts-rel-src")
dd = (js("src-rel.cmp.json") or {}).get("differences", {})
other = {k: v for k, v in dd.items() if k != "atime (symlinks)"}
line("FAIL" if other or not relok or js("src-rel.cmp.json") is None else ("LIMIT" if dd else "PASS"),
     "backups on the guest's own mount options (relatime) leave the source's atimes alone",
     f"source not on relatime for them ({reld})" if not relok else
     json.dumps(other)[:600] if other else ("no comparison" if js("src-rel.cmp.json") is None else
     (f"only symlinks' atimes move: readlink(2) has no O_NOATIME (both BACKOTTER.md), source and /boot-like partition on relatime ({json.dumps(dd)[:300]})" if dd
      else "restic and rustic backups with the source and its /boot-like partition on relatime: no atime moved")))
# Every item of the fixture made (a skipped one would pass unseen).
sk = [l.split("skipped: ", 1)[1] for l in rd("fixture.err").splitlines() if "skipped: " in l]
line("FAIL" if sk else "PASS", "fixture made in full", f"harness: skipped {sk[:6]}" if sk else "no fixture item skipped (fixture.err)")
# XFS's own per-file options.  Its preallocated flag (p, 0x2) isn't one of
# a file's options: a dense restore preallocates (S5) and so gives every
# file it, a sparse one none (an accepted difference, both BACKOTTER.md,
# linux.xfs_options).
def nop(l):
    l = re.sub(r"(fsxattr\.xflags = )0x([0-9a-fA-F]+)", lambda m: m.group(1) + hex(int(m.group(2), 16) & ~2), l)
    return re.sub(r"\[(.)p", r"[\1-", l)
sx = [nop(l) for l in rd("src.xfsx").splitlines()]
bad = [f"source: {l}" for l in sx if "fsxattr.xflags" not in l]
for d in DIRS + ["over-restic", "over-rustic"]:
    dx = [nop(l) for l in rd(d + ".xfsx").splitlines()]
    if not dx: bad.append(f"{NAMES[d]}: no record"); continue
    ch = [f"{a} -> {b}" for a, b in zip(sx, dx) if a != b]
    if ch: bad.append(f"{NAMES[d]}: {ch[:4]}")
line("FAIL" if bad or not sx else "PASS", "XFS per-file options (fsxattr: extent size hints and inheritance, nodefrag f, filestream S)",
     "; ".join(bad)[:900] if bad else f"source: {sx}; identical in 4 directions and both restores over existing (but p, preallocated: S5, accepted)")
line("LIMIT", "whole-disk needs (GPT, BIOS boot partition, PV/VG/LV metadata, boot loader)",
     "out of scope: whole-disk (HoltTest1 recreates the disk and the LVM stack); here the scratch stack is made like the guest's and only its file systems' contents are restored")
res = [{"result": v, "need": n, "detail": d} for v, n, d in out]
json.dump(res, open(os.path.join(L, "results-guest.json"), "w"), indent=1)
for v, n, d in out: print(f"{v} {n}: {d}")
