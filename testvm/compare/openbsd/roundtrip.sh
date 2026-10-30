#!/bin/sh
# Round trip of OpenBSD's file metadata through restic, or BackOtter's
# rustic: make a tree of hard-to-preserve files, back it up, restore it as
# "latest:SRC" into another directory, and compare the two with mtree(8).  OpenBSD has no ACLs or
# extended attributes; what it has is file flags.
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

mkdir -p $SRC && cd $SRC || exit 1
touch no-such-user; chown 1234:5678 no-such-user
touch setuid; chown 1234:5678 setuid; chmod 4755 setuid
touch setgid; chmod 2755 setgid
mkdir sticky-dir; chmod 1777 sticky-dir
echo linked >hardlink-a; ln hardlink-a hardlink-b; mkdir sub; ln hardlink-a sub/hardlink-c
ln -s hardlink-a symlink; ln -s /nonexistent/target symlink-dangling
mkfifo fifo
dd if=/dev/zero of=sparse bs=1m count=0 seek=1024 2>/dev/null
echo start | dd of=sparse conv=notrunc 2>/dev/null
echo middle | dd of=sparse bs=1m seek=512 conv=notrunc 2>/dev/null
touch "name with spaces" "$(printf '\377\376')-not-utf8"
touch -d 2001-02-03T04:05:06.123456789 nanoseconds
touch -d 2100-01-01T12:00:00 future
# File flags last: most of them stop any change afterwards.
for f in uchg uappnd nodump arch schg sappnd; do echo $f >flag-$f; try chflags $f flag-$f; done
mkdir uchg-dir; echo inside >uchg-dir/inside; chflags uchg uchg-dir
try chflags schg hardlink-a
try chflags -h uchg symlink
touch -d 2001-02-03T04:05:06 $SRC

mtree -c -p $SRC -k type,uid,gid,mode,flags,nlink,size,time,link,sha256digest >/tmp/bko-rt.spec
bko_init && bko_backup $SRC || { echo "BACKUP FAILED"; exit 1; }
echo "== restore"
bko_restore "latest:$SRC" $DST; echo "exit $?"
echo "== mtree: type, owner, group, mode, flags, links, size, mtime, symlink target, SHA-256"
mtree -f /tmp/bko-rt.spec -p $DST && echo identical
echo "== hard links (one inode for all three)"
ls -i $DST/hardlink-a $DST/hardlink-b $DST/sub/hardlink-c | awk '{print $1}' | sort -u | wc -l | sed 's/^ */inodes: /'
echo "== sparse (KB allocated, source then restored)"
du -k $SRC/sparse $DST/sparse
