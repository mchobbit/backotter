#!/usr/bin/env python3
"""Judge microos's check on one file system: judge.py LOGDIR FS TOPLOGDIR.
FS is btrfs (the root's type: the fixture with snapper/t-u slots) or vfat
(the ESP's: the FAT fixture).  From tumbleweed's judge.py, with the slots,
the transactional state files and the FAT needs (chimera-uefi's)."""
import json, os, re, sys
L, FS, LB = sys.argv[1], sys.argv[2], sys.argv[3]
def rd(n, d=""):
    try: return open(os.path.join(L, n), errors="replace").read()
    except OSError: return d
def rdb(n, d=""):
    try: return open(os.path.join(LB, n), errors="replace").read()
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
if FS == "btrfs":
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
else:  # vfat: the mount gives every file its owner and mode (fmask, dmask)
    KINDS = {
        "contents, types, sizes, names": ["missing", "extra", "type", "content"],
        "permission bits (vfat: read-only by mode, the mount's fmask and dmask)": ["mode"],
        "ownership (vfat: the mount's)": ["owner"],
        "mtime and atime (FAT: 2 s mtimes, date-only atimes) of files and dirs": ["mtime", "mtime (dirs)", "atime", "atime (dirs)"],
        "xattrs, labels, ACLs (vfat takes none)": ["selinux", "capability", "acl", "user-xattr", "trusted-xattr", "other-xattr"],
        "sparse files and allocation": ["holes lost", "holes lost (sub-4K)", "holes gained", "allocation lost"],
    }
out = []
def line(v, need, detail): out.append((v, need, detail))
full = {d: js(d + ".json") for d in DIRS + ["over-restic", "over-rustic"]}
from importlib.machinery import SourceFileLoader
fsmeta = SourceFileLoader("fsmeta", os.path.join(os.path.dirname(__file__), "fsmeta.py")).load_module()
diffs = {d: (fsmeta.differences(src, full[d]) if full[d] is not None else None) for d in full}
def tree_paths(ps): return [p for p in ps if p == TREE or p.startswith(TREE + "/")]
socket = TREE + "/socket"
unl = set()
for need, kinds in KINDS.items():
    if need.startswith("project IDs") and not any(e.get("projid") for p, e in src.items() if p.startswith(TREE)):
        line("LIMIT", need, f"{FS} takes no project ID (chattr -p fails: btrfs has none): " + ("; ".join(l for l in rd("fixture.err").splitlines() if "-p" in l) or "none set"))
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
nodes = {t: rd(f"nodes-{t}.txt") for t in ("restic", "rustic")}
if FS == "btrfs":
    # Sparse: marks and blocks.
    sp = [p for p, e in src.items() if p.startswith(TREE) and e["type"] == "file" and e.get("holes")]
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
    line("FAIL" if bad or not sp else "PASS", "sparse marks (unix.sparse) and blocks no more than the source's",
         "; ".join(bad)[:900] if bad else (f"{len(sp)} sparse files marked by both tools, blocks <= source's in 4 directions" if sp else "no sparse file in the source"))
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
# Exit status and messages.  On vfat the snapshot's top ancestor, /mnt (a
# tmpfs here, as / would be on the guest), carries a SELinux label that a
# FAT can't take: each restore says so for that directory alone (a LIMIT,
# below), and nothing else may warn.
ANC = re.compile(r"Warning: /mnt/vfat-d\w+/mnt: extended attributes security\.selinux not set, not supported by the target file system"
                 r"|\[WARN\] restore mnt: setting extended attributes failed: security\.selinux: Not supported \(os error 95\)")
anc_seen = set()
def warns(log, pat=r"(?i)warn|error|fail|ignor", also=()):
    out = []
    for x in rd(log).splitlines():
        if not re.search(pat, x): continue
        if FS == "vfat" and ANC.search(x): anc_seen.add(log); continue
        if any(a in x for a in also): continue
        out.append(x)
    return out
bad = [f"{k}={v}" for k, v in status.items() if (k.startswith("backup") or k in [f"restore-{d}" for d in DIRS]) and v != "0"]
msgs = []
for d in DIRS:
    m = warns(f"restore-{d}.log")
    if m: msgs.append(f"{NAMES[d]}: {m[:2]}")
for t in ("restic", "rustic"):
    m = [x for x in rd(f"backup-{t}.log").splitlines() if re.search(r"(?i)warn|error|fail", x)]
    if m: msgs.append(f"{t} backup: {m[:2]}")
line("FAIL" if bad or msgs else "PASS", "backups and restores exit 0 without warnings",
     "; ".join(bad + msgs)[:900] if bad or msgs else "2 backups, 4 restores: exit 0, no warning")
