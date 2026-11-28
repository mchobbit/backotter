#!/usr/bin/env bash
# Build a SteamOS 3 test guest for BackOtter (docs/design/steamos.md, S1)
# from Valve's recovery image with Valve's own installer, unattended
# (Decision 2): repair_device.sh all onto a blank NVMe disk, run by a
# systemd unit handed to the recovery system as an SMBIOS credential, so
# nothing is written into the image beforehand.  Then the guest's seed,
# offline, from the same VM; the first boot (slot A, 3.8.14); one OS update
# into slot B (3.9.x, the stable branch); the boot into B; both other-slot
# boots by SteamOS's own holo-reboot --reboot-other.
#
# Usage: ./make-steamos.sh [--port N] FLAVOR IMG WORK_DIR
#   e.g. ./make-steamos.sh steamos3 /bkoscratch/rebuild-inputs/steamos3/steamdeck-oobe-repair-20260707.10-3.8.14.img.bz2 vms/steamos3
#
# FLAVOR is the guest: steamos3 (the only one).
#
# IMG is Valve's recovery image, steamdeck-oobe-repair-20260707.10-3.8.14.img.bz2
# (what https://steamdeck-images.steamos.cloud/recovery/steamdeck-repair-latest.img.bz2
# redirected to on 2026-11-27), checked against the SHA-256 pinned below:
# Valve publishes no checksum or signature, so the pin is our own, from the
# first fetch (S1).  The installer's copy inside it, repair_device.sh, is
# pinned too (install.log carries it).  Nothing is downloaded here but the
# update the guest's own atomupd fetches from Valve.
#
# Licence (Decision 13, the user's call): Valve's EULA grants personal use
# and forbids copying or modifying the image, so this guest is never
# published to vm-store unless the user says so; it lives in the build
# host's cache only.
#
# The steps:
#   1. IMG decompressed into WORK_DIR (recovery.raw, ~7.6 GiB; deleted at
#      the end).  A vfat disk BKOINST holds steamos/install.sh, filled in.
#   2. The install VM: q35, OVMF without Secure Boot (OVMF_CODE_4M.fd, the
#      variable store uefi-vars.fd), the recovery copy as a virtio disk
#      (booted: steamcl, GRUB, the recovery system), the guest's qcow2 as
#      NVMe (repair_device.sh writes /dev/nvme0n1), VGA, virtio-net.  The
#      credentials: systemd.extra-unit.bko-install.service and
#      systemd.unit-dropin.multi-user.target (Wants=bko-install.service).
#      install.sh runs repair_device.sh all, then the seed (its header), and
#      the unit powers off.  steamcl-install's NVRAM entry for the NVMe disk
#      lands in uefi-vars.fd.
#   3. Boot 1, slot A: probe (steamos/probe.sh), then steamos-update check
#      and steamos-update (atomupd), a power-off.
#   4. Boot 2, the default (B, the update): probe; the mark /etc/bko-slot
#      "b" and the serial console in B's grub.cfg (the update rewrote it);
#      holo-reboot --reboot-other (one QEMU process: the flag lives on the
#      ESP) into A, probe, and back into B, probe; a power-off.
#   5. The qcow2 snapshot "fresh-install".
#
# Results, in WORK_DIR:
#   FLAVOR.qcow2          the guest's 64 GiB disk (sparse), snapshot "fresh-install"
#   uefi-vars.fd          the install's OVMF variables (steamcl's NVRAM entry),
#                         for OVMF_CODE_4M.fd on plain q35 (vm-run --uefi
#                         --no-secure-boot --guest-vars uefi-vars.fd)
#   root-password         the password of root, of the image user and of tester (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root and tester
#   ssh_host_ed25519_key.pub  the guest's host key
#   repair_device.sh      the installer's copy, from install.log
#   install.log           install.sh's output (the second serial port)
#   install-serial.log, boot1-serial.log, boot2-serial.log  the serial consoles
#   update.log            steamos-update's output
#   first-boot.txt        every probe, and the build's time
#   vm-run-options        how vm-run boots it
#
# Needs: qemu-system-x86_64 with KVM, OVMF, qemu-img, bzip2 (or lbzip2),
# mkfs.vfat and mtools, openssl, python3, ssh, and the network (Valve's
# update servers) for the guest.  Runs as an ordinary user in the kvm group.

set -euo pipefail

IMG_NAME=steamdeck-oobe-repair-20260707.10-3.8.14.img.bz2
IMG_SHA256=4254ee02ec34ae8add9aceef1881a2ce675a9d0176171df92e0eaa1bf014c594
REPAIR_SHA256=6305234a6ce0c91d67117ff72cba908b05d34d601d27d1efbe68f7bfa9111d29
DISK_SIZE=64G
MEMORY=8192
OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd
INSTALL_TIMEOUT=5400
BOOT_TIMEOUT=900
UPDATE_TIMEOUT=5400

