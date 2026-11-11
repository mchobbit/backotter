#!/usr/bin/env bash
# Build a Chimera Linux test guest for BackOtter with its root on ZFS, from
# the host: UEFI, GRUB (grub-x86_64-efi), a separate ext4 /boot, and the pool
# laid out as Chimera's "Root on ZFS" page does it, with the prebuilt modules
# of linux-lts-zfs-bin.  The pool is made by the host's own OpenZFS (2.3 on
# the hosts), so that the hosts and the rescue image can import, read and
# write it with every feature it has, while the guest runs Chimera's newer
# OpenZFS on it (2.4: it imports an older pool, and never upgrades it by
# itself).  ubuntu26-zfsmirror's pool, made by its own OpenZFS 2.4, has
# features the hosts lack.
#
# Usage: sudo ./make-chimera-zfs.sh WORK_DIR [NAME]
#   e.g. sudo ./make-chimera-zfs.sh vms/chimera-zfs        (NAME chimera-zfs)
#
# Why from the host: the Arch ISO's live system that make-bootstrap.sh uses
# has no ZFS, and Chimera's own live ISO would make the pool with its newer
# OpenZFS.  So this does what Chimera's guide does from a live system, with
# the host as that system, as make-debian-zfs.sh does for Debian: partition,
# zpool create, unpack the "full" ROOTFS tarball, then the kernel, ZFS, GRUB
# and sshd with Chimera's apk in a chroot (its apk and every tool it runs
# are Chimera's own, musl, inside the chroot; the host is only the kernel).
# The disk is a sparse raw file on a loop device, converted to qcow2 at the
# end.  The pool is created under a temporary in-core name (-t), so it can't
# clash with a host's own pool, while its on-disk name is "rpool"; it is
# exported at the end, so the guest's first import needs no -f, and writes
# the guest's own host ID into it.  The build runs no zpool or zfs command
# in the chroot (Chimera's newer userland against the host's module); only
# GRUB's tools probe the disks there, and the grub.cfg they write names the
# build's pool, so the first boot writes it again (below).  The chroot has a
# /dev of its own (below): Chimera's tmpfiles, which apk's triggers apply,
# would regroup the host's /dev/kvm and others by Chimera's group IDs.
#
# Trust: Chimera's sha256sums.txt is checked against the release batch's
# minisign key, pinned below (the same as make-bootstrap.sh's CHIMERA_KEY:
# main/chimera-image-keys/files/20261103.pub in chimera-linux/cports), and
# the tarball against it; apk in the chroot then checks every package's
# signature against the keys in the tarball's own /usr/lib/apk/keys.  The
# tarball is fixed by its date (and taken from WORK_DIR when already there,
# e.g. copied from vms/chimera, and checked the same), but apk upgrades it
# from the live repository, so a rebuild gets newer packages.  Nothing else
# is downloaded.
#
# The guest, host name NAME: UEFI, GPT: an ESP (512 MiB FAT32) at /boot/efi,
# /boot (1 GiB ext4), swap (2 GiB), and the ZFS pool rpool (the rest) with
# the guide's properties (ashift=12, acltype=posixacl, canmount=off,
# dnodesize=auto, relatime=on, xattr=sa, mountpoint=/; compression is
# OpenZFS's default, on) and datasets:
# rpool/ROOT (canmount=off, mountpoint=none), rpool/ROOT/chimera as /
# (canmount=noauto).  linux-lts with linux-lts-zfs-bin's modules, zfs (the
# userland), initramfs-tools with its zfs hook; /etc/hostid by zgenhostid;
# the kernel's command line names root=ZFS=rpool/ROOT/chimera.  GRUB in the
# ESP under its own id (chimera) and at the fallback path
# EFI/BOOT/BOOTX64.EFI, written with --no-nvram: OVMF boots the fallback
# with no boot entries, as vm-run --uefi's fresh variable store has none.
# dinit: sshd and dhcpcd enabled; root logs in with a key; a serial console
# (GRUB's too); the ARC capped at 512 MiB for 2 GB of RAM.
#
# The pool's cache file is the guest's own: the build boots the guest once
# (vm-run --uefi --no-secure-boot), and a one-shot dinit service there sets
# cachefile=/etc/zfs/zpool.cache on rpool (as the guest names its vdev,
# /dev/vda4), rebuilds the initramfs with it and grub.cfg (update-grub names
# the root by the dataset mounted at /, which only the guest sees as
# rpool's), removes itself and powers off, as make-debian-zfs.sh's does.
# Chimera's guide says nothing of the cache file; its early-fs-zfs imports the
# cache file's pools if there is one, all pools it finds if not.  At boot the
# initramfs imports rpool by name (the cache file only if that fails).
#
# Results, in WORK_DIR: NAME.qcow2 (16 GiB, sparse, snapshot
# "fresh-install"), root-password, id_ed25519 and .pub, vm-run-options
# (--uefi --no-secure-boot), install.log (apk's and the tools' output) and
# firstboot-serial.log.
#
# Needs: root; OpenZFS (zpool, zfs); bsdtar (it keeps the tarball's file
# capabilities, which GNU tar drops), minisign, wget, qemu-img, KVM and OVMF
# (vm-run --uefi), losetup, sfdisk, mkfs.fat, mkfs.ext4, mkswap, openssl,
# ssh-keygen, python3 (to pick a free port); network access.  SSH_PORT
# picks the first boot's forwarded port (vm-run forwards one even though
# nothing here uses it); unset, a free port is picked, so the first boot
# never clashes with a by-hand vm-run on 2222 or another rebuild slot.
# sudo drops SSH_PORT from the environment: pass it as sudo SSH_PORT=N.

