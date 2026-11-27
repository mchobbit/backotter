#!/usr/bin/env bash
# Install Gentoo Linux on a real machine from the BackOtter rescue stick, the
# way the gentoo-systemd test guest is built: testvm/bootstrap/gentoo.sh
# itself runs here, with GENTOO_METAL=yes (docs/design/gentoo.md, "Gentoo on
# the A100").  UEFI, GPT, a 1 GiB ESP that is /boot, then LUKS2 holding LVM
# vg0 (root ext4, swap), systemd, dracut's systemd-cryptsetup opening the
# volume (rd.luks.uuid on GRUB's command line), GRUB in EFI/gentoo with an
# NVRAM entry of its own, gentoo-kernel-bin by installkernel[dracut,grub],
# binary packages from Gentoo's 23.0 binhost.  bs1-adopt's family S
# (bs1-luks) then makes it unlock from the backup drive, with no unlock code
# of its own.  As root, on the stick (never on an installed system):
#
#   gentoo-bootstrap.sh [--detach] [--inputs DIR] SERIAL [HOSTNAME]
#
#   SERIAL    the one disk to ERASE, by serial number (lsblk -dno SERIAL):
#             not a USB disk, not the stick's, not the backup drive's
#   HOSTNAME  the installed system's host name, default thinkpad
#   --inputs  the directory with the stage3 (default <drive>/backshot1/gentoo)
#   --detach  in the background, past a dropped SSH connection: the log, pid
#             and exit status in /run/bko-gentoo/ (install.log, pid, exit;
#             exit is written last, once the log's copy is on the drive)
#
# One run at a time (flock on /run/bko-gentoo/lock, held by the detached
# run itself), from / (never the caller's directory, which may be on the
# drive).
#
# Takes no secrets on its command line or in its environment.  The backup
# drive is LABEL=mybig512, wherever it is mounted (/mnt/bs1-backup, as
# backshot1's $DRIVE does, when nothing has it).  Its backshot1/keys/ gives
# the volume's passphrase (disk-passphrase: no final newline, printable
# ASCII, since it is typed at the first boot), root's authorized_keys and
# the fixed host key ssh_host_ed25519_key{,.pub}.  The passphrase is read
# only by cryptsetup (--key-file, by its path on the drive), never printed,
# logged, copied, put on a command line or in an environment, and never in
# the installed system: crypttab's key field is none, so systemd-cryptsetup
# asks for it at boot until bs1-adopt's family S.
#
# Inputs, in DIR (backshot1/README.md, "Gentoo on the A100", says what to
# stage there): this script; testvm/bootstrap/gentoo.sh beside it (as
# gentoo.sh); the systemd stage3 the gentoo-systemd guest is built from
# (make-bootstrap.sh's GENTOO_STAGE3), its .asc and gentoo-release-key.gpg
# (Gentoo's Release Engineering key), each of the three pinned by SHA-256
# below, and the stage3 checked against the key with gpgv too.  Gentoo
# prunes autobuilds after about two months: the farm keeps them in
# /bkoscratch/rebuild-inputs/gentoo-systemd on Chopin.
# What isn't pinned, as for the guest, and comes over the stick's wired
# network: Portage's tree (emerge-webrsync's newest snapshot, checked
# against the key the stage3 ships), the binhost's packages (checked
# against getuto's keyring) and the distfiles of the few source builds
# (gentoo.sh's SOURCE_OK, with systemd for USE=cryptsetup, and
# linux-firmware; checked against the tree's Manifests).  The binhost
# follows the live tree, so an old pinned tree would turn binary packages
# into source builds.
#
# The disk (WIPED in full), gentoo-systemd's LAYOUT luks-esp at real sizes:
#   p1  1 GiB ESP, FAT32 labelled EFI, /boot (umask=0077): the kernels,
#       dracut's initramfs, GRUB (EFI/gentoo/grubx64.efi, the NVRAM entry
#       "gentoo" first in BootOrder; and EFI/BOOT/BOOTX64.EFI)
#   p2  the rest, LUKS2 (cryptsetup's defaults: argon2id, aes-xts-plain64),
#       cryptroot in crypttab (UUID, none, luks,discard), holding vg0:
#       root 64 GiB ext4 (/), swap 4 GiB, the rest of vg0 free
# The system: Gentoo's systemd stage3 (profile default/linux/amd64/23.0/
# systemd), gentoo.sh's packages for gentoo-systemd and, for the hardware
# and BackShot1, linux-firmware, dbus and avahi; systemd-networkd (DHCP on
# en*), systemd-resolved, sshd (root by key only: root's password is
# locked), avahi-daemon for HOSTNAME.local; the time zone UTC.  NVRAM
# entries labelled gentoo, or naming the wiped disk's old partitions, are
# deleted first: those partitions' GUIDs, read before each wipe, are kept
# in DIR/old-partuuids until a run gets that far, so a run that wiped the
# disk and then failed leaves the earlier system's entries to the next.
#
# BKO_GENTOO_CMDLINE (unset on a real machine): kernel arguments before
# rd.luks.uuid on GRUB's line.  The QEMU rehearsal sets "console=ttyS0,115200n8
# console=tty0": the kernel's log on its serial port, and the passphrase
# prompt still on the screen (tty0 last), where its keyboard types.
#
# Rerunnable after a failure: every run starts again from the wipe.  The
# stick's /run/bko-gentoo/install.log is copied to DIR/install-DATE-exitRC.log
# at the end.  Prints BKO-GENTOO-INSTALL-OK.  gentoo-bootstrap-qemu.sh
# rehearses it in QEMU.

