#!/usr/bin/env bash
# Build a Solus 4.9 test guest for BackOtter from Solus's own Xfce live ISO,
# unattended: Calamares's "erase disk" job sequence from the ISO's own
# config (ext4, the ESP at /boot and left out of fstab, clr-boot-manager
# with shim and systemd-boot), replayed by solus/install.sh in the live
# system, into a qcow2 disk image, under QEMU with KVM and OVMF.
# docs/design/solus.md is the design; solus/install.sh says what runs in the
# live system, and what the guest gets besides.
#
# Usage: ./make-solus.sh ISO WORK_DIR NAME
#   e.g. ./make-solus.sh /bkoscratch/rebuild-inputs/solus49/Solus-Xfce-Release-2026-04-18.iso vms/solus49 solus49
#
# NAME is the guest, solus49 (the only one).  ISO is
# Solus-Xfce-Release-2026-04-18.iso, checked against the SHA-256 pinned
# below (Solus publishes a .sha256sum and its .sha256sum.sign, a detached
# signature by "Solus (Release & Engineering)", primary key
# F5F6685CAF5559771D9CCB92618EB3600BD32D59, pinned below: the build checks
# the signature with the key file beside the ISO, solus-releng-pub.gpg).  The install brings the system up to date from Solus's
# repository (eopkg up): Solus takes no partial upgrade, and openssh-server,
# which the ISO lacks, comes from today's repository.
#
# The live system runs solus/install.sh from kernel-command-line.service
# (systemd.run= with systemd.wants=, so it boots on to multi-user.target and
# its network), fetched from 10.0.2.2, which QEMU's user-mode network maps to
# the host's 127.0.0.1, where a server for this build alone serves it.  The
# live system boots with Secure Boot off (OVMF's Secure Boot build with no
# keys enrolled): QEMU's -kernel loads the ISO's kernel directly.  The guest
# then boots with Secure Boot on, Solus's key (solus-enroll-me.cer, which
# clr-boot-manager puts on the ESP for the user to enroll in MokManager)
# enrolled as a MOK in its variable store by vm-run --mok, from the
# removable path EFI/BOOT (shim, then the Solus-signed systemd-boot, then the
# Solus-signed kernel): vm-run gives every boot a fresh store, with no NVRAM
# entries.
#
# Results, in WORK_DIR:
#   NAME.qcow2            the guest's 20 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" taken after the first boot
#   NAME-mok.der          Solus's MOK certificate (the ESP's solus-enroll-me.cer)
#   vm-run-options        "--uefi --mok BASE64", the MOK's DER in base64
#   root-password         the password of root and of "tester" (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the live system's serial console
#   facts/                what install.sh recorded on the installed system
#                         (os-release, partition table, fstab, the ESP's
#                         files with SHA-256s, the loader entries, ...)
#   first-boot.txt, first-boot-serial.log
#                         the first boot alone with Secure Boot on: mokutil,
#                         mounts, swap, clr-boot-manager's view
#
# The guest solus49, host name solus49: UEFI + GPT, Calamares's erase-disk
# layout (the ESP, 2 GiB, at /boot but not in fstab; an 8 MiB BIOS boot
# partition; ext4 /; no swap partition, zram), Xfce installed but booting to
# text mode, sshd on, 3 GB RAM.
#
# Needs: qemu-system-x86_64 with KVM, OVMF, qemu-img, xorriso, openssl,
# python3, ssh, virt-fw-vars (python3-virt-firmware, for vm-run --mok).
# Runs as an ordinary user in the kvm group.

set -euo pipefail

ISO_NAME=Solus-Xfce-Release-2026-04-18.iso
ISO_SHA256=a3085fa5c0171420e46e1554191fffe66e50dbea70c37e9d6cc4ec79fb557895
RELENG_FPR=F5F6685CAF5559771D9CCB92618EB3600BD32D59   # "Solus (Release & Engineering) <releng@getsol.us>"
DISK_SIZE=20G
INSTALL_MEMORY=4096
OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.secboot.fd
OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd
INSTALL_TIMEOUT=3600
USERNAME=tester

here=$(dirname "$(realpath "$0")")
die() { echo "make-solus: $*" >&2; exit 1; }

