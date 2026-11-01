#!/usr/bin/env bash
# Build a whole test round's binaries unattended: restic-bko for every OS and
# rustic-bko for Linux and DragonFly on Chopin, the native rustic-bko builds
# on Cinqcent (testvm/compare/README.md, "Builds"), then a table of what was
# built, reused or failed.  Here in stick/ beside the build scripts it drives;
# the same file runs on the Mac and on the hosts.
#
# Usage, on the Mac (no Go or Rust needed):
#   build-round.sh [--dry-run] [--hosts chopin|cinqcent|both] [--only LIST] ROUND
# On a host (the Mac part runs these over ssh):
#   build-round.sh --host chopin|cinqcent [--dry-run] [--only LIST] ROUND
#   build-round.sh --pull ROUND      (Chopin: fetch Cinqcent's binaries, SHA256SUMS on both)
#
# The Mac part checks that ~/git/restic, rustic_core and rustic (BKO_GIT) are
# clean, takes their HEADs and source digests (restic's own
# build-backotter.sh --source-digest; every file but .git, target and dist
# for the other two), syncs the trees to each host's SRC, and runs the host
# parts there in parallel with the commits and digests: a host builds nothing
# from a copy whose digests differ (round 7 shipped a build from a stale
# copy).  Binaries go to $BKO_ROUNDS/roundROUND/bin/OS/ on the host
# (BKO_ROUNDS: ~/backotter-work), logs to roundROUND/logs/.  Chopin then
# pushes what it built to Cinqcent, and --pull brings Cinqcent's back, so
# both hosts end with the whole set and the same SHA256SUMS.
#
# LIST (comma-separated): restic linux dragonfly (Chopin's), freebsd netbsd
# openbsd illumos (Cinqcent's).  Cinqcent builds in two lanes, freebsd then
# netbsd and openbsd then illumos: two guests at once, of the three it allows
# us.  illumos' binaries also go to bin/omnios (the harness takes either).
# --dry-run syncs and checks the sources and prints what would be built or
# reused, but builds and boots nothing.
#
# Speed.  Each built binary goes into a store, $BKO_BUILD_CACHE/store/
# (default ~/backotter-work/build-cache), under the digest of everything that
# goes into it: the sources (rustic's after prep-rustic-src.sh, Cargo.lock
# included; *.md left out while no .rs has an include_str!/include_bytes!),
# toolchain versions, profile, cargo config and CARGO_*/RUSTFLAGS
# environment, the build scripts, and for guest builds the image booted.  A
# later round with the same digest copies it from there instead of building,
# says so in the table and log, and adds a "reused" line to its commit file
# (whose first lines name this round's commits); OS/NAME.digest beside each
# binary lists the inputs.  BKO_REUSE=0 builds everything anyway.  Linux
# rustic builds in a fixed tree per target, $BKO_BUILD_CACHE/cargo/TARGET,
# whose target/ stays warm: changed files arrive with new mtimes (rsync
# --checksum, no -t), so cargo rebuilds what changed, rustic_core included;
# the fixed path also keeps the binary's bytes the same from round to round.
# Go's own build cache keeps restic warm; DragonFly's cross build keeps its
# cache in $DFLY_CACHE (default ~/backotter-work/dragonfly-cross).
#
#   BKO_PROFILE=fast   Linux and DragonFly rustic without fat LTO, 16 codegen
#                      units: a quicker link for fix rounds.  The last round
#                      of a batch, whose binaries are the ones judged (and
#                      shipped), must use the default, release (the fork's
#                      profile: fat LTO, one codegen unit).
#   BKO_GUEST_PROFILE  the same for the native guest builds; default fast
#                      (FAST=1, as the rounds have built them), release for
#                      binaries that ship in images.
#
# Mac environment: BKO_GIT (~/git), CHOPIN (m@chopin.local), CINQCENT
# (m@cinqcent.local), BKO_REMOTE_DIR (the hosts' directory with stick/ and
# testvm/, relative to home: backotter-work).  Passed to the hosts: BKO_ROUNDS,
# SRC (default ROUNDDIR/src), BKO_BUILD_CACHE, BKO_PROFILE, BKO_GUEST_PROFILE,
# BKO_REUSE, BKO_DFLY_PORT (2701), BKO_GUEST_PORTS ("2901 2902 2903 2904": freebsd
# netbsd openbsd illumos), DFLY_CACHE.  On a host by hand: RESTIC_COMMIT,
# RUSTIC_COMMIT, RUSTIC_CORE_COMMIT and RESTIC_DIGEST, RUSTIC_DIGEST,
# RUSTIC_CORE_DIGEST as the Mac part gives them (BKO_UNVERIFIED=1 builds
# without the digests, and the table says so); BKO_GO (go on PATH, else the
# newest ~/sdk/go*/bin/go).
#
# Needs: on the Mac git, rsync, ssh; on the hosts what the build scripts
# need (Go on Chopin, ~/.cargo/bin on both), flock, sha256sum.

