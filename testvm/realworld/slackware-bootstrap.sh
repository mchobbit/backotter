#!/bin/bash
# Install Slackware 15.0 on a real machine from another running Linux (the
# BackOtter rescue stick), the way the slackware15-uefi test guest is built
# (testvm/make-bootstrap.sh slackware-uefi, testvm/bootstrap/slackware.sh):
# Slackware's own installpkg --root of the series a, ap, d, l and n (less
# kernel-huge) in setup's order, from the 15.0 tree checked against
# Slackware's key, then the files setup's configuration steps write, the
# generic kernel with mkinitrd's initrd, and ELILO in EFI/Slackware with its
# NVRAM entry, as eliloconfig makes it.  For BackShot1
# (docs/design/slackware.md, S8).  As root, on the rescue:
#
#   slackware-bootstrap.sh SERIAL KEYS_DIR HOSTNAME CACHE_DIR
#
#   SERIAL     the one disk to ERASE, by serial number (lsblk -dno SERIAL)
#   KEYS_DIR   authorized_keys (root's login) and ssh_host_ed25519_key{,.pub}
#              (the fixed host key), BackShot1's keys/ on the backup drive
#   HOSTNAME   the installed system's host name (BackShot1's: thinkpad)
#   CACHE_DIR  where the tree is kept between runs (slackware64-15.0/ and the
#              key), on a disk other than SERIAL's: on the backup drive,
#              /mnt/bs1-backup/slackware-cache
#
# The tree comes from MIRROR into CACHE_DIR (only what is missing):
# CHECKSUMS.md5 and its .asc, checked with gpgv against Slackware's key (the
# mirror's GPG-KEY, held to SLACKWARE_KEY's fingerprint), then each package
# of the list against its MD5 there and its own .asc, as make-bootstrap.sh
# checks it; a cached package that fails is fetched again once, with its
# .asc.  All before the disk is touched.
#
# Layout (UEFI, GPT): a 512 MiB ESP (vfat) at /boot/efi and ext4 / for the
# rest, made with the tree's own mke2fs.conf (e2fsprogs 1.46.5's e2fsck
# refuses the live system's orphan_file); no swap.  fstab and root= by
# device name, as setup writes them, so an NVMe disk only: its name is the
# same with the stick plugged in or not (a SATA disk is sdb beside the
# stick, sda without it).  The generic kernel, mkinitrd's initrd
# from the command mkinitrd_command_generator.sh prints, ELILO's four files
# in EFI/Slackware (and EFI/BOOT, as the guest has them), its NVRAM entry
# "Slackware" first in BootOrder and as BootNext.  NVRAM entries for the
# disk's old ESP go.  Then: the host name (HOSTNAME.localdomain), DHCP from
# rc.inet1 on eth0-eth2 with the machine's wired adapters pinned to those
# names (70-persistent-net.rules, the onboard ones first), Europe/Paris,
# sshd on, root by key only (PermitRootLogin prohibit-password; root's
# password field "*"), the fixed host key, slackpkg's mirror and Slackware's
# key in root's keyring, and a user m with no password (locked: the person
# sets one).  No password is set anywhere.  Slackware 15.0 has no avahi:
# the machine answers by address, not HOSTNAME.local.
#
# Where the rescue (Ubuntu) isn't setup's ramdisk: installpkg runs with Slackware's own tar, with
# --numeric-owner, and bash as sh; the doinst.sh scripts that chown by name
# run once more in the chroot; mkinitrd's NVMe driver is the kernel's own
# (built in), not a module.  Each is said where it's done.
# Prints BKO-SLACKWARE-INSTALL-OK.

set -eEuo pipefail
main_pid=$$
trap '[[ $BASHPID == "$main_pid" ]] && echo "slackware-bootstrap: failed at line $LINENO: $BASH_COMMAND" >&2' ERR
SERIAL=${1:?usage: slackware-bootstrap.sh SERIAL KEYS_DIR HOSTNAME CACHE_DIR}
KEYS=${2:?usage: slackware-bootstrap.sh SERIAL KEYS_DIR HOSTNAME CACHE_DIR}
HOST=${3:?usage: slackware-bootstrap.sh SERIAL KEYS_DIR HOSTNAME CACHE_DIR}
CACHE=${4:?usage: slackware-bootstrap.sh SERIAL KEYS_DIR HOSTNAME CACHE_DIR}
# make-bootstrap.sh's SLACKWARE_MIRROR, SLACKWARE_KEY and SLACKWARE_SERIES.
MIRROR=https://slackware.osuosl.org/slackware64-15.0
SLACKWARE_KEY=EC5649DA401E22ABFA6736EF6A4463C040102233
SLACKWARE_SERIES="a ap d l n"
KERNEL=generic OTHER_KERNEL=huge
ESP_SIZE=512MiB
TZONE=Europe/Paris
DOMAIN=localdomain
USER_NAME=m
# Slackware's adduser's default extra groups, and wheel.
USER_GROUPS=audio,cdrom,floppy,input,lp,netdev,plugdev,power,scanner,video,wheel