[[ $# -eq 3 ]] || die "usage: $0 ISO WORK_DIR NAME"
iso=$(realpath "$1") name=$3
[[ $name == solus49 ]] || die "NAME is solus49, not '$name'"
[[ -f $iso ]] || die "no such ISO: $iso"
[[ ${iso##*/} == "$ISO_NAME" ]] || die "the ISO should be $ISO_NAME, not ${iso##*/}"
command -v virt-fw-vars >/dev/null || die "needs virt-fw-vars (apt install python3-virt-firmware)"
DISK=$name.qcow2
mkdir -p "$2"
cd "$2"
[[ -e $DISK ]] && die "$2/$DISK already exists; move it away to rebuild"

# Solus's signature over its .sha256sum, by the release key pinned here
# (solus-releng-pub.gpg beside the ISO, as fetched from getsol.us), and the
# .sha256sum's own sum, which has to be the one pinned below too.
d=$(dirname "$iso")
for f in "$ISO_NAME.sha256sum" "$ISO_NAME.sha256sum.sign" solus-releng-pub.gpg; do
    [[ -f $d/$f ]] || die "no $d/$f (fetched with the ISO; docs/design/solus.md S1)"
done
gpghome=$(mktemp -d)
gpg -q --homedir "$gpghome" --import "$d/solus-releng-pub.gpg" 2>/dev/null
[[ $(gpg --homedir "$gpghome" --with-colons --fingerprint 2>/dev/null | awk -F: '$1 == "fpr" { print $10; exit }') == "$RELENG_FPR" ]] ||
    { rm -rf "$gpghome"; die "solus-releng-pub.gpg's primary key isn't the pinned $RELENG_FPR"; }
status=$(gpg --homedir "$gpghome" --status-fd 1 --verify "$d/$ISO_NAME.sha256sum.sign" "$d/$ISO_NAME.sha256sum" 2>/dev/null)
rm -rf "$gpghome"
grep -Eq "^\[GNUPG:\] VALIDSIG [0-9A-F]+ .* $RELENG_FPR\$" <<<"$status" ||
    die "$ISO_NAME.sha256sum isn't signed by Solus's release key $RELENG_FPR"
[[ $(awk '{ print $1; exit }' "$d/$ISO_NAME.sha256sum") == "$ISO_SHA256" ]] ||
    die "the signed $ISO_NAME.sha256sum names another SHA-256 than the pinned $ISO_SHA256"
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
xorriso -osirrox on -indev "$iso" -extract /boot/kernel "$work/vmlinuz" \
    -extract /boot/initrd.img "$work/initrd" -extract /isolinux/isolinux.cfg "$work/isolinux.cfg" 2>/dev/null ||
    die "could not extract the kernel, initramfs and isolinux.cfg from $ISO_NAME"
chmod u+w "$work"/vmlinuz "$work"/initrd "$work"/isolinux.cfg
# The default entry ("Start Solus"), without initrd= (QEMU's -initrd), quiet,
# splash and the closing "--", so the boot shows on the serial console.
options=$(awk '$1 == "label" { l = $2 } l == "live" && $1 == "append" { $1 = ""; print; exit }' "$work/isolinux.cfg" |
    sed -E 's/^ +//; s/initrd=[^ ]+ ?//; s/ (quiet|splash)\b//g; s/ *--$//')
[[ $options == *root=live:CDLABEL=SolusLiveXfce* && $options == *rd.live.overlay.overlayfs=1* ]] ||
    die "unexpected default entry in the ISO's isolinux.cfg: $options"

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
    "$here/solus/install.sh" >"$work/www/install.sh"
if grep -n '@[A-Z_]\+@' "$work/www/install.sh"; then die "install.sh placeholders left unfilled"; fi
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
append+=" systemd.mask=serial-getty@ttyS0.service"
# Nor Solus's sched_ext scheduler (scx_loader.service, "flash"): on a busy
# host it stalls, is thrown out by the kernel, and the stall took eopkg's
# downloads with it (the first build).  The installed system keeps it on.
append+=" systemd.mask=scx_loader.service"

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
tar -C facts -xf "$work/facts.raw" 2>/dev/null || echo "make-solus: no facts on the facts disk" >&2
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
# removable path, shim, which loads systemd-boot (grubx64.efi), signed by
# Solus's key, which loads the Solus-signed kernel.
mok=$(base64 -w0 "$name-mok.der")
opts=(--uefi --mok "$mok")
sport=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
ssh_root=(ssh -q -i id_ed25519 -p "$sport" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
    -o ConnectTimeout=5 -o ServerAliveInterval=30 root@127.0.0.1)
SSH_PORT=$sport setsid "$here/vm-run" "${opts[@]}" --discard --serial-log first-boot-serial.log "$DISK" </dev/null >"$work/vm-run.log" 2>&1 &
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
    grep -E "^(ID|ID_LIKE|VERSION_ID|VERSION)=" /usr/lib/os-release; hostname
    findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /; findmnt -no TARGET,SOURCE,FSTYPE /boot || echo "/boot: not mounted"
    cat /etc/fstab; swapon --show; zramctl; efibootmgr -v; bootctl status --no-pager 2>&1 | head -30
    clr-boot-manager list-kernels; clr-boot-manager get-timeout
    findmnt -no TARGET /boot || echo "/boot: not mounted after clr-boot-manager"
    systemctl is-enabled sshd.service; systemctl --failed --no-legend
    sshd -T | grep -Ei "^(permitrootlogin|passwordauthentication) "; eopkg lr' </dev/null >first-boot.txt 2>&1 || true
# The first boot's TRIM (vm-run --discard) frees what the install left in
# the image's clusters, before the snapshot holds them.
"${ssh_root[@]}" 'mountpoint -q /boot || clr-boot-manager mount-boot; fstrim -av; umount /boot' </dev/null >>first-boot.txt 2>&1 || true
"${ssh_root[@]}" 'sync; systemctl poweroff' </dev/null 2>/dev/null || true
for _ in $(seq 60); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 2; done
kill "$qemu_pid" 2>/dev/null && die "the guest did not power off"
wait "$qemu_pid" 2>/dev/null || true
qemu_pid=""
grep -Eq '^ *(running|degraded)$' first-boot.txt || { cat first-boot.txt >&2; die "the first boot isn't up"; }
grep -q '^SecureBoot enabled$' first-boot.txt || die "the first boot wasn't under Secure Boot; see first-boot.txt"
grep -Eq '^/ +/dev/vda3 +ext4 ' first-boot.txt || die "/ isn't ext4 on /dev/vda3; see first-boot.txt"

qemu-img snapshot -c fresh-install "$DISK"
echo "${opts[*]}" >vm-run-options
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
