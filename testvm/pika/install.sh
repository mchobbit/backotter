#!/bin/bash
# PikaOS 4: a test guest for BackOtter, installed from PikaOS's own live ISO
# the way its GTK installer (pika-installer-gtk4 2.0.5, on the ISO) does an
# automatic install on a blank disk, unattended.  Run by make-pikaos.sh as
# root in the live system, over SSH, after it filled in the @...@ settings.
# Prints BACKOTTER-INSTALL-OK or -FAILED.
#
# What the GTK installer does (src/installation_summary_page/script_gen:
# mod.rs's STANDARD_INSTALLATION_PROG, then auto_basic.rs's
# AUTOMATIC_STANDARD_INSTALLATION_PROG, the layout's own script, then
# AUTOMATIC_OPEN_PART_PIKAINSTALL_PROG), one bash -e script, written below
# as it fills it in for the choices here:
#   layout xfs  auto_xfs.rs's AUTOMATIC_HOME_NONE_XFS_OPEN_INSTALLATION_PROG
#              ("Automatic", XFS, no separate /home, no LUKS): GPT,
#              "linux-efi" 1-500 MiB vfat, "linux-boot" 500-1500 MiB ext4,
#              "linux-root" the rest XFS, mounted at /media/pikaos/installation
#              with /boot and /boot/efi
#   layout btrfs-luks  auto_btrfs.rs's
#              AUTOMATIC_HOME_SUBVOL_BTRFS_LOCKED_INSTALLATION_PROG
#              ("Automatic", Btrfs, /home a subvolume, LUKS on): the same
#              GPT, "linux-root" LUKS2 opened as crypt_root, Btrfs on it with
#              subvolumes @ (/) and @home (/home); then
#              AUTOMATIC_LOCKED_PART_PIKAINSTALL_PROG, pikainstall with
#              -c PASSPHRASE.  Both copied from the 2.0.5 binary's strings.
#   pikainstall -r /media/pikaos/installation, with the GTK's defaults:
#              locale en_US, keymap and layout us, no variant; swap
#              "None/Zram" (swap_type 0, and custom_swap_size the spin row's
#              1, so -swt 0 -sws 1); rEFInd "show selection with timeout",
#              20 s (-rfs 20); BOOTX64 mode off (-rfb false) for xfs, on
#              (-rfb true) for btrfs-luks: refind-install --usedefault, rEFInd
#              in EFI/BOOT only and no NVRAM entry.  On LUKS pikainstall
#              writes /etc/crypttab's crypt_root line and refind_linux.conf's
#              rd.luks.name=UUID=crypt_root ... root=/dev/mapper/crypt_root.
#              pikainstall (Python, 2.0.5) unsquashes the live image, sets
#              bls_boot on /boot (and labels it PikaOS) and esp on the ESP,
#              writes /boot/refind_linux.conf, runs refind-install (its
#              NVRAM entry), copies the ext4 driver, writes /etc/booster.yaml,
#              and in the chroot (pika-install-chroot.sh) enables zram,
#              installs pika-refind-theme and booster from /cdrom's pool,
#              refind-mkdefault, removes the installer and makes the
#              first-setup user pikaos.  Its status messages to the GTK's
#              socket (nc -U /tmp/pikainstall-status.sock) fail harmlessly:
#              nothing listens.
#   The time zone is UTC (the GTK's is the user's choice).
#
# Then, in the target, only what every BackOtter test guest gets: host name
# @HOSTNAME@, root's password and SSH key, a "tester" user with the same
# password (in sudo), sshd enabled (Debian's default lets root log in with
# a key), text-mode boot (multi-user.target; GNOME stays installed) and a
# serial console on each of refind_linux.conf's lines.  The installer's
# first-setup user pikaos/pikaos stays as it made it.  And one more
# difference from a real install, outside BOOTX64 mode: a copy of EFI/refind
# as the fallback loader EFI/BOOT/BOOTX64.EFI (below: vm-run's fresh UEFI
# variables boot nothing else).  In BOOTX64 mode the installer made it.
#
# On a real machine (realworld/pika-bootstrap.sh, BackShot1's Thinkpad: run
# in a chroot of the live image from another Linux), two settings from the
# environment: PIKA_DISK, the disk's kernel name (vda without it), and
# PIKA_METAL=1, which leaves out what only a test guest gets: the serial
# console, text-mode boot, the "tester" user, the EFI/BOOT copy, and root's
# password when ROOT_PASSWORD_HASH is empty (root logs in by key only).  The
# installer's own steps are the same.

