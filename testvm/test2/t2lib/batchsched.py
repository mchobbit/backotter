"""Which batch entries run at once, and on which hosts (docs/design/batch.md §4.5).  Pure: no
I/O; it imports neither hostrun nor hostjob (estimates.pack is pure).

    capacity(table, hosts, facts=None) -> {host: HostCap}   pools from hosts.limits(), the ceiling
    Profile, Job                what the scheduler knows of an entry and of its jobs
    candidate_sets(p, cap)      the host sets P may take, feasible ones only
    admissible(p, hosts, cap, busy, live=None) -> (ok, why)
    decide(profiles, cap, running, sessions, live, now, ...) -> [Start | Grow | Wait]
    simulate(profiles, cap, running, now, ...) -> Schedule

The rules, in the spec's words:
  Pools     an entry placed on a host takes ALL of each pool it uses there (stic, holt, bs2,
            gate; a serial entry or a cmd with hosts= every pool), so a pool has one owner a
            host and the table's limits can't be exceeded by adding entries up.
  Shares    an entry's share of host H is min(ceiling(H), the sum over its pools of the n
            largest single-job reserves (DISK, MEM) of its jobs that can run on H, n the pool's
            slots there; each axis on its own).  It joins H only when every share there fits
            the ceiling, or when it would be alone there; a host of unknown ceiling takes one
            entry at a time.
  Live join at run time only (LIVE: pick-host's probe), a host whose shares don't fit still
            takes an entry whose pools are free there, when its cmp-run.sh line is empty, the
            room after it holds the entry's largest single job, and its load a core (with
            pick-host's LOAD_PENALTY on the shared host) is under 1.0.  simulate() never does.
  Order     ready entries longest first (LPT), file position breaking ties; not ready: an
            after= entry unfinished, a serial barrier, a lock (the lock matrix) or a session.
  Placement of the feasible, admissible candidate sets the earliest predicted end wins; ties
            go to the set without the shared host (Chopin), then the bigger set, then the
            table's order.  It starts there when that end is no later than waiting for its best
            set, or no later than the batch's predicted finish (FINISH, simulate()'s first
            pass): the no-idle-host rule.
  Growth    a running ht1 entry grows onto a host whose holt pool is free once the ready
            entries are placed (and none of them waits for that pool there).

Walls: a profile's walls {frozenset(hosts): s or None (not runnable there)}; a set with none is
packed from the profile's jobs (estimates.pack), else scaled from walls[None] (the figure on the
batch's hosts) by the pool slots the set has: work that spreads, spread evenly.
"""

from . import estimates

POOLS = ("stic", "holt", "bs2", "gate")
TICK_S = 60
EPS = 1e-6
GROW_MIN_S = 900          # an ht1 with less than this left doesn't grow
SHARED_ROLES = ("fleet", "store-mirror")   # pick-host's: the shared host (Chopin)
LOAD_PENALTY = 4.0                          # pick-host's
DEFAULT_RESERVE = {"stic": (3.0, 6.0), "holt": (4.0, 4.0), "bs2": (2.0, 3.0), "gate": (2.0, 4.0)}
SPLITS = ("none", "seven", "any")


class HostCap:
    """One batch host: its pools' slots, its cmp-run.sh ceiling (DISK, MEM) or None, its class
    (big, small), whether it is the shared host (Chopin: ties go against it), its arch, whether
    it is a big build host (holt.big_build_hosts: where a HoltTest2 may build bko-holt)."""

    def __init__(self, name, cls=None, pools=None, ceiling=None, arch="amd64", shared=False, index=0,
                 builder=False):
        self.name, self.cls, self.arch = name, cls, arch
        self.pools = dict(pools or {})
        self.ceiling = tuple(ceiling) if ceiling else None
        self.shared, self.index = bool(shared), index
        self.builder = bool(builder)

    def to_json(self):
        return {"name": self.name, "class": self.cls, "pools": dict(self.pools),
                "ceiling": list(self.ceiling) if self.ceiling else None, "arch": self.arch, "shared": self.shared,
                "builder": self.builder}


