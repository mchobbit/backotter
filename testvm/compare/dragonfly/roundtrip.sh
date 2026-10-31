#!/bin/sh
# Round trip of DragonFly's file metadata through restic, or BackOtter's
# rustic: make a tree of hard-to-preserve files on HAMMER2 (/root) and one
# on UFS (/boot), back both up in one snapshot, restore each as
# "latest:TREE" next to itself, and compare: mtree(8), and on their own the
# modes (of symlinks apart), the file flags, device numbers, and atimes and
# mtimes to the nanosecond.  DragonFly has no ACLs, extended attributes or
# birth times; what it has is file flags, all 32 bits settable by root on
# both file systems.  HAMMER2 keeps times to the microsecond and no atime of
# its own (it shows the mtime); UFS keeps both, to the nanosecond.
#
# Usage: roundtrip.sh BACKUP [RESTORE]   (as root, in a throwaway guest: at
#                                         securelevel 1, schg and sappnd
#                                         can't be cleared again)
# BACKUP and RESTORE are restic or BackOtter's rustic; RESTORE defaults to
# BACKUP.  Run it with sh: root's login shell is csh.

R=$1 RR=${2:-$1}
TREES="/root/bko-rt /boot/bko-rt"
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

# touch(1) here sets times to the microsecond (utimes(2)), and there's no
# mknod(8): a helper does both.
#   helper times [-h] PATH ASEC ANSEC MSEC MNSEC   atime, mtime to the ns
#   helper mknod PATH b|c MAJOR MINOR              a device node, mode 0644
cat >/tmp/bko-rt-helper.c <<'C'
#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv)
{
	struct timespec ts[2];
	int flags = 0;

	if (argc == 6 && strcmp(argv[1], "mknod") == 0) {
		mode_t type = strcmp(argv[3], "b") == 0 ? S_IFBLK : S_IFCHR;

		if (mknod(argv[2], type | 0644, makedev(atoi(argv[4]), atoi(argv[5]))) != 0) {
			perror(argv[2]);
			return 1;
		}
		return 0;
	}
	if (argc > 2 && strcmp(argv[1], "times") == 0) {
		argv++, argc--;
		if (argc > 1 && strcmp(argv[1], "-h") == 0) {
			flags = AT_SYMLINK_NOFOLLOW;
			argv++, argc--;
		}
		if (argc == 6) {
			ts[0].tv_sec = atoll(argv[2]), ts[0].tv_nsec = atol(argv[3]);
			ts[1].tv_sec = atoll(argv[4]), ts[1].tv_nsec = atol(argv[5]);
			if (utimensat(AT_FDCWD, argv[1], ts, flags) != 0) {
				perror(argv[1]);
				return 1;
			}
			return 0;
		}
	}
	fprintf(stderr, "usage: helper times [-h] path asec ansec msec mnsec\n"
	    "       helper mknod path b|c major minor\n");
	return 2;
}
C
cc -o /tmp/bko-rt-helper /tmp/bko-rt-helper.c || { echo "can't build the helper"; exit 1; }
nstouch() { /tmp/bko-rt-helper times "$@"; }

