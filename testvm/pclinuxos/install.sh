#!/bin/bash
# PCLinuxOS's installer, mylive-install 1.0.26 (installer.py), replayed
# without its GTK front end: docs/design/pclinuxos.md, Decision 1.  The same
# steps in the same order with the same files, from the ISO's own
# livecd.sqfs, onto one whole disk.  Used by make-bootstrap.sh (bootstrap/
# pclinuxos.sh: the pclinuxos and pclinuxos-bios guests, from the Arch live
# system) and, later, by realworld/pclinuxos-bootstrap.sh (the A100, from
# the rescue stick).  Runs as root on any Linux live system with overlayfs,
# squashfs and loop devices; every tool it runs is the ISO's own (parted,
# mkfs, rsync, blkid in a chroot of the live image; grub2-install, mkinitrd
# and chkconfig in a chroot of the new system), as in the live session.
#
# Usage: install.sh [OPTIONS] DISK LAYOUT ISO
#   DISK    the whole disk, erased (/dev/vda, /dev/nvme0n1)
#   LAYOUT  auto    the installer's Automated layout: on UEFI (--firmware uefi)
#                   GPT, ESP vfat 300 MB at /boot/EFI, swap 1024 MB, / ext4,
#                   and a separate ext4 /home on disks of 100 GB or more (the
#                   installer's dualpart_minsize, with its default size);
#                   on BIOS an msdos label with swap and / only
#           manual-xfs  what Manual mode gives on BIOS (msdos) when asked
#                   for /boot ext4 1 GiB, swap 1024 MB and / XFS for the rest
#   ISO     pclinuxos64-kde-2026.11.iso, or a block device holding it (/dev/sr1)
# Options:
#   --firmware uefi|bios  what partitioning.is_efi_supported() would say
#                   (default: uefi if /sys/firmware/efi exists)
#   --no-nvram      UEFI from a BIOS-booted VM (make-bootstrap.sh): instead of
#                   the installer's bare "grub2-install", which needs efivars,
#                   grub2-install --target=x86_64-efi --efi-directory=/boot/EFI
#                   --bootloader-id=pclinuxos --no-nvram, then the same with
#                   --removable for EFI/BOOT/BOOTX64.EFI (no NVRAM entry)
#   --hostname H    the installer's host name field (default localhost)
#   --user U        the user the installer adds (default tester, "Test user")
#   --password-hash HASH  crypt hash for the user and root (the GUI's
#                   "same password for root"); default: both locked
#   --timezone Z    default UTC;  keyboard us/pc104, language en_US
#   --rpm FILE SHA256  an extra RPM (openssh-server, Decision 5), checked
#                   against SHA256 and installed with rpm -i after the
#                   installer's steps; repeatable
#   --authorized-key KEY  root's authorized_keys (bko's addition)
#   --serial        bko's test-guest additions: initdefault 3, a ttyS0 getty,
#                   console=ttyS0,115200n8 in GRUB_CMDLINE_LINUX (grub.cfg
#                   remade), sshd on
# Prints BKO-PCLINUXOS-INSTALL-DONE at the end; exits non-zero on any failure.
set -eEuo pipefail
umask 022                              # InstallerEngine's os.umask(0o022)
trap 'echo "pclinuxos/install.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR

die() { echo "pclinuxos/install.sh: $*" >&2; exit 1; }
firmware="" nvram=yes hostname=localhost user=tester hash="" tz=UTC serial=no akey=""
rpms=() rpm_sums=()
while [[ ${1:-} == --* ]]; do
    case $1 in
        --firmware) firmware=$2; shift 2 ;;
        --no-nvram) nvram=no; shift ;;
        --hostname) hostname=$2; shift 2 ;;
        --user) user=$2; shift 2 ;;
        --password-hash) hash=$2; shift 2 ;;
        --timezone) tz=$2; shift 2 ;;
        --rpm) rpms+=("$2"); rpm_sums+=("$3"); shift 3 ;;
        --authorized-key) akey=$2; shift 2 ;;
        --serial) serial=yes; shift ;;
        *) die "unknown option $1" ;;
    esac
