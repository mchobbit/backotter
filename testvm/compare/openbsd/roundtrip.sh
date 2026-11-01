#!/bin/sh
# Round trip of OpenBSD's file metadata through restic, or BackOtter's
# rustic: make a tree of hard-to-preserve files, back it up, restore it as
# "latest:SRC" into another directory, and compare the two with mtree(8)
# (OpenBSD's compares mtimes to the nanosecond), with the modes (of symlinks
# apart) and the atimes on their own.  OpenBSD has no ACLs or extended
# attributes; what it has is file flags.
#
# Then, as on Linux (fs_roundtrip.py): a restore over the restored tree
# after changing it (rt-common.sh's rt_over), compared again; and when
# BACKUP and RESTORE are the two tools, the snapshot each of them makes of
# the tree, node by node (rt_snapshots).
#
# The tree has a Unix socket too, made after the listings, which neither
# tool keeps (restic never has; rustic since rustic_core 1a2a4a4): no node
# of it in either tool's snapshot, none restored, and no message about it
# from any backup or restore.
#
# Usage: roundtrip.sh BACKUP [RESTORE]   (as root, in a throwaway guest: at
#                                         securelevel 1, schg and sappnd
#                                         can't be cleared again)
# BACKUP and RESTORE are restic or BackOtter's rustic; RESTORE defaults to
# BACKUP.  rt-common.sh is next to this script.

R=$1 RR=${2:-$1}
here=$(cd "$(dirname "$0")" && pwd)
. "$here/rt-common.sh"
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
# Every backup's and restore's stderr, shown and kept in /tmp/bko-rt.msgs
# (for the socket check).
: >/tmp/bko-rt.msgs
keep_msgs() {  # COMMAND...
    "$@" 2>/tmp/bko-rt.run; ks=$?
    cat /tmp/bko-rt.run >&2; cat /tmp/bko-rt.run >>/tmp/bko-rt.msgs
    return $ks
}
bko_backup() {
    if is_rustic $R; then keep_msgs $R --no-progress --no-cache backup --set-atime yes "$@" >/dev/null
    else keep_msgs $R backup -q "$@"; fi
}
bko_restore() {
    if is_rustic $RR; then keep_msgs $RR --no-progress --no-cache restore --numeric-id --sparse by-content "$1" "$2"
    else keep_msgs $RR restore -q --sparse "$1" --target "$2"; fi
}
bko_exclude() { if is_rustic $R; then echo "--glob !$(basename "$1")"; else echo "--exclude $1"; fi; }

