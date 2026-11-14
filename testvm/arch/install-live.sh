#!/bin/bash
# An Arch-based desktop distribution's test guest for BackOtter, installed
# from its own live ISO the way its Calamares does an offline install on a
# blank disk, with that installer's defaults, unattended.  Run by make-arch.sh
# --live, which fills in the @...@ settings and serves this to the Arch ISO's
# live system (archiso's script= boot parameter), with the distribution's ISO
# as the second CD.  Prints BACKOTTER-INSTALL-OK or -FAILED, then powers off.
#
#   endeavouros  GPT; 2 GiB ESP at /efi; / ext4 (noatime); no swap; the
#                airootfs copied and cleaned as chrooted_cleaner_script.sh
#                does; systemd-boot, and dracut through kernel-install.
#   endeavouros-grub  the same with the installer's other choice of loader:
#                GRUB, with eos-dracut's initramfs in /boot.
#   manjaro      GPT; 300 MiB ESP at /boot/efi; / btrfs with @, @home, @cache
#                and @log (compress=zstd:1); no swap; rootfs and desktopfs
#                copied; GRUB and mkinitcpio.  mhwd's drivers aren't installed.
#   garuda       GPT; 300 MiB ESP at /boot/efi; / btrfs with @, @home, @root,
#                @srv, @cache, @log and @tmp (noatime,compress=zstd); no swap;
#                rootfs and desktopfs copied; GRUB, and dracut through
#                garuda-dracut-support, after postcfg removes the other CPU
#                vendor's microcode; snapper's root config and /.snapshots.
#                ghtcfg's drivers (garuda-hardware-tool, from ghtfs) aren't
#                installed.
#   biglinux     GPT; 300 MiB ESP at /boot/efi; / btrfs with @ and @home only
#                (noatime,compress-force=zstd, and discard=async on an SSD or
#                autodefrag on a disk); no swap; rootfs and desktopfs copied
#                with unpackfs's rsync; mkinitcpio with initcpiocfg's hooks;
#                GRUB as EFI/BigLinux; /boot and /usr/share/grub rewritten
#                uncompressed (btrfs-fix).  big-first-boot stays enabled, for
#                the first boot.  mhwd's drivers (mhwdfs) aren't installed.
#
# The guest boots to text mode with a serial console; root and "tester" share
# a generated password, and root accepts an SSH key.

set -euo pipefail
exec > >(tee /dev/ttyS0) 2>&1
trap 'echo "BACKOTTER-INSTALL-FAILED at line $LINENO"; sync; sleep 2; poweroff' ERR

FLAVOR='@FLAVOR@'
DISTRO=${FLAVOR%-grub}
GUEST_NAME='@HOSTNAME@'
ROOT_PASSWORD_HASH='@ROOT_PASSWORD_HASH@'
AUTHORIZED_KEY='@AUTHORIZED_KEY@'
DISK=/dev/vda
CONSOLE='console=tty0 console=ttyS0,115200n8'

echo "BACKOTTER-INSTALL-START ($FLAVOR)"
until systemctl is-active -q pacman-init.service; do sleep 1; done
mkdir -p /live /layer
mount -o ro /dev/sr1 /live

# The disk, and the file systems mounted where the installer mounts them.
wipefs -q -a "$DISK"
case $DISTRO in
    endeavouros) esp_size=2GiB esp_mp=/efi esp_opts=fmask=0137,dmask=0027 ;;
    manjaro) esp_size=300MiB esp_mp=/boot/efi esp_opts=defaults,umask=0077 ;;
    # partition.conf sets no ESP size, so Calamares's default 300 MiB.
    garuda) esp_size=300MiB esp_mp=/boot/efi esp_opts=defaults,umask=0077 ;;
    # The same: no size in partition.conf; mount.conf's efi options.
    biglinux) esp_size=300MiB esp_mp=/boot/efi esp_opts=defaults,umask=0077 ;;
    *) echo "unknown DISTRO $DISTRO"; false ;;
esac
sfdisk -q "$DISK" <<PARTS
label: gpt
size=$esp_size, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name=EFI
type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name=$DISTRO
PARTS
udevadm settle
esp=${DISK}1 root=${DISK}2
mkfs.vfat -F 32 -n EFI "$esp" >/dev/null
if [[ $DISTRO == endeavouros ]]; then
    mkfs.ext4 -q -F "$root"
    mount -t ext4 -o noatime "$root" /mnt
