#!/bin/bash
# The first part of every install script make-bootstrap.sh hands to an Arch
# ISO's live system: settings, logging, and the disk.  The distribution's own
# part (bootstrap/DISTRO.sh) follows it in the same file and installs into
# /mnt, which this part has mounted.
#
# The live system fetches the script and runs it as root on tty1 (archiso's
# script= boot parameter).  Its output is copied to the serial console, and
# it powers the VM off when done, printing BACKOTTER-INSTALL-OK or
# BACKOTTER-INSTALL-FAILED first.

set -euo pipefail
exec > >(tee /dev/ttyS0) 2>&1
trap 'echo "BACKOTTER-INSTALL-FAILED at line $LINENO"; sync; sleep 2; poweroff' ERR

DISTRO='@DISTRO@'
ROOT_FS='@ROOT_FS@'                    # what make-bootstrap.sh chose for /
FIRMWARE='@FIRMWARE@'                  # bios (MBR) or uefi (GPT, ESP as /boot)
GUEST_NAME='@HOSTNAME@'
ROOT_PASSWORD='@ROOT_PASSWORD@'
ROOT_PASSWORD_HASH='@ROOT_PASSWORD_HASH@'
AUTHORIZED_KEY='@AUTHORIZED_KEY@'
SERVER='@SERVER@'                      # make-bootstrap.sh's HTTP server, for its files
DISK=/dev/vda
SERIAL_CMDLINE="console=tty0 console=ttyS0,115200n8"

echo "BACKOTTER-INSTALL-START ($DISTRO, / on $ROOT_FS, $FIRMWARE)"
systemctl stop reflector.service 2>/dev/null || true
until systemctl is-active -q pacman-init.service; do sleep 1; done

# The disk: /boot 1 GiB, swap 2 GiB, / for the rest.  BIOS: MBR, /boot ext4.
# UEFI: GPT, /boot is the ESP (FAT32).  ROOT_PART may be a LUKS device that
# the distribution's part opens before formatting it.
wipefs -q -a "$DISK"
if [[ $FIRMWARE == bios ]]; then
    sfdisk -q "$DISK" <<'EOF'
label: dos
size=1GiB, type=83, bootable
size=2GiB, type=82
type=83
EOF
else
    sfdisk -q "$DISK" <<'EOF'
label: gpt
size=1GiB, type=uefi
size=2GiB, type=swap
type=linux
EOF
fi
udevadm settle
BOOT_PART=${DISK}1 SWAP_PART=${DISK}2 ROOT_PART=${DISK}3
mkswap -q "$SWAP_PART"
swapon "$SWAP_PART"

# mkfs_root DEVICE: make $ROOT_FS on it.
mkfs_root() {
    case $ROOT_FS in
        ext[234]) "mkfs.$ROOT_FS" -q -F "$1" ;;
        xfs) mkfs.xfs -q -f "$1" ;;
        jfs) mkfs.jfs -q "$1" ;;
        btrfs) mkfs.btrfs -q -f "$1" ;;
        bcachefs) bcachefs format -f "$1" >/dev/null ;;   # its -q crashes (1.13.0)
        *) echo "mkfs_root: can't make $ROOT_FS"; false ;;
    esac
}
# mount_boot: /boot, once / is mounted at /mnt.
mount_boot() {
    if [[ $FIRMWARE == bios ]]; then
        mkfs.ext4 -q -F "$BOOT_PART"
        mount -t ext4 --mkdir "$BOOT_PART" /mnt/boot
    else
        mkfs.fat -F 32 -n EFI "$BOOT_PART" >/dev/null
        mount -t vfat --mkdir "$BOOT_PART" /mnt/boot
    fi
}
# The kernel must be able to mount / at boot: check_root_module MODULES_DIR.
check_root_module() {
    compgen -G "$1/kernel/fs/$ROOT_FS/$ROOT_FS.ko*" >/dev/null ||
        grep -qs "kernel/fs/$ROOT_FS/$ROOT_FS.ko" "$1/modules.builtin" || {
        echo "the guest's kernel ($1) can't mount $ROOT_FS"
        false
    }
}
# install_ssh_key ROOT: root's authorized_keys under ROOT.
install_ssh_key() {
    install -d -m 700 "$1/root/.ssh"
    echo "$AUTHORIZED_KEY" >"$1/root/.ssh/authorized_keys"
    chmod 600 "$1/root/.ssh/authorized_keys"
}
# finish: unmount everything and power off.
finish() {
    swapoff "$SWAP_PART"
    umount -R /mnt
    sync
    echo "BACKOTTER-INSTALL-OK"
    poweroff
}

# --- The distribution's part ----------------------------------------------------------
