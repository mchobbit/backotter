#!/bin/bash
# Pin slackware64-current: fetch today's tree once, check it the way
# slackware-bootstrap.sh checks 15.0, and keep it read-only, so every later
# install and restore test of the -current Thinkpad uses this one tree, not
# whatever -current has become.  As root (or a user who can write
# CACHE_DIR), on a machine that mounts the backup drive (the rescue stick, or
# the -current Thinkpad; Slackware 15.0 can't mount mybig512's XFS):
#
#   slackware-pin.sh CACHE_DIR --check PIN
#   slackware-pin.sh CACHE_DIR [--new] [MIRROR]
#
#   CACHE_DIR  slackware-bootstrap.sh's CACHE_DIR, /mnt/bs1-backup/slackware-cache
#   PIN        a pin there, current-DATE (current-20261121)
#   MIRROR     the tree's URL (default: slackware.osuosl.org's, which
#              slackware-bootstrap.sh fetches 15.0 from).  One host for the
#              whole tree: mirrors.slackware.com redirects each file to some
#              mirror or other, at its own sync state.
#
# --check PIN checks a pin again and fetches nothing (no network): its
# CHECKSUMS.md5 by gpgv against the pin's own GPG-KEY (held to
# SLACKWARE_KEY's fingerprint), its SHA-256 against the pin's PINNED.txt and
# against PINS below (slackware-bootstrap.sh's; a pin not there yet is said),
# every file against its MD5 there, each package against its own .asc, the
# ChangeLog's first entry and the file count against PINNED.txt, and that it
# is still read-only.
#
# Without --check it pins the tree the mirror has now.  What is kept: every
# file at the top of the tree and of slackware64/, and every file of the
# series slackware-bootstrap.sh installs (a ap d l n: packages, .asc, .txt,
# tagfiles), as CHECKSUMS.md5 lists them; that is what
# slackware-bootstrap.sh and slackpkg read.  Checked: CHECKSUMS.md5 by gpgv
# against Slackware's key (the mirror's GPG-KEY, held to SLACKWARE_KEY's
# fingerprint), every file against its MD5 there, and each package against
# its own .asc.  It lands in CACHE_DIR/current-DATE, DATE being the day of
# the mirror's ChangeLog's first entry (so -current, which moves daily, makes
# a new pin on most days: a run is not a way to check an old one), with a
# PINNED.txt (that entry's time, CHECKSUMS.md5's SHA-256, the mirror, the
# counts), and is made read-only (chmod -R a-w).  slackware-bootstrap.sh
# takes "current-DATE" as its RELEASE and holds the tree to the SHA-256 it
# has for that pin (a new pin: a new PINS row there, here and in bs1-adopt).
# When CACHE_DIR has a pin already, a new one (2.9 GB) needs --new.  When the
# mirror is still at a pin there, that pin is checked as by --check instead.
# A run cut short leaves CACHE_DIR/current-DATE.part; the next run fetches
# only what is missing there.  Prints BKO-SLACKWARE-PIN-OK and the pin's
# path.

set -eEuo pipefail
trap 'echo "slackware-pin: failed at line $LINENO: $BASH_COMMAND" >&2' ERR
usage="usage: slackware-pin.sh CACHE_DIR --check PIN | slackware-pin.sh CACHE_DIR [--new] [MIRROR]"
CACHE=${1:?$usage}
shift
CHECK= NEW= MIRROR=
while [[ $# -gt 0 ]]; do
    case $1 in
    --check) CHECK=${2:?$usage}; shift 2 ;;
    --new) NEW=1; shift ;;
    -*) echo "slackware-pin: $usage" >&2; exit 1 ;;
    *) [[ -z $MIRROR ]] || { echo "slackware-pin: $usage" >&2; exit 1; }; MIRROR=$1; shift ;;
    esac
