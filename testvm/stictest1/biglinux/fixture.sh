#!/bin/bash
# The fixture for biglinux (BigLinux, on Manjaro: btrfs / in subvolume @
# with @home, both mounted noatime,compress-force=zstd,autodefrag, and
# systemd-swap's top-level @swapfile at /swapfile (NOCOW); Timeshift's
# snapshots in the top-level volume at timeshift-btrfs/snapshots/DATE/@,
# outside the backup; a FAT32 ESP at /boot/efi; no SELinux;
# docs/design/biglinux.md), one file system at a time:
#   fixture.sh ROOT btrfs  the main tree, ROOT/up1/up2/tree (ROOT: a fresh
#                          btrfs's @, mounted as the guest mounts /; the
#                          guest's other subvolumes mounted at their places
#                          in the tree, and the ESP at tree/boot/efi):
#                          garuda's checklist (tumbleweed's, but SELinux),
#                          btrfs's per-file options under compress-force, a
#                          file compressed by the mount option alone, a
#                          nested writable subvolume; systemd-swap's NOCOW
#                          swap files in the @swapfile mount (1 preallocated
#                          and mkswap'd, zram-writeback-1 sparse), and
#                          Timeshift's layout made in this file system's
#                          top-level volume (a writable snapshot of @ at
#                          timeshift-btrfs/snapshots/DATE/@ with its
#                          info.json and snapshots-daily link), outside the
#                          backed-up tree; content of their own in each
#                          mounted subvolume, whose roots get the guest's
#                          own mode, owner, NOCOW flag and times; the
#                          guest's own files as real examples
#   fixture.sh DIR vfat    on the FAT32 ESP made like the guest's: FAT
#                          attributes, names, FAT's time limits, the
#                          guest's ESP's files with their attributes
# SUBS: the guest's subvolumes but @, "MOUNTPOINT:SUBVOLUME" (home:@home
# swapfile:@swapfile).  ESPM: the guest's ESP mount point (/boot/efi).
# GTOP: the guest's own top-level volume, mounted read-only (its Timeshift
# snapshot's info.json is a real example), or unset.
# What can't be made is reported on stderr as "fixture: skipped: ..."
# (the judge fails on any but chattr -p, which btrfs refuses).
set -u
FS=$2
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
fat() {  # PATH ATTRS: FAT_IOCTL_SET_ATTRIBUTES
    python3 -c 'import fcntl,os,struct,sys; fd=os.open(sys.argv[1],os.O_RDONLY); fcntl.ioctl(fd,0x40047211,struct.pack("I",int(sys.argv[2],0))); os.close(fd)' "$1" "$2" ||
        skip "FAT attributes $2 on $1"
}
N=0xffffffff
big() { yes "BackOtter compressible line $1" | head -c 2M; }

if [[ $FS == vfat ]]; then
    # gentoo's ESP fixture (garuda's), on BigLinux's /boot/efi (umask=0077).
    T=$1
    mkdir -p "$T" && cd "$T" || exit 1
    # The guest's own ESP (EFI/BigLinux/grubx64.efi, the fallback
    # EFI/boot/bootx64.efi), with its times and FAT attributes.
    mkdir -p real/esp
    cp -R --preserve=timestamps "${ESPM:?}/." real/esp/ || skip "copy of $ESPM"
    python3 - "$ESPM" real/esp <<'PY' || skip "the ESP's FAT attributes"
import fcntl, os, sys
a, b = sys.argv[1], sys.argv[2]
for dp, dns, fns in os.walk(a):
    for n in dns + fns:
        p = os.path.join(dp, n); q = os.path.join(b, os.path.relpath(p, a))
        fd = os.open(p, os.O_RDONLY); v = fcntl.ioctl(fd, 0x80047210, b"\0" * 4); os.close(fd)
        fd = os.open(q, os.O_RDONLY); fcntl.ioctl(fd, 0x40047211, v); os.close(fd)
