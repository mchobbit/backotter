#!/usr/bin/env bash
# Install KDE Linux onto a whole disk the way its own installer does, from a
# Linux rescue stick (or any Linux with sgdisk, cryptsetup, btrfs-progs,
# mkfs.vfat, the erofs and loop modules, util-linux's X-mount.subdir):
# docs/design/kde-linux.md, "How they are built".  It replays Calamares 3.4's
# exec sequence on the KDE Linux ISO (settings.conf): partition ("Erase
# disk", partition.conf), mount, subvol (subvol/main.py, KDE Linux's own
# module), machineid, displaymanager, hwclock, shellprocess@uki, umount.
# Calamares makes no users, fstab or boot loader entry: the user is made at
# the first boot by plasma-setup.
#
# Usage, as root:
#   install.sh [--luks KEYFILE] [--version V | --image FILE] [--guest KEYS_DIR] DISK IMAGE_DIR HOSTNAME
#
# DISK is erased.  IMAGE_DIR holds either a sysupdate set (kde-linux_V.efi,
# kde-linux_V_root-x86-64.erofs, SHA256SUMS and SHA256SUMS.gpg, as
# https://kde-linux-images.s3.kde.org/testing/sysupdate/v2/ serves them;
# several versions may be there, --version picks one, else the only one) or
# one kde-linux_V.iso (with SHA256SUMS and its .gpg beside it), whose p2 is
# the same erofs byte for byte.  The erofs's SHA-256 is checked against
# SHA256SUMS, and SHA256SUMS's signature against the image's own keyring
# (/usr/lib/systemd/import-pubring.pgp: circular, see the spec's open
# question 3; the builders pin the SHA-256s themselves).  --image FILE takes
# the erofs or the ISO from FILE instead (a file or a block device: the
# guest build hands them to the stick as read-only disks), IMAGE_DIR then
# only for the sums.  The UKI installed
# is the erofs's own usr/share/factory/boot one (kde-linux_V+3.efi), never
# the set's .efi or the ISO's live ESP: exactly the installer's.
#
# --luks KEYFILE: "Encrypt system": LUKS2 on the root partition with the
# installer's parameters (--sector-size 4096 --key-size 512 --hash sha512),
# its passphrase KEYFILE's bytes (no final newline wanted), opened as root.
#
# --guest KEYS_DIR: BackOtter's own steps, after the installer's (a real
# install has none of them): KEYS_DIR/authorized_keys for root (root stays
# locked, as the image has it: sshd's UsePAM yes lets a key in), and if present
# KEYS_DIR/ssh_host_ed25519_key(.pub) as sshd's host key; sshd pulled in
# by a multi-user.target drop-in (a symlink would not survive the first
# boot's /etc migration: see below); /etc/plasma-setup-done (plasma-setup's
# own flag: its wizard never runs; no user is made here: useradd in this
# chroot has no /etc/default/useradd, which the running system's /etc takes
# from the image's factory tree, so a home made here is no subvolume; the
# guest build makes its user at the first boot, as plasma-setup would); and
# a global UKI command-line addon,
# loader/addons/bko-console.addon.efi, adding console=ttyS0,115200n8, built
# with the image's ukify and addon stub.  Without --guest the disk is as
# the installer leaves it (plasma-setup makes the user at the first boot).
#
# HOSTNAME goes to /etc/hostname (the installer writes none: its image's
# DEFAULT_HOSTNAME is used).  Only with --guest.
#
# Prints BACKOTTER-INSTALL-OK at the end; with --guest, a line on what ran
# in /var/log/bko-kdelinux-install.log on the installed system.

set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C

ESP_SIZE=4098M              # partition.conf's efi.recommendedSize
ROOT_TYPE=4f68bce3-e8cd-4db1-96e7-fbcaf984b709   # root-x86-64
MOUNT_OPTS=compress=zstd:-4 # mount.conf's btrfs options (with defaults)

die() { echo "install.sh: $*" >&2; exit 1; }
say() { echo "== $*"; }

keyfile="" version="" guest="" image=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --luks) keyfile=$(realpath "$2"); shift 2 ;;
        --version) version=$2; shift 2 ;;
        --guest) guest=$(realpath "$2"); shift 2 ;;
        --image) image=$(realpath "$2"); shift 2 ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
