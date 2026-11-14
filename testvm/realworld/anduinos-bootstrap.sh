#!/bin/bash
# Install AnduinOS 2 on a real machine from another running Linux (the
# BackOtter rescue stick), with AnduinOS's own installer backend: plan.py
# makes the GUI's "erase disk, Btrfs" InstallPlan and
# anduinos-installer-executor runs it, in a chroot of the ISO's live root,
# as the anduinos2 guest is built (testvm/anduinos/install.sh).  A7's
# groundwork (docs/design/anduinos.md, "What A7 found").  As root, on the
# rescue:
#
#   anduinos-bootstrap.sh SERIAL ISO KEYS_DIR [HOSTNAME [TIMEZONE]]
#
#   SERIAL    the one disk to ERASE, by serial number (lsblk -dno SERIAL)
#   ISO       AnduinOS-2.0.5-amd64.iso (its SHA-256 is checked against the
#             one pinned here and in make-anduinos.sh), on a disk that isn't
#             SERIAL: its contents are copied once beside it, as ISO's name
#             with .tree for .iso, and that copy is the installer's /cdrom
#   KEYS_DIR  authorized_keys (root's login) and ssh_host_ed25519_key{,.pub}
#             (the fixed host key), BackShot1's keys/ on the backup drive;
#             the desktop user's password is kept there too, in
#             anduinos-password (made on the first run, 0600)
#   HOSTNAME  default thinkpad; TIMEZONE default Europe/Paris
#
# What the installer makes (its erase-disk defaults, "What A1 found"): GPT,
# a 2 MiB BIOS boot partition (it runs grub-install --target=i386-pc too), a
# 1 GiB ESP at /boot/efi, swap, the rest Btrfs labelled AnduinOS with @root,
# @home, @log, @snapshots, @containers and @libvirt, and the Disk Snapshots
# Manager's two read-only "New OS" baselines; a MOK made on the machine,
# GRUB signed with it, shim, MokManager and GRUB in EFI/AnduinOS and
# EFI/BOOT, and the NVRAM entry "AnduinOS" (\EFI\AnduinOS\shimx64.efi); the
# desktop user bko.  Then only: root's authorized_keys (root's password
# stays locked), the fixed host key, ssh.service enabled (the installer
# disables it), avahi-daemon enabled if it isn't.  The snapshot manager's
# scheduler timer and apt hook stay as installed.
#
# The chroot: the live root (LiveOS/rootfs.squashfs) under a tmpfs overlay;
# /cdrom the ISO's copy, bound read-only, and /run/anduinos-live/rootfs.squashfs
# its squashfs, bound, as the live system's dracut module lays them out (the
# installer's media check hashes /cdrom by its md5sum.txt, and wants the
# source image to be that file).  The copy has to be on a disk: the
# installer refuses to run unless it can tell which disk holds the live
# media, and refuses a loop device.  /dev, /proc and /sys (with efivarfs,
# for the NVRAM entry) from the rescue, a /run of its own with the rescue's
# udev database.  Prints BKO-ANDUINOS-INSTALL-OK.

set -euo pipefail
SERIAL=${1:?usage: anduinos-bootstrap.sh SERIAL ISO KEYS_DIR [HOSTNAME [TIMEZONE]]}
ISO=${2:?}
KEYS=${3:?}
HOST=${4:-thinkpad}
TZONE=${5:-Europe/Paris}
ISO_SHA256=4b5c1778bbf54be7fc41202af576072ebf0931e776c35ce1b5795deb7169e810
USERNAME=bko
W=/run/bko-anduinos
HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)

