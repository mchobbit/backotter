"""A Test2 run's life (contract 1.9; rulings R1, R3, R10, R12, R21, R22).

    lifecycle(word, ctx, ...)   1 pull and 2 facts (the CLI has done them for ctx), 3 preflight,
                                4 plan, 5 copy, 5a-7 the family runner (it builds, rekeys and
                                writes cells.json through RunState), 8 pull, 9 derived rows,
                                10 the report
    resume(run, ...)            R10: the same run picked up again from MAC_ROOT/runs/RUN/plan.json

The pieces the runners use (t2lib.gate, t2lib.stic, t2lib.holt; their run(ctx, plan) gets this
module's RunState as ctx.run):
    new_run_id(family, stamp, suffix)   t2g-/t2s-/t2h-/t2v-/t2i-/t2b-/t2w-YYYYMMDDTHHMM-XXXX[-img]
    family_locks(word), last_sweep(mac_root)
    FamilyLock, CellLocks       R21: a family's lock; a holt run's cells under it shared (CELL_LOCKED)
    with_lock_wait(word, wait, attempt)   --lock-wait: R21's refusal waited out (lock_busy, wait_unlocked)
    Shared, RunGuards           what a composite (BkoTest2, SweepTest2) hands its children
    RunState                    the run id, its directories, the copy, cells.json writing,
                                record_made(), the StopEngine, limits and flakes
    StopEngine                  feed(result) -> ok | stop-family | stop-os OS | stop-starts | stop-run;
                                held() -> {host: why}: the hosts the swap rule (HostHolds) or the
                                host rule (HostWatch: no answer, a reboot) holds
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

from . import HARD, harness_stop, harness_stop_line
from . import copies, hostrun, host_root as default_host_root, keys, ledger, mac_root as default_mac_root, notify
from . import utc_now
from . import plan as planmod
from . import report

RUN_PREFIX = {"gate": "t2g", "stic": "t2s", "holt": "t2h", "guest": "t2v", "img": "t2i", "bko": "t2b", "sweep": "t2w"}
RUN_RE = re.compile(r"t2[gshvibw]-\d{8}T\d{4}-[0-9a-f]{4}(-img)?(-r\d)?\Z")
RUNNERS = {"gate": "gate", "stic": "stic", "holt": "holt", "guest": "guest", "img": "img", "bko": "bko", "sweep": "bko"}
ALL_FAMILIES = ("gate", "stic", "holt", "guest", "img")
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
    def __init__(self, holder, name, what=None):
        h = holder or {}
        on = f" on {what}" if what else ""
        super().__init__(f"another {name} run holds its lock{on}: run {h.get('run', '?')}, pid {h.get('pid', '?')}, "
                         f"started {h.get('start', '?')}")
        self.holder = holder
        self.name = name
        self.what = what


def new_stamp(t=None):
    """YYYYMMDDTHHMM-XXXX: the part of a run id after its prefix; a composite's children share one."""
    return f"{time.strftime('%Y%m%dT%H%M', time.gmtime(t or time.time()))}-{secrets.token_hex(2)}"


def new_run_id(family, stamp=None, suffix=None):
    """PREFIX-STAMP[-SUFFIX]: a new stamp, or a composite's (its children: t2s-S under t2b-S)."""
    tail = f"-{suffix.lstrip('-')}" if suffix else ""
    return f"{RUN_PREFIX[family]}-{stamp or new_stamp()}{tail}"


def runs_dir(mac_root=None):
    return os.path.join(mac_root or default_mac_root(), "runs")


def run_dir(run, mac_root=None):
    return os.path.join(runs_dir(mac_root), run)


# --- the run's frozen Mac tree (copies.make_tree) -------------------------------------------
# What runs on the Mac (gate:selftest, keyed on the Mac; the fork layers' fork-tests.sh) runs
# from MAC_ROOT/trees/RUN, made with the hosts' copies from the same preview, so a commit or
# an edit in the checkout mid-run can't change it or invalidate its row (gate.RunEnv).

MAC_TREE_FAMILIES = ("gate", "stic", "holt")   # the families whose runs run gate layers
MAC_TREE_GC_S = 2 * 86400   # a leftover tree of a run that isn't live, older than this, goes


def trees_dir(mac_root=None):
    return os.path.join(mac_root or default_mac_root(), "trees")


def _stamp_of(run):
    m = re.match(r"t2[a-z]-(\d{8}T\d{4}-[0-9a-f]{4})", run or "")
    return m.group(1) if m else None


def gc_mac_trees(checkout, mac_root=None, live=(), now=None):
    """Remove the Mac trees of runs that ended without removing theirs: not LIVE (nor a live
    composite's child, by its stamp) and made over MAC_TREE_GC_S ago.  The run ids removed."""
    d = trees_dir(mac_root)
    stamps = {_stamp_of(r) for r in live if r} - {None}
    now = now or time.time()
    keep = set(live)
    try:
        names = os.listdir(d)
    except OSError:
        return []
    for n in names:
        try:
            age = now - os.lstat(os.path.join(d, n, ".git")).st_mtime
        except OSError:
            age = now - os.lstat(os.path.join(d, n)).st_mtime
        if _stamp_of(n) in stamps or age < MAC_TREE_GC_S:
            keep.add(n)
    return copies.gc_trees(checkout, d, keep=keep)


def make_mac_tree(rs, ctx, pv=None, shared=None):
    """rs.mac_tree, the run's frozen Mac tree's info (copies.make_tree), also in
    MAC_ROOT/runs/RUN/mac-tree.json: the composite's when SHARED gives one, the run's own
    when it is still there unchanged (a resume), else made now from PV (the preview its
    copies were made from).  None for a family that runs nothing on the Mac.  CopyError."""
    if rs.family not in MAC_TREE_FAMILIES:
        return None
    given = getattr(shared, "mac_tree", None) if shared is not None else None
    if given:
        rs.mac_tree, rs.mac_tree_owned = dict(given), False
        return rs.mac_tree
    rec = os.path.join(rs.mac_dir, "mac-tree.json")
    old = read_json(rec)
    if old and copies.tree_info_ok(old):
        rs.log(f"Mac tree {old.get('id')}: the run's own, still at {old['path']}")
        info = old
    else:
        if old:
            rs.log(f"Mac tree {old.get('id')}: gone or changed: made again from the checkout now")
        info = copies.make_tree(ctx.checkout, os.path.join(trees_dir(rs.mac_root), rs.run), pv=pv, log=rs.log)
        write_json(rec, info)
    rs.mac_tree, rs.mac_tree_owned = info, True
    return info


def drop_mac_tree(rs, ctx, out=print):
    """The run's own Mac tree removed (at its end, the report written or not)."""
    info = getattr(rs, "mac_tree", None) if rs is not None else None
    if not info or not getattr(rs, "mac_tree_owned", False):
        return
    try:
        copies.remove_tree(ctx.checkout, info["path"])
    except OSError as e:
        out(f"t2: the Mac tree {info['path']} wasn't removed: {e}")


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


# --- one run of a family at a time (R21); holt: one run of a cell at a time ------------------------

# The families whose own runs lock cells, not the family: such a run takes the family's lock shared
# and each due cell's exclusive (CellLocks), so runs over disjoint cells go side by side.  A word
# that needs the whole family (ImgTest2's panel, BkoTest2, SweepTest2, GuestTest2 update) still
# takes it exclusive, which waits out (refuses on) every shared holder.
CELL_LOCKED = ("holt",)
IMG_UNIT = "+img"     # every +img cell's second unit: the pins (HOST_ROOT/img-pin/P) are one per platform


