#!/usr/bin/env bash
# Build and test stock restic, rustic_core and rustic once (the versions the
# forks are based on, audit-lib.sh's STOCK) and keep the results on the
# hosts, so that fork-tests.sh compares the forks' tests with them without
# building stock again.  restic runs on one host, rustic_core then rustic on
# another, the two in parallel; each host's run waits for room in the
# shared ledger (cmp-run.sh --reserve) and runs at nice 10.
#
# Usage, on the Mac (no Go or Rust needed here):
#   stock-baseline.sh [--force] [--restic-host H] [--host H] [NAME ...]
#
# NAME is restic, rustic_core or rustic (default: all three).  A baseline
# already on one of the hosts (the table's amd64 build-role hosts, and --host
# and --restic-host; its exit file 0) is not run again unless --force; its
# results are still copied here.  --host H runs rustic_core and rustic on H,
# --restic-host H restic on H (H needs Go).  H is a name of testvm/hosts.json
# (chopin), NAME.local or USER@HOST.  Without them (F10 of
# docs/design/img-speed.md: Chopin and Cinqcent were the defaults by name),
# the build-role hosts are probed (audit-hosts.py probe: a Go, rustup's
# BKO_AUDIT_RUST toolchain, default 1.94.1) and ranked by pick-host for
# RESERVE (6 12 leaves the small hosts out: their ceiling is under 12); restic
# goes to the best host with a Go, the Rust pair to the best other host with
# the toolchain (the same host when no other has it).
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
source "$(dirname "$0")/audit-pick.sh"

force=0 rhost= chost=
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
builders=$(hosts_names --role build --arch amd64 | tr '\n' ' ') || die "no build hosts in the host table"
builders=${builders% }
# rank: the build hosts probed and ranked, once (picked).
picked=
rank() {
    [[ -z $picked ]] || return 0
    # shellcheck disable=SC2086
    probe_hosts "$LOCAL/stock-probe" $builders
    # shellcheck disable=SC2086
    picked=$(rank_hosts $builders) || die "pick-host found no host among $builders for --reserve $RESERVE_DISK $RESERVE_MEM"
}
# pick restic|rust: the best of picked that can run it; the Rust pair not on
# restic's host (rpick) while another host can run it.
pick() {
    local want=$1 c
    for c in $picked; do
        [[ $want == rust && $c == "$rpick" && -n $(pick_other) ]] && continue
        python3 -I -B "$here/audit-hosts.py" stock-need --probe "$LOCAL/stock-probe/$c.txt" \
            --toolchain "${BKO_AUDIT_RUST:-1.94.1}" "$([[ $want == restic ]] && echo restic || echo rustic_core)" && { echo "$c"; return 0; }
    done
    die "no host among $picked can run $want's stock baseline (the reasons above)"
}
# pick_other: a host other than rpick with the Rust toolchain (empty: none).
pick_other() {
    local c
    for c in $picked; do
        [[ $c != "$rpick" ]] || continue
        python3 -I -B "$here/audit-hosts.py" stock-need --probe "$LOCAL/stock-probe/$c.txt" \
            --toolchain "${BKO_AUDIT_RUST:-1.94.1}" rustic_core 2>/dev/null && { echo "$c"; return 0; }
    done
}
rpick=
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
    if [[ $force == 0 ]]; then
        have=
        for x in $rhost $chost $builders; do done_on "$(dest_of "$x")" "$out" "$c" && { have=$(dest_of "$x"); break; }; done
        if [[ -n $have ]]; then
            log "$n $v: baseline on $have already (--force runs it again)"
            fetch "$have" "$out" "$LOCAL/stock-baseline/$n-$v"
            continue
        fi
    fi
    if [[ $n == restic ]]; then
        [[ -n $rhost ]] || { rank; rhost=$(pick restic) || exit 1; log "restic: on $rhost (best of $picked with a Go)"; }
        rpick=$rhost h=$(dest_of "$rhost")
    else
        [[ -n $chost ]] || { rank; chost=$(pick rust) || exit 1; log "$n: on $chost (best of $picked with Rust ${BKO_AUDIT_RUST:-1.94.1})"; }
        h=$(dest_of "$chost")
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
        v=$(stock_field "$n" 2) c=$(stock_field "$n" 3)
        if done_on "${hosts[$i]}" "$out" "$c"; then
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
        local_ok "$d" "$c" || { echo; echo "$n $v $c: no baseline"; continue; }
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
