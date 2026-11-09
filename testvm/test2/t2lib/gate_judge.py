"""GateTest2's judge (G1): how the gate layers' outputs become needs and verdicts.

The parsers of holt-test.sh's, fork-tests.sh's, fork-root.sh's, bincheck.sh's,
run-all.sh's and the self-tests' outputs, and the rules that turn them into each
layer's needs, FAIL lines and verdict.  It is a judge input of every gate layer
(layers.json), so a change to a rule here makes the layers it judges due; the
runner (gate.py: host jobs, reruns, rows) is not, and changes to it make only
gate:selftest due.  Pure: the unit tests feed it real, scrubbed output.
"""

import os
import re

NEED_FAIL = ("FAIL", "LIMIT-NEW")
TEXT_MAX = 200

# A skip message of the m run that means "needs root" (fork-root's root-list need), and
# a skip as root that shouldn't happen.
ROOTISH = re.compile(r"(?i)(needs? root|as root|CAP_[A-Z_]+|operation not permitted|mounting .*needs root|"
                     r"needs privileges)")


# --- needs and verdicts --------------------------------------------------------------------

def cut(s, n=TEXT_MAX):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n - 2] + ".."


def verdict_of(needs):
    """PASS unless a need FAILs: LIMIT (classed), SKIP and DIFF don't fail a layer.  No
    needs at all is no result: HARNESS, never a PASS."""
    if not needs:
        return "HARNESS"
    return "FAIL" if any(v in NEED_FAIL for v in needs.values()) else "PASS"


def counts_of(needs):
    c = {"PASS": 0, "FAIL": 0, "LIMIT": 0}
    for v in needs.values():
        if v == "PASS":
            c["PASS"] += 1
        elif v in NEED_FAIL:
            c["FAIL"] += 1
        elif v == "LIMIT":
            c["LIMIT"] += 1
    return c


class Outcome:
    """One attempt of one layer: its needs, FAIL lines, extra, and verdict."""

    def __init__(self, needs=None, fails=None, extra=None, harness=None, note=None, ram_peak_kib=None):
        self.needs = dict(needs or {})
        if not harness and not self.needs:   # nothing was read: never a PASS with no needs
            harness = "the layer gave no result (no needs)"
        self.harness = harness          # why it couldn't run (a HARNESS verdict), or None
        self.verdict = "HARNESS" if harness else verdict_of(self.needs)
        self.fails = [cut(f) for f in (fails or [])][:20]
        if harness and not self.fails:
            self.fails = [cut("harness: " + harness)]
        self.extra = dict(extra or {})
        self.note = note
        self.ram_peak_kib = ram_peak_kib
        self.wall_s = self.waits_s = 0
        self.logs = None
        self.wear = {}
        self.meta = {}
        self.start = None               # when the attempt began, when not when run_one called it
        self.still_running = False      # its job may still run: no rerun beside it
        self.invalid = None             # why its row can't stand for the cell (valid: false)

    def __repr__(self):
        return f"Outcome({self.verdict}, {self.needs})"


def harness(why, **k):
    """A HARNESS outcome.  WHY is never empty (a missing reason is still no result); a
    reason that says its job may still run (host_job's) stops the rerun."""
    o = Outcome(harness=str(why) if why else "unknown harness failure")
    o.still_running = bool(getattr(why, "still_running", False))
    for a, v in k.items():
        setattr(o, a, v)
    return o


# --- parsers (pure: the unit tests feed them real, scrubbed output) ------------------------

CARGO_RESULT = re.compile(r"^test result: (\w+)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; "
                          r"(\d+) filtered out")
CARGO_FAILED = re.compile(r"^test (\S+) \.\.\. FAILED")
CARGO_ERROR = re.compile(r"^error(?:\[E\d+\])?: (.*)$")


def parse_cargo(text):
    """cargo test's output: every "test result:" line summed, the failed tests by name,
    and the compiler's errors."""
    out = {"results": 0, "passed": 0, "failed": 0, "ignored": 0, "filtered": 0, "failed_tests": [], "errors": []}
    for raw in (text or "").splitlines():
        line = raw.strip()
        m = CARGO_RESULT.match(line)
        if m:
            out["results"] += 1
            for k, i in (("passed", 2), ("failed", 3), ("ignored", 4), ("filtered", 6)):
                out[k] += int(m.group(i))
            continue
        m = CARGO_FAILED.match(line)
        if m:
            if m.group(1) not in out["failed_tests"]:
                out["failed_tests"].append(m.group(1))
            continue
        m = CARGO_ERROR.match(line)
        if m and not m.group(1).startswith("test failed"):
            out["errors"].append(m.group(1))
    return out


