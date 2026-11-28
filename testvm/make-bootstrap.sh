#!/usr/bin/env bash
# Build a test guest for BackOtter of a distribution that installs by
# bootstrapping (unpacking a root file system or running its package manager
# into the new disk) rather than from an installer of its own: an unattended
# install, run from an official Arch ISO's live system, into a qcow2 disk
# image, under QEMU with KVM.
#
# Usage: ./make-bootstrap.sh [--arch amd64|arm64] DISTRO WORK_DIR
#   e.g. ./make-bootstrap.sh alpine vms/alpine324
#        ./make-bootstrap.sh --arch arm64 alpine vms/alpine324-arm64
#
#   DISTRO        guest name      /          installed from
#   alpine        alpine324       XFS        Alpine 3.24's minirootfs (musl, OpenRC)
#   alpine-uefi   alpine324-uefi  ext4       the same, for UEFI (below)
#   alpine-lvm    alpine324-lvm   XFS        the same, for UEFI, / on LVM
#   alpine-luks   alpine324-luks  ext4       the same, for UEFI, / on LVM on LUKS2
#   void          void-musl       btrfs      Void's musl ROOTFS tarball (runit)
#   chimera       chimera         XFS        Chimera Linux's full ROOTFS tarball (dinit)
#   chimera-uefi  chimera-uefi    ext4       the same, for UEFI, / on LVM on LUKS2 (below)
#   gentoo        gentoo          ext4       Gentoo's OpenRC stage3 (glibc), for UEFI, / on LVM (below)
#   gentoo-systemd gentoo-systemd ext4       Gentoo's systemd stage3, for UEFI, / on LVM on LUKS2 (below)
#   artix-dinit   artix-dinit     JFS        Artix's repositories (dinit)
#   artix-s6      artix-s6        ext4       Artix's repositories (s6)
#   nixos         nixos2605       bcachefs   NixOS 26.05's nixpkgs
#   slackware     slackware15     ext4       Slackware 15.0's series a, ap, d, l, n (BSD-style rc), LILO, no initrd
#   slackware-uefi slackware15-uefi XFS      the same, for UEFI, ELILO, mkinitrd (below)
#   pclinuxos     pclinuxos       ext4       PCLinuxOS's KDE ISO (livecd.sqfs), by pclinuxos/install.sh, for UEFI (below)
#   pclinuxos-bios pclinuxos-bios XFS        the same, BIOS, Manual mode's /boot + swap + XFS / (below)
#
# The versions are fixed below.  The Arch ISO (with its .sig and
# sha256sums.txt) and the distribution's root file system tarball, if it has
# one, are downloaded into WORK_DIR unless already there, and checked
# against their release keys before use; what is fetched later, inside the
# VM, is checked by the package manager doing it (apk, xbps, pacman, Nix).
# Chimera's ROOTFS is fixed by its date, but apk then upgrades it from the
# live repository, so a rebuild gets newer packages.  So is Gentoo's stage3
# (GENTOO_STAGE3, the OpenRC or the systemd one, checked with gpgv against
# the Release Engineering key);
# its tree is the newest snapshot emerge-webrsync finds (checked by
# emerge-webrsync), whose timestamp.chk goes in WORK_DIR/portage-tree.json,
# and its packages are the binhost's.  Gentoo prunes autobuilds after about
# two months: /bkoscratch/rebuild-inputs/gentoo and gentoo-systemd keep the
# stage3s and their .ascs on both hosts, for rebuild-one.sh.
# Slackware's tree (slackware64-15.0: CHECKSUMS.md5 and its .asc, the five
# series' packages and SLACKWARE_PATCHED's patches/packages/ ones, each with
# its .asc) is read from SLACKWARE_TREE if set (rebuild-one.sh:
# /bkoscratch/rebuild-inputs/slackware15), else downloaded into
# WORK_DIR/slackware64-15.0; a package missing from it is downloaded into
# it.  Either way CHECKSUMS.md5 is checked with gpgv against Slackware's key,
# each package against its MD5 there and its own .asc.  Its 15.0 tree is
# frozen; the patches come later, by vm-update, but for SLACKWARE_PATCHED's,
# whose version is the one the tree's CHECKSUMS.md5 lists.
# PCLinuxOS's ISO (PCLINUXOS_ISO) and openssh-server RPM (PCLINUXOS_SSH_RPM)
# are read from WORK_DIR (rebuild-one.sh links them in from
# /bkoscratch/rebuild-inputs/pclinuxos), else downloaded from NLUUG, and
# checked against the SHA-256s pinned below (PCLinuxOS signs neither: the
# ISO's MD5 was checked against NLUUG's and freedif's .md5sum when it was
# pinned, docs/design/pclinuxos.md PC1).
# bcachefs needs an Arch ISO whose kernel still has it (6.12), so NixOS's
# install uses an older one.
#
# Results, in WORK_DIR:
#   NAME.qcow2            the guest's 16 GiB disk (sparse; gentoo's 20 GiB),
#                         with a qcow2 snapshot "fresh-install" of the
#                         finished install
#   root-password         root's password (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the live system's serial console during the install
#   vm-run-options        (UEFI guests) --uefi --no-secure-boot, for vm-run
#   portage-tree.json     (gentoo, gentoo-systemd) the stage3's date and the tree's
#                         metadata/timestamp.chk
#   luks-keyfile          (alpine-luks, chimera-uefi, gentoo-systemd) the
#                         key that unlocks / (mode 600, no newline), as the
#                         guest's /crypto_keyfile.bin (Alpine) or
#                         /etc/cryptsetup-keys.d/cryptroot.key (Chimera,
#                         Gentoo)
#
# slackware: MBR, swap (2 GiB) and / (LAYOUT swap-root), the huge kernel
# with no initrd, LILO in the MBR.  slackware-uefi: LAYOUT uefi, the generic
# kernel with mkinitrd's initrd, ELILO in EFI/Slackware and EFI/BOOT.  Both
# 20 GiB, fstab and root= by device name, as Slackware's setup writes them
# (docs/design/slackware.md).
# pclinuxos, pclinuxos-bios (docs/design/pclinuxos.md): bootstrap/pclinuxos.sh
# alone (no common.sh) runs pclinuxos/install.sh, the replay of PCLinuxOS's
# mylive-install, with the PCLinuxOS ISO on a second CD (/dev/sr1).  32 GiB.
# pclinuxos: the installer's Automated layout on GPT (ESP 300 MB at
# /boot/EFI, swap 1024 MB, / ext4), grub2-efi under the id pclinuxos with
# --no-nvram and again at EFI/BOOT/BOOTX64.EFI; pclinuxos-bios: msdos,
# /boot ext4 1 GiB, swap 1 GiB, / XFS, grub2 i386-pc in the MBR.  Both:
# mkinitrd's (nash) initrd-K.img, runlevel 3 with a ttyS0 getty, sshd from
# the pinned RPM, root and tester sharing root-password.
# The guest: BIOS + MBR, /boot (1 GiB ext4), swap (2 GiB), / (the rest), 2 GB
# RAM, no desktop, console on the serial port too, DHCP and sshd.  The UEFI
# ones, GPT with GRUB in a 512 MiB ESP at /boot/efi (bootstrap/common.sh's
# LAYOUTs): alpine-uefi swap (2 GiB) and / (the rest); alpine-lvm an LVM PV,
# VG vg0 with LVs root (12 GiB) and swap (1 GiB) and free extents left;
# alpine-luks /boot (1 GiB ext4), then LUKS2 holding vg0 with root (10 GiB)
# and swap (1 GiB), opened at boot by the key file in the initramfs.
# chimera-uefi: systemd-boot instead, in a 1 GiB ESP that is /boot itself
# (LAYOUT luks-esp), then the same LUKS2 and vg0 as alpine-luks.
# gentoo: the BackShot1 Thinkpad's layout in small (LAYOUT efi-lvm): a
# 1 GiB ESP at /efi, 2 GiB swap, vg0 with root (10 GiB ext4) and home
# (3 GiB XFS), ~4 GiB free; GRUB under the id gentoo.  gentoo-systemd:
# chimera-uefi's disk (LAYOUT luks-esp), with GRUB in the ESP that is
# /boot, and systemd's dracut opening the LUKS2 with the key file in its
# initramfs.
# bootstrap/common.sh partitions the disk; bootstrap/DISTRO.sh installs.
# Run it with vm-run, and look inside it while it's off with vm-mount.
#
# --arch arm64 (docs/design/arm64.md A5; DISTRO alpine only) builds
# alpine324-arm64 on an aarch64 host (Bigfoot): alpine-uefi's disk (GPT, the
# ESP at /boot/efi, swap, the rest /) with / on XFS as alpine324's, from
# Alpine's aarch64 minirootfs.  Arch has no aarch64 ISO, so the live system
# is Alpine's own alpine-virt aarch64 ISO (checked against Alpine's release
# key), booted through vm-run (QEMU's virt machine, AAVMF without Secure
# Boot) with an overlay (apkovl=, served like the install script) whose
# local.d script adds the tools the install needs (bash, util-linux's,
# xfsprogs, dosfstools) from Alpine's repositories, and an arch-chroot of its
# own, and runs the install script, as archiso's script= does (genfstab is
# Alpine's, alpine-conf's, which lists no swap: alpine.sh adds it).  GRUB is
# arm64-efi's, in
# EFI/alpine and at EFI/BOOT/BOOTAA64.EFI; the consoles are on ttyAMA0
# (virt's PL011).  vm-run-options: "--uefi --no-secure-boot".
#
# Needs: qemu-system-x86_64 with KVM (--arch arm64: qemu-system-aarch64 and
# AAVMF, through vm-run), qemu-img, xorriso, gpg, gpgv, minisign,
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
CHIMERA_DATE=20261103
# The key for that release batch: main/chimera-image-keys/files/20261103.pub
# in chimera-linux/cports (key ID B7CC5D3A17688906).
CHIMERA_KEY=RWQGiWgXOl3Mt/2GUL9HpuxyHyWBc29/lZ9wW6XZz29lx2YLwkKfXk52
GENTOO_STAGE3=20261108T170107Z
# Gentoo Linux Release Engineering (Automated Weekly Release Key), as
# https://www.gentoo.org/downloads/signatures/ lists it; its signing subkey
# is 534E4209AB49EEE1C19D96162C44695DB9F6043D (expires 2028-07-01).
GENTOO_KEY=13EBBDBEDE7A12775DFDB1BABB572E0E2D182910
NIXOS_RELEASE=26.05
NIXPKGS=https://releases.nixos.org/nixos/26.05/nixos-26.05.13164.6f06d204d8f2/nixexprs.tar.xz
NIX_VERSION=2.31.2
SLACKWARE_MIRROR=https://slackware.osuosl.org/slackware64-15.0
# Slackware Linux Project <security@slackware.com>, the key in the tree's
# GPG-KEY (https://www.slackware.com/gpg-key)
SLACKWARE_KEY=EC5649DA401E22ABFA6736EF6A4463C040102233
SLACKWARE_SERIES="a ap d l n"
# Installed from patches/packages/ instead of their series: the newest
# version CHECKSUMS.md5 lists, in the series package's place in setup's
# order; with no patch listed, the series package.  ca-certificates: 15.0's
# (20211216) bundle can't verify https://slackware.osuosl.org/ (wget: the
# issuer CN=SSL.com TLS Issuing RSA CA R1 can't be verified), so slackpkg
# and vm-update (its wget of the mirror's GPG-KEY) can't fetch from it.
SLACKWARE_PATCHED="ca-certificates"
# PCLinuxOS's KDE ISO of 2026-11-21 and openssh-server (not on the ISO), by
# SHA-256 (docs/design/pclinuxos.md PC1, Decisions 5 and 6).  ISO MD5
# 488ae2664b13c40a88fdfe4d298cc079, as NLUUG's and freedif's .md5sum say.
PCLINUXOS_MIRROR=https://ftp.nluug.nl/os/Linux/distr/pclinuxos
PCLINUXOS_ISO=pclinuxos64-kde-2026.11.iso
PCLINUXOS_ISO_SHA256=8844514243f1923dd4f03a4e8bff89f52c4c0998aa4bd910eb0b836a91a85dbb
PCLINUXOS_SSH_RPM=openssh-server-9.8p1-2pclos2024.x86_64.rpm
PCLINUXOS_SSH_RPM_SHA256=9e8317306ca7e022215230a08e68b7d502d38bcc2f3445aa17451014b808f5ae

