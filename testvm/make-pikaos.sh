#!/usr/bin/env bash
# Build a PikaOS 4 test guest for BackOtter from PikaOS's own live ISO, with
# PikaOS's own installer backend, unattended: the GTK installer's partition
# script for the chosen layout, then the ISO's pikainstall with the flags the
# GTK installer passes it (pika/install.sh says how, and where the guest
# differs), into a qcow2 disk image, under QEMU with KVM and OVMF.
#
# Usage: ./make-pikaos.sh [--iso-dir DIR] [--first-boot-only] FLAVOR WORK_DIR
#   e.g. ./make-pikaos.sh --iso-dir /bkoscratch/rebuild-inputs/pikaos4 pikaos4 vms/pikaos4
#
# --first-boot-only: WORK_DIR already holds an installed FLAVOR.qcow2 (and
# uefi-vars.fd, id_ed25519, console-passphrase for pikaos4-luks) whose first
# boot failed: do only the first boot, its checks, first-boot.txt, the
# fallback boot, the snapshot and vm-run-options.  The ISO isn't needed.  An
# earlier run's first-boot-serial.log is kept as first-boot-serial-prev.log
# (the first run's, should there be several).
#
# A boot that doesn't answer on SSH, with an Oops or BUG on its serial
# console, is tried once more: the ISO's 7.1.8 kernel now and then oopses
# loading a module early in the boot (__text_poke from
# static_call_module_notify, as udev loads virtio_net), seen once in four
# boots of pikaos4-luks.  first-boot.txt says when.  Any other failure fails
# at once.
#
# FLAVOR is the guest:
#   pikaos4        the installer's defaults: automatic, XFS, no separate
#                  /home, no encryption, no swap (zram), rEFInd with its
#                  NVRAM entry
#   pikaos4-luks   automatic, Btrfs with /home a subvolume, LUKS on: Btrfs
#                  @ and @home on LUKS2 crypt_root; "BOOTX64 mode" (-rfb
#                  true): rEFInd in EFI/BOOT only, no NVRAM entry
#
# The ISO, PikaOS-Nest-GNOME-4.0-amd64-v3-26.08.20-4.iso with its .md5, is
# looked for in DIR (default WORK_DIR) and downloaded there if missing, then
# checked against the SHA-256 pinned below (PikaOS publishes an MD5 only,
# which is checked too, but proves only the transfer).  The ISO is the
# whole install: pikainstall unsquashes its live image, and installs only
# the .debs in its /cdrom pool.  So the guest is the ISO's PikaOS, as of
# 2026-08-20, until vm-update brings it up to date.
#
# The live system is driven over SSH: its systemd provisions root's
# authorized_keys from an SMBIOS credential (ssh.authorized_keys.root, its
# tmpfiles.d/provision.conf), and the ISO enables ssh.service.  It boots to
# multi-user.target, so no desktop (and no installer GUI) starts.
#
# Results, in WORK_DIR:
#   FLAVOR.qcow2          the guest's 32 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" taken after the first boot
#   uefi-vars.fd          the OVMF variables of the install, with rEFInd's
#                         NVRAM entry, for OVMF_CODE_4M.fd on a plain q35
#                         machine (as below; vm-run --uefi-vars can't read it:
#                         see uefi=); vm-run boots the fallback loader
#   root-password         the password of root and of "tester" (mode 600)
#   console-passphrase    pikaos4-luks: crypt_root's LUKS passphrase (mode
#                         600, letters and digits, new each build), which
#                         booster asks for at every boot on the serial
#                         console ("Enter passphrase for crypt_root:"), where
#                         console-boot.py (vm_boot, ht1.sh), compare.py and
#                         vm-update type it
#   id_ed25519, .pub      SSH key that logs in as root
#   install.log           the install script's output
#   install-serial.log, first-boot-serial.log, fallback-boot-serial.log
#                         the serial consoles
#   first-boot.txt        what the first boot showed (findmnt, efibootmgr,
#                         /proc/cmdline, the initramfs, failed units), and a
#                         second boot with fresh UEFI variables, as vm-run's,
#                         through the fallback loader EFI/BOOT/BOOTX64.EFI
#   vm-run-options        "--uefi --no-secure-boot", how vm-run must boot it
#
# The guest pikaos4, host name pikaos4: UEFI + GPT, "linux-efi" 1-500 MiB
# vfat at /boot/efi, "linux-boot" 500-1500 MiB ext4 at /boot (the
# XBOOTLDR type, bls_boot), "linux-root" the rest, XFS at /; zram swap;
# kernel-pika with a host-only booster initramfs (/boot/booster.img-K);
# rEFInd in EFI/refind with Pika's theme (and copied as the fallback
# EFI/BOOT/BOOTX64.EFI, which the installer doesn't do), its options in
# /boot/refind_linux.conf; GNOME installed but booting to text mode, sshd
# on, 4 GB RAM.  Needs an x86-64-v3 CPU (PikaOS's packages are built for it).
#
# The guest pikaos4-luks, host name pikaos4-luks: the same GPT, ESP and
# /boot; "linux-root" LUKS2 (cryptsetup's defaults), opened as crypt_root,
# holding Btrfs with subvolumes @ at / and @home at /home; /etc/crypttab
# "crypt_root UUID=... none luks,discard", refind_linux.conf's lines
# "rd.luks.name=UUID=crypt_root rd.luks.options=discard ...
# root=/dev/mapper/crypt_root"; booster unlocks / at boot, asking for the
# passphrase.  rEFInd only as the fallback loader EFI/BOOT/BOOTX64.EFI (the
# installer's BOOTX64 mode), with no NVRAM entry: every boot is the
# firmware's fallback, the first one included.
#
# Needs: qemu-system-x86_64 with KVM, OVMF, qemu-img, xorriso, mtools,
# openssl, python3, curl, ssh.  Runs as an ordinary user in the kvm group.

