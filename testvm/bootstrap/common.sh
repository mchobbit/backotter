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

# -E: the ERR trap in functions too, or a failure in one (mkfs_root's) ends
# the script without powering off, and the build waits out INSTALL_TIMEOUT.
set -eEuo pipefail
exec > >(tee /dev/ttyS0) 2>&1
trap 'echo "BACKOTTER-INSTALL-FAILED at line $LINENO"; sync; sleep 2; poweroff' ERR

DISTRO='@DISTRO@'
ROOT_FS='@ROOT_FS@'                    # what make-bootstrap.sh chose for /
FIRMWARE='@FIRMWARE@'                  # bios (MBR) or uefi (GPT, ESP as /boot)
LAYOUT='@LAYOUT@'                      # classic, or uefi, lvm, luks (UEFI, ESP at /boot/efi): below
LUKS_KEY='@LUKS_KEY@'                  # luks: the key file's contents (no newline)
GUEST_NAME='@HOSTNAME@'
ROOT_PASSWORD='@ROOT_PASSWORD@'
ROOT_PASSWORD_HASH='@ROOT_PASSWORD_HASH@'
AUTHORIZED_KEY='@AUTHORIZED_KEY@'
SERVER='@SERVER@'                      # make-bootstrap.sh's HTTP server, for its files
DISK=/dev/vda
SERIAL_CMDLINE="console=tty0 console=ttyS0,115200n8"

echo "BACKOTTER-INSTALL-START ($DISTRO, / on $ROOT_FS, $FIRMWARE, $LAYOUT)"
systemctl stop reflector.service 2>/dev/null || true
until systemctl is-active -q pacman-init.service; do sleep 1; done

# The disk, by LAYOUT.  classic: /boot 1 GiB, swap 2 GiB, / for the rest.
# BIOS: MBR, /boot ext4.  UEFI: GPT, /boot is the ESP (FAT32).  ROOT_PART
# may be a LUKS device that the distribution's part opens before formatting
# it.  The others are GPT for UEFI, with an ESP of 512 MiB (ESP_PART) to
# mount at /boot/efi, as Alpine's setup-disk makes them:
#   uefi  swap 2 GiB, / for the rest (/boot a directory on it)
#   lvm   an LVM PV for the rest: VG vg0 with LVs root (12 GiB) and swap
#         (1 GiB), the VG's other extents left free
#   luks  /boot 1 GiB ext4, then LUKS2 for the rest, opened as cryptroot
#         with LUKS_KEY, holding vg0's PV: LVs root (10 GiB) and swap
#         (1 GiB), the other extents free
BOOT_PART="" ESP_PART="" LUKS_PART="" VG=""
wipefs -q -a "$DISK"
case $LAYOUT in
classic)
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
    ;;
uefi)
    sfdisk -q "$DISK" <<'EOF'
label: gpt
size=512MiB, type=uefi
size=2GiB, type=swap
type=linux
EOF
    udevadm settle
    ESP_PART=${DISK}1 SWAP_PART=${DISK}2 ROOT_PART=${DISK}3
    ;;
lvm|luks)
    if [[ $LAYOUT == lvm ]]; then
        sfdisk -q "$DISK" <<'EOF'
label: gpt
size=512MiB, type=uefi
type=lvm
EOF
        udevadm settle
        ESP_PART=${DISK}1 PV=${DISK}2 ROOT_SIZE=12G
    else
        sfdisk -q "$DISK" <<'EOF'
label: gpt
size=512MiB, type=uefi
size=1GiB, type=linux
type=linux
EOF
        udevadm settle
        ESP_PART=${DISK}1 BOOT_PART=${DISK}2 ROOT_SIZE=10G
        [[ -n $LUKS_KEY ]] || { echo "LAYOUT=luks without LUKS_KEY"; false; }
        LUKS_PART=${DISK}3
        printf '%s' "$LUKS_KEY" | cryptsetup -q luksFormat --type luks2 --key-file - "$LUKS_PART"
        printf '%s' "$LUKS_KEY" | cryptsetup open --key-file - "$LUKS_PART" cryptroot
        PV=/dev/mapper/cryptroot
    fi
    VG=vg0
    pvcreate -q -y "$PV"
    vgcreate -q -y "$VG" "$PV"
    lvcreate -q -y -L "$ROOT_SIZE" -n root "$VG"
    lvcreate -q -y -L 1G -n swap "$VG"
    udevadm settle
    ROOT_PART=/dev/$VG/root SWAP_PART=/dev/$VG/swap
    ;;
*) echo "unknown LAYOUT $LAYOUT"; false ;;
esac
mkswap -q "$SWAP_PART"
swapon "$SWAP_PART"

# mkfs_root DEVICE: make $ROOT_FS on it.
mkfs_root() {
    case $ROOT_FS in
        ext[234]) "mkfs.$ROOT_FS" -q -F "$1" ;;
        xfs) mkfs.xfs -q -f "$1" ;;
        jfs) mkfs.jfs -q "$1" ;;
        btrfs) mkfs.btrfs -q -f "$1" ;;
        # Its -q crashes (1.13.0), and so, now and then, does it without:
        # glibc's "unaligned tcache chunk" abort. A few tries.
        bcachefs) local i; for i in 1 2 3; do bcachefs format -f "$1" >/dev/null && return; sleep 1; done; false ;;
        *) echo "mkfs_root: can't make $ROOT_FS"; false ;;
    esac
}
# mount_boot: /boot (and the ESP at /boot/efi), once / is mounted at /mnt.
mount_boot() {
    if [[ $LAYOUT != classic ]]; then
        if [[ -n $BOOT_PART ]]; then
            mkfs.ext4 -q -F "$BOOT_PART"
            mount -t ext4 --mkdir "$BOOT_PART" /mnt/boot
        fi
        mkfs.fat -F 32 -n EFI "$ESP_PART" >/dev/null
        mount -t vfat --mkdir "$ESP_PART" /mnt/boot/efi
    elif [[ $FIRMWARE == bios ]]; then
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
    if [[ -n $VG ]]; then vgchange -q -a n "$VG" >/dev/null; fi
    if [[ $LAYOUT == luks ]]; then cryptsetup close cryptroot; fi
    sync
    echo "BACKOTTER-INSTALL-OK"
    poweroff
}

# --- The distribution's part ----------------------------------------------------------