here=$(dirname "$(realpath "$0")")
die() { echo "make-bootstrap: $*" >&2; exit 1; }

arch=amd64
if [[ ${1:-} == --arch ]]; then arch=${2:-}; shift 2 || die "--arch takes amd64 or arm64"; fi
[[ $# -eq 2 ]] || die "usage: $0 [--arch amd64|arm64] alpine|alpine-uefi|alpine-lvm|alpine-luks|void|chimera|chimera-uefi|gentoo|gentoo-systemd|artix-dinit|artix-s6|nixos|slackware|slackware-uefi|pclinuxos|pclinuxos-bios WORK_DIR"
distro=$1
case $arch in
    amd64) ;;
    arm64)
        [[ $distro == alpine ]] || die "--arch arm64 is for DISTRO alpine only, not $distro"
        [[ $(uname -m) == aarch64 ]] || die "--arch arm64 builds on an aarch64 host (Bigfoot), not on $(uname -m)" ;;
    *) die "--arch takes amd64 or arm64, not '$arch'" ;;
esac
iso_release=$ARCH_ISO_CURRENT
script=$distro
firmware=bios layout=classic           # the guest's firmware and disk (bootstrap/common.sh)
extra=()                               # more @NAME@=value settings for the script
case $distro in
    alpine) name=alpine324 root_fs=xfs
        extra=("ALPINE_BRANCH=v${ALPINE_VERSION%.*}") ;;
    alpine-uefi|alpine-lvm|alpine-luks)
        layout=${distro#alpine-} name=alpine324-${distro#alpine-} script=alpine firmware=uefi
        if [[ $layout == lvm ]]; then root_fs=xfs; else root_fs=ext4; fi
        extra=("ALPINE_BRANCH=v${ALPINE_VERSION%.*}") ;;
    void) name=void-musl root_fs=btrfs ;;
    chimera) name=chimera root_fs=xfs ;;
    chimera-uefi) name=chimera-uefi root_fs=ext4 script=chimera firmware=uefi layout=luks-esp ;;
    # 20G: efi-lvm's 1 GiB ESP, 2 GiB swap and a ~17 GiB PV for root 10G,
    # home 3G and ~4 GiB free (16G would leave a PV of ~12.99 GiB).
    gentoo) name=gentoo root_fs=ext4 firmware=uefi layout=efi-lvm DISK_SIZE=20G stage3=openrc
        extra=(GENTOO_INIT=openrc) ;;
    gentoo-systemd) name=gentoo-systemd root_fs=ext4 script=gentoo firmware=uefi layout=luks-esp stage3=systemd
        extra=(GENTOO_INIT=systemd) ;;
    artix-dinit) name=artix-dinit root_fs=jfs script=artix extra=(INIT=dinit) ;;
    artix-s6) name=artix-s6 root_fs=ext4 script=artix extra=(INIT=s6) ;;
    nixos) name=nixos2605 root_fs=bcachefs iso_release=$ARCH_ISO_LINUX_6_12
        INSTALL_MEMORY=8192
        extra=("NIXPKGS=$NIXPKGS" "NIX_VERSION=$NIX_VERSION" "STATE_VERSION=$NIXOS_RELEASE") ;;
    # The packages setup installs for these series, less the other kernel.
    slackware) name=slackware15 root_fs=ext4 layout=swap-root DISK_SIZE=20G
        extra=(KERNEL=huge) ;;
    slackware-uefi) name=slackware15-uefi root_fs=xfs script=slackware firmware=uefi layout=uefi DISK_SIZE=20G
        extra=(KERNEL=generic) ;;
    # install.sh's LAYOUTs: the installer's Automated one (UEFI), Manual
    # mode's /boot + swap + XFS / (BIOS).
    pclinuxos) name=pclinuxos root_fs=ext4 script=pclinuxos firmware=uefi layout=auto DISK_SIZE=32G
        extra=("SSH_RPM=$PCLINUXOS_SSH_RPM" "SSH_RPM_SUM=$PCLINUXOS_SSH_RPM_SHA256") ;;
    pclinuxos-bios) name=pclinuxos-bios root_fs=xfs script=pclinuxos firmware=bios layout=manual-xfs DISK_SIZE=32G
        extra=("SSH_RPM=$PCLINUXOS_SSH_RPM" "SSH_RPM_SUM=$PCLINUXOS_SSH_RPM_SHA256") ;;
    *) die "unknown DISTRO $distro" ;;
