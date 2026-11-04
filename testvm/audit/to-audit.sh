#!/usr/bin/env bash
# What of the BackOtter forks no audit has covered yet: for each fork in
# testvm/audit/audited-heads, the commits from its last audited commit to its
# branch's head now (BKO_GIT/NAME, ~/git/NAME), with their diffstat, so the
# next audit reads only those.  After that audit, --mark moves the lines on.
#
# Usage, on the Mac:
#   to-audit.sh [NAME ...]
#       print, per fork, AUDITED..HEAD: the commits and the diffstat
#   to-audit.sh --mark [--what TEXT] [NAME[=REV] ...]
#       set NAME's line (default: every fork's) to REV (default: the
#       branch's head now), today's date and TEXT (default "audit"); commit
#       audited-heads after it
#
# A fork whose audited commit is no longer on its branch (rebased onto a new
# upstream release) gets the git range-diff to read instead, the old fork
# commits against the new ones, and the upstream range the rebase took in.
#
# Needs: git, and the forks' checkouts in BKO_GIT (default ~/git).
#
# Examples:
#   to-audit.sh
#   to-audit.sh restic
#   to-audit.sh --mark --what "fork-harm audit 2" restic rustic_core=6e8b009

set -eu

here=$(cd "$(dirname "$0")" && pwd)
heads=$here/audited-heads
BKO_GIT=${BKO_GIT:-$HOME/git}
die() { echo "to-audit: $*" >&2; exit 1; }
[[ -f $heads ]] || die "no $heads"

mark=0 what=audit
while [[ $# -gt 0 ]]; do
    case $1 in
        --mark) mark=1; shift ;;
        --what) what=${2:?}; shift 2 ;;
        -*) die "usage: to-audit.sh [NAME ...] | to-audit.sh --mark [--what TEXT] [NAME[=REV] ...]" ;;
        *) break ;;
    esac
done

# NAME[=REV] arguments: selected[NAME]=REV ("" for the branch head).
sel=" "
for a in "$@"; do
    n=${a%%=*}
    grep -q "^$n " "$heads" || die "no $n in $heads"
    sel+="$a "
done
selected() {  # NAME -> 0 and REV on stdout if selected
    [[ $sel == " " ]] && return 0
    local a
    for a in $sel; do
        [[ ${a%%=*} == "$1" ]] || continue
        [[ $a == *=* ]] && echo "${a#*=}"
        return 0
    done
    return 1
}

if [[ $mark == 1 ]]; then
    today=$(date +%F) tmp=$heads.new
    : >"$tmp"
    while IFS= read -r line; do
        read -r n branch commit _ <<<"$line"
        if [[ $line == \#* || -z $line ]] || ! rev=$(selected "$n"); then
            echo "$line" >>"$tmp"; continue
        fi
        g=$BKO_GIT/$n
        new=$(git -C "$g" rev-parse --verify "${rev:-$branch}^{commit}") || { rm -f "$tmp"; die "$n: no ${rev:-$branch}"; }
        echo "$n $branch $new $today $what" >>"$tmp"
        echo "$n: ${commit:0:12} -> ${new:0:12} ($what)"
    done <"$heads"
    mv "$tmp" "$heads"
    exit 0
fi

while read -r n branch commit date what; do
    [[ $n == \#* || -z $n ]] && continue
    selected "$n" >/dev/null || continue
    g=$BKO_GIT/$n
    head=$(git -C "$g" rev-parse --verify "$branch^{commit}") || die "$g has no branch $branch"
    echo "== $n ($branch): audited ${commit:0:12} on $date by $what; head ${head:0:12}"
    if ! git -C "$g" cat-file -e "$commit^{commit}" 2>/dev/null; then
        echo "   $g doesn't have the audited commit $commit (fetch origin)"
    elif [[ $commit == "$head" ]]; then
        echo "   nothing new to audit"
    elif git -C "$g" merge-base --is-ancestor "$commit" "$head"; then
        echo "   range: ${commit:0:12}..${head:0:12}, $(git -C "$g" rev-list --count "$commit..$head") commits"
        git -C "$g" log --no-decorate --format='   %h %ad %s' --date=short "$commit..$head"
        git -C "$g" diff --stat=100 "$commit" "$head" | sed 's/^/   /'
    else
        # Rebased: the fork's commits on the old base against the new ones.
        up=$(git -C "$g" for-each-ref --format='%(refname:short)' refs/remotes/upstream | grep -E '/(master|main)$' | head -1)
        [[ -n $up ]] || up=upstream/master
        ob=$(git -C "$g" merge-base "$commit" "$up" 2>/dev/null) || ob=
        nb=$(git -C "$g" merge-base "$head" "$up" 2>/dev/null) || nb=
        echo "   ${commit:0:12} isn't on $branch any more (rebased?)."
        if [[ -n $ob && -n $nb ]]; then
            echo "   upstream taken in: ${ob:0:12}..${nb:0:12}, $(git -C "$g" rev-list --count "$ob..$nb") commits"
            echo "   fork commits, old against new: git -C $g range-diff ${ob:0:12}..${commit:0:12} ${nb:0:12}..${head:0:12}"
            git -C "$g" range-diff --no-color "$ob..$commit" "$nb..$head" | grep -v '^ *[0-9-]*: *[0-9a-f]* = ' | sed 's/^/   /' | head -200
        else
            echo "   no $up merge-bases: compare by hand"
        fi
    fi
    echo
done <"$heads"
