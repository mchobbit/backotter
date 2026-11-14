#!/usr/bin/env python3
"""SticTest1 kali: judge inner.sh's records, one PASS/FAIL/LIMIT line per need.

  judge.py LOGDIR

ubuntu26-uefi's needs (fsmeta.differences() of every path, not the 5
examples of fsmeta's --json), with the ESP inside the backed-up tree
(its FAT attributes by fatattr.py), the scratch disk's layout against the
guest's, and the guest's own files as real examples.
"""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fsmeta  # noqa: E402

L = sys.argv[1]
DIRS = ["restic-restic", "rustic-rustic", "restic-rustic", "rustic-restic"]
OVER = ["restic-restic", "rustic-rustic"]
ESP = "boot/efi"

# need, fsmeta kinds, path filter (None: any path)
NEEDS = [
    ("File contents, sizes, types and names (non-UTF-8, 250 chars, newline, deep, empty files and dirs; FAT's names)",
     {"missing", "extra", "type", "content"}, None),
    ("Symlinks: targets and their own times", {"symlink target", "mtime (symlinks)", "atime (symlinks)"}, None),
    ("Permission bits incl. setuid, setgid and sticky", {"mode"}, None),
    ("Numeric ownership incl. ids with no name and above 2^31", {"owner"}, None),
    ("Modification times (ns, 1970, 2100; files and dirs; FAT's 2 s and its limits)", {"mtime", "mtime (dirs)"}, None),
    ("Access times (files and dirs; FAT's date-only)", {"atime", "atime (dirs)"}, None),
    ("Device nodes (incl. rdev 0) and FIFOs", {"device number"}, r"(chardev|blockdev|rdev0|fifo|/p$)"),
    ("Hard links", {"hard links"}, None),
    ("Extended attributes user.* (binary, 3000-byte, empty values; dirs; the journal's user.crtime_usec)", {"user-xattr"}, None),
    ("Extended attributes trusted.* (files, symlinks, FIFOs, devices)", {"trusted-xattr"}, None),
    ("Extended attributes security.* other than capabilities (files, symlinks)", {"other-xattr", "selinux"}, None),
    ("File capabilities (security.capability)", {"capability"}, None),
    ("POSIX ACLs, access and default (the journal's among them)", {"acl"}, None),
    ("Linux inode flags (i, a, d, A, S, D, j, T and others ext4 keeps)", {"inode flags"}, r"^(?!.*casefold)"),
    ("Casefolding (F) on ext4 -O casefold", {"inode flags"}, r"casefold"),
    ("Project IDs and inheritance (P) on ext4 -O project", {"project ID"}, None),
    ("Sparse files restored with holes; preallocated, zero-filled and swap files stay allocated",
     {"holes lost", "holes lost (sub-4K)", "holes lost (sub-block)", "holes gained", "allocation lost"}, None),
]


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
nesp = sum(1 for p in src if p == ESP or p.startswith(ESP + "/"))

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
         f"identical in all four directions ({len(src)} paths, {nesp} on the ESP)")
# Anything fsmeta saw that no need above names
known = set().union(*(k for _, k, _ in NEEDS))
other = [f"{d}: {k} {v[:3]}" for d, dif in diffs.items() for k, v in dif.items() if v and k not in known]
if other:
    line("FAIL", "Other metadata differences", "; ".join(other))

# Backups: exit 0, no warnings
bad = []
for t in ("restic", "rustic"):
    m = [x for x in rd(f"repo-{t}.backup").splitlines() if re.search(r"(?i)warn|error|signal|panic|floating", x) and "[INFO]" not in x]
    if status.get(f"backup-{t}") != "0" or m:
        bad.append(f"{t}: exit {status.get(f'backup-{t}')}: {' '.join(m[:3])}")
    line("FAIL" if bad else "PASS", f"{t}-bko runs natively and backs up the fixture",
         "; ".join(bad) if bad else "exit 0, no warnings (owners with no name, big ids, the ESP inside the tree included)")
    bad = []