set -euo pipefail
trap 'echo "BACKOTTER-INSTALL-FAILED at line $LINENO"' ERR

GUEST_NAME='@HOSTNAME@'
ROOT_PASSWORD_HASH='@ROOT_PASSWORD_HASH@'
AUTHORIZED_KEY='@AUTHORIZED_KEY@'
LAYOUT='@LAYOUT@'
REFIND_BOOTX64='@REFIND_BOOTX64@'
LUKS_PASSPHRASE='@LUKS_PASSPHRASE@'   # (btrfs-luks only; letters and digits)
DISK=${PIKA_DISK:-vda}
METAL=${PIKA_METAL:-}
[[ $DISK =~ ^[a-z0-9]+$ ]] || { echo "PIKA_DISK must be a disk's kernel name (vda, nvme0n1), not \"$DISK\""; false; }
# The disk's partition N, as the GTK script names it: nvme0n1p3, vda3.
part() { case $DISK in *nvme*) echo "/dev/${DISK}p$1" ;; *) echo "/dev/$DISK$1" ;; esac; }
CONSOLE='console=tty0 console=ttyS0,115200n8'
T=/media/pikaos/installation

echo "BACKOTTER-INSTALL-START ($GUEST_NAME, $LAYOUT)"
systemctl is-system-running --wait >/dev/null || true
grep -qx 'ID=pika' /etc/os-release || { echo "not PikaOS's live system"; false; }
[[ -x /usr/lib/pika/pikainstall/pikainstall && -d /cdrom/pool/main ]] || { echo "no pikainstall or /cdrom pool"; false; }
echo "pikainstall $(dpkg-query -W -f '${Version}' pikainstall), pika-installer-gtk4 $(dpkg-query -W -f '${Version}' pika-installer-gtk4)"

# --- The GTK installer's script ----------------------------------------------------
gtk=/root/pika-installer-gtk4-script.sh
{
cat <<EOF
#! /bin/bash
set -e

SOCKET_PATH="/tmp/pikainstall-status.sock"

rm -rf /tmp/pika-installer-gtk4-swaplist
rm -rf /tmp/PIKA_CRYPT

PIKA_INSTALL_CHROOT_PATH='$T'
PIKA_INSTALL_LOCALE='en_US.UTF-8'
PIKA_INSTALL_KEYMAP='us'
PIKA_INSTALL_XKB_LAYOUT='us'
PIKA_INSTALL_XKB_VARIANT=''
PIKA_INSTALL_TIMEZONE='UTC'
PIKA_INSTALL_SWAP_TYPE='0'
PIKA_INSTALL_SWAP_SIZE='1'
PIKA_INSTALL_REFIND_TIMEOUT='20'
PIKA_INSTALL_REFIND_BOOTX64='$REFIND_BOOTX64'


echo 'PARTING' | nc -U \$SOCKET_PATH || true

PIKA_INSTALL_AUTO_TARGET_DISK=$DISK
EOF
cat <<'EOF'

# Unmount host partitions from chroot
umount "$PIKA_INSTALL_CHROOT_PATH/media/cdrom" || umount -lf "$PIKA_INSTALL_CHROOT_PATH/media/cdrom" || true
umount "$PIKA_INSTALL_CHROOT_PATH/dev" || umount -lf "$PIKA_INSTALL_CHROOT_PATH/dev" || true
umount "$PIKA_INSTALL_CHROOT_PATH/run" || umount -lf "$PIKA_INSTALL_CHROOT_PATH/run" || true
umount "$PIKA_INSTALL_CHROOT_PATH/proc" || umount -lf "$PIKA_INSTALL_CHROOT_PATH/proc" || true
umount "$PIKA_INSTALL_CHROOT_PATH/sys" || umount -lf "$PIKA_INSTALL_CHROOT_PATH/sys" || true

if echo "$PIKA_INSTALL_AUTO_TARGET_DISK" | grep -i "nvme"
then
	for part in $(/usr/lib/pika/pika-installer-gtk4/scripts/partition-utility.sh get_partitions | grep "$PIKA_INSTALL_AUTO_TARGET_DISK")p; do
		PARTITION="/dev/$part"
		swapoff $PARTITION || true
		umount -l $PARTITION || true
	done
else
	for part in $(/usr/lib/pika/pika-installer-gtk4/scripts/partition-utility.sh get_partitions | grep "$PIKA_INSTALL_AUTO_TARGET_DISK"); do
		PARTITION="/dev/$part"
		swapoff $PARTITION || true
		umount -l $PARTITION || true
	done
fi

wipefs -af /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"
blockdev --rereadpt /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK" || systemctl restart systemd-udevd.service

EOF
case $LAYOUT in
xfs) cat <<'EOF'

