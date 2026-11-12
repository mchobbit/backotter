#!/usr/bin/env bash
# SticTest1 targeted check for chimera-uefi (Chimera Linux: musl, FreeBSD's
# userland (chimerautils), dinit, apk-tools 3; UEFI, systemd-boot without
# systemd; the ESP (vfat) as /boot, / ext4 on LVM on LUKS2 with the key in
# the initramfs; docs/design/chimera.md).  Derived from ../chimera (XFS /,
# ext4 /boot).
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR holds restic-bko and rustic-bko for Linux (roundN/bin/linux).
# Boots a throwaway (--snapshot) overlay of the guest (taking a guest slot and
# a cmp-run.sh reservation), installs the test tools there (apk: attr-progs
# acl-progs libcap-progs dosfstools; no bash: the guest part is POSIX sh for
# FreeBSD's sh and tools), and runs inner.sh there: the fixture on loop ext4
# (/'s features) and vfat (the ESP's FAT type, cluster size and mount
# options), four directions, restore over changed immutable/append-only
# files (ext4) and changed FAT attributes and read-only files (vfat), ext4's
# inode flags and FAT's attributes, the directories above the backed-up
# path, the two tools' snapshots compared node by node, each tool's check of
# the other's repository, one non-root run, fsmeta.py for every restore.
# Prints PASS/FAIL/LIMIT lines, writes results.json in the logs directory,
# exits 0 if nothing FAILs.
set -uo pipefail
[[ $# -ge 3 ]] || { echo "usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]" >&2; exit 2; }
BIN=$(cd "$1" && pwd); PORT=$2; PFX=$3; FILTER=${4:-}
GUEST=chimera-uefi
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$HOME/backotter-work
LOGS=${STC_LOGS:-$WORK/stictest1/$GUEST/logs}; mkdir -p "$LOGS"
if [[ -z ${CHECK_RESERVED:-} ]]; then
    # shellcheck source=/dev/null
    source "${BKO_TESTVM:-$WORK/testvm}/compare/guest-slot.sh"
    slot_take 3 "$(slot_range_here)"
    CHECK_RESERVED=1 HOT_C=90 "$WORK/cmp-run.sh" --reserve 4 6 "$HERE/check.sh" "$@"; rc=$?
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
    # vm-run reads the guest's vm-run-options (--uefi --no-secure-boot); the
    # LUKS2 key is in the initramfs, so it boots unattended.
    VM_BOOT_TIMEOUT=300 vm_boot $GUEST "$PORT" "$LOGS/vm" --snapshot --memory 4096 || { echo "FAIL boot: guest didn't come up"; exit 1; }
    # apk's own exit status (not tail's, nor mkdir's): without these tools
    # the fixture would lack what they set and still compare equal.
    T=300 vm_sh 'apk add --no-interactive -q --no-progress attr-progs acl-progs libcap-progs dosfstools >/root/apk.log 2>&1; rc=$?
        tail -n 3 /root/apk.log; mkdir -p /root/c4; exit $rc' ||
        echo "FAIL harness-tools: apk add attr-progs acl-progs libcap-progs dosfstools failed"
    for f in "$BIN/restic-bko" "$BIN/rustic-bko" "${BKO_TESTVM:-$WORK/testvm}/compare/fsmeta.py" "${BKO_TESTVM:-$WORK/testvm}/compare/rt-common.sh" \
             "$HERE/inner.sh" "$HERE/fatattr.py"; do
        vm_put "$f" "/root/c4/$(basename "$f")" || echo "FAIL harness-copy: $f"
    done
    T=900 vm_sh 'sh /root/c4/inner.sh' 2>&1; rc=$?
    [[ $rc -eq 0 ]] || echo "FAIL harness: inner.sh exit $rc"
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
json.dump({"guest": "chimera-uefi", "log": sys.argv[3], "results": rows}, open(sys.argv[2], "w"), indent=1)
PY
echo "log: $log"
if grep -q '^FAIL ' "$SCR/lines" || ! grep -q '^PASS ' "$SCR/lines" || ! grep -q '^INNER-DONE' "$log"; then exit 1; fi
exit 0
