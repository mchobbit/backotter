#!/usr/bin/env bash
# Build a whole test round's binaries unattended: restic-bko for every OS but
# Haiku (which has no restic) and rustic-bko for Linux, DragonFly and Haiku on
# Chopin, the other native rustic-bko builds on Cinqcent
# (testvm/compare/README.md, "Builds"), then a table of what was
# built, reused or failed.  Here in stick/ beside the build scripts it drives;
# the same file runs on the Mac and on the hosts.
#
# Usage, on the Mac (no Go or Rust needed):
#   build-round.sh [--dry-run] [--hosts both|chopin|cinqcent|chopin,cinqcent] [--only LIST]
#                  [--arch amd64|arm64|amd64,arm64|all] [--dist-hosts H,H|all] ROUND
#   build-round.sh --dist-only [--dry-run] [--hosts chopin|cinqcent] --dist-hosts H,H|all ROUND
# On a host (the Mac part runs these over ssh):
#   build-round.sh --host chopin|cinqcent|ARM64HOST [--dry-run] [--only LIST] [--arch LIST] ROUND
#   build-round.sh --pull ROUND      (Chopin: fetch Cinqcent's binaries, SHA256SUMS on both)
#   build-round.sh --sums ROUND      (a build host: the round's SHA256SUMS made again)
#
# The Mac part checks that ~/git/restic, rustic_core and rustic (BKO_GIT) are
# clean, takes their HEADs and source digests (restic's own
# build-backotter.sh --source-digest, which its commit file records, and,
# as for the other two, every file but .git, target and dist: the
# --source-digest leaves out the assembly files OpenBSD's and illumos'
# builds take), syncs the trees to each host's SRC, and runs the host parts
# there in parallel with the commits and digests: a host builds nothing from
# a copy whose digests differ (round 7 shipped a build from a stale copy).
# It ends with the table and the EXPECT_* line for run-all.sh.  Binaries go to $BKO_ROUNDS/roundROUND/bin/OS/ on the host
# (BKO_ROUNDS: ~/backotter-work), logs to roundROUND/logs/.  Chopin then
# pushes what it built to Cinqcent, and --pull brings Cinqcent's back, so
# both hosts end with the whole set and the same SHA256SUMS.
#
# Distribution.  --dist-hosts names hosts of testvm/hosts.json that build
# nothing this round (copland, cage; all: every enabled amd64 host --hosts
# doesn't name) to get the round too.  An arm64 host (hosts.json's arch:
# bigfoot) is refused: a round's binaries are amd64 ones (docs/design/arm64.md
# A2 adds its own).  Once the builds pass, the Mac copies
# roundROUND/bin whole (both build-*.files and build-*.tsv, SHA256SUMS,
# restic-commit) from Chopin, or from the one build host --hosts names, to
# each: host to host by rsync, else through the Mac when the source can't
# reach it.  Then it checks the copy there with sha256sum -c and its
# SHA256SUMS against the source's (which is checked first, on the source).
# An arm64 host named (bigfoot; never one of all's) gets only the round's
# arm64 directories (bin/linux-arm64/, below), each checked by its own
# SHA256SUMS: never an amd64 binary.  Nothing there is deleted.
# --dist-only does only that, for a round built before (the Mac's checkouts
# aren't looked at); --dry-run says what it would copy.  The default is none: a host that builds nothing gets a round only
# when asked (SticTest2 asks for its --hosts).
#
# Architectures.  --arch says which the round builds: amd64 (the default,
# what every round built before), arm64, or both (amd64,arm64 or all).
# arm64 is Linux's (docs/design/arm64.md, A2), in bin/linux-arm64/ beside the
# unchanged bin/linux/, with a SHA256SUMS of its own (the round's SHA256SUMS
# lists its files too), and its rows' OS is linux-arm64:
#   restic-bko   cross-built by Go on Chopin (linux/arm64); Cinqcent builds
#                it again, as the check.
#   rustic-bko   aarch64-unknown-linux-musl, built natively on the host with
#                testvm/hosts.json's build-arm64 role (bigfoot); Chopin
#                cross-builds it again, as the check.  Both go through
#                cargo-zigbuild, zig the C compiler and linker
#                (stick/zig-tools.sh pins and installs both; Bigfoot has no
#                musl C compiler, and installing one would be a system
#                change), in the same fixed tree layout with its paths,
#                CARGO_HOME's and zig's remapped (--remap-path-prefix,
#                -ffile-prefix-map) and C's __DATE__ and __TIME__ fixed, so
#                that no path or time tells the two apart.
# A check build goes to roundROUND/check/OS/, never bin/; its row's RESULT
# is check-built or check-reused, and the Mac compares its SHA-256 with the
# shipped build's after the builds ("Cross-checks").  A check that differs
# fails the round, as a FAILED build does, and so it is copied nowhere; one
# not made (cinqcent not in --hosts) is said.  But rustic-bko's two can't be
# the same bytes, and their difference is said and fails nothing: cargo
# hashes the host's rustc -vV (its host: line) into every build script and
# proc-macro, and from there into the crate hash of each crate that depends
# on one (286 of the 478 rlibs), which moves code and data in the binary;
# docs/design/arm64.md, "What A2 built".  Every C object, and the object
# code of the other 192, is the same bytes on both.  Two arm64 hosts'
# builds would be the same.  arm64 needs chopin in
# --hosts.  The arm64 host's part runs in its job_slice (hosts.json:
# bko.slice, through hosts.sh's hosts_job_prefix), with RUSTUP_HOME and
# CARGO_HOME from its row (rustup_home, cargo_home: hosts_rust_env).  The
# Mac brings what it built (build-HOST.files) to the build hosts, which then
# hold the whole round, and sends bin/linux-arm64/ back to it from Chopin,
# checked there by its SHA256SUMS; the arm64 host's round has no other
# directory and no top SHA256SUMS.
#
# LIST (comma-separated): restic linux dragonfly haiku (Chopin's), freebsd
# netbsd openbsd illumos (Cinqcent's).  With --arch arm64, restic is
# restic-bko linux-arm64 (and Cinqcent's check of it), linux rustic-bko
# linux-arm64 (the arm64 host's, and Chopin's check).  Chopin builds Linux,
# DragonFly and Haiku at once, Haiku natively in a throwaway haiku6 (haiku/build-rustic.sh,
# on BKO_HAIKU_PORT, 2905).  Cinqcent builds in two lanes, freebsd then
# netbsd and openbsd then illumos (BKO_GUEST_LANES=1: one).  Each guest of a
# native build first takes one of its host's slots that run-all.sh's guests
# take too (testvm/compare/guest-slot.sh: BKO_GUEST_MAX, 3, on
# BKO_GUEST_RANGE, 2900-2999, counting QEMU of ours there started without
# one), so builds and tests together never run more than three.
# illumos' binaries also go to bin/omnios (the harness takes either).
# --dry-run syncs and checks the sources and prints what would be built or
# reused, but builds and boots nothing.
#
# Speed.  Each built binary goes into a store, $BKO_BUILD_CACHE/store/
# (default ~/backotter-work/build-cache), under the digest of everything that
# goes into it: the sources (rustic's after prep-rustic-src.sh, Cargo.lock
# included; *.md left out while no .rs has an include_str!/include_bytes!),
# toolchain versions, profile, cargo config and CARGO_*/RUSTFLAGS
# environment, the build scripts, and for guest builds the image booted
# (Haiku's Rust comes from HaikuPorts' rolling repository at build time, so
# a newer one there doesn't count; BKO_REUSE=0 builds with it).  A
# later round with the same digest copies it from there instead of building,
# says so in the table and log, and adds a "reused" line to its commit file
# (whose first lines name this round's commits); OS/NAME.digest beside each
# binary lists the inputs (restic's: every file of its tree, not only what
# --source-digest hashes).  BKO_REUSE=0 builds everything anyway.  Linux
# rustic builds in a fixed tree per target, $BKO_BUILD_CACHE/cargo/TARGET,
# whose target/ stays warm: changed files arrive with new mtimes (rsync
# --checksum, no -t), so cargo rebuilds what changed, rustic_core included;
# the fixed path also keeps the binary's bytes the same from round to round.
# Go's own build cache keeps restic warm; DragonFly's cross build keeps its
# cache in $DFLY_CACHE (default ~/backotter-work/dragonfly-cross), one build
# at a time (dragonfly/build-rustic.sh waits for its lock).  On Chopin,
# ~/backotter-work/{build-cache,dragonfly-cross,vms-bko-build} and Go's
# ~/.cache/go-build are symlinks into /bkoscratch, its spare NVMe, to spare
# the root disk.
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
# Rust.  The host-side cargo, prep-rustic-src.sh's on both hosts and Linux
# rustic-bko's on Chopin, is rustup's BKO_RUST_TOOLCHAIN (default 1.94.1),
# named in RUSTUP_TOOLCHAIN, never the host's rustup default: a reinstall
# reset Chopin's default to 1.85.1, which rustic's dependencies are too new
# for (sysinfo, time and zip need 1.88).  The Linux build also needs its
# x86_64-unknown-linux-musl target.  Either missing, the host part says
# which rustup command adds it, and installs nothing itself.  The Linux
# digest records rustc -vV and cargo -V, so the same release named or taken
# by default is the same digest.  DragonFly's cross build takes its own
# nightly (dragonfly/build-rustic.sh, RUST_TOOLCHAIN on the host); the
# native builds take the guest's Rust.
#
# Mac environment: BKO_GIT (~/git), CHOPIN and CINQCENT (their ssh in
# testvm/hosts.json, m@chopin.local and m@cinqcent.local; each must have the
# role build there), BKO_REMOTE_DIR (the hosts' directory with stick/ and
# testvm/, relative to home: backotter-work).  Passed to the hosts: BKO_ROUNDS,
# SRC (default ROUNDDIR/src), BKO_BUILD_CACHE, BKO_PROFILE, BKO_GUEST_PROFILE,
# BKO_REUSE, BKO_RUST_TOOLCHAIN, BKO_GUEST_LANES, BKO_DFLY_PORT (2701), BKO_GUEST_PORTS ("2901
# 2902 2903 2904": freebsd netbsd openbsd illumos), BKO_HAIKU_PORT (2905), BKO_GUEST_MAX,
# BKO_GUEST_RANGE, DFLY_CACHE, BKO_WORK (~/backotter-work: vms/, as for the
# build scripts), BKO_HAIKU_TIMEOUT (9000 s, build_haiku's backstop) and
# haiku/build-rustic.sh's BKO_HAIKU_BUILD_TIMEOUT, BKO_HAIKU_POLL,
# BKO_HAIKU_STALL, BKO_HAIKU_STALL_PCT.  On a host by hand: RESTIC_COMMIT, RUSTIC_COMMIT,
# RUSTIC_CORE_COMMIT and RESTIC_DIGEST, RESTIC_TREE_DIGEST, RUSTIC_DIGEST,
# RUSTIC_CORE_DIGEST as the Mac part gives them (BKO_UNVERIFIED=1 builds
# without the digests, and the table says so); BKO_GO (go on PATH, else the
# newest ~/sdk/go*/bin/go).
#
# Needs: on the Mac git, rsync, ssh, tar; on the hosts what the build scripts
# need (Go on Chopin; on both rustup in ~/.cargo/bin with BKO_RUST_TOOLCHAIN,
# and on Chopin its x86_64-unknown-linux-musl), flock, sha256sum, python3
# (testvm/hosts.sh).  --arch arm64 adds: Go on Cinqcent too, the same
# release as Chopin's (the check must build with the same Go), zig-tools.sh's
# zig and cargo-zigbuild on Chopin and the arm64 host, and there
# BKO_RUST_TOOLCHAIN with its aarch64-unknown-linux-musl target.

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

