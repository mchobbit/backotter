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
# what a tool doesn't keep shows up as just that.
#
# Usage: roundtrip.sh BACKUP [RESTORE]   (as root, in a throwaway guest:
#                                         kern.securelevel <= 0, or schg and
#                                         sappnd can't be cleared again)
# BACKUP and RESTORE are restic or BackOtter's rustic; RESTORE defaults to
# BACKUP.  Runs on AlpineBSD too (NetBSD's kernel and libc): with NetBSD's
# mtree, stat, chflags, setfacl, getfacl, *extattr, md5, ls and touch in
# /root/nbtools, which comes first in PATH.

PATH=/root/nbtools:/sbin:/usr/sbin:/bin:/usr/bin
R=$1 RR=${2:-$1}
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

# An FFSv2ea file system (UFS2 extended attributes) on a vnd(4) image,
# mounted at DIR with the ACL kind given.  vnd0-3 are the four GENERIC has.
vnd=0
ea_mount() {  # DIR posix1eacls|nfs4acls
    dd if=/dev/zero of=/root/bko-ea$vnd.img bs=1048576 count=0 seek=64 2>/dev/null
    newfs -F -O 2ea -s 64m /root/bko-ea$vnd.img >/dev/null &&
        vndconfig vnd$vnd /root/bko-ea$vnd.img && mkdir -p "$1" &&
        mount_ffs -o "$2" /dev/vnd${vnd}a "$1" || { echo "can't mount an FFSv2ea image with $2"; exit 1; }
    vnd=$((vnd + 1))
}

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
        find ./posix1e ./nfs4 | sort | while IFS= read -r p; do
            for name in $(lsextattr -q -h $ns "$p" | tr ' \t' '\n\n' | sort); do
                # The ACLs' own storage: compared as ACLs.
                case $ns.$name in system.posix1e.acl_access|system.posix1e.acl_default|system.nfs4.acl) continue ;; esac
                echo "$p $name $(getextattr -q -h -x $ns "$name" "$p" | md5)"
            done
        done >$2.extattrs-$ns 2>&1
    done
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

# Atimes and mtimes to the nanosecond (mtree compares mtimes to the
# microsecond), name by name from the source's list: looking a name up
# moves no atime, reading a file or listing a directory does.  The source's
# are taken with its file systems mounted noatime, from the listings
# through the backup; but NetBSD drops the atimes utimensat(2) sets on one
# mounted noatime too, so the restore has atimes on, and the restored
# times are taken before anything reads the tree.
times() {  # DIR OUT-PREFIX
    (cd "$1" && tr '\n' '\0' </tmp/bko-rt.before.names | xargs -0 stat -f '%N %Fa %Fm' | sort >$2.times)
}
atimes() {  # noatime|atime: remount the source's file systems so
    for m in / $SRC/posix1e $SRC/nfs4; do
        mount -u -o $1 $m || { echo "can't remount $m $1"; exit 1; }
    done
}
atimes noatime
listings $SRC /tmp/bko-rt.before
times $SRC /tmp/bko-rt.before
bko_init || { echo "INIT FAILED"; exit 1; }
# A backup that fails may still have saved a snapshot (restic's exit 3: a
# file it couldn't read): what it holds is restored all the same.
echo "== backup"
bko_backup $SRC; status=$?
echo "exit $status"
[ $status -eq 0 ] || failed="$failed; backup"
atimes atime
mkdir -p $DST
ea_mount $DST/posix1e posix1eacls
ea_mount $DST/nfs4 nfs4acls
echo "== restore"
bko_restore "latest:$SRC" $DST; status=$?
echo "exit $status"
[ $status -eq 0 ] || failed="$failed; restore"
times $DST /tmp/bko-rt.after
listings $DST /tmp/bko-rt.after
t="mtree: type, owner, group, links, size, mtime, symlink target, SHA-256"
echo "== $t"
(cd $DST && mtree -f /tmp/bko-rt.before.mtree -p .) && echo identical || failed="$failed; $t"
check "modes (not symlinks)" modes
check "file flags (netbsd.file_flags)" flags
t="SF_LOG dropped, nodump kept (netbsd.file_flags mask 0xff1fffff)"
echo "== $t"
cat /tmp/bko-rt.before.flags-log
if [ "$(grep -c ' 400001$' /tmp/bko-rt.before.flags-log)" != 3 ]; then
    echo "the source hasn't SF_LOG|nodump on its three flag-log files"; failed="$failed; $t"
elif sed 's/ 400001$/ 1/' /tmp/bko-rt.before.flags-log | diff - /tmp/bko-rt.after.flags-log; then
    echo "restored: nodump only (3 lines)"
else
    failed="$failed; $t"
fi
check "symlink modes" symlink-modes
check "atime and mtime to the nanosecond" times
check "birth times, min(birth, mtime) (unix.birthtime)" birth
check "ACLs (netbsd.acl_*)" acls
check "user extended attributes" extattrs-user
check "system extended attributes (netbsd.system_extattrs)" extattrs-system
t="hard links (one inode for both)"
echo "== $t"
n=$(ls -i $DST/hardlink-a $DST/hardlink-b | awk '{print $1}' | sort -u | wc -l | tr -d ' ')
echo "inodes: $n"; [ "$n" = 1 ] || failed="$failed; $t"
echo "== sparse (KB allocated, source then restored)"
du -k $SRC/sparse $DST/sparse
echo "roundtrip: $([ -z "$failed" ] && echo PASS || echo "FAIL: ${failed#; }")"
