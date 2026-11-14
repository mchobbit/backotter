#!/bin/bash
# Install KDE neon User Edition on a real machine from another running Linux
# (the BackOtter rescue stick), the way neon's own installer, Calamares, does
# "Erase disk": the live ISO's squashfs copied onto the disk, the live-session
# packages removed, fstab, a swap file, the kernel from the ISO, then GRUB
# through shim with its own NVRAM entry.  The neon arms of
# testvm/make-from-live.sh (proven on the neon24 guest) on real hardware, for
# BackShot1: docs/design/kde-neon.md, N8.  As root, on the rescue:
#
#   neon-bootstrap.sh SERIAL ISO KEYS_DIR HOSTNAME
#
#   SERIAL    the one disk to ERASE, by serial number (lsblk -dno SERIAL)
#   ISO       neon-user-desk-20261025-1825.iso, its signature already checked
#             (gpg, on the build hosts); its sha256 is checked here
#   KEYS_DIR  authorized_keys (root's login) and ssh_host_ed25519_key{,.pub}
#             (the fixed host key), BackShot1's keys/ on the backup drive
#
# Layout (UEFI, GPT), Calamares's: a 300 MiB ESP vfat at /boot/efi, ext4 / on
# the rest, no partition names, no swap partition but a 512 MiB /swapfile.
# GRUB from Ubuntu's signed packages: EFI/neon/shimx64.efi and its NVRAM
# entry, the stub grub.cfg also in EFI/ubuntu, shim on the fallback path.
# Stays a desktop install (graphical target, the image's kernel command
# line); a user "tester" in the installer's groups with a random password in
# /root/tester-password (mode 600, printed nowhere), root by key only, sshd
# and avahi (thinkpad.local) on.  Packages beyond the ISO (openssh-server,
# avahi-daemon, efibootmgr where missing) come from the online archives, so
# this needs the stick's wired network.  Prints BKO-NEON-INSTALL-OK.

set -euo pipefail
SERIAL=${1:?usage: neon-bootstrap.sh SERIAL ISO KEYS_DIR HOSTNAME}
ISO=${2:?}
KEYS=${3:?}
HOST=${4:?}
ISO_SHA256=0ed36989556a7a27bb88296c198f305d5629a294d38f5f1b704047265be31015
# Calamares's sizes (make-from-live.sh's ESP_SIZE_NEON, SWAP_FILE_MIB_NEON).
ESP_SIZE=300MiB
SWAP_FILE_MIB=512
EFI_ID=neon

die() { echo "neon-bootstrap: $*" >&2; exit 1; }
step() { echo "== $(date +%T) $*"; }
[[ $EUID -eq 0 ]] || die "run it as root"
[[ -d /sys/firmware/efi/efivars ]] || die "this system wasn't booted by UEFI"
[[ -s $ISO || -b $ISO ]] || die "no $ISO"
for f in authorized_keys ssh_host_ed25519_key ssh_host_ed25519_key.pub; do [[ -s $KEYS/$f ]] || die "no $KEYS/$f"; done
for t in sfdisk wipefs mkfs.fat mkfs.ext4 mkswap blkid blockdev udevadm sha256sum chroot base64 cmp; do
    command -v $t >/dev/null || die "this system has no $t"
done
[[ $HOST =~ ^[a-zA-Z0-9][a-zA-Z0-9-]*$ ]] || die "HOSTNAME must be a plain host name, not $HOST"
grep -qw squashfs /proc/filesystems || modprobe squashfs 2>/dev/null || true

step "checking $(basename "$ISO")"
sum=$(sha256sum "$ISO" | awk '{ print $1 }')
[[ $sum == "$ISO_SHA256" ]] || die "$ISO's sha256 is $sum, not $ISO_SHA256 (neon-user-desk-20261025-1825.iso)"

