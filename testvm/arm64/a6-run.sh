#!/usr/bin/env bash
# ARM64 A6's heavy testing on Bigfoot, from the Mac, in one command
# (docs/design/arm64.md, "What A6 built"): SticTest1 of the five arm64
# guests (both archivers), HoltTest1 1ARM (with its rescuer), 1ARM +img
# from A4's image, HostTest2 on Bigfoot, then the Test2 candidates.
#
# Run it from the checkout's top on the Mac:
#   testvm/arm64/a6-run.sh [--only S[,S]] [--skip S[,S]] [--bins DIR] [--img FILE]
#                          [--img-guests G,G] [--rebuild-rescuer] [--stamp NAME]
#                          [--tree DIR] [--host USER@HOST] [--no-batch-check]
#                          [--from-run STAMP]
#   testvm/arm64/a6-run.sh --dry-run [the same]   the plan, from a scratch copy on Bigfoot
#   testvm/arm64/a6-run.sh --status [STAMP]        the latest (or STAMP's) run: state, stages
#   testvm/arm64/a6-run.sh --fetch [STAMP]         its logs and summary to ~/backotter-work/a6-run/STAMP
#   testvm/arm64/a6-run.sh --stop [STAMP]          TERM to its runner (the stage cleans up, ABORTED)
#   testvm/arm64/a6-run.sh --list                  the stages and groups
#   testvm/arm64/a6-run.sh --help
#
# What a run does:
#   1. Ships a clean copy of committed HEAD (git archive, never the working
#      tree: other sessions keep uncommitted edits in it) to Bigfoot's
#      ~/backotter-work/a6-run/STAMP/tree (STAMP: YYYYMMDD-HHMMSS), with its
#      commit in tree/COMMIT.  --tree DIR ships DIR's files instead (git
#      ls-files of a private worktree, for testing the driver itself).
#   2. Starts testvm/arm64/a6-stages.sh there, detached: a transient system
#      service, bko-a6-STAMP.service, in bko.slice (sudo -n systemd-run
#      --slice=bko.slice ... setpriv --reuid=m --regid=m --init-groups
#      --reset-env), the same slice and setpriv as hosts.py job_prefix's
#      scopes.  A service and not a --user unit: Bigfoot's user manager can't
#      set the slice's AllowedCPUs and IOWeight (docs/design/arm64.md, A0).
#      Closing this terminal or sleeping the Mac doesn't stop it; it runs
#      until its stages end (2.5 to 4 h est.).
#   3. Prints how to follow it.  This command returns at once.
# The stages (a6-stages.sh --list), one at a time, each in its own log, the
# run going on after a stage fails; exit (RUN/exit) 1 if any stage failed:
#   preflight; stic:GUEST x5; rescuer; holt:GUEST x5 (1ARM); imgpin;
#   holt:GUEST+img (debian13-arm64, debian13-zfs-arm64); hosttest2:bin,
#   :loop-linux, :loop-sparse, :facts; t2:corpus; t2:limits; final.
# --only and --skip take stage names and the groups stic, 1ARM, img,
# hosttest2 and t2; preflight and final always run.  --from-run STAMP: the
# t2:corpus stage takes run STAMP's 1ARM results (its out/holt reports and
# HoltTest1's records of it) instead of this run's, for a rerun of failed
# stages that runs no holt stage (--only t2:corpus,...); STAMP must be a run
# on Bigfoot with out/holt.
#
# On Bigfoot (RUN = ~/backotter-work/a6-run/STAMP = /home/m/bko-arm64/work/a6-run/STAMP):
#   runner.log, summary.tsv and summary.txt (a row per stage: PASS, FAIL,
#   SKIP or ABORTED, rc, times, detail), state (running, done), exit,
#   logs/STAGE.log, out/ (each check's logs, the 1ARM reports, HostTest2's
#   results, the Test2 candidates), bins/ (the set tested), t2/ (the +img pin
#   and the raw dumps, which never leave Bigfoot); HoltTest1's own records in
#   /bkoscratch/holttest1d/a6-STAMP/.  --fetch copies runner.log, summary.*,
#   state, exit, logs/, out/ and expect/ to the Mac's ~/backotter-work/a6-run/STAMP/
#   (rsync, no --delete), where SticTest1's and Test2's Mac results live.
#
# Before it starts anything it checks Bigfoot answers, that no a6 run is
# active there, and `testvm/test2/t2 batch status` (a batch never holds
# Bigfoot, which is no default host, but the run says so; --no-batch-check
# skips it).  Exit: 0 started (or the asked command worked), 1 a check
# refused, 2 bad usage.
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
die() { echo "a6-run: $*" >&2; exit 2; }
refuse() { echo "a6-run: $*" >&2; exit 1; }
usage() { sed -n '2,60p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-2}"; }

# The host: hosts.json's bigfoot row (its ssh user and ip: mDNS isn't reliable there).
default_host() {
    python3 -I -B - "$REPO/testvm/hosts.json" <<'PY' 2>/dev/null
import json, sys
for r in json.load(open(sys.argv[1]))["hosts"]:
    if r["name"] == "bigfoot":
        print(r["ssh"].split("@")[0] + "@" + (r.get("ip") or r["ssh"].split("@")[1]))
PY
}

MODE=run STAMP="" TREE_DIR="" HOST="" BATCH_CHECK=1 BINS_OPT="" FROM_RUN=""
PASS=()      # the options a6-stages.sh takes
while [ $# -gt 0 ]; do
    case $1 in
        --only|--skip|--bins|--img|--img-guests)
            [ $# -ge 2 ] && [ -n "$2" ] || die "$1 needs a value"
            case $2 in *[!A-Za-z0-9_.,:+/~-]*) die "$1 $2: letters, digits and _ . , : + / ~ - only" ;; esac
            [ "$1" != --bins ] || BINS_OPT=$2
            PASS+=("$1" "$2"); shift 2 ;;
        --rebuild-rescuer) PASS+=("$1"); shift ;;
        --from-run)
            [ $# -ge 2 ] && [ -n "$2" ] || die "$1 needs a value"
            case $2 in [A-Za-z0-9]*) case $2 in *[!A-Za-z0-9_.-]*) die "--from-run $2: letters, digits, _ . - only" ;; esac ;; *) die "bad --from-run $2" ;; esac
            FROM_RUN=$2; PASS+=("$1" "$2"); shift 2 ;;
        --stamp|--tree|--host)
            [ $# -ge 2 ] && [ -n "$2" ] || die "$1 needs a value"
            case $1 in --stamp) STAMP=$2 ;; --tree) TREE_DIR=$2 ;; --host) HOST=$2 ;; esac
            shift 2 ;;
        --no-batch-check) BATCH_CHECK=0; shift ;;
        --dry-run) MODE=dry; shift ;;
        --status|--fetch|--stop)
            MODE=${1#--}; shift
            if [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; then STAMP=$1; shift; fi ;;
        --list) MODE=list; shift ;;
        -h|--help) usage 0 ;;
        *) die "unknown argument $1 (--help)" ;;
    esac
