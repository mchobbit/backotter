#!/usr/bin/env bash
# Make BackOtter's rescue image from the stock Ubuntu Server live ISO.
#
# Usage: ./make-img.sh [--test] SOURCE_ISO SLIM_DIR LAYER_DIR [OUT_IMG]
#
# OUT_IMG defaults to backotter-live-linux-VERSION-ARCH.img in the current
# directory, or in OUT_IMG if that's a directory.  BackOtter has no releases
# yet, so VERSION is dev.DATE.COMMIT: the UTC build date and the commit stick/
# is at, plus .dirty if stick/ has uncommitted changes.  ARCH is SOURCE_ISO's.
#
# SLIM_DIR is slim-base-layer.sh's output and LAYER_DIR build-backotter-layer.sh's,
# both built from SOURCE_ISO.  The result is meant to be written raw to a USB
# stick, hence .img, but inside it is still a hybrid ISO9660 image like the
# stock one, so it also boots as a CD (a BMC's virtual media, say).  Compared
# with the stock ISO, the result:
#   - replaces casper/ubuntu-server-minimal.squashfs with the slimmed base
#   - adds casper/ubuntu-server-minimal.backotter.squashfs on top of it
#   - drops the stock ubuntu-server and installer layers (their copies of
#     /var/lib/dpkg/status don't match the slimmed base, and Subiquity needs
#     snapd), pool/ and dists/ (packages for offline installs),
#     install-sources.yaml (the installer's list of layers), and the stock
#     base's manifest.full (slim-base-layer.sh writes a new .manifest)
#   - gets a GRUB menu with BackOtter entries only: normal, safe graphics,
#     and serial console (console=ttyS0, for IPMI serial-over-LAN and tests)
#   - adds /.disk/backotter.json, the image marker, and a third partition:
#     32 MiB of FAT16 labelled BACKOTTER, the stick's writable config
#     partition (see docs/design/stick-config.md)
# The boot setup (BIOS and UEFI El Torito images, hybrid MBR/GPT, the appended
# EFI partition) is replayed from SOURCE_ISO unchanged, so the signed shim and
# GRUB still boot with Secure Boot on.
#
# --test makes the serial-console entry the default with a 3-second timeout,
# for boot-test.py.
#
# Needs: xorriso 1.5 or later, dosfstools, mtools, squashfs-tools 4.6 or
# later, python3 3.11 or later.

set -euo pipefail

PLATFORM=linux                  # the backotter-live-PLATFORM-... in the name
BASE=ubuntu-server-minimal
LAYER=$BASE.backotter
TIMEOUT=10
DEFAULT=0
CONFIG_PART=3
CONFIG_SECTORS=65536            # 32 MiB; FAT32 would need more than that
CONFIG_LABEL=BACKOTTER
BASIC_DATA_GUID=ebd0a0a2-b9e5-4433-87c0-68b6b72699c7   # GPT "Microsoft basic data"
SYSTEM_SCHEMAS=1                # system.json schemas the stick can read
PATH=$PATH:/usr/sbin:/sbin      # mkfs.vfat, for users whose PATH lacks sbin

die() { echo "make-img: $*" >&2; exit 1; }

if [[ ${1:-} == --test ]]; then
    TIMEOUT=3
    DEFAULT=2   # the serial-console entry
    shift
fi
[[ $# -eq 3 || $# -eq 4 ]] || die "usage: $0 [--test] SOURCE_ISO SLIM_DIR LAYER_DIR [OUT_IMG]"
SOURCE_ISO=$1 SLIM=$2 LAYER_DIR=$3 OUT_IMG=${4:-.}
for f in "$SOURCE_ISO" "$SLIM/$BASE".{squashfs,manifest,size} "$LAYER_DIR/$LAYER".{squashfs,manifest,size}; do
    [[ -f $f ]] || die "missing $f"
done
for c in xorriso mkfs.vfat mmd mcopy unsquashfs python3; do
    command -v "$c" >/dev/null || die "missing $c"
done

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
xorriso -osirrox on -indev "$SOURCE_ISO" \
    -extract /boot/grub/grub.cfg "$work/grub.cfg.stock" \
    -extract /md5sum.txt "$work/md5sum.txt.stock" \
    -extract /.disk/info "$work/disk-info.stock" 2>/dev/null
chmod u+w "$work"/*

# --- The output name ---------------------------------------------------------------
if [[ -d $OUT_IMG ]]; then
    arch=$(grep -owE 'amd64|arm64' "$work/disk-info.stock" | head -n 1)
    [[ -n $arch ]] || die "can't tell the architecture of $SOURCE_ISO"
    version=dev.$(date -u +%Y%m%d)
    here=$(dirname "$0")
    if commit=$(git -C "$here" rev-parse --short=7 HEAD 2>/dev/null); then
        version+=.$commit
        [[ -z $(git -C "$here" status --porcelain -- . 2>/dev/null) ]] || version+=.dirty
    fi
    OUT_IMG=$OUT_IMG/backotter-live-$PLATFORM-$version-$arch.img
fi
[[ -e $OUT_IMG ]] && die "$OUT_IMG already exists"

# --- The image marker and the config partition -------------------------------------
ubuntu=$(grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' "$work/disk-info.stock" | head -n 1)
kernel=$(awk '$1 ~ /^linux-image-[0-9]/ { sub(/^linux-image-/, "", $1); print $1 }' \
    "$LAYER_DIR/$LAYER.manifest")
zfs=$(awk '$1 == "zfsutils-linux" { sub(/-.*/, "", $2); print $2 }' "$LAYER_DIR/$LAYER.manifest")
[[ -n $ubuntu && -n $kernel && -n $zfs ]] || die "can't tell the Ubuntu, kernel or ZFS version"
# OpenZFS's own list of the features its release supports.
unsquashfs -cat "$LAYER_DIR/$LAYER.squashfs" \
    "usr/share/zfs/compatibility.d/openzfs-${zfs%.*}" >"$work/zfs-features" 2>/dev/null ||
    die "the layer has no ZFS feature list for $zfs"
