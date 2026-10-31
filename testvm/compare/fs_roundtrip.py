#!/usr/bin/env python3
"""Round trip of torture.sh's files through BackOtter's archivers on one kind
of file system, made with every feature those files need: project IDs and
case-insensitive directories on ext4 and f2fs, and so on.  compare.py tests
the guests' own file systems, which mostly lack them.

Usage: sudo fs_roundtrip.py FSTYPE [--onto FSTYPE2] [TOOL ...]
         FSTYPE: ext4 xfs btrfs f2fs jfs ext2 vfat ext4-plain (loop images in /var/tmp),
                 zfs (pools bkotest-RUN-src and bkotest-RUN-dst on files in
                 /var/tmp, destroyed afterwards)
         --onto: restore onto FSTYPE2 instead (e.g. ext4-plain: no project IDs,
                 no casefolding), to see warnings, not errors, for what it lacks
         TOOL:   restic-bko rustic-bko restic-to-rustic rustic-to-restic (default: all)

Besides torture.sh's files, the source gets a few more for BackOtter's own
rules (extras(), below).  Per tool it checks:
  - the restored tree against the source (fsmeta.py compare);
  - that the backup left the source's atimes alone: the source is mounted
    strictatime during the backup, read only otherwise;
  - a second restore over the restored tree after changing it (restore over
    an existing tree: changed bytes, punched holes, a symlinked directory, a
    casefold directory emptied and unflagged, a changed project ID);
and across tools: the snapshots' nodes (generic attributes such as
unix.sparse included), and st_blocks of each restored file.

restic-bko and rustic-bko are found as compare.py finds them ($RESTIC_BKO,
$RUSTIC_BKO).  Results go to ~/backotter-work/fs-roundtrip/RUN/, where RUN is
FSTYPE[-onto-FSTYPE2].

Each RUN has mount points (/mnt/bko-fsrt/RUN/{src,dst}), images and pools of
its own, so different RUNs can go at the same time (e.g. xargs -P); the same
RUN twice can't, and the second one stops.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare  # noqa: E402  (backup() and the tool lookup)

MKFS = {
    "ext4": ["mkfs.ext4", "-q", "-F", "-I", "256", "-O", "project,quota,casefold", "-E", "encoding=utf8"],
    "f2fs": ["mkfs.f2fs", "-q", "-f", "-O", "extra_attr,project_quota,casefold", "-C", "utf8"],
    "xfs": ["mkfs.xfs", "-q", "-f"],
    "btrfs": ["mkfs.btrfs", "-q", "-f"],
    "jfs": ["mkfs.jfs", "-q"],
    "ext2": ["mkfs.ext2", "-q", "-F"],
    "vfat": ["mkfs.vfat"],
    "ext4-plain": ["mkfs.ext4", "-q", "-F"],
    # POSIX ACLs and system-attribute xattrs, as a Linux root pool has them.
    "zfs": ["zpool", "create", "-f", "-O", "acltype=posix", "-O", "xattr=sa"],
}
TOOLS = ["restic-bko", "rustic-bko", "restic-to-rustic", "rustic-to-restic"]
WORK = compare.WORK_BASE / "fs-roundtrip"
# The source and destination mount points, per RUN (set_run()).
SRC = Path("/mnt/bko-fsrt/RUN/src")
DST = Path("/mnt/bko-fsrt/RUN/dst")
# ZFS pools, mounted at SRC and DST, when FSTYPE is zfs.
POOLS: dict[Path, str] = {}
# What remount options are on ZFS: dataset properties.
ZFS_PROPS = {"ro": ["readonly=on"], "rw": ["readonly=off"],
             "strictatime": ["atime=on", "relatime=off"], "noatime": ["atime=off"]}
TORTURE = "root/bko-torture"

# More files, for the rules BackOtter's two archivers share (unix.sparse,
# casefolding, project IDs, btrfs compression).
EXTRAS = r'''
T=$1
try() { "$@" 2>/dev/null || echo "extras: skipped (unsupported here): $*" >&2; }
fill() { python3 -c 'import os, sys; p = sys.argv[1]; open(p, "r+b").write(bytes([int(sys.argv[2], 0)]) * os.path.getsize(p))' "$@"; }

# A case-sensitive directory inside a case-insensitive one, holding names that
# differ only in case, and a case-insensitive one that inherits F.  A restore
# that leaves the inherited F on "sensitive" loses one of the two files.
if lsattr -d "$T/casefold-dir" 2>/dev/null | cut -d' ' -f1 | grep -q F; then
    mkdir "$T/casefold-dir/sensitive"; chattr -F "$T/casefold-dir/sensitive"
    echo upper >"$T/casefold-dir/sensitive/Makefile"; echo lower >"$T/casefold-dir/sensitive/makefile"
    mkdir "$T/casefold-dir/folded"; echo readme >"$T/casefold-dir/folded/Readme"
else
    echo "extras: skipped (unsupported here): casefold subdirectories" >&2
fi

# Small sparse files: data inside a blob, holes on both sides.
truncate -s 4M "$T/sparse-small"
printf 'middle' | dd of="$T/sparse-small" bs=1 seek=$((1536 * 1024)) conv=notrunc status=none
cp --sparse=always "$T/sparse-small" "$T/sparse-over"
mkdir "$T/sparse-dir"; cp --sparse=always "$T/sparse-small" "$T/sparse-dir/holes"

# Data, then written zeros: no holes, and none to come back.
{ head -c 1M /dev/urandom; head -c 7M /dev/zero; } >"$T/trailing-zeros"

# A project ID inherited from a P directory; one on an immutable file (the
# project ID has to be restored before the flag).
echo inherited >"$T/projid-dir/inherited"
echo projid >"$T/projid-immutable"; try chattr -p 44 "$T/projid-immutable"

# btrfs compression together with other inode flags.
for f in compress-immutable compress-nodump; do
    touch "$T/$f"; try setfattr -n btrfs.compression -v zstd "$T/$f"
    yes backotter | head -c 1M >"$T/$f"
done
try chattr +d "$T/compress-nodump"

# A directory that was moved.
mkdir -p "$T/mv-src/inner"; echo moved >"$T/mv-src/inner/file"; mv "$T/mv-src" "$T/moved"

for p in "$T/sparse-small" "$T/sparse-dir" "$T/moved" "$T/trailing-zeros"; do
    touch -a -d '2003-04-05 06:07:08.222222222' "$p"
done
[[ -d $T/casefold-dir/sensitive ]] && touch -a -d '2003-04-05 06:07:08.222222222' "$T/casefold-dir/sensitive"
try chattr +i "$T/projid-immutable"
try chattr +i "$T/compress-immutable"
exit 0
'''

# Changes to a restored tree before restoring over it.
OVER = r'''
T=$1
try() { "$@" 2>/dev/null || echo "over: skipped (unsupported here): $*" >&2; }
fill() { python3 -c 'import os, sys; p = sys.argv[1]; open(p, "r+b").write(bytes([int(sys.argv[2], 0)]) * os.path.getsize(p))' "$@"; }
find "$T" \( -type f -o -type d \) -exec chattr -i -a {} + 2>/dev/null
# Other bytes, same size.
fill "$T/atime" 0xaa
# Holes punched into files that had none: the swap file loses its header too.
try fallocate -p -o 0 -l 32M "$T/swapfile"
try fallocate -p -o 0 -l 16M "$T/preallocated"
try fallocate -p -o 0 -l 4M "$T/zeros"
# A file with holes in the snapshot, over a dense file with other bytes:
# restored dense (it isn't new), with the right bytes.
fill "$T/sparse-over" 0xaa
# The same through a symlinked directory (R-SPARSE-UNSEEN).
mv "$T/sparse-dir" "$T/elsewhere"; fill "$T/elsewhere/holes" 0xaa; ln -s elsewhere "$T/sparse-dir"
# An empty directory without F whose node has it, and one with F whose node
# lacks it.
if [[ -d $T/casefold-dir ]]; then rm -rf "$T/casefold-dir"/*; try chattr -F "$T/casefold-dir"; fi
try chattr +F "$T/empty-dir"
# Another project ID.
try chattr -p 99 "$T/projid-file"
# Hard links not made yet (an interrupted restore).
rm -f "$T/hardlink-b" "$T/sub/hardlink-c"
exit 0
'''
# Differences the over-existing restore is expected to show.
OVER_EXPECTED = {
    ("extra", f"{TORTURE}/elsewhere"), ("extra", f"{TORTURE}/elsewhere/holes"),
    # S4(b): a marked file is restored with holes only into a new file.
    ("holes lost", f"{TORTURE}/sparse-over"),
    # The same for the swap file, whose header OVER punched out, where the
    # source had holes (ZFS makes holes of runs of zeros) and it is marked.
    ("holes lost", f"{TORTURE}/swapfile"),
}


def run(cmd: list[str], check: bool = True, quiet: bool = False, **kw) -> subprocess.CompletedProcess:
    if not quiet:
        print("  $ " + " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)
    if check and r.returncode:
        raise SystemExit(f"failed ({r.returncode}): {r.stderr.strip() or r.stdout.strip()}")
    return r


def set_run(run_name: str) -> None:
    """Mount points and pools of this RUN's own."""
    global SRC, DST, POOLS
    SRC, DST = Path("/mnt/bko-fsrt") / run_name / "src", Path("/mnt/bko-fsrt") / run_name / "dst"
    POOLS = {SRC: f"bkotest-{run_name}-src", DST: f"bkotest-{run_name}-dst"}