set -euo pipefail

DISK_SIZE=16G
ESP_SIZE=512MiB
BOOT_SIZE=1GiB
SWAP_SIZE=2GiB
POOL=rpool
FIRST_BOOT_TIMEOUT=1200
CHIMERA_DATE=20261103
# The key for that release batch: main/chimera-image-keys/files/20261103.pub
# in chimera-linux/cports (key ID B7CC5D3A17688906).  Keep it the same as
# make-bootstrap.sh's.
CHIMERA_KEY=RWQGiWgXOl3Mt/2GUL9HpuxyHyWBc29/lZ9wW6XZz29lx2YLwkKfXk52
ZFS_PART_TYPE=6A898CC3-1DD2-11B2-99A6-080020C9B98D    # OpenZFS's (sgdisk's BF00)

here=$(dirname "$(realpath "$0")")
die() { echo "make-chimera-zfs: $*" >&2; exit 1; }

[[ $# -eq 1 || $# -eq 2 ]] || die "usage: $0 WORK_DIR [NAME]"
name=${2:-chimera-zfs}
[[ $name =~ ^[a-z][a-z0-9-]*$ ]] || die "NAME is a host name: lower-case letters, digits and -"
[[ $EUID -eq 0 ]] || die "must run as root"
for t in zpool zfs bsdtar minisign wget qemu-img qemu-system-x86_64 losetup sfdisk mkfs.fat mkfs.ext4 mkswap blkid openssl ssh-keygen python3; do
    command -v "$t" >/dev/null || die "needs $t"
done
[[ -f /usr/share/OVMF/OVMF_CODE_4M.fd ]] || die "needs OVMF (/usr/share/OVMF/OVMF_CODE_4M.fd)"
mkdir -p "$1"
cd "$1"
work_dir=$(pwd)
disk=$work_dir/$name.qcow2
[[ -e $disk ]] && die "$disk already exists; move it away to rebuild"
owner=${SUDO_USER:-root}
log=$work_dir/install.log
: >"$log"

# --- Fetch and verify the ROOTFS tarball ----------------------------------------------
# fetch URL FILE: download into WORK_DIR, unless it's already there.
fetch() {
    [[ -f $2 && ! -f $2.part ]] && return
    echo "Downloading $1"
    touch "$2.part"
    wget -c -q -O "$2" "$1" || die "could not download $1"
    rm "$2.part"
}
tarball=chimera-linux-x86_64-ROOTFS-$CHIMERA_DATE-full.tar.gz
sums=chimera-sha256sums-$CHIMERA_DATE.txt
url=https://repo.chimera-linux.org/live/$CHIMERA_DATE
fetch "$url/sha256sums.txt" "$sums"
fetch "$url/sha256sums.txt.minisig" "$sums.minisig"
fetch "$url/$tarball" "$tarball"
minisign -q -V -P "$CHIMERA_KEY" -m "$sums" -x "$sums.minisig" ||
    die "Chimera's sha256sums.txt is not signed by its release key"
grep -F " $tarball" "$sums" | sha256sum -c --quiet - || die "$tarball does not match Chimera's sha256sums.txt"

# --- Disk and pool --------------------------------------------------------------------
temp=bkocz$$                      # the pool's name on this host while it's built
root=/mnt/bko-chimera-zfs-$$      # its altroot: the new system
raw=$work_dir/.$name.raw
dev="" imported=no
unmount_all() { ! mountpoint -q "$root" || umount -R "$root" || umount -R -l "$root"; }
cleanup() {
    set +e
    unmount_all
    if [[ $imported == yes ]]; then zpool export "$temp" || zpool export -f "$temp"; fi
    [[ -n $dev ]] && losetup -d "$dev"
    rm -f "$raw"
    rmdir "$root" 2>/dev/null
}
trap cleanup EXIT

truncate -s "$DISK_SIZE" "$raw"
dev=$(losetup --find --show --partscan "$raw")
sfdisk -q "$dev" <<EOF
label: gpt
size=$ESP_SIZE, type=uefi
size=$BOOT_SIZE, type=linux
size=$SWAP_SIZE, type=swap
type=$ZFS_PART_TYPE
EOF
udevadm settle
for _ in 1 2 3 4 5; do [[ -e ${dev}p4 ]] && break; sleep 1; done
mkfs.fat -F 32 -n EFI "${dev}p1" >/dev/null
mkfs.ext4 -q -F "${dev}p2"
mkswap -q "${dev}p3"
mkdir -p "$root"
# Chimera's guide's pool and datasets; every feature this OpenZFS has.
zpool create -t "$temp" -o ashift=12 -o cachefile=none \
    -O acltype=posixacl -O canmount=off -O dnodesize=auto -O relatime=on \
    -O xattr=sa -O mountpoint=/ -R "$root" "$POOL" "${dev}p4"
imported=yes
zfs create -o canmount=off -o mountpoint=none "$temp/ROOT"
zfs create -o canmount=noauto -o mountpoint=/ "$temp/ROOT/chimera"
zfs mount "$temp/ROOT/chimera"
mkdir "$root/boot"
mount -t ext4 "${dev}p2" "$root/boot"
mkdir "$root/boot/efi"
mount -t vfat "${dev}p1" "$root/boot/efi"

# --- Chimera --------------------------------------------------------------------------
echo "Unpacking $tarball"
# --numeric-owner: Chimera's IDs, not the host's for the same names.
bsdtar -xpf "$tarball" --numeric-owner -C "$root"
grep -q '^ID="\?chimera"\?$' "$root/etc/os-release" || die "/etc/os-release doesn't say Chimera"
# The chroot gets a /dev of its own, never a bind of the host's: apk's
# triggers there apply Chimera's tmpfiles (dinit-chimera's
# static-nodes-permissions.conf), which give /dev/kvm, /dev/vhost-*,
# /dev/loop-control and /dev/snd/* Chimera's group IDs (kvm 11, disk 7),
# and on the host's own nodes that takes KVM from the hosts' users.  So a
# tmpfs with copies of the nodes the chroot needs: the usual ones, the loop
# device and its partitions (GRUB's tools), and /dev/zfs (grub-probe runs
# `zpool status` for /).  /sys and /proc/sys are read-only there.
mount -t tmpfs -o mode=755,nosuid tmpfs "$root/dev"
# copy_node NODE: the same device at the same path in the chroot's /dev.
copy_node() {
    local type=c
    [[ -b $1 ]] && type=b
    mknod -m "$(stat -c %a "$1")" "$root$1" "$type" "$((16#$(stat -c %t "$1")))" "$((16#$(stat -c %T "$1")))"
}
for n in /dev/null /dev/zero /dev/full /dev/random /dev/urandom /dev/tty /dev/zfs "$dev" "$dev"p[1-4]; do
    copy_node "$n"
done
mkdir "$root/dev/pts" "$root/dev/shm"
mount -t devpts -o newinstance,ptmxmode=0666,mode=620 devpts "$root/dev/pts"
ln -s pts/ptmx "$root/dev/ptmx"
ln -s /proc/self/fd "$root/dev/fd"
for i in 0 1 2; do ln -s "/proc/self/fd/$i" "$root/dev/$(echo stdin stdout stderr | cut -d' ' -f$((i + 1)))"; done
mount -t proc proc "$root/proc"
mount --bind "$root/proc/sys" "$root/proc/sys"
mount -o remount,bind,ro "$root/proc/sys"
mount --bind /sys "$root/sys"
mount -o remount,bind,ro "$root/sys"
mount -t tmpfs -o mode=755 tmpfs "$root/run"
rm -f "$root/etc/resolv.conf"
cp -L /etc/resolv.conf "$root/etc/resolv.conf"
# Chimera's tools are in /usr/bin, and its /bin/sh is FreeBSD's sh.
in_root() { chroot "$root" /usr/bin/env PATH=/usr/bin "$@"; }
chimera_apk() { in_root apk --no-interactive --no-progress "$@" >>"$log" 2>&1 || die "apk $* failed; see $log"; }

# zfs.conf before the kernel's initramfs: the ARC capped for 2 GB of RAM.
mkdir -p "$root/etc/modprobe.d"
echo "options zfs zfs_arc_max=536870912" >"$root/etc/modprobe.d/zfs.conf"
echo "apk: upgrading, then the kernel, OpenZFS, GRUB, sshd"
chimera_apk update
chimera_apk upgrade --available
chimera_apk add linux-lts linux-lts-zfs-bin zfs grub-x86_64-efi efibootmgr dosfstools e2fsprogs openssh
kver=$(ls "$root/usr/lib/modules" | sort -V | tail -n 1)
[[ -n $kver ]] || die "no kernel in /usr/lib/modules"
compgen -G "$root/usr/lib/modules/$kver/*/zfs.ko*" >/dev/null ||
    compgen -G "$root/usr/lib/modules/$kver/*/*/zfs.ko*" >/dev/null ||
    [[ -n $(find "$root/usr/lib/modules/$kver" -name 'zfs.ko*' -print -quit) ]] ||
    die "linux-lts-zfs-bin brought no zfs module for $kver"
in_root apk info -e ckms >/dev/null 2>&1 && echo "note: ckms is installed" || echo "ckms: not installed"
echo "Kernel $kver; OpenZFS in the guest: $(in_root apk info -d zfs 2>/dev/null | head -n 1)"

echo "$name" >"$root/etc/hostname"
printf '127.0.0.1\tlocalhost\n::1\t\tlocalhost\n127.0.1.1\t%s\n' "$name" >"$root/etc/hosts"
ln -sf /usr/share/zoneinfo/UTC "$root/etc/localtime"
uuid() { blkid -s UUID -o value "$1"; }
{
    echo "# Written by BackOtter's make-chimera-zfs.sh.  / is the ZFS dataset"
    echo "# $POOL/ROOT/chimera, which the initramfs mounts."
    echo "UUID=$(uuid "${dev}p2") /boot ext4 defaults 0 2"
    echo "UUID=$(uuid "${dev}p1") /boot/efi vfat defaults 0 2"
    echo "UUID=$(uuid "${dev}p3") none swap sw 0 0"
} >"$root/etc/fstab"

# Identity: root's password and SSH key, the host ID ZFS records in the pool.
umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$name test guest" -f id_ed25519
umask 022
printf 'root:%s\n' "$hash" | in_root chpasswd -e
install -d -m 700 "$root/root/.ssh"
install -m 600 id_ed25519.pub "$root/root/.ssh/authorized_keys"
rm -f "$root/etc/hostid"
in_root zgenhostid
[[ -s $root/etc/hostid ]] || die "zgenhostid wrote no /etc/hostid"
echo "--uefi --no-secure-boot" >vm-run-options

# dinit: a service starts at boot when /etc/dinit.d/boot.d has a link of its
# name (what `dinitctl enable` makes).
for s in sshd dhcpcd; do
    [[ -e $root/usr/lib/dinit.d/$s ]] || die "no dinit service $s"
    ln -sfn "/usr/lib/dinit.d/$s" "$root/etc/dinit.d/boot.d/$s"
done

# The first boot (in the build, below) writes the pool's cache file.
cat >"$root/etc/bko-first-boot" <<EOF
#!/bin/sh
# make-chimera-zfs.sh's first boot: $POOL's cache file, as this system names
# its vdev, into /etc and the initramfs; then this goes, and the guest powers
# off, after grub.cfg is made anew here (the build's names the build's
# pool and loop device).  Powered off either way: the build looks for "done"
# on the console.
exec >/dev/console 2>&1
# poweroff in the background: dinit's poweroff waits for the shutdown, which
# waits for this service's start to finish.
off() { sync; poweroff </dev/null >/dev/null 2>&1 & }
fail() { echo "bko-first-boot: failed: \$*"; off; exit 1; }
echo "bko-first-boot: start"
zpool set cachefile=/etc/zfs/zpool.cache $POOL || fail "zpool set cachefile"
i=0
while [ ! -s /etc/zfs/zpool.cache ] && [ \$i -lt 30 ]; do sleep 1; i=\$((i + 1)); done
[ -s /etc/zfs/zpool.cache ] || fail "no /etc/zfs/zpool.cache"
zdb -C -U /etc/zfs/zpool.cache | grep -E "^ *(name|pool_guid|hostid|hostname|path):"
# Not hostid(1): musl's gethostid() is always 0.  ZFS reads /etc/hostid.
echo "bko-first-boot: /etc/hostid \$(od -An -tu4 /etc/hostid | tr -d ' ')"
update-initramfs -u -k all || fail "update-initramfs"
lsinitramfs /boot/initrd.img-\$(uname -r) >/tmp/bko-initrd.list || fail "lsinitramfs"
grep -q 'etc/zfs/zpool.cache\$' /tmp/bko-initrd.list || fail "the initramfs has no zpool.cache"
update-grub || fail "update-grub"
grep -q "root=ZFS=$POOL/ROOT/chimera " /boot/grub/grub.cfg || fail "grub.cfg doesn't boot root=ZFS=$POOL/ROOT/chimera"
! grep -qE 'bkocz|/dev/loop' /boot/grub/grub.cfg || fail "grub.cfg still names the build's pool or loop device"
rm -f /etc/dinit.d/boot.d/bko-first-boot /etc/dinit.d/bko-first-boot /etc/bko-first-boot /tmp/bko-initrd.list
sync
echo "bko-first-boot: done"
off
EOF
chmod 755 "$root/etc/bko-first-boot"
cat >"$root/etc/dinit.d/bko-first-boot" <<'EOF'
# BackOtter build: the pool's cache file, then power off (make-chimera-zfs.sh)
type = scripted
command = /etc/bko-first-boot
depends-on: local.target
# update-initramfs takes longer than dinit's default 60 s.
start-timeout = 0
EOF
ln -sfn ../bko-first-boot "$root/etc/dinit.d/boot.d/bko-first-boot"

# The boot loader, with a serial console.
mkdir -p "$root/etc/default"
cat >>"$root/etc/default/grub" <<EOF
GRUB_CMDLINE_LINUX_DEFAULT="loglevel=4 console=tty0 console=ttyS0,115200n8"
GRUB_TIMEOUT=2
GRUB_DISABLE_OS_PROBER=true
GRUB_TERMINAL="console serial"
GRUB_SERIAL_COMMAND="serial --unit=0 --speed=115200"
EOF
in_root update-initramfs -u -k all >>"$log" 2>&1 || die "update-initramfs failed; see $log"
in_root lsinitramfs "/boot/initrd.img-$kver" >"$work_dir/.initrd.list" 2>>"$log" || die "lsinitramfs failed; see $log"
grep -q '/zfs\.ko' "$work_dir/.initrd.list" || die "the initramfs has no zfs module"
grep -q 'etc/hostid$' "$work_dir/.initrd.list" || die "the initramfs has no /etc/hostid"
grep -q 'scripts/zfs$' "$work_dir/.initrd.list" || die "the initramfs has no zfs boot script"
rm "$work_dir/.initrd.list"
# The build boots no UEFI: no EFI variables to write.  So GRUB under its own
# name, and again at the fallback path that the firmware boots with no boot
# entries, as vm-run --uefi's does.
in_root grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=chimera --no-nvram >>"$log" 2>&1 ||
    die "grub-install failed; see $log"
in_root grub-install --target=x86_64-efi --efi-directory=/boot/efi --removable >>"$log" 2>&1 ||
    die "grub-install --removable failed; see $log"
[[ -f $root/boot/efi/EFI/BOOT/BOOTX64.EFI && -f $root/boot/efi/EFI/chimera/grubx64.efi ]] ||
    die "GRUB isn't in the ESP"
# A grub.cfg for the first boot only: Chimera's 10_linux names the root
# by `findmnt -no source /`, which here is the build's in-core pool name
# ($temp/ROOT/chimera), and its menu entries by the loop device.  So that
# name becomes rpool's here, and the first boot makes grub.cfg anew in the
# guest, where it is rpool's own.
in_root update-grub >>"$log" 2>&1 || die "update-grub failed; see $log"
sed -i "s|root=ZFS=$temp/|root=ZFS=$POOL/|g" "$root/boot/grub/grub.cfg"
grep -q "root=ZFS=$POOL/ROOT/chimera " "$root/boot/grub/grub.cfg" ||
    die "grub.cfg doesn't boot root=ZFS=$POOL/ROOT/chimera"

rm "$root/etc/resolv.conf"
sync
unmount_all
zpool export "$temp"
imported=no
losetup -d "$dev"
dev=""
# Sparse in, sparse out: qcow2 stores only what was written.
qemu-img convert -f raw -O qcow2 "$raw" "$disk"
cleanup
trap - EXIT

# --- First boot -----------------------------------------------------------------------
echo "First boot: the pool's cache file; the console goes to $work_dir/firstboot-serial.log"
rm -f firstboot-serial.log
port=${SSH_PORT:-$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')}
SSH_PORT=$port timeout "$FIRST_BOOT_TIMEOUT" "$here/vm-run" --uefi --no-secure-boot --serial-log firstboot-serial.log "$disk" -- -no-reboot ||
    die "the first boot failed or timed out; see firstboot-serial.log"
grep -q 'bko-first-boot: done' firstboot-serial.log || die "the first boot didn't finish; see firstboot-serial.log"

qemu-img snapshot -c fresh-install "$disk"
chown "$owner": "$disk" root-password id_ed25519 id_ed25519.pub vm-run-options install.log firstboot-serial.log
echo "Done: $disk ($(du -h "$disk" | cut -f1) on disk), snapshot \"fresh-install\""