die() { echo "anduinos-bootstrap: $*" >&2; exit 1; }
step() { echo "== $(date +%T) $*"; }
[[ $EUID -eq 0 ]] || die "run it as root"
[[ -d /sys/firmware/efi/efivars ]] || die "this system wasn't booted by UEFI"
[[ -s $ISO ]] || die "no $ISO"
ISO=$(readlink -f "$ISO")
[[ $ISO == *.iso ]] || die "$ISO isn't named *.iso"
TREE=${ISO%.iso}.tree
[[ $HOST =~ ^[a-z0-9][a-z0-9-]*$ ]] || die "HOSTNAME \"$HOST\" isn't a plain host name"
[[ $TZONE =~ ^[A-Za-z_]+(/[A-Za-z0-9_+-]+)*$ ]] || die "TIMEZONE \"$TZONE\" isn't a time zone's name"
for f in authorized_keys ssh_host_ed25519_key ssh_host_ed25519_key.pub; do [[ -s $KEYS/$f ]] || die "no $KEYS/$f"; done
PLAN_PY=
for p in "$HERE/plan.py" "$HERE/../anduinos/plan.py"; do [[ -s $p ]] && { PLAN_PY=$p; break; }; done
[[ -n $PLAN_PY ]] || die "no plan.py beside this script or in ../anduinos/"
for t in sha256sum wipefs blockdev chroot lsblk findmnt efibootmgr systemctl btrfs; do
    command -v $t >/dev/null || die "this system has no $t"
done
grep -qw overlay /proc/filesystems || modprobe overlay || die "no overlayfs"
grep -qw squashfs /proc/filesystems || modprobe squashfs || die "no squashfs"
! findmnt -rn "$W" >/dev/null || die "$W is mounted already: an earlier run's? umount -R $W"

