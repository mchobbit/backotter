#!/usr/bin/env bash
# Promote the shared binary sets of ~/backotter-work, bko-bin, cmp-bin and
# holt-bin, from a build host to the other KVM test hosts, each file checked
# there by its sha256.  Run it here, on the Mac, by hand: it changes the sets
# that every host's images (img-one.sh), comparisons (cmp-run.sh) and tests
# take, so no test run starts it.
#
# Usage: promote-bins.sh [--dry-run] [--delete] [--from HOST] [--sets LIST] HOST... | all
#
# HOST: a host of testvm/hosts.json (all: every enabled host but the source).
# --from: the source, a host with the build role there (default chopin, the
# table's first).  --sets: comma-separated, of bko-bin cmp-bin holt-bin
# (default all three).
#
# For each set: on the source, every SHA256SUMS in the set must check
# (sha256sum -c), and a manifest of every file's sha256 (and every symlink's
# target) is made: what the copies are checked by (cmp-bin has no SHA256SUMS
# of its own).  A host whose files already match it is left alone.  Else the
# set goes to HOST:~/backotter-work/.promote-SET.PID by rsync, host to host,
# else through this Mac when the source can't reach the host; it is checked
# there against the manifest; then, holding images/.lock-PLATFORM for each
# PLATFORM/ the set has (img-one.sh's lock: no image build reads a half
# promoted set; one that is held refuses that host's set), it is copied into
# place with rsync --delay-updates (every changed file renamed in at the
# end), and checked again.  The source's files go as they are, BUILD-INFO and
# SHA256SUMS included, so every host ends with the same SHA256SUMS.  Files a
# host has that the source doesn't stay, and are counted, unless --delete
# removes them.  --dry-run reads only: per host and set, what would be copied
# and what is extra.  BKO_WORK_REMOTE names the hosts' directory instead
# (relative to the login's home; default backotter-work).
#
# Exit 0; 1 when a host's set failed (the others still go); 2 bad usage.
#
# Needs: bash 3.2 or newer, ssh, rsync and python3 (testvm/hosts.sh) here;
# on the hosts bash, rsync, sha256sum, flock, find, sort, xargs, readlink.

set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
W=${BKO_WORK_REMOTE:-backotter-work}
ALL_SETS="bko-bin cmp-bin holt-bin"
PLATFORMS="linux freebsd netbsd openbsd dragonfly illumos haiku"
die() { echo "promote-bins: $*" >&2; exit 2; }
log() { echo "$(date '+%H:%M:%S') $*"; }
usage() { sed -n '8p' "$0" | sed 's/^# //' >&2; exit 2; }
# shellcheck source=hosts.sh
. "$here/hosts.sh"

dry=no del=no from="" sets=$ALL_SETS
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run) dry=yes ;;
        --delete) del=yes ;;
        --from) from=${2:-}; shift ;;
        --sets) sets=$(echo "${2:-}" | tr ',' ' '); shift ;;
        -h|--help) usage ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
    shift