set -euo pipefail

ISO=PikaOS-Nest-GNOME-4.0-amd64-v3-26.08.20-4.iso
ISO_SHA256=ce26e89ee468ae146e2c7cefc6fd3a022face03316ccfa58caadb3090019a1ea
ISO_URL=https://iso.pika-os.com
DISK_SIZE=32G
MEMORY=4096
INSTALL_MEMORY=4096
OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd
INSTALL_TIMEOUT=5400

here=$(dirname "$(realpath "$0")")
die() { echo "make-pikaos: $*" >&2; exit 1; }

iso_dir="" first_boot_only=no
while [[ $# -gt 0 ]]; do
    case $1 in
        --iso-dir) iso_dir=$(realpath "$2"); shift 2 ;;
        --first-boot-only) first_boot_only=yes; shift ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
[[ $# -eq 2 ]] || die "usage: $0 [--iso-dir DIR] [--first-boot-only] FLAVOR WORK_DIR"
flavor=$1
# LAYOUT: which of the GTK installer's automatic partition scripts
# pika/install.sh runs; REFIND_BOOTX64: its "BOOTX64 mode" (-rfb).
case $flavor in
    pikaos4) LAYOUT=xfs REFIND_BOOTX64=false ;;
    pikaos4-luks) LAYOUT=btrfs-luks REFIND_BOOTX64=true ;;
    *) die "FLAVOR is pikaos4 or pikaos4-luks, not '$flavor'" ;;
esac
GUEST_NAME=$flavor
DISK=$GUEST_NAME.qcow2
mkdir -p "$2"
cd "$2"
if [[ $first_boot_only == yes ]]; then
    for f in "$DISK" uefi-vars.fd id_ed25519; do [[ -s $f ]] || die "--first-boot-only: no $2/$f"; done
    qemu-img snapshot -l "$DISK" | grep -qw fresh-install && die "$2/$DISK already has its fresh-install snapshot"
else
    [[ -e $DISK ]] && die "$2/$DISK already exists; move it away to rebuild"
fi
[[ -n $iso_dir ]] || iso_dir=$PWD

if [[ $first_boot_only == no ]]; then

