"""A Test2 run's life (contract 1.9; rulings R1, R3, R10, R12, R21, R22).

    lifecycle(word, ctx, ...)   1 pull and 2 facts (the CLI has done them for ctx), 3 preflight,
                                4 plan, 5 copy, 5a-7 the family runner (it builds, rekeys and
                                writes cells.json through RunState), 8 pull, 9 derived rows,
                                10 the report
    resume(run, ...)            R10: the same run picked up again from MAC_ROOT/runs/RUN/plan.json

The pieces the runners use (t2lib.gate, t2lib.stic, t2lib.holt; their run(ctx, plan) gets this
module's RunState as ctx.run):
    new_run_id(family)          t2g-/t2s-/t2h-YYYYMMDDTHHMM-XXXX
    RunState                    the run id, its directories, the copy, cells.json writing,
                                record_made(), the StopEngine, limits and flakes
    StopEngine                  feed(result) -> ok | stop-family | stop-os OS | stop-starts | stop-run
    rerun_verdict(), match_flake(), apply_limits(), limits_classed(), rerun_map(), fanout_due()
    derived_rows(plan, ...)     the carried and class rows for mac.jsonl
"""

import errno
import fcntl
import importlib
import json
import os
import re
import secrets
import shlex
import threading
import time

from . import HARD
from . import copies, hostrun, host_root as default_host_root, keys, ledger, mac_root as default_mac_root
from . import utc_now
from . import plan as planmod
from . import report

RUN_PREFIX = {"gate": "t2g", "stic": "t2s", "holt": "t2h"}
RUN_RE = re.compile(r"t2[gsh]-\d{8}T\d{4}-[0-9a-f]{4}(-r2)?\Z")
RUNNERS = {"gate": "gate", "stic": "stic", "holt": "holt"}
PREFLIGHT = {"tctl_c": 90.0, "scratch_free_gb": 80.0}
EXIT_OK, EXIT_FAIL, EXIT_REFUSED, EXIT_INTERNAL = 0, 1, 2, 3
STOP_ORDER = ("ok", "stop-starts", "stop-os", "stop-family", "stop-run")
FAILING = ("FAIL", "FLAKY-FAIL")
GIB = 1 << 30
UNFINISHED_S = 12 * 3600   # an unfinished run younger than this may still have host processes
_MADE_MU = threading.Lock()


class Refused(Exception):
    """A run that can't start (exit 2)."""


class Busy(Refused):
    def __init__(self, holder, name):
        h = holder or {}
        super().__init__(f"another {name} run holds its lock: run {h.get('run', '?')}, pid {h.get('pid', '?')}, "
                         f"started {h.get('start', '?')}")
        self.holder = holder


def new_run_id(family, t=None):
    return f"{RUN_PREFIX[family]}-{time.strftime('%Y%m%dT%H%M', time.gmtime(t or time.time()))}-{secrets.token_hex(2)}"


def runs_dir(mac_root=None):
    return os.path.join(mac_root or default_mac_root(), "runs")


def run_dir(run, mac_root=None):
    return os.path.join(runs_dir(mac_root), run)


def write_json(path, obj):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def write_text(path, text):
    """PATH written whole or not at all (tmp, then rename): report.txt marks a run finished."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


# --- one run of a family at a time (R21) -------------------------------------------------------

class FamilyLock:
    """flock on MAC_ROOT/runs/.lock-NAME for a run's life; .lock-NAME.json names the holder."""

    def __init__(self, name, mac_root=None):
        self.name = name
        d = runs_dir(mac_root)
        self.path = os.path.join(d, f".lock-{name}")
        self.info = self.path + ".json"
        self.fd = None

    def holder(self):
        return read_json(self.info)

    def held(self):
        """True when another process holds it (tested without taking it for long)."""
        if not os.path.exists(self.path):
            return False
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                return True
            raise
        finally:
            os.close(fd)

    def acquire(self, run, wait=1.0):
        """Busy when another run holds it.  It retries for WAIT seconds: a plan's held() takes
        the lock for an instant, and that must not refuse a run."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.time() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() >= deadline:
                    os.close(fd)
                    raise Busy(self.holder(), self.name)
                time.sleep(0.05)
        self.fd = fd
        write_json(self.info, {"run": run, "pid": os.getpid(), "start": utc_now()})
        return self

    def release(self):
        if self.fd is not None:
            try:
                os.remove(self.info)
            except OSError:
                pass
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.release()


def held_runs(mac_root=None):
    """The run ids holding a family lock in MAC_ROOT (any family; "pending" while one plans)."""
    out = set()
    for fam in RUN_PREFIX:
        lk = FamilyLock(fam, mac_root)
        if lk.held():
            out.add((lk.holder() or {}).get("run") or "?")
    return out


def unfinished_runs(family=None, mac_root=None, exclude=(), now=None, max_age=UNFINISHED_S):
    """[run] (of FAMILY, or any) that began host work (a cells-HOST.json), have no report.txt
    and changed within MAX_AGE: the lock went with the Mac process (Ctrl-C, a crash), but
    detached checks and queue runners on the hosts may still run (R21)."""
    d = runs_dir(mac_root)
    out = []
    if not os.path.isdir(d):
        return out
    now = now or time.time()
    pre = RUN_PREFIX[family] + "-" if family else "t2"
    for run in sorted(os.listdir(d)):
        if not RUN_RE.match(run) or not run.startswith(pre) or run in exclude:
            continue
        rd = os.path.join(d, run)
        try:
            names = os.listdir(rd)
            if "report.txt" in names or not any(n.startswith("cells-") and n.endswith(".json") for n in names):
                continue
            newest = max(os.path.getmtime(os.path.join(rd, n)) for n in names)
        except (OSError, ValueError):
            continue
        if now - newest <= max_age:
            out.append(run)
    return out


def run_hosts(run, mac_root=None):
    """The hosts RUN used, in its order: its cells-HOST.json files, else its plan's facts."""
    rd = run_dir(run, mac_root)
    pj = read_json(os.path.join(rd, "plan.json"), {}) or {}
    order = [h for h in ((pj.get("header") or {}).get("facts") or {}) if h != "mac"]
    try:
        cells = sorted(m.group(1) for m in (re.match(r"cells-(?!r2-)(.+)\.json\Z", n) for n in os.listdir(rd)) if m)
    except OSError:
        cells = []
    if not cells:
        return order
    return [h for h in order if h in cells] + [h for h in cells if h not in order]


