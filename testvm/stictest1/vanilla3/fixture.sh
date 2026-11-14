#!/bin/bash
# The fixture for vanilla3 (no SELinux or AppArmor labels; IMA/EVM keep no
# xattrs here: docs/design/vanillaos.md, "What V1 found"), one file system
# at a time:
#   fixture.sh ROOT btrfs main   the present root (a btrfs like vos-b on a
#                                thin LV): under ROOT/up1/up2/tree the
#                                directories above, every checklist feature
#                                btrfs has (its compression property, c, m,
#                                C), the guest's own files from / (set-id,
#                                shadow, ABRoot's abroot.json files and
#                                /abimage.abr, fstab, systemd's links), and
#                                tree/etc: the lower of the /etc overlay
#   fixture.sh DIR btrfs future  the other root (vos-a, the same thin pool)
#                                at tree/part-future: the features again,
#                                the guest's other root's ABRoot files, its
#                                own etc/ lower
#   fixture.sh DIR btrfs var     /var (vos-var): the features again; the
#                                /etc overlay uppers lib/abroot/etc/vos-b
#                                and vos-a made through overlayfs itself over
#                                OVL_LOWER_B and OVL_LOWER_A (whiteouts, an
#                                opaque directory, a redirect, origin and
#                                impure xattrs, a copy-up with a user
#                                xattr); a containers-storage-like layer
#                                stack (whiteouts char 0:0, an opaque
#                                directory, a device node, link and lower
#                                files, l/ symlinks); real/: the guest's own
#                                uppers whole and its storage's whiteouts
#                                (nodecopy.py), random-seed, the journal
#   fixture.sh DIR ext4 boot     /boot (vos-boot): the features again, with
#                                ext4's own inode flags (D, j, T), and the
#                                guest's /boot (the master grub.cfg and
#                                grub.cfg.future, GRUB's modules)
#   fixture.sh DIR ext4 init     /boot/init (the linear init LV, vos-init):
#                                the features again and the guest's init
#                                LV's files (INITM: each root's abroot.cfg,
#                                kernel, config, System.map; not the
#                                initramfs images, ~100 MiB each)
#   fixture.sh DIR vfat          the ESP (vos-efi): FAT attributes, names,
#                                FAT's time limits, the guest's ESP's files
#                                (EFI/vanilla, EFI/BOOT) with their attributes
# ESPM, BOOTM: the guest's ESP and /boot mount points.  INITM: the guest's
# init LV mounted read-only; FUTM: its other root (vos-a) mounted
# read-only.  OVL_LOWER_B, OVL_LOWER_A: the lowers (var only).  FIXLOG:
# where its own logs go (default /tmp).  What can't
# be made is reported on stderr as "fixture: skipped: ..." (the judge
# fails on any).  setfattr is bkoshim.py on this guest (no attr package).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
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
    # The guest's own ESP (shim, GRUB, mm and fb in EFI/vanilla, and the
    # EFI/BOOT copy the guest build adds), with its times and FAT attributes.
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

if [[ $ROLE == main ]]; then
    R=$1; U1=$R/up1; U2=$U1/up2; T=$U2/tree
else
    T=$1
fi
mkdir -p "$T"

