#!/bin/sh
# Round trip of NetBSD's file metadata through restic, or BackOtter's rustic:
# make a tree of hard-to-preserve files, back it up, restore it as
# "latest:SRC" into another directory, and compare the two.  The tree spans
# three file systems: the root (FFSv2: file flags, no extended attributes)
# and two FFSv2ea images mounted inside it, one with POSIX.1e ACLs and one
# with NFSv4 ACLs (extended attributes in both namespaces); the restore
# target gets the same two mounts.  Compared: mtree (type, owner, links,
# size, mtime to the microsecond, symlink target, SHA-256), modes, file
# flags, symlink modes, atimes and mtimes to the nanosecond, birth times,
# ACLs, and extended attributes of each namespace, each on its own, so that
# what a tool doesn't keep shows up as just that.  Where the root has
# extended attributes (AlpineBSD's is FFSv2ea), files on it get them too,
# compared on their own.
#
# Then, as on Linux (fs_roundtrip.py): a restore over the restored tree
# after changing it (rt-common.sh's rt_over), compared again; when BACKUP
# and RESTORE are the two tools, the snapshot each of them makes of the
# tree, node by node (rt_snapshots); and with the vnd images of the restore
# target freed: a tree on FFSv1, which has no birth times (stat gives 0: no
# unix.birthtime may be stored), restored onto FFSv1; and the FFSv2ea
# part's snapshot restored onto FFSv1 and onto plain FFSv2 (no extended
# attributes): the restore must warn, naming each attribute it can't set.
#
# Usage: roundtrip.sh BACKUP [RESTORE]   (as root, in a throwaway guest:
#                                         kern.securelevel <= 0, or schg and
#                                         sappnd can't be cleared again)
# BACKUP and RESTORE are restic or BackOtter's rustic; RESTORE defaults to
# BACKUP.  Runs on AlpineBSD too (NetBSD's kernel and libc): with NetBSD's
# mtree, stat, chflags, setfacl, getfacl, *extattr, md5, ls and touch in
# /root/nbtools, which comes first in PATH.  rt-common.sh is next to this
# script.

PATH=/root/nbtools:/sbin:/usr/sbin:/bin:/usr/bin
R=$1 RR=${2:-$1}
here=$(cd "$(dirname "$0")" && pwd)
. "$here/rt-common.sh"
SRC=/root/bko-rt DST=/root/bko-rt-restored
export RESTIC_PASSWORD=backotter-compare RESTIC_REPOSITORY=/root/bko-rt-repo
export LC_ALL=C           # file names that aren't UTF-8 sort too
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

# A file system on a vnd(4) image, mounted at DIR: FFSv2ea (UFS2 extended
# attributes) with the ACL kind given, or another format of newfs -O.
# vnd0-3 are the four GENERIC has.
vnd=0
vnd_mount() {  # DIR FORMAT [OPTIONS]
    dd if=/dev/zero of=/root/bko-vnd$vnd.img bs=1048576 count=0 seek=64 2>/dev/null
    newfs -F -O "$2" -s 64m /root/bko-vnd$vnd.img >/dev/null &&
        vndconfig vnd$vnd /root/bko-vnd$vnd.img && mkdir -p "$1" &&
        mount_ffs ${3:+-o "$3"} /dev/vnd${vnd}a "$1" ||
        { echo "can't mount an FFS image, newfs -O $2, ${3:-no options}, at $1"; exit 1; }
    vnd=$((vnd + 1))
}
ea_mount() { vnd_mount "$1" 2ea "$2"; }  # DIR posix1eacls|nfs4acls

