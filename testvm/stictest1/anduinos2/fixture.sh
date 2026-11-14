#!/bin/bash
# The fixture for anduinos2 (AnduinOS 2.0.5: Btrfs subvolumes mounted
# compress=zstd:3,noatime, the ESP at /boot/efi, AppArmor: no SELinux
# labels), one file system at a time:
#   fixture.sh ROOT root     on @root (mounted at ROOT like /): the
#                            directories above (ROOT/up1/up2) and the tree
#                            (ROOT/up1/up2/tree): every checklist feature
#                            Btrfs has (no project IDs: Btrfs has none),
#                            its per-file options (the compression
#                            property, c, m, C), a nested subvolume, and
#                            the guest's own files from / and its /boot
#   fixture.sh DIR home      on @home (tree/home): /home's mode, a user's
#                            directory like the guest's, hard links, ACLs,
#                            xattrs, nocow
#   fixture.sh DIR log       on @log (tree/var/log): the journal as the
#                            guest has it (its ACLs, nocow, xattrs)
#   fixture.sh DIR containers  on @containers: container storage's
#                            layout (an overlay whiteout: a 0:0 char
#                            device; an opaque dir's trusted.overlay.opaque)
#   fixture.sh DIR libvirt   on @libvirt: a sparse disk image, nocow
#   fixture.sh DIR snapshots on @snapshots: the snapshot manager's records
#                            (metadata, history, locks) as the guest has
#                            them; inner.sh then makes the read-only
#                            snapshots of @root and @home
#   fixture.sh DIR vfat      on the ESP (FAT32 made like the guest's): FAT
#                            attributes, names, FAT's time limits, the
#                            guest's ESP's files (shim, the MOK-signed GRUB,
#                            MokManager) with their attributes
# The guest has no attr package: setfattr is bkoshim.py (inner.sh puts it
# on PATH).  What can't be made is reported on stderr as
# "fixture: skipped: ..." (the judge fails on any).
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
ref() {  # DST SRC: DST's mode, owner and times as SRC's
    chmod --reference="$2" "$1"; chown --reference="$2" "$1"; touch -h --reference="$2" "$1"
}
N=0xffffffff
big() { yes "BackOtter compressible line $1" | head -c 2M; }

case $FS in
vfat)
    T=$1
    mkdir -p "$T" && cd "$T" || exit 1
    # The guest's own ESP (shim, MokManager, the MOK-signed GRUB, its stub
    # grub.cfg and BOOTX64.CSV, in EFI/AnduinOS and EFI/BOOT), with its
    # times and FAT attributes.
    mkdir -p real/esp
    cp -R --preserve=timestamps /boot/efi/. real/esp/ || skip "copy of /boot/efi"
    python3 - /boot/efi real/esp <<'PY' || skip "the ESP's FAT attributes"
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
    # Long, odd, case-mixed and Latin-1 names (iocharset=iso8859-1);
    # deep, empty and big directories; sizes.
    : >"name with spaces"; : >MixedCase.Txt; : >UPPER.TXT; : >"a+b,c;d=e[f]"; : >.dotfile
    : >"$(printf 'caf\351')" || skip "a Latin-1 name"
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
    exit 0 ;;
