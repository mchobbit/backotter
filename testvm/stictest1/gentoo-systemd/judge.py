#!/usr/bin/env python3
"""Judge gentoo's or gentoo-systemd's check (inner.sh's logs): one
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
lay = dict(kv.split("=", 1) for kv in rd("env.txt").splitlines()[0].split()) if rd("env.txt") else {}
ESPN, HOMEX, CRYPT = lay.get("ESPN", "efi"), lay.get("HOMEX") == "1", lay.get("CRYPTPART", "-") != "-"
GUEST = rd("guest").strip() or "gentoo"
src = js("src.json") or {}
TREE = "up1/up2/tree"
ESP, HOME = f"{TREE}/{ESPN}", f"{TREE}/home"
ABOVE = {".", "up1", "up1/up2"}
DIRS = ["rr", "uu", "ru", "ur"]
XF = ["xf", "xf2"] if HOMEX else []
NAMES = {"rr": "restic->restic", "uu": "rustic->rustic", "ru": "restic->rustic", "ur": "rustic->restic",
         "over-restic": "restic over existing", "over-rustic": "rustic over existing",
         "xf": "restic->restic onto an XFS /", "xf2": "rustic->rustic onto an XFS /"}
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
    "other security.* xattrs": ["other-xattr", "selinux"],
    "inode flags (i, a, d, A, S; ext4's D, j, T)": ["inode flags"],
    "project IDs (XFS /home, and P inheritance)": ["project ID"],
    "sparse files and allocation (S3-S5)": ["holes lost", "holes lost (sub-4K)", "holes gained", "allocation lost"],
}
if not HOMEX:
    del KINDS["project IDs (XFS /home, and P inheritance)"]
# ext4's inode flags XFS has no place for: data journaling (j), the Orlov
# top directory (T), dirsync (D).  (Extents, e, fsmeta never compares.)
EXT4_ONLY = {0x4000: "j (data journaling)", 0x20000: "T (top directory)", 0x10000: "D (dirsync)"}
out = []
def line(v, need, detail): out.append((v, need, detail))
recs = DIRS + ["over-restic", "over-rustic"] + XF
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
    fss = "ext4 /, " + ("XFS /home, " if HOMEX else "") + f"the ESP (vfat /{ESPN})"
    line("FAIL" if bad else "PASS", need, "; ".join(bad)[:900] if bad else f"4 directions, {n} source paths on {fss}")
# Sparse: marks and blocks.
sp = [p for p, e in src.items() if p.startswith(TREE) and e["type"] == "file" and e.get("holes")]
nodes = {t: rd(f"nodes-{t}.txt") for t in ("restic", "rustic")}
bad = []
for t in nodes:
    names = {x.split("\t")[0]: x for x in nodes[t].splitlines()}
    for p in sp:
        name = p[len(TREE) + 1:]
        if "unix.sparse" not in names.get(name, ""): bad.append(f"{t} snapshot: {name} not marked unix.sparse")
for d in DIRS + XF:
    for p in sp:
        a = (full[d] or {}).get(p)
        if a and a["blocks"] > src[p]["blocks"] + 8: bad.append(f"{NAMES[d]}: {p} blocks {a['blocks']} > {src[p]['blocks']}")
line("FAIL" if bad or not sp else "PASS", "sparse marks (unix.sparse) and blocks no more than the source's",
     "; ".join(bad)[:900] if bad else f"{len(sp)} sparse files marked by both tools, blocks <= source's in {len(DIRS + XF)} restores")
# Sockets.
bad = []
for t in nodes:
    rel = {p[len(TREE) + 1:] for p in sockets}
    if any(x.split("\t")[0] in rel for x in nodes[t].splitlines()): bad.append(f"socket in {t} snapshot")
for d in DIRS + XF:
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
    return (len(ms) >= 2 and not bad), f"{n}: {len(ms)} mounts" + (f", not on relatime: {bad[:2]}" if bad else "") + (rd("remount.err")[:200] and "; " + rd("remount.err")[:200])
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
     "; ".join(bad)[:900] if bad else "changed immutable file, immutable dir, appended append-only file on ext4" + (" and XFS" if HOMEX else "")
     + "; changed read-only file, cleared hidden/system/read-only/archive attributes on the ESP: both tools put back contents, xattrs, mode, flags and FAT attributes, exit 0 (targets on relatime)")
line("LIMIT", "birth times (statx btime on ext4" + (" and XFS" if HOMEX else "") + ")", "Linux can't set a birth time: neither tool keeps one on Linux (restic BACKOTTER.md, unix.birthtime)")
# The scratch stack made like the guest's.
def same(a, b, what):
    x, y = rd(a).split(), rd(b).split()
    return bool(x == y and x), f"{what}: guest {' '.join(x) or '?'}; scratch {(' '.join(y) or '?') if x != y else 'the same'}"
ok1, d1 = same("feat-root", "feat-src", "ext4 features")
ok2, d2 = same("geo-root", "geo-src", "inode and block size, default mount options, flex_bg, hash")
ok3, d3 = same("mopts-root", "mopts-src", "mount options")
line("PASS" if ok1 and ok2 and ok3 else "FAIL", "scratch ext4 on a logical volume made and mounted like / (vg0/root)", f"{d1}; {d2}; {d3}")
if HOMEX:
    ok1, d1 = same("feat-home", "feat-srchome", "xfs_info features")
    ok2, d2 = same("mopts-home", "mopts-srchome", "mount options")
    line("PASS" if ok1 and ok2 else "FAIL", "scratch XFS on a logical volume made and mounted like /home (vg0/home)", f"{d1}; {d2}; {rd('mkfs-xfs.log').strip()[:200]}")
ok1, d1 = same("fat-esp", "fat-src", "FAT type, sector and cluster size, FATs, reserved sectors")
ok2, d2 = same("mopts-esp", "mopts-srcesp", "mount options")
line("PASS" if ok1 and ok2 else "FAIL", f"scratch FAT32 ESP made and mounted like /{ESPN}", f"{d1}; {d2}")
ok1, d1 = same("lvm-root", "lvm-src", "PV format, data offset, metadata areas/size; VG extent size, metadata copies, attributes; LV attributes, segment type, stripes, read-ahead, layout")
g, sg = [l for l in rd("gpt-root").split("\n") if l], [l for l in rd("gpt-scratch").split("\n") if l]
types = lambda t: [l.split()[2] for l in t[1:] if len(l.split()) == 3]
gt, st_ = types(g), types(sg)
ok2 = g[:1] == sg[:1] == ["gpt"] and gt and st_ and g[1:2] == sg[1:2] and st_[-1] == gt[-1] and set(st_) == set(gt)
ok3, d3 = (True, "") if not CRYPT else same("luks-root", "luks-scratch", "LUKS2 header (version, data cipher, offset, sector, key bits, PBKDF, time, memory, threads)")
line("PASS" if ok1 and ok2 and ok3 else "FAIL", "scratch partitions" + (", LUKS2" if CRYPT else "") + " and volume group made like the guest's",
     f"{d1}; GPT: guest {' | '.join(g)}; scratch {' | '.join(sg)}" + (f"; {d3}" if CRYPT else ""))
# The file systems mounted inside the backed-up path: restored onto each
# target's own.
def inside(sub, name, devre):
    under = lambda p: p == sub or p.startswith(sub + "/")
    nb = sum(1 for p in src if under(p))
    ns = sum(1 for p in sockets if under(p))   # the sockets no restore makes
    bad = []
    for d in DIRS + XF:
        if diffs[d] is None: continue
        ks = sorted({k for k, ps in diffs[d].items() for p in ps if under(p) and not (k == "missing" and p in sockets)})
        if ks: bad.append(f"{NAMES[d]}: {ks}")
        m = sub.rsplit("/", 1)[1]
        c0, c1 = rd(f"count-src-{m}").strip(), rd(f"count-{d}-{m}").strip()
        if not c0.isdigit() or str(int(c0) - ns) != c1: bad.append(f"{NAMES[d]}: {c1 or '?'} paths on the target's own, source {c0 or '?'} ({ns} sockets)")
        if not re.fullmatch(devre, rd(f"dev-{d}-{m}").strip()): bad.append(f"{NAMES[d]}: on {rd(f'dev-{d}-{m}').strip()!r}")
    return ("FAIL" if bad or nb < 30 else "PASS"), ("; ".join(bad)[:900] if bad or nb < 30 else
            f"{nb} paths restored onto each target's own {name}, mounted before the restore, in {len(DIRS + XF)} restores; no difference")
v, det = inside(ESP, "ESP (a partition)", r"/dev/vdb[0-9]+")
line(v, f"the ESP (vfat, a partition) mounted inside the backed-up path, as /{ESPN}", det)
if HOMEX:
    v, det = inside(HOME, "XFS logical volume", r"/dev/mapper/bkst-(rr|uu|ru|ur|xf)--home")
    line(v, "a second LV (XFS, like /home) mounted inside the backed-up path", det)
# FAT attributes, each direction.
bad = []
for d in DIRS + XF:
    fd = fatcmp("src.fat.json", f"{d}.fat.json")
    if fd: bad.append(f"{NAMES[d]}: {len(fd)} differ, e.g. {fd[:4]}")
fa = js("src.fat.json") or {}
line("FAIL" if bad or len(fa) < 30 else "PASS", "FAT attributes (readonly, hidden, system, archive) on files and dirs, the guest's ESP's among them",
     "; ".join(bad)[:900] if bad or len(fa) < 30 else f"{len(fa)} paths, attributes {sorted({hex(v) for v in fa.values() if isinstance(v, int)})}, identical in {len(DIRS + XF)} restores")
# Real examples from the guest's own files.
# (/etc/lvm/backup and archive only if the guest has them: the guests' VG
# was made from the Arch ISO, so an LVM command run on the guest writes its
# first backup, not the build.)
want = [f"{TREE}/real/etc-lvm/lvm.conf", f"{TREE}/real/etc-lvm/profile", f"{TREE}/real/vdb",
        f"{TREE}/real/ping", f"{TREE}/real/shadow", f"{TREE}/real/make.conf", f"{ESP}/real/esp/EFI"]
want += [f"{TREE}/real/systemd-system"] if "systemd" in GUEST else [f"{TREE}/real/runlevels"]
if ESPN != "boot": want += [f"{TREE}/real/boot/grub", f"{TREE}/real/usr_lib_kernel_install.conf"]
else: want += [f"{ESP}/real/esp/grub", f"{TREE}/real/random-seed", f"{TREE}/real/journal", f"{TREE}/real/crypttab", f"{TREE}/real/etc_kernel_install.conf"]
miss = [p for p in want if p not in src]
vdb = sorted({p.split("/")[5] for p in src if p.startswith(f"{TREE}/real/vdb/")})
nreal = sum(1 for p in src if p.startswith(f"{TREE}/real/") or p.startswith(f"{ESP}/real/"))
line("FAIL" if miss or status.get("copy-etc-lvm") != "0" else "PASS", "real examples from the guest's / (LVM, Portage, set-id), its /boot and ESP",
     f"missing {miss}" if miss else f"{nreal} paths: /etc/lvm ({'the VG' + chr(39) + 's metadata backup and archive' if f'{TREE}/real/etc-lvm/backup' in src else 'lvm.conf and profiles; the guest has no metadata backup yet'}), Portage's package database ({', '.join(vdb)}), make.conf, set-id ping/su/passwd, shadow, the init's links, /boot and the ESP")
# Backups on relatime leave the atimes alone.
relok, reld = on_relatime("mopts-rel-src")
dd = (js("src-rel.cmp.json") or {}).get("differences", {})
other = {k: v for k, v in dd.items() if k != "atime (symlinks)"}
line("FAIL" if other or not relok or js("src-rel.cmp.json") is None else ("LIMIT" if dd else "PASS"),
     "backups on relatime (the kernel's default, the guest's ESP's) leave the source's atimes alone",
     f"source not on relatime for them ({reld})" if not relok else
     json.dumps(other)[:600] if other else ("no comparison" if js("src-rel.cmp.json") is None else
     (f"only symlinks' atimes move: readlink(2) has no O_NOATIME (both BACKOTTER.md), source and its mounts on relatime ({json.dumps(dd)[:300]})" if dd
      else "restic and rustic backups with the source and its mounts on relatime: no atime moved")))
# Every item of the fixture made (a skipped one would pass unseen).
sk = [l.split("skipped: ", 1)[1] for l in rd("fixture.err").splitlines() if "skipped: " in l]
line("FAIL" if sk else "PASS", "fixture made in full", f"harness: skipped {sk[:6]}" if sk else "no fixture item skipped (fixture.err)")
if HOMEX:
    # XFS's own per-file options.  Its preallocated flag (p, 0x2) isn't one
    # of a file's options: a dense restore preallocates (S5) and so gives
    # every file it, a sparse one none (accepted, both BACKOTTER.md).
    def nop(l):
        l = re.sub(r"(fsxattr\.xflags = )0x([0-9a-fA-F]+)", lambda m: m.group(1) + hex(int(m.group(2), 16) & ~2), l)
        return re.sub(r"\[(.)p", r"[\1-", l)
    sx = [nop(l) for l in rd("src.xfsx").splitlines()]
    bad = [f"source: {l}" for l in sx if "fsxattr.xflags" not in l]
    for d in DIRS + ["over-restic", "over-rustic"] + XF:
        dx = [nop(l) for l in rd(d + ".xfsx").splitlines()]
        if not dx: bad.append(f"{NAMES[d]}: no record"); continue
        ch = [f"{a} -> {b}" for a, b in zip(sx, dx) if a != b]
        if ch: bad.append(f"{NAMES[d]}: {ch[:4]}")
    line("FAIL" if bad or not sx else "PASS", "XFS per-file options on /home (fsxattr: extent size hints and inheritance, nodefrag f, filestream S)",
         "; ".join(bad)[:900] if bad else f"identical in {len(DIRS + XF) + 2} restores (but p, preallocated: S5, accepted)")
    # The cross-file-system case: each tool's snapshot of the ext4 tree
    # restored onto a stack whose / is XFS (its /home and ESP as before).
    # Judged as the four directions, but for what ext4 holds and XFS can't,
    # or holds its own way:
    #   - ext4's own inode flags (EXT4_ONLY): each such path keeps every
    #     other flag;
    #   - device numbers beyond XFS's 32-bit encoding (major < 2^14, minor
    #     < 2^18; ext4 takes Linux's 12+20 bits): no such node can be made,
    #     and the tool says so for that path only (restic then exits 1);
    #   - XFS's trusted.SGI_ACL_FILE/DEFAULT, its own copies of a path's
    #     POSIX ACLs, which ext4 has no twin of.
    names = lambda f: [n for b, n in EXT4_ONLY.items() if f & b]
    xfs_dev = lambda r: r[0] < (1 << 14) and r[1] < (1 << 18)
    TWIN = {"trusted.SGI_ACL_FILE": "system.posix_acl_access", "trusted.SGI_ACL_DEFAULT": "system.posix_acl_default"}
    for d in XF:
        bad, dropped, nodev, twins = [], {}, [], 0
        if rd(f"fstype-{d}").strip() != "xfs": bad.append(f"target / is {rd(f'fstype-{d}').strip() or '?'}, not XFS")
        dd = diffs.get(d)
        if dd is None: bad.append("no record"); dd = {}
        dst = full[d] or {}
        for k, ps in dd.items():
            ps = [p for p in tree_paths(ps) if not (k == "missing" and p in sockets)]
            if k == "inode flags":
                for p in ps:
                    sf, df = src[p].get("flags") or 0, dst[p].get("flags") or 0
                    if (sf & ~sum(EXT4_ONLY) & ~fsmeta.FLAGS_FS_INTERNAL) == (df & ~fsmeta.FLAGS_FS_INTERNAL): dropped[p[len(TREE) + 1:]] = names(sf)
                    else: bad.append(f"inode flags {p}: {hex(sf)} -> {hex(df)}")
            elif k == "missing":
                for p in ps:
                    if src[p]["type"] in ("chardev", "blockdev") and not xfs_dev(src[p]["rdev"]): nodev.append(p)
                    else: bad.append(f"missing {p}")
            elif k == "trusted-xattr":
                for p in sorted(set(ps)):
                    bx, ax = src[p].get("xattrs") or {}, dst[p].get("xattrs") or {}
                    other = [n for n in sorted(set(bx) | set(ax)) if n.startswith("trusted.") and bx.get(n) != ax.get(n)
                             and not (n in TWIN and n not in bx and TWIN[n] in bx)]
                    if other: bad.append(f"trusted-xattr {p}: {other[:3]}")
                    else: twins += 1
            elif ps: bad.append(f"{k} {len(ps)} e.g. {ps[:3]!r}")
        msg = [x for x in rd(f"restore-{d}.log").splitlines() if re.search(r"(?i)warn|error|fail|ignor", x)]
        nd = [p[len("up1/up2/tree/"):] for p in nodev]
        about = lambda x: any(re.search(r"(^|/)" + re.escape(n) + r"\b", x) for n in nd)
        unexp = [x for x in msg if not (about(x) or re.search(r"(?i)not supported|unsupported|EOPNOTSUPP|^Fatal: There were %d errors$" % len(nodev), x))]
        if unexp: bad.append(f"messages {unexp[:2]}")
        rc = status.get(f"restore-{d}")
        if rc != "0" and not (rc == "1" and nodev and d == "xf" and not unexp): bad.append(f"exit {rc}")
        exf = sorted({f for fl in dropped.values() for f in fl})
        n = sum(1 for p in src if p.startswith(TREE))
        line("FAIL" if bad else "PASS", f"cross-file-system restore: the ext4 / tree onto an XFS / ({NAMES[d].split(' onto')[0]})",
             "; ".join(bad)[:900] if bad else
             f"{n} source paths, every need as in the four directions but ext4's own: inode flags {', '.join(exf) or 'none'} not kept on {sorted(dropped)} (XFS has none)"
             + f"; {len(nodev)} device node(s) XFS can't encode not made ({', '.join(f'{p[len(TREE) + 1:]} {src[p]["rdev"]}' for p in nodev)})"
             + f"; {twins} path(s) with XFS's trusted.SGI_ACL_* twins of their ACLs; exit {rc}, messages: {[m[:160] for m in msg[:2]]}")
line("LIMIT", "whole-disk needs (GPT, ESP's loaders, " + ("LUKS2, " if CRYPT else "") + "PV/VG/LV metadata, boot loader)",
     "out of scope: whole-disk (HoltTest1 recreates the disk and the " + ("LUKS and " if CRYPT else "") + "LVM stack); here the scratch stack is made like the guest's and only its file systems' contents are restored")
res = [{"result": v, "need": n, "detail": d} for v, n, d in out]
json.dump(res, open(os.path.join(L, "results-guest.json"), "w"), indent=1)
for v, n, d in out: print(f"{v} {n}: {d}")
