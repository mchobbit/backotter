#!/usr/bin/env bash
# Build the KDE Linux test guest for BackOtter (docs/design/kde-linux.md,
# "Guests"), as BackShot1 will install the A100: the Linux rescue stick's
# image boots under QEMU with a blank disk and the inputs as read-only
# disks, and runs kdelinux/install.sh (KDE Linux's Calamares steps,
# replayed) over SSH; then the disk boots alone, by the NVRAM entry
# bootctl made, and takes a second version from a local mirror of
# KDE Linux's sysupdate files, by systemd-sysupdate as a real update does.
#
# Usage: ./make-kdelinux.sh [--iso-dir DIR] [--stick IMG] FLAVOR WORK_DIR
#        ./make-kdelinux.sh [--iso-dir DIR] [--stick IMG] --calamares-diff GUI.qcow2 WORK_DIR
#   e.g. ./make-kdelinux.sh --iso-dir /bkoscratch/rebuild-inputs/kdelinux kdelinux vms/kdelinux
#
# FLAVOR is kdelinux (kdelinux-luks is milestone K5).
#
# DIR (default /bkoscratch/rebuild-inputs/kdelinux) holds sysupdate/, a
# mirror of two daily builds' sysupdate sets as
# https://kde-linux-images.s3.kde.org/testing/sysupdate/v2/ served them
# (upstream keeps the 4 newest dailies only): kde-linux_V.efi,
# kde-linux_V_root-x86-64.erofs and kde-linux_V_root-x86-64.caibx for
# V1 and V2 below, and the signed SHA256SUMS(.gpg) that lists them.  Every
# file's SHA-256 is pinned below (the .efi, .erofs and .caibx are also in
# the signed SHA256SUMS).  With --calamares-diff, DIR's ISO and
# sysupdate-v2-SHA256SUMS(.gpg) too.
#
# IMG (default: the newest ~/backotter-work/images/linux/test/*.img, a
# "make-img.sh --test" Linux image) is only copied: stick/add-ssh-key.sh
# lets this build's key in, a qcow2 overlay takes the stick's writes.
#
# The guest kdelinux, host name kdelinux: UEFI, Secure Boot off; GPT, ESP
# 4098M vfat (systemd-boot, the UKIs, at /boot by gpt-auto), the rest
# "KDELinux", type root-x86-64, btrfs with simple quotas: @system at /,
# @system/etc, the image files kde-linux_V.erofs at the top level (/system),
# etc.mstack.v; no fstab.  V1 is installed by install.sh, its first boot
# writes /etc/bko-version (V1), then systemd-sysupdate installs V2 from
# the mirror (the guest's /etc/sysupdate.d copies of the image's three
# transfers keep InstancesMax=2, decision 8, and keep upstream's Path;
# the mirror is a /run drop-in for that update only), V2 boots and writes
# /etc/bko-version (V2) into its own /etc layer.  So two UKIs
# (EFI/Linux/kde-linux_V.efi), two image files, two etc_V.mstack; V2 the
# default, V1 by `bootctl set-oneshot kde-linux_V1.efi`.  root (locked, as
# the image has it) logs in by key; "tester" (wheel, zsh, home a btrfs
# subvolume with a snapper config home_1000 and one snapshot) by
# tester-password, made at the first boot (as plasma-setup would make it,
# so useradd takes the factory /etc/default/useradd's subvolume home);
# sshd on; multi-user.target the default; plasma-setup's
# wizard off (/etc/plasma-setup-done); a global UKI addon
# loader/addons/bko-console.addon.efi adds console=ttyS0,115200n8.
#
# --calamares-diff GUI.qcow2: K1's check of install.sh against the real
# installer.  GUI.qcow2 is a disk Calamares installed by hand from DIR's ISO
# ("Erase disk", nothing else chosen, never booted).  install.sh installs the
# same ISO, as the installer would (no --guest), onto a scratch disk, and
# kdelinux/list-install.py lists both from the stick: WORK_DIR/
# calamares-gui.txt, calamares-script.txt and calamares-diff.txt.  Nothing
# else is built.
#
# Results, in WORK_DIR:
#   kdelinux.qcow2        the 64 GiB disk (sparse, ~18 GB), qcow2 snapshot
#                         "fresh-install" taken at the end
#   uefi-vars.fd          the OVMF variables, with bootctl's "Linux Boot
#                         Manager" entry (vm-run boots fresh ones: the
#                         fallback loader, EFI/BOOT/BOOTX64.EFI, the same
#                         systemd-boot)
#   tester-password       tester's password (mode 600); root has none
#   id_ed25519, .pub      the SSH key that logs in as root
#   install.log, install-serial.log, *-boot-serial.log, first-boot.txt,
#   update.log, versions.txt
#   vm-run-options        "--uefi --no-secure-boot --memory 4096"
#
# Needs: qemu-system-x86_64 with KVM, OVMF (OVMF_CODE_4M.fd), qemu-img,
# python3, openssl, ssh, sfdisk/mtools (add-ssh-key.sh), mkfs.ext4.  Runs
# as an ordinary user in the kvm group.  ~20 GB in WORK_DIR, 4+4 GiB of
# memory while the stick installs.

