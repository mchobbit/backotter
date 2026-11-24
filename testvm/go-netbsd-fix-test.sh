#!/usr/bin/env bash
# go-netbsd-fix.sh's go_ver_of and go_netbsd_avx_fixed on what `go version`,
# `go version FILE` and restic-commit print.  Usage: testvm/go-netbsd-fix-test.sh
# Prints a FAIL line for each case that goes wrong and a summary; exit 1 if any.
set -u
here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=go-netbsd-fix.sh
. "$here/go-netbsd-fix.sh"
n=0 bad=0
fixed() {  # WANT(0 fixed, 1 not, 2 no release) TEXT
    local rc=0
    n=$((n + 1))
    go_netbsd_avx_fixed "$2" || rc=$?
    [[ $rc == "$1" ]] || { echo "FAIL fixed '$2': exit $rc, not $1"; bad=$((bad + 1)); }
}
ver() {  # WANT TEXT (WANT empty: none)
    local got rc=0
    n=$((n + 1))
    got=$(go_ver_of "$2") || rc=$?
    if [[ -z $1 ]]; then
        [[ $rc == 1 && -z $got ]] || { echo "FAIL ver '$2': '$got' (exit $rc), not none"; bad=$((bad + 1)); }
    else
        [[ $rc == 0 && $got == "$1" ]] || { echo "FAIL ver '$2': '$got' (exit $rc), not $1"; bad=$((bad + 1)); }
    fi
}

ver go1.26.10 "go version go1.26.10 linux/amd64"
ver go1.25.10 "/home/m/backotter-work/bko-bin/netbsd/restic-bko: go1.25.10"
ver go1.26.10 "go version go1.26.10 linux/amd64, built 2026-11-22 09:07 UTC on czerny"
ver go1.26.10 "go version go1.26.10 X:boringcrypto linux/amd64"
ver go1.27 "go1.27"
ver "" "go version go1.27rc1 linux/amd64"
ver "" "go version devel go1.28-6b8c3f2 Mon Nov 23 2026 linux/amd64"
ver "" "/home/m/sdk/go1.25.10/bin/go"
ver "" ""
ver go1.26.10 "$(printf 'f707056 (from src)\nsource digest 2162\ngo version go1.26.10 linux/amd64, built')"

fixed 1 "go version go1.25.10 linux/amd64"
fixed 1 "/x/restic-bko: go1.25.10"
fixed 1 "go version go1.25.99 linux/amd64"
fixed 1 "go version go1.26.0 linux/amd64"
fixed 1 "go version go1.26.7 linux/amd64"
fixed 0 "go version go1.26.8 linux/amd64"
fixed 0 "go version go1.26.9 linux/amd64"
fixed 0 "go version go1.26.10 linux/amd64"
fixed 0 "/x/restic-bko: go1.26.10"
fixed 1 "go version go1.26 linux/amd64"
fixed 0 "go version go1.27.0 linux/amd64"
fixed 0 "go version go1.27 linux/amd64"
fixed 0 "go version go1.27.3 linux/amd64"
fixed 0 "go version go1.30.1 linux/amd64"
fixed 1 "go version go1.9.7 linux/amd64"
fixed 1 "go version go1.08.9 linux/amd64"
fixed 2 "go version go1.27rc1 linux/amd64"
fixed 2 "go version devel go1.28-6b8c3f2 linux/amd64"
fixed 2 ""
fixed 2 "no go"
[[ -n $GO_NETBSD_FIX_RULE ]] || { echo "FAIL no GO_NETBSD_FIX_RULE"; bad=$((bad + 1)); }

# stick/build-round.sh's netbsd_go_bad and netbsd_embeds, taken out of it, with
# a fake go (go version FILE prints FAKE_GO's version), log and row.
T=$(mktemp -d "${TMPDIR:-/tmp}/go-netbsd-fix-test.XXXXXX") || exit 2
trap 'rm -rf "$T"' EXIT
awk '/^netbsd_go_bad\(\) \{$/ || /^netbsd_embeds\(\) \{$/ { f = 1 } f { print } f && /^}$/ { f = 0 }' \
    "$here/../stick/build-round.sh" >"$T/fns.sh"
[[ $(grep -c '^netbsd_' "$T/fns.sh") == 2 ]] || { echo "FAIL: no netbsd_go_bad and netbsd_embeds in stick/build-round.sh"; exit 1; }
# shellcheck source=/dev/null
. "$T/fns.sh"
mkdir -p "$T/bin" "$T/BIN/netbsd" "$T/rows"
printf '#!/bin/sh\n[ "$1" = version ] && echo "$2: $FAKE_GO"\n' >"$T/bin/go" && chmod 755 "$T/bin/go"
PATH=$T/bin:$PATH
log() { echo "$*" >>"$T/log"; }
row() { echo "$*" >>"$T/rowlog"; }
case_() {  # NAME WANT-RC WANT-LOG-GREP (-: nothing logged) CMD...
    local nm=$1 want=$2 pat=$3 rc=0
    shift 3
    n=$((n + 1))
    : >"$T/log"; : >"$T/rowlog"
    "$@" || rc=$?
    if [[ $rc != "$want" ]] || { [[ $pat == - ]] && [[ -s $T/log ]]; } || { [[ $pat != - ]] && ! grep -q -- "$pat" "$T/log"; }; then
        echo "FAIL $nm: exit $rc (not $want), log: $(cat "$T/log")"; bad=$((bad + 1))
    fi
}
unset BKO_ALLOW_FIXLESS_GO
case_ "bad: go1.25.10" 0 "refused: Go is go version go1.25.10" netbsd_go_bad "go version go1.25.10 linux/amd64" "Go is"
case_ "bad: go1.26.10" 1 - netbsd_go_bad "go version go1.26.10 linux/amd64" "Go is"
BKO_ALLOW_FIXLESS_GO=1 case_ "bad: allowed" 1 "WARNING: restic-bko netbsd" netbsd_go_bad "go version go1.25.10 linux/amd64" "Go is"
dry=no dirs="linux netbsd" BIN=$T/BIN rows=$T/rows t0=$SECONDS
for g in go1.26.10 go1.25.10; do
    echo bin >"$T/BIN/netbsd/restic-bko"; echo c >"$T/BIN/netbsd/restic-commit"; echo f >"$T/rows/restic-bko-netbsd.files"
    FAKE_GO=$g
    export FAKE_GO
    if [[ $g == go1.26.10 ]]; then
        case_ "embeds $g" 0 "embeds go1.26.10" netbsd_embeds
        [[ -f $T/BIN/netbsd/restic-bko && ! -s $T/rowlog ]] || { echo "FAIL embeds $g: binary taken out"; bad=$((bad + 1)); }
    else
        case_ "embeds $g" 0 "refused: $T/BIN/netbsd/restic-bko embeds go1.25.10" netbsd_embeds
        [[ ! -e $T/BIN/netbsd/restic-bko && ! -e $T/BIN/netbsd/restic-commit && ! -e $T/rows/restic-bko-netbsd.files ]] &&
            grep -q '^restic-bko netbsd FAILED' "$T/rowlog" || { echo "FAIL embeds $g: not taken out"; bad=$((bad + 1)); }
    fi
done
dirs="linux"
echo bin >"$T/BIN/netbsd/restic-bko"
case_ "embeds: no netbsd dir" 0 - netbsd_embeds
[[ -f $T/BIN/netbsd/restic-bko ]] || { echo "FAIL: netbsd_embeds without netbsd in dirs took it"; bad=$((bad + 1)); }

echo "go-netbsd-fix-test: $((n - bad)) of $n passed"
[[ $bad == 0 ]]