die() { echo "slackware-bootstrap: $*" >&2; exit 1; }
step() { echo "== $(date +%T) $*"; }
[[ $EUID -eq 0 ]] || die "run it as root"
[[ -d /sys/firmware/efi/efivars ]] || die "this system wasn't booted by UEFI (this installs ELILO)"
for f in authorized_keys ssh_host_ed25519_key ssh_host_ed25519_key.pub; do [[ -s $KEYS/$f ]] || die "no $KEYS/$f"; done
for t in curl gpg gpgv md5sum xz tar sfdisk wipefs mkfs.fat mkfs.ext4 dumpe2fs blkid blockdev udevadm chroot efibootmgr lsblk findmnt xargs; do
    command -v $t >/dev/null || die "this system has no $t"
done
[[ $HOST =~ ^[a-z0-9][a-z0-9-]*$ ]] || die "HOSTNAME \"$HOST\" isn't a plain host name"
[[ -d $CACHE ]] || die "no $CACHE"
CACHE=$(realpath "$CACHE")

# The disk, by serial and nothing else.
mapfile -t disks < <(lsblk -dnpo NAME,SERIAL | awk -v s="$SERIAL" '$2 == s { print $1 }')
[[ ${#disks[@]} -eq 1 ]] || die "${#disks[@]} disks have serial $SERIAL"
DISK=${disks[0]}
tran=$(lsblk -dno TRAN "$DISK")
[[ $tran != usb ]] || die "$DISK is a USB disk"
# fstab, root= and mkinitrd's -r name the root by device, as setup writes
# them; the rescue's name for a SATA disk (sdb, the stick being sda) isn't
# the installed system's (sda), an NVMe disk's is.  (UUID= would do for
# SATA, but setup never writes it.)  So NVMe only, whose driver the generic
# kernel has built in (checked below with the initrd).
[[ $tran == nvme ]] || die "$DISK is not an NVMe disk (transport \"$tran\"): this names the root by device, as setup does, and only an NVMe disk's name stays the same without the stick"
if lsblk -nro MOUNTPOINTS "$DISK" | grep -q .; then die "something on $DISK is mounted"; fi
cache_dev=$(findmnt -nro SOURCE -T "$CACHE")
if lsblk -lnpo NAME "$DISK" | grep -qxF "$cache_dev"; then die "$CACHE is on $DISK, the disk to erase"; fi
part() { case $DISK in *[0-9]) echo "${DISK}p$1" ;; *) echo "$DISK$1" ;; esac; }
ESP=$(part 1) ROOT=$(part 2)

tmp=$(mktemp -d /run/bko-slackware.XXXXXX)
# A private tmpfs: /run is shared, and the mounts below would otherwise be
# copied into every service's own namespace, and stay there after the
# unmount (pika-bootstrap.sh's first run).  The target under it, not /mnt:
# the backup drive with KEYS_DIR and the cache is mounted below /mnt
# (/mnt/bs1-backup), and /mnt would hide it.
mount -t tmpfs -o size=1G,mode=0700 bko-slackware "$tmp"
mount --make-private "$tmp"
T=$tmp/target
mkdir "$T"
# The target and everything below it: umount -R, or, when something there
# is busy (a process still in the chroot), lazily, said so.
unmount_target() {
    mountpoint -q "$T" || return 0
    umount -R "$T" && return 0
    echo "slackware-bootstrap: umount -R $T failed; still mounted: $(findmnt -Rrno TARGET "$T" | paste -sd ' ' -)" >&2
    echo "slackware-bootstrap: detaching them lazily (umount -R -l): $ROOT stays mounted until nothing uses it; reboot before touching $DISK again" >&2
    umount -R -l "$T"
}
cleanup() {
    set +e
    unmount_target
    if findmnt -rno TARGET | grep -q "^$tmp/"; then
        echo "slackware-bootstrap: $tmp still has mounts; left in place" >&2
    else
        { umount "$tmp" || { sleep 3; umount "$tmp"; }; } && rmdir "$tmp"
    fi
}
trap cleanup EXIT

# --- The tree, checked (make-bootstrap.sh's "slackware" case) ------------------------
step "Slackware's key and the tree's CHECKSUMS.md5, in $CACHE"
tree=$CACHE/slackware64-15.0
mkdir -p "$tree"
keyring=$CACHE/slackware-key.gpg
if [[ ! -s $keyring ]]; then
    # The mirror's GPG-KEY, but only the key with SLACKWARE_KEY's fingerprint
    # out of it (as in-guest.sh fetches it when slackpkg's keyring lacks it).
    curl -fsS --retry 3 -o "$tmp/GPG-KEY" "$MIRROR/GPG-KEY"
    mkdir -m 700 "$tmp/gnupg"
    GNUPGHOME=$tmp/gnupg gpg -q --batch --import "$tmp/GPG-KEY"
    GNUPGHOME=$tmp/gnupg gpg --batch --with-colons --fingerprint --list-keys "$SLACKWARE_KEY" | grep -q "^fpr:*$SLACKWARE_KEY:" ||
        die "the mirror's GPG-KEY is not Slackware's key $SLACKWARE_KEY"
    GNUPGHOME=$tmp/gnupg gpg -q --batch --export "$SLACKWARE_KEY" >"$keyring.part"
    # Its keyboxd or agent would keep $tmp busy.
    gpgconf --homedir "$tmp/gnupg" --kill all 2>/dev/null || true
    mv "$keyring.part" "$keyring"
fi
if [[ ! -s $tree/CHECKSUMS.md5.asc ]] || ! gpgv --keyring "$keyring" "$tree/CHECKSUMS.md5.asc" "$tree/CHECKSUMS.md5" 2>/dev/null; then
    curl -fsS --retry 3 -o "$tree/CHECKSUMS.md5" "$MIRROR/CHECKSUMS.md5"
    curl -fsS --retry 3 -o "$tree/CHECKSUMS.md5.asc" "$MIRROR/CHECKSUMS.md5.asc"
fi
gpgv --keyring "$keyring" "$tree/CHECKSUMS.md5.asc" "$tree/CHECKSUMS.md5" 2>/dev/null ||
    die "$tree/CHECKSUMS.md5 is not signed by Slackware's key"

# setup's order: by series as listed, by name within one; the other kernel
# left out.  The same list make-bootstrap.sh builds for slackware15-uefi.
series_re=$(tr ' ' '|' <<<"$SLACKWARE_SERIES")
grep -E "  \./slackware64/($series_re)/[^/]+\.txz\$" "$tree/CHECKSUMS.md5" | sed 's|  \./|  |' >"$tmp/sums"
[[ -s $tmp/sums ]] || die "CHECKSUMS.md5 lists no packages in series $SLACKWARE_SERIES"
for sr in $SLACKWARE_SERIES; do
    awk -v s="slackware64/$sr/" 'index($2, s) == 1 { print $2 }' "$tmp/sums" | sort
done | grep -v "/kernel-$OTHER_KERNEL-[^/]*\.txz\$" >"$tmp/pkglist.txt"
cp "$tmp/pkglist.txt" "$CACHE/pkglist.txt"
npkg=$(wc -l <"$tmp/pkglist.txt")

step "fetching what the cache lacks of $npkg packages"
missing=$(cd "$tree" && while read -r p; do [[ -f $p && -f $p.asc ]] || echo "$p"; done <"$tmp/pkglist.txt")
if [[ -n $missing ]]; then
    echo "downloading $(wc -l <<<"$missing") packages and their .ascs from $MIRROR"
    # One file a curl, six at a time; each lands under its .part name first.
    sed 'p; s|$|.asc|' <<<"$missing" | (cd "$tree" && xargs -P 6 -I{} sh -c '
        mkdir -p "$(dirname "$1")" &&
        curl -fsS --retry 3 -o "$1.part" "$2/$1" && mv "$1.part" "$1"' _ {} "$MIRROR") ||
        die "could not download Slackware's packages"
fi
step "checking $npkg packages (MD5 from CHECKSUMS.md5, then each .asc)"
declare -A want_md5
while read -r h p; do want_md5[$p]=$h; done <"$tmp/sums"
pkg_ok() {
    [[ $(md5sum <"$tree/$1") == "${want_md5[$1]}  -" ]] && gpgv --keyring "$keyring" "$tree/$1.asc" "$tree/$1" 2>/dev/null
}
# A cached package that fails (a download cut short, the backup drive's bad
# sector) would fail every later run: it and its .asc are fetched again,
# once, and checked again.
refetched=0
while read -r p; do
    pkg_ok "$p" && continue
    echo "$tree/$p: its MD5 or .asc doesn't check; fetching both again"
    rm -f "$tree/$p" "$tree/$p.asc"
    for f in "$p" "$p.asc"; do
        curl -fsS --retry 3 -o "$tree/$f.part" "$MIRROR/$f" </dev/null || die "could not download $MIRROR/$f"
        mv "$tree/$f.part" "$tree/$f"
    done
    pkg_ok "$p" || die "$tree/$p, fetched again, doesn't match CHECKSUMS.md5 or isn't signed by Slackware's key"
    refetched=$((refetched + 1))
done <"$tmp/pkglist.txt"
echo "Slackware: $npkg packages checked ($refetched fetched again)"
pkg_file() { grep -m1 "/$1-[^/]*\.txz\$" "$tmp/pkglist.txt"; }

# --- The disk ---------------------------------------------------------------------------
# The old ESP's (and every old partition's) PARTUUIDs, for the stale NVRAM
# entries below.
mapfile -t old_partuuids < <(lsblk -lnpo NAME "$DISK" | tail -n +2 | xargs -r -n1 blkid -s PARTUUID -o value | tr 'A-Z' 'a-z')
step "erasing $DISK ($SERIAL, $(lsblk -dno SIZE,MODEL "$DISK"))"
lsblk -o NAME,SIZE,FSTYPE,LABEL,PARTUUID "$DISK"
# Anything still open on it, from the top down (active swap is a mount
# point, refused above): first LVM's VGs on any of its nodes (a partition,
# or a LUKS mapping: LVM on LUKS), then the LUKS mappings, deepest first
# (lsblk lists a parent before its children), then the VGs once more, for
# LVM below LUKS, whose first vgchange its open mapping refused.  Then
# nothing but the disk and its partitions.
vgs_on_disk() { lsblk -lnpo NAME "$DISK" | xargs -r pvs --noheadings -o vg_name 2>/dev/null | awk 'NF { print $1 }' | sort -u; }
for vg in $(vgs_on_disk); do vgchange -an "$vg" || echo "$vg: again once the LUKS mappings are closed"; done
for m in $(lsblk -lnpo NAME,TYPE "$DISK" | awk '$2 == "crypt" && !seen[$1]++ { print $1 }' | tac); do cryptsetup close "$m"; done
for vg in $(vgs_on_disk); do vgchange -an "$vg"; done
left=$(lsblk -lnpo NAME,TYPE "$DISK" | awk -v d="$DISK" '$1 != d && $2 != "part" { print $1 }' | sort -u | paste -sd ' ' -)
[[ -z $left ]] || die "still open on $DISK: $left"
mapfile -t nodes < <(lsblk -lnpo NAME "$DISK" | sort -r)
wipefs -q -a "${nodes[@]}"
# Zero the first 4 MiB and the last 1 MiB (old GPT backup header, signatures).
sectors=$(blockdev --getsz "$DISK")
dd if=/dev/zero of="$DISK" bs=1M count=4 conv=fsync status=none
dd if=/dev/zero of="$DISK" bs=512 seek=$((sectors - 2048)) count=2048 conv=fsync status=none

step "partitioning: the ESP ($ESP_SIZE) and / (the rest)"
# bootstrap/common.sh's LAYOUT uefi, without its swap partition.
sfdisk -q "$DISK" <<EOF
label: gpt
size=$ESP_SIZE, type=uefi
type=linux
EOF
udevadm settle
for _ in 1 2 3 4 5; do [[ -e $ROOT ]] && break; sleep 1; done
[[ -b $ESP && -b $ROOT ]] || die "no $ESP and $ROOT after partitioning"
for p in "$ESP" "$ROOT"; do dd if=/dev/zero of="$p" bs=1M count=4 conv=fsync status=none; done

step "file systems, with Slackware's own mke2fs.conf"
# bootstrap/slackware.sh's ext4 arm: the tree's e2fsprogs's mke2fs.conf.
e2p=$(pkg_file e2fsprogs)
tar -xJOf "$tree/$e2p" etc/mke2fs.conf.new >"$tmp/slk-mke2fs.conf"
grep -q '^[[:space:]]*ext4 = {' "$tmp/slk-mke2fs.conf" || die "no ext4 in $e2p's mke2fs.conf"
mkfs.fat -F 32 -n EFI "$ESP" >/dev/null
MKE2FS_CONFIG=$tmp/slk-mke2fs.conf mkfs.ext4 -q -F "$ROOT"
features=$(dumpe2fs -h "$ROOT" 2>/dev/null | sed -n 's/^Filesystem features:[[:space:]]*//p')
echo "ext4 features: $features"
[[ " $features " != *" orphan_file "* ]] || die "/ has orphan_file, which Slackware 15's e2fsck refuses"
udevadm settle
mount -t ext4 "$ROOT" "$T"
mkdir -p "$T/boot/efi"
mount -t vfat "$ESP" "$T/boot/efi"

# --- The packages (bootstrap/slackware.sh) ----------------------------------------------
step "installpkg --root of $npkg packages"
# Slackware's pkgtools, unpacked beside the rescue's own tools, with
# Slackware's tar (1.34) for it: Ubuntu 26.10's tar 1.35 won't extract
# through a symbolic link to a directory even with --keep-directory-symlink
# ("Cannot open: Invalid cross-device link"), so every package's
# var/log/setup/ files (var/log/setup -> ../lib/pkgtools/setup) were lost,
# unseen: installpkg drops tar's errors.  And installpkg runs each doinst.sh
# with "sh" from PATH: Slackware's (and the Arch live system's, where the
# guests are built) is bash; the rescue's is dash.
pkgtools=$(pkg_file pkgtools) slktar=$(pkg_file tar)
mkdir -p "$tmp/pkgtools" "$tmp/bin"
tar -xJf "$tree/$pkgtools" -C "$tmp/pkgtools"
tar -xJf "$tree/$slktar" -C "$tmp/pkgtools"
installpkg=$tmp/pkgtools/sbin/installpkg
[[ -x $installpkg ]] || die "no sbin/installpkg in $pkgtools"
ln -s "$(command -v bash)" "$tmp/bin/sh"
# --numeric-owner: as root, tar gives each file the owner its archive names
# by the rescue's passwd and group (Ubuntu's daemon is 1, Slackware's 2; its
# lp 7, Slackware's 4); the archives' numbers are Slackware's own.  Last,
# after installpkg's own arguments: its "tar tf -" needs tf first.
printf '#!/bin/sh\nexec %q "$@" --numeric-owner\n' "$tmp/pkgtools/bin/tar" >"$tmp/bin/tar"
chmod 755 "$tmp/bin/tar"
PATH=$tmp/bin:$PATH tar --version | head -n 1 | grep -q ' 1\.34$' || die "Slackware's tar ($slktar) doesn't run here"
t0=$(date +%s)
n=0
while read -r p; do
    # --terse: one line a package, as setup's "terse" mode.
    PATH=$tmp/bin:$PATH "$installpkg" --root "$T" --terse "$tree/$p" </dev/null
    n=$((n + 1))
done <"$tmp/pkglist.txt"
echo "installed $n packages in $(( $(date +%s) - t0 )) s"
# The files the packages list (installpkg doesn't say when tar fails).  A
# few go in their own doinst.sh (glibc's lib64/incoming/, say): listed, not
# fatal; setup's scripts must be there.
lost=$(cd "$T" && awk 'FNR == 1 { on = 0 } /^FILE LIST:/ { on = 1; next } on && !/^(\.\/|install\/|install$)/ && !/\.new$/' var/lib/pkgtools/packages/* |
    while read -r f; do [[ -e $f || -L $f ]] || echo "$f"; done)
lost=$(sort -u <<<"$lost" | grep -v '^lib64/incoming/' || true)
[[ -z $lost ]] || echo "listed by the packages but not here: $(paste -sd ' ' - <<<"$lost")"
! grep -q '^var/log/setup/' <<<"$lost" || die "setup's scripts in var/log/setup weren't installed"
kver=$(ls "$T/lib/modules")
[[ $(wc -w <<<"$kver") -eq 1 ]] || die "expected one kernel in /lib/modules, found: $kver"
[[ -e $T/boot/vmlinuz-$KERNEL-$kver ]] || { ls -l "$T/boot"; die "no /boot/vmlinuz-$KERNEL-$kver"; }

# Commands in the new system, with Slackware's PATH; a chroot with the
# rescue's /dev, /proc, /sys and /run (arch-chroot's, by hand).  Each bind
# made a slave at once: a bind of the rescue's shared /dev is itself shared,
# in /dev's peer group, private tmpfs or not, so the dev/pts bind below it
# would show up on the rescue's /dev/pts as well (and a pty an SSH login
# got from that copy keeps umount -R busy).  A slave takes the rescue's
# mounts and unmounts but sends nothing back.
for d in dev dev/pts proc sys run; do
    mkdir -p "$T/$d"
    mount --bind "/$d" "$T/$d"
    mount --make-rslave "$T/$d"
done
in_slk() { chroot "$T" /usr/bin/env -i HOME=/root TERM=dumb PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin "$@"; }

step "setup's SeTconfig, the part that asks nothing"
in_slk /sbin/ldconfig
# A doinst.sh run from the new root without a chroot resolves the owners in
# its chown lines by the rescue's /etc/passwd and /etc/group, not
# Slackware's (etc's "chown root.utmp", "root.shadow"; at's daemon; mlocate's
# slocate, which the rescue lacks): those packages' doinst.sh again, now in
# the chroot, as upgradepkg would run them (their chmods after the chowns).
touch "$tmp/doinst-again"
for f in $(grep -lE '^[[:space:]]*(chown|chgrp)[[:space:]]' "$T"/var/lib/pkgtools/scripts/* || true); do
    echo "again in the chroot: ${f##*/}'s doinst.sh"
    # (Its config() lines complain of the .new files the first run moved.)
    in_slk sh -c "cd / && sh /var/lib/pkgtools/scripts/${f##*/}" </dev/null 2>/dev/null
done
# at's preserve_perms leaves an empty rc.atd.new the second time.
find "$T/etc" "$T/var" -xdev -name '*.new' -size 0 -newer "$tmp/doinst-again" -print -delete | sed "s|^$T|    removed the empty |"
in_slk ls -ln /etc/shadow /var/run/utmp /usr/bin/mlocate | awk '{ print "    " $1, $3, $4, $NF }'
# bootstrap/slackware.sh: installpkg --root ran the doinst.shs from the new
# root without a chroot; setup then runs these four var/log/setup scripts
# from it.  bash, as above.
for s in setup.07.update-desktop-database setup.07.update-mime-database setup.08.gtk-update-icon-cache setup.11.cacerts; do
    [[ -f $T/var/log/setup/$s ]] || die "no var/log/setup/$s"
    (cd "$T" && PATH=$tmp/bin:$PATH bash "var/log/setup/$s" "$T" "$ROOT")
done
[[ -s $T/etc/ssl/certs/ca-certificates.crt ]] || die "setup.11.cacerts made no /etc/ssl/certs/ca-certificates.crt"

step "fstab (device names, as setup writes them)"
{
    printf '%-16s %-16s %-11s %-16s %s\n' "$ROOT" / ext4 defaults "1   1"
    printf '%-16s %-16s %-11s %-16s %s\n' "$ESP" /boot/efi vfat defaults "1   0"
    printf '%-16s %-16s %-11s %-16s %s\n' "#/dev/cdrom" /mnt/cdrom auto "noauto,owner,ro,comment=x-gvfs-show" "0   0"
    printf '%-16s %-16s %-11s %-16s %s\n' /dev/fd0 /mnt/floppy auto noauto,owner "0   0"
    printf '%-16s %-16s %-11s %-16s %s\n' devpts /dev/pts devpts gid=5,mode=620 "0   0"
    printf '%-16s %-16s %-11s %-16s %s\n' proc /proc proc defaults "0   0"
    printf '%-16s %-16s %-11s %-16s %s\n' tmpfs /dev/shm tmpfs nosuid,nodev,noexec "0   0"
} >"$T/etc/fstab"
cat "$T/etc/fstab"

step "netconfig: $HOST.$DOMAIN, DHCP on the wired adapters"
echo "$HOST.$DOMAIN" >"$T/etc/HOSTNAME"
sed -i "s/^127\.0\.0\.1.*/127.0.0.1\t\tlocalhost $HOST.$DOMAIN $HOST/" "$T/etc/hosts"
# Slackware names interfaces eth0... in the kernel's order, and its udev's
# rule generator pins them by MAC at first boot.  This machine's wired
# adapters (the rescue's view: PCI ones first, then USB) are pinned now, so
# the onboard one is eth0 whatever probes first; DHCP on eth0-eth2, which
# rc.inet1.conf has slots for (a slot whose adapter has no cable gives up
# after DHCP_TIMEOUT, 15 s).
wired=()
for bus in pci usb; do
    for i in /sys/class/net/*; do
        [[ -e $i/device && ! -e $i/wireless && ! -e $i/phy80211 && $(cat "$i/type") == 1 ]] || continue
        [[ $(basename "$(readlink -f "$i/device/subsystem")") == "$bus" ]] && wired+=("$i")
    done
done
[[ ${#wired[@]} -gt 0 ]] || die "this machine has no wired adapter"
rules=$T/etc/udev/rules.d/70-persistent-net.rules
mkdir -p "${rules%/*}"
{
    echo "# Written by BackOtter's slackware-bootstrap.sh, in the format of"
    echo "# /lib/udev/write_net_rules: this machine's wired adapters, onboard first."
    k=0
    for i in "${wired[@]}"; do
        [[ $k -le 2 ]] || break
        mac=$(cat "$i/address") drv=$(basename "$(readlink -f "$i/device/driver")")
        echo ""
        echo "# ${i##*/} on the rescue ($drv)"
        echo "SUBSYSTEM==\"net\", ACTION==\"add\", DRIVERS==\"?*\", ATTR{address}==\"$mac\", ATTR{dev_id}==\"0x0\", ATTR{type}==\"1\", KERNEL==\"eth*\", NAME=\"eth$k\""
        k=$((k + 1))
    done
} >"$rules"
cat "$rules"
c=$T/etc/rc.d/rc.inet1.conf
for k in 0 1 2; do
    sed -i "s/^USE_DHCP\[$k\]=.*/USE_DHCP[$k]=\"yes\"/" "$c"
    grep -q "^USE_DHCP\[$k\]=\"yes\"" "$c" || die "rc.inet1.conf has no USE_DHCP[$k]"
