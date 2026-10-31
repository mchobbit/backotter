#!/usr/bin/env bash
# Add hard-to-preserve files to a mounted guest, under ROOT/root/bko-torture,
# so a backup-and-restore round trip is tested on more than a fresh install
# happens to contain.
#
# Usage: sudo torture.sh ROOT
#
# Each kind is attempted on its own; what the filesystem refuses (some don't
# support every inode flag, for one) is reported and skipped.

set -uo pipefail

[[ $# -eq 1 && -d $1 ]] || { echo "usage: $0 ROOT" >&2; exit 1; }
T=$1/root/bko-torture
mkdir -p "$T"
try() { "$@" 2>/dev/null || echo "torture: skipped (unsupported here): $*" >&2; }

# Ownership by ids that have no user name, and the special permission bits.
touch "$T/no-such-user"
chown 1234:5678 "$T/no-such-user"
install -m 0644 /dev/null "$T/setuid"; chown 1234:5678 "$T/setuid"; chmod 4755 "$T/setuid"
install -m 0644 /dev/null "$T/setgid"; chmod 2755 "$T/setgid"
mkdir -p "$T/sticky-dir"; chmod 1777 "$T/sticky-dir"
mkdir -p "$T/setgid-dir"; chmod 2775 "$T/setgid-dir"

# POSIX ACLs, including a default ACL on a directory.
touch "$T/acl-file"
try setfacl -m u:1234:rwx,g:5678:r "$T/acl-file"
mkdir -p "$T/acl-dir"
try setfacl -m u:1234:rx "$T/acl-dir"
try setfacl -d -m u:1234:rwx,g:5678:rx "$T/acl-dir"

# Extended attributes: user, trusted, a binary value, a large value.
touch "$T/xattr-file"
try setfattr -n user.backotter -v "hello" "$T/xattr-file"
try setfattr -n user.binary -v 0x00ff10fe7f "$T/xattr-file"
try setfattr -n user.large -v "$(head -c 3000 /dev/zero | tr '\0' x)" "$T/xattr-file"
try setfattr -n trusted.backotter -v "root-only" "$T/xattr-file"

# A file capability, as on ping or newuidmap.
cp "$1/usr/bin/true" "$T/cap-binary" 2>/dev/null || touch "$T/cap-binary"
try setcap cap_net_raw,cap_net_admin+ep "$T/cap-binary"

# Hard links, one in another directory; symlinks, one dangling.
echo "linked" >"$T/hardlink-a"
ln -f "$T/hardlink-a" "$T/hardlink-b"
mkdir -p "$T/sub"
ln -f "$T/hardlink-a" "$T/sub/hardlink-c"
ln -sfn hardlink-a "$T/symlink"
ln -sfn /nonexistent/target "$T/symlink-dangling"

# Device nodes, a FIFO, a socket.
rm -f "$T/chardev" "$T/blockdev" "$T/fifo" "$T/socket"
mknod "$T/chardev" c 1 3
mknod "$T/blockdev" b 7 0
mkfifo "$T/fifo"
python3 -c 'import socket, sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])' "$T/socket"

# A 1 GiB sparse file with data at two places, and a 1 GiB file of random
# data (Mondo handled big files separately).
rm -f "$T/sparse"
truncate -s 1G "$T/sparse"
echo "start" | dd of="$T/sparse" conv=notrunc status=none
echo "middle" | dd of="$T/sparse" bs=1M seek=512 conv=notrunc status=none
head -c 1G /dev/urandom >"$T/big-random"

# Awkward names: spaces, a newline, bytes that aren't UTF-8, 250 characters,
# and a deep path.
touch "$T/name with spaces"
touch "$T/new"$'\n'"line"
touch "$T/"$'\xff\xfe'"-not-utf8"
touch "$T/$(printf 'n%.0s' {1..250})"
mkdir -p "$T/deep$(printf '/level%s' {1..40})"
touch "$T/deep$(printf '/level%s' {1..40})/bottom"

# Allocation: a preallocated file (unwritten extents), a file of written
# zeros and a swap file, none of them with holes.  A restore that punches
# holes into them breaks the swap file (swapon refuses one with holes).
rm -f "$T/preallocated" "$T/zeros" "$T/swapfile"
fallocate -l 16M "$T/preallocated" 2>/dev/null || echo "torture: skipped (unsupported here): fallocate" >&2
head -c 8M /dev/zero >"$T/zeros"
if fallocate -l 32M "$T/swapfile" 2>/dev/null; then
    chmod 600 "$T/swapfile"
    try mkswap -q "$T/swapfile"
fi

# Project IDs (project quotas), where the file system has them: XFS, ZFS,
# ext4 made with -O project.
echo "project" >"$T/projid-file"; try chattr -p 42 "$T/projid-file"
mkdir -p "$T/projid-dir"; try chattr +P -p 43 "$T/projid-dir"

# A case-insensitive directory (ext4 or f2fs made with -O casefold), which
# only takes the flag while empty.
mkdir -p "$T/casefold-dir"; try chattr +F "$T/casefold-dir"; touch "$T/casefold-dir/MixedCase"

# btrfs's compression property, which btrfs keeps as the btrfs.compression
# extended attribute.
touch "$T/compress-zstd"; try setfattr -n btrfs.compression -v zstd "$T/compress-zstd"
mkdir -p "$T/compress-dir"; try setfattr -n btrfs.compression -v zstd "$T/compress-dir"

# Timestamps: far past, far future, odd nanoseconds, an atime apart from the
# mtime.  Last, since writing into a directory changes its mtime.
touch "$T/empty"
mkdir -p "$T/empty-dir"
touch -d '1970-01-02 00:00:00.123456789' "$T/old"
touch -d '2100-01-01 12:00:00.987654321' "$T/future"
echo "atime" >"$T/atime"; mkdir -p "$T/atime-dir"; ln -sfn atime "$T/atime-symlink"
for p in "$T/atime" "$T/atime-dir" "$T/atime-symlink"; do
    touch -h -m -d '2002-03-04 05:06:07.111111111' "$p"
    touch -h -a -d '2003-04-05 06:07:08.222222222' "$p"
done

# btrfs's no-copy-on-write flag (systemd sets it on its journals), which only
# takes on an empty file: set before the data goes in, and inherited from a
# directory.
touch "$T/nocow"; try chattr +C "$T/nocow"; head -c 65536 /dev/urandom >>"$T/nocow"
mkdir -p "$T/nocow-dir"; try chattr +C "$T/nocow-dir"; echo "inherited" >"$T/nocow-dir/file"

# ZFS's DOS attributes (what Windows sees over SMB: readonly, hidden,
# system, archive, ...), which only ZFS has, through its own ioctl; never
# issued on another file system.  ZFS_IOC_SETDOSFLAGS replaces the whole
# set, and a write sets archive again: so after the contents, and archive
# given along where it should stay.  Readonly refuses writes, nounlink
# unlinks.
dosflags() {  # PATH VALUE
    python3 -c 'import fcntl, os, struct, sys
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
fcntl.ioctl(fd, 0x40088302, struct.pack("Q", int(sys.argv[2], 0)))' "$@"
}
if [[ $(stat -f -c %t "$T") == 2fc12fc1 ]]; then
    archive=0x800000000
    echo "hidden" >"$T/dos-hidden-system"; dosflags "$T/dos-hidden-system" $((0x600000000 | archive))
    echo "readonly" >"$T/dos-readonly"; dosflags "$T/dos-readonly" $((0x100000000 | archive))
    echo "written" >"$T/dos-noarchive"; dosflags "$T/dos-noarchive" 0
    echo "nounlink" >"$T/dos-nounlink"; dosflags "$T/dos-nounlink" $((0x2000000000 | archive))
    echo "offline" >"$T/dos-offline-sparse"; dosflags "$T/dos-offline-sparse" $((0x300000000000 | archive))
    mkdir -p "$T/dos-hidden-dir"; dosflags "$T/dos-hidden-dir" $((0x200000000 | archive))
    echo "fixed" >"$T/dos-hidden-immutable"; dosflags "$T/dos-hidden-immutable" $((0x200000000 | archive))
    try chattr +i "$T/dos-hidden-immutable"
else
    echo "torture: skipped (unsupported here): ZFS DOS attributes" >&2
fi

# Inode flags, last of all: an immutable file can't be changed afterwards.
touch "$T/nodump"; try chattr +d "$T/nodump"
echo "append" >"$T/append-only"; try chattr +a "$T/append-only"
echo "fixed" >"$T/immutable"; try chattr +i "$T/immutable"
touch -d '2001-02-03 04:05:06' "$T"
exit 0