parted -s -a optimal /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK" mklabel gpt \
mkpart "linux-efi"  1MiB 500Mib \
mkpart "linux-boot" 500Mib 1500Mib \
mkpart "linux-root" 1500Mib  100%Mib \
print

blockdev --rereadpt /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK" || systemctl restart systemd-udevd.service

if echo "$PIKA_INSTALL_AUTO_TARGET_DISK" | grep -i "nvme"
then
#
sleep 10
# Add filesystems
yes | mkfs -t vfat -F 32 /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"p1
yes | mkfs -t ext4 /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"p2
yes | mkfs.xfs -f /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"p3
sleep 2
# Begin Mounting
echo 'MOUNTING' | nc -U $SOCKET_PATH || true
mkdir -p $PIKA_INSTALL_CHROOT_PATH
mount /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"p3 $PIKA_INSTALL_CHROOT_PATH/
mkdir -p $PIKA_INSTALL_CHROOT_PATH/boot
mount /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"p2 $PIKA_INSTALL_CHROOT_PATH/boot
mkdir -p $PIKA_INSTALL_CHROOT_PATH/boot/efi
mount /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"p1 $PIKA_INSTALL_CHROOT_PATH/boot/efi
else
sleep 10
# Add filesystems
yes | mkfs -t vfat -F 32 /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"1
yes | mkfs -t ext4 /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"2
yes | mkfs.xfs -f /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"3
sleep 2
# Begin Mounting
echo 'MOUNTING' | nc -U $SOCKET_PATH || true
mkdir -p $PIKA_INSTALL_CHROOT_PATH
mount /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"3 $PIKA_INSTALL_CHROOT_PATH/
mkdir -p $PIKA_INSTALL_CHROOT_PATH/boot
mount /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"2 $PIKA_INSTALL_CHROOT_PATH/boot
mkdir -p $PIKA_INSTALL_CHROOT_PATH/boot/efi
mount /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"1 $PIKA_INSTALL_CHROOT_PATH/boot/efi
fi
EOF
;;
btrfs-luks)
echo "PIKA_INSTALL_AUTO_LUKS_PASSWORD='$LUKS_PASSPHRASE'"
cat <<'EOF'

parted -s -a optimal /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK" mklabel gpt \
mkpart "linux-efi"  1MiB 500Mib \
mkpart "linux-boot" 500Mib 1500Mib \
mkpart "linux-root" 1500Mib  100%Mib \
print

blockdev --rereadpt /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK" || systemctl restart systemd-udevd.service

