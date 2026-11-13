#!/usr/bin/env bash
# ht1-queue.sh RUN BASEPORT MAXJOBS [QHOST]
# Runs ht1.sh (the one beside this script) for the lines of QHOST's
# /bkoscratch/holttest1d/queue/RUN/todo (default: this host's), taken from the
# top; at most MAXJOBS at once on this host.  Run it as m, on each host that
# takes a share, every one with the same QHOST.
#   ht1-queue.sh --order GUEST[+VARIANT]...   the guests biggest first: the
#                                             order to write todo in
# A todo line is GUEST[+VARIANT][=D][%M], one per line; # starts a comment,
# and a bad line is moved to todo.bad.  Each job works in RAM under its own
# `cmp-run.sh --reserve D M`, D and M from ram-size.sh unless the line gives
# them (HT_RAM=0 here: it reserves no RAM disk and works on /bkoscratch).
# DISK_OVER=1 (the host table's holt_disk_mode: HoltTest2 sets it; default
# 0): with HT_RAM=1, a line whose D is over this host's ceiling (cmp-run.sh
# --ceiling) but whose M fits works on /bkoscratch as with HT_RAM=0 (HT_RAM=0
# and --reserve 0 M for that job alone, NVMe wear under its WEAR_TAG); the
# others stay in RAM, and so does a +img line (ht1.sh runs +img only in RAM:
# one over the ceiling is left for another host).
# Big guests (D of BIG_GIB or more, default 13) take turns: one HoltTest run
# per host, running or waiting, ours or not; so do a guest and its variants.
# Nothing new starts while a job of ours waits for its room, so ours wait in
# cmp-run.sh's line one at a time (every 30 min of it is logged, with the
# ledger).  BASEPORT is 2600: a job's port is a free one in 2600-2689
# (2690-2699 are for runs by hand).
# Nothing new starts while the CPU (k10temp Tctl, or coretemp's) is at 90 C or more, or less
# than MINFREE_GB (default 80; a failed run is moved there) is free on
# /bkoscratch; above 95 C every running job (its ht1 tree and its QEMU) is
# stopped (SIGSTOP) until it's back under 85 C, or until the runner ends
# (any way but kill -9: README "Stop and abort").  Runs that died in RAM are
# swept to /bkoscratch (ht1-lib.sh --sweep) at the start and every loop.
# Guests in ONLY_ELSEWHERE (space-separated), and redox on a host without
# ~/backotter-work/tools/redox, are skipped here and left queued.  So is,
# read again before each take, every line this host can never run: its D or
# M over this host's ceiling (cmp-run.sh --ceiling; with DISK_OVER=1 its M
# only, a D over it working on /bkoscratch, but not +img's); its guest's image
# missing here (~/backotter-work/vms/GUEST/*.qcow2, as the drivers take it:
# a small host's LRU cache holds some guests only); a guest of NEEDS_ZFS
# (space-separated; default Test2's classes.json holt.needs_host_zfs, the
# Linux guests with a ZFS pool, which ht1.sh's backup and verify mount here)
# on a host without OpenZFS's zpool (vm-mount's need; the module alone isn't
# enough), every variant, as Test2's placement keeps them off it; a
# Linux line (ht1.sh's, not +img) without the rescuer (HT_RESCUER_IMG's, else
# /bkoscratch/holttest1d/rescuer/rescuer.qcow2) or bin/'s f2fs-tools and
# jfsutils debs, a Redox or Sculpt one without the rescuer, and a BSD,
# illumos or Haiku guest whose rescuer guest (ht1-bsd.sh's RG: freebsd15-ufs,
# netbsd11, ...) has no image here.  A line taken
# that turns out so anyway (the todo changed, or cmp-run.sh refused its
# --reserve as one that can never fit here) goes back on top of the todo
# (--putback) instead of running, and never counts as a failed job; one that
# can't go back (no answer from the queue host, @STOPPED) stays in todo.taken
# with a done row (rc 1, run_s -), as a job's would.
# On this host: touch queue/RUN/stop to start nothing more, queue/RUN/abort
# to also end the running jobs (each keeps its state, as a FAIL does);
# queue/RUN/max-HOST holds a new MAXJOBS for HOST.  queue/RUN/done gets a row
# per job (end_utc host gv rc d_gib m_gib port wait_s run_s),
# queue/RUN/runner-HOST.log what the runner did, /bkoscratch/holttest1d/
# RUN-GV.log each job's output.  HT_HOLT, HT_HOLT_BIN, HT_BKO_BIN, HT_ROUND,
# HT_RUSTIC and HT_RAM reach ht1.sh (paths made absolute), and HT_WORK with
# HT_RECORDS (tests: /mnt/bko-ram/NAME and /bkoscratch/NAME); any other HT_
# one (HT_QUICK, HT_LIMIT_KIB and the like) refuses the start.  HoltTest2's
# T2_RUN, T2_CELLS and T2_HOST_ROOT reach the jobs too (their ledger rows:
# ht1-lib.sh).  If the binaries (rustic-bko's set, and HT_HOLT_BIN's
# SHA256SUMS when it has one) change during the run, nothing more starts and a job of ours still in line
# leaves it; nothing more starts either after 3 jobs in a row that
# failed within 60 s, or a line ram-size.sh can't size.
# (--pop is the queue host's half of taking a line, --putback of giving one
# back.)
# Exit 0 when done, stopped or aborted; 2 on bad usage.
set -uo pipefail