# What every one of the three file systems gets: file flags, symlink
# modes, birth times.
common() {
    cd "$1" || exit 1
    echo linked >hardlink-a; ln hardlink-a hardlink-b
    ln -s hardlink-a symlink; ln -s hardlink-a symlink-0700; ln -s hardlink-a symlink-0777
    chmod -h 0700 symlink-0700; chmod -h 0777 symlink-0777
    # Birth times: the kernel lowers one to an mtime set earlier than it, so
    # these were born in 2001 and last changed in 2010.  "unborn" was born
    # now and changed in 1990: its birth comes down to 1990 with the mtime.
    echo born >born; mkdir born-dir; ln -s born born-symlink
    for p in born born-dir; do touch -t 200101010101 $p; touch -t 201001010101 $p; done
    touch -h -t 200101010101 born-symlink; touch -h -t 201001010101 born-symlink
    touch -t 199001010101 unborn
    # An atime apart from the mtime, to the nanosecond: a file, a directory
    # and a symlink.
    echo atime >atime-ns; mkdir atime-ns-dir; ln -s atime-ns atime-ns-symlink
    touch -d 2004-04-04T04:04:04.444444444 atime-ns atime-ns-dir
    touch -h -d 2004-04-04T04:04:04.444444444 atime-ns-symlink
    touch -a -d 2003-03-03T03:03:03.333333333 atime-ns atime-ns-dir
    touch -h -a -d 2003-03-03T03:03:03.333333333 atime-ns-symlink
}
# File flags, last: most of them stop any change afterwards.
flags() {
    cd "$1" || exit 1
    for f in uchg uappnd nodump opaque arch schg sappnd; do echo $f >flag-$f; try chflags $f flag-$f; done
    mkdir uchg-dir; echo inside >uchg-dir/inside; try chflags uchg uchg-dir
    try chflags schg,nodump hardlink-a
    try chflags -h uchg,nodump symlink
    # SF_LOG (0x400000) marks the WAPBL journal's inode: the kernel lets
    # root set it, the tools must not keep it.  Octal 20000001: SF_LOG|nodump.
    echo log >flag-log; try chflags 20000001 flag-log
}

mkdir -p $SRC && cd $SRC || exit 1
touch no-such-user; chown 1234:5678 no-such-user
touch setuid; chown 1234:5678 setuid; chmod 4755 setuid
touch setgid; chmod 2755 setgid
mkdir sticky-dir; chmod 1777 sticky-dir
ln -s /nonexistent/target symlink-dangling
mkfifo fifo
dd if=/dev/zero of=sparse bs=1048576 count=0 seek=1024 2>/dev/null
echo start | dd of=sparse conv=notrunc 2>/dev/null
echo middle | dd of=sparse bs=1048576 seek=512 conv=notrunc 2>/dev/null
touch "name with spaces" "$(printf '\377\376')-not-utf8"
touch -d 2001-02-03T04:05:06.123456789 nanoseconds
touch -d 2100-01-01T12:00:00 future
common $SRC

# The root's own extended attributes, where it has them (AlpineBSD).
root_ea=""
echo data >root-xattrs
if setextattr user backotter hello root-xattrs 2>/dev/null; then
    root_ea=1
    setextattr user binary "$(printf '\377\020\376')" root-xattrs
    setextattr system backotter root-only root-xattrs
    ln -s root-xattrs root-xattrs-symlink; setextattr -h user backotter on-the-link root-xattrs-symlink
    mkdir root-xattrs-dir; setextattr user backotter on-a-dir root-xattrs-dir
fi
ea_mount $SRC/posix1e posix1eacls
ea_mount $SRC/nfs4 nfs4acls
for fs in posix1e nfs4; do
    common $SRC/$fs
    # Extended attributes: user and system namespace, one value binary (no
    # NUL: setextattr takes it as an argument); one on a symlink.
    echo data >xattrs
    setextattr user backotter hello xattrs
    setextattr user binary "$(printf '\377\020\376')" xattrs
    setextattr system backotter root-only xattrs
    setextattr -h user backotter on-the-link symlink
done
# POSIX.1e: named entries, on a file and a symlink; a default ACL that a
# directory hands down.
cd $SRC/posix1e
echo acl >acl-file; setfacl -m u:1001:rwx,g:5678:r,m::rwx acl-file
setfacl -h -m u:1001:r,m::r symlink-0700
mkdir acl-dir; setfacl -d -m u::rwx,g::r-x,o::r-x,m::rwx,u:1001:r-x acl-dir
echo inherited >acl-dir/inherited
# NFSv4: a named entry, a deny entry, and ones a directory hands down.
cd $SRC/nfs4
echo acl >acl-file; setfacl -m u:1001:rwx::allow,g:5678:w::deny acl-file
mkdir acl-dir; setfacl -m u:1001:rx:fd:allow acl-dir
echo inherited >acl-dir/inherited
for d in $SRC $SRC/posix1e $SRC/nfs4; do flags $d; done
touch -d 2001-02-03T04:05:06.000000006 $SRC/posix1e $SRC/nfs4 $SRC