set -euo pipefail
export PATH=$PATH:/usr/sbin:/sbin

V1=202611240254 V2=202611270255
declare -A PIN=(
    [kde-linux_${V1}.efi]=e57847777a1743d8c864a88dff32e8b8c6ba624cbca02e259e3b0d277e038405
    [kde-linux_${V1}_root-x86-64.erofs]=58b8f40bbd775c2d131d9b8d691a97022964f5e8ec11373f18178646d56f14e3
    [kde-linux_${V1}_root-x86-64.caibx]=315b81a33dff861e86f9f8f99e9ed23fd32834fd9949fc9bf684d0d9730b8fb8
    [kde-linux_${V2}.efi]=e0450fd98483103b1edc424bfbd88792a813104ba50acd5fe689659db0c549c6
    [kde-linux_${V2}_root-x86-64.erofs]=ef8ad47da56c78a0a84abce7c906cbf7ed3d2fe1631352824c59b2ed20c50902
    [kde-linux_${V2}_root-x86-64.caibx]=367273c8c9c42143c21625b1fe18f11e433190f86edcbc1d7af3ede060663335
)
ISO=kde-linux_${V2}.iso
ISO_SHA256=f3e253ae84c141aa6b2f07129b34d41b3715a17be3ba66254f0060d5b9332baa
DISK_SIZE=64G
MEMORY=4096
OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd
INSTALL_TIMEOUT=3600

here=$(dirname "$(realpath "$0")")
die() { echo "make-kdelinux: $*" >&2; exit 1; }
say() { echo "== $(date +%T) $*"; }

iso_dir=/bkoscratch/rebuild-inputs/kdelinux stick="" gui=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --iso-dir) iso_dir=$(realpath "$2"); shift 2 ;;
        --stick) stick=$(realpath "$2"); shift 2 ;;
        --calamares-diff) gui=$(realpath "$2"); shift 2 ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
