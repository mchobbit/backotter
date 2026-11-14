#!/bin/bash
# The fixture for proxmox9 and proxmox9-mirror, one ZFS dataset at a time
# (AppArmor, no SELinux: the guest keeps no labels on disk):
#   fixture.sh ROOT main     the main tree, under ROOT/up1/up2/tree (ROOT: a
#                            fresh dataset made like the root dataset
#                            rpool/ROOT/pve-1, in a scratch pool made like
#                            rpool): the directories above, every checklist
#                            feature ZFS has on Linux (xattrs, POSIX ACLs,
#                            capabilities, i a d, project IDs and P, ZFS's
#                            DOS attributes, a 60000-byte xattr...), and the
#                            guest's own files from / (set-id, shadow,
#                            hostid, proxmox-boot-tool's /etc/kernel files,
#                            pmxcfs's config.db, apt's sources, dpkg's
#                            entries of the boot stack, systemd's links,
#                            random-seed, the journal, /boot's kernel)
#   fixture.sh DIR ds GM     on another dataset made like the guest's
#                            dataset mounted at GM (/rpool, /rpool/ROOT,
#                            /rpool/data, a CT's subvol-N-disk-M, /var/lib/vz),
#                            mounted inside the tree as the guest mounts it:
#                            the features again (ACLs only where the
#                            dataset has acltype=posix, as GM's), GM's own
#                            mode, owner and times, and GM's own files (the
#                            CT's files owned by 100000, vz's directories)
# What can't be made is reported on stderr as "fixture: skipped: ..." (the
# judge fails on any); what ZFS on Linux refuses by design as "fixture:
# zfs-refused: ..." and a dataset without ACLs as "fixture: noacl: ..." (the
# judge's LIMIT lines).  setfattr and setfacl are bkoshim.py on this guest.
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
    # Set-id binaries (Debian's ping is neither: it uses ping_group_range;
    # dbus's helper is setuid root, group messagebus, 4754), shadow, the
    # host id zfs-initramfs imports rpool with, proxmox-boot-tool's
    # /etc/kernel files, pmxcfs's database with its WAL (0600), apt's
    # sources (deb822, the enterprise ones disabled), dpkg's database
    # (status, and the boot stack's info files), systemd's links,
    # random-seed (user.random-seed-creditable), the journal's directories
    # with their ACLs and set-gid, /etc/zfs, /boot's running kernel.
    for f in /usr/bin/passwd /usr/bin/su /usr/bin/mount /usr/bin/newuidmap /usr/bin/chfn /usr/bin/fusermount3 \
             /usr/lib/openssh/ssh-keysign /usr/lib/dbus-1.0/dbus-daemon-launch-helper /usr/libexec/proxmox-mail-forward \
             /etc/shadow /etc/hostid /etc/hostname /etc/hosts /etc/fstab /etc/kernel/cmdline /etc/kernel/proxmox-boot-uuids \
             /var/lib/dpkg/status /var/lib/systemd/random-seed; do
        [[ -e $f ]] && try cp -a --preserve=all "$f" "$T/real/$(basename "$f")"
    done
    [[ -d /var/lib/pve-cluster ]] && try cp -a --preserve=all /var/lib/pve-cluster "$T/real/pve-cluster"
    [[ -d /etc/apt/sources.list.d ]] && try cp -a --preserve=all /etc/apt/sources.list.d "$T/real/apt-sources"
    [[ -d /etc/zfs ]] && try cp -a --preserve=all /etc/zfs "$T/real/etc-zfs"
    mkdir -p "$T/real/dpkg-info"
    for p in proxmox-kernel-helper proxmox-default-kernel proxmox-ve pve-manager zfsutils-linux zfs-initramfs; do
        for f in /var/lib/dpkg/info/$p.* /var/lib/dpkg/info/$p:amd64.*; do
            [[ -e $f ]] && try cp -a --preserve=all "$f" "$T/real/dpkg-info/"
        done
    done
    [[ -n $(ls "$T/real/dpkg-info") ]] || skip "no /var/lib/dpkg/info entries copied"
    [[ -d /etc/systemd/system ]] && try cp -a --preserve=all /etc/systemd/system "$T/real/systemd-system"
    [[ -d /var/log/journal ]] && try cp -a --preserve=all /var/log/journal "$T/real/journal"
    # The running kernel and its config and map (not its initrd: rebuilt
    # by load, and a large compressed file says nothing more).
    mkdir -p "$T/real/boot"
    for f in /boot/vmlinuz-$(uname -r) /boot/config-$(uname -r) /boot/System.map-$(uname -r); do
        [[ -f $f ]] && try cp -a --preserve=all "$f" "$T/real/boot/"
    done
    touch -r /boot "$T/real/boot"
else
    # The guest's dataset's own files (not those of the datasets mounted
    # inside it: -x), its root's mode and owner (its times at the end).
    chmod --reference="${GM:?}" "$T"; chown --reference="$GM" "$T"
    mkdir -p "$T/real/guest"
    try cp -ax --preserve=all "$GM/." "$T/real/guest/"
    touch -r "$GM" "$T/real/guest"
fi
mkdir -p "$T/real/tmp-like"; chmod 1777 "$T/real/tmp-like"

# Ownership by ids without names (and a CT's shifted root, 100000); special modes.
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
# CT's files have), permitted only.
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

# Sparse files and allocation (with compression=on, ZFS makes holes of
# runs of zeros too).
# (1 GiB on the main tree, 128 MiB on each other dataset: fsmeta reads every hole.)
if [[ $ROLE == main ]]; then sz=1G mid=512; else sz=128M mid=64; fi
truncate -s $sz "$T/sparse"
echo start | dd of="$T/sparse" conv=notrunc status=none
echo middle | dd of="$T/sparse" bs=1M seek=$mid conv=notrunc status=none
head -c 256K /dev/urandom >"$T/sparse-small"; truncate -s +256K "$T/sparse-small"; head -c 256K /dev/urandom >>"$T/sparse-small"
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
