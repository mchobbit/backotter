#!/usr/bin/env bash
# One HoltTest1 queue over every test host, in one command: write the todo on
# the queue host and start an ht1-queue.sh runner on each host of the farm,
# each with its own MAXJOBS from the host table.  Until now this was done by
# hand, one ssh per host (README "Queues: ht1-queue.sh"), which is why runs
# ended up on Chopin and Cinqcent alone while Cage and Copland idled.
#
# Usage (here, from the checkout; the hosts run their ~/backotter-work copy,
# so run testvm/sync-hosts.sh first):
#   testvm/holttest1d/ht1-farm.sh RUN GUEST[+VARIANT][=D][%M] ...
#   testvm/holttest1d/ht1-farm.sh RUN --hosts chopin,cage GUEST ...
#   testvm/holttest1d/ht1-farm.sh --dry-run RUN GUEST ...
#
# RUN names the queue, as ht1-queue.sh takes it (a letter or digit, then
# letters, digits, . _ -); the guests are todo lines, exactly as the todo
# takes them.  Each is checked here before anything starts, so a typo costs
# nothing.
#
# The hosts are the host table's default, enabled amd64 hosts
# (testvm/hosts.json, through testvm/hosts.sh: never a list of names here),
# or those --hosts names, in the table's order.  The first of them is the
# queue host: its /bkoscratch/holttest1d/queue/RUN/todo is the one todo, and
# every other host's runner takes lines from it over ssh.  Each host gets
#   ht1-queue.sh RUN 2600 MAXJOBS [QHOST.local]
# with MAXJOBS its holt_jobs in the table (hosts.py limit: 3 on a big host,
# 1 on a small one), under setsid, so the runners live on after this script
# and after the ssh connections close.  DISK_OVER=1 goes to a host whose
# table row has holt_disk_mode (Chopin and Cinqcent), as HoltTest2 sets it.
# A guest pinned to one host in testvm/test2/classes.json (holt.pins:
# openbsd8 on Chopin, dragonfly6 and redox on Cinqcent, whose one-time
# rescuer prep or tools exist there only) is left in the todo by every other
# host's runner (ONLY_ELSEWHERE).  Nothing else is placed here: each runner
# itself leaves the lines its host can never run (its ceiling, a missing
# image, no host ZFS, no rescuer or debs) for the hosts that can, so the
# farm needs no plan.
#
# The todo is written once.  A RUN whose todo already exists is refused,
# because writing it again would run the lines a runner has already taken;
# so is one with a stop or abort file left over (its runners would end at
# once).  Use another RUN, or go on by hand as the README says.  The todo is
# the queue host's alone, but stop and abort are read by each runner on its
# own host (ht1-queue.sh looks at its own $Q/stop and $Q/abort), so every
# host of the farm is asked about those before anything starts.
#
# A host that doesn't answer that question, or that the table gives no ssh
# destination for, is left out of the farm: it is named on stderr, the hosts
# that are up go ahead, and the exit status is 1 at the end ("no runner on
# HOST"), exactly as a host whose runner failed to start is handled.  One
# host rebooting is no reason to refuse a whole run, and a host that can't
# be reached runs nothing either way.  The queue host is the exception:
# its todo is the run, so the run ends there and then if it is unreachable.
#
# Each runner's output goes to /bkoscratch/holttest1d/RUN-runner-HOST.out on
# its host (what it says when it refuses to start), and the runner's own log,
# the done rows and each job's log are where ht1-queue.sh puts them:
# queue/RUN/runner-HOST.log, queue/RUN/done, RUN-GV.log.  At the end this
# script prints the commands to watch the farm and to stop it.
#
# HT_HOLT, HT_HOLT_BIN, HT_BKO_BIN, HT_ROUND, HT_RUSTIC, HT_RAM, HT_WORK,
# HT_RECORDS, MINFREE_GB and BIG_GIB in this script's environment are passed
# to every runner (ht1-queue.sh refuses any other HT_ one, and reads only
# these besides ONLY_ELSEWHERE and DISK_OVER).  WEAR_TAG is not: each job
# tags its own wear.
#
# Needs: ssh to each host (BatchMode), bash 3.2, and the hosts' synced
# testvm/ (sync-hosts.sh).  It writes nothing here.

