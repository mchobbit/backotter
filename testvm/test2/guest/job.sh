#!/usr/bin/env bash
# GuestTest2's job on a host (contract 2.1): guest/job.sh MODE GUEST RUN ATTEMPT OUT, under
# cmp-run.sh --reserve, from the run's private copy (update: from the synced tree).
#   MODE    try (vm-update --try), probe (--probe), update (the real form, with --merge-gate)
#           or console (redox and sculpt2610: no SSH)
#   OUT     this attempt's records: HOST_ROOT/runs/RUN[-rA]/guest/GUEST
# It composes the hook files, runs vm-update, keeps a failed run's state, and writes the row
# with t2-host row guest (row.id: its id; row-summary.json: what the Mac reads).
# Environment: T2_GUEST_RULES (the guest's rule codes), T2_GUEST_NEW=1 (established: false),
# T2_GUEST_PLAT, T2_GUEST_CPUS, T2_GUEST_MEMORY, T2_GUEST_HOSTS, T2_CELLS, T2_WORK or BKO_WORK,
# VM_UPDATE_RAM and, in update rounds, VM_UPDATE_ROUND.
# TERM goes to vm-update once, and this waits for it, so the reservation outlasts a merge: vm-update
# runs in a session of its own (a TERM to this job's group doesn't reach it twice over), and from
# the first TERM to the end of the row every further signal is ignored.
set -u
[[ $# -eq 5 ]] || { echo "usage: job.sh MODE GUEST RUN ATTEMPT OUT" >&2; exit 2; }
MODE=$1 G=$2 RUN=$3 A=$4 OUT=$5
here=$(dirname "$(realpath "$0")")
T2=$(dirname "$here") TV=$(dirname "$T2")
: "${BKO_WORK:=${T2_WORK:-$HOME/backotter-work}}"
export BKO_WORK
# A root under the Mac's own home arrives as ~/...: host_job quotes the values it exports.
for v in T2_HOST_ROOT T2_WORK BKO_WORK T2_CELLS T2_COPY_ROOT T2_LEDGER; do
    if [[ ${!v:-} == "~/"* ]]; then
        printf -v "$v" '%s' "$HOME/${!v#\~/}"
        export "${v?}"
    fi
done
RULES=${T2_GUEST_RULES:-} PLAT=${T2_GUEST_PLAT:-linux}
CELLS=${T2_CELLS:-}
mkdir -p "$OUT/hooks" || exit 2
H=$OUT/hooks

compose() {  # FILE WITH_IMAGE: a hook file of check-image.sh (if asked) and check-after.sh
    { printf 'RULES="%s"\n' "$RULES"
      [[ $2 == image ]] && cat "$here/check-image.sh"
      if [[ " $RULES " == *" B-lin "* ]]; then
          echo 'boot_state() ('
          cat "$TV/compare/boot-state.sh"
          echo ')'
      fi
      cat "$here/check-after.sh"
      echo "echo '=== end'"
    } > "$1"
}

rc=0
child= group= stopped=0 forwarded=0
rm -f "$OUT/.stopped"
forward() {  # TERM to the child, once
    forwarded=1
    if [[ -n $group ]]; then kill -TERM -- "-$child" 2>/dev/null || kill -TERM "$child" 2>/dev/null
    else kill -TERM "$child" 2>/dev/null; fi
}
on_term() {
    trap '' TERM INT HUP
    stopped=1
    : > "$OUT/.stopped"    # rows_guest: stopped from outside, whatever the exit says
    if [[ -n $child ]]; then forward; fi
}
run_child() {  # COMMAND...: in its own session; returns (rc=) when it has ended, through any signal
    setsid "$@" &
    child=$!
    if [[ $stopped -eq 1 && $forwarded -eq 0 ]]; then forward; fi
    while :; do
        wait "$child"; rc=$?
        kill -0 "$child" 2>/dev/null || break
    done
}
case $MODE in
try|probe|update)
    compose "$H/before" image
    args=(--out "$OUT" --hook "before=$H/before")
    [[ -n ${T2_GUEST_CPUS:-} ]] && args+=(--cpus "$T2_GUEST_CPUS")
    [[ -n ${T2_GUEST_MEMORY:-} ]] && args+=(--memory "$T2_GUEST_MEMORY")
    [[ -n ${T2_GUEST_HOSTS+x} ]] && args+=(--hosts "$T2_GUEST_HOSTS")
    if [[ $MODE != probe ]]; then
        compose "$H/after" noimage
        args+=(--hook "after=$H/after")
    fi
    case $MODE in
    try)
        args+=(--try --keep-failed)
        if [[ ${T2_GUEST_NEW:-0} == 1 ]]; then
            { cat "$here/new-guest.sh"; echo "echo '=== end'"; } > "$H/new"
            args+=(--hook "before=$H/new")
            for n in restic rustic; do
                args+=(--push "$BKO_WORK/bko-bin/$PLAT/$n-bko=/tmp/$n-bko")
            done
        fi ;;
    probe)
        args+=(--probe --keep-failed) ;;
    update)
        args+=(--write-fingerprint --merge-gate "python3 -B $T2/t2-host tool guest gate --guest $G --out $OUT") ;;
    esac
    trap on_term TERM INT HUP
    run_child "$TV/vm-update" "${args[@]}" "$G" ;;
console)
    probe=
    case $G in
    redox) probe=$here/probe-redox.sh ;;
    sculpt*) probe=$here/probe-sculpt.sh ;;
    *) echo "job.sh: no console probe for $G" >&2; rc=1 ;;
    esac
    if [[ -n $probe ]]; then
        group=1              # its helpers in the foreground must hear it too; its EXIT trap takes one stop
        trap on_term TERM INT HUP
        run_child "$probe" "$G" "$OUT"
    fi ;;
*)
    echo "job.sh: unknown mode $MODE" >&2; exit 2 ;;
esac

trap '' TERM INT HUP   # the failed state and the row get written, inside this reservation (timeout's own child still dies on time)
t2host=(python3 -B "$T2/t2-host")
if [[ $rc -ne 0 && $MODE != update && $MODE != console ]]; then
    "${t2host[@]}" tool guest failed-state --guest "$G" --run "$RUN" --attempt "$A" --out "$OUT" ||
        echo "job.sh: failed-state didn't finish" >&2
fi
timeout -k 5 300 "${t2host[@]}" row guest --guest "$G" --run "$RUN" --attempt "$A" --out "$OUT" --mode "$MODE" --rc "$rc" \
    ${CELLS:+--cells "$CELLS"} > "$OUT/row.id"
wrc=$?
if [[ $MODE != update && $MODE != console ]]; then
    "${t2host[@]}" tool guest failed-state --guest "$G" --run "$RUN" --attempt "$A" --out "$OUT" --after-row ||
        echo "job.sh: failed-state didn't finish" >&2
fi
[[ $wrc -eq 0 ]] || echo "job.sh: the row of $G wasn't written (t2-host exit $wrc)" >&2
exit "$wrc"
