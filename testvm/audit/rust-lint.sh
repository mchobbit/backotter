#!/usr/bin/env bash
# RustAudit1's mechanical checks as one command (docs/design/rust-audit.md,
# RA2, kept by RA9): rustfmt, clippy, rustdoc and the compiler over holt
# (bko-holt), rustic_core and rustic, per target, on a farm host, their
# output filtered to the lines that are ours (rust-scope.sh, rust-filter.py).
#
# Usage, on the Mac (no Rust needed here):
#   rust-lint.sh [--host H | --hosts H,H] [--spread N] [--targets LIST]
#                [--tests] [--baseline] [--compare OLDRUN] [NAME[=REV] ...]
#
# NAME is holt, rustic_core or rustic (default: all three); REV any commit
# of this checkout (holt, default main) or of BKO_GIT/NAME (~/git/NAME,
# default the backotter branch).  rustic is built against the rustic_core
# REV of the same run (stick/prep-rustic-src.sh, as fork-tests.sh does).
#
#   --host H       this host alone (a name of testvm/hosts.json, NAME.local
#                  or USER@HOST): every step there, as before F10
#   --hosts H,H    the candidates, instead of the table's amd64 build-role
#                  hosts (the default)
#   --spread N     up to N hosts share the targets (default 3; 1: one host)
#
#                  Hosts (F10 of docs/design/img-speed.md: Chopin was the
#                  default by name).  Each candidate is probed over ssh
#                  (audit-hosts.py probe: rustup's toolchains, targets and
#                  components, flock, time, cc) and pick-host ranks them by
#                  room for RESERVE; audit-hosts.py lint-plan deals the
#                  targets: the best host that has rustup 1.94.1 with clippy
#                  and rustfmt and 1.85.1 is the primary (fmt, doc, holt's
#                  parser pass and floor check, --tests, rustic, and its
#                  share of the targets), and up to N-1 more run clippy over
#                  the rest of the targets, each target only on a host with
#                  its std (1.94.1's rust-std; openbsd, dragonfly and haiku
#                  nightly-2026-10-31 with clippy and rust-src).  A host
#                  left out says why, with the rustup command that would add
#                  what it lacks.  The hosts' outputs merge into one raw/
#                  (info.txt's host is "A+B"; each other host's own files in
#                  raw-hosts/HOST/), so the findings are the same however
#                  they were spread
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
# ~/backotter-work/bko-audit/bin/; one detached job a host, rust-lint-host.sh under
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
#   probe/HOST.txt, plan.txt   each candidate's probe, and the deal
#   raw/                   the primary host's output (rust-lint-host.sh's OUT),
#                          the other hosts' per-target files merged in
#   raw-hosts/HOST/        each other host's own output
#   filtered/NAME.TARGET.TOOL.txt, .json   rust-filter.py's
#   summary.txt            per crate, target and tool: counts on our lines,
#                          top lints and files; steps' times and memory
#
# Exit: 0; 1 with --compare and a new in-scope diagnostic; 2 if the run
# didn't complete (a crate didn't compile, the job died, a step failed).
#
# Needs: git, rsync, ssh, python3; the forks' checkouts in BKO_GIT;
# testvm/pick-host and testvm/hosts.sh (the host table).
#
# Examples:
#   rust-lint.sh --tests --baseline
#   rust-lint.sh --host copland holt=c4917fc8
#   rust-lint.sh --targets linux holt
#   rust-lint.sh --compare 20261120T201500

set -euo pipefail
RESERVE=${RESERVE:-2 8}
source "$(dirname "$0")/audit-lib.sh"
source "$(dirname "$0")/audit-pick.sh"

