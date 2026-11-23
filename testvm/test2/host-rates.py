#!/usr/bin/env python3
"""Cells an hour, walls and GB written, per host, with the jobs whose scratch was on the SSD
apart from those in the RAM disk: Test2's ledger read for one question, what "RAM first, the SSD
for overflow" (the host table's scratch_mode, 2026-11-23: testvm/HOSTS.md) does to a host's rate.

Usage (on the Mac, after `t2 pull`, which mirrors the hosts' ledgers and joins the wear log):
    host-rates.py [--since UTC] [--until UTC] [--run RUN,...] [--family F,...] [--hosts H,...]
                  [--cut UTC] [--mac-root DIR]
        --since, --until   rows that ended (their t) in that time, 2026-11-24T06:00:00Z or a date
        --run              rows of these runs (t2h-..., t2v-..., t2s-...; a rerun's -r2 too)
        --family           holt, guest, stic, gate, img, bs2 (default: holt,guest,stic)
        --hosts            default: every host with a ledger in the mirror
        --cut UTC          a change made then (the work area put on the SSD): the table twice,
                           for the rows before it and the rows since, and per host the cells
                           that passed there both before and since, the wall since against the
                           wall before (each the cell's median; then the median of the ratios):
                           each host against itself.  With every host changed at once there is
                           no control: a host put back to scratch_mode ram for a cycle is one.

One line per host and family, and one for the host's families together.  A HoltTest row
says where its scratch was (its extra.peaks.mode: ram, the RAM disk; disk, /bkoscratch), so
holt has a line for each, "holt ram" and "holt ssd", when a host has both: the per-job walls
side by side, and the GB the SSD ones cost.  The last lines pair them: the cells that passed on
a host both ways (in the rows chosen), the SSD wall against the RAM wall.
    cells      rows (a job each: a cell's attempt), and how many of them PASS
    busy h     the hours in which at least one of them was running or waiting for its room
               (the union of [start - waits_s, t]): the host's time on these jobs
    cells/h    cells over busy h: the rate, whatever ran beside each other
    at once    the jobs' walls summed, over busy h: how many ran at once on average
    wall med   the median wall of a job, minutes
    GB         what the jobs wrote to the host's disks (iolog.sh's wear log by WEAR_TAG, joined
               to the rows by t2 pull: LEDGER/wear-HOST.json); "-" when no row has a figure yet
    GB/cell    GB over the rows that have a figure
Rows that are not runs (kind), or have no end, are left out.  Reads only; stdlib, Python 3.9+.
"""

import argparse
import calendar
import json
import os
import re
import statistics
import sys

STAMP = re.compile(r"(\d{4})-(\d\d)-(\d\d)(?:T(\d\d):(\d\d)(?::(\d\d))?Z?)?\Z")
HERE = os.path.dirname(os.path.realpath(__file__))


def epoch(s):
    """A UTC stamp (2026-11-24T06:00:00Z, 2026-11-24T06:00, 2026-11-24) as seconds, or None."""
    m = STAMP.match(str(s or ""))
    if not m:
        return None
    y, mo, d, h, mi, sec = (int(x) if x else 0 for x in m.groups())
    try:
        return calendar.timegm((y, mo, d, h, mi, sec, 0, 0, 0))
    except (ValueError, OverflowError):
        return None


def union_s(spans):
    """The seconds covered by SPANS, [(a, b)], overlaps counted once."""
    total, end = 0, None
    for a, b in sorted(s for s in spans if s[1] > s[0]):
        if end is None or a > end:
            total += b - a
            end = b
        elif b > end:
            total += b - end
            end = b
    return total


def load(ledger_dir, hosts=None):
    """{host: [row]} of the mirror's HOST.jsonl files, each row with its joined wear bytes in
    row["_bytes"] (None: no figure): LEDGER/wear-HOST.json's, else the row's own wear."""
    out = {}
    for name in sorted(os.listdir(ledger_dir)):
        h = name[:-6]
        if not name.endswith(".jsonl") or "." in h or (hosts and h not in hosts):
            continue
        joined = {}
        try:
            with open(os.path.join(ledger_dir, f"wear-{h}.json"), encoding="utf-8") as f:
                joined = (json.load(f) or {}).get("rows") or {}
        except (OSError, ValueError):
            pass
        rows = []
        with open(os.path.join(ledger_dir, name), encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict):
                    continue
                w = (joined.get(r.get("id")) or {}).get("wear") or r.get("wear") or {}
                vals = [v for v in w.values() if isinstance(v, (int, float))] if isinstance(w, dict) else []
                r["_bytes"] = sum(vals) if vals else None
                rows.append(r)
        out[h] = rows
    return out


