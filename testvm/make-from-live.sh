#!/usr/bin/env bash
# Build a test guest for BackOtter from a desktop distribution's live ISO
# (Linux Mint, Zorin OS, Pop!_OS, elementary OS, KDE neon, MX Linux), whose
# installers can't be run unattended reliably.  It does what those installers
# do: copy the live system's image onto the disk, remove the packages only the
# live session needs, write fstab, users and SSH access, then rebuild the
# initramfs and install the bootloader.  Installer quirks beyond that (Pop!_OS's
# recovery partition, for one) are not reproduced.
#
# Usage: sudo ./make-from-live.sh [--layout distinst|plain] [--luks] DISTRO ROOT_FS ISO WORK_DIR NAME
#   e.g. sudo ./make-from-live.sh mint f2fs linuxmint-22.3-xfce-64bit.iso vms/mint22 mint22
#        sudo ./make-from-live.sh --luks elementary ext4 elementaryos-8.1-stable-amd64.20260219.iso \
#            vms/elementary8-luks elementary8-luks
#
#   DISTRO   mint, zorin, pop, elementary or neon (Ubuntu-based, casper live
#            system), or mx (Debian-based, antiX live system, sysvinit)
#   ROOT_FS  ext2, ext3, ext4, f2fs or nilfs2 (ext4 only with --layout distinst)
#   --layout plain     three partitions: /boot (BIOS) or the EFI system
#            partition (UEFI), swap, root; for neon two, as its Calamares lays
#            them out (see below).  The default, except for elementary.
#   --layout distinst  elementary OS's installer's (distinst's) layout, and
#            elementary's default; refused for the others.  GPT: an EFI system
#            partition (278 MiB) at /boot/efi, then one LVM physical volume
#            holding VG "data", with LV "root" (ext4, /) and a 4 GiB LV "swap"
#            at the end.
#   --luks   with --layout distinst: distinst's encrypted layout.  The ESP, an
#            ext4 /boot (1 GiB), then a LUKS2 volume opened as "cryptdata"
#            holding VG "data" as above.  The guest asks for the passphrase on
#            its serial console at boot.
#
# The ISO must already be verified (make-new-isos.sh does that).  Pop!_OS,
# elementary OS and KDE neon are installed for UEFI (GPT, an EFI system
# partition at /boot/efi): Pop!_OS with systemd-boot, elementary and neon with
# GRUB through shim (Secure Boot); neon as its Calamares's "Erase disk" does
# it: a 300 MiB EFI system partition, root on the rest, no swap partition but
# a 512 MiB /swapfile.  The others are installed for BIOS (MBR,
# /boot ext4, GRUB).  Packages the guest needs beyond the live image
# (openssh-server, the tools for ROOT_FS, a bootloader, LVM, cryptsetup) come
# from the distribution's online archive, so this needs network access and the
# result isn't frozen at the ISO's release.
#
# The guest keeps its desktop but boots to text mode, to stay light in 2 GB of
# RAM (neon's gets 4 GB); root and a user "tester" share a generated password,
# and root accepts an SSH key.  Results, in WORK_DIR: NAME.qcow2 (16 GiB, 24 GiB
# for neon; snapshot "fresh-install"), root-password, id_ed25519, build.log; for
# the UEFI guests vm-run-options ("--uefi" for elementary, "--uefi --memory
# 4096" for neon, "--uefi --no-secure-boot"
# for Pop!_OS, whose bootloader isn't signed); with --luks, console-passphrase
# (the LUKS passphrase, mode 600), which vm-run and compare.py type on the
# serial console at each boot.
#
# Needs: root; qemu-img, losetup, unsquashfs, sfdisk, mkfs for ROOT_FS
# (f2fs-tools, nilfs-tools), dosfstools for the UEFI guests, lvm2 for
# --layout distinst, cryptsetup for --luks, xorriso, openssl.

set -euo pipefail

DISK_SIZE=16G
BOOT_SIZE=1GiB
SWAP_SIZE=2GiB
# distinst's sizes.  Its ESP is 278 MB; 278 MiB is close enough.
ESP_SIZE_DISTINST=278MiB
LV_SWAP_SIZE=$((4 << 30))
# neon's Calamares's: its built-in recommended ESP size (neon's partition.conf
# sets none) and the fstab module's swap file (create_swapfile).
ESP_SIZE_NEON=300MiB
SWAP_FILE_MIB_NEON=512

