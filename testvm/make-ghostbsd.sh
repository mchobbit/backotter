#!/usr/bin/env bash
# Build a GhostBSD test guest for BackOtter: an unattended install from the
# official ISO into a qcow2 disk image, under QEMU with KVM.  GhostBSD is
# FreeBSD with a desktop; its ISO boots a live desktop, whose installer,
# Install Station, is graphical only.  So this logs in as root on the live
# system's serial console and runs Install Station's backend, pc-sysinstall,
# with a configuration of ours (ghostbsd/pcinstall.cfg, run by
# ghostbsd/install.sh from the ISO), which
# copies the live system onto the disk as Install Station does, and then
# removes the desktop (ghostbsd/post-install.sh).
#
# Usage: ./make-ghostbsd.sh VERSION ROOT_FS WORK_DIR
#   e.g. ./make-ghostbsd.sh 26.2-R15.1p6 ufs vms/ghostbsd26
#        ./make-ghostbsd.sh 26.2-R15.1p6 zfs vms/ghostbsd26-zfs
#
# VERSION is a release in https://download.ghostbsd.org/releases/amd64/,
# installed from its MATE ISO (GhostBSD-VERSION.iso).  ROOT_FS is ufs or zfs,
# what Install Station offers for /.  GhostBSD signs nothing: the ISO is
# checked against the .sha256 file published beside it, fetched over HTTPS.
# Both are downloaded into WORK_DIR unless already there.
# Results, in WORK_DIR (N is the major version, e.g. 26):
#   ghostbsdN.qcow2       the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" of the finished install
#   root-password         root's password (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the live system's serial console during the install
#   vm-run-options        with CPUS or MEMORY set: "--cpus N --memory MB",
#                         how vm-run must boot it
#
# The guest, host name gbsdN: BIOS + GPT, freebsd-boot, / (ROOT_FS, all but
# the swap: UFS2 with soft updates and TRIM; for zfs, pool "zroot" with boot
# environments and Install Station's datasets), swap (2 GB, last:
# pc-sysinstall wants / first), 2 CPUs, 2 GB RAM, DHCP and sshd.  It is the
# live system without the desktop: of its packages only the base system
# (GhostBSD-*), pkg and the repositories' configuration stay, 0.9 GB of the
# live system's 6.8 (see ghostbsd/post-install.sh).  Its console is serial
# and screen both.  It updates with pkg, the base system included (GhostBSD's
# repository GhostBSD-base), as GhostBSD's Update Station does.  Run it with
# vm-run (and vm-run-options).
# vm-mount can't open it: Linux can't write UFS, and the ZFS pool would have
# to be imported on this host.
#
# The live system copies itself into a RAM disk of 75% of the VM's memory,
# and wants 4 GB at least: the install runs with INSTALL_MEMORY (MB, 8192).
# A bigger guest from the environment: DISK_SIZE (16G), CPUS (2), MEMORY
# (MB, 2048).
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, xorriso, openssl, wget,
# sha256sum, mkfifo.  Runs as an ordinary user in the kvm group.

set -euo pipefail

DISK_SIZE=${DISK_SIZE:-16G}
CPUS=${CPUS:-2}
MEMORY=${MEMORY:-2048}
INSTALL_MEMORY=${INSTALL_MEMORY:-8192}
MIRROR=https://download.ghostbsd.org/releases/amd64
INSTALL_TIMEOUT=3600
# Install Station's ZFS datasets (install_station/data.py, zfs_datasets).
ZFS_DATASETS='/,/home(mountpoint=/home),/tmp(mountpoint=/tmp|exec=on|setuid=off),/usr(mountpoint=/usr|canmount=off),/usr/ports(setuid=off),/usr/src,/var(mountpoint=/var|canmount=off),/var/audit(exec=off|setuid=off),/var/crash(exec=off|setuid=off),/var/log(exec=off|setuid=off),/var/mail(atime=on),/var/tmp(setuid=off)'

here=$(dirname "$(realpath "$0")")
die() { echo "make-ghostbsd: $*" >&2; exit 1; }

[[ $# -eq 3 ]] || die "usage: $0 VERSION ROOT_FS WORK_DIR"
version=$1 root_fs=$2
[[ $version =~ ^[0-9]+\.[0-9]+-R[0-9]+\.[0-9]+p[0-9]+$ ]] || die "VERSION looks like 26.2-R15.1p6, not '$version'"
case $root_fs in
    ufs) root_part="UFS+S 0 /" ;;
    zfs) root_part="ZFS 0 $ZFS_DATASETS" ;;
    *) die "ROOT_FS must be ufs or zfs" ;;
esac
major=${version%%.*}
guest_name=gbsd$major
DISK=ghostbsd$major.qcow2
ISO=GhostBSD-$version.iso
mkdir -p "$3"
cd "$3"
[[ -e $DISK ]] && die "$3/$DISK already exists; move it away to rebuild"

# --- Fetch and verify the ISO -------------------------------------------------------
download() {   # URL FILE
    [[ -f $2 && ! -f $2.part ]] && return
    echo "Downloading $1"
    touch "$2.part"
    wget -c -q -O "$2" "$1" || die "could not download $1"
    rm "$2.part"
}
download "$MIRROR/$version/$ISO.sha256" "$ISO.sha256"
download "$MIRROR/$version/$ISO" "$ISO"
want=$(sed -n "s/^SHA256 ($ISO) = \([0-9a-f]\{64\}\)\$/\1/p" "$ISO.sha256")
[[ -n $want ]] || die "$ISO.sha256 lists no SHA256 for $ISO"
echo "$want  $ISO" | sha256sum -c --quiet - || die "$ISO does not match $ISO.sha256"

