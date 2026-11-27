#!/usr/bin/env bash
# Rehearse pika-bootstrap.sh end to end in QEMU: the rescue stick installs
# PikaOS 4 onto a blank NVMe disk with PikaOS's own installer
# (testvm/pika/install.sh, PIKA_METAL=1), from the ISO on a USB drive, then
# that disk alone boots rEFInd by the NVRAM entry refind-install made.  On a
# KVM host whose CPU has AVX2 (x86-64-v3, as PikaOS's builds need: -cpu host):
#
#   pika-bootstrap-qemu.sh [--luks] TEST_IMG ISO WORK_DIR [PORT]
#
# TEST_IMG is a rescue image from "make-img.sh --test" (copied, never
# written); ISO PikaOS-Nest-GNOME-4.0-amd64-v3-26.08.20-4.iso (its SHA-256
# is checked by pika-bootstrap.sh).  Everything is written in WORK_DIR: the
# stick's copy (with a fresh key added by stick/add-ssh-key.sh), a 40 GiB
# NVMe disk (serial BKOPIKATEST), a USB drive (ext4, label BKOPIKADRIVE)
# holding the ISO, the scripts and the keys in backshot1/'s places (as
# mybig512 does), OVMF's variables, keys, console logs.  OVMF without Secure
# Boot (PikaOS wants it off).  8 GiB of RAM, as the Thinkpad has (zram is
# sized from it).  Two boots:
#
#   rescue  the stick, the blank disk and the drive: pika-bootstrap.sh; then
#           the teardown: nothing of the disk or the chroot still mounted, in
#           any process's mount namespace (the first Thinkpad run's chroot
#           mounts stayed in udevd's, logind's... until the --make-private
#           fix), no loop device left, and the drive unmounts
#   pika    the disk alone, from NVRAM: SSH as root with the fixed host key,
#           os-release ID=pika, the boot finished with no failed unit, the
#           firmware booted \EFI\refind\refind_x64.efi (and the ESP has no
#           EFI/BOOT fallback for it to have taken instead), sshd enabled,
#           / XFS, /boot ext4 labelled PikaOS, booster's image
#
# DISK_DIR=DIR (the run's own) puts the NVMe disk's image in DIR instead: a
# cool disk, as cmp-run.sh's "!" does, where a small host's RAM disk can't
# hold the install.
#
# --luks rehearses pika-bootstrap.sh --luks (LUKS2 crypt_root, Btrfs @ and
# @home) and then BackShot1's family P (bs1-luks: booster's key file a FIFO
# BackShot1's preinit fills from the backup drive), as the Thinkpad will
# run it.  The passphrase is a fresh one in keys/disk-passphrase, lower-case
# letters and digits with no final newline (as BackShot1 keeps it), on the
# drive for pika-bootstrap.sh; while it runs, a watcher on the stick looks
# for it in every process's command line and environment.  Then, after the
# rescue's checks (the volume is LUKS2, nothing of it left open, the
# passphrase in no log), four boots of the disk from NVRAM, their command
# lines refind_linux.conf's own (quiet splash: booster asks through
# Plymouth on the screen, so the passphrase is typed on the keyboard
# through QEMU's monitor, omarchy/type-luks-passphrase.py, blind, more than
# once; QMP screendumps of the screen as PNGs in WORK_DIR):
#
#   pika-typed   the disk and a fake mybig512 (USB, GPT, XFS labelled
#                mybig512, made here; bs1.conf's BACKUP_LABEL), the
#                passphrase typed: the checks above, adjusted (/ the @
#                subvolume on /dev/mapper/crypt_root, /home @home), then
#                BackShot1's scripts and keys staged on the drive, bs1-adopt
#                (family P, refind_linux.conf's lines), bs1-adopt again
#                (nothing changed or remade)
#   pika-drive   the disk and the drive, nothing typed: it answers, and
#                bs1-verify --unlock says UNLOCK=drive; the helper's kernel
#                log line
#   pika-nodrive the disk alone: the passphrase typed after the helper's wait,
#                bs1-verify --unlock (from a copy in /run) says it was typed
#   (and the passphrase in no file of the installed system's /etc, /root,
#   /var/log or /boot)
#
# Prints PASS and exits 0 if all of that held.

