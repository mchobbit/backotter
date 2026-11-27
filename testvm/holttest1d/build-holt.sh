#!/usr/bin/env bash
# Build bko-holt natively for a system other than Linux, in a throwaway copy
# of a test guest of that system: holt/ has no dependencies, so cargo needs
# nothing but the guest's Rust (1.85 or later).  Run as m on a test host:
#
#   build-holt.sh [--keep] PLATFORM [PORT]
#   build-holt.sh --reserved RUN PLATFORM     (HoltTest2's: under a reservation of its own)
#   build-holt.sh --where RUN PLATFORM        (where --reserved would put its scratch now)
#
# PLATFORM: freebsd (built on freebsd14, so that it runs on 14 and 15, and on
# HardenedBSD and GhostBSD), netbsd, openbsd, dragonfly, illumos or haiku;
# or linux, built here on the host (ht1-debug.sh's build too: static glibc,
# +crt-static, so that one binary dumps glibc and musl systems alike), with
# cargo's target/ in TMPDIR, which must name a directory: one in the RAM
# disk, under a reservation.  linux-arm64 is linux's twin for ARM64
# (docs/design/arm64.md, A2): aarch64-unknown-linux-gnu, +crt-static, built
# the same way, natively, on an arm64 host (testvm/hosts.json's arch:
# bigfoot), in its job_slice (bko.slice) when it has one, with RUSTUP_HOME
# and CARGO_HOME from its row when unset (rustup_home, cargo_home); on an
# x86_64 host it is refused.  It lands in /bkoscratch/holttest1d/bin/
# linux-arm64/bko-holt, beside the BSDs' PLATFORM directories (linux's
# bin/bko-holt is unchanged).  Linux builds with rustup's HOLT_RUST_TOOLCHAIN
# (default 1.94.1: the release holt-bin's shipped builds have used), named in
# RUSTUP_TOOLCHAIN, never the host's rustup default, which a reinstall can
# change unseen (Chopin's went from 1.94.1 to 1.85.1), so that every build
# uses the same Rust.  It needs that toolchain and its
# x86_64-unknown-linux-gnu std (linux-arm64: aarch64-unknown-linux-gnu);
# missing, it says which rustup command adds it and installs nothing.  CARGO names another cargo (Debian's
# /usr/bin/cargo, say), used as it is.  The guests build with their own
# packaged Rust.  The holt/ tree comes from
# ~/backotter-work/holt-src (rsynced there by the caller).  The binary lands
# in /bkoscratch/holttest1d/bin/PLATFORM/bko-holt (linux: bin/bko-holt),
# which HoltTest1 tests only when HT_HOLT names it (its default is
# ~/backotter-work/holt-bin's); the build's log in
# /bkoscratch/holttest1d/build-holt-PLATFORM/.  HOLT_SRC,
# HOLT_OUT and HOLT_LOGS override where the tree comes from, the binary goes
# (DIR/bko-holt) and the log goes; HOLT_INFO=FILE gets a JSON object about
# the build (platform, sha256, rustc, host, time).  The guest's vm-run is
# this copy's (the vm-lib.sh beside this script's testvm/).  With a test's
# T2_HOST_ROOT (set, and not /bkoscratch/test2) it needs HOLT_OUT and
# HOLT_LOGS, and refuses (exit 2) any of HOLT_OUT, HOLT_LOGS and HOLT_INFO's
# directory in ~/backotter-work/holt-bin or bko-bin or in
# /bkoscratch/holttest1d, by whatever path (a link, /bkoscratch/work).
#
# A stick/build-rustic.sh build image (~/backotter-work/vms-bko-build/
# VM-rust.qcow2) is used when there is one: it has Rust already.  Otherwise
# the guest gets Rust from its packages first (network needed).
#
# --keep (for a debugging loop, ht1-debug.sh): the build guest stays up
# after the build, and a later --keep build on the same PORT uses it again,
# its target/ kept, so cargo builds only what changed (seconds, not a fresh
# guest's 6-12 minutes).  Stop the guest with
# `build-holt.sh --stop PLATFORM [PORT]`.
#
# --reserved RUN PLATFORM (testvm/test2/t2lib/holt.py starts it, detached, as
# m): the build under a reservation of its own,
#   WEAR_TAG=ht1:RUN:build-PLATFORM cmp-run.sh --reserve HOLT_RESERVE 4 env TMPDIR=DIR build-holt.sh PLATFORM PORT
# HOLT_RESERVE GiB of RAM disk (default 3), DIR /mnt/bko-ram/t2b-RUN-PLATFORM:
# cargo's target/, and the build guest's -snapshot file (vm-run's TMPDIR).
# TMPDIR is set inside the reservation, since its sudo -E drops a TMPDIR
# given to cmp-run.sh.  It makes DIR before and removes it after; PORT is the
# first free one of 2600-2689 under ht1-queue.sh's /tmp/bko-ht1-ports locks,
# held for the build's life (linux needs none).  Its exit status goes in
# HOLT_LOGS/exit.  HOLT_SRC, HOLT_OUT, HOLT_LOGS and HOLT_INFO go through.
# RAM first, the SSD for overflow (docs/design/img-speed.md F8: a build may run
# on any build host, the small ones too), by this host's scratch_mode in the
# host table (SCRATCH_MODE in the environment instead), as ht1-queue.sh places
# a line: ram, in the RAM disk, waiting in cmp-run.sh's line for its room
# there (every build before F8); overflow, in the RAM disk when HOLT_RESERVE
# fits it right now (cmp-run.sh --free's "after" figures, the memory's 4 GiB
# with it), else on /bkoscratch when cmp-run.sh --ssd-free has room for
# HOLT_RESERVE there now, else the RAM disk's line as in mode ram; ssd, on
# /bkoscratch when it has the room, else the RAM disk.  On /bkoscratch DIR is
# /bkoscratch/t2b-RUN-PLATFORM, under cmp-run.sh --reserve-ssd HOLT_RESERVE 4
# (the wear log's scratch=ssd, which the write stop rule doesn't count).  A
# cmp-run.sh with no --reserve-ssd (a host not synced yet) is mode ram.
# `build-holt.sh --where RUN PLATFORM` prints where --reserved would put it
# now, "ram DIR" or "ssd DIR", and starts nothing.  HOLT_CMP_RUN, HOLT_RAM_ROOT
# and HOLT_SSD_ROOT (defaults WORK/cmp-run.sh, /mnt/bko-ram, /bkoscratch) are
# for a test.
#
# HOLT_CRATE=rescue (docs/design/rescue-tui.md, "Builds"; default holt, whose
# build nothing here changes) builds bko-rescue instead, the same way, in the
# same guests: HOLT_SRC is then a repo-shaped tree holding rescue/ and
# holt/src/json.rs (default ~/backotter-work/rescue-src; from a checkout,
# `rsync -aR --delete rescue holt/src/json.rs HOST:backotter-work/rescue-src/`),
# tarred from its root with those two paths (rescue/target left out), built
# in rescue/ (a guest's /tmp/rescue, so a --keep guest keeps holt's and
# rescue's target/ apart).  In the same guest boot (on the host for linux)
# the header test, `sh rescue/abi/check.sh BINARY [HOLT_ABI_CC]`, runs on the
# built binary; its output goes to HOLT_LOGS/abi.log and beside the binary
# as bko-rescue.abi.txt.  The binary lands in HOLT_OUT/bko-rescue (default
# /bkoscratch/rescue-build/bin/PLATFORM, linux's too), the logs in HOLT_LOGS
# (default /bkoscratch/rescue-build/build-PLATFORM).  None of HOLT_OUT,
# HOLT_LOGS or HOLT_INFO's directory may be in holt-bin, bko-bin,
# /bkoscratch/holttest1d or ~/backotter-work/rescue-bin (the pinned set a
# refresh script writes), by any path, test or not (exit 2).  Exit 3: built
# and landed, but the header test failed.  HOLT_INFO's JSON adds "crate" and
# "abi" (ok or FAIL).  --reserved's WEAR_TAG is rescue:RUN:build-PLATFORM.
set -euo pipefail

