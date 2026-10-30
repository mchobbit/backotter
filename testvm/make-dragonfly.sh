#!/usr/bin/env bash
# Build a DragonFly BSD test guest for BackOtter: an unattended install from
# the official live CD into a qcow2 disk image, under QEMU with KVM.  The
# install is a script of ours (dragonfly/rc.local), put on the CD as the
# /etc/rc.local its boot runs; DragonFly's own installer has no unattended
# mode.
#
# Usage: ./make-dragonfly.sh VERSION WORK_DIR
#   e.g. ./make-dragonfly.sh 6.4.2 vms/dragonfly6
#
# VERSION is a DragonFly release.  dfly-x86_64-VERSION_REL.iso is downloaded
# into WORK_DIR unless already there, and checked against the mirror's
# md5.txt, fetched over HTTPS: DragonFly publishes no signatures, nor any
# stronger checksum.
#
# Results, in WORK_DIR (N is the major version):
#   dragonflyN.qcow2      the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" of the finished install
#   root-password         root's password (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the live system's serial console during the install
#
# The guest, host name dflyN: BIOS + MBR, one DragonFly slice whose
# disklabel64 holds /boot (1 GiB UFS), swap (2 GiB) and / (HAMMER2, the
# rest), console on the serial port, 2 GB RAM, DHCP and sshd.  Run it with
# vm-run.  vm-mount can't open it: Linux reads neither disklabel64 nor
# HAMMER2.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, xorriso, openssl, wget.  Runs
# as an ordinary user in the kvm group.

set -euo pipefail

DISK_SIZE=16G
MEMORY=2048
MIRROR=https://mirror-master.dragonflybsd.org/iso-images
INSTALL_TIMEOUT=2400

here=$(dirname "$(realpath "$0")")
die() { echo "make-dragonfly: $*" >&2; exit 1; }

[[ $# -eq 2 ]] || die "usage: $0 VERSION WORK_DIR"
version=$1
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "VERSION looks like 6.4.2, not '$version'"
major=${version%%.*}
guest_name=dfly$major
DISK=dragonfly$major.qcow2
ISO=dfly-x86_64-${version}_REL.iso
mkdir -p "$2"
cd "$2"
[[ -e $DISK ]] && die "$2/$DISK already exists; move it away to rebuild"

# --- Fetch and verify the ISO -------------------------------------------------------
if [[ ! -f $ISO || -f $ISO.part ]]; then
    echo "Downloading $MIRROR/$ISO"
    touch "$ISO.part"
    wget -c -q -O "$ISO" "$MIRROR/$ISO" || die "could not download $MIRROR/$ISO"
    rm "$ISO.part"
fi
want=$(wget -q -O - "$MIRROR/md5.txt" | sed -n "s/^MD5 ($ISO) = //p") ||
    die "could not download $MIRROR/md5.txt"
[[ -n $want && $(md5sum "$ISO" | cut -d' ' -f1) == "$want" ]] || die "$ISO does not match md5.txt"

work=$(mktemp -d)
media=$(mktemp -p . --suffix=.iso install-media.XXXXXX)
cleanup() { chmod -R u+w "$work"; rm -rf "$work" "$media"; }
trap cleanup EXIT

umask 077
password=$(openssl rand -base64 18 | tr -dc 'A-Za-z0-9')
printf '%s\n' "$password" >root-password
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$guest_name test guest" -f id_ed25519
umask 022

# --- Install media: the console on the serial port, our script as rc.local --------
# The script only goes into the install media, inside this host, so the plain
# password in it goes nowhere else.
sed -e "s|@HOSTNAME@|$guest_name|" -e "s|@ROOT_PASSWORD@|$password|" \
    -e "s|@AUTHORIZED_KEY@|$(cat id_ed25519.pub)|" \
    "$here/dragonfly/rc.local" >"$work/rc.local"
if grep -n '@[A-Z_]\+@' "$work/rc.local"; then die "rc.local placeholders left unfilled"; fi
xorriso -osirrox on -indev "$ISO" -extract /boot/loader.conf "$work/loader.conf" 2>/dev/null ||
    die "$ISO lacks /boot/loader.conf"
chmod u+w "$work/loader.conf"
# boot_serial: the kernel's console too, not only the loader's.
printf '%s\n' 'console="comconsole"' 'boot_serial="YES"' 'autoboot_delay="1"' >>"$work/loader.conf"
rm -f "$media"
# Keep the plain ISO 9660 names as the stock CD spells them: the loader reads
# those, and by default xorriso would rewrite them (KERNEL.;1 and so on).
xorriso -indev "$ISO" -outdev "$media" -boot_image any replay \
    -compliance long_names:omit_version:no_force_dots:allow_dir_id_ext \
    -map "$work/rc.local" /etc/rc.local -map "$work/loader.conf" /boot/loader.conf \
    -chmod 0755 /etc/rc.local -- \
    2>"$work/xorriso.log" || { cat "$work/xorriso.log" >&2; die "could not write the install media"; }

# --- Install --------------------------------------------------------------------------
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing; the live system's console goes to $2/install-serial.log"
timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 2 -m "$MEMORY" -machine pc \
    -drive "file=$DISK,if=virtio,format=qcow2" \
    -drive "file=$media,media=cdrom,readonly=on" -boot order=d \
    -nic user,model=virtio-net-pci \
    -display none -serial file:install-serial.log -monitor none -no-reboot ||
    die "the install failed or timed out; see install-serial.log"
grep -aq BACKOTTER-INSTALL-OK install-serial.log || {
    grep -a BACKOTTER-INSTALL-FAILED install-serial.log >&2 || true
    die "the install did not finish; see install-serial.log"
}

qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
