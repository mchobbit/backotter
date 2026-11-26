#!/usr/bin/env bash
# The D6 retention rule (docs/design/img-speed.md §7, D6; the user's standing
# rule of 2026-11-26): each platform's images/P keeps its current image and its
# last two prev-* sets; older prev-STAMP and prev-xSTAMP directories are
# deleted.  The farm keeps images raw (no xz), so without this every round
# would add about 15.3 GB per host.
#
#   img-retain.sh [-n] [-p PLATFORM] [IMAGES_DIR]
#
# IMAGES_DIR defaults to BKO_IMG_DIR, else ~/backotter-work/images.  -p does
# only that platform, else every platform directory there (linux freebsd
# netbsd openbsd dragonfly illumos haiku; never images/publish/).  -n says
# what it would delete and deletes nothing.
#
# Only directories named prev-YYYYMMDD-HHMMSS (img-one.sh's) or
# prev-xYYYYMMDD-HHMMSS (img-xcopy.sh's on DEST) count, ordered by that stamp
# (older hand-made ones, prev-YYYYMMDD-HHMM or prev-YYYYMMDD-WORD, count too, by
# their date and then the rest); anything else beside them is left alone and
# named.  An empty one (img-xcopy
# makes one when DEST had nothing) is removed and not counted.  Nothing in
# images/P itself, its test/ or the two newest sets is ever touched.
#
# images/.lock-P is taken (flock -n) for the platform's deletions, so nothing is
# deleted while a build or a refresh of P runs (it holds that lock, and reads
# its parent under it), nor during img-xcopy's copy here; a platform whose lock
# is held is skipped and named.  img-one.sh, which holds the lock itself, says
# so with BKO_RETAIN_HELD=P.  A deleted image that an ImgTest2 pin still
# hard-links frees its space only when the pin moves.
#
# The last line sums it up.  Exit 0, 1 when a deletion failed or a platform was
# skipped, 2 on bad usage.
set -u
usage="usage: $0 [-n] [-p PLATFORM] [IMAGES_DIR]"
PLATFORMS="linux freebsd netbsd openbsd dragonfly illumos haiku"
dry="" only=""
while getopts np: o; do
    case $o in
        n) dry=1 ;;
        p) only=$OPTARG ;;
        *) echo "$usage" >&2; exit 2 ;;
    esac
done
shift $((OPTIND - 1))
[[ $# -le 1 ]] || { echo "$usage" >&2; exit 2; }
I=${1:-${BKO_IMG_DIR:-$HOME/backotter-work/images}}
[[ -d $I ]] || { echo "img-retain: no such directory: $I" >&2; exit 2; }
I=$(cd "$I" && pwd -P)
if [[ -n $only ]]; then
    [[ " $PLATFORMS " == *" $only "* ]] || { echo "img-retain: -p takes one of: $PLATFORMS" >&2; exit 2; }
    PLATFORMS=$only
fi
KEEP=2
# (--one-file-system where rm and du have it, GNU's: never into a mount left inside a set.)
ofs=(); rm --help 2>&1 | grep -q -- --one-file-system && ofs=(--one-file-system)
bad=0 gone=0 freed=0 notes=()
# one P: P's retention, under its lock.
one() {
    local p=$1 d=$I/$1 x n st keep=() del=() k sets=()
    [[ -d $d && ! -L $d ]] || return 0
    if [[ ${BKO_RETAIN_HELD:-} != "$p" ]]; then
        exec 7>>"$I/.lock-$p" || { notes+=("$p: can't open .lock-$p"); bad=$((bad + 1)); return; }
        if ! flock -n 7; then
            notes+=("$p: skipped, a build or copy of it holds .lock-$p"); bad=$((bad + 1)); exec 7>&-; return
        fi
    fi
    for x in "$d"/prev-*; do
        [[ -e $x || -L $x ]] || continue
        n=${x##*/}
        if [[ -L $x || ! -d $x ]] || ! [[ $n =~ ^prev-x?([0-9]{8}(-[A-Za-z0-9]+)?)$ ]]; then
            notes+=("$p: left alone: $n (not a prev-STAMP set)"); continue
        fi
        st=${BASH_REMATCH[1]}
        if [[ -z $(find "$x" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null) ]]; then
            [[ -n $dry ]] && { echo "$p: would remove the empty $n"; continue; }
            rmdir "$x" 2>/dev/null && echo "$p: removed the empty $n"; continue
        fi
        sets+=("$st $n")
    done
    while read -r _ n; do
        [[ -n $n ]] || continue
        if [[ ${#keep[@]} -lt $KEEP ]]; then keep+=("$n"); else del+=("$n"); fi
    done < <(printf '%s\n' "${sets[@]+"${sets[@]}"}" | LC_ALL=C sort -r -k1,1 -k2,2)
    for n in "${del[@]+"${del[@]}"}"; do
        k=$(du -skx "$d/$n" 2>/dev/null | cut -f1); k=${k:-0}
        if [[ -n $dry ]]; then
            echo "$p: would delete $n ($((k / 1024)) MiB)"; gone=$((gone + 1)); freed=$((freed + k)); continue
        fi
        # (sudo -n only for what a plain rm couldn't: a root-owned leftover of a build.)
        if rm -rf "${ofs[@]+"${ofs[@]}"}" -- "${d:?}/$n" 2>/dev/null || sudo -n rm -rf "${ofs[@]+"${ofs[@]}"}" -- "${d:?}/$n" 2>/dev/null; then
            [[ ! -e $d/$n ]] || { notes+=("$p: $n not deleted"); bad=$((bad + 1)); continue; }
            echo "$p: deleted $n ($((k / 1024)) MiB)"; gone=$((gone + 1)); freed=$((freed + k))
        else
            notes+=("$p: $n not deleted"); bad=$((bad + 1))
        fi
    done
    [[ ${#keep[@]} -eq 0 ]] || echo "$p: kept ${keep[*]}"
    [[ ${BKO_RETAIN_HELD:-} == "$p" ]] || exec 7>&-
}
for p in $PLATFORMS; do one "$p"; done
for n in "${notes[@]+"${notes[@]}"}"; do echo "$n" >&2; done
echo "${dry:+(dry run) }deleted $gone set(s), $((freed / 1024)) MiB$([[ $bad -eq 0 ]] || echo ", $bad problem(s)")"
[[ $bad -eq 0 ]]
