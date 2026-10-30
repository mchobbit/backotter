#!/usr/bin/env bash
# Build an Arch Linux (or CachyOS) test guest for BackOtter: an unattended
# install from an official Arch ISO into a qcow2 disk image, under QEMU with KVM.
#
# Usage: ./make-arch.sh [--cachyos] [--root-fs ext4|jfs] RELEASE WORK_DIR
#   e.g. ./make-arch.sh 2026.10.01 vms/arch
#        ./make-arch.sh --cachyos --root-fs jfs 2026.10.01 vms/cachyos
#
# RELEASE is an Arch ISO release date from https://archive.archlinux.org/iso/.
# archlinux-RELEASE-x86_64.iso with its .sig and sha256sums.txt is downloaded
# into WORK_DIR unless already there, and checked against the release key's
# signature before use.  For plain Arch, packages come from the Arch Linux
# Archive's snapshot of the same day, so the install is reproducible and the
# guest stays at that day (its mirrorlist points at the snapshot).
#
# --cachyos makes it CachyOS: Arch plus CachyOS's x86-64-v3 repositories, key,
# kernel (linux-cachyos) and settings, added the way CachyOS's cachyos-repo.sh
# adds them to Arch.  CachyOS keeps no archive, so its packages and Arch's are
# whatever is current, and the guest is frozen only by its qcow2 snapshot.  It
# needs an x86-64-v3 CPU.
#
# Results, in WORK_DIR (NAME is arch or cachyos):
#   NAME.qcow2            the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" of the finished install
#   root-password         root's password (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the live system's serial console during the install
#
# The guest, host name NAME: BIOS + MBR, /boot (1 GiB ext4), swap (2 GiB),
# / (ext4 unless --root-fs says otherwise, the rest), 2 GB RAM, no desktop;
# arch/install.sh does the install itself.  Run it with vm-run, and look
# inside it while it's off with vm-mount.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, xorriso, gpg, gpgv, openssl,
# python3, wget, and network access (the archive, mirrors).  Runs as an
# ordinary user in the kvm group.

set -euo pipefail

DISK_SIZE=16G
MEMORY=2048
# The Arch release signing key (Pierre Schmitz), as linked from
# https://archlinux.org/download/
RELEASE_KEY=3E80CA1A8B89F69CBA57D98A76A5EF9054449A5C
INSTALL_TIMEOUT=1800

here=$(dirname "$(realpath "$0")")
die() { echo "make-arch: $*" >&2; exit 1; }

flavor=arch root_fs=ext4
while [[ $# -gt 0 ]]; do
    case $1 in
        --cachyos) flavor=cachyos; shift ;;
        --root-fs) root_fs=$2; shift 2 ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
