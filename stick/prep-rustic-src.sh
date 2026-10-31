#!/bin/sh
# Point a copy of rustic at the rustic_core beside it, to build the two
# unpushed: rustic's [patch.crates-io] takes rustic_core from
# DIR/rustic_core/crates/core and walkdir from DIR/rustic_core/vendor/walkdir
# (rustic_core's own walkdir, which lists directories with O_NOATIME), instead
# of the fork's git branch and crates.io, and Cargo.lock is refreshed for
# those two (cargo update -p rustic_core -p walkdir), so that the build after
# it can be --locked.  Every build-rustic.sh runs it in SRC= mode.
#
# Usage: prep-rustic-src.sh DIR
#
# DIR holds rustic/ and rustic_core/ (checkouts of the forks, or copies
# without .git).  DIR/rustic/Cargo.toml and Cargo.lock are changed in place:
# give it a copy, not your checkout.  Running it again changes nothing more.
# Plain sh, sed-free and with awk's POSIX subset, so that it runs in the BSD
# and illumos guests as well as here: the native builds run it there, with
# the guest's cargo.
#
#   CARGO   the cargo to run (default: cargo from PATH, else ~/.cargo/bin/cargo)
#
# Needs: cargo, and crates.io's index (the update may look walkdir's
# dependencies up there).

set -eu

die() { echo "prep-rustic-src: $*" >&2; exit 1; }
[ $# -eq 1 ] || die "usage: prep-rustic-src.sh DIR"
dir=$1
toml=$dir/rustic/Cargo.toml
lock=$dir/rustic/Cargo.lock
[ -f "$toml" ] && [ -f "$lock" ] || die "$dir/rustic has no Cargo.toml and Cargo.lock"
[ -f "$dir/rustic_core/crates/core/Cargo.toml" ] && [ -f "$dir/rustic_core/vendor/walkdir/Cargo.toml" ] ||
    die "$dir/rustic_core has no crates/core and vendor/walkdir"
# illumos' /usr/bin/awk is the old one, without -v.
awk=$(command -v nawk 2>/dev/null || command -v awk)

git_line='rustic_core = { git = "https://github.com/mchobbit/rustic_core.git", branch = "backotter" }'
core_line='rustic_core = { path = "../rustic_core/crates/core" }'
walkdir_line='walkdir = { path = "../rustic_core/vendor/walkdir" }'
# In [patch.crates-io], the git rustic_core (or the path one, from an earlier
# run) becomes the two path lines, and any other walkdir line goes.
"$awk" -v git="$git_line" -v core="$core_line" -v wd="$walkdir_line" '
    /^\[/ { inpatch = ($0 == "[patch.crates-io]") }
    inpatch && ($0 == git || $0 == core) { print core; print wd; found = 1; next }
    inpatch && /^walkdir *=/ { next }
    { print }
    END { exit !found }' "$toml" >"$toml.new" || {
    rm -f "$toml.new"
    die "rustic's [patch.crates-io] has neither \"$git_line\" nor \"$core_line\""
}
# Unchanged, the file keeps its time, and cargo sees nothing to rebuild.
changed=yes
if cmp -s "$toml" "$toml.new"; then rm "$toml.new"; changed=no; else mv "$toml.new" "$toml"; fi

cargo=${CARGO:-}
if [ -z "$cargo" ]; then
    cargo=$(command -v cargo 2>/dev/null) || cargo=$HOME/.cargo/bin/cargo
fi
[ -x "$cargo" ] || die "no cargo (set CARGO)"

# Path dependencies have no "source" in Cargo.lock.
from_paths() {
    "$awk" '$0 == "name = \"rustic_core\"" || $0 == "name = \"walkdir\"" { p = $3; next }
        p != "" && /^source = / { print p " still comes from " $3; bad = 1 }
        /^$/ { p = "" }
        END { exit bad }' "$lock"
}
# Done already (a second run): no update, which isn't idempotent (it can
# swap windows-sys versions back and forth).  --locked fails if the lock
# doesn't match the trees, e.g. after rustic_core's version changed.
if [ $changed = no ] && from_paths >/dev/null &&
    (cd "$dir/rustic" && "$cargo" metadata --locked --format-version 1 >/dev/null 2>&1); then
    echo "prep-rustic-src: $dir/rustic already takes rustic_core and walkdir from $dir/rustic_core"
    exit 0
fi
(cd "$dir/rustic" && "$cargo" update -p rustic_core -p walkdir) || die "cargo update failed"
from_paths >&2 || die "Cargo.lock doesn't take both from $dir/rustic_core"