# --- Fetch and verify the ISO -------------------------------------------------------
# iso.pika-os.com (bunny.net) gives one connection about 0.2 MB/s: 16 ranged
# connections at once, then joined.
fetch() {  # NAME
    local f=$1 size n=16 i from to pids=()
    size=$(curl -sfIL "$ISO_URL/$f" | tr -d '\r' | awk 'tolower($1) == "content-length:" { s = $2 } END { print s }')
    [[ $size =~ ^[0-9]+$ ]] || die "no size for $ISO_URL/$f"
    if [[ $size -lt $((64 << 20)) ]]; then
        curl -sfL -o "$iso_dir/$f" "$ISO_URL/$f" || die "could not download $ISO_URL/$f"
        return
    fi
    for ((i = 0; i < n; i++)); do
        from=$((size * i / n)) to=$((size * (i + 1) / n - 1))
        curl -sfL --retry 5 -C - -r "$from-$to" -o "$iso_dir/$f.part.$i" "$ISO_URL/$f" &
        pids+=($!)
    done
    for i in "${pids[@]}"; do wait "$i" || die "could not download $ISO_URL/$f"; done
    for ((i = 0; i < n; i++)); do cat "$iso_dir/$f.part.$i"; done >"$iso_dir/$f.joined"
    [[ $(stat -c %s "$iso_dir/$f.joined") -eq $size ]] || die "$f: got the wrong size"
    mv "$iso_dir/$f.joined" "$iso_dir/$f"
    rm -f "$iso_dir/$f".part.*
}
md5=${ISO%.iso}.md5
for f in "$md5" "$ISO"; do
    [[ -f $iso_dir/$f && ! -f $iso_dir/$f.part ]] && continue
    echo "Downloading $ISO_URL/$f"
    touch "$iso_dir/$f.part"
    fetch "$f"
    rm "$iso_dir/$f.part"
done
[[ $(sha256sum <"$iso_dir/$ISO" | cut -d' ' -f1) == "$ISO_SHA256" ]] ||
    die "$ISO's SHA-256 isn't the pinned $ISO_SHA256"
[[ $(md5sum <"$iso_dir/$ISO" | cut -d' ' -f1) == "$(awk '{ print $1; exit }' "$iso_dir/$md5")" ]] ||
    die "$ISO does not match $md5"
fi

work=$(mktemp -d)
qemu_pid=""
cleanup() {
    [[ -n $qemu_pid ]] && kill "$qemu_pid" 2>/dev/null
    chmod -R u+w "$work"; rm -rf "$work"
}
trap cleanup EXIT

passphrase=""
if [[ $first_boot_only == yes ]]; then
    if [[ $LAYOUT == btrfs-luks ]]; then
        passphrase=$(cat console-passphrase 2>/dev/null) || die "--first-boot-only: no $2/console-passphrase"
    fi
    # (The first such log is kept: a rerun's own console then makes way.)
    if [[ -e first-boot-serial.log ]]; then
        if [[ -e first-boot-serial-prev.log ]]; then rm first-boot-serial.log; else mv first-boot-serial.log first-boot-serial-prev.log; fi
    fi
else
# --- The live system's kernel, initramfs and boot options --------------------------
# The ISO's only boot image is El Torito's EFI one: a FAT image holding
# rEFInd (EFI/BOOT), the kernel EFI/VMLINUZ and booster's initramfs
# EFI/INITRD, with the options in EFI/BOOT/refind.conf.
xorriso -osirrox on -indev "$iso_dir/$ISO" -extract_boot_images "$work/boot" 2>/dev/null ||
    die "could not extract $ISO's boot images"
efi_img=$work/boot/eltorito_img1_uefi.img
[[ -s $efi_img ]] || die "$ISO has no El Torito EFI image"
mcopy -n -i "$efi_img" ::/EFI/VMLINUZ "$work/vmlinuz"
mcopy -n -i "$efi_img" ::/EFI/INITRD "$work/initrd"
mcopy -n -i "$efi_img" ::/EFI/BOOT/refind.conf "$work/refind.conf"
# The live entry's options, as rEFInd passes them (a "" inside the quotes is
# one "): booster's live hook (01_pika_live.sh) finds the medium by
# ISO_LABEL_NAME among blkid's iso9660 devices.  Without quiet and splash, so
# the hook and the boot show on the serial console.
options=$(awk '$1 == "options" { sub(/^[ \t]*options[ \t]+"/, ""); sub(/"[ \t]*$/, ""); gsub(/""/, "\""); print; exit }' \
    "$work/refind.conf" | sed -E 's/ (quiet|splash)\b//g')
[[ $options == *boot=live* && $options == *booster.loadcdrom* && $options == *ISO_LABEL_NAME=* ]] ||
    die "unexpected live entry in refind.conf: $options"

umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$GUEST_NAME test guest" -f id_ed25519
# Letters and digits only, so it needs no quoting anywhere (the GTK's script
# printf's it into cryptsetup).
if [[ $LAYOUT == btrfs-luks ]]; then
    openssl rand -base64 24 | tr -dc 'A-Za-z0-9' >console-passphrase
    passphrase=$(cat console-passphrase)
    [[ $passphrase =~ ^[A-Za-z0-9]{16,}$ ]] || die "could not make a passphrase"