# An /etc lower as a root's (the image's /etc): plain files, one with a
# user xattr, a symlink, directories to be replaced, renamed and changed.
mklower() {  # DIR
    local E=$1
    mkdir -p "$E/replaced.d" "$E/olddir/y" "$E/sub" "$E/abroot"
    for f in keep modified chmod deleted keep-xattr; do echo "$f from the image" >"$E/$f.conf"; done
    echo a >"$E/replaced.d/a.conf"; echo b >"$E/replaced.d/b.conf"
    echo x >"$E/olddir/x.conf"; echo z >"$E/olddir/y/z.conf"
    echo deep >"$E/sub/deep.conf"; echo gone >"$E/sub/gone.conf"; echo stay >"$E/sub/stay.conf"
    echo '{"partLabelA": "vos-a"}' >"$E/abroot/abroot.json"
    ln -sfn keep.conf "$E/alias"
    try setfattr -n user.lower -v image "$E/keep.conf"
    chmod 0644 "$E"/*.conf
    find "$E" -exec touch -h -d '2026-08-24 10:00:00.123456789' {} +
}

# Real examples from the guest's own files.
mkdir -p "$T/real"
case $ROLE in
main)
    # Set-id binaries, shadow, the root's fstab (the lower's, Albius'
    # GenFstab) and os-release (a symlink), ABRoot's configuration and the
    # image's record, systemd's links, LVM's backups (in the upper).
    for f in /usr/bin/passwd /usr/bin/su /usr/bin/mount /usr/bin/newuidmap /usr/bin/chfn /etc/shadow /etc/fstab /etc/os-release \
             /usr/lib/os-release /abimage.abr /usr/share/abroot/abroot.json /etc/abroot/abroot.json /etc/abroot/kargs; do
        [[ -e $f || -L $f ]] && try cp -a --preserve=all "$f" "$T/real/$(echo "${f#/}" | tr / _)"
    done
    [[ -d /etc/systemd/system ]] && try cp -a --preserve=all /etc/systemd/system "$T/real/systemd-system"
    [[ -d /etc/lvm ]] && try cp -a --preserve=all /etc/lvm "$T/real/etc-lvm"
    mklower "$T/etc"
    ;;
future)
    # The other root's ABRoot files, read from it mounted read-only (FUTM).
    for f in abimage.abr usr/share/abroot/abroot.json etc/fstab usr/lib/os-release; do
        [[ -e ${FUTM:?}/$f ]] && try cp -a --preserve=all "$FUTM/$f" "$T/real/$(echo "$f" | tr / _)"
    done
    [[ -n $(ls "$T/real") ]] || skip "no files of the other root (FUTM $FUTM)"
    mklower "$T/etc"
    ;;
var)
    # The guest's own /etc uppers (and workdirs) whole, and its containers
    # storage's device nodes and trusted.* entries in their places with as
    # many of its whiteouts as fit in 600 directories (the node listing
    # reads each directory with a restic call: all ~7,000 are in ~31,000);
    # random-seed and the journal.
    mkdir -p "$T/real"
    python3 "$HERE/nodecopy.py" /var/lib/abroot/etc "$T/real/abroot-etc" >"${FIXLOG:-/tmp}/real-abroot-etc.txt" 2>&1 || skip "nodecopy /var/lib/abroot/etc"
    python3 "$HERE/nodecopy.py" --nodes 600 /var/lib/abroot/storage "$T/real/abroot-storage" >"${FIXLOG:-/tmp}/real-abroot-storage.txt" 2>&1 || skip "nodecopy --nodes /var/lib/abroot/storage"
    [[ -f /var/lib/systemd/random-seed ]] && try cp -a --preserve=all /var/lib/systemd/random-seed "$T/real/random-seed"
    [[ -d /var/log/journal ]] && try cp -a --preserve=all /var/log/journal "$T/real/journal"
    ;;
boot)
    # /boot's own mode, owner and times on this file system's root; its
    # files but the ESP's and the init LV's mount points and lost+found.
    chmod --reference="${BOOTM:?}" "$T"; chown --reference="$BOOTM" "$T"
    mkdir -p "$T/real/boot"
    for f in "$BOOTM"/*; do
        case ${f##*/} in efi | init | lost+found) continue ;; esac
        try cp -a --preserve=all "$f" "$T/real/boot/"
    done
    touch -r "$BOOTM" "$T/real/boot"
    ;;
init)
    # The init LV's own (each root's abroot.cfg, kernel, config, System.map).
    mkdir -p "$T/real/init"
    for d in "${INITM:?}"/vos-*; do
        [[ -d $d ]] || continue
        mkdir -p "$T/real/init/${d##*/}"
        for f in "$d"/*; do [[ ${f##*/} == initrd.img-* ]] || try cp -a --preserve=all "$f" "$T/real/init/${d##*/}/"; done
        touch -r "$d" "$T/real/init/${d##*/}"
    done
    [[ -n $(ls "$T/real/init") ]] || skip "no init LV files (INITM $INITM)"
    ;;