die() { echo "make-from-live: $*" >&2; exit 1; }
usage="usage: $0 [--layout distinst|plain] [--luks] DISTRO ROOT_FS ISO WORK_DIR NAME"

layout="" luks=no
while [[ $# -gt 0 ]]; do
    case $1 in
        --layout) [[ $# -ge 2 ]] || die "$usage"; layout=$2; shift 2 ;;
        --layout=*) layout=${1#--layout=}; shift ;;
        --luks) luks=yes; shift ;;
        --) shift; break ;;
        -*) die "unknown option $1; $usage" ;;
        *) break ;;
    esac
done
[[ $# -eq 5 ]] || die "$usage"
distro=$1 root_fs=$2 iso=$(realpath "$3") name=$5
case $distro in
    mint|zorin|pop|elementary|neon) family=casper ;;
    mx) family=antix ;;
    *) die "DISTRO must be mint, zorin, pop, elementary, neon or mx" ;;
esac
case $root_fs in ext2|ext3|ext4|f2fs|nilfs2) ;; *) die "unsupported ROOT_FS $root_fs" ;; esac
# The firmware, and the bootloader: GRUB for BIOS (grub-pc), systemd-boot
# with kernelstub (Pop!_OS), or GRUB through shim for UEFI (grub-shim:
# Ubuntu's signed shim and GRUB, as Ubuntu itself installs them).
case $distro in
    mint|zorin|mx) firmware=bios loader=grub-pc ;;
    pop) firmware=uefi loader=systemd-boot ;;
    elementary|neon) firmware=uefi loader=grub-shim ;;
esac
# grub-shim's \EFI\ID\ directory.  distinst (elementary's installer backend)
# names it after os-release's NAME, with spaces as underscores, except that
# "elementary OS" becomes "ubuntu" (normalize_os_release_name() in
# pop-os/distinst src/installer/steps/mod.rs): Ubuntu's signed GRUB looks for
# its grub.cfg in \EFI\ubuntu\.  neon's Calamares uses "neon".
efi_id=""
case $distro in
    elementary) efi_id=ubuntu ;;
    neon) efi_id=neon ;;
esac
# neon's desktop takes about 10 GiB installed (Check, N3), so a bigger disk.
case $distro in neon) DISK_SIZE=24G ;; esac
if [[ -z $layout ]]; then
    layout=plain
    if [[ $distro == elementary ]]; then layout=distinst; fi
fi
case $layout in
    plain) ;;
    distinst) [[ $distro == elementary ]] ||
        die "--layout distinst is elementary OS's installer's layout; $distro uses --layout plain" ;;
    *) die "--layout must be distinst or plain" ;;
esac
[[ $luks == no || $layout == distinst ]] || die "--luks needs --layout distinst"
[[ $layout == plain || $root_fs == ext4 ]] || die "--layout distinst puts / on ext4; ROOT_FS must be ext4"
[[ $EUID -eq 0 ]] || die "must run as root"
[[ -f $iso ]] || die "no such ISO: $iso"
# distinst's names are fixed: VG "data" and LUKS volume "cryptdata", the
# same in every elementary (and Pop!_OS) install.  The guest is built under
# those names, on this host, so they must be free here: a second VG "data"
# would clash with this build's, and LVM would refuse or pick the wrong one.
# --devicesfile "": every device, whatever this host's devices file lists.
lvm_all() { "$@" --devicesfile "" 2>/dev/null || "$@" 2>/dev/null; }
if [[ $layout == distinst ]]; then
    for t in pvcreate vgcreate lvcreate vgchange vgs dmsetup; do
        command -v "$t" >/dev/null || die "needs $t (lvm2)"
    done
    [[ $luks == no ]] || command -v cryptsetup >/dev/null || die "needs cryptsetup"
    held=$( { lvm_all vgs --noheadings -o vg_name | awk '$1 == "data" { print "VG data" }'
              dmsetup ls 2>/dev/null | awk '$1 ~ /^(data-[^-]|cryptdata$)/ { print "device-mapper " $1 }'; } |
        sort -u | paste -sd, - || true)
    [[ -z $held ]] || die "this host already has $held: another build or a mounted elementary guest holds it; " \
        "unmount or deactivate it first (vgchange -an data; cryptsetup close cryptdata)"
