#!/bin/bash
# The fixture for tumbleweed (btrfs / with subvolumes, ext4 /boot, SELinux
# enforcing): under $1 (a fresh btrfs or ext4), up1/up2 (the directories
# above the backed-up path) and up1/up2/tree.  $2: the file system type.
# The guest has no attr, acl or libcap tools: setfattr, setfacl and setcapraw
# are bkoshim.py (python3, os.setxattr).
set -u
R=$1 FS=$2; U1=$R/up1; U2=$U1/up2; T=$U2/tree
mkdir -p "$T"
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
# Real examples from the guest's own files: labels, capabilities
# (newuidmap/newgidmap), setuid binaries, shadow (mode 0, shadow_t),
# random-seed's user xattr, the journal (setgid dir, nocow C inherited from
# /var/log's like, user.crtime_usec), /var/lib's nocow dirs.
mkdir -p "$T/real"
for f in /usr/bin/newuidmap /usr/bin/newgidmap /usr/bin/passwd /usr/bin/su /usr/bin/mount /usr/bin/fusermount3 \
         /usr/sbin/unix_chkpwd /etc/shadow /etc/hostname /var/lib/systemd/random-seed /etc/selinux/config \
         /usr/lib/systemd/systemd /boot/grub2/grubenv; do
    try cp -a --preserve=all "$f" "$T/real/$(basename "$f")"
