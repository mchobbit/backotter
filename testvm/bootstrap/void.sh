# Void Linux, musl edition: the ROOTFS tarball make-bootstrap.sh checked
# against Void's release key, turned into a full install the way Void's
# handbook does it (base-system, its kernel, GRUB, runit services).  / on
# btrfs, used as a plain file system: no subvolumes, unlike the Debian,
# Fedora and openSUSE guests' btrfs.
#
# The tarball is unpacked with bsdtar (libarchive, on the Arch ISO; it reads
# the xz itself), as bootstrap/chimera.sh does: it keeps the extended
# attributes, and with them Void's file capability (iputils-ping's
# cap_net_raw=p), which GNU tar -xJp dropped.  The guest had it anyway only
# because xbps-install -yu below upgrades iputils, and xbps keeps xattrs.
# --numeric-owner: Void's IDs, not the ISO's names for them.  have_caps
# fails the build if iputils-ping has none, after the unpack and again at
# the end (xbps may have replaced it).

MIRROR=https://repo-default.voidlinux.org/current/musl
export XBPS_ARCH=x86_64-musl

mkfs_root "$ROOT_PART"
mount -t "$ROOT_FS" "$ROOT_PART" /mnt
mount_boot
curl -fsS "$SERVER/rootfs.tar.xz" | bsdtar -xpf - --numeric-owner -C /mnt
have_caps() {
    [[ $(getcap /mnt/usr/bin/iputils-ping) == *cap_net_raw* ]] ||
        { echo "/usr/bin/iputils-ping has no file capabilities ($1)"; false; }
}
have_caps "after the unpack"
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
have_caps "at the end"
rm /mnt/etc/resolv.conf
finish
