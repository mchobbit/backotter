"""The scheduler's profiles of a batch's entries (docs/design/batch.md §4.5 "Estimates"):
walls per candidate host set, jobs (for shares, feasibility and packing), builds.

    build(batch, hosts, cap, src) -> [batchsched.Profile]
    Sources(...)                     where the figures come from; every piece injectable
    T2Planner(checkout, hosts, ...)  the real one: each t2 word planned once per candidate set
                                     (planmod.make on a copy of one ctx), as `t2 WORD plan`

By kind:
  t2     with a planner: its plan's totals.wall_s on each candidate set (the batch's hosts, the
         big ones, the small ones, each all-but-one; GateTest2 and the composites the batch's
         hosts only), a set whose plan has a not-runnable cell the full plan places due marked
         not runnable (None); jobs from the full plan's due cells; builds from plan.set.  With
         none (no facts: a test root, or the probe failed): est=, else this file's history,
         else the kind's default, scaled to a subset by its pool slots.
  ht1    holt:G per guest (history, then estimates.tsv), D from ram-sizes.tsv, M 4; packed over
         any subset by estimates.pack at its holt_jobs.  Growable.
  stic1  stic:G per guest; reserves from drive.py's specs when it loads; packed over any subset
         at its stic_slots (drive.py's own simulate wants the hosts' binaries: not read here).
  bs2    bs2's own jobs_of()/split()/wall()/sweep_secs() (testvm/backshot2/bs2, loaded by path)
         on every subset, each host holding every source unless HAVE says otherwise.
  cmd    est=, else the median wall of its id in the last three batches of the file, else 5 min.
est= always wins for the wall (the jobs then keep only their reserves).
"""

import copy
import importlib.machinery
import importlib.util
import os
import statistics

from . import batchfile as bf
from . import batchsched as bs

SPLIT_FAMILIES = ("stic", "holt", "guest", "img")
FAMILY_POOL = {"stic": "stic", "holt": "holt", "guest": "holt", "img": "holt", "gate": "gate"}
IMG_RESERVE = (1.0, 5.0)        # img.RESERVE
GUEST_RESERVE = (2.0, 8.0)      # guest: --reserve 2 M, M its ram_gib (run.ram_gib_first 8 by default)
GATE_RESERVE = (2.0, 4.0)       # gate.base_reserve's default
HOLT_MEM = 4.0                  # ram-sizes.tsv: MEM is 4 for all
FAMILY_RESERVE = {"img": IMG_RESERVE, "guest": GUEST_RESERVE}   # a planless t2 entry's single job


def _family_share(family, pools, c):
    """A planless img or guest entry's share of host C: its pool's slots times its family's job."""
    d = m = 0.0
    for pool in sorted(pools):
        r = FAMILY_RESERVE[family] if FAMILY_POOL.get(family) == pool else bs.DEFAULT_RESERVE[pool]
        n = c.pools.get(pool, 0)
        d, m = d + n * r[0], m + n * r[1]
    return (d, m)


def split_of(e):
    """none | seven | any: how ENTRY's hosts may be chosen (§4.5 Placement)."""
    if e.hosts is not None or e.is_serial:
        return "none"
    if e.kind in ("ht1", "stic1", "bs2"):
        return "any"
    if e.kind == "t2" and e.family in SPLIT_FAMILIES and not e.update:
        return "seven"
    return "none"


def seven_sets(cap):
    """The t2 candidate sets: all, big, small, each all-but-one (non-empty, each once)."""
    full = tuple(cap)
    sets = [full, tuple(h for h in full if cap[h].cls == "big"), tuple(h for h in full if cap[h].cls == "small")]
    sets += [tuple(x for x in full if x != h) for h in full]
    out = []
    for s in sets:
        if s and s not in out:
            out.append(s)
    return out


