#!/usr/bin/env bash
# GateTest2's gate:holt layer (testvm/test2/gate/layers.json): holt's cargo
# test, the golden plans of the dump corpus and redact_dumps, all in RAM.
#
# Usage (on a KVM test host, as m, from a private copy, under
# ~/backotter-work/cmp-run.sh --reserve 2 4; t2lib/gate.py starts it so):
#   holt-test.sh --out DIR [--ram DIR] [--raw DIR] [--holt DIR]
#
#   --out DIR   the layer's results, on this host (HOST_ROOT/runs/RUN/gate/holt):
#               steps.tsv, info.txt, cargo-test.log, golden.log,
#               plans/ (diff.json and the differing GUEST.plan files),
#               redact-corpus.log, redact-raw.tsv; private/ (0700) keeps
#               what must stay here (redact_dumps' messages on the raw dumps)
#   --ram DIR   the reserved RAM directory (default /mnt/bko-ram/t2g-holt-PID):
#               CARGO_TARGET_DIR and TMPDIR.  Made here, removed at the end
#               however the run ends
#   --raw DIR   the raw dumps (HOST_ROOT/h1-raw, Chopin only): redact_dumps
#               runs over them too; only per-guest ok or FAIL leaves private/
#   --holt DIR  the holt tree (default: the copy's, ../../../holt from here)
#
# Steps, each with its exit and seconds in steps.tsv (STEP RC SECS NOTE):
#   cargo-test     cargo test --locked --offline -- --skip golden_  (every
#                  target; the golden plans' tests are the next step's)
#   golden-plans   BKO_HOLT_GOLDEN=report BKO_HOLT_PLANS_OUT=OUT/plans
#                  cargo test --test golden_plans -- --nocapture: differences
#                  go to plans/diff.json, only invariant violations fail
#   redact-raw     BKO_HOLT_DUMPS=RAW cargo test --test redact_dumps (--raw)
#   redact-corpus  BKO_HOLT_DUMPS=holt/tests/corpus cargo test --test redact_dumps
# A step whose test file, corpus or raw dumps aren't there is "skip" with
# the reason as its NOTE.  info.txt: rustc, cargo, the copy's COMMIT, the
# RAM directory's peak (ram_peak_kib, sampled every 10 s).
#
# Exit: 0 when every step ran to its end (steps.tsv says how each went);
# 2 when it refused (no cargo, no --out, no holt tree).
#
#   CARGO   the cargo to use (default ~/.cargo/bin/cargo, then PATH's)

set -u
here=$(cd "$(dirname "$0")" && pwd)
die() { echo "holt-test: $*" >&2; exit 2; }
log() { echo "$(date -u '+%H:%M:%S') holt-test: $*"; }

out="" ram="" raw="" holt=$here/../../../holt
while [[ $# -gt 0 ]]; do
    case $1 in
        --out) out=${2:-}; shift 2 ;;
        --ram) ram=${2:-}; shift 2 ;;
        --raw) raw=${2:-}; shift 2 ;;
        --holt) holt=${2:-}; shift 2 ;;
        *) die "usage: holt-test.sh --out DIR [--ram DIR] [--raw DIR] [--holt DIR]" ;;
    esac
