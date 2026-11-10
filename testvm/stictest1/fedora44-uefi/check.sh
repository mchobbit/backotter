#!/usr/bin/env bash
# SticTest1's targeted check for fedora44-uefi (Fedora 44 Server: btrfs / with
# compress=zstd:1 and subvolumes, ext4 /boot, SELinux enforcing): boots a
# throwaway overlay (vm-run --snapshot) with a spare disk, builds the
# fixture (fixture.sh) on fresh btrfs and ext4 file systems made like the
# guest's (inner.sh), and for each runs restic->restic, rustic->rustic, restic->rustic,
# rustic->restic, a restore over existing immutable/append-only files with
# each tool, nonroot-inner.sh (backup as root, restores as uid 4321), and
# judges (judge.py, fsmeta.py, rt-common.sh's node comparison).
#
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR: restic-bko and rustic-bko for Linux (roundN/bin/linux)
# Prints "PASS|FAIL|LIMIT need: detail", writes results.json beside the
# logs (~/backotter-work/stictest1/fedora44-uefi/logs/PREFIX-*), exits 0 if
# nothing FAILs.  Takes a guest slot and a cmp-run.sh reservation itself.
set -u
here=$(cd "$(dirname "$0")" && pwd)
WORK=$HOME/backotter-work
[[ $# -ge 3 ]] || { echo "usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PREFIX=$3 FILTER=${4:-}
if [[ -z ${BKST_INNER:-} ]]; then
    case $(hostname -s) in chopin) slots="3 2700-2799" ;; *) slots="3 2900-2999" ;; esac
    # shellcheck source=/dev/null
    . "${BKO_TESTVM:-$WORK/testvm}/compare/guest-slot.sh"
    slot_take $slots
    BKST_INNER=1 HOT_C=90 "$WORK/cmp-run.sh" --reserve 4 6 "$here/check.sh" "$@"
    rc=$?
    slot_give
    exit $rc
fi
STC_LOGS=${STC_LOGS:-$WORK/stictest1/fedora44-uefi/logs}
S=/mnt/bko-ram/$PREFIX/fedora44-uefi LOGS=$STC_LOGS/$PREFIX-$(date +%Y%m%d-%H%M%S)
mkdir -p "$S" "$LOGS"
export TMPDIR=$S
. "${BKO_TESTVM:-$WORK/testvm}/vm-lib.sh"
cleanup() { vm_halt; rm -rf "$S"; rmdir "/mnt/bko-ram/$PREFIX" 2>/dev/null; }
trap cleanup EXIT
qemu-img create -q -f qcow2 "$S/spare.qcow2" 16G
VM_BOOT_TIMEOUT=300 vm_boot fedora44-uefi "$PORT" "$S/vm" --snapshot --memory 4096 --cpus 2 --disk "$S/spare.qcow2" ||
    { echo "FAIL harness: fedora44-uefi didn't boot"; exit 2; }
T=60 vm_ssh "mkdir -p /root/bk"
for f in "$BIN/restic-bko" "$BIN/rustic-bko" "${BKO_TESTVM:-$WORK/testvm}/compare/fsmeta.py" "${BKO_TESTVM:-$WORK/testvm}/compare/rt-common.sh" \
         "${BKO_TESTVM:-$WORK/testvm}/compare/nonroot-inner.sh" "$here/fixture.sh" "$here/inner.sh" "$here/judge.py" "$here/fatattr.py"; do
    vm_put "$f" "/root/bk/$(basename "$f")" || { echo "FAIL harness: copying $f"; exit 2; }
done
T=780 vm_ssh "bash /root/bk/inner.sh" >"$LOGS/check.out" 2>&1
rc=$?
echo "inner exit $rc" >>"$LOGS/check.out"
if [[ $rc -eq 124 ]]; then
    # Out of time: inner.sh still runs in the guest and wrote no logs.tar.  Stop
    # it ([i]: not this command's own line), judge the file system passes it
    # finished, and tar what it logged, so the partial results aren't lost.
    echo "FAIL harness: inner.sh didn't finish within 780 s (partial logs in $LOGS)" >>"$LOGS/check.out"
    T=240 vm_ssh 'pkill -f "bash /root/bk/[i]nner.sh"; pkill -f "[r]estic-bko|[r]ustic-bko|[n]onroot-inner.sh"; sleep 2
        for FS in btrfs xfs vfat; do
            grep -q "^done 0" /mnt/bkw/logs/$FS/status 2>/dev/null && timeout 60 python3 /root/bk/judge.py /mnt/bkw/logs $FS
        done
        python3 -c "import tarfile; t = tarfile.open(\"/root/bk/logs.tar\", \"w\"); t.add(\"/mnt/bkw/logs\", arcname=\".\"); t.close()"' >>"$LOGS/check.out" 2>&1
fi
T=300 vm_ssh "cat /root/bk/logs.tar" 2>"$LOGS/tar.err" | tar xf - -C "$LOGS" 2>>"$LOGS/tar.err"
# The non-root run, judged as run-all.sh judges nonroot-rt.sh's log.
nr=$(python3 "${BKO_TESTVM:-$WORK/testvm}/compare/verdict.py" judge nonroot "$LOGS/nonroot.log" --full "$LOGS/nonroot.log" 2>&1)
echo "$nr" >"$LOGS/nonroot.verdict"
python3 - "$LOGS" <<'PY' >>"$LOGS/check.out"
import json, sys, os
L = sys.argv[1]
rows = []
for l in open(os.path.join(L, "nonroot.verdict")):
    try: rows.append(json.loads(l))
    except Exception: pass
bad = [r for r in rows if str(r.get("verdict", r.get("result", ""))).upper() not in ("PASS",)]
if not rows: print("FAIL [btrfs] non-root backup and restore (uid 4321): no verdict: " + open(os.path.join(L, "nonroot.verdict")).read()[:400])
elif bad: print("FAIL [btrfs] non-root backup and restore (uid 4321): " + json.dumps(bad)[:900])
else: print(f"PASS [btrfs] non-root backup and restore (uid 4321): {len(rows)} rows pass (verdict.py judge nonroot)")
PY
grep -E '^(PASS|FAIL|LIMIT) ' "$LOGS/check.out" | { [[ -n $FILTER ]] && grep -E -- "$FILTER" || cat; } | tee "$LOGS/lines.txt"
python3 - "$LOGS" >"$LOGS/results.json" <<'PY'
import json, sys, os, re
L = sys.argv[1]
out = []
for l in open(os.path.join(L, "lines.txt")):
    m = re.match(r"(PASS|FAIL|LIMIT) (.*?): (.*)", l.rstrip("\n"))
    if m: out.append({"result": m[1], "need": m[2], "detail": m[3]})
json.dump({"guest": "fedora44-uefi", "logs": L, "results": out}, sys.stdout, indent=1)
PY
cp "$LOGS/results.json" "$STC_LOGS/results.json"
[[ -s $LOGS/lines.txt ]] || { echo "FAIL harness: no results (see $LOGS/check.out)"; exit 1; }
! grep -q '^FAIL ' "$LOGS/lines.txt"
