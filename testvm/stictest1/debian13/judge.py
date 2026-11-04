#!/usr/bin/env python3
"""SticTest1 debian13: judge inner.sh's fsmeta records, one line per need.

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
    ("File contents, sizes, types and names (non-UTF-8, 250 chars, newline, deep, empty files and dirs)",
     {"missing", "extra", "type", "content"}, None),
    ("Symlinks: targets and their own times", {"symlink target", "mtime (symlinks)", "atime (symlinks)"}, None),
    ("Permission bits incl. setuid, setgid and sticky", {"mode"}, None),
    ("Numeric ownership incl. ids with no name and above 2^31", {"owner"}, None),
    ("Modification times (ns, 1970, 2100; files and dirs)", {"mtime", "mtime (dirs)"}, None),
    ("Access times (files and dirs)", {"atime", "atime (dirs)"}, None),
    ("Device nodes (incl. rdev 0) and FIFOs", {"device number"}, r"(chardev|blockdev|rdev0|fifo|/p$)"),
    ("Hard links", {"hard links"}, None),
    ("Extended attributes user.* (binary, 3000- and 15000-byte, empty values, 40 names; dirs; user.crtime_usec, user.random-seed-creditable)", {"user-xattr"}, None),
    ("Extended attributes trusted.* (files, symlinks, FIFOs, devices)", {"trusted-xattr"}, None),
    ("Extended attributes security.* other than capabilities (files, symlinks)", {"other-xattr", "selinux"}, r"^(?!.*compress)"),
    ("btrfs per-file compression property (btrfs.compression zstd, zstd:5, lzo, zlib; set on a dir and inherited)", {"other-xattr"}, r"compress"),
    ("File capabilities (security.capability)", {"capability"}, None),
    ("POSIX ACLs, access and default", {"acl"}, None),
    ("Linux inode flags (i, a, d, A, S, D on btrfs and ext4; btrfs c, m, C incl. inherited from a dir; ext4 j, T, e and others)", {"inode flags"}, r"^(?!.*casefold)"),
    ("Casefolding (F) on ext4 -O casefold", {"inode flags"}, r"casefold"),
    ("Project IDs and inheritance (P) on ext4 -O project", {"project ID"}, None),
    ("Sparse files restored with holes; preallocated, zero-filled and swap files stay allocated",
     {"holes lost", "holes lost (sub-4K)", "holes gained", "allocation lost"}, None),
    ("Nested btrfs subvolume in the backed-up tree (backed up across, restored as a directory with its owner, mode, xattr, times)",
     None, r"tw/up1/up2/tree/subvol"),
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
    # The guest's own files, whatever kind of difference
    bad = []
    for d, dif in diffs.items():
        for k, paths in dif.items():
            ps = [p for p in paths if str(p).startswith("guest") or "/tree/real" in str(p)]
            if ps:
                bad.append(f"{d}: {k}: {', '.join(ps[:3])}")
    ng = sum(1 for p in src if str(p).startswith("guest/") and src[p]["type"] == "file")
    ng += sum(1 for p in src if "/tree/real/" in str(p))
    out("FAIL" if bad else "PASS", "The guest's own files (set-id programs, hard-linked files, locale-archive with holes, nocow files, journal dirs with ACLs and user.crtime_usec, random-seed)",
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
        if s != 0 or dd:
            m = [l for l in read(f"{L}/{d}.over").splitlines() if re.search(r"error|warn", l, re.I)][:2]
            bad.append(f"{d}: exit {s} {summary(dd)[:250]} {' '.join(m)}")
    out("FAIL" if bad else "PASS", "Restore over existing immutable and append-only files (changed contents, mode, an immutable dir)",
        "; ".join(bad) if bad else "exit 0 and identical in all four directions")
    # Sockets
    nodes = read(f"{L}/nodes.restic") + read(f"{L}/nodes.rustic")
    nsock = len(re.findall(r"type=\"?socket", nodes))
    rs = [d for d in DIRS if os.path.exists(f"{L}/{d}.sock")]
    out("PASS" if not nsock and not rs else "FAIL", "Sockets skipped by both tools",
        "no socket node in either snapshot, none restored" if not nsock and not rs else f"{nsock} nodes, restored in {rs}")
    # The directory above the backup path
    a = {d: read(f"{L}/{d}.anc").strip() for d in DIRS}
    s = anc_src.strip()
    ok = a["restic-restic"] == s and a["restic-rustic"] == s
    out("PASS" if ok else "FAIL", "Ancestor directory metadata from restic-bko's snapshot, restored by either tool",
        f"anc1 back as the source: {s}" if ok else f"source [{s}] restic-restic [{a['restic-restic']}] restic-rustic [{a['restic-rustic']}]")
    n1, n2 = read(f"{L}/anc1.restic").strip(), read(f"{L}/anc1.rustic").strip()
    if n1 == n2:
        out("PASS", "Ancestor directory nodes the same in both tools' snapshots", "identical")
    else:
        r1 = a["rustic-rustic"].split(" ", 2)[:2], a["rustic-restic"].split(" ", 2)[:2]
        out("LIMIT", "Ancestor directory nodes the same in both tools' snapshots",
            "accepted difference (rustic_core BACKOTTER.md: rustic-bko makes 0755 nodes without owner or times "
            f"above the backup path); anc1 restored from rustic-bko's snapshot as {' '.join(r1[0])} (rustic) / {' '.join(r1[1])} (restic), source {' '.join(s.split(' ', 2)[:2])}")
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