def chosen(rows, since=None, until=None, runs=None, families=None):
    out = []
    for r in rows:
        t = epoch(r.get("t"))
        if r.get("kind") not in (None, "run") or t is None:
            continue
        if (since is not None and t < since) or (until is not None and t > until):
            continue
        if families and r.get("family") not in families:
            continue
        run = str(r.get("run") or "")
        if runs and not any(run == x or run.startswith(x + "-r") for x in runs):
            continue
        out.append(r)
    return out


def span(r):
    t = epoch(r.get("t"))
    wall = r.get("wall_s") if isinstance(r.get("wall_s"), (int, float)) else 0
    a = epoch(r.get("start"))
    a = t - wall if a is None else a
    waits = r.get("waits_s") if isinstance(r.get("waits_s"), (int, float)) else 0
    return (a - waits, t)


def rate(rows):
    """One line's figures for ROWS."""
    walls = [r["wall_s"] for r in rows if isinstance(r.get("wall_s"), (int, float))]
    busy = union_s([span(r) for r in rows]) / 3600
    wrote = [r["_bytes"] for r in rows if r.get("_bytes") is not None]
    return {"cells": len(rows), "pass": sum(1 for r in rows if r.get("verdict") == "PASS"), "busy_h": busy,
            "per_h": len(rows) / busy if busy > 0 else None, "at_once": sum(walls) / 3600 / busy if busy > 0 else None,
            "wall_med_min": statistics.median(walls) / 60 if walls else None,
            "gb": sum(wrote) / 1e9 if wrote else None, "gb_cell": sum(wrote) / 1e9 / len(wrote) if wrote else None}


def ratios(rows, cut):
    """[(cell, median wall before CUT, median wall since)] of ROWS' cells with a PASS on both sides."""
    before, after = {}, {}
    for r in rows:
        if r.get("verdict") == "PASS" and isinstance(r.get("wall_s"), (int, float)) and r["wall_s"] > 0:
            (before if epoch(r.get("t")) < cut else after).setdefault(r.get("cell"), []).append(r["wall_s"])
    return [(c, statistics.median(before[c]), statistics.median(after[c])) for c in sorted(before) if c in after]


def where(r):
    """ram or ssd for a holt row that says where its scratch was (extra.peaks.mode), else None."""
    m = (((r.get("extra") or {}).get("peaks") or {}).get("mode")) if isinstance(r.get("extra"), dict) else None
    return {"ram": "ram", "disk": "ssd"}.get(m) if r.get("family") == "holt" else None


def ram_ssd(rows):
    """[(cell, median RAM wall, median SSD wall)] of ROWS' holt cells with a PASS each way."""
    w = {"ram": {}, "ssd": {}}
    for r in rows:
        if where(r) and r.get("verdict") == "PASS" and isinstance(r.get("wall_s"), (int, float)) and r["wall_s"] > 0:
            w[where(r)].setdefault(r.get("cell"), []).append(r["wall_s"])
    return [(c, statistics.median(w["ram"][c]), statistics.median(w["ssd"][c])) for c in sorted(w["ram"]) if c in w["ssd"]]


