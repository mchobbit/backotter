#!/bin/bash
# Arch Linux (or CachyOS) test guest for BackOtter: the install, done the way
# the Arch wiki's installation guide does it, unattended.  BIOS + MBR with
# /boot (ext4), swap and / as three primary partitions, no desktop.
#
# FLAVOR=cachyos adds CachyOS's repositories the way CachyOS's own
# cachyos-repo.sh does for Arch (its key, x86-64-v3 repositories, keyring,
# mirror lists and its own pacman build), and installs its kernel
# (linux-cachyos) and cachyos-settings instead of Arch's kernel.
#
# make-arch.sh fills in the @...@ settings and serves this file over HTTP to
# the Arch ISO's live system, which fetches and runs it as root on tty1
# (archiso's script= boot parameter).  Its output is copied to the serial
# console, and it powers the VM off when done, printing BACKOTTER-INSTALL-OK
# or BACKOTTER-INSTALL-FAILED first.

set -euo pipefail
exec > >(tee /dev/ttyS0) 2>&1
trap 'echo "BACKOTTER-INSTALL-FAILED at line $LINENO"; sync; sleep 2; poweroff' ERR

FLAVOR='@FLAVOR@'                      # arch or cachyos
ROOT_FS='@ROOT_FS@'                    # ext4 or jfs
ARCHIVE_DATE='@ARCHIVE_DATE@'          # YYYY/MM/DD, the Arch Linux Archive snapshot
GUEST_NAME='@HOSTNAME@'
ROOT_PASSWORD_HASH='@ROOT_PASSWORD_HASH@'
AUTHORIZED_KEY='@AUTHORIZED_KEY@'
# CachyOS's signing key, "CachyOS <admin@cachyos.org>"
CACHYOS_KEY=882DCFE48E2051D48E2562ABF3B607488DB35A47
DISK=/dev/vda

PACKAGES=(base linux-firmware mkinitcpio grub openssh nano)
[[ $ROOT_FS == jfs ]] && PACKAGES+=(jfsutils)   # fsck.jfs, for the initramfs and boot
if [[ $FLAVOR == cachyos ]]; then
    # cachyos-hooks rewrites /etc/os-release to say CachyOS, as on its own installs.
    PACKAGES+=(linux-cachyos cachyos-keyring cachyos-mirrorlist cachyos-v3-mirrorlist
        cachyos-settings cachyos-hooks pacman)
    KERNEL=linux-cachyos
else
    PACKAGES+=(linux)
    KERNEL=linux
fi

echo "BACKOTTER-INSTALL-START ($FLAVOR, / on $ROOT_FS)"

systemctl stop reflector.service 2>/dev/null || true   # it would rewrite the mirrorlist
until systemctl is-active -q pacman-init.service; do sleep 1; done
if [[ $FLAVOR == arch ]]; then
    # The archive's snapshot of one day: the install is reproducible, and the
    # guest (which inherits this mirrorlist) stays frozen at that day.
    echo "Server = https://archive.archlinux.org/repos/$ARCHIVE_DATE/\$repo/os/\$arch" \
        >/etc/pacman.d/mirrorlist
else
    # CachyOS has no archive: its repositories and Arch's must both be
    # current, or their packages won't fit together.
    echo 'Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch' >/etc/pacman.d/mirrorlist
    pacman-key --keyserver hkps://keyserver.ubuntu.com --recv-keys "$CACHYOS_KEY"
    pacman-key --lsign-key "$CACHYOS_KEY"
    # The live system's stock pacman doesn't know CachyOS's $arch_v3 mirror
    # lists, so name the servers directly, ahead of Arch's repositories (as
    # cachyos-repo.sh orders them).
    awk '
        /^\[core\]/ && !done {
            print "[cachyos-v3]\nServer = https://mirror.cachyos.org/repo/x86_64_v3/$repo\n"
            print "[cachyos-core-v3]\nServer = https://mirror.cachyos.org/repo/x86_64_v3/$repo\n"
            print "[cachyos-extra-v3]\nServer = https://mirror.cachyos.org/repo/x86_64_v3/$repo\n"
            print "[cachyos]\nServer = https://mirror.cachyos.org/repo/x86_64/$repo\n"
            done = 1
        }
        /^Architecture *=/ { print "Architecture = x86_64 x86_64_v3"; next }
        { print }' /etc/pacman.conf >/tmp/pacman.conf
    mv /tmp/pacman.conf /etc/pacman.conf
    pacman -Sy
fi

