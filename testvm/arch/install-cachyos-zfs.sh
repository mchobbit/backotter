#!/bin/bash
# CachyOS on a ZFS root, booted by Limine: a test guest for BackOtter,
# installed from CachyOS's own live ISO the way its Calamares does an online
# install on a blank disk with "ZFS" and "Limine" chosen, unattended.  Run by
# make-cachyos-zfs.sh as root in the live system, over SSH, after it filled in
# the @...@ settings.  Prints BACKOTTER-INSTALL-OK or -FAILED.
#
# What Calamares does (cachyos-calamares, branch cachyos, settings_online.conf
# exec sequence), step by step below:
#   partition  GPT; the limine override's ESP, 4 GiB FAT32 as /boot; the rest
#              one ZFS partition; no swap (cachyos-settings gives zram)
#   zfs        zgenhostid; pool zpcachyos (zfs.conf poolOptions); datasets
#              ROOT, ROOT/cos (canmount=off), ROOT/cos/root (/, noauto),
#              ROOT/cos/home, ROOT/cos/varcache, ROOT/cos/varlog (on), all
#              with compression=lz4
#   mount      zpool import -N -R; the root dataset, then the others; the ESP
#              with umask=0077
#   shellprocess@modify_mk_hook   no initramfs while packages go in
#   shellprocess@initialize_pacman, before-online   keyring, mirror lists,
#              /etc/pacman-more.conf as the target's pacman.conf plus the
#              CPU's optimised repositories (detect-architecture)
#   pacstrap   pacstrap.conf's basePackages, limine + limine-entry-tool,
#              the CPU's microcode, zfs-utils + linux-cachyos-zfs +
#              linux-cachyos-lts-zfs
#   machineid, locale, keyboard, localecfg, packages@online (pacman -Syu)
#   fstab      the ESP only (ZFS mounts itself) and tmpfs /tmp
#   zfshostid  /etc/hostid copied in
#   initcpiocfg  HOOKS without systemd (ZFS): base udev autodetect microcode
#              modconf block keyboard keymap consolefont zfs filesystems fsck
#   initcpio   mkinitcpio -P
#   users, hwclock
#   bootloader limine.conf on the ESP, /etc/default/limine (ESP_PATH,
#              KERNEL_CMDLINE with root=ZFS=zpcachyos/ROOT/cos/root),
#              limine-install (the "Limine" NVRAM entry)
#   shellprocess@reset_mk_hook, services-systemd (zfs-import-cache, zfs-mount,
#              zfs-import.target, zfs.target, NetworkManager, timesyncd,
#              fstrim.timer), shellprocess's bootloader-post-setup
#              (limine-entry-tool -> limine-mkinitcpio-hook, limine-update)
#
# Where this guest differs from such an install, on purpose:
#   - No desktop and no netinstall groups (Calamares's packagechooser@desktop
#     "no desktop"); NetworkManager and openssh are added, sshd enabled, and
#     multi-user.target is the default.  root and "tester" share a generated
#     password; root takes an SSH key.
#   - The x86-64-v3 repositories, whatever the build host's CPU: the guest
#     must run on every host (detect-architecture picks v4 or znver4 on Zen 4).
#   - The pool's cachefile is /etc/zfs/zpool.cache, copied in (Calamares
#     leaves the live system's; zfs-import-cache.service, which it enables,
#     reads this one).
#   - ENABLE_LIMINE_FALLBACK=yes, so the ESP also has EFI/BOOT/BOOTX64.EFI:
#     vm-run starts each run with fresh UEFI variables.  The "Limine" NVRAM
#     entry is made all the same (make-cachyos-zfs.sh keeps those variables).
#   - The kernel command line also has a serial console.
#   - If the live system's OpenZFS is newer than 2.4, the pool gets
#     compatibility=openzfs-2.4-linux: BackOtter's rescue image has 2.4.1.

set -euo pipefail
trap 'echo "BACKOTTER-INSTALL-FAILED at line $LINENO"' ERR

GUEST_NAME='@HOSTNAME@'
ROOT_PASSWORD_HASH='@ROOT_PASSWORD_HASH@'
AUTHORIZED_KEY='@AUTHORIZED_KEY@'
DISK=/dev/vda
POOL=zpcachyos
CONSOLE='console=tty0 console=ttyS0,115200n8'
T=/mnt

echo "BACKOTTER-INSTALL-START ($GUEST_NAME)"
systemctl is-system-running --wait >/dev/null || true
until systemctl is-active -q pacman-init.service; do sleep 1; done
grep -q ' /run/archiso/' /proc/mounts || { echo "not CachyOS's live system"; false; }

