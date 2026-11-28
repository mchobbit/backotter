#!/usr/bin/env bash
# Build a Mageia 10 test guest for BackOtter with Mageia's own installer,
# DrakX, unattended: its auto_install from the classic DVD, the profile on a
# USB key, under QEMU with KVM and OVMF (Secure Boot off: Mageia has no
# signed shim).  docs/design/mageia.md ("Guests") is the design;
# mageia/auto_inst.cfg.pl.in is the profile and mageia/post-install.sh its
# postInstall.
#
# Usage: ./make-mageia.sh NAME ISO WORK_DIR
#   e.g. ./make-mageia.sh mageia10 /bkoscratch/rebuild-inputs/mageia10/Mageia-10-x86_64.iso vms/mageia10
#
# NAME is the guest, mageia10 (the only one this builder makes; mageia10-luks
# is install.sh's, Decision 4).  ISO is Mageia-10-x86_64.iso, the classic
# DVD, with Mageia-10-x86_64.iso.sha512, its .sha512.gpg (an inline-signed
# message holding the .sha512, not a detached signature) and
# mageia-release-key.asc beside it.  The build checks, before anything else:
# the key's fingerprint against the one pinned below; the message's
# signature by that key alone (gpgv against a keyring of it); that the
# message is the file Mageia-10-x86_64.iso.sha512, byte for byte the plain
# one; and the ISO's SHA-512 against both it and the one pinned below.
#
# The install: the DVD's own installer kernel and initrd (/boot/vmlinuz,
# /boot/all.rdz) by QEMU's -kernel, with the DVD's GRUB entry's
# "automatic=method:cdrom" plus "kickstart=floppy", which makes DrakX read
# auto_inst.cfg.pl from a removable disk: a 16 MiB USB key (qemu-xhci and
# usb-storage; DrakX's isKeyUsb wants a USB mass-storage sd disk, not a
# virtio one) with one FAT partition.  No "text" (it makes the auto install
# interactive).  It runs on vm-run --uefi --no-secure-boot's hardware, so
# the network card's name the profile finds is the installed system's, and
# with a kept variable store, uefi-vars.fd: DrakX's grub2-install writes an
# NVRAM entry and no EFI/BOOT/BOOTX64.EFI, so a fresh store boots nothing.
# The profile: DrakX's default partitioning of the whole disk (auto_allocate),
# security level 1 ("standard", so msec and shorewall, with 22/tcp open),
# the Xfce Workstation, Configuration, Console Tools and SSH Server groups,
# root and tester with one new password, root's SSH key, the serial console
# in GRUB and the kernel, text-mode boot.
#
# The first boot (on uefi-vars.fd itself) swaps the DVD's urpmi media for
# Mageia's online ones (urpmi.addmedia --distrib --mirrorlist, what a
# network install leaves) and records the guest's facts; the check boot
# then boots it alone, as vm-run-options says, on a copy of uefi-vars.fd,
# and checks it came up through the NVRAM entry.  No package is updated
# here: vm-update's Linux.urpmi does that.
#
# Results, in WORK_DIR:
#   NAME.qcow2            the guest's 32 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" taken after the check boot
#   uefi-vars.fd          its OVMF variable store (OVMF_CODE_4M.fd's, as
#                         vm-run --no-secure-boot runs it), with DrakX's entry
#   vm-run-options        "--uefi --no-secure-boot --guest-vars uefi-vars.fd"
#   root-password         the password of root and of "tester" (mode 600)
#   id_ed25519, .pub      SSH key that logs in as root
#   install-serial.log    the installer's serial console; install-screen.ppm
#                         its last screen (every minute)
#   first-boot.txt, first-boot-serial.log
#                         the first boot: post-install's log, the media, and
#                         every fact docs/design/mageia.md's M2 asks for
#   check-boot.txt, check-boot-serial.log
#                         the boot alone: BootCurrent, efibootmgr, mounts
#
# Needs: qemu-system-x86_64 with KVM, OVMF, qemu-img, xorriso, gpg, gpgv,
# sha512sum, openssl, perl, python3, sfdisk, mtools, ssh.  Runs as an
# ordinary user in the kvm group.

set -euo pipefail

