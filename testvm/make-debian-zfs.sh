#!/usr/bin/env bash
# Build a Debian test guest for BackOtter with its root on ZFS, from the
# host: Debian 13 (trixie) laid out as OpenZFS's "Debian Trixie Root on ZFS"
# guide does it (unencrypted, with /boot on ext4 instead of a boot pool), and
# its pool made by the host's own OpenZFS 2.3, the same as trixie's
# (contrib's zfs-dkms and zfsutils-linux), so that the hosts can import, read
# and write it with every feature it has, and compare.py can recreate it
# exactly.  (ubuntu26-zfsmirror's OpenZFS 2.4 pool has features they lack.)
#
# Usage: sudo ./make-debian-zfs.sh WORK_DIR NAME
#   e.g. sudo ./make-debian-zfs.sh vms/debian13-zfs debian13-zfs
#
# Why from the host: Debian's installer has no ZFS root, and a live system
# would bring its own OpenZFS.  So this does what the guide does from a live
# system, with the host as that system: partition, zpool create, debootstrap,
# then the kernel, zfs-dkms, zfs-initramfs and GRUB from the chroot.  The
# disk is a sparse raw file on a loop device, converted to qcow2 at the end,
# as make-from-live.sh does (qemu-nbd broke down there under load, "Double
# reply ... Dead connection"); a loop device also needs no nbd lock.  The
# pool is created under a temporary in-core name (-t), so it can't clash with
# a host's own pool, while its on-disk name is "rpool", and exported at the
# end.
#
# Trust: debootstrap checks trixie's Release file against the host's
# debian-archive-keyring (--keyring, --force-check-sig: no unsigned
# fallback), and every package against the checksums that file signs; apt in
# the chroot then checks trixie, trixie-updates and trixie-security against
# the guest's own debian-archive-keyring, which came that way.  Nothing else
# is downloaded.  The packages are the archive's of the day it's built.
#
# The guest, host name NAME: BIOS + MBR; /boot (1 GiB ext4), swap (2 GiB),
# and the ZFS pool rpool (the rest), with the guide's pool properties and
# datasets: rpool/ROOT/debian as / (canmount=noauto), rpool/home,
# rpool/home/root, rpool/var/log, rpool/var/spool, and of the guide's optional
# ones rpool/var/cache (com.sun:auto-snapshot=false), rpool/var/mail,
# rpool/srv and rpool/usr/local (rpool/var, rpool/var/lib and rpool/usr are
# canmount=off containers).  /etc/hostid made by zgenhostid; the kernel's
# command line names root=ZFS=rpool/ROOT/debian.  systemd-networkd (DHCP,
# IPv4) and systemd-resolved, sshd, root logs in with a key, python3
# (zfs-guest-rt.sh runs fs_roundtrip.py in the guest), a serial
# console (GRUB's too); the ARC is capped at 512 MiB for 2 GB of RAM and
# zfs-dkms's builds at kernel updates.
#
# The pool's cache file is the guest's own: the build boots the guest once,
# and a one-shot unit there sets cachefile=/etc/zfs/zpool.cache on rpool (as
# the guest names its vdev, /dev/vda3), rebuilds the initramfs with it, removes
# itself and powers off.  At boot the initramfs imports rpool by name (the
# cache file only if that fails), so a copy restored onto a pool made anew,
# with another GUID, still imports; but zfs-import-cache.service then
# finds the old GUID in the restored /etc/zfs/zpool.cache, unless the
# restore writes the new pool's there.
#
# Results, in WORK_DIR: NAME.qcow2 (16 GiB, sparse, snapshot
# "fresh-install"), root-password, id_ed25519 and .pub, firstboot-serial.log.
#
# Needs: root; OpenZFS (zpool, zfs) with the same features as trixie's,
# debootstrap, debian-archive-keyring, qemu-img, qemu-system-x86_64 with KVM,
# losetup, sfdisk, mkfs.ext4, mkswap, openssl, ssh-keygen; network access.

set -euo pipefail

DISK_SIZE=16G
BOOT_SIZE=1GiB
SWAP_SIZE=2GiB
MEMORY=2048
SUITE=trixie
MIRROR=http://deb.debian.org/debian
SECURITY=http://security.debian.org/debian-security
KEYRING=/usr/share/keyrings/debian-archive-keyring.gpg
POOL=rpool
FIRST_BOOT_TIMEOUT=1200

die() { echo "make-debian-zfs: $*" >&2; exit 1; }

