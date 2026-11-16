#!/bin/bash
# The fixture for uc24 (no SELinux; AppArmor keeps no xattrs: Ubuntu Core's
# profiles are by path), one file system at a time, each at its own root
# (bko backs up the four roots whole, docs/design/ubuntu-core.md):
#   fixture.sh DIR vfat seed     ubuntu-seed (vfat, the ESP type): FAT
#                                attributes, long and odd names, FAT's time
#                                limits; the guest's own seed (EFI/ with shim
#                                and GRUB, .disk/, the recovery systems
#                                systems/LABEL/ with their model and
#                                assertions, snaps/: the pool's .snap files
#                                up to SNAP_MAX_SEED bytes each) with their
#                                FAT attributes; squashfs .snap files made by
#                                mksquashfs (xz, gzip) under long names
#   fixture.sh DIR ext4 boot     ubuntu-boot: the checklist (ext4's own D, j,
#                                T among the flags) and the guest's own boot
#                                (EFI/ubuntu's grubenv, grub.cfg, the
#                                kernel.efi symlink and pc-kernel_N.snap/,
#                                device/model)
#   fixture.sh DIR ext4 save     ubuntu-save (32 MiB, 1 KiB blocks, as the
#                                gadget's): the checklist with smaller
#                                allocations, the guest's own save (device/,
#                                snap/, bko-mark) but its private keys
#   fixture.sh DIR ext4 data     ubuntu-data (inside LUKS2): the checklist
#                                and the guest's own data: system-data's
#                                snapd state (modeenv, the snaps up to
#                                SNAP_MAX_DATA bytes each, every revision of
#                                hello and its per-revision data in
#                                var/snap/hello, assertions), the writable
#                                etc but its host keys, extrausers,
#                                user-data (homes), bko-mark; squashfs .snap
#                                files made by mksquashfs
# SEEDM BOOTM SAVEM DATAM: the guest's own four roots (/run/mnt/...).
# FIXLOG: where its own logs go (default /tmp).  What can't be made is
# reported on stderr as "fixture: skipped: ..." (the judge fails on any).
# setfattr is bkoshim.py on this guest (the base has no attr package).
set -u
FS=$2 ROLE=${3:-}
SNAP_MAX_SEED=${SNAP_MAX_SEED:-16777216} SNAP_MAX_DATA=${SNAP_MAX_DATA:-41943040}
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
# Squashfs .snap files as snapd's pack makes them (mksquashfs -noappend
# -comp xz -no-fragments -all-root), and one gzip'd with fragments and
# xattrs: a snap.yaml, a binary, a symlink, a directory tree.
mksnap() {  # OUT COMP NAME
    local t; t=$(mktemp -d) || return 1
    mkdir -p "$t/meta" "$t/bin" "$t/share/doc/$3"
    printf 'name: %s\nversion: "1.0"\nsummary: BackOtter test snap\nbase: core24\napps:\n  %s:\n    command: bin/%s\n' "$3" "$3" "$3" >"$t/meta/snap.yaml"
    cp /usr/bin/true "$t/bin/$3"; ln -s "$3" "$t/bin/$3-alias"
    head -c 65536 /dev/urandom >"$t/share/doc/$3/random"; yes "BackOtter $3" | head -c 200000 >"$t/share/doc/$3/text"
    touch -d '2026-11-14 12:00:00' "$t" "$t"/* "$t"/*/*
    if [[ $2 == xz ]]; then mksquashfs "$t" "$1" -noappend -comp xz -no-fragments -all-root -no-progress -quiet >/dev/null 2>&1
    else setfattr -n user.bko -v snap "$t/meta/snap.yaml" 2>/dev/null
        mksquashfs "$t" "$1" -noappend -comp gzip -all-root -xattrs -no-progress -quiet >/dev/null 2>&1; fi
    local rc=$?
    rm -rf "$t"
    [[ $rc == 0 && -s $1 ]] || skip "mksquashfs $2 $1"
}
# The guest's own files: SRC's tree into DST with every file of at most MAX
# bytes and the paths not matching EXCLUDE (an extended regex over the path
# below SRC), with owners, modes, times and xattrs (GNU tar); the files
# left out are listed in FIXLOG/real-NAME-left-out.txt.
realcopy() {  # NAME SRC DST MAX EXCLUDE
    local n=$1 s=$2 d=$3 max=$4 ex=$5 lst
    lst=$(mktemp)
    ( cd "$s" && find . -mindepth 1 -print0 | python3 -c '
import os, re, sys
mx, ex, left = int(sys.argv[1]), re.compile(sys.argv[2]), open(sys.argv[3], "w")
for p in sys.stdin.buffer.read().split(b"\0"):
    if not p: continue
    q = os.fsdecode(p)[2:]
    st = os.lstat(p)
    if ex.search(q) or (os.path.isfile(p) and not os.path.islink(p) and st.st_size > mx):
        left.write(f"{q} {st.st_size}\n"); continue
    sys.stdout.buffer.write(p + b"\0")' "$max" "$ex" "${FIXLOG:-/tmp}/real-$n-left-out.txt" >"$lst" ) ||
        { skip "the list of $s"; rm -f "$lst"; return 1; }
    mkdir -p "$d"
    ( cd "$s" && tar --null --no-recursion -T "$lst" --xattrs --xattrs-include='*' --acls -cpf - ) 2>"${FIXLOG:-/tmp}/real-$n-tar.err" |
        tar --xattrs --xattrs-include='*' --acls -xpf - -C "$d" 2>>"${FIXLOG:-/tmp}/real-$n-tar.err" || skip "the copy of $s"
    echo "$(tr -cd '\0' <"$lst" | wc -c) paths of $s copied, $(wc -l <"${FIXLOG:-/tmp}/real-$n-left-out.txt") left out" >"${FIXLOG:-/tmp}/real-$n.txt"
    rm -f "$lst"
}
N=0xffffffff

if [[ $FS == vfat ]]; then
    T=$1
    mkdir -p "$T" && cd "$T" || exit 1
    # The guest's own seed, with its times and FAT attributes: everything
    # but the pool's big snaps (pc-kernel, core24, snapd ...: the list is
    # in real-seed-left-out.txt) and the recovery systems' kernels.
    python3 - "${SEEDM:?}" . "$SNAP_MAX_SEED" "${FIXLOG:-/tmp}/real-seed-left-out.txt" <<'PY' 2>"${FIXLOG:-/tmp}/real-seed.err" || skip "copy of $SEEDM"
import fcntl, os, shutil, sys
a, b, mx, left = sys.argv[1], sys.argv[2], int(sys.argv[3]), open(sys.argv[4], "w")
dirs, n = [], 0
for dp, dns, fns in os.walk(a):
    rel = os.path.relpath(dp, a)
    os.makedirs(os.path.join(b, rel), exist_ok=True)
    dirs.append(rel)
    for f in fns:
        p = os.path.join(dp, f); q = os.path.join(b, rel, f)
        if os.path.getsize(p) > mx: left.write(f"{os.path.join(rel, f)} {os.path.getsize(p)}\n"); continue
        shutil.copy2(p, q); n += 1
        fd = os.open(p, os.O_RDONLY); v = fcntl.ioctl(fd, 0x80047210, b"\0" * 4); os.close(fd)
        fd = os.open(q, os.O_RDONLY); fcntl.ioctl(fd, 0x40047211, v); os.close(fd)
for rel in reversed(dirs):
    p, q = os.path.join(a, rel), os.path.join(b, rel)
    if rel != ".":
        fd = os.open(p, os.O_RDONLY); v = fcntl.ioctl(fd, 0x80047210, b"\0" * 4); os.close(fd)
        fd = os.open(q, os.O_RDONLY); fcntl.ioctl(fd, 0x40047211, v); os.close(fd)
    st = os.stat(p); os.utime(q, ns=(st.st_atime_ns, st.st_mtime_ns))
print(f"{n} files of the seed copied", file=sys.stderr)
PY
    [[ -f EFI/boot/bootx64.efi || -f EFI/BOOT/BOOTX64.EFI ]] || skip "the seed's shim (EFI/boot/bootx64.efi)"
    ls -d systems/*/model >/dev/null 2>&1 || skip "the seed's recovery systems (systems/*/model)"
    mkdir -p snaps
    mksnap snaps/bko-squash-xz_1.snap xz bko-squash-xz; mksnap snaps/bko-squash-gzip-with-fragments_2.snap gzip bko-squash-gzip
    echo h >hidden; fat hidden 0x22
    echo s >system; fat system 0x24
    echo hs >hidden-system-noarchive; fat hidden-system-noarchive 0x06
    echo n >archive-cleared; fat archive-cleared 0
    echo a >archive-set; fat archive-set 0x20
    echo r >readonly-chmod; chmod a-w readonly-chmod
    echo r >readonly-attr; fat readonly-attr 0x21
    mkdir hidden-dir system-dir readonly-dir; echo in >hidden-dir/f; echo in >readonly-dir/f
    fat hidden-dir 0x12; fat system-dir 0x14; fat readonly-dir 0x11
    : >"name with spaces"; : >MixedCase.Txt; : >UPPER.TXT; : >"a+b,c;d=e[f]"; : >.dotfile
    : >"$(head -c 200 /dev/zero | tr '\0' n)"
    : >"a long file name the 8.3 short name can't hold, seed-like.snap"
    mkdir -p EFI/BKO; head -c 307200 /dev/urandom >EFI/BKO/bkox64.efi; head -c 20971520 /dev/urandom >big
    d=deep; for i in $(seq 30); do d=$d/level$i; done; mkdir -p $d; echo bottom >$d/bottom
    : >empty; mkdir empty-dir
    mkdir big-dir; for i in $(seq 300); do : >"big-dir/entry-$i-padding-padding"; done
    touch -d 1980-01-01T00:00:00 fat-min; touch -d 2107-12-31T23:59:58 fat-max
    touch -d 2001-02-03T04:05:08 even-second
    echo atime >atime; touch -m -d 2002-03-04T05:06:08 atime; touch -a -d 2003-04-05T00:00:00 atime
    touch -m -d 2002-03-04T05:06:08 readonly-dir hidden-dir; touch -a -d 2003-04-05T00:00:00 readonly-dir hidden-dir
    exit 0
