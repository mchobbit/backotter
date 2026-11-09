#!/usr/bin/env bash
# The ABI reference that gate:bin:abi checks FreeBSD binaries against
# (freebsd-14.5.abi beside this script): every shared library of FreeBSD
# 14.5's /lib and /usr/lib by its SONAME with the version names it defines,
# and, for the libraries BackOtter's binaries take (--symbols), the symbols
# each of those versions defines.  The symbols matter: 14.5's libc.so.7
# defines FBSD_1.8 and FBSD_1.9 too, yet not setgroups@FBSD_1.8, which a
# rustic-bko built on FreeBSD 15 needs (freebsd14's SticTest1 failure), so
# version names alone pass such a binary.  Made once; again only when the
# oldest FreeBSD BackOtter supports changes.
#
# Usage (on a KVM test host, as m):
#   abi-ref.sh [--iso ISO | --dir DIR] [--symbols LIB,...] [--out FILE]
#
#   --iso ISO      FreeBSD 14.5's release ISO, read with bsdtar (no root, no
#                  boot).  Default: the one freebsd14 was installed from,
#                  ~/backotter-work/vms/freebsd14/FreeBSD-14.5-RELEASE-amd64-disc1.iso
#                  (BKO_WORK for another ~/backotter-work).  Its sha256 must
#                  be the SHA256 line of announce-14.5.asc beside it, when
#                  there is one (the announcement's signature isn't checked)
#   --dir DIR      a copy of a FreeBSD 14.5 root holding lib/ and usr/lib/,
#                  e.g. from a `vm-run --snapshot` boot of freebsd14 (its root
#                  is ZFS, which vm-mount doesn't read on the host)
#   --symbols LIBS the libraries whose symbols are recorded (default
#                  libc.so.7,libthr.so.3,libm.so.5,libgcc_s.so.1,libutil.so.9,libz.so.6)
#   --out FILE     where it goes (default: stdout)
#
# Lines, after "#" lines that say where it came from:
#   lib<TAB>SONAME<TAB>VERSION ...         (sorted; "-" for none)
#   sym<TAB>SONAME<TAB>VERSION<TAB>NAME ...  (the symbols VERSION defines, as
#                                          name@@VERSION or name@VERSION)
# The files go to a temporary directory in TMPDIR (about 60 MB), removed at
# the end.
#
# Needs: bsdtar (--iso), GNU readelf, sha256sum.

set -u
die() { echo "abi-ref: $*" >&2; exit 2; }
iso="" dir="" out="" syms="libc.so.7,libthr.so.3,libm.so.5,libgcc_s.so.1,libutil.so.9,libz.so.6"
while [[ $# -gt 0 ]]; do
    case $1 in
        --iso) iso=${2:-}; shift 2 ;;
        --dir) dir=${2:-}; shift 2 ;;
        --symbols) syms=${2:-}; shift 2 ;;
        --out) out=${2:-}; shift 2 ;;
        *) die "usage: abi-ref.sh [--iso ISO | --dir DIR] [--symbols LIB,...] [--out FILE]" ;;
    esac
done
[[ -z $iso || -z $dir ]] || die "--iso or --dir, not both"
if [[ -z $iso && -z $dir ]]; then
    iso=${BKO_WORK:-$HOME/backotter-work}/vms/freebsd14/FreeBSD-14.5-RELEASE-amd64-disc1.iso
