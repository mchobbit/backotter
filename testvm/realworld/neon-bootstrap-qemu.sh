#!/usr/bin/env bash
# Rehearse neon-bootstrap.sh end to end in QEMU: the rescue stick installs
# KDE neon 24.04 User Edition onto a blank NVMe disk the way neon's Calamares
# does "Erase disk", from the ISO on a USB drive, then that disk alone boots
# shim by the NVRAM entry grub-install made.  On a KVM host with a route to
# the Ubuntu and neon archives (the bootstrap installs openssh-server and
# avahi-daemon with apt, through QEMU's user network):
#
#   neon-bootstrap-qemu.sh TEST_IMG ISO WORK_DIR [PORT]
#
# TEST_IMG is a rescue image from "make-img.sh --test" (copied, never
# written); ISO neon-user-desk-20261025-1825.iso (in
# /bkoscratch/rebuild-inputs/neon24/ on the build hosts; its sha256 is
# checked by neon-bootstrap.sh).  Everything is written in WORK_DIR: the
# stick's copy (with a fresh key added by stick/add-ssh-key.sh), a 40 GiB
# NVMe disk (serial BKONEONTEST), a USB drive (ext4, label BKONEONDRIVE)
# holding the ISO, the script and the keys in backshot1/'s places (as
# mybig512 does: the ISO beside the script in backshot1/neon/), OVMF's
# variables, keys, console logs.  The firmware is OVMF's Secure Boot build
# with an empty store, so Secure Boot is off but supported, as on the
# Thinkpad's passing run.  8 GiB of RAM, as the Thinkpad has.  Two boots:
#
#   rescue  the stick, the blank disk and the drive: neon-bootstrap.sh; then
#           the teardown: nothing of the disk or /run/bko-neon.* still
#           mounted, in any process's mount namespace, no loop device left
#           on the ISO or its squashfs, an NVRAM entry for
#           \EFI\neon\shimx64.efi, and the drive unmounts
#   neon    the disk alone, from NVRAM: SSH as root with the fixed host key,
#           os-release ID=neon, the boot finished with no failed unit, the
#           firmware booted \EFI\neon\shimx64.efi, sshd and avahi enabled,
#           / ext4, /boot/efi vfat "EFI", /swapfile the swap, the ESP's
#           EFI/neon, EFI/ubuntu/grub.cfg and fallback shim, grub.cfg with
#           no other disk's system in it ("(on /dev/...)") and the build's
#           os-prober drop-in gone, no live-session package left, tester
#
# DISK_DIR=DIR (the run's own) puts the NVMe disk's image in DIR instead: a
# cool disk, as cmp-run.sh's "!" does, where a small host's RAM disk can't
# hold the install.
#
# Prints PASS and exits 0 if all of that held.

set -euo pipefail
IMG=${1:?usage: neon-bootstrap-qemu.sh TEST_IMG ISO WORK_DIR [PORT]}
ISO=${2:?}
W=${3:?}
PORT=${4:-2990}
HERE=$(cd "$(dirname "$0")" && pwd)
OVMF=/usr/share/OVMF
SERIAL=BKONEONTEST LABEL=BKONEONDRIVE
export PATH=$PATH:/usr/sbin:/sbin
fails=()
die() { echo "neon-bootstrap-qemu: $*" >&2; exit 1; }
check() { if "${@:2}"; then echo "  ok    $1"; else echo "  FAIL  $1"; fails+=("$1"); fi; }

mkdir -p "$W"
W=$(cd "$W" && pwd)
[[ -s $IMG && -s $ISO ]] || die "no $IMG or $ISO"
K=$W/keys
rm -rf "$K"; mkdir -p -m 700 "$K"
ssh-keygen -q -t ed25519 -N '' -C neon-bootstrap-qemu -f "$K/id_ed25519"
ssh-keygen -q -t ed25519 -N '' -C root@neon-bootstrap-qemu -f "$K/ssh_host_ed25519_key"
cp "$K/id_ed25519.pub" "$K/authorized_keys"
printf 'neon-bootstrap-qemu %s\n' "$(cut -d' ' -f1,2 "$K/ssh_host_ed25519_key.pub")" >"$K/known_hosts"

cp "$IMG" "$W/stick.img"
"$HERE/../../stick/add-ssh-key.sh" --host-key "$K/ssh_host_ed25519_key" "$W/stick.img" "$K/id_ed25519.pub" >/dev/null
qemu-img create -q -f qcow2 -F raw -b "$W/stick.img" "$W/stick.qcow2" 16G
D=${DISK_DIR:-$W}
mkdir -p "$D"
INTERNAL=$(cd "$D" && pwd)/internal.qcow2
rm -f "$INTERNAL"
qemu-img create -q -f qcow2 "$INTERNAL" 40G
cp "$OVMF/OVMF_VARS_4M.fd" "$W/vars.fd"
# The drive: one ext4 partition with the ISO, the script and the keys in
# backshot1/'s places (backshot1/neon/ for the script and the ISO,
# backshot1/keys/).
rm -rf "$W/drive.d" "$W/drive.raw"
mkdir -p "$W/drive.d/backshot1/neon" "$W/drive.d/backshot1/keys"
cp "$ISO" "$HERE/neon-bootstrap.sh" "$W/drive.d/backshot1/neon/"
cp "$K/authorized_keys" "$K/ssh_host_ed25519_key" "$K/ssh_host_ed25519_key.pub" "$W/drive.d/backshot1/keys/"
truncate -s 8G "$W/drive.raw"
sfdisk -q --label gpt "$W/drive.raw" <<<",,L"
mkfs.ext4 -q -F -L "$LABEL" -E offset=1048576 -d "$W/drive.d" "$W/drive.raw" 8000M
rm -rf "$W/drive.d"

OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=30 -i "$K/id_ed25519" -o IdentitiesOnly=yes
      -o HostKeyAlias=neon-bootstrap-qemu -o UserKnownHostsFile="$K/known_hosts" -o StrictHostKeyChecking=yes -p "$PORT")
on() { local who=$1; shift; ssh "${OPTS[@]}" "$who@127.0.0.1" "$@"; }

qemu_pid=
vm() {      # vm NAME [rescue]: starts QEMU in the background
    local d=(-drive "if=none,id=internal,format=qcow2,file=$INTERNAL" -device "nvme,drive=internal,serial=$SERIAL")
    [[ ${2:-} != rescue ]] || d+=(-drive "if=none,id=stick,format=qcow2,file=$W/stick.qcow2" -device "usb-storage,bus=usb.0,drive=stick,bootindex=1"
                                  -drive "if=none,id=drive,format=raw,file=$W/drive.raw" -device "usb-storage,bus=usb.0,drive=drive,serial=$LABEL")
    qemu-system-x86_64 -enable-kvm -cpu host -smp 4 -m 8192 -display none -monitor none \
        -machine q35,smm=on -global driver=cfi.pflash01,property=secure,value=on \
        -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF/OVMF_CODE_4M.secboot.fd" \
        -drive "if=pflash,format=raw,unit=1,file=$W/vars.fd" \
        -device qemu-xhci,id=usb -device virtio-rng-pci "${d[@]}" \
        -netdev "user,id=n,hostfwd=tcp:127.0.0.1:$PORT-:22" -device virtio-net-pci,netdev=n \
        -serial "file:$W/console-$1.log" </dev/null >"$W/qemu-$1.log" 2>&1 &
    qemu_pid=$!
}
wait_ssh() {     # wait_ssh USER SECONDS
    local end=$((SECONDS + $2))
    while ((SECONDS < end)); do
        kill -0 "$qemu_pid" 2>/dev/null || return 1
        on "$1" true 2>/dev/null && return 0
        sleep 5
    done
    return 1
}
vm_off() {      # vm_off USER CMD: CMD powers it off; then waits, and kills only this QEMU
    on "$1" "$2" >/dev/null 2>&1 || true
    for _ in $(seq 120); do kill -0 "$qemu_pid" 2>/dev/null || return 0; sleep 1; done
    kill "$qemu_pid" 2>/dev/null; sleep 3; kill -9 "$qemu_pid" 2>/dev/null || true
}
trap '[[ -z $qemu_pid ]] || kill "$qemu_pid" 2>/dev/null || true' EXIT

echo "== rescue: the stick, a blank $SERIAL and the drive"
vm rescue rescue
wait_ssh backotter 400 || die "the stick never answered on SSH (console-rescue.log)"
on backotter 'test -f /cdrom/.disk/backotter.json' || die "that isn't the rescue stick"
B=/mnt/bs1-backup P=/mnt/bs1-backup/backshot1/neon
t0=$SECONDS
set +e
on backotter "sudo -n bash -c 'mkdir -p $B && mount LABEL=$LABEL $B && cd / && bash $P/neon-bootstrap.sh $SERIAL $P/$(basename "$ISO") $B/backshot1/keys neontest'" >"$W/bootstrap.log" 2>&1
rc=$?
set -e
echo "  neon-bootstrap.sh: exit $rc after $((SECONDS - t0)) s (bootstrap.log)"
grep -E '^copy took' "$W/bootstrap.log" || true
tail -n 3 "$W/bootstrap.log"
check "neon-bootstrap.sh ran to the end" grep -qx BKO-NEON-INSTALL-OK "$W/bootstrap.log"
check "nothing of the target is still mounted" on backotter "! findmnt -rn -o SOURCE | grep -q nvme0n1"
check "nothing of the bootstrap's /run/bko-neon.* is left" on backotter "! findmnt -rn -o TARGET | grep -q '^/run/bko-neon' && ! ls -d /run/bko-neon.* >/dev/null 2>&1"
check "no process's mount namespace still holds the target" on backotter "sudo -n sh -c '! grep -qs /run/bko-neon /proc/[0-9]*/mountinfo'"
check "no loop device is left on the ISO or its squashfs" on backotter "sudo -n sh -c 'test -z \"\$(losetup -nO BACK-FILE | grep -E \"bs1-backup|bko-neon\")\"'"
on backotter 'sudo -n efibootmgr -v' >"$W/efibootmgr-rescue.txt" 2>&1 || true
check "grub-install made an NVRAM entry for \\EFI\\neon\\shimx64.efi" grep -qi 'EFI.neon.shimx64.efi' "$W/efibootmgr-rescue.txt"
check "the drive unmounts (the ISO isn't busy)" on backotter "sudo -n umount $B"
on backotter "sudo -n systemctl poweroff" >/dev/null 2>&1 || true
for _ in $(seq 120); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 1; done
kill "$qemu_pid" 2>/dev/null || true
qemu_pid=