def parse_steps(text):
    """steps.tsv (STEP RC SECS NOTE) -> {step: {"rc": int or "skip", "secs", "note"}}."""
    out = {}
    for line in (text or "").splitlines():
        p = line.split("\t")
        if len(p) < 3:
            continue
        rc = p[1] if p[1] == "skip" else (int(p[1]) if p[1].lstrip("-").isdigit() else p[1])
        out[p[0]] = {"rc": rc, "secs": int(p[2]) if p[2].isdigit() else 0, "note": p[3] if len(p) > 3 else ""}
    return out


def parse_kv(text):
    """KEY=VALUE lines (info.txt): the first value of each key."""
    out = {}
    for line in (text or "").splitlines():
        k, sep, v = line.partition("=")
        if sep and k.strip() and k.strip() not in out:
            out[k.strip()] = v.strip()
    return out


def parse_diff_json(obj):
    """diff.json (contract P6, R29): corpus, differ {GUEST: {stages, lines_changed}},
    invariants {GUEST: [msg]}, invariants_skipped; what's missing is empty."""
    obj = obj if isinstance(obj, dict) else {}
    return {"corpus": obj.get("corpus"), "differ": dict(obj.get("differ") or {}),
            "invariants": {g: list(m) for g, m in (obj.get("invariants") or {}).items() if m},
            "invariants_skipped": dict(obj.get("invariants_skipped") or {})}


REDACT_LINE = re.compile(r"^(ok|FAIL)\s+(\S+?):?\s")


def parse_redact(text):
    """redact_dumps' output: the dumps that were ok and that FAILed, by path."""
    ok, bad = [], []
    for line in (text or "").splitlines():
        m = REDACT_LINE.match(line)
        if m:
            (ok if m.group(1) == "ok" else bad).append(m.group(2).rstrip(":"))
    return {"ok": ok, "fail": bad}


