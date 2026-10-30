#!/usr/bin/env bash
# Build a test guest for BackOtter from a desktop distribution's live ISO
# (Linux Mint, Zorin OS, Pop!_OS, MX Linux), whose installers can't be run
# unattended reliably.  It does what those installers do: copy the live
# system's image onto the disk, remove the packages only the live session
# needs, write fstab, users and SSH access, then rebuild the initramfs and
# install the bootloader.  Installer quirks beyond that (Pop!_OS's recovery
# partition, for one) are not reproduced.
#
# Usage: sudo ./make-from-live.sh DISTRO ROOT_FS ISO WORK_DIR NAME
#   e.g. sudo ./make-from-live.sh mint f2fs linuxmint-22.3-xfce-64bit.iso vms/mint22 mint22
#
#   DISTRO   mint, zorin or pop (Ubuntu-based, casper live system),
#            or mx (Debian-based, antiX live system, sysvinit)
#   ROOT_FS  ext2, ext3, ext4, f2fs or nilfs2
#
# The ISO must already be verified (make-new-isos.sh does that).  Pop!_OS is
# installed for UEFI (GPT, an EFI system partition as the boot partition,
# systemd-boot), the others for BIOS (MBR, /boot ext4, GRUB).  Packages the
# guest needs beyond the live image (openssh-server, the tools for ROOT_FS,
# a bootloader) come from the distribution's online archive, so this needs
# network access and the result isn't frozen at the ISO's release.
#
# The guest keeps its desktop but boots to text mode, to stay light in 2 GB of
# RAM; root and a user "tester" share a generated password, and root accepts
# an SSH key.  Results, in WORK_DIR: NAME.qcow2 (16 GiB, snapshot
# "fresh-install"), root-password, id_ed25519, build.log, and for Pop!_OS
# vm-run-options ("--uefi --no-secure-boot": its bootloader isn't signed).
#
# Needs: root; qemu-img, losetup, unsquashfs, sfdisk, mkfs for ROOT_FS
# (f2fs-tools, nilfs-tools), dosfstools for Pop!_OS, xorriso, openssl.

set -euo pipefail

DISK_SIZE=16G
BOOT_SIZE=1GiB
SWAP_SIZE=2GiB

die() { echo "make-from-live: $*" >&2; exit 1; }

[[ $# -eq 5 ]] || die "usage: $0 DISTRO ROOT_FS ISO WORK_DIR NAME"
distro=$1 root_fs=$2 iso=$(realpath "$3") name=$5
case $distro in
    mint|zorin|pop) family=casper ;;
    mx) family=antix ;;
    *) die "DISTRO must be mint, zorin, pop or mx" ;;
esac
case $root_fs in ext2|ext3|ext4|f2fs|nilfs2) ;; *) die "unsupported ROOT_FS $root_fs" ;; esac
firmware=bios
[[ $distro == pop ]] && firmware=uefi
[[ $EUID -eq 0 ]] || die "must run as root"
[[ -f $iso ]] || die "no such ISO: $iso"
mkdir -p "$4"
cd "$4"
work_dir=$(pwd)
disk=$work_dir/$name.qcow2
[[ -e $disk ]] && die "$disk already exists; move it away to rebuild"
owner=${SUDO_USER:-root}

# --- Disk ---------------------------------------------------------------------------
root=/mnt/live-$name
tmp=$(mktemp -d)
dev=""
# The disk is built as a sparse raw file on a loop device and converted to
# qcow2 at the end.  (Writing gigabytes through qemu-nbd instead broke down
# under load: "Double reply ... Dead connection" in the kernel log.)
raw=$work_dir/.$name.raw
# Everything is mounted below $root; -R also catches what tools mount inside
# the bound /sys (bootctl's efivarfs, for one).
unmount_all() { ! mountpoint -q "$root" || umount -R "$root" || umount -R -l "$root"; }
cleanup() {
    unmount_all || true
    [[ -n $dev ]] && losetup -d "$dev"
    rm -f "$raw"
    chmod -R u+w "$tmp" 2>/dev/null; rm -rf "$tmp"
}
trap cleanup EXIT
mount_on() { mount "$@"; }

truncate -s "$DISK_SIZE" "$raw"
dev=$(losetup --find --show --partscan "$raw")