fi

T=$1
mkdir -p "$T"

# Real examples from the guest's own files, at their own paths.
case $ROLE in
boot)
    realcopy boot "${BOOTM:?}" "$T" 67108864 '^lost\+found(/|$)'
    [[ -L $T/EFI/ubuntu/kernel.efi ]] || skip "the run kernel's symlink (ubuntu-boot/EFI/ubuntu/kernel.efi)"
    ;;
save)
    # (device/private-keys-v1: the device's serial keys; device/fde: the
    # sealed keys' markers on an FDE install, none on uc24.)
    realcopy save "${SAVEM:?}" "$T" 1048576 '^lost\+found(/|$)|(^|/)private-keys-v1(/|$)'
    ;;
data)
    # (state.json and the ssh host keys stay out: snapd's macaroons and the
    # guest's private keys; private-keys-v1: the serial's keys.)
    realcopy data "${DATAM:?}" "$T" "$SNAP_MAX_DATA" \
        '^lost\+found(/|$)|^system-data/var/lib/snapd/state\.json|(^|/)private-keys-v1(/|$)|^system-data/etc/ssh/ssh_host_[a-z0-9]+_key$|^system-data/var/(log|cache|tmp)(/|$)|^system-data/var/lib/snapd/(cache|cookie|seccomp|apparmor|mount|inhibit|sequence)(/|$)|^system-data/var/lib/(cloud|systemd/coredump)(/|$)|^system-data/snap(/|$)'
    [[ -f $T/system-data/var/lib/snapd/modeenv ]] || skip "the guest's modeenv"
    ls "$T"/system-data/var/lib/snapd/snaps/*.snap >/dev/null 2>&1 || skip "the guest's snaps (system-data/var/lib/snapd/snaps)"
    ls -d "$T"/system-data/var/snap/hello/[0-9]* >/dev/null 2>&1 || skip "hello's per-revision data (system-data/var/snap/hello)"
    mkdir -p "$T/system-data/var/lib/snapd/snaps"
    mksnap "$T/system-data/var/lib/snapd/snaps/bko-squash-xz_1.snap" xz bko-squash-xz
    mksnap "$T/system-data/var/lib/snapd/snaps/bko-squash-gzip_2.snap" gzip bko-squash-gzip
    # A state.json of snapd's shape (the guest's own holds its macaroons).
    printf '{"data":{"snaps":{"bko-squash-xz":{"type":"app","sequence":{"revisions":[{"snap":{"name":"bko-squash-xz","revision":"1"}}]},"current":"1"}}}}\n' \
        >"$T/system-data/var/lib/snapd/state.json"
    ;;
esac
mkdir -p "$T/real/tmp-like"; chmod 1777 "$T/real/tmp-like"
# (ubuntu-save is 32 MiB with 1 KiB blocks: its allocations are smaller.)
if [[ $ROLE == save ]]; then PRE=2M ZER=1M SWP=4M; else PRE=16M ZER=8M SWP=32M; fi

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
# files, dirs and symlinks (trusted only: Linux keeps user.* off symlinks).
touch "$T/xattr-file"
try setfattr -n user.backotter -v hello "$T/xattr-file"
try setfattr -n user.binary -v 0x00ff10fe7f00 "$T/xattr-file"
try setfattr -n user.empty -v "" "$T/xattr-file"
try setfattr -n trusted.backotter -v root-only "$T/xattr-file"
try setfattr -n security.bko -v sec-value "$T/xattr-file"
for i in $(seq 1 40); do setfattr -n "user.many$i" -v "v$i" "$T/xattr-file" 2>/dev/null || skip "user.many$i"; done
touch "$T/xattr-large"; try setfattr -n user.large -v "$(head -c 3000 /dev/zero | tr '\0' x)" "$T/xattr-large"
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

# Device nodes (rdev 0, big numbers), FIFO, a socket (neither tool keeps it).
mknod "$T/chardev" c 1 3; mknod "$T/blockdev" b 7 0; mknod "$T/chardev-rdev0" c 0 0
mknod "$T/chardev-big" c 511 1048575 2>/dev/null || mknod "$T/chardev-big" c 259 65536
mkfifo "$T/fifo"
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
fallocate -l $PRE "$T/preallocated" || skip fallocate
head -c $ZER /dev/zero >"$T/zeros"
fallocate -l $SWP "$T/swapfile" && chmod 600 "$T/swapfile" && try mkswap -q "$T/swapfile"
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

# Inode flags (chattr): sync, noatime, nodump, append, immutable; on a
# directory too.  ext4's own: dirsync (D), data journaling (j), the Orlov
# top directory (T).  Last: an immutable file can't change afterwards.
touch "$T/sync"; flag S "$T/sync"
touch "$T/noatime"; flag A "$T/noatime"
touch "$T/nodump"; flag d "$T/nodump"
mkdir -p "$T/nodump-dir"; flag d "$T/nodump-dir"
mkdir -p "$T/dirsync-dir"; flag D "$T/dirsync-dir"
echo j >"$T/journal-data"; flag j "$T/journal-data"
mkdir -p "$T/topdir"; flag T "$T/topdir"
echo append >"$T/append-only"; flag a "$T/append-only"
echo fixed >"$T/immutable"; flag i "$T/immutable"
echo both >"$T/immutable-nodump"; chattr +d "$T/immutable-nodump"; flag i "$T/immutable-nodump"
mkdir -p "$T/immutable-dir"; echo in >"$T/immutable-dir/f"; flag i "$T/immutable-dir"
echo fixed2 >"$T/real/immutable-linked"; ln "$T/real/immutable-linked" "$T/immutable-linked2"; flag i "$T/immutable-linked2"
# Each root's own mode, owner and times: the guest's root of the same role.
case $ROLE in
boot) chmod --reference="$BOOTM" "$T"; chown --reference="$BOOTM" "$T"; touch -r "$BOOTM" "$T" ;;
save) chmod --reference="$SAVEM" "$T"; chown --reference="$SAVEM" "$T"; touch -r "$SAVEM" "$T" ;;
data) chmod --reference="$DATAM" "$T"; chown --reference="$DATAM" "$T"; touch -d '2001-02-03 04:05:06' "$T" ;;
esac
exit 0
