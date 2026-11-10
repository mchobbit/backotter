#!/usr/bin/env bash
# SticTest1 targeted check of omnios58 (OmniOS r151058, illumos; ZFS root
# pool rpool, NFSv4 ACLs, system attributes, named attribute files; tmpfs
# /tmp and /var/run).
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR  holds restic-bko and rustic-bko for illumos (roundN/bin/omnios)
#   PORT     SSH port of the throwaway guests;  PREFIX  scratch name
#   NEED_FILTER  an extended regex: only checklist lines matching it count
# Two throwaway overlays in turn (vm-run --snapshot, TMPDIR=/mnt/bko-ram/PREFIX),
# in a guest slot and a cmp-run.sh --reserve room.  In the first:
#   - testvm/compare/illumos/roundtrip.sh in all four directions, its source
#     and restore target each a fresh ZFS dataset of rpool (system attributes
#     incl. immutable/appendonly/nounlink/av_quarantined and cleared
#     defaults, NFSv4 ACLs incl. SID entries, SID owners, named attributes
#     with their own owner/mode/times/ACL, crtime, ZFS project IDs, ns
#     times, hard links, symlink times, FIFO, sparse, socket skipped,
#     non-UTF-8 names, set-id/sticky, no-name owners; restore over a changed
#     tree; idmap unreachable at restore and backup; both tools' snapshots
#     node by node)
#   - extra.sh (next to this): the guest's real files, device nodes (rdev 0),
#     deep/empty/long names, ancestors' metadata, restore over immutable/
#     appendonly/nounlink files left set, restores onto tmpfs
#   - nonroot-inner.sh (one non-root restore, 4 directions), judged by verdict.py
# In the second (a fresh boot: idmap's ephemeral ids are new): each
# direction's repository restored again (roundtrip.sh --again), SIDs by SID.
# Prints "PASS|FAIL|LIMIT need: detail", writes results.json, exit 0 unless a FAIL.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
WORK=~/backotter-work C=${BKO_TESTVM:-$WORK/testvm}/compare
GUEST=omnios58
DIRS="restic-restic rustic-rustic restic-rustic rustic-restic"
# Before each direction: the round trip's trees gone (system attributes that
# stop removal cleared first), its source and restore target fresh datasets.
CLEAN='P=$(zfs list -H -o name / | cut -d/ -f1)
    for d in /root/bko-rt-quar /root/bko-rt-quar-restored /root/bko-rt-noidmap /root/bko-rt-noidmapb; do
        [ -e $d ] && find $d ! -type l -exec /usr/bin/chmod S-v"{immutable,appendonly,nounlink,readonly,av_quarantined}" {} \; 2>/dev/null
        rm -rf $d; done
    zfs destroy -r $P/bkosp-rt 2>/dev/null; rm -rf /root/bko-rt /root/bko-rt-restored /root/bko-rt-repo* /tmp/bko-rt*
    zfs create -o mountpoint=none $P/bkosp-rt &&
    zfs create -o mountpoint=/root/bko-rt -o atime=off $P/bkosp-rt/src &&
    zfs create -o mountpoint=/root/bko-rt-restored -o atime=off $P/bkosp-rt/dst'
