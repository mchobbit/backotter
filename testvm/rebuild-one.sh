#!/usr/bin/env bash
# rebuild-one.sh GUEST [DISK MEM SLOT]: build GUEST from scratch with today's
# builder into REBUILD_DIR/vms/GUEST, then boot it once and say how it came
# up. The existing ~/backotter-work/vms/GUEST is only read (its ISOs and
# keys, linked or copied in). Run as root by cmp-run.sh, in a systemd scope,
# from a fixed copy of testvm/, so edits to the checkout don't reach a
# rebuild under way:
#   rm -rf /bkoscratch/rebuild/testvm; mkdir -p /bkoscratch/rebuild
#   cp -a ~/backotter-work/testvm /bkoscratch/rebuild/testvm
#   WEAR_TAG=rebuild:TAG CMP_CMD=/bkoscratch/rebuild/testvm/rebuild-one.sh ~/backotter-work/cmp-run.sh TAG SLOTS GUEST=DISK%MEM ...
# REBUILD_DIR (default /bkoscratch/rebuild) holds that testvm/ and vms/;
# sudo drops it from the environment, so another one goes in CMP_CMD:
# CMP_CMD="env REBUILD_DIR=DIR DIR/testvm/rebuild-one.sh".
# Builders that don't need root run as m, with TMPDIR=/mnt/bko-ram for their
# small scratch; the root ones (make-from-live.sh, make-debian-zfs.sh,
# make-debian-stack.sh, make-chimera-zfs.sh) without a TMPDIR, so in /tmp: theirs would reach the
# guest's chroot. Images go to REBUILD_DIR. Results: REBUILD_DIR/vms/GUEST/rebuild-build.json and
# rebuild-boot.json; exit 0 only if it built and booted.
#
# Two read-only modes, for testvm/rebuild-on (docs/design/img-speed.md F12),
# which seeds a build host's /bkoscratch/rebuild-inputs from Chopin's; they run
# from anywhere (also sent over ssh as bash -s) and touch nothing:
#   rebuild-one.sh --inputs GUEST
#       the rebuild-inputs folders GUEST's build reads: "own NAME" for the one
#       it takes as SRC (GUEST's, then the folder it shares: elementary8 for
#       elementary8-luks, ...), "uses NAME" for each other one its recipe or
#       ISO check names; exit 2 when there is no recipe.
#   rebuild-one.sh --input-files DIR
#       the names of the files in DIR (a guest's vms/ folder) the build would
#       take as its inputs when GUEST has no rebuild-inputs folder.
set -u
IN=/bkoscratch/rebuild-inputs
# What of a SRC folder is an input: not the guest's own outputs and records.
input_file() {
    case $1 in
    openSUSE-MicroOS.*.qcow2) return 0 ;;   # microos's input image, not a guest's disk
    *.qcow2|root-password|id_ed25519*|*.log|*.log.*|*.exit|*.start|image-state|vm-run-options|uefi-vars.fd|\
    *-passphrase|*-passphrase-slot1|geli-*.key|*.verified|*.png|*.json|*.part|*.raw|update*|vmupdate*|install-media.*|*.try1|rebuild*) return 1 ;;
    esac
    return 0
}
# The rebuild-inputs folder a guest shares with another, used when it has none of its own.
shares() {
    case $1 in
    elementary8-luks) echo elementary8 ;;   # built from elementary8's ISO
    void-zbm-enc) echo void-zbm ;;          # built from void-zbm's inputs
    slackware15-uefi) echo slackware15 ;;   # built from slackware15's tree
    pclinuxos-bios) echo pclinuxos ;;       # built from pclinuxos's ISO and RPM
    gnomeos51-tpm) echo gnomeos51 ;;        # built from gnomeos51's ISO and feed copy
    omnios60-mirror) echo omnios60 ;;       # built from omnios60's cloud image
    esac
}
MODE=build
case ${1:-} in
--inputs) MODE=inputs; shift; [ $# = 1 ] || { echo "usage: rebuild-one.sh --inputs GUEST" >&2; exit 2; } ;;
--input-files)
    [ $# = 2 ] && [ -d "$2" ] || { echo "usage: rebuild-one.sh --input-files DIR (a folder)" >&2; exit 2; }
    for f in "$2"/*; do
        [ -f "$f" ] || continue
        [ -e "$f.part" ] && continue        # an unfinished download: the builder fetches it
        input_file "${f##*/}" && echo "${f##*/}"
    done
    exit 0 ;;
-*) echo "rebuild-one.sh: unknown option $1" >&2; exit 2 ;;
esac
G=$1 SLOT=${4:-1}
R=${REBUILD_DIR:-/bkoscratch/rebuild} HOME_M=/home/m
T=$R/testvm W=$R/vms/$G
# The copy in R, never the checkout's (whose vms/ is the guests' own).
[ $MODE = inputs ] || [ "$(readlink -f "$0")" = "$(readlink -f $T/rebuild-one.sh)" ] ||
    { echo "run $T/rebuild-one.sh, or set REBUILD_DIR to this copy's" >&2; exit 2; }
