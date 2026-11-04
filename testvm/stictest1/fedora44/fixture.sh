#!/bin/bash
# The fixture for fedora44 (btrfs / with compress=zstd:1 and subvolumes,
# ext4 /boot, SELinux enforcing): under $1 (a fresh file system of type $2,
# btrfs or ext4), up1/up2 (the directories above the backed-up path) and
# up1/up2/tree.
set -u
R=$1 FS=$2; U1=$R/up1; U2=$U1/up2; T=$U2/tree
mkdir -p "$T"
# The source file system's root gets /mnt's label, as a mounted root has on a
# labeled system (a fresh root has none).
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
# p-only, several caps), setuid/setgid binaries, shadow (mode 0, shadow_t),
# random-seed's user xattr, librepo's user xattrs, the tpm2-tss keystore
# (setgid dir, default ACL), the journal directory (btrfs: nocow C,
# setgid, access + default ACLs, journald's user.crtime_usec); on ext4,
# /boot's GRUB and BLS entries.
mkdir -p "$T/real"
for f in /usr/bin/newuidmap /usr/bin/newgidmap /usr/bin/arping /usr/bin/clockdiff /usr/bin/mtr-packet \
         /usr/libexec/sssd/krb5_child /usr/bin/passwd /usr/bin/su /usr/bin/crontab /usr/bin/pkexec /etc/shadow \
         /etc/hostname /var/lib/systemd/random-seed /usr/bin/unix_chkpwd /etc/selinux/config; do
    try cp -a --preserve=all "$f" "$T/real/$(basename "$f")"
done
f=$(getfattr -R -m user.librepo --absolute-names /var/cache 2>/dev/null | sed -n 's/^# file: //p' | head -1)
[[ -n $f ]] && try cp -a --preserve=all "$f" "$T/real/librepo-file"
try cp -a --preserve=all /var/lib/tpm2-tss/system/keystore "$T/real/keystore"
J=$T/real/journal; mkdir -p "$J"
[[ $FS == btrfs ]] && flag C "$J"
getfacl -p /var/log/journal 2>/dev/null | setfacl --set-file=- "$J" || skip "acl journal"
chmod --reference=/var/log/journal "$J"; chown --reference=/var/log/journal "$J"; try chcon --reference=/var/log/journal "$J"
for jd in /var/log/journal/*/; do
    n=$(basename "$jd"); mkdir -p "$J/$n"   # inherits C and the default ACL
    for jf in "$jd"system.journal; do try cp -a --preserve=all "$jf" "$J/$n/"; done
    chmod --reference="$jd" "$J/$n"; chown --reference="$jd" "$J/$n"; try chcon --reference="$jd" "$J/$n"
    getfacl -p "$jd" 2>/dev/null | setfacl --set-file=- "$J/$n"
done
if [[ $FS == ext4 ]]; then
    try cp -a --preserve=all /boot/grub2 "$T/real/grub2"
    try cp -a --preserve=all /boot/loader "$T/real/loader"
    try cp -a --preserve=all "$(ls /boot/config-* | head -1)" "$T/real/"
fi
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
mkfifo "$T/fifo"; try chcon -t var_run_t "$T/fifo"
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

# Project IDs: neither btrfs nor an ext4 without the project feature (/boot)
# takes one; tried anyway (skipped where refused).
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

# btrfs per-file options: the compression property (btrfs.compression:
# zstd, lzo, zlib, "no"), on files and an inheriting directory; c and m
# flags; nocow (C) on a file made empty then written, and on a directory
# whose entries inherit it; a nested subvolume (restored as a directory).
if [[ $FS == btrfs ]]; then
    head -c 200K /dev/urandom >"$T/compress-zstd"; try btrfs property set "$T/compress-zstd" compression zstd
    yes compressible | head -c 300K >"$T/compress-lzo"; try btrfs property set "$T/compress-lzo" compression lzo
    yes compressible | head -c 300K >"$T/compress-zlib"; try btrfs property set "$T/compress-zlib" compression zlib
    yes compressible | head -c 300K >"$T/compress-no"; btrfs property set "$T/compress-no" compression no 2>/dev/null ||
        try btrfs property set "$T/compress-no" compression none
    mkdir -p "$T/compress-dir"; try btrfs property set "$T/compress-dir" compression zstd
    yes inherited | head -c 100K >"$T/compress-dir/file"
    yes flag | head -c 100K >"$T/compress-flag-c"; flag c "$T/compress-flag-c"
    yes flag | head -c 100K >"$T/compress-flag-m"; flag m "$T/compress-flag-m"
    touch "$T/nocow"; flag C "$T/nocow"; head -c 1M /dev/urandom >>"$T/nocow"
    mkdir -p "$T/nocow-dir"; flag C "$T/nocow-dir"; head -c 64K /dev/urandom >"$T/nocow-dir/inherits"
    btrfs subvolume create -q "$T/subvol" >/dev/null 2>&1 || btrfs subvolume create "$T/subvol" >/dev/null 2>&1 || skip "subvolume"
    echo in-subvolume >"$T/subvol/file"; chmod 0750 "$T/subvol"; chown 1234:5678 "$T/subvol"
    try setfattr -n user.subvol -v s "$T/subvol"; touch -d '2005-05-05 05:05:05.5' "$T/subvol"
fi
# ext4 inode flags beside the common ones: D (dirsync), T (top dir),
# j (data journaling).
if [[ $FS == ext4 ]]; then
    mkdir -p "$T/dirsync-dir" "$T/topdir"; flag D "$T/dirsync-dir"; flag T "$T/topdir"
    echo j >"$T/journal-data"; flag j "$T/journal-data"
fi
mkdir -p "$T/dirsync"; [[ $FS == btrfs ]] && flag D "$T/dirsync"

# Inode flags both have (chattr): sync, noatime, nodump, append, immutable;
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
