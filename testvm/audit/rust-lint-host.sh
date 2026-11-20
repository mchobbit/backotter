#!/usr/bin/env bash
# The host part of rust-lint.sh (RustAudit1, RA2 and RA9): run rustfmt,
# clippy, rustdoc and the compiler over holt, rustic_core and rustic trees,
# per target, and leave their raw output in OUT for the Mac to filter to our
# lines (rust-filter.py).  rust-lint.sh copies it to
# HOST:~/backotter-work/bko-audit/bin/ with prep-rustic-src.sh and
# holt-lints.txt and runs it there, detached, under cmp-run.sh --reserve.
#
# Usage (on Chopin, the host with the toolchains):
#   rust-lint-host.sh [--targets LIST] [--tests] OUT NAME:ROOT[:CORE] ...
#
# NAME is holt, rustic_core or rustic; ROOT its tree (a git archive under
# /bkoscratch/bko-audit/src); CORE, for rustic, the rustic_core tree it is
# built against (prep-rustic-src.sh, in ROOT+core-COMMIT, the same tree
# audit-host.sh makes).  LIST: comma-separated targets (rust-lint.sh's
# header names them): x86_64-unknown-linux-gnu (the host's, cargo's default
# target), x86_64-unknown-linux-musl, aarch64-unknown-linux-musl and
# x86_64-unknown-{freebsd,netbsd,illumos} with rustup's std (TOOLCHAIN);
# x86_64-unknown-{openbsd,dragonfly,haiku}, which have no rustup std, with
# the pinned NIGHTLY and -Zbuild-std (a check build: std is checked from
# rust-src, nothing is linked).  The build scripts' C (zstd-sys) for a cross
# target is compiled with the host's cc and ar (CC_T, AR_T): a check build
# never links it.
#
# Per crate (each line one file in OUT/NAME/, TARGET.TOOL.json or .txt):
#   holt         fmt (cargo fmt --check); clippy --all-targets per target with
#                holt-lints.txt's [flags] and its [clippy.toml] (CLIPPY_CONF_DIR);
#                clippy-parser (linux): clippy --lib with [flags] and [parser];
#                doc (cargo doc --no-deps --document-private-items);
#                check-1.85.1 (cargo +1.85.1 check --all-targets, the floor);
#                with --tests, test-1.85.1 and test-TOOLCHAIN (cargo test)
#   rustic_core  fmt (--all); clippy --workspace --all-targets --all-features
#                (linux), clippy -p rustic_core --lib --all-features (other
#                targets), upstream's lints and FORK_FLAGS; doc --workspace
#                --all-features
#   rustic       fmt; clippy --locked --all-targets --features release, with
#                FORK_FLAGS (linux only); doc --features release
# JSON is cargo's --message-format=json (with the lints' names), .txt fmt's
# diff and the tests' output.  OUT also holds:
#   steps.tsv    NAME TARGET TOOL EXIT SECONDS MAXRSS_KB OK(yes/no), a row a step
#   info.txt     KEY=VALUE: host, date, commits, roots, toolchains
#   lint-groups.txt, lint-groups-nightly.txt   clippy-driver -W help
#   exit         0 when every step ran to its end (warnings, fmt diffs and
#                failing tests included); 1 when one didn't (a crate didn't
#                compile, a tool is missing)
#
# Caches, as audit-host.sh's: CARGO_HOME /bkoscratch/bko-audit/cache/cargo-home;
# CARGO_TARGET_DIR /bkoscratch/bko-audit/cache/target/lint-NAME, its own
# (never audit-host.sh's), locked for the crate's steps, its trees' files
# touched when it last built others.  The tests' TMPDIR is
# /mnt/bko-ram/bko-audit/rustlint-RUN (the reservation's disk).
#
#   TOOLCHAIN    rustup toolchain (default 1.94.1)
#   FLOOR        holt's floor toolchain (default 1.85.1)
#   NIGHTLY      for the build-std targets (default nightly-2026-10-31, as
#                stick/dragonfly/build-rustic.sh; needs its clippy and
#                rust-src components)
#   FORK_FLAGS   added to the forks' clippy (default -W
#                clippy::undocumented_unsafe_blocks -W
#                clippy::multiple_unsafe_ops_per_block)
#   JOBS         cargo -j and test threads (default 8)