set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
TESTVM=$(cd "$HERE/.." && pwd)
REMOTE=${BKO_WORK_REMOTE:-backotter-work}
L=/bkoscratch/holttest1d
BASEPORT=2600
# A todo line, as ht1-queue.sh's LINE_RE takes it.
LINE_RE='^[A-Za-z0-9][A-Za-z0-9_.-]*([+](bigger|oneshot|degraded|smaller|fitbigger|nearfull|refuse|img))?(=[0-9.]+)?(%[0-9.]+)?$'
# The settings a runner may have from here (ht1-queue.sh refuses any other HT_ one).
# Only what ht1-queue.sh itself reads: NODISK_POLLS is cmp-run.sh's queue worker's,
# which a HoltTest1 runner never is (it reserves room with --reserve), and nothing
# anywhere reads an ONLY_ELSEWHERE_EXTRA; both were passed on and dropped on the floor.
PASS_ENV=(HT_HOLT HT_HOLT_BIN HT_BKO_BIN HT_ROUND HT_RUSTIC HT_RAM HT_WORK HT_RECORDS
          MINFREE_GB BIG_GIB)

die() { echo "ht1-farm: $*" >&2; exit 2; }
usage() { sed -n '2,/^set -uo/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2; exit 2; }
rq() { printf '%q' "$1"; }   # one word for the remote shell

# shellcheck source=../hosts.sh
. "$TESTVM/hosts.sh" || die "no $TESTVM/hosts.sh"

dry=no named="" RUN="" lines=()
while [[ $# -gt 0 ]]; do
    case $1 in
    --dry-run) dry=yes; shift ;;
    --hosts) [[ $# -ge 2 ]] || die "--hosts needs a list"; named=$2; shift 2 ;;
    --hosts=*) named=${1#--hosts=}; shift ;;
    -h|--help) usage ;;
    -*) die "no such option $1 (--hosts H,H, --dry-run)" ;;
    *) if [[ -z $RUN ]]; then RUN=$1; else lines+=("$1"); fi; shift ;;
    esac