esac
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
head -c 256K /dev/urandom >"$T/sparse-small"; truncate -s +8K "$T/sparse-small"; head -c 256K /dev/urandom >>"$T/sparse-small"
head -c 4096 /dev/urandom >"$T/sparse-tail"; truncate -s 4099 "$T/sparse-tail"
truncate -s 64M "$T/sparse-allhole"
truncate -s 8M "$T/sparse-head"; echo end >>"$T/sparse-head"
fallocate -l 16M "$T/preallocated" || skip fallocate
head -c 8M /dev/zero >"$T/zeros"
if [[ $FS == btrfs ]]; then
    # (A swap file on btrfs must be nocow, made empty.)
    touch "$T/swapfile" && chattr +C "$T/swapfile" && fallocate -l 32M "$T/swapfile" && chmod 600 "$T/swapfile" && try mkswap -q "$T/swapfile"
else
    fallocate -l 32M "$T/swapfile" && chmod 600 "$T/swapfile" && try mkswap -q "$T/swapfile"
fi
head -c 300K /dev/urandom >"$T/random"

# Names: spaces, newline, not UTF-8, 255 bytes, deep, empty.
touch "$T/name with spaces" "$T/new"$'\n'"line" "$T/"$'\xff\xfe'"-not-utf8" "$T/$(printf 'n%.0s' {1..255})"
mkdir -p "$T/"$'\xe9'"-latin1-dir"; touch "$T/"$'\xe9'"-latin1-dir/f"
D="$T/deep$(printf '/level%s' {1..40})"; mkdir -p "$D"; touch "$D/bottom"
touch "$T/empty"; mkdir -p "$T/empty-dir"

if [[ $FS == btrfs && $ROLE == main ]]; then
    # Btrfs's per-file options on the present root: the compression
    # property on files and a directory (inherited); c, m, C.
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
fi

if [[ $ROLE == var ]]; then
    # The /etc overlay uppers, made by overlayfs: mounted as ABRoot's
    # mount-sys mounts /etc (lowerdir, upperdir, workdir), with
    # redirect_dir=on so that a renamed lower directory gets a redirect
    # (Vanilla's kernel defaults to redirect_dir=off: a real upper has none
    # until a user turns it on, but a restore must keep one), index off.
    # vos-b: a copy-up (trusted.overlay.origin, and impure on its parent), a
    # chmod's copy-up, a copy-up with a user xattr, whiteouts (char 0:0),
    # an opaque directory (removed and made again), a redirect (a renamed
    # directory), a new file, a whiteout in a copied-up directory.
    # vos-a: a copy-up, a whiteout, an opaque directory.
    ovl() {  # LOWER NAME OPS: mount, run OPS in the merged view, unmount
        local up=$T/lib/abroot/etc/$2 m=/mnt/bkovl-$2 rc=0
        mkdir -p "$up" "$up-work" "$m"
        mount -t overlay overlay -o "lowerdir=$1,upperdir=$up,workdir=$up-work,redirect_dir=on,index=off" "$m" 2>>"${FIXLOG:-/tmp}/ovl-mount.err" ||
            { skip "overlay mount over $1 ($(tail -1 "${FIXLOG:-/tmp}/ovl-mount.err"))"; return 1; }
        ( cd "$m" && eval "$3" ) 2>>"${FIXLOG:-/tmp}/ovl-ops.err" || { rc=1; skip "overlay ops on $2"; }
        umount "$m" && rmdir "$m"
        return $rc
    }
    ovl "${OVL_LOWER_B:?}" vos-b '
        echo "changed by the user" >>modified.conf
        chmod 0600 chmod.conf
        setfattr -n user.local -v changed keep-xattr.conf
        rm deleted.conf
        rm -r replaced.d && mkdir replaced.d && echo new >replaced.d/new.conf
        mv olddir newdir
        echo added >added.conf
        rm sub/gone.conf && echo "deep, changed" >sub/deep.conf
        ln -sfn added.conf alias
        mkdir -p new.d/inner && echo n >new.d/inner/n.conf'
    ovl "${OVL_LOWER_A:?}" vos-a '
        echo "changed in A" >>modified.conf
        rm deleted.conf
        rm -r replaced.d && mkdir replaced.d && echo newa >replaced.d/a-new.conf'
    # Each of what the judge relies on is there, as overlayfs made it.
    python3 - "$T/lib/abroot/etc" <<'PY' || skip "the overlay uppers lack what overlayfs should have made"
