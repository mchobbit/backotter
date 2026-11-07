#!/usr/bin/env bash
# compare.py over a queue of guests, up to SLOTS at once, BackOtter's four
# directions each, in RAM: compare.py works in /mnt/bko-ram by itself and
# leaves its results, diffs and logs in ~/backotter-work/compare/GUEST
# (GUEST.stick-mkfs with :STICK). Every job gets the archivers in
# ~/backotter-work/cmp-bin (RESTIC_BKO, RUSTIC_BKO), not compare.py's newest
# build.
#
# Usage (on a KVM test host; it works in ~/backotter-work, where
# sync-hosts.sh puts testvm/, and ~/backotter-work/cmp-run.sh links here):
#   cmp-run.sh TAG SLOTS GUEST[:STICK][@GROW_GIB][!][=DISK_GIB][%MEM_GIB] ...
#       writes the queue cmp-logs/TAG/queue on this host and works on it;
#   QUEUE_HOST=chopin.local cmp-run.sh TAG SLOTS
#       works on that host's queue (over ssh), so both hosts share one;
#   cmp-run.sh --reserve DISK_GIB MEM_GIB CMD ...
#       runs CMD once its room is free, holding the room meanwhile.
# A "!" puts the guest's restored disks on DISKS_DIR (default
# /bkoscratch/compare-disks, a cool disk) instead of the RAM disk; its
# DISK_GIB then counts only what stays in RAM (repositories, overlays).
# touch cmp-logs/TAG/stop on a host: its workers start nothing more (with
# QUEUE_HOST, on each host that runs workers). Lines taken from the queue
# are in cmp-logs/TAG/queue.taken on the queue's host; cmp-pop.sh says how to
# add one back. Slots are SLOT_BASE+1 .. SLOT_BASE+SLOTS (SLOT_BASE default
# 4, so another run on the host can take 1-4); compare.py takes 0-9 only.
#
# Nothing starts unless it fits, so neither the RAM disk nor memory runs out:
#   RAM disk: the DISK_GIB (default 8) of everything running stays within
#     the tmpfs, less 1 GiB;
#   memory, static: the MEM_GIB (default 5) of everything running fits in
#     MemTotal after the whole tmpfs, the ARC and BASE_GIB (default 6) for the
#     system, as if the tmpfs were full;
#   memory, live (other sessions): MemAvailable, less the tmpfs's room to
#     grow, the ARC's, swap in use, 2 GiB, and what running jobs have reserved
#     but not touched yet (MEM_GIB less their cgroup's anon; for a --reserve,
#     which has no cgroup of its own, less the anon of CMD's process tree, the
#     guest's QEMU included), covers it.
#   The ARC counts as its size now plus ARC_ROOM_GIB (default 1), within its
#     cap: not the whole cap (zfs_arc_max, 8 GiB on the hosts), which only the
#     host's small throwaway test pools ever use, and the ARC gives memory back
#     under pressure.
# Each job runs in a systemd scope with MemoryHigh = DISK + MEM GiB (its
# tmpfs pages count there too): past that it is slowed and reclaimed into
# zram, never killed. zram (16 GiB) is the cushion under all of it.
# Every job, a --reserve's CMD too, runs under iolog.sh, which logs what it
# wrote to disk in /bkoscratch/wear/jobs.log (wear-report.sh sums it up).
# Reservations live in /mnt/bko-ram/.reserve, shared by every cmp-run.sh on
# the host. Nothing new starts while the CPU is over HOT_C (default 95) or an
# NVMe over NVME_C (default 75).
#
# First come, first served: a --reserve, or a worker whose queue's first line
# doesn't fit, that has to wait takes a place in line (/mnt/bko-ram/.reserve.wait,
# PID DISK MEM HOT_C WHAT...), and room is offered to it only after what everyone
# before it in line asks for. So a big job isn't starved by small ones that
# keep taking the room it waits for; it costs the small ones a wait. A worker
# waits with its queue's first line's needs, updated as the queue changes (it
# leaves the line when it can't get them: no queue host, needs this host never
# meets), and takes any line that fits beside the ones before it. Only needs
# that the room could meet once every job in the ledger has ended are held
# back for: room taken outside the ledger (vm-update, another session's RAM
# work, leftovers) can't make one waiter hold up the host. The heat limit is
# the lowest HOT_C of anyone before in line, so jobs behind a waiter that is
# held back by heat don't keep the CPU hot.
set -u
cd ~/backotter-work
RAM=/mnt/bko-ram LEDGER=/mnt/bko-ram/.reserve WAITS=/mnt/bko-ram/.reserve.wait
mountpoint -q $RAM || { echo "no tmpfs at $RAM" >&2; exit 1; }
touch $LEDGER $LEDGER.lock $WAITS 2>/dev/null; chmod 666 $LEDGER $LEDGER.lock $WAITS 2>/dev/null
H=$(hostname -s)
DISKS_DIR=${DISKS_DIR:-/bkoscratch/compare-disks}

