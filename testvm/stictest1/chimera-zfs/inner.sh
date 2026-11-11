#!/bin/sh
# SticTest1 chimera-zfs, the part that runs in the throwaway guest, as root,
# from /root/c4 (restic-bko, rustic-bko, fsmeta.py, rt-common.sh beside it).
# Derived from ../chimera/inner.sh (chimera's is one script, not a library,
# so this is a copy): ZFS in place of XFS, and the ESP's vfat.
# POSIX sh and Chimera's userland: FreeBSD's sh and tools (chimerautils:
# stat -f, no stat -c, no date -d, sed -i takes a suffix), util-linux,
# e2fsprogs, dosfstools, OpenZFS, and attr-progs, acl-progs, libcap-progs
# (check.sh installs those three).  No bash.
#
# The fixture on ZFS datasets made in the guest's own pool with its root
# dataset's properties (xattr=sa, acltype=posix, dnodesize=auto,
# relatime...), on loop ext4 made like /boot (tune2fs's features) and on
# loop vfat made like the ESP (/boot/efi's mount options, FAT type,
# cluster size); one pass each: restic->restic, rustic->rustic,
# restic->rustic, rustic->restic into fresh file systems, each tool
# restoring once more over its own restore with changed immutable/
# append-only files (ZFS, ext4), ZFS's DOS attributes and project IDs, FAT's
# attributes, each tool's restore onto a dataset with directory xattrs
# (xattr=on) and zstd, the two tools' snapshots compared node by node, each
# tool's check --read-data of the other's repository; then one non-root run
# on ZFS.  Prints PASS/FAIL/LIMIT lines.
set -u
C=/root/c4
cd $C || exit 2
R=$C/restic-bko U=$C/rustic-bko
PFX=${PFX:-c4z}
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare LC_ALL=C
RT_TMP=$C/rt; mkdir -p $RT_TMP; export RT_TMP
. $C/rt-common.sh
W=$C/w; mkdir -p $W
pass() { echo "PASS $1: $2"; }
fail() { echo "FAIL $1: $2"; }
limit() { echo "LIMIT $1: $2"; }
oneline() { tr '\n' ' ' | cut -c1-${1:-600}; }
n() { echo $(( $(wc -l <"$1") )); }  # BSD wc pads its count
TS_M=2002-03-04T05:06:07.111111111 TS_A=2003-04-05T06:07:08.222222222

# Python helpers: FAT attributes (FAT_IOCTL_GET/SET_ATTRIBUTES) and ZFS's
# own per-file options (DOS attributes, ZFS_IOC_GET/SETDOSFLAGS; project ID
# and xflags, FS_IOC_FSGETXATTR).
cat >$C/fatattr.py <<'PY'
"""fatattr.py ROOT: every path's FAT attributes under ROOT, one line each."""
import fcntl, os, struct, sys
root = sys.argv[1]
for dp, dns, fns in sorted(os.walk(root)):
    dns.sort()
    for n in ([""] if dp == root else []) + sorted(dns + fns):
        p = os.path.join(dp, n) if n else dp
        try:
            fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
            try: v = "%#x" % struct.unpack("I", fcntl.ioctl(fd, 0x80047210, b"\0" * 4))[0]
            finally: os.close(fd)
        except OSError as e: v = "error %d" % e.errno
        sys.stdout.buffer.write(os.fsencode(os.path.relpath(p, root)) + b" " + v.encode() + b"\n")
