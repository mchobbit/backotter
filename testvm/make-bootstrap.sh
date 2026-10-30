#!/usr/bin/env bash
# Build a test guest for BackOtter of a distribution that installs by
# bootstrapping (unpacking a root file system or running its package manager
# into the new disk) rather than from an installer of its own: an unattended
# install, run from an official Arch ISO's live system, into a qcow2 disk
# image, under QEMU with KVM.
#
# Usage: ./make-bootstrap.sh DISTRO WORK_DIR
#   e.g. ./make-bootstrap.sh alpine vms/alpine324
#
#   DISTRO        guest name    /          installed from
#   alpine        alpine324     XFS        Alpine 3.24's minirootfs (musl, OpenRC)
#   void          void-musl     btrfs      Void's musl ROOTFS tarball (runit)
#   artix-dinit   artix-dinit   JFS        Artix's repositories (dinit)
#   artix-s6      artix-s6      ext4       Artix's repositories (s6)
#   nixos         nixos2605     bcachefs   NixOS 26.05's nixpkgs
#
# The versions are fixed below.  The Arch ISO (with its .sig and
# sha256sums.txt) and the distribution's root file system tarball, if it has
# one, are downloaded into WORK_DIR unless already there, and checked
# against their release keys before use; what is fetched later, inside the
# VM, is checked by the package manager doing it (apk, xbps, pacman, Nix).
# bcachefs needs an Arch ISO whose kernel still has it (6.12), so NixOS's
# install uses an older one.
#
# Results, in WORK_DIR:
#   NAME.qcow2            the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" of the finished install
#   root-password         root's password (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the live system's serial console during the install
#
# The guest: BIOS + MBR, /boot (1 GiB ext4), swap (2 GiB), / (the rest), 2 GB
# RAM, no desktop, console on the serial port too, DHCP and sshd.
# bootstrap/common.sh partitions the disk; bootstrap/DISTRO.sh installs.
# Run it with vm-run, and look inside it while it's off with vm-mount.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, xorriso, gpg, gpgv, minisign,
# openssl, python3, wget, and network access.  Runs as an ordinary user in
# the kvm group.

set -euo pipefail

DISK_SIZE=16G
INSTALL_MEMORY=4096                    # the live system holds downloads in RAM
INSTALL_TIMEOUT=3600
# The Arch release signing key (Pierre Schmitz), as linked from
# https://archlinux.org/download/
ARCH_KEY=3E80CA1A8B89F69CBA57D98A76A5EF9054449A5C
ARCH_ISO_CURRENT=2026.10.01
ARCH_ISO_LINUX_6_12=2024.12.01         # linux 6.12.1: still has bcachefs

ALPINE_VERSION=3.24.3
# Natanael Copa, who signs Alpine's releases (https://alpinelinux.org/downloads/)
ALPINE_KEY=0482D84022F52DF1C4E7CD43293ACD0907D9495A
VOID_DATE=20250202
# void-release-20250202.pub, from void-linux/void-packages's void-release-keys
VOID_KEY=RWT5+SoQD+dWTeOdNuc4Q/jq2+3+jpql7+JJp4WukkxTdpsZlk2EGuPj
NIXOS_RELEASE=26.05
NIXPKGS=https://releases.nixos.org/nixos/26.05/nixos-26.05.13164.6f06d204d8f2/nixexprs.tar.xz
NIX_VERSION=2.31.2

here=$(dirname "$(realpath "$0")")
die() { echo "make-bootstrap: $*" >&2; exit 1; }

[[ $# -eq 2 ]] || die "usage: $0 alpine|void|artix-dinit|artix-s6|nixos WORK_DIR"
distro=$1
iso_release=$ARCH_ISO_CURRENT
script=$distro
extra=()                               # more @NAME@=value settings for the script
case $distro in
    alpine) name=alpine324 root_fs=xfs
        extra=("ALPINE_BRANCH=v${ALPINE_VERSION%.*}") ;;
    void) name=void-musl root_fs=btrfs ;;
    artix-dinit) name=artix-dinit root_fs=jfs script=artix extra=(INIT=dinit) ;;
    artix-s6) name=artix-s6 root_fs=ext4 script=artix extra=(INIT=s6) ;;
    nixos) name=nixos2605 root_fs=bcachefs iso_release=$ARCH_ISO_LINUX_6_12
        INSTALL_MEMORY=8192
        extra=("NIXPKGS=$NIXPKGS" "NIX_VERSION=$NIX_VERSION" "STATE_VERSION=$NIXOS_RELEASE") ;;
    *) die "unknown DISTRO $distro" ;;
esac
DISK=$name.qcow2
mkdir -p "$2"
cd "$2"
[[ -e $DISK ]] && die "$2/$DISK already exists; move it away to rebuild"