done
[ -z "$STAMP" ] || case $STAMP in [A-Za-z0-9]*) case $STAMP in *[!A-Za-z0-9_.-]*) die "--stamp/STAMP: letters, digits, _ . - only" ;; esac ;; *) die "bad STAMP $STAMP" ;; esac
case $MODE in status|fetch|stop) ;; *) [ -n "$STAMP" ] || STAMP=$(date '+%Y%m%d-%H%M%S') ;; esac
[ -z "$FROM_RUN" ] || [ "$FROM_RUN" != "$STAMP" ] || die "--from-run $FROM_RUN is this run's stamp"
HOST=${HOST:-$(default_host)}
[ -n "$HOST" ] || die "no bigfoot row in testvm/hosts.json (or give --host USER@HOST)"
case $HOST in *[!A-Za-z0-9@._-]*) die "bad --host $HOST" ;; esac
BASE='~/backotter-work/a6-run'
SSH="ssh -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=6 $HOST"
MAC_RESULTS=$HOME/backotter-work/a6-run

rsh() { $SSH "$@"; }

if [ "$MODE" = list ]; then
    bash "$HERE/a6-stages.sh" --list "${PASS[@]+"${PASS[@]}"}" || exit 2
    echo "groups: stic 1ARM img hosttest2 t2 (preflight and final always run)"
    exit 0
