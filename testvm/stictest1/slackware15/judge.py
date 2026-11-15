#!/usr/bin/env python3
"""SticTest1 slackware15 and slackware15-uefi: judge inner.sh's records, one
PASS/FAIL/LIMIT line per need.

  judge.py LOGDIR

solus49's judge (ubuntu26-uefi's needs: fsmeta.differences() of every path)
for Slackware's two layouts, read from LOGDIR/layout ("ROOTFS ESP"):
slackware15's ext4 / (with the feature ext4's casefolding and project IDs)
on MBR, and slackware15-uefi's XFS / (project IDs and XFS's per-file options
on it) with the ESP at /boot/efi inside the backed-up tree (its FAT
attributes by fatattr.py) on GPT; the scratch disk's layout against the
guest's (the swap partition made by mkswap); and Slackware's own files as
real examples: the BSD-style rc scripts (a service is on when its script
is executable), pkgtools' database, LILO's lilo.conf and map with the huge
kernel, or ELILO's files on the ESP with the generic kernel and mkinitrd's
initrd and tree.  Python 3.9 (Slackware 15's).
"""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fsmeta  # noqa: E402

L = sys.argv[1]
DIRS = ["restic-restic", "rustic-rustic", "restic-rustic", "rustic-restic"]
OVER = ["restic-restic", "rustic-rustic"]


def rd(n, default=""):
    try:
        return open(os.path.join(L, n), errors="replace").read()
    except OSError:
        return default


def js(n):
    try:
        return json.load(open(os.path.join(L, n)))
    except Exception:  # noqa: BLE001
        return None


lay = rd("layout").split()
ROOTFS = lay[0] if lay else "?"
HASESP = len(lay) > 1 and lay[1] == "1"
XFS = ROOTFS == "xfs"
FSN = "XFS" if XFS else ROOTFS
ESP = "boot/efi"
kv = rd("kver").split()
KFLAV, KVER = (kv + ["?", "?"])[:2]

# need, fsmeta kinds, path filter (None: any path)
NEEDS = [
    ("File contents, sizes, types and names (non-UTF-8, 250 chars, newline, deep, empty files and dirs"
     + ("; FAT's names)" if HASESP else ")"), {"missing", "extra", "type", "content"}, None),
    ("Symlinks: targets and their own times", {"symlink target", "mtime (symlinks)", "atime (symlinks)"}, None),
    ("Permission bits incl. setuid, setgid and sticky", {"mode"}, None),
    ("Numeric ownership incl. ids with no name and above 2^31", {"owner"}, None),
    ("Modification times (ns, 1970, 2100; files and dirs" + ("; FAT's 2 s and its limits)" if HASESP else ")"),
     {"mtime", "mtime (dirs)"}, None),
    ("Access times (files and dirs" + ("; FAT's date-only)" if HASESP else ")"), {"atime", "atime (dirs)"}, None),
    ("Device nodes (incl. rdev 0) and FIFOs", {"device number"}, r"(chardev|blockdev|rdev0|fifo|/p$|/dev/)"),
    ("Hard links", {"hard links"}, None),
    ("Extended attributes user.* (binary, 3000-byte, empty values; dirs)", {"user-xattr"}, None),
    ("Extended attributes trusted.* (files, symlinks, FIFOs, devices"
     + ("; XFS's trusted.SGI_ACL_* twins of the ACLs, if listed)" if XFS else ")"), {"trusted-xattr"}, None),
    ("Extended attributes security.* other than capabilities (files, symlinks)", {"other-xattr", "selinux"}, None),
    ("File capabilities (security.capability)", {"capability"}, None),
    ("POSIX ACLs, access and default", {"acl"}, None),
    (("Linux inode flags (i, a, d, A, S, D: those XFS keeps)" if XFS else
      "Linux inode flags (i, a, d, A, S, D, j, T and others ext4 keeps)"), {"inode flags"}, r"^(?!.*casefold)"),
]
if not XFS:
    NEEDS.append(("Casefolding (F) on ext4 -O casefold", {"inode flags"}, r"casefold"))
NEEDS += [
    (("Project IDs and inheritance (P) on XFS (projid32bit: an ID above 2^31)" if XFS else
      "Project IDs and inheritance (P) on ext4 -O project"), {"project ID"}, None),
    ("Sparse files restored with holes; preallocated, zero-filled and swap files stay allocated",
     {"holes lost", "holes lost (sub-4K)", "holes lost (sub-block)", "holes gained", "allocation lost"}, None),
]