def holt_outcome(steps, logs, diff, raw_tsv=None, info=None):
    """gate:holt from holt-test.sh's steps.tsv, its logs and plans/diff.json.  PASS when
    cargo-test, invariants and redact-dumps pass and golden-plans is PASS or DIFF (R23);
    an invariant violation fails."""
    needs, fails, extra = {}, [], {}
    st = steps.get("cargo-test")
    c = parse_cargo(logs.get("cargo-test.log", ""))
    extra["cargo"] = {k: c[k] for k in ("results", "passed", "failed", "ignored", "filtered")}
    if st is None:
        needs["cargo-test"] = "FAIL"
        fails.append("cargo-test: didn't run")
    elif st["rc"] == 0 and c["results"] and not c["failed"]:
        needs["cargo-test"] = "PASS"
    else:
        needs["cargo-test"] = "FAIL"
        if c["failed_tests"]:
            fails.append(f"cargo-test: {c['failed']} failed: " + ", ".join(c["failed_tests"][:8]))
        elif c["errors"]:
            fails.append("cargo-test: " + c["errors"][0])
        else:
            fails.append(f"cargo-test: exit {st['rc']}, {c['results']} test binaries reported")
    g = steps.get("golden-plans")
    d = parse_diff_json(diff)
    if g is None or g["rc"] == "skip":
        # H1 is in the tree: no golden test (or corpus) in the copy is a broken copy, not
        # "not built yet".
        needs["golden-plans"] = needs["invariants"] = "FAIL"
        fails.append("golden-plans: " + (f"skipped: {g['note']}" if g is not None and g["note"] else
                                         "didn't run") + " (H1's golden plans are in the tree)")
        if g is not None:
            extra["golden_skipped"] = g["note"]
    else:
        gc = parse_cargo(logs.get("golden.log", ""))
        if diff is None:
            needs["golden-plans"] = needs["invariants"] = "FAIL"
            fails.append(f"golden-plans: exit {g['rc']}, no diff.json" + (f": {gc['errors'][0]}" if gc["errors"] else ""))
        else:
            needs["golden-plans"] = "DIFF" if d["differ"] else "PASS"
            if d["invariants"]:
                needs["invariants"] = "FAIL"
                for guest, msgs in sorted(d["invariants"].items()):
                    fails.append(f"invariants: {guest}: " + "; ".join(msgs[:3]))
            elif g["rc"] != 0:
                needs["invariants"] = "FAIL"
                fails.append(f"golden-plans: exit {g['rc']} with no invariant in diff.json" +
                             (f": {gc['failed_tests'][0]}" if gc["failed_tests"] else ""))
            else:
                needs["invariants"] = "PASS"
            extra["plan_diff"] = d["differ"]
            if d["invariants"]:
                extra["invariants"] = d["invariants"]
            if d["invariants_skipped"]:   # counts only: the messages stay in the host's diff.json
                extra["invariants_skipped"] = {g: len(m) if isinstance(m, list) else 1
                                               for g, m in sorted(d["invariants_skipped"].items())}
            if d["corpus"] is not None:
                extra["corpus"] = d["corpus"]
    ran, rv = [], "SKIP"
    for step, logname in (("redact-raw", None), ("redact-corpus", "redact-corpus.log")):
        s = steps.get(step)
        if s is None or s["rc"] == "skip":
            if step == "redact-corpus":   # the corpus is in the tree (H1): its redaction proof must run
                rv = "FAIL"
                fails.append("redact-corpus: " + (f"skipped: {s['note']}" if s is not None and s["note"] else
                                                  "didn't run") + " (H1's corpus is in the tree)")
            continue
        if logname:
            r = parse_redact(logs.get(logname, ""))
            n_ok = len(r["ok"])
            bad = [os.path.basename(os.path.dirname(p)) or p for p in r["fail"]]
        else:   # the raw dumps' messages stay on the host: per guest only ok or FAIL
            rows = [l.split("\t") for l in (raw_tsv or "").splitlines() if "\t" in l]
            n_ok, bad = sum(1 for x in rows if x[1] == "ok"), [x[0] for x in rows if x[1] != "ok"]
        ran.append(f"{step[7:]} {n_ok} ok" + (f", {len(bad)} FAIL" if bad else ""))
        if s["rc"] != 0 or bad or not n_ok:
            rv = "FAIL"
            fails.append(f"{step}: exit {s['rc']}, {n_ok} ok" + (": FAIL " + ", ".join(bad[:10]) if bad else ""))
        elif rv != "FAIL":
            rv = "PASS"
    needs["redact-dumps"] = rv
    if ran:
        extra["redact"] = "; ".join(ran)
    note = None
    if d["differ"]:
        odd = sorted(g for g, x in d["differ"].items() if isinstance(x, dict) and ("unverified" in x or "unplanned" in x))
        real = sorted(g for g in d["differ"] if g not in odd)
        bits = (["plan diff not yet accepted: " + ", ".join(real)] if real else []) + \
            (["unproven golden plans (no raw check_plan, or no plan): " + ", ".join(odd)] if odd else [])
        note = "; ".join(bits)
    peak = None
    if info and str(info.get("ram_peak_kib", "")).isdigit():
        peak = int(info["ram_peak_kib"])
    return Outcome(needs, fails, extra, note=cut(note, 1000) if note else None, ram_peak_kib=peak)


REPORT_HEADS = (   # results.py compare's sections, as fork-tests.sh's NAME.report has them
    ("fork_only", re.compile(r"^Fork-only test failures .*: (\d+)$")),
    ("build_fails", re.compile(r"^Build targets that fail on the fork, not on stock: (\d+)$")),
    ("skipped", re.compile(r"^Skipped on the fork, passing on stock: (\d+)$")),
    ("missing", re.compile(r"^On stock, missing on the fork: (\d+)$")),
    ("added", re.compile(r"^Added by the fork: (\d+)")),
    ("paired", re.compile(r"^Paired by name .*: (\d+)$")),
    ("known", re.compile(r"^Known stock failures: (\d+)$")),
)
REPORT_TEST = re.compile(r"^(.*?) :: (.*?)(?:  \((?:stock|fork): ([\w-]+)\)|  (\w+))?$")
REPORT_BUILD = re.compile(r"^(#\w+) (.*?)  \(stock: ([\w-]+)\)$")


