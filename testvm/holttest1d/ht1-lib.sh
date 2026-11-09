#!/usr/bin/env bash
# HoltTest1's write path, sourced by ht1.sh, ht1-bsd.sh and ht1-offline.sh
# (README: "Where a run works, and what it keeps"):
#   - R is where a run works: HT_WORK/RUN/GV in the RAM disk while the run
#     holds a cmp-run.sh --reserve of at least 1 GiB, else K itself.  TMPDIR
#     is R/tmp, so QEMU's -snapshot files are R's too.
#   - K, HT_RECORDS/RUN/GV, is where it ends up.  A pass that ran verify keeps
#     its records only (report.txt, logs/, dumps/, HT_ZIP compressed); any
#     other run keeps everything.  An earlier K is renamed K.1, K.2, ...
#   - A run holds a lock on RUN/GV for its life.  It refuses before any phase
#     (exit 2, nothing left behind), samples R's peaks (logs/phases.tsv,
#     HT_RECORDS/peaks.tsv) and stops a run in RAM that outgrows or outlives
#     its reservation, records the binaries (logs/manifest.txt), and gives a
#     rerun from a later phase a failure's state back from K.
#
# The driver sets G RUN GV VARIANT PHASES PORT W HERE T, fails=0, say ok bad,
# imgs and HOLT (and PLAT, ht1-bsd.sh) before ht_paths, and HT_BULK (globs in
# R a pass deletes) and HT_ZIP (files in R a pass compresses) before ht_begin.
# It may define ht_release (its own mounts), which ht_end calls before the
# lib releases the rest.  The calls, in order: ht_paths; the driver's own
# refusals; ht_require; ht_seed PATTERN...; ht_begin; ht_rustic_take PLAT; the
# header say; ht_manifest [FILE...]; for each phase ht_phase_start P, phase_P,
# ht_phase_end P; ht_summary; the RESULT say and the exit.  The lib sets R, K,
# RESERVED, HT_MODE (ram or disk), RUSTIC (R's checked rustic-bko) and
# HT_RUSTIC_REC; the driver may call ht_note TEXT (the report's note: line)
# and ht_qemus R (the PIDs of the run's own QEMU: those with a file in R
# that the run started, ht_own).  A VM the driver starts after ht_begin gets
# {HT_FD1}>&- {HT_FD2}>&- (ht_begin's copies of the driver's stdout and
# stderr) if it can outlive the run.
#
# A run's own processes are the ones it started (ht_own): their environment
# holds the TMPDIR=R/tmp that ht_begin exports, which vm-run, vm-mount, and
# the QEMU and qemu-nbd they start keep; a QEMU also when it forwards the
# run's port.  On /bkoscratch (R is K) the run signals, unmounts and
# detaches only those (and the driver's own mounts, its ht_release): what
# someone else started on K's files is left alone.  An earlier attempt's VM
# still up on K's files has K's TMPDIR too: a run from source refuses until
# it is stopped (ht_require), a rerun in K takes it over.  In RAM, R is the
# run's alone, so whatever still holds it at the end goes too, but for the
# qemu-nbd of a vm-mount someone else made of R's files: that vm-mount
# stays up, and the run says R is still held (ht_free).
#
# Env: HT_RAM (unset: RAM when reserved, else /bkoscratch with a note; 1: RAM
# or refuse; 0: /bkoscratch), HT_CLEAN (0: leave R as it is), HT_RESERVE_PID
# (the ledger PID of a reservation that outlives the run, read instead of the
# ancestors'), HT_KEEP, HT_RUSTIC, HT_ROUND, HT_BKO_BIN, HT_FAIL_AT (a test
# hook: a FAIL after that phase); tests only: HT_WORK, HT_RECORDS,
# HT_LIMIT_KIB (the use past which a run in RAM is stopped) and HT_FREE_KIB
# (the RAM disk's free KiB, as the sampler sees it), the last two refused
# with the default HT_WORK.
# Exit codes: 0 PASS; 1 FAIL, ended early, or stopped over its reservation;
# 2 refused before any phase, R removed; 129, 130, 143 ended by HUP, INT, TERM.
#
# Test2's ledger (README "Where a run works, and what it keeps"): ht_end
# runs this copy's testvm/test2/t2-host (T/test2/t2-host) for the run's row
# once K is in place, and for a refusal after ht_begin: best effort, at most
# 30 s, nothing written into R or K, at most one note: line on the driver's
# stderr; the exit code, what is kept and the move are the same with it or
# without it.  T2_HOST_ROOT, T2_CELLS and T2_RUN reach it from the
# environment (HoltTest2's queue sets them; without them the row is a
# HoltTest1 run's, rule test1).  HT_CLEAN=0 and RUN debug get no row.
#
# R/.ht is the lib's: pid (the driver), k (K), seeding (a seed not finished),
# moved (R is in K), held (left on purpose: the reservation PID that holds it),
# summed, overrun and lowfree (ht_summary's and the sampler's; a dead run's go
# when a rerun takes R).
#
#   ht1-lib.sh --sweep [HT_WORK]   (root; ht1-queue.sh runs it) moves each run
#       in HT_WORK that the lib made and that died (its lock free, its .ht/pid
#       dead) to K, records only, and deletes it from the RAM disk.  Anything
#       without R/.ht, ht1-debug.sh's debug/ and debug-state/ stay.  HT_WORK
#       and HT_RECORDS go as a pair: the defaults, or /mnt/bko-ram/NAME and
#       /bkoscratch/NAME.  A run from source in RAM sweeps such an R of its
#       own RUN/GV first (ht_require).

HT_WORK_DEFAULT=/mnt/bko-ram/holttest1d
HT_WORK=${HT_WORK:-$HT_WORK_DEFAULT}              # where a run in RAM works (tests override)
HT_RECORDS=${HT_RECORDS:-/bkoscratch/holttest1d}  # where every run ends up (tests override)
HT_LEDGER=/mnt/bko-ram/.reserve                   # cmp-run.sh's ledger: PID DISK MEM SCOPE WHAT... (its "Ledger rows")
HT_LOCKS=/run/lock/bko-ht1                        # run locks: on neither disk, gone at a reboot
HT_LIB=$(realpath "${BASH_SOURCE[0]}")
HT_NAME='^[A-Za-z0-9][A-Za-z0-9._-]*$'            # RUN and GV: plain names (R and K are deleted and moved)
HT_STARTED=0 HT_MADE_R=0 HT_SUMMED=0 HT_SAMPLER="" HT_LOCKER="" HT_SIG="" HT_NOTE="" HT_SEEDED="" HT_ROTATED="" HT_SEED_NEW=0
HT_FD1="" HT_FD2=""
HT_PEAK=0 HT_PEAK_ANON=- HT_CG="" HT_T0=0 HT_P0=0 HT_PLAT=linux HT_RESERVE_ROW=""
HT_RUSTIC_REC="" HT_RUSTIC_SRC="" HT_RUSTIC_SHA="" HT_MODE="" RESERVED=0
HT_BULK=() HT_ZIP=()

ht_die()  { echo "${0##*/}: $*" >&2; exit 2; }
ht_note() { HT_NOTE+="${HT_NOTE:+; }$*"; }
ht_gib()  { awk -v k="$1" 'BEGIN { printf "%.1f", k / 1048576 }'; }   # KiB -> GiB
ht_ge()   { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a + 0 >= b + 0) }'; }   # A >= B (GiB figures: 2.5 too)
ht_lockfile() { echo "$HT_LOCKS/$(tr / % <<<"$1").lock"; }   # K -> its run lock
ht_kept() { local m=""; read -r m 2>/dev/null <"$1/logs/kept.txt"; echo "${m%%:*}"; }   # DIR -> pass, fail, ...
# R is this driver's, for K: a dead run's markers of its attempt go (seeding
# is ht_seed's, and held stays: the sweep and the queue read its PID).
ht_mark() { mkdir -p "$R/.ht" && rm -f "$R/.ht/summed" "$R/.ht/overrun" "$R/.ht/lowfree" "$R/.ht/moved" && echo $$ >"$R/.ht/pid" && echo "$K" >"$R/.ht/k"; }

