#!/bin/bash
# Install GNU Guix System on a real machine from another running Linux (the
# BackOtter rescue stick), the way Guix's manual installs from a foreign
# distro: the binary tarball and guix-daemon on the live system, then
# partition and mount the target, then `guix system init`.  GX8's groundwork
# (docs/design/guix.md, "What GX8 found").  As root, on the rescue:
#
#   guix-bootstrap.sh SERIAL TARBALL KEYS_DIR HOSTNAME
#
#   SERIAL    the one disk to ERASE, by serial number (lsblk -dno SERIAL)
#   TARBALL   guix-binary-VERSION.x86_64-linux.tar.xz, its signature already
#             checked (gpgv against GNU's keyring)
#   KEYS_DIR  authorized_keys (root's login) and ssh_host_ed25519_key{,.pub}
#             (the fixed host key), BackShot1's keys/ on the backup drive
#
# Layout (UEFI, GPT): 1 GiB ESP vfat at /boot/efi, 8 GiB swap, the rest Btrfs
# labelled guixroot with subvolumes @ (/), @home and @gnu (/gnu).  GRUB by
# grub-efi-bootloader: EFI/Guix/grubx64.efi and its own NVRAM entry.  No
# encryption, no desktop; dhcpcd, openssh (root by key only) and avahi.
#
# The daemon's store for the bootstrap lives on the target, in
# /mnt/var/tmp/bko-guix-bootstrap bound onto /gnu and /var/guix, not in the
# stick's RAM, and is deleted at the end; then the target is unmounted and
# its swap turned off, ready to reboot.  Prints BKO-GUIX-INSTALL-OK.
# guix-bootstrap-qemu.sh rehearses it in QEMU.

set -euo pipefail
SERIAL=${1:?usage: guix-bootstrap.sh SERIAL TARBALL KEYS_DIR HOSTNAME}
TARBALL=${2:?}
KEYS=${3:?}
HOST=${4:?}
BOOT=/mnt/var/tmp/bko-guix-bootstrap

die() { echo "guix-bootstrap: $*" >&2; exit 1; }
step() { echo "== $(date +%T) $*"; }
[[ $EUID -eq 0 ]] || die "run it as root"
[[ -d /sys/firmware/efi/efivars ]] || die "this system wasn't booted by UEFI"
[[ -s $TARBALL ]] || die "no $TARBALL"
for f in authorized_keys ssh_host_ed25519_key ssh_host_ed25519_key.pub; do [[ -s $KEYS/$f ]] || die "no $KEYS/$f"; done
for t in sfdisk wipefs mkfs.fat mkswap mkfs.btrfs btrfs xz tar groupadd useradd blkid; do
    command -v $t >/dev/null || die "this system has no $t"
done
[[ ! -e /gnu && ! -e /var/guix ]] || die "/gnu or /var/guix exists already: a Guix is installed here"