def make_fs(fs: str, image: Path, mp: Path) -> None:
    unmount(mp)
    if fs == "zfs":
        image = Path("/var/tmp") / f"{POOLS[mp]}.img"
    image.unlink(missing_ok=True)
    run(["truncate", "-s", "4G", str(image)])
    mp.mkdir(parents=True, exist_ok=True)
    if fs == "zfs":
        run(MKFS[fs] + ["-m", str(mp), POOLS[mp], str(image)])
        return
    run(MKFS[fs] + [str(image)])
    run(["mount", "-o", "loop", str(image), str(mp)])


def is_pool(mp: Path) -> bool:
    return (shutil.which("zpool") is not None
            and run(["zpool", "list", "-H", POOLS[mp]], check=False, quiet=True).returncode == 0)


def remount(mp: Path, *opts: str) -> None:
    """mount -o remount,OPTS; on ZFS, the dataset properties that do the same."""
    if is_pool(mp):
        run(["zfs", "set"] + [p for o in opts for p in ZFS_PROPS[o]] + [POOLS[mp]])
    else:
        run(["mount", "-o", ",".join(("remount",) + opts), str(mp)])


def unmount(mp: Path) -> None:
    """Unmount mp; a ZFS pool there is destroyed, with its file."""
    if is_pool(mp):
        run(["zpool", "destroy", "-f", POOLS[mp]])
        Path(f"/var/tmp/{POOLS[mp]}.img").unlink(missing_ok=True)
    else:
        run(["umount", str(mp)], check=False)