set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
CACHE=/bkoscratch/bko-audit/cache
TOOLCHAIN=${TOOLCHAIN:-1.94.1}
FLOOR=${FLOOR:-1.85.1}
NIGHTLY=${NIGHTLY:-nightly-2026-10-31}
FORK_FLAGS=${FORK_FLAGS:--W clippy::undocumented_unsafe_blocks -W clippy::multiple_unsafe_ops_per_block}
JOBS=${JOBS:-8}
HOST_TARGET=x86_64-unknown-linux-gnu
BUILD_STD_TARGETS=" x86_64-unknown-openbsd x86_64-unknown-dragonfly x86_64-unknown-haiku "
export PATH=$HOME/.cargo/bin:$PATH LC_ALL=C.UTF-8 CARGO_TERM_COLOR=never
export CARGO_HOME=$CACHE/cargo-home CARGO_INCREMENTAL=0 CARGO_BUILD_JOBS=$JOBS RUST_TEST_THREADS=$JOBS
unset RUSTFLAGS CARGO_ENCODED_RUSTFLAGS RUSTC_WRAPPER RUST_BACKTRACE RUST_LIB_BACKTRACE RUSTUP_TOOLCHAIN \
    RUSTDOCFLAGS CLIPPY_CONF_DIR

die() { echo "rust-lint-host: $*" >&2; exit 1; }
log() { echo "$(date '+%F %T') $*"; }

targets=$HOST_TARGET tests=0
while [[ $# -gt 0 ]]; do
    case $1 in
        --targets) targets=${2:?}; shift 2 ;;
        --tests) tests=1; shift ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