else
    rm -f console-passphrase
fi
umask 022

sed -e "s|@HOSTNAME@|$GUEST_NAME|" -e "s|@ROOT_PASSWORD_HASH@|$hash|" \
    -e "s|@AUTHORIZED_KEY@|$(cat id_ed25519.pub)|" -e "s|@LAYOUT@|$LAYOUT|" \
    -e "s|@REFIND_BOOTX64@|$REFIND_BOOTX64|" -e "s|@LUKS_PASSPHRASE@|$passphrase|" \
    "$here/pika/install.sh" >"$work/install.sh"
if grep -n '@[A-Z_0-9]\+@' "$work/install.sh"; then die "install.sh placeholders left unfilled"; fi
fi

port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
ssh_root=(ssh -q -i id_ed25519 -p "$port" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
    -o ConnectTimeout=5 -o ServerAliveInterval=30 root@127.0.0.1)
wait_ssh() {  # SECONDS
    local _
    for _ in $(seq $(($1 / 5))); do
        kill -0 "$qemu_pid" 2>/dev/null || return 1
        "${ssh_root[@]}" true </dev/null 2>/dev/null && return 0
        sleep 5
    done
    return 1
}
stop_guest() {
    "${ssh_root[@]}" 'sync; systemctl poweroff' </dev/null 2>/dev/null || true
    local _
    for _ in $(seq 60); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 2; done
    kill "$qemu_pid" 2>/dev/null && die "the guest did not power off"
    wait "$qemu_pid" 2>/dev/null || true
    qemu_pid=""
}
# Plain q35, not vm-run's machine: vm-run marks the flash secure (SMM only)
# even for OVMF_CODE_4M.fd, which has no SMM, so under --no-secure-boot the
# firmware can neither read nor write the variable store and keeps its
# variables in RAM: rEFInd's entry would never reach uefi-vars.fd.
uefi=(-machine q35 -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE"
    -drive "if=pflash,format=raw,unit=1,file=uefi-vars.fd")
# Boot the installed disk in the background, its console in LOG: on
# pikaos4-luks through console-boot.py, which types console-passphrase at
# booster's prompt (and qemu_pid is console-boot.py's, which passes a TERM on).
boot_disk() {  # LOG QEMU_ARGS...
    local log=$1; shift
    if [[ -n $passphrase ]]; then
        python3 "$here/console-boot.py" "$log" console-passphrase -- \
            qemu-system-x86_64 "$@" -display none -serial stdio -monitor none </dev/null &
    else
        qemu-system-x86_64 "$@" -display none -serial "file:$log" -monitor none &
    fi
    qemu_pid=$!
}
# boot_disk, then wait for SSH; once more if the boot never answered and its
# console shows a kernel Oops or BUG (the header says why).  RETRIED names the
# boot when that happened.
retried=""
boot_up() {  # LOG WHAT QEMU_ARGS...
    local log=$1 what=$2; shift 2
    boot_disk "$log" "$@"
    wait_ssh 420 && return 0
    grep -aqE 'Oops|BUG: ' "$log" 2>/dev/null || die "$what never answered on SSH; see $PWD/$log"
    echo "make-pikaos: $what never answered on SSH, after a kernel oops; booting it once more" >&2
    kill "$qemu_pid" 2>/dev/null; wait "$qemu_pid" 2>/dev/null || true; qemu_pid=""
    mv "$log" "${log%.log}-oops.log"
    retried+="${retried:+, }$what (its console in ${log%.log}-oops.log)"
    boot_disk "$log" "$@"
    wait_ssh 420 || die "$what never answered on SSH, tried twice; see $PWD/$log"
}

# --- Install --------------------------------------------------------------------------
if [[ $first_boot_only == no ]]; then
cp "$OVMF_VARS" uefi-vars.fd
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
echo "Installing; the install script's output goes to $2/install.log"
qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 4 -m "$INSTALL_MEMORY" "${uefi[@]}" \
    -drive "file=$DISK,if=virtio,format=qcow2,discard=unmap" \
    -drive "file=$iso_dir/$ISO,media=cdrom,readonly=on" \
    -nic "user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$port-:22" \
    -smbios "type=11,value=io.systemd.credential.binary:ssh.authorized_keys.root=$(base64 -w0 id_ed25519.pub)" \
    -kernel "$work/vmlinuz" -initrd "$work/initrd" \
    -append "$options systemd.unit=multi-user.target console=tty0 console=ttyS0,115200n8" \
    -display none -serial file:install-serial.log -monitor none -no-reboot &
