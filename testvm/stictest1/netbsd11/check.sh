#!/usr/bin/env bash
# SticTest1 targeted check of netbsd11 (NetBSD 11, FFSv2 root with WAPBL; no
# extattrs or ACLs on the root, so FFSv2ea images carry those).
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR  restic-bko and rustic-bko for NetBSD (roundN/bin/netbsd).  A round
#            built with --only and no netbsd rustic-bko: the newest one in
#            ~/backotter-work/build-cache/store/rustic-netbsd (RUSTIC_BKO names
#            another), said in the output.
# One throwaway overlay (vm-run --snapshot, TMPDIR=/mnt/bko-ram/PREFIX), taken
# through guest-slot.sh and cmp-run.sh --reserve 3 6.  In it:
#   testvm/compare/netbsd/roundtrip.sh in all four directions (root FFSv2 +
#   FFSv2ea posix1e/nfs4 images: file flags incl. SF_LOG, POSIX.1e and NFSv4
#   ACLs, user/system extattrs incl. on symlinks, birth times, ns atimes/
#   mtimes, set-id/sticky, no-name owners, hard links, sparse, FIFO, socket
#   skipped, non-UTF-8 names, restore over a changed tree, FFSv1, restores
#   onto FFSv1/plain FFSv2, node identity of both tools' snapshots), and
#   probe.sh (real /dev nodes incl. rdev 0, real set-id binaries, deep/empty
#   dirs, ancestors above the backup path, restores onto fresh FFSv2 images,
#   restore over immutable/append-only files, non-root runs).
# Prints PASS/FAIL/LIMIT lines, writes results.json in the log dir
# (~/backotter-work/stictest1/netbsd11/logs/PREFIX-TIME), exits 0 if no FAIL.
set -uo pipefail
self=$(realpath "$0")
here=$(dirname "$self")
WORK=$HOME/backotter-work
CMP=$WORK/testvm/compare
[[ $# -ge 3 ]] || { sed -n 2,20p "$0" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PREFIX=$3 FILTER=${4:-}
LOGS=${CHECK_LOGS:-${STC_LOGS:-$WORK/stictest1/netbsd11/logs}/$PREFIX-$(date +%Y%m%d-%H%M%S)}
SCR=/mnt/bko-ram/$PREFIX

if [[ ${1:-} != --inner && -z ${CHECK_INNER:-} ]]; then
    mkdir -p "$LOGS"
    rustic=$BIN/rustic-bko
    [[ -f $rustic ]] || rustic=${RUSTIC_BKO:-$(ls -t "$WORK"/build-cache/store/rustic-netbsd/*/bin 2>/dev/null | head -n 1)}
    [[ -f $BIN/restic-bko && -f $rustic ]] || { echo "check: no restic-bko or rustic-bko for netbsd" >&2; exit 2; }
    { echo "restic-bko: $BIN/restic-bko"; cat "$BIN/restic-commit" 2>/dev/null
      echo "rustic-bko: $rustic"; cat "$(dirname "$rustic")/commit" "$BIN/rustic-commit" 2>/dev/null; } >"$LOGS/binaries.txt"
    # shellcheck source=/dev/null
    source "$CMP/guest-slot.sh"
    slot_take 3 2900-2999
    CHECK_INNER=1 CHECK_LOGS=$LOGS RUSTIC_BIN=$rustic HOT_C=90 \
        "$WORK/cmp-run.sh" --reserve 3 6 timeout 1500 "$self" "$@" >"$LOGS/run.log" 2>&1
    rc=$?
    slot_give
    rm -rf "$SCR"
    echo "inner exit $rc" >>"$LOGS/run.log"
    # The verdict lines.
    python3 - "$LOGS" "$FILTER" <<'EOF'
import json, re, sys
logs, filt = sys.argv[1], sys.argv[2]
lines = open(f"{logs}/run.log", errors="replace").read().splitlines()
res = []
for l in lines:
    m = re.match(r"CHK (PASS|FAIL|LIMIT) ([^:]+): (.*)", l)
    if m: res.append({"status": m[1], "need": m[2], "detail": m[3]})
for d in ("restic-restic", "rustic-rustic", "restic-rustic", "rustic-restic"):
    v = [l for l in lines if l.startswith(f"RT {d} roundtrip: ")]
    if not v:
        res.append({"status": "FAIL", "need": f"roundtrip-{d}", "detail": "no verdict (see run.log)"})
    elif v[-1].endswith("roundtrip: PASS"):
        res.append({"status": "PASS", "need": f"roundtrip-{d}", "detail": "flags, ACLs, extattrs, birth/a/mtimes, modes, owners, links, sparse, FIFO, socket skipped, over-restore, FFSv1, node identity"})
    else:
        res.append({"status": "FAIL", "need": f"roundtrip-{d}", "detail": v[-1].split("roundtrip: ", 1)[1]})
res.append({"status": "LIMIT", "need": "linux/selinux/capabilities/zfs/btrfs/bcachefs attributes", "detail": "not on NetBSD 11 (no such attributes; FFS only)"})
res.append({"status": "LIMIT", "need": "root extattrs/ACLs", "detail": "netbsd11's root is plain FFSv2 (lsextattr EOPNOTSUPP): extattrs and ACLs are tested on FFSv2ea images"})
if not any(r["need"].startswith("restore-") for r in res):
    res.append({"status": "FAIL", "need": "probe", "detail": "probe.sh gave no results (see run.log)"})
if filt:
    res = [r for r in res if re.search(filt, r["need"], re.I)]
json.dump(res, open(f"{logs}/results.json", "w"), indent=1)
for r in res: print(f'{r["status"]} {r["need"]}: {r["detail"]}')
print(f"logs: {logs}")
sys.exit(1 if any(r["status"] == "FAIL" for r in res) else 0)
EOF
    exit $?
fi

# --- Inside cmp-run.sh's reservation ------------------------------------------
# shellcheck source=/dev/null
. "$WORK/testvm/vm-lib.sh"
mkdir -p "$SCR" && export TMPDIR=$SCR
trap 'vm_halt; rm -rf "$SCR"' EXIT
echo "== boot netbsd11 on $PORT ($(date))"
vm_boot netbsd11 "$PORT" "$LOGS/vm" --snapshot --memory 4096 || { echo "boot failed"; exit 1; }
vm_put "$BIN/restic-bko" /root/restic-bko && vm_put "$RUSTIC_BIN" /root/rustic-bko &&
    vm_put "$CMP/netbsd/roundtrip.sh" /root/roundtrip.sh && vm_put "$CMP/rt-common.sh" /root/rt-common.sh &&
    vm_put "$here/probe.sh" /root/probe.sh || { echo "copy failed"; exit 1; }
T=60 vm_ssh '/root/restic-bko version; /root/rustic-bko --version; sysctl kern.securelevel; uname -a' 2>&1
reset='PATH=/sbin:/usr/sbin:/bin:/usr/bin; cd /; for m in $(mount | awk "\$3 ~ /^\\/root\\/bko/ {print \$3}" | sort -r); do umount -f $m; done; for v in 0 1 2 3; do vndconfig -u vnd$v 2>/dev/null; done; chflags -R 0 /root/bko-* /tmp/bko-* 2>/dev/null; rm -rf /root/bko-* /tmp/bko-*; true'
for dir in restic-restic rustic-rustic restic-rustic rustic-restic; do
    b=${dir%-*} r=${dir#*-}
    echo "== roundtrip $dir ($(date))"
    T=600 vm_ssh "$reset; cd /root && ./roundtrip.sh /root/$b-bko /root/$r-bko 2>&1" 2>&1 | tee "$LOGS/rt-$dir.log" | sed "s/^roundtrip: /RT $dir roundtrip: /"
done
T=60 vm_ssh "$reset" 2>&1
echo "== probe ($(date))"
T=600 vm_ssh '/bin/sh /root/probe.sh 2>&1' 2>&1 | tee "$LOGS/probe.log"
echo "== done ($(date))"
