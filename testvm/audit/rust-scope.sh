#!/usr/bin/env bash
# The lines of Rust (and of the other files) that are BackOtter's own, for
# RustAudit1 (docs/design/rust-audit.md, RA0): every tool's output is
# filtered to these lines (rust-filter.py) before anyone reads it, and the
# reviewers read these and not upstream's.
#
# Usage, on the Mac:
#   rust-scope.sh [--out DIR] [NAME[=REV] ...]
#
# NAME is rustic_core, rustic or holt (default: all three).  REV defaults to
# the forks' backotter branch (BKO_GIT/NAME, ~/git/NAME) and to main for holt
# (this checkout's, committed lines only).  A line is ours:
#   rustic_core, rustic  in a file the diff from the stock tag (audit-lib.sh's
#       STOCK) to REV touches, when `git blame -w -M -C --line-porcelain REV`
#       gives it to a commit whose subject starts "backotter:" (so the
#       upstream fixes cherry-picked onto the fork are not ours, nor
#       upstream lines we only moved or re-indented); and a line the diff
#       adds (git diff -w) that plain `git blame -w` gives to a "backotter:"
#       commit, unless a line the diff removes reads the same (whitespace
#       aside: one we moved), since -M -C gives a line we changed to an
#       identical upstream line nearby (modification.rs's set_atime default,
#       ForkAudit1 F3-05); a file added since the stock tag by a "backotter:"
#       commit is ours whole (one added by any other commit is blamed like
#       the rest)
#   vendor/walkdir (rustic_core)  the diff from the crates.io release it
#       vendors (its Cargo.toml's version; the .crate fetched from
#       static.crates.io into a fresh directory and checked against stock
#       rustic's Cargo.lock checksum), not from our import commit: new-side
#       lines of every hunk, files the release lacks whole.  The file's
#       comments also say whether the import commit itself was the release
#       plus marked hunks only (files dropped, hunks without a "BackOtter"
#       near them)
#   holt  every .rs file under holt/, whole; paths relative to holt/ (cargo's
#       workspace root there)
#
# Writes DIR/scope-NAME.txt (default DIR: testvm/audit/rust/, which is
# committed; rust-lint.sh writes its own into its run's directory when the
# committed one is of another commit): header comments (# name= commit=
# stock= date=), then one "PATH START-END" per range of our lines, paths
# relative to the repository root (the workspace root cargo prints them
# against), then "# lines OURS TOTAL HOW PATH" per file and a "# total" line.
#
# Needs: git, curl, shasum or sha256sum, tar; the forks' checkouts in BKO_GIT.
#
# Examples:
#   rust-scope.sh
#   rust-scope.sh rustic_core=695d676
#   rust-scope.sh --out /tmp/scope holt=HEAD

set -euo pipefail
source "$(dirname "$0")/audit-lib.sh"

out=$here/rust
while [[ $# -gt 0 ]]; do
    case $1 in
        --out) out=${2:?}; shift 2 ;;
        -h | --help) sed -n '2,/^$/s/^# \{0,1\}//p' "$0"; exit 0 ;;
        -*) die "usage: rust-scope.sh [--out DIR] [NAME[=REV] ...]" ;;
        *) break ;;
    esac
