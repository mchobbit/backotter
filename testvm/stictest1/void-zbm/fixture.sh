#!/bin/bash
# The fixture for void-zbm, one ZFS dataset at a time (no SELinux or
# AppArmor: the guest keeps no labels on disk):
#   fixture.sh ROOT main     the main tree, under ROOT/up1/up2/tree (ROOT: a
#                            fresh dataset made like the booted boot
#                            environment zroot/ROOT/void, in a scratch pool
#                            made like zroot): the directories above, every
#                            checklist feature ZFS has on Linux (xattrs,
#                            POSIX ACLs, capabilities, i a d, project IDs
#                            and P, ZFS's DOS attributes, a 60000-byte
#                            xattr...), and the guest's own files from /
#                            (set-id, iputils-ping's cap_net_raw=p, shadow,
#                            hostid, /etc/bko-be, dracut's zol.conf,
#                            /etc/zfs, xbps's database, DKMS's tree for
#                            both kernels, runit's services and links,
#                            seedrng's seed, /boot's running kernel)
#   fixture.sh DIR ds GM     on another dataset made like the guest's
#                            dataset mounted at GM (/home), mounted inside
#                            the tree as the guest mounts it: the features
#                            again, GM's own mode, owner and times, and
#                            GM's own files (tester's home)
#   fixture.sh DIR be GM     on a dataset made like an inactive boot
#                            environment (zroot/ROOT/void-6.18, a clone),
#                            mounted inside the tree at /run/bko-be/NAME as
#                            bko-holt mounts it; GM the guest's BE, mounted
#                            read-only there by inner.sh: the features
#                            again, GM's mode, owner and times, and of its
#                            files those ZFSBootMenu boots and names it by:
#                            its /boot (kernel, initramfs, config: restored,
#                            not rebuilt, Decision 6), /etc/bko-be,
#                            /etc/hostid, /etc/os-release
# What can't be made is reported on stderr as "fixture: skipped: ..." (the
# judge fails on any); what ZFS on Linux refuses by design as "fixture:
# zfs-refused: ..." and a dataset without ACLs as "fixture: noacl: ..." (the
# judge's LIMIT lines).  setfattr and setfacl are bkoshim.py on this guest.
# From proxmox9's fixture.sh.
set -u
ROLE=$2 GM=${3:-}
skip() { echo "fixture: skipped: $*" >&2; }
try() { "$@" 2>/dev/null || skip "$*"; }
acl() {  # PATH NAME ENTRY...   ENTRY = tag:perm:id  (tags 1 2 4 8 16 32)
    python3 - "$@" <<'PY' || skip "acl $*"
import os, struct, sys
p, n = sys.argv[1], sys.argv[2]
b = struct.pack('<I', 2)
for e in sys.argv[3:]:
    t, perm, i = (int(x, 0) for x in e.split(':'))
    b += struct.pack('<HHI', t, perm, i)
os.setxattr(p, n, b, follow_symlinks=False)
PY
}
flag() { chattr "+$1" "$2" 2>/dev/null && [[ $(lsattr -d "$2" | cut -d' ' -f1) == *"$1"* ]] || skip "chattr +$1 $2"; }
zflag() {  # FLAG PATH: a flag OpenZFS on Linux refuses (zpl_ioctl_setflags takes i a d P only)
    if chattr "+$1" "$2" 2>/dev/null && [[ $(lsattr -d "$2" | cut -d' ' -f1) == *"$1"* ]]; then echo "fixture: zfs-took: $1" >&2
    else echo "fixture: zfs-refused: $1" >&2; fi
}
dosflags() {  # PATH VALUE: ZFS_IOC_SETDOSFLAGS (replaces the whole set)
    python3 -c 'import fcntl, os, struct, sys
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
fcntl.ioctl(fd, 0x40088302, struct.pack("Q", int(sys.argv[2], 0)))' "$@" || skip "DOS flags $2 on $1"
}
N=0xffffffff