done

step "timeconfig: $TZONE, the hardware clock in UTC"
[[ -e $T/usr/share/zoneinfo/$TZONE ]] || die "no zoneinfo for $TZONE"
ln -sf "/usr/share/zoneinfo/$TZONE" "$T/etc/localtime-copied-from"
rm -f "$T/etc/localtime"
cp "$T/usr/share/zoneinfo/$TZONE" "$T/etc/localtime"
echo UTC >"$T/etc/hardwareclock"

step "sshd, root by key, the fixed host key, user $USER_NAME"
# The services setup's menu leaves on: whatever the packages shipped
# executable.  Made sure of for sshd.
chmod 755 "$T/etc/rc.d/rc.sshd"
conf=$T/etc/ssh/sshd_config
grep -q BackShot1 "$conf" || { printf '# BackShot1: root by key only (slackware-bootstrap.sh)\nPermitRootLogin prohibit-password\n'; cat "$conf"; } >"$conf.bko"
if [[ -f $conf.bko ]]; then cat "$conf.bko" >"$conf"; rm "$conf.bko"; fi
install -d -m 700 "$T/root/.ssh"
install -m 600 "$KEYS/authorized_keys" "$T/root/.ssh/authorized_keys"
# rc.sshd makes the other host keys at the first boot (ssh-keygen -A).
rm -f "$T"/etc/ssh/ssh_host_*
install -m 600 -o 0 -g 0 "$KEYS/ssh_host_ed25519_key" "$T/etc/ssh/ssh_host_ed25519_key"
install -m 644 -o 0 -g 0 "$KEYS/ssh_host_ed25519_key.pub" "$T/etc/ssh/ssh_host_ed25519_key.pub"
# The stock shadow's root has an empty password (setup asks for one): "*",
# no password, root logs in by key.
in_slk /usr/sbin/usermod -p '*' root
groups=$(tr ',' '\n' <<<"$USER_GROUPS" | while read -r g; do grep -q "^$g:" "$T/etc/group" && echo "$g"; done | paste -sd, -)
echo "$USER_NAME's groups: users, $groups"
in_slk /usr/sbin/useradd -m -g users -G "$groups" -s /bin/bash "$USER_NAME"
in_slk /usr/bin/passwd -S root
in_slk /usr/bin/passwd -S "$USER_NAME"
in_slk /usr/sbin/sshd -t

