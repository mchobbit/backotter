"""BackShot2All (docs/design/bs2-all.md; piece A9): what the word runs, what it
lists as SKIPPED, and its verdict.  Pure: bs2 gathers the hosts' rows and
passes them in; nothing here reaches a host.

  counts(sources, tier2_of)  {source: (tier 0, tier 1, tier 2)}: decision 5's
                             1,000 / 20 / 5 (UEFI only) a source, Alpine's four
                             as BackShot2Alpine (200 tier 1 each, its 30 tier 2
                             shared, 1,000 tier 0)
  skipped(table, missing)    the SKIPPED list: every source sources.py classes
                             as skipped, with its piece; every built source no
                             host of the run holds ("missing"); tier 3 (A8)
  groups(skipped)            that list by piece, in PIECES' order
  best(skipped)              the verdict a run can give at best
  tally(rows)                {(source, tier): {verdict: n}}, one row a case
  verdict(...)               FAIL, INCOMPLETE, PARTIAL PASS or PASS, and why
  summary(...)               the report as a dict (bs2's --json), and words()
                             its lines

A run's directories on a host (ROOT/runs/): RUN (tier1.jsonl, tier2.jsonl, the
queue's tierN-queue.jsonl rows for cases that wrote none, queue.txt, and
bs2all.json, the run's manifest: what it planned and SKIPPED), RUN-t0 (the
sweeps' and the draws' tier 0 rows), RUN-corpus (the corpus at tier 1) and
RUN-lo (the leftovers set: leftovers.jsonl).
"""

import json
import os
import re

WORD = "BackShot2All"
T0 = 1000            # tier 0 cases a source (decision 5; Alpine's 5 x 200 alike)
T1 = 20              # tier 1 cases a non-Alpine source
T1_ALPINE = 200      # an Alpine source's (BackShot2Alpine's N)
LEFTOVERS_STEP_S = 180   # a leftovers step (bs2-host leftovers --plan's ~3 min a step)

# The pieces that build what is SKIPPED, in the order the plan and the report list them.
PIECES = {
    "A3": "ZFS, md and multi-disk guests",
    "A4": "the BSDs",
    "A5": "illumos",
    "A6": "Haiku, Redox and Sculpt OS",
    "A8": "the Thinkpad tier (BackShot1's real machines, tier 3)",
    "later": "a later lane (not planned yet)",
    "unplanned": "no piece planned (families.PLANNED has no row for its OS)",
    "missing": "built, but not on any host in this run",
}
TIER3 = {"name": "tier 3", "piece": "A8", "why": "the Thinkpad tier (B10's glue, a case onto the Thinkpad "
                                                  "through backshot1, isn't built)"}
VERDICTS = ("PASS", "FAIL", "HARNESS", "INVALID", "UNSUPPORTED")
# A run's files on a host, after ROOT/runs/RUN (bs2's gather_cmd()); ROW_FILES are its cases' rows.
ROW_FILES = ("/tier1.jsonl", "/tier2.jsonl", "/tier1-queue.jsonl", "/tier2-queue.jsonl", "-t0/tier0.jsonl")
FILES = ROW_FILES + ("/queue.txt", "-corpus/tier1.jsonl", "-lo/leftovers.jsonl")
CASE_RE = re.compile(r"^(.+)-([0-9a-f]{16})$")


def counts(sources, tier2_of, alpine=()):
    """{source: (tier 0, tier 1, tier 2)} for an All run of SOURCES: T0 a source, tier 1
    T1_ALPINE for ALPINE's and T1 for the rest, tier 2 TIER2_OF(source, sources) (bs2's:
    Alpine's 30 shared by its UEFI sources, 5 a UEFI source, none for a BIOS one)."""
    return {s: (T0, T1_ALPINE if s in alpine else T1, tier2_of(s, sources)) for s in sources}