qemu_pid=$!
wait_ssh 600 || die "the live system never answered on SSH; see install-serial.log"
# Copied in first, so that nothing it runs can read the rest of it from stdin.
"${ssh_root[@]}" 'cat >/root/bko-install.sh' <"$work/install.sh" || die "could not copy the install script in"
timeout "$INSTALL_TIMEOUT" "${ssh_root[@]}" bash /root/bko-install.sh </dev/null >install.log 2>&1 || true
# (Nothing prints the passphrase; should anything ever, it isn't kept.)
[[ -z $passphrase ]] || sed -i "s/$passphrase/<console-passphrase>/g" install.log install-serial.log
grep -q BACKOTTER-INSTALL-OK install.log || {
    tail -n 30 install.log >&2
    die "the install did not finish; see install.log"
}
stop_guest
fi

# --- First boot, from the disk, through rEFInd's NVRAM entry -----------------------
# (pikaos4-luks: through the fallback loader, there being no entry.)  rEFInd
# waits 20 s on its menu (the installer's default timeout), then boots the
# newest kernel with refind_linux.conf's first line.
boot_up first-boot-serial.log "the first boot" \
    -enable-kvm -cpu host -smp 2 -m "$MEMORY" "${uefi[@]}" \
    -drive "file=$DISK,if=virtio,format=qcow2,discard=unmap" \
    -nic "user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$port-:22"
"${ssh_root[@]}" 'set -x; systemctl is-system-running --wait; cat /proc/cmdline; uname -r
    findmnt -no TARGET,SOURCE,FSTYPE /; findmnt -no TARGET,SOURCE,FSTYPE /boot; findmnt -no TARGET,SOURCE,FSTYPE /boot/efi
    findmnt -no TARGET,SOURCE,FSTYPE /home; findmnt -no TARGET,OPTIONS /; findmnt -no TARGET,OPTIONS /home; lsblk -o NAME,TYPE,FSTYPE,MOUNTPOINTS
    cat /etc/crypttab; ls -l /boot; cat /boot/refind_linux.conf; cat /etc/booster.yaml; grep -E "^(ID|VERSION_ID)=" /etc/os-release
    hostname; swapon --show; find /boot/efi -maxdepth 3 | sort; efibootmgr -v; systemctl --failed --no-legend' \
    </dev/null >first-boot.txt 2>&1 || true
# An earlier run's first boot (--first-boot-only), if it oopsed, counts as this one's first try.
if [[ -f first-boot-serial-prev.log ]] && grep -aqE 'Oops|BUG: ' first-boot-serial-prev.log; then
    retried="the first boot (an earlier run's, its console in first-boot-serial-prev.log)${retried:+, $retried}"
fi
grep -Eq '^ *(running|degraded)$' first-boot.txt || { cat first-boot.txt >&2; die "the first boot isn't up"; }
if [[ $LAYOUT == btrfs-luks ]]; then
    grep -q 'Enter passphrase for crypt_root' first-boot-serial.log ||
        die "booster's passphrase prompt never showed on the serial console; see first-boot-serial.log"
    grep -Eq '(^| )rd\.luks\.name=[0-9a-f-]+=crypt_root( |$)' first-boot.txt || die "/proc/cmdline has no rd.luks.name=UUID=crypt_root"
    grep -Eq '(^| )root=/dev/mapper/crypt_root( |$)' first-boot.txt || die "/proc/cmdline has no root=/dev/mapper/crypt_root"
    # findmnt names crypt_root by its kernel name (dm-N); lsblk says it is
    # crypt_root, a crypt device holding the Btrfs, on vda3's LUKS.
    grep -Eq '^[^a-z]*crypt_root +crypt +btrfs ' first-boot.txt && grep -Eq '^[^a-z]*vda3 +part +crypto_LUKS' first-boot.txt ||
        die "lsblk shows no crypt_root (Btrfs) on vda3's LUKS"
    grep -Eq '^/ +/dev/(mapper/crypt_root|dm-[0-9]+)\[/@\] +btrfs$' first-boot.txt || die "/ isn't crypt_root's Btrfs subvolume @"
    grep -Eq '^/home +/dev/(mapper/crypt_root|dm-[0-9]+)\[/@home\] +btrfs$' first-boot.txt || die "/home isn't crypt_root's Btrfs subvolume @home"
    [[ $(grep -Eo '^/(home)? +/dev/[^[]+\[' first-boot.txt | awk '{ print $2 }' | sort -u | wc -l) == 1 ]] ||
        die "/ and /home aren't on the same device"
    grep -Eq '^/ +.*subvol=/@(,|$)' first-boot.txt || die "/ isn't mounted subvol=@"
    grep -Eq '^/home +.*subvol=/@home(,|$)' first-boot.txt || die "/home isn't mounted subvol=@home"
    grep -Eq '^crypt_root[[:space:]]+UUID=[0-9a-f-]+[[:space:]]+none[[:space:]]+luks,discard$' first-boot.txt ||
        die "/etc/crypttab has no crypt_root UUID=... none luks,discard"
