#!/usr/bin/env bash
# Build bko-holt natively for a system other than Linux, in a throwaway copy
# of a test guest of that system: holt/ has no dependencies, so cargo needs
# nothing but the guest's Rust (1.85 or later).  Run as m on a test host:
#
#   build-holt.sh [--keep] PLATFORM [PORT]
#   build-holt.sh --reserved RUN PLATFORM     (HoltTest2's: under a reservation of its own)
#
# PLATFORM: freebsd (built on freebsd14, so that it runs on 14 and 15, and on
# HardenedBSD and GhostBSD), netbsd, openbsd, dragonfly, illumos or haiku;
# or linux, built here on the host as ht1-debug.sh builds it (static glibc,
# +crt-static, so that one binary dumps glibc and musl systems alike), with
# cargo's target/ in TMPDIR, which must name a directory: one in the RAM
# disk, under a reservation.  The holt/ tree comes from
# ~/backotter-work/holt-src (rsynced there by the caller).  The binary lands
# in /bkoscratch/holttest1d/bin/PLATFORM/bko-holt (linux: bin/bko-holt); the
# build's log in /bkoscratch/holttest1d/build-holt-PLATFORM/.  HOLT_SRC,
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
# held for the build's life (linux needs none).  It waits in cmp-run.sh's line
# for its room and never builds on disk.  Its exit status goes in
# HOLT_LOGS/exit.  HOLT_SRC, HOLT_OUT, HOLT_LOGS and HOLT_INFO go through.
set -euo pipefail

WORK=/home/m/backotter-work
HERE=$(dirname "$(realpath "$0")")
PLATFORMS="linux, freebsd, netbsd, openbsd, dragonfly, illumos or haiku"

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

if [[ ${1:-} == --reserved ]]; then
    [[ $# -eq 3 ]] || { echo "build-holt: --reserved RUN PLATFORM" >&2; exit 2; }
    refuse_shipped
    RUN=$2 PLATFORM=$3
    [[ $RUN =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "build-holt: RUN '$RUN': a plain name" >&2; exit 2; }
    case $PLATFORM in linux|freebsd|netbsd|openbsd|dragonfly|illumos|haiku) ;;
        *) echo "build-holt: PLATFORM: $PLATFORMS" >&2; exit 2 ;; esac
    LOGS=${HOLT_LOGS:-/bkoscratch/holttest1d/build-holt-$PLATFORM}
    mkdir -p "$LOGS"; rm -f "$LOGS/exit" "$LOGS/peak-kib"
    tmp=/mnt/bko-ram/t2b-$RUN-$PLATFORM rc=0 port=() lk=""
    finish() { rm -rf --one-file-system "$tmp"; echo "$1" >"$LOGS/exit"; exit "$1"; }
    trap 'finish 143' TERM; trap 'finish 130' INT; trap 'finish 129' HUP
    mountpoint -q /mnt/bko-ram || { echo "build-holt: no RAM disk at /mnt/bko-ram" >&2; finish 2; }
    if [[ $PLATFORM != linux ]]; then
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
    WEAR_TAG=ht1:$RUN:build-$PLATFORM "$WORK/cmp-run.sh" --reserve "${HOLT_RESERVE:-3}" 4 \
        env TMPDIR="$tmp" "$HERE/build-holt.sh" "$PLATFORM" ${port[@]+"${port[@]}"} || rc=$?
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
refuse_shipped
GUEST_PATH=/sbin:/usr/sbin:/bin:/usr/bin:/usr/local/sbin:/usr/local/bin:/usr/pkg/sbin:/usr/pkg/bin:/opt/ooce/bin:/usr/gnu/bin
case $PLATFORM in
    linux) ;;
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
[[ -f $SRC/Cargo.toml ]] || { echo "build-holt: no $SRC/Cargo.toml" >&2; exit 1; }
refuse_shipped   # (again, just before the first write)
mkdir -p "$OUT" "$LOGS"

# HOLT_INFO's JSON: PLATFORM SHA256 RUSTC.
info() {
    [[ -n ${HOLT_INFO:-} ]] || return 0
    python3 -c 'import json, socket, sys, time
p, sha, rustc, src, out = sys.argv[1:]
print(json.dumps({"platform": p, "sha256": sha, "rustc": rustc, "host": socket.gethostname().split(".")[0],
                  "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "src": src, "out": out}))' \
        "$1" "$2" "$3" "$SRC" "$OUT/bko-holt" >"$HOLT_INFO.part" && mv -f "$HOLT_INFO.part" "$HOLT_INFO"
}

if [[ $PLATFORM == linux ]]; then
    [[ -n ${TMPDIR:-} && -d $TMPDIR ]] ||
        { echo "build-holt: linux: TMPDIR must name a directory for cargo's target/ (one in the RAM disk, under cmp-run.sh --reserve)" >&2; exit 2; }
    cargo=${CARGO:-$HOME/.cargo/bin/cargo}
    [[ -x $cargo ]] || { echo "build-holt: no $cargo" >&2; exit 1; }
    # From a copy of the tree, as the guests build: cargo may rewrite Cargo.lock.
    tree=$TMPDIR/holt-src
    rm -rf "$tree"; mkdir -p "$tree"
    tar -C "$SRC" -cf - Cargo.toml Cargo.lock LICENSE src tests | tar -C "$tree" -xf -
    rv=$("$(dirname "$cargo")/rustc" --version 2>/dev/null || echo unknown)
    echo "$rv"
    if ! (cd "$tree" && RUSTFLAGS="-C target-feature=+crt-static" CARGO_TARGET_DIR=$TMPDIR/holt-target \
            "$cargo" build --release --offline --target x86_64-unknown-linux-gnu) >"$LOGS/build.log" 2>&1; then
        grep -E "^(error|warning)" -A 6 "$LOGS/build.log" | head -80 >&2
        exit 1
    fi
    b=$TMPDIR/holt-target/x86_64-unknown-linux-gnu/release/bko-holt
    "$b" --version
    cp "$b" "$OUT/bko-holt.part"
    chmod 755 "$OUT/bko-holt.part"
    mv "$OUT/bko-holt.part" "$OUT/bko-holt"
    info linux "$(sha256sum "$OUT/bko-holt" | cut -d' ' -f1)" "$rv"
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
fi
rv=$(vm_ssh "$env; rustc --version")
echo "$rv"
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