def load_path(name, path):
    """A module from a file by path (bs2 has no .py: SourceFileLoader)."""
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Sources:
    """est: an estimates.Estimator (.cell(name) -> {wall_s, src}); ram_sizes {GUEST: D}; drive:
    drive.py as a module or None; bs2: testvm/backshot2/bs2 as a module or None; have {host:
    {source}} for bs2 (None: every host holds every source); planner: T2Planner-like (.plan(entry,
    args, hosts) -> plan JSON or None) or None; history(eid) -> [walls]; args {id: t2 argv}."""

    def __init__(self, est=None, ram_sizes=None, drive=None, bs2=None, have=None, planner=None, history=None,
                 args=None, notes=None):
        self.est, self.ram_sizes, self.drive, self.bs2, self.have = est, ram_sizes or {}, drive, bs2, have
        self.planner, self.history = planner, history or (lambda eid: [])
        self.args = args or {}
        self.notes = notes if notes is not None else []

    @classmethod
    def default(cls, checkout, root=None, planner=None, history=None, args=None):
        """The real sources, each best effort (a missing one is a note, never a refusal)."""
        from . import estimates
        notes = []
        est = None
        try:
            from . import ledger
            by = {}
            for r in ledger.view(root, fill_wear=False).rows:
                if r.get("cell"):
                    by.setdefault(r["cell"], []).append(r)
            rows = by
        except Exception as e:   # noqa: BLE001 - history is an estimate source, never fatal
            rows = {}
            notes.append(f"the ledger can't be read for estimates ({type(e).__name__}): estimates.tsv only")
        est = estimates.Estimator(rows, estimates.load_tsv(estimates.default_tsv_path(checkout)))
        sizes = estimates.load_ram_sizes(os.path.join(checkout, "testvm", "holttest1d", "ram-sizes.tsv"))
        drive = bs2 = None
        try:
            from . import plan as planmod
            drive = planmod.load_drive(checkout)
        except Exception:   # noqa: BLE001
            drive = None
        p = os.path.join(checkout, "testvm", "backshot2", "bs2")
        if os.path.isfile(p):
            try:
                bs2 = load_path("t2_batch_bs2", p)
            except Exception as e:   # noqa: BLE001 - its entries take the default
                notes.append(f"testvm/backshot2/bs2 doesn't load ({type(e).__name__}: {e}): bs2 walls are defaults")
        return cls(est, sizes, drive, bs2, None, planner, history, args, notes)


def _cell_wall(src, cell):
    if src.est is None:
        return None, "default"
    e = src.est.cell(cell)
    return float(e.get("wall_s") or 0), e.get("src") or "default"


def _base_wall(e, src):
    """(wall, source) for an entry with no better figure: est=, history, the kind's default."""
    if e.est_s is not None:
        return float(e.est_s), "est="
    hist = src.history(e.id)
    if hist:
        return float(statistics.median(hist)), "history"
    return float(bf.DEFAULT_WALL.get(e.kind, 300)), "default"


def _guest_jobs(src, cells, pool, est_given):
    jobs, srcs = [], set()
    for c in cells:
        g = c.split(":", 1)[1]
        w, s = _cell_wall(src, c)
        srcs.add(s)
        if pool == "holt":
            d, m = src.ram_sizes.get(g, src.ram_sizes.get(g.split("+")[0], bs.DEFAULT_RESERVE["holt"][0])), HOLT_MEM
        else:
            d, m = bs.DEFAULT_RESERVE["stic"]
            if src.drive is not None:
                try:
                    sp = src.drive.load_specs([g])[g]
                    d, m = float(sp.d) * max(1, sp.n), float(sp.m) * max(1, sp.n)
                except Exception:   # noqa: BLE001 - its default reserve
                    pass
        jobs.append(bs.Job(c, 0 if est_given else (w or 0), d, m, pool))
    return jobs, ("tsv" if srcs <= {"tsv"} else ("history" if any(str(x).startswith("history") for x in srcs)
                                                else sorted(srcs)[0] if srcs else "default"))