done
mkdir -p "$T/real/var-log" "$T/real/var-lib"
if [[ $FS == btrfs ]]; then flag C "$T/real/var-log"; flag C "$T/real/var-lib"; fi
# The journal: only its directory and the newest journal file (its size is
# what the guest made; it gets C from its directory here, as in the guest).
J=$(ls -d /var/log/journal/*/ 2>/dev/null | head -1)
if [[ -n $J ]]; then
    JD=$T/real/var-log/journal/$(basename "$J")
    mkdir -p "$JD"
    cp -a --preserve=all "$J/system.journal" "$JD/" 2>/dev/null || skip "journal file"
    for p in "$JD:$J" "${JD%/*}:/var/log/journal"; do
        chown --reference="${p#*:}" "${p%%:*}"; chmod --reference="${p#*:}" "${p%%:*}"
        chcon --reference="${p#*:}" "${p%%:*}" 2>/dev/null; touch --reference="${p#*:}" "${p%%:*}"
    done
fi
try cp -a --preserve=all /var/lib/chrony "$T/real/var-lib/chrony"
try cp -a --preserve=all /var/lib/dbus "$T/real/var-lib/dbus"
mkdir -p "$T/real/tmp-like"; chmod 1777 "$T/real/tmp-like"; try chcon -t tmp_t "$T/real/tmp-like"

# Ownership by ids without names; special modes.
touch "$T/no-such-user"; chown 1234:5678 "$T/no-such-user"
install -m 0644 /dev/null "$T/setuid"; chown 1234:5678 "$T/setuid"; chmod 4755 "$T/setuid"
install -m 0644 /dev/null "$T/setgid"; chmod 2755 "$T/setgid"
mkdir -p "$T/sticky-dir"; chmod 1777 "$T/sticky-dir"
mkdir -p "$T/setgid-dir"; chmod 2775 "$T/setgid-dir"; chown 0:5678 "$T/setgid-dir"
install -m 7777 /dev/null "$T/all-special"; chown 4294967294:4294967294 "$T/all-special" 2>/dev/null || skip "chown 4294967294"
install -m 0000 /dev/null "$T/mode-0"; echo x >"$T/mode-0"

# POSIX ACLs: access (user, group, mask), access + default on a directory.
touch "$T/acl-file"; acl "$T/acl-file" system.posix_acl_access 1:6:$N 2:7:1234 4:4:$N 8:4:5678 16:7:$N 32:4:$N
mkdir -p "$T/acl-dir"
acl "$T/acl-dir" system.posix_acl_access 1:7:$N 2:5:1234 4:5:$N 16:5:$N 32:5:$N
acl "$T/acl-dir" system.posix_acl_default 1:7:$N 2:7:1234 4:5:$N 8:5:5678 16:7:$N 32:0:$N
echo inherited >"$T/acl-dir/file"; mkdir -p "$T/acl-dir/sub"
mkdir -p "$T/acl-default-only"; acl "$T/acl-default-only" system.posix_acl_default 1:7:$N 4:5:$N 32:5:$N

# Extended attributes: user (binary, large, many), trusted, security beside
# SELinux's, on files, dirs and symlinks (trusted only: Linux keeps user.*
# off symlinks).  btrfs takes values up to about a node (16K), ext4 a block
# for all of a file's attributes together.
touch "$T/xattr-file"
try setfattr -n user.backotter -v hello "$T/xattr-file"
try setfattr -n user.binary -v 0x00ff10fe7f00 "$T/xattr-file"
try setfattr -n user.empty -v "" "$T/xattr-file"
try setfattr -n trusted.backotter -v root-only "$T/xattr-file"
try setfattr -n security.bko -v sec-value "$T/xattr-file"
for i in $(seq 1 40); do setfattr -n "user.many$i" -v "v$i" "$T/xattr-file" 2>/dev/null; done
touch "$T/xattr-large"; try setfattr -n user.large -v "$(head -c 3000 /dev/zero | tr '\0' x)" "$T/xattr-large"
touch "$T/xattr-huge"
setfattr -n user.huge -v "$(head -c 15000 /dev/zero | tr '\0' y)" "$T/xattr-huge" 2>/dev/null ||
    setfattr -n user.huge -v "$(head -c 3800 /dev/zero | tr '\0' y)" "$T/xattr-huge" 2>/dev/null || skip "user.huge"
mkdir -p "$T/xattr-dir"; try setfattr -n user.dir -v d "$T/xattr-dir"; try setfattr -n trusted.dir -v t "$T/xattr-dir"
ln -sfn xattr-file "$T/xattr-symlink"; try setfattr -h -n trusted.link -v l "$T/xattr-symlink"
# systemd-journald's own attribute, as on the guest's journal files.
echo j >"$T/xattr-crtime"; try setfattr -n user.crtime_usec -v 0x40e2c5b1a0240600 "$T/xattr-crtime"

# Capabilities: v2 effective (as newuidmap's), permitted only, a v3 one with
# a rootid (user namespace).  CAP_NET_ADMIN 12, CAP_NET_RAW 13, CAP_SETUID 7,
# CAP_BPF 39.
cp /usr/bin/true "$T/cap-binary"; try setcapraw "$T/cap-binary" v2 12,13
cp /usr/bin/true "$T/cap-permitted"; try setcapraw "$T/cap-permitted" p 7,39
cp /usr/bin/true "$T/cap-v3"; try setcapraw "$T/cap-v3" v3 13 1234

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
mkfifo "$T/fifo"; try chcon -t tmp_t -l s0:c7 "$T/fifo"
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

# Project IDs: neither btrfs nor /boot's ext4 (no project feature) takes one;
# tried, and reported as skipped.
echo project >"$T/projid-file"; try chattr -p 42 "$T/projid-file"

# Times: far past, far future, nanoseconds, atime apart from mtime.
touch -d '1970-01-02 00:00:00.123456789' "$T/old"
touch -d '2100-01-01 12:00:00.987654321' "$T/future"
touch -h -d '1960-05-06 07:08:09.5' "$T/symlink-dangling" 2>/dev/null; touch -d '1901-12-14 00:00:00' "$T/pre-epoch" 2>/dev/null || touch "$T/pre-epoch"
echo atime >"$T/atime"; mkdir -p "$T/atime-dir"; ln -sfn atime "$T/atime-symlink"
for p in "$T/atime" "$T/atime-dir" "$T/atime-symlink"; do
    touch -h -m -d '2002-03-04 05:06:07.111111111' "$p"; touch -h -a -d '2003-04-05 06:07:08.222222222' "$p"
done

if [[ $FS == btrfs ]]; then
    # btrfs's per-file options: the compression property (btrfs.compression)
    # of each kind, set on an empty file then written, inherited from a
    # directory; c (compress), m (nocompress), C (nocow: set while empty,
    # inherited from a directory), with immutable too.
    big() { yes "BackOtter compressible line $1" | head -c 2M; }
    for c in zstd lzo zlib; do
        touch "$T/compress-$c"; try setfattr -n btrfs.compression -v $c "$T/compress-$c"; big $c >"$T/compress-$c"
    done
    touch "$T/compress-zstd-level"; try setfattr -n btrfs.compression -v zstd:5 "$T/compress-zstd-level"; big lvl >"$T/compress-zstd-level"
    mkdir -p "$T/compress-dir"; try setfattr -n btrfs.compression -v zstd "$T/compress-dir"
    big inherit >"$T/compress-dir/inherited"; mkdir -p "$T/compress-dir/sub"
    touch "$T/compress-flag"; flag c "$T/compress-flag"; big flag >"$T/compress-flag"
    touch "$T/compress-immutable"; try setfattr -n btrfs.compression -v zstd "$T/compress-immutable"; big imm >"$T/compress-immutable"; flag i "$T/compress-immutable"
    touch "$T/nocompress"; flag m "$T/nocompress"; big nocomp >"$T/nocompress"
    touch "$T/nocow"; flag C "$T/nocow"; head -c 1M /dev/urandom >"$T/nocow"
    mkdir -p "$T/nocow-dir"; flag C "$T/nocow-dir"; head -c 64K /dev/urandom >"$T/nocow-dir/inherited"; mkdir -p "$T/nocow-dir/sub"
    touch "$T/nocow-append"; flag C "$T/nocow-append"; echo data >>"$T/nocow-append"; flag a "$T/nocow-append"
    touch "$T/nocow-sparse"; flag C "$T/nocow-sparse"; truncate -s 4M "$T/nocow-sparse"; echo end >>"$T/nocow-sparse"
    # A nested subvolume inside the backed-up tree (as /home, /var are
    # subvolumes in the guest): backed up across, restored as a directory.
    btrfs -q subvolume create "$T/subvol" >/dev/null 2>&1 || skip "btrfs subvolume create"
    echo in-subvol >"$T/subvol/file"; chmod 0750 "$T/subvol"; chown 1234:5678 "$T/subvol"
    try setfattr -n user.subvol -v sv "$T/subvol"; touch -d '2005-05-05 05:05:05.5' "$T/subvol"
fi

# Inode flags (chattr): sync, noatime, nodump, dirsync, append, immutable;
# on a directory too.  Last: an immutable file can't change afterwards.
touch "$T/sync"; flag S "$T/sync"
touch "$T/noatime"; flag A "$T/noatime"
touch "$T/nodump"; flag d "$T/nodump"
mkdir -p "$T/nodump-dir"; flag d "$T/nodump-dir"
mkdir -p "$T/dirsync-dir"; flag D "$T/dirsync-dir"
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