def diff(before, after):
    try:
        # Sockets are judged on their own (both tools skip them): fsmeta's
        # --skip leaves out directories only.
        b = {k: v for k, v in json.load(open(os.path.join(L, before))).items() if v["type"] != "socket"}
        a = {k: v for k, v in json.load(open(os.path.join(L, after))).items() if v["type"] != "socket"}
        return fsmeta.differences(b, a)
    except Exception as e:  # noqa: BLE001
        return {"(unreadable)": [f"{after}: {e}"]}


def summary(d):
    return "; ".join(f"{k} {len(v)}: {', '.join(map(repr, v[:3]))}" for k, v in d.items() if v)[:500]


def exitcode(p):
    m = re.search(r"exit (\d+)", rd(p))
    return int(m.group(1)) if m else None


out = []


def line(v, need, det):
    out.append(f"{v} {need}: {det}".replace("\n", " ")[:900])


status = dict(l.split() for l in rd("status").splitlines() if len(l.split()) == 2)
src = js("src0.json") or {}
diffs = {d: diff("src0.json", f"{d}.json") for d in DIRS if os.path.exists(os.path.join(L, f"{d}.json"))}
missing_dirs = [d for d in DIRS if d not in diffs]
if not src:
    line("FAIL", "check ran", "no source record (src0.json)")
if ROOTFS not in ("ext4", "xfs"):
    line("FAIL", "check ran", f"no layout read (layout: {rd('layout').strip() or '?'})")
nesp = sum(1 for p in src if p == ESP or p.startswith(ESP + "/")) if HASESP else 0

for need, kinds, flt in NEEDS:
    bad = []
    for d, dif in diffs.items():
        for k, paths in dif.items():
            if k not in kinds:
                continue
            ps = [p for p in paths if flt is None or re.search(flt, p)]
            if ps:
                bad.append(f"{d}: {k}: {', '.join(map(str, ps[:4]))}{' ...' if len(ps) > 4 else ''}")
    if missing_dirs:
        bad.append(f"no result for {', '.join(missing_dirs)}")
    line("FAIL" if bad else "PASS", need, "; ".join(bad) if bad else
         f"identical in all four directions ({len(src)} paths" + (f", {nesp} on the ESP)" if HASESP else ")"))
# Anything fsmeta saw that no need above names
known = set().union(*(k for _, k, _ in NEEDS))
other = [f"{d}: {k} {v[:3]}" for d, dif in diffs.items() for k, v in dif.items() if v and k not in known]
if other:
    line("FAIL", "Other metadata differences", "; ".join(other))

# Backups: exit 0, no warnings
for t in ("restic", "rustic"):
    bad = []
    m = [x for x in rd(f"repo-{t}.backup").splitlines() if re.search(r"(?i)warn|error|signal|panic|floating", x) and "[INFO]" not in x]
    if status.get(f"backup-{t}") != "0" or m:
        bad.append(f"{t}: exit {status.get(f'backup-{t}')}: {' '.join(m[:3])}")
    line("FAIL" if bad else "PASS", f"{t}-bko runs natively and backs up the fixture",
         "; ".join(bad) if bad else "exit 0, no warnings (owners with no name, big ids" + (", the ESP inside the tree" if HASESP else "") + " included)")

# Backups leave the source's atimes alone (strictatime), per tool
for t in ("restic", "rustic"):
    d = diff(f"src-before-atime-{t}.json", f"src-after-atime-{t}.json")
    d = {k: v for k, v in d.items() if v and k != "atime (symlinks)"}
    ok = not d and status.get(f"backup-atime-{t}") == "0"
    line("PASS" if ok else "FAIL", f"{t}-bko backup leaves source atimes alone (strictatime)",
         f"same ({len(src)} paths; backup exit 0); symlinks' atimes move (readlink), accepted in S1, the snapshot keeps the earlier ones"
         if ok else f"backup exit {status.get(f'backup-atime-{t}')}: {summary(d)}")