# --- zfs: the module, its version, the host ID -------------------------------------
modprobe zfs
zfs_version=$(cat /sys/module/zfs/version)
echo "Live OpenZFS: $zfs_version ($(zfs version | head -n 1))"
compat=()
case ${zfs_version%%-*} in
    0.* | 1.* | 2.[0-4].*) ;;
    *) [[ -e /usr/share/zfs/compatibility.d/openzfs-2.4-linux ]] ||
           { echo "OpenZFS $zfs_version has no openzfs-2.4-linux compatibility file"; false; }
       compat=(-o compatibility=openzfs-2.4-linux) ;;
esac
[[ -e /etc/hostid ]] || zgenhostid

# --- partition ---------------------------------------------------------------------
wipefs -q -a "$DISK"
sfdisk -q "$DISK" <<'PARTS'
label: gpt
size=4096MiB, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B
type=6A898CC3-1DD2-11B2-99A6-080020736631
PARTS
udevadm settle
esp=${DISK}1 zpart=${DISK}2
mkfs.vfat -F 32 "$esp" >/dev/null

# --- zfs: the pool and datasets (zfs.conf) -----------------------------------------
# Calamares creates the pool without an altroot and exports it at once; -R
# here keeps /home and the rest off the live system meanwhile (altroot isn't
# stored in the pool).
zpool create -f -o ashift=12 -o autotrim=on "${compat[@]}" -R "$T" \
    -O mountpoint=none -O acltype=posixacl -O atime=off -O relatime=off \
    -O xattr=sa -O normalization=formD "$POOL" "$zpart"
ds() { zfs create -o compression=lz4 -o "canmount=$3" -o "mountpoint=$2" "$POOL/$1"; }
ds ROOT none off
ds ROOT/cos none off
ds ROOT/cos/root / noauto
ds ROOT/cos/home /home on
ds ROOT/cos/varcache /var/cache on
ds ROOT/cos/varlog /var/log on
zpool export "$POOL"

# --- mount ---------------------------------------------------------------------------
zpool import -N -R "$T" "$POOL"
zfs mount "$POOL/ROOT/cos/root"
for d in home varcache varlog; do zfs mount "$POOL/ROOT/cos/$d"; done
findmnt -no FSTYPE "$T" | grep -qx zfs || { echo "/ isn't the ZFS root"; false; }
mkdir -p "$T/boot"
mount -t vfat -o defaults,umask=0077 "$esp" "$T/boot"
# The pool cachefile, which the zfs hook and zfs-import-cache.service read.
mkdir -p "$T/etc/zfs"
zpool set cachefile=/etc/zfs/zpool.cache "$POOL"
cp /etc/zfs/zpool.cache "$T/etc/zfs/zpool.cache"

# --- shellprocess@modify_mk_hook: kernels into /boot, no initramfs yet ---------------
mkdir -p "$T/etc/pacman.d/hooks" "$T/usr/share/libalpm/scripts"
cat >"$T/usr/share/libalpm/scripts/mkinitcpio-install-calamares" <<'EOF'
#!/bin/bash -e
# As cachyos-calamares's: put each new kernel in /boot and make its preset,
# but no initramfs (initcpio makes them once mkinitcpio.conf is right).
while read -r line; do
    [[ $line == */vmlinuz ]] || continue
    read -r pkgbase <"${line%/vmlinuz}/pkgbase" || continue
    preset=/etc/mkinitcpio.d/$pkgbase.preset
    [[ -e $preset ]] || sed "s|%PKGBASE%|$pkgbase|g" /usr/share/mkinitcpio/hook.preset | install -Dm644 /dev/stdin "$preset"
    install -Dm644 "$line" "/boot/vmlinuz-$pkgbase"