def env() -> dict:
    return dict(os.environ, RESTIC_PASSWORD=compare.PASSWORD, RUSTIC_PASSWORD=compare.PASSWORD)


def restore(tool: str, work: Path) -> dict:
    """compare.restore()'s commands, keeping what the tool says."""
    repo = str(work / tool)
    restorer = compare.CROSS.get(tool, (tool, tool))[1]
    if restorer == "restic-bko":
        cmd = [compare.restic_exe(restorer), "-r", repo, "restore", f"latest:{SRC}", "--target", str(DST)]
    else:
        cmd = [compare.rustic_exe(), "-r", repo, "--no-progress", "--no-cache", "restore",
               f"latest:{SRC}", str(DST)]
    r = run(cmd, check=False, env=env())
    lines = [l for l in (r.stderr + "\n" + r.stdout).splitlines()
             if any(w in l.lower() for w in ("warn", "error", "ignor", "fail", "not restored", "unsupported"))]
    return {"exit": r.returncode, "messages": lines[:40], "stderr_tail": r.stderr[-1500:]}


def fsmeta_compare(fsmeta: list[str], before: str, after: str, out: str) -> dict:
    c = run(fsmeta + ["compare", before, after, "--json", out], check=False)
    print(c.stdout, flush=True)
    return json.loads(Path(out).read_text())