set -euo pipefail
LUKS=no
[[ ${1:-} != --luks ]] || { LUKS=yes; shift; }
IMG=${1:?usage: pika-bootstrap-qemu.sh [--luks] TEST_IMG ISO WORK_DIR [PORT]}
ISO=${2:?}
W=${3:?}
PORT=${4:-2987}
HERE=$(cd "$(dirname "$0")" && pwd)
OVMF=/usr/share/OVMF
SERIAL=BKOPIKATEST LABEL=BKOPIKADRIVE
# --luks: the fake backup drive, by bs1.conf's BACKUP_LABEL, as the Thinkpad's.
BIG=mybig512
export PATH=$PATH:/usr/sbin:/sbin
fails=()
die() { echo "pika-bootstrap-qemu: $*" >&2; exit 1; }
check() { if "${@:2}"; then echo "  ok    $1"; else echo "  FAIL  $1"; fails+=("$1"); fi; }

mkdir -p "$W"
W=$(cd "$W" && pwd)
[[ -s $IMG && -s $ISO ]] || die "no $IMG or $ISO"
grep -qw avx2 /proc/cpuinfo || die "this host's CPU has no AVX2: PikaOS needs x86-64-v3"
K=$W/keys
rm -rf "$K"; mkdir -p -m 700 "$K"
ssh-keygen -q -t ed25519 -N '' -C pika-bootstrap-qemu -f "$K/id_ed25519"
ssh-keygen -q -t ed25519 -N '' -C root@pika-bootstrap-qemu -f "$K/ssh_host_ed25519_key"
cp "$K/id_ed25519.pub" "$K/authorized_keys"
printf 'pika-bootstrap-qemu %s\n' "$(cut -d' ' -f1,2 "$K/ssh_host_ed25519_key.pub")" >"$K/known_hosts"
PASS=$K/disk-passphrase
if [[ $LUKS == yes ]]; then
    # Lower-case letters and digits, which QEMU's sendkey names as
    # themselves; no final newline, as BackShot1 keeps it.
    (umask 077; openssl rand -base64 64 | tr -dc 'a-z0-9' | head -c 24 >"$PASS")
    [[ $(wc -c <"$PASS") -eq 24 ]] || die "a short passphrase came out of openssl; run again"
fi

cp "$IMG" "$W/stick.img"
"$HERE/../../stick/add-ssh-key.sh" --host-key "$K/ssh_host_ed25519_key" "$W/stick.img" "$K/id_ed25519.pub" >/dev/null
qemu-img create -q -f qcow2 -F raw -b "$W/stick.img" "$W/stick.qcow2" 16G
D=${DISK_DIR:-$W}
mkdir -p "$D"
INTERNAL=$(cd "$D" && pwd)/internal.qcow2
rm -f "$INTERNAL"
qemu-img create -q -f qcow2 "$INTERNAL" 40G
cp "$OVMF/OVMF_VARS_4M.fd" "$W/vars.fd"
# The drive: one ext4 partition with the ISO, the scripts and the keys in
# backshot1/'s places (iso/pikaos4/, backshot1/pika/, backshot1/keys/).
rm -rf "$W/drive.d" "$W/drive.raw"
mkdir -p "$W/drive.d/iso/pikaos4" "$W/drive.d/backshot1/pika" "$W/drive.d/backshot1/keys"
cp "$ISO" "$W/drive.d/iso/pikaos4/"
cp "$HERE/pika-bootstrap.sh" "$HERE/../pika/install.sh" "$W/drive.d/backshot1/pika/"
cp "$K/authorized_keys" "$K/ssh_host_ed25519_key" "$K/ssh_host_ed25519_key.pub" "$W/drive.d/backshot1/keys/"
if [[ $LUKS == yes ]]; then
    cp -p "$PASS" "$W/drive.d/backshot1/keys/"
    # The watcher: any process whose command line or environment holds the
    # passphrase, while pika-bootstrap.sh runs (one grep over them all, five
    # times a second; its own has only the key file's path).
    cat >"$W/drive.d/backshot1/pika/pw-watch.sh" <<'SH'
