#!/bin/sh
# Round trip of FreeBSD's file metadata through restic, or BackOtter's
# rustic, within one running system: torture.sh's files in /root/bko-rt,
# backed up, restored as "latest:SRC" into another directory on the same
# file system (the root's: ZFS on freebsd15, UFS on freebsd15-ufs), and
# compared with fsmeta.sh.  torture.sh's files go also onto a UFS md(4)
# disk mounted with nfsv4acls inside the tree (NFSv4 ACLs on UFS; the
# restore target gets one too).  Then, as on Linux (fs_roundtrip.py): a
# restore over the restored tree after changing it (rt-common.sh's
# rt_over), compared again; and when BACKUP and RESTORE are the two tools,
# the snapshot each of them makes of the tree, node by node
# (rt_snapshots).  Unix sockets (torture.sh makes one in each tree) must be
# in no snapshot and come back from no restore, with no message about them:
# neither tool backs one up (restic never did, rustic since rustic_core
# 1a2a4a4).  rescue.sh does the same for a whole disk.
#
# Usage: roundtrip.sh BACKUP [RESTORE]   (as root, in a throwaway guest: at
#                                         securelevel 1, schg and sappnd
#                                         can't be cleared again)
# BACKUP and RESTORE are restic or BackOtter's rustic; RESTORE defaults to
# BACKUP.  torture.sh, fsmeta.sh and rt-common.sh are next to this script
# (run-guest-rt.sh puts them in one directory).

R=$1 RR=${2:-$1}
here=$(cd "$(dirname "$0")" && pwd)
SRC=/root/bko-rt DST=/root/bko-rt-restored OUT=/tmp/bko-rt
export RESTIC_PASSWORD=backotter-compare RESTIC_REPOSITORY=/root/bko-rt-repo
. "$here/rt-common.sh"
# Back up with $R, restore with $RR.  BackOtter's rustic (a binary named
# *rustic*) takes the same repository as restic, with its own options: the
# real atime, as restic keeps it, and numeric ids, as restic restores them.
export RUSTIC_PASSWORD=$RESTIC_PASSWORD RUSTIC_REPOSITORY=$RESTIC_REPOSITORY
is_rustic() { case $(basename "$1") in *rustic*) true ;; *) false ;; esac; }
bko_init() {
    if is_rustic $R; then $R --no-progress --no-cache init >/dev/null; else $R init -q; fi
}
bko_backup() {
    if is_rustic $R; then $R --no-progress --no-cache backup --set-atime yes "$@" >/dev/null
    else $R backup -q "$@"; fi
}
bko_restore() {
    if is_rustic $RR; then $RR --no-progress --no-cache restore --numeric-id --sparse by-content "$1" "$2"
    else $RR restore -q --sparse "$1" --target "$2"; fi
}

mkdir -p $SRC
kind=$(df -T $SRC | awk 'NR == 2 { print $2 }')
# UFS keeps POSIX.1e ACLs only when mounted with acls, which freebsd15-ufs's
# root isn't: on, before torture.sh sets them.
[ $kind = ufs ] && mount -u -o acls "$(df $SRC | awk 'NR == 2 { print $6 }')"
sh "$here/torture.sh" $SRC
# UFS with NFSv4 ACLs: a swap-backed md(4) disk, mounted with nfsv4acls
# (and noatime, as the root is below), with torture.sh's files too.
md_mount() {  # DIR
    md=$(mdconfig -a -t swap -s 2g) && newfs -U /dev/$md >/dev/null && mkdir -p "$1" &&
        mount -o nfsv4acls,noatime /dev/$md "$1" ||
        { echo "can't mount a UFS md disk with nfsv4acls at $1"; exit 1; }
}
md_mount $SRC/ufs-nfs4
sh "$here/torture.sh" $SRC/ufs-nfs4
# Reading a file or directory must not move its atime, here or in the
# backup: atimes are compared to the nanosecond.  freebsd15's ZFS has atime
# off already; UFS has it on.
if [ $kind = zfs ]; then
    zfs set atime=off "$(df $SRC | awk 'NR == 2 { print $1 }')"
else
    mount -u -o noatime "$(df $SRC | awk 'NR == 2 { print $6 }')"
fi
sh "$here/fsmeta.sh" $SRC $OUT/before
failed=""
bko_init || { echo "INIT FAILED"; exit 1; }
# A backup that fails may still have saved a snapshot (restic's exit 3: a
# file it couldn't read): what it holds is restored all the same.
echo "== backup"
bko_backup $SRC 2>$OUT/backup.msgs; status=$?
cat $OUT/backup.msgs
echo "exit $status"
[ $status -eq 0 ] || failed="$failed; backup"
t="sockets in the source, for the checks below (fsmeta.sh's sockets.txt)"
echo "== $t"
sed 's|^\./||' $OUT/before/sockets.txt
[ -s $OUT/before/sockets.txt ] || { echo "none: torture.sh couldn't make one"; failed="$failed; $t"; }
t="sockets: no node of one in the snapshot, no message about one at backup"
echo "== $t"
if rt_nodes $(rt_restic $R $RR) $RESTIC_REPOSITORY $SRC >$OUT/nodes.txt; then
    { grep 'type="socket"' $OUT/nodes.txt; awk -F '\t' '$1 ~ /(^|\/)socket$/' $OUT/nodes.txt
      grep -i socket $OUT/backup.msgs; } | sort -u >$OUT/sockets-backup.txt
    if [ -s $OUT/sockets-backup.txt ]; then cat $OUT/sockets-backup.txt; failed="$failed; $t"
    else echo "none ($(wc -l <$OUT/nodes.txt | tr -d ' ') nodes)"; fi
