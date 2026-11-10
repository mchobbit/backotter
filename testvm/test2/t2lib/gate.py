"""GateTest2 (G1): the layers below whole-guest runs, no VMs (docs/design/test2.md
"GateTest2 (new): no VMs"; contract 2.1; rulings R13, R18, R21, R23, R25).

    run(ctx, plan) -> RunResult          GateTest2 and GateTest2All: the plan's due gate
                                         cells, the fast layers one after another, then the
                                         slow ones on every host at once
    run_layers(ctx, names, part=None, plan=None)
                                         those layers only; SticTest2 and HoltTest2 call it
                                         for their family's fast part, and the slow part
                                         beside their guests.  {"cells", "rows", "stops",
                                         "stop", "ok", "exit"}
    family_layers(family) -> {"fast": [...], "slow": [...]}
                                         stic: fork, fork-root and bin fast, loop slow;
                                         holt: holt fast; gate: every layer

The layers, and their inputs in the spec format keys.py reads, are
testvm/test2/gate/layers.json.  How a layer's output becomes its needs and verdict is
gate_judge.py, a judge input of every layer (a rule change makes the layers due); this
file is the runner, keyed only by gate:selftest.  Each layer runs, its output is parsed into needs, and it
gets one row in the ledger of the host that ran it (gate:selftest: the Mac's mac.jsonl).
A FAIL, or a run that didn't finish (HARNESS), runs once more as attempt 2 under RUN-r2;
a pass then is FLAKY-FAIL.  Never beside a job that may still run: a host job given up
(no room after its wait, past its timeout, the host not answering) gets a TERM to its
process group, and when it can't be seen gone there is no attempt 2.  A fast layer that doesn't pass answers stop-family (no guest
of the calling family starts), a slow one stop-starts.

    gate:holt          holt-test.sh on Chopin (pinned there: the raw dumps of
                       /bkoscratch/test2/h1-raw are), under cmp-run.sh --reserve 2 4, in
                       RAM: cargo test (--skip golden_), the golden plans in report mode
                       (diff.json goes into extra.plan_diff; a DIFF passes, R23) with their
                       invariants, and redact_dumps over h1-raw and the corpus
    gate:fork:NAME     testvm/audit/fork-tests.sh --force, run on the Mac (restic on
                       Chopin, rustic_core and rustic on Cinqcent), BKO_AUDIT_TAG=RUN, the
                       job's WEAR_TAG gate:RUN:fork@HOST; NAME.report judged against
                       gate/baseline.json (R13)
    gate:fork-root:restic
                       fork-root.sh on Chopin: the reviewed root-only tests of
                       restic-root-tests.txt as root, in RAM (R25: the rest stays m's)
    gate:bin:abi|static|version
                       bincheck.sh on one host, on the set under test (and on bko-holt's
                       HOST_ROOT/holt-bin/CODE); version's agreement across OSes, with
                       baseline.json's classed exceptions
    gate:loop:linux@HOST, gate:loop:sparse@HOST
                       loop.sh: run-all.sh's linux or sparse steps from the copy, under
                       cmp-run.sh --reserve D M (D from the last rows' RAM peaks)
    gate:selftest      on the Mac: t2 self-test and drive.py --self-test

On a host everything runs from the run's private copy, detached (setsid), its records in
HOST_ROOT/runs/RUN/gate/LAYER/ (scratch only in reserved RAM directories), and its inputs
are measured there afterwards, from the copy, with the host's facts, through the copy's
own t2lib (hostrun.HostCtx, as t2-host's row builders are).  A layer holds
MAC_ROOT/runs/.lock-gate-LAYER while it runs, so that two runs never run one layer at
once (R21).
"""

import fcntl
import json
import os
import random
import re
import shlex
import subprocess
import sys
import threading
import time

from . import utc_now
from .gate_judge import (  # noqa: F401 - the judge's names, as gate.X too
    NEED_FAIL, TEXT_MAX, ROOTISH, cut, verdict_of, counts_of, Outcome, harness, CARGO_RESULT,
    CARGO_FAILED, CARGO_ERROR, parse_cargo, parse_steps, parse_kv, parse_diff_json, REDACT_LINE,
    parse_redact, holt_outcome, REPORT_HEADS, REPORT_TEST, REPORT_BUILD, parse_fork_report,
    baseline_entry, fork_outcome, GO_RESULT, GO_EVENT, parse_go_v, parse_root_list,
    fork_root_outcome, BINLINE, parse_bincheck, bin_outcome, agreement, loop_outcome,
    selftest_outcome, tail, version_unchecked)

HERE = os.path.dirname(os.path.abspath(__file__))
TEST2 = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(TEST2))
LAYERS_PATH = "testvm/test2/gate/layers.json"
BASELINE_PATH = "testvm/test2/gate/baseline.json"
ROOT_LIST_PATH = "testvm/test2/gate/restic-root-tests.txt"
HOSTS = ("chopin", "cinqcent")
FORKS = ("restic", "rustic_core", "rustic")
NOT_PASSED = ("FAIL", "FLAKY-FAIL", "HARNESS", "LOST")
CMP_RUN = "~/backotter-work/cmp-run.sh"
POLL_S = 15
LOCK_POLL_S = 10
FAST_ORDER = ("holt", "fork:restic", "fork:rustic_core", "fork:rustic", "fork-root:restic",
              "bin:abi", "bin:static", "bin:version", "selftest")


# --- the layer table -------------------------------------------------------------------

def load_layers(checkout=None):
    """layers.json of CHECKOUT (default: the tree this module is in)."""
    with open(os.path.join(checkout or REPO, LAYERS_PATH), encoding="utf-8") as f:
        return json.load(f)


def layer_entries(layers):
    """[(name, entry)] in the file's order."""
    c = layers.get("layers")
    if isinstance(c, dict):
        return list(c.items())
    return [(k, v) for k, v in layers.items() if isinstance(v, dict) and k not in
            ("plumbing", "soft", "defaults", "kinds", "policy")]


def layer_entry(layers, name):
    for n, e in layer_entries(layers):
        if n == name:
            return e
    raise KeyError(f"no gate layer {name!r} in {LAYERS_PATH}")


def entry_or_empty(layers, name):
    try:
        return layer_entry(layers, name)
    except KeyError:
        return {}


def per_host(entry):
    return bool(entry.get("per_host"))


def universe(layers=None, hosts=HOSTS):
    """Every gate cell: gate:LAYER, and gate:LAYER@HOST for the per-host layers."""
    layers = layers if layers is not None else load_layers()
    out = []
    for n, e in layer_entries(layers):
        out += [f"gate:{n}@{h}" for h in hosts] if per_host(e) else [f"gate:{n}"]
    return out


def family_layers(family, layers=None):
    """{"fast": [layer...], "slow": [layer...]}: the layers whose "families" name FAMILY
    (gate: every layer), by their "part"."""
    layers = layers if layers is not None else load_layers()
    out = {"fast": [], "slow": []}
    for n, e in layer_entries(layers):
        if family == "gate" or family in (e.get("families") or []):
            out["slow" if e.get("part") == "slow" else "fast"].append(n)
    return out


def split_cell(cell):
    """gate:loop:linux@chopin -> ("loop:linux", "chopin"); gate:holt -> ("holt", None)."""
    rest = cell[5:] if cell.startswith("gate:") else cell
    if "@" in rest:
        name, host = rest.rsplit("@", 1)
        return name, host
    return rest, None