def pid_alive(pid):
    """A Mac PID that may still run (one that can't be asked counts as alive)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _try_flock(fd, how, wait):
    """flock(FD, HOW) within WAIT seconds; False when it stayed taken."""
    deadline = time.time() + wait
    while True:
        try:
            fcntl.flock(fd, how | fcntl.LOCK_NB)
            return True
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                raise
            if time.time() >= deadline:
                return False
            time.sleep(0.05)


def _probe(path, shared=False):
    """True when another open file holds PATH's flock (tested without taking it for long); SHARED:
    when one holds it exclusive, which is what refuses a shared holder."""
    if not os.path.exists(path):
        return False
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError as e:
        if e.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
            return True
        raise
    finally:
        os.close(fd)


class FamilyLock:
    """flock on MAC_ROOT/runs/.lock-NAME for a run's life.  Exclusive, .lock-NAME.json names the
    holder; shared (a CELL_LOCKED family's own runs), .lock-NAME.d/pid-PID-X.json names each."""

    def __init__(self, name, mac_root=None):
        self.name = name
        d = runs_dir(mac_root)
        self.path = os.path.join(d, f".lock-{name}")
        self.excl_info = self.path + ".json"
        self.dir = self.path + ".d"
        self.info = self.excl_info     # (this holder's own record, once acquired)
        self.fd = None
        self.shared = False

    def holders(self):
        """The holders' records: the exclusive one's, then each shared holder's whose process runs."""
        out = []
        h = read_json(self.excl_info)
        if h:
            out.append(h)
        try:
            names = sorted(os.listdir(self.dir))
        except OSError:
            names = []
        for n in names:
            if n.startswith("pid-") and n.endswith(".json"):
                j = read_json(os.path.join(self.dir, n))
                if isinstance(j, dict) and pid_alive(j.get("pid")):
                    out.append(j)
        return out

    def holder(self):
        hs = self.holders()
        return hs[0] if hs else None

    def held(self):
        """True when another process holds it, shared or not (tested without taking it for long)."""
        return _probe(self.path)

    def acquire(self, run, wait=1.0, shared=False):
        """Busy when another run holds it (exclusive: any holder; shared: an exclusive one).  It
        retries for WAIT seconds: a plan's held() takes the lock for an instant, and that must not
        refuse a run."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            ok = _try_flock(fd, fcntl.LOCK_SH if shared else fcntl.LOCK_EX, wait)
        except OSError:
            os.close(fd)
            raise
        if not ok:
            os.close(fd)
            raise Busy(self.holder(), self.name)
        self.fd, self.shared = fd, shared
        if shared:
            # (no exclusive holder can be there now: a record of one is a dead one's)
            try:
                os.remove(self.excl_info)
            except OSError:
                pass
            os.makedirs(self.dir, exist_ok=True)
            self.info = os.path.join(self.dir, f"pid-{os.getpid()}-{secrets.token_hex(3)}.json")
        else:
            # (nor a shared one now)
            for n in (os.listdir(self.dir) if os.path.isdir(self.dir) else ()):
                if n.startswith("pid-") and n.endswith(".json"):
                    try:
                        os.remove(os.path.join(self.dir, n))
                    except OSError:
                        pass
            self.info = self.excl_info
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


def unit_file(unit):
    return "u-" + re.sub(r"[^A-Za-z0-9.+_-]", "_", unit)


def cell_units(cell):
    """The lock units of CELL: itself, and a +img cell IMG_UNIT too."""
    return [cell, IMG_UNIT] if cell.endswith("+img") else [cell]


class CellLocks:
    """A CELL_LOCKED family's run's cells: flock (exclusive) on MAC_ROOT/runs/.lock-FAMILY.d/u-UNIT
    and u-UNIT.json {unit, run, pid, start} for each unit of its due cells (cell_units), for the
    run's life, under the family's lock taken shared.  hold() is all or none for what it adds."""

    def __init__(self, family, mac_root=None):
        self.family = family
        self.dir = FamilyLock(family, mac_root).dir
        self.fds = {}      # unit -> fd
        self.run = None

    def path(self, unit):
        return os.path.join(self.dir, unit_file(unit))

    def units(self):
        return sorted(self.fds)

    def hold(self, cells, run, wait=1.0):
        """Every unit of CELLS this holds too; Busy (and none of them added) when another run
        holds one."""
        self.run = run
        want = []
        for c in cells:
            for u in cell_units(c):
                if u not in self.fds and u not in want:
                    want.append(u)
        os.makedirs(self.dir, exist_ok=True)
        got = []
        try:
            for u in want:
                fd = os.open(self.path(u), os.O_RDWR | os.O_CREAT, 0o644)
                try:
                    ok = _try_flock(fd, fcntl.LOCK_EX, wait)
                except OSError:
                    os.close(fd)
                    raise
                if not ok:
                    os.close(fd)
                    raise Busy(read_json(self.path(u) + ".json"), self.family, u)
                self.fds[u] = fd
                got.append(u)
                write_json(self.path(u) + ".json", {"unit": u, "run": run, "pid": os.getpid(), "start": utc_now()})
        except BaseException:
            for u in got:
                self._drop(u)
            raise
        return self

    def retag(self, run):
        """The units' records say RUN (the run id, once the plan has made it)."""
        self.run = run
        for u in self.fds:
            write_json(self.path(u) + ".json", {"unit": u, "run": run, "pid": os.getpid(), "start": utc_now()})

    def _drop(self, u):
        fd = self.fds.pop(u, None)
        if fd is None:
            return
        try:
            os.remove(self.path(u) + ".json")
        except OSError:
            pass
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    def release(self):
        for u in list(self.fds):
            self._drop(u)

    @staticmethod
    def held_by(family, mac_root=None, exclude=()):
        """{unit: run} of every unit another open file holds in FAMILY's .lock-FAMILY.d, less
        the runs in EXCLUDE."""
        d = FamilyLock(family, mac_root).dir
        out = {}
        try:
            names = sorted(os.listdir(d))
        except OSError:
            return out
        for n in names:
            if not (n.startswith("u-") and n.endswith(".json")):
                continue
            j = read_json(os.path.join(d, n))
            if not isinstance(j, dict) or not j.get("unit") or j.get("run") in exclude:
                continue
            if _probe(os.path.join(d, n[:-5])):
                out[j["unit"]] = j.get("run") or "?"
        return out


def family_locks(word):
    """The lock names WORD takes (in order; a run takes each once).  sweep and bko share "bko"."""
    fam = word.family
    if fam in ("bko", "sweep"):
        return ["bko"] + list(ALL_FAMILIES)
    if fam == "guest" and getattr(word, "update", False):
        return ["bko"] + list(ALL_FAMILIES)
    if fam == "img":
        return ["img", "holt"]     # its panel is a holt run
    return [fam]


def cell_locked(word):
    """True when WORD's own run locks its cells (the family lock shared): HoltTest2's words."""
    return word.family in CELL_LOCKED and family_locks(word) == [word.family]


def acquire_locks(names, run, mac_root=None, wait=1.0, shared=()):
    """[FamilyLock] of NAMES, all held (those in SHARED shared); Busy (and none held) when any is taken."""
    held = []
    try:
        for n in names:
            held.append(FamilyLock(n, mac_root).acquire(run, wait, shared=n in shared))
    except Busy:
        for lk in held:
            lk.release()
        raise
    return held


# --- --lock-wait: a run that waits for the locks it would be refused at -------------------------

LOCK_POLL_S = 5.0     # between two looks at a held lock


def lock_busy(names, mac_root=None, shared=()):
    """The Busy acquire_locks(NAMES, shared=SHARED) would raise now, or None (probed: nothing is
    held for long)."""
    for n in names:
        lk = FamilyLock(n, mac_root)
        if _probe(lk.path, shared=n in shared):
            return Busy(lk.holder(), n)
    return None


def still_busy(busy, mac_root=None, shared=()):
    """BUSY's lock as it is now: a Busy while another run holds it (its cell's, when it names
    one: Busy.what), else None."""
    if busy.what:
        path = CellLocks(busy.name, mac_root).path(busy.what)
        return Busy(read_json(path + ".json"), busy.name, busy.what) if _probe(path) else None
    return lock_busy([busy.name], mac_root, shared)


def taken_cell(plan, running, family, mac_root=None):
    """A Busy for the first cell of PLAN (a CELL_LOCKED family's run's own, made with running_skip)
    that isn't due because another live run holds it or the +img pins; None when there is none."""
    for pc in plan.cells:
        if pc.status == "not-runnable" and getattr(pc, "running", None) \
                and any(str(r).startswith("running in ") for r in pc.reasons):
            unit = pc.cell if pc.cell in (running or {}) else IMG_UNIT
            holder = read_json(CellLocks(family, mac_root).path(unit) + ".json")
            return Busy(holder or {"run": pc.running}, family, unit)
    return None


def fmt_wait(s):
    """5400 -> "1 h 30 min", 90 -> "2 min", 20 -> "20 s"."""
    s = max(0, int(round(s)))
    if s < 60:
        return f"{s} s"
    m = (s + 59) // 60
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60:02d} min"


def wait_unlocked(look, deadline, out=print, poll=None, clock=time.time, sleep=time.sleep, said=None):
    """Wait until LOOK() (a Busy, or None) finds nothing held, or DEADLINE (CLOCK's): None once
    free, else the last Busy.  Each holder gets one line (SAID: the one it has had)."""
    while True:
        b = look()
        if b is None:
            return None
        left = deadline - clock()
        if left <= 0:
            return b
        if str(b) != said:
            said = str(b)
            out(f"t2: {b}: waiting for it to end (--lock-wait, {fmt_wait(left)} left)")
        sleep(min(LOCK_POLL_S if poll is None else poll, left))


def with_lock_wait(word, wait, attempt, mac_root=None, out=print, poll=None, clock=time.time, sleep=time.sleep):
    """`t2 WORD --lock-wait T` and `t2 resume RUN --lock-wait T`: ATTEMPT(raises) -> the exit code,
    made once the locks WORD takes are free (R21 refuses; this waits, up to WAIT seconds, for a
    fixer's run of the family or the cycle's).  ATTEMPT(True) makes its ctx (pull and facts: fresh
    after a wait) and runs lifecycle or resume with busy_raises, so a lock lost in the meantime (to
    another waiter), or a holt cell another run holds, comes back here as a Busy and is waited
    for in its turn.  WAIT 0, or the wait run out: R21's refusal in its own words (exit 2)."""
    if not wait or wait <= 0:
        return attempt(False)
    t0 = clock()
    deadline = t0 + wait
    names = family_locks(word)
    shared = (word.family,) if cell_locked(word) else ()
    step = LOCK_POLL_S if poll is None else poll
    b = wait_unlocked(lambda: lock_busy(names, mac_root, shared), deadline, out, poll, clock, sleep)
    while b is None:
        try:
            return attempt(True)
        except Busy as e:
            b = e
        left = deadline - clock()
        if left <= 0:
            break
        out(f"t2: {b}: waiting for it to end (--lock-wait, {fmt_wait(left)} left)")
        sleep(min(step, left))     # (at least one step: a look that finds it free at once mustn't spin)
        b = wait_unlocked(lambda e=b: still_busy(e, mac_root, shared) or lock_busy(names, mac_root, shared),
                          deadline, out, poll, clock, sleep, said=str(b))
    out(f"t2: {b} (--lock-wait {fmt_wait(wait)} ran out)")
    return EXIT_REFUSED


def held_runs(mac_root=None):
    """The run ids holding a family lock in MAC_ROOT (any family, shared or not, and a
    CELL_LOCKED family's cells; "pending" while one plans)."""
    out = set()
    for fam in RUN_PREFIX:
        lk = FamilyLock(fam, mac_root)
        if lk.held():
            out |= {h.get("run") or "?" for h in lk.holders()} or {"?"}
    for fam in CELL_LOCKED:
        out |= set(CellLocks.held_by(fam, mac_root).values())
    return out


def unfinished_runs(family=None, mac_root=None, exclude=(), now=None, max_age=UNFINISHED_S):
    """[run] (of FAMILY, or any) that began host work (a cells-HOST.json), have no report.txt
    and changed within MAX_AGE: the lock went with the Mac process (Ctrl-C, a crash), but
    detached checks and queue runners on the hosts may still run (R21)."""
    # (A guest or img run writes no cells-HOST.json before its pool starts: its runner's record
    # counts the same, guest.json or img.json.)
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
            if "report.txt" in names or not any((n.startswith("cells-") and n.endswith(".json")) or
                                                n in ("guest.json", "img.json", "bko.json") for n in names):
                continue
            newest = max(os.path.getmtime(os.path.join(rd, n)) for n in names)
        except (OSError, ValueError):
            continue
        if now - newest <= max_age:
            out.append(run)
    return out


def composite_of(run, mac_root=None):
    """The unfinished BkoTest2 or SweepTest2 run (t2b-S, t2w-S: a bko.json, no report.txt) whose
    child RUN is, by its stamp, or None.  Its children go on under `t2 resume` of the composite."""
    m = re.match(r"t2[gshvi]-(\d{8}T\d{4}-[0-9a-f]{4})", run or "")
    if not m:
        return None
    for fam in ("bko", "sweep"):
        d = run_dir(new_run_id(fam, stamp=m.group(1)), mac_root)
        if os.path.exists(os.path.join(d, "bko.json")) and not os.path.exists(os.path.join(d, "report.txt")):
            return os.path.basename(d)
    return None


def resume_hint(run, mac_root=None):
    """`t2 resume RUN`, or the composite's when RUN is a child of an unfinished one."""
    return f"t2 resume {composite_of(run, mac_root) or run}"


