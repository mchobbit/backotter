#!/usr/bin/env bash
# Install Void Linux (musl) on a real machine from another running Linux (the
# BackOtter rescue stick, Ubuntu 26.04 base, OpenZFS 2.4): root on a natively
# encrypted ZFS pool booted by ZFSBootMenu, laid out as
# testvm/make-void-zfs.sh --encrypt lays out the void-zbm-enc guest
# (ZFSBootMenu's own "Void Linux (UEFI)" guide), adapted for musl, real
# hardware and an Xfce desktop, so that bs1-adopt's family Z applies
# (bs1-luks lk_zfs_adopt).  As root, on the rescue (never on the installed
# system nor a Void live ISO):
#
#   void-zbm-bootstrap.sh [--detach] [--inputs DIR] SERIAL [HOSTNAME [TIMEZONE]]
#
#   SERIAL    the one disk to ERASE, by serial number (lsblk -dno SERIAL): an
#             NVMe disk, /dev/disk/by-id/nvme-*_SERIAL (the pool's vdev), and
#             not the disk the backup drive is on
#   HOSTNAME  default thinkpad; TIMEZONE default Europe/Paris
#   --inputs  the directory with the two inputs (default <drive>/backshot1/void-zbm)
#   --detach  in the background, past a dropped SSH connection: the log, pid
#             and exit status in /run/bko-void-zbm/ (install.log, pid, exit)
#
# Takes no secrets.  The pool's passphrase is the backup drive's key file,
# <drive>/backshot1/keys/disk-passphrase (no final newline), LABEL=mybig512,
# mounted at /mnt/bs1-backup as backshot1's $DRIVE does when nothing has it.
# It is never printed.  The same keys/ gives root's authorized_keys and the
# fixed host key.  xbps's package cache and each run's log
# (install-DATE-exitRC.log) stay in <drive>/backshot1/void-zbm.  Rerunnable
# after a failure: every run starts again from the wipe.
#
# Inputs, in DIR, each pinned by SHA-256 below (their signatures are checked
# on the build hosts, where minisign and signify are; backshot1/README.md,
# "Void musl on encrypted ZFS"):
#   void-x86_64-musl-ROOTFS-20250202.tar.xz
#   zfsbootmenu-release-x86_64-v3.1.0-linux6.12.EFI
#
# The disk (WIPED in full):
#   p1  1 GiB ESP (ef00, FAT32, label EFI), /boot/efi: EFI/ZBM/VMLINUZ.EFI and
#       VMLINUZ-BACKUP.EFI (the release image as released, unsigned; bs1-adopt
#       rewrites VMLINUZ.EFI's .cmdline itself), and a copy of the backup at
#       the removable path EFI/BOOT/BOOTX64.EFI (bs1-adopt never touches it:
#       booted that way, ZBM always asks for the passphrase)
#   p2  the rest but 10 MiB (bf00): pool zroot, compatibility=openzfs-2.3-linux
#       (stick 2.4.x, ZBM 3.1.0's 2.4.0, Void's 2.4.4 all import it),
#       ashift=12, zstd, aes-256-gcm, keyformat=passphrase,
#       keylocation=file:///etc/zfs/zroot.key, org.zfsbootmenu:keysource=
#       zroot/ROOT/void, bootfs=zroot/ROOT/void, no cache file
#         zroot/ROOT           none   org.zfsbootmenu:commandline="loglevel=4"
#         zroot/ROOT/void      /      noauto  BE: Void's default kernel + a newer series
#         zroot/ROOT/void-SER  /      noauto  BE: clone of void@pre-mainline, default kernel only
#         zroot/home           /home
#         zroot/var            /var   canmount=off
#         zroot/var/log        /var/log  (shared by both BEs)
# hostid 0x00bab10c (the guide's, ZBM's release image's spl_hostid).
# Void: base-system, zfs (DKMS), dracut with zol.conf + the key file, Xfce,
# LightDM, dbus, elogind, polkit, NetworkManager, PipeWire, sshd, avahi,
# chrony, firmware; HOSTNAME, TIMEZONE; user m (wheel, no password until
# someone sets one); root by SSH key only (no password either); BackShot1's
# fixed host key and authorized_keys from <drive>/backshot1/keys.  /etc/bko-be
# names each BE.  The packages come from Void's mirror: this needs the
# stick's wired network.
# NVRAM: "ZFSBootMenu (Backup)" then "ZFSBootMenu" (first in BootOrder; its
# number in /run/bko-void-zbm/zbm-entry, ZBM_ENTRY=NNNN); stale entries
# naming the wiped disk's old partitions are deleted.
#
# What the person types: nothing while it runs.  At the first boot,
# ZFSBootMenu asks "Enter passphrase for 'zroot':" once: disk-passphrase's
# passphrase, on the US layout whatever the keys say (the release image sets
# no keymap).  The BE's initramfs then opens the pool with
# /etc/zfs/zroot.key.  After bs1-adopt (family Z, UNLOCK=drive) nobody types.
#
# Two musl gotchas (each failed a run on the Thinkpad):
#   - The ROOTFS's /usr/lib/ld-musl-x86_64.so.1 is an absolute symlink (to
#     /usr/lib64/libc.so): seen from the stick, -e follows it into the
#     stick's own /usr, so the musl check takes -L too.
#   - /etc/hostid is in dracut's install_items: dracut's 90zfs copies it only
#     in hostonly mode (zol.conf says hostonly="no"), and its fallback,
#     hostid(1), is always 00000000 on musl (gethostid() is a stub), so the
#     BE's initramfs would have no host ID to import the pool ZFSBootMenu
#     imported as 0x00bab10c.  check_initramfs refuses an image without it.
#
# Run on the Thinkpad (Lenovo A100) 2026-11-19; then bs1-adopt family Z
# (UNLOCK=drive, unattended) and BackShot1 PASS, run void-20261119-133257.
# Prints BKO-VOID-ZBM-INSTALL-OK.

