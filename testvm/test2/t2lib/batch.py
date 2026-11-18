"""t2 batch: unattended batches the user starts and a session reads (docs/design/batch.md;
pieces B1 and the supervisor).  Impure: the batch dir, child processes, locks, signals.

    t2 batch FILE [plan] [--hosts H,H] [--prev BATCH] [--id NAME] [--json]
                  [--dirty-ok] [--allow-update] [--reparse]
    t2 batch resume BATCH          t2 batch status          t2 batch ls [--last N]
    t2 batch report BATCH [--prev BATCH] [--json]     t2 batch summary BATCH [--no-fetch]
    t2 batch log BATCH [ENTRY:]CELL [--tail N] [--head N] [--file NAME] [--list]
    (the output: t2lib/batchout.py, its rules t2lib/batchreport.py; BATCH.md "Analysing a batch")

The plan (`plan`, `status`, schedule.txt) prints each entry's hosts (an ht1's grows as +HOST),
pools, predicted start and end, why it waits, each host's busy-until, the predicted finish and
what mode serial would take.

The layout (§5.1), under MAC_ROOT/batches/:
    .lock .lock.json          the driver's flock, and who holds it (§6.2)
    .running                  the beacon while a batch runs (§8)
    BATCH/batch.json          the parsed batch, frozen; BATCH/state.json, batch.log, schedule.txt
    BATCH/entries/ID/         placed child inner started run exit ended entry.log report.txt
                              (placed, started and ended created exclusively; attempt-N/ the old ones)

Each entry runs as a child process (§4.2): t2lib/batchchild.py around `t2 WORD ARGS --hosts S`,
the cmd, or t2lib/batchkinds.py for ht1 (ht1-farm.sh), stic1 (drive.py --private-copy) and bs2,
in its own session, stdin /dev/null, T2_UNKNOWN_MAX set from `patience`.  An ht1's queue is RUN-XXXX
and a stic1's mark st1-ROUND-XXXX (XXXX: the batch id's last four; rN added on a retry), so a
batch never reuses a queue or a mark.  The
child's own run.lifecycle takes its family locks; the run id is read from the lock's holder file
(its PID is the child's), so run.py is untouched.  What runs at once is batchsched.decide()'s.

Environment (tests): T2_BATCH_T2 (the t2 a child runs, split like a shell line), T2_BATCH_TICK
(the beacon and schedule refresh, s; default 60), T2_BATCH_POLL (s; default 1), T2_BATCH_STOP_WAIT
(s; default 60), T2_BATCH_MIN_FREE_GB (default 5), T2_BATCH_HOST_PREFLIGHT=1 (host checks on a
test MAC_ROOT; on the default root they always run), T2_BATCH_FACTS (a JSON {host: facts or
[D, M]}: the hosts' ceilings, instead of the probe and MAC_ROOT/facts/), T2_BATCH_LIVE (a JSON
{host: pick-host probe}: the live probe), T2_BATCH_PLAN (1: plan t2 words per set on a test root
too; 0: never), T2_BATCH_AWAY_S (a loop turn this late means the Mac was away; default 120).

What runs at once, and where, is batchsched's (§4.5): simulate() first, then decide() with its
finish (the no-idle-host rule), with pick-host's live probe only when an entry waits for shares.
Sleep and drops (§6.4, B4): the children get T2_UNKNOWN_MAX from `patience` and T2_SSH with
ServerAliveCountMax=4 and ConnectionAttempts=3; the supervisor's own ssh (the hosts' beacon) treats
255 as no answer and retries with backoff; a loop turn that comes late (the Mac slept) is logged and
counts toward no lock-wait.  batch-keeper.sh BATCH resumes a batch whose driver died.
"""

import errno
import fcntl
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import time

from . import batchfile as bf
from . import batchsched as bs
from . import hosts as H
from . import notify
from . import mac_root as default_mac_root
from . import utc_now, DEFAULT_MAC_ROOT, expand

HERE = os.path.dirname(os.path.abspath(__file__))
T2_DIR = os.path.dirname(HERE)
CHILD = os.path.join(HERE, "batchchild.py")
BATCH_RE = re.compile(r"bt-\d{8}T\d{4}-[0-9a-f]{4}\Z")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
EXIT_OK, EXIT_FAIL, EXIT_REFUSED, EXIT_INTERNAL, EXIT_INTERRUPTED = 0, 1, 2, 3, 130
INTERRUPTED_RC = (130, 129, 143, 128 + 2, 128 + 15, -2, -15, -1)
FINAL = ("PASS", "FAIL", "INCOMPLETE", "BLOCKED", "REFUSED", "ERROR", "UNKNOWN", "SKIPPED")
LIVE = ("starting", "running", "resume-wait")
DRIVEN_KINDS = ("t2", "ht1", "stic1", "bs2", "cmd")
HOST_AUTONOMOUS = ("ht1", "stic1", "bs2")   # §4.3: never signalled; their host work runs on
KINDS_PY = os.path.join(HERE, "batchkinds.py")
KIND_TOOL_ENV = {"ht1": "T2_BATCH_HT1_FARM", "stic1": "T2_BATCH_DRIVE", "bs2": "T2_BATCH_BS2"}   # a test root's stubs
POLL_S_HOSTJOB = 15                # hostjob.POLL_S: T2_UNKNOWN_MAX counts its polls
SSH_PATIENCE = ("-o", "ServerAliveCountMax=4", "-o", "ConnectionAttempts=3")   # §6.4 item 3, the children's ssh
AWAY_S = 120                       # a loop turn this much late: the Mac was away (asleep), §6.4
LOCK_WAIT_STEP_S = 120             # at most this much lock-wait a turn (a sleep isn't waiting)
VALVE = 3                          # decision 5: three entries in a row with no PASS stop the starts
LOST_RACE = "holds its lock"       # run.Busy's words
RESUME_MAX = 3                     # interrupt exits the batch didn't send, an entry, before it is left INCOMPLETE


def say(*a):
    print(*a, flush=True)


def err(*a):
    print("t2 batch:", *a, file=sys.stderr, flush=True)


def env_f(name, default):
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return float(default)


# --- paths and small files ----------------------------------------------------------------------

def batches_dir(root=None):
    return os.path.join(root or default_mac_root(), "batches")


def new_batch_id(t=None):
    return f"bt-{time.strftime('%Y%m%dT%H%M', time.gmtime(t or time.time()))}-{secrets.token_hex(2)}"