def layer_dir(name):
    """loop:linux@chopin -> loop-linux-chopin: a layer's directory and lock name."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name)



def full_run(row):
    """A row whose RAM peak is a whole layer's: it finished (not HARNESS or LOST), and no
    T2_LOOP_LINUX_FS or T2_LOOP_SPARSE_FS cut it down."""
    if row.get("verdict") in ("HARNESS", "LOST"):
        return False
    lp = (row.get("meta") or {}).get("loop") or {}
    return all(lp.get(k) in (None, "default") for k in ("linux_fs", "sparse_fs"))


def reserve_for(entry, rows, cell):
    """(DISK_GIB, MEM_GIB) of a layer's cmp-run.sh --reserve: the entry's "reserve"; with
    "peak_reserve", once its rows have RAM peaks, 1.1 x the largest of the last 3 plus 0.25
    GiB (contract 2.1).  Only whole runs' peaks count (full_run)."""
    d, m = (list(entry.get("reserve") or [2, 4]) + [4])[:2]
    if entry.get("peak_reserve"):
        mine = sorted((r for r in rows if r.get("cell") == cell and r.get("kind") == "run" and full_run(r)
                       and isinstance(r.get("ram_peak_kib"), int) and r["ram_peak_kib"] > 0),
                      key=lambda r: (r.get("t") or "", r.get("id") or ""), reverse=True)[:3]
        if mine:
            d = round(1.1 * max(r["ram_peak_kib"] for r in mine) / 1048576 + 0.25, 1)
    return float(d), float(m)


# --- the run's environment ---------------------------------------------------------------

class RunEnv:
    """What the gate needs to run: the run (RunState as ctx.run, run.py's), its word,
    roots and copy on each host, the set under test, the plan and the host I/O."""

    def __init__(self, ctx, plan=None, io=None, log=None):
        from . import hostrun, mac_root
        self.ctx, self.plan = ctx, plan
        rs = getattr(ctx, "run", None)
        self.rs = rs if rs is not None and hasattr(rs, "run") else None
        self.io = io or HostIO()
        self.checkout = os.path.abspath(getattr(ctx, "checkout", None) or REPO)
        self.run = self.rs.run if self.rs else new_run_id()
        w = getattr(self.rs, "word", None) if self.rs else None
        w = w or (getattr(plan, "word", None) if plan is not None else None)
        self.word = getattr(w, "text", None) or (w if isinstance(w, str) else "GateTest2")
        self.family = getattr(self.rs, "family", None) or "gate"
        self.hosts = list(getattr(ctx, "hosts", None) or HOSTS)
        self.facts = dict(getattr(ctx, "facts", None) or {})
        self.mac_root = getattr(self.rs, "mac_root", None) or mac_root()
        hr = getattr(self.rs, "host_root", None) or os.environ.get("T2_HOST_ROOT") or "/bkoscratch/test2"
        self.host_root = hostrun.host_path(hr)
        self.copy_root = hostrun.host_path(os.environ.get("T2_COPY_ROOT") or "~/backotter-work/t2/copy")
        self.rows = list(getattr(ctx, "ledger", None) or [])
        self.layers = load_layers(self.checkout)
        try:
            with open(os.path.join(self.checkout, BASELINE_PATH), encoding="utf-8") as f:
                self.baseline = json.load(f)
        except (OSError, ValueError):
            self.baseline = {}
        s = getattr(plan, "set", None) if plan is not None else None
        if not s and self.rs is not None:
            s = getattr(getattr(self.rs, "plan", None), "set", None)
        self.set = dict(s or {"name": "bko-bin"})
        self.set_name = self.set.get("name") or "bko-bin"
        self.set_dir = self.set.get("dir") or ("~/backotter-work/bko-bin" if self.set_name == "bko-bin"
                                               else f"~/backotter-work/{self.set_name}/bin")
        self.copy = {}
        for h, c in ((getattr(self.rs, "copy", None) or {}) if self.rs else {}).items():
            self.copy[h] = c.get("path") if isinstance(c, dict) else getattr(c, "path", c)
        self.copy_id = next((c.get("id") for c in (getattr(self.rs, "copy", None) or {}).values()
                             if isinstance(c, dict) and c.get("id")), None) if self.rs else None
        self._log = log or (getattr(self.rs, "log", None) if self.rs else None) or _stderr

    def log(self, text):
        self._log("gate: " + text)

    def attempt_run(self, attempt):
        return self.run if attempt == 1 else f"{self.run}-r{attempt}"

    def out_dir(self, name, attempt):
        return f"{self.host_root.rstrip('/')}/runs/{self.attempt_run(attempt)}/gate/{layer_dir(name)}"

    def ram_dir(self, name, attempt):
        return f"/mnt/bko-ram/{self.attempt_run(attempt)}-{layer_dir(name)}"

    def copy_path(self, host):
        p = self.copy.get(host)
        if not p:
            raise RuntimeError(f"no private copy on {host} (run.py's lifecycle makes it before the runner)")
        return p

    def plan_cell(self, cell):
        for pc in (getattr(self.plan, "cells", None) or []) if self.plan is not None else []:
            if getattr(pc, "cell", None) == cell:
                return pc
        return None

    def host_for(self, cell):
        """The host a cell runs on: its @HOST, its pin, the plan's placement, else the
        first host with facts."""
        name, h = split_cell(cell)
        if name == "selftest":
            return "mac"
        if h:
            return h
        pin = entry_or_empty(self.layers, name).get("pin")
        if pin:
            return pin
        pc = self.plan_cell(cell)
        for x in (getattr(pc, "placed", None), getattr(pc, "host", None)):
            if x and x in self.hosts:
                return x
        return next((x for x in self.hosts if x in self.facts), self.hosts[0])


def new_run_id(prefix="t2g", t=None):
    return time.strftime(f"{prefix}-%Y%m%dT%H%M", time.gmtime(t or time.time())) + "-%04x" % random.getrandbits(16)


def _stderr(msg):
    sys.stderr.write(time.strftime("%H:%M:%S ") + msg + "\n")
    sys.stderr.flush()


# --- host I/O (t2lib.hostrun underneath; the unit tests give a fake) ------------------------

FETCH_SCRIPT = r'''
import json, os, sys
d = json.loads(_T2_DATA)
out, total = {}, 0
base = os.path.expanduser(d["dir"])   # a HOST_ROOT under the Mac's home reaches the host as ~/...
for n in d["names"]:
    try:
        with open(os.path.join(base, n), "rb") as f:
            b = f.read(d.get("max", 4 << 20))
    except OSError:
        continue
    total += len(b)
    if total > d.get("total", 16 << 20):
        break
    out[n] = b.decode("utf-8", "replace")
sys.stdout.write(json.dumps(out))
'''

# Measures cells from the run's copy with this host's facts, and appends rows, through
# the copy's own t2lib (hostrun.HostCtx: what t2-host's row builders get).
HOST_SCRIPT = r'''
import json, os, sys
d = json.loads(_T2_DATA)
for k, v in (d.get("env") or {}).items():
    os.environ[k] = v
sys.path.insert(0, d["test2"])
from t2lib import hostrun
ctx = hostrun.HostCtx(tool=os.path.join(d["test2"], "t2-host"))
out = {"tree": ctx.tree_info()}
if d.get("measure"):
    facts = ctx.facts(d.get("want"))
    src = ctx.source()
    out["measure"] = {}
    for c in d["measure"]:
        try:
            out["measure"][c] = ctx.measure(c, opts=d.get("opts"), src=src, facts=facts)
        except Exception as e:
            out["measure"][c] = {"error": "%s: %s" % (type(e).__name__, e)}
if d.get("append"):
    out["ids"] = []
    for r in d["append"]:
        try:
            out["ids"].append(list(ctx.append(ctx.row(**r))))
        except Exception as e:
            out["ids"].append(["error", "%s: %s" % (type(e).__name__, e)])
sys.stdout.write(json.dumps(out))
'''


