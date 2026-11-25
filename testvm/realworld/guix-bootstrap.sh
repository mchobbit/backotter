#!/bin/bash
# Install GNU Guix System on a real machine from another running Linux (the
# BackOtter rescue stick), the way Guix's manual installs from a foreign
# distro: the binary tarball and guix-daemon on the live system, then
# partition and mount the target, then `guix system init`.  GX8's groundwork
# (docs/design/guix.md, "What GX8 found").  As root, on the rescue:
#
#   guix-bootstrap.sh SERIAL TARBALL KEYS_DIR HOSTNAME
#   guix-bootstrap.sh --luks prepare SERIAL
#   guix-bootstrap.sh --luks install SERIAL TARBALL KEYS_DIR HOSTNAME
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
# desktop; dhcpcd, openssh (root by key only) and avahi.
#
# --luks: the same, but the third partition (guixcrypt) is LUKS1 opened as
# cryptroot, holding the Btrfs; the ESP and the swap stay plain.  That is the
# guix15-luks guest's layout (testvm/guix/install.sh, docs/design/guix.md
# "guix15-luks"): a mapped-device by its LUKS UUID, target cryptroot,
# luks-device-mapping, the file systems on /dev/mapper/cryptroot with
# (dependencies mapped-devices).  /boot and the store are in the volume, so
# GRUB opens it (`cryptomount -u`, its prefix (cryptouuid/HEX)/@/boot/grub)
# and the initrd opens it again: the passphrase is typed twice per boot,
# until bs1-adopt's LUKS step (bs1-luks family G) makes both unlock from the
# backup drive.  LUKS1, PBKDF2: Guix's GRUB 2.12 has no Argon2.  The
# passphrase is never in a file or on a command line: the person types it.
# So it runs in two steps:
#
#   prepare  wipes and partitions the disk, then prints the cryptsetup line
#            the person runs (over `ssh -t`): luksFormat --type luks1 on the
#            third partition, then open it as cryptroot.  Type the bytes of
#            BackShot1's keys/disk-passphrase, or bs1-adopt's LUKS step stops
#            (the drive's key must open the volume).
#   install  wants cryptroot open on that disk's third partition, LUKS1; makes
#            the ESP, the swap and the Btrfs in cryptroot, and goes on as
#            without --luks.  Run it again after a failure, with cryptroot
#            still open (or opened again).
#
# The daemon's store for the bootstrap lives on the target, in
# /mnt/var/tmp/bko-guix-bootstrap bound onto /gnu and /var/guix, not in the
# stick's RAM, and is deleted at the end; then the target is unmounted, its
# swap turned off and (--luks) cryptroot closed, ready to reboot.  Prints
# BKO-GUIX-INSTALL-OK.  guix-bootstrap-qemu.sh [--luks] rehearses it in QEMU.
#
# BKO_GUIX_KERNEL_ARGUMENTS (unset on a real machine): the configuration's
# kernel-arguments, space-separated.  The rehearsal sets console=ttyS0 last so
# that the initrd's passphrase prompt is on QEMU's serial console; on the
# laptop that would put it on a serial port nobody sees.

set -euo pipefail
USAGE="usage: guix-bootstrap.sh SERIAL TARBALL KEYS_DIR HOSTNAME
       guix-bootstrap.sh --luks prepare SERIAL
       guix-bootstrap.sh --luks install SERIAL TARBALL KEYS_DIR HOSTNAME"
MODE=plain
if [[ ${1:-} == --luks ]]; then
    MODE=${2:-}
    [[ $MODE == prepare || $MODE == install ]] || { echo "$USAGE" >&2; exit 2; }
    shift 2