esac
# arm64: alpine324's twin for UEFI, alpine-uefi's disk with / on XFS.
[[ $arch == amd64 ]] || name=alpine324-arm64 firmware=uefi layout=uefi
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
if [[ $arch == arm64 ]]; then
    # The live system: Alpine's virt ISO for aarch64, signed as its minirootfs is.
    ISO=alpine-virt-$ALPINE_VERSION-aarch64.iso
    url=https://dl-cdn.alpinelinux.org/alpine/v${ALPINE_VERSION%.*}/releases/aarch64
    fetch "$url/$ISO.asc"
    fetch "$url/$ISO"
    gpg_key "$ALPINE_KEY" alpine-release-key.gpg
    gpgv --keyring ./alpine-release-key.gpg "$ISO.asc" "$ISO" 2>/dev/null ||
        die "$ISO is not signed by Alpine's release key"
else
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
fi

work=$(mktemp -d)
server=""
second_cd=""                           # pclinuxos: its ISO, the VM's second CD
# Files and directories extracted from the ISO are read-only.
cleanup() { [[ -n $server ]] && kill "$server" 2>/dev/null; chmod -R u+w "$work"; rm -rf "$work"; }
trap cleanup EXIT
mkdir "$work/www"
case $script in
    alpine)
        amach=x86_64
        [[ $arch == amd64 ]] || amach=aarch64
        f=alpine-minirootfs-$ALPINE_VERSION-$amach.tar.gz
        url=https://dl-cdn.alpinelinux.org/alpine/v${ALPINE_VERSION%.*}/releases/$amach
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
    chimera)
        f=chimera-linux-x86_64-ROOTFS-$CHIMERA_DATE-full.tar.gz
        url=https://repo.chimera-linux.org/live/$CHIMERA_DATE
        fetch "$url/sha256sums.txt" "chimera-sha256sums-$CHIMERA_DATE.txt"
        fetch "$url/sha256sums.txt.minisig" "chimera-sha256sums-$CHIMERA_DATE.txt.minisig"
        fetch "$url/$f"
        minisign -q -V -P "$CHIMERA_KEY" -m "chimera-sha256sums-$CHIMERA_DATE.txt" \
            -x "chimera-sha256sums-$CHIMERA_DATE.txt.minisig" || die "Chimera's sha256sums.txt is not signed by its release key"
        grep -F " $f" "chimera-sha256sums-$CHIMERA_DATE.txt" | sha256sum -c --quiet - || die "$f does not match Chimera's sha256sums.txt"
        ln -s "$PWD/$f" "$work/www/rootfs.tar.gz"
        ;;
    gentoo)
        f=stage3-amd64-$stage3-$GENTOO_STAGE3.tar.xz
        url=https://distfiles.gentoo.org/releases/amd64/autobuilds/$GENTOO_STAGE3
        fetch "$url/$f.asc"
        fetch "$url/$f"
        gpg_key "$GENTOO_KEY" gentoo-release-key.gpg
        gpgv --keyring ./gentoo-release-key.gpg "$f.asc" "$f" 2>/dev/null ||
            die "$f is not signed by Gentoo's Release Engineering key"
        ln -s "$PWD/$f" "$work/www/rootfs.tar.xz"
        stage3_file=$f
        ;;
    slackware)
        tree=${SLACKWARE_TREE:-$PWD/slackware64-15.0}
        mkdir -p "$tree"
        tree=$(realpath "$tree")
        gpg_key "$SLACKWARE_KEY" slackware-key.gpg
        if [[ ! -f $tree/CHECKSUMS.md5.asc ]]; then
            (cd "$tree" && fetch "$SLACKWARE_MIRROR/CHECKSUMS.md5" && fetch "$SLACKWARE_MIRROR/CHECKSUMS.md5.asc")
        fi
        gpgv --keyring ./slackware-key.gpg "$tree/CHECKSUMS.md5.asc" "$tree/CHECKSUMS.md5" 2>/dev/null ||
            die "$tree/CHECKSUMS.md5 is not signed by Slackware's key"
        series_re=$(tr ' ' '|' <<<"$SLACKWARE_SERIES")
        other=generic; [[ ${extra[0]} == KERNEL=generic ]] && other=huge
        # The series' packages and patches/packages/' (not its
        # subdirectories), for SLACKWARE_PATCHED.
        grep -E "  \./(slackware64/($series_re)|patches/packages)/[^/]+\.txz\$" "$tree/CHECKSUMS.md5" | sed 's|  \./|  |' >"$work/sums"
        grep -q '^[0-9a-f]*  slackware64/' "$work/sums" || die "CHECKSUMS.md5 lists no packages in series $SLACKWARE_SERIES"
        # setup's order: by series as listed, by name within one.
        for sr in $SLACKWARE_SERIES; do
            awk -v s="slackware64/$sr/" 'index($2, s) == 1 { print $2 }' "$work/sums" | sort
        done | grep -v "/kernel-$other-[^/]*\.txz\$" >"$work/www/pkglist.txt"
        # pkg_named NAME: the paths on stdin of package NAME, its file name
        # less -VERSION-ARCH-BUILD.txz (compared as a string, not a regex).
        pkg_named() {
            awk -v want="$1" '{ b = $NF; sub(/.*\//, "", b); n = split(b, f, "-"); name = f[1]
                for (i = 2; i <= n - 3; i++) name = name "-" f[i]
                if (n >= 4 && name == want) print $NF }'
        }
        for pp in $SLACKWARE_PATCHED; do
            series_p=$(pkg_named "$pp" <"$work/www/pkglist.txt")
            [[ -n $series_p && $(wc -l <<<"$series_p") -eq 1 ]] ||
                die "SLACKWARE_PATCHED's $pp isn't one package of series $SLACKWARE_SERIES"
            patch_p=$(awk 'index($2, "patches/packages/") == 1 { print $2 }' "$work/sums" | pkg_named "$pp" | sort -V | tail -n 1)
            if [[ -z $patch_p ]]; then
                echo "Slackware: CHECKSUMS.md5 lists no patch for $pp; $series_p stays"
                continue
            fi
            awk -v old="$series_p" -v new="$patch_p" '$0 == old { $0 = new } { print }' "$work/www/pkglist.txt" >"$work/pkglist.new"
            mv "$work/pkglist.new" "$work/www/pkglist.txt"
            echo "Slackware: $patch_p in place of $series_p"
        done
        missing=$(cd "$tree" && while read -r p; do [[ -f $p && -f $p.asc ]] || echo "$p"; done <"$work/www/pkglist.txt")
        if [[ -n $missing ]]; then
            echo "Downloading $(wc -l <<<"$missing") Slackware packages into $tree"
            # The mirror keeps only the newest patch of a package: one that
            # CHECKSUMS.md5 lists may be gone (ca-certificates 20261118
            # replaced the 20261107 that rebuild-inputs' CHECKSUMS.md5 of
            # 2026-11-12 lists).  15.0's series lines in it don't change.
            (cd "$tree" && sed "s|^|$SLACKWARE_MIRROR/|; p; s|\$|.asc|" <<<"$missing" |
                wget -q -nc -x -nH --cut-dirs=1 -i -) || {
                ! grep -q '^patches/' <<<"$missing" ||
                    echo "make-bootstrap: if the mirror has a newer patch than $tree/CHECKSUMS.md5 lists, put the mirror's CHECKSUMS.md5 and CHECKSUMS.md5.asc in $tree" >&2
                die "could not download Slackware's packages"
            }
        fi
        (cd "$tree" && grep -F -f "$work/www/pkglist.txt" "$work/sums" | md5sum -c --quiet -) ||
            die "Slackware's packages don't match CHECKSUMS.md5"
        while read -r p; do
            gpgv --keyring ./slackware-key.gpg "$tree/$p.asc" "$tree/$p" 2>/dev/null ||
                die "$p is not signed by Slackware's key"
        done <"$work/www/pkglist.txt"
        echo "Slackware: $(wc -l <"$work/www/pkglist.txt") packages checked"
        ln -s "$tree" "$work/www/tree"
        # The key, by its fingerprint, for slackpkg's keyring in the guest.
        cp slackware-key.gpg "$work/www/slackware-key.gpg"
        ;;
    pclinuxos)
        fetch "$PCLINUXOS_MIRROR/iso/$PCLINUXOS_ISO"
        fetch "$PCLINUXOS_MIRROR/apt/pclinuxos/64bit/RPMS.x86_64/$PCLINUXOS_SSH_RPM"
        echo "$PCLINUXOS_ISO_SHA256  $PCLINUXOS_ISO" | sha256sum -c --quiet - ||
            die "$PCLINUXOS_ISO does not match its pinned SHA-256"
        echo "$PCLINUXOS_SSH_RPM_SHA256  $PCLINUXOS_SSH_RPM" | sha256sum -c --quiet - ||
            die "$PCLINUXOS_SSH_RPM does not match its pinned SHA-256"
        cp "$here/pclinuxos/install.sh" "$work/www/install-pclinuxos.sh"
        ln -s "$PWD/$PCLINUXOS_SSH_RPM" "$work/www/$PCLINUXOS_SSH_RPM"
        second_cd=$PWD/$PCLINUXOS_ISO
        ;;