#!/bin/sh
# pw-watch.sh KEY_FILE HITS STOP: until STOP exists.
while [ ! -e "$3" ]; do
    grep -laF -f "$1" /proc/[0-9]*/cmdline /proc/[0-9]*/environ 2>/dev/null |
        while read -r f; do p=${f%/*}; echo "${p#/proc/} $(cat "$p/comm" 2>/dev/null) ${f##*/}"; done >>"$2"
    sleep 0.2
done
SH
fi
truncate -s 8G "$W/drive.raw"
sfdisk -q --label gpt "$W/drive.raw" <<<",,L"
mkfs.ext4 -q -F -L "$LABEL" -E offset=1048576 -d "$W/drive.d" "$W/drive.raw" 8000M
rm -rf "$W/drive.d"
if [[ $LUKS == yes ]]; then
    # The fake mybig512: GPT, one 2 GiB XFS partition labelled mybig512 at
    # 1 MiB (made apart and copied in: mkfs.xfs has no offset), empty; the
    # first pika boot stages BackShot1 on it.
    rm -f "$W/$BIG.raw" "$W/$BIG.part"
    truncate -s 2G "$W/$BIG.part"
    mkfs.xfs -q -L "$BIG" "$W/$BIG.part"
    truncate -s 2100M "$W/$BIG.raw"
    sfdisk -q --label gpt "$W/$BIG.raw" <<<"start=2048,size=4194304,type=L"
    dd if="$W/$BIG.part" of="$W/$BIG.raw" bs=1M seek=1 conv=notrunc,sparse status=none
    rm -f "$W/$BIG.part"
fi

OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=30 -i "$K/id_ed25519" -o IdentitiesOnly=yes
      -o HostKeyAlias=pika-bootstrap-qemu -o UserKnownHostsFile="$K/known_hosts" -o StrictHostKeyChecking=yes -p "$PORT")
SCP_OPTS=(-q -o BatchMode=yes -i "$K/id_ed25519" -o IdentitiesOnly=yes
          -o HostKeyAlias=pika-bootstrap-qemu -o UserKnownHostsFile="$K/known_hosts" -o StrictHostKeyChecking=yes -P "$PORT")
on() { local who=$1; shift; ssh "${OPTS[@]}" "$who@127.0.0.1" "$@"; }