# The listings compared, one file each, for the tree below $1.
listings() {  # DIR OUT-PREFIX
    cd "$1" || exit 1
    find . | sort >$2.names
    # mtree compares symlink modes too: those are a check of their own.
    mtree -c -p . -k type,uid,gid,nlink,size,time,link,sha256 >$2.mtree
    find . ! -type l -exec stat -f '%N %Mp%Lp' {} + | sort >$2.modes
    # flag-log (SF_LOG|nodump) comes back as nodump only: a check of its own.
    find . -exec stat -f '%N %Xf' {} + | sort >$2.flags-all
    grep -v '/flag-log ' $2.flags-all >$2.flags
    grep '/flag-log ' $2.flags-all >$2.flags-log
    find . -type l -exec stat -f '%N %Lp' {} + | sort >$2.symlink-modes
    # What a perfect restore gives: the birth time, or the mtime when that is
    # earlier (the kernel has no way to set a birth time later than it).
    find . -exec stat -f '%FB %Fm %N' {} + | awk '{
        split($1, b, "."); split($2, m, ".")
        early = (b[1] + 0 < m[1] + 0 || (b[1] + 0 == m[1] + 0 && b[2] + 0 <= m[2] + 0)) ? $1 : $2
        print substr($0, length($1) + length($2) + 3), early }' | sort >$2.birth
    for fs in posix1e nfs4; do
        find ./$fs ! -type l | sort | while IFS= read -r p; do getfacl -n "$p"; done
        find ./$fs -type d | sort | while IFS= read -r p; do echo "# default"; getfacl -n -d "$p"; done
        find ./$fs -type l | sort | while IFS= read -r p; do getfacl -n -h "$p"; done
    done >$2.acls 2>&1
    for ns in user system; do
        { find ./posix1e ./nfs4; [ -n "$root_ea" ] && find . -name 'root-xattrs*'; } | sort | while IFS= read -r p; do
            for name in $(lsextattr -q -h $ns "$p" | tr ' \t' '\n\n' | sort); do
                # The ACLs' own storage: compared as ACLs.
                case $ns.$name in system.posix1e.acl_access|system.posix1e.acl_default|system.nfs4.acl) continue ;; esac
                echo "$p $name $(getextattr -q -h -x $ns "$name" "$p" | md5)"
            done
        done >$2.extattrs-$ns 2>&1
    done
}
failed=""
after=/tmp/bko-rt.after
check() {  # TITLE KIND: compare before.KIND with $after.KIND
    echo "== $1"
    if diff /tmp/bko-rt.before.$2 $after.$2; then
        echo "identical ($(wc -l </tmp/bko-rt.before.$2 | tr -d ' ') lines)"
    else
        failed="$failed; $1"
    fi
}

