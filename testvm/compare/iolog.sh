#!/usr/bin/env bash
# What a test job wrote to the host's disks, for the wear log.
#
# Usage (on a KVM test host):
#   iolog.sh WHAT -- CMD ...
#       runs CMD, then appends one line to WEAR_LOG for this process's cgroup;
#   iolog.sh --cgroup DIR WHAT
#       appends the line for another cgroup (compare.py's qemu-nbd scopes).
#
# cmp-run.sh runs every job this way, in a systemd scope of its own with
# IOAccounting=yes: cgroup v2 then counts the writes of everything in the
# scope, the guest's QEMU and the page cache's writeback for it included.
# Writes to tmpfs (/mnt/bko-ram) and zram reach no disk, so they don't count:
# that is the point.  Before reading, the line syncs, so that what the job
# left dirty is written (and charged to it) first.
#
# WEAR_LOG (default /bkoscratch/wear/jobs.log), tab-separated:
#   END_UTC HOST SCOPE RC SECONDS DISK=BYTES[,DISK=BYTES...] WHAT
# e.g. 2026-11-07T05:10:02Z cinqcent bko-st1-5-1762490000.scope 0 812 nvme0n1=4318765056 st1 debian13
# A qemu-nbd scope's line has SCOPE its own and WHAT "nbd for JOB_SCOPE";
# wear-report.sh adds it to its job's.  WEAR_LOG=off logs nothing.
set -u

line() {  # CGROUP_DIR RC SECONDS WHAT
    local cg=$1 rc=$2 secs=$3 what=$4 log=${WEAR_LOG:-/bkoscratch/wear/jobs.log} out="" mm rest w name
    [ "$log" = off ] && return 0
    [ -r "$cg/io.stat" ] || return 0
    sync
    while read -r mm rest; do
        w=$(sed -n 's/.*wbytes=\([0-9]*\).*/\1/p' <<<"$rest")
        [ -n "$w" ] && [ "$w" -gt 0 ] || continue
        name=$(basename "$(readlink -f "/sys/dev/block/$mm")")
        out+="${out:+,}$name=$w"
    done <"$cg/io.stat"
    mkdir -p "$(dirname "$log")" 2>/dev/null
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$(hostname -s)" "$(basename "$cg")" \
        "$rc" "$secs" "${out:--}" "$what" >>"$log" 2>/dev/null || true
}

if [ "${1:-}" = --cgroup ]; then
    line "$2" - - "$3"
    exit 0
fi
[ $# -ge 3 ] && [ "$2" = -- ] || { echo "usage: $0 WHAT -- CMD ... | $0 --cgroup DIR WHAT" >&2; exit 2; }
what=$1; shift 2
cg=/sys/fs/cgroup$(sed -n 's/^0:://p' /proc/self/cgroup)
t0=$(date +%s)
# In the background, so that a TERM (a reservation ended with kill) still
# gets its line: passed on to CMD, then logged with rc 143.
# (<&0: a background command's stdin would be /dev/null otherwise.)
"$@" <&0 &
child=$!
trap 'kill -TERM "$child" 2>/dev/null; wait "$child"; line "$cg" 143 "$(($(date +%s) - t0))" "$what"; exit 143' TERM INT HUP
wait "$child"
rc=$?
line "$cg" "$rc" "$(($(date +%s) - t0))" "$what"
exit $rc
