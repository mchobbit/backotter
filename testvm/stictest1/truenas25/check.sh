#!/usr/bin/env bash
# SticTest1's targeted check for truenas25 (TrueNAS Community Edition
# 25.10: Debian 12 with TrueNAS's 6.12 kernel and OpenZFS 2.3, glibc, GNU,
# systemd, zsh for root; apt off, / and /usr read-only, /tmp and /mnt
# noexec; UEFI, GPT as its installer makes it: a BIOS boot partition, a
# 512 MiB ESP that nothing mounts, ZFS boot pool boot-pool on one disk
# (compatibility=grub2, ashift 12, compression=on relatime=on acltype=off
# devices=off), three boot environments boot-pool/ROOT/25.10.N, each a
# legacy readonly=on root with 15 noauto children (usr, opt and conf
# readonly=on; var/log/journal acltype=posix; per-child exec, setuid,
# devices and atime), boot-pool/grub at /boot/grub and the system dataset
# .system at /var/db/system; the middleware's config database
# /data/freenas-v1.db; docs/design/truenas.md).  proxmox9's design (one
# inner.sh, fixture.sh and judge.py reading the layout from the guest) and
# its bkoshim.py, standing in for setfattr and setfacl should the guest
# lack them (TrueNAS 25.10 has both; apt is off).  Boots a throwaway overlay (vm-run
# --snapshot) with a pool scratch disk for each of the guest's disks (10
# GiB) and a work disk (6 GiB) for the repositories, logs and the
# non-root run's binaries.  In the guest (inner.sh) one scratch pool per
# tree made like boot-pool: its vdev layout on a partition of its pool
# partition type, ashift, compatibility, every feature it has (zpool
# create -d, each enabled), its create-time properties, its datasets in
# order with their local properties (but the inactive boot environments,
# HoltTest1's), each mounted where the guest mounts it (the active BE's
# root as the tree's file system, the others inside the tree, legacy ones
# by mount -t zfs).  readonly=on held back until the dataset is filled,
# as bko-holt's load does (truenas.md Decision 3).  The fixture
# (fixture.sh) on every dataset (the guest's own files from each as real
# examples, the config database among them); each restore target a fresh
# pool, its datasets mounted before the restore as BackOtter does.  Runs
# restic->restic, rustic->rustic, restic->rustic, rustic->restic, a
# restore over existing immutable/append-only files and DOS attributes
# with each tool, a backup pair on relatime, nonroot-inner.sh (backup as
# root, restores as uid 4321) on a dataset like the BE's root, and judges
# (judge.py, fsmeta.py, rt-common.sh's node comparison, SQLite's
# integrity check).  Never a whole-disk run: the GPT, the ESP, GRUB, the
# pool itself and the inactive boot environments are HoltTest1's (a LIMIT
# here).
#
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR: restic-bko and rustic-bko for Linux (roundN/bin/linux)
# Prints "PASS|FAIL|LIMIT need: detail", writes results.json beside the
# logs (~/backotter-work/stictest1/truenas25/logs/PREFIX-*), exits 0 if
# nothing FAILs.  Takes a guest slot and a cmp-run.sh reservation itself.
set -u
GUEST=truenas25
here=$(cd "$(dirname "$0")" && pwd)
WORK=$HOME/backotter-work
[[ $# -ge 3 ]] || { echo "usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PREFIX=$3 FILTER=${4:-}
if [[ -z ${BKST_INNER:-} ]]; then
    # shellcheck source=/dev/null
    . "${BKO_TESTVM:-$WORK/testvm}/compare/guest-slot.sh"
    slot_take 3 "$(slot_range_here)"
    BKST_INNER=1 HOT_C=90 "$WORK/cmp-run.sh" --reserve 4 7 "$here/check.sh" "$@"
    rc=$?
    slot_give
    exit $rc
fi
STC_LOGS=${STC_LOGS:-$WORK/stictest1/$GUEST/logs}
S=/mnt/bko-ram/$PREFIX/$GUEST LOGS=$STC_LOGS/$PREFIX-$(date +%Y%m%d-%H%M%S)
mkdir -p "$S" "$LOGS"
export TMPDIR=$S
. "${BKO_TESTVM:-$WORK/testvm}/vm-lib.sh"
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
# A pool scratch disk (10 GiB) for each of the guest's own disks (its
# image and vm-run-options' --disk ones), then the work disk (6 GiB); inner.sh tells them by their sizes.
nd=$((1 + $(grep -o -- '--disk ' "$WORK/vms/$GUEST/vm-run-options" 2>/dev/null | wc -l)))
disks=()
for ((i = 0; i < nd; i++)); do
    qemu-img create -q -f qcow2 "$S/pool$i.qcow2" 10G && disks+=(--disk "$S/pool$i.qcow2")
done
qemu-img create -q -f qcow2 "$S/work.qcow2" 6G
VM_BOOT_TIMEOUT=600 vm_boot $GUEST "$PORT" "$S/vm" --snapshot --memory 6144 --cpus 2 \
    "${disks[@]}" --disk "$S/work.qcow2" ||
    { echo "FAIL harness: $GUEST didn't boot"; exit 2; }
T=60 vm_ssh "mkdir -p /root/bk"
for f in "$BIN/restic-bko" "$BIN/rustic-bko" "${BKO_TESTVM:-$WORK/testvm}/compare/fsmeta.py" "${BKO_TESTVM:-$WORK/testvm}/compare/rt-common.sh" \
         "${BKO_TESTVM:-$WORK/testvm}/compare/nonroot-inner.sh" "$here/fixture.sh" "$here/inner.sh" "$here/judge.py" "$here/bkoshim.py"; do
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
python3 - "$LOGS" "$GUEST" >"$LOGS/results.json" <<'PY'
import json, sys, os, re
L = sys.argv[1]
out = []
for l in open(os.path.join(L, "lines.txt")):
    m = re.match(r"(PASS|FAIL|LIMIT) (.*?): (.*)", l.rstrip("\n"))
    if m: out.append({"result": m[1], "need": m[2], "detail": m[3]})
json.dump({"guest": sys.argv[2], "logs": L, "results": out}, sys.stdout, indent=1)
PY
cp "$LOGS/results.json" "$STC_LOGS/results.json"
[[ -s $LOGS/lines.txt ]] || { echo "FAIL harness: no results (see $LOGS/check.out)"; exit 1; }
! grep -q '^FAIL ' "$LOGS/lines.txt"