PY
    # FAT attributes: hidden, system, archive set and cleared, read-only
    # (by chmod and by attribute), on files and directories.
    echo h >hidden; fat hidden 0x22
    echo s >system; fat system 0x24
    echo hs >hidden-system-noarchive; fat hidden-system-noarchive 0x06
    echo n >archive-cleared; fat archive-cleared 0
    echo a >archive-set; fat archive-set 0x20
    echo r >readonly-chmod; chmod a-w readonly-chmod
    echo r >readonly-attr; fat readonly-attr 0x21
    mkdir hidden-dir system-dir readonly-dir; echo in >hidden-dir/f; echo in >readonly-dir/f
    fat hidden-dir 0x12; fat system-dir 0x14; fat readonly-dir 0x11
    # Long, odd and case-mixed names (iocharset=ascii: no UTF-8 one); deep,
    # empty and big directories; sizes.
    : >"name with spaces"; : >MixedCase.Txt; : >UPPER.TXT; : >"a+b,c;d=e[f]"; : >.dotfile
    : >"$(head -c 200 /dev/zero | tr '\0' n)"
    mkdir -p EFI/BKO; head -c 307200 /dev/urandom >EFI/BKO/bkox64.efi; head -c 20971520 /dev/urandom >big
    d=deep; for i in $(seq 30); do d=$d/level$i; done; mkdir -p $d; echo bottom >$d/bottom
    : >empty; mkdir empty-dir
    mkdir big-dir; for i in $(seq 300); do : >"big-dir/entry-$i-padding-padding"; done
    # FAT's time limits (1980-01-01, 2107-12-31 23:59:58), its 2-second
    # mtimes and date-only atimes.
    touch -d 1980-01-01T00:00:00 fat-min; touch -d 2107-12-31T23:59:58 fat-max
    touch -d 2001-02-03T04:05:08 even-second
    echo atime >atime; touch -m -d 2002-03-04T05:06:08 atime; touch -a -d 2003-04-05T00:00:00 atime
    touch -m -d 2002-03-04T05:06:08 readonly-dir hidden-dir; touch -a -d 2003-04-05T00:00:00 readonly-dir hidden-dir
    exit 0
fi

R=$1; U1=$R/up1; U2=$U1/up2; T=$U2/tree
mkdir -p "$T"

# The mounted subvolumes (home, swapfile): content of their own (an owner
# without a name, a user xattr, a file the mount compresses, a hard link
# pair, a symlink); their roots get the guest's own mode, owner and NOCOW
# flag (systemd-swap's chattr +C on /swapfile, so what is written there
# after is NOCOW too) here, its times at the end.
for s in ${SUBS:?}; do
    m=${s%%:*} D=$T/${s%%:*}
    mountpoint -q "$D" || { skip "$D not mounted"; continue; }
    chmod --reference="/$m" "$D"; chown --reference="/$m" "$D"
    [[ $(lsattr -d "/$m" 2>/dev/null | cut -d' ' -f1) == *C* ]] && flag C "$D"
    echo "in ${s#*:}" >"$D/bko-file"; chown 1234:5678 "$D/bko-file"; try setfattr -n user.subvol -v "${s#*:}" "$D/bko-file"
    big "${s#*:}" >"$D/bko-compressed"
    echo linked >"$D/bko-link-a"; ln -f "$D/bko-link-a" "$D/bko-link-b"; ln -sfn bko-file "$D/bko-symlink"
done

# systemd-swap's swap files (swapFC) in the @swapfile mount, as the guest's
# /swapfile/1 and /swapfile/zram-writeback-1: NOCOW (made empty in the
# NOCOW directory, C set on each too), mode 0600; 1 preallocated and
# mkswap'd, zram-writeback-1 sparse (the guest's is 928 MiB, none of it
# allocated).  inner.sh swaps each restored 1 on and off.
SW=$T/swapfile
if mountpoint -q "$SW"; then
    touch "$SW/1"; chmod 600 "$SW/1"; flag C "$SW/1"
    fallocate -l 32M "$SW/1" || skip "fallocate $SW/1"; try mkswap -q "$SW/1"
    touch "$SW/zram-writeback-1"; chmod 600 "$SW/zram-writeback-1"; flag C "$SW/zram-writeback-1"; truncate -s 64M "$SW/zram-writeback-1"
    touch -d '2026-11-14 05:40:00' "$SW/1" "$SW/zram-writeback-1"
