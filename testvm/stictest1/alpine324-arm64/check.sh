#!/usr/bin/env bash
# SticTest1 targeted check for alpine324-arm64 (Alpine 3.24 aarch64 on Bigfoot,
# docs/design/arm64.md A5/A6: musl, an ESP at /boot/efi, swap and XFS /; no
# Secure Boot).  Its OS and root are alpine324's, so its in-guest part is
# that check's, ../alpine324/check-inner.sh, run here on aarch64: the fixture
# on loop XFS (the root's mkfs features) and ext4, four directions, restore
# over immutable/append-only, one non-root run, the two tools' snapshots
# compared node by node, fsmeta.py for every restore.
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR: holds restic-bko and rustic-bko for Linux arm64 (roundN/bin/linux-arm64)
# Boots a throwaway (--snapshot) overlay of the guest (taking a guest slot and a
# cmp-run.sh reservation), installs the test tools there (apk: bash attr acl
# libcap-utils python3 e2fsprogs-extra coreutils), and runs check-inner.sh
# there.  Prints PASS/FAIL/LIMIT lines, writes results.json, exits 0 if
# nothing FAILs.  Runs only on an aarch64 host (Bigfoot).
set -uo pipefail
[[ $# -ge 3 ]] || { echo "usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]" >&2; exit 2; }
BIN=$(cd "$1" && pwd); PORT=$2; PFX=$3; FILTER=${4:-}
GUEST=alpine324-arm64
HERE=$(cd "$(dirname "$0")" && pwd)
H=$(cd "$HERE/../alpine324" && pwd)   # the in-guest part: alpine324's
WORK=$HOME/backotter-work
[[ $(uname -m) == aarch64 ]] || { echo "FAIL harness: $GUEST is an arm64 guest, and this host is $(uname -m)"; exit 1; }
LOGS=${STC_LOGS:-$WORK/stictest1/$GUEST/logs}; mkdir -p "$LOGS"
if [[ -z ${CHECK_RESERVED:-} ]]; then
    # shellcheck source=/dev/null
    source "${BKO_TESTVM:-$WORK/testvm}/compare/guest-slot.sh"
    slot_take 3 "$(slot_range_here)"
    CHECK_RESERVED=1 HOT_C=90 "$WORK/cmp-run.sh" --reserve 3 6 "$HERE/check.sh" "$@"; rc=$?
    slot_give
    exit $rc
fi
SCR=/mnt/bko-ram/$PFX/$GUEST
mkdir -p "$SCR"
export TMPDIR=$SCR WORK
. "${BKO_TESTVM:-$WORK/testvm}/vm-lib.sh"
# shellcheck source=../apk-add.sh
. "$HERE/../apk-add.sh"
cleanup() { vm_halt; rm -rf -- "${SCR:?}"; rmdir "/mnt/bko-ram/${PFX:?}" 2>/dev/null; }
trap cleanup EXIT
log=$LOGS/check-$(date +%Y%m%d-%H%M%S).log
{
    echo "== $GUEST $(date -Is) bin=$BIN port=$PORT"
    cat "$BIN/restic-commit" "$BIN/rustic-commit" 2>/dev/null | head -4
    VM_BOOT_TIMEOUT=300 vm_boot $GUEST "$PORT" "$LOGS/vm" --snapshot --memory 4096 || { echo "FAIL boot: guest didn't come up"; exit 1; }
    # No copy and no inner part without the tools (apk-add.sh: the image's
    # index, this host's package cache, 4 tries; t2s-20261119T0726-e436's
    # 300 s ran out on a crawling link, and the inner part had no bash).
    if stic_apk_add bash attr acl libcap-utils python3 e2fsprogs-extra xfsprogs-extra coreutils file; then
        for f in "$BIN/restic-bko" "$BIN/rustic-bko" "${BKO_TESTVM:-$WORK/testvm}/compare/fsmeta.py" "${BKO_TESTVM:-$WORK/testvm}/compare/rt-common.sh" "$H/check-inner.sh"; do
            vm_put "$f" "/root/$(basename "$f")" || echo "FAIL harness-copy: $f"
        done
        T=60 vm_ssh 'uname -m' | sed 's/^/guest machine: /'
        # The arm64 guest has no ext4 of its own (an ESP and XFS /), so its kernel hasn't the ext4
        # module loaded, and busybox's mount of the fixture's loop ext4 fails (EINVAL) without it.
        T=60 vm_ssh 'modprobe -a ext4 loop 2>&1; grep -w ext4 /proc/filesystems' | sed 's/^/guest modules: /'
        T=780 vm_sh 'bash /root/check-inner.sh' 2>&1
        echo "inner exit $?"
    else
        echo "inner: not run (no test tools)"
    fi
    echo "== end $(date -Is)"
} >"$log" 2>&1
# (without its test tools nothing ran in the guest: apk-add.sh's harness-tools line says why)
if ! grep -q '^inner: not run (no test tools)$' "$log"; then
    grep -qx 'guest machine: aarch64' "$log" || echo "FAIL guest machine: the guest didn't say aarch64" >>"$log"
    # (check-inner.sh's last lines are its LIMITs: a run cut short is a FAIL, not a PASS on what it got to)
    grep -q '^LIMIT project-ids-ext4' "$log" || echo "FAIL check ran: check-inner.sh stopped before its end ($(grep '^inner exit' "$log"); see $log)" >>"$log"
fi
grep -E '^(PASS|FAIL|LIMIT) ' "$log" | { if [[ -n $FILTER ]]; then grep -E "$FILTER"; else cat; fi; } >"$SCR/lines"
cat "$SCR/lines"
python3 - "$SCR/lines" "$LOGS/results.json" "$log" <<'PY'
import json, sys
rows = []
for l in open(sys.argv[1], errors="replace"):
    st, rest = l.rstrip("\n").split(" ", 1)
    need, _, detail = rest.partition(": ")
    rows.append({"status": st, "need": need, "detail": detail})
json.dump({"guest": "alpine324-arm64", "log": sys.argv[3], "results": rows}, open(sys.argv[2], "w"), indent=1)
PY
echo "log: $log"
if grep -q '^FAIL ' "$SCR/lines" || ! grep -q '^PASS ' "$SCR/lines"; then exit 1; fi
exit 0