done
[[ $# -gt 0 ]] || usage
[[ $W =~ ^[A-Za-z0-9._/-]+$ && $W != /* ]] || die "BKO_WORK_REMOTE: a path relative to the home, not '$W'"
for s in $sets; do [[ " $ALL_SETS " == *" $s "* ]] || die "--sets: of $ALL_SETS, not '$s'"; done
[[ -n ${sets// /} ]] || die "--sets: of $ALL_SETS"

builders=" $(hosts_names --role build | tr '\n' ' ')" || die "no build hosts from testvm/hosts.json"
[[ -n $from ]] || from=$(echo $builders | cut -d' ' -f1)
[[ $builders == *" $from "* ]] || die "--from $from: not a build host in testvm/hosts.json (build hosts:${builders% })"
src=$(hosts_get "$from" ssh) || die "--from $from: no ssh in testvm/hosts.json"

targets=""
if [[ $# -eq 1 && $1 == all ]]; then
    for h in $(hosts_names); do [[ $h == "$from" ]] || targets+=" $h"; done
else
    for h; do
        [[ $h == "$from" ]] && die "$h is the source (--from)"
        hosts_get "$h" ssh >/dev/null || die "no such enabled host $h ($(hosts_phrase))"
        [[ " $targets " == *" $h "* ]] || targets+=" $h"
    done
fi
targets=${targets# }
[[ -n $targets ]] || die "no host to promote to"

# The hosts' side: a script on ssh's stdin, OP W ARGS... as bash -s's arguments
# (M: a manifest, for verify).  Every path is relative to ~/W.
read -r -d '' REMOTE <<'EOF' || true
set -u
op=$1 W=$2; shift 2
cd && cd "$W" 2>/dev/null || { echo "no ~/$W" >&2; exit 3; }
sums() {  # DIR: its manifest, "SHA256  ./PATH" and "link  ./PATH -> TARGET" lines
    [ -d "$1" ] || { echo "no ~/$W/$1" >&2; return 3; }
    (cd "$1" && find . -name '.~tmp~' -prune -o -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum &&
        find . -name '.~tmp~' -prune -o -type l -print | LC_ALL=C sort |
        while IFS= read -r l; do printf 'link  %s -> %s\n' "$l" "$(readlink "$l")"; done)
}
selfcheck() {  # DIR: every SHA256SUMS in it checks
    [ -d "$1" ] || { echo "no ~/$W/$1" >&2; return 3; }
    find "$1/" -name '.~tmp~' -prune -o -type f -name SHA256SUMS -print | {
        bad=0
        while IFS= read -r f; do
            (cd "$(dirname "$f")" && sha256sum -c --quiet --strict SHA256SUMS) || { echo "$f doesn't check" >&2; bad=1; }
        done
        exit $bad
    }
}
verify() {  # DIR: its files are M's
    local files links l p t bad=0
    [ -d "$1" ] || { echo "no ~/$W/$1" >&2; return 3; }
    files=$(printf '%s\n' "$M" | grep -v '^link  ')
    links=$(printf '%s\n' "$M" | grep '^link  ')
    if [ -n "$files" ]; then
        printf '%s\n' "$files" | (cd "$1" && sha256sum -c --quiet --strict -) || bad=1
    fi
    if [ -n "$links" ]; then
        while IFS= read -r l; do
            l=${l#link  }; p=${l%% -> *}; t=${l#* -> }
            [ "$(readlink "$1/$p")" = "$t" ] || { echo "$1/$p: not a link to $t" >&2; bad=1; }
        done <<LINKS
$links
LINKS
    fi
    return $bad
}
install_set() {  # SET STAGE DELETE: STAGE's files into SET, under the image builds' locks
    local set_=$1 stage=$2 n=3 p
    case $stage in .promote-*) ;; *) echo "bad stage $stage" >&2; return 2 ;; esac
    [ -d "$stage" ] || { echo "no ~/$W/$stage" >&2; return 3; }
    if [ -d images ]; then
        for p in $PLATFORMS; do
            [ -d "$stage/$p" ] || [ -d "$set_/$p" ] || continue
            eval "exec $n>images/.lock-$p" && flock -n $n ||
                { echo "images/.lock-$p is held (an image build of $p is under way): $set_ left as it was" >&2; return 4; }
            n=$((n + 1))
        done
    fi
    mkdir -p "$set_/" &&
        rsync -rlp --checksum --delay-updates $([ "$3" = yes ] && echo --delete-after) "$stage/" "$set_/" &&
        rm -rf "$stage"
}
case $op in
    sums) sums "$1" ;;
    selfcheck) selfcheck "$1" ;;
    verify) verify "$1" ;;
    stage) case $1 in .promote-*) rm -rf "$1" && mkdir -p "$1" ;; *) exit 2 ;; esac ;;
    drop) case $1 in .promote-*) rm -rf "$1" ;; *) exit 2 ;; esac ;;
    install) install_set "$1" "$2" "$3" ;;
    *) echo "no such op $op" >&2; exit 2 ;;
esac
EOF

# HOST OP ARGS... [-- MANIFEST]: the hosts' side of OP on HOST (an ssh destination).
remote() {
    local h=$1 op=$2 m="" a=()
    shift 2
    while [[ $# -gt 0 ]]; do
        if [[ $1 == -- ]]; then m=$2; break; fi
        a+=("$1"); shift
    done
    {
        printf 'PLATFORMS="%s"\n' "$PLATFORMS"
        printf "IFS= read -r -d '' M <<'BKO_MANIFEST_END' || true\n%s\nBKO_MANIFEST_END\n" "$m"
        printf '%s\n' "$REMOTE"
    } | ssh -o BatchMode=yes -o ServerAliveInterval=60 "$h" "bash -s -- $op $W ${a[*]+${a[*]}}"
}
paths() { sed -E 's/^link  //; s/^[0-9a-f]{64}  //; s/ -> .*$//'; }
some() {  # the first lines of stdin, on one line
    awk -v n="${1:-8}" 'NR <= n { printf "%s%s", (NR > 1 ? ", " : ""), $0 } END { if (NR > n) printf ", ... (%d in all)", NR }'
}

dssh="ssh -o BatchMode=yes -o ConnectTimeout=8"
tmp=""
cleanup() { [[ -z $tmp ]] || rm -rf "$tmp"; }
trap cleanup EXIT
failed=""
log "promoting ${sets// /, } from $from ($src:$W) to $targets$([[ $dry == yes ]] && echo " (dry run: nothing is written)")"
for set in $sets; do
    if ! remote "$src" selfcheck "$set"; then
        log "$set: FAILED: a SHA256SUMS in $from:$W/$set doesn't check (or it has no $set): promoted nowhere"
        failed+=" $set@$from"
        continue
    fi
    man=$(remote "$src" sums "$set") && [[ -n $man ]] ||
        { log "$set: FAILED: no manifest of $from:$W/$set"; failed+=" $set@$from"; continue; }
    nfiles=$(printf '%s\n' "$man" | wc -l | tr -d ' ')
    relayed=""
    for h in $targets; do
        t=$(hosts_get "$h" ssh)
        tman=$(remote "$t" sums "$set" 2>/dev/null)
        trc=$?
        if [[ $trc != 0 && $trc != 3 ]]; then
            log "$h $set: FAILED: can't read $t:$W/$set (exit $trc)"; failed+=" $set@$h"; continue
        fi
        todo=$(comm -23 <(printf '%s\n' "$man" | LC_ALL=C sort) <(printf '%s\n' "$tman" | LC_ALL=C sort) | grep -v '^$')
        extra=$(comm -13 <(printf '%s\n' "$man" | paths | LC_ALL=C sort -u) \
            <(printf '%s\n' "$tman" | grep -v '^$' | paths | LC_ALL=C sort -u))
        nextra=$(printf '%s' "$extra" | grep -c . || true)
        xnote=""
        [[ $nextra == 0 ]] || xnote="; $nextra there that $from hasn't ($(printf '%s\n' "$extra" | some 5))$(
            [[ $del == yes ]] && echo ", deleted" || echo ", kept")"
        if [[ -z $todo && ( $nextra == 0 || $del == no ) ]]; then
            log "$h $set: up to date ($nfiles files$xnote)"
            continue
        fi
        ntodo=$(printf '%s' "$todo" | grep -c . || true)
        if [[ $dry == yes ]]; then
            log "$h $set: would copy $ntodo of $nfiles files ($(printf '%s\n' "$todo" | paths | some 8))$xnote"
            continue
        fi
        stage=.promote-$set.$$
        if ! remote "$t" stage "$stage"; then
            log "$h $set: FAILED: can't make $t:$W/$stage"; failed+=" $set@$h"; continue
        fi
        # shellcheck disable=SC2029  # the paths are meant to expand here
        if ! ssh -o BatchMode=yes -o ServerAliveInterval=60 "$src" \
            "cd && rsync -rlp -e '$dssh' $W/$set/ $t:$W/$stage/" >/dev/null 2>&1; then
            log "$h $set: $from can't copy to $t: through the Mac"
            if [[ -z $relayed ]]; then
                [[ -n $tmp ]] || tmp=$(mktemp -d "${TMPDIR:-/tmp}/bko-promote.XXXXXX") || die "no temporary directory"
                rsync -rlp -e "$dssh" "$src:$W/$set/" "$tmp/$set/" && relayed=yes || relayed=no
            fi
            if [[ $relayed != yes ]] || ! rsync -rlp -e "$dssh" "$tmp/$set/" "$t:$W/$stage/"; then
                log "$h $set: FAILED to copy"; remote "$t" drop "$stage"; failed+=" $set@$h"; continue
            fi
        fi
        if ! remote "$t" verify "$stage" -- "$man"; then
            log "$h $set: FAILED: the copy in $t:$W/$stage isn't $from's: left as it was"
            remote "$t" drop "$stage"; failed+=" $set@$h"; continue
        fi
        if ! remote "$t" install "$set" "$stage" "$del"; then
            log "$h $set: FAILED to put it in place"; remote "$t" drop "$stage"; failed+=" $set@$h"; continue
        fi
        if ! remote "$t" verify "$set" -- "$man"; then
            log "$h $set: FAILED: $t:$W/$set isn't $from's after the copy"; failed+=" $set@$h"; continue
        fi
        log "$h $set: $ntodo of $nfiles files copied from $from, all $nfiles checked by sha256$xnote"
    done
done
[[ -z $failed ]] || { log "FAILED:$failed"; exit 1; }
log "done"
