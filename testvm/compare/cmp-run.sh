#!/usr/bin/env bash
# compare.py over a queue of guests, up to SLOTS at once, BackOtter's four
# directions each, in RAM: compare.py works in /mnt/bko-ram by itself and
# leaves its results, diffs and logs in ~/backotter-work/compare/GUEST.
# Every job gets the archivers in
# ~/backotter-work/cmp-bin (RESTIC_BKO, RUSTIC_BKO), not compare.py's newest
# build.
#
# Usage (on a KVM test host; it works in ~/backotter-work, where
# sync-hosts.sh puts testvm/, and ~/backotter-work/cmp-run.sh links here):
#   cmp-run.sh TAG SLOTS GUEST[@GROW_GIB][!][=DISK_GIB][%MEM_GIB] ...
#       writes the queue cmp-logs/TAG/queue on this host and works on it;
#   QUEUE_HOST=chopin.local cmp-run.sh TAG SLOTS
#       works on that host's queue (over ssh), so both hosts share one;
#   cmp-run.sh --reserve DISK_GIB MEM_GIB CMD ...
#       runs CMD once its room is free, holding the room meanwhile;
#   cmp-run.sh --reserve-ssd SSD_GIB MEM_GIB CMD ...
#       the same for a CMD whose scratch is on the host's SSD (/bkoscratch),
#       not in the RAM disk: it holds MEM_GIB of memory, none of the RAM disk,
#       and SSD_GIB of the SSD's room (below: "The SSD's room");
#   cmp-run.sh --ssd-free
#       prints that room now, one figure in GiB (0.0: none, and always on a
#       host whose scratch_mode is not overflow or ssd), and writes nothing,
#       as --free;
#   cmp-run.sh --ceiling
#       prints "DISK MEM", the most one job could ever get on this host
#       (ceiling(): a --reserve or a queue line over it is refused at once),
#       floored to a tenth as budget()'s figures are, and writes nothing: not
#       even the ledger's files.  Each figure is a limit of its own (D <= DISK
#       and M <= MEM): MEM is what is left beside a job of the whole DISK,
#       so any D and M within the two always fit an empty host.
#   cmp-run.sh --free
#       prints "DISK MEM", the room a new --reserve could get here now
#       (free_now(): the smaller of the ceiling's and budget()'s RAM disk, and
#       budget()'s memory within the ceiling's two figures together, which is
#       just what admission tests a --reserve against, so its live
#       MemAvailable and tmpfs checks and the ledger's rows all count),
#       floored at 0.  Here MEM is the memory for the job's D and M together
#       (below: "memory, charged D + M"): a --reserve D M within the ceiling
#       starts at once when D <= DISK and D + M <= MEM.  Then "waiting N", how
#       many jobs are in line (the WAITS
#       file); then "after DISK MEM", the room a new --reserve would be offered
#       after the line (after_line(), on the same figures and the line's live
#       rows, within the ceiling and floored at 0: a --reserve within it starts
#       at once, one over it waits; with nobody in line it is the first line's
#       figures).  It writes nothing either, not even the ledger's files, so
#       anyone may ask a host where there is room (testvm/pick-host does,
#       over ssh).  The figures are what the host had a moment ago: a job
#       that starts meanwhile takes its room, and only a --reserve of its own
#       holds any.  It also reads the ledger and the line without the lock
#       (taking it would make the lock file), and a row being written is a
#       truncate and a write (put's dd, no notrunc), so a reader can catch a
#       torn file and count a row short.  The figures are advice, never a
#       decision: the --reserve itself is what holds room, under the lock.
# WEAR_TAG names the work in the wear log (FAMILY:RUN[:CELL], e.g.
# ht1:r4all:pop24): a --reserve's CMD is logged with it as it is, a queue's
# jobs with :GUEST added (default cmp:TAG:GUEST). Give it to the cmp-run.sh
# on each host, since it doesn't travel with the queue. Set it per command,
# and don't export it.
# A "!" puts the guest's restored disks on DISKS_DIR (default
# /bkoscratch/compare-disks, a cool disk) instead of the RAM disk; its
# DISK_GIB then counts only what stays in RAM (repositories, overlays).
# A queue's cmp-run.sh makes DISKS_DIR when its parent is there and
# writable (cage and copland had none); a worker still without one takes no
# "!" line (cmp-pop.sh's nodisk) and leaves them to the hosts that can.
# Should none of those ever ask the queue for a line (QUEUE.disks, cmp-pop.sh's
# mark) while only "!" lines are left, unchanged, for NODISK_POLLS polls
# (default 20, about 10 minutes), the worker gives up waiting and takes them
# as before: a FAIL row each (exit 99 in done), never a silent hang.
# touch cmp-logs/TAG/stop on a host: its workers start nothing more (with
# QUEUE_HOST, on each host that runs workers). Lines taken from the queue
# are in cmp-logs/TAG/queue.taken on the queue's host; cmp-pop.sh says how to
# add one back. Slots are SLOT_BASE+1 .. SLOT_BASE+SLOTS (SLOT_BASE default
# 4, so another run on the host can take 1-4); compare.py takes 0-9 only.
#
# Nothing starts unless it fits, so neither the RAM disk nor memory runs out:
#   RAM disk: the DISK_GIB (default 8) of everything running stays within
#     the tmpfs, less 1 GiB;
#   memory, charged D + M: a job's RAM disk is memory too (tmpfs pages), so
#     the memory rules below count a new job's DISK_GIB and MEM_GIB together,
#     and a running job's RAM disk as what it reserved.  Until 2026-11-24 the
#     static rule charged the whole tmpfs, as if it were full, and each job's
#     MEM_GIB on top: the RAM disk nobody had reserved (all of it, on a big
#     host whose HoltTest jobs work on the SSD) was held as memory as well.
#     On cage at 06:15Z that day it offered 1.3 GiB of memory with
#     MemAvailable at 17 GiB and 0.7 of the 10 GiB RAM disk in use, and
#     SticTest's driver logged "nothing fits" ~180 times on czerny and copland
#     (t2s-20261124T0448-74bc);
#   memory, static: the DISK_GIB + MEM_GIB of a new job fits in MemTotal after
#     the RAM disk in use or reserved (the larger of the DISK_GIB of
#     everything running and the tmpfs's used space: what is written outside
#     any reservation, or past one, counts too), the MEM_GIB of everything
#     running, the ARC and BASE_GIB for the system.  BASE_GIB: the
#     environment's, else this host's base_gib in the host table
#     (testvm/hosts.json, by hostname -s; hosts.py limit, BKO_HOSTS_PY naming
#     another hosts.py), else 6;
#   memory, live (other sessions): MemAvailable, less what the RAM disk's
#     reservations may still write (their DISK_GIB less the tmpfs's used
#     space, floored at 0: what is written is off MemAvailable already), the
#     ARC's room to grow, swap in use, 2 GiB, and what running jobs have
#     reserved but not touched yet (MEM_GIB less their cgroup's anon; for a
#     --reserve, whose row names no cgroup, less the anon of CMD's process
#     tree: a VM started with setsid -f, as HoltTest's are, has left that
#     tree, so its memory counts twice, never too little), covers the new
#     job's DISK_GIB + MEM_GIB.  It is the guard: whatever the ledger
#     doesn't know of (another session, a job over its MEM_GIB, leftovers on
#     the tmpfs) is off MemAvailable already.
#   Neither rule admits less than the rules before 2026-11-24 did: a D within
#     the RAM disk's room was charged at least that much of the tmpfs there.
#     budget()'s MEM is this joint room (D + M <= MEM), and so are --free's,
#     after_line()'s, cmp-pop.sh's and every reader's of them (drive.py,
#     ht1-queue.sh, pick-host); ceiling()'s MEM is not (above).
#   The ARC counts as its size now plus ARC_ROOM_GIB (default 1), within its
#     cap: not the whole cap (zfs_arc_max, 8 GiB on the big hosts), which only
#     the host's small throwaway test pools ever use, and the ARC gives memory
#     back under pressure.  A host without ZFS (the small ones) has no ARC: 0.
# The SSD's room (RAM first, the SSD for overflow: the user's choice,
#   2026-11-23).  A job that can work on either (a HoltTest line: ht1-queue.sh
#   decides, by the host's scratch_mode in the host table) takes a plain
#   --reserve when its scratch fits in the RAM disk now, and a --reserve-ssd
#   when it doesn't, or is too big for it, so that it need not wait for the RAM
#   disk.  ssd_room(): the free space of /bkoscratch (df, so whatever anyone
#   has written counts), less SSD_FLOOR_GIB (the environment's, else the
#   host's ssd_floor_gib in the host table, else 80: Test2 starts nothing
#   under 80 GB free there), less the SSD_GIB of every --reserve-ssd running
#   (all of it, though part is written and off the free space already: counted
#   twice, never too little).  A --reserve-ssd starts when its memory fits as
#   any job's must (budget(), the line, the heat) and its SSD_GIB is within
#   that room; else it waits in the same line, with its memory.  One that
#   could never have its SSD_GIB (over the whole file system less the floor)
#   or its memory is refused at once.  Its ledger row is PID 0 MEM - reserve
#   ssd=SSD_GIB CMD...: a row every reader of the ledger takes as it is (no
#   RAM disk, MEM of memory; ht1-lib.sh, an older cmp-run.sh), and a row
#   without ssd= holds none of the SSD, so an older cmp-run.sh's rows and an
#   empty ledger read as before.  The wear log says where a job's scratch was:
#   scratch=ssd for a --reserve-ssd, scratch=ram for a --reserve of some RAM
#   disk (WEAR_SCRATCH in the environment names it instead, for a caller that
#   knows better: a --reserve 0 MEM whose CMD works on /bkoscratch).
# Every figure is GiB floored to a tenth, and EPS_GIB (0.001, a MiB) is added
# before the floor: fl() in ceiling(), budget() and free_now(), and the same
# tolerance in fits(), the admission comparison.  Without it the bookkeeping's
# own few KiB cost a whole tenth, because budget()'s RAM disk room is the
# tmpfs's free space less 1 GiB and the ledger's three files live on that same
# tmpfs: with the RAM disk otherwise empty df shows 4 KiB used, so on choubox's
# 4 GiB tmpfs the room came to 2.99999 and floored to 2.9 while ceiling() said
# 3.0, and a --reserve 3 6 (exactly that host's ceiling, as SticTest reserves)
# could never be let in -- it waited for ever.  Float noise in the memory
# figures (MemTotal is no round number of GiB: 5.9999 floored to 5.9) went the
# same way.  ceiling() floors with the tolerance too, where it used to round to
# the nearest tenth: a ceiling rounded up admits a --reserve that budget()
# never grants, which is that same endless wait, so both ends must quantize
# alike.  The tolerance cannot over-commit anything real: it is a thousandth of
# the step it saves, both figures stop well short of the metal anyway (the RAM
# disk 1 GiB short of the tmpfs, the memory 2 GiB short of MemAvailable), and
# room genuinely in use -- a tenth of a GiB of leftovers, the smallest figure
# any job reserves -- is a hundred times the tolerance and still comes off.
# after_line() needs none of its own: it works in whole tenths (t() rounds to
# the nearest one) on figures budget() has already floored.
# facts_host.py and drive.py cut tree_anon(), budget(), after_line() and
# ceiling() out of this file one at a time (sed) and run them on their own, so
# each carries its own copy of whatever it needs, fl() included, and may lean
# on nothing defined around it.
# Each queue job runs in a systemd scope with MemoryHigh = DISK + MEM GiB
# (its tmpfs pages count there too): past that it is slowed and reclaimed
# into zram, never killed. A --reserve's scope has no MemoryHigh. zram
# (16 GiB on the big hosts, 8 on the small ones) is the cushion under all of it.
# Every job, a --reserve's CMD too, runs under iolog.sh, which logs what it
# wrote to disk in /bkoscratch/wear/jobs.log, with WEAR_TAG (wear-report.py
# sums it up).
# On a host whose row in the host table has a job_slice (Bigfoot's
# bko.slice; hosts.py job_prefix), every job's scope, a --reserve's too, is in
# that slice (systemd-run --slice), and a WEAR_LOG=off --reserve's CMD runs
# under hosts.py job-prefix's words.
# Reservations live in /mnt/bko-ram/.reserve, shared by every cmp-run.sh on
# the host. Nothing new starts while the CPU is over HOT_C (default 95) or an
# NVMe over NVME_C (default 75).
#
# First come, first served, with backfill: a --reserve, or a worker whose
# queue's first line doesn't fit, that has to wait takes a place in line
# (/mnt/bko-ram/.reserve.wait, PID DISK MEM HOT_C SINCE WHAT..., SINCE the
# epoch second it took its place, kept when its needs change), and room is
# offered to it after what those before it in line are to have first:
#   - one whose needs fit the room left now has it first (it will take it on
#     its next try, within 30 s), so nobody behind it takes room it fits into;
#   - one whose needs don't fit now may be passed: a job behind it that fits
#     the room left starts now, while the one passed has waited less than
#     BACKFILL_MAX seconds (default 1800; 0: never passed, strictly first
#     come, first served);
#   - once it has waited BACKFILL_MAX, it is held back for: its needs come
#     off the room even though they don't fit, so nothing behind it starts
#     until the jobs running have ended and it can. So a big job isn't
#     starved by small ones that keep taking the room it waits for, and the
#     small ones don't sit idle behind it while it can't start anyway; what
#     it costs the big one is at most BACKFILL_MAX, plus the run of the last
#     job that passed it. 30 minutes is about one small host's job (a
#     HoltTest round, a SticTest check), so the big one waits at most about
#     that much longer, and the room it waits for is used meanwhile.
#   A row without SINCE (an older cmp-run.sh's) has waited BACKFILL_MAX
#   already: held back for, as that cmp-run.sh holds back for everyone.
# A worker waits with its queue's first line's needs, updated as the queue
# changes (it leaves the line when it can't get them: no queue host, needs
# this host never meets), and takes any line that fits beside the ones before
# it. Only needs that the room could meet once every job in the ledger has
# ended are held back for, or had first: room taken outside the ledger
# (vm-update, another session's RAM work, leftovers) can't make one waiter
# hold up the host. The heat limit is the lowest HOT_C of anyone before in
# line, passed or not, so jobs behind a waiter that is held back by heat
# don't keep the CPU hot: one passed for room needs the CPU cool as much
# when its room comes, and the ones that passed it shouldn't heat it for it.
# The ledger's files are made by whoever needs them first, m or root, in the
# sticky /mnt/bko-ram: m can't rename over root's (so no sed -i or mv on
# them), and fs.protected_regular (2 on the hosts) keeps root from opening
# m's with O_CREAT (so no >, >> or touch). So they are made 666 when missing,
# then only opened without O_CREAT and rewritten in place (ledger_files, put,
# rewrite, add). CMP_LEDGER_DIR (default /mnt/bko-ram) is for tests only
# (cmp-run-test.sh): a ledger of its own, beside the real one; CMP_SSD_DIR
# (default /bkoscratch) likewise: the directory whose file system is the SSD;
# and CMP_MEMINFO (default /proc/meminfo): the file budget() and ceiling() read
# the memory from (test_runner_hosts.py's, on a Mac).
set -u
HERE=$(dirname "$(realpath "${BASH_SOURCE[0]}")")   # before the cd: $0 may be relative
cd ~/backotter-work
# Jobs run under the iolog.sh beside this file (a private copy's own), or the
# shared one when there is none there (a lone copy of cmp-run.sh).
IOLOG=$HERE/iolog.sh; [ -x "$IOLOG" ] || IOLOG=$PWD/testvm/compare/iolog.sh
RAM=/mnt/bko-ram LEDGER=${CMP_LEDGER_DIR:-/mnt/bko-ram}/.reserve WAITS=${CMP_LEDGER_DIR:-/mnt/bko-ram}/.reserve.wait
mountpoint -q $RAM || { echo "no tmpfs at $RAM" >&2; exit 1; }
ledger_files() {  # make the ledger's files that are missing, 666 (noclobber: O_EXCL, never another's)
    local f
    for f in $LEDGER $LEDGER.lock $WAITS; do
        [ -e $f ] || (umask 0; set -C; : >$f) 2>/dev/null
    done
}
put() {  # FILE TEXT: FILE's content becomes TEXT, in place (the same inode, no O_CREAT)
    printf '%s' "$2" | dd of="$1" conv=nocreat bs=1M iflag=fullblock status=none
}
rewrite() {  # FILE CMD...: FILE's content becomes CMD's output (CMD reads FILE), if CMD succeeds
    local f=$1 c; shift
    c=$("$@"; rc=$?; echo .; exit $rc) || return; put "$f" "${c%.}"
}
add() { printf '%s\n' "$2" | dd of="$1" conv=nocreat,notrunc oflag=append status=none; }   # FILE LINE
SSD=${CMP_SSD_DIR:-/bkoscratch}
# --ceiling, --free and --ssd-free write nothing at all, so that anyone
# (pick-host over ssh) may ask a host what room it has without touching its ledger.
case ${1:-} in
--ceiling|--free|--ssd-free) ;;
*) ledger_files; chmod 666 $LEDGER $LEDGER.lock $WAITS 2>/dev/null ;;
esac
H=$(hostname -s)
# BASE_GIB once for the script (ceiling() and budget() look it up themselves
# when it is unset: facts_host.py and drive.py run them alone, by sed).
BKO_HOSTS_PY=${BKO_HOSTS_PY:-$HERE/../test2/t2lib/hosts.py}
[ -n "${BASE_GIB:-}" ] || BASE_GIB=$(python3 -I -B "$BKO_HOSTS_PY" limit "$H" base_gib 2>/dev/null)
[[ $BASE_GIB =~ ^[0-9]+([.][0-9]+)?$ ]] || BASE_GIB=6
# The host's job_slice (bko.slice on Bigfoot), or none: every job's scope goes in it.
JOB_SLICE=$(python3 -I -B "$BKO_HOSTS_PY" get "$H" job_slice 2>/dev/null) || JOB_SLICE=""
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
live_rows() {  # [FILE] -> the rows of FILE (default the ledger) whose holder is still
    # there, in order, one a line; nothing is written, and a file that isn't there has no
    # rows (--ceiling and --free don't make the ledger's files).  Once prune has run under
    # the lock the ledger holds exactly these, so budget() reads the same rows as before;
    # free_now(), which may not write, gets them without pruning.
    local f=${1:-$LEDGER} pid rest out=""
    [ -e "$f" ] || return 0
    while read -r pid rest; do
        # (A row that isn't PID first is junk: "." would pass as /proc/.)
        [[ $pid =~ ^[0-9]+$ ]] && [ -d /proc/$pid ] && out+="$pid $rest"$'\n'
    done <"$f"
    printf '%s' "$out"
}
budget() {  # [ROWS] -> "MAXDISK MAXMEM" in GiB for one more job (ledger locked; ROWS
    # default the ledger's live rows, which is all it holds once prune has run).
    # MAXMEM is for the job's DISK and MEM together (the header's "memory,
    # charged D + M"): a job D M fits when D <= MAXDISK and D + M <= MAXMEM.
    local arc_max arc_size unused=0 pid d m scope rest anon trees base=${BASE_GIB:-} rows size avail
    rows=${1-$(live_rows)}
    [ -n "$base" ] || base=$(python3 -I -B "${BKO_HOSTS_PY:-$HOME/backotter-work/testvm/test2/t2lib/hosts.py}" \
                                limit "$(hostname -s)" base_gib 2>/dev/null)
    [[ $base =~ ^[0-9]+([.][0-9]+)?$ ]] || base=6
    arc_max=$(awk '$1 == "c_max" { print $3 }' /proc/spl/kstat/zfs/arcstats 2>/dev/null)
    arc_size=$(awk '$1 == "size" { print $3 }' /proc/spl/kstat/zfs/arcstats 2>/dev/null)
    read -r size avail < <(df -Pk $RAM | awk 'NR == 2 { print $2, $4 }')
    # shellcheck disable=SC2046  # the PIDs are meant to split
    trees=$(tree_anon $(awk '$4 == "-" && $1 ~ /^[0-9]+$/ { print $1 }' <<<"$rows"))
    while read -r pid d m scope rest; do
        [ -n "$pid" ] || continue
        anon=0
        if [ "$scope" != - ]; then
            anon=$(awk '$1 == "anon" { print $2 }' /sys/fs/cgroup/${JOB_SLICE:-system.slice}/$scope/memory.stat 2>/dev/null)
        else   # a --reserve: what its CMD's tree uses
            anon=$(awk -v p="$pid" '$1 == p { print $2 }' <<<"$trees")
        fi
        unused=$(awk -v u="$unused" -v m="$m" -v a="${anon:-0}" \
            'BEGIN { x = m - a / 1073741824; if (x < 0) x = 0; print u + x }')
    done <<<"$rows"
    awk -v size_k="${size:-0}" -v avail_k="${avail:-0}" -v arc_max="${arc_max:-0}" -v arc_size="${arc_size:-0}" \
        -v base="$base" -v arc_room="${ARC_ROOM_GIB:-1}" -v unused="$unused" -v meminfo="${CMP_MEMINFO:-/proc/meminfo}" '
        # Down to a tenth, a MiB of tolerance first (EPS_GIB; see the header):
        # the bookkeeping sits on this tmpfs, and 2.99999 is 3.0 of room.
        function fl(x) { return int((x + 0.001) * 10 + (x < 0 ? -1 : 0)) / 10 }
        { rd += $2; rm += $3 }   # the rows of the ledger, on stdin
        END {
            G = 1073741824
            size = size_k / 1048576; avail = avail_k / 1048576
            while ((getline l < meminfo) > 0) {
                split(l, f, /[: ]+/)
                if (f[1] == "MemTotal") total = f[2] / 1048576
                if (f[1] == "MemAvailable") memavail = f[2] / 1048576
                if (f[1] == "SwapTotal") swt = f[2] / 1048576
                if (f[1] == "SwapFree") swf = f[2] / 1048576
            }
            if (total == 0 || size == 0) exit 1
            # The ARC: its size plus arc_room, within its cap; arcroom is its room to grow.
            arcroom = (arc_max - arc_size) / G; if (arcroom < 0) arcroom = 0
            if (arcroom > arc_room) arcroom = arc_room
            arc = arc_size / G + arcroom
            disk = size - 1 - rd; if (avail - 1 < disk) disk = avail - 1
            # The RAM disk as memory: the static rule charges what is reserved or
            # written, whichever is more (writes outside the ledger, or past a
            # reservation, count); the live one what reservations may still write,
            # the rest being off MemAvailable already.  A new job asks MAXMEM for
            # its own D too (D + M), so that is not here.
            used = size - avail
            ram = (rd > used) ? rd : used
            togo = rd - used; if (togo < 0) togo = 0
            mem = total - ram - arc - base - rm
            live = memavail - togo - arcroom - (swt - swf) - 2 - unused
            if (live < mem) mem = live
            printf "%.1f %.1f\n", fl(disk), fl(mem)
        }' <<<"$rows"
}
ssd_room() {  # [ROWS] -> "ROOM MOST" in GiB: what one more --reserve-ssd could get of the
    # SSD now, floored at 0, and the most one could ever get, the file system less the
    # floor; both 0 unless the host's scratch_mode is overflow or ssd (ram, or a table
    # that can't say: no job's scratch goes to the SSD here), or with no answer from df
    # (ROWS default the ledger's live rows; nothing is written)
    local rows floor=${SSD_FLOOR_GIB:-} mode size avail py=${BKO_HOSTS_PY:-$HOME/backotter-work/testvm/test2/t2lib/hosts.py}
    rows=${1-$(live_rows)}
    [ -n "$floor" ] || floor=$(python3 -I -B "$py" limit "$(hostname -s)" ssd_floor_gib 2>/dev/null)
    [[ $floor =~ ^[0-9]+([.][0-9]+)?$ ]] || floor=80
    mode=${SCRATCH_MODE:-$(python3 -I -B "$py" limit "$(hostname -s)" scratch_mode 2>/dev/null)}
    read -r size avail < <(df -Pk "$SSD" 2>/dev/null | awk 'NR == 2 { print $2, $4 }')
    awk -v floor="$floor" -v mode="$mode" -v size_k="${size:-0}" -v avail_k="${avail:-0}" '
        function fl(x) { x = int((x + 0.001) * 10 + (x < 0 ? -1 : 0)) / 10; return x < 0 ? 0 : x }
        $5 == "reserve" && $6 ~ /^ssd=[0-9]+([.][0-9]+)?$/ { held += substr($6, 5) }   # the rows, on stdin
        END {
            room = avail_k / 1048576 - floor - held; most = size_k / 1048576 - floor
            if (size_k + 0 == 0 || (mode != "overflow" && mode != "ssd")) room = most = 0
            printf "%.1f %.1f\n", fl(room), fl(most)
        }' <<<"$rows"
}
prune() {  # [PID]: drop rows (ledger and line) whose holder is gone, or PID's (ledger locked)
    local f pid rest keep
    for f in $LEDGER $WAITS; do
        keep=""
        while read -r pid rest; do
            # (A row that isn't PID first is junk: "." would pass as /proc/.)
            [[ $pid =~ ^[0-9]+$ ]] && [ -d /proc/$pid ] && [ "$pid" != "${1:-}" ] && keep+="$pid $rest"$'\n'
        done <$f
        put $f "$keep"
    done
}
after_line() {  # PID MAXDISK MAXMEM [LEDGER_ROWS WAIT_ROWS] -> "DISK MEM AHEAD HOT_C":
    # the room left for PID once those before it in line (everyone, if PID isn't in
    # it) who are to have theirs first have had it, how many are before it, and the
    # heat limit (ledger locked; --free passes the live rows instead, and locks
    # nothing). Before PID, in order: a waiter whose needs fit the room left now has
    # it; one whose needs don't is passed while it has waited (now - SINCE) less
    # than BACKFILL_MAX seconds, and held back for once it has (its needs come off
    # all the same); a row with no SINCE has waited forever. Only needs that fit in
    # MAXDISK/MAXMEM plus what the ledger's jobs hold are held back for. The figures
    # don't depend on PID's own needs: PID starts if they fit them. BACKFILL_MAX=0
    # holds back for everyone, as strict first come, first served does.
    # MAXMEM, and the MEM printed, are budget()'s joint memory: a row's DISK MEM
    # take its DISK of the RAM disk and its DISK + MEM of the memory, a waiter's
    # and a ledger row's alike.
    # (In tenths of a GiB: the figures come rounded to them, so t() rounds to
    # the nearest tenth and nothing here needs fl()'s tolerance -- half a tenth
    # of slack is fifty times it.)
    awk -v me="$1" -v d="$2" -v m="$3" -v hot="${HOT_C:-95}" -v now="$(date +%s)" \
        -v max="${BACKFILL_MAX:-1800}" '
        function t(x) { return int(x * 10 + (x < 0 ? -0.5 : 0.5)) }
        BEGIN { d = t(d); m = t(m); pd = d; pm = m; max += 0 }
        FILENAME == ARGV[1] { pd += t($2); pm += t($2) + t($3); next }   # the ledger
        NF == 0 { next }
        $1 == me { exit }
        { n++; if ($4 + 0 > 0 && $4 + 0 < hot + 0) hot = $4 + 0
          since = ($5 ~ /^[0-9]+$/) ? $5 + 0 : 0
          wd = t($2); wm = t($2) + t($3)
          if ((wd <= d && wm <= m) || (wd <= pd && wm <= pm && now - since >= max)) {
              d -= wd; m -= wm } }
        END { printf "%.1f %.1f %d %d\n", d / 10, m / 10, n, hot }' \
        <(if [ $# -ge 5 ]; then printf '%s\n' "$4"; else cat $LEDGER 2>/dev/null; fi) \
        <(if [ $# -ge 5 ]; then printf '%s\n' "$5"; else cat $WAITS 2>/dev/null; fi)
}
queue_up() {  # PID DISK MEM WHAT...: take a place in line, or change the needs at it (ledger locked)
    # (A new row's SINCE is now; a changed row keeps its SINCE, and so its age.)
    local pid=$1 d=$2 m=$3; shift 3
    if grep -q "^$pid " $WAITS; then
        rewrite $WAITS awk -v p="$pid" -v d="$d" -v m="$m" '$1 == p { $2 = d; $3 = m } { print }' $WAITS
    else
        add $WAITS "$pid $d $m ${HOT_C:-95} $(date +%s) $*"
    fi
}
leave_line() { rewrite $WAITS sed "/^$1 /d" $WAITS; }   # PID (ledger locked)
lock() { ledger_files; exec {lk}<$LEDGER.lock && flock -w 600 $lk; }
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
# MAXDISK WANTDISK MAXMEM WANTMEM: admission, with the same MiB of tolerance
# the figures are floored by (EPS_GIB; see the header), so that a --reserve of
# exactly the room offered is let in whichever way the last bit rounded.
fits() { awk -v a="$1" -v b="$2" -v c="$3" -v e="$4" 'BEGIN { exit !(b + 0 <= a + 0.001 && e + 0 <= c + 0.001) }'; }
# MAXDISK D MAXMEM M: the same against budget()'s or after_line()'s room, whose
# memory is for D + M (the header's "memory, charged D + M"); fits() is for
# the ceiling's figures, each a limit of its own, and the SSD's.
fits_room() { fits "$1" "$2" "$3" "$(awk -v d="$2" -v m="$4" 'BEGIN { print d + m }')"; }
parse() {  # LINE -> globals g grow ondisk d m (suffixes only at the end, in order); rc 1: a ':' in g
    local s=$1
    m=5 d=8 grow=0 ondisk=""
    [[ $s =~ %([0-9.]+)$ ]] && { m=${BASH_REMATCH[1]}; s=${s%"%$m"}; }
    [[ $s =~ =([0-9.]+)$ ]] && { d=${BASH_REMATCH[1]}; s=${s%"=$d"}; }
    [[ $s == *! ]] && { ondisk=1; s=${s%!}; }
    [[ $s =~ @([0-9]+)$ ]] && { grow=${BASH_REMATCH[1]}; s=${s%"@$grow"}; }
    g=$s
    # A ':' in it (a retired queue suffix) is refused by the callers.
    [[ $g != *:* ]]
}
ceiling() {  # -> "DISK MEM": the most one job could ever get on this host
    local arc_max arc_size base=${BASE_GIB:-}
    [ -n "$base" ] || base=$(python3 -I -B "${BKO_HOSTS_PY:-$HOME/backotter-work/testvm/test2/t2lib/hosts.py}" \
                                limit "$(hostname -s)" base_gib 2>/dev/null)
    [[ $base =~ ^[0-9]+([.][0-9]+)?$ ]] || base=6
    arc_max=$(awk '$1 == "c_max" { print $3 }' /proc/spl/kstat/zfs/arcstats 2>/dev/null)
    arc_size=$(awk '$1 == "size" { print $3 }' /proc/spl/kstat/zfs/arcstats 2>/dev/null)
    df -Pk $RAM | awk -v arc_max="${arc_max:-0}" -v arc_size="${arc_size:-0}" -v arc_room="${ARC_ROOM_GIB:-1}" \
                      -v base="$base" -v meminfo="${CMP_MEMINFO:-/proc/meminfo}" '
        # Down to a tenth with a MiB of tolerance, exactly as budget() floors
        # its own figures (EPS_GIB; see the header): a ceiling rounded up to a
        # tenth budget() never reaches is a --reserve that waits for ever.
        function fl(x) { return int((x + 0.001) * 10 + (x < 0 ? -1 : 0)) / 10 }
        NR == 2 { size = $2 / 1048576 }
        END { G = 1073741824
              arc = arc_size / G + arc_room; if (arc > arc_max / G) arc = arc_max / G   # as budget() counts it
              while ((getline l < meminfo) > 0) { split(l, f, /[: ]+/); if (f[1] == "MemTotal") t = f[2] / 1048576 }
              printf "%.1f %.1f\n", fl(size - 1), fl(t - size - arc - base - 2) }'
}
free_now() {  # -> "DISK MEM": the room a new --reserve could get on this host now, floored
    # at 0, nothing written.  It is exactly what admission would let in: a --reserve D M
    # starts at once when fits() passes against the ceiling (D <= its DISK, M <= its MEM)
    # and fits_room() against budget() (D <= its DISK, D + M <= its MEM), so the RAM
    # disk is the smaller of the two DISKs, and the memory, for D + M as budget()'s is,
    # the smaller of budget()'s and the most a job within the ceiling could take of it,
    # the ceiling's DISK + MEM.  The ledger's live rows are already off budget()'s own
    # figures (its disk = size - 1 - rd, its mem = total - the RAM disk reserved or used
    # - arc - base - rm), which also bring the live checks (MemAvailable, the tmpfs's
    # space, the ARC, what reservations haven't touched yet); the ceiling is the most one
    # job could ever get and is tested against the bare --reserve, as here.  So the rows
    # count once, in the same place as in admission.  (Taking them off the ceiling as
    # well, as this did until now, under-reported the memory by up to the 2 GiB of
    # headroom that ceiling() holds back and budget() doesn't: a --reserve this said was
    # too big for the host was admitted at once.)  It doesn't count who is in line
    # (after_line): the waiters are reported beside it, since room is offered to them
    # first.  It sets FREE_WAITING, how many are in line (the WAITS file's live rows),
    # and FREE_AFTER, "DISK MEM" a new --reserve would be offered after them: the same
    # min(ceiling, ...) of after_line() on the same budget() figures and the same rows,
    # floored at 0, which is what admission tests a --reserve against once it is in
    # line (after_line's PID 0 is nobody's: everyone in line is before it).
    local cd cm bd bm ad am rows waits out
    read -r cd cm < <(ceiling) && [ -n "$cm" ] || return 1
    rows=$(live_rows) waits=$(live_rows $WAITS)
    read -r bd bm < <(budget "$rows") && [ -n "$bm" ] || return 1
    read -r ad am _ < <(after_line 0 "$bd" "$bm" "$rows" "$waits") && [ -n "$am" ] || return 1
    out=$(awk -v cd="$cd" -v cm="$cm" -v bd="$bd" -v bm="$bm" -v ad="$ad" -v am="$am" '
        function fl(x) { x = int((x + 0.001) * 10 + (x < 0 ? -1 : 0)) / 10; return x < 0 ? 0 : x }   # down (a MiB of tolerance), then 0
        function lo(a, b) { return a + 0 < b + 0 ? a + 0 : b + 0 }
        BEGIN { cj = cd + cm   # the most of the joint memory a job within the ceiling takes
                printf "%.1f %.1f\n%.1f %.1f\n", fl(lo(cd, bd)), fl(lo(cj, bm)), fl(lo(cd, ad)), fl(lo(cj, am)) }') &&
        [ -n "$out" ] || return 1
    FREE_AFTER=${out#*$'\n'}
    FREE_WAITING=$(awk 'NF { n++ } END { print n + 0 }' <<<"$waits")
    printf '%s\n' "${out%%$'\n'*}"
}
if [ "${1:-}" = --ceiling ]; then
    [ $# -eq 1 ] || { echo "usage: cmp-run.sh --ceiling" >&2; exit 2; }
    ceiling; exit
fi
if [ "${1:-}" = --free ]; then
    [ $# -eq 1 ] || { echo "usage: cmp-run.sh --free" >&2; exit 2; }
    free_now || { echo "cmp-run.sh --free: can't tell what room there is here" >&2; exit 1; }
    echo "waiting $FREE_WAITING"
    echo "after $FREE_AFTER"
    exit 0
fi

if [ "${1:-}" = --ssd-free ]; then
    [ $# -eq 1 ] || { echo "usage: cmp-run.sh --ssd-free" >&2; exit 2; }
    read -r sr _ < <(ssd_room) && [ -n "$sr" ] || { echo "cmp-run.sh --ssd-free: can't tell what room $SSD has" >&2; exit 1; }
    echo "$sr"; exit 0
fi

if [ "${1:-}" = --reserve ] || [ "${1:-}" = --reserve-ssd ]; then
    # s: the SSD GiB of a --reserve-ssd (its row's ssd=), which holds no RAM disk.
    s=""
    if [ "$1" = --reserve-ssd ]; then
        [ $# -ge 4 ] && [[ $2 =~ ^[0-9]+([.][0-9]+)?$ && $3 =~ ^[0-9]+([.][0-9]+)?$ ]] ||
            { echo "usage: cmp-run.sh --reserve-ssd SSD_GIB MEM_GIB CMD ..." >&2; exit 2; }
        s=$2 d=0 m=$3
    else
        d=$2 m=$3
    fi
    shift 3
    read -r cd cm < <(ceiling)
    if [ -n "$s" ]; then
        read -r _ smax < <(ssd_room "")
        fits "${smax:-0}" "$s" "$cm" "$m" ||
            { echo "--reserve-ssd $s $m can never fit here (at most ${smax:-0} of $SSD, $cm of memory; 0.0: this host's scratch_mode gives the SSD no scratch)" >&2; exit 1; }
    else
        fits "$cd" "$d" "$cm" "$m" || { echo "--reserve $d $m can never fit here (at most $cd $cm)" >&2; exit 1; }
    fi
    while :; do
        lock || { sleep 30; continue; }
        prune
        if read -r maxd maxm < <(budget) && read -r maxd maxm _ hotc < <(after_line $$ "$maxd" "$maxm") &&
                ! hot "$hotc" && fits_room "$maxd" "$d" "$maxm" "$m" &&
                { [ -z "$s" ] || { read -r sr _ < <(ssd_room) && fits "${sr:-0}" "$s" 0 0; }; }; then
            add $LEDGER "$$ $d $m - reserve ${s:+ssd=$s }$*"; leave_line $$; unlock; break
        fi
        queue_up $$ "$d" "$m" reserve ${s:+"ssd=$s"} "$@"
        unlock; sleep 30
    done
    # Where the job's scratch is, for the wear log (iolog.sh's scratch=): the
    # caller's word, else ssd for a --reserve-ssd and ram for some RAM disk.
    ws=${WEAR_SCRATCH:-}
    [ -n "$ws" ] || { if [ -n "$s" ]; then ws=ssd; elif awk -v d="$d" 'BEGIN { exit !(d + 0 > 0) }'; then ws=ram; fi; }
    # In a scope of its own, as m still, so that iolog.sh can count what it
    # writes to disk (the wear log; WEAR_LOG=off: not). exec keeps $$, so the
    # reservation lasts as long as CMD.  setpriv --init-groups, not
    # systemd-run --uid/--gid: a scope's --uid keeps root's groups and drops
    # m's own, kvm among them (every VM a reservation ran failed so).
    if [ "${WEAR_LOG:-}" = off ]; then
        [ -n "$JOB_SLICE" ] || exec "$@"
        # In the slice, as this user, with this environment (setpriv --reset-env clears it).
        pre=() envs=()
        while IFS= read -r w; do pre+=("$w"); done < <(python3 -I -B "$BKO_HOSTS_PY" job-prefix "$H" "$(id -un)")
        while IFS= read -r -d '' kv; do envs+=("$kv"); done < <(env -0)
        [ ${#pre[@]} -gt 0 ] || { echo "no job-prefix for $H's job_slice $JOB_SLICE" >&2; exit 1; }
        exec "${pre[@]}" env -i "${envs[@]}" "$@"
    fi
    exec sudo -E systemd-run --quiet --scope --collect ${JOB_SLICE:+--slice="$JOB_SLICE"} -p IOAccounting=yes \
        setpriv --reuid="$(id -u)" --regid="$(id -g)" --init-groups \
        env PATH="$PATH" WEAR_TAG="${WEAR_TAG:-}" WEAR_SCRATCH="$ws" "$IOLOG" "reserve $*" -- "$@"
fi

tag=$1 slots=$2; shift 2
sb=${SLOT_BASE:-4}
[[ $slots =~ ^[1-9][0-9]*$ && $sb =~ ^[0-9]$ ]] && (( sb + slots <= 9 )) ||
    { echo "slots $((sb + 1))..$((sb + slots)): compare.py takes COMPARE_SLOT 0-9 only" >&2; exit 1; }
mkdir -p cmp-logs/$tag
Q=cmp-logs/$tag/queue
[ -d "$DISKS_DIR" ] || { [ -d "$(dirname "$DISKS_DIR")" ] && [ -w "$(dirname "$DISKS_DIR")" ] && mkdir -p "$DISKS_DIR"; }
if [ $# -gt 0 ]; then
    [ -z "${QUEUE_HOST:-}" ] || { echo "guests go in the queue on $QUEUE_HOST" >&2; exit 1; }
    read -r cd cm < <(ceiling)
    seen=" "
    for line; do
        parse "$line" || { echo "$line: no ':' in a guest name" >&2; exit 1; }
        fits "$cd" "$d" "$cm" "$m" || { echo "$line can never fit (at most =$cd %$cm)" >&2; exit 1; }
        [ -z "$ondisk" ] || [ -d "$DISKS_DIR" ] || { echo "$line: no $DISKS_DIR" >&2; exit 1; }
        case $seen in *" $g "*) echo "$g is queued twice" >&2; exit 1 ;; esac
        seen+="$g "
    done
    printf '%s\n' "$@" >$Q
    rm -f $Q.disks   # cmp-pop.sh's mark is the new queue's, from its workers
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
    local rc nd=""
    [ -d "$DISKS_DIR" ] || nd=nodisk   # no "!" line for a worker without DISKS_DIR
    [ -z "${gaveup:-}" ] || nd=anydisk   # ... until it gave up waiting (nodisk_wait)
    if [ -n "${QUEUE_HOST:-}" ]; then
        timeout 90 ssh -n -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=10 \
            -o ServerAliveCountMax=3 -o ControlMaster=no -o ControlPath=none "$QUEUE_HOST" \
            "cd ~/backotter-work && sh testvm/compare/cmp-pop.sh $Q $1 $2 $H $nd" {lk}>&-
        rc=$?
        case $rc in 0|1|3) return $rc ;; esac
        log "ssh pop failed ($rc): see $Q.taken on $QUEUE_HOST for a line taken but not run"
        return 1
    fi
    sh testvm/compare/cmp-pop.sh $Q "$1" "$2" $H $nd {lk}>&-
    rc=$?
    case $rc in 0|1|3) return $rc ;; esac
    log "cmp-pop.sh failed ($rc)"
    return 1
}
log() { echo "$(date -u +%T) $H slot $slot: $*" >>cmp-logs/$tag/progress; }
nodisk_wait() {  # "! N K" (cmp-pop.sh nodisk, only "!" lines left) -> rc 1: give up waiting
    # The caller's bangs (polls in a row with the same N and no mark) and bangn (that N).
    local n k
    read -r _ n k <<<"$1"
    if [ "${k:-0}" = 1 ]; then bangs=0 bangn=""; return 0; fi   # a host with DISKS_DIR asked: theirs
    if [ "$n" = "$bangn" ]; then bangs=$((bangs + 1)); else bangs=1 bangn=$n; fi
    [ "$bangs" -lt "${NODISK_POLLS:-20}" ]
}

worker() {  # SLOT
    local slot=$1 line rc g bad grow ondisk d m lk maxd maxm ahead hotc jp scope args why hi dd wt waiting="" me=$BASHPID
    local bangs=0 bangn="" gaveup=""
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
                why="nothing fits ${maxd}G RAM disk, ${maxm}G memory (for D + M)"
                [ "$ahead" -gt 0 ] && why+=" after $ahead before it in line"
                if [[ $line == '! '* ]]; then
                    why="only \"!\" lines left ($(set -- $line; echo $2) of them), and no $DISKS_DIR here: they are for the other hosts"
                    if ! nodisk_wait "$line"; then
                        gaveup=1
                        log "no host with $DISKS_DIR asked for the \"!\" lines in $bangs polls: taking them (a FAIL row each)"
                    fi
                fi
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
            bad=""; parse "$line" || bad=1
            scope=bko-$tag-$slot-$(date +%s).scope
            add $LEDGER "$me $d $m $scope $tag $line"
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

        if [ -n "$bad" ]; then   # an older cmp-run.sh's queue line (QUEUE_HOST)
            log "$g: no ':' in a guest name, not run"; echo "$g 99 $H" >>cmp-logs/$tag/done
            if lock; then prune $me; unlock; fi; continue
        fi
        args=()
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
        # The job's wear tag (and WEAR_LOG, when set): sudo resets the
        # environment, so env sets them.
        wt=${WEAR_TAG:-cmp:$tag}:$g
        # CMP_CMD: a command run in compare.py's place, the same way, with
        # GUEST DISK MEM SLOT [ARGS] (rebuild-one.sh, or a stand-in for tests).
        if [ -n "${CMP_CMD:-}" ]; then
            sudo systemd-run --quiet --scope --unit=${scope%.scope} ${JOB_SLICE:+--slice="$JOB_SLICE"} -p MemoryHigh=${hi}M -p IOAccounting=yes \
                env WEAR_TAG="$wt" ${WEAR_LOG:+WEAR_LOG="$WEAR_LOG"} "$IOLOG" "$tag $g" -- env COMPARE_SLOT=$slot COMPARE_GROW_GIB=$grow $dd $E $CMP_CMD "$g" "$d" "$m" "$slot" "${args[@]}" \
                >cmp-logs/$tag/$g.log 2>&1 &
        else
            sudo systemd-run --quiet --scope --unit=${scope%.scope} ${JOB_SLICE:+--slice="$JOB_SLICE"} -p MemoryHigh=${hi}M -p IOAccounting=yes \
                env WEAR_TAG="$wt" ${WEAR_LOG:+WEAR_LOG="$WEAR_LOG"} "$IOLOG" "$tag $g" -- env COMPARE_SLOT=$slot COMPARE_GROW_GIB=$grow $dd $E python3 testvm/compare/compare.py "${args[@]}" "$g" \
                restic-bko rustic-bko restic-to-rustic rustic-to-restic >cmp-logs/$tag/$g.log 2>&1 &
        fi
        jp=$!
        # The row belongs to the job (sudo lives as long as it), not to this
        # shell, so killing the worker doesn't free room the job still holds.
        if lock; then rewrite $LEDGER sed "s/^$me /$jp /" $LEDGER; unlock; fi
        wait $jp; rc=$?
        log "$g exit $rc"
        echo "$g $rc $H" >>cmp-logs/$tag/done
        if lock; then prune $jp; unlock; fi
    done
}
for s in $(seq 1 "$slots"); do worker "$((s + sb))" & sleep 5; done
wait
echo "$(date -u +%T) $H finished" >>cmp-logs/$tag/progress
