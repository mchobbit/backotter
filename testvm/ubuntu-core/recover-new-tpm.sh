#!/usr/bin/env bash
# The new-machine path of uc24-fde, when run mode can't unlock (it reboots
# in a loop without asking: new-tpm-test.sh): recover mode, by hand.  On a
# qcow2 overlay of the guest in WORK, a NEW swtpm state and fresh OVMF
# variables, with QEMU's QMP socket:
#   1. the overlay's seed grubenv is set to snapd_recovery_mode=recover
#      for its default system (what `snap reboot --recover` writes; needs
#      sudo -n for qemu-nbd), so the first boot is recover mode;
#   2. the recovery key is typed on the keyboard (qmp.py type: the prompt
#      is on tty1, the last console=, not on serial), twice, 20 s apart
#      (ubuntu-data, then ubuntu-save), with screen grabs around each;
#   3. in recover mode over SSH: what snap-bootstrap and snapd say
#      (WORK/recover.txt), then snap reboot --run;
#   4. the boots after: whether run mode unlocks without a key (two boots,
#      each with grabs, WORK/run-N.txt), or loops again.
# One summary line per step on stdout, "BKO-RECOVER ...".  The guest is
# only read.  Its grabs are in WORK/shots.
#
# Usage: recover-new-tpm.sh [--system LABEL] GUEST_DIR KEY_FILE WORK PORT
# (--system: recover into that recovery system, default the first good one)
set -uo pipefail
here=$(dirname "$(realpath "$0")")
testvm=$(dirname "$here")
die() { echo "recover-new-tpm: $*" >&2; exit 1; }
want_sys=""; [[ ${1:-} == --system ]] && { want_sys=$2; shift 2; }
[[ $# -eq 4 ]] || die "usage: $0 [--system LABEL] GUEST_DIR KEY_FILE WORK PORT"
gdir=$(realpath "$1") key=$(realpath "$2") work=$3 port=$4
g=$(basename "$gdir")
[[ -f $gdir/$g.qcow2 && -f $gdir/id_ed25519 && -f $key ]] || die "need $gdir/$g.qcow2, its id_ed25519 and $key"
ss -ltnH "sport = :$port" | grep -q . && die "port $port is taken"
mkdir -p "$work/shots"; work=$(realpath "$work")
[[ -e $work/disk.qcow2 ]] && die "$work/disk.qcow2 exists: a new WORK each run"
qemu-img create -q -f qcow2 -b "$gdir/$g.qcow2" -F qcow2 "$work/disk.qcow2"
cp /usr/share/OVMF/OVMF_VARS_4M.ms.fd "$work/vars.fd"
mkdir "$work/tpm"

# 1. Recover mode, as `snap reboot --recover` asks for it: the seed
# grubenv (ubuntu-seed, the 2nd partition, EFI/ubuntu/grubenv) of the
# OVERLAY gets snapd_recovery_mode=recover for its default system.  Keys at
# the firmware's splash land in OVMF's setup instead (fresh variables boot
# through shim's fallback, which resets once).  The nbd device is taken as
# vm-mount takes one: a free one under its lock, never one in use.
seed_recover() {
    sudo -n bash -s "$work/disk.qcow2" "$work" "$want_sys" <<'EOF'
set -euo pipefail
img=$1 work=$2 want=${3:-} PATH=$PATH:/usr/sbin:/sbin
[[ -d /sys/module/nbd ]] || modprobe nbd max_part=16
exec 9>/run/lock/backotter-nbd.lock; flock 9
dev=""
for b in /sys/block/nbd*; do [[ -e $b/pid ]] || { dev=/dev/${b##*/}; break; }; done
[[ -n $dev ]] || { echo "no free nbd device" >&2; exit 1; }
qemu-nbd --connect="$dev" --format=qcow2 "$img"
trap 'qemu-nbd --disconnect "$dev" >/dev/null' EXIT
flock -u 9
for _ in $(seq 50); do [[ -b ${dev}p2 ]] && break; sleep 0.1; done
env=$work/seed-grubenv
mcopy -n -i "${dev}p2" ::EFI/ubuntu/grubenv "$env"
cp "$env" "$env.before"
# In run mode snapd_recovery_system is empty: then the first good system.
sys=$want; [[ -n $sys ]] || sys=$(grub-editenv "$env" list | sed -n 's/^snapd_recovery_system=//p')
[[ -n $sys ]] || sys=$(grub-editenv "$env" list | sed -n 's/^snapd_good_recovery_systems=//p' | cut -d, -f1)
[[ -n $sys ]] || { echo "no recovery system named in the seed grubenv" >&2; exit 1; }
grub-editenv "$env" set snapd_recovery_mode=recover "snapd_recovery_system=$sys"
mcopy -o -i "${dev}p2" "$env" ::EFI/ubuntu/grubenv
sync
chown "$SUDO_UID:$SUDO_GID" "$env" "$env.before"
echo "$sys"
EOF
}
label=$(seed_recover) || die "couldn't set the seed grubenv on the overlay"
echo "BKO-RECOVER seed grubenv: snapd_recovery_mode=recover, system $label"
Q() { python3 -I "$here/qmp.py" "$work/qmp.sock" "$@"; }
shot() { Q screendump "$work/shots/$1.png" 2>/dev/null; }
gssh=(ssh -q -i "$gdir/id_ed25519" -p "$port" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
    -o ConnectTimeout=5 tester@127.0.0.1)
G() { timeout "${T:-120}" "${gssh[@]}" "$@" </dev/null; }
log=$work/serial.log
pid=""
trap '[[ -n $pid ]] && kill "$pid" 2>/dev/null; true' EXIT
SSH_PORT=$port "$testvm/vm-run" --uefi --memory 2048 --uefi-vars "$work/vars.fd" --tpm "$work/tpm" --serial-log "$log" \
    "$work/disk.qcow2" -- -qmp "unix:$work/qmp.sock,server=on,wait=off" </dev/null >"$work/vm-run.out" 2>&1 &
pid=$!
wait_for() {  # PATTERN SECONDS [NTH]: the serial log has PATTERN NTH times (default 1)
    local i
    for ((i = 0; i < $2 * 5; i++)); do
        local c; c=$(grep -ac -- "$1" "$log" 2>/dev/null) || true
        (( ${c:-0} >= ${3:-1} )) && return 0
        kill -0 "$pid" 2>/dev/null || return 1
        sleep 0.2
    done
    return 1
}
mode_up() {  # MODE SECONDS: SSH answers in MODE (run, recover)
    local i
    for ((i = 0; i < $2 / 10; i++)); do
        T=20 G "grep -q snapd_recovery_mode=$1 /proc/cmdline" 2>/dev/null && return 0
        kill -0 "$pid" 2>/dev/null || return 1
        sleep 10
    done
    return 1
}

wait_for "snapd_recovery_mode=recover" 180 || { shot 02-no-recover; die "no recover-mode kernel in 180 s; see $work/shots, $log"; }
echo "BKO-RECOVER $(grep -a -m1 'Command line:.*snapd_recovery_mode=recover' "$log" | sed 's/.*Command line: //')"

# 2. The recovery key, on the keyboard, for each volume.
for n in 1 2; do
    sleep 20; shot "03-before-key-$n"
    Q type "$key" >/dev/null && echo "BKO-RECOVER typed the key ($n)"
    sleep 5; shot "04-after-key-$n"
done
if mode_up recover 300; then
    echo "BKO-RECOVER recover mode answers on SSH"
else
    shot 05-no-ssh; die "recover mode never answered on SSH; see $work/shots"
fi

# 3. What recover mode knows.
T=300 G 'sudo snap wait system seed.loaded; sleep 30
    set -x; cat /proc/cmdline; snap version; sudo snap recovery; snap changes
    sudo ls -la /run/snapd/snap-bootstrap; for f in /run/snapd/snap-bootstrap/*.json; do echo "== $f"; sudo cat "$f"; echo; done
    sudo journalctl -b --no-pager -o short-monotonic | grep -iE "recovery key|reseal|reprovision|provision|unlock|sealed|tpm|fde|secboot|keyslot" | grep -vE "[0-9]{5}-[0-9]{5}" | tail -n 80
    sudo snap debug api /v2/system-volumes 2>&1 | head -c 3000; echo
    sudo grep -a -o -E "[a-z-]*(reprovision|replace-platform-key|replace-recovery-key|platform-key|reseal)[a-z-]*" /usr/lib/snapd/snapd | sort -u | head -40
    snap help --all 2>&1 | grep -iE "fde|recovery|reseal|tpm"
    sudo snap debug api /v2/system-volumes?by-container-role=true 2>&1 | head -c 3000; echo
    ls /var/lib/snapd/device/fde /run/mnt/ubuntu-boot/device/fde /run/mnt/ubuntu-seed/device/fde 2>&1' >"$work/recover.txt" 2>&1
echo "BKO-RECOVER recover.txt: $(wc -l <"$work/recover.txt") lines"
T=60 G 'sudo snap reboot --run' >>"$work/recover.txt" 2>&1
echo "BKO-RECOVER snap reboot --run"

# 4. The run-mode boots after it.
for n in 1 2; do
    before=$(grep -ac "Command line: snapd_recovery_mode=run" "$log")
    wait_for "Command line: snapd_recovery_mode=run" 240 $((before + 1)) || true
    up=no
    for i in $(seq 18); do
        sleep 10; shot "06-run-$n-$(printf %02d "$i")"
        T=20 G 'grep -q snapd_recovery_mode=run /proc/cmdline' 2>/dev/null && { up=yes; break; }
    done
    echo "BKO-RECOVER run boot $n: $([[ $up == yes ]] && echo "run mode over SSH, no key typed" || echo "NO run mode in 180 s")"
    [[ $up == yes ]] || break
    T=300 G 'sudo snap wait system seed.loaded; sleep 30; set -x; snap changes
        sudo journalctl -b --no-pager -o short-monotonic | grep -iE "recovery key|reseal|reprovision|provision|unlock|sealed|tpm|keyslot" | grep -vE "[0-9]{5}-[0-9]{5}" | tail -n 40
        for f in /run/snapd/snap-bootstrap/*.json; do echo "== $f"; sudo cat "$f"; echo; done' >"$work/run-$n.txt" 2>&1
    [[ $n == 2 ]] || T=60 G 'sudo systemctl reboot' 2>/dev/null
done
T=30 G 'sudo systemctl poweroff' 2>/dev/null
for _ in $(seq 60); do kill -0 "$pid" 2>/dev/null || break; sleep 2; done
kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; pid=""
echo "BKO-RECOVER done"
