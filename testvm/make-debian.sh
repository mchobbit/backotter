#!/usr/bin/env bash
# Build a Debian test guest for BackOtter: an unattended install from an
# official DVD-1 into a qcow2 disk image, under QEMU with KVM.
#
# Usage: ./make-debian.sh VERSION ROOT_FS WORK_DIR
#   e.g. ./make-debian.sh 12.0.0 ext4 vms/debian12
#        ./make-debian.sh 13.0.0 btrfs vms/debian13
#
# VERSION is a Debian point release in https://cdimage.debian.org/cdimage/archive/;
# ROOT_FS is ext4 or btrfs.  debian-VERSION-amd64-DVD-1.iso with its
# SHA256SUMS and SHA256SUMS.sign is downloaded into WORK_DIR unless already
# there, and checked against Debian's signature before use.  Results, in
# WORK_DIR (N is the major version, e.g. 12):
#   debianN.qcow2         the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" of the finished install
#   root-password         root's password (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the installer's serial console
#
# The guest, host name debN: BIOS + MBR, /boot (1 GB ext4), swap (2 GB),
# / (ROOT_FS, the rest), 2 GB RAM, no desktop; see debian/preseed.cfg.  Run it
# with vm-run, and look inside it while it's off with vm-mount.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, xorriso, cpio, gpgv, openssl,
# wget.  Runs as an ordinary user in the kvm group.

set -euo pipefail

DISK_SIZE=16G
MEMORY=2048
# "Debian CD signing key <debian-cd@lists.debian.org>", per
# https://www.debian.org/CD/verify
CD_KEY=DF9B9C49EAA9298432589D76DA87E80D6294BE9B
INSTALL_TIMEOUT=3600

here=$(dirname "$(realpath "$0")")
die() { echo "make-debian: $*" >&2; exit 1; }

[[ $# -eq 3 ]] || die "usage: $0 VERSION ROOT_FS WORK_DIR"
version=$1 root_fs=$2
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "VERSION looks like 13.0.0, not '$version'"
[[ $root_fs == ext4 || $root_fs == btrfs ]] || die "ROOT_FS must be ext4 or btrfs"
major=${version%%.*}
ISO=debian-$version-amd64-DVD-1.iso
DISK=debian$major.qcow2
guest_name=deb$major
mkdir -p "$3"
cd "$3"
[[ -e $DISK ]] && die "$3/$DISK already exists; move it away to rebuild"

# --- Fetch and verify the DVD ------------------------------------------------------
url=https://cdimage.debian.org/cdimage/archive/$version/amd64/iso-dvd
for f in SHA256SUMS SHA256SUMS.sign "$ISO"; do
    [[ -f $f && ! -f $f.part ]] && continue
    echo "Downloading $url/$f"
    touch "$f.part"
    wget -c -q -O "$f" "$url/$f" || die "could not download $url/$f"
    rm "$f.part"
done

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
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$guest_name test guest" -f id_ed25519
umask 022

mkdir "$work/add"
sed -e "s|@ROOT_PASSWORD_HASH@|$hash|" -e "s|@HOSTNAME@|$guest_name|g" -e "s|@ROOT_FS@|$root_fs|g" \
    "$here/debian/preseed.cfg" >"$work/add/preseed.cfg"
if grep -n '@[A-Z_]\+@' "$work/add/preseed.cfg"; then die "preseed placeholders left unfilled"; fi
cp id_ed25519.pub "$work/add/authorized_keys"
# A second gzipped cpio archive appended to the initrd is unpacked on top of
# the first; the installer reads /preseed.cfg from it automatically.
(cd "$work/add" && printf '%s\n' preseed.cfg authorized_keys |
    cpio -o -H newc --quiet | gzip -9) >>"$work/initrd.gz"

# --- Install ----------------------------------------------------------------------
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing; the installer's console goes to $3/install-serial.log"
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
echo "Done: $3/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
