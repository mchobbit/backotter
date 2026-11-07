#!/bin/bash
# The fixture for rocky10-lvm (XFS, SELinux enforcing): under $1 (a fresh
# XFS on a logical volume), up1/up2 (the directories above the backed-up
# path) and up1/up2/tree, whose boot/ is the mount point of a /boot-like XFS
# partition (mounted by inner.sh): the guest's own /boot files go there.
# LVMREAL: a copy of the guest's /etc/lvm made before the scratch volume
# group touched it.
set -u
R=$1; U1=$R/up1; U2=$U1/up2; T=$U2/tree
mkdir -p "$T"
# The source file system's root gets /mnt's label, as a mounted root has on a
# labeled system (a fresh XFS root has none).
chcon --reference=/mnt "$R" 2>/dev/null || chcon -t mnt_t "$R" 2>/dev/null || echo "fixture: skipped: label of $R" >&2
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
N=0xffffffff
# Real examples from the guest's own files: labels, capabilities (v2 ep and
# p-only, several caps), ACL (tpm2-tss keystore: setgid dir, default ACL
# with XFS's trusted.SGI_ACL_DEFAULT twin), random-seed's user xattr,
# shadow (mode 0, shadow_t), setuid binaries, sticky dir.
mkdir -p "$T/real"
for f in /usr/bin/newuidmap /usr/bin/arping /usr/bin/clockdiff /usr/libexec/sssd/krb5_child \
         /usr/bin/passwd /usr/bin/su /usr/bin/crontab /etc/shadow /etc/hostname /var/lib/systemd/random-seed \
         /usr/sbin/unix_chkpwd /etc/selinux/config /usr/lib/systemd/systemd; do
    try cp -a --preserve=all "$f" "$T/real/$(basename "$f")"
done
try cp -a --preserve=all /var/lib/tpm2-tss/system/keystore "$T/real/keystore"
# From / on its logical volume: fstab, and LVM's own configuration and
# metadata (lvm_etc_t, lvm_metadata_t; 0700 dirs; the VG's backup and
# archive, the devices file and its backup), as the guest has them.
try cp -a --preserve=all /etc/fstab "$T/real/fstab"
if [[ -d ${LVMREAL:-} ]]; then try cp -a --preserve=all "$LVMREAL" "$T/real/etc-lvm"
else skip "no LVMREAL copy of /etc/lvm"; fi
# From /boot, on its own partition: the boot loader's files (grub2 0700,
# grubenv, i386-pc modules, fonts), BLS entries, the empty EFI tree, kernel
# configs, System.map (system_map_t), symvers, the running kernel, its
# .hmac and initramfs (0600, unconfined_u where the updates wrote them),
# then the mount point's own mode, label and times.
B=$T/boot
if mountpoint -q "$B"; then
    for f in /boot/grub2 /boot/loader /boot/efi /boot/config-* /boot/System.map-* /boot/symvers-* /boot/.vmlinuz-*.hmac \
             /boot/vmlinuz-$(uname -r) /boot/initramfs-$(uname -r).img; do
        try cp -a --preserve=all "$f" "$B/"
    done
    # A few of the checklist's features on this second file system too.
    echo linked >"$B/bko-hardlink-a"; ln -f "$B/bko-hardlink-a" "$B/grub2/bko-hardlink-b"
    truncate -s 64M "$B/bko-sparse"; echo mid | dd of="$B/bko-sparse" bs=1M seek=32 conv=notrunc status=none
    touch "$B/bko-xattr"; try setfattr -n user.backotter -v boot "$B/bko-xattr"; try setfattr -n trusted.boot -v t "$B/bko-xattr"
    echo nodump >"$B/bko-nodump"; flag d "$B/bko-nodump"
    echo p >"$B/bko-projid"; try chattr -p 44 "$B/bko-projid"
    touch "$B/bko-xfs-extsize"; try xfs_io -c "extsize 512k" "$B/bko-xfs-extsize"; head -c 64K /dev/urandom >>"$B/bko-xfs-extsize"
    chmod --reference=/boot "$B"; try chcon --reference=/boot "$B"; touch -r /boot "$B"
else skip "$B is no mount point"; fi
mkdir -p "$T/real/tmp-like"; chmod 1777 "$T/real/tmp-like"; try chcon -t tmp_t "$T/real/tmp-like"

# Ownership by ids without names; special modes.
touch "$T/no-such-user"; chown 1234:5678 "$T/no-such-user"
install -m 0644 /dev/null "$T/setuid"; chown 1234:5678 "$T/setuid"; chmod 4755 "$T/setuid"
install -m 0644 /dev/null "$T/setgid"; chmod 2755 "$T/setgid"
mkdir -p "$T/sticky-dir"; chmod 1777 "$T/sticky-dir"
mkdir -p "$T/setgid-dir"; chmod 2775 "$T/setgid-dir"; chown 0:5678 "$T/setgid-dir"
install -m 7777 /dev/null "$T/all-special"; chown 4294967294:4294967294 "$T/all-special" 2>/dev/null || skip "chown 4294967294"