# --- Install media, with the install scripts added -------------------------------
work=$(mktemp -d)
media=$(mktemp -p . --suffix=.iso install-media.XXXXXX)
qemu="" catpid=""
cleanup() {
    [[ -z $qemu ]] || kill "$qemu" 2>/dev/null || true
    [[ -z $catpid ]] || kill "$catpid" 2>/dev/null || true
    rm -rf "$work" "$media"
}
trap cleanup EXIT

umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$guest_name test guest" -f id_ed25519
umask 022

mkdir "$work/backotter"
sed -e "s|@ROOT_PASSWORD_HASH@|$hash|" -e "s|@AUTHORIZED_KEY@|$(cat id_ed25519.pub)|" \
    -e "s/@ROOT_FS@/$root_fs/" "$here/ghostbsd/post-install.sh" >"$work/backotter/post-install.sh"
sed -e "s/@HOSTNAME@/$guest_name/" -e "s/@ROOT_FS@/$root_fs/" -e "s#@ROOT_PART@#$root_part#" \
    "$here/ghostbsd/pcinstall.cfg" >"$work/backotter/pcinstall.cfg"
cp "$here/ghostbsd/install.sh" "$work/backotter/"
if grep -n '@[A-Z_]\+@' "$work"/backotter/*; then die "placeholders left unfilled"; fi
xorriso -osirrox on -indev "$ISO" -extract /boot/loader.conf "$work/loader.conf" 2>/dev/null ||
    die "$ISO lacks /boot/loader.conf"
chmod u+w "$work/loader.conf"
# The loader, the kernel and a login prompt on the serial console, where
# QEMU logs it and this script types; and the boot's messages shown, not
# muted as GhostBSD has them.
printf '%s\n' 'boot_multicons="YES"' 'console="comconsole,vidconsole"' 'autoboot_delay="1"' \
    'boot_mute="NO"' >>"$work/loader.conf"
rm -f "$media"
# The ISO's own El Torito images are hidden outside its file tree, so xorriso
# can't carry them over; boot the copy from /boot/cdboot instead (BIOS only,
# as the guest is).  The live system's boot looks for the volume GHOSTBSD.
xorriso -indev "$ISO" -outdev "$media" -volid GHOSTBSD \
    -boot_image any discard -boot_image any bin_path=/boot/cdboot \
    -boot_image any emul_type=no_emulation -boot_image any load_size=2048 \
    -map "$work/backotter" /backotter -map "$work/loader.conf" /boot/loader.conf \
    -chown_r 0 /backotter /boot/loader.conf -- -chgrp_r 0 /backotter /boot/loader.conf -- \
    -chmod_r 0644 /backotter /boot/loader.conf -- -chmod 0755 /backotter -- \
    2>"$work/xorriso.log" || { cat "$work/xorriso.log" >&2; die "could not write the install media"; }

# --- Install ----------------------------------------------------------------------
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing; the live system's console goes to $3/install-serial.log"
# The console through a pair of FIFOs (console.in, console.out), so this
# script can type on it; QEMU logs it too.  install.sh reboots when it's
# done, which -no-reboot turns into QEMU exiting; it powers off if the
# install failed.  discard: what the desktop's removal frees leaves the
# image.  -vga none: the live system's boot ends by starting X with
# Install Station and waits for it, so no getty would start on the serial
# line; without a display X fails at once, and the boot goes on.
console=$work/console
mkfifo "$console.in" "$console.out"
: >install-serial.log
timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp "$CPUS" -m "$INSTALL_MEMORY" -machine pc \
    -drive "file=$DISK,if=virtio,format=qcow2,discard=unmap,detect-zeroes=unmap" \
    -drive "file=$media,media=cdrom,readonly=on" -boot order=d \
    -nic user,model=virtio-net-pci \
    -chardev "pipe,id=console,path=$console,logfile=install-serial.log" -serial chardev:console \
    -vga none -display none -monitor none -no-reboot &
qemu=$!
# Read what QEMU writes to the console, or it stops when the pipe is full.
# Both FIFOs opened read-write, which never blocks: a QEMU that died or
# never started can't leave this script (or the cat) waiting on an open.
cat 0<>"$console.out" >/dev/null &
catpid=$!
exec 3<>"$console.in"
wait_for() {   # PATTERN: until the console shows it, while QEMU runs
    until grep -aq -- "$1" install-serial.log; do
        kill -0 "$qemu" 2>/dev/null || return 1
        sleep 3
    done
}
send() { printf '%s\r' "$1" >&3; }
# getty's banner on the serial line, "FreeBSD/amd64 (livecd) (ttyu0)", then
# "login:".  Root has no password on the live system.  The command waits for
# root's prompt: before it, resizewin reads the terminal for a while.
wait_for '(ttyu0)' || die "the live system didn't boot; see install-serial.log"
sleep 5
send root
wait_for 'root@livecd:' || die "the live system didn't log root in; see install-serial.log"
send 'mkdir -p /tmp/bko-cd && mount_cd9660 /dev/iso9660/GHOSTBSD /tmp/bko-cd && sh /tmp/bko-cd/backotter/install.sh'
rc=0
wait "$qemu" || rc=$?
qemu=""
[[ $rc -eq 0 ]] || die "the install failed or timed out; see install-serial.log"
grep -aq '^BACKOTTER-INSTALL: OK' install-serial.log ||
    die "the install failed; see install-serial.log"

qemu-img snapshot -c fresh-install "$DISK"
[[ $CPUS == 2 && $MEMORY == 2048 ]] || echo "--cpus $CPUS --memory $MEMORY" >vm-run-options
echo "Done: $3/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