elif [[ $DISTRO == garuda ]]; then
    # mount.conf's btrfsSubvolumes (mount point:subvolume), / first; each
    # mounted with its btrfs mountOptions.
    garuda_subvols="/:@ /home:@home /root:@root /srv:@srv /var/cache:@cache /var/log:@log /var/tmp:@tmp"
    mkfs.btrfs -q -f "$root"
    mount -t btrfs "$root" /mnt
    for m in $garuda_subvols; do btrfs -q subvolume create "/mnt/${m#*:}"; done
    umount /mnt
    btrfs_opts=noatime,compress=zstd
    for m in $garuda_subvols; do
        mkdir -p "/mnt${m%%:*}"
        mount -t btrfs -o "subvol=/${m#*:},$btrfs_opts" "$root" "/mnt${m%%:*}"
    done
elif [[ $DISTRO == biglinux ]]; then
    # mount.conf's btrfsSubvolumes, @ and @home only, and its btrfs options,
    # with ssdOptions or hddOptions as the disk's rotational flag says.
    mkfs.btrfs -q -f "$root"
    mount -t btrfs "$root" /mnt
    for v in @ @home; do btrfs -q subvolume create "/mnt/$v"; done
    umount /mnt
    rotational=$(cat "/sys/block/${DISK#/dev/}/queue/rotational")
    if [[ $rotational == 0 ]]; then
        btrfs_opts=defaults,noatime,compress-force=zstd,discard=async
    else
        btrfs_opts=defaults,noatime,compress-force=zstd,autodefrag
    fi
    echo "BACKOTTER-INFO: ${DISK#/dev/}'s rotational is $rotational, so btrfs mounts with $btrfs_opts"
    mount -t btrfs -o "subvol=/@,$btrfs_opts" "$root" /mnt
    mkdir -p /mnt/home
    mount -t btrfs -o "subvol=/@home,$btrfs_opts" "$root" /mnt/home
else
    mkfs.btrfs -q -f "$root"
    mount -t btrfs "$root" /mnt
    for v in @ @home @cache @log; do btrfs -q subvolume create "/mnt/$v"; done
    umount /mnt
    btrfs_opts=compress=zstd:1
    mount -t btrfs -o "subvol=/@,$btrfs_opts" "$root" /mnt
    mkdir -p /mnt/home /mnt/var/cache /mnt/var/log
    mount -t btrfs -o "subvol=/@home,$btrfs_opts" "$root" /mnt/home
    mount -t btrfs -o "subvol=/@cache,$btrfs_opts" "$root" /mnt/var/cache
    mount -t btrfs -o "subvol=/@log,$btrfs_opts" "$root" /mnt/var/log
fi

# The live system's images.
case $DISTRO in
    endeavouros) layers=(/live/arch/x86_64/airootfs.sfs) ;;
    manjaro) layers=(/live/manjaro/x86_64/rootfs.sfs /live/manjaro/x86_64/desktopfs.sfs) ;;
    # unpackfs's two; ghtfs.sfs and livefs.sfs stay on the ISO.
    garuda) layers=(/live/garuda/x86_64/rootfs.sfs /live/garuda/x86_64/desktopfs.sfs) ;;
    # The two that BigLinux's pre-installer writes into unpackfs.conf (the
    # profile's own names manjaro/x86_64/rootfs.sfs, which isn't on the ISO);
    # mhwdfs.sfs and livefs.sfs stay on the ISO.
    biglinux) layers=(/live/biglinux/x86_64/rootfs.sfs /live/biglinux/x86_64/desktopfs.sfs) ;;
esac
for l in "${layers[@]}"; do
    echo "Copying $l"
    mount -t squashfs -o ro,loop "$l" /layer
    if [[ $DISTRO == biglinux ]]; then
        # unpackfs's own rsync: desktopfs's overlayfs xattrs (921 files)
        # stay behind; its whiteouts (character devices 0,0) come along,
        # except where rootfs has a non-empty directory (photos in
        # /usr/share/plasma/avatars): rsync leaves that directory and exits
        # 23, which unpackfs takes as success.
        rsync -aHAXSr --filter='-x trusted.overlay.*' /layer/ /mnt/ || {
            rc=$?
            [[ $rc == 23 ]] || false
            echo "BACKOTTER-INFO: rsync exit 23 for $l, which unpackfs allows"
        }
    else
        cp -a --remove-destination /layer/. /mnt/
    fi
    umount /layer
