#!/usr/bin/env bash
# Rescue images, one lane per argument, each lane's images one after another:
#
#   img-lanes.sh [--run RUN] "P P" ["P P" ...]
#
# Each image is img-one.sh (the one beside this script) under its own
# `cmp-run.sh --reserve D 10`, WEAR_TAG=img:RUN:P: D GiB of RAM disk from
# images/P/peak-kib (the last build's peak: 1.1 x it + 0.25 GiB), else 6
# (linux 1).  TMPDIR=/mnt/bko-ram/img-RUN-P is set inside the reservation (its
# sudo -E drops one given to cmp-run.sh), so a BUILD_VM_MODE=snapshot build's
# QEMU file is inside what's reserved; the directory's peak, with what a QEMU
# holds open there unlinked, is sampled every 5 s, as build-holt.sh --reserved
# does.  S, ISO, SL, BKO_WORK, BKO_IMG_DIR, BKO_HOLT_BIN and BKO_BIN pass
# through to img-one.sh; RUN defaults to the time.  CMP_RUN names cmp-run.sh
# (default: the shared ledger's, in /home/m/backotter-work, whatever BKO_WORK
# says).  A lane that is stopped (TERM, HUP) removes its RAM directory and
# temp files.  Exit 0 when every image's build ran and passed (done.exit 0), 1
# otherwise, 2 on bad usage or when T2_HOST_ROOT is a test's and the images
# directory is the real one.
set -uo pipefail
HERE=$(dirname "$(realpath "$0")")
RUN=$(date -u +%Y%m%dT%H%M)
if [[ ${1:-} == --run ]]; then RUN=$2; shift 2; fi
[[ $# -gt 0 && $RUN =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { sed -n '2,/^set -uo/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2; exit 2; }
W=${BKO_WORK:-/home/m/backotter-work}
I=${BKO_IMG_DIR:-$W/images}
CMP=${CMP_RUN:-/home/m/backotter-work/cmp-run.sh}
same_dir() { local a b; a=$(stat -Lc %d:%i -- "$1" 2>/dev/null) && b=$(stat -Lc %d:%i -- "$2" 2>/dev/null) && [[ $a == "$b" ]]; }
if [[ ${T2_HOST_ROOT:-/bkoscratch/test2} != /bkoscratch/test2 ]] && same_dir "$I" /home/m/backotter-work/images; then
    echo "img-lanes: T2_HOST_ROOT=$T2_HOST_ROOT is a test's, and $I is the real set: set BKO_IMG_DIR to a world's" >&2; exit 2
fi
rm -f "$I/lanes.done"
# 1.1 x the last peak + 0.25 GiB, rounded up to a tenth; else the default.
size() {  # P
    local k; k=$(cat "$I/$1/peak-kib" 2>/dev/null)
    if [[ $k =~ ^[0-9]+$ && $k -gt 0 ]]; then
        awk -v k="$k" 'BEGIN { d = 1.1 * k / 1048576 + 0.25; c = int(d * 10); if (c < d * 10) c++; printf "%.1f\n", (c < 10 ? 10 : c) / 10 }'
    elif [[ $1 == linux ]]; then echo 1; else echo 6; fi
}
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
one() {  # P
    local p=$1 d t rc rcf env=() me=$BASHPID
    d=$(size "$p") t=/mnt/bko-ram/img-$RUN-$p
    mountpoint -q /mnt/bko-ram || { echo "img-lanes: no RAM disk at /mnt/bko-ram" >&2; return 1; }
    # (No -p, and no rm first: another start of this RUN's directory is never this lane's.)
    mkdir "$t" || { echo "img-lanes: can't make $t: this RUN's $p is running, or was cut short" >&2; return 1; }
    tmp=$t pf=$I/.peak-$p.$$
    for v in S ISO SL BKO_WORK BKO_IMG_DIR BKO_HOLT_BIN BKO_BIN; do [[ -z ${!v:-} ]] || env+=("$v=${!v}"); done
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
    WEAR_TAG=img:$RUN:$p "$CMP" --reserve "$d" 10 env TMPDIR="$tmp" "${env[@]}" "$HERE/img-one.sh" "$p" >"$I/$p.lane.log" 2>&1
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
