#!/usr/bin/env bash
# GateTest2's gate:fork-root:restic layer (testvm/test2/gate/layers.json):
# the restic fork's root-only tests, run as root, in RAM.  Only the tests
# restic-root-tests.txt lists ("run" lines, each reviewed) run as root; the
# rest of the suite stays m's in testvm/audit/fork-tests.sh, since tests that
# expect a permission error can't pass as root, and root-built files in m's
# GOCACHE would break m's later runs.
#
# Usage (on Chopin, as m, from a private copy, under
# ~/backotter-work/cmp-run.sh --reserve 1 2; t2lib/gate.py starts it so):
#   fork-root.sh --commit HEX --out DIR [--ram DIR] [--list FILE]
#
#   --commit HEX  the restic commit (40 hex); its tree is fork-tests.sh's,
#                 /bkoscratch/bko-audit/src/restic-SHORT (SHORT: 12 hex),
#                 which fork-tests.sh (or gate.py, with audit-lib.sh's
#                 ship_src) put there
#   --out DIR     the layer's results on this host (HOST_ROOT/runs/RUN/gate/fork-root-restic):
#                 info.txt; build.tsv (PKG RC) and build-PKG.log; run.tsv
#                 (PKG RC SECS; RC 1 a test failed, else its binary's
#                 status) and run-PKG.log (the test binaries' -test.v
#                 output); skips.tsv (PKG TEST MESSAGE: the m run's skips,
#                 from fork-tests.sh's test.json of the same commit, when
#                 there); cleanup.txt (what was left mounted or attached)
#   --ram DIR     the reserved RAM directory (default /mnt/bko-ram/t2g-forkroot-PID):
#                 the test binaries, TMPDIR and HOME of the root run; made
#                 here and removed at the end, however the run ends
#   --list FILE   default restic-root-tests.txt beside this script
#
# How: as m, `go test -c` each package of the list in the source tree with
# fork-tests.sh's toolchain and caches (go.mod's toolchain line,
# ~/sdk/goVERSION, GOCACHE and GOMODCACHE in /bkoscratch/bko-audit/cache,
# GOTOOLCHAIN=local, GOFLAGS=-modcacherw), the binary into the RAM directory;
# then, in the package's directory, for each listed test, `sudo -n env PATH=...
# TMPDIR=RAM/tmp HOME=RAM/home PKG.test -test.run '^T$' -test.v -test.count=1`,
# one process per test, the outputs in one log.
# Without mkfs.bcachefs on the host, BKO_MKFS_BCACHEFS and a bcachefs
# command (RAM/path/bcachefs) run the Arch ISO's tools, as run-all.sh does
# (/bkoscratch/tools/arch-root, or $BKO_ARCH_ROOT).
# Afterwards what the tests left mounted under the RAM directory is unmounted
# and the loop devices whose backing file is there are detached, by path,
# never by name; cleanup.txt and the output say what.  t2lib/gate.py judges
# the logs: one need per listed test (PASS, FAIL, or SKIP with the test's
# reason) and root-list (no unreviewed root skip in skips.tsv).
#
# Exit: 0 when it ran to its end (the logs say how the tests went), 2 when
# it refused (no tree, no go, no sudo -n, no list).
#
#   BKO_AUDIT_GO  the go binary (as audit-host.sh)

set -u
here=$(cd "$(dirname "$0")" && pwd)
die() { echo "fork-root: $*" >&2; exit 2; }
log() { echo "$(date -u '+%H:%M:%S') fork-root: $*"; }
commit="" out="" ram="" list=$here/restic-root-tests.txt
while [[ $# -gt 0 ]]; do
    case $1 in
        --commit) commit=${2:-}; shift 2 ;;
        --out) out=${2:-}; shift 2 ;;
        --ram) ram=${2:-}; shift 2 ;;
        --list) list=${2:-}; shift 2 ;;
        *) die "usage: fork-root.sh --commit HEX --out DIR [--ram DIR] [--list FILE]" ;;
    esac