# Same repository format
nr = rd("nodes.restic")
ndiff = [l for l in rd("nodes.diff").splitlines() if l.strip() and "(not a check)" not in l]
ok = bool(nr.strip()) and status.get("nodes") == "0"
line("PASS" if ok else "FAIL", "Same repository format: snapshots node-identical",
     f"{len(nr.splitlines())} nodes at and below the backup path" if ok else
     (" ".join(ndiff[:8]) or f"no nodes decoded: {rd('nodes.err')[:300]}"))
bad = []
for k, who in (("check-restic-on-rustic", "restic check --read-data of rustic's repository"),
               ("check-rustic-on-restic", "rustic check --read-data of restic's repository")):
    m = [x for x in rd(k + ".log").splitlines() if re.search(r"(?i)^error|\[ERROR\]|\[WARN\]|fatal", x)]
    if status.get(k) != "0" or m:
        bad.append(f"{who}: exit {status.get(k)} {m[:2]}")
line("FAIL" if bad else "PASS", "Same repository format: each tool checks the other's repository",
     "; ".join(bad) if bad else "restic check --read-data and rustic check --read-data exit 0, no error")

# The guest's own files: real examples at their own paths, and guest/ copies
env = rd("env.txt")


def under(*pfx):
    return lambda p: any(str(p) == x or str(p).startswith(x + "/") for x in pfx)


def baddiffs(sel):
    return [f"{d}: {k}: {', '.join(map(str, ps[:3]))}" for d, dif in diffs.items()
            for k, paths in dif.items() for ps in [[p for p in paths if sel(p)]] if ps]


def same_bytes(a, b):
    ha, hb = src.get(a, {}).get("sha256"), src.get(b, {}).get("sha256")
    return bool(ha) and ha == hb


rcd, pkdb, boot = under("etc/rc.d"), under("var/lib/pkgtools", "var/log/packages", "var/log/scripts", "var/log/setup"), under("boot")
inner = under("guest", "etc", "bin", "sbin", "usr", "var/log")
sel = lambda p: inner(p) and not rcd(p) and not pkdb(p)  # noqa: E731 (those have needs of their own)
bad = baddiffs(sel)
want = ["etc/shadow", "etc/passwd", "etc/group", "etc/fstab", "etc/inittab", "etc/os-release", "etc/slackware-version",
        "bin/su", "bin/mount", "bin/ping", "usr/bin/passwd", "usr/bin/sudo", "usr/bin/crontab"]
miss = [p for p in want if p not in src]
setid = sorted(p for p, e in src.items() if re.match(r"(usr|bin|sbin|etc)/", p) and e["type"] == "file" and e["mode"] & 0o6000)
# Set-id programs owned by others than root (uucp's cu, daemon's at, messagebus's helper's group)
odd = sorted(p for p in setid if src[p].get("uid") or src[p].get("gid"))
caps = [p for p in src if "security.capability" in src[p]["xattrs"] and not p.startswith("root/bko-torture")]
ng = sum(1 for p in src if p.startswith("guest/") and src[p]["type"] == "file")
nreal = sum(1 for p in src if sel(p))
envcaps = re.search(r"files with capabilities: (.*)", env)
capnote = (f"capabilities: the guest's {len(caps)}" if caps else
           f"capabilities: the guest has none ({envcaps.group(1) if envcaps else '?'}; torture.sh's cap-binary is checked)")
logs = [p for p in ("var/log/messages", "var/log/syslog", "var/log/secure", "var/log/wtmp", "var/log/lastlog") if p in src]
fail = bool(bad or miss or len(setid) < 5)
line("FAIL" if fail else "PASS",
     "The guest's own files (set-id programs, shadow, passwd, group, fstab, inittab, os-release, slackware-version, syslog's files)",
     "; ".join(bad)[:600] + (f" missing {miss}" if miss else "") + f" set-id {len(setid)}"
     if fail else
     f"{nreal} paths identical in all four directions: {len(setid)} set-id programs ({len(odd)} not root:root), {capnote}, "
     f"logs {logs or 'none'}, {ng} guest/ copies (hard-linked, with holes)")