fi
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
# --layout distinst: the LVM physical volume ($pv), and whether VG "data"
# and LUKS volume "cryptdata" are up, for the clean-up.
pv="" vg_active=no crypt_open=no
# LVM on this build's physical volume alone.  --devices overrides this host's
# devices file, which doesn't list the loop device and would hide it.
lvm_pv() { "$@" --devices "$pv"; }
# Everything is mounted below $root; -R also catches what tools mount inside
# the bound /sys (bootctl's efivarfs, for one).
unmount_all() { ! mountpoint -q "$root" || umount -R "$root" || umount -R -l "$root"; }
# VG "data" and "cryptdata" hold the loop device: down before losetup -d.
close_lvm() {
    if [[ $vg_active == yes ]]; then lvm_pv vgchange -a n data >/dev/null; vg_active=no; fi
    if [[ $crypt_open == yes ]]; then cryptsetup close cryptdata; crypt_open=no; fi
}
cleanup() {
    unmount_all || true
    close_lvm || true
    [[ -n $dev ]] && losetup -d "$dev"
    rm -f "$raw"
    chmod -R u+w "$tmp" 2>/dev/null; rm -rf "$tmp"
}
trap cleanup EXIT
mount_on() { mount "$@"; }

truncate -s "$DISK_SIZE" "$raw"
dev=$(losetup --find --show --partscan "$raw")

# The partitions' roles: the EFI system partition ($esp, UEFI), a separate
# ext4 /boot ($boot_part: BIOS, and distinst's with --luks), swap and root
# ($swap_dev, $root_dev: partitions, or LVs in VG "data"; neon has no swap
# device but a swap file, $swap_file).
esp="" boot_part="" luks_part="" swap_file=""
if [[ $layout == distinst ]]; then
    # Check (E1): the partition type GUIDs and names distinst (libparted) sets;
    # here the ESP's, Linux LVM for the plain PV and Linux data under LUKS.
    if [[ $luks == yes ]]; then
        sfdisk -q "$dev" <<EOF
label: gpt
size=$ESP_SIZE_DISTINST, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B
size=$BOOT_SIZE, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4
type=0FC63DAF-8483-4772-8E79-3D69D8477DE4
EOF
        esp=${dev}p1 boot_part=${dev}p2 luks_part=${dev}p3 pv=/dev/mapper/cryptdata last=${dev}p3
    else
        sfdisk -q "$dev" <<EOF
label: gpt
size=$ESP_SIZE_DISTINST, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B
type=E6D6D379-F507-44C2-A23C-238F2A3DF928
EOF
        esp=${dev}p1 pv=${dev}p2 last=${dev}p2
    fi
    swap_dev=/dev/mapper/data-swap root_dev=/dev/mapper/data-root
elif [[ $distro == neon ]]; then
    # neon's Calamares, "Erase disk" with its default swap choice ("file",
    # partition.conf's initialSwapChoice): the ESP, then root on the rest; swap
    # is a file on root ($swap_file, below).  Calamares sets no partition
    # names (no efiSystemPartitionName, no partitionLayout).
    sfdisk -q "$dev" <<EOF
label: gpt
size=$ESP_SIZE_NEON, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B
type=0FC63DAF-8483-4772-8E79-3D69D8477DE4
EOF
    esp=${dev}p1 swap_dev="" root_dev=${dev}p2 last=${dev}p2 swap_file=/swapfile
elif [[ $firmware == uefi ]]; then
    sfdisk -q "$dev" <<EOF
label: gpt
size=$BOOT_SIZE, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name=EFI
size=$SWAP_SIZE, type=0657FD6D-A4AB-43C4-84E5-0933C84B4F4F, name=swap
type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name=root
EOF
    esp=${dev}p1 swap_dev=${dev}p2 root_dev=${dev}p3 last=${dev}p3
else
    sfdisk -q "$dev" <<EOF
label: dos
size=$BOOT_SIZE, type=83, bootable
size=$SWAP_SIZE, type=82
type=83
EOF
    boot_part=${dev}p1 swap_dev=${dev}p2 root_dev=${dev}p3 last=${dev}p3
