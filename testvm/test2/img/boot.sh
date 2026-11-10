#!/usr/bin/env bash
# ImgTest2's boot tests of one pinned image, from the run's private copy:
#
#   boot.sh [--ram DIR] PLATFORM PIN OUT MODE...
#
# MODE: bios, uefi, uefi-secureboot, or cdrom (BIOS, the image as a CD).  For
# each, stick/boot-test.py writes OUT/boot-MODE.log (the serial console),
# OUT/boot-MODE.out (its verdicts) and OUT/boot-MODE.rc.  The first mode that
# reaches the checks also types the manifest: OUT/manifest.json.  --ram DIR
# (the reserved RAM disk's) is made and removed here and holds the overlays.
# Exit 0 when every mode passed, 1 when one failed, 2 on bad usage.
set -u
RAM=""
if [[ ${1:-} == --ram ]]; then RAM=$2; shift 2; fi
[[ $# -ge 4 ]] || { sed -n '2,/^set -u/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2; exit 2; }
P=$1 PIN=$2 OUT=$3; shift 3
ROOT=$(cd "$(dirname "$(realpath "$0")")/../../.." && pwd)
[[ -f $PIN ]] || { echo "boot.sh: no $PIN" >&2; exit 2; }
mkdir -p "$OUT" || exit 2
if [[ -n $RAM ]]; then
    mkdir -p "$RAM" || exit 2
    trap 'rm -rf --one-file-system "$RAM"' EXIT
    # (Signals ignored first: a second one must not cut the EXIT trap's rm.)
    trap 'trap "" TERM INT HUP; exit 143' TERM
    trap 'trap "" TERM INT HUP; exit 130' INT
    trap 'trap "" TERM INT HUP; exit 129' HUP
    export TMPDIR=$RAM
fi
fails=0
for m in "$@"; do
    case $m in
    cdrom) media=cdrom mm=bios ;;
    bios|uefi|uefi-secureboot) media=usb mm=$m ;;
    *) echo "boot.sh: MODE: bios, uefi, uefi-secureboot or cdrom, not '$m'" >&2; exit 2 ;;
    esac
    mf=(); [[ -s $OUT/manifest.json ]] || mf=(--manifest "$OUT/manifest.json")
    python3 -B "$ROOT/stick/boot-test.py" "$PIN" "$mm" --platform "$P" --media "$media" \
        --log "$OUT/boot-$m.log" ${mf[@]+"${mf[@]}"} >"$OUT/boot-$m.out" 2>&1
    rc=$?
    echo "$rc" >"$OUT/boot-$m.rc"
    [[ $rc -eq 0 ]] || fails=$((fails + 1))
done
[[ $fails -eq 0 ]]
