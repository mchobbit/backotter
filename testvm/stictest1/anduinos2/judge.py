#!/usr/bin/env python3
"""Judge anduinos2's check (inner.sh's logs): judge.py LOGDIR.  One
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
src = js("src.json") or {}
TREE = "up1/up2/tree"
ESP = f"{TREE}/boot/efi"
ABOVE = {".", "up1", "up1/up2"}
DIRS = ["rr", "uu", "ru", "ur"]
NAMES = {"rr": "restic->restic", "uu": "rustic->rustic", "ru": "restic->rustic", "ur": "rustic->restic",
         "over-restic": "restic over existing", "over-rustic": "rustic over existing"}
KINDS = {  # need -> fsmeta difference kinds
    "contents, types, sizes, symlink targets, names": ["missing", "extra", "type", "content", "symlink target"],
    "permission bits incl. setuid/setgid/sticky": ["mode"],
    "ownership incl. ids without names": ["owner"],
    "mtime and atime (ns) of files, dirs, symlinks (FAT: 2 s, date-only atimes)": ["mtime", "mtime (dirs)", "mtime (symlinks)", "atime", "atime (dirs)", "atime (symlinks)"],
    "device nodes (incl. rdev 0, an overlay whiteout) and FIFOs": ["device number"],
    "hard links (incl. the same inode numbers in other subvolumes)": ["hard links"],
    "file capabilities (security.capability, v2 and v3; the guest's own)": ["capability"],
    "POSIX ACLs access + default (system.posix_acl_*; the journal's)": ["acl"],
    "user.* xattrs": ["user-xattr"],
    "trusted.* xattrs (incl. trusted.overlay.opaque)": ["trusted-xattr"],
    "other xattrs (security.bko, btrfs.compression)": ["other-xattr", "selinux"],
    "inode flags (i, a, d, A, S, D; Btrfs c, m, C)": ["inode flags"],
    "sparse files and allocation (S3-S5)": ["holes lost", "holes lost (sub-4K)", "holes gained", "allocation lost"],
}
out = []
def line(v, need, detail): out.append((v, need, detail))
recs = DIRS + ["over-restic", "over-rustic"]
full = {d: js(d + ".json") for d in recs}
from importlib.machinery import SourceFileLoader
fsmeta = SourceFileLoader("fsmeta", os.path.join(os.path.dirname(__file__), "fsmeta.py")).load_module()
diffs = {d: (fsmeta.differences(src, full[d]) if full[d] is not None else None) for d in full}
# A nested subvolume's place in a read-only snapshot is Btrfs's empty stub
# directory (inode 2): Btrfs gives it the time it reads the inode in, so
# its times differ from one stat to the next.  Judged without them.
STUBS = {l.strip() for l in rd("stubs-src").splitlines() if l.strip()}
TIMEK = lambda k: k.startswith("mtime") or k.startswith("atime")
stubbed = set()
for d, dd in diffs.items():
    for k in list(dd or {}):
        if TIMEK(k):
            stubbed.update(p for p in dd[k] if p in STUBS)
            dd[k] = [p for p in dd[k] if p not in STUBS]
            if not dd[k]: del dd[k]
def tree_paths(ps): return [p for p in ps if p == TREE or p.startswith(TREE + "/")]
socket = TREE + "/socket"
sockets = {p for p in src if src[p]["type"] == "socket"}
ntree = sum(1 for p in src if p.startswith(TREE))
for need, kinds in KINDS.items():
    bad = []
    for d in DIRS:
        if diffs[d] is None: bad.append(f"{NAMES[d]}: no record"); continue
        for k in kinds:
            ps = [p for p in tree_paths(diffs[d].get(k, [])) if not (k == "missing" and p in sockets)]
            if ps: bad.append(f"{NAMES[d]}: {k} {len(ps)} e.g. {ps[:3]!r}")
    line("FAIL" if bad else "PASS", need, "; ".join(bad)[:900] if bad else
         f"4 directions, {ntree} source paths on Btrfs (six subvolumes, read-only snapshots) and the ESP (vfat)")
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
     "; ".join(bad)[:900] if bad else f"{len(sp)} sparse files marked by both tools, blocks <= source's in 4 directions")
# Sockets.
bad = []
rel = {p[len(TREE) + 1:] for p in sockets}
for t in nodes:
    if any(x.split("\t")[0] in rel for x in nodes[t].splitlines()): bad.append(f"socket in {t} snapshot")
for d in DIRS:
    if full[d] and sockets & set(full[d]): bad.append(f"{NAMES[d]} made a socket")
    if "socket" in rd(f"restore-{d}.log"): bad.append(f"{NAMES[d]} mentions the socket")
for t in ("restic", "rustic"):
    if "socket" in rd(f"backup-{t}.log"): bad.append(f"{t} backup mentions the socket")
line("FAIL" if bad or socket not in src else "PASS", "sockets skipped (decision)",
     "; ".join(bad) if bad else (f"{len(sockets)} sockets in the source (the tree's, its read-only snapshot's); in no snapshot, no restore, no message" if socket in src else "no socket made"))
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
    if ks: bad.append(f"{NAMES[d]}: {ks}")  # both tools store them with their real metadata (since round 16)
line("FAIL" if bad else "PASS", "metadata of the directories above the backed-up path",
     "; ".join(bad) if bad else "up1, up1/up2 and /mnt/src (@root's own root) restored exactly in 4 directions (owner 1234:5678, 0710/1755, xattrs, ACL, times)")
# Snapshot identity.
nd = rd("nodes.diff").strip()
n = len(nodes["restic"].splitlines())
SREL = {p[len(TREE) + 1:] for p in STUBS}
ndl = [l.strip() for l in nd.splitlines() if l.strip()]
stubonly = bool(ndl) and all(re.fullmatch(r"(.*): (ctime|mtime|atime) @\S+ != @\S+", l) and l.rsplit(": ", 1)[0] in SREL for l in ndl)
if stubonly: status["nodes"] = "0"; stubbed.update(TREE + "/" + l.rsplit(": ", 1)[0] for l in ndl)
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
d = (js("src-after.cmp.json") or {}).get("differences", {})
for k in list(d):
    ex = d[k].get("examples", []) if isinstance(d[k], dict) else []
    if TIMEK(k) and ex and set(ex) <= STUBS and d[k].get("count") == len(ex): stubbed.update(ex); del d[k]
line("FAIL" if d or js("src-after.cmp.json") is None else "PASS", "backup leaves the source's metadata (atimes) alone",
     json.dumps(d)[:600] if d else ("no comparison" if js("src-after.cmp.json") is None else "identical before and after both backups (the guest's noatime mounts)"))
def fatcmp(a, b):
    a, b = js(a), js(b)
    if a is None or b is None: return ["no FAT record"]
    h = lambda v: hex(v) if isinstance(v, int) else v
    d = [f"{p} {h(a[p])}->{h(b.get(p))}" for p in sorted(a) if b.get(p) != a[p]]
    return d + [f"{p} not in the source" for p in sorted(set(b) - set(a))]
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
    fd = fatcmp("src.fat.json", f"{k}.fat.json")
    if fd: bad.append(f"{NAMES[k]}: FAT attributes {fd[:4]}")
line("FAIL" if bad else "PASS", "restore over existing immutable/append-only files (and an immutable dir), changed FAT attributes and read-only files",
     "; ".join(bad)[:900] if bad else "changed immutable file, immutable dir, appended append-only file, Btrfs's immutable compressed and append-only nocow files;"
     " changed read-only file, cleared hidden/system/read-only/archive attributes on the ESP: both tools put back contents, xattrs, mode, flags and FAT attributes, exit 0, no warning")
if stubbed:
    line("LIMIT", "a nested subvolume's place in a read-only snapshot (Btrfs's empty stub directory) has no times of its own",
         f"{sorted(p[len(TREE) + 1:] for p in stubbed)}: Btrfs gives this inode 2 the time it reads it in, so its mtime/ctime change between stats (and between the two backups' nodes); judged without its times, restored as an empty directory")
line("LIMIT", "birth times (statx btime on Btrfs and vfat)", "Linux can't set a birth time: neither tool keeps one on Linux (restic BACKOTTER.md, unix.birthtime)")
line("LIMIT", "project IDs (and P inheritance)", "not applicable: the guest's file systems are Btrfs and vfat, neither of which has project IDs (chattr -p is refused)")
lsm = rd("lsm").strip()
line("LIMIT", "SELinux labels", f"not applicable: the guest runs AppArmor (LSMs {lsm or '?'}), which keeps no labels on disk; security.bko, another security.* xattr, is checked")
# Btrfs per-file options, as lsattr and btrfs.compression show them.
sb = {l.split()[0]: l.split()[1:] for l in rd("src.btrfs").splitlines() if l.strip()}
bad = []
for d in DIRS:
    for l in rd(f"{d}.btrfs").splitlines():
        f = l.split()
        if f and sb.get(f[0]) != f[1:]: bad.append(f"{NAMES[d]}: {f[0]} {f[1:]} (source {sb.get(f[0])})")
    if not rd(f"{d}.btrfs").strip(): bad.append(f"{NAMES[d]}: no listing")
if not sb: bad.append("no source listing")
line("FAIL" if bad else "PASS", "Btrfs per-file options (compression property zstd, zstd:5, lzo, zlib and its dir inheritance; c, m, C and nocow dir inheritance) under compress=zstd:3",
     "; ".join(bad)[:900] if bad else f"{len(sb)} files: flags and btrfs.compression as the source's in 4 directions ({', '.join(k + '=' + '/'.join(v) for k, v in list(sb.items())[:5])}...)")
sv = [p for p in src if p == TREE + "/subvol" or p.startswith(TREE + "/subvol/")]
bad = [f"{NAMES[d]}: {k}" for d in DIRS if diffs[d] for k, ps in diffs[d].items() for p in ps if p in sv]
line("FAIL" if bad or not sv else "PASS", "nested Btrfs subvolume in the backed-up tree (backed up across, restored as a directory)",
     "; ".join(bad)[:600] if bad else (f"{len(sv)} paths (owner 1234:5678, 0750, user xattr, times) restored in 4 directions" if sv else "no subvolume made"))
# The scratch disk made like the guest's.
def same(a, b, what):
    x, y = rd(a).split(), rd(b).split()
    return bool(x == y and x), f"{what}: guest {' '.join(x) or '?'}; scratch {(' '.join(y) or '?') if x != y else 'the same'}"
ok1, d1 = same("gpt-guest", "gpt-src", "GPT (each partition's start, size but the last's, type, name)")
sw = rd("swap-guest").split(), rd("swap-src").split()
ok2 = len(sw[0]) == 2 and len(sw[1]) == 3 and sw[0][1] == sw[1][1] and sw[1][2] == "swap"
line("PASS" if ok1 and ok2 else "FAIL", "scratch disk partitioned like the guest's (BIOS boot, ESP, swap, Btrfs)",
     f"{d1}; swap: guest {' '.join(sw[0]) or '?'}, scratch {' '.join(sw[1]) or '?'}")
ok1, d1 = same("feat-guest", "feat-src", "superblock (incompat less LZO/ZSTD, compat_ro, csum, node and sector size)")
NEST = "/up1/up2/tree/subvol"  # the fixture's own nested subvolume (tumbleweed's), not the guest's
sg = [l for l in rd("subvols-guest").splitlines() if l.strip()]
ss = [l for l in rd("subvols-src").splitlines() if l.strip() and not l.split()[0].endswith(NEST)]
ok2 = sg == ss and len(sg) >= 6
ro = [l.split()[0] for l in sg if l.endswith(" ro")]
line("PASS" if ok1 and ok2 else "FAIL", "scratch Btrfs made like the guest's (features; the six subvolumes in its order; its read-only snapshots, by path, under @snapshots)",
     f"{d1}; subvolumes " + (f"as the guest's: {len(sg)}, read-only {ro}" if ok2 else f"guest {sg}; scratch {ss}"))
ok1, d1 = same("fat-guest", "fat-src", "FAT type, sector and cluster size, FATs, reserved sectors")
mg = dict(l.split(None, 1) for l in rd("mopts-guest").splitlines() if len(l.split(None, 1)) == 2)
ms = dict(l.split(None, 1) for l in rd("mopts-src").splitlines() if len(l.split(None, 1)) == 2)
ok2 = mg == ms and len(mg) >= 7
line("PASS" if ok1 and ok2 else "FAIL", "scratch file systems mounted like the guest's (fstab's options, the six subvolumes compress=zstd:3,noatime and the ESP)",
     f"{d1}; mount options " + (f"as the guest's for {sorted(mg)}: / {mg.get('/', '?')}; /boot/efi {mg.get('/boot/efi', '?')}" if ok2
                                else f"differ: {[(k, mg.get(k), ms.get(k)) for k in sorted(set(mg) | set(ms)) if mg.get(k) != ms.get(k)]}"))
# The file systems mounted inside the backed-up path, each restored onto
# the target's own.
def mtab(n):  # MP -> (SOURCE, FSROOT, COUNT)
    r = {}
    for l in rd(n).splitlines():
        f = l.split()
        if len(f) == 4: r[f[0]] = (f[1], f[2], f[3])
    return r
msrc = mtab("mounts-src")
fstab = dict(l.split(None, 1) for l in rd("fstab-btrfs").splitlines() if len(l.split(None, 1)) == 2)
for mp in [m for m in fstab if m != "/"] + ["/boot/efi"]:
    sub = TREE + mp
    under = lambda p: p == sub or p.startswith(sub + "/")
    nb = sum(1 for p in src if under(p))
    ns = sum(1 for p in sockets if under(p))
    want = "/" if mp == "/boot/efi" else "/" + next((o.split("=", 1)[1].lstrip("/") for o in fstab[mp].split(",") if o.startswith("subvol=")), "?")
    bad = []
    s0 = msrc.get(mp)
    if not s0 or s0[1] != want: bad.append(f"source on {s0}, not {want}")
    for d in DIRS:
        if diffs[d] is None: continue
        ks = sorted({k for k, ps in diffs[d].items() for p in ps if under(p) and not (k == "missing" and p in sockets)})
        if ks: bad.append(f"{NAMES[d]}: {ks}")
        t = mtab(f"mounts-{d}").get(mp)
        if not t: bad.append(f"{NAMES[d]}: no mount record"); continue
        if t[1] != want or not re.fullmatch(r"/dev/loop[0-9]+p(2|4)", t[0]): bad.append(f"{NAMES[d]}: on {t[0]} {t[1]}")
        if s0 and str(int(s0[2]) - ns) != t[2]: bad.append(f"{NAMES[d]}: {t[2]} paths on the target's own, source {s0[2]} ({ns} sockets)")
    what = "the ESP (vfat, a partition)" if mp == "/boot/efi" else f"the subvolume {want.lstrip('/')}"
    line("FAIL" if bad or nb < 3 else "PASS", f"{what} mounted inside the backed-up path, as {mp}",
         ("; ".join(bad)[:900] if bad else f"only {nb} source paths") if bad or nb < 3 else f"{nb} paths restored onto each target's own {what.split(' (')[0].replace('the ', '')}, mounted before the restore, in 4 directions; no difference")
# Read-only snapshots: their trees restored (as directories), and what
# only a subvolume has.
made = [l.split() for l in rd("snaps-made").splitlines() if len(l.split()) == 3]
snaps = [TREE + "/.snapshots/" + m[0].split("/.snapshots/", 1)[1] for m in made]
bad, nsn = [], 0
for s in snaps:
    under = lambda p: p == s or p.startswith(s + "/")
    nsn += sum(1 for p in src if under(p))
    for d in DIRS:
        if diffs[d] is None: continue
        ks = sorted({k for k, ps in diffs[d].items() for p in ps if under(p) and not (k == "missing" and p in sockets)})
        if ks: bad.append(f"{NAMES[d]}: {s[len(TREE) + 1:]}: {ks}")
line("FAIL" if bad or len(snaps) < 2 or nsn < 100 else "PASS", "read-only snapshots under @snapshots (the snapshot manager's deployments/<id>/root of @root, personal/snapshots/<id>/home of @home), their trees",
     "; ".join(bad)[:900] if bad else (f"{len(snaps)} snapshots, {nsn} paths: contents and metadata restored in 4 directions (as directories in the target's @snapshots)" if len(snaps) >= 2
                                       else f"only {len(snaps)} snapshot(s) made: {rd('snaps-made')[:300]}"))
ss = {l.split()[0]: l.split()[1:] for l in rd("snaps-src").splitlines() if l.strip()}
st_ = {d: {l.split()[0]: l.split()[1:] for l in rd(f"snaps-{d}").splitlines() if l.strip()} for d in DIRS}
srcok = ss and all(v[:2] == ["subvolume", "ro=true"] for v in ss.values())
line("LIMIT", "read-only snapshots' subvolume, read-only flag and shared extents",
     ("not the archivers': each comes back as a directory with its own extents (bko-holt's load recreates them; HoltTest1). "
      f"Source: {'; '.join(k.rsplit('/', 2)[-2][:8] + '/' + k.rsplit('/', 1)[-1] + ' ' + ' '.join(v) for k, v in ss.items())}; "
      f"restored ({NAMES['rr']}): {'; '.join(k.rsplit('/', 2)[-2][:8] + '/' + k.rsplit('/', 1)[-1] + ' ' + ' '.join(v) for k, v in st_['rr'].items())}")
     if srcok else f"source snapshots not read-only subvolumes: {ss}")
# The same inode numbers in other subvolumes (the tree's hard links, the
# snapshot's copies, @home's): each group restored as its own.
ino = [l.split() for l in rd("inodes-src").splitlines() if len(l.split()) == 4]
links = {}
for p, e in src.items():
    if e.get("links") and p.startswith(TREE): links[tuple(e["links"])] = 1
bad = []
for d in DIRS:
    if diffs[d] is None: continue
    hl = diffs[d].get("hard links", [])
    if tree_paths(hl): bad.append(f"{NAMES[d]}: {tree_paths(hl)[:3]}")
same_ino = len({i[1] for i in ino}) < len(ino)
line("FAIL" if bad or len(ino) < 3 or not same_ino else "PASS", "hard links in different subvolumes with the same inode numbers (a snapshot's copies) kept apart",
     "; ".join(bad)[:900] if bad else (f"{len(links)} hard-link groups; hardlink-a: {', '.join(i[0] + ':' + i[1] + ' x' + i[2] for i in ino)} (device:inode, links): each subvolume's group restored as its own in 4 directions"
                                       if len(ino) >= 3 and same_ino else f"no inode numbers shared across subvolumes: {rd('inodes-src')[:300]}"))
# FAT attributes, each direction.
bad = []
for d in DIRS:
    fd = fatcmp("src.fat.json", f"{d}.fat.json")
    if fd: bad.append(f"{NAMES[d]}: {len(fd)} differ, e.g. {fd[:4]}")
fa = js("src.fat.json") or {}
line("FAIL" if bad or len(fa) < 30 else "PASS", "FAT attributes (readonly, hidden, system, archive) on files and dirs, the guest's ESP's among them",
     "; ".join(bad)[:900] if bad or len(fa) < 30 else f"{len(fa)} paths, attributes {sorted({hex(v) for v in fa.values() if isinstance(v, int)})}, identical in 4 directions")
# Real examples from the guest's own files.
R = f"{TREE}/real"
want = [f"{R}/shadow", f"{R}/passwd", f"{R}/su", f"{R}/fstab", f"{R}/grub", f"{R}/MOK.der", f"{R}/random-seed",
        f"{R}/etc_anduinos-btrfs-snapshots-manager", f"{R}/boot/grub/grub.cfg", f"{R}/boot/grub/x86_64-efi",
        f"{ESP}/real/esp/EFI/AnduinOS/shimx64.efi", f"{ESP}/real/esp/EFI/AnduinOS/grubx64.efi", f"{ESP}/real/esp/EFI/AnduinOS/mmx64.efi",
        f"{ESP}/real/esp/EFI/BOOT/BOOTX64.EFI", f"{TREE}/home/real-user", f"{TREE}/var/log/journal",
        f"{TREE}/.snapshots/anduinos-btrfs-snapshots-manager/history/system-lineage.json"]
miss = [p for p in want if p not in src]
caps = sorted(p.rsplit("/", 1)[1] for p in src if p.startswith(f"{R}/caps/"))
jf = [p for p in src if p.startswith(f"{TREE}/var/log/journal/") and p.endswith(".journal")]
nreal = sum(1 for p in src if p.startswith(f"{R}/") or p.startswith(f"{ESP}/real/") or p.startswith(f"{TREE}/home/real-user")
            or p.startswith(f"{TREE}/var/log/journal") or p.startswith(f"{TREE}/.snapshots/anduinos-btrfs-snapshots-manager/"))
line("FAIL" if miss or not caps or not jf else "PASS", "real examples from the guest's / (set-id, capabilities, the MOK, the snapshot manager), /boot, the ESP, /home, the journal",
     f"missing {miss}, capabilities {caps}, journal files {len(jf)}" if miss or not caps or not jf else
     f"{nreal} paths: set-id passwd/su/mount, capabilities {caps}, shadow, fstab, GRUB's defaults and /boot/grub, MOK.der, the snapshot manager's config and records, "
     f"shim/GRUB/MokManager on the ESP, the user's home, {len(jf)} journal file(s) with the journal's ACLs and C")
# Every item of the fixture made (a skipped one would pass unseen).
sk = [l.split("skipped: ", 1)[1] for l in rd("fixture.err").splitlines() if "skipped: " in l]
line("FAIL" if sk else "PASS", "fixture made in full", f"harness: skipped {sk[:6]}" if sk else "no fixture item skipped (fixture.err)")
line("LIMIT", "whole-disk needs (GPT, BIOS boot, the ESP's signed loaders booting under Secure Boot, the MOK, GRUB)",
     "out of scope: whole-disk (HoltTest1 1U recreates the disk and checks the ESP's shim, MOK-signed GRUB and MokManager byte for byte); here the scratch disk is laid out like the guest's and only its file systems' contents are restored")
res = [{"result": v, "need": n, "detail": d} for v, n, d in out]
json.dump(res, open(os.path.join(L, "results-guest.json"), "w"), indent=1)
for v, n, d in out: print(f"{v} {n}: {d}")
