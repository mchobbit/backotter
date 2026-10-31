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

# Timestamps: odd nanoseconds, far future, a symlink's own, and atimes apart
# from the mtimes (on a file, a directory and a symlink), to the nanosecond.
touch "$T/empty"; mkdir -p "$T/empty-dir"
touch -d 2001-02-03T04:05:06.123456789 "$T/nanoseconds"
touch -d 2100-01-01T12:00:00.987654321 "$T/future"
touch -h -d 2002-02-02T02:02:02.000000007 "$T/symlink"
echo atime >"$T/atime-ns"; mkdir -p "$T/atime-ns-dir"; ln -sfh atime-ns "$T/atime-ns-symlink"
touch -d 2004-04-04T04:04:04.444444444 "$T/atime-ns" "$T/atime-ns-dir"
touch -h -d 2004-04-04T04:04:04.444444444 "$T/atime-ns-symlink"
touch -a -d 2003-03-03T03:03:03.333333333 "$T/atime-ns" "$T/atime-ns-dir"
touch -h -a -d 2003-03-03T03:03:03.333333333 "$T/atime-ns-symlink"

# Birth times.  No system call sets one, but the kernel lowers it to an
# mtime set earlier than it: these were born in 2001 and last changed in
# 2010, and "unborn", born now and changed in 1990, came down to 1990 with
# its mtime.  A restore can give min(birth, mtime) at best.
echo born >"$T/born"; mkdir -p "$T/born-dir"; ln -sfh born "$T/born-symlink"
for p in born born-dir; do touch -t 200101010101 "$T/$p"; touch -t 201001010101 "$T/$p"; done
touch -h -t 200101010101 "$T/born-symlink"; touch -h -t 201001010101 "$T/born-symlink"
touch -d 2001-01-01T01:01:01.123456789 "$T/born-ns"; touch -d 2010-01-01T01:01:01.987654321 "$T/born-ns"
echo unborn >"$T/unborn"; touch -t 199001010101 "$T/unborn"

# File flags, last of all: most of them stop any change afterwards.  ZFS
# refuses uchg, uappnd, uunlnk, arch (EOPNOTSUPP) and reparse (EPERM); it
# keeps the DOS-style ones (hidden, system, rdonly, offline, sparse, uarch),
# and gives every new file and directory uarch.  UFS takes them all.
for f in uchg uappnd uunlnk nodump hidden system rdonly offline sparse uarch arch schg sappnd sunlnk; do
    echo $f >"$T/flag-$f"; try chflags $f "$T/flag-$f"
done
echo hidden,system >"$T/dos-hidden-system"; try chflags hidden,system "$T/dos-hidden-system"
echo offline,sparse >"$T/dos-offline-sparse"; try chflags offline,sparse "$T/dos-offline-sparse"
mkdir -p "$T/dos-hidden-dir"; try chflags hidden "$T/dos-hidden-dir"
echo hidden,schg >"$T/dos-hidden-schg"; try chflags hidden,schg "$T/dos-hidden-schg"
# On ZFS a file with nothing but uarch cleared, as a Windows backup over SMB
# leaves one: a restore that writes it gets uarch back, unless it clears it
# again.  Nothing may touch it afterwards: on ZFS, setting its times sets
# uarch again.  (fsmeta.sh leaves it out of mtree's spec; roundtrip.sh
# compares its flags on their own.)
echo noarchive >"$T/dos-noarchive"; try chflags nouarch "$T/dos-noarchive"
mkdir -p "$T/schg-dir"; echo inside >"$T/schg-dir/inside"
try chflags schg "$T/hardlink-a"
try chflags -h schg "$T/symlink"
try chflags schg "$T/schg-dir"
touch -d 2001-02-03T04:05:06 "$T"
exit 0