done
[[ $# -eq 3 ]] || die "usage: install.sh [OPTIONS] DISK LAYOUT ISO"
DISK=$1 LAYOUT=$2 ISO=$3
[[ -b $DISK ]] || die "$DISK is not a block device"
[[ -z $firmware ]] && { if [[ -d /sys/firmware/efi ]]; then firmware=uefi; else firmware=bios; fi; }
case $firmware/$LAYOUT in uefi/auto|bios/auto|bios/manual-xfs) ;; *) die "no layout $LAYOUT on $firmware" ;; esac
[[ $nvram == yes || $firmware == uefi ]] || die "--no-nvram is for --firmware uefi"
for i in "${!rpms[@]}"; do
    echo "${rpm_sums[$i]}  ${rpms[$i]}" | sha256sum -c --quiet - || die "${rpms[$i]} doesn't match its pinned SHA-256"
done

# --- The live system: livecd.sqfs under a writable overlay, as the live
# session's /union is, at $L.  The installer's /target is $L/target.
W=$(mktemp -d /tmp/bko-pclos.XXXXXX)
L=$W/union T=$W/union/target
mkdir -p "$W/iso" "$W/sq" "$W/rw" "$L"
mount -o ro "$ISO" "$W/iso"
[[ -f $W/iso/livecd.sqfs ]] || die "no livecd.sqfs on $ISO"
mount -t squashfs -o ro,loop "$W/iso/livecd.sqfs" "$W/sq"
mount -t tmpfs -o size=2g bko-rw "$W/rw"
mkdir -p "$W/rw/up" "$W/rw/work"
mount -t overlay -o "lowerdir=$W/sq,upperdir=$W/rw/up,workdir=$W/rw/work" bko-union "$L"
# The copy's source: the union alone, without the chroot's mounts below (the
# live session's rsync reads /union/*, whose proc, sys, dev and run are the
# image's own directories).  At /source, which the installer makes and excludes.
mkdir -p "$L/source"
for d in dev proc sys run; do mkdir -p "$L/$d"; done   # (not in livecd.sqfs; 755, as in /union)
# Private, or the chroot's mounts below would show through the bind too.
mount --make-rprivate "$L"
mount --bind "$L" "$L/source"
mount --rbind /dev "$L/dev"; mount -t proc proc "$L/proc"; mount --rbind /sys "$L/sys"
mount -t tmpfs tmpfs "$L/run"
cp -L /etc/resolv.conf "$L/etc/resolv.conf" 2>/dev/null || true
live() { chroot "$L" env PATH=/usr/sbin:/usr/bin:/sbin:/bin "$@"; }
in_t() { chroot "$T" env PATH=/usr/sbin:/usr/bin:/sbin:/bin "$@"; }
LI=$L/usr/lib/live-installer

# --- What the live session's boot writes into /union before the installer
# runs, which the installer then copies: mylivecd's rc.sysinit setupinit()
# (these sed lines as it runs them), its hardware detection
# (service_harddrake --force: xorg.conf, modprobe.conf, harddrake2/*,
# finish-install, as the live boot leaves them for this machine), and udev's
# net_create_ifcfg for each wired interface (ifcfg-ethN: DEVICE, BOOTPROTO=dhcp,
# ONBOOT=yes; the kernel's ethN names, by ifindex, as PCLinuxOS keeps them).
live /bin/sed -i -e 's/action.*depmod/true/g' /etc/rc.d/rc.sysinit
live /bin/sed -i.livecd -e "s/\(\S*HOSTNAME=\).*/\1/g" /etc/sysconfig/network
# (setupinit's sed of init.d/halt is undone by rc.sysinit's livecd block,
# which moves halt.livecd back: the installed halt is the image's.)
[[ ! -f $L/etc/rc.d/init.d/netfs ]] || live /bin/sed -i.livecd -e 's,!\/\^#,!\/\(\^#\|loopfs\),g' /etc/rc.d/init.d/netfs
# rc.sysinit's livecd block: finish-install's first-login wizard (keyboard).
cat >"$L/etc/sysconfig/finish-install" <<'EOF2'
FINISH_INSTALL=yes
LICENSE=no
LANGUAGE=no
KEYBOARD=yes
COUNTRY=no
TIMEZONE=no
NETWORK=no
AUTHENTICATION=no
USERS=no
GLX=no
EOF2
timeout 300 chroot "$L" env PATH=/usr/sbin:/usr/bin:/sbin:/bin /usr/share/harddrake/service_harddrake --force >/dev/null 2>&1 ||
    echo "service_harddrake --force: exit $? (as at the live boot, not fatal)"
