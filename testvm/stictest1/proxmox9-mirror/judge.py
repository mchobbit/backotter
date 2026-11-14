#!/usr/bin/env python3
"""Judge proxmox9's or proxmox9-mirror's check (inner.sh's logs): one
PASS/FAIL/LIMIT line per need, results-guest.json beside them.  The layout
(the root pool's name, vdev layout and partition type, the root dataset,
the other datasets' mount points, the zvols) comes from env.txt's first
line, as inner.sh read it from the guest; the guest's and the scratch
pools' features, properties and datasets from its ds-*, dsprops-*, feat-*,
pool-*, topo-* and mopts-* files."""
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
POOL, ROOTDS, TOPO = lay.get("POOL", "rpool"), lay.get("ROOTDS", "rpool/ROOT/pve-1"), lay.get("TOPO", "")
MOUNTS = [m for m in lay.get("MOUNTS", "").split(",") if m]
ZVOLS = [z for z in lay.get("ZVOLS", "").split(",") if z]
GUEST = rd("guest").strip() or "proxmox9"
src = js("src.json") or {}
TREE = "up1/up2/tree"
ABOVE = {".", "up1", "up1/up2"}
DIRS = ["rr", "uu", "ru", "ur"]
NAMES = {"rr": "restic->restic", "uu": "rustic->rustic", "ru": "restic->rustic", "ur": "rustic->restic",
         "over-restic": "restic over existing", "over-rustic": "rustic over existing"}
