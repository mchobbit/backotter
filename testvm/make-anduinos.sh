#!/usr/bin/env bash
# Build an AnduinOS 2 test guest for BackOtter from AnduinOS's own live ISO,
# with AnduinOS's own installer backend, unattended: the GUI's "erase disk"
# plan (Btrfs, the installer's swap, its snapshot baselines, its MOK-signed
# GRUB behind shim) made by installer_core and run by
# anduinos-installer-executor, into a qcow2 disk image, under QEMU with KVM
# and OVMF.  docs/design/anduinos.md is the design; anduinos/install.sh says
# what runs in the live system, and what the guest gets besides.
#
# Usage: ./make-anduinos.sh ISO WORK_DIR NAME
#   e.g. ./make-anduinos.sh /bkoscratch/rebuild-inputs/anduinos2/AnduinOS-2.0.5-amd64.iso vms/anduinos2 anduinos2
#
# NAME is the guest, anduinos2 (the only one).  ISO is
# AnduinOS-2.0.5-amd64.iso, checked against the SHA-256 pinned below
# (AnduinOS publishes a .sha256 beside it, from the same CDN, and no
# signature).  The ISO is the whole install: the executor copies its live
# image, with no updates at install time, so the guest is the ISO's AnduinOS
# (2026-10-25) until vm-update brings it up to date.
#
# The live system runs anduinos/install.sh from kernel-command-line.service
# (systemd.run= with systemd.wants=, so it boots on to multi-user.target and
# its network), fetched from 10.0.2.2, which QEMU's user-mode network maps to
# the host's 127.0.0.1, where a server for this build alone serves it.  The
# live system boots with Secure Boot off (OVMF's Secure Boot build with no
# keys enrolled, as a PC with it turned off): QEMU's -kernel loads the ISO's
# kernel directly, which no Secure Boot db would pass.  So the installer
# queues no MOK enrollment; it makes the MOK and signs GRUB all the same.
# The guest then boots with Secure Boot on, the MOK enrolled in its variable
# store by vm-run --mok (as MokManager would), from the removable path
# EFI/BOOT (shim, then the MOK-signed GRUB): vm-run gives every boot a fresh
# store, with no NVRAM entries.
#
# Results, in WORK_DIR:
#   NAME.qcow2            the guest's 25 GiB disk (sparse, the installer's
#                         minimum), with a qcow2 snapshot "fresh-install"
#                         taken after the first boot
#   NAME-mok.der          the guest's MOK certificate (/var/lib/shim-signed/mok/MOK.der)
#   vm-run-options        "--uefi --mok BASE64", the MOK's DER in base64
#   root-password         the password of root and of "tester" (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the live system's serial console: the executor's
#                         JSON-lines events, prefixed "executor: "
#   facts/                what install.sh recorded on the installed system
#                         (os-release, partition table, fstab, the ESP's
#                         files with SHA-256s, the subvolumes, ...)
#   first-boot.txt, first-boot-serial.log
#                         the first boot alone with Secure Boot on: mokutil,
#                         mounts, swap, the snapshot manager's status
#
# The guest anduinos2, host name anduinos2: UEFI + GPT, the installer's
# erase-disk layout (ESP at /boot/efi, a swap partition, one Btrfs with
# @root, @home, @log, @snapshots, @containers, @libvirt), GNOME installed but
# booting to text mode, sshd on, 2 GB RAM (installed with 4: the installer
# sizes swap by the RAM it sees).
#
# Needs: qemu-system-x86_64 with KVM, OVMF, qemu-img, xorriso, openssl,
# python3, ssh, virt-fw-vars (python3-virt-firmware, for vm-run --mok).
# Runs as an ordinary user in the kvm group.

set -euo pipefail

ISO_NAME=AnduinOS-2.0.5-amd64.iso
ISO_SHA256=4b5c1778bbf54be7fc41202af576072ebf0931e776c35ce1b5795deb7169e810
DISK_SIZE=25G
INSTALL_MEMORY=4096
OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.secboot.fd
OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd
INSTALL_TIMEOUT=3600
USERNAME=tester

