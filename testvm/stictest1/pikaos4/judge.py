#!/usr/bin/env python3
"""Judge pikaos4's or pikaos4-luks's check (inner.sh's logs): one
PASS/FAIL/LIMIT line per need, results-guest.json beside them.  The layout
(ESP and /boot mount points, /'s type, LUKS under /, the partitions' roles,
Btrfs's subvolumes) comes from env.txt's first line, as inner.sh read it
from the guest.  pikaos4's need names are as they were before
pikaos4-luks's (its limits.json entry names them)."""
import json, os, re, sys
L = sys.argv[1]
def rd(n, d=""):
    try: return open(os.path.join(L, n), errors="replace").read()
    except OSError: return d
def js(n):
    try: return json.load(open(os.path.join(L, n)))
    except Exception: return None
status = dict(l.split() for l in rd("status").splitlines() if len(l.split()) == 2)
lay = dict(kv.split("=", 1) for kv in rd("env.txt").splitlines()[0].split()) if rd("env.txt") else {}
ESPM, BOOTM = lay.get("ESPM", "/boot/efi"), lay.get("BOOTM", "/boot")
CRYPT = lay.get("CRYPTPART", "-") != "-"
ROLES = [r for r in lay.get("ROLES", "").split(",") if r]
GUEST = rd("guest").strip() or "pikaos4"
src = js("src.json") or {}
TREE = "up1/up2/tree"
ESP, BOOT = TREE + ESPM, TREE + BOOTM
ABOVE = {".", "up1", "up1/up2"}
DIRS = ["rr", "uu", "ru", "ur"]
NAMES = {"rr": "restic->restic", "uu": "rustic->rustic", "ru": "restic->rustic", "ur": "rustic->restic",
         "over-restic": "restic over existing", "over-rustic": "rustic over existing"}
BTRFS = lay.get("ROOTFS", "xfs") == "btrfs"
SUBS = [x.split(":", 1) for x in lay.get("SUBS", "").split(",") if ":" in x]   # [[TARGET, FSROOT]]: /home, /@home
FSS = (f"Btrfs {lay.get('ROOTSUB', '/@')} as /" + "".join(f", {fr} as {t}" for t, fr in SUBS) + (" on LUKS2" if CRYPT else "")
       if BTRFS else "XFS /") + f", ext4 {BOOTM}, the ESP (vfat {ESPM})"
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
    ("other xattrs (security.bko, btrfs.compression)" if BTRFS else "other security.* xattrs"): ["other-xattr", "selinux"],
    ("inode flags (i, a, d, A, S; btrfs c, m, C; ext4's D, j, T on /boot)" if BTRFS else "inode flags (i, a, d, A, S; ext4's D, j, T on /boot)"): ["inode flags"],
    "project IDs (XFS /, and P inheritance)": ["project ID"],
    "sparse files and allocation (S3-S5)": ["holes lost", "holes lost (sub-4K)", "holes gained", "allocation lost"],
}
if BTRFS:
    del KINDS["project IDs (XFS /, and P inheritance)"]
out = []
def line(v, need, detail): out.append((v, need, detail))
recs = DIRS + ["over-restic", "over-rustic"]
full = {d: js(d + ".json") for d in recs}
from importlib.machinery import SourceFileLoader
fsmeta = SourceFileLoader("fsmeta", os.path.join(os.path.dirname(__file__), "fsmeta.py")).load_module()
diffs = {d: (fsmeta.differences(src, full[d]) if full[d] is not None else None) for d in full}
def tree_paths(ps): return [p for p in ps if p == TREE or p.startswith(TREE + "/")]
socket = TREE + "/socket"
sockets = {p for p in src if src[p]["type"] == "socket"}
for need, kinds in KINDS.items():
    bad = []
    for d in DIRS:
        if diffs[d] is None: bad.append(f"{NAMES[d]}: no record"); continue
        for k in kinds:
            ps = [p for p in tree_paths(diffs[d].get(k, [])) if not (k == "missing" and p in sockets)]
            if ps: bad.append(f"{NAMES[d]}: {k} {len(ps)} e.g. {ps[:3]!r}")
    n = sum(1 for p in src if p.startswith(TREE))
    line("FAIL" if bad else "PASS", need, "; ".join(bad)[:900] if bad else f"4 directions, {n} source paths on {FSS}")
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
     "; ".join(bad)[:900] if bad else f"{len(sp)} sparse files marked by both tools, blocks <= source's in {len(DIRS)} restores")
