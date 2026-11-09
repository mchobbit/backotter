"""SticTest2's runner: it carries out a plan.make() plan of family stic (contract
2.2; rulings R1, R3, R6, R10, R21, R22).  It decides no scope: the plan does.

    run(ctx, plan) -> result       steps 5a-7 of the run (1.9), for run.py's lifecycle
    resume(ctx, plan) -> result    t2 resume RUN (R10): the same drive.py marks again
                                   (drive.py adopts what already started and writes
                                   the rows still missing), then the rest

run.py's lifecycle has made the run (ctx.run, its RunState: run id, directories,
the copy, write_cells, replan, the StopEngine) after the pull, facts, preflight,
plan and copy (steps 1-5), and does the pull, the derived rows and the report
after (steps 8-10).  Called without a ctx.run (by hand, or by tests), the run is
this module's own: ctx.options["run"] gives its id, directories and copy (or it
makes them), it takes MAC_ROOT/runs/.lock-stic, and it writes the derived rows.

The flow (2.2):
  1. The round: built when the plan's set is pending (the fork heads' round,
     stick/build-round.sh from the copy, release profiles); then every key again
     from the real binaries (R1 5a): the cells that turned due are added, the
     ones that turned current dropped unless a fresh form named them.
  1b. The image sums the plan lacks (contract 1.4: the run's preflight makes
     them): a guest without image-state (sculpt2610) keys by the content sum of
     its disks, which the copy's t2-host imgsum makes on each host (read only,
     cached by path, size, mtime and inode); then every key again.  Without it
     such a guest's rows could never be valid.
  2. cells.json on every host (R1 6), each host's planned keys in it.
  3. The fast archiver gate (gate:fork:*, gate:fork-root:restic, gate:bin:*)
     when the gate plan has any of them due: a failure starts no guest, and a
     gate that can't be loaded or planned refuses the run (exit 2).
  4. drive.py --t2-run RUN --t2-copy COPY --t2-cells CELLS --logs-mark RUN ...
     with the due checks (canaries first after a plumbing change, drive.py's
     longest first otherwise), the slow gate (gate:loop:*) beside it.  As rows
     land (drive.py writes each as its guest ends, R10) the stop rules read
     them: one need failing on 2 guests of an OS puts that OS's checks in
     --skip-file; 2 harness results (known flakes aside), a failed slow gate,
     swap-out over 1 GiB on a host since the run began, or this run's own jobs
     (their wear tags in jobs.log, R22) writing over 2 x the run budget start
     nothing more (--stop-file).  A resumed run waits for a drive.py still
     running on the same --out, and counts the rows its first part fed again;
     an error here stops our drive.py (SIGINT, then SIGTERM: its PID is ours),
     whose started checks run on for a resume.
  5. One more drive.py round, mark RUN-r2 and cells-r2.json, for the reruns of
     the FAILs (attempt 2, rerun_of attempt 1's row, the known flake attempt 1's
     FAIL lines match: rows_stic's full-line match) and the class members of a
     representative whose attempt 1 FAILed (fanout:CLASS, their attempt 1),
     a known flake or not, unless the run was stopped.
  6. The result: per cell, ran or not, its rows (the decisive one last, also
     as final_row) and final verdict, for run.py's derived rows (a class row
     for each class-pending member of a representative that passed) and report.
     An attempt-2 HARNESS or LOST leaves attempt 1's FAIL standing.
Exit: 2 when the run was refused (the gate can't be loaded or planned; drive.py
ended non-zero having started nothing and written no row); else 1 when any cell
it had to run didn't end in a valid PASS (FAIL, FLAKY-FAIL, HARNESS, LOST, a
PASS whose row is invalid, no row) or a stop rule fired; else 0.
The state goes to MAC_ROOT/runs/RUN/stic.json after each step, for resume.
Every I/O goes through an Env (Env is the real one; tests give their own).
"""

import errno
import fcntl
import json
import os
import random
import signal
import subprocess
import sys
import threading
import time

from . import mac_root, utc_now

DRIVE = os.path.join("testvm", "stictest1", "drive.py")
POLL = 30
HOST_EVERY = 4                         # polls between the host reads of R22's swap and write rules
RERUN_VERDICTS = ("FAIL",)             # 1.9: a FAIL reruns once
FAN_VERDICTS = ("FAIL", "FLAKY-FAIL")  # 1.6.2: a representative's FAIL or FLAKY-FAIL fans out
STOPPING = ("stop-run", "stop-starts", "stop-family")
INCONCLUSIVE = ("HARNESS", "LOST")     # an attempt 2 like this leaves attempt 1's FAIL standing
HOST_ROOT_DEFAULT = "/bkoscratch/test2"
GIB = 1 << 30
EXIT_OK, EXIT_FAIL, EXIT_REFUSED = 0, 1, 2

try:
    from .run import Refused as _Refused   # t2 prints a Refused on one line and exits 2
except Exception:   # pragma: no cover - run.py (P2) not importable
    class _Refused(Exception):
        """A run that can't start (exit 2)."""


class SticError(_Refused):
    """The run can't go on (exit 2: refused)."""


def new_run_id(prefix="t2s", t=None):
    """t2s-YYYYMMDDTHHMM-XXXX (contract 1.1)."""
    return f"{prefix}-{time.strftime('%Y%m%dT%H%M', time.gmtime(t))}-{random.getrandbits(16):04x}"


def guest_of(cell):
    return cell.split(":", 1)[1] if cell.startswith("stic:") else cell


def _host_path(p):
    try:
        from . import hostrun
        return hostrun.host_path(p)
    except ImportError:   # pragma: no cover
        return p


def cut(text, n=160):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 3] + "..."


# --- the stop rules, when run.py's StopEngine isn't there ----------------------------------------

