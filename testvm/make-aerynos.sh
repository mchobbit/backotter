#!/usr/bin/env bash
# Build an AerynOS test guest for BackOtter: an unattended install from the
# official AerynOS live ISO into a qcow2 disk image, under QEMU with KVM.
# AerynOS's installer (lichen) is interactive and lays out only whole disks
# of 30 GB or more, so aerynos/install.sh does what it does, by hand.
#
# Usage: ./make-aerynos.sh [--root-fs ext4|xfs|f2fs|btrfs] VERSION TAG WORK_DIR
#   e.g. ./make-aerynos.sh 2026.11 2026-11-02-unstable-stream vms/aerynos
#
# VERSION is an AerynOS release (an ISO in https://cdn.aerynos.dev/isos/).
# Its GNOME live ISO (the only one there is) and its .sha256sum are
# downloaded into WORK_DIR unless already there, and the ISO is checked
# against the sum.  AerynOS signs neither: the sum is only as good as the
# HTTPS connection to its CDN.  What the install fetches is checked by moss
# (each package's hash, from the repository index).
#
# TAG is one of the repository's tags (the "tags" of
# https://cdn.aerynos.dev/main/moss-root-index.json), the unstable stream as
# of one day: the release's own is the one of the day its ISO was built
# (2026.11: 2026-11-02-unstable-stream).  Packages come from that tag, so the
# install is reproducible and the guest stays at that day until vm-update
# moves it to the live stream.
#
# Results, in WORK_DIR:
#   aerynos.qcow2         the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" of the finished install
#   root-password         root's password (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the live system's serial console during the install
#   vm-run-options        "--uefi --no-secure-boot", how vm-run must boot it
#
# The guest, host name aerynos: UEFI only (the ISO refuses BIOS, and
# AerynOS's boot manager, blsforme, writes systemd-boot entries, unsigned),
# GPT with an EFI system partition (1 GiB FAT32, at /efi, holding the
# bootloader and kernels: the one boot partition), swap (2 GiB), / (f2fs
# unless --root-fs says otherwise; btrfs gets the installer's @ and @home
# subvolumes), 2 GB RAM, no desktop (lichen-installer's "server" selection:
# NetworkManager, sshd; plus what it lacks: ssh-keygen, sed, grep, awk, ps,
# ip...), console on the serial port too.  Run it with
# vm-run $(cat WORK_DIR/vm-run-options), and look inside it while it's off
# with vm-mount.
#
# Needs: qemu-system-x86_64 with KVM, OVMF, qemu-img, xorriso, mtools,
# openssl, python3, wget, and network access (the CDN).  Runs as an ordinary
# user in the kvm group.

set -euo pipefail

DISK_SIZE=16G
INSTALL_MEMORY=4096                    # the live system keeps its writes in RAM
GUEST_NAME=aerynos
DISK=$GUEST_NAME.qcow2
OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd
INSTALL_TIMEOUT=3600

here=$(dirname "$(realpath "$0")")
die() { echo "make-aerynos: $*" >&2; exit 1; }

