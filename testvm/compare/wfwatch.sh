#!/usr/bin/env bash
# A watchdog for one long-running Claude Code Workflow run (OtterGrind1's
# multi-day test-and-fix loop, a fix round): the main session starts it in
# the background on the Mac and is woken when it exits, either because the
# run finished or because nothing in it has moved for too long.  Round 9
# lost 4.5 h to a workflow agent that had silently stalled; this catches
# that within STALL_MIN minutes.
#
# Usage: wfwatch.sh RUN_DIR [STALL_MIN]
#   RUN_DIR    the run's transcript directory,
#              ~/.claude/projects/PROJ/SESSION/subagents/workflows/wf_XXXX
#              (journal.jsonl: one JSON line per event, "started", "result",
#              "failed"; agent-ID.jsonl: each agent's transcript, which
#              grows while it works)
#   STALL_MIN  minutes with no file under RUN_DIR written before it calls
#              the run stalled (default 20)
#   WFWATCH_POLL  seconds between checks (default 60; for tests)
#   WFWATCH_SLACK seconds the state file may trail RUN_DIR's newest write
#                 and still mark the end (default 5)
# Prints one line and exits:
#   0  DONE RUN status=STATUS: the run is over.  The marker is the run's
#      state file, SESSION/workflows/wf_XXXX.json (RUN_DIR's ../../../workflows),
#      which Claude Code writes only when the run ends, its top-level
#      "status" completed or killed (no journal line marks the end: a
#      journal holds launched, started, result and failed lines only).
#      A killed run is DONE too; STATUS says which.  A run resumed with
#      resumeFromRunId keeps its id and dir, and the earlier end's state
#      file stays until the resumed run ends and rewrites it; the resume
#      writes no second "launched" line and the state file carries no
#      attempt field, so mtimes decide: the state file counts only when
#      it is no more than WFWATCH_SLACK seconds (default 5) older than the
#      newest file or directory under RUN_DIR (journal.jsonl, agent
#      transcripts).  A state file older than the watchdog's own start
#      must also pass that test on two checks one poll apart, so a resume
#      that has not yet written its first journal line is not DONE.
#   2  STALL RUN idle N min; last file PATH at TIME; agents running: LIST
#      (LIST: each agent-ID.jsonl written in the last 2*STALL_MIN minutes
#      whose ID has no result or failed line in journal.jsonl, with its
#      label from the journal; "none" if there is none)
#   3  RUN_DIR missing, at the start or later
#   1  bad arguments
# Checks at once, then every WFWATCH_POLL seconds.  Reads only: it never
# signals, stops or writes anything.  bash 3.2 and BSD stat/date on macOS;
# GNU stat/date on Linux.

set -u

usage() { echo "usage: $(basename "$0") RUN_DIR [STALL_MIN=20]" >&2; exit 1; }
[ $# -ge 1 ] && [ $# -le 2 ] || usage
dir=${1%/}
stall_min=${2:-20}
poll=${WFWATCH_POLL:-60}
case $stall_min in ''|*[!0-9]*) usage ;; esac
slack=${WFWATCH_SLACK:-5}
case $poll in ''|*[!0-9]*) usage ;; esac
case $slack in ''|*[!0-9]*) usage ;; esac
[ "$stall_min" -ge 1 ] || usage
[ "$poll" -ge 1 ] || usage

run=$(basename "$dir")
if [ ! -d "$dir" ]; then
    echo "MISSING $run: no directory $dir"
    exit 3
fi
state="$dir/../../../workflows/$run.json"

if stat -c %Y / >/dev/null 2>&1; then
    statfmt=(-c '%Y %n')                      # GNU
else
    statfmt=(-f '%m %N')                      # BSD
fi
if date -r 0 +%s >/dev/null 2>&1; then
    when() { date -r "$1" '+%Y-%m-%d %H:%M:%S %Z'; }
else
    when() { date -d "@$1" '+%Y-%m-%d %H:%M:%S %Z'; }
fi

# The state file's top-level status (nested results carry "status" too).
status_of() {
    python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status") or "unknown")' "$1" 2>/dev/null
}

# Agents whose transcript moved since $1 (epoch) and whose ID the journal
# has no result or failed line for: "ID(label) ..." or "none".
running_agents() {
    local since=$1 out= t f id label
    while read -r t f; do
        [ -n "$f" ] || continue
        [ "$t" -ge "$since" ] || continue
        id=$(basename "$f" .jsonl); id=${id#agent-}
        if [ -f "$dir/journal.jsonl" ] &&
           grep -E "\"type\":\"(result|failed)\"" "$dir/journal.jsonl" |
               grep -q "\"agentId\":\"$id\""; then
            continue
        fi
        label=
        [ -f "$dir/journal.jsonl" ] &&
            label=$(grep "\"type\":\"started\"" "$dir/journal.jsonl" |
                    grep "\"agentId\":\"$id\"" | tail -n 1 |
                    sed -n 's/.*"label":"\([^"]*\)".*/\1/p')
        out="$out ${id}${label:+($label)}"
    done <<EOF
$(find "$dir" -type f -name 'agent-*.jsonl' -exec stat "${statfmt[@]}" {} + 2>/dev/null)
EOF
    echo "${out# }" | sed 's/^$/none/'
}

# Seconds since the epoch the file $1 was last written, or nothing.
mtime_of() {
    local o
    o=$(stat "${statfmt[@]}" "$1" 2>/dev/null) || return 0
    echo "${o%% *}"
}

started=$(date +%s)
old_ok=0          # checks in a row a pre-start state file has passed

while :; do
    if [ ! -d "$dir" ]; then
        echo "MISSING $run: $dir has gone"
        exit 3
    fi
    now=$(date +%s)
    newest=$(find "$dir" -exec stat "${statfmt[@]}" {} + 2>/dev/null | sort -n | tail -n 1)
    newest_t=${newest%% *}
    newest_f=${newest#* }
    case $newest_t in ''|*[!0-9]*) newest_t=$now newest_f=? ;; esac
    ended=0
    if [ -f "$state" ]; then
        st=$(mtime_of "$state")
        case $st in ''|*[!0-9]*) st=0 ;; esac
        if [ $(( st + slack )) -ge "$newest_t" ]; then
            if [ "$st" -ge "$started" ]; then
                ended=1
            else                        # older than this watch: resumed?
                old_ok=$(( old_ok + 1 ))
                [ "$old_ok" -ge 2 ] && ended=1
            fi
        else
            old_ok=0                    # stale: a resumed run is working
        fi
    fi
    if [ "$ended" = 1 ]; then
        s=$(status_of "$state")
        if [ -z "$s" ]; then             # caught mid-write: read again
            sleep 2
            s=$(status_of "$state")
        fi
        echo "DONE $run status=${s:-unknown}"
        exit 0
    fi
    idle=$(( (now - newest_t) / 60 ))
    # A state file awaiting its second check is an end, not a stall.
    if [ "$old_ok" = 0 ] && [ "$idle" -ge "$stall_min" ]; then
        agents=$(running_agents $(( now - 2 * stall_min * 60 )))
        echo "STALL $run idle $idle min; last file $newest_f at $(when "$newest_t"); agents running: $agents"
        exit 2
    fi
    sleep "$poll"
done
