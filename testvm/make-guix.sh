#!/usr/bin/env bash
# Build a GNU Guix System test guest for BackOtter from Guix's own
# installation ISO, unattended: the manual's manual installation (partition,
# mount on /mnt, herd start cow-store /mnt, write config.scm, guix system
# init), run by guix/install.sh in the installation image, into a qcow2 disk
# image, under QEMU with KVM.  docs/design/guix.md is the design.
#
# Usage: ./make-guix.sh [--iso-dir DIR] FLAVOR WORK_DIR
#   e.g. ./make-guix.sh guix15 vms/guix15
#
# FLAVOR is guix15 (UEFI, ext4) or guix15-bios (BIOS, Btrfs subvolumes);
# guix/install.sh describes each layout.  DIR (default
# /bkoscratch/rebuild-inputs/guix15) holds guix-system-install-1.5.0.x86_64-linux.iso,
# checked against the SHA-256 pinned below.  Its .sig was checked once, on
# download (2026-11-14): a good signature by Efraim Flashner's key
# A28BF40C3E551372662D14F741AAE7DCCA3D8351, from GNU's keyring
# (ftp.gnu.org/gnu/gnu-keyring.gpg, kept beside the ISO).  The install takes
# the release's Guix and its substitutes as they are at the build; vm-update
# then brings the guest to the channel's head, a second generation.
#
# The installation image has no way to run a command at boot, and nothing
# on its serial console (no getty there: its Shepherd config has term-tty1 to
# tty6 only, the installer on tty1, the manual on tty2).  It has a root login
# with no password on tty3: QEMU boots the ISO's own kernel and initrd (from
# its grub.cfg, plus console=ttyS0 for the kernel's messages), and
# guix/console-type.py logs in there by the monitor's sendkey and types the
# command that fetches install.sh from 10.0.2.2, its output sent to
# /dev/ttyS0 (QEMU's user-mode network maps 10.0.2.2 to the host's
# 127.0.0.1, where a server for this build alone serves it).  The install VM
# has a virtio RNG.

# Results, in WORK_DIR:
#   NAME.qcow2            the guest's 20 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" after the first boot
#   uefi-vars.fd          (guix15) the install's OVMF variables, with Guix's
#                         NVRAM entry; for OVMF_CODE_4M.fd on a plain q35
#                         (vm-run --uefi --no-secure-boot --uefi-vars COPY)
#   vm-run-options        how vm-run must boot it
#   root-password         the password of root and of "tester" (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the installation image's serial console
#   facts/                what install.sh recorded (config.scm, grub.cfg,
#                         parameters, the layout, the ESP's files, ...)
#   first-boot.txt, first-boot-serial.log
#                         the first boot alone: herd status, mounts, swap,
#                         generations
#
# Needs: qemu-system-x86_64 with KVM, OVMF, qemu-img, xorriso, openssl,
# python3, ssh.  Runs as an ordinary user in the kvm group.

set -euo pipefail

ISO_NAME=guix-system-install-1.5.0.x86_64-linux.iso
ISO_SHA256=107e0a8082f03a10b15c1fb9383d2d752c1cdeda41b8db575a15550e1c2d8b4a
DISK_SIZE=20G
INSTALL_MEMORY=4096
OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd
INSTALL_TIMEOUT=${INSTALL_TIMEOUT:-5400}
USERNAME=tester

here=$(dirname "$(realpath "$0")")
die() { echo "make-guix: $*" >&2; exit 1; }

iso_dir=/bkoscratch/rebuild-inputs/guix15
[[ ${1:-} == --iso-dir ]] && { iso_dir=$2; shift 2; }
[[ $# -eq 2 ]] || die "usage: $0 [--iso-dir DIR] guix15|guix15-bios WORK_DIR"
name=$1
case $name in guix15) uefi=yes ;; guix15-bios) uefi=no ;; *) die "FLAVOR is guix15 or guix15-bios, not '$name'" ;; esac
iso=$(realpath "$iso_dir/$ISO_NAME")
[[ -f $iso ]] || die "no $ISO_NAME in $iso_dir"
DISK=$name.qcow2
mkdir -p "$2"
cd "$2"
[[ -e $DISK ]] && die "$2/$DISK already exists; move it away to rebuild"

echo "Checking $ISO_NAME's SHA-256"
[[ $(sha256sum <"$iso" | cut -d' ' -f1) == "$ISO_SHA256" ]] || die "$ISO_NAME's SHA-256 isn't the pinned $ISO_SHA256"

work=$(mktemp -d)
qemu_pid="" server=""
cleanup() {
    [[ -n $qemu_pid ]] && kill "$qemu_pid" 2>/dev/null
    [[ -n $server ]] && kill "$server" 2>/dev/null
    chmod -R u+w "$work"; rm -rf "$work"
}
trap cleanup EXIT

# --- The installation image's kernel, initrd and options -------------------------
xorriso -osirrox on -indev "$iso" -extract /boot/grub/grub.cfg "$work/grub.cfg" 2>/dev/null ||
    die "could not extract grub.cfg from $ISO_NAME"
chmod u+w "$work/grub.cfg"
read -r kernel options < <(awk '$1 == "linux" { $1 = ""; print; exit }' "$work/grub.cfg" | sed -E 's/^ +//')
initrd=$(awk '$1 == "initrd" { print $2; exit }' "$work/grub.cfg")
[[ $kernel == /gnu/store/*/bzImage && $initrd == /gnu/store/*/initrd.cpio.gz && $options == *gnu.system=* && $options == *root=* ]] ||
    die "unexpected first menu entry in the ISO's grub.cfg: linux $kernel $options; initrd $initrd"