else
    skip "$SW not mounted: no swap files"
fi

# Real examples from the guest's own files: set-id binaries (passwd, su,
# mount), PAM's helper, shadow, fstab (the three subvolumes, compress-force),
# Timeshift's configuration, its hourly cron entry and (from the guest's
# top-level volume, GTOP) its snapshot's info.json, systemd-swap's
# swap.conf, pacman's configuration and mirror lists, GRUB's defaults,
# random-seed, systemd; /boot/grub's grubenv, grub.cfg and grub-btrfs's menu
# under tree/boot; the journal (nocow C, as systemd's tmpfiles give
# /var/log/journal on btrfs) in @, where BigLinux keeps /var/log.
mkdir -p "$T/real"
for f in /usr/bin/passwd /usr/bin/su /usr/bin/mount /usr/bin/newuidmap /usr/bin/newgidmap /usr/bin/unix_chkpwd \
         /etc/shadow /etc/fstab /etc/hostname /etc/pacman.conf /etc/default/grub /var/lib/systemd/random-seed \
         /usr/lib/systemd/systemd; do
    [[ -e $f ]] && try cp -a --preserve=all "$f" "$T/real/$(basename "$f")"
done
for p in /etc/timeshift/timeshift.json:timeshift.json /etc/timeshift/default.json:timeshift-default.json \
         /etc/cron.d/timeshift-hourly:timeshift-hourly /etc/systemd/swap.conf:systemd-swap.conf \
         /etc/pacman.d/mirrorlist:mirrorlist /etc/pacman.d/mirrorcdn:mirrorcdn; do
    [[ -f ${p%%:*} ]] && try cp -a --preserve=all "${p%%:*}" "$T/real/${p#*:}"
done
i=$(ls -d "${GTOP:-/nonexistent}"/timeshift-btrfs/snapshots/*/info.json 2>/dev/null | head -1)
[[ -n $i ]] && try cp -a --preserve=all "$i" "$T/real/timeshift-info.json"
mkdir -p "$T/boot/grub"
for f in grubenv grub.cfg grub-btrfs.cfg; do [[ -f /boot/grub/$f ]] && try cp -a --preserve=all "/boot/grub/$f" "$T/boot/grub/"; done
chmod --reference=/boot/grub "$T/boot/grub"; touch -r /boot/grub "$T/boot/grub"
J=$(ls -d /var/log/journal/*/ 2>/dev/null | head -1)
if [[ -n $J ]]; then
    JD=$T/var/log/journal/$(basename "$J")
    mkdir -p "$T/var/log/journal"
    [[ $(lsattr -d /var/log/journal 2>/dev/null | cut -d' ' -f1) == *C* ]] && flag C "$T/var/log/journal"
    mkdir -p "$JD"
    cp -a --preserve=all "$J/system.journal" "$JD/" 2>/dev/null || skip "journal file"
    for p in "$JD:$J" "${JD%/*}:/var/log/journal" "$T/var/log:/var/log"; do
        chown --reference="${p#*:}" "${p%%:*}"; chmod --reference="${p#*:}" "${p%%:*}"; touch --reference="${p#*:}" "${p%%:*}"
        getfacl -p --omit-header "${p#*:}" 2>/dev/null | setfacl -M - "${p%%:*}" 2>/dev/null
    done
else
    skip "no journal in /var/log/journal"
fi
mkdir -p "$T/real/tmp-like"; chmod 1777 "$T/real/tmp-like"

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