esac

# --- The live system's kernel, initramfs and boot options ---------------------------
if [[ $arch == arm64 ]]; then
    # Alpine's: its GRUB menu's first entry.
    xorriso -osirrox on -indev "$ISO" -extract /boot/grub/grub.cfg "$work/grub.cfg" 2>/dev/null ||
        die "no /boot/grub/grub.cfg on $ISO"
    kernel=$(awk '$1 == "linux" { print $2; exit }' "$work/grub.cfg")
    initrd=$(awk '$1 == "initrd" { print $2; exit }' "$work/grub.cfg")
    options=$(awk '$1 == "linux" { $1 = $2 = ""; sub(/^ +/, ""); print; exit }' "$work/grub.cfg")
    [[ $kernel == /boot/vmlinuz-* && $initrd == /boot/initramfs-* && $options == *modules=* ]] ||
        die "unexpected boot entry in $ISO's grub.cfg"
    # The serial console is the install's own (SERIAL_TTY), named below.
    options=$(sed -E 's/(^| )console=[^ ]*//g; s/^ +//' <<<"$options")
else
xorriso -osirrox on -indev "$ISO" -extract /loader/entries "$work/entries" 2>/dev/null ||
    die "no /loader/entries on $ISO"
# The first entry is the ISO's default boot option.
entry=$(find "$work/entries" -name '*.conf' | sort | head -n 1)
kernel=$(awk '$1 == "linux" { print $2 }' "$entry")
initrd=$(awk '$1 == "initrd" && $2 ~ /initramfs/ { print $2 }' "$entry")
options=$(sed -n 's/^options[[:space:]]*//p' "$entry")
[[ -n $kernel && -n $initrd && $options == *archisobasedir* ]] ||
    die "unexpected boot entry $(basename "$entry")"