# Slackware's BSD-style init: /etc/rc.d, a service on when its rc.* is executable
R = "etc/rc.d"
scripts = {p: e for p, e in src.items() if re.fullmatch(R + r"/rc\.[^/]+", p) and e["type"] == "file"}
on = sorted(p[len(R) + 1:] for p, e in scripts.items() if e["mode"] & 0o100)
off = sorted(p[len(R) + 1:] for p, e in scripts.items() if not e["mode"] & 0o100)
want = [f"{R}/{x}" for x in ("rc.S", "rc.M", "rc.K", "rc.6", "rc.0", "rc.inet1", "rc.inet1.conf", "rc.sshd")]
miss = [p for p in want if p not in src]
r0 = src.get(f"{R}/rc.0", {})
if r0 and (r0.get("type") != "symlink" or r0.get("target") != "rc.6"):
    miss.append(f"rc.0 -> rc.6 ({r0.get('type')} {r0.get('target', '')})".strip())
if "rc.sshd" not in on:
    miss.append("rc.sshd not executable (sshd is on)")
bad = baddiffs(rcd)
nrc = sum(1 for p in src if rcd(p))
fail = bool(bad or miss or not off)
line("FAIL" if fail else "PASS",
     "Slackware's BSD-style init (/etc/rc.d: rc.S, rc.M, rc.K, rc.6 and rc.0 -> rc.6, rc.inet1 and rc.inet1.conf; each service on or off by its script's exec bit)",
     "; ".join(bad)[:600] + (f" missing {miss}" if miss else "") + ("" if off else " no rc.* script off (none without its exec bit)")
     if fail else
     f"{nrc} paths identical in all four directions: {len(on)} rc.* executable (on: {' '.join(x for x in on if x in ('rc.sshd', 'rc.syslog', 'rc.inet1', 'rc.udev', 'rc.crond'))} ...), "
     f"{len(off)} not ({' '.join(off[:6])}{' ...' if len(off) > 6 else ''}), modes kept")

# pkgtools' database
P = "var/lib/pkgtools/packages"
pk = sorted(p[len(P) + 1:] for p, e in src.items() if p.startswith(P + "/") and e["type"] == "file")
need = {"aaa_base", "pkgtools", "sysvinit", f"kernel-{KFLAV}", "kernel-modules", "elilo" if HASESP else "lilo", "e2fsprogs"}
have = {re.sub(r"-[^-]+-[^-]+-[^-]+$", "", x) for x in pk}
miss = sorted(need - have)
vlp = src.get("var/log/packages", {})
if vlp.get("type") != "symlink" or vlp.get("target") != "../lib/pkgtools/packages":
    miss.append(f"var/log/packages -> ../lib/pkgtools/packages ({vlp.get('type', 'missing')} {vlp.get('target', '')})".strip())
bad = baddiffs(pkdb)
nv = sum(1 for p in src if pkdb(p))
fail = bool(bad or miss or len(pk) < 100)
line("FAIL" if fail else "PASS",
     "pkgtools' database (/var/lib/pkgtools/packages, one file a package, and scripts; /var/log/packages -> ../lib/pkgtools/packages)",
     "; ".join(bad)[:600] + (f" missing {miss}" if miss else "") + f" {len(pk)} package files"
     if fail else f"{nv} paths identical in all four directions ({len(pk)} package files, the kernel's, the loader's and pkgtools' own among them)")

# The boot files
K = f"vmlinuz-{KFLAV}-{KVER}"
links = {"boot/vmlinuz": K, f"boot/vmlinuz-{KFLAV}": K}
wrong = [f"{p} -> {src[p].get('target')}" for p, t in links.items() if p in src and src[p].get("target") != t]
bootsel = lambda p: boot(p) and not (HASESP and (p == ESP or p.startswith(ESP + "/")))  # noqa: E731
bad = baddiffs(bootsel)
nb = sum(1 for p in src if bootsel(p))
if not HASESP:
    # LILO: lilo.conf, the map (the kernel's sector list), the huge kernel; no initrd
    want = ["etc/lilo.conf", "boot/map", f"boot/{K}", "boot/vmlinuz", f"boot/vmlinuz-{KFLAV}", "boot/System.map", "boot/config", "boot/slack.bmp"]
    miss = [p for p in want if p not in src]
    saved = sorted(p for p in src if re.fullmatch(r"boot/boot\.[0-9a-fA-F]{4}", p))
    initrd = [p for p in ("boot/initrd.gz", "boot/initrd-tree") if p in src]
    bad += baddiffs(under("etc/lilo.conf"))
    fail = bool(bad or miss or wrong)
    line("FAIL" if fail else "PASS",
         "LILO's boot files and the huge kernel (/etc/lilo.conf, /boot/map, vmlinuz-huge-K with its vmlinuz, vmlinuz-huge, System.map and config symlinks, slack.bmp, LILO's saved boot sector)",
         "; ".join(bad)[:600] + (f" missing {miss}" if miss else "") + (f" symlinks {wrong}" if wrong else "")
         if fail else
         f"{nb + 1} paths identical in all four directions (kernel {KFLAV} {KVER}; the symlinks' targets kept; the map {src['boot/map']['size']} bytes; "
         f"saved boot sector {saved or 'none'}; " + ("no initrd, as the huge kernel boots" if not initrd else f"an initrd too: {initrd}") + ")")
