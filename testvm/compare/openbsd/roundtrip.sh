#!/bin/sh
# Round trip of OpenBSD's file metadata through restic, or BackOtter's
# rustic: make a tree of hard-to-preserve files, back it up, restore it as
# "latest:SRC" into another directory, and compare the two with mtree(8)
# (OpenBSD's compares mtimes to the nanosecond), with the modes (of symlinks
# apart) and the atimes on their own.  OpenBSD has no ACLs or extended
# attributes; what it has is file flags.
#
# Usage: roundtrip.sh BACKUP [RESTORE]   (as root, in a throwaway guest: at
#                                         securelevel 1, schg and sappnd
#                                         can't be cleared again)
# BACKUP and RESTORE are restic or BackOtter's rustic; RESTORE defaults to
# BACKUP.

R=$1 RR=${2:-$1}
SRC=/root/bko-rt DST=/root/bko-rt-restored
export RESTIC_PASSWORD=backotter-compare RESTIC_REPOSITORY=/root/bko-rt-repo
try() { "$@" 2>/dev/null || echo "roundtrip: skipped (unsupported here): $*" >&2; }
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
bko_exclude() { if is_rustic $R; then echo "--glob !$(basename "$1")"; else echo "--exclude $1"; fi; }

# Reading a file or a directory must not move its atime, here or in the
# backup: atimes are compared to the nanosecond.
mount -u -o noatime / || { echo "can't remount / noatime"; exit 1; }
mkdir -p $SRC && cd $SRC || exit 1
touch no-such-user; chown 1234:5678 no-such-user
touch setuid; chown 1234:5678 setuid; chmod 4755 setuid
touch setgid; chmod 2755 setgid
mkdir sticky-dir; chmod 1777 sticky-dir
echo linked >hardlink-a; ln hardlink-a hardlink-b; mkdir sub; ln hardlink-a sub/hardlink-c
ln -s hardlink-a symlink; ln -s /nonexistent/target symlink-dangling
# Symlink modes: OpenBSD has no lchmod(2), but chmod -h (fchmodat(2) with
# AT_SYMLINK_NOFOLLOW) sets them.
ln -s hardlink-a symlink-0700; chmod -h 0700 symlink-0700
ln -s hardlink-a symlink-0777; chmod -h 0777 symlink-0777
mkfifo fifo
dd if=/dev/zero of=sparse bs=1m count=0 seek=1024 2>/dev/null
echo start | dd of=sparse conv=notrunc 2>/dev/null
echo middle | dd of=sparse bs=1m seek=512 conv=notrunc 2>/dev/null
touch "name with spaces" "$(printf '\377\376')-not-utf8"
touch -d 2001-02-03T04:05:06.123456789 nanoseconds
touch -d 2100-01-01T12:00:00 future
# An atime apart from the mtime, to the nanosecond: a file, a directory and
# a symlink.
echo atime >atime-ns; mkdir atime-ns-dir; ln -s atime-ns atime-ns-symlink
touch -d 2004-04-04T04:04:04.444444444 atime-ns atime-ns-dir
touch -a -d 2003-03-03T03:03:03.333333333 atime-ns atime-ns-dir
# OpenBSD's touch has no -h: a symlink's own times go through
# utimensat(2) with AT_SYMLINK_NOFOLLOW (cc is in the comp set).
cat >/tmp/bko-lutimens.c <<'C'
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>

int main(int argc, char **argv) {  /* PATH ASEC ANSEC MSEC MNSEC */
    struct timespec ts[2];

    if (argc != 6)
        return 2;
    ts[0].tv_sec = atoll(argv[2]); ts[0].tv_nsec = atol(argv[3]);
    ts[1].tv_sec = atoll(argv[4]); ts[1].tv_nsec = atol(argv[5]);
    if (utimensat(AT_FDCWD, argv[1], ts, AT_SYMLINK_NOFOLLOW) != 0) {
        perror(argv[1]);
        return 1;
    }
    return 0;
}
C
cc -o /tmp/bko-lutimens /tmp/bko-lutimens.c || { echo "can't build bko-lutimens"; exit 1; }
# 2003-03-03T03:03:03.333333333 and 2004-04-04T04:04:04.444444444 UTC
/tmp/bko-lutimens atime-ns-symlink 1046660583 333333333 1081051444 444444444 || exit 1
# File flags last: most of them stop any change afterwards.
for f in uchg uappnd nodump arch schg sappnd; do echo $f >flag-$f; try chflags $f flag-$f; done
mkdir uchg-dir; echo inside >uchg-dir/inside; chflags uchg uchg-dir
try chflags schg hardlink-a
try chflags -h uchg symlink
touch -d 2001-02-03T04:05:06.000000006 $SRC

listings() {  # DIR OUT-PREFIX: the listings compared, one file each
    cd "$1" || exit 1
    find . -exec stat -f '%N %Fa %Fm' {} + | sort >$2.times
    find . ! -type l -exec stat -f '%N %Mp%Lp' {} + | sort >$2.modes
    find . -type l -exec stat -f '%N %Lp' {} + | sort >$2.symlink-modes
}
failed=""
check() {  # TITLE KIND: compare before.KIND with after.KIND
    echo "== $1"
    if diff /tmp/bko-rt.before.$2 /tmp/bko-rt.after.$2; then
        echo "identical ($(wc -l </tmp/bko-rt.before.$2 | tr -d ' ') lines)"
    else
        failed="$failed; $1"
    fi
}

# mtree compares symlink modes too: those are a check of their own.
mtree -c -p $SRC -k type,uid,gid,flags,nlink,size,time,link,sha256digest >/tmp/bko-rt.spec
listings $SRC /tmp/bko-rt.before
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
listings $DST /tmp/bko-rt.after
t="mtree: type, owner, group, flags, links, size, mtime, symlink target, SHA-256"
echo "== $t"
mtree -f /tmp/bko-rt.spec -p $DST && echo identical || failed="$failed; $t"
check "modes (not symlinks)" modes
check "symlink modes" symlink-modes
check "atime and mtime to the nanosecond" times
t="hard links (one inode for all three)"
echo "== $t"
n=$(ls -i $DST/hardlink-a $DST/hardlink-b $DST/sub/hardlink-c | awk '{print $1}' | sort -u | wc -l | tr -d ' ')
echo "inodes: $n"; [ "$n" = 1 ] || failed="$failed; $t"
echo "== sparse (KB allocated, source then restored)"
du -k $SRC/sparse $DST/sparse
echo "roundtrip: $([ -z "$failed" ] && echo PASS || echo "FAIL: ${failed#; }")"