if [[ $ROLE == main ]]; then
    R=$1; U1=$R/up1; U2=$U1/up2; T=$U2/tree
else
    T=$1
fi
mkdir -p "$T"
HASACL=1
[[ ,$(findmnt -n -o OPTIONS --target "$T"), == *,posixacl,* ]] || { HASACL=0; echo "fixture: noacl: ${GM:-/}" >&2; }

# Real examples from the guest's own files.
mkdir -p "$T/real"
if [[ $ROLE == main ]]; then
    # Set-id binaries (shadow's, util-linux's, sudo, ssh-keysign, set-gid
    # write and wall, xbps-uchroot 4750 root:xbuilder), iputils-ping with
    # its cap_net_raw=p, shadow, the host id (random per build; ZBM hands
    # the BE spl_hostid=0x00bab10c), the BE's name, runit's rc.conf, the
    # fstab with the ESP, dracut's zol.conf, /etc/zfs, xbps's database
    # (pkgdb, keys, the repositories' indexes), DKMS's tree (zfs 2.4.4 for
    # both kernels: symlinks, logs, Module.symvers), runit's services and
    # /var/service's link, seedrng's seed (0600 in a 0700 directory),
    # /etc/default, /var/log, /boot's running kernel.
    for f in /usr/bin/passwd /usr/bin/su /usr/bin/mount /usr/bin/umount /usr/bin/sudo /usr/bin/newuidmap /usr/bin/chfn \
             /usr/bin/write /usr/bin/wall /usr/bin/xbps-uchroot /usr/bin/unix_chkpwd /usr/libexec/ssh-keysign /usr/bin/iputils-ping \
             /etc/shadow /etc/hostid /etc/hostname /etc/hosts /etc/fstab /etc/rc.conf /etc/bko-be /etc/os-release /var/service; do
        [[ -e $f || -L $f ]] && try cp -a --preserve=all "$f" "$T/real/$(basename "$f")"
    done
    for d in /etc/dracut.conf.d:dracut.conf.d /etc/zfs:etc-zfs /var/db/xbps:xbps-db /var/lib/dkms:dkms /etc/runit:runit \
             /etc/sv:sv /var/lib/seedrng:seedrng /etc/default:default /var/log:log; do
        [[ -d ${d%%:*} ]] && try cp -a --preserve=all "${d%%:*}" "$T/real/${d#*:}"
    done
    # The running kernel and its config (not its initramfs: rebuilt by
    # load, and a large compressed file says nothing more; the inactive
    # BE's, which load restores, is on that BE's dataset).
    mkdir -p "$T/real/boot"
    for f in /boot/vmlinuz-$(uname -r) /boot/config-$(uname -r); do
        [[ -f $f ]] && try cp -a --preserve=all "$f" "$T/real/boot/"
    done
    touch -r /boot "$T/real/boot"
elif [[ $ROLE == be ]]; then
    # The BE's root's mode and owner (its times at the end), and what
    # ZFSBootMenu boots from it and names it by.
    chmod --reference="${GM:?}" "$T"; chown --reference="$GM" "$T"
    mkdir -p "$T/real/guest/etc"
    try cp -a --preserve=all "$GM/boot" "$T/real/guest/boot"
    for f in bko-be hostid os-release; do
        [[ -e $GM/etc/$f || -L $GM/etc/$f ]] && try cp -a --preserve=all "$GM/etc/$f" "$T/real/guest/etc/$f"
    done
    touch -r "$GM/etc" "$T/real/guest/etc"; touch -r "$GM" "$T/real/guest"
else
    # The guest's dataset's own files (not those of the datasets mounted
    # inside it: -x), its root's mode and owner (its times at the end).
    chmod --reference="${GM:?}" "$T"; chown --reference="$GM" "$T"
    mkdir -p "$T/real/guest"
    try cp -ax --preserve=all "$GM/." "$T/real/guest/"
    touch -r "$GM" "$T/real/guest"