# Reading a file or a directory must not move its atime, here or in the
# backup: atimes are compared to the nanosecond.
mount -u -o noatime / || { echo "can't remount / noatime"; exit 1; }
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
# A Unix socket, left bound: bko-socket PATH.
cat >/tmp/bko-socket.c <<'C'
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {  /* PATH */
    struct sockaddr_un addr;
    int s;

    if (argc != 2)
        return 2;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strlcpy(addr.sun_path, argv[1], sizeof(addr.sun_path));
    if ((s = socket(AF_UNIX, SOCK_STREAM, 0)) < 0 ||
        bind(s, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        perror(argv[1]);
        return 1;
    }
    return 0;
}
C
cc -o /tmp/bko-socket /tmp/bko-socket.c || { echo "can't build bko-socket"; exit 1; }
# The tree, in DIR, with the system flags SF (schg sappnd) or none.
make_tree() {  # DIR [SF]
    mkdir -p "$1" && cd "$1" || exit 1
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
    # 2003-03-03T03:03:03.333333333 and 2004-04-04T04:04:04.444444444 UTC
    /tmp/bko-lutimens atime-ns-symlink 1046660583 333333333 1081051444 444444444 || exit 1
    # File flags last: most of them stop any change afterwards.
    for f in uchg uappnd nodump arch $2; do echo $f >flag-$f; try chflags $f flag-$f; done
    mkdir uchg-dir; echo inside >uchg-dir/inside; chflags uchg uchg-dir
    [ -n "$2" ] && try chflags schg hardlink-a
    try chflags -h uchg symlink
    touch -d 2001-02-03T04:05:06.000000006 "$1"
}
make_tree $SRC "schg sappnd"
cd /root
# At securelevel 1, schg and sappnd can't be cleared: the restore over an
# existing tree (below) works on a tree without them.
securelevel=$(sysctl -n kern.securelevel)
[ "$securelevel" -gt 0 ] && make_tree /root/bko-ov

listings() {  # DIR OUT-PREFIX: the listings compared, one file each
    cd "$1" || exit 1
    find . -exec stat -f '%N %Fa %Fm' {} + | sort >$2.times
    find . ! -type l -exec stat -f '%N %Mp%Lp' {} + | sort >$2.modes
    find . -type l -exec stat -f '%N %Lp' {} + | sort >$2.symlink-modes
}
failed=""
before=/tmp/bko-rt.before after=/tmp/bko-rt.after spec=/tmp/bko-rt.spec
check() {  # TITLE KIND: compare $before.KIND with $after.KIND
    echo "== $1"
    if diff $before.$2 $after.$2; then
        echo "identical ($(wc -l <$before.$2 | tr -d ' ') lines)"
    else
        failed="$failed; $1"
    fi
}

# mtree compares symlink modes too: those are a check of their own.
mtree -c -p $SRC -k type,uid,gid,flags,nlink,size,time,link,sha256digest >/tmp/bko-rt.spec
listings $SRC /tmp/bko-rt.before
# The socket, after the spec and the listings (no restore may have it), the
# tree's times set again after.
/tmp/bko-socket $SRC/bko-socket && [ -S $SRC/bko-socket ] || { echo "can't make $SRC/bko-socket"; exit 1; }
touch -d 2001-02-03T04:05:06.000000006 $SRC
ls -l $SRC/bko-socket
bko_init || { echo "INIT FAILED"; exit 1; }
# A backup that fails may still have saved a snapshot (restic's exit 3: a
# file it couldn't read): what it holds is restored all the same.
echo "== backup"
bko_backup $SRC; status=$?
echo "exit $status"
[ $status -eq 0 ] || failed="$failed; backup"
rm -f /tmp/bko-rt.nodes.2
rt_snapshots $R $RR $RESTIC_REPOSITORY $SRC
# The socket: in neither tool's snapshot (rt_snapshots' second is the
# other tool's, when there are two).
t="socket: no node in the snapshot"
echo "== $t"
if rt_nodes $(rt_restic $R $RR) $RESTIC_REPOSITORY $SRC >/tmp/bko-rt.nodes.0; then
    if cat /tmp/bko-rt.nodes.0 /tmp/bko-rt.nodes.2 2>/dev/null | grep socket; then
        failed="$failed; $t"
    else
        echo "none ($(wc -l </tmp/bko-rt.nodes.0 | tr -d ' ') nodes$([ -f /tmp/bko-rt.nodes.2 ] && echo ", and none in the other tool's snapshot"))"
    fi
else
    echo "can't decode the snapshot"; failed="$failed; $t"
fi
echo "== restore"
bko_restore "latest:$SRC" $DST; status=$?
echo "exit $status"
[ $status -eq 0 ] || failed="$failed; restore"
# The restored tree against the source, its listings into $after.*; X goes
# after each check's title.
compare_all() {  # X
    x=$1
    listings $DST $after
    t="mtree: type, owner, group, flags, links, size, mtime, symlink target, SHA-256$x"
    echo "== $t"
    mtree -f $spec -p $DST && echo identical || failed="$failed; $t"
    check "modes (not symlinks)$x" modes
    check "symlink modes$x" symlink-modes
    check "atime and mtime to the nanosecond$x" times
    t="hard links (one inode for all three)$x"
    echo "== $t"
    n=$(ls -i $DST/hardlink-a $DST/hardlink-b $DST/sub/hardlink-c | awk '{print $1}' | sort -u | wc -l | tr -d ' ')
    echo "inodes: $n"; [ "$n" = 1 ] || failed="$failed; $t"
    t="socket not restored$x"
    echo "== $t"
    n=$(find $DST -type s | wc -l | tr -d ' ')
    echo "sockets: $n"; find $DST -type s
    [ "$n" = 0 ] && [ ! -e $DST/bko-socket ] || failed="$failed; $t"
}
compare_all ""
echo "== sparse (KB allocated, source then restored)"
du -k $SRC/sparse $DST/sparse
# Again over the restored tree, changed: the file flags off (or nothing
# could change), a directory replaced by a symlink to one elsewhere, files
# with other bytes, mode and mtime.  At securelevel 1 that is the tree
# without schg and sappnd, backed up into a repository of its own and
# restored first.
if [ "$securelevel" -gt 0 ]; then
    echo "== securelevel $securelevel: the restore over an existing tree is of /root/bko-ov (no schg, sappnd)"
    mtree -c -p /root/bko-ov -k type,uid,gid,flags,nlink,size,time,link,sha256digest >/tmp/bko-ov.spec
    listings /root/bko-ov /tmp/bko-ov.before
    rt_backup $R /root/bko-ov-repo /root/bko-ov || failed="$failed; backup of /root/bko-ov"
    SRC=/root/bko-ov DST=/root/bko-ov-restored before=/tmp/bko-ov.before spec=/tmp/bko-ov.spec
    export RESTIC_REPOSITORY=/root/bko-ov-repo RUSTIC_REPOSITORY=/root/bko-ov-repo
    bko_restore "latest:$SRC" $DST || failed="$failed; restore of /root/bko-ov"
fi
echo "== restore over an existing, changed tree"
chflags -R 0 $DST && find $DST -type l -exec chflags -h 0 {} +
rt_over $DST sub atime-ns nanoseconds || failed="$failed; preparing the restore over an existing tree"
bko_restore "latest:$SRC" $DST; status=$?
echo "exit $status"
[ $status -eq 0 ] || failed="$failed; restore over an existing tree"
t="what the replaced directory's symlink pointed to, untouched"
echo "== $t"
rt_over_check && echo "untouched ($(wc -l </tmp/bko-rt.elsewhere.before | tr -d ' ') names)" || failed="$failed; $t"
after=/tmp/bko-rt.over
compare_all " (over an existing tree)"
t="no message about the socket from either tool (backup, restores)"
echo "== $t"
n=$(grep -ci socket /tmp/bko-rt.msgs)
echo "$n lines ($(wc -l </tmp/bko-rt.msgs | tr -d ' ') lines on stderr in all)"; grep -i socket /tmp/bko-rt.msgs | head -n 5
[ "$n" = 0 ] || failed="$failed; $t"
echo "roundtrip: $([ -z "$failed" ] && echo PASS || echo "FAIL: ${failed#; }")"