def parse_fork_report(text):
    """A NAME.report: each section's count ("n") and entries; test entries are
    (suite, test, stock-or-status), build entries (kind, name, stock)."""
    out = {k: [] for k, _ in REPORT_HEADS}
    out.update(n={}, fork_counts=None, stock_counts=None, detail={})
    sec = side = None
    for line in (text or "").splitlines():
        if line.startswith("stock: "):
            side = "stock"
            continue
        if line.startswith("fork:"):
            side = "fork"
            continue
        if side and line.startswith("  tests: "):
            out[side + "_counts"] = line.strip()
            side = None
            continue
        head = next(((k, rx.match(line)) for k, rx in REPORT_HEADS if rx.match(line)), None)
        if head:
            sec = head[0]
            out["n"][sec] = int(head[1].group(1))
            continue
        if line.startswith("      |") and sec in ("fork_only", "build_fails") and out[sec]:
            out["detail"].setdefault(out[sec][-1][:2], []).append(line[8:])
            continue
        if sec is None or not line.startswith("  ") or line.startswith("      |") or line.startswith("  ("):
            continue
        body = line[2:]
        if sec == "build_fails":
            m = REPORT_BUILD.match(body)
            if m:
                out[sec].append((m.group(1), m.group(2), m.group(3)))
            continue
        m = REPORT_TEST.match(body)
        if m:
            out[sec].append((m.group(1), m.group(2), m.group(3) or m.group(4) or ""))
    return out


def baseline_entry(baseline, name, suite, test, detail=()):
    for e in ((baseline or {}).get("fork") or {}).get(name) or []:
        if e.get("suite", suite) == suite and e.get("test") == test and \
                (not e.get("expect") or any(e["expect"] in d for d in detail)):
            return e
    return None


def fork_outcome(name, report_text, baseline, why=None):
    """A fork layer from its NAME.report: suite (no fork-only test failure beyond
    baseline.json's, each of which is a LIMIT with its reason) and build-targets."""
    if report_text is None:
        return harness(why or f"no complete results for {name}")
    r = parse_fork_report(report_text)
    if "fork_only" not in r["n"]:
        return harness(f"{name}.report has no comparison (results.py compare didn't run)")
    needs, fails, limits, bad = {}, [], {}, []
    for suite, test, stock in r["fork_only"]:
        e = baseline_entry(baseline, name, suite, test, r["detail"].get((suite, test), ()))
        if e:
            limits[f"{suite} :: {test}"] = e.get("why", "known")
        else:
            bad.append(f"{suite} :: {test} (stock: {stock or 'absent'})")
    for sec, what in (("skipped", "skipped on the fork, passing on stock"), ("missing", "on stock, missing on the fork")):
        for suite, test, stock in r[sec]:
            if sec == "missing" and stock != "pass":
                continue
            e = baseline_entry(baseline, name, suite, test)
            if e:
                limits[f"{suite} :: {test}"] = e.get("why", "known")
            else:
                bad.append(f"{suite} :: {test} ({what})")
    if not re.search(r"tests: [1-9]\d* pass", r["fork_counts"] or ""):
        bad.append(f"the fork's suite passed no test ({r['fork_counts']})")
    if r["n"]["fork_only"] > len(r["fork_only"]):
        bad.append(f"{r['n']['fork_only'] - len(r['fork_only'])} more fork-only failures (unparsed)")
    needs["suite"] = "FAIL" if bad else ("LIMIT" if limits else "PASS")
    fails += [f"fork-only: {b}" for b in bad]
    bbad = []
    for kind, bname, stock in r["build_fails"]:
        e = baseline_entry(baseline, name, kind, bname)
        if e:
            limits[f"{kind} {bname}"] = e.get("why", "known")
        else:
            bbad.append(f"{kind} {bname} (stock: {stock})")
    if r["n"].get("build_fails", 0) > len(r["build_fails"]):
        bbad.append(f"{r['n']['build_fails'] - len(r['build_fails'])} more failing build targets (unparsed)")
    needs["build-targets"] = "FAIL" if bbad else ("LIMIT" if any(k.startswith("#") for k in limits) else "PASS")
    fails += [f"build: {b}" for b in bbad]
    extra = {"counts": r["fork_counts"], "stock_counts": r["stock_counts"],
             "skipped_vs_stock": r["n"].get("skipped", 0), "missing_vs_stock": r["n"].get("missing", 0),
             "added": r["n"].get("added", 0), "known_stock_failures": r["n"].get("known", 0)}
    if limits:
        extra["limits"] = limits
    return Outcome(needs, fails, extra)