# The disk, by serial and nothing else.
mapfile -t disks < <(lsblk -dnpo NAME,SERIAL | awk -v s="$SERIAL" '$2 == s { print $1 }')
[[ ${#disks[@]} -eq 1 ]] || die "${#disks[@]} disks have serial $SERIAL"
DISK=${disks[0]}
[[ $(lsblk -dno TRAN "$DISK") != usb ]] || die "$DISK is a USB disk"
if lsblk -nro MOUNTPOINTS "$DISK" | grep -q .; then die "something on $DISK is mounted"; fi
part() { case $DISK in *[0-9]) echo "${DISK}p$1" ;; *) echo "$DISK$1" ;; esac; }

# The ISO and its squashfs, read-only, beside the target; everything here and
# under $T comes down on any exit.
tmp=$(mktemp -d /run/bko-neon.XXXXXX)
# The target under it too, not /mnt: the backup drive holding ISO and
# KEYS_DIR is often mounted below /mnt (/mnt/bs1-backup), and a target
# mounted on /mnt would hide it.
ISO_MNT=$tmp/iso SQ=$tmp/squashfs T=$tmp/target
mkdir "$ISO_MNT" "$SQ" "$T"
cleanup() {
    set +e
    if mountpoint -q $T; then umount -R $T 2>/dev/null || umount -R -l $T; fi
    mountpoint -q "$SQ" && umount "$SQ"
    mountpoint -q "$ISO_MNT" && umount "$ISO_MNT"
    rm_tmp
}
# $tmp holds the target's mount point: removed only once nothing below it is
# mounted, or rm would go through the target and its binds of /dev and /run.
rm_tmp() {
    if findmnt -rno TARGET | grep -q "^$tmp/"; then
        echo "neon-bootstrap: $tmp still has mounts; left in place" >&2
    else
        rm -rf "$tmp"
    fi
}
trap cleanup EXIT
mount -o loop,ro "$ISO" "$ISO_MNT"
image=$ISO_MNT/casper/filesystem.squashfs
if [[ ! -f $image ]]; then
    image=$(find "$ISO_MNT" -maxdepth 2 -name filesystem.squashfs | head -n 1)
    [[ -n $image ]] || die "$ISO has no casper/filesystem.squashfs"
fi
casper_dir=$(dirname "$image")
[[ -f $casper_dir/vmlinuz ]] || die "$ISO has no ${casper_dir#"$ISO_MNT"}/vmlinuz"
mount -t squashfs -o loop,ro "$image" "$SQ"
[[ -f $SQ/usr/lib/os-release ]] || die "${image#"$ISO_MNT"} holds no system"
echo "image: ${image#"$ISO_MNT"} ($(. "$SQ/usr/lib/os-release" && echo "$PRETTY_NAME $VERSION_ID"))"

step "erasing $DISK ($SERIAL, $(lsblk -dno SIZE,MODEL "$DISK"))"
lsblk -o NAME,SIZE,FSTYPE,LABEL "$DISK"
# Anything still open on it: LUKS mappings, swap.
for m in $(lsblk -lnpo NAME,TYPE "$DISK" | awk '$2 == "crypt" { print $1 }'); do cryptsetup close "$m"; done
swapoff -a 2>/dev/null || true
mapfile -t nodes < <(lsblk -lnpo NAME "$DISK" | sort -r)
wipefs -a "${nodes[@]}"
# Zero the first 4 MiB and the last 1 MiB (old GPT backup header, signatures).
sectors=$(blockdev --getsz "$DISK")
dd if=/dev/zero of="$DISK" bs=1M count=4 conv=fsync status=none
dd if=/dev/zero of="$DISK" bs=512 seek=$((sectors - 2048)) count=2048 conv=fsync status=none

step "partitioning"
# Calamares's "Erase disk" with neon's default swap choice ("file"): the ESP
# at its built-in recommended size, then root on the rest.  No partition
# names (neon sets no efiSystemPartitionName, no partitionLayout).
sfdisk --quiet "$DISK" <<EOF
label: gpt
size=$ESP_SIZE, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B
type=0FC63DAF-8483-4772-8E79-3D69D8477DE4
EOF
udevadm settle
for _ in 1 2 3 4 5; do [[ -e $(part 2) ]] && break; sleep 1; done
for i in 1 2; do dd if=/dev/zero of="$(part $i)" bs=1M count=4 conv=fsync status=none; done
ESP=$(part 1) ROOT=$(part 2)
mkfs.fat -F 32 -n EFI "$ESP" >/dev/null
mkfs.ext4 -q -F "$ROOT"
udevadm settle
uuid() { blkid -s UUID -o value "$1"; }

step "copying the live system's image (Calamares's unpackfs)"
mount -t ext4 "$ROOT" $T
t0=$(date +%s)
# -a keeps hard links, ACLs and extended attributes (file capabilities).
cp -a "$SQ/." "$T/"
echo "copy took $(( $(date +%s) - t0 )) s, $(du -sh -x $T | cut -f1)"
if command -v getcap >/dev/null; then
    caps_sq=$(getcap -r "$SQ" 2>/dev/null | wc -l)
    caps_t=$(getcap -r $T 2>/dev/null | wc -l)
    echo "file capabilities: $caps_sq in the image, $caps_t on the disk"
    [[ $caps_t -ge $caps_sq ]] || die "the copy lost file capabilities"
fi
# The installer's groups for its user, read from the image's Calamares
# settings before the remove list takes them: the desktop edition's
# (/calamares/desktop; bigscreen, mobile and the OEM ones sit beside it).
users_conf=""
for f in "$SQ/calamares/desktop/modules/users.conf" "$SQ/etc/calamares/modules/users.conf" "$SQ/usr/share/calamares/modules/users.conf"; do
    if [[ -f $f ]]; then users_conf=$f; break; fi
done
mkdir -p $T/boot/efi
mount -t vfat "$ESP" $T/boot/efi

step "fstab and the swap file"
cat >$T/etc/fstab <<EOF
# Written by BackOtter's neon-bootstrap.sh, as neon's Calamares writes it
UUID=$(uuid "$ROOT") / ext4 defaults 0 1
UUID=$(uuid "$ESP") /boot/efi vfat defaults 0 2
/swapfile swap swap defaults 0 0
EOF
cat $T/etc/fstab
# Calamares's fstab module's create_swapfile: zeros written out in full (a
# swap file can't have holes), mode 600, mkswap.
dd if=/dev/zero of=$T/swapfile bs=1M count="$SWAP_FILE_MIB" status=none
chmod 600 $T/swapfile
mkswap -q $T/swapfile

step "inside the new system"
for d in dev dev/pts proc sys run; do mount --bind "/$d" "$T/$d"; done
# The firmware's variables, for grub-install's NVRAM entry: a bind of /sys
# doesn't carry the efivarfs mounted inside it.
findmnt -rn /sys/firmware/efi/efivars >/dev/null || mount -t efivarfs efivarfs /sys/firmware/efi/efivars
mount --bind /sys/firmware/efi/efivars $T/sys/firmware/efi/efivars
# Name resolution for apt, then the image's own resolv.conf back afterwards.
if [[ -e $T/etc/resolv.conf || -L $T/etc/resolv.conf ]]; then
    mv $T/etc/resolv.conf "$tmp/resolv.conf.image"
fi
cp -L /etc/resolv.conf $T/etc/resolv.conf
printf '#!/bin/sh\nexit 101\n' >$T/usr/sbin/policy-rc.d
chmod 755 $T/usr/sbin/policy-rc.d
in_root() { chroot $T env DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1 "$@"; }

step "removing the live session's packages"
# neon's remove list, from its Calamares: the packages module's (packages.conf:
# calamares, neon-live, casper and '^live-*', an apt regex, so every package
# whose name starts with "live"), then neon-fs-cleanup's, the file system tools
# for the file systems the install doesn't use (/ is ext4: btrfs, reiserfs,
# XFS).  Only the installed ones are removed; the autoremove after it is
# neon-fs-cleanup's.  The UEFI boot packages stay.
mapfile -t live_pkgs < <({ printf '%s\n' calamares neon-live casper btrfs-progs reiserfsprogs xfsprogs
    in_root dpkg-query -W -f '${Package}\n' | grep -E '^live-*' || true; } | sort -u)
mapfile -t live_pkgs < <(in_root dpkg-query -W -f '${db:Status-Status} ${Package}\n' "${live_pkgs[@]}" 2>/dev/null |
    awk '$1 == "installed" && $2 !~ /^(shim-signed|grub-efi-amd64(-signed|-bin)?|mokutil)$/ { print $2 }' || true)
echo "Removing ${#live_pkgs[@]} live-session packages: ${live_pkgs[*]}"
if [[ ${#live_pkgs[@]} -gt 0 ]]; then
    protected=$(in_root apt-get -s purge "${live_pkgs[@]}" 2>&1 |
        sed -n '/essential packages will be removed/,/^[0-9]* upgraded/p' |
        sed '1,2d;$d' | sed 's/([^)]*)//g' | tr ' ' '\n' | grep -E '^[a-z0-9][a-z0-9.+-]+$' || true)
    [[ -z $protected ]] || die "removing the live packages would remove essential packages: $protected"
    in_root apt-get -y -q purge "${live_pkgs[@]}" >/dev/null
fi
gone=$(in_root apt-get -s --purge autoremove 2>/dev/null | awk '$1 == "Purg" { print $2 }' | paste -sd ' ' - || true)
echo "Autoremoving: ${gone:-nothing}"
in_root apt-get -y -q --purge autoremove >/dev/null

step "installing what the image lacks"
# neon's Calamares installs only grub-efi-amd64-signed and shim-signed
# (before_bootloader_context.conf, "apt install --no-upgrade"): not
# grub-efi-amd64, which would replace the image's grub-pc, so neon keeps it.
# The rest only where the image lacks them, so the image's own packages keep
# their versions and apt's marks.  avahi-daemon: BackShot1 finds the host as
# HOSTNAME.local.
extra=()
for p in openssh-server avahi-daemon grub-efi-amd64-signed shim-signed mokutil efibootmgr; do
    [[ $(in_root dpkg-query -W -f '${db:Status-Status}' "$p" 2>/dev/null) == installed ]] || extra+=("$p")
done
in_root apt-get -q update >/dev/null
echo "grub-pc grub-pc/install_devices multiselect" | in_root debconf-set-selections
if [[ ${#extra[@]} -gt 0 ]]; then
    echo "Installing ${extra[*]}"
    in_root apt-get -y -q install "${extra[@]}" >/dev/null
else
    echo "Installing nothing: the image has them all"
fi

step "identity: host name, machine id, locale, time zone"
echo "$HOST" >$T/etc/hostname
printf '127.0.0.1\tlocalhost\n127.0.1.1\t%s\n::1\tlocalhost ip6-localhost ip6-loopback\n' "$HOST" >$T/etc/hosts
# A fresh machine id (Calamares's machineid module: systemd's, and dbus's as a
# link to it).
rm -f $T/etc/machine-id $T/var/lib/dbus/machine-id
in_root systemd-machine-id-setup >/dev/null 2>&1 || in_root dbus-uuidgen --ensure=/etc/machine-id
if [[ -d $T/var/lib/dbus ]]; then ln -sf /etc/machine-id $T/var/lib/dbus/machine-id; fi
# Calamares's localecfg: /etc/default/locale, the locale generated; the time
# zone as /etc/localtime and /etc/timezone.
in_root locale-gen en_US.UTF-8 >/dev/null
in_root update-locale LANG=en_US.UTF-8
[[ -e $T/usr/share/zoneinfo/Europe/Paris ]] || die "the image has no zoneinfo for Europe/Paris"
ln -sfn /usr/share/zoneinfo/Europe/Paris $T/etc/localtime
echo Europe/Paris >$T/etc/timezone

step "users and SSH"
# The live session's remnants: casper makes its user at boot, in the live
# overlay, so the image should have none; any regular account in it goes.
mapfile -t stale < <(awk -F: '$3 >= 1000 && $3 < 60000 { print $1 }' $T/etc/passwd)
for u in "${stale[@]}"; do echo "removing the image's account $u"; in_root userdel -r "$u" 2>/dev/null || in_root userdel "$u"; done
rm -f $T/etc/sudoers.d/casper
# The live CD's self-check, where it outlived casper: it would fail at every
# boot, looking for a CD.
if [[ -e $T/usr/lib/systemd/system/casper-md5check.service ]]; then
    in_root systemctl mask casper-md5check.service >/dev/null
fi
# tester, in Calamares's defaultGroups (users.conf) that exist on the
# target; Kubuntu's list where the image's settings had none.
groups=()
if [[ -n $users_conf ]]; then
    echo "groups from ${users_conf#"$SQ"}"
    mapfile -t groups < <(awk '
        /^defaultGroups:/ { on = 1; next }
        on && /^[^[:space:]#]/ { on = 0 }
        on && /^[[:space:]]*-/ {
            s = $0; sub(/^[[:space:]]*-[[:space:]]*(name:[[:space:]]*)?["'"'"']?/, "", s)
            if (match(s, /^[a-z_][a-z0-9_-]*/)) print substr(s, 1, RLENGTH)
        }' "$users_conf")
fi
[[ ${#groups[@]} -gt 0 ]] || groups=(adm cdrom dip lpadmin plugdev sambashare sudo)
[[ " ${groups[*]} " == *" sudo "* ]] || groups+=(sudo)
have=()
for g in "${groups[@]}"; do
    if in_root getent group "$g" >/dev/null; then have+=("$g"); else echo "no group $g on the target: skipped"; fi
done
[[ ${#have[@]} -gt 0 ]] || die "none of the groups ${groups[*]} is on the target"
in_root useradd -m -s /bin/bash -c tester -G "$(IFS=,; echo "${have[*]}")" tester
umask 077
pw=$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | cut -c 1-20)
printf '%s\n' "$pw" >$T/root/tester-password
umask 022
chmod 600 $T/root/tester-password
printf 'tester:%s\n' "$pw" | in_root chpasswd
unset pw
install -d -m 700 $T/root/.ssh
install -m 600 "$KEYS/authorized_keys" $T/root/.ssh/authorized_keys
install -d -m 755 $T/etc/ssh/sshd_config.d
printf '# BackShot1: root by key only (neon-bootstrap.sh)\nPermitRootLogin prohibit-password\n' \
    >$T/etc/ssh/sshd_config.d/10-backshot1.conf
# The fixed host key, after the apt steps (openssh-server's postinst made its
# own); the other key types fresh.
rm -f $T/etc/ssh/ssh_host_*
install -m 600 -o 0 -g 0 "$KEYS/ssh_host_ed25519_key" $T/etc/ssh/ssh_host_ed25519_key
install -m 644 -o 0 -g 0 "$KEYS/ssh_host_ed25519_key.pub" $T/etc/ssh/ssh_host_ed25519_key.pub
in_root ssh-keygen -A >/dev/null
in_root systemctl enable ssh >/dev/null 2>&1 || true
in_root systemctl enable avahi-daemon >/dev/null 2>&1 || true

step "the kernel and the initramfs (Calamares's boot_deploy, boot_reconfigure)"
# The squashfs has the kernel's modules and initrd but no /boot/vmlinuz-KVER
# (casper keeps the kernel beside it, as casper/vmlinuz), so it comes from
# the ISO, and its package's postinst runs again for the symlinks and hooks,
# without an initramfs (INITRD=No; update-initramfs below makes it).
kvers=($T/usr/lib/modules/*/)
[[ ${#kvers[@]} -eq 1 && -d ${kvers[0]} ]] ||
    die "the image has ${#kvers[@]} kernels in /usr/lib/modules; neon's live image has one"
kver=$(basename "${kvers[0]}")
cp --preserve=timestamps "$casper_dir/vmlinuz" "$T/boot/vmlinuz-$kver"
chmod 644 "$T/boot/vmlinuz-$kver"
chown 0:0 "$T/boot/vmlinuz-$kver"
echo "boot/vmlinuz-$kver: copied from ${casper_dir#"$ISO_MNT"}/vmlinuz"
in_root env INITRD=No dpkg-reconfigure -fnoninteractive "linux-image-$kver" >/dev/null
# Calamares's grubcfg (overwrite: false, on an existing /etc/default/grub):
# GRUB_DISTRIBUTOR becomes the branding's bootloaderEntryName,
# "${PRETTY_NAME}".  The kernel command line stays the image's.
pretty=$(. $T/usr/lib/os-release && echo "$PRETTY_NAME")
sed -i "s|^GRUB_DISTRIBUTOR=.*|GRUB_DISTRIBUTOR='$pretty'|" $T/etc/default/grub
grep -q '^GRUB_DISTRIBUTOR=' $T/etc/default/grub || echo "GRUB_DISTRIBUTOR='$pretty'" >>$T/etc/default/grub
in_root update-initramfs -c -k all >/dev/null 2>&1 || in_root update-initramfs -u -k all >/dev/null
[[ -s $T/boot/initrd.img-$kver ]] || die "no /boot/initrd.img-$kver"

step "GRUB through shim, with its NVRAM entry"
in_root grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=$EFI_ID --uefi-secure-boot
in_root update-grub 2>/dev/null
grep -q '^[[:space:]]*linux[[:space:]]' $T/boot/grub/grub.cfg || die "/boot/grub/grub.cfg boots no kernel"
efi=$T/boot/efi/EFI
for f in shimx64.efi grubx64.efi grub.cfg; do
    [[ -s $efi/$EFI_ID/$f ]] || die "grub-install put no EFI/$EFI_ID/$f on the EFI system partition"
done
partuuid=$(blkid -s PARTUUID -o value "$ESP")
entries=$(in_root efibootmgr -v)
grep -iF "\\EFI\\$EFI_ID\\shimx64.efi" <<<"$entries" | grep -qi "$partuuid" ||
    die "grub-install made no NVRAM entry for EFI/$EFI_ID/shimx64.efi on $ESP ($partuuid)"
# The fallback path too, as make-from-live.sh lays it: shim as
# EFI/BOOT/BOOTX64.EFI, with fbx64.efi (and MokManager) beside it and
# EFI/neon/BOOTX64.CSV, so the disk boots even where the NVRAM entry is lost.
# Whatever grub-install left out comes from the packages, and the log says so.
mkdir -p "$efi/BOOT"
if ! cmp -s "$efi/$EFI_ID/shimx64.efi" "$efi/BOOT/BOOTX64.EFI"; then
    echo "EFI/BOOT/BOOTX64.EFI: shim, copied from EFI/$EFI_ID"
    cp "$efi/$EFI_ID/shimx64.efi" "$efi/BOOT/BOOTX64.EFI"
fi
# The signed helpers: under Secure Boot shim runs only what its vendor key
# (Canonical's) signed.  Paths as the target sees them, so absolute symlinks
# in the image resolve inside it.  MokManager may be missing.
for f in fbx64.efi mmx64.efi; do
    [[ -s $efi/BOOT/$f ]] && continue
    src=""
    for s in "/boot/efi/EFI/$EFI_ID/$f" "/usr/lib/shim/$f.signed" "/usr/lib/shim/$f"; do
        if in_root test -s "$s"; then src=$s; break; fi
    done
    if [[ -z $src ]]; then
        [[ $f == mmx64.efi ]] && continue
        die "no $f to put in EFI/BOOT (shim-signed, shim-helpers-amd64-signed)"
    fi
    echo "EFI/BOOT/$f: copied from $src"
    in_root cp -L "$src" "/boot/efi/EFI/BOOT/$f"
done
if [[ ! -s $efi/$EFI_ID/BOOTX64.CSV ]]; then
    in_root test -s /usr/lib/shim/BOOTX64.CSV || die "no BOOTX64.CSV for EFI/$EFI_ID (shim)"
    echo "EFI/$EFI_ID/BOOTX64.CSV: copied from /usr/lib/shim"
    in_root cp -L /usr/lib/shim/BOOTX64.CSV "/boot/efi/EFI/$EFI_ID/BOOTX64.CSV"
fi
# Calamares's after_bootloader: the stub grub.cfg also in EFI/ubuntu, where
# Ubuntu's signed GRUB (prefix /EFI/ubuntu built in) may look for it.
mkdir -p "$efi/ubuntu"
cp "$efi/$EFI_ID/grub.cfg" "$efi/ubuntu/"
echo "EFI/ubuntu/grub.cfg: copied from EFI/$EFI_ID"

step "tidying the chroot"
in_root apt-get clean
rm -f $T/usr/sbin/policy-rc.d $T/etc/resolv.conf
if [[ -e $tmp/resolv.conf.image || -L $tmp/resolv.conf.image ]]; then mv "$tmp/resolv.conf.image" $T/etc/resolv.conf; fi
cmp -s "$KEYS/ssh_host_ed25519_key" $T/etc/ssh/ssh_host_ed25519_key &&
    cmp -s "$KEYS/ssh_host_ed25519_key.pub" $T/etc/ssh/ssh_host_ed25519_key.pub ||
    die "/etc/ssh/ssh_host_ed25519_key isn't the fixed host key any more"

step "facts"
lsblk -o NAME,SIZE,FSTYPE,PARTTYPE,UUID "$DISK"
grep -E '^(PRETTY_NAME|VERSION_ID)=' $T/usr/lib/os-release
ls -l $T/boot | sed 's|^|    |'
grep -E '^GRUB_(DISTRIBUTOR|CMDLINE_LINUX_DEFAULT|CMDLINE_LINUX)=' $T/etc/default/grub
grep -E '^\s*(search|linux|initrd)' $T/boot/grub/grub.cfg | head -n 6
find $T/boot/efi -type f | sed "s|^$T||" | sort
in_root efibootmgr -v
echo "default target: $(in_root systemctl get-default 2>&1 || true)"
for u in ssh avahi-daemon NetworkManager sddm; do echo "$u: $(in_root systemctl is-enabled "$u" 2>&1 || true)"; done
in_root id tester
ls -l $T/swapfile $T/root/tester-password
ssh-keygen -lf $T/etc/ssh/ssh_host_ed25519_key.pub 2>/dev/null || cat $T/etc/ssh/ssh_host_ed25519_key.pub
cat $T/etc/default/locale; readlink $T/etc/localtime
ls $T/etc/netplan 2>/dev/null || echo "no /etc/netplan"
grep -rIl -i '^\s*User=' $T/etc/sddm.conf $T/etc/sddm.conf.d 2>/dev/null | sed "s|^$T|autologin set in |" || true
df -h $T $T/boot/efi

step "unmounting"
trap - EXIT
umount -R $T
umount "$SQ"
umount "$ISO_MNT"
rm_tmp
sync
echo BKO-NEON-INSTALL-OK
