#!/usr/bin/env python3
"""Judge uc24's check (inner.sh's logs): one PASS/FAIL/LIMIT line per need,
results-guest.json beside them.  The layout (the partitions' roles by GPT
name, the seed's volume ID) comes from env.txt's first line, as inner.sh
read it from the guest.

The tree is TREE = up1/up2/run/mnt below the source's tmpfs, with the four
roots of Ubuntu Core below it (ubuntu-seed vfat, ubuntu-boot and
ubuntu-save ext4, data ext4 inside LUKS2); everything at and above TREE is
"the directories above" (bko backs up the four roots).  What bko-holt load
does around the files (the LUKS2 header put back, every file system made
with the source's UUID, label and volume ID) is inner.sh's own, as load's
plan does it; the needs below check that the result is the source's."""
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
ROLES = [r for r in lay.get("ROLES", "").split(",") if r]
VOLID = lay.get("VOLID", "?")
src = js("src.json") or {}
TREE = "up1/up2/run/mnt"
ABOVE = {".", "up1", "up1/up2", "up1/up2/run", TREE}
DIRS = ["rr", "uu", "ru", "ur"]
SETS = ["src"] + DIRS
NAMES = {"rr": "restic->restic", "uu": "rustic->rustic", "ru": "restic->rustic", "ur": "rustic->restic",
         "over-restic": "restic over existing", "over-rustic": "rustic over existing"}
RD = {"seed": "ubuntu-seed", "boot": "ubuntu-boot", "save": "ubuntu-save", "data": "data"}
FSS = "vfat ubuntu-seed, ext4 ubuntu-boot and ubuntu-save, ext4 ubuntu-data inside LUKS2 (at /run/mnt as snap-bootstrap mounts them)"
def fs_of(p):  # the role of the file system p is on (None: the tmpfs at and above TREE)
    for k, m in RD.items():
        q = TREE + "/" + m
        if p == q or p.startswith(q + "/"): return k
    return None
KINDS = {  # need -> fsmeta difference kinds
    "contents, types, sizes, symlink targets, names": ["missing", "extra", "type", "content", "symlink target"],
    "permission bits incl. setuid/setgid/sticky": ["mode"],
    "ownership incl. ids without names": ["owner"],
    "mtime and atime (ns) of files, dirs, symlinks (FAT: 2 s, date-only atimes)": ["mtime", "mtime (dirs)", "mtime (symlinks)", "atime", "atime (dirs)", "atime (symlinks)"],
    "device nodes (incl. rdev 0) and FIFOs": ["device number"],
    "hard links": ["hard links"],
    "file capabilities (security.capability, v2 and v3)": ["capability"],
    "POSIX ACLs access + default (system.posix_acl_*)": ["acl"],
    "user.* xattrs": ["user-xattr"],
    "trusted.* xattrs": ["trusted-xattr"],
    "other xattrs (security.bko)": ["other-xattr", "selinux"],
    "inode flags (i, a, d, A, S; ext4's D, j, T on ubuntu-boot, ubuntu-save and ubuntu-data)": ["inode flags"],
    "sparse files and allocation (S3-S5)": ["holes lost", "holes lost (sub-4K)", "holes gained", "allocation lost"],
}
out = []
def line(v, need, detail): out.append((v, need, detail))
recs = DIRS + ["over-restic", "over-rustic"]
full = {d: js(d + ".json") for d in recs}
from importlib.machinery import SourceFileLoader
fsmeta = SourceFileLoader("fsmeta", os.path.join(os.path.dirname(os.path.abspath(__file__)), "fsmeta.py")).load_module()
diffs = {d: (fsmeta.differences(src, full[d]) if full[d] is not None else None) for d in full}
def tree_paths(ps): return [p for p in ps if p.startswith(TREE + "/")]
sockets = {p for p in src if src[p]["type"] == "socket"}
for need, kinds in KINDS.items():
    bad = []
    for d in DIRS:
        if diffs[d] is None: bad.append(f"{NAMES[d]}: no record"); continue
        for k in kinds:
            ps = [p for p in tree_paths(diffs[d].get(k, [])) if not (k == "missing" and p in sockets)]
            if ps: bad.append(f"{NAMES[d]}: {k} {len(ps)} e.g. {ps[:3]!r}")
    n = sum(1 for p in src if p.startswith(TREE + "/"))
    line("FAIL" if bad else "PASS", need, "; ".join(bad)[:900] if bad else f"4 directions, {n} source paths on {FSS}")