done
EOF
chmod +x "$T/usr/share/libalpm/scripts/mkinitcpio-install-calamares"
cat >"$T/etc/pacman.d/hooks/90-mkinitcpio-install.hook" <<'EOF'
[Trigger]
Type = Path
Operation = Install
Operation = Upgrade
Target = usr/lib/modules/*/vmlinuz
Target = usr/lib/initcpio/*

[Action]
Description = Updating linux initcpios...
When = PostTransaction
Exec = /usr/share/libalpm/scripts/mkinitcpio-install-calamares
NeedsTargets
EOF

# --- shellprocess@initialize_pacman and @before-online ------------------------------
pacman-key --init
for _ in 1 2 3 4 5; do pacman -Sy --noconfirm --needed cachyos-keyring && break; done
for _ in 1 2 3 4 5; do pacman -Sy --noconfirm --needed archlinux-keyring && break; done
pacman-key --populate
cp /etc/pacman.d/mirrorlist /etc/pacman.d/cachyos-mirrorlist /etc/pacman.d/cachyos-v3-mirrorlist \
    /etc/pacman.d/cachyos-v4-mirrorlist "$T/etc/pacman.d/"
cp -a /etc/pacman.d/gnupg "$T/etc/pacman.d/"
cp --remove-destination /etc/resolv.conf "$T/etc/"
cp /etc/pacman-more.conf "$T/etc/pacman.conf"
# detect-architecture's x86-64-v3 case.
sed -i '/^\[cachyos\]$/i \
[cachyos-v3] \
Include = /etc/pacman.d/cachyos-v3-mirrorlist \
\
[cachyos-extra-v3] \
Include = /etc/pacman.d/cachyos-v3-mirrorlist \
\
[cachyos-core-v3] \
Include = /etc/pacman.d/cachyos-v3-mirrorlist\n' "$T/etc/pacman.conf"
grep -q '^\[cachyos-core-v3\]' "$T/etc/pacman.conf" || { echo "no v3 repositories in pacman.conf"; false; }

# --- pacstrap (pacstrap_calamares: pacman --sysroot, the target's config) ------------
packages=(base base-devel btrfs-progs cachyos-hooks cachyos-keyring cachyos-mirrorlist
    cachyos-v4-mirrorlist cachyos-v3-mirrorlist cachyos-rate-mirrors cachyos-settings cryptsetup
    device-mapper diffutils dosfstools e2fsprogs efibootmgr exfatprogs f2fs-tools inetutils
    jfsutils less linux-cachyos linux-cachyos-headers linux-cachyos-lts linux-cachyos-lts-headers
    linux-firmware logrotate lsb-release lvm2 man-db man-pages mdadm chwd mkinitcpio netctl
    os-prober perl python s-nail sudo sysfsutils texinfo usbutils which xfsprogs
    limine limine-entry-tool
    zfs-utils linux-cachyos-zfs linux-cachyos-lts-zfs)
if grep -q '^vendor_id.*GenuineIntel' /proc/cpuinfo; then packages+=(intel-ucode); else packages+=(amd-ucode); fi
# This guest's additions (no desktop): a network and SSH.
packages+=(networkmanager openssh)
mkdir -m 0755 -p "$T"/var/{cache/pacman/pkg,lib/pacman,log} "$T"/{dev,run,etc/pacman.d}
mkdir -m 1777 -p "$T/tmp"
mkdir -m 0555 -p "$T"/{sys,proc}
# Calamares's mount module mounts these in the target before pacstrap.
mount -t proc proc "$T/proc"
mount -t sysfs sys "$T/sys"
mount -t efivarfs efivarfs "$T/sys/firmware/efi/efivars"
mount --bind /dev "$T/dev"
mount -t tmpfs tmpfs "$T/run"
mkdir -p "$T/run/udev" && mount --bind /run/udev "$T/run/udev"
# pacstrap_calamares -c: the cache is the target's own /var/cache/pacman/pkg.
pacman --sysroot "$T" -Sy --noconfirm --needed "${packages[@]}"
cp /etc/mkinitcpio.conf "$T/etc/mkinitcpio.conf"
in_t() { chroot "$T" "$@"; }

# --- machineid, locale, keyboard, localecfg ----------------------------------------
rm -f "$T/etc/machine-id"
in_t systemd-machine-id-setup
ln -sf /usr/share/zoneinfo/UTC "$T/etc/localtime"
sed -i 's/^#en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' "$T/etc/locale.gen"
echo LANG=en_US.UTF-8 >"$T/etc/locale.conf"
echo KEYMAP=us >"$T/etc/vconsole.conf"
echo "$GUEST_NAME" >"$T/etc/hostname"
in_t locale-gen

# --- packages@online: update_system ------------------------------------------------
in_t pacman -Syu --noconfirm

# --- fstab, zfshostid --------------------------------------------------------------
uuid() { blkid -s UUID -o value "$1"; }
{
    echo "# Written by BackOtter's arch/install-cachyos-zfs.sh, as Calamares writes it"
    echo "UUID=$(uuid "$esp") /boot vfat defaults,umask=0077 0 2"
    echo "tmpfs /tmp tmpfs defaults,noatime,mode=1777 0 0"
} >"$T/etc/fstab"
cp /etc/hostid "$T/etc/hostid"

# --- initcpiocfg, initcpio ---------------------------------------------------------
hooks='base udev autodetect microcode modconf block keyboard keymap consolefont zfs filesystems fsck'
sed -i "s/^HOOKS=.*/HOOKS=($hooks)/" "$T/etc/mkinitcpio.conf"
grep -qx "HOOKS=($hooks)" "$T/etc/mkinitcpio.conf" || { echo "mkinitcpio.conf has no HOOKS line"; false; }
in_t mkinitcpio -P