[[ $# -eq 3 ]] || die "usage: install.sh [--luks KEYFILE] [--version V | --image FILE] [--guest KEYS_DIR] DISK IMAGE_DIR HOSTNAME"
disk=$1 dir=$(realpath "$2") hostname=$3
[[ $(id -u) == 0 ]] || die "run as root"
[[ -b $disk ]] || die "$disk is not a block device"
[[ $hostname =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || die "bad host name '$hostname'"
[[ -z $keyfile || -s $keyfile ]] || die "no key file $keyfile"
if [[ -n $guest ]]; then
    [[ -s $guest/authorized_keys ]] || die "no $guest/authorized_keys"
fi
for c in python3 sgdisk sfdisk wipefs mkfs.vfat mkfs.btrfs btrfs blkid losetup sha256sum udevadm chroot; do
    command -v $c >/dev/null || die "missing $c"
done
[[ -z $keyfile ]] || command -v cryptsetup >/dev/null || die "missing cryptsetup"
modprobe erofs 2>/dev/null; modprobe loop 2>/dev/null; modprobe btrfs 2>/dev/null
grep -qw erofs /proc/filesystems || die "this kernel has no erofs"

t0=$SECONDS
work=$(mktemp -d /run/bko-kdelinux.XXXXXX)
top=$work/top root=$work/root src=$work/src
mkdir -p "$top" "$root" "$src"
isoloop="" mapped="" part=""
cleanup() {
    set +e
    for m in "$root" "$top" "$src"; do
        mountpoint -q "$m" && umount -R "$m"
    done
    [[ -z $isoloop ]] || losetup -d "$isoloop"
    [[ -z $mapped ]] || cryptsetup close root
    rmdir "$top" "$root" "$src" 2>/dev/null
    rmdir "$work" 2>/dev/null
}
trap cleanup EXIT

# --- The image: a sysupdate set's erofs, or the ISO's p2 ---------------------------
if [[ -z $image ]]; then
    shopt -s nullglob
    isos=("$dir"/kde-linux_*.iso)
    sets=("$dir"/kde-linux_*_root-x86-64.erofs)
    shopt -u nullglob
    if [[ -n $version ]]; then
        [[ -s $dir/kde-linux_${version}_root-x86-64.erofs ]] || die "no kde-linux_${version}_root-x86-64.erofs in $dir"
        image=$dir/kde-linux_${version}_root-x86-64.erofs
    elif [[ ${#sets[@]} -eq 1 ]]; then
        image=${sets[0]}
    elif [[ ${#sets[@]} -gt 1 ]]; then
        die "several versions in $dir: say which with --version"
    elif [[ ${#isos[@]} -eq 1 ]]; then
        image=${isos[0]}
    else
        die "$dir holds neither one sysupdate set nor one ISO"
    fi
fi
[[ -f $image || -b $image ]] || die "no image $image"
# An ISO is a GPT disk (systemd-repart --el-torito); an erofs has none.
iso=no
[[ $(blkid -p -o value -s PTTYPE "$image" 2>/dev/null) == gpt ]] && iso=yes
# The sums that list the erofs: the set's SHA256SUMS; beside an ISO, whose
# own SHA256SUMS lists only the .iso, the sysupdate one saved as
# sysupdate-v2-SHA256SUMS (as rebuild-inputs/kdelinux keeps it), if there.
sums=$dir/SHA256SUMS
[[ $iso == yes && -s $dir/sysupdate-v2-SHA256SUMS ]] && sums=$dir/sysupdate-v2-SHA256SUMS
[[ -s $sums && -s $sums.gpg ]] || die "no $sums(.gpg)"
if [[ $iso == yes ]]; then
    # p2 (KDELinuxLive, root-x86-64): the erofs, at its GPT offset.
    isoloop=$(losetup -P -r -f --show "$image")
    udevadm settle
    for p in "$isoloop"p*; do
        [[ $(blkid -p -o value -s PART_ENTRY_TYPE "$p" 2>/dev/null) == "$ROOT_TYPE" ]] && part=$p
    done
    [[ -n $part ]] || die "$image has no root-x86-64 partition"
    srcdev=$part
else
    srcdev=$image
fi
mount -t erofs -o ro "$srcdev" "$src" || die "could not mount $srcdev as erofs"
V=$(sed -n 's/^IMAGE_VERSION=//p' "$src/usr/lib/os-release" | tr -d '"')
[[ $V =~ ^[0-9]{12}$ ]] || die "no IMAGE_VERSION in the image's os-release"
grep -qx 'ID=kde-linux' "$src/usr/lib/os-release" || die "the image isn't KDE Linux"
[[ $image != */kde-linux_*_root-x86-64.erofs || $image == */kde-linux_${V}_root-x86-64.erofs ]] ||
    die "$image holds version $V"
uki=$(cd "$src/usr/share/factory/boot/EFI/Linux" && ls kde-linux_"$V"*.efi)
[[ $uki == "kde-linux_$V+3.efi" ]] || say "note: the factory UKI is $uki, not kde-linux_$V+3.efi"
say "KDE Linux $V from $image${part:+ (its p2)}"
# The signature, with the image's own keyring (circular), then the hash.
if command -v gpgv >/dev/null; then
    gpgv --keyring "$src/usr/lib/systemd/import-pubring.pgp" "$sums.gpg" "$sums" 2>&1 | sed 's/^/gpgv: /' ||
        die "SHA256SUMS's signature doesn't check with the image's keyring"
else
    say "note: no gpgv here, SHA256SUMS's signature not checked"
fi
want=$(awk -v f="kde-linux_${V}_root-x86-64.erofs" '$2 == f || $2 == "*" f { print $1 }' "$sums")
[[ $want =~ ^[0-9a-f]{64}$ ]] || die "SHA256SUMS has no kde-linux_${V}_root-x86-64.erofs"
size=$(blockdev --getsize64 "$srcdev" 2>/dev/null || stat -c %s "$srcdev")
umount "$src"

# --- partition (Calamares' "Erase disk", partition.conf) --------------------------
say "partitioning $disk"
for p in $(lsblk -lnpo NAME "$disk" | tail -n +2); do
    mountpoint -q "$p" 2>/dev/null && die "$p is mounted"
done
wipefs -a -q "$disk"
sgdisk --zap-all "$disk" >/dev/null
# The layout Calamares' "Erase disk" made on a 64 GiB disk (the K1 diff,
# docs/design/kde-linux.md "What K1 found"): first usable LBA 2048, the ESP
# at 2 MiB, 4098 MiB, named ESP; KDELinux from its end to the last usable
# LBA less 32 sectors, its end rounded down to 4 KiB (512-byte sectors
# assumed, as the installer's 4098M is).
[[ $(blockdev --getss "$disk") == 512 ]] || die "$disk hasn't 512-byte sectors"
last=$(( $(blockdev --getsz "$disk") - 34 ))
esp_start=4096 esp_size=$(( ${ESP_SIZE%M} * 2048 ))
root_start=$((esp_start + esp_size))
root_end=$(( (last - 32 + 1) / 8 * 8 - 1 ))
sfdisk -q "$disk" <<EOT
label: gpt
first-lba: 2048
start=$esp_start, size=$esp_size, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name="ESP"
start=$root_start, size=$((root_end - root_start + 1)), type=$ROOT_TYPE, name="KDELinux"
EOT
partprobe "$disk" 2>/dev/null || blockdev --rereadpt "$disk" 2>/dev/null || true
udevadm settle
esp=$(lsblk -lnpo NAME "$disk" | sed -n 2p) rootpart=$(lsblk -lnpo NAME "$disk" | sed -n 3p)
[[ -b $esp && -b $rootpart ]] || die "no partitions on $disk"
mkfs.vfat -F 32 "$esp" >/dev/null     # no label, as Calamares
dev=$rootpart
if [[ -n $keyfile ]]; then
    say "LUKS2 on $rootpart"
    cryptsetup luksFormat -q --type luks2 --sector-size 4096 --key-size 512 --hash sha512 \
        --key-file "$keyfile" "$rootpart"
    cryptsetup open --key-file "$keyfile" "$rootpart" root
    mapped=yes dev=/dev/mapper/root
fi
mkfs.btrfs -q -f -L KDELinux "$dev" >/dev/null

# --- subvol (subvol/main.py, outside the chroot) ----------------------------------
say "subvolumes"
mount -o "rw,$MOUNT_OPTS" "$dev" "$top"
btrfs quota enable --simple "$top"
btrfs subvolume create "$top/@system" >/dev/null
btrfs subvolume create "$top/@system/etc" >/dev/null
for d in boot proc sys dev run usr home; do mkdir -p "$top/@system/$d"; done

say "copying the image to kde-linux_$V.erofs ($((size >> 20)) MiB)"
# subvol/main.py's _copy_image: 16 MiB reads and writes, then fsync (dd or
# tee's smaller writes left btrfs's no-compress flag, lsattr's m, on the
# file, which the installer's copy hasn't).
got=$(python3 - "$srcdev" "$top/kde-linux_$V.erofs" <<'PY'
import hashlib, os, sys
fd = os.open(sys.argv[1], os.O_RDONLY); h = hashlib.sha256()
with open(sys.argv[2], "wb") as out:
    while chunk := os.read(fd, 16 << 20):
        out.write(chunk); h.update(chunk)
    out.flush(); os.fsync(out.fileno())
print(h.hexdigest())
PY
)
[[ $got == "$want" ]] || die "the erofs's SHA-256 is $got, SHA256SUMS says $want"
[[ $(stat -c %s "$top/kde-linux_$V.erofs") == "$size" ]] || die "the copy has the wrong size"
[[ -z $isoloop ]] || { losetup -d "$isoloop"; isoloop=""; }

say "the mount stack"
mount -o "subvol=@system,$MOUNT_OPTS" "$dev" "$root"
mount -t proc proc "$root/proc"
mount -t sysfs sys "$root/sys"
mount -o bind /dev "$root/dev"
mount -t tmpfs tmpfs "$root/run"
mkdir -p "$root/run/udev"
mount -o bind /run/udev "$root/run/udev"
[[ -d $root/sys/firmware/efi/efivars ]] && mount -t efivarfs efivarfs "$root/sys/firmware/efi/efivars" || true
mount -o ro,X-mount.subdir=usr "$top/kde-linux_$V.erofs" "$root/usr"
mount "$esp" "$root/boot"
# Everything below that the installer runs in its live system (the same
# image) runs here in a chroot of the target, with the image's own tools.
in_root() { chroot "$root" "$@"; }

# systemd-nspawn -D root true, only for base_filesystem_create(): the
# rescue stick has no nspawn, so the same table (src/shared/base-filesystem.c)
# by hand: links only where their target exists.
say "the base file system"
for l in bin:usr/bin lib:usr/lib sbin:usr/sbin lib64:usr/lib64; do
    n=${l%%:*} t=${l#*:}
    [[ -e $root/$n || -L $root/$n ]] || { [[ -e $root/$t ]] && ln -s "$t" "$root/$n"; }
done
for d in root:0750 var:0755 etc:0755 proc:0555 sys:0555 dev:0555 run:0555 tmp:1777; do
    [[ -e $root/${d%%:*} ]] || install -d -m "${d#*:}" "$root/${d%%:*}"
done

say "sysusers, the plasma-setup marker, presets, tmpfiles"
in_root systemd-sysusers
install -d -m 0755 "$root/var/lib/kde-linux"
touch "$root/var/lib/kde-linux/installed-with-plasma-setup"
chmod 0644 "$root/var/lib/kde-linux/installed-with-plasma-setup"
in_root systemctl preset-all
in_root systemctl preset-all --global
in_root systemd-tmpfiles --exclude-prefix=/usr --create || true

# machineid (systemd: true, dbus: true, dbus-symlink: true).
say "machine-id"
in_root systemd-machine-id-setup
install -d -m 0755 "$root/var/lib/dbus"
ln -sfn /etc/machine-id "$root/var/lib/dbus/machine-id"

# displaymanager (plasmalogin; no user, so autologin unset; the default
# desktop's .desktop name as the session): the module's own configparser
# write of /etc/plasmalogin.conf.
say "plasmalogin.conf"
in_root python3 - <<'PY'
import configparser, os
p = "/etc/plasmalogin.conf"
c = configparser.ConfigParser(strict=False)
c.optionxform = str
if os.path.isfile(p):
    c.read(p)
if "Autologin" not in c:
    c.add_section("Autologin")
if c.has_option("Autologin", "User"):
    c.remove_option("Autologin", "User")
c.set("Autologin", "Session", "plasma")
with open(p, "w") as f:
    c.write(f, space_around_delimiters=False)
PY

# hwclock --systohc --utc (failures ignored, as the module does).
in_root hwclock --systohc --utc 2>/dev/null || in_root hwclock --systohc --utc --directisa 2>/dev/null || true

# shellprocess@uki, in the chroot.
say "the UKI and systemd-boot"
in_root sh -c 'cp -rfv /usr/share/factory/boot/* /boot/' | sed 's/^/  /'
in_root bootctl install 2>&1 | sed 's/^/  bootctl: /'

# --- BackOtter's own steps -----------------------------------------------------------
if [[ -n $guest ]]; then
    say "the guest's own steps"
    echo "$hostname" >"$root/etc/hostname"
    install -d -m 0700 "$root/root/.ssh"
    install -m 0600 "$guest/authorized_keys" "$root/root/.ssh/authorized_keys"
    if [[ -s $guest/ssh_host_ed25519_key ]]; then
        install -d -m 0755 "$root/etc/ssh"
        install -m 0600 "$guest/ssh_host_ed25519_key" "$root/etc/ssh/ssh_host_ed25519_key"
        install -m 0644 "$guest/ssh_host_ed25519_key.pub" "$root/etc/ssh/ssh_host_ed25519_key.pub"
    fi
    # etc-mstack's first migration (the first boot) carries @system/etc's
    # regular files into the new /etc layer, not its symlinks (K1 found
    # sshd's enablement and default.target gone), so sshd is pulled in by a
    # drop-in, a regular file, here; make-kdelinux.sh enables it and sets
    # multi-user.target as the default over SSH at the first boot, and
    # removes the drop-in.
    install -d -m 0755 "$root/etc/systemd/system/multi-user.target.d"
    printf '[Unit]\nWants=sshd.service\n' >"$root/etc/systemd/system/multi-user.target.d/bko-sshd.conf"
    touch "$root/etc/plasma-setup-done"
    install -d -m 0755 "$root/boot/loader/addons"
    in_root ukify build --stub /usr/lib/systemd/boot/efi/addonx64.efi.stub \
        --cmdline 'console=ttyS0,115200n8' --output /boot/loader/addons/bko-console.addon.efi 2>&1 | sed 's/^/  ukify: /'
    [[ -s $root/boot/loader/addons/bko-console.addon.efi ]] || die "ukify made no addon"
fi

# What the installer's tools would have said, run here only to see that the
# rescue stick's own systemd (older than the image's) accepts the image's
# sysusers, presets and tmpfiles too: nothing is written (--dry-run).
for t in "systemd-sysusers --root=$root --dry-run" "systemctl --root=$root preset-all --dry-run" \
         "systemd-tmpfiles --root=$root --exclude-prefix=/usr --create --dry-run"; do
    if command -v ${t%% *} >/dev/null; then
        if out=$($t 2>&1 >/dev/null); then rc=0; else rc=$?; fi
        echo "stick-tools: $t: exit $rc, problem lines: $(grep -ciE 'unknown|invalid|failed|error' <<<"$out" || true)"
        { grep -iE 'unknown|invalid|failed|error' <<<"$out" || true; } | head -n 5 | sed 's/^/stick-tools:   /'
    fi
done

[[ -z $guest ]] || { echo "install.sh: KDE Linux $V from ${image##*/} onto $disk, $(date -u +%FT%TZ)"
  echo "luks=${keyfile:+yes} hostname=$hostname"; } >"$root/var/log/bko-kdelinux-install.log"
say "unmounting"
sync
umount -R "$root"
umount "$top"
[[ -z $mapped ]] || { cryptsetup close root; mapped=""; }
say "install.sh took $((SECONDS - t0)) s"
echo BACKOTTER-INSTALL-OK