W=/home/m/backotter-work
HERE=$(dirname "$(realpath "$0")")
L=/bkoscratch/holttest1d
H=$(hostname -s)
# A todo line (README "Queues: ht1-queue.sh"); [+] is a plus in bash and awk alike.
LINE_RE='^[A-Za-z0-9][A-Za-z0-9_.-]*([+](bigger|oneshot|degraded|smaller|fitbigger|nearfull|refuse|img))?(=[0-9.]+)?(%[0-9.]+)?$'
NUM_RE='^[0-9]+([.][0-9]+)?$'
usage() { sed -n '2,/^set -uo/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2; exit 2; }
die() { echo "ht1-queue: $*" >&2; exit 2; }

# The queue host's half of a pop, under the todo's lock: the first good line
# that isn't in SKIP (by its name or its base guest, or as =LINE the line
# itself only), nor big while NOBIG is
# 1 (its =D, else its name in BIGNAMES; both lists comma-separated).  Prints
# it, else @SKIPPED when lines are left, else @EMPTY.  Bad lines go to
# todo.bad.  Exit 0, else nothing was taken.
pop_here() {  # Q HOST NOBIG BIG_GIB SKIP BIGNAMES
    [[ $# -eq 6 && $3 =~ ^[01]$ && $4 =~ $NUM_RE ]] || die "--pop Q HOST NOBIG BIG_GIB SKIP BIGNAMES"
    local q=$1 lk out ans nbad
    { exec {lk}>>"$q/todo.lock" && flock -w 60 "$lk"; } || return 1
    [[ -e $q/todo ]] || { echo @EMPTY; return 0; }
    rm -f "$q/todo.new" "$q/todo.bad.new"
    out=$(awk -v nobig="$3" -v big="$4" -v skip="$5" -v names="$6" -v re="$LINE_RE" -v num="$NUM_RE" \
            -v new="$q/todo.new" -v bad="$q/todo.bad.new" '
        BEGIN { n = split(skip, a, ","); for (i = 1; i <= n; i++) sk[a[i]] = 1
                n = split(names, a, ","); for (i = 1; i <= n; i++) bn[a[i]] = 1 }
        { s = $0; sub(/#.*/, "", s); gsub(/^[ \t\r]+|[ \t\r]+$/, "", s)
          if (s == "") { print > new; next }                    # a comment or a blank line
          nm = s; d = ""; m = ""
          if (match(nm, /%[0-9.]+$/)) { m = substr(nm, RSTART + 1); nm = substr(nm, 1, RSTART - 1) }
          if (match(nm, /=[0-9.]+$/)) { d = substr(nm, RSTART + 1); nm = substr(nm, 1, RSTART - 1) }
          if (s !~ re || (d != "" && d !~ num) || (m != "" && m !~ num)) { print > bad; nbad++; next }
          g = nm; sub(/[+].*/, "", g)
          if (!got && !(s in sk || ("=" s) in sk || nm in sk || g in sk) && !(nobig + 0 && (d != "" ? d + 0 >= big + 0 : nm in bn))) {
              got = 1; ans = s; next }
          left++; print > new }
        END { print (got ? ans : left ? "@SKIPPED" : "@EMPTY"), nbad + 0 }' "$q/todo") ||
        { rm -f "$q/todo.new" "$q/todo.bad.new"; return 1; }
    read -r ans nbad <<<"$out"
    if [[ $ans != @* || $nbad != 0 ]]; then   # todo changes
        [[ -e $q/todo.new ]] || : >"$q/todo.new"
        mv -f "$q/todo.new" "$q/todo" || { rm -f "$q/todo.new" "$q/todo.bad.new"; return 1; }
        [[ -e $q/todo.bad.new ]] && cat "$q/todo.bad.new" >>"$q/todo.bad"
        [[ $ans == @* ]] || echo "$ans $2" >>"$q/todo.taken"
    fi
    rm -f "$q/todo.new" "$q/todo.bad.new"
    echo "$ans"
}

# The queue host's half of giving a taken line back, under the todo's lock:
# LINE goes on top of the todo, and its last todo.taken row (LINE HOST) goes.
# Not a line whose guest has lines in todo.stopped (Test2's stop-os took them
# out: it stays taken).  Prints @BACK, else @STOPPED; exit 0 when it's back.
putback_here() {  # Q LINE HOST
    [[ $# -eq 3 && $2 =~ $LINE_RE ]] || die "--putback Q LINE HOST"
    local q=$1 lk g=${2%%[+=%]*}
    { exec {lk}>>"$q/todo.lock" && flock -w 60 "$lk"; } || return 1
    if [[ -e $q/todo.stopped ]] && awk -v g="$g" '{ s = $0; sub(/#.*/, "", s); gsub(/^[ \t\r]+|[ \t\r]+$/, "", s)
            sub(/[+=%].*/, "", s); if (s == g) f = 1 } END { exit !f }' "$q/todo.stopped"; then
        echo @STOPPED; return 1
    fi
    rm -f "$q/todo.new" "$q/todo.taken.new"
    { printf '%s\n' "$2"; [[ ! -e $q/todo ]] || cat "$q/todo"; } >"$q/todo.new" && mv -f "$q/todo.new" "$q/todo" ||
        { rm -f "$q/todo.new"; return 1; }
    if [[ -e $q/todo.taken ]]; then
        awk -v r="$2 $3" '{ l[NR] = $0; if ($0 == r) k = NR } END { for (i = 1; i <= NR; i++) if (i != k) print l[i] }' \
            "$q/todo.taken" >"$q/todo.taken.new" && mv -f "$q/todo.taken.new" "$q/todo.taken"
        rm -f "$q/todo.taken.new"
    fi
    echo @BACK
}

order() {  # LINE...: biggest D first (a line's own =D, else ram-size.sh's)
    local l k d sz n i=0 names=()
    local -A dd=()
    [[ $# -gt 0 ]] || usage
    for l; do
        [[ $l =~ $LINE_RE ]] || die "not GUEST[+VARIANT][=D][%M]: $l"
        [[ $l == *=* ]] || names+=("${l%%[=%]*}")
    done
    if ((${#names[@]})); then
        sz=$("$HERE/ram-size.sh" "${names[@]}") || exit 2
        while read -r n d _; do dd[$n]=$d; done <<<"$sz"
    fi
    for l; do
        k=${l%%[=%]*}
        if [[ $l =~ =([0-9.]+) ]]; then d=${BASH_REMATCH[1]}; else d=${dd[$k]:-0}; fi
        printf '%s\t%s\t%s\n' "$d" "$((i++))" "$l"
    done | sort -t $'\t' -k1,1gr -k2,2n | cut -f3
}

case ${1:-} in
--pop) shift; pop_here "$@"; exit ;;
--putback) shift; putback_here "$@"; exit ;;
--order) shift; order "$@"; exit ;;
esac
[[ $# -eq 3 || $# -eq 4 ]] || usage
RUN=$1 BASE=$2 MAX=$3 QHOST=${4:-}
# RUN as ht1-lib.sh takes it; debug and debug-state are ht1-debug.sh's.
[[ $RUN =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "RUN: a letter or digit, then letters, digits, . _ -"
[[ $RUN != debug && $RUN != debug-state ]] || die "RUN $RUN is ht1-debug.sh's"
[[ $BASE == 2600 ]] || die "BASEPORT: 2600 (the queue's ports are 2600-2689; the others' are taken)"
[[ $MAX =~ ^[0-9]+$ ]] || die "MAXJOBS: a number"
MAX=$((10#$MAX))
MINFREE_GB=${MINFREE_GB:-80} BIG_GIB=${BIG_GIB:-13} RAM=${HT_RAM:-1} ONLY_ELSEWHERE=${ONLY_ELSEWHERE:-}
DISK_OVER=${DISK_OVER:-0}
[[ $DISK_OVER == [01] ]] || die "DISK_OVER: 0 (the default) or 1 (a line over the RAM disk works on /bkoscratch)"
[[ $MINFREE_GB =~ ^[0-9]+$ ]] || die "MINFREE_GB: a number of GB"
MINFREE_GB=$((10#$MINFREE_GB))
[[ $BIG_GIB =~ $NUM_RE ]] || die "BIG_GIB: a number of GiB"
[[ $RAM == [01] ]] || die "HT_RAM: 1 (in RAM, the default) or 0 (on /bkoscratch)"
# Any other HT_ setting (debugging, a test hook) would change every job's run
# (sudo -E passes them on).
for v in $(compgen -e); do
    case $v in
    HT_HOLT|HT_HOLT_BIN|HT_BKO_BIN|HT_ROUND|HT_RUSTIC|HT_RAM|HT_WORK|HT_RECORDS) ;;
    HT_*) die "$v is set: unset it (the queue's jobs are plain HoltTest1 runs)" ;;
    esac
done
((EUID)) || die "run it as m, not root: cmp-run.sh's lock files are m's"
# Paths as this runner and its jobs both see them (this works in $L, and
# cmp-run.sh in ~/backotter-work).
for v in HT_HOLT HT_HOLT_BIN HT_BKO_BIN HT_RUSTIC; do
    [[ -n ${!v:-} ]] || continue
    p=$(realpath -e -- "${!v}" 2>/dev/null) || die "$v=${!v}: no such file"
    export "$v=$p"
done
[[ -z ${HT_HOLT_BIN:-} || -d $HT_HOLT_BIN ]] || die "HT_HOLT_BIN=$HT_HOLT_BIN: not a directory (DIR/bko-holt, DIR/PLATFORM/bko-holt)"
p=${CMP_RUN:-$W/cmp-run.sh}   # the shared ledger, always
CMP_RUN=$(realpath -e -- "$p" 2>/dev/null) || die "no $p"
# The sweep (as root) takes each WORK/X/Y whose run lock, named after
# RECORDS/X/Y, is free for a dead run: the two go as a pair, the defaults or
# a NAME of their own on both.
WORK=$(realpath -m -- "${HT_WORK:-/mnt/bko-ram/holttest1d}") RECS=$(realpath -m -- "${HT_RECORDS:-$L}")
n=${WORK#/mnt/bko-ram/}
[[ $n =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && $n != tmp && $WORK == "/mnt/bko-ram/$n" && $RECS == "/bkoscratch/$n" ]] ||
    die "HT_WORK $WORK and HT_RECORDS $RECS: /mnt/bko-ram/NAME and /bkoscratch/NAME, one NAME (holttest1d)"
[[ -z ${HT_WORK+set} ]] || export HT_WORK=$WORK
[[ -z ${HT_RECORDS+set} ]] || export HT_RECORDS=$RECS
mountpoint -q /mnt/bko-ram || die "no RAM disk at /mnt/bko-ram (cmp-run.sh needs it, with HT_RAM=0 too)"
for f in "$HERE/ht1.sh" "$HERE/ht1-lib.sh" "$HERE/ram-size.sh" "$CMP_RUN"; do [[ -x $f ]] || die "no $f"; done
# The binaries every job restores with (ht1-lib.sh's ht_rustic_take), pinned.
if [[ -n ${HT_RUSTIC:-} ]]; then
    binfile=$HT_RUSTIC bins="HT_RUSTIC $HT_RUSTIC, no set"
else
    bset=${HT_BKO_BIN:-$W/bko-bin}; [[ -n ${HT_ROUND:-} ]] && bset=$W/round$HT_ROUND/bin
    binfile=$bset/SHA256SUMS
    bins="$bset: $(head -1 "$bset/BUILD-INFO" 2>/dev/null || echo "${HT_ROUND:+round $HT_ROUND}")"
fi
# HT_HOLT_BIN's SHA256SUMS too, when it has one (HoltTest2's builds do).
hsums=""; [[ -z ${HT_HOLT_BIN:-} || ! -f $HT_HOLT_BIN/SHA256SUMS ]] || hsums=$HT_HOLT_BIN/SHA256SUMS
binsum() { [[ -r $binfile ]] || return 0; cat -- "$binfile" ${hsums:+"$hsums"} 2>/dev/null | sha256sum | cut -c1-64; }
BINSUM=$(binsum); [[ -n $BINSUM ]] || die "no $binfile: every job would refuse"
bignames() { "$HERE/ram-size.sh" --host "$H" --big "$BIG_GIB" | paste -sd, -; }
BIGNAMES=$(bignames) || die "ram-size.sh --big $BIG_GIB failed"
read -ra skip <<<"$ONLY_ELSEWHERE"
[[ -x $W/tools/redox/bin/redoxfs ]] || skip+=(redox)   # its tools are on Cinqcent only
# What this host can never run (the header): CANT, SKIP entries made again
# from the todo before each take (cant_scan), and REFUSED, the lines
# cmp-run.sh refused here as never fitting (for the run).  A missing image
# skips the guest (each of its lines), anything else the line only (=LINE).
zfs_guests() {  # classes.json's holt.needs_host_zfs, space-separated (none when it can't be read)
    python3 -I -B -c 'import json, sys; print(" ".join(json.load(open(sys.argv[1]))["holt"].get("needs_host_zfs") or []))' \
        "$(dirname "$HERE")/test2/classes.json" 2>/dev/null
}
NEEDS_ZFS=${NEEDS_ZFS-$(zfs_guests)}
read -ra zfs_list <<<"$NEEDS_ZFS"
HOSTZFS=0   # zpool where root's PATH has it (facts_host.py's caps.zfs)
for p in /usr/sbin/zpool /sbin/zpool /usr/local/sbin/zpool /usr/bin/zpool /bin/zpool; do [[ -f $p && -x $p ]] && HOSTZFS=1; done
((HOSTZFS)) || ! command -v zpool >/dev/null 2>&1 || HOSTZFS=1
CANT=() REFUSED=() CANTWHY="" CANTSAID="" CEIL="" WHYNOT=""
has_image() { local f; for f in "$W/vms/$1/"*.qcow2; do [[ -f $f ]] && return 0; done; return 1; }   # GUEST
needs_zfs() { local z; for z in "${zfs_list[@]}"; do [[ $z == "$1" ]] && return 0; done; return 1; }   # GUEST
has_debs() { compgen -G "$L/bin/f2fs-tools_*.deb" >/dev/null && compgen -G "$L/bin/jfsutils_*.deb" >/dev/null; }
# cmp-run.sh's ceiling(), "DISK MEM" (CEIL), or "" when it can't say: then no
# line is held to it here (cmp-run.sh still refuses one over it).
ceil_now() { CEIL=$("$CMP_RUN" --ceiling 2>/dev/null) && [[ $CEIL =~ ^[0-9.]+\ -?[0-9.]+$ ]] || CEIL=""; }
over_ceil() { [[ -n $CEIL && -n $1 && -n $2 ]] && ! fits_ceil "$1" "$2"; }   # D M
fits_ceil() { awk -v c="$CEIL" -v d="$1" -v m="$2" 'BEGIN { split(c, x, " "); exit !(d + 0 <= x[1] + 0 && m + 0 <= x[2] + 0) }'; }
# Why this host can never run a line (WHYNOT, rc 0), else rc 1.  OVER: 1 or 0
# when the ceiling was checked already, else it is checked here.
cant_why() {  # NM GUEST VARIANT D M [OVER]
    local g=$2 v=$3 drv=linux rg="" ri
    case $g in
    freebsd*|hardenedbsd*|ghostbsd*|netbsd*|alpinebsd*|openbsd*|dragonfly*|omnios*|haiku*) drv=bsd ;;   # ht1.sh's drivers
    redox|sculpt*) drv=offline ;;
    esac
    # ht1-bsd.sh's rescuer guest (its RG), booted from the same vms/ (+img's backup and verify too)
    case $g in
    freebsd*|hardenedbsd*|ghostbsd*) rg=freebsd15-ufs ;;
    netbsd*|alpinebsd*) rg=netbsd11 ;;
    dragonfly*) rg=dragonfly6 ;;
    openbsd*) rg=openbsd8 ;;
    omnios*) rg=omnios58 ;;
    haiku*) rg=haiku6 ;;
    esac
    ri=${HT_RESCUER_IMG:-$L/rescuer/rescuer.qcow2}   # ht1.sh's and ht1-offline.sh's rescuer
    WHYNOT=""
    if ! has_image "$g"; then WHYNOT="no $W/vms/$g/*.qcow2 here"
    elif [[ -n $rg ]] && ! has_image "$rg"; then WHYNOT="no $W/vms/$rg/*.qcow2 here (rescuer of $g)"
    elif [[ $drv == offline || ($drv == linux && $v != img) ]] && [[ ! -f $ri ]]; then
        WHYNOT="no rescuer $ri here"
    elif [[ $drv == linux && $v != img ]] && ! has_debs; then WHYNOT="no f2fs-tools and jfsutils debs in $L/bin here"
    elif ((HOSTZFS == 0)) && needs_zfs "$g"; then
        WHYNOT="needs OpenZFS here (NEEDS_ZFS), and this host has no zpool"
    elif if [[ -n ${6:-} ]]; then ((${6})); else over_ceil "$4" "$5"; fi; then
        WHYNOT="--reserve $4 $5 over this host's ceiling $CEIL"
    fi
    [[ -n $WHYNOT ]]
}
# The todo as it is now (on QHOST, over ssh), for cant_scan, then a line END
# (after a newline: a last line may have none); exit 1
# when it can't be read (no todo: empty).
todo_text() {
    if [[ -z $QHOST ]]; then { [[ ! -e $Q/todo ]] || cat -- "$Q/todo"; } && printf '\nEND\n'; return; fi
    timeout 90 ssh -n -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=10 \
        -o ServerAliveCountMax=3 -o ControlMaster=no -o ControlPath=none "m@$QHOST" \
        "$(printf 'if [ -e %q ]; then cat -- %q || exit 1; fi; printf "\\nEND\\n"' "$Q/todo" "$Q/todo")" 2>/dev/null
}
cant_scan() {  # -> CANT and CANTWHY ("ENTRY (WHY); ..."), from the todo's good lines;
    # both as they were when the todo can't be read
    local l s nm g v d m n sd sm e over sz k=0 text
    local -a todo=() names=()
    local -A szd=() szm=() seen=() ov=()
    ceil_now
    text=$(close_locks; todo_text) && [[ $text == END || $text == *$'\n'END ]] || return 0   # (a child: the runner keeps the port locks)
    CANT=() CANTWHY=""
    while IFS= read -r l; do
        s=${l%%#*}; s=${s#"${s%%[![:space:]]*}"}; s=${s%"${s##*[![:space:]]}"}
        [[ $s =~ $LINE_RE ]] || continue
        todo+=("$s")
        [[ $s == *=*%* ]] || names+=("${s%%[=%]*}")
    done <<<"${text%END}"
    ((${#todo[@]})) || return 0
    if ((${#names[@]})) && sz=$("$HERE/ram-size.sh" --host "$H" $(printf '%s\n' "${names[@]}" | sort -u) 2>/dev/null); then
        while read -r n sd sm _; do szd[$n]=$sd szm[$n]=$sm; done <<<"$sz"
    fi
    # The ceiling, for every line at once (one awk).
    if [[ -n $CEIL ]]; then
        while read -r n over; do ov[$n]=$over; done < <(
            for s in "${todo[@]}"; do
                nm=${s%%[=%]*} d="" m=""
                [[ $s =~ =([0-9.]+) ]] && d=${BASH_REMATCH[1]}
                [[ $s =~ %([0-9.]+) ]] && m=${BASH_REMATCH[1]}
                d=${d:-${szd[$nm]:-}} m=${m:-${szm[$nm]:-}}; ((RAM)) || d=0
                [[ -n $d && -n $m ]] && echo "$((k++)) $d $m $([[ $nm == *+img ]] && echo 1 || echo 0)"
            done | awk -v c="$CEIL" -v dov="${DISK_OVER:-0}" 'BEGIN { split(c, x, " ") }
                { d = $2 + 0; if (dov + 0 && !($4 + 0) && d > x[1] + 0) d = 0   # DISK_OVER: on /bkoscratch, --reserve 0 M (not +img: ht1.sh runs it only in RAM)
                  print $1, ((d > x[1] + 0 || $3 + 0 > x[2] + 0) ? 1 : 0) }')
    fi
    k=0
    for s in "${todo[@]}"; do
        nm=${s%%[=%]*} g=${s%%[+=%]*} v="" d="" m=""
        [[ $nm == *+* ]] && v=${nm#*+}
        [[ $s =~ =([0-9.]+) ]] && d=${BASH_REMATCH[1]}
        [[ $s =~ %([0-9.]+) ]] && m=${BASH_REMATCH[1]}
        d=${d:-${szd[$nm]:-}} m=${m:-${szm[$nm]:-}}; ((RAM)) || d=0
        over=0; [[ -n $d && -n $m && -n $CEIL ]] && over=${ov[$((k++))]:-0}
        cant_why "$nm" "$g" "$v" "$d" "$m" "$over" || continue
        if [[ $WHYNOT == "no $W/vms/"* ]]; then e=$g; else e="=$s"; fi
        [[ -z ${seen[$e]:-} ]] || continue
        seen[$e]=1 CANT+=("$e") CANTWHY+="${e#=} ($WHYNOT); "
    done
}
Q=$L/queue/$RUN PORTS=/tmp/bko-ht1-ports
mkdir -p "$Q" "$PORTS" || die "can't make $Q or $PORTS"
cd "$L" || die "no $L"
LOG=$Q/runner-$H.log
say() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }
# The CPU's: k10temp's Tctl (AMD: all four hosts, the Ryzen 7 255s too), else
# coretemp's package (Intel) or zenpower's; the hottest when there are several.
temp() { local h n x t=0; for h in /sys/class/hwmon/hwmon*; do n=$(cat "$h/name" 2>/dev/null); case $n in k10temp|coretemp|zenpower) x=$(cat "$h/temp1_input" 2>/dev/null) && [[ $x =~ ^[0-9]+$ ]] && (( x / 1000 > t )) && t=$(( x / 1000 )) ;; esac; done; echo "$t"; }
freegb() { df -BG --output=avail "$L" | tail -1 | tr -dc 0-9; }
# PID and its descendants, less a vm-mount or vm-umount at work and what it
# runs: one cut short leaves the host's devices half set up, or a state no
# retry can take down; it finishes, then the driver's trap runs (ht1-lib.sh's
# ht_desc).
tree() { echo "$1"; local c; for c in $(pgrep -P "$1"); do mounting "$c" || tree "$c"; done; }
mounting() { tr '\0' ' ' 2>/dev/null <"/proc/$1/cmdline" | grep -qE '(^|/)vm-u?mount '; }
# The job's own QEMU, as ht1-lib.sh's ht_own takes them: its command line
# names R/ (a run's disks are all in R; a plain substring, not a pattern),
# and it forwards the job's PORT or has the job's TMPDIR=R/tmp in its
# environment (root's: read through sudo -n).  Never one started by hand on
# R's files (with HT_RAM=0, R is K on /bkoscratch).
qemu_of() {  # R PORT
    local p c
    [[ $1 == /?* ]] || return 0
    for p in $(pgrep -f '^qemu-system'); do
        c=$(tr '\0' ' ' 2>/dev/null <"/proc/$p/cmdline") && [[ $c == *"$1/"* ]] || continue
        if [[ ${2:-} =~ ^[0-9]+$ && $c == *":$2-:22"* ]] || sudo -n grep -qzxF -- "TMPDIR=$1/tmp" "/proc/$p/environ" 2>/dev/null; then
            echo "$p"
        fi
    done
    return 0
}
qemu_on() { pgrep -f "^qemu-system.*:$1-:22" || true; }   # anyone's on PORT: ht1-lib.sh's port check (ht_require)
# A HoltTest driver at work (R/.ht/pid's PID; PIDs get reused).
alive() { [[ ${1:-} =~ ^[0-9]+$ ]] && tr '\0' ' ' 2>/dev/null <"/proc/$1/cmdline" | grep -qE 'ht1(-bsd|-offline)?[.]sh '; }
in_ledger() { awk -v p="$1" '$1 == p { f = 1 } END { exit !f }' /mnt/bko-ram/.reserve 2>/dev/null; }
# The heat pause's processes, parents first: a running job's driver's
# descendants and its QEMU.  Never a sudo nor what is under one, the driver
# included (its parent is the inner sudo): a sudo whose command stops stops
# itself, and wakes only if CONT reaches it after its command.
kids() { local c; for c in $(pgrep -P "$1"); do [[ $(cat "/proc/$c/comm" 2>/dev/null) == sudo ]] || { echo "$c"; kids "$c"; }; done; }
held() {  # SLOT: nothing while it is in line or its driver hasn't begun
    local r d
    [[ -n ${TA[$1]} ]] || return 0
    r=$(rdir "${GVS[$1]}" "${JR[$1]:-$RAM}")
    d=$(cat "$r/.ht/pid" 2>/dev/null) && alive "$d" || return 0
    { kids "$d"; qemu_of "$r" "${PORT_OF[$1]:-}"; } | awk '!s[$0]++'
}
hold() {  # STOP|CONT: every running job's (CONT children first)
    local s p
    for s in "${!JOB[@]}"; do
        if [[ $1 == CONT ]]; then mapfile -t p < <(held "$s" | tac); else mapfile -t p < <(held "$s"); fi
        ((${#p[@]} == 0)) || sudo -n kill -"$1" "${p[@]}" 2>/dev/null
    done
}
# A job of ours still in cmp-run.sh's line leaves it: its cmp-run.sh is
# killed under the ledger's lock, so never inside its locked part and never
# once it has its row.
unline() {  # SLOT WHY
    local lk rc=1
    exec {lk}>>/mnt/bko-ram/.reserve.lock || return 1
    flock -w 60 "$lk" && ! in_ledger "${JOB[$1]}" && kill "${JOB[$1]}" 2>/dev/null && rc=0
    exec {lk}>&-
    ((rc)) && return 1
    SIG[$1]=line; say "$2: slot $1's ${GVS[$1]} leaves cmp-run.sh's line (its line is in $Q/todo.taken${QHOST:+ on $QHOST})"
}
# HoltTest runs on this host, ours or not, as "BASE D": the ledger's and the
# line's rows for ht1.sh, the live drivers in WORK and RECORDS, and the runs
# left there under a live reservation (.ht/held: ht1-debug.sh's debug/GUEST
# between its steps) (D 0).
host_runs() {
    local f pid d rest a i r gv h
    for f in /mnt/bko-ram/.reserve /mnt/bko-ram/.reserve.wait; do
        while read -r pid d _ _ rest; do
            [[ $pid =~ ^[0-9]+$ && -d /proc/$pid ]] || continue
            read -ra a <<<"$rest"
            for ((i = 0; i + 1 < ${#a[@]}; i++)); do
                [[ ${a[i]} == ht1.sh || ${a[i]} == */ht1.sh ]] && { echo "${a[i + 1]} $d"; break; }
            done
        done <"$f"
    done 2>/dev/null
    for r in "$WORK"/*/* "$RECS"/*/*; do
        if ! { [[ -e $r/.ht/pid ]] && alive "$(cat "$r/.ht/pid" 2>/dev/null)"; }; then
            h=$(cat "$r/.ht/held" 2>/dev/null); [[ $h =~ ^[0-9]+$ && -d /proc/$h ]] || continue
        fi
        gv=${r##*/}; [[ $gv =~ ^(.*)-(bigger|oneshot|degraded|smaller|fitbigger|nearfull|refuse|img)$ ]] && gv=${BASH_REMATCH[1]}
        echo "$gv 0"
    done
}
ge() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a + 0 >= b + 0) }'; }
rdir() { if ((${2:-$RAM})); then echo "$WORK/$RUN/$1"; else echo "$RECS/$RUN/$1"; fi; }   # GV [RAM]: the job's R
waiting() { [[ $1 == "$WHY" ]] || { [[ -z $2 ]] || say "waiting: $2"; WHY=$1; }; }   # KEY TEXT: once per reason
sweep() {  # runs that died in RAM: their records to /bkoscratch, R deleted (as root)
    local out rc l
    out=$(sudo -n -E "$HERE/ht1-lib.sh" --sweep "$WORK" 2>&1); rc=$?
    if ((rc == 0)); then
        SWEPT=""; [[ -z $out ]] || while IFS= read -r l; do say "sweep: $l"; done <<<"$out"
    elif [[ "$rc $out" != "$SWEPT" ]]; then   # a failure, once until it changes
        SWEPT="$rc $out"; say "sweep failed (rc $rc): ${out//$'\n'/ | }"
    fi
}

where="in RAM"; ((RAM)) || where="on /bkoscratch (HT_RAM=0)"
((RAM && DISK_OVER)) && where+=", a line over this host's RAM-disk ceiling on /bkoscratch (DISK_OVER=1)"
say "start: run $RUN, ports $BASE-$((BASE + 89)), at most $MAX, queue ${QHOST:-here}, $HERE/ht1.sh, $where"
say "env: $(env | grep -E '^(HT_[A-Za-z0-9_]*|T2_[A-Za-z0-9_]*|BIG_GIB|MINFREE_GB|ONLY_ELSEWHERE|NEEDS_ZFS|CMP_RUN|DISK_OVER)=' | sort | tr '\n' ' ')"
say "binaries: $bins (sha256 ${BINSUM:0:12} of $binfile${hsums:+ and $hsums}); bko-holt ${HT_HOLT:-${HT_HOLT_BIN:-$L/bin}}"
say "big (D >= $BIG_GIB GiB; one at a time here): ${BIGNAMES//,/ }"
((${#skip[@]})) && say "left for other hosts: ${skip[*]}"
ceil_now
say "this host: ceiling ${CEIL:-not known (cmp-run.sh --ceiling)}, OpenZFS $( ((HOSTZFS)) && echo yes || echo "no (left for others: $NEEDS_ZFS)"), rescuer $([[ -f ${HT_RESCUER_IMG:-$L/rescuer/rescuer.qcow2} ]] && echo yes || echo no) (${HT_RESCUER_IMG:-$L/rescuer/rescuer.qcow2}), bin/ debs $(has_debs && echo yes || echo no)"
SWEPT=""; sweep
for d in "$WORK"/*/*; do
    [[ -d $d ]] || continue
    case ${d#"$WORK"/} in debug/*|debug-state/*) continue ;; esac   # ht1-debug.sh's
    p=$(cat "$d/.ht/pid" 2>/dev/null)
    alive "$p" && continue
    # (Left in RAM on purpose, HT_CLEAN=0 or HT_KEEP=1, while the reservation it names lives.)
    h=$(cat "$d/.ht/held" 2>/dev/null); [[ $h =~ ^[0-9]+$ && -d /proc/$h ]] && continue
    if [[ ! -d $d/.ht ]]; then why="no .ht: not ht1-lib.sh's"
    elif [[ -n $p ]]; then why="its pid $p is gone"
    elif [[ -n $h ]]; then why="the reservation in its .ht/held ($h) is gone"
    else why="no .ht/pid"; fi
    say "leftover in the RAM disk, left alone: $d ($(du -sh "$d" 2>/dev/null | cut -f1), $why)"
done
hr=$(host_runs)
[[ -z $hr ]] || say "HoltTest runs here already (guest D; their guests and the big ones wait): ${hr//$'\n'/, }"

declare -A JOB=() GVS=() BASEG=() PORT_OF=() PLK=() JD=() JM=() T0=() TA=() WARN=() SIG=() LINE_OF=() JR=()   # per slot
NEWPORT="" NEWLK="" WHY="" paused=0 newbig=0 changed=0 BROKE="" QUICK="" NQUICK=0
# A runner that ends (TERM, INT, HUP, or its own exit) resumes the jobs it
# paused: stopped, they would hold their reservations forever.  (A kill -9
# can't be caught: README "Stop and abort".)
trap '((paused == 0)) || { hold CONT; paused=0; say "resumed: the runner is exiting"; }' EXIT
trap 'exit 143' TERM; trap 'exit 130' INT; trap 'exit 129' HUP

close_locks() {  # [keep]: in a child, close the port locks' fds (keep: all but NEWLK's)
    local s fd
    for s in "${!PLK[@]}"; do fd=${PLK[$s]}; exec {fd}>&-; done
    [[ ${1:-} == keep || -z $NEWLK ]] || exec {NEWLK}>&-
}
free_port() {  # -> NEWPORT, locked (NEWLK) until its job ends: no job of ours, listener or QEMU on it
    local p s fd
    for ((p = BASE; p <= BASE + 89; p++)); do
        for s in "${!PORT_OF[@]}"; do [[ ${PORT_OF[$s]} == "$p" ]] && continue 2; done
        [[ -z $(ss -Hltn "sport = :$p" 2>/dev/null) ]] || continue
        [[ -z $(qemu_on "$p") ]] || continue
        exec {fd}>>"$PORTS/$p.lock" || continue
        if flock -n "$fd"; then NEWPORT=$p NEWLK=$fd; return 0; fi
        exec {fd}>&-
    done
    return 1
}
nobig() {  # RUNS ("BASE D" lines): 1 when a big one runs or waits here
    local s g d
    for s in "${!JOB[@]}"; do ge "${JD[$s]}" "$BIG_GIB" && { echo 1; return; }; done
    while read -r g d; do [[ -n $d ]] && ge "$d" "$BIG_GIB" && { echo 1; return; }; done <<<"$1"
    echo 0
}
in_line() { local s; for s in "${!JOB[@]}"; do [[ -n ${TA[$s]} ]] || { echo "$s"; return 0; }; done; return 1; }
pop() {  # NOBIG RUNS -> a todo line, @SKIPPED or @EMPTY; anything else is a failed pop
    local s g a=() sk=("${skip[@]}" "${CANT[@]}" "${REFUSED[@]}")
    # A guest and its variants: one at a time here, ours or not.
    for s in "${!BASEG[@]}"; do sk+=("${BASEG[$s]}"); done
    while read -r g _; do [[ -z $g ]] || sk+=("$g"); done <<<"$2"
    a=("$Q" "$H" "$1" "$BIG_GIB" "$(IFS=,; echo "${sk[*]}")" "$BIGNAMES")
    close_locks   # (in $(...): the runner keeps them)
    [[ -n $QHOST ]] || { pop_here "${a[@]}"; return; }
    timeout 90 ssh -n -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=10 \
        -o ServerAliveCountMax=3 -o ControlMaster=no -o ControlPath=none "m@$QHOST" \
        "$(printf '%q ' "$HERE/ht1-queue.sh" --pop "${a[@]}")"
}
launch() {  # LINE: its job in the lowest free slot, on NEWPORT
    local line=$1 nm d="" m="" g v="" sz="" sd sm s jram=$RAM
    nm=${line%%[=%]*} g=${line%%[+=%]*}
    [[ $nm == *+* ]] && v=${nm#*+}
    [[ $line =~ =([0-9.]+) ]] && d=${BASH_REMATCH[1]}
    [[ $line =~ %([0-9.]+) ]] && m=${BASH_REMATCH[1]}
    if [[ -z $d || -z $m ]]; then
        { sz=$("$HERE/ram-size.sh" --host "$H" "$nm") && read -r _ sd sm _ <<<"$sz" && [[ -n ${sm:-} ]]; } ||
            { BROKE="$line: not started, ram-size.sh failed (the line is in $Q/todo.taken${QHOST:+ on $QHOST})"
              say "$BROKE; starting nothing more"; return 1; }
        d=${d:-$sd} m=${m:-$sm}
    fi
    ((RAM)) || d=0
    # DISK_OVER: a line over the RAM disk works on /bkoscratch (cant_scan's CEIL),
    # but not +img (ht1.sh runs it only in the RAM disk): cant_why gives it back.
    if ((RAM && DISK_OVER)) && [[ $v != img ]] && [[ -n $CEIL ]] &&
            ! awk -v c="$CEIL" -v d="$d" 'BEGIN { split(c, x, " "); exit !(d + 0 <= x[1] + 0) }'; then
        d=0 jram=0
    fi
    # The backstop to cant_scan (the todo may have changed since): a line this
    # host can never run goes back to the others.
    # (Skipped here from now on, until the next look at the todo says otherwise.)
    if cant_why "$nm" "$g" "$v" "$d" "$m"; then
        if [[ $WHYNOT == "no $W/vms/"* ]]; then CANT+=("$g"); else CANT+=("=$line"); fi
        # One that stays taken gets its done row, as reap's refused one: else no host runs it and nothing says so.
        give_back "$line" "not started here: $WHYNOT" ||
            done_row "$g${v:+-$v}" 1 "$d" "$m" "$NEWPORT" 0 -
        return 1
    fi
    for ((s = 0; ; s++)); do [[ -n ${JOB[$s]:-} ]] || break; done
    GVS[$s]=$g${v:+-$v}
    # env and setsid exec in place (not a group leader): $! is cmp-run.sh's,
    # which becomes the ledger row's sudo.  The job keeps its own port's lock.
    ( close_locks keep
      exec env WEAR_TAG="ht1:$RUN:${GVS[$s]}" HT_RAM="$jram" HT_PORT="$NEWPORT" HT_VARIANT="$v" \
          setsid "$CMP_RUN" --reserve "$d" "$m" sudo -E "$HERE/ht1.sh" "$g" "$RUN"
    ) > "$L/$RUN-${GVS[$s]}.log" 2>&1 < /dev/null &
    JOB[$s]=$! BASEG[$s]=$g PORT_OF[$s]=$NEWPORT PLK[$s]=$NEWLK JD[$s]=$d JM[$s]=$m T0[$s]=$(date +%s) TA[$s]="" WARN[$s]="" SIG[$s]="" LINE_OF[$s]=$line JR[$s]=$jram
    say "slot $s: $line on port $NEWPORT (pid ${JOB[$s]}), --reserve $d $m${sz:+ (ram-size.sh: ${sz#* })}$( ((jram < RAM)) && echo ", on /bkoscratch (DISK_OVER: over the RAM-disk ceiling $CEIL)"), ${t} C, ${f} GB free"
}
# A taken line back on top of the todo (the queue host's --putback), with
# WHY in the log; rc 1 when it stays taken (in todo.taken).
give_back() {  # LINE WHY
    local out
    out=$( close_locks
           if [[ -z $QHOST ]]; then putback_here "$Q" "$1" "$H"; else
               timeout 90 ssh -n -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=10 \
                   -o ServerAliveCountMax=3 -o ControlMaster=no -o ControlPath=none "m@$QHOST" \
                   "$(printf '%q ' "$HERE/ht1-queue.sh" --putback "$Q" "$1" "$H")"
           fi )
    if [[ $out == @BACK ]]; then say "$1: $2; back on top of the todo, for the other hosts"; return 0; fi
    say "$1: $2; it couldn't go back to the todo (${out:-no answer}): it is in $Q/todo.taken${QHOST:+ on $QHOST}"
    return 1
}
done_row() {  # GV RC D M PORT WAIT RUN: a row of queue/RUN/done (its header first)
    [[ -s $Q/done ]] || printf 'end_utc\thost\tgv\trc\td_gib\tm_gib\tport\twait_s\trun_s\n' >> "$Q/done"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$H" "$@" >> "$Q/done"
}
reap() {  # a job whose PID is gone: its exit status to done, its port freed
    local s rc end w r fd refused
    for s in "${!JOB[@]}"; do
        [[ -e /proc/${JOB[$s]} ]] && continue
        wait "${JOB[$s]}"; rc=$? refused=0
        end=$(date +%s)
        if [[ -n ${TA[$s]} ]]; then w=$(( ${TA[$s]} - ${T0[$s]} )) r=$(( end - ${TA[$s]} )); else w=$(( end - ${T0[$s]} )) r=-; fi
        fd=${PLK[$s]}; exec {fd}>&-
        # cmp-run.sh refused its --reserve as one that can never fit here (the
        # ceiling moved since cant_why): not a job that ran, nor a failure.
        # Back to the todo, never again here; its done row only when it stays taken.
        if ((rc == 1)) && [[ -z ${TA[$s]} ]] && grep -q -- '^--reserve .* can never fit here' "$L/$RUN-${GVS[$s]}.log" 2>/dev/null; then
            REFUSED+=("=${LINE_OF[$s]}")
            if give_back "${LINE_OF[$s]}" "slot $s: cmp-run.sh refused --reserve ${JD[$s]} ${JM[$s]} here (can never fit; not a failure)"; then
                unset "JOB[$s]" "GVS[$s]" "BASEG[$s]" "PORT_OF[$s]" "PLK[$s]" "JD[$s]" "JM[$s]" "T0[$s]" "TA[$s]" "WARN[$s]" "SIG[$s]" "LINE_OF[$s]" "JR[$s]"
                newbig=1; continue
            fi
            refused=1   # (done's row, below; never a quick failure)
        fi
        done_row "${GVS[$s]}" "$rc" "${JD[$s]}" "${JM[$s]}" "${PORT_OF[$s]}" "$w" "$r"
        say "slot $s: ${GVS[$s]} done, exit $rc (waited $w s, ran $r s)"
        if ((refused)); then :   # a refused --reserve: no change to the quick failures
        elif ((rc && end - ${T0[$s]} < 60)); then QUICK+=" ${GVS[$s]} (exit $rc)"; ((++NQUICK)); else QUICK="" NQUICK=0; fi
        if ((NQUICK >= 3)) && [[ -z $BROKE ]]; then
            BROKE="$NQUICK jobs in a row failed within 60 s:$QUICK (see $L/$RUN-GV.log)"; say "$BROKE; starting nothing more"
        fi
        unset "JOB[$s]" "GVS[$s]" "BASEG[$s]" "PORT_OF[$s]" "PLK[$s]" "JD[$s]" "JM[$s]" "T0[$s]" "TA[$s]" "WARN[$s]" "SIG[$s]" "LINE_OF[$s]" "JR[$s]"
        newbig=1
    done
}
admitted() {  # a job whose PID has a ledger row has its room; one still in line is shown every 30 min
    local s now l
    now=$(date +%s)
    for s in "${!JOB[@]}"; do
        if [[ -n ${TA[$s]} ]]; then continue
        elif in_ledger "${JOB[$s]}"; then
            TA[$s]=$now; say "slot $s: ${GVS[$s]} has its room, after $(( now - ${T0[$s]} )) s"
        elif (( now - ${WARN[$s]:-${T0[$s]}} >= 1800 )); then
            WARN[$s]=$now
            say "slot $s: ${GVS[$s]} has waited $(( (now - ${T0[$s]}) / 60 )) min for --reserve ${JD[$s]} ${JM[$s]}; the ledger, its line and /mnt/bko-ram:"
            while IFS= read -r l; do say "  ledger: $l"; done < /mnt/bko-ram/.reserve
            while IFS= read -r l; do say "  line: $l"; done < /mnt/bko-ram/.reserve.wait
            say "  $(df -h /mnt/bko-ram | tail -1)"
        fi
    done 2>/dev/null
}
# Abort, once per job: one still in line just leaves it (unline); a
# running one gets SIGTERM to its driver (R/.ht/pid) and the driver's
# children, so that its foreground command returns and its trap keeps the
# state, and to its QEMU.  Not to iolog.sh, cmp-run.sh or the sudos: they
# pass TERM on, and the driver gets one only.
abort_jobs() {
    local s d r pids
    for s in "${!JOB[@]}"; do
        [[ -z ${SIG[$s]} ]] || continue
        r=$(rdir "${GVS[$s]}" "${JR[$s]:-$RAM}")
        if [[ -z ${TA[$s]} ]] && ! in_ledger "${JOB[$s]}"; then
            unline "$s" abort
        elif d=$(cat "$r/.ht/pid" 2>/dev/null) && alive "$d"; then
            mapfile -t pids < <(tree "$d"; qemu_of "$r" "${PORT_OF[$s]:-}")
            sudo -n kill -TERM "${pids[@]}" 2>/dev/null
            SIG[$s]=term; say "abort: SIGTERM to slot $s's ${GVS[$s]} (driver pid $d and ${#pids[@]} processes)"
        fi   # else its driver hasn't begun yet, or is ending: next time
    done
}

while :; do
    reap
    if ((newbig)); then   # a run's peaks can change who is big
        newbig=0
        b=$(bignames) && [[ $b != "$BIGNAMES" ]] && { BIGNAMES=$b; say "big now: ${b//,/ }"; }
    fi
    admitted
    sweep
    # queue/RUN/max-HOST (on this host), if present, overrides MAXJOBS.
    if [[ -s $Q/max-$H ]]; then m=$(tr -dc 0-9 < "$Q/max-$H"); [[ -z $m ]] || MAX=$((10#$m)); fi
    t=$(temp)
    # The binaries every job takes once admitted: checked every loop, and a
    # job of ours still in line leaves it when they change.
    if ((changed == 0)); then
        sum=$(binsum)
        if [[ $sum != "$BINSUM" ]]; then
            changed=1; say "the binaries changed during the run ($binfile: sha256 ${BINSUM:0:12}, now ${sum:0:12}); restart the queue to continue"
        fi
    fi
    if ((changed)); then for s in "${!JOB[@]}"; do [[ -n ${TA[$s]} || -n ${SIG[$s]} ]] || unline "$s" "the binaries changed"; done; fi
    if [[ -e $Q/abort ]]; then
        if ((paused)); then hold CONT; paused=0; say "resumed at ${t} C, for the abort"; fi
        abort_jobs
    elif (( paused == 0 && t >= 95 && ${#JOB[@]} > 0 )); then
        hold STOP; paused=1; say "paused at ${t} C"
    elif (( paused == 1 && t < 85 )); then
        hold CONT; paused=0; say "resumed at ${t} C"
    fi
    if ((paused)); then waiting paused ""
    elif [[ -e $Q/abort ]]; then waiting abort "abort: ending ${#JOB[@]} jobs, starting none"
    elif [[ -e $Q/stop ]]; then waiting stop "stop: starting nothing more, ${#JOB[@]} running"
    elif ((changed)); then waiting changed ""
    elif [[ -n $BROKE ]]; then waiting broke ""
    elif ((MAX == 0)); then waiting max "max 0 ($Q/max-$H)"
    elif ((${#JOB[@]} >= MAX)); then waiting full ""
    elif ((t >= 90)); then waiting hot "hot, ${t} C"
    elif f=$(freegb); ((f < MINFREE_GB)); then waiting space "${f} GB free on $L, under MINFREE_GB $MINFREE_GB"
    elif ws=$(in_line); then waiting line "slot $ws's ${GVS[$ws]} waits for its room in cmp-run.sh's line"
    elif ! free_port; then waiting port "no free port in $BASE-$((BASE + 89))"
    else
        hr=$(host_runs); nb=$(nobig "$hr")
        cant_scan   # (the LRU cache and the ceiling change: every time)
        if [[ $CANTWHY != "$CANTSAID" ]]; then
            CANTSAID=$CANTWHY; [[ -z $CANTWHY ]] || say "left for other hosts, never run here: ${CANTWHY%; }"
        fi
        out=$(pop "$nb" "$hr"); rc=$?
        if ((rc == 0)) && [[ $out =~ $LINE_RE ]]; then
            launch "$out" || exec {NEWLK}>&-
            WHY=""
        else
            exec {NEWLK}>&-
            if ((rc == 0)) && [[ $out == @EMPTY || $out == @SKIPPED ]]; then
                if ((${#JOB[@]} == 0)) && [[ $out == @EMPTY || -z $hr ]]; then
                    say "queue done$([[ $out == @SKIPPED ]] && echo ": what is left is for other hosts")"; break
                fi
                if [[ $out == @EMPTY ]]; then waiting empty "queue empty, ${#JOB[@]} running"
                else waiting skipped "only lines this host can't start now are left (big ones, or guests running here, or ones for other hosts); ${#JOB[@]} running"; fi
            else
                waiting pop "pop failed (rc $rc${out:+: ${out//$'\n'/ | }})${QHOST:+; a line taken but not run is in $Q/todo.taken on $QHOST}"
            fi
        fi
    fi
    if ((${#JOB[@]} == 0)); then
        if [[ -e $Q/abort ]]; then say aborted; break
        elif [[ -e $Q/stop ]]; then say stopped; break
        elif ((changed)); then say "stopped: the binaries changed"; break
        elif [[ -n $BROKE ]]; then say "stopped: $BROKE"; break; fi
    fi
    sleep 15
done
exit 0
