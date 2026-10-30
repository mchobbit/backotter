#!/usr/bin/env bash
# Build a Fedora Server test guest for BackOtter: an unattended Kickstart
# install from an official Server DVD into a qcow2 disk image, under QEMU with
# KVM.  / is btrfs, laid out the way Fedora's installer does it.
#
# Usage: ./make-fedora.sh [--uefi] [--name NAME] RELEASE WORK_DIR
#   e.g. ./make-fedora.sh 44-1.7 vms/fedora44
#        ./make-fedora.sh --uefi --name fedora44-uefi 44-1.7 vms/fedora44-uefi
#
# RELEASE is a Fedora release and compose number, as in the ISO's name
# (Fedora-Server-dvd-x86_64-RELEASE.iso).  The ISO and its CHECKSUM file are
# downloaded into WORK_DIR unless already there (from dl.fedoraproject.org,
# or the archive for older releases), and checked against the release key's
# signature before use.  Results, in WORK_DIR (NAME is fedoraN by default,
# N the release number):
#   NAME.qcow2            the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" of the finished install
#   root-password         root's password (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the installer's serial console
#   vm-run-options        with --uefi: "--uefi", how vm-run must boot it
#
# The guest, host name NAME: BIOS + MBR, /boot (1 GiB ext4), swap (2 GiB),
# and btrfs for the rest (subvolumes root and home), 2 GB RAM, the Fedora
# Server package set, no desktop, update repositories turned off; see
# fedora/ks.cfg and fedora/storage-bios.ks.
#
# --uefi installs for UEFI instead, the disk laid out by Anaconda's own
# automatic partitioning with btrfs chosen: GPT with an EFI system partition,
# /boot (XFS) and a btrfs volume compressed with zstd, swap on zram only
# (fedora/storage-uefi.ks).  It boots through shim and GRUB with Secure Boot
# on: vm-run --uefi.  The installer itself runs with Secure Boot off, as
# QEMU's -kernel boot has no shim to vouch for the kernel; the guest's NVRAM
# boot entries aren't kept, so each boot starts from EFI/BOOT/BOOTX64.EFI, as
# on a new machine.
#
# Run it with vm-run (and vm-run-options), and look inside it while it's off
# with vm-mount.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, xorriso, gpg, gpgv, openssl,
# python3, wget.  Runs as an ordinary user in the kvm group.

set -euo pipefail

DISK_SIZE=16G
MEMORY=2048
# Fedora's release keys, per https://fedoraproject.org/security/
declare -A RELEASE_KEYS=(
    [44]=36F612DCF27F7D1A48A835E4DBFCF71C6D9F90A6   # Fedora (44)
)
INSTALL_TIMEOUT=2400

here=$(dirname "$(realpath "$0")")
die() { echo "make-fedora: $*" >&2; exit 1; }

firmware=bios name=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --uefi) firmware=uefi; shift ;;
        --name) name=$2; shift 2 ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