if [[ -n $gui ]]; then
    [[ $# -eq 1 ]] || die "usage: $0 [--iso-dir DIR] [--stick IMG] --calamares-diff GUI.qcow2 WORK_DIR"
    flavor=calamares-diff
else
    [[ $# -eq 2 ]] || die "usage: $0 [--iso-dir DIR] [--stick IMG] FLAVOR WORK_DIR"
    flavor=$1; shift
    case $flavor in
        kdelinux) ;;
        kdelinux-luks) die "kdelinux-luks is milestone K5 (docs/design/kde-linux.md), not built yet" ;;
        *) die "FLAVOR is kdelinux, not '$flavor'" ;;
    esac
fi
GUEST_NAME=kdelinux
DISK=$GUEST_NAME.qcow2
set_dir=$iso_dir/sysupdate
if [[ -z $stick ]]; then
    for d in "$here/../images/linux/test" /home/m/backotter-work/images/linux/test; do
        s=$(ls -t "$d"/*.img 2>/dev/null | head -n 1) && [[ -n $s ]] && { stick=$s; break; }
    done
fi
[[ -s $stick ]] || die "no rescue stick image: give --stick IMG (a make-img.sh --test Linux image)"
mkdir -p "$1"
cd "$1"
[[ $flavor == calamares-diff || ! -e $DISK ]] || die "$1/$DISK already exists; move it away to rebuild"

# --- The inputs, checked against their pins ----------------------------------------
check() {  # FILE: its SHA-256 is the pinned one
    local f=$1 b=${1##*/}
    [[ -s $f ]] || die "no $f"
    [[ $(sha256sum <"$f" | cut -d' ' -f1) == "${PIN[$b]}" ]] || die "$b's SHA-256 isn't the pinned ${PIN[$b]}"
}
say "checking the inputs in $iso_dir"
if [[ $flavor == calamares-diff ]]; then
    [[ -s $iso_dir/$ISO && -s $iso_dir/sysupdate-v2-SHA256SUMS && -s $iso_dir/sysupdate-v2-SHA256SUMS.gpg ]] ||
        die "no $ISO or sysupdate-v2-SHA256SUMS(.gpg) in $iso_dir"
    [[ $(sha256sum <"$iso_dir/$ISO" | cut -d' ' -f1) == "$ISO_SHA256" ]] || die "$ISO's SHA-256 isn't the pinned $ISO_SHA256"
    [[ -s $gui ]] || die "no $gui"
else
    for b in "${!PIN[@]}"; do check "$set_dir/$b"; done
    for b in SHA256SUMS SHA256SUMS.gpg; do [[ -s $set_dir/$b ]] || die "no $set_dir/$b"; done
fi

work=$(mktemp -d)
qemu_pid="" http_pid=""
cleanup() {
    [[ -z $qemu_pid ]] || kill "$qemu_pid" 2>/dev/null || true
    [[ -z $http_pid ]] || kill "$http_pid" 2>/dev/null || true
    rm -rf "$work"
}
trap cleanup EXIT

