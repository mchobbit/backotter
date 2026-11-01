#!/usr/bin/env bash
# The platforms a fix round must test again (run-all.sh --platforms), from
# what changed in the three forks and in BackOtter's harness since the last
# round that tested them.  Conservative: a change it can't put down to one
# OS's code (the archiver, restorer, chunker, repository format, a build
# script, rt-common.sh, ...) means every platform; tests, docs and notes
# mean none.
#
# Usage: affected.sh [--explain] [--restic OLD..NEW] [--rustic-core OLD..NEW]
#                    [--rustic OLD..NEW] [--harness OLD..NEW] [REPO:PATH ...]
#   --restic, --rustic-core, --rustic, --harness
#             git ranges in ~/git/restic, ~/git/rustic_core, ~/git/rustic and
#             the BackOtter checkout this script is in (RESTIC_REPO,
#             RUSTIC_CORE_REPO, RUSTIC_REPO, HARNESS_REPO name others); NEW
#             may be empty (OLD..): the working tree, uncommitted changes too
#   REPO:PATH changed paths given directly (REPO restic, rustic_core, rustic
#             or harness; "-" reads them from stdin, one per line); a Go
#             file's build constraint is read from the file as it is now
#   --explain print, for each platform, the paths that chose it and why, and
#             the paths that chose none
# Prints the platforms on one line, space-separated, in run-all.sh's order:
# linux sparse zfsguests dragonfly openbsd_illumos freebsd netbsd (each
# host's run-all.sh skips the other's).
#
# restic: a Go file (or .s) counts for the OSes it builds for, by its
# //go:build line and its _GOOS/_GOARCH name, evaluated for each tested
# OS on amd64 with CGO_ENABLED=0 (build-backotter.sh's build): a file every
# tested OS builds is OS-independent, so all platforms.  rustic_core: a
# file of crates/core/src/backend/osattrs/ counts for the OS it is named
# for (the cfg gates in osattrs.rs); osattrs.rs, sparse.rs and everything
# else in the crates, for all.  rustic: src/, Cargo.*, all; its
# build-backotter.sh, Linux's build only.  _test.go files, tests/,
# snapshots, testdata, *.md, docs and CI: none.  The harness: each script
# for the platforms that run it (rt-common.sh, sparse-rt.sh, run-all.sh:
# all), stick/'s rustic-bko builds for their OS; stick images and guest
# making: none.

set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
die() { echo "affected: $*" >&2; exit 2; }
ALL="linux sparse zfsguests dragonfly openbsd_illumos freebsd netbsd"
TESTED_OS="linux freebsd netbsd openbsd dragonfly illumos"
explain="" ranges=() paths=()
while [[ $# -gt 0 ]]; do
    case $1 in
        --explain) explain=1; shift ;;
        --restic|--rustic-core|--rustic|--harness)
            [[ ${2:-} == *..* ]] || die "$1 takes OLD..NEW"
            r=${1#--}; ranges+=("${r/-/_}=$2"); shift 2 ;;
        -h|--help) sed -n '2,33p' "$0" >&2; exit 2 ;;
        -) while read -r l; do [[ -n $l ]] && paths+=("$l"); done; shift ;;
        -*) die "no such option: $1" ;;
        *:*) paths+=("$1"); shift ;;
        *) die "not REPO:PATH: $1" ;;
    esac
done
[[ ${#ranges[@]} -gt 0 || ${#paths[@]} -gt 0 ]] || { sed -n '2,33p' "$0" >&2; exit 2; }
repo_dir() {
    case $1 in
        restic) echo "${RESTIC_REPO:-$HOME/git/restic}" ;;
        rustic_core) echo "${RUSTIC_CORE_REPO:-$HOME/git/rustic_core}" ;;
        rustic) echo "${RUSTIC_REPO:-$HOME/git/rustic}" ;;
        harness) echo "${HARNESS_REPO:-$(git -C "$here" rev-parse --show-toplevel)}" ;;
        *) die "no such repository: $1 (restic, rustic_core, rustic, harness)" ;;
    esac
}

# why_PLATFORM: the reasons it was chosen (bash 3.2, the Mac's: no
# associative arrays); none: the paths that chose no platform.
none=()
add() {  # REASON PLATFORM...
    local r=$1 p v; shift
    for p in "$@"; do v=why_$p; printf -v "$v" '%s    %s\n' "${!v:-}" "$r"; done
}
os_plats() {  # OS...: the platforms that test them
    local o
    for o in "$@"; do
        case $o in
            linux) echo linux sparse zfsguests ;;
            freebsd) echo freebsd ;; netbsd) echo netbsd ;; dragonfly) echo dragonfly ;;
            openbsd|illumos) echo openbsd_illumos ;;
        esac
    done
}

