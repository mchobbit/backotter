#!/usr/bin/env python3
"""SticTest1 debian13-stack: judge inner.sh's fsmeta records, one line per need.

  judge.py same BEFORE.json AFTER.json [IGNORED_KIND...]
                                 "same" (exit 0) or the differences (exit 1)
  judge.py judge LOGDIR ANC_SRC  PASS/FAIL/LIMIT lines for every need
Differences are fsmeta.differences() of the records (every path, not the
5 examples of fsmeta's --json).
"""
import json, os, re, sys
sys.path.insert(0, "/root")
import fsmeta  # noqa: E402

DIRS = ["restic-restic", "rustic-rustic", "restic-rustic", "rustic-restic"]
GENERIC = {"missing", "extra", "type"}

# need, fsmeta kinds, path filter (None: any path), path exclusion
NEEDS = [
    ("File contents, sizes, types and names (non-UTF-8, 250 chars, newline, deep, empty files and dirs; FAT's long and odd names)",
     {"missing", "extra", "type", "content"}, None),
    ("Symlinks: targets and their own times", {"symlink target", "mtime (symlinks)", "atime (symlinks)"}, None),
    ("Permission bits incl. setuid, setgid and sticky (FAT: the mount's fmask/dmask, read-only)", {"mode"}, None),
    ("Numeric ownership incl. ids with no name and above 2^31", {"owner"}, None),
    ("Modification times (ns, 1970, 2100; files and dirs; FAT's 2 s, 1980 and 2107)", {"mtime", "mtime (dirs)"}, None),
    ("Access times (files and dirs; FAT's dates)", {"atime", "atime (dirs)"}, None),
    ("Device nodes (incl. rdev 0) and FIFOs", {"device number"}, r"(chardev|blockdev|rdev0|fifo|/p$)"),
    ("Hard links", {"hard links"}, None),
    ("Extended attributes user.* (binary, 3000- and 15000-byte, empty values, 40 names; dirs; user.crtime_usec, user.random-seed-creditable; ext4 and XFS)", {"user-xattr"}, None),
    ("Extended attributes trusted.* (files, symlinks, FIFOs, devices)", {"trusted-xattr"}, None),
    ("Extended attributes security.* other than capabilities (files, symlinks)", {"other-xattr", "selinux"}, None),
    ("File capabilities (security.capability v2 and v3)", {"capability"}, None),
    ("POSIX ACLs, access and default (ext4 and XFS)", {"acl"}, None),
    ("Linux inode flags (i, a, d, A, S on ext4 and XFS; ext4 D, j, T, e and others; XFS P)", {"inode flags"}, r"^(?!.*casefold)"),
    ("Casefolding (F) on ext4 -O casefold", {"inode flags"}, r"casefold"),
    ("Project IDs and inheritance (P) on ext4 -O project and XFS (projid32bit)", {"project ID"}, None),
    ("Sparse files restored with holes; preallocated, zero-filled and swap files stay allocated",
     {"holes lost", "holes lost (sub-4K)", "holes gained", "allocation lost"}, None),
]


def diff(before, after):
    try:
        # Sockets are judged on their own (both tools skip them): fsmeta's
        # --skip leaves out directories only.
        b = {k: v for k, v in json.load(open(before)).items() if v["type"] != "socket"}
        a = {k: v for k, v in json.load(open(after)).items() if v["type"] != "socket"}
        return fsmeta.differences(b, a)
    except Exception as e:  # noqa: BLE001
        return {"(unreadable)": [f"{after}: {e}"]}


def summary(d):
    return "; ".join(f"{k} {len(v)}: {', '.join(map(repr, v[:3]))}" for k, v in d.items())[:500]


def same(before, after, ignore=()):
    d = {k: v for k, v in diff(before, after).items() if v and k not in ignore}
    print(f"same ({len(json.load(open(before)))} paths)" if not d else summary(d))
    return 1 if d else 0


def read(p, default=""):
    try:
        return open(p, errors="replace").read()
    except OSError:
        return default


def exitcode(p):
    m = re.search(r"exit (\d+)", read(p))
    return int(m.group(1)) if m else None


def out(v, need, det):
    print(f"{v} {need}: {det}".replace("\n", " ")[:900])


def own(p):
    p = str(p)
    return p.startswith("guest/") or "/tree/real" in p or re.match(r"boot/(efi2?/EFI|grub|config-)", p) is not None


def rjs(p):
    try:
        return json.load(open(p))
    except Exception:  # noqa: BLE001
        return None


