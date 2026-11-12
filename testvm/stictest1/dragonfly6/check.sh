#!/usr/bin/env bash
# SticTest1 targeted check of dragonfly6 (DragonFly 6.4.2: / HAMMER2, /boot
# UFS; no ACLs, extended attributes or birth times; file flags, all 32 bits).
# Never a whole-disk backup: throwaway copies of the guest (vm-run --ram,
# overlay in /mnt/bko-ram/PREFIX), one per run, all inside one
# guest-slot.sh slot and one cmp-run.sh --reserve 3 6:
#   run-guest-rt.sh dragonfly6 (testvm/compare/dragonfly/roundtrip.sh) in all
#     four directions (restic->restic, rustic->rustic, restic->rustic,
#     rustic->restic): a fixture tree on HAMMER2 (/root) and one on UFS
#     (/boot), restored into fresh directories and compared (mtree: type,
#     owner, group, links, size, mtime, symlink target, SHA-256; modes,
#     symlink modes, file flags, devices, atime/mtime to the ns, hard links,
#     sockets skipped), a restore over a changed restored tree (flags cleared,
#     dir replaced by a symlink), unix.sparse marks by blocks (S3), socket
#     nodes, rustic's time-zone warnings, and node identity of both tools'
#     snapshots (rt-common.sh's rt_snapshots).
#   nonroot-rt.sh dragonfly6: backups as root, restores as uid 4321 into
#     /bkonr on HAMMER2 in all four directions; the tools' restores must be
#     the same, no socket restored or named.
# Usage: check.sh BIN_DIR PORT PREFIX [NEED_FILTER]
#   BIN_DIR holds restic-bko and rustic-bko for DragonFly (roundN/bin/dragonfly).
# Prints PASS/FAIL/LIMIT lines, writes results.json in the log dir
# (~/backotter-work/stictest1/dragonfly6/logs/PREFIX-TIME), exits 0 if no FAIL.
set -uo pipefail
self=$(realpath "$0")
here=$(dirname "$self")
WORK=${BKO_WORK:-$HOME/backotter-work}
CMP=${BKO_TESTVM:-$WORK/testvm}/compare
guest=dragonfly6

if [[ ${1:-} == --inner ]]; then
    shift; BIN=$1 PORT=$2 PREFIX=$3 LOGS=$4
    export TMPDIR=/mnt/bko-ram/$PREFIX
    mkdir -p "$TMPDIR"
    trap 'rm -rf "$TMPDIR"' EXIT
    echo "== round trips ($(date))"
    RT_LOGS=$LOGS/rt MEMORY=4096 RT_TIMEOUT=600 timeout 1000 \
        "$CMP/run-guest-rt.sh" $guest "$PORT" "$BIN/restic-bko" "$BIN/rustic-bko" 2>&1
    echo "run-guest-rt exit $?"
    echo "== non-root ($(date))"
    NONROOT_LOGS=$LOGS/nonroot RESTIC=$BIN/restic-bko RUSTIC=$BIN/rustic-bko MEMORY=4096 timeout 400 \
        "$CMP/nonroot-rt.sh" $guest "$PORT" 2>&1
    echo "nonroot-rt exit $?"
    echo "== done ($(date))"
    exit 0
fi

