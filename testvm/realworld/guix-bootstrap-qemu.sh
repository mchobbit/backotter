#!/usr/bin/env bash
# Rehearse guix-bootstrap.sh end to end in QEMU: the rescue stick installs
# Guix System onto a blank NVMe disk, then that disk alone boots by its own
# NVRAM entry.  On a KVM host:
#
#   guix-bootstrap-qemu.sh [--luks] TEST_IMG TARBALL WORK_DIR [PORT]
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
# --luks rehearses guix-bootstrap.sh --luks: prepare, then the cryptsetup
# lines prepare printed, run on the stick with the passphrase from a file
# (a throwaway VM: a fresh random console-passphrase in WORK_DIR, mode 600;
# on the laptop the person types it), then install, with
# BKO_GUIX_KERNEL_ARGUMENTS putting the console on ttyS0 last so that the
# initrd's prompt is on the serial console.  install must refuse first while
# the partition isn't LUKS1, then while cryptroot isn't open.  The guix boot
# runs under testvm/console-boot.py, which types the passphrase at GRUB's
# prompt (OVMF's console, which QEMU's serial carries) and the initrd's; then
# also: both prompts came, / is on /dev/mapper/cryptroot and the swap plain,
# GRUB's image reads /boot/grub from the volume ((cryptouuid/HEX)/@/boot/grub),
# and bs1-luks's own lk_guix_eval reads /etc/config.scm as family G wants it
# (grub-efi, no extra-initrd, cryptroot a luks-device-mapping by UUID that
# the initrd opens).
#
# Prints PASS and exits 0 if all of that held.

set -euo pipefail
LUKS=no
[[ ${1:-} != --luks ]] || { LUKS=yes; shift; }
IMG=${1:?usage: guix-bootstrap-qemu.sh [--luks] TEST_IMG TARBALL WORK_DIR [PORT]}
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
PASS=$W/console-passphrase
if [[ $LUKS == yes ]]; then
    # Letters and digits, no final newline: typed at GRUB's prompt too.
    (umask 077; openssl rand -base64 24 | tr -dc 'A-Za-z0-9' >"$PASS")
    [[ $(wc -c <"$PASS") -ge 20 ]] || die "a short passphrase came out of openssl; run again"
fi

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
    # --luks, the installed disk: console-boot.py types the passphrase at
    # each prompt on the serial console, which it logs.
    local serial=(-serial "file:$W/console-$1.log") wrap=()
    if [[ $LUKS == yes && $1 == guix ]]; then
        serial=(-serial stdio)
        wrap=(python3 "$HERE/../console-boot.py" "$W/console-$1.log" "$PASS" --)
    fi
    "${wrap[@]}" qemu-system-x86_64 -enable-kvm -cpu host -smp 4 -m 6144 -display none -monitor none -machine q35 \
        -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF/OVMF_CODE_4M.fd" \
        -drive "if=pflash,format=raw,unit=1,file=$W/vars.fd" \
        -device qemu-xhci,id=usb -device virtio-rng-pci "${d[@]}" \
        -netdev "user,id=n,hostfwd=tcp:127.0.0.1:$PORT-:22" -device virtio-net-pci,netdev=n \
        "${serial[@]}" </dev/null >"$W/qemu-$1.log" 2>&1 &
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
if [[ $LUKS == yes ]]; then
    install="sudo -n env BKO_GUIX_KERNEL_ARGUMENTS='console=tty0 console=ttyS0,115200n8' bash /tmp/guix-bootstrap.sh --luks install $SERIAL /tmp/$(basename "$TARBALL") /tmp/keys guixtest"
    on backotter "sudo -n bash /tmp/guix-bootstrap.sh --luks prepare $SERIAL" >"$W/prepare.log" 2>&1
    echo "  guix-bootstrap.sh --luks prepare: exit $? (prepare.log)"
    fmt=$(sed -n 's/^BKO-GUIX-LUKS-FORMAT: //p' "$W/prepare.log")
    open=$(sed -n 's/^BKO-GUIX-LUKS-OPEN: //p' "$W/prepare.log")
    check "prepare ran to the end" grep -qx BKO-GUIX-LUKS-PREPARED "$W/prepare.log"
    check "prepare printed a luksFormat --type luks1 line and an open line" \
        test -n "$fmt" -a -n "$open" -a "${fmt#cryptsetup luksFormat --type luks1 }" != "$fmt" -a "${open#cryptsetup open }" != "$open"
    on backotter "$install" >"$W/refuse-1.log" 2>&1
    check "install refuses a partition that isn't LUKS1" grep -q "isn't LUKS1" "$W/refuse-1.log"
    # What the person types, from a file here: cryptsetup reads a passphrase
    # from stdin when it isn't a terminal (and asks no YES then).
    scp "${SCP_OPTS[@]}" "$PASS" backotter@127.0.0.1:/tmp/bko-pass
    on backotter "sudo -n $fmt </tmp/bko-pass" >"$W/luksformat.log" 2>&1
    echo "  luksFormat: exit $? (luksformat.log)"
    on backotter "$install" >"$W/refuse-2.log" 2>&1
    check "install refuses while cryptroot isn't open" grep -q "/dev/mapper/cryptroot isn't open" "$W/refuse-2.log"
    on backotter "sudo -n $open </tmp/bko-pass; rm -f /tmp/bko-pass" >>"$W/luksformat.log" 2>&1
    echo "  open: exit $?"
    on backotter "$install" >"$W/bootstrap.log" 2>&1
