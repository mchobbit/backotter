#!/usr/bin/env bash
# Make a test ISO: the stock Ubuntu Server live ISO with its base layer
# replaced by the output of slim-base-layer.sh.
#
# Usage: ./make-test-iso.sh SOURCE_ISO SLIM_DIR OUT_ISO
#
# The new first (default) GRUB entry boots the slimmed base layer with one
# small layer stacked on it, ubuntu-server-minimal.backotter-test.squashfs,
# with the kernel console on both the screen and the first serial port so
# boot-test.py can watch it.  The test layer only logs root in on the serial
# console: the base layer has no live user, because casper creates that user
# with the user-setup package, which lives in the stock installer layer.  It
# also shows that a layer of our own stacks on the slimmed base.
#
# The stock upper layers are left on the ISO but no longer match the base:
# each carries its own copy of /var/lib/dpkg/status listing the removed
# packages, and the installer layer's Subiquity is a snap that needs snapd.
# Their GRUB entries are relabelled to say so.
#
# The ISO's boot setup (BIOS and UEFI El Torito images, hybrid MBR/GPT, the
# appended EFI partition) is replayed from SOURCE_ISO unchanged.
#
# Needs: xorriso 1.5 or later, squashfs-tools.

set -euo pipefail

LAYER=ubuntu-server-minimal
TEST_LAYER=$LAYER.backotter-test

die() { echo "make-test-iso: $*" >&2; exit 1; }

[[ $# -eq 3 ]] || die "usage: $0 SOURCE_ISO SLIM_DIR OUT_ISO"
SOURCE_ISO=$1 SLIM=$2 OUT_ISO=$3
for f in "$SOURCE_ISO" "$SLIM/$LAYER.squashfs" "$SLIM/$LAYER.manifest" "$SLIM/$LAYER.size"; do
    [[ -f $f ]] || die "missing $f"
done
[[ -e $OUT_ISO ]] && die "$OUT_ISO already exists"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
xorriso -osirrox on -indev "$SOURCE_ISO" \
    -extract /boot/grub/grub.cfg "$work/grub.cfg.stock" \
    -extract /md5sum.txt "$work/md5sum.txt.stock" 2>/dev/null
chmod u+w "$work"/*

# The test layer: root logs in automatically on ttyS0.
dropin=$work/layer/etc/systemd/system/serial-getty@ttyS0.service.d
mkdir -p "$dropin"
cat >"$dropin/autologin.conf" <<'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --keep-baud 115200,57600,38400,9600 - $TERM
EOF
# And DHCP on every wired interface.  The base layer has no network config
# (the stock installer layer and cloud-init normally supply it), so without
# this systemd-networkd-wait-online waits out its 2-minute timeout at every
# boot.  "optional" keeps a machine with no cable from stalling the same way.
mkdir -p "$work/layer/etc/netplan"
cat >"$work/layer/etc/netplan/50-backotter-wired.yaml" <<'EOF'
network:
  version: 2
  ethernets:
    wired:
      match:
        name: "e*"
      dhcp4: true
      dhcp6: true
      optional: true
EOF
chmod 600 "$work/layer/etc/netplan/50-backotter-wired.yaml"
mksquashfs "$work/layer" "$work/$TEST_LAYER.squashfs" -all-root -comp xz \
    -noappend -no-progress >/dev/null

{
    cat <<EOF
set timeout=5

loadfont unicode

set menu_color_normal=white/black
set menu_color_highlight=black/light-gray

menuentry "BackOtter test: slimmed base layer + test layer" {
    set gfxpayload=keep
    linux  /casper/vmlinuz layerfs-path=$TEST_LAYER.squashfs console=tty0 console=ttyS0,115200n8 ---
    initrd /casper/initrd
}
EOF
    # The stock menu minus its header, with the stale entries relabelled.
    sed -e '1,/^set menu_color_highlight/d' \
        -e 's/^menuentry "\([^"]*\)"/menuentry "\1 (stock layers, broken by slimming)"/' \
        "$work/grub.cfg.stock"
} >"$work/grub.cfg"

# md5sum.txt lists every file; refresh the lines for the ones replaced.
declare -A replaced=(
    [./boot/grub/grub.cfg]=$work/grub.cfg
    [./casper/$LAYER.squashfs]=$SLIM/$LAYER.squashfs
    [./casper/$LAYER.manifest]=$SLIM/$LAYER.manifest
    [./casper/$LAYER.size]=$SLIM/$LAYER.size
)
cp "$work/md5sum.txt.stock" "$work/md5sum.txt"
for path in "${!replaced[@]}"; do
    sum=$(md5sum "${replaced[$path]}" | cut -d' ' -f1)
    sed -i "s|^[0-9a-f]\{32\}  ${path//./\\.}\$|$sum  $path|" "$work/md5sum.txt"
done
echo "$(md5sum "$work/$TEST_LAYER.squashfs" | cut -d' ' -f1)  ./casper/$TEST_LAYER.squashfs" \
    >>"$work/md5sum.txt"

xorriso -indev "$SOURCE_ISO" -outdev "$OUT_ISO" \
    -map "$work/$TEST_LAYER.squashfs" "/casper/$TEST_LAYER.squashfs" \
    -map "$work/grub.cfg" /boot/grub/grub.cfg \
    -map "$work/md5sum.txt" /md5sum.txt \
    -map "$SLIM/$LAYER.squashfs" "/casper/$LAYER.squashfs" \
    -map "$SLIM/$LAYER.manifest" "/casper/$LAYER.manifest" \
    -map "$SLIM/$LAYER.size" "/casper/$LAYER.size" \
    -boot_image any replay 2>"$work/xorriso.log" ||
    { cat "$work/xorriso.log" >&2; die "xorriso failed"; }

echo "Wrote $OUT_ISO ($(du -h "$OUT_ISO" | cut -f1))"