def _plan_jobs(pj, family):
    """Jobs from a plan JSON's due cells: their estimate, reserve, and placed host when pinned."""
    jobs = []
    pool = FAMILY_POOL.get(family, "holt")
    for c in pj.get("cells") or []:
        if c.get("status") != "due":
            continue
        name = c.get("cell") or "?"
        est = c.get("est") or {}
        res = c.get("reserve")
        if res:
            d, m = res
        elif family == "img":
            d, m = IMG_RESERVE
        elif family == "guest":
            d, m = GUEST_RESERVE
        elif family == "gate":
            d, m = GATE_RESERVE
        else:
            d, m = bs.DEFAULT_RESERVE[pool]
        host = c.get("host")
        jobs.append(bs.Job(name, float(est.get("wall_s") or 0), d, m,
                           FAMILY_POOL.get(name.split(":", 1)[0], pool) if family in ("bko", "sweep") else pool,
                           [host] if host and host != "any" else None))
    return jobs


def _not_runnable_placed(full, sub):
    """Cells the full plan places due that SUB's plan has not-runnable (§4.5 Feasibility)."""
    due = {c.get("cell") for c in full.get("cells") or [] if c.get("status") == "due"}
    return sorted(c.get("cell") for c in sub.get("cells") or []
                  if c.get("status") == "not-runnable" and c.get("cell") in due)


def _t2_profile(e, src, cap, prof):
    planner = src.planner
    if not planner:
        return
    sets = seven_sets(cap) if prof.split == "seven" else [tuple(prof.hosts) if prof.hosts is not None else tuple(cap)]
    args = src.args.get(e.id) or list(e.args)
    full = planner.plan(e, args, sets[0])
    if not full:
        return
    walls = {frozenset(sets[0]): float((full.get("totals") or {}).get("wall_s") or 0)}
    prof.srcs[bs._key(sets[0])] = "plan"
    for s in sets[1:]:
        pj = planner.plan(e, args, s)
        if not pj:
            continue
        bad = _not_runnable_placed(full, pj)
        walls[frozenset(s)] = None if bad else float((pj.get("totals") or {}).get("wall_s") or 0)
        prof.srcs[bs._key(s)] = ("not runnable: " + ", ".join(bad[:3])) if bad else "plan"
    prof.jobs = _plan_jobs(full, e.family)
    st = full.get("set") or {}
    if st.get("pending"):
        prof.builds = st.get("round") or st.get("name")
    if e.est_s is None:
        prof.walls = dict(walls)
        prof.walls[None] = walls[frozenset(sets[0])]
        prof.wall_src = "plan"
    else:
        prof.walls = {None: float(e.est_s)}
        for j in prof.jobs:
            j.wall = 0.0


def _bs2_walls(e, src, cap):
    """{frozenset(S): wall} over every subset S, from bs2's own split()/wall()."""
    m = src.bs2
    a = [x for x in e.args[1:] if x not in ("plan",) and not x.startswith("--")]
    n = int(a[-1]) if a and a[-1].isdigit() else 200
    sources = [x for x in a if not x.isdigit()] or list(getattr(m, "ALPINE", None) or getattr(m, "SOURCES"))
    jobs = m.jobs_of(sources, n)
    out = {}
    for s in bs._subsets(tuple(cap)):
        can = {x: [h for h in s if src.have is None or x in (src.have.get(h) or ())] for x in sources}
        if any(not v for v in can.values()):
            out[frozenset(s)] = None
            continue
        use = [h for h in s if any(h in hs for hs in can.values())]
        if len(use) < len(s):
            out[frozenset(s)] = None     # (a host holding none of the sources: no pool held for nothing)
            continue
        lead = m.leads(can)
        start = m.sweep_secs(sources, n, lead)
        nslots = {h: max(1, min(getattr(m, "MAX_SLOTS", 6), cap[h].pools.get("bs2", 1))) for h in use}
        parts = m.split(jobs, can, nslots, start)
        out[frozenset(s)] = float(max(m.wall(js, nslots[h], start.get(h, 0)) for h, js in parts.items()))
    return out


