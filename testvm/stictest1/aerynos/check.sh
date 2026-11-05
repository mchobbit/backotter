#!/usr/bin/env bash
# SticTest1 targeted check of aerynos (AerynOS, UEFI without
# Secure Boot, f2fs root, glibc), adapted from stictest1/void-musl.
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR holds restic-bko and rustic-bko for Linux (roundN/bin/linux).
# Boots a --snapshot throwaway of ~/backotter-work/vms/aerynos (its
# vm-run-options: --uefi --no-secure-boot) with two empty raw scratch disks in
# /bkoscratch/PREFIX/aerynos (disk, not RAM), runs inner.sh there (the guest's
# own mkfs.f2fs with the root's features: fixture, four directions into fresh
# f2fs file systems, restore over immutable/append-only files, non-root run),
# copies the dumps and repositories out and judges them with judge.py (node
# identity with restic-bko cat blob).  Prints PASS/FAIL/LIMIT lines, writes
# results.json in the run's log dir, exits 0 if nothing FAILs.  The guest has
# no python3 and no e2fsprogs, so the comparison in the guest uses
# stat/getfattr/getfacl (its own) and lsattr/chattr (the host's, shipped in
# with their glibc loader and libraries).
set -uo pipefail
[[ $# -ge 3 ]] || { echo "usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PFX=$3 FILTER=${4:-}
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$HOME/backotter-work
export TMPDIR=/bkoscratch/tmp; mkdir -p "$TMPDIR"
if [[ -z ${CHECK_INNER:-} ]]; then
    # shellcheck source=/dev/null
    source "$WORK/testvm/compare/guest-slot.sh"
    case $(hostname -s) in chopin) slot_take 3 2700-2799 ;; *) slot_take 3 2900-2999 ;; esac
    CHECK_INNER=1 HOT_C=90 "$WORK/cmp-run.sh" --reserve 1 5 env CHECK_INNER=1 TMPDIR="$TMPDIR" BKO_GUEST_SLOT="$BKO_GUEST_SLOT" STC_LOGS="${STC_LOGS:-}" "$HERE/$(basename "$0")" "$@"
    rc=$?; slot_give; exit $rc
fi
SCR=/bkoscratch/$PFX/aerynos
LOG=${STC_LOGS:-$WORK/stictest1/aerynos/logs}/$(date +%Y%m%d-%H%M%S); mkdir -p "$LOG"
cleanup() { vm_halt; rm -rf "$SCR"; rmdir "/bkoscratch/$PFX" 2>/dev/null; }
rm -rf "$SCR"; mkdir -p "$SCR/tmp" "$SCR/out/w" "$SCR/ship/c/b" "$SCR/ship/c/g/bin" "$SCR/ship/c/g/lib"
# shellcheck source=/dev/null
. "$WORK/testvm/vm-lib.sh"
trap cleanup EXIT
truncate -s 1500M "$SCR/src.img"; truncate -s 10G "$SCR/work.img"
cp "$BIN/restic-bko" "$BIN/rustic-bko" "$SCR/ship/c/b/"
cp "$HERE/inner.sh" "$SCR/ship/c/"
for t in chattr lsattr; do
    cp "$(command -v $t)" "$SCR/ship/c/g/lib/$t.real"
    printf '#!/bin/sh\nexec /root/c/g/lib/ld-linux-x86-64.so.2 --library-path /root/c/g/lib /root/c/g/lib/%s.real "$@"\n' $t >"$SCR/ship/c/g/bin/$t"
    chmod 755 "$SCR/ship/c/g/bin/$t"
done
cp -L /lib64/ld-linux-x86-64.so.2 /lib/x86_64-linux-gnu/{libc.so.6,libe2p.so.2,libcom_err.so.2} "$SCR/ship/c/g/lib/" ||
    { echo "FAIL harness: host's e2fsprogs libraries missing"; exit 1; }
echo "check: booting aerynos on $PORT (log $LOG)"
t0=$SECONDS
TMPDIR=$SCR/tmp VM_BOOT_TIMEOUT=400 vm_boot aerynos "$PORT" "$LOG/vm" --snapshot --memory 3072 --cpus 2 \
    --disk "$SCR/src.img" --disk "$SCR/work.img" || { echo "FAIL boot: aerynos didn't come up"; exit 1; }
echo "check: sshd up after $((SECONDS - t0)) s" | tee "$LOG/boot-time.txt"
T=120 vm_ssh 'systemctl --failed --no-legend; systemctl is-system-running; journalctl -b -p warning --no-pager' >"$LOG/guest-boot.txt" 2>&1
tar -C "$SCR/ship" -cf - c | T=120 vm_ssh 'rm -rf /root/c; tar -xf - -C /root'
T=900 vm_ssh 'bash /root/c/inner.sh' >"$LOG/inner.log" 2>&1
echo "check: inner exit $?"
T=120 vm_ssh 'cd /root/c && tar -cf - *.dump *.out *.diff 2>/dev/null' | tar -xf - -C "$SCR/out"
T=300 vm_ssh 'cd /w && tar -cf - repo-restic repo-rustic' | tar -xf - -C "$SCR/out/w"
cp "$LOG/inner.log" "$SCR/out/"
vm_halt
python3 "$HERE/judge.py" "$BIN/restic-bko" "$SCR/out" | { if [[ -n $FILTER ]]; then grep -i -- "$FILTER"; else cat; fi; } | tee "$LOG/verdict.txt"
rc=${PIPESTATUS[0]}
cp "$SCR/out/results.json" "$LOG/" 2>/dev/null; cp "$SCR/out/"*.diff "$SCR/out/"*.out "$SCR/out/"*.dump "$LOG/" 2>/dev/null
cp "$LOG/results.json" "$(dirname "$LOG")/results.json" 2>/dev/null
echo "check: results in $LOG/results.json"
exit "$rc"