fi
xorriso -osirrox on -indev "$ISO" -extract "$kernel" "$work/vmlinuz" \
    -extract "$initrd" "$work/initramfs.img" 2>/dev/null
chmod u+w "$work"/vmlinuz "$work"/initramfs.img

umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$name test guest" -f id_ed25519
# The LUKS key: printable, and without a newline, so the same bytes unlock it
# from the guest's key file, from this one (vm-mount --key) and typed.
luks_key=""
if [[ $layout == luks || $layout == luks-esp ]]; then
    luks_key=$(openssl rand -base64 32 | tr -d '\n')
    printf '%s' "$luks_key" >luks-keyfile
fi
umask 022
[[ $firmware == bios ]] || echo "--uefi --no-secure-boot" >vm-run-options

# Serve the install script and files to the guest only: QEMU's user-mode
# network maps 10.0.2.2 in the guest to the host's 127.0.0.1.
port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
serial_tty=ttyS0
[[ $arch == amd64 ]] || serial_tty=ttyAMA0     # virt's PL011
settings=(DISTRO="$distro" ARCH="$arch" SERIAL_TTY="$serial_tty"
    ROOT_FS="$root_fs" FIRMWARE="$firmware" LAYOUT="$layout" LUKS_KEY="$luks_key" HOSTNAME="$name"
    ROOT_PASSWORD="$password" ROOT_PASSWORD_HASH="$hash" AUTHORIZED_KEY="$(cat id_ed25519.pub)"
    SERVER="http://10.0.2.2:$port" "${extra[@]}")
