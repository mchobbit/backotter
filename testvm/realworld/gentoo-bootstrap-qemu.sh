#!/usr/bin/env bash
# Rehearse gentoo-bootstrap.sh end to end in QEMU, as the Thinkpad (the
# Lenovo A100) will run it: the rescue stick installs Gentoo (systemd, LVM
# on LUKS2, dracut, GRUB) onto a blank NVMe disk from a fake mybig512, then
# BackShot1's family S (bs1-luks) makes it unlock from that drive.  On a KVM
# host:
#
#   gentoo-bootstrap-qemu.sh TEST_IMG INPUTS_DIR WORK_DIR [PORT]
#
# TEST_IMG is a rescue image from "make-img.sh --test" (copied, never
# written); INPUTS_DIR holds gentoo-bootstrap.sh's pinned inputs (the
# systemd stage3, its .asc and gentoo-release-key.gpg: on Chopin
# /bkoscratch/rebuild-inputs/gentoo-systemd).  Everything is written in
# WORK_DIR: the stick's copy (a fresh key added by stick/add-ssh-key.sh), a
# 100 GiB sparse NVMe disk (serial BKOGENTOOTEST: the script's 64 GiB root
# fits), a fake mybig512 (USB, GPT, one XFS partition labelled mybig512, as
# the Thinkpad's), OVMF's variables (no Secure Boot), keys, console logs,
# QMP screendumps (screen-*.png).  8 GiB of RAM and 4 CPUs.  The passphrase
# is a fresh one in keys/disk-passphrase (lower-case letters and digits, no
# final newline, as BackShot1 keeps it, and as QEMU's sendkey can type).
# Then:
#
#   rescue        the stick, the blank disk and the drive.  The drive is
#                 staged over SSH as mybig512 is (backshot1/gentoo/: the
#                 script, bootstrap/gentoo.sh, the inputs; backshot1/keys/),
#                 then the stick runs, as root, the Thinkpad's command line
#                 with the rehearsal's serial and host name:
#                   gentoo-bootstrap.sh --detach BKOGENTOOTEST gentootest
#                 (BKO_GENTOO_CMDLINE puts the kernel's log on the serial
#                 port, the prompt still on the screen), waited for by its
#                 exit file.  Meanwhile a watcher on the stick looks for the
#                 passphrase in every process's command line and
#                 environment.  Then the teardown: nothing of the disk
#                 mounted or open (cryptroot, vg0), nothing of the chroot in
#                 any process's mount namespace, the volume LUKS2, crypttab
#                 "cryptroot UUID=... none luks,discard", the passphrase in
#                 neither log, efibootmgr's gentoo entry, the drive unmounts
#   gentoo-typed  the disk and the drive, from NVRAM, the passphrase typed
#                 blind on the keyboard through QEMU's monitor
#                 (omarchy/type-luks-passphrase.py, more than once): the
#                 boot's checks (below); BackShot1's scripts staged on the
#                 drive; bs1-adopt (family S, dracut, crypttab's key field
#                 the drive's), bs1-adopt again (nothing changed or remade)
#   gentoo-drive  the disk and the drive, nothing typed: it answers, and
#                 bs1-verify --unlock says UNLOCK=drive
#   gentoo-nodrive the disk alone: the passphrase typed after the 30 s wait,
#                 bs1-verify --unlock (from a copy in /run) says it was typed;
#                 the passphrase in no file of /etc, /root, /var/log or /boot
#
# Every boot of the installed disk: SSH as root with the fixed host key,
# os-release ID=gentoo, the host name, the boot finished with no failed
# unit, sshd and avahi-daemon running, / ext4 on vg0/root on cryptroot on
# nvme0n1p2 (LUKS2), /boot the vfat ESP, rd.luks.uuid on the command line,
# no /etc/cryptsetup-keys.d, the firmware booted \EFI\gentoo\grubx64.efi.
#
# DISK_DIR=DIR puts the NVMe disk's image in DIR instead of WORK_DIR (a cool
# disk, where a RAM disk can't hold the install: ~10 GiB written).
#
# Prints PASS and exits 0 if all of that held.