here=$(dirname "$(realpath "$0")")
die() { echo "make-anduinos: $*" >&2; exit 1; }

[[ $# -eq 3 ]] || die "usage: $0 ISO WORK_DIR NAME"
iso=$(realpath "$1") name=$3
[[ $name == anduinos2 ]] || die "NAME is anduinos2, not '$name'"
[[ -f $iso ]] || die "no such ISO: $iso"
[[ ${iso##*/} == "$ISO_NAME" ]] || die "the ISO should be $ISO_NAME, not ${iso##*/}"
command -v virt-fw-vars >/dev/null || die "needs virt-fw-vars (apt install python3-virt-firmware)"
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

# --- The live system's kernel, initramfs and boot options ---------------------------
xorriso -osirrox on -indev "$iso" -extract /LiveOS/vmlinuz "$work/vmlinuz" \
    -extract /LiveOS/initrd "$work/initrd" -extract /boot/grub/grub.cfg "$work/grub.cfg" 2>/dev/null ||
    die "could not extract the kernel, initramfs and grub.cfg from $ISO_NAME"
chmod u+w "$work"/vmlinuz "$work"/initrd "$work"/grub.cfg
# The first menu entry (English (United States)), without quiet and splash,
# so the boot shows on the serial console, and without the closing "---"
# (Debian's marker for the installed system's options), which goes last again.
options=$(awk '$1 == "linux" && $2 == "/LiveOS/vmlinuz" { $1 = $2 = ""; print; exit }' "$work/grub.cfg" |
    sed -E 's/^ +//; s/ (quiet|splash)\b//g; s/ *---$//')
[[ $options == *root=live:CDLABEL=* && $options == *rd.live.squashimg=* && $options == *locale=en_US.UTF-8* ]] ||
    die "unexpected first menu entry in the ISO's grub.cfg: $options"

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
sed -e "s|@HOSTNAME@|$name|" -e "s|@USERNAME@|$USERNAME|" -e "s|@PASSWORD_HASH@|$hash|" \
    -e "s|@AUTHORIZED_KEY@|$(cat id_ed25519.pub)|" -e "s|@URL@|$url|" \
    "$here/anduinos/install.sh" >"$work/www/install.sh"
if grep -n '@[A-Z_]\+@' "$work/www/install.sh"; then die "install.sh placeholders left unfilled"; fi
cp "$here/anduinos/plan.py" "$work/www/plan.py"
python3 -m http.server --bind 127.0.0.1 --directory "$work/www" "$port" >"$work/http.log" 2>&1 &
server=$!

# kernel-command-line.service runs this (systemd's own unquoting; no $ or %,
# which ExecStart= would expand).  systemd.wants= pulls it into
# multi-user.target, which systemd.unit= keeps the boot's target (else
# systemd.run= would boot into the service alone, with no network).
run="until curl -fsS -o /run/bko-install.sh $url/install.sh; do sleep 2; done; exec /usr/bin/bash /run/bko-install.sh"
append="$options console=tty0 console=ttyS0,115200n8 systemd.unit=multi-user.target"
append+=" systemd.run=\"/usr/bin/bash -c '$run'\" systemd.wants=kernel-command-line.service"
append+=" systemd.run_success_action=none systemd.run_failure_action=poweroff"
# No getty on the serial console: its vhangup cuts install.sh's output off.
append+=" systemd.mask=serial-getty@ttyS0.service ---"

# --- Install --------------------------------------------------------------------------
# OVMF's Secure Boot build (SMM, as vm-run --uefi runs it) with an empty
# variable store: Secure Boot "disabled", as the installer sees a PC with it
# turned off (OVMF_CODE_4M.fd, without SMM, it calls "unsupported by
# firmware").  The facts disk (/dev/vdb in the live system) takes
# install.sh's tar.
cp "$OVMF_VARS" "$work/install-vars.fd"
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
truncate -s 64M "$work/facts.raw"
echo "Installing; the live system's console goes to $2/install-serial.log"
t0=$(date +%s)
timeout "$INSTALL_TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 4 -m "$INSTALL_MEMORY" \
    -machine q35,smm=on -global driver=cfi.pflash01,property=secure,value=on \
    -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE" \
    -drive "if=pflash,format=raw,unit=1,file=$work/install-vars.fd" \
    -drive "file=$DISK,if=virtio,format=qcow2,discard=unmap" \
    -drive "file=$work/facts.raw,if=virtio,format=raw" \
    -drive "file=$iso,media=cdrom,readonly=on" \
    -nic user,ipv6=off,model=virtio-net-pci \
    -kernel "$work/vmlinuz" -initrd "$work/initrd" -append "$append" \
    -display none -serial file:install-serial.log -monitor none -no-reboot ||
    die "the install failed or timed out; see install-serial.log"
echo "The install took $(( $(date +%s) - t0 )) s"
rm -rf facts && mkdir facts
tar -C facts -xf "$work/facts.raw" 2>/dev/null || echo "make-anduinos: no facts on the facts disk" >&2
grep -aq BACKOTTER-INSTALL-OK install-serial.log || {
    grep -a BACKOTTER-INSTALL-FAILED install-serial.log >&2 || true
    die "the install did not finish; see install-serial.log"
}
kill "$server" 2>/dev/null; server=""
[[ -s facts/MOK.der ]] || die "install.sh recorded no MOK.der"
cp facts/MOK.der "$name-mok.der"
openssl x509 -inform der -in "$name-mok.der" -noout -subject -fingerprint -sha256

# --- First boot, alone, with Secure Boot on ------------------------------------------
# A fresh variable store with Microsoft's keys and the MOK: OVMF boots the
# removable path, shim, which loads the MOK-signed GRUB.
mok=$(base64 -w0 "$name-mok.der")
opts=(--uefi --mok "$mok")
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
"${ssh_root[@]}" 'set -x; systemctl is-system-running --wait; cat /proc/cmdline; uname -r
    mokutil --sb-state; mokutil --list-enrolled | grep -E "Subject:|SHA1 Fingerprint|^\[key"
    grep -E "^(ID|ID_LIKE|VERSION_ID|VERSION)=" /etc/os-release; hostname
    for m in / /boot/efi /home /var/log /.snapshots; do findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS $m; done
    swapon --show; zramctl; ls -l /boot; efibootmgr -v
    systemctl is-enabled ssh.service anduinos-btrfs-snapshots-manager-scheduler.timer; systemctl --failed --no-legend
    sshd -T | grep -Ei "^(permitrootlogin|passwordauthentication) "
    anduinos-btrfs-snapshots-manager-cli status --json; anduinos-btrfs-snapshots-manager-cli list --json
    btrfs subvolume list -a -u -q -R /' </dev/null >first-boot.txt 2>&1 || true
"${ssh_root[@]}" 'sync; systemctl poweroff' </dev/null 2>/dev/null || true
for _ in $(seq 60); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 2; done
kill "$qemu_pid" 2>/dev/null && die "the guest did not power off"
wait "$qemu_pid" 2>/dev/null || true
qemu_pid=""
grep -Eq '^ *(running|degraded)$' first-boot.txt || { cat first-boot.txt >&2; die "the first boot isn't up"; }
grep -q '^SecureBoot enabled$' first-boot.txt || die "the first boot wasn't under Secure Boot; see first-boot.txt"
grep -Eq '^/ +/dev/vda[0-9]+\[/@root\] +btrfs ' first-boot.txt || die "/ isn't the Btrfs's @root; see first-boot.txt"

qemu-img snapshot -c fresh-install "$DISK"
echo "${opts[*]}" >vm-run-options
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