def run_hosts(run, mac_root=None):
    """The hosts RUN used, in its order: its cells-HOST.json files, else its plan's facts."""
    rd = run_dir(run, mac_root)
    pj = read_json(os.path.join(rd, "plan.json"), {}) or {}
    order = [h for h in ((pj.get("header") or {}).get("facts") or {}) if h != "mac"]
    try:
        cells = sorted(m.group(1) for m in (re.match(r"cells-(?!r\d-)(.+)\.json\Z", n) for n in os.listdir(rd)) if m)
    except OSError:
        cells = []
    if not cells:
        return order
    return [h for h in order if h in cells] + [h for h in cells if h not in order]


def running_cells(family, mac_root=None, exclude=()):
    """{cell: run} of the live runs of FAMILY, less EXCLUDE's (R21: plan shows them as running
    RUN; a CELL_LOCKED family's run plans them not-runnable).  A CELL_LOCKED family's held units
    count, IMG_UNIT among them (the plan reads it for every +img cell), and the due cells of a run
    holding the family's lock exclusive."""
    lk = FamilyLock(family, mac_root)
    out = {}
    run = (read_json(lk.excl_info) or {}).get("run")
    if run and run not in exclude and lk.held() and not lk.holders()[1:]:   # (an exclusive holder is the only one)
        p = read_json(os.path.join(run_dir(run, mac_root), "plan.json"), {}) or {}
        out.update({c["cell"]: run for c in (p.get("cells") or []) if c.get("status") == "due"})
    if family in CELL_LOCKED:
        out.update(CellLocks.held_by(family, mac_root, exclude))
    return out


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


def _leftover_rm(path):
    """The shell that removes PATH on its host and says what became of it, on its last line:
    gone (it wasn't there), removed (it was, and isn't now) or kept (it still is)."""
    q = shlex.quote(path)
    there = f"{{ test -e {q} || test -L {q}; }}"
    return (f"if {there}; then rm -rf --one-file-system -- {q} 2>/dev/null || "
            f"sudo -n rm -rf --one-file-system -- {q}; if {there}; then echo kept; exit 1; fi; echo removed; "
            f"else echo gone; fi")