# Each tool's check --read-data of the other's repository.
c1, c2 = status.get("check-restic-on-rustic"), status.get("check-rustic-on-restic")
line("PASS" if c1 == c2 == "0" else "FAIL", "each tool checks the other's repository (check --read-data)",
     "restic-bko on rustic's and rustic-bko on restic's: exit 0" if c1 == c2 == "0" else
     f"restic on rustic's exit {c1}, rustic on restic's exit {c2}: {rd('check-restic-on-rustic.log')[-200:]!r} {rd('check-rustic-on-restic.log')[-200:]!r}")
# Directories above the backed-up path.  On vfat "." is the source FAT's
# root, which has no times (stat shows 1970-01-01); restored, it is a
# directory on a FAT, whose earliest time is 1980-01-01 (a LIMIT, below).
FAT_MIN = 315532800 * 10**9
def root_clamped(d, k, p):
    a = (full[d] or {}).get(p, {})
    return (FS == "vfat" and p == "." and k in ("mtime (dirs)", "atime (dirs)")
            and src.get(".", {}).get(k.split()[0]) == 0 and a.get(k.split()[0]) == FAT_MIN)
clamped = set()
bad = []
for d in DIRS:
    if diffs[d] is None: continue
    for k, ps in diffs[d].items():
        for p in ps:
            if p in ABOVE and root_clamped(d, k, p): clamped.add(f"{NAMES[d]}: {k}")
    ks = sorted({k for k, ps in diffs[d].items() for p in ps if p in ABOVE and not root_clamped(d, k, p)})
    if ks: bad.append(f"{NAMES[d]}: {ks}")  # since round 16 both tools store them with their real metadata
if FS == "vfat":
    su = js("src-up.fat.json") or {}
    for d in DIRS:
        du = js(f"{d}-up.fat.json") or {}
        if su.get(".") != du.get("."): bad.append(f"{NAMES[d]}: up1's FAT attributes {su.get('.')} -> {du.get('.')}")
line("FAIL" if bad else "PASS", "metadata of the directories above the backed-up path",
     "; ".join(bad) if bad else (f"up1, up1/up2 and /mnt/{FS}-src restored exactly in 4 directions (owner 1234:5678, 0710/1755, label, xattrs, ACL, times)" if FS == "btrfs"
                                 else f"up1, up1/up2 and /mnt/{FS}-src restored exactly in 4 directions (times; up1 hidden, archive cleared: {su.get('.')})"))
# Snapshot identity.
nd = rd("nodes.diff").strip()
n = len(nodes["restic"].splitlines())
least = 50 if FS == "btrfs" else 30
line("FAIL" if status.get("nodes") != "0" or n < least else "PASS", "the two tools' snapshots are node-identical",
     ("; ".join(x.strip()[:300] for x in nd.splitlines())[:1200] if status.get("nodes") != "0" else f"{n} nodes at and below the backed-up path, identical") if n >= least else f"only {n} nodes decoded: {rd('nodes-restic.err')[:300]}")
# Backup leaves the source alone.
sa = js("src-after.cmp.json") or {}
d = sa.get("differences", {})
line("FAIL" if d else "PASS", "backup leaves the source's metadata (atimes) alone", json.dumps(d)[:600] if d else "identical before and after both backups")
# Restore over existing.
def fatdiff(a, b, extra=""):
    a, b = a or {}, b or {}
    h = lambda v: hex(v) if isinstance(v, int) else v
    dd = [f"{p} {h(a[p])}->{h(b.get(p))}" for p in sorted(a) if b.get(p) != a[p]]
    dd += [f"{p} not in the source" for p in sorted(set(b) - set(a)) if p != extra]
    return dd
