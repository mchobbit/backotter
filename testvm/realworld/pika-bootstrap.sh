#!/bin/bash
# Install PikaOS 4 on a real machine from another running Linux (the
# BackOtter rescue stick), with PikaOS's own installer: the GTK installer's
# automatic XFS layout and its pikainstall 2.0.5 backend, run by
# testvm/pika/install.sh in a chroot of the ISO's live image, as the
# pikaos4 guest is built (make-pikaos.sh).  P8's groundwork
# (docs/design/pikaos.md, "What P8 found").  As root, on the rescue:
#
#   pika-bootstrap.sh SERIAL ISO INSTALL_SH KEYS_DIR HOSTNAME
#
#   SERIAL      the one disk to ERASE, by serial number (lsblk -dno SERIAL)
#   ISO         PikaOS-Nest-GNOME-4.0-amd64-v3-26.08.20-4.iso (its SHA-256
#               is checked against the one pinned here and in make-pikaos.sh)
#   INSTALL_SH  testvm/pika/install.sh
#   KEYS_DIR    authorized_keys (root's login) and ssh_host_ed25519_key{,.pub}
#               (the fixed host key), BackShot1's keys/ on the backup drive
#   HOSTNAME    the installed system's host name (BackShot1's: thinkpad)
#
# What the installer makes (its defaults): GPT, "linux-efi" 1-500 MiB vfat
# at /boot/efi, "linux-boot" 500-1500 MiB ext4 /boot (XBOOTLDR, labelled
# PikaOS), "linux-root" the rest XFS; zram; booster; rEFInd in EFI/refind
# with its NVRAM entry ("rEFInd Boot Manager", first in BootOrder) and Pika's
# theme; GNOME, with the first-setup user pikaos/pikaos.  No LUKS.  Then
# only: the host name, root's authorized_keys (root's password stays
# locked), sshd enabled, and the fixed host key.
#
# Run on the Thinkpad 2026-11-14 from the rescue stick (Ubuntu 26.04's
# kernel 7.0): install.sh took 43 s.
#
# The chroot: the ISO loop-mounted, its live/filesystem.squashfs under a
# tmpfs overlay (pikainstall writes its scripts there), the ISO's live/ at
# /live (where pikainstall unsquashes from), /dev, /proc and /sys (with
# efivarfs, for refind-install's NVRAM entry) from the rescue, a /run of its
# own with the rescue's udev database.  Prints BKO-PIKA-INSTALL-OK.

set -euo pipefail
SERIAL=${1:?usage: pika-bootstrap.sh SERIAL ISO INSTALL_SH KEYS_DIR HOSTNAME}
ISO=${2:?}
INSTALL_SH=${3:?}
KEYS=${4:?}
HOST=${5:?}
ISO_SHA256=ce26e89ee468ae146e2c7cefc6fd3a022face03316ccfa58caadb3090019a1ea
W=/run/bko-pika

die() { echo "pika-bootstrap: $*" >&2; exit 1; }
step() { echo "== $(date +%T) $*"; }
[[ $EUID -eq 0 ]] || die "run it as root"
[[ -d /sys/firmware/efi/efivars ]] || die "this system wasn't booted by UEFI (PikaOS is UEFI only)"
grep -qw avx2 /proc/cpuinfo || die "this CPU has no AVX2: PikaOS is built for x86-64-v3"
[[ -s $ISO && -s $INSTALL_SH ]] || die "no $ISO or $INSTALL_SH"
[[ $HOST =~ ^[a-z0-9][a-z0-9-]*$ ]] || die "HOSTNAME \"$HOST\" isn't a plain host name"
for f in authorized_keys ssh_host_ed25519_key ssh_host_ed25519_key.pub; do [[ -s $KEYS/$f ]] || die "no $KEYS/$f"; done
for t in sha256sum losetup wipefs blockdev chroot python3 lsblk findmnt; do
    command -v $t >/dev/null || die "this system has no $t"
done
grep -qw overlay /proc/filesystems || modprobe overlay || die "no overlayfs"
! findmnt -rn "$W" >/dev/null || die "$W is mounted already: an earlier run's? umount -R $W"

step "the ISO's SHA-256"
sum=$(sha256sum "$ISO" | cut -d' ' -f1)
[[ $sum == "$ISO_SHA256" ]] || die "$ISO's SHA-256 is $sum, not $ISO_SHA256"