set -uo pipefail

me=$0
here=$(cd "$(dirname "$0")" && pwd)
die() { echo "build-round: $*" >&2; exit 2; }
log() { echo "$(date '+%H:%M:%S') $*"; }
if command -v sha256sum >/dev/null 2>&1; then SHA=sha256sum; else SHA="shasum -a 256"; fi

# DIR [code]: the digest of DIR's files by path and content, the same for a
# checkout and an rsync copy of it; .git, target (as rsync --exclude target
# leaves them out) and ./dist left out, and with "code", *.md as well, unless
# some .rs includes a file in itself.
tree_digest() {
    local nomd=no
    [[ ${2:-} == code ]] && ! grep -rqE 'include_(str|bytes)!' --include='*.rs' "$1" && nomd=yes
    (cd "$1" && {
        find . \( -name .git -o -name target -o -path ./dist \) -prune -o -type f -print |
            { if [[ $nomd == yes ]]; then grep -v '\.md$'; else cat; fi; } |
            LC_ALL=C sort | tr '\n' '\0' | xargs -0 $SHA
        find . \( -name .git -o -name target -o -path ./dist \) -prune -o -type l -print |
            LC_ALL=C sort | while IFS= read -r l; do echo "link $l $(readlink "$l")"; done
    } | $SHA | cut -d' ' -f1)
}

dry=no hosts=both only="" mode=mac
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run) dry=yes ;;
        --hosts) hosts=${2:-}; shift ;;
        --only) only=${2:-}; shift ;;
        --host) mode=${2:-}; shift ;;
        --pull) mode=pull ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
    shift
done
[[ $# -eq 1 ]] || { sed -n '8,13p' "$me" >&2; exit 2; }
round=$1
[[ $round =~ ^[A-Za-z0-9._-]+$ ]] || die "bad ROUND $round"
want() { [[ -z $only || ",$only," == *",$1,"* ]]; }

# --- The Mac -------------------------------------------------------------------------
if [[ $mode == mac ]]; then
    git_dir=${BKO_GIT:-$HOME/git}
    chopin=${CHOPIN:-m@chopin.local} cinq=${CINQCENT:-m@cinqcent.local}
    rdir=${BKO_REMOTE_DIR:-backotter-work}
    rrounds=${BKO_ROUNDS:-backotter-work}
    rsrc=${SRC:-$rrounds/round$round/src}
    case $hosts in
        both) hl="chopin cinqcent" ;; chopin|cinqcent) hl=$hosts ;; *) die "--hosts: chopin, cinqcent or both" ;;
    esac
    for r in restic rustic_core rustic; do
        [[ -d $git_dir/$r/.git ]] || die "no checkout $git_dir/$r"
        [[ -z $(git -C "$git_dir/$r" status --porcelain) ]] || die "$git_dir/$r has uncommitted changes"
    done
    RESTIC_COMMIT=$(git -C "$git_dir/restic" rev-parse HEAD)
    RUSTIC_CORE_COMMIT=$(git -C "$git_dir/rustic_core" rev-parse HEAD)
    RUSTIC_COMMIT=$(git -C "$git_dir/rustic" rev-parse HEAD)
    RESTIC_DIGEST=$("$git_dir/restic/build-backotter.sh" --source-digest) || die "restic --source-digest failed"
    RUSTIC_CORE_DIGEST=$(tree_digest "$git_dir/rustic_core")
    RUSTIC_DIGEST=$(tree_digest "$git_dir/rustic")
    log "restic $RESTIC_COMMIT digest $RESTIC_DIGEST"
    log "rustic_core $RUSTIC_CORE_COMMIT digest $RUSTIC_CORE_DIGEST"
    log "rustic $RUSTIC_COMMIT digest $RUSTIC_DIGEST"
    hostof() { if [[ $1 == chopin ]]; then echo "$chopin"; else echo "$cinq"; fi; }
    vars() {  # the host part's environment, as a command line
        local v out="RESTIC_COMMIT=$RESTIC_COMMIT RUSTIC_COMMIT=$RUSTIC_COMMIT RUSTIC_CORE_COMMIT=$RUSTIC_CORE_COMMIT"
        out+=" RESTIC_DIGEST=$RESTIC_DIGEST RUSTIC_DIGEST=$RUSTIC_DIGEST RUSTIC_CORE_DIGEST=$RUSTIC_CORE_DIGEST"
        out+=" BKO_ROUNDS=$rrounds SRC=$rsrc"
        for v in BKO_BUILD_CACHE BKO_PROFILE BKO_GUEST_PROFILE BKO_REUSE BKO_DFLY_PORT BKO_GUEST_PORTS DFLY_CACHE BKO_WORK; do
            [[ -n ${!v:-} ]] && out+=" $v='${!v}'"
        done
        echo "$out"
    }
    peer=""
    [[ $hosts == both ]] && peer="BKO_PEER=$cinq:$rrounds/round$round/bin"
    flags=""
    [[ $dry == yes ]] && flags+=" --dry-run"
    [[ -n $only ]] && flags+=" --only $only"
    pids="" rc=0
    for h in $hl; do
        (
            ssh_h=$(hostof "$h")
            log "$h: syncing the trees to $ssh_h:$rsrc"
            ssh -o BatchMode=yes "$ssh_h" "mkdir -p $rsrc" &&
                rsync -rlp --checksum --delete --exclude target --exclude .git \
                    "$git_dir/restic" "$git_dir/rustic" "$git_dir/rustic_core" "$ssh_h:$rsrc/" ||
                { echo "build-round: $h: sync failed" >&2; exit 1; }
            p=""; [[ $h == chopin ]] && p=$peer
            ssh -o BatchMode=yes -o ServerAliveInterval=60 "$ssh_h" \
                "cd && env $(vars) $p bash $rdir/stick/build-round.sh --host $h$flags $round" 2>&1 |
                sed -u "s/^/[$h] /"
            exit "${PIPESTATUS[0]}"
        ) &
        pids+=" $!"
    done
    for p in $pids; do wait "$p" || rc=1; done
    if [[ $hosts == both && $dry == no ]]; then
        log "bringing Cinqcent's binaries to Chopin"
        ssh -o BatchMode=yes "$chopin" "cd && env BKO_ROUNDS=$rrounds BKO_PEER=$cinq:$rrounds/round$round/bin bash $rdir/stick/build-round.sh --pull $round" || rc=1
    fi
    table=$(for h in $hl; do
        ssh -o BatchMode=yes "$(hostof "$h")" "cat $rrounds/round$round/bin/build-$h.tsv 2>/dev/null" || true
    done | LC_ALL=C sort -t "$(printf '\t')" -k3,3 -k2,2)
    echo
    printf 'HOST\tBINARY\tOS\tRESULT\tSECONDS\tSHA256\tDIGEST\n%s\n' "$table"
    case $table in *FAILED*) rc=1 ;; esac
    [[ $dry == yes || -n $table ]] || rc=1
    exit $rc