# Atimes and mtimes to the nanosecond (mtree compares mtimes to the
# microsecond), name by name from the source's list: looking a name up
# moves no atime, reading a file or listing a directory does.  The source's
# are taken with its file systems mounted noatime, from the listings
# through the backup; but NetBSD drops the atimes utimensat(2) sets on one
# mounted noatime too, so the restore has atimes on, and the restored
# times are taken before anything reads the tree.
times_of() {  # DIR OUT-PREFIX
    (cd "$1" && tr '\n' '\0' </tmp/bko-rt.before.names | xargs -0 stat -f '%N %Fa %Fm' | sort >$2.times)
}
# mount -u replaces the options: the ACL kind and the root's journal go
# along again.
atimes() {  # noatime|atime: remount the source's file systems so
    log=""; mount | grep -q ' on / type ffs (log' && log=,log
    mount -u -o $1$log / &&
        mount -u -o $1,posix1eacls $SRC/posix1e && mount -u -o $1,nfs4acls $SRC/nfs4 ||
        { echo "can't remount the source's file systems $1"; exit 1; }
}
atimes noatime
listings $SRC /tmp/bko-rt.before
times_of $SRC /tmp/bko-rt.before
bko_init || { echo "INIT FAILED"; exit 1; }
# A backup that fails may still have saved a snapshot (restic's exit 3: a
# file it couldn't read): what it holds is restored all the same.
echo "== backup"
bko_backup $SRC; status=$?
echo "exit $status"
[ $status -eq 0 ] || failed="$failed; backup"
rt_snapshots $R $RR $RESTIC_REPOSITORY $SRC
atimes atime
mkdir -p $DST
ea_mount $DST/posix1e posix1eacls
ea_mount $DST/nfs4 nfs4acls
echo "== restore"
bko_restore "latest:$SRC" $DST; status=$?
echo "exit $status"
[ $status -eq 0 ] || failed="$failed; restore"
# The restored tree against the source, its listings into $after.*; X goes
# after each check's title.
compare_all() {  # X
    x=$1
    times_of $DST $after
    listings $DST $after
    t="mtree: type, owner, group, links, size, mtime, symlink target, SHA-256$x"
    echo "== $t"
    (cd $DST && mtree -f /tmp/bko-rt.before.mtree -p .) && echo identical || failed="$failed; $t"
    check "modes (not symlinks)$x" modes
    check "file flags (netbsd.file_flags)$x" flags
    t="SF_LOG dropped, nodump kept (netbsd.file_flags mask 0xff1fffff)$x"
    echo "== $t"
    cat /tmp/bko-rt.before.flags-log
    if [ "$(grep -c ' 400001$' /tmp/bko-rt.before.flags-log)" != 3 ]; then
        echo "the source hasn't SF_LOG|nodump on its three flag-log files"; failed="$failed; $t"
    elif sed 's/ 400001$/ 1/' /tmp/bko-rt.before.flags-log | diff - $after.flags-log; then
        echo "restored: nodump only (3 lines)"
    else
        failed="$failed; $t"
    fi
    check "symlink modes$x" symlink-modes
    check "atime and mtime to the nanosecond$x" times
    check "birth times, min(birth, mtime) (unix.birthtime)$x" birth
    check "ACLs (netbsd.acl_*)$x" acls
    check "user extended attributes$x" extattrs-user
    check "system extended attributes (netbsd.system_extattrs)$x" extattrs-system
    [ -n "$root_ea" ] && echo "   (both with the root's own: root-xattrs*)"
    t="hard links (one inode for both)$x"
    echo "== $t"
    n=$(ls -i $DST/hardlink-a $DST/hardlink-b | awk '{print $1}' | sort -u | wc -l | tr -d ' ')
    echo "inodes: $n"; [ "$n" = 1 ] || failed="$failed; $t"
}
compare_all ""
echo "== sparse (KB allocated, source then restored)"
du -k $SRC/sparse $DST/sparse

# Again over the restored tree, changed: the file flags off (or nothing
# could change), a directory replaced by a symlink to one elsewhere, files
# with other bytes, mode and mtime, an extended attribute removed.
echo "== restore over an existing, changed tree"
chflags -R 0 $DST && find $DST -type l -exec chflags -h 0 {} +
rmextattr user backotter $DST/posix1e/xattrs
rt_over $DST uchg-dir atime-ns posix1e/xattrs nfs4/acl-file ||
    failed="$failed; preparing the restore over an existing tree"
bko_restore "latest:$SRC" $DST; status=$?
echo "exit $status"
[ $status -eq 0 ] || failed="$failed; restore over an existing tree"
t="what the replaced directory's symlink pointed to, untouched"
echo "== $t"
rt_over_check && echo "untouched ($(wc -l </tmp/bko-rt.elsewhere.before | tr -d ' ') names)" || failed="$failed; $t"
after=/tmp/bko-rt.over
compare_all " (over an existing tree)"

# The restore target's two images free vnd2 and vnd3 for the rest.
umount $DST/posix1e && umount $DST/nfs4 && vndconfig -u vnd2 && vndconfig -u vnd3 ||
    { echo "can't free vnd2 and vnd3"; exit 1; }
vnd=2
# FFSv1: no birth times (stat shows 0), so none in the snapshot, and a
# restore onto FFSv1 that sets none and says nothing about them.
F1=/root/bko-ffs1 F1R=/root/bko-ffs1-restored
vnd_mount $F1 1
vnd_mount $F1R 1
(cd $F1 && echo born >born && mkdir born-dir && ln -s born born-symlink &&
    for p in born born-dir; do touch -t 200101010101 $p; touch -t 201001010101 $p; done &&
    touch -h -t 201001010101 born-symlink && touch -d 2001-02-03T04:05:06.123456789 nanoseconds)