import os, stat, sys
U = sys.argv[1]
def x(p, n):
    try: return os.getxattr(os.path.join(U, p), n, follow_symlinks=False)
    except OSError: return None
def wh(p):
    try: st = os.lstat(os.path.join(U, p)); return stat.S_ISCHR(st.st_mode) and st.st_rdev == 0
    except OSError: return False
bad = []
for p in ("vos-b/deleted.conf", "vos-b/olddir", "vos-b/sub/gone.conf", "vos-a/deleted.conf"):
    if not wh(p): bad.append("no whiteout " + p)
for p in ("vos-b/replaced.d", "vos-a/replaced.d"):
    if x(p, "trusted.overlay.opaque") != b"y": bad.append("not opaque " + p)
if not x("vos-b/newdir", "trusted.overlay.redirect"): bad.append("no redirect on vos-b/newdir")
for p in ("vos-b/modified.conf", "vos-b/chmod.conf", "vos-a/modified.conf"):
    if not x(p, "trusted.overlay.origin"): bad.append("no origin on " + p)
if x("vos-b/keep-xattr.conf", "user.local") != b"changed": bad.append("no user.local on vos-b/keep-xattr.conf")
for p in bad: print(p, file=sys.stderr)
sys.exit(1 if bad else 0)
PY
    # A containers-storage-like layer stack (storage/graph/overlay, as
    # ABRoot's: layer ID/diff, link, lower, work; l/ short names): layer
    # 2 deletes files of layer 1 (whiteouts, char 0:0), makes a directory
    # opaque (trusted.overlay.opaque=y) and holds a device node.
    G=$T/lib/abroot/storage/graph/overlay
    L1=1111111111111111111111111111111111111111111111111111111111111111 L2=2222222222222222222222222222222222222222222222222222222222222222
    mkdir -p "$G/l" "$G/$L1/diff/etc" "$G/$L1/diff/usr/bin" "$G/$L1/diff/usr/share/doc/x" "$G/$L1/diff/dev" "$G/$L1/diff/opaque-me" "$G/$L1/work" \
             "$G/$L2/diff/etc" "$G/$L2/diff/usr/share/doc/x" "$G/$L2/diff/opaque-me" "$G/$L2/work"
    echo a >"$G/$L1/diff/etc/a.conf"; echo b >"$G/$L1/diff/etc/b.conf"; cp /usr/bin/true "$G/$L1/diff/usr/bin/tool"
    echo 1 >"$G/$L1/diff/usr/share/doc/x/1"; echo 2 >"$G/$L1/diff/usr/share/doc/x/2"
    mknod "$G/$L1/diff/dev/null" c 1 3; echo old1 >"$G/$L1/diff/opaque-me/old1"; echo old2 >"$G/$L1/diff/opaque-me/old2"
    mknod "$G/$L2/diff/etc/b.conf" c 0 0; mknod "$G/$L2/diff/usr/share/doc/x/1" c 0 0
    try setfattr -n trusted.overlay.opaque -v y "$G/$L2/diff/opaque-me"; echo new1 >"$G/$L2/diff/opaque-me/new1"
    echo c >"$G/$L2/diff/etc/c.conf"
    echo AAAAAAAAAAAAAAAAAAAAAAAAAA >"$G/$L1/link"; echo BBBBBBBBBBBBBBBBBBBBBBBBBB >"$G/$L2/link"; echo "l/AAAAAAAAAAAAAAAAAAAAAAAAAA" >"$G/$L2/lower"
    ln -s "../$L1/diff" "$G/l/AAAAAAAAAAAAAAAAAAAAAAAAAA"; ln -s "../$L2/diff" "$G/l/BBBBBBBBBBBBBBBBBBBBBBBBBB"
    chmod 0700 "$G/$L1" "$G/$L2"
    find "$G" -exec touch -h -d '2026-11-06 12:00:00.5' {} +
fi

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
case $ROLE in main | future | var | init) touch -d '2001-02-03 04:05:06' "$T" ;; *) touch -r "$BOOTM" "$T" ;; esac

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