# --- squashfs .snap files ----------------------------------------------------
def snaps(n):  # path -> (sha256, unsquashfs -s, listing's sha256)
    return {l.split()[0]: tuple(l.split()[1:4]) for l in rd(f"snaps-{n}.txt").splitlines() if len(l.split()) == 4}
ss = snaps("src")
own = sorted(p for p in ss if not os.path.basename(p).startswith("bko-squash"))
made = sorted(p for p in ss if os.path.basename(p).startswith("bko-squash"))
bad = [f"source: {p} not a squashfs ({v[1]})" for p, v in ss.items() if v[1] != "ok"]
for d in recs:
    t = snaps(d)
    if not t: bad.append(f"{NAMES[d]}: no listing"); continue
    for p, v in ss.items():
        if p not in t: bad.append(f"{NAMES[d]}: {p} missing")
        elif t[p] != v: bad.append(f"{NAMES[d]}: {p} {'sha256 differs' if t[p][0] != v[0] else ''} {'unsquashfs -s ' + t[p][1] if t[p][1] != 'ok' else ''} {'listing differs' if t[p][2] != v[2] else ''}".rstrip())
    for p in set(t) - set(ss): bad.append(f"{NAMES[d]}: {p} not in the source")
nseed = sum(1 for p in ss if p.startswith("./ubuntu-seed/"))
line("FAIL" if bad or not own or len(made) < 4 else "PASS", "squashfs .snap files (the seed's pool, data's snaps) byte-exact and readable (unsquashfs -s, the same listing)",
     "; ".join(bad)[:900] if bad else (f"harness: the guest's snaps {len(own)}, mksquashfs's {len(made)}" if not own or len(made) < 4 else
     f"{len(ss)} .snap files ({len(own)} the guest's, e.g. {[os.path.basename(p) for p in own[:4]]}; {len(made)} made by mksquashfs, xz and gzip; {nseed} on the seed): SHA-256, unsquashfs -s and the listing as the source's in 4 directions and over existing (one appended to, one truncated first)"))

# --- the LUKS2 header kept ---------------------------------------------------
lsrc = js("luks-src.json") or {}
hsrc = rd("hdrsha-src").split()
def luks_cmp(n):  # what of n's header differs from the source's
    x = js(f"luks-{n}.json")
    if x is None: return [f"no header facts ({rd(f'luks-{n}.err').strip()[:160]})"]
    b = [f"{k} {str(lsrc.get(k))[:60]} -> {str(x.get(k))[:60]}" for k in sorted(set(lsrc) | set(x)) if lsrc.get(k) != x.get(k)]
    h = rd(f"hdrsha-{n}").split()
    if not h or h != hsrc: b.append(f"luksHeaderBackup {h[:2]} != the source's {hsrc[:2]}")
    return b
bad = []
if not lsrc or not hsrc: bad.append(f"harness: no source header ({rd('luks-src.err').strip()[:200]}; {rd('luks-new.err').strip()[:200]})")
elif lsrc.get("label") != "ubuntu-data-enc" or len(lsrc.get("keyslots", {})) != 2 or lsrc.get("header_area_bytes") != 6815744:
    bad.append(f"harness: the source's header isn't uc24-fde's shape: label {lsrc.get('label')!r}, {len(lsrc.get('keyslots', {}))} keyslots, header area {lsrc.get('header_area_bytes')}")
for n in DIRS + ["end-" + s for s in SETS]:
    c = luks_cmp(n)
    if c: bad.append(f"{n}: {c[:3]}")
