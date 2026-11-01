#!/usr/bin/env bash
# A whole test round on one KVM host, unattended: checks the binaries, runs
# every platform's round trips (the scripts here) at once within the
# host's limits, and prints a verdict table.  Agents only read the failures.
#
# Usage: run-all.sh [--host chopin|cinqcent] [--platforms LIST] [--only REGEX]
#                   [--dry-run] BIN_DIR LOG_DIR
#   --host       whose platforms, ports and limits (default: this hostname)
#   --platforms  comma- or space-separated, the other host's skipped (an
#                empty list: nothing; default: all of the host's):
#                  chopin:   linux sparse zfsguests dragonfly openbsd_illumos
#                  cinqcent: freebsd netbsd
#                linux      fs_roundtrip.py on zfs ext4 xfs btrfs f2fs bcachefs
#                           tmpfs jfs, zfs --onto ext4; nonroot-rt.sh linux
#                sparse     sparse-rt.sh on ext4 xfs btrfs zfs zfs-nocomp
#                zfsguests  zfs-guest-rt.sh ubuntu24, ubuntu26-zfsmirror
#                dragonfly  dragonfly6: run-guest-rt.sh, nonroot, sparse-rt.sh
#                openbsd_illumos
#                           openbsd8, omnios58: run-guest-rt.sh, nonroot;
#                           sparse-rt.sh in omnios58
#                freebsd    freebsd15, freebsd15-ufs: run-guest-rt.sh,
#                           nonroot, sparse-rt.sh
#                netbsd     netbsd11, alpinebsd: run-guest-rt.sh, nonroot;
#                           sparse-rt.sh in netbsd11's build image (cc)
#   --only       run only the steps whose PLATFORM/STEP matches (grep -E)
#   --dry-run    check the binaries and print every step's command; run none
#   BIN_DIR      the round's binaries: OS/restic-bko, OS/rustic-bko and their
#                restic-commit, rustic-commit (OS linux freebsd openbsd netbsd
#                dragonfly illumos; omnios58 runs illumos', alpinebsd netbsd's)
#   LOG_DIR      PLATFORM/STEP.log (and the scripts' own logs beside it),
#                summary.txt and summary.json (the table), rows.jsonl
#
# Environment:
#   OLD_BIN      the round before's bin directory: sparse-rt.sh's and
#                fs_roundtrip.py's snapshot identity (SPEC3 2); none: not checked
#   EXPECT_RESTIC_COMMIT, EXPECT_RESTIC_DIGEST, EXPECT_RUSTIC_COMMIT,
#   EXPECT_RUSTIC_CORE_COMMIT, EXPECT_RESTIC_VERSION, EXPECT_RUSTIC_VERSION
#                what the binaries must be (commit prefixes, restic's source
#                digest, e.g. 0.19.1+backotter.3); unset: only that every OS's
#                commit files agree.  Refuses to run on a mismatch, a missing
#                binary or commit file, or a SHA256SUMS line that fails.
#   PORT_BASE    ports are PORT_BASE + 10..19 (default 2700 on chopin, 2900
#                on cinqcent; e.g. 2750 for 2760-2769)
#   MAX_GUESTS   guests at once (default chopin 6, cinqcent 3); COUNT_OTHERS=1
#                (cinqcent's default) counts every other QEMU on a port in
#                GUEST_PORTS (ours: 2700-2799 chopin, 2900-2999 cinqcent) too
#   HOST_JOBS    fs_roundtrip.py and nonroot-rt.sh linux runs at once (4);
#                SPARSE_JOBS sparse-rt.sh runs on the host at once (2)
#   STEP_TIMEOUT seconds, every step (default by kind, 1.5-6 h: round 9's
#                times several over, as a host as loaded as Chopin was in its
#                round 10 (load 100) takes)
#   RUN_PREFIX   names of pools, mount points, images (default bkora: pools
#                bkora-fs-*, bkora-sp-*, bkora-nr); one run per prefix
#   BKO_WORK     where vms/ is (default ~/backotter-work); the scripts work
#                in LOG_DIR/work, which links to its vms/
#   RAM_DIR      the guests' --ram overlays (default LOG_DIR/work/ram, on
#                disk: round 9 filled /dev/shm)
#   BIG          sparse-rt.sh's big file on the host, GiB (20); GUEST_BIG in
#                guests (2)
#   BASELINE     an earlier LOG_DIR/summary.json: nonroot's per-direction
#                counts must be the same as there
#   KEEP_WORK=1  keep fs_roundtrip.py's repositories
#   ANY_HOST=1   run another host's platforms here too
#
# Steps of a platform run one after another in chains (one per port);
# chains and platforms run at once, a step waiting for a guest slot (or a
# host job slot).  A guest that doesn't come up is retried once.  A step
# that crashes or times out is a FAIL with its log's tail.  The verdicts
# are verdict.py's (accepted differences: its header).  Exits 0 only if
# every step passes; 2 if it refused to run.  On exit, Ctrl-C or TERM (a
# background job of a script ignores INT: TERM it) it stops its own
# processes (by BKO_RUNALL_ID in their environment, QEMU's too), and
# destroys its pools, mounts and loop devices (RUN_PREFIX).
#
# Needs: bash, python3, flock, timeout, setsid; root steps (linux, sparse):
# root or sudo -n; and what each script needs.