# shellcheck disable=SC2016,SC2029  # strings expanded on the guest, by design
set -euo pipefail
IMG=${1:?usage: gentoo-bootstrap-qemu.sh TEST_IMG INPUTS_DIR WORK_DIR [PORT]}
INPUTS=${2:?}
W=${3:?}
PORT=${4:-2991}
HERE=$(cd "$(dirname "$0")" && pwd)
OVMF=/usr/share/OVMF
SERIAL=BKOGENTOOTEST HOST=gentootest
BIG=mybig512                    # bs1.conf's BACKUP_LABEL, as the Thinkpad's
B=/mnt/bs1-backup               # where the stick has it, as backshot1's $DRIVE
G=$B/backshot1/gentoo
STAGE3=$(sed -n 's/^STAGE3=\(stage3-.*\.tar\.xz\)$/\1/p' "$HERE/gentoo-bootstrap.sh")
[[ $STAGE3 == stage3-amd64-systemd-*.tar.xz ]] || die "no STAGE3 in gentoo-bootstrap.sh"
INSTALL_TIMEOUT=${INSTALL_TIMEOUT:-5400}
export PATH=$PATH:/usr/sbin:/sbin
fails=()
die() { echo "gentoo-bootstrap-qemu: $*" >&2; exit 1; }
check() { if "${@:2}"; then echo "  ok    $1"; else echo "  FAIL  $1"; fails+=("$1"); fi; }

mkdir -p "$W"
W=$(cd "$W" && pwd)
[[ -s $IMG ]] || die "no $IMG"
for f in "$STAGE3" "$STAGE3.asc" gentoo-release-key.gpg; do [[ -s $INPUTS/$f ]] || die "no $INPUTS/$f"; done
K=$W/keys
rm -rf "$K"; mkdir -m 700 "$K"
ssh-keygen -q -t ed25519 -N '' -C gentoo-bootstrap-qemu -f "$K/id_ed25519"
ssh-keygen -q -t ed25519 -N '' -C root@gentoo-bootstrap-qemu -f "$K/ssh_host_ed25519_key"
cp "$K/id_ed25519.pub" "$K/authorized_keys"
printf 'gentoo-bootstrap-qemu %s\n' "$(cut -d' ' -f1,2 "$K/ssh_host_ed25519_key.pub")" >"$K/known_hosts"
PASS=$K/disk-passphrase
# Lower-case letters and digits, which QEMU's sendkey names as themselves;
# no final newline, as BackShot1 keeps it.
(umask 077; openssl rand -base64 64 | tr -dc 'a-z0-9' | head -c 24 >"$PASS")
[[ $(wc -c <"$PASS") -eq 24 ]] || die "a short passphrase came out of openssl; run again"

cp "$IMG" "$W/stick.img"
"$HERE/../../stick/add-ssh-key.sh" --host-key "$K/ssh_host_ed25519_key" "$W/stick.img" "$K/id_ed25519.pub" >/dev/null
qemu-img create -q -f qcow2 -F raw -b "$W/stick.img" "$W/stick.qcow2" 16G
D=${DISK_DIR:-$W}
mkdir -p "$D"
INTERNAL=$(cd "$D" && pwd)/internal.qcow2
rm -f "$INTERNAL"
qemu-img create -q -f qcow2 "$INTERNAL" 100G
cp "$OVMF/OVMF_VARS_4M.fd" "$W/vars.fd"
# The fake mybig512: GPT, one 2 GiB XFS partition labelled mybig512 at 1 MiB
# (made apart and copied in: mkfs.xfs has no offset), empty until staged.
rm -f "$W/$BIG.raw" "$W/$BIG.part"
truncate -s 2G "$W/$BIG.part"
mkfs.xfs -q -L "$BIG" "$W/$BIG.part"
truncate -s 2100M "$W/$BIG.raw"
sfdisk -q --label gpt "$W/$BIG.raw" <<<"start=2048,size=4194304,type=L"
dd if="$W/$BIG.part" of="$W/$BIG.raw" bs=1M seek=1 conv=notrunc,sparse status=none
rm -f "$W/$BIG.part"

OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=30 -i "$K/id_ed25519" -o IdentitiesOnly=yes
      -o HostKeyAlias=gentoo-bootstrap-qemu -o UserKnownHostsFile="$K/known_hosts" -o StrictHostKeyChecking=yes -p "$PORT")
SCP_OPTS=(-q -o BatchMode=yes -i "$K/id_ed25519" -o IdentitiesOnly=yes
          -o HostKeyAlias=gentoo-bootstrap-qemu -o UserKnownHostsFile="$K/known_hosts" -o StrictHostKeyChecking=yes -P "$PORT")
on() { local who=$1; shift; ssh "${OPTS[@]}" "$who@127.0.0.1" "$@"; }
# put FILE PATH MODE: FILE onto the stick at PATH (root's), through ssh's stdin.
put() { on backotter "sudo -n sh -c 'umask 077 && mkdir -p \"\$(dirname $2)\" && cat >$2 && chmod $3 $2'" <"$1"; }

qemu_pid=
vm() {      # vm NAME [rescue|drive]: starts QEMU in the background
    local d=(-drive "if=none,id=internal,format=qcow2,file=$INTERNAL,discard=unmap" -device "nvme,drive=internal,serial=$SERIAL")
    case ${2:-} in
    rescue) d+=(-drive "if=none,id=stick,format=qcow2,file=$W/stick.qcow2" -device "usb-storage,bus=usb.0,drive=stick,bootindex=1"
                -drive "if=none,id=big,format=raw,file=$W/$BIG.raw" -device "usb-storage,bus=usb.0,drive=big,serial=BKOMYBIG512") ;;
    drive) d+=(-drive "if=none,id=big,format=raw,file=$W/$BIG.raw" -device "usb-storage,bus=usb.0,drive=big,serial=BKOMYBIG512") ;;
    esac
    # The monitor, for the keyboard (type-luks-passphrase.py), and QMP, for
    # screendumps.
    rm -f "$W/monitor-$1.sock" "$W/qmp-$1.sock"
    qemu-system-x86_64 -enable-kvm -cpu host -smp 4 -m 8192 -display none -machine q35 \
        -monitor "unix:$W/monitor-$1.sock,server,nowait" -qmp "unix:$W/qmp-$1.sock,server,nowait" \
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
        timeout 30 ssh "${OPTS[@]}" "$1@127.0.0.1" true 2>/dev/null && return 0
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
# s (blind: systemd asks on the screen); typer_stop ends it.
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

echo "== rescue: the stick, a blank $SERIAL and $BIG"
vm rescue rescue
wait_ssh backotter 400 || die "the stick never answered on SSH (console-rescue.log)"
on backotter 'test -f /cdrom/.disk/backotter.json' || die "that isn't the rescue stick"
on backotter "sudo -n bash -c 'mkdir -p $B && mount LABEL=$BIG $B && mkdir -p $G && install -d -m 700 $B/backshot1/keys'"
# The drive staged as mybig512 is (backshot1/README.md, "Gentoo on the A100").
put "$HERE/gentoo-bootstrap.sh" "$G/gentoo-bootstrap.sh" 755
put "$HERE/../bootstrap/gentoo.sh" "$G/gentoo.sh" 644
for f in "$STAGE3" "$STAGE3.asc" gentoo-release-key.gpg; do put "$INPUTS/$f" "$G/$f" 644; done
for f in authorized_keys ssh_host_ed25519_key ssh_host_ed25519_key.pub disk-passphrase; do put "$K/$f" "$B/backshot1/keys/$f" 600; done
on backotter "sudo -n sh -c 'cd $G && sha256sum *; sync'" >"$W/staged.txt"
# The watcher: any process whose command line or environment holds the
# passphrase, while gentoo-bootstrap.sh runs (one grep over them all, five
# times a second; its own has only the key file's path).
put /dev/stdin /run/bko-pw-watch.sh 755 <<'SH'
#!/bin/sh
# pw-watch.sh KEY_FILE HITS STOP: until STOP exists.
while [ ! -e "$3" ]; do
    grep -laF -f "$1" /proc/[0-9]*/cmdline /proc/[0-9]*/environ 2>/dev/null |
        while read -r f; do p=${f%/*}; echo "${p#/proc/} $(cat "$p/comm" 2>/dev/null) ${f##*/}"; done >>"$2"
    sleep 0.2