# The disk, by serial and nothing else.
mapfile -t disks < <(lsblk -dnpo NAME,SERIAL | awk -v s="$SERIAL" '$2 == s { print $1 }')
[[ ${#disks[@]} -eq 1 ]] || die "${#disks[@]} disks have serial $SERIAL"
DISK=${disks[0]}
[[ $(lsblk -dno TRAN "$DISK") != usb ]] || die "$DISK is a USB disk"
if lsblk -nro MOUNTPOINTS "$DISK" | grep -q .; then die "something on $DISK is mounted"; fi
part() { case $DISK in *[0-9]) echo "${DISK}p$1" ;; *) echo "$DISK$1" ;; esac; }
# The ISO (and so its copy) on a real disk, not this one.
iso_src=$(findmnt -nvo SOURCE -T "$ISO")
iso_disk=$(lsblk -no PKNAME "$iso_src" 2>/dev/null | head -n 1)
[[ -n $iso_disk && $(lsblk -dno TYPE "/dev/$iso_disk") == disk ]] ||
    die "$ISO is on $iso_src, not on a disk's partition: the installer has to see its live media on a disk"
[[ /dev/$iso_disk != "$DISK" ]] || die "$ISO is on $DISK, the disk to erase"

step "the ISO's SHA-256"
sum=$(sha256sum "$ISO" | cut -d' ' -f1)
[[ $sum == "$ISO_SHA256" ]] || die "$ISO's SHA-256 is $sum, not $ISO_SHA256"

mkdir -p "$W"
mount -t tmpfs -o size=2G,mode=0755 bko-anduinos "$W"
mkdir -p "$W"/{iso,lower,upper,work,root,target}
R=$W/root T=$W/target
cleanup() {
    local m
    for m in "$T/boot/efi" "$T"; do mountpoint -q "$m" && umount "$m"; done
    findmnt -rn "$R" >/dev/null && umount -R "$R"
    for m in "$W/lower" "$W/iso"; do mountpoint -q "$m" && umount "$m"; done
    mountpoint -q "$W" && umount "$W"
    rmdir "$W" 2>/dev/null || true
}
trap cleanup EXIT

if [[ $(cat "$TREE/.bko-iso-sha256" 2>/dev/null) != "$ISO_SHA256" ]]; then
    step "copying the ISO's files to $TREE"
    rm -rf "$TREE" "$TREE.part"
    mount -o loop,ro "$ISO" "$W/iso"
    cp -a "$W/iso/." "$TREE.part/"
    umount "$W/iso"
    echo "$ISO_SHA256" >"$TREE.part/.bko-iso-sha256"
    sync
    mv "$TREE.part" "$TREE"
fi
[[ -s $TREE/LiveOS/rootfs.squashfs && -s $TREE/md5sum.txt ]] || die "$TREE has no LiveOS/rootfs.squashfs or md5sum.txt"

step "erasing $DISK ($SERIAL, $(lsblk -dno SIZE,MODEL "$DISK"))"
lsblk -o NAME,SIZE,FSTYPE,LABEL "$DISK"
# Anything still open on it: LUKS mappings, LVM, swap.
for m in $(lsblk -lnpo NAME,TYPE "$DISK" | awk '$2 == "crypt" { print $1 }'); do cryptsetup close "$m"; done
for vg in $(lsblk -lnpo NAME "$DISK" | xargs -r pvs --noheadings -o vg_name 2>/dev/null | sort -u); do vgchange -an "$vg"; done
for s in $(lsblk -lnpo NAME "$DISK"); do swapoff "$s" 2>/dev/null || true; done
wipefs -a $(lsblk -lnpo NAME "$DISK" | sort -r) >/dev/null
# Zero the first 4 MiB and the last 1 MiB (old GPT backup header, signatures).
sectors=$(blockdev --getsz "$DISK")
dd if=/dev/zero of="$DISK" bs=1M count=4 conv=fsync status=none
dd if=/dev/zero of="$DISK" bs=512 seek=$((sectors - 2048)) count=2048 conv=fsync status=none
blockdev --rereadpt "$DISK"
udevadm settle

step "the live root's chroot"
mount -o loop,ro -t squashfs "$TREE/LiveOS/rootfs.squashfs" "$W/lower"
mount -t overlay overlay -o "lowerdir=$W/lower,upperdir=$W/upper,workdir=$W/work" "$R"
grep -qx 'ID=anduinos' "$R/usr/lib/os-release" || die "the ISO's live root isn't AnduinOS"
[[ -x $R/usr/bin/anduinos-installer-executor ]] || die "the live root has no anduinos-installer-executor"
[[ -f $R/usr/share/zoneinfo/$TZONE ]] || die "the live root has no time zone $TZONE"
mkdir -p "$R/cdrom"
mount --bind "$TREE" "$R/cdrom"
mount -o remount,bind,ro "$R/cdrom"
mount --rbind /dev "$R/dev"; mount --make-rslave "$R/dev"
mount -t proc proc "$R/proc"
mount --rbind /sys "$R/sys"; mount --make-rslave "$R/sys"
findmnt -rn "$R/sys/firmware/efi/efivars" >/dev/null || mount -t efivarfs efivarfs "$R/sys/firmware/efi/efivars"
mount -t tmpfs -o mode=0755 run "$R/run"
if [[ -d /run/udev ]]; then mkdir -p "$R/run/udev"; mount --bind /run/udev "$R/run/udev"; fi
mount -t tmpfs -o mode=1777 tmp "$R/tmp"
# The live system's dracut module (95anduinos-live-layers) lays these out.
mkdir -p "$R/run/anduinos-live"
: >"$R/run/anduinos-live/rootfs.squashfs"
mount --bind "$R/cdrom/LiveOS/rootfs.squashfs" "$R/run/anduinos-live/rootfs.squashfs"
cat >"$R/run/anduinos-live/environment" <<EOF
ANDUINOS_LIVE=1
ANDUINOS_LIVE_MEDIA=/cdrom
ANDUINOS_LIVE_SOURCE=/run/anduinos-live/rootfs.squashfs
ANDUINOS_LIVE_DIRECTORY=LiveOS
ANDUINOS_LIVE_IMAGE=rootfs.squashfs
EOF
# The live root's resolv.conf is resolved's stub in a /run it doesn't have here.
rm -f "$R/etc/resolv.conf"
cat /etc/resolv.conf >"$R/etc/resolv.conf"
install -m 755 "$PLAN_PY" "$R/run/bko-plan.py"
in_r() {
    chroot "$R" /usr/bin/env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/root TERM=dumb LANG=C.UTF-8 "$@"
}
in_r dpkg-query -W anduinos-installer-beta anduinos-grub anduinos-shim-signed anduinos-secureboot-toolkit anduinos-btrfs-snapshots-manager |
    sed 's/^/live package: /'

step "the plan: erase ${DISK#/dev/}, Btrfs, host $HOST, user $USERNAME, $TZONE"
pwfile=$KEYS/anduinos-password
if [[ ! -s $pwfile ]]; then
    (umask 077; head -c 18 /dev/urandom | base64 | tr -d '/+=\n' >"$pwfile")
    echo "made $USERNAME's password: $pwfile"
fi
(umask 077; in_r openssl passwd -6 -stdin <"$pwfile" >"$R/run/bko-password-hash")
in_r python3 /run/bko-plan.py "$DISK" "$HOST" "$USERNAME" /run/bko-password-hash "$TZONE" >"$R/run/bko-plan.json" ||
    die "plan.py could not make the plan"
rm -f "$R/run/bko-password-hash"

step "anduinos-installer-executor"
t0=$(date +%s)
set +e
in_r /usr/bin/anduinos-installer-executor <"$R/run/bko-plan.json" | tee "$W/executor.jsonl" | sed -u 's/^/executor: /'
rc=${PIPESTATUS[0]}
set -e
rm -f "$R/run/bko-plan.json"
last=$(tail -n 1 "$W/executor.jsonl")
echo "the executor took $(( $(date +%s) - t0 )) s: exit $rc, last event: $last"
[[ $rc == 0 && $last == '{"event": "complete", "error": ""}' ]] || die "the executor failed: $last"

step "root's keys, the fixed host key, sshd and avahi"
btrfs_dev=$(lsblk -lnpo NAME,FSTYPE "$DISK" | awk '$2 == "btrfs" { print $1; exit }')
esp_dev=$(lsblk -lnpo NAME,FSTYPE "$DISK" | awk '$2 == "vfat" { print $1; exit }')
[[ -n $btrfs_dev && -n $esp_dev ]] || die "no Btrfs or no ESP on $DISK: $(lsblk -lnpo NAME,FSTYPE "$DISK" | tr '\n' ' ')"
mount -o subvol=@root,compress=zstd:3 "$btrfs_dev" "$T"
mount "$esp_dev" "$T/boot/efi"
install -d -m 700 "$T/root/.ssh"
install -m 600 "$KEYS/authorized_keys" "$T/root/.ssh/authorized_keys"
install -m 600 "$KEYS/ssh_host_ed25519_key" "$T/etc/ssh/ssh_host_ed25519_key"
install -m 644 "$KEYS/ssh_host_ed25519_key.pub" "$T/etc/ssh/ssh_host_ed25519_key.pub"
systemctl --root="$T" enable ssh.service
avahi=$(systemctl --root="$T" is-enabled avahi-daemon.service 2>&1 || true)
[[ $avahi == enabled ]] || systemctl --root="$T" enable avahi-daemon.service
mkdir -p "$T/var/log/bko-anduinos-install"
cp "$W/executor.jsonl" "$T/var/log/bko-anduinos-install/"

step "facts"
sed -n 's/^PRETTY_NAME=//p' "$T/usr/lib/os-release"
echo "hostname: $(cat "$T/etc/hostname")"
sfdisk -d "$DISK"
lsblk -o NAME,SIZE,TYPE,FSTYPE,PARTTYPENAME,LABEL,MOUNTPOINTS "$DISK"
cat "$T/etc/fstab"
btrfs subvolume list -a "$T"
(cd "$T/boot/efi" && find . -type f | sort | xargs -d '\n' sha256sum)
ls -la "$T/boot"
for u in ssh.service ssh.socket avahi-daemon.service anduinos-btrfs-snapshots-manager-scheduler.timer \
         anduinos-dracut-migration.timer apt-daily-upgrade.timer; do
    echo "$u: $(systemctl --root="$T" is-enabled "$u" 2>&1 || true)"
done
ls -la "$T/var/lib/anduinos-dracut-migration" 2>&1 || true
grep -E '^(PermitRootLogin|PasswordAuthentication)' "$T/etc/ssh/sshd_config" "$T"/etc/ssh/sshd_config.d/* 2>/dev/null || true
efibootmgr -v | grep -Ev '^ +(dp|data):' | grep -E '^Boot(Current|Next|Order)|AnduinOS'
order=$(efibootmgr | sed -n 's/^BootOrder: //p')
entry=$(efibootmgr -v | awk '/AnduinOS/ && /shimx64.efi/ { sub(/^Boot/, "", $1); sub(/\*.*/, "", $1); print $1; exit }')
[[ -n $entry ]] || die "the installer made no NVRAM entry for \\EFI\\AnduinOS\\shimx64.efi"
[[ ${order%%,*} == "$entry" ]] || die "Boot$entry (AnduinOS) isn't first in BootOrder: $order"
echo "NVRAM: Boot$entry AnduinOS, first in BootOrder"

step "taking the chroot away"
cleanup
trap - EXIT
sync
echo BKO-ANDUINOS-INSTALL-OK
