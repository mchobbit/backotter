#!/usr/bin/env bash
# Build and test stock restic, rustic_core and rustic once (the versions the
# forks are based on, audit-lib.sh's STOCK) and keep the results on the
# hosts, so that fork-tests.sh compares the forks' tests with them without
# building stock again.  restic runs on Chopin, rustic_core then rustic on
# Cinqcent, the two hosts in parallel; each host's run waits for room in the
# shared ledger (cmp-run.sh --reserve) and runs at nice 10.
#
# Usage, on the Mac (no Go or Rust needed here):
#   stock-baseline.sh [--force] [--restic-host H] [--host H] [NAME ...]
#
# NAME is restic, rustic_core or rustic (default: all three).  A baseline
# already on either host (its exit file 0) is not run again unless --force;
# its results are still copied here.  --host H runs rustic_core and rustic
# on H instead of Cinqcent (e.g. --host chopin when Cinqcent can't);
# --restic-host H restic on H instead of Chopin (H needs Go).  H is a name
# (chopin), NAME.local or USER@HOST.
#
# Where things go:
#   HOST:/bkoscratch/bko-audit/src/NAME-COMMIT   the stock trees (git archive
#       of ~/git/NAME at STOCK's commit)
#   HOST:/bkoscratch/bko-audit/cache/            build caches, kept
#   HOST:~/backotter-work/bko-audit/stock/NAME-VERSION/   results, binaries
#       (audit-host.sh says what's in it)
#   $BKO_AUDIT_LOCAL/stock-baseline/NAME-VERSION/   the Mac's copy, without
#       bin/ (default ${TMPDIR:-/tmp}/bko-audit)
#   testvm/audit/stock-baseline.txt   the summary to commit: versions,
#       commits, toolchains, counts, stock's own failures by name
#
# Needs: ssh and rsync, git checkouts of the three in BKO_GIT (~/git) with
# the stock commits (upstream fetched), python3; on the hosts what
# audit-host.sh needs and ~/backotter-work/testvm/compare/cmp-run.sh.
#
# Examples:
#   stock-baseline.sh
#   stock-baseline.sh --host chopin rustic_core rustic
#   stock-baseline.sh --force restic

set -u
source "$(dirname "$0")/audit-lib.sh"

force=0 rhost=chopin chost=cinqcent
while [[ $# -gt 0 ]]; do
    case $1 in
        --force) force=1; shift ;;
        --host) chost=${2:?}; shift 2 ;;
        --restic-host) rhost=${2:?}; shift 2 ;;
        -*) die "usage: stock-baseline.sh [--force] [--restic-host H] [--host H] [NAME ...]" ;;
        *) break ;;
    esac
done
names=("$@")
[[ $# -gt 0 ]] || names=(restic rustic_core rustic)
rhost=$(host_of "$rhost") chost=$(host_of "$chost")
for n in "${names[@]}"; do
    c=$(stock_field "$n" 3)
    [[ -n $c ]] || die "unknown NAME $n (restic, rustic_core, rustic)"
    git -C "$BKO_GIT/$n" cat-file -e "$c^{commit}" 2>/dev/null || die "$BKO_GIT/$n has no $c: fetch upstream"
done

# The jobs per host: SPEC lines for audit-host.sh.
declare -a hosts=() specs=()
add_job() {  # HOST SPEC
    local i
    for i in "${!hosts[@]}"; do
        [[ ${hosts[$i]} == "$1" ]] && { specs[$i]+=" $2"; return; }
    done
    hosts+=("$1"); specs+=("$2")
}
t_start=$(date +%s)
for n in "${names[@]}"; do
    v=$(stock_field "$n" 2) c=$(stock_field "$n" 3)
    out=$RWORK/stock/$n-$v
    if [[ $n == restic ]]; then h=$rhost; else h=$chost; fi
    if [[ $force == 0 ]]; then
        have=
        for x in "$h" "$rhost" "$chost"; do done_on "$x" "$out" && { have=$x; break; }; done
        if [[ -n $have ]]; then
            log "$n $v: baseline on $have already (--force runs it again)"
            fetch "$have" "$out" "$LOCAL/stock-baseline/$n-$v"
            continue
        fi
    fi
    ship_tools "$h"
    ship_src "$h" "$n" "$c"
    add_job "$h" "$n:$RSRC/$n-$(short "$c"):\$HOME/$out"
done

pids=() tags=()
for i in "${!hosts[@]}"; do
    h=${hosts[$i]} tag=stock-${hosts[$i]#*@}
    tag=${tag%%.*}
    launch "$h" "$tag" ${specs[$i]}
    ( t0=$(date +%s); wait_tag "$h" "$tag"; rc=$?
      log "$h: $tag ended: exit $rc after $(( $(date +%s) - t0 ))s"; exit $rc ) &
    pids+=($!) tags+=("$h $tag")
done
fail=0
for i in "${!pids[@]}"; do
    wait "${pids[$i]}" || { fail=1; echo "${tags[$i]}: failed; see its log in ~/$RWORK/logs" >&2; }
done

# Copy the new results here, from the host that ran each.
for i in "${!hosts[@]}"; do
    for s in ${specs[$i]}; do
        IFS=: read -r n _ out _ <<<"$s"
        out=${out#\$HOME/}
        v=$(stock_field "$n" 2)
        if done_on "${hosts[$i]}" "$out"; then
            fetch "${hosts[$i]}" "$out" "$LOCAL/stock-baseline/$n-$v"
        else
            echo "$n $v: no complete results on ${hosts[$i]}:$out" >&2; fail=1
        fi
    done
done
log "all done in $(( $(date +%s) - t_start ))s"

# The summary to commit, from every baseline here.
sum=$here/stock-baseline.txt
{
    echo "# Stock baselines for fork-tests.sh (written by stock-baseline.sh; the"
    echo "# results themselves are on the hosts, ~/$RWORK/stock/NAME-VERSION)."
    while read -r n v c; do
        d=$LOCAL/stock-baseline/$n-$v
        [[ -f $d/results.tsv ]] || { echo; echo "$n $v $c: no baseline"; continue; }
        echo
        echo "$n $v  commit $c"
        sed -nE 's/^(host|date|go|rustc|cargo|rustic_core_used|walkdir_used|seconds)=/  \1: /p' "$d/info.txt"
        awk -F'\t' '{ printf "  step: %s exit %s, %ss\n", $1, $2, $3 }' "$d/steps.tsv" | grep -v '^  step: cross '
        awk -F'\t' '$1 ~ /^cross / { n++; if ($2 != 0) bad = bad " " substr($1, 7) }
            END { if (n) printf "  cross builds: %d, failing:%s\n", n, (bad == "" ? " none" : bad) }' "$d/steps.tsv"
        echo "  $(cat "$d/summary.txt")"
        awk -F'\t' '$3 == "fail" && $1 != "#vet" { n++; print "  stock failure: " $1 " :: " $2 }
            END { if (!n) print "  stock failures: none" }' "$d/results.tsv"
        awk -F'\t' '$1 == "#vet" && $2 != "(go vet)" { print "  stock vet finding: " $2 }' "$d/results.tsv"
    done <<<"$STOCK"
} >"$sum"
cat "$sum"
exit $fail