xorriso -osirrox on -indev "$iso" -extract "$kernel" "$work/bzImage" -extract "$initrd" "$work/initrd.cpio.gz" 2>/dev/null ||
    die "could not extract $kernel and $initrd from $ISO_NAME"
chmod u+w "$work/bzImage" "$work/initrd.cpio.gz"
options=$(sed -E 's/(^| )quiet( |$)/ /g; s/  +/ /g; s/^ //; s/ $//' <<<"$options")

umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$name test guest" -f id_ed25519
umask 022

# Served to the guest only, on 127.0.0.1 (10.0.2.2 in the guest).
port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
url=http://10.0.2.2:$port
mkdir "$work/www"
sed -e "s|@FLAVOR@|$name|" -e "s|@HOSTNAME@|$name|" -e "s|@USERNAME@|$USERNAME|" -e "s|@PASSWORD_HASH@|$hash|" \
    -e "s|@AUTHORIZED_KEY@|$(cat id_ed25519.pub)|" "$here/guix/install.sh" >"$work/www/install.sh"
if grep -n '@[A-Z_]\+@' "$work/www/install.sh"; then die "install.sh placeholders left unfilled"; fi
python3 -m http.server --bind 127.0.0.1 --directory "$work/www" "$port" >"$work/http.log" 2>&1 &
server=$!

# What console-type.py types on tty3, once logged in: everything after it
# goes to the serial console.  The image's connman brings the network up on
# its own; the loop waits for it.
echo "exec >/dev/ttyS0 2>&1; until wget -q -O /tmp/i.sh $url/install.sh; do sleep 2; done; sh /tmp/i.sh" >"$work/command"

# --- Install --------------------------------------------------------------------------
fw=()
if [[ $uefi == yes ]]; then
    cp "$OVMF_VARS" uefi-vars.fd
    fw=(-machine q35 -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE"
        -drive "if=pflash,format=raw,unit=1,file=$PWD/uefi-vars.fd")
fi
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
truncate -s 64M "$work/facts.raw"
echo "Installing; the installation image's console goes to $2/install-serial.log"
t0=$(date +%s)
timeout "$INSTALL_TIMEOUT" python3 "$here/guix/console-type.py" install-serial.log "$work/monitor" "$work/command" \
    'Registering new logger for udev' -- \
    qemu-system-x86_64 -enable-kvm -cpu host -smp 4 -m "$INSTALL_MEMORY" "${fw[@]}" \
    -drive "file=$DISK,if=virtio,format=qcow2,discard=unmap" \
    -drive "file=$work/facts.raw,if=virtio,format=raw" \
    -drive "file=$iso,media=cdrom,readonly=on" \
    -nic user,ipv6=off,model=virtio-net-pci -device virtio-rng-pci \
    -kernel "$work/bzImage" -initrd "$work/initrd.cpio.gz" -append "$options console=tty0 console=ttyS0,115200n8" \
    -display none -serial stdio -monitor "unix:$work/monitor,server,nowait" -no-reboot </dev/null ||
    die "the install failed or timed out; see install-serial.log"
echo "The install took $(( $(date +%s) - t0 )) s"
rm -rf facts && mkdir facts
tar -C facts -xf "$work/facts.raw" 2>/dev/null || echo "make-guix: no facts on the facts disk" >&2
grep -aq BACKOTTER-INSTALL-OK install-serial.log || {
    grep -a BACKOTTER-INSTALL-FAILED install-serial.log >&2 || true
    die "the install did not finish; see install-serial.log"
}
kill "$server" 2>/dev/null; server=""

# --- First boot, alone ---------------------------------------------------------------
# vm-run's fresh variable store for guix15: the fallback path, EFI/BOOT.
if [[ $uefi == yes ]]; then opts=(--uefi --no-secure-boot); else opts=(); fi
sport=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
ssh_root=(ssh -q -i id_ed25519 -p "$sport" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
    -o ConnectTimeout=5 -o ServerAliveInterval=30 root@127.0.0.1)
SSH_PORT=$sport setsid "$here/vm-run" "${opts[@]}" --serial-log first-boot-serial.log "$DISK" </dev/null >"$work/vm-run.log" 2>&1 &
qemu_pid=$!
up=no
for _ in $(seq 120); do
    kill -0 "$qemu_pid" 2>/dev/null || break
    "${ssh_root[@]}" true </dev/null 2>/dev/null && { up=yes; break; }
    sleep 5
done
[[ $up == yes ]] || { cat "$work/vm-run.log" >&2; die "the installed system never answered on SSH; see first-boot-serial.log"; }
"${ssh_root[@]}" 'set -x; cat /proc/cmdline; uname -r; cat /etc/os-release; hostname
    herd status; guix system list-generations
    for m in / /boot/efi /home /gnu /gnu/store; do findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS $m; done
    swapon --show; cat /etc/fstab; ls -l /run/current-system /var/guix/profiles
    efibootmgr -v; command -v btrfs && btrfs subvolume list -a /
    df -h / ; find /gnu/store -xdev -type f -links +1 | wc -l' </dev/null >first-boot.txt 2>&1 || true
"${ssh_root[@]}" 'sync; halt' </dev/null 2>/dev/null || true
for _ in $(seq 60); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 2; done
kill "$qemu_pid" 2>/dev/null && die "the guest did not power off"
wait "$qemu_pid" 2>/dev/null || true
qemu_pid=""
grep -q '^ID=guix$' first-boot.txt || { cat first-boot.txt >&2; die "the first boot isn't Guix System; see first-boot.txt"; }
grep -q 'ssh-daemon' first-boot.txt || die "no ssh-daemon in herd status; see first-boot.txt"

qemu-img snapshot -c fresh-install "$DISK"
echo "${opts[*]}" >vm-run-options
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