fi
udevadm settle
for _ in 1 2 3 4 5; do [[ -e $last ]] && break; sleep 1; done
if [[ -n $esp ]]; then mkfs.vfat -F 32 -n EFI "$esp" >/dev/null; fi
if [[ -n $boot_part ]]; then mkfs.ext4 -q -F "$boot_part"; fi
if [[ $layout == distinst ]]; then
    if [[ $luks == yes ]]; then
        # The passphrase, as vm-lib.sh and compare.py expect it: in
        # console-passphrase, typed on the serial console at each boot.  No
        # newline, so the key file and the typed passphrase are the same key.
        umask 077
        openssl rand -base64 24 | tr -dc 'A-Za-z0-9' >console-passphrase
        umask 022
        # The key derivation's memory is capped: cryptsetup would size it to
        # this host's RAM (up to 1 GiB), and the guest unlocks in 2 GB.
        # Check (E1): distinst's luksFormat options (LUKS2 is cryptsetup's default).
        cryptsetup luksFormat --batch-mode --type luks2 --pbkdf argon2id --pbkdf-memory 262144 \
            --key-file console-passphrase "$luks_part"
        cryptsetup open --key-file console-passphrase "$luks_part" cryptdata
        crypt_open=yes
    fi
    vg_active=yes
    lvm_pv pvcreate -q "$pv" >/dev/null
    lvm_pv vgcreate -q data "$pv" >/dev/null
    # root first, then swap on what's left, so swap is the last 4 GiB, as
    # distinst lays it out.
    read -r extent free < <(lvm_pv vgs --noheadings --units b --nosuffix -o vg_extent_size,vg_free_count data)
    [[ ${extent:-} =~ ^[0-9]+$ && ${free:-} =~ ^[0-9]+$ ]] || die "can't read VG data's free extents"
    lvm_pv lvcreate -q -y -l $((free - LV_SWAP_SIZE / extent)) -n root data >/dev/null
    lvm_pv lvcreate -q -y -l 100%FREE -n swap data >/dev/null
    udevadm settle
    [[ -e $root_dev && -e $swap_dev ]] || die "no $root_dev or $swap_dev after lvcreate"
fi
if [[ -n $swap_dev ]]; then mkswap -q "$swap_dev"; fi
case $root_fs in
    ext2|ext3|ext4) "mkfs.$root_fs" -q -F "$root_dev" ;;
    f2fs) mkfs.f2fs -q -f "$root_dev" ;;
    nilfs2) mkfs.nilfs2 -q -f "$root_dev" ;;
esac
udevadm settle
uuid() { blkid -s UUID -o value "$1"; }

mkdir -p "$root"
mount_on -t "$root_fs" "$root_dev" "$root"
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
        casper_dir=$(dirname "$image")
        remove_list=$casper_dir/filesystem.manifest-remove
        # neon's ISO has one too, but it is Ubiquity's convention, which neon's
        # Calamares doesn't follow: neon's list is made below.
        if [[ $distro == neon ]]; then remove_list=""; fi ;;
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
# The image's /boot (kernels) now lives on the root filesystem; where there
# is a separate /boot partition (BIOS, distinst with --luks) it moves there.
if [[ -n $boot_part ]]; then
    mv "$root/boot" "$tmp/boot"
    mkdir "$root/boot"
    mount_on -t ext4 "$boot_part" "$root/boot"
    cp -a "$tmp/boot/." "$root/boot/"
fi
if [[ -n $esp ]]; then mount_on -t vfat "$esp" "$root/boot/efi"; fi

# --- fstab ----------------------------------------------------------------------------
{
    echo "# Written by BackOtter's make-from-live.sh"
    if [[ $layout == distinst ]]; then
        # Check (E1): distinst names / and swap by their device-mapper paths
        # (and the rest by UUID); its mount options and fsck passes.
        echo "$root_dev / $root_fs defaults 0 1"
        if [[ -n $boot_part ]]; then echo "UUID=$(uuid "$boot_part") /boot ext4 defaults 0 2"; fi
        echo "UUID=$(uuid "$esp") /boot/efi vfat umask=0077 0 2"
        echo "$swap_dev none swap sw 0 0"
    else
        echo "UUID=$(uuid "$root_dev") / $root_fs defaults 0 1"
        if [[ $distro == neon ]]; then
            # neon's Calamares 3.4.2: fstab.conf's efiMountOptions (umask=0077)
            # is an old key that its fstab module no longer reads, and neon's
            # mount.conf has no mountOptions, so the ESP gets "defaults".  The
            # swap file's line is the fstab module's own ("swap" for its mount
            # point, as for every swap).
            echo "UUID=$(uuid "$esp") /boot/efi vfat defaults 0 2"
            echo "$swap_file swap swap defaults 0 0"
        elif [[ $firmware == uefi ]]; then
            echo "UUID=$(uuid "$esp") /boot/efi vfat umask=0077 0 2"
        else
            echo "UUID=$(uuid "$boot_part") /boot ext4 defaults 0 2"
        fi
        if [[ -n $swap_dev ]]; then echo "UUID=$(uuid "$swap_dev") none swap sw 0 0"; fi
    fi
} >"$root/etc/fstab"
# neon's swap file, as Calamares's fstab module makes it (create_swapfile):
# zeros written out in full (a swap file can't have holes), mode 600, mkswap.
if [[ -n $swap_file ]]; then
    dd if=/dev/zero of="$root$swap_file" bs=1M count="$SWAP_FILE_MIB_NEON" status=none
    chmod 600 "$root$swap_file"
    mkswap -q "$root$swap_file"
