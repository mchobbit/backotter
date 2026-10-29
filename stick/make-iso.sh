#!/usr/bin/env bash
# Make BackOtter's rescue ISO from the stock Ubuntu Server live ISO.
#
# Usage: ./make-iso.sh [--test] SOURCE_ISO SLIM_DIR LAYER_DIR OUT_ISO
#
# SLIM_DIR is slim-base-layer.sh's output and LAYER_DIR build-backotter-layer.sh's,
# both built from SOURCE_ISO.  Compared with the stock ISO, the result:
#   - replaces casper/ubuntu-server-minimal.squashfs with the slimmed base
#   - adds casper/ubuntu-server-minimal.backotter.squashfs on top of it
#   - drops the stock ubuntu-server and installer layers (their copies of
#     /var/lib/dpkg/status don't match the slimmed base, and Subiquity needs
#     snapd), pool/ and dists/ (packages for offline installs),
#     install-sources.yaml (the installer's list of layers), and the stock
#     base's manifest.full (slim-base-layer.sh writes a new .manifest)
#   - gets a GRUB menu with BackOtter entries only: normal, safe graphics,
#     and serial console (console=ttyS0, for IPMI serial-over-LAN and tests)
# The boot setup (BIOS and UEFI El Torito images, hybrid MBR/GPT, the appended
# EFI partition) is replayed from SOURCE_ISO unchanged, so the signed shim and
# GRUB still boot with Secure Boot on.
#
# --test makes the serial-console entry the default with a 3-second timeout,
# for boot-test.py.
#
# Needs: xorriso 1.5 or later.

set -euo pipefail

BASE=ubuntu-server-minimal
LAYER=$BASE.backotter
TIMEOUT=10
DEFAULT=0

die() { echo "make-iso: $*" >&2; exit 1; }

if [[ ${1:-} == --test ]]; then
    TIMEOUT=3
    DEFAULT=2   # the serial-console entry
    shift
fi
[[ $# -eq 4 ]] || die "usage: $0 [--test] SOURCE_ISO SLIM_DIR LAYER_DIR OUT_ISO"
SOURCE_ISO=$1 SLIM=$2 LAYER_DIR=$3 OUT_ISO=$4
for f in "$SOURCE_ISO" "$SLIM/$BASE".{squashfs,manifest,size} "$LAYER_DIR/$LAYER".{squashfs,manifest,size}; do
    [[ -f $f ]] || die "missing $f"
done
[[ -e $OUT_ISO ]] && die "$OUT_ISO already exists"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
xorriso -osirrox on -indev "$SOURCE_ISO" \
    -extract /boot/grub/grub.cfg "$work/grub.cfg.stock" \
    -extract /md5sum.txt "$work/md5sum.txt.stock" 2>/dev/null
chmod u+w "$work"/*

entry() {   # entry TITLE EXTRA_KERNEL_ARGS
    cat <<EOF
menuentry "$1" {
    set gfxpayload=keep
    linux  /casper/vmlinuz layerfs-path=$LAYER.squashfs hostname=backotter $2 ---
    initrd /casper/initrd
}
EOF
}
{
    cat <<EOF
set timeout=$TIMEOUT
set default=$DEFAULT

loadfont unicode

set menu_color_normal=white/black
set menu_color_highlight=black/light-gray

EOF
    entry "BackOtter rescue" ""
    entry "BackOtter rescue (safe graphics)" "nomodeset"
    entry "BackOtter rescue (serial console ttyS0)" "console=tty0 console=ttyS0,115200n8"
    # The stock menu's firmware entries (UEFI only).
    sed -n '/^grub_platform/,$p' "$work/grub.cfg.stock"
} >"$work/grub.cfg"

# md5sum.txt lists every file on the ISO: drop the removed ones, then set the
# lines for the replaced and added ones.
grep -vE '^[0-9a-f]{32}  \./(pool/|dists/|casper/install-sources\.yaml$|casper/'"$BASE"'\.manifest\.full$|casper/'"$BASE"'\.ubuntu-server)' \
    "$work/md5sum.txt.stock" >"$work/md5sum.txt"
declare -A files=(
    [./boot/grub/grub.cfg]=$work/grub.cfg
    [./casper/$BASE.squashfs]=$SLIM/$BASE.squashfs
    [./casper/$BASE.manifest]=$SLIM/$BASE.manifest
    [./casper/$BASE.size]=$SLIM/$BASE.size
    [./casper/$LAYER.squashfs]=$LAYER_DIR/$LAYER.squashfs
    [./casper/$LAYER.manifest]=$LAYER_DIR/$LAYER.manifest
    [./casper/$LAYER.size]=$LAYER_DIR/$LAYER.size
)
for path in "${!files[@]}"; do
    awk -v p="$path" '$2 != p' "$work/md5sum.txt" >"$work/md5sum.tmp"
    echo "$(md5sum "${files[$path]}" | cut -d' ' -f1)  $path" >>"$work/md5sum.tmp"
    mv "$work/md5sum.tmp" "$work/md5sum.txt"
done
sort -k2 -o "$work/md5sum.txt" "$work/md5sum.txt"

maps=()
for path in "${!files[@]}"; do maps+=(-map "${files[$path]}" "${path#.}"); done
xorriso -indev "$SOURCE_ISO" -outdev "$OUT_ISO" \
    -rm_r /pool /dists -- \
    -rm /casper/install-sources.yaml "/casper/$BASE.manifest.full" -- \
    -find /casper -name "$BASE.ubuntu-server*" -exec rm -- \
    "${maps[@]}" \
    -map "$work/md5sum.txt" /md5sum.txt \
    -boot_image any replay 2>"$work/xorriso.log" ||
    { cat "$work/xorriso.log" >&2; die "xorriso failed"; }

echo "Wrote $OUT_ISO ($(du -h "$OUT_ISO" | cut -f1))"
