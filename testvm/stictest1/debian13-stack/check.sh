#!/usr/bin/env bash
# SticTest1: debian13-stack's targeted fixture test (Debian 13, UEFI with
# Secure Boot, two GPT disks: an ESP on each, /boot ext4 on md RAID1, LUKS2
# on md RAID1 holding LVM with / ext4 and /srv XFS; make-debian-stack.sh).
# The fixture is built on the guest's own storage layers, made afresh on two
# spare disks: FAT32 ESPs (one per disk), an ext4 /boot on md RAID1, and md
# RAID1 -> LUKS2 -> LVM with LVs for an ext4 / and an XFS /srv (and an ext4
# with project quotas and casefolding); see inner.sh.
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR: holds restic-bko and rustic-bko for Linux (roundN/bin/linux)
#   INVENTORY=1: also run inv.sh (the guest's inventory) in the same boot
# Boots a throwaway overlay (vm-run --snapshot, TMPDIR in /mnt/bko-ram/PREFIX)
# with two spare disks through guest-slot.sh and cmp-run.sh --reserve (the
# initramfs's LUKS prompt: vm_boot types console-passphrase), runs inner.sh
# there (fixture, four directions, restore over immutable files, a non-root
# run, snapshot node identity, each tool checking the other's repository),
# judges with judge.py, prints PASS/FAIL/LIMIT lines, writes results.json;
# exit 0 if nothing FAILs.  Logs: $STC_LOGS/run-TIME/
# (STC_LOGS: ~/backotter-work/stictest1/debian13-stack/logs by default).
set -uo pipefail
G=debian13-stack
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$HOME/backotter-work
C=${BKO_TESTVM:-$WORK/testvm}/compare
SPARE=26G      # each spare disk; inner.sh picks them by this size (the guest's own are 12 GiB)
if [[ ${1:-} == --inner ]]; then
    shift; BIN=$1 PORT=$2 PFX=$3 OUT=$4
    . "${BKO_TESTVM:-$WORK/testvm}/vm-lib.sh"
    R=/mnt/bko-ram/$PFX/$G.$$
    mkdir -p "$R"
    # What the run used of the RAM disk: the spare disks and QEMU's snapshot
    # overlay (unlinked, so counted through QEMU's open files), sampled.
    peak() {
        local m=0 k q
        while sleep 10; do
            k=$(du -sk "$R" 2>/dev/null | cut -f1)
            for q in $(pgrep -f "^qemu-system.*:$PORT-:22"); do
                for f in /proc/$q/fd/*; do
                    [[ $(readlink "$f") == "$R/"*"(deleted)" ]] && k=$((k + $(stat -L -c %b "$f" 2>/dev/null || echo 0) / 2))
                done
            done
            ((k > m)) && m=$k && echo "$m" >"$OUT/ramdisk-peak-kib"
        done
    }
    peak & PEAK=$!
    trap 'kill $PEAK 2>/dev/null; vm_halt; rm -rf "$R"; rmdir /mnt/bko-ram/$PFX 2>/dev/null' EXIT; trap 'exit 143' TERM INT
    qemu-img create -q -f qcow2 "$R/spare-a.qcow2" $SPARE && qemu-img create -q -f qcow2 "$R/spare-b.qcow2" $SPARE ||
        { echo "FAIL harness: can't make the spare disks"; exit 1; }
    # The boot asks for the LUKS passphrase (Argon2id, 256 MiB): about 20 s here.
    TMPDIR=$R VM_BOOT_TIMEOUT=420 vm_boot $G "$PORT" "$OUT/vm" --snapshot --memory 3072 \
        --disk "$R/spare-a.qcow2" --disk "$R/spare-b.qcow2" || { echo "FAIL boot: $G didn't come up"; exit 1; }
    files=("$BIN/restic-bko" "$BIN/rustic-bko" "$C/torture.sh" "$C/fsmeta.py" "$C/rt-common.sh" "$HERE/inner.sh" "$HERE/judge.py"
           "$HERE/nracl.py" "$HERE/fixture.sh" "$HERE/bkoshim.py" "$HERE/cpxattr.py" "$HERE/fatattr.py")
    [[ ${INVENTORY:-} == 1 ]] && files+=("$HERE/inv.sh")
    for f in "${files[@]}"; do
        vm_put "$f" "/root/$(basename "$f")" || { echo "FAIL copy: $f"; exit 1; }
    done
    if [[ ${INVENTORY:-} == 1 ]]; then T=600 vm_ssh "sh /root/inv.sh" >"$OUT/inventory.txt" 2>&1; fi
    T=1200 vm_sh "bash /root/inner.sh $(numfmt --from=iec $SPARE)" >"$OUT/guest.out" 2>"$OUT/guest.err"
    echo "inner exit $?" >>"$OUT/guest.err"
    T=60 vm_ssh "cat /root/st1.tgz" >"$OUT/st1.tgz" 2>/dev/null && tar -C "$OUT" -xzf "$OUT/st1.tgz"
    exit 0
fi
[[ $# -ge 3 ]] || { sed -n 9,11p "$0" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PFX=$3 FILTER=${4:-}
for b in restic-bko rustic-bko; do [[ -x $BIN/$b ]] || { echo "FAIL binaries: no $BIN/$b"; exit 1; }; done
STC_LOGS=${STC_LOGS:-$WORK/stictest1/$G/logs}
OUT=$STC_LOGS/run-$(date +%Y%m%d-%H%M%S)
mkdir -p "$OUT"
. "$C/guest-slot.sh"
case $(hostname -s) in chopin) slot_take 3 2700-2799 ;; *) slot_take 3 2900-2999 ;; esac
HOT_C=90 "$WORK/cmp-run.sh" --reserve 7 5 timeout 1560 "$HERE/$(basename "$0")" --inner "$BIN" "$PORT" "$PFX" "$OUT" >"$OUT/run.log" 2>&1
rc=$?
slot_give
lines=$(grep -E '^(PASS|FAIL|LIMIT) ' "$OUT/guest.out" 2>/dev/null)
if [[ -z $lines ]]; then
    lines="FAIL check ran: no results (see $OUT/run.log, guest.err)"
else
    # A run cut short (vm_sh's or the outer timeout, a guest that stopped
    # answering) leaves only its first lines: it ran to the end only if
    # inner.sh exited 0 and the judge printed its last need.
    grep -qx 'inner exit 0' "$OUT/guest.err" 2>/dev/null ||
        lines+=$'\n'"FAIL check ran: inner.sh didn't finish ($(tail -n 1 "$OUT/guest.err" 2>/dev/null); see $OUT/run.log)"
    grep -qE '^(PASS|FAIL) Non-root backup and restore' <<<"$lines" ||
        lines+=$'\n'"FAIL check ran: no judge result for the non-root run, the last need (see $OUT/guest.err)"
    ((rc == 0)) || lines+=$'\n'"FAIL check ran: the VM run exited $rc (124: the 1560 s timeout; see $OUT/run.log)"
fi
lines+=$'\n'"LIMIT Birth times: ext4 and XFS keep them (FAT its creation time), Linux can't set them (restic BACKOTTER.md unix.birthtime: not on Linux)"
lines+=$'\n'"LIMIT SELinux labels: not applicable; the guest runs AppArmor (3 of 102 profiles enforcing; TOMOYO built in, no policy), which keep no labels on disk"
lines+=$'\n'"LIMIT NFSv4 ACLs: Linux has them only over NFS (system.nfs4_acl); not on a local file system"
lines+=$'\n'"LIMIT Whole-disk layout (GPT, both ESPs, md RAID1 arrays, LUKS2 header and passphrase, LVM volume group, swap, GRUB and shim under Secure Boot): not in a file-level backup; recreating them is BackOtter's restore's job, covered by HoltTest1D (testvm/holttest1d/README.md), not here"
[[ -n $FILTER ]] && lines=$(grep -E -- "$FILTER" <<<"$lines")
echo "$lines"
printf '%s\n' "$lines" >"$OUT/lines.txt"
python3 -c '
import json, sys
rows = []
for l in open(sys.argv[1]).read().splitlines():
    v, rest = l.split(" ", 1); need, _, det = rest.partition(": ")
    rows.append({"verdict": v, "need": need, "detail": det})
json.dump({"guest": "debian13-stack", "bin": sys.argv[3], "results": rows}, open(sys.argv[2], "w"), indent=1)' "$OUT/lines.txt" "$OUT/results.json" "$BIN"
cp "$OUT/results.json" "$STC_LOGS/results.json" 2>/dev/null
echo "logs: $OUT"
! grep -q '^FAIL ' <<<"$lines"
