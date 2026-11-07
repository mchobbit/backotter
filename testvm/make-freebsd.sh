#!/usr/bin/env bash
# Build a FreeBSD test guest for BackOtter: an unattended install from an
# official ISO into a qcow2 disk image, under QEMU with KVM.
#
# Usage: ./make-freebsd.sh [--uefi-geli | --zfs-mirror] VERSION ROOT_FS WORK_DIR
#   e.g. ./make-freebsd.sh 15.1 zfs vms/freebsd15
#        ./make-freebsd.sh 14.5 ufs vms/freebsd14-ufs
#        ./make-freebsd.sh --uefi-geli 15.1 zfs vms/freebsd15-geli
#        ./make-freebsd.sh --zfs-mirror 15.1 zfs vms/freebsd15-mirror
#
# VERSION is a release in https://download.freebsd.org/releases/ISO-IMAGES/,
# installed from its disc1 ISO.  ROOT_FS is zfs or ufs.  FreeBSD publishes
# no signed checksum file; the ISO's checksum is instead taken from the
# release announcement, which the release engineer signs.  The ISO and the
# announcement are downloaded into WORK_DIR unless already there, and checked
# before use.  Results, in WORK_DIR (N is the major version, e.g. 15):
#   freebsdN.qcow2        the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" of the finished install
#   root-password         root's password (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the installer's serial console
#   vm-run-options        with CPUS or MEMORY set: "--cpus N --memory MB",
#                         how vm-run must boot it; with --uefi-geli,
#                         "--uefi --no-secure-boot" (the loader isn't signed)
#   console-passphrase    with --uefi-geli: GELI's passphrase (mode 600),
#                         which the loader asks for on the console at boot
#   zroot-b.qcow2         with --zfs-mirror: the second disk, the same size,
#                         snapshot "fresh-install" too; vm-run-options says
#                         "--disk zroot-b.qcow2" (a name that sorts after
#                         freebsdN.qcow2, so that stays the boot image)
#
# The guest, host name fbsdN: BIOS + GPT, swap (2 GB), / (ROOT_FS, the rest;
# for zfs, pool "zroot" with boot environments), 2 CPUs, 2 GB RAM, the base
# system and nothing else; see freebsd/installerconfig.  Its console is
# serial and screen both.  Run it with vm-run (and vm-run-options).  vm-mount
# can't open it: Linux can't write UFS, and the ZFS pool would have to be
# imported on this host.
#
# --uefi-geli (zfs only): UEFI instead, with an EFI system partition (the
# loader on its removable path, EFI/BOOT/BOOTX64.EFI, as vm-run's fresh
# variable store needs), and the pool on GELI (AES-XTS, as bsdinstall's
# "Encrypt Disks?" makes it), unlocked by the loader with the passphrase it
# asks for, which vm-update, vm-lib and SticTest1 type from
# console-passphrase.  Host name fbsdN-geli.
#
# --zfs-mirror (zfs only): pool "zroot" a two-way mirror of vtbd0 and vtbd1
# (bsdinstall's ZFSBOOT_DISKS and ZFSBOOT_VDEV_TYPE=mirror).  BIOS boot, so
# each disk gets its own freebsd-boot partition with gptzfsboot, and either
# one boots alone; each its own 2 GB swap partition, both in fstab (no
# gmirror: bko-holt doesn't rebuild one).  Host name fbsdN-mirror.
#
# A bigger guest, e.g. for building (see freebsd/builder-setup.sh), from the
# environment: DISK_SIZE (16G), CPUS (2), MEMORY (MB, 2048), and GUEST_NAME
# for the host name (fbsdN).
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, xorriso, gpg, gpgv, openssl,
# wget.  Runs as an ordinary user in the kvm group.

set -euo pipefail

DISK_SIZE=${DISK_SIZE:-16G}
CPUS=${CPUS:-2}
MEMORY=${MEMORY:-2048}
# Colin Percival, FreeBSD release engineering lead, who signs the release
# announcements; listed in https://docs.freebsd.org/pgpkeys/pgpkeys.txt.
RE_KEY=82563B84D0620EDC0DFB86413897F2E22E65AD3F
KEYSERVER=hkps://keyserver.ubuntu.com
INSTALL_TIMEOUT=3600

here=$(dirname "$(realpath "$0")")
die() { echo "make-freebsd: $*" >&2; exit 1; }