# pclinuxos: its script alone (pclinuxos/install.sh partitions the disk).
common=$here/bootstrap/common.sh
[[ $script != pclinuxos ]] || common=/dev/null
python3 - "$work/www/install.sh" "$common" "$here/bootstrap/$script.sh" \
    "${settings[@]}" <<'EOF'
import re, sys
out, parts, settings = sys.argv[1], sys.argv[2:4], dict(a.split("=", 1) for a in sys.argv[4:])
text = "".join(open(p).read() for p in parts)
text = re.sub(r"@([A-Z_]+)@", lambda m: settings[m.group(1)], text)
open(out, "w").write(text)
EOF
if [[ $arch == arm64 ]]; then
    # Alpine's live system reads an overlay (apkovl=) over HTTP at boot: its
    # default services, the network, and a local.d script that adds the
    # install's tools and runs install.sh, its output on the serial console.
    python3 - "$work/www/bko.apkovl.tar.gz" "http://10.0.2.2:$port" \
        "https://dl-cdn.alpinelinux.org/alpine/v${ALPINE_VERSION%.*}" <<'EOF'
import io, sys, tarfile
out, server, mirror = sys.argv[1:4]
start = f"""#!/bin/sh
# make-bootstrap.sh --arch arm64: the live system's part, as archiso's
# script= is on amd64.  Then install.sh, whose output goes to the console.
exec >/dev/ttyAMA0 2>&1
fail() {{ echo "BACKOTTER-INSTALL-FAILED in the live system: $*"; sync; sleep 2; poweroff; exit 1; }}
echo "BACKOTTER-LIVE: adding the install's tools"
printf '%s\\n' {mirror}/main {mirror}/community >/etc/apk/repositories
apk update -q || fail "apk update"
apk add -q bash sfdisk wipefs blkid findmnt util-linux-misc mount umount \\
    xfsprogs dosfstools e2fsprogs curl || fail "apk add"
# mdev, not udev, runs here: udevadm settle is a pause.
printf '#!/bin/sh\\nsleep 1\\n' >/usr/bin/udevadm && chmod 755 /usr/bin/udevadm || fail "udevadm"
# arch-chroot: arch-install-scripts' can't be added (its genfstab conflicts
# with alpine-conf's, which the install uses); this one mounts what it does.
cat >/usr/bin/arch-chroot <<'AC' && chmod 755 /usr/bin/arch-chroot || fail "arch-chroot"
#!/bin/sh
r=$1; shift
mount -t proc proc "$r/proc" && mount --bind /sys "$r/sys" && mount --bind /dev "$r/dev" &&
    mount --bind /dev/pts "$r/dev/pts" && mount -t tmpfs -o mode=755 run "$r/run" || exit 1
chroot "$r" "$@"; rc=$?
umount "$r/run" "$r/dev/pts" "$r/dev" "$r/sys" "$r/proc"
exit $rc
AC
wget -q -O /tmp/install.sh {server}/install.sh || fail "fetching install.sh"
exec bash /tmp/install.sh >/dev/null 2>&1
"""
files = [
    ("etc/.default_boot_services", "", 0o644),
    ("etc/hostname", "bko-live\n", 0o644),
    ("etc/apk/world", "alpine-base\n", 0o644),
    ("etc/network/interfaces", "auto lo\niface lo inet loopback\n\nauto eth0\niface eth0 inet dhcp\n", 0o644),
    ("etc/local.d/bko-install.start", start, 0o755),
]
links = [("etc/runlevels/default/networking", "/etc/init.d/networking"),
         ("etc/runlevels/default/local", "/etc/init.d/local")]
