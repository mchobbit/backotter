#!/usr/bin/env bash
# Build a slimmed copy of the base layer of Ubuntu Server's live ISO
# (casper/ubuntu-server-minimal.squashfs) for the BackOtter rescue stick.
#
# Usage: sudo ./slim-base-layer.sh [--tiers "1 2 3"] SOURCE_ISO OUT_DIR
#
# It always starts from the untouched Ubuntu ISO, so the same cuts can be
# re-applied to every point release.  OUT_DIR receives:
#   rootfs/                          the slimmed tree
#   ubuntu-server-minimal.squashfs   the rebuilt layer
#   ubuntu-server-minimal.manifest   package list, in the format Ubuntu ships
#   ubuntu-server-minimal.size       unpacked size in bytes
#   report.txt                       what was removed, and the size change
#
# The cuts come in three tiers (see the TIER sections below):
#   1  software a rescue stick never runs: tracing and debug tools plus the
#      LLVM/Clang they drag in, snapd, kernel headers, and what that orphans
#   2  firmware for ARM hardware -- the stick is amd64-only
#   3  firmware for hardware no PC contains: data-centre switch ASICs
#
# IF TIER 3 TURNS OUT TO BE A MISTAKE, rebuild with --tiers "1 2".  That keeps
# the switch firmware and changes nothing else.
#
# Only tier 1 removes packages.  Tiers 2 and 3 delete firmware files and add
# dpkg path-exclude rules for them, the way Ubuntu's own minimized images drop
# documentation: purging the firmware packages instead would also remove the
# linux-firmware metapackage, and apt would then want to download
# linux-firmware-minimal.  The rules stay in the image, so reinstalling or
# upgrading those packages later keeps the same files out.
#
# Needs: Linux, root, xorriso, squashfs-tools 4.6 or later.

set -euo pipefail

TIERS="1 2 3"
LAYER=ubuntu-server-minimal

# --- TIER 1: packages -------------------------------------------------------
# dpkg-query patterns; whatever is installed and matches gets purged.
TIER1_PURGE=(
    bpfcc-tools python3-bpfcc libbpfcc bpftrace bpftool crash   # tracing/debug
    linux-perf 'linux-tools-*'                                  # perf
    'libllvm[0-9]*' 'libclang-cpp[0-9]*' 'libclang1-[0-9]*'     # only for the above
    snapd                                                       # BackOtter uses no snaps
    'linux-headers-*'                                           # nothing on the stick compiles modules
    xauth                                                       # X11 forwarding; the stick has no display server
)
# Metapackages that depend on the purged packages and so go with them.  Any
# other package apt wants to remove stops the build.
TIER1_COLLATERAL=(linux-generic ubuntu-server-minimal)
# Left orphaned by the purge, but useful on a rescue stick: kept.
TIER1_KEEP=(
    openssh-client          # backups and restores over SSH
    hwdata pnp.ids usb.ids  # hardware names for lspci/lsusb and BackOtter's hardware report
)
# Commands that must survive slimming, if the stock layer had them.
REQUIRED_COMMANDS=(python3 lvm mdadm cryptsetup sgdisk sfdisk wipefs blkid lsblk
    mkfs.ext4 mkfs.xfs mkfs.btrfs mkfs.vfat grub-install ssh)

# --- TIER 2: ARM firmware ---------------------------------------------------
# Patterns under /usr/lib/firmware, matched as dpkg does ('*' also matches '/').
TIER2_EXCLUDE=(
    'qcom/*' 'a300_p*.fw.zst'            # Snapdragon SoCs and Adreno GPUs
    'nvidia/tegra*' 'nvidia/gk20a/*' 'nvidia/gm20b/*' 'nvidia/gp10b/*'  # NVIDIA Tegra
    'dpaa2/*'                            # NXP Layerscape networking
    'mediatek/mt81*' 'mediatek/sof/*' 'mediatek/sof-tplg/*'  # MediaTek Chromebook SoCs
    'vpu_[dp].bin.zst'                   # old names for mediatek/mt8173's video firmware
    'mediatek/mt7622*' 'mediatek/mt798*'  # MediaTek router SoCs (built-in Wi-Fi)
    'amlogic/*' 'meson/*' 'rockchip/*' 'imx/*' 'amphion/*' 'cnm/*'   # ARM SoC video and boot blobs
    'arm/*' 'powervr/*'                  # Mali and PowerVR GPUs
    's5p-mfc*' 'rcar_gen4_pcie*' 'r8a779x_usb3_*'  # Samsung Exynos, Renesas R-Car
)
# Exceptions inside qcom/: PCIe devices that do turn up in x86 machines.
TIER2_INCLUDE=(
    'qcom/sdx35/*' 'qcom/sdx61/*'        # 4G/5G modem cards (ThinkPads and others)
    'qcom/aic100/*' 'qcom/qdu100/*'      # Cloud AI 100 and X100 RAN accelerator cards
)