fi
if [[ $MODE == prepare ]]; then
    [[ $# -eq 1 ]] || { echo "$USAGE" >&2; exit 2; }
    SERIAL=$1
else
    [[ $# -eq 4 ]] || { echo "$USAGE" >&2; exit 2; }
    SERIAL=$1 TARBALL=$2 KEYS=$3 HOST=$4
fi
BOOT=/mnt/var/tmp/bko-guix-bootstrap
MAPPER=cryptroot

die() { echo "guix-bootstrap: $*" >&2; exit 1; }
step() { echo "== $(date +%T) $*"; }
[[ $EUID -eq 0 ]] || die "run it as root"
[[ -d /sys/firmware/efi/efivars ]] || die "this system wasn't booted by UEFI"
if [[ $MODE != prepare ]]; then
    [[ -s $TARBALL ]] || die "no $TARBALL"
    for f in authorized_keys ssh_host_ed25519_key ssh_host_ed25519_key.pub; do [[ -s $KEYS/$f ]] || die "no $KEYS/$f"; done
    for t in sfdisk wipefs mkfs.fat mkswap mkfs.btrfs btrfs xz tar groupadd useradd blkid; do
        command -v $t >/dev/null || die "this system has no $t"
    done
    [[ ! -e /gnu && ! -e /var/guix ]] || die "/gnu or /var/guix exists already: a Guix is installed here"
fi
if [[ $MODE != plain ]]; then
    for t in cryptsetup sfdisk wipefs blockdev; do command -v $t >/dev/null || die "this system has no $t"; done
fi

# The disk, by serial and nothing else.
mapfile -t disks < <(lsblk -dnpo NAME,SERIAL | awk -v s="$SERIAL" '$2 == s { print $1 }')
[[ ${#disks[@]} -eq 1 ]] || die "${#disks[@]} disks have serial $SERIAL"
DISK=${disks[0]}
[[ $(lsblk -dno TRAN "$DISK") != usb ]] || die "$DISK is a USB disk"
if lsblk -nro MOUNTPOINTS "$DISK" | grep -q .; then die "something on $DISK is mounted"; fi
part() { case $DISK in *[0-9]) echo "${DISK}p$1" ;; *) echo "$DISK$1" ;; esac; }

# wipe_and_partition NAME3: the whole disk erased, then GPT ESP + swap + the
# rest, the third partition named NAME3.
wipe_and_partition() {
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
type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name=$1
EOF
    udevadm settle
    for i in 1 2 3; do dd if=/dev/zero of="$(part $i)" bs=1M count=4 conv=fsync status=none; done
}

if [[ $MODE == prepare ]]; then
    wipe_and_partition guixcrypt
    udevadm settle
    lsblk -o NAME,SIZE,TYPE,PARTLABEL "$DISK"
    P3=$(part 3)
    FORMAT="cryptsetup luksFormat --type luks1 --cipher aes-xts-plain64 --key-size 512 --hash sha256 $P3"
    OPEN="cryptsetup open $P3 $MAPPER"
    echo "BKO-GUIX-LUKS-FORMAT: $FORMAT"
    echo "BKO-GUIX-LUKS-OPEN: $OPEN"
    cat <<EOF

Now type the passphrase yourself, on the stick (over ssh -t), the bytes of
BackShot1's keys/disk-passphrase: luksFormat asks twice (after YES in
capitals), open once.

    sudo $FORMAT && sudo $OPEN

Then:

    sudo bash $0 --luks install $SERIAL TARBALL KEYS_DIR HOSTNAME

BKO-GUIX-LUKS-PREPARED
EOF
    exit 0
fi

if [[ $MODE == plain ]]; then
    wipe_and_partition guixroot
    ROOTDEV=$(part 3)
else
    # cryptroot open on this disk's third partition, LUKS1, from prepare's
    # layout; the disk isn't partitioned again (the volume is on it).
    step "checking $DISK: prepare's layout, $MAPPER open on $(part 3)"
    lsblk -o NAME,SIZE,TYPE,FSTYPE,PARTLABEL "$DISK"
    [[ $(lsblk -nro PARTLABEL "$(part 1)" 2>/dev/null) == esp && $(lsblk -nro PARTLABEL "$(part 2)" 2>/dev/null) == swap &&
       $(lsblk -nro PARTLABEL "$(part 3)" 2>/dev/null) == guixcrypt ]] ||
        die "$DISK isn't laid out as --luks prepare does (esp, swap, guixcrypt): run prepare first"
    cryptsetup isLuks --type luks1 "$(part 3)" || die "$(part 3) isn't LUKS1: run prepare's cryptsetup line (--type luks1)"
    [[ -b /dev/mapper/$MAPPER ]] || die "/dev/mapper/$MAPPER isn't open: cryptsetup open $(part 3) $MAPPER"
    holder=$(lsblk -nrpo NAME,TYPE "$(part 3)" | awk '$2 == "crypt" { print $1 }')
    [[ $holder == "/dev/mapper/$MAPPER" ]] || die "/dev/mapper/$MAPPER isn't $(part 3)'s (it holds: ${holder:-nothing})"
    ROOTDEV=/dev/mapper/$MAPPER
    # On a failure, leave nothing mounted but cryptroot open, so that the
    # same command can run again.
    daemon=
    undo() {
        [[ $? -ne 0 ]] || return 0
        echo "guix-bootstrap: failed; unmounting the target (cryptroot stays open: run install again)" >&2
        set +e
        [[ -z $daemon ]] || { kill "$daemon"; wait "$daemon"; } 2>/dev/null
        sleep 2
        mountpoint -q /var/guix && umount /var/guix
        mountpoint -q /gnu && umount /gnu
        rmdir /gnu /var/guix 2>/dev/null
        swapoff "$(part 2)" 2>/dev/null
        mountpoint -q /mnt && umount -R /mnt
    }
    trap undo EXIT
    for i in 1 2; do dd if=/dev/zero of="$(part $i)" bs=1M count=4 conv=fsync status=none; done
fi
mkfs.fat -F 32 -n GUIXESP "$(part 1)" >/dev/null
mkswap -L guixswap "$(part 2)" >/dev/null
mkfs.btrfs -q -f -L guixroot "$ROOTDEV"
udevadm settle

step "subvolumes and mounts"
mount "$ROOTDEV" /mnt
for s in @ @home @gnu; do btrfs subvolume create "/mnt/$s" >/dev/null; done
umount /mnt
mount -o subvol=@,compress=zstd "$ROOTDEV" /mnt
mkdir -p /mnt/home /mnt/gnu /mnt/boot/efi
mount -o subvol=@home,compress=zstd "$ROOTDEV" /mnt/home
mount -o subvol=@gnu,compress=zstd "$ROOTDEV" /mnt/gnu
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
root_uuid=$(blkid -s UUID -o value "$(part 3)")    # --luks: the LUKS header's UUID
swap_uuid=$(blkid -s UUID -o value "$(part 2)")
esp_uuid=$(blkid -s UUID -o value "$(part 1)")
key=$(head -n 1 "$KEYS/authorized_keys")
mkdir -p /mnt/etc
fs() {  # fs MOUNT SUBVOL [extra]
    if [[ $MODE == plain ]]; then
        cat <<EOF
                 (file-system
                   (mount-point "$1")
                   (device (uuid "$root_uuid"))
                   (type "btrfs")
                   (options "subvol=$2,compress=zstd")${3:-})
EOF
    else
        cat <<EOF
                 (file-system
                   (mount-point "$1")
                   (device "/dev/mapper/$MAPPER")
                   (type "btrfs")
                   (options "subvol=$2,compress=zstd")${3:-}
                   (dependencies mapped-devices))
EOF
    fi
}
# --luks: as Guix's installer writes them (guix15-luks): the volume by its
# LUKS UUID, the file systems by /dev/mapper/cryptroot.
mapped=
[[ $MODE == plain ]] || mapped="
  (mapped-devices (list (mapped-device
                          (source (uuid \"$root_uuid\"))
                          (target \"$MAPPER\")
                          (type luks-device-mapping))))"
kargs=
if [[ -n ${BKO_GUIX_KERNEL_ARGUMENTS:-} ]]; then
    kargs="
  (kernel-arguments (list$(printf ' "%s"' $BKO_GUIX_KERNEL_ARGUMENTS)))"
fi
if [[ $MODE == plain ]]; then
    title="GNU Guix System on the Thinkpad, bootstrapped from the BackOtter rescue
;; stick by testvm/realworld/guix-bootstrap.sh (docs/design/guix.md, GX8)."
else
    title="GNU Guix System on the Thinkpad, bootstrapped from the BackOtter rescue
;; stick by testvm/realworld/guix-bootstrap.sh --luks (docs/design/guix.md, GX8),
;; root on LUKS1 as cryptroot (the guix15-luks layout)."
fi
cat >/mnt/etc/config.scm <<EOF
;; $title
(use-modules (gnu))
(use-service-modules avahi networking ssh)

(operating-system
  (host-name "$HOST")
  (timezone "Europe/Paris")
  (locale "en_US.utf8")
  (keyboard-layout (keyboard-layout "us"))$kargs
  (bootloader (bootloader-configuration
                (bootloader grub-efi-bootloader)
                (targets (list "/boot/efi"))
                (timeout 5)
                (keyboard-layout keyboard-layout)))$mapped
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
grep -E '^\s*(search|linux|initrd|cryptomount)' /mnt/boot/grub/grub.cfg | head -n 8
find /mnt/boot/efi -type f | sed 's|^/mnt||'
if [[ $MODE != plain ]]; then
    # GRUB opens the volume itself and reads /boot/grub from it.
    prefix=$(LC_ALL=C grep -aoE '\(cryptouuid/[0-9a-f]+\)/[!-~]*' /mnt/boot/efi/EFI/Guix/grubx64.efi | head -n 1 || true)
    echo "GRUB's prefix: ${prefix:-none}"
    [[ $prefix == "(cryptouuid/${root_uuid//-/})/@/boot/grub" ]] ||
        die "EFI/Guix/grubx64.efi's prefix is ${prefix:-missing}, not (cryptouuid/${root_uuid//-/})/@/boot/grub: GRUB wouldn't open the volume"
    cryptsetup luksDump "$(part 3)" | grep -E '^(Version|Cipher|Hash|MK bits|UUID)'
fi
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
[[ $MODE == plain ]] || { trap - EXIT; cryptsetup close "$MAPPER"; }
echo BKO-GUIX-INSTALL-OK
