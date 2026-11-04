#!/usr/bin/env bash
# SticTest1 targeted check for alpine324 (Alpine 3.24, musl, XFS root, ext4 /boot).
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
# Boots a throwaway (--snapshot) overlay of the guest (taking a guest slot and a
# cmp-run.sh reservation), installs the test tools there (apk: bash attr acl
# libcap-utils python3 e2fsprogs-extra coreutils), and runs check-inner.sh
# there: the fixture on loop XFS (the root's mkfs features) and ext4 (/boot's),
# four directions, restore over immutable/append-only, one non-root run, the
# two tools' snapshots compared node by node, fsmeta.py for every restore.
# Prints PASS/FAIL/LIMIT lines, writes results.json, exits 0 if nothing FAILs.
set -uo pipefail
[[ $# -ge 3 ]] || { echo "usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]" >&2; exit 2; }
BIN=$(cd "$1" && pwd); PORT=$2; PFX=$3; FILTER=${4:-}
GUEST=alpine324
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$HOME/backotter-work
LOGS=${STC_LOGS:-$WORK/stictest1/$GUEST/logs}; mkdir -p "$LOGS"
if [[ -z ${CHECK_RESERVED:-} ]]; then
    # shellcheck source=/dev/null
    source "$WORK/testvm/compare/guest-slot.sh"
    case $(hostname) in chopin*) slot_take 2 2700-2799 ;; *) slot_take 3 2900-2999 ;; esac
    CHECK_RESERVED=1 HOT_C=90 "$WORK/cmp-run.sh" --reserve 3 6 "$HERE/check.sh" "$@"; rc=$?
    slot_give
    exit $rc
fi
SCR=/mnt/bko-ram/$PFX/$GUEST
mkdir -p "$SCR"
export TMPDIR=$SCR WORK
. "$WORK/testvm/vm-lib.sh"
cleanup() { vm_halt; rm -rf -- "${SCR:?}"; rmdir "/mnt/bko-ram/${PFX:?}" 2>/dev/null; }
trap cleanup EXIT
log=$LOGS/check-$(date +%Y%m%d-%H%M%S).log
{
    echo "== $GUEST $(date -Is) bin=$BIN port=$PORT"
    cat "$BIN/restic-commit" "$BIN/rustic-commit" 2>/dev/null | head -4
    VM_BOOT_TIMEOUT=300 vm_boot $GUEST "$PORT" "$LOGS/vm" --snapshot --memory 4096 || { echo "FAIL boot: guest didn't come up"; exit 1; }
    T=300 vm_sh 'apk add -q --no-progress bash attr acl libcap-utils python3 e2fsprogs-extra xfsprogs-extra coreutils file 2>&1 | tail -3' ||
        echo "FAIL harness-tools: apk add failed"
    for f in "$BIN/restic-bko" "$BIN/rustic-bko" "$WORK/testvm/compare/fsmeta.py" "$WORK/testvm/compare/rt-common.sh" "$HERE/check-inner.sh"; do
        vm_put "$f" "/root/$(basename "$f")" || echo "FAIL harness-copy: $f"
    done
    T=780 vm_sh 'bash /root/check-inner.sh' 2>&1
    echo "== end $(date -Is)"
} >"$log" 2>&1
grep -E '^(PASS|FAIL|LIMIT) ' "$log" | { if [[ -n $FILTER ]]; then grep -E "$FILTER"; else cat; fi; } >"$SCR/lines"
cat "$SCR/lines"
python3 - "$SCR/lines" "$LOGS/results.json" "$log" <<'PY'
import json, sys
rows = []
for l in open(sys.argv[1], errors="replace"):
    st, rest = l.rstrip("\n").split(" ", 1)
    need, _, detail = rest.partition(": ")
    rows.append({"status": st, "need": need, "detail": detail})
json.dump({"guest": "alpine324", "log": sys.argv[3], "results": rows}, open(sys.argv[2], "w"), indent=1)
PY
echo "log: $log"
if grep -q '^FAIL ' "$SCR/lines" || ! grep -q '^PASS ' "$SCR/lines"; then exit 1; fi
exit 0