def real_root(root=None):
    """True for the default MAC_ROOT: the batch then talks to the hosts itself (host preflight,
    the hosts' beacon); a test root never does unless T2_BATCH_HOST_PREFLIGHT=1."""
    r = os.path.realpath(root or default_mac_root())
    return r == os.path.realpath(expand(DEFAULT_MAC_ROOT))


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def read_text(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return default


def excl(path, text):
    """PATH created with O_CREAT|O_EXCL holding TEXT: True, or False when it was there (§6.2)."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return True


# --- processes ------------------------------------------------------------------------------------

def lstart(pid):
    """`ps -o lstart= -p PID` (the process's start time, for adoption), or None: not running."""
    try:
        r = subprocess.run(["ps", "-o", "lstart=", "-p", str(int(pid))], capture_output=True, text=True, timeout=10)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    s = r.stdout.strip()
    return s if r.returncode == 0 and s else None


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError) as e:
        return getattr(e, "errno", None) == errno.EPERM


def alive(child):
    """True when CHILD ({pid, lstart}) still runs as the same process."""
    if not child or not child.get("pid") or not pid_alive(child["pid"]):
        return False
    ls = lstart(child["pid"])
    return ls is not None and (not child.get("lstart") or ls == child["lstart"])


# --- the driver lock (§6.2) ---------------------------------------------------------------------

class Busy(Exception):
    pass


class DriverLock:
    def __init__(self, root=None):
        d = batches_dir(root)
        self.path = os.path.join(d, ".lock")
        self.info = self.path + ".json"
        self.fd = None

    def acquire(self, batch):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            h = read_json(self.info) or {}
            raise Busy(f"another batch driver runs: batch {h.get('batch', '?')}, pid {h.get('pid', '?')}, "
                       f"started {h.get('start', '?')}") from None
        self.fd = fd
        write_json(self.info, {"batch": batch, "pid": os.getpid(), "start": utc_now()})
        return self

    def rename(self, batch):
        """The holder's batch id, once it is known (cmd_file takes the lock before make_dir)."""
        if self.fd is not None:
            h = read_json(self.info) or {}
            write_json(self.info, {"batch": batch, "pid": os.getpid(), "start": h.get("start") or utc_now()})

    def release(self):
        if self.fd is not None:
            try:
                os.remove(self.info)
            except OSError:
                pass
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None


# --- the beacon (§8.3, §8.4) ----------------------------------------------------------------------

def beacon_path(root=None):
    return os.path.join(batches_dir(root), ".running")


def read_beacon(root=None):
    """The live beacon, or None (none, or its PID is gone)."""
    b = read_json(beacon_path(root))
    if not isinstance(b, dict) or not pid_alive(b.get("pid")):
        return None
    return b


def beacon_line(b, all_hosts=None):
    """`batch ID (3 of 6 in flight, ETA 04:10Z) holds stic,holt on chopin,cinqcent and bs2 on
    copland; cage is free`."""
    if not b:
        return None
    held = {}
    for r in b.get("running") or []:
        for h in r.get("hosts") or []:
            held.setdefault(h, set()).update(r.get("pools") or [])
    groups = {}
    for h in (b.get("hosts") or sorted(held)):
        if held.get(h):
            groups.setdefault(",".join(sorted(held[h])), []).append(h)
    hold = " and ".join(f"{p} on {','.join(hs)}" for p, hs in groups.items())
    free = [h for h in (all_hosts or b.get("hosts") or []) if not held.get(h)]
    eta = b.get("eta")
    eta_s = time.strftime("%H:%MZ", time.gmtime(eta)) if isinstance(eta, (int, float)) else "?"
    n_run = len(b.get("running") or [])
    total = n_run + int(b.get("entries_left") or 0) + int(b.get("entries_done") or 0)
    s = f"batch {b.get('batch')} ({n_run} of {total} in flight, ETA {eta_s})"
    s += f" holds {hold}" if hold else " holds no host pool"
    if free:
        s += f"; {', '.join(free)} {'is' if len(free) == 1 else 'are'} free"
    return s


# --- pure: what resume does with an entry (§6.2) ----------------------------------------------------

def next_action(state, entry):
    """§6.2's table.  STATE: {ended, placed, started: bool; alive: the child still runs; run: the
    run id or None; exit: the child's recorded exit code or None; run_finished: the run has its
    report.txt}.  Returns none, adopt, run, finish, finish-unknown, replace, resume,
    gate-incomplete, incomplete, rerun or restart."""
    if state.get("ended"):
        return "none"
    if state.get("alive"):
        return "adopt"
    if not state.get("placed"):
        return "run"
    rc = state.get("exit")
    if rc is not None and rc not in INTERRUPTED_RC:
        return "finish"
    if not state.get("started"):
        return "replace"
    if entry.kind == "t2":
        if not state.get("run"):
            return "replace"
        if rc is None and state.get("run_finished"):
            return "finish-unknown"
        if entry.family == "gate":
            return "gate-incomplete"
        return "resume"
    if entry.kind == "cmd":
        return "rerun" if entry.rerun else "incomplete"
    return "restart"


def status_of(entry, rc):
    """An ended child's entry status from its exit code."""
    if rc in INTERRUPTED_RC:
        return "INCOMPLETE"
    if entry.kind == "cmd":
        return "PASS" if rc == 0 else "FAIL"
    return {0: "PASS", 1: "FAIL", 2: "REFUSED", 3: "ERROR", 127: "ERROR"}.get(rc, "FAIL")


def retry_shaped(status, rows=None):
    """§6.5: the entry's failure is the farm's trouble, not a finding: it exited 3 (ERROR), or it
    FAILed and at least half its non-PASS cells (ROWS: its summary rows) are class harness or
    lost (a host that stopped answering past patience leaves LOST cells).  A FAIL whose non-PASS
    cells are mostly class fail never retries."""
    if status == "ERROR":
        return True
    if status != "FAIL" or not rows:
        return False
    bad = [r for r in rows if r.get("verdict") not in ("PASS", "NOT-RUN") and r.get("cell") != "-"]
    farm = [r for r in bad if r.get("class") in ("harness", "lost")]   # (NOT-RUN: a stop rule's, it follows the rest)
    return bool(bad) and 2 * len(farm) >= len(bad)


def run_name(e, bid, attempt):
    """The ht1 queue (RUN-XXXX) or stic1 mark (st1-ROUND-XXXX) of entry E's ATTEMPT in batch BID:
    the batch's own, so a second batch of the file never meets the first's todo or mark."""
    tag = (bid or "bt-0000")[-4:] + (f"r{attempt}" if attempt and attempt > 1 else "")
    if e.kind == "ht1":
        return f"{e.args[0]}-{tag}"
    if e.kind == "stic1":
        return f"st1-{e.args[0]}-{tag}"
    return None


# --- profiles and claims ----------------------------------------------------------------------------

def history_walls(root, file, eid, exclude=None, last=3):
    """Walls (s) of entry EID in the last LAST batches of FILE that ran it (state.json)."""
    out = []
    d = batches_dir(root)
    try:
        names = sorted((n for n in os.listdir(d) if not n.startswith(".")), reverse=True)
    except OSError:
        return out
    for n in names:
        if n == exclude:
            continue
        bj = read_json(os.path.join(d, n, "batch.json"), {}) or {}
        if bj.get("file") != file:
            continue
        e = ((read_json(os.path.join(d, n, "state.json"), {}) or {}).get("entries") or {}).get(eid) or {}
        if e.get("status") in ("PASS", "FAIL") and isinstance(e.get("started"), (int, float)) \
                and isinstance(e.get("ended"), (int, float)):
            out.append(e["ended"] - e["started"])
        if len(out) >= last:
            break
    return out


def profiles(batch, hosts, root=None, file=None, exclude=None, cap=None, src=None, checkout=None):
    """[batchsched.Profile] (§4.5 Estimates, t2lib/batchprof.py): SRC the figures' sources
    (default: estimates.tsv, the local ledger's history, ram-sizes.tsv, drive.py, bs2; no t2
    planner), with this file's earlier batches as the history of est=-less entries; CAP the
    hosts' batchsched.capacity (default: the table's, no ceilings)."""
    from . import batchprof as bp
    if cap is None:
        cap = bs.capacity(H.load(), hosts)
    hist = (lambda eid: history_walls(root, file, eid, exclude)) if file else (lambda eid: [])
    if src is None:
        src = bp.Sources.default(checkout or os.path.dirname(os.path.dirname(T2_DIR)), root, history=hist)
    elif file and src.history is None:
        src.history = hist
    return bp.build(batch, hosts, cap, src)


def stub_json(name):
    """A test's stub source (T2_BATCH_FACTS, T2_BATCH_LIVE): the JSON file the variable names, or
    None when it is unset or unreadable."""
    p = os.environ.get(name)
    return read_json(p) if p else None


def batch_facts(hosts, root=None, probe=False):
    """{host: facts or [D, M]} the ceilings come from: T2_BATCH_FACTS (a stub, tests); with PROBE
    on the real root, facts.probe_all (read only, as `t2 WORD plan` probes); else the facts the
    last t2 run saved in MAC_ROOT/facts/.  A host with none is left out (its ceiling unknown)."""
    stub = stub_json("T2_BATCH_FACTS")
    if isinstance(stub, dict):
        return {h: stub[h] for h in hosts if h in stub}
    from . import facts as factsmod
    out = {}
    if probe and real_root(root):
        try:
            got = factsmod.probe_all(list(hosts), {"guests": [], "sets": []}, save=False)
        except Exception:   # noqa: BLE001 - the saved facts then
            got = {}
        out = {h: f for h, f in got.items() if f and f.get("ok") is not False}
    for h in hosts:
        if h not in out:
            f = factsmod.load(h, root)
            if f:
                out[h] = f
    return out


def ceilings_of(facts):
    return {h: list(c) for h, c in ((h, bs.ceiling_of(f)) for h, f in (facts or {}).items()) if c}


def t2_planner(batch, hosts, checkout, root=None, notes=None):
    """The real per-set planner (batchprof.T2Planner) on the real root, unless T2_BATCH_PLAN=0;
    T2_BATCH_PLAN=1 asks for it on a test root too.  None: no t2 plans (est=, history, default)."""
    want = os.environ.get("T2_BATCH_PLAN")
    if want == "0" or not any(e.kind == "t2" for e in batch.entries):
        return None
    if want != "1" and not real_root(root):
        return None
    from . import batchprof as bp
    return bp.T2Planner(checkout, hosts, root, batch.settings.get("round"), notes)


def live_probe(hosts, table=None):
    """{host: pick-host's probe facts} with load_per_core from its rank() (LOAD_PENALTY on the
    shared host), read only: cmp-run.sh --free and --ceiling, /proc.  T2_BATCH_LIVE: a stub
    (tests).  A host that doesn't answer is {"ok": False}: no live join there, never 'ended'."""
    stub = stub_json("T2_BATCH_LIVE")
    if isinstance(stub, dict):
        return {h: stub.get(h) or {"ok": False} for h in hosts}
    from . import batchprof as bp
    try:
        ph = bp.load_path("t2_batch_pick_host", os.path.join(os.path.dirname(T2_DIR), "pick-host"))
        tbl = ph._host_table()
        probes = ph.probe_hosts(tbl, list(hosts), [], timeout=20)
        rows = {h: dict(tbl.row(h), arch=tbl.host_arch(h)) for h in hosts}
        ranked, _dropped = ph.rank(probes, None, (), (), rows)
        for r in ranked:
            p = probes.get(r["host"])
            if p is not None:
                p["load_per_core"] = r["load_per_core"]
        return probes
    except Exception:   # noqa: BLE001 - no probe: no live join
        return {h: {"ok": False} for h in hosts}


def family_of_run(run):
    from . import run as runmod
    for fam, pre in runmod.RUN_PREFIX.items():
        if (run or "").startswith(pre + "-"):
            return fam
    return None


def session_claims(mac_root=None, ours_pids=(), ours_runs=()):
    """[batchsched.Session]: every run holding a family lock that is not the batch's own (its
    children's PIDs and runs), with the pools its family uses on the hosts it uses (§8.2)."""
    from . import run as runmod
    runs = {}
    for name in runmod.RUN_PREFIX:
        lk = runmod.FamilyLock(name, mac_root)
        try:
            if not lk.held():
                continue
        except OSError:
            continue
        for h in lk.holders() or [{}]:   # (a holt lock has a holder per run when they hold cells)
            if h.get("pid") in ours_pids or (h.get("run") and h.get("run") in ours_runs):
                continue
            runs.setdefault(h.get("run") or "?", set()).add(name)
    out = []
    for run, locks in sorted(runs.items()):
        pools = set()
        for n in locks:
            pools |= bf.FAMILY_POOLS.get(n, set())
        hs = runmod.run_hosts(run, mac_root) if runmod.RUN_RE.match(run or "") else []
        out.append(bs.Session(run, locks, pools, hs or None))
    return out


def discover_run(entry, pid, mac_root=None):
    """The run id the child PID holds ENTRY's first family lock for (its .lock-NAME.json), or
    None (still planning: "pending"; or not holding it)."""
    from . import run as runmod
    names = bf.lock_list(entry)
    if not names or not pid:
        return None
    for h in runmod.FamilyLock(names[0], mac_root).holders():
        r = h.get("run")
        if h.get("pid") == pid and r and r != "pending" and runmod.RUN_RE.match(r):
            return r
    return None


# --- host checks (§6.1 items 2, 4), on the real root only ------------------------------------------

def host_preflight(hosts, checkout, out, facts_out=None):
    """[refusal] for the hosts: each answers, its testvm/.backotter-commit is HEAD, and t2's own
    preflight (Tctl, /mnt/bko-ram, kvm, /bkoscratch) passes.  FACTS_OUT (a dict): the facts of
    the hosts that answered (their ceilings are the scheduler's)."""
    from . import facts as factsmod
    from . import run as runmod
    bad = []
    head = subprocess.run(["git", "-C", checkout, "rev-parse", "--short=7", "HEAD"], capture_output=True,
                          text=True).stdout.strip()
    got = factsmod.probe_all(list(hosts), {"guests": [], "sets": ["bko-bin"]}, save=False)
    facts = {}
    for h in hosts:
        f = got.get(h) or {}
        if f.get("ok") is False or not f:
            bad.append(f"{h}: no answer ({f.get('error') or 'no facts'})")
            continue
        facts[h] = f
        st = ((f.get("synced") or {}).get("testvm") or "").split(".")[0]
        if head and st != head:
            bad.append(f"{h}: testvm/ there is {st or 'unstamped'}, HEAD is {head}")
    bad += runmod.preflight(facts, [h for h in hosts if h in facts])
    if facts_out is not None:
        facts_out.update(facts)
    if any("HEAD is" in x for x in bad):
        bad.append(f"sync first: testvm/sync-hosts.sh {' '.join(hosts)}   (the batch checks; it never syncs)")
    return bad


# --- the batch dir ------------------------------------------------------------------------------------

def find_file(path, cwd=None):
    for p in (path, os.path.join(T2_DIR, path)):
        q = os.path.abspath(os.path.join(cwd or os.getcwd(), p)) if not os.path.isabs(p) else p
        if os.path.isfile(q):
            return os.path.realpath(q)
    return None


def reader(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def parse_file(path):
    return bf.parse(reader(path), path, reader)


def list_batches(root=None):
    d = batches_dir(root)
    try:
        names = [n for n in os.listdir(d) if not n.startswith(".") and os.path.isfile(os.path.join(d, n, "batch.json"))]
    except OSError:
        return []
    return sorted(names, key=lambda n: ((read_json(os.path.join(d, n, "batch.json"), {}) or {}).get("created") or 0, n))


def unfinished_of(file, root=None):
    """The newest unfinished batch (no report.txt) of FILE, or None."""
    d = batches_dir(root)
    for n in reversed(list_batches(root)):
        bj = read_json(os.path.join(d, n, "batch.json"), {}) or {}
        if bj.get("file") == file and not os.path.exists(os.path.join(d, n, "report.txt")):
            return n
    return None


def placed_hosts(edir):
    """An entry's hosts from its placed: the claim's, then each grow line's, in order (the rule
    batchkinds.placed_hosts follows too)."""
    try:
        with open(os.path.join(edir, "placed"), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    out = []
    for i, ln in enumerate(lines):
        try:
            hs = (json.loads(ln) or {}).get("hosts") or [] if i == 0 else \
                ([(json.loads(ln[5:]) or {}).get("host")] if ln.startswith("grow ") else [])
        except ValueError:
            continue
        out += [h for h in hs if h and h not in out]
    return out


def first_claim(edir):
    """An entry's claim: `placed`'s first line (the grow lines after it left out), or {}."""
    try:
        with open(os.path.join(edir, "placed"), encoding="utf-8") as f:
            ln = f.readline()
    except OSError:
        return {}
    try:
        return json.loads(ln) or {}
    except ValueError:
        return {}


def batch_dir(bid, root=None):
    if os.path.isdir(bid) and os.path.isfile(os.path.join(bid, "batch.json")):
        return os.path.abspath(bid)
    return os.path.join(batches_dir(root), bid)


# --- the driver -----------------------------------------------------------------------------------------

class Driver:
    """One batch's supervisor: its dir BDIR, its frozen batch, its state; drive() is the loop."""

    def __init__(self, bdir, checkout, root=None, out=say):
        self.bdir, self.checkout, self.root, self.out = bdir, checkout, root or default_mac_root(), out
        self.id = os.path.basename(bdir)
        self.table = None
        self.procs = {}          # id -> Popen of our own children
        self.stop = None
        self.signalled = set()
        self.last_tick = 0.0
        self.tick = env_f("T2_BATCH_TICK", 60)
        self.poll = env_f("T2_BATCH_POLL", 1)
        self.live, self.live_t = None, 0.0
        self.live_joins = {}     # host -> when an entry last joined it live (no second join on that probe)
        self.last_turn = None
        self.notes = []
        self._rows = {}
        self.load()

    def load(self):
        """batch.json and state.json as they are now (again once the driver lock is held: a
        driver that ended just before may have saved newer state); the valve with them."""
        self.doc = read_json(os.path.join(self.bdir, "batch.json")) or {}
        self.batch = bf.Batch.from_json(self.doc["batch"])
        self.hosts = list(self.doc.get("hosts") or [])
        self.state = read_json(os.path.join(self.bdir, "state.json")) or {"entries": {}}
        self.valve_run = int(self.state.get("valve_run") or 0)
        self.valve = bool(self.state.get("valve"))
        self._profiles = None
        self.ceilings = dict(self.doc.get("ceilings") or {})
        for e in self.batch.entries:
            self.st(e.id)

    # -- state
    def st(self, eid):
        es = self.state.setdefault("entries", {})
        return es.setdefault(eid, {"status": "pending", "attempts": 0, "resumes": 0, "lock_wait_s": 0.0})

    def edir(self, eid):
        d = os.path.join(self.bdir, "entries", eid)
        os.makedirs(d, exist_ok=True)
        return d

    def log(self, text, eid=None):
        """batch.log: append-only "UTC [ENTRY: ]text" lines."""
        with open(os.path.join(self.bdir, "batch.log"), "a", encoding="utf-8") as f:
            f.write(f"{utc_now()} {eid + ': ' if eid else ''}{text}\n")

    def entry_log(self, eid, text):
        with open(os.path.join(self.edir(eid), "entry.log"), "a", encoding="utf-8") as f:
            f.write(f"{utc_now()} batch: {text}\n")

    def save(self):
        self.state["updated"] = time.time()
        self.state["valve"], self.state["valve_run"] = self.valve, self.valve_run   # (decision 5 survives a resume)
        write_json(os.path.join(self.bdir, "state.json"), self.state)

    @property
    def entries(self):
        return self.batch.entries

    def settings(self):
        return self.batch.settings

    # -- sentinels
    def sentinels(self, e):
        d = self.edir(e.id)
        has = lambda n: os.path.exists(os.path.join(d, n))   # noqa: E731
        child = read_json(os.path.join(d, "child"))
        rc = None
        x = read_text(os.path.join(d, "exit"))
        if x and x.split():
            try:
                rc = int(x.split()[0])
            except ValueError:
                rc = None
        run = (read_text(os.path.join(d, "run")) or "").strip() or None
        from . import run as runmod
        fin = bool(run) and os.path.exists(os.path.join(runmod.run_dir(run, self.root), "report.txt"))
        return {"ended": has("ended"), "placed": has("placed"), "started": has("started"),
                "alive": alive(child) if child else False, "run": run, "exit": rc, "run_finished": fin,
                "child": child}

    def archive(self, e, keep=()):
        """Move the attempt's sentinels (all but KEEP) into attempt-N/."""
        d = self.edir(e.id)
        s = self.st(e.id)
        n = 1
        while os.path.exists(os.path.join(d, f"attempt-{n}")):
            n += 1
        a = os.path.join(d, f"attempt-{n}")
        for name in ("placed", "child", "inner", "exit", "started", "run", "ended"):
            if name in keep:
                continue
            p = os.path.join(d, name)
            if os.path.exists(p):
                os.makedirs(a, exist_ok=True)
                os.replace(p, os.path.join(a, name))
        self.procs.pop(e.id, None)
        s.pop("pid", None)
        return a

    # -- children
    def t2_prefix(self):
        o = os.environ.get("T2_BATCH_T2")
        if o:
            return shlex.split(o)
        return [sys.executable, "-B", os.path.join(T2_DIR, "t2")]

    def entry_argv(self, e, hosts):
        if e.kind == "t2":
            args = self.doc.get("args", {}).get(e.id) or list(e.args)
            return self.t2_prefix() + list(args) + ["--hosts", ",".join(hosts)]
        if e.kind == "cmd":
            return list(e.cmd)
        return self.kind_argv(e, hosts)

    def kind_argv(self, e, hosts, resume=False):
        """ht1, stic1, bs2: t2lib/batchkinds.py around the tool (§3.1's table)."""
        d = self.edir(e.id)
        py = [sys.executable, "-B", KINDS_PY]
        s = self.st(e.id)
        if e.kind == "ht1":
            run = s.get("run") or run_name(e, self.id, s.get("attempts") or 1)
            return py + ["ht1", d, "--run", run, "--queue-host", (list(hosts) or ["?"])[0]] + \
                (["--resume"] if resume else []) + ["--"] + list(e.args[1:])
        if e.kind == "stic1":
            mark = s.get("run") or run_name(e, self.id, s.get("attempts") or 1)
            drive = shlex.split(os.environ["T2_BATCH_DRIVE"]) if os.environ.get("T2_BATCH_DRIVE") else \
                [sys.executable, "-B", os.path.join(os.path.dirname(T2_DIR), "stictest1", "drive.py")]
            return py + ["stic1", d, "--"] + drive + ["--private-copy", "--hosts", ",".join(hosts), "--out",
                                                      os.path.join(d, "stic1"), "--logs-mark", mark] + list(e.args)
        if e.kind == "bs2":
            return py + ["bs2", d, "--hosts", ",".join(hosts)] + (["--resume"] if resume else []) + ["--"] + list(e.args)
        raise RuntimeError(f"no such kind {e.kind}")

    def child_env(self, e):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", T2_BATCH=self.id, T2_BATCH_ENTRY=e.id)
        pat = int(self.settings().get("patience") or 0)
        env["T2_UNKNOWN_MAX"] = str(max(20, -(-pat // POLL_S_HOSTJOB)))
        env["T2_BATCH_PATIENCE_S"] = str(pat or 7200)
        env["T2_SSH"] = ssh_with_patience(env.get("T2_SSH"))
        return env

    def spawn(self, e, hosts, pools, why, argv=None, place=True, predicted_end=None):
        """Start E's child on HOSTS: `placed` (exclusive: a second driver can't claim it too),
        then the child, then `child`.  False when the claim was taken."""
        d = self.edir(e.id)
        s = self.st(e.id)
        logp = os.path.join(d, "entry.log")
        off = os.path.getsize(logp) if os.path.exists(logp) else 0
        if place:
            claim = {"hosts": list(hosts), "pools": sorted(pools), "t": time.time(), "why": why,
                     "predicted_end": predicted_end, "attempt": s.get("attempts", 0) + 1, "log_offset": off}
            if not excl(os.path.join(d, "placed"), json.dumps(claim, sort_keys=True) + "\n"):
                self.log("not started: its placed is there (another driver?)", e.id)
                return False
            s["attempts"] = s.get("attempts", 0) + 1
            s["started"] = time.time()
            s.pop("resuming", None)
        s["log_offset"] = off
        if place and e.kind in ("ht1", "stic1"):
            s["run"] = run_name(e, self.id, s.get("attempts") or 1)
            write_text(os.path.join(d, "run"), s["run"] + "\n")
        argv = argv or self.entry_argv(e, hosts)
        self.entry_log(e.id, f"start ({why}): {' '.join(shlex.quote(a) for a in argv)}")
        logf = open(logp, "ab")
        try:
            p = subprocess.Popen([sys.executable, "-B", CHILD, d, "--"] + argv, stdin=subprocess.DEVNULL,
                                 stdout=logf, stderr=subprocess.STDOUT, start_new_session=True,
                                 cwd=self.checkout, env=self.child_env(e))
        finally:
            logf.close()
        self.procs[e.id] = p
        write_json(os.path.join(d, "child"), {"pid": p.pid, "lstart": lstart(p.pid), "argv": argv, "t": time.time()})
        s.update(status="running" if e.kind != "t2" else "starting", hosts=list(hosts), pools=sorted(pools),
                 pid=p.pid, predicted_end=predicted_end, wait=None)
        s.setdefault("started", time.time())
        s.pop("wait_since_t", None)
        if e.kind != "t2":
            excl(os.path.join(d, "started"), f"{int(time.time())}\n")
        self.log(f"start on {','.join(hosts) or 'the Mac'} ({why})", e.id)
        return True

    def inner_pid(self, e):
        x = read_text(os.path.join(self.edir(e.id), "inner"))
        try:
            return int((x or "").strip())
        except ValueError:
            return None

    def ours(self):
        pids, runs = set(), set()
        for e in self.entries:
            s = self.st(e.id)
            if s.get("status") in LIVE:
                p = self.inner_pid(e)
                if p:
                    pids.add(p)
            if s.get("run") and s.get("status") in LIVE + ("interrupted",):
                runs.add(s["run"])   # (a finished entry's run is no longer ours: a session may resume it)
        return pids, runs

    def discover(self):
        for e in self.entries:
            s = self.st(e.id)
            if e.kind == "bs2" and s.get("status") == "running" and not s.get("run"):
                r = (read_text(os.path.join(self.edir(e.id), "run")) or "").strip()
                if r:
                    s["run"] = r
                    self.log(f"run {r}", e.id)
                continue
            if e.kind != "t2" or s.get("status") != "starting":
                continue
            r = discover_run(e, self.inner_pid(e), self.root)
            if r:
                self.got_run(e, r)

    def got_run(self, e, r):
        d = self.edir(e.id)
        s = self.st(e.id)
        if not os.path.exists(os.path.join(d, "run")):
            write_text(os.path.join(d, "run"), r + "\n")
        excl(os.path.join(d, "started"), f"{int(time.time())}\n")
        s.update(run=r, status="running")
        self.log(f"run {r}", e.id)

    def fallback_run(self, e):
        """The one new run dir of E's family made since its claim and no other entry's, or None."""
        from . import run as runmod
        fam = e.family
        pre = runmod.RUN_PREFIX.get(fam)
        claim = first_claim(self.edir(e.id))
        t0 = float(claim.get("t") or 0) - 1
        taken = {self.st(x.id).get("run") for x in self.entries if x.id != e.id}
        try:
            names = os.listdir(runmod.runs_dir(self.root))
        except OSError:
            return None
        hits = [n for n in names if pre and n.startswith(pre + "-") and runmod.RUN_RE.match(n) and n not in taken
                and os.path.getmtime(os.path.join(runmod.runs_dir(self.root), n)) >= t0]
        return hits[0] if len(hits) == 1 else None

    def child_rc(self, e):
        """The exit code of E's child when it has ended, else None (still running)."""
        d = self.edir(e.id)
        x = read_text(os.path.join(d, "exit"))
        p = self.procs.get(e.id)
        if p is not None:
            rc = p.poll()
            if rc is None:
                return None
            x = read_text(os.path.join(d, "exit")) or x
            if x and x.split() and x.split()[0].lstrip("-").isdigit():
                return int(x.split()[0])
            return 128 - rc if rc < 0 else rc
        if x and x.split() and x.split()[0].lstrip("-").isdigit():
            return int(x.split()[0])
        child = read_json(os.path.join(d, "child"))
        if child and alive(child):
            return None
        return "gone"

    def log_since(self, e):
        p = os.path.join(self.edir(e.id), "entry.log")
        try:
            with open(p, "rb") as f:
                f.seek(int(self.st(e.id).get("log_offset") or 0))
                return f.read().decode("utf-8", errors="replace")
        except OSError:
            return ""

    def reap(self):
        changed = False
        for e in self.entries:
            s = self.st(e.id)
            if s.get("status") not in ("starting", "running"):
                continue
            rc = self.child_rc(e)
            if rc is None:
                continue
            changed = True
            if rc == "gone":
                self.gone(e)
            else:
                self.ended(e, rc)
        return changed

    def gone(self, e):
        """An adopted child that ended with no exit recorded: §6.2's row for it."""
        self.procs.pop(e.id, None)
        self.resume_one(e, self.sentinels(e))

    def ended(self, e, rc):
        s = self.st(e.id)
        self.procs.pop(e.id, None)
        if e.kind == "t2" and not s.get("run"):
            r = self.fallback_run(e) if rc != 2 else None
            if r:
                self.got_run(e, r)
        tail = self.log_since(e)
        if e.kind == "t2" and rc == 2 and not s.get("run") and LOST_RACE in tail:
            who = next((ln for ln in tail.splitlines() if LOST_RACE in ln), "").strip()
            self.archive(e)
            s.update(status="pending", wait="lost a lock race", hosts=None, pools=None)
            s["lost_races"] = s.get("lost_races", 0) + 1
            s["attempts"] = max(0, int(s.get("attempts") or 1) - 1)   # (sessions win locks: no attempt used)
            self.log(f"lost the lock race, back to ready: {who}", e.id)
            return
        if s.get("resuming") and rc == 2 and LOST_RACE in tail:
            self.archive(e, keep=("placed", "started", "run"))
            s.update(status="resume-wait", wait="its resume waits for its lock")
            self.log("its resume lost the lock race: waits", e.id)
            return
        if self.stop and rc in INTERRUPTED_RC:
            self.interrupted(e, rc)
            return
        if rc in INTERRUPTED_RC and not (e.kind == "cmd" and not e.rerun):
            self.interrupted_unsent(e, rc)
            return
        status = status_of(e, rc)
        retry = e.retry if e.retry is not None else int(self.settings().get("retry") or 0)
        rows = self.cell_rows(e, status) if status in ("FAIL", "PASS") and e.kind != "cmd" else None
        self._rows[e.id] = rows
        if retry_shaped(status, rows) and s.get("attempts", 1) <= retry and not self.stop:
            self.archive(e)
            s.update(status="pending", late=True, wait=f"retry after exit {rc}", run=None)
            self.log(f"exit {rc} ({status}): re-queued behind the unstarted entries (retry {s['attempts']} of {retry})",
                     e.id)
            self._rows.pop(e.id, None)
            return
        self.finish(e, status, rc)

    def cell_rows(self, e, status):
        """E's summary rows now (batchout.entry_summary: the ledger pulled from its hosts on the
        real root), for §6.5's retry shape and the valve; None when they can't be had."""
        from . import batchout
        try:
            self.save()
            return batchout.entry_summary(self.bdir, e.id, self.root, self.st(e.id).get("hosts") or (),
                                          status=status)
        except Exception as ex:   # noqa: BLE001 - the exit code alone then
            self.log(f"its cells can't be read ({type(ex).__name__}: {ex}): judged by its exit", e.id)
            return None

    def finish(self, e, status, rc, note=None, valve=True):
        s = self.st(e.id)
        d = self.edir(e.id)
        now = time.time()
        if status not in ("BLOCKED", "SKIPPED") or os.path.exists(os.path.join(d, "placed")):
            excl(os.path.join(d, "started"), f"{int(now)}\n")
        s.pop("resuming", None)
        if rc is not None:
            excl(os.path.join(d, "exit"), f"{rc} {int(now)}\n")
        excl(os.path.join(d, "ended"), f"{int(now)}\n")
        s.update(status=status, exit=rc, ended=now, wait=None)
        if note:
            s["note"] = note
        s.pop("pid", None)
        if s.get("run"):
            from . import run as runmod
            rep = read_text(os.path.join(runmod.run_dir(s["run"], self.root), "report.txt"))
            if rep is not None:
                write_text(os.path.join(d, "report.txt"), rep)
        self.log(f"ended {status}" + (f" (exit {rc})" if rc is not None else "") + (f": {note}" if note else ""), e.id)
        rows = self._rows.pop(e.id, None)
        if not valve:
            return
        no_pass = status in ("ERROR", "REFUSED", "INCOMPLETE") or (e.kind == "cmd" and status == "FAIL") or \
            (status == "FAIL" and rows is not None and not any(r.get("verdict") == "PASS" for r in rows))
        self.valve_run = self.valve_run + 1 if no_pass else 0
        if self.valve_run >= VALVE and not self.valve:
            self.valve = True
            self.log(f"safety valve: {VALVE} entries in a row ended with no PASS: no more starts")
        self.state["valve"], self.state["valve_run"] = self.valve, self.valve_run

    def interrupted_unsent(self, e, rc):
        """E's child ended with an interrupt exit this driver didn't send (an adopted child that
        wound down after its old driver exited, a Mac shutdown's TERM, a watcher TERMed while its
        host work goes on): §6.2's row for it, as a resume would do (t2 resume RUN, the watcher
        again, or a fresh start), never INCOMPLETE at once.  At most RESUME_MAX times an entry;
        then INCOMPLETE.  Neither counts toward the valve."""
        s = self.st(e.id)
        n = s["interrupts"] = int(s.get("interrupts") or 0) + 1
        if n > RESUME_MAX:
            self.finish(e, "INCOMPLETE", rc, f"interrupted {n} times (exit {rc}) while the batch ran on: left",
                        valve=False)
            return
        self.log(f"its child ended with exit {rc}, an interrupt the batch didn't send: picked up as a resume would",
                 e.id)
        self.archive(e, keep=("placed", "started", "run"))
        self.resume_one(e, self.sentinels(e))

    def interrupted(self, e, rc):
        """E's child ended under the batch's own interrupt: what resume picks up (§4.3)."""
        s = self.st(e.id)
        if e.kind == "t2" and s.get("run"):
            self.archive(e, keep=("placed", "started", "run"))
            s.update(status="interrupted", wait=None)
            self.log(f"interrupted (exit {rc}): `t2 batch resume {self.id}` resumes run {s['run']}", e.id)
        elif e.kind == "cmd" and not e.rerun:
            self.finish(e, "INCOMPLETE", rc, "interrupted (rerun=no)")
        else:
            self.archive(e)
            s.update(status="pending", wait=None, run=None)
            self.log(f"interrupted (exit {rc}) before any run: it runs again", e.id)

    # -- resume (§6.2)
    def resume_one(self, e, sen):
        s = self.st(e.id)
        act = next_action(sen, e)
        if sen.get("run"):
            s["run"] = sen["run"]
        claim = first_claim(self.edir(e.id))
        if act == "none":
            if s.get("status") not in FINAL:
                rc = sen.get("exit")
                s.update(status=status_of(e, rc) if rc is not None else "UNKNOWN", exit=rc)
        elif act == "adopt":
            s.update(status="running" if (e.kind != "t2" or sen.get("run")) else "starting",
                     hosts=placed_hosts(self.edir(e.id)) or claim.get("hosts") or s.get("hosts"),
                     pools=claim.get("pools") or s.get("pools"),
                     pid=(sen.get("child") or {}).get("pid"))
            s["log_offset"] = s.get("log_offset", 0)
            self.log(f"adopted its child, pid {s.get('pid')}", e.id)
        elif act == "finish":
            self.ended(e, sen["exit"])
        elif act == "finish-unknown":
            self.finish(e, "UNKNOWN", None, f"run {sen['run']} has its report but its exit wasn't seen: read it")
        elif act == "replace":
            self.archive(e)
            s.update(status="pending", run=None, wait=None)
            self.log("its start never reached a run: scheduled again", e.id)
        elif act == "resume":
            self.archive(e, keep=("placed", "started", "run"))
            s.update(status="resume-wait", hosts=claim.get("hosts") or s.get("hosts"),
                     pools=claim.get("pools") or s.get("pools"))
            self.try_resume(e)
        elif act == "gate-incomplete":
            from . import run as runmod
            rp = os.path.join(runmod.run_dir(sen["run"], self.root), "report.txt")
            if not os.path.exists(rp):
                write_text(rp, f"GateTest2 report: run {sen['run']}: set aside by t2 batch {self.id}: interrupted, "
                               "and gate has no resume() (docs/design/batch.md decision 7)\n")
            retry = e.retry if e.retry is not None else int(self.settings().get("retry") or 0)
            if not s.get("gate_requeued") and retry >= 1:
                self.archive(e)
                s.update(status="pending", run=None, gate_requeued=True, late=True)
                self.log(f"gate run {sen['run']} set aside (INCOMPLETE): re-queued once", e.id)
            else:
                self.finish(e, "INCOMPLETE", None, f"gate run {sen['run']} set aside: gate has no resume()")
        elif act == "incomplete":
            self.finish(e, "INCOMPLETE", sen.get("exit"), "interrupted; rerun=no")
        elif act == "rerun":
            self.archive(e)
            s.update(status="pending", wait=None)
            self.log("interrupted; rerun=yes: it runs again", e.id)
        elif act == "restart":
            hosts = placed_hosts(self.edir(e.id)) or claim.get("hosts") or s.get("hosts") or []
            self.archive(e, keep=("placed", "started", "run"))
            s.update(status="resume-wait", hosts=hosts, pools=claim.get("pools") or s.get("pools"))
            self.restart_kind(e)
        elif act == "run":
            if s.get("status") not in ("pending", "BLOCKED") or s.get("status") in LIVE:
                s["status"] = "pending"

    def restart_kind(self, e):
        """§6.2's ht1/stic1/bs2 rows: the watcher again, on the claim's hosts (and grows): ht1
        joins its runners back (--join), stic1 runs the same mark (drive.py adopts what ran), bs2
        re-attaches to its run (and restarts a queue that stopped)."""
        s = self.st(e.id)
        s["resumes"] = s.get("resumes", 0) + 1
        hosts = s.get("hosts") or []
        argv = self.kind_argv(e, hosts, resume=True)
        ok = self.spawn(e, hosts, s.get("pools") or [], f"resume {s.get('run') or e.kind}", argv=argv, place=False,
                        predicted_end=self.claim_end(e))
        if ok:
            s["status"] = "running"
        return ok

    def try_resume(self, e):
        """`t2 resume RUN` for E as a child, on its claim's hosts, when no session holds its locks."""
        s = self.st(e.id)
        held = [x for x in self.sessions() if bf.locks_of(e) & x.locks]
        if held:
            s["wait"] = f"its resume waits: lock held by session {held[0].run}"
            return False
        s["resumes"] = s.get("resumes", 0) + 1
        s["resuming"] = True
        ok = self.spawn(e, s.get("hosts") or [], s.get("pools") or [], f"resume {s['run']}",
                        argv=self.t2_prefix() + ["resume", s["run"]], place=False, predicted_end=self.claim_end(e))
        if ok:
            s["status"] = "running"
        return ok

    def claim_end(self, e):
        """E's predicted end as its claim had it (the state's, else the placed line's; a grow's
        newer one is the state's), for a resume that keeps the claim."""
        s = self.st(e.id)
        return s.get("predicted_end") or first_claim(self.edir(e.id)).get("predicted_end")

    # -- scheduling
    def sessions(self):
        pids, runs = self.ours()
        return session_claims(self.root, pids, runs)

    def running_claims(self, now):
        out, by, cap = [], None, None
        for e in self.entries:
            s = self.st(e.id)
            if s.get("status") in LIVE or s.get("status") == "interrupted":
                st0 = s.get("started") or now
                end = s.get("predicted_end")
                if end is None:   # (no end recorded: start + its wall there, §4.4's max(start + wall, now + 60 s))
                    if by is None:
                        by, cap = {p.id: p for p in self.profiles()}, self.cap()
                    p, hs = by.get(e.id), tuple(s.get("hosts") or ())
                    w = bs.wall_on(p, hs, cap) if p is not None and hs and all(h in cap for h in hs) else None
                    end = st0 + w if w is not None else None
                out.append(bs.Running(e.id, s.get("hosts") or (), s.get("pools") or (), st0, end))
        return out

    def make_profiles(self, plan=True, save=True):
        """The profiles, once a drive (on a resume the unstarted t2 entries are planned again:
        the ledger moved), kept in batch.json; T2Planner on the real root (t2_planner)."""
        from . import batchprof as bp
        planner = t2_planner(self.batch, self.hosts, self.checkout, self.root, self.notes) if plan else None
        hist = (lambda eid: history_walls(self.root, self.doc.get("file"), eid, self.id))
        src = bp.Sources.default(self.checkout, self.root, planner, hist, self.doc.get("args") or {})
        ps = bp.build(self.batch, self.hosts, self.cap(), src)
        self.notes += src.notes
        self._profiles = ps
        self.doc["profiles"] = [p.to_json() for p in ps]
        if save:
            write_json(os.path.join(self.bdir, "batch.json"), self.doc)
        return ps

    def profiles(self):
        if self._profiles is None:
            saved = self.doc.get("profiles")
            self._profiles = [bs.Profile.from_json(x) for x in saved] if saved else None
            if self._profiles is None:
                self.make_profiles(plan=False, save=False)
        ps = []
        for p in self._profiles:
            q = bs.Profile.from_json(p.to_json())
            if self.st(q.id).get("late"):
                q.pos += 100000    # (re-queued: behind every unstarted entry)
            ps.append(q)
        return ps

    def cap(self):
        return bs.capacity(self.table if self.table is not None else H.load(), self.hosts, self.ceilings)

    def done_ids(self):
        return {e.id for e in self.entries if self.st(e.id).get("status") in FINAL}

    def schedule(self, now):
        """decide(), then the starts it makes; lock-wait clocks; schedule.txt."""
        ps = self.profiles()
        cap = self.cap()
        sess = self.sessions()
        running = self.running_claims(now)
        conflicts = bf.lock_matrix(self.entries, {p.id: p.builds for p in ps if p.builds})
        blocked = {e.id for e in self.entries if self.st(e.id).get("status") in ("interrupted",)}
        serial = self.settings().get("mode") == "serial"
        sim = bs.simulate(ps, cap, running, now, sess, self.done_ids(), conflicts, serial, stopped=blocked)
        acts = bs.decide(ps, cap, running, sess, None, now, self.done_ids(), conflicts, serial, stopped=blocked,
                         finish=sim.horizon)
        if any(isinstance(a, bs.Wait) and a.kind == "capacity" and "shares:" in a.why for a in acts) \
                and self.live_source() and not self.stop:
            if self.live is None or now - self.live_t >= self.tick:
                self.live, self.live_t = live_probe(self.hosts, self.table), now
            live = dict(self.live or {})
            for h, t in self.live_joins.items():   # a host joined live: no second join until a probe a tick after it
                if self.live_t < t + self.tick:
                    live[h] = {"ok": False, "joined": True}
            acts = bs.decide(ps, cap, running, sess, live, now, self.done_ids(), conflicts, serial,
                             stopped=blocked, finish=sim.horizon)
        lw = float(self.settings().get("lock-wait") or 0)
        started = 0
        by = {e.id: e for e in self.entries}
        for a in acts:
            e = by[a.id]
            s = self.st(a.id)
            if isinstance(a, bs.Wait):
                last = s.get("wait_since_t")
                if a.session:
                    step = min(max(0.0, now - last), max(LOCK_WAIT_STEP_S, 5 * self.poll)) if last else 0.0
                    s["lock_wait_s"] = float(s.get("lock_wait_s") or 0) + step
                    s["wait_since_t"] = now
                    if lw and s["lock_wait_s"] > lw:
                        self.finish(e, "BLOCKED", None, f"lock-wait ran out: {a.why}")
                        continue
                else:
                    s.pop("wait_since_t", None)
                if s.get("wait") != a.why:
                    self.log(f"waits: {a.why}", a.id)
                s["wait"] = a.why
                continue
            if self.valve or self.stop:
                continue
            if isinstance(a, bs.Grow):
                self.grow(e, a)
                continue
            if self.spawn(e, a.hosts, a.pools, a.why, predicted_end=a.end):
                started += 1
                s.pop("wait_since_t", None)
                m = re.search(r"live join on ([^;]+)", a.why or "")
                if m:
                    for h in m.group(1).split(","):
                        self.live_joins[h.strip()] = now
                    self.live = None   # (the probe is used up: the joined entry is not in it)
        for e in self.entries:     # resumes waiting for a session's lock
            if self.st(e.id).get("status") == "resume-wait" and not self.stop:
                self.try_resume(e)
        self.write_schedule(sim)
        return started, acts

    def live_source(self):
        """Whether a live probe can be had: pick-host on the real root, T2_BATCH_LIVE in tests."""
        return bool(os.environ.get("T2_BATCH_LIVE")) or real_root(self.root)

    def grow(self, e, a):
        """§4.5's one exception to fixed hosts: an ht1 entry takes one more host.  The claim is
        appended to `placed` (a grow line) and the state's hosts; the entry's watcher
        (batchkinds.py ht1) reads it and starts the runner there (`ht1-farm.sh RUN --join`)."""
        s = self.st(e.id)
        d = self.edir(e.id)
        with open(os.path.join(d, "placed"), "a", encoding="utf-8") as f:
            f.write("grow " + json.dumps({"host": a.host, "t": time.time(), "why": a.why, "predicted_end": a.end},
                                         sort_keys=True) + "\n")
        s["hosts"] = list(s.get("hosts") or []) + [a.host]
        s["predicted_end"] = a.end
        self.log(f"grows onto {a.host} ({a.why})", e.id)
        starter = getattr(self, "grow_runner", None)
        if starter is not None:
            starter(e, a.host)

    def write_schedule(self, sim):
        write_text(os.path.join(self.bdir, "schedule.txt"),
                   render_schedule(self.batch, sim, self.hosts, sim.now, live=self.state))

    # -- beacon
    def beacon(self, sim_finish=None):
        now = time.time()
        running = []
        for e in self.entries:
            s = self.st(e.id)
            if s.get("status") in LIVE:
                running.append({"entry": e.id, "hosts": s.get("hosts") or [], "pools": s.get("pools") or [],
                                "eta": s.get("predicted_end")})
        left = sum(1 for e in self.entries if self.st(e.id).get("status") not in FINAL + LIVE)
        done = sum(1 for e in self.entries if self.st(e.id).get("status") in FINAL)
        doc = {"batch": self.id, "file": self.doc.get("file"), "pid": os.getpid(), "started": self.doc.get("created"),
               "hosts": self.hosts, "running": running, "entries_left": left, "entries_done": done,
               "eta": sim_finish, "t": now}
        write_json(beacon_path(self.root), doc)
        if real_root(self.root):
            push_host_beacon(self.hosts, doc)
        self.last_tick = now

    def beacon_off(self):
        b = read_json(beacon_path(self.root)) or {}
        if b.get("pid") == os.getpid():
            try:
                os.remove(beacon_path(self.root))
            except OSError:
                pass
        if real_root(self.root):
            for th in push_host_beacon(self.hosts, None):
                th.join(25)

    # -- signals
    def on_signal(self, signum, _frame):
        name = signal.Signals(signum).name
        if self.stop is None:
            self.stop = name   # (a second signal does the same: never a kill)

    def interrupt(self):
        """§4.3: one signal to each child (t2: SIGINT, its Ctrl-C handling; cmd: SIGTERM), wait up
        to T2_BATCH_STOP_WAIT for them; never a kill."""
        self.log(f"{self.stop}: no more starts; signalling the children once")
        for e in self.entries:
            s = self.st(e.id)
            if s.get("status") not in ("starting", "running"):
                continue
            child = read_json(os.path.join(self.edir(e.id), "child")) or {}
            pid = child.get("pid")
            if not pid or e.id in self.signalled or not (e.id in self.procs or alive(child)):
                continue
            if e.kind in HOST_AUTONOMOUS:
                continue        # (§4.3: its host work goes on; resume adopts its watcher)
            sig = signal.SIGINT if e.kind == "t2" else signal.SIGTERM
            try:
                os.kill(pid, sig)
                self.signalled.add(e.id)
                self.entry_log(e.id, f"{signal.Signals(sig).name} sent (the batch stops: {self.stop})")
            except OSError:
                pass
        deadline = time.time() + env_f("T2_BATCH_STOP_WAIT", 60)
        while time.time() < deadline and self.signalled:
            self.reap()
            if not any(self.st(e.id).get("status") in ("starting", "running") and e.id in self.signalled
                       for e in self.entries):
                break
            time.sleep(min(self.poll, 0.5))
        self.save()
        left = [e.id for e in self.entries if self.st(e.id).get("status") in ("starting", "running")]
        if left:
            self.log(f"still running as the driver exits (resume adopts them): {', '.join(left)}")

    # -- the loop
    def drive(self, resume=False, lock=None):
        """The loop under the driver lock (LOCK: one cmd_file holds already, handed in)."""
        if lock is None:
            lock = DriverLock(self.root)
            try:
                lock.acquire(self.id)
            except Busy as e:
                err(str(e))
                return EXIT_REFUSED
        else:
            lock.rename(self.id)
        self.load()   # (read again under the lock: a driver that just ended may have saved newer state)
        notify.begun(self.id, f"t2 batch {os.path.basename(self.doc.get('file') or '?')}")
        old = {}
        for sgn in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            old[sgn] = signal.getsignal(sgn)
            signal.signal(sgn, self.on_signal)
        try:
            self.table = H.load()
            self.log(f"driver pid {os.getpid()} {'resumes' if resume else 'starts'} the batch")
            if resume or not self.ceilings:
                got = ceilings_of(batch_facts(self.hosts, self.root, probe=resume))
                if got:
                    self.ceilings.update(got)
                    self.doc["ceilings"] = dict(self.ceilings)
            if resume or not self.doc.get("profiles"):
                self.make_profiles()
            for n in self.notes:
                self.log(f"note: {n}")
            for e in self.entries:
                s = self.st(e.id)
                sen = self.sentinels(e)
                if s.get("status") in FINAL and sen["ended"]:
                    continue
                if sen["placed"] or sen["ended"] or s.get("status") in LIVE + ("interrupted",):
                    self.resume_one(e, sen)
                elif s.get("status") == "BLOCKED":
                    pass
                else:
                    s["status"] = "pending"
            self.save()
            return self.loop()
        except KeyboardInterrupt:
            self.stop = self.stop or "SIGINT"
            self.interrupt()
            return EXIT_INTERRUPTED
        finally:
            self.beacon_off()
            self.save()
            lock.release()
            for sgn, h in old.items():
                try:
                    signal.signal(sgn, h)
                except (TypeError, ValueError):
                    pass

    def loop(self):
        requeued_blocked = False
        finish = None
        while True:
            now = time.time()
            if self.last_turn is not None and now - self.last_turn > max(env_f("T2_BATCH_AWAY_S", AWAY_S), 10 * self.poll):
                self.log(f"the Mac was away {bf.fmt_hm(now - self.last_turn)} (asleep?): carrying on; "
                         "no lock-wait counted for it")
                for e in self.entries:
                    if self.st(e.id).get("wait_since_t"):
                        self.st(e.id)["wait_since_t"] = now
                self.live = None
            self.last_turn = now
            self.reap()
            self.discover()
            if self.stop:
                self.interrupt()
                self.log(f"stopped by {self.stop}: exit 130")
                return EXIT_INTERRUPTED
            started, acts = self.schedule(now)
            if started or now - self.last_tick >= self.tick:
                finish = self.predicted_finish(now)
                self.beacon(finish)
            self.save()
            live = [e for e in self.entries if self.st(e.id).get("status") in LIVE]
            if not live and not started:
                waiting = [a for a in acts if isinstance(a, bs.Wait)]
                if waiting and not self.valve and any(a.session for a in waiting):
                    pass   # a session holds what they need: lock-wait runs out, or it frees
                elif waiting and not self.valve:
                    for a in waiting:
                        self.finish(next(e for e in self.entries if e.id == a.id), "BLOCKED", None,
                                     f"can't start: {a.why}")
                else:
                    left = [e for e in self.entries if self.st(e.id).get("status") not in FINAL]
                    if self.valve:
                        for e in left:
                            self.finish(e, "SKIPPED", None, "the safety valve stopped the starts")
                    blocked = [e for e in self.entries if self.st(e.id).get("status") == "BLOCKED"
                               and not self.st(e.id).get("blocked_requeued")]
                    if blocked and not requeued_blocked and not self.valve:
                        requeued_blocked = True
                        for e in blocked:
                            s = self.st(e.id)
                            try:
                                os.replace(os.path.join(self.edir(e.id), "ended"),
                                           os.path.join(self.edir(e.id), f"ended.blocked.{int(now)}"))
                            except FileNotFoundError:
                                pass   # (moved by a driver that died before its save)
                            s.update(status="pending", blocked_requeued=True, lock_wait_s=0.0, note=None)
                            s.pop("wait_since_t", None)
                            self.log("BLOCKED: re-queued once at the end of the batch", e.id)
                        self.save()
                        continue
                    return self.end()
            time.sleep(self.poll)

    def predicted_finish(self, now):
        ps = self.profiles()
        sim = bs.simulate(ps, self.cap(), self.running_claims(now), now, self.sessions(), self.done_ids(),
                          bf.lock_matrix(self.entries, {p.id: p.builds for p in ps if p.builds}),
                          self.settings().get("mode") == "serial",
                          stopped={e.id for e in self.entries if self.st(e.id).get("status") == "interrupted"})
        return sim.finish

    def end(self):
        sts = {e.id: self.st(e.id).get("status") for e in self.entries}
        code = EXIT_OK if all(v == "PASS" for v in sts.values()) else EXIT_FAIL
        counts = {}
        for v in sts.values():
            counts[v or "?"] = counts.get(v or "?", 0) + 1
        notify.outcome(self.id, ", ".join(f"{n} {v}" for v, n in sorted(counts.items())))
        self.state["exit"] = code
        self.state["ended"] = time.time()
        self.save()
        from . import batchout
        try:
            batchout.finish(self.bdir, self.root, log=lambda t: self.log(t))
        except Exception as ex:   # noqa: BLE001 - the short report then; `t2 batch summary` builds it again
            self.log(f"the summary failed ({type(ex).__name__}: {ex}): the short report; `t2 batch summary {self.id}`")
            write_text(os.path.join(self.bdir, "report.txt"), render_report(self))
        self.log(f"batch ended: exit {code}")
        return code


def ssh_with_patience(prog=None):
    """The T2_SSH a child gets (§6.4 item 3): its ssh program (T2_SSH, else ssh) with
    ServerAliveCountMax=4 and ConnectionAttempts=3 ahead of hostrun's own options, so a brief
    Wi-Fi drop costs a retry, not a cell.  Once: a T2_SSH that has them already is kept."""
    prog = prog or "ssh"
    if "ServerAliveCountMax=" in prog:
        return prog
    return " ".join([prog] + [shlex.quote(x) for x in SSH_PATIENCE])


NO_ANSWER = (255, 124)     # hostrun.ssh: ssh itself failed, or timed out: no answer, never "ended"


def ssh_retry(call, tries=4, backoff=5.0, sleep=time.sleep):
    """CALL() -> hostrun.Result, again while it is no answer (rc 255 or 124: audit-lib.sh's rule,
    'ssh exit 255 is no answer, not ended'), with doubling BACKOFF, at most TRIES times.  The
    last result either way; never raises for a host that doesn't answer."""
    r = None
    for i in range(max(1, tries)):
        try:
            r = call()
        except Exception:   # noqa: BLE001 - no answer
            r = None
        if r is not None and getattr(r, "rc", None) not in NO_ANSWER:
            return r
        if i + 1 < tries:
            sleep(backoff * (2 ** i))
    return r


_BEACON_GEN = [0]   # push_host_beacon's calls: a write thread of an older one stops retrying


def push_host_beacon(hosts, doc, tries=3, backoff=5.0):
    """HOST_ROOT/batch-running.json on each host (DOC None: removed), in the background, best
    effort: an advisory copy for a session that has only ssh (§8.3).  Real root only.  A host
    that doesn't answer (255) is tried again with backoff (ssh_retry), then left: advisory."""
    import threading
    import types
    from . import hostrun
    from . import host_root
    _BEACON_GEN[0] += 1
    gen = _BEACON_GEN[0]

    def one(h):
        p = hostrun.host_path(host_root())
        if doc is None:
            ssh_retry(lambda: hostrun.ssh(h, cmd=f"rm -f {hostrun.rq(p)}/batch-running.json", timeout=20),
                      tries, backoff)
        else:
            data = (json.dumps(doc, sort_keys=True) + "\n").encode()
            ssh_retry(lambda: types.SimpleNamespace(rc=0) if _BEACON_GEN[0] != gen else   # (a newer push, or off)
                      hostrun.ssh(h, cmd=f"mkdir -p {hostrun.rq(p)} && cat > {hostrun.rq(p)}/batch-running.json",
                                  input=data, timeout=20), tries, backoff)

    ths = [threading.Thread(target=one, args=(h,), daemon=True) for h in hosts]
    for th in ths:
        th.start()
    return ths


# --- rendering ------------------------------------------------------------------------------------------

def _rel(t, now):
    return "+" + bf.fmt_hm(max(0.0, t - now)) if t is not None else "-"


def render_schedule(batch, sim, hosts, now, live=None, serial_finish=None, refused=(), notes=()):
    """§4's plan table: entries in start order with hosts (an ht1's grows as +HOST), pools, start,
    end, wall, waits for; the busy line, the predicted finish and what mode serial would take."""
    st = (live or {}).get("entries") or {}
    s = batch.settings
    lines = [f"batch {batch.name}  hosts {' '.join(hosts)}  mode {s.get('mode')}"
             + (f"  round {s['round']}" if s.get("round") else "")]
    ents = batch.entries
    first = min((e.pos for e in ents), default=0)
    for m in batch.manual:
        if m.get("pos", 0) < first:
            lines.append(f"manual  {m['text']}")
    lines.append(f"{'entry':<34} {'hosts':<22} {'pools':<15} {'start':>6} {'end':>6} {'wall':>6}  waits for")
    seen = set()
    for r in sim.rows:
        hs = " ".join(list(r.hosts) + [f"+{g[0]}" for g in getattr(r, "grows", ())]) or "mac"
        hs = hs if len(hs) <= 22 else hs[:21] + "…"
        status = (st.get(r.id) or {}).get("status")
        why = r.why if r.why else "-"
        if status in FINAL:
            why = status.lower() if why == "-" else why
        lines.append(f"{r.id[:34]:<34} {hs:<22} {' '.join(r.pools) or '-':<15} {_rel(r.start, now):>6} "
                     f"{_rel(r.end, now):>6} {bf.fmt_hm(r.wall):>6}  {why}")
        seen.add(r.id)
    for e in ents:
        if e.id in seen:
            continue
        x = st.get(e.id) or {}
        why = sim.stuck.get(e.id) or x.get("note") or x.get("status") or "-"
        lines.append(f"{e.id[:34]:<34} {'-':<22} {' '.join(sorted(bf.pools_of(e))) or '-':<15} {'-':>6} {'-':>6} "
                     f"{'-':>6}  {why}")
    for m in batch.manual:
        if m.get("pos", 0) >= first:
            lines.append(f"manual  {m['text']}")
    for x in refused:
        lines.append(f"refused {x}")
    lines.append("busy    " + "  ".join(f"{h} {bf.fmt_hm(max(0.0, sim.busy.get(h, now) - now))}" for h in hosts))
    fin = f"finish  +{bf.fmt_hm(sim.finish - now)} ({time.strftime('%H:%MZ', time.gmtime(sim.finish))} if started now)"
    if serial_finish is not None:
        fin += f"; mode serial: +{bf.fmt_hm(serial_finish - now)}"
    lines.append(fin)
    for n in notes:
        lines.append(f"note    {n}")
    srcs = sorted({r.src for r in sim.rows})
    if "default" in srcs:
        lines.append("note    walls marked default are the kind's default (no est=, no history): "
                     "the plan's times are rough")
    return "\n".join(lines) + "\n"


def render_report(d):
    """The batch's short report.txt (the full one is piece B5's)."""
    lines = [f"batch {d.id}  file {d.doc.get('file')}  hosts {' '.join(d.hosts)}"]
    c = {}
    for e in d.entries:
        s = d.st(e.id)
        c[s.get("status")] = c.get(s.get("status"), 0) + 1
        wall = (s.get("ended") - s.get("started")) if isinstance(s.get("ended"), (int, float)) and \
            isinstance(s.get("started"), (int, float)) else None
        lines.append(f"{e.id:<40} {s.get('status') or '-':<10} exit {s.get('exit') if s.get('exit') is not None else '-':<4} "
                     f"{bf.fmt_hm(wall):>6}  {s.get('run') or '-'}" + (f"  {s['note']}" if s.get("note") else ""))
    lines.append("entries: " + ", ".join(f"{v} {k}" for k, v in sorted(c.items(), key=lambda kv: str(kv[0]))))
    for m in d.batch.manual:
        lines.append(f"manual (not run): {m['text']}")
    lines.append(f"exit {d.state.get('exit')}")
    return "\n".join(lines) + "\n"


# --- the CLI ---------------------------------------------------------------------------------------------

class Usage(Exception):
    pass


USAGE = ("t2 batch FILE [plan] [--hosts H,H] [--prev BATCH] [--id NAME] [--json] [--dirty-ok] [--allow-update] "
         "[--reparse] | t2 batch resume BATCH | t2 batch status | t2 batch ls [--last N] | "
         "t2 batch report BATCH [--prev BATCH] [--json] | t2 batch summary BATCH [--no-fetch] | "
         "t2 batch log BATCH [ENTRY:]CELL [--tail N] [--head N] [--file NAME] [--list]")


def take_opts(argv, spec):
    opts, rest, i = {}, [], 0
    while i < len(argv):
        a = argv[i]
        name, eq, val = a.partition("=")
        if name in spec:
            if spec[name]:
                if not eq:
                    i += 1
                    if i >= len(argv):
                        raise Usage(f"{name} needs a value")
                    val = argv[i]
                opts[name] = val
            else:
                opts[name] = True
        elif a.startswith("--"):
            raise Usage(f"no option {a}: {USAGE}")
        else:
            rest.append(a)
        i += 1
    return opts, rest


def known_names(checkout, hosts):
    """words.parse's names (plan.known_names over a light ctx: local files only), or None."""
    try:
        from . import plan as planmod
        return planmod.known_names(planmod.Ctx(checkout, hosts=list(hosts), options={}))
    except Exception as e:   # noqa: BLE001 - the words are then checked for shape only
        err(f"the word names can't be read ({type(e).__name__}: {e}): t2 words are checked for shape only")
        return None


def batch_hosts(batch, opts, table):
    if opts.get("--hosts"):
        return bf.host_list(opts["--hosts"])
    return list(batch.settings.get("hosts") or H.defaults(table))


def main(argv, checkout):
    try:
        if not argv or argv[0] in ("-h", "--help", "help"):
            say(USAGE)
            return EXIT_OK if argv else EXIT_REFUSED
        sub = argv[0]
        if sub == "resume":
            return cmd_resume(argv[1:], checkout)
        if sub == "status":
            return cmd_status(argv[1:], checkout)
        if sub == "ls":
            return cmd_ls(argv[1:])
        if sub in ("report", "summary", "log"):
            from . import batchout
            return {"report": batchout.cmd_report, "summary": batchout.cmd_summary,
                    "log": batchout.cmd_log}[sub](argv[1:], default_mac_root())
        return cmd_file(argv, checkout)
    except Usage as e:
        err(str(e))
        return EXIT_REFUSED
    except bf.BatchError as e:
        for x in e.errors:
            err(x)
        return EXIT_REFUSED


def cmd_file(argv, checkout):
    opts, rest = take_opts(argv, {"--hosts": True, "--prev": True, "--id": True, "--json": False,
                                  "--dirty-ok": False, "--allow-update": False, "--reparse": False})
    if not rest or len(rest) > 2 or (len(rest) == 2 and rest[1] != "plan"):
        raise Usage(USAGE)
    path = find_file(rest[0])
    if path is None:
        raise Usage(f"no batch file {rest[0]}")
    batch = parse_file(path)
    table = H.load()
    hosts = batch_hosts(batch, opts, table)
    known = known_names(checkout, [h for h in hosts if h in H.known(table)] or list(H.defaults(table)))
    refused = bf.refusals(batch, table, known, bool(opts.get("--allow-update")), hosts)
    if len(rest) == 2:
        return cmd_plan(batch, hosts, table, refused, opts, path, checkout, known)
    root = default_mac_root()
    bid = opts.get("--id")
    if bid is not None and (not NAME_RE.match(bid) or bid.startswith(".")):
        raise Usage(f"--id: a name of letters, digits, '.', '_', '-', not {bid!r}")
    # The driver lock first (§6.2): nothing below (a --reparse, a new batch dir) while another
    # driver runs; held on into drive().
    lock = DriverLock(root)
    try:
        lock.acquire(bid or "(starting)")
    except Busy as ex:
        err(str(ex))
        return EXIT_REFUSED
    try:
        return _cmd_file_locked(batch, path, hosts, table, known, refused, opts, checkout, root, bid, lock)
    finally:
        lock.release()   # (drive() released it already, or a refusal before it)


def _cmd_file_locked(batch, path, hosts, table, known, refused, opts, checkout, root, bid, lock):
    existing = bid if bid and os.path.isfile(os.path.join(batches_dir(root), bid, "batch.json")) else \
        (None if bid else unfinished_of(path, root))
    if existing:
        bdir = batch_dir(existing, root)
        doc = read_json(os.path.join(bdir, "batch.json")) or {}
        if os.path.exists(os.path.join(bdir, "report.txt")):
            err(f"batch {existing} has finished (its report.txt): --id NAME for a new one")
            return EXIT_REFUSED
        if doc.get("digest") != batch.digest:
            if not opts.get("--reparse"):
                old = bf.Batch.from_json(doc["batch"])
                err(f"{path} changed since batch {existing} began: {bf.diff_lines(old.lines, batch.lines) or 'its includes changed'}; "
                    "`--reparse` drops the state of every entry whose id or arguments moved and keeps the rest")
                return EXIT_REFUSED
            if refused:
                return refuse(refused)
            reparse(bdir, doc, batch, known)
        say(f"t2 batch: continuing {existing}")
        return Driver(bdir, checkout, root).drive(resume=True, lock=lock)
    if refused:
        return refuse(refused)
    facts = {}
    bad = preflight(batch, hosts, table, checkout, root, opts, facts, locked=True)
    if bad:
        return refuse(bad, "preflight refuses")
    bid = bid or new_batch_id()
    bdir = batch_dir(bid, root)
    make_dir(bdir, bid, batch, path, hosts, known, opts, ceilings_of(facts or batch_facts(hosts, root)))
    say(f"t2 batch: {bid} ({path}), in {bdir}")
    return Driver(bdir, checkout, root).drive(lock=lock)


def refuse(items, head="refused"):
    for x in items:
        err(f"{head}: {x}")
    return EXIT_REFUSED


def preflight(batch, hosts, table, checkout, root, opts, facts_out=None, locked=False):
    """§6.1 before anything starts: every unmet item.  FACTS_OUT: the probed hosts' facts.
    LOCKED: the caller holds the driver lock, so no other driver runs and a beacon is stale."""
    from . import run as runmod
    bad = []
    for e in batch.entries:
        if e.kind not in DRIVEN_KINDS:
            bad.append(f"{e.id}: {e.kind} entries are not driven")
        elif e.kind == "stic1" and not e.args[0][:1].isalnum():
            bad.append(f"{e.id}: stic1 ROUND [GUEST...]: {e.args[0]!r} is not a round")
        if e.kind in KIND_TOOL_ENV and not real_root(root) and not os.environ.get(KIND_TOOL_ENV[e.kind]):
            bad.append(f"{e.id}: a test MAC_ROOT runs no real {e.kind} tool (it reaches the farm): "
                       f"{KIND_TOOL_ENV[e.kind]} names its stub")
    if not opts.get("--dirty-ok"):
        br = subprocess.run(["git", "-C", checkout, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True)
        st = subprocess.run(["git", "-C", checkout, "status", "--porcelain"], capture_output=True, text=True)
        if br.stdout.strip() != "main":
            bad.append(f"the checkout is on {br.stdout.strip() or '?'}, not main (--dirty-ok to go on)")
        if st.returncode != 0 or st.stdout.strip():
            n = len(st.stdout.strip().splitlines())
            bad.append(f"the checkout has {n} uncommitted change(s): a PASS from an uncommitted copy doesn't count "
                       "(--dirty-ok to go on anyway)")
    else:
        say("t2 batch: --dirty-ok: rows from an uncommitted tree count only once the same diff is committed")
    b = read_beacon(root) if not locked else None
    if b:
        bad.append(f"batch {b.get('batch')} is running (pid {b.get('pid')})")
    fams = set()
    for e in batch.entries:
        fams |= bf.locks_of(e)
    for n in sorted(fams):
        lk = runmod.FamilyLock(n, root)
        if lk.held():
            h = lk.holder() or {}
            bad.append(f"the {n} lock is held: run {h.get('run', '?')}, pid {h.get('pid', '?')}")
        if n in runmod.RUN_PREFIX:
            for r in runmod.unfinished_runs(n, root):
                bad.append(f"run {r} of {n} is unfinished: `{runmod.resume_hint(r, root)}` first")
    try:
        os.makedirs(batches_dir(root), exist_ok=True)
        free = shutil.disk_usage(batches_dir(root)).free / 1e9
        need = env_f("T2_BATCH_MIN_FREE_GB", 5)
        if free < need:
            bad.append(f"{root} has {free:.1f} GB free (< {need:.0f} GB for the fetched logs)")
    except OSError as ex:
        bad.append(f"{root}: {ex}")
    host_using = [h for h in hosts if any(bf.pools_of(e) for e in batch.entries)]
    if host_using and (real_root(root) or os.environ.get("T2_BATCH_HOST_PREFLIGHT") == "1"):
        bad += host_preflight(host_using, checkout, say, facts_out)
    return bad


def make_dir(bdir, bid, batch, path, hosts, known, opts, ceilings=None):
    os.makedirs(os.path.join(bdir, "entries"), exist_ok=False)
    args = {e.id: bf.effective_args(e, batch.settings, known) for e in batch.entries if e.kind == "t2"}
    doc = {"v": 1, "id": bid, "file": path, "digest": batch.digest, "created": time.time(), "hosts": hosts,
           "batch": batch.to_json(), "args": args,
           "options": {"dirty_ok": bool(opts.get("--dirty-ok")), "allow_update": bool(opts.get("--allow-update"))},
           "prev": opts.get("--prev") or batch.settings.get("prev"), "ceilings": dict(ceilings or {})}
    write_json(os.path.join(bdir, "batch.json"), doc)
    write_json(os.path.join(bdir, "state.json"), {"entries": {e.id: {"status": "pending", "attempts": 0, "resumes": 0,
                                                                     "lock_wait_s": 0.0} for e in batch.entries}})
    with open(os.path.join(bdir, "batch.log"), "a", encoding="utf-8") as f:
        f.write(f"{utc_now()} batch {bid} of {path}: {len(batch.entries)} entries on {','.join(hosts)}\n")


def reparse(bdir, doc, batch, known):
    """--reparse: the new batch frozen; the state of every entry whose id or arguments moved dropped
    (moved to entries/.dropped/), the rest kept."""
    old = {e.id: e for e in bf.Batch.from_json(doc["batch"]).entries}
    st = read_json(os.path.join(bdir, "state.json")) or {"entries": {}}
    keep = {}
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    for e in batch.entries:
        o = old.get(e.id)
        if o is not None and o.same_as(e) and e.id in st["entries"]:
            keep[e.id] = st["entries"][e.id]
    for eid in old:
        if eid in keep:
            continue
        src = os.path.join(bdir, "entries", eid)
        if os.path.isdir(src):
            os.makedirs(os.path.join(bdir, "entries", ".dropped"), exist_ok=True)
            os.replace(src, os.path.join(bdir, "entries", ".dropped", f"{eid}.{stamp}"))
    st["entries"] = {e.id: keep.get(e.id) or {"status": "pending", "attempts": 0, "resumes": 0, "lock_wait_s": 0.0}
                     for e in batch.entries}
    doc.update(batch=batch.to_json(), digest=batch.digest,
               args={e.id: bf.effective_args(e, batch.settings, known) for e in batch.entries if e.kind == "t2"})
    write_json(os.path.join(bdir, "batch.json"), doc)
    write_json(os.path.join(bdir, "state.json"), st)
    with open(os.path.join(bdir, "batch.log"), "a", encoding="utf-8") as f:
        f.write(f"{utc_now()} --reparse: kept {', '.join(sorted(keep)) or 'nothing'}; "
                f"dropped {', '.join(sorted(set(old) - set(keep))) or 'nothing'}\n")


def plan_schedule(batch, hosts, table, checkout, root=None, path=None, facts=None, planner=None, now=None,
                  known=None):
    """(schedule, serial schedule, profiles, cap, notes): what `t2 batch FILE plan` prints.  FACTS
    give the ceilings (batch_facts: the probe as `t2 WORD plan` does it, on the real root); no
    live join, ever (§4.5)."""
    from . import batchprof as bp
    now = time.time() if now is None else now
    notes = []
    cap = bs.capacity(table, hosts, facts)
    planner = planner if planner is not None else t2_planner(batch, hosts, checkout, root, notes)
    hist = (lambda eid: history_walls(root, path, eid)) if path else (lambda eid: [])
    args = {e.id: bf.effective_args(e, batch.settings, known) for e in batch.entries if e.kind == "t2"}
    src = bp.Sources.default(checkout, root, planner, hist, args)
    ps = bp.build(batch, hosts, cap, src)
    notes += src.notes
    conflicts = bf.lock_matrix(batch.entries, {p.id: p.builds for p in ps if p.builds})
    serial = batch.settings.get("mode") == "serial"
    sim = bs.simulate(ps, cap, [], now, (), (), conflicts, serial)
    ser = bs.simulate(ps, cap, [], now, (), (), conflicts, True)
    for h in hosts:
        if cap[h].ceiling is None:
            notes.append(f"{h}'s cmp-run.sh ceiling is not known (no facts): it takes one entry at a time")
    return sim, ser, ps, cap, notes


def cmd_plan(batch, hosts, table, refused, opts, path, checkout=None, known=None):
    now = time.time()
    root = default_mac_root()
    checkout = checkout or os.path.dirname(os.path.dirname(T2_DIR))
    notes = []
    planner = t2_planner(batch, hosts, checkout, root, notes)
    facts = None
    if planner is not None:     # (one probe: the planner's ctx has every host's facts)
        try:
            facts = {h: f for h, f in planner.ctx().facts.items() if h in hosts}
        except Exception as ex:   # noqa: BLE001 - no plans then: est=, history, default
            notes.append(f"the t2 words can't be planned ({type(ex).__name__}: {ex}): est=, history or defaults")
            planner = False
    if facts is None:
        facts = batch_facts(hosts, root, probe=True)
    sim, ser, ps, cap, more = plan_schedule(batch, hosts, table, checkout, root, path, facts,
                                            planner=planner if planner is not None else False, now=now, known=known)
    notes += more
    if opts.get("--json"):
        say(json.dumps({"batch": batch.to_json(), "hosts": hosts, "schedule": sim.to_json(),
                        "serial_finish": ser.finish, "refused": refused, "notes": notes,
                        "capacity": {h: c.to_json() for h, c in cap.items()},
                        "profiles": [p.to_json() for p in ps]}, indent=1, sort_keys=True))
    else:
        sys.stdout.write(render_schedule(batch, sim, hosts, now, serial_finish=ser.finish, refused=refused,
                                         notes=notes))
        sys.stdout.flush()
    return EXIT_REFUSED if refused else EXIT_OK


def cmd_resume(argv, checkout):
    opts, rest = take_opts(argv, {})
    if len(rest) != 1:
        raise Usage("t2 batch resume BATCH")
    root = default_mac_root()
    bdir = batch_dir(rest[0], root)
    if not os.path.isfile(os.path.join(bdir, "batch.json")):
        err(f"no batch {rest[0]} in {batches_dir(root)}")
        return EXIT_REFUSED
    if os.path.exists(os.path.join(bdir, "report.txt")):
        st = read_json(os.path.join(bdir, "state.json"), {}) or {}
        say(f"t2 batch: {os.path.basename(bdir)} has finished (exit {st.get('exit')}): nothing to resume")
        return int(st.get("exit") or 0)
    return Driver(bdir, checkout, root).drive(resume=True)


def cmd_status(argv, checkout):
    opts, rest = take_opts(argv, {"--json": False})
    root = default_mac_root()
    b = read_beacon(root)
    bid = rest[0] if rest else (b or {}).get("batch")
    if not bid:
        names = [n for n in list_batches(root) if not os.path.exists(os.path.join(batches_dir(root), n, "report.txt"))]
        if not names:
            say("no batch is running")
            return EXIT_OK
        bid = names[-1]
        say(f"no batch is running; the newest unfinished one is {bid} (`t2 batch resume {bid}`)")
    bdir = batch_dir(bid, root)
    if not os.path.isfile(os.path.join(bdir, "batch.json")):
        err(f"no batch {bid}")
        return EXIT_REFUSED
    if b and b.get("batch") == bid:
        say(beacon_line(b))
    d = Driver(bdir, checkout, root)
    d.table = H.load()
    now = time.time()
    done = d.done_ids()
    ps = d.profiles()
    sim = bs.simulate(ps, d.cap(), d.running_claims(now), now, d.sessions(), done, bf.lock_matrix(d.entries),
                      d.settings().get("mode") == "serial",
                      stopped={e.id for e in d.entries if d.st(e.id).get("status") == "interrupted"})
    if opts.get("--json"):
        say(json.dumps({"batch": bid, "beacon": b, "state": d.state, "schedule": sim.to_json()}, indent=1, sort_keys=True))
        return EXIT_OK
    sys.stdout.write(render_schedule(d.batch, sim, d.hosts, now, live=d.state))
    for e in d.entries:
        s = d.st(e.id)
        if s.get("status") not in ("pending",):
            say(f"  {e.id:<40} {s.get('status'):<12} {s.get('run') or '-':<26} {s.get('wait') or s.get('note') or ''}")
    return EXIT_OK


def cmd_ls(argv):
    opts, rest = take_opts(argv, {"--last": True})
    if rest:
        raise Usage("t2 batch ls [--last N]")
    root = default_mac_root()
    names = list_batches(root)
    if opts.get("--last"):
        try:
            n = int(opts["--last"])
        except ValueError:
            raise Usage("--last takes a number") from None
        names = names[-n:] if n > 0 else []
    b = read_beacon(root)
    for n in names:
        d = os.path.join(batches_dir(root), n)
        bj = read_json(os.path.join(d, "batch.json"), {}) or {}
        st = read_json(os.path.join(d, "state.json"), {}) or {}
        c = {}
        for v in (st.get("entries") or {}).values():
            c[v.get("status")] = c.get(v.get("status"), 0) + 1
        state = "running" if b and b.get("batch") == n else \
            (f"exit {st.get('exit')}" if os.path.exists(os.path.join(d, "report.txt")) else "unfinished")
        t = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(bj.get("created") or 0))
        say(f"{n:<26} {t}  {os.path.basename(bj.get('file') or '?'):<28} {state:<11} "
            + ", ".join(f"{v} {k}" for k, v in sorted(c.items(), key=lambda kv: str(kv[0]))))
    if not names:
        say("no batches")
    return EXIT_OK
