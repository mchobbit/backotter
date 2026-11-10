"""What Test2 prints: the plan form (contract 1.7) and a run's report (1.9, R22, R27).

render_plan(plan)          the plan form: selection, reasons, estimates (one line when nothing is due)
render_report(rr, plan, ledger, policy, now)
                           the report: a line per cell, stops, the rows written, budget warnings,
                           the sweep line, and last the wear line per host and disk (no serials)
"""

import time

from . import parse_utc
from . import words

NOUN = {"stic": "checks", "holt": "cells", "gate": "layers"}
FAMILY_ALL = {"stic": "SticTest2All", "holt": "HoltTest2All", "gate": "GateTest2All"}
GB = 1e9


def fmt_min(s):
    s = int(s or 0)
    if s < 90:
        return f"~{s} s"
    if s < 5400:
        return f"~{round(s / 60)} min"
    return f"~{s / 3600:.1f} h"


def gb(x):
    return f"{x:.1f}" if x is not None else "?"


def cut(text, n):
    return text if len(text) <= n else text[:n - 2] + ".."


def grouped(cells, key):
    """[(key, [cells])] in first-seen order."""
    out = {}
    for c in cells:
        out.setdefault(key(c), []).append(c)
    return list(out.items())


def names(cells, n=8):
    cs = [c.cell for c in cells]
    return ", ".join(cs[:n]) + (f" and {len(cs) - n} more" if len(cs) > n else "")


def hhmm(t):
    e = t if isinstance(t, (int, float)) else parse_utc(t)
    return time.strftime("%H:%MZ", time.gmtime(e)) if e else "?"


# --- the plan form -------------------------------------------------------------------

def plan_header(plan):
    w, h, s = plan.word, plan.header, plan.set or {}
    parts = []
    if plan.family == "holt":
        code = (s.get("holt_code") or "")[:7]
        built = s.get("holt_built")
        parts.append(f"bko-holt code {code} ({'built on ' + ', '.join(built) if built else 'not built'}), "
                     f"rustic-bko {s.get('name', 'bko-bin')}")
    elif s.get("round"):
        parts.append(f"round {s['round']} ({s.get('dir')})" + (", not built" if s.get("pending") else ""))
    elif s.get("name"):
        parts.append(f"set {s['name']}" + (f" ({s['why']})" if s.get("why") else ""))
    base = f"base {h.get('base')}"
    if h.get("uncommitted"):
        base += f" + {h['uncommitted']} uncommitted path{'s' if h['uncommitted'] != 1 else ''}"
    parts.append(base)
    pulled = [f"{x}" for x in (h.get("pulled") or {}).values() if x]
    pt = [hhmm(p.get("t")) for p in (h.get("pulled") or {}).values() if isinstance(p, dict) and p.get("t")]
    parts.append(f"ledger {h.get('ledger_rows', 0)} rows" + (f" (pulled {max(pt)})" if pt else "")
                 + ("" if pulled else " (not pulled)"))
    facts = h.get("facts") or {}
    fx = " ".join(f"{k} {hhmm(t)}" for k, t in facts.items())
    for u in h.get("unreachable") or []:
        fx += f" {u} unreachable"
    parts.append("facts " + (fx.strip() or "none"))
    return f"{w.word} plan: " + ", ".join(parts)


def nothing_due_line(plan):
    c = plan.totals.get("counts", {})
    bits = [f"{c[s]} {s}" for s in ("current", "canary-verified", "awaiting-canaries", "soft-stale", "carried",
                                     "class-pending", "not-runnable") if c.get(s)]
    s = plan.set or {}
    where = []
    if s.get("round"):
        where.append(f"round {s['round']}")
    elif plan.family == "holt" and s.get("holt_code"):
        where.append(f"bko-holt code {s['holt_code'][:7]}")
    elif s.get("name"):
        where.append(f"set {s['name']}")
    where.append(f"base {plan.header.get('base')}")
    return f"{plan.word.word}: nothing due ({', '.join(bits) or 'no cells'}); {', '.join(where)}"