fi

# --- A host ----------------------------------------------------------------------------
cd || exit 2
WORK=${BKO_WORK:-$HOME/backotter-work}
rounds=$(cd "${BKO_ROUNDS:-$WORK}" && pwd) || die "no ${BKO_ROUNDS:-$WORK}"
rdir=$rounds/round$round
BIN=$rdir/bin LOGS=$rdir/logs
CACHE=${BKO_BUILD_CACHE:-$WORK/build-cache}
mkdir -p "$CACHE" && CACHE=$(cd "$CACHE" && pwd) || die "no $CACHE"
STORE=$CACHE/store
if [[ -n ${DFLY_CACHE:-} ]]; then
    mkdir -p "$DFLY_CACHE" && DFLY_CACHE=$(cd "$DFLY_CACHE" && pwd) && export DFLY_CACHE || die "no $DFLY_CACHE"
fi
host=$(uname -n)

shasums() {  # SHA256SUMS of BIN: binaries, commit and digest files
    (cd "$BIN" && find . -type f ! -name SHA256SUMS ! -name 'build-*' | LC_ALL=C sort |
        xargs -r sha256sum >SHA256SUMS.part && mv SHA256SUMS.part SHA256SUMS)
}

if [[ $mode == pull ]]; then
    [[ -n ${BKO_PEER:-} ]] || die "--pull needs BKO_PEER (user@host:ROUNDDIR/bin)"
    rsync -rlp --checksum "$BKO_PEER/build-cinqcent.files" "$BKO_PEER/build-cinqcent.tsv" "$BIN/" ||
        die "no build-cinqcent.files at $BKO_PEER"
    rsync -rlp --checksum --files-from="$BIN/build-cinqcent.files" "$BKO_PEER/" "$BIN/" || die "pull failed"
    shasums
    rsync -rlp --checksum "$BIN/SHA256SUMS" "$BIN/build-chopin.tsv" "$BIN/build-chopin.files" "$BKO_PEER/" 2>/dev/null ||
        rsync -rlp --checksum "$BIN/SHA256SUMS" "$BKO_PEER/" || die "couldn't copy SHA256SUMS to $BKO_PEER"
    log "pulled $(wc -l <"$BIN/build-cinqcent.files") files; SHA256SUMS on both"
    exit 0
fi
[[ $mode == chopin || $mode == cinqcent ]] || die "--host: chopin or cinqcent"
SRC=$(cd "${SRC:-$rdir/src}" && pwd) || die "no SRC"
for d in restic rustic rustic_core; do [[ -d $SRC/$d ]] || die "no $SRC/$d"; done
export PATH=$HOME/.cargo/bin:$PATH
# Nothing from the caller's environment changes how cargo builds.
for v in $(env | sed -n 's/^\(CARGO_PROFILE_[A-Z0-9_]*\|CARGO_BUILD_[A-Z0-9_]*\|RUSTFLAGS\|CARGO_ENCODED_RUSTFLAGS\|CARGO_TARGET_DIR\|RUSTC_WRAPPER\|CARGO_INCREMENTAL\)=.*/\1/p'); do
    unset "$v"