def skipped(table, missing=None, tier3=True):
    """The SKIPPED list, [{"name", "piece", "why"}] in PIECES' order then by name: TABLE's
    skipped sources ({name: sources.Class}, those not built: their piece, None as "not a
    source" never listed), MISSING ({name: why}: built, but no host of the run holds it),
    and tier 3 (A8) when TIER3."""
    out = [{"name": n, "piece": c.piece, "why": c.why} for n, c in table.items()
           if not c.built and c.piece is not None]
    out += [{"name": n, "piece": "missing", "why": f"not on any host in this run ({w})"}
            for n, w in (missing or {}).items()]
    if tier3:
        out.append(dict(TIER3))
    order = {p: i for i, p in enumerate(PIECES)}
    return sorted(out, key=lambda x: (order.get(x["piece"], len(order)), x["name"] != "tier 3", x["name"]))


def groups(sk):
    """[(piece, [entry...])] of SK (skipped()'s list), in PIECES' order."""
    out = []
    for x in sk:
        if not out or out[-1][0] != x["piece"]:
            out.append((x["piece"], []))
        out[-1][1].append(x)
    return out


def sources_skipped(sk):
    """How many sources SK names (tier 3 is a tier, not a source)."""
    return sum(1 for x in sk if x["name"] != "tier 3")


def best(sk):
    """The verdict a run with SK SKIPPED can give at best."""
    if not sk:
        return "PASS"
    n = sources_skipped(sk)
    by = ", ".join(f"{p} {sum(1 for x in xs if x['name'] != 'tier 3')}" for p, xs in groups(sk)
                   if any(x["name"] != "tier 3" for x in xs))
    t3 = any(x["name"] == "tier 3" for x in sk)
    return (f"PARTIAL PASS at best ({n} sources SKIPPED" + (f": {by}" if by else "") +
            ("; tier 3 SKIPPED" if t3 else "") + ")")


def skipped_words(sk, width=100):
    """The SKIPPED list's lines, by piece: names, and each one's why where they differ."""
    lines = []
    for p, xs in groups(sk):
        names = [x["name"] for x in xs]
        head = f"SKIPPED ({p}: {PIECES.get(p, p)}): "
        whys = {x["why"] for x in xs}
        if p in ("A3", "A4", "A5", "A6") or len(whys) == 1 and len(xs) > 1:
            lines.append(head + f"{len(xs)}: " + ", ".join(names))
            if len(whys) == 1 and p not in ("A3", "A4", "A5", "A6"):
                lines.append(f"  ({xs[0]['why'][:width * 2]})")
        else:
            lines.append(head + f"{len(xs)}")
            lines += [f"  {x['name']}: {x['why'][:width * 2]}" for x in xs]
    return lines


# ---- the rows --------------------------------------------------------------------------

def source_of_case(name):
    """The source a case's name ("SOURCE-HASH16", case.name_of) names, else None."""
    m = CASE_RE.match(name or "")
    return m.group(1) if m else None


def last_rows(rows):
    """ROWS (tierN.jsonl lines as dicts), one per (tier, case): the last there is (a case's
    row in tierN.jsonl and, should both be there, the queue's after it)."""
    out = {}
    for r in rows:
        if not isinstance(r, dict) or "case" not in r:
            continue
        out[(r.get("tier"), r["case"])] = r
    return list(out.values())


def tally(rows):
    """{(source, tier): {verdict: n}} of ROWS (last_rows()'s)."""
    out = {}
    for r in last_rows(rows):
        k = (r.get("source") or source_of_case(r["case"]) or "?", r.get("tier"))
        v = r.get("verdict") or "HARNESS"
        out.setdefault(k, {})
        out[k][v] = out[k].get(v, 0) + 1
    return out


def fails_of(rows, where=""):
    """The FAIL rows of ROWS as {"case", "source", "tier", "check", "dir", "where"}."""
    out = []
    for r in last_rows(rows):
        if r.get("verdict") != "FAIL":
            continue
        why = r.get("why") or []
        out.append({"case": r["case"], "source": r.get("source") or source_of_case(r["case"]), "tier": r.get("tier"),
                    "check": r.get("check") or (str(why[0])[:160] if why else ""), "dir": r.get("dir"),
                    "where": r.get("host") or where})
    return out


def parse_files(text):
    """{file: [entry]} of a host's gather output (an "@file F" line before each of FILES):
    a .jsonl file's rows (dicts; a line that isn't one skipped), queue.txt's (tier, case
    name) lines."""
    out, cur = {}, None
    for line in text.splitlines():
        if line.startswith("@file "):
            cur = line[len("@file "):].strip()
            out.setdefault(cur, [])
            continue
        if cur is None or not line.strip():
            continue
        if cur.endswith(".jsonl"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict):
                out[cur].append(r)
        elif cur.endswith("queue.txt"):
            w = line.split()
            if len(w) >= 2 and w[0].isdigit():
                name = os.path.basename(w[1])
                out[cur].append((int(w[0]), name[:-len(".case")] if name.endswith(".case") else name))
    return out


