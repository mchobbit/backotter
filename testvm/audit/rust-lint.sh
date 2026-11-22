#!/usr/bin/env bash
# RustAudit1's mechanical checks as one command (docs/design/rust-audit.md,
# RA2, kept by RA9): rustfmt, clippy, rustdoc and the compiler over holt
# (bko-holt), rustic_core and rustic, per target, on a farm host, their
# output filtered to the lines that are ours (rust-scope.sh, rust-filter.py).
#
# Usage, on the Mac (no Rust needed here):
#   rust-lint.sh [--host H] [--targets LIST] [--tests] [--baseline]
#                [--compare OLDRUN] [NAME[=REV] ...]
#
# NAME is holt, rustic_core or rustic (default: all three); REV any commit
# of this checkout (holt, default main) or of BKO_GIT/NAME (~/git/NAME,
# default the backotter branch).  rustic is built against the rustic_core
# REV of the same run (stick/prep-rustic-src.sh, as fork-tests.sh does).
#
#   --host H       the host (default chopin, the one with the toolchains:
#                  rustup 1.94.1 with clippy and rustfmt and the targets
#                  below, 1.85.1, nightly-2026-10-31 with clippy and rust-src)
#   --targets LIST comma-separated: linux (x86_64-unknown-linux-gnu), musl,
#                  aarch64-musl, freebsd, netbsd, illumos (rustup's std),
#                  openbsd, dragonfly, haiku (no rustup std: the nightly's
#                  clippy with -Zbuild-std, a check build), or full target
#                  triples; default all of them.  holt is linted for each;
#                  rustic_core for all but the two musl ones (its lib only,
#                  off Linux); rustic for linux only.  fmt, doc, holt's
#                  parser pass and its 1.85.1 check run once, on linux
#   --tests        also holt's cargo test, on 1.85.1 and 1.94.1 (the forks'
#                  tests are fork-tests.sh's)
#   --baseline     write testvm/audit/rust/baseline.txt from this run (commit
#                  it, with the scope files if they changed)
#   --compare OLDRUN  exit 1 if this run has an in-scope diagnostic OLDRUN
#                  (a run id under BKO_AUDIT_LOCAL/rust, or its directory)
#                  hasn't: a key per file, lint and message (numbers
#                  blanked) whose count grew
#
# What it runs per crate is rust-lint-host.sh's header; holt's lint set is
# testvm/audit/rust/holt-lints.txt (RA3's, until RA6 commits it to holt).
#
# How: the trees go to HOST:/bkoscratch/bko-audit/src/NAME-COMMIT (git
# archive, as fork-tests.sh's; holt's is holt/ alone), the tools to
# ~/backotter-work/bko-audit/bin/; one detached job, rust-lint-host.sh under
# cmp-run.sh --reserve (RESERVE, default "2 8": 2 GiB of RAM disk for holt's
# tests' TMPDIR, 8 GiB of memory; measured, a step peaks at 1.7 GiB)
# at nice -n 10, WEAR_TAG audit:rustlint-RUN, its log in
# ~/backotter-work/bko-audit/logs/rustlint-RUN.log; results in
# ~/backotter-work/bko-audit/rustlint/RUN/.  RUN is the start time,
# YYYYMMDDTHHMMSS.
#
# Writes, on the Mac, BKO_AUDIT_LOCAL/rust/RUN/ (default
# ${TMPDIR:-/tmp}/bko-audit/rust/RUN):
#   scope/scope-NAME.txt   the committed testvm/audit/rust/scope-NAME.txt when
#                          it is of the same commit, else made (rust-scope.sh)
#   raw/                   the host's output (rust-lint-host.sh's OUT)
#   filtered/NAME.TARGET.TOOL.txt, .json   rust-filter.py's
#   summary.txt            per crate, target and tool: counts on our lines,
#                          top lints and files; steps' times and memory
#
# Exit: 0; 1 with --compare and a new in-scope diagnostic; 2 if the run
# didn't complete (a crate didn't compile, the job died, a step failed).
#
# Needs: git, rsync, ssh, python3; the forks' checkouts in BKO_GIT.
#
# Examples:
#   rust-lint.sh --tests --baseline
#   rust-lint.sh --targets linux holt
#   rust-lint.sh --compare 20261120T201500

set -euo pipefail
RESERVE=${RESERVE:-2 8}
source "$(dirname "$0")/audit-lib.sh"

