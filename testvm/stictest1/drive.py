#!/usr/bin/env python3
"""Run a SticTest1 round from the Mac: every guest's check.sh, from one queue
shared by the KVM test hosts.

Usage: drive.py [OPTIONS] ROUND [GUEST ...]

ROUND names the binaries: a check gets ~/backotter-work/roundROUND/bin/OS on
its host (stick/build-round.sh ROUND puts them on both hosts), OS as its
check.sh's usage names it ("BIN_DIR ... roundN/bin/OS" or "for OS"; linux
where it names none).  GUEST is a directory beside this script with a
check.sh; the default is every one of them but those --exclude names.

One queue for all hosts, longest guest first (by its newest earlier run that
passed, else the estimates in README.md, else 10 min, weighted by the VMs it
runs at once).  The moment a place frees, a host starts the first guest that
fits there now:
  - fewer than --slots checks of ours run there (freebsd15-ufs counts 3, its
    three VMs at once);
  - a guest slot is free on the host's range (Chopin 2700-2799, others
    2900-2999: the checks' slot_take 3), counting other sessions' slots and
    slotless QEMU there; sculpt2610 boots nothing and takes none;
  - the room cmp-run.sh would offer after its waiting line (budget() and
    after_line() of the host's ~/backotter-work/cmp-run.sh) holds the
    guest's check.sh --reserve figures, less what our started checks have
    not reserved yet;
  - Tctl (k10temp) is at most --hot-c (or the lower HOT_C of someone in
    cmp-run.sh's line) and no NVMe is at 75 C (cmp-run.sh's NVME_C) or more;
  - the host has the guest's binaries and free ports of ours (Chopin
    2780-2799, Cinqcent 2980-2999; freebsd15-ufs takes three in a row).
A guest that doesn't fit now is passed over for one that does.  Each check.sh
still takes its own guest slot and cmp-run.sh --reserve; this only avoids
starting one that would wait there.

Each start is detached on its host (ssh -n, setsid -f), so the checks run on
if the driver or the Mac goes away.  On the host, MARK being --logs-mark:
    ~/backotter-work/stictest1/GUEST/logs/MARK/run.out   the check's output
    .../run.out.start, .end, .exit, .port                 epoch seconds, status, ports
STC_LOGS is that directory and the check's PREFIX is MARK-GUEST.  A guest
with a run.out.start under MARK is never started again: run the same command
again to pick a round back up (after a Ctrl-C, say), and give each recheck a
mark of its own (and its own --out: a state.json there belongs to one mark).
Every --poll seconds one ssh per host reads the exit files (and, where a
place is free or one just freed, the room).  A started guest with no exit
file and no process of it two reads running is LOST (the host rebooted, or
it was killed), so the round still ends.  At the end, in
OUT: results.tsv (guest host exit pass fail limit seconds logdir), fails.txt
(the FAIL lines), and a summary; then it cleans up what is its own only: dead
rows naming MARK in cmp-run.sh's ledger and waiting line (under their lock),
and /mnt/bko-ram, /dev/shm and /bkoscratch entries named MARK-GUEST*.

Options:
  --exclude G,G   leave these guests out (repeatable)
  --first G,G     start these before the rest (a recheck's failed guests)
  --hosts H,H     chopin, cinqcent or both (default chopin,cinqcent)
  --slots N       checks of ours at once per host (default 3)
  --hot-c C       start nothing while Tctl is over C (default 90)
  --poll S        seconds between checks of the exit files (default 15)
  --logs-mark M   the logs' and scratch's mark (default st1-ROUND)
  --bin DIR       the round's binaries on the hosts
                  (default ~/backotter-work/roundROUND/bin)
  --history GLOB  logs directories on the hosts to time guests by (default
                  ~/backotter-work/stictest1/*/logs): each MARK/run.out.*
                  there; a guest's newest run that exited 0 counts
  --out DIR       here, the driver's log, state, results.tsv and fails.txt
                  (default ~/backotter-work/stictest1/MARK)
  --private-copy  first copy this checkout's committed testvm/stictest1 to
                  ~/backotter-work/st1-wt/testvm/stictest1 on each host (git
                  archive, rsync --delete) and run the checks from there;
                  refused while testvm/stictest1 has uncommitted changes or a
                  check runs from that copy.  Without it they run from
                  ~/backotter-work/testvm/stictest1 (testvm/sync-hosts.sh's).
  --dry-run       print the plan (each host's state now, guest order,
                  estimates, which host, when) and start nothing: it only
                  reads the hosts, over ssh, and writes nothing anywhere
  --self-test     check the queue logic, and this checkout's check.sh
                  parsing, without any host

Needs: python3 (stdlib only), ssh with keys for m@HOST.local; git, tar and
rsync for --private-copy.  On the hosts: bash, flock, pgrep, ss, md5sum, and
~/backotter-work/cmp-run.sh (testvm/sync-hosts.sh puts it there).

Examples:
  drive.py --dry-run --exclude haiku6 st3a
  drive.py --private-copy --exclude haiku6 st3a 2>&1 | tee st3a.out
  drive.py --logs-mark st1-st3b --first freebsd15-ufs st3a freebsd15-ufs
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
HOSTS = {   # ssh destination, the checks' guest-slot range there, our ports
    "chopin": dict(ssh="m@chopin.local", slot_range="2700-2799", ports=(2780, 2799)),
    "cinqcent": dict(ssh="m@cinqcent.local", slot_range="2900-2999", ports=(2980, 2999)),
}
GUEST_SLOTS = 3          # every check.sh's slot_take 3
NVME_C = 75              # cmp-run.sh's: it starts nothing over it
DEFAULT_EST = 600        # README: "Most guests finish in 2 to 15 minutes"
OS_WORDS = {"Linux": "linux", "FreeBSD": "freebsd", "NetBSD": "netbsd", "OpenBSD": "openbsd",
            "DragonFly": "dragonfly", "illumos": "omnios", "Haiku": "haiku"}
RESTIC_FROM = {"haiku": "linux"}   # haiku6 reads its repository with Linux's restic-bko
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
WT_PRIVATE = "backotter-work/st1-wt/testvm/stictest1"
WT_SYNCED = "backotter-work/testvm/stictest1"


# --- the guests -------------------------------------------------------------------

class Spec:
    """What a guest's check.sh needs: OS of BIN_DIR, --reserve DISK MEM, how
    many VMs it runs at once (n: each takes a slot, a reservation and a port),
    and whether it takes guest slots at all."""
    def __init__(self, os_, d, m, n=1, slotted=True):
        self.os, self.d, self.m, self.n = os_, d, m, n
        self.slots = n if slotted else 0
        self.load = n

    def __repr__(self):
        return f"Spec({self.os}, {self.d}/{self.m} x{self.n}, slots {self.slots})"


def parse_check(text):
    lines = text.splitlines()
    code = "\n".join(l for l in lines if not l.lstrip().startswith("#"))
    head = lines[:40]
    os_ = None
    for i, l in enumerate(head):
        m = re.search(r"roundN/bin/([a-z]+)", l)
        if m:
            os_ = m.group(1)
            break
    if os_ is None:
        for i, l in enumerate(head):
            if re.match(r"#\s+BIN_DIR\b", l):
                m = re.search(r"\bfor (" + "|".join(OS_WORDS) + r")\b", " ".join(head[i:i + 2]))
                if m:
                    os_ = OS_WORDS[m.group(1)]
                break
    os_ = os_ or "linux"
    r = re.search(r"--reserve\s+([0-9.]+)\s+(\S+)", code)
    if not r:
        raise ValueError("no cmp-run.sh --reserve")
    d, mt = float(r.group(1)), r.group(2)
    if re.fullmatch(r"[0-9.]+", mt):
        m = float(mt)
    else:   # ubuntu26-zfsmirror: the guest's MEMORY (MiB) / 1024 + 0.5
        near = code[r.start():r.start() + 300]
        mm = re.search(r"MEMORY:-([0-9]+)", near)
        if not mm or "/ 1024 + 0.5" not in near:
            raise ValueError(f"--reserve memory {mt!r} not understood")
        m = int(mm.group(1)) / 1024 + 0.5
    n = max(1, len(re.findall(r"^\s*admit\s+[A-Za-z]", code, re.M)))
    return Spec(os_, d, m, n, "slot_take" in code)


def all_guests():
    return sorted(g for g in os.listdir(HERE) if os.path.isfile(os.path.join(HERE, g, "check.sh")))


def load_specs(guests):
    spec = {}
    for g in guests:
        with open(os.path.join(HERE, g, "check.sh")) as f:
            try:
                spec[g] = parse_check(f.read())
            except ValueError as e:
                raise SystemExit(f"drive.py: {g}/check.sh: {e}")
    return spec


def readme_estimates():
    """README.md's "Estimates (seconds ...):" paragraph: GUEST SECONDS pairs."""
    try:
        with open(os.path.join(HERE, "README.md")) as f:
            text = f.read()
    except OSError:
        return {}
    m = re.search(r"^Estimates \(seconds[^\n]*?:\n(.*?)(?:\n\s*\n|\Z)", text, re.M | re.S)
    if not m:
        return {}
    return {g: int(s) for g, s in re.findall(r"([a-z][a-z0-9-]*) (\d+)\b", m.group(1))}