here=$(dirname "$(realpath "$0")")
die() { echo "make-steamos: $*" >&2; exit 1; }
note() { echo "make-steamos: $*" >&2; echo "=== note: $*" >>first-boot.txt; }

port=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --port) port=$2; shift 2 ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
[[ $# -eq 3 ]] || die "usage: $0 [--port N] FLAVOR IMG WORK_DIR"
flavor=$1
[[ $flavor == steamos3 ]] || die "FLAVOR is steamos3, not '$flavor'"
img=$(realpath "$2")
[[ -f $img ]] || die "no $img"
[[ ${img##*/} == "$IMG_NAME" ]] || die "IMG is $IMG_NAME, not ${img##*/}"
GUEST_NAME=$flavor
DISK=$GUEST_NAME.qcow2
mkdir -p "$3"
cd "$3"
[[ -e $DISK ]] && die "$3/$DISK already exists; move it away to rebuild"
for t in qemu-system-x86_64 qemu-img mcopy openssl python3 ssh ssh-keygen; do command -v $t >/dev/null || die "needs $t"; done
mkfs_vfat=$(command -v mkfs.vfat || echo /usr/sbin/mkfs.vfat); [[ -x $mkfs_vfat ]] || die "needs mkfs.vfat"
bz=$(command -v lbzip2 || command -v pbzip2 || command -v bzip2) || die "needs bzip2"
for f in "$OVMF_CODE" "$OVMF_VARS"; do [[ -f $f ]] || die "no $f"; done
t_start=$(date +%s)

echo "Checking $IMG_NAME's SHA-256"
[[ $(sha256sum <"$img" | cut -d' ' -f1) == "$IMG_SHA256" ]] || die "$img's SHA-256 isn't the pinned $IMG_SHA256"

work=$(mktemp -d)
qemu_pid=""
cleanup() {
    [[ -n $qemu_pid ]] && kill "$qemu_pid" 2>/dev/null
    rm -rf "$work"
    rm -f "$PWD/recovery.raw"
}
trap cleanup EXIT

echo "Decompressing the recovery image"
"$bz" -dc <"$img" | dd of=recovery.raw bs=4M iflag=fullblock conv=sparse status=none
[[ $(($(stat -c %s recovery.raw) % 512)) == 0 ]] || die "recovery.raw isn't a whole number of sectors"

umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$GUEST_NAME test guest" -f id_ed25519
rm -f "$work/hostkey" "$work/hostkey.pub"
ssh-keygen -q -t ed25519 -N '' -C "$GUEST_NAME" -f "$work/hostkey"
cp "$work/hostkey.pub" ssh_host_ed25519_key.pub
umask 022

# --- The BKOINST disk: install.sh, filled in -------------------------------------------
sed -e "s|@HOSTNAME@|$GUEST_NAME|" -e "s|@ROOT_PASSWORD_HASH@|$hash|" \
    -e "s|@AUTHORIZED_KEY@|$(cat id_ed25519.pub)|" -e "s|@HOST_KEY_B64@|$(base64 -w0 "$work/hostkey")|" \
    "$here/steamos/install.sh" >"$work/install.sh"
rm -f "$work/hostkey"
if grep -n '@[A-Z_0-9]\+@' "$work/install.sh"; then die "install.sh placeholders left unfilled"; fi
"$mkfs_vfat" -C -n BKOINST "$work/bkoinst.img" 8192 >/dev/null
mcopy -i "$work/bkoinst.img" "$work/install.sh" ::install.sh
rm -f "$work/install.sh"

# The unit, as the recovery systemd's credential (systemd expands $$ to $),
# pulled in by a drop-in for multi-user.target, the second credential
# (steamcl and GRUB boot the kernel, so no systemd.wants= on its command line).
cat >"$work/bko-install.service" <<'EOF'
[Unit]
Description=BackOtter: unattended SteamOS install (make-steamos.sh)
After=local-fs.target systemd-udev-settle.service
Wants=network-online.target

[Service]
Type=oneshot
TimeoutStartSec=infinity
StandardOutput=journal+console
StandardError=journal+console
ExecStart=/bin/bash -c 'mkdir -p /run/bko-cd; for i in $$(seq 60); do mount -o ro /dev/disk/by-label/BKOINST /run/bko-cd 2>/dev/null && break; sleep 2; done; /bin/bash /run/bko-cd/install.sh 2>&1 | tee /dev/ttyS1'
ExecStopPost=/bin/sh -c 'sync; systemctl poweroff -ff'
EOF
printf '[Unit]\nWants=bko-install.service\n' >"$work/dropin.conf"

[[ -n $port ]] || port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
ssh_root=(ssh -q -i id_ed25519 -p "$port" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
    -o ConnectTimeout=5 -o ServerAliveInterval=30 -o ServerAliveCountMax=10 root@127.0.0.1)
wait_ssh() {  # SECONDS
    local _
    for _ in $(seq $(($1 / 5))); do
        kill -0 "$qemu_pid" 2>/dev/null || return 1
        timeout 20 "${ssh_root[@]}" true </dev/null 2>/dev/null && return 0
        sleep 5
    done
    return 1
}
wait_down() {  # SECONDS: until SSH stops answering (an in-guest reboot)
    local _
    for _ in $(seq $(($1 / 3))); do
        timeout 10 "${ssh_root[@]}" true </dev/null 2>/dev/null || return 0
        sleep 3
    done
    return 1
}
stop_guest() {
    timeout 60 "${ssh_root[@]}" 'sync; systemctl poweroff' </dev/null 2>/dev/null || true
    local _
    for _ in $(seq 150); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 2; done
    kill "$qemu_pid" 2>/dev/null && die "the guest did not power off"
    wait "$qemu_pid" 2>/dev/null || true
    qemu_pid=""
}
probe() {  # STAGE TITLE
    { echo "=== $2"; timeout 900 "${ssh_root[@]}" bash -s "$1" <"$here/steamos/probe.sh" 2>&1; } | tee "$work/$1.txt" >>first-boot.txt || true
}
val() { sed -n "s/^BKO $2=//p" "$work/$1.txt" | head -1; }   # STAGE KEY
# Boots of the guest as vm-run --uefi --no-secure-boot --guest-vars uefi-vars.fd
# --disk-bus nvme boots it (without --snapshot: written in place).
vm=(-enable-kvm -cpu host -smp 4 -m "$MEMORY" -machine q35
    -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE" -drive "if=pflash,format=raw,unit=1,file=uefi-vars.fd"
    -drive "file=$DISK,if=none,id=disk0,format=qcow2,discard=unmap" -device "nvme,drive=disk0,serial=backotter0"
    -nic "user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$port-:22" -vga std -display none -monitor none)

# --- Install ------------------------------------------------------------------------
cp "$OVMF_VARS" uefi-vars.fd
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing; install.sh's output goes to $3/install.log"
rm -f install.log install-serial.log first-boot.txt
qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 4 -m "$MEMORY" -machine q35 \
    -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE" -drive "if=pflash,format=raw,unit=1,file=uefi-vars.fd" \
    -drive "file=recovery.raw,if=none,id=rec,format=raw" -device "virtio-blk-pci,drive=rec,bootindex=0" \
    -drive "file=$DISK,if=none,id=disk0,format=qcow2,discard=unmap" -device "nvme,drive=disk0,serial=backotter0" \
    -drive "file=$work/bkoinst.img,if=virtio,format=raw,readonly=on" \
    -nic "user,model=virtio-net-pci" -vga std \
    -smbios "type=11,value=io.systemd.credential.binary:systemd.extra-unit.bko-install.service=$(base64 -w0 "$work/bko-install.service")" \
    -smbios "type=11,value=io.systemd.credential.binary:systemd.unit-dropin.multi-user.target~bko=$(base64 -w0 "$work/dropin.conf")" \
    -display none -serial file:install-serial.log -serial file:install.log -monitor none -no-reboot &
qemu_pid=$!
for ((i = 0; i < INSTALL_TIMEOUT / 10; i++)); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 10; done
if kill -0 "$qemu_pid" 2>/dev/null; then die "the install didn't finish in $INSTALL_TIMEOUT s; see install.log, install-serial.log"; fi
wait "$qemu_pid" 2>/dev/null || true; qemu_pid=""
sed -i -E 's/\r$//; s/\x1b(\[[0-9;=?]*[A-Za-z]|c)//g' install.log
grep -q '^BACKOTTER-INSTALL-START' install.log || die "the unit never ran (are the credentials read?); see install-serial.log"
grep -q '^BACKOTTER-INSTALL-OK' install.log || { tail -n 40 install.log >&2; die "the install did not finish; see install.log"; }
sed -n '/^BKO-REPAIR-BEGIN$/,/^BKO-REPAIR-END$/p' install.log | sed '1d;$d' | base64 -d >repair_device.sh
[[ $(sha256sum <repair_device.sh | cut -d' ' -f1) == "$REPAIR_SHA256" ]] || die "repair_device.sh's SHA-256 isn't the pinned $REPAIR_SHA256"
rm -f recovery.raw
t_install=$(date +%s)

# --- Boot 1: slot A; the update into B ----------------------------------------------
qemu-system-x86_64 "${vm[@]}" -serial file:boot1-serial.log &
qemu_pid=$!
wait_ssh "$BOOT_TIMEOUT" || die "the first boot never answered on SSH; see boot1-serial.log"
probe first "boot 1: the first boot (slot A)"
[[ $(val first slot) == A ]] || die "the first boot isn't slot A: $(val first slot)"
[[ $(val first bko_slot) == a ]] || die "slot A's mark isn't a: $(val first bko_slot)"
[[ $(val first graphical) == active ]] || note "graphical.target isn't active on the first boot: $(val first graphical)"
[[ $(val first boot_attempts) == 0 ]] || note "boot 1: boot-attempts is $(val first boot_attempts)"
[[ $(val first other_build) == "$(val first self_build)" ]] || note "before the update, B isn't A's build: $(val first other_build)"
echo "Updating; the output goes to $3/update.log"
upd=$(val first update_cmd)
[[ -n $upd ]] || die "no steamos-update or holo-update in the guest"
timeout "$UPDATE_TIMEOUT" "${ssh_root[@]}" "$upd check; echo BKO-CHECK-EXIT \$?; $upd; echo BKO-UPDATE-EXIT \$?" </dev/null >update.log 2>&1 || true
grep -q '^BKO-UPDATE-EXIT 0$' update.log || { tail -n 30 update.log >&2; die "$upd failed; see update.log"; }
probe updated "boot 1: after $upd"
stop_guest

# --- Boot 2: the default (B); the other slot and back ----------------------------------
qemu-system-x86_64 "${vm[@]}" -serial file:boot2-serial.log &
qemu_pid=$!
wait_ssh "$BOOT_TIMEOUT" || die "the boot into B never answered on SSH; see boot2-serial.log"
probe b-before "boot 2: the default after the update, before its mark"
[[ $(val b-before slot) == B ]] || die "the default after the update isn't B: $(val b-before slot)"
[[ $(val b-before self_build) != "$(val first self_build)" ]] || die "B is still A's build"
timeout 120 "${ssh_root[@]}" 'set -e; echo b >/etc/bko-slot; ls /efi/EFI >/dev/null; c=/efi/EFI/steamos/grub.cfg
    grep -q console=ttyS0 $c || sed -i -E "/^[[:space:]]*(steamenv_boot[[:space:]]+)?linux[[:space:]]/ s/\$/ console=ttyS0,115200/" $c
    sync; cat /var/lib/overlays/etc/upper/bko-slot' </dev/null | grep -qx b || die "/etc/bko-slot didn't land in var-B's upper"
probe b "boot 2: slot B, marked"
[[ $(val b bko_slot) == b ]] || die "slot B's mark isn't b"
[[ $(val b grub_console) == yes ]] || die "B's grub.cfg has no serial console"
[[ $(val b boot_attempts) == 0 ]] || die "slot B: boot-attempts is $(val b boot_attempts)"
reboot_other() {  # STAGE TITLE SLOT
    timeout 60 "${ssh_root[@]}" "$(val b reboot_cmd) --reboot-other" </dev/null >>first-boot.txt 2>&1 || true
    wait_down 300 || die "$3: the guest never went down"
    wait_ssh "$BOOT_TIMEOUT" || die "$3: the guest never came back"
    probe "$1" "$2"
    [[ $(val "$1" slot) == "$3" ]] || die "$2 isn't slot $3: $(val "$1" slot)"
    [[ $(val "$1" bko_slot) == "${3,,}" ]] || die "$2: the mark isn't ${3,,}: $(val "$1" bko_slot)"
    [[ $(val "$1" boot_attempts) == 0 ]] || die "$2: boot-attempts is $(val "$1" boot_attempts)"
}
reboot_other a "boot 3: holo-reboot --reboot-other into A" A
[[ $(val a grub_console) == yes ]] || die "A's grub.cfg has no serial console"
reboot_other b-again "boot 4: holo-reboot --reboot-other back into B" B
stop_guest

qemu-img snapshot -c fresh-install "$DISK"
echo "--uefi --no-secure-boot --guest-vars uefi-vars.fd --disk-bus nvme --memory 4096" >vm-run-options
t_end=$(date +%s)
{ echo "=== build: $((t_end - t_start)) s (install $((t_install - t_start)) s, boots and update $((t_end - t_install)) s)"
  echo "=== $DISK: $(du -h "$DISK" | cut -f1) on disk"; } >>first-boot.txt
echo "Done: $3/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