def fmt(x, spec):
    return "-" if x is None else format(x, spec)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Cells an hour and GB written per host, from Test2's ledger mirror.")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--run", default="")
    ap.add_argument("--family", default="holt,guest,stic")
    ap.add_argument("--hosts", default="")
    ap.add_argument("--cut")
    ap.add_argument("--mac-root", default=os.environ.get("T2_MAC_ROOT") or os.path.expanduser("~/backotter-work/test2"))
    a = ap.parse_args(argv)
    since, until, cut = (epoch(x) if x else None for x in (a.since, a.until, a.cut))
    if (a.since and since is None) or (a.until and until is None) or (a.cut and cut is None):
        print("host-rates: --since, --until and --cut take a UTC time, 2026-11-24T06:00:00Z, or a date", file=sys.stderr)
        return 2
    ledger_dir = os.path.join(a.mac_root, "ledger")
    if not os.path.isdir(ledger_dir):
        print(f"host-rates: no {ledger_dir} (t2 pull makes it)", file=sys.stderr)
        return 2
    fams = [f for f in a.family.split(",") if f]
    runs = [r for r in a.run.split(",") if r]
    by_host = {h: chosen(rows, since, until, runs, fams)
               for h, rows in load(ledger_dir, set(a.hosts.split(",")) - {""} or None).items()}
    sys.path.insert(0, HERE)
    try:
        from t2lib import hosts as table
        note = {h: f"{table.host_class(h) or '?'}, scratch_mode {table.limit(h, 'scratch_mode')} in the table now"
                for h in by_host}
    except Exception:   # noqa: BLE001 - the table only says the hosts' classes here
        note = {h: "?" for h in by_host}

    def show(title, pick):
        if title:
            print(title)
        print(f"{'host':<10}{'family':<9}{'cells':>5}{'PASS':>6}{'busy h':>8}{'cells/h':>9}{'at once':>9}{'wall med':>10}"
              f"{'GB':>9}{'GB/cell':>9}")
        for h, rows in by_host.items():
            rows = [r for r in rows if pick(r)]
            parts = [(f, [r for r in rows if r.get("family") == f]) for f in fams]
            holt = [r for r in rows if r.get("family") == "holt"]
            if {where(r) for r in holt} >= {"ram", "ssd"}:   # both ways here: a line each, after holt's own
                i = [f for f, _ in parts].index("holt") + 1 if "holt" in fams else len(parts)
                parts[i:i] = [("holt " + k, [r for r in holt if where(r) == k]) for k in ("ram", "ssd")]
            parts = [p for p in parts if p[1]]
            for i, (f, rs) in enumerate(parts + ([("all", rows)] if len({f.split()[0] for f, _ in parts}) > 1 else [])):
                x = rate(rs)
                print(f"{h:<10}{f:<9}{x['cells']:>5}{x['pass']:>6}{x['busy_h']:>8.2f}{fmt(x['per_h'], '.2f'):>9}"
                      f"{fmt(x['at_once'], '.2f'):>9}{fmt(x['wall_med_min'], '.1f'):>8} m{fmt(x['gb'], '.1f'):>9}"
                      f"{fmt(x['gb_cell'], '.2f'):>9}" + (f"   ({note[h]})" if i == 0 else ""))

    def pairs():
        print("\nholt cells that passed on a host both in the RAM disk and on the SSD (a cell's median wall each way):")
        for h, rows in by_host.items():
            rs = ram_ssd(rows)
            if rs:
                x = [b / a for _, a, b in rs]
                print(f"  {h:<10}{len(rs):>4} cells: the SSD wall is {statistics.median(x):.2f} x the RAM wall "
                      f"(the median of the ratios; {min(x):.2f} to {max(x):.2f})")
            elif any(where(r) == "ssd" for r in rows):
                print(f"  {h:<10}none yet (its SSD cells have no RAM pass in the rows chosen: a wider --since)")

    if cut is None:
        show("", lambda r: True)
        pairs()
        return 0
    show(f"before {a.cut}:", lambda r: epoch(r.get("t")) < cut)
    print()
    show(f"since {a.cut}:", lambda r: epoch(r.get("t")) >= cut)
    print(f"\nthe cells that passed on a host both before and since {a.cut} (a cell's median wall each side):")
    for h, rows in by_host.items():
        rs = ratios(rows, cut)
        if not rs:
            print(f"  {h:<10}none")
            continue
        x = [aft / bef for _, bef, aft in rs]
        print(f"  {h:<10}{len(rs):>4} cells: the wall since is {statistics.median(x):.2f} x the wall before "
              f"(the median of the ratios; {min(x):.2f} to {max(x):.2f})   ({note[h]})")
    pairs()
    return 0


if __name__ == "__main__":
    sys.exit(main())