bad = []
xnew = "hidden-dir/new" if FS == "vfat" else ""
for t in ("restic", "rustic"):
    k = f"over-{t}"
    if status.get(f"restore-{k}") != "0": bad.append(f"{NAMES[k]} exit {status.get('restore-' + k)}: {rd('restore-' + k + '.log')[-300:]!r}")
    # rustic notes the file the restore doesn't know (hidden-dir/new).
    m = warns(f"restore-{k}.log", also=("additional entries exist in destination",) if FS == "vfat" else ())
    if m: bad.append(f"{NAMES[k]}: {len(m)} messages: {m[:3]}")
    if FS == "vfat" and "changed 0" not in rd(f"{k}.before"): bad.append(f"{NAMES[k]}: harness: couldn't change the restored files first")
    dd = diffs.get(k)
    if dd is None: bad.append(f"{NAMES[k]}: no record"); continue
    # The new file isn't the snapshot's: a restore leaves what it doesn't
    # know (both tools, no --delete).
    ks = {kk: [p for p in tree_paths(ps) if not (kk == "selinux" and p in unl) and not (kk == "extra" and xnew and p == f"{TREE}/{xnew}")]
          for kk, ps in dd.items() if tree_paths(ps) and not (kk == "missing" and tree_paths(ps) == [socket])}
    ks = {kk: v for kk, v in ks.items() if v}
    if ks: bad.append(f"{NAMES[k]}: " + "; ".join(f"{kk} {v[:3]}" for kk, v in ks.items()))
    if FS == "vfat":
        fd = fatdiff(js("src.fat.json"), js(f"{k}.fat.json"), xnew)
        if fd: bad.append(f"{NAMES[k]}: FAT attributes: {len(fd)} differ, e.g. {fd[:6]}")
if FS == "btrfs":
    what = "changed immutable file, immutable dir, appended append-only file: both tools put back contents, xattrs, mode and flags, exit 0, no warning"
    need = "restore over existing immutable/append-only files (and an immutable dir)"
else:
    what = ("changed read-only files (by attribute and by mode), the shim rewritten, hidden cleared, archive set where cleared, system and read-only cleared on dirs, "
            "a new file in a hidden dir: both tools put back contents, times and FAT attributes, exit 0, no warning")
    need = "restore over existing read-only files and changed FAT attributes"
line("FAIL" if bad else "PASS", need, "; ".join(bad)[:900] if bad else what)
env = rd("env.txt").splitlines()
real = [p for p in src if p.startswith(TREE + "/real/")]
def same_in_all(paths):
    """The restores' differences on PATHS: [] if none."""
    return [f"{NAMES[d]}: {k} {p}" for d in DIRS if diffs[d] for k, ps in diffs[d].items() for p in ps
            if p in paths and not (k == "selinux" and p in unl)]
if FS == "btrfs":
    a0, a1 = rd("avc-fixture", "0").strip(), rd("avc-end", "0").strip()
    eb, ea = rdb("enforce-before").strip(), rdb("enforce-after").strip()
    line("PASS" if eb == ea == "Enforcing" and a0 == a1 else "FAIL", "SELinux enforcing throughout (targeted, MLS), no AVC denial from the tools",
         f"getenforce before/after: {eb}/{ea}; AVC denials in dmesg after the fixture {a0}, after both file systems' backups and restores {a1}")
    line("LIMIT", "birth times (statx btime on btrfs)", "Linux can't set a birth time: neither tool keeps one on Linux (restic BACKOTTER.md, unix.birthtime)")
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
    bad = same_in_all(set(sv))
    line("FAIL" if bad or not sv else "PASS", "nested btrfs subvolume in the backed-up tree (backed up across, restored as a directory)",
         "; ".join(bad)[:600] if bad else (f"{len(sv)} paths (owner 1234:5678, 0750, user xattr, times) restored in 4 directions" if sv else "no subvolume made"))
    # The transactional slots: read-only snapshots, each with a nested
    # writable /etc subvolume, as t-u leaves them.
    SN = TREE + "/real/.snapshots"
    sl = [p for p in src if p == SN or p.startswith(SN + "/")]
    props = rd("slots.txt")
    ro = re.findall(r"^(\S+) ro=true$", props, re.M)
    rw = re.findall(r"^(\S+/etc) ro=false$", props, re.M)
    bad = same_in_all(set(sl))
    if len(ro) < 2 or len(rw) < 2: bad.append(f"the source's slots aren't as made: {props.strip()[:300]!r}")
    usr = [p for p in sl if "/snapshot/usr/" in p and src[p]["type"] == "file"]
    sid = [p for p in usr if src[p]["mode"] & 0o6000]
    cap = [p for p in usr if "security.capability" in src[p]["xattrs"]]
    lab = {src[p]["xattrs"].get("security.selinux") for p in sl if "security.selinux" in src[p]["xattrs"]}
    etc = [p for p in sl if "/snapshot/etc/" in p]
    if not sid or not cap: bad.append(f"the slots' /usr has {len(sid)} set-id and {len(cap)} capability files")
    line("FAIL" if bad else "PASS", "snapper/t-u slots, read-only snapshots (a slot's /usr with set-id, capability and SELinux-labelled files), each with a nested writable /etc subvolume, restored as directories",
         "; ".join(bad)[:900] if bad else
         f"{len(sl)} paths in {len(ro)} read-only slots ({', '.join(ro)}), nested rw {', '.join(rw)}; /usr: {len(sid)} set-id, {len(cap)} with capabilities; "
         f"{len(etc)} /etc paths; {len(lab)} distinct labels; restored exactly in 4 directions")
    # The transactional state: health-checker's and t-u's (and
    # /etc/etc.syncpoint where t-u keeps one; this image has none).
    want = ["health-check.state", "transactional-update.state"]
    st = [p for p in real if p.startswith(TREE + "/real/var-lib-misc/") or p.endswith("/etc.syncpoint")]
    have = [w for w in want if TREE + "/real/var-lib-misc/" + w in src]
    bad = same_in_all(set(st))
    if len(have) < len(want): bad.append(f"not in the source: {sorted(set(want) - set(have))}: {rd('fixture.err')[-300:]!r}")
    line("FAIL" if bad else "PASS", "the transactional state (/var/lib/misc's health-check.state and transactional-update.state)",
         "; ".join(bad)[:900] if bad else f"{len(st)} paths ({', '.join(p[len(TREE) + 6:] for p in st)}) restored exactly (contents, label, times) in 4 directions")
    what = "/"
