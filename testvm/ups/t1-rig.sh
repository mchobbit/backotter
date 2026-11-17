#!/usr/bin/env bash
# T1 of docs/design/ups-shutdown.md: boot one of the two rehearsal guests.
#
# Usage (on a KVM farm host, as m, under a reservation of its own):
#   cd ~/backotter-work &&
#     WEAR_TAG=ups:t1:M ./cmp-run.sh --reserve 0.8 1.5 bash testvm/ups/t1-rig.sh M
#   (and the same for S)
#
#   M  the NUT master: debian13, ssh on 127.0.0.1:$PORT_M (default 2770)
#   S  the secondary: debian13-zfs, ssh on 127.0.0.1:$PORT_S (2771)
#
# Each boots from its own qcow2 overlay in T1_DIR (default
# /mnt/bko-ram/ups-t1), made on the first boot over ~/backotter-work/vms'
# image, so what the rehearsal installs and configures stays between boots;
# rm -r T1_DIR clears it all.  The two share a private LAN, a QEMU socket
# netdev on 127.0.0.1:$LAN_PORT (default 47713): M listens, S connects; no
# bridges, no taps, no root.  The guests give themselves 10.77.0.1 (M) and
# 10.77.0.2 (S) on the NIC with MAC 52:54:00:77:00:0N.
#
# The serial console goes to T1_DIR/NAME.serial.log, and a copy with the
# host's time on each line to NAME.serial.ts; NAME.events gets "boot" and
# "exit" lines with the host's time.  It returns when the guest powers off.
#
# Needs: testvm/vm-run, qemu-img.

set -euo pipefail

name=${1:?usage: t1-rig.sh M|S}
dir=${T1_DIR:-/mnt/bko-ram/ups-t1}
work=$HOME/backotter-work
lan=${LAN_PORT:-47713}
case $name in
    M) base=$work/vms/debian13/debian13.qcow2 port=${PORT_M:-2770} mac=52:54:00:77:00:01 net=listen ;;
    S) base=$work/vms/debian13-zfs/debian13-zfs.qcow2 port=${PORT_S:-2771} mac=52:54:00:77:00:02 net=connect ;;
    *) echo "t1-rig.sh: M or S, not $name" >&2; exit 2 ;;
esac

mkdir -p "$dir"
img=$dir/$name.qcow2
[ -e "$img" ] || qemu-img create -q -f qcow2 -F qcow2 -b "$base" "$img"

ts() { date '+%F %T.%3N'; }
echo "$(ts) boot $name" >>"$dir/$name.events"
: >"$dir/$name.serial.log"
# The host's time on each console line (tail --pid: gone when this is).
tail -n +1 -F --pid=$$ "$dir/$name.serial.log" 2>/dev/null |
    while IFS= read -r l; do printf '%s %s\n' "$(ts)" "${l%$'\r'}"; done >>"$dir/$name.serial.ts" &

rc=0
SSH_PORT=$port "$work/testvm/vm-run" --serial-log "$dir/$name.serial.log" --memory "${T1_MEM:-1024}" "$img" -- \
    -netdev "socket,id=lan,$net=127.0.0.1:$lan" -device "virtio-net-pci,netdev=lan,mac=$mac" || rc=$?
sleep 1
echo "$(ts) exit $name rc=$rc" >>"$dir/$name.events"
exit $rc