for t in $TREES; do
    mkdir -p $t && cd $t || exit 1
    touch no-such-user; chown 1234:5678 no-such-user
    touch setuid; chown 1234:5678 setuid; chmod 4755 setuid
    touch setgid; chmod 2755 setgid
    mkdir sticky-dir; chmod 1777 sticky-dir
    echo linked >hardlink-a; ln hardlink-a hardlink-b; mkdir sub; ln hardlink-a sub/hardlink-c
    ln -s hardlink-a symlink; ln -s /nonexistent/target symlink-dangling
    ln -s hardlink-a symlink-0700; chmod -h 0700 symlink-0700
    ln -s hardlink-a symlink-0777; chmod -h 0777 symlink-0777
    mkfifo fifo
    touch "name with spaces" "$(printf '\377\376')-not-utf8"
    touch -t 200102030405.06 old
    touch -t 210001011200 future
    # Times with nanoseconds: an mtime alone (atime the same), and an atime
    # apart from the mtime on a file, a directory and a symlink.
    touch nanoseconds; nstouch nanoseconds 981173106 123456789 981173106 123456789
    echo atime >atime-ns; mkdir atime-ns-dir; ln -s atime-ns atime-ns-symlink
    for p in atime-ns atime-ns-dir; do nstouch $p 1046660583 333333333 1081051444 444444444; done
    nstouch -h atime-ns-symlink 1046660583 333333333 1081051444 444444444
    # Devices: a block and a character device, one with /dev/null's number
    # and one with a number no device has.  DragonFly's dev_t is 32 bits, the
    # minor number in bits 0-7 and 16-31 around the major's 8-15: 65541
    # (0x10005) needs both parts of it.  Outside devfs, though, stat shows
    # every node's number as NOUDEV (255,0xffff00ff), whatever it was made
    # with: what can be compared is the type, and that the node is there.
    try /tmp/bko-rt-helper mknod blockdev b 13 2
    try /tmp/bko-rt-helper mknod chardev c 13 2
    try /tmp/bko-rt-helper mknod chardev-nodev c 33 65541
    try /tmp/bko-rt-helper mknod chardev-nodump c 13 2
    # File flags last: most of them stop any change afterwards.  Every flag
    # <sys/stat.h> names, as octal numbers: chflags(1) takes uunlnk and
    # sunlnk but sets nothing.  nohistory, cache, xlink and their system
    # twins are DragonFly's own ("noscache" is on /usr/obj after an install).
    for f in nodump:1 uchg:2 uappnd:4 opaque:10 uunlnk:20 nohistory:100 cache:200 xlink:400 \
            arch:200000 schg:400000 sappnd:1000000 sunlnk:4000000 noshistory:20000000 \
            noscache:40000000 sxlink:100000000; do
        echo ${f%:*} >flag-${f%:*}; try chflags ${f#*:} flag-${f%:*}
    done
    mkdir uchg-dir; echo inside >uchg-dir/inside; try chflags uchg,nohistory uchg-dir
    mkdir schg-dir; echo inside >schg-dir/inside; try chflags schg,noscache schg-dir
    try chflags schg,nodump hardlink-a
    try chflags -h uchg,cache symlink
    # On a FIFO and a device.
    mkfifo fifo-flags; try chflags nodump,uchg fifo-flags
    try chflags nodump chardev-nodump
    nstouch $t 1046660583 333333333 981173106 6
done
# HAMMER2 only: /boot is small.
cd /root/bko-rt && dd if=/dev/zero of=sparse bs=1m count=0 seek=1024 2>/dev/null
echo start | dd of=sparse conv=notrunc 2>/dev/null
echo middle | dd of=sparse bs=1m seek=512 conv=notrunc 2>/dev/null
nstouch /root/bko-rt 1046660583 333333333 981173106 6
# Reading a file or a directory must not move its atime, here or in the
# backup: UFS has atimes on (HAMMER2 has none).
mount -u -o noatime /boot

listings() {  # DIR OUT-PREFIX: the listings compared, one file each
    cd "$1" || exit 1
    # Times to the nanosecond (but HAMMER2 keeps microseconds).
    find . -exec stat -f '%N %Fa %Fm' {} + | sort >$2.times
    find . ! -type l -exec stat -f '%N %Mp%Lp' {} + | sort >$2.modes
    find . -type l -exec stat -f '%N %Lp' {} + | sort >$2.symlink-modes
    find . -exec stat -f '%N %Xf' {} + | sort >$2.flags
    find . \( -type b -o -type c \) -exec stat -f '%N %HT %Hr,%Lr' {} + | sort >$2.devices
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

for t in $TREES; do
    n=$(basename $(dirname $t))
    # mtree compares symlink modes too: those are a check of their own.
    mtree -c -p $t -k type,uid,gid,nlink,size,time,link,sha256digest >/tmp/bko-rt.$n.spec
    listings $t /tmp/bko-rt.before.$n
done
cd /root
bko_init || { echo "INIT FAILED"; exit 1; }
# A backup that fails may still have saved a snapshot (restic's exit 3: a
# file it couldn't read): what it holds is restored all the same.
echo "== backup"
bko_backup $TREES; status=$?
echo "exit $status"
[ $status -eq 0 ] || failed="$failed; backup"
for t in $TREES; do
    n=$(basename $(dirname $t))
    echo "== restore of $t"
    bko_restore "latest:$t" $t-restored; status=$?
    echo "exit $status"
    [ $status -eq 0 ] || failed="$failed; restore of $t"
    listings $t-restored /tmp/bko-rt.after.$n
    s="mtree of $t: type, owner, group, links, size, mtime, symlink target, SHA-256"
    echo "== $s"
    mtree -f /tmp/bko-rt.$n.spec -p $t-restored && echo identical || failed="$failed; $s"
    check "modes of $t (not symlinks)" $n.modes
    check "symlink modes of $t" $n.symlink-modes
    check "file flags of $t (dragonfly.file_flags)" $n.flags
    check "devices of $t (type; the number shows as NOUDEV)" $n.devices
    sed 's/^/  /' /tmp/bko-rt.before.$n.devices
    check "atime and mtime of $t to the nanosecond" $n.times
    s="hard links of $t (one inode for all three)"
    echo "== $s"
    i=$(ls -i $t-restored/hardlink-a $t-restored/hardlink-b $t-restored/sub/hardlink-c | awk '{print $1}' | sort -u | wc -l | tr -d ' ')
    echo "inodes: $i"; [ "$i" = 1 ] || failed="$failed; $s"
done
echo "== sparse (KB allocated, source then restored)"
du -k /root/bko-rt/sparse /root/bko-rt-restored/sparse
echo "roundtrip: $([ -z "$failed" ] && echo PASS || echo "FAIL: ${failed#; }")"
