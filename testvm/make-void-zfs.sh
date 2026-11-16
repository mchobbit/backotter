#!/usr/bin/env bash
# Build a glibc Void Linux test guest for BackOtter with its root on ZFS,
# booted by ZFSBootMenu (ZBM), from the host, as ZBM's own "Void Linux
# (UEFI)" guide lays it out (docs/design/zfsbootmenu.md): UEFI, GPT, an ESP
# that holds only ZBM's release EFI image, and the pool zroot with two boot
# environments (BEs), each with its own kernels and initramfs in its /boot.
#
# Usage: sudo ./make-void-zfs.sh [--inputs DIR] WORK_DIR NAME [--encrypt]
#   e.g. sudo ./make-void-zfs.sh --inputs /bkoscratch/rebuild-inputs/void-zbm vms/void-zbm void-zbm
#
# --encrypt (void-zbm-enc): the guide's native encryption.  zroot is an
# encryption root (-O encryption=aes-256-gcm -O keyformat=passphrase,
# keylocation=file:///etc/zfs/zroot.key once the pool is made, the build
# loading the key from a copy in its own scratch directory), and
# org.zfsbootmenu:keysource=zroot/ROOT/void on zroot.  The passphrase is new
# each build (24 letters and digits), in WORK_DIR/zfs-passphrase (mode 600,
# no newline) and in the guest's /etc/zfs/zroot.key (mode 000, the same
# bytes, so bko-holt load's --key-file check finds them equal), never
# anywhere else; dracut puts that file in every initramfs (zol.conf's
# install_items).  So ZFSBootMenu asks for the passphrase once a boot, on
# the serial console, and the BE's initramfs opens the pool with its key
# file.  The build's two boots go through console-boot.py, which types it
# there; vm-update, compare.py and ht1.sh type zfs-passphrase the same way,
# and vm-mount --key loads it.
#
# The inputs, in DIR (default WORK_DIR), none downloaded here, each pinned
# below by SHA-256:
#   void-x86_64-ROOTFS-20250202.tar.xz, sha256sum.txt, sha256sum.sig
#                    Void's glibc ROOTFS; sha256sum.txt is checked with
#                    minisign against Void's key for that date (the same as
#                    make-bootstrap.sh's VOID_KEY), the tarball against it
#   zfsbootmenu-release-x86_64-v3.1.0-linux6.12.EFI, sha256.txt, sha256.sig
#                    ZBM v3.1.0's release image (what the guide's
#                    get.zfsbootmenu.org/efi redirects to); sha256.txt is
#                    checked with signify against ZBM's signing key, pinned
#                    below (releng/keys/zfsbootmenu.pub at v3.1.0), the image
#                    against it
#   zbm-kcl          the release's tool for the image's embedded command
#                    line: in no signed list, so pinned by SHA-256 alone.  It
#                    runs here only on a copy of the image (objcopy, objdump)
#   zfsbootmenu.pub  only compared with the pinned key
# xbps in the chroot then checks every package against the keys in the
# tarball's own /var/db/xbps/keys; the packages are the live repository's of
# the day it is built.
#
# Why from the host, as make-chimera-zfs.sh: the pool is made by the host's
# OpenZFS (2.3), with the guide's compatibility=openzfs-2.3-linux, so the
# hosts, the rescue image (2.4) and ZBM 3.1.0 (2.4) can all import and
# write it.  The guide's live system (hrmpf) is the host.  The disk is a
# sparse raw file on a loop device, converted to qcow2 at the end.  The pool
# is created under a temporary in-core name (-t), so it can't clash with a
# host's own pool, while its on-disk name is zroot; it is exported at the
# end.  No zpool or zfs command runs in the chroot (Void's newer userland
# against the host's module): the chroot has no /dev/zfs, and zfs set,
# snapshot and clone run from the host.  The chroot has a /dev of its own,
# never the host's.
#
# The guest, host name NAME, as the guide's steps (its placeholders made
# real): GPT p1 ESP 1m:+512m (ef00, FAT32, at /boot/efi in fstab), p2 the
# pool 0:-10m (bf00); zpool create -f -o ashift=12 -O compression=lz4
# -O acltype=posixacl -O xattr=sa -O relatime=on -o autotrim=on
# -o compatibility=openzfs-2.3-linux -m none zroot; zroot/ROOT
# (mountpoint=none), zroot/ROOT/void (mountpoint=/, canmount=noauto),
# zroot/home (/home), bootfs=zroot/ROOT/void, no zpool.cache.  The guide's
# fixed host ID, zgenhostid 0x00bab10c (never the host's own /etc/hostid),
# in /etc/hostid: ZFSBootMenu's release image hands every BE
# spl.spl_hostid=0x00bab10c, so the guest's hostid then says the same.
# Void's base-system with its default kernel (linux) and its headers, zfs
# (DKMS), dracut with the guide's zol.conf, efibootmgr;
# rc.conf's keymap and clock, glibc's en_US.UTF-8 locale.
# org.zfsbootmenu:commandline="loglevel=4 console=tty0 console=ttyS0,115200n8"
# on zroot/ROOT, so every BE's kernel talks on the serial port.  ZBM's image
# at EFI/ZBM/VMLINUZ.EFI and VMLINUZ-BACKUP.EFI, its embedded command line
# with console=ttyS0,115200n8 zbm.timeout=5 appended by zbm-kcl.
#
# The second BE: once that install is complete, zroot/ROOT/void is
# snapshotted as @pre-mainline and cloned as zroot/ROOT/void-SERIES, SERIES
# Void's default kernel series, the one in it (void-6.18 on 2026-11-14)
# (mountpoint=/, canmount=noauto, its own org.zfsbootmenu:commandline
# "%{parent} bko.be=void-SERIES"); then a newer kernel series (and its
# headers) goes into zroot/ROOT/void only: linux-mainline when the guest's
# OpenZFS builds for it (its META's Linux-Maximum), else the newest series
# it builds for (linux7.2 on 2026-11-14, mainline being 7.3).  So ZBM boots
# the newer kernel there and the default one in the clone.  /etc/bko-be
# names each BE.
#
# Plus what every guest has: root's password (new each build) and SSH key,
# the user tester with root's password, sshd's host keys, dhcpcd, sshd and
# agetty-ttyS0 enabled, the ARC capped at 512 MiB.
#
# NVRAM: the guide's two efibootmgr entries, "ZFSBootMenu (Backup)" and then
# "ZFSBootMenu" (\EFI\ZBM\...; the last made comes first in BootOrder), in
# the build's own OVMF variables, uefi-vars.fd.  They are made by efibootmgr
# in the guest, as the guide makes them on the machine itself: the first
# boot (vm-run --uefi --no-secure-boot --uefi-vars uefi-vars.fd) finds ZBM
# through a copy at the fallback path EFI/BOOT/BOOTX64.EFI, ZBM boots
# zroot/ROOT/void, and a one-shot hook in rc.local there runs efibootmgr,
# removes that copy and powers off.  So the guest has no fallback loader
# (the guide makes none), and a fresh variable store doesn't boot it: it
# boots on uefi-vars.fd, which vm-run-options names (vm-run --guest-vars:
# with --ram or --snapshot on a copy, so the file stays as built).  A second
# boot, so and under --snapshot, checks that it comes up alone through the
# NVRAM entry, in BE void with the newer kernel, on SSH.
#
# Results, in WORK_DIR: NAME.qcow2 (16 GiB, sparse, snapshot
# "fresh-install"), uefi-vars.fd (for OVMF_CODE_4M.fd on a plain q35
# machine, vm-run --no-secure-boot's), root-password, id_ed25519 and .pub,
# vm-run-options (--uefi --no-secure-boot --guest-vars uefi-vars.fd),
# zfs-passphrase (--encrypt only), install.log
# (xbps's and the tools' output), firstboot-serial.log, check-serial.log and
# first-boot.txt (what the check boot showed).
#
# Needs: root; OpenZFS (zpool, zfs, zgenhostid); bsdtar (it keeps the
# tarball's file capabilities), minisign, signify-openbsd, objcopy and
# objdump (binutils, for zbm-kcl), getcap, qemu-img, KVM and OVMF
# (vm-run --uefi), losetup, sgdisk, mkfs.vfat, blkid, openssl, ssh-keygen,
# python3 (to pick a free port); network access for xbps.  BKO_XBPS_CACHE,
# a directory on the host, is bind-mounted as the chroot's xbps cache, so a
# rebuild downloads only what changed (xbps checks each package's signature
# all the same); unset, the cache is the guest's own, emptied before the
# snapshot and at the end.  SSH_PORT picks
# the boots' forwarded port; unset, a free one is picked.  sudo drops both
# from the environment: pass them as sudo SSH_PORT=N BKO_XBPS_CACHE=DIR.

