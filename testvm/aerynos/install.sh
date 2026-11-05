#!/bin/bash
# AerynOS test guest for BackOtter: the install, done the way AerynOS's own
# installer (lichen-installer's backend: install_service.rs) does it,
# unattended, onto BackOtter's layout instead of its whole-disk one (which
# wants 30 GB and has no swap): GPT with an EFI system partition (FAT32) as
# the one boot partition, mounted at /efi, swap, and / ($ROOT_FS).  moss
# installs the system from a system-model.kdl into the mounted disk, and its
# blsforme writes systemd-boot and the kernels to the ESP.
#
# make-aerynos.sh fills in the @...@ settings and serves this file over HTTP
# to the AerynOS ISO's live system, which fetches and runs it as root
# (systemd.run= on the kernel command line).  Its output is copied to the
# serial console, and it powers the VM off when done, printing
# BACKOTTER-INSTALL-OK or BACKOTTER-INSTALL-FAILED first.

set -euo pipefail
exec > >(tee /dev/ttyS0) 2>&1
trap 'echo "BACKOTTER-INSTALL-FAILED at line $LINENO"; sync; sleep 2; poweroff' ERR

ROOT_FS='@ROOT_FS@'                    # ext4, xfs, f2fs or btrfs
REPO_VERSION='@REPO_VERSION@'          # tag/DATE-unstable-stream: the repository as of one day
GUEST_NAME='@HOSTNAME@'
ROOT_PASSWORD_HASH='@ROOT_PASSWORD_HASH@'
AUTHORIZED_KEY='@AUTHORIZED_KEY@'
DISK=/dev/vda
T=/run/bko-target

# lichen-installer's "base" and "server" selections, its "kernel-common" and
# the kernel of "kernel-desktop" (without its firmware for real hardware),
# plus the tools for the guest's file systems: BackOtter's restores run the
# guest's own mkfs (dosfstools for the ESP).  And ssh-keygen, which the
# selection lacks (2026-11): without it sshd-keygen.service fails, and sshd
# has no host keys and won't start.  And the tools a server has, which
# those leave out too: no sed, grep or awk, which vm-update's and
# compare.py's scripts in the guest need, nor ps, ip or hostname.
PACKAGES=(bash dash moss sudo dbus-broker dracut kmod linux-pam lvm2 ncurses ntpd-rs shadow systemd
    systemd-boot systemd-coredump systemd-preset-aeryn systemd-resolved systemd-udev tzdata util-linux
    uutils-coreutils zstd
    'binary(nmtui)' networkmanager 'sysbinary(sshd)' 'binary(ssh-keygen)'
    intel-microcode linux-firmware-amd64-microcode linux-stable
    sed grep gawk diffutils less procps iproute2 inetutils which nano curl
    dosfstools)
case $ROOT_FS in
    ext4) PACKAGES+=(e2fsprogs) ;;
    xfs) PACKAGES+=(xfsprogs) ;;
    f2fs) PACKAGES+=(f2fs-tools) ;;
    btrfs) PACKAGES+=(btrfs-progs) ;;
esac

echo "BACKOTTER-INSTALL-START (/ on $ROOT_FS, repository $REPO_VERSION)"

# The network comes up beside this service, not before it.
for i in $(seq 60); do
    wget -q -O /dev/null https://cdn.aerynos.dev/main/moss-root-index.json && break
    [[ $i -lt 60 ]] || { echo "no network"; false; }
    sleep 5
done

# The disk: GPT, ESP 1 GiB, swap 2 GiB, / for the rest.  The partition types
# lichen-installer gives them: linux-root for btrfs, linux-fs otherwise.
root_type=0FC63DAF-8483-4772-8E79-3D69D8477DE4
[[ $ROOT_FS == btrfs ]] && root_type=4F68BCE3-E8CD-4DB1-96E7-FBCAF984B709
wipefs -q -a "$DISK"
sfdisk -q "$DISK" <<EOF
label: gpt
size=1GiB, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name=ESP
size=2GiB, type=0657FD6D-A4AB-43C4-84E5-0933C84B4F4F, name=swap
type=$root_type, name=ROOT
EOF
udevadm settle
# lichen-installer's mkfs options (disks-rs's formatter), labels included.
mkfs.fat -F 32 -n ESP "${DISK}1" >/dev/null
mkswap -q -L swap "${DISK}2"
case $ROOT_FS in
    ext4) mkfs.ext4 -q -F -L ROOT "${DISK}3" ;;
    xfs) mkfs.xfs -q -f -L ROOT -n parent=1 -i exchange=1 -m autofsck=repair "${DISK}3" ;;
    f2fs) mkfs.f2fs -q -f -l ROOT "${DISK}3" ;;
    btrfs) mkfs.btrfs -q -f -L ROOT "${DISK}3" ;;
esac
udevadm settle

# btrfs: its @ (/) and @home (/home) subvolumes, @ the default.
subvol=()
if [[ $ROOT_FS == btrfs ]]; then
    mkdir -p "$T"
    mount -t btrfs -o subvolid=5 "${DISK}3" "$T"
    btrfs -q subvolume create "$T/@" "$T/@home"
    btrfs subvolume set-default "$T/@"
    umount "$T"
    subvol=(-o subvol=@)