home)
    T=$1
    # /home's own mode, owner and times; the guest's user's directory
    # (0750, its dot files from /etc/skel), a second user's with ids
    # without names; hard links (their inode numbers are @home's own, so
    # the same numbers as files on @root's: a tool must link by device
    # and inode), an ACL'd share, xattrs, a nocow directory, sparse.
    ref "$T" /home
    U=$(ls -d /home/*/ 2>/dev/null | head -1); U=${U%/}
    if [[ -n $U ]]; then
        mkdir -p "$T/real-user"; cp -a --preserve=all "$U/." "$T/real-user/" 2>/dev/null || skip "copy of $U"; ref "$T/real-user" "$U"
    else skip "no user directory in /home"; fi
    try cp -a --preserve=all /etc/skel "$T/skel"
    mkdir -p "$T/u2001/.config/autostart" "$T/u2001/Documents"; chmod 0700 "$T/u2001"
    echo doc >"$T/u2001/Documents/notes.txt"; ln -f "$T/u2001/Documents/notes.txt" "$T/u2001/Documents/notes-link"
    echo a >"$T/hardlink-a"; ln -f "$T/hardlink-a" "$T/hardlink-b"; ln -f "$T/hardlink-a" "$T/u2001/hardlink-c"
    chown -R 2001:2001 "$T/u2001"
    mkdir -p "$T/share"; chmod 2770 "$T/share"; chown 0:2002 "$T/share"
    acl "$T/share" system.posix_acl_access 1:7:$N 2:7:2001 4:7:$N 16:7:$N 32:0:$N
    acl "$T/share" system.posix_acl_default 1:7:$N 2:7:2001 4:7:$N 16:7:$N 32:0:$N
    echo shared >"$T/share/inherits"
    echo x >"$T/xattr-file"; try setfattr -n user.xdg.origin.url -v https://example.invalid/f "$T/xattr-file"
    try setfattr -n user.binary -v 0x00ff10fe7f00 "$T/xattr-file"
    mkdir -p "$T/nocow-dir"; flag C "$T/nocow-dir"; head -c 64K /dev/urandom >"$T/nocow-dir/inherited"
    truncate -s 32M "$T/sparse"; echo end >>"$T/sparse"
    big home >"$T/compressible"
    ln -sfn u2001/Documents "$T/symlink"
    touch -h -d '2004-04-04 04:04:04.4' "$T/u2001" "$T/share"
    touch -h --reference=/home "$T"
    exit 0 ;;
log)
    T=$1
    # /var/log's own; the journal (its directory's setgid, ACLs and C,
    # the newest journal files: their user.crtime_usec, C from the
    # directory); other logs with their owners and modes.
    ref "$T" /var/log
    J=$(ls -d /var/log/journal/*/ 2>/dev/null | head -1); J=${J%/}
    if [[ -n $J ]]; then
        mkdir -p "$T/journal"
        lsattr -d /var/log/journal 2>/dev/null | cut -d' ' -f1 | grep -q C && flag C "$T/journal"
        JD=$T/journal/$(basename "$J"); mkdir -p "$JD"
        lsattr -d "$J" 2>/dev/null | cut -d' ' -f1 | grep -q C && flag C "$JD"
        for f in "$J"/system.journal "$J"/user-*.journal; do
            [[ -f $f ]] && { cp -a --preserve=all "$f" "$JD/" 2>/dev/null || skip "journal file $f"; }
        done
        for p in "$JD:$J" "$T/journal:/var/log/journal"; do
            ref "${p%%:*}" "${p#*:}"
            getfacl -p "${p#*:}" 2>/dev/null | setfacl --set-file=- "${p%%:*}" 2>/dev/null || skip "ACL of ${p#*:}"
        done
        # (getfacl prints the default ACL too, and setfacl --set-file sets both.)
    else skip "no /var/log/journal"; fi
    for f in /var/log/wtmp /var/log/btmp /var/log/lastlog /var/log/dpkg.log /var/log/apt; do
        [[ -e $f ]] && try cp -a --preserve=all "$f" "$T/"
    done
    mkdir -p "$T/private"; chmod 0700 "$T/private"
    touch -h --reference=/var/log "$T"
    exit 0 ;;
containers)
    T=$1
    # Container storage's layout (podman's: storage/overlay with a layer's
    # diff): an overlay whiteout (a 0:0 char device), an opaque directory
    # (trusted.overlay.opaque=y), a layer's link file, 0700 storage dirs.
    S=$T/storage/overlay/0123abcd
    mkdir -p "$S/diff/etc" "$S/diff/usr/opaque" "$S/work/work" "$T/storage/overlay/l"
    chmod 0700 "$T/storage" "$S/work" "$S/work/work"
    echo ABCDEFGHIJKLMNOPQRSTUVWXYZ >"$S/link"
    ln -sfn ../0123abcd/diff "$T/storage/overlay/l/ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    mknod "$S/diff/etc/removed-file" c 0 0 || skip "whiteout"
    try setfattr -n trusted.overlay.opaque -v y "$S/diff/usr/opaque"
    echo layer >"$S/diff/etc/os-release"; chmod 0644 "$S/diff/etc/os-release"
    install -m 4755 /dev/null "$S/diff/usr/setuid-in-layer"
    touch -d '2006-06-06 06:06:06' "$S/diff" "$S"
    exit 0 ;;
