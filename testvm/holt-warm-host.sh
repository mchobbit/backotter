#!/usr/bin/env bash
# The host side of testvm/holt-warm (docs/design/img-speed.md F14): a warm
# bko-holt build dir, ROOT/TAG/{src,target}, on any build host, and the prune
# of stale tags.  testvm/holt-warm runs it from the Mac; nothing else should.
#
#   holt-warm-host.sh test DIR TOOLCHAIN [CARGO_TEST_ARG...]
#       In DIR's src/holt: cargo test --offline with CARGO_TARGET_DIR=DIR/target
#       (warm: it is kept), Rust TOOLCHAIN (RUSTUP_TOOLCHAIN, never the host's
#       rustup default; build-holt.sh's rule), the host row's rustup_home and
#       cargo_home when unset (hosts.sh hosts_rust_env), and TMPDIR in the RAM
#       disk (/mnt/bko-ram, BKO_HOLT_WARM_TMP), removed after.  It stamps
#       DIR/.last-used first, so prune sees the tag in use however old its
#       files (rsync skips a file whose content is unchanged).  testvm/holt-warm runs it
#       under cmp-run.sh --reserve 2 4, which holds that room meanwhile.
#   holt-warm-host.sh prune ROOT DAYS [--whole] [--dry-run]
#       Each tag ROOT/TAG is stale when neither its .last-used nor any file or
#       directory in it changed in DAYS days, no process of this user has its
#       cwd or an open file in it (/proc; lsof +D where there is no /proc), and no
#       .cargo-lock in it is held (flock -n, where there is flock).  Of a stale
#       tag it removes the cargo target dirs (a directory, the tag or one
#       directly in it, holding cargo's CACHEDIR.TAG), the bulk of its size,
#       and keeps its src, logs and the rest; --whole removes the tag.  A tag
#       not stale is left whole.  --dry-run says what it would remove.  One
#       line a tag: "keep TAG: why", "prune TAG: PATH ..." (or "would prune"),
#       then "freed N KiB".
#
# Exit 0 when done, 1 on a failure (a test that failed: cargo's status), 2 on
# bad usage.

set -uo pipefail

die() { echo "holt-warm-host.sh: $*" >&2; exit 2; }
SIG='Signature: 8a477f597d28d172789f06886806bc55'