done
profile=${BKO_PROFILE:-release} gprofile=${BKO_GUEST_PROFILE:-fast}
[[ $profile == release || $profile == fast ]] || die "BKO_PROFILE: release or fast"
[[ $gprofile == release || $gprofile == fast ]] || die "BKO_GUEST_PROFILE: release or fast"
profile_env() {  # PROFILE: the cargo environment for it
    [[ $1 == fast ]] && echo "CARGO_PROFILE_RELEASE_LTO=false CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16"
    true
}
mkdir -p "$BIN" "$LOGS"
rows=$LOGS/rows-$mode
rm -rf "$rows" && mkdir -p "$rows"
exec > >(tee -a "$LOGS/build-round-$mode.log") 2>&1
log "build-round $round on $host ($mode): SRC=$SRC BIN=$BIN profile=$profile guests=$gprofile${only:+ only=$only}$([[ $dry == yes ]] && echo " (dry run)")"

# The copy must be the commits the Mac part named.
verified=yes
if [[ -n ${RESTIC_DIGEST:-} && -n ${RUSTIC_DIGEST:-} && -n ${RUSTIC_CORE_DIGEST:-} ]]; then
    d=$("$SRC/restic/build-backotter.sh" --source-digest)
    [[ $d == "$RESTIC_DIGEST" ]] || die "$SRC/restic's source digest is $d, not $RESTIC_DIGEST: a stale copy"
    d=$(tree_digest "$SRC/rustic")
    [[ $d == "$RUSTIC_DIGEST" ]] || die "$SRC/rustic's digest is $d, not $RUSTIC_DIGEST: a stale copy"
    d=$(tree_digest "$SRC/rustic_core")
    [[ $d == "$RUSTIC_CORE_DIGEST" ]] || die "$SRC/rustic_core's digest is $d, not $RUSTIC_CORE_DIGEST: a stale copy"
    log "sources match the Mac's digests"
elif [[ ${BKO_UNVERIFIED:-} == 1 ]]; then
    verified=no
    log "UNVERIFIED: no digests to check $SRC against"
else
    die "RESTIC_DIGEST, RUSTIC_DIGEST and RUSTIC_CORE_DIGEST are needed (or BKO_UNVERIFIED=1)"
fi
export RESTIC_COMMIT=${RESTIC_COMMIT:-} RUSTIC_COMMIT=${RUSTIC_COMMIT:-} RUSTIC_CORE_COMMIT=${RUSTIC_CORE_COMMIT:-}