if [[ ${1:-} == --inner ]]; then
    shift; BIN=$1 PORT=$2 PREFIX=$3 L=$4
    export TMPDIR=/mnt/bko-ram/$PREFIX
    . ${BKO_TESTVM:-$WORK/testvm}/vm-lib.sh
    trap vm_halt EXIT
    put_all() {
        vm_ssh 'mkdir -p /root/st1' || return 1
        for f in $BIN/restic-bko $BIN/rustic-bko $C/illumos/roundtrip.sh $C/rt-common.sh $C/nonroot-inner.sh $here/extra.sh; do
            vm_put $f /root/st1/$(basename $f) || return 1
        done
        vm_ssh 'chmod 755 /root/st1/*'
    }
    VM_BOOT_TIMEOUT=300 vm_boot $GUEST $PORT $L/vm --snapshot --memory 4096 || { echo "BOOT FAILED" >$L/boot.fail; exit 1; }
    put_all || { echo "PUT FAILED" >$L/boot.fail; exit 1; }
    vm_ssh 'uname -a; /root/st1/restic-bko version; /root/st1/rustic-bko --version' >$L/versions.txt 2>&1
    # The guest's inventory (inv-guest.sh, inv-scan.py next to this), first,
    # before anything of ours is on its disk, with INVENTORY=1 (first run only).
    if [[ ${INVENTORY:-0} == 1 && -f $here/inv-guest.sh ]]; then
        vm_put $here/inv-scan.py /root/inv-scan.py && vm_put $here/inv-guest.sh /root/inv-guest.sh &&
            T=600 vm_ssh 'ksh /root/inv-guest.sh 2>&1' >$L/inventory.txt 2>&1
    fi
    for d in $DIRS; do
        vm_sh "$CLEAN" >$L/clean-$d.log 2>&1
        T=360 vm_ssh "cd /root/st1 && ./roundtrip.sh /root/st1/${d%-*}-bko /root/st1/${d#*-}-bko 2>&1" >$L/rt-$d.log 2>&1
        T=120 vm_ssh 'cd / && tar -cf - root/bko-rt-repo tmp/bko-rt.before*' >$L/rt-$d.tar 2>/dev/null
    done
    T=420 vm_ssh 'ksh /root/st1/extra.sh /root/st1 2>&1' >$L/extra.log 2>&1
    vm_sh 'zfs create -o mountpoint=/bkonr -o atime=off "$(zfs list -H -o name / | cut -d/ -f1)/bkonr" &&
           groupadd -g 4321 bkonr && useradd -u 4321 -g 4321 -s /bin/sh -d /nonexistent bkonr && chmod 755 /bkonr &&
           cp /root/st1/restic-bko /root/st1/rustic-bko /bkonr/ && cp /root/st1/rt-common.sh /root/rt-common.sh &&
           cp /root/st1/nonroot-inner.sh /root/nonroot-inner.sh' >$L/nonroot-setup.log 2>&1
    { echo "######## $GUEST: $(vm_ssh '/bkonr/restic-bko version' | head -n 1), $(vm_ssh '/bkonr/rustic-bko --version')"
      T=420 vm_ssh 'sh /root/nonroot-inner.sh /bkonr/t /bkonr/restic-bko /bkonr/rustic-bko 2>&1' 2>&1; } >$L/nonroot.log 2>&1
    vm_halt
    # The second boot: each repository restored again, the SIDs by SID.
    VM_BOOT_TIMEOUT=300 vm_boot $GUEST $PORT $L/vm2 --snapshot --memory 4096 || { echo "BOOT 2 FAILED" >$L/boot.fail; exit 1; }
    put_all || { echo "PUT 2 FAILED" >$L/boot.fail; exit 1; }
    for d in $DIRS; do
        vm_sh "$CLEAN" >$L/clean2-$d.log 2>&1
        T=120 vm_ssh 'cd / && tar -xf -' <$L/rt-$d.tar || continue
        T=300 vm_ssh "cd /root/st1 && ./roundtrip.sh --again /root/st1/${d#*-}-bko 2>&1" >$L/rt-$d-again.log 2>&1
    done
    vm_halt
    rm -f $L/rt-*.tar
    exit 0
fi

