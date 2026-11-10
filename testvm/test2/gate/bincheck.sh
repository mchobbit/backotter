#!/usr/bin/env bash
# GateTest2's binary checks, the layers gate:bin:abi, gate:bin:static and
# gate:bin:version (testvm/test2/gate/layers.json), on a set of BackOtter's
# binaries: a round's bin/ or bko-bin (OS/restic-bko, OS/rustic-bko), and
# bko-holt's (HOLT/bko-holt for Linux, HOLT/PLAT/bko-holt).
#
# Usage (on a KVM test host; anywhere with GNU readelf, grep and python3):
#   bincheck.sh abi     [--set DIR] [--holt DIR] [--ref FILE] [FILE...]
#   bincheck.sh static  [--set DIR] [FILE...]
#   bincheck.sh version [--set DIR] [--holt DIR] [--expect FILE] [--holt-version V] [FILE...]
#
# Without FILEs each checks its binaries of --set (and --holt): abi
# freebsd/restic-bko, freebsd/rustic-bko and HOLT/freebsd/bko-holt; static
# linux/restic-bko and linux/rustic-bko; version every OS's restic-bko and
# rustic-bko (and those of any other directory of the set, such as
# haiku.rel4/, and links such as omnios -> illumos, each by its own name)
# and every bko-holt.
# A set's binary that isn't there is a FAIL,
# a holt one a SKIP (that CODE isn't built yet); a FILE named that isn't
# there is a FAIL.
#
#   abi      readelf's NEEDED libraries, version needs and undefined
#            symbols (readelf -dW, -V --wide, --dyn-syms -W) against FILE
#            (default freebsd-14.5.abi beside this script, made by
#            abi-ref.sh from FreeBSD 14.5's /lib and /usr/lib): every
#            library must be there, define every version the binary needs
#            from it, and, where the reference has the library's symbols,
#            define each symbol at the version the binary asks for (14.5's
#            libc defines FBSD_1.8, but not setgroups@FBSD_1.8).  A FAIL
#            names each library, version or symbol@version 14.5 lacks.
#   static   Linux binaries.  Both: no PT_INTERP (readelf -lW), no
#            DT_NEEDED (readelf -dW), no GLIBC_ version needs.  rustic-bko
#            (musl, issue #1 was a static glibc build): no NT_GNU_ABI_TAG
#            note (readelf -nW) and no "nsswitch.conf" string (glibc's NSS).
#            restic-bko: its Go build info (.go.buildinfo) says
#            "build CGO_ENABLED=0".  Not the nsswitch.conf string for
#            restic-bko: Go's own resolver carries it.  Any other FILE: the
#            three common checks.
#   version  The version each binary prints (linux/*, HOLT/bko-holt: run
#            with version or --version) or holds (grep -aoE
#            '[0-9]+\.[0-9]+\.[0-9]+\+backotter\.[0-9]+', as run-all.sh
#            reads non-Linux binaries) against --expect FILE: lines
#            NAME<TAB>VERSION[<TAB>CORE_VERSION] (NAME as OS/restic-bko;
#            CORE_VERSION: bko_core's, which rustic-bko holds too).
#            bko-holt: "bko-holt V" (--holt-version V; GateTest2 passes
#            holt/Cargo.toml's [package] version).  A binary with no
#            expectation is a SKIP that still prints what it holds; with
#            --expect, a set's binary missing from FILE is a FAIL (it can't
#            be checked: GateTest2 never passes an unchecked binary).
#
# Output, one line per binary:  VERDICT CHECK NAME [VALUE]: DETAIL
# (VERDICT PASS, FAIL or SKIP; NAME as OS/FILE, holt/PLAT/bko-holt, or the
# FILE as given; VALUE, for version, what it prints or holds).  Exit 0 if
# nothing failed, 1 if something did, 2 on a usage error.

set -u
here=$(cd "$(dirname "$0")" && pwd)
export LC_ALL=C
die() { echo "bincheck: $*" >&2; exit 2; }
usage() { sed -n '6,9p' "$0" >&2; exit 2; }

