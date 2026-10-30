# Alpine Linux: the minirootfs make-bootstrap.sh checked against Alpine's
# release key, made bootable with apk the way setup-disk's "sys" install
# would: linux-lts, mkinitfs, GRUB, OpenRC's usual services.  musl, busybox,
# / on XFS.

ALPINE_BRANCH='@ALPINE_BRANCH@'        # e.g. v3.24
MIRROR=https://dl-cdn.alpinelinux.org/alpine

mkfs_root "$ROOT_PART"
mount -t "$ROOT_FS" "$ROOT_PART" /mnt
mount_boot
curl -fsS "$SERVER/rootfs.tar.gz" | tar -xzp -C /mnt
cat >/mnt/etc/apk/repositories <<EOF
$MIRROR/$ALPINE_BRANCH/main
$MIRROR/$ALPINE_BRANCH/community
EOF
cp /etc/resolv.conf /mnt/etc/resolv.conf
# Alpine's tools are in /sbin and /usr/sbin, which the live system's PATH lacks.
in_alpine() { arch-chroot /mnt /usr/bin/env PATH=/usr/sbin:/usr/bin:/sbin:/bin "$@"; }
# mkinitfs runs when linux-lts is installed, so it must know about XFS
# first: its stock features, plus the root file system's (apk keeps this
# file, as a changed configuration file).
mkdir -p /mnt/etc/mkinitfs
echo "features=\"ata base ide scsi usb virtio ext4 $ROOT_FS\"" >/mnt/etc/mkinitfs/mkinitfs.conf
in_alpine apk add --no-progress alpine-base linux-lts linux-firmware-none mkinitfs \
    grub grub-bios openssh e2fsprogs xfsprogs chrony
check_root_module "$(echo /mnt/lib/modules/*)"

genfstab -U /mnt | grep -v '^#' >/mnt/etc/fstab
echo "$GUEST_NAME" >/mnt/etc/hostname
printf '127.0.0.1\t%s localhost\n::1\t\tlocalhost\n' "$GUEST_NAME" >/mnt/etc/hosts
cat >/mnt/etc/network/interfaces <<'EOF'
auto lo
iface lo inet loopback

auto eth0
iface eth0 inet dhcp
EOF
ln -sf /usr/share/zoneinfo/UTC /mnt/etc/localtime 2>/dev/null || true
# A login prompt on the serial console too.
sed -i 's|^#ttyS0::respawn:.*|ttyS0::respawn:/sbin/getty -L 115200 ttyS0 vt100|' /mnt/etc/inittab
grep -q '^ttyS0' /mnt/etc/securetty 2>/dev/null || echo ttyS0 >>/mnt/etc/securetty
install_ssh_key /mnt
printf 'root:%s\n' "$ROOT_PASSWORD_HASH" | in_alpine chpasswd -e

# The services setup-alpine enables.
for s in devfs dmesg mdev hwdrivers; do in_alpine rc-update add $s sysinit; done
for s in modules sysctl hostname bootmisc syslog hwclock swap; do in_alpine rc-update add $s boot; done
for s in mount-ro killprocs savecache; do in_alpine rc-update add $s shutdown; done
for s in networking sshd chronyd crond; do in_alpine rc-update add $s default; done

cat >>/mnt/etc/default/grub <<EOF
GRUB_CMDLINE_LINUX_DEFAULT="modules=sd-mod,usb-storage,$ROOT_FS rootfstype=$ROOT_FS quiet $SERIAL_CMDLINE"
GRUB_TIMEOUT=2
GRUB_DISABLE_OS_PROBER=true
EOF
in_alpine grub-install --target=i386-pc "$DISK"
in_alpine grub-mkconfig -o /boot/grub/grub.cfg
rm /mnt/etc/resolv.conf
finish
