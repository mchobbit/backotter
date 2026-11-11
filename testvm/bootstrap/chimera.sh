# Chimera Linux: the "full" ROOTFS tarball make-bootstrap.sh checked against
# the release's minisign key, made bootable with Chimera's apk (v3) the way
# its installation guide does it: linux-lts (initramfs-tools makes the
# initramfs), GRUB, openssh, and dinit services.  musl, LLVM-built packages,
# FreeBSD's userland (chimerautils).  LAYOUT classic: BIOS, MBR, /boot ext4,
# swap, / on XFS.
#
# Chimera's own tools are in /usr/bin (sbin and usr/sbin are links to it),
# and its /bin/sh is FreeBSD's sh: everything below that runs inside the
# chroot is called with a full PATH and no bash.

mkfs_root "$ROOT_PART"
mount -t "$ROOT_FS" "$ROOT_PART" /mnt
mount_boot
curl -fsS "$SERVER/rootfs.tar.gz" | tar -xzp -C /mnt
grep -q '^ID="\?chimera"\?$' /mnt/etc/os-release || { echo "/etc/os-release doesn't say Chimera"; false; }
rm -f /mnt/etc/resolv.conf
cp /etc/resolv.conf /mnt/etc/resolv.conf
in_chimera() { arch-chroot /mnt /usr/bin/env PATH=/usr/bin "$@"; }
# --no-interactive: apk v3 asks before changing anything when on a terminal,
# and archiso's script= runs on tty1.
chimera_apk() { in_chimera apk --no-interactive --no-progress "$@"; }
chimera_apk update
chimera_apk upgrade --available
# grub-i386-pc for the BIOS guest.  The kernel's install runs
# initramfs-tools, which must find / and /boot mounted, as they are.
chimera_apk add linux-lts grub-i386-pc openssh xfsprogs e2fsprogs
check_root_module "$(echo /mnt/usr/lib/modules/*)"

genfstab -U /mnt | grep -v '^#' >/mnt/etc/fstab
echo "$GUEST_NAME" >/mnt/etc/hostname
printf '127.0.0.1\tlocalhost\n::1\t\tlocalhost\n127.0.1.1\t%s\n' "$GUEST_NAME" >/mnt/etc/hosts
ln -sf /usr/share/zoneinfo/UTC /mnt/etc/localtime
install_ssh_key /mnt
printf 'root:%s\n' "$ROOT_PASSWORD_HASH" | in_chimera chpasswd -e

# dinit: a service starts at boot when /etc/dinit.d/boot.d has a link of its
# name (what `dinitctl enable` makes).  agetty, which Chimera enables by
# default in /usr/lib/dinit.d/boot.d, starts a getty on every active console,
# so the serial one too.
for s in sshd dhcpcd; do
    [[ -e /mnt/usr/lib/dinit.d/$s ]] || { echo "no dinit service $s"; false; }
    ln -sfn "/usr/lib/dinit.d/$s" "/mnt/etc/dinit.d/boot.d/$s"
done

mkdir -p /mnt/etc/default
cat >>/mnt/etc/default/grub <<EOF
GRUB_CMDLINE_LINUX_DEFAULT="loglevel=4 $SERIAL_CMDLINE"
GRUB_TIMEOUT=2
GRUB_DISABLE_OS_PROBER=true
EOF
in_chimera grub-install --target=i386-pc "$DISK"
# Every kernel's initramfs again (the guest's own config), then grub.cfg.
in_chimera update-initramfs -u -k all
in_chimera update-grub
rm /mnt/etc/resolv.conf
finish
