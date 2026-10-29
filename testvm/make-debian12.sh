#!/usr/bin/env bash
# Build BackOtter's Debian 12.0 test guest: an unattended install from the
# official 12.0.0 DVD-1 into a qcow2 disk image, under QEMU with KVM.
#
# Usage: ./make-debian12.sh WORK_DIR
#
# WORK_DIR must hold debian-12.0.0-amd64-DVD-1.iso with the SHA256SUMS and
# SHA256SUMS.sign from the same Debian archive directory
# (https://cdimage.debian.org/cdimage/archive/12.0.0/amd64/iso-dvd/).
# The ISO is checked against them before use.  Results, in WORK_DIR:
#   debian12.qcow2        the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" of the finished install
#   root-password         root's password (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the installer's serial console
#
# The guest: BIOS + MBR, /boot (1 GB ext4), swap (2 GB), / (ext4, the rest),
# 2 GB RAM, no desktop; see debian12/preseed.cfg.  Run it with vm-run, and
# look inside it while it's off with vm-mount.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, xorriso, cpio, gpgv, openssl.
# Runs as an ordinary user in the kvm group.

set -euo pipefail

ISO=debian-12.0.0-amd64-DVD-1.iso
DISK=debian12.qcow2
DISK_SIZE=16G
MEMORY=2048
# "Debian CD signing key <debian-cd@lists.debian.org>", per
# https://www.debian.org/CD/verify
CD_KEY=DF9B9C49EAA9298432589D76DA87E80D6294BE9B
INSTALL_TIMEOUT=3600

here=$(dirname "$(realpath "$0")")
die() { echo "make-debian12: $*" >&2; exit 1; }

[[ $# -eq 1 ]] || die "usage: $0 WORK_DIR"
cd "$1"
for f in "$ISO" SHA256SUMS SHA256SUMS.sign; do [[ -f $f ]] || die "missing $1/$f"; done
[[ -e $DISK ]] && die "$1/$DISK already exists; move it away to rebuild"

# --- Verify the DVD ------------------------------------------------------------
if [[ ! -s debian-cd-key.gpg ]]; then
    # Fetched by full fingerprint, so the keyserver can't substitute a key.
    gnupg=$(mktemp -d)
    GNUPGHOME=$gnupg gpg -q --keyserver hkps://keyring.debian.org --recv-keys "$CD_KEY"
    GNUPGHOME=$gnupg gpg -q --export "$CD_KEY" >debian-cd-key.gpg
    rm -rf "$gnupg"
fi
gpgv --keyring ./debian-cd-key.gpg SHA256SUMS.sign SHA256SUMS 2>/dev/null ||
    die "SHA256SUMS is not signed by the Debian CD key"
grep " $ISO\$" SHA256SUMS | sha256sum -c --quiet - || die "$ISO does not match SHA256SUMS"

# --- Installer kernel and initrd, with the preseed added ------------------------
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
xorriso -osirrox on -indev "$ISO" \
    -extract /install.amd/vmlinuz "$work/vmlinuz" \
    -extract /install.amd/initrd.gz "$work/initrd.gz" 2>/dev/null
chmod u+w "$work"/*

umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@deb12 test guest" -f id_ed25519
umask 022

mkdir "$work/add"
sed "s|@ROOT_PASSWORD_HASH@|$hash|" "$here/debian12/preseed.cfg" >"$work/add/preseed.cfg"
cp id_ed25519.pub "$work/add/authorized_keys"
# A second gzipped cpio archive appended to the initrd is unpacked on top of
# the first; the installer reads /preseed.cfg from it automatically.
(cd "$work/add" && printf '%s\n' preseed.cfg authorized_keys |
    cpio -o -H newc --quiet | gzip -9) >>"$work/initrd.gz"

# --- Install ----------------------------------------------------------------------
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing; the installer's console goes to $1/install-serial.log"
# DEBIAN_FRONTEND=text: a plain-text installer, readable in the serial log.
# Arguments after "---" are kept on the installed system's kernel command
# line, so it has a serial console too.
timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 2 -m "$MEMORY" -machine pc \
    -drive "file=$DISK,if=virtio,format=qcow2" \
    -drive "file=$ISO,media=cdrom,readonly=on" \
    -nic user,model=virtio-net-pci \
    -kernel "$work/vmlinuz" -initrd "$work/initrd.gz" \
    -append "auto=true priority=critical DEBIAN_FRONTEND=text console=ttyS0,115200n8 --- console=tty0 console=ttyS0,115200n8" \
    -display none -serial file:install-serial.log -monitor none -no-reboot ||
    die "the install failed or timed out; see install-serial.log"
# The installer only powers off at the very end (a failed step waits for an
# answer instead, until the timeout), but check that a system was written.
[[ $(qemu-img info --output=json "$DISK" | python3 -c 'import json, sys; print(json.load(sys.stdin)["actual-size"])') -gt 500000000 ]] ||
    die "the disk is nearly empty; see install-serial.log"

qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $1/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