done
[[ -n $RUN ]] || usage
((${#lines[@]})) || die "no guests: ht1-farm.sh RUN GUEST[+VARIANT]..."
[[ $RUN =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "RUN: a letter or digit, then letters, digits, . _ -"
[[ $RUN != debug && $RUN != debug-state ]] || die "RUN $RUN is ht1-debug.sh's"
for l in "${lines[@]}"; do
    [[ $l =~ $LINE_RE ]] || die "not GUEST[+VARIANT][=D][%M]: $l"
done

# The farm: --hosts, else the table's default amd64 hosts, in the table's order.
all=$(hosts_names --default --arch amd64) || die "no hosts from the host table"
hosts=()
if [[ -n $named ]]; then
    known=$(hosts_names) || die "no hosts from the host table"
    for h in ${named//,/ }; do
        [[ -n $h ]] || continue
        grep -qx "$h" <<<"$known" || die "no such host $h (known: $(hosts_phrase))"
        grep -qx "$h" <<<"$(printf '%s\n' "${hosts[@]+"${hosts[@]}"}")" || hosts+=("$h")
    done
    # --hosts "," or "" names nothing: say so, rather than let the empty array
    # below be an unbound variable under set -u (bash 3.2).
    ((${#hosts[@]})) || die "--hosts $named names no host (--hosts H,H)"
    # The table's order, whatever --hosts' is.
    ordered=()
    while IFS= read -r h; do
        for x in "${hosts[@]}"; do [[ $x == "$h" ]] && ordered+=("$h"); done
    done <<<"$known"
    hosts=("${ordered[@]}")
else
    while IFS= read -r h; do [[ -n $h ]] && hosts+=("$h"); done <<<"$all"
fi
((${#hosts[@]})) || die "no host to run on"
QHOST=${hosts[0]}
QSSH=$(hosts_get "$QHOST" ssh) || die "no ssh destination for $QHOST"
# What the other hosts' runners reach the queue host by (ht1-queue.sh's QHOST, which it
# uses as m@QHOST): the row's host_key_alias, else the host part of its ssh.
QADDR=$(hosts_get "$QHOST" host_key_alias) || QADDR=""
[[ -n $QADDR ]] || QADDR=${QSSH#*@}

# The guests pinned to one host (classes.json holt.pins): every other host's
# runner leaves their lines in the todo (ONLY_ELSEWHERE).
pins=$(python3 -I -B -c '
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        pins = (json.load(f)["holt"].get("pins") or {})
except (OSError, ValueError, KeyError):
    sys.exit(0)                      # no class table: nothing is pinned here
for g, h in sorted(pins.items()):
    print(g, h)
' "$TESTVM/test2/classes.json") || pins=""
only_elsewhere() {  # HOST -> the pinned guests of this run that are not its own
    local h=$1 g ph out=""
    [[ -n $pins ]] || return 0
    while read -r g ph; do
        [[ -n $g && $ph != "$h" ]] || continue
        for l in "${lines[@]}"; do [[ ${l%%[+=%]*} == "$g" ]] && { out+="$g "; break; }; done
    done <<<"$pins"
    echo "${out% }"
}

# sshq for a command with nothing to send it (-n: its stdin is /dev/null, so a
# loop's ssh never eats this script's own stdin); sshin when the command reads
# what we pipe to it (the todo), where -n would hand it an empty file.
sshq() { ssh -n -o BatchMode=yes -o ConnectTimeout=15 "$@"; }
sshin() { ssh -o BatchMode=yes -o ConnectTimeout=15 "$@"; }

Q=$L/queue/$RUN
echo "== queue $RUN on $QHOST ($(printf '%s ' "${hosts[@]}" | sed 's/ $//'))"
# Each host's ssh destination once (every one is needed below, twice).  A host
# the table gives none for can be reached by nothing here, so it is left out
# and named at the end; the queue host, whose todo is the run, is not.
dests=() named_hosts=() failed=()
for h in "${hosts[@]}"; do
    d=$(hosts_get "$h" ssh) || d=""
    if [[ -z $d ]]; then
        [[ $h != "$QHOST" ]] || die "no ssh destination for the queue host $QHOST in the host table"
        echo "ht1-farm: no ssh destination for $h in the host table: left out" >&2
        failed+=("$h")
        continue
    fi
    named_hosts+=("$h"); dests+=("$d")
done
hosts=("${named_hosts[@]}")

# A RUN already in use: refused before anything starts.  The todo is the queue
# host's, but every runner reads stop and abort on its own host (ht1-queue.sh's
# $Q/stop and $Q/abort, Q=$L/queue/$RUN there), so a leftover one anywhere would
# end that host's runner at once.  A host that doesn't answer is left out of the
# farm here (it could run nothing anyway) and named at the end, rather than
# ending a run the other hosts can do; the queue host alone is fatal.
up=() updest=() i=0
for h in "${hosts[@]}"; do
    d=${dests[$i]}; i=$((i + 1))
    what="stop abort"
    [[ $h != "$QHOST" ]] || what="todo stop abort"
    if ! left=$(sshq "$d" "for f in $what; do [ -e $(rq "$Q")/\$f ] && echo \$f; done; exit 0"); then
        [[ $h != "$QHOST" ]] ||
            die "can't reach the queue host $QHOST ($d): its $Q/todo is the whole run"
        echo "ht1-farm: can't reach $h ($d): left out of the farm" >&2
        failed+=("$h")
        continue
    fi
    if [[ -n $left ]]; then
        die "$h already has $Q/$(tr '\n' ' ' <<<"$left" | sed 's/ $//'): use another RUN, or
    see README \"Stop and abort\" (a todo written again would rerun taken lines)"
    fi
    up+=("$h"); updest+=("$d")
done

# The todo, biggest guest first (ht1-queue.sh --order, on the queue host, where
# ram-size.sh knows that host's sizes).
order_cmd="cd ~/$REMOTE && testvm/holttest1d/ht1-queue.sh --order"
for l in "${lines[@]}"; do order_cmd+=" $(rq "$l")"; done
todo=$(sshq "$QSSH" "$order_cmd") && [[ -n $todo ]] || die "ht1-queue.sh --order failed on $QHOST"
want=$(wc -l <<<"$todo" | tr -d ' ')
if [[ $dry == yes ]]; then
    echo "-- the todo ($want lines), biggest first:"
    sed 's/^/     /' <<<"$todo"
else
    # sshin, not sshq: the todo is this command's stdin.  The remote wc -l is read
    # back and must be the lines we sent, because a todo that arrived short or
    # empty would have every runner exit at once with the run undone.
    got=$(sshin "$QSSH" "mkdir -p $(rq "$Q") && cat >$(rq "$Q/todo") && wc -l <$(rq "$Q/todo")" \
              <<<"$todo" | tr -d ' ') ||
        die "can't write $Q/todo on $QHOST"
    [[ $got == "$want" ]] ||
        die "$Q/todo on $QHOST has $got of the $want lines sent: remove it and run this again"
    echo "-- $QHOST: $Q/todo, $want lines"
fi

# A runner on every host, each with its own MAXJOBS, under setsid: it lives on
# after this ssh ends.
i=0
for h in "${up[@]}"; do
    hssh=${updest[$i]}; i=$((i + 1))
    max=$(hosts_limit "$h" holt_jobs) || max=1
    envs=()
    [[ $(hosts_limit "$h" holt_disk_mode) == true ]] && envs+=("DISK_OVER=1")
    oe=$(only_elsewhere "$h")
    [[ -z $oe ]] || envs+=("ONLY_ELSEWHERE=$(rq "$oe")")
    for v in "${PASS_ENV[@]}"; do
        [[ -n ${!v:-} ]] && envs+=("$v=$(rq "${!v}")")
    done
    out=$L/$RUN-runner-$h.out
    cmd="cd ~/$REMOTE && setsid -f env ${envs[*]+${envs[*]}} testvm/holttest1d/ht1-queue.sh"
    cmd+=" $(rq "$RUN") $BASEPORT $max"
    [[ $h == "$QHOST" ]] || cmd+=" $(rq "$QADDR")"
    cmd+=" </dev/null >$(rq "$out") 2>&1"
    if [[ $dry == yes ]]; then
        echo "-- $h would run: $cmd"
        continue
    fi
    if sshq "$hssh" "$cmd"; then
        echo "-- $h: runner started, at most $max jobs${oe:+, leaving $oe to the others} ($out)"
    else
        failed+=("$h")
    fi
done

if [[ $dry == yes ]]; then
    echo "-- --dry-run: nothing was written and no runner started"
    ((${#failed[@]} == 0)) || echo "-- left out of the farm, not reached: ${failed[*]}"
    exit 0
fi
cat <<EOF
-- watch it:
     ssh $QSSH 'tail -f $Q/runner-$QHOST.log'            # one host's runner
     ssh $QSSH 'wc -l <$Q/todo; cat $Q/done'             # what is left, and every job
     for h in $(printf '%s ' "${up[@]}" | sed 's/ $//'); do
         ssh m@\$h.local "tail -3 $Q/runner-\$h.log"; done
-- stop it (on each host, as the README says):
     ssh HOST 'touch $Q/stop'                            # start nothing more there
     ssh HOST 'touch $Q/abort'                           # also end its running jobs
EOF
((${#failed[@]} == 0)) || { echo "ht1-farm: no runner on ${failed[*]}" >&2; exit 1; }