# The cmp-run.sh --reserve row the run holds, as "PID DISK": HT_RESERVE_PID's,
# else the nearest ancestor's.  cmp-run.sh's reserving PID execs sudo (the
# exec that ends its --reserve), so the driver runs under it; sudo's use_pty
# monitor is one more step on the way.  A row counts only while its PID lives
# (rows outlive their holders until cmp-run.sh prunes them).  Read, never
# locked.
ht_reserve_row() {
    local p=${HT_RESERVE_PID:-$$} d i
    for ((i = 0; i < 64; i++)); do
        [[ $p =~ ^[0-9]+$ ]] && ((p > 1)) || return 0
        d=$(awk -v p="$p" '$1 == p { print $2; exit }' "$HT_LEDGER" 2>/dev/null)
        if [[ -n $d && -d /proc/$p ]]; then echo "$p $d"; return 0; fi
        [[ -z ${HT_RESERVE_PID:-} ]] || return 0
        p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
    done
}
ht_reserved_gib() {  # the DISK GiB of that row, else 0
    local r; r=$(ht_reserve_row)
    if [[ -n $r ]]; then echo "${r#* }"; else echo 0; fi
}

# R, K, RESERVED and the mode; nothing is made yet.  Refused here too: a PORT
# that isn't one (it goes into pgrep patterns), and ht1-debug.sh's RUNs:
# debug, a run it keeps in the RAM disk under its own reservation, and
# debug-state, its build and reservation.
ht_paths() {
    local p row
    # (Today an unknown phase passes: its "command not found" counts no FAIL.)
    for p in "${PHASES[@]}"; do
        case $p in source|backup|rebuild|verify) ;; *) ht_die "no phase $p (source backup rebuild verify)" ;; esac
    done
    for p in "$RUN" "$GV"; do
        [[ $p =~ $HT_NAME ]] || ht_die "'$p': RUN and the guest's name must be plain names ([A-Za-z0-9._-])"
    done
    [[ ${PORT:-} =~ ^[1-9][0-9]{3,4}$ ]] && ((PORT >= 1024 && PORT <= 65535)) || ht_die "HT_PORT=${PORT:-}: a port, 1024-65535"
    case $RUN in
        debug-state) ht_die "RUN debug-state is ht1-debug.sh's own state: take another RUN" ;;
        debug) [[ ${HT_CLEAN:-1} == 0 && -n ${HT_RESERVE_PID:-} ]] ||
            ht_die "RUN debug is ht1-debug.sh's (with HT_CLEAN=0 and HT_RESERVE_PID): take another RUN" ;;
    esac
    for p in HT_LIMIT_KIB HT_FREE_KIB; do
        [[ -z ${!p:-} ]] || [[ ${!p} =~ ^[0-9]+$ && $HT_WORK != "$HT_WORK_DEFAULT" ]] ||
            ht_die "$p=${!p}: a number of KiB, for tests (with their own HT_WORK) only"
    done
    K=$HT_RECORDS/$RUN/$GV
    row=$(ht_reserve_row); HT_RESERVE_ROW=${row%% *} RESERVED=${row#* }; RESERVED=${RESERVED:-0}
    case ${HT_RAM:-} in
        1) HT_MODE=ram ;;
        0) HT_MODE=disk ;;
        "") if mountpoint -q /mnt/bko-ram && ht_ge "$RESERVED" 1; then
                HT_MODE=ram
            else
                HT_MODE=disk
                ht_note "no reservation of the RAM disk (cmp-run.sh --reserve D M): working on /bkoscratch, as HT_RAM=0"
            fi ;;
        *) ht_die "HT_RAM=$HT_RAM: 1 (in RAM), 0 (on /bkoscratch) or unset (in RAM when reserved)" ;;
    esac
    if [[ $HT_MODE == ram ]]; then R=$HT_WORK/$RUN/$GV; else R=$K; fi
}

# The run lock on RUN/GV (K's), held by a watcher of the driver: the driver's
# children (QEMU, setsid'd helpers) don't inherit it, so it frees itself when
# the driver dies, however it dies.  ht_end frees it last.
ht_lock() {
    local lk f; f=$(ht_lockfile "$K")
    mkdir -p "$HT_LOCKS" && exec {lk}>>"$f" || ht_die "can't open the run lock $f"
    if ! flock -n "$lk"; then exec {lk}>&-; ht_die "$RUN/$GV is in use (another run holds $f)"; fi
    ht_lockwatch "$lk" </dev/null >/dev/null 2>&1 &
    HT_LOCKER=$!
    exec {lk}>&-
    trap 'ht_unlock' EXIT
}
ht_lockwatch() {  # FD: holds it while the driver lives; only ht_unlock's KILL ends it sooner
    local fd=$1
    trap '' TERM INT HUP
    cd / || :   # (holds nothing of R's or K's: ht_free leaves it be)
    while kill -0 $$ 2>/dev/null; do sleep 1 {fd}>&-; done
}
ht_unlock() { [[ -z $HT_LOCKER ]] || { kill -9 "$HT_LOCKER" 2>/dev/null; wait "$HT_LOCKER" 2>/dev/null; HT_LOCKER=""; }; }

ht_in_use() {  # DIR: the PID in DIR/.ht/pid, when it is a live HoltTest driver (PIDs get reused)
    local p=""
    read -r p 2>/dev/null <"$1/.ht/pid"
    [[ $p =~ ^[0-9]+$ && $p != "$$" ]] && tr '\0' ' ' 2>/dev/null <"/proc/$p/cmdline" | grep -q ht1 && echo "$p"
}

