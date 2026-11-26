#!/bin/sh
# The header test: bko-rescue's view of the C ABI (`bko-rescue --abi`)
# against this system's headers (abi.c), then the symbol each function
# links to on both sides.  POSIX sh, cc and nm only, so it runs in every
# build guest, the BSDs and illumos included.
#
#   abi/check.sh PATH/TO/bko-rescue [CC]
#
# Exit 0 when everything agrees, 1 on a difference, 2 on a usage error.
set -eu

[ $# -ge 1 ] || { echo "usage: $0 BKO_RESCUE [CC]" >&2; exit 2; }
bin=$1
cc=${2:-${CC:-}}
if [ -z "$cc" ]; then
    # cc, else what the build guests have: omnios58's gcc14 is only
    # /opt/gcc-14/bin/gcc, with no cc on PATH (RT3).
    for c in cc gcc clang /opt/gcc-*/bin/gcc; do
        if command -v "$c" >/dev/null 2>&1; then
            cc=$c
            break
        fi
    done
fi
if [ -z "$cc" ] || ! command -v "$cc" >/dev/null 2>&1; then
    echo "abi: FAIL: no C compiler (${cc:-cc, gcc, clang or /opt/gcc-*/bin/gcc}): name one as the second argument or in CC" >&2
    exit 1
fi
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/bko-abi.XXXXXX")
trap 'rm -rf "$work"' EXIT INT TERM

"$bin" --abi >"$work/rs-all.txt"
grep -v '^sym ' "$work/rs-all.txt" >"$work/rs.txt"
req=$(sed -n 's/^type ioctl\.request //p' "$work/rs.txt")
[ -n "$req" ] || { echo "abi: no ioctl request type in --abi" >&2; exit 1; }
buf=$(sed -n 's/^room statvfs //p' "$work/rs.txt")
[ -n "$buf" ] || { echo "abi: no statvfs buffer size in --abi" >&2; exit 1; }

# -O0 and no fortify, so the C calls stay plain calls of the plain names.
if ! "$cc" -O0 -U_FORTIFY_SOURCE "-DBKO_IOCTL_REQ=$req" "-DBKO_STATVFS_BUF=$buf" \
    -o "$work/abi" "$here/abi.c" 2>"$work/cc.err"; then
    cat "$work/cc.err" >&2
    echo "abi: FAIL: abi.c doesn't build with ioctl's request as '$req'" >&2
    exit 1
fi
"$work/abi" >"$work/c.txt"

fail=0
if ! diff "$work/c.txt" "$work/rs.txt" >"$work/diff.txt"; then
    echo "abi: FAIL: our declarations differ from the headers (< C, > ours):"
    cat "$work/diff.txt"
    fail=1
fi
facts=$(wc -l <"$work/c.txt" | tr -d ' ')

# Symbols: the name the C program imports for each function, from nm's
# portable format (name first), without any @VERSION suffix.
syms() {
    nm -P "$@" 2>/dev/null | awk '{ sub(/@.*/, "", $1); print $1 }' | sort -u
}
syms -u "$work/abi" >"$work/c.syms"
syms "$bin" >"$work/rs.syms"
if [ ! -s "$work/rs.syms" ]; then
    # A stripped binary: its dynamic symbols.
    syms -D "$bin" >"$work/rs.syms"
fi
nsyms=0
while read -r tag fn link; do
    [ "$tag" = sym ] || continue
    nsyms=$((nsyms + 1))
    cname=$(grep -E "^_*${fn}[0-9]*\$" "$work/c.syms" || true)
    case $cname in
    "")
        echo "abi: FAIL: the C program imports no symbol for $fn"
        fail=1 ;;
    *"
"*)
        echo "abi: FAIL: the C program imports more than one name for $fn:" $cname
        fail=1 ;;
    "$link") ;;
    *)
        echo "abi: FAIL: $fn is $cname in C but $link in ours (a #[link_name] fixes it)"
        fail=1 ;;
    esac
    if [ -s "$work/rs.syms" ] && ! grep -qx "$link" "$work/rs.syms"; then
        echo "abi: FAIL: $bin has no symbol $link"
        fail=1
    fi
done <"$work/rs-all.txt"
[ -s "$work/rs.syms" ] || echo "abi: note: $bin has no symbols nm can list; imports not checked"

if [ "$fail" = 0 ]; then
    echo "abi: ok: $facts facts and $nsyms symbols agree ($(uname -sr))"
fi
exit "$fail"