hostsarg= spread=3 targets=all tests=0 baseline=0 compare=
while [[ $# -gt 0 ]]; do
    case $1 in
        --host) hostsarg=${2:?}; spread=1; shift 2 ;;
        --hosts) hostsarg=${2:?}; shift 2 ;;
        --spread) spread=${2:?}; shift 2 ;;
        --targets) targets=${2:?}; shift 2 ;;
        --tests) tests=1; shift ;;
        --baseline) baseline=1; shift ;;
        --compare) compare=${2:?}; shift 2 ;;
        -h | --help) sed -n '2,/^$/s/^# \{0,1\}//p' "$0"; exit 0 ;;
        -*) die "usage: rust-lint.sh [--host H | --hosts H,H] [--spread N] [--targets LIST] [--tests] [--baseline] [--compare OLDRUN] [NAME[=REV] ...]" ;;
        *) break ;;
    esac
done
[[ $spread =~ ^[1-9][0-9]*$ ]] || die "--spread: a count, 1 or more, not $spread"
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

# The hosts: the candidates probed, ranked by pick-host, the targets dealt
# (audit-hosts.py lint-plan).
if [[ -n $hostsarg ]]; then
    cands=${hostsarg//,/ }
else
    cands=$(hosts_names --role build --arch amd64 | tr '\n' ' ') || die "no build hosts in the host table"
fi
# shellcheck disable=SC2086
probe_hosts "$L/probe" $cands
# shellcheck disable=SC2086
ranked=$(rank_hosts $cands) || die "pick-host found no host among $cands"
plan=$(python3 -I -B "$here/audit-hosts.py" lint-plan --probe-dir "$L/probe" --hosts "$(echo $ranked | tr ' ' ',')" \
    --targets "$triples" --spread "$spread" $([[ $tests == 0 ]] || echo --tests)) ||
    die "no hosts for this run (audit-hosts.py's reasons above; the probes in $L/probe)"
echo "$plan" >"$L/plan.txt"
PH=() PR=() PT=() PD=()
while read -r a b c; do PH+=("$a") PR+=("$b") PT+=("$c") PD+=("$(dest_of "$a")"); done <<<"$plan"
h=${PD[0]}
log "run $RUN: ${names[*]}, targets $triples$([[ $tests == 0 ]] || echo ', with tests'); here $L"
for i in "${!PH[@]}"; do log "  ${PH[$i]} (${PD[$i]}): ${PR[$i]}, targets ${PT[$i]}"; done

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

# The tools and the trees, on every host of the plan (rustic on the primary
# alone: it is linted on linux only).
OUTR=$RWORK/rustlint/$RUN
LG=$RWORK/logs/$tag
for i in "${!PD[@]}"; do
    hh=${PD[$i]}
    "${SSH[@]}" "$hh" "mkdir -p $RBIN $RWORK/logs $RWORK/rustlint" &&
        rsync -p --checksum "$here/rust-lint-host.sh" "$repo/stick/prep-rustic-src.sh" "$here/rust/holt-lints.txt" \
            "$hh:$RBIN/" || die "can't copy the tools to $hh"
    specs=()
    for n in "${names[@]}"; do
        c=commit_$n
        d=$RSRC/$n-$(short "${!c}")
        case $n in
            holt)
                # holt/ alone, at the tree's root (cargo's workspace root).
                if ! "${SSH[@]}" "$hh" "test -f $d/.bko-src"; then
                    log "$hh: $d from $repo holt/ ${!c}"
                    git -C "$repo" archive --format=tar "${!c}:holt" |
                        "${SSH[@]}" "$hh" "set -e; rm -rf $d.part; mkdir -p $d.part; tar -x -C $d.part; echo ${!c} >$d.part/.bko-src; mv $d.part $d" ||
                        die "can't copy holt ${!c} to $hh:$d"
                fi
                specs+=("holt:$d") ;;
            rustic_core)
                ship_src "$hh" rustic_core "$commit_rustic_core"
                specs+=("rustic_core:$d") ;;
            rustic)
                [[ ${PR[$i]} == primary ]] || continue
                ship_src "$hh" rustic "$commit_rustic"
                ship_src "$hh" rustic_core "$commit_rustic_core"
                specs+=("rustic:$d:$RSRC/rustic_core-$(short "$commit_rustic_core")") ;;
        esac
    done
    # One detached job a host under the shared ledger (as audit-lib.sh's launch).
    args="--targets ${PT[$i]}"
    [[ ${PR[$i]} == primary ]] || args+=" --clippy-only"
    [[ $tests == 1 && ${PR[$i]} == primary ]] && args+=" --tests"
    cmd="rm -f $LG.exit; setsid -f bash -c 'echo \$\$ >\$HOME/$LG.pid; exec </dev/null;"
    cmd+=" WEAR_TAG=${BKO_WEAR_TAG:-audit:$tag} $CMP_RUN --reserve $RESERVE_DISK $RESERVE_MEM nice -n 10"
    cmd+=" \$HOME/$RBIN/rust-lint-host.sh $args \$HOME/$OUTR ${specs[*]}"
    cmd+=" >\$HOME/$LG.log 2>&1; echo \$? >\$HOME/$LG.exit' </dev/null >/dev/null 2>&1"
    "${SSH[@]}" -n "$hh" "$cmd" || die "can't start $tag on $hh"
    log "$hh: started $tag (log $hh:~/$LG.log)"