libvirt)
    T=$1
    # libvirt's images: the directory 0711, a sparse raw image (nocow, as
    # libvirt's pool directories on Btrfs are made), a qcow2-like file.
    chmod 0711 "$T"
    touch "$T/guest.raw"; flag C "$T/guest.raw"; truncate -s 2G "$T/guest.raw"
    echo boot | dd of="$T/guest.raw" bs=512 seek=0 conv=notrunc status=none
    echo data | dd of="$T/guest.raw" bs=1M seek=1024 conv=notrunc status=none
    head -c 64K /dev/urandom >"$T/guest.qcow2"; truncate -s 16M "$T/guest.qcow2"; chown 64055:994 "$T/guest.qcow2"; chmod 0600 "$T/guest.qcow2"
    touch -d '2007-07-07 07:07:07' "$T/guest.raw"
    exit 0 ;;
snapshots)
    T=$1
    # The snapshot manager's records as the guest has them, but its
    # read-only snapshots (subvolumes, which inner.sh makes here from the
    # scratch @root and @home under the same names): metadata/*.json,
    # history/system-lineage.json, the locks, personal/; every directory's
    # mode, owner and times as the guest's.
    M=/.snapshots/anduinos-btrfs-snapshots-manager
    ref "$T" /.snapshots
    [[ -d $M ]] || { skip "no $M"; exit 0; }
    python3 - "$M" "$T/anduinos-btrfs-snapshots-manager" <<'PY' || skip "the snapshot manager's records"
import os, shutil, stat, sys
src, dst = sys.argv[1], sys.argv[2]
def walk(a, b):
    os.makedirs(b, exist_ok=True)
    for n in sorted(os.listdir(a)):
        p, q = os.path.join(a, n), os.path.join(b, n)
        st = os.lstat(p)
        if stat.S_ISDIR(st.st_mode):
            if st.st_ino == 256:        # a subvolume's root: a snapshot (made by inner.sh)
                continue
            walk(p, q)
        else:
            shutil.copy2(p, q, follow_symlinks=False)
            os.lchown(q, st.st_uid, st.st_gid)
    st = os.lstat(a)
    os.chmod(b, stat.S_IMODE(st.st_mode)); os.chown(b, st.st_uid, st.st_gid)
    os.utime(b, ns=(st.st_atime_ns, st.st_mtime_ns))
walk(src, dst)
PY
    touch -h --reference=/.snapshots "$T"
    exit 0 ;;
esac

# --- root: @root, mounted like / ------------------------------------------
R=$1; U1=$R/up1; U2=$U1/up2; T=$U2/tree
mkdir -p "$T"
# Real examples from the guest's own files: set-id programs and file
# capabilities (the guest's own), shadow (0640 root:shadow), fstab as the
# installer wrote it, GRUB's defaults, the MOK's certificate (the private
# key stays out), the snapshot manager's configuration and state, the
# random seed, /var/lib's nocow-able state; /boot (GRUB's directory, the
# kernel's config and System.map, the vmlinuz/initrd.img links) without
# the kernel and initramfs themselves.
mkdir -p "$T/real"
for f in /usr/bin/passwd /usr/bin/su /usr/bin/mount /usr/bin/newuidmap /usr/bin/newgidmap /usr/bin/sudo /usr/bin/fusermount3 \
         /usr/lib/openssh/ssh-keysign /etc/shadow /etc/gshadow /etc/fstab /etc/hostname /etc/default/grub \
         /var/lib/shim-signed/mok/MOK.der /var/lib/systemd/random-seed; do
    [[ -e $f ]] && try cp -a --preserve=all "$f" "$T/real/$(basename "$f")"