with tarfile.open(out, "w:gz", format=tarfile.USTAR_FORMAT) as t:
    def entry(name, kind, mode=0o755):
        i = tarfile.TarInfo(name); i.type = kind; i.mode = mode; i.uid = i.gid = 0; i.uname = i.gname = "root"
        return i
    for d in ("etc", "etc/apk", "etc/network", "etc/local.d", "etc/runlevels", "etc/runlevels/default"):
        t.addfile(entry(d, tarfile.DIRTYPE))
    for name, text, mode in files:
        i = entry(name, tarfile.REGTYPE, mode); data = text.encode(); i.size = len(data)
        t.addfile(i, io.BytesIO(data))
    for name, target in links:
        i = entry(name, tarfile.SYMTYPE, 0o777); i.linkname = target
        t.addfile(i)
EOF
    options+=" ip=dhcp apkovl=http://10.0.2.2:$port/bko.apkovl.tar.gz"
fi
python3 -m http.server --bind 127.0.0.1 --directory "$work/www" "$port" >"$work/http.log" 2>&1 &
server=$!

# --- Install --------------------------------------------------------------------------
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing $distro; the live system's console goes to $2/install-serial.log"
if [[ $arch == arm64 ]]; then
    # Through vm-run, with the aarch64 host's machine (virt, -cpu host, AAVMF
    # without Secure Boot on a fresh variable store, the ISO a SCSI CD),
    # pinning and job_slice.
    ssh_port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
    SSH_PORT=$ssh_port timeout "$INSTALL_TIMEOUT" "$here/vm-run" --uefi --no-secure-boot \
        --cpus 4 --memory "$INSTALL_MEMORY" --serial-log install-serial.log --cdrom "$ISO" "$DISK" -- \
        -kernel "$work/vmlinuz" -initrd "$work/initramfs.img" \
        -append "$options console=tty0 console=$serial_tty,115200n8" -no-reboot ||
        die "the install failed or timed out; see install-serial.log"