if [[ $firmware == uefi ]]; then
    sfdisk -q "$dev" <<EOF
label: gpt
size=$BOOT_SIZE, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name=EFI
size=$SWAP_SIZE, type=0657FD6D-A4AB-43C4-84E5-0933C84B4F4F, name=swap
type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name=root
EOF
else
    sfdisk -q "$dev" <<EOF
label: dos
size=$BOOT_SIZE, type=83, bootable
size=$SWAP_SIZE, type=82
type=83
EOF
fi
udevadm settle
for _ in 1 2 3 4 5; do [[ -e ${dev}p3 ]] && break; sleep 1; done
if [[ $firmware == uefi ]]; then mkfs.vfat -F 32 -n EFI "${dev}p1" >/dev/null; else mkfs.ext4 -q -F "${dev}p1"; fi
mkswap -q "${dev}p2"
case $root_fs in
    ext2|ext3|ext4) "mkfs.$root_fs" -q -F "${dev}p3" ;;
    f2fs) mkfs.f2fs -q -f "${dev}p3" ;;
    nilfs2) mkfs.nilfs2 -q -f "${dev}p3" ;;
esac
udevadm settle
uuid() { blkid -s UUID -o value "$1"; }

mkdir -p "$root"
mount_on -t "$root_fs" "${dev}p3" "$root"
boot_mp=/boot
[[ $firmware == uefi ]] && boot_mp=/boot/efi

# --- The live system's image --------------------------------------------------------
case $family in
    casper)
        # Usually /casper; Pop!_OS names the folder after its build
        # (e.g. /casper_pop-os_24.04_amd64_intel_debug_377).
        image=$(xorriso -indev "$iso" -find / -maxdepth 2 -name filesystem.squashfs 2>/dev/null |
            tr -d "'" | head -n 1)
        [[ -n $image ]] || die "$iso has no casper filesystem.squashfs"
        remove_list=$(dirname "$image")/filesystem.manifest-remove ;;
    antix) image=/antiX/linuxfs; remove_list="" ;;
esac
echo "Copying $image from $(basename "$iso")"
xorriso -osirrox on -indev "$iso" -extract "$image" "$tmp/image.sfs" 2>/dev/null ||
    die "$iso has no $image"
[[ -n $remove_list ]] && { xorriso -osirrox on -indev "$iso" -extract "$remove_list" "$tmp/remove" 2>/dev/null || : >"$tmp/remove"; }
xattrs=()
# NILFS2 has no extended attributes at all, so file capabilities (and
# SELinux labels) can't be kept on it.
[[ $root_fs == nilfs2 ]] && xattrs=(-no-xattrs)
unsquashfs -q -f "${xattrs[@]}" -d "$root" "$tmp/image.sfs" >/dev/null
rm -f "$tmp/image.sfs"
mkdir -p "$root$boot_mp"
# The image's /boot (kernels) now lives on the root filesystem; for BIOS
# guests it moves to the separate /boot partition.
if [[ $firmware == bios ]]; then
    mv "$root/boot" "$tmp/boot"
    mkdir "$root/boot"
    mount_on -t ext4 "${dev}p1" "$root/boot"
    cp -a "$tmp/boot/." "$root/boot/"
else
    mount_on -t vfat "${dev}p1" "$root/boot/efi"
fi

# --- fstab ----------------------------------------------------------------------------
{
    echo "# Written by BackOtter's make-from-live.sh"
    echo "UUID=$(uuid "${dev}p3") / $root_fs defaults 0 1"
    if [[ $firmware == uefi ]]; then
        echo "UUID=$(uuid "${dev}p1") /boot/efi vfat umask=0077 0 2"
    else
        echo "UUID=$(uuid "${dev}p1") /boot ext4 defaults 0 2"
    fi
    echo "UUID=$(uuid "${dev}p2") none swap sw 0 0"
} >"$root/etc/fstab"
# ext2 and NILFS2 have no fsck worth running at boot.
[[ $root_fs == nilfs2 ]] && sed -i 's| / nilfs2 defaults 0 1| / nilfs2 defaults 0 0|' "$root/etc/fstab"

