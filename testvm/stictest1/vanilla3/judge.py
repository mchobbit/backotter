#!/usr/bin/env python3
"""Judge vanilla3's check (inner.sh's logs): one PASS/FAIL/LIMIT line per
need, results-guest.json beside them.  The layout (the volume groups, the
pool, the roots' LV names and labels, the partitions' roles) comes from
env.txt's first line, as inner.sh read it from the guest.

What overlayfs keeps in metadata (whiteouts, char 0:0; trusted.overlay.*
xattrs) is judged in its own needs, by archiver: rustic-bko (bko-holt's)
losing any is a FAIL; restic-bko losing some while rustic-bko keeps them
all is a LIMIT (cross-tool fidelity is a courtesy).  The generic
"trusted.* xattrs" need leaves those paths to them."""
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
ESPM, BOOTM = lay.get("ESPM", "/boot/efi"), lay.get("BOOTM", "/boot")
GVR, GVV, PRESL, OTHERL = lay.get("GVR", "vos-root"), lay.get("GVV", "vos-var"), lay.get("PRESL", "vos-b"), lay.get("OTHERL", "vos-a")
PRES, OTHER = lay.get("PRES", "root-b"), lay.get("OTHER", "root-a")
ROLES = [r for r in lay.get("ROLES", "").split(",") if r]
src = js("src.json") or {}
TREE = "up1/up2/tree"
ABOVE = {".", "up1", "up1/up2"}
DIRS = ["rr", "uu", "ru", "ur"]
NAMES = {"rr": "restic->restic", "uu": "rustic->rustic", "ru": "restic->rustic", "ur": "rustic->restic",
         "over-restic": "restic over existing", "over-rustic": "rustic over existing"}
RESTIC_IN = {"rr", "ru", "ur", "over-restic"}   # directions where restic-bko backs up or restores
FSS = f"btrfs {PRESL} and {OTHERL} (thin LVs), btrfs /var, ext4 {BOOTM} and {BOOTM}/init (a linear LV), the ESP (vfat {ESPM})"
# The file systems inside the tree: key -> mount point under TREE.
MOUNTS = {"var": "/var", "future": "/part-future", "boot": BOOTM, "esp": ESPM, "init": BOOTM + "/init"}
def fs_of(p):  # the key of the file system p is on ("root": the present root)
    best, bl = "root", -1
    for k, m in MOUNTS.items():
        q = TREE + m
        if (p == q or p.startswith(q + "/")) and len(q) > bl: best, bl = k, len(q)
    return best
dm = lambda vg, lv: "/dev/mapper/" + vg.replace("-", "--") + "-" + lv.replace("-", "--")
OVLDIRS = [TREE + "/var/lib/abroot/etc", TREE + "/var/lib/abroot/storage", TREE + "/var/real/abroot-etc", TREE + "/var/real/abroot-storage"]
under = lambda p, d: p == d or p.startswith(d + "/")
def is_wh(e): return e and e["type"] == "chardev" and e.get("rdev") == [0, 0]
def ovl_x(e): return {n: v for n, v in (e or {}).get("xattrs", {}).items() if n.startswith("trusted.overlay.")}
OVL = {p for p, e in src.items() if any(under(p, d) for d in OVLDIRS) and (is_wh(e) or ovl_x(e))}
OVLX = {p for p in src if ovl_x(src[p])}
# Whiteouts overlayfs made share one inode with its workdir's (Linux
# reuses one whiteout, linking it): hard-linked char 0:0 nodes.
WHL = {p for p, e in src.items() if is_wh(e) and e.get("links")}
KINDS = {  # need -> fsmeta difference kinds
    "contents, types, sizes, symlink targets, names": ["missing", "extra", "type", "content", "symlink target"],
    "permission bits incl. setuid/setgid/sticky": ["mode"],
    "ownership incl. ids without names": ["owner"],
    "mtime and atime (ns) of files, dirs, symlinks (FAT: 2 s, date-only atimes)": ["mtime", "mtime (dirs)", "mtime (symlinks)", "atime", "atime (dirs)", "atime (symlinks)"],
    "device nodes (incl. rdev 0) and FIFOs": ["device number"],
    "hard links (but whiteouts', judged below)": ["hard links"],
    "file capabilities (security.capability, v2 and v3)": ["capability"],
    "POSIX ACLs access + default (system.posix_acl_*)": ["acl"],
    "user.* xattrs": ["user-xattr"],
    "trusted.* xattrs (but trusted.overlay.*, judged below)": ["trusted-xattr"],
    "other xattrs (security.bko, btrfs.compression)": ["other-xattr", "selinux"],
    "inode flags (i, a, d, A, S; btrfs c, m, C; ext4's D, j, T on /boot and init)": ["inode flags"],
    "sparse files and allocation (S3-S5)": ["holes lost", "holes lost (sub-4K)", "holes gained", "allocation lost"],
}
out = []
def line(v, need, detail): out.append((v, need, detail))
recs = DIRS + ["over-restic", "over-rustic"]
full = {d: js(d + ".json") for d in recs}
from importlib.machinery import SourceFileLoader
fsmeta = SourceFileLoader("fsmeta", os.path.join(os.path.dirname(__file__), "fsmeta.py")).load_module()
diffs = {d: (fsmeta.differences(src, full[d]) if full[d] is not None else None) for d in full}
def tree_paths(ps): return [p for p in ps if p == TREE or p.startswith(TREE + "/")]
sockets = {p for p in src if src[p]["type"] == "socket"}
for need, kinds in KINDS.items():
    bad = []
    for d in DIRS:
        if diffs[d] is None: bad.append(f"{NAMES[d]}: no record"); continue
        for k in kinds:
            ps = [p for p in tree_paths(diffs[d].get(k, [])) if not (k == "missing" and p in sockets) and not (k == "trusted-xattr" and p in OVLX)
                  and not (k == "hard links" and p in WHL)]
            if ps: bad.append(f"{NAMES[d]}: {k} {len(ps)} e.g. {ps[:3]!r}")
    n = sum(1 for p in src if p.startswith(TREE))
    line("FAIL" if bad else "PASS", need, "; ".join(bad)[:900] if bad else f"4 directions, {n} source paths on {FSS}")