fi
# ext2 and NILFS2 have no fsck worth running at boot.
[[ $root_fs == nilfs2 ]] && sed -i 's| / nilfs2 defaults 0 1| / nilfs2 defaults 0 0|' "$root/etc/fstab"
# distinst's crypttab.  Check (E1): its exact line (options, UUID= or the
# device path).  "initramfs" for the build only: cryptsetup-initramfs's hook
# then takes the volume whatever the chroot lets it trace from / to
# cryptdata.  The plain line goes in after the initramfs is built.
crypttab=""
if [[ $luks == yes ]]; then
    crypttab="cryptdata UUID=$(uuid "$luks_part") none luks"
    echo "$crypttab,initramfs" >"$root/etc/crypttab"
fi

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

# neon's remove list, from its Calamares: the packages module's (packages.conf:
# calamares, neon-live, casper and '^live-*', an apt regex, so every package
# whose name starts with "live"), then neon-fs-cleanup's, the file system tools
# for the file systems the install doesn't use (btrfs, reiserfs, XFS).  Only
# the installed ones are removed, below; the autoremove after it is
# neon-fs-cleanup's.
if [[ $distro == neon ]]; then
    { printf '%s\n' calamares neon-live casper
      in_root dpkg-query -W -f '${Package}\n' | grep -E '^live-*' || true
      for t in btrfs:btrfs-progs reiserfs:reiserfsprogs xfs:xfsprogs; do
          [[ $root_fs == "${t%%:*}" ]] || echo "${t#*:}"
      done; } >"$tmp/remove"