fi
mkdir -p "$T/real/tmp-like"; chmod 1777 "$T/real/tmp-like"

# Ownership by ids without names (and a user namespace's shifted root, 100000); special modes.
touch "$T/no-such-user"; chown 1234:5678 "$T/no-such-user"
touch "$T/ct-root"; chown 100000:100000 "$T/ct-root"
install -m 0644 /dev/null "$T/setuid"; chown 1234:5678 "$T/setuid"; chmod 4755 "$T/setuid"
install -m 0644 /dev/null "$T/setgid"; chmod 2755 "$T/setgid"
mkdir -p "$T/sticky-dir"; chmod 1777 "$T/sticky-dir"
mkdir -p "$T/setgid-dir"; chmod 2775 "$T/setgid-dir"; chown 0:5678 "$T/setgid-dir"
install -m 7777 /dev/null "$T/all-special"; chown 4294967294:4294967294 "$T/all-special" 2>/dev/null || skip "chown 4294967294"

# POSIX ACLs: access (user, group, mask), access + default on a directory
# (where the dataset has them: acltype=posix).
if ((HASACL)); then
    touch "$T/acl-file"; acl "$T/acl-file" system.posix_acl_access 1:6:$N 2:7:1234 4:4:$N 8:4:5678 16:7:$N 32:4:$N
    mkdir -p "$T/acl-dir"
    acl "$T/acl-dir" system.posix_acl_access 1:7:$N 2:5:1234 4:5:$N 16:5:$N 32:5:$N
    acl "$T/acl-dir" system.posix_acl_default 1:7:$N 2:7:1234 4:5:$N 8:5:5678 16:7:$N 32:0:$N
    echo inherited >"$T/acl-dir/file"; mkdir -p "$T/acl-dir/sub"
fi

# Extended attributes: user (binary, large, huge, many), trusted, security,
# on files, dirs and symlinks (trusted only: Linux keeps user.* off
# symlinks).  With xattr=sa a large one spills to a directory xattr.
touch "$T/xattr-file"
try setfattr -n user.backotter -v hello "$T/xattr-file"
try setfattr -n user.binary -v 0x00ff10fe7f00 "$T/xattr-file"
try setfattr -n user.empty -v "" "$T/xattr-file"
try setfattr -n trusted.backotter -v root-only "$T/xattr-file"
try setfattr -n security.bko -v sec-value "$T/xattr-file"
for i in $(seq 1 40); do setfattr -n "user.many$i" -v "v$i" "$T/xattr-file" 2>/dev/null || skip "user.many$i"; done
touch "$T/xattr-large"; try setfattr -n user.large -v "$(head -c 3000 /dev/zero | tr '\0' x)" "$T/xattr-large"
touch "$T/xattr-huge"; try setfattr -n user.huge -v "$(head -c 60000 /dev/zero | tr '\0' y)" "$T/xattr-huge"
mkdir -p "$T/xattr-dir"; try setfattr -n user.dir -v d "$T/xattr-dir"; try setfattr -n trusted.dir -v t "$T/xattr-dir"
ln -sfn xattr-file "$T/xattr-symlink"; try setfattr -h -n trusted.link -v l "$T/xattr-symlink"

# Capabilities: v2 effective, a v3 one with a rootid (user namespace, as a
# container's files have), permitted only.
cp /usr/bin/true "$T/cap-binary"; try setcap cap_net_raw,cap_net_admin+ep "$T/cap-binary"
cp /usr/bin/true "$T/cap-v3"; try setcap -n 100000 cap_net_raw+ep "$T/cap-v3"
cp /usr/bin/true "$T/cap-p-only"; try setcap cap_sys_admin+p "$T/cap-p-only"

