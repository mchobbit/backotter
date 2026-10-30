#!/bin/sh
# Add hard-to-preserve files to a mounted FreeBSD system, under
# ROOT/root/bko-torture: the FreeBSD counterpart of ../torture.sh, with
# FreeBSD's own metadata (file flags, POSIX.1e or NFSv4 ACLs, extended
# attributes in both namespaces).
#
# Usage: torture.sh ROOT
#
# Each kind is attempted on its own; what the file system refuses (ZFS has no
# uchg, for one) is reported and skipped.

[ $# -eq 1 ] && [ -d "$1" ] || { echo "usage: $0 ROOT" >&2; exit 1; }
T=$1/root/bko-torture
mkdir -p "$T"
try() { "$@" 2>/dev/null || echo "torture: skipped (unsupported here): $*" >&2; }

# Ownership by ids that have no user name, and the special permission bits.
touch "$T/no-such-user"; chown 1234:5678 "$T/no-such-user"
touch "$T/setuid"; chown 1234:5678 "$T/setuid"; chmod 4755 "$T/setuid"
touch "$T/setgid"; chmod 2755 "$T/setgid"
mkdir -p "$T/sticky-dir"; chmod 1777 "$T/sticky-dir"

# Extended attributes, both namespaces, one value binary.
echo data >"$T/xattrs"
setextattr user backotter hello "$T/xattrs"
setextattr system backotter root-only "$T/xattrs"
printf '\000\377\020\376' | setextattr -i user binary "$T/xattrs"

# ACLs of whichever kind the file system has: NFSv4 (ZFS, UFS with
# nfsv4acls) or POSIX.1e (UFS with acls).  Named entries, a deny entry, and
# ones a directory hands down.
echo acl >"$T/acl-file"; mkdir -p "$T/acl-dir"; echo inside >"$T/acl-dir/inside"
if setfacl -m u:nobody:rwxp::allow,g:wheel:r::deny "$T/acl-file" 2>/dev/null; then
    setfacl -m u:nobody:rx:fd:allow "$T/acl-dir"
elif setfacl -m u:nobody:rwx,g:wheel:r,m::rwx "$T/acl-file" 2>/dev/null; then
    setfacl -d -m u::rwx,g::rx,o::rx,u:nobody:rx,m::rx "$T/acl-dir"
else
    echo "torture: skipped (unsupported here): ACLs" >&2
fi

# Hard links, one in another directory; symlinks, one dangling.
echo linked >"$T/hardlink-a"
ln -f "$T/hardlink-a" "$T/hardlink-b"
mkdir -p "$T/sub"; ln -f "$T/hardlink-a" "$T/sub/hardlink-c"
ln -sfh hardlink-a "$T/symlink"
ln -sfh /nonexistent/target "$T/symlink-dangling"
mkfifo "$T/fifo"

# A 1 GiB sparse file with data at two places.
truncate -s 1G "$T/sparse"
echo start | dd of="$T/sparse" conv=notrunc 2>/dev/null
echo middle | dd of="$T/sparse" bs=1m seek=512 conv=notrunc 2>/dev/null

# Awkward names: spaces, bytes that aren't UTF-8, 250 characters, a deep path.
touch "$T/name with spaces" "$T/$(printf '\377\376')-not-utf8" "$T/$(printf '%0250d' 0)"
deep=$T/deep; i=1; while [ $i -le 40 ]; do deep=$deep/level$i; i=$((i + 1)); done
mkdir -p "$deep"; touch "$deep/bottom"

# Timestamps: odd nanoseconds, far future, a symlink's own.
touch "$T/empty"; mkdir -p "$T/empty-dir"
touch -d 2001-02-03T04:05:06.123456789 "$T/nanoseconds"
touch -d 2100-01-01T12:00:00.987654321 "$T/future"
touch -h -d 2002-02-02T02:02:02 "$T/symlink"

# File flags, last of all: most of them stop any change afterwards.
for f in uchg uappnd uunlnk nodump hidden schg sappnd sunlnk; do
    echo $f >"$T/flag-$f"; try chflags $f "$T/flag-$f"
done
mkdir -p "$T/schg-dir"; echo inside >"$T/schg-dir/inside"
try chflags schg "$T/hardlink-a"
try chflags -h schg "$T/symlink"
try chflags schg "$T/schg-dir"
touch -d 2001-02-03T04:05:06 "$T"
exit 0