# --- TIER 3: firmware for hardware no PC contains -----------------------------
# Remove this tier (--tiers "1 2") if it turns out to be a mistake.
TIER3_EXCLUDE=(
    'mellanox/mlxsw_spectrum*' 'mellanox/lc_ini_bundle_*'  # Mellanox Spectrum switch ASICs
    'mrvl/prestera/*'                                       # Marvell Prestera switch ASICs
)

# --- Compression -------------------------------------------------------------
# The highest ratio the kernel can read.  The kernel's squashfs mounts xz,
# zstd, lz4, lzo and gzip (not lzma), and its xz decoder has the x86 BCJ filter
# built in.  mksquashfs has no xz level option: it uses liblzma's default
# preset, and presets 6-9 differ only in dictionary size, which squashfs caps
# at the block size.  So 1 MiB blocks (the maximum) with a 1 MiB dictionary is
# exactly what "xz -9" would produce here.  -Xbcj x86 makes mksquashfs try the
# x86 filter on each block and keep whichever result is smaller.
MKSQUASHFS_OPTS=(-comp xz -Xbcj x86 -b 1M -Xdict-size 1M -noappend -no-progress)

die() { echo "slim-base-layer: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case $1 in
        --tiers) TIERS=$2; shift 2 ;;
        -h|--help) sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