def estimates(guests, hist, readme):
    """hist: (guest, mark, start, end, exit) of the hosts' earlier runs."""
    best, failed = {}, {}
    for g, mark, s, e, x in hist:
        if g not in guests or s is None or e is None or e < s:
            continue
        if x == 0:
            if g not in best or s > best[g][0]:
                best[g] = (s, e - s, mark)
        elif g not in failed or s > failed[g][0]:
            failed[g] = (s, e - s, mark)
    est, src = {}, {}
    for g in guests:
        if g in best:
            est[g], src[g] = best[g][1], best[g][2]
        elif g in readme:
            est[g], src[g] = readme[g], "README"
        elif g in failed:   # a failed run may have stopped early: last resort
            est[g], src[g] = max(failed[g][1], 60), failed[g][2] + "(failed)"
        else:
            est[g], src[g] = DEFAULT_EST, "default"
    return est, src


# --- the queue (pure: the live loop, --dry-run's plan and --self-test share it) ----

def order(guests, spec, est, first=()):
    return sorted(guests, key=lambda g: (g not in first, -est[g] * max(spec[g].load, 1), g))


def has_bins(s, have):
    return f"{s.os}/rustic-bko" in have and f"{RESTIC_FROM.get(s.os, s.os)}/restic-bko" in have


def pick(cands, spec, have, free_cap, free_slots, room, ceil, ports_fn=None):
    """The first of cands (in queue order) that fits a host now: (guest, ports,
    why-not list).  room/ceil None: not known, not checked."""
    why = []
    for g in cands:
        s = spec[g]
        if not has_bins(s, have):
            why.append(f"{g}:nobin")
        elif ceil and (s.d > ceil[0] or s.m > ceil[1]):
            why.append(f"{g}:ceiling")
        elif s.load > free_cap:
            why.append(f"{g}:cap")
        elif s.slots > free_slots:
            why.append(f"{g}:slot")
        elif room and (s.n * s.d > room[0] + 1e-9 or s.n * s.m > room[1] + 1e-9):
            why.append(f"{g}:room")
        else:
            ports = ports_fn(g) if ports_fn else []
            if ports is None:
                why.append(f"{g}:port")
                continue
            return g, ports, why
    return None, None, why


def ports_for(k, lo, hi, busy, listen, used):
    """k ports in a row in lo..hi, none busy (ours running) or listening; ones
    never used this round first (no lingering forward from a finished guest)."""
    free = [list(range(p, p + k)) for p in range(lo, hi - k + 2)
            if not set(range(p, p + k)) & (busy | listen)]
    fresh = [b for b in free if not set(b) & used]
    return (fresh or free or [None])[0]


def simulate(guests, spec, est, hosts, cap, first=()):
    """The plan: hosts {name: dict(have, slots_free, room, ceil)} as of now
    (room None: unknown, not checked).  Returns rows (guest, host, start,
    end, waited-for-room) and the guests no host can run."""
    rem = order(guests, spec, est, first)
    run = {h: [] for h in hosts}        # (end, guest)
    rows, t = [], 0.0

    def busy_until(h):
        return sum((e - t) * spec[g].load for e, g in run[h]) / cap

    def try_start(relax):
        for h in sorted(hosts, key=lambda h: (busy_until(h), h)):
            v = hosts[h]
            free_cap = cap - sum(spec[g].load for _, g in run[h])
            free_slots = v["slots_free"] - sum(spec[g].slots for _, g in run[h])
            room = None
            if v["room"] is not None and not relax:
                room = (v["room"][0] - sum(spec[g].n * spec[g].d for _, g in run[h]),
                        v["room"][1] - sum(spec[g].n * spec[g].m for _, g in run[h]))
            g, _, _ = pick(rem, spec, v["have"], free_cap, free_slots, room, v["ceil"])
            if g:
                rem.remove(g)
                run[h].append((t + est[g], g))
                rows.append((g, h, t, t + est[g], relax))
                return True
        return False

    while rem or any(run.values()):
        while rem and try_start(False):
            pass
        if rem and not any(run.values()):   # nothing fits the room as it is now
            if not try_start(True):
                break
            continue
        if not any(run.values()):
            break
        t = min(e for h in run for e, _ in run[h])
        for h in run:
            run[h] = [(e, g) for e, g in run[h] if e > t]
    return rows, rem


# --- the hosts -------------------------------------------------------------------

def rq(p):
    """A host path for a remote shell: ~/x and x are under $HOME."""
    if p.startswith("/"):
        return shlex.quote(p)
    return '"$HOME"/' + shlex.quote(p[2:] if p.startswith("~/") else p)


def ssh(host, cmd, script=None, t=90):
    args = ["ssh", "-T" if script is not None else "-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
            "-o", "ServerAliveInterval=15", HOSTS[host]["ssh"], cmd]
    try:
        r = subprocess.run(args, input=script, capture_output=True, text=True, timeout=t)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return None, "", "timed out"


def run_script(host, script, *args, t=90):
    return ssh(host, "bash -s " + " ".join(shlex.quote(str(a)) for a in args), script, t)


PROBE_SH = r'''
set -u
BIN=$1 WT=$2 HIST=$3; shift 3
abs() { case $1 in /*) echo "$1" ;; "~/"*) echo "$HOME/${1#"~/"}" ;; *) echo "$HOME/$1" ;; esac; }
BIN=$(abs "$BIN") WT=$(abs "$WT") HIST=$(abs "$HIST")
for o in linux freebsd netbsd openbsd dragonfly omnios haiku; do
    for f in restic-bko rustic-bko; do [ -x "$BIN/$o/$f" ] && echo "BIN $o/$f"; done
done
if [ -d "$WT" ]; then
    (cd "$WT" && for g; do [ -d "$g" ] || continue
        find "$g" -type f ! -path '*/__pycache__/*' ! -name '*.pyc' ! -name .DS_Store -exec md5sum {} +; done) |
        sed 's/^/MD5 /'
else
    echo NOWT
fi
pgrep -f -- "$WT/" >/dev/null && echo BUSYWT
shopt -s nullglob
IFS=$'\n'
# shellcheck disable=SC2086  # HIST is a glob
for s in $HIST/*/run.out.start; do
    b=${s%.start} d=${s%/run.out.start}
    m=${d##*/} g=${d%/*}; g=${g%/*}; g=${g##*/}
    echo "HIST $g $m $(cat "$s" 2>/dev/null || echo -) $(cat "$b.end" 2>/dev/null || echo -) $(cat "$b.exit" 2>/dev/null || echo -)"
done
echo END
'''