step "slackpkg: the mirror and Slackware's key"
# bootstrap/slackware.sh: the key the tree was checked with, in root's
# keyring as slackpkg's import_gpg_key imports it.
echo "$MIRROR/" >>"$T/etc/slackpkg/mirrors"
cp "$keyring" "$T/root/slackware-key.gpg"
in_slk gpg --batch --import /root/slackware-key.gpg
rm "$T/root/slackware-key.gpg"
in_slk gpg --batch --with-colons --fingerprint --list-keys "$SLACKWARE_KEY" | grep -q "^fpr:*$SLACKWARE_KEY:" ||
    die "Slackware's key $SLACKWARE_KEY is not in root's keyring"

step "mkinitrd and ELILO"
# mkinitrd as setup's "generate an initrd" step makes it: the command
# mkinitrd_command_generator.sh prints for this root, run.
cmd=$(in_slk /usr/share/mkinitrd/mkinitrd_command_generator.sh -r -k "$kver" | grep '^mkinitrd ')
echo "mkinitrd command: $cmd"
[[ $cmd == *ext4* ]] || die "the generated mkinitrd command has no ext4"
in_slk sh -c "$cmd"
[[ -s $T/boot/initrd.gz ]] || die "mkinitrd made no /boot/initrd.gz"
listing=$(in_slk sh -c 'zcat /boot/initrd.gz | cpio -it --quiet')
# The root disk's driver (nvme: only an NVMe disk gets this far) and ext4,
# each in the initrd or built into the kernel (the generic kernel has nvme
# and nvme-core built in).
for m in nvme ext4; do
    if grep -q "/$m\.ko" <<<"$listing"; then echo "$m: in the initrd"
    elif grep -q "/$m\.ko\$" "$T/lib/modules/$kver/modules.builtin"; then echo "$m: built into the kernel"
    else die "$m is neither in the initrd nor built into $kver"
    fi
