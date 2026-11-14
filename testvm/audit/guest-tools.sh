#!/usr/bin/env bash
# The tools on each test guest: probe.sh's listing of every executable on
# the usual bin dirs, kept per day, and what appeared or disappeared since
# the run before.  On a KVM test host (Chopin), from ~/backotter-work/testvm.
#
# Usage: guest-tools.sh [-j JOBS] [-n] [GUEST ...]
#        guest-tools.sh --diff OLD.txt NEW.txt
#
#   GUEST    the guests to probe (default every one in ~/backotter-work/vms
#            but those never probed: sculpt2610, redox, freebsd15-builder,
#            haiku6*, iso, old)
#   -j JOBS  how many at once (default 3)
#   -n       say what would run, and run nothing
#   --diff   the tools that appeared (+) and disappeared (-) between two
#            listings
#
# A guest is booted as a throwaway copy (compare/guest-run.sh, --ram) under
# cmp-run.sh --reserve RESERVE (default "1 5": a 4 GiB guest, its overlay in
# /dev/shm).  A guest that asks for a passphrase at boot and has it as
# luks-passphrase or zfs-passphrase (omarchy, ubuntu26-zfsmirror) is read
# instead: sudo vm-mount --ro --key FILE, probe.sh under chroot, vm-umount.
# Ports: 2700-2779 only (2780-2799 are left to others); job slot S takes the
# first free one of 2700+S, 2700+S+JOBS, ..., so no two jobs share one.
#
# Out: ~/backotter-work/audit/guest-tools/YYYYMMDD/GUEST.txt (the listing),
# GUEST.log (the run's output), GUEST.failed.txt (a probe whose listing
# doesn't end with "end": never compared).  Each guest's listing is
# compared with its newest one in an earlier dated dir.  Exit 1 if any
# probe failed, 2 on a usage error.
#
# Never kills a process it didn't start (by PID only).

set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
TESTVM=$(cd "$here/.." && pwd)
WORK=${BKO_WORK:-$HOME/backotter-work}
VMS=$WORK/vms
OUT_ROOT=${GT_OUT:-$WORK/audit/guest-tools}
CMP_RUN=${CMP_RUN:-$WORK/cmp-run.sh}
GUEST_RUN=${GUEST_RUN:-$TESTVM/compare/guest-run.sh}
VM_MOUNT=${VM_MOUNT:-$TESTVM/vm-mount}
VM_UMOUNT=${VM_UMOUNT:-$TESTVM/vm-umount}
# CMP_RUN, GUEST_RUN, VM_MOUNT, VM_UMOUNT and SUDO (default "sudo -n")
# replace the real ones in tests.
read -ra SUDO <<<"${SUDO-sudo -n}"
PORT_LO=2700 PORT_HI=2779
read -r RES_DISK RES_MEM <<<"${RESERVE:-1 5}"
SKIP_RE='^(sculpt2610|redox|freebsd15-builder|haiku6.*|iso|old)$'

die() { echo "guest-tools: $*" >&2; exit 2; }

# The tool names of a listing, sorted, once each.
tools() { awk '$1 == "exe" {print $3}' "$1" | sort -u; }

diff_listings() {  # OLD NEW: "+name" and "-name" lines
    comm -13 <(tools "$1") <(tools "$2") | sed 's/^/+/'
    comm -23 <(tools "$1") <(tools "$2") | sed 's/^/-/'
}

if [[ ${1:-} == --diff ]]; then
    [[ $# -eq 3 && -f $2 && -f $3 ]] || die "usage: guest-tools.sh --diff OLD.txt NEW.txt"
    diff_listings "$2" "$3"
    exit 0
fi

jobs=3 dry=no
while [[ $# -gt 0 ]]; do
    case $1 in
        -j) [[ ${2:-} =~ ^[1-9][0-9]?$ ]] || die "-j takes 1-99"; jobs=$2; shift 2 ;;
        -n) dry=yes; shift ;;
        -h|--help) sed -n 2,31p "$0"; exit 0 ;;
        -*) die "unknown option $1" ;;
        *) break ;;
    esac
done
(( jobs <= PORT_HI - PORT_LO + 1 )) || die "-j $jobs: more jobs than ports"