# One snapshot of a host (read-only: never writes the ledger or the line).
#   MODE cheap: temperatures, our jobs' start/exit files, our waits
#   MODE full:  that, plus the ledger, cmp-run.sh's budget, the room left
#               after the waiting line and its ceiling, others' slots, QEMU
#               and the listening ports of ours
STATUS_SH = r'''
set -u
MODE=$1 MARK=$2 RANGE=$3 PLO=$4 PHI=$5; shift 5
W=$HOME/backotter-work R=/mnt/bko-ram
declare -A OURS; for g; do OURS[$g]=1; done
guest_of() { guest_of_ "$@" 2>/dev/null; }
guest_of_() {  # PID -> the guest of ours it (or an ancestor) belongs to, else fail
    local p=$1 i l v w g
    for i in $(seq 40); do
        [[ $p =~ ^[0-9]+$ && $p -gt 1 && -d /proc/$p ]] || return 1
        while IFS= read -r -d '' l; do
            [[ $l == STC_LOGS=* ]] || continue
            v=${l#STC_LOGS=}
            if [[ $v == "$W/stictest1/"*"/logs/$MARK" ]]; then
                g=${v#"$W/stictest1/"}; g=${g%%/*}
                [[ -n ${OURS[$g]:-} ]] && { echo "$g"; return 0; }
            fi
        done <"/proc/$p/environ"
        for w in $(tr '\0' '\n' <"/proc/$p/cmdline" | grep -oE -- "$MARK-[A-Za-z0-9_.-]+"); do
            g=${w#"$MARK-"}
            while [[ -n $g ]]; do
                [[ -n ${OURS[$g]:-} ]] && { echo "$g"; return 0; }
                [[ $g == *-* ]] || break
                g=${g%-*}
            done
        done
        p=$(awk '{ print $4 }' "/proc/$p/stat")
    done
    return 1
}

for h in /sys/class/hwmon/hwmon*; do
    case $(cat "$h/name" 2>/dev/null) in
    k10temp) echo "TEMP $(cat "$h/temp1_input")" ;;
    nvme) echo "NVME $(cat "$h/temp1_input")" ;;
    esac
done

for g; do   # GUEST START EXIT END PORTS ALIVE (1: a process of it runs; read only while no exit)
    b=$W/stictest1/$g/logs/$MARK/run.out
    [[ -e $b.start ]] || continue
    s=$(cat "$b.start" 2>/dev/null) x=$(cat "$b.exit" 2>/dev/null) e=$(cat "$b.end" 2>/dev/null)
    p=$(cat "$b.port" 2>/dev/null) a=-
    [[ $s =~ ^[0-9]+$ ]] || s=-; [[ $x =~ ^[0-9]+$ ]] || x=-; [[ $e =~ ^[0-9]+$ ]] || e=-
    [[ $p =~ ^[0-9,]+$ ]] || p=-
    if [[ $x == - ]]; then a=0; pgrep -f -- "$MARK-$g( |\$)" >/dev/null && a=1; fi
    echo "JOB $g $s $x $e $p $a"
done

if [[ -f $R/.reserve.wait ]]; then   # waiting in cmp-run.sh's line: PID OURGUEST(or -) WHAT
    while read -r pid rest; do
        [[ $pid =~ ^[0-9]+$ && -d /proc/$pid ]] || continue
        g=$(guest_of "$pid") || g=-
        echo "WAIT $pid $g $rest"
    done <$R/.reserve.wait
fi

for p in $(pgrep -x sleep); do   # our check.sh shells in slot_take's 10 s loop
    [[ $(tr '\0' ' ' <"/proc/$p/cmdline" 2>/dev/null) == "sleep 10 " ]] || continue
    pp=$(awk '{ print $4 }' "/proc/$p/stat" 2>/dev/null)
    g=$(guest_of "$pp") || continue
    [[ $(tr '\0' ' ' <"/proc/$pp/cmdline" 2>/dev/null) == *check.sh* ]] && echo "SLOTWAIT $g"
done

if [[ $MODE == full ]]; then
    if mountpoint -q $R && [[ -f $W/cmp-run.sh ]]; then
        tmp=$(mktemp -d)
        : >"$tmp/ledger"; : >"$tmp/waits"
        if [[ -f $R/.reserve ]]; then
            while read -r pid d m scope rest; do
                [[ $pid =~ ^[0-9]+$ && -d /proc/$pid ]] || continue
                echo "$pid $d $m $scope $rest" >>"$tmp/ledger"
                g=$(guest_of "$pid") || g=-
                echo "LEDGER $pid $d $m $g $rest"
            done <$R/.reserve
        fi
        if [[ -f $R/.reserve.wait ]]; then
            while read -r pid rest; do
                [[ $pid =~ ^[0-9]+$ && -d /proc/$pid ]] && echo "$pid $rest" >>"$tmp/waits"
            done <$R/.reserve.wait
        fi
        (
            # shellcheck source=/dev/null
            source <(sed -n '/^tree_anon()/,/^}/p; /^budget()/,/^}/p; /^after_line()/,/^}/p; /^ceiling()/,/^}/p' "$W/cmp-run.sh")
            RAM=$R LEDGER=$tmp/ledger WAITS=$tmp/waits
            if read -r bd bm < <(budget) && [[ -n ${bm:-} ]]; then
                echo "BUDGET $bd $bm"
                echo "ROOM $(after_line 0 "$bd" "$bm")"
            fi
            read -r cd cm < <(ceiling) && [[ -n ${cm:-} ]] && echo "CEIL $cd $cm"
        )
        rm -rf "$tmp"
    else
        echo "NORAM"
    fi

    d=/tmp/bko-guest-slots-$RANGE n=0   # slots held by others
    for f in "$d"/slot.*; do
        [[ -e $f ]] || continue
        exec {fd}>>"$f"
        if flock -n "$fd"; then flock -u "$fd"; exec {fd}>&-; continue; fi
        exec {fd}>&-
        mine=""
        for l in /proc/[0-9]*/fd/*; do
            [[ $(readlink "$l" 2>/dev/null) == "$f" ]] || continue
            p=${l#/proc/}; p=${p%%/*}
            mine=$(guest_of "$p") && break
        done
        if [[ -n $mine ]]; then echo "SLOT_OURS $mine $f"; else n=$((n + 1)); fi
    done
    echo "SLOTS_OTHER $n"

    for pid in $(pgrep qemu-system); do   # PID PORT SLOTFILE(or -) OURGUEST(or -)
        port=$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null | grep -oE 'hostfwd=tcp:127\.0\.0\.1:[0-9]+' | head -n 1)
        port=${port##*:}
        s=$(tr '\0' '\n' <"/proc/$pid/environ" 2>/dev/null | sed -n 's/^BKO_GUEST_SLOT=//p' | head -n 1)
        g=$(guest_of "$pid") || g=-
        echo "QEMU $pid ${port:--} ${s:--} $g"
    done

    ss -Hltn 2>/dev/null | awk -v lo="$PLO" -v hi="$PHI" '
        { n = split($4, a, ":"); p = a[n] + 0; if (p >= lo && p <= hi) print "LISTEN " p }' | sort -u
fi
echo END
'''

# Per guest a RES line (GUEST EXIT PASS FAIL LIMIT SECONDS LOGDIR) and its FAIL lines.
COLLECT_SH = r'''
set -u
MARK=$1; shift
W=$HOME/backotter-work
for g; do
    L=$W/stictest1/$g/logs/$MARK o=$W/stictest1/$g/logs/$MARK/run.out
    [[ -f $o.start ]] || continue
    s=$(cat "$o.start") e=$(cat "$o.end" 2>/dev/null || echo -) x=$(cat "$o.exit" 2>/dev/null || echo -)
    p=$(grep -cE '^PASS ' "$o") f=$(grep -cE '^FAIL ' "$o") l=$(grep -cE '^LIMIT ' "$o")
    d=$(grep -oE '^(logs|\(wall [0-9]+ s; logs) \S+' "$o" | tail -n 1 | awk '{ print $NF }' | tr -d ')')
    if [[ -z $d ]]; then   # the newest entry under STC_LOGS made since the start
        d=$(find "$L" -mindepth 1 -maxdepth 1 -type d -newermt "@$((s - 2))" -printf '%T@ %p\n' |
            sort -n | tail -n 1 | cut -d' ' -f2)
    fi
    secs=-; [[ $e != - ]] && secs=$((e - s))
    echo "RES $g $x $p $f $l $secs ${d:--}"
    grep -E '^FAIL ' "$o" | sed "s/^/FAILLINE $g /"
done
echo END
'''

