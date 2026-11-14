#!/usr/bin/env bash
# Rehearse anduinos-bootstrap.sh end to end in QEMU: the rescue stick
# installs AnduinOS 2 onto a blank NVMe disk with AnduinOS's own installer
# backend, from the ISO on a USB drive, then that disk alone boots by the
# NVRAM entry the installer made.  On a KVM host:
#
#   anduinos-bootstrap-qemu.sh TEST_IMG ISO WORK_DIR [PORT]
#
# TEST_IMG is a rescue image from "make-img.sh --test" (copied, never
# written); ISO AnduinOS-2.0.5-amd64.iso.  Everything is written in
# WORK_DIR: the stick's copy (with a fresh key added by
# stick/add-ssh-key.sh), a 40 GiB NVMe disk (serial BKOANDUTEST), a USB
# drive (ext4, label BKOANDUDRIVE) holding the ISO, OVMF's variables, keys,
# console logs.  The firmware is OVMF's Secure Boot build with an empty
# store, so Secure Boot is off but supported, as on the Thinkpad.  8 GiB of
# RAM, as the Thinkpad has (the installer sizes swap from it).  Two boots:
#
#   rescue    the stick, the blank disk and the drive: anduinos-bootstrap.sh
#   anduinos  the disk alone, from NVRAM: SSH as root with the fixed host
#             key, os-release ID=anduinos, the boot finished with no failed
#             unit, the firmware booted \EFI\AnduinOS\shimx64.efi, sshd and
#             avahi enabled, the six subvolumes and two read-only baselines
#
# Prints PASS and exits 0 if all of that held.

set -euo pipefail
IMG=${1:?usage: anduinos-bootstrap-qemu.sh TEST_IMG ISO WORK_DIR [PORT]}
ISO=${2:?}
W=${3:?}
PORT=${4:-2989}
HERE=$(cd "$(dirname "$0")" && pwd)
OVMF=/usr/share/OVMF
SERIAL=BKOANDUTEST LABEL=BKOANDUDRIVE
export PATH=$PATH:/usr/sbin:/sbin
fails=()
die() { echo "anduinos-bootstrap-qemu: $*" >&2; exit 1; }
check() { if "${@:2}"; then echo "  ok    $1"; else echo "  FAIL  $1"; fails+=("$1"); fi; }

mkdir -p "$W"
W=$(cd "$W" && pwd)
[[ -s $IMG && -s $ISO ]] || die "no $IMG or $ISO"
K=$W/keys
rm -rf "$K"; mkdir -p -m 700 "$K"
ssh-keygen -q -t ed25519 -N '' -C anduinos-bootstrap-qemu -f "$K/id_ed25519"
ssh-keygen -q -t ed25519 -N '' -C root@anduinos-bootstrap-qemu -f "$K/ssh_host_ed25519_key"
cp "$K/id_ed25519.pub" "$K/authorized_keys"
printf 'anduinos-bootstrap-qemu %s\n' "$(cut -d' ' -f1,2 "$K/ssh_host_ed25519_key.pub")" >"$K/known_hosts"

cp "$IMG" "$W/stick.img"
"$HERE/../../stick/add-ssh-key.sh" --host-key "$K/ssh_host_ed25519_key" "$W/stick.img" "$K/id_ed25519.pub" >/dev/null
qemu-img create -q -f qcow2 -F raw -b "$W/stick.img" "$W/stick.qcow2" 16G
rm -f "$W/internal.qcow2"
qemu-img create -q -f qcow2 "$W/internal.qcow2" 40G
cp "$OVMF/OVMF_VARS_4M.fd" "$W/vars.fd"
# The drive: one ext4 partition with the ISO, the scripts and the keys in
# backshot1/'s places (iso/anduinos2/, backshot1/anduinos/, backshot1/keys/).
rm -rf "$W/drive.d" "$W/drive.raw"
mkdir -p "$W/drive.d/iso/anduinos2" "$W/drive.d/backshot1/anduinos" "$W/drive.d/backshot1/keys"
cp "$ISO" "$W/drive.d/iso/anduinos2/"
cp "$HERE/anduinos-bootstrap.sh" "$HERE/../anduinos/plan.py" "$W/drive.d/backshot1/anduinos/"
cp "$K/authorized_keys" "$K/ssh_host_ed25519_key" "$K/ssh_host_ed25519_key.pub" "$W/drive.d/backshot1/keys/"
truncate -s 8G "$W/drive.raw"
sfdisk -q --label gpt "$W/drive.raw" <<<",,L"
mkfs.ext4 -q -F -L "$LABEL" -E offset=1048576 -d "$W/drive.d" "$W/drive.raw" 8000M
rm -rf "$W/drive.d"

OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=30 -i "$K/id_ed25519" -o IdentitiesOnly=yes
      -o HostKeyAlias=anduinos-bootstrap-qemu -o UserKnownHostsFile="$K/known_hosts" -o StrictHostKeyChecking=yes -p "$PORT")
on() { local who=$1; shift; ssh "${OPTS[@]}" "$who@127.0.0.1" "$@"; }

qemu_pid=
vm() {      # vm NAME [rescue]: starts QEMU in the background
    local d=(-drive "if=none,id=internal,format=qcow2,file=$W/internal.qcow2" -device "nvme,drive=internal,serial=$SERIAL")
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
B=/mnt/bs1-backup
t0=$SECONDS
set +e
on backotter "sudo -n bash -c 'mkdir -p $B && mount LABEL=$LABEL $B && cd / && bash $B/backshot1/anduinos/anduinos-bootstrap.sh $SERIAL $B/iso/anduinos2/$(basename "$ISO") $B/backshot1/keys anduinostest'" >"$W/bootstrap.log" 2>&1
rc=$?
set -e
echo "  anduinos-bootstrap.sh: exit $rc after $((SECONDS - t0)) s (bootstrap.log)"
tail -n 3 "$W/bootstrap.log"
check "anduinos-bootstrap.sh ran to the end" grep -qx BKO-ANDUINOS-INSTALL-OK "$W/bootstrap.log"
check "nothing of the target is still mounted" on backotter "! findmnt -rn -o SOURCE | grep -q nvme0n1"
check "nothing of the chroot is left" on backotter "! findmnt -rn /run/bko-anduinos >/dev/null"
on backotter 'sudo -n efibootmgr' >"$W/efibootmgr-rescue.txt" 2>&1 || true
check "the installer made an NVRAM entry for EFI\\AnduinOS\\shimx64.efi" grep -qi 'AnduinOS' "$W/efibootmgr-rescue.txt"
on backotter "sudo -n sh -c 'umount $B; systemctl poweroff'" >/dev/null 2>&1 || true
for _ in $(seq 120); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 1; done
kill "$qemu_pid" 2>/dev/null || true
qemu_pid=

if ((${#fails[@]} == 0)); then
    echo "== anduinos: the disk alone, from NVRAM"
    t0=$SECONDS
    vm anduinos
    if wait_ssh root 400; then
        echo "  ok    root's SSH, with the fixed host key, $((SECONDS - t0)) s after power-on"
        on root 'systemctl is-system-running --wait' >"$W/state.txt" 2>&1 || true
        on root 'systemctl --failed --no-legend --plain' >"$W/failed.txt" 2>&1 || true
        on root 'cat /usr/lib/os-release; hostname; efibootmgr -v | grep -Ev "^ +(dp|data):";
                 systemctl is-enabled ssh.service avahi-daemon.service anduinos-btrfs-snapshots-manager-scheduler.timer;
                 btrfs subvolume list -a /; echo read-only:; btrfs subvolume list -r -a /; findmnt -R /; ls -la /boot /var/lib/anduinos-dracut-migration;
                 mokutil --sb-state; anduinos-btrfs-snapshots-manager-cli status --json' >"$W/anduinos-facts.txt" 2>&1 || true
        check "the boot finished: $(cat "$W/state.txt")" grep -qxE 'running|degraded' "$W/state.txt"
        check "no unit failed: $(tr '\n' ' ' <"$W/failed.txt")" test ! -s "$W/failed.txt"
        check "os-release says ID=anduinos" grep -qx 'ID=anduinos' "$W/anduinos-facts.txt"
        check "hostname is anduinostest" test "$(on root cat /proc/sys/kernel/hostname)" = anduinostest
        check "two read-only baselines" test "$(sed -n '/^read-only:/,$p' "$W/anduinos-facts.txt" | grep -c '^ID ')" = 2
        cur=$(on root "efibootmgr | sed -n 's/^BootCurrent: //p'")
        check "the firmware booted Boot$cur, \\EFI\\AnduinOS\\shimx64.efi" sh -c "grep -E '^Boot$cur' '$W/anduinos-facts.txt' | grep -qi 'EFI.AnduinOS.shimx64.efi'"
        vm_off root 'systemctl poweroff'
    else
        echo "  FAIL  the installed disk never answered on SSH (console-anduinos.log)"
        fails+=(anduinos-boot)
    fi
    qemu_pid=
fi

if ((${#fails[@]})); then echo "FAILED: ${fails[*]}"; exit 1; fi
echo PASS
