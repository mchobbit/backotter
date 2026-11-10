#!/bin/bash
# An Arch-based desktop distribution's test guest for BackOtter, installed
# from its own live ISO the way its Calamares does an offline install on a
# blank disk, with that installer's defaults, unattended.  Run by make-arch.sh
# --live, which fills in the @...@ settings and serves this to the Arch ISO's
# live system (archiso's script= boot parameter), with the distribution's ISO
# as the second CD.  Prints BACKOTTER-INSTALL-OK or -FAILED, then powers off.
#
#   endeavouros  GPT; 2 GiB ESP at /efi; / ext4 (noatime); no swap; the
#                airootfs copied and cleaned as chrooted_cleaner_script.sh
#                does; systemd-boot, and dracut through kernel-install.
#   endeavouros-grub  the same with the installer's other choice of loader:
#                GRUB, with eos-dracut's initramfs in /boot.
#   manjaro      GPT; 300 MiB ESP at /boot/efi; / btrfs with @, @home, @cache
#                and @log (compress=zstd:1); no swap; rootfs and desktopfs
#                copied; GRUB and mkinitcpio.  mhwd's drivers aren't installed.
#
# The guest boots to text mode with a serial console; root and "tester" share
# a generated password, and root accepts an SSH key.

set -euo pipefail
exec > >(tee /dev/ttyS0) 2>&1
trap 'echo "BACKOTTER-INSTALL-FAILED at line $LINENO"; sync; sleep 2; poweroff' ERR

FLAVOR='@FLAVOR@'
DISTRO=${FLAVOR%-grub}
GUEST_NAME='@HOSTNAME@'
ROOT_PASSWORD_HASH='@ROOT_PASSWORD_HASH@'
AUTHORIZED_KEY='@AUTHORIZED_KEY@'
DISK=/dev/vda
CONSOLE='console=tty0 console=ttyS0,115200n8'

echo "BACKOTTER-INSTALL-START ($FLAVOR)"
until systemctl is-active -q pacman-init.service; do sleep 1; done
mkdir -p /live /layer
mount -o ro /dev/sr1 /live

# The disk, and the file systems mounted where the installer mounts them.
wipefs -q -a "$DISK"
case $DISTRO in
    endeavouros) esp_size=2GiB esp_mp=/efi esp_opts=fmask=0137,dmask=0027 ;;
    manjaro) esp_size=300MiB esp_mp=/boot/efi esp_opts=defaults,umask=0077 ;;
    *) echo "unknown DISTRO $DISTRO"; false ;;
esac
sfdisk -q "$DISK" <<PARTS
label: gpt
size=$esp_size, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name=EFI
type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name=$DISTRO
PARTS
udevadm settle
esp=${DISK}1 root=${DISK}2
mkfs.vfat -F 32 -n EFI "$esp" >/dev/null
if [[ $DISTRO == endeavouros ]]; then
    mkfs.ext4 -q -F "$root"
    mount -t ext4 -o noatime "$root" /mnt
else
    mkfs.btrfs -q -f "$root"
    mount -t btrfs "$root" /mnt
    for v in @ @home @cache @log; do btrfs -q subvolume create "/mnt/$v"; done
    umount /mnt
    btrfs_opts=compress=zstd:1
    mount -t btrfs -o "subvol=/@,$btrfs_opts" "$root" /mnt
    mkdir -p /mnt/home /mnt/var/cache /mnt/var/log
    mount -t btrfs -o "subvol=/@home,$btrfs_opts" "$root" /mnt/home
    mount -t btrfs -o "subvol=/@cache,$btrfs_opts" "$root" /mnt/var/cache
    mount -t btrfs -o "subvol=/@log,$btrfs_opts" "$root" /mnt/var/log
fi

# The live system's images.
case $DISTRO in
    endeavouros) layers=(/live/arch/x86_64/airootfs.sfs) ;;
    manjaro) layers=(/live/manjaro/x86_64/rootfs.sfs /live/manjaro/x86_64/desktopfs.sfs) ;;
esac
for l in "${layers[@]}"; do
    echo "Copying $l"
    mount -t squashfs -o ro,loop "$l" /layer
    cp -a --remove-destination /layer/. /mnt/
    umount /layer
done
mkdir -p "/mnt$esp_mp"
mount -t vfat -o "$esp_opts" "$esp" "/mnt$esp_mp"