set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C

USAGE="usage: void-zbm-bootstrap.sh [--detach] [--inputs DIR] SERIAL [HOSTNAME [TIMEZONE]]"
BACKUP_LABEL=mybig512
USER_=m
POOL=zroot
ESP_SIZE=+1G
MIRROR=https://repo-default.voidlinux.org/current/musl
ROOTFS=void-x86_64-musl-ROOTFS-20250202.tar.xz
ROOTFS_SHA256=27b2945ea7e2ca2a6ab123ce6f284b255adea2182820da25483d700ffe16e7c2
ZBM_EFI=zfsbootmenu-release-x86_64-v3.1.0-linux6.12.EFI
ZBM_EFI_SHA256=131d55c7b385df8cd319d267b6aa15d97f8c32c94f5149bd906556715fadf4e7
ZBM_COMMANDLINE="loglevel=4"
COMPAT=openzfs-2.3-linux
HOSTID=0x00bab10c
STATE=/run/bko-void-zbm
ROOT=/mnt/bko-void-zbm          # the pool's altroot: the new system
BE2_MNT=/mnt/bko-void-zbm-be    # the second BE, for its marker
CHROOT_PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# The guide's: the default kernel's headers, OpenZFS (DKMS), dracut, efibootmgr.
ZFS_PKGS=(linux-headers zfs dracut efibootmgr)
# Everything else, in both BEs.
PKGS=(
    binutils python3 bash sudo
    linux-firmware linux-firmware-intel sof-firmware
    dbus elogind polkit NetworkManager network-manager-applet
    openssh avahi chrony
    xorg-minimal mesa-dri dejavu-fonts-ttf noto-fonts-ttf adwaita-icon-theme
    xfce4 xfce4-pulseaudio-plugin xfce4-whiskermenu-plugin xfce4-notifyd xfce4-screensaver
    lightdm lightdm-gtk-greeter gvfs xdg-user-dirs xdg-utils
    pipewire wireplumber alsa-utils pavucontrol
    nano less curl rsync
)
# runit services enabled in both BEs (elogind may be absent as a service).
# Not acpid (base-system's): elogind handles the lid and power keys.
SERVICES=(dbus NetworkManager sshd avahi-daemon chronyd lightdm)

die() { echo "void-zbm-bootstrap: $*" >&2; exit 1; }
say() { echo "== $(date -u +%H:%M:%S) $*"; }

# --- Arguments ------------------------------------------------------------------------
detach=no
inputs=""
pos=()
args=()       # the arguments but --detach, for the detached run
while [[ $# -gt 0 ]]; do
    case $1 in
        --detach) detach=yes; shift ;;
        --inputs) inputs=${2:?--inputs DIR}; args+=("$1" "$2"); shift 2 ;;
        -*) die "$USAGE" ;;
        *) pos+=("$1"); args+=("$1"); shift ;;
    esac
