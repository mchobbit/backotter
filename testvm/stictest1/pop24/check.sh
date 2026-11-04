#!/usr/bin/env bash
# SticTest1: the targeted fixture test of restic-bko and rustic-bko on pop24
# (Pop!_OS 24.04 LTS, COSMIC, Ubuntu noble base, kernel 6.16, ext4 root, vfat ESP
# at /boot/efi, UEFI systemd-boot/kernelstub, AppArmor).
#
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR  holds restic-bko and rustic-bko for Linux (roundN/bin/linux)
#   PORT     the guest's SSH port (yours)
#   PREFIX   your name prefix: scratch in /mnt/bko-ram/PREFIX/check-pop24
#   NEED_FILTER  a regex: only needs matching it are run/reported
# INVENTORY=1 runs inv.sh (the guest's inventory) first, in the same boot.
#
# Boots a throwaway overlay of ~/backotter-work/vms/pop24 (vm-run --snapshot,
# overlay in RAM), within a guest slot and cmp-run.sh --reserve 3 6 (3 GiB guest), and runs
# inner.sh there: the fixture on fresh scratch file systems of the guest's own
# types (ext4 made like the root, ext4 with project and casefold, tmpfs as
# /run, on loop devices or tmpfs; then a vfat ESP holding a copy of /boot/efi,
# mounted with the guest's options), backups with both tools, restores in all four
# directions into fresh file systems, restores again over each restored tree
# (immutable and append-only files in it), one non-root run, fsmeta.py
# comparisons and the two tools' snapshots compared node by node.  Prints
# "PASS need: detail", "FAIL need: detail" or "LIMIT need: reason" per
# checklist item, writes results.json and the logs to
# ~/backotter-work/stictest1/pop24/logs/, and exits 0 if nothing FAILs.
set -uo pipefail
[[ $# -ge 3 ]] || { echo "usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]" >&2; exit 2; }
GUEST=pop24
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${BKO_WORK:-$HOME/backotter-work}
C=$WORK/testvm/compare
BIN=$(cd "$1" && pwd); PORT=$2; PREFIX=$3; FILTER=${4:-.}
LOGS=${STC_LOGS:-$WORK/stictest1/$GUEST/logs}; mkdir -p "$LOGS"
for f in restic-bko rustic-bko; do [[ -x $BIN/$f ]] || { echo "check: no $BIN/$f" >&2; exit 2; }; done
[[ -f $WORK/vms/$GUEST/$GUEST.qcow2 ]] || { echo "check: no guest $GUEST" >&2; exit 2; }

if [[ ${STC_INNER:-} != "$PREFIX" ]]; then
    # Admission: a guest slot, then room (RAM disk, memory, temperature).
    # shellcheck source=/dev/null
    . "$C/guest-slot.sh"
    case $(hostname -s) in
        cinqcent) slot_take 3 2900-2999 ;;
        *) slot_take 2 2700-2799 ;;
    esac
    STC_INNER=$PREFIX HOT_C=90 "$WORK/cmp-run.sh" --reserve 3 6 env STC_INNER="$PREFIX" "$HERE/$(basename "$0")" "$BIN" "$PORT" "$PREFIX" "$FILTER"
    rc=$?
    slot_give
    exit $rc
fi

SCR=/mnt/bko-ram/$PREFIX/check-$GUEST
mkdir -p "$SCR"
# shellcheck source=/dev/null
. "$WORK/testvm/vm-lib.sh"
cleanup() { vm_halt; rm -rf "$SCR"; }
trap cleanup EXIT
trap 'exit 130' INT TERM
start=$SECONDS
echo "check: booting a throwaway $GUEST on port $PORT"
TMPDIR=$SCR MEMORY=3072 vm_boot "$GUEST" "$PORT" "$LOGS/boot" --snapshot ||
    { echo "FAIL boot: $GUEST didn't come up"; exit 1; }
vm_ssh 'mkdir -p /root/st'
for f in "$BIN/restic-bko" "$BIN/rustic-bko" "$C/fsmeta.py" "$C/rt-common.sh" "$HERE/inner.sh" "$HERE/inv.sh"; do
    vm_put "$f" "/root/st/$(basename "$f")" || { echo "FAIL boot: copying $f"; exit 1; }
done
vm_ssh '/root/st/restic-bko version; /root/st/rustic-bko --version' | tee "$LOGS/versions.txt"
# INVENTORY=1: the guest's inventory first, in the same boot (logs/inventory.log).
[[ -n ${INVENTORY:-} ]] && T=300 vm_ssh 'sh /root/st/inv.sh' >"$LOGS/inventory.log" 2>&1
# INVENTORY=only: the inventory alone, no fixture.
[[ ${INVENTORY:-} == only ]] && { echo "check: inventory in $LOGS/inventory.log"; exit 0; }
T=840 vm_ssh "FSLIST='${FSLIST:-ext4 ext4+ tmpfs}' FILTER='$FILTER' bash /root/st/inner.sh" >"$LOGS/inner.log" 2>&1
irc=$?
echo "check: inner.sh exit $irc after $((SECONDS - start)) s (log $LOGS/inner.log)"
python3 - "$LOGS/inner.log" "$LOGS/results.json" "$irc" "$BIN" <<'PY'
import json, sys
order, res = [], {}
for line in open(sys.argv[1], errors="replace"):
    p = line.rstrip("\n").split("\t")
    if len(p) != 4 or p[0] != "CK":
        continue
    _, need, st, det = p
    if need not in res:
        order.append(need); res[need] = {"PASS": [], "FAIL": [], "LIMIT": []}
    res[need][st].append(det)
out, bad = [], False
for need in order:
    r = res[need]
    st = "FAIL" if r["FAIL"] else "PASS" if r["PASS"] else "LIMIT"
    bad |= st == "FAIL"
    det = r[st]
    summary = "; ".join(det[:4]) + (f"; ... ({len(det)} in all)" if len(det) > 4 else "")
    if st == "PASS" and r["LIMIT"]:
        summary += " | limits: " + "; ".join(r["LIMIT"][:3])
    print(f"{st} {need}: {summary}")
    out.append({"need": need, "result": st, "details": det, "limits": r["LIMIT"]})
done = any("inner: done" in l for l in open(sys.argv[1], errors="replace"))
if not done or sys.argv[3] != "0":
    print(f"FAIL harness: inner.sh exit {sys.argv[3]}, finished: {done}"); bad = True
json.dump({"guest": "pop24", "bin": sys.argv[4], "needs": out, "inner_exit": int(sys.argv[3]),
           "verdict": "FAIL" if bad else "PASS"}, open(sys.argv[2], "w"), indent=1)
sys.exit(1 if bad else 0)
PY