done
# eliloconfig's files (elilo 3.16's usr/sbin/eliloconfig) in EFI/Slackware,
# for a generic kernel with an initrd; EFI/BOOT gets the same, as the guest's
# ESP has them, should the NVRAM entry be lost.
for d in Slackware BOOT; do
    e=$T/boot/efi/EFI/$d
    mkdir -p "$e"
    if [[ $d == BOOT ]]; then
        cp -a "$T/boot/elilo-x86_64.efi" "$e/BOOTX64.EFI"
    else
        cp -a "$T/boot/elilo-x86_64.efi" "$e/elilo.efi"
    fi
    cp "$T/boot/vmlinuz-$KERNEL-$kver" "$e/vmlinuz"
    touch -r "$T/boot/vmlinuz-$KERNEL-$kver" "$e/vmlinuz"
    cp "$T/boot/initrd.gz" "$e/initrd.gz"
    touch -r "$T/boot/initrd.gz" "$e/initrd.gz"
    cat >"$e/elilo.conf" <<EOF
chooser=simple
delay=1
timeout=1
#
image=vmlinuz
        label=vmlinuz
        initrd=initrd.gz
        read-only
        append="root=$ROOT vga=normal ro"
EOF
done

step "the NVRAM entry"
# Gone: entries for the old partitions of this disk (the system it held),
# and an earlier run's "Slackware" for it.
stale=()
for u in "${old_partuuids[@]}"; do
    [[ -n $u ]] || continue
    mapfile -t -O "${#stale[@]}" stale < <(efibootmgr -v | tr 'A-Z' 'a-z' | grep -E '^boot[0-9a-f]{4}' | grep -F "gpt,$u," | cut -c5-8 || true)