done
[[ $commit =~ ^[0-9a-f]{40}$ ]] || die "--commit: 40 hex digits"
[[ -n $out ]] || die "no --out DIR"
[[ -f $list ]] || die "no list $list"
CACHE=/bkoscratch/bko-audit/cache
src=/bkoscratch/bko-audit/src/restic-${commit:0:12}
[[ $(cat "$src/.bko-src" 2>/dev/null) == "$commit" ]] || die "no source tree of restic $commit at $src (fork-tests.sh ships it)"
sudo -n true 2>/dev/null || die "needs sudo -n"
mountpoint -q /mnt/bko-ram || die "no /mnt/bko-ram"
[[ -n $ram ]] || ram=/mnt/bko-ram/t2g-forkroot-$$
# One new directory right under /mnt/bko-ram (the EXIT trap removes it): no "..", no
# deeper path, no dot name such as .reserve.
[[ $ram =~ ^/mnt/bko-ram/[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "--ram must be one directory name under /mnt/bko-ram (RAM only): $ram"
[[ ! -e $ram ]] || die "$ram is there already: another run's"

tc=$(sed -n 's/^toolchain go//p' "$src/go.mod" | head -n 1)
[[ -n $tc ]] || tc=$(sed -n 's/^go \([0-9.]*\)$/\1/p' "$src/go.mod" | head -n 1)
go=${BKO_AUDIT_GO:-}
if [[ -z $go ]]; then
    if [[ -x $HOME/sdk/go$tc/bin/go ]]; then go=$HOME/sdk/go$tc/bin/go; else go=$(command -v go) || die "no go (~/sdk/go$tc)"; fi
fi
# The list's "run" lines: PACKAGE TEST.
runs=$(awk '$1 == "run" && NF >= 3 { print $2, $3 }' "$list")
[[ -n $runs ]] || die "no run lines in $list"
pkgs=$(awk '{ print $1 }' <<<"$runs" | sort -u)

mkdir -p "$out" || die "can't make $out"
rm -f "$out"/*.tsv "$out"/*.log "$out/info.txt" "$out/cleanup.txt"
mkdir -p "$ram/bin" "$ram/tmp" "$ram/home" "$ram/path" || die "can't make $ram"

cleanup() {
    trap '' INT TERM HUP     # the stop's second signal must not cut this short
    local m l f
    # Mounts under the RAM directory, deepest first, by path.
    findmnt -rn -o TARGET 2>/dev/null | awk -v r="$ram/" 'index($0, r) == 1' | sort -r | while read -r m; do
        sudo -n umount -l "$m" 2>/dev/null && echo "unmounted $m" >>"$out/cleanup.txt"
    done
    # Loop devices whose backing file is under it, by path.
    sudo -n losetup -l -n -O NAME,BACK-FILE 2>/dev/null | while read -r l f; do
        case $f in "$ram"/*) sudo -n losetup -d "$l" 2>/dev/null && echo "detached $l ($f)" >>"$out/cleanup.txt" ;; esac
    done
    if [[ -s $out/cleanup.txt ]]; then sed 's/^/fork-root: cleanup: /' "$out/cleanup.txt"; else : >"$out/cleanup.txt"; fi
    # A test that died with a flag set leaves an immutable file behind.
    sudo -n chattr -R -ia "$ram" 2>/dev/null
    sudo -n rm -rf --one-file-system "$ram"
}
trap cleanup EXIT
trap 'trap "" INT TERM HUP; exit 130' INT TERM HUP

export GOTOOLCHAIN=local GOFLAGS=-modcacherw GOCACHE=$CACHE/gocache GOMODCACHE=$CACHE/gomod
export PATH=$(dirname "$go"):$PATH LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1
penv=()
A=${BKO_ARCH_ROOT:-/bkoscratch/tools/arch-root}
if ! command -v mkfs.bcachefs >/dev/null && [[ -x $A/usr/bin/bcachefs ]]; then
    bc="env GLIBC_TUNABLES=glibc.malloc.tcache_count=0 $A/usr/lib/ld-linux-x86-64.so.2 --library-path $A/usr/lib $A/usr/bin/bcachefs"
    penv+=(BKO_MKFS_BCACHEFS="$bc format")
    printf '#!/bin/sh\nexec %s "$@"\n' "$bc" >"$ram/path/bcachefs" && chmod 755 "$ram/path/bcachefs"
    bcachefs="the Arch ISO's ($A)"
elif command -v mkfs.bcachefs >/dev/null; then bcachefs="the host's"
else bcachefs="none"; fi
{
    echo "commit=$commit"
    echo "src=$src"
    echo "go=$("$go" version 2>&1)"
    echo "packages=$(echo $pkgs)"
    echo "bcachefs=$bcachefs"
    echo "host=$(hostname -s)"
    echo "ram=$ram"
} >"$out/info.txt"

for p in $pkgs; do
    f=${p//\//_}
    log "build $p"
    (cd "$src" && "$go" test -c -o "$ram/bin/$f.test" "./$p") >"$out/build-$f.log" 2>&1
    rc=$?
    printf '%s\t%s\n' "$p" "$rc" >>"$out/build.tsv"
    [[ $rc -eq 0 && -x $ram/bin/$f.test ]] || { log "build $p failed: exit $rc"; continue; }
    # One process per test: the tests mount and unmount loop devices, and the
    # package caches each device number's file system type (internal/fs's
    # fsTypes), which holds for a mount that stays but not for a loop device
    # the next test reuses (TestNodeProjectIDBcachefs read a stale vfat entry
    # as "no project ID").  The binary's output of each test goes to one log.
    tests=$(awk -v p="$p" '$1 == p { print $2 }' <<<"$runs")
    log "run $p as root: $(echo $tests)"
    t0=$(date +%s)
    rc=0
    : >"$out/run-$f.log"
    for t in $tests; do
        (cd "$src/$p" && sudo -n env PATH="$ram/path:/usr/sbin:/usr/bin:/sbin:/bin" TMPDIR="$ram/tmp" HOME="$ram/home" \
            LC_ALL=C.UTF-8 "${penv[@]}" "$ram/bin/$f.test" -test.run "^$t\$" -test.v -test.count=1 -test.timeout=20m) \
            >>"$out/run-$f.log" 2>&1 </dev/null
        trc=$?
        # The package's status: 1 a test failed, any other non-zero its binary died.
        if [[ $trc -ne 0 && ( $rc -eq 0 || $rc -eq 1 ) ]]; then rc=$trc; fi
    done
    printf '%s\t%s\t%s\n' "$p" "$rc" "$(( $(date +%s) - t0 ))" >>"$out/run.tsv"
    log "run $p: exit $rc"
done

# The m run's skips of the same commit (fork-tests.sh's test.json), for the
# root-list need: top-level tests only, one line each.
tj=$HOME/backotter-work/bko-audit/fork/restic-${commit:0:12}/test.json
if [[ -f $tj ]] && grep -qx "commit=$commit" "$(dirname "$tj")/info.txt" 2>/dev/null; then
    python3 -I - "$tj" >"$out/skips.tsv" <<'EOF'
import collections, json, sys
out, skips = collections.defaultdict(list), set()
for line in open(sys.argv[1], errors="replace"):
    if not line.startswith("{"):
        continue
    try:
        e = json.loads(line)
    except ValueError:
        continue
    pkg, test = e.get("Package") or "", e.get("Test") or ""
    if not test or "/" in test:
        continue
    k = (pkg.replace("github.com/restic/restic/", ""), test)
    if e.get("Action") == "output":
        out[k].append(e.get("Output", ""))
    elif e.get("Action") == "skip":
        skips.add(k)
for k in sorted(skips):
    msg = " | ".join(l.strip() for l in out[k] if l.strip() and not l.startswith(("=== ", "--- ")))
    print("%s\t%s\t%s" % (k[0], k[1], msg.replace("\t", " ")[:300]))
EOF
else
    echo "no m run of restic $commit here ($tj): root-list not checked" >"$out/skips.none"
fi
log "done; results in $out"
exit 0
