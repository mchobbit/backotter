#!/usr/bin/env bash
# Check that a Sculpt OS test guest (make-sculpt.sh) still boots and uses its
# disk.  Sculpt has no serial log and no sshd, so this boots it on a throwaway
# qcow2 overlay with a QEMU monitor and checks three things:
#   - the screen: Sculpt's Leitzentrale (its dark blue background, the yellow
#     tab of its top bar) within BOOT_TIMEOUT, from screendumps;
#   - the disk: the GENODE* partition's ext2 file system marked "not clean"
#     in the overlay, which Sculpt's mount (read-write) does; a boot that
#     never found the partition leaves it clean, as make-sculpt.sh did;
#   - QEMU still running then (-no-reboot: a crash ends it), and e2fsck
#     finding no errors it can't fix in what it left after being stopped.
# The guest's own disk isn't written.
#
# Usage: ./sculpt-check.sh WORK_DIR
#   e.g. ./sculpt-check.sh vms/sculpt2610
# Exits 0 if all of it holds.  The last screendump is WORK_DIR/check-screen.png.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, e2fsprogs, python3.

set -euo pipefail

BOOT_TIMEOUT=300                # seconds until the Leitzentrale is on the screen
SETTLE_TIME=30                  # seconds it runs on after that, for its mount

here=$(dirname "$(realpath "$0")")
PATH=$PATH:/sbin:/usr/sbin
die() { echo "sculpt-check: $*" >&2; exit 1; }

[[ $# -eq 1 ]] || die "usage: $0 WORK_DIR"
dir=$(realpath "$1")
disks=("$dir"/sculpt*.qcow2)
[[ ${#disks[@]} -eq 1 && -f ${disks[0]} ]] || die "$dir has no single sculpt*.qcow2"
disk=${disks[0]}
[[ -f $dir/vm-run-options ]] || die "$dir has no vm-run-options"

work=$(mktemp -d)
qemu_pid=""
cleanup() {
    [[ -z $qemu_pid ]] || kill "$qemu_pid" 2>/dev/null || true
    rm -rf "$work"
}
trap cleanup EXIT

# GENODE*'s first sector, from the primary GPT ("GENODE" too: Sculpt doesn't
# mount that one without asking, which the check then finds).
qemu-img dd -f qcow2 -O raw if="$disk" of="$work/head" bs=512 count=34
start=$(python3 -I - "$work/head" <<'EOF'
import struct, sys
d = open(sys.argv[1], "rb").read()
assert d[512:520] == b"EFI PART", "no GPT"
table, count, size = struct.unpack_from("<QII", d, 512 + 72)
for n in range(count):
    e = d[table * 512 + n * size:table * 512 + (n + 1) * size]
    if e[56:128].decode("utf-16-le").rstrip("\0") in ("GENODE*", "GENODE"):
        print(struct.unpack_from("<Q", e, 32)[0])
        break
EOF
) || die "can't read $disk's GPT"
[[ -n $start ]] || die "$disk has no GENODE* partition"
# s_state, at byte 58 of the superblock (1024 bytes into the partition): bit 0
# is "cleanly unmounted".  qemu-img dd's count counts from the disk's start,
# skip included.
fs_state() {
    qemu-img dd -f qcow2 -O raw if="$1" of="$work/sb" bs=512 skip=$((start + 2)) count=$((start + 4)) ||
        { echo "unreadable"; return; }
    python3 -I -c 'import struct, sys
d = open(sys.argv[1], "rb").read()
magic, state = struct.unpack_from("<HH", d, 56)
print("not-ext2" if magic != 0xEF53 else "clean" if state & 1 else "not-clean")' "$work/sb"
}
[[ $(fs_state "$disk") == clean ]] || die "GENODE* isn't clean on $disk itself; was the guest stopped while running?"

qemu-img create -q -f qcow2 -b "$disk" -F qcow2 "$work/overlay.qcow2"
port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
echo "Booting $(basename "$disk") on an overlay, until the Leitzentrale is up (at most $BOOT_TIMEOUT s)"
# shellcheck disable=SC2046
SSH_PORT=$port "$here/vm-run" $(<"$dir/vm-run-options") --serial-log /dev/null "$work/overlay.qcow2" -- \
    -monitor "unix:$work/mon.sock,server,nowait" -no-reboot >"$work/qemu.log" 2>&1 &
qemu_pid=$!
shot="$dir/check-screen.png"
# The Leitzentrale: its background, (34, 41, 61), near the left edge below
# the top bar and halfway down (its Diagnostics box can cover the bottom-left
# corner), and its top bar's yellow tab ("Components" or "Files", whichever
# is open), which no boot screen before it has.
leitzentrale_up() {
    python3 -I - "$here/haiku" "$work/mon.sock" "$shot" <<'EOF'
import sys
sys.path.insert(0, sys.argv[1])
import drive
width, height, pixels = drive.screen(sys.argv[2])
if len(pixels) != width * height * 3:
    sys.exit(1)
drive.save_png(sys.argv[2], sys.argv[3])
def at(x, y):
    i = (y * width + x) * 3
    return pixels[i:i + 3]
blue = all(all(abs(p - c) <= 10 for p, c in zip(at(10, y), (34, 41, 61)))
           for y in (60, height // 2))
yellow = sum(1 for y in range(min(40, height)) for x in range(width)
             if (lambda r, g, b: r > 150 and g > 150 and r - b > 35)(*at(x, y)))
sys.exit(0 if blue and yellow > 100 else 1)
EOF
}
deadline=$((SECONDS + BOOT_TIMEOUT))
until leitzentrale_up 2>/dev/null; do
    kill -0 "$qemu_pid" 2>/dev/null || { cat "$work/qemu.log" >&2; die "QEMU ended by itself (a crash?)"; }
    (( SECONDS < deadline )) || die "no Leitzentrale after $BOOT_TIMEOUT s; see $shot"
    sleep 5
done
echo "Leitzentrale up after $((SECONDS - deadline + BOOT_TIMEOUT)) s; $SETTLE_TIME s more"
sleep "$SETTLE_TIME"
kill -0 "$qemu_pid" 2>/dev/null || { cat "$work/qemu.log" >&2; die "QEMU ended by itself (a crash?)"; }
leitzentrale_up || die "the Leitzentrale went away; see $shot"
kill "$qemu_pid"
wait "$qemu_pid" || true
qemu_pid=""

state=$(fs_state "$work/overlay.qcow2")
[[ $state == not-clean ]] || die "GENODE* is $state after the boot: Sculpt didn't mount it"
# Fixed in a scratch copy: a stop with the file system mounted can leave what
# e2fsck tidies up (exit 1, as after make-sculpt.sh's first boot); 4 and up
# are errors it can't fix.
qemu-img convert -f qcow2 -O raw "$work/overlay.qcow2" "$work/disk.raw"
e2fsck -fy "$work/disk.raw?offset=$((start * 512))" >"$work/fsck.log" 2>&1 || [[ $? -eq 1 ]] || {
    cat "$work/fsck.log" >&2; die "GENODE* has errors after the boot"; }
grep -q 'FILE SYSTEM WAS MODIFIED' "$work/fsck.log" && tidied=" (e2fsck tidied up after the stop)" || tidied=""
echo "OK: Sculpt booted to the Leitzentrale and mounted GENODE*, which e2fsck finds without errors$tidied"