set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
die() { echo "run-all: $*" >&2; exit 2; }
usage() { sed -n '6,31p' "$0" >&2; exit 2; }

host="" plats="" pset="" only="" dry=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --host) host=${2:-}; shift 2 ;;
        --platforms) plats=${2:-} pset=1; shift 2 ;;
        --only) only=${2:-}; shift 2 ;;
        --dry-run) dry=1; shift ;;
        -h|--help) usage ;;
        -*) die "no such option: $1" ;;
        *) break ;;
    esac
done
[[ $# -eq 2 ]] || usage
[[ -d $1 ]] || die "no bin directory $1"
BIN=$(cd "$1" && pwd) LOG=$2
[[ $LOG == /* ]] || LOG=$PWD/$LOG
[[ -n $host ]] || host=$(hostname -s 2>/dev/null || hostname)
case $host in
    chopin*) host=chopin; all="linux sparse zfsguests dragonfly openbsd_illumos"
        : "${PORT_BASE:=2700}" "${MAX_GUESTS:=6}" "${COUNT_OTHERS:=0}" "${GUEST_PORTS:=2700-2799}" ;;
    cinqcent*) host=cinqcent; all="freebsd netbsd"
        : "${PORT_BASE:=2900}" "${MAX_GUESTS:=3}" "${COUNT_OTHERS:=1}" "${GUEST_PORTS:=2900-2999}" ;;
    *) die "no such host: $host (--host chopin or cinqcent)" ;;
esac
plats=${plats//,/ }
[[ -z $pset || $plats == all ]] && plats=$all
mine=""
for p in $plats; do
    case " linux sparse zfsguests dragonfly openbsd_illumos freebsd netbsd " in
        *" $p "*) ;; *) die "no such platform: $p" ;; esac
    if [[ " $all " == *" $p "* || -n ${ANY_HOST:-} ]]; then mine+=" $p"
    else echo "run-all: $p is the other host's: skipped (ANY_HOST=1 runs it here)"; fi
done
plats=${mine# }
[[ -n $plats ]] || { echo "run-all: nothing to run on $host"; exit 0; }
HOST_JOBS=${HOST_JOBS:-4} SPARSE_JOBS=${SPARSE_JOBS:-2}
PFX=${RUN_PREFIX:-bkora}
[[ $PFX =~ ^bko[a-z0-9]+$ ]] || die "RUN_PREFIX must be bko followed by letters or digits"
SRCWORK=${BKO_WORK:-$HOME/backotter-work}
[[ -d $SRCWORK/vms ]] || die "no $SRCWORK/vms (set BKO_WORK)"
OLD=${OLD_BIN:-}
[[ -z $OLD || -d $OLD ]] || die "no OLD_BIN directory $OLD"
[[ -n $OLD ]] && OLD=$(cd "$OLD" && pwd)
C=$here W=$LOG/work
notes=()
P() { echo $((PORT_BASE + $1)); }

# --- the binaries -------------------------------------------------------------
need_os=""
for p in $plats; do
    case $p in
        linux|sparse|zfsguests) need_os+=" linux" ;;
        dragonfly) need_os+=" dragonfly" ;;
        openbsd_illumos) need_os+=" openbsd illumos" ;;
        freebsd) need_os+=" freebsd" ;;
        netbsd) need_os+=" netbsd" ;;
    esac
done
need_os=$(printf '%s\n' $need_os | sort -u | tr '\n' ' ')
bad=() seen_restic="" seen_rustic="" seen_core="" seen_digest=""
prefix_ok() { [[ -z $2 || ( ${#2} -ge 7 && $1 == "$2"* ) ]]; }  # ACTUAL EXPECTED
agree() {  # NAME VAR VALUE: VAR holds the first value seen
    local n=$1 v=$3
    if [[ -z ${!2} ]]; then printf -v "$2" %s "$v"
    elif [[ ${!2} != "$v" ]]; then bad+=("$n differs between OSes: ${!2} and $v"); fi
}
echo "== binaries in $BIN ($host: $need_os)"
for os in $need_os; do
    for t in restic rustic; do
        f=$BIN/$os/$t-bko
        if [[ ! -s $f ]]; then bad+=("no $f"); continue; fi
        if [[ $os == linux && $(uname -s) == Linux ]]; then
            if [[ $t == restic ]]; then v=$("$f" version 2>&1 | head -n 1); else v=$("$f" --version 2>&1 | head -n 1); fi
        else
            v=$(grep -aoE "[0-9]+\.[0-9]+\.[0-9]+\+backotter\.[0-9]+" "$f" | sort -u | tr '\n' ' ')
        fi
        ev=EXPECT_${t^^}_VERSION
        [[ -z ${!ev:-} || " $v " == *"${!ev}"[!0-9]* ]] || bad+=("$f: version $v, not ${!ev}")
        echo "   $os/$t-bko: $v"
        cf=$BIN/$os/$t-commit
        if [[ ! -s $cf ]]; then bad+=("no $cf"); continue; fi
        c=$(head -n 1 "$cf" | cut -d' ' -f1)
        if [[ $t == restic ]]; then
            d=$(sed -n 's/^source digest //p' "$cf")
            agree "restic commit" seen_restic "$c"; agree "restic source digest" seen_digest "$d"
            prefix_ok "$c" "${EXPECT_RESTIC_COMMIT:-}" || bad+=("$cf: restic $c, not ${EXPECT_RESTIC_COMMIT}")
            [[ -z ${EXPECT_RESTIC_DIGEST:-} || $d == "$EXPECT_RESTIC_DIGEST" ]] ||
                bad+=("$cf: source digest $d, not $EXPECT_RESTIC_DIGEST")
            echo "      restic $c, source digest ${d:-none}"
        else
            k=$(sed -n 's/^rustic_core \([0-9a-f]*\).*/\1/p' "$cf")
            agree "rustic commit" seen_rustic "$c"; agree "rustic_core commit" seen_core "$k"
            prefix_ok "$c" "${EXPECT_RUSTIC_COMMIT:-}" || bad+=("$cf: rustic $c, not ${EXPECT_RUSTIC_COMMIT}")
            prefix_ok "$k" "${EXPECT_RUSTIC_CORE_COMMIT:-}" ||
                bad+=("$cf: rustic_core $k, not ${EXPECT_RUSTIC_CORE_COMMIT}")
            echo "      rustic $c, rustic_core ${k:-none}"
        fi
        if [[ -f $BIN/SHA256SUMS ]] && grep -q " \./$os/$t-bko\$" "$BIN/SHA256SUMS"; then
            (cd "$BIN" && grep " \./$os/$t-bko\$" SHA256SUMS | sha256sum -c --quiet >/dev/null 2>&1) ||
                bad+=("$f: not the binary SHA256SUMS lists")
        fi
    done
done
if [[ -z ${EXPECT_RESTIC_COMMIT:-}${EXPECT_RUSTIC_COMMIT:-}${EXPECT_RUSTIC_CORE_COMMIT:-}${EXPECT_RESTIC_DIGEST:-} ]]; then
    notes+=("binaries not checked against expected commits (EXPECT_* unset); they agree with each other")
fi
if [[ ${#bad[@]} -gt 0 ]]; then
    printf 'run-all: refused: %s\n' "${bad[@]}" >&2
    [[ -n $dry ]] || exit 2
fi
oldpair() {  # OS: OLD_BIN/OS's two binaries, if both are there
    [[ -n $OLD && -s $OLD/$1/restic-bko && -s $OLD/$1/rustic-bko ]]
}
if [[ -z $OLD ]]; then notes+=("OLD_BIN unset: snapshot identity (SPEC3 2) not checked")
else
    for os in $need_os; do oldpair "$os" || notes+=("no OLD_BIN/$os/{restic,rustic}-bko: identity not checked on $os"); done
fi

# --- the steps ----------------------------------------------------------------
# step PLATFORM CHAIN NAME KIND GUESTS SEMAPHORE TIMEOUT ROOT JUDGE-ARGS CMD
# CMD is a bash snippet, run in LOG_DIR/PLATFORM.  Steps of a chain run in
# order; chains run at once.
S_PLAT=() S_CHAIN=() S_NAME=() S_KIND=() S_GUESTS=() S_SEM=() S_TO=() S_ROOT=() S_JARGS=() S_CMD=()
step() {
    [[ -z $only ]] || grep -qE -- "$only" <<<"$1/$3" || return 0
    S_PLAT+=("$1") S_CHAIN+=("$1/$2") S_NAME+=("$3") S_KIND+=("$4") S_GUESTS+=("$5") S_SEM+=("$6")
    S_TO+=("${STEP_TIMEOUT:-$7}") S_ROOT+=("$8") S_JARGS+=("$9") S_CMD+=("${10}")
}
q() { printf '%q ' "$@"; }
if [[ $(id -u) == 0 ]]; then SUDO=""; else SUDO="sudo -n"; fi
ID=${BKO_RUNALL_ID:-$PFX-$$-$(date +%s)}
export BKO_RUNALL_ID=$ID BKO_WORK=$W RAM_DIR=${RAM_DIR:-$W/ram}
rootenv() { echo "$SUDO env BKO_RUNALL_ID=$ID BKO_WORK=$W"; }

guest_rt() {  # PLATFORM CHAIN GUEST PORT OS
    local n=4 to=14400
    [[ $3 == omnios* ]] && n=8 to=21600
    step "$1" "$2" "rt-$3" guest-rt 1 - $to "" "--expect $n" \
        "RT_LOGS=$(q "$LOG/$1/rt") $(q "$C/run-guest-rt.sh" "$3" "$4" "$BIN/$5/restic-bko" "$BIN/$5/rustic-bko")"
}
nonroot() {  # PLATFORM CHAIN GUEST PORT OS
    step "$1" "$2" "nonroot-$3" nonroot 1 - 5400 "" "${BASELINE:+--baseline $BASELINE}" \
        "NONROOT_LOGS=$(q "$LOG/$1/nonroot") RESTIC=$(q "$BIN/$5/restic-bko") RUSTIC=$(q "$BIN/$5/rustic-bko") $(q "$C/nonroot-rt.sh" "$3" "$4")"
}
guest_sparse() {  # PLATFORM CHAIN GUEST PORT OS [ENV...]
    local p=$1 ch=$2 g=$3 port=$4 os=$5 files args="/root/restic-bko /root/rustic-bko"
    shift 5
    files=("$C/sparse-rt.sh" "$C/rt-common.sh" "$BIN/$os/restic-bko" "$BIN/$os/rustic-bko")
    if oldpair "$os"; then
        files+=("$W/old/$os/restic-bko-old" "$W/old/$os/rustic-bko-old")
        args+=" /root/restic-bko-old /root/rustic-bko-old"
    fi
    step "$p" "$ch" "sparse-$g" sparse 1 - 11400 "" "" \
        "$* T=10800 $(q "$C/guest-run.sh" "$g" "$port" "cd /root && BIG=${GUEST_BIG:-2} sh sparse-rt.sh $args" "${files[@]}")"
}

for p in $plats; do
    case $p in
    linux)
        for fs in zfs 'zfs --onto ext4' bcachefs f2fs ext4 xfs btrfs tmpfs jfs; do
            n=${fs/ --onto /-onto-}
            # bcachefs format sometimes aborts in malloc (round 8): again.
            step linux "$n" "$n" fsrt 0 host 14400 1 "--results $(q "$LOG/linux/$n")${OLD:+ --identity}" "
                t=\$(mktemp); for i in 1 2 3 4 5; do
                    $(rootenv) RESTIC_BKO=$(q "$BIN/linux/restic-bko") RUSTIC_BKO=$(q "$BIN/linux/rustic-bko") \
                        ${OLD:+BKO_OLD_RESTIC=$(q "$OLD/linux/restic-bko")} BKO_FSRT_PREFIX=$PFX-fs \
                        BKO_MKFS_BCACHEFS=\"\${BKO_MKFS_BCACHEFS:-}\" python3 $(q "$C/fs_roundtrip.py") $fs 2>&1 | tee \$t
                    rc=\${PIPESTATUS[0]}; grep -q 'failed (-6): malloc' \$t || break; echo \"== again (\$i): malloc abort\"
                done; rm -f \$t
                r=$(q "$here/../../fs-roundtrip/$PFX-fs/$n")
                mkdir -p $n && $SUDO sh -c \"cp \$r/*.json $n/ 2>/dev/null; chown -R $(id -u):$(id -g) $n\"
                [ -n \"\${KEEP_WORK:-}\" ] || $SUDO rm -rf \"\$r\"
                exit \$rc"
        done
        # nonroot-rt.sh linux's mount points are the host's: wait for any
        # other run of it to end.
        step linux nonroot nonroot-linux nonroot 0 host 5400 1 "${BASELINE:+--baseline $BASELINE}" "
            while findmnt /mnt/bkonr-ext4 >/dev/null || findmnt /mnt/bkonr-zfs >/dev/null; do
                echo 'waiting for another nonroot-rt.sh linux'; sleep 30; done
            $(rootenv) NONROOT_POOL=$PFX-nr NONROOT_LOGS=$(q "$LOG/linux/nonroot") RESTIC=$(q "$BIN/linux/restic-bko") \
                RUSTIC=$(q "$BIN/linux/rustic-bko") $(q "$C/nonroot-rt.sh") linux"
        ;;
    sparse)
        for run in ext4 xfs btrfs zfs zfs-nocomp; do
            fs=${run%-nocomp} opts=""; [[ $run == zfs-nocomp ]] && opts="-O compression=off"
            step sparse "$run" "sparse-$run" sparse 0 sparse 10800 1 "" \
                "$(rootenv) SPARSE_PREFIX=$PFX-sp SPARSE_FS=$fs SPARSE_RUN=$run SPARSE_ZFS_OPTS=$(q "$opts") BIG=${BIG:-20} \
                    $(q "$C/sparse-rt.sh" "$BIN/linux/restic-bko" "$BIN/linux/rustic-bko") \
                    ${OLD:+$(q "$OLD/linux/restic-bko" "$OLD/linux/rustic-bko")}"
        done ;;
    zfsguests)
        i=7
        for g in ubuntu24 ubuntu26-zfsmirror; do
            step zfsguests "$g" "zfs-$g" zfsguest 1 - 21600 "" "--results $(q "$LOG/zfsguests/zfs-$g")${OLD:+ --identity}" \
                "BKO_BIN=$(q "$BIN") ${OLD:+OLD_RESTIC=$(q "$OLD/linux/restic-bko") OLD_RUSTIC=$(q "$OLD/linux/rustic-bko")} \
                    ZFS_GUEST_LOGS=$(q "$LOG/zfsguests/zfs-$g") $(q "$C/zfs-guest-rt.sh" "$g" "$(P $i)")"
            i=$((i + 1))
        done ;;
    dragonfly)
        guest_rt dragonfly a dragonfly6 "$(P 10)" dragonfly
        nonroot dragonfly b dragonfly6 "$(P 11)" dragonfly
        guest_sparse dragonfly b dragonfly6 "$(P 11)" dragonfly ;;
    openbsd_illumos)
        guest_rt openbsd_illumos a openbsd8 "$(P 12)" openbsd
        nonroot openbsd_illumos a openbsd8 "$(P 12)" openbsd
        guest_rt openbsd_illumos b omnios58 "$(P 13)" illumos
        nonroot openbsd_illumos b omnios58 "$(P 13)" illumos
        guest_sparse openbsd_illumos b omnios58 "$(P 13)" illumos ;;
    freebsd)
        i=10
        for g in freebsd15 freebsd15-ufs; do
            guest_rt freebsd "$g" "$g" "$(P $i)" freebsd
            nonroot freebsd "$g" "$g" "$(P $i)" freebsd
            guest_sparse freebsd "$g" "$g" "$(P $i)" freebsd
            i=$((i + 1))
        done ;;
    netbsd)
        guest_rt netbsd a netbsd11 "$(P 15)" netbsd
        nonroot netbsd a netbsd11 "$(P 15)" netbsd
        # netbsd11 has no cc or python3: its build image has cc.
        img=$SRCWORK/vms-bko-build/netbsd11-rust.qcow2
        if [[ -f $img ]]; then guest_sparse netbsd a netbsd11 "$(P 15)" netbsd "VM_IMAGE=$(q "$img")"
        else notes+=("no $img: sparse-rt.sh not run on NetBSD"); fi
        guest_rt netbsd b alpinebsd "$(P 16)" netbsd
        nonroot netbsd b alpinebsd "$(P 16)" netbsd ;;
    esac
done
[[ ${#S_NAME[@]} -gt 0 ]] || die "no steps (--only $only)"
roots=""
for i in "${!S_NAME[@]}"; do [[ -n ${S_ROOT[$i]} ]] && roots=1; done
if [[ -n $roots && -n $SUDO && -z $dry ]]; then
    sudo -n true 2>/dev/null || die "the linux and sparse steps need root or sudo -n"
fi
: "${BKO_MKFS_BCACHEFS:=}"
A=$SRCWORK/round6/arch-root
if [[ -z $BKO_MKFS_BCACHEFS && -x $A/usr/bin/bcachefs ]]; then
    # Chopin has no mkfs.bcachefs: the Arch ISO's, with its own libraries.
    BKO_MKFS_BCACHEFS="env GLIBC_TUNABLES=glibc.malloc.tcache_count=0 $A/usr/lib/ld-linux-x86-64.so.2 --library-path $A/usr/lib $A/usr/bin/bcachefs format"
fi
export BKO_MKFS_BCACHEFS

if [[ -n $dry ]]; then
    echo "== $host: ports $PORT_BASE+, at most $MAX_GUESTS guests (others counted: $COUNT_OTHERS, $GUEST_PORTS)," \
        "$HOST_JOBS host jobs, $SPARSE_JOBS sparse jobs; prefix $PFX; OLD_BIN ${OLD:-unset}"
    for i in "${!S_NAME[@]}"; do
        echo "-- ${S_CHAIN[$i]}: ${S_NAME[$i]} (${S_KIND[$i]}, guests ${S_GUESTS[$i]}, slot ${S_SEM[$i]}," \
            "timeout ${S_TO[$i]} s${S_ROOT[$i]:+, root}) -> $LOG/${S_PLAT[$i]}/${S_NAME[$i]}.log"
        sed 's/^ */    /' <<<"${S_CMD[$i]}" | grep -v '^ *$'
    done
    [[ ${#notes[@]} -eq 0 ]] || printf 'note: %s\n' "${notes[@]}"
    [[ ${#bad[@]} -eq 0 ]] || { echo "run-all: would refuse (above)"; exit 2; }
    exit 0
fi

# --- running ------------------------------------------------------------------
mkdir -p "$LOG" "$W/ram" "$LOG/.sem" "$LOG/rows" || die "can't make $LOG"
exec 8>"/tmp/run-all-$PFX.lock"
flock -n 8 || die "another run-all.sh with RUN_PREFIX=$PFX is running"
rm -f "$LOG"/rows/* "$LOG"/.sem/* "$LOG/rows.jsonl"
for d in vms vms-bko-build netbsd-tools; do
    [[ -e $SRCWORK/$d && ! -e $W/$d ]] && ln -s "$SRCWORK/$d" "$W/$d"
done
for os in $need_os; do
    if oldpair "$os"; then
        mkdir -p "$W/old/$os"
        ln -sf "$OLD/$os/restic-bko" "$W/old/$os/restic-bko-old"; ln -sf "$OLD/$os/rustic-bko" "$W/old/$os/rustic-bko-old"
    fi
done

tagged() {  # the PIDs with our BKO_RUNALL_ID (the scan itself has none)
    env -u BKO_RUNALL_ID ${roots:+$SUDO} sh -c 'for e in /proc/[0-9]*/environ; do
        grep -qzx "BKO_RUNALL_ID=$1" "$e" 2>/dev/null && { p=${e#/proc/}; echo "${p%/environ}"; }; done' - "$ID"
}
cleaned="" chain_pids=()
cleanup() {
    [[ -z $cleaned ]] || return; cleaned=1
    trap '' INT TERM
    local pids n
    # The chains first (forks of this shell, untagged), so that none starts
    # another step.
    [[ ${#chain_pids[@]} -eq 0 ]] || kill -TERM "${chain_pids[@]}" 2>/dev/null
    pids=$(tagged)
    if [[ -n $pids ]]; then
        echo "run-all: stopping $(wc -w <<<"$pids") processes of this run" >&2
        ${roots:+$SUDO} kill -TERM $pids 2>/dev/null
        # The scripts' EXIT traps power their guests off (up to a minute).
        for n in $(seq 45); do pids=$(tagged); [[ -n $pids ]] || break; sleep 2; done
        [[ -z $pids ]] || ${roots:+$SUDO} kill -KILL $pids 2>/dev/null
    fi
    if [[ -n $roots ]]; then
        $SUDO sh -c '
            P=$1
            zpool list -H -o name 2>/dev/null | grep "^$P-" | while read -r z; do zpool destroy -f "$z"; done
            findmnt -rn -o TARGET | grep -E "^/mnt/$P-(fs|sp)(/|\$)" | sort -r | while read -r m; do umount -l "$m"; done
            losetup -a 2>/dev/null | grep -E "\(/var/tmp/$P-(fs|sp)" | cut -d: -f1 | while read -r l; do losetup -d "$l"; done
            rm -rf /var/tmp/$P-fs-*.img /var/tmp/$P-sp /mnt/$P-fs /mnt/$P-sp' - "$PFX" 2>/dev/null
        # fs_roundtrip.py's repositories, of a step stopped before it removed them.
        [[ -n ${KEEP_WORK:-} ]] || $SUDO rm -rf "$here/../../fs-roundtrip/$PFX-fs"
    fi
    rm -rf "$W/ram"
}
trap cleanup EXIT
trap 'echo "run-all: interrupted" >&2; exit 130' INT TERM HUP

# A semaphore: NAME N MAX; guests also count others' QEMU on GUEST_PORTS.
others() {
    [[ $COUNT_OTHERS == 1 ]] || { echo 0; return; }
    local lo=${GUEST_PORTS%-*} hi=${GUEST_PORTS#*-} n=0 pid port
    for pid in $(pgrep qemu-system); do
        port=$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null | grep -oE 'hostfwd=tcp:127\.0\.0\.1:[0-9]+' | head -n 1)
        port=${port##*:}
        [[ -n $port && $port -ge $lo && $port -le $hi ]] || continue
        grep -qzx "BKO_RUNALL_ID=$ID" "/proc/$pid/environ" 2>/dev/null || n=$((n + 1))
    done
    echo $n
}
acquire() {
    local f=$LOG/.sem/$1
    until (flock 9; c=$(cat "$f" 2>/dev/null || echo 0); o=0
           [[ $1 == guests ]] && o=$(others)
           [[ $((c + o + $2)) -le $3 ]] || exit 1
           echo $((c + $2)) >"$f") 9>"$f.lock"; do
        sleep 10
    done
}
release() {
    local f=$LOG/.sem/$1
    (flock 9; c=$(cat "$f" 2>/dev/null || echo 0); echo $((c - $2)) >"$f") 9>"$f.lock"
}
max_of() { case $1 in guests) echo "$MAX_GUESTS" ;; host) echo "$HOST_JOBS" ;; sparse) echo "$SPARSE_JOBS" ;; esac; }

BOOTFAIL="didn't come up|QEMU exited|port [0-9]+ is in use"
run_step() {  # INDEX
    local i=$1 p=${S_PLAT[$1]} n=${S_NAME[$1]} log rc t0 wall try=1
    log=$LOG/$p/$n.log
    mkdir -p "$LOG/$p"
    [[ ${S_GUESTS[$i]} -gt 0 ]] && acquire guests "${S_GUESTS[$i]}" "$MAX_GUESTS"
    [[ ${S_SEM[$i]} != - ]] && acquire "${S_SEM[$i]}" 1 "$(max_of "${S_SEM[$i]}")"
    echo "$(date +%T) start ${S_CHAIN[$i]}: $n"
    t0=$SECONDS
    while :; do
        (cd "$LOG/$p" && exec timeout -k 120 "${S_TO[$i]}" bash -c "${S_CMD[$i]}") >"$log" 2>&1 </dev/null
        rc=$?
        # A guest that didn't come up: once more (round 9: ubuntu26-zfsmirror).
        if [[ ${S_GUESTS[$i]} -gt 0 && $try -lt 2 && $rc != 124 ]] && grep -qE "$BOOTFAIL" "$log"; then
            mv "$log" "$log.try$try"; try=$((try + 1)); sleep 30; continue
        fi
        break
    done
    wall=$((SECONDS - t0))
    [[ ${S_SEM[$i]} != - ]] && release "${S_SEM[$i]}" 1
    [[ ${S_GUESTS[$i]} -gt 0 ]] && release guests "${S_GUESTS[$i]}"
    # shellcheck disable=SC2086
    python3 "$C/verdict.py" judge "${S_KIND[$i]}" "$log" --platform "$p" --step "$n" --rc "$rc" --wall "$wall" \
        ${S_JARGS[$i]} >"$LOG/rows/$(printf %03d "$i").jsonl"
    echo "$(date +%T) done  ${S_CHAIN[$i]}: $n: exit $rc, $wall s, $(grep -c '"verdict": "FAIL"' "$LOG/rows/$(printf %03d "$i").jsonl") failed"
}
run_chain() {  # CHAIN
    local i
    for i in "${!S_NAME[@]}"; do [[ ${S_CHAIN[$i]} == "$1" ]] && run_step "$i"; done
}

t_start=$SECONDS
echo "== $host $(date -u '+%F %T UTC'): ${#S_NAME[@]} steps of $plats; logs in $LOG; run id $ID"
chains=$(printf '%s\n' "${S_CHAIN[@]}" | awk '!seen[$0]++')
for ch in $chains; do run_chain "$ch" & chain_pids+=($!); sleep 1; done
wait
chain_pids=()
cat "$LOG"/rows/*.jsonl >"$LOG/rows.jsonl" 2>/dev/null
js() { local s=${1//\\/\\\\}; s=${s//\"/\\\"}; printf '"%s"' "$s"; }
{
    printf '{"host": %s, "platforms": %s, "bin": %s, "old_bin": %s, "wall": %s, "id": %s,\n' \
        "$(js $host)" "$(js "$plats")" "$(js "$BIN")" "$(js "$OLD")" $((SECONDS - t_start)) "$(js "$ID")"
    printf ' "restic": %s, "restic_digest": %s, "rustic": %s, "rustic_core": %s, "notes": [' \
        "$(js "$seen_restic")" "$(js "$seen_digest")" "$(js "$seen_rustic")" "$(js "$seen_core")"
    sep=""; for n in "${notes[@]}"; do printf '%s%s' "$sep" "$(js "$n")"; sep=", "; done
    printf ']}\n'
} >"$LOG/meta.json"
python3 "$C/verdict.py" summary "$LOG"