set -euo pipefail
export PATH=$PATH:/usr/sbin:/sbin

DISK_SIZE=16G
POOL=zroot
MEMORY=2048            # enough: boot, and a DKMS build of zfs with 1 GiB left (Z1)
FIRST_BOOT_TIMEOUT=1200
CHECK_BOOT_TIMEOUT=600
MIRROR=https://repo-default.voidlinux.org/current
VOID_DATE=20250202
# Void's key for that date ("only valid for images with date 20250202").
# Keep it the same as make-bootstrap.sh's.
VOID_KEY=RWT5+SoQD+dWTeOdNuc4Q/jq2+3+jpql7+JJp4WukkxTdpsZlk2EGuPj
ROOTFS=void-x86_64-ROOTFS-$VOID_DATE.tar.xz
ROOTFS_SHA256=3f48e6673ac5907a897d913c97eb96edbfb230162731b4016562c51b3b8f1876
ZBM_EFI=zfsbootmenu-release-x86_64-v3.1.0-linux6.12.EFI
ZBM_EFI_SHA256=131d55c7b385df8cd319d267b6aa15d97f8c32c94f5149bd906556715fadf4e7
# ZBM's signing key: releng/keys/zfsbootmenu.pub in zbm-dev/zfsbootmenu at
# v3.1.0 (the same on master).
ZBM_KEY=RWSUawlGznCLF8NxEb1EyrGpP2eIR9l7JIZgLzCdv+64uBDvfXNJm+cF
ZBM_KCL_SHA256=16edae3eee5df9a0b133734bc4d8a8cb68ca73ac30f98cb21cea3212b051ff01
ZBM_KCL_ADD=(console=ttyS0,115200n8 zbm.timeout=5)
ZBM_COMMANDLINE="loglevel=4 console=tty0 console=ttyS0,115200n8"

here=$(dirname "$(realpath "$0")")
die() { echo "make-void-zfs: $*" >&2; exit 1; }

inputs="" encrypt=no args=()
while [[ $# -gt 0 ]]; do
    case $1 in
        --inputs) inputs=$(realpath "$2"); shift 2 ;;
        --encrypt) encrypt=yes; shift ;;
        -*) die "unknown option $1" ;;
        *) args+=("$1"); shift ;;
    esac