# Backups leave the source's atimes alone (strictatime), per tool
for t in ("restic", "rustic"):
    d = diff(f"src-before-atime-{t}.json", f"src-after-atime-{t}.json")
    d = {k: v for k, v in d.items() if v and k != "atime (symlinks)"}
    ok = not d and status.get(f"backup-atime-{t}") == "0"
    line("PASS" if ok else "FAIL", f"{t}-bko backup leaves source atimes alone (strictatime)",
         f"same ({len(src)} paths, the ESP's among them; backup exit 0); symlinks' atimes move (readlink), accepted in S1, the snapshot keeps the earlier ones"
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
bad = []
for d, dif in diffs.items():
    for k, paths in dif.items():
        ps = [p for p in paths if re.match(r"(guest|etc|usr|boot|home|var)(/|$)", str(p))]
        if ps:
            bad.append(f"{d}: {k}: {', '.join(ps[:3])}")
want = ["etc/shadow", "etc/gshadow", "etc/passwd", "etc/fstab", "etc/default/grub", "etc/apt/sources.list.d/kali.sources",
        "etc/sudoers", "usr/bin/su", "usr/bin/passwd", "usr/bin/mount", "usr/bin/sudo", "usr/lib/openssh/ssh-keysign",
        "boot/grub/grub.cfg", "boot/grub/grubenv", "boot/grub/x86_64-efi",
        "boot/efi/EFI/kali/grubx64.efi", "boot/efi/EFI/BOOT/BOOTX64.EFI",
        "home/tester", "home/tester/.bashrc", "home/tester/.zshrc", "var/log/journal"]
miss = [p for p in want if p not in src]
setid = sorted(p for p, e in src.items() if re.match(r"(usr|etc)/", p) and e["type"] == "file" and e["mode"] & 0o6000)
jf = [p for p in src if p.startswith("var/log/journal/") and p.endswith(".journal")]
jacl = [p for p in src if p.startswith("var/log/journal") and any(x.startswith("system.posix_acl") for x in src[p]["xattrs"])]
caps = [p for p in src if "security.capability" in src[p]["xattrs"] and not p.startswith("root/bko-torture")]
ng = sum(1 for p in src if p.startswith("guest/") and src[p]["type"] == "file")
nreal = sum(1 for p in src if re.match(r"(guest|etc|usr|boot|home|var)(/|$)", p))
envcaps = re.search(r"files with capabilities: (.*)", rd("env.txt"))
capnote = (f"capabilities: the guest's {len(caps)}" if caps else
           f"capabilities: the guest has none ({envcaps.group(1) if envcaps else '?'}; torture.sh's cap-binary is checked)")
line("FAIL" if bad or miss or not jf or not jacl or len(setid) < 10 else "PASS",
     "The guest's own files (set-id programs, shadow, fstab, GRUB's defaults and /boot/grub, kali.sources, the ESP's GRUB, tester's home, the journal with its ACLs)",
     "; ".join(bad)[:600] + (f" missing {miss}" if miss else "") + (f" journal files {len(jf)}, with ACLs {len(jacl)}, set-id {len(setid)}")
     if bad or miss or not jf or not jacl or len(setid) < 10 else
     f"{nreal} paths identical in all four directions: {len(setid)} set-id programs, {capnote}, {len(jf)} journal file(s), "
     f"{len(jacl)} journal paths with ACLs, EFI/kali/grubx64.efi and EFI/BOOT/BOOTX64.EFI, {ng} guest/ copies (hard-linked, with holes)")

# Restores exit 0
# rustic-bko notes the target's lost+found (mkfs.ext4's, in each target's
# root, as on any freshly made ext4 BackOtter restores onto): accepted
# when it is the one extra entry, a directory, and nothing else is.
NOTE = "[WARN] Note: additional entries exist in destination"


def messages(log):
    t = rd(log)
    lf = re.search(r"Files:.*\b0 additional", t) and re.search(r"Dirs:.*\b1 additional", t)
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
bad = []
for d in DIRS:
    m = rd(f"{d}.espmount").split()
    if len(m) != 2 or m[1] != "vfat" or not re.fullmatch(r"/dev/loop[0-9]+p[0-9]+", m[0]):
        bad.append(f"{d}: ESP on {m}")
    ks = sorted({k for k, ps in diffs.get(d, {}).items() for p in ps if p == ESP or p.startswith(ESP + "/")})
    if ks:
        bad.append(f"{d}: {ks}")
line("FAIL" if bad or nesp < 30 else "PASS", "The ESP (vfat, its own partition) mounted inside the backed-up path, as /boot/efi",
     "; ".join(bad)[:900] if bad or nesp < 30 else
     f"{nesp} paths restored onto each target's own ESP partition (umask=0077), mounted before the restore, in 4 directions; no difference")

# Restore over the restored tree
bad = []
for d in OVER:
    s = status.get(f"restore-over-{d}")
    dd = {k: v for k, v in diff("src0.json", f"{d}.over.json").items() if v}
    fd = fatcmp("src.fat.json", f"{d}.over.fat.json")
    m = messages(f"{d}.over")[:2]
    if s != "0" or dd or fd or m:
        bad.append(f"{d}: exit {s} {summary(dd)[:250]} {('FAT ' + str(fd[:3])) if fd else ''} {' '.join(m)}")
line("FAIL" if bad else "PASS",
     "Restore over existing immutable and append-only files (changed contents, mode, an immutable dir), changed FAT attributes and a changed read-only file and GRUB on the ESP",
     "; ".join(bad) if bad else "each tool over its own restore: exit 0, contents, metadata and FAT attributes as the source's")

# Sockets
nodes = rd("nodes.restic") + rd("nodes.rustic")
nsock = len(re.findall(r"type=\"?socket", nodes))
rs = [d for d in DIRS if os.path.exists(os.path.join(L, f"{d}.sock"))]
hassock = any(v["type"] == "socket" for v in src.values()) or "root/bko-torture/socket" in src
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


ok1, d1 = same("gpt-guest", "gpt-src", "GPT (label, first/last LBA, sector size; each partition's start, size, type, name, attrs)")
tg = [d for d in DIRS if rd(f"gpt-{d}").split() != rd("gpt-guest").split()]
sw = rd("swap-guest").split(), rd("swap-src").split()
ok2 = len(sw[0]) == 3 and len(sw[1]) == 3 and sw[0][1:] == sw[1][1:] and sw[1][2] == "swap"
line("PASS" if ok1 and ok2 and not tg else "FAIL", "Scratch disk partitioned like the guest's (sfdisk -d: ESP, ext4 /, swap), and each target image",
     f"{d1}; swap: guest {' '.join(sw[0]) or '?'}, scratch {' '.join(sw[1]) or '?'}" + (f"; targets differ: {tg}" if tg else "; the 4 targets the same"))
ok1, d1 = same("feat-guest", "feat-src", "ext4 (features, inode/block size, flex_bg, hash, journal, reserved)")
ok2, d2 = same("fat-guest", "fat-src", "ESP (FAT type, sector and cluster size, FATs, reserved and hidden sectors)")
mg = dict(l.split(None, 1) for l in rd("mopts-guest").splitlines() if len(l.split(None, 1)) == 2)
ms = dict(l.split(None, 1) for l in rd("mopts-src").splitlines() if len(l.split(None, 1)) == 2)
ok3 = mg == ms and len(mg) == 2
line("PASS" if ok1 and ok2 and ok3 else "FAIL", "Scratch file systems made and mounted like the guest's (ext4 /, the FAT32 ESP; fstab's options)",
     f"{d1}; {d2}; mount options " + (f"as the guest's: / {mg.get('/')}; /boot/efi {mg.get('/boot/efi')}" if ok3 else f"guest {mg}; scratch {ms}"))

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
     "Non-root backup and restore (uid 4321's own tree: xattrs, ACLs, nodump, setgid dir, hard link, FIFO, sparse, times)",
     "; ".join(bad) if bad else "exit 0 and identical in all four directions")

# LIMITs
lsm = rd("lsm").strip()
line("LIMIT", "Birth times", "ext4 and vfat keep them, Linux can't set them (restic BACKOTTER.md unix.birthtime: not on Linux)")
line("LIMIT", "SELinux labels", f"not applicable; the guest runs AppArmor (LSMs {lsm or '?'}), which keeps no labels on disk")
line("LIMIT", "NFSv4 ACLs", "Linux has them only over NFS (system.nfs4_acl); not on a local file system")
line("LIMIT", "Whole-disk needs (GPT, GRUB's NVRAM entry and EFI/kali, the initramfs, the swap partition)",
     "out of scope: whole-disk (HoltTest1 1Obscure recreates the disk, boots it alone from its NVRAM entry); here the scratch disk is laid out like the guest's and only its file systems' contents are restored")

json.dump([{"line": l} for l in out], open(os.path.join(L, "results-guest.json"), "w"), indent=1)
for l in out:
    print(l)