# The guest's datasets: REL TYPE CANMOUNT MOUNTPOINT
DS = [l.split() for l in rd("ds-guest").splitlines() if len(l.split()) == 4]
REL_AT = {d[3]: ("" if d[0] == "." else d[0]) for d in DS if d[1] == "filesystem"}   # mount point -> dataset (relative to the pool; "" the pool's own)
TOPOW = " + ".join(("a mirror of %s" % v.split(":")[1] if v.startswith("mirror:") else "one disk" if v == "disk:1" else v) for v in TOPO.split(",") if v)
FSS = f"ZFS: {ROOTDS} as / and {len(MOUNTS)} datasets inside it ({', '.join(MOUNTS)}), in scratch pools made like {POOL} ({TOPOW})"
KINDS = {  # need -> fsmeta difference kinds
    "contents, types, sizes, symlink targets, names": ["missing", "extra", "type", "content", "symlink target"],
    "permission bits incl. setuid/setgid/sticky": ["mode"],
    "ownership incl. ids without names (and a CT's 100000)": ["owner"],
    "mtime and atime (ns) of files, dirs, symlinks": ["mtime", "mtime (dirs)", "mtime (symlinks)", "atime", "atime (dirs)", "atime (symlinks)"],
    "device nodes (incl. rdev 0) and FIFOs": ["device number"],
    "hard links": ["hard links"],
    "file capabilities (security.capability, v2 and v3)": ["capability"],
    "POSIX ACLs access + default (system.posix_acl_*, on the acltype=posix datasets)": ["acl"],
    "user.* xattrs (incl. 60000 bytes, xattr=sa and the default)": ["user-xattr"],
    "trusted.* xattrs": ["trusted-xattr"],
    "other security.* xattrs": ["other-xattr", "selinux"],
    "inode flags (i, a, d)": ["inode flags"],
    "project IDs (and P inheritance)": ["project ID"],
    "ZFS DOS attributes (readonly, hidden, system, nounlink, offline, sparse, archive)": ["ZFS DOS flags"],
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
            ps = [p for p in tree_paths(diffs[d].get(k, [])) if not (k == "missing" and p in sockets)]
            if ps: bad.append(f"{NAMES[d]}: {k} {len(ps)} e.g. {ps[:3]!r}")
    n = sum(1 for p in src if p.startswith(TREE))
    line("FAIL" if bad else "PASS", need, "; ".join(bad)[:900] if bad else f"4 directions, {n} source paths on {FSS}")
# Sparse: marks (ZFS's st_blocks counts what compression left and is a
# guess until the write is committed: fsmeta doesn't compare blocks there).
sp = [p for p, e in src.items() if p.startswith(TREE) and e["type"] == "file" and e.get("holes")]
nodes = {t: rd(f"nodes-{t}.txt") for t in ("restic", "rustic")}
bad = []
for t in nodes:
    names = {x.split("\t")[0]: x for x in nodes[t].splitlines()}
    for p in sp:
        name = p[len(TREE) + 1:]
        if "unix.sparse" not in names.get(name, ""): bad.append(f"{t} snapshot: {name} not marked unix.sparse")
line("FAIL" if bad or not sp else "PASS", "sparse marks (unix.sparse) on files with holes",
     "; ".join(bad)[:900] if bad else f"{len(sp)} files with holes marked by both tools (ZFS's blocks aren't compared: compression=on)")
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
line("FAIL" if bad or TREE + "/socket" not in src else "PASS", "sockets skipped (decision)",
     "; ".join(bad) if bad else (f"{len(sockets)} sockets in the source; in no snapshot, no restore, no message" if TREE + "/socket" in src else "no socket made"))
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
    return (len(ms) == len(MOUNTS) + 1 and not bad), f"{n}: {len(ms)} mounts" + (f", not on relatime: {bad[:2]}" if bad else "") + (rd("remount.err")[:200] and "; " + rd("remount.err")[:200])
# Restore over existing.
bad = []
for t in ("restic", "rustic"):
    k = f"over-{t}"
    if status.get(f"restore-{k}") != "0": bad.append(f"{NAMES[k]} exit {status.get('restore-' + k)}: {rd('restore-' + k + '.log')[-300:]!r}")
    dd = diffs.get(k)
    if dd is None: bad.append(f"{NAMES[k]}: no record"); continue
    ks = {kk: tree_paths(ps) for kk, ps in dd.items() if tree_paths(ps) and not (kk == "missing" and set(tree_paths(ps)) <= sockets)}
    if ks: bad.append(f"{NAMES[k]}: " + "; ".join(f"{kk} {v[:3]}" for kk, v in ks.items()))
    nb = rd(f"{k}.before").count("DOS readonly file changed")
    if nb != len(MOUNTS) + 1: bad.append(f"{NAMES[k]}: {nb} DOS-readonly files changed before, not {len(MOUNTS) + 1}")
    ok, why = on_relatime(f"mopts-rel-{k}")
    if not ok: bad.append(f"{NAMES[k]}: target not on relatime ({why})")
line("FAIL" if bad else "PASS", "restore over existing immutable/append-only files (and an immutable dir) and changed DOS-readonly files",
     "; ".join(bad)[:900] if bad else f"changed immutable file, immutable dir, appended append-only file and a changed DOS-readonly file on each of the {len(MOUNTS) + 1} datasets: "
     "both tools put back contents, xattrs, mode, flags and DOS attributes, exit 0 (targets on relatime)")
line("LIMIT", "birth times (ZFS crtime)", "Linux can't set a birth time: neither tool keeps one on Linux (restic BACKOTTER.md, unix.birthtime)")
# What OpenZFS on Linux refuses (zpl_ioctl_setflags: i a d P only).
fx = rd("fixture.err").splitlines()
refused = sorted({l.split("zfs-refused: ", 1)[1] for l in fx if "zfs-refused: " in l})
took = sorted({l.split("zfs-took: ", 1)[1] for l in fx if "zfs-took: " in l})
if took:
    line("FAIL", "inode flags OpenZFS on Linux refuses (S, A)", f"ZFS took {took}: the fixture's judge doesn't check them (fixture.sh's zflag)")
else:
    line("LIMIT", "inode flags OpenZFS on Linux refuses (S, A)", f"OpenZFS on Linux keeps only immutable, append-only, nodump and project inheritance (i a d P, in the fixture and every restore); chattr refused {', '.join(refused) or '?'}")
noacl = sorted({l.split("noacl: ", 1)[1] for l in fx if "fixture: noacl: " in l})
if noacl:
    line("LIMIT", "POSIX ACLs on the datasets without them (acltype off)", f"{', '.join(noacl)}: acltype off as the guest's (only {ROOTDS} and the CT subvols have acltype=posix): no ACL made there, none restored")
if ZVOLS:
    line("LIMIT", "zvols (VM disks in rpool/data)", f"{', '.join(POOL + z for z in ZVOLS)}: a block device, no files: neither tool backs it up (vzdump's or Proxmox Backup Server's); HoltTest1's load makes it empty with a note")
# The scratch pools made like the guest's.
def words(n): return rd(n).split()
def lines_(n): return [l for l in rd(n).splitlines() if l.strip()]
bad = []
fg, fs_ = words("feat-guest"), words("feat-src")
if not fg or fg != fs_: bad.append(f"features: guest {len(fg)}, scratch {len(fs_)}: only guest {sorted(set(fg) - set(fs_))[:6]} only scratch {sorted(set(fs_) - set(fg))[:6]}")
if words("pool-guest") != words("pool-src") or not words("pool-guest"): bad.append(f"pool properties: guest {rd('pool-guest').strip()}; scratch {rd('pool-src').strip()}")
if rd("topo-guest").strip() != rd("topo-src").strip() or not TOPO: bad.append(f"vdev layout: guest {rd('topo-guest').strip()}; scratch {rd('topo-src').strip()}")
ng = [l.split()[:3] for l in lines_("ds-guest") if l.split()[1] == "filesystem"]
ns_ = [l.split()[:3] for l in lines_("ds-src")]
if ng != ns_: bad.append(f"datasets (name, type, canmount): guest {ng}; scratch {ns_}")
pg = [l for l in lines_("dsprops-guest") if l.split()[0] not in ZVOLS]
for s in ["src"] + DIRS:
    ps = lines_(f"dsprops-{s}")
    if pg != ps: bad.append(f"local properties ({s}): only guest {sorted(set(pg) - set(ps))[:4]}, only scratch {sorted(set(ps) - set(pg))[:4]}")
    if s != "src" and lines_("mopts-guest") != lines_(f"mopts-{s}"): bad.append(f"mount options ({s}): {lines_(f'mopts-{s}')[:3]}")
if lines_("mopts-guest") != lines_("mopts-src"): bad.append(f"mount options: guest {lines_('mopts-guest')}; scratch {lines_('mopts-src')}")
line("FAIL" if bad else "PASS", f"scratch pools made like {POOL} (vdev layout, ashift, features, datasets, local properties, mount options)",
     "; ".join(bad)[:900] if bad else f"{TOPOW} on partitions of type {lay.get('PTYPE')}; {rd('pool-guest').strip()}; {len(fg)} features enabled or active "
     f"({fg[-1] if fg else '?'} among them); {len(ng)} datasets with the guest's {len(pg)} local properties ({'; '.join(pg)[:300]}); the same in the source and {len(DIRS)} restore pools")
# The datasets mounted inside the backed-up path: restored onto each
# target's own.
def inside(m):
    sub = TREE + m
    under = lambda p, s=sub: p == s or p.startswith(s + "/")
    deeper = [TREE + x for x in MOUNTS if x != m and x.startswith(m + "/")]
    own = lambda p: under(p) and not any(under(p, q) for q in deeper)
    nb = sum(1 for p in src if own(p))
    key = m.strip("/").replace("/", "_")
    ns = sum(1 for p in sockets if own(p))
    bad = []
    for d in DIRS:
        if diffs[d] is None: continue
        ks = sorted({k for k, ps in diffs[d].items() for p in ps if own(p) and not (k == "missing" and p in sockets)})
        if ks: bad.append(f"{NAMES[d]}: {ks}")
        c0, c1 = rd(f"count-src-{key}").strip(), rd(f"count-{d}-{key}").strip()
        if not c0.isdigit() or str(int(c0) - ns) != c1: bad.append(f"{NAMES[d]}: {c1 or '?'} paths on the target's own, source {c0 or '?'} ({ns} sockets)")
        want = f"bkst-{d}{REL_AT.get(m, '?')}"
        if rd(f"dev-{d}-{key}").strip() != want: bad.append(f"{NAMES[d]}: on {rd(f'dev-{d}-{key}').strip()!r}, not {want}")
    return ("FAIL" if bad or nb < 30 else "PASS"), ("; ".join(bad)[:900] if bad or nb < 30 else
            f"{nb} paths restored onto each target's own dataset {POOL}{REL_AT.get(m, '?')} (mounted before the restore), in {len(DIRS)} restores; no difference")
for m in MOUNTS:
    v, det = inside(m)
    line(v, f"dataset {POOL}{REL_AT.get(m, '?')} (at {m}) mounted inside the backed-up path", det)
# Real examples from the guest's own files.
want = [f"{TREE}/real/{x}" for x in ("passwd", "su", "dbus-daemon-launch-helper", "shadow", "hostid", "cmdline", "proxmox-boot-uuids",
                                      "pve-cluster/config.db", "apt-sources", "status", "dpkg-info", "systemd-system", "random-seed", "journal")]
miss = [p for p in want if p not in src]
if not any(p.startswith(f"{TREE}/real/boot/vmlinuz-") for p in src): miss.append("a kernel")
ct = [m for m in MOUNTS if re.search(r"/subvol-[0-9]+-disk-[0-9]+$", m)]
ctown = sorted({src[p]["uid"] for m in ct for p in src if p.startswith(f"{TREE}{m}/real/guest/") and src[p].get("uid", 0) >= 100000})
if ct and not ctown: miss.append("a CT's files owned by 100000")
for m in MOUNTS:
    if f"{TREE}{m}/real/guest" not in src: miss.append(f"{m}'s own files")
info = sorted({p.split("/")[-1].split(".")[0] for p in src if p.startswith(f"{TREE}/real/dpkg-info/")})
nreal = sum(1 for p in src if "/real/" in p + "/" and p.startswith(TREE))
line("FAIL" if miss else "PASS", "real examples from the guest's datasets (set-id, proxmox-boot-tool, pmxcfs's config.db, dpkg, systemd, a CT's files)",
     f"missing {miss}" if miss else f"{nreal} paths: set-id passwd/su/mount/dbus-daemon-launch-helper, shadow, hostid, /etc/kernel's cmdline and proxmox-boot-uuids, "
     f"pmxcfs's config.db with its WAL, apt's sources, dpkg's status and info ({', '.join(info)}), systemd's links, random-seed, the journal (ACLs, set-gid), /boot's kernel; "
     f"each dataset's own files ({', '.join(MOUNTS)})" + (f", the CT's owned by {ctown}" if ctown else ""))
# Backups on relatime leave the atimes alone.
relok, reld = on_relatime("mopts-rel-src")
dd = (js("src-rel.cmp.json") or {}).get("differences", {})
other = {k: v for k, v in dd.items() if k != "atime (symlinks)"}
line("FAIL" if other or not relok or js("src-rel.cmp.json") is None else ("LIMIT" if dd else "PASS"),
     "backups on relatime (the guest's datasets mount relatime) leave the source's atimes alone",
     f"source not on relatime for them ({reld})" if not relok else
     json.dumps(other)[:600] if other else ("no comparison" if js("src-rel.cmp.json") is None else
     (f"only symlinks' atimes move: readlink(2) has no O_NOATIME (both BACKOTTER.md), source and its mounts on relatime ({json.dumps(dd)[:300]})" if dd
      else "restic and rustic backups with the source and its mounts on relatime: no atime moved")))
# Every item of the fixture made (a skipped one would pass unseen).
sk = [l.split("skipped: ", 1)[1] for l in fx if "skipped: " in l]
line("FAIL" if sk else "PASS", "fixture made in full", f"harness: skipped {sk[:6]}" if sk else "no fixture item skipped (fixture.err)" + (f"; {', '.join(rd('shims').split(chr(10))).strip(', ')}" if rd("shims").strip() else ""))
line("LIMIT", "whole-disk needs (GPT, the BIOS boot partition, proxmox-boot-tool's unmounted ESPs, pmxcfs at /etc/pve, the pool's bootfs and host id)",
     "out of scope: whole-disk (HoltTest1 recreates the disks and the pool, has proxmox-boot-tool rebuild the ESPs, which aren't backup sources, and skips pmxcfs: its data is config.db, here among the real examples); "
     "here the scratch pools are made like the guest's and only its datasets' contents are restored")
res = [{"result": v, "need": n, "detail": d} for v, n, d in out]
json.dump(res, open(os.path.join(L, "results-guest.json"), "w"), indent=1)
for v, n, d in out: print(f"{v} {n}: {d}")