[[ $# -eq 2 ]] || die "usage: $0 [--uefi] [--name NAME] RELEASE WORK_DIR"
release=$1
[[ $release =~ ^([0-9]+)-[0-9]+\.[0-9]+$ ]] || die "RELEASE looks like 44-1.7, not '$release'"
major=${BASH_REMATCH[1]}
key_fpr=${RELEASE_KEYS[$major]:-}
[[ -n $key_fpr ]] || die "no release key known for Fedora $major; add it to RELEASE_KEYS"
ISO=Fedora-Server-dvd-x86_64-$release.iso
SUMS=Fedora-Server-$major-${release#*-}-x86_64-CHECKSUM
guest_name=${name:-fedora$major}
DISK=$guest_name.qcow2
mkdir -p "$2"
cd "$2"
[[ -e $DISK ]] && die "$2/$DISK already exists; move it away to rebuild"

# --- Fetch and verify the ISO -------------------------------------------------------
url=""
for base in "https://dl.fedoraproject.org/pub/fedora/linux/releases/$major/Server/x86_64/iso" \
            "https://archives.fedoraproject.org/pub/archive/fedora/linux/releases/$major/Server/x86_64/iso"; do
    if wget -q --spider "$base/$SUMS"; then url=$base; break; fi
done
[[ -n $url ]] || die "can't find $SUMS on Fedora's servers"
for f in "$SUMS" "$ISO"; do
    [[ -f $f && ! -f $f.part ]] && continue
    echo "Downloading $url/$f"
    touch "$f.part"
    wget -c -q -O "$f" "$url/$f" || die "could not download $url/$f"
    rm "$f.part"
done
if [[ ! -s fedora-release-key.gpg ]]; then
    gnupg=$(mktemp -d)
    wget -q -O "$gnupg/all.gpg" https://fedoraproject.org/fedora.gpg
    GNUPGHOME=$gnupg gpg -q --import "$gnupg/all.gpg" 2>/dev/null
    GNUPGHOME=$gnupg gpg -q --export "$key_fpr" >fedora-release-key.gpg
    rm -rf "$gnupg"
fi
got=$(gpg --show-keys --with-colons fedora-release-key.gpg 2>/dev/null | awk -F: '/^fpr/ { print $10; exit }')
[[ $got == "$key_fpr" ]] || die "release key fingerprint is '$got', expected $key_fpr"
# The CHECKSUM file is clear-signed: take the checksum only from the signed text.
gpgv --keyring ./fedora-release-key.gpg --output "$SUMS.verified" "$SUMS" 2>/dev/null ||
    die "$SUMS is not signed by the Fedora $major key"
grep -E "^SHA256 \($ISO\) = " "$SUMS.verified" | sha256sum -c --quiet - || die "$ISO does not match $SUMS"

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
    -e "s|@AUTHORIZED_KEY@|$(cat id_ed25519.pub)|" \
    -e "/^@STORAGE@\$/{r $here/fedora/storage-$firmware.ks" -e "d}" "$here/fedora/ks.cfg" >"$work/www/ks.cfg"
if grep -n '@[A-Z_]\+@' "$work/www/ks.cfg"; then die "ks.cfg placeholders left unfilled"; fi

# Serve ks.cfg to the guest only: QEMU's user-mode network maps 10.0.2.2 in
# the guest to the host's 127.0.0.1.
port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
python3 -m http.server --bind 127.0.0.1 --directory "$work/www" "$port" >"$work/http.log" 2>&1 &
server=$!

# --- Install --------------------------------------------------------------------------
machine=(-machine pc)
if [[ $firmware == uefi ]]; then
    # OVMF without Secure Boot, and a variable store thrown away afterwards.
    cp /usr/share/OVMF/OVMF_VARS_4M.fd "$work/vars.fd"
    machine=(-machine q35
        -drive if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd
        -drive "if=pflash,format=raw,unit=1,file=$work/vars.fd")
fi
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing; the installer's console goes to $2/install-serial.log"
timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 2 -m "$MEMORY" "${machine[@]}" \
    -drive "file=$DISK,if=virtio,format=qcow2" \
    -drive "file=$ISO,media=cdrom,readonly=on" \
    -nic user,model=virtio-net-pci \
    -kernel "$work/vmlinuz" -initrd "$work/initrd.img" \
    -append "$stage2 inst.ks=http://10.0.2.2:$port/ks.cfg inst.text console=tty0 console=ttyS0,115200n8" \
    -display none -serial file:install-serial.log -monitor none -no-reboot ||
    die "the install failed or timed out; see install-serial.log"
[[ $(qemu-img info --output=json "$DISK" | python3 -c 'import json, sys; print(json.load(sys.stdin)["actual-size"])') -gt 500000000 ]] ||
    die "the disk is nearly empty; see install-serial.log"

[[ $firmware == uefi ]] && echo "--uefi" >vm-run-options
qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