fi

# The latest run's stamp on Bigfoot, or STAMP checked.  The latest is the one
# last started (BASE/.latest, written at each start), else the newest run
# directory by its mtime (not by name: a --stamp NAME sorts after any date).
which_run() {
    if [ -n "$STAMP" ]; then
        rsh "test -d $BASE/$STAMP" || refuse "no run $STAMP on $HOST ($BASE)"
    else
        STAMP=$(rsh "cd $BASE 2>/dev/null || exit 0
            l=\$(head -n 1 .latest 2>/dev/null); if [ -n \"\$l\" ] && [ -d \"\$l\" ]; then echo \"\$l\"; exit 0; fi
            ls -1td [0-9A-Za-z]*/ 2>/dev/null | sed 's|/\$||' | grep -v '^dry-' | head -n 1")
        [ -n "$STAMP" ] || refuse "no a6 run on $HOST yet ($BASE)"
        case $STAMP in [A-Za-z0-9]*) case $STAMP in *[!A-Za-z0-9_.-]*) refuse "the latest run's name on $HOST isn't plain: $STAMP" ;; esac ;;
            *) refuse "the latest run's name on $HOST isn't plain: $STAMP" ;; esac
    fi
}

case $MODE in
status)
    which_run
    rsh "cd $BASE/$STAMP && echo \"run $STAMP: \$(cat state 2>/dev/null || echo 'not started'), unit \$(systemctl is-active bko-a6-$STAMP.service 2>/dev/null)\$( [ -f exit ] && echo \", exit \$(cat exit)\")\"
         [ -f stage ] && echo \"now: \$(cut -d' ' -f1 stage), since \$(date -d @\$(cut -d' ' -f3 stage) '+%T')\"
         echo; if [ -f summary.txt ]; then cat summary.txt; elif [ -f summary.tsv ]; then column -t -s \"\$(printf '\\t')\" summary.tsv; fi
         echo; echo '-- runner.log, last lines:'; tail -n 8 runner.log 2>/dev/null; exit 0"
    exit $? ;;
fetch)
    which_run
    mkdir -p "$MAC_RESULTS/$STAMP" || die "can't make $MAC_RESULTS/$STAMP"
    # (never the run's own t2/: the +img pin and the raw dumps stay on Bigfoot; never bins/ or tree/.
    # Anchored: out/t2/, the Test2 candidates, is fetched.)
    rsync -a -e "ssh -o BatchMode=yes -o ConnectTimeout=10" --exclude='/tree/' --exclude='/bins/' --exclude='/t2/' \
        --include='runner.log' --include='summary.*' --include='state' --include='exit' --include='COMMIT' \
        --include='logs/***' --include='out/***' --include='expect/***' --exclude='*' \
        "$HOST:backotter-work/a6-run/$STAMP/" "$MAC_RESULTS/$STAMP/" || refuse "rsync from $HOST failed"
    echo "a6-run: $STAMP's logs and summary in $MAC_RESULTS/$STAMP/"
    [ -f "$MAC_RESULTS/$STAMP/summary.txt" ] && cat "$MAC_RESULTS/$STAMP/summary.txt"
    exit 0 ;;
stop)
    which_run
    rsh "cd $BASE/$STAMP && [ \"\$(cat state 2>/dev/null)\" = running ] && p=\$(cat runner.pid) && [ -n \"\$p\" ] &&
         ps -o args= -p \"\$p\" | grep -q a6-stages.sh && kill -TERM \"\$p\" && echo \"TERM to $STAMP's runner, pid \$p: its stage stops and cleans up, the rest is ABORTED\"" ||
        refuse "run $STAMP isn't running (or its runner.pid isn't a6-stages.sh)"
    exit 0 ;;
esac