def running_cells(family, mac_root=None):
    """{cell: run} of a live run of FAMILY (R21: plan shows them as running RUN)."""
    lk = FamilyLock(family, mac_root)
    if not lk.held():
        return {}
    h = lk.holder() or {}
    run = h.get("run")
    p = read_json(os.path.join(run_dir(run, mac_root), "plan.json"), {}) if run else {}
    return {c["cell"]: run for c in (p.get("cells") or []) if c.get("status") == "due"}


# --- preflight -----------------------------------------------------------------------------------

def preflight(facts, hosts):
    """Refusals (exit 2) for the hosts in use: Tctl >= 90 C, no /mnt/bko-ram, m not in kvm,
    /bkoscratch under 80 GB free, no facts."""
    out = []
    for h in hosts:
        if h == "mac":
            continue
        f = facts.get(h)
        if not f:
            out.append(f"{h}: unreachable (no facts)")
            continue
        p = f.get("preflight") or {}
        t = p.get("tctl_c")
        if isinstance(t, (int, float)) and t >= PREFLIGHT["tctl_c"]:
            out.append(f"{h}: Tctl {t:.1f} C (>= {PREFLIGHT['tctl_c']:.0f})")
        if not (p.get("ram") or {}).get("mounted"):
            out.append(f"{h}: no /mnt/bko-ram")
        if p.get("kvm") is False:
            out.append(f"{h}: m is not in group kvm")
        fr = p.get("scratch_free_gb")
        if isinstance(fr, (int, float)) and fr < PREFLIGHT["scratch_free_gb"]:
            out.append(f"{h}: /bkoscratch has {fr:.0f} GB free (< {PREFLIGHT['scratch_free_gb']:.0f})")
    return out


def our_leftovers(mac_root=None, live=()):
    """[(run, host, path, pid)] that dead runs of ours made (their made.json), runs with no
    report and no live lock.  Only paths under /mnt/bko-ram or a copy root that name the run."""
    out = []
    d = runs_dir(mac_root)
    if not os.path.isdir(d):
        return out
    for run in sorted(os.listdir(d)):
        if not RUN_RE.match(run) or run in live:
            continue
        rd = os.path.join(d, run)
        if os.path.exists(os.path.join(rd, "report.txt")):
            continue
        made = read_json(os.path.join(rd, "made.json"), {}) or {}
        for host, items in made.items():
            for it in items or []:
                p, pid = it.get("path"), it.get("pid")
                if p and run in p and (p.startswith("/mnt/bko-ram/") or "/t2/copy/" in p or it.get("copy_root")):
                    out.append((run, host, p, pid))
    return out


def pid_dead(host, pid):
    """True only when HOST answered that PID doesn't run.  An ssh that fails, times out or is
    refused is no answer: the PID counts as alive, and nothing of its run is removed."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if host == hostrun.LOCAL and not os.path.isdir("/proc"):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        return False
    r = hostrun.ssh(host, f"if test -d /proc/{pid}; then echo alive; else echo dead; fi", timeout=30)
    return r.rc == 0 and r.text.strip() == "dead"


def remove_leftovers(items, log=print, dead=None):
    """Remove a dead run's leftovers, run by run: when any PID its made.json recorded still
    runs, or can't be checked, every leftover of that run is kept and said so."""
    dead = dead or pid_dead
    done, by_run = [], {}
    for it in items:
        by_run.setdefault(it[0], []).append(it)
    for run, its in by_run.items():
        live = next(((h, pid) for _, h, _, pid in its if pid and not dead(h, pid)), None)
        if live:
            log(f"leftovers of {run} kept: pid {live[1]} on {live[0]} still runs or couldn't be checked "
                f"({', '.join(p for _, _, p, _ in its)})")
            continue
        for _, host, path, pid in its:
            r = hostrun.ssh(host, f"rm -rf --one-file-system -- {shlex.quote(path)} 2>/dev/null || "
                                  f"sudo -n rm -rf --one-file-system -- {shlex.quote(path)}", timeout=120)
            log(f"leftover of {run} on {host}: {path} {'removed' if r.rc == 0 else 'NOT removed: ' + r.errtext[-120:]}")
            done.append((host, path, r.rc == 0))
    return done


# --- the stop rules (1.9; R22) ------------------------------------------------------------------

