"""t2 batch's output, the pure part (docs/design/batch.md §5.2, §7; piece B5): summary.tsv's rows
from the ledger and each entry's own sources, the diff against the previous batch (`since`), the
grouped report and ANALYSE.md.  No I/O: t2lib/batchout.py reads the files and writes the results.

summary_rows(entries, ledger_rows) -> [row]: one row per cell, plus one per entry with no cell
(cell "-").  ENTRIES are plain dicts (batchout.entry_facts builds them):
    id, kind, status, exit         the entry, and how it ended (state.json)
    match                          a regex (fullmatch) the ledger rows' `run` field takes
    plan                           [{cell, status, reasons}]: the cells its run(s) planned
                                   (status due, to-class; the rest are ignored)
    tool                           [{cell, verdict, host, wall_s, reason, log, attempt}]: the
                                   tool's own record (ht1's done rows, drive.py's results.tsv)
                                   for a cell that has no ledger row
    log                            the entry's own log (entries/ID/entry.log), a cmd's verdict
A cell's verdict comes from its decisive ledger row (as the run itself judges it, bko's
decisive_cells: the newest valid PASS, FAIL or FLAKY-FAIL row of kind run or carried; only
when there is none, the newest run row, a HARNESS or LOST one, or a PASS that doesn't count,
INVALID), re-classed by a later kind-class row of that cell; else from the tool's record; else a
planned cell with no row is NOT-RUN when a run-wide stop rule fired first (the entry's
`stops`), LOST (class lost) when the entry ran to its end, NOT-RUN when it did not, and a
to-class cell is TO-CLASS (class to-class).

diff(rows, prev) sets `since`: new, known (the baseline had the cell failing with the same
reason head, its first HEAD_N characters), fixed (the baseline failed, this passes) or same.
"""

import json
import re

HEADER = ("entry", "cell", "verdict", "class", "since", "host", "attempt", "gm", "wall_s", "run", "row", "log",
          "reason")
REASON_MAX = 160
HEAD_N = 60
REPORT_MAX = 120          # §7.1: one screen
GROUPS_MAX = 30           # groups printed in the report (the rest counted)
LOG_GROUPS = 10           # §7.1 step 2: at most this many logs read
RUN_KINDS = ("run",)
DECISIVE = ("PASS", "FAIL", "FLAKY-FAIL")  # bko.DECISIVE: what decides a cell (HARNESS and LOST never do)
ENDED = ("PASS", "FAIL", "UNKNOWN")        # an entry that ran to its end: a cell left with no row is LOST
FAILING_CLASSES = ("fail", "harness", "lost", "to-class")
SINCE = ("new", "known", "fixed", "same")


def clean(s):
    """One line, no tabs: S's whitespace runs as single spaces."""
    return re.sub(r"\s+", " ", str(s if s is not None else "")).strip()


def cut(s, n=REASON_MAX):
    s = clean(s)
    return s if len(s) <= n else s[:n - 1] + "…"


def head(reason):
    """The reason head the groups and `known` compare: its first HEAD_N characters."""
    return clean(reason)[:HEAD_N]


def passing(row):
    return row.get("verdict") == "PASS"


def _num(v, fmt=None):
    if v is None or v == "":
        return "-"
    if fmt and isinstance(v, (int, float)):
        return fmt.format(v)
    return clean(v)


def _row_reason(r):
    """A ledger row's reason: its first fails line; for a PASS that doesn't count, why not."""
    fails = r.get("fails") or []
    if fails and r.get("verdict") != "PASS":
        return cut(fails[0])
    if r.get("valid") is False:
        why = (r.get("reasons") or [""])[0] if r.get("reasons") else ""
        return cut("invalid row" + (f": {why}" if why else ""))
    if r.get("verdict") == "PASS" and r.get("committed") is False:
        return "uncommitted: counts once the same diff is committed"
    if fails:
        return cut(fails[0])
    return "" if r.get("verdict") == "PASS" else cut(r.get("note") or "")