# POSIX ACLs: access (user, group, mask), access + default on a directory.
touch "$T/acl-file"; acl "$T/acl-file" system.posix_acl_access 1:6:$N 2:7:1234 4:4:$N 8:4:5678 16:7:$N 32:4:$N
mkdir -p "$T/acl-dir"
acl "$T/acl-dir" system.posix_acl_access 1:7:$N 2:5:1234 4:5:$N 16:5:$N 32:5:$N
acl "$T/acl-dir" system.posix_acl_default 1:7:$N 2:7:1234 4:5:$N 8:5:5678 16:7:$N 32:0:$N
echo inherited >"$T/acl-dir/file"; mkdir -p "$T/acl-dir/sub"

# Extended attributes: user (binary, large, many), trusted, security
# beside SELinux's, on files, dirs and symlinks (trusted only: Linux keeps
# user.* off symlinks).
touch "$T/xattr-file"
try setfattr -n user.backotter -v hello "$T/xattr-file"
try setfattr -n user.binary -v 0x00ff10fe7f00 "$T/xattr-file"
try setfattr -n user.large -v "$(head -c 3000 /dev/zero | tr '\0' x)" "$T/xattr-file"
try setfattr -n user.huge -v "$(head -c 60000 /dev/zero | tr '\0' y)" "$T/xattr-file"
try setfattr -n user.empty -v "" "$T/xattr-file"
try setfattr -n trusted.backotter -v root-only "$T/xattr-file"
try setfattr -n security.bko -v sec-value "$T/xattr-file"
for i in $(seq 1 40); do setfattr -n "user.many$i" -v "v$i" "$T/xattr-file" 2>/dev/null; done
mkdir -p "$T/xattr-dir"; try setfattr -n user.dir -v d "$T/xattr-dir"; try setfattr -n trusted.dir -v t "$T/xattr-dir"
ln -sfn xattr-file "$T/xattr-symlink"; try setfattr -h -n trusted.link -v l "$T/xattr-symlink"

# Capabilities: v2 effective, a v3 one with a rootid (user namespace).
cp /usr/bin/true "$T/cap-binary"; try setcap cap_net_raw,cap_net_admin+ep "$T/cap-binary"
cp /usr/bin/true "$T/cap-v3"; try setcap -n 1234 cap_net_raw+ep "$T/cap-v3"

# SELinux labels: types, MLS levels and categories, on files, dirs,
# symlinks, devices, a FIFO.
touch "$T/label-httpd"; try chcon -t httpd_sys_content_t "$T/label-httpd"
touch "$T/label-mls"; try chcon -l s0:c1,c42 "$T/label-mls"
touch "$T/label-range"; try chcon -u system_u -t etc_t -l s0-s0:c0.c1023 "$T/label-range"
mkdir -p "$T/label-dir"; try chcon -t var_log_t "$T/label-dir"
ln -sfn label-httpd "$T/label-symlink"; try chcon -h -t bin_t "$T/label-symlink"

# Hard links (across directories), symlinks (dangling, own times).
echo linked >"$T/hardlink-a"; ln -f "$T/hardlink-a" "$T/hardlink-b"
mkdir -p "$T/sub"; ln -f "$T/hardlink-a" "$T/sub/hardlink-c"
ln -sfn hardlink-a "$T/symlink"; ln -sfn /nonexistent/target "$T/symlink-dangling"

# Device nodes (rdev 0, big numbers), FIFO, a socket (neither tool keeps it).
mknod "$T/chardev" c 1 3; mknod "$T/blockdev" b 7 0; mknod "$T/chardev-rdev0" c 0 0
mknod "$T/chardev-big" c 511 1048575 2>/dev/null || mknod "$T/chardev-big" c 259 65536
try chcon -t null_device_t "$T/chardev"
mkfifo "$T/fifo"; try chcon -t user_tmp_t "$T/fifo"
python3 -c 'import socket, sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])' "$T/socket"

# Sparse files and allocation.
truncate -s 1G "$T/sparse"
echo start | dd of="$T/sparse" conv=notrunc status=none
echo middle | dd of="$T/sparse" bs=1M seek=512 conv=notrunc status=none
head -c 256K /dev/urandom >"$T/sparse-small"; truncate -s +8K "$T/sparse-small"; head -c 256K /dev/urandom >>"$T/sparse-small"
head -c 4096 /dev/urandom >"$T/sparse-tail"; truncate -s 4099 "$T/sparse-tail"
truncate -s 64M "$T/sparse-allhole"
truncate -s 8M "$T/sparse-head"; echo end >>"$T/sparse-head"
fallocate -l 16M "$T/preallocated" || skip fallocate
head -c 8M /dev/zero >"$T/zeros"
fallocate -l 32M "$T/swapfile" && chmod 600 "$T/swapfile" && try mkswap -q "$T/swapfile"
head -c 300K /dev/urandom >"$T/random"

