#!/bin/sh
# FreeBSD's rescue image: make-img-bsd.sh runs this as root in the FreeBSD
# build VM, in a fresh directory holding in/ (see make-img-bsd.sh's header).
#
#   build.sh info       KERNEL= and ZFS_VERSION= of the memstick's system,
#                       and its OpenZFS feature list in in/zfs-features
#   build.sh assemble   out/image.img
#
# The image is the official memstick remastered.  Its root file system, less
# the installer (rc.local, which starts it, and the packages it installs),
# is copied into a new UFS2 labelled BACKOTTER-<first 8 hex digits of the
# image ID>, so that a second stick (or the machine's own disks) can't stand
# in for the root.  It gets BackOtter's console and remote access, restic-bko
# and rustic-bko, and the marker /.disk/backotter.json.  The disk layout is the
# memstick's, plus the config partition:
#
#   MBR slice 1   the memstick's ESP, as is (EFI/BOOT/bootx64.efi is loader.efi)
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

set -- "${1:-}" in/FreeBSD-*-amd64-memstick.img
[ $# -eq 2 ] && [ -f "$2" ] || die "need exactly one memstick in in/"
cmd=$1 memstick=$2

md= md2=
cleanup() {
    umount mnt 2>/dev/null || true
    [ -z "$md" ] || mdconfig -d -u "$md"
    [ -z "$md2" ] || mdconfig -d -u "$md2"
}
# sh skips the EXIT trap when a signal kills it (SIGPIPE, say, once
# make-img-bsd.sh's ssh is gone), and the builder lives on.
trap cleanup EXIT
trap 'exit 1' HUP INT TERM PIPE
mkdir -p mnt
md=$(mdconfig -a -t vnode -o readonly -f "$memstick")
mount -o ro "/dev/${md}s2a" mnt

case $cmd in
info)
    kernel=$(ROOT=mnt sh mnt/bin/freebsd-version -k)
    # The first of zfs.ko's version strings (no head: SIGPIPE and pipefail).
    zfs=$(strings mnt/boot/kernel/zfs.ko | sed -n 's/^\([0-9]*\.[0-9]*\.[0-9]*\)-[0-9]*-FreeBSD_g[0-9a-f]*$/\1/p')
    set -- $zfs
    zfs=${1:-}
    [ -n "$kernel" ] && [ -n "$zfs" ] || die "can't tell the kernel or ZFS version"
    cp "mnt/usr/share/zfs/compatibility.d/openzfs-${zfs%.*}" in/zfs-features ||
        die "the memstick has no ZFS feature list for $zfs"
    echo "KERNEL=$kernel"
    echo "ZFS_VERSION=$zfs"
    exit 0 ;;
assemble) ;;
*) die "usage: build.sh info|assemble" ;;
esac

image_id=$(sed -n 's/^  "image_id": "\([0-9a-f-]*\)",$/\1/p' in/backotter.json)
[ -n "$image_id" ] || die "in/backotter.json has no image_id"
label=BACKOTTER-$(echo "$image_id" | cut -c1-8)

# --- The root file system ---------------------------------------------------------
# File flags (schg and friends) mean nothing on a read-only root, and would
# keep make-img-bsd.sh from removing this directory.
echo "Copying the memstick's root"
mkdir root
tar -C mnt --no-fflags -cf - . | tar -C root -xpf -
umount mnt

# The installer: what starts it and what it installs.
rm -rf root/etc/rc.local root/usr/freebsd-dist root/usr/freebsd-packages
# Points into the installer's /tmp/bsdinstall_etc; dhclient writes the real one.
rm root/etc/resolv.conf

printf '/dev/ufs/%s / ufs ro,noatime 1 1\n' "$label" >root/etc/fstab
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
        # QEMU's emulated USB stick on xHCI (boot-test.py's) garbles large
        # bulk reads (umass: "bad CSW signature"), and the root never
        # mounts; the stock memstick hangs the same way, and 128 KiB is
        # still too much.  Real sticks boot with FreeBSD's 1 MiB.
        echo 'kern.maxphys="65536"'
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
install -m 755 in/console in/remote-access "root$LIB/"
install -m 755 in/restic-bko root/usr/local/bin/
[ ! -f in/rustic-bko ] || install -m 755 in/rustic-bko root/usr/local/bin/
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
makefs -t ffs -B little -o version=2 -o label="$label" root.ufs root
dd if="/dev/${md}s1" of=efi.img bs=1m status=none
mkimg -s mbr -b root/boot/mbr -p efi:=efi.img \
    -p freebsd:-"mkimg -s bsd -b root/boot/boot -p freebsd-ufs:=root.ufs" \
    -p fat16b:=in/config.img -a 2 -o out/image.img
md2=$(mdconfig -a -t vnode -o readonly -f out/image.img)
gpart show "$md2" "${md2}s2"