# --- overlayfs's metadata, by archiver ---------------------------------------
def by_tool(bad):  # {direction: [problems]} -> verdict, detail
    if bad.get("uu") or bad.get("over-rustic"): return "FAIL"
    return "LIMIT" if any(bad.values()) else "PASS"
def ovl_bad(d, paths):
    a = full.get(d)
    if a is None: return ["no record"]
    b = []
    for p in sorted(paths):
        s, t = src[p], a.get(p)
        if t is None: b.append(f"{p[len(TREE) + 1:]} missing"); continue
        if is_wh(s) and not is_wh(t): b.append(f"{p[len(TREE) + 1:]} not a whiteout ({t['type']} {t.get('rdev')})")
        sx, tx = ovl_x(s), ovl_x(t)
        if sx != tx: b.append(f"{p[len(TREE) + 1:]} trusted.overlay.* {sorted(sx)} -> {sorted(tx)}")
    return b
def tool_line(need, paths, what):
    bad = {d: ovl_bad(d, paths) for d in recs}
    v = by_tool(bad)
    lost = {d: b for d, b in bad.items() if b}
    det = (f"{what}: kept in every direction and over existing" if not lost else
           "; ".join(f"{NAMES[d]}: {len(b)} e.g. {b[:2]}" for d, b in lost.items())[:700]
           + (" (rustic-bko, bko-holt's archiver, loses them)" if v == "FAIL" else
              " (rustic-bko keeps them all: restic-bko's, a cross-tool courtesy, cross-tool-compat-courtesy)"))
    line(v if paths else "FAIL", need, det if paths else "harness: none in the source")
etcu = TREE + "/var/lib/abroot/etc"
up = {p for p in OVL if under(p, etcu)}
xs = {}
for p in up:
    for n in ovl_x(src[p]): xs[n] = xs.get(n, 0) + 1
nwh = sum(1 for p in up if is_wh(src[p]))
tool_line("/etc overlay uppers (made by overlayfs): whiteouts (char 0:0) and trusted.overlay.* xattrs", up,
          f"{nwh} whiteouts, xattrs {', '.join(f'{k[16:]} x{v}' for k, v in sorted(xs.items()))} in {etcu[len(TREE) + 1:]}/{PRESL} and {OTHERL}")
real = {p for p in OVL if under(p, TREE + "/var/real/abroot-etc")}
tool_line("the guest's own /etc uppers (vos-a, vos-b, their workdirs): trusted.overlay.origin, impure, uuid", real,
          f"{len(real)} paths with trusted.overlay.* ({sorted({n[16:] for p in real for n in ovl_x(src[p])})})")
stor = {p for p in OVL if under(p, TREE + "/var/lib/abroot/storage") or under(p, TREE + "/var/real/abroot-storage")}
nsw = sum(1 for p in stor if is_wh(src[p]))
nrw = sum(1 for p in stor if is_wh(src[p]) and under(p, TREE + "/var/real/abroot-storage"))
tool_line("containers storage's whiteouts (char 0:0) and trusted.overlay.* (the guest's, and a layer stack)", stor,
          f"{nsw} whiteouts ({nrw} of them the guest's /var/lib/abroot/storage's), {sum(1 for p in stor if ovl_x(src[p]))} entries with trusted.overlay.*")