geli=no mirror=no
while [[ ${1:-} == --* ]]; do
    case $1 in
    --uefi-geli) geli=yes ;;
    --zfs-mirror) mirror=yes ;;
    *) die "unknown option $1" ;;
    esac
    shift
done
[[ $# -eq 3 ]] || die "usage: $0 [--uefi-geli | --zfs-mirror] VERSION ROOT_FS WORK_DIR"
version=$1 root_fs=$2
[[ $version =~ ^[0-9]+\.[0-9]+$ ]] || die "VERSION looks like 15.1, not '$version'"
case $root_fs in zfs|ufs) ;; *) die "ROOT_FS must be zfs or ufs" ;; esac
[[ $geli == no || $root_fs == zfs ]] || die "--uefi-geli needs ROOT_FS zfs"
[[ $mirror == no || $root_fs == zfs ]] || die "--zfs-mirror needs ROOT_FS zfs"
[[ $mirror == no || $geli == no ]] || die "--zfs-mirror and --uefi-geli don't go together"
major=${version%%.*}

ISO=FreeBSD-$version-RELEASE-amd64-disc1.iso
ANNOUNCE=announce-$version.asc
iso_url=https://download.freebsd.org/releases/ISO-IMAGES/$version/$ISO
announce_url=https://www.freebsd.org/releases/${version}R/announce.asc
guest_name=${GUEST_NAME:-fbsd$major}
[[ $geli == yes ]] && guest_name=${GUEST_NAME:-fbsd$major-geli}
[[ $mirror == yes ]] && guest_name=${GUEST_NAME:-fbsd$major-mirror}
[[ $guest_name =~ ^[a-z0-9][a-z0-9-]*$ ]] || die "GUEST_NAME '$guest_name' isn't a host name"
DISK=freebsd$major.qcow2 DISK_B=zroot-b.qcow2
mkdir -p "$3"
cd "$3"
[[ -e $DISK ]] && die "$3/$DISK already exists; move it away to rebuild"
[[ $mirror == yes && -e $DISK_B ]] && die "$3/$DISK_B already exists; move it away to rebuild"

# --- Fetch and verify the ISO -------------------------------------------------------
download() {   # URL FILE
    [[ -f $2 && ! -f $2.part ]] && return
    echo "Downloading $1"
    touch "$2.part"
    wget -c -q -O "$2" "$1" || die "could not download $1"
    rm "$2.part"
}
download "$announce_url" "$ANNOUNCE"
download "$iso_url" "$ISO"
keyring=freebsd-re-key.gpg
if [[ ! -s $keyring ]]; then
    # Fetched by full fingerprint, so the keyserver can't substitute a key.
    gnupg=$(mktemp -d)
    GNUPGHOME=$gnupg gpg -q --keyserver "$KEYSERVER" --recv-keys "$RE_KEY"
    GNUPGHOME=$gnupg gpg -q --export "$RE_KEY" >"$keyring"
    rm -rf "$gnupg"
fi
# Only the signed text counts: gpgv writes it out, without anything an
# attacker might have added around the signature.
signed=$(gpgv --keyring "./$keyring" --output - "$ANNOUNCE" 2>/dev/null) ||
    die "$ANNOUNCE is not signed by $RE_KEY"
want=$(sed -n "s/^ *SHA256 ($ISO) = \([0-9a-f]\{64\}\)\$/\1/p" <<<"$signed")
[[ -n $want ]] || die "$ANNOUNCE lists no SHA256 for $ISO"
echo "$want  $ISO" | sha256sum -c --quiet - || die "$ISO does not match $ANNOUNCE"

# --- Install media, with the install script added ------------------------------
work=$(mktemp -d)
media=$(mktemp -p . --suffix=.iso install-media.XXXXXX)
trap 'rm -rf "$work" "$media"' EXIT

umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$guest_name test guest" -f id_ed25519
umask 022

boot_type=BIOS extra_map=() extra_files=() zfs_disks=vtbd0 zfs_vdev=stripe
[[ $mirror == yes ]] && zfs_disks="vtbd0 vtbd1" zfs_vdev=mirror
if [[ $geli == yes ]]; then
    boot_type=UEFI
    umask 077
    openssl rand -base64 24 | tr -dc 'A-Za-z0-9' >console-passphrase
    umask 022
    # Not on a command line, where every process on the host sees it.
    python3 -c 'import sys; k = open("console-passphrase").read().strip()
