#!/usr/bin/env python3
"""Round trip of torture.sh's files through BackOtter's archivers on one kind
of file system, made with every feature those files need: project IDs and
case-insensitive directories on ext4 and f2fs, and so on.  compare.py tests
the guests' own file systems, which mostly lack them.

Usage: sudo fs_roundtrip.py FSTYPE [--onto FSTYPE2] [TOOL ...]
         FSTYPE: ext4 xfs btrfs f2fs jfs ext2 vfat ext4-plain bcachefs (loop images
                 in /var/tmp), tmpfs (4 GiB each, in RAM),
                 zfs (pools bkotest-RUN-src and bkotest-RUN-dst on files in
                 /var/tmp, destroyed afterwards),
                 zfs-ds (file systems bko-fsrt-RUN-src and bko-fsrt-RUN-dst in an
                 existing pool, $BKO_ZFS_POOL or the root's, destroyed afterwards:
                 e.g. a guest's encrypted or mirrored root pool)
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
unix.sparse included), and st_blocks of each restored file.  Extended
attributes: every snapshot's are sorted by name, those of a file that has
them set out of order (user.zz-first first) too (results.json
"xattr_order", AUDIT-R6 N4).  Files marked unix.sparse, after the first
restore: st_blocks against the source's (results.json "sparse_blocks",
SPEC3 3 as revised: no more where the source's holes are aligned to the
destination's block size B, "over" if more; a failure if more than the
source's plus a block per blob).  With $BKO_OLD_RESTIC (an earlier
restic-bko), SPEC3 2's snapshot identity: in a copy of restic-bko's
repository, the source backed up again with it and with rustic-bko gives
every file restic-bko's content blob IDs ("identity").  Sockets: both
tools skip them (restic always has, rustic since rustic_core 1a2a4a4), so
torture.sh's socket must be "missing" after every restore, with no socket
node in any snapshot, nothing said about it at backup or restore, and the
first restore's exit 0 (results.json "sockets", SPEC3 1); a source without
one (torture.sh made none) fails that check too.

restic-bko and rustic-bko are found as compare.py finds them ($RESTIC_BKO,
$RUSTIC_BKO).  Results go to ~/backotter-work/fs-roundtrip/RUN/, where RUN is
FSTYPE[-onto-FSTYPE2].

Each RUN has mount points (/mnt/bko-fsrt/RUN/{src,dst}), images and pools of
its own, so different RUNs can go at the same time (e.g. xargs -P); the same
RUN twice can't, and the second one stops.  With $BKO_FSRT_PREFIX set to P
(e.g. bkor9-fsrt), every name is P's instead: pools and file systems
P-RUN-src and P-RUN-dst, mount points /mnt/P/RUN/{src,dst}, images
/var/tmp/P-RUN-{src,dst}.img, results in ~/backotter-work/fs-roundtrip/P/RUN/;
so that runs from different checkouts or sessions keep apart.

A file system this host can't make (no kernel support, no mkfs: bcachefs
needs mkfs.bcachefs, or $BKO_MKFS_BCACHEFS naming one, e.g. "chroot
/mnt/nixos mkfs.bcachefs") is skipped, with a line saying why, in
results.json too, and exit status 0.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shlex
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
    "bcachefs": ["mkfs.bcachefs", "-f"],
    "tmpfs": [],
    # POSIX ACLs and system-attribute xattrs, as a Linux root pool has them.
    "zfs": ["zpool", "create", "-f", "-O", "acltype=posix", "-O", "xattr=sa"],
    "zfs-ds": ["zfs", "create", "-o", "acltype=posix", "-o", "xattr=sa"],
}
MOUNT_TYPE = {"bcachefs": ["-t", "bcachefs"]}
TOOLS = ["restic-bko", "rustic-bko", "restic-to-rustic", "rustic-to-restic"]
WORK = compare.WORK_BASE / "fs-roundtrip"
# Names' prefix (set_run(); the module doc).
PREFIX = os.environ.get("BKO_FSRT_PREFIX", "")
# The source and destination mount points, per RUN (set_run()).
SRC = Path("/mnt/bko-fsrt/RUN/src")
DST = Path("/mnt/bko-fsrt/RUN/dst")
# ZFS pools, mounted at SRC and DST, when FSTYPE is zfs; file systems in an
# existing pool when it is zfs-ds.
POOLS: dict[Path, str] = {}
DATASETS: dict[Path, str] = {}
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

# Extended attributes set out of order, zz-first first: both tools store
# them sorted by name (AUDIT-R6 N4; results.json "xattr_order").
touch "$T/xattr-order"
for a in zz-first aa mm; do try setfattr -n user.$a -v $a "$T/xattr-order"; done

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


# Sockets are skipped by both tools (results.json "sockets"): each one in
# the source is "missing" after every restore, expected, so not a difference.


def without_sockets(d: dict[str, list[str]], sockets: set[str]) -> dict[str, list[str]]:
    """d without the sockets' "missing" entries."""
    return {k: v for k, v in ((k, [p for p in v if not (k == "missing" and p in sockets)])
                              for k, v in d.items()) if v}