# Ledger rows: PID DISK MEM SCOPE WHAT...   (SCOPE "-" when there is none)
tree_anon() {  # PID ... -> "PID BYTES" lines: the anon memory (RssAnon) of each
    # PID's process tree. A process counts for its nearest ancestor among the
    # PIDs, so a --reserve inside another's tree isn't counted twice. A process
    # that left the tree (reparented to init) isn't counted: its row then
    # reserves more, as before, never less.
    [ $# -gt 0 ] || return 0
    cat /proc/[0-9]*/status 2>/dev/null | awk -v roots="$*" '
        $1 == "Pid:" { p = $2 } $1 == "PPid:" { pp[p] = $2 } $1 == "RssAnon:" { ra[p] = $2 * 1024 }
        END {
            n = split(roots, r, " "); for (i = 1; i <= n; i++) { root[r[i]] = 1; sum[r[i]] = 0 }
            for (p in ra) { k = 0; for (x = p; x > 1 && k++ < 256; x = pp[x]) if (x in root) { sum[x] += ra[p]; break } }
            for (x in sum) print x, sum[x]
        }'
}
budget() {  # -> "MAXDISK MAXMEM" in GiB for one more job (ledger locked)
    local arc_max arc_size unused=0 pid d m scope rest anon trees
    arc_max=$(awk '$1 == "c_max" { print $3 }' /proc/spl/kstat/zfs/arcstats 2>/dev/null)
    arc_size=$(awk '$1 == "size" { print $3 }' /proc/spl/kstat/zfs/arcstats 2>/dev/null)
    # shellcheck disable=SC2046  # the PIDs are meant to split
    trees=$(tree_anon $(awk '$4 == "-" && $1 ~ /^[0-9]+$/ { print $1 }' $LEDGER))
    while read -r pid d m scope rest; do
        [ -n "$pid" ] || continue
        anon=0
        if [ "$scope" != - ]; then
            anon=$(awk '$1 == "anon" { print $2 }' /sys/fs/cgroup/system.slice/$scope/memory.stat 2>/dev/null)
        else   # a --reserve: what its CMD's tree uses
            anon=$(awk -v p="$pid" '$1 == p { print $2 }' <<<"$trees")
        fi
        unused=$(awk -v u="$unused" -v m="$m" -v a="${anon:-0}" \
            'BEGIN { x = m - a / 1073741824; if (x < 0) x = 0; print u + x }')
    done <$LEDGER
    df -Pk $RAM | awk -v arc_max="${arc_max:-0}" -v arc_size="${arc_size:-0}" -v base="${BASE_GIB:-6}" \
                      -v arc_room="${ARC_ROOM_GIB:-1}" -v unused="$unused" -v ledger=$LEDGER '
        function fl(x) { return int(x * 10 + (x < 0 ? -1 : 0)) / 10 }   # round down
        NR == 2 { size = $2 / 1048576; avail = $4 / 1048576 }
        END {
            G = 1073741824
            while ((getline l < "/proc/meminfo") > 0) {
                split(l, f, /[: ]+/)
                if (f[1] == "MemTotal") total = f[2] / 1048576
                if (f[1] == "MemAvailable") memavail = f[2] / 1048576
                if (f[1] == "SwapTotal") swt = f[2] / 1048576
                if (f[1] == "SwapFree") swf = f[2] / 1048576
            }
            if (total == 0 || size == 0) exit 1
            while ((getline l < ledger) > 0) { split(l, f, " "); rd += f[2]; rm += f[3] }
            # The ARC: its size plus arc_room, within its cap; arcroom is its room to grow.
            arcroom = (arc_max - arc_size) / G; if (arcroom < 0) arcroom = 0
            if (arcroom > arc_room) arcroom = arc_room
            arc = arc_size / G + arcroom
            disk = size - 1 - rd; if (avail - 1 < disk) disk = avail - 1
            mem = total - size - arc - base - rm
            live = memavail - avail - arcroom - (swt - swf) - 2 - unused
            if (live < mem) mem = live
            printf "%.1f %.1f\n", fl(disk), fl(mem)
        }'
}
prune() {  # [PID]: drop rows (ledger and line) whose holder is gone, or PID's (ledger locked)
    local f pid rest keep
    for f in $LEDGER $WAITS; do
        keep=""
        while read -r pid rest; do
            # (A row that isn't PID first is junk: "." would pass as /proc/.)
            [[ $pid =~ ^[0-9]+$ ]] && [ -d /proc/$pid ] && [ "$pid" != "${1:-}" ] && keep+="$pid $rest"$'\n'
        done <$f
        printf '%s' "$keep" >$f
    done
}
after_line() {  # PID MAXDISK MAXMEM -> "DISK MEM AHEAD HOT_C": the room left once
    # everyone before PID in line (everyone, if PID isn't in it) has had theirs,
    # how many they are, and the heat limit (ledger locked). A need held back
    # for must fit in MAXDISK/MAXMEM plus what the ledger's jobs hold.
    # (In tenths of a GiB: the figures come rounded to them.)
    awk -v me="$1" -v d="$2" -v m="$3" -v hot="${HOT_C:-95}" -v ledger=$LEDGER '
        function t(x) { return int(x * 10 + (x < 0 ? -0.5 : 0.5)) }
        BEGIN { d = t(d); m = t(m); pd = d; pm = m
                while ((getline l < ledger) > 0) { split(l, f, " "); pd += t(f[2]); pm += t(f[3]) } }
        $1 == me { exit }
        { n++; if ($4 + 0 > 0 && $4 + 0 < hot + 0) hot = $4 + 0
          if (t($2) <= pd && t($3) <= pm) { d -= t($2); m -= t($3) } }
        END { printf "%.1f %.1f %d %d\n", d / 10, m / 10, n, hot }' $WAITS
}
queue_up() {  # PID DISK MEM WHAT...: take a place in line, or change the needs at it (ledger locked)
    local pid=$1 d=$2 m=$3; shift 3
    if grep -q "^$pid " $WAITS; then
        awk -v p="$pid" -v d="$d" -v m="$m" '$1 == p { $2 = d; $3 = m } { print }' $WAITS >$WAITS.new &&
            cat $WAITS.new >$WAITS; rm -f $WAITS.new
    else
        echo "$pid $d $m ${HOT_C:-95} $*" >>$WAITS
    fi
}
leave_line() { sed -i "/^$1 /d" $WAITS; }   # PID (ledger locked)
lock() { exec {lk}>>$LEDGER.lock && flock -w 600 $lk; }
unlock() { exec {lk}>&-; }
hot() {  # [HOT_C]: true while the CPU or an NVMe is too hot to start more
    local h n t
    for h in /sys/class/hwmon/hwmon*; do
        n=$(cat $h/name 2>/dev/null) t=$(cat $h/temp1_input 2>/dev/null) || continue
        case $n in
        k10temp|coretemp) [ "$t" -ge $(( ${1:-${HOT_C:-95}} * 1000 )) ] && return 0 ;;
        nvme) [ "$t" -ge $(( ${NVME_C:-75} * 1000 )) ] && return 0 ;;
        esac
    done
    return 1
}
fits() { awk -v a="$1" -v b="$2" -v c="$3" -v e="$4" 'BEGIN { exit !(b + 0 <= a + 0 && e + 0 <= c + 0) }'; }
parse() {  # LINE -> globals g stick grow ondisk d m (suffixes only at the end, in order)
    local s=$1
    m=5 d=8 grow=0 ondisk=""
    [[ $s =~ %([0-9.]+)$ ]] && { m=${BASH_REMATCH[1]}; s=${s%"%$m"}; }
    [[ $s =~ =([0-9.]+)$ ]] && { d=${BASH_REMATCH[1]}; s=${s%"=$d"}; }
    [[ $s == *! ]] && { ondisk=1; s=${s%!}; }
    [[ $s =~ @([0-9]+)$ ]] && { grow=${BASH_REMATCH[1]}; s=${s%"@$grow"}; }
    g=${s%%:*} stick=""; [ "$g" != "$s" ] && stick=${s#*:}
}
ceiling() {  # -> "DISK MEM": the most one job could ever get on this host
    local arc_max arc_size
    arc_max=$(awk '$1 == "c_max" { print $3 }' /proc/spl/kstat/zfs/arcstats 2>/dev/null)
    arc_size=$(awk '$1 == "size" { print $3 }' /proc/spl/kstat/zfs/arcstats 2>/dev/null)
    df -Pk $RAM | awk -v arc_max="${arc_max:-0}" -v arc_size="${arc_size:-0}" -v arc_room="${ARC_ROOM_GIB:-1}" \
                      -v base="${BASE_GIB:-6}" '
        NR == 2 { size = $2 / 1048576 }
        END { G = 1073741824
              arc = arc_size / G + arc_room; if (arc > arc_max / G) arc = arc_max / G   # as budget() counts it
              while ((getline l < "/proc/meminfo") > 0) { split(l, f, /[: ]+/); if (f[1] == "MemTotal") t = f[2] / 1048576 }
              printf "%.1f %.1f\n", size - 1, t - size - arc - base - 2 }'
}

if [ "${1:-}" = --reserve ]; then
    d=$2 m=$3; shift 3
    read -r cd cm < <(ceiling)
    fits "$cd" "$d" "$cm" "$m" || { echo "--reserve $d $m can never fit here (at most $cd $cm)" >&2; exit 1; }
    while :; do
        lock || { sleep 30; continue; }
        prune
        if read -r maxd maxm < <(budget) && read -r maxd maxm _ hotc < <(after_line $$ "$maxd" "$maxm") &&
                ! hot "$hotc" && fits "$maxd" "$d" "$maxm" "$m"; then
            echo "$$ $d $m - reserve $*" >>$LEDGER; leave_line $$; unlock; break
        fi
        queue_up $$ "$d" "$m" reserve "$@"
        unlock; sleep 30
    done
    # In a scope of its own, as m still, so that iolog.sh can count what it
    # writes to disk (the wear log; WEAR_LOG=off: not). exec keeps $$, so the
    # reservation lasts as long as CMD.
    [ "${WEAR_LOG:-}" = off ] && exec "$@"
    exec sudo -E systemd-run --quiet --scope --collect -p IOAccounting=yes --uid="$(id -u)" --gid="$(id -g)" \
        env PATH="$PATH" "$PWD/testvm/compare/iolog.sh" "reserve $*" -- "$@"
fi

tag=$1 slots=$2; shift 2
sb=${SLOT_BASE:-4}
[[ $slots =~ ^[1-9][0-9]*$ && $sb =~ ^[0-9]$ ]] && (( sb + slots <= 9 )) ||
    { echo "slots $((sb + 1))..$((sb + slots)): compare.py takes COMPARE_SLOT 0-9 only" >&2; exit 1; }
mkdir -p cmp-logs/$tag
Q=cmp-logs/$tag/queue
if [ $# -gt 0 ]; then
    [ -z "${QUEUE_HOST:-}" ] || { echo "guests go in the queue on $QUEUE_HOST" >&2; exit 1; }
    read -r cd cm < <(ceiling)
    seen=" "
    for line; do
        parse "$line"
        fits "$cd" "$d" "$cm" "$m" || { echo "$line can never fit (at most =$cd %$cm)" >&2; exit 1; }
        [ -z "$ondisk" ] || [ -d "$DISKS_DIR" ] || { echo "$line: no $DISKS_DIR" >&2; exit 1; }
        case $seen in *" $g "*) echo "$g is queued twice" >&2; exit 1 ;; esac
        seen+="$g "
    done
    printf '%s\n' "$@" >$Q
fi
[ -n "${QUEUE_HOST:-}" ] || [ -e $Q ] || { echo "no $Q" >&2; exit 1; }
touch cmp-logs/$tag/done
read -r cd cm < <(ceiling)
# No TMPDIR: it would reach the commands compare.py runs in a restored guest's
# chroot (update-initramfs), where /mnt/bko-ram doesn't exist. vm-run picks
# /mnt/bko-ram by itself.
E="RESTIC_BKO=$PWD/cmp-bin/restic-bko RUSTIC_BKO=$PWD/cmp-bin/rustic-bko"

pop() {  # MAXDISK MAXMEM -> a queue line that fits; rc 0 got, 1 none fits (or failed), 3 empty
    # (with 1, cmp-pop.sh prints the first line's "DISK MEM" when it got that far)
    local rc
    if [ -n "${QUEUE_HOST:-}" ]; then
        timeout 90 ssh -n -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=10 \
            -o ServerAliveCountMax=3 -o ControlMaster=no -o ControlPath=none "$QUEUE_HOST" \
            "cd ~/backotter-work && sh testvm/compare/cmp-pop.sh $Q $1 $2 $H" {lk}>&-
        rc=$?
        case $rc in 0|1|3) return $rc ;; esac
        log "ssh pop failed ($rc): see $Q.taken on $QUEUE_HOST for a line taken but not run"
        return 1
    fi
    sh testvm/compare/cmp-pop.sh $Q "$1" "$2" $H {lk}>&-
    rc=$?
    case $rc in 0|1|3) return $rc ;; esac
    log "cmp-pop.sh failed ($rc)"
    return 1
}
log() { echo "$(date -u +%T) $H slot $slot: $*" >>cmp-logs/$tag/progress; }

