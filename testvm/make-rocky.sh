#!/usr/bin/env bash
# Build a Rocky Linux test guest for BackOtter (Rocky is a rebuild of Red Hat
# Enterprise Linux): an unattended Kickstart install from an official minimal
# ISO into a qcow2 disk image, under QEMU with KVM.
#
# Usage: ./make-rocky.sh VERSION WORK_DIR
#   e.g. ./make-rocky.sh 10.0 vms/rocky10
#
# VERSION is a Rocky release in https://dl.rockylinux.org/vault/rocky/.
# Rocky-VERSION-x86_64-minimal.iso with the release's CHECKSUM and
# CHECKSUM.asc is downloaded into WORK_DIR unless already there, and checked
# against the release key's signature before use.  Results, in WORK_DIR (N is
# the major version):
#   rockyN.qcow2          the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" of the finished install
#   root-password         root's password (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the installer's serial console
#
# The guest, host name rockyN: BIOS + MBR, /boot (1 GiB), swap (2 GiB),
# / (the rest), all XFS, 2 GB RAM, no desktop, its repositories pinned to the
# vault's copy of VERSION; see rocky/ks.cfg.  Rocky 10 needs an x86-64-v3 CPU
# (AVX2 and friends).  Run it with vm-run, and look inside it while it's off
# with vm-mount.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, xorriso, gpg, gpgv, openssl,
# python3, wget.  Runs as an ordinary user in the kvm group.

set -euo pipefail

DISK_SIZE=16G
MEMORY=2048
# Rocky's release keys, per https://rockylinux.org/resources/gpg-key-info
declare -A RELEASE_KEYS=(
    [10]=FC226859C0860BF0DDB95B085B106C736FEDFC85   # Release Engineering (Rocky Linux 10)
)
INSTALL_TIMEOUT=1800

here=$(dirname "$(realpath "$0")")
die() { echo "make-rocky: $*" >&2; exit 1; }

[[ $# -eq 2 ]] || die "usage: $0 VERSION WORK_DIR"
version=$1
[[ $version =~ ^[0-9]+\.[0-9]+$ ]] || die "VERSION looks like 10.0, not '$version'"
major=${version%%.*}
key_fpr=${RELEASE_KEYS[$major]:-}
[[ -n $key_fpr ]] || die "no release key known for Rocky $major; add it to RELEASE_KEYS"
ISO=Rocky-$version-x86_64-minimal.iso
DISK=rocky$major.qcow2
guest_name=rocky$major
mkdir -p "$2"
cd "$2"
[[ -e $DISK ]] && die "$2/$DISK already exists; move it away to rebuild"

# --- Fetch and verify the ISO -------------------------------------------------------
url=https://dl.rockylinux.org/vault/rocky/$version/isos/x86_64
for f in CHECKSUM CHECKSUM.asc "$ISO"; do
    [[ -f $f && ! -f $f.part ]] && continue
    echo "Downloading $url/$f"
    touch "$f.part"
    wget -c -q -O "$f" "$url/$f" || die "could not download $url/$f"
    rm "$f.part"
done
if [[ ! -s rocky-release-key.gpg ]]; then
    wget -q -O - "https://dl.rockylinux.org/pub/rocky/RPM-GPG-KEY-Rocky-$major" |
        gpg --dearmor >rocky-release-key.gpg
fi
# The key came over HTTPS from Rocky's server; it must also be the one Rocky
# publishes the fingerprint of.
got=$(gpg --show-keys --with-colons rocky-release-key.gpg 2>/dev/null | awk -F: '/^fpr/ { print $10; exit }')
[[ $got == "$key_fpr" ]] || die "release key fingerprint is $got, expected $key_fpr"
gpgv --keyring ./rocky-release-key.gpg CHECKSUM.asc CHECKSUM 2>/dev/null ||
    die "CHECKSUM is not signed by the Rocky $major release key"
grep -E "^SHA256 \($ISO\) = " CHECKSUM | sha256sum -c --quiet - || die "$ISO does not match CHECKSUM"

# --- Installer kernel, initrd and stage-2 location --------------------------------
work=$(mktemp -d)
server=""
# Files and directories extracted from the ISO are read-only.
cleanup() { [[ -n $server ]] && kill "$server" 2>/dev/null; chmod -R u+w "$work"; rm -rf "$work"; }
trap cleanup EXIT
xorriso -osirrox on -indev "$ISO" \
    -extract /images/pxeboot/vmlinuz "$work/vmlinuz" \
    -extract /images/pxeboot/initrd.img "$work/initrd.img" \
    -extract /EFI/BOOT/grub.cfg "$work/grub.cfg" 2>/dev/null ||
    die "$ISO lacks /images/pxeboot/{vmlinuz,initrd.img} or /EFI/BOOT/grub.cfg"
# The installer finds its second stage by the ISO's volume label.
stage2=$(grep -oE 'inst\.stage2=hd:LABEL=[^ ]+' "$work/grub.cfg" | head -n 1)
[[ -n $stage2 ]] || die "no inst.stage2= in the ISO's grub.cfg"

umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$guest_name test guest" -f id_ed25519
umask 022

mkdir "$work/www"
sed -e "s|@HOSTNAME@|$guest_name|" -e "s|@ROOT_PASSWORD_HASH@|$hash|" \
    -e "s|@AUTHORIZED_KEY@|$(cat id_ed25519.pub)|" -e "s|@VERSION@|$version|g" \
    "$here/rocky/ks.cfg" >"$work/www/ks.cfg"
if grep -n '@[A-Z_]\+@' "$work/www/ks.cfg"; then die "ks.cfg placeholders left unfilled"; fi

# Serve ks.cfg to the guest only: QEMU's user-mode network maps 10.0.2.2 in
# the guest to the host's 127.0.0.1.
port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
python3 -m http.server --bind 127.0.0.1 --directory "$work/www" "$port" >"$work/http.log" 2>&1 &
server=$!

# --- Install --------------------------------------------------------------------------
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing; the installer's console goes to $2/install-serial.log"
timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 2 -m "$MEMORY" -machine pc \
    -drive "file=$DISK,if=virtio,format=qcow2" \
    -drive "file=$ISO,media=cdrom,readonly=on" \
    -nic user,model=virtio-net-pci \
    -kernel "$work/vmlinuz" -initrd "$work/initrd.img" \
    -append "$stage2 inst.ks=http://10.0.2.2:$port/ks.cfg inst.text console=tty0 console=ttyS0,115200n8" \
    -display none -serial file:install-serial.log -monitor none -no-reboot ||
    die "the install failed or timed out; see install-serial.log"
# Kickstart's poweroff only comes at the very end (a failed step waits for an
# answer instead, until the timeout), but check that a system was written.
[[ $(qemu-img info --output=json "$DISK" | python3 -c 'import json, sys; print(json.load(sys.stdin)["actual-size"])') -gt 500000000 ]] ||
    die "the disk is nearly empty; see install-serial.log"

qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
