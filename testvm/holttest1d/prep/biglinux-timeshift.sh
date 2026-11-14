# HT_SOURCE_PREP for biglinux (ht1.sh): two more Timeshift snapshots, in
# btrfs mode, of a root that changes between them, beside the one
# big-first-boot took at the guest's first boot (and any its updates added),
# so that every run's source has Timeshift snapshots under the top level's
# timeshift-btrfs/snapshots/ for load to leave out with its note
# (docs/design/biglinux.md, decision 3 and BL4), and grub-btrfs's submenu
# lists them.  Offline: nothing is downloaded.
# Run as root in the guest's throwaway overlay.  Its last line is not
# REBOOT: the source isn't rebooted after it.
set -eu
# big-first-boot's hourly cron.d entry runs `timeshift --check`, which takes
# a scheduled snapshot when one is due and deletes the ones over their count
# or without info.json: one mid-run would change the set between this
# script's list and the dump.  Cron is stopped for this boot (the overlay's);
# the cron.d file and timeshift.json stay as they were, so the backup holds
# the schedule unchanged.
for u in cronie.service crond.service cron.service; do
    if systemctl is-active -q "$u"; then systemctl stop "$u"; echo "stopped $u for this boot"; fi
done
cfg=/etc/timeshift/timeshift.json
grep -q '"btrfs_mode" *: *"true"' "$cfg" || { echo "Timeshift isn't in btrfs mode ($cfg)"; exit 1; }
d=/usr/share/bko-snap
mkdir -p "$d"
rnd() { head -c "$(($2 << 20))" /dev/urandom > "$1"; }
rnd "$d/base.bin" 48
for i in $(seq 1 300); do echo "file $i" > "$d/s$i"; done
ln -sf s1 "$d/link"
sync
timeshift --create --scripted --tags O --comments "bko-ht1 1"
# A package-like update: a new file, part of the big one rewritten, a file gone.
sleep 2   # Timeshift names a snapshot by the second it was taken
rnd "$d/pkg-a.bin" 24
dd if=/dev/urandom of="$d/base.bin" bs=1M count=2 seek=10 conv=notrunc status=none
rm "$d/s5"
ln -sf s2 "$d/link"
sync
timeshift --create --scripted --tags O --comments "bko-ht1 2"
# And a change after the last snapshot, so the live root is neither's.
rnd "$d/pkg-b.bin" 16
echo changed >> "$d/s10"
sync
timeshift --list --scripted
btrfs subvolume list -a -p -u -q /
btrfs subvolume get-default /
# grub-btrfsd rewrites /boot/grub/grub-btrfs.cfg when a snapshot appears:
# wait until it names every snapshot (at most a minute), so the dump and the
# backup see the file as it settles.  Not judged here: printed.
n=$(btrfs subvolume list -a / | grep -c 'timeshift-btrfs/snapshots/[^/]*/@$' || true)
for _ in $(seq 30); do
    [ "$(grep -o 'timeshift-btrfs/snapshots/[^/]*/@' /boot/grub/grub-btrfs.cfg 2>/dev/null | sort -u | wc -l)" -ge "$n" ] && break
    sleep 2
done
echo "grub-btrfs.cfg names $(grep -o 'timeshift-btrfs/snapshots/[^/]*/@' /boot/grub/grub-btrfs.cfg 2>/dev/null | sort -u | wc -l) of $n Timeshift snapshots of @"
sync