# The disk, by serial and nothing else.
mapfile -t disks < <(lsblk -dnpo NAME,SERIAL | awk -v s="$SERIAL" '$2 == s { print $1 }')
[[ ${#disks[@]} -eq 1 ]] || die "${#disks[@]} disks have serial $SERIAL"
DISK=${disks[0]}
[[ $(lsblk -dno TRAN "$DISK") != usb ]] || die "$DISK is a USB disk"
if lsblk -nro MOUNTPOINTS "$DISK" | grep -q .; then die "something on $DISK is mounted"; fi
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

step "the live image's chroot"
mkdir -p "$W"
mount -t tmpfs -o size=2G,mode=0755 bko-pika "$W"
# Private: /run is shared, and the mounts below would otherwise be copied
# into every service's own namespace (udevd's, logind's, resolved's...),
# where unmounting them here leaves them, and the ISO busy (the first run).
mount --make-private "$W"
mkdir -p "$W"/{iso,lower,upper,work,root}
mount -o loop,ro "$ISO" "$W/iso"
[[ -s $W/iso/live/filesystem.squashfs ]] || die "the ISO has no live/filesystem.squashfs"
mount -o loop,ro -t squashfs "$W/iso/live/filesystem.squashfs" "$W/lower"
mount -t overlay overlay -o "lowerdir=$W/lower,upperdir=$W/upper,workdir=$W/work" "$W/root"
R=$W/root
grep -qx 'ID=pika' "$R/etc/os-release" || die "the ISO's live image isn't PikaOS"
mkdir -p "$R/live"
mount --bind "$W/iso/live" "$R/live"
mount --rbind /dev "$R/dev"; mount --make-rslave "$R/dev"
mount -t proc proc "$R/proc"
mount --rbind /sys "$R/sys"; mount --make-rslave "$R/sys"
findmnt -rn "$R/sys/firmware/efi/efivars" >/dev/null || mount -t efivarfs efivarfs "$R/sys/firmware/efi/efivars"
mount -t tmpfs -o mode=0755 run "$R/run"
if [[ -d /run/udev ]]; then mkdir -p "$R/run/udev"; mount --bind /run/udev "$R/run/udev"; fi
mount -t tmpfs -o mode=1777 tmp "$R/tmp"

step "install.sh, filled in for $HOST on ${DISK#/dev/}"
python3 - "$INSTALL_SH" "$R/root/bko-install.sh" "$HOST" "$(head -n 1 "$KEYS/authorized_keys")" <<'PY'
import re, sys
src, dst, host, key = sys.argv[1:]
text = open(src).read()
for k, v in {"HOSTNAME": host, "ROOT_PASSWORD_HASH": "", "AUTHORIZED_KEY": key, "LAYOUT": "xfs",
             "REFIND_BOOTX64": "false", "LUKS_PASSPHRASE": ""}.items():
    text = text.replace(f"@{k}@", v)
left = re.findall(r"@[A-Z_0-9]+@", text)
if left:
    sys.exit(f"install.sh placeholders left unfilled: {' '.join(sorted(set(left)))}")
open(dst, "w").write(text)
PY
t0=$(date +%s)
rc=0
chroot "$R" /usr/bin/env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/root TERM=dumb LANG=C.UTF-8 \
    PIKA_DISK="${DISK#/dev/}" PIKA_METAL=1 bash /root/bko-install.sh 2>&1 | tee "$W/install.log" || rc=$?
echo "install.sh took $(( $(date +%s) - t0 )) s"
grep -qx BACKOTTER-INSTALL-OK "$W/install.log" || die "install.sh didn't finish (exit $rc): $(grep -m1 BACKOTTER-INSTALL-FAILED "$W/install.log" || tail -n 3 "$W/install.log")"

step "the fixed host key and root's keys"
part() { case $DISK in *[0-9]) echo "${DISK}p$1" ;; *) echo "$DISK$1" ;; esac; }
T=$W/target
mkdir -p "$T"
mount "$(part 3)" "$T"
install -m 600 "$KEYS/ssh_host_ed25519_key" "$T/etc/ssh/ssh_host_ed25519_key"
install -m 644 "$KEYS/ssh_host_ed25519_key.pub" "$T/etc/ssh/ssh_host_ed25519_key.pub"
install -m 600 "$KEYS/authorized_keys" "$T/root/.ssh/authorized_keys"
echo "hostname: $(cat "$T/etc/hostname")"
grep -E '^127\.0\.1\.1' "$T/etc/hosts" || true
umount "$T"

step "taking the chroot away"
cp "$W/install.log" /var/log/bko-pika-install.log 2>/dev/null || true
umount -R "$R"
umount "$W/lower" "$W/iso"
umount "$W"
rmdir "$W"
efibootmgr | grep -iE '^Boot(Current|Next|Order)|refind'
sync
echo BKO-PIKA-INSTALL-OK