# What is our own only: dead ledger/line rows naming MARK (a row is a line
# starting with a PID plus its continuation lines), and, once none of our
# checks runs, /mnt/bko-ram, /dev/shm and /bkoscratch entries MARK-GUEST*.
CLEAN_SH = r'''
set -u
MARK=$1; shift
R=${BKO_RAM:-/mnt/bko-ram}
live=""
for g; do pgrep -f -- "$MARK-$g" >/dev/null && live+=" $g"; done
[[ -n $live ]] && echo "LIVE$live"
if [[ -f $R/.reserve ]] && exec 9>>$R/.reserve.lock && flock -w 120 9; then
    for f in $R/.reserve $R/.reserve.wait; do
        [[ -f $f ]] || continue
        new=$f.drive-$$
        awk -v mark="$MARK" -v gl="$*" -v out="$new" '
            BEGIN { n = split(gl, G, " ") }
            function ours(b,  i) {
                if (index(b, "/logs/" mark "/") || index(b, "/logs/" mark " ")) return 1
                for (i = 1; i <= n; i++) if (index(b, mark "-" G[i])) return 1
                return 0 }
            function flush() {
                if (blk == "") return
                if (pid != "" && ours(blk) && system("test -d /proc/" pid) != 0) { print "DROP " FILENAME " " pid; dropped++ }
                else printf "%s", blk > out
                blk = "" }
            /^[0-9]+ / { flush(); pid = $1 }
            { blk = blk $0 "\n" }
            END { flush(); close(out); exit dropped ? 0 : 1 }' "$f" && touch "$new" && cat "$new" >"$f"
        rm -f "$new"
    done
    exec 9>&-
elif [[ -f $R/.reserve ]]; then
    echo "NOLOCK"
fi
if [[ -z $live ]]; then
    for base in $R /dev/shm /bkoscratch; do
        for d in "$base/$MARK-"*; do
            [[ -e $d ]] || continue
            n=${d##*/}; n=${n#"$MARK-"} ok=""
            for g; do case $n in "$g" | "$g"[-._]*) ok=1 ;; esac; done
            [[ -n $ok ]] || continue
            if grep -qE " $d(/| )" /proc/mounts; then echo "MOUNTED $d"; continue; fi
            rm -rf --one-file-system -- "$d" 2>/dev/null || sudo -n rm -rf --one-file-system -- "$d" 2>/dev/null
            if [[ -e $d ]]; then echo "KEPT $d"; else echo "REMOVED $d"; fi
        done
    done
fi
echo END
'''


def probe(host, bin_dir, wt, hist, guests):
    rc, out, err = run_script(host, PROBE_SH, bin_dir, wt, hist, *guests, t=120)
    if rc != 0 or "END" not in out:
        return None
    p = dict(have=set(), md5={}, nowt=False, busywt=False, hist=[])
    for line in out.splitlines():
        f = line.split()
        if not f:
            continue
        if f[0] == "BIN":
            p["have"].add(f[1])
        elif f[0] == "MD5" and len(f) >= 3:
            p["md5"][" ".join(f[2:])] = f[1]
        elif f[0] == "NOWT":
            p["nowt"] = True
        elif f[0] == "BUSYWT":
            p["busywt"] = True
        elif f[0] == "HIST" and len(f) == 6:
            num = lambda x: int(x) if x.lstrip("-").isdigit() else None
            p["hist"].append((f[1], f[2], num(f[3]), num(f[4]), num(f[5])))
    return p


def local_md5(guests):
    md5 = {}
    for g in guests:
        for dp, dns, fns in os.walk(os.path.join(HERE, g)):
            dns[:] = [d for d in dns if d != "__pycache__"]
            for fn in fns:
                if fn.endswith(".pyc") or fn == ".DS_Store":
                    continue
                p = os.path.join(dp, fn)
                with open(p, "rb") as f:
                    md5[os.path.relpath(p, HERE)] = hashlib.md5(f.read()).hexdigest()
    return md5


def copy_differs(local, remote, guests):
    """Guests whose files on the host differ from this checkout's."""
    out = {}
    for g in guests:
        keys = {k for k in local if k.startswith(g + "/")} | {k for k in remote if k.startswith(g + "/")}
        n = sum(1 for k in keys if local.get(k) != remote.get(k))
        if n:
            out[g] = n
    return out


def status(host, full, mark, guests):
    h = HOSTS[host]
    rc, out, err = run_script(host, STATUS_SH, "full" if full else "cheap", mark, h["slot_range"],
                              h["ports"][0], h["ports"][1], *guests)
    if rc != 0 or "END" not in out:
        return None
    s = dict(full=full, temp=None, nvme=[], budget=None, room=None, ceil=None, noram=False, ledger=[],
             waits=[], slots_other=0, slots_ours=[], qemu=[], listen=set(), jobs={}, slotwait=[])
    for line in out.splitlines():
        f = line.split()
        if not f:
            continue
        k = f[0]
        try:
            if k == "TEMP":
                s["temp"] = int(f[1]) / 1000
            elif k == "NVME":
                s["nvme"].append(int(f[1]) / 1000)
            elif k == "BUDGET":
                s["budget"] = (float(f[1]), float(f[2]))
            elif k == "ROOM":
                s["room"] = (float(f[1]), float(f[2]), int(f[3]), int(f[4]))
            elif k == "CEIL":
                s["ceil"] = (float(f[1]), float(f[2]))
            elif k == "NORAM":
                s["noram"] = True
            elif k == "LEDGER":
                s["ledger"].append(dict(pid=f[1], d=float(f[2]), m=float(f[3]), g=f[4], what=" ".join(f[5:])))
            elif k == "WAIT":
                s["waits"].append(dict(pid=f[1], g=f[2], what=" ".join(f[3:])))
            elif k == "SLOTWAIT":
                s["slotwait"].append(f[1])
            elif k == "SLOTS_OTHER":
                s["slots_other"] = int(f[1])
            elif k == "SLOT_OURS":
                s["slots_ours"].append((f[1], f[2]))
            elif k == "QEMU":
                s["qemu"].append(dict(pid=f[1], port=f[2], slot=f[3], g=f[4]))
            elif k == "LISTEN":
                s["listen"].add(int(f[1]))
            elif k == "JOB":
                num = lambda x: int(x) if x.isdigit() else None
                s["jobs"][f[1]] = dict(start=num(f[2]), exit=num(f[3]), end=num(f[4]),
                                       ports=[int(p) for p in f[5].split(",") if p.isdigit()],
                                       alive=f[6] != "0")
        except (IndexError, ValueError):
            continue
    return s


def hot_limit(a, snap):
    """--hot-c, or the lower heat limit of cmp-run.sh's waiting line (after_line's)."""
    r = snap["room"]
    return min(a.hot_c, r[3]) if r and r[3] > 0 else a.hot_c


def other_qemu(snap, host):
    """Other sessions' slotless QEMU on the host's slot range (slot_others)."""
    lo, hi = (int(x) for x in HOSTS[host]["slot_range"].split("-"))
    return sum(1 for q in snap["qemu"] if q["g"] == "-" and q["slot"] == "-" and q["port"].isdigit()
               and lo <= int(q["port"]) <= hi)


# --- the driver ----------------------------------------------------------------------