else
    echo "can't decode the snapshot"; failed="$failed; $t"
fi
rt_snapshots $R $RR $RESTIC_REPOSITORY $SRC
mkdir -p $DST
md_mount $DST/ufs-nfs4
echo "== restore"
bko_restore "latest:$SRC" $DST >$OUT/after.msgs 2>&1; status=$?
cat $OUT/after.msgs
echo "exit $status"
[ $status -eq 0 ] || failed="$failed; restore"
# What the flags compared are (not a check): which ones the file system took.
echo "== torture.sh's flag-* and dos-* files' flags before, stat %Xf (not a check)"
grep -E '/(flag|dos)-[^/]* ' $OUT/before/flags.txt | sed 's|^\./||'
# The restored tree against the source: fsmeta.sh's listings of it into
# $OUT/AFTER, each compared with the source's.  SUFFIX goes after each
# check's title.
compare_all() {  # AFTER SUFFIX
    a=$1 x=$2
    sh "$here/fsmeta.sh" $DST $OUT/$a
    t="mtree: type, owner, group, mode, flags, links, size, mtime, symlink target, SHA-256$x"
    echo "== $t"
    (cd $DST && mtree -f $OUT/before/spec -X $OUT/before/mtree-exclude) && echo identical || failed="$failed; $t"
    # dos-noarchive (torture.sh) on its own: on ZFS, uarch cleared and
    # nothing else set.  On ZFS, compression rather than holes decides what
    # a file allocates: the md disk's files alone are compared there.
    for d in before $a; do
        grep -v '/dos-noarchive ' $OUT/$d/flags.txt >$OUT/$d/flags-but-noarchive.txt
        grep '/dos-noarchive ' $OUT/$d/flags.txt >$OUT/$d/noarchive.txt
        grep '^\./ufs-nfs4/' $OUT/$d/sparse.txt >$OUT/$d/sparse-md.txt
    done
    t="no socket restored, and no message about one$x"
    echo "== $t"
    if [ -s $OUT/$a/sockets.txt ] || grep -i socket $OUT/$a.msgs; then
        sed 's|^\./||' $OUT/$a/sockets.txt; failed="$failed; $t"
    else
        echo "none"
    fi
    checks="flags-but-noarchive noarchive times acls extattrs hardlinks births"
    if [ $kind = ufs ]; then checks="$checks sparse"; else checks="$checks sparse-md"; fi
    for f in $checks; do
        case $f in
            births) t="births, min(birth, mtime) (unix.birthtime)" ;;
            flags-but-noarchive) t="file flags, stat %Xf (freebsd.file_flags)" ;;
            noarchive) t="uarch cleared and nothing else (dos-noarchive, SPEC2 3)" ;;
            times) t="atime and mtime to the nanosecond" ;;
            acls) t="ACLs (POSIX.1e or NFSv4 on the root; NFSv4 on UFS in ufs-nfs4/)" ;;
            sparse-md) t="sparse (the UFS md disk's files)" ;;
            *) t=$f ;;
        esac
        t="$t$x"
        echo "== $t"
        if diff $OUT/before/$f.txt $OUT/$a/$f.txt; then
            echo "identical ($(wc -l <$OUT/before/$f.txt | tr -d ' ') lines)"
        else
            failed="$failed; $t"
        fi
    done
}
compare_all after ""
# Again over the restored tree, changed: the file flags off (or nothing
# could change), a directory replaced by a symlink to one elsewhere, files
# with other bytes, mode and mtime.
echo "== restore over an existing, changed tree"
chflags -R 0 $DST && find $DST -type l -exec chflags -h 0 {} +
T=root/bko-torture
rt_over $DST $T/sub $T/atime-ns $T/xattrs ufs-nfs4/$T/acl-file || failed="$failed; preparing the restore over an existing tree"
bko_restore "latest:$SRC" $DST >$OUT/over.msgs 2>&1; status=$?
cat $OUT/over.msgs
echo "exit $status"
[ $status -eq 0 ] || failed="$failed; restore over an existing tree"
t="what the replaced directory's symlink pointed to, untouched"
echo "== $t"
rt_over_check && echo "untouched ($(wc -l </tmp/bko-rt.elsewhere.before | tr -d ' ') names)" || failed="$failed; $t"
compare_all over " (over an existing tree)"
echo "roundtrip: $([ -z "$failed" ] && echo PASS || echo "FAIL: ${failed#; }")"