GO_RESULT = re.compile(r"^--- (PASS|FAIL|SKIP): (\S+) \(([\d.]+)s\)")
GO_EVENT = re.compile(r"^=== (RUN|CONT|PAUSE|NAME)\s+(\S+)")


def parse_go_v(text):
    """go test -test.v output: {top-level test: (status, [its output lines])}; a test
    that started and never ended is NORESULT."""
    status, lines, cur = {}, {}, None
    for line in (text or "").splitlines():
        m = GO_EVENT.match(line)
        if m:
            cur = m.group(2).split("/")[0]
            lines.setdefault(cur, [])
            if m.group(1) == "RUN" and "/" not in m.group(2):
                status.setdefault(cur, "NORESULT")
            continue
        m = GO_RESULT.match(line)
        if m:
            if "/" not in m.group(2):
                status[m.group(2)] = m.group(1)
            continue
        s = line.strip()
        if cur and s and not s.startswith("--- ") and s not in ("PASS", "FAIL") and not s.startswith(("ok ", "FAIL\t")):
            lines[cur].append(s)
    return {t: (st, lines.get(t, [])) for t, st in status.items()}


def parse_root_list(text):
    """restic-root-tests.txt: (runs [(pkg, test)], outs [(pkg, test)])."""
    runs, outs = [], []
    for line in (text or "").splitlines():
        f = line.split("#", 1)[0].split()
        if len(f) >= 3 and f[0] in ("run", "out"):
            (runs if f[0] == "run" else outs).append((f[1], f[2]))
    return runs, outs


def fork_root_outcome(listing, files):
    """gate:fork-root:restic: one need per listed test (PASS, FAIL, or SKIP with the test's
    reason; a skip for root when run as root fails) and root-list (no root skip of the m
    run that the list doesn't review).  FILES: fork-root.sh's outputs by name."""
    runs, outs = parse_root_list(listing)
    if not runs:
        return harness("restic-root-tests.txt lists no run lines")
    built = {}
    for line in (files.get("build.tsv") or "").splitlines():
        p = line.split("\t")
        if len(p) >= 2:
            built[p[0]] = p[1]
    if not built:
        return harness("fork-root.sh built nothing (no build.tsv): " + tail(files.get("job.log")))
    ran = {}
    for line in (files.get("run.tsv") or "").splitlines():
        p = line.split("\t")
        if len(p) >= 2:
            ran[p[0]] = p[1]
    needs, fails, seen = {}, [], set()
    for pkg in sorted({p for p, _ in runs}):
        res = parse_go_v(files.get("run-%s.log" % pkg.replace("/", "_"), ""))
        ended = any(st == "NORESULT" for st, _ in res.values()) or ran.get(pkg, "0") not in ("0", "1")
        for t in [t for p, t in runs if p == pkg]:
            nm = t if t not in seen else f"{pkg}:{t}"
            seen.add(t)
            if built.get(pkg) != "0":
                needs[nm] = "FAIL"
                fails.append(f"{t}: {pkg} didn't compile (go test -c exit {built.get(pkg, '?')})")
                continue
            st, out = res.get(t, (None, []))
            why = cut("; ".join(out[:3]), 150)
            if st == "PASS":
                needs[nm] = "PASS"
            elif st == "SKIP":
                needs[nm] = "FAIL"
                fails.append(f"{t}: skipped{' for root although run as root' if ROOTISH.search(' '.join(out)) else ', so it did not run'}: {why}")
            elif st is None and ended:
                needs[nm] = "FAIL"
                fails.append(f"{t}: didn't run: the test binary ended first (exit {ran.get(pkg, '?')})")
            elif st is None:
                needs[nm] = "FAIL"
                fails.append(f"{t}: listed, but the test binary has no such test (review the list)")
            else:
                needs[nm] = "FAIL"
                fails.append(f"{t}: {'no result (the binary ended first)' if st == 'NORESULT' else 'FAIL'}: {why}")
    extra = {}
    if "skips.tsv" in files:
        listed, unreviewed = set(runs) | set(outs), {}
        for line in files["skips.tsv"].splitlines():
            p = line.split("\t")
            if len(p) >= 3 and ROOTISH.search(p[2]) and (p[0], p[1]) not in listed:
                unreviewed[f"{p[0]} {p[1]}"] = cut(p[2], 120)
        needs["root-list"] = "FAIL" if unreviewed else "PASS"
        if unreviewed:
            fails.append("not reviewed for root (a run or out line in restic-root-tests.txt): " +
                         ", ".join(sorted(unreviewed)[:8]))
            extra["unreviewed"] = unreviewed
    else:
        needs["root-list"] = "SKIP"
        extra["root_list"] = cut(files.get("skips.none") or "no m run of this commit on the host", 150)
    cleaned = [l for l in (files.get("cleanup.txt") or "").splitlines() if l.strip()]
    extra["cleanup"] = cleaned or "nothing was left mounted or attached"
    note = cut("removed after the tests: " + "; ".join(cleaned), 1000) if cleaned else None
    return Outcome(needs, fails, extra, note=note)