class Driver:
    def __init__(self, a, guests, spec, est, probes, wt):
        self.a, self.guests, self.spec, self.est, self.probes, self.wt = a, guests, spec, est, probes, wt
        self.hosts = a.hosts
        self.statef = os.path.join(a.out, "state.json")
        self.logf = open(os.path.join(a.out, "driver.log"), "a", buffering=1)
        if os.path.exists(self.statef):
            with open(self.statef) as f:
                self.state = json.load(f)
            if self.state.get("mark", a.logs_mark) != a.logs_mark:   # its jobs aren't this mark's
                raise SystemExit(f"drive.py: {self.statef} is mark {self.state['mark']}'s, not {a.logs_mark}'s:"
                                 " give this mark an --out of its own")
        else:
            self.state = dict(jobs={}, checks=0, temp_max={}, temp_pauses=0, started=time.time())
        self.state["mark"] = a.logs_mark
        for g in [g for g in self.state["jobs"] if g not in guests]:   # an earlier run's, with other guests
            del self.state["jobs"][g]
        self.seen = set()   # hosts read at least once: nothing starts before all are (no run twice)

    def log(self, *x):
        line = time.strftime("%H:%M:%S ") + " ".join(str(y) for y in x)
        self.logf.write(line + "\n")
        print(line, flush=True)

    def save(self):
        with open(self.statef + ".new", "w") as f:
            json.dump(self.state, f, indent=1)
        os.replace(self.statef + ".new", self.statef)

    def jobs(self):
        return self.state["jobs"]

    def remaining(self):
        return [g for g in self.guests if g not in self.jobs()]

    def running(self, host=None):
        return [g for g, j in self.jobs().items()
                if j.get("exit") is None and (host is None or j["host"] == host)]

    def new_job(self, host, ports, launched, **kw):
        return dict(host=host, ports=ports, launched=launched, exit=None, seen_ledger=0,
                    ledger_wait=0, slot_wait=0, slotfiles=[], **kw)

    def ports(self, host, g, snap):
        lo, hi = HOSTS[host]["ports"]
        mine = [j for j in self.jobs().values() if j["host"] == host]
        busy = {p for j in mine if j.get("exit") is None for p in j["ports"]}
        used = {p for j in mine for p in j["ports"]}
        return ports_for(self.spec[g].n, lo, hi, busy, snap["listen"], used)

    def launch(self, host, g, ports):
        s, a = self.spec[g], self.a
        L = f'"$HOME"/backotter-work/stictest1/{g}/logs/{a.logs_mark}'
        check = rq(f"{self.wt}/{g}/check.sh")
        binos = rq(f"{a.bin}/{s.os}")
        # The start file is made (noclobber: only once) before the job starts: a
        # second launch refuses (exit 3).  The port file lets a later driver
        # that adopts the job keep its ports out of others' way.
        cmd = (f'L={L}; mkdir -p "$L" && '
               f'{{ ( set -C; date +%s >"$L/run.out.start" ) 2>/dev/null || {{ [ -e "$L/run.out.start" ] && exit 3; exit 1; }}; }} && '
               f'echo {",".join(str(p) for p in ports)} >"$L/run.out.port" && '
               f'WEAR_TAG=stic:{a.logs_mark}:{g} STC_LOGS="$L" setsid -f bash -c \'"$0" "$@" >"$STC_LOGS/run.out" 2>&1; rc=$?; '
               f'date +%s >"$STC_LOGS/run.out.end"; echo $rc >"$STC_LOGS/run.out.exit"\' '
               f'{check} {binos} {ports[0]} {a.logs_mark}-{g} </dev/null >/dev/null 2>&1')
        rc, _, err = ssh(host, cmd, t=60)
        if rc == 3:
            self.jobs()[g] = self.new_job(host, ports, time.time(), adopted=True)
            self.log("launch:", g, "already has a start file on", host, "- adopting it, not starting it again")
            return True
        if rc != 0:
            # Did it start?  The start file says so; with the host unreachable it
            # stays unconfirmed, on this host only, until a status says either way.
            time.sleep(5)
            rc2, out, _ = ssh(host, f'cat {L}/run.out.start 2>/dev/null || echo NONE', t=60)
            if rc2 == 0 and out.strip() == "NONE":
                self.log("LAUNCH FAILED", host, g, "rc", rc, err.strip()[:200], "- will retry")
                return False
            if rc2 != 0:
                self.jobs()[g] = self.new_job(host, ports, time.time(), unconfirmed=True)
                self.log("launch of", g, "on", host, "unconfirmed (ssh rc", rc, rc2, ") - waiting for its status")
                return True
            self.log("launch ssh rc", rc, "but", g, "started on", host)
        self.jobs()[g] = self.new_job(host, ports, time.time())
        self.log("START", g, "on", host, "port", ports[0], "bin", s.os, "reserve", s.d, s.m, "x", s.n,
                 "est", self.est[g])
        return True

    def busy_until(self, host, now):
        t = sum(max(self.est[g] - (now - self.jobs()[g]["launched"]), 30) * self.spec[g].load
                for g in self.running(host))
        return t / self.a.slots

    def take_status(self, host, now, dt):
        a, spec = self.a, self.spec
        load = sum(spec[g].load for g in self.running(host))
        full = bool(self.remaining()) and load < a.slots
        snap = status(host, full, a.logs_mark, self.guests)
        if snap is not None and not full and self.remaining() and any(
                (snap["jobs"].get(g) or {}).get("exit") is not None for g in self.running(host)):
            full = True   # a place just freed: read the room now, not a check later
            snap = status(host, True, a.logs_mark, self.guests)
        if snap is None:
            self.log("status FAILED for", host)
            return None
        if snap["temp"] is not None:
            tm = self.state["temp_max"]
            tm[host] = max(tm.get(host, 0), snap["temp"])
        for g, jj in snap["jobs"].items():
            j = self.jobs().get(g)
            if j is None:   # started by an earlier driver that died before saving it
                self.jobs()[g] = self.new_job(host, jj["ports"], jj["start"] or now, adopted=True)
                self.log("ADOPT", g, "on", host, "(start file from",
                         time.strftime("%H:%M:%S", time.localtime(jj["start"] or now)) + ", ports", jj["ports"], ")")
            elif j["host"] != host:
                if not j.get("dup_warned"):
                    j["dup_warned"] = True
                    self.log("WARNING", g, "has a start file on", host, "too; following its run on", j["host"])
            elif jj["ports"] and j["ports"] != jj["ports"]:   # the run's own ports (an adopted one's)
                j["ports"] = jj["ports"]
        for g in list(self.jobs()):
            j = self.jobs()[g]
            if j["host"] != host or j.get("exit") is not None:
                continue
            jj = snap["jobs"].get(g)
            if j.get("unconfirmed"):
                if jj is None:
                    del self.jobs()[g]
                    self.log("launch of", g, "on", host, "never happened (no start file); queued again")
                    continue
                j.pop("unconfirmed")
                self.log("START", g, "on", host, "confirmed")
            if jj and jj["exit"] is not None:
                start = jj["start"] or j["launched"]
                j.update(exit=jj["exit"], start=start, end=jj["end"])
                self.log("DONE", g, "on", host, "exit", jj["exit"], "secs", (jj["end"] or start) - start,
                         "est", self.est[g])
                continue
            # No exit file and no process of it (or no start file) twice running:
            # the host rebooted or it was killed.  Lost, so the round can end.
            if jj is None or not jj["alive"]:
                j["gone"] = j.get("gone", 0) + 1
                if j["gone"] >= 2:
                    j.update(exit="lost", start=(jj and jj["start"]) or j["launched"], end=None)
                    self.log("LOST", g, "on", host, "- no exit file and nothing of it runs" if jj else "- no start file")
                    continue
            else:
                j["gone"] = 0
            if any(w["g"] == g for w in snap["waits"]):
                j["ledger_wait"] += dt
            if g in snap["slotwait"]:
                j["slot_wait"] += dt
            if full:
                j["seen_ledger"] = max(j["seen_ledger"], sum(1 for r in snap["ledger"] if r["g"] == g))
                for q in snap["qemu"]:
                    if q["g"] == g and q["slot"] != "-" and q["slot"] not in j["slotfiles"]:
                        j["slotfiles"].append(q["slot"])
                        ok = q["slot"].startswith(f"/tmp/bko-guest-slots-{HOSTS[host]['slot_range']}/")
                        self.log("SLOT", g, "on", host, "QEMU port", q["port"], "holds", q["slot"],
                                 "OK" if ok else "WRONG RANGE")
        waits = [w["g"] for w in snap["waits"] if w["g"] != "-"]
        other = [w["what"][:50] for w in snap["waits"] if w["g"] == "-"]
        self.log(f"{host}: T={snap['temp']} nvme={snap['nvme']} run={self.running(host)}"
                 + (f" budget={snap['budget']} room={snap['room'] and snap['room'][:2]}"
                    f" slots_other={snap['slots_other']}+{other_qemu(snap, host)}" if full else "")
                 + (f" waits_ours={waits}" if waits else "") + (f" waits_other={other}" if other else "")
                 + (f" slotwait={snap['slotwait']}" if snap["slotwait"] else ""))
        return snap

    def start_some(self, snaps, now):
        a, spec = self.a, self.spec
        tried = set()
        progress = True
        while progress and self.remaining():
            progress = False
            for host in sorted(snaps, key=lambda h: (self.busy_until(h, now), h)):
                snap = snaps[host]
                if host in tried or not snap["full"]:
                    continue
                hold, lim = None, hot_limit(a, snap)
                if snap["temp"] is not None and snap["temp"] > lim:
                    hold = f"TEMP PAUSE: Tctl {snap['temp']} C over {lim:g}"
                elif any(t >= NVME_C for t in snap["nvme"]):
                    hold = f"NVME PAUSE: {snap['nvme']} C"
                elif snap["room"] is None:
                    hold = "no room figures (no RAM disk or cmp-run.sh?): starting nothing"
                held = self.state.setdefault("held", {})
                if hold:
                    if held.get(host) != hold.split(":")[0]:
                        held[host] = hold.split(":")[0]
                        self.state["temp_pauses"] += hold.startswith("TEMP")
                        self.log(host, hold)
                    tried.add(host)
                    continue
                if held.pop(host, None):
                    self.log(host, "pause over")
                run = self.running(host)
                free_cap = a.slots - sum(spec[g].load for g in run)
                free_slots = GUEST_SLOTS - snap["slots_other"] - other_qemu(snap, host) - sum(spec[g].slots for g in run)
                room_d, room_m = snap["room"][0], snap["room"][1]
                for g in run:   # less what our checks will still reserve
                    k = spec[g].n - self.jobs()[g]["seen_ledger"]
                    if k > 0:
                        room_d -= k * spec[g].d
                        room_m -= k * spec[g].m
                cands = order(self.remaining(), spec, self.est, a.first)
                g, ports, why = pick(cands, spec, self.probes[host]["have"], free_cap, free_slots,
                                     (room_d, room_m), snap["ceil"], lambda g: self.ports(host, g, snap))
                if g is None:
                    if free_cap > 0:
                        key = str((free_cap, free_slots, round(room_d, 1), round(room_m, 1), why[:6]))
                        if self.state.setdefault("lastwhy", {}).get(host) != key:
                            self.state["lastwhy"][host] = key
                            self.log(host, "free", free_cap, "slots", free_slots, "room", round(room_d, 1),
                                     round(room_m, 1), "- nothing fits:", " ".join(why[:12]))
                    tried.add(host)
                    continue
                if self.launch(host, g, ports):
                    progress = True
                    snap["listen"] |= set(ports)
                else:
                    tried.add(host)

    def loop(self):
        a = self.a
        self.log("driver start: round", a.round, "mark", a.logs_mark, "hosts", ",".join(self.hosts),
                 "guests", len(self.guests), "checks from", self.wt)
        last = time.time()
        while True:
            self.state["checks"] += 1
            now = time.time()
            dt, last = now - last, now
            snaps = {}
            for host in self.hosts:
                snap = self.take_status(host, now, dt)
                if snap is not None:
                    snaps[host] = snap
                    self.seen.add(host)
            self.save()
            if not self.remaining() and not self.running():
                break
            if self.seen >= set(self.hosts):
                self.start_some(snaps, now)
            for g, j in self.jobs().items():
                if j.get("exit") is None and not j.get("alerted") and \
                        time.time() - j["launched"] > max(2.5 * self.est[g], 1800):
                    j["alerted"] = True
                    self.log("ALERT", g, "running", int(time.time() - j["launched"]), "s on", j["host"],
                             "(estimate", self.est[g], ")")
            self.save()
            time.sleep(a.poll)
        self.state["finished"] = time.time()
        self.save()

    def collect(self):
        a = self.a
        res, fails = [], []
        for host in self.hosts:
            mine = sorted(g for g, j in self.jobs().items() if j["host"] == host)
            if not mine:
                continue
            rc, out, err = run_script(host, COLLECT_SH, a.logs_mark, *mine, t=180)
            if rc != 0 or "END" not in out:
                self.log("collect FAILED on", host, err.strip()[:200])
            for line in out.splitlines():
                f = line.split()
                if f[:1] == ["RES"] and len(f) == 8:
                    res.append([f[1], host] + f[2:])
                elif f[:1] == ["FAILLINE"]:
                    fails.append(f"{host} {' '.join(f[1:])}")
        res.sort()
        with open(os.path.join(a.out, "results.tsv"), "w") as f:
            f.write("guest\thost\texit\tpass\tfail\tlimit\tseconds\tlogdir\n")
            for r in res:
                f.write("\t".join(r) + "\n")
        with open(os.path.join(a.out, "fails.txt"), "w") as f:
            f.write("".join(l + "\n" for l in fails))
        return res

    def summary(self, res):
        a = self.a
        bad = [r[0] for r in res if r[2] != "0" or r[4] != "0"]
        missing = [g for g in self.guests if g not in {r[0] for r in res}]
        lines = [f"SticTest1 round {a.round}, mark {a.logs_mark}: {len(res)} guests ran, "
                 f"{len(res) - len(bad)} pass" + (f", {len(bad)} fail: {' '.join(bad)}" if bad else "")
                 + (f"; no result for {' '.join(missing)}" if missing else "")]
        for host in self.hosts:
            js = {g: j for g, j in self.jobs().items() if j["host"] == host and j.get("end")}
            if not js:
                lines.append(f"  {host}: nothing ran")
                continue
            t0 = min(j.get("start", j["launched"]) for j in js.values())
            t1 = max(j["end"] for j in js.values())
            gs = sum((j["end"] - j.get("start", j["launched"])) * self.spec[g].load for g, j in js.items())
            waits = sum(j["ledger_wait"] + j["slot_wait"] for j in js.values())
            lines.append(f"  {host}: {len(js)} guests, busy {time.strftime('%H:%M:%S', time.localtime(t0))}-"
                         f"{time.strftime('%H:%M:%S', time.localtime(t1))} ({int(t1 - t0)} s), "
                         f"{int(gs)} VM-seconds ({gs / max(t1 - t0, 1):.2f} at once), waits {int(waits)} s, "
                         f"Tctl max {self.state['temp_max'].get(host, '?')}")
        lines.append(f"  slowest against its estimate: " + ", ".join(
            f"{r[0]} {r[6]}/{self.est[r[0]]} s" for r in sorted(
                (r for r in res if r[6].isdigit()), key=lambda r: -int(r[6]) / max(self.est[r[0]], 1))[:3]))
        lines.append(f"  results: {os.path.join(a.out, 'results.tsv')}; FAIL lines: {os.path.join(a.out, 'fails.txt')}")
        for l in lines:
            self.log(l)
        return not bad and not missing

    def cleanup(self):
        for host in self.hosts:
            rc, out, err = run_script(host, CLEAN_SH, self.a.logs_mark, *self.guests, t=180)
            got = [l for l in out.splitlines() if l and l != "END"]
            self.log(f"cleanup on {host}:", "; ".join(got) if got else "nothing of ours left",
                     "" if rc == 0 and "END" in out else f"(rc {rc} {err.strip()[:200]})")


