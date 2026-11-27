#!/bin/sh
# FreeBSD's rescue image: make-img-bsd.sh runs this as root in the FreeBSD
# build VM, in a fresh directory holding in/ (see make-img-bsd.sh's header).
#
#   build.sh info       makes root/ (the memstick's system, updated), and
#                       prints its KERNEL= and ZFS_VERSION=, with its
#                       OpenZFS feature list in in/zfs-features
#   build.sh assemble   out/image.img
#   build.sh list       out/image.img's root, read-only, as a tar on stdout
#   build.sh room       out/image.img's root: its free space (BKO-ROOM)
#   build.sh refresh    out/image.img, the parent, with the binaries and the
#                       marker swapped in place and its label rewritten
#                       (docs/design/img-speed.md §4.2): exit 3 when they
#                       don't fit
#
# The image is the official memstick remastered, with the release's security
# updates (freebsd-update, to the latest -pN).  Its root file system, less
# the installer (rc.local, which starts it, and the packages it installs),
# is copied into a new UFS2 labelled BACKOTTER-<first 8 hex digits of the
# image ID>, so that a second stick (or the machine's own disks) can't stand
# in for the root.  It gets BackOtter's console and remote access, restic-bko,
# rustic-bko and bko-holt, key access at boot (/etc/rc.d/backotter_key_access:
# SSH keys for "backotter" from the config slice, mdo for root), and the
# marker /.disk/backotter.json.  The disk layout is the memstick's, plus the
# config partition:
#
#   MBR slice 1   the memstick's ESP, its EFI/BOOT/bootx64.efi the updated
#                 root's loader.efi
#   MBR slice 2   active, BSD label with the root UFS2 as "a" (boot0-less MBR
#                 boot code, then boot1/boot2 from the label, as the memstick)
#   MBR slice 3   config.img, FAT16 (type 0x06)
#
# The root is mounted read-only, as on the memstick.  /var and /tmp are in
# memory (rc.conf's varmfs and tmpmfs, on automatically for a read-only
# root); /etc, /root and /home too, through FreeBSD's own diskless setup
# (/etc/rc.initdiskless, which /etc/diskless turns on): each /conf/base/DIR is
# copied into a memory file system mounted on /DIR.  All are tmpfs (mdmfs
# makes tmpfs in 15.x).  /conf/base/etc is the image's
# /etc hard-linked, so it takes no space.

set -eu -o pipefail
umask 022
die() { echo "build.sh: $*" >&2; exit 1; }

LIB=/usr/local/lib/backotter    # where console and remote-access go
# Caps on the memory file systems (tmpfs takes memory only as files fill
# them): /tmp and /root (restic's and rustic's caches) big, the rest enough.
TMPSIZE=1g
VARSIZE=256m
ETC_SECTORS=40960               # 512-byte sectors: 20 MiB (the memstick's /etc is 4)
ROOT_SECTORS=2097152            # 1 GiB
HOME_SECTORS=131072             # 64 MiB

BINS="restic-bko rustic-bko bko-holt"   # in /usr/local/bin
HEADROOM_MIN_KIB=65536          # the root's free space: at least this (img-speed.md §4.2)
in=$PWD/in                      # (refresh-lib.sh's)

cmd=${1:-}
md= md2=
cleanup() {
    for d in mnt lst/root rm/root hr; do umount "$d" 2>/dev/null || true; done
    [ -z "$md" ] || mdconfig -d -u "$md"
    [ -z "$md2" ] || mdconfig -d -u "$md2"
}
# sh skips the EXIT trap when a signal kills it (SIGPIPE, say, once
# make-img-bsd.sh's ssh is gone), and the builder lives on.
trap cleanup EXIT
trap 'exit 1' HUP INT TERM PIPE