done
mkdir -p "/mnt$esp_mp"
mount -t vfat -o "$esp_opts" "$esp" "/mnt$esp_mp"

uuid() { blkid -s UUID -o value "$1"; }
{
    echo "# Written by BackOtter's arch/install-live.sh, as Calamares writes it"
    echo "UUID=$(uuid "$esp") $esp_mp vfat $esp_opts 0 2"
    if [[ $DISTRO == endeavouros ]]; then
        echo "UUID=$(uuid "$root") / ext4 noatime 0 1"
    elif [[ $DISTRO == garuda ]]; then
        for m in $garuda_subvols; do
            echo "UUID=$(uuid "$root") ${m%%:*} btrfs subvol=/${m#*:},defaults,$btrfs_opts 0 0"
        done
    elif [[ $DISTRO == biglinux ]]; then
        # btrfs_opts begins with mount.conf's "defaults".
        echo "UUID=$(uuid "$root") / btrfs subvol=/@,$btrfs_opts 0 0"
        echo "UUID=$(uuid "$root") /home btrfs subvol=/@home,$btrfs_opts 0 0"
    else
        for m in /:@ /home:@home /var/cache:@cache /var/log:@log; do
            echo "UUID=$(uuid "$root") ${m%%:*} btrfs subvol=/${m#*:},defaults,$btrfs_opts 0 0"
        done
    fi
    # Garuda's Calamares has no fstab.conf, and without one the fstab module
    # adds a tmpfs /tmp only for an SSD; the virtio disk reads as rotational.
    # BigLinux's uses Calamares's fstab.conf, whose tmpOptions say the same.
    if [[ ($DISTRO != garuda && $DISTRO != biglinux) || $(cat "/sys/block/${DISK#/dev/}/queue/rotational") == 0 ]]; then
        echo "tmpfs /tmp tmpfs defaults,noatime,mode=1777 0 0"
    fi
} >/mnt/etc/fstab

# Settings written as files from out here; only tools run in the chroot.
ln -sf /usr/share/zoneinfo/UTC /mnt/etc/localtime
sed -i 's/^#en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /mnt/etc/locale.gen
echo LANG=en_US.UTF-8 >/mnt/etc/locale.conf
echo KEYMAP=us >/mnt/etc/vconsole.conf
echo "$GUEST_NAME" >/mnt/etc/hostname
rm -f /mnt/etc/machine-id
arch-chroot /mnt systemd-machine-id-setup
arch-chroot /mnt locale-gen
arch-chroot /mnt hwclock --systohc || true
arch-chroot /mnt pacman-key --init
if [[ $DISTRO == garuda ]]; then
    # As postcfg does: every keyring in /usr/share/pacman/keyrings (archlinux
    # and chaotic; Garuda has none of its own).
    arch-chroot /mnt pacman-key --populate
elif [[ $DISTRO == biglinux ]]; then
    # shellprocess@initialize_pacman's three, then postcfg's --populate of
    # its keyrings: [], which is every keyring in /usr/share/pacman/keyrings.
    arch-chroot /mnt pacman-key --populate archlinux manjaro biglinux
    arch-chroot /mnt pacman-key --populate
else
    arch-chroot /mnt pacman-key --populate archlinux "$DISTRO"
fi