else
    grep -Eq '^BOOT_IMAGE=.* root=UUID=|(^| )root=UUID=' first-boot.txt || die "/proc/cmdline has no root=UUID="
    grep -Eq '^/ +/dev/vda3 +xfs$' first-boot.txt || die "/ isn't /dev/vda3's XFS"
fi
grep -Eq '^/boot +/dev/vda2 +ext4$' first-boot.txt || die "/boot isn't /dev/vda2's ext4"
grep -Eq '^/boot/efi +/dev/vda1 +vfat$' first-boot.txt || die "/boot/efi isn't /dev/vda1's vfat"
# The initramfs the kernel was given: rEFInd passes initrd=\booster.img-K
# (its path on /boot) when it finds the kernel's initramfs by name.
grep -Eq '(^| )initrd=[^ ]*booster\.img-' first-boot.txt ||
    die "the kernel wasn't booted with /boot/booster.img-*"
# (systemctl --failed is the last command: whatever follows it is a unit.)
[[ -z $(sed -n '/^+ systemctl --failed --no-legend$/,$p' first-boot.txt | tail -n +2) ]] ||
    die "the first boot has failed units; see first-boot.txt"
current=$(awk '$1 == "BootCurrent:" { print $2 }' first-boot.txt)
if [[ $REFIND_BOOTX64 == true ]]; then
    # The firmware's own entry for the disk (OVMF's "UEFI Misc Device"),
    # which runs EFI/BOOT/BOOTX64.EFI; and no entry of rEFInd's anywhere.
    grep -E "^Boot$current\\*? " first-boot.txt | grep -qE 'UEFI Misc Device|auto_created_boot_option' ||
        die "the first boot wasn't the firmware's fallback (BootCurrent ${current:-none})"
    ! grep -E '^Boot[0-9A-F]{4}' first-boot.txt | grep -qiE 'refind' || die "BOOTX64 mode, yet a rEFInd NVRAM entry"
else
    grep -E "^Boot$current\\*? " first-boot.txt | grep -qiF '\EFI\refind\refind_x64.efi' ||
        die "the first boot didn't come through rEFInd's NVRAM entry (BootCurrent ${current:-none})"
fi
stop_guest

# --- And once as vm-run boots it: fresh UEFI variables, the fallback loader ----------
cp "$OVMF_VARS" "$work/fresh-vars.fd"
boot_up fallback-boot-serial.log "the fallback loader's boot" \
    -enable-kvm -cpu host -smp 2 -m "$MEMORY" -machine q35 \
    -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE" \
    -drive "if=pflash,format=raw,unit=1,file=$work/fresh-vars.fd" \
    -drive "file=$DISK,if=virtio,format=qcow2,discard=unmap" -snapshot \
    -nic "user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$port-:22"
{ echo "=== fallback boot (fresh UEFI variables)"
  "${ssh_root[@]}" 'set -x; systemctl is-system-running --wait; cat /proc/cmdline; efibootmgr' </dev/null 2>&1; } \
    >>first-boot.txt || true
grep -A3 '^=== fallback boot' first-boot.txt | grep -Eq '^ *(running|degraded)$' ||
    { cat first-boot.txt >&2; die "the fallback loader's boot isn't up"; }
stop_guest
[[ -z $retried ]] || echo "=== retried after a kernel oops in the ISO's 7.1.8 kernel: $retried" >>first-boot.txt

qemu-img snapshot -c fresh-install "$DISK"
echo "--uefi --no-secure-boot" >vm-run-options
echo "Done: $2/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