# add p to partition if it's nvme
if echo "$PIKA_INSTALL_AUTO_TARGET_DISK" | grep -i "nvme"
then
sleep 10
# Add filesystems
yes | mkfs -t vfat -F 32 /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"p1
yes | mkfs -t ext4 /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"p2
printf "$PIKA_INSTALL_AUTO_LUKS_PASSWORD" | cryptsetup -q -v --type luks2 luksFormat /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"p3
printf "$PIKA_INSTALL_AUTO_LUKS_PASSWORD" | cryptsetup -q -v luksOpen /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"p3 crypt_root
yes | mkfs.btrfs -f /dev/mapper/crypt_root
sleep 2
# Begin Mounting
echo 'MOUNTING' | nc -U $SOCKET_PATH || true
mkdir -p /var/cache/root-mnt
mount /dev/mapper/crypt_root /var/cache/root-mnt
btrfs subvolume create /var/cache/root-mnt/@
btrfs subvolume create /var/cache/root-mnt/@home
#
mkdir -p $PIKA_INSTALL_CHROOT_PATH
mount /dev/mapper/crypt_root $PIKA_INSTALL_CHROOT_PATH/ -o subvol=@
mkdir -p $PIKA_INSTALL_CHROOT_PATH/home
mount /dev/mapper/crypt_root $PIKA_INSTALL_CHROOT_PATH/home -o subvol=@home
mkdir -p $PIKA_INSTALL_CHROOT_PATH/boot
mount /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"p2 $PIKA_INSTALL_CHROOT_PATH/boot
mkdir -p $PIKA_INSTALL_CHROOT_PATH/boot/efi
mount /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"p1 $PIKA_INSTALL_CHROOT_PATH/boot/efi
else
sleep 10
# Add filesystems
yes | mkfs -t vfat -F 32 /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"1
yes | mkfs -t ext4 /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"2
printf "$PIKA_INSTALL_AUTO_LUKS_PASSWORD" | cryptsetup -q -v --type luks2 luksFormat /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"3
printf "$PIKA_INSTALL_AUTO_LUKS_PASSWORD" | cryptsetup -q -v luksOpen /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"3 crypt_root
yes | mkfs.btrfs -f /dev/mapper/crypt_root
sleep 2
# Begin Mounting
echo 'MOUNTING' | nc -U $SOCKET_PATH || true
mkdir -p /var/cache/root-mnt
mount /dev/mapper/crypt_root /var/cache/root-mnt
btrfs subvolume create /var/cache/root-mnt/@
btrfs subvolume create /var/cache/root-mnt/@home
#
mkdir -p $PIKA_INSTALL_CHROOT_PATH
mount /dev/mapper/crypt_root $PIKA_INSTALL_CHROOT_PATH/ -o subvol=@
mkdir -p $PIKA_INSTALL_CHROOT_PATH/home
mount /dev/mapper/crypt_root $PIKA_INSTALL_CHROOT_PATH/home -o subvol=@home
mkdir -p $PIKA_INSTALL_CHROOT_PATH/boot
mount /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"2 $PIKA_INSTALL_CHROOT_PATH/boot
mkdir -p $PIKA_INSTALL_CHROOT_PATH/boot/efi
mount /dev/"$PIKA_INSTALL_AUTO_TARGET_DISK"1 $PIKA_INSTALL_CHROOT_PATH/boot/efi
fi
EOF
;;
*) echo "layout $LAYOUT isn't one this script knows"; false ;;
esac
# The pikainstall run: AUTOMATIC_OPEN_PART_PIKAINSTALL_PROG, or on LUKS
# AUTOMATIC_LOCKED_PART_PIKAINSTALL_PROG (-c, the passphrase, which
# pikainstall uses only for a /home on another LUKS volume: not here).
crypt=""; [[ $LAYOUT == btrfs-luks ]] && crypt='-c "$PIKA_INSTALL_AUTO_LUKS_PASSWORD" '
cat <<EOF



if [ -z "\$PIKA_INSTALL_XKB_VARIANT" ]
then
pikainstall $crypt-r "\$PIKA_INSTALL_CHROOT_PATH" -l "\$PIKA_INSTALL_LOCALE" -k "\$PIKA_INSTALL_KEYMAP" -kl "\$PIKA_INSTALL_XKB_LAYOUT" -t "\$PIKA_INSTALL_TIMEZONE" -swt "\$PIKA_INSTALL_SWAP_TYPE" -sws "\$PIKA_INSTALL_SWAP_SIZE" -rfs "\$PIKA_INSTALL_REFIND_TIMEOUT" -rfb "\$PIKA_INSTALL_REFIND_BOOTX64"
else
pikainstall $crypt-r "\$PIKA_INSTALL_CHROOT_PATH" -l "\$PIKA_INSTALL_LOCALE" -k "\$PIKA_INSTALL_KEYMAP" -kl "\$PIKA_INSTALL_XKB_LAYOUT" -t "\$PIKA_INSTALL_TIMEZONE" -swt "\$PIKA_INSTALL_SWAP_TYPE" -sws "\$PIKA_INSTALL_SWAP_SIZE" -rfs "\$PIKA_INSTALL_REFIND_TIMEOUT" -rfb "\$PIKA_INSTALL_REFIND_BOOTX64" -kv "\$PIKA_INSTALL_XKB_VARIANT"
fi
EOF
} >"$gtk"
echo "--- running the GTK installer's script ($gtk)"
# As the GTK runs it: bash -e, no pipefail (its "yes | mkfs" would fail).
bash "$gtk"
echo "--- the GTK installer's script finished"