done
[[ ${#pos[@]} -ge 1 && ${#pos[@]} -le 3 ]] || die "$USAGE"
SERIAL=${pos[0]}
HOSTNAME_=${pos[1]:-thinkpad}
TZ_=${pos[2]:-Europe/Paris}
[[ $SERIAL =~ ^[A-Za-z0-9._-]+$ ]] || die "SERIAL \"$SERIAL\" isn't a plain serial number"
[[ $HOSTNAME_ =~ ^[a-z0-9][a-z0-9-]*$ ]] || die "HOSTNAME \"$HOSTNAME_\" isn't a plain host name"
[[ $TZ_ =~ ^[A-Za-z_]+(/[A-Za-z0-9_+-]+)*$ ]] || die "TIMEZONE \"$TZ_\" isn't a time zone's name"

# --- Detaching ------------------------------------------------------------------------
if [[ $detach == yes ]]; then
    [[ $EUID -eq 0 ]] || die "must run as root"
    mkdir -p "$STATE"
    if [[ -s $STATE/pid ]] && kill -0 "$(cat "$STATE/pid")" 2>/dev/null; then
        die "already running as pid $(cat "$STATE/pid")"
    fi
    rm -f "$STATE/exit" "$STATE/pid"
    self=$(readlink -f "$0")
    # A copy in RAM, so the run doesn't read a script that is replaced under it.
    cp "$self" "$STATE/void-zbm-bootstrap.sh"
    setsid nohup bash "$STATE/void-zbm-bootstrap.sh" "${args[@]}" >"$STATE/install.log" 2>&1 </dev/null &
    sleep 2      # the run writes its own pid
    echo "detached: pid $(cat "$STATE/pid" 2>/dev/null || echo '?'), log $STATE/install.log, exit status in $STATE/exit"
    exit 0
fi

[[ $EUID -eq 0 ]] || die "must run as root"
mkdir -p "$STATE"
echo $$ >"$STATE/pid"
rm -f "$STATE/exit"

# --- Cleanup: what a failed run leaves, undone; also the first thing a rerun does ------
unmount_all() {
    local m
    if mountpoint -q "$BE2_MNT" 2>/dev/null; then umount "$BE2_MNT" || umount -l "$BE2_MNT"; fi
    if [[ -d $ROOT ]]; then
        # Deepest first; zfs datasets too (umount works for them).
        while read -r m; do
            umount "$m" 2>/dev/null || umount -l "$m" 2>/dev/null || true
        done < <(findmnt -rn -o TARGET | awk -v r="$ROOT" '$0 == r || index($0, r "/") == 1' | sort -r)
    fi
}
export_pool() {
    if zpool list -H -o name "$POOL" >/dev/null 2>&1; then
        zpool export "$POOL" 2>/dev/null || zpool export -f "$POOL" || true
    fi
}
finish() {
    local rc=$?
    set +e
    unmount_all
    export_pool
    rmdir "$BE2_MNT" "$ROOT" 2>/dev/null
    rm -rf "${scratch:-/nonexistent-void-zbm}"
    echo "$rc" >"$STATE/exit"
    # The log onto the drive too: /run is gone at the next boot.
    if [[ -n ${drive:-} && -d $drive/backshot1/void-zbm && -f $STATE/install.log ]]; then
        cp "$STATE/install.log" "$drive/backshot1/void-zbm/install-$(date -u +%Y%m%d-%H%M%S)-exit$rc.log" 2>/dev/null
        sync
    fi
    if [[ $rc -ne 0 ]]; then echo "void-zbm-bootstrap: FAILED (exit $rc)" >&2; fi
}
scratch=""
drive=""
trap finish EXIT

# --- Where we are ---------------------------------------------------------------------
[[ -f /cdrom/.disk/backotter.json ]] || die "this isn't the BackOtter rescue stick (no /cdrom/.disk/backotter.json)"
[[ -d /sys/firmware/efi ]] || die "not booted by UEFI"
for t in zpool zfs zgenhostid sgdisk wipefs blkdiscard mkfs.vfat blkid lsblk findmnt efibootmgr tar xz \
    sha256sum getcap chroot udevadm blockdev partx cmp install mountpoint; do
    command -v "$t" >/dev/null || die "the stick has no $t"
done
modprobe zfs 2>/dev/null || true
[[ -e /dev/zfs ]] || die "no /dev/zfs: the zfs module didn't load"
say "stick: $(zfs version 2>/dev/null | paste -sd ' ' -), kernel $(uname -r)"
[[ -f /usr/share/zfs/compatibility.d/$COMPAT ]] || die "the stick's OpenZFS has no compatibility file $COMPAT"
findmnt -rn /sys/firmware/efi/efivars >/dev/null || mount -t efivarfs efivarfs /sys/firmware/efi/efivars

# The backup drive, as backshot1's $DRIVE finds or mounts it.
drive=$({ findmnt -nro TARGET -S "LABEL=$BACKUP_LABEL" || true; } | awk 'NR == 1')
if [[ -z $drive ]]; then
    mkdir -p /mnt/bs1-backup
    modprobe -q xfs 2>/dev/null || true
    mount "LABEL=$BACKUP_LABEL" /mnt/bs1-backup || die "can't mount LABEL=$BACKUP_LABEL"
    drive=/mnt/bs1-backup
fi
keys=$drive/backshot1/keys
KEY=$keys/disk-passphrase
[[ $KEY =~ ^/[A-Za-z0-9._/-]+$ ]] || die "the key's path $KEY has characters keylocation=file:// can't take"
for f in "$KEY" "$keys/ssh_host_ed25519_key" "$keys/ssh_host_ed25519_key.pub" "$keys/authorized_keys"; do
    [[ -s $f ]] || die "no $f"
done
# The passphrase's shape, never its bytes: 8 to 512 of them, no CR or LF.
ksize=$(wc -c <"$KEY")
(( ksize >= 8 && ksize <= 512 )) || die "$KEY is $ksize bytes: a ZFS passphrase is 8 to 512"
[[ $(tr -dc '\r\n' <"$KEY" | wc -c) -eq 0 ]] || die "$KEY has a newline or CR in it (it must be printf '%s' PASSPHRASE)"
[[ -n $inputs ]] || inputs=$drive/backshot1/void-zbm
for f in "$ROOTFS" "$ZBM_EFI"; do [[ -f $inputs/$f ]] || die "no $inputs/$f"; done
sha256_is() { [[ $(sha256sum <"$1" | cut -d' ' -f1) == "$2" ]] || die "$1 doesn't have the pinned SHA-256 $2"; }
sha256_is "$inputs/$ROOTFS" "$ROOTFS_SHA256"
sha256_is "$inputs/$ZBM_EFI" "$ZBM_EFI_SHA256"
[[ $(head -c 2 "$inputs/$ZBM_EFI") == MZ ]] || die "$ZBM_EFI isn't a PE image"
say "inputs verified in $inputs; the drive at $drive"

# The disk: exactly one with the serial, an NVMe one, not the stick or the drive.
disk=$(lsblk -dnro NAME,SERIAL | awk -v s="$SERIAL" '$2 == s { print $1 }')
[[ -n $disk && $(wc -l <<<"$disk") -eq 1 ]] || die "not exactly one disk has serial $SERIAL (found: ${disk:-none})"
[[ $(lsblk -dno TRAN "/dev/$disk" | tr -d ' ') == nvme ]] || die "/dev/$disk (serial $SERIAL) isn't an NVMe disk"
[[ $(lsblk -dno SERIAL "/dev/$disk" | tr -d ' ') == "$SERIAL" ]] || die "/dev/$disk's serial changed"
DEV=/dev/$disk
byid=$(find /dev/disk/by-id -maxdepth 1 -name "nvme-*_$SERIAL" ! -name '*-part*' | sort | awk 'NR == 1')
[[ -n $byid && $(readlink -f "$byid") == "$DEV" ]] || die "no /dev/disk/by-id/nvme-*_$SERIAL for $DEV"
drive_disk=$(lsblk -no PKNAME "$(findmnt -nro SOURCE --target "$drive")" | awk 'NR == 1')
[[ $drive_disk != "$disk" ]] || die "the backup drive is on $DEV"
say "target: $DEV ($byid), $(lsblk -dno SIZE "$DEV" | tr -d ' ')"

# --- Wipe ------------------------------------------------------------------------------
say "wipe: $DEV, all of it"
unmount_all
export_pool
# Any other imported pool with a vdev on this disk (the stick imports none by
# itself; a pool from a failed run is zroot, done above).
while read -r p; do
    [[ -n $p ]] || continue
    vdevs=$(zpool list -vHPL "$p" 2>/dev/null | awk '$1 ~ /^\// { print $1 }' || true)
    under=""
    for v in $vdevs; do under+=$(lsblk -snlo NAME "$v" 2>/dev/null || true)$'\n'; done
    if awk -v d="$disk" '$1 == d { f = 1 } END { exit !f }' <<<"$under"; then
        say "exporting pool $p (on $DEV)"
        zpool export -f "$p" || die "can't export pool $p from $DEV"
    fi
done < <(zpool list -Ho name 2>/dev/null || true)
mounted=$(findmnt -rn -o SOURCE,TARGET | awk -v d="$DEV" 'index($1, d) == 1' || true)
[[ -z $mounted ]] || die "something on $DEV is still mounted: $(paste -sd ';' - <<<"$mounted")"
swapoff -a 2>/dev/null || true
# The old partitions' GUIDs: firmware entries that name them go below.
old_partuuids=$(lsblk -rno PARTUUID "$DEV" | awk 'NF' | tr 'A-F' 'a-f' | paste -sd ' ' -)
for p in $(lsblk -rnpo NAME "$DEV" | awk 'NR > 1'); do
    if [[ $(blkid -o value -s TYPE "$p" 2>/dev/null) == zfs_member ]]; then
        zpool labelclear -f "$p" || die "zpool labelclear $p failed"
    fi
    wipefs -aq "$p" || true
done
wipefs -aq "$DEV" || true
sgdisk --zap-all "$DEV" >/dev/null || die "sgdisk --zap-all $DEV failed"
blkdiscard -f "$DEV" 2>/dev/null || say "note: blkdiscard $DEV failed; the disk is zapped, not discarded"
udevadm settle
say "partition: ESP $ESP_SIZE, pool the rest less 10 MiB"
sgdisk -n "1:1M:$ESP_SIZE" -t 1:ef00 -c 1:EFI -n 2:0:-10M -t 2:bf00 -c 2:zroot "$DEV" >/dev/null || die "sgdisk failed"
blockdev --rereadpt "$DEV" 2>/dev/null || partx -u "$DEV" 2>/dev/null || true
udevadm settle
for _ in $(seq 20); do [[ -e $byid-part1 && -e $byid-part2 ]] && break; sleep 1; udevadm settle; done
[[ -b $byid-part1 && -b $byid-part2 ]] || die "no $byid-part1 and -part2 after partitioning"
ESP_DEV=$(readlink -f "$byid-part1")
POOL_VDEV=$byid-part2
mkfs.vfat -F 32 -n EFI "$ESP_DEV" >/dev/null || die "mkfs.vfat $ESP_DEV failed"

# --- Pool and datasets -----------------------------------------------------------------
scratch=$(mktemp -d)
zgenhostid -o "$scratch/hostid" "$HOSTID"
[[ $(od -An -tx1 "$scratch/hostid" | tr -d ' \n') == 0cb1ba00 ]] || die "zgenhostid didn't write $HOSTID"
mkdir -p "$ROOT"
say "pool $POOL on $POOL_VDEV (encrypted; the key from the drive's key file)"
zpool create -f -R "$ROOT" -o cachefile=none -o ashift=12 -o autotrim=on -o "compatibility=$COMPAT" \
    -O compression=zstd -O acltype=posixacl -O xattr=sa -O relatime=on -O dnodesize=auto \
    -O encryption=aes-256-gcm -O keyformat=passphrase -O "keylocation=file://$KEY" \
    -m none "$POOL" "$POOL_VDEV" || die "zpool create failed"
zfs set keylocation=file:///etc/zfs/zroot.key "$POOL"
zfs set "org.zfsbootmenu:keysource=$POOL/ROOT/void" "$POOL"
zfs create -o mountpoint=none "$POOL/ROOT"
zfs set "org.zfsbootmenu:commandline=$ZBM_COMMANDLINE" "$POOL/ROOT"
zfs create -o mountpoint=/ -o canmount=noauto "$POOL/ROOT/void"
zpool set bootfs="$POOL/ROOT/void" "$POOL"
# / first: the rest mount themselves at once, and must land inside it.
zfs mount "$POOL/ROOT/void"
zfs create -o mountpoint=/home "$POOL/home"
zfs create -o mountpoint=/var -o canmount=off "$POOL/var"
zfs create "$POOL/var/log"
for m in home var/log; do mountpoint -q "$ROOT/$m" || zfs mount "$POOL/$m"; done
if ! mountpoint -q "$ROOT/home" || ! mountpoint -q "$ROOT/var/log"; then die "zroot/home or zroot/var/log didn't mount under $ROOT"; fi

# --- Void ------------------------------------------------------------------------------
say "unpacking $ROOTFS"
# --xattrs: the tarball's file capabilities (SCHILY.xattr.security.capability).
tar --xattrs --xattrs-include='*' --numeric-owner -xpJf "$inputs/$ROOTFS" -C "$ROOT"
grep -qE '^ID="?void"?$' "$ROOT/etc/os-release" || die "/etc/os-release doesn't say Void"
[[ -L $ROOT/usr/lib/ld-musl-x86_64.so.1 || -e $ROOT/usr/lib/ld-musl-x86_64.so.1 ]] || die "the ROOTFS isn't musl"   # an absolute link to /usr/lib64/libc.so
caps_ok() { [[ $(getcap "$ROOT/usr/bin/iputils-ping" 2>/dev/null) == *cap_net_raw* ]]; }
caps_ok || say "note: /usr/bin/iputils-ping has no file capabilities after the unpack; iputils is reinstalled below"
install -m 644 "$scratch/hostid" "$ROOT/etc/hostid"

# A /dev of its own (as make-void-zfs.sh's): no /dev/zfs, no disks, so nothing
# in the chroot reaches the pool through Void's newer userland.
mount -t tmpfs -o mode=755,nosuid tmpfs "$ROOT/dev"
copy_node() {
    mknod -m "$(stat -c %a "$1")" "$ROOT$1" c "$((16#$(stat -c %t "$1")))" "$((16#$(stat -c %T "$1")))"
}
for n in /dev/null /dev/zero /dev/full /dev/random /dev/urandom /dev/tty; do copy_node "$n"; done
mkdir "$ROOT/dev/pts" "$ROOT/dev/shm"
mount -t devpts -o newinstance,ptmxmode=0666,mode=620 devpts "$ROOT/dev/pts"
ln -s pts/ptmx "$ROOT/dev/ptmx"
ln -s /proc/self/fd "$ROOT/dev/fd"
ln -s /proc/self/fd/0 "$ROOT/dev/stdin"; ln -s /proc/self/fd/1 "$ROOT/dev/stdout"; ln -s /proc/self/fd/2 "$ROOT/dev/stderr"
mount -t proc proc "$ROOT/proc"
mount --bind "$ROOT/proc/sys" "$ROOT/proc/sys"
mount -o remount,bind,ro "$ROOT/proc/sys"
mount --bind /sys "$ROOT/sys"
mount -o remount,bind,ro "$ROOT/sys"
mount -t tmpfs -o mode=755 tmpfs "$ROOT/run"
mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs "$ROOT/tmp"
# xbps's cache on the drive, so a rerun downloads only what changed (xbps
# checks every package's signature all the same); never in either BE.
cache=$drive/backshot1/void-zbm/xbps-cache
mkdir -p "$cache" "$ROOT/var/cache/xbps"
mount --bind "$cache" "$ROOT/var/cache/xbps"
# The stick's resolver: systemd-resolved's upstream list, not its 127.0.0.53
# stub, where there is one.
resolv() {
    rm -f "$ROOT/etc/resolv.conf"
    [[ $1 == off ]] && return 0
    if [[ -s /run/systemd/resolve/resolv.conf ]]; then cp /run/systemd/resolve/resolv.conf "$ROOT/etc/resolv.conf"
    else cp -L /etc/resolv.conf "$ROOT/etc/resolv.conf"; fi
}
resolv on
in_root() { chroot "$ROOT" /usr/bin/env -i PATH="$CHROOT_PATH" HOME=/root TERM=dumb LANG=C "$@"; }
# xbps CMD...: up to 3 tries, a minute apart (xbps downloads before it changes anything).
xbps() {
    local try
    for try in 1 2 3; do
        in_root "$@" && return 0
        echo "note: $* failed (try $try)" >&2
        sleep 60
    done
    die "$* failed"
}
# xbps_sync: the index fetched for sure: xbps-install -S goes on with the
# tarball's stale one when the fetch fails, and exits 0.
xbps_sync() {
    local try out
    for try in 1 2 3 4 5; do
        out=$(in_root xbps-install -S 2>&1) || true
        printf '%s\n' "$out"
        grep -q 'ERROR' <<<"$out" || return 0
        echo "note: xbps-install -S failed (try $try)" >&2
        sleep 60
    done
    die "xbps-install -S can't fetch the repository's index"
}

say "configuration before the kernel's initramfs"
cat >>"$ROOT/etc/rc.conf" <<'EOF'
KEYMAP="us"
HARDWARECLOCK="UTC"
EOF
[[ -f $ROOT/usr/share/zoneinfo/$TZ_ ]] || die "no time zone $TZ_ in the ROOTFS"
ln -sf "/usr/share/zoneinfo/$TZ_" "$ROOT/etc/localtime"
# musl: no libc-locales, nothing to generate.  /etc/hostid by install_items: 90zfs copies it only in hostonly mode, and its fallback, hostid(1), is always 00000000 on musl.
mkdir -p "$ROOT/etc/dracut.conf.d" "$ROOT/etc/zfs"
cat >"$ROOT/etc/dracut.conf.d/zol.conf" <<'EOF'
nofsck="yes"
hostonly="no"
add_dracutmodules+=" zfs "
omit_dracutmodules+=" btrfs "
add_drivers+=" nvme "
install_items+=" /etc/zfs/zroot.key /etc/hostid "
EOF
# The guide's key file: the drive's key, byte for byte, mode 000; in every
# initramfs (install_items), so the BE's initramfs opens the pool after
# ZFSBootMenu's prompt.
install -m 000 "$KEY" "$ROOT/etc/zfs/zroot.key"
cmp -s "$KEY" "$ROOT/etc/zfs/zroot.key" || die "/etc/zfs/zroot.key isn't the drive's key"
rm -f "$ROOT/etc/zfs/zpool.cache"
mkdir -p "$ROOT/etc/xbps.d"
echo "repository=$MIRROR" >"$ROOT/etc/xbps.d/00-repository-main.conf"

say "xbps: xbps itself, the system, base-system"
xbps_sync
xbps xbps-install -uy xbps
xbps_sync
xbps xbps-install -yu
xbps xbps-install -y base-system
in_root xbps-remove -y base-container-full || true
caps_ok || xbps xbps-install -fy iputils
caps_ok || die "/usr/bin/iputils-ping has no file capabilities"
kpkg=$(in_root xbps-query -x linux | sed -n 's/^\(linux[0-9][0-9.]*\)[<>=].*/\1/p' | awk 'NR == 1')
[[ -n $kpkg ]] || die "can't tell which kernel series the linux package is"
BE2=void-${kpkg#linux}
say "Void's default kernel: $kpkg (the second BE: $BE2); now ${ZFS_PKGS[*]}"
xbps xbps-install -y "${ZFS_PKGS[@]}"
kvers() { ls "$ROOT/usr/lib/modules"; }
[[ $(kvers | wc -l) -eq 1 ]] || die "more than one kernel after base-system: $(kvers | tr '\n' ' ')"
kver=$(kvers)
has_zfs_ko() { [[ -n $(find "$ROOT/usr/lib/modules/$1" -name 'zfs.ko*' -print -quit) ]]; }
has_zfs_ko "$kver" || die "DKMS built no zfs module for $kver"
say "the rest: ${PKGS[*]}"
xbps xbps-install -y "${PKGS[@]}"
[[ $(kvers | wc -l) -eq 1 ]] || die "the desktop brought another kernel: $(kvers | tr '\n' ' ')"
# The kernel's hooks again (DKMS, dracut), now with zfs and the firmware: its initramfs.
xbps xbps-reconfigure -f "$kpkg"
# check_initramfs KVER: OpenZFS, the host ID, the key file, NVMe; no cache file.
check_initramfs() {
    local img=/boot/initramfs-$1.img list=$scratch/initrd-$1.list
    [[ -f $ROOT$img ]] || die "no $img"
    in_root lsinitrd "$img" >"$list" 2>/dev/null || die "lsinitrd $img failed"
    grep -q '/zfs\.ko' "$list" || die "$img has no zfs module"
    grep -q ' etc/hostid$' "$list" || die "$img has no /etc/hostid"
    grep -q ' etc/zfs/zroot\.key$' "$list" || die "$img has no /etc/zfs/zroot.key"
    grep -q '/nvme\.ko' "$list" || grep -qs '/nvme\.ko' "$ROOT/usr/lib/modules/$1/modules.builtin" || die "$img has no nvme driver"
    ! grep -q 'zpool\.cache' "$list" || die "$img has a zpool.cache"
    say "initramfs $img: zfs, hostid, key, nvme"
}
check_initramfs "$kver"
say "kernel $kpkg ($kver); OpenZFS in Void: $(in_root xbps-query -p pkgver zfs)"

say "identity: $HOSTNAME_, root by key only, user $USER_, the fixed host key"
echo "$HOSTNAME_" >"$ROOT/etc/hostname"
printf '127.0.0.1\tlocalhost\n::1\t\tlocalhost\n127.0.1.1\t%s\n' "$HOSTNAME_" >"$ROOT/etc/hosts"
# The ROOTFS's root password (voidlinux) goes: '*' takes no password, and
# isn't "locked" ('!'), which sshd would refuse even a key for.
in_root usermod -p '*' root
groups=wheel
for g in audio video input network storage plugdev lp scanner; do
    if grep -q "^$g:" "$ROOT/etc/group"; then groups+=",$g"; fi
done
in_root sh -c "id $USER_ >/dev/null 2>&1 || useradd -m -s /bin/bash -c 'm' -G $groups $USER_"
in_root usermod -p '*' "$USER_"         # no password until someone runs passwd m
echo '%wheel ALL=(ALL:ALL) ALL' >"$ROOT/etc/sudoers.d/10-wheel"
chmod 440 "$ROOT/etc/sudoers.d/10-wheel"
install -d -m 700 "$ROOT/root/.ssh"
install -m 600 "$keys/authorized_keys" "$ROOT/root/.ssh/authorized_keys"
rm -f "$ROOT"/etc/ssh/ssh_host_*
install -m 600 "$keys/ssh_host_ed25519_key" "$ROOT/etc/ssh/ssh_host_ed25519_key"
install -m 644 "$keys/ssh_host_ed25519_key.pub" "$ROOT/etc/ssh/ssh_host_ed25519_key.pub"
grep -q '^Include /etc/ssh/sshd_config.d/\*\.conf' "$ROOT/etc/ssh/sshd_config" || die "Void's sshd_config has no Include of sshd_config.d"
mkdir -p "$ROOT/etc/ssh/sshd_config.d"
printf '# The BackShot1 testbed: root by key only.\nPermitRootLogin prohibit-password\n' >"$ROOT/etc/ssh/sshd_config.d/10-backshot1.conf"
in_root ssh-keygen -A >/dev/null       # the other types, as Void's run script would
in_root /usr/bin/sshd -t || say "note: sshd -t complains in the chroot (see above)"

say "services: ${SERVICES[*]} (+ elogind), not dhcpcd (NetworkManager has the network)"
for s in "${SERVICES[@]}"; do
    [[ -d $ROOT/etc/sv/$s ]] || die "no runit service $s"
    ln -sfn "/etc/sv/$s" "$ROOT/etc/runit/runsvdir/default/$s"
done
if [[ -d $ROOT/etc/sv/elogind ]]; then ln -sfn /etc/sv/elogind "$ROOT/etc/runit/runsvdir/default/elogind"; fi
rm -f "$ROOT/etc/runit/runsvdir/default/dhcpcd" "$ROOT/etc/runit/runsvdir/default/wpa_supplicant" "$ROOT/etc/runit/runsvdir/default/acpid"
# LightDM: the GTK greeter, into Xfce.
mkdir -p "$ROOT/etc/lightdm/lightdm.conf.d"
printf '[Seat:*]\ngreeter-session=lightdm-gtk-greeter\nuser-session=xfce\n' >"$ROOT/etc/lightdm/lightdm.conf.d/10-backshot1.conf"
[[ -e $ROOT/usr/share/xsessions/xfce.desktop ]] || die "no /usr/share/xsessions/xfce.desktop"
[[ -e $ROOT/usr/share/xgreeters/lightdm-gtk-greeter.desktop ]] || die "no lightdm-gtk-greeter in /usr/share/xgreeters"
say "enabled: $(find "$ROOT/etc/runit/runsvdir/default" -mindepth 1 -maxdepth 1 -printf '%f\n' | sort | paste -sd ' ' -)"
# PipeWire as Void's handbook sets it up: wireplumber and pipewire-pulse from
# pipewire's own configuration, pipewire started with the session.
mkdir -p "$ROOT/etc/pipewire/pipewire.conf.d" "$ROOT/etc/xdg/autostart"
for f in /usr/share/examples/wireplumber/10-wireplumber.conf /usr/share/examples/pipewire/20-pipewire-pulse.conf; do
    if [[ -e $ROOT$f ]]; then ln -sfn "$f" "$ROOT/etc/pipewire/pipewire.conf.d/${f##*/}"; else say "note: no $f"; fi
done
if [[ -e $ROOT/usr/share/applications/pipewire.desktop ]]; then
    ln -sfn /usr/share/applications/pipewire.desktop "$ROOT/etc/xdg/autostart/pipewire.desktop"
fi

say "ESP: ZFSBootMenu's release image, VMLINUZ.EFI and VMLINUZ-BACKUP.EFI, and EFI/BOOT/BOOTX64.EFI"
esp_uuid=$(blkid -s UUID -o value "$ESP_DEV")
[[ $esp_uuid =~ ^[0-9A-F]{4}-[0-9A-F]{4}$ ]] || die "the ESP has no FAT volume ID ($esp_uuid)"
echo "UUID=$esp_uuid /boot/efi vfat defaults 0 0" >>"$ROOT/etc/fstab"
mkdir -p "$ROOT/boot/efi"
mount -t vfat "$ESP_DEV" "$ROOT/boot/efi"
mkdir -p "$ROOT/boot/efi/EFI/ZBM" "$ROOT/boot/efi/EFI/BOOT"
cp "$inputs/$ZBM_EFI" "$ROOT/boot/efi/EFI/ZBM/VMLINUZ.EFI"
cp "$inputs/$ZBM_EFI" "$ROOT/boot/efi/EFI/ZBM/VMLINUZ-BACKUP.EFI"
cp "$inputs/$ZBM_EFI" "$ROOT/boot/efi/EFI/BOOT/BOOTX64.EFI"
for f in ZBM/VMLINUZ.EFI ZBM/VMLINUZ-BACKUP.EFI BOOT/BOOTX64.EFI; do
    sha256_is "$ROOT/boot/efi/EFI/$f" "$ZBM_EFI_SHA256"
done
sync

# --- The second BE ---------------------------------------------------------------------
say "BEs: void and its clone $BE2, before the newer kernel"
echo void >"$ROOT/etc/bko-be"
resolv off
sync
zfs snapshot "$POOL/ROOT/void@pre-mainline"
zfs clone -o mountpoint=/ -o canmount=noauto -o "org.zfsbootmenu:commandline=%{parent} bko.be=$BE2" \
    "$POOL/ROOT/void@pre-mainline" "$POOL/ROOT/$BE2"
mkdir -p "$BE2_MNT"
mount -t zfs -o zfsutil "$POOL/ROOT/$BE2" "$BE2_MNT"
echo "$BE2" >"$BE2_MNT/etc/bko-be"
sync
umount "$BE2_MNT"
rmdir "$BE2_MNT"

# The newer kernel, in void only: linux-mainline when Void's OpenZFS builds for
# its series (META's Linux-Maximum), else the newest series it builds for.
resolv on
zmax=$(sed -n 's/^Linux-Maximum: *//p' "$ROOT"/usr/src/zfs-*/META | awk 'NR == 1')
[[ $zmax =~ ^[0-9]+\.[0-9]+$ ]] || die "can't read Linux-Maximum from /usr/src/zfs-*/META"
series_le() { [[ $(printf '%s\n' "$1" "$2" | sort -V | awk 'NR == 1') == "$1" ]]; }   # A <= B
mlpkg=$(in_root xbps-query -R -x linux-mainline | sed -n 's/^\(linux[0-9][0-9.]*\)[<>=].*/\1/p' | awk 'NR == 1')
if [[ -n $mlpkg ]] && series_le "${mlpkg#linux}" "$zmax"; then
    mpkgs=(linux-mainline linux-mainline-headers) mkpkg=$mlpkg
else
    mkpkg=$(in_root xbps-query -Rs linux | sed -n 's/^\[.\] \(linux[0-9][0-9]*\.[0-9][0-9]*\)-[0-9].*/\1/p' |
        while read -r k; do if series_le "${k#linux}" "$zmax"; then echo "$k"; fi; done | sort -V | awk 'END { print }')
    [[ -n $mkpkg ]] || die "no kernel series in the repository that zfs (Linux-Maximum $zmax) builds for"
    mpkgs=("$mkpkg" "$mkpkg-headers")
fi
# shellcheck disable=SC2015  # as make-void-zfs.sh: both must hold
[[ $mkpkg != "$kpkg" ]] && ! series_le "${mkpkg#linux}" "${kpkg#linux}" ||
    die "no kernel series newer than $kpkg that zfs (Linux-Maximum $zmax) builds for (linux-mainline is ${mlpkg:-?})"
say "xbps: ${mpkgs[*]} (linux-mainline is ${mlpkg:-?}, zfs's Linux-Maximum $zmax), in void only"
xbps xbps-install -y "${mpkgs[@]}"
mkver=$(kvers | grep -vxF "$kver" | sort -V | awk 'END { print }')
[[ -n $mkver && $(kvers | wc -l) -eq 2 ]] || die "${mpkgs[0]} brought no second kernel: $(kvers | tr '\n' ' ')"
[[ $(printf '%s\n' "$kver" "$mkver" | sort -V | awk 'END { print }') == "$mkver" ]] || die "$mkver isn't newer than $kver"
has_zfs_ko "$mkver" || die "DKMS built no zfs module for $mkver"
check_initramfs "$mkver"
check_initramfs "$kver"
say "newer kernel: $mkpkg ($mkver)"
caps_ok || die "/usr/bin/iputils-ping lost its file capabilities"
resolv off
sync

# --- NVRAM -----------------------------------------------------------------------------
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
say "NVRAM: stale entries for the old partitions (${old_partuuids:-none}), and earlier ZFSBootMenu ones"
while IFS=$'\t' read -r n lbl rest; do
    low=${rest,,}
    del=no
    if [[ $lbl == "ZFSBootMenu" || $lbl == "ZFSBootMenu (Backup)" ]]; then del=yes; fi
    for u in $old_partuuids; do [[ $low == *"$u"* ]] && del=yes; done
    if [[ $del == yes ]]; then
        say "deleting Boot$n ($lbl)"
        efibootmgr -q -b "$n" -B || die "can't delete Boot$n"
    fi
done < <(entries)
part=$(cat "/sys/class/block/${ESP_DEV##*/}/partition")
efibootmgr -q -c -d "$DEV" -p "$part" -L "ZFSBootMenu (Backup)" -l '\EFI\ZBM\VMLINUZ-BACKUP.EFI' || die "efibootmgr (Backup) failed"
efibootmgr -q -c -d "$DEV" -p "$part" -L "ZFSBootMenu" -l '\EFI\ZBM\VMLINUZ.EFI' || die "efibootmgr failed"
list=$(entries) || die "efibootmgr -v failed"
main=$(awk -F'\t' '$2 == "ZFSBootMenu" && !m { m = $1 } END { print m }' <<<"$list")
backup=$(awk -F'\t' '$2 == "ZFSBootMenu (Backup)" && !m { m = $1 } END { print m }' <<<"$list")
[[ -n $main && -n $backup ]] || die "the firmware didn't keep the ZFSBootMenu entries"
order=$(efibootmgr | awk '/^BootOrder:/ { print $2 }')
rest=$(tr ',' '\n' <<<"$order" | awk -v a="$main" -v b="$backup" 'NF && toupper($1) != toupper(a) && toupper($1) != toupper(b)' | paste -sd ',' -)
efibootmgr -q -o "$main,$backup${rest:+,$rest}" || die "can't set BootOrder"
ev=$(efibootmgr -v) || die "efibootmgr -v failed"
grep -E "^(BootCurrent|BootNext|BootOrder|Boot$main|Boot$backup)" <<<"$ev" || true
esp_guid=$(lsblk -no PARTUUID "$ESP_DEV" | tr 'A-F' 'a-f')
mline=$(grep -E "^Boot$main" <<<"$ev" | tr 'A-F' 'a-f' || true)
[[ -n $esp_guid && $mline == *"$esp_guid"* ]] || die "Boot$main doesn't name the ESP ($esp_guid)"
[[ $(awk '/^BootOrder:/ { print $2 }' <<<"$ev") == "$main,$backup"* ]] || die "BootOrder doesn't start $main,$backup"
echo "ZBM_ENTRY=$main" >"$STATE/zbm-entry"

# --- Done ------------------------------------------------------------------------------
say "summary"
zpool get -H -o property,value bootfs,compatibility,ashift,autotrim,cachefile "$POOL"
zfs list -t all -o name,used,mountpoint,canmount,origin -r "$POOL"
zfs get -H -o name,property,value -r -t filesystem encryptionroot,keylocation,keyformat,keystatus "$POOL" | awk -F'\t' '$1 == "'"$POOL"'" || $2 != "keylocation"'
zfs get -H -o name,property,value -s local org.zfsbootmenu:commandline,org.zfsbootmenu:keysource -r "$POOL"
ls -l "$ROOT/boot" "$ROOT/boot/efi/EFI/ZBM" "$ROOT/boot/efi/EFI/BOOT"
ls -l "$ROOT/etc/zfs/zroot.key"
# shellcheck disable=SC2016  # expanded in the chroot's sh
in_root sh -c 'for p in zfs linux linux-headers dracut base-system xfce4 lightdm NetworkManager openssh avahi; do xbps-query -p pkgver $p; done' || true
unmount_all
zpool export "$POOL" || die "zpool export $POOL failed"
say "Void is on $DEV; ZFSBootMenu is Boot$main (first in BootOrder); the pool is exported"
echo "BKO-VOID-ZBM-INSTALL-OK"
