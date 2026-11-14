#!/bin/bash
# The fixture for pikaos4 and pikaos4-luks, one file system at a time (no
# SELinux, no AppArmor: the guest's LSMs keep no labels):
#   fixture.sh ROOT xfs main   the main tree, under ROOT/up1/up2/tree (ROOT:
#                              a fresh XFS made like /, on a partition like
#                              linux-root): the directories above, every
#                              checklist feature XFS has, XFS's project IDs
#                              (P, 32-bit) and fsxattr options, a
#                              60000-byte xattr, and the guest's own files
#                              from / (set-id, shadow, booster.yaml, apt's
#                              sources, dpkg's entries of the boot stack,
#                              systemd's links, random-seed, the journal)
#   fixture.sh ROOT btrfs main the same on pikaos4-luks's Btrfs @ (on
#                              LUKS2), without project IDs (Btrfs has none)
#                              or the 60000-byte xattr (a node's at most),
#                              with Btrfs's own: the compression property,
#                              c, m, C (nocow) and their inheritance, a nested
#                              subvolume (tumbleweed's)
#   fixture.sh DIR btrfs home  on the same Btrfs's @home, mounted at /home:
#                              the features again, /home's mode and owner,
#                              /etc/skel
#   fixture.sh DIR ext4 boot   on an ext4 made like /boot (linux-boot, the
#                              XBOOTLDR partition): the features again, with
#                              ext4's own inode flags (D, j, T; no project
#                              IDs: the guest's /boot has no project
#                              feature), and /boot's own files (the kernel,
#                              booster's initramfs, refind_linux.conf)
#   fixture.sh DIR vfat        on a FAT32 made like the ESP: FAT attributes,
#                              names, FAT's time limits, the guest's ESP's
#                              files (rEFInd's, in EFI/refind and EFI/BOOT)
#                              with their attributes
# ESPM: the guest's ESP mount point (/boot/efi).  BOOTM: its /boot mount
# point.  What can't be made is reported on stderr as "fixture: skipped:
# ..." (the judge fails on any).  setfattr is bkoshim.py on this guest.
set -u
FS=$2 ROLE=${3:-}
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

if [[ $FS == vfat ]]; then
    T=$1
    mkdir -p "$T" && cd "$T" || exit 1
    # The guest's own ESP (rEFInd in EFI/refind and, as the fallback
    # loader, EFI/BOOT; its drivers, icons, theme and refind.conf), with its
    # times and FAT attributes.
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
    # Long, odd and case-mixed names (iocharset=ascii with utf8: no UTF-8
    # one); deep, empty and big directories; sizes.
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

if [[ $ROLE == main ]]; then
    R=$1; U1=$R/up1; U2=$U1/up2; T=$U2/tree
else
    T=$1
fi
mkdir -p "$T"

# Real examples from the guest's own files.
mkdir -p "$T/real"
if [[ $ROLE == main ]]; then
    # Set-id binaries (PikaOS's ping is neither setuid nor capable: it uses
    # ping_group_range), shadow, fstab, booster's configuration, apt's
    # sources, zram's settings, crypttab (pikaos4-luks), dpkg's database
    # (status, and the boot stack's info files), systemd's links,
    # random-seed (user.random-seed-creditable), the journal's directories
    # with their ACLs and set-gid.
    for f in /usr/bin/passwd /usr/bin/su /usr/bin/mount /usr/bin/newuidmap /usr/bin/chfn /usr/sbin/unix_chkpwd \
             /usr/lib/openssh/ssh-keysign /etc/shadow /etc/fstab /etc/hostname /etc/booster.yaml /etc/default/zramswap \
             /etc/crypttab /var/lib/dpkg/status /var/lib/systemd/random-seed; do
        [[ -e $f ]] && try cp -a --preserve=all "$f" "$T/real/$(basename "$f")"
    done
    [[ -d /etc/apt/sources.list.d ]] && try cp -a --preserve=all /etc/apt/sources.list.d "$T/real/apt-sources"
    mkdir -p "$T/real/dpkg-info"
    for p in booster refind kernel-pika xfsprogs zram-tools; do
        for f in /var/lib/dpkg/info/$p.* /var/lib/dpkg/info/$p:amd64.*; do
            [[ -e $f ]] && try cp -a --preserve=all "$f" "$T/real/dpkg-info/"
        done
    done
    [[ -n $(ls "$T/real/dpkg-info") ]] || skip "no /var/lib/dpkg/info entries copied"
    [[ -d /etc/systemd/system ]] && try cp -a --preserve=all /etc/systemd/system "$T/real/systemd-system"
    [[ -d /var/log/journal ]] && try cp -a --preserve=all /var/log/journal "$T/real/journal"
