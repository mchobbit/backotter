# Void Linux, musl edition: the ROOTFS tarball make-bootstrap.sh checked
# against Void's release key, turned into a full install the way Void's
# handbook does it (base-system, its kernel, GRUB, runit services).  / on
# btrfs, used as a plain file system: no subvolumes, unlike the Debian,
# Fedora and openSUSE guests' btrfs.

MIRROR=https://repo-default.voidlinux.org/current/musl
export XBPS_ARCH=x86_64-musl

mkfs_root "$ROOT_PART"
mount -t "$ROOT_FS" "$ROOT_PART" /mnt
mount_boot
curl -fsS "$SERVER/rootfs.tar.xz" | tar -xJp -C /mnt
cp /etc/resolv.conf /mnt/etc/resolv.conf
echo "repository=$MIRROR" >/mnt/etc/xbps.d/00-repository-main.conf
arch-chroot /mnt xbps-install -Syu xbps
arch-chroot /mnt xbps-install -yu
arch-chroot /mnt xbps-install -y base-system grub btrfs-progs
arch-chroot /mnt xbps-remove -y base-container-full 2>/dev/null || true
check_root_module "$(echo /mnt/usr/lib/modules/*)"

genfstab -U /mnt | grep -v '^#' >/mnt/etc/fstab
echo "$GUEST_NAME" >/mnt/etc/hostname
ln -sf /usr/share/zoneinfo/UTC /mnt/etc/localtime
install_ssh_key /mnt
printf 'root:%s\n' "$ROOT_PASSWORD_HASH" | arch-chroot /mnt chpasswd -e
for s in dhcpcd sshd agetty-ttyS0; do
    ln -sfn "/etc/sv/$s" "/mnt/etc/runit/runsvdir/default/$s"
done
cat >>/mnt/etc/default/grub <<EOF
GRUB_CMDLINE_LINUX_DEFAULT="loglevel=4 $SERIAL_CMDLINE"
GRUB_TIMEOUT=2
EOF
arch-chroot /mnt grub-install --target=i386-pc "$DISK"
# Builds the initramfs (dracut) and grub.cfg, as after any kernel install.
arch-chroot /mnt xbps-reconfigure -fa
rm /mnt/etc/resolv.conf
finish
