#!/usr/bin/env bash
# SticTest1 targeted check of openbsd8: the fixture (guest.sh, beside this
# script) on a scratch FFS2 (vnd) in a throwaway overlay of the guest; four
# directions into fresh FFS2, a restore over existing uchg/uappnd files, a
# non-root run, cross-tool snapshot identity (rt-common.sh's rt_nodes).
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR holds restic-bko and rustic-bko for OpenBSD.
# Prints PASS/FAIL/LIMIT lines, writes results.json and the log to
# ~/backotter-work/stictest1/openbsd8/logs/, exits 0 if nothing FAILs.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
WORK=${BKO_WORK:-$HOME/backotter-work}
guest=openbsd8
if [[ ${1:-} == --inner ]]; then
    shift; bin=$1 port=$2 prefix=$3 logs=$4
    . "$WORK/testvm/vm-lib.sh"
    trap vm_halt EXIT
    export TMPDIR=/mnt/bko-ram/$prefix
    mkdir -p "$TMPDIR"
    vm_boot $guest "$port" "$logs/vm" --snapshot --memory 4096 || { echo "FAIL harness: guest didn't boot"; exit 1; }
    vm_put "$bin/restic-bko" /root/restic-bko && vm_put "$bin/rustic-bko" /root/rustic-bko &&
        vm_put "$WORK/testvm/compare/rt-common.sh" /root/rt-common.sh && vm_put "$here/guest.sh" /root/guest.sh ||
        { echo "FAIL harness: copying into the guest"; exit 1; }
    T=840 vm_ssh '/bin/sh /root/guest.sh 2>&1'
    vm_halt
    exit 0
fi
[[ $# -ge 3 ]] || { sed -n '2,9p' "$0" >&2; exit 2; }
bin=$(realpath "$1") port=$2 prefix=$3 filter=${4:-}
[[ -x $bin/restic-bko && -x $bin/rustic-bko ]] || { echo "FAIL harness: no restic-bko or rustic-bko in $bin"; exit 1; }
logs=${STC_LOGS:-$WORK/stictest1/$guest/logs}; mkdir -p "$logs"
log=$logs/check-$(date +%Y%m%d-%H%M%S).log
. "$WORK/testvm/compare/guest-slot.sh"
case $(hostname) in chopin*) slot_take 2 2700-2799 ;; *) slot_take 3 2900-2999 ;; esac
HOT_C=90 "$WORK/cmp-run.sh" --reserve 3 6 "$here/$(basename "$0")" --inner "$bin" "$port" "$prefix" "$logs" >"$log" 2>&1
slot_give
rm -rf "/mnt/bko-ram/$prefix"
grep -aE '^(PASS|FAIL|LIMIT) ' "$log" | { if [[ -n $filter ]]; then grep -aE -- "$filter"; else cat; fi; } >"$logs/lines"
cat "$logs/lines"
[[ -s $logs/lines ]] || echo "FAIL harness: no results (see $log)" | tee "$logs/lines"
python3 - "$logs/lines" "$log" "$bin" >"$logs/results.json" <<'PY'
import json, sys
rows = []
for l in open(sys.argv[1], errors="replace"):
    st, rest = l.rstrip("\n").split(" ", 1)
    need, _, detail = rest.partition(": ")
    rows.append({"status": st, "need": need, "detail": detail})
print(json.dumps({"guest": "openbsd8", "bin": sys.argv[3], "log": sys.argv[2], "results": rows}, indent=1))
PY
echo "log: $log"
! grep -q '^FAIL ' "$logs/lines"