done
SH
t0=$SECONDS
on backotter "sudo -n bash -c 'rm -f /run/bko-pw-hits /run/bko-pw-stop && setsid -f sh /run/bko-pw-watch.sh $B/backshot1/keys/disk-passphrase /run/bko-pw-hits /run/bko-pw-stop </dev/null >/dev/null 2>&1 &&
    cd / && BKO_GENTOO_CMDLINE=\"console=ttyS0,115200n8 console=tty0\" bash $G/gentoo-bootstrap.sh --detach $SERIAL $HOST'" >"$W/detach.txt" 2>&1 || true
cat "$W/detach.txt"
check "--detach returns at once, naming the run's pid" grep -qE '^detached: pid [0-9]+,' "$W/detach.txt"
rc=
while ((SECONDS - t0 < INSTALL_TIMEOUT)); do
    sleep 30
    kill -0 "$qemu_pid" 2>/dev/null || break
    rc=$(timeout 60 ssh "${OPTS[@]}" backotter@127.0.0.1 'cat /run/bko-gentoo/exit 2>/dev/null' 2>/dev/null || true)
    [[ -z $rc ]] || break
    # The log so far, for whoever watches WORK_DIR.
    timeout 60 ssh "${OPTS[@]}" backotter@127.0.0.1 'sudo -n cat /run/bko-gentoo/install.log' >"$W/bootstrap.log" 2>/dev/null || true
done
on backotter 'sudo -n touch /run/bko-pw-stop; sleep 2' || true
on backotter 'sudo -n cat /run/bko-gentoo/install.log' >"$W/bootstrap.log" 2>&1 || true
echo "  gentoo-bootstrap.sh: exit ${rc:-none} after $((SECONDS - t0)) s (bootstrap.log)"
grep -E '^== .* gentoo\.sh took|source-built:' "$W/bootstrap.log" || true
tail -n 4 "$W/bootstrap.log"
check "gentoo-bootstrap.sh exited 0" test "${rc:-x}" = 0
check "gentoo-bootstrap.sh ran to the end" grep -qx BKO-GENTOO-INSTALL-OK "$W/bootstrap.log"
check "nothing of the target is still mounted" on backotter "! findmnt -rn -o SOURCE | grep -qE 'nvme0n1|vg0|cryptroot'"
check "nothing of the chroot is left" on backotter "! findmnt -rn /run/bko-gentoo/mnt >/dev/null && test ! -e /run/bko-gentoo/mnt"
check "no process's mount namespace still holds the chroot" on backotter "sudo -n sh -c '! grep -qs /run/bko-gentoo/mnt /proc/[0-9]*/mountinfo'"
check "nothing of the volume is left open (cryptroot, vg0)" on backotter '! ls /dev/mapper | grep -qE "^(cryptroot|vg0-)"'
check "the root volume is LUKS2" grep -qE '^Version:[[:space:]]+2$' "$W/bootstrap.log"
check "crypttab is cryptroot UUID=... none luks,discard" grep -qE '^crypttab: cryptroot[[:space:]]+UUID=[0-9a-f-]+[[:space:]]+none[[:space:]]+luks,discard$' "$W/bootstrap.log"
check "GRUB's command line has rd.luks.uuid" grep -qE '^grub\.cfg: linux[[:space:]].* rd\.luks\.uuid=[0-9a-f-]+' "$W/bootstrap.log"
on backotter 'cat /run/bko-pw-hits 2>/dev/null' >"$W/pw-hits.txt" || true
check "no process had the passphrase in its command line or environment: $(tr '\n' ' ' <"$W/pw-hits.txt")" test ! -s "$W/pw-hits.txt"
check "the watcher ran, and stopped" on backotter 'test -e /run/bko-pw-stop && ! pgrep -f "[p]w-watch.sh" >/dev/null'
unlogged() { ! grep -qaF -f "$PASS" "$W/bootstrap.log" &&
             on backotter "sudo -n sh -c 'ls $G/install-*-exit0.log >/dev/null && ! grep -qaF -f $B/backshot1/keys/disk-passphrase $G/install-*.log'"; }