# fetch URL [FILE]: download into WORK_DIR, unless it's already there.
fetch() {
    local f=${2:-${1##*/}}
    [[ -f $f && ! -f $f.part ]] && return
    echo "Downloading $1"
    touch "$f.part"
    wget -c -q -O "$f" "$1" || die "could not download $1"
    rm "$f.part"
}
# gpg_key FINGERPRINT FILE: the key, fetched by full fingerprint so the
# keyserver can't substitute another.
gpg_key() {
    [[ -s $2 ]] && return
    local gnupg
    gnupg=$(mktemp -d)
    GNUPGHOME=$gnupg gpg -q --keyserver hkps://keyserver.ubuntu.com --recv-keys "$1"
    GNUPGHOME=$gnupg gpg -q --export "$1" >"$2"
    rm -rf "$gnupg"
}

# --- Fetch and verify the Arch ISO, and the root file system ------------------------
ISO=archlinux-$iso_release-x86_64.iso
url=https://archive.archlinux.org/iso/$iso_release
fetch "$url/sha256sums.txt" "sha256sums-$iso_release.txt"
fetch "$url/$ISO.sig"
fetch "$url/$ISO"
gpg_key "$ARCH_KEY" arch-release-key.gpg
gpgv --keyring ./arch-release-key.gpg "$ISO.sig" "$ISO" 2>/dev/null ||
    die "$ISO is not signed by the Arch release key"
grep " $ISO\$" "sha256sums-$iso_release.txt" | sha256sum -c --quiet - ||
    die "$ISO does not match sha256sums.txt"

work=$(mktemp -d)
server=""
# Files and directories extracted from the ISO are read-only.
cleanup() { [[ -n $server ]] && kill "$server" 2>/dev/null; chmod -R u+w "$work"; rm -rf "$work"; }
trap cleanup EXIT
mkdir "$work/www"
case $distro in
    alpine)
        f=alpine-minirootfs-$ALPINE_VERSION-x86_64.tar.gz
        url=https://dl-cdn.alpinelinux.org/alpine/v${ALPINE_VERSION%.*}/releases/x86_64
        fetch "$url/$f.asc"
        fetch "$url/$f"
        gpg_key "$ALPINE_KEY" alpine-release-key.gpg
        gpgv --keyring ./alpine-release-key.gpg "$f.asc" "$f" 2>/dev/null ||
            die "$f is not signed by Alpine's release key"
        ln -s "$PWD/$f" "$work/www/rootfs.tar.gz"
        ;;
    void)
        f=void-x86_64-musl-ROOTFS-$VOID_DATE.tar.xz
        url=https://repo-default.voidlinux.org/live/$VOID_DATE
        fetch "$url/sha256sum.txt" "void-sha256sum-$VOID_DATE.txt"
        fetch "$url/sha256sum.sig" "void-sha256sum-$VOID_DATE.sig"
        fetch "$url/$f"
        minisign -q -V -P "$VOID_KEY" -m "void-sha256sum-$VOID_DATE.txt" \
            -x "void-sha256sum-$VOID_DATE.sig" || die "Void's sha256sum.txt is not signed by its release key"
        grep -F "$f" "void-sha256sum-$VOID_DATE.txt" | sha256sum -c --quiet - || die "$f does not match Void's sha256sum.txt"
        ln -s "$PWD/$f" "$work/www/rootfs.tar.xz"
        ;;
esac

# --- The live system's kernel, initramfs and boot options ---------------------------
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
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$name test guest" -f id_ed25519
umask 022

# Serve the install script and files to the guest only: QEMU's user-mode
# network maps 10.0.2.2 in the guest to the host's 127.0.0.1.
port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
settings=(DISTRO="$distro" ROOT_FS="$root_fs" FIRMWARE=bios HOSTNAME="$name"
    ROOT_PASSWORD="$password" ROOT_PASSWORD_HASH="$hash" AUTHORIZED_KEY="$(cat id_ed25519.pub)"
    SERVER="http://10.0.2.2:$port" "${extra[@]}")
python3 - "$work/www/install.sh" "$here/bootstrap/common.sh" "$here/bootstrap/$script.sh" \
    "${settings[@]}" <<'EOF'
import re, sys
out, parts, settings = sys.argv[1], sys.argv[2:4], dict(a.split("=", 1) for a in sys.argv[4:])
text = "".join(open(p).read() for p in parts)
text = re.sub(r"@([A-Z_]+)@", lambda m: settings[m.group(1)], text)
open(out, "w").write(text)
EOF
python3 -m http.server --bind 127.0.0.1 --directory "$work/www" "$port" >"$work/http.log" 2>&1 &
server=$!

# --- Install --------------------------------------------------------------------------
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing $distro; the live system's console goes to $2/install-serial.log"
# cow_spacesize: the live system's writable layer, where downloads land.
timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 4 -m "$INSTALL_MEMORY" -machine pc \
    -drive "file=$DISK,if=virtio,format=qcow2" \
    -drive "file=$ISO,media=cdrom,readonly=on" \
    -nic user,model=virtio-net-pci \
    -kernel "$work/vmlinuz" -initrd "$work/initramfs.img" \
    -append "$options cow_spacesize=$((INSTALL_MEMORY / 2))M console=tty0 console=ttyS0,115200n8 script=http://10.0.2.2:$port/install.sh" \
    -display none -serial file:install-serial.log -monitor none -no-reboot ||
    die "the install failed or timed out; see install-serial.log"
grep -q BACKOTTER-INSTALL-OK install-serial.log || {
    grep -a BACKOTTER-INSTALL-FAILED install-serial.log >&2 || true
    die "the install did not finish; see install-serial.log"
}

qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