# --- Rows, the store -------------------------------------------------------------------
# row NAME OS RESULT SECONDS [DIGEST]: a line of the table, and the files made.
row() {
    local f=$BIN/$2/$1 sum=- d=${5:-}
    [[ $3 == FAILED || $3 == skipped || ! -f $f ]] || sum=$(sha256sum <"$f" | cut -c1-16)
    [[ $verified == yes ]] || d="${d:--} UNVERIFIED"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$mode" "$1" "$2" "$3" "$4" "$sum" "${d:0:12}${d:64}" >"$rows/$1-$2"
    if [[ -f $f ]]; then
        local c=${1%-bko}-commit
        printf '%s/%s\n' "$2" "$1" >"$rows/$1-$2.files"
        for x in "$2/$c" "$2/$1.digest"; do [[ -f $BIN/$x ]] && echo "$x" >>"$rows/$1-$2.files"; done
    fi
}
# digest_of INPUTS-FILE: the digest of a build, from the text of its inputs.
digest_of() { sha256sum <"$1" | cut -d' ' -f1; }
# reuse COMP DIGEST OS NAME: copy the stored build, if any, to BIN/OS/NAME,
# with a commit file naming this round's commits.
reuse() {
    local s=$STORE/$1/$2 c=${4%-bko}-commit
    [[ ${BKO_REUSE:-1} != 0 && -f $s/bin && -f $s/commit ]] || return 1
    [[ $dry == yes ]] && { log "$4 $3: would reuse $s"; return 0; }
    mkdir -p "$BIN/$3"
    cp "$s/bin" "$BIN/$3/$4.part" && chmod 755 "$BIN/$3/$4.part" && mv "$BIN/$3/$4.part" "$BIN/$3/$4" || return 1
    {
        if [[ $c == restic-commit ]]; then
            echo "${RESTIC_COMMIT:-unknown} (from $SRC/restic)"
            sed -n '2,$p' "$s/commit"
        else
            echo "${RUSTIC_COMMIT:-unknown} (local, unpushed, from $SRC/rustic)"
            echo "rustic_core ${RUSTIC_CORE_COMMIT:-unknown} (local, unpushed, from $SRC/rustic_core)"
            sed -n '3,$p' "$s/commit"
        fi
        echo "reused by build-round.sh $(date -u '+%Y-%m-%d %H:%M UTC') on $host: the build of digest $2, first built for $(head -n 1 "$s/commit")"
    } >"$BIN/$3/$c"
    { cat "$s/inputs"; echo "digest $2"; echo "reused from $s"; } >"$BIN/$3/$4.digest"
    log "REUSED $4 $3: digest $2 from $s (built $(stat -c %y "$s/bin" | cut -d. -f1))"
}
# keep COMP DIGEST OS NAME INPUTS: put BIN/OS/NAME and its commit file in the store.
keep() {
    local s=$STORE/$1/$2 c=${4%-bko}-commit
    { cat "$5"; echo "digest $2"; echo "built on $host $(date -u '+%Y-%m-%d %H:%M UTC')"; } >"$BIN/$3/$4.digest"
    if [[ -d $s ]]; then  # built again (BKO_REUSE=0): is the build reproducible?
        cmp -s "$s/bin" "$BIN/$3/$4" || log "NOTE: $4 $3 differs from the earlier build of the same digest in $s"
        return 0
    fi
    mkdir -p "$STORE/$1" && rm -rf "$s.part.$$" && mkdir "$s.part.$$" &&
        cp "$BIN/$3/$4" "$s.part.$$/bin" && cp "$BIN/$3/$c" "$s.part.$$/commit" && cp "$5" "$s.part.$$/inputs" &&
        mv -T "$s.part.$$" "$s" 2>/dev/null || rm -rf "$s.part.$$"
}
# The common inputs of every rustic build: the prepared sources, the profile.
cargo_env_inputs() {
    local f
    for f in "$HOME"/.cargo/config "$HOME"/.cargo/config.toml; do
        [[ -f $f ]] && echo "cargo-config $f $(sha256sum <"$f" | cut -d' ' -f1)"
    done
    env | grep -E '^(CARGO_|RUSTFLAGS|RUSTC|CC|CXX|AR|CFLAGS|CXXFLAGS|LDFLAGS)' | grep -v '^CARGO_HOME=' | LC_ALL=C sort
    true
}
# FILE...: their digests, by names that don't depend on where SRC or this script is.
hash_files() {
    local f n
    for f in "$@"; do
        n=${f#"$here"/}; n=${n#"$SRC"/}
        echo "file $n $(sha256sum <"$f" | cut -d' ' -f1)"
    done
}

# prepare: SRC's rustic pointed at its rustic_core (prep-rustic-src.sh), in
# ROUNDDIR/prepped, once for every rustic build on this host: they all build
# that Cargo.lock.  What it makes of rustic's Cargo.toml and Cargo.lock
# depends only on the manifests (and the index, which can take minutes to
# reach): kept in CACHE/prep under their digest, and used again for the same
# manifests.  PREP_DIGEST: the prepared trees' digest.
prepare() {
    local p=$rdir/prepped key k
    rm -rf "$p" && mkdir -p "$p" && cp -a "$SRC/rustic" "$SRC/rustic_core" "$p/" &&
        rm -rf "$p/rustic/target" "$p/rustic/dist" || die "couldn't copy $SRC to $p"
    key=$( { cd "$p" && find rustic/Cargo.toml rustic/Cargo.lock rustic_core -name target -prune -o \
        -name Cargo.toml -print | LC_ALL=C sort | xargs sha256sum; sha256sum <"$here/prep-rustic-src.sh"; cargo -V; } |
        sha256sum | cut -d' ' -f1)
    k=$CACHE/prep/$key
    if [[ -f $k/Cargo.toml && -f $k/Cargo.lock ]]; then
        cp "$k/Cargo.toml" "$k/Cargo.lock" "$p/rustic/"
        log "prep-rustic-src.sh's Cargo.toml and Cargo.lock from $k"
    else
        "$here/prep-rustic-src.sh" "$p" >"$LOGS/prep.log" 2>&1 || { cat "$LOGS/prep.log"; die "prep-rustic-src.sh failed"; }
        mkdir -p "$CACHE/prep" && rm -rf "$k.part.$$" && mkdir "$k.part.$$" &&
            cp "$p/rustic/Cargo.toml" "$p/rustic/Cargo.lock" "$k.part.$$/" &&
            mv -T "$k.part.$$" "$k" 2>/dev/null || rm -rf "$k.part.$$"
    fi
    PREPPED=$p
    PREP_DIGEST="$(tree_digest "$p/rustic" code) $(tree_digest "$p/rustic_core" code)"
    log "prepared $p (sources $PREP_DIGEST)"
}

# --- Chopin's builds ---------------------------------------------------------------------
oses="linux freebsd openbsd netbsd dragonfly illumos"
build_restic() {
    local go=${BKO_GO:-} os gov sd todo="" t0=$SECONDS d inputs
    [[ -n $go ]] || go=$(command -v go) || go=$(ls -d "$HOME"/sdk/go*/bin/go 2>/dev/null | sort -V | tail -n 1)
    [[ -x $go ]] || { log "no go"; for os in $oses; do row restic-bko $os FAILED 0; done; return 1; }
    export PATH=$(dirname "$go"):$PATH
    gov=$(go version)
    sd=$("$SRC/restic/build-backotter.sh" --source-digest)
    for os in $oses; do
        inputs=$LOGS/restic-$os.inputs
        { echo "restic-bko $os/amd64"; echo "sources $sd"
          echo "$gov"; hash_files "$SRC/restic/build-backotter.sh"
          env | grep -E '^(GO[A-Z0-9_]*|CGO_[A-Z_]*)=' | grep -vE '^(GOCACHE|GOMODCACHE|GOPATH|GOROOT)=' | LC_ALL=C sort; } >"$inputs"
        d=$(digest_of "$inputs")
        if reuse restic-$os "$d" $os restic-bko; then
            [[ $dry == yes ]] || row restic-bko $os reused 0 "$d"
        else
            todo+=" $os"
        fi
    done
    [[ -n $todo ]] || return 0
    if [[ $dry == yes ]]; then log "restic-bko:$todo: would build (build-backotter.sh)"; return 0; fi
    log "restic-bko: building$todo ($gov)"
    rm -rf "$SRC/restic/dist"
    if ! (cd "$SRC/restic" && ./build-backotter.sh $(for os in $todo; do echo $os/amd64; done)) >"$LOGS/build-restic.log" 2>&1; then
        tail -n 20 "$LOGS/build-restic.log"
        for os in $todo; do row restic-bko $os FAILED $((SECONDS - t0)); done
        return 1
    fi
    d=$(sed -n 's/^source digest //p' "$SRC/restic/dist/restic-commit")
    if [[ $verified == yes && $d != "$RESTIC_DIGEST" ]]; then
        log "restic-commit says source digest $d, not $RESTIC_DIGEST"
        for os in $todo; do row restic-bko $os FAILED $((SECONDS - t0)); done
        return 1
    fi
    for os in $todo; do
        mkdir -p "$BIN/$os"
        cp "$SRC"/restic/dist/restic-backotter-*-$os-amd64 "$BIN/$os/restic-bko.part" &&
            chmod 755 "$BIN/$os/restic-bko.part" && mv "$BIN/$os/restic-bko.part" "$BIN/$os/restic-bko" &&
            cp "$SRC/restic/dist/restic-commit" "$BIN/$os/restic-commit" || { row restic-bko $os FAILED 0; continue; }
        d=$(digest_of "$LOGS/restic-$os.inputs")
        keep restic-$os "$d" $os restic-bko "$LOGS/restic-$os.inputs"
        row restic-bko $os built $((SECONDS - t0)) "$d"
    done
    log "restic-bko built in $((SECONDS - t0)) s"
}
omnios_copies() {  # NAME: bin/omnios/NAME (and commit, digest) = bin/illumos's
    local x
    [[ -f $BIN/illumos/$1 ]] || return 0
    mkdir -p "$BIN/omnios"
    for x in "$1" "${1%-bko}-commit" "$1.digest"; do
        [[ -f $BIN/illumos/$x ]] && cp -p "$BIN/illumos/$x" "$BIN/omnios/$x"
    done
    [[ -f $rows/$1-illumos.files ]] && sed 's|^illumos/|omnios/|' "$rows/$1-illumos.files" >"$rows/$1-omnios.files"
    true
}
build_linux() {
    local t=x86_64-unknown-linux-gnu t0=$SECONDS d out inputs=$LOGS/rustic-linux.inputs pe
    local tree=$CACHE/cargo/$t
    pe=$(profile_env "$profile")
    { echo "rustic-bko $t, build-backotter.sh in a fixed tree"; echo "sources $PREP_DIGEST"; echo "profile $profile $pe"
      rustc -vV; cargo -V; echo "cc $(cc --version | head -n 1)"; cargo_env_inputs; } >"$inputs"
    d=$(digest_of "$inputs")
    if reuse rustic-linux "$d" linux rustic-bko; then
        [[ $dry == yes ]] || row rustic-bko linux reused 0 "$d"
        return 0
    fi
    if [[ $dry == yes ]]; then log "rustic-bko linux: would build in $tree ($profile)"; return 0; fi
    log "rustic-bko linux: building in $tree ($profile)"
    mkdir -p "$tree"
    if ! (
        exec 9>"$tree/.lock"; flock 9
        for x in rustic rustic_core; do
            rsync -rlp --checksum --delete --exclude /target --exclude /dist "$PREPPED/$x/" "$tree/$x/" || exit 1
        done
        cd "$tree/rustic" && env -u PROJECT_VERSION $pe ./build-backotter.sh "$t"
    ) >"$LOGS/build-rustic-linux.log" 2>&1; then
        tail -n 30 "$LOGS/build-rustic-linux.log"
        row rustic-bko linux FAILED $((SECONDS - t0))
        return 1
    fi
    out=$(grep "^dist/rustic-backotter-.*-$t\$" "$LOGS/build-rustic-linux.log" | tail -n 1)
    mkdir -p "$BIN/linux"
    cp "$tree/rustic/$out" "$BIN/linux/rustic-bko.part" && chmod 755 "$BIN/linux/rustic-bko.part" &&
        mv "$BIN/linux/rustic-bko.part" "$BIN/linux/rustic-bko" || { row rustic-bko linux FAILED 0; return 1; }
    printf '%s (local, unpushed, from %s/rustic)\nrustic_core %s (local, unpushed, from %s/rustic_core)\n%s, built with build-backotter.sh on %s in %s%s\n' \
        "${RUSTIC_COMMIT:-unknown}" "$SRC" "${RUSTIC_CORE_COMMIT:-unknown}" "$SRC" "$(rustc --version)" "$host" "$tree" \
        "$([[ $profile == fast ]] && echo ' (BKO_PROFILE=fast: no LTO, 16 codegen units)')" >"$BIN/linux/rustic-commit"
    keep rustic-linux "$d" linux rustic-bko "$inputs"
    row rustic-bko linux built $((SECONDS - t0)) "$d"
    log "rustic-bko linux built in $((SECONDS - t0)) s"
}
build_dragonfly() {
    local t0=$SECONDS d inputs=$LOGS/rustic-dragonfly.inputs pe port=${BKO_DFLY_PORT:-2701} img
    local vm=$WORK/vms/dragonfly6/dragonfly6.qcow2
    pe=$(profile_env "$profile")
    img=$(stat -L -c '%s %Y' "$vm" 2>/dev/null) || { log "no $vm"; row rustic-bko dragonfly FAILED 0; return 1; }
    { echo "rustic-bko x86_64-unknown-dragonfly, cross"; echo "sources $PREP_DIGEST"; echo "profile $profile $pe"
      echo "sysroot dragonfly6.qcow2 $img"; echo "toolchain ${RUST_TOOLCHAIN:-default}"
      # The clang and lld it takes: the host's, else unpacked for the newest libllvmN.
      echo "clang $(ls /usr/lib/llvm-[0-9]*/bin/clang /usr/lib/llvm-[0-9]*/bin/ld.lld 2>/dev/null | tr '\n' ' ')$(
          dpkg-query -W -f '${Package}=${Version} ' 'libllvm[0-9]*' 2>/dev/null)"
      hash_files "$here/dragonfly/build-rustic.sh" "$here/prep-rustic-src.sh"; cargo_env_inputs; } >"$inputs"
    d=$(digest_of "$inputs")
    if reuse rustic-dragonfly "$d" dragonfly rustic-bko; then
        [[ $dry == yes ]] || row rustic-bko dragonfly reused 0 "$d"
        return 0
    fi
    if [[ $dry == yes ]]; then log "rustic-bko dragonfly: would build (dragonfly/build-rustic.sh $port, $profile)"; return 0; fi
    log "rustic-bko dragonfly: building (dragonfly/build-rustic.sh $port, $profile)"
    if ! env SRC="$PREPPED" BKO_BIN="$BIN" BKO_WORK="$WORK" $pe "$here/dragonfly/build-rustic.sh" "$port" \
        >"$LOGS/build-rustic-dragonfly.log" 2>&1; then
        tail -n 30 "$LOGS/build-rustic-dragonfly.log"
        row rustic-bko dragonfly FAILED $((SECONDS - t0))
        return 1
    fi
    [[ $profile == fast ]] && sed -i '3s/$/ (BKO_PROFILE=fast: no LTO, 16 codegen units)/' "$BIN/dragonfly/rustic-commit"
    keep rustic-dragonfly "$d" dragonfly rustic-bko "$inputs"
    row rustic-bko dragonfly built $((SECONDS - t0)) "$d"
    log "rustic-bko dragonfly built in $((SECONDS - t0)) s"
}

