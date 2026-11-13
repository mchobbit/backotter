"""Estimates for the plan form (contract 1.7): per cell gm, wall and GB, and a word's wall time.

A cell's estimate is, in order:
  history:N  the median of its last 3 PASS-like `run` rows (valid ones): gm, wall_s, wear;
  tsv        its row in testvm/test2/estimates.tsv (seeded from docs/design/test2/data/);
  README     testvm/stictest1/README.md's "Estimates (seconds ...)" (stic only);
  default    5 gm.
Wall time: SticTest2 by drive.py's simulate() (its queue, 3 checks per host); HoltTest2 by
packing per host at 3 jobs, guests of D >= 13 GiB one at a time; GateTest2 the fast layers in
sequence plus the longest slow layer per host.
"""

import os
import statistics

DEFAULT_GM = 5.0
BIG_GIB = 13
HOLT_JOBS = 3
STIC_SLOTS = 3


def _f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def load_tsv(path):
    """estimates.tsv -> {cell: {gm, wall_s, gb, gb_disk, src}}."""
    out = {}
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    head = None
    for line in lines:
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        if head is None:
            head = cols
            continue
        r = dict(zip(head, cols))
        c = r.get("cell")
        if not c:
            continue
        out[c] = dict(gm=_f(r.get("gm"), 0.0), wall_s=_f(r.get("wall_s"), 0.0), gb=_f(r.get("gb"), 0.0),
                      gb_disk=_f(r.get("gb_disk")), src=r.get("src", ""))
    return out


def load_ram_sizes(path):
    """testvm/holttest1d/ram-sizes.tsv -> {GUEST[+VARIANT]: D}, the largest D over its rows."""
    out = {}
    try:
        with open(path) as f:
            for line in f:
                if line.startswith("#"):
                    continue
                c = line.rstrip("\n").split("\t")
                if len(c) < 5 or not c[4].replace(".", "", 1).isdigit():
                    continue
                n = c[0] + (f"+{c[1]}" if c[1] not in ("-", "") else "")
                out[n] = max(out.get(n, 0), float(c[4]))
    except OSError:
        pass
    return out


def wear_gb(row):
    w = row.get("wear")
    if not isinstance(w, dict):
        return None
    vals = [v for v in w.values() if isinstance(v, (int, float))]
    if not vals:
        return None
    return sum(vals) / 1e9


class Estimator:
    """rows_by_cell: {cell: [row, ...]} oldest first (the merged ledger's order)."""

    def __init__(self, rows_by_cell, tsv=None, readme=None):
        self.rows = rows_by_cell or {}
        self.tsv = tsv or {}
        self.readme = readme or {}
        self._memo = {}

    def history(self, cell):
        rs = [r for r in self.rows.get(cell, ()) if r.get("kind") == "run" and r.get("verdict") == "PASS"
              and r.get("valid") is True]
        rs = rs[-3:]
        if not rs:
            return None
        gm = [x for x in (_f(r.get("gm")) for r in rs) if x is not None]
        wall = []
        for r in rs:
            w = _f(r.get("wall_s"))
            if w is not None:
                wall.append(max(w - (_f(r.get("waits_s"), 0.0) or 0.0), 0.0))
        gbs = [x for x in (wear_gb(r) for r in rs) if x is not None]
        if not gm:
            return None
        t = self.tsv.get(cell, {})
        return dict(gm=statistics.median(gm), wall_s=statistics.median(wall) if wall else t.get("wall_s", 0.0),
                    gb=statistics.median(gbs) if gbs else t.get("gb", 0.0), src=f"history:{len(rs)}")

    def cell(self, cell):
        if cell in self._memo:
            return self._memo[cell]
        e = self.history(cell)
        if e is None and cell in self.tsv:
            t = self.tsv[cell]
            e = dict(gm=t["gm"], wall_s=t["wall_s"], gb=t["gb"], src="tsv")
        if e is None and cell.startswith("stic:") and cell[5:] in self.readme:
            s = float(self.readme[cell[5:]])
            e = dict(gm=s / 60, wall_s=s, gb=0.0, src="README")
        if e is None:
            e = dict(gm=DEFAULT_GM, wall_s=DEFAULT_GM * 60, gb=0.0, src="default")
        e = dict(gm=round(e["gm"], 2), wall_s=int(round(e["wall_s"] or 0)), gb=round(e["gb"] or 0.0, 2), src=e["src"])
        self._memo[cell] = e
        return e