[[ $# -ge 3 ]] || { sed -n 5,9p "$0" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PREFIX=$3 FILTER=${4:-.}
[[ -x $BIN/restic-bko && -x $BIN/rustic-bko ]] || { echo "FAIL harness: no restic-bko or rustic-bko in $BIN" >&2; exit 2; }
STC_LOGS=${STC_LOGS:-$WORK/stictest1/$GUEST/logs}
L=$STC_LOGS/$(date +%Y%m%d-%H%M%S)-$(basename "$(dirname "$(dirname "$BIN")")")
mkdir -p "$L" /mnt/bko-ram/$PREFIX
START=$(date +%s)
. $C/guest-slot.sh
case $(hostname -s) in chopin) slot_take 3 2700-2799 ;; *) slot_take 3 2900-2999 ;; esac
HOT_C=90 $WORK/cmp-run.sh --reserve 3 6 timeout 1200 bash "$here/check.sh" --inner "$BIN" "$PORT" "$PREFIX" "$L"
slot_give
rm -rf /mnt/bko-ram/$PREFIX

out=()
add() { out+=("$1 $2: $3"); }
rtv() { grep -a '^roundtrip: ' "$L/rt-$1.log" 2>/dev/null | tail -n 1; }
if [[ -f $L/boot.fail ]]; then add FAIL "guest boots" "$(cat $L/boot.fail) ($L/vm)"; fi
# roundtrip.sh's sections, per need, across the four directions (and the
# four restores again in a fresh boot, where the section exists there).
sect() {  # NEED SECTION-REGEX [again]
    local need=$1 re=$2 bad="" d v runs=$DIRS
    [[ ${3:-} == again ]] && runs="$DIRS $(for d in $DIRS; do echo -n "$d-again "; done)"
    for d in $runs; do
        v=$(rtv $d)
        [[ -n $v ]] || { bad="$bad $d:no-verdict"; continue; }
        if [[ $v == "roundtrip: FAIL"* ]] && grep -qE "$re" <<<"${v#roundtrip: FAIL: }"; then
            bad="$bad $d:$(grep -oE "($re)[^;]*" <<<"$v" | head -n 1)"
        fi
    done
    if [[ -z $bad ]]; then add PASS "$need" "roundtrip.sh, $(wc -w <<<"$runs") runs, fresh ZFS datasets"
    else add FAIL "$need" "${bad# }"; fi
}
sect "File contents, types, sizes, modes (set-id, sticky), owners incl. no-name, links, ns atime/mtime, symlinks and their times, FIFO, non-UTF-8 names, NFSv4 ACLs, system attributes, named attributes (main listing)" "compare \(" again
sect "Creation times (crtime, unix.birthtime)" "creation times" again
sect "Named attribute files' own owner, mode, times, ACL (illumos.xattr_meta)" "attribute files" again
sect "SID owners and SID ACL entries (same boot, after idmap restart, and in a fresh boot)" "SID owners" again
sect "System attributes cleared from a new file's default (noarchive, noav_modified)" "system attributes off" again
sect "ZFS project IDs" "ZFS project" again
sect "Hard links (one inode)" "hard links" again
sect "Sockets skipped (no node, no restore, no message)" "socket" again
sect "Sparse file marked and restored with no more blocks" "sparse"
sect "Restore over an existing, changed tree" "restore over an existing tree|replaced directory"
sect "av_quarantined file: node without contents, warning, restored empty and quarantined" "av_quarantined"
sect "idmap unreachable at restore (nobody, one warning per attribute) and at backup (one warning, numbers kept)" "idmap unreachable"
sect "Backup and restore exit 0" "^backup|; backup|^restore$|; restore$|^restore;|; restore;"
# Both tools' snapshots of the round trip's tree, node by node (rt_snapshots
# in the cross directions).
snapbad=""
for d in restic-rustic rustic-restic; do
    n=$(grep -ac '^== snapshot nodes of' "$L/rt-$d.log" 2>/dev/null)
    [[ ${n:-0} -ge 1 ]] || snapbad="$snapbad $d:not-run"
    v=$(rtv $d); [[ $v == *FAIL*"snapshot nodes"* ]] && snapbad="$snapbad $d:differ"