[[ $# -ge 3 ]] || { sed -n 2,23p "$0" >&2; exit 2; }
BIN=$(realpath "$1") PORT=$2 PREFIX=$3 FILTER=${4:-}
[[ -x $BIN/restic-bko && -x $BIN/rustic-bko ]] || { echo "FAIL harness: no restic-bko or rustic-bko in $BIN"; exit 1; }
LOGS=${STC_LOGS:-$WORK/stictest1/$guest/logs}/$PREFIX-$(date +%Y%m%d-%H%M%S)
mkdir -p "$LOGS"
{ echo "restic-bko: $BIN/restic-bko"; cat "$BIN/restic-commit" 2>/dev/null
  echo "rustic-bko: $BIN/rustic-bko"; cat "$BIN/rustic-commit" 2>/dev/null; } >"$LOGS/binaries.txt"
# shellcheck source=/dev/null
source "$CMP/guest-slot.sh"
slot_take 3 "$(slot_range_here)"
HOT_C=90 "$WORK/cmp-run.sh" --reserve 3 6 timeout 1500 "$self" --inner "$BIN" "$PORT" "$PREFIX" "$LOGS" >"$LOGS/run.log" 2>&1
rc=$?
slot_give
rm -rf "/mnt/bko-ram/$PREFIX"
echo "inner exit $rc" >>"$LOGS/run.log"

python3 - "$LOGS" "$FILTER" "$guest" <<'EOF'
import json, os, re, sys
logs, filt, guest = sys.argv[1:4]
dirs = ["restic-restic", "rustic-rustic", "restic-rustic", "rustic-restic"]
res = []
seen, failed, order = {}, {}, []
skip = re.compile(r"^(sparse \(KB allocated|restore over .*, changed$|dragonfly6)")
for d in dirs:
    p = f"{logs}/rt/{guest}/{d}.log"
    lines = open(p, errors="replace").read().splitlines() if os.path.exists(p) else []
    v = [l for l in lines if l.startswith("roundtrip: ")]
    if not v:
        res.append({"status": "FAIL", "need": f"roundtrip {d}", "detail": f"no verdict (see {p})"})
        continue
    verdict = v[-1][len("roundtrip: "):]
    res.append({"status": "PASS" if verdict == "PASS" else "FAIL", "need": f"roundtrip {d}", "detail": verdict})
    for l in lines:
        if l.startswith("== "):
            t = l[3:]
            if skip.match(t): continue
            if t not in seen: seen[t] = []; order.append(t)
            if d not in seen[t]: seen[t].append(d)
    if verdict.startswith("FAIL: "):
        for t in verdict[6:].split("; "):
            if t not in seen: seen[t] = []; order.append(t)
            failed.setdefault(t, []).append(d)
for t in order:
    if t in failed:
        res.append({"status": "FAIL", "need": t, "detail": "failed in " + ", ".join(failed[t])})
    else:
        res.append({"status": "PASS", "need": t, "detail": "identical in " + ", ".join(seen[t])})
# Non-root.
p = f"{logs}/nonroot/{guest}.log"
txt = open(p, errors="replace").read() if os.path.exists(p) else ""
m = re.search(r"^== summary.*", txt, re.M | re.S)
if not m:
    res.append({"status": "FAIL", "need": "Non-root backup and restore (uid 4321, HAMMER2)", "detail": f"no summary (see {p})"})
else:
    s = m.group(0)
    bad = []
    for a, b, n in re.findall(r"^  (\S+) vs (\S+): (\d+) names differ", s, re.M):
        if n != "0": bad.append(f"{a} vs {b}: {n} names differ")
    for l in re.findall(r"^  \S+-\S+: exit .*", s, re.M):
        mm = re.search(r"(\d+) names differ.*sockets restored: (\d+), messages naming one: (\d+)", l)
        if not mm or int(mm[1]) > 6 or mm[2] != "0" or mm[3] != "0": bad.append(l.strip())
    for l in re.findall(r"^  backup by .*", s, re.M):
        if not re.search(r": 0 socket nodes in the snapshot, 0 messages", l): bad.append(l.strip())
    if len(re.findall(r" vs ", s)) < 3: bad.append("fewer than 3 cross-tool comparisons")
    detail = "; ".join(l.strip() for l in s.splitlines()[1:] if l.strip())
    res.append({"status": "FAIL" if bad else "PASS", "need": "Non-root backup and restore (uid 4321, HAMMER2): the tools' restores the same, root-only flags/devices warned not aborted, sockets skipped",
                "detail": ("; ".join(bad) + " || " if bad else "") + detail})
res.append({"status": "LIMIT", "need": "ACLs, extended attributes, birth times", "detail": "DragonFly has none (BACKOTTER.md)"})
res.append({"status": "LIMIT", "need": "Device numbers", "detail": "outside devfs a device's number shows as NOUDEV: the type is compared"})
res.append({"status": "LIMIT", "need": "Whole-system restore", "detail": "out of scope: whole-disk (the harness can't mount disklabel64/HAMMER2)"})
if filt:
    res = [r for r in res if re.search(filt, r["need"], re.I)]
json.dump({"guest": guest, "log": logs, "results": res}, open(f"{logs}/results.json", "w"), indent=1)
for r in res: print(f'{r["status"]} {r["need"]}: {r["detail"]}')
print(f"logs: {logs}")
sys.exit(1 if any(r["status"] == "FAIL" for r in res) else 0)
EOF
