#!/usr/bin/env bash
# GuestTest2's probe of Sculpt (contract 2.1 step 4): no SSH or serial, so sculpt-check.sh, from a
# copy directory in reserved RAM (it writes check-screen.png beside the image), then
# `sculpt-update --check` (Genode's signed image index against the installed image; its scratch in
# the same RAM).  probe-sculpt.sh GUEST OUT writes OUT/hook-probe.out (=== cmd probe, ok or what
# failed; === cmd update-check, "exit N" and its last line; === end), OUT/check.log,
# OUT/update-check.log and OUT/result.json.  The guest's disk is only read.  --check's exit 10 (a
# newer pc image of its release) or 11 (a newer release) is a note of need image:current, never a
# FAIL; nor is a check that couldn't run (a SKIP).
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
SCULPT_UPDATE_SCRATCH=$R "$TV/sculpt-update" --check "$gdir" > "$OUT/update-check.log" 2>&1
crc=$?
{ echo "=== cmd probe"
  if [[ $rc -eq 0 ]]; then echo ok; else echo "sculpt-check.sh exit $rc"; fi
  echo "=== cmd update-check"
  echo "exit $crc"
  grep -v '^[[:space:]]*$' "$OUT/update-check.log" | tail -n 1 | cut -c1-300
  echo "=== end"; } > "$OUT/hook-probe.out"
python3 -I - "$OUT/result.json" "$G" $((SECONDS - t0)) <<'PY'
import json, sys
json.dump({"guest": sys.argv[2], "mode": "console", "rc": 0, "phase": "done", "wall_s": int(sys.argv[3])}, open(sys.argv[1], "w"))
PY