# Extended attributes: user (binary, large, many), trusted, security, on
# files, dirs and symlinks (trusted only: Linux keeps user.* off
# symlinks).  btrfs takes values up to about a node (16K).
touch "$T/xattr-file"
try setfattr -n user.backotter -v hello "$T/xattr-file"
try setfattr -n user.binary -v 0x00ff10fe7f00 "$T/xattr-file"
try setfattr -n user.empty -v "" "$T/xattr-file"
try setfattr -n trusted.backotter -v root-only "$T/xattr-file"
try setfattr -n security.bko -v sec-value "$T/xattr-file"
for i in $(seq 1 40); do setfattr -n "user.many$i" -v "v$i" "$T/xattr-file" 2>/dev/null || skip "user.many$i"; done
touch "$T/xattr-large"; try setfattr -n user.large -v "$(head -c 3000 /dev/zero | tr '\0' x)" "$T/xattr-large"
touch "$T/xattr-huge"
setfattr -n user.huge -v "$(head -c 15000 /dev/zero | tr '\0' y)" "$T/xattr-huge" 2>/dev/null ||
    setfattr -n user.huge -v "$(head -c 3800 /dev/zero | tr '\0' y)" "$T/xattr-huge" 2>/dev/null || skip "user.huge"
mkdir -p "$T/xattr-dir"; try setfattr -n user.dir -v d "$T/xattr-dir"; try setfattr -n trusted.dir -v t "$T/xattr-dir"
ln -sfn xattr-file "$T/xattr-symlink"; try setfattr -h -n trusted.link -v l "$T/xattr-symlink"
echo j >"$T/xattr-crtime"; try setfattr -n user.crtime_usec -v 0x40e2c5b1a0240600 "$T/xattr-crtime"

# Capabilities (libcap's setcap, as gentoo's): v2 effective, permitted
# only, a v3 one with a rootid (user namespace).
cp /usr/bin/true "$T/cap-binary"; try setcap cap_net_raw,cap_net_admin+ep "$T/cap-binary"
cp /usr/bin/true "$T/cap-permitted"; try setcap cap_setuid,cap_bpf+p "$T/cap-permitted"
cp /usr/bin/true "$T/cap-v3"; try setcap -n 1234 cap_net_raw+ep "$T/cap-v3"

# Hard links (across directories), symlinks (dangling, own times).
echo linked >"$T/hardlink-a"; ln -f "$T/hardlink-a" "$T/hardlink-b"
mkdir -p "$T/sub"; ln -f "$T/hardlink-a" "$T/sub/hardlink-c"
ln -sfn hardlink-a "$T/symlink"; ln -sfn /nonexistent/target "$T/symlink-dangling"

# Device nodes (rdev 0, big numbers), FIFO, a socket (neither tool keeps it).
mknod "$T/chardev" c 1 3; mknod "$T/blockdev" b 7 0; mknod "$T/chardev-rdev0" c 0 0
mknod "$T/chardev-big" c 511 1048575 2>/dev/null || mknod "$T/chardev-big" c 259 65536
mkfifo "$T/fifo"
python3 -c 'import socket, sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])' "$T/socket"

# Sparse files and allocation (the swap files above are the NOCOW ones).
truncate -s 1G "$T/sparse"
echo start | dd of="$T/sparse" conv=notrunc status=none
echo middle | dd of="$T/sparse" bs=1M seek=512 conv=notrunc status=none
# sparse-small: 256 KiB of data, a 64 KiB hole, 256 KiB of data, a file S3
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
sparse_small "$T/sparse-small" 65536 || skip "sparse-small's hole ($(stat -c '%b blocks, size %s' "$T/sparse-small"))"
head -c 4096 /dev/urandom >"$T/sparse-tail"; truncate -s 4099 "$T/sparse-tail"
truncate -s 64M "$T/sparse-allhole"
truncate -s 8M "$T/sparse-head"; echo end >>"$T/sparse-head"
fallocate -l 16M "$T/preallocated" || skip fallocate
head -c 8M /dev/zero >"$T/zeros"
head -c 300K /dev/urandom >"$T/random"