class HostIO:
    """The gate's operations on hosts and on the Mac."""

    def __init__(self):
        from . import hostrun
        self.h = hostrun

    def sh(self, host, cmd, timeout=60, input=None):
        r = self.h.ssh(host, cmd, timeout=timeout, input=input)
        return r.rc, r.text, r.errtext

    def script(self, host, script, data, timeout=120):
        r = self.h.ssh(host, script=script, data=data, timeout=timeout)
        if r.rc != 0:
            raise RuntimeError(f"{host}: script exit {r.rc}: {r.errtext.strip()[-300:]}")
        return json.loads(r.text or "{}")

    def start(self, host, cmd, log, pidfile):
        return self.h.detached(host, cmd, log, pidfile)

    def alive(self, host, pid, needle=None):
        """True while PID runs on HOST with NEEDLE in its command line, False when the host
        says it doesn't, None when the host didn't answer (an ssh that failed is no answer)."""
        pid = int(pid)
        test = (f"test -r /proc/{pid}/cmdline && tr '\\0' ' ' < /proc/{pid}/cmdline | grep -qF -- {shlex.quote(needle)}"
                if needle else f"test -d /proc/{pid}")
        rc, text, _ = self.sh(host, f"if {test}; then echo alive; else echo gone; fi", timeout=30)
        ans = (text or "").strip()
        return True if rc == 0 and ans == "alive" else False if rc == 0 and ans == "gone" else None

    def stop(self, host, pid, needle, wait_s=300):
        """TERM the process group of a job this run started (PID leads it: setsid), only
        while PID is still that job (NEEDLE in its command line); then wait up to WAIT_S for
        the group to be gone.  True once nothing of it runs, False when it may still run.
        Never a KILL: the scripts' TERM traps clean up their pools, mounts and RAM."""
        pid = int(pid)
        if pid <= 1:
            return False
        state = self.alive(host, pid, needle)
        if state is None:
            return False
        if state:
            rc, text, _ = self.sh(host, f"[ \"$(ps -o pgid= -p {pid} | tr -d ' ')\" = {pid} ] && kill -TERM -- -{pid}"
                                        f" && echo sent", timeout=30)
            if rc != 0 or "sent" not in (text or ""):
                return False
        else:   # the leader is gone; what is left of its group is still ours (Linux reuses no
            # PID while a process group of that number lives)
            self.sh(host, f"kill -TERM -- -{pid} 2>/dev/null", timeout=30)
        t0 = time.time()
        while True:   # the group: kill -0 says "No such process" once none of it is left
            rc, text, err = self.sh(host, f"kill -0 -- -{pid} 2>&1; echo rc=$?", timeout=30)
            if rc == 0 and "rc=1" in text and "No such process" in text:
                return True
            if time.time() - t0 > wait_s:
                return False
            self.sleep(POLL_S)

    def fetch(self, host, d, names):
        """{name: text} of the files of D on HOST that are there."""
        try:
            return self.script(host, FETCH_SCRIPT, {"dir": d, "names": list(names)})
        except (RuntimeError, ValueError):
            return {}

    def local(self, argv, cwd=None, env=None, timeout=None):
        """(rc, output) of ARGV on the Mac, in a process group of its own: past TIMEOUT the
        whole group gets a TERM (fork-tests.sh's background waiters too), rc 124."""
        import signal as _sig
        try:
            p = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 start_new_session=True)
        except OSError as e:
            return 127, str(e)
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, _sig.SIGTERM)
            except OSError:
                pass
            try:
                out, err = p.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(p.pid, _sig.SIGKILL)
                except OSError:
                    pass
                out, err = p.communicate()
            return 124, f"timed out after {timeout} s\n" + (out or b"").decode("utf-8", "replace")[-2000:]
        return p.returncode, out.decode("utf-8", "replace") + err.decode("utf-8", "replace")

    def sleep(self, s):
        time.sleep(s)


def _q(p):
    """P for a remote shell; a leading ~/ stays for it to expand."""
    if p == "~":
        return '"$HOME"'
    if p.startswith("~/"):
        return '"$HOME"/' + shlex.quote(p[2:])
    return shlex.quote(p)


# --- one job on a host ----------------------------------------------------------------------

class JobFailure(str):
    """host_job's why: the text, and whether the job may still run on the host (then the
    layer isn't run again beside it)."""

    def __new__(cls, text, still_running=False):
        o = str.__new__(cls, text)
        o.still_running = still_running
        return o


UNKNOWN_MAX = 20   # polls in a row with no answer from the host before host_job gives up


def job_poll_cmd(out):
    """host_job's poll: .exit, a blank line, .began.  Exit 0 whether or not they are there yet
    (exit 1 only when the job's directory is gone): a job waiting in cmp-run.sh's line has
    neither, and that isn't 'no answer'."""
    return f"cd {_q(out)} || exit 1; cat .exit 2>/dev/null; echo; cat .began 2>/dev/null; exit 0"


def host_job(env, host, name, attempt, script_cmd, reserve=None, wear_tag=None, timeout_s=3600,
             wait_s=4 * 3600, extra_env=None, ram=None):
    """SCRIPT_CMD (a shell command, run in the copy's root) on HOST, detached, under
    ~/backotter-work/cmp-run.sh --reserve when RESERVE is (DISK, MEM), with WEAR_TAG for
    that command alone; waits for its end.  (rc or None, out_dir, times, why): why (a
    JobFailure) says what went wrong when rc is None, never empty.  TIMEOUT_S counts from
    the job's start, after the wait for room (at most WAIT_S); a job past either is stopped
    (its process group gets a TERM), and when it can't be seen gone, why.still_running."""
    out = env.out_dir(name, attempt)
    copy = env.copy_path(host)
    # In the job's command line as written ("$HOME"/'...' for a ~/ root): never a reused PID.
    needle = f"/runs/{env.attempt_run(attempt)}/gate/{layer_dir(name)}/"
    ev = dict({"PYTHONDONTWRITEBYTECODE": "1"}, **(extra_env or {}))
    exports = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in sorted(ev.items()))
    # cmp-run.sh runs its command in ~/backotter-work (the synced tree's root): back to the copy.
    inner = f"cd {_q(copy)} || exit 2; date +%s > {_q(out + '/.began')}; exec {script_cmd}"
    if reserve:
        wt = f"WEAR_TAG={shlex.quote(wear_tag)} " if wear_tag else ""
        run = f"{wt}{CMP_RUN} --reserve {reserve[0]:g} {reserve[1]:g} bash -c {shlex.quote(inner)}"
    else:
        run = f"bash -c {shlex.quote(inner)}"
    body = (f"cd {_q(copy)} || {{ echo 'no copy here' >&2; echo 2 > {_q(out + '/.exit')}; exit 2; }}; "
            f"export {exports}; date +%s > {_q(out + '/.submitted')}; "
            f"{run}; rc=$?; date +%s > {_q(out + '/.ended')}; echo $rc > {_q(out + '/.exit')}")
    rc, _, err = env.io.sh(host, f"mkdir -p {_q(out)} && cd {_q(out)} && rm -f .exit .began .submitted .ended .pid")
    if rc != 0:
        return None, out, {}, JobFailure(f"can't make {out} on {host}: {err.strip()[-150:]}")
    pid = env.io.start(host, f"bash -c {shlex.quote(body)}", out + "/job.log", out + "/.pid")
    if not pid:
        return None, out, {}, JobFailure(f"the job didn't start on {host}")
    if ram and env.rs is not None and hasattr(env.rs, "record_made"):
        try:   # for preflight's leftovers of a run that died (the job removes it itself)
            env.rs.record_made(host, ram, pid)
        except Exception:   # noqa: BLE001
            pass
    env.log(f"{host}: gate:{name} attempt {attempt} started, pid {pid}, records {out}")

    def give_up(why):
        gone = False
        if hasattr(env.io, "stop"):
            try:
                gone = env.io.stop(host, pid, needle)
            except Exception:   # noqa: BLE001
                gone = False
        env.log(f"{host}: gate:{name} attempt {attempt}: {why}; "
                + ("stopped" if gone else f"pid {pid} may still run: no rerun beside it"))
        return None, out, {}, JobFailure(f"{why}; " + ("stopped" if gone else f"pid {pid} may still run"),
                                         still_running=not gone)

    t0, began, said, unknown, ex = time.time(), None, False, 0, None
    while True:
        rc, text, _ = env.io.sh(host, job_poll_cmd(out))
        e1, _, b = (text or "").partition("\n")
        if rc == 0 and e1.strip().lstrip("-").isdigit():
            ex = int(e1.strip())
            break
        if rc == 0 and b.strip().isdigit() and began is None:
            began = time.time()
        state = env.io.alive(host, pid, needle) if rc == 0 else None
        if state is False:
            rc2, text2, _ = env.io.sh(host, f"cat {_q(out + '/.exit')} 2>/dev/null")
            if rc2 == 0 and text2.strip().lstrip("-").isdigit():
                ex = int(text2.strip())
                break
            if rc2 == 0:
                return give_up(f"the job on {host} ended without an exit status (pid {pid}; {out}/job.log)")
            state = None
        unknown = unknown + 1 if state is None else 0
        now = time.time()
        if unknown >= UNKNOWN_MAX:
            return give_up(f"no answer from {host} in {unknown} polls")
        if began is None and now - t0 > wait_s:
            return give_up(f"no room for it on {host} after {wait_s} s")
        if began is not None and now - began > timeout_s:
            return give_up(f"no end after {timeout_s} s ({out}/job.log)")
        if began is None and not said and now - t0 > 120:
            env.log(f"{host}: gate:{name} waits for room in cmp-run.sh's ledger")
            said = True
        env.io.sleep(POLL_S)
    f = env.io.fetch(host, out, [".submitted", ".began", ".ended", ".exit"])
    t = {k[1:]: int(v.strip()) for k, v in f.items() if v.strip().lstrip("-").isdigit()}
    t["exit"] = ex   # the polled exit: a fetch that failed afterwards loses only the times
    return ex, out, t, None


