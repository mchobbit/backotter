# HT_SOURCE_PREP for anduinos2 (ht1.sh): the source's oldest shared snapshot,
# the System deployment snapshot of @root (anduinos-btrfs-snapshots-manager's
# deployments/UUID/root, read-only, ID 262), written after the newer Home
# snapshot (personal/snapshots/UUID/home, ID 263): made writable, one file
# added, made read-only again.  Its last-written generation is then past the
# Home snapshot's, which is what renumbered BackShot2's
# anduinos2-b6082ebacc9aac0c (262 -> 264) when load made shared snapshots in
# last-written order across families.  The round trip must keep both IDs.
# Run as root in the guest's throwaway overlay.
set -eu
top=$(findmnt -n -o TARGET -T /.snapshots)
dep=$(ls -d "$top"/anduinos-btrfs-snapshots-manager/deployments/*/root | head -1)
home=$(ls -d "$top"/anduinos-btrfs-snapshots-manager/personal/snapshots/*/home | head -1)
gen() { btrfs subvolume show "$1" | awk '$1 == "Generation:" { print $2 }'; }
echo "before: deployment gen $(gen "$dep"), home gen $(gen "$home")"
btrfs property set -ts "$dep" ro false
echo "written after the Home snapshot (HT_SOURCE_PREP anduinos-older-snapshot.sh)" > "$dep/etc/bko-holt-older-snapshot"
sync
btrfs property set -ts "$dep" ro true
btrfs filesystem sync "$top"
a=$(gen "$dep"); b=$(gen "$home")
echo "after: deployment gen $a, home gen $b"
[ "$a" -gt "$b" ] || { echo "the deployment snapshot's generation ($a) isn't past the Home snapshot's ($b)" >&2; exit 1; }
