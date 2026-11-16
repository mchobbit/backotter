#!/usr/bin/env bash
# ARM64 A6's heavy tests, run ON BIGFOOT by testvm/arm64/a6-run.sh (the Mac's
# driver: see its header and docs/design/arm64.md, "What A6 built").  The
# stages, in order, one at a time:
#
#   preflight            Bigfoot itself, the five guests, A4's image and the
#                        binaries, each with its sha256; assembles RUN/bins
#   stic:GUEST           SticTest1's check of each arm64 guest (both
#                        archivers, all four directions: check.sh)
#   rescuer              make-rescuer.sh --arch arm64's rescuer-arm64.qcow2
#                        (kept when it is there already: --rebuild-rescuer)
#   holt:GUEST           HoltTest1 1ARM: ht1.sh's round trip of each guest
#   imgpin               A4's --test image pinned in RUN/t2/img-pin/linux-arm64
#   holt:GUEST+img       1ARM +img: the round trip with the image as rescuer
#   hosttest2:bin        gate/bincheck.sh static and version on RUN/bins
#   hosttest2:loop-linux gate/loop.sh linux (run-all.sh's arm64 profile:
#                        fs_roundtrip.py on ZFS on files and tmpfs)
#   hosttest2:loop-sparse gate/loop.sh sparse (sparse-rt.sh on zfs, zfs-nocomp)
#   hosttest2:facts      t2-host facts and t2-host self-test (T2_HOST_ROOT=RUN/t2)
#   t2:corpus            the passing 1ARM dumps as corpus candidates
#                        (collect-dumps.sh, corpus_build.rs, golden_plans.rs
#                        bless, leakscan): RUN/out/t2/corpus-arm64.patch
#   t2:limits            the checks' LIMIT needs against stic/limits.json, and
#                        each stage's time: RUN/out/t2/*-candidates.*
#   final                the guests' and the image's sha256 again (unchanged)
#
# Usage: a6-stages.sh --run DIR [--only S[,S]] [--skip S[,S]] [--bins DIR]
#                     [--img FILE] [--img-guests G,G] [--rebuild-rescuer]
#                     [--dry-run] [--list]
#   --run DIR     the run's directory: DIR/tree (the shipped tree), and what
#                 the run writes: runner.log, summary.tsv, summary.txt,
#                 state, exit, logs/STAGE.log, out/ (what --fetch copies),
#                 bins/, t2/ (img-pin, the raw dumps: never fetched)
#   --only, --skip  stage names or groups: stic, 1ARM (rescuer and holt:*),
#                 img (imgpin and holt:*+img), hosttest2, t2; preflight and
#                 final always run.  A holt stage brings rescuer along, an
#                 +img stage imgpin, unless --skip names them.
#   --bins DIR    the binaries (DIR/linux-arm64/{restic-bko,rustic-bko,bko-holt}
#                 or DIR/{...}); default bko-bin/linux-arm64's archivers and
#                 the newest native A3+ bko-holt on Bigfoot
#   --img FILE    the --test image (default A4's, a4-scratch/images/out/test/)
#   --img-guests  the +img panel (default debian13-arm64,debian13-zfs-arm64)
#   --dry-run     print each stage's command and check the inputs (read only)
#   --list        the stage names, one a line
#
# Rules it keeps (docs/design/arm64.md "Keeping it out of the user's way",
# testvm/HOSTS.md "Bigfoot"): one heavy stage at a time; each heavy command
# under ~/backotter-work/cmp-run.sh --reserve (the RAM disk's ledger every
# session shares), WEAR_TAG a6:STAMP:STAGE; a6-run.sh starts this script in
# bko.slice (a transient system service), so vm-run and cmp-run.sh's jobs
# are in the slice too.  Nothing under /bt, DantePool or BkoStore is read or
# written; DantePool's health is only read (zpool list).  No package, no
# system setting, no loop device on the host.  A stage runs in a process
# group of its own; TERM (a6-run.sh --stop) goes to that group, whose tools
# stop their own VMs, nbd devices, mounts and pools; then each process the
# stage started that outlived the group (a setsid'd vm-run and its QEMU) gets
# TERM by its PID, the run's own scratch goes, and the stage is ABORTED.
# Exit 0 when every stage passed (or was skipped as asked), 1 otherwise, 2 on
# bad usage.
set -uo pipefail

ME=$(readlink -f "$0")
TREE=$(cd "$(dirname "$ME")/../.." && pwd)
W=$HOME/backotter-work
GUESTS=(debian13-arm64 ubuntu26-arm64 fedora44-arm64 alpine324-arm64 debian13-zfs-arm64)
IMG_GUESTS=(debian13-arm64 debian13-zfs-arm64)
A4_IMG=$W/a4-scratch/images/out/test/backotter-live-linux-dev.20261116.1552b54.dirty-arm64.img
# A4's image's own binaries (its manifest's bin:*), which the +img pin must be.
A4_BINS="bko-holt=21d9cbfb21c225292049c851b3cdba6702339203c51185516bd10949b49eade2 restic-bko=b79fe55a8896d8dd3d50181804aa8b8fc04be93df038e9068d4bfb2f2fb50ff7 rustic-bko=bdfbda6dc234ff010fb3bb742aef8f1f20076548eb09229b903119c354b95ab0"
STIC_PORT=3290      # Bigfoot's stic_ports are 3280-3299 (hosts.json)
HOLT_PORT=2690      # ht1.sh's by-hand ports are 2690-2699
RESCUER_PORT=3279   # make-rescuer.sh --arch arm64's default
RESCUER_IMG=/bkoscratch/holttest1d/rescuer/rescuer-arm64.qcow2
RESCUER_NEW=/bkoscratch/holttest1d/rescuer/rescuer-arm64.new.qcow2   # the copy make-rescuer.sh boots
RESCUER_PIDF=/bkoscratch/holttest1d/rescuer/rescuer-arm64.qemu.pid   # its QEMU's pid file

die() { echo "a6-stages: $*" >&2; exit 2; }
usage() { sed -n '2,59p' "$ME" | sed 's/^# \{0,1\}//'; exit "${1:-2}"; }

RUN="" ONLY="" SKIP="" BINS_FROM="" IMG="" DRY=0 LIST=0 REBUILD_RESCUER=0
while [[ $# -gt 0 ]]; do
    case $1 in
        --run|--only|--skip|--bins|--img|--img-guests) [[ $# -ge 2 && -n $2 ]] || die "$1 needs a value" ;;
    esac
    case $1 in
        --run) RUN=$2; shift 2 ;;
        --only) ONLY+=",$2"; shift 2 ;;
        --skip) SKIP+=",$2"; shift 2 ;;
        --bins) BINS_FROM=$2; shift 2 ;;
        --img) IMG=$2; shift 2 ;;
        --img-guests) IFS=, read -ra IMG_GUESTS <<<"$2"; shift 2 ;;
        --rebuild-rescuer) REBUILD_RESCUER=1; shift ;;
        --dry-run) DRY=1; shift ;;
        --list) LIST=1; shift ;;
        -h|--help) usage 0 ;;
        *) die "unknown argument $1 (--help)" ;;
    esac
done
IMG=${IMG:-$A4_IMG}

