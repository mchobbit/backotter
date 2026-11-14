#!/usr/bin/env bash
# A fast loop for debugging bko-holt on one guest: HoltTest1's phases, all
# in RAM (/mnt/bko-ram), the backup made once, and each try only rebuilds,
# from a fresh rescuer and blank targets, with the bko-holt just built.
# Run as m on a test host, from the testvm/ copy holding your changes:
#
#   ht1-debug.sh GUEST setup [GIB]    reserve GIB of RAM disk (default:
#                                     ram-size.sh's + 2) and 6 GiB of memory
#                                     in cmp-run.sh's ledger until `done`;
#                                     then dump the source and back it up
#   ht1-debug.sh GUEST build [SRC]    build bko-holt from SRC (default
#                                     ~/backotter-work/holt-src) with
#                                     build-holt.sh: on the host for Linux
#                                     (its Rust, 1.85.1), in a build guest
#                                     kept up between builds otherwise
#   ht1-debug.sh GUEST again [STAGE]  a rebuild without the safety checks,
#                                     through STAGE (default: close), the
#                                     rescuer left up as it ended
#   ht1-debug.sh GUEST ssh [CMD]      into that rescuer
#   ht1-debug.sh GUEST full           the whole rebuild, safety checks too
#   ht1-debug.sh GUEST verify         HoltTest1's verify of the last rebuild
#   ht1-debug.sh GUEST status         where things stand
#   ht1-debug.sh GUEST done           stop both VMs, free the reservation,
#                                     delete the RAM-disk run
#
# A typical loop: setup once; then edit holt/, rsync it to SRC, `build`,
# `again partition` (or the stage that failed) until it passes, `again`,
# `verify`; `done`.  Confirm the fix with a normal HoltTest1 run after.
#
# HT_PORT (default 2698) is the rescuer's and the guests' SSH port, and
# HT_PORT + 1 the build guest's: give each debugging session on a host its
# own, from the 2690-2699 of runs by hand (ht1-queue.sh hands out none of
# them).  The run's VMs are found by their disks in its directory, never by
# the port.  The run is RUN "debug" of ht1.sh (HT_RAM=1, HT_HOLT the build
# here, HT_QUICK, HT_THROUGH, HT_KEEP: see ht1.sh), in
# /mnt/bko-ram/holttest1d/debug/GUEST, kept there as it is between calls
# (HT_CLEAN=0: nothing deleted, nor moved to /bkoscratch) under setup's
# reservation (HT_RESERVE_PID, its ledger PID); this script's own state
# (the bko-holt build, cargo's target/, the reservation) in
# /mnt/bko-ram/holttest1d/debug-state/GUEST.  setup's default size is the
# run's (ram-size.sh) plus 2 GiB for that state.
set -uo pipefail

[[ $# -ge 2 ]] || { sed -n '2,/^set -uo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 2; }
G=$1 CMD=$2; shift 2
# A plain name: R and ST below are deleted by `done`.
[[ $G =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "ht1-debug: '$G' is no guest's name" >&2; exit 2; }
HERE=$(dirname "$(realpath "$0")")
W=/home/m/backotter-work
PORT=${HT_PORT:-2698}
[[ $PORT =~ ^[1-9][0-9]{3,4}$ ]] && ((PORT >= 1024 && PORT < 65535)) || { echo "ht1-debug: HT_PORT=$PORT: a port, 1024-65534" >&2; exit 2; }
BPORT=$((PORT + 1))
R=/mnt/bko-ram/holttest1d/debug/$G
ST=/mnt/bko-ram/holttest1d/debug-state/$G
# The run's VMs (the rescuer kept up): a QEMU with its disks in R, never
# another's on the port.
RPAT="^qemu-system.*$(sed 's/[][\.*^$]/\\&/g' <<<"$R")/"
BIN=/bkoscratch/holttest1d/bin
case $G in
    freebsd*|hardenedbsd*|ghostbsd*) PLAT=freebsd ;;
    netbsd*|alpinebsd*) PLAT=netbsd ;;
    openbsd*) PLAT=openbsd ;;
    dragonfly*) PLAT=dragonfly ;;
    omnios*|oi*) PLAT=illumos ;;
    haiku*) PLAT=haiku ;;
    redox|sculpt*) echo "ht1-debug: $G is checked offline (ht1-offline.sh), with no rebuild to loop over" >&2; exit 2 ;;
    *) PLAT=linux ;;
