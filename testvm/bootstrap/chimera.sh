# Chimera Linux: the "full" ROOTFS tarball make-bootstrap.sh checked against
# the release's minisign key, made bootable with Chimera's apk (v3) the way
# its installation guide does it: linux-lts (initramfs-tools makes the
# initramfs), GRUB, openssh, and dinit services.  musl, LLVM-built packages,
# FreeBSD's userland (chimerautils).  LAYOUT classic: BIOS, MBR, /boot ext4,
# swap, / on XFS, GRUB.  LAYOUT luks-esp (chimera-uefi): UEFI, the ESP as
# /boot, / ext4 on LVM on LUKS2; systemd-boot (bootctl, with Chimera's
# gen-systemd-boot writing its entries), and cryptsetup-scripts' initramfs-tools
# hook unlocking / at boot with the key file it copies into the initramfs,
# never a typed passphrase, as Debian does it.
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
# grub-i386-pc for the BIOS guest, systemd-boot for the UEFI one.  The
# kernel's install runs initramfs-tools, which must find / and /boot mounted,
# as they are; its initramfs is made again below, once crypttab is there.
# gen-systemd-boot runs from the kernel's install too, and does nothing until
# bootctl has installed systemd-boot.
if [[ $FIRMWARE == bios ]]; then
    boot_packages=(grub-i386-pc)
else
    boot_packages=(systemd-boot systemd-boot-efi efibootmgr dosfstools)
fi
[[ -z $VG ]] || boot_packages+=(lvm2)
[[ -z $LUKS_PART ]] || boot_packages+=(cryptsetup cryptsetup-scripts cryptsetup-scripts-initramfs-tools)
chimera_apk add linux-lts "${boot_packages[@]}" openssh xfsprogs e2fsprogs
check_root_module "$(echo /mnt/usr/lib/modules/*)"

genfstab -U /mnt | grep -v '^#' >/mnt/etc/fstab
# An ESP as /boot holds the initramfs, and with it the LUKS key: FAT has no
# modes of its own, so the mount's masks keep it root's only.
if [[ $LAYOUT == luks-esp ]]; then
    sed -i '\|[[:space:]]/boot[[:space:]]|s/fmask=0022,dmask=0022/fmask=0077,dmask=0077/' /mnt/etc/fstab
    grep -q '[[:space:]]/boot[[:space:]].*fmask=0077,dmask=0077' /mnt/etc/fstab || { echo "fstab: /boot's masks"; false; }
fi
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

# LUKS: crypttab names the key file, and cryptsetup-scripts' hook copies
# every key file KEYFILE_PATTERN matches into the initramfs (as
# /cryptroot/keyfiles/cryptroot.key), where its local-top script opens / with
# it.  UMASK keeps the initramfs, with the key in it, readable by root only.
if [[ -n $LUKS_PART ]]; then
    install -d -m 700 /mnt/etc/cryptsetup-keys.d
    (umask 0377; printf '%s' "$LUKS_KEY" >/mnt/etc/cryptsetup-keys.d/cryptroot.key)
    printf 'cryptroot\tUUID=%s\t/etc/cryptsetup-keys.d/cryptroot.key\tluks,discard\n' \
        "$(blkid -s UUID -o value "$LUKS_PART")" >/mnt/etc/crypttab
    echo 'KEYFILE_PATTERN="/etc/cryptsetup-keys.d/*.key"' >>/mnt/etc/cryptsetup-initramfs/conf-hook
    echo UMASK=0077 >>/mnt/etc/initramfs-tools/initramfs.conf
fi

mkdir -p /mnt/etc/default
if [[ $FIRMWARE == bios ]]; then
    cat >>/mnt/etc/default/grub <<EOF
GRUB_CMDLINE_LINUX_DEFAULT="loglevel=4 $SERIAL_CMDLINE"
GRUB_TIMEOUT=2
GRUB_DISABLE_OS_PROBER=true
EOF
    in_chimera grub-install --target=i386-pc "$DISK"
    # Every kernel's initramfs again (the guest's own config), then grub.cfg.
    in_chimera update-initramfs -u -k all
    in_chimera update-grub
else
    # gen-systemd-boot's settings: the ESP is /boot, and the command line
    # (root= it adds itself, from what / is mounted from) has the serial
    # console, in place of the stock default's "quiet splash".
    cat >/mnt/etc/default/systemd-boot <<EOF
SD_BOOT_ESP_PATH=/boot
SD_BOOT_BOOT_PATH=/boot
SD_BOOT_CMDLINE="$SERIAL_CMDLINE"
SD_BOOT_CMDLINE_DEFAULT="loglevel=4"
EOF
    # The install VM boots by BIOS: no EFI variables to write.  bootctl puts
    # systemd-boot at the firmware's fallback path (EFI/BOOT/BOOTX64.EFI) too,
    # which is what vm-run --uefi's empty NVRAM boots.
    in_chimera bootctl install --esp-path=/boot --no-variables
    [[ -f /mnt/boot/EFI/BOOT/BOOTX64.EFI ]] || { echo "bootctl left no EFI/BOOT/BOOTX64.EFI"; false; }
    # The newest kernel's normal entry by default, not its recovery one.
    printf 'default chimera-*-generic.conf\ntimeout 2\n' >/mnt/boot/loader/loader.conf
    in_chimera update-initramfs -u -k all
    in_chimera gen-systemd-boot
    compgen -G '/mnt/boot/loader/entries/chimera-*.conf' >/dev/null || { echo "gen-systemd-boot wrote no entries"; false; }
    cat /mnt/boot/loader/entries/*.conf
    in_chimera bootctl list --no-pager || true
fi
if [[ -n $LUKS_PART ]]; then
    for f in /mnt/boot/initrd.img-*; do
        # Not grep -q: lsinitramfs, cut off by its exit, would fail the pipe.
        in_chimera lsinitramfs "${f#/mnt}" | grep 'cryptroot/keyfiles/cryptroot.key$' >/dev/null ||
            { echo "${f#/mnt} has no key file"; false; }
    done
fi
rm /mnt/etc/resolv.conf
finish