worker() {  # SLOT
    local slot=$1 line rc g stick grow ondisk d m lk maxd maxm ahead hotc jp scope args why hi dd waiting="" me=$BASHPID
    while :; do
        if [ -e cmp-logs/$tag/stop ]; then
            if lock; then leave_line $me; unlock; fi
            log "stopped"; return 0
        fi
        lock || { log "cannot lock $LEDGER.lock"; sleep 30; continue; }
        prune
        line="" rc=1 why=""
        if ! read -r maxd maxm < <(budget) || [ -z "$maxm" ] ||
                ! read -r maxd maxm ahead hotc < <(after_line $me "$maxd" "$maxm"); then why="budget failed"
        elif hot "$hotc"; then why="too hot"
        else
            line=$(pop "$maxd" "$maxm"); rc=$?
            if [ $rc = 1 ]; then
                why="nothing fits ${maxd}G RAM disk, ${maxm}G memory"
                [ "$ahead" -gt 0 ] && why+=" after $ahead before it in line"
                # In line with the first line's needs, if this host can ever meet
                # them; out of it when it can't, or got no needs (ssh, lock).
                if [[ $line =~ ^[0-9.]+\ [0-9.]+$ ]] && fits "$cd" "${line% *}" "$cm" "${line#* }"; then
                    queue_up $me ${line% *} ${line#* } "$tag slot $slot"
                else
                    leave_line $me
                fi
            fi
        fi
        if [ $rc = 0 ] && [ -n "$line" ]; then
            parse "$line"
            scope=bko-$tag-$slot-$(date +%s).scope
            echo "$me $d $m $scope $tag $line" >>$LEDGER
            leave_line $me
        fi
        [ $rc = 3 ] && leave_line $me
        unlock
        [ $rc = 3 ] && return 0
        if [ $rc != 0 ] || [ -z "$line" ]; then
            # Once per reason, not per changing figure.
            [ "${why%% [0-9-]*}" != "$waiting" ] && log "waiting: $why"; waiting=${why%% [0-9-]*}
            sleep 30; continue
        fi
        waiting=""

        args=(); [ -n "$stick" ] && args=(--mkfs-from "$stick")
        dd=""
        if [ -n "$ondisk" ]; then
            if [ ! -d "$DISKS_DIR" ]; then   # never fall back to RAM: it wasn't reserved
                log "$g: no $DISKS_DIR"; echo "$g 99 $H" >>cmp-logs/$tag/done
                if lock; then prune $me; unlock; fi; continue
            fi
            dd="COMPARE_DISKS_DIR=$DISKS_DIR"
        fi
        log "$g (reserved ${d}G RAM disk, ${m}G memory${dd:+, restored disks in $DISKS_DIR}; budget was ${maxd}G/${maxm}G)"
        hi=$(awk -v d="$d" -v m="$m" 'BEGIN { printf "%d", (d + m) * 1024 }')
        # CMP_CMD: a command run in compare.py's place, the same way, with
        # GUEST DISK MEM SLOT [ARGS] (rebuild-one.sh, or a stand-in for tests).
        if [ -n "${CMP_CMD:-}" ]; then
            sudo systemd-run --quiet --scope --unit=${scope%.scope} -p MemoryHigh=${hi}M -p IOAccounting=yes \
                testvm/compare/iolog.sh "$tag $g" -- env COMPARE_SLOT=$slot COMPARE_GROW_GIB=$grow $dd $E $CMP_CMD "$g" "$d" "$m" "$slot" "${args[@]}" \
                >cmp-logs/$tag/$g.log 2>&1 &
        else
            sudo systemd-run --quiet --scope --unit=${scope%.scope} -p MemoryHigh=${hi}M -p IOAccounting=yes \
                testvm/compare/iolog.sh "$tag $g" -- env COMPARE_SLOT=$slot COMPARE_GROW_GIB=$grow $dd $E python3 testvm/compare/compare.py "${args[@]}" "$g" \
                restic-bko rustic-bko restic-to-rustic rustic-to-restic >cmp-logs/$tag/$g.log 2>&1 &
        fi
        jp=$!
        # The row belongs to the job (sudo lives as long as it), not to this
        # shell, so killing the worker doesn't free room the job still holds.
        if lock; then sed -i "s/^$me /$jp /" $LEDGER; unlock; fi
        wait $jp; rc=$?
        log "$g exit $rc"
        echo "$g $rc $H" >>cmp-logs/$tag/done
        if lock; then prune $jp; unlock; fi
    done
}
for s in $(seq 1 "$slots"); do worker "$((s + sb))" & sleep 5; done
wait
echo "$(date -u +%T) $H finished" >>cmp-logs/$tag/progress