# The disk: MBR, /boot 1 GiB ext4, swap 2 GiB, / ($ROOT_FS) for the rest.
wipefs -q -a "$DISK"
sfdisk -q "$DISK" <<'EOF'
label: dos
size=1GiB, type=83, bootable
size=2GiB, type=82
type=83
EOF
udevadm settle
mkfs.ext4 -q -F "${DISK}1"
mkswap -q "${DISK}2"
case $ROOT_FS in
    ext4) mkfs.ext4 -q -F "${DISK}3" ;;
    jfs) mkfs.jfs -q "${DISK}3" ;;
esac
# Name the type: right after mkfs, mount can't yet probe it, and it only
# guesses among filesystems whose modules are already loaded (jfs isn't).
mount -t "$ROOT_FS" "${DISK}3" /mnt
mount -t ext4 --mkdir "${DISK}1" /mnt/boot
swapon "${DISK}2"

if [[ $FLAVOR == arch ]]; then
    pacstrap -K /mnt "${PACKAGES[@]}"
else
    # Without -K, pacstrap copies the live system's keyring, which now trusts
    # CachyOS's key; a fresh one wouldn't, and CachyOS's packages would fail.
    pacstrap /mnt "${PACKAGES[@]}"
    # The installed system has CachyOS's pacman, which reads the mirror lists
    # its packages installed: the same repositories, as CachyOS sets them up.
    awk '
        /^\[core\]/ && !done {
            print "[cachyos-v3]\nInclude = /etc/pacman.d/cachyos-v3-mirrorlist\n"
            print "[cachyos-core-v3]\nInclude = /etc/pacman.d/cachyos-v3-mirrorlist\n"
            print "[cachyos-extra-v3]\nInclude = /etc/pacman.d/cachyos-v3-mirrorlist\n"
            print "[cachyos]\nInclude = /etc/pacman.d/cachyos-mirrorlist\n"
            done = 1
        }
        /^\[cachyos/ { skip = 1; next }   # whatever its pacman.conf already had
        /^\[/ { skip = 0 }
        skip { next }
        /^Architecture *=/ { print "Architecture = auto"; next }
        { print }' /mnt/etc/pacman.conf >/tmp/pacman.conf
    mv /tmp/pacman.conf /mnt/etc/pacman.conf
fi
genfstab -U /mnt >>/mnt/etc/fstab
if [[ $FLAVOR == cachyos ]]; then
    grep -qx 'ID=cachyos' /mnt/etc/os-release || { echo "/etc/os-release doesn't say CachyOS"; false; }
fi

# The kernel must be able to mount / at boot.
if [[ $ROOT_FS != ext4 ]]; then
    compgen -G "/mnt/usr/lib/modules/*/kernel/fs/$ROOT_FS/$ROOT_FS.ko*" >/dev/null ||
        grep -qs "kernel/fs/$ROOT_FS/$ROOT_FS.ko" /mnt/usr/lib/modules/*/modules.builtin || {
        echo "$KERNEL can't mount $ROOT_FS (no module, not built in)"
        false
    }
fi

# Settings written as files from out here; only tools run in the chroot.
ln -sf /usr/share/zoneinfo/UTC /mnt/etc/localtime
sed -i 's/^#en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /mnt/etc/locale.gen
echo LANG=en_US.UTF-8 >/mnt/etc/locale.conf
echo KEYMAP=us >/mnt/etc/vconsole.conf
echo "$GUEST_NAME" >/mnt/etc/hostname
cat >/mnt/etc/systemd/network/20-wired.network <<'EOF'
[Match]
Name=en*

[Network]
DHCP=yes
EOF
ln -sf ../run/systemd/resolve/stub-resolv.conf /mnt/etc/resolv.conf
install -d -m 700 /mnt/root/.ssh
echo "$AUTHORIZED_KEY" >/mnt/root/.ssh/authorized_keys
chmod 600 /mnt/root/.ssh/authorized_keys
# A serial console too, as on the Debian guests.
sed -i 's/^GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 quiet console=tty0 console=ttyS0,115200n8"/' \
    /mnt/etc/default/grub

arch-chroot /mnt hwclock --systohc
arch-chroot /mnt locale-gen
printf 'root:%s\n' "$ROOT_PASSWORD_HASH" | arch-chroot /mnt chpasswd -e
arch-chroot /mnt systemctl enable systemd-networkd.service systemd-resolved.service sshd.service
arch-chroot /mnt grub-install --target=i386-pc "$DISK"
arch-chroot /mnt grub-mkconfig -o /boot/grub/grub.cfg

swapoff "${DISK}2"
umount -R /mnt
sync
echo "BACKOTTER-INSTALL-OK"
poweroff
