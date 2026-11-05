#!/usr/bin/env bash
# Build a Debian 13 test guest for BackOtter on the storage stack an
# installer's "encrypted LVM on RAID" gives, from the host: UEFI with Secure
# Boot, two GPT disks, an EFI system partition on each, /boot on md RAID1,
# and LUKS2 on md RAID1 holding LVM with / (ext4), /srv (XFS) and swap.
# HoltTest1D's third source guest (testvm/holttest1d/README.md).
#
# Usage: sudo ./make-debian-stack.sh WORK_DIR NAME
#   e.g. sudo ./make-debian-stack.sh vms/debian13-stack debian13-stack
#
# Why from the host: Debian's installer can't preseed LUKS on a RAID array
# (partman-auto-raid makes no crypto), so this does what its manual
# partitioning would, the way make-debian-zfs.sh does: partition, mdadm,
# cryptsetup, LVM and mkfs on loop devices, debootstrap, then the kernel,
# shim, GRUB and the initramfs from the chroot.  The hosts run trixie, so
# every array, LUKS header, PV and file system is made by trixie's own tools
# with their defaults.  The disks are sparse raw files, converted to qcow2 at
# the end.  On this host the arrays and the LUKS volume have names of their
# own (bkos$$-...), so they clash with nothing; the guest's names are kept
# in their metadata and its configuration.  LVM runs with no devices file
# (--devicesfile ""), so this host's own LVM configuration is left alone,
# and the build stops if this host already has a volume group named NAME-vg.
#
# Trust: debootstrap checks trixie's Release file against the host's
# debian-archive-keyring (--keyring, --force-check-sig: no unsigned
# fallback), and every package against the checksums that file signs; apt in
# the chroot then checks trixie, trixie-updates and trixie-security against
# the guest's own debian-archive-keyring, which came that way.  The packages
# are the archive's of the day it's built.
#
# The guest, host name NAME, on two disks of DISK_SIZE (12 GiB) each, both
# GPT and laid out alike:
#   1  512 MiB  EFI system partition (FAT32); the first disk's is /boot/efi,
#               the second's /boot/efi2 (nofail), a copy made at the build
#   2    1 GiB  md RAID1 member: md0 (name NAME:0, metadata 1.2), /boot ext4
#   3  the rest md RAID1 member: md1 (name NAME:1), LUKS2 (trixie's defaults
#               but for Argon2id's memory, 256 MiB for a 2 GB guest), opened
#               as md1_crypt: the PV of volume group NAME-vg, with the
#               logical volumes root (7 GiB, ext4, /), srv (2 GiB, XFS, /srv)
#               and swap_1 (1 GiB), and some 400 MiB left free.
# The arrays are made --assume-clean: both members are all zeros then.
# Boot: shim-signed and grub-efi-amd64-signed, so Secure Boot with
# Microsoft's keys (vm-run --uefi); GRUB on EFI/debian and on the removable
# path EFI/BOOT/BOOTX64.EFI as well, which is what boots: vm-run gives every
# boot a fresh variable store, with no boot entries.  The initramfs asks for
# the LUKS passphrase on the serial console ("Please unlock disk
# md1_crypt"), where vm-update and compare.py type console-passphrase.
# systemd-networkd (DHCP, IPv4) and systemd-resolved, sshd, root logs in with
# a key, python3, a serial console (GRUB's too).
#
# Results, in WORK_DIR: NAME.qcow2 and NAME-b.qcow2 (sparse, each with a
# snapshot "fresh-install"), vm-run-options (--uefi --disk NAME-b.qcow2),
# console-passphrase (the LUKS passphrase, mode 600), root-password,
# id_ed25519 and .pub, firstboot-serial.log.  The first boot, here, checks
# that it all comes up under Secure Boot and rebuilds the initramfs in the
# guest itself.
#
# Needs: root; debootstrap, debian-archive-keyring, sfdisk, mdadm,
# cryptsetup, lvm2, mkfs.ext4, mkfs.xfs, mkfs.vfat, mkswap, qemu-img,
# qemu-system-x86_64 with KVM, OVMF (/usr/share/OVMF), python3, openssl,
# ssh-keygen, losetup; network access.

set -euo pipefail

