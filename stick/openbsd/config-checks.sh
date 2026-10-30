# Run after checks.sh when the image is attached as a USB stick.
#
# Finds the config partition the way docs/design/stick-config.md says, on
# OpenBSD: the disk holding the root file system only, partition N of the
# marker being MBR slot N-1, which OpenBSD reaches through the disklabel
# partition at the same offset.  OpenBSD's base has no Python: the marker and
# stick.json are read with sed, the FAT boot sector with dd and od.  "rw"
# (mount_msdos has no -o sync: sync, then unmount) also checks stick.json, writes a file and reads it back after remounting;
# "settings" only checks the format and schema lines (no TOML parser here).
cat >/tmp/bko-config.sh <<'EOF'
mode=${1:-find}
src=$(mount | awk '$3 == "/" { print $1 }')
case $src in
/dev/*) disk=${src#/dev/}; disk=${disk%?} ;;
*) duid=${src%.?}; disk=$(sysctl -n hw.disknames | tr , '\n' | sed -n "s/:$duid\$//p") ;;
esac
test -n "$disk" || { echo "can't tell the boot disk from $src"; exit 1; }
m=/.disk/backotter.json
val() { sed -n "s/^ *\"$1\": \"*\([^\",]*\)\"*,*\$/\1/p" $m | head -n 1; }
num=$(val number) sectors=$(val sectors) label=$(val label) serial=$(val fat_serial) image_id=$(val image_id)
slot=$((num - 1))
set -- $(fdisk $disk | sed -n "s/^[ *]*$slot: \([0-9A-F][0-9A-F]\) .*\[ *\([0-9]*\): *\([0-9]*\) ].*/\1 \2 \3/p")
test $# -eq 3 || { echo "no partition $num (MBR slot $slot) on $disk"; exit 1; }
id=$1
test "$3" = "$sectors" || { echo "$disk slot $slot: $3 sectors, not $sectors"; exit 1; }
p=$(disklabel $disk | awk -v o=$2 -v n=$3 '$2 == n && $3 == o && $4 == "MSDOS" { sub(":", "", $1); print $1 }')
test -n "$p" || { echo "$disk's disklabel has no MSDOS partition at slot $slot's place"; exit 1; }
part=/dev/$disk$p
dd if=/dev/r$disk$p of=/tmp/bko-bs bs=512 count=1 2>/dev/null
test "$(dd if=/tmp/bko-bs bs=1 skip=54 count=5 2>/dev/null)" = FAT16 || { echo "$part: not FAT16"; exit 1; }
test "$(dd if=/tmp/bko-bs bs=1 skip=43 count=11 2>/dev/null | sed 's/ *$//')" = "$label" || { echo "$part: label is not $label"; exit 1; }
test "$(od -An -tx1 -j39 -N4 /tmp/bko-bs | awk '{ print toupper($4 $3 "-" $2 $1) }')" = "$serial" || { echo "$part: serial is not $serial"; exit 1; }
mnt=/tmp/bko-cfg; mkdir -p $mnt
case $mode in
find) echo "/ is $src; config is $part (MBR id $id), $label $serial" ;;
rw)
  mount -t msdos -o nosuid,nodev,noexec $part $mnt || exit 1
  got=$(sed -n 's/^ *"image_id": "\([^"]*\)".*/\1/p' $mnt/backotter/stick.json)
  test "$got" = "$image_id" || { umount $mnt; echo "stick.json is for image $got"; exit 1; }
  stamp=$(date -u +%FT%TZ)
  echo "$stamp" >$mnt/backotter/boot-test.txt && sync && umount $mnt || exit 1
  mount -t msdos -o ro $part $mnt || exit 1
  test "$(cat $mnt/backotter/boot-test.txt)" = "$stamp"; r=$?
  ls $mnt/backotter | tr "\n" " "; umount $mnt; exit $r ;;
settings)
  mount -t msdos -o ro $part $mnt || exit 1
  grep -x 'format = "backotter-settings"' $mnt/backotter/settings.toml && grep -x 'schema = 1' $mnt/backotter/settings.toml; r=$?
  umount $mnt; exit $r ;;
esac
EOF
check config-partition 'sh /tmp/bko-config.sh find'
check config-rw 'sh /tmp/bko-config.sh rw'
check config-settings 'sh /tmp/bko-config.sh settings'
