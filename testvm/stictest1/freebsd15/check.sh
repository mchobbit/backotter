#!/usr/bin/env bash
# SticTest1 targeted check of freebsd15 (FreeBSD 15.1, ZFS root zroot with
# acltype=nfsv4, xattr=sa, compression; one dataset atime=on).
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR  holds restic-bko and rustic-bko for FreeBSD (roundN/bin/freebsd)
#   PORT     SSH port of the throwaway guest;  PREFIX  scratch name
#   NEED_FILTER  an extended regex: only checklist lines matching it count
# One throwaway overlay (vm-run --snapshot, TMPDIR=/mnt/bko-ram/PREFIX), in a
# guest slot and a cmp-run.sh --reserve room.  In it:
#   - testvm/compare/freebsd/roundtrip.sh in all four directions (torture.sh's
#     fixture on the ZFS root and on a UFS md disk with nfsv4acls: file flags,
#     DOS flags, NFSv4 ACLs, user+system extattrs sorted, births, ns times,
#     hard links, symlink times, FIFO, socket skipped, sparse, non-UTF-8 and
#     deep names, set-id/sticky, no-name owners; restore over a changed tree;
#     restore onto UFS without ACLs; snapshots of both tools node by node)
#   - extra.sh (next to this): the guest's own files (libc/su schg,
#     /boot/entropy uarch,nodump), ZFS project IDs, sappnd/sunlnk, device
#     nodes, ancestors' metadata, restore over schg/sappnd files left set
#   - nonroot-inner.sh (one non-root restore, 4 directions), judged by
#     verdict.py
# Prints "PASS|FAIL|LIMIT need: detail", writes results.json, exit 0 unless a FAIL.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
WORK=~/backotter-work C=${BKO_TESTVM:-$WORK/testvm}/compare
GUEST=freebsd15
if [[ ${1:-} == --inner ]]; then
    shift; BIN=$1 PORT=$2 PREFIX=$3 L=$4
    export TMPDIR=/mnt/bko-ram/$PREFIX
    . ${BKO_TESTVM:-$WORK/testvm}/vm-lib.sh
    trap vm_halt EXIT
    VM_BOOT_TIMEOUT=300 vm_boot $GUEST $PORT $L/vm --snapshot --memory 4096 || { echo "BOOT FAILED" >$L/boot.fail; exit 1; }
    vm_ssh 'mkdir -p /root/st1' || exit 1
    for f in $BIN/restic-bko $BIN/rustic-bko $C/freebsd/roundtrip.sh $C/rt-common.sh $C/freebsd/torture.sh \
             $C/freebsd/fsmeta.sh $C/nonroot-inner.sh $here/extra.sh; do
        vm_put $f /root/st1/$(basename $f) || exit 1
    done
    vm_ssh 'uname -a; /root/st1/restic-bko version; /root/st1/rustic-bko --version' >$L/versions.txt 2>&1
    clean='cd /; for m in $(mount -p | awk "\$2 ~ /^\/root\/bko-rt/ { print \$2 }" | sort -r); do umount -f $m; done;
           for m in $(mdconfig -l); do mdconfig -d -u $m; done; chflags -R 0 /root/bko-rt* 2>/dev/null;
           rm -rf /root/bko-rt /root/bko-rt-restored /root/bko-rt-noacl /root/bko-rt-repo /root/bko-rt-repo-2 /tmp/bko-rt*'
    for d in restic-restic rustic-rustic restic-rustic rustic-restic; do
        vm_sh "$clean" >/dev/null 2>&1
        T=420 vm_ssh "cd /root/st1 && ./roundtrip.sh /root/st1/${d%-*}-bko /root/st1/${d#*-}-bko 2>&1" >$L/rt-$d.log 2>&1
    done
    vm_sh "$clean" >/dev/null 2>&1
    T=420 vm_ssh 'sh /root/st1/extra.sh /root/st1 2>&1' >$L/extra.log 2>&1
    vm_sh 'zfs create -o mountpoint=/bkonr -o atime=off zroot/bkonr && pw groupadd bkonr -g 4321 &&
           pw useradd bkonr -u 4321 -g 4321 -s /bin/sh -d /nonexistent && chmod 755 /bkonr &&
           cp /root/st1/restic-bko /root/st1/rustic-bko /bkonr/ && cp /root/st1/rt-common.sh /root/rt-common.sh &&
           cp /root/st1/nonroot-inner.sh /root/nonroot-inner.sh' >$L/nonroot-setup.log 2>&1
    { echo "######## $GUEST: $(vm_ssh '/bkonr/restic-bko version' | head -n 1), $(vm_ssh '/bkonr/rustic-bko --version')"
      T=420 vm_ssh 'sh /root/nonroot-inner.sh /bkonr/t /bkonr/restic-bko /bkonr/rustic-bko 2>&1' 2>&1; } >$L/nonroot.log 2>&1
    vm_halt
    exit 0
fi