else:
    # mkinitrd: /boot/initrd.gz and its tree (busybox links, device nodes, command_line), the generic kernel
    T = "boot/initrd-tree"
    want = [f"boot/{K}", "boot/vmlinuz", f"boot/vmlinuz-{KFLAV}", "boot/System.map", "boot/config", "boot/initrd.gz", T, f"{T}/init", f"{T}/bin/busybox"]
    miss = [p for p in want if p not in src]
    tl = sum(1 for p in src if p.startswith(T + "/") and src[p]["type"] == "symlink")
    tdev = sum(1 for p in src if p.startswith(T + "/") and src[p]["type"] in ("chardev", "blockdev", "char", "block"))
    cl = f"{T}/command_line" in src
    fail = bool(bad or miss or wrong or tl < 50)
    line("FAIL" if fail else "PASS",
         "mkinitrd's initrd and tree and the generic kernel (/boot/initrd.gz; /boot/initrd-tree with its busybox links and device nodes; vmlinuz-generic-K with its symlinks)",
         "; ".join(bad)[:600] + (f" missing {miss}" if miss else "") + (f" symlinks {wrong}" if wrong else "") + f" initrd-tree: {tl} symlinks"
         if fail else
         f"{nb} paths identical in all four directions (kernel {KFLAV} {KVER}; initrd-tree: {tl} symlinks, {tdev} device nodes, "
         + ("its command_line" if cl else "no command_line file") + ")")
    # The ESP's ELILO files
    want = [f"{ESP}/EFI/Slackware/{x}" for x in ("elilo.efi", "elilo.conf", "vmlinuz", "initrd.gz")]
    miss = [p for p in want if p not in src]
    gs = dict(reversed(l.split("  ", 1)) for l in rd("esp-guest.sha256").splitlines() if "  " in l)
    copyerr = [p for p, h in gs.items() if src.get(p, {}).get("sha256") != h]
    chain = under(f"{ESP}/EFI/Slackware", f"{ESP}/EFI/BOOT")
    bad = baddiffs(chain)
    fa = js("src.fat.json") or {}
    for d in DIRS:
        b = js(f"{d}.fat.json") or {}
        fd = [p for p in fa if chain(ESP + "/" + p) and b.get(p) != fa[p]]
        if fd:
            bad.append(f"{d}: FAT attributes of {fd[:3]}")
    # The kernel ELILO boots is the ESP's copy: as /boot's (a guest fact, noted)
    notes = []
    for a, b in ((f"{ESP}/EFI/Slackware/vmlinuz", f"boot/{K}"), (f"{ESP}/EFI/Slackware/initrd.gz", "boot/initrd.gz")):
        if a in src and b in src:
            notes.append(f"{a.rsplit('/', 1)[1]} {'is' if same_bytes(a, b) else 'is NOT'} /{b}'s bytes")
    fb = [p for p in src if p.startswith(f"{ESP}/EFI/BOOT/")]
    fail = bool(bad or miss or copyerr or not gs)
    line("FAIL" if fail else "PASS",
         "The ESP's ELILO files (EFI/Slackware/elilo.efi, elilo.conf, vmlinuz, initrd.gz and EFI/BOOT's copies): bytes, times and FAT attributes",
         "; ".join(bad)[:600] + (f" missing {miss}" if miss else "") + (f" copied wrong from the guest's ESP: {copyerr[:3]}" if copyerr else "")
         + ("" if gs else " no esp-guest.sha256")
         if fail else
         f"{sum(1 for p in src if chain(p))} paths identical in all four directions ({len(fb)} in EFI/BOOT), FAT attributes "
         f"{sorted({hex(v) for p, v in fa.items() if chain(ESP + '/' + p) and isinstance(v, int)})}; {len(gs)} files as the guest's ESP holds them; "
         + ", ".join(notes))