image_id=$(cat /proc/sys/kernel/random/uuid)
fat_serial=$(od -An -N4 -tx4 /dev/urandom | tr -d ' \n' | tr a-f A-F)

python3 - "$work" "$image_id" "$PLATFORM" "$ubuntu" "$kernel" "$zfs" "$SYSTEM_SCHEMAS" \
    "$CONFIG_PART" "$CONFIG_SECTORS" "$CONFIG_LABEL" "${fat_serial:0:4}-${fat_serial:4}" <<'EOF'
import datetime, json, sys
work, image_id, platform, ubuntu, kernel, zfs, schemas, part, sectors, label, serial = sys.argv[1:]
with open(f"{work}/zfs-features") as f:
    features = sorted(l.strip() for l in f if l.strip() and not l.startswith("#"))
built = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
docs = {
    "backotter.json": {
        "format": "backotter-image", "schema": 1, "image_id": image_id,
        "built": built, "platform": platform, "ubuntu": ubuntu, "kernel": kernel,
        "zfs": {"version": zfs, "features": features},
        "system_schemas": [int(s) for s in schemas.split()],
        "config_partition": {"number": int(part), "sectors": int(sectors),
                             "label": label, "fat_serial": serial},
    },
    "stick.json": {"format": "backotter-stick", "schema": 1,
                   "image_id": image_id, "stick_id": None},
}
for name, doc in docs.items():
    with open(f"{work}/{name}", "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
EOF
cat >"$work/README.txt" <<'EOF'
BackOtter rescue stick: configuration partition

The rest of this stick is the rescue system, which is read-only.  This
partition holds what the stick needs to know about the machines it can
restore.  BackOtter's backup tool updates it whenever the stick is plugged in.

  backotter/settings.toml   yours to edit with any text editor: a name for
                            this stick, and per-machine overrides
  backotter/stick.json      written by BackOtter; don't edit
  backotter/machines/       written by BackOtter; don't edit

No passwords or keys are ever stored here.
EOF
# The settings file people edit.  docs/design/stick-config.md shows the same text.
cat >"$work/settings.toml" <<'EOF'
# BackOtter rescue stick: settings you may edit.
#
# Lines starting with # are comments.  Save as plain text.  BackOtter only
# reads this file and never rewrites it, so your comments stay as you leave
# them.  Eject the stick before unplugging it.

format = "backotter-settings"
schema = 1

# A name for this stick, to tell it apart from others.  Remove the # to use it.
# name = "drawer stick"

# Overrides for one machine.  Copy the two lines below once per machine,
# remove the #s, and put in the machine's install ID: the name of its
# directory under backotter/machines/.
#
# [machines."0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90"]
# repository_url = "sftp://nas.local/backotter/lovelace"
EOF
python3 -c 'import sys, tomllib; tomllib.load(open(sys.argv[1], "rb"))' "$work/settings.toml" ||
    die "settings.toml is not valid TOML"

truncate -s $((CONFIG_SECTORS * 512)) "$work/config.img"
mkfs.vfat -F 16 -n "$CONFIG_LABEL" -i "$fat_serial" "$work/config.img" >/dev/null
export MTOOLS_SKIP_CHECK=1
mmd -i "$work/config.img" ::/backotter ::/backotter/machines
mcopy -i "$work/config.img" "$work/stick.json" "$work/settings.toml" ::/backotter/
mcopy -i "$work/config.img" "$work/README.txt" ::/

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

# md5sum.txt lists every file in the ISO9660 file system: drop the removed
# ones, then set the lines for the replaced and added ones.
grep -vE '^[0-9a-f]{32}  \./(pool/|dists/|casper/install-sources\.yaml$|casper/'"$BASE"'\.manifest\.full$|casper/'"$BASE"'\.ubuntu-server)' \
    "$work/md5sum.txt.stock" >"$work/md5sum.txt"
declare -A files=(
    [./.disk/backotter.json]=$work/backotter.json
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
xorriso -indev "$SOURCE_ISO" -outdev "$OUT_IMG" \
    -rm_r /pool /dists -- \
    -rm /casper/install-sources.yaml "/casper/$BASE.manifest.full" -- \
    -find /casper -name "$BASE.ubuntu-server*" -exec rm -- \
    "${maps[@]}" \
    -map "$work/md5sum.txt" /md5sum.txt \
    -boot_image any replay \
    -append_partition "$CONFIG_PART" "$BASIC_DATA_GUID" "$work/config.img" 2>"$work/xorriso.log" ||
    { cat "$work/xorriso.log" >&2; die "xorriso failed"; }

echo "Wrote $OUT_IMG ($(du -h "$OUT_IMG" | cut -f1)), image $image_id"
