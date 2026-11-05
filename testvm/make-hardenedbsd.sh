#!/usr/bin/env bash
# Build a HardenedBSD test guest for BackOtter: an unattended install from
# the project's disc1 ISO into a qcow2 disk image, under QEMU with KVM.
# HardenedBSD is FreeBSD with exploit mitigations, and its installer is
# FreeBSD's bsdinstall, so this is make-freebsd.sh's install with
# HardenedBSD's media.
#
# Usage: ./make-hardenedbsd.sh BRANCH BUILD ROOT_FS WORK_DIR
#   e.g. ./make-hardenedbsd.sh 15 23 ufs vms/hardenedbsd15
#
# BRANCH is a stable branch's major version (15 for 15-STABLE) and BUILD
# the number of one of its installer builds, as listed in
# https://installers.hardenedbsd.org/pub/15-stable/amd64/amd64/installer/
# (build-23 there): HardenedBSD makes no releases, only these numbered
# snapshots of its branches.  ROOT_FS is ufs or zfs.  The ISO and its
# signature are downloaded into WORK_DIR unless already there, from MIRROR
# (default https://installers.hardenedbsd.org/pub, which sends some 400 KB/s;
# the mirrors at https://hardenedbsd.org/content/mirrors are faster, e.g.
# https://mirror.laylo.nl/pub/hardenedbsd), and checked before use: every
# build artifact carries an SSH signature (ssh-keygen -Y sign) by the build
# server's key, which is pinned below.  Results, in WORK_DIR (N is BRANCH):
#   hardenedbsdN.qcow2    the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" of the finished install
#   root-password         root's password (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the installer's serial console
#   vm-run-options        with CPUS or MEMORY set: "--cpus N --memory MB",
#                         how vm-run must boot it
#
# The guest, host name hbsdN: BIOS + GPT, freebsd-boot (first on zfs; on
# ufs the installer puts it second, after swap), swap (2 GB), / (ROOT_FS, the
# rest; ufs with TRIM on, or zfs: pool "zroot" with boot environments), 2
# CPUs, 2 GB RAM, the base system with DHCP and sshd and nothing else, not
# even pkg; see hardenedbsd/installerconfig.  Its console is serial and
# screen both.  Its kernel is the branch's stock HARDENEDBSD one, which on
# -STABLE has WITNESS (lock checking) on: slower than FreeBSD's, so its
# timings don't compare with freebsd15's.  It updates its base system with
# hbsd-update (it has no freebsd-update).  Run
# it with vm-run (and vm-run-options).  vm-mount can't open it: Linux can't
# write UFS, and the ZFS pool would have to be imported on this host.
#
# From the environment: DISK_SIZE (16G), CPUS (2), MEMORY (MB, 2048), and
# GUEST_NAME for the host name (hbsdN).
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, xorriso, ssh-keygen (OpenSSH
# 8.1 or later, for -Y verify), openssl, wget.  Runs as an ordinary user in
# the kvm group.

set -euo pipefail

DISK_SIZE=${DISK_SIZE:-16G}
CPUS=${CPUS:-2}
MEMORY=${MEMORY:-2048}
MIRROR=${MIRROR:-https://installers.hardenedbsd.org/pub}
# The build server's signing key, as https://hardenedbsd.org/node/254
# ("Verifying Build Artifacts") and installers.hardenedbsd.org/pub/keys
# both publish it; SHA256:s0kT851lT007DxaQpsoXG7bDg7QML7p4waDOGRSFsPo.
SIGNER=hbsd-os-build-01
SIGNING_KEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPpi/BYqEClmw7VCmpPNrTtUgiL+Fc49qy3bgabDlf9F"
INSTALL_TIMEOUT=3600

here=$(dirname "$(realpath "$0")")
die() { echo "make-hardenedbsd: $*" >&2; exit 1; }

[[ $# -eq 4 ]] || die "usage: $0 BRANCH BUILD ROOT_FS WORK_DIR"
major=$1 build=$2 root_fs=$3
[[ $major =~ ^[0-9]+$ ]] || die "BRANCH looks like 15, not '$major'"
[[ $build =~ ^[0-9]+$ ]] || die "BUILD looks like 23, not '$build'"
case $root_fs in zfs|ufs) ;; *) die "ROOT_FS must be zfs or ufs" ;; esac

ISO=HardenedBSD-$major-STABLE-build-$build-amd64-disc1.iso
iso_url=$MIRROR/$major-stable/amd64/amd64/installer/build-$build/disc1.iso
guest_name=${GUEST_NAME:-hbsd$major}
[[ $guest_name =~ ^[a-z0-9][a-z0-9-]*$ ]] || die "GUEST_NAME '$guest_name' isn't a host name"
DISK=hardenedbsd$major.qcow2
mkdir -p "$4"
cd "$4"
[[ -e $DISK ]] && die "$4/$DISK already exists; move it away to rebuild"

# --- Fetch and verify the ISO -------------------------------------------------------
download() {   # URL FILE
    [[ -f $2 && ! -f $2.part ]] && return
    echo "Downloading $1"
    touch "$2.part"
    wget -c -q -O "$2" "$1" || die "could not download $1"
    rm "$2.part"
}
download "$iso_url.sig" "$ISO.sig"
download "$iso_url" "$ISO"
echo "$SIGNER $SIGNING_KEY" >hardenedbsd-allowed-signers
ssh-keygen -Y verify -f hardenedbsd-allowed-signers -I "$SIGNER" -n file -s "$ISO.sig" <"$ISO" >/dev/null ||
    die "$ISO is not signed by $SIGNER"

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

sed -e "s|@ROOT_PASSWORD_HASH@|$hash|" -e "s|@HOSTNAME@|$guest_name|g" -e "s|@ROOT_FS@|$root_fs|g" \
    -e "s|@AUTHORIZED_KEY@|$(cat id_ed25519.pub)|" \
    "$here/hardenedbsd/installerconfig" >"$work/installerconfig"
if grep -n '@[A-Z_]\+@' "$work/installerconfig"; then die "installerconfig placeholders left unfilled"; fi
xorriso -osirrox on -indev "$ISO" -extract /boot/loader.conf "$work/loader.conf" 2>/dev/null ||
    die "$ISO lacks /boot/loader.conf"
chmod u+w "$work/loader.conf"
# The installer's loader and kernel on the serial console, where QEMU logs it.
printf '%s\n' 'console="comconsole"' 'autoboot_delay="1"' >>"$work/loader.conf"
rm -f "$media"
# As on FreeBSD's media, the El Torito images are hidden outside the file
# tree, so xorriso can't carry them over; boot the copy from /boot/cdboot
# instead (BIOS only, as the guest is).
xorriso -indev "$ISO" -outdev "$media" \
    -boot_image any discard -boot_image any bin_path=/boot/cdboot \
    -boot_image any emul_type=no_emulation -boot_image any load_size=2048 \
    -map "$work/installerconfig" /etc/installerconfig \
    -map "$here/hardenedbsd/rc.local" /etc/rc.local \
    -map "$work/loader.conf" /boot/loader.conf \
    -chown 0 /etc/installerconfig /etc/rc.local /boot/loader.conf -- \
    -chgrp 0 /etc/installerconfig /etc/rc.local /boot/loader.conf -- \
    -chmod 0644 /etc/installerconfig /boot/loader.conf -- \
    -chmod 0755 /etc/rc.local -- 2>"$work/xorriso.log" ||
    { cat "$work/xorriso.log" >&2; die "could not write the install media"; }

# --- Install ----------------------------------------------------------------------
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing; the installer's console goes to $4/install-serial.log"
# The install script reboots when it's done, which -no-reboot turns into
# QEMU exiting; it powers off if the install failed.
timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp "$CPUS" -m "$MEMORY" -machine pc \
    -drive "file=$DISK,if=virtio,format=qcow2" \
    -drive "file=$media,media=cdrom,readonly=on" -boot order=d \
    -nic user,model=virtio-net-pci \
    -display none -serial file:install-serial.log -monitor none -no-reboot ||
    die "the install failed or timed out; see install-serial.log"
grep -q '^BACKOTTER-INSTALL: OK' install-serial.log ||
    die "the install failed; see install-serial.log"

qemu-img snapshot -c fresh-install "$DISK"
[[ $CPUS == 2 && $MEMORY == 2048 ]] || echo "--cpus $CPUS --memory $MEMORY" >vm-run-options
echo "Done: $4/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
