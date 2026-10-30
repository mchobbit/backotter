# Run after checks.sh when the image is attached as a USB stick.
#
# Finds the config partition the way docs/design/stick-config.md says: on the
# disk the root file system is on only, as the marker describes it.  "rw" also
# checks stick.json, writes a file and reads it back after remounting.
cat >/tmp/bko-config.sh <<'EOF'
m=/.disk/backotter.json
field() { sed -n "s/^ *\"$1\": \"*\([^\",]*\)\"*,*\$/\1/p" $m | head -n 1; }
num=$(field number) sectors=$(field sectors) label=$(field label) serial=$(field fat_serial) image_id=$(field image_id)
root=$(mount | sed -n "s| on / (.*||p")
rdev=$(stat -L -f %r "$root") || { echo "/ is on $root, which isn't a device"; exit 1; }
disk=
for d in $(sysctl -n kern.disks); do
  for p in /dev/${d}s*; do test -c "$p" && test "$(stat -f %r "$p")" = "$rdev" && disk=$d; done
done
test -n "$disk" || { echo "no disk holds / ($root)"; exit 1; }
part=/dev/${disk}s$num
test -c "$part" || { echo "no slice $num on $disk (/ is $root)"; exit 1; }
got=$(fdisk -s $disk 2>/dev/null | awk -v n="$num:" '$1 == n { print $3 }')
test "$got" = "$sectors" || { echo "$part: $got sectors, not $sectors"; exit 1; }
test "$(fstyp $part 2>/dev/null)" = msdosfs || { echo "$part: not FAT"; exit 1; }
bs=$(dd if=$part bs=512 count=1 2>/dev/null | od -An -v -tx1 | tr -d " \n")
test "$(echo $bs | cut -c87-108)" = "$(printf %-11s "$label" | od -An -tx1 | tr -d " \n")" || { echo "$part: label is not $label"; exit 1; }
s=$(echo $bs | cut -c79-86)
got=$(echo $s | sed "s/\(..\)\(..\)\(..\)\(..\)/\4\3-\2\1/" | tr a-f A-F)
test "$got" = "$serial" || { echo "$part: serial is $got, not $serial"; exit 1; }
mnt=/tmp/bko-cfg; mkdir -p $mnt
case ${1:-find} in
find) echo "/ is on $disk; config is $part, $label $serial" ;;
rw)
  mount_msdos -o noexec,nosuid $part $mnt || exit 1
  got=$(sed -n "s/^ *\"image_id\": \"\(.*\)\",*\$/\1/p" $mnt/backotter/stick.json)
  test "$got" = "$image_id" || { umount $mnt; echo "stick.json is for image $got"; exit 1; }
  stamp=$(date -u +%FT%TZ)
  echo "$stamp" >$mnt/backotter/boot-test.txt && sync && umount $mnt || exit 1
  mount_msdos -o ro $part $mnt || exit 1
  test "$(cat $mnt/backotter/boot-test.txt)" = "$stamp"; r=$?
  ls $mnt/backotter | tr "\n" " "; umount $mnt; exit $r ;;
settings)
  mount_msdos -o ro $part $mnt || exit 1
  f=$mnt/backotter/settings.toml
  grep -qx 'format = "backotter-settings"' $f && grep -qx "schema = 1" $f; r=$?
  test $r -eq 0 && echo "format backotter-settings, schema 1"
  umount $mnt; exit $r ;;
esac
EOF
check config-partition 'sh /tmp/bko-config.sh find'
check config-rw 'sh /tmp/bko-config.sh rw'
check config-settings 'sh /tmp/bko-config.sh settings'