# Go: does a file named NAME with build line EXPR build for GOOS (amd64)?
GOOSES="aix android darwin dragonfly freebsd hurd illumos ios js linux nacl netbsd openbsd plan9 solaris wasip1 windows zos"
GOARCHES="386 amd64 amd64p32 arm armbe arm64 arm64be loong64 mips mipsle mips64 mips64le mips64p32 mips64p32le ppc ppc64 ppc64le riscv riscv64 s390 s390x sparc sparc64 wasm"
go_builds() {  # GOOS NAME EXPR
    local os=$1 base=${2%.*} e last="" prev="" rest
    base=${base%_test}
    # NAME_GOOS, NAME_GOARCH, NAME_GOOS_GOARCH (a bare GOOS.go is no constraint).
    if [[ $base == *_* ]]; then
        last=${base##*_} rest=${base%_*}
        [[ $rest == *_* ]] && prev=${rest##*_}
    fi
    is() { [[ -n $2 && " $1 " == *" $2 "* ]]; }
    osok() { [[ $1 == "$os" || ( $1 == solaris && $os == illumos ) ]]; }
    if is "$GOARCHES" "$last"; then
        [[ $last == amd64 ]] || return 1
        if is "$GOOSES" "$prev"; then osok "$prev" || return 1; fi
    elif is "$GOOSES" "$last"; then
        osok "$last" || return 1
    fi
    [[ -n $3 ]] || return 0
    # Every tag 1 or 0, then bash's arithmetic: ! && || ( ).
    e=$(awk -v os="$os" -v gooses=" $GOOSES " -v arches=" $GOARCHES " '{
        out = ""; s = $0
        while (match(s, /[A-Za-z_][A-Za-z0-9_.]*/)) {
            t = substr(s, RSTART, RLENGTH)
            v = 0
            if (t == os || (t == "solaris" && os == "illumos") || t == "unix" || t == "amd64" || t == "gc" || t ~ /^go1\./) v = 1
            out = out substr(s, 1, RSTART - 1) v; s = substr(s, RSTART + RLENGTH)
        }
        print out s }' <<<"$3")
    (( e ))
}
restic_file() {  # PATH CONTENT-OLD CONTENT-NEW (as "-" if absent)
    local p=$1 os oses="" c line
    for c in "$2" "$3"; do
        [[ $c == - ]] && continue
        line=$(sed -n '/^package /q; s|^//go:build ||p' <<<"$c" | head -n 1)
        for os in $TESTED_OS; do
            go_builds "$os" "$(basename "$p")" "$line" && [[ " $oses " != *" $os "* ]] && oses+=" $os"
        done
    done
    oses=${oses# }
    if [[ -z $oses ]]; then none+=("restic:$p (builds for no tested OS)")
    elif [[ $(wc -w <<<"$oses") -eq 6 ]]; then add "restic:$p (every tested OS builds it)" $ALL
    else add "restic:$p (builds for $oses only)" $(os_plats $oses); fi
}

classify() {  # REPO PATH OLD-REV NEW-REV (revs empty: the working tree)
    local repo=$1 p=$2 old=$3 new=$4 d o n os
    d=$(repo_dir "$repo")
    case $repo:$p in
        *.md|*:LICENSE*|*:.github/*|*:.gitignore|*:.gitattributes|*:CONTRIBUTORS|*:.codespellrc|*:.golangci.yml|*:.readthedocs.yaml|*:.dockerignore|*:cliff.toml|*:dprint.json|*:release-plz.toml|*:deny.toml|*:.cargo/audit.toml)
            none+=("$repo:$p (docs, notes or CI)"); return ;;
    esac
    case $repo in
    restic)
        case $p in
            doc/*|changelog/*|contrib/*|docker/*|helpers/*|*_test.go|*/testdata/*)
                none+=("restic:$p (tests, docs or release tooling: not in the binary)") ;;
            *.go|*.s)
                if [[ -n $new ]]; then n=$(git -C "$d" show "$new:$p" 2>/dev/null) || n=-
                else n=$(cat "$d/$p" 2>/dev/null) || n=-; fi
                o=-; [[ -n $old ]] && { o=$(git -C "$d" show "$old:$p" 2>/dev/null) || o=-; }
                restic_file "$p" "$o" "$n" ;;
            *) add "restic:$p (not attributable to one OS)" $ALL ;;
        esac ;;
    rustic_core)
        case $p in
            crates/*/tests/*|*/snapshots/*|*.snap|crates/*/benches/*|crates/*/examples/*|examples/*|*/testdata/*)
                none+=("rustic_core:$p (tests: not in the binary)") ;;
            crates/core/src/backend/osattrs/*.rs)
                n=$(basename "$p" .rs)
                case $n in
                    linux) os=linux ;; freebsd) os=freebsd ;; netbsd) os=netbsd ;; openbsd) os=openbsd ;;
                    dragonfly) os=dragonfly ;; illumos*) os=illumos ;;
                    freebsd_netbsd|freebsd_acl) os="freebsd netbsd" ;;
                    bsd_flags) os="freebsd netbsd dragonfly openbsd" ;;
                    other) os="" ;;
                    *) os=$TESTED_OS ;;   # sparse.rs (cfg(unix)) and any new one
                esac
                if [[ -z $os ]]; then none+=("rustic_core:$p (OSes no round tests: other.rs)")
                elif [[ $(wc -w <<<"$os") -eq 6 ]]; then add "rustic_core:$p (all Unix)" $ALL
                else add "rustic_core:$p (osattrs for $os)" $(os_plats $os); fi ;;
            *) add "rustic_core:$p (not attributable to one OS)" $ALL ;;
        esac ;;
    rustic)
        case $p in
            docs/*|config/*|tests/*|*/snapshots/*|coverage/*|Dockerfile|maskfile.md|platform-settings.toml|build.sh|scripts/*)
                none+=("rustic:$p (docs, sample config, tests or upstream release tooling)") ;;
            build-backotter.sh) add "rustic:$p (Linux's build only)" $(os_plats linux) ;;
            *) add "rustic:$p (not attributable to one OS)" $ALL ;;
        esac ;;
    harness)
        local guests="dragonfly openbsd_illumos freebsd netbsd"
        case $p in
            testvm/compare/rt-common.sh|testvm/compare/sparse-rt.sh|testvm/compare/run-all.sh|testvm/compare/verdict.py)
                add "harness:$p (used on every platform)" $ALL ;;
            testvm/compare/nonroot-rt.sh|testvm/compare/nonroot-inner.sh)
                add "harness:$p (nonroot steps)" linux $guests ;;
            testvm/compare/run-guest-rt.sh|testvm/compare/guest-run.sh)
                add "harness:$p (guest round trips)" $guests ;;
            testvm/compare/fs_roundtrip.py|testvm/compare/torture.sh|testvm/compare/fsmeta.py|testvm/compare/compare.py)
                add "harness:$p (fs_roundtrip.py, on the host and in the ZFS guests)" linux zfsguests ;;
            testvm/compare/zfs-guest-rt.sh) add "harness:$p" zfsguests ;;
            testvm/compare/dragonfly/*) add "harness:$p" dragonfly ;;
            testvm/compare/openbsd/*|testvm/compare/illumos/*) add "harness:$p" openbsd_illumos ;;
            testvm/compare/freebsd/rescue.sh) none+=("harness:$p (compare-freebsd.sh only)") ;;
            testvm/compare/freebsd/*) add "harness:$p" freebsd ;;
            testvm/compare/netbsd/*) add "harness:$p" netbsd ;;
            testvm/vm-lib.sh|testvm/vm-run) add "harness:$p (every guest)" zfsguests $guests ;;
            stick/prep-rustic-src.sh|stick/build-round.sh) add "harness:$p (every binary's build)" $ALL ;;
            stick/build-rustic-native.sh) add "harness:$p (native rustic-bko builds)" freebsd netbsd openbsd_illumos ;;
            stick/dragonfly/build-rustic*) add "harness:$p (DragonFly's rustic-bko build)" dragonfly ;;
            stick/openbsd/build-rustic*|stick/illumos/build-rustic*) add "harness:$p (its rustic-bko build)" openbsd_illumos ;;
            stick/freebsd/build-rustic*) add "harness:$p (FreeBSD's rustic-bko build)" freebsd ;;
            stick/netbsd/build-rustic*) add "harness:$p (NetBSD's rustic-bko build)" netbsd ;;
            testvm/compare/affected.sh|testvm/compare/compare-freebsd.sh|testvm/compare/boot-state.sh)
                none+=("harness:$p (not run by a round)") ;;
            stick/*|testvm/*|docs/*)
                none+=("harness:$p (stick images, guest making or docs: not what a round runs; rebuilt guests are another matter)") ;;
            *) none+=("harness:$p (outside testvm/ and stick/)") ;;
        esac ;;
    esac
}

for r in ${ranges[@]+"${ranges[@]}"}; do
    repo=${r%%=*} range=${r#*=} old=${range%%..*} new=${range#*..}
    d=$(repo_dir "$repo")
    git -C "$d" rev-parse --verify -q "$old^{commit}" >/dev/null || die "$repo: no commit $old in $d"
    if [[ -n $new ]]; then
        git -C "$d" rev-parse --verify -q "$new^{commit}" >/dev/null || die "$repo: no commit $new in $d"
        list=$(git -C "$d" diff --no-renames --name-only "$old" "$new")
    else
        list=$( { git -C "$d" diff --no-renames --name-only "$old"; git -C "$d" ls-files --others --exclude-standard; } | sort -u)
    fi
    while read -r p; do [[ -n $p ]] && classify "$repo" "$p" "$old" "$new"; done <<<"$list"
done
for rp in ${paths[@]+"${paths[@]}"}; do
    repo=${rp%%:*} p=${rp#*:}
    repo_dir "$repo" >/dev/null || exit 2
    classify "$repo" "$p" "" ""
done

out=""
for p in $ALL; do v=why_$p; [[ -n ${!v:-} ]] && out+=" $p"; done
echo "${out# }"
if [[ -n $explain ]]; then
    for p in $ALL; do
        v=why_$p; [[ -n ${!v:-} ]] || continue
        echo "$p:"; printf '%s' "${!v}" | sort -u
    done
    if [[ ${#none[@]} -gt 0 ]]; then
        echo "none:"; printf '    %s\n' "${none[@]}" | sort -u
    fi
fi