[[ $# -ge 3 ]] || { sed -n 2,6p "$0" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PREFIX=$3 FILTER=${4:-.}
[[ -x $BIN/restic-bko && -x $BIN/rustic-bko ]] || { echo "no restic-bko/rustic-bko in $BIN" >&2; exit 2; }
STC_LOGS=${STC_LOGS:-$WORK/stictest1/$GUEST/logs}
L=$STC_LOGS/$(date +%Y%m%d-%H%M%S)-$(basename "$(dirname "$BIN")")
mkdir -p "$L" /mnt/bko-ram/$PREFIX
START=$(date +%s)
. $C/guest-slot.sh
slot_take 3 "$(slot_range_here)"
HOT_C=90 $WORK/cmp-run.sh --reserve 3 6 timeout 1000 bash "$here/check.sh" --inner "$BIN" "$PORT" "$PREFIX" "$L"
slot_give
rm -rf /mnt/bko-ram/$PREFIX

out=()
add() { out+=("$1 $2: $3"); }
rtv() { grep -a '^roundtrip: ' "$L/rt-$1.log" 2>/dev/null | tail -n 1; }
if [[ -f $L/boot.fail ]]; then add FAIL "guest boots" "the throwaway overlay didn't come up ($L/vm)"; fi
# roundtrip.sh's sections, per need, across the four directions.
sect() {  # NEED SECTION-REGEX
    local need=$1 re=$2 bad="" d n=0
    for d in restic-restic rustic-rustic restic-rustic rustic-restic; do
        local v; v=$(rtv $d)
        [[ -n $v ]] || { bad="$bad $d:no-verdict"; continue; }
        if [[ $v == "roundtrip: FAIL"* ]] && grep -qE "$re" <<<"${v#roundtrip: FAIL: }"; then
            bad="$bad $d:$(grep -oE "$re[^;]*" <<<"$v" | head -n 1)"
        fi
        n=$((n + 1))
    done
    if [[ -z $bad ]]; then add PASS "$need" "roundtrip.sh, 4 directions (ZFS root + UFS nfsv4acls md)"
    else add FAIL "$need" "${bad# }"; fi
}
sect "FreeBSD file flags (schg/sappnd/sunlnk/nodump/uarch, ZFS DOS flags; uchg/uappnd/uunlnk/arch on UFS)" "file flags|uarch cleared|mtree"
sect "NFSv4 ACLs on ZFS and UFS; restore onto UFS without ACLs warns and exits 0" "ACLs|restore onto a UFS without ACLs"
sect "Extended attributes user+system, stored sorted by name" "extattrs|extended attributes stored sorted"
sect "Birth times min(birth, mtime)" "births"
sect "atime/mtime to the nanosecond, files, dirs, symlinks" "atime and mtime"
sect "Hard links, symlinks, set-id/sticky, owners with no name, FIFO, non-UTF-8/deep/empty names" "hardlinks|mtree"
sect "Sparse files (UFS md disk)" "sparse"
sect "Sockets skipped (no node, no restore, no message)" "socket"
sect "Restore over an existing, changed tree" "restore over an existing tree|replaced directory"
sect "Backup and restore exit 0" "^backup|; backup|restore$|restore;"
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
exk "Snapshots of both tools node-identical (same repository format)" snap-identical
# roundtrip.sh's own cross-direction node comparison too
if grep -aq 'snapshot' "$L/rt-restic-rustic.log" && ! grep -aE '^roundtrip: FAIL.*(snapshot|node)' "$L"/rt-restic-rustic.log "$L"/rt-rustic-restic.log >/dev/null; then
    add PASS "Snapshots node-identical (torture tree, roundtrip.sh rt_snapshots)" "restic->rustic, rustic->restic"
else add FAIL "Snapshots node-identical (torture tree, roundtrip.sh rt_snapshots)" "see rt-restic-rustic.log/rt-rustic-restic.log"; fi
exk "Guest's real files (libc/su schg, /boot/entropy uarch,nodump), ZFS project IDs, sappnd/sunlnk, system xattrs, NFSv4 ACL, FIFO, sparse: 4 directions" rt-restic-restic rt-rustic-rustic rt-restic-rustic rt-rustic-restic
exk "Restore over existing immutable (schg) / append-only (sappnd) files changed under their flags" over-restic-restic over-rustic-rustic over-restic-rustic over-rustic-restic
exk "Ancestor directories of the backup path stored with their metadata, the same by both tools" ancestors
exk "Device nodes (rdev 0)" devnodes-made
# Non-root
nr=$(python3 $C/verdict.py judge nonroot "$L/nonroot.log" --targets $GUEST 2>&1)
if grep -q '"verdict": "PASS"' <<<"$nr" && ! grep -q '"verdict": "FAIL"' <<<"$nr"; then
    add PASS "Non-root restore (uid 4321, 4 directions)" "verdict.py judge nonroot"
else add FAIL "Non-root restore (uid 4321, 4 directions)" "$(head -c 600 <<<"$nr" | tr '\n' ' ')"; fi
add LIMIT "Linux-only metadata (SELinux, capabilities, inode flags, POSIX ACL default on ZFS)" "not on FreeBSD/ZFS: NFSv4 ACLs instead, no security.* namespace (MAC labels off: security.mac.labeled=0)"
add LIMIT "Whole-disk needs (zpool.cache, boot)" "out of scope: whole-disk"

fail=0
printf '%s\n' "${out[@]}" | grep -E "$FILTER" | tee "$L/results.txt"
grep -E "$FILTER" "$L/results.txt" | grep -q '^FAIL' && fail=1
python3 - "$L/results.txt" "$L/results.json" "$L" "$(( $(date +%s) - START ))" <<'PY'
import json, sys
rows = []
for line in open(sys.argv[1]):
    v, rest = line.rstrip("\n").split(" ", 1)
    need, _, detail = rest.partition(": ")
    rows.append({"verdict": v, "need": need, "detail": detail})
json.dump({"guest": "freebsd15", "logs": sys.argv[3], "seconds": int(sys.argv[4]), "results": rows}, open(sys.argv[2], "w"), indent=1)
PY
cp "$L/results.json" "$STC_LOGS/results.json"
echo "logs: $L ($(( $(date +%s) - START )) s)"
exit $fail
