#!/bin/bash
# exact-rt.sh: rustic-bko's restore --exact and --clone-from on btrfs, the
# way bko-holt load restores shared snapshots (docs/design/btrfs-snapshots.md).
#
# Builds an openSUSE-shaped btrfs in RAM: @/.snapshots/1/snapshot is the live
# root, mounted at the source's /, with @/.snapshots at /.snapshots, and the
# read-only snapper snapshots 2 and 3 taken as the live tree changed.  It
# backs that up, restores it onto a new btrfs as bko-holt does (the main
# restore leaving out the alias and the snapshots' contents, then each
# snapshot a btrfs snapshot of the one before it, made exact), and checks:
#
#   - every tree is the source's: types, contents, modes, owners, mtimes,
#     atimes, symlink targets, extended attributes, hard-link groups;
#   - the restored snapshots share their extents: each one's exclusive size
#     (qgroups) is no more than the source's plus SLACK_MIB;
#   - a file added after snapshot 2 and still in the live tree is cloned
#     (the same physical extent as the live tree's copy);
#   - a file whose size and mtime match but whose contents don't is fixed;
#   - an --exact restore over a tree that already matches changes nothing
#     (no inode's ctime moves);
#   - no AlreadyExists in any restore's output.
#
# Then the CachyOS/Fedora layout, where the snapshot is inside the subvolume
# cloned from: @ is the live root, its snapper snapshot @/.snapshots/1/snapshot
# lies inside it, and bko-holt restores that snapshot, seeded from @, with
# --clone-from /=<top>/@.  It checks that:
#
#   - @ and the snapshot are the source's, as above;
#   - a seeded file of the snapshot's size and other contents gets the
#     snapshot's contents, a small one and a 4 MiB one (rustic-bko before
#     0.11.4+backotter.11 cloned it from itself: 0 bytes, the snapshot's
#     mtime, exit 0; the Thinkpad's CachyOS BackShot1);
#   - a file whose contents are elsewhere in @ (renamed there since) is
#     cloned from there.
#
# Usage (root, on a test host): exact-rt.sh RUSTIC_BKO
#   WORK      default /mnt/bko-ram/exact-rt-$$ (deleted on a pass)
#   SLACK_MIB default 8
set -euo pipefail

R=${1:?usage: exact-rt.sh RUSTIC_BKO}
WORK=${WORK:-/mnt/bko-ram/exact-rt-$$}
SLACK_MIB=${SLACK_MIB:-8}
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$WORK"
SRC_IMG=$WORK/src.img DST_IMG=$WORK/dst.img REPO=$WORK/repo
S=$WORK/s T=$WORK/t ST=$WORK/stop DT=$WORK/dtop
C_IMG=$WORK/c.img CD_IMG=$WORK/cd.img C_REPO=$WORK/crepo CT=$WORK/ctop CDT=$WORK/cdtop
export RUSTIC_PASSWORD=exact-rt RUSTIC_NO_PROGRESS=true RUSTIC_NO_CACHE=true
fail=0
bad() { echo "FAIL: $*"; fail=1; }
cleanup() {
    for m in "$S/.snapshots" "$S" "$ST" "$T/.snapshots" "$T" "$DT" "$CT" "$CDT"; do mountpoint -q "$m" 2>/dev/null && umount "$m"; done
    for l in "${SRC_LOOP:-}" "${DST_LOOP:-}" "${C_LOOP:-}" "${CD_LOOP:-}"; do [ -n "$l" ] && losetup -d "$l" 2>/dev/null; done
    true
}
trap cleanup EXIT

mkfs() {
    truncate -s 1536M "$1"
    mkfs.btrfs -q -f "$1"
    losetup --find --show "$1"
}