# The ownership test: PID is the run's own when it was started under the
# run, so that its environment holds exactly the TMPDIR=R/tmp that ht_begin
# exports (one entry of /proc/PID/environ, NUL-separated: root's to read).
# vm-run and vm-mount keep a TMPDIR that is set, and the QEMU and qemu-nbd
# they start inherit it; one started by hand on R's files has another.  A
# QEMU (its command line starts qemu-system) also counts when it forwards
# one of the PORTs.
ht_own() {  # PID R [PORT...]
    local c p
    grep -qzxF -- "TMPDIR=$2/tmp" "/proc/$1/environ" 2>/dev/null && return 0
    (($# > 2)) && c=$(tr '\0' ' ' 2>/dev/null <"/proc/$1/cmdline") && [[ $c == qemu-system* ]] || return 1
    for p in "${@:3}"; do [[ $c == *":$p-:22"* ]] && return 0; done
    return 1
}
# The run's ports, for ht_own: HT_PORT, and with it HT_PORT+1 for
# ht1-debug.sh's run (RUN debug), its build guest's.  (That guest's disks
# and serial log aren't in R, so ht_qemus never takes it today.)  None in
# the sweep, which has no PORT.
ht_ports() {
    [[ ${PORT:-} =~ ^[0-9]+$ ]] || return 0
    echo "$PORT"
    [[ ${RUN:-} != debug ]] || echo $((PORT + 1))
}
ht_qemus() {  # R: the PIDs of the run's own QEMU (ht_own) with a file in R (its disks, serial log or socket)
    local q
    for q in $(pgrep -f "^qemu-system.*$(sed 's/[][\.*^$]/\\&/g' <<<"$1")/"); do
        # shellcheck disable=SC2046  # the ports, one word each
        ht_own "$q" "$1" $(ht_ports) && echo "$q"
    done
    return 0
}

# The refusals, before anything is made: the run lock, the port, an earlier
# attempt's VM (on /bkoscratch), the RAM disk.
ht_require() {
    local p q ours d k gv=$G${VARIANT:++$VARIANT}
    ht_lock
    p=$(ht_in_use "$R"); [[ -z $p ]] || ht_die "$R is in use (pid $p)"
    if [[ $R != "$K" ]]; then p=$(ht_in_use "$K"); [[ -z $p ]] || ht_die "$K is in use (pid $p)"; fi
    # Another's VM on the port would answer this run's ssh, and so would a
    # dead run's.  Only a rescuer that an earlier run left up on purpose
    # (HT_KEEP, HT_CLEAN=0: R/.ht/held, no .ht/pid) is this run's, for a
    # rebuild or verify, which take it down first.  (ht_paths took only a port.)
    ours=""
    [[ -e $R/.ht/held && ! -e $R/.ht/pid ]] && [[ ${PHASES[0]} == rebuild || ${PHASES[0]} == verify ]] &&
        ours=$(ht_qemus "$R")
    for q in $(pgrep -f "^qemu-system.*:$PORT-:22"); do
        grep -qx "$q" <<<"$ours" && continue
        ht_die "port $PORT in use (QEMU pid $q, not this run's)"
    done
    if [[ -z $(pgrep -f "^qemu-system.*:$PORT-:22") && -n $(ss -Hltn "sport = :$PORT" 2>/dev/null) ]]; then
        ht_die "port $PORT in use (a listener: ss -ltnp 'sport = :$PORT')"
    fi
    # On /bkoscratch every attempt in K has the TMPDIR K/tmp, so a VM that an
    # earlier attempt left up on K's files (HT_KEEP's rescuer, a dead
    # driver's) passes for the run's own (ht_own), even once a run from
    # source has renamed that attempt K.N: such a run refuses until it is
    # stopped.  (A rerun from a later phase goes on in K, with what the
    # attempt before left there: a rebuild stops a rescuer kept up for it.)
    if [[ $HT_MODE == disk && ${PHASES[0]} == source ]]; then
        q=$(ht_qemus "$K" | tr '\n' ' '); q=${q% }
        [[ -z $q ]] || ht_die "an earlier attempt's VM is up on $K's files (QEMU pid $q$([[ -e $K/.ht/held ]] && echo ", kept up: $K/.ht/held")): stop it first, or this run from source would take it for its own"
    fi
    [[ $HT_MODE == ram ]] || return 0
    mountpoint -q /mnt/bko-ram || ht_die "no RAM disk at /mnt/bko-ram (HT_RAM=0 works on /bkoscratch)"
    ht_ge "$RESERVED" 1 ||
        ht_die "no reservation of the RAM disk: run under cmp-run.sh --reserve D M (D: ram-size.sh $gv), or with HT_RAM=0"
    if [[ -z ${HT_RESERVE_PID:-} ]] && [[ ${HT_CLEAN:-1} == 0 || ${HT_KEEP:-} == 1 ]]; then
        ht_die "HT_CLEAN=0 and HT_KEEP=1 would leave the run in the RAM disk after its reservation: only with HT_RESERVE_PID (ht1-debug.sh's)"
    fi
    if [[ -x $HERE/ram-size.sh ]]; then
        d=$("$HERE/ram-size.sh" "$gv" 2>/dev/null | awk 'NF >= 3 { print $2; exit }')
        [[ $d =~ ^[0-9.]+$ ]] && ! ht_ge "$RESERVED" "$d" && ht_note "reserved $RESERVED GiB of the RAM disk; ram-size.sh says $d"
    fi
    # A dead run's R (SIGKILL, OOM; not swept yet), for a run from source: swept
    # first, as --sweep would, so its records reach K and this run starts anew.
    # (A later phase goes on with it, as with a failure's state: ht_seed.)
    if [[ ${PHASES[0]} == source && $RUN != debug ]] && ht_sweepable "$R"; then
        k=""; read -r k 2>/dev/null <"$R/.ht/k"
        [[ -z $k || $k == "$K" ]] || ht_die "$R is a dead run's, for $k: sudo $HT_LIB --sweep, then run again"
        ht_sweep_one "$R" "$K" "$GV"
        ht_note "a run that died had left $R: swept first (its records to K)"
    fi
    return 0
}

# A rerun that starts after source goes on with R's state, else with a
# failure's that ht_end moved to K: copied back into R (only read from the
# disk), less the PATTERNs, which its first phase remakes.  A record that kept
# its records only (pass, overrun, swept, lost) has no disks or repository to
# go on with.  R/.ht/seeding marks a seed not finished: R is the seed's alone,
# and it goes on (rsync resumes) or goes.
ht_seed() {  # PATTERN...
    local p m x
    [[ ${PHASES[0]} == source ]] && return 0
    if [[ ! -e $R/.ht/seeding ]] && [[ -e $R/dumps/src.json || $R == "$K" ]]; then
        m=$(ht_kept "$R")
        [[ -z $m || $m == fail ]] || ht_die "$R holds records only (logs/kept.txt: $m), no disks or repository: run from source"
        return 0
    fi
    [[ -e $K/dumps/src.json ]] || return 0
    m=$(ht_kept "$K")
    [[ -z $m || $m == fail ]] || ht_die "$K holds records only (logs/kept.txt: $m), no disks or repository: run from source"
    # (Each attempt has its own report.txt, phases.tsv, kept.txt and manifest.txt; K.N keeps the earlier one's.)
    x=(--exclude=/.ht/ --exclude=/report.txt --exclude=/logs/phases.tsv --exclude=/logs/kept.txt --exclude=/logs/manifest.txt)
    for p; do x+=("--exclude=/$p"); done
    HT_SEED_NEW=0; [[ -d $R && ! -e $R/.ht/seeding ]] || HT_SEED_NEW=1
    ht_mark && : >"$R/.ht/seeding" || ht_die "can't make $R"
    # Cut short (a refusal, ^C, TERM): a new R goes; one that was there keeps
    # .ht/seeding.  (A second signal can't cut the undo short.)
    trap 'ht_seed_undo; ht_unlock' EXIT
    trap 'trap "" TERM INT HUP; exit 143' TERM; trap 'trap "" TERM INT HUP; exit 130' INT; trap 'trap "" TERM INT HUP; exit 129' HUP
    rsync -a --sparse "${x[@]}" "$K/" "$R/" || ht_die "couldn't copy $K's state into $R"
    ht_repoint "$K" "$R"
    rm -f "$R/.ht/seeding"
    trap 'ht_unlock' EXIT; trap - TERM INT HUP
    # The attempt it came from, by the name it has once this run moves in (ht_end's ht_rotate).
    HT_SEEDED=$(ht_next "$K")
    ((HT_SEED_NEW)) && HT_MADE_R=1
    return 0
}
ht_seed_undo() { ((HT_SEED_NEW == 0)) || { rm -rf --one-file-system "$R"; rmdir "${R%/*}" 2>/dev/null; }; }

# A file of another attempt's too (--link-dest's hard links) gets its own
# copy before anything rewrites it in place.
ht_unshare() {  # FILE...
    local f
    for f; do
        [[ -f $f && ! -L $f && $(stat -c %h "$f") -gt 1 ]] || continue
        cp -a --sparse=always "$f" "$f.ht-unshare" && mv -f "$f.ht-unshare" "$f"
    done
}

# After a copy FROM -> TO (in DIR, else TO): the overlays and symlinks that
# name FROM/ name TO/ (ht1-bsd.sh's rescuer-try.qcow2, ht1-offline.sh's
# boot-*.qcow2 and check-*/sculpt-check.qcow2).  Paths here have no blanks.
ht_repoint() {  # FROM TO [DIR]
    local from=$1 to=$2 dir=${3:-$2} f b fmt t
    for f in "$dir"/*.qcow2; do
        [[ -f $f && ! -L $f ]] || continue
        read -r b fmt < <(qemu-img info -U --output=json "$f" 2>/dev/null | python3 -c '
import json, sys
d = json.load(sys.stdin)
print(d.get("backing-filename", "-"), d.get("backing-filename-format", "qcow2"))' 2>/dev/null)
        [[ ${b:-} == "$from/"* ]] || continue
        ht_unshare "$f"
        qemu-img rebase -u -F "$fmt" -b "$to/${b#"$from/"}" "$f"
    done
    while read -r f; do
        t=$(readlink "$f")
        [[ $t == "$from/"* ]] && ln -sfn "$to/${t#"$from/"}" "$f"
    done < <(find "$dir" -mindepth 1 -maxdepth 2 -type l 2>/dev/null)
    return 0
}

ht_next() {  # K: K.N, the next number after the highest
    local k=$1 f n=1
    for f in "$k".*; do [[ ${f##*.} =~ ^[0-9]+$ ]] && ((10#${f##*.} >= n)) && n=$((10#${f##*.} + 1)); done
    echo "$k.$n"
}
ht_rotate() {  # K: an existing K becomes K.N (ht_next), its references with it; prints K.N
    local k=$1 n
    [[ -e $k ]] || return 0
    n=$(ht_next "$k")
    mv -T "$k" "$n" || return 1
    ht_repoint "$k" "$n" >/dev/null 2>&1
    echo "$n"
}
# K.part becomes K, an existing K becoming K.N first; if K can't move, the
# new attempt is the next K.N instead.  Prints "WHERE [K.N]".
ht_place() {  # K
    local k=$1 d=$1 old=""
    if [[ -e $k ]]; then
        old=$(ht_rotate "$k")
        if [[ -z $old || -e $k ]]; then
            old="" d=$(ht_next "$k")
            ht_repoint "$k" "$d" "$k.part" >/dev/null 2>&1
            echo "${0##*/}: couldn't rename $k to make room: this attempt is $d" >&2
        fi
    fi
    mv -T "$k.part" "$d" && echo "$d${old:+ $old}"
}

ht_begin() {
    local f
    trap 'ht_end $?' EXIT
    # (Each ignores the next signal at once: a second can't end the shell
    # inside ht_end, before its own trap '' does that.)
    trap 'HT_SIG=TERM; trap "" TERM INT HUP; exit 143' TERM
    trap 'HT_SIG=INT; trap "" TERM INT HUP; exit 130' INT
    trap 'HT_SIG=HUP; trap "" TERM INT HUP; exit 129' HUP
    HT_T0=$(date +%s)
    # On /bkoscratch, a run from source starts a new K too (K is always the
    # latest attempt; a refusal puts the old one back).
    if [[ $R == "$K" && ${PHASES[0]} == source && -e $K ]]; then
        HT_ROTATED=$(ht_rotate "$K"); [[ -n $HT_ROTATED ]] || ht_die "couldn't rename $K for the new run"
    fi
    # A rerun from a later phase works in K itself: what it shares with K.N
    # (a seeded run's move) gets its own copy first, so K.N stays as it was.
    if [[ $R == "$K" && -d $K ]]; then
        while IFS= read -r -d '' f; do ht_unshare "$f"; done < <(find "$K" -xdev -type f -links +1 -print0 2>/dev/null)
    fi
    [[ -d $R ]] || HT_MADE_R=1
    ht_mark && mkdir -p "$R/logs" "$R/dumps" "$R/tmp" || ht_die "can't make $R"
    # QEMU's -snapshot file, vm-run's mktemp, vm-mount's COW files: in R,
    # counted with it, gone with it.
    export TMPDIR=$R/tmp RT_RAM=0
    HT_CG=/sys/fs/cgroup$(sed -n 's/^0:://p' /proc/self/cgroup)
    # A reservation's scope (cmp-run.sh --reserve's systemd-run --scope), not
    # a login session's (ht1-debug.sh's).
    [[ $HT_CG == */system.slice/*.scope ]] || HT_CG=""
    [[ -z $HT_CG ]] || HT_PEAK_ANON=0
    ht_sampler </dev/null >/dev/null 2>&1 &
    HT_SAMPLER=$!
    # The driver's stdout and stderr, for ht_end: a signal can end the run
    # inside a function call whose stdio is in R (sshk ... >"$R/logs/X"),
    # which the EXIT trap would inherit.  (After the sampler: it has none.)
    exec {HT_FD1}>&1 || exec {HT_FD1}>/dev/null
    exec {HT_FD2}>&2 || exec {HT_FD2}>/dev/null
}

# "DISK_KIB ANON_KIB": R's blocks, plus what any QEMU holds open in R
# unlinked (its -snapshot overlay; stictest1/debian13-stack/check.sh:33-44);
# the reservation's scope's anon, or - outside one.
ht_measure() {
    local k q f a=-
    k=$(du -sxk "$R" 2>/dev/null | cut -f1)
    k=${k:-0}
    for q in $(pgrep '^qemu-system'); do
        for f in /proc/"$q"/fd/*; do
            [[ $(readlink "$f" 2>/dev/null) == "$R/"*" (deleted)" ]] && k=$((k + $(stat -L -c %b "$f" 2>/dev/null || echo 0) / 2))
        done
    done
    [[ -z $HT_CG ]] || a=$(awk '$1 == "anon" { printf "%d", $2 / 1024 }' "$HT_CG/memory.stat" 2>/dev/null)
    echo "$k ${a:--}"
}
ht_max() {  # FILE KIB: FILE keeps the larger, written whole
    local m=0
    read -r m 2>/dev/null <"$1"; [[ $m =~ ^[0-9]+$ ]] || m=0
    [[ $2 =~ ^[0-9]+$ ]] && (($2 > m)) && echo "$2" >"$1.$BASHPID" && mv -f "$1.$BASHPID" "$1"
    return 0
}
ht_limit_kib() {  # RESERVED: the use past which a run in RAM is stopped, RESERVED + max(1 GiB, 10 %)
    if [[ -n ${HT_LIMIT_KIB:-} ]]; then echo "$HT_LIMIT_KIB"; return; fi
    awk -v r="$1" 'BEGIN { m = r * 0.1; if (m < 1) m = 1; printf "%d", (r + m) * 1048576 }'
}
# Every 5 s while the driver lives: the running maxima in R/.ht/peak and
# peak-anon.  In RAM, it also stops a run that outgrows its reservation
# (past ht_limit_kib's limit; or past the reservation itself while the RAM
# disk has under 1 GiB free), or outlives it (no live ledger row:
# cmp-run.sh hands its room out again): the reason in R/.ht/overrun, the
# run's own QEMU killed (ht_qemus: so a foreground ssh returns), then TERM
# to the driver and what it runs (rustic's backup, qemu-img convert), so
# the driver's trap runs now.  Not to a vm-mount or vm-umount at work, which
# finishes first (ht_desc), nor to what else holds R: vm-mount's qemu-nbd
# serves a mounted disk, and ht_end's ht_free takes vm-mount's states down
# before it kills R's other holders.  A run within its reservation goes on
# when the RAM disk is that full (others' room: cmp-run.sh admits rows up to
# its size less 1 GiB, and runs may pass theirs by the limit's margin), with
# R/.ht/lowfree for ht_summary's note: line; should the disk fill, its
# ENOSPC is an ordinary FAIL.  HT_FREE_KIB (tests) stands in for df's free.
ht_sampler() {
    local k a s="" lim rk free why miss=0 me=$BASHPID
    trap '[[ -z ${s:-} ]] || kill "$s" 2>/dev/null; exit 0' TERM
    cd / || :
    lim=$(ht_limit_kib "$RESERVED")
    rk=$(awk -v r="$RESERVED" 'BEGIN { printf "%d", r * 1048576 }')   # the reservation itself, KiB (RESERVED may be 2.5)
    while kill -0 $$ 2>/dev/null; do
        sleep 5 & s=$!
        wait "$s"; s=""
        read -r k a < <(ht_measure)
        ht_max "$R/.ht/peak" "$k"
        [[ $a == - ]] || ht_max "$R/.ht/peak-anon" "$a"
        [[ $HT_MODE == ram && ! -e $R/.ht/summed ]] || continue
        free=${HT_FREE_KIB:-$(df -Pk /mnt/bko-ram 2>/dev/null | awk 'NR == 2 { print $4 }')} why=""
        # (Gone twice in a row: cmp-run.sh rewrites the ledger in place.)
        if [[ -n $(ht_reserve_row) ]]; then miss=0; else miss=$((miss + 1)); fi
        if ((k > lim)); then
            why="over its reservation: $(ht_gib "$k") GiB, past the limit of $(ht_gib "$lim") GiB ($RESERVED GiB reserved)"
        elif [[ $free =~ ^[0-9]+$ ]] && ((free < 1048576 && k > rk)); then
            why="the RAM disk has under 1 GiB free ($(ht_gib "$free") GiB), the run over its reservation at $(awk -v k="$k" 'BEGIN { printf "%.2f", k / 1048576 }') GiB of $RESERVED GiB reserved"
        elif ((miss >= 2)); then
            why="its reservation ended (no live ledger row, pid ${HT_RESERVE_ROW:-?})"
        fi
        if [[ -z $why ]]; then
            [[ $free =~ ^[0-9]+$ ]] && ((free < 1048576)) && [[ ! -e $R/.ht/lowfree ]] && echo "$free $k" >"$R/.ht/lowfree"
            continue
        fi
        echo "$why" >"$R/.ht/overrun"
        # shellcheck disable=SC2046  # PIDs
        kill $(ht_qemus "$R") 2>/dev/null
        kill -TERM $$
        # shellcheck disable=SC2046  # PIDs
        kill $(ht_desc $$ "$me" "$HT_LOCKER") 2>/dev/null
        exit 0
    done
}
# PID's descendants, less the SKIPs, a vm-mount or vm-umount at work, and
# theirs.  vm-mount records each step in its state only once it is done, and
# vm-umount removes the state only at its end: one cut short (or its zpool,
# cryptsetup or qemu-nbd) leaves the host's devices half set up, or a state
# no retry can take down.
ht_desc() {  # PID [SKIP...]
    local c s
    for c in $(pgrep -P "$1"); do
        for s in "${@:2}"; do [[ $c == "$s" ]] && continue 2; done
        ht_mounting "$c" && continue
        echo "$c"; ht_desc "$c" "${@:2}"
    done
}
ht_mounting() { tr '\0' ' ' 2>/dev/null <"/proc/$1/cmdline" | grep -qE '(^|/)vm-u?mount '; }   # PID: a vm-mount or vm-umount

ht_phase_start() {  # P
    HT_STARTED=1 HT_P0=$(date +%s)
    echo 0 >"$R/.ht/peak"; echo 0 >"$R/.ht/peak-anon"
}

ht_phase_end() {  # P: its row in logs/phases.tsv
    local p=$1 k a m=0 ma=0 t=$R/logs/phases.tsv
    [[ ${HT_FAIL_AT:-} == "$p" ]] && bad "HT_FAIL_AT=$p: a test hook (no check failed)"
    read -r k a < <(ht_measure)
    ht_max "$R/.ht/peak" "$k"
    [[ $a == - ]] || ht_max "$R/.ht/peak-anon" "$a"
    read -r m 2>/dev/null <"$R/.ht/peak"; [[ $m =~ ^[0-9]+$ ]] || m=0
    read -r ma 2>/dev/null <"$R/.ht/peak-anon"; [[ $ma =~ ^[0-9]+$ ]] || ma=0
    ((m > HT_PEAK)) && HT_PEAK=$m
    if [[ -n $HT_CG ]]; then ((ma > HT_PEAK_ANON)) && HT_PEAK_ANON=$ma; else ma=-; fi
    [[ -s $t ]] || printf 'phase\tstart_utc\tseconds\tpeak_disk_kib\tpeak_anon_kib\treserved_gib\tmode\tfails\n' >"$t"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$p" "$(date -u -d "@$HT_P0" +%FT%TZ)" "$(($(date +%s) - HT_P0))" \
        "$m" "$ma" "$RESERVED" "$HT_MODE" "$fails" >>"$t"
}

# The run's peaks, before its RESULT line.  Use past the reservation stops a
# run in RAM (the sampler), but the line itself is no check.
ht_summary() {
    local over="" mem
    : >"$R/.ht/summed"   # (the sampler stops nothing after this)
    [[ ! -e $R/.ht/lowfree ]] || ht_note "the RAM disk had under 1 GiB free; this run was within its reservation"
    [[ -z $HT_NOTE ]] || { say "note: $HT_NOTE"; HT_NOTE=""; }   # any added after the manifest's
    if [[ $HT_PEAK_ANON == - ]]; then mem="memory: not measured (no reservation's scope)"
    else mem="memory: peak $(ht_gib "$HT_PEAK_ANON") GiB anon"; fi
    if [[ $HT_MODE == ram ]]; then
        awk -v p="$HT_PEAK" -v r="$RESERVED" 'BEGIN { exit !(p / 1048576 > r + 0) }' &&
            over=": over the reservation, raise ram-sizes.tsv"
        say "RAM disk: peak $(ht_gib "$HT_PEAK") GiB, reserved $RESERVED GiB$over; $mem"
    else
        say "disk ($HT_RECORDS, HT_RAM=0): peak $(ht_gib "$HT_PEAK") GiB; $mem"
    fi
    HT_SUMMED=1   # (said: the next line is the RESULT)
}

# RUSTIC=R/rustic-bko: a copy of the set's PLAT/rustic-bko, checked against
# the set's SHA256SUMS.  The set is HT_ROUND's ~/backotter-work/roundNAME/bin,
# else HT_BKO_BIN, else ~/backotter-work/bko-bin (what the images ship);
# HT_RUSTIC names a file to test instead, unchecked.
ht_rustic_take() {  # PLAT
    local plat=$1 set="" src want="" got id c12
    HT_PLAT=$plat
    if [[ -n ${HT_RUSTIC:-} ]]; then
        [[ -f $HT_RUSTIC ]] || ht_die "HT_RUSTIC=$HT_RUSTIC: no such file"
        src=$HT_RUSTIC id="HT_RUSTIC, no set"
    else
        set=${HT_BKO_BIN:-$W/bko-bin}
        [[ -z ${HT_ROUND:-} ]] || set=$W/round$HT_ROUND/bin
        src=$set/$plat/rustic-bko
        # bko-bin's lines are "HASH *PLAT/FILE", a round's "HASH  ./PLAT/FILE".
        want=$(awk -v f="$plat/rustic-bko" '{ n = $2; sub(/^\*/, "", n); sub(/^\.\//, "", n) } n == f { print $1; exit }' \
            "$set/SHA256SUMS" 2>/dev/null)
        [[ -n $want ]] || ht_die "no $plat/rustic-bko in $set/SHA256SUMS"
        [[ -f $src ]] || ht_die "no $src"
        id=$(head -n 1 "$set/BUILD-INFO" 2>/dev/null)
        [[ -n $id ]] || id=${HT_ROUND:+round $HT_ROUND}
        [[ -n $id ]] || id=$set
    fi
    RUSTIC=$R/rustic-bko
    if ! { cp "$src" "$RUSTIC.part" && chmod 755 "$RUSTIC.part" && mv -f "$RUSTIC.part" "$RUSTIC"; }; then
        rm -f "$RUSTIC.part"; ht_die "couldn't copy $src to $RUSTIC"
    fi
    got=$(sha256sum "$RUSTIC" | cut -d' ' -f1)
    if [[ -n $want && $got != "$want" ]]; then
        rm -f "$RUSTIC"; ht_die "$src is $got; $set/SHA256SUMS says $want"
    fi
    c12=$(head -n 1 "${src%/*}/rustic-commit" 2>/dev/null | cut -c1-12)
    HT_RUSTIC_SRC=$src HT_RUSTIC_SHA=$got
    HT_RUSTIC_REC="rustic-bko $plat $got $src ($id; rustic ${c12:-?})"
}

# logs/manifest.txt (what L1 can key a run on), then the report's binaries:
# and note: lines.  FILEs are the driver's other inputs: disk images get an
# image line (size, mtime, inode; their .sha256 file's hash), the rest a sha256.
ht_manifest() {  # [FILE...]
    local f h holt=""
    [[ ! -f ${HOLT:-} ]] || holt=$(sha256sum "$HOLT" | cut -d' ' -f1)
    {
        echo "host $(hostname -s) kernel $(uname -r) $(qemu-system-x86_64 --version 2>/dev/null | head -n 1)"
        echo "driver $(realpath "$0") mode $HT_MODE work $R records $K reserved $RESERVED${HT_RESERVE_ROW:+ (pid $HT_RESERVE_ROW)}${HT_SEEDED:+ seeded from $HT_SEEDED}"
        env | grep '^HT_' | LC_ALL=C sort | sed 's/^/env /'
        echo "$HT_RUSTIC_REC"
        # The tree's commit: a private copy's COMMIT, else the
        # .backotter-commit that testvm/sync-hosts.sh writes in each copy.
        if [[ -f $T/../COMMIT ]]; then echo "tree $(head -n 1 "$T/../COMMIT")"
        elif [[ -f $T/.backotter-commit ]]; then echo "tree $(head -n 1 "$T/.backotter-commit")"; fi
        [[ -z $holt ]] || echo "sha256 $holt $HOLT"
        echo "sha256 $HT_RUSTIC_SHA $HT_RUSTIC_SRC"
        for f in "$(realpath "$0")" "$HT_LIB" "$HERE/judge.py" "$T/compare/fsmeta.py" "$T/vm-run" "$T/vm-mount" "$@"; do
            case $f in *.qcow2|*.img|*.raw|*.iso) continue ;; esac
            [[ ! -f $f ]] || echo "sha256 $(sha256sum "$f" | cut -d' ' -f1) $f"
        done
        for f in "$@" ${imgs[@]+"${imgs[@]}"}; do
            case $f in *.qcow2|*.img|*.raw|*.iso) ;; *) continue ;; esac
            [[ -f $f ]] || continue
            h=""; [[ ! -f $f.sha256 ]] || h=" sha256-file $(head -n 1 "$f.sha256" | cut -d' ' -f1)"
            echo "image $f $(stat -L -c '%s %Y %i' "$f")$h"
        done
        if [[ $HT_PLAT == linux && -x ${HOLT:-} ]]; then
            echo "version $("$HOLT" --version 2>&1 | head -n 1); $("$RUSTIC" --version 2>&1 | head -n 1)"
        fi
    } >"$R/logs/manifest.txt"
    say "binaries: bko-holt ${holt:0:12} (${HOLT:-}); $HT_RUSTIC_REC"
    [[ -z $HT_NOTE ]] || { say "note: $HT_NOTE"; HT_NOTE=""; }
    [[ -z $HT_SEEDED ]] || say "note: this run's state came from the attempt before it ($K until this run moves in, then $HT_SEEDED)"
    return 0
}