# --- the stages ------------------------------------------------------------
STAGES=(preflight)
for g in "${GUESTS[@]}"; do STAGES+=("stic:$g"); done
STAGES+=(rescuer)
for g in "${GUESTS[@]}"; do STAGES+=("holt:$g"); done
STAGES+=(imgpin)
for g in "${IMG_GUESTS[@]}"; do STAGES+=("holt:$g+img"); done
STAGES+=(hosttest2:bin hosttest2:loop-linux hosttest2:loop-sparse hosttest2:facts t2:corpus t2:limits final)

if ((LIST)); then printf '%s\n' "${STAGES[@]}"; exit 0; fi
for g in "${IMG_GUESTS[@]}"; do
    [[ " ${GUESTS[*]} " == *" $g "* ]] || die "--img-guests: $g is not one of ${GUESTS[*]}"
done

# A name or group -> the stages it stands for.
expand() {  # NAME
    local n=$1 s
    case $n in
        stic) for s in "${STAGES[@]}"; do [[ $s == stic:* ]] && echo "$s"; done ;;
        1ARM|1arm|holt) echo rescuer; for s in "${STAGES[@]}"; do [[ $s == holt:* && $s != *+img ]] && echo "$s"; done ;;
        img|1ARM+img|+img) echo imgpin; for s in "${STAGES[@]}"; do [[ $s == *+img ]] && echo "$s"; done ;;
        hosttest2|HostTest2) for s in "${STAGES[@]}"; do [[ $s == hosttest2:* ]] && echo "$s"; done ;;
        t2|Test2) echo t2:corpus; echo t2:limits ;;
        *) for s in "${STAGES[@]}"; do [[ $s == "$n" ]] && { echo "$s"; return 0; }; done
           echo "?$n" ;;
    esac
}
declare -A WANT=()
pick() {  # LIST VALUE: every stage LIST names gets VALUE
    local x s
    IFS=, read -ra xs <<<"$1"
    for x in "${xs[@]}"; do
        [[ -n $x ]] || continue
        while read -r s; do
            [[ $s != \?* ]] || die "no stage or group ${s#\?} (--list; groups: stic 1ARM img hosttest2 t2)"
            WANT[$s]=$2
        done < <(expand "$x")
    done
}
if [[ -n ${ONLY#,} ]]; then
    for s in "${STAGES[@]}"; do WANT[$s]=0; done
    WANT[preflight]=1 WANT[final]=1
    pick "$ONLY" 1
    # What a selected stage needs: holt -> rescuer, +img -> imgpin (unless skipped below).
    for s in "${STAGES[@]}"; do
        [[ ${WANT[$s]} == 1 ]] || continue
        [[ $s == holt:* && $s != *+img ]] && WANT[rescuer]=1
        [[ $s == *+img ]] && WANT[imgpin]=1
    done
else
    for s in "${STAGES[@]}"; do WANT[$s]=1; done
fi
[[ -z ${SKIP#,} ]] || pick "$SKIP" 0
[[ ${WANT[preflight]} == 1 && ${WANT[final]} == 1 ]] || die "preflight and final always run (they record the inputs)"

[[ -n $RUN ]] || die "no --run DIR (--help)"
if ((DRY)); then
    RUN=$(realpath -m "$RUN")
else
    [[ -d $RUN/tree ]] || die "no $RUN/tree: a6-run.sh ships the tree there first"
    RUN=$(cd "$RUN" && pwd)
fi
STAMP=${RUN##*/}
[[ $STAMP =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || die "the run's directory name ($STAMP) must be a plain name"
HT_RUN=a6-$STAMP                     # HoltTest1's RUN: /bkoscratch/holttest1d/HT_RUN/GUEST
PFX=a6${STAMP//[^0-9a-z]/}           # SticTest1's PREFIX and the loops' bko... prefix part
PFX=${PFX:0:20}
BINS=$RUN/bins
OUT=$RUN/out
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$HOME/.cargo/bin

# --- the record ------------------------------------------------------------
LOG=$RUN/runner.log
SUMMARY=$RUN/summary.tsv
say() { if ((DRY)); then echo "$*"; else echo "$(date '+%F %T') $*" | tee -a "$LOG"; fi; }
row() {  # STAGE RESULT RC START END DETAIL
    local secs=$(( $5 - $4 ))
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$(date -d "@$4" '+%F %T')" "$(date -d "@$5" '+%F %T')" "$secs" "${6//$'\t'/ }" >>"$SUMMARY"
    say "== $1: $2 (rc $3, $secs s)${6:+: $6}"
}

# --- running one stage -------------------------------------------------------
CUR_PID=""
# make-rescuer.sh's QEMU (setsid -f: in no stage's process tree), while its pid
# file's PID is still that QEMU (its command line names the copy it boots, by
# vm-run's resolved path: /home/m/bko-arm64/scratch/..., so by the file's name).
rescuer_qemu() {
    local p; p=$(cat "$RESCUER_PIDF" 2>/dev/null) || p=""
    [[ $p =~ ^[0-9]+$ ]] && tr '\0' ' ' <"/proc/$p/cmdline" 2>/dev/null | grep -qF "/${RESCUER_NEW##*/}," && echo "$p"
    return 0
}
# Every process descended from PID (by /proc's parent links, at this moment): what the stage
# started, its sessions of their own too (vm_boot's setsid'd vm-run and QEMU), root's included.
descendants() {  # PID
    ps -e -o pid=,ppid= | awk -v top="$1" '{ kid[$2] = kid[$2] " " $1 }
        END { q = top; while (q != "") { split(q, a, " "); q = ""; for (i in a) { n = split(kid[a[i]], k, " "); for (j = 1; j <= n; j++) { print k[j]; q = q " " k[j] } } } }'
}
on_term() {
    trap '' TERM INT HUP
    local all=() p left
    say "stop asked: TERM to the stage's process group ${CUR_PID:-(none)}"
    if [[ -n $CUR_PID ]]; then
        mapfile -t all < <(descendants "$CUR_PID")
        kill -TERM -- "-$CUR_PID" 2>/dev/null
        for _ in $(seq 120); do kill -0 -- "-$CUR_PID" 2>/dev/null || break; sleep 1; done
        kill -0 -- "-$CUR_PID" 2>/dev/null && { say "the stage's group is still there after 120 s: KILL"; kill -KILL -- "-$CUR_PID" 2>/dev/null; }
        # What left the group (a VM in a session of its own) and outlived its parent's cleanup:
        # TERM to each of those PIDs, the ones this stage started and no other (vm-run passes it
        # on to its QEMU), then KILL after 60 s.
        for _ in 1 2; do
            left=()
            for p in "${all[@]}"; do [[ -d /proc/$p ]] && left+=("$p"); done
            ((${#left[@]})) || break
            say "stop: TERM to what the stage started and left running: ${left[*]}"
            kill -TERM "${left[@]}" 2>/dev/null; sudo -n kill -TERM "${left[@]}" 2>/dev/null
            for _ in $(seq 60); do
                left=(); for p in "${all[@]}"; do [[ -d /proc/$p ]] && left+=("$p"); done
                ((${#left[@]})) || break; sleep 1
            done
        done
        ((${#left[@]})) && { say "stop: KILL to ${left[*]}"; sudo -n kill -KILL "${left[@]}" 2>/dev/null; }
        # The rescuer stage's VM, which make-rescuer.sh's own trap stops, unless it was cut short.
        p=$(rescuer_qemu)
        if [[ -n $p && ${STAGE_NAME:-} == rescuer ]]; then
            say "stop: TERM to make-rescuer.sh's QEMU $p (its pid file)"
            sudo -n kill -TERM "$p" 2>/dev/null
            for _ in $(seq 30); do [[ -d /proc/$p ]] || break; sleep 1; done
            [[ -d /proc/$p ]] && { say "stop: KILL to $p"; sudo -n kill -KILL "$p" 2>/dev/null; }
        fi
        # This run's own scratch in the RAM disk and on /bkoscratch (its PREFIX's names only).
        sudo -n rm -rf --one-file-system /mnt/bko-ram/"$PFX"-* /bkoscratch/st1-scratch/"$PFX"-* 2>/dev/null
    fi
    STOPPED=1
}
STOPPED=0
# stage NAME DETAIL_FILE -- CMD...: CMD in a session (process group) of its own,
# its output in logs/NAME.log; rc 0 PASS, 3 SKIP (DETAIL_FILE says why), else FAIL.
stage() {
    local name=$1 df=$2; shift 3
    local lf=$RUN/logs/${name//[:+]/_}.log t0 t1 rc res detail
    if ((DRY)); then
        printf '   %q' "$@"; echo
        return 0
    fi
    if ((STOPPED)); then row "$name" ABORTED - "$(date +%s)" "$(date +%s)" "not started: the run was stopped"; return 1; fi
    say ">> $name ($lf)"
    : >"$df"
    t0=$(date +%s)
    # (a background child of this non-interactive shell leads no group, so setsid makes
    # its session in place: its PID is the group TERM goes to)
    setsid "$@" >"$lf" 2>&1 </dev/null &
    CUR_PID=$! STAGE_NAME=$name
    sleep 0.2
    [[ $(ps -o pgid= -p "$CUR_PID" 2>/dev/null | tr -d ' ') == "$CUR_PID" ]] || ! kill -0 "$CUR_PID" 2>/dev/null ||
        say "NOTE $name: its process group isn't its PID $CUR_PID ($(ps -o pgid= -p "$CUR_PID"))"
    echo "$name $CUR_PID $t0" >"$RUN/stage"
    wait "$CUR_PID"; rc=$?
    while kill -0 "$CUR_PID" 2>/dev/null; do wait "$CUR_PID"; rc=$?; done
    CUR_PID="" STAGE_NAME=""; rm -f "$RUN/stage"
    t1=$(date +%s)
    detail=$(head -c 400 "$df" 2>/dev/null | tr '\n' ' ')
    if ((STOPPED)); then res=ABORTED
    elif [[ $rc == 0 ]]; then res=PASS
    elif [[ $rc == 3 ]]; then res=SKIP
    else res=FAIL; [[ -n $detail ]] || detail=$(grep -E '^(FAIL|RESULT)|refused|error' "$lf" 2>/dev/null | tail -n 3 | head -c 400 | tr '\n' ' ')
    fi
    row "$name" "$res" "$rc" "$t0" "$t1" "$detail"
    [[ $res == PASS || $res == SKIP ]]
}

# DantePool's health, read only (a stage that uses host ZFS runs only beside a
# healthy pool): OK when zpool list says ONLINE, or says plainly there is no
# such pool (not imported: nothing to disturb).  Anything else is not OK: a
# zpool list that hangs (20 s: a suspended pool, a held namespace lock), any
# other health (SUSPENDED, DEGRADED, ...), any other error (no /dev/zfs).
# DANTE_WHY says which.
DANTE_WHY=""
dante_ok() {
    local out rc
    out=$(timeout 20 zpool list -H -o health DantePool 2>&1); rc=$?
    if [[ $rc == 0 && $out == ONLINE ]]; then DANTE_WHY="ONLINE"; return 0; fi
    if [[ $rc == 1 && $out == *"no such pool"* ]]; then DANTE_WHY="not imported"; return 0; fi
    if [[ $rc == 124 ]]; then DANTE_WHY="zpool list didn't answer in 20 s"
    elif [[ $rc == 0 ]]; then DANTE_WHY="health ${out:-empty}"
    else DANTE_WHY="zpool list exit $rc: $(head -n 1 <<<"$out" | cut -c1-120)"; fi
    return 1
}

# ============================================================================
# Stage bodies (run with A6_BODY=NAME in the stage's own process group).
# Each prints its log; DF (the detail file) gets one line for the summary.
# ============================================================================
body() {
    local name=$1 df=$2 g
    case $name in
    preflight) body_preflight "$df" ;;
    stic:*) body_stic "${name#stic:}" "$df" ;;
    rescuer) body_rescuer "$df" ;;
    holt:*+img) g=${name#holt:}; body_holt "${g%+img}" img "$df" ;;
    holt:*) body_holt "${name#holt:}" "" "$df" ;;
    imgpin) body_imgpin "$df" ;;
    hosttest2:bin) body_ht2_bin "$df" ;;
    hosttest2:loop-linux) body_ht2_loop linux "$df" ;;
    hosttest2:loop-sparse) body_ht2_loop sparse "$df" ;;
    hosttest2:facts) body_ht2_facts "$df" ;;
    t2:corpus) body_t2_corpus "$df" ;;
    t2:limits) body_t2_limits "$df" ;;
    final) body_final "$df" ;;
    *) echo "no stage $name" >"$df"; return 2 ;;
    esac
}

sha() { sha256sum "$1" | cut -d' ' -f1; }
is_aarch64() { LC_ALL=C readelf -h "$1" 2>/dev/null | grep -q 'Machine: *AArch64'; }

# The newest native A3+ bko-holt on Bigfoot: an aarch64 build whose load knows
# the architectures (A3's refusal text is in it), newest first by mtime.
newest_holt() {
    local f best="" bt=0 t
    for f in "$W"/a4-scratch/holt-bin/linux-arm64/bko-holt "$W"/a3-scratch/bf1/*/out/bko-holt \
             "$W"/holt-bin/linux-arm64/bko-holt /bkoscratch/holttest1d/bin/linux-arm64/bko-holt; do
        [[ -f $f ]] || continue
        is_aarch64 "$f" || continue
        grep -aqF 'restores a system only from a rescue system of its own architecture' "$f" || continue
        t=$(stat -c %Y "$f")
        ((t > bt)) && { best=$f bt=$t; }
    done
    echo "$best"
}

body_preflight() {
    local df=$1 bad=() f g h a
    echo "== Bigfoot"
    echo "host $(hostname -s) $(uname -m) kernel $(uname -r); user $(id -un) groups $(id -Gn)"
    [[ $(uname -m) == aarch64 ]] || bad+=("not an aarch64 host")
    [[ -c /dev/kvm && -w /dev/kvm ]] || bad+=("no writable /dev/kvm")
    sudo -n true 2>/dev/null || bad+=("no sudo -n")
    echo "cgroup: $(tail -n 1 /proc/self/cgroup)"
    grep -q '/bko.slice/' /proc/self/cgroup || echo "NOTE not in bko.slice (vm-run and cmp-run.sh put their jobs there themselves)"
    mountpoint -q /mnt/bko-ram || bad+=("no RAM disk at /mnt/bko-ram")
    df -h /home /mnt/bko-ram /bkoscratch/ 2>&1
    a=$(df -Pk /home | awk 'NR == 2 { print int($4 / 1048576) }')
    ((a >= 80)) || bad+=("/home has $a GiB free, under 80")
    echo "cmp-run.sh ceiling: $("$W/cmp-run.sh" --ceiling 2>&1)"
    echo "== DantePool (read only: zpool list)"
    h=$(timeout 20 zpool list -H -o name,health 2>&1); echo "$h"
    if dante_ok; then echo "DantePool: $DANTE_WHY"
    else echo "NOTE DantePool: $DANTE_WHY: the host-ZFS stages (holt:debian13-zfs-arm64, hosttest2:loop-*) will be SKIPped"; fi
    echo "== other QEMU (read only): ours would name /home/m/bko-arm64 or backotter-work"
    local q; q=$(pgrep -a qemu-system 2>/dev/null || true)
    if [[ -n $q ]]; then
        echo "$q" | cut -c1-300
        grep -qE 'bko-arm64|backotter-work|bkoscratch|bko-ram' <<<"$q" && bad+=("a bko QEMU is running already (another session's run?): $(grep -oE '^[0-9]+' <<<"$q" | tr '\n' ' ')")
    else echo "none"; fi
    echo "== the guests"
    for g in "${GUESTS[@]}"; do
        f=$W/vms/$g/$g.qcow2
        if [[ -f $f ]]; then echo "$g $(sha "$f") $(stat -c %s "$f") $(cat "$W/vms/$g/vm-run-options" 2>/dev/null)" | tee -a "$OUT/inputs.txt"
        else bad+=("no $f"); fi
        for h in id_ed25519 vm-run-options; do [[ -f $W/vms/$g/$h ]] || bad+=("no $W/vms/$g/$h"); done
    done
    echo "== the image (A4's --test build)"
    if [[ -f $IMG ]]; then echo "image $(sha "$IMG") $IMG" | tee -a "$OUT/inputs.txt"; else echo "NOTE no image $IMG: the +img stages will FAIL"; fi
    echo "== the binaries"
    mkdir -p "$BINS/linux-arm64"
    local src_r src_h
    if [[ -n $BINS_FROM ]]; then
        if [[ -f $BINS_FROM/linux-arm64/rustic-bko ]]; then src_r=$BINS_FROM/linux-arm64 src_h=$BINS_FROM/linux-arm64/bko-holt
        else src_r=$BINS_FROM src_h=$BINS_FROM/bko-holt; fi
    else
        src_r=$W/bko-bin/linux-arm64 src_h=$(newest_holt)
    fi
    [[ -n $src_h && -f $src_h ]] || bad+=("no native A3+ bko-holt (a4-scratch/holt-bin, a3-scratch/bf1/*/out, holt-bin/linux-arm64; or --bins)")
    for f in restic-bko rustic-bko restic-commit rustic-commit BUILD-INFO; do
        if [[ -f $src_r/$f ]]; then cp -p "$src_r/$f" "$BINS/linux-arm64/$f"
        elif [[ $f == *-bko ]]; then bad+=("no $src_r/$f"); fi
    done
    if [[ -f ${src_h:-} ]]; then
        cp -p "$src_h" "$BINS/linux-arm64/bko-holt"
        if [[ -f ${src_h%/*}/COMMIT ]]; then cp -p "${src_h%/*}/COMMIT" "$BINS/linux-arm64/COMMIT"
        else echo "bko-holt $(sha "$src_h") from $src_h (native A3+ build; info: $(tr -d '\n' <"${src_h%/*}/info.json" 2>/dev/null | head -c 300))" >"$BINS/linux-arm64/COMMIT"; fi
    fi
    chmod 755 "$BINS"/linux-arm64/*-bko "$BINS/linux-arm64/bko-holt" 2>/dev/null
    (cd "$BINS" && find linux-arm64 -type f | LC_ALL=C sort | sed 's|^|./|' | xargs -r sha256sum >SHA256SUMS)
    (cd "$BINS/linux-arm64" && find . -type f ! -name SHA256SUMS | LC_ALL=C sort | xargs -r sha256sum >SHA256SUMS)
    echo "set $BINS (from $src_r and ${src_h:-none}):"; cat "$BINS/SHA256SUMS"
    sed 's|^|bin |' "$BINS/SHA256SUMS" >>"$OUT/inputs.txt"
    for f in restic-bko rustic-bko bko-holt; do
        [[ -x $BINS/linux-arm64/$f ]] || continue
        is_aarch64 "$BINS/linux-arm64/$f" || bad+=("$f isn't an aarch64 ELF")
        case $f in restic-bko) a=$("$BINS/linux-arm64/$f" version 2>&1 | head -n 1) ;; *) a=$("$BINS/linux-arm64/$f" --version 2>&1 | head -n 1) ;; esac
        echo "$f: $a"
    done
    echo "== the tree"
    echo "tree $(head -n 1 "$TREE/COMMIT" 2>/dev/null) at $TREE"
    for f in testvm/stictest1/debian13-arm64/check.sh testvm/holttest1d/ht1.sh testvm/holttest1d/make-rescuer.sh testvm/test2/gate/loop.sh; do
        [[ -f $TREE/$f ]] || bad+=("the tree has no $f")
    done
    grep -q 'img-pin/\$PLAT' "$TREE/testvm/holttest1d/ht1.sh" || bad+=("the tree's ht1.sh has no arm64 support (A6)")
    echo "rescuer: $( [[ -f $RESCUER_IMG ]] && echo "$RESCUER_IMG $(head -c 64 "$RESCUER_IMG.sha256" 2>/dev/null)" || echo "none yet (the rescuer stage makes it)")"
    if ((${#bad[@]})); then printf 'FAIL %s\n' "${bad[@]}"; printf '%s; ' "${bad[@]}" >"$df"; return 1; fi
    echo "preflight ok" >"$df"; return 0
}

body_stic() {
    local g=$1 df=$2 rc lines=$OUT/stic/$1.lines
    mkdir -p "$OUT/stic/$g"
    cd "$W" || return 1
    # (each check prints its PASS, FAIL and LIMIT lines: kept in out/stic/GUEST.lines)
    STC_LOGS=$OUT/stic/$g BKO_TESTVM=$TREE/testvm WEAR_TAG=a6:$STAMP:stic:$g \
        "$TREE/testvm/stictest1/$g/check.sh" "$BINS/linux-arm64" "$STIC_PORT" "$PFX-$g" 2>&1 | tee "$lines"
    rc=${PIPESTATUS[0]}
    echo "$(grep -c '^PASS ' "$lines") PASS, $(grep -c '^FAIL ' "$lines") FAIL, $(grep -c '^LIMIT ' "$lines") LIMIT (check.sh exit $rc)$(grep -m2 '^FAIL ' "$lines" | cut -c1-150 | sed 's/^/; /' | tr -d '\n')" >"$df"
    return $rc
}

body_rescuer() {
    local df=$1 rc
    if [[ -f $RESCUER_IMG ]] && ((!REBUILD_RESCUER)); then
        echo "rescuer-arm64.qcow2 is there: kept ($(head -c 64 "$RESCUER_IMG.sha256" 2>/dev/null)); --rebuild-rescuer makes it again"
        echo "present, kept: $(head -c 12 "$RESCUER_IMG.sha256" 2>/dev/null)" >"$df"; return 0
    fi
    [[ -f $W/vms/ubuntu26-arm64/ubuntu26-arm64.qcow2 ]] || { echo "no ubuntu26-arm64 guest" >"$df"; return 1; }
    cd "$W" || return 1
    WEAR_TAG=a6:$STAMP:rescuer "$W/cmp-run.sh" --reserve 0 5 \
        sudo -n -E env BKO_TESTVM="$TREE/testvm" "$TREE/testvm/holttest1d/make-rescuer.sh" --arch arm64 "$RESCUER_PORT"
    rc=$?
    [[ $rc == 0 ]] && echo "made: $(head -c 12 "$RESCUER_IMG.sha256" 2>/dev/null)" >"$df" ||
        echo "make-rescuer.sh exit $rc (see /bkoscratch/holttest1d/rescuer/rescuer-arm64-build.log)" >"$df"
    return $rc
}

body_holt() {
    local g=$1 v=$2 df=$3 gv d m from ceil res=() rc k
    gv=$g${v:++$v}
    k=/bkoscratch/holttest1d/$HT_RUN/$g${v:+-$v}
    read -r _ d m from < <("$TREE/testvm/holttest1d/ram-size.sh" --host bigfoot "$gv")
    ceil=$("$W/cmp-run.sh" --ceiling 2>/dev/null | cut -d' ' -f1)
    echo "$gv: ram-size $d $m ($from); RAM-disk ceiling ${ceil:-?}"
    local env=(HT_BKO_BIN="$BINS" HT_HOLT_BIN="$BINS" HT_PORT=$HOLT_PORT WEAR_TAG="ht1:$HT_RUN:$g${v:+-$v}")
    if [[ $g == debian13-zfs-arm64 ]] && ! dante_ok; then
        echo "DantePool not known healthy ($DANTE_WHY): vm-mount would import a pool on the host beside it" | tee "$df"; return 3
    fi
    if [[ $v == img ]]; then
        [[ -f $RUN/t2/img-pin/linux-arm64/rescuer.img ]] || { echo "no pin (the imgpin stage)" | tee "$df"; return 1; }
        env+=(HT_VARIANT=img T2_HOST_ROOT="$RUN/t2")
        if [[ -n $ceil ]] && awk -v d="$d" -v c="$ceil" 'BEGIN { exit !(d > c) }'; then
            echo "+img runs only in the RAM disk, and $gv's D $d is over its ceiling $ceil" | tee "$df"; return 3
        fi
        res=(--reserve "$d" "$m"); env+=(HT_RAM=1)
    elif [[ -n $ceil ]] && awk -v d="$d" -v c="$ceil" 'BEGIN { exit !(d > c) }'; then
        echo "D $d is over the RAM disk's ceiling $ceil: on /bkoscratch (HT_RAM=0), as holt_disk_mode does"
        res=(--reserve 0 "$m"); env+=(HT_RAM=0)
    else
        res=(--reserve "$d" "$m"); env+=(HT_RAM=1)
    fi
    cd "$W" || return 1
    env "${env[@]}" "$W/cmp-run.sh" "${res[@]}" sudo -E "$TREE/testvm/holttest1d/ht1.sh" "$g" "$HT_RUN"
    rc=$?
    mkdir -p "$OUT/holt/$gv"
    # The report and the logs, not the dumps (raw dumps stay on Bigfoot: h1/collect-dumps.sh).
    sudo -n tar -C "$k" -cf - --exclude='*.json' report.txt logs 2>/dev/null | tar -C "$OUT/holt/$gv" -xf - 2>/dev/null
    if [[ -f $OUT/holt/$gv/report.txt ]]; then
        { grep -m1 '^RESULT' "$OUT/holt/$gv/report.txt"; grep -c '^PASS ' "$OUT/holt/$gv/report.txt" | sed 's/$/ PASS lines/'
          grep '^FAIL ' "$OUT/holt/$gv/report.txt" | head -n 3; } | tr '\n' ' ' >"$df"
    else
        echo "ht1.sh exit $rc, no report in $k" >"$df"
    fi
    return $rc
}

body_imgpin() {
    local df=$1 p=$RUN/t2/img-pin/linux-arm64 kv n want got bad=()
    [[ -f $IMG ]] || { echo "no image $IMG" | tee "$df"; return 1; }
    mkdir -p "$p"
    rm -f "$p/rescuer.img" "$p"/*-bko "$p/bko-holt" "$p/bins-stale"
    # A hard link (the same file system), as t2-host tool img pin makes one: the image itself is never written.
    ln "$IMG" "$p/rescuer.img" 2>/dev/null || cp --reflink=auto "$IMG" "$p/rescuer.img" || { echo "can't link or copy $IMG" | tee "$df"; return 1; }
    echo "$(sha "$p/rescuer.img")  rescuer.img" >"$p/rescuer.img.sha256"
    for kv in $A4_BINS; do
        n=${kv%%=*} want=${kv#*=}
        got=$(sha "$BINS/linux-arm64/$n" 2>/dev/null)
        if [[ $got == "$want" ]]; then cp -p "$BINS/linux-arm64/$n" "$p/$n"
        else bad+=("$n is ${got:0:12} in $BINS, the image's is ${want:0:12}"); fi
    done
    printf '{"path": "%s", "sha256": "%s", "from": "a6-stages.sh imgpin"}\n' "$IMG" "$(cut -d' ' -f1 "$p/rescuer.img.sha256")" >"$p/source.json"
    cat "$p/rescuer.img.sha256"; ls -l "$p"
    if ((${#bad[@]})); then printf '%s; ' "${bad[@]}" | tee "$df"; touch "$p/bins-stale"; return 1; fi
    echo "pinned $(head -c 12 "$p/rescuer.img.sha256")" >"$df"
}

# GateTest2's gate:bin:static and gate:bin:version on the set, judged as gate.py
# judges them (a6-bin.py judge: an unchecked binary or a SKIP fails): the
# archivers against RUN/expect/expect.tsv (a6-run.sh makes it on the Mac from
# BKO_GIT, for the set's commits), bko-holt against holt/Cargo.toml's version.
body_ht2_bin() {
    local df=$1 rc o=$OUT/hosttest2/bin hv b=$TREE/testvm/test2/gate/bincheck.sh
    mkdir -p "$o"
    hv=$(python3 -I -B -c 'import sys; sys.path.insert(0, sys.argv[1]); from t2lib.gate import cargo_version; print(cargo_version(open(sys.argv[2]).read()) or "")' \
         "$TREE/testvm/test2" "$TREE/holt/Cargo.toml" 2>/dev/null)
    bash "$b" static "$BINS/linux-arm64/restic-bko" "$BINS/linux-arm64/rustic-bko" >"$o/static.out" 2>&1
    bash "$b" version --set "$BINS" --expect "$RUN/expect/expect.tsv" >"$o/version.out" 2>&1
    bash "$b" version ${hv:+--holt-version "$hv"} "$BINS/linux-arm64/bko-holt" >"$o/holt.out" 2>&1
    cat "$o/static.out" "$o/version.out" "$o/holt.out"
    echo "== judged (bko-holt expected ${hv:-unknown: holt/Cargo.toml has no version})"
    python3 -I -B "$TREE/testvm/arm64/a6-bin.py" judge --set "$BINS" --expect "$RUN/expect" \
        "$o/static.out" "$o/version.out" "$o/holt.out" | tee "$o/judged.txt"
    rc=${PIPESTATUS[0]}
    { grep -c '^PASS ' "$o/judged.txt" | sed 's/$/ PASS/'; grep '^FAIL ' "$o/judged.txt" | cut -c1-160 | head -n 3; } | tr '\n' ' ' >"$df"
    return $rc
}

body_ht2_loop() {
    local kind=$1 df=$2 d m o=$OUT/hosttest2/loop-$1 rc lp left
    lp=bko${PFX//[^a-z0-9]/}${kind:0:1}
    dante_ok || { echo "DantePool not known healthy ($DANTE_WHY): no pool is made on the host beside it" | tee "$df"; return 3; }
    # The small class's host_reserve (gate/layers.json): 8.5 4 linux, 6 4 sparse.
    if [[ $kind == linux ]]; then d=8.5 m=4; else d=6 m=4; fi
    cd "$W" || return 1
    rm -rf "$o"
    WEAR_TAG=a6:$STAMP:hosttest2:$kind HOT_C=90 "$W/cmp-run.sh" --reserve "$d" "$m" \
        "$TREE/testvm/test2/gate/loop.sh" "$kind" --bin "$BINS" --out "$o" --prefix "$lp" --old none
    rc=$?
    # The empty mount points fs_roundtrip.py and sparse-rt.sh leave in /mnt (LP-fs/RUN, LP-sp):
    # empty directories of this prefix only, nothing mounted or holding a file.
    sudo -n find /mnt -mindepth 1 -maxdepth 1 -xdev -type d -name "$lp-*" -exec find {} -xdev -depth -type d -empty -delete \; 2>/dev/null
    left=$(ls -1d /mnt/"$lp"-* /var/tmp/"$lp"-* 2>/dev/null | tr '\n' ' ')
    [[ -z $left ]] || echo "NOTE left on the root file system: $left"
    { tail -n 25 "$o/log/summary.txt" 2>/dev/null || tail -n 25 "$o/run-all.out" 2>/dev/null; } | sed 's/^/  /'
    { grep -m1 -E 'PASS|FAIL' "$o/log/summary.txt" 2>/dev/null | head -c 200; echo " rc=$rc"; [[ -z $left ]] || echo "; left: $left"; } | tr '\n' ' ' >"$df"
    [[ -z $left || $rc != 0 ]] || rc=1
    return $rc
}

body_ht2_facts() {
    local df=$1 rc=0 o=$OUT/hosttest2
    mkdir -p "$o" "$RUN/t2"
    T2_HOST_ROOT=$RUN/t2 python3 -B "$TREE/testvm/test2/t2-host" facts >"$o/facts.json" || rc=1
    python3 - "$o/facts.json" <<'PY' || rc=1
import json, sys
f = json.load(open(sys.argv[1]))
print("machine", f.get("machine"), "caps", json.dumps(f.get("caps")), "guests", sorted((f.get("guests") or {}).keys()))
PY
    T2_HOST_ROOT=$RUN/t2 python3 -B "$TREE/testvm/test2/t2-host" self-test || rc=1
    echo "facts $( [[ $rc == 0 ]] && echo ok || echo FAILED ); machine $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("machine"))' "$o/facts.json" 2>/dev/null)" >"$df"
    return $rc
}

body_t2_corpus() {
    local df=$1 raw=$RUN/t2/h1-raw new=$RUN/t2/corpus-new src=$RUN/t2/sources.txt c=$TREE/holt/tests/corpus g n=0 rc=0
    local pass=()
    for g in "${GUESTS[@]}"; do
        grep -qx 'RESULT: PASS' "$OUT/holt/$g/report.txt" 2>/dev/null && pass+=("$g")
    done
    ((${#pass[@]})) || { echo "no 1ARM PASS in this run: nothing to bless" | tee "$df"; return 3; }
    printf '%s\n' "${pass[@]}" >"$src"
    rm -rf "$raw" "$new"; mkdir -p "$raw" "$new"; chmod 700 "$raw" "$RUN/t2"
    "$TREE/testvm/test2/h1/collect-dumps.sh" --out "$raw" --hosts "$(hostname -s)" --sources "$src" || rc=1
    # The row's toolchain homes (hosts.json: rustup_home, cargo_home), never ~/.rustup or ~/.cargo's registry.
    local hp=$TREE/testvm/test2/t2lib/hosts.py
    RUSTUP_HOME=$(python3 -I -B "$hp" get "$(hostname -s)" rustup_home 2>/dev/null) || RUSTUP_HOME=""
    CARGO_HOME=$(python3 -I -B "$hp" get "$(hostname -s)" cargo_home 2>/dev/null) || CARGO_HOME=""
    [[ $RUSTUP_HOME == /* && $CARGO_HOME == /* ]] || { echo "no rustup_home and cargo_home in hosts.json's row" | tee -a "$df"; return 1; }
    export RUSTUP_HOME CARGO_HOME
    export CARGO_TARGET_DIR=$RUN/t2/target TMPDIR=$RUN/t2/tmp; mkdir -p "$TMPDIR"
    local tc=+${HOLT_RUST_TOOLCHAIN:-1.94.1}
    cp -a "$c" "$RUN/t2/corpus-before"
    (cd "$TREE/holt" && BKO_HOLT_CORPUS_RAW=$raw BKO_HOLT_CORPUS_OUT=$new cargo "$tc" test --offline --test corpus_build -- --ignored --nocapture) || rc=1
    for g in "${pass[@]}"; do
        [[ -f $new/$g/src.json ]] || continue
        rm -rf "${c:?}/$g"; cp -a "$new/$g" "$c/$g"; n=$((n + 1))
        grep -v "^$g"$'\t' "$c/index.tsv" >"$c/index.tsv.new"; grep "^$g"$'\t' "$new/index.tsv" >>"$c/index.tsv.new"; mv "$c/index.tsv.new" "$c/index.tsv"
    done
    (cd "$TREE/holt" && BKO_HOLT_GOLDEN=bless cargo "$tc" test --offline --test golden_plans -- --nocapture) || echo "NOTE golden_plans bless: exit $? (its other plans' differences are the tree's, not A6's)"
    "$TREE/testvm/test2/h1/collect-dumps.sh" --scan "$new" --out "$raw" --hosts "$(hostname -s)" || rc=1
    mkdir -p "$OUT/t2"
    { for g in "${pass[@]}"; do diff -ruN "$RUN/t2/corpus-before/$g" "$c/$g" | sed "s|$RUN/t2/corpus-before/|a/holt/tests/corpus/|; s|$c/|b/holt/tests/corpus/|"; done
      diff -u "$RUN/t2/corpus-before/index.tsv" "$c/index.tsv" | sed "s|$RUN/t2/corpus-before/|a/holt/tests/corpus/|; s|$c/|b/holt/tests/corpus/|"
    } >"$OUT/t2/corpus-arm64.patch"
    echo "$n of ${#pass[@]} passing guests' redacted dumps and golden plans in out/t2/corpus-arm64.patch (git apply it in the checkout, after review); leakscan $( [[ $rc == 0 ]] && echo clean || echo 'see the log')" >"$df"
    return $rc
}

body_t2_limits() {
    local df=$1
    mkdir -p "$OUT/t2"
    python3 - "$TREE/testvm/test2/stic/limits.json" "$OUT" "$SUMMARY" <<'PY' >"$df"
import glob, json, os, re, sys
limits, out, summary = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
cand, seen = {}, {}
for d in sorted(glob.glob(os.path.join(out, "stic", "*"))):
    g = os.path.basename(d)
    lines = []
    for f in glob.glob(os.path.join(d, "**", "*"), recursive=True):
        if os.path.basename(f) in ("lines.txt", "check.out", "verdicts.txt") or re.match(r"check-.*\.log$", os.path.basename(f)):
            lines += open(f, errors="replace").read().splitlines()
    for l in lines:
        m = re.match(r"LIMIT (.*?): (.*)", l)
        if m:
            seen.setdefault(g, {})[m[1]] = m[2]
            if m[1] not in (limits.get(g) or {}):
                cand.setdefault(g, {})[m[1]] = m[2]
json.dump({"new": cand, "seen": seen}, open(os.path.join(out, "t2", "limits-candidates.json"), "w"), indent=1)
est = []
for l in open(summary):
    f = l.rstrip("\n").split("\t")
    if len(f) >= 6 and f[1] == "PASS" and (f[0].startswith("stic:") or f[0].startswith("holt:")):
        cell = f[0]; s = int(f[5])
        est.append(f"{cell.split(':')[0]}\t{cell}\t{s / 60:.2f}\t{s}\t0.00\t-\tbigfoot a6 run {os.path.basename(os.path.dirname(out))}, wall of the stage")
open(os.path.join(out, "t2", "estimates-candidates.tsv"), "w").write("\n".join(est) + ("\n" if est else ""))
print(f"{sum(len(v) for v in cand.values())} LIMIT needs not in limits.json ({', '.join(sorted(cand)) or 'none'}); {len(est)} estimate rows")
PY
}

body_final() {
    local df=$1 g f bad=() was now p
    for g in "${GUESTS[@]}"; do
        f=$W/vms/$g/$g.qcow2
        was=$(awk -v g="$g" '$1 == g { print $2 }' "$OUT/inputs.txt" 2>/dev/null)
        now=$( [[ -f $f ]] && sha "$f")
        echo "$g ${now:-missing} (was ${was:-?})"
        [[ -z $was || $now == "$was" ]] || bad+=("$g's image changed")
    done
    was=$(awk '$1 == "image" { print $2 }' "$OUT/inputs.txt" 2>/dev/null)
    if [[ -n $was ]]; then now=$(sha "$IMG"); echo "image $now (was $was)"; [[ $now == "$was" ]] || bad+=("the image changed"); fi
    echo "== left behind? (read only)"
    pgrep -a qemu-system 2>/dev/null | grep -E "$PFX|$HT_RUN|a6-run/$STAMP|rescuer-arm64\.new\.qcow2" && bad+=("a QEMU of this run is still up")
    p=$(rescuer_qemu); [[ -z $p ]] || bad+=("make-rescuer.sh's QEMU $p ($RESCUER_PIDF) is still up")
    zpool list -H -o name 2>/dev/null | grep -E "^bko${PFX//[^a-z0-9]/}" && bad+=("a pool of this run is still imported")
    findmnt -rn -o TARGET 2>/dev/null | grep -E "/mnt/ht1-(src|dst)-.*-arm64|$PFX" && bad+=("a mount of this run is still there")
    if ((${#bad[@]})); then printf '%s; ' "${bad[@]}" | tee "$df"; return 1; fi
    echo "the guests and the image unchanged; nothing of the run left up" >"$df"
}

# --- as a stage's own process: run its body ---------------------------------------
if [[ -n ${A6_BODY:-} ]]; then
    body "$A6_BODY" "$A6_DF"
    exit $?
fi

# --- the plan --------------------------------------------------------------
plan() {
    local s g d m k ceil
    ceil=$("$W/cmp-run.sh" --ceiling 2>/dev/null | cut -d' ' -f1)
    for s in "${STAGES[@]}"; do
        [[ ${WANT[$s]} == 1 ]] || { echo "   (skipped) $s"; continue; }
        echo "-- $s"
        case $s in
            preflight) echo "   read: hostname, /dev/kvm, sudo -n, df, zpool list (DantePool's health), pgrep qemu-system;"
                       echo "   sha256 of ${GUESTS[*]/#/$W/vms/}... and $IMG; assemble $BINS from ${BINS_FROM:-$W/bko-bin/linux-arm64 + $(newest_holt)}" ;;
            stic:*) g=${s#stic:}; printf '   cd %q; STC_LOGS=%q BKO_TESTVM=%q WEAR_TAG=%q %q %q %q %q\n' "$W" "$OUT/stic/$g" "$TREE/testvm" "a6:$STAMP:stic:$g" \
                        "$TREE/testvm/stictest1/$g/check.sh" "$BINS/linux-arm64" "$STIC_PORT" "$PFX-$g" ;;
            rescuer) if [[ -f $RESCUER_IMG ]] && ((!REBUILD_RESCUER)); then echo "   keep $RESCUER_IMG (there already)"
                     else printf '   %q --reserve 0 5 sudo -n -E env BKO_TESTVM=%q %q --arch arm64 %q\n' "$W/cmp-run.sh" "$TREE/testvm" "$TREE/testvm/holttest1d/make-rescuer.sh" "$RESCUER_PORT"; fi ;;
            holt:*) g=${s#holt:}
                    read -r _ d m _ < <("$TREE/testvm/holttest1d/ram-size.sh" --host bigfoot "$g" 2>/dev/null) || d=? m=?
                    local ram=1 over=0
                    [[ -n $ceil && $d != \? ]] && awk -v d="$d" -v c="$ceil" 'BEGIN { exit !(d > c) }' && over=1
                    if ((over)) && [[ $g == *+img ]]; then echo "   SKIP: +img runs only in the RAM disk, and D $d is over its ceiling $ceil"; continue; fi
                    ((over)) && { ram=0; d=0; }
                    printf '   cd %q; HT_BKO_BIN=%q HT_HOLT_BIN=%q HT_PORT=%q HT_RAM=%s%s WEAR_TAG=%q %q --reserve %s %s sudo -E %q %q %q\n' "$W" "$BINS" "$BINS" "$HOLT_PORT" "$ram" \
                        "$( [[ $g == *+img ]] && echo " HT_VARIANT=img T2_HOST_ROOT=$RUN/t2")" "ht1:$HT_RUN:${g/+/-}" "$W/cmp-run.sh" "$d" "$m" \
                        "$TREE/testvm/holttest1d/ht1.sh" "${g%+img}" "$HT_RUN"
                    ((over)) && echo "   (its D is over the RAM disk's ceiling $ceil: on /bkoscratch, as holt_disk_mode does)" ;;
            imgpin) echo "   ln $IMG $RUN/t2/img-pin/linux-arm64/rescuer.img; its sha256; the image's bko-holt, rustic-bko, restic-bko from $BINS/linux-arm64 (each must be the image's: $A4_BINS)" ;;
            hosttest2:bin) echo "   $TREE/testvm/test2/gate/bincheck.sh static $BINS/linux-arm64/{restic,rustic}-bko;"
                           echo "   bincheck.sh version --set $BINS --expect $RUN/expect/expect.tsv; bincheck.sh version --holt-version (holt/Cargo.toml's) $BINS/linux-arm64/bko-holt;"
                           echo "   judged by a6-bin.py judge as gate:bin:static and gate:bin:version (expectations: $( [[ -s $RUN/expect/expect.tsv ]] && awk -F'\t' '{ printf "%s%s %s%s", (NR > 1 ? "; " : ""), $1, $2, ($3 != "" ? " core " $3 : "") }' "$RUN/expect/expect.tsv" || echo 'NONE: the stage would FAIL'))" ;;
            hosttest2:loop-*) k=${s#hosttest2:loop-}
                    printf '   WEAR_TAG=%q %q --reserve %s 4 %q %s --bin %q --out %q --prefix %q --old none\n' "a6:$STAMP:hosttest2:$k" "$W/cmp-run.sh" \
                        "$( [[ $k == linux ]] && echo 8.5 || echo 6)" "$TREE/testvm/test2/gate/loop.sh" "$k" "$BINS" "$OUT/hosttest2/loop-$k" "bko${PFX//[^a-z0-9]/}${k:0:1}"
                    echo "   (run-all.sh's arm64 profile: $( [[ $k == linux ]] && echo "fs_roundtrip.py zfs, tmpfs" || echo "sparse-rt.sh zfs, zfs-nocomp, SPARSE_DISK_WORK in its log, not /var/tmp"); pools on files in /mnt/bko-ram, no loop device;"
                    echo "   then its empty mount points /mnt/bko${PFX//[^a-z0-9]/}${k:0:1}-* go)" ;;
            hosttest2:facts) echo "   T2_HOST_ROOT=$RUN/t2 t2-host facts > $OUT/hosttest2/facts.json; t2-host self-test" ;;
            t2:corpus) echo "   for each 1ARM PASS: collect-dumps.sh --hosts bigfoot --out $RUN/t2/h1-raw (raw, 0700, never fetched);"
                       echo "   cargo +1.94.1 test --offline --test corpus_build -- --ignored; BKO_HOLT_GOLDEN=bless ... --test golden_plans; collect-dumps.sh --scan;"
                       echo "   -> $OUT/t2/corpus-arm64.patch" ;;
            t2:limits) echo "   the checks' LIMIT lines against stic/limits.json -> $OUT/t2/limits-candidates.json; stage times -> estimates-candidates.tsv" ;;
            final) echo "   sha256 of the guests and the image again (must be unchanged); no QEMU, pool or mount of this run left" ;;
        esac
    done
}

if ((DRY)); then
    echo "a6-stages.sh --dry-run: run $RUN (HoltTest1 RUN $HT_RUN, prefix $PFX), tree $TREE"
    echo "   host $(hostname -s) $(uname -m); bins ${BINS_FROM:-default}; image $IMG; +img panel ${IMG_GUESTS[*]}"
    plan
    echo "-- inputs (read only):"
    for g in "${GUESTS[@]}"; do printf '   %-20s %s\n' "$g" "$( [[ -f $W/vms/$g/$g.qcow2 ]] && echo "$(stat -c %s "$W/vms/$g/$g.qcow2") bytes, $(cat "$W/vms/$g/vm-run-options" 2>/dev/null)" || echo MISSING)"; done
    printf '   %-20s %s\n' image "$( [[ -f $IMG ]] && echo "$(stat -c %s "$IMG") bytes" || echo MISSING)"
    printf '   %-20s %s\n' bko-holt "${BINS_FROM:-$(newest_holt)}"
    printf '   %-20s %s\n' archivers "$(ls "${BINS_FROM:-$W/bko-bin/linux-arm64}" 2>&1 | tr '\n' ' ')"
    printf '   %-20s %s\n' rescuer "$( [[ -f $RESCUER_IMG ]] && echo there || echo 'not yet (the rescuer stage makes it)')"
    printf '   %-20s %s\n' DantePool "$(dante_ok; echo "$DANTE_WHY")"
    printf '   %-20s %s\n' "RAM disk" "$(df -h --output=avail /mnt/bko-ram 2>/dev/null | tail -n 1 | tr -d ' ') free; ceiling $("$W/cmp-run.sh" --ceiling 2>/dev/null)"
    printf '   %-20s %s\n' /home "$(df -h --output=avail /home 2>/dev/null | tail -n 1 | tr -d ' ') free"
    printf '   %-20s %s\n' qemu "$(pgrep -c qemu-system 2>/dev/null; true) running"
    exit 0
fi

# --- the run ---------------------------------------------------------------
mkdir -p "$RUN/logs" "$OUT" "$RUN/t2" "$W/a6-run"
exec 9>"$W/a6-run/.lock" || die "can't open $W/a6-run/.lock"
flock -n 9 || die "another a6 run holds $W/a6-run/.lock ($(cat "$W/a6-run/.lock.who" 2>/dev/null))"
echo "$STAMP $$" >"$W/a6-run/.lock.who"
echo $$ >"$RUN/runner.pid"
echo running >"$RUN/state"
printf 'stage\tresult\trc\tstart\tend\tseconds\tdetail\n' >"$SUMMARY"
trap on_term TERM INT HUP
say "a6 run $STAMP: tree $(head -n 1 "$TREE/COMMIT" 2>/dev/null), $(uname -n) $(uname -m), pid $$, $(tail -n 1 /proc/self/cgroup)"
say "stages: $(for s in "${STAGES[@]}"; do [[ ${WANT[$s]} == 1 ]] && echo -n "$s "; done)"
fails=0
for s in "${STAGES[@]}"; do
    [[ ${WANT[$s]} == 1 ]] || continue
    if ((STOPPED)) && [[ $s != final ]]; then row "$s" ABORTED - "$(date +%s)" "$(date +%s)" "not started: the run was stopped"; fails=$((fails + 1)); continue; fi
    [[ $s != final ]] || STOPPED=0   # (final, read only, says what a stop left behind)
    df=$RUN/logs/${s//[:+]/_}.detail
    stage "$s" "$df" -- env A6_BODY="$s" A6_DF="$df" BINS_FROM="$BINS_FROM" bash "$ME" --run "$RUN" ${ONLY:+--only "${ONLY#,}"} ${SKIP:+--skip "${SKIP#,}"} \
        ${BINS_FROM:+--bins "$BINS_FROM"} --img "$IMG" --img-guests "$(IFS=,; echo "${IMG_GUESTS[*]}")" $( ((REBUILD_RESCUER)) && echo --rebuild-rescuer) || fails=$((fails + 1))
    # A failed preflight stops the run: the stages after it would run on inputs it didn't vouch for.
    if [[ $s == preflight ]] && ! grep -q $'^preflight\tPASS' "$SUMMARY"; then
        say "preflight failed: no other stage runs"; STOPPED=2
    fi
done
{
    echo "a6 run $STAMP on $(hostname -s), tree $(head -n 1 "$TREE/COMMIT" 2>/dev/null)"
    echo "finished $(date '+%F %T'); $fails stage(s) not PASS or SKIP"
    echo
    column -t -s $'\t' "$SUMMARY" 2>/dev/null || cat "$SUMMARY"
} >"$RUN/summary.txt"
cat "$RUN/summary.txt" >>"$LOG"
rc=0; ((fails == 0)) || rc=1
echo "$rc" >"$RUN/exit"
echo done >"$RUN/state"
say "exit $rc"
exit $rc