eth=0
for i in $(for n in /sys/class/net/*; do
        if [[ -e $n/device && ! -e $n/wireless && ! -e $n/phy80211 && $(cat "$n/type") == 1 ]]; then echo "$(cat "$n/ifindex") ${n##*/}"; fi
    done | sort -n | awk '{ print $2 }'); do
    [[ -e $L/etc/sysconfig/network-scripts/ifcfg-eth$eth ]] ||
        printf 'DEVICE=eth%s\nBOOTPROTO=dhcp\nONBOOT=yes\n' "$eth" >"$L/etc/sysconfig/network-scripts/ifcfg-eth$eth"
    echo "wired interface $i: ifcfg-eth$eth"
    eth=$((eth + 1))
done
[[ -f $LI/installer.py ]] || die "no mylive-install in livecd.sqfs"
echo "mylive-install $(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$LI/main.py") from $(cat "$L/etc/pclinuxos-release")"
LOG=$W/live-installer.log
log() { echo " --> $*" | tee -a "$LOG"; }
# The installer's do_unmount: lazy, and never fatal.
unmount() { umount -lf "$1" 2>/dev/null || true; }
cleanup() {
    for m in "$T/sys/firmware/efi/efivars" "$T/dev/shm" "$T/dev/pts" "$T/dev" "$T/sys" "$T/proc" "$T/run" \
        "$T/boot/EFI" "$T/home" "$T/boot" "$T" "$L/run" "$L/sys" "$L/proc" "$L/dev" "$L" "$W/rw" "$W/sq" "$W/iso"; do
        mountpoint -q "$m" 2>/dev/null && umount -R -l "$m" 2>/dev/null
    done
    swapoff -a 2>/dev/null || true
    true
}
trap cleanup EXIT

# --- start_installation: partitions (create_partitions / format_partitions,
# mount_partitions; partitioning.full_disk_format) -------------------------
log "Installation started"
mkdir -p "$T" "$L/source"
p=""; [[ $DISK == /dev/nvme* || $DISK == /dev/mmcblk* ]] && p=p
parted_() { live parted --script --align optimal "$DISK" "$@"; sync; }
dd if=/dev/zero of="$DISK" bs=512 count=1 conv=notrunc status=none
# The installer's create_home default: dualpart_limits, used only at >= 100 GB.
disk_mb=$(( $(blockdev --getsize64 "$DISK") / 1000000 ))
efi_part="" boot_part="" swap_part="" root_part="" home_part="" root_fs=ext4
if [[ $LAYOUT == auto ]]; then
    label=msdos; [[ $firmware == uefi ]] && label=gpt
    live parted -s "$DISK" mklabel "$label"
    start=2 n=0 root_size=0
    want_home=no
    if (( disk_mb >= 100000 )); then
        # partitioning.dualpart_limits(disk, efi, swap): / gets rootfs_recsize
        # (30 GB) and /home the rest, as the GUI's /home selector starts.
        want_home=yes root_size=30000
    fi
    made=""
    mk() {  # mk SIZE_MB (0: the rest): the next partition, in $made
        local end=100%
        if (( $1 )); then end=$((start + $1))MB; fi
        parted_ mkpart primary "${start}MB" "$end"
        n=$((n + 1)); start=$((start + $1 + 1))
        udevadm settle 2>/dev/null || sleep 1
        made=$DISK$p$n
        [[ -b $made ]] || { sleep 2; [[ -b $made ]]; } || die "no $made after mkpart"
    }
    if [[ $firmware == uefi ]]; then mk 300; efi_part=$made; live mkfs.vfat "$efi_part" -F 32 >/dev/null; fi
    mk 1024; swap_part=$made; live mkswap "$swap_part" >/dev/null
    mk "$root_size"; root_part=$made; live mkfs.ext4 -q -F "$root_part"
    if [[ $want_home == yes ]]; then mk 0; home_part=$made; live mkfs.ext4 -q -F "$home_part"; fi
    [[ $firmware == uefi ]] && parted_ set 1 boot on
    mount -t ext4 "$root_part" "$T"
    [[ -z $home_part ]] || { mkdir -p "$T/home"; mount -t ext4 "$home_part" "$T/home"; }
    [[ -z $efi_part ]] || { mkdir -p "$T/boot/EFI"; mount -t vfat "$efi_part" "$T/boot/EFI"; }