WORK=/home/m/backotter-work
HERE=$(dirname "$(realpath "$0")")
PLATFORMS="linux, linux-arm64, freebsd, netbsd, openbsd, dragonfly, illumos or haiku"
CRATE=${HOLT_CRATE:-holt} BIN=bko-holt
case $CRATE in
    holt) ;;
    rescue) BIN=bko-rescue ;;
    *) echo "build-holt: HOLT_CRATE=$CRATE: holt or rescue" >&2; exit 2 ;;
esac

# A test's build (T2_HOST_ROOT set, and not HoltTest2's own /bkoscratch/test2)
# never lands in the shipped sets, WORK/holt-bin and WORK/bko-bin (the images
# are built from them), nor in HoltTest1's shared /bkoscratch/holttest1d
# (bin/PLATFORM/bko-holt is every by-hand BSD run's), by any path to them: a
# link, or /bkoscratch/work, of which WORK is a bind mount, so the same
# directories under another realpath.  So each path is compared by device
# and inode (-ef), it and every ancestor of it that exists.  HOLT_OUT and
# HOLT_LOGS must be set: their defaults are HoltTest1's.  A test world's
# holt-bin is a directory of its own.
SHARED=("$WORK/holt-bin" "$WORK/bko-bin" /bkoscratch/holttest1d)
# PATH: the shared directory it is or is in, printed; else false.
in_shared() {
    local p d; p=$(realpath -m -- "$1")
    while :; do
        if [[ -e $p ]]; then
            for d in "${SHARED[@]}"; do
                [[ -e $d && $p -ef $d ]] && { echo "$d"; return 0; }
            done
        fi
        [[ $p == / ]] && return 1
        p=$(dirname -- "$p")
    done
}
refuse_shipped() {
    [[ -n ${T2_HOST_ROOT:-} ]] || return 0
    [[ $(realpath -m -- "$T2_HOST_ROOT") != "$(realpath -m /bkoscratch/test2)" ]] || return 0
    local v p d
    for v in HOLT_OUT HOLT_LOGS; do
        [[ -n ${!v:-} ]] && continue
        echo "build-holt: $v is unset, and T2_HOST_ROOT=$T2_HOST_ROOT is a test's: its default is HoltTest1's shared one; a test names its own" >&2
        exit 2
    done
    for v in HOLT_OUT HOLT_LOGS HOLT_INFO; do
        [[ -n ${!v:-} ]] || continue
        p=${!v}; [[ $v == HOLT_INFO ]] && p=$(dirname -- "$p")
        if d=$(in_shared "$p"); then
            echo "build-holt: $v=${!v} is in $d (the same device and inode), and T2_HOST_ROOT=$T2_HOST_ROOT is a test's: a test builds into its own world's holt-bin, a real directory" >&2
            exit 2
        fi
    done
}
# HOLT_CRATE=rescue: bko-rescue never lands among bko-holt's sets or in
# rescue-bin (its own pinned set, written only by a refresh script), test or not.
refuse_pinned() {
    [[ $CRATE == rescue ]] || return 0
    local SHARED=("${SHARED[@]}" "$WORK/rescue-bin") v p d
    for v in OUT LOGS HOLT_INFO; do
        [[ -n ${!v:-} ]] || continue
        p=${!v}; [[ $v == HOLT_INFO ]] && p=$(dirname -- "$p")
        if d=$(in_shared "$p"); then
            echo "build-holt: HOLT_CRATE=rescue: $v=${!v} is in $d (the same device and inode): bko-rescue builds go to a scratch directory (default /bkoscratch/rescue-build)" >&2
            exit 2
        fi
    done
}