done
# And any "Slackware" whose partition is on no disk here (an interrupted run's).
present=" $(lsblk -lnpo PARTUUID | tr 'A-Z' 'a-z' | paste -sd ' ' -) "
while read -r b u; do
    [[ $present == *" $u "* ]] || stale+=("$b")
done < <(efibootmgr -v | tr 'A-Z' 'a-z' | sed -nE 's/^boot([0-9a-f]{4})\*? slackware\thd\([0-9]+,gpt,([0-9a-f-]+),.*/\1 \2/p' || true)
for b in $(printf '%s\n' "${stale[@]}" | sort -u); do
    echo "removing Boot${b^^}: $(efibootmgr | grep -i "^Boot$b" | cut -c10- | cut -f1) (the disk's old partitions)"
    efibootmgr -q -b "$b" -B
done
# eliloconfig's: efibootmgr -c on the ESP's disk and partition, "Slackware";
# -c puts it first in BootOrder.
efibootmgr -q -c -d "$DISK" -p 1 -L Slackware -l '\EFI\Slackware\elilo.efi'
partuuid=$(blkid -s PARTUUID -o value "$ESP" | tr 'A-Z' 'a-z')
num=$(efibootmgr -v | tr 'A-Z' 'a-z' | grep -F "gpt,$partuuid," | grep -F 'efi\slackware\elilo.efi' | head -n 1 | cut -c5-8)
[[ -n $num ]] || die "no NVRAM entry for EFI/Slackware/elilo.efi on $ESP ($partuuid)"
num=${num^^}
efibootmgr -q -n "$num"
order=$(efibootmgr | sed -n 's/^BootOrder: //p')
[[ ${order%%,*} == "$num" ]] || die "Boot$num isn't first in BootOrder ($order)"