fi
if [[ -s ${tmp}/remove ]]; then
    mapfile -t live_pkgs < <(awk '{ print $1 }' "$tmp/remove" | sed 's/:.*//' | sort -u)
    mapfile -t live_pkgs < <(in_root dpkg-query -W -f '${db:Status-Status} ${Package}\n' "${live_pkgs[@]}" 2>/dev/null |
        awk '$1 == "installed" { print $2 }')
    # The UEFI boot packages stay where they boot the guest (grub-shim): the
    # remove list names them so that a BIOS install drops them, and a UEFI
    # install with GRUB keeps them.
    if [[ $loader == grub-shim ]]; then
        keep=()
        for p in "${live_pkgs[@]}"; do
            [[ $p =~ ^(shim-signed|grub-efi-amd64(-signed|-bin)?|mokutil)$ ]] || keep+=("$p")
        done
        live_pkgs=("${keep[@]}")
    fi
    echo "Removing ${#live_pkgs[@]} live-session packages"
    if [[ ${#live_pkgs[@]} -gt 0 ]]; then
        # Ubuntu marks the UEFI boot packages (shim-signed, grub-efi-amd64-signed,
        # mokutil) protected; on a BIOS install the distribution's own installer
        # removes them too (grub-shim keeps them, above; Pop!_OS's systemd-boot
        # install isn't expected to see them).  Anything else protected stops
        # the build.
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
if [[ $distro == neon ]]; then
    gone=$(in_root apt-get -s --purge autoremove 2>/dev/null | awk '$1 == "Purg" { print $2 }' | paste -sd ' ' -)
    echo "Autoremoving: ${gone:-nothing}"
    in_root apt-get -y -q --purge autoremove >/dev/null
fi

extra=(openssh-server)
case $root_fs in f2fs) extra+=(f2fs-tools) ;; nilfs2) extra+=(nilfs-tools) ;; esac
if [[ $firmware == bios ]]; then extra+=(grub-pc); fi
# The rest only where the image lacks them, so the image's own packages keep
# their versions and apt's marks.  GRUB through shim: Ubuntu's signed boot
# chain; distinst's layout: LVM, and cryptsetup's initramfs part for --luks.
want=()
# neon's Calamares installs only grub-efi-amd64-signed and shim-signed
# (before_bootloader_context.conf, "apt install --no-upgrade"): not
# grub-efi-amd64, which would replace the image's grub-pc, so neon keeps it.
if [[ $distro == neon ]]; then want+=(grub-efi-amd64-signed shim-signed mokutil efibootmgr)
elif [[ $loader == grub-shim ]]; then want+=(grub-efi-amd64 grub-efi-amd64-signed shim-signed mokutil efibootmgr); fi
if [[ $layout == distinst ]]; then want+=(lvm2); fi
# Pop's and Zorin's live systems list fdisk (fdisk, sfdisk, cfdisk) among
# their live-only packages (Pop's ethtool too), which a real install keeps: a
# restore by hand wants sfdisk.  The 2026-11-02 rebuild lacked them (pop24
# until its first update; zorin18 still on 2026-11-14).
if [[ $distro == pop || $distro == zorin ]]; then want+=(fdisk ethtool); fi
if [[ $luks == yes ]]; then want+=(cryptsetup cryptsetup-initramfs); fi
for p in "${want[@]}"; do
    [[ $(in_root dpkg-query -W -f '${db:Status-Status}' "$p" 2>/dev/null) == installed ]] || extra+=("$p")
done
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
    # casper itself, where the remove list left it (Mint's): it runs at every
    # shutdown.  Purged if nothing else goes with it.
    if in_root dpkg -s casper >/dev/null 2>&1 &&
        [[ $(in_root apt-get -s purge casper 2>/dev/null | grep -c '^Purg ') == 1 ]]; then
        in_root apt-get -y -q purge casper >/dev/null
    fi
fi

# The initramfs must be able to mount / (F2FS and NILFS2 aren't in it by default).
case $root_fs in f2fs|nilfs2) grep -qx "$root_fs" "$root/etc/initramfs-tools/modules" ||
    echo "$root_fs" >>"$root/etc/initramfs-tools/modules" ;; esac

# The bootloader.  ttyS0 is the last console=, so it is /dev/console, where
# the initramfs asks for a LUKS passphrase (no "splash": Plymouth would take
# the prompt).
cmdline="console=tty0 console=ttyS0,115200n8"
if [[ $loader == grub-pc ]]; then
    sed -i "s/^GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT=\"$cmdline\"/" "$root/etc/default/grub"
    in_root update-initramfs -c -k all >/dev/null 2>&1 || in_root update-initramfs -u -k all >/dev/null
    in_root grub-install --target=i386-pc "$dev"
    in_root update-grub 2>/dev/null
