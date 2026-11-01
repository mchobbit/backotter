#!/usr/bin/env bash
# Build a Sculpt OS test guest for BackOtter from Genode Labs' official disk
# image, under QEMU with KVM.  Sculpt has no installer: installing it is
# writing its image to the disk and growing its GENODE partition to the rest
# of the disk, which its storage dialog does with "Expand" and "Default".
# This does the same here, offline (sgdisk, resize2fs), then boots the guest
# once and checks that Sculpt used the partition.
#
# Usage: ./make-sculpt.sh RELEASE WORK_DIR
#   e.g. ./make-sculpt.sh 26.10 vms/sculpt2610
#
# RELEASE is a release on https://genode.org/download/sculpt (YY.MM).  Its
# sculpt-YY-MM.img and .img.asc are downloaded into WORK_DIR from
# genode.org/files/sculpt unless already there, with the Genode Labs depot
# key (repos/gems/sculpt/depot/genodelabs/pubkey at Genode's sculpt-RELEASE
# tag on Codeberg), and the image is checked against the signature with that
# key, whose fingerprint is below.
# Results, in WORK_DIR (YYMM is the release without its dot, e.g. 2610):
#   sculptYYMM.qcow2      the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" after its first boot
#   first-boot-serial.log the guest's first boot: the serial port (empty:
#                         Sculpt logs to its own log view, not there)
#   first-boot-screen.png the screen at the end of the first boot
#   vm-run-options        "--q35 --disk-bus sata --nic e1000", how vm-run
#                         must boot it
#
# The guest: the image's GPT, its last partition grown to the end of the
# disk: 1, "BIOSBOOT" (50 KiB, no file system: GRUB's BIOS core), 2, "GRUB2",
# the EFI system partition (2.5 MiB, FAT: GRUB for UEFI), and 3, "GENODE*",
# ext2 over the rest, which holds Sculpt itself (/boot) and everything it
# keeps (depot/, config/RELEASE/).  The "*" makes Sculpt use it at boot
# without asking.  No swap: Genode never pages to disk.  BIOS (SeaBIOS) on
# q35, as Genode's own tests run it: QEMU's older PC machine is too old for
# Sculpt, and with OVMF (Chopin's, Debian 13) Sculpt 26.10 never gets past
# GRUB's splash screen.  The disk is on AHCI and the NIC an e1000: Sculpt has
# no virtio drivers.  2 GB RAM (it needs 1 GB).  Sculpt is graphical only: no
# login on the serial port, no sshd, no host name and no root, so there's no
# id_ed25519 or ssh-user, and vm-update can't update it (a new release is a
# new guest; don't pass it to vm-update).
# To see and use it, give vm-run a display after "--", e.g.
#   vm-run $(<WORK_DIR/vm-run-options) --serial-log /dev/null WORK_DIR/sculpt2610.qcow2 -- -vnc 127.0.0.1:1
# vm-mount can't open it (it looks for /etc/fstab); e2fsprogs and debugfs
# read partition 3 of a raw copy as FILE?offset=BYTES.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, sgdisk, e2fsprogs, gpg,
# curl, python3.  Runs as an ordinary user in the kvm group.

set -euo pipefail

DISK_SIZE=16G
FIRST_BOOT_TIMEOUT=900          # seconds for Sculpt to use GENODE*
SETTLE_TIME=60                  # seconds it runs on after that, for its writes
SIGNING_KEY=6566131D7AD813BA544BA91BDA439C67842B4F25    # Genode Labs Depot 17.05 <depot@genode.org>
GENODE_PART=3                   # the image's GENODE partition; GRUB's grub.cfg loads from it

here=$(dirname "$(realpath "$0")")
PATH=$PATH:/sbin:/usr/sbin
die() { echo "make-sculpt: $*" >&2; exit 1; }