BINLINE = re.compile(r"^(PASS|FAIL|SKIP) (abi|static|version) (\S+)(?: \[([^\]]*)\])?: (.*)$")


def parse_bincheck(text):
    """bincheck.sh's lines: [(verdict, check, name, value, detail)]."""
    out = []
    for line in (text or "").splitlines():
        m = BINLINE.match(line.strip())
        if m:
            out.append((m.group(1), m.group(2), m.group(3), m.group(4) or "", m.group(5)))
    return out


def bin_outcome(check, text, rc, baseline=None, sums=None, commits=None):
    """gate:bin:CHECK: one need per binary; version also agree (agreement()).  GLIBC, NSS
    and ABI failures are never LIMITs: bincheck.sh's FAIL stays a FAIL."""
    lines = parse_bincheck(text)
    if not lines:
        return harness(f"bincheck.sh {check}: no result lines (exit {rc}): " + tail(text))
    needs, fails, extra, found = {}, [], {}, {}
    for v, _, name, value, detail in lines:
        needs[name] = v
        if v == "FAIL":
            fails.append(f"{name}: {detail}")
        if value:
            found[name] = value
    if check == "version":
        extra["versions"] = found
        needs["agree"], why = agreement(commits or {}, found, ((baseline or {}).get("bin") or {}), sums or {})
        if needs["agree"] == "FAIL":
            fails.append("agree: " + why)
        elif needs["agree"] == "LIMIT":
            extra["limits"] = {"agree": why}
    if rc not in (0, 1):
        return harness(f"bincheck.sh {check}: exit {rc}: " + tail(text))
    return Outcome(needs, fails, extra)


def agreement(commits, found, binbase, sums):
    """(need, why) of gate:bin:version's agree: every OS's restic-bko from one restic
    commit, every rustic-bko from one rustic and rustic_core commit, one version per tool
    for one commit, and one bko-holt version.  A binary from another commit than the rest
    is a LIMIT only when baseline.json's bin.version-agree classes it by name and sha256."""
    groups = {}
    for name, c in (commits or {}).items():
        if name.endswith("/restic-bko"):
            ident = ("restic", c if isinstance(c, str) else (c or {}).get("commit"))
        elif name.endswith("/rustic-bko"):
            c = c if isinstance(c, dict) else {"commit": c}
            ident = ("rustic", f"{c.get('commit')}+{c.get('rustic_core')}")
        else:
            continue
        groups.setdefault(ident[0], {}).setdefault(ident[1], []).append(name)
    for tool in ("restic", "rustic"):
        by_commit = groups.get(tool, {})
        for ident, names in by_commit.items():
            vs = {found[n] for n in names if n in found}
            if len(vs) > 1:
                return "FAIL", f"{tool}-bko of one commit ({str(ident)[:12]}) hold different versions: " + \
                       ", ".join(sorted(vs))
    holt = {v for n, v in found.items() if n.startswith("holt/")}
    if len(holt) > 1:
        return "FAIL", "bko-holt's platforms hold different versions: " + ", ".join(sorted(holt))
    odd = []
    for tool, by in sorted(groups.items()):
        if len(by) > 1:
            main = max(sorted(by.items()), key=lambda kv: len(kv[1]))[0]
            odd += [(tool, n, ident) for ident, names in sorted(by.items()) if ident != main for n in sorted(names)]
    if not odd:
        return "PASS", "one commit per tool"
    classed = {(e.get("binary"), e.get("sha256")): e for e in binbase.get("version-agree") or []}
    whys, unclassed = [], []
    for tool, n, ident in odd:
        e = classed.get((n, sums.get(n)))
        if e:
            whys.append(f"{n}: {e.get('why', 'classed in baseline.json')}")
        else:
            unclassed.append(f"{n} is {tool} {ident[:12]}, unlike the others")
    if unclassed:
        return "FAIL", "a mixed set: " + "; ".join(unclassed)
    return "LIMIT", "; ".join(whys)


