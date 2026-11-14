# HT_SOURCE_PREP for garuda (ht1.sh): snapper snapshots of a root that
# changes between them, beyond the four snap-pac pre/post snapshots the
# guest's image has from its first vm-update (@/.snapshots/1..4), so that a
# round trip restores a longer chain of shared snapshots with real changes
# between them, one of them read-write and written to since
# (docs/design/garuda.md, HoltTest1).  No rollback: Garuda's GRUB boots
# rootflags=subvol=@, so snapper's new default subvolume would never be
# booted.  Offline: nothing is downloaded.
# Run as root in the guest's throwaway overlay.  Its last line is not
# REBOOT: the source isn't rebooted after it.
set -eu
# Garuda's config "root" has NUMBER_CLEANUP=yes, NUMBER_LIMIT=10 and
# NUMBER_MIN_AGE=1800.  4 + 4 snapshots stay under the limit, but a guest
# whose updates made more pairs would lose its oldest to the next cleanup,
# mid-run or mid-backup: the timer is stopped for this boot (the overlay's).
systemctl stop snapper-cleanup.timer
d=/usr/share/bko-snap
mkdir -p "$d"
rnd() { head -c "$(($2 << 20))" /dev/urandom > "$1"; }
rnd "$d/base.bin" 48
for i in $(seq 1 300); do echo "file $i" > "$d/s$i"; done
ln -sf s1 "$d/link"
snapper -c root create -d "bko-holt 1" --cleanup-algorithm number
# A package-like update: a new file, part of the big one rewritten, a file gone.
rnd "$d/pkg-a.bin" 24
dd if=/dev/urandom of="$d/base.bin" bs=1M count=2 seek=10 conv=notrunc status=none
rm "$d/s5"
ln -sf s2 "$d/link"
snapper -c root create -d "bko-holt 2" --cleanup-algorithm number
rnd "$d/pkg-b.bin" 16
echo changed >> "$d/s10"
# A read-write snapshot (snapper's are read-only unless asked), then written
# to itself, so its tree is neither the root's nor its neighbours'.
n=$(snapper -c root create -d "bko-holt 3" --cleanup-algorithm number --read-write --print-number)
rw=/.snapshots/$n/snapshot
btrfs property get -ts "$rw" ro | grep -qx 'ro=false' || { echo "snapshot $n isn't read-write"; exit 1; }
echo "written in snapshot $n" > "$rw$d/rw-only"
rm "$d/pkg-a.bin"
rnd "$d/pkg-c.bin" 8
snapper -c root create -d "bko-holt 4" --cleanup-algorithm number
snapper -c root list
btrfs subvolume list -a -p -u -q /
sync