installed() { arch-chroot /mnt pacman -Qq "$@" 2>/dev/null || true; }
# Garuda's zzz_post.hook leaves "garuda-hooks-runner post_back" running in the
# chroot after pacman returns.  Waits up to 60 s for every process whose root
# is /mnt to end, so that nothing keeps the target busy, then stops those left.
chroot_settle() {
    local i p r left
    for ((i = 0; i <= 70; i++)); do
        left=()
        for p in /proc/[0-9]*; do
            r=$(readlink "$p/root" 2>/dev/null) || continue
            if [[ $r == /mnt || $r == /mnt/* ]]; then left+=("${p#/proc/}"); fi
        done
        if [[ ${#left[@]} -eq 0 ]]; then return 0; fi
        if [[ $i -eq 60 ]]; then
            echo "BACKOTTER-INFO: stopping what still runs in /mnt after 60 s: ${left[*]}"
            kill "${left[@]}" 2>/dev/null || true
        fi
        sleep 1
    done
    echo "processes still running in /mnt: ${left[*]}"
    return 1
}

if [[ $DISTRO == endeavouros ]]; then
    # chrooted_cleaner_script.sh, offline: the live session's packages, then
    # its files.
    arch-chroot /mnt sh -c 'id liveuser >/dev/null 2>&1 && userdel -r liveuser || true'
    mapfile -t remove < <(installed edk2-shell arch-install-scripts net-tools memtest86+ memtest86+-efi \
        mkinitcpio mkinitcpio-archiso mkinitcpio-busybox mkinitcpio-nfs-utils nbd pv syslinux clonezilla \
        fsarchiver gpart gparted gptfdisk grsync ckbcomp)
    mapfile -t -O "${#remove[@]}" remove < <(arch-chroot /mnt pacman -Qq | grep calamares)
    arch-chroot /mnt pacman -Rsn --noconfirm "${remove[@]}"
    arch-chroot /mnt sh -c 'rm -rf /etc/sudoers.d/g_wheel /var/lib/NetworkManager/NetworkManager.state \
        /etc/systemd/system/getty@tty1.service.d /etc/systemd/system/multi-user.target.wants/* \
        /etc/systemd/journald.conf.d /etc/systemd/logind.conf.d /etc/mkinitcpio-archiso.conf /etc/initcpio \
        /etc/motd /gpg.conf /gpg-agent.conf /pubring.gpg /secring.gpg /version /root/* /root/.[!.]* \
        /etc/calamares /opt/extra-drivers; find /usr/lib/initcpio -name "archiso*" -type f -delete 2>/dev/null; true'
    # The ISO's Arch mirrorlist is all comments; eos_pacman ranks mirrors
    # during the install.  One worldwide mirror does the same job here.
    sed -i 's|^#\(Server = https://geo\.mirror\.pkgbuild\.com/\)|\1|' /mnt/etc/pacman.d/mirrorlist
    grep -q '^Server' /mnt/etc/pacman.d/mirrorlist || { echo "no Arch mirror enabled"; false; }
    # eos_bootloader's packages for the chosen loader, from the ISO's own cache.
    if [[ $FLAVOR == endeavouros-grub ]]; then
        pkgs='eos-dracut-*.pkg.tar.zst grub-[0-9]*.pkg.tar.zst'
    else
        pkgs='kernel-install-for-dracut-*.pkg.tar.zst'
    fi
    arch-chroot /mnt sh -c "cd /usr/share/packages && pacman --noconfirm -U $pkgs && cd / && rm -rf /usr/share/packages"
    if [[ $FLAVOR == endeavouros-grub ]]; then
        # eos_bootloader's run_dracut, then Calamares's bootloader module, with
        # its fallback loader (installEFIFallback).
        for m in /mnt/usr/lib/modules/*/pkgbase; do
            cp "${m%/pkgbase}/vmlinuz" "/mnt/boot/vmlinuz-$(cat "$m")"
        done
        arch-chroot /mnt dracut-rebuild
        sed -i "s/^GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT=\"nvme_load=YES nowatchdog $CONSOLE\"/" /mnt/etc/default/grub
        arch-chroot /mnt grub-install --target=x86_64-efi --efi-directory=/efi --bootloader-id=endeavouros --recheck --no-nvram
        install -d /mnt/efi/EFI/boot
        cp /mnt/efi/EFI/endeavouros/grubx64.efi /mnt/efi/EFI/boot/bootx64.efi
        arch-chroot /mnt env GRUB_DISABLE_OS_PROBER=true grub-mkconfig -o /boot/grub/grub.cfg
        compgen -G '/mnt/boot/initramfs-*.img' >/dev/null || { echo "dracut-rebuild made no initramfs"; false; }
        grep -q 'initramfs-linux' /mnt/boot/grub/grub.cfg || { echo "grub.cfg names no initramfs"; false; }
    else
        # Calamares's bootloader module for systemd-boot.
        install -d /mnt/etc/kernel
        echo "nvme_load=YES nowatchdog rw root=UUID=$(uuid "$root") $CONSOLE" >/mnt/etc/kernel/cmdline
        arch-chroot /mnt bootctl --esp-path=/efi --no-variables install
        for m in /mnt/usr/lib/modules/*/vmlinuz; do
            v=${m%/vmlinuz}; v=${v##*/}
            arch-chroot /mnt kernel-install add "$v" "/usr/lib/modules/$v/vmlinuz"
        done
        printf 'timeout 5\nconsole-mode auto\nreboot-for-bitlocker 1\n' >/mnt/efi/loader/loader.conf
        compgen -G '/mnt/efi/loader/entries/*.conf' >/dev/null || { echo "kernel-install made no boot entry"; false; }
        compgen -G '/mnt/efi/*/*/initrd' >/dev/null || { echo "kernel-install made no initrd"; false; }
    fi
elif [[ $DISTRO == garuda ]]; then
    [[ -f /mnt/boot/vmlinuz-linux-garuda ]] || { echo "no /boot/vmlinuz-linux-garuda"; false; }
    # Calamares's users module: tester in its defaultGroups, those the image
    # has (the common step below then finds tester there).
    tester_groups=""
    for g in lp wheel video input realtime; do
        if grep -q "^$g:" /mnt/etc/group; then tester_groups+=,$g; fi
    done
    arch-chroot /mnt useradd -m -s /bin/bash -c "Test user" -G "${tester_groups#,}" tester
    # Calamares's services-systemd: its units enabled, and the live system's
    # pacman-init disabled.  The default target is set below.
    arch-chroot /mnt systemctl enable avahi-daemon.service bluetooth.service ModemManager.service \
        NetworkManager.service systemd-timesyncd.service systemd-oomd.service \
        power-profiles-daemon.service plasmalogin.service
    if [[ -e /mnt/usr/lib/systemd/system/pacman-init.service || -e /mnt/etc/systemd/system/pacman-init.service ]]; then
        arch-chroot /mnt systemctl disable pacman-init.service
    fi
    # Calamares's postcfg, after pacman-key: the other vendor's microcode
    # removed (Calamares asks hwinfo for the vendor), or dracut-rebuild if
    # it's neither.  Removing it fires Garuda's 90-dracut-install.hook (a path
    # trigger on usr/lib/firmware/*), whose dracut-install-garuda rebuilds each
    # kernel's host-only and fallback initramfs here, on the guest's own
    # (virtual) hardware.  snap-pac's hooks can't snapshot without
    # /.snapshots, as in a real install; pacman reports a failed hook that
    # isn't AbortOnFail but still succeeds, so only pacman's own failure stops
    # this.
    stamp=$(mktemp)
    vendor=$(awk -F': ' '/^vendor_id/ { print $2; exit }' /proc/cpuinfo)
    case $vendor in
        AuthenticAMD) ucode=intel-ucode ;;
        GenuineIntel) ucode=amd-ucode ;;
        *) ucode="" ;;
    esac
    if [[ -n $ucode ]]; then
        echo "BACKOTTER-INFO: CPU vendor $vendor, so $ucode is removed"
        arch-chroot /mnt pacman -R --noconfirm "$ucode"
    else
        echo "BACKOTTER-INFO: CPU vendor '$vendor', so no microcode is removed; dracut-rebuild"
        arch-chroot /mnt dracut-rebuild
    fi
    chroot_settle
    for f in initramfs-linux-garuda.img initramfs-linux-garuda-fallback.img; do
        [[ /mnt/boot/$f -nt $stamp ]] || { echo "/boot/$f wasn't rebuilt"; false; }
    done
    # The rest of postcfg: the live skel copied to root's home, then snapper's
    # root config, with /.snapshots a subvolume nested in @.  The image
    # already has /etc/snapper/configs/root, listed in /etc/conf.d/snapper, so
    # create-config refuses (260819: "Creating config failed (subvolume
    # already covered)."); Calamares ignores its exit status.  The config
    # must be there either way.
    cp -a /mnt/etc/skel/. /mnt/root/
    if out=$(arch-chroot /mnt snapper --no-dbus -c root create-config / 2>&1); then
        echo "BACKOTTER-INFO: snapper create-config made the root config"
    else
        echo "BACKOTTER-INFO: snapper create-config refused, as Calamares allows: $out"
    fi
    [[ -s /mnt/etc/snapper/configs/root ]] || { echo "no snapper root config"; false; }
    # create-config makes /.snapshots itself when it succeeds.
    if btrfs subvolume show /mnt/.snapshots >/dev/null 2>&1; then
        echo "BACKOTTER-INFO: /.snapshots made by snapper"
    else
        arch-chroot /mnt btrfs subvolume create /.snapshots
        echo "BACKOTTER-INFO: /.snapshots made by btrfs subvolume create"
    fi
    # Calamares's grubcfg and bootloader modules, with its fallback loader
    # (installEFIFallback).  grub-btrfs's 41_snapshots-btrfs finds no
    # snapshots yet; grub-mkconfig itself must succeed.
    sed -i "s/^GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT=\"loglevel=3 quiet $CONSOLE\"/" /mnt/etc/default/grub
    arch-chroot /mnt grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=Garuda --recheck --no-nvram
    install -d /mnt/boot/efi/EFI/boot
    cp /mnt/boot/efi/EFI/Garuda/grubx64.efi /mnt/boot/efi/EFI/boot/bootx64.efi
    arch-chroot /mnt env GRUB_DISABLE_OS_PROBER=true grub-mkconfig -o /boot/grub/grub.cfg
    grep -Eq 'subvol=/?@([[:space:]]|$)' /mnt/boot/grub/grub.cfg || { echo "grub.cfg doesn't name subvol=@"; false; }
    grep -q 'initramfs-linux-garuda\.img' /mnt/boot/grub/grub.cfg || { echo "grub.cfg names no initramfs-linux-garuda.img"; false; }
