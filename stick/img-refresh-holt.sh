#!/usr/bin/env bash
# img-refresh-holt.sh COMMIT CODE [PLATFORM...]: the bko-holt the next images
# are built with.  Copies HoltTest2's builds, T2_HOST_ROOT/holt-bin/CODE
# (bko-holt for linux, PLATFORM/bko-holt for the others), to
# BKO_WORK/holt-bin/PLATFORM/bko-holt by temp name and rename, and writes
# holt-bin/PLATFORM/COMMIT: COMMIT's 40 hex.  Nothing else writes that file.
#
# PLATFORMs default to all seven.  Every source is checked before anything is
# written, against CODE/SHA256SUMS when that has its line.  Refused while a
# build of the platform holds images/.lock-PLATFORM, which this script then
# holds until it ends, and when T2_HOST_ROOT isn't the default and
# BKO_WORK/holt-bin is the real one, under any of its names (a test writing to
# production's set).  COMMIT goes before the new binary lands and comes back
# after it: a refresh cut short leaves no COMMIT, and an image built then is
# refused (img-one.sh writes no bko-holt.commit).  Exit 0, 1 when a copy failed,
# 2 on bad usage or a refusal.
set -uo pipefail
die() { echo "img-refresh-holt: $*" >&2; exit 2; }
ALL="linux freebsd netbsd openbsd dragonfly illumos haiku"
[[ $# -ge 2 ]] || die "usage: img-refresh-holt.sh COMMIT CODE [PLATFORM...]"
COMMIT=$1 CODE=$2; shift 2
[[ $COMMIT =~ ^[0-9a-f]{40}$ ]] || die "COMMIT: 40 hex, not '$COMMIT'"
[[ $CODE =~ ^[0-9a-f]{64}$ ]] || die "CODE: 64 hex, not '$CODE'"
W=${BKO_WORK:-/home/m/backotter-work}
HR=${T2_HOST_ROOT:-/bkoscratch/test2}
I=${BKO_IMG_DIR:-$W/images}
same_dir() { local a b; a=$(stat -Lc %d:%i -- "$1" 2>/dev/null) && b=$(stat -Lc %d:%i -- "$2" 2>/dev/null) && [[ $a == "$b" ]]; }
if [[ ${T2_HOST_ROOT:-/bkoscratch/test2} != /bkoscratch/test2 ]] && same_dir "$W/holt-bin" /home/m/backotter-work/holt-bin; then
    die "T2_HOST_ROOT=$T2_HOST_ROOT is a test's, and $W/holt-bin is the real set: set BKO_WORK to a world"
fi
SRC=$HR/holt-bin/$CODE
[[ -d $SRC ]] || die "no $SRC"
[[ $# -gt 0 ]] || set -- $ALL
rel() { if [[ $1 == linux ]]; then echo bko-holt; else echo "$1/bko-holt"; fi; }
for p; do
    [[ " $ALL " == *" $p "* ]] || die "PLATFORM: one of $ALL, not '$p'"
    f=$SRC/$(rel "$p")
    [[ -f $f ]] || die "no $f"
    want=$(awk -v f="$(rel "$p")" '{ n = $2; sub(/^\*/, "", n) } n == f { print $1; exit }' "$SRC/SHA256SUMS" 2>/dev/null)
    if [[ -n $want ]]; then
        got=$(sha256sum "$f" | cut -d' ' -f1)
        [[ $got == "$want" ]] || die "$f is ${got:0:12}, its SHA256SUMS says ${want:0:12}"
    fi
    if [[ -d $I ]]; then   # (held to the end of this script: no build starts under the copy)
        exec {lk}>>"$I/.lock-$p" || die "can't open $I/.lock-$p"
        flock -n "$lk" || die "a build of $p is under way ($I/.lock-$p is held)"
    fi
done
ex=0
for p; do
    d=$W/holt-bin/$p
    if mkdir -p "$d" && cp "$SRC/$(rel "$p")" "$d/.bko-holt.tmp.$$" && chmod 755 "$d/.bko-holt.tmp.$$" &&
       echo "$COMMIT" >"$d/.COMMIT.tmp.$$" && rm -f "$d/COMMIT" &&
       mv -f "$d/.bko-holt.tmp.$$" "$d/bko-holt" && mv -f "$d/.COMMIT.tmp.$$" "$d/COMMIT"; then
        echo "$p: bko-holt of $(cut -c1-12 <<<"$CODE"), commit ${COMMIT:0:7}"
    else
        rm -f "$d/.bko-holt.tmp.$$" "$d/.COMMIT.tmp.$$"
        echo "img-refresh-holt: $p: not refreshed" >&2; ex=1
    fi
done
exit "$ex"