# TMPDIR=/mnt/bko-ram only for the builders run as m: the root ones chroot
# into the guest, where that path doesn't exist.
RAMTMP=/mnt/bko-ram; unset TMPDIR
export COMPARE_SLOT=$SLOT SUDO_USER=m
# Inputs: a host without the ISOs gets copies in /bkoscratch/rebuild-inputs
# (testvm/rebuild-on inputs GUEST --host H copies Chopin's there).
# Its own folder, else the one it shares (shares()), else its vms/ folder.
SRC=$HOME_M/backotter-work/vms/$G S=$(shares $G)
if [ -d $IN/$G ]; then SRC=$IN/$G
elif [ -n "$S" ] && [ -d $IN/$S ]; then SRC=$IN/$S
fi
[ $MODE = inputs ] && SRC=$IN/$G    # (a stand-in: own names it)
if [ $MODE = build ]; then
    [ -d $T ] || { echo "no $T" >&2; exit 2; }
    [ -d "$SRC" ] || { echo "no $SRC" >&2; exit 2; }
fi
asm() { runuser -u m -- env TMPDIR="$RAMTMP" PATH="$PATH:/usr/sbin:/sbin" "$@"; }
note=()

case $G in
debian12)   cmd=(asm $T/make-debian.sh 12.0.0 ext4 "$W") ;;
debian13)   cmd=(asm $T/make-debian.sh 13.0.0 btrfs "$W") ;;
devuan6)    cmd=(asm $T/make-debian.sh --devuan --init openrc 6.1.1 ext3 "$W") ;;
# kali: from Kali's netinst ISO, SHA256SUMS(.gpg) and kali-signing-key.gpg in
# /bkoscratch/rebuild-inputs/kali (linked and copied in below, so nothing is
# downloaded); kali-rolling's packages as they are at the rebuild.
kali)       cmd=(asm $T/make-debian.sh --kali 2026.3 ext4 "$W") ;;
debian13-zfs) cmd=($T/make-debian-zfs.sh "$W" debian13-zfs) ;;
debian13-stack) cmd=($T/make-debian-stack.sh "$W" debian13-stack) ;;
ubuntu24)   cmd=(asm $T/make-ubuntu.sh 24.04 "$W") ;;
ubuntu24-uefi) cmd=(asm $T/make-ubuntu.sh --uefi --name ubuntu24-uefi 24.04.5 "$W") ;;
ubuntu24-luks) cmd=(asm $T/make-ubuntu.sh --uefi --luks --name ubuntu24-luks 24.04.5 "$W") ;;
ubuntu26-uefi) cmd=(asm $T/make-ubuntu.sh --uefi --name ubuntu26-uefi 26.04.1 "$W") ;;
ubuntu26-zfs) cmd=(asm $T/make-ubuntu.sh --uefi --zfs-guided --name ubuntu26-zfs 26.04.1 "$W") ;;
ubuntu26-zfsmirror) cmd=(asm $T/make-ubuntu.sh --zfs-mirror --name ubuntu26-zfsmirror 26.04.1 "$W") ;;
ubuntu26-raid) cmd=(asm $T/make-ubuntu.sh --uefi --raid --name ubuntu26-raid 26.04.1 "$W") ;;
ubuntu26-luks) cmd=(asm $T/make-ubuntu.sh --uefi --luks --name ubuntu26-luks 26.04.1 "$W") ;;
fedora44)   cmd=(asm $T/make-fedora.sh 44-1.7 "$W") ;;
fedora44-uefi) cmd=(asm $T/make-fedora.sh --uefi --name fedora44-uefi 44-1.7 "$W") ;;
fedora44-raid) cmd=(asm $T/make-fedora.sh --uefi --raid --name fedora44-raid 44-1.7 "$W") ;;
# kinoite44: the :44 tag as it is at the rebuild, so newer content than the
# guest's (kinoite44.digest says which).
kinoite44)  cmd=(asm $T/make-fedora.sh --ostree quay.io/fedora/fedora-kinoite:44 --name kinoite44 44-1.7 "$W") ;;
rocky10)    cmd=(asm $T/make-rocky.sh 10.0 "$W") ;;
rocky10-lvm) cmd=(asm $T/make-rocky.sh --lvm 10.0 "$W") ;;
rocky10-raid5) cmd=(asm $T/make-rocky.sh --raid5 10.0 "$W") ;;
tumbleweed) cmd=(asm $T/make-opensuse.sh 20261030 "$W") ;;
# leap16: Agama from Leap 16.1's offline ISO (Build60.2, pinned in
# make-opensuse.sh), its .sha512(.asc) in /bkoscratch/rebuild-inputs/leap16;
# the packages as on the ISO.
leap16)     cmd=(asm $T/make-opensuse.sh leap 16.1 "$W") ;;
# microos: openSUSE's MicroOS VM image of snapshot 20261111, its .sha256 and
# .sha256.asc, from /bkoscratch/rebuild-inputs/microos (only the current
# snapshot's is served); the packages as in the image (vm-update updates it).
microos)    cmd=(asm $T/make-opensuse.sh microos 20261111 "$W") ;;
# aeon: tik from openSUSE's Aeon installer of snapshot 20261111 (Aeon-Installer...raw.xz,
# its .sha256 and .sha256.asc), from /bkoscratch/rebuild-inputs/aeon (only the
# current snapshot's is served), on a new swtpm TPM; the packages as on the stick.
aeon)       cmd=(asm $T/make-opensuse.sh aeon 20261111 "$W") ;;
alpine324)  cmd=(asm $T/make-bootstrap.sh alpine "$W") ;;
void-musl)  cmd=(asm $T/make-bootstrap.sh void "$W") ;;
# void-zbm: glibc Void on ZFS, booted by ZFSBootMenu; a root builder, its inputs
# (Void's ROOTFS, ZBM's release EFI, zbm-kcl, all pinned in the builder) from
# /bkoscratch/rebuild-inputs/void-zbm, copied into W; xbps's packages at the rebuild.
void-zbm)   cmd=($T/make-void-zfs.sh "$W" void-zbm) ;;
# void-zbm-enc: the same with the guide's native encryption; void-zbm's inputs;
# a new zfs-passphrase each build (typed at ZFSBootMenu's prompt by console-boot.py).
void-zbm-enc) cmd=($T/make-void-zfs.sh "$W" void-zbm-enc --encrypt) ;;
chimera)    cmd=(asm $T/make-bootstrap.sh chimera "$W") ;;
chimera-uefi) cmd=(asm $T/make-bootstrap.sh chimera-uefi "$W") ;;
chimera-zfs) cmd=($T/make-chimera-zfs.sh "$W" chimera-zfs) ;;
# gentoo: its stage3 at the pinned date, from /bkoscratch/rebuild-inputs/gentoo
# (Gentoo prunes autobuilds); the tree and packages as they are at the rebuild.
gentoo)     cmd=(asm $T/make-bootstrap.sh gentoo "$W") ;;
# gentoo-systemd: the same, with the systemd stage3 from
# /bkoscratch/rebuild-inputs/gentoo-systemd.
gentoo-systemd) cmd=(asm $T/make-bootstrap.sh gentoo-systemd "$W") ;;
artix-dinit|artix-s6) cmd=(asm $T/make-bootstrap.sh $G "$W") ;;
nixos2605)  cmd=(asm $T/make-bootstrap.sh nixos "$W") ;;
# slackware15, slackware15-uefi: Slackware 15.0's frozen tree (series a, ap,
# d, l, n), in /bkoscratch/rebuild-inputs/slackware15 (slackware64/ and
# CHECKSUMS.md5 with its .asc), checked by make-bootstrap.sh against
# Slackware's key; the 15.0 patches come later, by vm-update.
slackware15) cmd=(asm env SLACKWARE_TREE=/bkoscratch/rebuild-inputs/slackware15 $T/make-bootstrap.sh slackware "$W") ;;
slackware15-uefi) cmd=(asm env SLACKWARE_TREE=/bkoscratch/rebuild-inputs/slackware15 $T/make-bootstrap.sh slackware-uefi "$W") ;;
# pclinuxos, pclinuxos-bios: PCLinuxOS's KDE ISO and the openssh-server RPM in
# /bkoscratch/rebuild-inputs/pclinuxos (both SHA-256s pinned in make-bootstrap.sh,
# with the Arch ISO it boots), by pclinuxos/install.sh; the ISO's packages only.
pclinuxos)  cmd=(asm $T/make-bootstrap.sh pclinuxos "$W") ;;
pclinuxos-bios) cmd=(asm $T/make-bootstrap.sh pclinuxos-bios "$W") ;;
arch)       cmd=(asm $T/make-arch.sh 2026.10.01 "$W") ;;
cachyos)    cmd=(asm $T/make-arch.sh --cachyos --root-fs jfs 2026.10.01 "$W") ;;
# cachyos-zfs: from CachyOS's live ISO in /bkoscratch/rebuild-inputs/cachyos-zfs
# (or the guest's own dir); CachyOS's and Arch's packages as they are at the rebuild.
cachyos-zfs) cmd=(asm $T/make-cachyos-zfs.sh --iso-dir "$SRC" 260809 "$W") ;;
# pikaos4: from PikaOS's live ISO in /bkoscratch/rebuild-inputs/pikaos4 (or the
# guest's own dir), its SHA-256 pinned in the builder; the ISO's packages only.
pikaos4)    cmd=(asm $T/make-pikaos.sh --iso-dir "$SRC" pikaos4 "$W") ;;
# pikaos4-luks: from pikaos4's ISO (Btrfs on LUKS2, BOOTX64 mode); a new
# console-passphrase each build.
pikaos4-luks) cmd=(asm $T/make-pikaos.sh --iso-dir /bkoscratch/rebuild-inputs/pikaos4 pikaos4-luks "$W") ;;
# proxmox9, proxmox9-mirror: Proxmox's automated installer, from the ISO, its
# .asc, the release key and the assistant's .deb in /bkoscratch/rebuild-inputs/proxmox9
# (all pinned in the builder; the prepared ISO is made there per first-boot.sh).
proxmox9|proxmox9-mirror) cmd=(asm $T/make-proxmox.sh --iso-dir /bkoscratch/rebuild-inputs/proxmox9 $G "$W") ;;
# truenas25, truenas25-mirror: TrueNAS's installer (its JSON-RPC API) from the
# 25.10.6 ISO, then the 25.10.7 and 25.10.8 .update files (truenas25-mirror: 25.10.8
# only), each a new boot environment, in /bkoscratch/rebuild-inputs/truenas25
# (SHA-256 pinned in the builder).
truenas25|truenas25-mirror) cmd=(asm $T/make-truenas.sh --iso-dir /bkoscratch/rebuild-inputs/truenas25 $G "$W") ;;
# qubes43: Qubes OS's installer from the R4.3.2 ISO in /bkoscratch/rebuild-inputs/qubes43,
# with its .DIGESTS(.asc) and the release and master keys (all pinned in the builder;
# the prepared ISO is made there per qubes/install-grub.cfg), driven by a kickstart and
# keys; the ISO's templates only (no updates: dom0 has no network). Needs nested SVM
# and ~11 GiB of memory; its boot check is the builder's own check boot (below).
qubes43)    cmd=(asm $T/make-qubes.sh --iso-dir /bkoscratch/rebuild-inputs/qubes43 $G "$W") ;;
# vanilla3: from Vanilla OS's live ISO in /bkoscratch/rebuild-inputs/vanilla3 (or
# the guest's own dir), its SHA-256 pinned in the builder; the system image
# ghcr.io/vanilla-os/gnome:latest as it is at the rebuild (pulled by albius;
# the builder's abroot upgrade --force fills root B from it). ~22 GiB at its peak.
vanilla3)   cmd=(asm $T/make-vanilla.sh --iso-dir "$SRC" vanilla3 "$W") ;;
# kdelinux: KDE Linux installed by kdelinux/install.sh from the rescue stick's
# test image (the newest ~m/backotter-work/images/linux/test/*.img) under QEMU,
# V1 202611240254 then V2 202611270255 by systemd-sysupdate, both from the
# mirrored sysupdate sets in /bkoscratch/rebuild-inputs/kdelinux/sysupdate (every
# SHA-256 pinned in the builder; upstream keeps 4 dailies). ~20 GB in W.
kdelinux)   cmd=(asm $T/make-kdelinux.sh --iso-dir /bkoscratch/rebuild-inputs/kdelinux kdelinux "$W") ;;
# anduinos2: from AnduinOS's live ISO in /bkoscratch/rebuild-inputs/anduinos2 (or
# the guest's own dir), its SHA-256 pinned in the builder; the ISO's packages only.
anduinos2)  cmd=(asm $T/make-anduinos.sh "$SRC/AnduinOS-2.0.5-amd64.iso" "$W" anduinos2) ;;
# solus49: from Solus's Xfce live ISO in /bkoscratch/rebuild-inputs/solus49 (or
# the guest's own dir), its SHA-256 pinned in the builder (.sha256sum.sign
# checked on download); eopkg up from the Polaris repository at the rebuild.
solus49)    cmd=(asm $T/make-solus.sh "$SRC/Solus-Xfce-Release-2026-04-18.iso" "$W" solus49) ;;
# guix15, guix15-bios, guix15-luks: from Guix's 1.5.0 installation ISO in
# /bkoscratch/rebuild-inputs/guix15 (its SHA-256 pinned in the builder, its
# .sig checked on download), with 1.5.0's substitutes at the rebuild
# (guix15-luks: a new LUKS passphrase, console-passphrase, each build).
guix15|guix15-bios|guix15-luks) cmd=(asm $T/make-guix.sh --iso-dir /bkoscratch/rebuild-inputs/guix15 $G "$W") ;;
# uc24, uc24-fde: Ubuntu Core 24's stock signed image in
# /bkoscratch/rebuild-inputs/ubuntu-core24 (its SHA-256 pinned in the builder,
# SHA256SUMS beside it), every snap refreshed from the store at the rebuild.
# uc24-fde's recovery key goes beside W (W.recovery), never over the guest's own
# in rebuild-inputs. Its boot check below boots on copies of W's uefi-vars.fd
# and tpm/ (vm-run-options' --guest-vars and --guest-tpm, under --ram), as
# every test boot of it does: the same machine, so it unlocks unattended.
uc24)       cmd=(asm $T/make-ubuntu-core.sh uc24 /bkoscratch/rebuild-inputs/ubuntu-core24/ubuntu-core-24-amd64.img.xz "$W") ;;
uc24-fde)   cmd=(asm $T/make-ubuntu-core.sh --recovery-key-file "$W.recovery" uc24-fde /bkoscratch/rebuild-inputs/ubuntu-core24/ubuntu-core-24-amd64.img.xz "$W") ;;
# gnomeos51, gnomeos51-tpm: upstream's raw pre-install (systemd-repart --image)
# of GNOME OS 51.1's ISO in /bkoscratch/rebuild-inputs/gnomeos51 (its SHA-256
# pinned in make-gnomeos.sh; GNOME publishes none), then the one update into
# slot B from the local copy of the feed's 51.1-66 files beside it
# (sysupdate-51.1-66: the signed SHA256SUMS and that build's usr, verity, UKI),
# so the build is the same whatever the feed holds today. gnomeos51-tpm's
# recovery key goes beside W (W.recovery), as uc24-fde's; its boot check boots
# on copies of W's uefi-vars.fd (GNOME's keys enrolled) and tpm/.
gnomeos51)  cmd=(asm $T/make-gnomeos.sh gnomeos51 $IN/gnomeos51/gnome_os_51.1-x86_64.iso "$W") ;;
gnomeos51-tpm) cmd=(asm $T/make-gnomeos.sh --recovery-key-file "$W.recovery" gnomeos51-tpm $IN/gnomeos51/gnome_os_51.1-x86_64.iso "$W") ;;
endeavouros) cmd=(asm $T/make-arch.sh --live endeavouros "$SRC/EndeavourOS_Titan-Nova-2026.08.15.iso" 2026.10.01 "$W") ;;
endeavouros-grub) cmd=(asm $T/make-arch.sh --live endeavouros-grub /bkoscratch/rebuild-inputs/endeavouros/EndeavourOS_Titan-Nova-2026.08.15.iso 2026.10.01 "$W") ;;
manjaro)    cmd=(asm $T/make-arch.sh --live manjaro "$SRC/manjaro-xfce-26.1.1-minimal-260825-linux71.iso" 2026.10.01 "$W") ;;
# garuda: from Garuda's dr460nized live ISO in /bkoscratch/rebuild-inputs/garuda,
# its SHA-256 pinned in make-arch.sh; Garuda's, chaotic-aur's and Arch's packages
# as on the ISO.
garuda)     cmd=(asm $T/make-arch.sh --live garuda "$SRC/garuda-dr460nized-linux-garuda-260819.iso" 2026.10.01 "$W") ;;
# biglinux: from BigLinux's k618 live ISO in /bkoscratch/rebuild-inputs/biglinux,
# SHA-256 1d12186abab0a2cb33ee9f0edd30140cd8ed842aa081e90ad4f0e3d050e6ae90, pinned
# in make-arch.sh (BigLinux signs no ISO); its rootfs and desktopfs as on the ISO.
biglinux)   cmd=(asm $T/make-arch.sh --live biglinux "$SRC/biglinux_2026-10-03_k618.iso" 2026.10.01 "$W") ;;
mx25)       cmd=($T/make-from-live.sh mx ext2 "$SRC/MX-25.3_Xfce_x64.iso" "$W" mx25) ;;
mint22)     cmd=($T/make-from-live.sh mint f2fs "$SRC/linuxmint-22.3-xfce-64bit.iso" "$W" mint22) ;;
pop24)      cmd=($T/make-from-live.sh pop ext4 "$SRC/pop-os_24.04_amd64_intel_20.iso" "$W" pop24) ;;
zorin18)    cmd=($T/make-from-live.sh zorin nilfs2 "$SRC/Zorin-OS-18.1-Core-64-bit.iso" "$W" zorin18) ;;
neon24)     cmd=($T/make-from-live.sh neon ext4 "$SRC/neon-user-desk-20261025-1825.iso" "$W" neon24) ;;
elementary8) cmd=($T/make-from-live.sh elementary ext4 "$SRC/elementaryos-8.1-stable-amd64.20260219.iso" "$W" elementary8) ;;
elementary8-luks) cmd=($T/make-from-live.sh --luks elementary ext4 "$SRC/elementaryos-8.1-stable-amd64.20260219.iso" "$W" elementary8-luks) ;;
omarchy)    cmd=(asm $T/make-omarchy.sh 4.0.6 "$W") ;;
freebsd15)  cmd=(asm $T/make-freebsd.sh 15.1 zfs "$W") ;;
freebsd15-ufs) cmd=(asm $T/make-freebsd.sh 15.1 ufs "$W") ;;
freebsd15-geli) cmd=(asm $T/make-freebsd.sh --uefi-geli 15.1 zfs "$W") ;;
freebsd15-geli-bios) cmd=(asm $T/make-freebsd.sh --bios-geli 15.1 zfs "$W") ;;
freebsd15-geli-ufs) cmd=(asm $T/make-freebsd.sh --geli-ufs 15.1 ufs "$W") ;;
freebsd14)  cmd=(asm $T/make-freebsd.sh 14.5 zfs "$W") ;;
hardenedbsd15) cmd=(asm $T/make-hardenedbsd.sh 15 23 ufs "$W") ;;
hardenedbsd15-zfs) cmd=(asm $T/make-hardenedbsd.sh 15 23 zfs "$W") ;;
# ghostbsd26: its install VM takes 8 GB (INSTALL_MEMORY): reserve ~9 GiB.
ghostbsd26) cmd=(asm $T/make-ghostbsd.sh 26.2-R15.1p6 ufs "$W") ;;
ghostbsd26-zfs) cmd=(asm $T/make-ghostbsd.sh 26.2-R15.1p6 zfs "$W") ;;
aerynos)    cmd=(asm $T/make-aerynos.sh 2026.11 2026-11-02-unstable-stream "$W") ;;
openbsd8)   cmd=(asm $T/make-openbsd.sh 8.0 "$W") ;;
netbsd11)   cmd=(asm $T/make-netbsd.sh 11.1 "$W") ;;
alpinebsd)  cmd=(asm $T/make-netbsd.sh --alpinebsd 11.0 "$W") ;;
dragonfly6) cmd=(asm $T/make-dragonfly.sh 6.4.2 "$W") ;;
omnios58)   cmd=(asm $T/make-omnios.sh r151058 "$W") ;;
# omnios60, omnios60-mirror: OmniOS r151060's cloud image in
# /bkoscratch/rebuild-inputs/omnios60, checked by make-omnios.sh against its
# .sha256 (OmniOS signs neither). r151060 has illumos 18322's virtio notify fix.
omnios60)   cmd=(asm $T/make-omnios.sh r151060 "$W") ;;
omnios60-mirror) cmd=(asm $T/make-omnios.sh --rpool-mirror r151060 "$W") ;;
# oi2610: OpenIndiana's text ISO and its installer, driven over the console;
# oi2610-bios: OpenIndiana's supplemental cloud image. Both in
# /bkoscratch/rebuild-inputs/GUEST, their SHA-256s pinned in make-openindiana.sh
# (OpenIndiana signs neither); the packages as on the media (no pkg update).
oi2610)     cmd=(asm $T/make-openindiana.sh oi2610 "$SRC/OI-hipster-text-20261030.iso" "$W") ;;
oi2610-bios) cmd=(asm $T/make-openindiana.sh --cloud oi2610-bios "$SRC/OI-hipster-cloudimage-20261107.img.zst" "$W") ;;
sculpt2610) cmd=(asm $T/make-sculpt.sh 26.10 "$W") ;;
*) echo "no recipe for $G" >&2; exit 2 ;;
esac
# The image's name: make-freebsd.sh calls it freebsd$major.qcow2 whatever the
# guest, make-ghostbsd.sh ghostbsd$major.qcow2, make-hardenedbsd.sh
# hardenedbsd$major.qcow2.
IMG=$G; case $G in freebsd15-ufs|freebsd15-geli|freebsd15-geli-bios|freebsd15-geli-ufs) IMG=freebsd15 ;; ghostbsd26-zfs) IMG=ghostbsd26 ;; hardenedbsd15-zfs) IMG=hardenedbsd15 ;; esac
# Never two VMs on one port: compare.py's boot uses 2230 + COMPARE_SLOT.
if [ $MODE = build ] && ss -ltnH "sport = :$((2230 + SLOT))" | grep -q .; then echo "port $((2230 + SLOT)) is taken" >&2; exit 2; fi
# The ISO: the recipe's .iso argument, wherever options put it.
ISO="" what=ISO
case $G in mx25|mint22|pop24|zorin18|neon24|elementary8|elementary8-luks|anduinos2|solus49)
    for a in "${cmd[@]}"; do case $a in *.iso) ISO=$a ;; esac; done ;;