# --- Keys ---------------------------------------------------------------------------------
umask 077
[[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$GUEST_NAME test guest" -f id_ed25519
if [[ $flavor != calamares-diff ]]; then
    password=$(openssl rand -base64 18)
    printf '%s\n' "$password" >tester-password
    rm -f root-password
fi
umask 022

port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
sshopts=(-q -i id_ed25519 -p "$port" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
    -o ConnectTimeout=5 -o ServerAliveInterval=30 -o IdentitiesOnly=yes)
on() {  # USER CMD...: ssh into whatever is booted
    local u=$1; shift
    ssh "${sshopts[@]}" "$u@127.0.0.1" "$@"
}
wait_ssh() {  # USER SECONDS
    local _
    for _ in $(seq $(($2 / 5))); do
        kill -0 "$qemu_pid" 2>/dev/null || return 1
        on "$1" true </dev/null 2>/dev/null && return 0
        sleep 5
    done
    return 1
}
stop_guest() {  # USER
    on "$1" 'sync; sudo -n systemctl poweroff 2>/dev/null || systemctl poweroff' </dev/null >/dev/null 2>&1 || true
    local _
    for _ in $(seq 90); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 2; done
    kill "$qemu_pid" 2>/dev/null && die "the VM did not power off"
    wait "$qemu_pid" 2>/dev/null || true
    qemu_pid=""
}
# Plain q35 with OVMF_CODE_4M.fd and its own variable store, as vm-run
# --no-secure-boot boots.
uefi=(-machine q35 -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE"
    -drive "if=pflash,format=raw,unit=1,file=uefi-vars.fd")
net=(-nic "user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$port-:22")

# --- The stick: install.sh ----------------------------------------------------------------
# The stick on USB (it boots first), the target as a virtio disk (as vm-run
# gives it), the inputs as read-only virtio disks found by their serials,
# and a small ext4 drive with the scripts, sums and keys.
stick_boot() {  # LOG TARGET_DISK... (each as FILE:SERIAL[:ro])
    local log=$1 d drives=() f s ro i=0
    shift
    cp "$stick" "$work/stick.img"
    "$here/../stick/add-ssh-key.sh" "$work/stick.img" id_ed25519.pub >/dev/null
    qemu-img create -q -f qcow2 -F raw -b "$work/stick.img" "$work/stick.qcow2" 16G
    for d in "$@"; do
        IFS=: read -r f s ro <<<"$d"
        i=$((i + 1))
        case $f in
            *.qcow2) drives+=(-drive "if=none,id=d$i,format=qcow2,file=$f${ro:+,readonly=on}") ;;
            *) drives+=(-drive "if=none,id=d$i,format=raw,file=$f${ro:+,readonly=on}") ;;
        esac
        drives+=(-device "virtio-blk-pci,drive=d$i,serial=$s")
    done
    qemu-system-x86_64 -enable-kvm -cpu host -smp 4 -m 4096 "${uefi[@]}" \
        -device qemu-xhci,id=usb -drive "if=none,id=stick,format=qcow2,file=$work/stick.qcow2" \
        -device usb-storage,bus=usb.0,drive=stick,bootindex=1 "${drives[@]}" \
        -device virtio-rng-pci "${net[@]}" -display none -serial "file:$log" -monitor none &
    qemu_pid=$!
    wait_ssh backotter 400 || die "the rescue stick never answered on SSH; see $PWD/$log"
    on backotter 'test -f /cdrom/.disk/backotter.json' || die "$stick isn't the rescue stick"
}
# The small drive: install.sh, list-install.py, the sums and the keys.
make_drive() {  # DIR_ON_DRIVE_SOURCE
    rm -f "$work/drive.raw"
    truncate -s 64M "$work/drive.raw"
    mkfs.ext4 -q -F -L BKOKDL -d "$1" "$work/drive.raw"
}
mkdir -p "$work/drive/keys" "$work/drive/sums"
cp "$here/kdelinux/install.sh" "$here/kdelinux/list-install.py" "$work/drive/"
cp id_ed25519.pub "$work/drive/keys/authorized_keys"
in_stick='set -e; sudo -n mkdir -p /mnt/kdl; mountpoint -q /mnt/kdl || sudo -n mount -o ro LABEL=BKOKDL /mnt/kdl'
dev_of() { echo "/dev/disk/by-id/virtio-$1"; }

if [[ $flavor == calamares-diff ]]; then
    cp "$iso_dir/sysupdate-v2-SHA256SUMS" "$iso_dir/sysupdate-v2-SHA256SUMS.gpg" "$work/drive/sums/"
    make_drive "$work/drive"
    cp "$OVMF_VARS" uefi-vars.fd
    rm -f script.qcow2
    qemu-img create -q -f qcow2 script.qcow2 "$DISK_SIZE"
    say "the stick: install.sh (no --guest) from the ISO onto a scratch disk, then both listed"
    stick_boot calamares-serial.log "$work/drive.raw:BKODRIVE:ro" "$iso_dir/$ISO:BKOISO:ro" \
        "script.qcow2:BKOSCRIPT" "$gui:BKOGUI:ro"
    on backotter "$in_stick; sudo -n bash /mnt/kdl/install.sh --image $(dev_of BKOISO) $(dev_of BKOSCRIPT) /mnt/kdl/sums kdelinux" \
        </dev/null >calamares-install.log 2>&1 || true
    grep -q BACKOTTER-INSTALL-OK calamares-install.log || { tail -n 30 calamares-install.log >&2; die "install.sh failed; see calamares-install.log"; }
    on backotter "$in_stick; sudo -n python3 /mnt/kdl/list-install.py $(dev_of BKOGUI)" </dev/null >calamares-gui.txt
    on backotter "$in_stick; sudo -n python3 /mnt/kdl/list-install.py $(dev_of BKOSCRIPT)" </dev/null >calamares-script.txt
    stop_guest backotter
    diff -u calamares-gui.txt calamares-script.txt >calamares-diff.txt || true
    say "Done: $PWD/calamares-diff.txt, $(grep -c '^[-+][^-+]' calamares-diff.txt || true) lines differ"
    exit 0
