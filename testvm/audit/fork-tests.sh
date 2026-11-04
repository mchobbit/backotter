#!/usr/bin/env bash
# Build and test the forks (restic on Chopin, rustic_core then rustic on
# Cinqcent, in parallel) as stock-baseline.sh tested stock, with the same
# caches, and compare each fork's per-test results with its stock baseline.
# Prints, per fork: the tests that fail on the fork but not on stock (with
# their output), the build targets (restic's GOOS/GOARCH cross builds, go
# vet, crates that don't compile) that fail on the fork only, the tests the
# fork skips that pass on stock, the tests stock has and the fork lacks, the
# tests the fork added, and stock's own known failures.
#
# Usage, on the Mac (no Go or Rust needed here):
#   fork-tests.sh [--force] [--restic-host H] [--rustic-host H] [NAME[=REV] ...]
#
# NAME is restic, rustic_core or rustic; naming any runs only those.  REV
# (default: the backotter branch of BKO_GIT/NAME, ~/git/NAME) is any commit
# there.  rustic is built against the rustic_core REV of this run (as
# build-round.sh builds rustic-bko), not the commit its Cargo.lock pins;
# it says so when the two differ.  Results already on the host for the
# same commits are reused unless --force.  --rustic-host H runs rustic_core
# and rustic on H instead of Cinqcent; --restic-host H restic instead of
# Chopin.  H is a name (chopin), NAME.local or USER@HOST.
#
# Exit: 0 if no fork fails anything its stock baseline doesn't; 1 if one
# does (a test, a build target, go vet); 2 if a run didn't complete or a
# baseline is missing (run stock-baseline.sh first).
#
# Where things go:
#   HOST:/bkoscratch/bko-audit/src/NAME-COMMIT  the trees (git archive), and
#       rustic-COMMIT+core-COMMIT, rustic pointed at that rustic_core
#   HOST:~/backotter-work/bko-audit/fork/NAME-COMMIT/  results, binaries
#       (rustic-COMMIT+core-COMMIT for rustic)
#   $BKO_AUDIT_LOCAL/fork-tests/   the Mac's copy, and NAME.report: the
#       comparison (default ${TMPDIR:-/tmp}/bko-audit)
#
# Needs: as stock-baseline.sh, and its baselines (here or on a host).
#
# Examples:
#   fork-tests.sh
#   fork-tests.sh restic
#   fork-tests.sh --rustic-host chopin rustic_core=6e8b009 rustic=f989942

set -u
source "$(dirname "$0")/audit-lib.sh"

force=0 rhost=chopin chost=cinqcent
while [[ $# -gt 0 ]]; do
    case $1 in
        --force) force=1; shift ;;
        --restic-host) rhost=${2:?}; shift 2 ;;
        --rustic-host) chost=${2:?}; shift 2 ;;
        -*) die "usage: fork-tests.sh [--force] [--restic-host H] [--rustic-host H] [NAME[=REV] ...]" ;;
        *) break ;;
    esac
done
rhost=$(host_of "$rhost") chost=$(host_of "$chost")
names=() rev_restic=backotter rev_rustic_core=backotter rev_rustic=backotter
for a in "$@"; do
    n=${a%%=*}
    case $n in restic | rustic_core | rustic) ;; *) die "unknown NAME $n (restic, rustic_core, rustic)" ;; esac
    names+=("$n")
    [[ $a == *=* ]] && eval "rev_$n=\${a#*=}"