# ---- the source ----
SRC_LOOP=$(mkfs "$SRC_IMG")
mkdir -p "$ST" "$S"
mount -o noatime "$SRC_LOOP" "$ST"
btrfs -q subvolume create "$ST/@"
btrfs -q subvolume create "$ST/@/.snapshots"
mkdir -p "$ST/@/.snapshots/1"
btrfs -q subvolume create "$ST/@/.snapshots/1/snapshot"
L=$ST/@/.snapshots/1/snapshot
rnd() { head -c "$2" /dev/urandom >"$1"; }
# State A (snapshot 2)
mkdir -p "$L/usr/lib" "$L/etc" "$L/.snapshots" "$L/var"
rnd "$L/usr/lib/big.so" $((48 << 20))
rnd "$L/usr/lib/old-pkg.bin" $((24 << 20))
for i in $(seq 1 400); do echo "small $i" >"$L/usr/lib/s$i"; done
ln -s big.so "$L/usr/lib/libbig.so"
ln -s s1 "$L/usr/lib/link-a"
echo one >"$L/etc/h1" && ln "$L/etc/h1" "$L/etc/h2"
echo "AAAA" >"$L/etc/trick" && touch -d @1700000000 "$L/etc/trick"
setfattr -n user.color -v blue "$L/etc/h1"
echo cfg >"$L/etc/cfg" && chmod 600 "$L/etc/cfg"
mkfifo "$L/var/fifo"
for f in "$ST/@/.snapshots"/{2,3}; do mkdir -p "$f"; echo "<snapshot/>" >"$f/info.xml"; done
btrfs -q subvolume snapshot -r "$L" "$ST/@/.snapshots/2/snapshot"
# State B (snapshot 3)
dd if=/dev/urandom of="$L/usr/lib/big.so" bs=1M count=1 seek=20 conv=notrunc status=none
rnd "$L/usr/lib/pkg-v2.bin" $((32 << 20))
rm "$L/usr/lib/old-pkg.bin" "$L/usr/lib/s7"
ln -sfn s2 "$L/usr/lib/link-a"
cp "$L/etc/h1" "$L/etc/h2.new" && mv "$L/etc/h2.new" "$L/etc/h2"   # h1, h2 apart
echo two >>"$L/etc/h1"
echo "BBBB" >"$L/etc/trick" && touch -d @1700000000 "$L/etc/trick"   # same size, same mtime
chmod 644 "$L/etc/cfg"
setfattr -x user.color "$L/etc/h1"
btrfs -q subvolume snapshot -r "$L" "$ST/@/.snapshots/3/snapshot"
# State C (the live tree)
echo "CCCC" >>"$L/etc/cfg"
# Labels the live tree has and the snapshots don't (Tumbleweed's): the
# snapshots made from it must lose them.
setfattr -n security.selinux -v 'system_u:object_r:etc_t:s0' "$L/etc/cfg" "$L/etc/h1" "$L/usr/lib/big.so"
rnd "$L/usr/lib/live-only.bin" $((8 << 20))
sync
mount -o noatime,subvol=@/.snapshots/1/snapshot "$SRC_LOOP" "$S"
mount -o noatime,subvol=@/.snapshots "$SRC_LOOP" "$S/.snapshots"

# ---- backup ----
"$R" -r "$REPO" init >/dev/null 2>&1
"$R" -r "$REPO" backup --set-atime yes --as-path / "$S" >"$WORK/backup.log" 2>&1 || { cat "$WORK/backup.log"; exit 1; }
SNAP=latest

# ---- the restore, as bko-holt's plan does it ----
DST_LOOP=$(mkfs "$DST_IMG")
mkdir -p "$DT" "$T"
mount "$DST_LOOP" "$DT"
btrfs -q subvolume create "$DT/@"
btrfs -q subvolume create "$DT/@/.snapshots"
mkdir -p "$DT/@/.snapshots/1"
btrfs -q subvolume create "$DT/@/.snapshots/1/snapshot"
umount "$DT"
mount -o subvol=@/.snapshots/1/snapshot "$DST_LOOP" "$T"
mkdir -p "$T/.snapshots"
mount -o subvol=@/.snapshots "$DST_LOOP" "$T/.snapshots"
printf '%s\n' '!/.snapshots/1/snapshot/**' '!/.snapshots/2/snapshot/**' '!/.snapshots/3/snapshot/**' >"$WORK/globs"
RUSTIC_STRICT_EXIT=true "$R" -r "$REPO" restore --numeric-id --target-metadata --glob-file "$WORK/globs" "$SNAP" "$T" >"$WORK/restore-main.log" 2>&1 ||
    { cat "$WORK/restore-main.log"; bad "the main restore failed"; }
mount -o subvolid=5,noatime "$DST_LOOP" "$DT"
base=@/.snapshots/1/snapshot
for n in 2 3; do
    d=$DT/@/.snapshots/$n/snapshot
    [ -d "$d" ] && [ -z "$(ls -A "$d")" ] || bad "the main restore didn't leave $n/snapshot empty"
    touch -r "${d%/*}" "$WORK/times.ref"
    rmdir "$d"
    btrfs -q subvolume snapshot "$DT/$base" "$d"
    RUSTIC_STRICT_EXIT=true "$R" -r "$REPO" restore --numeric-id --exact --clone-from "/=$DT/@/.snapshots/1/snapshot" \
        "$SNAP:/.snapshots/$n/snapshot" "$d" >"$WORK/restore-$n.log" 2>&1 || { cat "$WORK/restore-$n.log"; bad "snapshot $n's restore failed"; }
    touch -r "$WORK/times.ref" "${d%/*}"
    base=@/.snapshots/$n/snapshot