def job_times(t):
    """(wall_s, waits_s) of host_job's times: the waits are the time in cmp-run.sh's line."""
    sub, began, ended = t.get("submitted"), t.get("began"), t.get("ended")
    if began is None:
        return (max(ended - sub, 0) if ended and sub else 0), 0
    return max((ended or began) - began, 0), max(began - (sub or began), 0)


def _finish(o, host, out, t, tag=None):
    o.logs = f"{host}:{out}"
    o.wall_s, o.waits_s = job_times(t)
    if tag:
        o.wear = {tag: None}
    return o


# --- the layers ----------------------------------------------------------------------------

def run_holt(env, host, attempt):
    name = "holt"
    e = layer_entry(env.layers, name)
    raw = next((x.get("path") if isinstance(x, dict) else x for x in (e.get("host_inputs") or [])
                if "h1-raw" in str(x)), None)
    out = env.out_dir(name, attempt)
    cmd = (f"bash {_q(env.copy_path(host) + '/testvm/test2/gate/holt-test.sh')} --out {_q(out)} --ram {_q(env.ram_dir(name, attempt))}"
           + (f" --raw {_q(raw)}" if raw else ""))
    tag = f"gate:{env.attempt_run(attempt)}:holt"
    rc, out, t, why = host_job(env, host, name, attempt, cmd, reserve_for(e, env.rows, "gate:holt"), tag,
                               int(e.get("timeout_s", 3600)), ram=env.ram_dir(name, attempt))
    if rc is None:
        return _finish(harness(why), host, out, t, tag)
    f = env.io.fetch(host, out, ["steps.tsv", "info.txt", "cargo-test.log", "golden.log", "redact-corpus.log",
                                 "redact-raw.tsv", "plans/diff.json", "job.log"])
    if rc != 0 or "steps.tsv" not in f:
        return _finish(harness(f"holt-test.sh exit {rc}: " + tail(f.get("job.log"))), host, out, t, tag)
    diff = None
    if f.get("plans/diff.json"):
        try:
            diff = json.loads(f["plans/diff.json"])
        except ValueError:
            diff = None
    info = parse_kv(f.get("info.txt"))
    o = holt_outcome(parse_steps(f["steps.tsv"]), f, diff, f.get("redact-raw.tsv"), info)
    o.meta["holt"] = {"rustc": info.get("rustc"), "commit": info.get("commit")}
    return _finish(o, host, out, t, tag)


def fork_commits(env, names):
    """{fork: commit} the fork layers test: the backotter heads in BKO_GIT (the layers'
    git: inputs); rustic_core with rustic, which is built against it."""
    from . import bko_git
    git = getattr(env.ctx, "bko_git", None) or bko_git()
    heads = (getattr(env.ctx, "options", None) or {}).get("heads") or {}
    out = {}
    for n in sorted(set(names) | ({"rustic_core"} if "rustic" in names else set())):
        if heads.get(n):
            out[n] = heads[n]
            continue
        rc, text = env.io.local(["git", "-C", os.path.join(git, n), "rev-parse", "--verify", "-q", "backotter^{commit}"])
        if rc == 0 and re.match(r"^[0-9a-f]{40}$", text.strip()):
            out[n] = text.strip()
    return out


FRESH_SLACK_S = 600   # the hosts' clocks against the Mac's


def fork_results_date(local, name):
    """The epoch of the date= line of NAME's fetched info.txt (audit-host.sh writes it when
    its job reaches NAME's spec), or None when there's none to read."""
    import datetime
    base = os.path.join(local, "fork-tests")
    try:
        dirs = sorted(os.listdir(base))
    except OSError:
        return None
    for d in dirs:
        try:
            with open(os.path.join(base, d, "info.txt"), encoding="utf-8", errors="replace") as f:
                kv = parse_kv(f.read())
        except OSError:
            continue
        if kv.get("name") != name or not kv.get("date"):
            continue
        try:
            return datetime.datetime.strptime(kv["date"], "%Y-%m-%d %H:%M:%S %z").timestamp()
        except ValueError:
            return None
    return None