fi

cp "$set_dir/SHA256SUMS" "$set_dir/SHA256SUMS.gpg" "$work/drive/sums/"
make_drive "$work/drive"
cp "$OVMF_VARS" uefi-vars.fd
qemu-img create -q -f qcow2 "$DISK" "$DISK_SIZE"
say "the stick: install.sh, KDE Linux $V1 from the mirror's set; its output in install.log"
stick_boot install-serial.log "$work/drive.raw:BKODRIVE:ro" \
    "$set_dir/kde-linux_${V1}_root-x86-64.erofs:BKOEROFS:ro" "$DISK:BKOTARGET"
timeout "$INSTALL_TIMEOUT" ssh "${sshopts[@]}" backotter@127.0.0.1 \
    "$in_stick; sudo -n bash /mnt/kdl/install.sh --image $(dev_of BKOEROFS) --guest /mnt/kdl/keys $(dev_of BKOTARGET) /mnt/kdl/sums $GUEST_NAME" \
    </dev/null >install.log 2>&1 || true
on backotter 'sudo -n efibootmgr -v' </dev/null >>install.log 2>&1 || true
grep -q BACKOTTER-INSTALL-OK install.log || { tail -n 30 install.log >&2; die "the install did not finish; see install.log"; }
grep -q 'Linux Boot Manager' install.log || die "bootctl made no NVRAM entry; see install.log"
stop_guest backotter

# --- Boots of the disk alone ---------------------------------------------------------------
boot_disk() {  # LOG [QEMU_ARGS...]
    local log=$1; shift
    qemu-system-x86_64 -enable-kvm -cpu host -smp 2 -m "$MEMORY" "${uefi[@]}" \
        -drive "file=$DISK,if=virtio,format=qcow2,discard=unmap" "${net[@]}" "$@" \
        -display none -serial "file:$log" -monitor none &
    qemu_pid=$!
    wait_ssh root 420 || die "the boot ($log) never answered on SSH as root; see $PWD/$log"
}
# What a boot shows; the boot must be up (running or degraded).
FACTS='systemctl is-system-running --wait; echo "IMAGE_VERSION=$(. /usr/lib/os-release; echo $IMAGE_VERSION)"
    cat /proc/cmdline; uname -r; findmnt -no SOURCE,FSTYPE,OPTIONS /; findmnt -no SOURCE,FSTYPE /usr
    losetup -nO NAME,BACK-FILE; findmnt -no SOURCE,FSTYPE,OPTIONS /etc; findmnt -no SOURCE,FSTYPE,OPTIONS /system
    ls /boot >/dev/null; findmnt -no SOURCE,FSTYPE,OPTIONS /boot; ls -la /boot/EFI/Linux /boot/loader/addons /boot/loader
    cat /boot/loader/loader.conf; ls -la /system /system/etc.mstack.v; btrfs subvolume list -p /system
    cat /etc/bko-version 2>/dev/null; bootctl status 2>/dev/null | grep -E "Current Entry|Default Entry|Boot counting|Loader:"
    bootctl list --no-pager 2>/dev/null | grep -E "^ *(title|id|version|source|tries)"; efibootmgr
    systemctl is-active display-manager.service plasmalogin.service kde-linux-bless-userspace.service kde-linux-bless-boot.service systemd-bless-boot.service sshd.service
    systemctl --failed --no-legend'