def run(cmd: list[str], check: bool = True, quiet: bool = False, **kw) -> subprocess.CompletedProcess:
    if not quiet:
        print("  $ " + " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)
    if check and r.returncode:
        raise SystemExit(f"failed ({r.returncode}): {r.stderr.strip() or r.stdout.strip()}")
    return r


def set_run(run_name: str) -> None:
    """Mount points, pools and file systems of this RUN's own."""
    global SRC, DST, POOLS, DATASETS
    mnt = Path("/mnt") / (PREFIX or "bko-fsrt") / run_name
    SRC, DST = mnt / "src", mnt / "dst"
    POOLS = {SRC: f"{PREFIX or 'bkotest'}-{run_name}-src", DST: f"{PREFIX or 'bkotest'}-{run_name}-dst"}
    DATASETS = {SRC: f"{PREFIX or 'bko-fsrt'}-{run_name}-src", DST: f"{PREFIX or 'bko-fsrt'}-{run_name}-dst"}


def mkfs_cmd(fs: str) -> list[str]:
    if fs == "bcachefs" and os.environ.get("BKO_MKFS_BCACHEFS"):
        return shlex.split(os.environ["BKO_MKFS_BCACHEFS"]) + MKFS[fs][1:]
    return MKFS[fs]


def unsupported(fs: str) -> str | None:
    """Why this host can't make fs, or None."""
    kernel = {"zfs": "zfs", "zfs-ds": "zfs", "ext4-plain": "ext4", "ext2": "ext4"}.get(fs, fs)
    if kernel not in Path("/proc/filesystems").read_text().split():
        if shutil.which("modprobe"):
            run(["modprobe", kernel], check=False, quiet=True)
        if kernel not in Path("/proc/filesystems").read_text().split():
            return f"the kernel ({os.uname().release}) has no {kernel} file system"
    cmd = mkfs_cmd(fs)
    if cmd and shutil.which(cmd[0]) is None:
        return f"no {cmd[0]} here" + (" (BKO_MKFS_BCACHEFS can name one)" if fs == "bcachefs" else "")
    if fs == "zfs-ds" and not zfs_pool():
        return "no ZFS pool to make file systems in (BKO_ZFS_POOL, or a root on ZFS)"
    return None


def zfs_pool() -> str:
    """zfs-ds's pool: $BKO_ZFS_POOL, else the root's."""
    if os.environ.get("BKO_ZFS_POOL"):
        return os.environ["BKO_ZFS_POOL"]
    r = run(["zfs", "list", "-H", "-o", "name", "/"], check=False, quiet=True) if shutil.which("zfs") else None
    return r.stdout.strip().split("/")[0] if r and r.returncode == 0 else ""


def make_fs(fs: str, image: Path, mp: Path) -> None:
    unmount(mp)
    mp.mkdir(parents=True, exist_ok=True)
    if fs == "tmpfs":
        run(["mount", "-t", "tmpfs", "-o", "size=4g,mode=0755", "tmpfs", str(mp)])
        return
    if fs == "zfs-ds":
        run(MKFS[fs] + ["-o", f"mountpoint={mp}", f"{zfs_pool()}/{DATASETS[mp]}"])
        return
    if fs == "zfs":
        image = Path("/var/tmp") / f"{POOLS[mp]}.img"
    image.unlink(missing_ok=True)
    run(["truncate", "-s", "4G", str(image)])
    if fs == "zfs":
        run(MKFS[fs] + ["-m", str(mp), POOLS[mp], str(image)])
        return
    run(mkfs_cmd(fs) + [str(image)])
    run(["mount", "-o", "loop"] + MOUNT_TYPE.get(fs, []) + [str(image), str(mp)])


def zfs_at(mp: Path) -> tuple[str, str] | None:
    """("pool", name) or ("ds", name) for the ZFS this script made at mp."""
    if shutil.which("zfs") is None:
        return None
    if run(["zpool", "list", "-H", POOLS[mp]], check=False, quiet=True).returncode == 0:
        return "pool", POOLS[mp]
    pool = zfs_pool()
    if pool and run(["zfs", "list", "-H", f"{pool}/{DATASETS[mp]}"], check=False, quiet=True).returncode == 0:
        return "ds", f"{pool}/{DATASETS[mp]}"
    return None


def remount(mp: Path, *opts: str) -> None:
    """mount -o remount,OPTS; on ZFS, the dataset properties that do the same.
    Written data goes to disk first: bcachefs (Linux 6.12) remounted read only
    with dirty pages never writes them, and once they leave the page cache
    the file reads back as a hole of zeros with no blocks (round 7, eight
    file systems at once: the source changed under the backups;
    round7/logs/linux/bcprobe.sh on Chopin shows it without the sync)."""
    z = zfs_at(mp)
    if z:
        run(["zfs", "set"] + [p for o in opts for p in ZFS_PROPS[o]] + [z[1]])
    else:
        run(["sync", "-f", str(mp)])
        run(["mount", "-o", ",".join(("remount",) + opts), str(mp)])


def unmount(mp: Path) -> None:
    """Unmount mp; a ZFS pool there is destroyed, with its file, as is a
    file system zfs-ds made."""
    z = zfs_at(mp)
    if z and z[0] == "pool":
        run(["zpool", "destroy", "-f", z[1]])
        Path(f"/var/tmp/{z[1]}.img").unlink(missing_ok=True)
    elif z:
        run(["zfs", "destroy", "-r", "-f", z[1]])
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
    return {"exit": r.returncode, "messages": messages(r)[:40], "stderr_tail": r.stderr[-1500:]}


def messages(r: subprocess.CompletedProcess) -> list[str]:
    """What a tool's output says that may be a warning."""
    return [l for l in (r.stderr + "\n" + r.stdout).splitlines()
            if any(w in l.lower() for w in ("warn", "error", "ignor", "fail", "not restored", "unsupported",
                                            "sock", "skip"))]


def backup(tool: str, work: Path) -> list[str]:
    """compare.backup(), keeping what the tools say (it drops their output)."""
    said: list[str] = []
    orig = compare.run

    def keep(*a, **kw):
        r = orig(*a, **kw)
        said.extend(messages(r))
        return r

    compare.run = keep
    try:
        compare.backup(tool, work)
    finally:
        compare.run = orig
    return said


def sockets(record: str) -> list[str]:
    """The sockets in a fsmeta.py record."""
    return sorted(p for p, e in json.loads(Path(record).read_text()).items() if e["type"] == "socket")


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


def snapshot_nodes(repo: Path, contents: dict[str, list[str]] | None = None,
                   tree: str = "") -> dict[str, dict]:
    """Every node of the latest snapshot (or of the snapshot whose tree is
    tree; read with restic-bko, whichever tool wrote it), without content and
    subtree ids, which differ per repo; contents, if given, gets each file's
    content blob IDs."""
    restic = compare.restic_exe("restic-bko")
    if not tree:
        tree = json.loads(run([restic, "-r", str(repo), "snapshots", "--json", "latest"], quiet=True,
                              env=env()).stdout)[0]["tree"]
    nodes: dict[str, dict] = {}

    def walk(tree: str, prefix: str) -> None:
        t = json.loads(run([restic, "-r", str(repo), "cat", "blob", tree], quiet=True, env=env()).stdout)
        for n in t.get("nodes") or []:
            path = f"{prefix}/{n['name']}" if prefix else n["name"]
            sub = n.pop("subtree", None)
            content = n.pop("content", None)
            if contents is not None and n.get("type") == "file":
                contents[path] = content or []
            nodes[path] = n
            if sub:
                walk(sub, path)

    walk(tree, "")
    return nodes


def data_map(path: Path) -> list[tuple[int, int]]:
    """A file's data ranges, by SEEK_DATA and SEEK_HOLE."""
    out: list[tuple[int, int]] = []
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        size, off = os.fstat(fd).st_size, 0
        while off < size:
            try:
                start = os.lseek(fd, off, os.SEEK_DATA)
            except OSError:  # ENXIO: only a hole to the end
                break
            end = os.lseek(fd, start, os.SEEK_HOLE)
            out.append((start, end))
            off = end
    finally:
        os.close(fd)
    return out


def sparse_blocks(before: dict, after: dict, marked: dict[str, list[str]], maps: dict,
                  bsize: int) -> dict:
    """SPEC3 3 as revised: each file marked unix.sparse, restored into a new
    file, against its source: st_blocks no more than the source's where its
    holes are B-aligned (B the destination's block size, at least 4096),
    plus file system metadata, reported ("over"); never more than the
    source's plus one block per blob (a failure).  marked: the marked
    files' content blob IDs, by path relative to SRC."""
    res: dict = {"B": bsize, "files": {}, "over": [], "failures": []}
    for p, blobs in sorted(marked.items()):
        a, b = before.get(p), after.get(p)
        if not a or not b or "blocks" not in a or "blocks" not in b:
            res["failures"].append(f"{p}: not in the source or not restored")
            continue
        m = maps.get(p)
        aligned = m is not None and all(x % bsize == 0 or x == a["size"] for r in m for x in r)
        limit = a["blocks"] + len(blobs) * bsize // 512
        res["files"][p] = {"source": a["blocks"], "restored": b["blocks"], "blobs": len(blobs),
                           "holes_aligned": aligned}
        if b["blocks"] > limit:
            res["failures"].append(f"{p}: {b['blocks']} blocks restored, more than the source's "
                                   f"{a['blocks']} + {len(blobs)} blobs x {bsize}")
        elif aligned and b["blocks"] > a["blocks"]:
            res["over"].append(f"{p}: {b['blocks']} > {a['blocks']} (holes {bsize}-aligned)")
    return res


def identity(work: Path, old_restic: str) -> dict:
    """SPEC3 2: a file with holes read by its data extents gives the chunker
    the bytes plain reading gives.  In a copy of restic-bko's repository
    (one chunker polynomial), SRC backed up again with old_restic (a
    restic-bko from before, reading every byte) and with rustic-bko (whose
    chunker cuts where restic's does, rustic_core 9f88530): every file's
    content blob IDs must be those of restic-bko's own backup.  An old
    rustic-bko isn't a reference: its chunker cut elsewhere."""
    repo = work / "identity"
    shutil.rmtree(repo, ignore_errors=True)
    shutil.copytree(work / "restic-bko", repo)
    restic = compare.restic_exe("restic-bko")
    res: dict = {"old_restic": old_restic, "failures": []}
    for tag, cmd in (("bko-old", [old_restic, "-r", str(repo), "backup", "-q", "--force"]),
                     ("bko-rustic", [compare.rustic_exe(), "-r", str(repo), "--no-progress", "--no-cache",
                                     "backup", "--force"])):
        r = run(cmd + ["--tag", tag, str(SRC)], check=False, env=env())
        res[f"{tag} exit"] = r.returncode
        if r.returncode:
            res["failures"].append(f"{tag} backup exit {r.returncode}: {r.stderr.strip()[-300:]}")
    snaps = json.loads(run([restic, "-r", str(repo), "snapshots", "--json"], quiet=True, env=env()).stdout)
    trees = {(s.get("tags") or ["new"])[0]: s["tree"] for s in snaps}
    contents: dict[str, dict[str, list[str]]] = {}
    for tag in ("new", "bko-old", "bko-rustic"):
        if tag in trees:
            contents[tag] = {}
            snapshot_nodes(repo, contents[tag], trees[tag])
        else:
            res["failures"].append(f"no {tag} snapshot")
    new = contents.get("new", {})
    res["files"] = len(new)
    res["blobs"] = sum(map(len, new.values()))
    for tag in ("bko-old", "bko-rustic"):
        other = contents.get(tag, {})
        for p in sorted(new.keys() | other.keys()):
            if new.get(p) != other.get(p):
                res["failures"].append(f"{tag}: {p}: {len(other.get(p) or [])} blobs, not restic-bko's "
                                       f"{len(new.get(p) or [])}" + ("" if p in new and p in other else " (missing)"))
    shutil.rmtree(repo, ignore_errors=True)
    return res


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
    work = WORK / PREFIX / (fs if onto == fs else f"{fs}-onto-{onto}")
    work.mkdir(parents=True, exist_ok=True)
    os.chdir(work)
    lock = open(".lock", "w")  # held until exit
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(f"fs_roundtrip: {work.name} is running already")
    set_run(work.name)
    why = next((f"{f}: {w}" for f in dict.fromkeys((fs, onto)) if (w := unsupported(f))), None)
    if why:
        Path("results.json").write_text(json.dumps({"fs": fs, "onto": onto, "skipped": why}, indent=1))
        print(f"fs_roundtrip: skipped {fs} onto {onto}: {why}")
        return 0
    # compare.py's backup() backs up SRC_MP.
    compare.SRC_MP, compare.DST_MP = SRC, DST
    fsmeta = [sys.executable, "-I", str(compare.HERE / "fsmeta.py")]
    for mp in (SRC, DST):
        unmount(mp)

    src_img, dst_img = (Path(f"/var/tmp/{PREFIX or 'bko-fsrt'}-{work.name}-{s}.img") for s in ("src", "dst"))
    make_fs(fs, src_img, SRC)
    r = run([str(compare.HERE / "torture.sh"), str(SRC)])
    skipped = [l for l in r.stderr.splitlines() if "skipped" in l]
    r = run(["bash", "-c", EXTRAS, "extras", str(SRC / TORTURE)])
    skipped += [l for l in r.stderr.splitlines() if "skipped" in l]
    try:
        xattr_listed = os.listxattr(SRC / TORTURE / "xattr-order")
    except OSError:
        xattr_listed = []
    remount(SRC, "ro")
    run(fsmeta + ["record", str(SRC), "before.json"])
    before = json.loads(Path("before.json").read_text())
    src_sockets = set(sockets("before.json"))
    # The data ranges of the files with holes, for SPEC3 3's B-alignment.
    maps: dict[str, list[tuple[int, int]]] = {}
    for p, e in before.items():
        if e.get("holes"):
            try:
                maps[p] = data_map(SRC / p)
            except OSError as ex:
                print(f"  no data map of {p}: {ex}")
    results = {"fs": fs, "onto": onto, "torture_skipped": skipped, "tools": {}}
    reset: set[str] = set()  # the paths whose atimes were put back
    try:
        for tool in tools:
            print(f"== {fs}: {tool}", flush=True)
            res = results["tools"][tool] = {}
            # The backup, on a source that records every read in its atimes.
            remount(SRC, "rw", "strictatime")
            try:
                res["backup_messages"] = backup(tool, work)
            finally:
                remount(SRC, "ro")
            run(fsmeta + ["record", str(SRC), f"src-after-{tool}.json"])
            changed = full_diff("before.json", f"src-after-{tool}.json")
            res["source_changed_by_backup"] = {k: v[:8] for k, v in changed.items()}
            if changed:
                remount(SRC, "rw")
                paths = {p for v in changed.values() for p in v if p in before}
                # Every name of a hard-linked file: putting its atime back
                # changes the inode's ctime, which the snapshot comparison
                # must then skip under each of its names.
                paths |= {q for p in list(paths) for q in before[p].get("links", [])}
                reset_atimes(before, sorted(paths))
                reset |= paths
                remount(SRC, "ro")

            make_fs(onto, dst_img, DST)
            try:
                res["restore"] = restore(tool, work)
                res["dst_bsize"] = max(os.statvfs(DST).f_bsize, 4096)
                remount(DST, "noatime")
                run(fsmeta + ["record", str(DST), f"after-{tool}.json"])
                res["diff"] = fsmeta_compare(fsmeta, "before.json", f"after-{tool}.json",
                                             f"diff-{tool}.json")["differences"]
                res["diff_all"] = without_sockets(full_diff("before.json", f"after-{tool}.json"), src_sockets)
                results["coverage"] = json.loads(Path(f"diff-{tool}.json").read_text())["coverage"]

                # Restore again, over a changed copy of what was restored.
                r = run(["bash", "-c", OVER, "over", str(DST / TORTURE)])
                res["over_skipped"] = [l for l in r.stderr.splitlines() if "skipped" in l]
                res["over_restore"] = restore(tool, work)
                run(fsmeta + ["record", str(DST), f"after-over-{tool}.json"])
                fsmeta_compare(fsmeta, "before.json", f"after-over-{tool}.json", f"diff-over-{tool}.json")
                d = full_diff("before.json", f"after-over-{tool}.json")
                res["over_diff_all"] = without_sockets({k: v for k, v in
                                                        ((k, [p for p in v if (k, p) not in OVER_EXPECTED])
                                                         for k, v in d.items()) if v}, src_sockets)

                # And once more, over the result unchanged (a restore run twice).
                res["rerun_restore"] = restore(tool, work)
                run(fsmeta + ["record", str(DST), f"after-rerun-{tool}.json"])
                d = full_diff("before.json", f"after-rerun-{tool}.json")
                res["rerun_diff_all"] = without_sockets({k: v for k, v in
                                                         ((k, [p for p in v if (k, p) not in OVER_EXPECTED])
                                                          for k, v in d.items()) if v}, src_sockets)
                # Skipped: none restored, by any of the three restores.
                res["sockets_restored"] = {s: socks for s in ("", "over-", "rerun-")
                                           if (socks := sockets(f"after-{s}{tool}.json"))}
            finally:
                unmount(DST)
                dst_img.unlink(missing_ok=True)
        # SPEC3 2, with $BKO_OLD_RESTIC (an earlier restic-bko).
        if os.environ.get("BKO_OLD_RESTIC") and (work / "restic-bko").is_dir() and "restic-bko" in tools:
            print(f"== {fs}: snapshot identity (SPEC3 2)", flush=True)
            results["identity"] = identity(work, os.environ["BKO_OLD_RESTIC"])
    finally:
        unmount(SRC)
        src_img.unlink(missing_ok=True)

    # Sockets are skipped: none in any snapshot, none restored, nothing said,
    # the first restore's exit 0 (those over a tree may fail for reasons of
    # their own: an immutable file in the way).  Each snapshot read once.
    contents: dict[str, dict[str, list[str]]] = {t: {} for t in tools}
    snaps = {t: snapshot_nodes(work / t, contents[t]) for t in tools if (work / t).is_dir()}
    socks = {"source": sockets("before.json"), "failures": []}
    if not socks["source"]:
        socks["failures"].append("no socket in the source (torture.sh made none)")
    for tool in tools:
        res = results["tools"].get(tool, {})
        socks["failures"] += [f"{tool}: socket node in snapshot: {p}" for p, n in snaps.get(tool, {}).items()
                              if n.get("type") == "socket"]
        socks["failures"] += [f"{tool}: socket restored ({s or 'first '}restore): {p}"
                              for s, v in res.get("sockets_restored", {}).items() for p in v]
        socks["failures"] += [f"{tool}: says at {k}: {m}" for k in ("backup", "restore", "over_restore", "rerun_restore")
                              for m in sorted(set((res.get(f"{k}_messages") if k == "backup" else
                                                   res.get(k, {}).get("messages", []) +
                                                   res.get(k, {}).get("stderr_tail", "").splitlines()) or []))
                              if "sock" in m.lower()]
        if "restore" in res and res["restore"].get("exit") != 0:
            socks["failures"].append(f"{tool}: first restore exit {res['restore'].get('exit')}")
    socks["ok"] = not socks["failures"]
    results["sockets"] = socks
    # Extended attributes sorted by name in every node of every snapshot;
    # xattr-order's all there, though set zz-first first.
    xo_path = f"{str(SRC).lstrip('/')}/{TORTURE}/xattr-order"
    xo = {"listed in the source": xattr_listed, "failures": []}
    if not xattr_listed and any(e.get("xattrs") for e in before.values()):
        xo["failures"].append("xattr-order has no extended attributes (setfattr failed): not exercised")
    for tool, nodes in snaps.items():
        for p, n in nodes.items():
            names = [x.get("name") for x in n.get("extended_attributes") or []]
            if names != sorted(names):
                xo["failures"].append(f"{tool}: {p}: stored {names}, not sorted")
            if p == xo_path:
                xo[tool] = names
                if sorted(names) != sorted(xattr_listed):
                    xo["failures"].append(f"{tool}: {p}: stored {names}, source has {xattr_listed}")
    xo["ok"] = not xo["failures"]
    results["xattr_order"] = xo
    if "restic-bko" in tools and "rustic-bko" in tools:
        a, b = snaps["restic-bko"], snaps["rustic-bko"]
        Path("snapshot-nodes.json").write_text(json.dumps({"restic-bko": a, "rustic-bko": b}, indent=1))
        results["sparse_marked"] = {
            t: sorted(p for p, n in nodes.items() if "unix.sparse" in (n.get("generic_attributes") or {}))
            for t, nodes in (("restic-bko", a), ("rustic-bko", b))}
        # Inode numbers, ctimes, device ids and the like are the same source's:
        # every field should be equal, but the ctimes reset_atimes() changed.
        results["atimes_reset"] = sorted(reset)
        results["snapshot_node_diff (restic-bko vs rustic-bko)"] = node_differences(a, b, reset)
    results["blocks_mismatch"] = blocks_mismatch(work, tools)
    # SPEC3 3: the marked files' st_blocks after the first restore (into a
    # new file system), against the source's.
    src_prefix = str(SRC).lstrip("/") + "/"
    results["sparse_blocks"] = {}
    for tool in tools:
        res = results["tools"].get(tool, {})
        if tool in snaps and "dst_bsize" in res and (work / f"after-{tool}.json").exists():
            marked = {p[len(src_prefix):]: contents[tool].get(p, []) for p, n in snaps[tool].items()
                      if p.startswith(src_prefix) and "unix.sparse" in (n.get("generic_attributes") or {})}
            results["sparse_blocks"][tool] = sparse_blocks(
                before, json.loads((work / f"after-{tool}.json").read_text()), marked, maps, res["dst_bsize"])

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
        for m in t.get("backup_messages", [])[:6]:
            print(f"{'':16} backup says: {m}")
        for m in rs.get("messages", [])[:10]:
            print(f"{'':16} restore says: {m}")
    print(f"sockets (SPEC3 1: skipped by both): {'as expected' if results['sockets']['ok'] else 'NOT AS EXPECTED'}; "
          f"source {results['sockets']['source']}, "
          + ("; ".join(results["sockets"]["failures"]) or "skipped by every tool (none in a snapshot, none restored, nothing said)"))
    xo = results["xattr_order"]
    print(f"xattr order (AUDIT-R6 N4: stored sorted by name): {'as expected' if xo['ok'] else 'NOT AS EXPECTED'}; "
          f"xattr-order listed in the source as {xo['listed in the source']}, stored as "
          + ", ".join(f"{t} {xo[t]}" for t in tools if t in xo)
          + ("; " + "; ".join(xo["failures"][:8]) if xo["failures"] else ""))
    for k in ("sparse_marked", "blocks_mismatch"):
        if k in results:
            print(f"{k}: {json.dumps(results[k])}")
    for tool, sb in results["sparse_blocks"].items():
        print(f"sparse blocks (SPEC3 3), {tool}, B {sb['B']}: "
              f"{'as expected' if not sb['failures'] else 'NOT AS EXPECTED'}; "
              + ", ".join(f"{p.rsplit('/', 1)[-1]} {f['restored']}/{f['source']} ({f['blobs']} blobs"
                          f"{'' if f['holes_aligned'] else ', holes not aligned'})" for p, f in sb["files"].items())
              + "".join(f"; over the source's: {o}" for o in sb["over"])
              + "".join(f"; {m}" for m in sb["failures"]))
    idn = results.get("identity")
    print("snapshot identity (SPEC3 2): " + (
        "not run (BKO_OLD_RESTIC unset)" if idn is None else
        f"{'as expected' if not idn['failures'] else 'NOT AS EXPECTED'}; {idn.get('files')} files, "
        f"{idn.get('blobs')} blobs: old restic-bko ({idn['old_restic']}) and rustic-bko give restic-bko's"
        + "".join(f"; {m}" for m in idn["failures"][:12])))
    nd = results.get("snapshot_node_diff (restic-bko vs rustic-bko)")
    if nd is not None:
        print(f"snapshot node differences restic-bko vs rustic-bko: {sum(map(len, nd.values()))}")
        for k, v in nd.items():
            print(f"  {k} {len(v)}: " + "; ".join(v[:4]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