def run_forks(env, names, attempt, commits):
    """gate:fork:NAME for NAMES: fork-tests.sh --force on the Mac, one run per host (the
    hosts' jobs carry their own wear tags): {name: Outcome}.  A NAME.report is judged only
    when that host's fork-tests.sh finished (exit 0 or 1: every job of it completed) and its
    results date from this attempt: else HARNESS, whatever report there is."""
    from . import bko_git
    out = {n: harness(f"no backotter branch of {n} in BKO_GIT") for n in names if n not in commits}
    names = [n for n in names if n in commits]
    if not names:
        return out
    run = env.attempt_run(attempt)
    local = os.path.join(env.mac_root, "runs", run, "audit")
    os.makedirs(local, exist_ok=True)
    groups = {}
    for n in names:
        groups.setdefault(env.host_for(f"gate:fork:{n}"), []).append(n)
    results, lock = {}, threading.Lock()

    def one(host, ns):
        e = dict(os.environ, BKO_AUDIT_TAG=run, BKO_WEAR_TAG=f"gate:{run}:fork@{host}", BKO_AUDIT_LOCAL=local,
                 PYTHONDONTWRITEBYTECODE="1", BKO_GIT=getattr(env.ctx, "bko_git", None) or bko_git())
        hostopt = ["--restic-host", host] if ns == ["restic"] else ["--rustic-host", host]
        if "restic" in ns and len(ns) > 1:
            hostopt += ["--restic-host", host]
        argv = ["bash", os.path.join(env.checkout, "testvm", "audit", "fork-tests.sh"), "--force"] + hostopt + \
            [f"{n}={commits[n]}" for n in ns]
        env.log(f"fork-tests.sh {' '.join(argv[2:])} (job {run}-{host})")
        t0, start = time.time(), utc_now()
        r = env.io.local(argv, cwd=os.path.join(env.checkout, "testvm", "audit"), env=e, timeout=4 * 3600)
        with lock:
            results[host] = (r[0], r[1], start, t0, int(time.time() - t0))

    ths = [threading.Thread(target=one, args=(h, ns)) for h, ns in groups.items()]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    for host, ns in groups.items():
        rc, text, start, t0, wall = results.get(host, (None, "", None, time.time(), 0))
        may_run = False
        if rc not in (0, 1):    # no complete run: its job has ended only if the host says so
            L = f"backotter-work/bko-audit/logs/{run}-{host}"
            r2, ans, _ = env.io.sh(host, f"cd; if test -e {L}.exit; then echo over; elif test -e {L}.pid && "
                                         f"kill -0 \"$(cat {L}.pid)\" 2>/dev/null; then echo runs; else echo over; fi")
            may_run = not (r2 == 0 and ans.strip() == "over")
        for n in ns:
            if rc not in (0, 1):   # a job didn't complete: what's there may be an earlier run's (R13)
                o = harness(JobFailure(f"fork-tests.sh exit {rc} on {host}: " + tail(text),
                                       still_running=may_run))
            else:
                try:
                    with open(os.path.join(local, "fork-tests", f"{n}.report"), encoding="utf-8",
                              errors="replace") as f:
                        report = f.read()
                except OSError:
                    report = None
                when = fork_results_date(local, n)
                if report is not None and when is not None and when < t0 - FRESH_SLACK_S:
                    o = harness(f"{n}'s results on {host} date from "
                                f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(when))}, before this attempt")
                else:
                    o = fork_outcome(n, report, env.baseline, f"fork-tests.sh exit {rc}, no {n}.report: " + tail(text))
            o.wall_s, o.start = wall, start
            o.logs = f"{host}:~/backotter-work/bko-audit/logs/{run}-{host}.log"
            o.wear = {f"gate:{run}:fork@{host}": None}
            o.meta["commits"] = {k: v for k, v in commits.items() if k == n or (n == "rustic" and k == "rustic_core")}
            o.extra["fork_tests_exit"] = rc
            out[n] = o
        if may_run:     # its host jobs may still run and use them
            env.log(f"{host}: fork-tests.sh exit {rc} and its job may still run: "
                    f"~/backotter-work/bko-audit/bin-{run} is left for it")
            continue
        # The tools of this run's tag: fork-tests.sh copied them there (audit-lib.sh's RBIN).
        env.io.sh(host, f"rm -rf ~/backotter-work/bko-audit/bin-{shlex.quote(run)}")
    return out


def run_fork_root(env, host, attempt, commit):
    name = "fork-root:restic"
    e = layer_entry(env.layers, name)
    if not commit:
        return harness("no restic commit (no backotter branch in BKO_GIT)")
    rc, _, _ = env.io.sh(host, f"test \"$(cat /bkoscratch/bko-audit/src/restic-{commit[:12]}/.bko-src 2>/dev/null)\""
                               f" = {commit}")
    if rc != 0:   # fork:restic didn't run here: the tree as fork-tests.sh ships it
        from . import bko_git
        lib = os.path.join(env.checkout, "testvm", "audit", "audit-lib.sh")
        rc2, text = env.io.local(["bash", "-c", 'source "$1" && ship_src "$2" restic "$3"', "_", lib,
                                  f"m@{host}.local", commit], cwd=os.path.dirname(lib),
                                 env=dict(os.environ, BKO_GIT=getattr(env.ctx, "bko_git", None) or bko_git()),
                                 timeout=900)
        if rc2 != 0:
            return harness(f"can't put restic {commit[:12]}'s tree on {host}: " + tail(text))
    out = env.out_dir(name, attempt)
    cmd = (f"bash {_q(env.copy_path(host) + '/testvm/test2/gate/fork-root.sh')} --commit {commit} --out {_q(out)} "
           f"--ram {_q(env.ram_dir(name, attempt))}")
    tag = f"gate:{env.attempt_run(attempt)}:{name}"
    rc, out, t, why = host_job(env, host, name, attempt, cmd, reserve_for(e, env.rows, "gate:" + name), tag,
                               int(e.get("timeout_s", 3600)), ram=env.ram_dir(name, attempt))
    if rc is None:
        return _finish(harness(why), host, out, t, tag)
    try:
        with open(os.path.join(env.checkout, ROOT_LIST_PATH), encoding="utf-8") as f:
            listing = f.read()
    except OSError:
        listing = ""
    runs, _ = parse_root_list(listing)
    names = ["build.tsv", "run.tsv", "skips.tsv", "skips.none", "cleanup.txt", "info.txt", "job.log"]
    names += ["run-%s.log" % p.replace("/", "_") for p in sorted({p for p, _ in runs})]
    f = env.io.fetch(host, out, names)
    if rc != 0:
        return _finish(harness(f"fork-root.sh exit {rc}: " + tail(f.get("job.log"))), host, out, t, tag)
    o = fork_root_outcome(listing, f)
    o.meta["commits"] = {"restic": commit}
    o.meta["go"] = parse_kv(f.get("info.txt")).get("go")
    return _finish(o, host, out, t, tag)


def cargo_version(text):
    """The [package] version of a Cargo.toml's text."""
    pkg = False
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("["):
            pkg = s == "[package]"
            continue
        m = re.match(r'^version\s*=\s*"([^"]+)"', s)
        if pkg and m:
            return m.group(1)
    return None


def expected_versions(env, commits):
    """{OS/NAME: (version, core)}: what each binary must say, from the set's commit files
    (the facts) and the forks' version lines at those commits in BKO_GIT."""
    from . import bko_git
    git = getattr(env.ctx, "bko_git", None) or bko_git()
    cache = {}

    def show(repo, commit, path):
        k = (repo, commit, path)
        if k not in cache:
            rc, text = env.io.local(["git", "-C", os.path.join(git, repo), "show", f"{commit}:{path}"])
            cache[k] = text if rc == 0 else None
        return cache[k]

    out = {}
    for name, c in sorted((commits or {}).items()):
        if name.endswith("/restic-bko") and isinstance(c, str):
            m = re.search(r'Version\s*=\s*"([^"]+)"', show("restic", c, "internal/global/global.go") or "")
            if m:
                out[name] = (m.group(1), "")
        elif name.endswith("/rustic-bko"):
            c = c if isinstance(c, dict) else {"commit": c}
            v = cargo_version(show("rustic", c["commit"], "Cargo.toml")) if c.get("commit") else None
            core = cargo_version(show("rustic_core", c["rustic_core"], "crates/core/Cargo.toml")) \
                if c.get("rustic_core") else None
            if v:
                out[name] = (v, core or "")
    return out


