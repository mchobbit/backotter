# Run after checks.sh when the image is attached as a USB stick.
#
# Finds the config partition the way docs/design/stick-config.md says: on the
# disk the root file system is on only, as the marker describes it.  NetBSD's
# base has no blkid or Python, so the FAT boot sector is read with od and the
# JSON with sed.  "rw" also checks stick.json, writes a file and reads it back
# after remounting; "settings" checks the shipped settings.toml's format and
# schema lines (there's no TOML parser here).
cat >/tmp/bko-config.sh <<'EOF'
m=/.disk/backotter.json
j() { sed -n "s/.*\"$1\": \"*\([^\",]*\)\"*,*$/\1/p" "$2" | head -n 1; }
num=$(j number $m) sectors=$(j sectors $m) label=$(j label $m)
serial=$(j fat_serial $m) image_id=$(j image_id $m)
root=$(sysctl -n kern.root_device)
disk=$(dkctl "$root" getwedgeinfo | sed -n "s/^$root at \([^:]*\):.*/\1/p")
test -n "$disk" || { echo "/ is on $root, not a wedge"; exit 1; }
start=$(gpt -r show -i "$num" "$disk" 2>/dev/null | sed -n "s/^Start: \([0-9]*\).*/\1/p")
test -n "$start" || { echo "no partition $num on $disk (/ is $root)"; exit 1; }
part=$(dkctl "$disk" listwedges | sed -n "s/^\(dk[0-9]*\): .* blocks at $start, .*/\1/p")
test -n "$part" || { echo "no wedge for $disk's partition $num"; exit 1; }
size=$(dkctl "$part" getwedgeinfo | sed -n "s/^$part: \([0-9]*\) blocks at.*/\1/p")
test "$size" = "$sectors" || { echo "$part: $size sectors, not $sectors"; exit 1; }
dd if=/dev/r$part of=/tmp/bko-bs bs=512 count=1 2>/dev/null
b() { od -A n -t x1 -j "$1" -N "$2" /tmp/bko-bs | tr -d " \n" | tr a-f A-F; }
txt() { dd if=/tmp/bko-bs bs=1 skip="$1" count="$2" 2>/dev/null | sed "s/ *$//"; }
test "$(b 38 1)" = 29 && test "$(txt 54 8)" = FAT16 || { echo "$part: not FAT16"; exit 1; }
test "$(txt 43 11)" = "$label" || { echo "$part: label is not $label"; exit 1; }
s=$(b 39 4)
got=$(echo "$s" | sed "s/\(..\)\(..\)\(..\)\(..\)/\4\3-\2\1/")
test "$got" = "$serial" || { echo "$part: serial is $got, not $serial"; exit 1; }
mnt=/tmp/bko-cfg; mkdir -p $mnt
case ${1:-find} in
find) echo "/ is $root on $disk; config is $part, $label $serial" ;;
rw)
  mount -t msdos -o nosuid,nodev,noexec /dev/$part $mnt || exit 1
  got=$(j image_id $mnt/backotter/stick.json)
  test "$got" = "$image_id" || { umount $mnt; echo "stick.json is for image $got"; exit 1; }
  stamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  echo "$stamp" >$mnt/backotter/boot-test.txt && sync && umount $mnt || exit 1
  mount -t msdos -r /dev/$part $mnt || exit 1
  test "$(cat $mnt/backotter/boot-test.txt)" = "$stamp"; r=$?
  ls $mnt/backotter | tr "\n" " "; umount $mnt; exit $r ;;
settings)
  mount -t msdos -r /dev/$part $mnt || exit 1
  f=$mnt/backotter/settings.toml
  grep -q '^format = "backotter-settings"' $f && grep -q '^schema = 1' $f && grep -q '^# name = ' $f
  r=$?; test $r = 0 && echo "format, schema and the name example are there"
  umount $mnt; exit $r ;;
esac
EOF
check config-partition 'sh /tmp/bko-config.sh find'
check config-rw 'sh /tmp/bko-config.sh rw'
check config-settings 'sh /tmp/bko-config.sh settings'
