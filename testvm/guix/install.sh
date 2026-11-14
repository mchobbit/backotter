#!/bin/sh
# Run as root in Guix's installation image (make-guix.sh types the command
# that fetches it, on the image's serial console): the manual's "manual
# installation", unattended.  Partition /dev/vda for FLAVOR, make the file
# systems, mount them on /mnt, put the store's new items on the target
# (herd start cow-store /mnt), write /mnt/etc/config.scm and run
# `guix system init`, with the release's own Guix and substitutes from its
# default servers.  docs/design/guix.md, "Guests".
#
#   guix15       UEFI, GPT: 512 MiB ESP at /boot/efi, 2 GiB swap, / ext4 by
#                UUID; grub-efi-bootloader (EFI/Guix and its NVRAM entry),
#                plus a copy at the fallback path EFI/BOOT/BOOTX64.EFI
#   guix15-bios  BIOS, GPT: 1 MiB BIOS boot partition, 2 GiB swap, / Btrfs
#                (label guixroot) with subvolumes @ (/), @home, @gnu (/gnu,
#                compress=zstd), by label; grub-bootloader on /dev/vda
#
# The facts (os-release, layout, grub.cfg, the generation's parameters, the
# ESP's files) go out as a tar on the second disk, /dev/vdb.  Prints
# BACKOTTER-INSTALL-OK at the end, or BACKOTTER-INSTALL-FAILED, then halts.

FLAVOR='@FLAVOR@'
HOST='@HOSTNAME@'
USERNAME='@USERNAME@'
PASSWORD_HASH='@PASSWORD_HASH@'
AUTHORIZED_KEY='@AUTHORIZED_KEY@'
DISK=/dev/vda
FACTS=/dev/vdb

set -eu
fail() { echo "BACKOTTER-INSTALL-FAILED: $*"; sync; halt; exit 1; }
trap 'fail "line $LINENO"' EXIT 2>/dev/null || true
step() { echo "== bko-install: $*"; }

part() { case $DISK in *[0-9]) echo "${DISK}p$1" ;; *) echo "$DISK$1" ;; esac; }

step "partitioning $DISK for $FLAVOR"
wipefs -a "$DISK"
case $FLAVOR in
guix15)
    sfdisk --quiet "$DISK" <<EOF
label: gpt
size=512MiB, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name=esp
size=2GiB, type=0657FD6D-A4AB-43C4-84E5-0933C84B4F4F, name=swap
type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name=root
EOF
    udevadm settle
    mkfs.fat -F 32 -n ESP "$(part 1)"
    mkswap -L swap "$(part 2)"
    mkfs.ext4 -q -F -L root "$(part 3)"
    mount "$(part 3)" /mnt
    mkdir -p /mnt/boot/efi
    mount "$(part 1)" /mnt/boot/efi
    ;;
guix15-bios)
    sfdisk --quiet "$DISK" <<EOF
label: gpt
size=1MiB, type=21686148-6449-6E6F-744E-656564454649, name=bios
size=2GiB, type=0657FD6D-A4AB-43C4-84E5-0933C84B4F4F, name=swap
type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name=root
EOF
    udevadm settle
    mkswap -L swap "$(part 2)"
    mkfs.btrfs -q -f -L guixroot "$(part 3)"
    mount "$(part 3)" /mnt
    for s in @ @home @gnu; do btrfs subvolume create "/mnt/$s" >/dev/null; done
    umount /mnt
    mount -o subvol=@ "$(part 3)" /mnt
    mkdir -p /mnt/home /mnt/gnu
    mount -o subvol=@home "$(part 3)" /mnt/home
    mount -o subvol=@gnu,compress=zstd "$(part 3)" /mnt/gnu
    ;;
*) fail "unknown flavor $FLAVOR" ;;
esac
swapon "$(part 2)"

step "cow-store on /mnt"
herd start cow-store /mnt

