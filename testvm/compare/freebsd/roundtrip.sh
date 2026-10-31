#!/bin/sh
# Round trip of FreeBSD's file metadata through restic, or BackOtter's
# rustic, within one running system: torture.sh's files in /root/bko-rt,
# backed up, restored as "latest:SRC" into another directory on the same
# file system (the root's: ZFS on freebsd15, UFS on freebsd15-ufs), and
# compared with fsmeta.sh.  rescue.sh does the same for a whole disk.
#
# Usage: roundtrip.sh BACKUP [RESTORE]   (as root, in a throwaway guest: at
#                                         securelevel 1, schg and sappnd
#                                         can't be cleared again)
# BACKUP and RESTORE are restic or BackOtter's rustic; RESTORE defaults to
# BACKUP.  torture.sh and fsmeta.sh are next to this script.

R=$1 RR=${2:-$1}
here=$(cd "$(dirname "$0")" && pwd)
SRC=/root/bko-rt DST=/root/bko-rt-restored OUT=/tmp/bko-rt
export RESTIC_PASSWORD=backotter-compare RESTIC_REPOSITORY=/root/bko-rt-repo
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
bko_backup $SRC; status=$?
echo "exit $status"
[ $status -eq 0 ] || failed="$failed; backup"
echo "== restore"
bko_restore "latest:$SRC" $DST; status=$?
echo "exit $status"
[ $status -eq 0 ] || failed="$failed; restore"
sh "$here/fsmeta.sh" $DST $OUT/after
t="mtree: type, owner, group, mode, flags, links, size, mtime, symlink target, SHA-256"
echo "== $t"
(cd $DST && mtree -f $OUT/before/spec -X $OUT/before/mtree-exclude) && echo identical || failed="$failed; $t"
# dos-noarchive (torture.sh) on its own: on ZFS, uarch cleared and nothing
# else set.
for d in before after; do
    grep -v '/dos-noarchive ' $OUT/$d/flags.txt >$OUT/$d/flags-but-noarchive.txt
    grep '/dos-noarchive ' $OUT/$d/flags.txt >$OUT/$d/noarchive.txt
done
# What the flags compared are (not a check): which ones the file system took.
echo "== torture.sh's flag-* and dos-* files' flags before, stat %Xf (not a check)"
grep -E '/(flag|dos)-[^/]* ' $OUT/before/flags.txt | sed 's|^\./root/bko-torture/|    |'
# On ZFS, compression rather than holes decides what a file allocates.
checks="flags-but-noarchive noarchive times acls extattrs hardlinks births"
[ $kind = ufs ] && checks="$checks sparse"
for f in $checks; do
    case $f in
        births) t="births, min(birth, mtime) (unix.birthtime)" ;;
        flags-but-noarchive) t="file flags, stat %Xf (freebsd.file_flags)" ;;
        noarchive) t="uarch cleared and nothing else (dos-noarchive, SPEC2 3)" ;;
        times) t="atime and mtime to the nanosecond" ;;
        *) t=$f ;;
    esac
    echo "== $t"
    if diff $OUT/before/$f.txt $OUT/after/$f.txt; then
        echo "identical ($(wc -l <$OUT/before/$f.txt | tr -d ' ') lines)"
    else
        failed="$failed; $t"
    fi
done
echo "roundtrip: $([ -z "$failed" ] && echo PASS || echo "FAIL: ${failed#; }")"