qemu_pid=
vm() {      # vm NAME [rescue|drive]: starts QEMU in the background
    local d=(-drive "if=none,id=internal,format=qcow2,file=$INTERNAL" -device "nvme,drive=internal,serial=$SERIAL") mon=(-monitor none)
    case ${2:-} in
    rescue) d+=(-drive "if=none,id=stick,format=qcow2,file=$W/stick.qcow2" -device "usb-storage,bus=usb.0,drive=stick,bootindex=1"
                -drive "if=none,id=drive,format=raw,file=$W/drive.raw" -device "usb-storage,bus=usb.0,drive=drive,serial=$LABEL") ;;
    drive) d+=(-drive "if=none,id=big,format=raw,file=$W/$BIG.raw" -device "usb-storage,bus=usb.0,drive=big,serial=BKOMYBIG512") ;;
    esac
    # --luks, the installed disk: the monitor, for the keyboard
    # (type-luks-passphrase.py), and QMP, for screendumps.
    if [[ $LUKS == yes && ${2:-} != rescue ]]; then
        rm -f "$W/monitor-$1.sock" "$W/qmp-$1.sock"
        mon=(-monitor "unix:$W/monitor-$1.sock,server,nowait" -qmp "unix:$W/qmp-$1.sock,server,nowait")
    fi
    qemu-system-x86_64 -enable-kvm -cpu host -smp 4 -m 8192 -display none "${mon[@]}" -machine q35 \
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
vm_off() {      # vm_off USER CMD: CMD powers it off; then waits, and kills only this QEMU
    on "$1" "$2" >/dev/null 2>&1 || true
    for _ in $(seq 120); do kill -0 "$qemu_pid" 2>/dev/null || return 0; sleep 1; done
    kill "$qemu_pid" 2>/dev/null; sleep 3; kill -9 "$qemu_pid" 2>/dev/null || true
}
# typer NAME DELAY...: the passphrase typed on the keyboard after each DELAY
# s (blind: Plymouth asks on the screen); typer_stop ends it.
typer_pid=
typer() {
    python3 "$HERE/../omarchy/type-luks-passphrase.py" "$W/monitor-$1.sock" "$PASS" "${@:2}" >"$W/typer-$1.log" 2>&1 &
    typer_pid=$!
}
typer_stop() { [[ -z $typer_pid ]] || kill "$typer_pid" 2>/dev/null || true; typer_pid=; }
# shot NAME TAG: the screen as WORK_DIR/screen-NAME-TAG.png, through QMP;
# shots NAME SECONDS...: one after each (in the background).
shot() {
    python3 - "$W/qmp-$1.sock" "$W/screen-$1-$2.png" <<'PY' >/dev/null 2>&1 || true
import json, socket, sys
s = socket.socket(socket.AF_UNIX)
s.settimeout(20)
s.connect(sys.argv[1])
f = s.makefile("rw")
def cmd(c, **a):
    f.write(json.dumps({"execute": c, **({"arguments": a} if a else {})}) + "\n")
    f.flush()
    while True:
        r = json.loads(f.readline())
        if "return" in r or "error" in r:
            return r
f.readline()
cmd("qmp_capabilities")
r = cmd("screendump", filename=sys.argv[2], format="png")
sys.exit("error" in r)
PY
}
shots() { local n=$1 t0=$SECONDS t; shift; ( for t in "$@"; do sleep $((t0 + t - SECONDS > 0 ? t0 + t - SECONDS : 0)); shot "$n" "${t}s"; done ) & }
trap 'typer_stop; [[ -z $qemu_pid ]] || kill "$qemu_pid" 2>/dev/null || true' EXIT

echo "== rescue: the stick, a blank $SERIAL and the drive"
vm rescue rescue
wait_ssh backotter 400 || die "the stick never answered on SSH (console-rescue.log)"
on backotter 'test -f /cdrom/.disk/backotter.json' || die "that isn't the rescue stick"
B=/mnt/bs1-backup P=/mnt/bs1-backup/backshot1/pika
t0=$SECONDS
set +e
if [[ $LUKS == yes ]]; then
    on backotter "sudo -n bash -c 'mkdir -p $B && mount LABEL=$LABEL $B && rm -f /run/bko-pw-hits /run/bko-pw-stop && setsid -f sh $P/pw-watch.sh $B/backshot1/keys/disk-passphrase /run/bko-pw-hits /run/bko-pw-stop </dev/null >/dev/null 2>&1 && cd / && bash $P/pika-bootstrap.sh --luks $SERIAL $B/iso/pikaos4/$(basename "$ISO") $P/install.sh $B/backshot1/keys pikatest'" >"$W/bootstrap.log" 2>&1
    rc=$?
    on backotter 'sudo -n touch /run/bko-pw-stop; sleep 2' || true
else
    on backotter "sudo -n bash -c 'mkdir -p $B && mount LABEL=$LABEL $B && cd / && bash $P/pika-bootstrap.sh $SERIAL $B/iso/pikaos4/$(basename "$ISO") $P/install.sh $B/backshot1/keys pikatest'" >"$W/bootstrap.log" 2>&1
    rc=$?