root_uuid=$(blkid -s UUID -o value "$(part 3)")
swap_uuid=$(blkid -s UUID -o value "$(part 2)")
mkdir -p /mnt/etc
case $FLAVOR in
guix15)
    esp_uuid=$(blkid -s UUID -o value "$(part 1)")
    BOOTLOADER="(bootloader grub-efi-bootloader)
                (targets (list \"/boot/efi\"))"
    FILE_SYSTEMS="(file-system
                   (mount-point \"/\")
                   (device (uuid \"$root_uuid\" 'ext4))
                   (type \"ext4\"))
                 (file-system
                   (mount-point \"/boot/efi\")
                   (device (uuid \"$esp_uuid\" 'fat32))
                   (type \"vfat\"))" ;;
guix15-bios)
    BOOTLOADER="(bootloader grub-bootloader)
                (targets (list \"$DISK\"))"
    FILE_SYSTEMS="(file-system
                   (mount-point \"/\")
                   (device (file-system-label \"guixroot\"))
                   (type \"btrfs\")
                   (options \"subvol=@\"))
                 (file-system
                   (mount-point \"/home\")
                   (device (file-system-label \"guixroot\"))
                   (type \"btrfs\")
                   (options \"subvol=@home\"))
                 (file-system
                   (mount-point \"/gnu\")
                   (device (file-system-label \"guixroot\"))
                   (type \"btrfs\")
                   (options \"subvol=@gnu,compress=zstd\")
                   (needed-for-boot? #t))" ;;
esac

step "writing /mnt/etc/config.scm"
cat >/mnt/etc/config.scm <<EOF
;; A BackOtter test guest ($FLAVOR); testvm/guix/install.sh wrote this.
(use-modules (gnu))
(use-service-modules networking ssh)

(operating-system
  (host-name "$HOST")
  (timezone "UTC")
  (locale "en_US.utf8")
  (keyboard-layout (keyboard-layout "us"))
  (kernel-arguments (list "console=tty0" "console=ttyS0,115200n8"))
  (bootloader (bootloader-configuration
                $BOOTLOADER
                (timeout 2)
                (terminal-inputs '(console serial))
                (terminal-outputs '(console serial))
                (serial-unit 0)
                (serial-speed 115200)))
  (swap-devices (list (swap-space (target (uuid "$swap_uuid")))))
  (file-systems (cons* $FILE_SYSTEMS
                       %base-file-systems))
  (users (cons (user-account
                 (name "$USERNAME")
                 (comment "BackOtter tester")
                 (group "users")
                 (supplementary-groups '("wheel"))
                 (password "$PASSWORD_HASH"))
               %base-user-accounts))
  (services
    (append (list (service dhcpcd-service-type)
                  (service openssh-service-type
                    (openssh-configuration
                      (permit-root-login 'prohibit-password)
                      (password-authentication? #f)
                      (authorized-keys
                        \`(("root" ,(plain-file "root.pub" "$AUTHORIZED_KEY")))))))
            %base-services)))
EOF

step "guix system init"
t0=$(date +%s)
guix system init /mnt/etc/config.scm /mnt
echo "== bko-install: guix system init took $(( $(date +%s) - t0 )) s"

# root's password: activation keeps the hashes of accounts already in
# /etc/shadow (it makes the file on the first boot otherwise, root's empty).
printf 'root:%s:20000:0:99999:7:::\n' "$PASSWORD_HASH" >/mnt/etc/shadow
chmod 600 /mnt/etc/shadow

if [ "$FLAVOR" = guix15 ]; then
    # vm-run gives each boot a fresh variable store, with no NVRAM entries:
    # the fallback path too (the guest keeps the install's store in
    # uefi-vars.fd, with Guix's entry, for boots through it).
    mkdir -p /mnt/boot/efi/EFI/BOOT
    cp /mnt/boot/efi/EFI/Guix/grubx64.efi /mnt/boot/efi/EFI/BOOT/BOOTX64.EFI
fi

step "facts"
f=/tmp/facts; rm -rf $f; mkdir -p $f
sys=$(readlink -f /mnt/var/guix/profiles/system | sed 's|^/mnt||')
{
    echo "system: $sys"; ls -l /mnt/var/guix/profiles/
    cat "/mnt$sys/parameters"; echo
} >$f/generations.txt 2>&1 || true
cp -L /mnt/etc/config.scm $f/ 2>/dev/null || true
cp -L /mnt/boot/grub/grub.cfg $f/ 2>/dev/null || true
cp -L "/mnt$sys/etc/os-release" $f/ 2>/dev/null || true
cp -L "/mnt$sys/etc/fstab" $f/ 2>/dev/null || true
sfdisk -d "$DISK" >$f/sfdisk.txt 2>&1 || true
lsblk -o NAME,SIZE,TYPE,FSTYPE,LABEL,UUID,PARTLABEL,MOUNTPOINTS "$DISK" >$f/lsblk.txt 2>&1 || true
findmnt -R /mnt >$f/findmnt.txt 2>&1 || true
[ "$FLAVOR" != guix15-bios ] || btrfs subvolume list -a /mnt >$f/subvolumes.txt 2>&1 || true
[ ! -d /mnt/boot/efi/EFI ] || (cd /mnt/boot/efi && find . -type f -exec sha256sum {} +) >$f/esp.txt 2>&1 || true
efibootmgr -v >$f/efibootmgr.txt 2>&1 || true
du -sh /mnt/gnu/store >$f/store-size.txt 2>&1 || true
find /mnt/gnu/store -xdev -type f -links +1 | wc -l >$f/store-hardlinked-files.txt 2>&1 || true
ls /mnt/gnu/store | grep -E -- '-grub(-pc|-efi)?-[0-9]' >$f/store-grubs.txt 2>&1 || true
tar -C $f -cf "$FACTS" .

sync
trap - EXIT
echo BACKOTTER-INSTALL-OK
umount -R /mnt || true
halt