def run_bin(env, check, host, attempt):
    from . import keys
    name = f"bin:{check}"
    e = layer_entry(env.layers, name)
    out = env.out_dir(name, attempt)
    code = keys.holt_code(keys.TreeSource(env.checkout))
    holt = f"{env.host_root.rstrip('/')}/holt-bin/{code}"
    sf = (((env.facts.get(host) or {}).get("sets") or {}).get(env.set_name)) or {}
    commits = sf.get("commits") or {}
    rc, _, err = env.io.sh(host, f"mkdir -p {_q(out)}")
    if rc != 0:
        return harness(f"can't make {out} on {host}: {err.strip()[-120:]}")
    extra_args = ""
    if check == "version":
        exp = expected_versions(env, commits)
        if not exp:
            return harness(f"no expected versions: the facts have no commits of {env.set_name} on {host}")
        lines = "".join(f"{n}\t{v}\t{c}\n" for n, (v, c) in sorted(exp.items()))
        rc, _, err = env.io.sh(host, f"cat > {_q(out + '/expect.tsv')}", input=lines.encode())
        if rc != 0:
            return harness(f"can't write {out}/expect.tsv on {host}: {err.strip()[-120:]}")
        extra_args = f" --expect {_q(out + '/expect.tsv')}"
        try:   # bko-holt's: holt/Cargo.toml's version (a harness input of this layer)
            with open(os.path.join(env.checkout, "holt", "Cargo.toml"), encoding="utf-8") as f:
                hv = cargo_version(f.read())
        except OSError:
            hv = None
        if hv:
            extra_args += f" --holt-version {shlex.quote(hv)}"
    holt_arg = ' ${h:+--holt "$h"}' if check in ("abi", "version") else ""
    # holt-bin/CODE isn't there until bko-holt is built: bincheck.sh then has no holt binaries.
    cmd = (f"cd {_q(env.copy_path(host))} && h={_q(holt)}; [ -d \"$h\" ] || h=; "
           f"bash testvm/test2/gate/bincheck.sh {check} --set {_q(env.set_dir)}{holt_arg}{extra_args}"
           f" > {_q(out + '/bincheck.out')} 2>&1; echo $? > {_q(out + '/.exit')}")
    t0 = time.time()
    env.io.sh(host, cmd, timeout=int(e.get("timeout_s", 600)))
    wall = int(time.time() - t0)
    f = env.io.fetch(host, out, ["bincheck.out", ".exit"])
    brc = int(f[".exit"].strip()) if f.get(".exit", "").strip().isdigit() else None
    if brc is None:
        o = harness(f"bincheck.sh {check} didn't run on {host}")
    else:
        o = bin_outcome(check, f.get("bincheck.out", ""), brc, env.baseline, sf.get("sums") or {}, commits)
        if check == "version":
            # An unchecked set binary (no line, or no expectation) fails, never a SKIP.
            version_unchecked(o, commits, exp)
    o.logs, o.wall_s = f"{host}:{out}", wall
    o.meta["bin"] = {"set": env.set_name, "commits": commits, "build_info": sf.get("build_info"), "holt_code": code}
    return o


def loop_prefix(run, kind, attempt=1):
    """run-all.sh's RUN_PREFIX for a loop layer: bkog, the run id's time (HHMM) and its 4 hex
    digits, l or s, and the attempt after the first (bko + letters or digits)."""
    m = re.search(r"T(\d{4})-([0-9a-f]{4})$", run or "")
    mid = m.group(1) + m.group(2) if m else "%08x" % random.getrandbits(32)
    return f"bkog{mid}{kind[0]}" + ("" if attempt == 1 else str(attempt))


def run_loop(env, kind, host, attempt):
    name = f"loop:{kind}"
    cell = f"gate:{name}@{host}"
    e = layer_entry(env.layers, name)
    out = env.out_dir(name, attempt)
    pfx = loop_prefix(env.run, kind, attempt)
    old = "none" if env.set_name == "bko-bin" else "~/backotter-work/bko-bin"
    ev = {k: os.environ[k] for k in ("T2_LOOP_LINUX_FS", "T2_LOOP_SPARSE_FS", "IMG_GB", "BIG") if os.environ.get(k)}
    cmd = (f"bash {_q(env.copy_path(host) + '/testvm/test2/gate/loop.sh')} {kind} --bin {_q(env.set_dir)} --out {_q(out)} --prefix {pfx} "
           f"--old {'none' if old == 'none' else _q(old)}")
    d, mem = reserve_for(e, env.rows, cell)
    if kind == "sparse" and os.environ.get("IMG_GB") and d == float((e.get("reserve") or [20])[0]):
        d = float(os.environ["IMG_GB"]) + 4   # contract 2.1: IMG_GB + 4 until a peak is logged
    tag = f"gate:{env.attempt_run(attempt)}:{name}@{host}"
    rc, out, t, why = host_job(env, host, name, attempt, cmd, (d, mem), tag, int(e.get("timeout_s", 6 * 3600)),
                               extra_env=ev)   # its RAM names are PFX-*, without the run id run.py's leftovers need
    if rc is None:
        o = harness(why)
    else:
        f = env.io.fetch(host, out, ["log/summary.json", "info.txt", "run-all.out", "job.log"])
        summary = None
        if f.get("log/summary.json"):
            try:
                summary = json.loads(f["log/summary.json"])
            except ValueError:
                summary = None
        info = parse_kv(f.get("info.txt"))
        o = loop_outcome(summary, rc, info, f.get("run-all.out") or f.get("job.log", ""))
        o.meta["loop"] = {"set": env.set_name, "old_bin": old, "linux_fs": info.get("linux_fs"),
                          "sparse_fs": info.get("sparse_fs")}
    cutdown = {k: v for k, v in ev.items() if k in ("T2_LOOP_LINUX_FS", "T2_LOOP_SPARSE_FS")}
    if cutdown:   # not the cell's whole check: its row can't make the cell current
        o.invalid = "a cut-down run (" + ", ".join(f"{k}={v}" for k, v in sorted(cutdown.items())) + \
            "): not the layer's whole check"
        o.meta.setdefault("loop", {}).update({"linux_fs": ev.get("T2_LOOP_LINUX_FS") or "default",
                                              "sparse_fs": ev.get("T2_LOOP_SPARSE_FS") or "default"})
    o.extra.setdefault("peaks", {})["reserved_gib"] = d
    o.extra["prefix"] = pfx
    return _finish(o, host, out, t, tag)


def run_selftest(env, attempt):
    import shutil
    import tempfile
    t0 = time.time()
    d = os.path.join(env.mac_root, "runs", env.attempt_run(attempt), "gate", "selftest")
    os.makedirs(d, exist_ok=True)
    # Roots of their own, removed after: never this run's, never the defaults.
    roots = tempfile.mkdtemp(prefix="roots-", dir=d)
    e = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", T2_MAC_ROOT=os.path.join(roots, "mac"),
             T2_HOST_ROOT=os.path.join(roots, "host"), T2_COPY_ROOT=os.path.join(roots, "copy"))
    e.pop("T2_LEDGER", None)
    res = {}
    t2 = os.path.join(env.checkout, "testvm", "test2", "t2")
    res["t2-self-test"] = (env.io.local([sys.executable, "-B", t2, "self-test"], cwd=env.checkout, env=e, timeout=1800)
                           if os.path.exists(t2) else (None, "no testvm/test2/t2"))
    drive = os.path.join(env.checkout, "testvm", "stictest1", "drive.py")
    res["drive-self-test"] = env.io.local([sys.executable, "-B", drive, "--self-test"], cwd=env.checkout, env=e,
                                          timeout=600)
    shutil.rmtree(roots, ignore_errors=True)
    o = selftest_outcome(res)
    o.wall_s = int(time.time() - t0)
    o.logs = "mac:" + d
    try:
        for k, (rc, text) in res.items():
            with open(os.path.join(d, k + ".log"), "w", encoding="utf-8") as f:
                f.write(f"exit {rc}\n{text or ''}")
    except OSError:
        pass
    return o


# --- measuring and rows --------------------------------------------------------------------

def measure_opts(env, cell, commits=None):
    o = {"set": env.set_name}
    if env.set.get("pending"):
        o["pending"] = env.set["pending"]
    if split_cell(cell)[0].startswith("fork") and commits:
        o["gitrepos"] = dict(commits)   # the commits tested: the hosts have no BKO_GIT
    return o


def _host_env(env):
    e = {"T2_HOST_ROOT": env.host_root, "T2_COPY_ROOT": env.copy_root, "PYTHONDONTWRITEBYTECODE": "1"}
    if os.environ.get("T2_LEDGER"):
        e["T2_LEDGER"] = os.environ["T2_LEDGER"]
    return e