else
    # Manual mode: the partitions as a user makes them in the editor
    # (msdos, 1 MiB aligned), formatted by format_partitions' commands and
    # mounted in mount_partitions' order (/ first, then by mount point).
    root_fs=xfs
    live parted -s "$DISK" mklabel msdos
    parted_ mkpart primary 1MiB 1025MiB
    parted_ mkpart primary 1025MiB 2049MiB
    parted_ mkpart primary 2049MiB 100%
    udevadm settle 2>/dev/null || sleep 1
    boot_part=$DISK${p}1 swap_part=$DISK${p}2 root_part=$DISK${p}3
    live mkfs.ext4 -q -F "$boot_part"
    live mkswap "$swap_part" >/dev/null
    live mkfs.xfs -q -f "$root_part"
    mount -t xfs "$root_part" "$T"
    mkdir -p "$T/boot"; mount -t ext4 "$boot_part" "$T/boot"
fi
swapon "$swap_part"

# Stale EFI loaders on the live media go first (the live image carries its
# builder's ESP in /boot/EFI).
live sh -c 'rm -rf /boot/EFI/EFI/*'
kver=$(ls "$L/lib/modules")
[[ $(wc -w <<<"$kver") == 1 ]] || die "expected one kernel in livecd.sqfs, found: $kver"
# (The installer copies /lib/modules/$(uname -r)/vmlinuz if there is one;
# the live image has none.)
[[ ! -e $L/lib/modules/$kver/vmlinuz ]] || cp "$L/lib/modules/$kver/vmlinuz" "$T/boot/vmlinuz-$kver"

