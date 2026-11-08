# Shared by stock-baseline.sh and fork-tests.sh (sourced, on the Mac): the
# stock versions, the hosts' layout, and copying, launching and waiting on
# audit-host.sh there.  Not run by itself.
#
#   BKO_GIT          where the checkouts are (default ~/git): restic,
#                    rustic_core, rustic
#   BKO_AUDIT_LOCAL  the Mac's copy of the results (default
#                    ${TMPDIR:-/tmp}/bko-audit): stock-baseline/NAME-VERSION,
#                    fork-tests/NAME-COMMIT
#   RESERVE          DISK_GIB MEM_GIB for cmp-run.sh --reserve (default "6 12")

# Stock: what the forks are based on (their merge-bases with upstream).
# NAME VERSION COMMIT; rustic_core 0.13.0 is the crates.io release stock
# rustic 0.11.4's Cargo.lock takes.
STOCK="restic 0.19.1 6aa3a516ce654808a1f28f9fa21e9b7c8e6e90bf
rustic_core 0.13.0 1030be4568e5f83ddf9a1d5354479adc57dce227
rustic 0.11.4 6b96f976ff437a0528a4742358b172b32c3e3932"

BKO_GIT=${BKO_GIT:-$HOME/git}
LOCAL=${BKO_AUDIT_LOCAL:-${TMPDIR:-/tmp}/bko-audit}
LOCAL=${LOCAL%/}
read -r RESERVE_DISK RESERVE_MEM <<<"${RESERVE:-6 12}"
# On the hosts (results and tools in the home, sources and caches on
# /bkoscratch); RWORK relative to the login's home.
RWORK=backotter-work/bko-audit
RSRC=/bkoscratch/bko-audit/src
CMP_RUN='$HOME/backotter-work/testvm/compare/cmp-run.sh'
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=20)

die() { echo "$(basename "$0" .sh): $*" >&2; exit 1; }
log() { echo "$(date '+%H:%M:%S') $*"; }
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)

stock_field() {  # NAME FIELD(2 version, 3 commit)
    awk -v n="$1" -v f="$2" '$1 == n { print $f }' <<<"$STOCK"
}
short() { echo "${1:0:12}"; }

host_of() {  # chopin -> m@chopin.local; anything with @ or a dot as is
    case $1 in
        *@*) echo "$1" ;;
        *.*) echo "m@$1" ;;
        *) echo "m@$1.local" ;;
    esac
}

# ship_tools HOST: audit-host.sh, results.py and prep-rustic-src.sh to
# HOST:~/backotter-work/bko-audit/bin (only there: never testvm/ or stick/).
ship_tools() {
    "${SSH[@]}" "$1" "mkdir -p $RWORK/bin $RWORK/logs" &&
        rsync -p --checksum "$here/audit-host.sh" "$here/results.py" "$repo/stick/prep-rustic-src.sh" \
            "$1:$RWORK/bin/" || die "can't copy the tools to $1"
}

# ship_src HOST NAME COMMIT: HOST:/bkoscratch/bko-audit/src/NAME-SHORT, a git
# archive of ~/git/NAME's COMMIT, unless one is there already.
ship_src() {
    local h=$1 n=$2 c=$3 d
    d=$RSRC/$n-$(short "$c")
    if "${SSH[@]}" "$h" "test -f $d/.bko-src"; then
        return 0
    fi
    log "$h: $d from $BKO_GIT/$n $c"
    git -C "$BKO_GIT/$n" archive --format=tar "$c" |
        "${SSH[@]}" "$h" "set -e; rm -rf $d.part; mkdir -p $d.part; tar -x -C $d.part; echo $c >$d.part/.bko-src; mv $d.part $d" ||
        die "can't copy $n $c to $h:$d"
}

# done_on HOST OUT COMMIT: OUT (relative to the home) holds a finished run
# of COMMIT.
done_on() {
    "${SSH[@]}" "$1" "test \"\$(cat $2/exit 2>/dev/null)\" = 0 -a -s $2/results.tsv && grep -qx commit=$3 $2/info.txt"
}

# local_ok DIR COMMIT: the Mac's copy DIR holds results of COMMIT.
local_ok() {
    [[ -s $1/results.tsv ]] && grep -qx "commit=$2" "$1/info.txt" 2>/dev/null
}

# launch HOST TAG SPEC...: audit-host.sh SPEC... on HOST, detached, under
# cmp-run.sh --reserve, logging to ~/backotter-work/bko-audit/logs/TAG.log
# and TAG.exit.  A TAG already running there isn't started again: the caller
# then waits on it as if just launched.
launch() {
    local h=$1 tag=$2 L; shift 2
    L=$RWORK/logs/$tag
    if "${SSH[@]}" "$h" "test ! -e $L.exit -a -e $L.pid && kill -0 \$(cat $L.pid) 2>/dev/null"; then
        log "$h: $tag is already running; waiting on it"
        return 0
    fi
    # Every SPEC path is $HOME-relative or absolute and has no blanks.
    local specs="$*" cmd
    cmd="rm -f $L.exit; setsid -f bash -c 'echo \$\$ >\$HOME/$L.pid; exec </dev/null;"
    cmd+=" WEAR_TAG=audit:$tag $CMP_RUN --reserve $RESERVE_DISK $RESERVE_MEM nice -n 10 \$HOME/$RWORK/bin/audit-host.sh $specs"
    cmd+=" >\$HOME/$L.log 2>&1; echo \$? >\$HOME/$L.exit' </dev/null >/dev/null 2>&1"
    "${SSH[@]}" -n "$h" "$cmd" || die "can't start $tag on $h"
    log "$h: started $tag (log $h:~/$L.log)"
}

# wait_tag HOST TAG: until TAG.exit is there; prints audit-host.sh's last
# line every 10 minutes; returns TAG's exit.
wait_tag() {
    local h=$1 L=$RWORK/logs/$2 rc n=0
    local SSH=("${SSH[@]}" -n)
    while :; do
        rc=$("${SSH[@]}" "$h" "cat $L.exit 2>/dev/null")
        [[ -n $rc ]] && break
        if ! "${SSH[@]}" "$h" "test -e $L.pid && kill -0 \$(cat $L.pid) 2>/dev/null"; then
            sleep 5
            rc=$("${SSH[@]}" "$h" "cat $L.exit 2>/dev/null")
            [[ -n $rc ]] && break
            echo "$h: $2 ended without $L.exit" >&2; return 1
        fi
        (( n++ % 20 == 19 )) && log "$h: $2: $("${SSH[@]}" "$h" "tail -1 $L.log 2>/dev/null")"
        sleep 30
    done
    return "$rc"
}

# fetch HOST OUT LOCALDIR: the results (not bin/, not the caches) to the
# Mac, in place of what LOCALDIR held.
fetch() {
    mkdir -p "$3" && rsync -r --checksum --delete --exclude bin/ --exclude .lock "$1:$2/" "$3/" ||
        die "can't copy $1:$2 here"
}
