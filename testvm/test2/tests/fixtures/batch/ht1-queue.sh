#!/usr/bin/env bash
# A stand-in for ht1-queue.sh's runner (tests): RUN 2600 MAXJOBS [QHOST], with FAKE_HOST its
# host.  It takes the todo's lines one at a time (under a mkdir lock), "runs" each for FAKE_JOB_S
# and appends a done row; a guest in FAKE_FAIL (space-separated) ends rc 1.  It ends when the
# todo is empty or queue/RUN/stop exists.  Roots: T2_BATCH_HT1_ROOT.
set -u
RUN=$1
Q=$T2_BATCH_HT1_ROOT/queue/$RUN
mkdir -p "$Q"
while :; do
    [[ -e $Q/stop ]] && break
    until mkdir "$Q/.lk" 2>/dev/null; do sleep 0.05; done
    line=$(head -n 1 "$Q/todo" 2>/dev/null)
    if [[ -n $line ]]; then tail -n +2 "$Q/todo" >"$Q/todo.new"; mv "$Q/todo.new" "$Q/todo"; fi
    rmdir "$Q/.lk"
    [[ -n $line ]] || break
    sleep "${FAKE_JOB_S:-0.3}"
    g=${line%%[=%]*}; gv=${g//+/-}; rc=0
    for f in ${FAKE_FAIL:-}; do [[ $f == "$g" ]] && rc=1; done
    [[ -s $Q/done ]] || printf 'end_utc\thost\tgv\trc\td_gib\tm_gib\tport\twait_s\trun_s\n' >>"$Q/done"
    printf '%s\t%s\t%s\t%s\t4\t4\t2600\t0\t1\n' "$(date -u +%FT%TZ)" "$FAKE_HOST" "$gv" "$rc" >>"$Q/done"
done