else
    on backotter "sudo -n bash /tmp/guix-bootstrap.sh $SERIAL /tmp/$(basename "$TARBALL") /tmp/keys guixtest" >"$W/bootstrap.log" 2>&1
fi
rc=$?
set -e
echo "  guix-bootstrap.sh: exit $rc after $((SECONDS - t0)) s (bootstrap.log)"
tail -n 3 "$W/bootstrap.log"
check "guix-bootstrap.sh ran to the end" grep -qx BKO-GUIX-INSTALL-OK "$W/bootstrap.log"
check "the bootstrap store is gone from the stick's /gnu and /var/guix" on backotter 'test ! -e /gnu && test ! -e /var/guix'
check "nothing of the target is still mounted" on backotter "! findmnt -rn -o SOURCE | grep -q nvme0n1"
if [[ $LUKS == yes ]]; then
    check "cryptroot is closed again" on backotter 'test ! -e /dev/mapper/cryptroot'
    check "GRUB's image reads /boot/grub from the volume" grep -qE "^GRUB's prefix: \(cryptouuid/[0-9a-f]{32}\)/@/boot/grub$" "$W/bootstrap.log"
    check "the volume is LUKS1, aes-xts-plain64, 512-bit key, sha256" sh -c "grep -qE '^Version:\s+1$' '$W/bootstrap.log' && grep -qE '^Cipher mode:\s+xts-plain64$' '$W/bootstrap.log' && grep -qE '^MK bits:\s+512$' '$W/bootstrap.log' && grep -qE '^Hash spec:\s+sha256$' '$W/bootstrap.log'"
fi
on backotter 'sudo -n efibootmgr' >"$W/efibootmgr-rescue.txt" 2>&1 || true
check "grub-install made an NVRAM entry for EFI\\Guix" grep -q 'Guix' "$W/efibootmgr-rescue.txt"
on backotter 'sudo -n systemctl poweroff' >/dev/null 2>&1 || true
for _ in $(seq 120); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 1; done
kill "$qemu_pid" 2>/dev/null || true
qemu_pid=

if ((${#fails[@]} == 0)); then
    echo "== guix: the disk alone, from NVRAM"
    vm guix
    if wait_ssh root $([[ $LUKS == yes ]] && echo 900 || echo 400); then
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
        if [[ $LUKS == yes ]]; then
            check "GRUB and the initrd each asked for the passphrase" test "$(grep -aci 'enter passphrase for' "$W/console-guix.log")" -ge 2
            on root 'lsblk -o NAME,TYPE,FSTYPE,UUID,MOUNTPOINTS; findmnt -no SOURCE /; swapon --show=NAME --noheadings' >"$W/guix-luks.txt" 2>&1 || true
            check "cryptroot is open" grep -Eq 'cryptroot +crypt ' "$W/guix-luks.txt"
            check "/ is @ on /dev/mapper/cryptroot" grep -qx '/dev/mapper/cryptroot\[/@\]' "$W/guix-luks.txt"
            check "the swap is nvme0n1p2, plain" grep -qx '/dev/nvme0n1p2' "$W/guix-luks.txt"
            # bs1-luks's own reading of /etc/config.scm (family G's).
            { echo 'export PATH=/run/current-system/profile/bin:/run/current-system/profile/sbin:$PATH'
              sed -n '/^lk_guix_eval() {/,/^}/p' "$HERE/backshot1/bs1-luks"; echo 'LK_GUIX_CONF=/etc/config.scm; lk_guix_eval'; } |
                on root bash >"$W/guix-eval.txt" 2>&1 || true
            luks_uuid=$(awk '$3 == "crypto_LUKS" { print $4 }' "$W/guix-luks.txt")
            check "bs1-luks reads grub-efi, no extra-initrd, cryptroot by UUID opened in the initrd" sh -c \
                "grep -qx 'bootloader grub-efi' '$W/guix-eval.txt' && grep -qx 'extra-initrd -' '$W/guix-eval.txt' && grep -qx 'md 1 luks $luks_uuid cryptroot -' '$W/guix-eval.txt'"
            sed -i "s/$(cat "$PASS")/<console-passphrase>/g" "$W"/console-*.log "$W"/*.log 2>/dev/null || true
        fi
        vm_off halt
    else
        echo "  FAIL  the installed disk never answered on SSH (console-guix.log)"
        fails+=(guix-boot)
    fi
    qemu_pid=
fi

if ((${#fails[@]})); then echo "FAILED: ${fails[*]}"; exit 1; fi
echo PASS