# Names: spaces, newline, not UTF-8, 255 bytes, deep, empty.
touch "$T/name with spaces" "$T/new"$'\n'"line" "$T/"$'\xff\xfe'"-not-utf8" "$T/$(printf 'n%.0s' {1..255})"
mkdir -p "$T/"$'\xe9'"-latin1-dir"; touch "$T/"$'\xe9'"-latin1-dir/f"
D="$T/deep$(printf '/level%s' {1..40})"; mkdir -p "$D"; touch "$D/bottom"
touch "$T/empty"; mkdir -p "$T/empty-dir"

# Project IDs: btrfs takes none; tried, and reported as skipped.
echo project >"$T/projid-file"; try chattr -p 42 "$T/projid-file"

# Times: far past, far future, nanoseconds, atime apart from mtime (the
# mounts are noatime, as BigLinux's: no read moves them afterwards).
touch -d '1970-01-02 00:00:00.123456789' "$T/old"
touch -d '2100-01-01 12:00:00.987654321' "$T/future"
touch -h -d '1960-05-06 07:08:09.5' "$T/symlink-dangling" 2>/dev/null; touch -d '1901-12-14 00:00:00' "$T/pre-epoch" 2>/dev/null || touch "$T/pre-epoch"
echo atime >"$T/atime"; mkdir -p "$T/atime-dir"; ln -sfn atime "$T/atime-symlink"
for p in "$T/atime" "$T/atime-dir" "$T/atime-symlink"; do
    touch -h -m -d '2002-03-04 05:06:07.111111111' "$p"; touch -h -a -d '2003-04-05 06:07:08.222222222' "$p"
done

# btrfs's per-file options (tumbleweed's) under compress-force=zstd: the
# compression property (btrfs.compression) of each kind, set on an empty
# file then written, inherited from a directory; c (compress), m
# (nocompress: compress-force compresses it anyway, inode_need_compress()
# tests FORCE_COMPRESS before NOCOMPRESS), C (nocow: set while empty,
# inherited from a directory; never compressed), with immutable too.  And
# compress-mount: neither property nor flag, compressed by the mount's
# compress-force=zstd alone, as every file BigLinux writes.
for c in zstd lzo zlib; do
    touch "$T/compress-$c"; try setfattr -n btrfs.compression -v $c "$T/compress-$c"; big $c >"$T/compress-$c"
done
touch "$T/compress-zstd-level"; try setfattr -n btrfs.compression -v zstd:5 "$T/compress-zstd-level"; big lvl >"$T/compress-zstd-level"
mkdir -p "$T/compress-dir"; try setfattr -n btrfs.compression -v zstd "$T/compress-dir"
big inherit >"$T/compress-dir/inherited"; mkdir -p "$T/compress-dir/sub"
touch "$T/compress-flag"; flag c "$T/compress-flag"; big flag >"$T/compress-flag"
touch "$T/compress-immutable"; try setfattr -n btrfs.compression -v zstd "$T/compress-immutable"; big imm >"$T/compress-immutable"; flag i "$T/compress-immutable"
big mount >"$T/compress-mount"
touch "$T/nocompress"; flag m "$T/nocompress"; big nocomp >"$T/nocompress"
touch "$T/nocow"; flag C "$T/nocow"; head -c 1M /dev/urandom >"$T/nocow"
mkdir -p "$T/nocow-dir"; flag C "$T/nocow-dir"; head -c 64K /dev/urandom >"$T/nocow-dir/inherited"; mkdir -p "$T/nocow-dir/sub"
touch "$T/nocow-append"; flag C "$T/nocow-append"; echo data >>"$T/nocow-append"; flag a "$T/nocow-append"
touch "$T/nocow-sparse"; flag C "$T/nocow-sparse"; truncate -s 4M "$T/nocow-sparse"; echo end >>"$T/nocow-sparse"
# compressible data in a NOCOW file: compress-force leaves it alone.
touch "$T/nocow-compressible"; flag C "$T/nocow-compressible"; big nocowc >"$T/nocow-compressible"
# A nested, unmounted writable subvolume inside the backed-up tree:
# backed up across, restored as a directory.
btrfs -q subvolume create "$T/subvol" >/dev/null 2>&1 || skip "btrfs subvolume create"
echo in-subvol >"$T/subvol/file"; chmod 0750 "$T/subvol"; chown 1234:5678 "$T/subvol"
try setfattr -n user.subvol -v sv "$T/subvol"; touch -d '2005-05-05 05:05:05.5' "$T/subvol"