[[ $# -ge 2 ]] || die "usage: rust-lint-host.sh [--targets LIST] [--tests] OUT NAME:ROOT[:CORE] ..."
OUT=$1; shift
targets=${targets//,/ }
mkdir -p "$OUT" || die "can't make $OUT"
exec 9>"$OUT/.lock"
flock -n 9 || die "$OUT is being written by another run"
rm -f "$OUT/exit" "$OUT/steps.tsv"
RAMTMP=/mnt/bko-ram/bko-audit/rustlint-$(basename "$OUT")
rm -rf "$RAMTMP" && mkdir -p "$RAMTMP" || die "can't make $RAMTMP"
trap 'rm -rf "$RAMTMP"' EXIT
{
    echo "host=$(hostname -s)"
    echo "date=$(date '+%F %T %z')"
    echo "targets=$targets"
    echo "toolchain=$TOOLCHAIN"
    for tc in "$TOOLCHAIN" "$FLOOR" "$NIGHTLY"; do
        echo "rustc_$tc=$(rustc +"$tc" -V 2>&1)"
        echo "clippy_$tc=$(cargo +"$tc" clippy -V 2>/dev/null || echo "no clippy")"
        echo "rustfmt_$tc=$(rustfmt +"$tc" -V 2>&1 | head -1)"
    done
    echo "fork_flags=$FORK_FLAGS"
} >"$OUT/info.txt"
rustup run "$TOOLCHAIN" clippy-driver -W help >"$OUT/lint-groups.txt" 2>&1 || die "no clippy in $TOOLCHAIN"
rustup run "$NIGHTLY" clippy-driver -W help >"$OUT/lint-groups-nightly.txt" 2>&1 || true

fail=0
# step NAME TARGET TOOL OUTFILE OKCODES CMD...: run CMD in the current
# directory, stdout to OUTFILE (stderr too, for .txt; beside it, .err, for
# JSON), and a row in steps.tsv.  OKCODES: the exits that mean "ran to its
# end" (fmt: 0 1); for a test step, a log with a "test result:" line and no
# "could not compile" also counts.
step() {
    local name=$1 target=$2 tool=$3 of=$4 okc=$5 t0 rc ok=no rss; shift 5
    log "start $name $target $tool"
    t0=$(date +%s)
    if [[ $of == *.json ]]; then
        /usr/bin/time -f %M -o "$of.rss" "$@" >"$of" 2>"${of%.json}.err"
    else
        /usr/bin/time -f %M -o "$of.rss" "$@" >"$of" 2>&1
    fi
    rc=$?
    rss=$(tail -1 "$of.rss" 2>/dev/null); rm -f "$of.rss"
    [[ " $okc " == *" $rc "* ]] && ok=yes
    if [[ $tool == test-* && $ok == no ]] && grep -q '^test result:' "$of" && ! grep -q 'could not compile' "$of"; then
        ok=yes
    fi
    [[ $ok == yes ]] || fail=1
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$target" "$tool" "$rc" "$(( $(date +%s) - t0 ))" "${rss:-?}" "$ok" >>"$OUT/steps.tsv"
    log "end $name $target $tool: exit $rc, ok $ok, $(( $(date +%s) - t0 ))s, ${rss:-?} kB"
}

# own_target TREE...: as audit-host.sh's: lock CARGO_TARGET_DIR for this
# crate's steps, and touch TREE's files when another tree was built there.
own_target() {
    exec 8>"$CARGO_TARGET_DIR/.bko-audit.lock"
    flock 8 || die "can't lock $CARGO_TARGET_DIR"
    local mark=$CARGO_TARGET_DIR/.bko-audit.trees
    if [[ "$(cat "$mark" 2>/dev/null)" != "$*" ]]; then
        log "$CARGO_TARGET_DIR last built other trees: $* get the time now"
        rm -f "$mark"
        find "$@" -type f -exec touch -c {} + || die "can't touch $*"
        echo "$*" >"$mark"
    fi
}

# Section SECT of holt-lints.txt: its lines without comments.
lints_section() {
    awk -v s="[$1]" '/^\[/ { on = ($0 == s); next } on && !/^#/ && NF { print }' "$here/holt-lints.txt"
}

cross_env() {  # TARGET: export CC_T/AR_T for a cross target
    local t=${1//-/_}
    export "CC_$t=cc" "AR_$t=ar"
}
clippy_cmd() {  # TARGET -> sets CMD=(cargo +TC clippy [--target T] [-Zbuild-std])
    local t=$1
    if [[ $BUILD_STD_TARGETS == *" $t "* ]]; then
        CMD=(cargo +"$NIGHTLY" clippy -Zbuild-std=std,panic_abort --target "$t")
    elif [[ $t == "$HOST_TARGET" ]]; then
        CMD=(cargo +"$TOOLCHAIN" clippy)
    else
        CMD=(cargo +"$TOOLCHAIN" clippy --target "$t")
    fi
    [[ $t == "$HOST_TARGET" ]] || cross_env "$t"
}

run_holt() {
    local t flags=() pflags=()
    mapfile -t flags < <(lints_section flags | tr ' ' '\n' | grep .)
    mapfile -t pflags < <(lints_section parser | tr ' ' '\n' | grep .)
    mkdir -p "$OUT/conf/holt"
    lints_section clippy.toml >"$OUT/conf/holt/clippy.toml"
    export CLIPPY_CONF_DIR=$OUT/conf/holt
    step holt any fmt "$D/any.fmt.txt" "0 1" cargo +"$TOOLCHAIN" fmt --check -- --color=never
    for t in $targets; do
        clippy_cmd "$t"
        step holt "$t" clippy "$D/$t.clippy.json" 0 "${CMD[@]}" --all-targets --message-format=json -- "${flags[@]}"
    done
    step holt "$HOST_TARGET" clippy-parser "$D/$HOST_TARGET.clippy-parser.json" 0 \
        cargo +"$TOOLCHAIN" clippy --lib --message-format=json -- "${flags[@]}" "${pflags[@]}"
    unset CLIPPY_CONF_DIR
    step holt "$HOST_TARGET" doc "$D/$HOST_TARGET.doc.json" 0 \
        cargo +"$TOOLCHAIN" doc --no-deps --document-private-items --message-format=json
    step holt "$HOST_TARGET" "check-$FLOOR" "$D/$HOST_TARGET.check-$FLOOR.json" 0 \
        cargo +"$FLOOR" check --all-targets --message-format=json
    if [[ $tests == 1 ]]; then
        local tc
        for tc in "$FLOOR" "$TOOLCHAIN"; do
            step holt "$HOST_TARGET" "test-$tc" "$D/$HOST_TARGET.test-$tc.txt" 0 \
                env TMPDIR="$RAMTMP" cargo +"$tc" test --no-fail-fast
        done
    fi
}

run_rustic_core() {
    local t
    # shellcheck disable=SC2206
    local ff=($FORK_FLAGS)
    step rustic_core any fmt "$D/any.fmt.txt" "0 1" cargo +"$TOOLCHAIN" fmt --all --check -- --color=never
    for t in $targets; do
        [[ $t == *-linux-musl ]] && continue   # holt's targets only
        clippy_cmd "$t"
        if [[ $t == "$HOST_TARGET" ]]; then
            step rustic_core "$t" clippy "$D/$t.clippy.json" 0 "${CMD[@]}" --workspace --all-targets --all-features \
                --message-format=json -- "${ff[@]}"
        else
            step rustic_core "$t" clippy "$D/$t.clippy.json" 0 "${CMD[@]}" -p rustic_core --lib --all-features \
                --message-format=json -- "${ff[@]}"
        fi
    done
    step rustic_core "$HOST_TARGET" doc "$D/$HOST_TARGET.doc.json" 0 \
        cargo +"$TOOLCHAIN" doc --workspace --no-deps --document-private-items --all-features --message-format=json
}

run_rustic() {
    # shellcheck disable=SC2206
    local ff=($FORK_FLAGS)
    step rustic any fmt "$D/any.fmt.txt" "0 1" cargo +"$TOOLCHAIN" fmt --check -- --color=never
    step rustic "$HOST_TARGET" clippy "$D/$HOST_TARGET.clippy.json" 0 cargo +"$TOOLCHAIN" clippy --locked \
        --all-targets --features release --message-format=json -- "${ff[@]}"
    step rustic "$HOST_TARGET" doc "$D/$HOST_TARGET.doc.json" 0 cargo +"$TOOLCHAIN" doc --locked --no-deps \
        --document-private-items --features release --message-format=json
}

for spec in "$@"; do
    IFS=: read -r NAME ROOT CORE <<<"$spec"
    [[ -n $NAME && -d $ROOT ]] || die "bad job $spec (NAME:ROOT[:CORE], ROOT a directory)"
    D=$OUT/$NAME
    rm -rf "$D" && mkdir -p "$D"
    echo "commit_$NAME=$(cat "$ROOT/.bko-src" 2>/dev/null)" >>"$OUT/info.txt"
    export CARGO_TARGET_DIR=$CACHE/target/lint-$NAME
    mkdir -p "$CARGO_TARGET_DIR"
    log "crate $NAME in $ROOT${CORE:+ with $CORE}"
    (
        case $NAME in
            holt)
                cd "$ROOT" || exit 1
                own_target "$ROOT"
                echo "root_holt=$ROOT" >>"$OUT/info.txt"
                run_holt ;;
            rustic_core)
                cd "$ROOT" || exit 1
                own_target "$ROOT"
                echo "root_rustic_core=$ROOT" >>"$OUT/info.txt"
                run_rustic_core ;;
            rustic)
                [[ -d $CORE ]] || die "rustic needs a CORE tree"
                work=$ROOT+core-$(basename "$CORE" | sed 's/^rustic_core-//')
                if [[ ! -f $work/.bko-prepared ]]; then
                    rm -rf "$work" && mkdir -p "$work" && cp -a "$ROOT" "$work/rustic" &&
                        ln -s "$CORE" "$work/rustic_core" || die "can't make $work"
                    sh "$here/prep-rustic-src.sh" "$work" >"$D/prep.log" 2>&1 ||
                        { cat "$D/prep.log"; die "prep-rustic-src.sh failed"; }
                    touch "$work/.bko-prepared"
                fi
                cd "$work/rustic" || exit 1
                own_target "$work/rustic" "$CORE"
                { echo "root_rustic=$work/rustic"; echo "rustic_core_tree=$CORE"; } >>"$OUT/info.txt"
                run_rustic ;;
            *) die "unknown NAME $NAME" ;;
        esac
        exit $fail
    ) || fail=1
done
echo "seconds=$SECONDS" >>"$OUT/info.txt"
echo $fail >"$OUT/exit"
log "done: exit $fail"
exit $fail