# The opaque directory and the redirect, named.
want = {f"{etcu}/{PRESL}/replaced.d": "trusted.overlay.opaque", f"{etcu}/{OTHERL}/replaced.d": "trusted.overlay.opaque",
        f"{etcu}/{PRESL}/newdir": "trusted.overlay.redirect",
        TREE + "/var/lib/abroot/storage/graph/overlay/2222222222222222222222222222222222222222222222222222222222222222/diff/opaque-me": "trusted.overlay.opaque"}
miss = [p for p, n in want.items() if n not in (src.get(p) or {}).get("xattrs", {})]
bad = {d: [f"{p[len(TREE) + 1:]} {n}" for p, n in want.items() if (full.get(d) or {}).get(p, {}).get("xattrs", {}).get(n) != (src.get(p) or {}).get("xattrs", {}).get(n)] for d in recs}
v = by_tool(bad)
line("FAIL" if miss else v, "opaque directories (trusted.overlay.opaque=y) and a redirect (trusted.overlay.redirect)",
     f"harness: not in the source {miss}" if miss else
     ("; ".join(f"{NAMES[d]}: {b}" for d, b in bad.items() if b)[:700] if any(bad.values()) else
      f"{len(want)} kept in every direction and over existing (the restores over existing had replaced.d made plain, newdir's redirect changed)"))
# The whiteouts' hard links: overlayfs treats a whiteout alone and one
# linked to the workdir's the same (the merged views below show it).
bad = {}
for d in recs:
    a = full.get(d)
    bad[d] = ["no record"] if a is None else [p[len(TREE) + 1:] for p in sorted(WHL) if (a.get(p) or {}).get("links") != src[p]["links"]]
lost = {d: b for d, b in bad.items() if b}
line("PASS" if WHL and not lost else ("FAIL" if not WHL else "LIMIT"), "hard-linked whiteouts (overlayfs links each whiteout it makes to its workdir's one)",
     "harness: no hard-linked whiteout in the source" if not WHL else
     (f"{len(WHL)} kept linked in every direction and over existing" if not lost else
      "; ".join(f"{NAMES[d]}: {len(b)} of {len(WHL)} restored unlinked, e.g. {b[:2]}" for d, b in lost.items())[:600]
      + f" ({len(WHL)} in the source; a whiteout is a whiteout to overlayfs whether linked or not: the merged views below are the test)"))
# The merged views: each restore's overlays mounted read-only over its
# restored layers, as the source's were.
def merged_cmp(a, b):
    x, y = js(a), js(b)
    if x is None or y is None: return None
    return {k: v for k, v in fsmeta.differences(x, y).items()}
sem = []
mb = js("merged-src-b.json") or {}
for p, ok in (("deleted.conf", False), ("olddir", False), ("newdir/x.conf", True), ("newdir/y/z.conf", True), ("replaced.d/new.conf", True),
              ("replaced.d/a.conf", False), ("sub/gone.conf", False), ("sub/stay.conf", True), ("added.conf", True), ("keep.conf", True)):
    if (p in mb) != ok: sem.append(f"{p} {'missing' if ok else 'there'}")
if mb.get("chmod.conf", {}).get("mode") != 0o600: sem.append("chmod.conf not 0600")
mc = js("merged-src-c.json") or {}
for p, ok in (("etc/a.conf", True), ("etc/b.conf", False), ("etc/c.conf", True), ("usr/share/doc/x/1", False), ("usr/share/doc/x/2", True),
              ("opaque-me/new1", True), ("opaque-me/old1", False), ("dev/null", True)):
    if (p in mc) != ok: sem.append(f"containers stack: {p} {'missing' if ok else 'there'}")
