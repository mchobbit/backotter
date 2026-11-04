#!/usr/bin/env bash
# SticTest1 targeted check of alpinebsd (AlpineBSD 1.0: NetBSD 11.0 kernel and
# libc, busybox userland, s6 init; root FFSv2ea with WAPBL, tmpfs on /run).
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR  restic-bko and rustic-bko for NetBSD (roundN/bin/netbsd).  With
#            no rustic-bko there (round 14 built none for NetBSD): RUSTIC_BKO,
#            else the newest ~/backotter-work/build-cache/store/rustic-netbsd/*/bin
#            (named in binaries.txt and the output).
# One throwaway overlay (vm-run --snapshot, TMPDIR=/mnt/bko-ram/PREFIX), taken
# through guest-slot.sh (slot_take 3 2700-2799 on Chopin, 3 2900-2999 on
# Cinqcent) and cmp-run.sh --reserve 3 6.
# NetBSD's mtree, stat, chflags, *facl, *extattr, touch, nc go in /root/nbtools
# (AlpineBSD has none: busybox), from ~/backotter-work/netbsd-tools; a host
# without them fetches them first from a throwaway netbsd11 on PORT (as
# testvm/compare/run-guest-rt.sh alpinebsd does), under the same slot and
# reservation.  In it:
#   testvm/compare/netbsd/roundtrip.sh in all four directions (root FFSv2ea +
#   FFSv2ea posix1e/nfs4 images: file flags, POSIX.1e and NFSv4 ACLs, user/
#   system extattrs incl. on symlinks and on the root's own FFSv2ea, birth
#   times, ns times, set-id/sticky, no-name owners, hard links, sparse, FIFO,
#   socket skipped, non-UTF-8 names, restore over a changed tree, FFSv1,
#   restores onto FFSv1/plain FFSv2, node identity of both tools' snapshots),
#   and probe.sh next to this script (inventory counts; the guest's real
#   devices incl. rdev 0, set-id binaries, s6-rc setgid tree, fifo, hard-linked
#   pair; deep/empty dirs, ancestors; restores onto fresh FFSv2ea images,
#   over immutable/append-only files, onto tmpfs; non-root runs).
# Prints PASS/FAIL/LIMIT lines, writes results.json in the log dir
# (~/backotter-work/stictest1/alpinebsd/logs/PREFIX-TIME), exits 0 if no FAIL.
set -uo pipefail
self=$(realpath "$0")
here=$(dirname "$self")
WORK=$HOME/backotter-work
CMP=$WORK/testvm/compare
[[ $# -ge 3 ]] || { sed -n 2,28p "$0" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PREFIX=$3 FILTER=${4:-}
LOGS=${CHECK_LOGS:-${STC_LOGS:-$WORK/stictest1/alpinebsd/logs}/$PREFIX-$(date +%Y%m%d-%H%M%S)}
SCR=/mnt/bko-ram/$PREFIX

if [[ -z ${CHECK_INNER:-} ]]; then
    mkdir -p "$LOGS"
    rustic=$BIN/rustic-bko
    [[ -f $rustic ]] || rustic=${RUSTIC_BKO:-$(ls -t "$WORK"/build-cache/store/rustic-netbsd/*/bin 2>/dev/null | head -n 1)}
    [[ -f $BIN/restic-bko && -f $rustic ]] || { echo "check: no restic-bko or rustic-bko for netbsd" >&2; exit 2; }
    { echo "restic-bko: $BIN/restic-bko"; cat "$BIN/restic-commit" 2>/dev/null
      echo "rustic-bko: $rustic"; cat "$(dirname "$rustic")/commit" "$BIN/rustic-commit" 2>/dev/null; } >"$LOGS/binaries.txt"
    echo "rustic-bko used: $rustic ($(head -n 1 "$(dirname "$rustic")/commit" 2>/dev/null || cat "$BIN/rustic-commit" 2>/dev/null | head -n 1))"
    # shellcheck source=/dev/null
    source "$CMP/guest-slot.sh"
    case $(hostname -s) in chopin) slot_take 3 2700-2799 ;; *) slot_take 3 2900-2999 ;; esac
    CHECK_INNER=1 CHECK_LOGS=$LOGS RUSTIC_BIN=$rustic HOT_C=90 \
        "$WORK/cmp-run.sh" --reserve 3 6 timeout 900 "$self" "$@" >"$LOGS/run.log" 2>&1
    rc=$?
    slot_give
    rm -rf "$SCR"
    echo "inner exit $rc" >>"$LOGS/run.log"
    python3 - "$LOGS" "$FILTER" <<'PY'
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
        res.append({"status": "PASS", "need": f"roundtrip-{d}", "detail": "flags, POSIX.1e/NFSv4 ACLs, user/system extattrs (root FFSv2ea too), birth/a/mtimes, modes, owners, links, sparse, FIFO, socket skipped, over-restore, FFSv1, node identity"})
    else:
        res.append({"status": "FAIL", "need": f"roundtrip-{d}", "detail": v[-1].split("roundtrip: ", 1)[1]})
res.append({"status": "LIMIT", "need": "linux/selinux/capabilities/zfs/btrfs/bcachefs attributes", "detail": "not on AlpineBSD (NetBSD kernel, FFS and tmpfs only; no MAC labels, no capabilities; secmodel securelevel -1)"})
if not any(r["need"].startswith("restore-") for r in res):
    res.append({"status": "FAIL", "need": "probe", "detail": "probe.sh gave no results (see run.log)"})
if filt:
    res = [r for r in res if re.search(filt, r["need"], re.I)]
json.dump(res, open(f"{logs}/results.json", "w"), indent=1)
for l in lines:
    if l.startswith("INV "): print(l)
for r in res: print(f'{r["status"]} {r["need"]}: {r["detail"]}')
print(f"logs: {logs}")
sys.exit(1 if any(r["status"] == "FAIL" for r in res) else 0)
PY
    exit $?
fi

# --- Inside cmp-run.sh's reservation ------------------------------------------
# shellcheck source=/dev/null
. "$WORK/testvm/vm-lib.sh"
mkdir -p "$SCR" && export TMPDIR=$SCR
trap 'vm_halt; rm -rf "$SCR"' EXIT
tools=$WORK/netbsd-tools
have_tools() { [[ -x $1/mtree && -x $1/stat && -x $1/nc ]]; }
if ! have_tools "$tools"; then
    # Once per host, the first time: the list and the way are
    # run-guest-rt.sh's.  The lock keeps a second run from fetching too.
    exec 9>"$tools.lock" && flock 9
    if ! have_tools "$tools"; then
        echo "== fetching NetBSD's tools from netbsd11 on $PORT ($(date))"
        part=$tools.part.$$
        rm -rf "$part" && mkdir -p "$part"
        vm_boot netbsd11 "$PORT" "$LOGS/vm-netbsd11" --snapshot --memory 4096 || { echo "netbsd11 boot failed (fetching NetBSD's tools)"; rm -rf "$part"; exit 1; }
        T=120 vm_ssh 'cd /root && rm -rf nbtools && mkdir nbtools && cp /usr/sbin/mtree /usr/bin/stat /usr/bin/chflags /bin/chmod /bin/setfacl /bin/getfacl /usr/bin/setextattr /usr/bin/getextattr /usr/bin/lsextattr /usr/bin/rmextattr /usr/bin/md5 /usr/bin/sha256 /bin/ls /usr/bin/touch /usr/bin/nc nbtools/ && tar -cf - nbtools' |
            tar -C "$part" --strip-components=1 -xf -
        vm_halt
        have_tools "$part" || { echo "couldn't fetch NetBSD's tools from netbsd11"; rm -rf "$part"; exit 1; }
        rm -rf "$tools" && mv "$part" "$tools"
        echo "fetched NetBSD's tools into $tools: $(ls "$tools" | tr '\n' ' ')"
    fi
    flock -u 9; exec 9>&-
fi
echo "== boot alpinebsd on $PORT ($(date))"
vm_boot alpinebsd "$PORT" "$LOGS/vm" --snapshot --memory 4096 || { echo "boot failed"; exit 1; }
tar -C "$WORK/netbsd-tools" -cf - . | T=300 vm_ssh 'mkdir -p /root/nbtools && cd /root/nbtools && tar -xf -' || { echo "nbtools copy failed"; exit 1; }
vm_put "$BIN/restic-bko" /root/restic-bko && vm_put "$RUSTIC_BIN" /root/rustic-bko &&
    vm_put "$CMP/netbsd/roundtrip.sh" /root/roundtrip.sh && vm_put "$CMP/rt-common.sh" /root/rt-common.sh &&
    vm_put "$here/probe.sh" /root/probe.sh || { echo "copy failed"; exit 1; }
T=60 vm_ssh '/root/restic-bko version; /root/rustic-bko --version; /sbin/sysctl kern.securelevel; uname -a; cat /etc/alpine-release; mount' 2>&1
reset='PATH=/root/nbtools:/sbin:/usr/sbin:/bin:/usr/bin; cd /; for m in $(mount | awk "\$3 ~ /^\\/root\\/bk/ {print \$3}" | sort -r); do umount -f $m; done; for v in 0 1 2 3; do vndconfig -u vnd$v 2>/dev/null; done; chflags -R 0 /root/bko-* /tmp/bko-* 2>/dev/null; find /root/bko-* /tmp/bko-* -type l -exec chflags -h 0 {} + 2>/dev/null; rm -rf /root/bko-* /tmp/bko-*; true'
if [[ -z $FILTER || roundtrip =~ $FILTER ]]; then
for dir in restic-restic rustic-rustic restic-rustic rustic-restic; do
    b=${dir%-*} r=${dir#*-}
    echo "== roundtrip $dir ($(date))"
    T=420 vm_ssh "$reset; cd /root && ./roundtrip.sh /root/$b-bko /root/$r-bko 2>&1" 2>&1 | tee "$LOGS/rt-$dir.log" | sed "s/^roundtrip: /RT $dir roundtrip: /"
done
fi
T=60 vm_ssh "$reset" 2>&1
echo "== probe ($(date))"
T=420 vm_ssh '/bin/sh /root/probe.sh 2>&1' 2>&1 | tee "$LOGS/probe.log"
echo "== done ($(date))"