class Stops:
    """1.9's rules on SticTest2's own rows and hosts: one need failing on
    OS_NEED_GUESTS guests of an OS stops that OS; HARNESS_RESULTS harness-class
    results (known flakes aside) stop the run; swap-out over SWAP_OUT_GIB since
    the run began, or this run's jobs writing over WRITE_BUDGET_X x the run
    budget (R22), start nothing more.  feed(res) and feed_host(host, swap, ours)
    answer "ok" | "stop-os OS" | "stop-run" | "stop-starts", as run.py's
    StopEngine does (it also has the gate rules)."""

    def __init__(self, policy=None):
        stop = (policy or {}).get("stop") or {}
        budget = (policy or {}).get("budget") or {}
        self.os_need = int(stop.get("os_need_guests", 2))
        self.harness_max = int(stop.get("harness_results", 2))
        self.swap_max = float(stop.get("swap_out_gib", 1)) * GIB
        self.write_max = float(stop.get("write_budget_x", 2)) * float(budget.get("run_gb", 2)) * 1e9
        self.failed, self.harness, self.stopped_os = {}, 0, set()
        self.stop_run = self.stop_starts = False
        self.stops = []

    def feed(self, res):
        out = "ok"
        if res.get("class") in ("harness", "lost") and not res.get("flake"):
            self.harness += 1
            if self.harness >= self.harness_max and not self.stop_run:
                self.stop_run = True
                self.stops.append(f"stop-run: {self.harness} harness-class results (last {res.get('cell')})")
                return "stop-run"
        osn = res.get("os")
        if res.get("verdict") in FAN_VERDICTS and osn and res.get("class") != "known-flake" and not res.get("flake"):
            for need, v in (res.get("needs") or {}).items():
                if v not in ("FAIL", "LIMIT-NEW"):
                    continue
                s = self.failed.setdefault((osn, need), set())
                s.add(res.get("cell"))
                if len(s) >= self.os_need and osn not in self.stopped_os:
                    self.stopped_os.add(osn)
                    self.stops.append(f"stop-os {osn}: need {need} failed on {', '.join(sorted(s))}")
                    out = f"stop-os {osn}"
        return out

    def feed_host(self, host, swap_out_bytes=None, ours_bytes=None):
        if swap_out_bytes is not None and swap_out_bytes > self.swap_max and not self.stop_starts:
            self.stop_starts = True
            self.stops.append(f"stop-starts: {host}: {swap_out_bytes / GIB:.2f} GiB swapped out since the run began")
            return "stop-starts"
        if ours_bytes is not None and ours_bytes > self.write_max and not self.stop_starts:
            self.stop_starts = True
            self.stops.append(f"stop-starts: {host}: this run's jobs wrote {ours_bytes / 1e9:.2f} GB, "
                              f"over {self.write_max / 1e9:g} GB")
            return "stop-starts"
        return "ok"


# --- the real I/O ----------------------------------------------------------------------------

# Run on a host after wear.py's own functions (stdin, read only): this run's bytes on
# the physical disks by its wear tags in jobs.log (R22: never other sessions'), and
# the host's pswpout counter.
HOST_LOAD = r'''
_d = json.loads(_T2_DATA)
_run, _lo = _d["run"], _d.get("lo")
_jobs = read_jobs(_d.get("log") or DEFAULT_LOG) or []
_ours = 0
for _j in _jobs:
    _p = (_j.tag or "").split(":")
    if (_run in _p or _run + "-r2" in _p) and (_lo is None or (_j.t or 0) >= _lo):
        _ours += _j.physical_bytes()
_sw = None
try:
    with open("/proc/vmstat") as _f:
        for _l in _f:
            if _l.startswith("pswpout "):
                _sw = int(_l.split()[1])
except OSError:
    pass
import os as _os
sys.stdout.write(json.dumps({"ours": _ours, "pswpout": _sw, "page": _os.sysconf("SC_PAGE_SIZE")}) + "\n")
'''


