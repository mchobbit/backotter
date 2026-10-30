#!/usr/bin/env bash
# Whole-system backup and restore of a FreeBSD test guest with BackOtter's
# restic, and a boot of the result: the FreeBSD counterpart of compare.py.
#
# Usage: compare-freebsd.sh GUEST
#   e.g. compare-freebsd.sh freebsd15        (/ on ZFS)
#        compare-freebsd.sh freebsd15-ufs    (/ on UFS)
#
# A Linux host can't write UFS, and restic on Linux skips FreeBSD's ACLs and
# file flags, so the work happens in FreeBSD: the freebsd15-ufs guest stands
# in for a FreeBSD rescue system, with a throwaway overlay on GUEST's disk
# (src.qcow2) and a blank disk (dst.qcow2) attached; freebsd/rescue.sh does
# the rest there.  Then dst.qcow2 is booted on its own and checked over SSH
# with GUEST's key.  In ~/backotter-work/compare-freebsd/GUEST/: src.qcow2,
# dst.qcow2, rescue.log, results/ (rescue.sh's), boot.log, boot-check.txt.
#
# restic is $RESTIC_BKO_FREEBSD, or else the newest FreeBSD build in
# ~/backotter-work/restic/dist/; it can be BackOtter's rustic instead.  With
# $RESTORE_BKO_FREEBSD, that one restores what $RESTIC_BKO_FREEBSD backed up
# (restic-bko and rustic-bko, the other way round too, read each other's).
# Runs as an ordinary user in the kvm group.

set -euo pipefail

RESCUE=freebsd15-ufs
RESCUE_PORT=2270
BOOT_PORT=2271

here=$(dirname "$(realpath "$0")")
testvm=$(dirname "$here")
work_base=$(dirname "$testvm")
die() { echo "compare-freebsd: $*" >&2; exit 1; }

[[ $# -eq 1 ]] || die "usage: $0 GUEST"
guest=$1
image=$(ls "$work_base/vms/$guest"/*.qcow2 2>/dev/null | head -n 1)
[[ -n $image ]] || die "no disk image in $work_base/vms/$guest"
rescue_image=$(ls "$work_base/vms/$RESCUE"/*.qcow2 | head -n 1)
restic=${RESTIC_BKO_FREEBSD:-$(ls -t "$work_base"/restic/dist/restic-backotter-*-freebsd-amd64 2>/dev/null | head -n 1)}
[[ -x $restic ]] || die "no FreeBSD build of restic: set RESTIC_BKO_FREEBSD, or run build-backotter.sh"
restore_with=${RESTORE_BKO_FREEBSD:-$restic}
work=$work_base/compare-freebsd/$guest
mkdir -p "$work"
cd "$work"

ssh_to() {  # KEY PORT COMMAND...
    local key=$1 port=$2; shift 2
    ssh -q -i "$key" -p "$port" -o BatchMode=yes -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 root@127.0.0.1 "$@"
}
wait_ssh() {  # KEY PORT PID
    for _ in $(seq 80); do
        ssh_to "$1" "$2" true 2>/dev/null && return 0
        kill -0 "$3" 2>/dev/null || return 1
        sleep 3
    done
    return 1
}
vm=""
trap '[[ -n $vm ]] && kill "$vm" 2>/dev/null; true' EXIT

# --- The rescue system: back up, lay out, restore, compare ------------------------
rm -f src.qcow2 dst.qcow2
qemu-img create -q -f qcow2 -b "$image" -F qcow2 src.qcow2
qemu-img create -q -f qcow2 dst.qcow2 16G
echo "== rescue system ($RESCUE), with $guest as vtbd1 and a blank vtbd2"
SSH_PORT=$RESCUE_PORT "$testvm/vm-run" --ram --serial-log rescue-serial.log "$rescue_image" -- \
    -drive "file=$work/src.qcow2,if=virtio,format=qcow2" \
    -drive "file=$work/dst.qcow2,if=virtio,format=qcow2" </dev/null >/dev/null 2>&1 &
vm=$!
rkey=$work_base/vms/$RESCUE/id_ed25519
wait_ssh "$rkey" $RESCUE_PORT $vm || die "the rescue system didn't come up; see rescue-serial.log"
scp -q -i "$rkey" -P $RESCUE_PORT -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    "$restic" "$restore_with" "$here"/freebsd/{rescue,torture,fsmeta}.sh root@127.0.0.1:/root/
ssh_to "$rkey" $RESCUE_PORT "sh /root/rescue.sh /root/$(basename "$restic") /root/$(basename "$restore_with")" 2>&1 |
    tee rescue.log
rm -rf results
scp -q -r -i "$rkey" -P $RESCUE_PORT -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    root@127.0.0.1:/root/results .
ssh_to "$rkey" $RESCUE_PORT "shutdown -p now" >/dev/null 2>&1 || true
wait "$vm" || true
vm=""

# --- The restored disk, booted on its own ---------------------------------------
echo "== booting the restored disk"
SSH_PORT=$BOOT_PORT "$testvm/vm-run" --ram --serial-log boot.log dst.qcow2 </dev/null >/dev/null 2>&1 &
vm=$!
gkey=$work_base/vms/$guest/id_ed25519
if wait_ssh "$gkey" $BOOT_PORT $vm; then
    ssh_to "$gkey" $BOOT_PORT '
        echo "ssh: OK"; freebsd-version; uname -n
        echo "-- file systems"; mount -p | grep -v devfs
        zpool list -H -o name,health 2>/dev/null; bectl list -H 2>/dev/null
        echo "-- base system files FreeBSD installs with schg"; ls -lo /lib/libc.so.* /libexec/ld-elf.so.1 | cut -c1-80
        echo "-- test files"; cd /root/bko-torture && ls -lo flag-* | awk "{print \$5, \$9}"
        getfacl acl-file acl-dir 2>&1 | grep -v "^#"
        lsextattr -q user xattrs; lsextattr -q system xattrs
        echo "-- errors in /var/log/messages"; grep -i "fail\|error" /var/log/messages | tail -n 5
        shutdown -p now >/dev/null 2>&1' >boot-check.txt 2>&1 || true
else
    echo "ssh: no answer within 4 minutes" >boot-check.txt
fi
wait "$vm" || true
vm=""
cat boot-check.txt