line("FAIL" if bad else "PASS", "the LUKS2 header of ubuntu-data byte-identical after a keep-header restore (luksHeaderRestore, opened by key, no luksFormat), and after the restores and the closes",
     "; ".join(bad)[:900] if bad else
     f"{lsrc.get('header_area_bytes')} bytes (2 x {lsrc.get('hdr_size')} of metadata, {lsrc.get('config', {}).get('keyslots_size')} of keyslots; data at {[s.get('offset') for s in lsrc.get('segments', {}).values()]}): "
     f"SHA-256 {lsrc.get('header_area_sha256', '')[:16]}... and luksHeaderBackup's {hsrc[0][:16]}... the same on 4 targets after their restores and on all 5 sets at the end; UUID {lsrc.get('uuid')}, label {lsrc.get('label')}, seqid {lsrc.get('seqid')} kept")
ttype = rd("token-type").strip()
tsrc = js("token-src.json")
bad = []
if not tsrc: bad.append(f"harness: no token in the source ({rd('token-import.err').strip()[:200]})")
slots = lsrc.get("keyslots", {})
if lsrc and [slots.get(k, {}).get("priority") for k in ("0", "1")] != [2, 1]: bad.append(f"harness: the source's priorities {[(k, v.get('priority')) for k, v in slots.items()]}")
for n in DIRS + ["end-" + s for s in SETS]:
    t = js(f"token-{n}.json")
    if t != tsrc: bad.append(f"{n}: token {str(t)[:80]} (source {str(tsrc)[:60]})")
    for k in (0, 1):
        if status.get(f"luks-slot{k}-{n}") != "0": bad.append(f"{n}: keyslot {k} doesn't open by its key ({status.get(f'luks-slot{k}-{n}')})")
for k in (0, 1):
    if status.get(f"luks-slot{k}-src") != "0": bad.append(f"src: keyslot {k} doesn't open by its key")
line("FAIL" if bad else "PASS", "LUKS2 keyslots (0 preferred, 1 the recovery-key-like one) and the token kept and readable",
     "; ".join(bad)[:900] if bad else
     f"token 0 ({ttype}, keyslots {tsrc.get('keyslots')}) exported the same from 4 targets and all 5 sets at the end; keyslot 0 (priority preferred, argon2i {slots.get('0', {}).get('memory')} KiB) and keyslot 1 (16 bytes, {slots.get('1', {}).get('memory')} KiB) open by their keys on every set"
     + (" (the token typed as our own: cryptsetup's systemd-tpm2 plugin refused the made-up one)" if ttype != "systemd-tpm2" else ""))

# --- labels, UUIDs and the seed's volume ID ----------------------------------
def labs(n):  # SET -> {role: {TYPE, LABEL, UUID, LABEL_FATBOOT}}, "fatid": "VOLID 'LABEL'"
    o = {}
    for l in rd(n).splitlines():
        f = l.split(None, 3)
        if len(f) < 3: continue
        if f[1] == "fatid": o.setdefault(f[0], {})["fatid"] = l.split(None, 2)[2]; continue
        o.setdefault(f[0], {})[f[1]] = dict(kv.split("=", 1) for kv in (f[3] if len(f) > 3 else "").split() if "=" in kv)
    return o
gl, ml = labs("labels-guest").get("guest", {}), labs("labels-made")
bad = []
want = {"seed": ("vfat", gl.get("seed", {}).get("LABEL")), "boot": ("ext4", gl.get("boot", {}).get("LABEL")),
        "save": ("ext4", gl.get("save", {}).get("LABEL")), "inner": ("ext4", gl.get("data", {}).get("LABEL")), "data": ("crypto_LUKS", "ubuntu-data-enc")}
s0 = ml.get("src", {})
for r, (ty, lb) in want.items():
    got = s0.get(r, {})
    if (got.get("TYPE"), got.get("LABEL")) != (ty, lb): bad.append(f"src {r}: {got.get('TYPE')} {got.get('LABEL')!r}, want {ty} {lb!r}")
if s0.get("seed", {}).get("UUID") != VOLID or s0.get("seed", {}).get("LABEL_FATBOOT") != gl.get("seed", {}).get("LABEL_FATBOOT"):
    bad.append(f"src seed: volume ID {s0.get('seed', {}).get('UUID')} LABEL_FATBOOT {s0.get('seed', {}).get('LABEL_FATBOOT')!r}, the guest's {VOLID} {gl.get('seed', {}).get('LABEL_FATBOOT')!r}")