facts() {  # FILE TITLE
    { echo "=== $2"; on root "set -x; $FACTS" </dev/null 2>&1; } >>"$1" || true
}
up() {  # FILE TITLE: the last facts' boot is up, and its IMAGE_VERSION
    awk -v t="=== $2" '$0 == t { f = 1; next } f && /^=== / { f = 0 } f' "$1" >"$work/last"
    grep -Eq '^(running|degraded)$' "$work/last" || { cat "$work/last" >&2; die "$2: the boot isn't up"; }
    sed -n 's/^IMAGE_VERSION=//p' "$work/last"
}

say "the first boot: V1 ($V1) from NVRAM"
rm -f first-boot.txt
boot_disk first-boot-serial.log
facts first-boot.txt "first boot"
[[ $(up first-boot.txt "first boot") == "$V1" ]] || die "the first boot isn't $V1; see first-boot.txt"
grep -q 'console=ttyS0,115200n8' first-boot.txt || die "/proc/cmdline has no console=ttyS0: the global addon wasn't loaded"
# The tester's snapper config (as its first login would make it) and one
# snapshot; V1's /etc/bko-version.
on root "set -e; echo $V1 >/etc/bko-version
    systemctl enable sshd.service; systemctl set-default multi-user.target
    rm /etc/systemd/system/multi-user.target.d/bko-sshd.conf; rmdir /etc/systemd/system/multi-user.target.d
    useradd -m -G wheel -s /bin/zsh tester; usermod -p '$(openssl passwd -6 -stdin <tester-password)' tester
    touch /home/tester/.zshrc; chown tester: /home/tester/.zshrc
    systemctl start kde-linux-snapper-home-config@\$(id -u tester).service
    snapper -c home_\$(id -u tester) create -d 'make-kdelinux: first boot'; snapper -c home_\$(id -u tester) list
    btrfs subvolume show /home/tester | head -n 3" </dev/null >>first-boot.txt 2>&1 ||
    die "tester's snapper config or snapshot failed; see first-boot.txt"