# --reserved's scratch (the header): WHERE (ram or ssd) and DIR for RUN
# PLATFORM, by this host's scratch_mode and the room cmp-run.sh says it has now.
CMP_RUN=${HOLT_CMP_RUN:-$WORK/cmp-run.sh}
place_scratch() {
    local run=$1 plat=$2 d=${HOLT_RESERVE:-3} m=4 mode hpy fr ad am sr num='^[0-9]+([.][0-9]+)?$'
    WHERE=ram DIR=${HOLT_RAM_ROOT:-/mnt/bko-ram}/t2b-$run-$plat MODE=ram
    hpy=$(dirname "$HERE")/test2/t2lib/hosts.py; [[ -f $hpy ]] || hpy=$WORK/testvm/test2/t2lib/hosts.py
    mode=${SCRATCH_MODE:-$(python3 -I -B "$hpy" limit "$(hostname -s)" scratch_mode 2>/dev/null)}
    case $mode in overflow|ssd) MODE=$mode ;; *) return 0 ;; esac
    grep -q -- '--reserve-ssd' "$CMP_RUN" 2>/dev/null || return 0
    sr=$("$CMP_RUN" --ssd-free 2>/dev/null) && [[ $sr =~ $num ]] || sr=0
    awk -v s="$sr" -v d="$d" 'BEGIN { exit !(s + 0 >= d + 0) }' || return 0
    if [[ $mode == overflow ]]; then
        fr=$("$CMP_RUN" --free 2>/dev/null) && read -r _ ad am < <(sed -n '3p' <<<"$fr") &&
            [[ ${ad:-} =~ $num && ${am:-} =~ $num ]] &&
            awk -v ad="$ad" -v am="$am" -v d="$d" -v m="$m" 'BEGIN { exit !(d + 0 <= ad + 0 && d + m <= am + 0) }' &&
            return 0
    fi
    WHERE=ssd DIR=${HOLT_SSD_ROOT:-/bkoscratch}/t2b-$run-$plat
}