# Hard links (across directories), symlinks (dangling, own times).
echo linked >"$T/hardlink-a"; ln -f "$T/hardlink-a" "$T/hardlink-b"
mkdir -p "$T/sub"; ln -f "$T/hardlink-a" "$T/sub/hardlink-c"
ln -sfn hardlink-a "$T/symlink"; ln -sfn /nonexistent/target "$T/symlink-dangling"

# Device nodes (rdev 0, big numbers), FIFO, a socket (neither tool keeps it).
mknod "$T/chardev" c 1 3; mknod "$T/blockdev" b 7 0; mknod "$T/chardev-rdev0" c 0 0
mknod "$T/chardev-big" c 511 1048575 2>/dev/null || mknod "$T/chardev-big" c 259 65536
mkfifo "$T/fifo"
python3 -c 'import socket, sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])' "$T/socket"

# Sparse files and allocation (with compression=lz4, ZFS makes holes of
# runs of zeros too).
# (1 GiB on the main tree, 128 MiB on each other dataset: fsmeta reads every hole.)
if [[ $ROLE == main ]]; then sz=1G mid=512; else sz=128M mid=64; fi
truncate -s $sz "$T/sparse"
echo start | dd of="$T/sparse" conv=notrunc status=none
echo middle | dd of="$T/sparse" bs=1M seek=$mid conv=notrunc status=none
# sparse-small: 256 KiB of data, a 256 KiB hole, 256 KiB of data, a file S3
# marks (fewer blocks than its size).  Written whole, the hole punched
# (aligned for blocks up to 64 KiB), synced, and checked.  Not by truncate
# between two appends: XFS keeps the speculative preallocation past EOF of
# a file appended to, and st_blocks counts it until it is trimmed, so
# neither tool marked the file and its hole came back filled.  The
# truncate way only where nothing punches holes (JFS, NILFS2).
sparse_small() {  # FILE HOLE-BYTES: true if S3 marks FILE
    { head -c 262144 /dev/urandom; head -c "$2" /dev/zero; head -c 262144 /dev/urandom; } >"$1"
    fallocate -p -o 262144 -l "$2" "$1" 2>/dev/null ||
        { head -c 262144 /dev/urandom >"$1"; truncate -s +"$2" "$1"; head -c 262144 /dev/urandom >>"$1"; }
    sync "$1"
    (( $(stat -c %b "$1") * 512 < $(stat -c %s "$1") ))
}
sparse_small "$T/sparse-small" 262144 || skip "sparse-small's hole ($(stat -c '%b blocks, size %s' "$T/sparse-small"))"
head -c 4096 /dev/urandom >"$T/sparse-tail"; truncate -s 4099 "$T/sparse-tail"
truncate -s 64M "$T/sparse-allhole"
truncate -s 8M "$T/sparse-head"; echo end >>"$T/sparse-head"
fallocate -l 16M "$T/preallocated" || skip fallocate
head -c 8M /dev/zero >"$T/zeros"
head -c 300K /dev/urandom >"$T/random"

# Names: spaces, newline, not UTF-8, 255 bytes, deep, empty.
touch "$T/name with spaces" "$T/new"$'\n'"line" "$T/"$'\xff\xfe'"-not-utf8" "$T/$(printf 'n%.0s' {1..255})" || skip "odd names"
mkdir -p "$T/"$'\xe9'"-latin1-dir" && touch "$T/"$'\xe9'"-latin1-dir/f" || skip "latin1 dir"
D="$T/deep$(printf '/level%s' {1..40})"; mkdir -p "$D"; touch "$D/bottom"
touch "$T/empty"; mkdir -p "$T/empty-dir"

# Project IDs (project_quota) and inheritance (P).
echo project >"$T/projid-file"; try chattr -p 42 "$T/projid-file"
mkdir -p "$T/projid-dir"; try chattr +P -p 43 "$T/projid-dir"; echo in >"$T/projid-dir/file"
echo big >"$T/projid-big"; try chattr -p 4000000000 "$T/projid-big"