def loop_outcome(summary, rc, info=None, runall_out=""):
    """A loop layer: one need per run-all.sh row of summary.json (STEP[/TARGET][/DIRECTION]);
    a SKIP row is a SKIP when run-all.sh allowed it, else a FAIL (verdict.py's rule)."""
    if not isinstance(summary, dict) or "rows" not in summary:
        return harness(f"run-all.sh exit {rc}, no summary.json: " + tail(runall_out, 3))
    needs, fails = {}, []
    for r in summary["rows"]:
        parts = [str(r.get("step") or r.get("platform") or "?")]
        if r.get("target"):
            parts.append(str(r["target"]).replace(" ", "_"))
        if r.get("direction"):
            parts.append(str(r["direction"]))
        base = nm = "/".join(parts)
        i = 2
        while nm in needs:
            nm, i = f"{base}#{i}", i + 1
        v = r.get("verdict")
        if v == "PASS":
            needs[nm] = "PASS"
        elif v == "SKIP" and r.get("allowed"):
            needs[nm] = "SKIP"
        else:
            needs[nm] = "FAIL"
            checks = [c for c in (r.get("checks") or []) if c]
            fails.append(f"{nm}: {v}" + (": " + "; ".join(checks[:2]) if checks else ""))
    if not needs:
        return harness("summary.json has no rows")
    extra = {"rows": len(summary["rows"]),
             "run_all": {k: summary.get(k) for k in ("host", "platforms", "wall", "id") if k in summary}}
    for k in ("notes", "lost"):
        if summary.get(k):
            extra[k] = summary[k][:5]
    peak = int(info["ram_peak_kib"]) if info and str(info.get("ram_peak_kib", "")).isdigit() else None
    if peak is not None:
        extra["peaks"] = {"ram_kib": peak}
    if rc not in (0, 1):
        return harness(f"run-all.sh exit {rc}: " + tail(runall_out, 3), extra=extra, ram_peak_kib=peak)
    return Outcome(needs, fails, extra, ram_peak_kib=peak)


def selftest_outcome(results):
    """gate:selftest: {suite: (rc, output)}, PASS by exit 0 (rc None: not there, a FAIL:
    both self-tests are in the tree)."""
    needs, fails = {}, []
    for suite, (rc, text) in results.items():
        if rc is None:
            needs[suite] = "FAIL"
            fails.append(f"{suite}: not run: " + tail(text, 1))
            continue
        needs[suite] = "PASS" if rc == 0 else "FAIL"
        if rc != 0:
            fails.append(f"{suite}: exit {rc}: " + tail(text, 3))
    return Outcome(needs, fails)


def tail(text, n=2):
    lines = [l for l in (text or "").splitlines() if l.strip()]
    return cut(" | ".join(lines[-n:]) or "(no output)", 160)


def version_unchecked(o, commits, exp):
    """gate:bin:version after bincheck.sh: every set binary the facts or the expectations
    name must have a PASS or FAIL line, and no set binary may SKIP (no expected version):
    an unchecked binary fails, never a SKIP.  bko-holt (holt/*) may SKIP: not built yet."""
    if o.verdict == "HARNESS":
        return o
    for n in sorted(set(commits or {}) | set(exp or {})):
        if o.needs.get(n) in (None, "SKIP"):
            o.needs[n] = "FAIL"
            if n not in (exp or {}):
                c = commits[n] if isinstance(commits[n], str) else (commits[n] or {}).get("commit")
                o.fails.append(cut(f"{n}: no expected version: its commit {str(c)[:12]} isn't in BKO_GIT"))
            else:
                o.fails.append(cut(f"{n}: bincheck.sh gave it no result line"))
    for n, v in sorted(o.needs.items()):
        if v == "SKIP" and not n.startswith("holt/") and n != "agree":
            o.needs[n] = "FAIL"
            o.fails.append(cut(f"{n}: no expected version (no commit file in the set), so it isn't checked"))
    o.fails = o.fails[:20]
    o.verdict = verdict_of(o.needs)
    return o