def _decisive(rows):
    """The decisive row of one cell's rows, re-classed by a later class row: the newest valid
    PASS, FAIL or FLAKY-FAIL row of kind run or carried (bko.decisive_cells, run.outcome_exit);
    only when there is none, the newest run row (HARNESS, LOST, or an invalid one: a PASS that
    doesn't count shows as INVALID, class -)."""
    newest = (lambda r: (r.get("t") or "", r.get("id") or ""))   # noqa: E731
    dec = [r for r in rows if r.get("kind") in RUN_KINDS + ("carried",) and r.get("verdict") in DECISIVE
           and r.get("valid") is True]
    runs = [r for r in rows if r.get("kind") in RUN_KINDS]
    if dec:
        d = dict(max(dec, key=newest))
    elif runs:
        d = dict(max(runs, key=newest))
        if d.get("verdict") == "PASS" and d.get("valid") is not True:
            d["verdict"], d["class"] = "INVALID", "-"
    else:
        return None
    cls = [r for r in rows if r.get("kind") == "class" and (r.get("t") or "") >= (d.get("t") or "")]
    if cls:
        cls.sort(key=lambda r: (r.get("t") or "", r.get("id") or ""))
        c = cls[-1]
        d["verdict"], d["class"] = c.get("verdict") or d.get("verdict"), c.get("class") or d.get("class")
        d["id"] = c.get("id") or d.get("id")
        if c.get("fails"):
            d["fails"] = c["fails"]
    return d


def _out(entry, cell, verdict, klass, host="-", attempt="-", gm="-", wall="-", run="-", row="-", log="-",
         reason=""):
    return {"entry": entry, "cell": cell, "verdict": verdict, "class": klass, "since": "same", "host": host or "-",
            "attempt": attempt, "gm": gm, "wall_s": wall, "run": run or "-", "row": row or "-", "log": log or "-",
            "reason": cut(reason)}


def tool_class(verdict):
    return {"PASS": "pass", "FAIL": "fail", "FLAKY-FAIL": "fail", "HARNESS": "harness", "LOST": "lost"}.get(verdict, "-")


def entry_rows(e, ledger_rows):
    """The summary rows of one entry (see the module's docstring)."""
    eid, kind = e["id"], e.get("kind")
    status = e.get("status") or "-"
    if kind == "cmd":
        v = status if status != "PASS" else "PASS"
        return [_out(eid, "-", v, "cmd", "mac", e.get("attempt") or 1, "-", _num(e.get("wall_s"), "{:.0f}"),
                     log=e.get("log"), reason="" if v == "PASS" else f"exit {e.get('exit')}")]
    pat = re.compile(e["match"]) if e.get("match") else None
    by = {}
    if pat is not None:
        for r in ledger_rows:
            if r.get("cell") and r.get("kind") in ("run", "carried", "class") and pat.fullmatch(r.get("run") or ""):
                by.setdefault(r["cell"], []).append(r)
    tools = {t["cell"]: t for t in e.get("tool") or [] if t.get("cell")}
    order, seen = [], set()
    for c in [p["cell"] for p in e.get("plan") or [] if p.get("status") in ("due", "to-class")] + \
            sorted(by) + [t["cell"] for t in e.get("tool") or [] if t.get("cell")]:
        if c not in seen:
            seen.add(c)
            order.append(c)
    plan = {p["cell"]: p for p in e.get("plan") or []}
    out = []
    for c in order:
        d = _decisive(by.get(c, []))
        if d is not None:
            out.append(_out(eid, c, d.get("verdict") or "-", d.get("class") or "-", d.get("host"),
                            d.get("attempt") if d.get("attempt") is not None else "-",
                            _num(d.get("gm"), "{:.1f}"), _num(d.get("wall_s"), "{:.0f}"), d.get("run"), d.get("id"),
                            "-", _row_reason(d)))
            out[-1]["_logs"] = d.get("logs")
            continue
        t = tools.get(c)
        if t is not None:
            v = t.get("verdict") or "FAIL"
            out.append(_out(eid, c, v, t.get("class") or tool_class(v), t.get("host"), t.get("attempt") or 1, "-",
                            _num(t.get("wall_s"), "{:.0f}"), e.get("run"), "-", "-", t.get("reason") or ""))
            out[-1]["_logs"] = t.get("log")
            continue
        p = plan.get(c) or {}
        if p.get("status") == "to-class":
            out.append(_out(eid, c, "TO-CLASS", "to-class", run=e.get("run"),
                            reason="; ".join(p.get("reasons") or []) or "a new log line to class by hand"))
        elif e.get("stops"):
            out.append(_out(eid, c, "NOT-RUN", "-", run=e.get("run"), reason=f"stopped: {e['stops'][0]}"))
        elif status in ENDED:
            out.append(_out(eid, c, "LOST", "lost", run=e.get("run"),
                            reason="planned, and no row: its host went away or the run never reached it"))
        else:
            out.append(_out(eid, c, "NOT-RUN", "-", run=e.get("run"), reason=f"the entry ended {status} first"))
    if not out:
        v = status if status != "PASS" else "PASS"
        out.append(_out(eid, "-", v, "-", run=e.get("run"), log=e.get("log"),
                        reason="" if v == "PASS" else (e.get("note") or f"the entry ended {status}")))
    return out