# XFS's per-file options (fsxattr) on -uefi's XFS
if XFS:
    # Its preallocated flag (p, 0x2) isn't one of a file's options: a dense
    # restore preallocates (S5) and so gives every file it, a sparse one
    # none (accepted, both BACKOTTER.md).  As gentoo's judge.
    def nop(l):
        l = re.sub(r"(fsxattr\.xflags = )0x([0-9a-fA-F]+)", lambda m: m.group(1) + hex(int(m.group(2), 16) & ~2), l)
        return re.sub(r"\[(.)p", r"[\1-", l)
    sx = [nop(l) for l in rd("src.xfsx").splitlines()]
    bad = [f"source: {l}" for l in sx if "fsxattr.xflags" not in l]
    for d in DIRS + [f"{o}.over" for o in OVER]:
        dx = [nop(l) for l in rd(d + ".xfsx").splitlines()]
        if not dx:
            bad.append(f"{d}: no record")
            continue
        ch = [f"{a} -> {b}" for a, b in zip(sx, dx) if a != b]
        if ch:
            bad.append(f"{d}: {ch[:4]}")
    line("FAIL" if bad or not sx else "PASS", "XFS per-file options (fsxattr: extent size hints and inheritance, nodefrag f, filestream S)",
         "; ".join(bad)[:900] if bad or not sx else f"identical in {len(DIRS) + len(OVER)} restores (but p, preallocated: S5, accepted)")

# Restores exit 0
# rustic-bko notes an ext4 target's lost+found (mkfs.ext4's, in each
# target's root, as on any freshly made ext4 BackOtter restores onto):
# accepted when it is the one extra entry, a directory, and nothing else is.
NOTE = "[WARN] Note: additional entries exist in destination"


def messages(log):
    t = rd(log)
    lf = (not XFS) and re.search(r"Files:.*\b0 additional", t) and re.search(r"Dirs:.*\b1 additional", t)
    return [l for l in t.splitlines() if re.search(r"(?i)warn|error|fail|panic|ignor", l) and "[INFO]" not in l
            and not (l.strip() == NOTE and lf)]


st = {d: status.get(f"restore-{d}") for d in DIRS}
msgs = {d: messages(f"{d}.restore") for d in DIRS}
noted = [d for d in DIRS if NOTE in rd(f"{d}.restore")]
bad = [f"{d} exit {s}" for d, s in st.items() if s != "0"] + [f"{d}: {m[0]}" for d, m in msgs.items() if m]
line("FAIL" if bad else "PASS", "Restores exit 0 in all four directions",
     "; ".join(bad)[:600] if bad else "exit 0, no warning or error lines" +
     (f" but rustic-bko's note of the target's lost+found (1 additional dir, 0 files) in {', '.join(noted)}" if noted else ""))


def fatcmp(a, b):
    a, b = js(a), js(b)
    if a is None or b is None:
        return ["no FAT record"]
    h = lambda v: hex(v) if isinstance(v, int) else v  # noqa: E731
    d = [f"{p} {h(a[p])}->{h(b.get(p))}" for p in sorted(a) if b.get(p) != a[p]]
    return d + [f"{p} not in the source" for p in sorted(set(b) - set(a))]