echo "== FFSv1: birth times as stat shows them (not a check)"
(cd $F1 && stat -f '  %N birth %FB mtime %Fm' born born-dir born-symlink nanoseconds)
mount -u -o noatime $F1
mtree -c -p $F1 -k type,uid,gid,mode,nlink,size,time,link,sha256 >/tmp/bko-rt.ffs1.mtree
(cd $F1 && stat -f '%N %Fa %Fm' born born-dir born-symlink nanoseconds) >/tmp/bko-rt.ffs1.times
echo "== FFSv1: backup with $(basename $R)"
rt_backup $R /root/bko-rt-repo-ffs1 $F1; echo "exit $?"
t="FFSv1: no unix.birthtime in the snapshot (birth 0 isn't one)"
echo "== $t"
if rt_nodes $(rt_restic $R $RR) /root/bko-rt-repo-ffs1 $F1 >/tmp/bko-rt.ffs1.nodes; then
    sed 's/^/  /' /tmp/bko-rt.ffs1.nodes
    if grep -q 'unix\.birthtime' /tmp/bko-rt.ffs1.nodes; then failed="$failed; $t"; else echo "none"; fi
else
    echo "can't decode the snapshot"; failed="$failed; $t"
fi
t="FFSv1 restored onto FFSv1: exit 0, no messages, mtree and times identical"
echo "== $t"
bko_restore_repo() {  # REPO SNAPSHOT-PATH TARGET: $RR's restore from REPO, its messages in /tmp/bko-rt.msgs
    repo=$RESTIC_REPOSITORY
    export RESTIC_REPOSITORY=$1 RUSTIC_REPOSITORY=$1
    bko_restore "$2" "$3" >/tmp/bko-rt.msgs 2>&1; rs=$?
    export RESTIC_REPOSITORY=$repo RUSTIC_REPOSITORY=$repo
    grep -v -e 'failed to get system time zone' -e '^\[INFO\]' -e '^Files: ' -e '^Dirs: ' \
        -e '^restore done' -e '^using no cache' -e '^Summary: Restored ' /tmp/bko-rt.msgs >/tmp/bko-rt.msgs.f
    sed 's/^/  says: /' /tmp/bko-rt.msgs.f
    return $rs
}
bko_restore_repo /root/bko-rt-repo-ffs1 "latest:$F1" $F1R; status=$?
echo "exit $status"
ok=1
[ $status -eq 0 ] && [ ! -s /tmp/bko-rt.msgs.f ] || ok=""
(cd $F1R && stat -f '%N %Fa %Fm' born born-dir born-symlink nanoseconds) >/tmp/bko-rt.ffs1.times-after
diff /tmp/bko-rt.ffs1.times /tmp/bko-rt.ffs1.times-after || ok=""
mtree -f /tmp/bko-rt.ffs1.mtree -p $F1R || ok=""
[ -n "$ok" ] && echo "yes" || failed="$failed; $t"

# The FFSv2ea part's snapshot (extended attributes, ACLs, birth times) onto
# file systems without extended attributes: FFSv1, then plain FFSv2.
# Its attributes, NAMESPACE.NAME: a system one is named with its namespace,
# a user one by its bare name in a message that isn't about a system one.
names=$(for ns in user system; do sed -n "s|^\./posix1e/[^ ]* \([^ ]*\) .*|$ns.\1|p" /tmp/bko-rt.before.extattrs-$ns; done |
    sort -u | tr '\n' ' ')
names=${names% }
noea() {  # TARGET KIND
    t="the FFSv2ea part restored onto $2: warns, naming each extended attribute ($names)"
    echo "== $t"
    bko_restore_repo /root/bko-rt-repo "latest:$SRC/posix1e" $1; echo "exit $?"
    missing=""
    for a in $names; do
        case $a in
            system.*) grep -q -- "$a" /tmp/bko-rt.msgs.f ;;
            *) grep -v -- "system.${a#user.}" /tmp/bko-rt.msgs.f | grep -q -- "${a#user.}" ;;
        esac || missing="$missing $a"
    done
    if [ -z "$missing" ]; then echo "every one named"; else echo "not named:$missing"; failed="$failed; $t"; fi
}
noea $F1R/posix1e FFSv1
umount $F1R && vndconfig -u vnd3 || { echo "can't free vnd3"; exit 1; }
vnd=3
vnd_mount /root/bko-ffs2-plain 2
noea /root/bko-ffs2-plain/posix1e "plain FFSv2"
echo "roundtrip: $([ -z "$failed" ] && echo PASS || echo "FAIL: ${failed#; }")"
