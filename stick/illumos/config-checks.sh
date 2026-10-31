# Run after checks.sh when the image is attached as a USB stick.
#
# Finds the config partition the way docs/design/stick-config.md says for
# illumos, whose root is a RAM disk and so on no disk: on the disk whose
# partition N (the marker's number, slice N-1) has the marker's size, label
# and serial, reading only that partition's first sector of each disk, and
# mounting nothing else.  Two sticks written from the same image both match:
# then it refuses rather than pick one.  "rw" also checks stick.json, writes
# a file and reads it back after remounting.
cat >/tmp/bko-config.sh <<'EOF'
PATH=/usr/xpg4/bin:/usr/bin:/usr/sbin:/sbin
m=/.disk/backotter.json
field() { sed -n "s/^ *\"$1\": \"*\([^\",]*\)\"*,*\$/\1/p" $m | head -n 1; }
num=$(field number) sectors=$(field sectors) label=$(field label) serial=$(field fat_serial) image_id=$(field image_id)
s=s$((num - 1))
want=$(printf %-11s "$label" | od -An -tx1 | tr -d " \n")
part=
for d in $(diskinfo -H | awk '{ print $2 }'); do
  got=$(prtvtoc -h /dev/rdsk/${d}p0 2>/dev/null | awk -v n=$((num - 1)) '$1 == n { print $5 }')
  test "$got" = "$sectors" || continue
  bs=$(dd if=/dev/rdsk/${d}$s bs=512 count=1 2>/dev/null | od -An -v -tx1 | tr -d " \n")
  test "$(echo $bs | cut -c87-108)" = "$want" || continue
  x=$(echo $bs | cut -c79-86 | sed "s/\(..\)\(..\)\(..\)\(..\)/\4\3-\2\1/" | tr a-f A-F)
  test "$x" = "$serial" && part="$part $d$s"
done
test -n "$part" || { echo "no disk has a partition $num of $sectors sectors labelled $label, serial $serial"; exit 1; }
test $(echo $part | wc -w) -eq 1 || { echo "several sticks from this image:$part"; exit 1; }
part=$(echo $part)
test "$(fstyp /dev/rdsk/$part 2>/dev/null)" = pcfs || { echo "$part: not FAT"; exit 1; }
mnt=/tmp/bko-cfg; mkdir -p $mnt
case ${1:-find} in
find) echo "config is $part, $label $serial" ;;
rw)
  mount -F pcfs -o nosuid,noexec /dev/dsk/$part $mnt || exit 1
  got=$(sed -n "s/^ *\"image_id\": \"\(.*\)\",*\$/\1/p" $mnt/backotter/stick.json)
  test "$got" = "$image_id" || { umount $mnt; echo "stick.json is for image $got"; exit 1; }
  stamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  echo "$stamp" >$mnt/backotter/boot-test.txt && sync && umount $mnt || exit 1
  mount -F pcfs -o ro /dev/dsk/$part $mnt || exit 1
  test "$(cat $mnt/backotter/boot-test.txt)" = "$stamp"; r=$?
  ls $mnt/backotter | tr "\n" " "; umount $mnt; exit $r ;;
settings)
  mount -F pcfs -o ro /dev/dsk/$part $mnt || exit 1
  f=$mnt/backotter/settings.toml
  grep -qx 'format = "backotter-settings"' $f && grep -qx "schema = 1" $f; r=$?
  test $r -eq 0 && echo "format backotter-settings, schema 1"
  umount $mnt; exit $r ;;
esac
EOF
check config-partition 'sh /tmp/bko-config.sh find'
check config-rw 'sh /tmp/bko-config.sh rw'
check config-settings 'sh /tmp/bko-config.sh settings'