def measure(env, host, cell, opts):
    """keys.measure() of CELL as run: on HOST from the run's copy with that host's facts
    (on the Mac, for mac, from the checkout).  {"error"} when it can't."""
    from . import keys
    try:
        specs = keys.Specs(keys.TreeSource(env.checkout))
        if host == "mac":
            return keys.measure(cell, keys.TreeSource(env.checkout), {"host": "mac"}, specs, opts)
        data = {"test2": env.copy_path(host).rstrip("/") + "/testvm/test2", "measure": [cell], "opts": opts,
                "want": keys.wants(specs, ("gate",), sets=[env.set_name]), "env": _host_env(env)}
        r = env.io.script(host, HOST_SCRIPT, data, timeout=180)
        return (r.get("measure") or {}).get(cell) or {"error": "no measure"}
    except Exception as e:   # noqa: BLE001 - an unmeasured row is invalid, never a crash
        return {"error": f"{type(e).__name__}: {e}"}


def make_row(env, cell, attempt, o, m, start, rule, reasons, planned_key, rerun_of=None):
    """One attempt's row (contract 1.2) less host, commit, committed, dirty and copy,
    which HostCtx.row() fills on the host (append_rows() on the Mac)."""
    err, unknown = (m or {}).get("error"), (m or {}).get("unknown") or []
    valid = not err and not unknown and (planned_key is None or m.get("key") == planned_key) and not o.invalid
    notes = []
    if o.invalid:
        notes.append(o.invalid)
    if err:
        notes.append(f"inputs not measured: {err}")
    elif planned_key and m.get("key") != planned_key:
        notes.append("its inputs as run differ from the plan's")
    if unknown:
        notes.append("not measured: " + ", ".join(unknown[:5]))
    if o.note:
        notes.append(o.note)
    extra = dict(o.extra)
    if rerun_of:
        extra["rerun_of"] = rerun_of
    return {
        "origin": f"gate:{env.run}:{cell}:{attempt}", "family": "gate", "cell": cell, "kind": "run",
        "run": env.run, "word": cut(env.word or "GateTest2"), "rule": rule, "reasons": [cut(r) for r in reasons][:20],
        "attempt": attempt, "start": start, "inputs": (m or {}).get("inputs") or {}, "spec": (m or {}).get("spec"),
        "planned_key": planned_key, "valid": bool(valid), "verdict": o.verdict,
        "class": {"PASS": "pass", "FAIL": "fail", "FLAKY-FAIL": "fail", "HARNESS": "harness", "LOST": "lost"}[o.verdict],
        "needs": o.needs, "counts": counts_of(o.needs), "fails": o.fails, "gm": 0.0, "wall_s": int(o.wall_s),
        "waits_s": int(o.waits_s), "wear": dict(o.wear), "ram_peak_kib": o.ram_peak_kib, "seed": None,
        "logs": o.logs, "meta": dict(o.meta, set=env.set_name, run_family=env.family), "extra": extra, "via": None,
        "note": cut("; ".join(notes), 1000) if notes else None,
    }


def append_row(env, host, row):
    """Write ROW on HOST through the copy's t2lib (complete, scrub, validate, append), or
    on the Mac into mac.jsonl.  (id, appended) or ("error", why)."""
    if host == "mac":
        from . import copies, ledger, mac_ledger_file
        try:
            pv = copies.preview(env.checkout)
            common = {"commit": pv.commit, "committed": not pv.dirty, "dirty": dict(pv.dirty), "copy": pv.id}
        except Exception as e:   # noqa: BLE001
            common = {"commit": None, "committed": False, "dirty": None, "copy": "unknown"}
            env.log(f"mac: no copy preview: {e}")
        try:
            r = ledger.complete(dict(common, **row), host="mac")
            ledger.check(r)
            return tuple(ledger.append(mac_ledger_file(), [r])[0])
        except Exception as e:   # noqa: BLE001
            return ("error", f"{type(e).__name__}: {e}")
    data = {"test2": env.copy_path(host).rstrip("/") + "/testvm/test2", "append": [row], "env": _host_env(env)}
    try:
        r = env.io.script(host, HOST_SCRIPT, data, timeout=120)
        ids = r.get("ids") or [["error", "no id"]]
        return tuple(ids[0])
    except (RuntimeError, ValueError) as e:
        return ("error", str(e))


# --- one layer, with its rerun -------------------------------------------------------------

def run_one(env, cell, attempt_fn):
    """CELL's attempts (ATTEMPT_FN(n) -> Outcome; a second on FAIL or HARNESS), each
    measured and written.  Its RunResult cell entry."""
    host = env.host_for(cell)
    pc = env.plan_cell(cell)
    rule = getattr(pc, "rule", None) or "fresh:names"
    reasons = list(getattr(pc, "reasons", None) or ([] if pc is not None else [f"run by {env.word}"]))
    planned = None
    if pc is not None:
        planned = (getattr(pc, "keys", None) or {}).get(host) or getattr(pc, "key", None)
    ids, first, prev, o = [], None, None, None
    lost = 0
    for a in (1, 2):
        start = utc_now()
        env.log(f"{cell} on {host}: attempt {a}")
        try:
            o = attempt_fn(a)
        except Exception as ex:   # noqa: BLE001 - the layer's code failing is a harness result
            o = harness(f"{type(ex).__name__}: {ex}")
        start = o.start or start   # the fork layers ran before run_one: their own start
        if a == 2 and first == "FAIL" and o.verdict == "PASS":
            o.verdict = "FLAKY-FAIL"
            o.note = cut("passed on the rerun after a FAIL: flaky" + (f"; {o.note}" if o.note else ""), 1000)
        m = measure(env, host, cell, measure_opts(env, cell, o.meta.get("commits")))
        row = make_row(env, cell, a, o, m, start, rule if a == 1 else "rerun",
                       reasons if a == 1 else ([f"attempt 1: {first}"] + reasons)[:20], planned, prev)
        rid = append_row(env, host, row)
        if rid and rid[0] == "error":
            env.log(f"{cell}: its row wasn't written: {rid[1]}")
            ids.append(f"unwritten ({cut(rid[1], 80)})")
            prev = None
            lost += 1
        else:
            ids.append(rid[0])
            prev = rid[0]
        env.log(f"{cell} on {host}: attempt {a} {o.verdict}" + (f": {o.fails[0]}" if o.fails else ""))
        if a == 1:
            first = o.verdict
        if o.verdict not in ("FAIL", "HARNESS"):
            break
        if o.still_running:   # its job may still run on the host: never a second beside it
            env.log(f"{cell}: no rerun: attempt {a}'s job may still run")
            break
    name, _ = split_cell(cell)
    if o.note and o.note.startswith(("plan diff not yet accepted", "unproven golden plans")):
        reasons = [o.note] + reasons   # R23: the report names the unaccepted plan diff
    cls = {"PASS": "pass", "FLAKY-FAIL": "fail", "FAIL": "fail", "HARNESS": "harness"}.get(o.verdict, "lost")
    if lost:   # the ledger doesn't hold what this entry says: the cell stays due, and the run says so
        cls = "lost"
    return {"cell": cell, "outcome": "ran", "rule": rule, "reasons": reasons, "rows": ids, "verdict": o.verdict,
            "class": cls, "unwritten": lost,
            "gm": 0.0, "wall_s": o.wall_s, "gb": None, "host": host, "needs": o.needs, "fails": o.fails,
            "note": o.note, "part": "slow" if entry_or_empty(env.layers, name).get("part") == "slow" else "fast",
            "plan_diff": o.extra.get("plan_diff") if name == "holt" else None}


class LayerLock:
    """MAC_ROOT/runs/.lock-gate-LAYER for as long as a layer runs (R21): a second run of
    the same layer waits for the first."""

    def __init__(self, env, cell):
        self.env, self.cell = env, cell
        d = os.path.join(env.mac_root, "runs")
        os.makedirs(d, exist_ok=True)
        self.path = os.path.join(d, ".lock-gate-" + layer_dir(cell[5:]))
        self.fd = None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        said = False
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if not said:
                    try:
                        holder = os.pread(self.fd, 4096, 0).decode("utf-8", "replace").strip()
                    except OSError:
                        holder = "?"
                    self.env.log(f"{self.cell} is running in another run ({holder or '?'}): waiting for it")
                    said = True
                time.sleep(LOCK_POLL_S)
        os.ftruncate(self.fd, 0)
        os.pwrite(self.fd, f"run {self.env.run}, pid {os.getpid()}, started {utc_now()}\n".encode(), 0)
        return self

    def __exit__(self, *a):
        try:
            os.ftruncate(self.fd, 0)
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)