done
for d in /etc/anduinos-btrfs-snapshots-manager /var/lib/anduinos-btrfs-snapshots-manager /etc/apparmor.d/tunables; do
    [[ -d $d ]] && try cp -a --preserve=all "$d" "$T/real/$(echo "${d#/}" | tr / _)"
done
# Files with file capabilities (security.capability) from the guest (up to 4).
python3 - "$T/real/caps" <<'PY' 2>/dev/null || skip "the guest's files with capabilities"
import os, shutil, sys
out = sys.argv[1]; os.makedirs(out, exist_ok=True); n = 0
for top in ("/usr/bin", "/usr/sbin", "/usr/lib", "/usr/libexec"):
    for d, ds, fs in os.walk(top):
        for f in fs:
            p = os.path.join(d, f)
            try: v = os.getxattr(p, "security.capability", follow_symlinks=False)
            except OSError: continue
            q = os.path.join(out, f); shutil.copy2(p, q); os.setxattr(q, "security.capability", v)
            st = os.lstat(p); os.chown(q, st.st_uid, st.st_gid); os.chmod(q, st.st_mode & 0o7777); n += 1
            if n >= 4: sys.exit(0)
PY
mkdir -p "$T/real/boot"
( cd /boot && for f in * .[!.]*; do
      [[ -e $f || -L $f ]] || continue
      case $f in vmlinuz-*|initrd.img-*|efi) continue ;; esac
      cp -a --preserve=all "$f" "$T/real/boot/" || echo "fixture: skipped: copy of /boot/$f" >&2
  done )
ref "$T/real/boot" /boot
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
# symlinks).  Btrfs takes values up to about a node (16K).
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
# systemd-journald's own attribute, as on the guest's journal files.
echo j >"$T/xattr-crtime"; try setfattr -n user.crtime_usec -v 0x40e2c5b1a0240600 "$T/xattr-crtime"

# Capabilities: v2 effective, permitted only, a v3 one with a rootid.
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

# Times: far past, far future, nanoseconds, atime apart from mtime.
touch -d '1970-01-02 00:00:00.123456789' "$T/old"
touch -d '2100-01-01 12:00:00.987654321' "$T/future"
touch -h -d '1960-05-06 07:08:09.5' "$T/symlink-dangling" 2>/dev/null; touch -d '1901-12-14 00:00:00' "$T/pre-epoch" 2>/dev/null || touch "$T/pre-epoch"
echo atime >"$T/atime"; mkdir -p "$T/atime-dir"; ln -sfn atime "$T/atime-symlink"
for p in "$T/atime" "$T/atime-dir" "$T/atime-symlink"; do
    touch -h -m -d '2002-03-04 05:06:07.111111111' "$p"; touch -h -a -d '2003-04-05 06:07:08.222222222' "$p"
done

# Btrfs's per-file options: the compression property (btrfs.compression)
# of each kind, set on an empty file then written, inherited from a
# directory; c (compress), m (nocompress), C (nocow: set while empty,
# inherited from a directory), with immutable too.  (The mount's
# compress=zstd:3 compresses the rest.)
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
# A nested subvolume inside the backed-up tree (beside the mounted ones):
# backed up across, restored as a directory.
btrfs -q subvolume create "$T/subvol" >/dev/null 2>&1 || skip "btrfs subvolume create"
echo in-subvol >"$T/subvol/file"; chmod 0750 "$T/subvol"; chown 1234:5678 "$T/subvol"
try setfattr -n user.subvol -v sv "$T/subvol"; touch -d '2005-05-05 05:05:05.5' "$T/subvol"

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

# The directories above: owners without names, odd modes, xattrs, an ACL.
chown 1234:5678 "$U1"; chmod 0710 "$U1"
try setfattr -n user.above -v up1 "$U1"
acl "$U1" system.posix_acl_access 1:7:$N 2:5:4321 4:1:$N 16:5:$N 32:0:$N
chmod 1755 "$U2"; try setfattr -n trusted.above -v up2 "$U2"
touch -d '1999-09-09 09:09:09.123' "$U2"; touch -d '1998-08-08 08:08:08.456' "$U1"
exit 0
