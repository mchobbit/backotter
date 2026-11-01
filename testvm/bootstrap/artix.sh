# Artix Linux, Arch without systemd: installed with the live system's
# pacstrap from Artix's own repositories, as Artix's basestrap would, with
# dinit or s6 as init and elogind for seats.  Artix keeps no archive, so the
# guest is whatever is current, frozen only by its qcow2 snapshot.

INIT='@INIT@'                          # dinit or s6
MIRROR='https://mirrors.dotsrc.org/artix-linux/repos/$repo/os/$arch'

# Artix's keyring, from Artix's mirror over HTTPS: there is no key of
# Artix's that the live system already trusts to check it with.
cat >/tmp/artix-pacman.conf <<EOF
[options]
Architecture = auto
SigLevel = Required DatabaseOptional
LocalFileSigLevel = Optional
ParallelDownloads = 5

[system]
Server = $MIRROR
[world]
Server = $MIRROR
[galaxy]
Server = $MIRROR
EOF
sed 's/^SigLevel.*/SigLevel = Never/' /tmp/artix-pacman.conf >/tmp/artix-bootstrap.conf
mkdir -p /tmp/artix-db /tmp/artix-pkg
pacman --noconfirm --config /tmp/artix-bootstrap.conf --dbpath /tmp/artix-db -Sy
pacman --noconfirm --config /tmp/artix-bootstrap.conf --dbpath /tmp/artix-db -Sw \
    --cachedir /tmp/artix-pkg artix-keyring
tar -xf /tmp/artix-pkg/artix-keyring-*.pkg.tar.zst -C /tmp usr/share/pacman/keyrings
cp /tmp/usr/share/pacman/keyrings/artix* /usr/share/pacman/keyrings/
pacman-key --populate artix

case $INIT in
    dinit) INIT_PACKAGES=(dinit elogind-dinit openssh-dinit dhcpcd-dinit) ;;
    s6) INIT_PACKAGES=(s6-base elogind-s6 openssh-s6 dhcpcd-s6) ;;
esac
FS_PACKAGES=(e2fsprogs)
[[ $ROOT_FS == jfs ]] && FS_PACKAGES+=(jfsutils)

mkfs_root "$ROOT_PART"
mount -t "$ROOT_FS" "$ROOT_PART" /mnt
mount_boot
# Without -K, pacstrap copies the live system's keyring, which now has
# Artix's keys; the installed artix-keyring then takes over.  -M: not the
# live system's mirror list (Arch's), but artix-mirrorlist's.
pacstrap -M -C /tmp/artix-pacman.conf /mnt base "${INIT_PACKAGES[@]}" linux mkinitcpio \
    grub nano artix-keyring "${FS_PACKAGES[@]}"
check_root_module "$(echo /mnt/usr/lib/modules/*)"
grep -q '^ID=artix' /mnt/etc/os-release || { echo "/etc/os-release doesn't say Artix"; false; }

genfstab -U /mnt | grep -v '^#' >/mnt/etc/fstab
ln -sf /usr/share/zoneinfo/UTC /mnt/etc/localtime
sed -i 's/^#en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /mnt/etc/locale.gen
echo LANG=en_US.UTF-8 >/mnt/etc/locale.conf
echo "$GUEST_NAME" >/mnt/etc/hostname
printf '127.0.0.1\tlocalhost\n::1\t\tlocalhost\n127.0.1.1\t%s\n' "$GUEST_NAME" >/mnt/etc/hosts
install_ssh_key /mnt
arch-chroot /mnt locale-gen
printf 'root:%s\n' "$ROOT_PASSWORD_HASH" | arch-chroot /mnt chpasswd -e

# Start sshd and dhcpcd at boot, the way each init does it.
case $INIT in
    dinit)
        for s in sshd dhcpcd; do ln -sfn "../$s" "/mnt/etc/dinit.d/boot.d/$s"; done
        ;;
    s6)
        # Artix's s6 front end: enable, then compile the database it boots from.
        arch-chroot /mnt s6 set enable sshd dhcpcd
        arch-chroot /mnt s6 set commit
        arch-chroot /mnt s6 live install --init
        ;;
esac

cat >>/mnt/etc/default/grub <<EOF
GRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 quiet $SERIAL_CMDLINE"
EOF
arch-chroot /mnt grub-install --target=i386-pc "$DISK"
arch-chroot /mnt grub-mkconfig -o /boot/grub/grub.cfg
finish
