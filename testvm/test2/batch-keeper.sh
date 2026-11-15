#!/usr/bin/env bash
# batch-keeper.sh BATCH [LOG]: keep a t2 batch driven when its driver dies (a crash, a closed
# terminal, an OS update): `t2 batch resume BATCH` again and again, a minute apart, until the batch
# has its report.txt (docs/design/batch.md §6.4).  The batch installs nothing: the user runs this
# in a second terminal, or as a launchd agent, and stops it with Ctrl-C (or TERM).
#
#   BATCH   the batch id (bt-...) or its directory
#   LOG     where the keeper's lines and the drivers' output go (default MAC_ROOT/batches/BATCH/keeper.log)
#
# A resume while another driver holds the batch lock is refused (exit 2) and simply tried again;
# one driver at a time is the batch's own rule (flock on MAC_ROOT/batches/.lock).  The keeper's
# exit is the batch's (0 every entry PASS, 1 a non-PASS entry), or 130 when it was stopped.
# Environment: T2_MAC_ROOT (as t2), T2_BATCH_KEEPER_SLEEP (s, default 60), T2_BATCH_KEEPER_T2
# (the t2 to run, default this directory's).
set -u
B=${1:?usage: batch-keeper.sh BATCH [LOG]}
HERE=$(cd "$(dirname "$0")" && pwd)
T2=${T2_BATCH_KEEPER_T2:-$HERE/t2}
ROOT=${T2_MAC_ROOT:-$HOME/backotter-work/test2}
case $ROOT in "~"/*) ROOT=$HOME/${ROOT#"~/"} ;; "~") ROOT=$HOME ;; esac
SLEEP=${T2_BATCH_KEEPER_SLEEP:-60}
if [[ -d $B && -f $B/batch.json ]]; then
    DIR=$(cd "$B" && pwd)
else
    DIR=$ROOT/batches/$B
fi
ID=$(basename "$DIR")
if [[ ! -f $DIR/batch.json ]]; then
    echo "batch-keeper: no batch $B in $ROOT/batches" >&2
    exit 2
fi
LOG=${2:-$DIR/keeper.log}
say() { printf '%s batch-keeper: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG" >&2; }
STOP= child=
# bash runs a trap only once the foreground command has ended, so the resume runs in the
# background and the keeper waits for it: a TERM (launchd's stop) reaches the driver at once,
# once, and the keeper waits for the driver's own graceful stop.
trap '[[ -z $STOP && -n $child ]] && kill -TERM "$child" 2>/dev/null; STOP=1' INT TERM HUP
say "keeps $ID driven (pid $$; a resume every ${SLEEP}s while it isn't finished; log $LOG)"
while :; do
    if [[ -f $DIR/report.txt ]]; then
        rc=$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1])).get("exit") or 0))' \
             "$DIR/state.json" 2>/dev/null || echo 1)
        say "$ID has finished (exit $rc)"
        exit "$rc"
    fi
    [[ -n $STOP ]] && { say "stopped"; exit 130; }
    "$T2" batch resume "$ID" </dev/null >>"$LOG" 2>&1 &
    child=$!
    while :; do
        wait "$child"
        rc=$?
        # (a signal ends the wait early: wait again while the driver (or its zombie) is there)
        ((rc > 128)) && kill -0 "$child" 2>/dev/null && continue
        break
    done
    child=
    say "t2 batch resume $ID: exit $rc"
    [[ -n $STOP ]] && { say "stopped"; exit 130; }
    [[ -f $DIR/report.txt ]] && continue
    sleep "$SLEEP" & wait $! 2>/dev/null
done