set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C

USAGE="usage: gentoo-bootstrap.sh [--detach] [--inputs DIR] SERIAL [HOSTNAME]"
BACKUP_LABEL=mybig512
# make-bootstrap.sh's GENTOO_STAGE3, the systemd one (test2's
# test_gentoo_bootstrap.py checks that they agree).
STAGE3=stage3-amd64-systemd-20261108T170107Z.tar.xz
STAGE3_SHA256=8007e84c9190485a25f79f93d0c9438edbdc63d7b76d4462cb80ab9672af72ef
STAGE3_ASC_SHA256=c77d8b28093f8bb05bb8ab9ce9cdf584828b5fe57511b16c5b433aff098edcb4
RELEASE_KEY=gentoo-release-key.gpg
RELEASE_KEY_SHA256=af00969b4ed25c80615c023d6228721a02cf630383a4f959d3950a1815db333e
ESP_SIZE=1GiB
ROOT_SIZE=64G
SWAP_SIZE=4G
STATE=/run/bko-gentoo
MNT=$STATE/mnt                  # a private tmpfs: nothing under it reaches other namespaces
ROOT_MNT=$MNT/root              # the new system, as gentoo.sh's ROOT_MNT
MAP=cryptroot
VG=vg0
BOOT_LABEL=gentoo
BOOT_LOADER='\EFI\gentoo\grubx64.efi'
SECURE_BOOT_VAR=/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-e98c2b0e0b8c

die() { echo "gentoo-bootstrap: $*" >&2; exit 1; }
say() { echo "== $(date -u +%H:%M:%S) $*"; }