[[ $root_fs == ext4 || $root_fs == jfs ]] || die "--root-fs must be ext4 or jfs"
[[ $# -eq 2 ]] || die "usage: $0 [--cachyos] [--root-fs ext4|jfs] RELEASE WORK_DIR"
GUEST_NAME=$flavor
DISK=$flavor.qcow2
release=$1
[[ $release =~ ^[0-9]{4}\.[0-9]{2}\.[0-9]{2}$ ]] || die "RELEASE looks like 2026.10.01, not '$release'"
ISO=archlinux-$release-x86_64.iso
mkdir -p "$2"
cd "$2"
[[ -e $DISK ]] && die "$2/$DISK already exists; move it away to rebuild"

# --- Fetch and verify the ISO -------------------------------------------------------
url=https://archive.archlinux.org/iso/$release
for f in sha256sums.txt "$ISO.sig" "$ISO"; do
    [[ -f $f && ! -f $f.part ]] && continue
    echo "Downloading $url/$f"
    touch "$f.part"
    wget -c -q -O "$f" "$url/$f" || die "could not download $url/$f"
    rm "$f.part"
done
if [[ ! -s arch-release-key.gpg ]]; then
    # Fetched by full fingerprint, so the keyserver can't substitute a key.
    gnupg=$(mktemp -d)
    GNUPGHOME=$gnupg gpg -q --keyserver hkps://keyserver.ubuntu.com --recv-keys "$RELEASE_KEY"
    GNUPGHOME=$gnupg gpg -q --export "$RELEASE_KEY" >arch-release-key.gpg
    rm -rf "$gnupg"
fi
gpgv --keyring ./arch-release-key.gpg "$ISO.sig" "$ISO" 2>/dev/null ||
    die "$ISO is not signed by the Arch release key"
grep " $ISO\$" sha256sums.txt | sha256sum -c --quiet - || die "$ISO does not match sha256sums.txt"

# --- The live system's kernel, initramfs and boot options ---------------------------
work=$(mktemp -d)
server=""
# Files and directories extracted from the ISO are read-only.
cleanup() { [[ -n $server ]] && kill "$server" 2>/dev/null; chmod -R u+w "$work"; rm -rf "$work"; }
trap cleanup EXIT
xorriso -osirrox on -indev "$ISO" -extract /loader/entries "$work/entries" 2>/dev/null ||
    die "no /loader/entries on $ISO"
# The first entry is the ISO's default boot option.
entry=$(find "$work/entries" -name '*.conf' | sort | head -n 1)
kernel=$(awk '$1 == "linux" { print $2 }' "$entry")
initrd=$(awk '$1 == "initrd" && $2 ~ /initramfs/ { print $2 }' "$entry")
options=$(sed -n 's/^options[[:space:]]*//p' "$entry")
[[ -n $kernel && -n $initrd && $options == *archisobasedir* ]] ||
    die "unexpected boot entry $(basename "$entry")"
xorriso -osirrox on -indev "$ISO" -extract "$kernel" "$work/vmlinuz" \
    -extract "$initrd" "$work/initramfs.img" 2>/dev/null
chmod u+w "$work"/vmlinuz "$work"/initramfs.img

umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$GUEST_NAME test guest" -f id_ed25519
umask 022

mkdir "$work/www"
sed -e "s|@ARCHIVE_DATE@|${release//.//}|" -e "s|@HOSTNAME@|$GUEST_NAME|" \
    -e "s|@FLAVOR@|$flavor|" -e "s|@ROOT_FS@|$root_fs|" \
    -e "s|@ROOT_PASSWORD_HASH@|$hash|" -e "s|@AUTHORIZED_KEY@|$(cat id_ed25519.pub)|" \
    "$here/arch/install.sh" >"$work/www/install.sh"
if grep -n '@[A-Z_]\+@' "$work/www/install.sh"; then die "install.sh placeholders left unfilled"; fi

# Serve install.sh to the guest only: QEMU's user-mode network maps
# 10.0.2.2 in the guest to the host's 127.0.0.1.
port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
python3 -m http.server --bind 127.0.0.1 --directory "$work/www" "$port" >"$work/http.log" 2>&1 &
server=$!

# --- Install --------------------------------------------------------------------------
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing; the live system's console goes to $2/install-serial.log"
timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 2 -m "$MEMORY" -machine pc \
    -drive "file=$DISK,if=virtio,format=qcow2" \
    -drive "file=$ISO,media=cdrom,readonly=on" \
    -nic user,model=virtio-net-pci \
    -kernel "$work/vmlinuz" -initrd "$work/initramfs.img" \
    -append "$options console=tty0 console=ttyS0,115200n8 script=http://10.0.2.2:$port/install.sh" \
    -display none -serial file:install-serial.log -monitor none -no-reboot ||
    die "the install failed or timed out; see install-serial.log"
grep -q BACKOTTER-INSTALL-OK install-serial.log || {
    grep -a BACKOTTER-INSTALL-FAILED install-serial.log >&2 || true
    die "the install did not finish; see install-serial.log"
}

qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