for x, what in (("b", f"/etc overlay {PRESL}"), ("a", f"/etc overlay {OTHERL}"), ("c", "containers layer stack")):
    bad = {}
    for d in recs:
        c = merged_cmp(f"merged-src-{x}.json", f"merged-{d}-{x}.json")
        bad[d] = ["no merged record (" + rd(f"merged-{d}.err").strip()[:200] + ")"] if c is None else [f"{k} {v[:3]}" for k, v in c.items()]
    n = len(js(f"merged-src-{x}.json") or {})
    v = by_tool(bad)
    if not n or (x != "a" and sem): v = "FAIL"
    line(v, f"merged view of the restored {what} (read-only, upper over lower) equals the source's",
         (f"harness: the source's merged view {'is empty' if not n else 'is wrong: ' + str(sem[:4])} ({rd('merged-src.err').strip()[:200]})" if not n or (x != "a" and sem) else
          "; ".join(f"{NAMES[d]}: {b[:3]}" for d, b in bad.items() if b)[:800] if any(bad.values()) else
          f"{n} merged paths identical in 4 directions and over existing: deleted files stay deleted, opaque directories hide the lower, the redirect shows the renamed lower directory"
          + (" (whiteouts, opaque, the redirect, chmod's copy-up checked in the source's view)" if x == "b" else "")))
bad = {}
for d in ["src"] + DIRS:
    c = merged_cmp("merged-src-b.json", f"mergedrw-{d}-b.json")
    w = rd(f"mergedrw-{d}-write").strip() == "ok"
    # (The source's symlinks' atimes moved since its read-only view was
    # recorded: the backups on relatime read them.)
    b = ["no record: " + rd(f"mergedrw-{d}.err").strip()[:200]] if c is None else [f"{k} {v[:3]}" for k, v in c.items() if not (d == "src" and k == "atime (symlinks)")]
    if not w: b.append("write/delete through it failed: " + rd(f"mergedrw-{d}.err").strip()[:200])
    bad[d] = b
v = "FAIL" if bad["src"] or bad["uu"] else ("LIMIT" if any(bad.values()) else "PASS")
line(v, f"the restored upper mounts read-write as mount-sys mounts /etc (lowerdir, upperdir, workdir) and shows the same view",
     "; ".join(f"{NAMES.get(d, d)}: {b[:3]}" for d, b in bad.items() if b)[:800] if any(bad.values()) else
     f"source and 4 restores (rr and uu after the restores over existing): mounted with stale origin file handles, view identical to the source's read-only one, a write and a delete (a new whiteout) through it; dmesg: {rd('mergedrw-uu.dmesg').strip()[-160:]!r}")

# --- the rest, as the other Linux checks -------------------------------------
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
     "; ".join(bad)[:900] if bad else f"{len(sp)} sparse files marked by both tools, blocks <= source's in {len(DIRS)} restores")
bad = []
for t in nodes:
    rel = {p[len(TREE) + 1:] for p in sockets}
    if any(x.split("\t")[0] in rel for x in nodes[t].splitlines()): bad.append(f"socket in {t} snapshot")
for d in DIRS:
    if full[d] and sockets & set(full[d]): bad.append(f"{NAMES[d]} made a socket")
    if "socket" in rd(f"restore-{d}.log"): bad.append(f"{NAMES[d]} mentions the socket")
for t in ("restic", "rustic"):
    if "socket" in rd(f"backup-{t}.log"): bad.append(f"{t} backup mentions the socket")
line("FAIL" if bad or not sockets else "PASS", "sockets skipped (decision)",
     "; ".join(bad) if bad else (f"{len(sockets)} sockets in the source; in no snapshot, no restore, no message" if sockets else "no socket made"))
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
bad, lim = [], []
for d in DIRS:
    if diffs[d] is None: continue
    ks = sorted({k for k, ps in diffs[d].items() for p in ps if p in ABOVE})
    if not ks: continue
    (bad if d[0] == "r" else lim).append(f"{NAMES[d]}: {ks}")
line("FAIL" if bad else ("LIMIT" if lim else "PASS"), "metadata of the directories above the backed-up path",
     ("; ".join(bad) if bad else "restic snapshots: up1, up1/up2 and /mnt/src restored exactly (owner 1234:5678, 0710/1755, xattrs, ACL, times)"
      + ("; rustic snapshots: " + "; ".join(lim) + " (rustic_core BACKOTTER.md: rustic stores 0755 nodes without owner/times for them)" if lim else "")))
nd = rd("nodes.diff").strip()
n = len(nodes["restic"].splitlines())
line("FAIL" if status.get("nodes") != "0" or n < 50 else "PASS", "the two tools' snapshots are node-identical",
     (nd[:900] if status.get("nodes") != "0" else f"{n} nodes at and below the backed-up path, identical (the overlay xattrs and whiteouts among them)") if n >= 50 else f"only {n} nodes decoded: {rd('nodes-restic.err')[:300]}")
bad = []
for k, who in (("check-restic-on-rustic", "restic check --read-data of rustic's repository"),
               ("check-rustic-on-restic", "rustic check --read-data of restic's repository")):
    m = [x for x in rd(k + ".log").splitlines() if re.search(r"(?i)^error|\[ERROR\]|\[WARN\]|fatal", x)]
    if status.get(k) != "0" or m: bad.append(f"{who}: exit {status.get(k)} {m[:2]}")
