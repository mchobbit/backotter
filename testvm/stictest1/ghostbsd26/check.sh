#!/usr/bin/env bash
# SticTest1 targeted check of ghostbsd26 (GhostBSD 26.2, FreeBSD 15.1 base
# from GhostBSD's base packages; UFS2 root with soft updates, no journal,
# TRIM, POSIX.1e ACLs off on the root, no ZFS pool), adapted from
# freebsd15-ufs's: restic-bko and rustic-bko keep
# every piece of metadata BackOtter needs there, write the same repository
# format and restore each other's snapshots.  Three throwaway (--ram)
# overlays of the guest at once, each through guest-slot.sh and
# cmp-run.sh --reserve:
#   PORT    run-guest-rt.sh restic->restic, restic->rustic (freebsd/
#           roundtrip.sh: torture.sh on the UFS root with POSIX.1e ACLs and
#           on a UFS md disk with NFSv4 ACLs; flags, user+system extattrs,
#           ns times, births, sparse, hard links, FIFOs, symlinks, non-UTF-8,
#           deep paths, setid/sticky, nameless owners, sockets skipped, a
#           restore over a changed tree, node identity, a UFS without ACLs)
#   PORT+1  the same, rustic->rustic, rustic->restic
#   PORT+2  extras.sh (guest's own files, devices, ancestors, restore over
#           changed immutable/append-only files, whole-snapshot restore),
#           then nonroot-rt.sh ghostbsd26 (uid 4321 restores)
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR: roundN/bin/freebsd (restic-bko, rustic-bko); PREFIX names the
#   scratch dir /mnt/bko-ram/PREFIX; NEED_FILTER: a regex of need names.
# Prints PASS/FAIL/LIMIT need: detail; writes results.json in the log dir
# (~/backotter-work/stictest1/ghostbsd26/logs/STAMP, or $STC_LOGS/STAMP);
# exit 0 if nothing FAILs.
set -uo pipefail
[[ $# -ge 3 ]] || { sed -n 2,25p "$0"; exit 2; }
BIN=$(realpath "$1") PORT=$2 PFX=$3 FILTER=${4:-.}
here=$(cd "$(dirname "$0")" && pwd)
W=$HOME/backotter-work C=$W/testvm/compare G=ghostbsd26
STC_LOGS=${STC_LOGS:-$W/stictest1/$G/logs}
LOGS=$STC_LOGS/$(date +%Y%m%d-%H%M%S); mkdir -p "$LOGS"
RAM=/mnt/bko-ram/$PFX; mkdir -p "$RAM/tmp"
export TMPDIR=$RAM/tmp BKO_WORK=$W
[[ -x $BIN/restic-bko && -x $BIN/rustic-bko ]] || { echo "no binaries in $BIN"; exit 2; }
pids=()
cleanup() {
    for p in "${pids[@]}"; do pkill -TERM -P "$p" 2>/dev/null; kill "$p" 2>/dev/null; done
    for p in $PORT $((PORT + 1)) $((PORT + 2)); do
        pkill -f "hostfwd=tcp:127.0.0.1:$p-" 2>/dev/null
    done
    rm -rf "$RAM"
}
trap cleanup EXIT; trap 'exit 1' INT TERM
admit() {  # NAME CMD...: slot + room, then CMD, output to LOGS/NAME.out
    local name=$1; shift
    ( source "$C/guest-slot.sh"
      case $(hostname -s) in chopin) slot_take 3 2700-2799 ;; *) slot_take 3 2900-2999 ;; esac
      HOT_C=90 "$W/cmp-run.sh" --reserve 3 6 "$@"; s=$?; slot_give; exit $s ) >"$LOGS/$name.out" 2>&1
}
start=$SECONDS
want() { local n; for n; do [[ $n =~ $FILTER ]] && return 0; done; return 1; }
want roundtrip-restic- || want snapshot-identity-torture-restic &&
admit rt1 env RT_LOGS="$LOGS/rt-a" RT_TIMEOUT=780 "$C/run-guest-rt.sh" $G "$PORT" "$BIN/restic-bko" "$BIN/rustic-bko" restic-restic restic-rustic & pids+=($!)
want roundtrip-rustic- || want snapshot-identity-torture-rustic &&
admit rt2 env RT_LOGS="$LOGS/rt-b" RT_TIMEOUT=780 "$C/run-guest-rt.sh" $G $((PORT + 1)) "$BIN/restic-bko" "$BIN/rustic-bko" rustic-rustic rustic-restic & pids+=($!)
want snapshot-identity ancestor-nodes-identical restore-restic-bko-restic-bko restore-over-immutable device-nodes guest-own-files ancestor-restore nonroot &&
admit extras bash -c '
    WORK="'"$W"'"; . "'"$W"'/testvm/vm-lib.sh"; trap vm_halt EXIT
    vm_boot '$G' '$((PORT + 2))' "'"$LOGS"'/vm-extras" --ram --memory 5120 || exit 1
    for f in "'"$BIN"'/restic-bko" "'"$BIN"'/rustic-bko" "'"$here"'/extras.sh" "'"$C"'/rt-common.sh" "'"$C"'/freebsd/torture.sh" "'"$C"'/freebsd/fsmeta.sh"; do
        vm_put "$f" "/root/$(basename "$f")" || exit 1; done
    vm_ssh "/root/restic-bko version; /root/rustic-bko --version"
    T=780 vm_ssh "cd /root && sh ./extras.sh /root/restic-bko /root/rustic-bko 2>&1"
    vm_halt
    RESTIC='"$BIN"'/restic-bko RUSTIC='"$BIN"'/rustic-bko NONROOT_LOGS="'"$LOGS"'/nonroot" \
        timeout 600 "'"$C"'/nonroot-rt.sh" '$G' '$((PORT + 2))'
