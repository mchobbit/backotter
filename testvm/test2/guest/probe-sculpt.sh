#!/usr/bin/env bash
# GuestTest2's probe of Sculpt (contract 2.1 step 4): no SSH or serial, so sculpt-check.sh, from a
# copy directory in reserved RAM (it writes check-screen.png beside the image).  probe-sculpt.sh
# GUEST OUT writes OUT/hook-probe.out (=== cmd probe, ok or what failed, === end) and
# OUT/result.json.  The guest's disk is only read.
set -u
G=$1 OUT=$2
here=$(dirname "$(realpath "$0")")
TV=$(dirname "$(dirname "$here")")
W=${BKO_WORK:-$HOME/backotter-work}
gdir=$W/vms/$G
img=$gdir/$G.qcow2; [[ -f $img ]] || img=$(ls "$gdir"/*.qcow2 | head -n 1)
R=$(mktemp -d /mnt/bko-ram/t2-guest-probe.XXXXXX) || exit 2
printf '{"pid": %d, "work": "%s"}\n' "$$" "$(realpath "$W")" > "$R/owner.json"   # for preflight's sweep, after a KILL
trap 'trap "" TERM INT HUP; rm -rf "$R"' EXIT
trap 'exit 143' TERM INT HUP
t0=$SECONDS
d=$R/check
mkdir -p "$d"
ln -s "$img" "$d/sculpt-check.qcow2"
cp "$gdir/vm-run-options" "$d/"
TMPDIR=$R "$TV/sculpt-check.sh" "$d" > "$OUT/check.log" 2>&1
rc=$?
{ echo "=== cmd probe"
  if [[ $rc -eq 0 ]]; then echo ok; else echo "sculpt-check.sh exit $rc"; fi
  echo "=== end"; } > "$OUT/hook-probe.out"
python3 -I - "$OUT/result.json" "$G" $((SECONDS - t0)) <<'PY'
import json, sys
json.dump({"guest": sys.argv[2], "mode": "console", "rc": 0, "phase": "done", "wall_s": int(sys.argv[3])}, open(sys.argv[1], "w"))
PY