# Names: spaces, newline, not UTF-8, 255 bytes, deep, empty.
touch "$T/name with spaces" "$T/new"$'\n'"line" "$T/"$'\xff\xfe'"-not-utf8" "$T/$(printf 'n%.0s' {1..255})"
mkdir -p "$T/"$'\xe9'"-latin1-dir"; touch "$T/"$'\xe9'"-latin1-dir/f"
D="$T/deep$(printf '/level%s' {1..40})"; mkdir -p "$D"; touch "$D/bottom"
touch "$T/empty"; mkdir -p "$T/empty-dir"

# Project IDs (XFS, projid32bit) and inheritance.
echo project >"$T/projid-file"; try chattr -p 42 "$T/projid-file"
mkdir -p "$T/projid-dir"; try chattr +P -p 43 "$T/projid-dir"; echo in >"$T/projid-dir/file"
echo big >"$T/projid-big"; try chattr -p 4000000000 "$T/projid-big"

# XFS's own fsxattr flags beside chattr's: extent size hints (on a file,
# and on a directory, which its new files inherit), nodefrag, filestream
# (xfs_io; neither tool claims those).
touch "$T/xfs-extsize"; try xfs_io -c "extsize 1m" "$T/xfs-extsize"; head -c 64K /dev/urandom >>"$T/xfs-extsize"
mkdir -p "$T/xfs-extsize-dir"; try xfs_io -c "extsize 2m" "$T/xfs-extsize-dir"; head -c 64K /dev/urandom >"$T/xfs-extsize-dir/inherits"
echo n >"$T/xfs-nodefrag"; try xfs_io -c "chattr +f" "$T/xfs-nodefrag"
mkdir -p "$T/xfs-filestream"; try xfs_io -c "chattr +S" "$T/xfs-filestream"

# Times: far past, far future, nanoseconds, atime apart from mtime.
touch -d '1970-01-02 00:00:00.123456789' "$T/old"
touch -d '2100-01-01 12:00:00.987654321' "$T/future"
touch -h -d '1960-05-06 07:08:09.5' "$T/symlink-dangling" 2>/dev/null; touch -d '1901-12-14 00:00:00' "$T/pre-epoch" 2>/dev/null || touch "$T/pre-epoch"
echo atime >"$T/atime"; mkdir -p "$T/atime-dir"; ln -sfn atime "$T/atime-symlink"
for p in "$T/atime" "$T/atime-dir" "$T/atime-symlink"; do
    touch -h -m -d '2002-03-04 05:06:07.111111111' "$p"; touch -h -a -d '2003-04-05 06:07:08.222222222' "$p"
done

# Inode flags XFS has (chattr): sync, noatime, nodump, append, immutable;
# on a directory too.  Last: an immutable file can't change afterwards.
touch "$T/sync"; flag S "$T/sync"
touch "$T/noatime"; flag A "$T/noatime"
touch "$T/nodump"; flag d "$T/nodump"
mkdir -p "$T/nodump-dir"; flag d "$T/nodump-dir"
echo append >"$T/append-only"; flag a "$T/append-only"
echo fixed >"$T/immutable"; flag i "$T/immutable"
echo both >"$T/immutable-nodump"; chattr +d "$T/immutable-nodump"; flag i "$T/immutable-nodump"
mkdir -p "$T/immutable-dir"; echo in >"$T/immutable-dir/f"; flag i "$T/immutable-dir"
echo fixed2 >"$T/real/immutable-linked"; ln "$T/real/immutable-linked" "$T/immutable-linked2"; flag i "$T/immutable-linked2"
touch -d '2001-02-03 04:05:06' "$T"

# The directories above: owners without names, odd modes, labels, xattrs, ACL.
chown 1234:5678 "$U1"; chmod 0710 "$U1"; try chcon -t httpd_sys_content_t -l s0:c3 "$U1"
try setfattr -n user.above -v up1 "$U1"
acl "$U1" system.posix_acl_access 1:7:$N 2:5:4321 4:1:$N 16:5:$N 32:0:$N
chmod 1755 "$U2"; try setfattr -n trusted.above -v up2 "$U2"; try chcon -t var_t "$U2"
touch -d '1999-09-09 09:09:09.123' "$U2"; touch -d '1998-08-08 08:08:08.456' "$U1"
exit 0