[[ $# -eq 2 ]] || die "usage: $0 RELEASE WORK_DIR"
release=$1
[[ $release =~ ^[0-9][0-9]\.[0-9][0-9]$ ]] || die "RELEASE looks like 26.10, not '$release'"
IMAGE=sculpt-${release/./-}.img
url=https://genode.org/files/sculpt
key_url=https://codeberg.org/genodelabs/genode/raw/tag/sculpt-$release/repos/gems/sculpt/depot/genodelabs/pubkey
guest_name=sculpt${release/./}
DISK=$guest_name.qcow2
out=$2
mkdir -p "$out"
cd "$out"
[[ -e $DISK ]] && die "$out/$DISK already exists; move it away to rebuild"

# --- Fetch and check the image ------------------------------------------------------
fetch() {   # URL FILE
    [[ -f $2 && ! -f $2.part ]] && return
    echo "Downloading $1"
    touch "$2.part"
    curl -fsSL -C - -o "$2" "$1" || die "could not download $1"
    rm "$2.part"
}
fetch "$url/$IMAGE.asc" "$IMAGE.asc"
fetch "$key_url" genodelabs.pub
fetch "$url/$IMAGE" "$IMAGE"

work=$(mktemp -d)
raw=$(mktemp -p . --suffix=.raw disk.XXXXXX)
genode=$(mktemp -p . --suffix=.ext2 genode.XXXXXX)
qemu_pid=""
cleanup() {
    # || true: under set -e, a kill of a process that's already gone would
    # end the trap before the rest.
    [[ -z $qemu_pid ]] || kill "$qemu_pid" 2>/dev/null || true
    rm -rf "$work" "$raw" "$genode"
}
trap cleanup EXIT

# gpgv with only this key: the signature must be good and made by exactly it.
[[ $(gpg --show-keys --with-colons genodelabs.pub | awk -F: '$1 == "fpr" { print $10; exit }') == "$SIGNING_KEY" ]] ||
    die "genodelabs.pub isn't the key $SIGNING_KEY"
gpg --dearmor <genodelabs.pub >"$work/keyring.gpg"
gpgv --keyring "$work/keyring.gpg" --status-fd 1 "$IMAGE.asc" "$IMAGE" 2>/dev/null |
    grep -q "^\[GNUPG:\] VALIDSIG $SIGNING_KEY " || die "$IMAGE does not match its signature"

# --- The disk: the image, its GENODE partition grown to the end -----------------------
part_field() {      # DISK PART FIELD: a field of sgdisk -i, e.g. "First sector"
    sgdisk -i "$2" "$1" | sed -n "s/^$3: \([^ ]*\).*/\1/p"
}
[[ $(part_field "$IMAGE" "$GENODE_PART" "Partition name") == "'GENODE'" ]] ||
    die "$IMAGE's partition $GENODE_PART isn't GENODE"
start=$(part_field "$IMAGE" "$GENODE_PART" "First sector")
guid=$(part_field "$IMAGE" "$GENODE_PART" "Partition unique GUID")
image_sectors=$(part_field "$IMAGE" "$GENODE_PART" "Partition size")
# The image up to the GENODE partition (its MBR, primary GPT, BIOS boot and
# EFI system partitions); the partition's file system goes in once grown.
truncate -s "$DISK_SIZE" "$raw"
dd if="$IMAGE" of="$raw" bs=512 count="$start" conv=notrunc,sparse status=none
disk_bytes=$(stat -c %s "$raw")
# Both GPTs whole and the disk its full size.  Sculpt's part_block sees no
# partitions at all on a disk whose backup GPT it can't read.
gpt_ok() {
    [[ $(stat -c %s "$raw") == "$disk_bytes" ]] || die "the disk isn't $DISK_SIZE any more"
    sgdisk -v "$raw" 2>&1 | grep -q "^No problems found" || { sgdisk -v "$raw" >&2; die "the disk's GPT has problems"; }
}
# -e writes the backup GPT at the disk's end; -a 1 keeps the partition's
# start exactly where it was (sgdisk would align it to 1 MiB).  The partition
# keeps its number (grub.cfg loads from it), its type and its GUID.  No -q:
# with it, sgdisk 1.0.10 changes nothing and still succeeds.
sgdisk -e -a 1 -d "$GENODE_PART" \
    -n "$GENODE_PART:$start:0" -t "$GENODE_PART:8300" -u "$GENODE_PART:$guid" \
    -c "$GENODE_PART:GENODE*" "$raw" >"$work/sgdisk.log" 2>&1 || { cat "$work/sgdisk.log" >&2; die "sgdisk failed"; }
[[ $(part_field "$raw" "$GENODE_PART" "First sector") == "$start" ]] || die "partition $GENODE_PART moved"
[[ $(part_field "$raw" "$GENODE_PART" "Last sector") == "$(sgdisk -p "$raw" | sed -n 's/.*last usable sector is \([0-9]*\).*/\1/p')" ]] ||
    die "partition $GENODE_PART doesn't reach the end of the disk"
[[ $(part_field "$raw" "$GENODE_PART" "Partition name") == "'GENODE*'" ]] || die "partition $GENODE_PART isn't named GENODE*"
sectors=$(part_field "$raw" "$GENODE_PART" "Partition size")
gpt_ok
# The file system grown in a file of its own: resize2fs on the disk itself
# (FILE?offset=) truncates the file at the file system's size counted from
# the file's start, which cuts off the backup GPT and the file system's own
# last blocks.  Written in sparse, over the partition's zeros.
dd if="$IMAGE" of="$genode" bs=512 skip="$start" count="$image_sectors" conv=sparse status=none
truncate -s $((sectors * 512)) "$genode"
e2fsck -fy "$genode" >"$work/fsck.log" 2>&1 || [[ $? -eq 1 ]] || { cat "$work/fsck.log" >&2; die "the image's file system has errors"; }
resize2fs "$genode" >"$work/resize.log" 2>&1 || { cat "$work/resize.log" >&2; die "resize2fs failed"; }
e2fsck -fn "$genode" >"$work/fsck.log" 2>&1 || { cat "$work/fsck.log" >&2; die "the grown file system has errors"; }
dd if="$genode" of="$raw" bs=1M seek=$((start * 512)) oflag=seek_bytes conv=notrunc,sparse status=none
rm -f "$genode"
gpt_ok
fs="$raw?offset=$((start * 512))"
e2fsck -fn "$fs" >"$work/fsck.log" 2>&1 || { cat "$work/fsck.log" >&2; die "the file system on the disk has errors"; }
echo "Disk: $(sgdisk -p "$raw" | grep -c '^ *[0-9]') partitions, GENODE* is $((sectors / 2048)) MiB"

# --- First boot ----------------------------------------------------------------------
# Sculpt copies its depot users into depot/ when it uses the partition: that's
# the sign it booted, found the disk and wrote to it.  It can't be powered off
# from outside (only from its own menu, with ACPI support switched on there),
# so once depot/ is on the disk (read here while it runs) it gets SETTLE_TIME
# more, and is then stopped and its file system checked here.  -no-reboot: a
# crash ends QEMU instead of booting again.  The monitor is for the
# screenshot.
echo "--q35 --disk-bus sata --nic e1000" >vm-run-options
depot_users() {
    debugfs "$@" -R "ls -p /depot" "$fs" 2>/dev/null |
        awk -F/ '$6 != "." && $6 != ".." && $6 != "" { print $6 }' | tr '\n' ' '
}
port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
echo "First boot, until Sculpt has written depot/ to GENODE* (at most $FIRST_BOOT_TIMEOUT s)"
rm -f first-boot-screen.png
# shellcheck disable=SC2046
SSH_PORT=$port "$here/vm-run" $(<vm-run-options) --serial-log first-boot-serial.log "$raw" -- \
    -monitor "unix:$work/mon.sock,server,nowait" -no-reboot &
qemu_pid=$!
shot() { python3 "$here/haiku/drive.py" "$work/mon.sock" shot first-boot-screen.png || die "no screenshot of the first boot"; }
deadline=$((SECONDS + FIRST_BOOT_TIMEOUT))
until [[ -n $(depot_users -c) ]]; do      # -c: read only, as the disk changes under it
    kill -0 "$qemu_pid" 2>/dev/null || die "the first boot ended by itself (a crash?); see $out/first-boot-serial.log"
    (( SECONDS < deadline )) || { shot; die "Sculpt didn't use its partition (no /depot); see $out/first-boot-screen.png"; }
    sleep 15
done
echo "depot/ is there after $((SECONDS - deadline + FIRST_BOOT_TIMEOUT)) s; $SETTLE_TIME s more"
sleep "$SETTLE_TIME"
kill -0 "$qemu_pid" 2>/dev/null || die "the first boot ended by itself (a crash?); see $out/first-boot-serial.log"
shot
kill "$qemu_pid"
wait "$qemu_pid" || true
qemu_pid=""

e2fsck -fy "$fs" >"$work/fsck.log" 2>&1 || [[ $? -eq 1 ]] || { cat "$work/fsck.log" >&2; die "the first boot left file system errors e2fsck can't fix"; }
grep -q 'FILE SYSTEM WAS MODIFIED' "$work/fsck.log" && echo "e2fsck tidied up after the stopped boot"
gpt_ok
depot=$(depot_users)
[[ -n $depot ]] || die "depot/ is gone after e2fsck; see $out/first-boot-screen.png"
echo "Sculpt used GENODE*: /depot holds $depot"

qemu-img convert -f raw -O qcow2 "$raw" "$DISK"
rm -f "$raw"
qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $out/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\""
