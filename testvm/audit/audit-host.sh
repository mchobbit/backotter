#!/usr/bin/env bash
# The host part of stock-baseline.sh and fork-tests.sh: build and test one or
# more source trees of restic, rustic_core or rustic, one after the other,
# and leave per-test results in each job's OUT directory.  The Mac scripts
# copy it to HOST:~/backotter-work/bko-audit/bin/ with results.py and
# stick/prep-rustic-src.sh, and run it there under cmp-run.sh --reserve.
#
# Usage (on a KVM test host):
#   audit-host.sh NAME:SRC:OUT[:CORE] ...
#
# NAME is restic, rustic_core or rustic; SRC its source tree (a git archive,
# /bkoscratch/bko-audit/src/NAME-COMMIT); OUT the results directory.  CORE,
# for rustic only, is a rustic_core tree: rustic is then built against it
# (prep-rustic-src.sh, as build-round.sh does) in SRC+core-COMMIT beside SRC,
# instead of the rustic_core its Cargo.lock names; without CORE, rustic
# builds --locked from its own Cargo.lock (stock rustic: rustic_core from
# crates.io).
#
# What each runs (the features of the projects' CI, .github/workflows/ci.yml;
# Rust in the dev profile without debug info, not ci.yml's -r with fat LTO):
#   restic       go build ./cmd/restic (OUT/bin/restic); go vet ./...;
#                go test -count=1 -json -p 8 ./...; and go build ./cmd/restic
#                with CGO_ENABLED=0 for each of CROSS (below)
#   rustic_core  cargo test --all-targets --all-features --workspace
#                --examples --no-fail-fast; the same with --doc
#   rustic       cargo test [--locked] --all-targets --features release
#                --workspace --no-fail-fast; the same with --doc;
#                target/debug/rustic to OUT/bin/rustic
# OUT then holds:
#   results.tsv  SUITE<TAB>TEST<TAB>pass|fail|skip, one line per test (Go
#                packages as TEST "(package)"), and the build rows: SUITE
#                "#build" (a build, a crate that didn't compile), "#cross"
#                (GOOS/GOARCH), "#vet" ("(go vet)" and one row per finding,
#                its line numbers left out), "#bin" (a test binary that ended
#                without its "test result" line: crashed)
#   details.json the output of each failed test or build, for the report
#   info.txt     KEY=VALUE: host, source commit, toolchains, rustic_core used
#   steps.tsv    LABEL<TAB>EXIT<TAB>SECONDS of every step
#   summary.txt  counts of pass/fail/skip
#   *.log, test.json, vet.txt, cross/   the raw output
#   exit         0 once the job ran to its end (test failures included):
#                the results are complete; anything else, they are not
#
# Caches stay between runs, on /bkoscratch (not the RAM disk):
#   /bkoscratch/bko-audit/cache/gocache, gomod     GOCACHE, GOMODCACHE
#   /bkoscratch/bko-audit/cache/cargo-home         CARGO_HOME (registry, git)
#   /bkoscratch/bko-audit/cache/target/NAME        CARGO_TARGET_DIR, one per
#       project, shared by stock and fork trees: the fork's build reuses the
#       dependencies the stock one built; a target over TARGET_MAX_GIB
#       (default 60) is emptied first
# Tests write their temporary files in /mnt/bko-ram/bko-audit/JOB (removed
# after the job): the reservation's DISK_GIB covers them.
#
#   BKO_AUDIT_RUST  the Rust toolchain (default 1.94.1): rustup's toolchain
#                   of that name, else the default one if it is that version
#   BKO_AUDIT_GO    the go binary (default ~/sdk/goVERSION/bin/go for the
#                   go.mod's toolchain line, else go from PATH)
#   JOBS            parallel jobs: go test -p, cargo -j, test threads (8)
#
# Needs: Go (restic), rustup (rustic_core, rustic), python3, flock.
#
# Examples:
#   audit-host.sh restic:/bkoscratch/bko-audit/src/restic-6aa3a516ce65:$HOME/backotter-work/bko-audit/stock/restic-0.19.1
#   audit-host.sh rustic:/bkoscratch/bko-audit/src/rustic-f989942cfd3e:$HOME/backotter-work/bko-audit/fork/rustic-f989942cfd3e+core-6e8b00983d33:/bkoscratch/bko-audit/src/rustic_core-6e8b00983d33

set -u

here=$(cd "$(dirname "$0")" && pwd)
CACHE=/bkoscratch/bko-audit/cache
RAMTMP=/mnt/bko-ram/bko-audit
JOBS=${JOBS:-8}
TARGET_MAX_GIB=${TARGET_MAX_GIB:-60}
CROSS="windows/amd64 darwin/arm64 freebsd/amd64 openbsd/amd64 netbsd/amd64 solaris/amd64 aix/ppc64 linux/386 linux/arm linux/s390x dragonfly/amd64"
export PATH=$HOME/.cargo/bin:$PATH LC_ALL=C.UTF-8