dry=no hosts=both only="" mode=mac dist="" distonly=no arch=amd64
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run) dry=yes ;;
        --arch) arch=${2:-}; shift ;;
        --sums) mode=sums ;;
        --hosts) hosts=${2:-}; shift ;;
        --dist-hosts) dist=${2:-}; shift ;;
        --dist-only) distonly=yes ;;
        --only) only=${2:-}; shift ;;
        --host) mode=${2:-}; shift ;;
        --pull) mode=pull ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
    shift
done
[[ $# -eq 1 ]] || { sed -n '9,16p' "$me" >&2; exit 2; }
round=$1
[[ $round =~ ^[A-Za-z0-9._-]+$ ]] || die "bad ROUND $round"
[[ $mode == mac || ( -z $dist && $distonly == no ) ]] || die "--dist-hosts and --dist-only are the Mac part's"
want() { [[ -z $only || ",$only," == *",$1,"* ]]; }
# --arch: amd64, arm64, both (amd64,arm64 or all), normalized to that order.
[[ $arch == all ]] && arch=amd64,arm64
archs=""
for a in $(echo "$arch" | tr ',' ' '); do
    [[ $a == amd64 || $a == arm64 ]] || die "--arch: amd64, arm64, amd64,arm64 or all, not $arch"
done
for a in amd64 arm64; do [[ ",$arch," == *",$a,"* ]] && archs+=",$a"; done
arch=${archs#,}
[[ -n $arch ]] || die "--arch: amd64, arm64, amd64,arm64 or all"
want_arch() { [[ ",$arch," == *",$1,"* ]]; }

# --- The Mac -------------------------------------------------------------------------
if [[ $mode == mac ]]; then
    git_dir=${BKO_GIT:-$HOME/git}
    # shellcheck source=../testvm/hosts.sh
    . "$here/../testvm/hosts.sh"
    chopin=${CHOPIN:-$(hosts_get chopin ssh)} && cinq=${CINQCENT:-$(hosts_get cinqcent ssh)} ||
        die "no ssh for chopin and cinqcent from testvm/hosts.json"
    rdir=${BKO_REMOTE_DIR:-backotter-work}
    rrounds=${BKO_ROUNDS:-backotter-work}
    rsrc=${SRC:-$rrounds/round$round/src}
    # --hosts: both, or chopin and/or cinqcent (comma-separated): the build split
    # knows those two.  hl: the build hosts, in that order.
    [[ $hosts == both ]] && hosts=chopin,cinqcent
    hl=""
    for h in $(echo "$hosts" | tr ',' ' '); do
        [[ $h == chopin || $h == cinqcent ]] || die "--hosts: both, chopin, cinqcent or chopin,cinqcent"
    done
    for h in chopin cinqcent; do [[ ",$hosts," == *",$h,"* ]] && hl+=" $h"; done
    hl=${hl# }
    [[ -n $hl ]] || die "--hosts: both, chopin, cinqcent or chopin,cinqcent"
    pair=no; [[ $hl == "chopin cinqcent" ]] && pair=yes
    builders=" $(hosts_names --role build | tr '\n' ' ')" || die "no build hosts from testvm/hosts.json"
    for h in $hl; do
        [[ $builders == *" $h "* ]] || die "$h is not a build host in testvm/hosts.json (build hosts:${builders% })"
    done
    hostof() {  # NAME: its ssh destination (CHOPIN and CINQCENT win for those two)
        case $1 in chopin) echo "$chopin" ;; cinqcent) echo "$cinq" ;; *) hosts_get "$1" ssh ;; esac
    }
    # --arch arm64: a64, the host with the build-arm64 role (its native rustic-bko).
    a64="" a64_ssh=""
    if want_arch arm64; then
        [[ " $hl " == *" chopin "* ]] ||
            die "--arch arm64 needs chopin in --hosts: it builds restic-bko linux-arm64 and the check of rustic-bko's"
        a64=$(hosts_names --role build-arm64 | head -n 1) && [[ -n $a64 ]] ||
            die "--arch arm64: no enabled host has the build-arm64 role in testvm/hosts.json"
        [[ $(hosts_arch "$a64") == arm64 ]] ||
            die "--arch arm64: $a64 has the build-arm64 role, but it is no arm64 host (testvm/hosts.json's arch)"
        a64_ssh=$(hosts_get "$a64" ssh) && [[ -n $a64_ssh ]] || die "--arch arm64: no ssh for $a64 in testvm/hosts.json"
    fi
    # --dist-hosts: dl, NAME=SSH pairs of the hosts that get the round's bin.
    dl=""
    if [[ -n $dist ]]; then
        if [[ $dist == all ]]; then
            dnames=$(hosts_names --arch amd64) || die "no hosts from testvm/hosts.json"
        else
            dnames=$(echo "$dist" | tr ',' ' ')
        fi
        for d in $dnames; do
            if [[ " $hl " == *" $d "* ]]; then
                [[ $dist == all ]] && continue
                die "--dist-hosts: $d builds this round (--hosts $hosts)"
            fi
            [[ $d == "$a64" && $distonly == no ]] && die "--dist-hosts: $d builds this round's arm64 rustic-bko (--arch $arch)"
            ds=$(hosts_get "$d" ssh) && [[ -n $ds ]] ||
                die "--dist-hosts: $d isn't an enabled host of testvm/hosts.json ($(hosts_phrase))"
            da=$(hosts_arch "$d") && [[ $da == amd64 || $da == arm64 ]] ||
                die "--dist-hosts: $d is an ${da:-unknown} host (testvm/hosts.json's arch)"
            [[ " $dl " == *" $d="* ]] || dl+=" $d=$ds"
        done
        dl=${dl# }
        [[ -n $dl ]] || die "--dist-hosts $dist: no host to copy to"
    fi
    [[ $distonly == no || -n $dl ]] || die "--dist-only needs --dist-hosts"
    src_name=${hl%% *}   # the source of the copies: Chopin, else the one build host
    src_ssh=$(hostof "$src_name")
    rbin=$rrounds/round$round/bin
    dssh="ssh -o BatchMode=yes -o ConnectTimeout=8"
    # NAME SSH WANT: roundROUND/bin from the source to NAME, then checked there
    # (WANT: the source's SHA256SUMS).
    dist_one() {
        local n=$1 t=$2 want=$3 tmp ok=no got
        # shellcheck disable=SC2029  # the paths are meant to expand here
        ssh -o BatchMode=yes "$t" "mkdir -p $rbin" || { log "$n: FAILED: can't reach $t"; return 1; }
        # shellcheck disable=SC2029
        if ssh -o BatchMode=yes -o ServerAliveInterval=60 "$src_ssh" \
            "cd && rsync -rlp --checksum -e '$dssh' $rbin/ $t:$rbin/"; then
            ok=yes
        else
            log "$n: $src_name can't copy to $t: through the Mac"
            tmp=$(mktemp -d "${TMPDIR:-/tmp}/bko-dist.XXXXXX") || return 1
            rsync -rlp -e "$dssh" "$src_ssh:$rbin/" "$tmp/" && rsync -rlp --checksum -e "$dssh" "$tmp/" "$t:$rbin/" &&
                ok=yes
            rm -rf "$tmp"
        fi
        [[ $ok == yes ]] || { log "$n: FAILED to copy $rbin from $src_name"; return 1; }
        # shellcheck disable=SC2029
        got=$(ssh -o BatchMode=yes "$t" "cd $rbin && sha256sum -c --quiet --strict SHA256SUMS >&2 && cat SHA256SUMS") ||
            { log "$n: FAILED: $t:$rbin doesn't check against its SHA256SUMS"; return 1; }
        [[ $got == "$want" ]] || { log "$n: FAILED: $t:$rbin/SHA256SUMS isn't $src_name's"; return 1; }
        log "$n: $rbin copied from $src_name, $(echo "$want" | wc -l | tr -d ' ') sums checked there"
    }
    # FROM-SSH TO-SSH DIR...: the DIRs of roundROUND/bin (or a file list's
    # files, -T LIST) from one host to another through the Mac, by tar: the
    # arm64 host and the build hosts needn't reach each other.
    tar_copy() {
        local f=$1 t=$2
        shift 2
        # shellcheck disable=SC2029  # the paths are meant to expand here
        ssh -o BatchMode=yes "$t" "mkdir -p $rbin" &&
            ssh -o BatchMode=yes "$f" "cd $rbin && tar -cf - $*" | ssh -o BatchMode=yes "$t" "cd $rbin && tar -xpf -" &&
            [[ ${PIPESTATUS[0]} == 0 ]]
    }
    # FROM-SSH DIR: DIR's SHA256SUMS there, once it checks there; status 1 when it doesn't.
    dir_sums() {
        # shellcheck disable=SC2029
        ssh -o BatchMode=yes "$1" "cd $rbin/$2 && sha256sum -c --quiet --strict SHA256SUMS >&2 && cat SHA256SUMS"
    }
    # The arm64 directories (*-arm64/, with a SHA256SUMS) of the source's roundROUND/bin.
    arm_dirs() {
        # shellcheck disable=SC2029,SC2016
        ssh -o BatchMode=yes "$src_ssh" "cd $rbin 2>/dev/null && for d in *-arm64; do [ -f \"\$d/SHA256SUMS\" ] && echo \"\$d\"; done; true"
    }
    # NAME SSH: an arm64 host's copy: only the source's arm64 directories, each
    # checked there by its SHA256SUMS, and that the source's.
    dist_arm() {
        local n=$1 t=$2 dirs d want got sums=0
        dirs=$(arm_dirs) && [[ -n $dirs ]] ||
            { log "$n: FAILED: $src_name:$rbin has no arm64 directory (*-arm64/ with a SHA256SUMS) for an arm64 host"; return 1; }
        for d in $dirs; do
            want=$(dir_sums "$src_ssh" "$d") && [[ -n $want ]] ||
                { log "$n: FAILED: $src_name:$rbin/$d has no SHA256SUMS that checks"; return 1; }
            tar_copy "$src_ssh" "$t" "$d" || { log "$n: FAILED to copy $rbin/$d from $src_name"; return 1; }
            got=$(dir_sums "$t" "$d") || { log "$n: FAILED: $t:$rbin/$d doesn't check against its SHA256SUMS"; return 1; }
            [[ $got == "$want" ]] || { log "$n: FAILED: $t:$rbin/$d/SHA256SUMS isn't $src_name's"; return 1; }
            sums=$((sums + $(echo "$want" | wc -l)))
        done
        log "$n: $rbin/{$(echo $dirs | tr ' ' ',')} copied from $src_name (its arm64 binaries only), $sums sums checked there"
    }
    dist_all() {  # every --dist-hosts host; status 1 when one failed
        local p want r=0
        if [[ $dry == yes ]]; then
            for p in $dl; do
                if [[ $(hosts_arch "${p%%=*}") == arm64 ]]; then
                    log "${p%%=*}: would copy $rbin's arm64 directories from $src_name and check them there by their SHA256SUMS"
                else
                    log "${p%%=*}: would copy $rbin from $src_name and check it there by SHA256SUMS"
                fi
            done
            return 0
        fi
        local amd="" arm=""
        for p in $dl; do
            if [[ $(hosts_arch "${p%%=*}") == arm64 ]]; then arm+=" $p"; else amd+=" $p"; fi
        done
        if [[ -n $amd ]]; then
            # shellcheck disable=SC2029
            want=$(ssh -o BatchMode=yes "$src_ssh" "cd $rbin && sha256sum -c --quiet --strict SHA256SUMS >&2 && cat SHA256SUMS") &&
                [[ -n $want ]] || { log "FAILED: $src_name:$rbin has no SHA256SUMS that checks: copied to no host"; return 1; }
            for p in $amd; do dist_one "${p%%=*}" "${p#*=}" "$want" || r=1; done
        fi
        for p in $arm; do dist_arm "${p%%=*}" "${p#*=}" || r=1; done
        return $r
    }
    if [[ $distonly == yes ]]; then
        log "round$round: copying $rbin from $src_name to$(for p in $dl; do printf ' %s' "${p%%=*}"; done)$(
            [[ $dry == yes ]] && echo " (dry run)")"
        dist_all
        exit $?
    fi
    for r in restic rustic_core rustic; do
        [[ -d $git_dir/$r/.git ]] || die "no checkout $git_dir/$r"
        [[ -z $(git -C "$git_dir/$r" status --porcelain) ]] || die "$git_dir/$r has uncommitted changes"
    done
    RESTIC_COMMIT=$(git -C "$git_dir/restic" rev-parse HEAD)
    RUSTIC_CORE_COMMIT=$(git -C "$git_dir/rustic_core" rev-parse HEAD)
    RUSTIC_COMMIT=$(git -C "$git_dir/rustic" rev-parse HEAD)
    RESTIC_DIGEST=$("$git_dir/restic/build-backotter.sh" --source-digest) || die "restic --source-digest failed"
    RESTIC_TREE_DIGEST=$(tree_digest "$git_dir/restic")
    RUSTIC_CORE_DIGEST=$(tree_digest "$git_dir/rustic_core")
    RUSTIC_DIGEST=$(tree_digest "$git_dir/rustic")
    log "restic $RESTIC_COMMIT digest $RESTIC_DIGEST, tree $RESTIC_TREE_DIGEST"
    log "rustic_core $RUSTIC_CORE_COMMIT digest $RUSTIC_CORE_DIGEST"
    log "rustic $RUSTIC_COMMIT digest $RUSTIC_DIGEST"
    hostof() { case $1 in chopin) echo "$chopin" ;; cinqcent) echo "$cinq" ;; *) echo "$a64_ssh" ;; esac; }
    vars() {  # the host part's environment, as a command line
        local v out="RESTIC_COMMIT=$RESTIC_COMMIT RUSTIC_COMMIT=$RUSTIC_COMMIT RUSTIC_CORE_COMMIT=$RUSTIC_CORE_COMMIT"
        out+=" RESTIC_DIGEST=$RESTIC_DIGEST RESTIC_TREE_DIGEST=$RESTIC_TREE_DIGEST RUSTIC_DIGEST=$RUSTIC_DIGEST RUSTIC_CORE_DIGEST=$RUSTIC_CORE_DIGEST"
        out+=" BKO_ROUNDS=$rrounds SRC=$rsrc"
        for v in BKO_BUILD_CACHE BKO_PROFILE BKO_GUEST_PROFILE BKO_REUSE BKO_RUST_TOOLCHAIN BKO_DFLY_PORT BKO_GUEST_PORTS BKO_HAIKU_PORT BKO_GUEST_LANES \
            BKO_GUEST_MAX BKO_GUEST_RANGE DFLY_CACHE BKO_WORK BKO_HAIKU_TIMEOUT BKO_HAIKU_BUILD_TIMEOUT \
            BKO_HAIKU_POLL BKO_HAIKU_STALL BKO_HAIKU_STALL_PCT; do
            [[ -n ${!v:-} ]] && out+=" $v='${!v}'"
        done
        echo "$out"
    }
    peer=""
    [[ $pair == yes ]] && peer="BKO_PEER=$cinq:$rrounds/round$round/bin"
    flags=""
    [[ $dry == yes ]] && flags+=" --dry-run"
    [[ -n $only ]] && flags+=" --only $only"
    [[ $arch == amd64 ]] || flags+=" --arch $arch"
    # The arm64 host's part runs in its job_slice: the words before the command.
    jp=""
    [[ -z $a64 ]] || jp=$(hosts_job_prefix "$a64" | tr '\n' ' ') || die "no job prefix for $a64"
    pids="" rc=0
    for h in $hl $a64; do
        (
            ssh_h=$(hostof "$h")
            log "$h: syncing the trees to $ssh_h:$rsrc"
            ssh -o BatchMode=yes "$ssh_h" "mkdir -p $rsrc" &&
                rsync -rlp --checksum --delete --exclude target --exclude .git \
                    "$git_dir/restic" "$git_dir/rustic" "$git_dir/rustic_core" "$ssh_h:$rsrc/" ||
                { echo "build-round: $h: sync failed" >&2; exit 1; }
            p=""; [[ $h == chopin ]] && p=$peer
            j=""; [[ $h == "$a64" ]] && j=$jp
            ssh -o BatchMode=yes -o ServerAliveInterval=60 "$ssh_h" \
                "cd && ${j}env $(vars) $p bash $rdir/stick/build-round.sh --host $h$flags $round" 2>&1 |
                sed -u "s/^/[$h] /"
            exit "${PIPESTATUS[0]}"
        ) &
        pids+=" $!"
    done
    for p in $pids; do wait "$p" || rc=1; done
    rbuilt=$rrounds/round$round/bin
    if [[ -n $a64 && $dry == no ]]; then
        # What the arm64 host built (its build-HOST.files), to every build host.
        log "bringing $a64's binaries to $(echo "$hl" | sed 's/ / and /')"
        for h in $hl; do
            tar_copy "$a64_ssh" "$(hostof "$h")" -T "build-$a64.files" "build-$a64.files" "build-$a64.tsv" ||
                { log "FAILED to copy $a64's build-$a64.files from $a64_ssh:$rbuilt to $h"; rc=1; }
        done
    fi
    if [[ $pair == yes && $dry == no ]]; then
        log "bringing Cinqcent's binaries to Chopin"
        ssh -o BatchMode=yes "$chopin" "cd && env BKO_ROUNDS=$rrounds BKO_PEER=$cinq:$rrounds/round$round/bin bash $rdir/stick/build-round.sh --pull $round" || rc=1
    elif [[ -n $a64 && $dry == no ]]; then
        ssh -o BatchMode=yes "$chopin" "cd && env BKO_ROUNDS=$rrounds bash $rdir/stick/build-round.sh --sums $round" || rc=1
    fi
    table=$(for h in $hl $a64; do
        ssh -o BatchMode=yes "$(hostof "$h")" "cat $rrounds/round$round/bin/build-$h.tsv 2>/dev/null" || true
    done | LC_ALL=C sort -t "$(printf '\t')" -k3,3 -k2,2 -k1,1)
    echo
    printf 'HOST\tBINARY\tOS\tRESULT\tSECONDS\tSHA256\tDIGEST\n%s\n' "$table"
    case $table in *FAILED*) rc=1 ;; esac
    [[ $dry == yes || -n $table ]] || rc=1
    # Cross-checks: each check build (RESULT check-*) against the shipped
    # build of the same BINARY and OS, by SHA-256; and the arm64 builds with
    # no check this round.  Status 1 when one differs or has nothing to match.
    xcheck() {
        local r=0 host bin os res sum p phost psum
        echo
        echo "Cross-checks (a check build against the shipped one, by SHA-256):"
        while IFS=$'\t' read -r host bin os res _ sum _; do
            [[ $res == check-* ]] || continue
            p=$(printf '%s\n' "$table" | awk -F'\t' -v b="$bin" -v o="$os" '$2 == b && $3 == o && ($4 == "built" || $4 == "reused") { print $1 "\t" $6; exit }')
            if [[ -z $p ]]; then
                echo "  $bin $os: FAILED: $host's check build $sum has no shipped build to match"; r=1; continue
            fi
            phost=${p%%$'\t'*} psum=${p#*$'\t'}
            if [[ $sum == "$psum" && $sum != - ]]; then
                echo "  $bin $os: the same: $phost's $psum, $host's check $sum"
            elif [[ $bin == rustic-bko && $sum != - && $(hosts_arch "$phost") != "$(hosts_arch "$host")" ]]; then
                # cargo hashes the host's rustc -vV into every build script and
                # proc-macro, and so into each crate that uses one: builds on
                # hosts of two architectures can't be the same bytes.
                echo "  $bin $os: differs, as builds on an $(hosts_arch "$phost") and an $(hosts_arch "$host") host must" \
                    "(docs/design/arm64.md, \"What A2 built\"): $phost's $psum, $host's check $sum"
            else
                echo "  $bin $os: DIFFERS: $phost's $psum, $host's check $sum"; r=1
            fi
        done <<<"$table"
        while IFS=$'\t' read -r host bin os res _ sum _; do
            [[ $os == *-arm64 && ( $res == built || $res == reused ) ]] || continue
            printf '%s\n' "$table" | awk -F'\t' -v b="$bin" -v o="$os" '$2 == b && $3 == o && $4 ~ /^check-/ { f = 1 } END { exit !f }' ||
                echo "  $bin $os: $host's $sum, not cross-checked: no check build this round (--hosts $hosts${only:+, --only $only})"
        done <<<"$table"
        return $r
    }
    if [[ $dry == no ]] && want_arch arm64; then
        xcheck || rc=1
    fi
    if [[ -n $a64 && $dry == no ]]; then
        echo
        if [[ $rc == 0 ]]; then
            dist_arm "$a64" "$a64_ssh" || rc=1
        else
            log "round$round's arm64 directories not copied back to $a64 (the build failed)"
        fi
    fi
    if [[ -n $dl ]]; then
        echo
        if [[ $rc == 0 ]]; then
            dist_all || rc=1
        else
            log "round$round not copied to $(for p in $dl; do printf '%s ' "${p%%=*}"; done)(the build failed)"
        fi
    fi
    echo
    echo "For run-all.sh on both hosts (OLD_BIN: the round before's bin):"
    echo "EXPECT_RESTIC_COMMIT=$RESTIC_COMMIT EXPECT_RESTIC_DIGEST=$RESTIC_DIGEST" \
        "EXPECT_RUSTIC_COMMIT=$RUSTIC_COMMIT EXPECT_RUSTIC_CORE_COMMIT=$RUSTIC_CORE_COMMIT"
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
# The host table beside this script (the arm64 host's part, hosts_rust_env).
# shellcheck source=../testvm/hosts.sh
. "$here/../testvm/hosts.sh" || die "no $here/../testvm/hosts.sh"
# The arm64 host's part (--host NAME of the build-arm64 role) holds only the
# arm64 directories: no top SHA256SUMS.
armhost=no
if [[ $mode != chopin && $mode != cinqcent && $mode != pull && $mode != sums ]]; then
    [[ " $(hosts_names --role build-arm64 2>/dev/null | tr '\n' ' ') " == *" $mode "* ]] ||
        die "--host: chopin, cinqcent or a host with the build-arm64 role in testvm/hosts.json, not $mode"
    [[ $(uname -m) == aarch64 ]] || die "--host $mode: this is a $(uname -m) machine, not aarch64"
    armhost=yes
fi

# SHA256SUMS of BIN: binaries, commit and digest files (every file but the
# SHA256SUMS and build-* ones), on the arm64 host none; and each arm64
# directory's own, of its files.  ARM_SUMS: those, relative to BIN.
shasums() {
    local d
    if [[ $armhost == yes ]]; then
        rm -f "$BIN/SHA256SUMS"
    else
        (cd "$BIN" && find . -type f ! -name 'SHA256SUMS*' ! -name 'build-*' | LC_ALL=C sort |
            xargs -r sha256sum >SHA256SUMS.part && mv SHA256SUMS.part SHA256SUMS) || return 1
    fi
    ARM_SUMS=""
    for d in "$BIN"/*-arm64; do
        [[ -d $d ]] || continue
        (cd "$d" && find . -type f ! -name 'SHA256SUMS*' | LC_ALL=C sort |
            xargs -r sha256sum >SHA256SUMS.part && mv SHA256SUMS.part SHA256SUMS) || return 1
        ARM_SUMS+=" ${d#"$BIN"/}/SHA256SUMS"
    done
}

if [[ $mode == pull ]]; then
    [[ -n ${BKO_PEER:-} ]] || die "--pull needs BKO_PEER (user@host:ROUNDDIR/bin)"
    rsync -rlp --checksum "$BKO_PEER/build-cinqcent.files" "$BKO_PEER/build-cinqcent.tsv" "$BIN/" ||
        die "no build-cinqcent.files at $BKO_PEER"
    rsync -rlp --checksum --files-from="$BIN/build-cinqcent.files" "$BKO_PEER/" "$BIN/" || die "pull failed"
    shasums || die "no SHA256SUMS in $BIN"
    rsync -rlp --checksum "$BIN/SHA256SUMS" "$BIN/build-chopin.tsv" "$BIN/build-chopin.files" "$BKO_PEER/" 2>/dev/null ||
        rsync -rlp --checksum "$BIN/SHA256SUMS" "$BKO_PEER/" || die "couldn't copy SHA256SUMS to $BKO_PEER"
    for f in $ARM_SUMS; do
        rsync -rlp --checksum "$BIN/$f" "$BKO_PEER/$f" || die "couldn't copy $f to $BKO_PEER"
    done
    log "pulled $(wc -l <"$BIN/build-cinqcent.files") files; SHA256SUMS on both"
    exit 0
fi
if [[ $mode == sums ]]; then   # (the Mac part, after it brought the arm64 host's builds)
    [[ -d $BIN ]] || die "no $BIN"
    shasums || die "no SHA256SUMS in $BIN"
    log "SHA256SUMS of $BIN made again$([[ -n $ARM_SUMS ]] && echo ", and$ARM_SUMS")"
    exit 0
fi
[[ $armhost == no ]] || want_arch arm64 || die "--host $mode builds only arm64 (--arch arm64)"
SRC=$(cd "${SRC:-$rdir/src}" && pwd) || die "no SRC"
for d in restic rustic rustic_core; do [[ -d $SRC/$d ]] || die "no $SRC/$d"; done
export PATH=$HOME/.cargo/bin:$PATH
# The host's own toolchain homes, when its row names them and the caller
# set none (bigfoot's, in its staging folder: testvm/hosts.json).
hosts_rust_env "$mode" || die "testvm/hosts.json: no rust_env for $mode"
# A check build's output (--arch arm64: "Architectures" above), never BIN.
CHK=$rdir/check
# Rust for every cargo and rustc run here ("Rust" above): rustup's proxies
# take RUSTUP_TOOLCHAIN over the default, and with RUSTUP_AUTO_INSTALL=0 fail
# rather than install a missing one.  DragonFly's build names its nightly to
# rustup run, which takes that over these.
rust_tc=${BKO_RUST_TOOLCHAIN:-1.94.1}
export RUSTUP_TOOLCHAIN=$rust_tc RUSTUP_AUTO_INSTALL=0
# rust_check [TARGET]: rust_tc installed (and TARGET's std for it), or why not.
rust_check() {
    command -v rustup >/dev/null 2>&1 ||
        { echo "no rustup in ~/.cargo/bin (https://rustup.rs) for Rust $rust_tc"; return 1; }
    rustup run "$rust_tc" rustc -V >/dev/null 2>&1 ||
        { echo "no Rust $rust_tc (BKO_RUST_TOOLCHAIN): rustup toolchain install $rust_tc --profile minimal${1:+ --target $1}"; return 1; }
    [[ -z ${1:-} ]] || rustup target list --installed --toolchain "$rust_tc" 2>/dev/null | grep -qxF -- "$1" ||
        { echo "Rust $rust_tc has no $1 target: rustup target add --toolchain $rust_tc $1"; return 1; }
}
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
log "build-round $round on $host ($mode): SRC=$SRC BIN=$BIN profile=$profile guests=$gprofile arch=$arch${only:+ only=$only}$([[ $dry == yes ]] && echo " (dry run)")"

# The copy must be the commits the Mac part named.
verified=yes
RESTIC_TREE=$(tree_digest "$SRC/restic")
if [[ -n ${RESTIC_DIGEST:-} && -n ${RESTIC_TREE_DIGEST:-} && -n ${RUSTIC_DIGEST:-} && -n ${RUSTIC_CORE_DIGEST:-} ]]; then
    d=$("$SRC/restic/build-backotter.sh" --source-digest)
    [[ $d == "$RESTIC_DIGEST" ]] || die "$SRC/restic's source digest is $d, not $RESTIC_DIGEST: a stale copy"
    [[ $RESTIC_TREE == "$RESTIC_TREE_DIGEST" ]] ||
        die "$SRC/restic's tree digest is $RESTIC_TREE, not $RESTIC_TREE_DIGEST: a stale copy"
    d=$(tree_digest "$SRC/rustic")
    [[ $d == "$RUSTIC_DIGEST" ]] || die "$SRC/rustic's digest is $d, not $RUSTIC_DIGEST: a stale copy"
    d=$(tree_digest "$SRC/rustic_core")
    [[ $d == "$RUSTIC_CORE_DIGEST" ]] || die "$SRC/rustic_core's digest is $d, not $RUSTIC_CORE_DIGEST: a stale copy"
    log "sources match the Mac's digests"
elif [[ ${BKO_UNVERIFIED:-} == 1 ]]; then
    verified=no
    log "UNVERIFIED: no digests to check $SRC against"
else
    die "RESTIC_DIGEST, RESTIC_TREE_DIGEST, RUSTIC_DIGEST and RUSTIC_CORE_DIGEST are needed (or BKO_UNVERIFIED=1)"
fi
export RESTIC_COMMIT=${RESTIC_COMMIT:-} RUSTIC_COMMIT=${RUSTIC_COMMIT:-} RUSTIC_CORE_COMMIT=${RUSTIC_CORE_COMMIT:-}

# --- Rows, the store -------------------------------------------------------------------
# row NAME OS RESULT SECONDS [DIGEST]: a line of the table, and the files made.
# A check build's (BIN is CHK): RESULT check-built or check-reused, and no
# files (they aren't the round's).
row() {
    local f=$BIN/$2/$1 sum=- d=${5:-} res=$3 ck=""
    [[ $3 == FAILED || $3 == skipped || ! -f $f ]] || sum=$(sha256sum <"$f" | cut -c1-16)
    [[ $verified == yes ]] || d="${d:--} UNVERIFIED"
    if [[ $BIN == "$CHK" ]]; then
        ck=.check
        [[ $res == built || $res == reused ]] && res=check-$res
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$mode" "$1" "$2" "$res" "$4" "$sum" "${d:0:12}${d:64}" >"$rows/$1-$2$ck"
    if [[ -f $f && -z $ck ]]; then
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
    for f in "${CARGO_HOME:-$HOME/.cargo}"/config "${CARGO_HOME:-$HOME/.cargo}"/config.toml; do
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
    local p=$rdir/prepped key k why
    why=$(rust_check) || die "$why"
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
# A bin directory's Go target: OS is OS/amd64, linux-arm64 linux/arm64.
go_target() { case $1 in *-arm64) echo "${1%-arm64}/arm64" ;; *) echo "$1/amd64" ;; esac; }
# DIR...: restic-bko for each bin directory (oses, linux-arm64), into BIN
# (a check's: CHK).
build_restic() {
    local go=${BKO_GO:-} os gov sd todo="" t0=$SECONDS d inputs t dirs="$*"
    [[ -n $go ]] || go=$(command -v go) || go=$(ls -d "$HOME"/sdk/go*/bin/go 2>/dev/null | sort -V | tail -n 1)
    [[ -x $go ]] || { log "no go"; for os in $dirs; do row restic-bko $os FAILED 0; done; return 1; }
    export PATH=$(dirname "$go"):$PATH
    gov=$(go version)
    sd=$("$SRC/restic/build-backotter.sh" --source-digest)
    for os in $dirs; do
        inputs=$LOGS/restic-$os.inputs
        { echo "restic-bko $(go_target "$os")"; echo "sources $sd"; echo "tree $RESTIC_TREE"
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
    if ! (cd "$SRC/restic" && ./build-backotter.sh $(for os in $todo; do go_target "$os"; done)) >"$LOGS/build-restic.log" 2>&1; then
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
        t=$(go_target "$os")
        cp "$SRC"/restic/dist/restic-backotter-*-"${t%/*}-${t#*/}" "$BIN/$os/restic-bko.part" &&
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
# [linux|linux-arm64]: Linux's rustic-bko, into BIN (a check's: CHK).  linux
# (the default) builds x86_64-unknown-linux-musl with build-backotter.sh as
# it is; linux-arm64 aarch64-unknown-linux-musl through cargo-zigbuild
# ("Architectures" above): zigbuild/cargo stands in for cargo, the pinned
# zig and cargo-zigbuild (zig-tools.sh) on PATH, zig's and cargo-zigbuild's
# caches in CACHE, the tree's and CARGO_HOME's paths remapped, and C's
# __DATE__ and __TIME__ fixed (mimalloc prints them).
build_linux() {
    local os=${1:-linux} t0=$SECONDS d out inputs pe t tree why zt="" real="" ch zd m remap="" cmap="" zv=""
    # C's build time, which mimalloc prints (__DATE__, __TIME__), fixed: the
    # same on every host and in every round, whatever the trees' warmth.
    local cfix='-Wno-builtin-macro-redefined -D__DATE__="Nov-14-2026" -D__TIME__="00:00:00"'
    t=x86_64-unknown-linux-musl
    [[ $os == linux-arm64 ]] && t=aarch64-unknown-linux-musl
    inputs=$LOGS/rustic-$os.inputs tree=$CACHE/cargo/$t
    if ! why=$(rust_check "$t"); then
        log "rustic-bko $os: $why"
        [[ $dry == yes ]] || row rustic-bko "$os" FAILED 0
        return 1
    fi
    pe=$(profile_env "$profile")
    if [[ $os == linux ]]; then
        { echo "rustic-bko $t, build-backotter.sh in a fixed tree"; echo "sources $PREP_DIGEST"; echo "profile $profile $pe"
          rustc -vV; cargo -V; echo "cc $(cc --version | head -n 1)"; cargo_env_inputs; } >"$inputs"
    else
        if ! why=$("$here/zig-tools.sh" --check); then
            log "rustic-bko $os: $why"
            [[ $dry == yes ]] || row rustic-bko "$os" FAILED 0
            return 1
        fi
        zt=$("$here/zig-tools.sh" --path) zv=$("$here/zig-tools.sh" --versions)
        { echo "rustic-bko $t, cargo-zigbuild in a fixed tree, its paths, CARGO_HOME's and zig's remapped"
          echo "C's build time fixed: $cfix"
          echo "sources $PREP_DIGEST"; echo "profile $profile $pe"
          rustc -vV; cargo -V; echo "$zv"; hash_files "$here/zigbuild/cargo" "$here/zig-tools.sh"; cargo_env_inputs; } >"$inputs"
    fi
    d=$(digest_of "$inputs")
    if reuse "rustic-$os" "$d" "$os" rustic-bko; then
        [[ $dry == yes ]] || row rustic-bko "$os" reused 0 "$d"
        return 0
    fi
    if [[ $dry == yes ]]; then log "rustic-bko $os: would build in $tree ($profile$([[ -n $zt ]] && echo ", cargo-zigbuild"))"; return 0; fi
    log "rustic-bko $os: building in $tree ($profile$([[ -n $zt ]] && echo ", cargo-zigbuild"))"
    mkdir -p "$tree"
    if [[ $os == linux-arm64 ]]; then
        # Every form of the paths the build sees (as given, and physical)
        # becomes one fixed name, the same on every host: the tree's,
        # CARGO_HOME's, and zig's (its headers, in the C objects' debug
        # information).  A path that doesn't resolve gives none (an empty
        # prefix would match every path).
        ch=${CARGO_HOME:-$HOME/.cargo} zd=${zt%%:*}
        for m in "$tree=/bko/build" "$(cd "$tree" 2>/dev/null && pwd -P)=/bko/build" "$ch=/bko/cargo-home" "$(cd "$ch" 2>/dev/null && pwd -P)=/bko/cargo-home" \
            "$zd=/bko/zig" "$(cd "$zd" 2>/dev/null && pwd -P)=/bko/zig"; do
            [[ $m == =* || " $remap " == *" --remap-path-prefix=$m "* ]] && continue
            remap+=" --remap-path-prefix=$m" cmap+=" -ffile-prefix-map=$m"
        done
        real=$(command -v cargo)
    fi
    if ! (
        exec 9>"$tree/.lock"; flock 9
        for x in rustic rustic_core; do
            rsync -rlp --checksum --delete --exclude /target --exclude /dist "$PREPPED/$x/" "$tree/$x/" || exit 1
        done
        if [[ $os == linux-arm64 ]]; then
            mkdir -p "$CACHE/zig-cache" "$CACHE/xdg-cache"
            export PATH=$here/zigbuild:$zt:$PATH BKO_CARGO=$real ZIG_GLOBAL_CACHE_DIR=$CACHE/zig-cache \
                ZIG_LOCAL_CACHE_DIR=$CACHE/zig-cache XDG_CACHE_HOME=$CACHE/xdg-cache RUSTFLAGS="${remap# }" \
                CFLAGS_aarch64_unknown_linux_musl="${cmap# } $cfix"
        fi
        cd "$tree/rustic" && env -u PROJECT_VERSION $pe ./build-backotter.sh "$t"
    ) >"$LOGS/build-rustic-$os.log" 2>&1; then
        tail -n 30 "$LOGS/build-rustic-$os.log"
        row rustic-bko "$os" FAILED $((SECONDS - t0))
        return 1
    fi
    out=$(grep "^dist/rustic-backotter-.*-$t\$" "$LOGS/build-rustic-$os.log" | tail -n 1)
    mkdir -p "$BIN/$os"
    cp "$tree/rustic/$out" "$BIN/$os/rustic-bko.part" && chmod 755 "$BIN/$os/rustic-bko.part" &&
        mv "$BIN/$os/rustic-bko.part" "$BIN/$os/rustic-bko" || { row rustic-bko "$os" FAILED 0; return 1; }
    printf '%s (local, unpushed, from %s/rustic)\nrustic_core %s (local, unpushed, from %s/rustic_core)\n%s, built with build-backotter.sh on %s in %s%s\n' \
        "${RUSTIC_COMMIT:-unknown}" "$SRC" "${RUSTIC_CORE_COMMIT:-unknown}" "$SRC" "$(rustc --version)" "$host" "$tree" \
        "$([[ -n $zt ]] && echo ", through cargo-zigbuild ($zv)")$([[ $profile == fast ]] && echo ' (BKO_PROFILE=fast: no LTO, 16 codegen units)')" >"$BIN/$os/rustic-commit"
    keep "rustic-$os" "$d" "$os" rustic-bko "$inputs"
    row rustic-bko "$os" built $((SECONDS - t0)) "$d"
    log "rustic-bko $os built in $((SECONDS - t0)) s"
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
build_haiku() {  # natively in a throwaway haiku6, on a guest slot
    local t0=$SECONDS d inputs=$LOGS/rustic-haiku.inputs port=${BKO_HAIKU_PORT:-2905} img fast="" s
    img=$WORK/vms/haiku6/haiku6.qcow2
    [[ -f $img ]] || { log "no $img"; row rustic-bko haiku FAILED 0; return 1; }
    [[ $gprofile == fast ]] && fast=1
    { echo "rustic-bko haiku, native in haiku6"; echo "sources $PREP_DIGEST"; echo "profile $gprofile"
      echo "image ${img#"$WORK"/} $(stat -L -c '%s %Y' "$img")"
      hash_files "$here/haiku/build-rustic.sh" "$here/prep-rustic-src.sh"; } >"$inputs"
    d=$(digest_of "$inputs")
    if reuse rustic-haiku "$d" haiku rustic-bko; then
        [[ $dry == yes ]] || row rustic-bko haiku reused 0 "$d"
        return 0
    fi
    if [[ $dry == yes ]]; then log "rustic-bko haiku: would build (haiku/build-rustic.sh $port, $gprofile)"; return 0; fi
    slot_take "${BKO_GUEST_MAX:-3}" "${BKO_GUEST_RANGE:-2900-2999}"
    log "rustic-bko haiku: building (haiku/build-rustic.sh $port, $gprofile; guest slot $BKO_GUEST_SLOT)"
    # The script has its own watchdog; this is the backstop, and it saves
    # its logs in $LOGS/haiku-build.  A guest kernel panic gets one rerun.
    local try
    for try in 1 2; do
        timeout --kill-after=120 "${BKO_HAIKU_TIMEOUT:-9000}" \
            env BKO_LOG_DIR="$LOGS" SRC="$PREPPED" BKO_BIN="$BIN" BKO_WORK="$WORK" FAST=$fast "$here/haiku/build-rustic.sh" "$port" \
            >"$LOGS/build-rustic-haiku.log" 2>&1
        s=$?
        if [[ $s == 0 || $try == 2 ]] ||
            ! grep -q "the haiku6 guest's kernel panicked" "$LOGS/build-rustic-haiku.log"; then
            break
        fi
        log "rustic-bko haiku: the haiku6 guest's kernel panicked; trying once more on a fresh guest"
        mv -f "$LOGS/build-rustic-haiku.log" "$LOGS/build-rustic-haiku.1.log"
        rm -rf "$LOGS/haiku-build.1"
        [[ ! -e $LOGS/haiku-build ]] || mv "$LOGS/haiku-build" "$LOGS/haiku-build.1"
    done
    slot_give
    if [[ $s != 0 ]]; then
        tail -n 30 "$LOGS/build-rustic-haiku.log"
        [[ $s == 124 || $s == 137 ]] &&
            log "rustic-bko haiku: build-rustic.sh ran over ${BKO_HAIKU_TIMEOUT:-9000} s, killed; see $LOGS/haiku-build/"
        row rustic-bko haiku FAILED $((SECONDS - t0))
        return 1
    fi
    keep rustic-haiku "$d" haiku rustic-bko "$inputs"
    row rustic-bko haiku built $((SECONDS - t0)) "$d"
    log "rustic-bko haiku built in $((SECONDS - t0)) s"
}

# --- Cinqcent's builds ---------------------------------------------------------------------
vm_of() { case $1 in freebsd) echo freebsd14 ;; openbsd) echo openbsd8 ;; netbsd) echo netbsd11 ;; illumos) echo omnios58 ;; esac; }
build_native() {  # OS PORT
    local os=$1 port=$2 t0=$SECONDS d inputs=$LOGS/rustic-$1.inputs vm img f fast="" s
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
    slot_take "${BKO_GUEST_MAX:-3}" "${BKO_GUEST_RANGE:-2900-2999}"
    log "rustic-bko $os: building ($os/build-rustic.sh $port, $vm, $gprofile; guest slot $BKO_GUEST_SLOT)"
    env SRC="$PREPPED" BKO_BIN="$BIN" BKO_WORK="$WORK" FAST=$fast "$here/$os/build-rustic.sh" "$port" \
        >"$LOGS/build-rustic-$os.log" 2>&1
    s=$?
    slot_give
    if [[ $s != 0 ]]; then
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
    rd=""
    want_arch amd64 && rd+=" $oses"
    want_arch arm64 && rd+=" linux-arm64"
    # shellcheck disable=SC2086  # bin directories: no spaces
    want restic && build_restic $rd
    if { want_arch amd64 && { want linux || want dragonfly || want haiku; }; } || { want_arch arm64 && want linux; }; then
        prepare
        pids=""
        if want_arch amd64; then
            if want linux; then build_linux & pids+=" $!"; fi
            if want dragonfly; then build_dragonfly & pids+=" $!"; fi
            if want haiku; then
                # shellcheck source=../testvm/compare/guest-slot.sh
                . "$here/../testvm/compare/guest-slot.sh" || die "no $here/../testvm/compare/guest-slot.sh"
                build_haiku & pids+=" $!"
            fi
        fi
        # The check of the arm64 host's rustic-bko: a cross build here.
        if want_arch arm64 && want linux; then ( BIN=$CHK; build_linux linux-arm64 ) & pids+=" $!"; fi
        for p in $pids; do wait "$p"; done
    fi
    [[ $dry == yes ]] || omnios_copies restic-bko
    [[ $dry == no && -f $BIN/linux/restic-commit ]] && cp "$BIN/linux/restic-commit" "$BIN/restic-commit"
elif [[ $mode == cinqcent ]]; then
    # The check of Chopin's restic-bko linux-arm64 (Go cross-builds it here too).
    if want_arch arm64 && want restic; then ( BIN=$CHK; build_restic linux-arm64 ); fi
    if want_arch amd64; then
        ports=(${BKO_GUEST_PORTS:-2901 2902 2903 2904})
        # shellcheck source=../testvm/compare/guest-slot.sh
        . "$here/../testvm/compare/guest-slot.sh" || die "no $here/../testvm/compare/guest-slot.sh"
        [[ ${#ports[@]} -eq 4 ]] || die "BKO_GUEST_PORTS: four ports (freebsd netbsd openbsd illumos)"
        if want freebsd || want netbsd || want openbsd || want illumos; then
            prepare
            # Two lanes, one guest each when a slot is free (BKO_GUEST_LANES=1: one lane).
            ( want freebsd && build_native freebsd "${ports[0]}"; want netbsd && build_native netbsd "${ports[1]}"; true ) &
            a=$!
            [[ ${BKO_GUEST_LANES:-2} == 1 ]] && wait $a
            ( want openbsd && build_native openbsd "${ports[2]}"; want illumos && build_native illumos "${ports[3]}"; true ) &
            b=$!
            wait $a; wait $b
            [[ $dry == yes ]] || omnios_copies rustic-bko
        fi
    fi
else
    # The arm64 host (its whole part in its job_slice: the Mac starts it so).
    if want linux; then
        prepare
        build_linux linux-arm64
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