def full_diff(before: str, after: str) -> dict[str, list[str]]:
    sys.path.insert(0, str(compare.HERE))
    import fsmeta  # noqa: E402
    return fsmeta.differences(json.loads(Path(before).read_text()), json.loads(Path(after).read_text()))


def reset_atimes(before: dict, paths: list[str]) -> None:
    """Put back the atimes a backup changed, so the next tool starts clean."""
    for rel in paths:
        e = before[rel]
        try:
            os.utime(SRC / rel, ns=(e["atime"], e["mtime"]), follow_symlinks=False)
        except OSError as ex:
            print(f"  can't reset atime of {rel}: {ex}")


def snapshot_nodes(repo: Path) -> dict[str, dict]:
    """Every node of the latest snapshot (read with restic-bko, whichever
    tool wrote it), without content and subtree ids, which differ per repo."""
    restic = compare.restic_exe("restic-bko")
    snaps = json.loads(run([restic, "-r", str(repo), "snapshots", "--json", "latest"], quiet=True,
                           env=env()).stdout)
    nodes: dict[str, dict] = {}

    def walk(tree: str, prefix: str) -> None:
        t = json.loads(run([restic, "-r", str(repo), "cat", "blob", tree], quiet=True, env=env()).stdout)
        for n in t.get("nodes") or []:
            path = f"{prefix}/{n['name']}" if prefix else n["name"]
            sub = n.pop("subtree", None)
            n.pop("content", None)
            nodes[path] = n
            if sub:
                walk(sub, path)

    walk(snaps[0]["tree"], "")
    return nodes


def norm_time(v):
    """RFC 3339 times as (seconds, nanoseconds) in UTC: restic writes "Z",
    rustic "+00:00", and either drops a zero fraction."""
    m = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)", str(v))
    if not m:
        return v
    from datetime import datetime
    tz = "+00:00" if m[3] == "Z" else m[3]
    return (int(datetime.fromisoformat(m[1] + tz).timestamp()), int((m[2] or "0").ljust(9, "0")[:9]))


# Not stored by stock rustic unless asked (--set-devid); the parents of the
# backed-up path are bare nodes in rustic's snapshots.
NODE_IGNORE = {"device_id"}


def node_differences(a: dict[str, dict], b: dict[str, dict], reset: set[str] = frozenset()) -> dict[str, list[str]]:
    """What differs between two snapshots' nodes.  reset: the paths (relative
    to SRC) whose atimes this script put back between the backups, which
    changed their ctimes: those aren't compared."""
    out: dict[str, list[str]] = {}
    for p in sorted(a.keys() - b.keys()):
        out.setdefault("only in first", []).append(p)
    for p in sorted(b.keys() - a.keys()):
        out.setdefault("only in second", []).append(p)
    src = str(SRC).lstrip("/") + "/"
    for p in sorted(a.keys() & b.keys()):
        if src[:-1].startswith(p + "/"):  # SRC's parents
            continue
        na, nb = a[p], b[p]
        ignore = NODE_IGNORE | ({"ctime"} if p.startswith(src) and p[len(src):] in reset else set())
        for k in sorted((na.keys() | nb.keys()) - ignore):
            va, vb = na.get(k), nb.get(k)
            if k in ("atime", "mtime", "ctime"):
                va, vb = norm_time(va), norm_time(vb)
            if va != vb:
                out.setdefault(k, []).append(f"{p}: {json.dumps(na.get(k))[:160]} != {json.dumps(nb.get(k))[:160]}")
    return out