proxmox9|proxmox9-mirror) ISO=$IN/proxmox9/proxmox-ve_9.2-1.iso ;;
truenas25|truenas25-mirror) ISO=$IN/truenas25/TrueNAS-SCALE-25.10.6.iso ;;
qubes43) ISO=$IN/qubes43/Qubes-R4.3.2-x86_64.iso ;;
uc24|uc24-fde) ISO=$IN/ubuntu-core24/ubuntu-core-24-amd64.img.xz what=image ;;
gnomeos51|gnomeos51-tpm) ISO=$IN/gnomeos51/gnome_os_51.1-x86_64.iso ;;
esac
if [ $MODE = inputs ]; then
    # own: GUEST's folder, then the one it shares; uses: every other folder the
    # recipe's words or the ISO name (a word with $IN/ in it, anywhere).
    S=$(shares $G)
    echo "own $G"; [ -n "$S" ] && echo "own $S"
    for a in "${cmd[@]}" "$ISO"; do
        while [[ $a == *"$IN/"* ]]; do
            a=${a#*"$IN/"}; n=${a%%/*}; n=${n%%[!A-Za-z0-9._-]*}
            [ -n "$n" ] && [ "$n" != $G ] && [ "$n" != "$S" ] && echo "uses $n"
        done
    done | awk '!seen[$0]++'
    exit 0
fi
[ -z "$ISO" ] || [ -f "$ISO" ] || { echo "no $what $ISO" >&2; exit 2; }
leftovers() {  # what a failed root builder may leave: this build's loop devices; mounts, pools, arrays, VGs and LUKS volumes of any build on the host
    { losetup -a | grep -F "$W"; findmnt -rno TARGET | grep -E '^/mnt/(bko-debian-zfs|bko-debian-stack|bko-chimera-zfs|live-)'
      zpool list -H -o name 2>/dev/null | grep -E '^bko(dz|cz)'
      # make-from-live.sh --layout distinst: VG "data" on a loop device (or in cryptdata), LUKS volume "cryptdata".
      pvs --devicesfile "" --noheadings -o vg_name,pv_name 2>/dev/null | awk '$1 == "data" && $2 ~ /^\/dev\/(loop|mapper\/cryptdata$)/ { print "vg-data:" $2 }'
      ls /dev/md 2>/dev/null | grep '^bkos'; dmsetup ls 2>/dev/null | awk '$1 ~ /^bkos/ || $1 == "cryptdata" { print $1 }'; } | tr '\n' ' '
}

mkdir -p $R/vms && chown m:m $R $R/vms
if [ -e $W/$IMG.qcow2 ] && [ "$(cat $W/build.exit 2>/dev/null)" = 0 ]; then
    echo "== $G: already built; booting it again"
else
    # A fresh WORK_DIR: a failed attempt's leftovers would stop the builder.
    if [ -e $W ]; then mv $W $W.failed-$(date +%s); fi
    install -d -o m -g m $W
    # The inputs: everything in SRC but the guest's own outputs. Big files
    # are linked (only read), small ones copied (some builders rewrite them).
    for f in "$SRC"/*; do
        [ -f "$f" ] || continue
        [ -e "$f.part" ] && continue        # an unfinished download: the builder fetches it
        b=${f##*/}
        input_file "$b" || continue
        if [ "$(stat -Lc %s "$f")" -gt $((64 << 20)) ]; then ln -s "$(readlink -f "$f")" $W/$b
        else cp -L "$f" $W/$b; fi
    done
    # Debian's builder reads debian-signing-key.gpg, the old one debian-cd-key.gpg.
    [ -e $W/debian-cd-key.gpg ] && [ ! -e $W/debian-signing-key.gpg ] && cp $W/debian-cd-key.gpg $W/debian-signing-key.gpg
    chown -h m:m $W/* 2>/dev/null
    for l in $W/*; do [ -L "$l" ] && [ ! -e "$l" ] && { echo "$l: target gone" >&2; exit 2; }; done
    echo "== $G: building: ${cmd[*]}"
    t0=$(date +%s)
    (cd $W && "${cmd[@]}") >$W/build.log 2>&1
    rc=$?
    echo $rc >$W/build.exit; chown m:m $W/build.log $W/build.exit
    echo "== $G: builder exit $rc after $(( $(date +%s) - t0 )) s"
    tail -5 $W/build.log
    left=""; [ $rc = 0 ] || left=$(leftovers)
    printf '{"guest": "%s", "build_exit": %d, "build_s": %d, "leftovers": "%s"}\n' \
        $G $rc $(( $(date +%s) - t0 )) "$left" >$W/rebuild-build.json
    [ -n "$left" ] && echo "== $G: left behind: $left"
    [ $rc = 0 ] && [ -e $W/$IMG.qcow2 ] || { echo "== $G: build failed"; exit 1; }
fi

# Hardware the builder should have recorded: if it didn't and the old guest
# has some, borrow it for the boot, and say so (a finding about the builder).
if [ ! -e $W/vm-run-options ] && [ -e $HOME_M/backotter-work/vms/$G/vm-run-options ]; then
    cp $HOME_M/backotter-work/vms/$G/vm-run-options $W/vm-run-options.borrowed
fi
[ -e $W/vm-run-options.borrowed ] && [ ! -e $W/vm-run-options ] &&
    note+=("vm-run-options not written by the builder; borrowed the old guest's: $(cat $W/vm-run-options.borrowed)")

echo "== $G: booting it"
rm -f $W/rebuild-boot.json $W/vm-run.*.qcow2 $W/rebuild-boot-console.log.monitor
if [ $G = sculpt2610 ]; then
    asm $T/sculpt-check.sh $W >$W/rebuild-boot.log 2>&1; brc=$?
    printf '{"guest": "%s", "boot": {"sculpt_check_exit": %d}}\n' $G $brc >$W/rebuild-boot.json
elif [ $G = qubes43 ]; then
    # No network in dom0, so no SSH: bko-qubes-check.service's serial lines.
    asm $T/make-qubes.sh --check-only $G $W >$W/rebuild-boot.log 2>&1; brc=$?
    printf '{"guest": "%s", "boot": {"qubes_check_exit": %d}}\n' $G $brc >$W/rebuild-boot.json
else
    python3 -I - "$T/compare" "$W" "$IMG" "${note[@]+"${note[@]}"}" >$W/rebuild-boot.log 2>&1 <<'EOF'
import json, shlex, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
w = Path(sys.argv[2]); notes = sys.argv[4:]
try:
    import compare as c     # in the try: it exits at import on a bad COMPARE_SLOT
    # The BSDs' and OmniOS's way to power off, where Linux's poweroff isn't one.
    ssh = c.VM.ssh
    # (In sh: DragonFly root's shell is csh.)
    OFF = "sync; sh -c 'PATH=/sbin:/usr/sbin:$PATH; poweroff || halt -p || shutdown -p now || shutdown -y -i5 -g0'"
    c.VM.ssh = lambda self, cmd, *a, **k: ssh(self, OFF if cmd == "sync; poweroff" else cmd, *a, **k)
    cfg = c.GuestConfig(w)
    cfg.base = w / f"{sys.argv[3]}.qcow2"
    borrowed = w / "vm-run-options.borrowed"
    if borrowed.exists() and cfg.options is None:   # in memory only: W keeps what the builder wrote
        opts, cfg.options = shlex.split(borrowed.read_text()), []
        while opts:
            o = opts.pop(0)
            if o == "--disk" and opts:
                cfg.disks.append(w / opts.pop(0))
            elif o == "--guest-vars" and opts:
                cfg.vars = w / opts.pop(0)
            elif o == "--guest-tpm" and opts:
                cfg.tpm = w / opts.pop(0)
            else:
                cfg.options.append(o)
    b = c.boot([cfg.base] + cfg.disks, cfg, cfg.source_hardware(cfg.hardware({})), w / "rebuild-boot-console.log")
except BaseException as e:
    b = {"ssh": False, "error": repr(e)}
json.dump({"guest": w.name, "boot": b, "notes": notes}, open(w / "rebuild-boot.json", "w"), indent=1)
print(json.dumps(b))
sys.exit(0 if b.get("ssh") else 1)
EOF
    brc=$?
fi
chown m:m $W/rebuild-boot* 2>/dev/null
tail -3 $W/rebuild-boot.log
echo "== $G: boot check exit $brc"
exit $brc