# --- What pikainstall left (it doesn't check its steps' exit statuses) -------------
findmnt -R "$T"
# rEFInd's directory: EFI/refind (refind-install --root), or in BOOTX64 mode
# EFI/BOOT (--usedefault: refind_x64.efi as BOOTX64.EFI, no NVRAM entry).
if [[ $REFIND_BOOTX64 == true ]]; then rd=boot/efi/EFI/BOOT rbin=BOOTX64.EFI; else rd=boot/efi/EFI/refind rbin=refind_x64.efi; fi
for f in etc/fstab etc/booster.yaml boot/refind_linux.conf $rd/$rbin $rd/refind.conf $rd/drivers_x64/ext4_x64.efi; do
    [[ -s $T/$f ]] || { echo "pikainstall left no /$f"; false; }
done
ls "$T"/boot/booster.img-* >/dev/null || { echo "no /boot/booster.img-*"; false; }
if [[ $LAYOUT == btrfs-luks ]]; then
    grep -q 'rd\.luks\.name=[0-9a-f-]*=crypt_root .*root=/dev/mapper/crypt_root' "$T/boot/refind_linux.conf" ||
        { echo "refind_linux.conf has no rd.luks.name=UUID=crypt_root ... root=/dev/mapper/crypt_root"; false; }
    grep -Eq '^crypt_root[[:space:]]+UUID=[0-9a-f-]+[[:space:]]+none[[:space:]]+luks,discard$' "$T/etc/crypttab" ||
        { echo "/etc/crypttab has no crypt_root UUID=... none luks,discard"; false; }
    [[ ! -e $T/boot/efi/EFI/refind ]] || { echo "BOOTX64 mode left an EFI/refind too"; false; }
else
    grep -q 'root=UUID=' "$T/boot/refind_linux.conf" || { echo "refind_linux.conf has no root=UUID="; false; }
fi
if grep -q 'refind-dark' "$T/$rd/refind.conf"; then echo "/$rd/refind.conf includes Pika's theme"
elif [[ $REFIND_BOOTX64 == true ]]; then echo "note: /$rd/refind.conf doesn't include Pika's theme (pika-refind-theme installs to EFI/refind)"
else echo "refind.conf doesn't include Pika's theme"; false; fi
if [[ $REFIND_BOOTX64 == true ]]; then
    ! efibootmgr | grep -iF 'refind' || { echo "BOOTX64 mode made a rEFInd NVRAM entry"; false; }
else
    efibootmgr | grep -iF 'refind' || { echo "no rEFInd NVRAM entry"; false; }
fi
! dpkg-query --admindir="$T/var/lib/dpkg" -W -f '${Status}' pika-installer-gtk4 2>/dev/null | grep -q ' installed' ||
    { echo "the installer is still installed in the target"; false; }
id_pikaos=$(chroot "$T" id -u pikaos) && [[ $id_pikaos == 1001 ]] || { echo "no first-setup user pikaos (1001)"; false; }
# The fallback loader.  Outside BOOTX64 mode the installer puts rEFInd in
# EFI/refind only, with its NVRAM entry; but vm-run starts each run with
# fresh UEFI variables, which boot only EFI/BOOT/BOOTX64.EFI.  So EFI/refind
# is copied there whole (rEFInd reads refind.conf, its theme and drivers
# from its own directory), its binary as BOOTX64.EFI, as refind-install
# --usedefault names it.  The NVRAM entry stays the first boot's way in.
# In BOOTX64 mode the installer made EFI/BOOT itself: nothing is added.
# A real machine keeps its NVRAM: no copy there, as the installer leaves it.
if [[ -z $METAL ]]; then
    if [[ $REFIND_BOOTX64 == false && ! -e $T/boot/efi/EFI/BOOT ]]; then
        cp -a "$T/boot/efi/EFI/refind" "$T/boot/efi/EFI/BOOT"
        mv "$T/boot/efi/EFI/BOOT/refind_x64.efi" "$T/boot/efi/EFI/BOOT/BOOTX64.EFI"
    fi
    [[ -s $T/boot/efi/EFI/BOOT/BOOTX64.EFI ]] || { echo "no fallback loader EFI/BOOT/BOOTX64.EFI"; false; }
fi