fid = re.match(r"fatid (\S+)", rd("fat-guest").splitlines()[1] if len(rd("fat-guest").splitlines()) > 1 else "")
for s in SETS:
    got = ml.get(s, {})
    if not got: bad.append(f"{s}: not made"); continue
    for r in want:
        if got.get(r) != s0.get(r): bad.append(f"{s} {r}: {got.get(r)} != the source's {s0.get(r)}")
    if got.get("fatid", "").split()[:1] != [VOLID] or (fid and fid[1] != VOLID): bad.append(f"{s}: boot sector's volume ID {got.get('fatid')} (blkid {VOLID}, the guest's boot sector {fid[1] if fid else '?'})")
gu = [v.get("UUID") for r, v in gl.items() if r != "seed"]
su = [s0.get(r, {}).get("UUID") for r in ("boot", "save", "inner")]
if set(gu) & set(su): bad.append("the source's ext4 UUIDs are the guest's own")
line("FAIL" if bad or not gl else "PASS", "labels, UUIDs and the seed's vfat volume ID: the guest's labels and volume ID on the source, the source's on every target (as load makes them)",
     "; ".join(bad)[:900] if bad else
     f"seed vfat {want['seed'][1]!r} volume ID {VOLID} (blkid and the boot sector, LABEL_FATBOOT {s0.get('seed', {}).get('LABEL_FATBOOT')!r}), boot {want['boot'][1]!r}, save {want['save'][1]!r}, LUKS2 {want['data'][1]!r} holding ext4 {want['inner'][1]!r}: "
     f"each UUID and label the same on the 4 targets (mkfs.fat -i, mke2fs -U -L -E hash_seed, the header's LUKS UUID); the source's ext4 UUIDs its own, not the guest's")

# --- the rest, as the other Linux checks -------------------------------------
sp = [p for p, e in src.items() if p.startswith(TREE + "/") and e["type"] == "file" and e.get("holes")]
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
bad += [f"{k}: not run" for k in ["backup-restic", "backup-rustic", "backup-rel-restic", "backup-rel-rustic"] + [f"restore-{d}" for d in DIRS] if k not in status]
msgs = []
for d in DIRS:
    m = [x for x in rd(f"restore-{d}.log").splitlines() if re.search(r"(?i)warn|error|fail|ignor", x)]
    if m: msgs.append(f"{NAMES[d]}: {m[:2]}")
for t in ("restic", "rustic", "rel-restic", "rel-rustic"):
    m = [x for x in rd(f"backup-{t}.log").splitlines() if re.search(r"(?i)warn|error|fail", x)]
    if m: msgs.append(f"{t} backup: {m[:2]}")
line("FAIL" if bad or msgs else "PASS", "backups and restores exit 0 without warnings",
     "; ".join(bad + msgs)[:900] if bad or msgs else "4 backups of the four roots (2 on noatime, 2 on relatime), 4 restores: exit 0, no warning")
bad, lim = [], []
for d in DIRS:
    if diffs[d] is None: continue
    ks = sorted({k for k, ps in diffs[d].items() for p in ps if p in ABOVE})
    if not ks: continue
    (bad if d[0] == "r" else lim).append(f"{NAMES[d]}: {ks}")
bad += [f"{NAMES[d]}: no record" for d in DIRS if diffs[d] is None]
line("FAIL" if bad else ("LIMIT" if lim else "PASS"), "metadata of the directories above the backed-up paths",
     ("; ".join(bad) if bad else "restic snapshots: up1, up1/up2, run, run/mnt (the four roots' parent, as /run/mnt) and the tmpfs's root restored exactly (owner 1234:5678, 0710/1755, xattrs, ACL, times)"
      + ("; rustic snapshots: " + "; ".join(lim) + " (rustic_core BACKOTTER.md: rustic stores 0755 nodes without owner/times for them)" if lim else "")))
