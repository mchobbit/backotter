#!/usr/bin/env bash
# SticTest1 targeted check of void-musl (Void Linux, musl, btrfs root, ext4 /boot).
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR holds restic-bko and rustic-bko for Linux (roundN/bin/linux).
# Boots a --snapshot throwaway of ~/backotter-work/vms/void-musl with three
# empty raw scratch disks (in /mnt/bko-ram/PREFIX), runs inner.sh there (the
# guest's own mkfs.btrfs/mkfs.ext4: fixture, four directions into fresh file
# systems, restore over immutable/append-only files, non-root run), copies the
# dumps and repositories out and judges them with judge.py (node identity with
# restic-bko cat blob).  Prints PASS/FAIL/LIMIT lines, writes results.json in
# the run's log dir, exits 0 if nothing FAILs.  The guest has no python3, so
# the comparison in the guest uses stat/lsattr/getfattr/getfacl (the host's
# attr/acl tools shipped in with their glibc loader where the guest lacks them).
set -uo pipefail
[[ $# -ge 3 ]] || { echo "usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PFX=$3 FILTER=${4:-}
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$HOME/backotter-work
if [[ -z ${CHECK_INNER:-} ]]; then
    # shellcheck source=/dev/null
    source "${BKO_TESTVM:-$WORK/testvm}/compare/guest-slot.sh"
    slot_take 3 "$(slot_range_here)"
    CHECK_INNER=1 HOT_C=90 "$WORK/cmp-run.sh" --reserve 3 6 env CHECK_INNER=1 BKO_GUEST_SLOT="$BKO_GUEST_SLOT" "$HERE/$(basename "$0")" "$@"
    rc=$?; slot_give; exit $rc
fi
RAM=/mnt/bko-ram/$PFX/void-musl
LOG=${STC_LOGS:-$WORK/stictest1/void-musl/logs}/$(date +%Y%m%d-%H%M%S); mkdir -p "$LOG"
cleanup() { vm_halt; rm -rf "$RAM"; }
rm -rf "$RAM"; mkdir -p "$RAM/tmp" "$RAM/out/w" "$RAM/ship/c/b" "$RAM/ship/c/g/bin" "$RAM/ship/c/g/lib"
# shellcheck source=/dev/null
. "${BKO_TESTVM:-$WORK/testvm}/vm-lib.sh"
trap cleanup EXIT
truncate -s 1G "$RAM/src.img"; truncate -s 300M "$RAM/srcb.img"; truncate -s 8G "$RAM/work.img"
cp "$BIN/restic-bko" "$BIN/rustic-bko" "$RAM/ship/c/b/"
cp "$HERE/inner.sh" "$RAM/ship/c/"
for t in getfattr setfattr getfacl setfacl; do
    cp "$(command -v $t)" "$RAM/ship/c/g/lib/$t.real"
    printf '#!/bin/sh\nexec /root/c/g/lib/ld-linux-x86-64.so.2 --library-path /root/c/g/lib /root/c/g/lib/%s.real "$@"\n' $t >"$RAM/ship/c/g/bin/$t"
    chmod 755 "$RAM/ship/c/g/bin/$t"
done
cp -L /lib64/ld-linux-x86-64.so.2 /lib/x86_64-linux-gnu/{libc.so.6,libacl.so.1,libattr.so.1} "$RAM/ship/c/g/lib/" 2>/dev/null
echo "check: booting void-musl on $PORT (log $LOG)"
TMPDIR=$RAM/tmp vm_boot void-musl "$PORT" "$LOG/vm" --snapshot --memory 3072 \
    --disk "$RAM/src.img" --disk "$RAM/srcb.img" --disk "$RAM/work.img" || { echo "FAIL boot: void-musl didn't come up"; exit 1; }
tar -C "$RAM/ship" -cf - c | T=120 vm_ssh 'rm -rf /root/c; tar -xf - -C /root'
T=780 vm_ssh 'bash /root/c/inner.sh' >"$LOG/inner.log" 2>&1
echo "check: inner exit $?"
T=120 vm_ssh 'cd /root/c && tar -cf - *.dump *.out *.diff 2>/dev/null' | tar -xf - -C "$RAM/out"
T=120 vm_ssh 'cd /w && tar -cf - repo-restic repo-rustic' | tar -xf - -C "$RAM/out/w"
cp "$LOG/inner.log" "$RAM/out/"
vm_halt
python3 "$HERE/judge.py" "$BIN/restic-bko" "$RAM/out" | { if [[ -n $FILTER ]]; then grep -i -- "$FILTER"; else cat; fi; } | tee "$LOG/verdict.txt"
rc=${PIPESTATUS[0]}
cp "$RAM/out/results.json" "$LOG/" 2>/dev/null; cp "$RAM/out/"*.diff "$RAM/out/"*.out "$LOG/" 2>/dev/null
echo "check: results in $LOG/results.json"
exit "$rc"