fi
set -e
echo "  pika-bootstrap.sh: exit $rc after $((SECONDS - t0)) s (bootstrap.log)"
grep -E '^install.sh took' "$W/bootstrap.log" || true
tail -n 3 "$W/bootstrap.log"
check "pika-bootstrap.sh ran to the end" grep -qx BKO-PIKA-INSTALL-OK "$W/bootstrap.log"
check "nothing of the target is still mounted" on backotter "! findmnt -rn -o SOURCE | grep -q nvme0n1"
check "nothing of the chroot is left" on backotter "! findmnt -rn /run/bko-pika >/dev/null && test ! -e /run/bko-pika"
check "no process's mount namespace still holds the chroot" on backotter "sudo -n sh -c '! grep -qs /run/bko-pika /proc/[0-9]*/mountinfo'"
check "no loop device is left on the ISO or its squashfs" on backotter "sudo -n sh -c 'test -z \"\$(losetup -nO BACK-FILE | grep -i pika)\"'"
if [[ $LUKS == yes ]]; then
    check "the root volume is LUKS2" grep -qE '^Version:[[:space:]]+2$' "$W/bootstrap.log"
    check "crypttab is crypt_root UUID=... none luks,discard" grep -qE '^crypttab: crypt_root[[:space:]]+UUID=[0-9a-f-]+[[:space:]]+none[[:space:]]+luks,discard$' "$W/bootstrap.log"
    check "nothing of the volume is left open (bko-pika-root, crypt_root)" on backotter '! ls /dev/mapper | grep -qE "^(bko-pika-root|crypt_root)$"'
    on backotter 'cat /run/bko-pw-hits 2>/dev/null' >"$W/pw-hits.txt" || true
    check "no process had the passphrase in its command line or environment: $(tr '\n' ' ' <"$W/pw-hits.txt")" test ! -s "$W/pw-hits.txt"
    check "the watcher ran, and stopped" on backotter 'test -e /run/bko-pw-stop && ! pgrep -f "[p]w-watch.sh" >/dev/null'
    unlogged() { ! grep -qaF -f "$PASS" "$W/bootstrap.log" &&
                 on backotter "sudo -n sh -c '! grep -qaF -f $B/backshot1/keys/disk-passphrase /var/log/bko-pika-install.log'"; }
    check "the passphrase is in neither install log (here, the stick's /var/log)" unlogged
fi
on backotter 'sudo -n efibootmgr' >"$W/efibootmgr-rescue.txt" 2>&1 || true
check "refind-install made an NVRAM entry for rEFInd" grep -qi 'rEFInd' "$W/efibootmgr-rescue.txt"
check "the drive unmounts (the ISO isn't busy)" on backotter "sudo -n umount $B"
on backotter "sudo -n systemctl poweroff" >/dev/null 2>&1 || true
for _ in $(seq 120); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 1; done
kill "$qemu_pid" 2>/dev/null || true
qemu_pid=