line("FAIL" if bad else "PASS", "each tool checks the other's repository",
     "; ".join(bad)[:900] if bad else "restic check --read-data of rustic's repository and rustic check --read-data of restic's: exit 0, no error")
d = (js("src-after.cmp.json") or {}).get("differences", {})
line("FAIL" if d or js("src-after.cmp.json") is None else "PASS", "backup leaves the source's metadata (atimes) alone",
     json.dumps(d)[:600] if d else ("no comparison" if js("src-after.cmp.json") is None else "identical before and after both backups"))
def on_relatime(n):  # findmnt TARGET OPTIONS lines: each relatime, none noatime
    ms = [l.split(None, 1) for l in rd(n).splitlines() if l.strip()]
    bad = [m for m in ms if len(m) < 2 or "relatime" not in m[1].split(",") or "noatime" in m[1].split(",")]
    return (len(ms) >= 6 and not bad), f"{n}: {len(ms)} mounts" + (f", not on relatime: {bad[:2]}" if bad else "") + (rd("remount.err")[:200] and "; " + rd("remount.err")[:200])
def fatcmp(a, b):
    a, b = js(a), js(b)
    if a is None or b is None: return ["no FAT record"]
    h = lambda v: hex(v) if isinstance(v, int) else v
    d = [f"{p} {h(a[p])}->{h(b.get(p))}" for p in sorted(a) if b.get(p) != a[p]]
    return d + [f"{p} not in the source" for p in sorted(set(b) - set(a))]
bad = []
for t in ("restic", "rustic"):
    k = f"over-{t}"
    if status.get(f"restore-{k}") != "0": bad.append(f"{NAMES[k]} exit {status.get('restore-' + k)}: {rd('restore-' + k + '.log')[-300:]!r}")
    dd = diffs.get(k)
    if dd is None: bad.append(f"{NAMES[k]}: no record"); continue
    ks = {kk: [p for p in tree_paths(ps) if not (kk == "hard links" and p in WHL)] for kk, ps in dd.items()
          if tree_paths(ps) and not (kk == "missing" and set(tree_paths(ps)) <= sockets)}
    ks = {kk: v for kk, v in ks.items() if v}
    if ks: bad.append(f"{NAMES[k]}: " + "; ".join(f"{kk} {v[:3]}" for kk, v in ks.items()))
    fd = fatcmp("src.fat.json", f"{k}.fat.json")
    if fd: bad.append(f"{NAMES[k]}: FAT attributes {fd[:4]}")
    ok, why = on_relatime(f"mopts-rel-{k}")
    if not ok: bad.append(f"{NAMES[k]}: target not on relatime ({why})")
    if "upper changed" not in rd(f"{k}.before"): bad.append(f"{NAMES[k]}: the upper wasn't changed first: {rd(k + '.before')[-200:]!r}")
line("FAIL" if bad else "PASS", "restore over existing immutable/append-only files (and an immutable dir), changed FAT attributes and read-only files, a changed overlay upper",
     "; ".join(bad)[:900] if bad else "changed immutable file, immutable dir, appended append-only file on both roots, /var, /boot and init; "
     "changed read-only file, cleared hidden/system/read-only/archive attributes on the ESP; replaced.d made plain, newdir's redirect changed, a whiteout removed: both tools put back contents, xattrs, mode, flags, FAT attributes and the upper, exit 0 (targets on relatime)")
line("LIMIT", "project IDs (and P inheritance)", "Btrfs has no project IDs, and the guest's /boot and init ext4 have no project feature: none is made")
line("LIMIT", "birth times (statx btime on btrfs and ext4)", "Linux can't set a birth time: neither tool keeps one on Linux (restic BACKOTTER.md, unix.birthtime)")
# --- the scratch stack made like the guest's ---------------------------------
def same(a, b, what):
    x, y = rd(a).split(), rd(b).split()
    return bool(x == y and x), f"{what}: guest {' '.join(x) or '?'}; scratch {(' '.join(y) or '?') if x != y else 'the same'}"
oks = [same("feat-root", "feat-src", f"{PRESL}'s btrfs superblock (incompat and compat_ro flags, checksum, node and sector size)"),
       same("feat-other", "feat-srcother", f"{OTHERL}'s"), same("feat-var", "feat-srcvar", "/var's"),
       same("mopts-root", "mopts-src", "mount options of / (but ro, subvolid)"), same("mopts-root", "mopts-srcfuture", f"of {OTHERL} at /part-future (/'s)"),
       same("mopts-var", "mopts-srcvar", "of /var")]