# The guest's own sysupdate transfers: the image's three, InstancesMax=2.
on root 'set -e; mkdir -p /etc/sysupdate.d; for f in /usr/lib/sysupdate.d/*.transfer; do
        sed "s/^InstancesMax=.*/InstancesMax=2/" "$f" >/etc/sysupdate.d/"${f##*/}"; done
    grep -H InstancesMax /etc/sysupdate.d/*.transfer' </dev/null >>first-boot.txt 2>&1
# Let the first boot bless itself, if it does (display-manager up).
sleep 30
facts first-boot.txt "first boot, 30 s on"
stop_guest root

# --- The second version, from the mirror -------------------------------------------------
# The mirror: SHA256SUMS(.gpg) as fetched (signed; they list other dailies
# too, which aren't served) and V2's three files.  kde-linux-sysupdated's
# socket redirects the erofs transfer to its delta proxy (a /run drop-in
# its ExecStartPost writes, its ExecStopPre removes) which reads upstream:
# stopped for this update, as the update doc's own workaround does.
mkdir -p "$work/mirror"
for b in SHA256SUMS SHA256SUMS.gpg kde-linux_${V2}.efi kde-linux_${V2}_root-x86-64.erofs kde-linux_${V2}_root-x86-64.caibx; do
    ln -s "$set_dir/$b" "$work/mirror/$b"
done
hport=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
python3 -m http.server --bind 127.0.0.1 --directory "$work/mirror" "$hport" >"$work/http.log" 2>&1 &
http_pid=$!
say "V2 ($V2) by systemd-sysupdate from the mirror at 10.0.2.2:$hport; its output in update.log"
boot_disk update-boot-serial.log
on root "set -x; systemctl stop kde-linux-sysupdated.socket kde-linux-sysupdated.service
    for t in 50-root-x86-64-erofs 50-root-x86-64-caibx 60-esp; do
        mkdir -p /run/sysupdate.d/\$t.transfer.d
        printf '[Source]\nPath=http://10.0.2.2:$hport/\n' >/run/sysupdate.d/\$t.transfer.d/90-bko-mirror.conf
    done
    /usr/lib/systemd/systemd-sysupdate list
    time updatectl update </dev/null
    /usr/lib/systemd/systemd-sysupdate list; ls -la /system /boot/EFI/Linux; ls /system/etc.mstack.v
    rm -rf /run/sysupdate.d" </dev/null >update.log 2>&1 || true
kill "$http_pid" 2>/dev/null || true; http_pid=""
on root "ls /boot/EFI/Linux; ls /system" </dev/null >"$work/after" 2>&1 || true
grep -q "kde-linux_${V2}" "$work/after" && grep -q "kde-linux_${V2}.erofs" "$work/after" ||
    { tail -n 40 update.log >&2; die "the update didn't install $V2; see update.log"; }
stop_guest root

say "the second boot: V2, the default"
boot_disk v2-boot-serial.log
facts first-boot.txt "V2 boot"
[[ $(up first-boot.txt "V2 boot") == "$V2" ]] || die "the default boot after the update isn't $V2; see first-boot.txt"
on root "echo $V2 >/etc/bko-version" </dev/null
sleep 30
facts first-boot.txt "V2 boot, 30 s on"
v1_entry=$(on root "bootctl list --json=short --no-pager" </dev/null | python3 -c '
import json, sys
for e in json.load(sys.stdin):
    if "'"$V1"'" in e.get("id", ""):
        print(e["id"]); break')
[[ -n $v1_entry ]] || die "bootctl lists no entry for $V1"
on root "bootctl set-oneshot $v1_entry" </dev/null
stop_guest root

say "V1 by bootctl set-oneshot $v1_entry"
boot_disk v1-boot-serial.log
facts first-boot.txt "V1 oneshot boot"
[[ $(up first-boot.txt "V1 oneshot boot") == "$V1" ]] || die "the oneshot boot isn't $V1; see first-boot.txt"
[[ $(on root cat /etc/bko-version </dev/null) == "$V1" ]] || die "V1's /etc/bko-version isn't $V1"
stop_guest root

say "the default again, then as vm-run boots it (fresh UEFI variables, the fallback loader)"
boot_disk v2-again-serial.log
facts first-boot.txt "V2 again"
[[ $(up first-boot.txt "V2 again") == "$V2" ]] || die "the default after the oneshot isn't $V2"
[[ $(on root cat /etc/bko-version </dev/null) == "$V2" ]] || die "V2's /etc/bko-version isn't $V2"
stop_guest root
cp "$OVMF_VARS" "$work/fresh-vars.fd"
uefi_saved=("${uefi[@]}")
uefi=(-machine q35 -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE"
    -drive "if=pflash,format=raw,unit=1,file=$work/fresh-vars.fd")
boot_disk fallback-boot-serial.log -snapshot
facts first-boot.txt "fallback boot (fresh UEFI variables)"
[[ $(up first-boot.txt "fallback boot (fresh UEFI variables)") == "$V2" ]] || die "the fallback boot isn't $V2"
stop_guest root
uefi=("${uefi_saved[@]}")

{ echo "V1=$V1"; echo "V2=$V2"; echo "default=$V2"; echo "oneshot_v1=$v1_entry"
  echo "stick=${stick##*/}"; } >versions.txt
qemu-img snapshot -c fresh-install "$DISK"
echo "--uefi --no-secure-boot --memory $MEMORY" >vm-run-options
say "Done: $PWD/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
