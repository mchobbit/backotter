#!/usr/bin/env bash
# SticTest1's targeted check for vanilla3 (Vanilla OS 3 "Reunion" as its
# installer lays it out, docs/design/vanillaos.md: GPT vos-boot ext4 /boot,
# vos-efi ESP /boot/efi, PV vos-root (VG vos-root: linear `init` ext4
# vos-init; thin pool `root`, 64 KiB chunks, discards passdown, zeroing,
# lvol0_pmspare; over-provisioned thin LVs root-a and root-b, btrfs vos-a
# and vos-b without subvolumes), PV vos-var (VG vos-var: btrfs vos-var,
# /var); the running root is ro with an overlay on /etc whose upper is
# /var/lib/abroot/etc/vos-X, holding trusted.overlay.* xattrs, and
# /var/lib/abroot/storage holds containers storage's whiteouts (char 0:0)).
# The scratch disk (vdb) holds that layout once per tree, each file system
# made and mounted like the guest's at ABRoot's own points (the present
# root as the tree, /var, /boot, /boot/efi, /boot/init, /part-future); the
# fixture puts an /etc overlay upper made through overlayfs itself
# (whiteouts, an opaque directory, a redirect, origin and impure xattrs)
# and a containers-storage-like layer stack in /var, with the guest's own
# uppers and storage whiteouts beside them.  Each restore's overlays are
# mounted again over the restored layers and their merged views compared
# with the source's: the test of whether trusted.overlay.* survived.
# Never a whole-disk run: the GPT, the LVM stack and the boot loader are
# HoltTest1's (a LIMIT here).
#
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR: restic-bko and rustic-bko for Linux (roundN/bin/linux)
# Prints "PASS|FAIL|LIMIT need: detail", writes results.json beside the
# logs (~/backotter-work/stictest1/vanilla3/logs/PREFIX-*), exits 0 if
# nothing FAILs.  Takes a guest slot and a cmp-run.sh reservation itself.
# STC_SCRATCH_DIR (default /mnt/bko-ram) is where its scratch (QEMU's
# --snapshot overlay, the scratch and work disks) goes; outside the RAM
# disk (/bkoscratch, when the RAM disk is taken) it reserves no RAM disk.
set -u
GUEST=vanilla3
here=$(cd "$(dirname "$0")" && pwd)
WORK=$HOME/backotter-work
[[ $# -ge 3 ]] || { echo "usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PREFIX=$3 FILTER=${4:-}
SCR=${STC_SCRATCH_DIR:-/mnt/bko-ram}
if [[ -z ${BKST_INNER:-} ]]; then
    # shellcheck source=/dev/null
    . "${BKO_TESTVM:-$WORK/testvm}/compare/guest-slot.sh"
    slot_take 3 "$(slot_range_here)"
    if [[ $SCR == /mnt/bko-ram ]]; then BKST_INNER=1 HOT_C=90 "$WORK/cmp-run.sh" --reserve 6 6 "$here/check.sh" "$@"
    else BKST_INNER=1 HOT_C=90 "$WORK/cmp-run.sh" --reserve 0 6 "$here/check.sh" "$@"; fi
    rc=$?
    slot_give
    exit $rc
fi
STC_LOGS=${STC_LOGS:-$WORK/stictest1/$GUEST/logs}
S=$SCR/$PREFIX/$GUEST LOGS=$STC_LOGS/$PREFIX-$(date +%Y%m%d-%H%M%S)
mkdir -p "$S" "$LOGS"
export TMPDIR=$S
. "${BKO_TESTVM:-$WORK/testvm}/vm-lib.sh"
scr_used() {  # MiB this run holds in its scratch: its files and QEMU's --snapshot overlays (deleted, open)
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
cleanup() {  # the room the run took (for --reserve), then everything
    scr_used >"$LOGS/ram-used" 2>&1; vm_halt; rm -rf "$S"; rmdir "$SCR/$PREFIX" 2>/dev/null
}
trap cleanup EXIT
# vdb: the guest-like disk (5 sets of its partitions and one PV more,
# ~17 GiB, sparse); vdc: work (the repositories).
qemu-img create -q -f qcow2 "$S/stack.qcow2" 20G && qemu-img create -q -f qcow2 "$S/work.qcow2" 8G
VM_BOOT_TIMEOUT=400 vm_boot $GUEST "$PORT" "$S/vm" --snapshot --memory 4096 --cpus 2 \
    --disk "$S/stack.qcow2" --disk "$S/work.qcow2" ||
    { echo "FAIL harness: $GUEST didn't boot"; exit 2; }
T=60 vm_ssh "mkdir -p /root/bk"
for f in "$BIN/restic-bko" "$BIN/rustic-bko" "${BKO_TESTVM:-$WORK/testvm}/compare/fsmeta.py" "${BKO_TESTVM:-$WORK/testvm}/compare/rt-common.sh" \
         "${BKO_TESTVM:-$WORK/testvm}/compare/nonroot-inner.sh" "$here/fixture.sh" "$here/inner.sh" "$here/judge.py" "$here/fatattr.py" \
         "$here/gpt.py" "$here/bkoshim.py" "$here/nodecopy.py"; do
    vm_put "$f" "/root/bk/$(basename "$f")" || { echo "FAIL harness: copying $f"; exit 2; }
done
T=3600 vm_ssh "bash /root/bk/inner.sh $GUEST" >"$LOGS/check.out" 2>&1
echo "inner exit $?" >>"$LOGS/check.out"
T=300 vm_ssh "cat /root/bk/logs.tar" 2>"$LOGS/tar.err" | tar xf - -C "$LOGS" 2>>"$LOGS/tar.err"
cp "$S/vm/serial.log" "$LOGS/serial.log" 2>/dev/null
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
if not rows: print("FAIL non-root backup and restore (uid 4321): no verdict: " + open(os.path.join(L, "nonroot.verdict")).read()[:400])
elif bad: print("FAIL non-root backup and restore (uid 4321): " + json.dumps(bad)[:900])
else: print(f"PASS non-root backup and restore (uid 4321): {len(rows)} rows pass (verdict.py judge nonroot)")
PY
grep -E '^(PASS|FAIL|LIMIT) ' "$LOGS/check.out" | { [[ -n $FILTER ]] && grep -E -- "$FILTER" || cat; } | tee "$LOGS/lines.txt"
python3 - "$LOGS" "$GUEST" "$BIN" >"$LOGS/results.json" <<'PY'
import json, sys, os, re
L = sys.argv[1]
out = []
for l in open(os.path.join(L, "lines.txt")):
    m = re.match(r"(PASS|FAIL|LIMIT) (.*?): (.*)", l.rstrip("\n"))
    if m: out.append({"result": m[1], "need": m[2], "detail": m[3]})
json.dump({"guest": sys.argv[2], "bin": sys.argv[3], "logs": L, "results": out}, sys.stdout, indent=1)
PY
cp "$LOGS/results.json" "$STC_LOGS/results.json"
echo "logs: $LOGS"
[[ -s $LOGS/lines.txt ]] || { echo "FAIL harness: no results (see $LOGS/check.out)"; exit 1; }
! grep -q '^FAIL ' "$LOGS/lines.txt"