if ((${#fails[@]} == 0)); then
    echo "== neon: the disk alone, from NVRAM"
    t0=$SECONDS
    vm neon
    if wait_ssh root 400; then
        echo "  ok    root's SSH, with the fixed host key, $((SECONDS - t0)) s after power-on"
        on root 'systemctl is-system-running --wait' >"$W/state.txt" 2>&1 || true
        on root 'systemctl --failed --no-legend --plain' >"$W/failed.txt" 2>&1 || true
        on root 'cat /usr/lib/os-release; hostname; efibootmgr -v | grep -Ev "^ +(dp|data):"; systemctl is-enabled ssh.service avahi-daemon.service;
                 findmnt -R /; lsblk -o NAME,SIZE,FSTYPE,LABEL,PARTTYPE; swapon --show; cat /etc/fstab; ls -la /boot;
                 find /boot/efi -type f | sort; ls -la /etc/default/grub.d; grep -E "^\s*(menuentry|submenu) " /boot/grub/grub.cfg;
                 mokutil --sb-state; id tester' >"$W/neon-facts.txt" 2>&1 || true
        check "the boot finished: $(cat "$W/state.txt")" grep -qxE 'running|degraded' "$W/state.txt"
        check "no unit failed: $(tr '\n' ' ' <"$W/failed.txt")" test ! -s "$W/failed.txt"
        check "os-release says ID=neon" grep -qx 'ID=neon' "$W/neon-facts.txt"
        check "hostname is neontest" test "$(on root cat /proc/sys/kernel/hostname)" = neontest
        check "sshd and avahi are enabled" on root 'systemctl is-enabled -q ssh.service && systemctl is-enabled -q avahi-daemon.service'
        check "/ is ext4, /boot/efi vfat labelled EFI" \
            on root 'test "$(findmnt -no FSTYPE /)" = ext4 && test "$(findmnt -no FSTYPE /boot/efi)" = vfat &&
                     test "$(lsblk -no LABEL "$(findmnt -no SOURCE /boot/efi)")" = EFI'
        check "/swapfile is the swap, from fstab, and no swap partition" \
            on root 'test "$(swapon --show=NAME,TYPE --noheadings --raw)" = "/swapfile file" && grep -qE "^/swapfile\s+swap\s+swap\s" /etc/fstab'
        check "the ESP has EFI/neon's shim, GRUB, BOOTX64.CSV and grub.cfg, EFI/ubuntu/grub.cfg" \
            on root 'e=/boot/efi/EFI; for f in neon/shimx64.efi neon/grubx64.efi neon/BOOTX64.CSV neon/grub.cfg ubuntu/grub.cfg; do test -s $e/$f || exit 1; done'
        check "the fallback path is shim (EFI/BOOT/BOOTX64.EFI = EFI/neon/shimx64.efi) with fbx64.efi" \
            on root 'cmp -s /boot/efi/EFI/neon/shimx64.efi /boot/efi/EFI/BOOT/BOOTX64.EFI && test -s /boot/efi/EFI/BOOT/fbx64.efi'
        check "grub.cfg boots a kernel and lists no other disk's system (\"(on /dev/\")" \
            on root 'grep -q "^[[:space:]]*linux[[:space:]]" /boot/grub/grub.cfg && ! grep -q "^[[:space:]]*menuentry .*(on /dev/" /boot/grub/grub.cfg'
        check "the build's os-prober drop-in is gone (/etc/default/grub.d/zz-bko-build-no-os-prober.cfg)" \
            on root 'test ! -e /etc/default/grub.d/zz-bko-build-no-os-prober.cfg'
        check "no live-session package is left (calamares, neon-live, casper)" \
            on root '! dpkg-query -W -f "\${db:Status-Status} \${Package}\n" calamares neon-live casper 2>/dev/null | grep -q "^installed "'
        check "the user tester, its password in /root/tester-password (600)" \
            on root 'id tester >/dev/null && test "$(stat -c %a /root/tester-password)" = 600'
        cur=$(on root "efibootmgr | sed -n 's/^BootCurrent: //p'")
        check "the firmware booted Boot$cur, \\EFI\\neon\\shimx64.efi" sh -c "grep -E '^Boot$cur' '$W/neon-facts.txt' | grep -qi 'EFI.neon.shimx64.efi'"
        vm_off root 'systemctl poweroff'
    else
        echo "  FAIL  the installed disk never answered on SSH (console-neon.log)"
        fails+=(neon-boot)
    fi
    qemu_pid=
fi

if ((${#fails[@]})); then echo "FAILED: ${fails[*]}"; exit 1; fi
echo PASS
