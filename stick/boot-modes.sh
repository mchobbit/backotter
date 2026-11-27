#!/usr/bin/env bash
# The boot tests of one image, its modes side by side (docs/design/img-speed.md §9, D8):
#
#   boot-modes.sh [--width N] [--ram DIR] [--ram-gib G] [--sha256 HEX] [--manifest FILE]
#                 PLATFORM IMG OUT MODE...
#
# MODE: bios, uefi, uefi-secureboot, or cdrom (BIOS, the image as a CD).  For each,
# boot-test.py (beside this script) writes OUT/boot-MODE.log (the serial console),
# OUT/boot-MODE.out (its verdicts) and OUT/boot-MODE.rc (its exit status).  At most N
# modes run at once (default 1: one after another, in the order given).  Each guest has its
# own copy-on-write overlay and firmware variables in TMPDIR and no network port, so the
# modes share the image read-only; each is 2 vCPUs and 4 GiB, as when they ran one by one.
# The caller's reservation holds N of them (4.5 GiB each).
#
# --ram DIR (the reservation's RAM directory): IMG is first copied into DIR, and the modes
# boot the copy, which is removed at the end.  --sha256 HEX: the copy must have that
# SHA-256.  --ram-gib G: the copy is made only when IMG + 0.5 GiB fits in what DIR has
# left of G GiB.  Where no copy is made (no room, a failed copy, another SHA-256), the
# modes boot IMG itself, as before, and OUT/boot-modes.txt says why.
#
# --manifest FILE: the first MODE named also types PLATFORM/manifest.sh (FILE, JSON).  When
# it wrote none (it failed before or during its checks), the next mode in order that
# completed its checks in the batch is booted once more, alone, with --manifest, into
# OUT/manifest-MODE.log and OUT/manifest-MODE.out: the verdicts stay the batch's.
#
# OUT/boot-modes.txt: the width, what was booted, each mode's start, end and rc.  A stop
# (TERM, INT, HUP) stops the modes' boot-test.py (each powers its QEMU off) and removes the
# copy.  Exit 0 when every mode passed, 1 when one failed, 2 on bad usage.
set -u
HERE=$(dirname "$(realpath "$0")")
usage() { sed -n '2,/^set -u/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2; exit 2; }
WIDTH=1 RAM="" RAMGIB="" SUM="" MF=""
while [[ ${1:-} == --* ]]; do
    [[ $# -ge 2 ]] || usage
    case $1 in
    --width) WIDTH=$2 ;;
    --ram) RAM=$2 ;;
    --ram-gib) RAMGIB=$2 ;;
    --sha256) SUM=$2 ;;
    --manifest) MF=$2 ;;
    *) usage ;;
    esac
    shift 2