fi
command -v readelf >/dev/null || die "no readelf"
export LC_ALL=C
tmp=$(mktemp -d "${TMPDIR:-/tmp}/abi-ref.XXXXXX") || die "no temporary directory"
trap 'rm -rf "$tmp"' EXIT
pat='^(\./)?(lib|usr/lib)/[^/]+\.so(\.[0-9]+)*$'
if [[ -n $iso ]]; then
    [[ -f $iso ]] || die "no $iso"
    command -v bsdtar >/dev/null || die "no bsdtar (libarchive-tools)"
    sum=$(sha256sum "$iso" | cut -d' ' -f1)
    ann=$(dirname "$iso")/announce-14.5.asc
    check="not checked: no announce-14.5.asc beside it"
    if [[ -f $ann ]]; then
        want=$(sed -n "s/^ *SHA256 ($(basename "$iso")) = \([0-9a-f]*\).*/\1/p" "$ann" | head -n 1)
        if [[ $want == "$sum" ]]; then check="equal to the SHA256 line of $(basename "$ann")"
        else die "$iso: sha256 $sum, but $(basename "$ann") says ${want:-nothing}"; fi
    fi
    bsdtar -tf "$iso" | grep -E "$pat" >"$tmp/names" || die "no libraries in $iso"
    mkdir "$tmp/root" && bsdtar -x -f "$iso" -C "$tmp/root" -T "$tmp/names" || die "can't read $iso"
    src="$(basename "$iso") (sha256 $sum, $check)"
    root=$tmp/root
else
    [[ -d $dir/lib && -d $dir/usr/lib ]] || die "no lib/ and usr/lib/ in $dir"
    root=$(cd "$dir" && pwd)
    src="the directory $root"
fi
: >"$tmp/libs"
: >"$tmp/syms"
for f in "$root"/lib/* "$root"/usr/lib/*; do
    [[ -f $f ]] || continue
    rel=${f#"$root"/}
    [[ $rel =~ $pat ]] || continue
    readelf -hW "$f" 2>/dev/null | grep -q 'Type: *DYN' || continue
    so=$(readelf -dW "$f" 2>/dev/null | sed -n 's/.*(SONAME).*\[\(.*\)\].*/\1/p')
    [[ -n $so ]] || so=${f##*/}
    v=$(readelf -V --wide "$f" 2>/dev/null | awk '
        /^Version definition section/ { d = 1; next }
        /^Version (needs|symbols) section/ { d = 0 }
        d && /Flags: / && !/Flags: BASE/ { for (i = 1; i <= NF; i++) if ($i == "Name:") print $(i + 1) }' |
        sort -u -V | tr '\n' ' ' | sed 's/ $//')
    printf 'lib\t%s\t%s\n' "$so" "${v:--}" >>"$tmp/libs"
    if [[ ,$syms, == *",$so,"* ]]; then
        # Defined (not UND) versioned symbols: name@@VERSION (the default) or name@VERSION.
        readelf --dyn-syms -W "$f" 2>/dev/null | awk -v so="$so" '
            $1 ~ /^[0-9]+:$/ && $7 != "UND" && $8 ~ /@/ { n = split($8, a, "@"); if (a[1] != "") print so "\t" a[n] "\t" a[1] }' \
            >>"$tmp/syms.raw"
    fi
done
[[ -s $tmp/libs ]] || die "no shared libraries found"
if [[ -s $tmp/syms.raw ]]; then
    sort -u "$tmp/syms.raw" | awk -F'\t' '
        { k = $1 "\t" $2; if (k != last) { if (last != "") print "sym\t" last "\t" s; last = k; s = $3 } else s = s " " $3 }
        END { if (last != "") print "sym\t" last "\t" s }' >"$tmp/syms"
fi
nlib=$(sort -u "$tmp/libs" | wc -l | tr -d ' ')
{
    echo "# FreeBSD 14.5's shared libraries (/lib, /usr/lib): the version names each defines, and the symbols"
    echo "# of each version of $(tr ',' ' ' <<<"$syms"), for gate:bin:abi (testvm/test2/gate/bincheck.sh abi)."
    echo "# Made by testvm/test2/gate/abi-ref.sh on $(date -u '+%Y-%m-%d') from $src."
    echo "# lib<TAB>SONAME<TAB>versions (- for none): $nlib libraries; sym<TAB>SONAME<TAB>VERSION<TAB>its symbols."
    sort -u "$tmp/libs"
    sort -V "$tmp/syms"
} >"$tmp/abi"
if [[ -n $out ]]; then cp "$tmp/abi" "$out" || die "can't write $out"; else cat "$tmp/abi"; fi