def queued_unrowed(queued, rows):
    """The queued cases ([(tier, case name)], queue.txt's) with no row in ROWS."""
    have = {(r.get("tier"), r.get("case")) for r in rows if isinstance(r, dict)}
    return [(t, c) for t, c in queued if (t, c) not in have]


# ---- the verdict -----------------------------------------------------------------------

def verdict(n_fail, incomplete, n_skipped):
    """The run's verdict: FAIL when anything failed (N_FAIL); else INCOMPLETE while anything
    (INCOMPLETE, its reasons) has no verdict yet: a planned or queued case with no row, a
    HARNESS, INVALID or UNSUPPORTED row (no verdict on bko-holt), the corpus or the leftovers
    set not run; else PARTIAL PASS while anything is SKIPPED (N_SKIPPED: sources and tier 3);
    PASS only when nothing is."""
    if n_fail:
        return "FAIL"
    if incomplete:
        return "INCOMPLETE"
    if n_skipped:
        return "PARTIAL PASS"
    return "PASS"


def summary(manifest, rows, queued, corpus_rows, leftovers_rows, unread=()):
    """The report of an All run, as a dict.  MANIFEST: the run's bs2all.json (run, hosts,
    planned {source: [t0, t1, t2]}, skipped [entry], corpus [case names], leftovers {steps,
    host}); ROWS: every host's tier 0, 1 and 2 rows (and the queue's) of the run, each with
    "host"; QUEUED: [(tier, case name)] of every host's queue.txt; CORPUS_ROWS: the corpus
    run's tier 1 rows; LEFTOVERS_ROWS: leftovers.jsonl's rows; UNREAD: the hosts that didn't
    answer, in words (each makes the run INCOMPLETE)."""
    planned = {s: tuple(v) for s, v in (manifest.get("planned") or {}).items()}
    sk = manifest.get("skipped") or []
    tl = tally(rows)
    incomplete, per = list(unread), {}
    for s, (t0, t1, t2) in planned.items():
        per[s] = {}
        for tier, want in ((0, t0 + t1 + t2 if t0 else 0), (1, t1), (2, t2)):
            got = tl.get((s, tier), {})
            n = sum(got.values())
            per[s][tier] = {"planned": want, **{v: got.get(v, 0) for v in VERDICTS},
                            **{v: k for v, k in got.items() if v not in VERDICTS}}
            if n < want:
                incomplete.append(f"{s} tier {tier}: {n} of {want} cases have a row"
                                  + (" (tier 0: the sweep's and every drawn case's)" if tier == 0 else ""))
            bad = {v: k for v, k in got.items() if v not in ("PASS", "FAIL")}
            if bad:
                incomplete.append(f"{s} tier {tier}: " + ", ".join(f"{k} {v}" for v, k in sorted(bad.items())))
    for s in sorted({k[0] for k in tl} - set(planned)):
        incomplete.append(f"{s}: rows, but not in the run's plan")
    un = queued_unrowed(queued, rows)
    if un:
        incomplete.append(f"{len(un)} queued cases have no row yet (" +
                          ", ".join(f"{c} t{t}" for t, c in un[:3]) + (", ..." if len(un) > 3 else "") + ")")
    fails = fails_of(rows)
    # The corpus: every case in cases/ (of a source the run held) at tier 1, in RUN-corpus.
    want_c = list(manifest.get("corpus") or [])
    crows = {r["case"]: r for r in last_rows(corpus_rows)}
    corpus = {"planned": len(want_c), **{v: sum(1 for c in want_c if (crows.get(c) or {}).get("verdict") == v)
                                         for v in VERDICTS}}
    if want_c and not crows:
        incomplete.append(f"the corpus not run ({len(want_c)} cases: bs2 {WORD} corpus {manifest.get('run')})")
    else:
        no = [c for c in want_c if c not in crows]
        if no:
            incomplete.append(f"the corpus: {len(no)} of {len(want_c)} cases have no row ({', '.join(no[:3])})")
        bad = [c for c in want_c if (crows.get(c) or {}).get("verdict") not in (None, "PASS", "FAIL")]
        if bad:
            incomplete.append(f"the corpus: {len(bad)} cases without a verdict ({', '.join(bad[:3])})")
    fails += [dict(f, where="corpus") for f in fails_of([crows[c] for c in want_c if c in crows])]
    # The leftovers set: its steps in order on one disk, RUN-lo/leftovers.jsonl.
    lo = manifest.get("leftovers") or {}
    steps = int(lo.get("steps") or 0)
    lrows = {}
    for r in leftovers_rows:
        if isinstance(r, dict) and "id" in r:
            lrows[r["id"]] = r
    left = {"planned": steps, "host": lo.get("host"),
            **{v: sum(1 for r in lrows.values() if r.get("verdict") == v) for v in VERDICTS}}
    if steps and not lrows:
        incomplete.append(f"the leftovers set not run ({steps} steps: bs2 {WORD} leftovers {manifest.get('run')})")
    elif steps:
        if len(lrows) < steps:
            incomplete.append(f"the leftovers set: {len(lrows)} of {steps} steps have a row (it stops at the first "
                              "step that doesn't pass)")
        bad = sorted(i for i, r in lrows.items() if r.get("verdict") not in ("PASS", "FAIL"))
        if bad:
            incomplete.append(f"the leftovers set: {', '.join(bad)} without a verdict")
    fails += [{"case": r.get("case"), "source": "leftovers", "tier": 1, "check": r.get("check") or "",
               "dir": r.get("dir"), "where": f"leftovers {i}"} for i, r in sorted(lrows.items()) if r.get("verdict") == "FAIL"]
    n_sk = len(sk)
    v = verdict(len(fails), incomplete, n_sk)
    return {"run": manifest.get("run"), "word": WORD, "hosts": manifest.get("hosts") or [], "verdict": v,
            "best": best(sk), "fails": fails, "incomplete": incomplete, "sources": per,
            "corpus": corpus, "leftovers": left, "skipped": sk}