# booted NAME: the checks of every boot of the installed disk (facts in
# WORK_DIR/NAME-facts.txt).
booted() {
    local n=$1 cur
    on root 'systemctl is-system-running --wait' >"$W/$n-state.txt" 2>&1 || true
    on root 'systemctl --failed --no-legend --plain' >"$W/$n-failed.txt" 2>&1 || true
    # geoclue's start sometimes times out in this VM (90 s, then systemd
    # gives up: --luks's typed first boot, then its drive boot, Cinqcent
    # 2026-11-27); that one, and only for a timeout, is noted, not failed.
    if grep -q '^geoclue\.service ' "$W/$n-failed.txt" && [[ $(on root 'systemctl show -P Result geoclue.service') == timeout ]]; then
        echo "  note  geoclue.service timed out starting (not counted)"
        sed -i '/^geoclue\.service /d' "$W/$n-failed.txt"
    fi
    on root 'cat /etc/os-release; hostname; efibootmgr -v | grep -Ev "^ +(dp|data):"; systemctl is-enabled ssh.service;
             findmnt -R /; lsblk -o NAME,TYPE,SIZE,FSTYPE,LABEL,PARTLABEL; ls -la /boot /boot/efi/EFI; cat /boot/refind_linux.conf; cat /proc/cmdline' >"$W/$n-facts.txt" 2>&1 || true
    check "the boot finished: $(cat "$W/$n-state.txt")" grep -qxE 'running|degraded' "$W/$n-state.txt"
    check "no unit failed: $(tr '\n' ' ' <"$W/$n-failed.txt")" test ! -s "$W/$n-failed.txt"
    check "os-release says ID=pika" grep -qx 'ID=pika' "$W/$n-facts.txt"
    check "hostname is pikatest" test "$(on root cat /proc/sys/kernel/hostname)" = pikatest
    check "sshd is enabled" on root 'systemctl is-enabled -q ssh.service'
    if [[ $LUKS == yes ]]; then
        check "/ is Btrfs @ on /dev/mapper/crypt_root, /home @home, /boot ext4 labelled PikaOS, /boot/efi vfat" \
            on root 'test "$(findmnt -no FSTYPE /)" = btrfs && test "$(lsblk -no NAME "$(findmnt -nvo SOURCE /)")" = crypt_root &&
                     test "$(findmnt -no FSROOT /)" = /@ && test "$(findmnt -nvo SOURCE /home)" = "$(findmnt -nvo SOURCE /)" &&
                     test "$(findmnt -no FSROOT /home)" = /@home && test "$(findmnt -no FSTYPE /boot)" = ext4 &&
                     test "$(lsblk -no LABEL "$(findmnt -no SOURCE /boot)")" = PikaOS && test "$(findmnt -no FSTYPE /boot/efi)" = vfat'
        check "crypt_root is open on nvme0n1p3's LUKS2" on root 'cryptsetup status crypt_root | grep -qE "^ *device: +/dev/nvme0n1p3$" &&
                     cryptsetup luksDump /dev/nvme0n1p3 | grep -qE "^Version:[[:space:]]+2$"'
    else
        check "/ is XFS, /boot ext4 labelled PikaOS, /boot/efi vfat" \
            on root 'test "$(findmnt -no FSTYPE /)" = xfs && test "$(findmnt -no FSTYPE /boot)" = ext4 &&
                     test "$(lsblk -no LABEL "$(findmnt -no SOURCE /boot)")" = PikaOS && test "$(findmnt -no FSTYPE /boot/efi)" = vfat'
    fi
    check "booster made /boot/booster.img-$(on root uname -r)" on root 'test -s /boot/booster.img-$(uname -r)'
    check "the ESP has no EFI/BOOT fallback loader (PIKA_METAL)" on root '! ls /boot/efi/EFI | grep -qix boot'
    cur=$(on root "efibootmgr | sed -n 's/^BootCurrent: //p'")
    check "the firmware booted Boot$cur, \\EFI\\refind\\refind_x64.efi" sh -c "grep -E '^Boot$cur' '$W/$n-facts.txt' | grep -qi 'EFI.refind.refind_x64.efi'"
}

