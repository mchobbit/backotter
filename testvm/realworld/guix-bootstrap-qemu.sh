#!/usr/bin/env bash
# Rehearse guix-bootstrap.sh end to end in QEMU: the rescue stick installs
# Guix System onto a blank NVMe disk, then that disk alone boots by its own
# NVRAM entry.  On a KVM host:
#
#   guix-bootstrap-qemu.sh TEST_IMG TARBALL WORK_DIR [PORT]
#
# TEST_IMG is a rescue image from "make-img.sh --test" (copied, never
# written); TARBALL Guix's guix-binary-VERSION.x86_64-linux.tar.xz, its
# signature checked.  Everything is written in WORK_DIR: the stick's copy
# (with a fresh key added by stick/add-ssh-key.sh), a 40 GiB NVMe disk
# (serial BKOGUIXTEST), OVMF's variables, keys, console logs.  Two boots:
#
#   rescue   the stick and the blank disk: guix-bootstrap.sh, as root
#   guix     the disk alone, from the NVRAM entry grub-install made: SSH with
#            the fixed host key, os-release ID=guix, every Shepherd service
#            up, the firmware booted \EFI\Guix\grubx64.efi
#
# Prints PASS and exits 0 if all of that held.

set -euo pipefail
IMG=${1:?usage: guix-bootstrap-qemu.sh TEST_IMG TARBALL WORK_DIR [PORT]}
TARBALL=${2:?}
W=${3:?}
PORT=${4:-2988}
HERE=$(cd "$(dirname "$0")" && pwd)
OVMF=/usr/share/OVMF
SERIAL=BKOGUIXTEST
fails=()
die() { echo "guix-bootstrap-qemu: $*" >&2; exit 1; }
check() { if "${@:2}"; then echo "  ok    $1"; else echo "  FAIL  $1"; fails+=("$1"); fi; }

mkdir -p "$W"
W=$(cd "$W" && pwd)
[[ -s $IMG && -s $TARBALL ]] || die "no $IMG or $TARBALL"
K=$W/keys
rm -rf "$K"; mkdir -p -m 700 "$K"
ssh-keygen -q -t ed25519 -N '' -C guix-bootstrap-qemu -f "$K/id_ed25519"
ssh-keygen -q -t ed25519 -N '' -C root@guix-bootstrap-qemu -f "$K/ssh_host_ed25519_key"
cp "$K/id_ed25519.pub" "$K/authorized_keys"
printf 'guix-bootstrap-qemu %s\n' "$(cut -d' ' -f1,2 "$K/ssh_host_ed25519_key.pub")" >"$K/known_hosts"

cp "$IMG" "$W/stick.img"
"$HERE/../../stick/add-ssh-key.sh" --host-key "$K/ssh_host_ed25519_key" "$W/stick.img" "$K/id_ed25519.pub" >/dev/null
qemu-img create -q -f qcow2 -F raw -b "$W/stick.img" "$W/stick.qcow2" 16G
rm -f "$W/internal.qcow2"
qemu-img create -q -f qcow2 "$W/internal.qcow2" 40G
cp "$OVMF/OVMF_VARS_4M.fd" "$W/vars.fd"

OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=30 -i "$K/id_ed25519" -o IdentitiesOnly=yes
      -o HostKeyAlias=guix-bootstrap-qemu -o UserKnownHostsFile="$K/known_hosts" -o StrictHostKeyChecking=yes -p "$PORT")
SCP_OPTS=(-q -o BatchMode=yes -i "$K/id_ed25519" -o IdentitiesOnly=yes
          -o HostKeyAlias=guix-bootstrap-qemu -o UserKnownHostsFile="$K/known_hosts" -o StrictHostKeyChecking=yes -P "$PORT")
on() { local who=$1; shift; ssh "${OPTS[@]}" "$who@127.0.0.1" "$@"; }
# Nothing listed under Stopped: or Failed: in herd status's output.
herd_clean() { ! sed -n '/^Stopped:/,/^One-shot:/p' "$1" | grep -qE '^ [-!] '; }

