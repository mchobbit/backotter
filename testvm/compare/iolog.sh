#!/usr/bin/env bash
# What a test job wrote to the host's disks, for the wear log.
#
# Usage (on a KVM test host):
#   [WEAR_TAG=TAG] iolog.sh WHAT -- CMD ...
#       runs CMD, then appends one line to WEAR_LOG for this process's cgroup;
#   [WEAR_TAG=TAG] iolog.sh --cgroup DIR WHAT
#       appends the line for another cgroup (compare.py's qemu-nbd scopes).
#
# cmp-run.sh runs every job this way, in a systemd scope of its own with
# IOAccounting=yes: cgroup v2 then counts the writes of everything in the
# scope, the guest's QEMU and the page cache's writeback for it included.
# io.stat names whole disks, and also the zram, nbd, loop, md, dm and zd (ZFS
# volume) devices the job wrote to.  They are all logged, and wear-report.py
# counts only the physical disks: writes to tmpfs (/mnt/bko-ram) and zram
# reach no disk, so they don't count, which is the point.  Before reading,
# the line syncs, so that what the job left dirty is written (and charged to
# it) first.
#
# WEAR_LOG (default /bkoscratch/wear/jobs.log), tab-separated:
#   END_UTC HOST SCOPE RC SECONDS DISK=BYTES[,DISK=BYTES...] WHAT [KEY=VALUE...]
# e.g. 2026-11-07T05:10:02Z cinqcent bko-st1-5-1762490000.scope 0 812 nvme0n1=4318765056 st1 debian13 tag=cmp:st1:debian13 ...
# WHAT's tabs, CRs and newlines become spaces, and it is cut at 400
# characters.  The KEY=VALUE fields after it are newer than the rest (older
# lines have none).  A reader skips a key it doesn't know, and a missing key
# means unknown:
#   tag=TAG           WEAR_TAG, FAMILY:RUN[:CELL] (ht1:r4all:pop24), with
#                     characters other than A-Za-z0-9._:+@- made _, cut at 160
#   serial=DISK:SERIAL,...  the physical disks' serials (Chopin's NVMe names
#                     swap between boots)
#   swpeak=BYTES      the most swap, zram and disk, the cgroup held at once
#   mpeak=BYTES       the cgroup's memory peak, tmpfs pages and page cache included
#   host_dswap=BYTES  what the host wrote to its disk swap while the job ran,
#   host_zram=BYTES   and to zram: everyone's, not the job's (the kernel counts
#                     neither per cgroup); not with --cgroup
# A qemu-nbd scope's line has SCOPE its own and WHAT "nbd for JOB_SCOPE";
# wear-report.py adds it to its job's.  WEAR_LOG=off logs nothing.
set -u
tag=${WEAR_TAG:-}; tag=${tag//[!A-Za-z0-9._:+@-]/_}; tag=${tag:0:160}; s0=""

swapw() {  # -> "DISK ZRAM": bytes the host has written to its swap devices (/proc/swaps)
    awk 'FNR == NR { if (FNR > 1) { n = $1; sub(/.*\//, "", n); sw[n] = 1 } next }
         ($3 in sw) { if ($3 ~ /^zram/) z += $10 * 512; else d += $10 * 512 }
         END { printf "%.0f %.0f\n", d, z }' /proc/swaps /proc/diskstats 2>/dev/null
}

line() {  # CGROUP_DIR RC SECONDS WHAT
    local cg=$1 rc=$2 secs=$3 what=$4 log=${WEAR_LOG:-/bkoscratch/wear/jobs.log} out="" ser="" x="" \
        mm rest w name dev
    [ "$log" = off ] && return 0
    [ -r "$cg/io.stat" ] || return 0
    sync
    while read -r mm rest; do
        w=$(sed -n 's/.*wbytes=\([0-9]*\).*/\1/p' <<<"$rest")
        [ -n "$w" ] && [ "$w" -gt 0 ] || continue
        dev=$(readlink -f "/sys/dev/block/$mm") name=${dev##*/}
        out+="${out:+,}$name=$w"
        case $dev in /sys/devices/virtual/block/*) continue ;; esac   # zram, nbd, loop, md, dm, zd
        w=$(cat "/sys/block/$name/device/serial" 2>/dev/null || lsblk -dno SERIAL "/dev/$name" 2>/dev/null)
        w=${w//[!A-Za-z0-9._-]/}
        [ -n "$w" ] && ser+="${ser:+,}$name:$w"
    done <"$cg/io.stat"
    [ -n "$tag" ] && x+=$'\t'"tag=$tag"
    [ -n "$ser" ] && x+=$'\t'"serial=$ser"
    w=$(cat "$cg/memory.swap.peak" 2>/dev/null); [[ $w =~ ^[0-9]+$ ]] && x+=$'\t'"swpeak=$w"
    w=$(cat "$cg/memory.peak" 2>/dev/null); [[ $w =~ ^[0-9]+$ ]] && x+=$'\t'"mpeak=$w"
    # The host's swap writes since the start (s0; a --cgroup line has none).
    if [ -n "$s0" ] && [[ "$s0 $(swapw)" =~ ^([0-9]+)\ ([0-9]+)\ ([0-9]+)\ ([0-9]+)$ ]]; then
        w=$((10#${BASH_REMATCH[3]} - 10#${BASH_REMATCH[1]})); [ "$w" -ge 0 ] && x+=$'\t'"host_dswap=$w"
        w=$((10#${BASH_REMATCH[4]} - 10#${BASH_REMATCH[2]})); [ "$w" -ge 0 ] && x+=$'\t'"host_zram=$w"
    fi
    what=${what//[$'\t\r\n']/ }; [ ${#what} -le 400 ] || what="${what:0:400}..."
    # A new log is made 666, as wear-setup.sh makes jobs.log: root (a queue's
    # jobs) and m (a --reserve's) both append to it.
    [ -e "$log" ] || { mkdir -p "$(dirname "$log")" && : >>"$log" && chmod 666 "$log"; } 2>/dev/null
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s%s\n' "$(date -u +%FT%TZ)" "$(hostname -s)" "$(basename "$cg")" \
        "$rc" "$secs" "${out:--}" "$what" "$x" >>"$log" 2>/dev/null || true
}

reap() {  # -> rc, CMD's exit status, once CMD is gone (a signal ends a wait early)
    local r
    wait "$child"; r=$?
    while kill -0 "$child" 2>/dev/null; do wait "$child"; r=$?; done
    rc=$r
}

if [ "${1:-}" = --cgroup ]; then
    line "$2" - - "$3"
    exit 0
fi
[ $# -ge 3 ] && [ "$2" = -- ] || { echo "usage: $0 WHAT -- CMD ... | $0 --cgroup DIR WHAT" >&2; exit 2; }
what=$1; shift 2
cg=/sys/fs/cgroup$(sed -n 's/^0:://p' /proc/self/cgroup)
t0=$(date +%s) s0=$(swapw)
# In the background, so that a TERM (a reservation ended with kill) still
# gets its line: passed on to CMD, then logged with CMD's own status (143
# when the TERM ended it) once CMD has ended, and not before: else the end
# of the reservation could come while CMD runs.  A signal once CMD has ended
# (during line()) logs CMD's status as it is.
# (<&0: a background command's stdin would be /dev/null otherwise.)
"$@" <&0 &
child=$! rc=""
trap '[ -n "$rc" ] || { kill -TERM "$child" 2>/dev/null; reap; }
      line "$cg" "$rc" "$(($(date +%s) - t0))" "$what"; exit "$rc"' TERM INT HUP
reap
line "$cg" "$rc" "$(($(date +%s) - t0))" "$what"
exit "$rc"