# The disk, by serial and nothing else.
mapfile -t disks < <(lsblk -dnpo NAME,SERIAL | awk -v s="$SERIAL" '$2 == s { print $1 }')
[[ ${#disks[@]} -eq 1 ]] || die "${#disks[@]} disks have serial $SERIAL"
DISK=${disks[0]}
[[ $(lsblk -dno TRAN "$DISK") != usb ]] || die "$DISK is a USB disk"
if lsblk -nro MOUNTPOINTS "$DISK" | grep -q .; then die "something on $DISK is mounted"; fi
part() { case $DISK in *[0-9]) echo "${DISK}p$1" ;; *) echo "$DISK$1" ;; esac; }
step "erasing $DISK ($SERIAL, $(lsblk -dno SIZE,MODEL "$DISK"))"
lsblk -o NAME,SIZE,FSTYPE,LABEL "$DISK"

# Anything still open on it: LUKS mappings, swap.
for m in $(lsblk -lnpo NAME,TYPE "$DISK" | awk '$2 == "crypt" { print $1 }'); do cryptsetup close "$m"; done
swapoff -a 2>/dev/null || true
wipefs -a $(lsblk -lnpo NAME "$DISK" | sort -r)
# Zero the first 4 MiB and the last 1 MiB (old GPT backup header, signatures).
sectors=$(blockdev --getsz "$DISK")
dd if=/dev/zero of="$DISK" bs=1M count=4 conv=fsync status=none
dd if=/dev/zero of="$DISK" bs=512 seek=$((sectors - 2048)) count=2048 conv=fsync status=none

step "partitioning"
sfdisk --quiet "$DISK" <<EOF
label: gpt
size=1GiB, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name=esp
size=8GiB, type=0657FD6D-A4AB-43C4-84E5-0933C84B4F4F, name=swap
type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name=guixroot
EOF
udevadm settle
for i in 1 2 3; do dd if=/dev/zero of="$(part $i)" bs=1M count=4 conv=fsync status=none; done
mkfs.fat -F 32 -n GUIXESP "$(part 1)" >/dev/null
mkswap -L guixswap "$(part 2)" >/dev/null
mkfs.btrfs -q -f -L guixroot "$(part 3)"
udevadm settle

step "subvolumes and mounts"
mount "$(part 3)" /mnt
for s in @ @home @gnu; do btrfs subvolume create "/mnt/$s" >/dev/null; done
umount /mnt
mount -o subvol=@,compress=zstd "$(part 3)" /mnt
mkdir -p /mnt/home /mnt/gnu /mnt/boot/efi
mount -o subvol=@home,compress=zstd "$(part 3)" /mnt/home
mount -o subvol=@gnu,compress=zstd "$(part 3)" /mnt/gnu
mount "$(part 1)" /mnt/boot/efi
swapon "$(part 2)"
btrfs subvolume list /mnt

step "Guix's binary tarball, its store on the target"
mkdir -p "$BOOT"
tar --warning=no-timestamp -C "$BOOT" -xf "$TARBALL"
mkdir -p /gnu /var/guix
mount --bind "$BOOT/gnu" /gnu
mount --bind "$BOOT/var/guix" /var/guix
GUIX=/var/guix/profiles/per-user/root/current-guix
mkdir -p ~root/.config/guix
ln -sfn "$GUIX" ~root/.config/guix/current
getent group guixbuild >/dev/null || groupadd --system guixbuild
for i in $(seq -w 1 10); do
    id "guixbuilder$i" >/dev/null 2>&1 ||
        useradd -g guixbuild -G guixbuild -d /var/empty -s "$(command -v nologin)" -c "Guix build user $i" --system "guixbuilder$i"
done
"$GUIX/bin/guix" archive --authorize <"$GUIX/share/guix/ci.guix.gnu.org.pub"
"$GUIX/bin/guix" archive --authorize <"$GUIX/share/guix/bordeaux.guix.gnu.org.pub"
export GUIX_LOCPATH=$GUIX/lib/locale
"$GUIX/bin/guix-daemon" --build-users-group=guixbuild >/var/log/guix-daemon.log 2>&1 </dev/null &
daemon=$!
for i in $(seq 30); do [[ -S /var/guix/daemon-socket/socket ]] && break; sleep 1; done
[[ -S /var/guix/daemon-socket/socket ]] || die "guix-daemon didn't start: $(cat /var/log/guix-daemon.log)"
"$GUIX/bin/guix" --version | head -n 1

step "/mnt/etc/config.scm"
root_uuid=$(blkid -s UUID -o value "$(part 3)")
swap_uuid=$(blkid -s UUID -o value "$(part 2)")
esp_uuid=$(blkid -s UUID -o value "$(part 1)")
key=$(head -n 1 "$KEYS/authorized_keys")
mkdir -p /mnt/etc
fs() {  # fs MOUNT SUBVOL [extra]
    cat <<EOF
                 (file-system
                   (mount-point "$1")
                   (device (uuid "$root_uuid"))
                   (type "btrfs")
                   (options "subvol=$2,compress=zstd")${3:-})
EOF
}
cat >/mnt/etc/config.scm <<EOF
;; GNU Guix System on the Thinkpad, bootstrapped from the BackOtter rescue
;; stick by testvm/realworld/guix-bootstrap.sh (docs/design/guix.md, GX8).
(use-modules (gnu))
(use-service-modules avahi networking ssh)

(operating-system
  (host-name "$HOST")
  (timezone "Europe/Paris")
  (locale "en_US.utf8")
  (keyboard-layout (keyboard-layout "us"))
  (bootloader (bootloader-configuration
                (bootloader grub-efi-bootloader)
                (targets (list "/boot/efi"))
                (timeout 5)
                (keyboard-layout keyboard-layout)))
  (swap-devices (list (swap-space (target (uuid "$swap_uuid")))))
  (file-systems (cons* $(fs / @)
$(fs /home @home)
$(fs /gnu @gnu "
                   (needed-for-boot? #t)")
                 (file-system
                   (mount-point "/boot/efi")
                   (device (uuid "$esp_uuid" 'fat32))
                   (type "vfat"))
                 %base-file-systems))
  ;; What BackShot1's scripts use: python3, efibootmgr (bs1-to-stick's
  ;; BootNext), btrfs.
  (packages (append (map specification->package
                         '("python" "efibootmgr" "btrfs-progs"))
                    %base-packages))
  (services
    (append (list (service dhcpcd-service-type)
                  (service avahi-service-type)
                  (service openssh-service-type
                    (openssh-configuration
                      (permit-root-login 'prohibit-password)
                      (password-authentication? #f)
                      (authorized-keys
                        \`(("root" ,(plain-file "root.pub" "$key\n")))))))
            %base-services)))
EOF
cat /mnt/etc/config.scm

step "guix system init"
t0=$(date +%s)
"$GUIX/bin/guix" system init /mnt/etc/config.scm /mnt
echo "guix system init took $(( $(date +%s) - t0 )) s"

step "the fixed host key, before the first boot's activation makes its own"
install -d -m 755 /mnt/etc/ssh
install -m 600 "$KEYS/ssh_host_ed25519_key" /mnt/etc/ssh/ssh_host_ed25519_key
install -m 644 "$KEYS/ssh_host_ed25519_key.pub" /mnt/etc/ssh/ssh_host_ed25519_key.pub

step "facts"
sys=$(readlink -f /mnt/var/guix/profiles/system); sys=${sys#/mnt}
echo "system: $sys"
ls -l /mnt/var/guix/profiles/
cat "/mnt$sys/parameters"; echo
grep -E '^\s*(search|linux|initrd)' /mnt/boot/grub/grub.cfg | head -n 6
find /mnt/boot/efi -type f | sed 's|^/mnt||'
command -v efibootmgr >/dev/null && efibootmgr -v
du -sh /mnt/gnu/store

step "taking the bootstrap store away"
kill "$daemon" 2>/dev/null || true
wait "$daemon" 2>/dev/null || true
for i in $(seq 10); do fuser -s /gnu/store 2>/dev/null || break; sleep 1; done
umount /var/guix /gnu
rmdir /gnu /var/guix
rm -rf "$BOOT"
sync
step "unmounting the target"
swapoff "$(part 2)"
umount -R /mnt
echo BKO-GUIX-INSTALL-OK