if HASESP:
    # FAT attributes, each direction
    bad = []
    for d in DIRS:
        fd = fatcmp("src.fat.json", f"{d}.fat.json")
        if fd:
            bad.append(f"{d}: {len(fd)} differ, e.g. {fd[:4]}")
    fa = js("src.fat.json") or {}
    line("FAIL" if bad or len(fa) < 30 else "PASS",
         "FAT attributes (readonly, hidden, system, archive) on files and dirs, the guest's ESP's among them",
         "; ".join(bad)[:900] if bad or len(fa) < 30 else
         f"{len(fa)} paths, attributes {sorted({hex(v) for v in fa.values() if isinstance(v, int)})}, identical in 4 directions")
    # The ESP inside the backed-up path, restored onto the target's own
    mopt = dict(l.split(None, 1) for l in rd("mopts-guest").splitlines() if len(l.split(None, 1)) == 2)
    bad = []
    for d in DIRS:
        m = rd(f"{d}.espmount").split()
        if len(m) != 2 or m[1] != "vfat" or not re.fullmatch(r"/dev/loop[0-9]+p[0-9]+", m[0]):
            bad.append(f"{d}: ESP on {m}")
        ks = sorted({k for k, ps in diffs.get(d, {}).items() for p in ps if p == ESP or p.startswith(ESP + "/")})
        if ks:
            bad.append(f"{d}: {ks}")
    line("FAIL" if bad or nesp < 30 else "PASS", "The ESP (vfat, its own partition) mounted inside the backed-up path, as /boot/efi (fstab's)",
         "; ".join(bad)[:900] if bad or nesp < 30 else
         f"{nesp} paths restored onto each target's own ESP partition ({mopt.get('/boot/efi', '?')}), mounted before the restore, in 4 directions; no difference")

# Restore over the restored tree
bad = []
for d in OVER:
    s = status.get(f"restore-over-{d}")
    dd = {k: v for k, v in diff("src0.json", f"{d}.over.json").items() if v}
    fd = fatcmp("src.fat.json", f"{d}.over.fat.json") if HASESP else []
    m = messages(f"{d}.over")[:2]
    if s != "0" or dd or fd or m:
        bad.append(f"{d}: exit {s} {summary(dd)[:250]} {('FAT ' + str(fd[:3])) if fd else ''} {' '.join(m)}")
line("FAIL" if bad else "PASS",
     "Restore over existing immutable and append-only files (changed contents, mode, an immutable dir), "
     + ("changed FAT attributes, a changed read-only file, elilo.efi and elilo.conf on the ESP" if HASESP else "LILO's map and lilo.conf changed"),
     "; ".join(bad) if bad else "each tool over its own restore: exit 0, contents and metadata" + (" and FAT attributes" if HASESP else "")
     + " as the source's; " + " ".join(rd("restic-restic.over.before").split("\n")[:2]).strip())

# Sockets
nodes = rd("nodes.restic") + rd("nodes.rustic")
nsock = len(re.findall(r"type=\"?socket", nodes))
rs = [d for d in DIRS if os.path.exists(os.path.join(L, f"{d}.sock"))]
line("PASS" if not nsock and not rs else "FAIL", "Sockets skipped by both tools",
     "no socket node in either snapshot, none restored" if not nsock and not rs else f"{nsock} nodes, restored in {rs}")

# The directory above the backup path
a = {d: rd(f"{d}.anc").strip() for d in DIRS}
s = rd("anc-src").strip()
bad = [d for d in DIRS if a[d] != s]
line("PASS" if s and not bad else "FAIL", "Ancestor directory metadata, restored by either tool",
     f"anc1 back as the source in 4 directions: {s}" if s and not bad else f"source [{s}] " + " ".join(f"{d} [{a[d]}]" for d in bad))
n1, n2 = rd("anc1.restic").strip(), rd("anc1.rustic").strip()
if n1 and n1 == n2:
    line("PASS", "Ancestor directory nodes the same in both tools' snapshots", "identical")
else:
    line("FAIL", "Ancestor directory nodes the same in both tools' snapshots", f"restic [{n1[:300]}] rustic [{n2[:300]}]")


# The scratch disk made like the guest's
def same(a, b, what):
    x, y = rd(a).split(), rd(b).split()
    return bool(x == y and x), f"{what}: guest {' '.join(x) or '?'}; scratch {(' '.join(y) or '?') if x != y else 'the same'}"


ok1, d1 = same("pt-guest", "pt-src", "partition table (label, first/last LBA, sector size; each partition's start, size, type, name, attrs, bootable)")
tg = [d for d in DIRS if rd(f"pt-{d}").split() != rd("pt-guest").split()]
# swap: "SIZE FSTYPE"; the size as the guest's, made by mkswap, on the scratch and each target
sw = {k: rd(f"swap-{k}").split() for k in ["guest", "src"] + DIRS}
sbad = [k for k, v in sw.items() if len(v) != 2 or v != sw["guest"] or v[1] != "swap"]
line("PASS" if ok1 and not sbad and not tg else "FAIL",
     "Scratch disk partitioned like the guest's (sfdisk -d: " + ("GPT: ESP, swap, XFS /" if HASESP else "MBR: swap, ext4 / bootable") + "), and each target image",
     f"{d1}; swap: guest {' '.join(sw['guest']) or '?'} (size, file system)"
     + (f", differs: {[(k, sw[k]) for k in sbad]}" if sbad else ", made by mkswap on the scratch and the targets")
     + (f"; targets differ: {tg}" if tg else "; the 4 targets the same"))