elif [[ $DISTRO == biglinux ]]; then
    [[ -f /mnt/boot/vmlinuz-6.18-x86_64 ]] || { echo "no /boot/vmlinuz-6.18-x86_64"; false; }
    # machineid's dbus symlink (the image has it already).
    [[ -L /mnt/var/lib/dbus/machine-id ]] || ln -sf /etc/machine-id /mnt/var/lib/dbus/machine-id
    # initcpiocfg (Calamares's own initcpiocfg.conf: useSystemdHook false,
    # its "bogus" hook added and removed again): the live system's
    # mkinitcpio.conf, rootfs's, with HOOKS rewritten, udev instead of the
    # image's systemd, plymouth because it is installed, no fsck on btrfs;
    # MODULES, FILES and BINARIES emptied.  Then initcpio's mkinitcpio -P,
    # which faster-mkinitcpio's presets make one image each.
    hooks="base udev autodetect microcode kms modconf block keyboard keymap consolefont"
    [[ ! -x /mnt/usr/bin/plymouth ]] || hooks+=" plymouth"
    hooks+=" filesystems"
    sed -i -e "s/^[[:space:]]*HOOKS.*/HOOKS=($hooks)/" -e 's/^[[:space:]]*MODULES.*/MODULES=()/' \
        -e 's/^[[:space:]]*FILES.*/FILES=()/' -e 's/^[[:space:]]*BINARIES.*/BINARIES=()/' /mnt/etc/mkinitcpio.conf
    echo "BACKOTTER-INFO: mkinitcpio.conf: $(grep '^HOOKS' /mnt/etc/mkinitcpio.conf)"
    arch-chroot /mnt mkinitcpio -P
    # users: tester in users.conf's defaultGroups, those missing made first
    # (plain names: Calamares makes them), and sudoersGroup's rule.
    tester_groups=(cdrom floppy audio dip video plugdev netdev lpadmin scanner bluetooth vboxsf
        sambashare input scard lp network power sys wheel)
    for g in "${tester_groups[@]}"; do
        grep -q "^$g:" /mnt/etc/group || arch-chroot /mnt groupadd "$g"
    done
    arch-chroot /mnt useradd -m -s /bin/bash -c "Test user" -G "$(IFS=,; echo "${tester_groups[*]}")" tester
    echo '%wheel ALL=(ALL:ALL) ALL' >/mnt/etc/sudoers.d/10-installer
    chmod 440 /mnt/etc/sudoers.d/10-installer
    # services: services.conf's units, none of them mandatory (so those the
    # image lacks, vboxservice for one, are passed by), less the display
    # managers; pacman-init masked.  graphical.target's set-default is
    # multi-user.target below: the guest runs in text mode.
    for u in bluetooth cronie ModemManager NetworkManager cups fstrim.timer pkgfile-update.timer vboxservice \
        ufw nmb smb dnsmasq cups-browsed; do
        arch-chroot /mnt systemctl enable "$u" 2>/dev/null || echo "BACKOTTER-INFO: $u not enabled: the image hasn't it"
    done
    arch-chroot /mnt systemctl mask pacman-init.service
    # grubcfg: GRUB_DISTRIBUTOR (BigLinux, the image's already), its defaults
    # for keys the file lacks, and kernel_params "quiet", plus "splash" as
    # plymouth is installed.
    f=/mnt/etc/default/grub
    grep -q '^GRUB_DISTRIBUTOR="BigLinux"' $f || { echo "/etc/default/grub's GRUB_DISTRIBUTOR isn't BigLinux"; false; }
    grep -q '^GRUB_DISABLE_SUBMENU=' $f || echo 'GRUB_DISABLE_SUBMENU=true' >>$f
    grep -q '^GRUB_TERMINAL_OUTPUT=' $f || echo 'GRUB_TERMINAL_OUTPUT="console"' >>$f
    # bootloader: grub-install, with its fallback loader (installEFIFallback).
    arch-chroot /mnt grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=BigLinux --recheck --no-nvram
    install -d /mnt/boot/efi/EFI/boot
    cp /mnt/boot/efi/EFI/BigLinux/grubx64.efi /mnt/boot/efi/EFI/boot/bootx64.efi
    # shellprocess@grub_crypt only themes a LUKS prompt (|| true: the image
    # has no such script).  grubcfg-fix's biglinux-install-setup.sh: the
    # live kernel's arguments (kernels.cfg's first entry, less its live-only
    # ones and quiet and splash) after the file's own, GRUB_SAVEDEFAULT=false,
    # GRUB_EARLY_INITRD_LINUX_STOCK='' and SDDM's last session (Wayland, as
    # that entry says wayland); then the serial console here.  Calamares runs
    # it after the bootloader module's grub-mkconfig, so that a real
    # install's first grub.cfg has neither; here grub-mkconfig comes after,
    # so that grub.cfg has the console.
    args=(quiet splash)
    for a in $(awk '$1 == "linux" { $1 = $2 = ""; print; exit }' /live/boot/grub/kernels.cfg); do
        case $a in
            BOOT_IMAGE=* | initrd=* | misobasedir=* | misolabel=* | driver=* | rdinit=* | biglinux.bootcmd=* | \
                sshenable | wayland | rw | ro | quiet | splash) continue ;;
        esac
        [[ " ${args[*]} " == *" $a "* ]] || args+=("$a")
    done
    [[ ${#args[@]} -gt 2 ]] || { echo "no live kernel arguments in the ISO's boot/grub/kernels.cfg"; false; }
    sed -i "s|^GRUB_CMDLINE_LINUX_DEFAULT=.*|GRUB_CMDLINE_LINUX_DEFAULT=\"${args[*]} $CONSOLE\"|" $f
    if grep -q '^GRUB_SAVEDEFAULT=' $f; then
        sed -i 's/^GRUB_SAVEDEFAULT=.*/GRUB_SAVEDEFAULT=false/' $f
    else
        echo 'GRUB_SAVEDEFAULT=false' >>$f
    fi
    grep -q '^GRUB_EARLY_INITRD_LINUX_STOCK=' $f || echo "GRUB_EARLY_INITRD_LINUX_STOCK=''" >>$f
    install -d /mnt/var/lib/sddm /mnt/etc/big-default-config
    printf '[Last]\nSession=/usr/share/wayland-sessions/plasmawayland.desktop\n' >/mnt/var/lib/sddm/state.conf
    # grub-btrfs's 41_snapshots-btrfs runs in the chroot here and finds no
    # snapshots yet; grub-mkconfig itself must succeed.
    arch-chroot /mnt env GRUB_DISABLE_OS_PROBER=true grub-mkconfig -o /boot/grub/grub.cfg
    # postcfg: the skel in root's home, the kernels rewritten by dd, GRUB's
    # boot flags (grub-set-bootflag is installed), Calamares removed if the
    # image has it, /root 0750.  Its pacman-mirrors -f3 and pacman -Syy are for
    # an install with a network; this one is offline, as manjaro's.
    cp -a /mnt/etc/skel/. /mnt/root/
    arch-chroot /mnt sh -c 'mkdir -p /tmp/vmlinuz-hack && mv /boot/vmlinuz-* /tmp/vmlinuz-hack/ &&
        for k in /tmp/vmlinuz-hack/*; do dd if="$k" of="/boot/${k##*/}" status=none || exit 1; done'
    if [[ -d /mnt/usr/lib/plasma-setup ]]; then
        arch-chroot /mnt systemd-sysusers
        arch-chroot /mnt systemctl enable plasma-setup.service
    fi
    [[ ! -e /mnt/usr/bin/grub-set-bootflag ]] || arch-chroot /mnt grub-editenv - set menu_auto_hide=1 boot_success=1
    [[ ! -e /mnt/usr/bin/calamares ]] || arch-chroot /mnt pacman -R --noconfirm calamares
    [[ $(stat -c %a /mnt/root) != 755 ]] || chmod 750 /mnt/root
    # btrfs-fix: GRUB's files rewritten uncompressed (it runs from the live
    # system on the target's paths; -r stays on the one file system, so not
    # into the ESP).
    for d in usr/share/grub boot; do
        btrfs filesystem defragment --nocomp -r "/mnt/$d"
    done
    grep -Eq 'subvol=/?@([[:space:]]|$)' /mnt/boot/grub/grub.cfg || { echo "grub.cfg doesn't name subvol=@"; false; }
    echo "BACKOTTER-INFO: grub.cfg: $(grep -Eo 'rootflags=subvol=[^[:space:]]*' /mnt/boot/grub/grub.cfg | sort -u | tr '\n' ' ')"
    [[ -f /mnt/boot/initramfs-6.18-x86_64.img ]] || { echo "no /boot/initramfs-6.18-x86_64.img"; false; }
    ! compgen -G '/mnt/boot/*-fallback.img' >/dev/null || { echo "a fallback initramfs: $(ls /mnt/boot/*-fallback.img)"; false; }
    [[ -f /mnt/boot/efi/EFI/BigLinux/grubx64.efi ]] || { echo "no EFI/BigLinux/grubx64.efi"; false; }
    systemctl --root=/mnt is-enabled -q big-first-boot.service || { echo "big-first-boot.service isn't enabled"; false; }
else
    # Calamares's initcpio, grubcfg and bootloader modules, with its fallback
    # loader (installEFIFallback).
    compgen -G '/mnt/boot/vmlinuz-*' >/dev/null || { echo "no kernel in /boot"; false; }
    arch-chroot /mnt mkinitcpio -P
    sed -i "s/^GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT=\"quiet udev.log_priority=3 $CONSOLE\"/" /mnt/etc/default/grub
    arch-chroot /mnt grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=manjaro --recheck --no-nvram
    install -d /mnt/boot/efi/EFI/boot
    cp /mnt/boot/efi/EFI/manjaro/grubx64.efi /mnt/boot/efi/EFI/boot/bootx64.efi
    arch-chroot /mnt env GRUB_DISABLE_OS_PROBER=true grub-mkconfig -o /boot/grub/grub.cfg
    grep -q 'subvol=' /mnt/boot/grub/grub.cfg || { echo "grub.cfg names no btrfs subvolume"; false; }
fi
grep -Eq '^ID="?'"$DISTRO"'"?$' /mnt/usr/lib/os-release || { echo "os-release doesn't say $DISTRO"; false; }

# SSH, users, and text mode.
[[ -n $(installed openssh) ]] || arch-chroot /mnt pacman -Sy --needed --noconfirm openssh
printf 'root:%s\n' "$ROOT_PASSWORD_HASH" | arch-chroot /mnt chpasswd -e
arch-chroot /mnt sh -c 'id tester >/dev/null 2>&1 || useradd -m -s /bin/bash -c "Test user" -G wheel tester'
printf 'tester:%s\n' "$ROOT_PASSWORD_HASH" | arch-chroot /mnt chpasswd -e
install -d -m 700 /mnt/root/.ssh
echo "$AUTHORIZED_KEY" >/mnt/root/.ssh/authorized_keys
chmod 600 /mnt/root/.ssh/authorized_keys
arch-chroot /mnt systemctl enable NetworkManager.service sshd.service fstrim.timer
arch-chroot /mnt systemctl set-default multi-user.target
yes | arch-chroot /mnt pacman -Scc >/dev/null || true
# The openssh install above, if any, ran Garuda's hooks too.
[[ $DISTRO != garuda ]] || chroot_settle

umount -R /mnt
umount /live
sync
echo "BACKOTTER-INSTALL-OK"
poweroff