done
[[ ${#args[@]} -eq 2 ]] || die "usage: $0 [--inputs DIR] WORK_DIR NAME [--encrypt]"
name=${args[1]}
[[ $name =~ ^[a-z][a-z0-9-]*$ ]] || die "NAME is a host name: lower-case letters, digits and -"
[[ $EUID -eq 0 ]] || die "must run as root"
for t in zpool zfs zgenhostid bsdtar minisign signify-openbsd objcopy objdump getcap qemu-img qemu-system-x86_64 \
    losetup sgdisk mkfs.vfat blkid udevadm openssl ssh-keygen python3; do
    command -v "$t" >/dev/null || die "needs $t"
done
[[ -f /usr/share/OVMF/OVMF_CODE_4M.fd && -f /usr/share/OVMF/OVMF_VARS_4M.fd ]] || die "needs OVMF (/usr/share/OVMF/OVMF_CODE_4M.fd)"
mkdir -p "${args[0]}"
cd "${args[0]}"
work_dir=$(pwd)
[[ -n $inputs ]] || inputs=$work_dir
disk=$work_dir/$name.qcow2
[[ -e $disk ]] && die "$disk already exists; move it away to rebuild"
owner=${SUDO_USER:-root}
log=$work_dir/install.log
: >"$log"

# --- Check the inputs -----------------------------------------------------------------
for f in "$ROOTFS" sha256sum.txt sha256sum.sig "$ZBM_EFI" sha256.txt sha256.sig zbm-kcl zfsbootmenu.pub; do
    [[ -f $inputs/$f ]] || die "no $inputs/$f (the inputs are never downloaded here)"
done
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT        # cleanup() below, once there is more to undo
minisign -q -V -P "$VOID_KEY" -m "$inputs/sha256sum.txt" -x "$inputs/sha256sum.sig" ||
    die "Void's sha256sum.txt is not signed by its key for $VOID_DATE"
grep -qxF "SHA256 ($ROOTFS) = $ROOTFS_SHA256" "$inputs/sha256sum.txt" || die "Void's sha256sum.txt doesn't give $ROOTFS the pinned SHA-256"
printf 'untrusted comment: ZFSBootMenu signing key public key\n%s\n' "$ZBM_KEY" >"$scratch/zfsbootmenu.pub"
grep -qxF "$ZBM_KEY" "$inputs/zfsbootmenu.pub" || die "$inputs/zfsbootmenu.pub is not the pinned key"
signify-openbsd -V -q -p "$scratch/zfsbootmenu.pub" -x "$inputs/sha256.sig" -m "$inputs/sha256.txt" ||
    die "ZBM's sha256.txt is not signed by its release key"
grep -qxF "SHA256 ($ZBM_EFI) = $ZBM_EFI_SHA256" "$inputs/sha256.txt" || die "ZBM's sha256.txt doesn't give $ZBM_EFI the pinned SHA-256"
sha256_is() { [[ $(sha256sum <"$1" | cut -d' ' -f1) == "$2" ]] || die "$1 doesn't have the pinned SHA-256 $2"; }
sha256_is "$inputs/$ROOTFS" "$ROOTFS_SHA256"
sha256_is "$inputs/$ZBM_EFI" "$ZBM_EFI_SHA256"
sha256_is "$inputs/zbm-kcl" "$ZBM_KCL_SHA256"

# ZBM's image with the serial console and a 5 s countdown in its embedded
# command line, made by zbm-kcl (bash, from its pinned copy) on a copy.
# objcopy stamps the PE header with the time (and its checksum with it)
# unless SOURCE_DATE_EPOCH says which: the release's BUILD_ID, 2026-01-01,
# so every build has the same image.
export SOURCE_DATE_EPOCH=1767225600
cp "$inputs/$ZBM_EFI" "$scratch/in.efi"
cp "$inputs/zbm-kcl" "$scratch/zbm-kcl"
kcl_add=(); for a in "${ZBM_KCL_ADD[@]}"; do kcl_add+=(-a "$a"); done
TMPDIR=$scratch bash "$scratch/zbm-kcl" "${kcl_add[@]}" -o "$scratch/VMLINUZ.EFI" "$scratch/in.efi" >>"$log" 2>&1 ||
    die "zbm-kcl failed; see $log"
zbm_kcl=$(TMPDIR=$scratch bash "$scratch/zbm-kcl" "$scratch/VMLINUZ.EFI") || die "zbm-kcl can't read the new image"
for a in "${ZBM_KCL_ADD[@]}"; do [[ " $zbm_kcl " == *" $a "* ]] || die "the image's command line lacks $a: $zbm_kcl"; done
unset SOURCE_DATE_EPOCH
echo "ZBM's embedded command line: $zbm_kcl; the image's SHA-256 $(sha256sum <"$scratch/VMLINUZ.EFI" | cut -d' ' -f1)"

# --- Disk and pool --------------------------------------------------------------------
temp=bkovz$$                       # the pool's name on this host while it's built
root=/mnt/bko-void-zfs-$$          # its altroot: the new system
be2_mnt=/mnt/bko-void-zfs-$$-be    # the second BE, for its marker
raw=$work_dir/.$name.raw
dev="" imported=no
unmount_all() {
    ! mountpoint -q "$be2_mnt" 2>/dev/null || umount "$be2_mnt" || umount -l "$be2_mnt"
    ! mountpoint -q "$root" || umount -R "$root" || umount -R -l "$root"
}
cleanup() {
    set +e
    unmount_all
    if [[ $imported == yes ]]; then zpool export "$temp" || zpool export -f "$temp"; fi
    [[ -n $dev ]] && losetup -d "$dev"
    rm -f "$raw"
    rmdir "$root" "$be2_mnt" 2>/dev/null
    rm -rf "$scratch"
}
trap cleanup EXIT

truncate -s "$DISK_SIZE" "$raw"
dev=$(losetup --find --show --partscan "$raw")
# The guide's partitions.
sgdisk --zap-all "$dev" >>"$log"
sgdisk -n "1:1m:+512m" -t "1:ef00" "$dev" >>"$log"
sgdisk -n "2:0:-10m" -t "2:bf00" "$dev" >>"$log"
partprobe "$dev" 2>/dev/null || blockdev --rereadpt "$dev" 2>/dev/null || true
udevadm settle
for _ in 1 2 3 4 5; do [[ -e ${dev}p2 ]] && break; sleep 1; done
[[ -b ${dev}p1 && -b ${dev}p2 ]] || die "no partitions on $dev"
# The guide's host ID (zgenhostid -f 0x00bab10c), written to scratch, never
# to this host's /etc/hostid.
zgenhostid -o "$scratch/hostid" 0x00bab10c
[[ $(od -An -tx1 "$scratch/hostid" | tr -d ' \n') == 0cb1ba00 ]] || die "zgenhostid didn't write 0x00bab10c"
mkdir -p "$root"
# --encrypt: the guide's encryption, with a new passphrase, its key loaded
# from a copy in scratch while the pool is built (the guide's
# keylocation, /etc/zfs/zroot.key, would be this host's file): keylocation
# is set to the guide's once the pool exists.
enc_opts=()
if [[ $encrypt == yes ]]; then
    rm -f zfs-passphrase
    (umask 077; openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 24 >zfs-passphrase)
    [[ $(cat zfs-passphrase) =~ ^[A-Za-z0-9]{24}$ ]] || die "could not make a passphrase"
    (umask 077; cp zfs-passphrase "$scratch/zroot.key")
    enc_opts=(-O encryption=aes-256-gcm -O keyformat=passphrase -O "keylocation=file://$scratch/zroot.key")
fi
# The guide's pool, as written; -t and -R as the build's own.
zpool create -f -t "$temp" -R "$root" -o cachefile=none -o ashift=12 \
    -O compression=lz4 -O acltype=posixacl -O xattr=sa -O relatime=on "${enc_opts[@]}" \
    -o autotrim=on -o compatibility=openzfs-2.3-linux -m none "$POOL" "${dev}p2"
imported=yes
if [[ $encrypt == yes ]]; then
    zfs set keylocation=file:///etc/zfs/zroot.key "$temp"
    # The guide's keysource: the BE whose /etc/zfs/zroot.key ZFSBootMenu
    # caches once it has the pool open (the pool's own name, not $temp).
    zfs set "org.zfsbootmenu:keysource=$POOL/ROOT/void" "$temp"
fi
zfs create -o mountpoint=none "$temp/ROOT"
zfs create -o mountpoint=/ -o canmount=noauto "$temp/ROOT/void"
zpool set bootfs="$temp/ROOT/void" "$temp"
# / first: zroot/home mounts itself at once, and must land inside it.
zfs mount "$temp/ROOT/void"
zfs create -o mountpoint=/home "$temp/home"
mountpoint -q "$root/home" || zfs mount "$temp/home"
# Every BE's kernel command line, set from the host (the guide's "quiet" made
# the serial console).
zfs set org.zfsbootmenu:commandline="$ZBM_COMMANDLINE" "$temp/ROOT"

# --- Void -----------------------------------------------------------------------------
echo "Unpacking $ROOTFS"
# --numeric-owner: Void's IDs, not the host's for the same names.
bsdtar -xpf "$inputs/$ROOTFS" --numeric-owner -C "$root"
grep -qE '^ID="?void"?$' "$root/etc/os-release" || die "/etc/os-release doesn't say Void"
have_caps() {
    [[ $(getcap "$root/usr/bin/iputils-ping") == *cap_net_raw* ]] ||
        die "/usr/bin/iputils-ping has no file capabilities ($1)"
}
have_caps "after the unpack"
install -m 644 "$scratch/hostid" "$root/etc/hostid"
# A /dev of its own (as make-chimera-zfs.sh's): no /dev/zfs, no loop device,
# so nothing in the chroot reaches the host's pool or disks.
mount -t tmpfs -o mode=755,nosuid tmpfs "$root/dev"
copy_node() {
    mknod -m "$(stat -c %a "$1")" "$root$1" c "$((16#$(stat -c %t "$1")))" "$((16#$(stat -c %T "$1")))"
}
for n in /dev/null /dev/zero /dev/full /dev/random /dev/urandom /dev/tty; do copy_node "$n"; done
mkdir "$root/dev/pts" "$root/dev/shm"
mount -t devpts -o newinstance,ptmxmode=0666,mode=620 devpts "$root/dev/pts"
ln -s pts/ptmx "$root/dev/ptmx"
ln -s /proc/self/fd "$root/dev/fd"
ln -s /proc/self/fd/0 "$root/dev/stdin"; ln -s /proc/self/fd/1 "$root/dev/stdout"; ln -s /proc/self/fd/2 "$root/dev/stderr"
mount -t proc proc "$root/proc"
mount --bind "$root/proc/sys" "$root/proc/sys"
mount -o remount,bind,ro "$root/proc/sys"
mount --bind /sys "$root/sys"
mount -o remount,bind,ro "$root/sys"
mount -t tmpfs -o mode=755 tmpfs "$root/run"
mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs "$root/tmp"
if [[ -n ${BKO_XBPS_CACHE:-} ]]; then
    mkdir -p "$BKO_XBPS_CACHE" "$root/var/cache/xbps"
    mount --bind "$BKO_XBPS_CACHE" "$root/var/cache/xbps"
fi
resolv() { rm -f "$root/etc/resolv.conf"; [[ $1 == off ]] || cp -L /etc/resolv.conf "$root/etc/resolv.conf"; }
resolv on
in_root() { chroot "$root" /usr/bin/env -i PATH=/usr/bin:/usr/sbin HOME=/root TERM=dumb LANG=C "$@"; }
# xbps CMD...: up to 3 tries, a minute apart, for the network's hiccups (a
# resolver failure has stopped a download); xbps downloads every package
# before it changes anything.
xbps() {
    local try
    for try in 1 2 3; do
        in_root "$@" >>"$log" 2>&1 && return 0
        echo "note: $* failed (try $try); see $log" >&2
        sleep 60
    done
    die "$* failed; see $log"
}
# xbps_sync: the repository's index, fetched for sure: xbps-install -S goes on
# with the tarball's own (stale) index when the fetch fails, and exits 0.
xbps_sync() {
    local try out
    for try in 1 2 3 4 5; do
        out=$(in_root xbps-install -S 2>&1) || true
        printf '%s\n' "$out" >>"$log"
        grep -q 'ERROR' <<<"$out" || return 0
        echo "note: xbps-install -S failed (try $try): $(grep ERROR <<<"$out" | head -n 1)" >&2
        sleep 60
    done
    die "xbps-install -S can't fetch the repository's index; see $log"
}

# The guide's chroot configuration, before the kernel's initramfs is made.
cat >>"$root/etc/rc.conf" <<'EOF'
KEYMAP="us"
HARDWARECLOCK="UTC"
EOF
ln -sf /usr/share/zoneinfo/UTC "$root/etc/localtime"
echo "en_US.UTF-8 UTF-8" >>"$root/etc/default/libc-locales"
mkdir -p "$root/etc/dracut.conf.d"
cat >"$root/etc/dracut.conf.d/zol.conf" <<'EOF'
nofsck="yes"
add_dracutmodules+=" zfs "
omit_dracutmodules+=" btrfs "
EOF
if [[ $encrypt == yes ]]; then
    # The guide's key file (mode 000), in every initramfs: the BE's
    # initramfs opens the pool with it, after ZFSBootMenu's prompt.  The
    # passphrase's bytes, no newline, as zfs-passphrase holds them.
    mkdir -p "$root/etc/zfs"
    install -m 000 "$scratch/zroot.key" "$root/etc/zfs/zroot.key"
    echo 'install_items+=" /etc/zfs/zroot.key "' >>"$root/etc/dracut.conf.d/zol.conf"
fi
# The ARC capped for the guest's memory (dracut puts modprobe.d in the initramfs).
mkdir -p "$root/etc/modprobe.d"
echo "options zfs zfs_arc_max=536870912" >"$root/etc/modprobe.d/zfs.conf"
echo "repository=$MIRROR" >"$root/etc/xbps.d/00-repository-main.conf"

echo "xbps: xbps itself, then the system, base-system, the kernel's headers, zfs, efibootmgr"
xbps_sync
xbps xbps-install -uy xbps
xbps_sync
xbps xbps-install -yu
xbps xbps-install -y base-system
in_root xbps-remove -y base-container-full >>"$log" 2>&1 || true
xbps xbps-reconfigure -f glibc-locales
# Void's default kernel series: what the linux metapackage depends on.
kpkg=$(in_root xbps-query -x linux | sed -n 's/^\(linux[0-9][0-9.]*\)[<>=].*/\1/p' | head -n 1)
[[ -n $kpkg ]] || die "can't tell which kernel series the linux package is"
BE2=void-${kpkg#linux}      # the clone: the BE with Void's default kernel alone
in_root xbps-query "$kpkg-headers" >/dev/null 2>&1 && echo "note: $kpkg-headers came with base-system" ||
    echo "note: base-system has no $kpkg-headers; installing linux-headers (zfs's DKMS needs them)"
xbps xbps-install -y linux-headers zfs efibootmgr
kvers() { ls "$root/usr/lib/modules"; }
kver=$(kvers | sort -V | tail -n 1)
[[ $(kvers | wc -l) == 1 ]] || die "more than one kernel after base-system: $(kvers | tr '\n' ' ')"
has_zfs_ko() { [[ -n $(find "$root/usr/lib/modules/$1" -name 'zfs.ko*' -print -quit) ]]; }
has_zfs_ko "$kver" || die "DKMS built no zfs module for $kver; see $log"
# The kernel's hooks again (DKMS, dracut), now with zfs: its initramfs.
xbps xbps-reconfigure -f "$kpkg"
# check_initramfs KVER: the initramfs has OpenZFS, the guest's host ID and
# virtio's disk driver, and no cache file.
check_initramfs() {
    local img=/boot/initramfs-$1.img list=$scratch/initrd-$1.list
    [[ -f $root$img ]] || die "no $img"
    in_root lsinitrd "$img" >"$list" 2>>"$log" || die "lsinitrd $img failed; see $log"
    grep -q '/zfs\.ko' "$list" || die "$img has no zfs module"
    grep -q ' etc/hostid$' "$list" || die "$img has no /etc/hostid"
    grep -q 'virtio_blk\.ko' "$list" || grep -qs 'virtio_blk' "$root/usr/lib/modules/$1/modules.builtin" ||
        die "$img has no virtio_blk"
    ! grep -q 'zpool\.cache' "$list" || die "$img has a zpool.cache"
    [[ $encrypt == no ]] || grep -q ' etc/zfs/zroot\.key$' "$list" || die "$img has no /etc/zfs/zroot.key"
}
check_initramfs "$kver"
echo "Kernel $kpkg ($kver); OpenZFS in the guest: $(in_root xbps-query -p pkgver zfs)"

# Identity: host name, root's password and SSH key, tester, sshd's host keys.
echo "$name" >"$root/etc/hostname"
printf '127.0.0.1\tlocalhost\n::1\t\tlocalhost\n127.0.1.1\t%s\n' "$name" >"$root/etc/hosts"
umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$name test guest" -f id_ed25519
umask 022
in_root sh -c 'id tester >/dev/null 2>&1 || useradd -m -s /bin/bash -c "Test user" -G wheel tester'
printf 'root:%s\ntester:%s\n' "$hash" "$hash" | in_root chpasswd -e
install -d -m 700 "$root/root/.ssh"
install -m 600 id_ed25519.pub "$root/root/.ssh/authorized_keys"
rm -f "$root"/etc/ssh/ssh_host_*
in_root ssh-keygen -A >>"$log"
for s in dhcpcd sshd agetty-ttyS0; do
    [[ -d $root/etc/sv/$s ]] || die "no runit service $s"
    ln -sfn "/etc/sv/$s" "$root/etc/runit/runsvdir/default/$s"
done
echo "--uefi --no-secure-boot --guest-vars uefi-vars.fd" >vm-run-options       # vm-run's 2048 MB is MEMORY

# The ESP, as the guide: FAT32 at /boot/efi, ZBM's image and its backup.
mkfs.vfat -F32 "${dev}p1" >>"$log"
echo "UUID=$(blkid -s UUID -o value "${dev}p1") /boot/efi vfat defaults 0 0" >>"$root/etc/fstab"
mkdir -p "$root/boot/efi"
mount -t vfat "${dev}p1" "$root/boot/efi"
mkdir -p "$root/boot/efi/EFI/ZBM"
cp "$scratch/VMLINUZ.EFI" "$root/boot/efi/EFI/ZBM/VMLINUZ.EFI"
cp "$root/boot/efi/EFI/ZBM/VMLINUZ.EFI" "$root/boot/efi/EFI/ZBM/VMLINUZ-BACKUP.EFI"

# --- The second BE --------------------------------------------------------------------
echo "$name's BEs: void and its clone $BE2, before the newer kernel"
echo void >"$root/etc/bko-be"
# The package cache (xbps-remove -O keeps the current packages): in neither
# BE.  Never the host's BKO_XBPS_CACHE, which is only mounted there.
empty_cache() { [[ -n ${BKO_XBPS_CACHE:-} ]] || find "$root/var/cache/xbps" -mindepth 1 -delete; }
empty_cache
resolv off
sync
zfs snapshot "$temp/ROOT/void@pre-mainline"
zfs clone -o mountpoint=/ -o canmount=noauto -o "org.zfsbootmenu:commandline=%{parent} bko.be=$BE2" \
    "$temp/ROOT/void@pre-mainline" "$temp/ROOT/$BE2"
mkdir -p "$be2_mnt"
mount -t zfs -o zfsutil "$temp/ROOT/$BE2" "$be2_mnt"
echo "$BE2" >"$be2_mnt/etc/bko-be"
sync
umount "$be2_mnt"
rmdir "$be2_mnt"

# The newer kernel: linux-mainline when the guest's OpenZFS builds for its
# series (its META's Linux-Maximum), else the newest series in the
# repository that it does build for, as a ZFS user of Void must pick (on
# 2026-11-14: mainline 7.3, zfs 2.4.4's maximum 7.2, so linux7.2).
resolv on
zmax=$(sed -n 's/^Linux-Maximum: *//p' "$root"/usr/src/zfs-*/META | head -n 1)
[[ $zmax =~ ^[0-9]+\.[0-9]+$ ]] || die "can't read Linux-Maximum from the guest's /usr/src/zfs-*/META"
series_le() { [[ $(printf '%s\n' "$1" "$2" | sort -V | head -n 1) == "$1" ]]; }   # A <= B
mlpkg=$(in_root xbps-query -R -x linux-mainline | sed -n 's/^\(linux[0-9][0-9.]*\)[<>=].*/\1/p' | head -n 1)
if [[ -n $mlpkg ]] && series_le "${mlpkg#linux}" "$zmax"; then
    mpkgs=(linux-mainline linux-mainline-headers) mkpkg=$mlpkg
else
    mkpkg=$(in_root xbps-query -Rs linux | sed -n 's/^\[.\] \(linux[0-9][0-9]*\.[0-9][0-9]*\)-[0-9].*/\1/p' |
        while read -r k; do if series_le "${k#linux}" "$zmax"; then echo "$k"; fi; done | sort -V | tail -n 1)
    [[ -n $mkpkg ]] || die "no kernel series in the repository that zfs (Linux-Maximum $zmax) builds for"
    mpkgs=("$mkpkg" "$mkpkg-headers")
fi
[[ $mkpkg != "$kpkg" ]] && ! series_le "${mkpkg#linux}" "${kpkg#linux}" ||
    die "no kernel series newer than $kpkg that zfs (Linux-Maximum $zmax) builds for (linux-mainline is ${mlpkg:-?})"
echo "xbps: ${mpkgs[*]} (linux-mainline is ${mlpkg:-?}, zfs's Linux-Maximum $zmax), in void only"
xbps xbps-install -y "${mpkgs[@]}"
mkver=$(kvers | grep -vxF "$kver" | sort -V | tail -n 1)
[[ -n $mkver && $(kvers | wc -l) == 2 ]] || die "${mpkgs[0]} brought no second kernel: $(kvers | tr '\n' ' ')"
[[ $(printf '%s\n' "$kver" "$mkver" | sort -V | tail -n 1) == "$mkver" ]] || die "$mkver isn't newer than $kver"
has_zfs_ko "$mkver" || die "DKMS built no zfs module for $mkver; see $log"
check_initramfs "$mkver"
check_initramfs "$kver"
echo "Newer kernel: $mkpkg ($mkver)"
empty_cache
have_caps "at the end"

# The first boot (below): the guide's NVRAM entries, made by efibootmgr in
# the guest; ZBM reached through a copy at the fallback path for that boot
# only.  rc.local runs in runit's stage 2, before the services.
mkdir -p "$root/boot/efi/EFI/BOOT"
cp "$root/boot/efi/EFI/ZBM/VMLINUZ.EFI" "$root/boot/efi/EFI/BOOT/BOOTX64.EFI"
cp -p "$root/etc/rc.local" "$root/etc/rc.local.bko-orig"
echo '[ -x /etc/bko-first-boot ] && /etc/bko-first-boot' >>"$root/etc/rc.local"
cat >"$root/etc/bko-first-boot" <<'EOF'
#!/bin/sh
# make-void-zfs.sh's first boot: the guide's NVRAM entries, then the
# fallback copy of ZBM goes, this goes, and the guest powers off.  Powered
# off either way: the build looks for "done" on the console.
exec >/dev/console 2>&1
off() { mv -f /etc/rc.local.bko-orig /etc/rc.local; rm -f /etc/bko-first-boot; sync; poweroff; exit 0; }
fail() { echo "bko-first-boot: failed: $*"; off; }
echo "bko-first-boot: start: BE $(cat /etc/bko-be), $(uname -r)"
echo "bko-first-boot: cmdline: $(cat /proc/cmdline)"
mountpoint -q /boot/efi || mount /boot/efi || fail "no /boot/efi"
efibootmgr -c -d /dev/vda -p 1 -L "ZFSBootMenu (Backup)" -l '\EFI\ZBM\VMLINUZ-BACKUP.EFI' >/dev/null || fail "efibootmgr (Backup)"
efibootmgr -c -d /dev/vda -p 1 -L "ZFSBootMenu" -l '\EFI\ZBM\VMLINUZ.EFI' >/dev/null || fail "efibootmgr"
rm -rf /boot/efi/EFI/BOOT || fail "rm EFI/BOOT"
efibootmgr -v | sed 's/^/bko-first-boot: /'
echo "bko-first-boot: done"
off
EOF
chmod 755 "$root/etc/bko-first-boot"

resolv off
sync
unmount_all
zpool export "$temp"
imported=no
losetup -d "$dev"
dev=""
qemu-img convert -f raw -O qcow2 "$raw" "$disk"
rm -f "$raw"

# --- First boot: the NVRAM entries ----------------------------------------------------
port=${SSH_PORT:-$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')}
# vm_run LOG: sets vm to vm-run with its serial console in LOG; with
# --encrypt, under console-boot.py, which types zfs-passphrase at
# ZFSBootMenu's prompt (zfs load-key's "Enter passphrase for 'zroot':").
vm_run() {
    if [[ $encrypt == yes ]]; then
        vm=(python3 -I "$here/console-boot.py" "$1" "$work_dir/zfs-passphrase" -- "$here/vm-run")
    else
        vm=("$here/vm-run" --serial-log "$1")
    fi
}
echo "First boot: the NVRAM entries; the console goes to $work_dir/firstboot-serial.log"
rm -f firstboot-serial.log uefi-vars.fd
cp /usr/share/OVMF/OVMF_VARS_4M.fd uefi-vars.fd
vm_run firstboot-serial.log
SSH_PORT=$port timeout "$FIRST_BOOT_TIMEOUT" "${vm[@]}" --uefi --no-secure-boot --uefi-vars "$work_dir/uefi-vars.fd" \
    --memory "$MEMORY" "$disk" -- -no-reboot </dev/null ||
    die "the first boot failed or timed out; see firstboot-serial.log"
grep -aq 'bko-first-boot: done' firstboot-serial.log || die "the first boot didn't finish; see firstboot-serial.log"

# --- Check boot: alone through the NVRAM entry, as vm-run-options boots it -------------
echo "Check boot (vm-run-options' --guest-vars, --snapshot: on a copy of uefi-vars.fd); the console goes to $work_dir/check-serial.log"
rm -f check-serial.log first-boot.txt
read -ra opts <vm-run-options
vm_run check-serial.log
SSH_PORT=$port "${vm[@]}" "${opts[@]}" --snapshot \
    --memory "$MEMORY" "$disk" </dev/null &
qemu_pid=$!
ssh_root=(ssh -q -i id_ed25519 -p "$port" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
    -o BatchMode=yes -o ConnectTimeout=5 root@127.0.0.1)
up=no
for _ in $(seq $((CHECK_BOOT_TIMEOUT / 5))); do
    kill -0 "$qemu_pid" 2>/dev/null || break
    if "${ssh_root[@]}" true </dev/null 2>/dev/null; then up=yes; break; fi
    sleep 5
done
[[ $up == yes ]] || { kill "$qemu_pid" 2>/dev/null; wait "$qemu_pid" 2>/dev/null; die "the check boot never answered on SSH; see check-serial.log"; }
"${ssh_root[@]}" 'set -x; cat /etc/bko-be; uname -r; cat /proc/cmdline; hostname; od -An -tx1 /etc/hostid; hostid
    efibootmgr -v; ls -laR /boot/efi; ls -l /boot; findmnt -rno TARGET,SOURCE,FSTYPE; cat /etc/fstab
    zfs version; zpool status; zpool get bootfs,compatibility,cachefile,autotrim,ashift zroot
    zfs list -t all -o name,used,mountpoint,canmount,mounted,origin
    zfs get -r -s local,inherited -o name,property,value,source all zroot
    zfs get -H -r -t filesystem -o name,property,value encryption,keyformat,keylocation,encryptionroot,keystatus zroot
    ls -la /etc/zfs; dkms status; for p in zfs linux linux-headers linux-mainline linux6.18 linux7.2 dracut efibootmgr base-system; do xbps-query -p pkgver $p; done
    for i in /boot/initramfs-*.img; do echo "== $i"; lsinitrd "$i" | grep -E "zfs\.ko|etc/hostid|zpool\.cache|etc/zfs"; done
    cat /sys/module/zfs/parameters/zfs_arc_max; free -m; getcap /usr/bin/iputils-ping; id tester
    sv status /var/service/*' </dev/null >first-boot.txt 2>&1 || true
"${ssh_root[@]}" 'sync; poweroff' </dev/null 2>/dev/null || true
for _ in $(seq 24); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 5; done
kill "$qemu_pid" 2>/dev/null || true
wait "$qemu_pid" 2>/dev/null || true
fact() { awk -v h="+ $1" '$0 == h { f = 1; next } f && /^\+ / { exit } f' first-boot.txt; }   # CMD's output there
[[ $(fact 'cat /etc/bko-be') == void ]] || die "the check boot isn't in BE void; see first-boot.txt"
[[ $(fact hostid) == 00bab10c ]] || die "the check boot's hostid isn't 00bab10c; see first-boot.txt"
[[ $(fact 'uname -r') == "$mkver" ]] || die "the check boot didn't run the newer kernel ($mkver); see first-boot.txt"
grep -Eq "(^| )root=zfs:$POOL/ROOT/void( |$)" first-boot.txt || die "/proc/cmdline has no root=zfs:$POOL/ROOT/void"
current=$(awk '$1 == "BootCurrent:" { print $2 }' first-boot.txt)
grep -E "^Boot$current\\*? ZFSBootMenu[[:space:]]" first-boot.txt | grep -qiF 'VMLINUZ.EFI' ||
    die "the check boot didn't come through the \"ZFSBootMenu\" entry (BootCurrent ${current:-none}); see first-boot.txt"
! grep -q '^/boot/efi/EFI/BOOT' <(fact 'ls -laR /boot/efi') || die "the ESP still has EFI/BOOT"
if [[ $encrypt == yes ]]; then
    # Every filesystem in the pool under the one encryption root, its key
    # loaded; the guide's keylocation and keysource; and one prompt only,
    # ZFSBootMenu's: the BE's initramfs opened the pool with its key file.
    encfact=$(fact 'zfs get -H -r -t filesystem -o name,property,value encryption,keyformat,keylocation,encryptionroot,keystatus zroot')
    for ds in "$POOL" "$POOL/ROOT" "$POOL/ROOT/void" "$POOL/ROOT/$BE2" "$POOL/home"; do
        for pv in encryption=aes-256-gcm keyformat=passphrase encryptionroot="$POOL" keystatus=available; do
            grep -qxF "$ds	${pv%%=*}	${pv#*=}" <<<"$encfact" || die "the check boot's $ds hasn't ${pv}; see first-boot.txt"
        done
    done
    grep -qxF "$POOL	keylocation	file:///etc/zfs/zroot.key" <<<"$encfact" || die "$POOL's keylocation isn't file:///etc/zfs/zroot.key; see first-boot.txt"
    grep -Eq "^$POOL +org\.zfsbootmenu:keysource +$POOL/ROOT/void +local$" first-boot.txt ||
        die "$POOL has no local org.zfsbootmenu:keysource=$POOL/ROOT/void; see first-boot.txt"
    for log in firstboot-serial.log check-serial.log; do
        n=$(sed 's/\x1b\[[0-9;?]*[A-Za-z]//g' "$log" | grep -aci 'enter passphrase' || true)
        [[ $n == 1 ]] || die "$log has $n passphrase prompts, not ZFSBootMenu's one"
    done
fi
echo "Check boot: BE void, $(fact 'uname -r'), through Boot$current"

qemu-img snapshot -c fresh-install "$disk"
chown "$owner": "$work_dir" "$disk" uefi-vars.fd root-password id_ed25519 id_ed25519.pub vm-run-options install.log \
    firstboot-serial.log check-serial.log first-boot.txt
[[ $encrypt == no ]] || chown "$owner": zfs-passphrase
echo "Done: $disk ($(du -h "$disk" | cut -f1) on disk), snapshot \"fresh-install\""