# The copy: installer.py's rsync, its excludes as they are.
log "Copying the system"
excl=(dev/* proc/* sys/* tmp/* run/* var/run/user/* mnt/* media/* lost+found source target data/user/*)
filt=(); for d in "${excl[@]}"; do filt+=("--exclude=/$d"); done
live sh -c 'cd / && exec rsync --no-inc-recursive --archive --no-D --acls --hard-links --xattrs "$@" /source/* /target/' rsync "${filt[@]}"

# The chroot.
for d in dev dev/shm dev/pts sys proc run tmp mnt media; do mkdir -p "$T/$d"; done
chmod 1777 "$T/tmp"
mount --bind /dev "$T/dev"; mount --bind /dev/shm "$T/dev/shm" 2>/dev/null || true
mount --bind /dev/pts "$T/dev/pts"; mount --bind /sys "$T/sys"; mount --bind /proc "$T/proc"
mount --bind "$L/run" "$T/run"
rm -f "$T/etc/fstab"
[[ ! -d /sys/firmware/efi ]] || mount --bind /sys/firmware/efi/efivars "$T/sys/firmware/efi/efivars"
rm -rf "$T/etc/resolv.conf"; cp -rf "$L/etc/resolv.conf" "$T/etc/resolv.conf" 2>/dev/null || true

# Users: the live user goes, the installer's user comes.
log "Adding new user"
if grep -q '^guest:' "$T/etc/passwd"; then in_t /usr/sbin/userdel --remove guest || true; fi
rm -rf "$T/home/guest"
echo "Creating users"
in_t useradd -m -s /bin/bash -c "Test user" "$user"
for g in lp cdrom cdwriter dialout disk users polkituser audio video netdev; do in_t usermod -aG "$g" "$user" || true; done
if [[ -n $hash ]]; then
    in_t usermod -p "$hash" "$user"
    in_t usermod -p "$hash" root
fi
# add_sudoer: a drop-in, and the include if sudoers lacks it.
grep -qE '^[@#]includedir[[:space:]]+/etc/sudoers\.d/?[[:space:]]*$' "$T/etc/sudoers" ||
    { [[ -z $(tail -c 1 "$T/etc/sudoers") ]] || echo >>"$T/etc/sudoers"; echo "@includedir /etc/sudoers.d" >>"$T/etc/sudoers"; }
install -d -m 750 "$T/etc/sudoers.d"
echo "$user ALL=(ALL:ALL) ALL" >"$T/etc/sudoers.d/$user"; chmod 440 "$T/etc/sudoers.d/$user"
find "$T/home/" -type f -iname live-installer.desktop -delete || true
cp -f "$LI/resources/sddm.conf" "$T/etc"
cp -f "$LI/resources/custom.conf" "$T/etc/X11/gdm"

# write_fstab
log "Writing fstab"
uuid() { live blkid "$1" | tr ' ' '\n' | grep -m1 'UUID=' | tr -d '"'; }
{
    echo "#### Static Filesystem Table File"
    echo "proc /proc proc defaults 0 0"
    echo "none /dev/pts devpts defaults 0 0"
    if [[ $LAYOUT == auto ]]; then
        echo "# $root_part"; echo "$(uuid "$root_part") /  ext4 defaults 0 1"
        echo "# $swap_part"; echo "$(uuid "$swap_part") none   swap sw 0 0"
        [[ -z $home_part ]] || { echo "# $home_part"; echo "$(uuid "$home_part") /home  ext4 defaults 0 2"; }
        [[ -z $efi_part ]] || { echo "# $efi_part"; echo "$(uuid "$efi_part") /boot/EFI  vfat defaults 0 1"; }
    else
        # Manual: in mount_partitions' order ("/", "/boot", "swap").
        echo "# $root_part"; echo "$(uuid "$root_part") / xfs defaults,rw 0 1"
        echo "# $boot_part"; echo "$(uuid "$boot_part") /boot ext4 defaults,rw 0 0"
        echo "# $swap_part"; echo "$(uuid "$swap_part") swap swap sw 0 0"
    fi
} >"$T/etc/fstab"
cat "$T/etc/fstab"

# --- finish_installation ---------------------------------------------------
log "Writing hostname"
echo "$hostname" >"$T/etc/hostname"
echo "127.0.0.1 $hostname localhost" >"$T/etc/hosts"
log "Setting the locale"
grep -qxF "en_US.UTF-8 UTF-8" "$T/etc/locale.gen" 2>/dev/null || echo "en_US.UTF-8 UTF-8" >>"$T/etc/locale.gen"
log "Setting the timezone"
if [[ -e $T/etc/localtime || -L $T/etc/localtime ]]; then rm -f "$T/etc/localtime"; ln -s "../usr/share/zoneinfo/$tz" "$T/etc/localtime"; fi
printf 'ARC=false\nUTC=true\nZONE=%s\n' "$tz" >"$T/etc/sysconfig/clock"
# The keyboard files, rewritten as installer.py rewrites them, with the
# target's python3 (us, pc104, no variant).
log "Setting keyboard options"
in_t python3 -I - <<'EOF'
import os
layout, model, variant = "us", "pc104", ""
d = "/etc/X11/xorg.conf.d" if os.path.exists("/etc/X11/xorg.conf.d") else "/usr/share/X11/xorg.conf.d/"
if os.path.exists(d):
    with open(os.path.join(d, "00-keyboard.conf"), "w") as f:
        f.write('Section "InputClass"\nIdentifier "system-keyboard"\nMatchIsKeyboard "on"\n')
        f.write('Option "XkbLayout" "{}"\nOption "XkbModel" "{}"\nOption "XkbVariant" "{}"\n'.format(layout, model, variant))
        f.write('EndSection\n')
def rewrite(path, fix):
    if not os.path.exists(path):
        return
    out = [fix(l.rstrip("\r\n")) for l in open(path)]
    open(path + ".new", "w").write("".join(out))
    os.remove(path); os.rename(path + ".new", path)
def xkb(line):
    if line.startswith("XKBMODEL="): return 'XKBMODEL="%s"\n' % model
    if line.startswith("XKBLAYOUT="): return 'XKBLAYOUT="%s"\n' % layout
    if line.startswith("XKBVARIANT=") and variant != "": return 'XKBVARIANT="%s"\n' % variant
    return line + "\n"
rewrite("/etc/default/console-setup", xkb)
rewrite("/etc/vconsole.conf", lambda l: ('KEYMAP="%s"\n' % layout) if l.startswith("KEYMAP=") else l + "\n")
rewrite("/etc/default/keyboard", lambda l: 'XKBOPTIONS=""' if l.startswith("XKBOPTIONS=") else xkb(l))
if os.path.exists("/usr/share/glib-2.0/schemas/org.gnome.desktop.input-sources.gschema.xml"):
    with open("/usr/share/glib-2.0/schemas/org.gnome.desktop.gnome-keyboard-config.gschema.override", "w") as s:
        s.write("[org.gnome.desktop.input-sources]\nsources = [('xkb', '%s')]" % layout)
EOF
in_t glib-compile-schemas /usr/share/glib-2.0/schemas/ || true

# grub_prepare: the ISO's own cleanup.sh, run where the installer runs it.
log "Preparing bootloader installation"
live sh -c "cd /usr/lib/live-installer && bash scripts/cleanup.sh"

log "Installing bootloader"
if [[ $firmware == uefi ]]; then
    if [[ $nvram == yes ]]; then
        in_t grub2-install
    else
        in_t grub2-install --target=x86_64-efi --efi-directory=/boot/EFI --bootloader-id=pclinuxos --no-nvram
        in_t grub2-install --target=x86_64-efi --efi-directory=/boot/EFI --bootloader-id=pclinuxos --no-nvram --removable
    fi
    printf 'grub2-install ' >"$T/boot/grub2/install.sh"
else
    in_t grub2-install "$DISK"
    printf 'grub2-install %s' "$DISK" >"$T/boot/grub2/install.sh"
fi
chmod 700 "$T/boot/grub2/install.sh"
# resume= in GRUB_CMDLINE_LINUX_DEFAULT: the first fstab line naming swap.
swapdev=$(awk '/swap/ { print $1; exit }' "$T/etc/fstab")
python3_edit_default_grub() {
    in_t python3 -I - "$swapdev" <<'EOF'
import re, sys
swap = sys.argv[1]
lines = open("/etc/default/grub").readlines()
out = []
for line in lines:
    if line.startswith("GRUB_CMDLINE_LINUX_DEFAULT="):
        m = re.search(r"resume=\S+", line)
        if swap:
            if m: line = line.replace(m.group(), "resume=" + swap)
            else:
                o = re.search(r"quiet", line)
                if o: line = line.replace(o.group(), "quiet resume=" + swap)
        elif m:
            line = line.replace(m.group(), "")
    out.append(line)
open("/etc/default/grub", "w").writelines(out)
EOF
}
python3_edit_default_grub
in_t grub2-mkconfig -o /boot/grub2/grub.cfg
log "Configuring bootloader"
in_t /bin/sh -c "grub2-mkconfig -o /boot/grub2/grub.cfg" >>"$LOG" 2>&1
[[ -f $T/boot/grub2/grub.cfg ]] || die "no /boot/grub2/grub.cfg"

log "Configuring initrd"
if [[ $firmware == uefi && ! -d /sys/firmware/efi ]]; then
    # --no-nvram (a BIOS-booted install VM): drakx finds a UEFI GRUB only
    # when /sys/firmware/efi exists (common.pm is_uefi, bootloader.pm read:
    # "Cannot find a boot loader installed" otherwise), so the chroot gets an
    # empty one for this step alone, as the installed system has under UEFI.
    mount -t tmpfs -o size=1m bko-fw "$T/sys/firmware"
    mkdir "$T/sys/firmware/efi"
    in_t bootloader-config --action rebuild-initrds
    umount "$T/sys/firmware"
else
    in_t bootloader-config --action rebuild-initrds
fi
# With a separate /boot, rebuild-initrds rebuilds nothing: drakx reads the
# entry's initrd as grub.cfg names it on /boot's own file system
# (/initrd-K.img), so rebuild_initrd's rename to the .old name (on /) fails
# across file systems, and bootloader::mkinitrd then skips the image that
# "already exists" (bootloader.pm 136-138, 176-186).  The system keeps the
# live image's initrd, made on the ISO builder's machine: no virtio_blk and
# no xfs (both modules in this kernel), so an XFS root (pclinuxos-bios) can't
# be mounted.  Where that happened, the image is made the way rebuild_initrd
# meant to: mkinitrd -f /boot/initrd-K.img K, no options (docs/design/
# pclinuxos.md, "What PC1 found").
for k in "$T"/boot/vmlinuz-*; do
    v=${k##*/vmlinuz-}
    [[ -e $T/boot/initrd-$v.img && -e $L/boot/initrd-$v.img ]] || continue
    if cmp -s "$T/boot/initrd-$v.img" "$L/boot/initrd-$v.img"; then
        echo "rebuild-initrds left the live image's initrd-$v.img: mkinitrd -f /boot/initrd-$v.img $v"
        in_t mkinitrd -f "/boot/initrd-$v.img" "$v"
    fi
