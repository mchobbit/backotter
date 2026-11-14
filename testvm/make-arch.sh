#!/usr/bin/env bash
# Build an Arch Linux (or CachyOS) test guest for BackOtter: an unattended
# install from an official Arch ISO into a qcow2 disk image, under QEMU with KVM.
#
# Usage: ./make-arch.sh [--cachyos] [--root-fs ext4|jfs] RELEASE WORK_DIR
#        ./make-arch.sh --live endeavouros|endeavouros-grub|manjaro|garuda|biglinux LIVE_ISO RELEASE WORK_DIR
#   e.g. ./make-arch.sh 2026.10.01 vms/arch
#        ./make-arch.sh --cachyos --root-fs jfs 2026.10.01 vms/cachyos
#        ./make-arch.sh --live manjaro manjaro-xfce-26.1.1-minimal-260825-linux71.iso 2026.10.01 vms/manjaro
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
# --live installs an Arch-based desktop distribution from its own live ISO
# (already verified) the way its Calamares does in an offline install, as
# arch/install-live.sh says: the Arch ISO only runs that script, so that the
# initramfs is built on the guest's own (virtual) hardware.  The guest is UEFI
# (vm-run-options: its loader isn't signed), 24 GiB, with the distribution's
# default partitions; WORK_DIR's other results are as below, NAME being the
# distribution's.  Garuda's guest is 32 GiB, and its ISO is checked here
# against the SHA-256 pinned below, as Garuda signs nothing.  So is BigLinux's
# (biglinux_2026-10-03_k618.iso): BigLinux publishes only an MD5 beside it.
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
# garuda-dr460nized-linux-garuda-260819.iso's SHA-256, as read on 2026-11-14.
# Garuda publishes no signature, only a .sha256 on the same host as the ISO,
# so this pin is what --live garuda checks.
GARUDA_ISO_SHA256=3dac6b04225fe9577ea0143a1c712e13316dff26985c2c4417f8c5e4af77f7f7
# biglinux_2026-10-03_k618.iso's SHA-256, read on 2026-11-14 from two copies
# downloaded apart (Chopin's and Cinqcent's), both matching the .md5 that
# iso.biglinux.com.br serves beside it; BigLinux signs no ISO.
BIGLINUX_ISO_SHA256=1d12186abab0a2cb33ee9f0edd30140cd8ed842aa081e90ad4f0e3d050e6ae90

here=$(dirname "$(realpath "$0")")
die() { echo "make-arch: $*" >&2; exit 1; }

flavor=arch root_fs=ext4 live_iso=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --cachyos) flavor=cachyos; shift ;;
        --root-fs) root_fs=$2; shift 2 ;;
        --live)
            [[ $2 == endeavouros || $2 == endeavouros-grub || $2 == manjaro || $2 == garuda || $2 == biglinux ]] ||
                die "--live takes endeavouros, endeavouros-grub, manjaro, garuda or biglinux"
            flavor=$2 live_iso=$(realpath "$3"); shift 3 ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
[[ $root_fs == ext4 || $root_fs == jfs ]] || die "--root-fs must be ext4 or jfs"
[[ $# -eq 2 ]] || die "usage: $0 [--cachyos] [--root-fs ext4|jfs] [--live DISTRO LIVE_ISO] RELEASE WORK_DIR"
install_script=install.sh live_drive=()
if [[ -n $live_iso ]]; then
    [[ -f $live_iso ]] || die "no such ISO: $live_iso"
    DISK_SIZE=24G MEMORY=4096 install_script=install-live.sh
    # Second CD: /dev/sr1 in the live system.
    live_drive=(-drive "file=$live_iso,media=cdrom,readonly=on")
    if [[ $flavor == garuda ]]; then
        # Garuda's Calamares checks for 29.9 GB of disk; the qcow2 is sparse.
        # 8 GB of layers to copy and two dracut runs (host-only and fallback)
        # take longer than the others' installs.
        DISK_SIZE=32G INSTALL_TIMEOUT=3600
        [[ $(sha256sum <"$live_iso" | cut -d' ' -f1) == "$GARUDA_ISO_SHA256" ]] ||
            die "$live_iso's SHA-256 isn't the pinned $GARUDA_ISO_SHA256"
    elif [[ $flavor == biglinux ]]; then
        # 3.4 GB of squashed layers (KDE in full), written through
        # compress-force=zstd on 2 CPUs.
        INSTALL_TIMEOUT=3600
        [[ $(sha256sum <"$live_iso" | cut -d' ' -f1) == "$BIGLINUX_ISO_SHA256" ]] ||
            die "$live_iso's SHA-256 isn't the pinned $BIGLINUX_ISO_SHA256"
    fi
fi
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
    "$here/arch/$install_script" >"$work/www/install.sh"
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
    -drive "file=$ISO,media=cdrom,readonly=on" "${live_drive[@]}" \
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
[[ -z $live_iso ]] || echo "--uefi --no-secure-boot" >vm-run-options
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
