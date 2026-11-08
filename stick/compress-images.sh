#!/usr/bin/env bash
# Compress the rescue images under an images directory (as img-one.sh leaves
# them: PLATFORM/*.img, PLATFORM/test/*.img, PLATFORM/prev-*/).
#
# Usage: compress-images.sh [-T THREADS] [-p PLATFORM] [IMAGES_DIR]
#
# IMAGES_DIR defaults to ~/backotter-work/images.  -p does only that platform.
# THREADS is xz's -T: by default 4 (about 5 GB of memory; several builds may
# run this at once), or 2 on Chopin, whose cooler is weak.
#
# img-one.sh runs this for its platform after a build passes its boot tests,
# and xcopy.sh on the host it copied to.
#
#   PLATFORM/*.img            the current images, for download: X.img.xz is
#                             made beside X.img, which stays (QEMU, boot-test.py
#                             and writing a stick from the host want it raw)
#   PLATFORM/test/*.img       left alone: only boot-test.py uses them
#   PLATFORM/prev-*/**/*.img  old images, kept for reference: replaced by
#                             X.img.xz
#
# A raw image is deleted only once X.img.xz, decompressed, has its SHA256.  An
# X.img.xz already there (made elsewhere, or carried along when img-one.sh
# moved the current image into prev-*) is checked the same way and not made
# again, and one that doesn't match stops the run.  Run it again at any time:
# what is already compressed is left as it is.
#
# Each directory's SHA256SUMS gets both checksums.  The raw image's identifies
# it, since xz's output varies with its version and thread count.  Where the
# raw image is gone, check with: sha256sum -c --ignore-missing SHA256SUMS
#
# Needs: xz, sha256sum.

set -euo pipefail
die() { echo "compress-images: $*" >&2; exit 1; }

usage="usage: $0 [-T THREADS] [-p PLATFORM] [IMAGES_DIR]"
threads=4
[[ $(hostname -s) != chopin ]] || threads=2
platform=
while getopts T:p: o; do
    case $o in
        T) threads=$OPTARG ;;
        p) platform=$OPTARG ;;
        *) die "$usage" ;;
    esac
done
shift $((OPTIND - 1))
[[ $# -le 1 ]] || die "$usage"
dir=${1:-$HOME/backotter-work/images}
# depth: how far below dir a platform's current images are.
depth=2
if [[ -n $platform ]]; then dir=$dir/$platform depth=1; fi
[[ -d $dir ]] || die "no such directory: $dir"
command -v xz >/dev/null || die "xz is missing"
export LC_ALL=C

sum() { sha256sum | cut -d' ' -f1; }

# record DIR NAME SHA256: NAME's line in DIR/SHA256SUMS.
record() {
    local f=$1/SHA256SUMS
    { [[ ! -f $f ]] || awk -v n="$2" '$2 != n' "$f"; echo "$3  $2"; } | sort -k2 >"$f.new"
    mv "$f.new" "$f"
}

# compress IMG: IMG.xz made if missing, then checked against IMG, and both
# checksums recorded.
compress() {
    local img=$1 xz=$1.xz d=${1%/*} n=${1##*/} want got
    if command -v fuser >/dev/null && fuser -s "$img" 2>/dev/null; then
        echo "skipping $img: in use" >&2
        return 1
    fi
    want=$(sum <"$img")
    if [[ ! -s $xz ]]; then
        echo "compressing $img"
        nice -n 10 xz -9 -T"$threads" -c "$img" >"$xz.part" || die "xz failed on $img"
        touch -r "$img" "$xz.part"
        mv "$xz.part" "$xz"
    fi
    got=$(nice -n 10 xz -dc -T"$threads" "$xz" | sum)
    [[ $got == "$want" ]] || die "$xz decompresses to $got, but $img is $want"
    record "$d" "$n" "$want"
    record "$d" "$n.xz" "$(sum <"$xz")"
}

before=$(du -s --block-size=1M "$dir" | cut -f1)
skipped=0
# Current images: the raw file stays.
while IFS= read -r -d '' img; do
    compress "$img" || skipped=$((skipped + 1))
done < <(find "$dir" -mindepth $depth -maxdepth $depth -type f -name '*.img' -print0 | sort -z)
# Old images: the raw file goes, once its .xz is proved.
while IFS= read -r -d '' img; do
    if compress "$img"; then rm "$img"; else skipped=$((skipped + 1)); fi
done < <(find "$dir" -mindepth $((depth + 1)) -type f -name '*.img' -path '*/prev-*' -print0 | sort -z)
after=$(du -s --block-size=1M "$dir" | cut -f1)
echo "$dir: $before MiB -> $after MiB; $skipped in use and skipped"
[[ $skipped -eq 0 ]]