line("PASS" if all(o for o, _ in oks) else "FAIL", f"scratch btrfs made and mounted like {PRESL}, {OTHERL} (no subvolumes) and /var",
     "; ".join(d for _, d in oks)[:900] + f"; {rd('mkfs-btrfs.log').strip()[:200]}")
oks = [same("feat-boot", "feat-srcboot", f"{BOOTM}'s ext4 features"), same("geo-boot", "geo-srcboot", "inode and block size, default mount options, flex_bg, hash"),
       same("mopts-boot", "mopts-srcboot", "mount options"), same("feat-init", "feat-srcinit", "init's ext4 features"), same("geo-init", "geo-srcinit", "init's geometry")]
line("PASS" if all(o for o, _ in oks) else "FAIL", f"scratch ext4 made like {BOOTM} (vos-boot, a partition) and the init LV (vos-init)", "; ".join(d for _, d in oks)[:900])
ok1, d1 = same("fat-esp", "fat-src", "FAT type, sector and cluster size, FATs, reserved sectors")
ok2, d2 = same("mopts-esp", "mopts-srcesp", "mount options")
line("PASS" if ok1 and ok2 else "FAIL", f"scratch FAT32 ESP made and mounted like {ESPM} (vos-efi)", f"{d1}; {d2}")
def lvlines(n): return [l.strip() for l in rd(n).splitlines() if l.strip()]
bad = []
for g, s, what in (("lvm-root", "lvm-srcroot", GVR), ("lvm-var", "lvm-srcvar", GVV)):
    a, b = lvlines(g), lvlines(s)
    if not a or a != b: bad.append(f"{what}: guest {a} scratch {b}")
pool = [l for l in lvlines("lvm-root") if " thin-pool " in f" {l} "]
ratio = [l for l in lvlines("lvm-root") if l.startswith("thin LVs")]
line("FAIL" if bad else "PASS", f"scratch volume groups made like {GVR} and {GVV} (PVs, extents; linear init and var; the thin pool's chunk size, discards, zeroing, lvol0_pmspare; thin LVs over-provisioned as the guest's)",
     "; ".join(bad)[:900] if bad else f"PV, VG and every LV's attributes and segment as the guest's: pool {pool[:1]}; {ratio[:1]}; sizes scaled down (lvsizes-guest, lvsizes-src)")
def parts(n): return [l.split() for l in rd(n).splitlines()[1:] if len(l.split()) == 6]
g, sg = parts("gpt-root"), parts("gpt-scratch")
key = lambda e, r: (e[3], e[4], e[5]) + ((e[2],) if r == "esp" else ())
want = [key(e, r) for e, r in zip(g, ROLES)]
bad = []
if not g or len(ROLES) != len(g): bad.append(f"guest's GPT {len(g)} partitions, roles {ROLES}")
if len(sg) != 5 * len(g) + 1: bad.append(f"scratch has {len(sg)} partitions, not 5 x {len(g)} + 1")
for i in range(5):
    got = [key(e, r) for e, r in zip(sg[i * len(g):(i + 1) * len(g)], ROLES)]
    if got != want: bad.append(f"set {i + 1}: {got} != {want}")
line("FAIL" if bad else "PASS", "scratch GPT made like the guest's (partition names, types, attributes; the ESP's size)",
     "; ".join(bad)[:900] if bad else f"guest {' | '.join(' '.join(e[3:]) for e in g)} (roles {','.join(ROLES)}); scratch the same 5 times, then one PV more")
gl = {l.split()[1]: l.split()[2:] for l in rd("labels-guest").splitlines() if len(l.split()) >= 5}
ml = [l.split() for l in rd("labels-made").splitlines() if l.strip()]
bad = []
glab = sorted((v[0], v[1]) for v in gl.values())
for s in ("src", "rr", "uu", "ru", "ur"):
    got = sorted((x[2], x[3][:-len(s) - 1]) for x in ml if x[0] == s and len(x) >= 5 and x[3].endswith("-" + s))
    if got != glab: bad.append(f"{s}: {got} != {glab}")
uu_ = [x[4] for x in ml if len(x) >= 5] + [v[2] for v in gl.values()]
if len(set(uu_)) != len(uu_): bad.append("UUIDs not all distinct")
line("FAIL" if bad or not gl else "PASS", "file system types and labels made like the guest's (its labels, -SET added), each with its own UUID",
     "; ".join(bad)[:900] if bad else f"{len(ml)} file systems: {', '.join(f'{v[0]} {v[1]}' for v in gl.values())}, each with -SET; {len(uu_)} UUIDs distinct "
     "(files naming the guest's UUIDs, fstab, abroot.cfg, grub.cfg, restored byte for byte above; a file system's own UUID is the restore's, not the archive's)")