def blocks_mismatch(work: Path, tools: list[str]) -> dict[str, list[str]]:
    """Files restored with holes by one tool and fully allocated by another.
    Not other differences in st_blocks: ZFS counts its compressed indirect
    blocks in it too, which vary from one write of a dense file to the next,
    and what a file restored sparse has allocated follows its blobs (S4:
    each one's zeros at the start are left out), whose boundaries depend on
    the chunker polynomial of each repository."""
    rec = {t: json.loads((work / f"after-{t}.json").read_text()) for t in tools
           if (work / f"after-{t}.json").exists()}
    out: dict[str, list[str]] = {}
    names = list(rec)

    def dense(e: dict) -> bool:
        return e["blocks"] * 512 >= e.get("size", 0)

    for t in names[1:]:
        a, b = rec[names[0]], rec[t]
        out[f"{names[0]} vs {t}"] = [
            f"{p}: {a[p]['blocks']} != {b[p]['blocks']}" for p in sorted(a.keys() & b.keys())
            if a[p]["type"] == "file" and "blocks" in a[p] and "blocks" in b[p]
            and dense(a[p]) != dense(b[p])]
    return out


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("must run as root")
    args = sys.argv[1:]
    if not args or args[0] not in MKFS:
        raise SystemExit(__doc__)
    fs, args = args[0], args[1:]
    onto = fs
    if args[:1] == ["--onto"]:
        if len(args) < 2 or args[1] not in MKFS:
            raise SystemExit(__doc__)
        onto, args = args[1], args[2:]
    tools = args or TOOLS
    work = WORK / (fs if onto == fs else f"{fs}-onto-{onto}")
    work.mkdir(parents=True, exist_ok=True)
    os.chdir(work)
    lock = open(".lock", "w")  # held until exit
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(f"fs_roundtrip: {work.name} is running already")
    set_run(work.name)
    # compare.py's backup() backs up SRC_MP.
    compare.SRC_MP, compare.DST_MP = SRC, DST
    fsmeta = [sys.executable, "-I", str(compare.HERE / "fsmeta.py")]
    for mp in (SRC, DST):
        unmount(mp)

    src_img, dst_img = (Path(f"/var/tmp/bko-fsrt-{work.name}-{s}.img") for s in ("src", "dst"))
    make_fs(fs, src_img, SRC)
    r = run([str(compare.HERE / "torture.sh"), str(SRC)])
    skipped = [l for l in r.stderr.splitlines() if "skipped" in l]
    r = run(["bash", "-c", EXTRAS, "extras", str(SRC / TORTURE)])
    skipped += [l for l in r.stderr.splitlines() if "skipped" in l]
    remount(SRC, "ro")
    run(fsmeta + ["record", str(SRC), "before.json"])
    before = json.loads(Path("before.json").read_text())
    results = {"fs": fs, "onto": onto, "torture_skipped": skipped, "tools": {}}
    reset: set[str] = set()  # the paths whose atimes were put back
    try:
        for tool in tools:
            print(f"== {fs}: {tool}", flush=True)
            res = results["tools"][tool] = {}
            # The backup, on a source that records every read in its atimes.
            remount(SRC, "rw", "strictatime")
            try:
                compare.backup(tool, work)
            finally:
                remount(SRC, "ro")
            run(fsmeta + ["record", str(SRC), f"src-after-{tool}.json"])
            changed = full_diff("before.json", f"src-after-{tool}.json")
            res["source_changed_by_backup"] = {k: v[:8] for k, v in changed.items()}
            if changed:
                remount(SRC, "rw")
                paths = {p for v in changed.values() for p in v if p in before}
                reset_atimes(before, sorted(paths))
                reset |= paths
                remount(SRC, "ro")

            make_fs(onto, dst_img, DST)
            try:
                res["restore"] = restore(tool, work)
                remount(DST, "noatime")
                run(fsmeta + ["record", str(DST), f"after-{tool}.json"])
                res["diff"] = fsmeta_compare(fsmeta, "before.json", f"after-{tool}.json",
                                             f"diff-{tool}.json")["differences"]
                res["diff_all"] = full_diff("before.json", f"after-{tool}.json")
                results["coverage"] = json.loads(Path(f"diff-{tool}.json").read_text())["coverage"]

                # Restore again, over a changed copy of what was restored.
                r = run(["bash", "-c", OVER, "over", str(DST / TORTURE)])
                res["over_skipped"] = [l for l in r.stderr.splitlines() if "skipped" in l]
                res["over_restore"] = restore(tool, work)
                run(fsmeta + ["record", str(DST), f"after-over-{tool}.json"])
                fsmeta_compare(fsmeta, "before.json", f"after-over-{tool}.json", f"diff-over-{tool}.json")
                d = full_diff("before.json", f"after-over-{tool}.json")
                res["over_diff_all"] = {k: v for k, v in
                                        ((k, [p for p in v if (k, p) not in OVER_EXPECTED])
                                         for k, v in d.items()) if v}

                # And once more, over the result unchanged (a restore run twice).
                res["rerun_restore"] = restore(tool, work)
                run(fsmeta + ["record", str(DST), f"after-rerun-{tool}.json"])
                d = full_diff("before.json", f"after-rerun-{tool}.json")
                res["rerun_diff_all"] = {k: v for k, v in
                                         ((k, [p for p in v if (k, p) not in OVER_EXPECTED])
                                          for k, v in d.items()) if v}
            finally:
                unmount(DST)
                dst_img.unlink(missing_ok=True)
    finally:
        unmount(SRC)
        src_img.unlink(missing_ok=True)

    if "restic-bko" in tools and "rustic-bko" in tools:
        a, b = snapshot_nodes(work / "restic-bko"), snapshot_nodes(work / "rustic-bko")
        Path("snapshot-nodes.json").write_text(json.dumps({"restic-bko": a, "rustic-bko": b}, indent=1))
        results["sparse_marked"] = {
            t: sorted(p for p, n in nodes.items() if "unix.sparse" in (n.get("generic_attributes") or {}))
            for t, nodes in (("restic-bko", a), ("rustic-bko", b))}
        # Inode numbers, ctimes, device ids and the like are the same source's:
        # every field should be equal, but the ctimes reset_atimes() changed.
        results["atimes_reset"] = sorted(reset)
        results["snapshot_node_diff (restic-bko vs rustic-bko)"] = node_differences(a, b, reset)
    results["blocks_mismatch"] = blocks_mismatch(work, tools)

    Path("results.json").write_text(json.dumps(results, indent=1))
    print(f"\n{fs} onto {onto}: skipped {len(skipped)}: " + "; ".join(s.split(': ', 2)[-1] for s in skipped))
    print("coverage: " + ", ".join(f"{k} {v}" for k, v in sorted(results.get("coverage", {}).items())))
    for tool, t in results["tools"].items():
        diffs = "; ".join(f"{k} {len(v)}: {', '.join(v[:4])}" for k, v in t.get("diff_all", {}).items()) or "none"
        over = "; ".join(f"{k} {len(v)}: {', '.join(v[:4])}" for k, v in t.get("over_diff_all", {}).items()) or "none"
        src = "; ".join(f"{k}: {', '.join(v[:4])}" for k, v in t["source_changed_by_backup"].items()) or "none"
        rs = t.get("restore", {})
        print(f"{tool:16} restore exit {rs.get('exit')}  diff: {diffs}")
        print(f"{'':16} over-existing exit {t.get('over_restore', {}).get('exit')}  diff: {over}")
        rerun = "; ".join(f"{k} {len(v)}: {', '.join(v[:4])}" for k, v in t.get("rerun_diff_all", {}).items()) or "none"
        print(f"{'':16} rerun exit {t.get('rerun_restore', {}).get('exit')}  diff: {rerun}")
        for m in t.get("over_restore", {}).get("messages", [])[:6] + t.get("rerun_restore", {}).get("messages", [])[:6]:
            print(f"{'':16} over/rerun says: {m}")
        print(f"{'':16} source changed by backup: {src}")
        for m in rs.get("messages", [])[:10]:
            print(f"{'':16} restore says: {m}")
    for k in ("sparse_marked", "blocks_mismatch"):
        if k in results:
            print(f"{k}: {json.dumps(results[k])}")
    nd = results.get("snapshot_node_diff (restic-bko vs rustic-bko)")
    if nd is not None:
        print(f"snapshot node differences restic-bko vs rustic-bko: {sum(map(len, nd.values()))}")
        for k, v in nd.items():
            print(f"  {k} {len(v)}: " + "; ".join(v[:4]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