ISO_NAME=Mageia-10-x86_64.iso
ISO_SHA512=e02d59303d284dd0ee05ce0b327cbed6d3270410803965078c2fa6961e23d9131775011b111a57476e60f3354321ef46c6c1e107db4db9b708634ed7a26fd270
REL_FPR=B21076A0CBE4D93D66A9D08D835E41F4EDCA7A90   # "Mageia Release <release@mageia.org>"
DISK_SIZE=32G
INSTALL_MEMORY=4096
INSTALL_TIMEOUT=5400
BOOT_TIMEOUT=900
USERNAME=tester
OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd

here=$(dirname "$(realpath "$0")")
die() { echo "make-mageia: $*" >&2; exit 1; }

[[ $# -eq 3 ]] || die "usage: $0 NAME ISO WORK_DIR"
name=$1 iso=$(realpath "$2")
[[ $name == mageia10 ]] || die "NAME is mageia10, not '$name'"
[[ -f $iso ]] || die "no such ISO: $iso"
[[ ${iso##*/} == "$ISO_NAME" ]] || die "the ISO should be $ISO_NAME, not ${iso##*/}"
for t in qemu-img xorriso gpg gpgv sha512sum openssl perl python3 sfdisk mformat mcopy ssh; do
    command -v $t >/dev/null || die "needs $t"
done
[[ -f $OVMF_VARS ]] || die "no $OVMF_VARS (ovmf)"
DISK=$name.qcow2
mkdir -p "$3"
cd "$3"
wdir=$(pwd)
[[ -e $DISK ]] && die "$3/$DISK already exists; move it away to rebuild"

work=$(mktemp -d)
qemu_pid="" shot_pid=""
cleanup() {
    [[ -n $shot_pid ]] && kill "$shot_pid" 2>/dev/null
    [[ -n $qemu_pid ]] && kill "$qemu_pid" 2>/dev/null
    chmod -R u+w "$work"; rm -rf "$work"
}
trap cleanup EXIT

# --- The signed sums and the ISO ---------------------------------------------------
d=$(dirname "$iso")
for f in "$ISO_NAME.sha512" "$ISO_NAME.sha512.gpg" mageia-release-key.asc; do
    [[ -f $d/$f ]] || die "no $d/$f (fetched with the ISO; docs/design/mageia.md M1)"
done
export GNUPGHOME=$work/gnupg
mkdir -m 700 "$GNUPGHOME"
gpg -q --import "$d/mageia-release-key.asc" 2>/dev/null || die "could not read mageia-release-key.asc"
fprs=$(gpg --with-colons --fingerprint 2>/dev/null | awk -F: '$1 == "pub" { p = 1; next } p && $1 == "fpr" { print $10; p = 0 }')
[[ $fprs == "$REL_FPR" ]] || die "mageia-release-key.asc holds $(echo $fprs), not the pinned $REL_FPR alone"
gpg --export "$REL_FPR" >"$work/rel.gpg"
# (gpgv exits 0 for a good signature by any key in its keyring: the keyring
# is the pinned key alone, and VALIDSIG names it.)
status=$(gpgv --keyring "$work/rel.gpg" --status-fd 1 --output "$work/sha512" "$d/$ISO_NAME.sha512.gpg" 2>/dev/null) ||
    die "$ISO_NAME.sha512.gpg isn't signed by Mageia's release key $REL_FPR"
grep -Eq "^\[GNUPG:\] VALIDSIG [0-9A-F]+ .* $REL_FPR\$" <<<"$status" ||
    die "$ISO_NAME.sha512.gpg's signature isn't by $REL_FPR"
grep -Eq "^\[GNUPG:\] PLAINTEXT [0-9a-f]+ [0-9]+ $ISO_NAME\.sha512\$" <<<"$status" ||
    die "$ISO_NAME.sha512.gpg's message isn't the file $ISO_NAME.sha512"
cmp -s "$work/sha512" "$d/$ISO_NAME.sha512" || die "$ISO_NAME.sha512 isn't the one $ISO_NAME.sha512.gpg signs"
[[ $(awk '{ print $1; exit }' "$work/sha512") == "$ISO_SHA512" ]] ||
    die "the signed $ISO_NAME.sha512 names another SHA-512 than the pinned one"
unset GNUPGHOME
echo "Checking $ISO_NAME's SHA-512"
[[ $(sha512sum <"$iso" | cut -d' ' -f1) == "$ISO_SHA512" ]] || die "$ISO_NAME's SHA-512 isn't the pinned one"

# --- The installer's kernel and initrd ------------------------------------------------
xorriso -osirrox on -indev "$iso" -extract /boot/vmlinuz "$work/vmlinuz" \
    -extract /boot/all.rdz "$work/all.rdz" -extract /boot/grub2/grub.cfg "$work/grub.cfg" 2>/dev/null ||
    die "could not extract /boot/vmlinuz, /boot/all.rdz and /boot/grub2/grub.cfg from $ISO_NAME"
chmod u+w "$work"/vmlinuz "$work"/all.rdz "$work"/grub.cfg
# The stock "Install Mageia" entry's own options, as the DVD boots them.
grep -q 'automatic=method:cdrom' "$work/grub.cfg" || die "the DVD's grub.cfg has no automatic=method:cdrom"

# --- Identity and the profile -----------------------------------------------------------
umask 077
password=$(openssl rand -base64 18)
printf '%s\n' "$password" >root-password
hash=$(printf '%s' "$password" | openssl passwd -6 -stdin)
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$name test guest" -f id_ed25519
umask 022
python3 -I - "$here/mageia/auto_inst.cfg.pl.in" "$here/mageia/post-install.sh" "$work/auto_inst.cfg.pl" \
    "$name" "$USERNAME" "$hash" "$(cat id_ed25519.pub)" <<'EOF'
import sys
tmpl, post, out, host, user, hsh, key = sys.argv[1:]
p = open(post).read().replace("@AUTHORIZED_KEY@", key)
assert "\nBKO_POST\n" not in "\n" + p + "\n", "post-install.sh has a BKO_POST line"
t = open(tmpl).read()
for k, v in (("@HOSTNAME@", host), ("@USERNAME@", user), ("@ROOT_PASSWORD_HASH@", hsh)):
    t = t.replace(k, v)
t = t.replace("@POST_INSTALL@\n", p if p.endswith("\n") else p + "\n")
open(out, "w").write(t)
EOF
if grep -n '@[A-Z_]\+@' "$work/auto_inst.cfg.pl"; then die "auto_inst.cfg.pl placeholders left unfilled"; fi
perl -c "$work/auto_inst.cfg.pl" 2>&1 | grep -q 'syntax OK' || { perl -c "$work/auto_inst.cfg.pl"; die "auto_inst.cfg.pl doesn't parse"; }
bash -n "$here/mageia/post-install.sh" || die "mageia/post-install.sh doesn't parse"

# The USB key: MBR, one FAT partition from 1 MiB, auto_inst.cfg.pl on it.
truncate -s 16M "$work/stick.raw"
echo 'start=2048, type=e' | sfdisk -q "$work/stick.raw" >/dev/null || die "sfdisk on the stick failed"
truncate -s 15M "$work/fat.raw"
mformat -i "$work/fat.raw" -v BKOAUTO -T $((15 * 2048)) -h 64 -s 32 -H 2048 :: || die "mformat failed"
mcopy -i "$work/fat.raw" "$work/auto_inst.cfg.pl" ::auto_inst.cfg.pl || die "mcopy failed"
dd if="$work/fat.raw" of="$work/stick.raw" bs=1M seek=1 conv=notrunc status=none

# --- Install ---------------------------------------------------------------------------
free_port() { python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
rm -f uefi-vars.fd install-screen.ppm
cp "$OVMF_VARS" uefi-vars.fd
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
append="automatic=method:cdrom kickstart=floppy audit=0 noiswmd console=tty0 console=ttyS0,115200n8"
echo "Installing; the installer's console goes to $wdir/install-serial.log"
t0=$(date +%s)
SSH_PORT=$(free_port) MEMORY=$INSTALL_MEMORY timeout "$INSTALL_TIMEOUT" "$here/vm-run" --uefi --no-secure-boot \
    --uefi-vars uefi-vars.fd --cpus 4 --discard --serial-log install-serial.log "$DISK" -- \
    -drive "file=$iso,media=cdrom,readonly=on" \
    -kernel "$work/vmlinuz" -initrd "$work/all.rdz" -append "$append" \
    -device qemu-xhci,id=xhci -drive "if=none,id=stick,format=raw,file=$work/stick.raw" \
    -device usb-storage,bus=xhci.0,drive=stick,removable=on \
    -monitor "unix:$work/mon,server=on,wait=off" -no-reboot \
    </dev/null >install.vm-run.log 2>&1 &
qemu_pid=$!
# The installer's screen, every minute: what it shows when it waits.  A
# DrakX error ("error :(" over the serial console, then "Press <Enter> to
# reboot") waits for a key: QEMU is told to quit, and the build fails.
mon() {
    python3 -I - "$work/mon" "$1" <<'EOF' 2>/dev/null || :
import socket, sys, time
s = socket.socket(socket.AF_UNIX); s.settimeout(10); s.connect(sys.argv[1])
time.sleep(1); s.sendall(sys.argv[2].encode() + b"\n"); time.sleep(2); s.close()
EOF
}
(
    while sleep 60; do
        mon "screendump $wdir/install-screen.ppm.new"
        [[ -s $wdir/install-screen.ppm.new ]] && mv -f "$wdir/install-screen.ppm.new" "$wdir/install-screen.ppm"
        if grep -aq 'error :(' install-serial.log && grep -aq 'Press <Enter> to reboot' install-serial.log; then
            echo "make-mageia: DrakX stopped at an error; quitting QEMU" >&2
            mon quit
        fi
    done
) &
shot_pid=$!
rc=0; wait "$qemu_pid" || rc=$?
qemu_pid=""
kill "$shot_pid" 2>/dev/null; wait "$shot_pid" 2>/dev/null || :; shot_pid=""
echo "The install took $(( $(date +%s) - t0 )) s (exit $rc)"
grep -a BACKOTTER-INSTALL-FAILED install-serial.log >&2 && die "post-install failed; see install-serial.log"
if grep -aq 'error :(' install-serial.log && grep -aq 'Press <Enter> to reboot' install-serial.log; then
    grep -a -B3 'Press <Enter> to reboot' install-serial.log | sed 's/\x1b\[[0-9;?]*[A-Za-z]//g' >&2
    die "DrakX stopped at an error; see install-serial.log"
fi
[[ $rc == 0 ]] || die "the install failed or timed out (exit $rc); see install-serial.log and install-screen.ppm"
[[ $(qemu-img info --output=json "$DISK" | python3 -c 'import json, sys; print(json.load(sys.stdin)["actual-size"])') -gt 2000000000 ]] ||
    die "the disk is nearly empty; see install-serial.log and install-screen.ppm"
echo "--uefi --no-secure-boot --guest-vars uefi-vars.fd" >vm-run-options

# --- Boots --------------------------------------------------------------------------
# boot LOG OPTS...: vm-run in the background on a free SSH port, until root
# answers on SSH (else die).
boot() {
    local log=$1; shift
    sport=$(free_port)
    ssh_root=(ssh -q -i id_ed25519 -p "$sport" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
        -o BatchMode=yes -o ConnectTimeout=5 -o ServerAliveInterval=30 root@127.0.0.1)
    rm -f "$log"
    SSH_PORT=$sport setsid "$here/vm-run" "$@" --serial-log "$log" "$DISK" </dev/null >"$work/vm-run.log" 2>&1 &
    qemu_pid=$!
    local up=no
    for _ in $(seq $((BOOT_TIMEOUT / 5))); do
        kill -0 "$qemu_pid" 2>/dev/null || break
        "${ssh_root[@]}" true </dev/null 2>/dev/null && { up=yes; break; }
        sleep 5
    done
    [[ $up == yes ]] || { cat "$work/vm-run.log" >&2; die "the guest never answered on SSH; see $wdir/$log"; }
}
off() {
    "${ssh_root[@]}" 'sync; systemctl poweroff' </dev/null 2>/dev/null || :
    for _ in $(seq 60); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 2; done
    kill "$qemu_pid" 2>/dev/null && die "the guest did not power off"
    wait "$qemu_pid" 2>/dev/null || :
    qemu_pid=""
}

echo "First boot, on uefi-vars.fd; the console goes to $wdir/first-boot-serial.log"
boot first-boot-serial.log --uefi --no-secure-boot --guest-vars uefi-vars.fd --discard
# shellcheck disable=SC2016  # run in the guest
"${ssh_root[@]}" 'exec 2>&1; cat /root/drakx/bko-post-install.log; echo "== post-install log end"
    set -x
    systemctl is-system-running --wait; systemctl --failed --no-legend
    cat /etc/os-release; uname -r; cat /proc/cmdline; hostname; cat /etc/hostname; hostnamectl
    ip -br link; ip -br addr; ls /etc/sysconfig/network-scripts; cat /etc/sysconfig/network-scripts/ifcfg-*
    systemctl is-enabled network NetworkManager sshd shorewall; systemctl is-active shorewall
    cat /etc/shorewall/interfaces /etc/shorewall/zones /etc/shorewall/policy /etc/shorewall/rules.drakx
    grep -n PermitRootLogin /etc/ssh/sshd_config; ls /etc/ssh/sshd_config.d; sshd -T | grep -Ei "^(permitrootlogin|passwordauthentication) "
    cat /etc/security/msec/security.conf; grep -E "^(BASE_LEVEL|CHECK_PERMS)" /etc/security/msec/*.conf
    cat /etc/fstab; findmnt -rno TARGET,SOURCE,FSTYPE,OPTIONS; swapon --show
    lsblk -o NAME,SIZE,TYPE,FSTYPE,PARTTYPE,PARTTYPENAME,PARTLABEL,UUID,MOUNTPOINTS; sfdisk -d /dev/vda
    ls -la /boot /boot/grub2; readlink /boot/efi; find /boot/EFI -ls
    ls -la /boot/EFI/EFI/BOOT; cat /boot/EFI/EFI/mageia/grub.cfg
    strings /boot/EFI/EFI/mageia/grubx64.efi | grep -E "/boot/grub2|search|fs_uuid|hd0|prefix" | head -20
    wc -c /boot/grub2/grubenv; od -c /boot/grub2/grubenv | head -8
    cat /boot/grub2/install.sh; cat /boot/grub2/install.sh.old; cat /etc/default/grub
    grep -E "^(menuentry|submenu|[[:space:]]*(linux|initrd|search))" /boot/grub2/grub.cfg | head -20
    efibootmgr -v
    lsinitrd -m; ls /etc/dracut.conf.d; cat /etc/dracut.conf.d/*.conf
    ls -la /root/drakx; cat /etc/urpmi/urpmi.cfg; urpmq --list-media active
    ls -l /usr/bin/dnf; rpm -q urpmi dnf5 remove-old-kernels kernel-desktop-latest grub2 grub2-efi grub2-common dracut msec shorewall openssh-server task-xfce
    rpm -qa | wc -l; df -h /; free -m; id tester; grep -E "^(root|tester):" /etc/shadow | cut -c1-12' </dev/null >first-boot.txt || :
grep -q '^BACKOTTER-INSTALL-OK$' first-boot.txt || die "post-install didn't finish; see first-boot.txt"
grep -q '^== post-install log end$' first-boot.txt || die "no post-install log; see first-boot.txt"
echo "Swapping the DVD's urpmi media for the online ones"
"${ssh_root[@]}" 'set -ex; exec 2>&1; urpmi.removemedia -a; urpmi.addmedia --distrib --mirrorlist "\$MIRRORLIST"
    cat /etc/urpmi/urpmi.cfg; urpmq --list-media active; urpmq --list-media
    fstrim -av' </dev/null >>first-boot.txt || die "urpmi.addmedia failed; see first-boot.txt"
off

echo "Check boot, alone (vm-run-options, --snapshot); the console goes to $wdir/check-boot-serial.log"
read -ra opts <vm-run-options
boot check-boot-serial.log "${opts[@]}" --snapshot
"${ssh_root[@]}" 'set -x; exec 2>&1; systemctl is-system-running --wait; systemctl --failed --no-legend
    uname -r; cat /proc/cmdline; hostname; efibootmgr -v; findmnt -no SOURCE,FSTYPE /; findmnt -no SOURCE,FSTYPE /boot/EFI
    urpmq --list-media active' </dev/null >check-boot.txt || :
off
fact() { awk -v h="+ $1" '$0 == h { f = 1; next } f && /^\+ / { exit } f' check-boot.txt; }   # CMD's output there
current=$(awk '$1 == "BootCurrent:" { print $2 }' check-boot.txt)
[[ -n $current ]] || die "the check boot has no BootCurrent; see check-boot.txt"
grep -E "^Boot$current\\*? " check-boot.txt | grep -qi 'mageia' ||
    die "the check boot didn't come through DrakX's mageia entry (BootCurrent $current); see check-boot.txt"
grep -q 'ext4$' <<<"$(fact 'findmnt -no SOURCE,FSTYPE /')" || die "/ isn't ext4; see check-boot.txt"
grep -q 'vfat$' <<<"$(fact 'findmnt -no SOURCE,FSTYPE /boot/EFI')" || die "no ESP at /boot/EFI; see check-boot.txt"
grep -Eq '^ *(running|degraded)$' check-boot.txt || die "the check boot isn't up; see check-boot.txt"
grep -q 'Core Release' <<<"$(fact 'urpmq --list-media active')" || die "no online Core Release medium; see check-boot.txt"

qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $wdir/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