# headroom: the root's free space the headroom rule asks, in KiB: max(64 MiB,
# half the binaries' size).
headroom() {
    b=0
    for n in $BINS; do
        [ ! -f "in/$n" ] || b=$((b + ($(wc -c <"in/$n") + 1023) / 1024))
    done
    [ $((b / 2)) -gt $HEADROOM_MIN_KIB ] && echo $((b / 2)) || echo $HEADROOM_MIN_KIB
}

# fs_free UFS_IMAGE: the free KiB (its size less what is used: root writes
# past minfree) of the file system in a file.
fs_free() {
    mkdir -p hr
    md2=$(mdconfig -a -t vnode -o readonly -f "$1")
    mount -o ro "/dev/$md2" hr
    df -k hr | awk 'NR == 2 { print $2 - $3 }'
    umount hr
    mdconfig -d -u "$md2"; md2=
}

# marker_field KEY: the marker's top-level string KEY (in/backotter.json,
# marker.py's indent=2).
marker_field() { sed -n "s/^  \"$1\": \"\([^\"]*\)\",*\$/\1/p" in/backotter.json; }

# zfs_version ROOT: the first of ROOT/boot/kernel/zfs.ko's version strings (no
# head: SIGPIPE and pipefail).
zfs_version() {
    set -- $(strings "$1/boot/kernel/zfs.ko" | sed -n 's/^\([0-9]*\.[0-9]*\.[0-9]*\)-[0-9]*-FreeBSD_g[0-9a-f]*$/\1/p')
    echo "${1:-}"
}

# fsck_clean DEV WHEN: fsck_ffs -n -f of the root, which must find nothing.
# (makefs doesn't set the flag for tracking directory depth, which fsck offers
# to: not a fault.)
fsck_clean() {
    fsck_ffs -n -f "$1" >fsck.out 2>&1 || { cat fsck.out >&2; die "fsck_ffs $2 the swap: the root is not clean"; }
    # (Not a pipe into grep -q: under pipefail its early exit would fail the pipe.)
    grep -v '^UPDATE FILESYSTEM TO TRACK DIRECTORY DEPTH? no$' fsck.out >fsck.chk || true
    ! grep -Eq '\? no$|DIRTY|UNEXPECTED|INCORRECT|MODIFIED' fsck.chk ||
        { cat fsck.out >&2; die "fsck_ffs $2 the swap: the root is not clean"; }
    echo "fsck $2: $(tail -n 1 fsck.out)"
}

# label_of DEV: the volume label of the UFS on DEV.
label_of() { tunefs -p "$1" 2>&1 | sed -n 's/^tunefs: volume label: (-L)[[:space:]]*//p'; }

# put_text FILE OLD NEW: FILE with OLD (a fixed string) replaced by NEW, its
# bytes rewritten in place so that its inode (and every hard link) stays.
put_text() {
    sed "s|$2|$3|g" "$1" >put.tmp
    cat put.tmp >"$1"
    rm -f put.tmp
}

case $cmd in
# --- list: out/image.img's root (MBR slice 2, BSD partition a), read-only, as a tar
list)
    mkdir -p lst/root
    md2=$(mdconfig -a -t vnode -o readonly -f out/image.img)
    mount -o ro "/dev/${md2}s2a" lst/root
    tar -C lst -cf - root
    umount lst/root
    mdconfig -d -u "$md2"; md2=
    exit 0 ;;
# --- room: the root's free space and the binaries' blocks (refresh-lib.sh's BKO-ROOM)
room)
    . in/refresh-lib.sh
    mkdir -p rm/root
    md2=$(mdconfig -a -t vnode -o readonly -f out/image.img)
    mount -o ro "/dev/${md2}s2a" rm/root
    # shellcheck disable=SC2046
    set -- $(df -k rm/root | awk 'NR == 2 { print $2 - $3, $2 }')
    # shellcheck disable=SC2086
    bko_room rm/root /usr/local/bin "$1" "$2" $BINS
    umount rm/root
    mdconfig -d -u "$md2"; md2=
    exit 0 ;;