def render_plan(plan):
    t = plan.totals
    if not t.get("due"):
        lines = [nothing_due_line(plan)]
        nr = plan.by_status("not-runnable")
        if nr:
            why = {}
            for c in nr:
                why.setdefault("; ".join(c.reasons) or "no host can run it", []).append(c.cell)
            for w_, cs in sorted(why.items(), key=lambda x: -len(x[1]))[:3]:
                lines.append(f"not runnable: {', '.join(cs[:6])}{' ...' if len(cs) > 6 else ''} ({cut(w_, 120)})")
        for n in plan.notes:
            if n.startswith(("verdict changed", "the fork heads' round", "testvm/test2/", "no round", "inputs differ")) \
                    or "unreachable" in n:
                lines.append("note: " + n)
        return "\n".join(lines) + "\n"
    out = [plan_header(plan)]
    noun = NOUN.get(plan.family, "cells")
    gbh = t.get("gb") or {}
    out.append(f"due: {t['due']} of {t.get('cells', len(plan.cells))} {noun}, est. {t['gm']:.1f} gm, "
               f"{fmt_min(t['wall_s'])} wall, {sum(gbh.values()):.1f} GB"
               + (" (" + ", ".join(f"{h} {x:.1f}" for h, x in gbh.items()) + ")" if gbh else ""))
    due = plan.by_status("due")
    w1 = max([len(c.cell) for c in due] + [10])
    w3 = max([len(c.rule or "") for c in due] + [8])
    for c in due:
        why = "; ".join(c.reasons) or "-"
        run = f"  [running {c.running}]" if c.running else ""
        out.append(f"  {c.cell:<{w1}}  {c.host:<8} {c.rule or '-':<{w3}}  {cut(why, 110)}  "
                   f"{c.est.get('gm', 0):.1f} gm  {c.est.get('src', '')}{run}")
    pend = plan.by_status("class-pending")
    by_rep = {}
    for c in pend:
        by_rep.setdefault((c.klass, c.rep), []).append(c.cell)
    for (k, rep), cs in sorted(by_rep.items(), key=lambda x: str(x[0])):
        out.append(f"class-pending: {', '.join(cs)} ({k}, covered if {rep} passes)")
    car = plan.by_status("carried")
    for (rule, why), cs in grouped(car, lambda c: (c.rule, cut("; ".join(c.reasons), 90))):
        out.append(f"carried: {names(cs)} ({rule}: {why})")
    aw = plan.by_status("awaiting-canaries")
    if aw:
        out.append(f"awaiting canaries: {len(aw)} ({', '.join(c.cell for c in aw[:8])}"
                   + (" ..." if len(aw) > 8 else "") + f"): {aw[0].reasons[-1] if aw[0].reasons else ''}")
    cv = plan.by_status("canary-verified")
    soft = plan.by_status("soft-stale")
    beside = [c.cell for c in due if c.rule == "stale:beside"]
    if soft or beside:
        parts = [f"{names(cs, 4)}: {why}" for (why,), cs in grouped(soft, lambda c: (cut(c.reasons[0] if c.reasons else "", 70),))]
        out.append(f"soft-stale: {len(soft)}" + (" (" + "; ".join(parts) + ")" if parts else "")
                   + (f"; beside this run: {', '.join(beside)}" if beside else ""))
    nr = plan.by_status("not-runnable")
    tail = f"current: {len(plan.by_status('current'))}"
    if cv:
        tail += f"   canary-verified: {len(cv)}"
    if nr:
        tail += "   not runnable: " + "; ".join(f"{c.cell} ({cut('; '.join(c.reasons), 70)})" for c in nr[:6]) \
                + (f"; +{len(nr) - 6} more" if len(nr) > 6 else "")
    out.append(tail)
    if plan.unmapped:
        out.append("unmapped: name them in a spec or in policy.unmapped_ignore: " + ", ".join(plan.unmapped[:20]))
    for n in plan.notes:
        out.append("note: " + n)
    if plan.word.plan:
        out.append("plan only: nothing started, nothing written")
    return "\n".join(out) + "\n"


# --- the run report ----------------------------------------------------------------------