# --- Cinqcent's builds ---------------------------------------------------------------------
vm_of() { case $1 in freebsd) echo freebsd15 ;; openbsd) echo openbsd8 ;; netbsd) echo netbsd11 ;; illumos) echo omnios58 ;; esac; }
build_native() {  # OS PORT
    local os=$1 port=$2 t0=$SECONDS d inputs=$LOGS/rustic-$1.inputs vm img f fast=""
    vm=$(vm_of "$os")
    img=$WORK/vms-bko-build/$vm-rust.qcow2
    [[ -f $img ]] || img=$WORK/vms/$vm/$vm.qcow2
    [[ -f $img ]] || img=$(ls "$WORK/vms/$vm"/*.qcow2 2>/dev/null | head -n 1)
    [[ -f $img ]] || { log "no image for $vm"; row rustic-bko "$os" FAILED 0; return 1; }
    [[ $gprofile == fast ]] && fast=1
    { echo "rustic-bko $os, native in $vm"; echo "sources $PREP_DIGEST"; echo "profile $gprofile"
      for f in "$img" "$WORK/vms-bko-build/$vm-base.qcow2"; do
          [[ -f $f ]] && echo "image ${f#"$WORK"/} $(stat -L -c '%s %Y' "$f")"
      done
      hash_files "$here/build-rustic-native.sh" "$here/$os/build-rustic.sh" "$here/prep-rustic-src.sh"
      [[ -f $here/$os/platform.conf ]] && hash_files "$here/$os/platform.conf"; true; } >"$inputs"
    d=$(digest_of "$inputs")
    if reuse "rustic-$os" "$d" "$os" rustic-bko; then
        [[ $dry == yes ]] || row rustic-bko "$os" reused 0 "$d"
        return 0
    fi
    if [[ $dry == yes ]]; then log "rustic-bko $os: would build ($os/build-rustic.sh $port, $vm, $gprofile)"; return 0; fi
    log "rustic-bko $os: building ($os/build-rustic.sh $port, $vm, $gprofile)"
    if ! env SRC="$PREPPED" BKO_BIN="$BIN" BKO_WORK="$WORK" FAST=$fast "$here/$os/build-rustic.sh" "$port" \
        >"$LOGS/build-rustic-$os.log" 2>&1; then
        tail -n 30 "$LOGS/build-rustic-$os.log"
        row rustic-bko "$os" FAILED $((SECONDS - t0))
        return 1
    fi
    keep "rustic-$os" "$d" "$os" rustic-bko "$inputs"
    row rustic-bko "$os" built $((SECONDS - t0)) "$d"
    log "rustic-bko $os built in $((SECONDS - t0)) s"
}

# --- Run -------------------------------------------------------------------------------------
t_all=$SECONDS
if [[ $mode == chopin ]]; then
    want restic && build_restic
    if want linux || want dragonfly; then
        prepare
        pids=""
        if want linux; then build_linux & pids+=" $!"; fi
        if want dragonfly; then build_dragonfly & pids+=" $!"; fi
        for p in $pids; do wait "$p"; done
    fi
    [[ $dry == yes ]] || omnios_copies restic-bko
    [[ $dry == no && -f $BIN/linux/restic-commit ]] && cp "$BIN/linux/restic-commit" "$BIN/restic-commit"
else
    ports=(${BKO_GUEST_PORTS:-2901 2902 2903 2904})
    [[ ${#ports[@]} -eq 4 ]] || die "BKO_GUEST_PORTS: four ports (freebsd netbsd openbsd illumos)"
    if want freebsd || want netbsd || want openbsd || want illumos; then
        prepare
        # Two lanes, one guest each.
        ( want freebsd && build_native freebsd "${ports[0]}"; want netbsd && build_native netbsd "${ports[1]}"; true ) &
        a=$!
        ( want openbsd && build_native openbsd "${ports[2]}"; want illumos && build_native illumos "${ports[3]}"; true ) &
        b=$!
        wait $a; wait $b
        [[ $dry == yes ]] || omnios_copies rustic-bko
    fi
fi
[[ $dry == yes ]] && { log "dry run done in $((SECONDS - t_all)) s"; exit 0; }
cat "$rows"/*.files 2>/dev/null | LC_ALL=C sort -u >"$BIN/build-$mode.files"
[[ -f $BIN/restic-commit && $mode == chopin ]] && echo restic-commit >>"$BIN/build-$mode.files"
for f in "$rows"/*-omnios.files; do  # the omnios copies' rows
    [[ -f $f ]] || continue
    n=$(basename "$f" -omnios.files)
    [[ -f $rows/$n-illumos ]] && sed "s/\tillumos\t/\tomnios\t/" "$rows/$n-illumos" >"$rows/$n-omnios"
done
cat $(ls "$rows"/* 2>/dev/null | grep -v '\.files$') >"$BIN/build-$mode.tsv" 2>/dev/null
shasums
if [[ $mode == chopin && -n ${BKO_PEER:-} ]]; then
    log "copying Chopin's binaries to $BKO_PEER"
    ssh -o BatchMode=yes "${BKO_PEER%%:*}" "mkdir -p ${BKO_PEER#*:}" &&
        rsync -rlp --checksum --files-from="$BIN/build-$mode.files" "$BIN/" "$BKO_PEER/" &&
        rsync -rlp --checksum "$BIN/build-$mode.files" "$BIN/build-$mode.tsv" "$BKO_PEER/" ||
        log "FAILED to copy to $BKO_PEER"
fi
log "done in $((SECONDS - t_all)) s"
cat "$BIN/build-$mode.tsv"
! grep -q FAILED "$BIN/build-$mode.tsv"
