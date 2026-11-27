#!/usr/bin/env bash
# ImgTest2's boot tests of one pinned image, from the run's private copy:
#
#   boot.sh [--ram DIR] [--ram-gib G] [--width N] PLATFORM PIN OUT MODE...
#
# MODE: bios, uefi, uefi-secureboot, or cdrom (BIOS, the image as a CD).  For
# each, stick/boot-test.py writes OUT/boot-MODE.log (the serial console),
# OUT/boot-MODE.out (its verdicts) and OUT/boot-MODE.rc.  stick/boot-modes.sh
# runs them, N at once (default 1; img.py picks N from the host's ceiling and
# reserves 4.5 GiB for each: img-speed.md §9, D8).  The first mode named also
# types the manifest, OUT/manifest.json; when it typed none, the next mode that
# completed its checks is booted once more, alone, to type it.  --ram DIR (the
# reserved RAM disk's) is made and removed here: it holds the overlays and a
# copy of the pin (checked against PIN.sha256), which the modes boot, when the
# pin + 0.5 GiB fits in G GiB.  Exit 0 when every mode passed, 1 when one
# failed, 2 on bad usage.
set -u
RAM="" opts=()
while [[ ${1:-} == --* ]]; do
    [[ $# -ge 2 ]] || break
    case $1 in
    --ram) RAM=$2 ;;
    --ram-gib|--width) opts+=("$1" "$2") ;;
    *) break ;;
    esac
    shift 2
done
[[ $# -ge 4 && ${1:-} != --* ]] || { sed -n '2,/^set -u/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2; exit 2; }
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
    opts+=(--ram "$RAM")
    sum=$(cut -d' ' -f1 "$PIN.sha256" 2>/dev/null | head -n 1)
    [[ $sum =~ ^[0-9a-f]{64}$ ]] && opts+=(--sha256 "$sum")
fi
bash "$ROOT/stick/boot-modes.sh" ${opts[@]+"${opts[@]}"} --manifest "$OUT/manifest.json" "$P" "$PIN" "$OUT" "$@"
