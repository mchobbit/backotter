#!/usr/bin/env bash
# GateTest2's slow layers gate:loop:linux@HOST and gate:loop:sparse@HOST
# (testvm/test2/gate/layers.json): run-all.sh's linux steps (fs_roundtrip.py
# on this host's file systems, nonroot-rt.sh linux) or its sparse steps
# (sparse-rt.sh), from a private copy, all in RAM.
#
# Usage (on a KVM test host, as m, from the copy, under
# ~/backotter-work/cmp-run.sh --reserve D M; t2lib/gate.py starts it so):
#   loop.sh linux|sparse --bin DIR --out DIR --prefix PFX [--old DIR|none]
#
#   --bin DIR     the set under test (a round's bin/ or bko-bin)
#   --out DIR     the layer's results on this host (HOST_ROOT/runs/RUN/gate/LAYER):
#                 run-all.out, info.txt, log/ (run-all.sh's LOG_DIR without
#                 work/: summary.json, summary.txt, rows.jsonl, meta.json,
#                 PLATFORM/STEP.log)
#   --prefix PFX  run-all.sh's RUN_PREFIX (bko + letters or digits): pools,
#                 mount points, images and /mnt/bko-ram/PFX-* are this run's
#   --old DIR     OLD_BIN, the set before (SPEC3 2's identity), or none (default)
#
# What it sets: RT_RAM=1 (never 0), PYTHONDONTWRITEBYTECODE=1, RUN_PREFIX,
# HOST_JOBS=2 (linux; run-all.sh's JOBS_CAP makes it 1 on a small host,
# copland or cage) or SPARSE_JOBS=1 (sparse), OLD_BIN, and
# EXPECT_RESTIC_COMMIT, EXPECT_RUSTIC_COMMIT, EXPECT_RUSTIC_CORE_COMMIT from
# the set's linux commit files.  run-all.sh's LOG_DIR is /mnt/bko-ram/PFX-log,
# inside the reservation, and goes to --out at the end.  LINUX_FS and
# SPARSE_FS stay the host's share (run-all.sh's defaults) unless
# T2_LOOP_LINUX_FS or T2_LOOP_SPARSE_FS name others (for tests); the rest of
# the environment (EXPECT_*_VERSION, IMG_GB, BIG) passes through.
# sparse: SPARSE_RAM_GIB, the DISK GiB of the cmp-run.sh --reserve row this
# script runs under (its own PID's or the nearest ancestor's, in the ledger
# /mnt/bko-ram/.reserve, as ht1-lib.sh's ht_reserve_row), goes to run-all.sh,
# so that sparse-rt.sh sizes from the room reserved for it, not from what df
# shows the host's other reserved jobs leaving; info.txt's reserve_gib (none:
# no row).  A sparse-rt.sh that found even that room short worked on disk
# ("sparse-rt: on disk:" in its log): info.txt's ram_on_disk names those
# steps, and gate_judge.py's loop_outcome then learns no RAM peak from the run.
# Every 10 s the size of /mnt/bko-ram/PFX-* and /mnt/bko-ram/nonroot-PFX-*
# (du as root: the steps' files are root's) is sampled; the peak goes to
# info.txt as ram_peak_kib.  Afterwards fs_roundtrip.py's emptied result
# directories in the copy (COPY/fs-roundtrip/PFX-fs, made by root) go too.
#
# Exit: run-all.sh's: 0 every row passed, 1 one didn't, 2 refused; 2 also
# when this script refuses.

set -u
here=$(cd "$(dirname "$0")" && pwd)
die() { echo "loop: $*" >&2; exit 2; }
kind=${1:-}; shift || true
case $kind in linux|sparse) ;; *) die "usage: loop.sh linux|sparse --bin DIR --out DIR --prefix PFX [--old DIR|none]" ;; esac
bin="" out="" pfx="" old=none
while [[ $# -gt 0 ]]; do
    case $1 in
        --bin) bin=${2:-}; shift 2 ;;
        --out) out=${2:-}; shift 2 ;;
        --prefix) pfx=${2:-}; shift 2 ;;
        --old) old=${2:-}; shift 2 ;;
        *) die "unknown argument $1" ;;
    esac
done
[[ -d $bin ]] || die "no --bin directory ${bin:-(none)}"
[[ -n $out ]] || die "no --out DIR"
[[ $pfx =~ ^bko[a-z0-9]+$ ]] || die "--prefix must be bko followed by letters or digits"
[[ $old == none || -d $old ]] || die "no --old directory $old"
mountpoint -q /mnt/bko-ram || die "no /mnt/bko-ram (RAM only)"
sudo -n true 2>/dev/null || die "needs sudo -n (run-all.sh's root steps)"
runall=$(cd "$here/../../compare" 2>/dev/null && pwd)/run-all.sh
copy=$(cd "$here/../../.." && pwd)
[[ -x $runall ]] || die "no $runall"
log=/mnt/bko-ram/$pfx-log
[[ ! -e $log ]] || die "$log is there already: another run's"