# --- refresh: out/image.img, the parent (make-img-bsd.sh puts it there), with
# the binaries and the marker swapped in place in its root and its id sites
# rewritten (img-speed.md §4.2, D1, D2): the UFS label BACKOTTER-<8 hex> by
# tunefs -L, and both fstabs (/conf/base/etc is /etc hard-linked by pax -l), in
# place.  The ESP, the boot code, the BSD label and the MBR stay as they are;
# the host writes config.img into slice 3.  The release and the test copy are
# each refreshed from their own parent, so TEST plays no part.
refresh)
    for f in backotter.json bko-holt refresh-lib.sh; do
        [ -e "in/$f" ] || die "no in/$f"
    done
    [ -f out/image.img ] || die "no out/image.img, the parent"
    . in/refresh-lib.sh
    image_id=$(marker_field image_id)
    [ -n "$image_id" ] || die "in/backotter.json has no image_id"
    new=BACKOTTER-$(echo "$image_id" | cut -c1-8)
    md=$(mdconfig -a -t vnode -f out/image.img)
    dev=/dev/${md}s2a
    [ -c "$dev" ] || die "the parent has no slice 2 partition a"
    fsck_clean "$dev" before
    old=$(label_of "$dev")
    case $old in BACKOTTER-????????) ;; *) die "the parent's root is labelled '$old', not BACKOTTER-<8 hex>" ;; esac
    [ "$old" != "$new" ] || die "the new image_id's label $new is the parent's"
    mkdir -p mnt
    mount -o noatime "$dev" mnt
    # The marker's kernel and ZFS are the parent's (marker.py was given them):
    # they must still be the parent's tree's.
    k=$(ROOT=mnt sh mnt/bin/freebsd-version -k)
    m=$(marker_field kernel)
    [ -n "$k" ] && [ "$k" = "$m" ] || die "the parent's kernel is '$k', its marker says '$m'"
    z=$(zfs_version mnt)
    mz=$(awk '/^  "zfs": \{/ { z = 1 } z && /^    "version": / { gsub(/[",]/, ""); print $2; exit }' in/backotter.json)
    [ -n "$z" ] && [ "$z" = "$mz" ] || die "the parent's zfs.ko is ZFS '$z', its marker says '$mz'"
    compat=mnt/usr/share/zfs/compatibility.d/openzfs-${z%.*}
    [ -f "$compat" ] || die "the parent has no ZFS feature list for $z"
    awk '/^  "zfs": \{/ { z = 1 } z && /^    "features": \[/ { f = 1; next } f && /^    \]/ { exit }
         f { gsub(/[ ",]/, ""); print }' in/backotter.json >features.marker
    grep -v '^#' "$compat" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | grep . | LC_ALL=C sort >features.tree
    cmp -s features.marker features.tree ||
        die "the marker's ZFS features are not the parent's $compat"
    echo "kernel $k, ZFS $z and its $(wc -l <features.tree | tr -d ' ') features, as the parent's marker says"
    # The fstabs name the label; /conf/base/etc/fstab is /etc/fstab's hard link.
    for f in etc/fstab conf/base/etc/fstab; do
        grep -q "^/dev/ufs/$old " "mnt/$f" || die "the parent's /$f doesn't mount /dev/ufs/$old"
    done
    # No other file of the root names the old label (the id sites are §1.3's).
    others=$(grep -rlF "$old" mnt/etc mnt/boot mnt/conf 2>/dev/null | grep -vx -e mnt/etc/fstab -e mnt/conf/base/etc/fstab || true)
    [ -z "$others" ] || die "the parent names its label outside the fstabs: $others"
    # shellcheck disable=SC2046
    set -- $(df -k mnt | awk 'NR == 2 { print $2 - $3 }')
    rc=0
    # shellcheck disable=SC2086
    bko_fit mnt /usr/local/bin "$1" $BINS || rc=$?
    if [ $rc -ne 0 ]; then
        umount mnt; mdconfig -d -u "$md"; md=
        exit $rc
    fi
    # (A binary the parent lacks is installed 0755, as assemble installs it.)
    # shellcheck disable=SC2086
    BKO_BIN_MODE=755 bko_swap mnt /usr/local/bin $BINS || die "the swap failed"
    put_text mnt/etc/fstab "/dev/ufs/$old " "/dev/ufs/$new "
    [ "$(stat -f %i mnt/etc/fstab)" = "$(stat -f %i mnt/conf/base/etc/fstab)" ] ||
        put_text mnt/conf/base/etc/fstab "/dev/ufs/$old " "/dev/ufs/$new "
    for f in etc/fstab conf/base/etc/fstab; do
        grep -q "^/dev/ufs/$new " "mnt/$f" && ! grep -qF "$old" "mnt/$f" || die "/$f doesn't name $new alone"
    done
    echo "refresh: both fstabs mount /dev/ufs/$new"
    sync
    umount mnt
    tunefs -L "$new" "$dev" >tunefs.out 2>&1 || { cat tunefs.out >&2; die "tunefs -L $new failed"; }
    [ "$(label_of "$dev")" = "$new" ] || die "the root's label is '$(label_of "$dev")' after tunefs -L $new"
    echo "refresh: the root's label $old is now $new"
    fsck_clean "$dev" after
    mdconfig -d -u "$md"; md=
    exit 0 ;;
info|assemble) ;;
*) die "usage: build.sh info|assemble|list|room|refresh" ;;
esac

set -- in/FreeBSD-*-amd64-memstick.img
[ $# -eq 1 ] && [ -f "$1" ] || die "need exactly one memstick in in/"
memstick=$1

# --- The root file system, with the release's security updates ------------------
# The memstick's root, copied into root/ and brought to the release's latest
# patch level (-pN) by freebsd-update, from FreeBSD's signed update server:
# -b updates the tree, not the builder.  Done once, by info; assemble runs in
# the same directory and takes root/ as it is.  File flags (schg and friends)
# mean nothing on a read-only root, and would keep make-img-bsd.sh from
# removing this directory.
if [ ! -f root.done ]; then
    rm -rf root fu
    mkdir -p mnt root fu
    md=$(mdconfig -a -t vnode -o readonly -f "$memstick")
    mount -o ro "/dev/${md}s2a" mnt
    echo "Copying the memstick's root"
    tar -C mnt --no-fflags -cf - . | tar -C root -xpf -
    umount mnt
    mdconfig -d -u "$md"; md=
    release=$(ROOT=root sh root/bin/freebsd-version -u)
    echo "Updating it from $release with freebsd-update"
    fu() {
        PAGER=cat freebsd-update -b "$PWD/root" -d "$PWD/fu" -f root/etc/freebsd-update.conf \
            --currently-running "$release" --not-running-from-cron "$@"
    }
    fu fetch >fu.log 2>&1 || { tail -n 20 fu.log >&2; die "freebsd-update fetch failed"; }
    if ! grep -q '^No updates needed' fu.log; then
        fu install >>fu.log 2>&1 || { tail -n 20 fu.log >&2; die "freebsd-update install failed"; }
    fi
    rm -rf fu
        : >root.done
fi

case $cmd in
info)
    kernel=$(ROOT=root sh root/bin/freebsd-version -k)
    # The first of zfs.ko's version strings (no head: SIGPIPE and pipefail).
    zfs=$(strings root/boot/kernel/zfs.ko | sed -n 's/^\([0-9]*\.[0-9]*\.[0-9]*\)-[0-9]*-FreeBSD_g[0-9a-f]*$/\1/p')
    set -- $zfs
    zfs=${1:-}
    [ -n "$kernel" ] && [ -n "$zfs" ] || die "can't tell the kernel or ZFS version"
    cp "root/usr/share/zfs/compatibility.d/openzfs-${zfs%.*}" in/zfs-features ||
        die "the root has no ZFS feature list for $zfs"
    echo "KERNEL=$kernel"
    echo "ZFS_VERSION=$zfs"
    exit 0 ;;
esac

image_id=$(sed -n 's/^  "image_id": "\([0-9a-f-]*\)",$/\1/p' in/backotter.json)
[ -n "$image_id" ] || die "in/backotter.json has no image_id"
label=BACKOTTER-$(echo "$image_id" | cut -c1-8)

# --- BackOtter's changes to the root ----------------------------------------------
# The installer: what starts it and what it installs.
rm -rf root/etc/rc.local root/usr/freebsd-dist root/usr/freebsd-packages
# Points into the installer's /tmp/bsdinstall_etc; dhclient writes the real one.
rm root/etc/resolv.conf

# /mnt is a tmpfs: bko-holt load's target (/mnt/bko-target) and what is made
# under it must be writable before anything is mounted there.  zpool create
# -R /mnt/bko-target makes the new pool's own mountpoint (zroot's /zroot) at
# once, which the read-only root refused ("failed to create mountpoint",
# HoltTest2's freebsd14+img).  Not on /mnt/bko-target itself: load's status
# would read a mount there as the target tree's.
{
    printf '/dev/ufs/%s / ufs ro,noatime 1 1\n' "$label"
    printf 'tmpfs /mnt tmpfs rw,mode=755 0 0\n'
} >root/etc/fstab
cat >root/etc/rc.conf.local <<EOF
# BackOtter rescue stick.
hostname="backotter"
root_rw_mount="NO"
ifconfig_DEFAULT="DHCP"
sshd_enable="NO"            # on only through BackOtter's console
sendmail_enable="NONE"
dumpdev="NO"
tmpsize="$TMPSIZE"
varsize="$VARSIZE"
EOF

# The loader: the memstick's settings less its installer branding.  The
# kernel and getty use the screen and the first serial port both
# (boot_multicons, with uart0 a console through device.hints' flags 0x10), for
# IPMI serial-over-LAN.  The screen stays the primary console, the one that
# gets rc's output and prompts, as with the Linux image's default entry.
# --test images make serial primary and show the loader menu there too, as
# the Linux image's --test makes its serial entry the default: naming
# comconsole in console= does that.  The UEFI loader calls the screen "efi",
# the BIOS one "vidconsole"; each skips the name it doesn't have.
{
    echo '# BackOtter rescue stick.'
    echo 'vfs.mountroot.timeout="10"'
    echo 'kernels_autodetect="NO"'
    echo 'boot_multicons="YES"'
    echo 'hint.uart.0.baud="115200"'
    if [ "${TEST:-0}" = 1 ]; then
        echo 'autoboot_delay="3"'
        echo 'comconsole_speed="115200"'
        echo 'console="comconsole,vidconsole,efi"'
    else
        echo 'autoboot_delay="10"'
    fi
} >root/boot/loader.conf

# BackOtter's console on ttyv0 and the serial console, logged in as root
# by getty itself, as agetty --autologin --login-program does on Linux; ttyv1
# to ttyv7 log root straight into a shell, like the Linux image's tty2-tty6.
cat >>root/etc/gettytab <<EOF

#
# BackOtter rescue stick: its console, run by getty as the login program.
#
backotter|BackOtter console:\\
	:al=root:lo=$LIB/console:tc=Pc:
backotter.serial|BackOtter console on a serial port:\\
	:al=root:lo=$LIB/console:tc=3wire.115200:
EOF
sed -i '' -E \
    -e 's|^(ttyv0[[:space:]]+)"/usr/libexec/getty Pc"|\1"/usr/libexec/getty backotter"|' \
    -e 's|^(ttyv[1-7][[:space:]]+)"/usr/libexec/getty Pc"|\1"/usr/libexec/getty al.Pc"|' \
    -e 's|^(ttyu[0-3][[:space:]]+)"/usr/libexec/getty 3wire"|\1"/usr/libexec/getty backotter.serial"|' \
    root/etc/ttys
[ "$(grep -c 'getty backotter' root/etc/ttys)" -eq 5 ] || die "unexpected /etc/ttys"
[ "$(grep -c 'getty al.Pc' root/etc/ttys)" -eq 7 ] || die "unexpected /etc/ttys"

# sshd: BackOtter's settings first (sshd takes the first value it reads), no
# host keys (remote-access makes them).
{ cat in/sshd_config.backotter; echo; cat root/etc/ssh/sshd_config; } >sshd_config
mv sshd_config root/etc/ssh/sshd_config
! ls root/etc/ssh/ssh_host_* >/dev/null 2>&1 || die "the memstick has SSH host keys"

mkdir -p "root$LIB" root/usr/local/bin root/.disk root/home
# Where bko-holt load mounts what it restores (its default --target): the
# root is read-only, so the directory comes with the image (under the tmpfs
# on /mnt, for when that mount fails).
mkdir -p root/mnt/bko-target
install -m 755 in/console in/remote-access "root$LIB/"
# SSH keys from the config slice, at boot (stick/bsd/key-access): before
# /etc is copied into /conf/base below.
install -m 555 in/key-access root/etc/rc.d/backotter_key_access
install -m 755 in/restic-bko root/usr/local/bin/
[ ! -f in/rustic-bko ] || install -m 755 in/rustic-bko root/usr/local/bin/
install -m 755 in/bko-holt root/usr/local/bin/
install -m 644 in/backotter.json root/.disk/

# /etc, /root and /home in memory (see the top).  rc.initdiskless copies each
# md_size along, and DIR.remove lists what to delete after copying.
touch root/etc/diskless
mkdir -p root/conf/base/root root/conf/base/home
echo "$ETC_SECTORS" >root/etc/md_size
(cd root && pax -rw -l -pe etc conf/base/)
(cd root/root && pax -rw -pe . ../conf/base/root/)
echo "$ROOT_SECTORS" >root/conf/base/root/md_size
echo "$HOME_SECTORS" >root/conf/base/home/md_size
for d in etc root home; do echo md_size >"root/conf/base/$d.remove"; done

# --- The image --------------------------------------------------------------------
echo "Making the root file system, $label"
# makefs sizes it to fit, which today leaves some 90 MiB free.  A refresh
# needs room to swap the binaries in place (img-speed.md §4.2, D1): max(64
# MiB, half the binaries' size) free, and where makefs left less it is made
# again with the shortfall as -b (free blocks makefs adds to the size).
want=$(headroom)
makefs -t ffs -B little -o version=2 -o label="$label" root.ufs root
free=$(fs_free root.ufs)
if [ "$free" -lt "$want" ]; then
    rm -f root.ufs
    makefs -t ffs -B little -o version=2 -o label="$label" -b "$((want - free + 1024))k" root.ufs root
    free=$(fs_free root.ufs)
    [ "$free" -ge "$want" ] || die "the root has $free KiB free, under the headroom rule's $want"
fi
echo "Headroom: $free KiB free in the root (the rule: $want)"
# The memstick's ESP, with the updated root's loader.efi as its bootx64.efi.
md=$(mdconfig -a -t vnode -o readonly -f "$memstick")
dd if="/dev/${md}s1" of=efi.img bs=1m status=none
mdconfig -d -u "$md"; md=
md=$(mdconfig -a -t vnode -f efi.img)
mount -t msdosfs "/dev/$md" mnt
[ -f mnt/EFI/BOOT/bootx64.efi ] || die "the memstick's ESP has no EFI/BOOT/bootx64.efi"
cp root/boot/loader.efi mnt/EFI/BOOT/bootx64.efi
umount mnt
mdconfig -d -u "$md"; md=
mkimg -s mbr -b root/boot/mbr -p efi:=efi.img \
    -p freebsd:-"mkimg -s bsd -b root/boot/boot -p freebsd-ufs:=root.ufs" \
    -p fat16b:=in/config.img -a 2 -o out/image.img
md2=$(mdconfig -a -t vnode -o readonly -f out/image.img)
gpart show "$md2" "${md2}s2"