[[ $# -ge 1 ]] || usage
check=$1; shift
case $check in abi|static|version) ;; *) usage ;; esac
set_dir="" holt="" ref=$here/freebsd-14.5.abi expect="" holt_version="" files=()
while [[ $# -gt 0 ]]; do
    case $1 in
        --set) set_dir=${2:-}; shift 2 ;;
        --holt) holt=${2:-}; shift 2 ;;
        --ref) ref=${2:-}; shift 2 ;;
        --expect) expect=${2:-}; shift 2 ;;
        --holt-version) holt_version=${2:-}; shift 2 ;;
        -*) usage ;;
        *) files+=("$1"); shift ;;
    esac
done
command -v readelf >/dev/null || die "no readelf (binutils)"
[[ -z $set_dir || -d $set_dir ]] || die "no set directory $set_dir"
[[ -z $holt || -d $holt ]] || die "no holt directory $holt"

OSES="linux freebsd netbsd openbsd dragonfly illumos haiku"
PLATS="freebsd netbsd openbsd dragonfly illumos haiku"
# NAME<TAB>PATH<TAB>MISSING (what a missing default is: FAIL or SKIP)<TAB>ORIGIN
# (set: a binary of --set; else holt or file)
targets=()
add() { targets+=("$1"$'\t'"$2"$'\t'"$3"$'\t'"${4:-file}"); }
if [[ ${#files[@]} -gt 0 ]]; then
    for f in "${files[@]}"; do add "$f" "$f" FAIL; done
else
    [[ -n $set_dir || -n $holt ]] || die "no FILE, --set or --holt"
    case $check in
        abi)
            if [[ -n $set_dir ]]; then for t in restic rustic; do add "freebsd/$t-bko" "$set_dir/freebsd/$t-bko" FAIL; done; fi
            [[ -z $holt ]] || add holt/freebsd/bko-holt "$holt/freebsd/bko-holt" SKIP ;;
        static)
            [[ -n $set_dir ]] || die "static needs --set or FILEs"
            for t in restic rustic; do add "linux/$t-bko" "$set_dir/linux/$t-bko" FAIL; done ;;
        version)
            if [[ -n $set_dir ]]; then
                for os in $OSES; do
                    for t in restic rustic; do
                        # Haiku has no restic (BUILD-INFO: "No restic on Haiku").
                        [[ $os == haiku && $t == restic ]] && continue
                        [[ -d $set_dir/$os ]] && add "$os/$t-bko" "$set_dir/$os/$t-bko" FAIL set
                    done
                done
                # Other directories of the set that hold archivers (haiku.rel4/, the link
                # omnios/): every binary of the set is checked by the name the set's
                # commit files give it, not only the OS list's.
                for d in "$set_dir"/*/; do
                    d=${d%/} os=${d##*/}
                    [[ " $OSES " == *" $os "* ]] && continue
                    for t in restic rustic; do
                        [[ -e $d/$t-bko ]] && add "$os/$t-bko" "$d/$t-bko" FAIL set
                    done
                done
            fi
            if [[ -n $holt ]]; then
                add holt/bko-holt "$holt/bko-holt" SKIP
                for p in $PLATS; do add "holt/$p/bko-holt" "$holt/$p/bko-holt" SKIP; done
            fi ;;
    esac
fi

fails=0
say() {  # VERDICT NAME [VALUE] DETAIL
    local v=$1 n=$2 val=$3 d=$4
    [[ $v == FAIL ]] && fails=$((fails + 1))
    if [[ -n $val ]]; then echo "$v $check $n [$val]: $d"; else echo "$v $check $n: $d"; fi
}
missing() {  # NAME MISSING-VERDICT
    if [[ $2 == SKIP ]]; then say SKIP "$1" "" "not built (no such file)"; else say FAIL "$1" "" "no such file"; fi
}
is_elf() { [[ $(head -c 4 "$1" 2>/dev/null | od -An -c | tr -d ' \n') == 177ELF ]]; }