' & pids+=($!)
wait
echo "(wall $((SECONDS - start)) s; logs $LOGS)" >&2

# Verdicts, one line per need.
out=()
add() { [[ $2 =~ $FILTER ]] && out+=("$1 $2: $3"); }
for d in restic-restic restic-rustic rustic-rustic rustic-restic; do
    l=$LOGS/rt-a/$G/$d.log; [[ -f $l ]] || l=$LOGS/rt-b/$G/$d.log
    v=$(grep -a '^roundtrip: ' "$l" 2>/dev/null | tail -n 1)
    case $v in
        "roundtrip: PASS") add PASS "roundtrip-${d/-/->}" "torture.sh on UFS root (POSIX.1e) + UFS md (NFSv4): flags, ACLs, user+system extattrs, ns times, births, sparse, links, FIFO, symlinks, non-UTF-8, deep, setid, sockets skipped, restore over changed tree, no-ACL UFS" ;;
        *) add FAIL "roundtrip-${d/-/->}" "${v:-no verdict} ($l)" ;;
    esac
done
for d in restic-rustic rustic-restic; do
    l=$LOGS/rt-a/$G/$d.log; [[ -f $l ]] || l=$LOGS/rt-b/$G/$d.log
    if grep -aA2 '^== snapshot nodes of' "$l" 2>/dev/null | grep -q '^identical'; then
        add PASS "snapshot-identity-torture-${d}" "$(grep -aA2 '^== snapshot nodes of' "$l" | grep -a '^identical' | head -n 1)"
    else add FAIL "snapshot-identity-torture-${d}" "not identical or not run ($l)"; fi
done
el=$LOGS/extras.out
for n in snapshot-identity ancestor-nodes-identical restore-restic-bko-restic-bko restore-restic-bko-rustic-bko \
         restore-rustic-bko-rustic-bko restore-rustic-bko-restic-bko device-nodes restore-over-immutable guest-own-files ancestor-restore; do
    line=$(grep -a "^RESULT $n|" "$el" 2>/dev/null | tail -n 1)
    if [[ -z $line ]]; then add FAIL "$n" "no result ($el)"
    else IFS='|' read -r _ v det <<<"${line#RESULT }"; add "$v" "$n" "$det"; fi
done
nl=$LOGS/nonroot/$G.log
if [[ -f $nl ]]; then
    j=$(python3 "$C/verdict.py" judge nonroot "$nl" 2>&1)
    echo "$j" >"$LOGS/nonroot-verdict.jsonl"
    if [[ -n $j ]] && ! grep -q '"verdict": "FAIL"' <<<"$j" && grep -q '"verdict": "PASS"' <<<"$j"; then
        add PASS nonroot "uid 4321 restores in 4 directions judged PASS by verdict.py"
    else add FAIL nonroot "$(grep -o '"checks": \[[^]]*' <<<"$j" | head -n 2 | cut -c1-300)"; fi
else add FAIL nonroot "no log ($nl)"; fi
add LIMIT "linux-only-attributes" "SELinux labels, Linux inode flags, capabilities, project IDs, security/trusted xattrs: not on FreeBSD UFS"
add LIMIT "birth-time-exact" "no system call sets a birth time: min(birth, mtime) is what both keep (BACKOTTER.md)"
add LIMIT "sujournal" "ghostbsd26's root has soft updates without a journal (no /.sujournal); a journal's flags are tested on a copy"
add LIMIT "zfs-per-file-options" "no ZFS pool on ghostbsd26 (UFS root; freebsd15 covers ZFS)"
printf '%s\n' "${out[@]}"
printf '%s\n' "${out[@]}" | python3 -c '
import json,sys
r=[dict(zip(("verdict","need","detail"),(l.split(" ",1)[0],)+tuple(l.split(" ",1)[1].split(": ",1)))) for l in sys.stdin.read().splitlines()]
json.dump({"guest":"'$G'","bin":"'"$BIN"'","logs":"'"$LOGS"'","results":r},open("'"$LOGS"'/results.json","w"),indent=1)'
cp "$LOGS/results.json" "$STC_LOGS/results.json"
! printf '%s\n' "${out[@]}" | grep -q '^FAIL '
