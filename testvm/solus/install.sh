#!/bin/bash
# Run as root in Solus 4.9's live system by make-solus.sh (fetched by
# kernel-command-line.service, systemd.run=): install Solus on /dev/vda the
# way its Calamares does for "erase disk", unattended, then give the guest
# what the other test guests get, record what the build found, and power off.
#
# 1. Calamares's exec sequence, from the ISO's own config
#    (/usr/share/calamares/settings.conf and modules/*.conf, read in S1 of
#    docs/design/solus.md), replayed by hand:
#    partition  GPT: a 2 MiB gap, the ESP (FAT32, 2 GiB, GPT name "EFI"),
#               an 8 MiB BIOS boot partition (createHybridBootloaderLayout),
#               no swap (initialSwapChoice's default, "none"), ext4 / on the
#               rest (PartitionActions.cpp, Calamares 3.4.2);
#    mount      / at the target, the ESP at /boot, proc, sys, dev, efivarfs;
#    unpackfs   the live squashfs's LiveOS/rootfs.img, rsync'd;
#    machineid  systemd-machine-id-setup;
#    fstab      / only: Solus's fstab module leaves /boot (the ESP) out on
#               UEFI, "clr-boot-manager will mount it as needed";
#    locale, keyboard, localecfg
#               en_US.UTF-8, us, UTC;
#    postinstall
#               usysconf run, vconsole's font, eopkg rr Local, eopkg ar Solus;
#    removeliveos
#               the live branding packages and calamares removed,
#               /etc/sudoers.d and /etc/pam.d removed (stateless defaults);
#    removeuser live; users (tester, in Calamares's groups, wheel for sudo);
#    bootloader no resume (no swap), clr-boot-manager update in the target
#               with real EFI variables.
# 2. What the other test guests get: the whole system brought up to date
#    with eopkg up (Solus takes no partial upgrade, and openssh-server, which
#    the ISO lacks, comes from today's repository), openssh-server with
#    sshd.service enabled (and a preset that keeps it so), mokutil, root's password (the same hash as tester's) and
#    SSH key, text mode at boot, a serial console (/etc/kernel/cmdline.d),
#    then usysconf and clr-boot-manager update again, eopkg dc.
# 3. The facts, as a tar written raw on /dev/vdb: os-release, the partition
#    table, fstab, the ESP's files with their SHA-256s, the kernels, the
#    loader entries, the NVRAM entries, the MOK's certificate
#    (solus-enroll-me.cer), the packages.
# The last line is BACKOTTER-INSTALL-OK, or BACKOTTER-INSTALL-FAILED: WHY.
set -uo pipefail

HOSTNAME='@HOSTNAME@'
USERNAME='@USERNAME@'
PASSWORD_HASH='@PASSWORD_HASH@'
AUTHORIZED_KEY='@AUTHORIZED_KEY@'
URL='@URL@'
DISK=/dev/vda FACTS_DISK=/dev/vdb
T=/mnt/bko-target SQ=/mnt/bko-squash SRC=/mnt/bko-rootfs F=/run/bko-facts
REPO=https://cdn.getsol.us/repo/polaris/eopkg-index.xml.xz
GROUPS_=(users audio video cdrom dialout fuse sambashares lpadmin plugdev scanner wheel)

mkdir -p $F
# The serial console opened afresh for each line: a getty's vhangup on ttyS0
# would cut off an fd held open (the kernel line masks it too).
exec > >(while IFS= read -r l; do
    printf '%s\n' "$l" >>$F/install.log; printf '%s\r\n' "$l" >/dev/ttyS0 2>/dev/null
done) 2>&1
say() { echo "bko-install: $*"; }
save_facts() {
    sync
    if [[ -b $FACTS_DISK ]]; then
        tar -C $F -cf $FACTS_DISK . && sync && say "facts written to $FACTS_DISK"
    fi
}
cleanup_mounts() {
    local m
    for m in $T/sys/firmware/efi/efivars $T/dev/pts $T/dev $T/proc $T/sys $T/run $T/boot $T $SRC $SQ; do
        mountpoint -q $m && umount $m
    done
}
finish() {
    cleanup_mounts
    save_facts
    echo "$1"
    sleep 2
    systemctl poweroff
    exit 0
}
fail() { finish "BACKOTTER-INSTALL-FAILED: $*"; }
in_t() { chroot $T "$@"; }

say "the live system: $(uname -r), $(. /etc/os-release; echo "$PRETTY_NAME")"
say "kernel command line: $(cat /proc/cmdline)"
for _ in $(seq 60); do [[ -b $DISK && -b $FACTS_DISK ]] && break; sleep 1; done
[[ -b $DISK ]] || fail "no $DISK"
[[ -d /sys/firmware/efi/efivars ]] || fail "not booted by UEFI"
eopkg li 2>/dev/null | grep -E '^(calamares|clr-boot-manager|shim-signed|linux-current|eopkg|usysconf) ' |
    sed 's/^/bko-install: live package: /'