nd = rd("nodes.diff").strip()
n = len(nodes["restic"].splitlines())
line("FAIL" if status.get("nodes") != "0" or n < 50 else "PASS", "the two tools' snapshots are node-identical",
     (nd[:900] if status.get("nodes") != "0" else f"{n} nodes at and below run/mnt (the four roots), identical (the .snap files among them)") if n >= 50 else f"only {n} nodes decoded: {rd('nodes-restic.err')[:300]}")
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
    return (len(ms) >= 5 and not bad), f"{n}: {len(ms)} mounts" + (f", not on relatime: {bad[:2]}" if bad else "") + (rd("remount.err")[:200] and "; " + rd("remount.err")[:200])
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
    ks = {kk: tree_paths(ps) for kk, ps in dd.items() if tree_paths(ps) and not (kk == "missing" and set(tree_paths(ps)) <= sockets)}
    if ks: bad.append(f"{NAMES[k]}: " + "; ".join(f"{kk} {v[:3]}" for kk, v in ks.items()))
    fd = fatcmp("src.fat.json", f"{k}.fat.json")
    if fd: bad.append(f"{NAMES[k]}: FAT attributes {fd[:4]}")
    ok, why = on_relatime(f"mopts-rel-{k}")
    if not ok: bad.append(f"{NAMES[k]}: target not on relatime ({why})")
    if "snaps changed" not in rd(f"{k}.before"): bad.append(f"{NAMES[k]}: the snaps weren't changed first: {rd(k + '.before')[-200:]!r}")
line("FAIL" if bad else "PASS", "restore over existing immutable/append-only files (and an immutable dir), changed FAT attributes and read-only files, changed .snap files",
     "; ".join(bad)[:900] if bad else "changed immutable file, immutable dir, appended append-only file on ubuntu-boot, ubuntu-save and ubuntu-data; "
     "changed read-only file, cleared hidden/system/read-only/archive attributes, a truncated .snap on ubuntu-seed; an appended .snap in data: both tools put back contents, xattrs, mode, flags and FAT attributes, exit 0 (targets on relatime)")
line("LIMIT", "project IDs (and P inheritance)", "the guest's ext4 (ubuntu-boot, ubuntu-save, ubuntu-data) have no project feature, and vfat has none: none is made")
line("LIMIT", "birth times (statx btime on ext4 and vfat)", "Linux can't set a birth time: neither tool keeps one on Linux (restic BACKOTTER.md, unix.birthtime)")
# --- the scratch disk made like the guest's ----------------------------------
def same(a, b, what):
    x, y = rd(a).split(), rd(b).split()
    return bool(x == y and x), f"{what}: guest {' '.join(x) or '?'}; {b} {(' '.join(y) or '?') if x != y else 'the same'}"
oks = []
for r in ("boot", "save", "data"):
    for s in SETS:
        oks.append(same(f"feat-guest-{r}", f"feat-{s}-{r}", f"{RD[r]}'s ext4 features"))
        oks.append(same(f"geo-guest-{r}", f"geo-{s}-{r}", f"{RD[r]}'s inode and block size, default mount options, flex_bg, hash"))
for r in ("boot", "save", "data"):
    oks.append(same(f"mopts-guest-{r}", f"mopts-src-{r}", f"{RD[r]}'s mount options"))
okb = [d for o, d in oks if not o]
line("FAIL" if okb else "PASS", "scratch ext4 made and mounted like the guest's ubuntu-boot, ubuntu-save (1 KiB blocks) and ubuntu-data, on the source and every target",
     "; ".join(okb)[:900] if okb else f"{len(oks)} comparisons the same: " + "; ".join(f"{RD[r]} {rd(f'feat-guest-{r}').strip()[:60]}..." for r in ("boot", "save", "data"))[:600])
fg = rd("fat-guest").splitlines()[0].split(None, 1)[1] if rd("fat-guest").strip() else "?"
bad = [f"{s}: {rd(f'fat-{s}').strip()}" for s in SETS if rd(f"fat-{s}").strip() != fg]
ok2, d2 = same("mopts-guest-seed", "mopts-src-seed", "mount options")
line("PASS" if not bad and ok2 and fg != "?" else "FAIL", "scratch ubuntu-seed (vfat, the ESP type) made and mounted like the guest's (FAT type, sector and cluster size, FATs, reserved sectors)",
     ("; ".join(bad) + "; " if bad else f"{fg} on all 5 sets; ") + d2 + f"; {rd('mkfs-vfat.log').strip()[:200]}")