# --- abi --------------------------------------------------------------------------
needed() { readelf -dW "$1" 2>/dev/null | sed -n 's/.*(NEEDED).*\[\(.*\)\].*/\1/p'; }
version_needs() {  # FILE -> "LIB VERSION" lines from .gnu.version_r
    readelf -V --wide "$1" 2>/dev/null | awk '
        /^Version needs section/ { inr = 1; next }
        /^Version (definition|symbols) section/ { inr = 0 }
        inr && /File: / { for (i = 1; i <= NF; i++) if ($i == "File:") lib = $(i + 1) }
        inr && /Name: / { for (i = 1; i <= NF; i++) if ($i == "Name:") print lib, $(i + 1) }'
}
# The ABI check: readelf's NEEDED, version needs (.gnu.version_r: library, version,
# index) and undefined symbols (name@VERSION (index)) of the binary against the
# reference's lib and sym lines.  A symbol is checked against the symbols its version
# defines in the reference when the reference has that library's symbols; otherwise
# only the version name is.  Prints "VERDICT DETAIL".
ABICHECK='
import re, subprocess, sys
ref, binary = sys.argv[1], sys.argv[2]
libs, syms = {}, {}
for line in open(ref, encoding="utf-8"):
    if line.startswith("#") or not line.strip():
        continue
    f = line.rstrip("\n").split("\t")
    if f[0] == "lib" and len(f) >= 3:
        libs[f[1]] = set() if f[2] == "-" else set(f[2].split())
    elif f[0] == "sym" and len(f) >= 4:
        syms.setdefault(f[1], {})[f[2]] = set(f[3].split())
def readelf(*a):
    return subprocess.run(["readelf"] + list(a) + [binary], capture_output=True, text=True).stdout
needed = re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", readelf("-dW"))
vers, lib, inr = {}, None, False
for line in readelf("-V", "--wide").splitlines():
    if line.startswith("Version needs section"):
        inr = True
        continue
    if line.startswith("Version ") and "section" in line:
        inr = False
    if not inr:
        continue
    m = re.search(r"File: (\S+)", line)
    if m:
        lib = m.group(1)
    m = re.search(r"Name: (\S+)\s+Flags: \S+\s+Version: (\d+)", line)
    if m and lib:
        vers[m.group(2)] = (lib, m.group(1))
und = []
for line in readelf("--dyn-syms", "-W").splitlines():
    f = line.split()
    if len(f) >= 8 and f[0].endswith(":") and f[6] == "UND" and "@" in f[7]:
        name, _, ver = f[7].partition("@")
        idx = f[8].strip("()") if len(f) > 8 else None
        und.append((name, ver.lstrip("@"), idx))
if not needed and not vers:
    print("PASS no libraries needed (static)")
    sys.exit(0)
bad, note = [], []
for l in needed:
    if l not in libs:
        bad.append(l + " is not in FreeBSD 14.5")
missing_v = {}
for idx, (l, v) in sorted(vers.items(), key=lambda kv: int(kv[0])):
    if l not in libs:
        if l not in needed:
            bad.append("%s (for %s) is not in FreeBSD 14.5" % (l, v))
        continue
    if v not in libs[l]:
        missing_v.setdefault(l, []).append(v)
for l, vs in sorted(missing_v.items()):
    bad.append("%s: versions %s are not in FreeBSD 14.5" % (l, ", ".join(vs)))
missing_s = {}
for name, ver, idx in und:
    l = vers.get(idx, (None, None))[0] if idx else None
    if l is None:
        l = next((x for x, v in vers.values() if v == ver), None)
    if l is None or l not in libs or ver not in libs[l] or l not in syms:
        if l in libs and l not in syms and ver in libs[l]:
            note.append(l)
        continue
    if name not in syms[l].get(ver, ()):
        missing_s.setdefault(l, []).append("%s@%s" % (name, ver))
for l, ss in sorted(missing_s.items()):
    bad.append("%s: %s not in FreeBSD 14.5" % (l, ", ".join(sorted(ss))))
if bad:
    print("FAIL " + "; ".join(bad))
else:
    by = {}
    for l, v in vers.values():
        by.setdefault(l, []).append(v)
    d = " ".join("%s(%s)" % (l, " ".join(sorted(vs))) for l, vs in sorted(by.items()))
    d += ", %d symbols checked" % sum(1 for n, v, i in und if (vers.get(i, (None,))[0] in syms))
    if note:
        d += "; version names only for " + ", ".join(sorted(set(note)))
    print("PASS " + d)
'
abi_one() {  # NAME PATH
    local out
    out=$(python3 -I -c "$ABICHECK" "$ref" "$2" 2>&1)
    case $out in
        PASS\ *|FAIL\ *) say "${out%% *}" "$1" "" "${out#* }" ;;
        *) say FAIL "$1" "" "the ABI check failed: $(tail -n 1 <<<"$out")" ;;
    esac
}