if ((${#fails[@]} == 0)) && [[ $LUKS == no ]]; then
    echo "== pika: the disk alone, from NVRAM"
    t0=$SECONDS
    vm pika
    if wait_ssh root 400; then
        echo "  ok    root's SSH, with the fixed host key, $((SECONDS - t0)) s after power-on"
        booted pika
        vm_off root 'systemctl poweroff'
    else
        echo "  FAIL  the installed disk never answered on SSH (console-pika.log)"
        fails+=(pika-boot)
    fi
    qemu_pid=
fi

# --luks: BackShot1's family P, as on the Thinkpad.
BS1=/mnt/bs1-backup/backshot1
MOUNT_BIG="mkdir -p /mnt/bs1-backup; for i in \$(seq 15); do mount LABEL=$BIG /mnt/bs1-backup 2>/dev/null && break; sleep 2; done; mountpoint -q /mnt/bs1-backup"
LUKS_FILES='sha256sum /boot/refind_linux.conf /etc/booster.yaml /boot/booster.img-* /etc/backshot1/luks-unlock /usr/local/lib/backshot1/* 2>&1'
if ((${#fails[@]} == 0)) && [[ $LUKS == yes ]]; then
    echo "== pika-typed: the disk and $BIG from NVRAM, the passphrase typed"
    t0=$SECONDS
    vm pika-typed drive
    typer pika-typed 40 70 100 130 160 200 240
    shots pika-typed 20 35 60 90
    if wait_ssh root 400; then
        typer_stop
        echo "  ok    root's SSH, with the fixed host key, $((SECONDS - t0)) s after power-on"
        booted pika-typed
        # BackShot1 staged on the drive, as `backshot1 stage` does on mybig512.
        on root "$MOUNT_BIG && mkdir -p $BS1/bin $BS1/runs && mkdir -m 700 $BS1/keys" >/dev/null
        printf 'SOURCE_HOST=pikatest\nINTERNAL_SERIAL=%s\nBACKUP_LABEL=%s\n' "$SERIAL" "$BIG" >"$W/bs1.conf"
        names=()
        for n in common backup restore verify manifest ostree abroot adopt luks unlock-key list efi; do names+=("$HERE/backshot1/bs1-$n"); done
        scp "${SCP_OPTS[@]}" "${names[@]}" "$HERE/../ostree_paths.py" "$W/bs1.conf" root@127.0.0.1:$BS1/
        scp "${SCP_OPTS[@]}" "$K/authorized_keys" "$K/ssh_host_ed25519_key" "$K/ssh_host_ed25519_key.pub" "$PASS" root@127.0.0.1:$BS1/keys/
        on root "chmod 755 $BS1/bs1-*; chmod 600 $BS1/keys/disk-passphrase"
        on root "$LUKS_FILES" >"$W/luks-files-before.txt" || true
        set +e
        on root "sh $BS1/bs1-adopt" >"$W/adopt.log" 2>&1
        rc=$?
        set -e
        echo "  bs1-adopt: exit $rc (adopt.log)"
        grep -E 'family|booster' "$W/adopt.log" | tail -n 8 || true
        check "bs1-adopt passes" test "$rc" = 0
        on root '. /etc/backshot1/luks-unlock && echo "family=$family generator=$generator volumes=$volumes label=$label path=$path fstype=$fstype"' >"$W/luks-record.txt" 2>&1 || true
        cat "$W/luks-record.txt"
        uuid=$(on root 'cryptsetup luksUUID /dev/nvme0n1p3' || true)
        check "the record: family P, booster, crypt_root:$uuid:P, the key on $BIG's XFS" \
            grep -qx "family=P generator=booster volumes=crypt_root:$uuid:P label=$BIG path=/backshot1/keys/disk-passphrase fstype=xfs" "$W/luks-record.txt"
        on root 'cat /boot/refind_linux.conf' >"$W/refind_linux.conf.adopted" 2>&1 || true
        lines=$(grep -c '^"' "$W/refind_linux.conf.adopted" || true)
        check "each of refind_linux.conf's $lines lines has rdinit= the preinit and crypt_root's rd.luks.key=" test "$lines" -gt 0 -a \
            "$(grep -c " rdinit=/usr/local/lib/backshot1/booster-preinit rd.luks.key=$uuid=/backshot1-key.$uuid" "$W/refind_linux.conf.adopted")" = "$lines"
        check "booster.log=warning on each quiet line" test "$(grep -c ' quiet ' "$W/refind_linux.conf.adopted")" = \
            "$(grep ' quiet ' "$W/refind_linux.conf.adopted" | grep -c ' booster.log=warning')"
        on root "$LUKS_FILES" >"$W/luks-files.txt" || true
        set +e
        on root "sh $BS1/bs1-adopt" >"$W/adopt-2.log" 2>&1
        rc=$?
        set -e
        on root "$LUKS_FILES" >"$W/luks-files-2.txt" || true
        check "a second bs1-adopt changes nothing and remakes nothing" sh -c "test $rc = 0 && grep -q \"unchanged; the initramfs isn't remade\" '$W/adopt-2.log' && cmp -s '$W/luks-files.txt' '$W/luks-files-2.txt'"
        on root 'umount /mnt/bs1-backup' || true
        vm_off root 'systemctl poweroff'
    else
        typer_stop
        echo "  FAIL  the installed disk never answered on SSH with the passphrase typed (screen-pika-typed-*.png)"
        fails+=(pika-typed-boot)
    fi
    qemu_pid=
fi

if ((${#fails[@]} == 0)) && [[ $LUKS == yes ]]; then
    echo "== pika-drive: the disk and $BIG, nothing typed"
    t0=$SECONDS
    vm pika-drive drive
    shots pika-drive 20 35 50 80 120
    if wait_ssh root 300; then
        echo "  ok    root's SSH, $((SECONDS - t0)) s after power-on, nothing typed"
        booted pika-drive
        on root "$MOUNT_BIG && bash $BS1/bs1-verify --unlock" >"$W/unlock-drive.txt" 2>&1 || true
        cat "$W/unlock-drive.txt"
        check "bs1-verify --unlock: UNLOCK=drive, crypt_root drive" sh -c "grep -qx UNLOCK=drive '$W/unlock-drive.txt' && grep -qx '  crypt_root drive' '$W/unlock-drive.txt'"
        on root 'cat /run/backshot1-unlock; journalctl -k -b -o cat --no-pager | grep -iE "backshot1|booster" | head -n 40' >"$W/drive-klog.txt" 2>&1 || true
        # (journald takes "BackShot1:" for the line's identifier: -o cat leaves it out.)
        check "the kernel's log has the helper's \"handed to booster\"" grep -qF "crypt_root: the backup drive's key, handed to booster" "$W/drive-klog.txt"
        on root 'umount /mnt/bs1-backup' || true
        vm_off root 'systemctl poweroff'
    else
        echo "  FAIL  the disk never answered on SSH with the drive and nothing typed (screen-pika-drive-*.png)"
        fails+=(pika-drive-boot)
    fi
    qemu_pid=
fi

if ((${#fails[@]} == 0)) && [[ $LUKS == yes ]]; then
    echo "== pika-nodrive: the disk alone, the passphrase typed after the helper's wait"
    t0=$SECONDS
    vm pika-nodrive
    typer pika-nodrive 75 105 135 165 200 240 280
    shots pika-nodrive 20 45 70 100
    if wait_ssh root 500; then
        typer_stop
        echo "  ok    root's SSH, $((SECONDS - t0)) s after power-on, the passphrase typed"
        booted pika-nodrive
        on root 'mkdir -p -m 700 /run/bs1-check'
        scp "${SCP_OPTS[@]}" "$HERE/backshot1/bs1-common" "$HERE/backshot1/bs1-verify" "$W/bs1.conf" root@127.0.0.1:/run/bs1-check/
        on root 'bash /run/bs1-check/bs1-verify --unlock' >"$W/unlock-nodrive.txt" 2>&1 || true
        cat "$W/unlock-nodrive.txt"
        check "bs1-verify --unlock: typed, not the drive (UNLOCK=attended, crypt_root typed)" sh -c "grep -qx UNLOCK=attended '$W/unlock-nodrive.txt' && grep -qx '  crypt_root typed' '$W/unlock-nodrive.txt'"
        on root 'cat /run/backshot1-unlock' >"$W/nodrive-mark.txt" 2>&1 || true
        check "the helper marked crypt_root typed: $(tr '\n' ' ' <"$W/nodrive-mark.txt")" grep -qx 'crypt_root typed' "$W/nodrive-mark.txt"
        # The passphrase in no file of the installed system's (but what the
        # typing may have left in GDM's way, under /home and /var/lib).
        scp "${SCP_OPTS[@]}" "$PASS" root@127.0.0.1:/run/bs1-check/pw
        on root 'grep -rlaF -f /run/bs1-check/pw /etc /root /var/log /boot 2>/dev/null; rm -f /run/bs1-check/pw' >"$W/pw-files.txt" 2>&1 || true
        check "the passphrase is in no file of /etc, /root, /var/log or /boot: $(tr '\n' ' ' <"$W/pw-files.txt")" test ! -s "$W/pw-files.txt"
        vm_off root 'systemctl poweroff'
    else
        typer_stop
        echo "  FAIL  the disk alone never answered on SSH with the passphrase typed (screen-pika-nodrive-*.png)"
        fails+=(pika-nodrive-boot)
    fi
    qemu_pid=
fi

if ((${#fails[@]})); then echo "FAILED: ${fails[*]}"; exit 1; fi
echo PASS
