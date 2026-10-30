# Run after checks.sh (which writes /tmp/bko-json.lua) when the image is
# attached as a USB stick.
#
# Finds the config partition the way docs/design/stick-config.md says: on the
# disk holding the root file system only (its GEOM label's provider, e.g.
# da0s2a, gives the disk), as the marker describes it.  "rw" also checks
# stick.json, writes a file and reads it back after remounting.  "settings"
# can't parse TOML (the base system has no parser), so it checks the file's
# format and schema lines; the Linux image's check parses it.
cat >/tmp/bko-config.sh <<'EOF'
mode=${1:-find}
root=$(mount -p | awk '$2 == "/" { print $1 }')
prov=$(glabel status -s | awk -v l="${root#/dev/}" '$1 == l { print $3 }')
test -n "$prov" || { echo "/ is on $root, which has no GEOM label"; exit 1; }
disk=$(echo "$prov" | sed -E 's/s[0-9]+[a-h]?$//')
set -- $(/usr/libexec/flua /tmp/bko-json.lua /.disk/backotter.json config_partition.number config_partition.sectors config_partition.label config_partition.fat_serial image_id)
num=$1 sectors=$2 label=$3 serial=$4 image_id=$5
part=/dev/${disk}s$num
test -c "$part" || { echo "no slice $num on $disk (/ is $prov)"; exit 1; }
size=$(diskinfo "$part" | awk '{ print $3 }')
test "$size" = $((sectors * 512)) || { echo "$part: $size bytes, not $sectors sectors"; exit 1; }
test "$(fstyp -l "$part")" = "msdosfs $label" || { echo "$part: $(fstyp -l "$part"), not FAT labelled $label"; exit 1; }
got=$(dd if="$part" bs=512 count=1 2>/dev/null | od -An -tx1 -j39 -N4 | awk '{ print toupper($4 $3 "-" $2 $1) }')
test "$got" = "$serial" || { echo "$part: serial $got, not $serial"; exit 1; }
mnt=/tmp/bko-cfg
case $mode in
find) echo "/ is on $prov; config is $part, $label $serial" ;;
rw)
  mkdir -p $mnt
  mount -t msdosfs -o sync,nosuid,noexec "$part" $mnt || exit 1
  got=$(/usr/libexec/flua /tmp/bko-json.lua $mnt/backotter/stick.json image_id)
  test "$got" = "$image_id" || { umount $mnt; echo "stick.json is for image $got"; exit 1; }
  stamp=$(date -u +%FT%TZ)
  echo "$stamp" >$mnt/backotter/boot-test.txt && umount $mnt || exit 1
  mount -t msdosfs -o ro "$part" $mnt || exit 1
  test "$(cat $mnt/backotter/boot-test.txt)" = "$stamp"; r=$?
  ls $mnt/backotter | tr "\n" " "; umount $mnt; exit $r ;;
settings)
  mkdir -p $mnt
  mount -t msdosfs -o ro "$part" $mnt || exit 1
  f=$mnt/backotter/settings.toml
  grep -qx 'format = "backotter-settings"' $f && grep -qx 'schema = 1' $f && test -s $mnt/README.txt; r=$?
  test $r -eq 0 && echo "format backotter-settings, schema 1; README.txt there"
  umount $mnt; exit $r ;;
esac
EOF
check config-partition 'sh /tmp/bko-config.sh find'
check config-rw 'sh /tmp/bko-config.sh rw'
check config-settings 'sh /tmp/bko-config.sh settings'