done

# ---- checks ----
grep -l AlreadyExists "$WORK"/restore-*.log && bad "AlreadyExists in a restore's output"
manifest() { python3 -I "$here/exact-manifest.py" "$1"; }
# The live root's .snapshots, the mount point under /.snapshots: the backup
# holds it only through the alias, which the restore leaves out, so its
# times are its mkdir's (as when a backup leaves aliases out, compare.py).
for n in 1 2 3; do
    manifest "$ST/@/.snapshots/$n/snapshot" | { [ $n = 1 ] && grep -v '^\.snapshots ' || cat; } >"$WORK/src-$n.m"
    manifest "$DT/@/.snapshots/$n/snapshot" | { [ $n = 1 ] && grep -v '^\.snapshots ' || cat; } >"$WORK/dst-$n.m"
    diff "$WORK/src-$n.m" "$WORK/dst-$n.m" >"$WORK/diff-$n" || { head -20 "$WORK/diff-$n"; bad "tree $n differs"; }
done
 manifest_top() { (cd "$1" && for n in 2 3; do stat -c "%n %Y %X %a" "$n" "$n/info.xml"; done); }
[ "$(manifest_top "$ST/@/.snapshots")" = "$(manifest_top "$DT/@/.snapshots")" ] || bad "snapper's N/ directories' times differ"
grep -q "AAAA" "$DT/@/.snapshots/2/snapshot/etc/trick" || bad "same size and mtime, other contents: not fixed"
# Cloned: pkg-v2.bin in 3 is the live tree's extent.
phys() { filefrag -v "$1" | awk '$1 == "0:" { print $4; exit }'; }
[ "$(phys "$DT/@/.snapshots/3/snapshot/usr/lib/pkg-v2.bin")" = "$(phys "$DT/@/.snapshots/1/snapshot/usr/lib/pkg-v2.bin")" ] ||
    bad "pkg-v2.bin in snapshot 3 isn't the live tree's (not cloned)"
# Again over itself: nothing changes.
find "$DT/@/.snapshots/3/snapshot" -printf '%C@ %i %p\n' | sort >"$WORK/ctime-before"
RUSTIC_STRICT_EXIT=true "$R" -r "$REPO" restore --numeric-id --exact "$SNAP:/.snapshots/3/snapshot" "$DT/@/.snapshots/3/snapshot" >"$WORK/restore-again.log" 2>&1 ||
    { cat "$WORK/restore-again.log"; bad "the restore again failed"; }
find "$DT/@/.snapshots/3/snapshot" -printf '%C@ %i %p\n' | sort >"$WORK/ctime-after"
diff "$WORK/ctime-before" "$WORK/ctime-after" >"$WORK/ctime-diff" || { head "$WORK/ctime-diff"; bad "an --exact restore over a matching tree changed $(grep -c '^>' "$WORK/ctime-diff") entries"; }
# Sharing, by qgroups, against the source's.
for n in 2 3; do btrfs property set -ts "$DT/@/.snapshots/$n/snapshot" ro true; done
excl() {
    # enable starts a rescan of its own: wait for it, then for a whole one.
    btrfs quota enable "$1" && btrfs quota rescan -W "$1" && btrfs quota rescan -w "$1" >/dev/null
    for n in 2 3; do
        id=$(btrfs inspect-internal rootid "$1/@/.snapshots/$n/snapshot")
        btrfs qgroup show --raw "$1" | awk -v q="0/$id" '$1 == q { print $3 }'
    done
}
read -r -d '' s2 s3 < <(excl "$ST"; printf '\0') || true
read -r -d '' d2 d3 < <(excl "$DT"; printf '\0') || true
echo "exclusive bytes: source 2=$s2 3=$s3, restored 2=$d2 3=$d3"
for p in "2 $s2 $d2" "3 $s3 $d3"; do
    set -- $p
    [ "$3" -le $(($2 + (SLACK_MIB << 20))) ] || bad "snapshot $1 has $3 bytes exclusive here, $2 on the source"
done
echo "sizes: $(grep -h 'cloning\|total restore size\|using' "$WORK"/restore-[23].log | tr '\n' ' ')"