# --- wall time ---------------------------------------------------------------------------

def stic_wall(drive, guests, est, have, first=(), slots=STIC_SLOTS, ceils=None):
    """drive: testvm/stictest1/drive.py as a module.  guests: names; est: {guest: seconds};
    have: {host: {"OS/NAME", ...}} (the round's binaries there); ceils: {host: (DISK, MEM)},
    cmp-run.sh's ceiling there (none: not checked).  Returns (wall_s, {guest: host},
    [guests no host can run])."""
    if not guests:
        return 0, {}, []
    spec = drive.load_specs(list(guests))
    hosts = {h: dict(have=set(v), slots_free=drive.GUEST_SLOTS, room=None, ceil=(ceils or {}).get(h))
             for h, v in have.items()}
    if not hosts:
        return 0, {}, list(guests)
    est_s = {g: max(int(est.get(g, DEFAULT_GM * 60)), 1) for g in guests}
    rows, left = drive.simulate(list(guests), spec, est_s, hosts, slots, set(first))
    wall = max((r[3] for r in rows), default=0)
    return int(round(wall)), {r[0]: r[1] for r in rows}, list(left)


def pack(jobs, hosts, slots=HOLT_JOBS, big=BIG_GIB):
    """jobs: [(name, seconds, d_gib, allowed_hosts or None)], longest first.  At most SLOTS
    at once per host, and one job of D >= BIG at a time per host.  Returns (wall_s,
    {name: host}, [names no host takes])."""
    hosts = list(hosts)
    if not hosts:
        return 0, {}, [j[0] for j in jobs]
    rem = sorted(jobs, key=lambda j: (-j[1], j[0]))
    left = [j[0] for j in rem if j[3] is not None and not (set(j[3]) & set(hosts))]
    rem = [j for j in rem if j[0] not in left]
    run = {h: [] for h in hosts}          # (end, big)
    place, t = {}, 0.0
    while rem:
        started = False
        for j in list(rem):
            name, sec, d, allowed = j
            cands = [h for h in hosts if (allowed is None or h in allowed) and len(run[h]) < slots
                     and not (d >= big and any(b for _, b in run[h]))]
            if not cands:
                continue
            h = min(cands, key=lambda h: (sum(e - t for e, _ in run[h]), len(run[h]), hosts.index(h)))
            run[h].append((t + sec, d >= big))
            place[name] = h
            rem.remove(j)
            started = True
        if rem and not started:
            ends = [e for h in hosts for e, _ in run[h]]
            if not ends:
                break
            t = min(ends)
            for h in hosts:
                run[h] = [(e, b) for e, b in run[h] if e > t]
    wall = max([e for h in hosts for e, _ in run[h]] + [t])
    return int(round(wall)), place, left


def gate_wall(cells, est, part, host_of):
    """Fast layers in sequence, then the longest slow layer per host (they run in parallel
    on both hosts)."""
    fast = sum(est[c]["wall_s"] for c in cells if part.get(c, "fast") == "fast")
    slow = {}
    for c in cells:
        if part.get(c) == "slow":
            h = host_of.get(c, "any")
            slow[h] = max(slow.get(h, 0), est[c]["wall_s"])
    return int(fast + max(slow.values(), default=0))


def readme_estimates(drive):
    try:
        return drive.readme_estimates()
    except Exception:  # noqa: BLE001 - an estimate source, never fatal
        return {}


def default_tsv_path(checkout):
    return os.path.join(checkout, "testvm", "test2", "estimates.tsv")