[[ $# -eq 2 ]] || die "usage: $0 WORK_DIR NAME"
name=$2
[[ $name =~ ^[a-z][a-z0-9-]*$ ]] || die "NAME is a host name: lower-case letters, digits and -"
[[ $EUID -eq 0 ]] || die "must run as root"
for t in zpool zfs debootstrap qemu-img qemu-system-x86_64 losetup sfdisk mkfs.ext4 mkswap openssl ssh-keygen; do
    command -v "$t" >/dev/null || die "needs $t"
done
[[ -s $KEYRING ]] || die "no $KEYRING (debian-archive-keyring)"
mkdir -p "$1"
cd "$1"
work_dir=$(pwd)
disk=$work_dir/$name.qcow2
[[ -e $disk ]] && die "$disk already exists; move it away to rebuild"
owner=${SUDO_USER:-root}

# --- Disk and pool ------------------------------------------------------------------
temp=bkodz$$                      # the pool's name on this host while it's built
root=/mnt/bko-debian-zfs-$$       # its altroot: the new system
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
label: dos
size=$BOOT_SIZE, type=83, bootable
size=$SWAP_SIZE, type=82
type=bf
EOF
udevadm settle
for _ in 1 2 3 4 5; do [[ -e ${dev}p3 ]] && break; sleep 1; done
mkfs.ext4 -q -F "${dev}p1"
mkswap -q "${dev}p2"
mkdir -p "$root"
# The guide's pool: every feature this OpenZFS has (the same as trixie's).
zpool create -t "$temp" -o ashift=12 -o autotrim=on -o cachefile=none \
    -O acltype=posixacl -O xattr=sa -O dnodesize=auto -O compression=lz4 \
    -O normalization=formD -O relatime=on -O canmount=off -O mountpoint=/ \
    -R "$root" "$POOL" "${dev}p3"
imported=yes
zfs create -o canmount=off -o mountpoint=none "$temp/ROOT"
zfs create -o canmount=noauto -o mountpoint=/ "$temp/ROOT/debian"
zfs mount "$temp/ROOT/debian"
zfs create "$temp/home"
zfs create -o mountpoint=/root "$temp/home/root"
chmod 700 "$root/root"
zfs create -o canmount=off "$temp/var"
zfs create -o canmount=off "$temp/var/lib"
zfs create "$temp/var/log"
zfs create "$temp/var/spool"
zfs create -o com.sun:auto-snapshot=false "$temp/var/cache"
zfs create "$temp/var/mail"
zfs create "$temp/srv"
zfs create -o canmount=off "$temp/usr"
zfs create "$temp/usr/local"
mkdir "$root/boot"
mount -t ext4 "${dev}p1" "$root/boot"

# --- Debian -------------------------------------------------------------------------
echo "debootstrap $SUITE from $MIRROR"
debootstrap --force-check-sig --keyring="$KEYRING" --components=main,contrib \
    "$SUITE" "$root" "$MIRROR" >"$work_dir/debootstrap.log" 2>&1 ||
    die "debootstrap failed; see $work_dir/debootstrap.log"
rm -f "$root/etc/apt/sources.list"
cat >"$root/etc/apt/sources.list.d/debian.sources" <<EOF
Types: deb
URIs: $MIRROR
Suites: $SUITE $SUITE-updates
Components: main contrib non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: $SECURITY
Suites: $SUITE-security
Components: main contrib non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF

for d in dev dev/pts proc sys; do mount --bind "/$d" "$root/$d"; done
mount -t tmpfs -o mode=755 tmpfs "$root/run"
cp -L /etc/resolv.conf "$root/etc/resolv.conf"
printf '#!/bin/sh\nexit 101\n' >"$root/usr/sbin/policy-rc.d"
chmod 755 "$root/usr/sbin/policy-rc.d"
in_root() { chroot "$root" env DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8 "$@"; }

echo "$name" >"$root/etc/hostname"
printf '127.0.0.1\tlocalhost\n127.0.1.1\t%s\n::1\tlocalhost ip6-localhost ip6-loopback\n' "$name" >"$root/etc/hosts"
uuid() { blkid -s UUID -o value "$1"; }
{
    echo "# Written by BackOtter's make-debian-zfs.sh.  / and the rest are ZFS"
    echo "# datasets in rpool, which mount themselves."
    echo "UUID=$(uuid "${dev}p1") /boot ext4 defaults 0 2"
    echo "UUID=$(uuid "${dev}p2") none swap sw 0 0"
} >"$root/etc/fstab"
# zfs-dkms's build at kernel updates needs room too, in 2 GB.
echo "options zfs zfs_arc_max=536870912" >"$root/etc/modprobe.d/zfs.conf"

echo "Installing the kernel, OpenZFS (zfs-dkms builds its module), GRUB, sshd"
in_root apt-get -q update >/dev/null
# GRUB goes onto the loop device below; the guest's own disk afterwards.
echo "grub-pc grub-pc/install_devices multiselect" | in_root debconf-set-selections
# zstd: initramfs-tools compresses with it, and lsinitramfs reads it with it.
in_root apt-get -y -q install zstd linux-image-amd64 linux-headers-amd64 >/dev/null
in_root apt-get -y -q install zfs-dkms zfsutils-linux zfs-zed zfs-initramfs \
    grub-pc openssh-server dbus python3 os-prober- >"$work_dir/apt-zfs.log" 2>&1 ||
    die "installing OpenZFS and GRUB failed; see $work_dir/apt-zfs.log"
kver=$(ls "$root/lib/modules" | sort -V | tail -n 1)
compgen -G "$root/lib/modules/$kver/updates/dkms/zfs.ko*" >/dev/null ||
    die "zfs-dkms built no module for $kver; see $work_dir/apt-zfs.log"
echo "OpenZFS in the guest: $(in_root dpkg-query -W -f '${Version}' zfsutils-linux), module for $kver"

# Network: DHCP (IPv4: the hosts have no IPv6 route out) on any wired card.
cat >"$root/etc/systemd/network/20-wired.network" <<'EOF'
[Match]
Type=ether

[Network]
DHCP=ipv4
EOF
in_root systemctl enable systemd-networkd >/dev/null 2>&1

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
[[ -e $root/etc/hostid ]] || in_root zgenhostid

# The first boot (in the build, below) writes the pool's cache file.
cat >"$root/etc/bko-first-boot" <<EOF
#!/bin/sh
# make-debian-zfs.sh's first boot: rpool's cache file, as this system names
# its vdev, into /etc and the initramfs; then this goes, and the guest powers off.
set -e
echo "bko-first-boot: start"
zpool set cachefile=/etc/zfs/zpool.cache $POOL
i=0
while [ ! -s /etc/zfs/zpool.cache ] && [ \$i -lt 30 ]; do sleep 1; i=\$((i + 1)); done
zdb -C -U /etc/zfs/zpool.cache | grep -E "^ *(name|pool_guid|hostid|hostname|path):"
update-initramfs -u -k all
lsinitramfs /boot/initrd.img-\$(uname -r) | grep -q 'etc/zfs/zpool.cache\$'
rm -f /etc/systemd/system/multi-user.target.wants/bko-first-boot.service \\
    /etc/systemd/system/bko-first-boot.service /etc/bko-first-boot
sync
echo "bko-first-boot: done"
systemctl --no-block poweroff
EOF
chmod 755 "$root/etc/bko-first-boot"
cat >"$root/etc/systemd/system/bko-first-boot.service" <<'EOF'
[Unit]
Description=BackOtter build: the pool's cache file, then power off
After=zfs-import.target zfs-mount.service multi-user.target
# Powered off either way: the build looks for "done" on the console.
FailureAction=poweroff

[Service]
Type=oneshot
ExecStart=/etc/bko-first-boot
StandardOutput=journal+console
StandardError=journal+console

[Install]
WantedBy=multi-user.target
EOF
in_root systemctl enable bko-first-boot.service >/dev/null 2>&1

# The bootloader, with a serial console.
sed -i -e 's|^GRUB_CMDLINE_LINUX_DEFAULT=.*|GRUB_CMDLINE_LINUX_DEFAULT="console=tty0 console=ttyS0,115200n8"|' \
    -e "s|^GRUB_CMDLINE_LINUX=.*|GRUB_CMDLINE_LINUX=\"root=ZFS=$POOL/ROOT/debian\"|" "$root/etc/default/grub"
printf '%s\n' 'GRUB_TERMINAL="console serial"' 'GRUB_SERIAL_COMMAND="serial --unit=0 --speed=115200"' \
    >>"$root/etc/default/grub"
in_root update-initramfs -u -k all >/dev/null 2>&1 || in_root update-initramfs -c -k all >/dev/null
in_root lsinitramfs "/boot/initrd.img-$kver" | grep -q '/zfs\.ko' || die "the initramfs has no zfs module"
in_root grub-install --target=i386-pc "$dev"
in_root update-grub
grep -q "root=ZFS=$POOL/ROOT/debian" "$root/boot/grub/grub.cfg" || die "grub.cfg doesn't boot root=ZFS=$POOL/ROOT/debian"
# Later grub-pc upgrades, in the guest, install to its disk.
echo "grub-pc grub-pc/install_devices multiselect /dev/vda" | in_root debconf-set-selections

# systemd-resolved last: it makes /etc/resolv.conf its stub, which the
# chroot can't use.
in_root apt-get -y -q install systemd-resolved >/dev/null
ln -sfn ../run/systemd/resolve/stub-resolv.conf "$root/etc/resolv.conf"
rm -f "$root/usr/sbin/policy-rc.d"
in_root apt-get clean
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
timeout "$FIRST_BOOT_TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 2 -m "$MEMORY" -machine pc \
    -drive "file=$disk,if=virtio,format=qcow2" \
    -nic user,ipv6=off,model=virtio-net-pci \
    -display none -serial file:firstboot-serial.log -monitor none -no-reboot ||
    die "the first boot failed or timed out; see firstboot-serial.log"
grep -q 'bko-first-boot: done' firstboot-serial.log || die "the first boot didn't finish; see firstboot-serial.log"

qemu-img snapshot -c fresh-install "$disk"
chown "$owner": "$disk" root-password id_ed25519 id_ed25519.pub debootstrap.log apt-zfs.log firstboot-serial.log
echo "Done: $disk ($(du -h "$disk" | cut -f1) on disk), snapshot \"fresh-install\""