host=chopin targets=all tests=0 baseline=0 compare=
while [[ $# -gt 0 ]]; do
    case $1 in
        --host) host=${2:?}; shift 2 ;;
        --targets) targets=${2:?}; shift 2 ;;
        --tests) tests=1; shift ;;
        --baseline) baseline=1; shift ;;
        --compare) compare=${2:?}; shift 2 ;;
        -h | --help) sed -n '2,/^$/s/^# \{0,1\}//p' "$0"; exit 0 ;;
        -*) die "usage: rust-lint.sh [--host H] [--targets LIST] [--tests] [--baseline] [--compare OLDRUN] [NAME[=REV] ...]" ;;
        *) break ;;
    esac
done
h=$(host_of "$host")
ALL="linux musl aarch64-musl freebsd netbsd illumos openbsd dragonfly haiku"
[[ $targets == all ]] && targets=${ALL// /,}
triples=
for t in ${targets//,/ }; do
    case $t in
        linux) t=x86_64-unknown-linux-gnu ;;
        musl) t=x86_64-unknown-linux-musl ;;
        aarch64-musl | arm64-musl) t=aarch64-unknown-linux-musl ;;
        freebsd | netbsd | illumos | openbsd | dragonfly | haiku) t=x86_64-unknown-$t ;;
        *-*-*) ;;
        *) die "unknown target $t (one of: $ALL, or a triple)" ;;
    esac
    triples+="${triples:+,}$t"
done
if [[ -n $compare ]]; then
    [[ -d $compare ]] || compare=$LOCAL/rust/$compare
    [[ -d $compare/filtered ]] || die "no run $compare (no filtered/ there)"
fi

names=() rev_holt=main rev_rustic_core=backotter rev_rustic=backotter
for a in "$@"; do
    n=${a%%=*}
    case $n in holt | rustic_core | rustic) ;; *) die "unknown NAME $n (holt, rustic_core, rustic)" ;; esac
    names+=("$n")
    [[ $a == *=* ]] && eval "rev_$n=\${a#*=}"
done
[[ ${#names[@]} -gt 0 ]] || names=(holt rustic_core rustic)
commit_holt=$(git -C "$repo" rev-parse --verify "$rev_holt^{commit}") || die "no holt $rev_holt"
commit_rustic_core=$(git -C "$BKO_GIT/rustic_core" rev-parse --verify "$rev_rustic_core^{commit}") || die "no rustic_core $rev_rustic_core"
commit_rustic=$(git -C "$BKO_GIT/rustic" rev-parse --verify "$rev_rustic^{commit}") || die "no rustic $rev_rustic"

RUN=$(date +%Y%m%dT%H%M%S)
tag=rustlint-$RUN
L=$LOCAL/rust/$RUN
mkdir -p "$L/scope" "$L/filtered"
log "run $RUN: ${names[*]} on $h, targets $triples$([[ $tests == 0 ]] || echo ', with tests'); here $L"

# The scopes, for exactly the commits linted.
for n in "${names[@]}"; do
    c=commit_$n
    f=$here/rust/scope-$n.txt
    if [[ -f $f ]] && grep -q "^# name=$n commit=${!c} " "$f"; then
        cp "$f" "$L/scope/"
    else
        log "scope of $n ${!c:0:12}: made for this run (the committed one is of another commit)"
        "$here/rust-scope.sh" --out "$L/scope" "$n=${!c}" >/dev/null
    fi
done

# The tools and the trees.
"${SSH[@]}" "$h" "mkdir -p $RBIN $RWORK/logs $RWORK/rustlint" &&
    rsync -p --checksum "$here/rust-lint-host.sh" "$repo/stick/prep-rustic-src.sh" "$here/rust/holt-lints.txt" \
        "$h:$RBIN/" || die "can't copy the tools to $h"
specs=()
for n in "${names[@]}"; do
    c=commit_$n
    d=$RSRC/$n-$(short "${!c}")
    case $n in
        holt)
            # holt/ alone, at the tree's root (cargo's workspace root).
            if ! "${SSH[@]}" "$h" "test -f $d/.bko-src"; then
                log "$h: $d from $repo holt/ ${!c}"
                git -C "$repo" archive --format=tar "${!c}:holt" |
                    "${SSH[@]}" "$h" "set -e; rm -rf $d.part; mkdir -p $d.part; tar -x -C $d.part; echo ${!c} >$d.part/.bko-src; mv $d.part $d" ||
                    die "can't copy holt ${!c} to $h:$d"
            fi
            specs+=("holt:$d") ;;
        rustic_core)
            ship_src "$h" rustic_core "$commit_rustic_core"
            specs+=("rustic_core:$d") ;;
        rustic)
            ship_src "$h" rustic "$commit_rustic"
            ship_src "$h" rustic_core "$commit_rustic_core"
            specs+=("rustic:$d:$RSRC/rustic_core-$(short "$commit_rustic_core")") ;;
    esac