do_test() {
    local dir=$1 tc=$2; shift 2
    [[ $dir == /* && -d $dir/src/holt ]] || die "no $dir/src/holt"
    [[ $tc =~ ^[A-Za-z0-9._-]+$ ]] || die "bad toolchain $tc"
    touch "$dir/.last-used" || return 1
    local tv=${BKO_TESTVM:-$HOME/backotter-work/testvm}
    if [[ -r $tv/hosts.sh ]]; then
        # shellcheck source=hosts.sh
        . "$tv/hosts.sh" && hosts_rust_env "$(hostname -s)" 2>/dev/null
    fi
    export RUSTUP_TOOLCHAIN=$tc RUSTUP_AUTO_INSTALL=0 PATH=$HOME/.cargo/bin:$PATH
    rustup run "$tc" rustc -V || { echo "no Rust $tc here (rustup toolchain install $tc)" >&2; return 1; }
    local tb=${BKO_HOLT_WARM_TMP:-/mnt/bko-ram}
    [[ -d $tb && -w $tb ]] || tb=$dir
    # Global, not local: the EXIT trap runs after this function has returned.
    WARM_TMP=$(mktemp -d "$tb/holt-warm-$(basename "$dir").XXXXXX") || return 1
    trap 'rm -rf "$WARM_TMP"' EXIT
    export CARGO_TARGET_DIR=$dir/target TMPDIR=$WARM_TMP
    echo "holt-warm: $(hostname -s) $dir rust $tc: cargo test --offline $*"
    (cd "$dir/src/holt" && cargo test --offline "$@")
}

# Every path some process of ours has open or as its cwd, one a line (lsof: those under $1).
open_paths() {
    if [[ -d /proc/self/fd ]]; then
        local p
        for p in /proc/[0-9]*; do
            readlink "$p/cwd" 2>/dev/null
            find "$p/fd" -mindepth 1 -maxdepth 1 -exec readlink {} \; 2>/dev/null
        done
    elif command -v lsof >/dev/null; then
        lsof -w -Fn +D "$1" 2>/dev/null | sed -n 's/^n//p'
    fi
}

# Why TAG (a directory) must stay; nothing when it is stale.
busy() {
    local t=$1 mins=$2 opened=$3 l
    if [[ -e $t/.last-used ]] && [[ -n $(find "$t/.last-used" -mmin "-$mins" 2>/dev/null) ]]; then
        echo "used in the last $((mins / 1440)) days"; return
    fi
    [[ -z $(find "$t" -mmin "-$mins" -print -quit 2>/dev/null) ]] ||
        { echo "changed in the last $((mins / 1440)) days"; return; }
    if grep -qxF -e "$t" <<<"$opened" || grep -qF -e "$t/" <<<"$opened"; then
        echo "a process has it open"; return
    fi
    if command -v flock >/dev/null; then
        while IFS= read -r l; do
            flock -n "$l" true 2>/dev/null || { echo "${l#"$t"/} is held"; return; }
        done < <(find "$t" -maxdepth 4 -name .cargo-lock -type f 2>/dev/null)
    fi
}

# The cargo target dirs of tag T: T itself, or the directories directly in it.
targets() {
    local t=$1 c
    for c in "$t/CACHEDIR.TAG" "$t"/*/CACHEDIR.TAG; do
        [[ -f $c && ! -L $c && ! -L ${c%/CACHEDIR.TAG} ]] || continue
        [[ $(head -n1 "$c" 2>/dev/null) == "$SIG" ]] || continue
        echo "${c%/CACHEDIR.TAG}"
    done
}

do_prune() {
    local root=$1 days=$2; shift 2
    local whole=0 dry=0 a
    for a; do
        case $a in --whole) whole=1 ;; --dry-run) dry=1 ;; *) die "prune: unknown $a" ;; esac
    done
    [[ $root == /* && -d $root ]] || die "no $root"
    [[ $days =~ ^[1-9][0-9]*$ ]] || die "DAYS must be a whole number of days, 1 or more"
    root=$(cd "$root" && pwd -P)
    local mins=$((days * 1440)) opened t why gone k freed=0 rc=0 verb=prune
    ((dry)) && verb="would prune"
    opened=$(open_paths "$root")
    for t in "$root"/*; do
        [[ -d $t && ! -L $t ]] || continue
        why=$(busy "$t" "$mins" "$opened")
        if [[ -n $why ]]; then echo "keep ${t##*/}: $why"; continue; fi
        gone=()
        if ((whole)); then gone=("$t"); else while IFS= read -r a; do gone+=("$a"); done < <(targets "$t"); fi
        if [[ ${#gone[@]} -eq 0 ]]; then echo "keep ${t##*/}: stale, no cargo target dir"; continue; fi
        k=$(du -sk "${gone[@]}" 2>/dev/null | awk '{s += $1} END {print s + 0}')
        echo "$verb ${t##*/}: ${gone[*]#"$root"/} (${k} KiB)"
        ((dry)) || rm -rf -- "${gone[@]}" || rc=1
        freed=$((freed + k))
    done
    if ((dry)); then echo "would free $freed KiB"; else echo "freed $freed KiB"; fi
    return $rc
}

case ${1:-} in
    test) [[ $# -ge 3 ]] || die "usage: test DIR TOOLCHAIN [CARGO_TEST_ARG...]"; shift; do_test "$@" ;;
    prune) [[ $# -ge 3 ]] || die "usage: prune ROOT DAYS [--whole] [--dry-run]"; shift; do_prune "$@" ;;
    *) die "usage: test DIR TOOLCHAIN [ARG...] | prune ROOT DAYS [--whole] [--dry-run]" ;;
esac