# --- dry-run and run: ship the tree ---------------------------------------------
cd "$REPO" || die "no $REPO"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/a6-run.XXXXXX") || die "no temporary directory"
trap 'rm -rf "$TMP"' EXIT
if [ -n "$TREE_DIR" ]; then
    [ -d "$TREE_DIR/testvm/arm64" ] || die "--tree $TREE_DIR has no testvm/arm64"
    COMMIT="$(git -C "$TREE_DIR" rev-parse HEAD 2>/dev/null || echo unknown)+tree:$TREE_DIR"
    [ -z "$(git -C "$TREE_DIR" status --porcelain 2>/dev/null)" ] || COMMIT="$COMMIT (dirty)"
    # Its tracked and untracked files (not the ignored ones), those that exist (a deletion isn't shipped).
    (cd "$TREE_DIR" && git ls-files -co --exclude-standard -z |
        python3 -I -c 'import os, sys; sys.stdout.write("".join(p + "\0" for p in sys.stdin.read().split("\0") if p and (os.path.isfile(p) or os.path.islink(p))))' |
        COPYFILE_DISABLE=1 tar --no-xattrs --null -T - -cf "$TMP/tree.tar") || die "can't archive $TREE_DIR"
else
    COMMIT=$(git rev-parse HEAD) || die "no HEAD"
    git archive --format=tar -o "$TMP/tree.tar" HEAD || die "git archive HEAD failed"
fi
SUM=$(shasum -a 256 "$TMP/tree.tar" | cut -d' ' -f1)

rsh true || refuse "$HOST doesn't answer (ssh)"
[ "$(rsh 'uname -m')" = aarch64 ] || refuse "$HOST isn't an aarch64 host"

if [ -n "$FROM_RUN" ]; then
    rsh "test -d $BASE/$FROM_RUN/out/holt" || refuse "--from-run $FROM_RUN: no run with 1ARM results on $HOST ($BASE/$FROM_RUN/out/holt)"
fi
if [ "$MODE" = dry ]; then
    D="dry-$STAMP"
else
    D=$STAMP
    rsh "test ! -e $BASE/$D" || refuse "$BASE/$D is there already on $HOST (--stamp another name)"
    act=$(rsh "systemctl list-units --plain --no-legend --state=active,activating 'bko-a6-*' 2>/dev/null | cut -d' ' -f1")
    [ -z "$act" ] || refuse "an a6 run is active on $HOST: $act (a6-run.sh --status; --stop)"
    if [ "$BATCH_CHECK" = 1 ] && [ -x "$REPO/testvm/test2/t2" ]; then
        b=$(cd "$REPO" && perl -e 'alarm shift; exec @ARGV' 120 testvm/test2/t2 batch status 2>&1 | head -n 20)
        echo "t2 batch status:"; echo "$b" | sed 's/^/   /'
        echo "$b" | grep -qi bigfoot && refuse "t2 batch status names bigfoot: a batch holds it (--no-batch-check runs anyway)"
    fi
fi
echo "a6-run: shipping $COMMIT ($(wc -c <"$TMP/tree.tar" | tr -d ' ') bytes, sha256 ${SUM:0:12}) to $HOST:$BASE/$D/tree"
rsh "mkdir -p $BASE/$D/tree && cd $BASE/$D && tar -C tree -xf - && printf '%s\n' '$COMMIT' >tree/COMMIT && cp tree/COMMIT COMMIT" <"$TMP/tree.tar" ||
    refuse "shipping the tree failed"
got=$(rsh "cd $BASE/$D/tree && find . ! -type d ! -name COMMIT | wc -l" | tr -d ' ')
want=$(tar -tf "$TMP/tree.tar" | grep -v '/$' | wc -l | tr -d ' ')
if [ "$got" != "$want" ]; then
    [ "$MODE" != dry ] || rsh "rm -rf $BASE/$D"
    refuse "the tree on $HOST has $got files, the archive $want"
fi

