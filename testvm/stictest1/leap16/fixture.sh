#!/bin/bash
# The fixture for leap16 (openSUSE Leap 16.1: btrfs / the writable snapper
# snapshot 1, the default subvolume, with Leap's subvolumes beside it;
# SELinux enforcing): under $1 (a fresh btrfs laid out like the guest's),
# up1/up2 (the directories above the backed-up path) and up1/up2/tree; $2:
# the file system type (btrfs), $3: the log directory.  From tumbleweed's
# and microos's, with Leap's subvolumes (real/layout: @/var with C,
# @/usr/local, @/srv, @/root, @/opt, @/home, @/boot/grub2/x86_64-efi and
# i386-pc, as the guest has them), /boot/grub2, and snapper's snapshots
# (real/.snapshots: 1 writable as the booted one, then a zypper pre/post
# pair of read-only snapshots).  setcapraw, and setfattr or setfacl where
# the guest lacks them, are bkoshim.py (python3, os.setxattr).
set -u
R=$1 FS=$2 L=${3:-/tmp}; U1=$R/up1; U2=$U1/up2; T=$U2/tree
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
# Real examples from the guest's own files: labels, capabilities, set-id
# binaries, shadow (mode 0, shadow_t), /etc/selinux/config, random-seed,
# the journal (setgid dir, nocow C inherited from /var/log's like,
# user.crtime_usec), /var/lib's nocow dirs.  Leap's subvolumes, /boot/grub2
# and snapper's snapshots come below (btrfs).
mkdir -p "$T/real"
for f in /usr/bin/newuidmap /usr/bin/newgidmap /usr/bin/passwd /usr/bin/su /usr/bin/mount /usr/bin/fusermount3 \
         /usr/sbin/unix_chkpwd /etc/shadow /etc/hostname /var/lib/systemd/random-seed /etc/selinux/config \
         /usr/lib/systemd/systemd /boot/grub2/grubenv; do
    [[ -e $f ]] && try cp -a --preserve=all "$f" "$T/real/$(basename "$f")"
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
fallocate -l 32M "$T/swapfile" && chmod 600 "$T/swapfile" && try mkswap -q "$T/swapfile"
head -c 300K /dev/urandom >"$T/random"

# Names: spaces, newline, not UTF-8, 255 bytes, deep, empty.
touch "$T/name with spaces" "$T/new"$'\n'"line" "$T/"$'\xff\xfe'"-not-utf8" "$T/$(printf 'n%.0s' {1..255})"
mkdir -p "$T/"$'\xe9'"-latin1-dir"; touch "$T/"$'\xe9'"-latin1-dir/f"
D="$T/deep$(printf '/level%s' {1..40})"; mkdir -p "$D"; touch "$D/bottom"
touch "$T/empty"; mkdir -p "$T/empty-dir"