done
[[ -z $snapbad ]] && add PASS "Both tools' snapshots node-identical (roundtrip tree)" "rt_snapshots, restic->rustic and rustic->restic" ||
    add FAIL "Both tools' snapshots node-identical (roundtrip tree)" "${snapbad# }"
# extra.sh
ex() { grep -a "^R $1 " "$L/extra.log" 2>/dev/null | head -n 1 | cut -d' ' -f3-; }
exk() {  # NEED KEY...
    local need=$1 k bad="" lim="" ok=""; shift
    for k in "$@"; do
        local v; v=$(ex "$k")
        case $v in PASS*) ok="$ok $k" ;; LIMIT*) lim="$lim $k: ${v#LIMIT }" ;; FAIL*) bad="$bad $k: ${v#FAIL }" ;; *) bad="$bad $k: no result" ;; esac
    done
    if [[ -n $bad ]]; then add FAIL "$need" "${bad# }"
    elif [[ -n $lim ]]; then add LIMIT "$need" "${lim# }"
    else add PASS "$need" "extra.sh:$ok"; fi
}
exk "Backups of the extra fixture exit 0" backups
exk "Both tools' snapshots node-identical (extra fixture, ancestors included)" snap-identical
exk "Guest's real files (setuid passwd/su, shadow, files with xattrs/ACLs), device nodes incl. rdev 0, deep/empty/long/non-UTF-8 names, big and no-name ids, SID owner, hard links across dirs, sysattrs on dirs: 4 directions into fresh datasets" rt-restic-restic rt-rustic-rustic rt-restic-rustic rt-rustic-restic
exk "Device nodes made (c, b, rdev 0)" devnodes-made
exk "Door files (the guest's own, /etc/sysevent): skipped quietly by both tools, backups and restores exit 0" doors
exk "Snapshot nodes of the directories above the backed-up path, both tools" snap-ancestors
exk "Ancestor directories' metadata (owner incl. SID, mode, ACL, sysattrs, xattr, crtime, times)" anc-restic-restic anc-rustic-rustic anc-restic-rustic anc-rustic-restic
exk "Restore over existing immutable/appendonly/nounlink files" over-restic-restic over-rustic-rustic over-restic-rustic over-rustic-restic
exk "Both tools alike over immutable files" over-alike
exk "Restore onto tmpfs (the guest's other file system): contents, owners, modes, mtimes, ACLs, xattrs, rdev; both tools alike" tmpfs-restic tmpfs-rustic tmpfs-alike
# Non-root
nr=$(python3 $C/verdict.py judge nonroot "$L/nonroot.log" --full "$L/nonroot.log" --targets $GUEST 2>&1)
if grep -q '"verdict": "PASS"' <<<"$nr" && ! grep -qE '"verdict": "(FAIL|SKIP)"' <<<"$nr"; then
    add PASS "Non-root restore (uid 4321, 4 directions)" "verdict.py judge nonroot"
else add FAIL "Non-root restore (uid 4321, 4 directions)" "$(head -c 600 <<<"$nr" | tr '\n' ' ')"; fi

fail=0
printf '%s\n' "${out[@]}" | grep -E "$FILTER" | tee "$L/results.txt"
grep -q '^FAIL' "$L/results.txt" && fail=1
python3 - "$L/results.txt" "$L/results.json" "$L" "$(( $(date +%s) - START ))" <<'PY'
import json, sys
rows = []
for line in open(sys.argv[1]):
    v, rest = line.rstrip("\n").split(" ", 1)
    need, _, detail = rest.partition(": ")
    rows.append({"verdict": v, "need": need, "detail": detail})
json.dump({"guest": "omnios58", "logs": sys.argv[3], "seconds": int(sys.argv[4]), "results": rows}, open(sys.argv[2], "w"), indent=1)
PY
cp "$L/results.json" "$STC_LOGS/results.json"
echo "logs: $L ($(( $(date +%s) - START )) s)"
exit $fail
