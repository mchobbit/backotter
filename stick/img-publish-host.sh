#!/usr/bin/env bash
# The host half of stick/img-publish.sh (img_publish.py sends it over ssh as
# `bash -s`; docs/design/img-speed.md §7, C8, D7).  On a build host that is not
# Chopin:
#
#   img-publish-host.sh SET INFO P=NAME=SHA256 ...
#
# IMAGES is BKO_IMG_DIR (default BKO_WORK/images, BKO_WORK default
# /home/m/backotter-work), OUT is BKO_PUBLISH_DIR (default IMAGES/publish).
# For each P: under a shared flock of IMAGES/.lock-P (refused while a build of P
# runs; img-one.sh holds it), IMAGES/P/done.exit must be 0, and IMAGES/P/NAME is
# hard-linked (or, across file systems, copied) into OUT/.SET.part/P/ and must
# have SHA256, the image the Mac proved (C4); its provenance.json, bko-bin.sha256
# and bko-holt.commit go along.  Then, under `cmp-run.sh --reserve 0 MEM`
# (BKO_PUBLISH_MEM, default 8 GiB; xz's memory limit is a GiB under it, which
# lowers its thread count to fit), WEAR_TAG=publish:SET:P, it is compressed
# there with xz -9 -T0; the .xz must decompress to SHA256 (C8), and
# P/SHA256SUMS gets both lines ("SHA256  NAME", as compress-images.sh writes
# them).  The raw copy goes, and once every platform is done INFO (the Mac's
# JSON: what was proved, and where) is PUBLISHED.json and OUT/.SET.part becomes
# OUT/SET.  IMAGES/P is only read.
#
# CMP_RUN names cmp-run.sh (default /home/m/backotter-work/cmp-run.sh).  A
# failure removes OUT/.SET.part.  Exit 0 when published, 1 when a step failed,
# 3 when refused (SET already published, or being published, or a build of P
# under way), 2 on bad usage.
set -u
[[ $# -ge 3 ]] || { sed -n '2,/^set -u/p' "$0" 2>/dev/null | sed '$d; s/^# \{0,1\}//' >&2; exit 2; }
SET=$1 INFO=$2; shift 2
I=${BKO_IMG_DIR:-${BKO_WORK:-/home/m/backotter-work}/images}
OUT=${BKO_PUBLISH_DIR:-$I/publish}
[[ $SET =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "img-publish: SET is a name, not '$SET'" >&2; exit 2; }
CMP=${CMP_RUN:-/home/m/backotter-work/cmp-run.sh}
MEM=${BKO_PUBLISH_MEM:-8}
[[ $MEM =~ ^[0-9]+$ && $MEM -ge 2 ]] || { echo "img-publish: BKO_PUBLISH_MEM is whole GiB, 2 or more" >&2; exit 2; }
command -v xz >/dev/null || { echo "img-publish: no xz here" >&2; exit 1; }
[[ -d $I ]] || { echo "img-publish: no images directory $I" >&2; exit 2; }
for e in "$@"; do
    [[ $e =~ ^(linux|freebsd|netbsd|openbsd|dragonfly|illumos|haiku)=([^/=]+\.img)=([0-9a-f]{64})$ ]] ||
        { echo "img-publish: not P=NAME.img=SHA256: $e" >&2; exit 2; }
done
mkdir -p "$OUT" || exit 1
dest=$OUT/$SET part=$OUT/.$SET.part
exec 6>>"$OUT/.lock-$SET" || exit 1
flock -n 6 || { echo "img-publish: $SET is being published ($OUT/.lock-$SET is held)" >&2; exit 3; }
[[ ! -e $dest ]] || { echo "img-publish: $dest is there already: a set is published once" >&2; exit 3; }
rm -rf "$part"; mkdir "$part" || exit 1
# (Signals ignored first: a second one must not cut the cleanup.)
gone() { trap '' TERM INT HUP; rm -rf "$part"; exit "$1"; }
trap 'gone 143' TERM; trap 'gone 130' INT; trap 'gone 129' HUP
fail() { echo "img-publish: $*" >&2; gone 1; }
sum() { sha256sum | cut -d' ' -f1; }
for e in "$@"; do
    IFS== read -r p name want <<<"$e"
    o=$part/$p; mkdir "$o" || fail "can't make $o"
    exec 7>>"$I/.lock-$p" || fail "can't open $I/.lock-$p"
    if ! flock -n -s 7; then
        echo "img-publish: $p: a build of it is under way ($I/.lock-$p is held)" >&2; gone 3
    fi
    [[ $(cat "$I/$p/done.exit" 2>/dev/null) == 0 ]] || fail "$p: $I/$p/done.exit is not 0"
    ln "$I/$p/$name" "$o/$name" 2>/dev/null || cp --reflink=auto "$I/$p/$name" "$o/$name" ||
        fail "$p: can't copy $I/$p/$name"
    for f in provenance.json bko-bin.sha256 bko-holt.commit; do
        [[ ! -f $I/$p/$f ]] || cp "$I/$p/$f" "$o/$f" || fail "$p: can't copy $f"
    done
    exec 7>&-
    got=$(sum <"$o/$name")
    [[ $got == "$want" ]] || fail "$p: $I/$p/$name is ${got:0:12}, not the proven ${want:0:12}"
    lim=$((MEM - 1))
    WEAR_TAG=publish:$SET:$p "$CMP" --reserve 0 "$MEM" sh -c \
        'nice -n 10 xz -9 -T0 --memlimit-compress="$2"GiB -c "$1" >"$1.xz.part"' sh "$o/$name" "$lim" ||
        fail "$p: xz failed"
    xs=$(nice -n 10 xz -dc "$o/$name.xz.part" | sum)
    [[ $xs == "$want" ]] || fail "$p: $name.xz decompresses to ${xs:0:12}, the raw image is ${want:0:12} (C8)"
    touch -r "$o/$name" "$o/$name.xz.part"
    mv "$o/$name.xz.part" "$o/$name.xz" || fail "$p: can't rename its .xz"
    { echo "$want  $name"; echo "$(sum <"$o/$name.xz")  $name.xz"; } | LC_ALL=C sort -k2 >"$o/SHA256SUMS" ||
        fail "$p: can't write SHA256SUMS"
    rm -f "$o/$name"
    sz=$(wc -c <"$o/$name.xz" | tr -d ' ')
    echo "$p: $name.xz ($((${sz:-0} / 1048576)) MiB), decompresses to ${want:0:12}"
done
printf '%s\n' "$INFO" >"$part/PUBLISHED.json" || fail "can't write PUBLISHED.json"
mv "$part" "$dest" || fail "can't rename $part to $dest"
trap - TERM INT HUP
echo "published $SET: $dest ($# platform(s))"