done
[[ $# -gt 0 ]] || set -- rustic_core rustic holt
mkdir -p "$out"
JOBS=${JOBS:-8}

# ranges: line numbers (one per line, any order) to "START-END" lines.
ranges() {
    sort -n -u | awk 'NR == 1 { s = e = $1; next }
        $1 == e + 1 { e = $1; next }
        { print s "-" e; s = e = $1 }
        END { if (NR) print s "-" e }'
}
# nlines: lines of stdin, a last one without a newline counted.
nlines() { awk 'END { print NR }'; }
sha256() { if command -v sha256sum >/dev/null; then sha256sum | cut -d' ' -f1; else shasum -a 256 | cut -d' ' -f1; fi; }

# The crates.io release DIR (fetched once, into a fresh directory, and
# checked): crate_src NAME VERSION SHA256 -> prints the extracted directory.
crate_src() {
    local n=$1 v=$2 want=$3 d
    d=$LOCAL/rust/crates/$n-$v
    if [[ ! -f $d/.bko-ok ]]; then
        rm -rf "$d" && mkdir -p "$d/x" || die "can't make $d"
        curl -sSfL --retry 2 -o "$d/$n-$v.crate" "https://static.crates.io/crates/$n/$n-$v.crate" ||
            die "can't fetch $n-$v.crate"
        [[ $(sha256 <"$d/$n-$v.crate") == "$want" ]] || die "$n-$v.crate: sha256 isn't $want"
        tar -xzf "$d/$n-$v.crate" -C "$d/x" || die "can't unpack $n-$v.crate"
        echo "$want" >"$d/.bko-ok"
    fi
    echo "$d/x/$n-$v"
}

# hunks_new OLD NEW [CONTEXT]: "START COUNT" of the new side of each diff
# hunk (-U0 by default: the changed lines alone).
hunks_new() {
    diff -U"${3:-0}" "$1" "$2" | awk '/^@@ / {
        split($3, a, ","); s = substr(a[1], 2); c = (2 in a) ? a[2] : 1
        print s, c }' || true
}

scope_holt() {
    local rev=$1 c f n
    c=$(git -C "$repo" rev-parse --verify "$rev^{commit}") || die "no holt $rev"
    {
        echo "# rust-scope.sh: the lines of holt (bko-holt) that are BackOtter's own: all of them (RustAudit1, RA0)"
        echo "# name=holt commit=$c stock=none date=$(date '+%F %T %z')"
        echo "# rule: every .rs file under holt/ at $c, whole; paths relative to holt/"
        echo "# PATH START-END"
    } >"$tmp/head"
    : >"$tmp/body"; : >"$tmp/sum"
    git -C "$repo" ls-tree -r --name-only "$c" -- holt/ | grep '\.rs$' | while IFS= read -r f; do
        n=$(git -C "$repo" cat-file -p "$c:$f" | nlines)
        [[ $n -gt 0 ]] || continue
        echo "${f#holt/} 1-$n" >>"$tmp/body"
        printf '# lines %6d %6d whole %s\n' "$n" "$n" "${f#holt/}" >>"$tmp/sum"
    done
    finish holt
}

# finish NAME: DIR/scope-NAME.txt from tmp's head, body, sum (and walkdir).
finish() {
    local f=$out/scope-$1.txt
    {
        cat "$tmp/head"
        cat "$tmp/body"
        echo "# per file: lines OURS TOTAL HOW PATH (HOW: added = a file a backotter: commit added, whole;"
        echo "#   blame = git blame; walkdir = the diff from crates.io; whole = holt)"
        cat "$tmp/sum"
        awk '{ o += $3; t += $4; n++; if ($6 ~ /\.rs$/) { ro += $3; rt += $4; rn++ } }
            END { printf "# total: %d of %d lines ours in %d files; .rs: %d of %d in %d files\n", o, t, n, ro, rt, rn }' "$tmp/sum"
        [[ ! -s $tmp/walkdir ]] || cat "$tmp/walkdir"
    } >"$f.part"
    mv "$f.part" "$f"
    log "$f: $(sed -n 's/^# total: //p' "$f")"
}

scope_fork() {
    local name=$1 rev=$2 g=$BKO_GIT/$1 c stock tag st path how n first subj
    c=$(git -C "$g" rev-parse --verify "$rev^{commit}") || die "no $name $rev"
    stock=$(stock_field "$name" 3)
    # The stock commit's own tag (rustic_core's carries its sibling crates' too).
    tag=$(git -C "$g" tag --points-at "$stock" | grep -E "^($name-)?v[0-9]" | head -1 || true)
    [[ -n $tag ]] || tag=$(stock_field "$name" 2)
    git -C "$g" merge-base --is-ancestor "$stock" "$c" ||
        echo "warning: $name ${c:0:12} isn't based on stock $tag ($stock)" >&2
    {
        echo "# rust-scope.sh: the lines of $name that are BackOtter's own (RustAudit1, RA0)"
        echo "# name=$name commit=$c stock=$tag $stock date=$(date '+%F %T %z')"
        echo "# rule: in files stock..commit touches, lines git blame -w -M -C gives to a commit whose subject"
        echo "#   starts \"backotter:\", and lines the diff adds that plain git blame -w gives to one, but for those"
        echo "#   that read as a line the diff removes; files a backotter: commit added, whole; vendor/walkdir: the"
        echo "#   diff from crates.io"
        echo "# upstream commits in stock..commit (not ours): $(git -C "$g" log --format=%s "$stock..$c" | grep -vc '^backotter:' || true)"
        echo "# PATH START-END, relative to the repository root"
    } >"$tmp/head"
    : >"$tmp/body"; : >"$tmp/sum"; : >"$tmp/walkdir"; : >"$tmp/blame-list"
    mkdir -p "$tmp/blame"
    while IFS=$'\t' read -r st path p2; do
        [[ -n ${p2:-} ]] && path=$p2   # renames and copies: the new path
        case $st in D*) continue ;; esac
        [[ $path == vendor/walkdir/* ]] && continue
        git -C "$g" cat-file -p "$c:$path" >"$tmp/blob"
        LC_ALL=C grep -qI '' "$tmp/blob" || continue   # binary or empty
        how=blame
        if [[ $st == A ]]; then
            subj=$(git -C "$g" log --diff-filter=A --format=%s "$stock..$c" -- "$path" | tail -1)
            [[ $subj == backotter:* ]] && how=added
        fi
        if [[ $how == added ]]; then
            n=$(nlines <"$tmp/blob")
            echo "$path 1-$n" >>"$tmp/body"
            printf '# lines %6d %6d added %s\n' "$n" "$n" "$path" >>"$tmp/sum"
        else
            echo "$path" >>"$tmp/blame-list"
        fi
    done < <(git -C "$g" diff --name-status -M "$stock" "$c")

    # What the diff removes, whitespace aside (a line we moved reads so).
    git -C "$g" diff -w -U0 --no-color -M "$stock" "$c" -- . ':!vendor/walkdir' |
        awk '/^diff --git / { h = 0; next } /^@@ / { h = 1; next }
            h && /^-/ { l = substr($0, 2); gsub(/[ \t\r]/, "", l); print l }' | sort -u >"$tmp/removed"
    # Blame in parallel, one porcelain file per path (/ as %), with -M -C
    # and plain (.plain), and the diff's added lines (.plus: "LINE").
    tr '\n' '\0' <"$tmp/blame-list" | xargs -0 -n 1 -P "$JOBS" sh -c \
        'b="$3/$(printf %s "$5" | tr / %)"
        git -C "$1" blame -w -M -C --line-porcelain "$2" -- "$5" >"$b" &&
        git -C "$1" blame -w --line-porcelain "$2" -- "$5" >"$b.plain" &&
        git -C "$1" diff -w -U0 --no-color "$4" "$2" -- "$5" | awk "/^@@ / {
            split(\$3, a, \",\"); s = substr(a[1], 2); c = (2 in a) ? a[2] : 1
            for (i = 0; i < c; i++) print s + i }" >"$b.plus"' \
        _ "$g" "$c" "$tmp/blame" "$stock" 2>"$tmp/blame.err" || die "git blame failed: $(head -3 "$tmp/blame.err")"
    while IFS= read -r path; do
        local bf=$tmp/blame/$(printf %s "$path" | tr / %)
        # Each line: a "SHA ORIG FINAL [N]" header, its fields (summary
        # among them), then the line itself after a tab.
        awk 'length($1) == 40 && $1 ~ /^[0-9a-f]+$/ && $3 ~ /^[0-9]+$/ { ln = $3; next }
            /^summary / { s = substr($0, 9); next }
            /^\t/ { total++; if (s ~ /^backotter:/) print ln }
            END { print "total", total > "/dev/stderr" }' "$bf" 2>"$bf.total" >"$bf.lines"
        # The diff's added lines plain blame gives to us, but for moved ones.
        awk 'FILENAME == ARGV[1] { gone[$0] = 1; next } FILENAME == ARGV[2] { plus[$1] = 1; next }
            length($1) == 40 && $1 ~ /^[0-9a-f]+$/ && $3 ~ /^[0-9]+$/ { ln = $3; next }
            /^summary / { s = substr($0, 9); next }
            /^\t/ { l = substr($0, 2); gsub(/[ \t\r]/, "", l)
                if ((ln in plus) && s ~ /^backotter:/ && !(l in gone)) print ln }' \
            "$tmp/removed" "$bf.plus" "$bf.plain" >>"$bf.lines"
        ranges <"$bf.lines" >"$bf.ranges"
        n=$(awk '{ split($1, a, "-"); s += a[2] - a[1] + 1 } END { print s + 0 }' "$bf.ranges")
        sed "s|^|$path |" "$bf.ranges" >>"$tmp/body"
        printf '# lines %6d %6d blame %s\n' "$n" "$(awk '{ print $2 }' "$bf.total")" "$path" >>"$tmp/sum"
    done <"$tmp/blame-list"

    [[ $name == rustic_core ]] && scope_walkdir "$g" "$c"
    finish "$name"
}

# The vendored walkdir: our lines are the diff from the crates.io release.
scope_walkdir() {
    local g=$1 c=$2 v sum src imp head f n h s cnt unmarked
    git -C "$g" cat-file -e "$c:vendor/walkdir/Cargo.toml" 2>/dev/null || return 0
    v=$(git -C "$g" cat-file -p "$c:vendor/walkdir/Cargo.toml" | awk -F'"' '/^version *=/ { print $2; exit }')
    sum=$(git -C "$BKO_GIT/rustic" show "$(stock_field rustic 3):Cargo.lock" |
        awk -v v="$v" '$0 == "name = \"walkdir\"" { f = 1; next } f && $0 == "version = \"" v "\"" { g = 1; next }
            f && g && /^checksum/ { gsub(/"/, "", $3); print $3; exit } /^$/ { f = g = 0 }')
    [[ -n $sum ]] || die "no checksum of walkdir $v in stock rustic's Cargo.lock"
    src=$(crate_src walkdir "$v" "$sum")
    head=$tmp/wd-head; imp=$tmp/wd-import
    mkdir -p "$head" "$imp"
    git -C "$g" archive "$c" vendor/walkdir | tar -x -C "$head"
    head=$head/vendor/walkdir
    # The scope: new-side lines of each hunk; files the release lacks, whole.
    (cd "$head" && find . -type f | sed 's|^\./||' | sort) | while IFS= read -r f; do
        LC_ALL=C grep -qI '' "$head/$f" || continue
        n=$(nlines <"$head/$f")
        if [[ ! -f $src/$f ]]; then
            echo "vendor/walkdir/$f 1-$n" >>"$tmp/body"
            printf '# lines %6d %6d walkdir %s\n' "$n" "$n" "vendor/walkdir/$f" >>"$tmp/sum"
            continue
        fi
        hunks_new "$src/$f" "$head/$f" | awk '$2 > 0 { for (i = 0; i < $2; i++) print $1 + i }' | ranges >"$tmp/wd.r"
        [[ -s $tmp/wd.r ]] || continue
        sed "s|^|vendor/walkdir/$f |" "$tmp/wd.r" >>"$tmp/body"
        printf '# lines %6d %6d walkdir %s\n' \
            "$(awk '{ split($1, a, "-"); s += a[2] - a[1] + 1 } END { print s + 0 }' "$tmp/wd.r")" "$n" "vendor/walkdir/$f" >>"$tmp/sum"
    done
    # The import commit (the one that added src/lib.rs) against the release.
    local ic
    ic=$(git -C "$g" log --diff-filter=A --format=%H "$c" -- vendor/walkdir/src/lib.rs | tail -1)
    git -C "$g" archive "$ic" vendor/walkdir | tar -x -C "$imp"
    imp=$imp/vendor/walkdir
    {
        echo "# walkdir: vendor/walkdir against crates.io walkdir-$v (sha256 $sum, as stock rustic's Cargo.lock)"
        echo "# walkdir import ${ic:0:12} ($(git -C "$g" log -1 --format=%s "$ic" | cut -c 1-90)) against the release:"
        (cd "$src" && find . -type f | sed 's|^\./||' | sort) | while IFS= read -r f; do
            [[ -f $imp/$f ]] || echo "#   dropped: $f (in the .crate, not in the import)"
        done
        unmarked=0
        (cd "$imp" && find . -type f | sed 's|^\./||' | sort) | while IFS= read -r f; do
            if [[ ! -f $src/$f ]]; then echo "#   added: $f ($(nlines <"$imp/$f") lines)"; continue; fi
            cmp -s "$src/$f" "$imp/$f" && continue
            # A hunk of diff -U3 (changes closer than 6 lines are one) is
            # marked when "BackOtter" is in it, its context included.
            h=0 unmarked=0
            while read -r s cnt; do
                h=$((h + 1))
                sed -n "${s},$((s + cnt - 1))p" "$imp/$f" | grep -q BackOtter || unmarked=$((unmarked + 1))
            done < <(hunks_new "$src/$f" "$imp/$f" 3)
            echo "#   changed: $f, $h hunks (diff -U3), $unmarked without a BackOtter mark in them"
        done
    } >"$tmp/walkdir"
    if grep -q '^#   \(changed\|added\|dropped\)' "$tmp/walkdir" &&
        ! grep '^#   changed' "$tmp/walkdir" | grep -vq ', 0 without'; then
        echo "# walkdir import verdict: the release plus marked hunks and added files only (see the lines above for what it dropped)" >>"$tmp/walkdir"
    else
        echo "# walkdir import verdict: the release plus our changes, but with hunks that carry no BackOtter mark: read them (RA4 R8)" >>"$tmp/walkdir"
    fi
}

tmp=$(mktemp -d "${TMPDIR:-/tmp}/rust-scope.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
for a in "$@"; do
    n=${a%%=*} rev=
    [[ $a == *=* ]] && rev=${a#*=}
    rm -rf "${tmp:?}"/*
    case $n in
        holt) scope_holt "${rev:-main}" ;;
        rustic_core | rustic) scope_fork "$n" "${rev:-backotter}" ;;
        *) die "unknown NAME $n (rustic_core, rustic, holt)" ;;
    esac
done
