#!/usr/bin/env bash
# Rescue images, one lane per argument, each lane's images one after another:
#
#   img-lanes.sh [--run RUN] "P P" ["P P" ...]
#
# A P is a platform (a full build), or P@SHA256: a refresh from the parent
# whose test image has that SHA-256 (img-one.sh --refresh-from SHA256; the
# round's plan names it, img-speed.md §5.2).
# Each image is img-one.sh (the one beside this script) under its own
# `cmp-run.sh --reserve D 10`, WEAR_TAG=img:RUN:P: D GiB of RAM disk from
# images/P/peak-kib (the last build's peak: 1.1 x it + 0.25 GiB), else 6
# (linux 1), and never less than the last test image + 0.5 GiB: the boot tests
# copy the image there and boot it from the RAM disk (img-speed.md §9), as many
# modes at once as the 10 GiB holds at 4.5 GiB each, two (BKO_BOOT_RAM,
# BKO_BOOT_RAM_GIB and BKO_BOOT_WIDTH to img-one.sh).
# TMPDIR=/mnt/bko-ram/img-RUN-P is set inside the reservation (its
# sudo -E drops one given to cmp-run.sh), so a BUILD_VM_MODE=snapshot build's
# QEMU file is inside what's reserved; the directory's peak, with what a QEMU
# holds open there unlinked, is sampled every 5 s, as build-holt.sh --reserved
# does.  S, ISO, SL, BKO_WORK, BKO_IMG_DIR, BKO_HOLT_BIN, BKO_BIN, BKO_IMG_COMPRESS, BKO_IMG_RETAIN,
# BKO_DIGEST_CACHE and a round's BKO_PLAN_BKO_BIN and BKO_PLAN_HOLT_BIN (C10)
# pass through to img-one.sh, and BKO_PLAN_BUILDER_P (P's builder identity in
# the plan) goes to P's build as BKO_PLAN_BUILDER; RUN defaults to the time,
# and goes to img-one.sh as BKO_IMG_RUN (the provenance's built.run).
# BKO_LANES_RAM names the RAM disk (default /mnt/bko-ram: tests).  CMP_RUN names cmp-run.sh
# (default: the shared ledger's, in /home/m/backotter-work, whatever BKO_WORK
# says).  A lane that is stopped (TERM, HUP) removes its RAM directory and
# temp files.  Exit 0 when every image's build ran and passed (done.exit 0), 1
# otherwise, 2 on bad usage or when T2_HOST_ROOT is a test's and the images
# directory is the real one.
set -uo pipefail
HERE=$(dirname "$(realpath "$0")")
RUN=$(date -u +%Y%m%dT%H%M)
if [[ ${1:-} == --run ]]; then RUN=$2; shift 2; fi
ok=1
for l in "$@"; do for u in $l; do [[ $u =~ ^(linux|freebsd|netbsd|openbsd|dragonfly|illumos|haiku)(@[0-9a-f]{64})?$ ]] || ok=""; done; done
[[ $# -gt 0 && -n $ok && $RUN =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { sed -n '2,/^set -uo/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2; exit 2; }
W=${BKO_WORK:-/home/m/backotter-work}
I=${BKO_IMG_DIR:-$W/images}
CMP=${CMP_RUN:-/home/m/backotter-work/cmp-run.sh}
RAM=${BKO_LANES_RAM:-/mnt/bko-ram}
same_dir() { local a b; a=$(stat -Lc %d:%i -- "$1" 2>/dev/null) && b=$(stat -Lc %d:%i -- "$2" 2>/dev/null) && [[ $a == "$b" ]]; }
if [[ ${T2_HOST_ROOT:-/bkoscratch/test2} != /bkoscratch/test2 ]] && same_dir "$I" /home/m/backotter-work/images; then
    echo "img-lanes: T2_HOST_ROOT=$T2_HOST_ROOT is a test's, and $I is the real set: set BKO_IMG_DIR to a world's" >&2; exit 2
fi
rm -f "$I/lanes.done"
# 1.1 x the last peak + 0.25 GiB, else the default; and at least the test
# image's size + 0.5 GiB (its copy for the boot tests); rounded up to a tenth.
size() {  # P
    local k b t
    k=$(cat "$I/$1/peak-kib" 2>/dev/null)
    t=$(ls "$I/$1"/test/*.img 2>/dev/null | tail -n 1)
    b=$( [[ -n $t ]] && stat -Lc %s -- "$t" 2>/dev/null); [[ $b =~ ^[0-9]+$ ]] || b=0
    if [[ $k =~ ^[0-9]+$ && $k -gt 0 ]]; then
        awk -v k="$k" -v b="$b" 'BEGIN { d = 1.1 * k / 1048576 + 0.25; i = b / 1073741824 + 0.5; if (b > 0 && i > d) d = i
            c = int(d * 10); if (c < d * 10) c++; printf "%.1f\n", (c < 10 ? 10 : c) / 10 }'
    else
        awk -v d="$([[ $1 == linux ]] && echo 1 || echo 6)" -v b="$b" 'BEGIN { i = b / 1073741824 + 0.5; if (b > 0 && i > d) d = i
            c = int(d * 10); if (c < d * 10) c++; printf "%.1f\n", c / 10 }'
    fi
}
# The boot tests' width: as many modes as MEM holds at 4.5 GiB each (D8).
MEM=10
WIDTH=$(awk -v m="$MEM" 'BEGIN { w = int(m / 4.5); print (w < 1 ? 1 : w) }')
# The lane's image now: what clean removes, once this lane owns it.
tmp="" pf="" sampler=""
clean() {
    [[ -z $sampler ]] || { kill "$sampler" 2>/dev/null; wait "$sampler" 2>/dev/null; sampler=""; }
    [[ -z $pf ]] || rm -f "$pf" "$pf.done"
    [[ -z $tmp ]] || rm -rf --one-file-system "$tmp"
    pf="" tmp=""
}
# (Signals ignored first: a second one must not cut the rm.)
finish() { trap '' TERM INT HUP; clean; exit "$1"; }
one() {  # P or P@SHA256 (a refresh)
    local p=${1%%@*} d t rc rcf env=() me=$BASHPID rf=() bv
    [[ $1 != *@* ]] || rf=(--refresh-from "${1#*@}")
    d=$(size "$p") t=$RAM/img-$RUN-$p
    mountpoint -q "$RAM" || { echo "img-lanes: no RAM disk at $RAM" >&2; return 1; }
    # (No -p, and no rm first: another start of this RUN's directory is never this lane's.)
    mkdir "$t" || { echo "img-lanes: can't make $t: this RUN's $p is running, or was cut short" >&2; return 1; }
    tmp=$t pf=$I/.peak-$p.$$
    for v in S ISO SL BKO_WORK BKO_IMG_DIR BKO_HOLT_BIN BKO_BIN BKO_IMG_COMPRESS BKO_IMG_RETAIN BKO_DIGEST_CACHE \
             BKO_PLAN_BKO_BIN BKO_PLAN_HOLT_BIN; do [[ -z ${!v:-} ]] || env+=("$v=${!v}"); done
    bv=BKO_PLAN_BUILDER_$p; [[ -z ${!bv:-} ]] || env+=("BKO_PLAN_BUILDER=${!bv}")
    ( set +e; m=0
      while [[ ! -e $pf.done ]] && kill -0 "$me" 2>/dev/null; do
          k=$(du -sxk "$tmp" 2>/dev/null | cut -f1); k=${k:-0}
          for f in /proc/[0-9]*/fd/*; do
              [[ $(readlink "$f" 2>/dev/null) == "$tmp/"*" (deleted)" ]] && k=$((k + $(stat -L -c %b "$f" 2>/dev/null || echo 0) / 2))
          done
          ((k > m)) && { m=$k; echo "$m" >"$pf"; }
          sleep 5
      done ) </dev/null >/dev/null 2>&1 &
    sampler=$!
    WEAR_TAG=img:$RUN:$p "$CMP" --reserve "$d" "$MEM" env TMPDIR="$tmp" BKO_BOOT_RAM="$tmp" BKO_BOOT_RAM_GIB="$d" BKO_BOOT_WIDTH="$WIDTH" \
        BKO_IMG_RUN="$RUN" "${env[@]}" "$HERE/img-one.sh" "${rf[@]}" "$p" >"$I/$p.lane.log" 2>&1
    rc=$?
    : >"$pf.done"; wait "$sampler" 2>/dev/null; sampler=""
    # (Written after the build: img-one.sh moves images/P/'s old contents aside at its start.)
    [[ -s $pf ]] && mv -f "$pf" "$I/$p/peak-kib"
    clean
    rcf=$(cat "$I/$p/done.exit" 2>/dev/null)
    [[ $rc -eq 0 && $rcf == 0 ]]
}
lane() {
    local p r=0
    trap 'finish 143' TERM; trap 'finish 130' INT; trap 'finish 129' HUP
    for p in $1; do one "$p" || r=1; done
    return "$r"
}
pids=()
for l in "$@"; do lane "$l" & pids+=($!); done
ex=0
for p in "${pids[@]}"; do wait "$p" || ex=1; done
date -u >"$I/lanes.done"
exit "$ex"