done
rm -rf "$T/var/run/user/1000/"
for s in avahi-daemon atd cups cups-browsed crond rsyslog xinetd vnstat update-notifier; do
    in_t chkconfig "$s" on || echo "chkconfig $s on failed (as the installer, not fatal)"
done
log "All done"
cat "$LOG" >"$T/var/log/live-installer.log"

# --- After the installer -----------------------------------------------------
for i in "${!rpms[@]}"; do
    cp "${rpms[$i]}" "$T/tmp/"
    in_t rpm -i "/tmp/${rpms[$i]##*/}"
    rm -f "$T/tmp/${rpms[$i]##*/}"
done
if [[ -n $akey ]]; then
    install -d -m 700 "$T/root/.ssh"
    echo "$akey" >"$T/root/.ssh/authorized_keys"; chmod 600 "$T/root/.ssh/authorized_keys"
fi
if [[ $serial == yes ]]; then
    sed -i 's/^id:[0-9]:initdefault:/id:3:initdefault:/' "$T/etc/inittab"
    grep -q '^id:3:initdefault:' "$T/etc/inittab" || die "no initdefault line in inittab"
    grep -q '^s0:' "$T/etc/inittab" || echo 's0:2345:respawn:/sbin/agetty ttyS0 115200 vt100' >>"$T/etc/inittab"
    grep -qx ttyS0 "$T/etc/securetty" 2>/dev/null || { [[ ! -f $T/etc/securetty ]] || echo ttyS0 >>"$T/etc/securetty"; }
    if grep -q '^GRUB_CMDLINE_LINUX=' "$T/etc/default/grub"; then
        sed -i 's/^GRUB_CMDLINE_LINUX="\(.*\)"/GRUB_CMDLINE_LINUX="\1 console=tty0 console=ttyS0,115200n8"/' "$T/etc/default/grub"
    else
        echo 'GRUB_CMDLINE_LINUX="console=tty0 console=ttyS0,115200n8"' >>"$T/etc/default/grub"
    fi
    in_t grub2-mkconfig -o /boot/grub2/grub.cfg
    in_t chkconfig sshd on
fi

# What the image is, for the build log.
echo "fstab:"; sed 's/^/  /' "$T/etc/fstab"
for k in "$T"/boot/vmlinuz-*; do
    v=${k##*/vmlinuz-}
    [[ -s $T/boot/initrd-$v.img ]] || die "no /boot/initrd-$v.img"
    # (The new system's zcat and cpio: the live system may lack cpio.)
    echo "initrd-$v.img: $(in_t sh -c "zcat /boot/initrd-$v.img | cpio -i --quiet --to-stdout init | head -n 1")," \
        "modules: $(in_t sh -c "zcat /boot/initrd-$v.img | cpio -it --quiet" | sed -n 's|.*/\([^/]*\)\.ko.*|\1|p' | sort | tr '\n' ' ')"
done
[[ $firmware == bios ]] || find "$T/boot/EFI" -type f | sed "s|^$T|esp: |"
sync
echo BKO-PCLINUXOS-INSTALL-DONE