done
[[ $# -ge 4 ]] || usage
P=$1 IMG=$2 OUT=$3; shift 3
MODES=("$@")
[[ $WIDTH =~ ^[1-9][0-9]*$ ]] || { echo "boot-modes: --width: a whole number from 1, not '$WIDTH'" >&2; exit 2; }
[[ -z $RAMGIB || $RAMGIB =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo "boot-modes: --ram-gib: GiB, not '$RAMGIB'" >&2; exit 2; }
[[ -z $SUM || $SUM =~ ^[0-9a-f]{64}$ ]] || { echo "boot-modes: --sha256: 64 hex, not '$SUM'" >&2; exit 2; }
for m in "${MODES[@]}"; do
    case $m in bios|uefi|uefi-secureboot|cdrom) ;;
    *) echo "boot-modes: MODE: bios, uefi, uefi-secureboot or cdrom, not '$m'" >&2; exit 2 ;;
    esac
done
[[ -f $IMG ]] || { echo "boot-modes: no $IMG" >&2; exit 2; }
mkdir -p "$OUT" || exit 2
note() { echo "$*" >>"$OUT/boot-modes.txt"; }
: >"$OUT/boot-modes.txt"
pids=() pmodes=() copy=""
stop() {
    trap '' TERM INT HUP
    local p
    for p in ${pids[@]+"${pids[@]}"}; do kill -TERM "$p" 2>/dev/null; done
    wait
    [[ -z $copy ]] || rm -f -- "$copy"
    note "stopped (exit $1)"
    exit "$1"
}
trap 'stop 143' TERM; trap 'stop 130' INT; trap 'stop 129' HUP

# The copy in the RAM disk, when it fits and is IMG's bytes.
SRC=$IMG
if [[ -n $RAM ]]; then
    why=""
    size=$(stat -Lc %s -- "$IMG" 2>/dev/null || stat -Lf %z -- "$IMG" 2>/dev/null)
    mkdir -p -- "$RAM" || why="can't make $RAM"
    if [[ -z $why && -n $RAMGIB ]]; then
        used=$(du -sk -- "$RAM" 2>/dev/null | cut -f1)
        awk -v s="${size:-0}" -v u="${used:-0}" -v g="$RAMGIB" \
            'BEGIN { exit !(s / 1073741824 + 0.5 + u / 1048576 <= g + 1e-9) }' ||
            why="$IMG ($size bytes) + 0.5 GiB is over what $RAM has left of its $RAMGIB GiB"
    fi
    if [[ -z $why ]]; then
        copy=$RAM/boot-modes.$$.${IMG##*/}
        { cp --sparse=always -- "$IMG" "$copy" 2>/dev/null || cp -- "$IMG" "$copy"; } || why="the copy into $RAM failed"
    fi
    if [[ -z $why && -n $SUM ]]; then
        h=$(sha256sum <"$copy" | cut -d' ' -f1)
        [[ $h == "$SUM" ]] || why="the copy's SHA-256 is ${h:0:12}, not ${SUM:0:12}"
    fi
    if [[ -z $why ]]; then
        SRC=$copy
        note "image: a copy in $RAM"
    else
        [[ -z $copy ]] || rm -f -- "$copy"
        copy=""
        note "image: $IMG itself ($why)"
    fi
else
    note "image: $IMG itself"
fi
note "width: $WIDTH, modes: ${MODES[*]}"

# boot MODE LOG OUTFILE [--manifest FILE]: boot-test.py in the background (its PID in $!).
boot() {
    local m=$1 log=$2 out=$3 media=usb mm=$1; shift 3
    [[ $m == cdrom ]] && { media=cdrom; mm=bios; }
    python3 -B "$HERE/boot-test.py" "$SRC" "$mm" --platform "$P" --media "$media" --log "$log" "$@" >"$out" 2>&1 &
}
# reap: each finished mode's rc, into OUT/boot-MODE.rc.  Echoes how many still run.
reap() {
    local i n=0 rc
    running=0
    [[ ${#pids[@]} -gt 0 ]] || return 0
    for i in "${!pids[@]}"; do
        [[ -n ${pids[i]} ]] || continue
        if kill -0 "${pids[i]}" 2>/dev/null; then n=$((n + 1)); continue; fi
        wait "${pids[i]}"; rc=$?
        echo "$rc" >"$OUT/boot-${pmodes[i]}.rc"
        note "${pmodes[i]}: ended $(date -u +%H:%M:%S) rc=$rc"
        pids[i]=""
    done
    running=$n
}
running=0 first=1
for m in "${MODES[@]}"; do
    while reap; [[ $running -ge $WIDTH ]]; do sleep 0.5; done
    mf=()
    [[ -n $MF && $first == 1 ]] && mf=(--manifest "$MF")
    first=0
    boot "$m" "$OUT/boot-$m.log" "$OUT/boot-$m.out" ${mf[@]+"${mf[@]}"}
    pids+=($!) pmodes+=("$m")
    note "$m: started $(date -u +%H:%M:%S)"
done
while reap; [[ $running -gt 0 ]]; do sleep 0.5; done

# The manifest, when the first mode typed none: the next mode that completed its checks, alone.
if [[ -n $MF && ! -s $MF ]]; then
    for m in "${MODES[@]:1}"; do
        grep -q 'reached login in' "$OUT/boot-$m.out" 2>/dev/null || continue
        note "manifest: none from ${MODES[0]}; $m booted once more, alone, with --manifest"
        boot "$m" "$OUT/manifest-$m.log" "$OUT/manifest-$m.out" --manifest "$MF"
        pids=($!) pmodes=("manifest-$m")
        while reap; [[ $running -gt 0 ]]; do sleep 0.5; done
        rm -f "$OUT/boot-manifest-$m.rc"
        break
    done
fi
[[ -z $copy ]] || rm -f -- "$copy"
fails=0
for m in "${MODES[@]}"; do
    [[ $(cat "$OUT/boot-$m.rc" 2>/dev/null) == 0 ]] || fails=$((fails + 1))
done
note "failed: $fails of ${#MODES[@]}"
[[ $fails -eq 0 ]]