# --- users, hwclock -----------------------------------------------------------------
printf 'root:%s\n' "$ROOT_PASSWORD_HASH" | in_t chpasswd -e
in_t useradd -m -s /bin/bash -c "Test user" -G wheel tester
printf 'tester:%s\n' "$ROOT_PASSWORD_HASH" | in_t chpasswd -e
echo '%wheel ALL=(ALL:ALL) ALL' >"$T/etc/sudoers.d/10-installer"
chmod 440 "$T/etc/sudoers.d/10-installer"
install -d -m 700 "$T/root/.ssh"
echo "$AUTHORIZED_KEY" >"$T/root/.ssh/authorized_keys"
chmod 600 "$T/root/.ssh/authorized_keys"
in_t hwclock --systohc || true

# --- bootloader: limine ------------------------------------------------------------
machine_id=$(cat "$T/etc/machine-id")
cmdline="quiet nowatchdog rw root=ZFS=$POOL/ROOT/cos/root $CONSOLE"
printf 'timeout: 5\ndefault_entry: 2\nremember_last_entry: yes\n\ncomment: machine-id=%s\n/+CachyOS\n' \
    "$machine_id" >"$T/boot/limine.conf"
cat >"$T/etc/default/limine" <<EOF
ESP_PATH="/boot"
KERNEL_CMDLINE[default]+="$cmdline"
BOOT_ORDER="*, *lts, *fallback, Snapshots"
ENABLE_LIMINE_FALLBACK=yes
EOF
in_t limine-install

# --- shellprocess@reset_mk_hook, services-systemd -----------------------------------
rm "$T/etc/pacman.d/hooks/90-mkinitcpio-install.hook" "$T/usr/share/libalpm/scripts/mkinitcpio-install-calamares"
for u in NetworkManager systemd-timesyncd zfs-import-cache zfs-mount zfs-import.target zfs.target \
    fstrim.timer sshd; do
    in_t systemctl enable "$u"
done
in_t systemctl set-default multi-user.target

# --- shellprocess: bootloader-post-setup --------------------------------------------
in_t pacman -R --noconfirm limine-entry-tool || true   # as there: not checked
in_t pacman -S --noconfirm --needed limine-mkinitcpio-hook
in_t limine-update

# --- checks -------------------------------------------------------------------------
# cachyos-hooks writes /etc/os-release (/usr/lib/os-release stays Arch's).
grep -qx 'ID=cachyos' "$T/etc/os-release" || { echo "/etc/os-release doesn't say CachyOS"; false; }
grep -q "root=ZFS=$POOL/ROOT/cos/root" "$T/boot/limine.conf" || { echo "limine.conf has no ZFS root entry"; false; }
[[ -e $T/boot/EFI/BOOT/BOOTX64.EFI ]] || { echo "no fallback loader EFI/BOOT/BOOTX64.EFI"; false; }
efibootmgr | grep -i limine || { echo "no Limine NVRAM entry"; false; }
# The initramfs Limine boots (limine-mkinitcpio-hook's, under the machine ID):
# what of the pool's host ID and cachefile went in (shown; the first boot is
# the real check).
for k in linux-cachyos linux-cachyos-lts; do
    img=/boot/$machine_id/$k/initramfs
    [[ -s $T$img ]] || { echo "no $img"; false; }
    echo "--- $img:"
    in_t lsinitcpio -a "$img" 2>&1 | head -n 12 || true
    in_t lsinitcpio "$img" 2>/dev/null | grep -E 'zpool\.cache|hostid' || echo "(neither etc/hostid nor etc/zfs/zpool.cache listed)"
done
echo "--- /etc/default/limine"; cat "$T/etc/default/limine"
echo "--- limine.conf"; cat "$T/boot/limine.conf"
echo "--- ESP"; find "$T/boot" -maxdepth 3 | sort
zfs list -r -o name,mountpoint,canmount,compression "$POOL"
zpool get -H compatibility,cachefile,ashift,autotrim "$POOL"

# --- finish: Calamares's cleanup, unmount, export -----------------------------------
yes | in_t pacman -Scc >/dev/null || true
rm -f "$T/etc/resolv.conf"
# What the chroot left running (pacman-key's gpg-agent and the like): every
# process whose root is the target, so that it can be unmounted.
for p in /proc/[0-9]*; do
    [[ $(readlink "$p/root" 2>/dev/null) == "$T" ]] || continue
    echo "stopping ${p#/proc/} ($(tr '\0' ' ' <"$p/cmdline"), left by the chroot)"
    kill "${p#/proc/}" 2>/dev/null || true
done
sleep 2
umount "$T/run/udev" "$T/run" "$T/dev" "$T/sys/firmware/efi/efivars" "$T/sys" "$T/proc"
umount "$T/boot"
zpool trim -w "$POOL" || true
zfs umount -a
zpool export "$POOL"
sync
echo "BACKOTTER-INSTALL-OK"