# --- --private-copy ------------------------------------------------------------------

def private_copy(hosts, probes, dry):
    rel = os.path.relpath(HERE, REPO)
    st = subprocess.run(["git", "-C", REPO, "status", "--porcelain", "--", rel], capture_output=True, text=True)
    if st.returncode != 0:
        raise SystemExit(f"drive.py: git status failed: {st.stderr.strip()}")
    refuse = []
    if st.stdout.strip():
        refuse.append(f"{rel} has uncommitted changes:\n{st.stdout.rstrip()}")
    busy = [h for h in hosts if probes[h]["busywt"]]
    if busy:
        refuse.append(f"a check runs from ~/{WT_PRIVATE} on {', '.join(busy)}")
    if refuse:
        msg = "--private-copy: " + "; ".join(refuse)
        if not dry:
            raise SystemExit("drive.py: " + msg)
        print("it would refuse " + msg)
        return
    head = subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    if dry:
        print(f"--private-copy: would copy {rel} at {head} to ~/{WT_PRIVATE} on {', '.join(hosts)}")
        return
    with tempfile.TemporaryDirectory(prefix="st1-copy-") as tmp:
        arch = subprocess.Popen(["git", "-C", REPO, "archive", "HEAD", rel], stdout=subprocess.PIPE)
        subprocess.run(["tar", "-x", "-C", tmp], stdin=arch.stdout, check=True)
        if arch.wait() != 0:
            raise SystemExit("drive.py: git archive failed")
        for h in hosts:
            rc, _, err = ssh(h, f"mkdir -p {rq(WT_PRIVATE)}")
            r = subprocess.run(["rsync", "-rlp", "--checksum", "--delete", "--exclude", "__pycache__",
                                "--exclude", "*.pyc", "--exclude", ".DS_Store",
                                os.path.join(tmp, rel) + "/", f"{HOSTS[h]['ssh']}:{WT_PRIVATE}/"])
            if rc != 0 or r.returncode != 0:
                raise SystemExit(f"drive.py: --private-copy to {h} failed {err.strip()}")
            print(f"--private-copy: {rel} at {head} -> {h}:~/{WT_PRIVATE}")


# --- --dry-run -------------------------------------------------------------------------