esac
[[ -d $W/vms/$G ]] || { echo "ht1-debug: no guest $G in $W/vms" >&2; exit 2; }
mountpoint -q /mnt/bko-ram || { echo "ht1-debug: no RAM disk at /mnt/bko-ram" >&2; exit 1; }

ht1() {  # PHASE... (with the env: HT_QUICK, HT_THROUGH, HT_KEEP)
    # Left in RAM as it ends, under setup's reservation (reserve.pid: its ledger row's PID);
    # no variant, whose run would be another directory than R.
    sudo -E HT_RAM=1 HT_CLEAN=0 HT_RESERVE_PID="$(cat "$ST/reserve.pid" 2>/dev/null)" HT_HOLT="$ST/bko-holt" HT_PORT="$PORT" HT_VARIANT= "$HERE/ht1.sh" "$G" debug "$@"
    local rc=$?
    echo "--- $R/report.txt; logs in $R/logs"
    return $rc
}
held() { [[ -s $ST/reserve.pid ]] && ps -p "$(cat "$ST/reserve.pid")" >/dev/null 2>&1; }
rescuer_up() { pgrep -f "$RPAT" >/dev/null; }
# The PID of a step (ht1.sh) at work in R, if one is.
running() { local p=""; read -r p 2>/dev/null <"$R/.ht/pid"; [[ $p =~ ^[0-9]+$ ]] && tr '\0' ' ' 2>/dev/null <"/proc/$p/cmdline" | grep -q ht1 && echo "$p"; return 0; }

# A rebuild needs setup's backup.
case $CMD in again|full|verify)
    [[ -f $ST/bko-holt && -s $R/dumps/src.json ]] && grep -q '^RESULT: PASS' "$ST/setup.result" 2>/dev/null ||
        { echo "ht1-debug: run setup first (it hasn't passed: $R/report.txt)" >&2; exit 1; } ;;