else
# cow_spacesize: the live system's writable layer, where downloads land.
timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 4 -m "$INSTALL_MEMORY" -machine pc \
    -drive "file=$DISK,if=virtio,format=qcow2" \
    -drive "file=$ISO,media=cdrom,readonly=on" \
    ${second_cd:+-drive "file=$second_cd,media=cdrom,readonly=on"} \
    -nic user,model=virtio-net-pci \
    -kernel "$work/vmlinuz" -initrd "$work/initramfs.img" \
    -append "$options cow_spacesize=$((INSTALL_MEMORY / 2))M console=tty0 console=ttyS0,115200n8 script=http://10.0.2.2:$port/install.sh" \
    -display none -serial file:install-serial.log -monitor none -no-reboot ||
    die "the install failed or timed out; see install-serial.log"
fi
grep -q BACKOTTER-INSTALL-OK install-serial.log || {
    grep -a BACKOTTER-INSTALL-FAILED install-serial.log >&2 || true
    die "the install did not finish; see install-serial.log"
}

if [[ $script == gentoo ]]; then
    tree=$(sed -n 's/.*BACKOTTER-GENTOO-TREE: //p' install-serial.log | tr -d '\r' | tail -n 1)
    [[ -n $tree ]] || die "install-serial.log has no BACKOTTER-GENTOO-TREE line"
    python3 -c 'import json, sys; json.dump({"stage3": sys.argv[1], "timestamp.chk": sys.argv[2]}, open("portage-tree.json", "w"), indent=1)' \
        "$stage3_file" "$tree"
fi
qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
