# Run after checks.sh when the image is attached as a USB stick.
#
# Finds the config partition the way docs/design/stick-config.md says: on the
# disk the boot volume is on only, as the marker describes it.  Haiku numbers
# a disk's partitions from 0 (/dev/disk/usb/0/0/2 is MBR partition 3).  "rw"
# also checks stick.json, writes a file and reads it back after remounting.
cat >/tmp/bko-config.sh <<'EOF'
m=/boot/.disk/backotter.json
field() { sed -n "s/^ *\"$1\": \"*\([^\",]*\)\"*,*\$/\1/p" $m | head -n 1; }
num=$(field number) sectors=$(field sectors) label=$(field label) serial=$(field fat_serial) image_id=$(field image_id)
root=$(df /boot | sed -n 's/^ *Device: *//p')
case $root in /dev/disk/virtual/*) echo "/boot is on $root"; exit 1 ;; /dev/disk/*/[0-9]*) ;; *) echo "/boot is on '$root'"; exit 1 ;; esac
disk=${root%/*}
part=$disk/$((num - 1))
test -e $part || { echo "no partition $num on $disk (/boot is $root)"; exit 1; }
got=$(od -An -t u4 -j $((446 + 16 * (num - 1) + 12)) -N 4 $disk/raw | tr -d " ")
test "$got" = "$sectors" || { echo "$part: $got sectors, not $sectors"; exit 1; }
bs=$(dd if=$part bs=512 count=1 2>/dev/null | od -An -v -tx1 | tr -d " \n")
test "$(echo $bs | cut -c109-124)" = "$(printf %-8s FAT16 | od -An -tx1 | tr -d " \n")" || { echo "$part: not FAT16"; exit 1; }
test "$(echo $bs | cut -c87-108)" = "$(printf %-11s "$label" | od -An -tx1 | tr -d " \n")" || { echo "$part: label is not $label"; exit 1; }
s=$(echo $bs | cut -c79-86)
got=$(echo $s | sed "s/\(..\)\(..\)\(..\)\(..\)/\4\3-\2\1/" | tr a-f A-F)
test "$got" = "$serial" || { echo "$part: serial is $got, not $serial"; exit 1; }
mnt=/tmp/bko-cfg; mkdir -p $mnt
case ${1:-find} in
find) echo "/boot is on $root; config is $part, $label $serial" ;;
rw)
  mount -t fat $part $mnt || exit 1
  got=$(sed -n "s/^ *\"image_id\": \"\(.*\)\",*\$/\1/p" $mnt/backotter/stick.json)
  test "$got" = "$image_id" || { unmount $mnt; echo "stick.json is for image $got"; exit 1; }
  stamp=$(date -u +%FT%TZ)
  echo "$stamp" >$mnt/backotter/boot-test.txt && sync && unmount $mnt || exit 1
  mount -ro -t fat $part $mnt || exit 1
  test "$(cat $mnt/backotter/boot-test.txt)" = "$stamp"; r=$?
  ls $mnt/backotter | tr "\n" " "; unmount $mnt; exit $r ;;
settings)
  mount -ro -t fat $part $mnt || exit 1
  f=$mnt/backotter/settings.toml
  grep -qx 'format = "backotter-settings"' $f && grep -qx "schema = 1" $f; r=$?
  test $r -eq 0 && echo "format backotter-settings, schema 1"
  unmount $mnt; exit $r ;;
esac
EOF
check config-partition 'bash /tmp/bko-config.sh find'
check config-rw 'bash /tmp/bko-config.sh rw'
check config-settings 'bash /tmp/bko-config.sh settings'