# check_passphrase FILE: its shape, never its bytes: 8 to 512 of them,
# printable ASCII (typed at the first boot's prompt), no newline.
check_passphrase() {
    local n
    n=$(wc -c <"$1" | tr -d ' ')
    (( n >= 8 && n <= 512 )) || die "$1 is $n bytes: 8 to 512, please"
    [[ $(tail -c 1 "$1" | od -An -tx1 | tr -d ' ') != 0a ]] || die "$1 ends with a newline: it must be printf '%s' PASSPHRASE"
    [[ $(tr -dc '\r\n' <"$1" | wc -c | tr -d ' ') == 0 ]] || die "$1 has a newline or CR in it"
    ! LC_ALL=C grep -q '[^ -~]' "$1" || die "$1 has a character that isn't printable ASCII: it must be typeable at the boot prompt"
}
# find_disk SERIAL DRIVE: the one whole disk whose serial is SERIAL, on
# stdout; not a USB disk, nor the one under DRIVE (the backup drive's
# mount) or /cdrom (the stick's).
holder() { lsblk -snlpo NAME,TYPE "$1" 2>/dev/null | awk '$2 == "disk" { print $1 }'; }
find_disk() {
    local disks d src
    disks=$(lsblk -dnrpo NAME,TYPE,SERIAL | awk -v s="$1" '$2 == "disk" && $3 == s { print $1 }')
    [[ -n $disks && $(wc -l <<<"$disks") -eq 1 ]] || die "not exactly one disk has serial $1 (found: $(paste -sd ' ' - <<<"${disks:-none}"))"
    d=$disks
    [[ $(lsblk -dno TRAN "$d" | tr -d ' ') != usb ]] || die "$d (serial $1) is a USB disk"
    [[ $(holder "$(findmnt -nro SOURCE --target "$2")") != "$d" ]] || die "the backup drive is on $d"
    if src=$(findmnt -nro SOURCE /cdrom 2>/dev/null) && [[ -n $src ]]; then
        [[ $(holder "$src") != "$d" ]] || die "the stick is on $d"
    fi
    echo "$d"
}
part() { case $DISK in *[0-9]) echo "${DISK}p$1" ;; *) echo "$DISK$1" ;; esac; }

# --- Arguments ------------------------------------------------------------------------
detach=no
inputs=""
pos=()
args=()       # the arguments but --detach, for the detached run
while [[ $# -gt 0 ]]; do
    case $1 in
        --detach) detach=yes; shift ;;
        --inputs) [[ $# -ge 2 ]] || die "$USAGE"; inputs=$2; args+=("$1" "$2"); shift 2 ;;
        -*) die "$USAGE" ;;
        *) pos+=("$1"); args+=("$1"); shift ;;
    esac