# --- This guest's additions ----------------------------------------------------------
mount --bind /dev "$T/dev"
mount -t proc proc "$T/proc"
mount -t sysfs sys "$T/sys"
mount -t tmpfs tmpfs "$T/run"
in_t() { chroot "$T" "$@"; }
echo "$GUEST_NAME" >"$T/etc/hostname"
sed -i -E "s/^(127\\.0\\.1\\.1[[:space:]]+)pikaos\$/\\1$GUEST_NAME/" "$T/etc/hosts"
[[ -z $ROOT_PASSWORD_HASH ]] || printf 'root:%s\n' "$ROOT_PASSWORD_HASH" | in_t chpasswd -e
install -d -m 700 "$T/root/.ssh"
echo "$AUTHORIZED_KEY" >"$T/root/.ssh/authorized_keys"
chmod 600 "$T/root/.ssh/authorized_keys"
if [[ -z $METAL ]]; then
    in_t useradd -m -s /bin/bash -c "Test user" -G sudo tester
    printf 'tester:%s\n' "$ROOT_PASSWORD_HASH" | in_t chpasswd -e
fi
in_t systemctl enable ssh.service
if [[ -z $METAL ]]; then
    in_t systemctl set-default multi-user.target
    # The serial console on each of rEFInd's lines ("TITLE"  "OPTIONS ---"),
    # before the closing --- (its separator), the rest kept.
    sed -i -E "/^\"/ s/( ---)?\"[[:space:]]*\$/ $CONSOLE\\1\"/" "$T/boot/refind_linux.conf"
    [[ $(grep -c . "$T/boot/refind_linux.conf") == $(grep -c " $CONSOLE" "$T/boot/refind_linux.conf") ]] ||
        { echo "not every refind_linux.conf line has the serial console"; false; }
fi

# --- checks -------------------------------------------------------------------------
grep -qx 'ID=pika' "$T/etc/os-release" || { echo "the target's os-release doesn't say PikaOS"; false; }
echo "--- os-release"; cat "$T/etc/os-release"
echo "--- fstab"; cat "$T/etc/fstab"
echo "--- refind_linux.conf"; cat "$T/boot/refind_linux.conf"
echo "--- booster.yaml"; cat "$T/etc/booster.yaml"
echo "--- /boot"; ls -l "$T/boot"
echo "--- ESP"; find "$T/boot/efi" -maxdepth 3 | sort
echo "--- refind.conf (set lines)"; grep -vE '^[[:space:]]*(#|$)' "$T/$rd/refind.conf"
if [[ $LAYOUT == btrfs-luks ]]; then
    echo "--- crypttab"; cat "$T/etc/crypttab"
    echo "--- LUKS"; cryptsetup luksDump "$(part 3)" | grep -E '^(Version|UUID|Cipher|Keyslots|  [0-9]+: luks2|	(Cipher|Key|PBKDF):)' || true
    echo "--- Btrfs subvolumes"; btrfs subvolume list "$T"
fi
echo "--- efibootmgr"; efibootmgr -v
echo "--- GPT"; parted -s /dev/$DISK print
echo "--- packages"
in_t dpkg-query -W -f '${Package} ${Version} ${Status}\n' booster initramfs-tools refind pika-refind-theme \
    kernel-pika zram-tools openssh-server refind-btrfs apt-btrfs-snapper 'linux-image-*' 2>/dev/null | grep ' installed$' || true
in_t systemctl is-enabled ssh.service zramswap.service booster-reassert.timer || true

# --- finish: unmount ---------------------------------------------------------------
for p in /proc/[0-9]*; do
    [[ $(readlink "$p/root" 2>/dev/null) == "$T" ]] || continue
    echo "stopping ${p#/proc/} ($(tr '\0' ' ' <"$p/cmdline"), left by the chroot)"
    kill "${p#/proc/}" 2>/dev/null || true
done
sleep 2
umount "$T/run" "$T/sys" "$T/proc" "$T/dev"
fstrim -v "$T/boot/efi" || true
fstrim -v "$T/boot" || true
fstrim -v "$T" || true
if [[ $LAYOUT == btrfs-luks ]]; then umount "$T/home"; fi
umount "$T/boot/efi" "$T/boot" "$T"
if [[ $LAYOUT == btrfs-luks ]]; then
    # The GTK script's mount of the whole Btrfs, and crypt_root itself.
    umount /var/cache/root-mnt
    cryptsetup close crypt_root
fi
sync
echo "BACKOTTER-INSTALL-OK"