done
[[ -z $CHECK || ( -z $NEW && -z $MIRROR ) ]] || { echo "slackware-pin: $usage" >&2; exit 1; }
MIRROR=${MIRROR:-https://slackware.osuosl.org/slackware64-current}
MIRROR=${MIRROR%/}
SLACKWARE_KEY=EC5649DA401E22ABFA6736EF6A4463C040102233
SERIES="a ap d l n"
# slackware-bootstrap.sh's PINS (testvm/realworld/slackware-current.pin):
# each pin's CHECKSUMS.md5, by SHA-256.
declare -A PINS=(
    [current-20261121]=9418aee6f8c5a06d1b051c2d25064d468cc0b19946912fc2898a949ee7fb9917
)

die() { echo "slackware-pin: $*" >&2; exit 1; }
step() { echo "== $(date +%T) $*"; }
tools="gpg gpgv md5sum sha256sum date"
[[ -n $CHECK ]] || tools="$tools curl xargs"
for t in $tools; do command -v $t >/dev/null || die "this system has no $t"; done
[[ -d $CACHE ]] || die "no $CACHE"
CACHE=$(realpath "$CACHE")
tmp=$(mktemp -d)
trap 'gpgconf --homedir "$tmp/gnupg" --kill all 2>/dev/null || true; rm -rf "$tmp"' EXIT
get() { curl -fsSL --retry 3 -o "$2" "$MIRROR/$1"; }
series_re=$(tr ' ' '|' <<<"$SERIES")
# sums_of CHECKSUMS.md5: what the pin holds (path relative to the tree, MD5).
sums_of() {
    awk '$2 ~ /^\.\/[^\/]+$/ || $2 ~ /^\.\/slackware64\/[^\/]+$/ || $2 ~ /^\.\/slackware64\/('"$series_re"')\/[^\/]+$/ { sub(/^\.\//, "", $2); print $1 "  " $2 }' \
        "$1" >"$tmp/sums"
    grep -q '  ChangeLog\.txt$' "$tmp/sums" && grep -q '  GPG-KEY$' "$tmp/sums" || die "$1 lists no ChangeLog.txt or GPG-KEY"
}
# keyring GPG-KEY: Slackware's key out of it, by fingerprint, into $tmp/key.gpg.
keyring() {
    rm -rf "$tmp/gnupg"
    mkdir -m 700 "$tmp/gnupg"
    GNUPGHOME=$tmp/gnupg gpg -q --batch --import "$1" 2>/dev/null || die "$1 isn't a key gpg imports"
    local fpr
    fpr=$(GNUPGHOME=$tmp/gnupg gpg --batch --with-colons --fingerprint --list-keys "$SLACKWARE_KEY" 2>/dev/null) || true
    grep -q "^fpr:*$SLACKWARE_KEY:" <<<"$fpr" || die "$1 is not Slackware's key $SLACKWARE_KEY"
    GNUPGHOME=$tmp/gnupg gpg -q --batch --export "$SLACKWARE_KEY" >"$tmp/key.gpg"
    gpgconf --homedir "$tmp/gnupg" --kill all 2>/dev/null || true
}
# check DIR: every file of the list against its MD5, each package against
# its .asc; prints the ones that fail.
check() {
    (cd "$1" && md5sum --quiet -c "$tmp/sums" 2>/dev/null | sed -n 's/: FAILED.*$//p') || true
    (cd "$1" && awk '$2 ~ /\.txz$/ { print $2 }' "$tmp/sums" | while read -r p; do
        [[ -f $p && -f $p.asc ]] && gpgv --keyring "$tmp/key.gpg" "$p.asc" "$p" 2>/dev/null || echo "$p"
    done)
}
# check_pin DIR: the pin checked offline, from its own files (--check).
check_pin() {
    local pin=$1 name=${1##*/} sha pinned bad
    [[ -d $pin && -s $pin/PINNED.txt && -s $pin/CHECKSUMS.md5 ]] || die "$pin is no pin (slackware-pin.sh's, with a PINNED.txt)"
    step "checking the pin $pin, offline (its own GPG-KEY, CHECKSUMS.md5 and PINNED.txt)"
    keyring "$pin/GPG-KEY"
    gpgv --keyring "$tmp/key.gpg" "$pin/CHECKSUMS.md5.asc" "$pin/CHECKSUMS.md5" 2>/dev/null ||
        die "$pin/CHECKSUMS.md5 isn't signed by Slackware's key"
    sha=$(sha256sum <"$pin/CHECKSUMS.md5" | cut -d' ' -f1)
    pinned=$(sed -n 's/^CHECKSUMS_MD5_SHA256=//p' "$pin/PINNED.txt")
    [[ $sha == "$pinned" ]] || die "$pin/CHECKSUMS.md5 (sha256 $sha) isn't the one its PINNED.txt names ($pinned)"
    if [[ -n ${PINS[$name]:-} ]]; then
        [[ $sha == "${PINS[$name]}" ]] || die "$pin/CHECKSUMS.md5 (sha256 $sha) isn't the one PINS has for $name (${PINS[$name]})"
        echo "CHECKSUMS.md5 sha256 $sha, as PINS and PINNED.txt have it"
    else
        echo "CHECKSUMS.md5 sha256 $sha, as PINNED.txt has it; $name is not in PINS yet (a new pin: add it here, in slackware-bootstrap.sh and in bs1-adopt)"
    fi
    sums_of "$pin/CHECKSUMS.md5"
    [[ $(wc -l <"$tmp/sums") -eq $(sed -n 's/^FILES=//p' "$pin/PINNED.txt") ]] || die "$pin's file list isn't PINNED.txt's FILES"
    [[ $(head -n 1 "$pin/ChangeLog.txt") == "$(sed -n 's/^CHANGELOG_FIRST_ENTRY=//p' "$pin/PINNED.txt")" ]] ||
        die "$pin/ChangeLog.txt doesn't start with PINNED.txt's CHANGELOG_FIRST_ENTRY"
    step "checking $(wc -l <"$tmp/sums") files (MD5 from CHECKSUMS.md5, then each package's .asc)"
    bad=$(check "$pin")
    [[ -z $bad ]] || die "in $pin, these don't check: $(paste -sd ' ' - <<<"$bad")"
    bad=$(find "$pin" -perm -u=w | head -n 3)
    [[ -z $bad ]] || die "$pin isn't read-only (chmod -R a-w): $(paste -sd ' ' - <<<"$bad")"
    cat "$pin/PINNED.txt"
    echo BKO-SLACKWARE-PIN-OK "$pin"
}

if [[ -n $CHECK ]]; then
    [[ $CHECK =~ ^current-[0-9]{8}$ ]] || die "PIN is current-DATE, not \"$CHECK\""
    check_pin "$CACHE/$CHECK"
    exit 0
fi

step "the mirror's ChangeLog, CHECKSUMS.md5 and GPG-KEY ($MIRROR)"
for f in ChangeLog.txt CHECKSUMS.md5 CHECKSUMS.md5.asc GPG-KEY; do get "$f" "$tmp/$f" || die "could not download $MIRROR/$f"; done
keyring "$tmp/GPG-KEY"
gpgv --keyring "$tmp/key.gpg" "$tmp/CHECKSUMS.md5.asc" "$tmp/CHECKSUMS.md5" 2>/dev/null ||
    die "the mirror's CHECKSUMS.md5 is not signed by Slackware's key"
# The ChangeLog's first entry: its time line, the first line of the file.
first=$(head -n 1 "$tmp/ChangeLog.txt")
day=$(date -u -d "$first" +%Y%m%d) || die "the ChangeLog's first line \"$first\" isn't a date"
pin=$CACHE/current-$day
sha=$(sha256sum <"$tmp/CHECKSUMS.md5" | cut -d' ' -f1)
echo "ChangeLog's first entry: $first; CHECKSUMS.md5 sha256 $sha"

if [[ -d $pin ]]; then
    echo "the mirror is still at $pin, pinned already: checking it instead"
    [[ $(sha256sum <"$pin/CHECKSUMS.md5" | cut -d' ' -f1) == "$sha" ]] ||
        echo "(the mirror's CHECKSUMS.md5 has changed since; the pin stays as it is)"
    check_pin "$pin"
    exit 0
fi
part=$pin.part
if [[ -z $NEW && ! -d $part ]]; then
    have=$(cd "$CACHE" && for d in current-*; do [[ -d $d && $d != *.part ]] && echo "$d"; done | paste -sd ' ' -) || true
    [[ -z $have ]] || die "$CACHE has the pins $have already, and the mirror is at a new one, current-$day: --check PIN checks a pin; --new makes this new one (2.9 GB)"
fi
sums_of "$tmp/CHECKSUMS.md5"
nfiles=$(wc -l <"$tmp/sums")

mkdir -p "$part"
cp "$tmp/CHECKSUMS.md5" "$tmp/CHECKSUMS.md5.asc" "$part/"
step "fetching what $part lacks of $nfiles files"
missing=$(cd "$part" && awk '{ print $2 }' "$tmp/sums" | while read -r p; do [[ -f $p ]] || echo "$p"; done)
if [[ -n $missing ]]; then
    echo "downloading $(wc -l <<<"$missing") files from $MIRROR"
    # One file a curl, six at a time; each under its .part name first.
    (cd "$part" && xargs -P 6 -I{} sh -c '
        mkdir -p "$(dirname "$1")" &&
        curl -fsSL --retry 3 -o "$1.part" "$2/$1" && mv "$1.part" "$1"' _ {} "$MIRROR" <<<"$missing") ||
        die "could not download the tree (a mirror mid-sync loses files: run it again later)"
fi
step "checking $nfiles files (MD5 from CHECKSUMS.md5, then each package's .asc)"
bad=$(check "$part")
if [[ -n $bad ]]; then
    # Fetched again, once: a download cut short, or a mirror that synced
    # while this ran (then they fail again: run it later).
    echo "fetching again: $(paste -sd ' ' - <<<"$bad")"
    while read -r p; do
        for f in "$p" $([[ $p == *.txz ]] && echo "$p.asc"); do
            rm -f "$part/$f"; get "$f" "$part/$f" </dev/null || die "could not download $MIRROR/$f"
        done
    done <<<"$bad"
    bad=$(check "$part")
    [[ -z $bad ]] || die "these don't match the signed CHECKSUMS.md5 (the mirror is mid-sync?): $(paste -sd ' ' - <<<"$bad")"
fi
# Anything else in the .part directory (an earlier run's files from an older
# CHECKSUMS.md5) goes.
(cd "$part" && find . -type f ! -name CHECKSUMS.md5 ! -name CHECKSUMS.md5.asc | sed 's|^\./||' | sort) >"$tmp/have"
awk '{ print $2 }' "$tmp/sums" | sort >"$tmp/want"
comm -23 "$tmp/have" "$tmp/want" | while read -r f; do echo "not in this CHECKSUMS.md5, removed: $f"; rm -f "$part/$f"; done
[[ $(head -n 1 "$part/ChangeLog.txt") == "$first" ]] || die "the pinned ChangeLog.txt doesn't start with $first"
bytes=$(du -sb "$part" | cut -f1)
npkg=$(grep -c '\.txz$' "$tmp/sums")
{
    echo "# slackware64-current, pinned by testvm/realworld/slackware-pin.sh; read-only."
    echo "# slackware-bootstrap.sh RELEASE current-$day installs from this tree."
    echo "TREE=slackware64-current"
    echo "PIN=current-$day"
    echo "CHANGELOG_FIRST_ENTRY=$first"
    echo "CHECKSUMS_MD5_SHA256=$sha"
    echo "GPG_KEY_FINGERPRINT=$SLACKWARE_KEY"
    echo "SERIES=$SERIES"
    echo "MIRROR=$MIRROR"
    echo "FETCHED=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "FILES=$nfiles"
    echo "PACKAGES=$npkg"
    echo "BYTES=$bytes"
} >"$part/PINNED.txt"
mv "$part" "$pin"
chmod -R a-w "$pin"
cat "$pin/PINNED.txt"
echo BKO-SLACKWARE-PIN-OK "$pin"