done
[[ ${#pos[@]} -ge 1 && ${#pos[@]} -le 2 ]] || die "$USAGE"
SERIAL=${pos[0]}
HOSTNAME_=${pos[1]:-thinkpad}
[[ $SERIAL =~ ^[A-Za-z0-9._-]+$ ]] || die "SERIAL \"$SERIAL\" isn't a plain serial number"
[[ $HOSTNAME_ =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] || die "HOSTNAME \"$HOSTNAME_\" isn't a plain host name"
CMDLINE=${BKO_GENTOO_CMDLINE:-}
[[ $CMDLINE =~ ^[A-Za-z0-9=,._:/\ -]*$ ]] || die "BKO_GENTOO_CMDLINE has characters a kernel argument here can't"
[[ -z $inputs || $inputs == /* ]] || die "--inputs takes an absolute path"
SELF=$(readlink -f "$0")
HERE=$(dirname "$SELF")
GENTOO_SH=$HERE/gentoo.sh
# Not the caller's directory: on the drive, it would keep it busy all run.
cd /

# take_lock: $STATE/lock, flock'd on fd 9 for the whole run: one install at
# a time, and no second one truncates a running one's log.  The detached
# run inherits its parent's fd 9 (the same lock) and takes it again.
take_lock() {
    command -v flock >/dev/null || die "the stick has no flock"
    [[ /proc/$$/fd/9 -ef $STATE/lock ]] || exec 9>"$STATE/lock"
    flock -n 9 || die "already running as pid $(cat "$STATE/pid" 2>/dev/null || echo '?') ($STATE/lock is held)"
}

# --- Detaching ------------------------------------------------------------------------
if [[ $detach == yes ]]; then
    [[ $EUID -eq 0 ]] || die "run it as root"
    [[ -f $GENTOO_SH ]] || die "no $GENTOO_SH (testvm/bootstrap/gentoo.sh) beside this script"
    mkdir -p "$STATE"
    take_lock
    rm -f "$STATE/exit" "$STATE/pid"
    # Copies in RAM, so the run doesn't read scripts replaced under it.
    cp "$SELF" "$STATE/gentoo-bootstrap.sh"
    cp "$GENTOO_SH" "$STATE/gentoo.sh"
    BKO_GENTOO_LOGGED=1 setsid nohup bash "$STATE/gentoo-bootstrap.sh" "${args[@]}" >"$STATE/install.log" 2>&1 </dev/null &
    sleep 2      # the run writes its own pid
    echo "detached: pid $(cat "$STATE/pid" 2>/dev/null || echo '?'), log $STATE/install.log, exit status in $STATE/exit"
    exit 0
fi

[[ $EUID -eq 0 ]] || die "run it as root"
mkdir -p "$STATE"
take_lock      # before the trap: a refused run cleans up nothing of the other's
echo $$ >"$STATE/pid"
rm -f "$STATE/exit"
if [[ -z ${BKO_GENTOO_LOGGED:-} ]]; then
    : >"$STATE/install.log"
    # tee lives on past a dropped SSH connection (SIGHUP ignored, a dead
    # terminal only warned of), so on_exit's cleanup isn't cut short by a
    # SIGPIPE, and the log keeps all of it.
    exec > >(trap '' HUP PIPE; exec tee --output-error=warn -a "$STATE/install.log") 2>&1
fi
unset BKO_GENTOO_LOGGED

# --- Cleanup: what a failed run leaves, undone; also the first thing a rerun does ------
DISK="" LUKS_PART="" drive="" GENTOO_TMP=""
# chroot_procs: processes whose root is the new system (gpg's agents, a
# killed emerge's children): ours, by where they are, never by name.
chroot_procs() {
    local p
    for p in /proc/[0-9]*; do
        [[ $(readlink "$p/root" 2>/dev/null) == "$ROOT_MNT" ]] && echo "${p#/proc/}"
    done
    return 0
}
unmount_all() {
    local pids
    findmnt -rn "$MNT" >/dev/null || return 0
    pids=$(chroot_procs)
    if [[ -n $pids ]]; then
        echo "stopping $(wc -w <<<"$pids") processes left running in the chroot"
        # shellcheck disable=SC2086
        kill $pids 2>/dev/null || true
        sleep 2
        pids=$(chroot_procs)
        # shellcheck disable=SC2086
        [[ -z $pids ]] || kill -9 $pids 2>/dev/null || true
    fi
    umount -R "$MNT" 2>/dev/null || umount -R -l "$MNT" || true
}
# release_disk: the swap, vg0 and cryptroot on DISK off and closed, deepest
# first (only what is on DISK).
release_disk() {
    local n t vg
    [[ -n $DISK ]] || return 0
    while read -r n t; do
        [[ $t == lvm || $t == crypt || $t == part ]] || continue
        swapoff "$n" 2>/dev/null || true
    done < <(lsblk -lnpo NAME,TYPE "$DISK")
    for vg in $(lsblk -lnpo NAME,TYPE "$DISK" | awk '$2 == "crypt" || $2 == "part" { print $1 }' |
                xargs -r pvs --noheadings -o vg_name 2>/dev/null | awk 'NF' | sort -u); do
        vgchange -q -a n "$vg" >/dev/null || return 1
    done
    for n in $(lsblk -lnpo NAME,TYPE "$DISK" | awk '$2 == "crypt" { print $1 }' | sort -r); do
        cryptsetup close "$n" || return 1
    done
}
on_exit() {
    local rc=$?
    set +e
    # The cleanup runs to its end: a second signal, or a write to a gone
    # terminal, doesn't stop it halfway.
    trap '' HUP PIPE INT TERM
    unmount_all
    release_disk
    rmdir "$MNT" 2>/dev/null
    [[ -z $GENTOO_TMP ]] || rm -rf "$GENTOO_TMP"
    if [[ $rc -ne 0 ]]; then echo "gentoo-bootstrap: FAILED (exit $rc)" >&2; fi
    # The log onto the drive too: /run is gone at the next boot.
    if [[ -n $inputs && -n $drive && -d $inputs && -s $STATE/install.log ]]; then
        cp "$STATE/install.log" "$inputs/install-$(date -u +%Y%m%d-%H%M%S)-exit$rc.log" 2>/dev/null
        sync
    fi
    # Last, and whole: whoever waits for it may unplug the drive at once.
    echo "$rc" >"$STATE/exit.tmp" && mv -f "$STATE/exit.tmp" "$STATE/exit"
}
trap on_exit EXIT

# --- Where we are ---------------------------------------------------------------------
[[ -f /cdrom/.disk/backotter.json ]] || die "this isn't the BackOtter rescue stick (no /cdrom/.disk/backotter.json)"
[[ -d /sys/firmware/efi ]] || die "this system wasn't booted by UEFI"
findmnt -rn /sys/firmware/efi/efivars >/dev/null || mount -t efivarfs efivarfs /sys/firmware/efi/efivars
# The firmware's SecureBoot variable: 4 attribute bytes, then 1 if it's on.
if [[ -r $SECURE_BOOT_VAR && $(od -An -tu1 -j4 -N1 "$SECURE_BOOT_VAR" | tr -d ' ') == 1 ]]; then
    die "Secure Boot is on: Gentoo's GRUB here is unsigned (no shim); turn Secure Boot off in the firmware's setup"
fi
for t in sha256sum gpgv tar xz cryptsetup pvcreate vgcreate lvcreate vgchange pvs vgs sfdisk wipefs blockdev \
    mkfs.ext4 mkfs.vfat mkswap swapon blkid lsblk findmnt efibootmgr chroot udevadm nproc getent; do
    command -v "$t" >/dev/null || die "the stick has no $t"
done
say "stick: kernel $(uname -r), $(nproc) CPUs"
getent ahosts distfiles.gentoo.org >/dev/null || die "distfiles.gentoo.org doesn't resolve: the install needs the stick's wired network (the tree, the binhost)"

# The backup drive, as backshot1's $DRIVE finds or mounts it.
drive=$({ findmnt -nro TARGET -S "LABEL=$BACKUP_LABEL" || true; } | awk 'NR == 1')
if [[ -z $drive ]]; then
    mkdir -p /mnt/bs1-backup
    modprobe -q xfs 2>/dev/null || true
    mount "LABEL=$BACKUP_LABEL" /mnt/bs1-backup || die "can't mount LABEL=$BACKUP_LABEL"
    drive=/mnt/bs1-backup
fi
KEYS=$drive/backshot1/keys
KEY=$KEYS/disk-passphrase
for f in "$KEY" "$KEYS/authorized_keys" "$KEYS/ssh_host_ed25519_key" "$KEYS/ssh_host_ed25519_key.pub"; do
    [[ -s $f ]] || die "no $f"
done
check_passphrase "$KEY"
[[ -n $inputs ]] || inputs=$drive/backshot1/gentoo
for f in "$STAGE3" "$STAGE3.asc" "$RELEASE_KEY"; do [[ -f $inputs/$f ]] || die "no $inputs/$f"; done
[[ -f $GENTOO_SH ]] || die "no $GENTOO_SH (testvm/bootstrap/gentoo.sh) beside this script"
# shellcheck disable=SC2016  # the text to find, not an expansion
if ! grep -q '^GENTOO_METAL=\${GENTOO_METAL:-no}' "$GENTOO_SH" || ! grep -q "^GENTOO_INIT='@GENTOO_INIT@'" "$GENTOO_SH"; then
    die "$GENTOO_SH isn't a bootstrap/gentoo.sh that knows GENTOO_METAL"
fi
sha256_is() { [[ $(sha256sum <"$1" | cut -d' ' -f1) == "$2" ]] || die "$1 doesn't have the pinned SHA-256 $2"; }
sha256_is "$inputs/$STAGE3" "$STAGE3_SHA256"
sha256_is "$inputs/$STAGE3.asc" "$STAGE3_ASC_SHA256"
sha256_is "$inputs/$RELEASE_KEY" "$RELEASE_KEY_SHA256"
gpgv --keyring "$inputs/$RELEASE_KEY" "$inputs/$STAGE3.asc" "$inputs/$STAGE3" 2>&1 | grep 'Good signature' ||
    die "$STAGE3 isn't signed by $RELEASE_KEY"
say "inputs verified in $inputs; the drive at $drive"

DISK=$(find_disk "$SERIAL" "$drive") || exit 1
ESP_PART=$(part 1) LUKS_PART=$(part 2)
say "target: $DISK ($SERIAL, $(lsblk -dno SIZE,MODEL "$DISK" | tr -s ' '))"

# --- Wipe -----------------------------------------------------------------------------
unmount_all
release_disk || die "can't release what's open on $DISK"
mounted=$(lsblk -lnpo NAME,MOUNTPOINTS "$DISK" | awk 'NF > 1' || true)
[[ -z $mounted ]] || die "something on $DISK is still mounted: $(paste -sd ';' - <<<"$mounted")"
! [[ -e /dev/mapper/$MAP ]] || die "/dev/mapper/$MAP is open, and not on $DISK"
say "erasing $DISK"
lsblk -o NAME,SIZE,TYPE,FSTYPE,LABEL "$DISK"
# The old partitions' GUIDs: firmware entries that name them go below.  On
# the drive, added to and synced before the wipe (/run is gone if the
# stick reboots): a run that wiped the disk and failed has recorded the
# earlier system's, which the next run's wipe no longer shows.
ledger=$inputs/old-partuuids
lsblk -rno PARTUUID "$DISK" | awk 'NF' | tr 'A-F' 'a-f' >>"$ledger" || die "can't record $DISK's partition GUIDs in $ledger"
sync "$ledger" || die "can't sync $ledger"
old_partuuids=$(grep -E '^[0-9a-f-]+$' "$ledger" | sort -u | paste -sd ' ' - || true)
lsblk -lnpo NAME "$DISK" | sort -r | xargs -r wipefs -aq
# The first 4 MiB and the last 1 MiB (the old GPT's backup, signatures).
sectors=$(blockdev --getsz "$DISK")
dd if=/dev/zero of="$DISK" bs=1M count=4 conv=fsync status=none
dd if=/dev/zero of="$DISK" bs=512 seek=$((sectors - 2048)) count=2048 conv=fsync status=none
blockdev --rereadpt "$DISK" 2>/dev/null || true
udevadm settle

say "partitioning: ESP $ESP_SIZE, LUKS2 the rest"
sfdisk -q "$DISK" <<EOF
label: gpt
size=$ESP_SIZE, type=uefi
type=linux
EOF
udevadm settle
for _ in $(seq 20); do [[ -b $ESP_PART && -b $LUKS_PART ]] && break; sleep 1; udevadm settle; done
[[ -b $ESP_PART && -b $LUKS_PART ]] || die "no $ESP_PART and $LUKS_PART after partitioning"
for p in "$ESP_PART" "$LUKS_PART"; do dd if=/dev/zero of="$p" bs=1M count=4 conv=fsync status=none; done
# Only now: an old vg0 on this disk's partitions is gone with them.
! vgs "$VG" >/dev/null 2>&1 || die "there is a volume group $VG on another disk"

say "LUKS2 on $LUKS_PART (the drive's passphrase), $MAP, $VG: root $ROOT_SIZE, swap $SWAP_SIZE"
cryptsetup -q luksFormat --type luks2 --key-file "$KEY" "$LUKS_PART"
cryptsetup open --key-file "$KEY" "$LUKS_PART" "$MAP"
cryptsetup luksDump "$LUKS_PART" | grep -E '^(Version|UUID):'
pvcreate -q -y "/dev/mapper/$MAP"
vgcreate -q -y "$VG" "/dev/mapper/$MAP"
lvcreate -q -y -L "$ROOT_SIZE" -n root "$VG"
lvcreate -q -y -L "$SWAP_SIZE" -n swap "$VG"
udevadm settle
# shellcheck disable=SC2034  # ROOT_PART: read by gentoo.sh
ROOT_PART=/dev/$VG/root SWAP_PART=/dev/$VG/swap
mkswap -q "$SWAP_PART"
swapon "$SWAP_PART"

# --- gentoo.sh, as the guest's build runs it ------------------------------------------
# What bootstrap/common.sh gives it in the guest's build: the settings and
# the functions, for this disk and ROOT_MNT.
# shellcheck disable=SC2034  # read by gentoo.sh
{
ROOT_FS=ext4 FIRMWARE=uefi LAYOUT=luks-esp
LUKS_KEY=""                    # the guest's key file; none here
GUEST_NAME=$HOSTNAME_
ROOT_PASSWORD_HASH='*'         # root's password locked: SSH by key only
SERVER=""                      # the guest's HTTP server; the stage3 is STAGE3
SERIAL_CMDLINE=$CMDLINE
BOOT_PART="" HOME_PART=""
STAGE3=$inputs/$STAGE3
GENTOO_METAL=yes
GENTOO_TMP=$STATE/tmp          # emerge -p's plans, lsinitrd's lists: not the stick's /tmp
}
mkfs_root() { mkfs.ext4 -q -F "$1"; }
mount_boot() {
    mkfs.vfat -F 32 -n EFI "$ESP_PART" >/dev/null
    mount -t vfat --mkdir "$ESP_PART" "$ROOT_MNT/boot"
}
check_root_module() {
    compgen -G "$1/kernel/fs/$ROOT_FS/$ROOT_FS.ko*" >/dev/null ||
        grep -qs "kernel/fs/$ROOT_FS/$ROOT_FS.ko" "$1/modules.builtin" || {
        echo "the kernel ($1) can't mount $ROOT_FS"
        false
    }
}
# install_ssh_key ROOT: root's authorized_keys and the fixed host key, from the drive.
install_ssh_key() {
    install -d -m 700 "$1/root/.ssh"
    install -m 600 "$KEYS/authorized_keys" "$1/root/.ssh/authorized_keys"
    install -m 600 "$KEYS/ssh_host_ed25519_key" "$1/etc/ssh/ssh_host_ed25519_key"
    install -m 644 "$KEYS/ssh_host_ed25519_key.pub" "$1/etc/ssh/ssh_host_ed25519_key.pub"
}
gentoo_done=no
finish() { gentoo_done=yes; }

mkdir -p "$MNT"
mount -t tmpfs -o size=1M,mode=0755 bko-gentoo "$MNT"
# Private: /run is shared, and the mounts below would otherwise be copied
# into every service's own mount namespace (pika-bootstrap.sh's first run).
mount --make-private "$MNT"
mkdir "$ROOT_MNT"
rm -rf "$GENTOO_TMP"
mkdir -m 700 "$GENTOO_TMP"
sed "s/@GENTOO_INIT@/systemd/" "$GENTOO_SH" >"$STATE/gentoo-filled.sh"
! grep -n '@[A-Z_][A-Z_]*@' "$STATE/gentoo-filled.sh" || die "gentoo.sh has placeholders left unfilled"
say "gentoo.sh (GENTOO_INIT=systemd, GENTOO_METAL=yes) into $ROOT_MNT"
t0=$SECONDS
# shellcheck source=/dev/null  # testvm/bootstrap/gentoo.sh, filled in
source "$STATE/gentoo-filled.sh"
[[ $gentoo_done == yes ]] || die "gentoo.sh didn't reach its end"
say "gentoo.sh took $(( SECONDS - t0 )) s"

# --- What it made, then the target closed -------------------------------------------
say "the installed system"
echo "crypttab: $(cat "$ROOT_MNT/etc/crypttab")"
echo "fstab:"; sed 's/^/  /' "$ROOT_MNT/etc/fstab"
grep -m1 -E '^[[:space:]]*linux[[:space:]]' "$ROOT_MNT/boot/grub/grub.cfg" | sed 's/^[[:space:]]*/grub.cfg: /'
ls "$ROOT_MNT/boot" "$ROOT_MNT/boot/EFI"
echo "hostname: $(cat "$ROOT_MNT/etc/hostname")"
[[ ! -e $ROOT_MNT/etc/cryptsetup-keys.d ]] || die "the system has /etc/cryptsetup-keys.d"
# The passphrase in no file of the new system's /etc, /root or /boot (read
# by grep from the key file: never a command line).
leaks=$(grep -rlaF -f "$KEY" "$ROOT_MNT/etc" "$ROOT_MNT/root" "$ROOT_MNT/boot" 2>/dev/null || true)
[[ -z $leaks ]] || die "the passphrase is in $(paste -sd ' ' - <<<"$leaks")"
sync
unmount_all
swapoff "$SWAP_PART"
vgchange -q -a n "$VG" >/dev/null
cryptsetup close "$MAP"
rmdir "$MNT"

# --- NVRAM ----------------------------------------------------------------------------
# entries: "NUM<tab>LABEL<tab>REST" for each Boot#### (efibootmgr -v's first lines).
entries() {
    local out
    out=$(efibootmgr -v) || return 1
    awk '/^Boot[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]/ {
        n = substr($1, 5, 4); line = $0
        sub(/^Boot[0-9A-Fa-f]+\*? ?/, "", line)
        t = index(line, "\t"); if (t) { lbl = substr(line, 1, t - 1); rest = substr(line, t + 1) } else { lbl = line; rest = "" }
        printf "%s\t%s\t%s\n", n, lbl, rest }' <<<"$out"
}
say "NVRAM: stale entries for the old partitions (${old_partuuids:-none}), and earlier $BOOT_LABEL ones"
while IFS=$'\t' read -r n lbl rest; do
    low=${rest,,}
    del=no
    [[ $lbl != "$BOOT_LABEL" ]] || del=yes
    for u in $old_partuuids; do [[ $low != *"$u"* ]] || del=yes; done
    if [[ $del == yes ]]; then
        echo "deleting Boot$n ($lbl)"
        efibootmgr -q -b "$n" -B || die "can't delete Boot$n"
    fi
done < <(entries)
efibootmgr -q -c -d "$DISK" -p 1 -L "$BOOT_LABEL" -l "$BOOT_LOADER" || die "efibootmgr -c failed"
list=$(entries) || die "efibootmgr -v failed"
main=$(awk -F'\t' -v l="$BOOT_LABEL" '$2 == l && !m { m = $1 } END { print m }' <<<"$list")
[[ -n $main ]] || die "the firmware didn't keep the $BOOT_LABEL entry"
order=$(efibootmgr | awk '/^BootOrder:/ { print $2 }')
rest=$(tr ',' '\n' <<<"$order" | awk -v a="$main" 'NF && toupper($1) != toupper(a)' | paste -sd ',' -)
efibootmgr -q -o "$main${rest:+,$rest}" || die "can't set BootOrder"
ev=$(efibootmgr -v) || die "efibootmgr -v failed"
grep -E "^(BootCurrent|BootNext|BootOrder|Boot$main)" <<<"$ev" || true
esp_guid=$(lsblk -no PARTUUID "$ESP_PART" | tr 'A-F' 'a-f')
mline=$(grep -E "^Boot$main" <<<"$ev" | tr 'A-F' 'a-f' || true)
[[ -n $esp_guid && $mline == *"$esp_guid"* ]] || die "Boot$main doesn't name the ESP ($esp_guid)"
[[ $(awk '/^BootOrder:/ { print $2 }' <<<"$ev") == "$main"* ]] || die "BootOrder doesn't start with $main"
# Their entries are gone: the next run records this disk's own.
rm -f "$ledger"
sync

say "Gentoo is on $DISK: $BOOT_LABEL is Boot$main, first in BootOrder; $MAP and $VG are closed"
echo "At the first boot, systemd asks for $MAP's passphrase on the screen (the US layout);"
echo "then bs1-adopt (family S) makes the next ones unlock from $BACKUP_LABEL."
echo "BKO-GENTOO-INSTALL-OK"