def ceiling_of(f):
    """facts.ceiling()'s rule, kept pure: F's preflight.ram.ceiling as (D, M), else None.  F may
    also be a bare [D, M] (a stub facts source, batch.json's saved ceilings)."""
    c = f
    if isinstance(f, dict):
        c = (((f.get("preflight") or {}).get("ram") or {}).get("ceiling"))
    try:
        return float(c[0]), float(c[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None


def capacity(table, hosts, facts=None):
    """{host: HostCap} for HOSTS, in their order: each pool's slots from hosts.limits() (gate 1);
    the ceiling from FACTS ({host: facts, or [D, M]}), None when not known."""
    from . import hosts as H
    from .holt import big_build_hosts   # (import-time pure: HoltTest2's own stop rule's predicate)
    builders = set(big_build_hosts(list(hosts), table))
    out = {}
    for i, h in enumerate(hosts):
        lim = H.limits(h, table)
        r = H.find(h, True, table) or {}
        out[h] = HostCap(h, r.get("class"), {"stic": lim["stic_slots"], "holt": lim["holt_jobs"],
                                             "bs2": lim["bs2_slots"], "gate": 1},
                         ceiling_of((facts or {}).get(h)), H.row_arch(r) if r else "amd64",
                         bool(set(r.get("roles") or ()) & set(SHARED_ROLES)), i, h in builders)
    return out


class Job:
    """One job of an entry: a cell, a guest, a case.  hosts: where it may run (None: anywhere);
    d, m: its single-job reserve (GiB); pool: the pool it takes a slot of."""

    def __init__(self, name, wall, d=None, m=None, pool="holt", hosts=None):
        self.name, self.wall, self.pool = name, float(wall or 0), pool
        dd = DEFAULT_RESERVE.get(pool, (2.0, 4.0))
        self.d = float(d) if d is not None else dd[0]
        self.m = float(m) if m is not None else dd[1]
        self.hosts = tuple(hosts) if hosts else None

    def runs_on(self, h):
        return self.hosts is None or h in self.hosts

    def to_json(self):
        return [self.name, self.wall, self.d, self.m, self.pool, list(self.hosts) if self.hosts else None]

    @classmethod
    def from_json(cls, x):
        return cls(*x)


def _key(hosts):
    return ",".join(sorted(hosts)) if hosts is not None else "*"


def _unkey(k):
    return None if k == "*" else frozenset(x for x in k.split(",") if x)


class Profile:
    """An entry as the scheduler sees it.
    walls     {frozenset(hosts): seconds or None (not runnable on that set)}; None as the key:
              the figure on the batch's hosts, scaled to a set without its own (see the module)
    wall_src  where the figure came from: est=, history, tsv, plan, default
    hosts     a hosts= claim (never split), else None
    split     none | seven (a t2 Stic/Holt/Guest/Img word) | any (ht1, stic1, bs2)
    growable  an ht1: grows onto a freed holt pool
    jobs      [Job]: what shares, feasibility and packed walls are made from
    share     {host: (D, M)}: a share given outright, overriding the jobs'
    builds    the round its plan would build, or None (two that build the same conflict)
    needs_builder  its run may build bko-holt linux (batchfile.builds_holt): a set takes it only
              with a big build host in it (HostCap.builder), as HoltTest2's stop rule asks"""

    def __init__(self, id, kind, pos, locks=(), pools=(), serial=False, after=(), hosts=None, walls=None,
                 wall_src="default", splittable=False, growable=False, share=None, single=None, jobs=None,
                 split=None, builds=None, srcs=None, needs_builder=False):
        self.id, self.kind, self.pos = id, kind, pos
        self.locks, self.pools = frozenset(locks), frozenset(pools)
        self.serial, self.after = bool(serial), tuple(after)
        self.hosts = tuple(hosts) if hosts is not None else None
        self.walls = dict(walls or {})
        self.wall_src = wall_src
        self.split = split if split in SPLITS else ("any" if splittable else "none")
        self.growable = bool(growable)
        self.share = {h: tuple(v) for h, v in (share or {}).items()}
        self.single = {h: tuple(v) for h, v in (single or {}).items()}
        self.jobs = [j if isinstance(j, Job) else Job.from_json(j) for j in (jobs or [])]
        self.builds = builds
        self.srcs = dict(srcs or {})        # {set key: source of that set's wall}, for --json
        self.needs_builder = bool(needs_builder)

    @property
    def splittable(self):
        return self.split != "none"

    def wall(self, hosts):
        """The figure for HOSTS when the profile has one, else walls[None] (no scaling: see
        wall_on for the scheduler's)."""
        w = self.walls.get(frozenset(hosts)) if hosts is not None else None
        if w is None:
            w = self.walls.get(None)
        return float(w) if w is not None else 300.0

    def to_json(self):
        return {"id": self.id, "kind": self.kind, "pos": self.pos, "locks": sorted(self.locks),
                "pools": sorted(self.pools), "serial": self.serial, "after": list(self.after),
                "hosts": list(self.hosts) if self.hosts is not None else None,
                "walls": {_key(k): v for k, v in sorted(self.walls.items(), key=lambda kv: _key(kv[0]))},
                "wall_src": self.wall_src, "split": self.split, "growable": self.growable,
                "share": {h: list(v) for h, v in sorted(self.share.items())},
                "jobs": [j.to_json() for j in self.jobs], "builds": self.builds,
                "srcs": dict(sorted(self.srcs.items())), "needs_builder": self.needs_builder}

    @classmethod
    def from_json(cls, d):
        walls = {_unkey(k): v for k, v in (d.get("walls") or {}).items()}
        return cls(d["id"], d["kind"], d.get("pos", 0), d.get("locks") or (), d.get("pools") or (), d.get("serial"),
                   d.get("after") or (), d.get("hosts"), walls, d.get("wall_src", "default"),
                   growable=d.get("growable"), share=d.get("share"), jobs=d.get("jobs"), split=d.get("split"),
                   builds=d.get("builds"), srcs=d.get("srcs"), needs_builder=d.get("needs_builder"))


class Start:
    def __init__(self, id, hosts, pools, why="", end=None):
        self.id, self.hosts, self.pools, self.why, self.end = id, tuple(hosts), tuple(sorted(pools)), why, end

    def __repr__(self):
        return f"Start({self.id}, {','.join(self.hosts)})"


class Grow:
    """Start one more runner of a running ht1 entry on HOST (ht1-farm.sh RUN --join --hosts H)."""

    def __init__(self, id, host, why="", end=None):
        self.id, self.host, self.why, self.end = id, host, why, end

    def __repr__(self):
        return f"Grow({self.id}, {self.host})"


class Wait:
    """KIND: after, serial, lock (the batch's own), session (a session's lock or pool: counts
    toward lock-wait), capacity (pools or shares), later (the no-idle rule kept it for a better
    set)."""

    def __init__(self, id, why, session=False, kind=None):
        self.id, self.why, self.session = id, why, bool(session)
        self.kind = kind or ("session" if session else "capacity")

    def __repr__(self):
        return f"Wait({self.id}: {self.why})"


class Running:
    """An entry in flight: its hosts and pools (its claim, its grows included), when it
    started, its predicted end."""

    def __init__(self, id, hosts, pools, start, end=None):
        self.id, self.hosts, self.pools, self.start, self.end = id, tuple(hosts), frozenset(pools), start, end


class Session:
    """A run of a session's (a lock that is not the batch's): it owns POOLS on HOSTS (None: every
    host, while it plans) and holds LOCKS."""

    def __init__(self, run, locks, pools, hosts=None):
        self.run, self.locks, self.pools = run, frozenset(locks), frozenset(pools)
        self.hosts = tuple(hosts) if hosts is not None else None


# --- walls, shares, sets ---------------------------------------------------------------------------

def _ordered(hosts, cap):
    return tuple(h for h in cap if h in set(hosts))


def units(p, hosts, cap):
    """The slots P's pools have on HOSTS: what a split entry's work spreads over."""
    n = 0
    for h in hosts:
        c = cap.get(h)
        if c is not None:
            n += sum(c.pools.get(pool, 0) for pool in sorted(p.pools))
    return n


class Memo:
    def __init__(self):
        self.walls = {}


def wall_on(p, hosts, cap, memo=None):
    """P's predicted wall on HOSTS (seconds), or None: P can't run there."""
    k = frozenset(hosts)
    if k in p.walls:
        w = p.walls[k]
        return float(w) if w is not None else None
    mk = (p.id, tuple(sorted(k)))
    if memo is not None and mk in memo.walls:
        return memo.walls[mk]
    w = None
    full = tuple(cap)
    if p.jobs and p.split != "none" and p.hosts is None and k and sum(j.wall for j in p.jobs) > 0:
        pool = sorted(p.pools)[0] if p.pools else "holt"
        hs = list(_ordered(hosts, cap))
        jobs = [(j.name, j.wall, j.d, list(j.hosts) if j.hosts else None) for j in p.jobs]
        w0, _place, left = estimates.pack(jobs, hs, slots={h: max(1, cap[h].pools.get(pool, 1)) for h in hs})
        w = float(w0) if not left else None
    else:
        base = p.walls.get(None)
        if base is None:
            base = p.walls.get(frozenset(full))
        base = float(base) if base is not None else 300.0
        if p.split != "none" and p.hosts is None and k and k != frozenset(full):
            u_full, u_set = units(p, full, cap), units(p, hosts, cap)
            w = base * u_full / u_set if u_set else None
        else:
            w = base
    if memo is not None:
        memo.walls[mk] = w
    return w


def share_on(p, h, cap):
    """P's share (D, M) of host H (see the module): never over H's ceiling."""
    c = cap.get(h)
    if p.share.get(h) is not None:
        s = p.share[h]
    elif not p.pools:
        s = (0.0, 0.0)
    elif p.serial or set(p.pools) == set(POOLS) and p.kind == "cmd":
        s = c.ceiling if c is not None and c.ceiling else (1e9, 1e9)
    else:
        d = m = 0.0
        for pool in sorted(p.pools):
            n = c.pools.get(pool, 0) if c is not None else 0
            js = [j for j in p.jobs if j.pool == pool and j.runs_on(h)]
            if js:
                d += sum(sorted((j.d for j in js), reverse=True)[:n])
                m += sum(sorted((j.m for j in js), reverse=True)[:n])
            else:
                dd = DEFAULT_RESERVE.get(pool, (2.0, 4.0))
                d += n * dd[0]
                m += n * dd[1]
        s = (d, m)
    if c is not None and c.ceiling:
        s = (min(s[0], c.ceiling[0]), min(s[1], c.ceiling[1]))
    return (float(s[0]), float(s[1]))


def single_on(p, h, cap):
    """P's largest single-job reserve (D, M) on H (each axis on its own)."""
    if p.single.get(h) is not None:
        return tuple(p.single[h])
    js = [j for j in p.jobs if j.runs_on(h)]
    if js:
        return (max(j.d for j in js), max(j.m for j in js))
    ds = [DEFAULT_RESERVE.get(pool, (2.0, 4.0)) for pool in sorted(p.pools)] or [(0.0, 0.0)]
    return (max(x[0] for x in ds), max(x[1] for x in ds))


def feasible(p, hosts, cap):
    """Whether P on HOSTS still has a host for every job the batch's full set could run, and no
    host of it is one none of its jobs can use (a pool held for nothing); and, for a P that may
    build bko-holt (needs_builder), a big build host among HOSTS (HoltTest2 stops without one)."""
    k = frozenset(hosts)
    if any(h not in cap for h in hosts):
        return False
    if p.needs_builder and not any(cap[h].builder for h in hosts):
        return False
    if k in p.walls and p.walls[k] is None:
        return False
    full = set(cap)
    for j in p.jobs:
        if j.hosts is not None and set(j.hosts) & full and not set(j.hosts) & k:
            return False
    if p.jobs and p.split != "none":
        for h in hosts:
            if not any(j.runs_on(h) for j in p.jobs):
                return False
    return True


def _subsets(hosts):
    hs = list(hosts)
    out = []
    for mask in range(1, 1 << len(hs)):
        out.append(tuple(h for i, h in enumerate(hs) if mask >> i & 1))
    return out


def candidate_sets(p, cap):
    """The host sets P may take, feasible ones only, in a fixed order (the batch's hosts first):
    hosts= alone; ht1/stic1/bs2 any subset; a t2 Stic/Holt/Guest/Img word the batch's hosts, the
    big ones, the small ones and each all-but-one (at most seven); everything else (GateTest2,
    BkoTest2/SweepTest2, a serial entry) the batch's hosts.  An entry with no pools: ()."""
    if p.hosts is not None:
        return [tuple(p.hosts)]
    if not p.pools:
        return [()]
    full = tuple(cap)
    if p.serial or p.split == "none":
        sets = [full]
    elif p.split == "seven":
        sets = [full]
        for cls in ("big", "small"):
            sets.append(tuple(h for h in full if cap[h].cls == cls))
        for h in full:
            sets.append(tuple(x for x in full if x != h))
    else:
        sets = sorted(_subsets(full), key=lambda s: (-len(s), [cap[h].index for h in s]))
    out, seen = [], set()
    for s in sets:
        if not s or s in seen:
            continue
        seen.add(s)
        if feasible(p, s, cap) and wall_on(p, s, cap) is not None:
            out.append(s)
    return out


# --- who holds what ------------------------------------------------------------------------------

class Busy:
    """The hosts' state at one instant: owners {(host, pool): id or 'session RUN'}, the entries on
    each host {host: [id]}, their shares {(host, id): (D, M)} and ends {id: t (inf: a session)}."""

    def __init__(self):
        self.owners, self.on, self.shares, self.ends = {}, {}, {}, {}

    def put(self, p_or_id, hosts, pools, end, cap, p=None):
        eid = p_or_id if isinstance(p_or_id, str) else p_or_id.id
        p = p if p is not None else (None if isinstance(p_or_id, str) else p_or_id)
        for h in hosts:
            for pool in sorted(pools):
                self.owners[(h, pool)] = eid
            if eid not in self.on.setdefault(h, []):
                self.on[h].append(eid)
            self.shares[(h, eid)] = share_on(p, h, cap) if p is not None else (0.0, 0.0)
        self.ends[eid] = end


def owners(running, sessions, cap, by_id=None, now=0.0):
    """Busy: every pool in use by RUNNING (batch entries) and SESSIONS."""
    b = Busy()
    by_id = by_id or {}
    for r in running:
        end = r.end if r.end is not None else now
        b.put(r.id, r.hosts, r.pools, max(float(end), float(now)), cap, by_id.get(r.id))
    for s in sessions or ():
        name = f"session {s.run}"
        for h in (s.hosts if s.hosts is not None else tuple(cap)):
            for pool in sorted(s.pools):
                b.owners.setdefault((h, pool), name)
        b.ends[name] = float("inf")
    return b


def _fits(used, add, ceil):
    return used[0] + add[0] <= ceil[0] + EPS and used[1] + add[1] <= ceil[1] + EPS


def live_ok(p, h, cap, probe):
    """(ok, why): pick-host's probe of H lets P join it live (see the module)."""
    if probe and probe.get("joined"):
        return False, f"{h} took a live join since its last probe: the next probe decides"
    if not probe or not probe.get("ok"):
        return False, f"{h} didn't answer the probe"
    if probe.get("waiting") != 0:
        return False, f"{h}'s cmp-run.sh line has {probe.get('waiting', '?')} waiting"
    room = probe.get("line_room") or probe.get("free")
    one = single_on(p, h, cap)
    if not room or room[0] + EPS < one[0] or room[1] + EPS < one[1]:
        return False, f"{h} has no room after its line for {one[0]:g} {one[1]:g}"
    lpc = probe.get("load_per_core")
    if lpc is None:
        load, cores = probe.get("load"), probe.get("nproc") or 1
        if load is None:
            return False, f"{h}'s load is not known"
        lpc = (float(load) + (LOAD_PENALTY if cap[h].shared else 0.0)) / max(1, cores)
    if lpc >= 1.0:
        return False, f"{h}'s load a core is {lpc:.2f}" + (" (with the shared host's penalty)" if cap[h].shared else "")
    return True, "live join"


def admissible(p, hosts, cap, busy, live=None):
    """(ok, why): on each host of HOSTS, P's pools have no owner and the shares fit the ceiling
    (or P would be alone there, or a live join holds).  BUSY: a Busy (or a bare {(host, pool):
    owner}, pools only)."""
    if not isinstance(busy, Busy):
        b = Busy()
        b.owners = dict(busy or {})
        busy = b
    joined = []
    for h in hosts:
        if h not in cap:
            return False, f"{h} is not a batch host"
        for pool in sorted(p.pools):
            o = busy.owners.get((h, pool))
            if o is not None and o != p.id:
                return False, f"pool {pool} on {h}: {o}"
        others = [x for x in busy.on.get(h, []) if x != p.id]
        if not others:
            continue
        c = cap[h]
        if c.ceiling is None:
            ok, why = (live_ok(p, h, cap, live.get(h)) if live is not None else (False, ""))
            if not ok:
                return False, f"shares: {h}'s ceiling is not known and {others[0]} is there"
            joined.append(h)
            continue
        used = [0.0, 0.0]
        for x in others:
            s = busy.shares.get((h, x), (0.0, 0.0))
            used[0] += s[0]
            used[1] += s[1]
        if not _fits(used, share_on(p, h, cap), c.ceiling):
            ok, why = (live_ok(p, h, cap, live.get(h)) if live is not None else (False, ""))
            if not ok:
                full = used[0] >= c.ceiling[0] - EPS or used[1] >= c.ceiling[1] - EPS
                return False, (f"shares: {','.join(others)} {'fills' if full else 'leaves too little of'} {h}"
                               + (f" ({why})" if why else ""))
            joined.append(h)
    return True, ("live join on " + ",".join(joined)) if joined else ""


def _host_free_at(p, h, cap, busy, now):
    """When P could take host H (its pools free, the shares fitting) going by the predicted ends."""
    t = float(now)
    for pool in sorted(p.pools):
        o = busy.owners.get((h, pool))
        if o is not None and o != p.id:
            t = max(t, busy.ends.get(o, float("inf")))
    others = [x for x in busy.on.get(h, []) if x != p.id]
    if others:
        c = cap[h]
        if c.ceiling is None:
            t = max(t, max(busy.ends.get(x, float("inf")) for x in others))
        else:
            used = [0.0, 0.0]
            for x in others:
                s = busy.shares.get((h, x), (0.0, 0.0))
                used = [used[0] + s[0], used[1] + s[1]]
            if not _fits(used, share_on(p, h, cap), c.ceiling):
                t = max(t, max(busy.ends.get(x, float("inf")) for x in others))
    return t


def _choice_key(s, end, cap):
    return (round(end, 6), any(cap[h].shared for h in s), -len(s), [cap[h].index for h in s])


def _is_serial(eid, profiles):
    for p in profiles:
        if p.id == eid:
            return p.serial
    return False


def best_set(p, cap, memo=None):
    """P's set with the earliest end when every host is free (LPT's figure), and that wall."""
    best = None
    for s in candidate_sets(p, cap):
        w = wall_on(p, s, cap, memo)
        if w is None:
            continue
        k = _choice_key(s, w, cap)
        if best is None or k < best[0]:
            best = (k, s, w)
    return (best[1], best[2]) if best else ((), p.wall(()))


def _order(profiles, cap, memo=None):
    """LPT: the longest predicted wall (on its best set) first, file position breaking ties."""
    return sorted(profiles, key=lambda p: (-best_set(p, cap, memo)[1], p.pos))


# --- decide ---------------------------------------------------------------------------------------

def decide(profiles, cap, running, sessions=(), live=None, now=0.0, done=(), conflicts=None, serial_all=False,
           stopped=(), finish=None, memo=None, grow=True):
    """[Start | Grow | Wait] for every profile neither DONE nor running nor STOPPED, at NOW.
    RUNNING: [Running]; SESSIONS: [Session]; LIVE: {host: pick-host probe} or None (never in the
    plan); CONFLICTS: the lock matrix {(a, b): reason}; SERIAL_ALL: mode serial; FINISH: the
    batch's predicted finish (simulate()'s .horizon), which the no-idle rule compares with."""
    memo = memo or Memo()
    done = set(done)
    live = dict(live) if live is not None else None   # (a live join below uses up its host's probe)
    by_id = {p.id: p for p in profiles}
    now = float(now)
    running = [Running(r.id, r.hosts, r.pools, r.start, max(float(r.end if r.end is not None else now), now + TICK_S))
               for r in running]
    run_ids = {r.id for r in running}
    out = []
    todo = [p for p in profiles if p.id not in done and p.id not in run_ids and p.id not in set(stopped)]
    by_pos = sorted(profiles, key=lambda p: p.pos)
    started = []   # Running claims made by this call
    busy = owners(running, sessions, cap, by_id, now)
    cf = conflicts or {}
    ends = [r.end for r in running]
    fin = max(float(finish), now) if finish is not None else now
    waits_for_pool = {}   # (host, pool) -> an entry waiting for that pool there (no growth onto it)
    for p in _order(todo, cap, memo):
        ser = serial_all or p.serial
        if any(a not in done for a in p.after):
            out.append(Wait(p.id, "after " + ",".join(a for a in p.after if a not in done), kind="after"))
            continue
        barrier = next((q for q in by_pos if q.pos < p.pos and (serial_all or q.serial) and q.id not in done), None)
        if barrier is not None:
            out.append(Wait(p.id, f"serial: {barrier.id}", kind="serial"))
            continue
        if ser:
            above = next((q for q in by_pos if q.pos < p.pos and q.id not in done), None)
            if above is not None:
                out.append(Wait(p.id, f"serial: {above.id} first", kind="serial"))
                continue
            if running or started:
                out.append(Wait(p.id, "serial: runs alone", kind="serial"))
                continue
        else:
            alone = next((r for r in list(running) + started if (serial_all or _is_serial(r.id, profiles))), None)
            if alone is not None:
                out.append(Wait(p.id, f"serial: {alone.id} runs alone", kind="serial"))
                continue
        clash = next(((r.id, cf[(p.id, r.id)]) for r in list(running) + started if (p.id, r.id) in cf), None)
        if clash:
            out.append(Wait(p.id, f"{clash[1]}: {clash[0]}", kind="lock"))
            continue
        held = next((s for s in sessions or () if p.locks & s.locks), None)
        if held is not None:
            out.append(Wait(p.id, f"lock {','.join(sorted(p.locks & held.locks))}: session {held.run}", session=True))
            continue
        cands = candidate_sets(p, cap)
        if not cands:
            out.append(Wait(p.id, "no feasible host set among the batch's hosts", kind="capacity"))
            continue
        adm, whys, best_wait = [], [], None
        for s in cands:
            w = wall_on(p, s, cap, memo)
            ok, why = admissible(p, s, cap, busy, live)
            if ok:
                adm.append((_choice_key(s, now + w, cap), s, now + w, why))
            else:
                whys.append(why)
            free = max([now] + [_host_free_at(p, h, cap, busy, now) for h in s])
            e = free + w
            if best_wait is None or e < best_wait[0] - EPS:
                best_wait = (e, s)
        if not adm:
            # A session's wait only when every set waits on a session (no set frees by the
            # predicted ends: the batch's own entries never count, §4.4's lock-wait).
            sess = best_wait is not None and best_wait[0] == float("inf")
            why0 = next((x for x in whys if ("session " in x) == sess), whys[0] if whys else "")
            out.append(Wait(p.id, why0 or "no room", session=sess, kind="session" if sess else "capacity"))
            for s in cands:
                for h in s:
                    for pool in sorted(p.pools):
                        waits_for_pool.setdefault((h, pool), p.id)
            continue
        adm.sort(key=lambda x: x[0])
        _k, placed, end, how = adm[0]
        horizon = max([fin] + ends)
        if end > best_wait[0] + EPS and end > horizon + EPS:
            out.append(Wait(p.id, f"later: on {','.join(placed)} it would end +{_hm(end - now)}, past the finish; "
                                  f"{','.join(best_wait[1])} ends it +{_hm(best_wait[0] - now)}", kind="later"))
            for h in best_wait[1]:
                for pool in sorted(p.pools):
                    waits_for_pool.setdefault((h, pool), p.id)
            continue
        pools = set(p.pools) if not ser else (set(POOLS) if placed else set())
        r = Running(p.id, placed, pools, now, end)
        started.append(r)
        ends.append(end)
        busy.put(p, placed, pools, end, cap)
        why = "serial" if ser else ("LPT" + (f"; {how}" if how else "")
                                    + ("" if end <= best_wait[0] + EPS else "; a free host, within the finish"))
        out.append(Start(p.id, placed, pools, why, end))
        if live is not None and how.startswith("live join on "):
            for h in how[len("live join on "):].split(","):
                live[h] = {"ok": False, "joined": True}   # one join a probe: the next probe decides
        if ser:
            break
    if grow and not any(isinstance(a, Start) and (serial_all or _is_serial(a.id, profiles)) for a in out):
        out += _grows(profiles, cap, running, busy, now, memo, waits_for_pool, serial_all)
    return out


def _grows(profiles, cap, running, busy, now, memo, waits_for_pool, serial_all):
    """Grow lines: each running growable entry onto each host whose pools it uses are free there,
    the ready entries placed first (none waits for that pool there), while its queue still has
    lines the runners it has can't take at once.  The remaining work is re-packed over the slots:
    its end moves to now + left x (slots before / slots after); the lines left are its jobs times
    the share of its wall still to run (unknown jobs: always some)."""
    out = []
    by_id = {p.id: p for p in profiles}
    for r in sorted(running, key=lambda r: (by_id[r.id].pos if r.id in by_id else 0)):
        p = by_id.get(r.id)
        if p is None or not p.growable or p.serial or serial_all or p.hosts is not None:
            continue
        hosts = list(r.hosts)
        for h in cap:
            left = (r.end or now) - now
            if left < GROW_MIN_S:
                break
            if h in hosts or any(waits_for_pool.get((h, pool)) for pool in sorted(p.pools)):
                continue
            if not any(j.runs_on(h) for j in p.jobs) and p.jobs:
                continue
            ok, _why = admissible(p, (h,), cap, busy)
            if not ok:
                continue
            old = tuple(_ordered(hosts, cap))
            u_old, u_new = units(p, old, cap), units(p, old + (h,), cap)
            w_old = r.end - r.start if r.end is not None else None
            if not u_old or not w_old or u_new <= u_old:
                continue
            if p.jobs:
                lines_left = len(p.jobs) * min(1.0, left / max(w_old, EPS))
                if lines_left <= u_old + EPS:
                    continue
            end = now + left * u_old / u_new
            busy.put(p, (h,), p.pools, end, cap)
            out.append(Grow(r.id, h, f"its {','.join(sorted(p.pools))} pool on {h} is free", end))
            hosts.append(h)
            r.end = end
    return out


def _hm(s):
    s = max(0, int(round(s)))
    return f"{s // 3600}:{s % 3600 // 60:02d}"


# --- simulate ---------------------------------------------------------------------------------------

class Row:
    def __init__(self, id, hosts, pools, start, end, wall, why, src, grows=None):
        self.id, self.hosts, self.pools, self.start, self.end = id, tuple(hosts), tuple(sorted(pools)), start, end
        self.wall, self.why, self.src = wall, why, src
        self.grows = list(grows or [])     # [(host, t)]

    def to_json(self):
        return {"id": self.id, "hosts": list(self.hosts), "pools": list(self.pools), "start": self.start,
                "end": self.end, "wall": self.wall, "waits": self.why, "src": self.src,
                "grows": [[h, t] for h, t in self.grows]}


class Schedule:
    def __init__(self, rows, finish, busy, stuck, now, horizon=None):
        self.rows, self.finish, self.busy, self.stuck, self.now = rows, finish, busy, stuck, now
        self.horizon = horizon   # what decide() takes as FINISH to start as this schedule did

    def row(self, eid):
        return next((r for r in self.rows if r.id == eid), None)

    def to_json(self):
        return {"rows": [r.to_json() for r in self.rows], "finish": self.finish, "busy": dict(self.busy),
                "stuck": dict(self.stuck), "now": self.now, "horizon": self.horizon}


def _run(profiles, cap, running, now, sessions, done, conflicts, serial_all, stopped, finish):
    t = float(now)
    done = set(done)
    live = {}
    rows = {}
    first_why = {}
    memo = Memo()
    by_id = {p.id: p for p in profiles}
    for r in running:
        end = max(float(r.end if r.end is not None else t), t + TICK_S)
        live[r.id] = Running(r.id, r.hosts, r.pools, r.start, end)
        p = by_id.get(r.id)
        rows[r.id] = Row(r.id, r.hosts, r.pools, r.start, end, end - float(r.start), "-",
                         p.wall_src if p else "running")
    stuck = {}
    for _ in range(20 * len(profiles) + 20):
        acts = decide(profiles, cap, list(live.values()), sessions, None, t, done, conflicts, serial_all, stopped,
                      finish, memo)
        for a in acts:
            if isinstance(a, Start):
                p = by_id[a.id]
                live[a.id] = Running(a.id, a.hosts, a.pools, t, a.end)
                rows[a.id] = Row(a.id, a.hosts, a.pools, t, a.end, a.end - t, first_why.get(a.id, "-"), p.wall_src)
            elif isinstance(a, Grow):
                r = live[a.id]
                live[a.id] = Running(a.id, tuple(_ordered(list(r.hosts) + [a.host], cap)), r.pools, r.start, a.end)
                row = rows[a.id]
                row.grows.append((a.host, t))
                row.end = a.end
                row.wall = a.end - row.start
            else:
                first_why.setdefault(a.id, a.why)
        waiting = [a for a in acts if isinstance(a, Wait)]
        if not live:
            for a in waiting:
                stuck[a.id] = a.why
            break
        t = min(r.end for r in live.values())
        for eid in sorted(k for k, r in live.items() if r.end <= t + EPS):
            done.add(eid)
            del live[eid]
    busy = {}   # host -> when its last work is predicted to end
    for r in rows.values():
        for h in list(r.hosts) + [g[0] for g in r.grows]:
            busy[h] = max(busy.get(h, float(now)), r.end)
    order = sorted(rows.values(), key=lambda r: (r.start, by_id[r.id].pos if r.id in by_id else 0))
    fin = max([r.end for r in order] + [float(now)])
    return Schedule(order, fin, busy, stuck, float(now))


def simulate(profiles, cap, running, now, sessions=(), done=(), conflicts=None, serial_all=False, stopped=(),
             finish=None):
    """decide() run forward over predicted ends: each entry's hosts, start and end, the first
    reason it waited, and the finish.  A running entry ends at max(its predicted end, now + 60 s).
    Sessions are taken to hold what they hold for the whole schedule (the plan can't see their
    ends); what can never start is in .stuck with its reason.  No live join.  Two passes: the
    first with no finish to compare with, the second with the first's finish (the no-idle rule's
    figure); the second is kept when it finishes no later.  .horizon is the FINISH decide() takes
    to start as the kept schedule does.  Deterministic."""
    if finish is not None:
        s = _run(profiles, cap, running, now, sessions, done, conflicts, serial_all, stopped, finish)
        s.horizon = finish
        return s
    one = _run(profiles, cap, running, now, sessions, done, conflicts, serial_all, stopped, None)
    two = _run(profiles, cap, running, now, sessions, done, conflicts, serial_all, stopped, one.finish)
    if two.finish <= one.finish + EPS:
        two.horizon = one.finish
        return two
    return one