die() { echo "audit-host: $*" >&2; exit 1; }
log() { echo "$(date '+%F %T') $*"; }
[[ $# -gt 0 ]] || die "usage: audit-host.sh NAME:SRC:OUT[:CORE] ..."

# step LABEL LOGFILE CMD...: run CMD (in the current directory) with its
# output in LOGFILE, and note its exit and time in steps.tsv.
step() {
    local label=$1 lf=$2 t0 rc; shift 2
    log "start $label"
    t0=$(date +%s)
    "$@" >"$lf" 2>&1
    rc=$?
    printf '%s\t%s\t%s\n' "$label" "$rc" "$(( $(date +%s) - t0 ))" >>"$OUT/steps.tsv"
    log "end $label: exit $rc, $(( $(date +%s) - t0 ))s"
    return $rc
}

rust_env() {
    local want=${BKO_AUDIT_RUST:-1.94.1}
    unset RUSTUP_TOOLCHAIN
    if rustup toolchain list 2>/dev/null | grep -q "^$want-"; then
        export RUSTUP_TOOLCHAIN=$want
    elif ! rustc -V 2>/dev/null | grep -q "^rustc $want "; then
        die "needs Rust $want: rustup toolchain install $want --profile minimal"
    fi
    export CARGO_HOME=$CACHE/cargo-home CARGO_TARGET_DIR=$CACHE/target/$NAME
    export CARGO_BUILD_JOBS=$JOBS RUST_TEST_THREADS=$JOBS CARGO_TERM_COLOR=never
    export CARGO_INCREMENTAL=0 INSTA_UPDATE=no
    # No RUST_BACKTRACE: rustic_core's insta snapshots of errors expect
    # "<disabled>" backtraces, and fail with one set.
    unset RUSTFLAGS CARGO_ENCODED_RUSTFLAGS RUSTC_WRAPPER RUST_BACKTRACE RUST_LIB_BACKTRACE
    mkdir -p "$CARGO_HOME" "$CARGO_TARGET_DIR"
    local g
    g=$(du -s --block-size=1G "$CARGO_TARGET_DIR" 2>/dev/null | cut -f1)
    if [[ ${g:-0} -gt $TARGET_MAX_GIB ]]; then
        log "$CARGO_TARGET_DIR is ${g} GiB, over $TARGET_MAX_GIB: emptied"
        rm -rf "$CARGO_TARGET_DIR" && mkdir -p "$CARGO_TARGET_DIR"
    fi
    {
        echo "rustc=$(rustc -V)"
        echo "cargo=$(cargo -V)"
        echo "rust_toolchain=${RUSTUP_TOOLCHAIN:-default}"
    } >>"$OUT/info.txt"
}
# No debug info: a far smaller target directory; the tests are the same.
CARGO_CFG=(--config 'profile.dev.debug=false' --config 'profile.test.debug=false'
    --config 'profile.dev.package."*".debug=false' --config 'profile.test.package."*".debug=false')

go_env() {
    local tc go
    tc=$(sed -n 's/^toolchain go//p' go.mod | head -1)
    [[ -n $tc ]] || tc=$(sed -n 's/^go \([0-9.]*\)$/\1/p' go.mod | head -1)
    go=${BKO_AUDIT_GO:-}
    if [[ -z $go ]]; then
        if [[ -x $HOME/sdk/go$tc/bin/go ]]; then go=$HOME/sdk/go$tc/bin/go; else go=$(command -v go) || die "no go"; fi
    fi
    export PATH=$(dirname "$go"):$PATH GOTOOLCHAIN=local GOFLAGS=-modcacherw
    export GOCACHE=$CACHE/gocache GOMODCACHE=$CACHE/gomod
    # restic's TestStdinFromCommand* run "python", which Debian doesn't
    # have by that name: python3 stands in, as in restic's CI.
    if ! command -v python >/dev/null; then
        mkdir -p "$here/pyshim" && ln -sf "$(command -v python3)" "$here/pyshim/python" &&
            export PATH=$PATH:$here/pyshim
    fi
    mkdir -p "$GOCACHE" "$GOMODCACHE"
    { echo "go=$(go version)"; echo "go_mod_toolchain=go$tc"; } >>"$OUT/info.txt"
}

run_restic() {
    go_env
    mkdir -p "$OUT/bin" "$OUT/cross"
    step "go mod download" "$OUT/moddownload.log" go mod download
    step "go build" "$OUT/build.log" go build -o "$OUT/bin/restic" ./cmd/restic
    [[ -x $OUT/bin/restic ]] && echo "restic_version=$("$OUT/bin/restic" version 2>&1 | head -1)" >>"$OUT/info.txt"
    step "go vet" "$OUT/vet.txt" go vet ./...
    step "go test" "$OUT/test.stderr" sh -c "exec go test -count=1 -json -p $JOBS -timeout 60m ./... >'$OUT/test.json'"
    local t
    for t in $CROSS; do
        step "cross $t" "$OUT/cross/${t/\//-}.txt" env CGO_ENABLED=0 GOOS=${t%/*} GOARCH=${t#*/} \
            go build -o /dev/null ./cmd/restic
    done
    python3 "$here/results.py" go "$OUT"
}

run_rustic_core() {
    rust_env
    step "cargo test" "$OUT/test.log" cargo "${CARGO_CFG[@]}" test --all-targets --all-features \
        --workspace --examples --no-fail-fast
    step "cargo test --doc" "$OUT/doctest.log" cargo "${CARGO_CFG[@]}" test --doc --all-features \
        --workspace --no-fail-fast
    python3 "$here/results.py" cargo "$OUT"
}

run_rustic() {
    rust_env
    local locked=(--locked)
    if [[ -n $CORE ]]; then
        # SRC stays the pristine archive; the build tree beside it takes
        # CORE's rustic_core and walkdir (prep-rustic-src.sh).
        local work=$SRC+core-$(basename "$CORE" | sed 's/^rustic_core-//')
        if [[ ! -f $work/.bko-prepared ]]; then
            rm -rf "$work" && mkdir -p "$work" && cp -a "$SRC" "$work/rustic" &&
                ln -s "$CORE" "$work/rustic_core" || die "can't make $work"
            step "prep-rustic-src" "$OUT/prep.log" sh "$here/prep-rustic-src.sh" "$work" ||
                { cat "$OUT/prep.log"; die "prep-rustic-src.sh failed"; }
            touch "$work/.bko-prepared"
        fi
        cd "$work/rustic" || die "no $work/rustic"
        echo "rustic_core_tree=$CORE" >>"$OUT/info.txt"
    fi
    # Which rustic_core (and walkdir) the build takes, from Cargo.lock.
    awk '$0 == "name = \"rustic_core\"" || $0 == "name = \"walkdir\"" { n = $3; gsub(/"/, "", n); next }
        n != "" && /^version = / { v = $3; gsub(/"/, "", v) }
        n != "" && /^source = / { s = $3; gsub(/"/, "", s) }
        n != "" && /^$/ { print n "_used=" v " " (s == "" ? "path" : s); n = ""; s = "" }' Cargo.lock >>"$OUT/info.txt"
    step "cargo test" "$OUT/test.log" cargo "${CARGO_CFG[@]}" test "${locked[@]}" --all-targets \
        --features release --workspace --no-fail-fast
    step "cargo test --doc" "$OUT/doctest.log" cargo "${CARGO_CFG[@]}" test "${locked[@]}" --doc \
        --features release --workspace --no-fail-fast
    mkdir -p "$OUT/bin"
    [[ -x $CARGO_TARGET_DIR/debug/rustic ]] && cp "$CARGO_TARGET_DIR/debug/rustic" "$OUT/bin/rustic" &&
        echo "rustic_version=$("$OUT/bin/rustic" --version 2>&1 | head -1) (debug build, as the tests ran it)" >>"$OUT/info.txt"
    python3 "$here/results.py" cargo "$OUT"
}

rc_all=0
for spec in "$@"; do
    IFS=: read -r NAME SRC OUT CORE <<<"$spec"
    [[ -n $NAME && -d $SRC && -n $OUT ]] || die "bad job $spec (NAME:SRC:OUT[:CORE], SRC a directory)"
    [[ -z $CORE || -d $CORE ]] || die "no $CORE"
    mkdir -p "$OUT"
    exec 9>"$OUT/.lock"
    flock -n 9 || die "$OUT is being written by another run"
    echo $$ >"$OUT/run.pid"
    rm -f "$OUT/exit" "$OUT/steps.tsv" "$OUT/info.txt" "$OUT/results.tsv" "$OUT/details.json" "$OUT/summary.txt"
    log "job $NAME from $SRC${CORE:+ with $CORE} to $OUT"
    t0=$(date +%s)
    {
        echo "name=$NAME"
        echo "host=$(hostname -s)"
        echo "date=$(date '+%F %T %z')"
        echo "source=$SRC"
        echo "commit=$(cat "$SRC/.bko-src" 2>/dev/null)"
        echo "kernel=$(uname -r)"
        echo "user=$(id -un)"
    } >"$OUT/info.txt"
    JOBTMP=$RAMTMP/$(basename "$OUT")
    rm -rf "$JOBTMP" && mkdir -p "$JOBTMP" && export TMPDIR=$JOBTMP
    (
        cd "$SRC" || exit 1
        case $NAME in
            restic) run_restic ;;
            rustic_core) run_rustic_core ;;
            rustic) run_rustic ;;
            *) die "unknown NAME $NAME" ;;
        esac
    )
    rc=$?
    chmod -R u+rwx "$JOBTMP" 2>/dev/null; rm -rf "$JOBTMP"
    echo "seconds=$(( $(date +%s) - t0 ))" >>"$OUT/info.txt"
    [[ -s $OUT/results.tsv ]] || { [[ $rc -ne 0 ]] || rc=1; }
    echo $rc >"$OUT/exit"
    rm -f "$OUT/run.pid"
    exec 9>&-
    log "job $NAME done: exit $rc, $(( $(date +%s) - t0 ))s; $(cat "$OUT/summary.txt" 2>/dev/null)"
    [[ $rc -eq 0 ]] || rc_all=$rc
done
exit $rc_all