step "facts"
lsblk -o NAME,SIZE,FSTYPE,PARTTYPE,PARTUUID,UUID "$DISK"
cat "$T/etc/slackware-version"
echo "kernel: $kver ($KERNEL); $(ls "$T/var/lib/pkgtools/packages" | wc -l) packages in /var/lib/pkgtools/packages"
cat "$T/boot/efi/EFI/Slackware/elilo.conf"
find "$T/boot/efi" -type f | sed "s|^$T||" | sort
grep -E '/(nvme|ext4|jbd2|mbcache|crc32c)[^/]*\.ko' <<<"$listing" | sed 's|^|    initrd: |'
efibootmgr | grep -E '^Boot(Current|Next|Order)|Slackware'
ls -l "$T"/etc/rc.d/rc.* | awk '$1 ~ /x/ { print "    on: " $NF }' | sed "s|$T||"
grep -E '^USE_DHCP\[' "$T/etc/rc.d/rc.inet1.conf"
cat "$T/etc/HOSTNAME"; readlink "$T/etc/localtime-copied-from"
ssh-keygen -lf "$T/etc/ssh/ssh_host_ed25519_key.pub"
head -n 2 "$T/etc/ssh/sshd_config"
df -h "$T" "$T/boot/efi"

step "unmounting"
trap - EXIT
unmount_target
umount "$tmp"
rmdir "$tmp"
sync
echo BKO-SLACKWARE-INSTALL-OK
