#!/usr/bin/env bash
# SticTest1: debian12's targeted fixture test (ext4 like the guest's root).
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR: holds restic-bko and rustic-bko for Linux (roundN/bin/linux)
# Boots a throwaway overlay (vm-run --snapshot, TMPDIR in /mnt/bko-ram/PREFIX)
# through guest-slot.sh and cmp-run.sh --reserve, runs inner.sh there (fixture
# on fresh loop ext4s, four directions, restore over immutable files, a
# non-root run, snapshot node identity), prints PASS/FAIL/LIMIT lines,
# writes results.json; exit 0 if nothing FAILs.  Logs: $STC_LOGS/run-TIME/
# (STC_LOGS: ~/backotter-work/stictest1/debian12/logs by default).
set -uo pipefail
G=debian12
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$HOME/backotter-work
C=$WORK/testvm/compare
if [[ ${1:-} == --inner ]]; then
    shift; BIN=$1 PORT=$2 PFX=$3 OUT=$4
    . "$WORK/testvm/vm-lib.sh"
    R=/mnt/bko-ram/$PFX/$G.$$
    mkdir -p "$R"
    trap 'vm_halt; rm -rf "$R"' EXIT
    TMPDIR=$R VM_BOOT_TIMEOUT=300 vm_boot $G "$PORT" "$OUT/vm" --snapshot --memory 4096 || { echo "FAIL boot: $G didn't come up"; exit 1; }
    for f in "$BIN/restic-bko" "$BIN/rustic-bko" "$C/torture.sh" "$C/fsmeta.py" "$C/rt-common.sh" "$HERE/inner.sh"; do
        vm_put "$f" "/root/$(basename "$f")" || { echo "FAIL copy: $f"; exit 1; }
    done
    T=840 vm_sh "bash /root/inner.sh" >"$OUT/guest.out" 2>"$OUT/guest.err"
    echo "inner exit $?" >>"$OUT/guest.err"
    T=60 vm_ssh "cat /root/st1.tgz" >"$OUT/st1.tgz" 2>/dev/null && tar -C "$OUT" -xzf "$OUT/st1.tgz"
    exit 0
fi
[[ $# -ge 3 ]] || { sed -n 2,9p "$0" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PFX=$3 FILTER=${4:-}
STC_LOGS=${STC_LOGS:-$WORK/stictest1/$G/logs}
OUT=$STC_LOGS/run-$(date +%Y%m%d-%H%M%S)
mkdir -p "$OUT"
. "$C/guest-slot.sh"
case $(hostname -s) in chopin) slot_take 3 2700-2799 ;; *) slot_take 3 2900-2999 ;; esac
HOT_C=90 "$WORK/cmp-run.sh" --reserve 4 6 timeout 1500 "$HERE/$(basename "$0")" --inner "$BIN" "$PORT" "$PFX" "$OUT" >"$OUT/run.log" 2>&1
slot_give
lines=$(grep -E '^(PASS|FAIL|LIMIT) ' "$OUT/guest.out" 2>/dev/null)
[[ -n $lines ]] || lines="FAIL check ran: no results (see $OUT/run.log, guest.err)"
[[ -n $FILTER ]] && lines=$(grep -E -- "$FILTER" <<<"$lines")
echo "$lines"
printf '%s\n' "$lines" >"$OUT/lines.txt"
python3 -c '
import json, sys
rows = []
for l in open(sys.argv[1]).read().splitlines():
    v, rest = l.split(" ", 1); need, _, det = rest.partition(": ")
    rows.append({"verdict": v, "need": need, "detail": det})
json.dump({"guest": "debian12", "results": rows}, open(sys.argv[2], "w"), indent=1)' "$OUT/lines.txt" "$OUT/results.json"
cp "$OUT/results.json" "$STC_LOGS/results.json" 2>/dev/null
echo "logs: $OUT"
! grep -q '^FAIL ' <<<"$lines"