class Env:
    """What a run does to the world: ssh to the hosts, the ledger, drive.py, time."""

    def __init__(self, ctx):
        self.ctx = ctx

    def now(self):
        return time.time()

    def sleep(self, s):
        time.sleep(s)

    def log(self, msg):
        rs = getattr(self.ctx, "run", None)
        if rs is not None and hasattr(rs, "log"):
            rs.log(msg)
        else:
            print(time.strftime("%H:%M:%S ") + msg, flush=True)

    def put(self, host, path, text):
        """PATH (on HOST, ~/ allowed) written whole, then renamed into place."""
        from . import hostrun
        d, q = os.path.dirname(path), hostrun.rq
        r = hostrun.ssh(host, f"mkdir -p {q(d)} && cat > {q(path + '.tmp')} && mv -f {q(path + '.tmp')} {q(path)}",
                        input=text.encode("utf-8"), timeout=60)
        if r.rc != 0:
            raise SticError(f"{host}: can't write {path}: {r.errtext.strip()[-200:]}")

    def rows(self, hosts):
        """The merged ledger after a pull of HOSTS (an unreachable host keeps its mirror)."""
        from . import ledger
        try:
            ledger.pull(hosts, wear=False)
        except Exception as e:   # noqa: BLE001 - the rows already pulled still count
            self.log(f"pull: {type(e).__name__}: {e}")
        return list(ledger.view(fill_wear=False).rows)

    def append_mac(self, rows):
        from . import ledger, mac_ledger_file
        done = []
        for r in rows:
            c = ledger.complete(r, host="mac")
            e = ledger.validate(c)
            if e:
                raise SticError(f"derived row {r.get('origin')}: " + "; ".join(e[:6]))
            done.append(c)
        return ledger.append(mac_ledger_file(), done) if done else []

    def spawn(self, argv, logpath, env):
        """drive.py, here: a Popen, its output to LOGPATH (in our process group: a
        Ctrl-C reaches it too, and it saves its state and stops starting)."""
        os.makedirs(os.path.dirname(logpath), exist_ok=True)
        with open(logpath, "ab") as out:
            return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, env=env)

    def stop_child(self, p, log=None):
        """Our own drive.py (P: the Popen we started, its PID ours) when the run stops
        early: a moment to end by itself (a Ctrl-C reached it too), then SIGINT (it saves
        its state and stops starting; the checks it started run on, for a resume), then
        SIGTERM."""
        for sig, wait in ((None, 5), (signal.SIGINT, 60), (signal.SIGTERM, 10)):
            if sig is not None:
                if log:
                    log(f"drive.py (pid {p.pid}) still runs: {signal.Signals(sig).name}")
                try:
                    p.send_signal(sig)
                except OSError:
                    return
            try:
                p.wait(timeout=wait)
                return
            except subprocess.TimeoutExpired:
                continue

    def driver_busy(self, out):
        """Whether a drive.py holds OUT/driver.lock (a resumed run's earlier one, still running)."""
        p = os.path.join(out, "driver.lock")
        try:
            fd = os.open(p, os.O_RDONLY)
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError as e:
            return e.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)

    def drive_state(self, out):
        """drive.py's OUT/state.json (its jobs: the guests it started), or None."""
        try:
            with open(os.path.join(out, "state.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def tail(self, path, n=3):
        try:
            with open(path, "rb") as f:
                f.seek(max(0, os.fstat(f.fileno()).st_size - 8192))
                lines = f.read().decode("utf-8", "replace").splitlines()
        except OSError:
            return []
        return [cut(x, 200) for x in lines[-n:] if x.strip()]

    def run(self, argv, env=None, timeout=None):
        return subprocess.run(argv, env=env, timeout=timeout)

    def gate(self):
        try:
            from . import gate
            return gate
        except Exception as e:   # noqa: BLE001 - a gate that doesn't import refuses the run
            self.log(f"gate: t2lib/gate.py doesn't import: {type(e).__name__}: {e}")
            return None

    def replan(self, word, ctx):
        from . import plan as plan_mod
        return plan_mod.make(word, ctx)

    def refresh_facts(self, ctx, sets):
        from . import facts, keys
        got = facts.probe_all(ctx.hosts, keys.wants(ctx.specs, ("stic", "holt", "gate"), sets))
        return {h: f for h, f in got.items() if f.get("ok")}

    def imgsum(self, host, tool, host_root, paths):
        """t2-host imgsum PATHS on HOST (the copy's TOOL, the run's HOST_ROOT for its cache):
        (ok, error text)."""
        from . import hostrun
        q = hostrun.rq
        cmd = (f"T2_HOST_ROOT={q(host_root)} timeout -k 10 {120 + 60 * len(paths)} python3 -B {q(tool)} imgsum "
               + " ".join(q(p) for p in paths))
        r = hostrun.ssh(host, cmd, timeout=180 + 60 * len(paths))
        return r.rc == 0, r.errtext.strip()[-300:]

    def host_load(self, host, run, lo):
        """{ours, pswpout, page} on HOST (R22's inputs), or None."""
        from . import hostrun, wear
        try:
            with open(wear.__file__, encoding="utf-8") as f:
                src = f.read()
        except OSError:
            return None
        marker = 'if __name__ == "__main__":'
        if marker not in src:
            return None
        r = hostrun.ssh(host, script=src.replace(marker, "if False:") + HOST_LOAD,
                        data={"run": run, "lo": lo, "log": os.environ.get("T2_WEAR_LOG")}, timeout=60)
        if r.rc != 0:
            return None
        try:
            return r.json()
        except ValueError:
            return None


# --- the run -----------------------------------------------------------------------------------

class SticRun:
    def __init__(self, ctx, plan, env=None, info=None):
        self.ctx, self.plan = ctx, plan
        self.env = env or Env(ctx)
        rs = getattr(ctx, "run", None)
        self.rs = rs if rs is not None and hasattr(rs, "write_cells") else None
        opts = getattr(ctx, "options", None) or {}
        self.info = dict(info or opts.get("run") or {})
        self.policy = (ctx.classes or {}).get("policy") or {}
        self.state, self.stops, self.lock = {}, [], None
        self.engine = self.rs.stops if self.rs is not None and getattr(self.rs, "stops", None) else Stops(self.policy)
        self.mu = threading.RLock()   # the state, stic.json and the stop files: the slow gate's thread too
        self.seen = set()
        self._os = None
        self._gctx = None

    def _env(self, name):
        return getattr(self.env, name, None)

    # -- set up --
    def setup(self):
        info, rs = self.info, self.rs
        if rs is not None:
            info.update(id=rs.run, word=rs.word.text, mac_dir=rs.mac_dir, hosts=list(rs.hosts))
            copies_ = {h: rs.copy.get(h) or {} for h in rs.hosts}
            saved = info.get("copy") or {}
            if saved.get("path") and any(not c.get("path") for c in copies_.values()):
                # t2 resume of a run that died before cells.json (run.py rebuilds the copy
                # from cells-HOST.json): the copy this run made, as stic.json kept it
                for h, c in copies_.items():
                    if not c.get("path"):
                        rs.copy[h] = copies_[h] = dict(saved)
            paths = {c.get("path") for c in copies_.values()}
            if len(paths) != 1 or None in paths:
                raise SticError(f"the private copy's path differs between hosts or is missing: {copies_} "
                                "(drive.py takes one --t2-copy)")
            info["copy"] = next(iter(copies_.values()))
            cells = {h: rs.host_run_dir(h) for h in rs.hosts}
            if len(set(cells.values())) != 1:
                raise SticError(f"HOST_ROOT differs between hosts: {cells} (drive.py takes one --t2-cells)")
            info["run_dir"] = next(iter(cells.values()))
        else:
            info.setdefault("hosts", list(self.ctx.hosts))
            info.setdefault("id", new_run_id("t2s"))
            info.setdefault("word", getattr(self.plan.word, "text", None) or "SticTest2")
            info.setdefault("mac_dir", os.path.join(mac_root(), "runs", info["id"]))
            hr = info.get("host_root") or _host_path(os.environ.get("T2_HOST_ROOT") or HOST_ROOT_DEFAULT)
            info["run_dir"] = f"{hr.rstrip('/')}/runs/{info['id']}"
            os.makedirs(info["mac_dir"], exist_ok=True)
            if not info.get("copy"):
                info["copy"] = self.make_copy(info["hosts"])
        os.makedirs(info["mac_dir"], exist_ok=True)
        self.refresh_set()
        info["cells_path"] = info["run_dir"] + "/cells.json"
        info["cells_r2_path"] = info["run_dir"] + "/cells-r2.json"
        return info

    def host_root(self):
        return self.info["run_dir"].rsplit("/runs/", 1)[0]

    def refresh_set(self):
        st = self.plan.set or {}
        self.info["set"] = st.get("name")
        self.info["round"] = st.get("round") or (st.get("name") or "")[len("round"):]
        self.info["bin"] = st.get("dir") or f"~/backotter-work/{st.get('name')}/bin"

    def make_copy(self, hosts):
        from . import copies, hostrun
        made = copies.make(self.ctx.checkout, hosts, log=self.env.log)
        pv = copies.preview(self.ctx.checkout)
        paths = {h: r.path for h, r in made.items()}
        if len(set(paths.values())) != 1:
            raise SticError(f"the copy's path differs between hosts: {paths} (drive.py takes one --t2-copy)")
        some = next(iter(made.values()))
        return {"id": some.id, "path": hostrun.host_path(some.path), "commit": pv.commit, "dirty": pv.dirty}

    def take_lock(self):
        """R21: one SticTest2 at a time.  run.py holds the family's lock for its runs."""
        if self.rs is not None or (getattr(self.ctx, "options", None) or {}).get("run"):
            return
        d = os.path.join(mac_root(), "runs")
        os.makedirs(d, exist_ok=True)
        f = open(os.path.join(d, ".lock-stic"), "a+")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.seek(0)
            holder = f.read().strip()
            f.close()
            raise SticError(f"another SticTest2 run holds {d}/.lock-stic: {holder or '?'}")
        f.seek(0)
        f.truncate()
        f.write(f"{self.info.get('id')} pid {os.getpid()} since {utc_now()}\n")
        f.flush()
        self.lock = f

    def save(self):
        """stic.json, whole: written by one thread at a time (the slow gate's acts too),
        to a temporary file of its own, then renamed."""
        p = os.path.join(self.info["mac_dir"], "stic.json")
        with self.mu:
            text = json.dumps({"info": self.info, "state": self.state}, indent=1, sort_keys=True, default=str)
            tmp = f"{p}.tmp.{os.getpid()}.{threading.get_ident()}"
            with open(tmp, "w") as f:
                f.write(text)
            os.replace(tmp, p)

    def log(self, msg):
        self.env.log(msg)

    # -- what the plan's cells need --
    def os_of(self):
        if self._os is None:
            drive = self.ctx.drive
            guests = drive.all_guests()
            specs = drive.load_specs(guests)
            self._os = {g: specs[g].os for g in guests}
        return self._os

    def limits_classed(self, guest):
        """R3: limits.json's entry and the LIMIT needs of the cell's newest PASS-like run row."""
        try:
            with open(os.path.join(self.ctx.checkout, "testvm", "test2", "stic", "limits.json")) as f:
                lim = set((json.load(f).get(guest) or {}).keys())
        except (OSError, ValueError):
            lim = set()
        best = None
        for r in self.ctx.ledger or ():
            if r.get("cell") == f"stic:{guest}" and r.get("kind") == "run" and r.get("verdict") == "PASS":
                if best is None or (r.get("t") or "") > (best.get("t") or ""):
                    best = r
        lim |= {n for n, v in ((best or {}).get("needs") or {}).items() if v == "LIMIT"}
        return sorted(lim)

    def prev_needs(self, pc, host):
        """The needs of the cell's newest valid PASS run row, while its own check and judge are that row's; else None."""
        now = getattr(getattr(pc, "res", {}).get(host), "groups", None) or {}
        rows = [r for r in self.ctx.ledger or () if r.get("cell") == pc.cell and r.get("kind") == "run"
                and r.get("verdict") == "PASS" and r.get("valid") is True]
        best = max(rows, key=lambda r: (r.get("t") or "", r.get("id", "")), default=None)
        g = (best or {}).get("groups") or {}
        return sorted(best["needs"]) if best and now and all(g.get(x) == now.get(x) for x in ("harness", "judge")) else None

    def flakes(self):
        if self.rs is not None and getattr(self.rs, "flakes", None) is not None:
            return list(self.rs.flakes)
        try:
            with open(os.path.join(self.ctx.checkout, "testvm", "test2", "stic", "flakes.json")) as f:
                d = json.load(f)
            return d if isinstance(d, list) else []
        except (OSError, ValueError):
            return []

    def xguests(self):
        """{guest: [the other guests its check boots]} from manifests.json's xguest."""
        try:
            with open(os.path.join(self.ctx.checkout, "testvm", "test2", "stic", "manifests.json")) as f:
                ents = (json.load(f).get("entries") or {})
        except (OSError, ValueError):
            return {}
        return {g: [x for x in (e.get("xguest") or []) if isinstance(x, str) and not x.startswith("host:")]
                for g, e in ents.items() if isinstance(e, dict)}

    # -- 2. cells.json --
    def entry(self, pc, host, attempt=1, over=None):
        k = (getattr(pc, "keys", None) or {}).get(host)
        e = {"cell": pc.cell, "rule": pc.rule, "reasons": list(pc.reasons or [])[:20],
             "planned_key": k or pc.key, "attempt": attempt, "rerun_of": None, "flake": None,
             "limits_classed": self.limits_classed(guest_of(pc.cell)), "prev_needs": self.prev_needs(pc, host)}
        e.update(over or {})
        return e

    def write_cells(self, attempt=1, reruns=None):
        """cells.json, or (REruns: {cell: {attempt, rerun_of, flake, rule}}) cells-r2.json, on every host."""
        name = "cells.json" if attempt == 1 else "cells-r2.json"
        if self.rs is not None:
            if reruns is not None:
                from . import run as runmod
                runmod.fanout_due(self.plan, reruns)
            return self.rs.write_cells(self.plan, attempt, name, reruns)
        c = self.info["copy"]
        out = {}
        for h in self.info["hosts"]:
            cells = {}
            for pc in self.plan.cells:
                if reruns is not None:
                    rr = reruns.get(pc.cell)
                    if rr is None:
                        continue
                    cells[guest_of(pc.cell)] = self.entry(pc, h, int(rr.get("attempt") or 2), {
                        k: v for k, v in rr.items() if k in ("rerun_of", "flake", "rule", "reasons")})
                elif pc.status == "due":
                    cells[guest_of(pc.cell)] = self.entry(pc, h)
            doc = {"v": 1, "run": self.info["id"], "word": self.info["word"], "family": "stic", "attempt": attempt,
                   "copy": {"id": c.get("id"), "path": c.get("path"), "commit": c.get("commit"), "dirty": c.get("dirty")},
                   "cells": cells}
            path = self.info["run_dir"] + "/" + name
            self.env.put(h, path, json.dumps(doc, indent=1, sort_keys=True) + "\n")
            out[h] = path
        return out

    # -- 1. the round, 1b. the image sums: then every key again (R1 5a) --
    def rekey(self, facts, why):
        before = self.plan
        fresh = {c.cell: c for c in before.due() if (c.rule or "").startswith("fresh:")}
        if self.rs is not None:
            new, added, dropped = self.rs.replan(facts)
        else:
            self.ctx.facts = facts
            new = self.env.replan(self.plan.word, self.ctx)
            was, now = {c.cell for c in before.due()}, {c.cell for c in new.due()}
            added, dropped = sorted(now - was), sorted(was - now)
        for c in new.cells:   # a fresh form keeps its cells
            if c.cell in fresh and c.status != "due":
                c.status, c.rule, c.reasons = "due", fresh[c.cell].rule, fresh[c.cell].reasons
        dropped = [d for d in dropped if d not in fresh]
        self.plan = new
        self.refresh_set()
        with self.mu:
            rk = self.state.setdefault("rekey", {"added": [], "dropped": []})
            rk["added"] = [x for x in rk["added"] if x not in dropped] + [x for x in added if x not in rk["added"]]
            rk["dropped"] = [x for x in rk["dropped"] if x not in added] + [x for x in dropped if x not in rk["dropped"]]
        self.log(f"rekeyed from {why}: {len(added)} more due ({', '.join(added) or 'none'}), "
                 f"{len(dropped)} no longer ({', '.join(dropped) or 'none'})")

    def round_step(self):
        st = self.plan.set or {}
        if not st.get("pending"):
            return
        name = st.get("round")
        if sorted(self.info["hosts"]) != ["chopin", "cinqcent"]:
            raise SticError(f"round {name} isn't built and build-round.sh needs both hosts: give ROUND or drop --hosts")
        self.log(f"building round {name} (stick/build-round.sh from the copy, release profiles)")
        rel = self.info["copy"]["path"]
        rel = rel[2:] if rel.startswith("~/") else rel
        env = dict(os.environ, BKO_REMOTE_DIR=rel, BKO_PROFILE="release", BKO_GUEST_PROFILE="release",
                   PYTHONDONTWRITEBYTECODE="1")
        r = self.env.run(["bash", os.path.join(self.ctx.checkout, "stick", "build-round.sh"), name], env=env)
        if r.returncode != 0:
            raise SticError(f"build-round.sh {name} failed (exit {r.returncode})")
        facts = dict(self.ctx.facts)
        facts.update(self.env.refresh_facts(self.ctx, [st.get("name")]))
        self.rekey(facts, "the built binaries")

    def imgsum_step(self):
        """Contract 1.4: the image sums of the guests these cells boot that have no
        image-state and no cached sum (sculpt2610), made on each host by the copy's
        t2-host imgsum into the run's HOST_ROOT/cache; then every key again."""
        fn = self._env("imgsum")
        if fn is None:
            return
        xg = self.xguests()
        want = set()
        for c in self.plan.cells:
            if c.status in ("due", "class-pending"):
                g = guest_of(c.cell)
                want.add(g)
                want.update(xg.get(g, []))
        todo = {}
        for h in self.info["hosts"]:
            gs = ((self.ctx.facts or {}).get(h) or {}).get("guests") or {}
            for g in sorted(want):
                gf = gs.get(g)
                if not isinstance(gf, dict) or gf.get("generation") or gf.get("imgsum"):
                    continue
                paths = [d.get("path") for d in gf.get("disks") or []
                         if isinstance(d, dict) and d.get("path") and d.get("size") is not None]
                if paths:
                    todo.setdefault(h, {})[g] = paths
        if not todo:
            return
        tool = self.info["copy"]["path"].rstrip("/") + "/testvm/test2/t2-host"
        made, failed = [], []
        for h, gs in sorted(todo.items()):
            paths = [p for g in sorted(gs) for p in gs[g]]
            self.log(f"imgsum on {h}: {', '.join(sorted(gs))} ({len(paths)} image(s)), for the guests' identity")
            ok, err = fn(h, tool, self.host_root(), paths)
            if ok:
                made += [f"{g}@{h}" for g in sorted(gs)]
            else:
                failed += [f"{g}@{h}" for g in sorted(gs)]
                self.log(f"imgsum on {h} failed: {cut(err, 200)}: those rows stay invalid (guest identity unknown)")
        with self.mu:
            self.state["imgsum"] = {"made": made, "failed": failed}
        if made:
            facts = dict(self.ctx.facts)
            facts.update(self.env.refresh_facts(self.ctx, [self.info.get("set")]))
            self.rekey(facts, "the image sums")

    # -- 3. the gate --
    def gate_ctx(self):
        """The run's ctx with facts that also hold the gate's wants (its shared: plumbing and
        host inputs): a SticTest2 word's facts are probed for stic only, and gate keys measured
        on them aren't the GateTest2 plan's, so its current layers would be due here and every
        gate row's key would differ from its planned one.  One probe per run, the run's ctx
        itself when the Env can't probe."""
        if self._gctx is None:
            import copy
            fn, more = self._env("refresh_facts"), {}
            if fn is not None:
                try:
                    more = fn(self.ctx, [self.info.get("set")]) or {}
                except Exception as e:   # noqa: BLE001 - the gate plans on the run's facts then
                    self.log(f"gate: facts for the gate's inputs: {type(e).__name__}: {cut(e, 200)}")
            if more:
                c = copy.copy(self.ctx)
                f = dict(self.ctx.facts or {})
                f.update(more)
                c.facts = f
                self._gctx = c
            else:
                self._gctx = self.ctx
        return self._gctx

    def gate_step(self, part):
        """The gate layers of PART (fast: the archivers'; slow: loop) the gate plan has due.
        (ok, refused): refused names why the gate can't run at all (then ok is False)."""
        gate = self.env.gate()
        if gate is None:
            return False, f"the {part} archiver gate can't run: t2lib/gate.py doesn't import"
        try:
            names = (gate.family_layers("stic") or {}).get(part) or []
            gctx = self.gate_ctx()
            gp = gate.gate_plan(gctx) if hasattr(gate, "gate_plan") else None
        except Exception as e:   # noqa: BLE001 - a gate that can't be planned refuses the run
            return False, f"the {part} archiver gate can't be planned: {type(e).__name__}: {cut(e, 200)}"
        if gp is None:
            return False, f"the {part} archiver gate can't be planned"
        due = [c.cell for c in gp.due() if c.cell[len("gate:"):].split("@")[0] in names]
        if not due:
            return True, None
        self.log(f"gate {part}: {', '.join(due)}")
        try:
            res = gate.run_layers(gctx, due, part, plan=gp)
        except Exception as e:   # noqa: BLE001
            self.log(f"gate {part}: {type(e).__name__}: {e}")
            return False, None
        ok = res.get("ok", res.get("exit", 1) == 0) if isinstance(res, dict) else bool(getattr(res, "ok", False))
        if isinstance(res, dict):   # the report's lines for the layers this run ran (contract 1.9)
            with self.mu:
                self.state.setdefault("gate_cells", []).extend(res.get("cells") or [])
        return bool(ok), None

    def slow_gate(self):
        """The slow gate beside the guests (a thread): a failure starts nothing more."""
        try:
            ok, refused = self.gate_step("slow")
        except Exception as e:   # noqa: BLE001 - a thread's error, said and stopped on
            ok, refused = False, f"the slow gate: {type(e).__name__}: {cut(e, 200)}"
        with self.mu:
            self.state["gate_slow"] = ok
        if not ok:
            self.act("stop-starts", refused or "the slow gate failed")
        else:
            self.save()

    # -- 4./5. drive.py --
    def drive_argv(self, mark, guests, cells_path, attempt, first):
        c = self.info["copy"]
        out = os.path.join(self.info["mac_dir"], "drive" if mark == self.info["id"] else "drive-r2")
        argv = [sys.executable, "-B", os.path.join(self.ctx.checkout, DRIVE),
                "--t2-run", self.info["id"], "--t2-copy", c["path"], "--t2-cells", cells_path,
                "--t2-attempt", str(attempt), "--logs-mark", mark, "--bin", self.info["bin"], "--out", out,
                "--stop-file", os.path.join(self.info["mac_dir"], "stop"),
                "--skip-file", os.path.join(self.info["mac_dir"], "skip"),
                "--hosts", ",".join(self.info["hosts"])]
        if first:
            argv += ["--first", ",".join(first)]
        return argv + [self.info["round"]] + list(guests), out

    def first_guests(self, pcs):
        """Canaries first after a plumbing change (decision 4); otherwise drive.py's longest first."""
        return [guest_of(c.cell) for c in pcs if (c.rule or "").startswith("canary:")]

    def res_of(self, row):
        g = guest_of(row.get("cell", ""))
        return {"cell": row.get("cell"), "verdict": row.get("verdict"), "class": row.get("class"),
                "needs": row.get("needs") or {}, "os": self.os_of().get(g),
                "flake": (row.get("extra") or {}).get("flake"), "part": None}

    def ours(self, r, mark=None):
        """Whether R is a row of this run (of MARK's origins, or any of the run's marks)."""
        if r.get("run") != self.info["id"]:
            return False
        o = str(r.get("origin", ""))
        marks = [mark] if mark else [self.info["id"], self.info["id"] + "-r2"]
        return any(o.startswith(f"stic:{m}:") for m in marks)

    def feed_rows(self, mark):
        for r in self.env.rows(self.info["hosts"]):
            if not self.ours(r, mark) or r.get("id") in self.seen:
                continue
            with self.mu:
                self.seen.add(r["id"])
                self.state["fed"] = sorted(self.seen)
            ans = self.engine.feed(self.res_of(r))
            if ans and ans != "ok":
                self.act(ans, f"after {r.get('cell')} {r.get('verdict')} (row {r.get('id')})")

    def refeed(self):
        """t2 resume (R10): the rows the run's first part fed, counted again by this part's
        (new) stop engine.  Their answers aren't acted on: those stops are in the state."""
        fed = set(self.state.get("fed") or [])
        if not fed:
            return
        n = 0
        for r in self.env.rows(self.info["hosts"]):
            if r.get("id") in fed and self.ours(r):
                self.engine.feed(self.res_of(r))
                n += 1
        self.log(f"resume: {n} row(s) of the run's first part counted again by the stop rules")

    def swap0(self, host, now_pages):
        """pswpout at the run's start on HOST: the facts the run began with, else the first reading."""
        with self.mu:
            base = self.state.setdefault("swap0", {})
            if host not in base:
                opts = getattr(self.ctx, "options", None) or {}
                for f in ((opts.get("facts_start") or {}).get(host), (self.ctx.facts or {}).get(host)):
                    v = ((f or {}).get("preflight") or {}).get("pswpout")
                    if isinstance(v, int):
                        base[host] = v
                        break
                else:
                    base[host] = now_pages
            return base[host]

    def check_hosts(self):
        """R22's host rules: swap-out on a host since the run began (host-wide), and this
        run's own bytes (its wear tags in jobs.log; never other sessions' writes)."""
        fn, feed = self._env("host_load"), getattr(self.engine, "feed_host", None)
        if fn is None or feed is None:
            return
        t0 = self.state.get("t0")
        for h in self.info["hosts"]:
            try:
                got = fn(h, self.info["id"], (t0 - 60) if isinstance(t0, (int, float)) else None)
            except Exception as e:   # noqa: BLE001 - a host read that fails stops nothing
                self.log(f"host read on {h}: {type(e).__name__}: {e}")
                continue
            if not isinstance(got, dict):
                continue
            sw = got.get("pswpout")
            base = self.swap0(h, sw) if isinstance(sw, int) else None
            swap = (sw - base) * int(got.get("page") or 4096) if isinstance(sw, int) and isinstance(base, int) else None
            ours = got.get("ours")
            with self.mu:
                self.state.setdefault("host_load", {})[h] = {"swap_out_bytes": swap, "ours_bytes": ours,
                                                             "t": utc_now()}
            ans = feed(h, swap_out_bytes=swap, ours_bytes=ours)
            if ans and ans != "ok":
                said = [s for s in (getattr(self.engine, "stops", None) or []) if s.startswith(ans)]
                why = said[-1][len(ans):].lstrip(": ") if said else f"{h}: swap-out {swap}, ours {ours}"
                self.act(ans, why)

    def landed(self, mark):
        return self.final_rows(mark)

    def drive_round(self, mark, guests, cells_path, attempt, first=()):
        argv, out = self.drive_argv(mark, guests, cells_path, attempt, first)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", T2_HOST_ROOT=self.host_root())
        with self.mu:
            self.state.setdefault("drives", {})[mark] = {"argv": argv, "out": out, "guests": list(guests)}
        self.save()
        busy, n = self._env("driver_busy"), 0
        if busy is not None and busy(out):   # a resumed run's earlier drive.py runs on: no second one
            self.log(f"drive.py {mark}: an earlier drive.py still runs on {out}: waiting for it")
            while busy(out):
                self.feed_rows(mark)
                if n % HOST_EVERY == 0:
                    self.check_hosts()
                n += 1
                self.env.sleep(POLL)
        self.log(f"drive.py {mark}: {len(guests)} checks: {' '.join(guests)}")
        p = self.env.spawn(argv, os.path.join(out, "drive.out"), env)
        rc, n = None, 0
        try:
            while True:
                rc = p.poll()
                self.feed_rows(mark)
                if n % HOST_EVERY == 0:
                    self.check_hosts()
                n += 1
                if rc is not None:
                    break
                self.env.sleep(POLL)
        finally:
            if rc is None:   # an error or an interrupt here: our drive.py stops starting
                stop = self._env("stop_child")
                if stop is not None:
                    stop(p, self.log)
        landed = self.landed(mark)
        ds = (self._env("drive_state") or (lambda o: None))(out) or {}
        started = sorted(g for g, j in (ds.get("jobs") or {}).items() if g in guests)
        with self.mu:
            d = self.state["drives"][mark]
            d.update(exit=rc, rows=len(landed), started=started)
            if rc != 0 and not landed and not started:
                tail = (self._env("tail") or (lambda p, n=3: []))(os.path.join(out, "drive.out"))
                d["refused"] = f"drive.py exit {rc}, nothing started, no row" + (f": {' | '.join(tail)}" if tail else "")
        self.save()
        return rc

    def final_rows(self, mark):
        """{guest: newest row} of MARK's origins, after a pull."""
        out = {}
        for r in self.env.rows(self.info["hosts"]):
            if not self.ours(r, mark):
                continue
            g = guest_of(r.get("cell", ""))
            if g not in out or (r.get("t") or "", r.get("id", "")) >= (out[g].get("t") or "", out[g].get("id", "")):
                out[g] = r
        return out

    def act(self, ans, why=""):
        """A stop rule's answer: stop-os OS skips that OS's checks; stop-run, stop-starts and
        stop-family start nothing more."""
        kind, _, arg = ans.partition(" ")
        with self.mu:
            self.stops.append(f"{ans}: {why}" if why else ans)
            self.state["stops"] = list(self.stops)
            mdir = self.info["mac_dir"]
            if kind == "stop-os" and arg:
                gs = sorted(g for g, o in self.os_of().items() if o == arg)
                with open(os.path.join(mdir, "skip"), "a") as f:
                    f.write("\n".join(gs) + "\n")
            elif kind in STOPPING:
                open(os.path.join(mdir, "stop"), "a").close()
            self.save()
        if kind == "stop-os" and arg:
            self.log(f"STOP {arg}: {why}: no more {arg} checks start")
        elif kind in STOPPING:
            self.log(f"STOP ({kind}): {why}: nothing more starts")

    # -- 5. reruns and fan-out --
    def reruns(self, rows1):
        """cells-r2.json's cells: {cell: {attempt, rerun_of, flake, rule, reasons}}: the FAILs
        again (attempt 2, with the known flake rows_stic matched over attempt 1's full FAIL
        lines and run.out: the row's extra.flake), and the members of a representative whose
        attempt 1 FAILed (fanout:CLASS, attempt 1), a known flake or not: a run has one
        more round, so a representative's rerun that FAILs again leaves nothing uncovered."""
        out = {}
        if int(self.policy.get("rerun_fail", 1)) >= 1:
            for g, r in sorted(rows1.items()):
                if r.get("verdict") not in RERUN_VERDICTS:
                    continue
                fl = (r.get("extra") or {}).get("flake")
                out[r["cell"]] = {"attempt": 2, "rerun_of": r["id"], "flake": fl, "rule": "rerun",
                                  "reasons": [f"attempt 1 {r['verdict']} (row {r['id']})"
                                              + (" (a known flake)" if fl else "")]}
        for cl in self.plan.classes or []:
            rep = guest_of(cl.get("rep") or "")
            r = rows1.get(rep)
            if r is None or r.get("verdict") not in FAN_VERDICTS:
                continue
            fl = " (a known flake: its members run beside its rerun)" if (r.get("extra") or {}).get("flake") else ""
            for m in cl.get("members") or []:
                if guest_of(m) == rep or guest_of(m) in rows1:
                    continue
                out.setdefault(m, {"attempt": 1, "rerun_of": None, "flake": None, "rule": f"fanout:{cl.get('class')}",
                                   "reasons": [f"{cl.get('class')}'s representative {rep} {r['verdict']} "
                                               f"(row {r['id']}){fl}"]})
        return out

    # -- 6. derived rows (only when this module runs the run itself) --
    def inputs_for(self, pc, host=None):
        res = getattr(pc, "res", None) or {}
        hosts = ([host] if host else []) + [h for h in self.info["hosts"] if h != host]
        for h in hosts:
            if h in res and getattr(res[h], "inputs", None):
                return res[h].inputs, getattr(res[h], "spec", None)
        from . import keys
        src = keys.TreeSource(self.ctx.checkout)
        specs = keys.Specs(src)
        for h in hosts:
            f = (self.ctx.facts or {}).get(h)
            if f:
                r = keys.resolve(pc.cell, src, f, specs, {"set": self.info.get("set")})
                return r.inputs, r.spec
        return None, None

    def derived(self, pc, kind, via, rule, reasons, needs=None, host=None):
        inputs, spec = self.inputs_for(pc, host)
        if inputs is None:
            return None
        c = self.info["copy"]
        dirty = c.get("dirty")
        return {"origin": f"derived:{self.info['id']}:{pc.cell}:{kind}", "family": "stic", "cell": pc.cell,
                "kind": kind, "start": None, "run": self.info["id"], "word": self.info["word"], "rule": rule,
                "reasons": list(reasons)[:20], "attempt": 1, "inputs": inputs, "spec": spec, "planned_key": None,
                "valid": True, "verdict": "PASS", "class": "pass", "needs": dict(needs or {}), "counts": {},
                "fails": [], "gm": 0.0, "wall_s": 0, "waits_s": 0, "wear": {}, "ram_peak_kib": None, "seed": None,
                "commit": c.get("commit"), "committed": dirty == {}, "dirty": dirty, "copy": c.get("id") or "copy",
                "logs": None, "meta": {"derived": kind}, "extra": {}, "via": via, "note": None}

    def decisive(self, r1, r2):
        """(the cell's decisive row, its verdict): attempt 2's, unless it is HARNESS or LOST
        after attempt 1's FAIL, which then stands."""
        if r1 is not None and r2 is not None and r1.get("verdict") in RERUN_VERDICTS \
                and r2.get("verdict") in INCONCLUSIVE:
            return r1, r1.get("verdict")
        fin = r2 or r1
        return fin, (fin or {}).get("verdict")

    def derived_rows(self, rows1, rows2):
        rows, notes = [], []
        for cl in self.plan.classes or []:
            rep = guest_of(cl.get("rep") or "")
            r, v = self.decisive(rows1.get(rep), rows2.get(rep))
            if r is None or v != "PASS" or not r.get("valid"):
                continue
            ran = set(rows1) | set(rows2)
            for m in cl.get("members") or []:
                pc = self.plan.cell(m if m.startswith("stic:") else f"stic:{m}")
                if pc is None or guest_of(pc.cell) == rep or pc.status != "class-pending" or guest_of(pc.cell) in ran:
                    continue
                d = self.derived(pc, "class", r["id"], f"class:{cl.get('class')}",
                                 [f"covered by {rep}'s PASS (row {r['id']})"], r.get("needs"), r.get("host"))
                if d is None:
                    notes.append(f"no inputs for {pc.cell}: no class row")
                    continue
                d["meta"].update({"class": cl.get("class"), "rep": f"stic:{rep}"})
                rows.append(d)
        word = getattr(self.plan, "word", None)
        if getattr(word, "form", "bare") == "bare" or getattr(word, "stale", False):
            for pc in self.plan.by_status("carried"):
                if not pc.via:
                    continue
                d = self.derived(pc, "carried", pc.via, pc.rule or "carry", pc.reasons)
                if d is None:
                    notes.append(f"no inputs for {pc.cell}: no carried row")
                    continue
                rows.append(d)
        return rows, notes

    # -- the whole --
    def go(self, resume=False):
        info = self.setup()
        self.take_lock()
        st = self.state
        with self.mu:
            st.setdefault("phase", "start")
            st.setdefault("start", utc_now())
            st.setdefault("t0", self.env.now())
            self.seen = set(st.get("fed") or [])
        self.save()
        if resume:
            self.refeed()
        if st["phase"] == "start":
            self.round_step()
            self.imgsum_step()
            with self.mu:
                st["due"] = [c.cell for c in self.plan.due()]
            if st["due"]:
                self.write_cells(1)
            with self.mu:
                st["phase"] = "gate"
            self.save()
        pcs = [c for c in self.plan.cells if c.cell in set(st.get("due", []))]
        guests = [guest_of(c.cell) for c in pcs]
        if st["phase"] == "gate":
            ok, refused = self.gate_step("fast") if guests else (True, None)
            with self.mu:
                st["gate_fast"] = ok
                st["phase"] = "drive1" if ok else "after"
                if refused:
                    st["refused"] = refused
            if refused:
                self.act("stop-family", f"{refused}: no guest starts")
            elif not ok:
                self.act("stop-family", "the fast archiver gate failed: no guest starts")
            self.save()
        slow = None
        if st["phase"] == "drive1":
            if guests:
                slow = threading.Thread(target=self.slow_gate, daemon=True)
                slow.start()
                self.drive_round(info["id"], guests, info["cells_path"], 1, self.first_guests(pcs))
            with self.mu:
                st["phase"] = "drive2"
            self.save()
        rows1 = self.final_rows(info["id"])
        stopped = any(s.split(" ")[0].rstrip(":") in STOPPING for s in self.stops)
        refused1 = ((st.get("drives") or {}).get(info["id"]) or {}).get("refused")
        if st["phase"] == "drive2":
            rr = {} if refused1 else self.reruns(rows1)
            g2 = sorted(guest_of(c) for c in rr)
            if g2 and not stopped:
                with self.mu:
                    st["reruns"] = rr
                self.write_cells(2, rr)
                self.drive_round(info["id"] + "-r2", g2, info["cells_r2_path"], 2, [])
            elif g2:
                self.log(f"no rerun round: the run stopped ({'; '.join(self.stops)}): {' '.join(g2)}")
            with self.mu:
                st["phase"] = "after"
            self.save()
        if slow is not None:
            slow.join()
        rows2 = self.final_rows(info["id"] + "-r2")
        derived, ids, notes = [], [], []
        if self.rs is None:   # run.py's finish() writes them when it runs the run
            derived, notes = self.derived_rows(rows1, rows2)
            ids = self.env.append_mac(derived) if derived else []
            with self.mu:
                st["derived"] = [list(x) for x in ids]
        with self.mu:
            st["phase"] = "done"
            st["end"] = utc_now()
        self.save()
        return self.result(rows1, rows2, derived, ids, notes)

    def started(self, cell):
        g = guest_of(cell)
        return any(g in (d.get("started") or []) for d in (self.state.get("drives") or {}).values())

    def result(self, rows1, rows2, derived=(), ids=(), notes=()):
        st = self.state
        want = set(st.get("due") or []) | set(st.get("reruns") or {})
        cells, bad = [dict(c, outcome=c.get("outcome") or "ran") for c in st.get("gate_cells") or []], []
        notes = list(notes)
        by_cell = {}
        for x, (rid, _) in zip(derived, ids):
            by_cell.setdefault(x["cell"], []).append((x, rid))
        for pc in self.plan.cells:
            g = guest_of(pc.cell)
            r1, r2 = rows1.get(g), rows2.get(g)
            if r1 is not None or r2 is not None:
                fin, verdict = self.decisive(r1, r2)
                if fin is r1 and r2 is not None:
                    notes.append(f"{pc.cell}: attempt 2 {r2.get('verdict')} (row {r2.get('id')}): "
                                 f"attempt 1's {verdict} stands")
                # the decisive row last (run.py's decisive_id and the report read rows[-1]), and
                # named as final_row
                order = [x for x in (r1, r2) if x is not None and x is not fin] + [fin]
                cells.append({"cell": pc.cell, "outcome": "ran", "attempt": fin.get("attempt", 1),
                              "rule": fin.get("rule"), "reasons": fin.get("reasons") or [],
                              "rows": [x["id"] for x in order], "final_row": fin.get("id"), "verdict": verdict,
                              "class": fin.get("class"), "valid": fin.get("valid"),
                              "gm": round(sum((x or {}).get("gm") or 0 for x in (r1, r2)), 3),
                              "wall_s": sum((x or {}).get("wall_s") or 0 for x in (r1, r2)),
                              "gb": None, "host": fin.get("host")})
                if verdict != "PASS":
                    bad.append(f"{pc.cell} {verdict}")
                elif fin.get("valid") is False:
                    bad.append(f"{pc.cell} PASS, but its row {fin.get('id')} is invalid: {cut(fin.get('note'), 140)}")
                continue
            d = by_cell.get(pc.cell) or []
            if d:
                outcome = d[0][0]["kind"]
            elif pc.cell in want:
                outcome = "no-row" if self.started(pc.cell) else "not-started"
                bad.append(f"{pc.cell} {outcome}")
            elif pc.status == "class-pending":   # run.py's finish() derives its class row, or not
                outcome = "class-pending"
            else:
                outcome = "skipped"
            cells.append({"cell": pc.cell, "outcome": outcome, "attempt": 1, "rule": pc.rule,
                          "reasons": list(pc.reasons or []), "rows": [rid for _, rid in d],
                          "final_row": d[0][1] if d else None,
                          "verdict": "PASS" if d else None, "gm": 0.0, "wall_s": 0, "gb": None,
                          "host": "mac" if d else None})
        for mark, d in sorted((st.get("drives") or {}).items()):
            notes.append(f"drive.py {mark}: exit {d.get('exit')}, {d.get('rows', 0)} row(s)"
                         + (f"; REFUSED: {d['refused']}" if d.get("refused") else ""))
        if st.get("imgsum"):
            notes.append(f"imgsum: {st['imgsum']}")
        if st.get("rekey"):
            notes.append(f"rekey: {st['rekey']}")
        refused = st.get("refused") or ((st.get("drives") or {}).get(self.info["id"]) or {}).get("refused")
        if refused:
            ex = EXIT_REFUSED
            notes.append(f"refused: {refused}")
        elif bad or self.stops:
            ex = EXIT_FAIL
            if bad:
                notes.append("not passed: " + "; ".join(bad[:12]) + (f"; and {len(bad) - 12} more" if len(bad) > 12 else ""))
        else:
            ex = EXIT_OK
        return {"run": self.info["id"], "word": self.info["word"], "family": "stic", "start": st.get("start"),
                "end": st.get("end"), "cells": cells, "stops": list(self.stops), "wear": {},
                "exit": ex, "notes": notes}


def run(ctx, plan, env=None):
    """SticTest2's steps 5a-7 for PLAN (a plan.make() plan of family stic)."""
    return SticRun(ctx, plan, env).go()


def resume(ctx, plan, env=None):
    """t2 resume RUN (R10): the state in MAC_ROOT/runs/RUN/stic.json, the same drive.py marks
    again, then the rest.  The run is ctx.run's (run.py's resume) or ctx.options["run"]'s."""
    rs = getattr(ctx, "run", None)
    info = dict((getattr(ctx, "options", None) or {}).get("run") or {})
    mdir = getattr(rs, "mac_dir", None) or info.get("mac_dir") or os.path.join(mac_root(), "runs", info.get("id", "?"))
    p = os.path.join(mdir, "stic.json")
    try:
        with open(p) as f:
            saved = json.load(f)
    except (OSError, ValueError) as e:
        raise SticError(f"no SticTest2 state in {p}: {e}")
    sr = SticRun(ctx, plan, env, info=saved.get("info"))
    sr.state = saved.get("state") or {}
    sr.stops = list(sr.state.get("stops") or [])
    if sr.state.get("phase") == "done":
        sr.setup()
        res = sr.result(sr.final_rows(sr.info["id"]), sr.final_rows(sr.info["id"] + "-r2"))
        res["notes"].append("already done")
        return res
    return sr.go(resume=True)