[[ $# -eq 2 ]] || die "usage: $0 [--tiers \"1 2 3\"] SOURCE_ISO OUT_DIR"
SOURCE_ISO=$(realpath "$1")
OUT=$2
for t in $TIERS; do [[ $t == [123] ]] || die "unknown tier '$t'"; done
has_tier() { [[ " $TIERS " == *" $1 "* ]]; }

[[ $EUID -eq 0 ]] || die "must run as root"
[[ -f $SOURCE_ISO ]] || die "no such ISO: $SOURCE_ISO"
for c in xorriso unsquashfs mksquashfs; do
    command -v "$c" >/dev/null || die "missing $c"
done
# OUT_DIR is wiped on every run, so only reuse one this script created.
if [[ -e $OUT ]]; then
    [[ -e $OUT/.slim-base-layer ]] || die "$OUT exists and was not made by this script"
    rm -rf "$OUT"
fi
mkdir -p "$OUT"
touch "$OUT/.slim-base-layer"
OUT=$(realpath "$OUT")
ROOT=$OUT/rootfs
FW=$ROOT/usr/lib/firmware
REPORT=$OUT/report.txt
: >"$REPORT"
log() { echo "$*" | tee -a "$REPORT"; }

log "Source ISO: $SOURCE_ISO"
log "Tiers:      $TIERS"

# --- Unpack -------------------------------------------------------------------
xorriso -osirrox on -indev "$SOURCE_ISO" \
    -extract "/casper/$LAYER.squashfs" "$OUT/stock.squashfs" 2>"$OUT/xorriso.log" ||
    die "xorriso failed; see $OUT/xorriso.log"
unsquashfs -q -n -d "$ROOT" "$OUT/stock.squashfs"
stock_squashfs=$(stat -c %s "$OUT/stock.squashfs")
rm "$OUT/stock.squashfs"
stock_size=$(du -sx --block-size=1 "$ROOT" | cut -f1)

in_root() { chroot "$ROOT" "$@"; }
present_commands() {
    local c
    for c in "${REQUIRED_COMMANDS[@]}"; do
        if in_root sh -c "command -v $c" >/dev/null 2>&1; then echo "$c"; fi
    done
}
installed_only() {
    in_root dpkg-query -W -f '${db:Status-Status} ${Package}\n' "$@" 2>/dev/null |
        awk '$1 == "installed" { print $2 }' || true
}
commands_before=$(present_commands)

# --- Chroot setup ---------------------------------------------------------------
mounts=()
cleanup() {
    local i
    for ((i = ${#mounts[@]} - 1; i >= 0; i--)); do umount "${mounts[i]}" || true; done
    rm -f "$ROOT/usr/sbin/policy-rc.d"
}
trap cleanup EXIT
for m in proc sys dev dev/pts; do
    mount --bind "/$m" "$ROOT/$m"
    mounts+=("$ROOT/$m")
done
# Keep maintainer scripts from starting or stopping services.
printf '#!/bin/sh\nexit 101\n' >"$ROOT/usr/sbin/policy-rc.d"
chmod 755 "$ROOT/usr/sbin/policy-rc.d"
export DEBIAN_FRONTEND=noninteractive

# --- TIER 1 ------------------------------------------------------------------------
if has_tier 1; then
    log ""
    log "== Tier 1: packages"
    mapfile -t purge < <(installed_only "${TIER1_PURGE[@]}")
    [[ ${#purge[@]} -gt 0 ]] || die "tier 1: none of the packages are installed"

    # Everything the collateral metapackages pulled in stays, except what is
    # being purged; marking those manual keeps autoremove from taking them.
    mapfile -t meta_deps < <(in_root apt-cache depends --installed --no-suggests \
        --no-conflicts --no-breaks --no-replaces --no-enhances "${TIER1_COLLATERAL[@]}" \
        2>/dev/null | awk '/(Depends|Recommends):/ { gsub(/[<>]/, "", $2); print $2 }' | sort -u)
    keep=()
    for d in "${TIER1_KEEP[@]}" "${meta_deps[@]}"; do
        [[ " ${purge[*]} " == *" $d "* ]] || keep+=("$d")
    done
    # apt-mark rejects the whole list if one name isn't an installed package.
    mapfile -t keep < <(installed_only "${keep[@]}")
    in_root apt-mark manual "${keep[@]}" >/dev/null

    # Check the plan before acting on it.
    plan=$(in_root apt-get -s purge "${purge[@]}")
    if grep -q '^Inst' <<<"$plan"; then
        die "tier 1: apt wants to install packages:"$'\n'"$(grep '^Inst' <<<"$plan")"
    fi
    allowed=" ${purge[*]} ${TIER1_COLLATERAL[*]} "
    while read -r _ pkg _; do
        [[ $allowed == *" $pkg "* ]] || die "tier 1: apt would also remove $pkg"
    done < <(grep -E '^(Purg|Remv)' <<<"$plan")

    sizes() { in_root dpkg-query -W -f '${Installed-Size}\t${Package}\n' "$@" 2>/dev/null || true; }
    removed=$(sizes "${purge[@]}" "${TIER1_COLLATERAL[@]}")
    in_root apt-get -y -q purge "${purge[@]}" >/dev/null

    # Whatever that orphaned (TIER1_KEEP is already marked manual).
    mapfile -t orphans < <(in_root apt-get -s autoremove | awk '/^Remv/ { print $2 }')
    if [[ ${#orphans[@]} -gt 0 ]]; then
        removed+=$'\n'$(sizes "${orphans[@]}")
        in_root apt-get -y -q --purge autoremove >/dev/null
    fi
    in_root apt-get clean

    log "Removed packages (KB, name):"
    sort -rn <<<"$removed" | sed 's/^/  /' | tee -a "$REPORT"
    log "Total: $(awk '{ s += $1 } END { printf "%.1f MB", s / 1024 }' <<<"$removed")"
fi

# --- TIERS 2 and 3 -----------------------------------------------------------------
# '*' must match '/' too, as in dpkg's path-exclude; bash's [[ == ]] does that.
matches_any() {
    local path=$1 pat
    shift
    for pat; do
        # shellcheck disable=SC2053  # unquoted: $pat is a pattern
        [[ $path == $pat ]] && return 0
    done
    return 1
}

# remove_firmware TIER EXCLUDE_ARRAY_NAME INCLUDE_ARRAY_NAME
remove_firmware() {
    local tier=$1
    local -n exclude=$2 include=$3
    local f rel bytes=0 count=0 pat
    while IFS= read -r -d '' f; do
        rel=${f#"$FW"/}
        if matches_any "$rel" "${exclude[@]}" && ! matches_any "$rel" "${include[@]}"; then
            bytes=$((bytes + $(stat -c %s "$f")))
            count=$((count + 1))
            rm "$f"
        fi
    done < <(find "$FW" \( -type f -o -type l \) -print0)
    {
        echo ""
        echo "# Tier $tier"
        for pat in "${exclude[@]}"; do echo "path-exclude=/usr/lib/firmware/$pat"; done
        for pat in "${include[@]}"; do echo "path-include=/usr/lib/firmware/$pat"; done
    } >>"$DPKG_CFG"
    log ""
    log "== Tier $tier: firmware"
    log "Removed $count files, $(awk -v b="$bytes" 'BEGIN { printf "%.1f MB", b / 1048576 }')"
}

DPKG_CFG=$ROOT/etc/dpkg/dpkg.cfg.d/50-backotter-firmware
if has_tier 2 || has_tier 3; then
    cat >"$DPKG_CFG" <<'EOF'
# Written by BackOtter's slim-base-layer.sh.  Keeps firmware the rescue stick
# cannot use out of this system, including on reinstalls and upgrades.
# path-include lines must follow the path-exclude lines they make exceptions to.
EOF
    # Stock linux-firmware already has some dangling symlinks and empty
    # directories; only the ones slimming creates count.
    broken_before=$(find "$FW" -xtype l | sort)
    empty_before=$(find "$FW" -mindepth 1 -type d -empty | sort)
    no_includes=()
    if has_tier 2; then remove_firmware 2 TIER2_EXCLUDE TIER2_INCLUDE; fi
    if has_tier 3; then remove_firmware 3 TIER3_EXCLUDE no_includes; fi
    # A kept file must not be a symlink into something removed.
    broken=$(comm -13 <(echo "$broken_before") <(find "$FW" -xtype l | sort))
    [[ -z $broken ]] || die "firmware symlinks left dangling:"$'\n'"$broken"
    # Repeat until nothing changes: removing a directory can empty its parent.
    while :; do
        removed_dir=no
        while IFS= read -r -d '' d; do
            grep -qxF "$d" <<<"$empty_before" && continue
            rmdir "$d"
            removed_dir=yes
        done < <(find "$FW" -mindepth 1 -type d -empty -print0)
        [[ $removed_dir == yes ]] || break
    done
fi

# --- Checks --------------------------------------------------------------------------
in_root dpkg --audit | grep . && die "dpkg --audit reports problems"
in_root apt-get check -q >/dev/null || die "apt-get check failed"
commands_after=$(present_commands)
lost=$(comm -23 <(sort <<<"$commands_before") <(sort <<<"$commands_after"))
[[ -z $lost ]] || die "slimming removed required commands: $lost"

cleanup
trap - EXIT

# --- Rebuild the layer ---------------------------------------------------------------
in_root dpkg-query -W -f '${binary:Package}\t${Version}\n' >"$OUT/$LAYER.manifest"
slim_size=$(du -sx --block-size=1 "$ROOT" | cut -f1)
echo "$slim_size" >"$OUT/$LAYER.size"
mksquashfs "$ROOT" "$OUT/$LAYER.squashfs" "${MKSQUASHFS_OPTS[@]}" >/dev/null
slim_squashfs=$(stat -c %s "$OUT/$LAYER.squashfs")

mb() { awk -v b="$1" 'BEGIN { printf "%7.1f MB", b / 1048576 }'; }
log ""
log "== Result"
log "                 stock        slimmed"
log "Unpacked:   $(mb "$stock_size")   $(mb "$slim_size")"
log "Squashfs:   $(mb "$stock_squashfs")   $(mb "$slim_squashfs")"
log "Packages:   $(wc -l <"$OUT/$LAYER.manifest") remain"
log "Compression: mksquashfs ${MKSQUASHFS_OPTS[*]}"