def parts(n): return [l.split() for l in rd(n).splitlines()[1:] if len(l.split()) == 6]
g, sg = parts("gpt-guest"), parts("gpt-scratch")
key = lambda e, r: (e[3], e[4], e[5]) + ((e[2],) if r in ("bios", "save") else ())
want = [key(e, r) for e, r in zip(g, ROLES)]
bad = []
if not g or len(ROLES) != len(g) or sorted(ROLES) != sorted(["bios", "seed", "boot", "save", "data"]): bad.append(f"guest's GPT {len(g)} partitions, roles {ROLES}")
if len(sg) != 5 * len(g) + 1: bad.append(f"scratch has {len(sg)} partitions, not 5 x {len(g)} + 1")
for i in range(5):
    got = [key(e, r) for e, r in zip(sg[i * len(g):(i + 1) * len(g)], ROLES)]
    if got != want: bad.append(f"set {SETS[i]}: {got} != {want}")
line("FAIL" if bad else "PASS", "scratch GPT made like the guest's (the gadget's partition names, types, attributes; BIOS Boot's and ubuntu-save's sizes)",
     "; ".join(bad)[:900] if bad else f"guest {' | '.join(' '.join(e[3:]) for e in g)} (roles {','.join(ROLES)}); scratch the same 5 times, then one ext4 more")
def inside(key, name, devre):
    sub = TREE + "/" + RD[key]
    on = lambda p: fs_of(p) == key
    nb = sum(1 for p in src if on(p))
    ns = sum(1 for p in sockets if on(p))
    bad = []
    for d in DIRS:
        if diffs[d] is None: continue
        ks = sorted({k for k, ps in diffs[d].items() for p in ps if on(p) and not (k == "missing" and p in sockets)})
        if ks: bad.append(f"{NAMES[d]}: {ks}")
        c0, c1 = rd(f"count-src-{key}").strip(), rd(f"count-{d}-{key}").strip()
        if not c0.isdigit() or str(int(c0) - ns) != c1: bad.append(f"{NAMES[d]}: {c1 or '?'} paths on the target's own, source {c0 or '?'} ({ns} sockets)")
        dv = rd(f"dev-{d}-{key}").strip()
        if not re.fullmatch(devre.replace("SET", d), dv): bad.append(f"{NAMES[d]}: on {dv!r}")
    return ("FAIL" if bad or nb < 30 else "PASS"), ("; ".join(bad)[:900] if bad or nb < 30 else
            f"{nb} paths restored onto each target's own {name}, mounted before the restore at {sub[len(TREE) - 7:]}, in {len(DIRS)} restores; no difference")
for k, name, devre in (("seed", "vfat partition", r"/dev/vdb[0-9]+"), ("boot", "ext4 partition", r"/dev/vdb[0-9]+"),
                       ("save", "ext4 partition", r"/dev/vdb[0-9]+"), ("data", "ext4 inside its LUKS2 (the kept header)", r"/dev/mapper/bkst-data-SET")):
    v, det = inside(k, name, devre)
    line(v, f"{RD[k]} restored onto its own file system at snap-bootstrap's mount point", det)
bad = []
for d in DIRS:
    fd = fatcmp("src.fat.json", f"{d}.fat.json")
    if fd: bad.append(f"{NAMES[d]}: {len(fd)} differ, e.g. {fd[:4]}")
fa = js("src.fat.json") or {}
line("FAIL" if bad or len(fa) < 30 else "PASS", "FAT attributes (readonly, hidden, system, archive) on files and dirs, the guest's seed's among them",
     "; ".join(bad)[:900] if bad or len(fa) < 30 else f"{len(fa)} paths, attributes {sorted({hex(v) for v in fa.values() if isinstance(v, int)})}, identical in {len(DIRS)} restores")
