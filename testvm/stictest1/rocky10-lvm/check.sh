#!/usr/bin/env bash
# SticTest1's targeted check for rocky10-lvm (Rocky Linux 10 as the
# installer lays it out by itself, autopart --type=lvm on BIOS: GPT with a
# BIOS boot partition, /boot XFS on a partition, volume group rl_rocky10-lvm
# with / XFS and swap; SELinux enforcing).  Based on rocky10's check.  Boots
# a throwaway overlay (vm-run --snapshot) with two spare disks: vdb laid out
# like the guest's disk (GPT, a BIOS boot partition, 1 GiB XFS partitions
# like /boot, a volume group of the rest whose XFS logical volumes are made
# and mounted like /), vdc for the repositories and logs.  The fixture
# (fixture.sh) is on a logical volume, with a /boot-like partition mounted
# inside it (the guest's own /boot files, as real examples, with its own /
# and /etc/lvm ones on the volume), each restore target the same, mounted
# before the restore as BackOtter does.  Runs restic->restic,
# rustic->rustic, restic->rustic, rustic->restic, a restore over existing
# immutable/append-only files with each tool, a backup pair with the source
# on /'s own relatime, nonroot-inner.sh (backup as root, restores as uid
# 4321) on a volume, and judges (judge.py, fsmeta.py, rt-common.sh's node
# comparison).  Never a whole-disk run: the GPT, PV/VG/LV metadata and boot
# loader are HoltTest1's (a LIMIT here).
#
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR: restic-bko and rustic-bko for Linux (roundN/bin/linux)
# Prints "PASS|FAIL|LIMIT need: detail", writes results.json beside the
# logs (~/backotter-work/stictest1/rocky10-lvm/logs/PREFIX-*), exits 0 if
# nothing FAILs.  Takes a guest slot and a cmp-run.sh reservation itself.
set -u
here=$(cd "$(dirname "$0")" && pwd)
WORK=$HOME/backotter-work
[[ $# -ge 3 ]] || { echo "usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PREFIX=$3 FILTER=${4:-}
if [[ -z ${BKST_INNER:-} ]]; then
    case $(hostname -s) in chopin) slots="3 2700-2799" ;; *) slots="3 2900-2999" ;; esac
    # shellcheck source=/dev/null
    . "$WORK/testvm/compare/guest-slot.sh"
    slot_take $slots
    BKST_INNER=1 HOT_C=90 "$WORK/cmp-run.sh" --reserve 2 6 "$here/check.sh" "$@"
    rc=$?
    slot_give
    exit $rc
fi
STC_LOGS=${STC_LOGS:-$WORK/stictest1/rocky10-lvm/logs}
S=/mnt/bko-ram/$PREFIX/rocky10-lvm LOGS=$STC_LOGS/$PREFIX-$(date +%Y%m%d-%H%M%S)
mkdir -p "$S" "$LOGS"
export TMPDIR=$S
. "$WORK/testvm/vm-lib.sh"
ram_used() {  # MiB this run holds in the RAM disk: its files and QEMU's --snapshot overlays (deleted, open)
    local p q k=0 f
    desc() { local c; for c in $(pgrep -P "$1"); do echo "$c"; desc "$c"; done; }
    [[ -n ${VM_PID:-} ]] && for p in $VM_PID $(desc "$VM_PID"); do
        [[ $(cat /proc/$p/comm 2>/dev/null) == qemu* ]] || continue
        for f in /proc/$p/fd/*; do
            [[ $(readlink "$f") == "$S"/*" (deleted)" ]] && q=$(stat -L -c '%b*%B' "$f" 2>/dev/null) && k=$((k + q))
        done
    done
    echo "$(( ($(du -sk "$S" | cut -f1) * 1024 + k) >> 20 )) MiB in $S (overlays $((k >> 20)) MiB)"
}
cleanup() {  # the RAM the run took (for --reserve), then everything
    ram_used >"$LOGS/ram-used" 2>&1; vm_halt; rm -rf "$S"; rmdir "/mnt/bko-ram/$PREFIX" 2>/dev/null
}
trap cleanup EXIT
# vdb: the guest-like disk (partitions and the volume group); vdc: work.
qemu-img create -q -f qcow2 "$S/stack.qcow2" 18G && qemu-img create -q -f qcow2 "$S/work.qcow2" 8G
VM_BOOT_TIMEOUT=300 vm_boot rocky10-lvm "$PORT" "$S/vm" --snapshot --memory 4096 --cpus 2 \
    --disk "$S/stack.qcow2" --disk "$S/work.qcow2" ||
    { echo "FAIL harness: rocky10-lvm didn't boot"; exit 2; }
T=60 vm_ssh "mkdir -p /root/bk"
for f in "$BIN/restic-bko" "$BIN/rustic-bko" "$WORK/testvm/compare/fsmeta.py" "$WORK/testvm/compare/rt-common.sh" \
         "$WORK/testvm/compare/nonroot-inner.sh" "$here/fixture.sh" "$here/inner.sh" "$here/judge.py"; do
    vm_put "$f" "/root/bk/$(basename "$f")" || { echo "FAIL harness: copying $f"; exit 2; }
done
T=900 vm_ssh "bash /root/bk/inner.sh" >"$LOGS/check.out" 2>&1
echo "inner exit $?" >>"$LOGS/check.out"
T=300 vm_ssh "cat /root/bk/logs.tar" 2>"$LOGS/tar.err" | tar xf - -C "$LOGS" 2>>"$LOGS/tar.err"
# The non-root run, judged as run-all.sh judges nonroot-rt.sh's log.
nr=$(python3 "$WORK/testvm/compare/verdict.py" judge nonroot "$LOGS/nonroot.log" --full "$LOGS/nonroot.log" 2>&1)
echo "$nr" >"$LOGS/nonroot.verdict"
python3 - "$LOGS" <<'PY' >>"$LOGS/check.out"
import json, sys, os
L = sys.argv[1]
rows = []
for l in open(os.path.join(L, "nonroot.verdict")):
    try: rows.append(json.loads(l))
    except Exception: pass
bad = [r for r in rows if str(r.get("verdict", r.get("result", ""))).upper() not in ("PASS",)]
if not rows: print("FAIL non-root backup and restore (uid 4321): no verdict: " + open(os.path.join(L, "nonroot.verdict")).read()[:400])
elif bad: print("FAIL non-root backup and restore (uid 4321): " + json.dumps(bad)[:900])
else: print(f"PASS non-root backup and restore (uid 4321): {len(rows)} rows pass (verdict.py judge nonroot)")
PY
grep -E '^(PASS|FAIL|LIMIT) ' "$LOGS/check.out" | { [[ -n $FILTER ]] && grep -E -- "$FILTER" || cat; } | tee "$LOGS/lines.txt"
python3 - "$LOGS" >"$LOGS/results.json" <<'PY'
import json, sys, os, re
L = sys.argv[1]
out = []
for l in open(os.path.join(L, "lines.txt")):
    m = re.match(r"(PASS|FAIL|LIMIT) (.*?): (.*)", l.rstrip("\n"))
    if m: out.append({"result": m[1], "need": m[2], "detail": m[3]})
json.dump({"guest": "rocky10-lvm", "logs": L, "results": out}, sys.stdout, indent=1)
PY
cp "$LOGS/results.json" "$STC_LOGS/results.json"
[[ -s $LOGS/lines.txt ]] || { echo "FAIL harness: no results (see $LOGS/check.out)"; exit 1; }
! grep -q '^FAIL ' "$LOGS/lines.txt"