def summary_rows(entries, ledger_rows):
    out = []
    for e in entries:
        out += entry_rows(e, ledger_rows)
    return out


# --- since --------------------------------------------------------------------------------------

def failing(row):
    """A baseline row that failed: a failing class (a NOT-RUN, INVALID or SKIPPED cell did not
    fail), or a cmd entry that FAILed."""
    return row.get("class") in FAILING_CLASSES or (row.get("class") == "cmd" and row.get("verdict") == "FAIL")


def diff(rows, prev):
    """ROWS with `since` set against PREV (the baseline's summary rows; None or []: no baseline,
    every failing cell new).  A cell is matched by name, the same entry's row first; the host
    doesn't matter (a cell that moved host is still the same cell).  Returns ROWS."""
    idx = {}
    for p in prev or []:
        if p.get("cell") and p.get("cell") != "-":
            idx.setdefault(p["cell"], []).append(p)
        else:
            idx.setdefault(("entry", p.get("entry")), []).append(p)
    for r in rows:
        key = r["cell"] if r.get("cell") and r["cell"] != "-" else ("entry", r.get("entry"))
        cands = idx.get(key) or []
        p = next((x for x in cands if x.get("entry") == r.get("entry")), cands[0] if cands else None)
        if passing(r):
            r["since"] = "fixed" if p is not None and failing(p) else "same"
        elif p is not None and failing(p) and head(p.get("reason")) == head(r.get("reason")):
            r["since"] = "known"
        else:
            r["since"] = "new"
    return rows


def fixed_or_gone(rows, prev):
    """Baseline cells that failed and are not in ROWS at all (not run this time): [prev row]."""
    have = {r["cell"] for r in rows}
    return [p for p in prev or [] if p.get("cell") not in have and p.get("cell") != "-" and failing(p)]


# --- summary.tsv -----------------------------------------------------------------------------------

def to_tsv(rows):
    lines = ["#" + "\t".join(HEADER)]
    for r in rows:
        vals = [clean(r.get(k)) for k in HEADER]
        lines.append("\t".join(v if v != "" or k == "reason" else "-" for k, v in zip(HEADER, vals)))
    return "\n".join(lines) + "\n"


def parse_tsv(text):
    """summary.tsv's rows ([dict]); a line of the wrong width is skipped; a file without a reason
    column (an older one) reads with reason ''."""
    rows, cols = [], list(HEADER)
    for ln in (text or "").splitlines():
        if not ln.strip():
            continue
        if ln.startswith("#"):
            cols = ln[1:].split("\t")
            continue
        f = ln.split("\t")
        if len(f) != len(cols):
            continue
        r = dict(zip(cols, f))
        r.setdefault("reason", "")
        rows.append(r)
    return rows


# --- groups and the report ----------------------------------------------------------------------------

def groups(rows):
    """The failing cells grouped by (class, reason head), biggest first, then new before known:
    [{class, head, count, cells, hosts, since, entries, rep}] (rep: the representative cell, the
    first new one)."""
    g = {}
    for r in rows:
        if passing(r):
            continue
        k = (r.get("class") or "-", head(r.get("reason")))
        x = g.setdefault(k, {"class": k[0], "head": k[1], "count": 0, "cells": [], "hosts": [], "since": [],
                             "entries": [], "rows": [], "rep": None})
        x["count"] += 1
        x["cells"].append(r["cell"])
        if r.get("host") not in (None, "-") and r["host"] not in x["hosts"]:
            x["hosts"].append(r["host"])
        if r.get("since") not in x["since"]:
            x["since"].append(r.get("since"))
        if r["entry"] not in x["entries"]:
            x["entries"].append(r["entry"])
        if r.get("row") not in (None, "-"):
            x["rows"].append(r["row"])
        if x["rep"] is None or (r.get("since") == "new" and x["rep"].get("since") != "new"):
            x["rep"] = {"entry": r["entry"], "cell": r["cell"], "since": r.get("since"), "log": r.get("log")}
    order = {"fail": 0, "to-class": 1, "harness": 2, "lost": 3}
    return sorted(g.values(), key=lambda x: (order.get(x["class"], 4), "new" not in x["since"], -x["count"],
                                             x["head"]))