guests=()
if [[ $# -gt 0 ]]; then
    for g; do
        if [[ $g =~ $SKIP_RE ]]; then echo "$g: skipped (never probed)"; continue; fi
        [[ -d $VMS/$g ]] || die "no guest $VMS/$g"
        guests+=("$g")
    done
else
    for d in "$VMS"/*/; do
        g=$(basename "$d")
        [[ $g =~ $SKIP_RE || $g == .* ]] && continue
        guests+=("$g")
    done
fi
[[ ${#guests[@]} -gt 0 ]] || die "no guests to probe"

# The passphrase file that makes a guest one to mount, not boot.
mount_key() {
    local f
    for f in luks-passphrase zfs-passphrase; do
        [[ -f $VMS/$1/$f ]] && { echo "$VMS/$1/$f"; return 0; }
    done
    return 1
}

# The guest's images, the boot disk first, as vm-run attaches them: the
# extra disks are vm-run-options' --disk ones.
images() {
    local dir=$VMS/$1 opts=() extra=() main=() i p
    [[ -f $dir/vm-run-options ]] && read -ra opts < <(tr '\n' ' ' <"$dir/vm-run-options")
    for ((i = 0; i + 1 < ${#opts[@]}; i++)); do
        [[ ${opts[i]} == --disk ]] && extra+=("${opts[i + 1]}")
    done
    for p in "$dir"/*.qcow2; do
        [[ -f $p ]] || continue
        p=${p##*/}
        [[ $p == .* || " ${extra[*]:-} " == *" $p "* ]] || main+=("$p")
    done
    [[ ${#main[@]} -eq 1 ]] || { echo "$dir: not one boot image (${main[*]:-none})" >&2; return 1; }
    echo "$dir/${main[0]}"
    for p in ${extra[@]+"${extra[@]}"}; do echo "$dir/$p"; done
}

port_free() {
    command -v ss >/dev/null 2>&1 || return 0
    [[ -z $(ss -Hltn "sport = :$1" 2>/dev/null) ]]
}

# Slot S's first free port: 2700+S, then every JOBS-th port after it.
slot_port() {
    local p
    for ((p = PORT_LO + $1; p <= PORT_HI; p += jobs)); do
        port_free "$p" && { echo "$p"; return 0; }
    done
    return 1
}

day=$(date +%Y%m%d)
DAY=$OUT_ROOT/$day

if [[ $dry == yes ]]; then
    for g in "${guests[@]}"; do
        if k=$(mount_key "$g"); then
            echo "$g: mount --ro --key ${k##*/} $(images "$g" 2>&1 | tr '\n' ' ')"
        else
            echo "$g: boot (guest-run.sh, cmp-run.sh --reserve $RES_DISK $RES_MEM)"
        fi
    done
    echo "jobs $jobs, ports: slot S takes $PORT_LO+S, +$jobs ... up to $PORT_HI; out: $DAY"
    exit 0
fi

mkdir -p "$DAY" || die "can't make $DAY"
RUN=$(mktemp -d "$DAY/.run.XXXXXX") || die "can't make a run dir in $DAY"
printf '%s\n' "${guests[@]}" >"$RUN/queue"

pop() {  # the queue's next guest, or status 1
    local g
    until mkdir "$RUN/lock" 2>/dev/null; do sleep 0.2; done
    g=$(head -n 1 "$RUN/queue")
    [[ -z $g ]] || { tail -n +2 "$RUN/queue" >"$RUN/queue.new"; mv "$RUN/queue.new" "$RUN/queue"; }
    rmdir "$RUN/lock"
    [[ -n $g ]] && echo "$g"
}

# Booted: probe.sh copied to /root and run, under a reservation.
probe_boot() {  # GUEST PORT RAW
    WEAR_TAG="audit:gt-$day:$1" "$CMP_RUN" --reserve "$RES_DISK" "$RES_MEM" \
        env T=900 "$GUEST_RUN" "$1" "$2" "sh /root/probe.sh" "$here/probe.sh" >"$3" 2>&1 &
    jp=$!
    wait "$jp"
}

# Mounted read-only: probe.sh under chroot, so the guest's own symlinks
# resolve; its uname is the host's, so that line says so instead.
probe_mount() {  # GUEST KEY RAW
    local imgs=() args=() i rc
    while IFS= read -r i; do imgs+=("$i"); done < <(images "$1" 2>>"$3")
    [[ ${#imgs[@]} -gt 0 ]] || return 1
    for i in "${imgs[@]:1}"; do args+=(--disk "$i"); done
    mp=$(mktemp -d "$RUN/mnt.$1.XXXXXX") || return 1
    ${SUDO[@]+"${SUDO[@]}"} timeout -k 10 600 "$VM_MOUNT" --ro --key "$2" ${args[@]+"${args[@]}"} "${imgs[0]}" "$mp" >>"$3" 2>&1 &
    jp=$!
    wait "$jp"; rc=$?
    if [[ $rc -eq 0 ]]; then
        ${SUDO[@]+"${SUDO[@]}"} timeout -k 10 300 chroot "$mp" /bin/sh -s <"$here/probe.sh" 2>>"$3" |
            sed 's/^os uname .*/os uname - (not booted: read from a read-only mount)/' >>"$3" &
        jp=$!
        wait "$jp"; rc=$?
    fi
    unmount
    return "$rc"
}

unmount() {
    [[ -n ${mp:-} ]] || return 0
    if ${SUDO[@]+"${SUDO[@]}"} "$VM_UMOUNT" "$mp" >>"$RUN/umount.log" 2>&1 || ! mountpoint -q "$mp" 2>/dev/null; then
        rmdir "$mp" 2>/dev/null
    else
        echo "guest-tools: $mp is still mounted (vm-umount failed: $RUN/umount.log)" >&2
    fi
    mp=""
}

# One worker per slot: guests from the queue, one after another.
worker() {  # SLOT
    local g port raw lst k st
    jp="" mp=""
    trap '[[ -z $jp ]] || kill -TERM "$jp" 2>/dev/null; wait; unmount; exit 1' TERM INT
    while g=$(pop); do
        raw=$DAY/$g.log lst=$DAY/$g.txt.new
        : >"$raw"
        if k=$(mount_key "$g"); then
            echo "$g: mounting (${k##*/})"
            probe_mount "$g" "$k" "$raw"
        elif port=$(slot_port "$1"); then
            echo "$g: booting on port $port"
            probe_boot "$g" "$port" "$raw"
        else
            echo "no free port for slot $1" >>"$raw"
        fi
        st=$?; jp=""
        grep -E '^(os|exe) |^end$' "$raw" >"$lst"
        if [[ $(tail -n 1 "$lst") == end ]] && grep -q '^exe ' "$lst"; then
            mv -f "$lst" "$DAY/$g.txt"; rm -f "$DAY/$g.failed.txt"
            echo "ok" >"$RUN/status.$g"
        else
            mv -f "$lst" "$DAY/$g.failed.txt"
            echo "failed: no complete listing (exit $st; $raw)" >"$RUN/status.$g"
        fi
    done
}

# Never rm -r over a mount vm-umount left behind.
tidy() {
    if compgen -G "$RUN/mnt.*" >/dev/null; then echo "guest-tools: kept $RUN: a mount is left in it" >&2; else rm -rf "$RUN"; fi
}
pids=()
stop() {
    trap '' INT TERM
    echo "guest-tools: stopping" >&2
    [[ ${#pids[@]} -eq 0 ]] || kill -TERM "${pids[@]}" 2>/dev/null
    wait
    tidy
    exit 1
}
trap stop INT TERM
for ((s = 0; s < jobs && s < ${#guests[@]}; s++)); do
    worker "$s" & pids+=($!)
done
wait

# The report, guest by guest: against each one's newest earlier listing.
failed=0
echo "== tools per guest, $day"
for g in "${guests[@]}"; do
    st=$(cat "$RUN/status.$g" 2>/dev/null || echo "failed: not run")
    if [[ $st != ok ]]; then
        echo "$g: FAILED PROBE: ${st#failed: }"; failed=$((failed + 1)); continue
    fi
    n=$(tools "$DAY/$g.txt" | wc -l | tr -d ' ')
    prev=""
    for d in $(ls -1 "$OUT_ROOT" 2>/dev/null | grep -E '^[0-9]{8}$' | sort -r); do
        [[ $d < $day && -f $OUT_ROOT/$d/$g.txt ]] && { prev=$d; break; }
    done
    if [[ -z $prev ]]; then echo "$g: $n tools, the first listing"; continue; fi
    ch=$(diff_listings "$OUT_ROOT/$prev/$g.txt" "$DAY/$g.txt" | tr '\n' ' ' | sed 's/ $//')
    echo "$g: $n tools, against $prev: ${ch:-no change}"
done
tidy
[[ $failed -eq 0 ]] || { echo "$failed failed probes"; exit 1; }