# --- Inside the new system ----------------------------------------------------------
for d in dev dev/pts proc sys run; do mount_on --bind "/$d" "$root/$d"; done
# Name resolution for apt, then the image's own resolv.conf back afterwards.
if [[ -e $root/etc/resolv.conf || -L $root/etc/resolv.conf ]]; then
    mv "$root/etc/resolv.conf" "$tmp/resolv.conf.image"
fi
cp -L /etc/resolv.conf "$root/etc/resolv.conf"
printf '#!/bin/sh\nexit 101\n' >"$root/usr/sbin/policy-rc.d"
chmod 755 "$root/usr/sbin/policy-rc.d"
in_root() { chroot "$root" env DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1 "$@"; }

if [[ -s ${tmp}/remove ]]; then
    mapfile -t live_pkgs < <(awk '{ print $1 }' "$tmp/remove" | sed 's/:.*//' | sort -u)
    mapfile -t live_pkgs < <(in_root dpkg-query -W -f '${db:Status-Status} ${Package}\n' "${live_pkgs[@]}" 2>/dev/null |
        awk '$1 == "installed" { print $2 }')
    echo "Removing ${#live_pkgs[@]} live-session packages"
    if [[ ${#live_pkgs[@]} -gt 0 ]]; then
        # Ubuntu marks the UEFI boot packages (shim-signed, grub-efi-amd64-signed,
        # mokutil) protected; on a BIOS install the distribution's own installer
        # removes them too.  Anything else protected stops the build.
        protected=$(in_root apt-get -s purge "${live_pkgs[@]}" 2>&1 |
            sed -n '/essential packages will be removed/,/^[0-9]* upgraded/p' |
            sed '1,2d;$d' | sed 's/([^)]*)//g' | tr ' ' '\n' | grep -E '^[a-z0-9][a-z0-9.+-]+$' || true)
        allow=()
        if [[ -n $protected ]]; then
            unexpected=$(grep -vxE 'shim-signed|grub-efi-amd64(-signed|-bin)?|mokutil' <<<"$protected" || true)
            [[ -z $unexpected ]] || die "removing the live packages would remove essential packages: $unexpected"
            [[ $firmware == bios ]] || die "the live packages include UEFI boot packages: $protected"
            allow=(--allow-remove-essential)
        fi
        in_root apt-get -y -q "${allow[@]}" purge "${live_pkgs[@]}" >/dev/null
    fi
fi

extra=(openssh-server)
case $root_fs in f2fs) extra+=(f2fs-tools) ;; nilfs2) extra+=(nilfs-tools) ;; esac
if [[ $firmware == bios ]]; then extra+=(grub-pc); fi
echo "Installing ${extra[*]}"
in_root apt-get -q update >/dev/null
echo "grub-pc grub-pc/install_devices multiselect" | in_root debconf-set-selections
in_root apt-get -y -q install "${extra[@]}" >/dev/null

# Identity: host name, a fresh machine id, root and tester, SSH.
echo "$name" >"$root/etc/hostname"
printf '127.0.0.1\tlocalhost\n127.0.1.1\t%s\n::1\tlocalhost ip6-localhost ip6-loopback\n' "$name" >"$root/etc/hosts"
rm -f "$root/etc/machine-id" "$root/var/lib/dbus/machine-id"
in_root systemd-machine-id-setup >/dev/null 2>&1 || in_root dbus-uuidgen --ensure=/etc/machine-id
umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$name test guest" -f id_ed25519
umask 022
in_root sh -c "id tester >/dev/null 2>&1 || useradd -m -s /bin/bash -c 'Test user' -G sudo tester"
printf 'root:%s\ntester:%s\n' "$hash" "$hash" | in_root chpasswd -e
install -d -m 700 "$root/root/.ssh"
install -m 600 id_ed25519.pub "$root/root/.ssh/authorized_keys"
rm -f "$root"/etc/ssh/ssh_host_*      # made on first boot, or by ssh-keygen -A below
in_root ssh-keygen -A >/dev/null

# Text mode at boot, and a serial console too.
if [[ $family == antix ]]; then
    # MX's live system sets up the network at boot with its own scripts;
    # installed, nothing does, so give NetworkManager a wired DHCP connection.
    install -d -m 755 "$root/etc/NetworkManager/system-connections"
    cat >"$root/etc/NetworkManager/system-connections/wired.nmconnection" <<'EOF'
[connection]
id=Wired
type=ethernet
autoconnect=true

[ipv4]
method=auto

[ipv6]
method=auto
EOF
    chmod 600 "$root/etc/NetworkManager/system-connections/wired.nmconnection"
    # MX enables ufw, denying all incoming connections.  Keep it on, as a real
    # install has it, and allow SSH alone (the rules "ufw allow 22/tcp" writes).
    for v in "" 6; do
        rules=$root/etc/ufw/user$v.rules
        [[ -f $rules ]] || continue
        any=0.0.0.0/0
        [[ $v == 6 ]] && any=::/0
        sed -i "/^### RULES ###/a ### tuple ### allow tcp 22 $any any $any in\n-A ufw$v-user-input -p tcp --dport 22 -j ACCEPT\n" "$rules"
    done
    for dm in lightdm sddm; do in_root update-rc.d "$dm" disable >/dev/null 2>&1 || true; done
    in_root update-rc.d ssh enable >/dev/null 2>&1 || true
    grep -q '^T0:' "$root/etc/inittab" ||
        echo 'T0:2345:respawn:/sbin/getty -L ttyS0 115200 vt100' >>"$root/etc/inittab"
else
    in_root systemctl set-default multi-user.target >/dev/null
    in_root systemctl enable ssh >/dev/null 2>&1 || true
    # The live CD's self-check survives the removal of casper; it would fail
    # at every boot, looking for a CD.
    in_root systemctl mask casper-md5check.service >/dev/null 2>&1 || true
fi

# The initramfs must be able to mount / (F2FS and NILFS2 aren't in it by default).
case $root_fs in f2fs|nilfs2) grep -qx "$root_fs" "$root/etc/initramfs-tools/modules" ||
    echo "$root_fs" >>"$root/etc/initramfs-tools/modules" ;; esac

