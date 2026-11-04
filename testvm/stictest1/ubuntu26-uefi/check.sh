#!/usr/bin/env bash
# SticTest1: ubuntu26-uefi's targeted fixture test: ext4 like the guest's root, an
# ext4 /boot-like disk, its vfat ESP (a copy of /boot/efi) and an ext4 with project quotas and casefolding.
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR: holds restic-bko and rustic-bko for Linux (roundN/bin/linux)
#   INVENTORY=1: also run inv.sh (the guest's inventory) in the same boot
# Boots a throwaway overlay (vm-run --snapshot, TMPDIR in /mnt/bko-ram/PREFIX)
# through guest-slot.sh and cmp-run.sh --reserve, runs inner.sh there (fixture
# on fresh loop ext4s, four directions, restore over immutable files, a
# non-root run, snapshot node identity, each tool checking the other's
# repository), judges with judge.py, prints PASS/FAIL/LIMIT lines, writes
# results.json; exit 0 if nothing FAILs.  Logs: $STC_LOGS/run-TIME/
# (STC_LOGS: ~/backotter-work/stictest1/ubuntu26-uefi/logs by default).
set -uo pipefail
G=ubuntu26-uefi
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$HOME/backotter-work
C=$WORK/testvm/compare
if [[ ${1:-} == --inner ]]; then
    shift; BIN=$1 PORT=$2 PFX=$3 OUT=$4
    . "$WORK/testvm/vm-lib.sh"
    R=/mnt/bko-ram/$PFX/$G.$$
    mkdir -p "$R"
    trap 'vm_halt; rm -rf "$R"; rmdir /mnt/bko-ram/$PFX 2>/dev/null' EXIT; trap 'exit 143' TERM INT
    TMPDIR=$R VM_BOOT_TIMEOUT=300 vm_boot $G "$PORT" "$OUT/vm" --snapshot --memory 3072 || { echo "FAIL boot: $G didn't come up"; exit 1; }
    files=("$BIN/restic-bko" "$BIN/rustic-bko" "$C/torture.sh" "$C/fsmeta.py" "$C/rt-common.sh" "$HERE/inner.sh" "$HERE/judge.py" "$HERE/nracl.py")
    [[ ${INVENTORY:-} == 1 ]] && files+=("$HERE/inv.sh")
    for f in "${files[@]}"; do
        vm_put "$f" "/root/$(basename "$f")" || { echo "FAIL copy: $f"; exit 1; }
    done
    if [[ ${INVENTORY:-} == 1 ]]; then T=600 vm_ssh "sh /root/inv.sh" >"$OUT/inventory.txt" 2>&1; fi
    T=780 vm_sh "bash /root/inner.sh" >"$OUT/guest.out" 2>"$OUT/guest.err"
    echo "inner exit $?" >>"$OUT/guest.err"
    T=60 vm_ssh "cat /root/st1.tgz" >"$OUT/st1.tgz" 2>/dev/null && tar -C "$OUT" -xzf "$OUT/st1.tgz"
    exit 0
fi
[[ $# -ge 3 ]] || { sed -n 2,12p "$0" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PFX=$3 FILTER=${4:-}
for b in restic-bko rustic-bko; do [[ -x $BIN/$b ]] || { echo "FAIL binaries: no $BIN/$b"; exit 1; }; done
STC_LOGS=${STC_LOGS:-$WORK/stictest1/$G/logs}
OUT=$STC_LOGS/run-$(date +%Y%m%d-%H%M%S)
mkdir -p "$OUT"
. "$C/guest-slot.sh"
case $(hostname -s) in chopin) slot_take 2 2700-2799 ;; *) slot_take 3 2900-2999 ;; esac
HOT_C=90 "$WORK/cmp-run.sh" --reserve 3 5 timeout 1080 "$HERE/$(basename "$0")" --inner "$BIN" "$PORT" "$PFX" "$OUT" >"$OUT/run.log" 2>&1
slot_give
lines=$(grep -E '^(PASS|FAIL|LIMIT) ' "$OUT/guest.out" 2>/dev/null)
[[ -n $lines ]] || lines="FAIL check ran: no results (see $OUT/run.log, guest.err)"
lines+=$'\n'"LIMIT Birth times: ext4 keeps them, Linux can't set them (restic BACKOTTER.md unix.birthtime: not on Linux)"
lines+=$'\n'"LIMIT SELinux labels: not applicable; the guest runs AppArmor (186 profiles, 110 enforcing), which keeps no labels on disk"
lines+=$'\n'"LIMIT NFSv4 ACLs: Linux has them only over NFS (system.nfs4_acl); not on a local file system"
[[ -n $FILTER ]] && lines=$(grep -E -- "$FILTER" <<<"$lines")
echo "$lines"
printf '%s\n' "$lines" >"$OUT/lines.txt"
python3 -c '
import json, sys
rows = []
for l in open(sys.argv[1]).read().splitlines():
    v, rest = l.split(" ", 1); need, _, det = rest.partition(": ")
    rows.append({"verdict": v, "need": need, "detail": det})
json.dump({"guest": "ubuntu26-uefi", "bin": sys.argv[3], "results": rows}, open(sys.argv[2], "w"), indent=1)' "$OUT/lines.txt" "$OUT/results.json" "$BIN"
cp "$OUT/results.json" "$STC_LOGS/results.json" 2>/dev/null
echo "logs: $OUT"
! grep -q '^FAIL ' <<<"$lines"