def build(batch, hosts, cap, src):
    """[batchsched.Profile] for BATCH's entries on HOSTS (CAP: batchsched.capacity of them)."""
    out = []
    serial_all = batch.settings.get("mode") == "serial"
    for e in batch.entries:
        pools = bf.pools_of(e)
        claim = tuple(e.hosts) if e.hosts is not None else None
        split = "none" if serial_all else split_of(e)
        wall, wsrc = _base_wall(e, src)
        p = bs.Profile(e.id, e.kind, e.pos, bf.locks_of(e), pools, e.is_serial or serial_all, e.after, claim,
                       {None: wall}, wsrc, split=split, growable=(e.kind == "ht1" and split == "any"))
        est_given = e.est_s is not None
        try:
            if e.kind == "t2":
                _t2_profile(e, src, cap, p)
            elif e.kind == "ht1" and len(e.args) > 1:
                p.jobs, s = _guest_jobs(src, [f"holt:{g}" for g in e.args[1:]], "holt", est_given)
                if not est_given:
                    p.walls, p.wall_src = {}, s
            elif e.kind == "stic1":
                guests = [g for g in e.args[1:] if not g.startswith("-")]
                if not guests and src.drive is not None:
                    try:
                        guests = list(src.drive.all_guests())
                    except Exception:   # noqa: BLE001
                        guests = []
                if guests:
                    p.jobs, s = _guest_jobs(src, [f"stic:{g}" for g in guests], "stic", est_given)
                    if not est_given:
                        p.walls, p.wall_src = {}, s
            elif e.kind == "bs2" and src.bs2 is not None and not est_given and claim is None and split == "any":
                p.walls, p.wall_src = _bs2_walls(e, src, cap), "bs2"
                full = p.walls.get(frozenset(cap))
                if full is not None:
                    p.walls[None] = full
            if e.kind == "t2" and not p.jobs and e.family in FAMILY_RESERVE and not p.serial:
                p.share = {h: _family_share(e.family, pools, cap[h]) for h in cap}
        except Exception as ex:   # noqa: BLE001 - an estimate source, never fatal
            src.notes.append(f"{e.id}: its estimate failed ({type(ex).__name__}: {ex}): {wsrc}")
            p.walls, p.wall_src, p.jobs = {None: wall}, wsrc, []
        out.append(p)
    return out


class T2Planner:
    """Each t2 word planned per candidate set, as `t2 WORD plan` would on those hosts: one ctx
    (bko.Env.make_ctx: the pull and every family's facts, once), then planmod.make on a copy of it
    with the set's hosts and facts.  Plans are memoized; one that raises is None (and a note)."""

    def __init__(self, checkout, hosts, root=None, round_=None, notes=None, ctx=None, known=None):
        self.checkout, self.hosts, self.root, self.round = checkout, list(hosts), root, round_
        self.notes = notes if notes is not None else []
        self._ctx, self._known, self._memo = ctx, known, {}

    def ctx(self):
        if self._ctx is None:
            from . import bko, words
            w = words.Word("bko", "BkoTest2")
            w.round = self.round
            w.plan = True
            env = bko.Env(self.checkout, self.hosts, self.root)
            self._ctx = env.make_ctx(w)
        return self._ctx

    def known(self):
        if self._known is None:
            from . import plan as planmod
            self._known = planmod.known_names(self.ctx())
        return self._known

    def plan(self, entry, args, hosts):
        k = (entry.id, tuple(hosts))
        if k in self._memo:
            return self._memo[k]
        pj = None
        try:
            from . import plan as planmod
            from . import words
            argv = [x for x in args if not x.startswith("--")]
            w = words.parse(argv, self.known())
            w.plan = True
            base = self.ctx()
            c = copy.copy(base)
            c.hosts = [h for h in hosts if h in base.facts]
            c.facts = {h: base.facts[h] for h in c.hosts}
            c.options = dict(base.options)
            if not c.hosts:
                raise RuntimeError("no host of the set answered the facts probe")
            pj = planmod.make(w, c).to_json()
        except Exception as ex:   # noqa: BLE001 - the entry falls back to est=/history/default
            self.notes.append(f"{entry.id}: no plan on {','.join(hosts)} ({type(ex).__name__}: {ex})")
            pj = None
        self._memo[k] = pj
        return pj