# Sockets.
bad = []
for t in nodes:
    rel = {p[len(TREE) + 1:] for p in sockets}
    if any(x.split("\t")[0] in rel for x in nodes[t].splitlines()): bad.append(f"socket in {t} snapshot")
for d in DIRS:
    if full[d] and sockets & set(full[d]): bad.append(f"{NAMES[d]} made a socket")
    if "socket" in rd(f"restore-{d}.log"): bad.append(f"{NAMES[d]} mentions the socket")
for t in ("restic", "rustic"):
    if "socket" in rd(f"backup-{t}.log"): bad.append(f"{t} backup mentions the socket")
line("FAIL" if bad or socket not in src else "PASS", "sockets skipped (decision)",
     "; ".join(bad) if bad else (f"{len(sockets)} sockets in the source; in no snapshot, no restore, no message" if socket in src else "no socket made"))
# Exit status and messages.
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
     ("; ".join(bad) if bad else "restic snapshots: up1, up1/up2 and /mnt/src restored exactly (owner 1234:5678, 0710/1755, xattrs, ACL, times)"
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
d = (js("src-after.cmp.json") or {}).get("differences", {})
line("FAIL" if d or js("src-after.cmp.json") is None else "PASS", "backup leaves the source's metadata (atimes) alone",
     json.dumps(d)[:600] if d else ("no comparison" if js("src-after.cmp.json") is None else "identical before and after both backups"))
def on_relatime(n):  # findmnt TARGET OPTIONS lines: each relatime, none noatime
    ms = [l.split(None, 1) for l in rd(n).splitlines() if l.strip()]
    bad = [m for m in ms if len(m) < 2 or "relatime" not in m[1].split(",") or "noatime" in m[1].split(",")]
    return (len(ms) >= 3 and not bad), f"{n}: {len(ms)} mounts" + (f", not on relatime: {bad[:2]}" if bad else "") + (rd("remount.err")[:200] and "; " + rd("remount.err")[:200])
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
    dd = diffs.get(k)
    if dd is None: bad.append(f"{NAMES[k]}: no record"); continue
    ks = {kk: tree_paths(ps) for kk, ps in dd.items() if tree_paths(ps) and not (kk == "missing" and set(tree_paths(ps)) <= sockets)}
    if ks: bad.append(f"{NAMES[k]}: " + "; ".join(f"{kk} {v[:3]}" for kk, v in ks.items()))
    fd = fatcmp("src.fat.json", f"{k}.fat.json")
    if fd: bad.append(f"{NAMES[k]}: FAT attributes {fd[:4]}")
    ok, why = on_relatime(f"mopts-rel-{k}")
    if not ok: bad.append(f"{NAMES[k]}: target not on relatime ({why})")
line("FAIL" if bad else "PASS", "restore over existing immutable/append-only files (and an immutable dir), changed FAT attributes and read-only files",
     "; ".join(bad)[:900] if bad else f"changed immutable file, immutable dir, appended append-only file on / and ext4 {BOOTM}" + (" and " + ", ".join(t for t, _ in SUBS) if SUBS else "") + ""
     "; changed read-only file, cleared hidden/system/read-only/archive attributes on the ESP: both tools put back contents, xattrs, mode, flags and FAT attributes, exit 0 (targets on relatime)")
if BTRFS:
    line("LIMIT", "project IDs (and P inheritance)", "Btrfs has no project IDs, and the guest's /boot ext4 has no project feature: none is made (fixture.sh)")
line("LIMIT", f"birth times (statx btime on {'Btrfs' if BTRFS else 'XFS'} and ext4)", "Linux can't set a birth time: neither tool keeps one on Linux (restic BACKOTTER.md, unix.birthtime)")
# The scratch disk made like the guest's.
def same(a, b, what):
    x, y = rd(a).split(), rd(b).split()
    return bool(x == y and x), f"{what}: guest {' '.join(x) or '?'}; scratch {(' '.join(y) or '?') if x != y else 'the same'}"
if BTRFS:
    ok1, d1 = same("feat-root", "feat-src", "Btrfs superblock (incompat and compat_ro flags, checksum, node and sector size)")
    ok2, d2 = same("mopts-root", "mopts-src", f"mount options of {lay.get('ROOTSUB', '/@')} (but subvolid)")
    oks = [same(f"mopts-root-{os.path.basename(t)}", f"mopts-src-{os.path.basename(t)}", f"mount options of {fr} at {t} (but subvolid)") for t, fr in SUBS]
    sv = [l.split()[-1] for l in rd("subvols-root").splitlines() if l.startswith("ID ") and " path " in l]
    line("PASS" if ok1 and ok2 and all(o for o, _ in oks) and SUBS else "FAIL",
         "scratch Btrfs" + (" on LUKS2" if CRYPT else "") + " on a partition (linux-root) made like /'s, its subvolumes made and mounted like the guest's (/ and " + ", ".join(t for t, _ in SUBS) + ")",
         f"{d1}; {d2}; " + "; ".join(d for _, d in oks) + f"; the guest's subvolumes {sv}; {rd('mkfs-btrfs.log').strip()[:200]}")
else:
    ok1, d1 = same("feat-root", "feat-src", "xfs_info features")
    ok2, d2 = same("mopts-root", "mopts-src", "mount options")
    line("PASS" if ok1 and ok2 else "FAIL", "scratch XFS on a partition made and mounted like / (linux-root)", f"{d1}; {d2}; {rd('mkfs-xfs.log').strip()[:200]}")
ok1, d1 = same("feat-boot", "feat-srcboot", "ext4 features")
ok2, d2 = same("geo-boot", "geo-srcboot", "inode and block size, default mount options, flex_bg, hash")
ok3, d3 = same("mopts-boot", "mopts-srcboot", "mount options")
line("PASS" if ok1 and ok2 and ok3 else "FAIL", f"scratch ext4 on an XBOOTLDR partition made and mounted like {BOOTM} (linux-boot)", f"{d1}; {d2}; {d3}")
ok1, d1 = same("fat-esp", "fat-src", "FAT type, sector and cluster size, FATs, reserved sectors")
ok2, d2 = same("mopts-esp", "mopts-srcesp", "mount options")
line("PASS" if ok1 and ok2 else "FAIL", f"scratch FAT32 ESP made and mounted like {ESPM} (linux-efi)", f"{d1}; {d2}")
# The GPT: per set, the guest's partitions in their order with their
# types, names and attributes (the ESP's and /boot's sizes too); then one
# more like / (the non-root run's).
def parts(n):
    return [l.split() for l in rd(n).splitlines()[1:] if len(l.split()) == 6]
g, sg = parts("gpt-root"), parts("gpt-scratch")
key = lambda e, r: (e[3], e[4], e[5]) + ((e[2],) if r in ("esp", "boot") else ())
want = [key(e, r) for e, r in zip(g, ROLES)]
bad = []
if not g or len(ROLES) != len(g): bad.append(f"guest's GPT {len(g)} partitions, roles {ROLES}")
nsets = 5
if len(sg) != nsets * len(g) + 1: bad.append(f"scratch has {len(sg)} partitions, not {nsets} x {len(g)} + 1")
for i in range(nsets):
    got = [key(e, r) for e, r in zip(sg[i * len(g):(i + 1) * len(g)], ROLES)]
    if got != want: bad.append(f"set {i + 1}: {got} != {want}")
rootk = [key(e, "root") for e, r in zip(g, ROLES) if r == "root"]
if sg and rootk and key(sg[-1], "root") != rootk[0]: bad.append(f"the non-root run's partition {sg[-1]} isn't like /'s")
ok3, d3 = (True, "") if not CRYPT else same("luks-root", "luks-scratch", "LUKS2 header (version, data cipher, offset, sector, key bits, PBKDF, time, memory, threads)")
line("FAIL" if bad or not ok3 else "PASS", "scratch GPT" + (" and LUKS2" if CRYPT else "") + " made like the guest's (partition names, types incl. XBOOTLDR, attributes; the ESP's and /boot's sizes)",
     "; ".join(bad)[:900] if bad else f"guest {' | '.join(' '.join(e[3:]) + (' ' + e[2] if r in ('esp', 'boot') else '') for e, r in zip(g, ROLES))} (roles {','.join(ROLES)}); "
     f"scratch the same {nsets} times, then one more like /" + (f"; {d3}" if CRYPT else ""))
# The file systems mounted inside the backed-up path: restored onto each
# target's own.
def inside(sub, m, name, devre):
    under = lambda p: p == sub or p.startswith(sub + "/")
    nb = sum(1 for p in src if under(p))
    bad = []
    for d in DIRS:
        if diffs[d] is None: continue
        ks = sorted({k for k, ps in diffs[d].items() for p in ps if under(p) and not (k == "missing" and p in sockets)})
        if ks: bad.append(f"{NAMES[d]}: {ks}")
        c0, c1 = rd(f"count-src-{m}").strip(), rd(f"count-{d}-{m}").strip()
        ns = sum(1 for p in sockets if under(p) and (m != "boot" or not under_esp(p)))   # the sockets no restore makes, on this file system
        if not c0.isdigit() or str(int(c0) - ns) != c1: bad.append(f"{NAMES[d]}: {c1 or '?'} paths on the target's own, source {c0 or '?'} ({ns} sockets)")
        if not re.fullmatch(devre, rd(f"dev-{d}-{m}").strip()): bad.append(f"{NAMES[d]}: on {rd(f'dev-{d}-{m}').strip()!r}")
    return ("FAIL" if bad or nb < 30 else "PASS"), ("; ".join(bad)[:900] if bad or nb < 30 else
            f"{nb} paths restored onto each target's own {name}, mounted before the restore, in {len(DIRS)} restores; no difference")
under_esp = lambda p: p == ESP or p.startswith(ESP + "/")
v, det = inside(BOOT, "boot", "ext4 (an XBOOTLDR partition)", r"/dev/vdb[0-9]+")
line(v, f"{BOOTM} (ext4, an XBOOTLDR partition) mounted inside the backed-up path", det)
v, det = inside(ESP, "esp", "ESP (a partition)", r"/dev/vdb[0-9]+")
line(v, f"the ESP (vfat, a partition) mounted inside {BOOTM} inside the backed-up path, as {ESPM}", det)
for t, fr in SUBS:
    m = os.path.basename(t)
    v, det = inside(TREE + t, m, f"Btrfs subvolume {fr}", r"/dev/(mapper/bkst_)?(rr|uu|ru|ur|vdb[0-9]+)\[" + re.escape(fr) + r"\]")
    line(v, f"a second Btrfs subvolume ({fr}, like {t}) mounted inside the backed-up path", det)
# FAT attributes, each direction.
bad = []
for d in DIRS:
    fd = fatcmp("src.fat.json", f"{d}.fat.json")
    if fd: bad.append(f"{NAMES[d]}: {len(fd)} differ, e.g. {fd[:4]}")
fa = js("src.fat.json") or {}
line("FAIL" if bad or len(fa) < 30 else "PASS", "FAT attributes (readonly, hidden, system, archive) on files and dirs, the guest's ESP's among them",
     "; ".join(bad)[:900] if bad or len(fa) < 30 else f"{len(fa)} paths, attributes {sorted({hex(v) for v in fa.values() if isinstance(v, int)})}, identical in {len(DIRS)} restores")
# Real examples from the guest's own files.
want = [f"{TREE}/real/passwd", f"{TREE}/real/su", f"{TREE}/real/unix_chkpwd", f"{TREE}/real/shadow", f"{TREE}/real/booster.yaml",
        f"{TREE}/real/apt-sources", f"{TREE}/real/status", f"{TREE}/real/dpkg-info", f"{TREE}/real/systemd-system",
        f"{TREE}/real/random-seed", f"{TREE}/real/journal",
        f"{BOOT}/real/boot/refind_linux.conf", f"{ESP}/real/esp/EFI/BOOT"]
want += [f"{TREE}/real/crypttab"] if CRYPT else []
want += [f"{TREE}{t}/real/skel" for t, _ in SUBS if t == "/home"]
miss = [p for p in want if p not in src]
# rEFInd in EFI/refind with an NVRAM entry (pikaos4), or in EFI/BOOT only
# (pikaos4-luks's "BOOTX64 mode"): its refind.conf either way.
if not any(f"{ESP}/real/esp/EFI/{d}/refind.conf" in src for d in ("refind", "BOOT")): miss.append("rEFInd's refind.conf")
loaders = [d for d in ("refind", "BOOT") if f"{ESP}/real/esp/EFI/{d}/refind.conf" in src]
for pre, what in ((f"{BOOT}/real/boot/vmlinuz-", "a kernel"), (f"{BOOT}/real/boot/booster.img-", "booster's initramfs")):
    if not any(p.startswith(pre) for p in src): miss.append(what)
info = sorted({p.split("/")[-1].split(".")[0] for p in src if p.startswith(f"{TREE}/real/dpkg-info/")})
nreal = sum(1 for p in src if "/real/" in p + "/" and p.startswith(TREE))
line("FAIL" if miss else "PASS", f"real examples from the guest's / (set-id, dpkg, booster, systemd), its {BOOTM} and ESP",
     f"missing {miss}" if miss else f"{nreal} paths: set-id passwd/su/mount, set-gid unix_chkpwd, shadow, booster.yaml, apt's sources, dpkg's status and info ({', '.join(info)}), "
     f"systemd's links, random-seed, the journal (ACLs, set-gid)" + (", crypttab" if CRYPT else "") + (", /home's skel" if SUBS else "")
     + f"; {BOOTM}'s kernel, booster.img and refind_linux.conf; the ESP's rEFInd ({', '.join('EFI/' + d for d in loaders)})")
# Backups on relatime leave the atimes alone.
relok, reld = on_relatime("mopts-rel-src")
dd = (js("src-rel.cmp.json") or {}).get("differences", {})
other = {k: v for k, v in dd.items() if k != "atime (symlinks)"}
line("FAIL" if other or not relok or js("src-rel.cmp.json") is None else ("LIMIT" if dd else "PASS"),
     "backups on relatime (the guest's /, /boot and ESP) leave the source's atimes alone",
     f"source not on relatime for them ({reld})" if not relok else
     json.dumps(other)[:600] if other else ("no comparison" if js("src-rel.cmp.json") is None else
     (f"only symlinks' atimes move: readlink(2) has no O_NOATIME (both BACKOTTER.md), source and its mounts on relatime ({json.dumps(dd)[:300]})" if dd
      else "restic and rustic backups with the source and its mounts on relatime: no atime moved")))
# Every item of the fixture made (a skipped one would pass unseen).
sk = [l.split("skipped: ", 1)[1] for l in rd("fixture.err").splitlines() if "skipped: " in l]
line("FAIL" if sk else "PASS", "fixture made in full", f"harness: skipped {sk[:6]}" if sk else "no fixture item skipped (fixture.err)" + (f"; {rd('shims').strip()}" if rd("shims").strip() else ""))
# XFS's own per-file options.  Its preallocated flag (p, 0x2) isn't one
# of a file's options: a dense restore preallocates (S5) and so gives
# every file it, a sparse one none (accepted, both BACKOTTER.md).
def nop(l):
    l = re.sub(r"(fsxattr\.xflags = )0x([0-9a-fA-F]+)", lambda m: m.group(1) + hex(int(m.group(2), 16) & ~2), l)
    return re.sub(r"\[(.)p", r"[\1-", l)
if BTRFS:
    # Btrfs's per-file options (inner.sh's btrfsx: lsattr and the
    # btrfs.compression property), as tumbleweed judges them.
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
    sv = [p for p in src if p == TREE + "/subvol" or p.startswith(TREE + "/subvol/")]
    bad = [f"{NAMES[d]}: {k}" for d in DIRS if diffs[d] for k, ps in diffs[d].items() for p in ps if p in sv]
    line("FAIL" if bad or not sv else "PASS", "nested btrfs subvolume in the backed-up tree (backed up across, restored as a directory)",
         "; ".join(bad)[:600] if bad else (f"{len(sv)} paths (owner 1234:5678, 0750, user xattr, times) restored in 4 directions" if sv else "no subvolume made"))
else:
    sx = [nop(l) for l in rd("src.xfsx").splitlines()]
    bad = [f"source: {l}" for l in sx if "fsxattr.xflags" not in l]
    for d in DIRS + ["over-restic", "over-rustic"]:
        dx = [nop(l) for l in rd(d + ".xfsx").splitlines()]
        if not dx: bad.append(f"{NAMES[d]}: no record"); continue
        ch = [f"{a} -> {b}" for a, b in zip(sx, dx) if a != b]
        if ch: bad.append(f"{NAMES[d]}: {ch[:4]}")
    line("FAIL" if bad or not sx else "PASS", "XFS per-file options on / (fsxattr: extent size hints and inheritance, nodefrag f, filestream S)",
         "; ".join(bad)[:900] if bad else f"identical in {len(DIRS) + 2} restores (but p, preallocated: S5, accepted)")
# (pikaos4-luks's rEFInd is the fallback loader only: no NVRAM entry.)
NVRAM = f"{ESP}/real/esp/EFI/refind" in src
line("LIMIT", "whole-disk needs (GPT, " + ("LUKS2, " if CRYPT else "") + "the ESP's rEFInd" + (" and its NVRAM entry" if NVRAM else " (the fallback loader)") + ", booster's initramfs)",
     "out of scope: whole-disk (HoltTest1 recreates the disk" + (", LUKS2 and the Btrfs subvolumes" if CRYPT and BTRFS else " and LUKS2" if CRYPT else "") + ", keeps rEFInd from the ESP and rebuilds booster's initramfs); here the scratch disk is made like the guest's and only its file systems' contents are restored")
res = [{"result": v, "need": n, "detail": d} for v, n, d in out]
json.dump(res, open(os.path.join(L, "results-guest.json"), "w"), indent=1)
for v, n, d in out: print(f"{v} {n}: {d}")