def counts(rows, key):
    c = {}
    for r in rows:
        c[r.get(key) or "-"] = c.get(r.get(key) or "-", 0) + 1
    return c


def _counts_line(c, order=()):
    ks = [k for k in order if k in c] + sorted(k for k in c if k not in order)
    return "  ".join(f"{k} {c[k]}" for k in ks) or "none"


def fmt_hm(s):
    if s is None:
        return "-"
    s = int(max(0, s))
    return f"{s // 3600}:{s // 60 % 60:02d}"


def _names(xs, n=6):
    xs = list(xs)
    return ", ".join(xs[:n]) + (f" and {len(xs) - n} more" if len(xs) > n else "")


def report(meta, entries, rows, prev_id=None, gone=()):
    """The report as data (what --json prints and render() lays out).  META: {batch, file, hosts,
    started, ended, exit, manual}; ENTRIES: [{id, status, exit, wall_s, run, note}]."""
    gs = groups(rows)
    per = {}
    for r in rows:
        x = per.setdefault(r["entry"], {})
        if r.get("cell") != "-":
            x[r["verdict"]] = x.get(r["verdict"], 0) + 1
    since = counts([r for r in rows if r.get("cell") != "-" or r.get("verdict") != "PASS"], "since")
    still = sum(1 for r in rows if r.get("since") == "known")
    return {"meta": dict(meta), "prev": prev_id,
            "entries": [dict(e, cells=per.get(e["id"], {})) for e in entries],
            "verdicts": counts(rows, "verdict"), "classes": counts(rows, "class"),
            "groups": [dict(g, rows=g["rows"][:5]) for g in gs],
            "diff": {"new": since.get("new", 0), "fixed": since.get("fixed", 0), "known": still,
                     "gone": len(list(gone))},
            "not_finished": [e["id"] for e in entries if e.get("status") not in ("PASS", "FAIL")]}


def render(rep, cap=REPORT_MAX, groups_cap=GROUPS_MAX):
    """§7.1 step 1: one screen, at most CAP lines."""
    m = rep["meta"]
    L = [f"batch {m.get('batch')}  file {m.get('file')}  hosts {' '.join(m.get('hosts') or [])}",
         f"started {m.get('started') or '-'}  ended {m.get('ended') or '-'}  wall {fmt_hm(m.get('wall_s'))}  "
         f"exit {m.get('exit') if m.get('exit') is not None else '-'}  prev {rep.get('prev') or 'none'}"]
    L.append(f"{'entry':<40} {'status':<10} {'exit':>4} {'wall':>6}  cells")
    for e in rep["entries"]:
        cells = _counts_line(e.get("cells") or {}, ("PASS", "FAIL", "FLAKY-FAIL", "HARNESS", "LOST"))
        L.append(f"{e['id'][:40]:<40} {e.get('status') or '-':<10} {str(e.get('exit') if e.get('exit') is not None else '-'):>4} "
                 f"{fmt_hm(e.get('wall_s')):>6}  {cells if e.get('cells') else '-'}"
                 + (f"  ({cut(e['note'], 70)})" if e.get("note") else ""))
    L.append("verdicts  " + _counts_line(rep["verdicts"], ("PASS", "FAIL", "FLAKY-FAIL", "HARNESS", "LOST")))
    L.append("classes   " + _counts_line(rep["classes"], ("pass", "fail", "harness", "lost", "known-flake")))
    for x in m.get("stops") or []:
        L.append(f"stop      {cut(x, 110)}")
    for eid in rep.get("not_finished") or []:
        e = next(x for x in rep["entries"] if x["id"] == eid)
        L.append(f"unfinished {eid}: {e.get('status')}" + (f" ({cut(e['note'], 80)})" if e.get("note") else ""))
    d = rep["diff"]
    L.append(f"diff vs {rep.get('prev') or 'nothing (no baseline: every failing cell is new)'}: "
             f"new {d['new']}, fixed {d['fixed']}, still failing (known) {d['known']}"
             + (f", failing then and not run now {d['gone']}" if d.get("gone") else ""))
    gs = rep["groups"]
    L.append(f"failing groups: {len(gs)} (by class and the first {HEAD_N} characters of the reason)")
    tail = []
    for m_ in m.get("manual") or []:
        tail.append(f"manual (not run): {cut(m_, 100)}")
    shown = 0
    for g in gs:
        if shown >= groups_cap or len(L) + 2 + len(tail) >= cap:
            break
        since = "/".join(s for s in SINCE if s in g["since"])
        L.append(f"  {g['class']:<8} {g['count']:>4} {since:<10} {g['head'] or '(no reason)'}")
        L.append(f"           cells {_names(g['cells'], 5)}; hosts {','.join(g['hosts']) or '-'}"
                 + (f"; rows {','.join(g['rows'][:2])}" if g.get("rows") else ""))
        shown += 1
    if shown < len(gs):
        L.append(f"  ... and {len(gs) - shown} more groups ({sum(g['count'] for g in gs[shown:])} cells): "
                 "awk -F'\\t' '$3 != \"PASS\"' summary.tsv")
    L += tail
    if len(L) > cap:
        L = L[:cap - 1] + [f"... cut at {cap} lines: t2 batch report BATCH --json, or summary.tsv"]
    return "\n".join(L) + "\n"