ok1, d1 = same("feat-guest", "feat-src", "XFS (features, isize, block and sector sizes, AGs, log)" if XFS else
               "ext4 (features, inode/block size, flex_bg, hash, journal, reserved)")
ok2, d2 = same("fat-guest", "fat-src", "ESP (FAT type, sector and cluster size, FATs, reserved and hidden sectors)") if HASESP else (True, "no ESP (BIOS)")
mg = dict(l.split(None, 1) for l in rd("mopts-guest").splitlines() if len(l.split(None, 1)) == 2)
ms = dict(l.split(None, 1) for l in rd("mopts-src").splitlines() if len(l.split(None, 1)) == 2)
ok3 = mg == ms and len(mg) == (2 if HASESP else 1)
fs = rd("fstab-guest").strip().replace("\n", "; ")
line("PASS" if ok1 and ok2 and ok3 else "FAIL",
     "Scratch file systems made and mounted like the guest's (" + (f"{FSN} / with fstab's options" + (", the FAT32 ESP with its mount's" if HASESP else "")) + ")",
     f"{d1}; {d2}; mount options " + (f"as the guest's: {mg}" if ok3 else f"guest {mg}; scratch {ms}") + f"; fstab: {fs or '?'}")

# Every item of the fixture made (a skipped one would pass unseen)
sk = [l.split("skipped: ", 1)[1] for l in rd("fixture.err").splitlines() if "skipped: " in l]
sk += [l for l in rd("fixture.err").splitlines() if l.startswith("real: missing") or "Traceback" in l]
line("FAIL" if sk else "PASS", "Fixture made in full", f"harness: {sk[:6]}" if sk else "no fixture item skipped (fixture.err)")

# Non-root
bad = []
for d in DIRS:
    b = d.split("-")[0]
    sb = exitcode(f"nr-{b}.backup.exit")
    s = exitcode(f"nr-{d}.exit")
    dd = {k: v for k, v in diff("nr-src.json", f"nr-{d}.json").items() if v}
    if s != 0 or sb != 0 or dd:
        m = [l for l in rd(f"nr-{d}.restore").splitlines() if re.search(r"(?i)error|warn", l)][:2]
        bad.append(f"{d}: backup {sb} restore {s} {summary(dd)[:250]} {' '.join(m)}")
line("FAIL" if bad else "PASS",
     f"Non-root backup and restore (uid 4321's own tree on {FSN}: xattrs, ACLs, nodump, setgid dir, hard link, FIFO, sparse, times)",
     "; ".join(bad) if bad else "exit 0 and identical in all four directions")

# LIMITs
line("LIMIT", "Birth times", ("XFS and vfat keep them" if HASESP else "ext4 keeps them")
     + ", Linux can't set them (restic BACKOTTER.md unix.birthtime: not on Linux)")
line("LIMIT", "SELinux labels", "not applicable; Slackware 15's kernels have no SELinux or AppArmor (CONFIG_SECURITY_SELINUX not set): no labels on disk")
line("LIMIT", "NFSv4 ACLs", "Linux has them only over NFS (system.nfs4_acl); not on a local file system")
if HASESP:
    line("LIMIT", "Whole-disk needs (GPT, the swap's contents, ELILO's NVRAM entry)",
         "out of scope: whole-disk (HoltTest1 1Obscure recreates the disk and boots it alone); here the scratch disk is laid out like the guest's and only its file systems' contents are restored")
else:
    line("LIMIT", "Whole-disk needs (MBR, the swap's contents, LILO's boot sector and its map's sector list)",
         "out of scope: whole-disk (HoltTest1 1Obscure recreates the disk, reruns lilo and boots it alone); here the scratch disk is laid out like the guest's and only its file systems' contents are restored")

json.dump([{"line": l} for l in out], open(os.path.join(L, "results-guest.json"), "w"), indent=1)
for l in out:
    print(l)