class StopEngine:
    """Runners feed it each cell's result as it lands:

        feed({cell, family, part ("fast"/"slow" for gate layers, else None), verdict, class,
              needs {need: v}, os, host})
        feed_host(host, swap_out_bytes=..., ours_bytes=...)   swap-out since the run began; this
                                                               run's bytes from its wear tags (R22)

    and gets back ok, stop-family, stop-os OS, stop-starts or stop-run.  Its state:
    stop_family (no guest of the family starts), stopped_os, stop_starts, stop_run, stops [text]."""

    def __init__(self, family, policy=None, sweep=False, now=None):
        p = (policy or {})
        st = p.get("stop") or {}
        b = p.get("budget") or {}
        self.family = family
        self.os_need = int(st.get("os_need_guests", 2))
        self.harness_max = int(st.get("harness_results", 2))
        self.swap_max = float(st.get("swap_out_gib", 1)) * GIB
        self.write_x = float(st.get("write_budget_x", 2))
        self.run_gb = float(b.get("sweep_gb" if sweep else "run_gb", 2))
        self.bind = report.budget_binds(b.get("bind_from"), now)
        self.harness = 0
        self.need_fail = {}
        self.stop_family = self.stop_starts = self.stop_run = False
        self.stopped_os = set()
        self.stops = []

    def _stop(self, what, why):
        self.stops.append(f"{what}: {why}")
        return what

    def feed(self, res):
        verdict, klass = res.get("verdict"), res.get("class")
        out = "ok"
        if res.get("part") == "fast" and verdict in FAILING + ("HARNESS", "LOST"):
            if not self.stop_family:
                self.stop_family = True
                out = self._stop("stop-family", f"fast gate {res.get('cell')} {verdict}")
        elif res.get("part") == "slow" and verdict in FAILING + ("HARNESS", "LOST"):
            if not self.stop_starts:
                self.stop_starts = True
                out = self._stop("stop-starts", f"slow gate {res.get('cell')} {verdict}")
        if klass in ("harness", "lost") and not res.get("flake"):
            self.harness += 1
            if self.harness >= self.harness_max and not self.stop_run:
                self.stop_run = True
                return self._stop("stop-run", f"{self.harness} harness-class results (last {res.get('cell')})")
        osn = res.get("os")
        if verdict in FAILING and osn and klass != "known-flake" and not res.get("flake"):
            for need, v in (res.get("needs") or {}).items():
                if v in ("FAIL", "LIMIT-NEW"):
                    s = self.need_fail.setdefault((osn, need), set())
                    s.add(res.get("cell"))
                    if len(s) >= self.os_need and osn not in self.stopped_os:
                        self.stopped_os.add(osn)
                        o = self._stop(f"stop-os {osn}", f"need {need} failed on {', '.join(sorted(s))}")
                        out = o if STOP_ORDER.index("stop-os") > STOP_ORDER.index(out.split()[0]) else out
        return out

    def feed_host(self, host, swap_out_bytes=None, ours_bytes=None):
        if swap_out_bytes is not None and swap_out_bytes > self.swap_max and not self.stop_starts:
            self.stop_starts = True
            return self._stop("stop-starts", f"{host}: {swap_out_bytes / GIB:.2f} GiB swapped out since the run began")
        if ours_bytes is not None:
            limit = self.write_x * self.run_gb * 1e9
            if ours_bytes > limit and not self.stop_starts:
                self.stop_starts = True
                return self._stop("stop-starts", f"{host}: this run's jobs wrote {ours_bytes / 1e9:.2f} GB, "
                                                 f"over {self.write_x:g} x the {self.run_gb:g} GB budget")
            if self.bind and ours_bytes > self.run_gb * 1e9 and not self.stop_starts:
                self.stop_starts = True
                return self._stop("stop-starts", f"{host}: this run's jobs wrote {ours_bytes / 1e9:.2f} GB, over the "
                                                 f"{self.run_gb:g} GB budget (bound)")
        return "ok"

    def may_start(self, cell_os=None):
        return not (self.stop_run or self.stop_starts or self.stop_family or (cell_os and cell_os in self.stopped_os))


# --- reruns, flakes, LIMITs (1.9, R3) ----------------------------------------------------------

def match_flake(guest, text, flakes):
    """The known flake (flakes.json: [{guest, pattern, why, ref}]) whose pattern matches TEXT
    (attempt 1's FAIL lines or run.out) for GUEST, or None."""
    for f in flakes or ():
        if f.get("guest") not in (guest, "*"):
            continue
        try:
            if re.search(f.get("pattern") or r"(?!)", text or "", re.M):
                return f
        except re.error:
            continue
    return None


def rerun_verdict(second_verdict, flake):
    """Attempt 2 after a FAIL: a PASS is FLAKY-FAIL (the run fails), or PASS/known-flake when
    attempt 1's FAILs matched a known flake.  Returns (verdict, class)."""
    if second_verdict == "PASS":
        return ("PASS", "known-flake") if flake else ("FLAKY-FAIL", "fail")
    return second_verdict, {"FAIL": "fail", "HARNESS": "harness", "LOST": "lost"}.get(second_verdict, "fail")


def apply_limits(needs, classed):
    """NEEDS {need: v} with every LIMIT not in CLASSED made LIMIT-NEW; and whether one is."""
    out, new = {}, False
    cl = set(classed or ())
    for n, v in (needs or {}).items():
        if v == "LIMIT" and n not in cl:
            out[n], new = "LIMIT-NEW", True
        else:
            out[n] = v
    return out, new


def limits_classed(cell, limits, rows):
    """R3: limits.json[GUEST] and the LIMIT needs of the cell's newest PASS-like run row (any key)."""
    guest = cell.split(":", 1)[1]
    out = set((limits or {}).get(guest, {}) or {})
    for r in sorted((r for r in rows or () if r.get("cell") == cell and r.get("kind") == "run"
                     and r.get("verdict") == "PASS"), key=lambda r: (r.get("t") or "", r.get("id", "")), reverse=True):
        out |= {n for n, v in (r.get("needs") or {}).items() if v == "LIMIT"}
        break
    return sorted(out)


def rerun_map(plan, results, flakes=()):
    """cells-r2.json's cells (1.9 Reruns, 1.6.2 fan-out): {cell: {attempt, rerun_of, flake, rule}}.

    RESULTS: [{cell, verdict, rows: [row id...], fails: [line...]}] of attempt 1.  A FAIL (or
    LIMIT-NEW, which is one) reruns once as attempt 2, with the known flake its FAIL lines match;
    a class representative's FAIL or FLAKY-FAIL makes its class-pending members due with
    fanout:CLASS, as their attempt 1."""
    out = {}
    for r in results or ():
        if r.get("verdict") not in ("FAIL",):
            continue
        cell = r.get("cell")
        rows = r.get("rows") or []
        rid = rows[0].get("id") if rows and isinstance(rows[0], dict) else (rows[0] if rows else None)
        guest = cell.split(":", 1)[1] if cell else None
        fl = match_flake(guest.split("+")[0] if guest else None, "\n".join(r.get("fails") or []), flakes)
        out[cell] = {"attempt": 2, "rerun_of": rid, "flake": fl, "rule": "rerun"}
    # a FAIL that matches a known flake reruns, but fans nothing out (as stic.reruns does)
    bad = {r.get("cell") for r in results or () if r.get("verdict") in FAILING
           and not (r.get("verdict") == "FAIL" and (out.get(r.get("cell")) or {}).get("flake"))}
    for cl in getattr(plan, "classes", None) or []:
        if cl.get("rep") in bad:
            for m in cl.get("members") or []:
                out.setdefault(m, {"attempt": 1, "rerun_of": None, "flake": None, "rule": f"fanout:{cl['class']}"})
    return out