# Timeshift's layout, as BigLinux's (big-first-boot's first snapshot): in
# this file system's top-level volume, a writable snapshot of @ (Timeshift
# makes them writable; include_btrfs_home_for_backup is false, so @ alone)
# at timeshift-btrfs/snapshots/DATE/@, its info.json beside it, the empty
# snapshots-* directories and snapshots-daily/DATE linking to it.  None of
# it is under @'s mount, so neither backup reaches it, as bko's doesn't:
# bko-holt load leaves Timeshift's snapshots out with a note.  Then the live
# tree moves on: ts-changed differs from the snapshot's, ts-deleted is only
# in the snapshot.
TS=2026-11-14_04-34-40
echo before-timeshift >"$T/ts-changed"; echo only-in-timeshift-snapshot >"$T/ts-deleted"
TDEV=$(findmnt -n -o SOURCE --target "$R"); TDEV=${TDEV%%\[*}
RSV=$(findmnt -n -o FSROOT --target "$R"); RSV=${RSV#/}
TOPT=$(findmnt -n -o OPTIONS --target "$R" | tr , '\n' | grep -vE '^(subvolid=.*|subvol=.*)$' | paste -sd,)
TOP=/mnt/bko-tstop
if mkdir -p $TOP && mount -t btrfs -o "subvolid=5,$TOPT" "$TDEV" $TOP; then
    TD=$TOP/timeshift-btrfs
    mkdir -p "$TD/snapshots/$TS"
    for k in boot hourly daily weekly monthly ondemand; do mkdir -p "$TD/snapshots-$k"; done
    sync
    btrfs -q subvolume snapshot "$TOP/$RSV" "$TD/snapshots/$TS/@" >/dev/null 2>&1 || skip "btrfs subvolume snapshot (Timeshift's)"
    U=$(findmnt -n -o UUID --target "$R")
    cat >"$TD/snapshots/$TS/info.json" <<JSON
{
  "created" : "1794630880",
  "sys-uuid" : "$U",
  "sys-distro" : "BigLinux based on Manjaro Linux 26.1.2 (Bian-May)",
  "app-version" : "25.12.4",
  "file_count" : "0",
  "tags" : "daily",
  "comments" : "",
  "live" : "false",
  "type" : "btrfs",
  "subvolumes" : {
    "@" : [
      "@",
      "-1",
      "0",
      "0",
      "$U"
    ]
  }
}
JSON
    ln -sfn ../snapshots/$TS "$TD/snapshots-daily/$TS"
    sync; umount $TOP
else
    skip "mount of the top-level volume (Timeshift's layout)"
fi
echo after-timeshift >"$T/ts-changed"; rm -f "$T/ts-deleted"

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

# The guest's times on the mounted subvolumes' roots and the directories
# above them in the tree (boot, var), then the tree's.
for s in $SUBS; do [[ -d $T/${s%%:*} ]] && touch -r "/${s%%:*}" "$T/${s%%:*}"; done
for d in boot var; do mkdir -p "$T/$d"; chmod --reference=/$d "$T/$d"; chown --reference=/$d "$T/$d"; touch -r /$d "$T/$d"; done
touch -d '2001-02-03 04:05:06' "$T"

# The directories above: owners without names, odd modes, xattrs, ACL.
chown 1234:5678 "$U1"; chmod 0710 "$U1"
try setfattr -n user.above -v up1 "$U1"
acl "$U1" system.posix_acl_access 1:7:$N 2:5:4321 4:1:$N 16:5:$N 32:0:$N
chmod 1755 "$U2"; try setfattr -n trusted.above -v up2 "$U2"
touch -d '1999-09-09 09:09:09.123' "$U2"; touch -d '1998-08-08 08:08:08.456' "$U1"
exit 0