def inside(key, name, devre):
    sub = TREE + MOUNTS[key]
    on = lambda p: fs_of(p) == key or p == sub
    nb = sum(1 for p in src if on(p))
    ns = sum(1 for p in sockets if on(p))
    bad = []
    for d in DIRS:
        if diffs[d] is None: continue
        ks = sorted({k for k, ps in diffs[d].items() for p in ps if on(p) and not (k == "missing" and p in sockets) and not (k == "trusted-xattr" and p in OVLX)
                     and not (k == "hard links" and p in WHL)})
        if ks: bad.append(f"{NAMES[d]}: {ks}")
        c0, c1 = rd(f"count-src-{key}").strip(), rd(f"count-{d}-{key}").strip()
        if not c0.isdigit() or str(int(c0) - ns) != c1: bad.append(f"{NAMES[d]}: {c1 or '?'} paths on the target's own, source {c0 or '?'} ({ns} sockets)")
        dv = rd(f"dev-{d}-{key}").strip()
        if not re.fullmatch(devre.replace("SET", d), dv): bad.append(f"{NAMES[d]}: on {dv!r}")
    return ("FAIL" if bad or nb < 30 else "PASS"), ("; ".join(bad)[:900] if bad or nb < 30 else
            f"{nb} paths restored onto each target's own {name}, mounted before the restore, in {len(DIRS)} restores; no difference")
for k, name, devre, need in (
        ("future", f"thin LV {OTHER} (btrfs {OTHERL}-SET)", re.escape(dm(GVR + "-SET", OTHER)).replace("SET", "SET"), f"the other root ({OTHER}, a thin LV of the same pool) mounted inside the backed-up path as /part-future"),
        ("var", "linear LV var (btrfs)", re.escape(dm(GVV + "-SET", "var")), "/var (btrfs on its own volume group) mounted inside the backed-up path"),
        ("boot", "ext4 partition", r"/dev/vdb[0-9]+", f"{BOOTM} (ext4, a partition) mounted inside the backed-up path"),
        ("esp", "ESP (a partition)", r"/dev/vdb[0-9]+", f"the ESP (vfat, a partition) mounted inside {BOOTM} as {ESPM}"),
        ("init", "linear LV init (ext4)", re.escape(dm(GVR + "-SET", "init")), f"the init LV (ext4, linear, beside the pool) mounted inside {BOOTM} as {BOOTM}/init")):
    v, det = inside(k, name, devre.replace(re.escape("SET"), "SET"))
    line(v, need, det)
# The present root (the tree) on its thin LV, every restore.
bad = [f"{NAMES[d]}: on {rd(f'dev-{d}-root').strip()!r}" for d in DIRS if rd(f"dev-{d}-root").strip() != dm(f"{GVR}-{d}", PRES)]
nroot = sum(1 for p in src if p.startswith(TREE) and fs_of(p) == "root")
line("FAIL" if bad else "PASS", f"the present root ({PRES}, a thin LV over-provisioned with {OTHER}) holds the tree",
     "; ".join(bad) if bad else f"{nroot} paths on each target's own {PRES} (btrfs {PRESL}-SET), as their differences above")
bad = []
for d in DIRS:
    fd = fatcmp("src.fat.json", f"{d}.fat.json")
    if fd: bad.append(f"{NAMES[d]}: {len(fd)} differ, e.g. {fd[:4]}")
fa = js("src.fat.json") or {}
line("FAIL" if bad or len(fa) < 30 else "PASS", "FAT attributes (readonly, hidden, system, archive) on files and dirs, the guest's ESP's among them",
     "; ".join(bad)[:900] if bad or len(fa) < 30 else f"{len(fa)} paths, attributes {sorted({hex(v) for v in fa.values() if isinstance(v, int)})}, identical in {len(DIRS)} restores")
R, RF, RV, RB, RI, RE = TREE + "/real", TREE + "/part-future/real", TREE + "/var/real", TREE + BOOTM + "/real/boot", TREE + BOOTM + "/init/real/init", TREE + ESPM + "/real/esp"
want = [f"{R}/usr_share_abroot_abroot.json", f"{R}/etc_abroot_abroot.json", f"{R}/abimage.abr", f"{R}/etc_fstab", f"{R}/etc_os-release",
        f"{R}/usr_bin_passwd", f"{R}/etc_shadow", f"{R}/systemd-system", f"{R}/etc-lvm",
        f"{RF}/abimage.abr", f"{RF}/usr_share_abroot_abroot.json",
        f"{RV}/abroot-etc/{PRESL}", f"{RV}/abroot-etc/{OTHERL}", f"{RV}/abroot-etc/{PRESL}-work", f"{RV}/abroot-storage", f"{RV}/journal",
        f"{RB}/grub/grub.cfg", f"{RI}/{PRESL}/abroot.cfg", f"{RI}/{OTHERL}/abroot.cfg"]