def fanout_due(plan, reruns):
    """The plan with REruns' fan-out members made due (rule fanout:CLASS), for cells-r2.json."""
    for c in plan.cells:
        rr = reruns.get(c.cell)
        if rr and rr.get("rule", "").startswith("fanout:") and c.status == "class-pending":
            c.status, c.rule = "due", rr["rule"]
            c.reasons = [f"its class representative {c.rep} failed"] + c.reasons
    return plan


def driver_id(cell):
    """cells.json's key: GUEST (stic), GV: GUEST or GUEST-VARIANT (holt), LAYER (gate)."""
    fam, _, rest = cell.partition(":")
    if fam == "holt":
        return rest.replace("+", "-", 1)
    if fam == "gate":
        return rest.split("@", 1)[0]
    return rest


# --- the run's state, for the runners -------------------------------------------------------------

class RunState:
    """What a runner gets as ctx.run.

        run, word, family, mac_dir (MAC_ROOT/runs/RUN), host_root, hosts
        copy           {host: {id, path, commit, dirty}} once lifecycle step 5 made it
        stops          the StopEngine
        flakes         flakes.json's entries; limits: limits.json
        write_cells(plan, attempt=1, name="cells.json", reruns=None)   R1's step 6, on every host
        replan(facts, ledger_rows)   R1's step 5a: (plan, added, dropped) after a build
        host_run_dir(host)   HOST_ROOT/runs/RUN
        record_made(host, path, pid)   for preflight's leftovers
        log(text)      to MAC_ROOT/runs/RUN/run.log and stdout
    """

    def __init__(self, run, word, plan, ctx, mac_root=None, host_root=None, log=print):
        self.run, self.word, self.family = run, word, word.family
        self.plan = plan
        self.ctx = ctx
        self.mac_root = mac_root or default_mac_root()
        self.mac_dir = run_dir(run, self.mac_root)
        self.host_root = host_root or default_host_root()
        self.hosts = [h for h in ctx.hosts if h in ctx.facts]
        self.copy = {}
        # SweepTest2's 5 GB budget is its own: the All forms keep the per-run 2 GB
        self.stops = StopEngine(word.family, ctx.classes.get("policy"), sweep=getattr(word, "sweep", False),
                                now=getattr(ctx, "now", None))
        self.flakes = read_json(os.path.join(ctx.checkout, "testvm", "test2", "stic", "flakes.json"), []) or []
        if isinstance(self.flakes, dict):
            self.flakes = self.flakes.get("flakes") or []
        self.limits = read_json(os.path.join(ctx.checkout, "testvm", "test2", "stic", "limits.json"), {}) or {}
        self._log = log
        os.makedirs(self.mac_dir, exist_ok=True)

    def log(self, text):
        line = f"{utc_now()} {text}"
        with open(os.path.join(self.mac_dir, "run.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if self._log:
            self._log(text)

    def host_run_dir(self, host):
        return os.path.join(hostrun.host_path(self.host_root), "runs", self.run)

    def record_made(self, host, path, pid=None):
        """Runners call it from several threads (gate's slow layers): one writer at a time,
        across processes too (made.json.lock)."""
        p = os.path.join(self.mac_dir, "made.json")
        with _MADE_MU:
            fd = os.open(p + ".lock", os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                made = read_json(p, {}) or {}
                made.setdefault(host, []).append({"path": path, "pid": pid, "t": utc_now()})
                write_json(p, made)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def cells_doc(self, plan, host, attempt=1, reruns=None):
        """A host's cells.json: the plan's due cells this host may run (R1: the rekeyed keys)."""
        cp = self.copy.get(host) or {}
        out = {"run": self.run, "word": self.word.text, "attempt": attempt,
               "copy": {"id": cp.get("id"), "path": cp.get("path"), "commit": cp.get("commit"),
                        "dirty": cp.get("dirty")},
               "cells": {}}
        rows = self.ctx.ledger
        for pc in plan.cells:
            if pc.status != "due" or (pc.keys and host not in pc.keys):
                continue
            rr = (reruns or {}).get(pc.cell) if reruns is not None else None
            if reruns is not None and rr is None:
                continue
            ent = {"cell": pc.cell, "rule": pc.rule, "reasons": list(pc.reasons)[:20],
                   "planned_key": pc.keys.get(host), "attempt": int((rr or {}).get("attempt") or attempt),
                   "rerun_of": (rr or {}).get("rerun_of"), "flake": (rr or {}).get("flake"),
                   "limits_classed": limits_classed(pc.cell, self.limits, rows) if pc.family == "stic" else []}
            if rr and rr.get("rule"):
                ent["rule"] = rr["rule"]
            out["cells"][driver_id(pc.cell)] = ent
        return out

    def replan(self, facts=None, ledger_rows=None):
        """R1's step 5a (and R15's re-plan after the fast holt gate): the plan again on fresh
        facts and rows.  Returns (plan, added, dropped): cells due now that weren't, and cells
        that were due and aren't (a fresh form keeps its cells due in any plan)."""
        ctx = self.ctx
        if facts is not None:
            ctx.facts = dict(facts)
        if ledger_rows is not None:
            ctx.ledger = list(ledger_rows)
        new = planmod.make(self.word, ctx)
        was = {c.cell for c in self.plan.due()}
        now = {c.cell for c in new.due()}
        added, dropped = sorted(now - was), sorted(was - now)
        if added:
            new.notes.append("due after the build: " + ", ".join(added))
        if dropped:
            new.notes.append("no longer due after the build: " + ", ".join(dropped))
        self.plan = new
        save_plan(new, self.run, self.mac_root)
        return new, added, dropped

    def write_cells(self, plan, attempt=1, name=None, reruns=None):
        """cells.json (attempt 1) or cells-r2.json (reruns: {cell: {rerun_of, flake, rule}}) to
        HOST_ROOT/runs/RUN/ on every host, atomically (tmp, then rename).  Returns {host: path}."""
        name = name or ("cells.json" if attempt == 1 else "cells-r2.json")
        out = {}
        for h in self.hosts:
            doc = self.cells_doc(plan, h, attempt, reruns)
            write_json(os.path.join(self.mac_dir, f"{name[:-5]}-{h}.json"), doc)
            d = self.host_run_dir(h)
            data = (json.dumps(doc, indent=1, sort_keys=True) + "\n").encode()
            qd = hostrun.rq(d)
            r = hostrun.ssh(h, f"mkdir -p {qd} && cat > {qd}/{name}.tmp && mv -f {qd}/{name}.tmp {qd}/{name}",
                            input=data, timeout=60)
            if r.rc != 0:
                raise Refused(f"{h}: can't write {d}/{name}: {r.errtext.strip()[-200:]}")
            out[h] = f"{d}/{name}"
        return out


# --- derived rows (1.9 step 9) -------------------------------------------------------------------

def _copy_fields(ctx):
    cp = ctx.copy_preview or {}
    dirty = cp.get("dirty") or {}
    return {"commit": ctx.head, "committed": not dirty, "dirty": dict(dirty), "copy": cp.get("id") or "plan"}


def _meta_now(ctx, pc, host, plan):
    meta = {}
    f = ctx.facts.get(host) or {}
    s = plan.set or {}
    if pc.family in ("stic", "gate") and s.get("name"):
        st = ((f.get("sets") or {}).get(s["name"])) or {}
        if st:
            meta["bin"] = {"set": s["name"], "commits": st.get("commits") or {},
                           "digest_texts": st.get("digest_texts") or {}, "build_info": st.get("build_info")}
        elif s.get("pending"):
            meta["bin"] = {"set": s["name"], "pending": s["pending"]}
    if pc.family == "holt":
        meta["holt"] = {"code": s.get("holt_code"), "commit": ctx.head}
    return meta


def decisive_id(c):
    """A result cell's decisive row id: final_row, else its last row (attempt 2 after 1)."""
    rid = c.get("final_row")
    if not rid:
        rs = c.get("rows") or []
        rid = rs[-1] if rs else None
    return rid.get("id") if isinstance(rid, dict) else rid


def hard_change(old_inputs, new_inputs):
    """The plan's change set (plan.Planner.changed): {(group, name, new value)} of the hard groups."""
    return frozenset((g, n, b) for g, n, a, b in keys.diff_inputs(old_inputs or {}, new_inputs or {}) if g in HARD)


def _unsure(inputs):
    """The input values that can't stand for what ran: pending: (a binary not built yet),
    img:unknown (an identity not summed), missing."""
    out = []
    for g, x in (inputs or {}).items():
        for n, val in (x or {}).items() if isinstance(x, dict) else ():
            if isinstance(val, str) and (val.startswith("pending:") or val in (keys.UNKNOWN_IMG, "missing")):
                out.append(f"{g}:{n}")
    return out


def derivable(pc, host, P):
    """(inputs, None) when PC's row may be derived at HOST's key, else (None, why).  The carry or
    class decision compared one host's inputs with P (the cell's last PASS); every other host
    gets a row only when its own change against P is that same change, and none of its
    inputs is pending, unknown or missing."""
    res = pc.res.get(host)
    inputs = (getattr(pc, "inp", None) or {}).get(host) or (res.inputs if res is not None else None)
    if not inputs:
        return None, "no inputs (a plan without Resolved inputs)"
    unsure = list(getattr(res, "unknown", None) or []) + _unsure(inputs)
    if unsure:
        return None, "inputs not known: " + ", ".join(sorted(set(unsure))[:4])
    if P is None:
        return None, "its last PASS isn't in the rows"
    change = getattr(pc, "change", None)
    if change is None or hard_change(P.get("inputs"), inputs) != change:
        return None, f"its change on {host} isn't the one the plan judged"
    return inputs, None


def derived_rows(plan, run, word, ctx, result=None, rows=None, notes=None):
    """Complete carried rows (bare runs; contract 1.6 rule 4) and class rows (a representative's
    PASS covers its class-pending members, 1.6.2), for MAC_ROOT/ledger/mac.jsonl.

    PLAN is the run's final plan (after the runner's re-plans).  No row is derived for a cell
    this run ran, for a host whose inputs differ from the judged change or aren't known, or,
    for carried rows, when a fast gate or a stop-run stopped the family.  A class row needs
    the representative's decisive row: a valid, committed PASS at its planned key."""
    out = []
    notes = notes if notes is not None else []
    base = _copy_fields(ctx)
    rows = list(rows if rows is not None else ctx.ledger)
    by_id = {r.get("id"): r for r in rows}
    rcells = (result or {}).get("cells") or []
    ran = {c.get("cell") for c in rcells if c.get("rows") and c.get("outcome", "ran") == "ran"}
    ran |= {r.get("cell") for r in rows if r.get("kind") == "run" and r.get("run") in (run, f"{run}-r2")}
    stopped = [s for s in (result or {}).get("stops") or [] if str(s).startswith(("stop-family", "stop-run"))]

    def emit(pc, kind, extra):
        made = 0
        for h, k in sorted(pc.keys.items()):
            if k in [x["key"] for x in out if x["cell"] == pc.cell]:
                continue
            P = by_id.get((pc.last_pass or {}).get("id")) if kind == "class" else by_id.get(pc.via)
            inputs, why = derivable(pc, h, P)
            if inputs is None:
                notes.append(f"no {kind} row for {pc.cell} at {h}'s key: {why}")
                continue
            if kind == "carried":   # not in the key, and nothing ran with the new ones: the next plan sees the change
                inputs = dict(inputs, plumbing=(P.get("inputs") or {}).get("plumbing") or {},
                              soft=(P.get("inputs") or {}).get("soft") or {})
            res = pc.res.get(h)
            name = kind if len(set(pc.keys.values())) == 1 else f"{kind}@{h}"
            r = dict(base, origin=f"derived:{run}:{pc.cell}:{name}", family=pc.family, cell=pc.cell, kind=kind,
                     run=run, word=word.text, attempt=1, inputs=json.loads(json.dumps(inputs)),
                     spec=getattr(res, "spec", None), planned_key=None, valid=True, verdict="PASS",
                     meta=_meta_now(ctx, pc, h, plan), host="mac", start=None, **extra(P))
            r["class"] = "pass"
            r = ledger.complete(r, host="mac")
            if r.get("key") != k:
                notes.append(f"no {kind} row for {pc.cell} at {h}'s key: its inputs key as {str(r.get('key'))[:12]}")
                continue
            out.append(r)
            made += 1
        return made

    if (word.form == "bare" or word.stale) and not stopped:
        def panel_passed(pc):
            r = by_id.get(decisive_id(next((c for c in rcells if c.get("cell") == pc.cell), {})))
            return r is not None and r.get("verdict") == "PASS" and r.get("valid") is True and r.get("key") in pc.keys.values()
        panel_bad = [pc.cell for pc in plan.by_status("due") if pc.rule == "holt:rustic" and not panel_passed(pc)]
        if panel_bad and any(pc.rule == "carry:rustic-panel" for pc in plan.by_status("carried")):
            notes.append(f"no carry:rustic-panel rows: the panel didn't pass ({', '.join(panel_bad[:8])})")
        for pc in plan.by_status("carried"):
            if pc.cell in ran or (pc.rule == "carry:rustic-panel" and panel_bad):
                continue
            emit(pc, "carried", lambda P, pc=pc: dict(
                rule=pc.rule or "carry", reasons=list(pc.reasons)[:20], needs=dict((P or {}).get("needs") or {}),
                counts=dict((P or {}).get("counts") or {}), via=pc.via, note=f"carried from {pc.via}"))
    elif stopped and plan.by_status("carried"):
        notes.append(f"no carried rows: {stopped[0]}")
    reps = {}
    for c in rcells:
        reps[c.get("cell")] = c
    for cl in plan.classes:
        rep_pc, c = plan.cell(cl["rep"]), reps.get(cl["rep"])
        if rep_pc is None or not c or c.get("verdict") != "PASS":
            continue
        rid = decisive_id(c)
        rrow = by_id.get(rid)
        why = None
        if rrow is None:
            why = f"its decisive row {rid} isn't in the pulled rows"
        elif rrow.get("kind") != "run" or rrow.get("verdict") != "PASS":
            why = f"{rid} is {rrow.get('kind')} {rrow.get('verdict')}"
        elif rrow.get("valid") is not True:
            why = f"{rid} is invalid"
        elif not (rrow.get("committed") is True or copies.committed_ok(rrow, ctx.head_sha256)):
            why = f"{rid} ran from an uncommitted copy"
        elif rrow.get("key") != rep_pc.keys.get(rrow.get("host")):
            why = f"{rid}'s key isn't {cl['rep']}'s planned key on {rrow.get('host')}"
        if why:
            notes.append(f"no class rows for {cl['class']}: {why}")
            continue
        for m in cl["members"]:
            pc = plan.cell(m)
            if pc is None or pc.status != "class-pending" or m in ran:
                continue
            emit(pc, "class", lambda P, cl=cl, rid=rid, rrow=rrow: dict(
                rule=f"class:{cl['class']}", reasons=[f"{cl['rep']} passed for {cl['class']}"],
                needs=dict(rrow.get("needs") or {}), counts=dict(rrow.get("counts") or {}), via=rid))
    return out


def outcome_exit(plan, result, rows, derived=()):
    """(exit, [cell]) from the final plan: 1 unless every due cell has a valid PASS as its
    decisive row (by the pulled rows; the runner's verdict when the row isn't there), or a
    derived row.  HARNESS, LOST, an invalid PASS and a due cell that never ran all fail."""
    by_id = {r.get("id"): r for r in rows or ()}
    got = {r.get("cell") for r in derived or ()}
    cells = {}
    for c in (result or {}).get("cells") or []:
        cells[c.get("cell")] = c
    bad = []
    for pc in plan.due():
        if pc.cell in got:
            continue
        c = cells.get(pc.cell)
        if not c or (not c.get("rows") and c.get("outcome", "ran") not in ("carried", "class")):
            bad.append(f"{pc.cell} {(c or {}).get('outcome') or 'not run'}")
            continue
        r = by_id.get(decisive_id(c))
        if r is not None:
            ok = r.get("valid") is True and r.get("verdict") == "PASS"
        else:
            ok = c.get("verdict") == "PASS" and c.get("valid") is not False
        if not ok:
            bad.append(f"{pc.cell} {(r or c).get('verdict') or 'no verdict'}"
                       + (" (invalid)" if r is not None and r.get("valid") is not True else ""))
    return (EXIT_FAIL if bad else EXIT_OK), bad


def write_derived(rows, mac_root=None):
    if not rows:
        return []
    path = os.path.join(mac_root or default_mac_root(), "ledger", "mac.jsonl")
    for r in rows:
        ledger.check(r)
    return ledger.append(path, rows)


# --- dispatch, lifecycle, resume ---------------------------------------------------------------------

def runner(family):
    """t2lib.gate, t2lib.stic or t2lib.holt; None when that package isn't built."""
    try:
        m = importlib.import_module("t2lib." + RUNNERS[family])
    except ImportError:
        return None
    return m if hasattr(m, "run") else None


def save_plan(plan, run, mac_root=None):
    write_json(os.path.join(run_dir(run, mac_root), "plan.json"), plan.to_json())


def refresh_rows(ctx, pull, out):
    """The rows again, now that the lock is ours (R21): a run that finished between the CLI's
    pull and the lock counts."""
    if pull is None:
        return
    try:
        rows = pull()
    except Exception as e:   # noqa: BLE001 - the earlier pull's rows stand, and the run says so
        out(f"t2: the pull under the lock failed, the rows pulled before it stand: {e}")
        return
    if rows is not None:
        ctx.ledger = list(rows)


def lifecycle(word, ctx, mac_root=None, host_root=None, out=print, pull=None):
    """Run WORD (not `plan`).  ctx has pulled rows and fresh facts (steps 1-2).  Returns the exit code."""
    fam = word.family
    mod = runner(fam)
    if pull is not None:
        ctx.options.setdefault("pull", pull)
    lock = FamilyLock(fam, mac_root)
    try:
        lock.acquire("pending")
    except Busy as e:
        out(f"t2: {e}")
        return EXIT_REFUSED
    try:
        refresh_rows(ctx, pull, out)
        # 3 preflight
        hosts = [h for h in ctx.hosts]
        bad = preflight(ctx.facts, hosts)
        if bad:
            out("t2: preflight refuses: " + "; ".join(bad))
            return EXIT_REFUSED
        busy = unfinished_runs(fam, mac_root)   # file times: the real clock
        if busy:
            out(f"t2: run {busy[-1]} of {fam} is unfinished (it began host work and has no report): its checks "
                f"or queue runners may still run on the hosts. `t2 resume {busy[-1]}` finishes it; once nothing "
                f"of it runs, a report.txt in {run_dir(busy[-1], mac_root)} sets it aside")
            return EXIT_REFUSED
        # never another run's: a held lock (any family), or an unfinished run whose host work may go on
        live = held_runs(mac_root) | {(lock.holder() or {}).get("run")}
        live |= set(unfinished_runs(None, mac_root))
        lefts = our_leftovers(mac_root, live)
        if lefts:
            remove_leftovers(lefts, out)
        # 4 plan
        plan = planmod.make(word, ctx)
        miss = (plan.set or {}).get("missing_on")
        if miss:
            out(f"t2: {plan.set['dir']} isn't on {', '.join(miss)}: a ROUND must be on every host in use "
                "(stick/build-round.sh ROUND, or --hosts)")
            return EXIT_REFUSED
        run = new_run_id(fam)
        write_json(lock.info, {"run": run, "pid": os.getpid(), "start": utc_now()})
        if not plan.totals.get("due"):
            nr = plan.by_status("not-runnable")
            if word.form != "bare" and nr:
                # the cells the word names can't run: say why, and refuse
                out(report.render_plan(plan).rstrip())
                return EXIT_REFUSED
            notes = []
            rows = derived_rows(plan, run, word, ctx, notes=notes)
            write_derived(rows, mac_root)
            out(report.nothing_due_line(plan).rstrip() + (f"; {len(rows)} carried row(s) written" if rows else ""))
            for n in notes[:10]:
                out("note: " + n)
            return EXIT_OK
        if mod is None:
            out(f"t2: {word.word}'s runner (t2lib/{RUNNERS[fam]}.py) is not built; its plan:")
            out(plan.render().rstrip())
            return EXIT_REFUSED
        save_plan(plan, run, mac_root)
        ctx.options["facts_start"] = {h: dict(f) for h, f in ctx.facts.items()}
        ctx.options["run_start"] = time.time()
        rs = RunState(run, word, plan, ctx, mac_root, host_root, log=out)
        ctx.run = rs
        rs.log(f"{word.word} run {run}: {plan.totals['due']} due, est. {plan.totals['gm']} gm")
        # 5 copy
        try:
            made = copies.make(ctx.checkout, rs.hosts, log=rs.log)
        except copies.CopyError as e:
            out(f"t2: no private copy: {e}")
            return EXIT_REFUSED
        pv = ctx.copy_preview or {}
        for h, c in made.items():
            rs.copy[h] = {"id": c.id, "path": c.path, "commit": pv.get("commit") or ctx.head, "dirty": pv.get("dirty")}
        # 5a-7: the runner builds, rekeys, writes cells.json (rs.write_cells) and runs
        result = mod.run(ctx, plan) or {}
        result = result if isinstance(result, dict) else getattr(result, "__dict__", {})
        # 8-10 on the runner's last plan (R1 5a, R15: its re-plans live in rs.plan)
        return finish(word, ctx, rs.plan or plan, run, result, mac_root, out, pull)
    finally:
        lock.release()


WEAR_DRIVER = r'''
_d = json.loads(_T2_DATA)
_run, _lo = _d["run"], _d.get("lo")
_jobs = read_jobs(_d.get("log") or DEFAULT_LOG) or []
_ours = collections.Counter()
for _j in _jobs:
    _parts = (_j.tag or "").split(":")
    if (_run in _parts or _run + "-r2" in _parts) and (_lo is None or (_j.t or 0) >= _lo):
        for _k, _v in _j.disks.items():
            if physical(_k):
                _ours[_k] += _v
try:
    with open(_d.get("smart") or DEFAULT_SMART) as _f:
        _st = smart_today(_f.read(), _d["day"])
except OSError:
    _st = []
sys.stdout.write(json.dumps({"ours": dict(_ours), "smart": _st}, sort_keys=True) + "\n")
'''


def wear_lines_data(run, hosts, before, after, start_epoch, now=None, timeout=60, log=None, smart=None):
    """{host: {disk: {model, window_bytes, ours_bytes, today_bytes}}} for the report's wear
    lines: diskstats between the run's start and end facts (all writers), this run's jobs
    by their wear tags (jobs.log, R22), and the day by SMART (smart.log).  wear.py's own
    functions run on each host (stdin, read only); no serial comes back."""
    from . import wear
    src_path = wear.__file__
    try:
        with open(src_path, encoding="utf-8") as f:
            src = f.read()
    except OSError:
        return {}
    marker = 'if __name__ == "__main__":'
    if marker not in src:
        return {}
    script = src.replace(marker, "if False:") + WEAR_DRIVER
    day = time.strftime("%Y-%m-%d", time.gmtime(now or time.time()))
    out = {}
    for h in hosts:
        if h == "mac":
            continue
        win = wear.diskstats_delta(((before.get(h) or {}).get("preflight") or {}).get("diskstats"),
                                   ((after.get(h) or {}).get("preflight") or {}).get("diskstats"))
        r = hostrun.ssh(h, script=script, data={"run": run, "lo": (start_epoch or 0) - 60, "day": day,
                                                "log": log or os.environ.get("T2_WEAR_LOG"),
                                                "smart": smart or os.environ.get("T2_SMART_LOG")},
                        timeout=timeout)
        got = {}
        if r.rc == 0:
            try:
                got = r.json()
            except ValueError:
                got = {}
        by_disk = {x.get("disk"): x for x in got.get("smart") or [] if not x.get("host") or x.get("host") == h
                   or h == hostrun.LOCAL}
        disks = {}
        for d, w in win.items():
            disks[d] = {"model": w.get("model"), "window_bytes": w.get("bytes"),
                        "ours_bytes": (got.get("ours") or {}).get(d, 0 if got else None),
                        "today_bytes": (by_disk.get(d) or {}).get("bytes")}
        for d, b in (got.get("ours") or {}).items():
            disks.setdefault(d, {"model": None, "window_bytes": None, "ours_bytes": b, "today_bytes": None})
        if disks:
            out[h] = disks
    return out


def finish(word, ctx, plan, run, result, mac_root=None, out=print, pull=None):
    """Steps 8-10: pull, derived rows, the report.  PLAN is the run's final plan."""
    if not result.get("wear") and ctx.options.get("facts_start") is not None:
        try:
            from . import facts as factsmod
            hs = [h for h in ctx.hosts if h in ctx.facts]
            end = {h: f for h, f in factsmod.probe_all(hs, {"sets": []}, save=False).items() if f.get("ok")}
            result["wear"] = wear_lines_data(run, hs, ctx.options["facts_start"], end,
                                             ctx.options.get("run_start"))
        except Exception as e:   # noqa: BLE001 - the report says it has no wear figures
            out(f"t2: no wear figures: {e}")
    rows = list(ctx.ledger)
    if pull:
        try:
            rows = pull() or rows
        except Exception as e:   # noqa: BLE001 - the report says it
            out(f"t2: pull after the run failed: {e}")
    notes = []
    derived = derived_rows(plan, run, word, ctx, result, rows, notes=notes)
    write_derived(derived, mac_root)
    result.setdefault("derived", [r["id"] for r in derived])
    ex = result.get("exit")
    if ex is None:
        bad = any(c.get("verdict") in FAILING for c in result.get("cells") or [])
        ex = EXIT_FAIL if bad or result.get("stops") else EXIT_OK
    ex2, unpassed = outcome_exit(plan, result, rows, derived)
    if unpassed:
        notes.append("due and not passed: " + "; ".join(unpassed[:12]) + (f"; and {len(unpassed) - 12} more"
                                                                         if len(unpassed) > 12 else ""))
    result["notes"] = list(result.get("notes") or []) + notes
    text = report.render_report(result, plan, rows, ctx.classes.get("policy"), ctx.now)
    write_text(os.path.join(run_dir(run, mac_root), "report.txt"), text)
    out(text.rstrip())
    return max(int(ex), ex2)


def adopt_inputs(saved, fresh):
    """R10: a plan.json plan has no Resolved inputs.  Its carried and class-pending cells take
    them from FRESH (the same word planned again now) where FRESH agrees: the same status and
    the same keys.  The others keep none, so no row is derived for them."""
    if fresh is None:
        return saved
    for pc in saved.cells:
        if pc.status not in ("carried", "class-pending"):
            continue
        f = fresh.cell(pc.cell)
        if f is None or f.status != pc.status or dict(f.keys) != dict(pc.keys):
            continue
        pc.res, pc.inp, pc.change = f.res, getattr(f, "inp", {}), getattr(f, "change", None)
        pc.last_pass = pc.last_pass or f.last_pass
    return saved


def resume(run, ctx, mac_root=None, out=print, pull=None):
    """R10: pick RUN up again: its plan.json, the hosts' cells.json, the runner's resume().
    ctx must be made for the run's own hosts (run_hosts); preflight runs again."""
    if not RUN_RE.match(run):
        out(f"t2: not a run id: {run}")
        return EXIT_REFUSED
    pj = read_json(os.path.join(run_dir(run, mac_root), "plan.json"))
    if not pj:
        out(f"t2: no {run_dir(run, mac_root)}/plan.json")
        return EXIT_REFUSED
    fam = pj.get("family")
    mod = runner(fam)
    if mod is None or not hasattr(mod, "resume"):
        out(f"t2: resume for {fam} is not built (t2lib/{RUNNERS.get(fam, fam)}.py resume())")
        return EXIT_REFUSED
    want = run_hosts(run, mac_root)
    if want and list(ctx.hosts) != want:
        out(f"t2: {run} ran on {','.join(want)}, not {','.join(ctx.hosts)}")
        return EXIT_REFUSED
    lock = FamilyLock(fam, mac_root)
    try:
        lock.acquire(run)
    except Busy as e:
        out(f"t2: {e}")
        return EXIT_REFUSED
    try:
        refresh_rows(ctx, pull, out)
        bad = preflight(ctx.facts, list(ctx.hosts))
        if bad:
            out("t2: preflight refuses: " + "; ".join(bad))
            return EXIT_REFUSED
        from . import words
        w = words.parse(pj["word"]["text"].split(), planmod.known_names(ctx))
        plan = plan_from_json(pj, w)
        try:
            fresh = planmod.make(w, ctx)
        except planmod.PlanError as e:
            fresh = None
            out(f"t2: no fresh plan for the derived rows ({e}): none is written")
        adopt_inputs(plan, fresh)
        rs = RunState(run, w, plan, ctx, mac_root, log=out)
        ctx.run = rs
        for h in rs.hosts:
            doc = read_json(os.path.join(rs.mac_dir, f"cells-{h}.json"), {}) or {}
            rs.copy[h] = doc.get("copy") or {}
        result = mod.resume(ctx, plan) or {}
        result = result if isinstance(result, dict) else getattr(result, "__dict__", {})
        return finish(w, ctx, rs.plan or plan, run, result, mac_root, out, pull)
    finally:
        lock.release()


def plan_from_json(pj, word):
    """A Plan back from plan.json (no Resolved inputs: adopt_inputs takes them from a fresh plan)."""
    p = planmod.Plan(word, pj.get("family"))
    p.header, p.set, p.totals = pj.get("header") or {}, pj.get("set"), pj.get("totals") or {}
    p.notes, p.classes, p.unmapped = list(pj.get("notes") or []), list(pj.get("classes") or []), list(pj.get("unmapped") or [])
    for c in pj.get("cells") or []:
        pc = planmod.PlanCell(c["cell"], c["cell"].split(":", 1)[0])
        for k in ("status", "rule", "reasons", "host", "keys", "last_pass", "est", "rep", "klass", "running", "via",
                  "differ", "part", "placed"):
            if k in c:
                setattr(pc, k, c[k])
        p.cells.append(pc)
    return p