# --- the parts --------------------------------------------------------------------------------

def cells_of(env, names):
    """Gate cells for NAMES (cells or layer names).  A per-host layer's name gives a cell
    for each host with facts, or, with a plan, each one the plan has due."""
    out = []
    hosts = [h for h in env.hosts if h in env.facts] or list(env.hosts)
    for n in names:
        name, h = split_cell(n)
        if h or not per_host(entry_or_empty(env.layers, name)):
            c = f"gate:{name}" + (f"@{h}" if h else "")
            if c not in out:
                out.append(c)
            continue
        cs = [f"gate:{name}@{x}" for x in hosts]
        if env.plan is not None:
            due = [c for c in cs if getattr(env.plan_cell(c), "status", None) == "due"]
            cs = due or cs
        out += [c for c in cs if c not in out]
    return out


def gate_plan(ctx):
    """The GateTest2 plan for the calling run's set (SticTest2: its round) when run_layers
    gets none: the cells' rules, reasons and planned keys.  None when it can't be made."""
    try:
        import copy
        from . import plan as planmod, words
        rs = getattr(ctx, "run", None)
        s = getattr(getattr(rs, "plan", None), "set", None) or {}
        rnd = s.get("round") if isinstance(s, dict) else None
        w = words.Word("gate", "GateTest2" + (f" {rnd}" if rnd else ""))
        w.round = rnd
        c = copy.copy(ctx)   # the caller's --universe names its own family's cells
        c.options = {k: v for k, v in (getattr(ctx, "options", None) or {}).items() if k != "universe"}
        return planmod.make(w, c)
    except Exception:   # noqa: BLE001 - the rows then have no planned key
        return None


def run_layers(ctx, names, part=None, plan=None, env=None, io=None, log=None):
    """Run the gate layers NAMES (fast: one after another; slow: all at once).
    {"cells": [entry], "rows": [id], "stops": [text], "stop": None|stop-family|stop-starts,
    "ok": bool, "exit": 0|1}."""
    if env is None:
        if plan is None:
            plan = gate_plan(ctx)
        env = RunEnv(ctx, plan, io=io, log=log)
    cells = cells_of(env, names)
    if part is None:
        part = "slow" if cells and all(entry_or_empty(env.layers, split_cell(c)[0]).get("part") == "slow"
                                       for c in cells) else "fast"
    results = []
    if part == "slow":
        res, lock = {}, threading.Lock()

        def one(c):
            r = locked(env, c, lambda: run_cell(env, c))
            with lock:
                res[c] = r

        ths = [threading.Thread(target=one, args=(c,)) for c in cells]
        for th in ths:
            th.start()
        for th in ths:
            th.join()
        results = [res[c] for c in cells]
    else:
        order = {n: i for i, n in enumerate(FAST_ORDER)}
        cells.sort(key=lambda c: order.get(split_cell(c)[0], len(order)))
        forks = [c for c in cells if split_cell(c)[0].startswith("fork:")]
        done, commits = {}, {}
        for c in cells:
            name = split_cell(c)[0]
            if name.startswith("fork:"):
                if not done:
                    commits = fork_commits(env, [split_cell(x)[0][5:] for x in forks])
                    done = run_fork_cells(env, forks, commits)
                results.append(done[c])
                continue
            if name == "fork-root:restic" and "restic" not in commits:
                commits.update(fork_commits(env, ["restic"]))
            results.append(locked(env, c, lambda c=c: run_cell(env, c, commits)))
    stops = [f"{'stop-family' if part == 'fast' else 'stop-starts'}: {part} gate {r['cell']} {r['verdict']}"
             for r in results if r["verdict"] in NOT_PASSED]
    lost = any(r.get("class") == "lost" for r in results)
    return {"cells": results, "rows": [i for r in results for i in r["rows"]], "stops": stops,
            "stop": (("stop-family" if part == "fast" else "stop-starts") if stops else None),
            "ok": not stops, "exit": 1 if stops or lost else 0}


def locked(env, cell, fn):
    with LayerLock(env, cell):
        return fn()


def run_cell(env, cell, commits=None):
    name, h = split_cell(cell)
    host = env.host_for(cell)
    if name == "holt":
        fn = lambda a: run_holt(env, host, a)
    elif name == "fork-root:restic":
        fn = lambda a: run_fork_root(env, host, a, (commits or {}).get("restic") or
                                     fork_commits(env, ["restic"]).get("restic"))
    elif name.startswith("bin:"):
        fn = lambda a: run_bin(env, name[4:], host, a)
    elif name.startswith("loop:"):
        fn = lambda a: run_loop(env, name[5:], host, a)
    elif name == "selftest":
        fn = lambda a: run_selftest(env, a)
    else:
        fn = lambda a: harness(f"no runner for the gate layer {name}")
    return run_one(env, cell, fn)


def run_fork_cells(env, cells, commits):
    """The fork layers: one fork-tests.sh per host and attempt; the layers that FAIL (or
    don't finish) run again together as attempt 2.  {cell: entry}."""
    names = [split_cell(c)[0][5:] for c in cells]
    locks = [LayerLock(env, c) for c in cells]
    for lk in locks:
        lk.__enter__()
    try:
        first = run_forks(env, names, 1, commits)
        again = [n for n in names if first[n].verdict in ("FAIL", "HARNESS") and not first[n].still_running]
        second = run_forks(env, again, 2, commits) if again else {}
        out = {}
        for c, n in zip(cells, names):
            seq = iter([first[n]] + ([second[n]] if n in second else []))
            out[c] = run_one(env, c, lambda a, seq=seq: next(seq, None) or harness("no second fork-tests.sh run"))
        return out
    finally:
        for lk in reversed(locks):
            lk.__exit__()


# --- the word ---------------------------------------------------------------------------------

def due_cells(plan):
    return [pc.cell for pc in (getattr(plan, "cells", None) or [])
            if getattr(pc, "status", None) == "due" and str(getattr(pc, "cell", "")).startswith("gate:")]


def run(ctx, plan, io=None, log=None):
    """GateTest2's runner (run.py's lifecycle calls it after the copy): the plan's due
    cells, the fast layers first, then the slow ones.  A RunResult dict (contract 1.9)."""
    env = RunEnv(ctx, plan, io=io, log=log)
    start = utc_now()
    rs = env.rs
    if rs is not None and hasattr(rs, "write_cells"):
        try:
            rs.write_cells(plan)
        except Exception as e:   # noqa: BLE001 - the gate reads no cells.json; say so and go on
            env.log(f"cells.json not written: {e}")
    due = due_cells(plan)
    fast = [c for c in due if entry_or_empty(env.layers, split_cell(c)[0]).get("part") != "slow"]
    slow = [c for c in due if c not in fast]
    cells, stops = [], []
    for part, cs in (("fast", fast), ("slow", slow)):
        if cs:
            r = run_layers(ctx, cs, part, plan, env=env)
            cells += r["cells"]
            stops += r["stops"]
    notes = [c["note"] for c in cells if c.get("note") and c["cell"] == "gate:holt"]
    return {"run": env.run, "word": env.word, "family": "gate", "start": start, "end": utc_now(), "cells": cells,
            "stops": stops, "notes": notes, "wear": {}, "hosts": sorted({c["host"] for c in cells if c.get("host")}),
            "copy": env.copy_id,
            "exit": 1 if any(c["verdict"] in NOT_PASSED or c.get("class") == "lost" for c in cells) else 0}