# --- static -----------------------------------------------------------------------
GOBUILDINFO='
import struct, sys
p = sys.argv[1]
data = open(p, "rb").read()
MAGIC = b"\xff Go buildinf:"
off = -1
if data[:4] == b"\x7fELF" and data[4] == 2:
    e = "<" if data[5] == 1 else ">"
    shoff, = struct.unpack_from(e + "Q", data, 0x28)
    shentsize, shnum, shstrndx = struct.unpack_from(e + "HHH", data, 0x3A)
    if shoff and shnum and shstrndx < shnum:
        def sh(i):
            return struct.unpack_from(e + "IIQQQQ", data, shoff + i * shentsize)
        stro = sh(shstrndx)[4]
        for i in range(shnum):
            name, _, _, _, o, size = sh(i)
            end = data.find(b"\0", stro + name)
            if data[stro + name:end] == b".go.buildinfo":
                off = o
                break
if off < 0 or data[off:off + 14] != MAGIC:
    off = -1
    i = data.find(MAGIC)
    while i >= 0:
        if i % 16 == 0:
            off = i
            break
        i = data.find(MAGIC, i + 1)
if off < 0:
    print("none")
    sys.exit(0)
flags = data[off + 15]
if not flags & 2:
    print("old")      # before Go 1.18: pointers, not read here
    sys.exit(0)
def varstr(i):
    n = shift = 0
    while True:
        b = data[i]; i += 1
        n |= (b & 0x7f) << shift; shift += 7
        if b < 0x80:
            break
    return data[i:i + n], i + n
ver, i = varstr(off + 32)
mod, _ = varstr(i)
if len(mod) >= 33:
    mod = mod[16:-16]
for line in mod.decode("utf-8", "replace").splitlines():
    if line.startswith("build\tCGO_ENABLED="):
        print(line.split("=", 1)[1]); sys.exit(0)