# --- ANALYSE.md --------------------------------------------------------------------------------------

def render_analyse(rep, procedure, bid):
    """ANALYSE.md: this batch's numbers first, then the procedure (BATCH.md's "Analysing a batch",
    PROCEDURE), with the batch id filled in for each <BATCH> and the logs to read named."""
    gs = rep["groups"]
    new_fail = [g for g in gs if g["class"] == "fail" and "new" in g["since"]]
    farm = [g for g in gs if g["class"] in ("harness", "lost")]
    L = [f"# Analysing batch {bid}", "",
         f"Read this file first; then `t2 batch report {bid}` (one screen).  The procedure is below, "
         "and these are this batch's own numbers.", "",
         f"- file `{rep['meta'].get('file')}`, hosts {' '.join(rep['meta'].get('hosts') or [])}, "
         f"exit {rep['meta'].get('exit')}, wall {fmt_hm(rep['meta'].get('wall_s'))}, baseline {rep.get('prev') or 'none'}",
         "- verdicts: " + _counts_line(rep["verdicts"], ("PASS", "FAIL", "FLAKY-FAIL", "HARNESS", "LOST")),
         "- classes: " + _counts_line(rep["classes"], ("pass", "fail", "harness", "lost", "known-flake")),
         f"- diff: new {rep['diff']['new']}, fixed {rep['diff']['fixed']}, known {rep['diff']['known']}",
         f"- failing groups: {len(gs)}; new `fail` groups: {len(new_fail)}; farm (`harness`, `lost`) groups: {len(farm)}"]
    if rep.get("not_finished"):
        L.append("- entries that did not finish: " + ", ".join(f"`{x}`" for x in rep["not_finished"]))
    L += ["", "## The logs to read (step 2)", ""]
    if not new_fail:
        L.append("None: no new `fail` group.  Steps 3 and 4 still apply.")
    for g in new_fail[:LOG_GROUPS]:
        rep_ = g.get("rep") or {}
        L.append(f"- `t2 batch log {bid} {rep_.get('entry')}:{rep_.get('cell')} --tail 200`: "
                 f"{g['count']} cell(s), {cut(g['head'], 60) or '(no reason)'}")
    if len(new_fail) > LOG_GROUPS:
        L.append(f"- and {len(new_fail) - LOG_GROUPS} more new `fail` groups: report the biggest ten, say there are more")
    # §7.1 step 3: a farm group's log only when three or more cells share its reason head, or
    # when the farm cells outnumber the fail cells; §7.2: the ten logs are shared with step 2's.
    fail_cells = sum(g["count"] for g in gs if g["class"] == "fail")
    farm_cells = sum(g["count"] for g in farm)
    farm_logs = [g for g in farm if g["count"] >= 3 or farm_cells > fail_cells]
    room = max(0, LOG_GROUPS - min(len(new_fail), LOG_GROUPS))
    for g in farm_logs[:room]:
        rep_ = g.get("rep") or {}
        L.append(f"- (farm, {g['count']} cell(s) share it) `t2 batch log {bid} {rep_.get('entry')}:{rep_.get('cell')} "
                 f"--tail 200`: {cut(g['head'], 60)}")
    if len(farm_logs) > room:
        L.append(f"- and {len(farm_logs) - room} more farm groups past the {LOG_GROUPS} logs: triage them by reason alone")
    if rep["meta"].get("manual"):
        L += ["", "## Manual steps the batch printed and did not run", ""]
        L += [f"- {m}" for m in rep["meta"]["manual"]]
    L += ["", "---", ""]
    L.append((procedure or "").replace("<BATCH>", bid).strip())
    return "\n".join(L) + "\n"


def to_json(rep):
    return json.dumps(rep, indent=1, sort_keys=True)
