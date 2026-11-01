# Host-wide slots for our guests on a port range, so that run-all.sh and
# stick/build-round.sh (Cinqcent's builds) stay within the host's limit
# together: a slot is a lock on a file in /tmp/bko-guest-slots-RANGE
# (BKO_SLOT_DIR replaces /tmp/bko-guest-slots), held from before the guest
# boots until it is gone (its QEMU inherits the lock).  QEMU of ours on the
# range without a slot (started by hand) count too, from when they run.
# Sourced by bash.
#
#   slot_take MAX RANGE   wait until fewer than MAX slots are held, counting
#                         those QEMU, then hold one; exports BKO_GUEST_SLOT
#                         (QEMU's environment says it holds a slot)
#   slot_give             let it go
#   slot_count RANGE      held slots and slotless QEMU, now
#
# Needs: flock, pgrep, /proc.

# QEMU on RANGE (lo-hi) with no BKO_GUEST_SLOT: guests that took no slot.
slot_others() {
    local lo=${1%-*} hi=${1#*-} n=0 pid port
    for pid in $(pgrep qemu-system); do
        port=$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null | grep -oE 'hostfwd=tcp:127\.0\.0\.1:[0-9]+' | head -n 1)
        port=${port##*:}
        [[ -n $port && $port -ge $lo && $port -le $hi ]] || continue
        grep -qz '^BKO_GUEST_SLOT=' "/proc/$pid/environ" 2>/dev/null || n=$((n + 1))
    done
    echo $n
}
slot_held() {  # DIR: slots someone holds
    local f n=0 fd
    for f in "$1"/slot.*; do
        [[ -e $f ]] || continue
        exec {fd}>>"$f"
        if flock -n "$fd"; then flock -u "$fd"; else n=$((n + 1)); fi
        exec {fd}>&-
    done
    echo $n
}
slot_count() { echo $(($(slot_held "${BKO_SLOT_DIR:-/tmp/bko-guest-slots}-$1") + $(slot_others "$1"))); }
slot_take() {
    local max=$1 d=${BKO_SLOT_DIR:-/tmp/bko-guest-slots}-$2 lk n fd got=""
    mkdir -p "$d" && chmod 1777 "$d" 2>/dev/null
    while :; do
        exec {lk}>>"$d/.lock"; flock "$lk"
        if [[ $(($(slot_held "$d") + $(slot_others "$2"))) -lt $max ]]; then
            for n in $(seq "$max"); do
                exec {fd}>>"$d/slot.$n"
                if flock -n "$fd"; then got=$n; break; fi
                exec {fd}>&-
            done
        fi
        exec {lk}>&-
        [[ -n $got ]] && break
        sleep 10
    done
    SLOT_FD=$fd
    export BKO_GUEST_SLOT=$d/slot.$got
}
slot_give() {
    [[ -n ${SLOT_FD:-} ]] && exec {SLOT_FD}>&-
    SLOT_FD="" BKO_GUEST_SLOT=""
    unset BKO_GUEST_SLOT
}