done
[[ -n $out ]] || die "no --out DIR"
[[ -f $holt/Cargo.toml ]] || die "no holt tree at $holt"
holt=$(cd "$holt" && pwd)
[[ -z $raw || ! -d $raw ]] || raw=$(cd "$raw" && pwd)
cargo=${CARGO:-$HOME/.cargo/bin/cargo}
[[ -x $cargo ]] || cargo=$(command -v cargo) || die "no cargo (~/.cargo/bin/cargo, CARGO)"
rustc=$(dirname "$cargo")/rustc
[[ -x $rustc ]] || rustc=rustc
[[ -n $ram ]] || ram=/mnt/bko-ram/t2g-holt-$$
# One new directory right under /mnt/bko-ram (the EXIT trap removes it): no "..", no
# deeper path, no dot name such as .reserve.
[[ $ram =~ ^/mnt/bko-ram/[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "--ram must be one directory name under /mnt/bko-ram (RAM only): $ram"
mountpoint -q /mnt/bko-ram || die "no /mnt/bko-ram"
[[ ! -e $ram ]] || die "$ram is there already: another run's"

mkdir -p "$out" "$out/plans" "$out/private" || die "can't make $out"
chmod 700 "$out/private"
rm -f "$out/steps.tsv" "$out/info.txt"
mkdir -p "$ram/target" "$ram/tmp" "$ram/plans" || die "can't make $ram"
sampler=""
cleanup() {
    trap '' INT TERM HUP     # the stop's second signal must not cut this short
    local k p
    [[ -z $sampler ]] || kill "$sampler" 2>/dev/null
    wait 2>/dev/null
    # One sample more at the end: a run shorter than the interval has had only its first.
    k=$(du -sk "$ram" 2>/dev/null | cut -f1) p=$(cat "$ram/.peak" 2>/dev/null)
    [[ ${k:-0} -gt ${p:-0} ]] && p=$k
    echo "ram_peak_kib=${p:-0}" >>"$out/info.txt"
    rm -rf "$ram"
}
trap cleanup EXIT
trap 'trap "" INT TERM HUP; exit 130' INT TERM HUP
# The RAM directory's peak, every 10 s.
( peak=0; while :; do
      k=$(du -sk "$ram" 2>/dev/null | cut -f1)
      [[ ${k:-0} -gt $peak ]] && { peak=$k; echo "$peak" >"$ram/.peak.new" && mv -f "$ram/.peak.new" "$ram/.peak"; }
      sleep 10
  done ) &
sampler=$!

export CARGO_TARGET_DIR=$ram/target TMPDIR=$ram/tmp CARGO_TERM_COLOR=never CARGO_INCREMENTAL=0
export PYTHONDONTWRITEBYTECODE=1
unset RUSTFLAGS CARGO_ENCODED_RUSTFLAGS RUSTC_WRAPPER BKO_HOLT_DUMPS BKO_HOLT_DUMPS_ALL BKO_HOLT_GOLDEN BKO_HOLT_PLANS_OUT
{
    echo "holt=$holt"
    echo "commit=$(head -n 1 "$holt/../COMMIT" 2>/dev/null)"
    echo "rustc=$("$rustc" -V 2>/dev/null)"
    echo "cargo=$("$cargo" -V 2>/dev/null)"
    echo "host=$(hostname -s)"
    echo "ram=$ram"
} >"$out/info.txt"

# step NAME LOG CMD...: CMD in the holt tree, its output in LOG.
step() {
    local name=$1 lf=$2 t0 rc; shift 2
    log "start $name"
    t0=$(date +%s)
    (cd "$holt" && "$@") >"$lf" 2>&1 </dev/null
    rc=$?
    printf '%s\t%s\t%s\t%s\n' "$name" "$rc" "$(( $(date +%s) - t0 ))" "" >>"$out/steps.tsv"
    log "end $name: exit $rc, $(( $(date +%s) - t0 ))s"
    return 0
}
skip() {  # NAME WHY
    printf '%s\tskip\t0\t%s\n' "$1" "$2" >>"$out/steps.tsv"
    log "skip $1: $2"
}
has_dumps() { [[ -d $1 ]] && [[ -n $(find "$1" -name src.json -print -quit 2>/dev/null) ]]; }

C=("$cargo" test --locked --offline)
step cargo-test "$out/cargo-test.log" "${C[@]}" -- --skip golden_
if [[ -f $holt/tests/golden_plans.rs ]]; then
    step golden-plans "$out/golden.log" env BKO_HOLT_GOLDEN=report BKO_HOLT_PLANS_OUT="$ram/plans" \
        "${C[@]}" --test golden_plans -- --nocapture
    cp -p "$ram/plans/"* "$out/plans/" 2>/dev/null
else
    skip golden-plans "no holt/tests/golden_plans.rs in this tree (H1's)"
fi
if [[ -z $raw ]]; then
    skip redact-raw "no raw dumps given (--raw)"
elif ! has_dumps "$raw"; then
    skip redact-raw "no src.json under $raw"
else
    # Its messages can name a raw dump's identifiers: they stay in private/.
    step redact-raw "$out/private/redact-raw.log" env BKO_HOLT_DUMPS="$raw" \
        "${C[@]}" --test redact_dumps -- --nocapture
    chmod 600 "$out/private/redact-raw.log"
    awk -v raw="$raw/" '
        /^(ok|FAIL)  +/ { p = $2; sub(":$", "", p); sub("^" raw, "", p); sub("/src\\.json$", "", p)
                          print p "\t" $1 }' "$out/private/redact-raw.log" >"$out/redact-raw.tsv"
fi
if has_dumps "$holt/tests/corpus"; then
    step redact-corpus "$out/redact-corpus.log" env BKO_HOLT_DUMPS="$holt/tests/corpus" \
        "${C[@]}" --test redact_dumps -- --nocapture
else
    skip redact-corpus "no holt/tests/corpus/*/src.json in this tree (H1's)"
fi
log "done; results in $out"
exit 0