elif [[ $loader == grub-shim ]]; then
    sed -i "s/^GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT=\"$cmdline\"/" "$root/etc/default/grub"
    case $distro in
        elementary)
            # distinst appends this on every UEFI GRUB install.  Its own
            # grub-install, with GRUB on the ESP, comes after the shared step
            # below.
            grep -qx 'GRUB_ENABLE_CRYPTODISK=y' "$root/etc/default/grub" ||
                echo "GRUB_ENABLE_CRYPTODISK=y" >>"$root/etc/default/grub" ;;
        neon)
            # Calamares's boot_deploy and boot_reconfigure: the squashfs has
            # the kernel's modules and initrd but no /boot/vmlinuz-KVER (casper
            # keeps the kernel beside it, as casper/vmlinuz), so it comes from
            # the ISO, and its package's postinst runs again for the symlinks
            # and hooks, without an initramfs (INITRD=No; update-initramfs
            # below makes it).
            kvers=("$root"/usr/lib/modules/*/)
            [[ ${#kvers[@]} -eq 1 && -d ${kvers[0]} ]] ||
                die "the image has ${#kvers[@]} kernels in /usr/lib/modules; neon's live image has one"
            kver=$(basename "${kvers[0]}")
            # xorriso restores the file's timestamps, as Calamares's cp --preserve=timestamps.
            xorriso -osirrox on -indev "$iso" -extract "$casper_dir/vmlinuz" "$root/boot/vmlinuz-$kver" 2>/dev/null ||
                die "$iso has no $casper_dir/vmlinuz"
            chmod 644 "$root/boot/vmlinuz-$kver"
            chown 0:0 "$root/boot/vmlinuz-$kver"
            echo "boot/vmlinuz-$kver: copied from $casper_dir/vmlinuz"
            in_root env INITRD=No dpkg-reconfigure -fnoninteractive "linux-image-$kver" >/dev/null
            # Calamares's grubcfg (overwrite: false, on an existing
            # /etc/default/grub): GRUB_DISTRIBUTOR becomes the branding's
            # bootloaderEntryName, "${PRETTY_NAME}".  grubcfg.conf's default
            # GRUB_ENABLE_CRYPTODISK=true isn't applied: grubcfg only takes
            # its defaults with overwrite or always_use_defaults, or with no
            # /etc/default/grub (and sets it to "y" itself for an encrypted /).
            pretty=$(. "$root/usr/lib/os-release" && echo "$PRETTY_NAME")
            sed -i "s/^GRUB_DISTRIBUTOR=.*/GRUB_DISTRIBUTOR='$pretty'/" "$root/etc/default/grub"
            grep -q '^GRUB_DISTRIBUTOR=' "$root/etc/default/grub" ||
                echo "GRUB_DISTRIBUTOR='$pretty'" >>"$root/etc/default/grub"
            ;;
    esac
    in_root update-initramfs -c -k all >/dev/null 2>&1 || in_root update-initramfs -u -k all >/dev/null
    initrd=$(find "$root/boot" -maxdepth 1 -name 'initrd.img-*' ! -name '*.old-dkms' | sort -V | tail -n 1)
    [[ -n $initrd ]] || die "no initramfs in /boot"
    need=()
    if [[ $layout == distinst ]]; then need+=(sbin/lvm); fi
    if [[ $luks == yes ]]; then need+=(cryptroot/crypttab); fi
    for f in "${need[@]}"; do
        # grep reads it all: grep -q would stop lsinitramfs's zstd with SIGPIPE.
        in_root lsinitramfs "/boot/${initrd##*/}" | grep "$f\$" >/dev/null || die "${initrd##*/} has no $f"
    done
    # No NVRAM entry: the firmware's fallback path finds the guest.  On a
    # fresh variable store OVMF boots EFI/BOOT/BOOTX64.EFI, which is shim;
    # shim runs fbx64.efi beside it, which makes the boot entry from
    # EFI/ID/BOOTX64.CSV and boots EFI/ID/shimx64.efi, as on a real disk
    # moved to a new machine.
    in_root grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id="$efi_id" \
        --uefi-secure-boot --no-nvram
    in_root update-grub 2>/dev/null
    grep -q '^[[:space:]]*linux[[:space:]]' "$root/boot/grub/grub.cfg" || die "/boot/grub/grub.cfg boots no kernel"
    efi=$root/boot/efi/EFI
    for f in shimx64.efi grubx64.efi grub.cfg; do
        [[ -s $efi/$efi_id/$f ]] || die "grub-install put no EFI/$efi_id/$f on the EFI system partition"
    done
    # Ubuntu's grub-install normally puts the fallback path in place too;
    # whatever it left out comes from the packages, and the log says so.
    mkdir -p "$efi/BOOT"
    if ! cmp -s "$efi/$efi_id/shimx64.efi" "$efi/BOOT/BOOTX64.EFI"; then
        echo "EFI/BOOT/BOOTX64.EFI: shim, copied from EFI/$efi_id"
        cp "$efi/$efi_id/shimx64.efi" "$efi/BOOT/BOOTX64.EFI"
    fi
    # The signed helpers first: under Secure Boot shim runs only what its
    # vendor key (Canonical's) signed.  Paths as the guest sees them, so
    # absolute symlinks in the image resolve inside it.  MokManager is only
    # needed to change the MOK list, so it may be missing.
    for f in fbx64.efi mmx64.efi; do
        [[ -s $efi/BOOT/$f ]] && continue
        src=""
        for s in "/boot/efi/EFI/$efi_id/$f" "/usr/lib/shim/$f.signed" "/usr/lib/shim/$f"; do
            if in_root test -s "$s"; then src=$s; break; fi
        done
        if [[ -z $src ]]; then
            [[ $f == mmx64.efi ]] && continue
            die "no $f to put in EFI/BOOT (shim-signed, shim-helpers-amd64-signed)"
        fi
        echo "EFI/BOOT/$f: copied from $src"
        in_root cp -L "$src" "/boot/efi/EFI/BOOT/$f"
    done
    if [[ ! -s $efi/$efi_id/BOOTX64.CSV ]]; then
        in_root test -s /usr/lib/shim/BOOTX64.CSV || die "no BOOTX64.CSV for EFI/$efi_id (shim)"
        echo "EFI/$efi_id/BOOTX64.CSV: copied from /usr/lib/shim"
        in_root cp -L /usr/lib/shim/BOOTX64.CSV "/boot/efi/EFI/$efi_id/BOOTX64.CSV"
    fi
    case $distro in
        elementary)
            # Last, what distinst's bootloader step does (pop-os/distinst
            # src/installer/steps/bootloader.rs), so the ESP ends as a fresh
            # elementary install's: GRUB's modules and a grub.cfg on the ESP,
            # under EFI/ubuntu/grub.  The stub EFI/ubuntu/grub.cfg names the
            # --boot-directory of whichever grub-install ran last, so this one
            # decides which grub.cfg boots.  /boot/grub from update-grub stays
            # as well: package postinsts and kernel updates write it on a real
            # system.
            in_root grub-install --target=x86_64-efi --efi-directory=/boot/efi \
                --boot-directory=/boot/efi/EFI/ubuntu --bootloader-id=ubuntu --no-nvram --recheck
            in_root grub-mkconfig -o /boot/efi/EFI/ubuntu/grub/grub.cfg 2>/dev/null
            grep -q '^[[:space:]]*linux[[:space:]]' "$efi/ubuntu/grub/grub.cfg" ||
                die "EFI/ubuntu/grub/grub.cfg boots no kernel"
            echo "EFI/ubuntu/grub.cfg (the stub) points to:"
            grep -E '^[[:space:]]*(search|set[[:space:]]+prefix|configfile)' "$efi/ubuntu/grub.cfg" | sed 's/^/    /' ||
                echo "    (no search, prefix or configfile line)"
            ;;
        neon)
            # Calamares's after_bootloader: the stub grub.cfg also in
            # EFI/ubuntu, where Ubuntu's signed GRUB (prefix /EFI/ubuntu built
            # in) may look for it.
            mkdir -p "$efi/ubuntu"
            cp "$efi/neon/grub.cfg" "$efi/ubuntu/"
            echo "EFI/ubuntu/grub.cfg: copied from EFI/neon"
            ;;
    esac
    if [[ -n $crypttab ]]; then echo "$crypttab" >"$root/etc/crypttab"; fi
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
    # kernelstub leaves bootctl's loader.conf without a default, and
    # systemd-boot then boots the last entry by name, Pop_OS-oldkern: the
    # kernel whose modules an update purges.
    l=$root/boot/efi/loader/loader.conf
    grep -qE '^[[:space:]]*default[[:space:]]' "$l" || echo "default Pop_OS-current" >>"$l"