PY
cat >$C/zfsx.py <<'PY'
"""zfsx.py ROOT PATH...: each PATH's ZFS DOS attributes (the ones BackOtter
keeps, and archive), project ID and xflags, one line each."""
import fcntl, os, struct, sys
root = sys.argv[1]
for p in sys.argv[2:]:
    fd = os.open(os.path.join(root, p), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        b = bytearray(8); fcntl.ioctl(fd, 0x80088301, b)
        dos = int.from_bytes(b, "little") & (0x302f00000000 | 0x800000000)
        x = bytearray(28); fcntl.ioctl(fd, 0x801c581f, x)
        xf, _, _, pj, _ = struct.unpack("IIIII", bytes(x[:20]))
        print(p, "dos=%#x" % dos, "projid=%d" % pj, "xflags=%#x" % xf)
    except OSError as e: print(p, "error", e)
    finally: os.close(fd)
PY
fatset() {  # PATH ATTRS
    python3 -c 'import fcntl, os, struct, sys
fd = os.open(sys.argv[1], os.O_RDONLY); fcntl.ioctl(fd, 0x40047211, struct.pack("I", int(sys.argv[2], 0))); os.close(fd)' "$@" ||
        echo "FAT attributes $2 refused on $1"
}
dosflags() {  # PATH VALUE: ZFS_IOC_SETDOSFLAGS (replaces the whole set)
    python3 -c 'import fcntl, os, struct, sys
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
fcntl.ioctl(fd, 0x40088302, struct.pack("Q", int(sys.argv[2], 0)))' "$@" || echo "DOS flags $2 refused on $1"
}

# --- What the guest has --------------------------------------------------
POOL=$(zfs list -H -o name / | cut -d/ -f1) ROOTDS=$(zfs list -H -o name /)
echo "== guest: $(uname -r); $(. /etc/os-release; echo "$PRETTY_NAME ($ID)"); sh $(readlink -f /bin/sh); musl $(ls /usr/lib/ld-musl* 2>/dev/null); $(zfs version | oneline)"
echo "== mounts: $(findmnt -n -o TARGET,SOURCE,FSTYPE,OPTIONS --target / | oneline); $(findmnt -n -o TARGET,SOURCE,FSTYPE,OPTIONS --target /boot); $(findmnt -n -o TARGET,SOURCE,FSTYPE,OPTIONS --target /boot/efi)"
echo "== LSMs: $(cat /sys/kernel/security/lsm)"
echo "== tools: $(apk info -e attr-progs acl-progs libcap-progs zfs e2fsprogs dosfstools 2>&1 | oneline)"
# Without them the fixture lacks what they set, and fsmeta still finds the
# restores equal to it: their absence is a FAIL, not a smaller fixture.
TOOLS="setfattr getfattr setfacl getfacl setcap getcap chattr lsattr zfs zpool mkfs.ext4 tune2fs mkfs.vfat python3"
miss=; for t in $TOOLS; do command -v $t >/dev/null 2>&1 || miss="$miss $t"; done
if [ -z "$miss" ]; then pass "tools-present" "$TOOLS"
else fail "tools-present" "not in the guest:$miss"; fi
rv=$($R version 2>&1 | head -n 1); uv=$($U --version 2>&1 | head -n 1)
echo "== versions: $rv / $uv"
file $R $U 2>&1
if $R version >/dev/null 2>&1 && $U --version >/dev/null 2>&1; then
    pass "tools-run" "restic-bko and rustic-bko run on Chimera (musl, $(uname -r), $(zfs version | head -n 1)): $rv; $uv"
else fail "tools-run" "restic-bko version or rustic-bko --version failed: $rv; $uv"; fi

# The root dataset's properties that bear on metadata; a scratch dataset
# gets those that aren't the default (local or inherited), and the three
# fixed at creation as they are.
ZP=xattr,acltype,aclinherit,aclmode,dnodesize,compression,recordsize,checksum,atime,relatime,normalization,utf8only,casesensitivity,devices,setuid,exec,snapdir,encryption
zprops() { zfs get -H -o property,value $ZP "$1" | tr '\t' '=' | tr '\n' ' '; }
zopts=$(zfs get -H -o property,value,source $ZP "$ROOTDS" | awk -F'\t' '
    $1 ~ /^(normalization|utf8only|casesensitivity)$/ || ($3 != "default" && $3 != "-") { printf "-o %s=%s ", $1, $2 }')
echo "== $ROOTDS: $(zprops $ROOTDS); zfs create $zopts"
echo "== $POOL features: $(zpool get -H -o property,value all $POOL | awk -F'\t' '$1 ~ /^feature@/ && $2 != "disabled" { sub("feature@", "", $1); printf "%s ", $1 }')"
# ext4 features of /boot (tune2fs), as mkfs.ext4's.
extfeat() { tune2fs -l "$1" | sed -n 's/^Filesystem features: *//p' | tr ' ' '\n' | grep -v -e '^needs_recovery$' -e '^orphan_present$' -e '^$' | sort | tr '\n' ' '; }
bootdev=$(findmnt -n -o SOURCE /boot)
bootfeat=$(extfeat "$bootdev")
bootisz=$(tune2fs -l "$bootdev" | sed -n 's/^Inode size: *//p')
extO=$(echo "$bootfeat" | tr ' ' '\n' | grep . | grep -v -e '^has_journal$' | tr '\n' ',' | sed 's/,$//')
echo "== /boot ext4: $bootfeat inode size $bootisz; mkfs.ext4 -O $extO"
# The ESP: its mount options (but those every mount has), FAT type, cluster.
espdev=$(findmnt -n -o SOURCE /boot/efi)
esp_opts() { findmnt -rn -o OPTIONS "$1" | tr , '\n' | grep -v -E '^(rw|ro|relatime|noatime|strictatime|nosuid|nodev|noexec)$' | tr '\n' , | sed 's/,$//'; }
fatenv() {  # MOUNTPOINT DEV
    echo "$(esp_opts "$1") $(blkid -p -o value -s VERSION "$2") $(fsck.fat -n -v "$2" 2>/dev/null | grep -E 'bytes per (cluster|logical sector)' | sed 's/^ *//' | tr '\n' ' ')"
}
EOPTS=$(esp_opts /boot/efi)
espenv=$(fatenv /boot/efi "$espdev")
espspc=$(fsck.fat -n -v "$espdev" 2>/dev/null | sed -n 's/^ *\([0-9]*\) bytes per cluster.*/\1/p')
echo "== /boot/efi vfat: $espenv"
mkfs_ext4() { mkfs.ext4 -q -F -b 4096 -I "$bootisz" -O "$extO" "$1"; }
mkfs_vfat() { mkfs.vfat -F 32 -S 512 -s $(( ${espspc:-4096} / 512 )) -n BKOESP "$1"; }
mkimg() {  # FS NAME [SIZE_MIB] [PROP=VALUE...]: a fresh file system at /mnt/NAME
    # (ZFS: a dataset with the root's properties, but the PROP=VALUEs given)
    fs=$1 nm=$2 sz=${3:-}; shift 2; [ $# -gt 0 ] && shift
    mkdir -p "/mnt/$nm"
    if [ $fs = zfs ]; then
        zo=$zopts
        for kv; do zo=$(echo "$zo" | sed "s/-o ${kv%%=*}=[^ ]* //")"-o $kv "; done
        # shellcheck disable=SC2086
        zfs create $zo -o mountpoint=/mnt/$nm "$POOL/$PFX-$nm" >$W/mkfs-$nm.log 2>&1 ||
            { echo "zfs create $nm: $(oneline <$W/mkfs-$nm.log)"; return 1; }
        return 0
    fi
    img=$W/$nm.img
    rm -f -- "$img"; truncate -s "${sz:-$([ $fs = vfat ] && echo 400 || echo 640)}M" "$img"
    mkfs_$fs "$img" >$W/mkfs-$nm.log 2>&1 || { echo "mkfs.$fs $nm: $(oneline <$W/mkfs-$nm.log)"; return 1; }
    if [ $fs = vfat ]; then mount -o loop${EOPTS:+,$EOPTS} "$img" "/mnt/$nm"
    else mount -o loop "$img" "/mnt/$nm"; fi
}
rmimg() {  # NAME
    if zfs list -H -o name "$POOL/$PFX-$1" >/dev/null 2>&1; then zfs destroy -r -f "$POOL/$PFX-$1"
    else umount "/mnt/$1" 2>/dev/null; rm -f -- "$W/$1.img"; fi
    rmdir "/mnt/$1" 2>/dev/null
}
noatime() {  # NAME: no atime updates from here on (fsmeta reads every file)
    if zfs list -H -o name "$POOL/$PFX-$1" >/dev/null 2>&1; then zfs set atime=off "$POOL/$PFX-$1"
    else mount -o remount,noatime "/mnt/$1"; fi
}

# ZFS's own per-file options: the DOS attributes (linux.zfs_dos_flags),
# project IDs and their inheritance (linux.project_id, P), next to the
# inode flags they share the ioctls with.
ZX="dos-hidden-system dos-readonly dos-noarchive dos-nounlink dos-offline-sparse dos-hidden-dir dos-hidden-immutable dos-plain projid-file projid-dir projid-dir/inherits immutable append-only nodump"
# COV_JSON KIND,KIND...: the kinds fsmeta's coverage counts none of.
covcheck() { python3 -c 'import json,sys; c=json.load(open(sys.argv[1]))["coverage"]; print(", ".join(k for k in sys.argv[2].split(",") if not c.get(k)))' "$1" "$2"; }
# A directory's own mode, owner, times (FreeBSD stat), xattrs and ACL.
dirmeta() { for d; do stat -f '%N %Lp %u %g %Fm %Fa' "$d"; getfattr -h -d -m - -e hex "$d" 2>/dev/null | grep -v '^#' | grep . | sort; done | oneline 900; }

fixture() {  # DIR FS: the fixture in DIR, on a file system of type FS (zfs, ext4)
    T=$1 fs=$2
    mkdir -p "$T" && cd "$T" || return 1
    # Owners with no name, set-id and sticky modes.
    : >no-such-user; chown 1234:5678 no-such-user
    : >bigid; chown 70000:70001 bigid
    : >setuid; chown 4321:4322 setuid; chmod 4755 setuid
    : >setgid; chmod 2755 setgid
    mkdir sticky-dir setgid-dir; chmod 1777 sticky-dir; chown 4321:4322 setgid-dir; chmod 2775 setgid-dir
    # The guest's own set-id files, copied with their modes.
    mkdir real-setid
    find / -xdev \( -perm -4000 -o -perm -2000 \) -type f 2>/dev/null | head -n 5 | while read -r f; do cp -p "$f" real-setid/; done
    echo "real set-id files: $(ls real-setid | oneline)"
    # POSIX ACLs, access and default, and a file that inherited the default.
    : >acl-file; setfacl -m u:1234:rwx,g:5678:r acl-file
    mkdir acl-dir; setfacl -m u:1234:rx acl-dir; setfacl -d -m u:1234:rwx,g:5678:rx acl-dir
    : >acl-dir/inherits; mkdir acl-dir/subdir
    # xattrs: user (empty, binary, large), trusted, security; files, dirs, symlinks.
    : >xattr-file
    setfattr -n user.backotter -v hello xattr-file
    setfattr -n user.empty xattr-file
    setfattr -n user.binary -v 0x00ff10fe7f xattr-file
    setfattr -n user.large -v "$(head -c 3000 /dev/zero | tr '\0' x)" xattr-file
    setfattr -n trusted.backotter -v root-only xattr-file
    setfattr -n security.backotter -v sec-label xattr-file || echo "security.backotter refused on $fs"
    mkdir xattr-dir; setfattr -n user.dir -v dirval xattr-dir; setfattr -n trusted.dir -v 0x00ff01 xattr-dir
    # A user xattr bigger than ZFS's system-attribute space (xattr=sa spills
    # it to a directory xattr object), and many small ones.
    : >xattr-spill; setfattr -n user.big -v "$(head -c 60000 /dev/zero | tr '\0' z)" xattr-spill || echo "60000-byte user xattr refused on $fs"
    : >xattr-many; for i in $(seq 40); do setfattr -n user.x$i -v v$i xattr-many; done
    # File capabilities: the guest's own (ping, clockdiff, if it has them),
    # one set here, and a raw one.
    mkdir real-caps
    getcap -r /usr/bin 2>/dev/null | while read -r f c; do
        cp -p "$f" real-caps/ && setcap "$c" "real-caps/$(basename "$f")" && echo "guest capability: $f $c"
    done
    cp /usr/bin/true cap-binary; setcap cap_net_raw,cap_net_admin+ep cap-binary || echo "setcap refused on $fs"
    : >cap-root-ns; setfattr -n security.capability -v 0x0100000200200000000000000000000000000000 cap-root-ns
    # Hard links (and the guest's own link group, if it has one).
    echo linked >hardlink-a; ln hardlink-a hardlink-b; mkdir sub; ln hardlink-a sub/hardlink-c
    mkdir real-links
    hlf=$(find /usr -xdev -type f -links +1 2>/dev/null | head -n 1)
    if [ -n "$hlf" ]; then
        # Numbered: the group's names may be one basename in two directories.
        first= i=0
        for f in $(find / -xdev -inum "$(stat -f %i "$hlf")" 2>/dev/null); do
            i=$((i + 1))
            if [ -z "$first" ]; then first=real-links/$i-$(basename "$f"); cp -p "$f" "$first"
            else ln "$first" "real-links/$i-$(basename "$f")"; fi
            echo "  guest link $i: $f"
        done
        echo "guest hard-link group: $hlf, $(stat -f %l "$hlf") links ($(ls real-links | oneline))"
    else echo "guest hard-link group: none under /usr"; fi
    # Symlinks, with their own xattrs and times.
    ln -s hardlink-a symlink; ln -s /nonexistent/target symlink-dangling; ln -s ../xattr-dir sub/symlink-to-dir
    ln -s "$(head -c 900 /dev/zero | tr '\0' y)" symlink-long
    ln -s hardlink-a symlink-xattr; setfattr -h -n trusted.onlink -v yes symlink-xattr
    # Devices (rdev 0 too), FIFOs, a socket (both tools skip sockets).
    mknod chardev c 1 3; mknod blockdev b 7 0; mknod rdev0 c 0 0; mknod rdev0-b b 0 0; mkfifo fifo; mkfifo -m 600 fifo600
    python3 -c 'import socket,sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])' socket
    # Sparse files (ZFS makes holes of runs of zeros too).
    truncate -s 64M sparse; printf start | dd of=sparse conv=notrunc status=none
    printf middle | dd of=sparse bs=1048576 seek=32 conv=notrunc status=none
    truncate -s 8M all-hole
    truncate -s 16M data-at-end; head -c 4096 /dev/urandom | dd of=data-at-end bs=4096 seek=4095 conv=notrunc status=none
    head -c 262144 /dev/urandom >sparse-small; truncate -s +8192 sparse-small; head -c 262144 /dev/urandom >>sparse-small
    head -c 4194304 /dev/urandom >random
    fallocate -l 4M preallocated 2>/dev/null || echo "fallocate refused on $fs"; head -c 2097152 /dev/zero >zeros
    # Odd names, deep, empty and big directories.
    : >"name with spaces"; : >"$(printf 'new\nline')"; : >"$(printf '\377\376-not-utf8')"; : >"$(printf 'utf8-\303\251')"
    : >"$(head -c 250 /dev/zero | tr '\0' n)"
    d=deep; for i in $(seq 40); do d=$d/level$i; done; mkdir -p $d; echo bottom >$d/bottom
    mkdir empty-dir; : >empty
    mkdir big-dir; for i in $(seq 600); do : >"big-dir/entry-$i-padding-padding-padding"; done
    # Project IDs (ZFS with project_quota; ext4 only with its project feature).
    echo project >projid-file; chattr -p 42 projid-file 2>/dev/null || echo "chattr -p refused on $fs"
    mkdir projid-dir; chattr +P -p 43 projid-dir 2>/dev/null || echo "chattr +P -p refused on $fs"
    : >projid-dir/inherits
    # Times, deepest first (BSD touch: -d YYYY-MM-DDThh:mm:SS.frac), and an
    # old and a far-future one; then the flags that would block them.
    touch -d 1970-01-02T00:00:00.123456789 old; touch -d 2100-01-01T12:00:00.987654321 future
    echo atime >atime; mkdir atime-dir; ln -s atime atime-symlink
    : >nodump; : >noatime; : >sync; mkdir flagdir dirsync-dir
    # ext4's topdir (T) and data journalling (j).
    [ "$fs" = ext4 ] && { mkdir topdir; echo journalled >journal-data; }
    echo append >append-only; echo fixed >immutable; mkdir immutable-dir; echo in >immutable-dir/child
    : >immutable-nodump; mkdir append-dir; echo in >append-dir/child
    if [ "$fs" = zfs ]; then
        # ZFS's DOS attributes (as torture.sh): after the contents (a write
        # sets archive again), archive given along where it should stay.
        archive=0x800000000
        echo hidden >dos-hidden-system; echo readonly >dos-readonly; echo written >dos-noarchive
        echo nounlink >dos-nounlink; echo offline >dos-offline-sparse; mkdir dos-hidden-dir
        echo fixed >dos-hidden-immutable; echo plain >dos-plain
    fi
    find . -depth ! -name old ! -name future -exec touch -h -m -d $TS_M {} + -exec touch -h -a -d $TS_A {} +
    for p in atime atime-dir atime-symlink symlink-xattr; do
        touch -h -m -d 2004-05-06T07:08:09.333333333 $p; touch -h -a -d 2005-06-07T08:09:10.444444444 $p; done
    if [ "$fs" = zfs ]; then
        dosflags dos-hidden-system $((0x600000000 | archive)); dosflags dos-readonly $((0x100000000 | archive))
        dosflags dos-noarchive 0; dosflags dos-nounlink $((0x2000000000 | archive))
        dosflags dos-offline-sparse $((0x300000000000 | archive)); dosflags dos-hidden-dir $((0x200000000 | archive))
        dosflags dos-hidden-immutable $((0x200000000 | archive)); chattr +i dos-hidden-immutable
    fi
    # ZFS on Linux takes immutable, append-only, nodump and project
    # inheritance only (check.sh's zfs-inode-flags need); each flag on its own.
    chattr +d nodump; chattr +A noatime 2>/dev/null || echo "chattr +A refused on $fs"
    chattr +S sync 2>/dev/null || echo "chattr +S refused on $fs"
    chattr +d flagdir; chattr +A flagdir 2>/dev/null || echo "chattr +A (dir) refused on $fs"
    chattr +D dirsync-dir 2>/dev/null || echo "chattr +D refused on $fs"
    if [ "$fs" = ext4 ]; then chattr +T topdir || echo "chattr +T refused on $fs"; chattr +j journal-data || echo "chattr +j refused on $fs"; fi
    chattr +a append-only append-dir; chattr +i immutable immutable-dir; chattr +i +d immutable-nodump
    echo "flags kept on $fs: $(lsattr -d nodump noatime sync append-only immutable immutable-dir immutable-nodump flagdir append-dir dirsync-dir $([ "$fs" = ext4 ] && echo topdir journal-data) 2>&1 | oneline)"
    echo "projects on $fs: $(lsattr -p -d projid-file projid-dir projid-dir/inherits 2>&1 | oneline)"
    [ "$fs" = zfs ] && echo "ZFS options: $(python3 $C/zfsx.py . $ZX 2>&1 | oneline 1200)"
    cd /
}

fixture_vfat() {  # DIR: the ESP's fixture (as fedora44-uefi's): the guest's own
    # ESP with its FAT attributes, FAT attributes (readonly, hidden, system,
    # archive set and cleared) on files and directories, FAT's time limits
    # and 2-second mtimes, long and odd names, sizes, deep and empty dirs.
    T=$1
    mkdir -p "$T" && cd "$T" || return 1
    mkdir real; cp -Rp /boot/efi real/esp || echo "copy of /boot/efi failed"
    python3 - /boot/efi real/esp <<'PY' || echo "the ESP's FAT attributes not copied"
import fcntl, os, sys
a, b = sys.argv[1], sys.argv[2]
for dp, dns, fns in os.walk(a):
    for n in dns + fns:
        p = os.path.join(dp, n); q = os.path.join(b, os.path.relpath(p, a))
        fd = os.open(p, os.O_RDONLY); v = fcntl.ioctl(fd, 0x80047210, b"\0" * 4); os.close(fd)
        fd = os.open(q, os.O_RDONLY); fcntl.ioctl(fd, 0x40047211, v); os.close(fd)
PY
    echo h >hidden; echo s >system; echo hs >hidden-system-noarchive; echo n >archive-cleared; echo a >archive-set
    echo r >readonly-chmod; echo r >readonly-attr
    mkdir hidden-dir system-dir readonly-dir; echo in >hidden-dir/f
    : >"name with spaces"; : >MixedCase.Txt; : >UPPER.TXT; : >"a+b,c;d=e[f]"; : >.dotfile; : >"$(head -c 200 /dev/zero | tr '\0' n)"
    : >"$(printf '\303\251t\303\251')" || echo "UTF-8 name refused on vfat"
    mkdir -p EFI/BOOT; head -c 307200 /dev/urandom >EFI/BOOT/BOOTX64.EFI; head -c 20971520 /dev/urandom >big
    d=deep; for i in $(seq 30); do d=$d/level$i; done; mkdir -p $d; : >$d/bottom
    : >empty; mkdir empty-dir
    find . -depth -exec touch -m -d 2002-03-04T05:06:08 {} + -exec touch -a -d 2003-04-05T00:00:00 {} +
    touch -d 1980-01-01T00:00:00 fat-min; touch -d 2107-12-31T23:59:58 fat-max
    touch -d 2001-02-03T04:05:07 odd-second; touch -m -d 2002-03-04T05:06:08 atime; touch -a -d 2003-04-05T00:00:00 atime
    # Attributes last: read-only refuses the touches above.
    fatset hidden 0x22; fatset system 0x24; fatset hidden-system-noarchive 0x06; fatset archive-cleared 0; fatset archive-set 0x20
    chmod a-w readonly-chmod; fatset readonly-attr 0x21
    fatset hidden-dir 0x12; fatset system-dir 0x14; fatset readonly-dir 0x11
    echo "FAT attributes: $(python3 $C/fatattr.py . | grep -v ' 0x20$' | oneline 900)"
    cd /
}

restore() {  # TOOL REPO DEST
    if [ $1 = U ]; then $U --no-progress --no-cache -r "$2" restore --numeric-id latest "$3"
    else $R -r "$2" restore latest --target "$3"; fi
}
msgs() {  # LOG: a restore's warnings and errors, the expected ones aside
    grep -v -e '^\[INFO\]' -e 'additional entries exist in destination' "$1" | grep -iE 'warn|error|fail|denied|not permitted' | head -n 3 | oneline 400
}
nosock() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); d.pop("socket",None); json.dump(d,open(sys.argv[1],"w"))' "$1"; }

for fs in zfs ext4 vfat; do
    S=src-$fs
    mkimg $fs $S || { fail "scratch-$fs" "no source $fs file system"; continue; }
    case $fs in
    zfs) a=$(zprops $ROOTDS); b=$(zprops $POOL/$PFX-$S); what="$ROOTDS's properties" ;;
    ext4) a=$bootfeat; b=$(extfeat $(findmnt -n -o SOURCE /mnt/$S)); what="/boot's ext4 features" ;;
    vfat) a=$espenv; b=$(fatenv /mnt/$S $(findmnt -n -o SOURCE /mnt/$S)); what="the ESP's mount options, FAT type and cluster" ;;
    esac
    if [ "$a" = "$b" ]; then pass "scratch-$fs" "the scratch $fs has $what: $b"
    else fail "scratch-$fs" "$what: $a; scratch: $b"; fi
    # The directories above the backed-up path, with metadata of their own
    # (on FAT only times: the mount gives the rest).
    mkdir -p /mnt/$S/anc1/anc2
    if [ $fs != vfat ]; then
        chown 1111:2222 /mnt/$S/anc1; chmod 0751 /mnt/$S/anc1
        chmod 0705 /mnt/$S/anc1/anc2; chown 3333:4444 /mnt/$S/anc1/anc2
        setfattr -n user.anc -v up /mnt/$S/anc1; setfacl -m u:1234:rx /mnt/$S/anc1/anc2
    fi
    P=/mnt/$S/anc1/anc2/tree
    if [ $fs = vfat ]; then fixture_vfat $P >$W/fixture-$fs.log 2>&1; else fixture $P $fs >$W/fixture-$fs.log 2>&1; fi
    sed 's/^/  fixture: /' $W/fixture-$fs.log
    touch -d 2001-02-04T04:05:06.5 $P /mnt/$S/anc1/anc2; touch -d 1999-09-09T09:09:10.25 /mnt/$S/anc1
    sync; noatime $S
    python3 $C/fsmeta.py record $P $W/src-$fs.json
    # Sockets are skipped by design (both tools): not expected back.
    nosock $W/src-$fs.json
    [ $fs = zfs ] && python3 $C/zfsx.py $P $ZX >$W/src-$fs.zfsx 2>&1
    [ $fs = vfat ] && python3 $C/fatattr.py $P >$W/src-$fs.fat
    anc_src=$(dirmeta /mnt/$S/anc1 /mnt/$S/anc1/anc2)
    for t in R U; do
        b=$R; [ $t = U ] && b=$U
        if ( cd /; rt_backup $b $W/repo-$fs-$t $P ) >$W/bk-$fs-$t.log 2>&1; then pass "backup-$fs-$t" "$(basename $b) backed up the fixture on $fs, exit 0"
        else fail "backup-$fs-$t" "$(basename $b) backup on $fs failed: $(tail -n 3 $W/bk-$fs-$t.log | oneline)"; fi
        sed 's/^/    backup msg: /' $W/bk-$fs-$t.log | head -n 10
    done
    python3 $C/fsmeta.py record $P $W/src-after-$fs.json
    nosock $W/src-after-$fs.json
    if out=$(python3 $C/fsmeta.py compare $W/src-$fs.json $W/src-after-$fs.json --json $W/cov-$fs.json 2>&1); then
        pass "backup-leaves-source-$fs" "after both backups: $out"
    else fail "backup-leaves-source-$fs" "the backups changed the source: $(echo "$out" | oneline)"; fi
    echo "  coverage $fs: $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["coverage"])' $W/cov-$fs.json)"
    if [ $fs != vfat ]; then
        # What the fixture sets, in the source as fsmeta saw it (a tool
        # missing or refused leaves it out, and the restores would still
        # compare equal).
        covwant="acl,user-xattr,trusted-xattr,capability,immutable,append,nodump,hard-linked,symlink,chardev,blockdev,fifo,with holes,setuid/setgid/sticky"
        [ $fs = ext4 ] && covwant="$covwant,noatime"
        [ $fs = zfs ] && covwant="$covwant,project ID"
        if covmiss=$(covcheck $W/cov-$fs.json "$covwant" 2>&1) && [ -z "$covmiss" ]; then
            pass "fixture-coverage-$fs" "the source has each of: $covwant"
        else fail "fixture-coverage-$fs" "not in the source (fsmeta coverage): $covmiss"; fi
    fi
    # Same repository format: snapshots node-identical.
    rt_nodes $R $W/repo-$fs-R $P >$W/nodes-$fs-R.txt 2>$W/nodes-$fs-R.err
    rt_nodes $R $W/repo-$fs-U $P >$W/nodes-$fs-U.txt 2>$W/nodes-$fs-U.err
    if [ -s $W/nodes-$fs-R.txt ] && rt_node_diff $W/nodes-$fs-R.txt $W/nodes-$fs-U.txt >$W/nodediff-$fs.txt 2>&1; then
        pass "snapshots-identical-$fs" "$(n $W/nodes-$fs-R.txt) nodes, restic and rustic snapshots node-identical; $(grep -c 'not a check' $W/nodediff-$fs.txt) not-a-check lines"
    else fail "snapshots-identical-$fs" "$(head -n 8 $W/nodediff-$fs.txt $W/nodes-$fs-R.err | oneline)"; fi
    if [ $fs != vfat ]; then
        if grep -q '^socket	' $W/nodes-$fs-R.txt $W/nodes-$fs-U.txt; then fail "sockets-skipped-$fs" "a socket node is in a snapshot"
        else pass "sockets-skipped-$fs" "no socket node in either snapshot"; fi
    fi
    # Each tool reads all of the other's repository.
    $R -r $W/repo-$fs-U check --read-data >$W/chk-$fs-RU.log 2>&1; c1=$?
    $U --no-progress --no-cache -r $W/repo-$fs-R check --read-data >$W/chk-$fs-UR.log 2>&1; c2=$?
    if [ $c1 = 0 ] && [ $c2 = 0 ]; then pass "cross-check-$fs" "restic-bko check --read-data of rustic's repository and rustic-bko's of restic's: exit 0"
    else fail "cross-check-$fs" "restic on rustic's exit $c1, rustic on restic's exit $c2: $(tail -n 3 $W/chk-$fs-RU.log $W/chk-$fs-UR.log | oneline)"; fi
    dirs="RR UU RU UR"
    # ZFS: each tool once more onto a dataset with directory xattrs and zstd.
    [ $fs = zfs ] && dirs="$dirs RRx UUx"
    for d in $dirs; do
        # Separate commands: FreeBSD sh expands a line's assignments first.
        dd=${d%x}; b=${d%${d#?}}; r=${dd#?}
        D=dst-$fs-$d
        if [ $d != $dd ]; then mkimg $fs $D "" xattr=on compression=zstd
        else mkimg $fs $D; fi || { fail "roundtrip-$fs-$d" "no target file system"; continue; }
        need=roundtrip-$fs-$d; [ $d != $dd ] && need=roundtrip-zfs-xattr-dir-zstd-$dd
        restore $r $W/repo-$fs-$b /mnt/$D >$W/rs-$fs-$d.log 2>&1; rc=$?
        sync; noatime $D
        python3 $C/fsmeta.py record /mnt/$D$P $W/dst-$fs-$d.json
        cmpout=$(python3 $C/fsmeta.py compare $W/src-$fs.json $W/dst-$fs-$d.json 2>&1); crc=$?
        warn=$(msgs $W/rs-$fs-$d.log)
        if [ $rc = 0 ] && [ $crc = 0 ] && [ -z "$warn" ]; then pass "$need" "$cmpout; restore exit 0, no warnings"
        else fail "$need" "restore rc=$rc; fsmeta: $(echo "$cmpout" | oneline); msgs: $warn"; fi
        if [ $d != $dd ]; then
            # The dataset's own properties, as made: not the scratch's.
            echo "  $D: $(zfs get -H -o value xattr,compression $POOL/$PFX-$D | oneline)"
            chattr -R -i -a /mnt/$D 2>/dev/null; rmimg $D
            continue
        fi
        anc_dst=$(dirmeta /mnt/$D/mnt/$S/anc1 /mnt/$D/mnt/$S/anc1/anc2 | sed "s|/mnt/$D||g")
        if [ "$anc_src" = "$anc_dst" ]; then pass "ancestors-$fs-$d" "anc1, anc2 mode owner mtime atime xattrs ACL: $anc_dst"
        else fail "ancestors-$fs-$d" "source: $anc_src; restored: $anc_dst"; fi
        if [ $fs = zfs ]; then
            python3 $C/zfsx.py /mnt/$D$P $ZX >$W/dst-$fs-$d.zfsx 2>&1
            if [ -s $W/src-$fs.zfsx ] && ! grep -q error $W/src-$fs.zfsx && cmp -s $W/src-$fs.zfsx $W/dst-$fs-$d.zfsx; then
                pass "zfs-options-$d" "DOS attributes (hidden, system, readonly, nounlink, offline, sparse, archive set and cleared; with +i), project IDs and P, flags identical: $(oneline 900 <$W/src-$fs.zfsx)"
            else fail "zfs-options-$d" "$(diff $W/src-$fs.zfsx $W/dst-$fs-$d.zfsx | grep '^[<>]' | head -n 8 | oneline 900)"; fi
        fi
        if [ $fs = vfat ]; then
            python3 $C/fatattr.py /mnt/$D$P >$W/dst-$fs-$d.fat
            if [ -s $W/src-$fs.fat ] && ! grep -q error $W/src-$fs.fat && cmp -s $W/src-$fs.fat $W/dst-$fs-$d.fat; then
                pass "fat-attrs-$d" "FAT attributes of $(n $W/src-$fs.fat) paths identical (readonly, hidden, system, archive set and cleared, the guest's ESP's): $(grep -v ' 0x20$' $W/src-$fs.fat | oneline 500)"
            else fail "fat-attrs-$d" "$(diff $W/src-$fs.fat $W/dst-$fs-$d.fat | grep '^[<>]' | head -n 8 | oneline 900)"; fi
        fi
        if [ $fs != vfat ] && { [ $d = RR ] || [ $d = UU ]; }; then
            # Restore over the restore, its immutable and append-only files
            # changed since (and an immutable directory given a new file);
            # on ZFS a DOS-readonly file changed too.
            X=/mnt/$D$P
            if chattr -i $X/immutable $X/immutable-dir $X/immutable-nodump && echo changed-contents >$X/immutable &&
                setfattr -n user.extra -v 1 $X/immutable && echo x >$X/immutable-dir/new && chmod 600 $X/immutable-nodump &&
                chattr +i $X/immutable $X/immutable-dir $X/immutable-nodump && echo appended >>$X/append-only; then ch=ok
            else ch=failed; fi
            if [ $fs = zfs ] && [ $ch = ok ]; then
                dosflags $X/dos-readonly 0x800000000 && echo changed >>$X/dos-readonly && dosflags $X/dos-readonly 0x900000000 || ch=failed
            fi
            echo "  over-$fs-$d before ($ch): $(lsattr -d $X/immutable $X/append-only $X/immutable-dir $X/immutable-nodump 2>&1 | sed "s|$X/||" | oneline); immutable: $(cat $X/immutable); append-only: $(cat $X/append-only | oneline)"
            restore $r $W/repo-$fs-$b /mnt/$D >$W/ov-$fs-$d.log 2>&1; rc=$?
            sync
            python3 $C/fsmeta.py record /mnt/$D$P $W/ov-$fs-$d.json
            cmpout=$(python3 $C/fsmeta.py compare $W/src-$fs.json $W/ov-$fs-$d.json 2>&1); crc=$?
            # The new file in the immutable directory isn't the snapshot's:
            # a restore leaves what it doesn't know (both tools, no --delete).
            cmpout2=$(echo "$cmpout" | grep -v -e '^extra  *1  *e.g. .immutable-dir/new.$')
            what="changed +i file (contents, a user xattr), +i dir (a new file), +i+d file (mode), +a file (a line more)"
            [ $fs = zfs ] && what="$what, DOS-readonly file (a line more)"
            if [ $ch != ok ]; then fail "restore-over-immutable-$fs-$d" "harness: couldn't change the restored immutable/append-only files first"
            elif [ $rc = 0 ] && { [ $crc = 0 ] || [ -z "$cmpout2" ]; }; then
                pass "restore-over-immutable-$fs-$d" "$what back as backed up: $(echo "$cmpout" | oneline); exit 0"
            else fail "restore-over-immutable-$fs-$d" "rc=$rc; $(echo "$cmpout" | oneline); $(msgs $W/ov-$fs-$d.log)"; fi
        fi
        chattr -R -i -a /mnt/$D 2>/dev/null; rmimg $D
    done
    chattr -R -i -a /mnt/$S 2>/dev/null; rmimg $S
    rm -rf $W/repo-$fs-R $W/repo-$fs-U
done

# --- One non-root run per tool: uid 4321 (no name) backs up and restores its
# own tree, on a ZFS dataset like the root's.
asuser() {  # COMMAND...: as uid 4321, gid 4322, no supplementary groups
    python3 -c 'import os,sys; os.setgroups([]); os.setgid(4322); os.setuid(4321); os.environ["HOME"]="/mnt/nr/home"; os.execvp(sys.argv[1], sys.argv[1:])' "$@"
}
if mkimg zfs nr; then
    mkdir -p /mnt/nr/home/t /mnt/nr/bin; cp $R $U /mnt/nr/bin/; chmod 755 /mnt/nr/bin/*
    chown -R 4321:4322 /mnt/nr/home; chmod 0755 /mnt/nr
    asuser sh -c 'cd /mnt/nr/home/t && echo a >f && chmod 0640 f && ln -s f l && mkfifo p && ln f f2 && mkdir -p d/e && chmod 2750 d &&
      setfattr -n user.x -v y f && truncate -s 16M sp && printf x | dd of=sp bs=4096 seek=100 conv=notrunc status=none &&
      find . -depth -exec touch -h -m -d 2005-05-05T05:05:05.5 {} + -exec touch -h -a -d 2006-06-06T06:06:06.25 {} +' >$W/nr-setup.log 2>&1
    nrset=$?
    sync; noatime nr
    python3 $C/fsmeta.py record /mnt/nr/home/t $W/nr-src.json
    for t in R U; do
        if [ $t = U ]; then
            out=$(asuser sh -c 'cd /mnt/nr/home && b=/mnt/nr/bin/rustic-bko && $b --no-progress --no-cache -r /mnt/nr/home/repo-U init >/dev/null &&
              $b --no-progress --no-cache -r /mnt/nr/home/repo-U backup --set-atime yes /mnt/nr/home/t >/dev/null &&
              $b --no-progress --no-cache -r /mnt/nr/home/repo-U restore --numeric-id latest:/mnt/nr/home/t /mnt/nr/home/out-U' 2>&1); rc=$?
        else
            out=$(asuser sh -c 'cd /mnt/nr/home && b=/mnt/nr/bin/restic-bko && $b -r /mnt/nr/home/repo-R init -q &&
              $b -r /mnt/nr/home/repo-R backup -q --with-atime /mnt/nr/home/t &&
              $b -r /mnt/nr/home/repo-R restore -q latest:/mnt/nr/home/t --target /mnt/nr/home/out-R' 2>&1); rc=$?
        fi
        sync
        python3 $C/fsmeta.py record /mnt/nr/home/out-$t $W/nr-$t.json
        cmpout=$(python3 $C/fsmeta.py compare $W/nr-src.json $W/nr-$t.json 2>&1); crc=$?
        nm=restic-bko; [ $t = U ] && nm=rustic-bko
        if [ $nrset != 0 ]; then fail "nonroot-$t" "harness: uid 4321's tree wasn't made (exit $nrset): $(oneline 300 <$W/nr-setup.log)"
        elif [ $rc = 0 ] && [ $crc = 0 ]; then pass "nonroot-$t" "$nm as uid 4321 on ZFS (no name; user xattr, setgid dir, hard link, holes, fifo, symlink, times): $cmpout"
        else fail "nonroot-$t" "$nm rc=$rc: $(echo "$out" | grep -v INFO | head -n 3 | oneline); fsmeta: $(echo "$cmpout" | oneline)"; fi
    done
    rmimg nr
else fail "nonroot-R" "no ZFS dataset for the non-root run"; fi

# rustic-bko's WARN when /etc/localtime can't be read (Alpine without tzdata).
if $U --no-progress --no-cache -r /nonexistent snapshots 2>&1 | grep -q 'localtime not understood'; then
    fail "no-spurious-warnings" "rustic-bko warns '/etc/localtime not understood' on every command; /etc/localtime -> $(readlink /etc/localtime)"
else pass "no-spurious-warnings" "rustic-bko silent about /etc/localtime (-> $(readlink /etc/localtime))"; fi

# What OpenZFS on Linux refuses: inode flags other than i, a, d, P
# (zpl_ioctl_setflags), checked here on a fresh dataset: a flag is taken
# when chattr succeeds and lsattr shows it after.
hasflag() { lsattr -d "$2" 2>/dev/null | cut -d' ' -f1 | grep -q "$1"; }
if mkimg zfs fl; then
    : >/mnt/fl/f; mkdir /mnt/fl/d; refused= taken=
    for f in A S D c C s u t e F x m; do
        if { chattr +$f /mnt/fl/f 2>/dev/null && hasflag $f /mnt/fl/f; } || { chattr +$f /mnt/fl/d 2>/dev/null && hasflag $f /mnt/fl/d; }; then
            taken="$taken$f"; else refused="$refused$f"; fi
        chattr -$f /mnt/fl/f /mnt/fl/d 2>/dev/null
    done
    for f in i a d; do { chattr +$f /mnt/fl/f 2>/dev/null && hasflag $f /mnt/fl/f; } || refused="$refused$f"; chattr -$f /mnt/fl/f 2>/dev/null; done
    { chattr +P /mnt/fl/d 2>/dev/null && hasflag P /mnt/fl/d; } || refused="${refused}P"
    rmimg fl
    case $refused in
    *[iadP]*) fail "zfs-inode-flags" "ZFS refused one of i, a, d, P: refused $refused" ;;
    *) if [ -n "$refused" ]; then
           limit "zfs-inode-flags" "OpenZFS on Linux keeps only immutable, append-only, nodump and project inheritance (i a d P, checked in the fixture and every restore)${taken:+ and $taken (in the fixture too)}; chattr refuses the rest or lsattr doesn't show them: $refused"
       else pass "zfs-inode-flags" "every flag taken"; fi ;;
    esac
fi
limit "selinux-labels" "no SELinux on Chimera (LSMs $(cat /sys/kernel/security/lsm))"
limit "nfsv4-acls" "Linux ZFS (acltype=posix), ext4 and vfat have no NFSv4 ACLs"
limit "birth-times" "ZFS crtime, ext4 btime and FAT creation times can't be set on Linux (BACKOTTER.md)"
limit "bsd-file-flags" "Linux guest: no BSD file flags"
case " $bootfeat " in
    *" project "*) ;;
    *) limit "project-ids-ext4" "the guest's ext4 (/boot) has no project feature: chattr -p refused; project IDs are checked on ZFS" ;;
esac
limit "vfat-unix-metadata" "FAT keeps no owners, modes but read-only, xattrs, ACLs, links, devices, FIFOs or sockets (the mount's uid, gid, fmask, dmask): the ESP's fixture has the names, sizes, times and FAT attributes FAT keeps"
echo INNER-DONE
exit 0
