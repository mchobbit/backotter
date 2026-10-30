# Run after checks.sh when the image is attached as a USB stick (a CD has no
# partitions).
#
# Finds the config partition the way docs/design/stick-config.md says: on the
# disk casper mounted at /cdrom only, as the marker describes it.  "rw" also
# checks stick.json, writes a file and reads it back after remounting.
cat >/run/bko-config.sh <<'EOF'
src=$(findmnt -n -o SOURCE /cdrom) || { echo "nothing mounted on /cdrom"; exit 1; }
parent=$(lsblk -no PKNAME "$src" | head -n 1)
disk=/dev/${parent:-${src#/dev/}}
eval "$(python3 -c 'import json; m = json.load(open("/cdrom/.disk/backotter.json")); p = m["config_partition"]; print("num=%s sectors=%s label=%s serial=%s image_id=%s" % (p["number"], p["sectors"], p["label"], p["fat_serial"], m["image_id"]))')"
part=
for f in /sys/class/block/${disk#/dev/}/*/partition; do
  test "$(cat "$f")" = "$num" && part=/dev/$(basename "$(dirname "$f")")
done
test -n "$part" || { echo "no partition $num on $disk (/cdrom is $src)"; exit 1; }
test "$(cat /sys/class/block/${part#/dev/}/size)" = "$sectors" || { echo "$part: not $sectors sectors"; exit 1; }
test "$(blkid -o value -s TYPE "$part")" = vfat || { echo "$part: not FAT"; exit 1; }
test "$(blkid -o value -s LABEL "$part")" = "$label" || { echo "$part: label is not $label"; exit 1; }
test "$(blkid -o value -s UUID "$part")" = "$serial" || { echo "$part: serial is not $serial"; exit 1; }
case ${1:-find} in
find) echo "/cdrom is $src; config is $part, $label $serial" ;;
rw)
  mnt=/run/bko-cfg; mkdir -p $mnt
  mount -o flush,nosuid,nodev,noexec "$part" $mnt || exit 1
  got=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["image_id"])' $mnt/backotter/stick.json)
  test "$got" = "$image_id" || { umount $mnt; echo "stick.json is for image $got"; exit 1; }
  stamp=$(date -u +%FT%TZ)
  echo "$stamp" >$mnt/backotter/boot-test.txt && sync && umount $mnt || exit 1
  mount -o ro "$part" $mnt || exit 1
  test "$(cat $mnt/backotter/boot-test.txt)" = "$stamp"; r=$?
  ls $mnt/backotter | tr "\n" " "; umount $mnt; exit $r ;;
settings)
  mnt=/run/bko-cfg; mkdir -p $mnt
  mount -o ro "$part" $mnt || exit 1
  python3 - $mnt/backotter/settings.toml <<'PY'; r=$?
import sys, tomllib
raw = open(sys.argv[1], "rb").read().decode()
s = tomllib.loads(raw)
assert (s["format"], s["schema"]) == ("backotter-settings", 1), s
# What the comments tell a user to do, in an editor that adds a byte-order mark.
edited = "\n".join(l[2:] if l.startswith(("# name", "# [machines", "# repository_url")) else l
                   for l in raw.splitlines())
e = tomllib.loads(("﻿" + edited).encode().decode("utf-8-sig"))
assert e["name"] == "drawer stick" and len(e["machines"]) == 1, e
print("parses as shipped and as edited per its comments")
PY
  umount $mnt; exit $r ;;
esac
EOF
check config-partition 'sh /run/bko-config.sh find'
check config-rw 'sh /run/bko-config.sh rw'
check config-settings 'sh /run/bko-config.sh settings'