# --- 1. Calamares's job sequence ---------------------------------------------
# partition
wipefs -a $DISK >/dev/null
sgdisk -Z $DISK >/dev/null 2>&1
sgdisk -n 1:4096:+2G -t 1:ef00 -c 1:EFI \
    -n 2:0:+8M -t 2:ef02 \
    -n 3:0:0 -t 3:8300 $DISK || fail "sgdisk"
partprobe $DISK; udevadm settle
esp=${DISK}1 root=${DISK}3
[[ -b $esp && -b $root ]] || fail "no $esp or $root after partitioning"
mkfs.fat -F 32 $esp >/dev/null || fail "mkfs.fat"
mkfs.ext4 -q -F $root || fail "mkfs.ext4"

# mount
mkdir -p $T $SQ $SRC
mount $root $T || fail "could not mount /"
mkdir -p $T/boot && mount -o umask=0077 $esp $T/boot || fail "could not mount the ESP at /boot"

# shellprocess@mountsquashfs, unpackfs
mount -o loop,ro /run/initramfs/live/LiveOS/squashfs.img $SQ || fail "could not mount the live squashfs"
mount -o loop,ro $SQ/LiveOS/rootfs.img $SRC || fail "could not mount LiveOS/rootfs.img"
say "unpacking LiveOS/rootfs.img"
# The image's /boot is empty (the ESP is mounted over it, FAT32).
[[ -z $(ls -A $SRC/boot) ]] || fail "LiveOS/rootfs.img's /boot isn't empty: $(ls -A $SRC/boot | tr '\n' ' ')"
rsync -aHAX --numeric-ids --exclude=/boot/ $SRC/ $T/ || fail "rsync of the root image"
umount $SRC; umount $SQ

for d in dev dev/pts proc sys run; do mkdir -p $T/$d; mount --bind /$d $T/$d || fail "could not bind /$d"; done
mount -t efivarfs efivarfs $T/sys/firmware/efi/efivars || fail "could not mount efivarfs in the target"

# machineid
rm -f $T/etc/machine-id
in_t systemd-machine-id-setup >/dev/null || fail "systemd-machine-id-setup"

# fstab: / only (Solus's patch leaves the ESP at /boot out on UEFI)
printf '# /etc/fstab: written by make-solus.sh as Solus'"'"'s Calamares does\nUUID=%s / ext4 defaults,noatime 0 1\n' \
    "$(blkid -s UUID -o value $root)" >$T/etc/fstab

# locale, keyboard, localecfg
echo 'LANG=en_US.UTF-8' >$T/etc/locale.conf
echo 'KEYMAP=us' >$T/etc/vconsole.conf
ln -sf ../usr/share/zoneinfo/UTC $T/etc/localtime

# shellprocess@postinstall
in_t /usr/sbin/usysconf run || say "usysconf run failed (going on, as Calamares would not)"
echo 'FONT=ter-v32b' >>$T/etc/vconsole.conf
in_t /usr/bin/eopkg rr Local >/dev/null 2>&1 || true
in_t /usr/bin/eopkg ar --no-fetch -y Solus $REPO || fail "eopkg ar Solus"

# shellprocess@removeliveos
in_t eopkg rmf -y budgie-desktop-branding-livecd gnome-desktop-branding-livecd mate-desktop-branding-livecd \
    plasma-desktop-branding-livecd xfce4-desktop-branding-livecd network-manager-livecd || say "eopkg rmf of the live branding failed"
in_t eopkg rmf -y calamares || fail "eopkg rmf calamares"
rm -f $T/etc/gdm/custom.conf
rm -rf $T/etc/sudoers.d $T/etc/pam.d

# removeuser, users
in_t userdel -f -r live 2>/dev/null || say "no live user to remove"
for g in "${GROUPS_[@]}"; do in_t getent group $g >/dev/null || in_t groupadd $g; done
in_t useradd -m -s /usr/bin/bash -G "$(IFS=,; echo "${GROUPS_[*]}")" "$USERNAME" || fail "useradd $USERNAME"
printf '%s:%s\n' "$USERNAME" "$PASSWORD_HASH" | in_t chpasswd -e || fail "chpasswd $USERNAME"
echo "$HOSTNAME" >$T/etc/hostname
printf '127.0.0.1 localhost\n127.0.1.1 %s\n::1 localhost\n' "$HOSTNAME" >$T/etc/hosts

# bootloader (no swap, so no 10_resume.conf)
in_t clr-boot-manager update || fail "clr-boot-manager update (Calamares's bootloader job)"
say "Calamares's sequence done"