fi

rm -f "$root/usr/sbin/policy-rc.d" "$root/etc/resolv.conf"
[[ -e $tmp/resolv.conf.image || -L $tmp/resolv.conf.image ]] && mv "$tmp/resolv.conf.image" "$root/etc/resolv.conf"
in_root apt-get clean
sync
unmount_all
close_lvm
losetup -d "$dev"
dev=""
# Sparse in, sparse out: qcow2 stores only what was written.
qemu-img convert -f raw -O qcow2 "$raw" "$disk"
cleanup
trap - EXIT

qemu-img snapshot -c fresh-install "$disk"
chown "$owner": "$disk" root-password id_ed25519 id_ed25519.pub
if [[ $luks == yes ]]; then chown "$owner": console-passphrase; fi
# Secure Boot on where the guest boots through Ubuntu's signed shim; off for
# Pop!_OS, whose systemd-boot isn't signed.
case $loader in
    systemd-boot) echo "--uefi --no-secure-boot" >vm-run-options ;;
    grub-shim) echo "--uefi" >vm-run-options ;;
esac
# neon's Plasma desktop (its services, even without the session) wants more
# than vm-run's 2 GB.
if [[ $distro == neon ]]; then echo "--uefi --memory 4096" >vm-run-options; fi
if [[ $firmware == uefi ]]; then chown "$owner": vm-run-options; fi
echo "Done: $disk ($(du -h "$disk" | cut -f1) on disk), snapshot \"fresh-install\""