miss = [p for p in want if p not in src]
if not any(p.lower().startswith((RE + "/EFI/vanilla/shimx64.efi").lower()) for p in src): miss.append("the ESP's EFI/vanilla/shimx64.efi")
kern = sorted(p.rsplit("/", 1)[1] for p in src if p.startswith(RI + "/") and "/vmlinuz-" in p)
fut = f"{RB}/grub/grub.cfg.future" in src
nreal = sum(1 for p in src if "/real/" in p + "/" and p.startswith(TREE))
line("FAIL" if miss or nrw < 300 else "PASS", "real examples from the guest's roots (set-id, ABRoot's abroot.json and abimage.abr, fstab), /var (its /etc uppers, storage's whiteouts, the journal), /boot, init and the ESP",
     f"missing {miss}; {nrw} of the guest's storage whiteouts ({rd('real-abroot-storage.txt').strip()})" if miss or nrw < 300 else
     f"{nreal} paths: both roots' ABRoot files, the uppers {OTHERL} and {PRESL} with their workdirs ({rd('real-abroot-etc.txt').strip()}), storage ({rd('real-abroot-storage.txt').strip()}); "
     f"the master grub.cfg{' and grub.cfg.future' if fut else ''}; init's abroot.cfg for both roots and {kern}; the ESP's shim and GRUB")
relok, reld = on_relatime("mopts-rel-src")
dd = (js("src-rel.cmp.json") or {}).get("differences", {})
other = {k: v for k, v in dd.items() if k != "atime (symlinks)"}
line("FAIL" if other or not relok or js("src-rel.cmp.json") is None else ("LIMIT" if dd else "PASS"),
     "backups on relatime (the guest's /, /var and ESP) leave the source's atimes alone",
     f"source not on relatime for them ({reld})" if not relok else
     json.dumps(other)[:600] if other else ("no comparison" if js("src-rel.cmp.json") is None else
     (f"only symlinks' atimes move: readlink(2) has no O_NOATIME (both BACKOTTER.md), source and its mounts on relatime ({json.dumps(dd)[:300]})" if dd
      else "restic and rustic backups with the source and its mounts on relatime: no atime moved")))
sk = [l.split("skipped: ", 1)[1] for l in rd("fixture.err").splitlines() if "skipped: " in l]
line("FAIL" if sk else "PASS", "fixture made in full", f"harness: skipped {sk[:6]}" if sk else "no fixture item skipped (fixture.err)" + (f"; {rd('shims').strip()}" if rd("shims").strip() else ""))
sb = {l.split()[0]: l.split()[1:] for l in rd("src.xfsx").splitlines() if l.strip()}
bad = [] if sb else ["no source listing"]
for d in DIRS + ["over-restic", "over-rustic"]:
    ls = rd(d + ".xfsx").splitlines()
    if not ls: bad.append(f"{NAMES[d]}: no record"); continue
    for l in ls:
        f = l.split()
        if f and sb.get(f[0]) != f[1:]: bad.append(f"{NAMES[d]}: {f[0]} {f[1:]} (source {sb.get(f[0])})")
line("FAIL" if bad else "PASS", "btrfs per-file options: compression property (zstd, zstd:5, lzo, zlib, dir inheritance), c, m, C (nocow, dir inheritance)",
     "; ".join(bad)[:900] if bad else f"{len(sb)} files: flags and btrfs.compression as the source's in {len(DIRS) + 2} restores ({', '.join(k + '=' + '/'.join(v) for k, v in list(sb.items())[:4])}...)")
line("LIMIT", "whole-disk needs (GPT, the LVM stack and its thin pool, file system UUIDs and labels, shim and GRUB with the NVRAM entry)",
     "out of scope: whole-disk (HoltTest1 recreates the disk, both VGs, the thin pool and its thin LVs, every file system with its UUID and label, and boots both roots); here the scratch stack is made like the guest's and only its file systems' contents are restored")
res = [{"result": v, "need": n, "detail": d} for v, n, d in out]
json.dump(res, open(os.path.join(L, "results-guest.json"), "w"), indent=1)
for v, n, d in out: print(f"{v} {n}: {d}")