qemu_pid=
vm() {      # vm NAME [stick]: starts QEMU in the background
    local d=(-drive "if=none,id=internal,format=qcow2,file=$W/internal.qcow2" -device "nvme,drive=internal,serial=$SERIAL")
    [[ ${2:-} != stick ]] || d+=(-drive "if=none,id=stick,format=qcow2,file=$W/stick.qcow2" -device "usb-storage,bus=usb.0,drive=stick,bootindex=1")
    qemu-system-x86_64 -enable-kvm -cpu host -smp 4 -m 6144 -display none -monitor none -machine q35 \
        -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF/OVMF_CODE_4M.fd" \
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
vm_off() {      # vm_off CMD: CMD powers it off; then waits, and kills only this QEMU
    on root "$1" >/dev/null 2>&1 || true
    for _ in $(seq 120); do kill -0 "$qemu_pid" 2>/dev/null || return 0; sleep 1; done
    kill "$qemu_pid" 2>/dev/null; sleep 3; kill -9 "$qemu_pid" 2>/dev/null || true
}
trap '[[ -z $qemu_pid ]] || kill "$qemu_pid" 2>/dev/null || true' EXIT

echo "== rescue: the stick and a blank $SERIAL"
vm rescue stick
wait_ssh backotter 400 || die "the stick never answered on SSH (console-rescue.log)"
on backotter 'test -f /cdrom/.disk/backotter.json' || die "that isn't the rescue stick"
scp "${SCP_OPTS[@]}" "$TARBALL" "$HERE/guix-bootstrap.sh" "$K/authorized_keys" "$K/ssh_host_ed25519_key" "$K/ssh_host_ed25519_key.pub" backotter@127.0.0.1:/tmp/
on backotter 'mkdir -p /tmp/keys && mv /tmp/authorized_keys /tmp/ssh_host_ed25519_key* /tmp/keys/'
t0=$SECONDS
set +e
on backotter "sudo -n bash /tmp/guix-bootstrap.sh $SERIAL /tmp/$(basename "$TARBALL") /tmp/keys guixtest" >"$W/bootstrap.log" 2>&1
rc=$?
set -e
echo "  guix-bootstrap.sh: exit $rc after $((SECONDS - t0)) s (bootstrap.log)"
tail -n 3 "$W/bootstrap.log"
check "guix-bootstrap.sh ran to the end" grep -qx BKO-GUIX-INSTALL-OK "$W/bootstrap.log"
check "the bootstrap store is gone from the stick's /gnu and /var/guix" on backotter 'test ! -e /gnu && test ! -e /var/guix'
check "nothing of the target is still mounted" on backotter "! findmnt -rn -o SOURCE | grep -q nvme0n1"
on backotter 'sudo -n efibootmgr' >"$W/efibootmgr-rescue.txt" 2>&1 || true
check "grub-install made an NVRAM entry for EFI\\Guix" grep -q 'Guix' "$W/efibootmgr-rescue.txt"
on backotter 'sudo -n systemctl poweroff' >/dev/null 2>&1 || true
for _ in $(seq 120); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 1; done
kill "$qemu_pid" 2>/dev/null || true
qemu_pid=

if ((${#fails[@]} == 0)); then
    echo "== guix: the disk alone, from NVRAM"
    vm guix
    if wait_ssh root 400; then
        echo "  ok    root's SSH, with the fixed host key"
        on root 'cat /etc/os-release; echo; herd status; echo; tr " " "\n" </proc/cmdline; findmnt -R /' >"$W/guix-facts.txt" 2>&1 || true
        check "os-release says ID=guix" grep -qx 'ID=guix' "$W/guix-facts.txt"
        check "hostname is guixtest" test "$(on root cat /proc/sys/kernel/hostname)" = guixtest
        check "no Shepherd service is stopped or failed" herd_clean "$W/guix-facts.txt"
        check "/gnu is the @gnu subvolume" on root 'findmnt -no FSROOT /gnu | grep -qx /@gnu'
        # BootCurrent's number, then that entry's path, from efivarfs: Guix has no efibootmgr until bs1-adopt.
        cur=$(on root 'od -An -tx1 -j4 /sys/firmware/efi/efivars/BootCurrent-8be4df61-93ca-11d2-aa0d-00e098032b8c | tr -d " \n"' || true)
        [[ ${#cur} -eq 4 ]] || cur=XXXX
        n=$(printf %s "${cur:2:2}${cur:0:2}" | tr a-f A-F)
        check "the firmware booted Boot$n, \\EFI\\Guix\\grubx64.efi" sh -c "grep -E '^Boot$n' '$W/efibootmgr-rescue.txt' | grep -qi 'EFI.Guix.grubx64.efi'"
        vm_off halt
    else
        echo "  FAIL  the installed disk never answered on SSH (console-guix.log)"
        fails+=(guix-boot)
    fi
    qemu_pid=
fi

if ((${#fails[@]})); then echo "FAILED: ${fails[*]}"; exit 1; fi
echo PASS