# ---- the CachyOS/Fedora layout: the snapshot inside the subvolume cloned from ----
C_LOOP=$(mkfs "$C_IMG")
mkdir -p "$CT"
mount -o noatime "$C_LOOP" "$CT"
btrfs -q subvolume create "$CT/@"
C=$CT/@
mkdir -p "$C/etc" "$C/usr/lib" "$C/.snapshots/1"
# Snapshot 1's state
echo "root:x:0:0::/root:/bin/sh # snapshot 1" >"$C/etc/passwd-"
rnd "$C/usr/lib/tool" $((4 << 20))
rnd "$C/usr/lib/moved.bin" $((1 << 20))
echo same >"$C/etc/same"
touch -d '2026-11-13 16:05:04' "$C/etc/passwd-" "$C/usr/lib/tool" "$C/usr/lib/moved.bin"
echo "<snapshot/>" >"$C/.snapshots/1/info.xml"
btrfs -q subvolume snapshot -r "$C" "$C/.snapshots/1/snapshot"
# The live tree since: the same sizes, other contents and mtimes; moved.bin
# renamed, and a new one of its size in its place.
echo "root:x:0:0::/root:/bin/sh # the live @" >"$C/etc/passwd-"
rnd "$C/usr/lib/tool" $((4 << 20))
mv "$C/usr/lib/moved.bin" "$C/usr/lib/renamed.bin"
rnd "$C/usr/lib/moved.bin" $((1 << 20))
touch -d '2026-11-13 16:16:16' "$C/etc/passwd-" "$C/usr/lib/tool" "$C/usr/lib/moved.bin"
sync
"$R" -r "$C_REPO" init >/dev/null 2>&1
"$R" -r "$C_REPO" backup --set-atime yes --as-path / "$C" >"$WORK/cbackup.log" 2>&1 || { cat "$WORK/cbackup.log"; exit 1; }
CD_LOOP=$(mkfs "$CD_IMG")
mkdir -p "$CDT"
mount -o noatime "$CD_LOOP" "$CDT"
btrfs -q subvolume create "$CDT/@"
echo '!/.snapshots/1/snapshot/**' >"$WORK/cglobs"
RUSTIC_STRICT_EXIT=true "$R" -r "$C_REPO" restore --numeric-id --glob-file "$WORK/cglobs" latest "$CDT/@" >"$WORK/crestore-main.log" 2>&1 ||
    { cat "$WORK/crestore-main.log"; bad "CachyOS layout: the main restore failed"; }
d=$CDT/@/.snapshots/1/snapshot
[ -d "$d" ] && [ -z "$(ls -A "$d")" ] || bad "CachyOS layout: the main restore didn't leave 1/snapshot empty"
touch -r "${d%/*}" "$WORK/times.ref"
rmdir "$d"
btrfs -q subvolume snapshot "$CDT/@" "$d"
RUSTIC_STRICT_EXIT=true "$R" -r "$C_REPO" restore --numeric-id --exact --clone-from "/=$CDT/@" \
    "latest:/.snapshots/1/snapshot" "$d" >"$WORK/crestore-1.log" 2>&1 || { cat "$WORK/crestore-1.log"; bad "CachyOS layout: snapshot 1's restore failed"; }
touch -r "$WORK/times.ref" "${d%/*}"
btrfs property set -ts "$d" ro true
manifest "$C" >"$WORK/csrc.m"
manifest "$CDT/@" >"$WORK/cdst.m"
diff "$WORK/csrc.m" "$WORK/cdst.m" >"$WORK/cdiff" || { head -20 "$WORK/cdiff"; bad "CachyOS layout: @ or its snapshot differs"; }
for f in etc/passwd- usr/lib/tool; do
    cmp -s "$C/.snapshots/1/snapshot/$f" "$d/$f" || bad "CachyOS layout: snapshot 1's $f is $(stat -c %s "$d/$f") bytes of other contents (cloned from itself?)"
done
[ "$(phys "$d/usr/lib/moved.bin")" = "$(phys "$CDT/@/usr/lib/renamed.bin")" ] ||
    bad "CachyOS layout: moved.bin in snapshot 1 isn't @'s renamed.bin (not cloned)"
echo "CachyOS layout: $(grep -h "files' contents to clone" "$WORK/crestore-1.log")"
if [ $fail = 0 ]; then
    echo "PASS"
    trap - EXIT
    cleanup
    rm -rf "$WORK"
    exit 0
else
    echo "FAIL (state kept in $WORK)"
    exit 1
fi