DISK_SIZE=12G
ESP_SIZE=512MiB
BOOT_SIZE=1GiB
ROOT_SIZE=7G SRV_SIZE=2G SWAP_SIZE=1G
MEMORY=2048
SUITE=trixie
MIRROR=http://deb.debian.org/debian
SECURITY=http://security.debian.org/debian-security
KEYRING=/usr/share/keyrings/debian-archive-keyring.gpg
FIRST_BOOT_TIMEOUT=1200
ESP_TYPE=C12A7328-F81F-11D2-BA4B-00A0C93EC93B RAID_TYPE=A19D880F-05FC-4D3B-A006-743F0F84911E

here=$(dirname "$(realpath "$0")")
die() { echo "make-debian-stack: $*" >&2; exit 1; }

[[ $# -eq 2 ]] || die "usage: $0 WORK_DIR NAME"
name=$2
[[ $name =~ ^[a-z][a-z0-9-]*$ ]] || die "NAME is a host name: lower-case letters, digits and -"
[[ $EUID -eq 0 ]] || die "must run as root"
for t in debootstrap sfdisk mdadm cryptsetup pvcreate vgcreate lvcreate mkfs.ext4 mkfs.xfs mkfs.vfat mkswap \
         qemu-img qemu-system-x86_64 python3 losetup openssl ssh-keygen blkid; do
    command -v "$t" >/dev/null || die "needs $t"
done
[[ -s $KEYRING ]] || die "no $KEYRING (debian-archive-keyring)"
for f in OVMF_CODE_4M.secboot.fd OVMF_VARS_4M.ms.fd; do
    [[ -s /usr/share/OVMF/$f ]] || die "no /usr/share/OVMF/$f (ovmf)"
done
vg=$name-vg
lvm_() { "$@" --devicesfile ""; }   # LVM without this host's devices file
lvm_ vgs "$vg" >/dev/null 2>&1 && die "this host already has a volume group $vg"
mkdir -p "$1"
cd "$1"
work_dir=$(pwd)
disks=("$work_dir/$name.qcow2" "$work_dir/$name-b.qcow2")
for d in "${disks[@]}"; do [[ -e $d ]] && die "$d already exists; move it away to rebuild"; done
owner=${SUDO_USER:-root}

# --- Disks, arrays, LUKS, LVM ----------------------------------------------------------
tag=bkos$$                        # this host's names for the arrays and the LUKS volume
root=/mnt/bko-debian-stack-$$     # the new system
raws=("$work_dir/.$name.raw" "$work_dir/.$name-b.raw")
devs=() md=() crypt="" vg_active=no
unmount_all() { ! mountpoint -q "$root" || umount -R "$root" || umount -R -l "$root"; }
cleanup() {
    set +e
    unmount_all
    [[ $vg_active == yes ]] && lvm_ vgchange -a n "$vg" >/dev/null
    [[ -n $crypt ]] && cryptsetup close "$crypt"
    for m in "${md[@]}"; do mdadm --stop "$m" >/dev/null 2>&1; done
    for d in "${devs[@]}"; do losetup -d "$d"; done
    rm -f "${raws[@]}"
    rmdir "$root" 2>/dev/null
}
trap cleanup EXIT

for r in "${raws[@]}"; do
    truncate -s "$DISK_SIZE" "$r"
    d=$(losetup --find --show --partscan "$r")
    devs+=("$d")
    sfdisk -q "$d" <<EOF
label: gpt
size=$ESP_SIZE, type=$ESP_TYPE, name="EFI system partition"
size=$BOOT_SIZE, type=$RAID_TYPE, name="boot"
type=$RAID_TYPE, name="system"
EOF
done
udevadm settle
for d in "${devs[@]}"; do
    for _ in 1 2 3 4 5; do [[ -e ${d}p3 ]] && break; sleep 1; done
    [[ -e ${d}p3 ]] || die "${d}p3 didn't appear"
done
a=${devs[0]} b=${devs[1]}
for i in 0 1; do
    p=$((i + 2))
    # The array's name is NAME:i, as an installer on host NAME makes it.
    mdadm --create "/dev/md/$tag-$i" --run --metadata=1.2 --level=1 --raid-devices=2 --assume-clean \
        --homehost="$name" --name="$i" "${a}p$p" "${b}p$p" >/dev/null 2>&1 ||
        die "mdadm --create md$i failed"
    md+=("/dev/md/$tag-$i")
done
udevadm settle
umask 077
openssl rand -base64 24 | tr -dc 'A-Za-z0-9' >console-passphrase
umask 022
cryptsetup luksFormat --batch-mode --type luks2 --pbkdf argon2id --pbkdf-memory 262144 \
    --key-file console-passphrase "${md[1]}"
cryptsetup open --key-file console-passphrase "${md[1]}" "$tag-crypt"
crypt=$tag-crypt
lvm_ pvcreate -q "/dev/mapper/$crypt" >/dev/null
lvm_ vgcreate -q "$vg" "/dev/mapper/$crypt" >/dev/null
vg_active=yes
lvm_ lvcreate -q -y -L "$ROOT_SIZE" -n root "$vg" >/dev/null
lvm_ lvcreate -q -y -L "$SRV_SIZE" -n srv "$vg" >/dev/null
lvm_ lvcreate -q -y -L "$SWAP_SIZE" -n swap_1 "$vg" >/dev/null
udevadm settle
lv=/dev/mapper/${vg//-/--}           # how device-mapper names NAME-vg's volumes
mkfs.vfat -F 32 "${a}p1" >/dev/null
mkfs.vfat -F 32 "${b}p1" >/dev/null
mkfs.ext4 -q -F "${md[0]}"
mkfs.ext4 -q -F "$lv-root"
mkfs.xfs -q -f "$lv-srv"
mkswap -q "$lv-swap_1"
mkdir -p "$root"
mount "$lv-root" "$root"
mkdir -p "$root/boot" "$root/srv"
mount "${md[0]}" "$root/boot"
mkdir -p "$root/boot/efi" "$root/boot/efi2"
mount "${a}p1" "$root/boot/efi"
mount "$lv-srv" "$root/srv"

# --- Debian -------------------------------------------------------------------------
echo "debootstrap $SUITE from $MIRROR"
debootstrap --force-check-sig --keyring="$KEYRING" --components=main \
    "$SUITE" "$root" "$MIRROR" >"$work_dir/debootstrap.log" 2>&1 ||
    die "debootstrap failed; see $work_dir/debootstrap.log"
rm -f "$root/etc/apt/sources.list"
cat >"$root/etc/apt/sources.list.d/debian.sources" <<EOF
Types: deb
URIs: $MIRROR
Suites: $SUITE $SUITE-updates
Components: main non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: $SECURITY
Suites: $SUITE-security
Components: main non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF

for d in dev dev/pts proc sys; do mount --bind "/$d" "$root/$d"; done
mount -t tmpfs -o mode=755 tmpfs "$root/run"
# lvm in the chroot (the initramfs hook) asks lvmetad-less LVM for the PV,
# which wants /run/lvm and the udev database's view of the devices.
mkdir -p "$root/run/udev" && mount --bind /run/udev "$root/run/udev"
cp -L /etc/resolv.conf "$root/etc/resolv.conf"
printf '#!/bin/sh\nexit 101\n' >"$root/usr/sbin/policy-rc.d"
chmod 755 "$root/usr/sbin/policy-rc.d"
in_root() { chroot "$root" env DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8 "$@"; }

echo "$name" >"$root/etc/hostname"
printf '127.0.0.1\tlocalhost\n127.0.1.1\t%s\n::1\tlocalhost ip6-localhost ip6-loopback\n' "$name" >"$root/etc/hosts"
uuid() { blkid -s UUID -o value "$1"; }
mdadm_uuid() { mdadm --detail --export "$1" | sed -n 's/^MD_UUID=//p'; }
# As Debian's installer writes them: logical volumes by their mapper names,
# the rest by UUID.
cat >"$root/etc/fstab" <<EOF
# Written by BackOtter's make-debian-stack.sh.
$lv-root / ext4 errors=remount-ro 0 1
UUID=$(uuid "${md[0]}") /boot ext4 defaults 0 2
UUID=$(uuid "${a}p1") /boot/efi vfat umask=0077 0 1
UUID=$(uuid "${b}p1") /boot/efi2 vfat umask=0077,nofail 0 1
$lv-srv /srv xfs defaults 0 2
$lv-swap_1 none swap sw 0 0
EOF
# "initramfs" for the build only: the chroot sees the volume as $crypt, not
# md1_crypt, so cryptsetup-initramfs's hook can't tell / needs it.  The
# first boot rebuilds the initramfs without it, as the guest sees itself.
crypttab="md1_crypt UUID=$(uuid "${md[1]}") none luks,discard"
echo "$crypttab,initramfs" >"$root/etc/crypttab"
mkdir -p "$root/etc/mdadm"
{
    echo "# Written by BackOtter's make-debian-stack.sh."
    echo "HOMEHOST <system>"
    echo "MAILADDR root"
    for i in 0 1; do echo "ARRAY /dev/md/$i metadata=1.2 UUID=$(mdadm_uuid "${md[$i]}") name=$name:$i"; done
} >"$root/etc/mdadm/mdadm.conf"
mkdir -p "$root/etc/initramfs-tools/conf.d"
echo "RESUME=$lv-swap_1" >"$root/etc/initramfs-tools/conf.d/resume"

echo "Installing the kernel, mdadm, cryptsetup, LVM, XFS, shim and GRUB, sshd"
in_root apt-get -q update >/dev/null
# GRUB keeps the removable path up to date at its upgrades, and the build
# writes no boot entry (there are no variables to write to here).
printf '%s\n' "grub-efi-amd64 grub2/force_efi_extra_removable boolean true" \
    "grub-efi-amd64 grub2/update_nvram boolean false" | in_root debconf-set-selections
in_root apt-get -y -q install zstd linux-image-amd64 mdadm cryptsetup cryptsetup-initramfs lvm2 xfsprogs \
    dosfstools shim-signed grub-efi-amd64-signed grub-efi-amd64 efibootmgr mokutil openssh-server dbus \
    python3 os-prober- >"$work_dir/apt.log" 2>&1 || die "installing the system failed; see $work_dir/apt.log"
kver=$(ls "$root/lib/modules" | sort -V | tail -n 1)

# Network: DHCP (IPv4: the hosts have no IPv6 route out) on any wired card.
cat >"$root/etc/systemd/network/20-wired.network" <<'EOF'
[Match]
Type=ether

[Network]
DHCP=ipv4
EOF
in_root systemctl enable systemd-networkd >/dev/null 2>&1

# Identity: root's password and SSH key.
umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$name test guest" -f id_ed25519
umask 022
printf 'root:%s\n' "$hash" | in_root chpasswd -e
install -d -m 700 "$root/root/.ssh"
install -m 600 id_ed25519.pub "$root/root/.ssh/authorized_keys"

# The first boot (in the build, below): what came up, the initramfs
# rebuilt by the guest itself, then this goes and the guest powers off.
cat >"$root/etc/bko-first-boot" <<'EOF'
#!/bin/sh
# make-debian-stack.sh's first boot: say what came up, rebuild the initramfs
# as the running system sees itself, then remove this and power off.
set -e
echo "bko-first-boot: start"
echo "bko-first-boot: secure boot: $(mokutil --sb-state 2>&1 | head -n 1)"
cat /proc/mdstat
cryptsetup status md1_crypt | head -n 3
lvs --noheadings -o lv_name,lv_size,vg_name
findmnt -rno TARGET,SOURCE,FSTYPE | grep -E '^(/|/boot|/boot/efi|/boot/efi2|/srv) '
swapon --show --noheadings | grep -q .
update-initramfs -u -k all
# grep reads it all: grep -q would stop lsinitramfs's zstd with SIGPIPE.
lsinitramfs /boot/initrd.img-$(uname -r) | grep 'cryptroot/crypttab$' >/dev/null
rm -f /etc/systemd/system/multi-user.target.wants/bko-first-boot.service \
    /etc/systemd/system/bko-first-boot.service /etc/bko-first-boot
sync
echo "bko-first-boot: done"
systemctl --no-block poweroff
EOF
chmod 755 "$root/etc/bko-first-boot"
cat >"$root/etc/systemd/system/bko-first-boot.service" <<'EOF'
[Unit]
Description=BackOtter build: check the boot, rebuild the initramfs, power off
After=multi-user.target
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

# The bootloader, with a serial console: the kernel's last console= is
# /dev/console, where the initramfs asks for the passphrase.
sed -i -e 's|^GRUB_CMDLINE_LINUX_DEFAULT=.*|GRUB_CMDLINE_LINUX_DEFAULT="console=tty0 console=ttyS0,115200n8"|' \
    "$root/etc/default/grub"
printf '%s\n' 'GRUB_TERMINAL="console serial"' 'GRUB_SERIAL_COMMAND="serial --unit=0 --speed=115200"' \
    >>"$root/etc/default/grub"
{ in_root update-initramfs -u -k all || in_root update-initramfs -c -k all; } >"$work_dir/initramfs.log" 2>&1 ||
    die "update-initramfs failed; see $work_dir/initramfs.log"
# grep reads it all: grep -q would stop lsinitramfs's zstd with SIGPIPE.
for f in 'cryptroot/crypttab' 'sbin/mdadm' 'sbin/lvm' 'etc/mdadm/mdadm.conf'; do
    in_root lsinitramfs "/boot/initrd.img-$kver" | grep "$f\$" >/dev/null || die "the initramfs has no $f"
done
in_root grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=debian \
    --uefi-secure-boot --force-extra-removable --no-nvram >"$work_dir/grub-install.log" 2>&1 ||
    die "grub-install failed; see $work_dir/grub-install.log"
in_root update-grub 2>>"$work_dir/grub-install.log"
grep -q "$lv-root" "$root/boot/grub/grub.cfg" || die "grub.cfg doesn't boot $lv-root"
for f in EFI/BOOT/BOOTX64.EFI EFI/BOOT/grubx64.efi EFI/debian/shimx64.efi EFI/debian/grub.cfg; do
    [[ -s $root/boot/efi/$f ]] || die "the EFI system partition has no $f"
done
# GRUB on the removable path reads the grub.cfg beside it, which points it
# at /boot/grub, as EFI/debian's does.
[[ -e $root/boot/efi/EFI/BOOT/grub.cfg ]] || cp "$root/boot/efi/EFI/debian/grub.cfg" "$root/boot/efi/EFI/BOOT/grub.cfg"
# The second disk's ESP: a copy of the first's, so either disk boots alone.
mount "${b}p1" "$root/boot/efi2"
cp -a "$root/boot/efi/." "$root/boot/efi2/"
echo "$crypttab" >"$root/etc/crypttab"

# systemd-resolved last: it makes /etc/resolv.conf its stub, which the
# chroot can't use.
in_root apt-get -y -q install systemd-resolved >/dev/null
ln -sfn ../run/systemd/resolve/stub-resolv.conf "$root/etc/resolv.conf"
rm -f "$root/usr/sbin/policy-rc.d"
in_root apt-get clean
sync
unmount_all
lvm_ vgchange -a n "$vg" >/dev/null
vg_active=no
cryptsetup close "$crypt"
crypt=""
for m in "${md[@]}"; do mdadm --stop "$m" >/dev/null; done
md=()
for d in "${devs[@]}"; do losetup -d "$d"; done
devs=()
# Sparse in, sparse out: qcow2 stores only what was written.
for i in 0 1; do qemu-img convert -f raw -O qcow2 "${raws[$i]}" "${disks[$i]}"; done
cleanup
trap - EXIT
set -e      # cleanup turned it off
echo "--uefi --disk $name-b.qcow2" >vm-run-options

# --- First boot -----------------------------------------------------------------------
# As vm-run --uefi boots it: OVMF with Secure Boot and Microsoft's keys, a
# fresh variable store; the passphrase typed at the initramfs's prompt.
echo "First boot, with Secure Boot; the console goes to $work_dir/firstboot-serial.log"
vars=$(mktemp)
cp /usr/share/OVMF/OVMF_VARS_4M.ms.fd "$vars"
timeout "$FIRST_BOOT_TIMEOUT" python3 "$here/console-boot.py" firstboot-serial.log console-passphrase -- \
    qemu-system-x86_64 -enable-kvm -cpu host -smp 2 -m "$MEMORY" \
    -machine q35,smm=on -global driver=cfi.pflash01,property=secure,value=on \
    -drive "if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.secboot.fd" \
    -drive "if=pflash,format=raw,unit=1,file=$vars" \
    -drive "file=${disks[0]},if=virtio,format=qcow2" -drive "file=${disks[1]},if=virtio,format=qcow2" \
    -nic user,ipv6=off,model=virtio-net-pci \
    -display none -serial stdio -monitor none -no-reboot ||
    { rm -f "$vars"; die "the first boot failed or timed out; see firstboot-serial.log"; }
rm -f "$vars"
grep -q 'bko-first-boot: done' firstboot-serial.log || die "the first boot didn't finish; see firstboot-serial.log"
grep -q 'bko-first-boot: secure boot: SecureBoot enabled' firstboot-serial.log ||
    die "the first boot wasn't under Secure Boot; see firstboot-serial.log"

for d in "${disks[@]}"; do qemu-img snapshot -c fresh-install "$d"; done
chown "$owner": "${disks[@]}" vm-run-options console-passphrase root-password id_ed25519 id_ed25519.pub \
    debootstrap.log apt.log initramfs.log grub-install.log firstboot-serial.log
echo "Done: ${disks[*]} ($(du -ch "${disks[@]}" | tail -n 1 | cut -f1) on disk), snapshot \"fresh-install\""