def fmt_t(s):
    s = int(s)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def dry_run(a, guests, spec, est, src, probes, wt, unrunnable):
    hosts_v, hosts_now, marked = {}, {}, {}
    for h in a.hosts:
        snap = status(h, True, a.logs_mark, guests)
        p = probes[h]
        if snap is None:
            print(f"{h}: status FAILED")
            continue
        for g, jj in snap["jobs"].items():
            marked[g] = (h, jj)
        oq = other_qemu(snap, h)
        ours_ledger = [r for r in snap["ledger"] if r["g"] != "-"]
        oses = sorted({k.split("/")[0] for k in p["have"]})
        lim = hot_limit(a, snap)
        print(f"{h}: Tctl {snap['temp']} C (limit {lim:g}{', OVER: it would wait' if (snap['temp'] or 0) > lim else ''}), "
              f"NVMe {'/'.join(str(x) for x in snap['nvme'])} C")
        if snap["noram"]:
            print(f"  no /mnt/bko-ram or ~/backotter-work/cmp-run.sh: no room figures, it would start nothing")
        else:
            print(f"  cmp-run.sh budget {snap['budget']} GiB disk/mem, room after the line {snap['room'] and snap['room'][:2]}"
                  f" ({snap['room'][2] if snap['room'] else '?'} waiting), ceiling {snap['ceil']}; "
                  f"ledger rows {len(snap['ledger'])} ({len(ours_ledger)} ours)")
        print(f"  guest slots on {HOSTS[h]['slot_range']}: {snap['slots_other']} held by others, {oq} slotless QEMU of "
              f"others, {GUEST_SLOTS - snap['slots_other'] - oq} free of {GUEST_SLOTS}; ports {HOSTS[h]['ports'][0]}-"
              f"{HOSTS[h]['ports'][1]}: {len(snap['listen'])} listening")
        print(f"  binaries in {a.bin}: {' '.join(oses) or 'NONE'}")
        if p["nowt"]:
            print(f"  checks: ~/{wt} is missing" + (" (--private-copy would make it)" if a.private_copy else ""))
        else:
            diff = copy_differs(local_md5(guests), p["md5"], guests)
            print(f"  checks in ~/{wt}: " + ("same as this checkout for every guest" if not diff else
                  "DIFFER from this checkout for " + ", ".join(f"{g} ({n} files)" for g, n in sorted(diff.items()))
                  + (" (--private-copy would replace them)" if a.private_copy else "")))
        hosts_v[h] = dict(have=p["have"], slots_free=GUEST_SLOTS, room=snap["ceil"], ceil=snap["ceil"])
        hosts_now[h] = dict(have=p["have"], slots_free=GUEST_SLOTS - snap["slots_other"] - oq,
                            room=snap["room"][:2] if snap["room"] else (0, 0), ceil=snap["ceil"],
                            hot=(snap["temp"] or 0) > lim or any(t >= NVME_C for t in snap["nvme"]))
    if a.private_copy:
        private_copy(a.hosts, probes, True)
    counts = {}
    for g in guests:
        k = "README" if src[g] == "README" else "default" if src[g] == "default" else "history"
        counts[k] = counts.get(k, 0) + 1
    print(f"estimates: " + ", ".join(f"{n} from {k}" for k, n in sorted(counts.items())))
    todo = [g for g in guests if g not in marked and g not in unrunnable]
    if marked:
        print(f"already started under {a.logs_mark} (adopted, not run again): " + ", ".join(
            f"{g}@{h}{'' if jj['exit'] is None else ' exit ' + str(jj['exit'])}" for g, (h, jj) in sorted(marked.items())))
    rows, left = simulate(todo, spec, est, hosts_v, a.slots, a.first)
    print(f"plan for {len(todo)} guests, {a.slots} at once per host, on idle hosts (room: cmp-run.sh's ceiling; "
          f"w: over it; from: min:s after the first start):")
    print(f"  {'#':>2}  {'guest':<19} {'host':<9} {'bin':<10} {'reserve':<11} {'est':>5}  {'from':<6} {'source'}")
    for i, (g, h, t0, t1, w) in enumerate(sorted(rows, key=lambda r: (r[2], r[1])), 1):
        s = spec[g]
        print(f"  {i:>2}  {g:<19} {h:<9} {s.os:<10} {f'{s.d:g}/{s.m:g}' + (f' x{s.n}' if s.n > 1 else ''):<11} "
              f"{est[g]:>5}  {fmt_t(t0):>6}{'w' if w else ' '} {src[g]}")
    for h in a.hosts:
        mine = [r for r in rows if r[1] == h]
        if mine:
            gs = sum((r[3] - r[2]) * spec[r[0]].load for r in mine)
            end = max(r[3] for r in mine)
            print(f"  {h}: {len(mine)} guests, ends at {fmt_t(end)}, {gs / end:.2f} VMs at once")
    if left:
        print("  no host can run: " + ", ".join(left))
    for g, why in sorted(unrunnable.items()):
        print(f"  not runnable: {g} ({why})")
    rem = order(todo, spec, est, a.first)
    for h, v in hosts_now.items():   # what would start this minute
        started, why = [], []
        while not v["hot"]:
            free_cap = a.slots - sum(spec[g].load for g in started)
            free_slots = v["slots_free"] - sum(spec[g].slots for g in started)
            room = (v["room"][0] - sum(spec[g].n * spec[g].d for g in started),
                    v["room"][1] - sum(spec[g].n * spec[g].m for g in started))
            g, _, why = pick(rem, spec, v["have"], free_cap, free_slots, room, v["ceil"])
            if not g:
                break
            started.append(g)
            rem.remove(g)
        print(f"  now, {h} would start: " + (", ".join(started) or "nothing")
              + (" (too hot)" if v["hot"] else f" (then: room {v['room'][0]:g}/{v['room'][1]:g} GiB less theirs; "
                 + " ".join(why[:4]) + ")" if why else ""))
    print("--dry-run: nothing started, nothing written")


# --- --self-test ------------------------------------------------------------------------

def self_test():
    ok = True

    def check(cond, what):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + what)
        ok = ok and cond

    want_os = {"freebsd15": "freebsd", "freebsd15-ufs": "freebsd", "hardenedbsd15": "freebsd",
               "ghostbsd26": "freebsd", "ghostbsd26-zfs": "freebsd", "freebsd14": "freebsd",
               "freebsd15-geli": "freebsd", "netbsd11": "netbsd", "alpinebsd": "netbsd",
               "openbsd8": "openbsd", "dragonfly6": "dragonfly", "omnios58": "omnios", "haiku6": "haiku"}
    guests = all_guests()
    spec = load_specs(guests)
    for g in guests:
        w = want_os.get(g, "linux")
        if spec[g].os != w:
            check(False, f"{g}: BIN_DIR bin/{spec[g].os}, want bin/{w}")
    check(all(spec[g].os == want_os.get(g, "linux") for g in guests),
          f"BIN_DIR of {len(guests)} guests as their usage says (Linux guests and sculpt2610: bin/linux)")
    for g, d, m, n, sl in (("debian13-zfs", 13, 5, 1, 1), ("ubuntu26-zfsmirror", 9, 4.5, 1, 1),
                           ("freebsd15-ufs", 3, 6, 3, 3), ("sculpt2610", 2, 2, 1, 0), ("mint22", 2, 4, 1, 1)):
        if g in spec:
            s = spec[g]
            check((s.d, s.m, s.n, s.slots) == (d, m, n, sl), f"{g}: --reserve {s.d:g} {s.m:g} x{s.n}, slots {s.slots}")
    nums = set()
    for g in guests:
        with open(os.path.join(HERE, g, "check.sh")) as f:
            nums |= set(re.findall(r'(?:slot_take |slots="|SLOTMAX=)(\d+)', f.read()))
    check(nums <= {str(GUEST_SLOTS)}, f"every check takes slot_take {GUEST_SLOTS} ({sorted(nums)})")
    rd = readme_estimates()
    check(all(g in rd for g in guests if g != "haiku6"), f"README.md estimates for {len(rd)} guests")

    # The queue, on made-up guests.
    sp = dict(big=Spec("linux", 13, 6), long=Spec("linux", 3, 6), ufs=Spec("freebsd", 3, 6, 3),
              short=Spec("linux", 2, 2), bsd=Spec("netbsd", 3, 6), noslot=Spec("linux", 2, 2, 1, False))
    est = dict(big=900, long=800, ufs=400, short=100, bsd=300, noslot=50)
    o = order(list(sp), sp, est)
    check(o[0] == "ufs" and o[1:3] == ["big", "long"], f"longest first, weighted by VMs: {o}")
    check(order(list(sp), sp, est, {"short"})[0] == "short", "--first goes first")
    have = {"linux/restic-bko", "linux/rustic-bko", "freebsd/restic-bko", "freebsd/rustic-bko"}
    g, _, why = pick(o, sp, have, 3, 3, (10, 12), None)
    check(g == "long" and "ufs:room" in why and "big:room" in why, f"a guest that doesn't fit is passed over: {g} {why}")
    g, _, why = pick(["bsd", "short"], sp, have, 3, 3, (50, 50), None)
    check(g == "short" and "bsd:nobin" in why, "no binaries for its OS: not on this host")
    g, _, _ = pick(["ufs", "short"], sp, have, 2, 3, (50, 50), None)
    check(g == "short", "freebsd15-ufs-like (3 VMs) needs 3 free places")
    g, _, _ = pick(["long", "noslot"], sp, have, 1, 0, (50, 50), None)
    check(g == "noslot", "no free guest slot: only a guest that takes none")
    g, _, why = pick(["big"], sp, have, 3, 3, (50, 50), (12, 40))
    check(g is None and why == ["big:ceiling"], "over cmp-run.sh's ceiling: never on that host")
    g, p, why = pick(["long", "short"], sp, have, 3, 3, (50, 50), None, lambda g: None if g == "long" else [1])
    check(g == "short" and "long:port" in why, "no free port: the next one")
    check(ports_for(1, 2780, 2799, set(), set(), set()) == [2780], "first port")
    check(ports_for(1, 2780, 2799, {2781}, {2782}, {2780}) == [2783], "ports: not busy, listening or used before")
    check(ports_for(1, 2780, 2799, {2781}, set(), set(range(2780, 2800)) - {2790} | {2780}) == [2790],
          "ports: unused ones first")
    check(ports_for(1, 2780, 2780, set(), set(), {2780}) == [2780], "ports: a used one when no other is free")
    check(ports_for(3, 2780, 2799, {2781}, {2784}, set()) == [2785, 2786, 2787], "three in a row")
    check(ports_for(3, 2780, 2783, {2781}, set(), set()) is None, "none free")
    allhave = have | {"netbsd/restic-bko", "netbsd/rustic-bko"}
    hosts = dict(a=dict(have=allhave, slots_free=3, room=(26, 20), ceil=None),
                 b=dict(have=have, slots_free=3, room=(26, 20), ceil=None))
    rows, left = simulate(list(sp), sp, est, hosts, 3)
    check(not left and len(rows) == len(sp), f"plan places every guest: {[(r[0], r[1], int(r[2])) for r in rows]}")
    check(all(r[1] == "a" for r in rows if r[0] == "bsd"), "plan: a guest goes only where its binaries are")
    ok_cap = True
    for h in hosts:
        for t in sorted({r[2] for r in rows}):
            at = [r for r in rows if r[1] == h and r[2] <= t < r[3]]
            if sum(sp[r[0]].load for r in at) > 3 or any(r[4] for r in at) or \
                    sum(sp[r[0]].n * sp[r[0]].d for r in at) > 26 + 1e-9 or \
                    sum(sp[r[0]].n * sp[r[0]].m for r in at) > 20 + 1e-9:
                ok_cap = False
    check(ok_cap, "plan: never more than 3 places or the room on a host")
    hosts["a"]["room"] = hosts["b"]["room"] = (5, 5)
    rows, left = simulate(["big", "short"], sp, est, hosts, 3)
    check([r[0] for r in rows if r[4]] == ["big"] and not left, "plan: a guest over the room now waits (w), never lost")
    rows, left = simulate(["bsd"], sp, est, dict(b=hosts["b"]), 3)
    check(left == ["bsd"], "plan: no host with its binaries: left over")
    print("self-test:", "all ok" if ok else "FAILED")
    return 0 if ok else 1