done

# One detached job under the shared ledger (as audit-lib.sh's launch).
OUTR=$RWORK/rustlint/$RUN
LG=$RWORK/logs/$tag
args="--targets $triples"
[[ $tests == 1 ]] && args+=" --tests"
cmd="rm -f $LG.exit; setsid -f bash -c 'echo \$\$ >\$HOME/$LG.pid; exec </dev/null;"
cmd+=" WEAR_TAG=${BKO_WEAR_TAG:-audit:$tag} $CMP_RUN --reserve $RESERVE_DISK $RESERVE_MEM nice -n 10"
cmd+=" \$HOME/$RBIN/rust-lint-host.sh $args \$HOME/$OUTR ${specs[*]}"
cmd+=" >\$HOME/$LG.log 2>&1; echo \$? >\$HOME/$LG.exit' </dev/null >/dev/null 2>&1"
"${SSH[@]}" -n "$h" "$cmd" || die "can't start $tag on $h"
log "$h: started $tag (log $h:~/$LG.log)"
t0=$(date +%s)
rc=0; wait_tag "$h" "$tag" || rc=$?
log "$h: $tag ended: exit $rc after $(( $(date +%s) - t0 ))s"
incomplete=0
[[ $rc == 0 ]] || incomplete=1
"${SSH[@]}" "$h" "test -d $OUTR" || die "no $h:$OUTR (see $h:~/$LG.log)"
fetch "$h" "$OUTR" "$L/raw"
rsync -q "$h:$LG.log" "$L/raw/job.log" || true

# Filter each output to our lines.
nlist=$(awk '/^\[/ { on = ($0 == "[parser]"); next } on && /^-W/ { print $2 }' "$here/rust/holt-lints.txt" | paste -sd, -)
flist=$(awk '/^\[/ { on = ($0 == "[parser-files]"); next } on && !/^#/ && NF { print }' "$here/rust/holt-lints.txt" | paste -sd, -)
for n in "${names[@]}"; do
    root=$(sed -n "s/^root_$n=//p" "$L/raw/info.txt" | tail -1)
    [[ -n $root ]] || { echo "$n: no root in the host's info.txt: it didn't run" >&2; incomplete=1; continue; }
    for f in "$L/raw/$n"/*.json "$L/raw/$n"/*.fmt.txt "$L/raw/$n"/*.walkdir-fmt.txt; do
        [[ -f $f ]] || continue
        b=$(basename "$f"); t=${b%%.*}; tool=${b#*.}; tool=${tool%.json}; tool=${tool%.txt}
        groups=$L/raw/lint-groups.txt
        case $t in x86_64-unknown-openbsd | x86_64-unknown-dragonfly | x86_64-unknown-haiku) groups=$L/raw/lint-groups-nightly.txt ;; esac
        extra=()
        [[ $tool == clippy-parser ]] && extra=(--files "$flist" --lints "$nlist")
        python3 "$here/rust-filter.py" --scope "$L/scope/scope-$n.txt" --root "$root" --tool "$n $t $tool" \
            --groups "$groups" --json "$L/filtered/$n.$t.$tool.json" ${extra[@]+"${extra[@]}"} "$f" \
            >"$L/filtered/$n.$t.$tool.txt" || { echo "rust-filter.py failed on $f" >&2; incomplete=1; }
    done
done

python3 "$here/rust-filter.py" --summarize "$L" >"$L/summary.txt" || incomplete=1
cat "$L/summary.txt"
if [[ $baseline == 1 ]]; then
    cp "$L/summary.txt" "$here/rust/baseline.txt"
    for n in "${names[@]}"; do
        cmp -s "$L/scope/scope-$n.txt" "$here/rust/scope-$n.txt" 2>/dev/null ||
            cp "$L/scope/scope-$n.txt" "$here/rust/scope-$n.txt"
    done
    log "wrote $here/rust/baseline.txt (and the scopes): commit them"
fi
worse=0
if [[ -n $compare ]]; then
    echo
    python3 "$here/rust-filter.py" --compare "$compare/filtered" "$L/filtered" || worse=$?
    [[ $worse -le 1 ]] || incomplete=1
fi
log "done in $SECONDS s; results in $L"
[[ $incomplete == 0 ]] || { echo "rust-lint: the run didn't complete (see $L/raw/steps.tsv and job.log)" >&2; exit 2; }
exit $worse