ht_peaks_row() {  # RC [PASS|FAIL]: the run's row in HT_RECORDS/peaks.tsv (ram-size.sh reads them)
    local rc=$1 res=${2:-} t=$HT_RECORDS/peaks.tsv lk="" ph
    [[ -n $res ]] || { res=FAIL; ((rc == 0 && fails == 0)) && [[ -z $HT_SIG ]] && res=PASS; }
    ph=$(IFS=,; echo "${PHASES[*]}")
    mkdir -p "$HT_RECORDS"
    if exec {lk}>>"$HT_RECORDS/.peaks.lock"; then
        flock -w 60 "$lk" || echo "${0##*/}: no lock on $HT_RECORDS/.peaks.lock in 60 s; writing the row anyway" >&2
    fi
    [[ -s $t ]] ||
        printf 'end_utc\thost\tdriver\trun\tgv\tphases\tseeded\trc\tresult\tmode\treserved_gib\tpeak_disk_kib\tpeak_anon_kib\twall_s\n' >>"$t"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$(hostname -s)" "${0##*/}" \
        "$RUN" "$GV" "$ph" "$([[ -n $HT_SEEDED ]] && echo 1 || echo 0)" "$rc" "$res" "$HT_MODE" "$RESERVED" \
        "$HT_PEAK" "$HT_PEAK_ANON" "$(($(date +%s) - HT_T0))" >>"$t"
    [[ -z $lk ]] || exec {lk}>&-
}

# DIR: the PIDs with an fd, cwd or mapping under DIR.  Not this shell, the
# driver, its ancestors or its lock watcher; nor this scan's own helpers,
# which have the driver's fds and cwd (the subshell of $$ it runs in, and all
# below it), nor a PID gone by the scan's end.
ht_holders() {
    local p=$$ i top="" x=("$$" "$BASHPID")
    [[ -z $HT_LOCKER ]] || x+=("$HT_LOCKER")
    for ((i = 0; i < 64; i++)); do
        p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
        [[ $p =~ ^[0-9]+$ ]] && ((p > 1)) || break
        x+=("$p")
    done
    p=$BASHPID
    for ((i = 0; i < 64 && p != $$; i++)); do
        top=$p
        p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
        [[ $p =~ ^[0-9]+$ ]] && ((p > 1)) || break
    done
    [[ $p == "$$" ]] || top=""
    { find /proc -mindepth 3 -maxdepth 3 -path '/proc/[0-9]*/fd/*' -lname "$1/*" 2>/dev/null | cut -d/ -f3
      find /proc -mindepth 2 -maxdepth 2 -path '/proc/[0-9]*/cwd' \( -lname "$1" -o -lname "$1/*" \) 2>/dev/null | cut -d/ -f3
      grep -lF " $1/" /proc/[0-9]*/maps 2>/dev/null | cut -d/ -f3; } | sort -u | grep -vxF -f <(printf '%s\n' "${x[@]}") |
        while read -r p; do [[ -d /proc/$p ]] && ! ht_under "$p" "$top" && echo "$p"; done
}
ht_under() {  # PID TOP: whether PID is TOP or below it (no TOP: no)
    local p=$1 i
    [[ -n $2 ]] || return 1
    for ((i = 0; i < 64; i++)); do
        [[ $p == "$2" ]] && return 0
        p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
        [[ $p =~ ^[0-9]+$ ]] && ((p > 1)) || return 1
    done
    return 1
}

# Nothing may hold R while it moves: the run's vm-mount states (in RAM, a
# mount point in R; one of its qemu-nbd serving a file in R, started by the
# run, ht_own; at the run's own mount point, one not attached yet), mounts
# in R, loop devices on its files (and what holds those), the run's own
# QEMU with a file in R (ht_qemus), then, in RAM, any process holding R,
# which is this run's.  A state that won't come down keeps every qemu-nbd it
# names (its file systems are still mounted), and its loop devices (a RAID
# member's copy-on-write file in R/tmp) are its vm-umount's to detach: what
# holds its mount point goes first.  Up to 30 s: TERM, then KILL; at the
# last try, lazy unmounts.  On /bkoscratch R is K and nothing moves: only
# the run's own states and QEMU go (the driver's ht_release undoes its own
# mounts), and a VM, vm-mount, mount, loop device or shell that someone else
# has on K's files (a look at the last attempt's disks) is left alone.  No
# qemu-nbd of a vm-mount state that isn't the run's is killed either, in
# RAM though it holds R (`vm-mount --ro R/src-0.qcow2 /mnt/look` by hand):
# its disk is mounted, and an imported pool on it would suspend, ZFS on the
# host with it (compare.py).  That vm-mount stays up, and the run says at
# once that R is still held by it: the files of R it has open stay in RAM
# until its vm-umount.
ht_free() {  # R GV [ram|disk]
    local r=$1 gv=$2 mode=${3:-ram} try s mp dev p t pids busy keep named hold left theirs=()
    for try in 1 2 3 4 5 6 7; do
        busy="" pids="" keep=" " theirs=()
        for s in /run/vm-mount/*; do
            [[ -f $s ]] || continue
            mp=$(systemd-escape --unescape --path "${s##*/}" 2>/dev/null) || continue
            dev=$(head -n 1 "$s" 2>/dev/null) p=""
            [[ -z $dev ]] || p=$(cat "/sys/block/${dev#/dev/}/pid" 2>/dev/null)
            if [[ $mode == ram && $mp == "$r"/* ]] || ht_nbd_own "$s" "$r" ||
                { [[ -z $p ]] && [[ $mp == /mnt/ht1-src-$gv || $mp == /mnt/ht1-dst-$gv ]]; }; then
                busy+=" vm-mount:$mp"
                if ! "${HT_LIB%/*/*}/vm-umount" "$mp" >/dev/null 2>&1 && [[ -f $s ]]; then
                    keep+="$(ht_nbd_pids "$s" | tr '\n' ' ')"
                    pids+=" $(ht_holders "$mp" | tr '\n' ' ')"
                fi
            else
                # Not the run's: its qemu-nbd stay, whatever they hold.
                for t in $(ht_nbd_pids "$s"); do keep+="$t "; theirs+=("$t $mp"); done
            fi
        done
        # (In RAM only: on /bkoscratch these may be someone else's, on K's files.)
        if [[ $mode == ram ]]; then
            for mp in $(findmnt -rn -o TARGET 2>/dev/null | awk -v r="$r/" 'index($0, r) == 1' | sort -r); do
                busy+=" $mp"
                umount "$mp" 2>/dev/null || fusermount3 -u "$mp" 2>/dev/null || ((try < 6)) || umount -l "$mp"
            done
            named=" $(awk '$1 == "cow" { printf "%s ", $3 } $1 == "loop" { printf "%s ", $2 }' /run/vm-mount/* 2>/dev/null)"
            while read -r dev p; do
                [[ $p == "$r"/* && $named != *" $dev "* ]] || continue
                busy+=" $dev"
                for mp in $(findmnt -rn -S "$dev" -o TARGET 2>/dev/null); do
                    umount "$mp" 2>/dev/null || fusermount3 -u "$mp" 2>/dev/null || ((try < 6)) || umount -l "$mp"
                done
                pids+=" $(find /proc -mindepth 3 -maxdepth 3 -path '/proc/[0-9]*/fd/*' -lname "$dev" 2>/dev/null | cut -d/ -f3 | tr '\n' ' ')"
                losetup -d "$dev" 2>/dev/null
            done < <(losetup -l -n -O NAME,BACK-FILE 2>/dev/null)
        fi
        pids+=" $(ht_qemus "$r" | tr '\n' ' ')"
        hold=" "; [[ $mode == disk ]] || hold+="$(ht_holders "$r" | tr '\n' ' ')"
        pids+=$hold left=""
        for t in "${theirs[@]}"; do [[ $hold != *" ${t%% *} "* ]] || left+=" vm-mount:${t#* } (qemu-nbd ${t%% *})"; done
        pids=$(for p in $pids; do [[ $keep == *" $p "* ]] || echo "$p"; done | sort -u | tr '\n' ' ')
        if [[ -z ${busy// /} && -z ${pids// /} ]]; then
            [[ -n $left ]] || return 0
            echo "${0##*/}: $r is still held:$left: a vm-mount the run didn't make, left up (its disk is mounted); its vm-umount lets go of R's files" >&2
            return 1
        fi
        ((try < 7)) || break
        # shellcheck disable=SC2086  # PIDs
        if ((try <= 2)); then kill $pids 2>/dev/null; else kill -9 $pids 2>/dev/null; fi
        sleep 5
    done
    echo "${0##*/}: $r is still held:${busy}${left}${pids:+ (pids $pids)}" >&2
    return 1
}
# STATE: the PIDs of the qemu-nbd serving the vm-mount state's devices (line
# 1's, its "nbd DEVICE" lines').  /sys/block/nbdN/pid is qemu-nbd's serving
# thread; its Tgid is the process, as ht_holders lists it.
ht_nbd_pids() {
    local d q
    for d in $(head -n 1 "$1" 2>/dev/null) $(awk '$1 == "nbd" { print $2 }' "$1" 2>/dev/null); do
        q=$(cat "/sys/block/${d#/dev/}/pid" 2>/dev/null) && [[ $q =~ ^[0-9]+$ ]] || continue
        awk '$1 == "Tgid:" { print $2 }' "/proc/$q/status" 2>/dev/null
    done
}
# STATE R: one of those serves a file in R and is the run's own (ht_own: its
# TMPDIR).
ht_nbd_own() {
    local q
    for q in $(ht_nbd_pids "$1"); do
        tr '\0' ' ' 2>/dev/null <"/proc/$q/cmdline" | grep -qF " $2/" && ht_own "$q" "$2" && return 0
    done
    return 1
}

# rsync handles INT, HUP and TERM itself, whatever it inherits.  In a session
# of its own a terminal's ^C can't reach it; an abort's TERM to the job's tree
# still does, and costs one try of three, each going on from the last.  (A
# try cut short can leave its temp file, .NAME.XXXXXX: the callers copy into
# a new directory with --delete, so the next try removes it.)
ht_rsync() {
    local i rc
    for i in 1 2 3; do
        setsid -w rsync "$@" && return 0
        rc=$?; echo "${0##*/}: rsync exit $rc (try $i of 3)" >&2
    done
    return 1
}

# The run's row in Test2's ledger: t2-host row holt reads K's records (or
# knows the run was refused, or its records lost) and appends the row to
# T2_HOST_ROOT's ledger.  timeout's TERM at 30 s ends it though ht_end
# ignores TERM (timeout gives its command TERM's default back: exit 124,
# measured on Chopin), its KILL 5 s later one that ignores TERM itself.
# python3 -B: no __pycache__ in the copy.  It reads no stdin, and its output
# goes to one note: line: the row's id, or why there is none.
ht_t2_row() {  # RC CODE --k DIR | --lost | --refused
    local rc=$1 code=$2 tool=${T:-}/test2/t2-host out s last
    shift 2
    [[ ${HT_CLEAN:-1} != 0 && ${RUN:-} != debug ]] || return 0
    if [[ ! -f $tool ]]; then
        echo "${0##*/}: note: no Test2 ledger row ($tool isn't there)" >&2
        return 0
    fi
    out=$(T2_WORK=${W:-} timeout -k 5 30 python3 -B "$tool" row holt "$@" --r "${R:-}" --run "${RUN:-}" \
        --gv "${GV:-}" --driver "${0##*/}" --rc "$rc" --code "$code" </dev/null 2>&1)
    s=$?
    last=$(printf '%s\n' "$out" | grep -v '^[[:space:]]*$' | tail -n 1 | cut -c1-200)
    if ((s == 0)); then
        echo "${0##*/}: note: Test2 ledger row ${last:-written}" >&2
    elif ((s == 124 || s == 137)); then
        echo "${0##*/}: note: no Test2 ledger row ($tool took over 30 s)" >&2
    else
        echo "${0##*/}: note: no Test2 ledger row ($tool exit $s: ${last:-no output})" >&2
    fi
    return 0
}

ht_copy_records() {  # R DEST [FILE...]: into a new DEST, R's records only (report.txt, logs/, dumps/, the FILEs)
    local f x=(--include=/report.txt --include='/logs/***' --include='/dumps/***')
    for f in "${@:3}"; do x+=("--include=/$f"); done
    rm -rf --one-file-system "$2"
    ht_rsync -aHx --delete-excluded "${x[@]}" --exclude='*' "$1/" "$2/" && mkdir -p "$2/logs"
}

# The EXIT trap: how a run ends.  A refusal leaves nothing; otherwise the run
# gets a RESULT line if it has none, everything that holds R is released, its
# row goes into peaks.tsv, a pass that ran verify (or a run stopped over its
# reservation) keeps its records only, and R goes to K.  The driver is still
# under its reservation here (the ledger PID is its ancestor), so the room is
# held until the move is done.
ht_end() {  # RC
    # To its end: a later TERM (an abort, sudo's relay), a closed stdout or
    # an unbound variable can't cut it short; cp and zstd inherit the ignored
    # signals (rsync doesn't: ht_rsync).
    trap '' TERM INT HUP PIPE QUIT
    local rc=$1 code=1 m="" f pat over="" kept last="" said=0 zipped=() x=() what="" placed="" msg=""
    set +eu
    trap - EXIT
    # The driver's own stdio again (ht_begin's copies): a signal that came
    # inside a redirected function call leaves this trap that call's, in R.
    if [[ -n $HT_FD1 && -n $HT_FD2 ]]; then
        exec </dev/null 1>&"$HT_FD1" 2>&"$HT_FD2"
        exec {HT_FD1}>&- {HT_FD2}>&-; HT_FD1="" HT_FD2=""
    fi
    if [[ -n $HT_SAMPLER ]]; then kill "$HT_SAMPLER" 2>/dev/null; wait "$HT_SAMPLER" 2>/dev/null; fi
    # (A phase cut short has its peak in the files only.)
    read -r m 2>/dev/null <"$R/.ht/peak"; [[ $m =~ ^[0-9]+$ ]] && ((m > HT_PEAK)) && HT_PEAK=$m
    m=""; read -r m 2>/dev/null <"$R/.ht/peak-anon"
    [[ $HT_PEAK_ANON != - && $m =~ ^[0-9]+$ ]] && ((m > HT_PEAK_ANON)) && HT_PEAK_ANON=$m
    read -r over 2>/dev/null <"$R/.ht/overrun"

    # Refused before any phase: nothing is kept, and nothing reaches K.  An R
    # in RAM that was there (a dead run's) keeps its .ht, a dead run's again,
    # for the sweep; a K put back gets its references back (ht_rotate's).
    if ((HT_STARTED == 0 && rc == 2)); then
        [[ $R != "$K" ]] && ((HT_MADE_R == 0)) || rm -rf "$R/.ht"
        rmdir "$R/tmp" 2>/dev/null
        ((HT_MADE_R)) && { rm -rf --one-file-system "$R"; rmdir "${R%/*}" 2>/dev/null; }
        [[ -z $HT_ROTATED || -e $K ]] || { mv -T "$HT_ROTATED" "$K" && ht_repoint "$HT_ROTATED" "$K" >/dev/null 2>&1; }
        ht_t2_row 2 2 --refused
        ht_unlock
        exit 2
    fi
    # Its RESULT line is the report's last line after ht_summary's (a signal
    # that comes after it ends a run that had finished).
    last=$(tail -n 1 "$R/report.txt" 2>/dev/null)
    ((HT_SUMMED)) && [[ $last == "RESULT: "* ]] && said=1
    if ((said == 0)); then
        ((HT_SUMMED)) || ht_summary
        if [[ -n $over ]]; then
            say "RESULT: FAIL ($((fails + 1)); harness: $over; stopped before its RESULT, exit $rc)"
        else
            say "RESULT: FAIL ($((fails + 1)); ended ${HT_SIG:+by SIG$HT_SIG }before its RESULT, exit $rc)"
        fi
        fails=$((fails + 1))
        # The exit code: 1, or a signal's 128+N (not for the sampler's own stop).
        [[ -z $HT_SIG || -n $over ]] || code=$rc
    elif [[ -n $over ]]; then
        # (Stopped just after its RESULT line: that verdict stands.)
        say "note: harness: $over (after the RESULT line): the run keeps its records only"
        ((fails == 0)) && code=0
    elif [[ $last == "RESULT: PASS" ]] && ((fails == 0)) && { ((rc == 0)) || [[ -n $HT_SIG ]]; }; then
        code=0
    elif [[ -n $HT_SIG ]]; then
        code=$rc
    fi

    # Left as it is: ht1-debug.sh's run (its reservation outlives it), or a
    # rescuer HT_KEEP kept up (it has R's disks open).  .ht/held says so: the
    # next run may use that rescuer's port, and the sweep leaves R while the
    # reservation it names lives.
    if [[ ${HT_CLEAN:-1} == 0 ]] || { [[ ${HT_KEEP:-} == 1 && -n $(ht_qemus "$R") ]]; }; then
        rm -rf "$R/.ht"
        mkdir -p "$R/.ht" && echo "${HT_RESERVE_PID:-none}" >"$R/.ht/held"
        ht_unlock
        exit "$code"
    fi

    declare -F ht_release >/dev/null && ht_release
    ht_free "$R" "$GV" "$HT_MODE"
    ht_peaks_row "$rc" "$( ((code == 0)) && [[ -z $over ]] && echo PASS || echo FAIL)"

    if [[ -n $over ]] || { ((code == 0)) && [[ " ${PHASES[*]} " == *" verify "* ]]; }; then
        shopt -s nullglob
        for pat in "${HT_BULK[@]}"; do
            [[ -n $pat && $pat != /* && $pat != *..* ]] || continue
            # shellcheck disable=SC2206  # a glob in R
            for f in "$R"/$pat; do rm -rf --one-file-system -- "$f"; done
        done
        for f in "${HT_ZIP[@]}"; do
            [[ -f $R/$f ]] && zstd -q -f -3 -T2 --rm "$R/$f" && zipped+=("$f")
        done
        shopt -u nullglob
        kept="pass: records only, $(date -u +%FT%TZ)"
        [[ -z $over ]] || kept="overrun: records only, $(date -u +%FT%TZ); harness: $over"
        echo "$kept; deleted ${HT_BULK[*]:-nothing}; zstd ${zipped[*]:-nothing}" >"$R/logs/kept.txt"
    else
        echo "fail: everything kept (exit $rc${HT_SIG:+, SIG$HT_SIG}$( ((code == 0)) && echo ", no verify")), $(date -u +%FT%TZ)" >"$R/logs/kept.txt"
    fi

    # R to K: copied into K.part (a full /bkoscratch can't leave half a run
    # in each place), one file system only, sparse, with unchanged files of
    # the attempt before (a seeded repository) as hard links to its; then the
    # attempt before becomes K.N, and K.part K.  R/.ht stays until R goes (a
    # driver killed here leaves R to the sweep), with .ht/moved once K is in.
    if [[ $R == "$K" ]]; then
        rm -rf "$R/.ht"
    else
        mkdir -p "${K%/*}"; rm -rf --one-file-system "$K.part"
        echo "${0##*/}: moving $R to $K ($(ht_gib "$(du -sxk "$R" 2>/dev/null | cut -f1)") GiB)"
        x=(); [[ ! -d $K ]] || x=("--link-dest=$K")
        if ht_rsync -aHx --sparse --delete --exclude=/.ht/ "${x[@]}" "$R/" "$K.part/"; then
            ht_repoint "$R" "$K" "$K.part"
            what="the run is"
        else
            # Nothing of ours stays in RAM without a reservation: the records, then R goes.
            echo "${0##*/}: couldn't copy $R to $K: keeping its records only" >&2
            sed -i "1s/^/lost: records only, the copy of the rest to K failed ($(date -u +%FT%TZ)); it was /" "$R/logs/kept.txt"
            x=()
            for f in "${HT_ZIP[@]}"; do
                [[ ! -f $R/$f ]] || x+=("$f")
                [[ ! -f $R/$f.zst ]] || x+=("$f.zst")
            done
            ht_copy_records "$R" "$K.part" "${x[@]}" && what="the run's records are"
        fi
        if [[ -n $what ]] && placed=$(ht_place "$K"); then
            : >"$R/.ht/moved"
            msg="$what in ${placed%% *}"
            [[ $placed != *" "* ]] || msg+=" (the one before it: ${placed#* })"
        elif [[ -n $what ]]; then
            : >"$R/.ht/moved"
            msg="$what in $K.part (it couldn't be renamed $K)"
        else
            rm -rf --one-file-system "$K.part"
            echo "${0##*/}: couldn't copy $R's records to $K either: they are lost" >&2
        fi
        rm -rf --one-file-system "$R"; rmdir "${R%/*}" 2>/dev/null
    fi
    [[ -z $msg ]] || echo "${0##*/}: $msg"
    # Test2's row, from K as it now is (README "Where a run works, and what it keeps").
    if [[ $R == "$K" ]]; then ht_t2_row "$rc" "$code" --k "$K"
    elif [[ -n $placed ]]; then ht_t2_row "$rc" "$code" --k "${placed%% *}"
    elif [[ -n $what ]]; then ht_t2_row "$rc" "$code" --k "$K.part"
    else ht_t2_row "$rc" "$code" --lost; fi
    ht_unlock
    exit "$code"
}

# The sweep: each HT_WORK/RUN/GV that the lib made (R/.ht) and whose driver
# died (SIGKILL, OOM; its run lock free): its records go to K (rotated as
# ever), and R is deleted.  A seed cut short and an R already in K are only
# deleted.  What has no R/.ht (another session's older driver), ht1-debug.sh's
# debug/ and debug-state/, and a run left on purpose (HT_CLEAN=0, HT_KEEP)
# while the reservation in its .ht/held lives, stay.
ht_sweepable() {  # R
    local h=""
    [[ -d $1 && ! -L $1 ]] && [[ -f $1/.ht/pid || -f $1/.ht/held ]] || return 1
    [[ -z $(ht_in_use "$1") ]] || return 1
    read -r h 2>/dev/null <"$1/.ht/held"
    ! [[ $h =~ ^[0-9]+$ && -d /proc/$h ]]
}
ht_sweep() {  # WORK
    local work=$1 r run gv k lk
    mkdir -p "$HT_LOCKS"
    for r in "$work"/*/*; do
        gv=${r##*/} run=${r%/*}; run=${run##*/}
        case $run in debug|debug-state) continue ;; esac
        [[ $run =~ $HT_NAME && $gv =~ $HT_NAME ]] || continue
        ht_sweepable "$r" || continue
        # K, and with it the run lock, as the run had them (its HT_RECORDS).
        k=""; read -r k 2>/dev/null <"$r/.ht/k"
        [[ $k =~ ^/[A-Za-z0-9._/-]+$ && $k != *..* && $k == */"$run/$gv" ]] || k=$HT_RECORDS/$run/$gv
        exec {lk}>>"$(ht_lockfile "$k")" || continue
        # (Again under the lock: the run may have ended in between.)
        if flock -n "$lk" && ht_sweepable "$r"; then ht_sweep_one "$r" "$k" "$gv"; fi
        exec {lk}>&-
    done
    return 0
}
ht_sweep_one() {  # R K GV: under K's run lock, a dead run's R: released, its records to K, R deleted
    local r=$1 k=$2 pid="" placed
    read -r pid 2>/dev/null <"$r/.ht/pid"
    ht_free "$r" "$3"
    if [[ -e $r/.ht/moved ]]; then
        echo "${0##*/}: deleted $r (its run is in $k)"
    elif [[ -e $r/.ht/seeding ]]; then
        echo "${0##*/}: deleted $r (a seed cut short; $k as it was)"
    elif mkdir -p "${k%/*}" && ht_copy_records "$r" "$k.part" &&
        echo "swept: records only (the run died; pid ${pid:-?}), $(date -u +%FT%TZ)" >"$k.part/logs/kept.txt" &&
        placed=$(ht_place "$k"); then
        echo "${0##*/}: swept $r into ${placed%% *}$([[ $placed != *" "* ]] || echo " (the one before it: ${placed#* })")"
    else
        rm -rf --one-file-system "$k.part"
        echo "${0##*/}: couldn't copy $r's records to $k: they are lost" >&2
    fi
    rm -rf --one-file-system "$r"; rmdir "${r%/*}" 2>/dev/null
    return 0
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    set -uo pipefail
    [[ ${1:-} == --sweep && $# -le 2 ]] || { echo "usage: ${0##*/} --sweep [HT_WORK]" >&2; exit 2; }
    [[ $EUID == 0 ]] || { echo "${0##*/} --sweep: run as root" >&2; exit 2; }
    [[ -z ${2:-} || ${2%/} == "${HT_WORK%/}" ]] || { echo "${0##*/} --sweep: $2 isn't HT_WORK ($HT_WORK)" >&2; exit 2; }
    # HT_WORK and HT_RECORDS as a pair, as ht1-queue.sh takes them: the
    # defaults, or a test's /mnt/bko-ram/NAME and /bkoscratch/NAME.
    w=$(realpath -m -- "$HT_WORK") k=$(realpath -m -- "$HT_RECORDS") n=${w#/mnt/bko-ram/}
    [[ $n =~ $HT_NAME && $n != tmp && $w == "/mnt/bko-ram/$n" && $k == "/bkoscratch/$n" ]] ||
        { echo "${0##*/} --sweep: HT_WORK $w and HT_RECORDS $k: /mnt/bko-ram/NAME and /bkoscratch/NAME, one NAME (holttest1d)" >&2; exit 2; }
    ht_sweep "$w"
fi
