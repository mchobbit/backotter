#!/usr/bin/env bash
# GuestTest2 update's job for Sculpt (contract 2.1, the update round; job.sh MODE console-update).
# Sculpt has no SSH, so not vm-update but testvm/sculpt-update: Sculpt's own System Update, done
# offline on an overlay, which it boots (sculpt-check.sh) before it merges; that boot is its merge
# gate.  update-sculpt.sh GUEST OUT runs it on BKO_WORK/vms/GUEST (the synced tree, on the fleet
# host) as a job of the round VM_UPDATE_ROUND names (the fleet lock shared, as vm-update's jobs
# hold it), its scratch in this job's reserved RAM, and writes OUT/sculpt-update.log (stdout),
# OUT/sculpt-update.err (stderr: its error, if it fails) and OUT/result.json: rc, merged, generation_before and _after (vms/GUEST/image-state), merge.why
# (why nothing merged) or merge.steps (the pc image installed).  No --publish: vm-store publish
# needs the fleet lock to itself; the guest is published after the round (vm-store publish GUEST,
# or the weekly publish --changed), as vm-update's merges are.  It exits with sculpt-update's
# status.  A TERM goes to sculpt-update once, which stops (a merge under way finishes first).
set -u
G=$1 OUT=$2
here=$(dirname "$(realpath "$0")")
TV=$(dirname "$(dirname "$here")")
W=${BKO_WORK:-$HOME/backotter-work}
gdir=$W/vms/$G
[[ -n ${VM_UPDATE_ROUND:-} ]] || { echo "update-sculpt.sh: no VM_UPDATE_ROUND: this is a round's job" >&2; exit 2; }
R=$(mktemp -d /mnt/bko-ram/t2-guest-probe.XXXXXX) || exit 2
printf '{"pid": %d, "work": "%s"}\n' "$$" "$(realpath "$W")" > "$R/owner.json"   # for preflight's sweep, after a KILL
trap 'trap "" TERM INT HUP; rm -rf "$R"' EXIT
pid=
trap '[[ -n $pid ]] && kill -TERM "$pid" 2>/dev/null; trap "" TERM INT HUP' TERM INT HUP
state() {  # the image-state generation, and the pc image sculpt-image.json records
    python3 -I - "$gdir" <<'PY'
import json, os, sys
def get(name, key):
    try:
        return json.load(open(os.path.join(sys.argv[1], name))).get(key) or ""
    except (OSError, ValueError, AttributeError):
        return ""
print(get("image-state", "generation"))
print(get("sculpt-image.json", "version"))
PY
}
{ read -r gen0; read -r ver0; } < <(state)
t0=$SECONDS
SCULPT_UPDATE_SCRATCH=$R "$TV/sculpt-update" "$gdir" > "$OUT/sculpt-update.log" 2> "$OUT/sculpt-update.err" &
pid=$!
while :; do
    wait "$pid"; rc=$?
    kill -0 "$pid" 2>/dev/null || break
done
{ read -r gen1; read -r ver1; } < <(state)
python3 -I - "$OUT/result.json" "$G" "$rc" $((SECONDS - t0)) "${gen0:-}" "${gen1:-}" "${ver0:-}" "${ver1:-}" "$OUT/sculpt-update.log" "$OUT/sculpt-update.err" <<'PY'
import json, sys
out, g, rc, wall, gen0, gen1, ver0, ver1, log, err = sys.argv[1:]
rc = int(rc)
def lines(p):
    try:
        return [x for x in open(p, errors="replace").read().splitlines() if x.strip()]
    except OSError:
        return []
errs = lines(err)
tail = (lines(log) + errs)[-8:]    # its error last: sculpt-update prints it to stderr as it exits
merged = rc == 0 and bool(gen0) and bool(gen1) and gen0 != gen1
if merged:
    merge = {"decided": "merged", "why": None, "steps": [f"sculpt-update: pc image {ver0 or '?'} -> {ver1 or '?'}"]}
elif rc == 0:
    merge = {"decided": "nothing", "why": f"sculpt-update: nothing newer to install ({ver1 or ver0 or '?'} is its release's newest)",
             "steps": []}
else:
    last = (errs or tail)[-1:]
    merge = {"decided": "failed", "why": f"sculpt-update exit {rc}" + (f": {last[0]}" if last else ""), "steps": []}
json.dump({"guest": g, "mode": "console-update", "rc": rc, "phase": "done", "wall_s": int(wall), "merged": merged,
           "generation_before": gen0 or None, "generation_after": gen1 or None, "merge": merge, "tail": tail},
          open(out, "w"), indent=1)
PY
exit "$rc"