esac
case $CMD in
setup)
    sudo install -d -o "$(id -u)" -g "$(id -g)" "$ST" "$ST/tmp"
    if ! held; then
        local_gib=${1:-}
        if [[ -z $local_gib ]]; then
            # The run's room (ram-size.sh: logged peaks, else ram-sizes.tsv) plus 2 GiB
            # of this script's own (the bko-holt build, cargo's target/).
            local_gib=$("$HERE/ram-size.sh" "$G" 2>/dev/null | awk 'NF >= 3 { print $2 + 2 }')
            if [[ -z $local_gib ]]; then   # no ram-size.sh beside it: the old rule
                used=$(du -s --block-size=1G "$W/vms/$G"/*.qcow2 2>/dev/null | awk '{s += $1} END {print s + 0}')
                local_gib=$(( used * 5 / 2 + 4 ))
            fi
        fi
        echo "Reserving ${local_gib} GiB of RAM disk and 6 GiB of memory (cmp-run.sh's ledger)"
        rm -f "$ST/reserve.pid"   # a dead holder's: the wait below reads the new one's
        # The debug steps run outside the holder's scope, so the wear log
        # (jobs.log) sees only the holder: sleep's writes.
        WEAR_TAG=ht1dbg:$G setsid bash -c "echo \$\$ > '$ST/reserve.pid'; exec '$W/cmp-run.sh' --reserve '$local_gib' 6 sleep infinity" \
            </dev/null >"$ST/reserve.log" 2>&1 &
        # Until the ledger has its row (cmp-run.sh waits there while the
        # room isn't free); the pid file comes a moment after the start.
        for i in $(seq 3600); do
            if [[ -s $ST/reserve.pid ]]; then
                grep -q "^$(cat "$ST/reserve.pid") " /mnt/bko-ram/.reserve && break
                held || { echo "ht1-debug: the reservation ended:" >&2; cat "$ST/reserve.log" >&2; exit 1; }
            elif ((i > 15)); then
                echo "ht1-debug: the reservation didn't start ($ST/reserve.log)" >&2; exit 1
            fi
            sleep 2
        done
        echo "Reserved (pid $(cat "$ST/reserve.pid"))"
    fi
    if [[ ! -f $ST/bko-holt ]]; then
        if [[ $PLAT == linux ]]; then cp "$BIN/bko-holt" "$ST/bko-holt"; else cp "$BIN/$PLAT/bko-holt" "$ST/bko-holt"; fi
        echo "bko-holt: bin/'s for now ($(sha256sum "$ST/bko-holt" | cut -c1-12)); \`build\` replaces it"
    fi
    ht1 source backup; rc=$?
    grep '^RESULT' "$R/report.txt" > "$ST/setup.result"
    exit $rc
    ;;
build)
    src=${1:-$W/holt-src}
    [[ -f $src/Cargo.toml ]] || { echo "ht1-debug: no $src/Cargo.toml" >&2; exit 1; }
    sudo install -d -o "$(id -u)" -g "$(id -g)" "$ST" "$ST/tmp"
    # Linux: cargo's target/ in ST/tmp, in RAM, kept between builds (--keep
    # and the port are for the build guest of the others).
    HOLT_SRC=$src HOLT_OUT=$ST TMPDIR=$ST/tmp "$HERE/build-holt.sh" --keep "$PLAT" "$BPORT" || exit 1
    echo "bko-holt for the next try: $ST/bko-holt ($(sha256sum "$ST/bko-holt" | cut -c1-12))"
    ;;
again)
    HT_QUICK=1 HT_KEEP=1 HT_THROUGH=${1:-} ht1 rebuild
    ;;
full)
    HT_THROUGH= HT_KEEP= HT_QUICK= ht1 rebuild
    ;;
verify)
    ht1 verify
    ;;
ssh)
    # The key and user the last try's report names.
    rg=$(sed -n 's/.*rescuer kept up: ssh -i \([^ ]*\) .* \([a-z]*\)@127\.0\.0\.1.*/\1 \2/p' "$R/report.txt" 2>/dev/null | tail -1)
    [[ -n $rg ]] || { echo "ht1-debug: no kept rescuer in $R/report.txt (run again)" >&2; exit 1; }
    key=${rg% *} user=${rg#* }
    rescuer_up || { echo "ht1-debug: no rescuer up on port $PORT (run again)" >&2; exit 1; }
    exec sudo ssh -i "$key" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$user@127.0.0.1" "$@"
    ;;
status)
    echo "guest $G ($PLAT), run $R"
    held && echo "reserved: $(grep "^$(cat "$ST/reserve.pid") " /mnt/bko-ram/.reserve | cut -d' ' -f2,3 | sed 's/ / GiB RAM disk, /') GiB memory" || echo "not reserved"
    rescuer_up && echo "rescuer up on port $PORT" || echo "no rescuer up"
    p=$(running); [[ -z $p ]] || echo "a step at work: ht1.sh, pid $p"
    [[ -n $(ss -Hltn "sport = :$BPORT" 2>/dev/null) ]] && echo "build guest up on port $BPORT"
    [[ -f $ST/bko-holt ]] && echo "bko-holt: $(sha256sum "$ST/bko-holt" | cut -c1-12), built $(date -r "$ST/bko-holt" +%T)"
    [[ -d $R ]] && echo "RAM disk use: $(sudo du -sh "$R" | cut -f1)"
    [[ -f $R/logs/phases.tsv ]] && { echo "phases (KiB):"; sed 's/^/  /' "$R/logs/phases.tsv"; }
    [[ -f $R/report.txt ]] && { echo "last report:"; sed 's/^/  /' "$R/report.txt"; }
    ;;
done)
    p=$(running)
    [[ -z $p ]] || { echo "ht1-debug: a step (ht1.sh, pid $p) is at work in $R: let it end first" >&2; exit 1; }
    rescuer_up && { sudo pkill -f "$RPAT"; sleep 3; }
    [[ $PLAT == linux ]] || "$HERE/build-holt.sh" --stop "$PLAT" "$BPORT" 2>/dev/null
    # The holder is sudo (cmp-run.sh runs CMD through it): root's to signal.
    held && sudo kill "$(cat "$ST/reserve.pid")"
    sudo rm -rf --one-file-system "$R" "$ST"
    echo "done: $G's debugging run deleted, its reservation freed"
    ;;
*)
    echo "ht1-debug: setup, build, again, ssh, full, verify, status or done" >&2; exit 2 ;;
esac