# Project IDs: btrfs takes none;
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
    # Leap's subvolumes (real/layout): each of the guest's subvolumes below
    # @ but snapper's, made again at its path with its mount point's flags
    # (@/var's C), owner, mode and label, the directories between them
    # (boot, boot/grub2, usr) as the guest's.  @/var holds a file and
    # directories written after (they take C from it) and random-seed;
    # @/boot/grub2/x86_64-efi the guest's GRUB modules; boot/grub2 itself
    # the rest of the guest's /boot/grub2 (grub.cfg, grubenv, fonts...).
    LY=$T/real/layout
    mkdir -p "$LY"
    for p in $(btrfs subvolume list / | sed -n 's|^ID [0-9]* gen [0-9]* top level [0-9]* path @/||p' | grep -v '^\.snapshots'); do
        m=$(findmnt -rn -t btrfs -o TARGET,FSROOT | awk -v r="/@/$p" '$2 == r { print $1; exit }')
        [[ -n $m ]] || { skip "layout: no mount of @/$p"; continue; }
        mkdir -p "$(dirname "$LY/$p")"
        btrfs -q subvolume create "$LY/$p" >/dev/null || { skip "layout: subvolume @/$p"; continue; }
        [[ $(lsattr -d "$m" | cut -d' ' -f1) == *C* ]] && flag C "$LY/$p"
        chown --reference="$m" "$LY/$p"; chmod --reference="$m" "$LY/$p"; chcon --reference="$m" "$LY/$p" 2>/dev/null
        echo "@/$p $m $(lsattr -d "$m" | cut -d' ' -f1) $(lsattr -d "$LY/$p" | cut -d' ' -f1)" >>"$L/layout-subvols.txt"
    done
    for d in boot boot/grub2 usr; do
        [[ -d $LY/$d && -d /$d ]] && { chown --reference=/$d "$LY/$d"; chmod --reference=/$d "$LY/$d"; chcon --reference=/$d "$LY/$d" 2>/dev/null; }
    done
    if [[ -d $LY/var ]]; then
        head -c 256K /dev/urandom >"$LY/var/nocow-inherited"
        mkdir -p "$LY/var/lib/systemd" "$LY/var/log"
        try cp -a --preserve=all /var/lib/systemd/random-seed "$LY/var/lib/systemd/random-seed"
        for p in "$LY/var/lib:/var/lib" "$LY/var/lib/systemd:/var/lib/systemd" "$LY/var/log:/var/log"; do
            chown --reference="${p#*:}" "${p%%:*}"; chmod --reference="${p#*:}" "${p%%:*}"; chcon --reference="${p#*:}" "${p%%:*}" 2>/dev/null
        done
    fi
    [[ -d $LY/boot/grub2/x86_64-efi ]] && { cp -a --preserve=all /boot/grub2/x86_64-efi/. "$LY/boot/grub2/x86_64-efi/" || skip "layout: x86_64-efi's modules"; }
    [[ -d $LY/boot/grub2/i386-pc ]] && { cp -a --preserve=all /boot/grub2/i386-pc/. "$LY/boot/grub2/i386-pc/" 2>/dev/null || skip "layout: i386-pc's files"; }
    mkdir -p "$LY/boot/grub2"
    for f in /boot/grub2/* /boot/grub2/.[!.]*; do
        [[ -e $f && $f != /boot/grub2/x86_64-efi && $f != /boot/grub2/i386-pc ]] || continue
        cp -a --preserve=all "$f" "$LY/boot/grub2/" || skip "layout: $f"
    done
    ( cd /boot/grub2 && find . -xdev -type f -exec sha256sum {} + | sort -k2 ) >"$L/boot-grub2.sha256"
    touch --reference=/boot/grub2 "$LY/boot/grub2" 2>/dev/null
    # snapper's snapshots (real/.snapshots), as Leap's snapper makes them:
    # N/info.xml beside N/snapshot.  1 is the installer's first root file
    # system, writable, as the guest boots it (its info.xml the guest's),
    # holding the guest's set-id, capability-carrying and labelled /usr
    # binaries (with their directories), /etc's files and /boot/grub2's
    # grub.cfg and grubenv; then a zypper transaction's pair: 2 a read-only
    # pre snapshot of 1, 1 changed as zypper changes the root (a file
    # added, grub.cfg rewritten, a binary removed), 3 the read-only post.
    SN=$T/real/.snapshots
    cur=$(btrfs subvolume get-default / | sed -n 's|.* path @/.snapshots/\([0-9]*\)/snapshot$|\1|p')
    [[ -n $cur ]] || skip "the booted snapshot: $(btrfs subvolume get-default / 2>&1)"
    btrfs -q subvolume create "$SN" >/dev/null || skip "snapshots: subvolume $SN"
    chown --reference=/.snapshots "$SN"; chmod --reference=/.snapshots "$SN"; chcon --reference=/.snapshots "$SN" 2>/dev/null
    [[ -e /.snapshots/grub-snapshot.cfg ]] && try cp -a --preserve=all /.snapshots/grub-snapshot.cfg "$SN/"
    etc_files="/etc/passwd /etc/group /etc/shadow /etc/fstab /etc/hostname /etc/machine-id /etc/os-release /etc/selinux/config
               /etc/ssh/sshd_config /etc/default/grub /etc/snapper/configs/root /etc/sysconfig/snapper /etc/sysconfig/bootloader"
    usr_files=$( { find /usr/bin /usr/sbin /usr/lib /usr/libexec -xdev -type f -perm /6000 2>/dev/null
                   python3 -c 'import os
for d in ("/usr/bin", "/usr/sbin", "/usr/libexec", "/usr/lib/systemd"):
    for dp, dn, fn in os.walk(d):
        for n in fn:
            p = os.path.join(dp, n)
            try:
                if not os.path.islink(p) and "security.capability" in os.listxattr(p): print(p)
            except OSError: pass'
                   ls -d /usr/lib/systemd/systemd /usr/bin/zypper /usr/bin/snapper 2>/dev/null; } | sort -u)
    mkdir -p "$SN/1"
    [[ -n $cur ]] && { chown --reference=/.snapshots/$cur "$SN/1"; chmod --reference=/.snapshots/$cur "$SN/1"; chcon --reference=/.snapshots/$cur "$SN/1" 2>/dev/null; }
    btrfs -q subvolume create "$SN/1/snapshot" >/dev/null || skip "snapshots: subvolume 1/snapshot"
    chcon --reference=/ "$SN/1/snapshot" 2>/dev/null; chmod --reference=/ "$SN/1/snapshot"
    for f in $usr_files $etc_files /boot/grub2/grub.cfg /boot/grub2/grubenv; do
        [[ -e $f ]] && { cp -a --preserve=all --parents "$f" "$SN/1/snapshot/" 2>/dev/null || skip "snapshot 1: $f"; }
    done
    # In case the guest's /usr has no file with capabilities (openSUSE's
    # permissions may set none): one as a package's would be, ping's
    # cap_net_raw.
    if ! python3 -c 'import os, sys
sys.exit(not any("security.capability" in os.listxattr(os.path.join(d, n)) for d, _, ns in os.walk(sys.argv[1]) for n in ns
                 if not os.path.islink(os.path.join(d, n))))' "$SN/1/snapshot/usr"; then
        mkdir -p "$SN/1/snapshot/usr/bin"; cp -a --preserve=all /usr/bin/true "$SN/1/snapshot/usr/bin/bko-ping"
        try setcapraw "$SN/1/snapshot/usr/bin/bko-ping" v2 13; echo "fixture: snapshot 1: no file with capabilities in the guest's /usr: usr/bin/bko-ping given cap_net_raw" >&2
    fi
    if [[ -n $cur && -e /.snapshots/$cur/info.xml ]]; then cp -a --preserve=all "/.snapshots/$cur/info.xml" "$SN/1/" || skip "snapshot 1: info.xml"
    else printf '<?xml version="1.0"?>\n<snapshot>\n  <type>single</type>\n  <num>1</num>\n  <date>2026-11-14 15:40:00</date>\n  <description>first root filesystem</description>\n</snapshot>\n' >"$SN/1/info.xml"; fi
    info() {  # N TYPE [PRE]: snapshot N's info.xml, from 1's, as snapper writes a zypper pair's
        sed -e "s|<type>[a-z]*</type>|<type>$2</type>|" -e "s|<num>[0-9]*</num>|<num>$1</num>${3:+\n  <pre_num>$3</pre_num>}|" \
            -e "s|<description>.*</description>|<description>zypp(zypper)</description>\n  <cleanup>number</cleanup>\n  <userdata>\n    <key>important</key>\n    <value>no</value>\n  </userdata>|" \
            "$SN/1/info.xml" >"$SN/$1/info.xml"
        chmod 640 "$SN/$1/info.xml"; chcon --reference="$SN/1/info.xml" "$SN/$1/info.xml" 2>/dev/null
        touch -d "2026-11-14 16:5$1:00.5" "$SN/$1/info.xml"
    }
    mkdir -p "$SN/2" "$SN/3"
    btrfs -q subvolume snapshot -r "$SN/1/snapshot" "$SN/2/snapshot" >/dev/null || skip "snapshots: 1 -> 2 (pre)"
    info 2 pre
    mkdir -p "$SN/1/snapshot/usr/share"; echo "added by zypper's transaction" >"$SN/1/snapshot/usr/share/bko-zypper"
    chcon -t usr_t "$SN/1/snapshot/usr/share/bko-zypper" 2>/dev/null
    first=$(ls "$SN/1/snapshot/usr/bin" 2>/dev/null | grep -vx bko-ping | head -1); [[ -n $first ]] && rm -f "$SN/1/snapshot/usr/bin/$first"
    [[ -f $SN/1/snapshot/boot/grub2/grub.cfg ]] && echo "# rewritten by zypper's transaction" >>"$SN/1/snapshot/boot/grub2/grub.cfg"
    btrfs -q subvolume snapshot -r "$SN/1/snapshot" "$SN/3/snapshot" >/dev/null || skip "snapshots: 1 -> 3 (post)"
    info 3 post 2
    for n in 1 2 3; do [[ -d $SN/$n ]] && touch -d "2026-11-14 16:5$n:00" "$SN/$n"; done
    for s in 1/snapshot 2/snapshot 3/snapshot; do echo "$s $(btrfs property get -ts "$SN/$s" ro 2>&1)"; done >"$L/slots.txt"
    btrfs subvolume list -pqu "$R" >>"$L/slots.txt" 2>&1
    { btrfs subvolume list / ; btrfs subvolume get-default /; snapper --no-dbus list 2>&1; } >"$L/guest-subvolumes.txt" 2>&1
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