check "the passphrase is in neither log (the stick's, the drive's copy)" unlogged
on backotter 'sudo -n efibootmgr -v' >"$W/efibootmgr-rescue.txt" 2>&1 || true
check "an NVRAM entry gentoo for \\EFI\\gentoo\\grubx64.efi, first in BootOrder" sh -c "
    n=\$(sed -n 's/^Boot\\([0-9A-F]\\{4\\}\\)\\* gentoo\\b.*/\\1/p' '$W/efibootmgr-rescue.txt' | head -n 1)
    test -n \"\$n\" && grep -E \"^Boot\$n\" '$W/efibootmgr-rescue.txt' | grep -qi 'EFI.gentoo.grubx64.efi' &&
    grep -qE \"^BootOrder: \$n\" '$W/efibootmgr-rescue.txt'"
check "the drive unmounts" on backotter "sudo -n umount $B"
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
    on root 'cat /etc/os-release; hostname; efibootmgr -v | grep -Ev "^ +(dp|data):"; systemctl is-enabled sshd.service avahi-daemon.service;
             findmnt -R /; lsblk -o NAME,TYPE,SIZE,FSTYPE,LABEL; ls -la /boot /boot/EFI; cat /etc/crypttab; cat /proc/cmdline; uname -r' >"$W/$n-facts.txt" 2>&1 || true
    check "the boot finished: $(cat "$W/$n-state.txt")" grep -qxE 'running|degraded' "$W/$n-state.txt"
    check "no unit failed: $(tr '\n' ' ' <"$W/$n-failed.txt")" test ! -s "$W/$n-failed.txt"
    check "os-release says ID=gentoo" grep -qE "^ID='?gentoo'?$" "$W/$n-facts.txt"
    check "hostname is $HOST" test "$(on root cat /proc/sys/kernel/hostname)" = "$HOST"
    check "sshd and avahi-daemon are running" on root 'systemctl is-active -q sshd.service avahi-daemon.service'
    check "/ is ext4 on vg0/root, its PV cryptroot on nvme0n1p2's LUKS2, /boot the vfat ESP" \
        on root 'test "$(findmnt -no FSTYPE /)" = ext4 && test "$(findmnt -nvo SOURCE /)" = /dev/mapper/vg0-root &&
                 test "$(pvs --noheadings -o pv_name,vg_name | awk "\$2 == \"vg0\" { print \$1 }")" = /dev/mapper/cryptroot &&
                 cryptsetup status cryptroot | grep -qE "^ *device: +/dev/nvme0n1p2$" &&
                 cryptsetup luksDump /dev/nvme0n1p2 | grep -qE "^Version:[[:space:]]+2$" &&
                 test "$(findmnt -no FSTYPE /boot)" = vfat && test "$(lsblk -no PARTTYPE "$(findmnt -nvo SOURCE /boot)")" = c12a7328-f81f-11d2-ba4b-00a0c93ec93b'
    check "the command line has rd.luks.uuid=$(on root 'cryptsetup luksUUID /dev/nvme0n1p2')" \
        on root 'grep -qw "rd.luks.uuid=$(cryptsetup luksUUID /dev/nvme0n1p2)" /proc/cmdline'
    check "no /etc/cryptsetup-keys.d (no key file of its own)" on root 'test ! -e /etc/cryptsetup-keys.d'
    cur=$(on root "efibootmgr | sed -n 's/^BootCurrent: //p'")
    check "the firmware booted Boot$cur, \\EFI\\gentoo\\grubx64.efi" sh -c "grep -E '^Boot$cur' '$W/$n-facts.txt' | grep -qi 'EFI.gentoo.grubx64.efi'"
}