uuid() { blkid -s UUID -o value "$1"; }
{
    echo "# Written by BackOtter's arch/install-live.sh, as Calamares writes it"
    echo "UUID=$(uuid "$esp") $esp_mp vfat $esp_opts 0 2"
    if [[ $DISTRO == endeavouros ]]; then
        echo "UUID=$(uuid "$root") / ext4 noatime 0 1"
    else
        for m in /:@ /home:@home /var/cache:@cache /var/log:@log; do
            echo "UUID=$(uuid "$root") ${m%%:*} btrfs subvol=/${m#*:},defaults,$btrfs_opts 0 0"
        done
    fi
    echo "tmpfs /tmp tmpfs defaults,noatime,mode=1777 0 0"
} >/mnt/etc/fstab

# Settings written as files from out here; only tools run in the chroot.
ln -sf /usr/share/zoneinfo/UTC /mnt/etc/localtime
sed -i 's/^#en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /mnt/etc/locale.gen
echo LANG=en_US.UTF-8 >/mnt/etc/locale.conf
echo KEYMAP=us >/mnt/etc/vconsole.conf
echo "$GUEST_NAME" >/mnt/etc/hostname
rm -f /mnt/etc/machine-id
arch-chroot /mnt systemd-machine-id-setup
arch-chroot /mnt locale-gen
arch-chroot /mnt hwclock --systohc || true
arch-chroot /mnt pacman-key --init
arch-chroot /mnt pacman-key --populate archlinux "$DISTRO"

installed() { arch-chroot /mnt pacman -Qq "$@" 2>/dev/null || true; }