def sweep_line(family, ledger, policy, now):
    """R27: "sweep: FAMILY All last ran DATE (N days ago), due in M days"."""
    sw = (policy or {}).get("sweep") or {}
    days = sw.get("days", (policy or {}).get("sweep_days", 14))
    shadow = sw.get("shadow_until")
    if shadow and parse_utc(shadow + "T00:00:00Z" if len(shadow) == 10 else shadow) and \
            now > parse_utc(shadow + "T00:00:00Z" if len(shadow) == 10 else shadow):
        days = sw.get("then_days", days)
    last = None
    for r in ledger or ():
        if r.get("family") == family and r.get("rule") == "fresh:All" and r.get("kind") == "run":
            t = parse_utc(r.get("t"))
            if t and (last is None or t > last):
                last = t
    name = FAMILY_ALL.get(family, family)
    if last is None:
        return f"sweep: {name} never ran on the new write path, due now"
    ago = int((now - last) // 86400)
    return (f"sweep: {name} last ran {time.strftime('%Y-%m-%d', time.gmtime(last))} ({ago} days ago), "
            f"due in {max(days - ago, 0)} days")


def wear_lines(wear):
    """wear: {host: {disk: {model, window_bytes, ours_bytes, today_bytes}}} (no serials)."""
    out = []
    for h in sorted(wear or {}):
        for d in sorted(wear[h] or {}):
            x = wear[h][d] or {}

            def g(k):
                v = x.get(k)
                return f"{v / GB:.2f} GB" if isinstance(v, (int, float)) else "?"
            model = f" ({x['model']})" if x.get("model") else ""
            out.append(f"{h} {d}{model}: run window {g('window_bytes')} (diskstats, all writers), our jobs "
                       f"{g('ours_bytes')} (wear tags), today {g('today_bytes')} (SMART)")
    return out or ["wear: no disk figures (facts or wear logs unreachable)"]


def budget_binds(bind_from, now=None):
    """policy.budget.bind_from: null warns only; a date (YYYY-MM-DD or a UTC time) binds from then
    on ("warn for two weeks, then bind")."""
    if not bind_from:
        return False
    t = parse_utc(bind_from + "T00:00:00Z" if len(str(bind_from)) == 10 else bind_from)
    return bool(t) and (now or time.time()) >= t


def run_bytes(rr):
    """{host: GB} this run's jobs wrote: the cells' gb when a runner gives it, else the wear
    tags' sums (rr["wear"][HOST][DISK]["ours_bytes"], R22)."""
    per = {}
    for c in rr.get("cells", ()):
        if c.get("host") and c.get("gb") is not None:
            per[c["host"]] = per.get(c["host"], 0.0) + float(c["gb"] or 0)
    for h, disks in (rr.get("wear") or {}).items():
        if h in per:
            continue
        vals = [d.get("ours_bytes") for d in (disks or {}).values() if isinstance(d, dict)]
        vals = [v for v in vals if isinstance(v, (int, float))]
        if vals:
            per[h] = sum(vals) / GB
    return per


def budget_lines(rr, policy, sweep=False, ledger=(), now=None):
    """Budget warnings (1.9, R22): this run's jobs per host (their wear tags), and the day's
    Test2 jobs per host (the ledger's rows of today, other runs', plus this run's).  Other
    sessions' writes count in neither: SMART's day figure is on the wear line, never acted on."""
    b = (policy or {}).get("budget") or {}
    run_gb = b.get("sweep_gb" if sweep else "run_gb", 2)
    day_gb = b.get("day_gb", 50)
    bind = b.get("bind_from")
    mode = ("binds" if budget_binds(bind, now) else f"warn only until {bind}") if bind else \
        "warn only (bind_from not set)"
    out = []
    per = run_bytes(rr)
    for h, x in sorted(per.items()):
        if x > run_gb:
            out.append(f"budget: {h} wrote {x:.2f} GB in this run's jobs, over {run_gb} GB ({mode})")
    day = time.strftime("%Y-%m-%d", time.gmtime(now or time.time()))
    daily = dict(per)
    mine = {rr.get("run"), f"{rr.get('run')}-r2"}
    for r in ledger or ():
        if not str(r.get("t") or "").startswith(day) or not str(r.get("run") or "").startswith("t2"):
            continue
        if r.get("run") in mine:
            continue   # this run's: in PER, from its wear tags
        vals = [v for v in (r.get("wear") or {}).values() if isinstance(v, (int, float))]
        if vals and r.get("host"):
            daily[r["host"]] = daily.get(r["host"], 0.0) + sum(vals) / GB
    for h, x in sorted(daily.items()):
        if x > day_gb:
            out.append(f"budget: {h}: Test2's jobs wrote {x:.1f} GB today, over {day_gb} GB a day ({mode})")
    return out


def render_report(rr, plan, ledger=(), policy=None, now=None):
    """rr: a runner's RunResult as a dict (contract 1.9's runner API)."""
    now = now or time.time()
    h = plan.header if plan else {}
    s = (plan.set if plan else None) or {}
    hosts = rr.get("hosts") or (sorted({c.get("host") for c in rr.get("cells", ()) if c.get("host")}))
    head = (f"{rr.get('word') or (plan.word.word if plan else '?')} report: run {rr.get('run')}, "
            f"word \"{plan.word.text if plan else rr.get('word')}\", base {h.get('base', '?')}"
            + (f" + {h['uncommitted']} uncommitted" if h.get("uncommitted") else "")
            + f", copy {rr.get('copy') or h.get('copy') or '?'}"
            + (f", round {s['round']}" if s.get("round") else "")
            + (f", bko-holt code {s['holt_code'][:7]}" if s.get("holt_code") else "")
            + f", hosts {' '.join(hosts) or '-'}")
    out = [head]
    seen = set()
    cells = rr.get("cells") or []
    by_id = {r.get("id"): r for r in ledger or ()}
    wc = max([len(c.get("cell", "")) for c in cells] + [len(pc.cell) for pc in (plan.cells if plan else ())] + [10])
    for c in cells:
        seen.add(c.get("cell"))
        rows = c.get("rows") or []
        rid = rows[-1] if rows else "-"
        if isinstance(rid, dict):
            rid = rid.get("id", "-")
        gbs = c.get("gb")
        if gbs is None:     # the wear pulled in for this cell's rows
            tags = {t: v for i in rows for t, v in ((by_id.get(i.get("id") if isinstance(i, dict) else i) or {}).get("wear") or {}).items()
                    if isinstance(v, (int, float))}
            gbs = sum(tags.values()) / GB if tags else None
        out.append(f"  {c.get('outcome', 'ran'):<8} {c.get('cell', ''):<{wc}} {c.get('verdict') or '-':<10} "
                   f"{c.get('rule') or '-':<16} {cut('; '.join(c.get('reasons') or []), 70) or '-'}  "
                   f"{float(c.get('gm') or 0):.1f} gm  {'-' if gbs is None else f'{float(gbs):.2f}'} GB  "
                   f"{c.get('host') or '-'}  {rid}")
    if plan:
        for pc in plan.cells:
            if pc.cell in seen:
                continue
            why = pc.rule or pc.status
            out.append(f"  {'skipped':<8} {pc.cell:<{wc}} {'-':<10} {why:<16} "
                       f"{cut(pc.status + (': ' + '; '.join(pc.reasons) if pc.reasons else ''), 70)}")
    stops = rr.get("stops") or []
    out.append("stops: " + ("; ".join(stops) if stops else "none"))
    rows = [r for c in cells for r in (c.get("rows") or [])] + list(rr.get("derived") or [])
    by = {}
    for c in cells:
        for _ in c.get("rows") or []:
            by[c.get("host") or "?"] = by.get(c.get("host") or "?", 0) + 1
    if rr.get("derived"):
        by["mac"] = by.get("mac", 0) + len(rr["derived"])
    out.append(f"rows: {len(rows)} written" + (" (" + ", ".join(f"{k} {v}" for k, v in sorted(by.items())) + ")"
                                               if by else ""))
    for n in (plan.notes if plan else ()):
        if n.startswith(("due after", "no longer due", "dropped after", "verdict changed", "debian13+oneshot")):
            out.append("note: " + n)
    for n in rr.get("notes") or ():
        out.append("note: " + str(n))
    fam = rr.get("family") or (plan.family if plan else None)
    sweep = bool(plan and getattr(plan.word, "sweep", False))   # SweepTest2's budget only
    out += budget_lines(rr, policy, sweep, ledger, now)
    if fam:
        out.append(sweep_line(fam, ledger, policy, now))
    out += wear_lines(rr.get("wear"))
    return "\n".join(out) + "\n"


def word_of(plan):
    return plan.word.word if plan else words.FAMILY_WORD.get("stic")