# --- 2. What the other test guests get ---------------------------------------
# Name resolution in the target for eopkg: the live system's.
[[ -e $T/etc/resolv.conf || -L $T/etc/resolv.conf ]] && mv $T/etc/resolv.conf $T/etc/resolv.conf.bko
cp -L /etc/resolv.conf $T/etc/resolv.conf || fail "no resolv.conf in the live system"
say "eopkg up"
# A download that stalls ends eopkg with "System error"; what it fetched
# stays in /var/cache/eopkg/packages, so a retry goes on from there.
retry() { local i; for i in 1 2 3 4 5; do "$@" && return 0; say "attempt $i of: $* failed"; sleep 10; done; return 1; }
retry in_t eopkg up -y || fail "eopkg up"
# mokutil: for the harnesses' "mokutil --sb-state" (Solus installs it only
# on demand; the first boot of the third build had none).
retry in_t eopkg it -y openssh-server mokutil || fail "eopkg it openssh-server mokutil"
rm -f $T/etc/resolv.conf
[[ -e $T/etc/resolv.conf.bko || -L $T/etc/resolv.conf.bko ]] && mv $T/etc/resolv.conf.bko $T/etc/resolv.conf

printf 'root:%s\n' "$PASSWORD_HASH" | in_t chpasswd -e || fail "chpasswd root"
install -d -m 700 $T/root/.ssh
printf '%s\n' "$AUTHORIZED_KEY" >$T/root/.ssh/authorized_keys
chmod 600 $T/root/.ssh/authorized_keys
# Solus's presets end with "disable *" (99-default.preset), and usysconf
# applies them again after package changes, which turned an enabled sshd off
# (the second build): a preset of its own keeps it on, as on a Solus box.
mkdir -p $T/etc/systemd/system-preset
echo 'enable sshd.service' >$T/etc/systemd/system-preset/50-bko-sshd.preset
in_t systemctl enable sshd.service || fail "could not enable sshd.service"
in_t systemctl set-default multi-user.target || fail "set-default"
# The serial console (ttyS0 last: it is /dev/console), and no quiet splash:
# clr-boot-manager appends cmdline.d and drops cmdline-removal.d's words.
mkdir -p $T/etc/kernel/cmdline.d $T/etc/kernel/cmdline-removal.d
echo 'console=tty0 console=ttyS0,115200n8' >$T/etc/kernel/cmdline.d/90-bko-serial.conf
printf 'quiet\nsplash\n' >$T/etc/kernel/cmdline-removal.d/90-bko-serial.conf
in_t /usr/sbin/usysconf run -f || say "usysconf run -f failed"
for k in $T/usr/lib/modules/*; do in_t depmod -a "${k##*/}" || say "depmod ${k##*/} failed"; done
in_t clr-boot-manager update || fail "clr-boot-manager update (after eopkg up)"
[[ $(in_t systemctl is-enabled sshd.service 2>&1) == enabled ]] || fail "sshd.service isn't enabled after usysconf: $(in_t systemctl is-enabled sshd.service 2>&1)"
in_t eopkg dc >/dev/null 2>&1 || true
in_t sshd -T 2>/dev/null | grep -Ei '^(permitrootlogin|passwordauthentication) ' | sed 's/^/bko-install: sshd: /'

# What eopkg up replaced and eopkg dc deleted (~7 GiB) handed back to the
# image (make-solus.sh's disk is discard=unmap).
fstrim -v $T/boot; fstrim -v $T

# --- 3. The facts ------------------------------------------------------------
cp $T/usr/lib/os-release $F/os-release
sfdisk -d $DISK >$F/partition-table.txt
lsblk -o NAME,SIZE,TYPE,FSTYPE,PARTTYPENAME,PARTLABEL,LABEL,UUID,MOUNTPOINTS $DISK >$F/lsblk.txt
blkid >$F/blkid.txt
cp $T/etc/fstab $F/fstab
(cd $T/boot && find . -type f | sort | xargs -d '\n' sha256sum) >$F/esp-files.sha256
(cd $T/boot && find . | sort | xargs -d '\n' ls -ld --time-style=+) >$F/esp-ls.txt
for e in $T/boot/loader/loader.conf $T/boot/loader/entries/*.conf; do
    [[ -f $e ]] && { echo "== ${e#$T}"; cat "$e"; }
done >$F/loader-entries.txt
ls -la $T/usr/lib64/kernel $T/usr/lib/modules >$F/kernels.txt 2>&1
cat $T/etc/kernel/cmdline.d/* $T/etc/kernel/cmdline-removal.d/* >$F/cmdline-d.txt 2>&1
cp $T/boot/solus-enroll-me.cer $F/MOK.der || fail "no solus-enroll-me.cer on the ESP"
efibootmgr -v >$F/efibootmgr.txt 2>&1
in_t eopkg li >$F/packages.txt 2>&1
in_t clr-boot-manager list-kernels >$F/cbm-kernels.txt 2>&1
in_t systemctl is-enabled sshd.service zram-generator.service >$F/units.txt 2>&1
ls -la $T/var/lib/eopkg >$F/eopkg-db.txt 2>&1
cleanup_mounts
finish BACKOTTER-INSTALL-OK