root_fs=f2fs                           # a random draw among the four, 2026-11-04
while [[ $# -gt 0 ]]; do
    case $1 in
        --root-fs) root_fs=$2; shift 2 ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
# What lichen-installer offers but bcachefs: its out-of-tree module tracks
# the guest's kernel, and the test hosts' can't mount what it makes.
case $root_fs in ext4|xfs|f2fs|btrfs) ;; *) die "--root-fs must be ext4, xfs, f2fs or btrfs" ;; esac
[[ $# -eq 3 ]] || die "usage: $0 [--root-fs ext4|xfs|f2fs|btrfs] VERSION TAG WORK_DIR"
version=$1 tag=$2
[[ $version =~ ^[0-9]{4}\.[0-9]{2}$ ]] || die "VERSION looks like 2026.11, not '$version'"
[[ $tag =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}-unstable-stream$ ]] ||
    die "TAG looks like 2026-11-02-unstable-stream, not '$tag'"
mkdir -p "$3"
cd "$3"
[[ -e $DISK ]] && die "$3/$DISK already exists; move it away to rebuild"

# --- Fetch and verify the ISO -------------------------------------------------------
url=https://cdn.aerynos.dev/isos
# The name's case has changed between releases (AerynOS-2026.08, aerynOS-2026.11).
ISO=$(compgen -G "[aA]erynOS-$version-GNOME-live-x86_64.iso" | head -n 1 || true)
if [[ -z $ISO || -f $ISO.part ]]; then
    ISO=$(wget -q -O - "$url/" | grep -oE "[aA]erynOS-$version-GNOME-live-x86_64\.iso\"" | head -n 1 | tr -d '"') ||
        true
    [[ -n $ISO ]] || die "no AerynOS $version GNOME live ISO in $url/"
fi
tag_ok=$(wget -q -O - https://cdn.aerynos.dev/main/moss-root-index.json |
    python3 -c 'import json, sys; print(sys.argv[1] in json.load(sys.stdin)["tags"])' "$tag") ||
    die "could not read the repository's tags"
[[ $tag_ok == True ]] || die "the repository has no tag $tag"
for f in "$ISO.sha256sum" "$ISO"; do
    [[ -f $f && ! -f $f.part ]] && continue
    echo "Downloading $url/$f"
    touch "$f.part"
    wget -c -q -O "$f" "$url/$f" || die "could not download $url/$f"
    rm "$f.part"
done
sha256sum -c --quiet "$ISO.sha256sum" || die "$ISO does not match $ISO.sha256sum"

# --- The live system's kernel, initramfs and boot options ---------------------------
work=$(mktemp -d)
server=""
# Files and directories extracted from the ISO are read-only.
cleanup() { [[ -n $server ]] && kill "$server" 2>/dev/null; chmod -R u+w "$work"; rm -rf "$work"; }
trap cleanup EXIT
# They are in the ISO's EFI boot image (FAT), beside its systemd-boot.
xorriso -osirrox on -indev "$ISO" -extract /EFI/Boot/efiboot.img "$work/efiboot.img" 2>/dev/null ||
    die "no /EFI/Boot/efiboot.img on $ISO"
chmod u+w "$work/efiboot.img"
entry=$(mdir -b -i "$work/efiboot.img" ::/loader/entries 2>/dev/null | grep -i '\.conf$' | sort | head -n 1)
[[ -n $entry ]] || die "no boot entry in $ISO's EFI boot image"
mtype -i "$work/efiboot.img" "$entry" | tr -d '\r' >"$work/entry.conf"
kernel=$(awk '$1 == "linux" { print $2 }' "$work/entry.conf")
initrd=$(awk '$1 == "initrd" { print $2 }' "$work/entry.conf")
# Without the boot splash: the console is the serial port.
options=$(awk '$1 == "options" { for (i = 2; i <= NF; i++)
    if ($i != "quiet" && $i != "splash" && $i !~ /^plymouth\./) printf "%s ", $i }' "$work/entry.conf")
[[ -n $kernel && -n $initrd && $options == *root=live:* ]] || die "unexpected boot entry $entry"
mcopy -n -i "$work/efiboot.img" "::$kernel" "$work/vmlinuz"
mcopy -n -i "$work/efiboot.img" "::$initrd" "$work/initrd.img"
rm "$work/efiboot.img"

umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$GUEST_NAME test guest" -f id_ed25519
umask 022

mkdir "$work/www"
sed -e "s|@HOSTNAME@|$GUEST_NAME|" -e "s|@ROOT_FS@|$root_fs|" -e "s|@REPO_VERSION@|tag/$tag|" \
    -e "s|@ROOT_PASSWORD_HASH@|$hash|" -e "s|@AUTHORIZED_KEY@|$(cat id_ed25519.pub)|" \
    "$here/aerynos/install.sh" >"$work/www/install.sh"
if grep -n '@[A-Z_]\+@' "$work/www/install.sh"; then die "install.sh placeholders left unfilled"; fi

# Serve install.sh to the guest only: QEMU's user-mode network maps
# 10.0.2.2 in the guest to the host's 127.0.0.1.
port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
python3 -m http.server --bind 127.0.0.1 --directory "$work/www" "$port" >"$work/http.log" 2>&1 &
server=$!

# --- Install --------------------------------------------------------------------------
# systemd.run= makes the live system boot into a service that runs it (and
# powers off after), instead of its desktop; that service starts the rest
# of the system (the network) itself, all but the serial console's getty,
# which would take ttyS0 from install.sh's output.  Quoted for the kernel
# command line, then for systemd's ExecStart=: no $ in it.
run="/usr/bin/bash -c 'systemctl mask --runtime serial-getty@ttyS0.service;"
run+=" systemctl start --no-block multi-user.target;"
run+=" until wget -q -O /run/bko-install.sh http://10.0.2.2:$port/install.sh; do sleep 2; done;"
run+=" exec bash /run/bko-install.sh'"
cp "$OVMF_VARS" "$work/vars.fd"
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing; the live system's console goes to $3/install-serial.log"
# discard=unmap: what the install deletes (moss's downloads) leaves no
# clusters behind in the image, once install.sh's fstrim hands it back.
timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 2 -m "$INSTALL_MEMORY" -machine q35 \
    -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE" \
    -drive "if=pflash,format=raw,unit=1,file=$work/vars.fd" \
    -drive "file=$DISK,if=virtio,format=qcow2,discard=unmap" \
    -drive "file=$ISO,media=cdrom,readonly=on" \
    -nic user,ipv6=off,model=virtio-net-pci \
    -kernel "$work/vmlinuz" -initrd "$work/initrd.img" \
    -append "$options console=tty0 console=ttyS0,115200n8 systemd.run=\"$run\"" \
    -display none -serial file:install-serial.log -monitor none -no-reboot ||
    die "the install failed or timed out; see install-serial.log"
grep -q BACKOTTER-INSTALL-OK install-serial.log || {
    grep -a BACKOTTER-INSTALL-FAILED install-serial.log >&2 || true
    die "the install did not finish; see install-serial.log"
}

echo "--uefi --no-secure-boot" >vm-run-options
qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $3/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