done
t0=$(date +%s)
wpids=()
for i in "${!PD[@]}"; do
    ( rc=0; wait_tag "${PD[$i]}" "$tag" || rc=$?
      log "${PD[$i]}: $tag ended: exit $rc after $(( $(date +%s) - t0 ))s"; exit "$rc" ) &
    wpids+=($!)
done
incomplete=0
for i in "${!wpids[@]}"; do wait "${wpids[$i]}" || incomplete=1; done
# The primary's output is raw/; every other host's goes to raw-hosts/HOST/ and
# its per-target files, steps and nightly lint groups into raw/.
for i in "${!PD[@]}"; do
    hh=${PD[$i]}
    dst=$L/raw
    [[ $i == 0 ]] || dst=$L/raw-hosts/${PH[$i]}
    "${SSH[@]}" "$hh" "test -d $OUTR" || die "no $hh:$OUTR (see $hh:~/$LG.log)"
    fetch "$hh" "$OUTR" "$dst"
    rsync -q "$hh:$LG.log" "$dst/job.log" || true
done
for i in "${!PD[@]}"; do
    [[ $i == 0 ]] && continue
    src=$L/raw-hosts/${PH[$i]}
    for n in "${names[@]}"; do
        [[ -d $src/$n ]] || continue
        for f in "$src/$n"/*; do
            [[ -f $f ]] || continue
            [[ ! -e $L/raw/$n/${f##*/} ]] || die "${PH[$i]} and ${PH[0]} both wrote $n/${f##*/}"
            cp -p "$f" "$L/raw/$n/"
        done
    done
    cat "$src/steps.tsv" >>"$L/raw/steps.tsv" 2>/dev/null || true
    case ",${PT[$i]}," in *,x86_64-unknown-openbsd,* | *,x86_64-unknown-dragonfly,* | *,x86_64-unknown-haiku,*)
        cp -p "$src/lint-groups-nightly.txt" "$L/raw/" ;;
    esac
    awk -v h="${PH[$i]}" -F= '$1 == "targets" || $1 == "seconds" { print h "_" $0 }' "$src/info.txt" >>"$L/raw/info.txt"
done
if [[ ${#PD[@]} -gt 1 ]]; then
    hs=$(IFS=+; echo "${PH[*]}")
    awk -v hs="$hs" 'BEGIN { FS = OFS = "=" } $1 == "host" { $0 = "host=" hs } { print }' "$L/raw/info.txt" >"$L/raw/info.txt.new" &&
        mv "$L/raw/info.txt.new" "$L/raw/info.txt"
fi

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
