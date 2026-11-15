#!/usr/bin/env bash
# A stand-in for ht1-farm.sh (T2_BATCH_HT1_FARM, tests): RUN [--join] --hosts H,H --queue-host Q
# [LINE...].  It writes the todo (not with --join) and starts the fake runner (ht1-queue.sh
# beside this script) for each host, detached, FAKE_HOST set; every call is logged to
# MAC_ROOT/stub-calls.log.  Every fake host is this machine, so they share one queue/RUN/done
# (each row names its host); the watcher reads it from each "host" and drops the repeats.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
RUN="" hosts="" q="" join=no lines=()
while [[ $# -gt 0 ]]; do
    case $1 in
    --hosts) hosts=$2; shift 2 ;;
    --queue-host) q=$2; shift 2 ;;
    --join) join=yes; shift ;;
    *) if [[ -z $RUN ]]; then RUN=$1; else lines+=("$1"); fi; shift ;;
    esac
done
printf '%s %s FARM %s join=%s hosts=%s q=%s %s\n' "$(date +%s)" "$$" "$RUN" "$join" "$hosts" "$q" "${lines[*]+${lines[*]}}" \
    >>"$T2_MAC_ROOT/stub-calls.log"
[[ -z ${FAKE_FARM_RC:-} ]] || { echo "ht1-farm: refused (stub)" >&2; exit "$FAKE_FARM_RC"; }
Q=$T2_BATCH_HT1_ROOT/queue/$RUN
mkdir -p "$Q"
if [[ $join == no ]]; then
    [[ ! -e $Q/todo ]] || { echo "ht1-farm: $q already has $Q/todo" >&2; exit 2; }
    printf '%s\n' "${lines[@]}" >"$Q/todo"
fi
for h in ${hosts//,/ }; do
    FAKE_HOST=$h nohup bash "$HERE/ht1-queue.sh" "$RUN" 2600 1 </dev/null >/dev/null 2>&1 &
    echo "-- $h: runner started"
done
exit 0