fi
# Name the type: right after mkfs, mount can't always probe it.
mkdir -p "$T"
mount -t "$ROOT_FS" "${subvol[@]}" "${DISK}3" "$T"
if [[ $ROOT_FS == btrfs ]]; then mount -t btrfs -o subvol=@home --mkdir "${DISK}3" "$T/home"; fi
# Mounted before moss runs: blsforme writes the boot entries to the ESP
# mounted below the target, and refuses to mount one itself there.
mount -t vfat --mkdir "${DISK}1" "$T/efi"
swapon "${DISK}2"
for d in dev dev/shm dev/pts proc sys; do mount --bind --mkdir "/$d" "$T/$d"; done

# The repository: the installer's, but at the tag of one day, so that the
# install is reproducible and the guest stays at that day (vm-update's
# in-guest.sh moves it to the live stream).  Only this one: the live
# system's own (volatile) must not leak into the guest.
mkdir -p "$T/etc/moss/repo.d" "$T/usr/lib"
cat >"$T/etc/moss/repo.d/unstable.kdl" <<EOF
unstable {
    description "AerynOS unstable package stream"
    base-uri "https://cdn.aerynos.dev/"
    channel main
    version "$REPO_VERSION"
    arch x86_64
    priority 0
    active #true
}
EOF
{
    cat <<EOF
repositories {
    unstable {
        description "AerynOS unstable package stream"
        base-uri "https://cdn.aerynos.dev/"
        version "$REPO_VERSION"
        priority 0
    }
}
packages {
EOF
    printf '    %s\n' "${PACKAGES[@]}" | sed -E 's/^    (.*\(.*)$/    "\1"/'
    echo "}"
} >"$T/usr/lib/system-model.kdl"
# The serial console too, as on the other guests; blsforme adds /etc's
# cmdline.d to every boot entry it writes.
mkdir -p "$T/etc/kernel/cmdline.d"
echo "console=tty0 console=ttyS0,115200n8" >"$T/etc/kernel/cmdline.d/90-backotter-console.cmdline"

moss -D "$T" repo update
moss sync --import "$T/usr/lib/system-model.kdl" -D "$T" -u -y
# The downloaded packages (350 MB): their files are in the new state
# already.  cache prune keeps those still in a repository, so they go by
# hand; moss fetches again what it needs.
moss -D "$T" -y cache prune
rm -rf "$T/.moss/cache/downloads"

# What the installer configures, as it does it.
echo LANG=en_US.UTF-8 >"$T/etc/locale.conf"
ln -sfn ../usr/share/zoneinfo/UTC "$T/etc/localtime"
# moss's trigger made the live system's machine id; a new one.
rm -f "$T/etc/machine-id"
chroot "$T" systemd-machine-id-setup
printf 'root:%s\n' "$ROOT_PASSWORD_HASH" | chroot "$T" chpasswd -e
partuuid() { blkid -c /dev/null -s PARTUUID -o value "$1"; }
{
    echo "# /etc/fstab: static filesystem information."
    if [[ $ROOT_FS == btrfs ]]; then
        echo "PARTUUID=$(partuuid "${DISK}3") / btrfs defaults,subvol=@ 0 0"
        echo "PARTUUID=$(partuuid "${DISK}3") /home btrfs defaults,subvol=@home 0 0"
    else
        echo "PARTUUID=$(partuuid "${DISK}3") / $ROOT_FS defaults 0 1"
    fi
    echo "PARTUUID=$(partuuid "${DISK}1") /efi vfat defaults,umask=0077 0 0"
    # Beyond the installer, which makes no swap.
    echo "PARTUUID=$(partuuid "${DISK}2") none swap defaults 0 0"
} >"$T/etc/fstab"

# BackOtter's own: host name, SSH for root by key, sshd (its preset enables
# it on a server install already).
echo "$GUEST_NAME" >"$T/etc/hostname"
install -d -m 700 "$T/root/.ssh"
echo "$AUTHORIZED_KEY" >"$T/root/.ssh/authorized_keys"
chmod 600 "$T/root/.ssh/authorized_keys"
chroot "$T" systemctl enable sshd.service NetworkManager.service
# Host keys now, so the fresh-install snapshot keeps the same ones.
chroot "$T" ssh-keygen -A

echo "--- the ESP, boot entries, fstab"
find "$T/efi" -type f | sed "s|^$T||"
cat "$T"/efi/loader/entries/*.conf
cat "$T/etc/fstab"
df -h "$T" "$T/efi"
# The boot entries name / (by its partition's PARTUUID on GPT), and the
# ESP holds systemd-boot at the removable-media path (each boot gets a
# fresh UEFI variable store).
grep -rqs "root=PARTUUID=$(partuuid "${DISK}3") " "$T/efi/loader/entries" ||
    { echo "no boot entry for / on the ESP"; false; }
[[ -n $(find "$T/efi/EFI" -ipath '*/EFI/Boot/BOOTX64.EFI') ]] ||
    { echo "no systemd-boot at EFI/Boot/BOOTX64.EFI"; false; }

# What the install wrote and deleted (the downloads) back to the image.
fstrim "$T/efi"
fstrim "$T"
swapoff "${DISK}2"
umount -R "$T"
sync
echo "BACKOTTER-INSTALL-OK"
poweroff