# BackShot1's family S, as on the Thinkpad.
BS1=/mnt/bs1-backup/backshot1
MOUNT_BIG="mkdir -p /mnt/bs1-backup; for i in \$(seq 15); do mount LABEL=$BIG /mnt/bs1-backup 2>/dev/null && break; sleep 2; done; mountpoint -q /mnt/bs1-backup"
LUKS_FILES='sha256sum /etc/crypttab /etc/dracut.conf.d/* /boot/initramfs-*.img /etc/backshot1/luks-unlock /usr/local/lib/backshot1/* 2>&1'
if ((${#fails[@]} == 0)); then
    echo "== gentoo-typed: the disk and $BIG from NVRAM, the passphrase typed"
    t0=$SECONDS
    vm gentoo-typed drive
    typer gentoo-typed 30 45 60 80 100 130 160 200 240
    shots gentoo-typed 20 35 60 90
    if wait_ssh root 420; then
        typer_stop
        echo "  ok    root's SSH, with the fixed host key, $((SECONDS - t0)) s after power-on"
        booted gentoo-typed
        # BackShot1 staged on the drive, as `backshot1 stage` does on mybig512
        # (the keys are there already).
        on root "$MOUNT_BIG && mkdir -p $BS1/bin $BS1/runs" >/dev/null
        printf 'SOURCE_HOST=%s\nINTERNAL_SERIAL=%s\nBACKUP_LABEL=%s\n' "$HOST" "$SERIAL" "$BIG" >"$W/bs1.conf"
        names=()
        for n in common backup restore verify manifest ostree abroot adopt luks unlock-key list efi; do names+=("$HERE/backshot1/bs1-$n"); done
        scp "${SCP_OPTS[@]}" "${names[@]}" "$HERE/../ostree_paths.py" "$W/bs1.conf" root@127.0.0.1:$BS1/
        on root "chmod 755 $BS1/bs1-*; chmod 600 $BS1/keys/disk-passphrase"
        on root "$LUKS_FILES" >"$W/luks-files-before.txt" || true
        set +e
        on root "sh $BS1/bs1-adopt" >"$W/adopt.log" 2>&1
        rc=$?
        set -e
        echo "  bs1-adopt: exit $rc (adopt.log)"
        grep -E 'family|dracut' "$W/adopt.log" | tail -n 8 || true
        check "bs1-adopt passes" test "$rc" = 0
        on root '. /etc/backshot1/luks-unlock && echo "family=$family generator=$generator volumes=$volumes label=$label path=$path fstype=$fstype"' >"$W/luks-record.txt" 2>&1 || true
        cat "$W/luks-record.txt"
        uuid=$(on root 'cryptsetup luksUUID /dev/nvme0n1p2' || true)
        check "the record: family S, dracut, cryptroot:$uuid:S, the key on $BIG's XFS" \
            grep -qx "family=S generator=dracut volumes=cryptroot:$uuid:S label=$BIG path=/backshot1/keys/disk-passphrase fstype=xfs" "$W/luks-record.txt"
        on root 'cat /etc/crypttab' >"$W/crypttab.adopted" 2>&1 || true
        check "crypttab: cryptroot's key the drive's, keyfile-timeout=30s" \
            grep -qE "^cryptroot[[:space:]]+UUID=${uuid}[[:space:]]+/backshot1/keys/disk-passphrase:LABEL=${BIG}[[:space:]]+(.*,)?keyfile-timeout=30s(,|$)" "$W/crypttab.adopted"
        on root 'lsinitrd -f etc/crypttab /boot/initramfs-$(uname -r).img' >"$W/initramfs-crypttab.txt" 2>&1 || true
        check "the remade initramfs has that crypttab" grep -qE "^cryptroot[[:space:]]+(UUID=|/dev/disk/by-uuid/)${uuid}[[:space:]]+/backshot1/keys/disk-passphrase:LABEL=$BIG" "$W/initramfs-crypttab.txt"
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
        echo "  FAIL  the installed disk never answered on SSH with the passphrase typed (screen-gentoo-typed-*.png, console-gentoo-typed.log)"
        fails+=(gentoo-typed-boot)
    fi
    qemu_pid=
fi

if ((${#fails[@]} == 0)); then
    echo "== gentoo-drive: the disk and $BIG, nothing typed"
    t0=$SECONDS
    vm gentoo-drive drive
    shots gentoo-drive 20 35 50 80 120
    if wait_ssh root 300; then
        echo "  ok    root's SSH, $((SECONDS - t0)) s after power-on, nothing typed"
        booted gentoo-drive
        on root "$MOUNT_BIG && bash $BS1/bs1-verify --unlock" >"$W/unlock-drive.txt" 2>&1 || true
        cat "$W/unlock-drive.txt"
        check "bs1-verify --unlock: UNLOCK=drive, cryptroot drive" sh -c "grep -qx UNLOCK=drive '$W/unlock-drive.txt' && grep -qx '  cryptroot drive' '$W/unlock-drive.txt'"
        on root 'umount /mnt/bs1-backup' || true
        vm_off root 'systemctl poweroff'
    else
        echo "  FAIL  the disk never answered on SSH with the drive and nothing typed (screen-gentoo-drive-*.png)"
        fails+=(gentoo-drive-boot)
    fi
    qemu_pid=
fi

if ((${#fails[@]} == 0)); then
    echo "== gentoo-nodrive: the disk alone, the passphrase typed after the 30 s wait"
    t0=$SECONDS
    vm gentoo-nodrive
    typer gentoo-nodrive 60 75 90 110 140 170 210 250
    shots gentoo-nodrive 20 45 70 100
    if wait_ssh root 500; then
        typer_stop
        echo "  ok    root's SSH, $((SECONDS - t0)) s after power-on, the passphrase typed"
        booted gentoo-nodrive
        on root 'mkdir -p -m 700 /run/bs1-check'
        scp "${SCP_OPTS[@]}" "$HERE/backshot1/bs1-common" "$HERE/backshot1/bs1-verify" "$W/bs1.conf" root@127.0.0.1:/run/bs1-check/
        on root 'bash /run/bs1-check/bs1-verify --unlock' >"$W/unlock-nodrive.txt" 2>&1 || true
        cat "$W/unlock-nodrive.txt"
        check "bs1-verify --unlock: typed, not the drive (UNLOCK=attended, cryptroot typed)" sh -c "grep -qx UNLOCK=attended '$W/unlock-nodrive.txt' && grep -qx '  cryptroot typed' '$W/unlock-nodrive.txt'"
        # The passphrase in no file of the installed system's.
        scp "${SCP_OPTS[@]}" "$PASS" root@127.0.0.1:/run/bs1-check/pw
        on root 'grep -rlaF -f /run/bs1-check/pw /etc /root /var/log /boot 2>/dev/null; rm -f /run/bs1-check/pw' >"$W/pw-files.txt" 2>&1 || true
        check "the passphrase is in no file of /etc, /root, /var/log or /boot: $(tr '\n' ' ' <"$W/pw-files.txt")" test ! -s "$W/pw-files.txt"
        vm_off root 'systemctl poweroff'
    else
        typer_stop
        echo "  FAIL  the disk alone never answered on SSH with the passphrase typed (screen-gentoo-nodrive-*.png)"
        fails+=(gentoo-nodrive-boot)
    fi
    qemu_pid=
fi

if ((${#fails[@]})); then echo "FAILED: ${fails[*]}"; exit 1; fi
echo PASS