# Times: far past, far future, nanoseconds, atime apart from mtime.
touch -d '1970-01-02 00:00:00.123456789' "$T/old"
touch -d '2100-01-01 12:00:00.987654321' "$T/future"
touch -h -d '1960-05-06 07:08:09.5' "$T/symlink-dangling" 2>/dev/null; touch -d '1901-12-14 00:00:00' "$T/pre-epoch" 2>/dev/null || touch "$T/pre-epoch"
echo atime >"$T/atime"; mkdir -p "$T/atime-dir"; ln -sfn atime "$T/atime-symlink"
for p in "$T/atime" "$T/atime-dir" "$T/atime-symlink"; do
    touch -h -m -d '2002-03-04 05:06:07.111111111' "$p"; touch -h -a -d '2003-04-05 06:07:08.222222222' "$p"
done

# ZFS's DOS attributes (what Windows sees over SMB, kept by both tools as
# linux.zfs_dos_flags): after the contents (a write sets archive again),
# archive given along where it should stay.
archive=0x800000000
echo hidden >"$T/dos-hidden-system"; dosflags "$T/dos-hidden-system" $((0x600000000 | archive))
echo readonly >"$T/dos-readonly"; dosflags "$T/dos-readonly" $((0x100000000 | archive))
echo written >"$T/dos-noarchive"; dosflags "$T/dos-noarchive" 0
echo nounlink >"$T/dos-nounlink"; dosflags "$T/dos-nounlink" $((0x2000000000 | archive))
echo offline >"$T/dos-offline-sparse"; dosflags "$T/dos-offline-sparse" $((0x300000000000 | archive))
mkdir -p "$T/dos-hidden-dir"; dosflags "$T/dos-hidden-dir" $((0x200000000 | archive))
echo fixed >"$T/dos-hidden-immutable"; dosflags "$T/dos-hidden-immutable" $((0x200000000 | archive))

# Inode flags: OpenZFS on Linux keeps immutable, append-only, nodump and
# project inheritance (i a d P); sync and noatime (S, A) are tried and
# named (zfs-refused/zfs-took).  Last: an immutable file can't change
# afterwards.
touch "$T/sync"; zflag S "$T/sync"
touch "$T/noatime"; zflag A "$T/noatime"
touch "$T/nodump"; flag d "$T/nodump"
mkdir -p "$T/nodump-dir"; flag d "$T/nodump-dir"
echo append >"$T/append-only"; flag a "$T/append-only"
echo fixed >"$T/immutable"; flag i "$T/immutable"
echo both >"$T/immutable-nodump"; chattr +d "$T/immutable-nodump"; flag i "$T/immutable-nodump"
mkdir -p "$T/immutable-dir"; echo in >"$T/immutable-dir/f"; flag i "$T/immutable-dir"
flag i "$T/dos-hidden-immutable"
echo fixed2 >"$T/real/immutable-linked"; ln "$T/real/immutable-linked" "$T/immutable-linked2"; flag i "$T/immutable-linked2"
if [[ $ROLE == main ]]; then touch -d '2001-02-03 04:05:06' "$T"; else touch -r "$GM" "$T"; fi

# The directories above (the main tree): owners without names, odd modes,
# xattrs, an ACL.
if [[ $ROLE == main ]]; then
    chown 1234:5678 "$U1"; chmod 0710 "$U1"
    try setfattr -n user.above -v up1 "$U1"
    ((HASACL)) && acl "$U1" system.posix_acl_access 1:7:$N 2:5:4321 4:1:$N 16:5:$N 32:0:$N
    chmod 1755 "$U2"; try setfattr -n trusted.above -v up2 "$U2"
    touch -d '1999-09-09 09:09:09.123' "$U2"; touch -d '1998-08-08 08:08:08.456' "$U1"
fi
exit 0