if [[ $DISTRO == endeavouros ]]; then
    # chrooted_cleaner_script.sh, offline: the live session's packages, then
    # its files.
    arch-chroot /mnt sh -c 'id liveuser >/dev/null 2>&1 && userdel -r liveuser || true'
    mapfile -t remove < <(installed edk2-shell arch-install-scripts net-tools memtest86+ memtest86+-efi \
        mkinitcpio mkinitcpio-archiso mkinitcpio-busybox mkinitcpio-nfs-utils nbd pv syslinux clonezilla \
        fsarchiver gpart gparted gptfdisk grsync ckbcomp)
    mapfile -t -O "${#remove[@]}" remove < <(arch-chroot /mnt pacman -Qq | grep calamares)
    arch-chroot /mnt pacman -Rsn --noconfirm "${remove[@]}"
    arch-chroot /mnt sh -c 'rm -rf /etc/sudoers.d/g_wheel /var/lib/NetworkManager/NetworkManager.state \
        /etc/systemd/system/getty@tty1.service.d /etc/systemd/system/multi-user.target.wants/* \
        /etc/systemd/journald.conf.d /etc/systemd/logind.conf.d /etc/mkinitcpio-archiso.conf /etc/initcpio \
        /etc/motd /gpg.conf /gpg-agent.conf /pubring.gpg /secring.gpg /version /root/* /root/.[!.]* \
        /etc/calamares /opt/extra-drivers; find /usr/lib/initcpio -name "archiso*" -type f -delete 2>/dev/null; true'
    # The ISO's Arch mirrorlist is all comments; eos_pacman ranks mirrors
    # during the install.  One worldwide mirror does the same job here.
    sed -i 's|^#\(Server = https://geo\.mirror\.pkgbuild\.com/\)|\1|' /mnt/etc/pacman.d/mirrorlist
    grep -q '^Server' /mnt/etc/pacman.d/mirrorlist || { echo "no Arch mirror enabled"; false; }
    # eos_bootloader's packages for the chosen loader, from the ISO's own cache.
    if [[ $FLAVOR == endeavouros-grub ]]; then
        pkgs='eos-dracut-*.pkg.tar.zst grub-[0-9]*.pkg.tar.zst'
    else
        pkgs='kernel-install-for-dracut-*.pkg.tar.zst'
    fi
    arch-chroot /mnt sh -c "cd /usr/share/packages && pacman --noconfirm -U $pkgs && cd / && rm -rf /usr/share/packages"
    if [[ $FLAVOR == endeavouros-grub ]]; then
        # eos_bootloader's run_dracut, then Calamares's bootloader module, with
        # its fallback loader (installEFIFallback).
        for m in /mnt/usr/lib/modules/*/pkgbase; do
            cp "${m%/pkgbase}/vmlinuz" "/mnt/boot/vmlinuz-$(cat "$m")"
        done
        arch-chroot /mnt dracut-rebuild
        sed -i "s/^GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT=\"nvme_load=YES nowatchdog $CONSOLE\"/" /mnt/etc/default/grub
        arch-chroot /mnt grub-install --target=x86_64-efi --efi-directory=/efi --bootloader-id=endeavouros --recheck --no-nvram
        install -d /mnt/efi/EFI/boot
        cp /mnt/efi/EFI/endeavouros/grubx64.efi /mnt/efi/EFI/boot/bootx64.efi
        arch-chroot /mnt env GRUB_DISABLE_OS_PROBER=true grub-mkconfig -o /boot/grub/grub.cfg
        compgen -G '/mnt/boot/initramfs-*.img' >/dev/null || { echo "dracut-rebuild made no initramfs"; false; }
        grep -q 'initramfs-linux' /mnt/boot/grub/grub.cfg || { echo "grub.cfg names no initramfs"; false; }
    else
        # Calamares's bootloader module for systemd-boot.
        install -d /mnt/etc/kernel
        echo "nvme_load=YES nowatchdog rw root=UUID=$(uuid "$root") $CONSOLE" >/mnt/etc/kernel/cmdline
        arch-chroot /mnt bootctl --esp-path=/efi --no-variables install
        for m in /mnt/usr/lib/modules/*/vmlinuz; do
            v=${m%/vmlinuz}; v=${v##*/}
            arch-chroot /mnt kernel-install add "$v" "/usr/lib/modules/$v/vmlinuz"
        done
        printf 'timeout 5\nconsole-mode auto\nreboot-for-bitlocker 1\n' >/mnt/efi/loader/loader.conf
        compgen -G '/mnt/efi/loader/entries/*.conf' >/dev/null || { echo "kernel-install made no boot entry"; false; }
        compgen -G '/mnt/efi/*/*/initrd' >/dev/null || { echo "kernel-install made no initrd"; false; }
    fi
else
    # Calamares's initcpio, grubcfg and bootloader modules, with its fallback
    # loader (installEFIFallback).
    compgen -G '/mnt/boot/vmlinuz-*' >/dev/null || { echo "no kernel in /boot"; false; }
    arch-chroot /mnt mkinitcpio -P
    sed -i "s/^GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT=\"quiet udev.log_priority=3 $CONSOLE\"/" /mnt/etc/default/grub
    arch-chroot /mnt grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=manjaro --recheck --no-nvram
    install -d /mnt/boot/efi/EFI/boot
    cp /mnt/boot/efi/EFI/manjaro/grubx64.efi /mnt/boot/efi/EFI/boot/bootx64.efi
    arch-chroot /mnt env GRUB_DISABLE_OS_PROBER=true grub-mkconfig -o /boot/grub/grub.cfg
    grep -q 'subvol=' /mnt/boot/grub/grub.cfg || { echo "grub.cfg names no btrfs subvolume"; false; }
fi
grep -Eq '^ID="?'"$DISTRO"'"?$' /mnt/usr/lib/os-release || { echo "os-release doesn't say $DISTRO"; false; }

# SSH, users, and text mode.
[[ -n $(installed openssh) ]] || arch-chroot /mnt pacman -Sy --needed --noconfirm openssh
printf 'root:%s\n' "$ROOT_PASSWORD_HASH" | arch-chroot /mnt chpasswd -e
arch-chroot /mnt sh -c 'id tester >/dev/null 2>&1 || useradd -m -s /bin/bash -c "Test user" -G wheel tester'
printf 'tester:%s\n' "$ROOT_PASSWORD_HASH" | arch-chroot /mnt chpasswd -e
install -d -m 700 /mnt/root/.ssh
echo "$AUTHORIZED_KEY" >/mnt/root/.ssh/authorized_keys
chmod 600 /mnt/root/.ssh/authorized_keys
arch-chroot /mnt systemctl enable NetworkManager.service sshd.service fstrim.timer
arch-chroot /mnt systemctl set-default multi-user.target
yes | arch-chroot /mnt pacman -Scc >/dev/null || true

umount -R /mnt
umount /live
sync
echo "BACKOTTER-INSTALL-OK"
poweroff