def judge(L, anc_src):
    S = f"{L}/src0.json"
    diffs = {d: diff(S, f"{L}/{d}.json") for d in DIRS if os.path.exists(f"{L}/{d}.json")}
    missing_dirs = [d for d in DIRS if d not in diffs]
    src = json.load(open(S))
    for need, kinds, flt in NEEDS:
        bad = []
        for d, dif in diffs.items():
            for k, paths in dif.items():
                if kinds is not None and k not in kinds:
                    continue
                ps = [p for p in paths if flt is None or re.search(flt, p)]
                if ps:
                    bad.append(f"{d}: {k}: {', '.join(map(str, ps[:4]))}{' ...' if len(ps) > 4 else ''}")
        if missing_dirs:
            bad.append(f"no result for {', '.join(missing_dirs)}")
        out("FAIL" if bad else "PASS", need, "; ".join(bad) if bad else "identical in all four directions")
    # FAT attributes (FAT_IOCTL_GET_ATTRIBUTES) of both ESPs, the guest's
    # files and the FAT fixture
    bad, n, kinds = [], 0, set()
    for e in ("efi", "efi2"):
        sf = rjs(f"{L}/src0.{e}.fat.json")
        if not sf:
            bad.append(f"{e}: no source record"); continue
        n += len(sf); kinds |= {v for v in sf.values() if isinstance(v, int)}
        for d in DIRS:
            df = rjs(f"{L}/{d}.{e}.fat.json")
            if df is None:
                bad.append(f"{d} {e}: no record"); continue
            ch = [f"{p} {sf[p]}->{df.get(p)}" for p in sf if df.get(p) != sf[p]]
            ch += [f"{p} extra" for p in df if p not in sf]
            if ch:
                bad.append(f"{d} {e}: {len(ch)} changed, e.g. {ch[:5]}")
    out("FAIL" if bad else "PASS", "FAT attributes on the ESPs (readonly, hidden, system, archive set and cleared; the guest's ESPs' files)",
        "; ".join(bad) if bad else f"{n} paths on both ESPs, attributes {[hex(k) for k in sorted(kinds)]}, identical in all four directions")
    # XFS's own per-file options (fsxattr), the preallocated flag p aside: a
    # dense restore preallocates (S5) and so gives every file it, a sparse one
    # none (an accepted difference, both BACKOTTER.md, linux.xfs_options).
    def nop(l):
        l = re.sub(r"(fsxattr\.xflags = )0x([0-9a-fA-F]+)", lambda m: m.group(1) + hex(int(m.group(2), 16) & ~2), l)
        return re.sub(r"\[(.)p", r"[\1-", l)
    sx = [nop(l) for l in read(f"{L}/src0.xfsx").splitlines()]
    bad = []
    for d in DIRS:
        dx = [nop(l) for l in read(f"{L}/{d}.xfsx").splitlines()]
        if not dx:
            bad.append(f"{d}: no record"); continue
        ch = [f"{a} -> {b}" for a, b in zip(sx, dx) if a != b]
        if ch:
            bad.append(f"{d}: {ch[:4]}")
    # Each option really set in the source (xfs_io may have refused one)
    want = {"xfs-extsize": 0x800, "xfs-extsize-dir": 0x1000, "xfs-nodefrag": 0x2000, "xfs-filestream": 0x4000,
            "xfs-cowextsize": 0x10000, "xfs-cowextsize-dir": 0x10000, "xfs-nosymlinks": 0x400}
    got = {}
    for l in sx:
        m = re.match(r"(\S+) fsxattr\.xflags = 0x([0-9a-fA-F]+)", l)
        if m:
            got[m.group(1)] = int(m.group(2), 16)
    unset = [n for n, b in want.items() if not got.get(n, 0) & b]
    ok = sx and all("fsxattr.xflags" in l for l in sx) and not unset
    short = [re.sub(r"fsxattr[.](\w+) = ", r"\1=", l).strip() for l in sx]
    out("FAIL" if bad or not ok else "PASS", "XFS per-file options on the /srv-like XFS (fsxattr: extent size hints e/E, nodefrag f, filestream S, copy-on-write extent size hints C, nosymlinks n)",
        "; ".join(bad) if bad else (f"source: {short}; identical in all four directions (but p, preallocated: S5, accepted)" if ok else
                                f"not recorded or not set in the source ({unset}): {sx}"))
    # The guest's own files, whatever kind of difference
    bad = []
    for d, dif in diffs.items():
        for k, paths in dif.items():
            ps = [p for p in paths if own(p)]
            if ps:
                bad.append(f"{d}: {k}: {', '.join(ps[:3])}")
    ng = sum(1 for p in src if own(p) and src[p]["type"] == "file")
    out("FAIL" if bad else "PASS", "The guest's own files (set-id programs, a hard-linked pair, the journal copied sparse, journal dirs with ACLs and user.crtime_usec, random-seed, crypttab, mdadm.conf, LVM's configuration and backup, /boot's GRUB directory, both ESPs' files)",
        "; ".join(bad) if bad else f"{ng} copied files identical in all four directions")
    # Anything fsmeta saw that no need above names
    known = set().union(*(k for _, k, _ in NEEDS if k))
    other = [f"{d}: {k}" for d, dif in diffs.items() for k, v in dif.items() if v and k not in known]
    if other:
        out("FAIL", "Other metadata differences", "; ".join(other))
    # Exit status of each restore
    st = {d: exitcode(f"{L}/{d}.exit") for d in DIRS}
    msgs = {d: [l for l in read(f"{L}/{d}.restore").splitlines()
                if re.search(r"warn|error|fail|panic", l, re.I) and "[INFO]" not in l] for d in DIRS}
    bad = [f"{d} exit {s}" for d, s in st.items() if s != 0]
    out("FAIL" if bad else "PASS", "Restores exit 0 in all four directions",
        "; ".join(bad + [f"{d}: {m[0]}" for d, m in msgs.items() if m])[:600] if bad else
        "exit 0; message lines: " + ", ".join(f"{d} {len(m)}" for d, m in msgs.items()) +
        ("" if not any(msgs.values()) else " (" + "; ".join(m[0] for m in msgs.values() if m)[:300] + ")"))
    # Restore over the restored tree
    bad = []
    for d in DIRS:
        s = exitcode(f"{L}/{d}.over.exit")
        dd = diff(S, f"{L}/{d}.over.json")
        # FAT attributes and XFS options, which fsmeta doesn't record
        fx = []
        for e in ("efi", "efi2"):
            sf, df = rjs(f"{L}/src0.{e}.fat.json"), rjs(f"{L}/{d}.over.{e}.fat.json")
            if not sf or df is None:
                fx.append(f"{e}: no record"); continue
            fx += [f"{e}/{p} {sf[p]}->{df.get(p)}" for p in sf if df.get(p) != sf[p]][:3]
            fx += [f"{e}/{p} extra" for p in df if p not in sf][:3]
        ox = [nop(l) for l in read(f"{L}/{d}.over.xfsx").splitlines()]
        if not ox:
            fx.append("no XFS options record")
        fx += [f"{a} -> {b}" for a, b in zip(sx, ox) if a != b][:3]
        if s != 0 or dd or fx:
            m = [l for l in read(f"{L}/{d}.over").splitlines() if re.search(r"error|warn", l, re.I)][:2]
            bad.append(f"{d}: exit {s} {summary(dd)[:250]} {'; '.join(fx)[:300]} {' '.join(m)}")
    out("FAIL" if bad else "PASS", "Restore over existing immutable and append-only files (changed contents, mode, an immutable dir, a FAT attribute; FAT attributes and XFS options compared too)",
        "; ".join(bad) if bad else "exit 0 and identical in all four directions")
    # Sockets
    nodes = read(f"{L}/nodes.restic") + read(f"{L}/nodes.rustic")
    nsock = len(re.findall(r"type=\"?socket", nodes))
    rs = [d for d in DIRS if os.path.exists(f"{L}/{d}.sock")]
    out("PASS" if not nsock and not rs else "FAIL", "Sockets skipped by both tools",
        "no socket node in either snapshot, none restored" if not nsock and not rs else f"{nsock} nodes, restored in {rs}")
    # The directory above the backup path: both tools store it with its own
    # metadata (restic BACKOTTER.md; rustic_core BACKOTTER.md, "Directories
    # above the backed-up path"), so all four directions give it back.
    a = {d: read(f"{L}/{d}.anc").strip() for d in DIRS}
    s = anc_src.strip()
    bad = [f"{d} [{v}]" for d, v in a.items() if v != s]
    out("FAIL" if bad else "PASS", "Ancestor directory metadata (mode, owner, times, xattrs, ACL) from either tool's snapshot, restored by either tool",
        f"source [{s}]; " + "; ".join(bad) if bad else f"anc1 back as the source in all four directions: {s}")
    n1, n2 = read(f"{L}/anc1.restic").strip(), read(f"{L}/anc1.rustic").strip()
    ok = n1 and n1 == n2 and len(n1.splitlines()) == 4
    out("PASS" if ok else "FAIL", "Ancestor directory nodes the same in both tools' snapshots",
        "identical: /mnt, /mnt/bkosb, the source's root and anc1" if ok else f"restic [{n1[:300]}] rustic [{n2[:300]}] {read(L + '/anc1.err')[:200]}")
    # Non-root
    bad = []
    for d in DIRS:
        b = d.split("-")[0]
        sb = exitcode(f"{L}/nr-{b}.backup.exit")
        s = exitcode(f"{L}/nr-{d}.exit")
        dd = diff(f"{L}/nr-src.json", f"{L}/nr-{d}.json")
        if s != 0 or sb != 0 or dd:
            m = [l for l in read(f"{L}/nr-{d}.restore").splitlines() if re.search(r"error|warn", l, re.I)][:2]
            bad.append(f"{d}: backup {sb} restore {s} {summary(dd)[:250]} {' '.join(m)}")
    out("FAIL" if bad else "PASS",
        "Non-root backup and restore (uid 4321's own tree: xattrs, ACLs, nodump, setgid dir, hard link, FIFO, sparse, times)",
        "; ".join(bad) if bad else "exit 0 and identical in all four directions")
    return 0


if __name__ == "__main__":
    if sys.argv[1] == "same":
        sys.exit(same(sys.argv[2], sys.argv[3], sys.argv[4:]))
    sys.exit(judge(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else ""))
