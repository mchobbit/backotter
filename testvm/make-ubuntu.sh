#!/usr/bin/env bash
# Build an Ubuntu Server test guest for BackOtter, with / on ZFS, or for UEFI
# laid out as Ubuntu's installer does by default: an unattended, offline
# "autoinstall" from an official live-server ISO into a qcow2 disk image,
# under QEMU with KVM.  (Linux Mint 22 is built on Ubuntu 24.04, so a 24.04
# guest covers what a Mint restore depends on, minus the desktop.)
#
# Usage: ./make-ubuntu.sh [--uefi | --zfs-mirror] [--name NAME] VERSION WORK_DIR
#   e.g. ./make-ubuntu.sh 24.04 vms/ubuntu24
#        ./make-ubuntu.sh --zfs-mirror --name ubuntu26-zfsmirror 26.04.1 vms/ubuntu26-zfsmirror
#        ./make-ubuntu.sh --uefi --name ubuntu26-uefi 26.04.1 vms/ubuntu26-uefi
#
# VERSION is an Ubuntu release or point release (24.04, 24.04.1, ...).
# ubuntu-VERSION-live-server-amd64.iso with its SHA256SUMS and SHA256SUMS.gpg
# is downloaded into WORK_DIR unless already there (from old-releases.ubuntu.com,
# or releases.ubuntu.com for current ones), and checked against Ubuntu's
# signature before use.  Results, in WORK_DIR (NAME is ubuntuN by default,
# N the major version):
#   NAME.qcow2            the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" of the finished install
#   root-password         the password of root and of the user "tester" (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root and as tester
#   install-serial.log    the installer's serial console
#   vm-run-options        with --uefi: "--uefi", how vm-run must boot it;
#                         with --zfs-mirror: "--disk NAME-b.qcow2"
#   NAME-b.qcow2          with --zfs-mirror: the second disk
#   zfs-passphrase        with --zfs-mirror: the pool's passphrase (mode 600)
#
# The guest, host name NAME: BIOS + MBR, /boot (1 GiB ext4), swap (2 GiB),
# and / on ZFS (pool rpool, dataset rpool/ROOT/ubuntu), 2 GB RAM, no desktop,
# apt pointed at snapshot.ubuntu.com as of the ISO's build date; see
# ubuntu/user-data and ubuntu/storage-zfs.yaml.
#
# --uefi installs for UEFI instead, with the layout Ubuntu's installer picks
# by default (Subiquity's guided "direct" layout, ubuntu/storage-direct.yaml):
# GPT, an EFI system partition, ext4 for /, and a swap file.  It boots through
# shim and GRUB with Secure Boot on: vm-run --uefi.  The installer itself runs
# with Secure Boot off, as QEMU's -kernel boot has no shim to vouch for the
# kernel; the guest's NVRAM boot entries aren't kept, so each boot starts from
# EFI/BOOT/BOOTX64.EFI, as on a new machine.
#
# --zfs-mirror makes it two 16 GiB disks instead, with /boot on an mdadm
# RAID 1 across both, swap on both, and rpool a two-way mirror, encrypted as
# Subiquity's "ZFS with encryption" does it: a LUKS keystore volume in the
# pool, whose passphrase (zfs-passphrase) the guest asks for on its console
# at every boot (ubuntu/storage-zfs-mirror.yaml).
#
# Run it with vm-run (and vm-run-options), and look inside it while it's off
# with vm-mount.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, xorriso, gpg, gpgv, openssl,
# python3 with yaml, wget.  Runs as an ordinary user in the kvm group.

set -euo pipefail

DISK_SIZE=16G
MEMORY=2048
# "Ubuntu CD Image Automatic Signing Key (2012) <cdimage@ubuntu.com>"
CD_KEY=843938DF228D22F7B3742BC0D94AA3F0EFE21092
INSTALL_TIMEOUT=2400

here=$(dirname "$(realpath "$0")")
die() { echo "make-ubuntu: $*" >&2; exit 1; }