SE, BO, SA, DA = (TREE + "/" + RD[r] for r in ("seed", "boot", "save", "data"))
lower = {p.lower(): p for p in src}
want = [f"{BO}/EFI/ubuntu/grubenv", f"{BO}/EFI/ubuntu/kernel.efi", f"{BO}/device/model", f"{SA}/device", f"{DA}/system-data/var/lib/snapd/modeenv",
        f"{DA}/system-data/var/lib/snapd/snaps", f"{DA}/user-data"]
miss = [p for p in want if p not in src]
for p in (f"{SE}/EFI/boot/bootx64.efi", f"{SE}/EFI/ubuntu/grubenv"):
    if p.lower() not in lower: miss.append(p)
systems = sorted({p.split("/")[len(SE.split("/")) + 1] for p in src if p.startswith(SE + "/systems/") and p.endswith("/model")})
if not systems: miss.append(f"{SE}/systems/*/model")
if (src.get(f"{BO}/EFI/ubuntu/kernel.efi") or {}).get("type") != "symlink": miss.append("the run kernel.efi as a symlink")
hello = sorted(p.rsplit("/", 2)[-2] for p in src if re.match(re.escape(DA) + r"/system-data/var/snap/hello/[0-9]+/bko-data", p))
marks = [p for p in (f"{SA}/bko-mark", f"{DA}/bko-mark") if p in src]
line("FAIL" if miss or not hello else "PASS", "real examples from the guest's four roots (the seed's shim, GRUB and recovery systems, boot's grubenv and kernel.efi symlink, save's device/, data's modeenv, snaps and hello's per-revision data)",
     f"missing {miss}{'' if hello else ', hello per-revision data'}" if miss or not hello else
     f"seed: {rd('real-seed.err').strip()[-80:]}, recovery systems {systems}; boot ({rd('real-boot.txt').strip()}); save ({rd('real-save.txt').strip()}); data ({rd('real-data.txt').strip()}), "
     f"hello revisions {hello}, marks {[m.rsplit('/', 2)[-2] for m in marks]}; left out by size: {sum(len(rd(f'real-{r}-left-out.txt').splitlines()) for r in ('seed', 'boot', 'save', 'data'))} files (real-*-left-out.txt)")
relok, reld = on_relatime("mopts-rel-src")
dd = (js("src-rel.cmp.json") or {}).get("differences", {})
other = {k: v for k, v in dd.items() if k != "atime (symlinks)"}
line("FAIL" if other or not relok or js("src-rel.cmp.json") is None else ("LIMIT" if dd else "PASS"),
     "backups on relatime (the guest's four roots) leave the source's atimes alone",
     f"source not on relatime for them ({reld})" if not relok else
     json.dumps(other)[:600] if other else ("no comparison" if js("src-rel.cmp.json") is None else
     (f"only symlinks' atimes move: readlink(2) has no O_NOATIME (both BACKOTTER.md), source and its mounts on relatime ({json.dumps(dd)[:300]})" if dd
      else "restic and rustic backups with the source and its mounts on relatime: no atime moved")))
sk = [l.split("skipped: ", 1)[1] for l in rd("fixture.err").splitlines() if "skipped: " in l]
line("FAIL" if sk else "PASS", "fixture made in full", f"harness: skipped {sk[:6]}" if sk else "no fixture item skipped (fixture.err)" + (f"; {rd('shims').strip()}" if rd("shims").strip() else ""))
line("LIMIT", "whole-disk needs (the GPT's PARTUUIDs, BIOS Boot and the MBR stub, the boot chain, the TPM-sealed keys, recover mode on a new machine)",
     "out of scope: whole-disk (HoltTest1 1U restores uc24 and uc24-fde with bko-holt and boots them: the same machine unattended, a new machine to recover mode, docs/design/ubuntu-core.md); here the gadget's layout is made on a scratch disk and only its four roots' contents are restored, into targets made as load makes them")
res = [{"result": v, "need": n, "detail": d} for v, n, d in out]
json.dump(res, open(os.path.join(L, "results-guest.json"), "w"), indent=1)
for v, n, d in out: print(f"{v} {n}: {d}")