# hosttest2:bin's expectations, made here as GateTest2's gate:bin:version makes
# them on the Mac: each fork's version line at the set's commits (its
# restic-commit and rustic-commit on Bigfoot, --bins DIR as a6-stages.sh's
# preflight takes it, from ~/backotter-work) in BKO_GIT (default ~/git).
bsrc=${BINS_OPT:-'~/backotter-work/bko-bin/linux-arm64'}
mkdir -p "$TMP/exp"
if rsh "cd ~/backotter-work && d=$bsrc && { [ ! -f \$d/linux-arm64/rustic-bko ] || d=\$d/linux-arm64; } && cat \$d/restic-commit" >"$TMP/exp/restic-commit" &&
   rsh "cd ~/backotter-work && d=$bsrc && { [ ! -f \$d/linux-arm64/rustic-bko ] || d=\$d/linux-arm64; } && cat \$d/rustic-commit" >"$TMP/exp/rustic-commit" &&
   python3 -I -B "$HERE/a6-bin.py" expect --git "${BKO_GIT:-$HOME/git}" "$TMP/exp/restic-commit" "$TMP/exp/rustic-commit" "$TMP/exp/out" |
       sed 's/^/a6-run: hosttest2:bin will /' && [ -s "$TMP/exp/out/expect.tsv" ]; then
    rsh "mkdir -p $BASE/$D/expect && cat >$BASE/$D/expect/expect.tsv" <"$TMP/exp/out/expect.tsv" &&
        rsh "cat >$BASE/$D/expect/commits.json" <"$TMP/exp/out/commits.json" || refuse "shipping the expectations failed"
else
    echo "a6-run: NOTE no expectations for hosttest2:bin (the set's commit files on $HOST, or their commits in ${BKO_GIT:-$HOME/git}): that stage will FAIL" >&2
fi

# (each value is letters, digits and _ . , : + / ~ - only, checked above: no quoting needed
# through ssh, and inside the service's bash -c '...')
ARGS=""
for a in "${PASS[@]+"${PASS[@]}"}"; do ARGS="$ARGS $a"; done

if [ "$MODE" = dry ]; then
    rsh "bash $BASE/$D/tree/testvm/arm64/a6-stages.sh --dry-run --run $BASE/$D$ARGS"; rc=$?
    rsh "rm -rf $BASE/$D"
    echo "a6-run: dry run $( [ $rc = 0 ] && echo done || echo "FAILED ($rc)"); nothing ran (the scratch copy $BASE/$D is gone)."
    echo "Start it with: testvm/arm64/a6-run.sh $(for a in "${PASS[@]+"${PASS[@]}"}"; do printf '%s ' "$a"; done)"
    exit $rc
fi

UNIT=bko-a6-$STAMP
rsh "sudo -n systemd-run --quiet --unit=$UNIT --slice=bko.slice --collect --description='BackOtter ARM64 A6 run $STAMP (testvm/arm64/a6-run.sh)' \
       -- setpriv --reuid=m --regid=m --init-groups --reset-env -- \
       /bin/bash -c 'cd \$HOME/backotter-work && exec bash a6-run/$STAMP/tree/testvm/arm64/a6-stages.sh --run a6-run/$STAMP$ARGS'" ||
    refuse "systemd-run on $HOST failed (sudo -n? bko.slice?)"
rsh "printf '%s\n' $STAMP >$BASE/.latest"
sleep 3
st=$(rsh "systemctl is-active $UNIT.service 2>/dev/null; cat $BASE/$STAMP/state 2>/dev/null")
case $st in
    *running*) ;;
    *) echo "a6-run: $UNIT.service didn't start its stages ($(echo $st)); its journal:" >&2
       rsh "sudo -n journalctl -u $UNIT --no-pager -n 15 2>/dev/null" >&2
       exit 1 ;;
esac
echo "a6-run: started run $STAMP on $HOST as $UNIT.service (in bko.slice): $(echo $st)"
cat <<EOF

It runs on Bigfoot by itself now; this terminal and the Mac may go.  Follow it with:
  testvm/arm64/a6-run.sh --status $STAMP      stages so far, the current one, the log's tail
  testvm/arm64/a6-run.sh --fetch $STAMP       logs and summary to $MAC_RESULTS/$STAMP/
  testvm/arm64/a6-run.sh --stop $STAMP        stop it (the stage cleans up, the rest ABORTED)
On Bigfoot: ~/backotter-work/a6-run/$STAMP/runner.log, summary.txt; journalctl -u $UNIT
EOF
exit 0