if [[ ${1:-} == --reserved || ${1:-} == --where ]]; then
    [[ $# -eq 3 ]] || { echo "build-holt: $1 RUN PLATFORM" >&2; exit 2; }
    verb=$1
    [[ $verb == --where ]] || refuse_shipped
    RUN=$2 PLATFORM=$3
    [[ $RUN =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "build-holt: RUN '$RUN': a plain name" >&2; exit 2; }
    case $PLATFORM in linux|linux-arm64|freebsd|netbsd|openbsd|dragonfly|illumos|haiku) ;;
        *) echo "build-holt: PLATFORM: $PLATFORMS" >&2; exit 2 ;; esac
    place_scratch "$RUN" "$PLATFORM"
    [[ $verb == --reserved ]] || { echo "$WHERE $DIR"; exit 0; }
    LOGS=${HOLT_LOGS:-/bkoscratch/holttest1d/build-holt-$PLATFORM}
    [[ $CRATE != rescue ]] || { LOGS=${HOLT_LOGS:-/bkoscratch/rescue-build/build-$PLATFORM}; OUT=; refuse_pinned; }
    mkdir -p "$LOGS"; rm -f "$LOGS/exit" "$LOGS/peak-kib"
    tmp=$DIR rc=0 port=() lk=""
    finish() { rm -rf --one-file-system "$tmp"; echo "$1" >"$LOGS/exit"; exit "$1"; }
    trap 'finish 143' TERM; trap 'finish 130' INT; trap 'finish 129' HUP
    res=(--reserve "${HOLT_RESERVE:-3}" 4)
    if [[ $WHERE == ssd ]]; then
        res=(--reserve-ssd "${HOLT_RESERVE:-3}" 4)
        why="the RAM disk has no room for ${HOLT_RESERVE:-3} GiB now"; [[ $MODE == overflow ]] || why="scratch_mode $MODE"
        echo "build-holt: scratch on the SSD, $tmp ($why)"
    else
        r=${HOLT_RAM_ROOT:-/mnt/bko-ram}
        mountpoint -q "$r" || { echo "build-holt: no RAM disk at $r" >&2; finish 2; }
    fi
    if [[ $PLATFORM != linux && $PLATFORM != linux-arm64 ]]; then
        mkdir -p /tmp/bko-ht1-ports
        for p in $(seq 2600 2689); do
            [[ -z $(ss -Hltn "sport = :$p" 2>/dev/null) ]] || continue
            exec {lk}>>"/tmp/bko-ht1-ports/$p.lock" || continue
            if flock -n "$lk"; then port=("$p"); break; fi
            exec {lk}>&-; lk=""
        done
        ((${#port[@]})) || { echo "build-holt: no free port in 2600-2689" >&2; finish 1; }
        echo "build-holt: port ${port[0]} (its lock held for the build)"
    fi
    rm -rf --one-file-system "$tmp"; mkdir -p "$tmp" || finish 1
    # DIR's peak (with what a QEMU holds open there unlinked: its -snapshot
    # file, as ht1-lib.sh's ht_measure counts it) in HOLT_LOGS/peak-kib, every
    # 5 s: holt.py reserves 1.1 times the last builds' peak, plus 0.25 GiB.
    ( set +e; m=0
      while kill -0 $$ 2>/dev/null; do
          k=$(du -sxk "$tmp" 2>/dev/null | cut -f1); k=${k:-0}
          for f in /proc/[0-9]*/fd/*; do
              [[ $(readlink "$f" 2>/dev/null) == "$tmp/"*" (deleted)" ]] && k=$((k + $(stat -L -c %b "$f" 2>/dev/null || echo 0) / 2))
          done
          ((k > m)) && { m=$k; echo "$m" >"$LOGS/peak-kib"; }
          sleep 5
      done ) </dev/null >/dev/null 2>&1 &
    tag=ht1; [[ $CRATE == holt ]] || tag=rescue
    WEAR_TAG=$tag:$RUN:build-$PLATFORM "$CMP_RUN" "${res[@]}" \
        env TMPDIR="$tmp" ${HOLT_CRATE:+HOLT_CRATE="$HOLT_CRATE"} "$HERE/build-holt.sh" "$PLATFORM" ${port[@]+"${port[@]}"} || rc=$?
    finish "$rc"
fi

keep=no stop=no
[[ ${1:-} == --keep ]] && { keep=yes; shift; }
[[ ${1:-} == --stop ]] && { stop=yes; shift; }
PLATFORM=${1:-} PORT=${2:-2950}
SRC=${HOLT_SRC:-$WORK/holt-src}
OUT=${HOLT_OUT:-/bkoscratch/holttest1d/bin/$PLATFORM}
[[ $PLATFORM != linux ]] || OUT=${HOLT_OUT:-/bkoscratch/holttest1d/bin}
LOGS=${HOLT_LOGS:-/bkoscratch/holttest1d/build-holt-$PLATFORM}
if [[ $CRATE == rescue ]]; then
    SRC=${HOLT_SRC:-$WORK/rescue-src}
    OUT=${HOLT_OUT:-/bkoscratch/rescue-build/bin/$PLATFORM}
    LOGS=${HOLT_LOGS:-/bkoscratch/rescue-build/build-$PLATFORM}
fi
refuse_shipped
refuse_pinned
GUEST_PATH=/sbin:/usr/sbin:/bin:/usr/bin:/usr/local/sbin:/usr/local/bin:/usr/pkg/sbin:/usr/pkg/bin:/opt/ooce/bin:/usr/gnu/bin
case $PLATFORM in
    linux|linux-arm64) ;;
    freebsd) VM=freebsd14 RUST='env ASSUME_ALWAYS_YES=yes pkg install -y rust' ;;
    netbsd) VM=netbsd11 RUST='pkg_info -e rust >/dev/null || pkgin -y install rust' ;;
    openbsd) VM=openbsd8 RUST='pkg_add -I rust' ;;
    # DragonFly: upgrade first.  Avalon carries two packages named openssl
    # (3.0, libssl.so.12, and 3.5, .so.17); rust and curl need 3.5's, but
    # pkg install takes the guest's 3.0 as meeting "openssl" ("duplicate
    # dependency listing") and leaves it, so cargo can't load.  cargo
    # --version checks that it loads.
    dragonfly) VM=dragonfly6 RUST='export ASSUME_ALWAYS_YES=yes; pkg upgrade -y && pkg install -y rust && cargo --version' ;;
    illumos) VM=omnios58 RUST='pkg install -q ooce/developer/rust developer/gcc14; s=$?; [ $s -eq 0 ] || [ $s -eq 4 ]' ;;
    haiku) VM=haiku6 RUST='pkgman install -y rust_bin gcc binutils haiku_devel' GUEST_PATH=/bin:/boot/system/bin:/boot/system/non-packaged/bin
        export VM_USER=user ;;   # Haiku's uid 0
    *) echo "build-holt: PLATFORM: $PLATFORMS" >&2; exit 2 ;;
esac
if [[ $CRATE == rescue ]]; then
    [[ -f $SRC/rescue/Cargo.toml && -f $SRC/holt/src/json.rs ]] ||
        { echo "build-holt: HOLT_CRATE=rescue: no $SRC/rescue/Cargo.toml or $SRC/holt/src/json.rs (HOLT_SRC: a repo-shaped tree)" >&2; exit 1; }
else
[[ -f $SRC/Cargo.toml ]] || { echo "build-holt: no $SRC/Cargo.toml" >&2; exit 1; }
fi
refuse_shipped   # (again, just before the first write)
refuse_pinned
mkdir -p "$OUT" "$LOGS"

# HOLT_INFO's JSON: PLATFORM SHA256 RUSTC [ABI] (ABI, ok or FAIL: HOLT_CRATE=rescue's).
info() {
    [[ -n ${HOLT_INFO:-} ]] || return 0
    python3 -c 'import json, socket, sys, time
p, sha, rustc, src, out = sys.argv[1:6]
d = {"platform": p, "sha256": sha, "rustc": rustc, "host": socket.gethostname().split(".")[0],
     "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "src": src, "out": out}
if len(sys.argv) > 6:
    d["crate"], d["abi"] = "rescue", sys.argv[6]
print(json.dumps(d))' \
        "$1" "$2" "$3" "$SRC" "$OUT/$BIN" ${4:+"$4"} >"$HOLT_INFO.part" && mv -f "$HOLT_INFO.part" "$HOLT_INFO"
}
# The header test's verdict, after the binary has landed: its output is in
# LOGS/abi.log and OUT/bko-rescue.abi.txt; exit 3 when it failed.
abi_verdict() {
    cp "$LOGS/abi.log" "$OUT/$BIN.abi.txt"
    cat "$LOGS/abi.log"
    if [[ $1 != 0 ]]; then
        echo "build-holt: $OUT/$BIN built, but its header test failed (exit $1; $LOGS/abi.log)" >&2
        exit 3
    fi
}

if [[ $PLATFORM == linux || $PLATFORM == linux-arm64 ]]; then
    [[ -n ${TMPDIR:-} && -d $TMPDIR ]] ||
        { echo "build-holt: $PLATFORM: TMPDIR must name a directory for cargo's target/ (one in the RAM disk, under cmp-run.sh --reserve)" >&2; exit 2; }
    t=x86_64-unknown-linux-gnu job=()
    if [[ $PLATFORM == linux-arm64 ]]; then
        # Natively, on an arm64 host: its toolchain homes, its job_slice.
        t=aarch64-unknown-linux-gnu
        [[ $(uname -m) == aarch64 ]] ||
            { echo "build-holt: linux-arm64 builds natively on an arm64 host (testvm/hosts.json's arch: bigfoot), not on this $(uname -m) one" >&2; exit 2; }
        HOSTS_SH=$(dirname "$HERE")/hosts.sh; [[ -f $HOSTS_SH ]] || HOSTS_SH=$WORK/testvm/hosts.sh
        # shellcheck source=../hosts.sh
        . "$HOSTS_SH"
        hosts_rust_env || { echo "build-holt: no toolchain homes from testvm/hosts.json" >&2; exit 1; }
        # Its job_slice, unless this runs in it already (cmp-run.sh --reserve's scope is there).
        slice=$(hosts_get "$(uname -n)" job_slice 2>/dev/null || true)
        if [[ -n $slice ]] && ! grep -q "/$slice/" /proc/self/cgroup 2>/dev/null; then
            mapfile -t job < <(hosts_job_prefix "$(uname -n)")
        fi
    fi
    if [[ -n ${CARGO:-} ]]; then
        cargo=$CARGO
        [[ -x $cargo ]] || { echo "build-holt: no $cargo" >&2; exit 1; }
    else
        tc=${HOLT_RUST_TOOLCHAIN:-1.94.1}
        cargo=$HOME/.cargo/bin/cargo rustup=$HOME/.cargo/bin/rustup
        [[ -x $cargo && -x $rustup ]] || { echo "build-holt: no rustup in ~/.cargo/bin (https://rustup.rs) for Rust $tc" >&2; exit 1; }
        # rustup's proxies (cargo, and the rustc it runs) take this over the
        # default, and with RUSTUP_AUTO_INSTALL=0 fail rather than install it.
        export RUSTUP_TOOLCHAIN=$tc RUSTUP_AUTO_INSTALL=0
        "$rustup" run "$tc" rustc -V >/dev/null 2>&1 ||
            { echo "build-holt: no Rust $tc (HOLT_RUST_TOOLCHAIN): rustup toolchain install $tc --profile minimal" >&2; exit 1; }
        "$rustup" target list --installed --toolchain "$tc" 2>/dev/null | grep -qxF $t ||
            { echo "build-holt: Rust $tc has no $t target: rustup target add --toolchain $tc $t" >&2; exit 1; }
    fi
    # From a copy of the tree, as the guests build: cargo may rewrite Cargo.lock.
    if [[ $CRATE == rescue ]]; then
        tree=$TMPDIR/rescue-src
        rm -rf "$tree"; mkdir -p "$tree"
        tar -C "$SRC" --exclude=rescue/target -cf - rescue holt/src/json.rs | tar -C "$tree" -xf -
        rv=$("$(dirname "$cargo")/rustc" --version 2>/dev/null || echo unknown)
        echo "$rv"
        ((${#job[@]} == 0)) || job+=(env HOME="$HOME" PATH="$PATH"
            ${RUSTUP_HOME:+RUSTUP_HOME="$RUSTUP_HOME"} ${CARGO_HOME:+CARGO_HOME="$CARGO_HOME"}
            ${RUSTUP_TOOLCHAIN:+RUSTUP_TOOLCHAIN="$RUSTUP_TOOLCHAIN"} ${RUSTUP_AUTO_INSTALL:+RUSTUP_AUTO_INSTALL="$RUSTUP_AUTO_INSTALL"})
        if ! (cd "$tree/rescue" && ${job[@]+"${job[@]}"} env RUSTFLAGS="-C target-feature=+crt-static" CARGO_TARGET_DIR="$TMPDIR/rescue-target" \
                "$cargo" build --release --offline --target $t) >"$LOGS/build.log" 2>&1; then
            grep -E "^(error|warning)" -A 6 "$LOGS/build.log" | head -80 >&2
            exit 1
        fi
        b=$TMPDIR/rescue-target/$t/release/bko-rescue
        "$b" --version
        cp "$b" "$OUT/$BIN.part"
        chmod 755 "$OUT/$BIN.part"
        mv "$OUT/$BIN.part" "$OUT/$BIN"
        arc=0; (cd "$tree/rescue" && sh abi/check.sh "$OUT/$BIN" ${HOLT_ABI_CC:+"$HOLT_ABI_CC"}) >"$LOGS/abi.log" 2>&1 || arc=$?
        av=ok; [[ $arc == 0 ]] || av=FAIL
        info "$PLATFORM" "$(sha256sum "$OUT/$BIN" | cut -d' ' -f1)" "$rv" "$av"
        ls -l "$OUT/$BIN"
        abi_verdict "$arc"
        exit 0
    fi
    tree=$TMPDIR/holt-src
    rm -rf "$tree"; mkdir -p "$tree"
    tar -C "$SRC" -cf - Cargo.toml Cargo.lock LICENSE src tests | tar -C "$tree" -xf -
    rv=$("$(dirname "$cargo")/rustc" --version 2>/dev/null || echo unknown)
    echo "$rv"
    # In the host's job_slice (none: as it is), with the environment the
    # build needs (the prefix's setpriv --reset-env clears it).
    ((${#job[@]} == 0)) || job+=(env HOME="$HOME" PATH="$PATH"
        ${RUSTUP_HOME:+RUSTUP_HOME="$RUSTUP_HOME"} ${CARGO_HOME:+CARGO_HOME="$CARGO_HOME"}
        ${RUSTUP_TOOLCHAIN:+RUSTUP_TOOLCHAIN="$RUSTUP_TOOLCHAIN"} ${RUSTUP_AUTO_INSTALL:+RUSTUP_AUTO_INSTALL="$RUSTUP_AUTO_INSTALL"})
    if ! (cd "$tree" && ${job[@]+"${job[@]}"} env RUSTFLAGS="-C target-feature=+crt-static" CARGO_TARGET_DIR="$TMPDIR/holt-target" \
            "$cargo" build --release --offline --target $t) >"$LOGS/build.log" 2>&1; then
        grep -E "^(error|warning)" -A 6 "$LOGS/build.log" | head -80 >&2
        exit 1
    fi
    b=$TMPDIR/holt-target/$t/release/bko-holt
    "$b" --version
    cp "$b" "$OUT/bko-holt.part"
    chmod 755 "$OUT/bko-holt.part"
    mv "$OUT/bko-holt.part" "$OUT/bko-holt"
    info "$PLATFORM" "$(sha256sum "$OUT/bko-holt" | cut -d' ' -f1)" "$rv"
    ls -l "$OUT/bko-holt"
    exit 0
fi

# The vm-lib.sh of this script's testvm/ (a private copy's own), else the synced one.
VM_LIB=$(dirname "$HERE")/vm-lib.sh; [[ -f $VM_LIB ]] || VM_LIB=$WORK/testvm/vm-lib.sh
VM_LIB_DIR=$(dirname "$VM_LIB")
# shellcheck source=../vm-lib.sh
. "$VM_LIB"
key=$WORK/vms/$VM/id_ed25519
[[ -f $WORK/vms-bko-build/$VM-rust.qcow2 ]] && key=$WORK/vms-bko-build/$VM.id_ed25519
# A kept build guest already on PORT.
up=no
if [[ -n $(ss -Hltn "sport = :$PORT" 2>/dev/null) ]]; then
    VM_GUEST=$VM VM_PORT=$PORT VM_KEY=$key
    T=15 vm_ssh true 2>/dev/null && up=yes
fi
if [[ $stop == yes ]]; then
    [[ $up == yes ]] && { T=30 vm_ssh '/sbin/poweroff || /sbin/shutdown -p now || /usr/sbin/shutdown -y -i5 -g0 || shutdown' >/dev/null 2>&1; echo "stopped the $VM build guest on $PORT"; }
    for _ in $(seq 30); do [[ -z $(ss -Hltn "sport = :$PORT" 2>/dev/null) ]] && exit 0; sleep 2; done
    pkill -f "^qemu-system.*:$PORT-:22"; exit 0
fi
if [[ $up == yes && $keep == yes ]]; then
    echo "Using the $VM build guest already on port $PORT"
else
    [[ $up == yes ]] && { echo "build-holt: port $PORT is in use (a kept build guest? --keep uses it, --stop stops it)" >&2; exit 1; }
    [[ $keep == yes ]] || trap vm_halt EXIT
    if [[ -f $WORK/vms-bko-build/$VM-rust.qcow2 ]]; then
        VM_IMAGE=$WORK/vms-bko-build/$VM-rust.qcow2 VM_IMAGE_KEY=$key \
            vm_boot "$VM" "$PORT" "$LOGS" --snapshot --cpus 4 --memory 4096
    else
        vm_boot "$VM" "$PORT" "$LOGS" --snapshot --cpus 4 --memory 4096
    fi
fi
env="PATH=$GUEST_PATH; export PATH"
if ! vm_ssh "$env; command -v cargo && command -v cc" >/dev/null 2>&1; then
    echo "Installing Rust"
    vm_ssh "$env; $RUST" >"$LOGS/rust-install.log" 2>&1 || { tail -20 "$LOGS/rust-install.log" >&2; exit 1; }
    # Haiku: haiku_devel must match the haiku package, so pkgman upgrades
    # that too when the guest is behind its repository, and none of it takes
    # effect until the next boot.  The guest reboots in the same QEMU, so on
    # the same throwaway overlay; /tmp is emptied at boot.
    if grep -q 'A reboot is necessary' "$LOGS/rust-install.log"; then
        echo "Rebooting into the upgraded system"
        T=30 vm_ssh 'touch /tmp/bko-before-reboot && shutdown -r -q' >/dev/null 2>&1 || true
        deadline=$((SECONDS + 600))
        until T=15 vm_ssh '[ ! -e /tmp/bko-before-reboot ]' 2>/dev/null; do
            [[ $SECONDS -lt $deadline ]] || { echo "build-holt: $VM's sshd didn't come back after the reboot (serial console: $LOGS/serial.log)" >&2; exit 1; }
            [[ -z $VM_PID ]] || kill -0 "$VM_PID" 2>/dev/null || { echo "build-holt: QEMU exited during the reboot; $LOGS/vm-run.log says:" >&2; tail -n 5 "$LOGS/vm-run.log" >&2; exit 1; }
            sleep 5
        done
    fi
fi
rv=$(vm_ssh "$env; rustc --version")
echo "$rv"
if [[ $CRATE == rescue ]]; then
    # In /tmp/rescue, the repo-shaped tree; --keep keeps its rescue/target.
    clean='rm -rf /tmp/rescue && mkdir /tmp/rescue'
    [[ $keep == yes ]] && clean='mkdir -p /tmp/rescue && rm -rf /tmp/rescue/holt /tmp/rescue/rescue/src /tmp/rescue/rescue/tests /tmp/rescue/rescue/abi'
    tar -C "$SRC" --exclude=rescue/target -czf - rescue holt/src/json.rs | T=600 vm_ssh "$clean && cd /tmp/rescue && gzip -dc | tar -xf -"
    if ! T=3600 vm_ssh "$env; cd /tmp/rescue/rescue && cargo build --release --offline 2>&1" >"$LOGS/build.log"; then
        grep -E "^(error|warning)" -A 6 "$LOGS/build.log" | head -80 >&2
        exit 1
    fi
    vm_ssh "/tmp/rescue/rescue/target/release/bko-rescue --version"
    vm_ssh 'cat /tmp/rescue/rescue/target/release/bko-rescue' >"$OUT/$BIN.part"
    chmod 755 "$OUT/$BIN.part"
    mv "$OUT/$BIN.part" "$OUT/$BIN"
    # The header test, in this boot, with the guest's own cc and headers.
    arc=0; T=600 vm_ssh "$env; cd /tmp/rescue/rescue && sh abi/check.sh target/release/bko-rescue ${HOLT_ABI_CC:-} 2>&1" >"$LOGS/abi.log" || arc=$?
    av=ok; [[ $arc == 0 ]] || av=FAIL
    info "$PLATFORM" "$(sha256sum "$OUT/$BIN" | cut -d' ' -f1)" "$rv" "$av"
    ls -l "$OUT/$BIN"
    abi_verdict "$arc"
    exit 0
fi
# --keep: the tree replaced, target/ kept (tar keeps the files' mtimes, so
# cargo rebuilds what changed).
clean='rm -rf /tmp/holt && mkdir /tmp/holt'
[[ $keep == yes ]] && clean='mkdir -p /tmp/holt && rm -rf /tmp/holt/src /tmp/holt/tests'
tar -C "$SRC" -czf - Cargo.toml Cargo.lock LICENSE src tests | T=600 vm_ssh "$clean && cd /tmp/holt && gzip -dc | tar -xf -"
if ! T=3600 vm_ssh "$env; cd /tmp/holt && cargo build --release --offline 2>&1" >"$LOGS/build.log"; then
    grep -E "^(error|warning)" -A 6 "$LOGS/build.log" | head -80 >&2
    exit 1
fi
vm_ssh "/tmp/holt/target/release/bko-holt --version"
vm_ssh 'cat /tmp/holt/target/release/bko-holt' >"$OUT/bko-holt.part"
chmod 755 "$OUT/bko-holt.part"
mv "$OUT/bko-holt.part" "$OUT/bko-holt"
info "$PLATFORM" "$(sha256sum "$OUT/bko-holt" | cut -d' ' -f1)" "$rv"
ls -l "$OUT/bko-holt"