print("unset")
'
static_one() {  # NAME PATH
    local n=$1 p=$2 bad=() ok=() base c
    base=${p##*/}
    if readelf -lW "$p" 2>/dev/null | grep -qE '^ +INTERP '; then
        bad+=("a PT_INTERP ($(readelf -lW "$p" | sed -n 's/.*Requesting program interpreter: \(.*\)\]/\1/p'))")
    else ok+=("no PT_INTERP"); fi
    c=$(needed "$p" | tr '\n' ' ' | sed 's/ $//')
    if [[ -n $c ]]; then bad+=("DT_NEEDED $c"); else ok+=("no DT_NEEDED"); fi
    c=$(version_needs "$p" | awk '$2 ~ /^GLIBC_/ { print $2 }' | sort -u | tr '\n' ' ' | sed 's/ $//')
    if [[ -n $c ]]; then bad+=("glibc version needs $c"); else ok+=("no GLIBC_ needs"); fi
    case $base in
        rustic-bko)
            if readelf -nW "$p" 2>/dev/null | grep -q NT_GNU_ABI_TAG; then
                bad+=("an NT_GNU_ABI_TAG note (a glibc build, not musl)")
            else ok+=("no GNU ABI tag"); fi
            c=$(grep -ac nsswitch.conf "$p")
            if [[ $c -gt 0 ]]; then bad+=("the string nsswitch.conf (glibc's NSS)"); else ok+=("no nsswitch.conf"); fi ;;
        restic-bko)
            c=$(python3 -I -c "$GOBUILDINFO" "$p" 2>&1)
            case $c in
                0) ok+=("Go build info: CGO_ENABLED=0") ;;
                none) bad+=("no Go build info (.go.buildinfo)") ;;
                old) bad+=("Go build info of a Go before 1.18 (unread)") ;;
                unset) bad+=("Go build info without CGO_ENABLED") ;;
                *) bad+=("Go build info: CGO_ENABLED=$c") ;;
            esac ;;
    esac
    if [[ ${#bad[@]} -gt 0 ]]; then
        say FAIL "$n" "" "$(printf '%s; ' "${bad[@]}" | sed 's/; $//')"
    else
        say PASS "$n" "" "$(printf '%s, ' "${ok[@]}" | sed 's/, $//')"
    fi
}

# --- version ----------------------------------------------------------------------
expect_of() {  # NAME -> "VERSION CORE" from --expect
    [[ -n $expect && -f $expect ]] || return 0
    awk -F'\t' -v n="$1" '$1 == n { print $2, $3; exit }' "$expect"
}
runs_here() {  # NAME PATH: a Linux binary, run to ask (on Linux only)
    [[ $(uname -s) == Linux ]] || return 1
    [[ $1 == holt/bko-holt || $1 == linux/* || $2 == */linux/* ]]
}
version_one() {  # NAME PATH
    local n=$1 p=$2 want core found how out
    if [[ $n == holt/* || ${p##*/} == bko-holt ]]; then
        want=$holt_version
        if runs_here "$n" "$p"; then
            out=$("$p" --version 2>&1 | head -n 1); how="prints"
            found=$(sed -n 's/^bko-holt \([0-9][0-9.]*\).*/\1/p' <<<"$out")
        else
            found=$(grep -aoE 'bko-holt [0-9]+\.[0-9]+\.[0-9]+' "$p" | sed 's/^bko-holt //' | sort -u | tr '\n' ' ' | sed 's/ $//')
            how="holds"
        fi
        if [[ -z $want ]]; then say SKIP "$n" "$found" "$how bko-holt ${found:-nothing}; no expected version"
        elif [[ " $found " == *" $want "* ]]; then say PASS "$n" "$want" "$how bko-holt $want"
        else say FAIL "$n" "$found" "$how bko-holt ${found:-no version}, not $want"; fi
        return
    fi
    read -r want core <<<"$(expect_of "$n")"
    if runs_here "$n" "$p"; then
        case ${p##*/} in
            restic-bko) out=$("$p" version 2>&1 | head -n 1) ;;
            *) out=$("$p" --version 2>&1 | head -n 1) ;;
        esac
        found=$(awk '{ print $2 }' <<<"$out") how="prints"
        # bko_core's version is only held, as in every rustic-bko.
        local held; held=$(grep -aoE '[0-9]+\.[0-9]+\.[0-9]+\+backotter\.[0-9]+' "$p" | sort -u | tr '\n' ' ')
    else
        found=$(grep -aoE '[0-9]+\.[0-9]+\.[0-9]+\+backotter\.[0-9]+' "$p" | sort -u | tr '\n' ' ' | sed 's/ $//')
        how="holds" held="$found "
    fi
    local main=$found
    if [[ -n $want && $how == holds ]]; then
        # The binary's own version among what it holds (rustic-bko holds bko_core's too).
        [[ " $found " == *" $want "* ]] && main=$want
    fi
    if [[ -z $want ]]; then
        if [[ -n $expect && $origin == set ]]; then   # a set binary --expect doesn't name: unchecked
            say FAIL "$n" "$main" "$how ${found:-no version}; no expected version in $expect"
        else
            say SKIP "$n" "$main" "$how ${found:-no version}; no expected version"
        fi
        return
    fi
    local bad=()
    [[ " $found " == *" $want "* ]] || bad+=("$how ${found:-no version}, not $want")
    if [[ -n $core ]]; then [[ " $held " == *" $core "* ]] || bad+=("no bko_core $core in it"); fi
    if [[ ${#bad[@]} -gt 0 ]]; then
        say FAIL "$n" "$main" "$(printf '%s; ' "${bad[@]}" | sed 's/; $//')"
    else
        say PASS "$n" "$want" "$how $want${core:+, bko_core $core}"
    fi
}

if [[ $check == abi ]]; then
    [[ -f $ref ]] || die "no ABI reference $ref (abi-ref.sh makes it)"
    command -v python3 >/dev/null || die "no python3"
fi
for t in "${targets[@]}"; do
    IFS=$'\t' read -r name path miss origin <<<"$t"
    if [[ ! -e $path ]]; then missing "$name" "$miss"; continue; fi
    if ! is_elf "$path"; then say FAIL "$name" "" "not an ELF file"; continue; fi
    case $check in
        abi) abi_one "$name" "$path" ;;
        static) static_one "$name" "$path" ;;
        version) version_one "$name" "$path" ;;
    esac
done
[[ $fails -eq 0 ]] || exit 1
exit 0