first() { head -n 1 "$1" 2>/dev/null | cut -d' ' -f1; }
# The DISK GiB of the cmp-run.sh --reserve row held by this script or its
# nearest ancestor (cmp-run.sh's reserving PID execs sudo, systemd-run, setpriv,
# iolog.sh; a row counts while its PID lives), else nothing.  Read, never locked.
reserve_gib() {
    local p=$$ d i ledger=${CMP_LEDGER_DIR:-/mnt/bko-ram}/.reserve
    for ((i = 0; i < 64; i++)); do
        [[ $p =~ ^[0-9]+$ ]] && ((p > 1)) || return 0
        d=$(awk -v p="$p" '$1 == p { print $2; exit }' "$ledger" 2>/dev/null)
        if [[ -n $d && -d /proc/$p ]]; then echo "$d"; return 0; fi
        p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
    done
}
# The Linux binaries' directory: linux, or linux-arm64 on an aarch64 host
# (Bigfoot: run-all.sh's arm64 profile, docs/design/arm64.md A6).
los=linux; [[ $(uname -m) != aarch64 ]] || los=linux-arm64
rc_commit=$(first "$bin/$los/restic-commit") ru_commit=$(first "$bin/$los/rustic-commit")
core_commit=$(sed -n 's/^rustic_core \([0-9a-f]*\).*/\1/p' "$bin/$los/rustic-commit" 2>/dev/null | head -n 1)
[[ -n $rc_commit && -n $ru_commit && -n $core_commit ]] || die "no commits in $bin/$los/restic-commit, rustic-commit"

mkdir -p "$out" "$log" || die "can't make $out or $log"
rm -f "$out/info.txt" "$out/run-all.out"
sampler=""
cleanup() {
    trap '' INT TERM HUP     # the stop's second signal must not cut this short
    local peak k disk
    [[ -z $sampler ]] || kill "$sampler" 2>/dev/null
    wait 2>/dev/null
    peak=$(cat "$log/.peak" 2>/dev/null)
    k=$(sudo -n du -sxk /mnt/bko-ram/"$pfx"-* /mnt/bko-ram/nonroot-"$pfx"-* 2>/dev/null | awk '{ s += $1 } END { print s + 0 }')
    [[ ${k:-0} -gt ${peak:-0} ]] && peak=$k
    echo "ram_peak_kib=${peak:-0}" >>"$out/info.txt"
    disk=$(grep -rls --exclude-dir=work '^sparse-rt: on disk:' "$log" 2>/dev/null | sed 's|.*/||; s|\.log$||' | sort | tr '\n' ' ')
    [[ -z ${disk// } ]] || echo "ram_on_disk=${disk% }" >>"$out/info.txt"
    if [[ -d $log ]]; then
        mkdir -p "$out/log"
        # The results, without work/ (links to vms/, overlays) or the semaphores.
        (cd "$log" && tar --exclude=./work --exclude=./.sem --exclude=./.peak -cf - . 2>/dev/null) |
            tar -C "$out/log" -xf - 2>/dev/null
        sudo -n rm -rf --one-file-system "$log"
    fi
    # A stop kills fs_roundtrip.py before it clears its image folders, and the reservation
    # ends with this script.  --one-file-system leaves anything still mounted alone.
    [[ -z ${stopped:-} ]] || sudo -n rm -rf --one-file-system /mnt/bko-ram/"$pfx"-* /mnt/bko-ram/nonroot-"$pfx"-*
    # Left by fs_roundtrip.py (root) in the copy, once run-all.sh took its results.
    sudo -n rmdir "$copy/fs-roundtrip/$pfx-fs" "$copy/fs-roundtrip" 2>/dev/null
}
trap cleanup EXIT
trap 'trap "" INT TERM HUP; exit 130' INT TERM HUP
( peak=0; while :; do
      k=$(sudo -n du -sxk /mnt/bko-ram/"$pfx"-* /mnt/bko-ram/nonroot-"$pfx"-* 2>/dev/null | awk '{ s += $1 } END { print s + 0 }')
      [[ ${k:-0} -gt $peak ]] && { peak=$k; echo "$peak" >"$log/.peak"; }
      sleep 10
  done ) &
sampler=$!

env=(RT_RAM=1 PYTHONDONTWRITEBYTECODE=1 RUN_PREFIX="$pfx" OLD_BIN="$old"
     EXPECT_RESTIC_COMMIT="$rc_commit" EXPECT_RUSTIC_COMMIT="$ru_commit" EXPECT_RUSTIC_CORE_COMMIT="$core_commit")
if [[ $kind == linux ]]; then
    env+=(HOST_JOBS=2)
    [[ -z ${T2_LOOP_LINUX_FS:-} ]] || env+=(LINUX_FS="$T2_LOOP_LINUX_FS")
else
    env+=(SPARSE_JOBS=1)
    res=$(reserve_gib)
    [[ -z $res ]] || env+=(SPARSE_RAM_GIB="$res")
    [[ -z ${T2_LOOP_SPARSE_FS:-} ]] || env+=(SPARSE_FS="$T2_LOOP_SPARSE_FS")
fi
{
    echo "kind=$kind"
    echo "prefix=$pfx"
    echo "bin=$bin"
    echo "old_bin=$old"
    echo "copy=$copy"
    echo "host=$(hostname -s)"
    echo "linux_fs=${T2_LOOP_LINUX_FS:-default}"
    echo "sparse_fs=${T2_LOOP_SPARSE_FS:-default}"
    [[ $kind != sparse ]] || echo "reserve_gib=${res:-none}"
    echo "start=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
} >"$out/info.txt"
# In the background, so that a TERM reaches run-all.sh (whose own trap then
# stops its steps and destroys its pools) instead of waiting for it.
env "${env[@]}" "$runall" --platforms "$kind" "$bin" "$log" >"$out/run-all.out" 2>&1 </dev/null &
child=$!
stopped=""
trap 'stopped=1; kill -TERM "$child" 2>/dev/null' INT TERM HUP
wait "$child"; rc=$?
while kill -0 "$child" 2>/dev/null; do wait "$child"; rc=$?; done
{ echo "rc=$rc"; echo "end=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"; [[ -z $stopped ]] || echo "stopped=1"; } >>"$out/info.txt"
[[ -z $stopped ]] || exit 130
exit $rc