else:
    if anc_seen:
        line("LIMIT", "the top ancestor's SELinux label on a FAT (/mnt, restored as a directory on vfat)",
             f"the snapshot's /mnt (a tmpfs, labelled mnt_t) carries security.selinux; vfat takes no xattr, so each restore warns for that one directory ({len(anc_seen)} logs), and for nothing else")
    if clamped:
        line("LIMIT", "the source FAT root's times (none, shown as 1970-01-01) restored as a directory on FAT (earliest 1980-01-01)",
             f"FAT's root directory has no times; a FAT directory can't take 1970-01-01, so the restore gets FAT's earliest, 1980-01-01 00:00:00: {sorted(clamped)}")
    line("LIMIT", "birth times (FAT creation times, statx btime on vfat)", "Linux can't set a birth time: neither tool keeps one on Linux (restic BACKOTTER.md, unix.birthtime)")
    # FAT attributes, in every direction.
    sf = js("src.fat.json") or {}
    bad = []
    for d in DIRS:
        fd = fatdiff(sf, js(f"{d}.fat.json"))
        if fd: bad.append(f"{NAMES[d]}: {len(fd)} differ, e.g. {fd[:6]}")
    vals = sorted({hex(v) for v in sf.values() if isinstance(v, int)})
    if len(vals) < 5: bad.append(f"source attributes too few: {vals}")
    line("FAIL" if bad else "PASS", "FAT attributes (readonly, hidden, system, archive set and cleared) on files and dirs",
         "; ".join(bad)[:900] if bad else f"{len(sf)} paths, attributes {vals}, as the source's in 4 directions")
    # The guest's own ESP: shim, GRUB, MokManager, the stub grub.cfg.
    esp = [p for p in real if p.startswith(TREE + "/real/esp")]
    sums = {l.split()[1][2:]: l.split()[0] for l in rd("esp.sha256").splitlines() if len(l.split()) == 2}
    shim = TREE + "/real/esp/EFI/BOOT/bootx64.efi"
    bad = same_in_all(set(esp))
    bad += [f"{p}: copy differs from the guest's" for p, h in sums.items()
            if (src.get(TREE + "/real/esp/" + p) or {}).get("sha256") != h]
    if shim not in src: bad.append("no shim (EFI/BOOT/bootx64.efi) in the source")
    efa = {p: v for p, v in sf.items() if p.startswith("real/esp")}
    line("FAIL" if bad or not sums else "PASS", "the guest's ESP (shim, GRUB, MokManager, the stub grub.cfg) with its FAT attributes and times",
         "; ".join(bad)[:900] if bad else f"{len(esp)} paths ({', '.join(sorted(sums))}), byte-identical to the guest's, FAT attributes {sorted({hex(v) for v in efa.values() if isinstance(v, int)})}, restored exactly in 4 directions")
    what = "/boot/efi, the ESP"
line("PASS" if len(env) > 2 and env[1].split() == env[2].split() else "FAIL", f"scratch {FS} made like the guest's ({what})",
     f"guest: {env[1] if len(env) > 1 else '?'}; scratch: {env[2] if len(env) > 2 else '?'}")
out = [(v, f"[{FS}] {n}", d) for v, n, d in out]
res = [{"result": v, "need": n, "detail": d} for v, n, d in out]
json.dump(res, open(os.path.join(L, "results-guest.json"), "w"), indent=1)
for v, n, d in out: print(f"{v} {n}: " + d.replace("\n", "; "))