firmware=bios name="" mirror=no
while [[ $# -gt 0 ]]; do
    case $1 in
        --uefi) firmware=uefi; shift ;;
        --zfs-mirror) mirror=yes; shift ;;
        --name) name=$2; shift 2 ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
[[ $# -eq 2 ]] || die "usage: $0 [--uefi | --zfs-mirror] [--name NAME] VERSION WORK_DIR"
[[ $firmware == uefi && $mirror == yes ]] && die "--zfs-mirror is for BIOS only"
version=$1
[[ $version =~ ^[0-9]{2}\.[0-9]{2}(\.[0-9]+)?$ ]] || die "VERSION looks like 24.04 or 24.04.1, not '$version'"
release=$(cut -d. -f1-2 <<<"$version")
major=${version%%.*}
ISO=ubuntu-$version-live-server-amd64.iso
guest_name=${name:-ubuntu$major}
DISK=$guest_name.qcow2
storage=zfs
[[ $firmware == uefi ]] && storage=direct
[[ $mirror == yes ]] && storage=zfs-mirror
DISK_B=$guest_name-b.qcow2
mkdir -p "$2"
cd "$2"
[[ -e $DISK ]] && die "$2/$DISK already exists; move it away to rebuild"

# --- Fetch and verify the ISO -------------------------------------------------------
# Superseded point releases move to old-releases.ubuntu.com.
url=""
for base in "https://releases.ubuntu.com/$release" "https://old-releases.ubuntu.com/releases/$release"; do
    if wget -q -O - "$base/SHA256SUMS" 2>/dev/null | grep -q " \*$ISO\$"; then url=$base; break; fi
done
[[ -n $url ]] || die "can't find $ISO on releases.ubuntu.com or old-releases.ubuntu.com"
for f in SHA256SUMS SHA256SUMS.gpg "$ISO"; do
    [[ -f $f && ! -f $f.part ]] && continue
    echo "Downloading $url/$f"
    touch "$f.part"
    wget -c -q -O "$f" "$url/$f" || die "could not download $url/$f"
    rm "$f.part"
done
if [[ ! -s ubuntu-cd-key.gpg ]]; then
    # Fetched by full fingerprint, so the keyserver can't substitute a key.
    gnupg=$(mktemp -d)
    GNUPGHOME=$gnupg gpg -q --keyserver hkps://keyserver.ubuntu.com --recv-keys "$CD_KEY"
    GNUPGHOME=$gnupg gpg -q --export "$CD_KEY" >ubuntu-cd-key.gpg
    rm -rf "$gnupg"
fi
gpgv --keyring ./ubuntu-cd-key.gpg SHA256SUMS.gpg SHA256SUMS 2>/dev/null ||
    die "SHA256SUMS is not signed by the Ubuntu CD key"
grep " \*$ISO\$" SHA256SUMS | sha256sum -c --quiet - || die "$ISO does not match SHA256SUMS"

# --- Installer kernel and initrd, and the answers on a CIDATA disc ---------------
work=$(mktemp -d)
# Files and directories extracted from the ISO are read-only.
trap 'chmod -R u+w "$work"; rm -rf "$work"' EXIT
xorriso -osirrox on -indev "$ISO" \
    -extract /casper/vmlinuz "$work/vmlinuz" \
    -extract /casper/initrd "$work/initrd" \
    -extract /.disk/info "$work/info" 2>/dev/null ||
    die "$ISO lacks /casper/vmlinuz, /casper/initrd or /.disk/info"
# The ISO's build date, e.g. "... Release amd64 (20240423)", picks the snapshot.
build_date=$(grep -oE '\([0-9]{8}(\.[0-9]+)?\)' "$work/info" | tr -d '()' | cut -d. -f1)
[[ -n $build_date ]] || die "no build date in the ISO's .disk/info"
snapshot=${build_date}T235959Z

umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
# Letters and digits only, so it needs no quoting anywhere.
[[ $mirror == yes ]] && openssl rand -base64 24 | tr -dc 'A-Za-z0-9' >zfs-passphrase
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$guest_name test guest" -f id_ed25519
umask 022

mkdir "$work/cidata"
late=$here/ubuntu/storage-$storage-late.yaml
[[ -f $late ]] || late=/dev/null
passphrase=$(cat zfs-passphrase 2>/dev/null || true)
sed -e "s|@HOSTNAME@|$guest_name|" -e "s|@PASSWORD_HASH@|$hash|g" \
    -e "s|@AUTHORIZED_KEY@|$(cat id_ed25519.pub)|g" -e "s|@SNAPSHOT@|$snapshot|g" \
    -e "/^@STORAGE@\$/{r $here/ubuntu/storage-$storage.yaml" -e "d}" \
    -e "/^@STORAGE_LATE@\$/{r $late" -e "d}" \
    "$here/ubuntu/user-data" >"$work/cidata/user-data"
# The storage files' own settings: sed's r inserts text after its edits.
sed -i "s|@ZFS_PASSPHRASE@|$passphrase|g" "$work/cidata/user-data"
if grep -n '@[A-Z_]\+@' "$work/cidata/user-data"; then die "user-data placeholders left unfilled"; fi
# cloud-init quietly ignores user-data it can't parse, and the installer then
# waits for answers until the timeout; catch that here.
python3 -c '
import sys, yaml
config = yaml.safe_load(open(sys.argv[1]))
assert isinstance(config, dict) and "autoinstall" in config, "no autoinstall: section"
' "$work/cidata/user-data" || die "ubuntu/user-data is not valid (needs python3-yaml to check)"
: >"$work/cidata/meta-data"
xorriso -as mkisofs -quiet -o "$work/cidata.iso" -V CIDATA -J -r "$work/cidata" 2>"$work/xorriso.log" ||
    { cat "$work/xorriso.log" >&2; die "could not make the CIDATA ISO"; }

# --- Install, with no network: everything comes from the ISO -----------------------
machine=(-machine pc)
if [[ $firmware == uefi ]]; then
    # OVMF without Secure Boot, and a variable store thrown away afterwards.
    cp /usr/share/OVMF/OVMF_VARS_4M.fd "$work/vars.fd"
    machine=(-machine q35
        -drive if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd
        -drive "if=pflash,format=raw,unit=1,file=$work/vars.fd")
fi
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
disks=(-drive "file=$DISK,if=virtio,format=qcow2")
if [[ $mirror == yes ]]; then
    qemu-img create -q -f qcow2 "$DISK_B" "$DISK_SIZE"
    disks+=(-drive "file=$DISK_B,if=virtio,format=qcow2")
fi
echo "Installing; the installer's console goes to $2/install-serial.log"
timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 2 -m "$MEMORY" "${machine[@]}" \
    "${disks[@]}" \
    -drive "file=$ISO,media=cdrom,readonly=on" \
    -drive "file=$work/cidata.iso,media=cdrom,readonly=on" \
    -nic none \
    -kernel "$work/vmlinuz" -initrd "$work/initrd" \
    -append "autoinstall console=tty0 console=ttyS0,115200n8" \
    -display none -serial file:install-serial.log -monitor none -no-reboot ||
    die "the install failed or timed out; see install-serial.log"
# The installer only powers off at the very end (an error waits for an answer
# instead, until the timeout), but check that a system was written.
[[ $(qemu-img info --output=json "$DISK" | python3 -c 'import json, sys; print(json.load(sys.stdin)["actual-size"])') -gt 500000000 ]] ||
    die "the disk is nearly empty; see install-serial.log"

[[ $firmware == uefi ]] && echo "--uefi" >vm-run-options
[[ $mirror == yes ]] && echo "--disk $DISK_B" >vm-run-options
qemu-img snapshot -c fresh-install "$DISK"
[[ $mirror == yes ]] && qemu-img snapshot -c fresh-install "$DISK_B"
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