# --- main --------------------------------------------------------------------------------

def main():
    if any(x in ("-h", "--help") for x in sys.argv[1:]):
        print(__doc__.strip())
        return 0
    ap = argparse.ArgumentParser(usage="drive.py [OPTIONS] ROUND [GUEST ...]   (--help: all of it)", add_help=False)
    ap.add_argument("round", nargs="?", metavar="ROUND", help="the round whose binaries the checks get")
    ap.add_argument("guests", nargs="*", metavar="GUEST", help="default: every guest here but --exclude's")
    ap.add_argument("--exclude", action="append", default=[], metavar="G,G", help="leave these guests out")
    ap.add_argument("--first", default="", metavar="G,G", help="start these before the rest")
    ap.add_argument("--hosts", default="chopin,cinqcent", metavar="H,H", help="default chopin,cinqcent")
    ap.add_argument("--slots", type=int, default=3, metavar="N", help="checks of ours at once per host (3)")
    ap.add_argument("--hot-c", type=float, default=90, metavar="C", help="start nothing while Tctl is over C (90)")
    ap.add_argument("--poll", type=float, default=15, metavar="S", help="seconds between checks (15)")
    ap.add_argument("--logs-mark", metavar="MARK", help="default st1-ROUND")
    ap.add_argument("--bin", metavar="DIR", help="on the hosts; default ~/backotter-work/roundROUND/bin")
    ap.add_argument("--history", default="~/backotter-work/stictest1/*/logs", metavar="GLOB",
                    help="earlier runs on the hosts to time guests by (default %(default)s)")
    ap.add_argument("--out", metavar="DIR", help="here; default ~/backotter-work/stictest1/MARK")
    ap.add_argument("--private-copy", action="store_true",
                    help=f"run the checks from a copy of this checkout's committed tree in ~/{WT_PRIVATE}")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; start and write nothing")
    ap.add_argument("--self-test", action="store_true", help="check the queue logic without hosts")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not a.round:
        ap.error("ROUND is needed")
    if not NAME.match(a.round):
        ap.error(f"bad ROUND {a.round!r}")
    a.logs_mark = a.logs_mark or f"st1-{a.round}"
    if not NAME.match(a.logs_mark):
        ap.error(f"bad --logs-mark {a.logs_mark!r}")
    a.bin = a.bin or f"~/backotter-work/round{a.round}/bin"
    a.out = os.path.expanduser(a.out or f"~/backotter-work/stictest1/{a.logs_mark}")
    a.hosts = [h for h in a.hosts.split(",") if h]
    for h in a.hosts:
        if h not in HOSTS:
            ap.error(f"unknown host {h} (known: {', '.join(HOSTS)})")
    if a.slots < 1 or a.poll < 1:
        ap.error("--slots and --poll must be at least 1")
    known = all_guests()
    excl = {g for e in a.exclude for g in e.split(",") if g}
    a.first = {g for g in a.first.split(",") if g}
    for g in excl | a.first | set(a.guests):
        if g not in known:
            ap.error(f"no guest {g} (no {g}/check.sh beside drive.py)")
    guests = [g for g in (a.guests or known) if g not in excl]
    if not guests:
        ap.error("no guests left")
    spec = load_specs(guests)
    wt = WT_PRIVATE if a.private_copy else WT_SYNCED

    probes = {}
    for h in a.hosts:
        probes[h] = probe(h, a.bin, wt, a.history, guests)
        if probes[h] is None:
            raise SystemExit(f"drive.py: cannot read {h} ({HOSTS[h]['ssh']})")
    est, src = estimates(set(guests), [x for p in probes.values() for x in p["hist"]], readme_estimates())
    unrunnable = {g: f"no host has {spec[g].os}/rustic-bko and {RESTIC_FROM.get(spec[g].os, spec[g].os)}/restic-bko in {a.bin}"
                  for g in guests if not any(has_bins(spec[g], probes[h]["have"]) for h in a.hosts)}
    if a.dry_run:
        print(f"drive.py --dry-run: round {a.round}, mark {a.logs_mark}, binaries {a.bin}, checks from ~/{wt}, "
              f"{len(guests)} guests" + (f" (excluded: {', '.join(sorted(excl))})" if excl else ""))
        dry_run(a, guests, spec, est, src, probes, wt, unrunnable)
        return 0
    if unrunnable:
        raise SystemExit("drive.py: " + "; ".join(f"{g}: {w}" for g, w in sorted(unrunnable.items()))
                         + f" (leave it out: --exclude {','.join(sorted(unrunnable))})")
    os.makedirs(a.out, exist_ok=True)
    lockf = open(os.path.join(a.out, "driver.lock"), "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit(f"drive.py: another driver runs on {a.out}")
    if a.private_copy:
        private_copy(a.hosts, probes, False)
        for h in a.hosts:
            probes[h] = probe(h, a.bin, wt, a.history, guests) or probes[h]
    drv = Driver(a, guests, spec, est, probes, wt)
    local = local_md5(guests)
    for h in a.hosts:
        diff = copy_differs(local, probes[h]["md5"], guests)
        if diff:
            drv.log(f"WARNING: {h}:~/{wt} differs from this checkout for", ", ".join(sorted(diff)))
    drv.log("estimates", {g: (est[g], src[g]) for g in order(guests, spec, est, a.first)})
    try:
        drv.loop()
    except KeyboardInterrupt:
        drv.save()
        drv.log("interrupted: the checks started keep running; the same command picks the round back up")
        return 130
    res = drv.collect()
    good = drv.summary(res)
    drv.cleanup()
    drv.log("all done")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