# The bootloader.
cmdline="console=tty0 console=ttyS0,115200n8"
if [[ $firmware == bios ]]; then
    sed -i "s/^GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT=\"$cmdline\"/" "$root/etc/default/grub"
    in_root update-initramfs -c -k all >/dev/null 2>&1 || in_root update-initramfs -u -k all >/dev/null
    in_root grub-install --target=i386-pc "$dev"
    in_root update-grub 2>/dev/null
else
    in_root update-initramfs -c -k all >/dev/null 2>&1 || in_root update-initramfs -u -k all >/dev/null
    in_root bootctl install --esp-path=/boot/efi --no-variables >/dev/null
    mkdir -p "$root/etc/kernelstub"
    cat >"$root/etc/kernelstub/configuration" <<EOF
{
  "default": {"kernel_options": ["quiet", "splash"], "esp_path": "/boot/efi", "setup_loader": false,
              "manage_mode": false, "force_update": false, "live_mode": false, "config_rev": 3},
  "user": {"kernel_options": ["quiet", "loglevel=0", "systemd.show_status=false", "splash",
                              "console=tty0", "console=ttyS0,115200n8"],
           "esp_path": "/boot/efi", "setup_loader": true, "manage_mode": true,
           "force_update": false, "live_mode": false, "config_rev": 3}
}
EOF
    in_root kernelstub -v -e /boot/efi -k /boot/vmlinuz -i /boot/initrd.img -l -m >/dev/null
fi

rm -f "$root/usr/sbin/policy-rc.d" "$root/etc/resolv.conf"
[[ -e $tmp/resolv.conf.image || -L $tmp/resolv.conf.image ]] && mv "$tmp/resolv.conf.image" "$root/etc/resolv.conf"
in_root apt-get clean
sync
unmount_all
losetup -d "$dev"
dev=""
# Sparse in, sparse out: qcow2 stores only what was written.
qemu-img convert -f raw -O qcow2 "$raw" "$disk"
cleanup
trap - EXIT

qemu-img snapshot -c fresh-install "$disk"
chown "$owner": "$disk" root-password id_ed25519 id_ed25519.pub
if [[ $firmware == uefi ]]; then
    echo "--uefi --no-secure-boot" >vm-run-options
    chown "$owner": vm-run-options
fi
echo "Done: $disk ($(du -h "$disk" | cut -f1) on disk), snapshot \"fresh-install\""