sys.stdout.write(open(sys.argv[1]).read().replace("@GELI_PASSPHRASE@", k))' \
        "$here/freebsd/bsddialog-geli" >"$work/bsddialog-geli"
    if grep -n '@[A-Z_]\+@' "$work/bsddialog-geli"; then die "bsddialog-geli placeholders left unfilled"; fi
    extra_map=(-map "$work/bsddialog-geli" /etc/bsddialog-geli)
    extra_files=(/etc/bsddialog-geli)
fi
sed -e "s|@ROOT_PASSWORD_HASH@|$hash|" -e "s|@HOSTNAME@|$guest_name|g" -e "s|@ROOT_FS@|$root_fs|g" \
    -e "s|@AUTHORIZED_KEY@|$(cat id_ed25519.pub)|" -e "s|@BOOT_TYPE@|$boot_type|" \
    -e "s|@ZFS_DISKS@|$zfs_disks|" -e "s|@ZFS_VDEV_TYPE@|$zfs_vdev|" \
    "$here/freebsd/installerconfig" >"$work/installerconfig"
if grep -n '@[A-Z_]\+@' "$work/installerconfig"; then die "installerconfig placeholders left unfilled"; fi
xorriso -osirrox on -indev "$ISO" -extract /boot/loader.conf "$work/loader.conf" 2>/dev/null ||
    die "$ISO lacks /boot/loader.conf"
chmod u+w "$work/loader.conf"
# The installer's loader and kernel on the serial console, where QEMU logs it.
printf '%s\n' 'console="comconsole"' 'autoboot_delay="1"' >>"$work/loader.conf"
rm -f "$media"
# The ISO's own El Torito images are hidden outside its file tree, so xorriso
# can't carry them over; boot the copy from /boot/cdboot instead (BIOS only:
# the installer runs by BIOS even when it installs a UEFI guest, --uefi-geli).
xorriso -indev "$ISO" -outdev "$media" \
    -boot_image any discard -boot_image any bin_path=/boot/cdboot \
    -boot_image any emul_type=no_emulation -boot_image any load_size=2048 \
    -map "$work/installerconfig" /etc/installerconfig \
    -map "$here/freebsd/rc.local" /etc/rc.local \
    -map "$work/loader.conf" /boot/loader.conf "${extra_map[@]}" \
    -chown 0 /etc/installerconfig /etc/rc.local /boot/loader.conf "${extra_files[@]}" -- \
    -chgrp 0 /etc/installerconfig /etc/rc.local /boot/loader.conf "${extra_files[@]}" -- \
    -chmod 0644 /etc/installerconfig /boot/loader.conf "${extra_files[@]}" -- \
    -chmod 0755 /etc/rc.local -- 2>"$work/xorriso.log" ||
    { cat "$work/xorriso.log" >&2; die "could not write the install media"; }

# --- Install ----------------------------------------------------------------------
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
drive_b=()
if [[ $mirror == yes ]]; then
    qemu-img create -q -f qcow2 "$DISK_B" "$DISK_SIZE"
    drive_b=(-drive "file=$DISK_B,if=virtio,format=qcow2")
fi
echo "Installing; the installer's console goes to $3/install-serial.log"
# The install script reboots when it's done, which -no-reboot turns into
# QEMU exiting; it powers off if the install failed.
timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp "$CPUS" -m "$MEMORY" -machine pc \
    -drive "file=$DISK,if=virtio,format=qcow2" "${drive_b[@]}" \
    -drive "file=$media,media=cdrom,readonly=on" -boot order=d \
    -nic user,model=virtio-net-pci \
    -display none -serial file:install-serial.log -monitor none -no-reboot ||
    die "the install failed or timed out; see install-serial.log"
grep -q '^BACKOTTER-INSTALL: OK' install-serial.log ||
    die "the install failed; see install-serial.log"

qemu-img snapshot -c fresh-install "$DISK"
[[ $mirror == no ]] || qemu-img snapshot -c fresh-install "$DISK_B"
opts=()
[[ $geli == yes ]] && opts+=(--uefi --no-secure-boot)
[[ $mirror == yes ]] && opts+=(--disk "$DISK_B")
[[ $CPUS == 2 && $MEMORY == 2048 ]] || opts+=(--cpus "$CPUS" --memory "$MEMORY")
[[ ${#opts[@]} -eq 0 ]] || echo "${opts[*]}" >vm-run-options
echo "Done: $3/$DISK ($(du -h "$DISK" | cut -f1) on disk)${drive_b[*]:+ and $DISK_B ($(du -h "$DISK_B" | cut -f1))}, snapshot \"fresh-install\""
