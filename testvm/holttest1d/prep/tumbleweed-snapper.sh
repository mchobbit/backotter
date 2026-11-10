# HT_SOURCE_PREP for tumbleweed (ht1.sh): snapper snapshots of a root that
# changes between them, then a rollback, so that a round trip restores
# shared snapshots of every kind docs/design/btrfs-snapshots.md plans for:
# a chain, snapper's old root (1, no parent) once a rollback left it
# unmounted, and a live root that is itself a snapshot with the highest ID.
# Run as root in the guest's throwaway overlay.  Its last line is REBOOT:
# the rollback takes effect at the next boot.
set -eu
d=/usr/share/bko-snap
mkdir -p "$d"
rnd() { head -c "$(($2 << 20))" /dev/urandom > "$1"; }
rnd "$d/base.bin" 48
for i in $(seq 1 300); do echo "file $i" > "$d/s$i"; done
ln -sf s1 "$d/link"
snapper create -d "bko-holt 1" --cleanup-algorithm number
# A package-like update: a new file, part of the big one rewritten, a file gone.
rnd "$d/pkg-a.bin" 24
dd if=/dev/urandom of="$d/base.bin" bs=1M count=2 seek=10 conv=notrunc status=none
rm "$d/s5"
ln -sf s2 "$d/link"
snapper create -d "bko-holt 2" --cleanup-algorithm number
rnd "$d/pkg-b.bin" 16
echo changed >> "$d/s10"
snapper create -d "bko-holt 3" --cleanup-algorithm number
rm "$d/pkg-a.bin"
rnd "$d/pkg-c.bin" 8
snapper create -d "bko-holt 4" --cleanup-algorithm number
# Back to the state of "bko-holt 2": snapper makes a read-only snapshot of
# the current root and a read-write one of that one, the default from now.
target=$(snapper --csvout list --columns number,description | awk -F, '$2 == "bko-holt 2" { print $1 }')
snapper --ambit classic rollback "$target"
snapper list
btrfs subvolume list -a -p -u -q /
btrfs subvolume get-default /
sync
echo REBOOT
