#!/usr/bin/env bash
# SticTest1 targeted check for chimera-zfs (Chimera Linux: musl, FreeBSD's
# userland (chimerautils), dinit, apk-tools 3; UEFI, / on ZFS
# rpool/ROOT/chimera, /boot ext4, ESP vfat at /boot/efi;
# docs/design/chimera.md).  Derived from chimera's check (the same harness,
# its inner script adapted: ZFS in place of XFS, and the ESP).
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR holds restic-bko and rustic-bko for Linux (roundN/bin/linux).
#   INVENTORY=1: also run inv.sh (each file system and what it supports) in
#   the same boot, into the log; INVENTORY=only: the inventory alone.
# Boots a throwaway (--snapshot) overlay of the guest (taking a guest slot and
# a cmp-run.sh reservation), installs the test tools there (apk: attr-progs
# acl-progs libcap-progs; no bash: the guest part is POSIX sh for FreeBSD's
# sh and tools), and runs inner.sh there: the fixture on ZFS datasets in the
# guest's own rpool given its root dataset's properties, on loop ext4 made
# like /boot and on loop vfat made like the ESP; four directions, restore
# over changed immutable/append-only files, ZFS's DOS attributes and project
# IDs, FAT's attributes, the directories above the backed-up path, the two
# tools' snapshots compared node by node, each tool's check of the other's
# repository, one non-root run on ZFS, fsmeta.py for every restore.  Prints
# PASS/FAIL/LIMIT lines, writes results.json in the logs directory, exits 0
# if nothing FAILs.
set -uo pipefail
[[ $# -ge 3 ]] || { echo "usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]" >&2; exit 2; }
BIN=$(cd "$1" && pwd); PORT=$2; PFX=$3; FILTER=${4:-}
GUEST=chimera-zfs
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$HOME/backotter-work
LOGS=${STC_LOGS:-$WORK/stictest1/$GUEST/logs}; mkdir -p "$LOGS"
if [[ -z ${CHECK_RESERVED:-} ]]; then
    # shellcheck source=/dev/null
    source "${BKO_TESTVM:-$WORK/testvm}/compare/guest-slot.sh"
    slot_take 3 "$(slot_range_here)"
    CHECK_RESERVED=1 HOT_C=90 "$WORK/cmp-run.sh" --reserve 2 6 "$HERE/check.sh" "$@"; rc=$?
    slot_give
    exit $rc
fi
SCR=/mnt/bko-ram/$PFX/$GUEST
mkdir -p "$SCR"
export TMPDIR=$SCR WORK
# shellcheck source=/dev/null
. "${BKO_TESTVM:-$WORK/testvm}/vm-lib.sh"
cleanup() { vm_halt; rm -rf -- "${SCR:?}"; rmdir "/mnt/bko-ram/${PFX:?}" 2>/dev/null; }
trap cleanup EXIT
log=$LOGS/check-$(date +%Y%m%d-%H%M%S).log
{
    echo "== $GUEST $(date -Is) bin=$BIN port=$PORT"
    cat "$BIN/restic-commit" "$BIN/rustic-commit" 2>/dev/null | head -4
    VM_BOOT_TIMEOUT=300 vm_boot $GUEST "$PORT" "$LOGS/vm" --snapshot --memory 4096 || { echo "FAIL boot: guest didn't come up"; exit 1; }
    # apk's own exit status (not tail's, nor mkdir's): without these tools
    # the fixture would lack what they set and still compare equal.
    T=300 vm_sh 'apk add --no-interactive -q --no-progress attr-progs acl-progs libcap-progs >/root/apk.log 2>&1; rc=$?
        tail -n 3 /root/apk.log; mkdir -p /root/c4; exit $rc' ||
        echo "FAIL harness-tools: apk add attr-progs acl-progs libcap-progs failed"
    if [[ -n ${INVENTORY:-} ]]; then
        vm_put "$HERE/inv.sh" /root/c4/inv.sh && T=600 vm_sh 'sh /root/c4/inv.sh' 2>&1
        [[ $INVENTORY == only ]] && { echo "== end $(date -Is)"; exit 0; }
    fi
    for f in "$BIN/restic-bko" "$BIN/rustic-bko" "${BKO_TESTVM:-$WORK/testvm}/compare/fsmeta.py" "${BKO_TESTVM:-$WORK/testvm}/compare/rt-common.sh" "$HERE/inner.sh"; do
        vm_put "$f" "/root/c4/$(basename "$f")" || echo "FAIL harness-copy: $f"
    done
    T=1500 vm_sh "PFX=$PFX sh /root/c4/inner.sh" 2>&1; rc=$?
    [[ $rc -eq 0 ]] || echo "FAIL harness: inner.sh exit $rc"
    echo "== end $(date -Is)"
} >"$log" 2>&1
if [[ ${INVENTORY:-} == only ]]; then echo "inventory: $log"; exit 0; fi
grep -E '^(PASS|FAIL|LIMIT) ' "$log" | { if [[ -n $FILTER ]]; then grep -E "$FILTER"; else cat; fi; } >"$SCR/lines"
cat "$SCR/lines"
python3 - "$SCR/lines" "$LOGS/results.json" "$log" <<'PY'
import json, sys
rows = []
for l in open(sys.argv[1], errors="replace"):
    st, rest = l.rstrip("\n").split(" ", 1)
    need, _, detail = rest.partition(": ")
    rows.append({"status": st, "need": need, "detail": detail})
json.dump({"guest": "chimera-zfs", "log": sys.argv[3], "results": rows}, open(sys.argv[2], "w"), indent=1)
PY
echo "log: $log"
if grep -q '^FAIL ' "$SCR/lines" || ! grep -q '^PASS ' "$SCR/lines" || ! grep -q '^INNER-DONE' "$log"; then exit 1; fi
exit 0