done
[[ ${#names[@]} -gt 0 ]] || names=(restic rustic_core rustic)
commit_restic=$(git -C "$BKO_GIT/restic" rev-parse --verify "$rev_restic^{commit}") || die "no restic $rev_restic"
commit_rustic_core=$(git -C "$BKO_GIT/rustic_core" rev-parse --verify "$rev_rustic_core^{commit}") || die "no rustic_core $rev_rustic_core"
commit_rustic=$(git -C "$BKO_GIT/rustic" rev-parse --verify "$rev_rustic^{commit}") || die "no rustic $rev_rustic"

# The baselines, here (a copy from the host that has one, if not yet).
for n in "${names[@]}"; do
    v=$(stock_field "$n" 2) sc=$(stock_field "$n" 3)
    d=$LOCAL/stock-baseline/$n-$v
    if ! local_ok "$d" "$sc"; then
        for x in "$rhost" "$chost"; do
            done_on "$x" "$RWORK/stock/$n-$v" "$sc" && { fetch "$x" "$RWORK/stock/$n-$v" "$d"; break; }
        done
    fi
    local_ok "$d" "$sc" || { echo "no stock baseline of $n $v ($sc): run stock-baseline.sh first" >&2; exit 2; }
    c=commit_$n
    git -C "$BKO_GIT/$n" merge-base --is-ancestor "$sc" "${!c}" ||
        echo "warning: $n ${!c:0:12} isn't based on stock $v ($sc): the baseline may be stale (update STOCK in audit-lib.sh and run stock-baseline.sh)" >&2
done

out_of() {  # NAME -> results dir relative to the home
    case $1 in
        rustic) echo "$RWORK/fork/rustic-$(short "$commit_rustic")+core-$(short "$commit_rustic_core")" ;;
        *) local c=commit_$1; echo "$RWORK/fork/$1-$(short "${!c}")" ;;
    esac
}

declare -a hosts=() specs=() jobnames=()
add_job() {  # HOST NAME SPEC
    local i
    for i in "${!hosts[@]}"; do
        [[ ${hosts[$i]} == "$1" ]] && { specs[$i]+=" $3"; jobnames[$i]+=" $2"; return; }
    done
    hosts+=("$1"); specs+=("$3"); jobnames+=("$2")
}
hostfor() { if [[ $1 == restic ]]; then echo "$rhost"; else echo "$chost"; fi; }

t_start=$(date +%s)
for n in "${names[@]}"; do
    h=$(hostfor "$n") out=$(out_of "$n")
    c=commit_$n
    if [[ $n == rustic ]]; then
        pin=$(git -C "$BKO_GIT/rustic" show "$commit_rustic:Cargo.lock" |
            awk '$0 == "name = \"rustic_core\"" { f = 1; next } f && /^source = / { print $3; exit }')
        [[ $pin == *"#$commit_rustic_core\"" ]] ||
            echo "note: rustic ${commit_rustic:0:12}'s Cargo.lock takes rustic_core from $pin; built here with rustic_core ${commit_rustic_core:0:12}"
    fi
    if [[ $force == 0 ]] && done_on "$h" "$out" "${!c}"; then
        log "$n ${!c:0:12}: results on $h already (--force runs it again)"
        continue
    fi
    ship_tools "$h"
    ship_src "$h" "$n" "${!c}"
    spec="$n:$RSRC/$n-$(short "${!c}"):\$HOME/$out"
    if [[ $n == rustic ]]; then
        ship_src "$h" rustic_core "$commit_rustic_core"
        spec+=":$RSRC/rustic_core-$(short "$commit_rustic_core")"
    fi
    add_job "$h" "$n" "$spec"
done

pids=() tags=()
for i in "${!hosts[@]}"; do
    h=${hosts[$i]} tag=fork-${hosts[$i]#*@}
    tag=${tag%%.*}
    launch "$h" "$tag" ${specs[$i]}
    ( t0=$(date +%s); wait_tag "$h" "$tag"; rc=$?
      log "$h: $tag ($(echo ${jobnames[$i]})) ended: exit $rc after $(( $(date +%s) - t0 ))s"; exit $rc ) &
    pids+=($!) tags+=("$h $tag")
done
incomplete=0
for i in "${!pids[@]}"; do
    wait "${pids[$i]}" || { incomplete=1; echo "${tags[$i]}: failed; see its log in ~/$RWORK/logs" >&2; }
done
log "built and tested in $(( $(date +%s) - t_start ))s"

worse=0
for n in "${names[@]}"; do
    h=$(hostfor "$n") out=$(out_of "$n") v=$(stock_field "$n" 2) c=commit_$n
    d=$LOCAL/fork-tests/$(basename "$out")
    if ! done_on "$h" "$out" "${!c}"; then
        echo "$n: no complete results on $h:$out" >&2; incomplete=1; continue
    fi
    fetch "$h" "$out" "$d"
    echo
    echo "======== $n: fork vs stock $v"
    python3 "$here/results.py" compare "$LOCAL/stock-baseline/$n-$v" "$d" | tee "$LOCAL/fork-tests/$n.report"
    [[ ${PIPESTATUS[0]} -eq 0 ]] || { worse=1; echo "==> $n: FAILS WHERE STOCK DOESN'T"; }
    awk -F'\t' '{ s += $3 } END { printf "(%s: %d steps, %ds of steps on %s)\n", n, NR, s, h }' \
        n="$n" h="$h" "$d/steps.tsv"
done
log "all done in $(( $(date +%s) - t_start ))s"
[[ $incomplete == 0 ]] || exit 2
exit $worse