def forget_made(run, done, mac_root=None):
    """Drop DONE ({(host, path)}: removed, or found gone) from dead RUN's made.json, under
    record_made's lock; made.json itself goes once nothing is left in it.  So the next
    preflight doesn't sweep (and say it removed) the same paths again."""
    p = os.path.join(run_dir(run, mac_root), "made.json")
    if not done or not os.path.exists(p):
        return
    with _MADE_MU:
        fd = os.open(p + ".lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            made = read_json(p, {}) or {}
            left = {h: [it for it in (its or []) if (h, it.get("path")) not in done] for h, its in made.items()}
            left = {h: its for h, its in left.items() if its}
            if left:
                write_json(p, left)
            else:
                os.unlink(p)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    if not os.path.exists(p):
        try:
            os.unlink(p + ".lock")
        except OSError:
            pass


def remove_leftovers(items, log=print, dead=None, mac_root=None):
    """Remove a dead run's leftovers, run by run: when any PID its made.json recorded still
    runs, or can't be checked, every leftover of that run is kept and said so.  A path already
    gone is dropped quietly, a removed one said so; both leave the run's made.json
    (forget_made), so they aren't swept again.  [(host, path, removed or gone)]."""
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
        cleared = set()
        for _, host, path, pid in its:
            r = hostrun.ssh(host, _leftover_rm(path), timeout=120)
            said = (r.text.strip().splitlines() or [""])[-1]
            if said == "gone" and r.rc == 0:
                cleared.add((host, path))
            elif said == "removed" and r.rc == 0:
                cleared.add((host, path))
                log(f"leftover of {run} on {host}: {path} removed")
            else:
                why = r.errtext.strip()[-120:] or ("still there" if said == "kept" else f"rc {r.rc}")
                log(f"leftover of {run} on {host}: {path} NOT removed: {why}")
            done.append((host, path, (host, path) in cleared))
        forget_made(run, cleared, mac_root)
    return done


# --- the stop rules (1.9; R22) ------------------------------------------------------------------

def allowed_note(allowed, counted, kept=0):
    """The write stop rule's words for ALLOWED ({cell: bytes} that cells' write allowances
    excuse, holt.py's allowed_bytes) and KEPT (bytes a failed run's state wrote as it was
    moved out of RAM to keep it: iolog.sh's keep=) beside the true total: '' when nothing
    is excused."""
    ex = sum(v for v in (allowed or {}).values() if v)
    parts = []
    if kept and kept > 0:
        parts.append(f"{kept / 1e9:.2f} GB of it a failed run's kept state or a job's SSD scratch on /bkoscratch")
    if ex > 0:
        parts.append(f"{ex / 1e9:.2f} GB of it within the write allowance of {', '.join(sorted(allowed))}")
    if not parts:
        return ""
    return f" ({'; '.join(parts)}, not counted; {counted / 1e9:.2f} GB counted)"


class HostHolds:
    """The swap rule, per host (1.9, revised 2026-11-18).  A host whose swap-out over the
    last swap_window_min minutes (10) is over swap_out_gib (1 GiB) is held: nothing new
    starts there, its running jobs go on, and its queued cells go to the hosts that can take
    them (each runner's own placement rules).  It starts again once it has been held
    swap_hold_min minutes (15) and its swap-out over the window is at most swap_clear_gib
    (0.25 GiB): the hysteresis, so a host that swapped once and recovered resumes, without
    flapping.  A hold that lasts swap_hold_max_min minutes (120) is the old run-wide stop:
    feed() says why, once (a host that never recovers would leave cells only it can take
    waiting for ever).  Swap is a host's own state: the swap-out since the run began no
    longer counts, nor does the swap in use (pages stay there long after the pressure).

        feed(family, host, swap_out_bytes) -> the run-wide why, or None
                                 SWAP_OUT_BYTES: the family's cumulative swap-out on HOST
                                 (since any base of its own: only its growth counts)
        held() -> {host: why};   notes() -> [what happened, for the report]

    Not thread-safe: RunGuards calls it under its lock."""

    def __init__(self, stop=None, clock=None):
        st = stop or {}
        self.trip_b = float(st.get("swap_out_gib", 1)) * GIB
        self.window_s = float(st.get("swap_window_min", 10)) * 60
        self.clear_b = float(st.get("swap_clear_gib", 0.25)) * GIB
        self.min_s = float(st.get("swap_hold_min", 15)) * 60
        self.max_s = float(st.get("swap_hold_max_min", 120)) * 60
        self.clock = clock or time.time
        self.series = {}     # (family, host) -> [(t, bytes)]: a virtual (first t, 0), then the readings
        self.holds = {}      # host -> {"since": t, "why": text}
        self.escalated = set()
        self.said = []

    def _mins(self, s):
        return f"{s / 60:g}"

    def growth(self, host, now):
        """HOST's swap-out over the window: the most any family's readings grew by."""
        out = 0
        for (f, h), s in self.series.items():
            if h != host or not s:
                continue
            ref = s[0]
            for x in s:
                if x[0] <= now - self.window_s:
                    ref = x
            out = max(out, s[-1][1] - ref[1])
        return out

    def feed(self, family, host, swap_out_bytes):
        if swap_out_bytes is None:
            return None
        now = self.clock()
        s = self.series.setdefault((family, host), [(now, 0)])
        if swap_out_bytes < s[-1][1]:     # the counter went back (a reboot, a new base): start again
            s[:] = [(now, swap_out_bytes)]
        else:
            s.append((now, swap_out_bytes))
        while len(s) > 2 and s[1][0] <= now - self.window_s:    # keep one reading older than the window
            s.pop(0)
        g = self.growth(host, now)
        win = self._mins(self.window_s)
        h = self.holds.get(host)
        if h is None:
            if g > self.trip_b:
                why = f"{host}: {g / GIB:.2f} GiB swapped out in the last {win} min"
                self.holds[host] = {"since": now, "why": why}
                self.said.append(f"held {why}: nothing new started there, its cells went to the other hosts "
                                 f"({utc_now()})")
            return None
        held_s = now - h["since"]
        if held_s >= self.min_s and g <= self.clear_b:
            del self.holds[host]
            self.said.append(f"{host}: started again after {held_s / 60:.0f} min held "
                             f"({g / GIB:.2f} GiB swapped out in the last {win} min; {utc_now()})")
            return None
        if held_s >= self.max_s and host not in self.escalated:
            self.escalated.add(host)
            return (f"{host}: held {held_s / 60:.0f} min by the swap rule and swap-out hasn't eased "
                    f"({g / GIB:.2f} GiB in the last {win} min)")
        return None

    def held(self):
        return {h: d["why"] for h, d in self.holds.items()}

    def notes(self):
        return list(self.said)


def _epoch(t):
    """T (epoch seconds or a UTC ISO time) as epoch seconds, or None."""
    if isinstance(t, (int, float)) and not isinstance(t, bool):
        return float(t)
    from . import parse_utc
    return parse_utc(t)


class HostWatch:
    """The host rule, per host (2026-11-19: SweepTest2 #4's chopin, powered off for maintenance
    at 15:30Z, came back as two HARNESS and four LOST results, and the run-wide harness count
    stopped stic, guest and holt on the four hosts that were fine; GateTest2All never ran).
    A host that stops answering (every ssh of a look fails) for host_away_s seconds (60), or
    that answers with another boot id than before (it rebooted), is held: nothing new starts
    there, and its queued cells go to the hosts that can take them, as the swap rule's hold
    does (the same held()).  The hold lifts once the host has answered for host_settle_min
    minutes (5) in a row with no host_away_s without an answer between (a look that fails
    alone doesn't start the settle again), and has been up that long.  A hold
    that lasts host_hold_max_min minutes (120) is run-wide, once (feed() says why), as the
    swap rule's is.

    lost(host, start, end) says whether a harness-class result of a job that ran on HOST from
    START to END is the host's going away rather than the harness's: the host booted after the
    job began (its uptime: so a host that came back before the Mac ever saw it gone still
    counts) and before END + host_settle_min; or the job ended while the host is held now, at
    or after the start of the hold's away window (its last answer before); or it ended inside
    an earlier away window (its last answer before a hold to its first answer after) or
    host_settle_min after it.  A window with no reboot in it doesn't show that a job that ran on
    past it was lost (2026-11-19 review: a 60 s blip under a 3 h holt cell made its real HARNESS
    host-lost), nor does a hold that began after the job ended (an old row judged again in an
    r2 round while its host is held).  Such a result is host-lost: the runners requeue it once,
    and it never counts toward the run-wide harness stop (StopEngine.feed's res["host_lost"]).

        feed(host, seen) -> the run-wide why, or None
                                SEEN: None when HOST didn't answer, else {"boot": its boot
                                id or None, "up_s": its uptime in seconds or None}
        held() -> {host: why};  lost(host, start=None, end=None) -> why or None
        rebooted(host, since) -> True when HOST booted after SINCE;  notes() -> [text]

    Not thread-safe: RunGuards calls it under its lock."""

    SKEW_S = 30   # a boot this close to a job's start isn't one under it (the clocks' drift)

    def __init__(self, stop=None, clock=None):
        st = stop or {}
        self.away_s = float(st.get("host_away_s", 60))
        self.settle_s = float(st.get("host_settle_min", 5)) * 60
        self.max_s = float(st.get("host_hold_max_min", 120)) * 60
        self.confirm_s = float(st.get("host_confirm_s", 120))
        self.confirm_max_s = float(st.get("host_confirm_max_min", 15)) * 60
        self.clock = clock or time.time
        self.st = {}         # host -> {"boot", "boot_t", "ok_t", "fail_t", "ok_since"}
        self.holds = {}      # host -> {"since": t, "why": text}
        self.away = {}       # host -> [[t0, t1 or None]]: the windows it was away, its last answer before to its first after
        self.boots = {}      # host -> [boot_t]: the boots seen after the first look (its reboots)
        self.escalated = set()
        self.said = []

    def _hold(self, host, now, why, t0):
        self.holds[host] = {"since": now, "why": why}
        self.away.setdefault(host, []).append([t0, None])
        self.said.append(f"held {why}: nothing new started there, its cells went to the other hosts ({utc_now(now)})")

    def feed(self, host, seen):
        now = self.clock()
        s = self.st.setdefault(host, {})
        h = self.holds.get(host)
        if seen is None:
            s.setdefault("fail_t", now)
            # (a look that fails alone, under host_away_s, is no going away, held or not: a held
            # host's settle goes on through it, or one ssh timing out under load every few
            # minutes kept a host that was back held until host_hold_max_min made it run-wide)
            if now - s["fail_t"] >= self.away_s:
                s.pop("ok_since", None)
                if h is None:
                    self._hold(host, now, f"{host}: no answer since {utc_now(s['fail_t'])}", s.get("ok_t") or s["fail_t"])
                    return None
                w = self.away.setdefault(host, [])
                if not w or w[-1][1] is not None:   # away again while held: a window of its own (lost())
                    w.append([s.get("ok_t") or s["fail_t"], None])
        else:
            boot = seen.get("boot") if isinstance(seen.get("boot"), str) and seen.get("boot") else None
            up = seen.get("up_s")
            boot_t = now - float(up) if isinstance(up, (int, float)) and not isinstance(up, bool) else None
            was_boot, was_t = s.get("boot"), s.get("boot_t")
            rebooted = bool(boot and was_boot and boot != was_boot) or bool(
                not (boot and was_boot) and boot_t is not None and was_t is not None and boot_t > was_t + 120)
            if rebooted:
                bt = boot_t if boot_t is not None else now
                self.boots.setdefault(host, []).append(bt)
                why = f"{host}: rebooted at {utc_now(bt)} (its boot id changed)"
                if h is None:
                    self._hold(host, now, why, s.get("ok_t") or bt)
                    h = self.holds[host]
                else:
                    h["why"] = why
                s.pop("ok_since", None)
            s.pop("fail_t", None)
            s.update(ok_t=now, boot=boot or (was_boot if not rebooted else None), boot_t=boot_t)
            s.setdefault("ok_since", now)
            if h is not None:
                w = self.away.get(host)
                if w and w[-1][1] is None:   # back: the window it was away ends at its first answer
                    w[-1][1] = now
                up_s = now - boot_t if boot_t is not None else None
                if now - s["ok_since"] >= self.settle_s and (up_s is None or up_s >= self.settle_s):
                    del self.holds[host]
                    self.said.append(f"{host}: started again after {(now - h['since']) / 60:.0f} min held "
                                     f"(answering for {(now - s['ok_since']) / 60:.0f} min; {utc_now(now)})")
                    self.escalated.discard(host)
                    return None
        h = self.holds.get(host)
        if h is not None and now - h["since"] >= self.max_s and host not in self.escalated:
            self.escalated.add(host)
            return f"{host}: held {(now - h['since']) / 60:.0f} min by the host rule ({h['why']})"
        return None

    def held(self):
        return {h: d["why"] for h, d in self.holds.items()}

    def rebooted(self, host, since):
        since = _epoch(since)
        if since is None:
            return False
        bt = (self.st.get(host) or {}).get("boot_t")
        return any(b > since + self.SKEW_S for b in (self.boots.get(host) or []) + ([bt] if bt is not None else []))

    def lost(self, host, start=None, end=None):
        now = self.clock()
        start, end = _epoch(start), _epoch(end)
        end = now if end is None else end
        start = end if start is None else start
        bt = (self.st.get(host) or {}).get("boot_t")
        for b in sorted(set((self.boots.get(host) or []) + ([bt] if bt is not None else []))):
            if start + self.SKEW_S < b <= end + self.settle_s:
                return f"{host} rebooted at {utc_now(b)}, after the job began ({utc_now(start)})"
        wins = [w for w in self.away.get(host) or () if w[0] is not None]
        h = self.holds.get(host)
        if h is not None and wins and end >= wins[-1][0] - self.SKEW_S:
            return h["why"]   # held now, and the job ended after the host went away
        for t0, t1 in wins:   # (no reboot under the job: only one that ended while it was away, or just after)
            if t0 - self.SKEW_S <= end <= (now if t1 is None else t1) + self.settle_s:
                return (f"{host} was away from {utc_now(t0)}" + (f" to {utc_now(t1)}" if t1 else "")
                        + " (the host rule held it)")
        return None

    def known(self, host):
        """True once HOST has been looked at (its results can wait for confirmed())."""
        return host in self.st

    def confirmed(self, host, end):
        """True when HOST, not held, has answered at a look host_confirm_s (120) or more after END
        and answers still: a result that ended at END wasn't its host going away (one written as
        the host shut down, its jobs getting their TERM, lands before the host stops answering)."""
        s = self.st.get(host) or {}
        end = _epoch(end)
        return (host not in self.holds and "fail_t" not in s and s.get("ok_t") is not None
                and (end is None or s["ok_t"] >= end + self.confirm_s))

    def notes(self):
        return list(self.said)


def merge_held(*maps):
    """{host: why} of several rules' holds, a host held by more than one with each why."""
    out = {}
    for m in maps:
        for h, w in (m or {}).items():
            out[h] = f"{out[h]}; {w}" if h in out else w
    return out


class RunGuards:
    """The stop rules that hold across a whole run or composite, thread-safe: the harness count,
    the write bytes per family and host, the swap rule's host holds (HostHolds: per host,
    shared by the composite's families, since swap is the host's) and the host rule's
    (HostWatch: a host that stops answering or reboots; its results are host-lost and never
    counted toward the harness count).  One per composite
    (Shared.guards); a plain run has its own.  StopEngine feeds it; each runner asks
    tripped() once per poll and starts nothing more, and held() for the hosts it starts
    nothing new on.

        add_harness(cell) -> count
        feed_host(family, host, swap_out_bytes=None, ours_bytes=None, allowed=None, kept=None)
                                                -> why it tripped now, or None
        trip(why); tripped() -> bool; why() -> [text]
        held() -> {host: why} (both rules'); hold_notes() -> [text] (the swap rule's)
        feed_seen(host, seen) -> why it tripped now, or None   (HostWatch.feed)
        host_held() -> {host: why} (the host rule's alone)
        host_lost(host, start, end) -> why or None; rebooted(host, since) -> bool
        add_host_lost(text); host_notes() -> [text] (the host rule's, and its results)
        to_json(), from_json(doc, policy...)    what a composite's resume rebuilds

    ALLOWED ({cell: bytes}, holt's disk-heavy cells: holt/spec.json write_allow) is the part
    of OURS_BYTES that those cells' write allowances excuse, and KEPT (bytes) the part a failed
    run's state wrote as it moved out of RAM to /bkoscratch for its diagnostic rerun (the
    design's own evidence-keeping; the jobs.log lines' keep=, wear.Job.kept_bytes): the write
    rules count the host's total less both; the words, the wear line and the budget line still
    give the true total.
    """

    def __init__(self, policy=None, sweep=False, update=False, now=None):
        p = policy or {}
        st, b = p.get("stop") or {}, p.get("budget") or {}
        self.harness_max = harness_stop(st)[0]      # (T2_HARNESS_STOP over policy.stop.harness_results)
        self.holds = HostHolds(st)
        self.watch = HostWatch(st)
        self.lost_said = []     # the host-lost results, for the report (add_host_lost)
        self.write_x = float(st.get("write_budget_x", 2))
        self.run_gb = budget_gb(b, sweep, update)
        self.bind = report.budget_binds(b.get("bind_from"), now)
        self.harness = 0
        self.bytes = {}         # {(family, host): the family's bytes on that host so far}
        self.allowed = {}       # {(family, host): {cell: bytes of those a write allowance excuses}}
        self.kept = {}          # {(family, host): bytes of those a failed run's kept state wrote}
        self.reasons = []
        self._mu = threading.Lock()

    def add_harness(self, cell=None):
        with self._mu:
            self.harness += 1
            if self.harness >= self.harness_max and not self.reasons:
                self.reasons.append(f"{self.harness} harness-class results (last {cell})")
            return self.harness

    def trip(self, why):
        with self._mu:
            if why not in self.reasons:
                self.reasons.append(why)

    def tripped(self):
        with self._mu:
            return bool(self.reasons)

    def why(self):
        with self._mu:
            return list(self.reasons)

    def host_bytes(self, host):
        with self._mu:
            return sum(v for (f, h), v in self.bytes.items() if h == host)

    def held(self):
        """{host: why}: the hosts the swap rule or the host rule holds now (nothing new starts there)."""
        with self._mu:
            return merge_held(self.holds.held(), self.watch.held())

    def hold_notes(self):
        with self._mu:
            return self.holds.notes()

    def host_held(self):
        """{host: why}: the host rule's holds alone (a rerun waits for its host to be back: wait_back)."""
        with self._mu:
            return self.watch.held()

    def feed_seen(self, host, seen):
        """HOST's look (HostWatch.feed's SEEN): why the host rule tripped the guards now, else None."""
        with self._mu:
            why = self.watch.feed(host, seen)
            if why and not self.reasons:
                self.reasons.append(why)
                return why
            return None

    def host_lost(self, host, start=None, end=None):
        with self._mu:
            return self.watch.lost(host, start, end)

    def rebooted(self, host, since):
        with self._mu:
            return self.watch.rebooted(host, since)

    def host_known(self, host):
        with self._mu:
            return self.watch.known(host)

    def host_confirmed(self, host, end):
        with self._mu:
            return self.watch.confirmed(host, end)

    def add_host_lost(self, text):
        with self._mu:
            self.lost_said.append(text)

    def host_notes(self):
        with self._mu:
            return self.watch.notes() + list(self.lost_said)

    def feed_host(self, family, host, swap_out_bytes=None, ours_bytes=None, allowed=None, kept=None):
        """Why this feed tripped the guards (the first time), else None.  Swap-out holds or
        frees HOST alone (HostHolds); only a hold that lasts trips the guards."""
        with self._mu:
            why = self.holds.feed(family, host, swap_out_bytes)
            if ours_bytes is not None:
                self.bytes[(family, host)] = ours_bytes
                al = {c: v for c, v in (allowed or {}).items() if v and v > 0}
                if al:
                    self.allowed[(family, host)] = al
                else:
                    self.allowed.pop((family, host), None)
                kb = min(int(kept), int(ours_bytes)) if isinstance(kept, (int, float)) and kept > 0 else 0
                if kb:
                    self.kept[(family, host)] = kb
                else:
                    self.kept.pop((family, host), None)
                total = sum(v for (f, h), v in self.bytes.items() if h == host)
                excused = {}
                for (f, h), cells in self.allowed.items():
                    if h == host:
                        for c, v in cells.items():
                            excused[c] = excused.get(c, 0) + v
                kept_all = sum(v for (f, h), v in self.kept.items() if h == host)
                counted = max(0, total - sum(excused.values()) - kept_all)
                note = allowed_note(excused, counted, kept_all)
                if not why and counted > self.write_x * self.run_gb * 1e9:
                    why = (f"{host}: this run's jobs wrote {total / 1e9:.2f} GB{note}, "
                           f"over {self.write_x:g} x the {self.run_gb:g} GB budget")
                if not why and self.bind and counted > self.run_gb * 1e9:
                    why = (f"{host}: this run's jobs wrote {total / 1e9:.2f} GB{note}, "
                           f"over the {self.run_gb:g} GB budget (bound)")
            if why and not self.reasons:
                self.reasons.append(why)
                return why
            return None

    def to_json(self):
        with self._mu:
            return {"harness": self.harness, "reasons": list(self.reasons),
                    "bytes": [[f, h, v] for (f, h), v in sorted(self.bytes.items())],
                    "allowed": [[f, h, dict(v)] for (f, h), v in sorted(self.allowed.items())],
                    "kept": [[f, h, v] for (f, h), v in sorted(self.kept.items())],
                    # (the report's: a resumed composite still lists what the rules did before)
                    "notes": {"swap": self.holds.notes(), "host": self.watch.notes(), "lost": list(self.lost_said)}}

    @classmethod
    def from_json(cls, doc, policy=None, sweep=False, update=False, now=None):
        g = cls(policy, sweep, update, now)
        doc = doc or {}
        g.harness = int(doc.get("harness") or 0)
        g.reasons = list(doc.get("reasons") or [])
        g.bytes = {(f, h): v for f, h, v in doc.get("bytes") or []}
        g.allowed = {(f, h): dict(v) for f, h, v in doc.get("allowed") or [] if isinstance(v, dict) and v}
        g.kept = {(f, h): v for f, h, v in doc.get("kept") or [] if isinstance(v, (int, float)) and v > 0}
        n = doc.get("notes") if isinstance(doc.get("notes"), dict) else {}
        g.holds.said = [str(x) for x in n.get("swap") or []]
        g.watch.said = [str(x) for x in n.get("host") or []]
        g.lost_said = [str(x) for x in n.get("lost") or []]
        return g


class Shared:
    """What a composite gives each child's lifecycle() or resume(): its STAMP, the one private
    COPY ({host: {id, path, commit, dirty}}), the run-wide GUARDS, the child's PLAN if it
    made it already, a run-id SUFFIX (ImgTest2's panel: img) and its MAC_TREE (make_mac_tree's
    info; None: each child makes its own)."""

    def __init__(self, stamp, copy, guards, plan=None, suffix=None, mac_tree=None):
        self.stamp, self.copy, self.guards, self.plan, self.suffix = stamp, copy, guards, plan, suffix
        self.mac_tree = mac_tree


def budget_gb(b, sweep=False, update=False):
    if update:
        return float(b.get("update_gb", 40))
    return float(b.get("sweep_gb" if sweep else "run_gb", 2))


class StopEngine:
    """Runners feed it each cell's result as it lands:

        feed({cell, family, part ("fast"/"slow" for gate layers, else None), verdict, class,
              needs {need: v}, os, host, flake, net, host_lost})
                                                               net: a harness result stalled on the
                                                               network, the family's own stop at
                                                               harness_max, never the run-wide count;
                                                               host_lost: why a harness-class result
                                                               is its host's going away (HostWatch.lost,
                                                               hostjob.host_lost): counted by no stop
                                                               rule (the runner requeues it once);
                                                               lost_again: why it was lost on a second
                                                               host after that requeue: it counts
        feed_seen(host, seen)                                  a look at HOST (HostWatch.feed): the host
                                                               rule's holds; a hold that lasts is stop-starts
        feed_host(host, swap_out_bytes=..., ours_bytes=..., allowed=..., kept=...)
                                                               the host's swap-out (cumulative, any
                                                               base: HostHolds judges its growth); this
                                                               run's bytes from its wear tags (R22);
                                                               ALLOWED {cell: bytes} the part of them
                                                               cells' write allowances excuse (holt);
                                                               KEPT the part failed runs' kept state
                                                               wrote (keep=; RunGuards)

    and gets back ok, stop-family, stop-os OS, stop-starts or stop-run.  Its state:
    stop_family (no guest of the family starts), stopped_os, stop_starts, stop_run, stops [text];
    held() {host: why}, the hosts the swap rule or the host rule holds: the runner starts nothing
    new there and sends their queued cells to the other hosts that can take them (a hold is no
    stop: it isn't in stops; hold_notes() and host_notes() give the report what happened).
    host_lost(host, start, end) and rebooted(host, since) ask the host rule about a result.
    A harness-class result with no host_lost, of a host the host rule has looked at, waits
    (pending) until the host is seen answering host_confirm_s after the result's end (res
    "end", else when it was fed): then it counts as before; if the host goes away first it is
    host-lost (a job's row written as its host shut down lands before the host stops
    answering).  At most host_confirm_max_min; settle(final=True) (run.finish) counts the rest.
    The stops a later settle() makes come back from the next feed(), feed_seen() or feed_host().
    With GUARDS (a RunGuards), the harness count, swap holds and write bytes are the run's, shared
    with the composite's other families, and a guard another family tripped stops this one."""

    def __init__(self, family, policy=None, sweep=False, update=False, now=None, guards=None):
        p = (policy or {})
        st = p.get("stop") or {}
        b = p.get("budget") or {}
        self.family = family
        self.os_need = int(st.get("os_need_guests", 2))
        self.harness_max = harness_stop(st)[0]      # (T2_HARNESS_STOP over policy.stop.harness_results)
        self.holds = HostHolds(st)      # (with GUARDS, theirs is the one fed)
        self.watch = HostWatch(st)      # (the same)
        self.lost_said = []
        self.pending = []               # [(res, end, fed)]: harness-class results waiting for their host's confirm
        self._pmu = threading.RLock()   # (pending: ImgTest2's worker threads feed while its main thread looks)
        self.write_x = float(st.get("write_budget_x", 2))
        self.run_gb = budget_gb(b, sweep, update)
        self.bind = report.budget_binds(b.get("bind_from"), now)
        self.guards = guards
        self.harness = 0
        self.net = 0            # harness-class results stalled on the network (feed's res["net"]): this family's
        self.need_fail = {}
        self.stop_family = self.stop_starts = self.stop_run = False
        self.stopped_os = set()
        self.stops = []

    def _stop(self, what, why):
        self.stops.append(f"{what}: {why}")
        return what

    def _host_rule(self, name):
        g = self.guards if self.guards is not None and hasattr(self.guards, "feed_seen") else None
        if g is not None:
            return getattr(g, {"known": "host_known", "confirmed": "host_confirmed"}.get(name, name))
        return getattr(self.watch, name)

    def _now(self):
        w = getattr(self.guards, "watch", None) if self.guards is not None else None
        return (w or self.watch).clock()

    @staticmethod
    def _worse(a, b):
        return b if STOP_ORDER.index(b.split()[0]) > STOP_ORDER.index(a.split()[0]) else a

    def _count_harness(self, res):
        self.harness += 1
        count = self.guards.add_harness(res.get("cell")) if self.guards is not None else self.harness
        if count >= self.harness_max and not self.stop_run:
            self.stop_run = True
            return self._stop("stop-run", f"{count} harness-class results (last {res.get('cell')})")
        return "ok"

    def settle(self, final=False):
        """The pending harness-class results (feed): each one host-lost now (its host went away
        after it), counted (its host confirmed, waited host_confirm_max_min, or FINAL), or left.
        The worst stop it made, or "ok"."""
        with self._pmu:
            return self._settle(final)

    def _settle(self, final):
        out, left, now = "ok", [], self._now()
        for res, end, fed in self.pending:
            host = res.get("host")
            e = _epoch(end)   # (gone within host_confirm_s after it ended: its host's going away too)
            why = self.host_lost(host, res.get("start"), None if e is None else e + self.watch.confirm_s)
            if why:
                self.add_host_lost(f"{res.get('cell')} {res.get('verdict')} on {host}: host lost ({why}, after it "
                                   "ended): no stop rule counts it")
                continue
            if final or self._host_rule("confirmed")(host, end) or now - fed > self.watch.confirm_max_s:
                out = self._worse(out, self._count_harness(res))
                continue
            left.append((res, end, fed))
        self.pending = left
        return out

    def feed(self, res):
        with self._pmu:
            return self._feed(res)

    def _feed(self, res):
        verdict, klass = res.get("verdict"), res.get("class")
        out = self.settle() if self.pending else "ok"
        if res.get("part") == "fast" and verdict in FAILING + ("HARNESS", "LOST"):
            if not self.stop_family:
                self.stop_family = True
                out = self._worse(out, self._stop("stop-family", f"fast gate {res.get('cell')} {verdict}"))
        elif res.get("part") == "slow" and verdict in FAILING + ("HARNESS", "LOST"):
            if not self.stop_starts:
                self.stop_starts = True
                out = self._worse(out, self._stop("stop-starts", f"slow gate {res.get('cell')} {verdict}"))
        if klass in ("harness", "lost") and res.get("lost_again") and verdict not in FAILING:
            # its host went away under it again, on another host than the first time (the runner
            # requeues once): two hosts lost under one cell is the cell's as much as theirs, so it
            # counts once, as a harness result does (GuestTest2: Runner.requeue_lost)
            self.add_host_lost(f"{res.get('cell')} {verdict} on {res.get('host') or '?'}: host lost again "
                               f"({res.get('lost_again')}): counted as a harness result")
            return self._worse(out, self._count_harness(res))
        if klass in ("harness", "lost") and res.get("host_lost") and verdict not in FAILING:
            # its host went away under it (no answer, a reboot: HostWatch): not the harness's, so no
            # count at all; the runner requeues it once (SweepTest2 #4: chopin's maintenance
            # power-off made two HARNESS and four LOST results, and the run-wide count stopped
            # stic, guest and holt on four hosts that were fine)
            self.add_host_lost(f"{res.get('cell')} {verdict} on {res.get('host') or '?'}: host lost "
                               f"({res.get('host_lost')}): no stop rule counts it")
            return out
        if klass in ("harness", "lost") and not res.get("flake") and res.get("net"):
            # stopped while it waited on the network (GuestTest2's rows_guest.net_stall): the
            # internet's, not the harness's, so the family's own stop at harness_max of them; the
            # run-wide count would stop the composite's offline families too (t2s-20261119T0157-c8c7
            # stopped on guest:rocky10-lvm's and guest:elementary8-luks's 90 min stalled downloads)
            self.net += 1
            if self.net >= self.harness_max and not self.stop_family:
                self.stop_family = True
                o = self._stop("stop-family", f"{self.net} results stalled on the network (last {res.get('cell')}): "
                                              "nothing more of this family starts")
                out = o if STOP_ORDER.index("stop-family") > STOP_ORDER.index(out.split()[0]) else out
        elif klass in ("harness", "lost") and not res.get("flake"):
            host = res.get("host")
            if host and self.watch.confirm_s > 0 and self._host_rule("known")(host):
                # (its host may be going away: wait for it to be seen answering after the result's end)
                self.pending.append((res, res.get("end") or self._now(), self._now()))
                return out
            o = self._count_harness(res)
            if o != "ok":
                return o
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

    def feed_seen(self, host, seen):
        """A look at HOST (HostWatch.feed's SEEN: None for no answer): "ok", or stop-starts once a
        host rule's hold has lasted host_hold_max_min."""
        g = self.guards if self.guards is not None and hasattr(self.guards, "feed_seen") else None
        why = g.feed_seen(host, seen) if g is not None else self.watch.feed(host, seen)
        out = self.settle() if self.pending else "ok"
        trip = bool(why) or (self.guards is not None and self.guards.tripped())
        if trip and not self.stop_starts:
            self.stop_starts = True
            return self._worse(out, self._stop("stop-starts", why or "; ".join(self.guards.why())))
        return out

    def host_lost(self, host, start=None, end=None):
        """Why a harness-class result of a job on HOST from START to END is its host's going away, or None."""
        g = self.guards if self.guards is not None and hasattr(self.guards, "host_lost") else None
        return g.host_lost(host, start, end) if g is not None else self.watch.lost(host, start, end)

    def rebooted(self, host, since):
        g = self.guards if self.guards is not None and hasattr(self.guards, "rebooted") else None
        return g.rebooted(host, since) if g is not None else self.watch.rebooted(host, since)

    def host_held(self):
        """{host: why}: the host rule's holds alone (not the swap rule's): what wait_back waits on."""
        g = self.guards if self.guards is not None and hasattr(self.guards, "host_held") else None
        return g.host_held() if g is not None else self.watch.held()

    def host_settle_s(self):
        """host_settle_min in seconds: how long a host back from going away answers before a rerun there."""
        return self.watch.settle_s

    def host_known(self, host):
        """True once the host rule has looked at HOST (a result there can wait for host_confirmed)."""
        return bool(self._host_rule("known")(host))

    def host_confirmed(self, host, end):
        """True once HOST has been seen answering host_confirm_s after END (HostWatch.confirmed)."""
        return bool(self._host_rule("confirmed")(host, end))

    def host_confirm(self):
        """(host_confirm_s, host_confirm_max_min in seconds): how long a result waits for its host."""
        return self.watch.confirm_s, self.watch.confirm_max_s

    def add_host_lost(self, text):
        g = self.guards if self.guards is not None and hasattr(self.guards, "add_host_lost") else None
        if g is not None:
            g.add_host_lost(text)
        else:
            self.lost_said.append(text)

    def host_notes(self):
        g = self.guards if self.guards is not None and hasattr(self.guards, "host_notes") else None
        return g.host_notes() if g is not None else self.watch.notes() + list(self.lost_said)

    def feed_host(self, host, swap_out_bytes=None, ours_bytes=None, allowed=None, kept=None):
        if self.pending:
            o = self.settle()
            if o != "ok":
                return o
        if self.guards is not None:
            # (only what is given: a guards object of an older signature takes the rest)
            kw = {"kept": kept} if kept else {}
            if allowed or kw:
                why = self.guards.feed_host(self.family, host, swap_out_bytes, ours_bytes, allowed, **kw)
            else:
                why = self.guards.feed_host(self.family, host, swap_out_bytes, ours_bytes)
            if self.guards.tripped() and not self.stop_starts:
                self.stop_starts = True
                return self._stop("stop-starts", why or "; ".join(self.guards.why()))
            return "ok"
        why = self.holds.feed(self.family, host, swap_out_bytes)
        if why and not self.stop_starts:
            self.stop_starts = True
            return self._stop("stop-starts", why)
        if ours_bytes is not None:
            limit = self.write_x * self.run_gb * 1e9
            al = {c: v for c, v in (allowed or {}).items() if v and v > 0}
            kb = min(int(kept), int(ours_bytes)) if isinstance(kept, (int, float)) and kept > 0 else 0
            counted = max(0, ours_bytes - sum(al.values()) - kb)
            note = allowed_note(al, counted, kb)
            if counted > limit and not self.stop_starts:
                self.stop_starts = True
                return self._stop("stop-starts", f"{host}: this run's jobs wrote {ours_bytes / 1e9:.2f} GB{note}, "
                                                 f"over {self.write_x:g} x the {self.run_gb:g} GB budget")
            if self.bind and counted > self.run_gb * 1e9 and not self.stop_starts:
                self.stop_starts = True
                return self._stop("stop-starts", f"{host}: this run's jobs wrote {ours_bytes / 1e9:.2f} GB{note}, over the "
                                                 f"{self.run_gb:g} GB budget (bound)")
        return "ok"

    def may_start(self, cell_os=None):
        if self.pending:
            self.settle()
        return not (self.stop_run or self.stop_starts or self.stop_family or (cell_os and cell_os in self.stopped_os)
                    or (self.guards is not None and self.guards.tripped()))

    def held(self):
        """{host: why}: the hosts the swap rule or the host rule holds now; a runner starts nothing new there."""
        if self.guards is not None and hasattr(self.guards, "held"):
            g = self.guards.held()
            return g if hasattr(self.guards, "feed_seen") else merge_held(g, self.watch.held())
        return merge_held(self.holds.held(), self.watch.held())

    def hold_notes(self):
        if self.guards is not None and hasattr(self.guards, "hold_notes"):
            return self.guards.hold_notes()
        return self.holds.notes()


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

def prev_needs_of(pc, host, rows):
    """The needs a cell's newest valid PASS run row passed, while the row's own check and judge
    are the cell's now; else None (a resumed plan.json has no pc.res, so it says None too).
    Not its LIMITs: some checks print one only when the guest shows the condition, and a
    limit that stopped applying has lost nothing."""
    now = getattr(getattr(pc, "res", None) or {}, "get", lambda _h: None)(host)
    now = getattr(now, "groups", None) or {}
    found = [r for r in rows or () if r.get("cell") == pc.cell and r.get("kind") == "run"
             and r.get("verdict") == "PASS" and r.get("valid") is True]
    best = max(found, key=lambda r: (r.get("t") or "", r.get("id", "")), default=None)
    g = (best or {}).get("groups") or {}
    if not (best and now and all(g.get(x) == now.get(x) for x in ("harness", "judge"))):
        return None
    return sorted(n for n, v in best["needs"].items() if v != "LIMIT")


class RunState:
    """What a runner gets as ctx.run.

        run, word, family, mac_dir (MAC_ROOT/runs/RUN), host_root, hosts
        copy           {host: {id, path, commit, dirty}} once lifecycle step 5 made it
        stops          the StopEngine
        flakes         flakes.json's entries; limits: limits.json
        write_cells(plan, attempt=1, name="cells.json", reruns=None)   R1's step 6, on every host
        replan(facts, ledger_rows)   R1's step 5a: (plan, added, dropped) after a build
        cell_locks     a holt run's own CellLocks (the re-plan holds what it adds), else None
        host_run_dir(host)   HOST_ROOT/runs/RUN
        record_made(host, path, pid)   for preflight's leftovers
        log(text)      to MAC_ROOT/runs/RUN/run.log and stdout
    """

    def __init__(self, run, word, plan, ctx, mac_root=None, host_root=None, log=print, shared=None):
        self.run, self.word, self.family = run, word, word.family
        self.plan = plan
        self.ctx = ctx
        self.mac_root = mac_root or default_mac_root()
        self.mac_dir = run_dir(run, self.mac_root)
        self.host_root = host_root or default_host_root()
        self.hosts = [h for h in ctx.hosts if h in ctx.facts]
        self.copy = {}
        self.mac_tree, self.mac_tree_owned = None, False   # make_mac_tree's
        self.shared = shared
        self.cell_locks = None   # a CELL_LOCKED family's own run: its CellLocks (lifecycle, resume set it)
        pol = ctx.classes.get("policy")
        # SweepTest2's 5 GB budget is its own: the All forms keep the per-run 2 GB; an update round has its own too
        sweep, update = getattr(word, "sweep", False), getattr(word, "update", False)
        now = getattr(ctx, "now", None)
        self.guards = shared.guards if shared is not None and shared.guards is not None else \
            RunGuards(pol, sweep=sweep, update=update, now=now)
        self.stops = StopEngine(word.family, pol, sweep=sweep, update=update, now=now, guards=self.guards)
        d = word.family if word.family in ("guest", "img") else "stic"
        self.flakes = read_json(os.path.join(ctx.checkout, "testvm", "test2", d, "flakes.json"), []) or []
        if isinstance(self.flakes, dict):
            self.flakes = self.flakes.get("flakes") or []
        self.limits = read_json(os.path.join(ctx.checkout, "testvm", "test2", d, "limits.json"), {}) or {}
        self._log = log
        os.makedirs(self.mac_dir, exist_ok=True)
        notify.begun(run, word.word)
        hs = harness_stop_line((pol or {}).get("stop"))
        if hs:
            self.log(hs)    # (T2_HARNESS_STOP, t2 --harness-stop: the run.log says what this run stopped at)

    def log(self, text):
        line = f"{utc_now()} {text}"
        with open(os.path.join(self.mac_dir, "run.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if self._log:
            self._log(text)

    def host_run_dir(self, host):
        return os.path.join(hostrun.host_path(self.host_root), "runs", self.run)

    def attempt_run(self, attempt):
        """RUN for a job's first real start, RUN-rA after it."""
        return self.run if int(attempt or 1) <= 1 else f"{self.run}-r{int(attempt)}"

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
                   "planned_key": None if getattr(self.word, "update", False) else pc.keys.get(host),   # a merge changes it
                   "attempt": int((rr or {}).get("attempt") or attempt),
                   "rerun_of": (rr or {}).get("rerun_of"), "flake": (rr or {}).get("flake"),
                   "limits_classed": limits_classed(pc.cell, self.limits, rows) if pc.family == "stic" else []}
            if pc.family == "stic":
                ent["prev_needs"] = prev_needs_of(pc, host, rows)
            if rr and rr.get("rule"):
                ent["rule"] = rr["rule"]
            if rr and rr.get("rerun_why"):   # (HoltTest2's rescuer-made: not after a FAIL)
                ent["rerun_why"] = rr["rerun_why"]
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
        cl = getattr(self, "cell_locks", None)
        if cl is not None:
            # (cells another run holds now stay out; the ones this run adds it holds first, or
            # the re-plan fails (Busy) and the plan stays as it was: the runner says so)
            ctx.options["running"] = running_cells(self.family, self.mac_root, exclude=(self.run,))
            ctx.options["running_skip"] = True
        new = planmod.make(self.word, ctx)
        if cl is not None:
            cl.hold([c.cell for c in new.due()], self.run)
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
        """cells.json (attempt 1) or cells-r2.json (reruns: {cell: {rerun_of, flake, rule, rerun_why}}) to
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


def _of_run(row_run, run):
    """True when a row's run id is RUN or one of its attempts (RUN-rA)."""
    return row_run == run or bool(row_run and re.fullmatch(re.escape(run) + r"-r\d", row_run))


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
    ran |= {r.get("cell") for r in rows if r.get("kind") == "run" and _of_run(r.get("run"), run)}
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
                soft = (P.get("inputs") or {}).get("soft") or {}
                if getattr(pc, "fp_carry", False):    # the guest soft inputs are the image as it is
                    soft = dict({n: x for n, x in soft.items() if ":" not in n},
                                **{n: x for n, x in (inputs.get("soft") or {}).items() if ":" in n})
                inputs = dict(inputs, plumbing=(P.get("inputs") or {}).get("plumbing") or {}, soft=soft)
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


def lifecycle(word, ctx, mac_root=None, host_root=None, out=print, pull=None, shared=None, busy_raises=False):
    """Run WORD (not `plan`).  ctx has pulled rows and fresh facts (steps 1-2).  Returns the exit code.
    With SHARED (a composite's child) it takes no lock, refreshes no rows, runs neither the runner's
    pre_plan nor its preflight, and uses the composite's stamp, copy and (when given) plan.
    BUSY_RAISES (with_lock_wait): a held lock is a Busy raised, not a refusal said, and so is a cell
    of a CELL_LOCKED family's plan that another live run holds (without it: planned not-runnable)."""
    fam = word.family
    mod = runner(fam)
    if pull is not None:
        ctx.options.setdefault("pull", pull)
    locks, rs = [], None
    cells_lk = None
    by_cell = shared is None and cell_locked(word)
    if shared is None:
        try:
            locks = acquire_locks(family_locks(word), "pending", mac_root, shared=(fam,) if by_cell else ())
        except Busy as e:
            if busy_raises:
                raise
            out(f"t2: {e}")
            return EXIT_REFUSED
    try:
        if shared is None:
            refresh_rows(ctx, pull, out)
        # 3 preflight
        hosts = [h for h in ctx.hosts]
        bad = preflight(ctx.facts, hosts)
        if bad:
            out("t2: preflight refuses: " + "; ".join(bad))
            return EXIT_REFUSED
        if shared is None:
            if getattr(word, "update", False):
                busy = unfinished_runs(None, mac_root)
                if busy:
                    out(f"t2: GuestTest2 update waits for every Test2 run to end: {busy[-1]} is unfinished. "
                        f"`{resume_hint(busy[-1], mac_root)}` finishes it")
                    return EXIT_REFUSED
            for f_ in (x for x in family_locks(word) if x in RUN_PREFIX):
                # (a live run beside this one, over other cells, holds its locks: it isn't unfinished)
                busy = unfinished_runs(f_, mac_root, exclude=held_runs(mac_root) if by_cell else ())   # file times: the real clock
                if busy:
                    out(f"t2: run {busy[-1]} of {f_} is unfinished (it began host work and has no report): its checks "
                        f"or queue runners may still run on the hosts. `{resume_hint(busy[-1], mac_root)}` finishes it; once nothing "
                        f"of it runs, a report.txt in {run_dir(busy[-1], mac_root)} sets it aside")
                    return EXIT_REFUSED
            # never another run's: a held lock (any family), or an unfinished run whose host work may go on
            live = held_runs(mac_root) | {(lk.holder() or {}).get("run") for lk in locks}
            live |= set(unfinished_runs(None, mac_root))
            lefts = our_leftovers(mac_root, live)
            if lefts:
                remove_leftovers(lefts, out, mac_root=mac_root)
            gone = gc_mac_trees(ctx.checkout, mac_root, live)
            if gone:
                out(f"t2: removed the Mac trees of ended runs: {', '.join(gone)}")
            if mod is not None and hasattr(mod, "preflight"):
                mod.preflight(ctx, out)
            if mod is not None and hasattr(mod, "pre_plan"):
                mod.pre_plan(ctx, word, out)
        # 4 plan
        if by_cell:
            # (the cells other live runs hold: this plan makes them not-runnable)
            ctx.options["running"] = running_cells(fam, mac_root)
            ctx.options["running_skip"] = True
        plan = shared.plan if shared is not None and shared.plan is not None else planmod.make(word, ctx)
        miss = (plan.set or {}).get("missing_on")
        if miss:
            out(f"t2: {plan.set['dir']} isn't on {', '.join(miss)}: a ROUND must be on every host in use "
                "(stick/build-round.sh ROUND, or --hosts)")
            return EXIT_REFUSED
        run = new_run_id(fam, stamp=shared.stamp if shared is not None else None,
                         suffix=shared.suffix if shared is not None else None)
        for lk in locks:
            write_json(lk.info, {"run": run, "pid": os.getpid(), "start": utc_now()})
        if by_cell:
            cells_lk = CellLocks(fam, mac_root)
            try:
                plan = hold_due(cells_lk, plan, word, ctx, run, mac_root)
            except Busy as e:
                if busy_raises:
                    raise
                out(f"t2: {e}")
                return EXIT_REFUSED
            taken = taken_cell(plan, ctx.options.get("running"), fam, mac_root) if busy_raises else None
            if taken is not None:
                raise taken     # (nothing began: the finally lets go of what this holds)
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
            for pc in plan.by_status("to-class"):
                out(f"to-class: {pc.cell}: " + "; ".join(pc.reasons))
            for pc in nr:
                if getattr(pc, "refused", False):
                    out(f"refused: {pc.cell}: " + "; ".join(pc.reasons))
            if shared is None:
                out(report.sweep_line(ctx.classes.get("policy"), last_sweep(mac_root), ctx.now))
            return plan_exit(plan, EXIT_OK)
        if mod is None:
            out(f"t2: {word.word}'s runner (t2lib/{RUNNERS[fam]}.py) is not built; its plan:")
            out(plan.render().rstrip())
            return EXIT_REFUSED
        save_plan(plan, run, mac_root)
        ctx.options["facts_start"] = {h: dict(f) for h, f in ctx.facts.items()}
        ctx.options["run_start"] = time.time()
        rs = RunState(run, word, plan, ctx, mac_root, host_root, log=out, shared=shared)
        rs.cell_locks = cells_lk
        ctx.run = rs
        rs.log(f"{word.word} run {run}: {plan.totals['due']} due, est. {plan.totals['gm']} gm")
        # 5 copy
        pvo = None
        if shared is not None:
            rs.copy = dict(shared.copy or {})
        else:
            try:
                pvo = copies.preview(ctx.checkout)   # (one preview: the hosts' copies and the Mac tree alike)
                made = copies.make(ctx.checkout, rs.hosts, log=rs.log, pv=pvo)
            except copies.CopyError as e:
                out(f"t2: no private copy: {e}")
                return EXIT_REFUSED
            pv = ctx.copy_preview or {}
            for h, c in made.items():
                rs.copy[h] = {"id": c.id, "path": c.path, "commit": pv.get("commit") or ctx.head, "dirty": pv.get("dirty")}
        try:
            make_mac_tree(rs, ctx, pv=pvo, shared=shared)
        except copies.CopyError as e:
            out(f"t2: no Mac tree: {e}")
            return EXIT_REFUSED
        # 5a-7: the runner builds, rekeys, writes cells.json (rs.write_cells) and runs
        result = mod.run(ctx, plan) or {}
        result = result if isinstance(result, dict) else getattr(result, "__dict__", {})
        # 8-10 on the runner's last plan (R1 5a, R15: its re-plans live in rs.plan)
        return finish(word, ctx, rs.plan or plan, run, result, mac_root, out, pull)
    finally:
        drop_mac_tree(rs, ctx, out)
        if cells_lk is not None:
            cells_lk.release()
        for lk in reversed(locks):
            lk.release()


HOLD_TRIES = 3


def hold_due(cells_lk, plan, word, ctx, run, mac_root=None):
    """CELLS_LK holds every unit of PLAN's due cells for RUN, or the plan is made again with what
    other runs took since (two runs planning at once), up to HOLD_TRIES times; the plan it holds.
    Busy when it still can't."""
    for i in range(HOLD_TRIES):
        try:
            cells_lk.hold([pc.cell for pc in plan.due()], run)
            return plan
        except Busy:
            if i == HOLD_TRIES - 1:
                raise
        ctx.options["running"] = running_cells(word.family, mac_root, exclude=(run,))
        plan = planmod.make(word, ctx)
    return plan


def plan_exit(plan, code):
    """CODE, or higher: to-class cells make it 1, a refused image (contract 1.7) 2."""
    if plan.by_status("to-class"):
        code = max(code, EXIT_FAIL)
    if any(getattr(pc, "refused", False) for pc in plan.by_status("not-runnable")):
        code = max(code, EXIT_REFUSED)
    return code


WEAR_DRIVER = r'''
_d = json.loads(_T2_DATA)
_run, _lo = _d["run"], _d.get("lo")
_jobs = read_jobs(_d.get("log") or DEFAULT_LOG) or []
_ours = collections.Counter()
_kept = collections.Counter()
for _j in _jobs:
    _parts = (_j.tag or "").split(":")
    if (_run in _parts or any(_p.startswith(_run + "-r") for _p in _parts)) and (_lo is None or (_j.t or 0) >= _lo):
        for _k, _v in _j.disks.items():
            if physical(_k):
                _ours[_k] += _v
        _kept.update(_j.kept_disks())
try:
    with open(_d.get("smart") or DEFAULT_SMART) as _f:
        _st = smart_today(_f.read(), _d["day"])
except OSError:
    _st = []
sys.stdout.write(json.dumps({"ours": dict(_ours), "kept": dict(_kept), "smart": _st}, sort_keys=True) + "\n")
'''


def wear_lines_data(run, hosts, before, after, start_epoch, now=None, timeout=60, log=None, smart=None):
    """{host: {disk: {model, window_bytes, ours_bytes, today_bytes[, kept_bytes]}}} for the
    report's wear lines: diskstats between the run's start and end facts (all writers), this
    run's jobs by their wear tags (jobs.log, R22; kept_bytes the part of them a failed run's
    state kept wrote, iolog.sh's keep=), and the day by SMART (smart.log).  wear.py's own
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
        for d, b in (got.get("kept") or {}).items():   # (in ours_bytes: a failed run's state kept)
            if d in disks and isinstance(b, int) and b > 0:
                disks[d]["kept_bytes"] = b
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
    notes += [f"refused: {pc.cell}: " + "; ".join(pc.reasons) for pc in plan.by_status("not-runnable")
              if getattr(pc, "refused", False)]
    try:   # the swap rule's host holds: no stop, but the report says what they did
        notes += ["swap rule: " + n for n in getattr(getattr(ctx, "run", None), "stops", None).hold_notes()]
    except Exception:   # noqa: BLE001 - a RunState or engine without them (tests): no such note
        pass
    try:   # the host rule's holds and host-lost results: no stop either (the results still waiting
        # for their host's confirm count now, as they would have)
        st_ = getattr(getattr(ctx, "run", None), "stops", None)
        if getattr(st_, "pending", None):
            st_.settle(final=True)
        notes += ["host rule: " + n for n in getattr(getattr(ctx, "run", None), "stops", None).host_notes()]
    except Exception:   # noqa: BLE001 - (the same)
        pass
    result["notes"] = list(result.get("notes") or []) + notes
    result.setdefault("last_sweep", last_sweep(mac_root))
    text = report.render_report(result, plan, rows, ctx.classes.get("policy"), ctx.now)
    write_text(os.path.join(run_dir(run, mac_root), "report.txt"), text)
    counts = {}
    for c in result.get("cells") or []:
        if c.get("verdict"):
            counts[c["verdict"]] = counts.get(c["verdict"], 0) + 1
    notify.outcome(run, ", ".join(f"{n} {v}" for v, n in sorted(counts.items())))
    out(text.rstrip())
    return plan_exit(plan, max(int(ex), ex2))


def _is_default_root(mac_root=None):
    from . import DEFAULT_MAC_ROOT, expand
    try:
        return os.path.realpath(mac_root or default_mac_root()) == os.path.realpath(expand(DEFAULT_MAC_ROOT))
    except (OSError, TypeError, ValueError):
        return True


def last_sweep(mac_root=None):
    """The newest complete SweepTest2's sweep.json, with "first_t" (the first complete one's t),
    or None.  An incomplete sweep doesn't count."""
    d = runs_dir(mac_root)
    try:
        names = sorted(n for n in os.listdir(d) if n.startswith("t2w-"))
    except OSError:
        return None
    done = []
    real = _is_default_root(mac_root)
    for n in names:
        doc = read_json(os.path.join(d, n, "sweep.json"))
        if isinstance(doc, dict) and doc.get("complete") is True and doc.get("t"):
            if real and doc.get("universe"):    # (a test's cut sweep, which bko refuses in this root anyway)
                continue
            hs = doc.get("hosts")
            if isinstance(hs, list) and set(planmod.DEFAULT_HOSTS) - set(hs):   # a --hosts subset is no sweep
                continue
            done.append(doc)
    if not done:
        return None
    done.sort(key=lambda x: str(x.get("t")))
    return dict(done[-1], first_t=done[0].get("t"))


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
        pc.fp_carry = getattr(f, "fp_carry", False)     # (not serialized: its derived row takes the new guest soft inputs)
        pc.last_pass = pc.last_pass or f.last_pass
    return saved


def restore_word(w, pj, ctx):
    """What a resumed word and its ctx lose by being parsed again: word.sweep, .update, .release
    and options.extra_due, .identity_bad, .gate_fresh, as lifecycle's plan.json saved them."""
    wj = pj.get("word") or {}
    for k in ("sweep", "update", "release"):
        setattr(w, k, bool(wj.get(k)))
    for k, v in (pj.get("options") or {}).items():
        ctx.options.setdefault(k, v)
    return w


def resume(run, ctx, mac_root=None, out=print, pull=None, shared=None, busy_raises=False):
    """R10: pick RUN up again: its plan.json, the hosts' cells.json, the runner's resume().
    ctx must be made for the run's own hosts (run_hosts); preflight runs again.  With SHARED
    (a composite's resume, under its locks) it takes no lock and refreshes no rows.
    BUSY_RAISES (with_lock_wait): a held lock is a Busy raised, not a refusal said."""
    if not RUN_RE.match(run):
        out(f"t2: not a run id: {run}")
        return EXIT_REFUSED
    pj = read_json(os.path.join(run_dir(run, mac_root), "plan.json"))
    if not pj:
        out(f"t2: no {run_dir(run, mac_root)}/plan.json")
        return EXIT_REFUSED
    fam = pj.get("family")
    comp = composite_of(run, mac_root) if shared is None else None
    if comp:
        # (alone it would run with guards and a lock of its own, outside the composite's run-wide stops)
        out(f"t2: {run} is a child of {comp}, which hasn't finished: `t2 resume {comp}` goes on with it")
        return EXIT_REFUSED
    mod = runner(fam)
    if mod is None or not hasattr(mod, "resume"):
        out(f"t2: resume for {fam} is not built (t2lib/{RUNNERS.get(fam, fam)}.py resume())")
        return EXIT_REFUSED
    want = run_hosts(run, mac_root)
    if want and list(ctx.hosts) != want:
        out(f"t2: {run} ran on {','.join(want)}, not {','.join(ctx.hosts)}")
        return EXIT_REFUSED
    locks, rs = [], None
    cells_lk = None
    try:
        if shared is None:
            from . import words as _w
            w0 = restore_word(_w.Word(fam, ""), pj, ctx)
            by_cell = cell_locked(w0)
            locks = acquire_locks(family_locks(w0), run, mac_root, shared=(fam,) if by_cell else ())
            if by_cell:
                cells_lk = CellLocks(fam, mac_root).hold([c["cell"] for c in pj.get("cells") or []
                                                          if c.get("status") == "due"], run)
    except Busy as e:
        for lk in reversed(locks):
            lk.release()
        if busy_raises:
            raise
        out(f"t2: {e}")
        return EXIT_REFUSED
    try:
        if shared is None:
            refresh_rows(ctx, pull, out)
        bad = preflight(ctx.facts, list(ctx.hosts))
        if bad:
            out("t2: preflight refuses: " + "; ".join(bad))
            return EXIT_REFUSED
        from . import words
        w = restore_word(words.parse(pj["word"]["text"].split(), planmod.known_names(ctx)), pj, ctx)
        plan = plan_from_json(pj, w)
        try:
            fresh = planmod.make(w, ctx)
        except planmod.PlanError as e:
            fresh = None
            out(f"t2: no fresh plan for the derived rows ({e}): none is written")
        adopt_inputs(plan, fresh)
        rs = RunState(run, w, plan, ctx, mac_root, log=out, shared=shared)
        rs.cell_locks = cells_lk
        ctx.run = rs
        for h in rs.hosts:
            doc = read_json(os.path.join(rs.mac_dir, f"cells-{h}.json"), {}) or {}
            rs.copy[h] = doc.get("copy") or (shared.copy or {}).get(h, {}) if shared is not None else doc.get("copy") or {}
        try:
            make_mac_tree(rs, ctx, shared=shared)
        except copies.CopyError as e:
            out(f"t2: no Mac tree: {e}")
            return EXIT_REFUSED
        result = mod.resume(ctx, plan) or {}
        result = result if isinstance(result, dict) else getattr(result, "__dict__", {})
        return finish(w, ctx, rs.plan or plan, run, result, mac_root, out, pull)
    finally:
        drop_mac_tree(rs, ctx, out)
        if cells_lk is not None:
            cells_lk.release()
        for lk in reversed(locks):
            lk.release()


def plan_from_json(pj, word):
    """A Plan back from plan.json (no Resolved inputs: adopt_inputs takes them from a fresh plan)."""
    p = planmod.Plan(word, pj.get("family"))
    p.header, p.set, p.totals = pj.get("header") or {}, pj.get("set"), pj.get("totals") or {}
    p.notes, p.classes, p.unmapped = list(pj.get("notes") or []), list(pj.get("classes") or []), list(pj.get("unmapped") or [])
    p.options = dict(pj.get("options") or {})
    for c in pj.get("cells") or []:
        pc = planmod.PlanCell(c["cell"], c["cell"].split(":", 1)[0])
        for k in ("status", "rule", "reasons", "host", "keys", "last_pass", "est", "rep", "klass", "running", "via",
                  "differ", "part", "placed", "refused", "binaries"):
            if k in c:
                setattr(pc, k, c[k])
        p.cells.append(pc)
    return p