elif [[ $ROLE == boot ]]; then
    # The guest's /boot's own mode, owner and times on this file system's
    # root; its files (not its lost+found, not the ESP mounted in it): the
    # kernel, booster's initramfs, config, System.map, refind_linux.conf.
    chmod --reference="${BOOTM:?}" "$T"; chown --reference="$BOOTM" "$T"
    mkdir -p "$T/real/boot"
    for f in "$BOOTM"/*; do [[ -f $f && ! -L $f ]] && try cp -a --preserve=all "$f" "$T/real/boot/"; done
    touch -r "$BOOTM" "$T/real/boot"
elif [[ $ROLE == home ]]; then
    # The guest's /home's own mode, owner and times on this subvolume's root.
    chmod --reference=/home "$T"; chown --reference=/home "$T"
    try cp -a --preserve=all /etc/skel "$T/real/skel"
fi
mkdir -p "$T/real/tmp-like"; chmod 1777 "$T/real/tmp-like"

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

# Extended attributes: user (binary, large, many), trusted, security, on
# files, dirs and symlinks (trusted only: Linux keeps user.* off
# symlinks).  ext4 (no ea_inode) holds one block of them per inode: the
# large one has a file of its own there; XFS takes a 60000-byte one too.
touch "$T/xattr-file"
try setfattr -n user.backotter -v hello "$T/xattr-file"
try setfattr -n user.binary -v 0x00ff10fe7f00 "$T/xattr-file"
try setfattr -n user.empty -v "" "$T/xattr-file"
try setfattr -n trusted.backotter -v root-only "$T/xattr-file"
try setfattr -n security.bko -v sec-value "$T/xattr-file"
for i in $(seq 1 40); do setfattr -n "user.many$i" -v "v$i" "$T/xattr-file" 2>/dev/null || skip "user.many$i"; done
touch "$T/xattr-large"; try setfattr -n user.large -v "$(head -c 3000 /dev/zero | tr '\0' x)" "$T/xattr-large"
[[ $FS == xfs ]] && { touch "$T/xattr-huge"; try setfattr -n user.huge -v "$(head -c 60000 /dev/zero | tr '\0' y)" "$T/xattr-huge"; }
mkdir -p "$T/xattr-dir"; try setfattr -n user.dir -v d "$T/xattr-dir"; try setfattr -n trusted.dir -v t "$T/xattr-dir"
ln -sfn xattr-file "$T/xattr-symlink"; try setfattr -h -n trusted.link -v l "$T/xattr-symlink"

# Capabilities: v2 effective, a v3 one with a rootid (user namespace).
cp /usr/bin/true "$T/cap-binary"; try setcap cap_net_raw,cap_net_admin+ep "$T/cap-binary"
cp /usr/bin/true "$T/cap-v3"; try setcap -n 1234 cap_net_raw+ep "$T/cap-v3"
cp /usr/bin/true "$T/cap-p-only"; try setcap cap_sys_admin+p "$T/cap-p-only"

# Hard links (across directories), symlinks (dangling, own times).
echo linked >"$T/hardlink-a"; ln -f "$T/hardlink-a" "$T/hardlink-b"
mkdir -p "$T/sub"; ln -f "$T/hardlink-a" "$T/sub/hardlink-c"
ln -sfn hardlink-a "$T/symlink"; ln -sfn /nonexistent/target "$T/symlink-dangling"

# Device nodes (rdev 0, big numbers: XFS encodes 14/18 bits, ext4 Linux's
# 12/20), FIFO, a socket (neither tool keeps it).
mknod "$T/chardev" c 1 3; mknod "$T/blockdev" b 7 0; mknod "$T/chardev-rdev0" c 0 0
mknod "$T/chardev-big" c 511 1048575 2>/dev/null || mknod "$T/chardev-big" c 259 65536
mkfifo "$T/fifo"
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

if [[ $FS == xfs ]]; then
    # Project IDs (XFS, projid32bit) and inheritance (P).
    echo project >"$T/projid-file"; try chattr -p 42 "$T/projid-file"
    mkdir -p "$T/projid-dir"; try chattr +P -p 43 "$T/projid-dir"; echo in >"$T/projid-dir/file"
    echo big >"$T/projid-big"; try chattr -p 4000000000 "$T/projid-big"
    # XFS's own fsxattr flags beside chattr's: extent size hints (on a
    # file, and on a directory, which its new files inherit), nodefrag,
    # filestream (xfs_io).
    touch "$T/xfs-extsize"; try xfs_io -c "extsize 1m" "$T/xfs-extsize"; head -c 64K /dev/urandom >>"$T/xfs-extsize"
    mkdir -p "$T/xfs-extsize-dir"; try xfs_io -c "extsize 2m" "$T/xfs-extsize-dir"; head -c 64K /dev/urandom >"$T/xfs-extsize-dir/inherits"
    echo n >"$T/xfs-nodefrag"; try xfs_io -c "chattr +f" "$T/xfs-nodefrag"
    mkdir -p "$T/xfs-filestream"; try xfs_io -c "chattr +S" "$T/xfs-filestream"
fi

if [[ $FS == btrfs ]]; then
    # Btrfs's per-file options: the compression property (btrfs.compression)
    # on files and a directory (inherited); c (compress), m (nocompress),
    # C (nocow: set while empty, inherited from a directory).
    big() { yes "BackOtter compressible line $1" | head -c 2M; }
    for c in zstd lzo zlib; do
        touch "$T/compress-$c"; try setfattr -n btrfs.compression -v $c "$T/compress-$c"; big $c >"$T/compress-$c"
    done
    touch "$T/compress-zstd-level"; try setfattr -n btrfs.compression -v zstd:5 "$T/compress-zstd-level"; big lvl >"$T/compress-zstd-level"
    mkdir -p "$T/compress-dir"; try setfattr -n btrfs.compression -v zstd "$T/compress-dir"
    big inherit >"$T/compress-dir/inherited"; mkdir -p "$T/compress-dir/sub"
    touch "$T/compress-flag"; flag c "$T/compress-flag"; big flag >"$T/compress-flag"
    touch "$T/nocompress"; flag m "$T/nocompress"; big nocomp >"$T/nocompress"
    touch "$T/nocow"; flag C "$T/nocow"; head -c 1M /dev/urandom >"$T/nocow"
    mkdir -p "$T/nocow-dir"; flag C "$T/nocow-dir"; head -c 64K /dev/urandom >"$T/nocow-dir/inherited"; mkdir -p "$T/nocow-dir/sub"
    touch "$T/nocow-sparse"; flag C "$T/nocow-sparse"; truncate -s 4M "$T/nocow-sparse"; echo end >>"$T/nocow-sparse"
    if [[ $ROLE == main ]]; then
        # A nested subvolume inside the backed-up tree (as @home is one in
        # the guest): backed up across, restored as a directory.
        btrfs -q subvolume create "$T/subvol" >/dev/null 2>&1 || skip "btrfs subvolume create"
        echo in-subvol >"$T/subvol/file"; chmod 0750 "$T/subvol"; chown 1234:5678 "$T/subvol"
        try setfattr -n user.subvol -v sv "$T/subvol"; touch -d '2005-05-05 05:05:05.5' "$T/subvol"
    fi
fi

# Times: far past, far future, nanoseconds, atime apart from mtime.
touch -d '1970-01-02 00:00:00.123456789' "$T/old"
touch -d '2100-01-01 12:00:00.987654321' "$T/future"
touch -h -d '1960-05-06 07:08:09.5' "$T/symlink-dangling" 2>/dev/null; touch -d '1901-12-14 00:00:00' "$T/pre-epoch" 2>/dev/null || touch "$T/pre-epoch"
echo atime >"$T/atime"; mkdir -p "$T/atime-dir"; ln -sfn atime "$T/atime-symlink"
for p in "$T/atime" "$T/atime-dir" "$T/atime-symlink"; do
    touch -h -m -d '2002-03-04 05:06:07.111111111' "$p"; touch -h -a -d '2003-04-05 06:07:08.222222222' "$p"
done

# Inode flags both file systems have (chattr): sync, noatime, nodump,
# append, immutable; on a directory too.  ext4's own: dirsync (D), data
# journaling (j), the Orlov top directory (T).  Last: an immutable file
# can't change afterwards.
touch "$T/sync"; flag S "$T/sync"
touch "$T/noatime"; flag A "$T/noatime"
touch "$T/nodump"; flag d "$T/nodump"
mkdir -p "$T/nodump-dir"; flag d "$T/nodump-dir"
if [[ $FS == ext4 ]]; then
    mkdir -p "$T/dirsync-dir"; flag D "$T/dirsync-dir"
    echo j >"$T/journal-data"; flag j "$T/journal-data"
    mkdir -p "$T/topdir"; flag T "$T/topdir"
fi
echo append >"$T/append-only"; flag a "$T/append-only"
echo fixed >"$T/immutable"; flag i "$T/immutable"
echo both >"$T/immutable-nodump"; chattr +d "$T/immutable-nodump"; flag i "$T/immutable-nodump"
mkdir -p "$T/immutable-dir"; echo in >"$T/immutable-dir/f"; flag i "$T/immutable-dir"
echo fixed2 >"$T/real/immutable-linked"; ln "$T/real/immutable-linked" "$T/immutable-linked2"; flag i "$T/immutable-linked2"
case $ROLE in main) touch -d '2001-02-03 04:05:06' "$T" ;; home) touch -r /home "$T" ;; *) touch -r "$BOOTM" "$T" ;; esac

# The directories above (the main tree): owners without names, odd modes,
# xattrs, an ACL.
if [[ $ROLE == main ]]; then
    chown 1234:5678 "$U1"; chmod 0710 "$U1"
    try setfattr -n user.above -v up1 "$U1"
    acl "$U1" system.posix_acl_access 1:7:$N 2:5:4321 4:1:$N 16:5:$N 32:0:$N
    chmod 1755 "$U2"; try setfattr -n trusted.above -v up2 "$U2"
    touch -d '1999-09-09 09:09:09.123' "$U2"; touch -d '1998-08-08 08:08:08.456' "$U1"
fi
exit 0