def cell(c):
    """A tier's cell in the report: "PASS/planned" and the rest's first letters."""
    if not c["planned"] and not any(c.get(v) for v in VERDICTS):
        return "-"
    rest = " ".join(f"{v[0]}{c[v]}" for v in VERDICTS[1:] if c.get(v))
    return f"{c['PASS']}/{c['planned']}" + (f" {rest}" if rest else "")


def words(sm):
    """The report's lines of SM (summary()'s)."""
    out = [f"{WORD} {sm['run']}: hosts {', '.join(sm['hosts'])}",
           "per source, each tier PASS/planned, then F FAIL, H HARNESS, I INVALID, U UNSUPPORTED "
           "(tier 0 planned: the sweep's and every drawn case's)"]
    w = max([16] + [len(s) + 1 for s in sm["sources"]])
    out.append(f"{'source':{w}} {'tier 0':18} {'tier 1':14} tier 2")
    for s, t in sm["sources"].items():
        out.append(f"{s:{w}} {cell(t[0]):18} {cell(t[1]):14} {cell(t[2])}")
    c, lo = sm["corpus"], sm["leftovers"]
    out.append(f"{'corpus':{w}} {'':18} {cell(c):14}")
    out.append(f"{'leftovers':{w}} {'':18} {cell(lo):14}" + (f" (on {lo['host']})" if lo.get("host") else ""))
    out += skipped_words(sm["skipped"])
    for f in sm["fails"]:
        out.append(f"FAIL {f['case']} tier {f['tier']} ({f['where']}): {f['check']}" + (f" ({f['dir']})" if f.get("dir") else ""))
    for x in sm["incomplete"][:30]:
        out.append(f"incomplete: {x}")
    if len(sm["incomplete"]) > 30:
        out.append(f"incomplete: ... and {len(sm['incomplete']) - 30} more (--json has them all)")
    tail = f" ({sm['best']})" if sm["verdict"] in ("PARTIAL PASS", "INCOMPLETE") and sm["skipped"] else ""
    out.append(f"verdict: {sm['verdict']}{tail}")
    return out
