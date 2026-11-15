"""t2 batch's output (docs/design/batch.md §5, §7, §11.1's summary, diff and report; piece B5):
t2lib/batchreport.py's rules from fixture entries and ledger rows, and t2lib/batchout.py's
summary, log fetch (through a fake ssh that runs here: no host), report and log commands over a
fixture batch dir.  The retry shapes of §6.5 too."""

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
TESTVM = os.path.dirname(T2)
FAKE_SSH = os.path.join(HERE, "fixtures", "batch", "fake-ssh")
GOLDEN = os.path.join(HERE, "fixtures", "batch", "golden")
sys.path.insert(0, T2)

from t2lib import batch as B          # noqa: E402
from t2lib import batchfile as bf     # noqa: E402
from t2lib import batchout as bo      # noqa: E402
from t2lib import batchreport as br   # noqa: E402
from t2lib import ledger              # noqa: E402


def rd(p):
    with open(p) as f:
        return f.read()


def row(cell, verdict="PASS", klass=None, run="t2h-20261115T0401-3cd2", attempt=1, host="chopin", fails=(), t="2026-11-15T04:10:00Z",
        kind="run", valid=True, committed=True, logs=None, rid=None, gm=1.0, wall=100):
    klass = klass or {"PASS": "pass", "FAIL": "fail", "FLAKY-FAIL": "fail", "HARNESS": "harness", "LOST": "lost"}[verdict]
    import hashlib
    h = hashlib.sha256(repr((cell, attempt, kind, run, verdict, host, t)).encode()).hexdigest()[:8]
    r = {"v": 1, "id": rid or f"{host}-{t.replace(':', '').replace('-', '')}-{h}",
         "cell": cell, "verdict": verdict, "class": klass, "run": run, "attempt": attempt, "host": host,
         "fails": list(fails), "t": t, "kind": kind, "valid": valid, "committed": committed, "logs": logs,
         "gm": gm, "wall_s": wall, "family": cell.split(":")[0], "origin": f"o:{cell}:{attempt}:{kind}"}
    r["c"] = ledger.row_c(r)
    return r


def ent(eid, kind="t2", status="FAIL", run="t2h-20261115T0401-3cd2", plan=(), tool=(), **k):
    d = {"id": eid, "kind": kind, "status": status, "exit": 1 if status == "FAIL" else 0, "run": run,
         "match": bo.run_match(kind, run), "plan": list(plan), "tool": list(tool), "log": f"entries/{eid}/entry.log",
         "wall_s": 600}
    d.update(k)
    return d


class Summary(unittest.TestCase):
    def test_every_verdict_and_class(self):
        rows = [row("holt:a"), row("holt:b", "FAIL", fails=["verify: 3 files differ"]),
                row("holt:c", "FAIL", attempt=1, fails=["first"]), row("holt:c", "PASS", "pass", attempt=2, t="2026-11-15T04:20:00Z"),
                row("holt:d", "FLAKY-FAIL", fails=["passed on its rerun"]), row("holt:e", "HARNESS", fails=["no answer"]),
                row("holt:f", "LOST", fails=["host rebooted"]), row("holt:g", "PASS", "known-flake"),
                row("holt:h", "PASS", valid=False), row("holt:i", "PASS", committed=False),
                row("holt:x", run="t2h-20261115T0401-3cd2-r2", fails=[]),
                row("holt:other", run="t2h-20261114T0000-aaaa")]           # another run: not this entry's
        plan = [{"cell": c, "status": "due"} for c in ("holt:a", "holt:b", "holt:c", "holt:lost")] + \
            [{"cell": "holt:tc", "status": "to-class", "reasons": ["2 new lines"]},
             {"cell": "holt:cur", "status": "current"}]
        out = {r["cell"]: r for r in br.summary_rows([ent("003-t2-holt", plan=plan)], rows)}
        self.assertEqual(out["holt:a"]["verdict"], "PASS")
        self.assertEqual((out["holt:b"]["verdict"], out["holt:b"]["class"], out["holt:b"]["reason"]),
                         ("FAIL", "fail", "verify: 3 files differ"))
        self.assertEqual((out["holt:c"]["verdict"], out["holt:c"]["attempt"]), ("PASS", 2))   # the decisive row
        self.assertEqual(out["holt:d"]["class"], "fail")
        self.assertEqual(out["holt:e"]["class"], "harness")
        self.assertEqual(out["holt:f"]["class"], "lost")
        self.assertEqual(out["holt:g"]["class"], "known-flake")
        self.assertIn("invalid row", out["holt:h"]["reason"])
        self.assertIn("uncommitted", out["holt:i"]["reason"])
        self.assertIn("holt:x", out)                                         # RUN-r2: an attempt of the run
        self.assertNotIn("holt:other", out)
        self.assertNotIn("holt:cur", out)                                    # not due: not this batch's
        self.assertEqual((out["holt:lost"]["verdict"], out["holt:lost"]["class"]), ("LOST", "lost"))
        self.assertEqual((out["holt:tc"]["verdict"], out["holt:tc"]["class"]), ("TO-CLASS", "to-class"))
        self.assertEqual(list(out)[:4], ["holt:a", "holt:b", "holt:c", "holt:lost"])   # the plan's order first

    def test_not_run_when_the_entry_did_not_finish(self):
        out = br.summary_rows([ent("e", status="INCOMPLETE", plan=[{"cell": "holt:a", "status": "due"}])], [])
        self.assertEqual((out[0]["verdict"], out[0]["class"]), ("NOT-RUN", "-"))

    def test_class_row_reclasses(self):
        rows = [row("guest:k", "FAIL", fails=["logs: 3 lines"]),
                row("guest:k", "PASS", "pass", kind="class", t="2026-11-15T05:00:00Z", rid="mac-x-class")]
        out = br.summary_rows([ent("e", run="t2h-20261115T0401-3cd2")], rows)
        self.assertEqual((out[0]["verdict"], out[0]["row"]), ("PASS", "mac-x-class"))

    def test_cmd_and_cell_less_entries(self):
        out = br.summary_rows([ent("006-cmd-x", kind="cmd", status="PASS", run=None),
                               ent("007-cmd-y", kind="cmd", status="FAIL", run=None, exit=3),
                               ent("008-t2-gate", status="REFUSED", run=None, note="Busy")], [])
        self.assertEqual([(r["cell"], r["verdict"], r["class"], r["host"]) for r in out],
                         [("-", "PASS", "cmd", "mac"), ("-", "FAIL", "cmd", "mac"), ("-", "REFUSED", "-", "-")])
        self.assertEqual(out[0]["log"], "entries/006-cmd-x/entry.log")
        self.assertEqual(out[2]["reason"], "Busy")

    def test_long_reason_and_tab(self):
        long = "x" * 300
        out = br.summary_rows([ent("e")], [row("holt:a", "FAIL", fails=[long]), row("holt:b", "FAIL", fails=["a\tb\nc"])])
        a, b = out
        self.assertEqual(len(a["reason"]), br.REASON_MAX)
        self.assertEqual(b["reason"], "a b c")
        text = br.to_tsv(out)
        for ln in text.splitlines():
            self.assertEqual(len(ln.split("\t")), len(br.HEADER), ln)
        back = br.parse_tsv(text)
        self.assertEqual([r["cell"] for r in back], ["holt:a", "holt:b"])
        self.assertEqual(back[1]["reason"], "a b c")
        self.assertTrue(text.startswith("#entry\tcell\tverdict\tclass\tsince\thost\tattempt\tgm\twall_s\trun\trow\tlog"))

    def test_tool_rows_when_the_ledger_has_none(self):
        tool = [{"cell": "holt:debian13", "verdict": "FAIL", "host": "cage", "reason": "ht1 job exit 1"},
                {"cell": "holt:arch", "verdict": "LOST", "class": "lost", "reason": "left in the todo"}]
        plan = [{"cell": "holt:debian13", "status": "due"}, {"cell": "holt:arch", "status": "due"},
                {"cell": "holt:alpine324", "status": "due"}]
        rows = [row("holt:alpine324", run="h1a-9ab1")]
        out = {r["cell"]: r for r in br.summary_rows([ent("e", kind="ht1", run="h1a-9ab1", plan=plan, tool=tool)], rows)}
        self.assertEqual(out["holt:alpine324"]["verdict"], "PASS")          # the ledger first
        self.assertEqual((out["holt:debian13"]["verdict"], out["holt:debian13"]["host"]), ("FAIL", "cage"))
        self.assertEqual(out["holt:arch"]["class"], "lost")


class Since(unittest.TestCase):
    def r(self, cell, verdict, reason="", host="chopin", entry="e"):
        return {"entry": entry, "cell": cell, "verdict": verdict, "class": "fail" if verdict != "PASS" else "pass",
                "host": host, "reason": reason}

    def test_new_known_fixed_same(self):
        prev = [self.r("a", "FAIL", "boom: x"), self.r("b", "FAIL", "old reason"), self.r("c", "FAIL", "z"),
                self.r("d", "PASS"), self.r("h", "FAIL", "same head", host="cage")]
        rows = [self.r("a", "FAIL", "boom: x"), self.r("b", "FAIL", "new reason"), self.r("c", "PASS"),
                self.r("d", "PASS"), self.r("e", "FAIL", "absent then"), self.r("f", "PASS"),
                self.r("h", "FAIL", "same head", host="cinqcent")]
        got = {r["cell"]: r["since"] for r in br.diff(rows, prev)}
        self.assertEqual(got, {"a": "known", "b": "new", "c": "fixed", "d": "same", "e": "new", "f": "same",
                               "h": "known"})

    def test_reason_head_is_60_characters(self):
        prev = [self.r("a", "FAIL", "x" * 60 + " then one thing")]
        rows = [self.r("a", "FAIL", "x" * 60 + " then another")]
        self.assertEqual(br.diff(rows, prev)[0]["since"], "known")

    def test_no_baseline(self):
        rows = br.diff([self.r("a", "FAIL", "x"), self.r("b", "PASS")], None)
        self.assertEqual([r["since"] for r in rows], ["new", "same"])

    def test_same_entry_first(self):
        prev = [self.r("a", "PASS", entry="e1"), self.r("a", "FAIL", "x", entry="e2")]
        rows = br.diff([self.r("a", "FAIL", "x", entry="e2")], prev)
        self.assertEqual(rows[0]["since"], "known")


class Report(unittest.TestCase):
    META = {"batch": "bt-20261115T0312-9ab1", "file": "/x/carpetbeater.batch", "hosts": ["chopin", "cage"],
            "started": "2026-11-15T03:12Z", "ended": "2026-11-15T12:17Z", "exit": 1, "wall_s": 32700,
            "manual": ["step 1: refresh"], "stops": []}

    def rows(self, n_groups, per=3):
        out = []
        for g in range(n_groups):
            for i in range(per):
                out.append({"entry": "003-t2-sweeptest2-rel6", "cell": f"holt:g{g}c{i}", "verdict": "FAIL",
                            "class": "fail" if g % 3 else "harness", "since": "new" if g % 2 else "known",
                            "host": ["chopin", "cage"][i % 2], "row": f"r{g}{i}", "reason": f"group {g}: what broke",
                            "log": "-"})
        out.append({"entry": "002-t2-gate", "cell": "gate:holt", "verdict": "PASS", "class": "pass", "since": "fixed",
                    "host": "chopin", "row": "rp", "reason": "", "log": "-"})
        return out

    def test_groups(self):
        gs = br.groups(self.rows(4))
        self.assertEqual(len(gs), 4)
        self.assertEqual([g["class"] for g in gs], ["fail", "fail", "harness", "harness"])
        self.assertEqual(gs[0]["count"], 3)
        self.assertEqual(sorted(gs[0]["hosts"]), ["cage", "chopin"])
        self.assertIn("new", gs[0]["since"])                       # new fail groups before known ones
        self.assertEqual(gs[0]["rep"]["cell"], "holt:g1c0")

    def test_render_and_cap(self):
        ents = [{"id": "002-t2-gate", "status": "PASS", "exit": 0, "wall_s": 600},
                {"id": "003-t2-sweeptest2-rel6", "status": "FAIL", "exit": 1, "wall_s": 22000},
                {"id": "005-bs2-x", "status": "INCOMPLETE", "exit": None, "wall_s": None, "note": "interrupted"}]
        rep = br.report(self.META, ents, self.rows(3), "bt-20261101T0300-0001")
        text = br.render(rep)
        self.assertIn("failing groups: 3", text)
        self.assertIn("unfinished 005-bs2-x: INCOMPLETE", text)
        self.assertIn("diff vs bt-20261101T0300-0001: new 3, fixed 1, still failing (known) 6", text)
        self.assertIn("manual (not run): step 1: refresh", text)
        big = br.render(br.report(self.META, ents, self.rows(200, 2), None))
        self.assertLessEqual(len(big.splitlines()), br.REPORT_MAX)
        self.assertIn("more groups", big)
        tight = br.render(br.report(self.META, ents, self.rows(200, 2), None), cap=20)
        self.assertLessEqual(len(tight.splitlines()), 20)

    def test_golden(self):
        ents = [{"id": "002-t2-gate", "status": "PASS", "exit": 0, "wall_s": 600},
                {"id": "003-t2-sweeptest2-rel6", "status": "FAIL", "exit": 1, "wall_s": 22000}]
        rep = br.report(self.META, ents, self.rows(4), "bt-20261101T0300-0001")
        got = br.render(rep) + "\n----\n" + br.render_analyse(rep, "## Analysing a batch\n\n1. `t2 batch report <BATCH>`.\n",
                                                             self.META["batch"])
        path = os.path.join(GOLDEN, "report.txt")
        if os.environ.get("T2_GOLDEN") == "bless" or not os.path.exists(path):
            os.makedirs(GOLDEN, exist_ok=True)
            with open(path, "w") as f:
                f.write(got)
        with open(path) as f:
            self.assertEqual(got, f.read())

    def test_analyse(self):
        rows = [dict(r, **{"class": "fail", "since": "new"}) for r in self.rows(25, 1)]
        rep = br.report(self.META, [], rows, None)
        text = br.render_analyse(rep, "## Analysing a batch\n\nstep `t2 batch report <BATCH>`\n", "bt-X")
        self.assertIn("# Analysing batch bt-X", text)
        self.assertIn("step `t2 batch report bt-X`", text)
        self.assertEqual(text.count("`t2 batch log bt-X"), br.LOG_GROUPS)    # at most ten logs named
        self.assertIn("more new `fail` groups", text)
        self.assertIn("step 1: refresh", text)


class Retry(unittest.TestCase):
    def test_shapes(self):
        f = lambda v, k: {"cell": "c", "verdict": v, "class": k}   # noqa: E731
        self.assertTrue(B.retry_shaped("ERROR"))
        self.assertFalse(B.retry_shaped("FAIL"))
        self.assertFalse(B.retry_shaped("FAIL", [f("FAIL", "fail"), f("FAIL", "fail")]))
        self.assertTrue(B.retry_shaped("FAIL", [f("FAIL", "fail"), f("LOST", "lost")]))       # half
        self.assertTrue(B.retry_shaped("FAIL", [f("HARNESS", "harness"), f("PASS", "pass")]))
        self.assertFalse(B.retry_shaped("FAIL", [f("FAIL", "fail"), f("FAIL", "fail"), f("LOST", "lost")]))
        self.assertFalse(B.retry_shaped("PASS", [f("PASS", "pass")]))
        self.assertFalse(B.retry_shaped("REFUSED", [f("LOST", "lost")]))

    def test_run_names(self):
        e = bf.parse("ht1 h1a arch\nstic1 rel6 alpine324\n", "t.batch").entries
        self.assertEqual(B.run_name(e[0], "bt-20261115T0312-9ab1", 1), "h1a-9ab1")
        self.assertEqual(B.run_name(e[0], "bt-20261115T0312-9ab1", 2), "h1a-9ab1r2")
        self.assertEqual(B.run_name(e[1], "bt-20261115T0312-9ab1", 1), "st1-rel6-9ab1")


class Match(unittest.TestCase):
    def test_run_match(self):
        import re
        m = lambda k, r, x: bool(re.fullmatch(bo.run_match(k, r), x))   # noqa: E731
        self.assertTrue(m("t2", "t2h-20261115T0401-3cd2", "t2h-20261115T0401-3cd2-r2"))
        self.assertFalse(m("t2", "t2h-20261115T0401-3cd2", "t2h-20261115T0401-3cd3"))
        self.assertTrue(m("t2", "t2i-20261115T0401-3cd2", "t2h-20261115T0401-3cd2-img"))
        for child in ("t2s-20261115T0401-3cd2", "t2h-20261115T0401-3cd2-r2", "t2g-20261115T0401-3cd2"):
            self.assertTrue(m("t2", "t2w-20261115T0401-3cd2", child), child)
        self.assertFalse(m("t2", "t2w-20261115T0401-3cd2", "t2s-20261115T0402-3cd2"))
        self.assertTrue(m("bs2", "bs2a-20261115-0730", "bs2a-20261115-0730-t0"))
        self.assertTrue(m("ht1", "h1a-9ab1", "h1a-9ab1"))
        self.assertFalse(m("ht1", "h1a-9ab1", "h1a-9ab1r2"))
        self.assertIsNone(bo.run_match("t2", None))


class Dir(unittest.TestCase):
    """batchout over a fixture batch dir in a temporary MAC_ROOT, the ledger as fixture rows in
    MAC_ROOT/ledger/chopin.jsonl, and the log fetch through the fake ssh (it runs here)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2batchout-")
        self.root = os.path.join(self.tmp, "mac")
        self.old = {k: os.environ.get(k) for k in ("T2_MAC_ROOT", "T2_SSH", "T2_BATCH_PULL", "FAKE_SSH_LOG",
                                                   "T2_BATCH_FETCH_TRIES")}
        os.environ.update(T2_MAC_ROOT=self.root, T2_SSH=FAKE_SSH, T2_BATCH_PULL="0",
                          FAKE_SSH_LOG=os.path.join(self.tmp, "ssh.log"), T2_BATCH_FETCH_TRIES="1")
        self.logs = os.path.join(self.tmp, "host", "records")      # "the host's" records (fake ssh: here)
        os.makedirs(os.path.join(self.logs, "holt-b"))
        with open(os.path.join(self.logs, "holt-b", "run.out"), "w") as f:
            f.write("".join(f"line {i}\n" for i in range(1000)))
        with open(os.path.join(self.logs, "holt-b", "verdict.txt"), "w") as f:
            f.write("FAIL: verify\n")

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make(self, text, states, bid="bt-20261115T0312-9ab1", runs=None, created=1000.0, ended=2000.0, write_runs=True):
        b = bf.parse(text, os.path.join(self.tmp, "c.batch"))
        d = os.path.join(self.root, "batches", bid)
        os.makedirs(os.path.join(d, "entries"))
        doc = {"v": 1, "id": bid, "file": os.path.join(self.tmp, "c.batch"), "created": created,
               "hosts": ["chopin", "cage"], "batch": b.to_json(), "prev": None}
        with open(os.path.join(d, "batch.json"), "w") as f:
            json.dump(doc, f)
        st = {"entries": {}, "exit": 1, "ended": ended}
        for e in b.entries:
            s = dict(states.get(e.id) or {"status": "PASS"}, started=created + 10, ended=ended - 10)
            st["entries"][e.id] = s
            os.makedirs(os.path.join(d, "entries", e.id))
            if (runs or {}).get(e.id):
                with open(os.path.join(d, "entries", e.id, "run"), "w") as f:
                    f.write(runs[e.id] + "\n")
        with open(os.path.join(d, "state.json"), "w") as f:
            json.dump(st, f)
        return d, b

    def ledger(self, rows):
        os.makedirs(os.path.join(self.root, "ledger"), exist_ok=True)
        with open(os.path.join(self.root, "ledger", "chopin.jsonl"), "a") as f:
            for r in rows:
                f.write(json.dumps(r, sort_keys=True) + "\n")

    def plan(self, run, cells):
        rd = os.path.join(self.root, "runs", run)
        os.makedirs(rd, exist_ok=True)
        with open(os.path.join(rd, "plan.json"), "w") as f:
            json.dump({"cells": [{"cell": c, "status": "due"} for c in cells]}, f)

    def test_build_fetch_report_and_log(self):
        run = "t2h-20261115T0401-3cd2"
        d, _ = self.make("t2 HoltTest2\ncmd ok -- true\n", {"001-t2-holttest2": {"status": "FAIL", "exit": 1}},
                         runs={"001-t2-holttest2": run})
        self.plan(run, ["holt:a", "holt:b", "holt:c"])
        self.ledger([row("holt:a", run=run), row("holt:b", "FAIL", run=run, fails=["verify: 3 files differ"],
                                                   logs=f"chopin:{self.logs}/holt-b")])
        text = bo.finish(d, self.root)
        rows = br.parse_tsv(rd(os.path.join(d, "summary.tsv")))
        by = {r["cell"]: r for r in rows}
        self.assertEqual(set(by), {"holt:a", "holt:b", "holt:c", "-"})
        self.assertEqual(by["holt:c"]["verdict"], "LOST")
        self.assertEqual(by["holt:b"]["log"], "logs/001-t2-holttest2/holt-b")
        self.assertEqual(by["holt:a"]["log"], "-")                          # a PASS keeps no log
        fetched = os.path.join(d, by["holt:b"]["log"])
        self.assertTrue(os.path.exists(os.path.join(fetched, "FETCH.txt")))
        self.assertTrue(os.path.exists(os.path.join(fetched, "records", "run.out")))
        self.assertIn("failing groups: 2", text)
        self.assertTrue(os.path.exists(os.path.join(d, "ANALYSE.md")))
        self.assertIn(f"t2 batch log {os.path.basename(d)} 001-t2-holttest2:holt-b", rd(os.path.join(d, "ANALYSE.md"))
                      .replace("holt:b", "holt-b"))
        # the log command: the cell's main file, its tail
        r = self.cli("log", os.path.basename(d), "001-t2-holttest2:holt:b", "--tail", "5")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("# 001-t2-holttest2 holt:b: FAIL", r.stdout)
        self.assertIn("line 999", r.stdout)
        self.assertNotIn("line 990\n", r.stdout)
        self.assertIn("lines above", r.stdout)
        r = self.cli("log", os.path.basename(d), "holt-b", "--list")
        self.assertIn("records/verdict.txt", r.stdout)
        r = self.cli("log", os.path.basename(d), "holt:b", "--tail", "100000")
        self.assertLessEqual(len(r.stdout.splitlines()), 400 + 6)          # never more than 400 lines of log
        r = self.cli("report", os.path.basename(d))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("failing groups: 2", r.stdout)
        j = json.loads(self.cli("report", os.path.basename(d), "--json").stdout)
        self.assertEqual(j["verdicts"], {"PASS": 2, "FAIL": 1, "LOST": 1})
        self.assertEqual(self.cli("log", os.path.basename(d), "nosuch").returncode, 2)

    def cli(self, *args):
        return subprocess.run([sys.executable, "-B", os.path.join(T2, "t2"), "batch"] + list(args), capture_output=True,
                              text=True, env=dict(os.environ), timeout=60, cwd=self.tmp)

    def test_prev_and_since(self):
        run1, run2 = "t2h-20261114T0401-0001", "t2h-20261115T0401-0002"
        d1, _ = self.make("t2 HoltTest2\n", {"001-t2-holttest2": {"status": "FAIL"}}, bid="bt-20261114T0312-0001",
                          runs={"001-t2-holttest2": run1}, created=1000.0)
        self.plan(run1, ["holt:a", "holt:b", "holt:c"])
        self.ledger([row("holt:a", "FAIL", run=run1, fails=["boom"]), row("holt:b", "FAIL", run=run1, fails=["old"]),
                     row("holt:c", run=run1)])
        bo.build(d1, self.root, fetch=False)
        d2, _ = self.make("t2 HoltTest2\n", {"001-t2-holttest2": {"status": "FAIL"}}, bid="bt-20261115T0312-0002",
                          runs={"001-t2-holttest2": run2}, created=5000.0)
        self.plan(run2, ["holt:a", "holt:b", "holt:c"])
        self.ledger([row("holt:a", "FAIL", run=run2, fails=["boom"], host="cage"), row("holt:b", "PASS", run=run2),
                     row("holt:c", "FAIL", run=run2, fails=["new"])])
        rows, rep = bo.build(d2, self.root, fetch=False)
        self.assertEqual({r["cell"]: r["since"] for r in rows}, {"holt:a": "known", "holt:b": "fixed", "holt:c": "new"})
        self.assertEqual(rep["prev"], os.path.basename(d1))
        self.assertEqual(rd(os.path.join(d2, "prev")).strip(), os.path.basename(d1))
        # --prev another baseline (none of it failing): this print only
        r = self.cli("report", os.path.basename(d2), "--prev", os.path.basename(d2))
        self.assertIn("diff vs", r.stdout)
        self.assertEqual({r["cell"]: r["since"] for r in br.parse_tsv(rd(os.path.join(d2, "summary.tsv")))},
                         {"holt:a": "known", "holt:b": "fixed", "holt:c": "new"})

    def test_kind_records(self):
        d, b = self.make("ht1 h1a arch alpine324+bigger=3 debian13\nstic1 rel6 alpine324 debian13\n",
                         {"001-ht1-h1a-arch-alpine324+bigger-3-debian13": {"status": "FAIL"},
                          "002-stic1-rel6-alpine324-debian13": {"status": "FAIL"}},
                         runs={"001-ht1-h1a-arch-alpine324+bigger-3-debian13": "h1a-9ab1",
                               "002-stic1-rel6-alpine324-debian13": "st1-rel6-9ab1"})
        e1, e2 = b.entries
        ed1 = os.path.join(d, "entries", e1.id)
        with open(os.path.join(ed1, "ht1-done.tsv"), "w") as f:
            f.write("end_utc\thost\tgv\trc\td_gib\tm_gib\tport\twait_s\trun_s\n"
                    "2026-11-15T05:00:00Z\tcage\talpine324-bigger\t1\t3\t4\t2601\t0\t300\n")
        with open(os.path.join(ed1, "ht1-left.txt"), "w") as f:
            f.write("debian13\n")
        ed2 = os.path.join(d, "entries", e2.id)
        os.makedirs(os.path.join(ed2, "stic1"))
        with open(os.path.join(ed2, "stic1", "results.tsv"), "w") as f:
            f.write("guest\thost\texit\tpass\tfail\tlimit\tseconds\tlogdir\n"
                    "alpine324\tcage\t0\t10\t0\t1\t40\t/x/l1\ndebian13\tcopland\t1\t9\t1\t0\t50\t/x/l2\n")
        self.ledger([row("holt:arch", run="h1a-9ab1")])
        rows, _ = bo.build(d, self.root, fetch=False)
        by = {(r["entry"][:3], r["cell"]): r for r in rows}
        self.assertEqual(by[("001", "holt:arch")]["verdict"], "PASS")
        self.assertEqual((by[("001", "holt:alpine324+bigger")]["verdict"], by[("001", "holt:alpine324+bigger")]["host"]),
                         ("FAIL", "cage"))
        self.assertEqual(by[("001", "holt:debian13")]["class"], "lost")
        self.assertEqual(by[("002", "stic:alpine324")]["verdict"], "PASS")
        self.assertEqual((by[("002", "stic:debian13")]["verdict"], by[("002", "stic:debian13")]["log"]),
                         ("FAIL", "copland:/x/l2"))                              # not fetched: its host path

    def test_a_host_that_does_not_answer(self):
        run = "t2h-20261115T0401-3cd2"
        d, _ = self.make("t2 HoltTest2\n", {"001-t2-holttest2": {"status": "FAIL"}}, runs={"001-t2-holttest2": run})
        self.ledger([row("holt:b", "FAIL", run=run, fails=["x"], logs=f"chopin:{self.logs}/holt-b")])
        os.environ["FAKE_SSH_DOWN"] = "m@chopin.local"
        try:
            rows, _ = bo.build(d, self.root, fetch=True)
        finally:
            os.environ.pop("FAKE_SSH_DOWN", None)
        note = rd(os.path.join(d, rows[0]["log"], "FETCH.txt"))
        self.assertIn("no answer (ssh exit 255", note)

    def test_fetch_caps(self):
        many = os.path.join(self.tmp, "host", "many")
        os.makedirs(many)
        for i in range(80):
            with open(os.path.join(many, f"f{i:02d}.log"), "w") as f:
                f.write("x" * 1000)
        with open(os.path.join(many, "big.log"), "w") as f:
            f.write("HEAD\n" + "y" * 200000 + "\nTAIL\n")
        dest = os.path.join(self.tmp, "got")
        self.assertTrue(bo.fetch_one("chopin", many, dest, maxb=60000, maxf=20))
        files = [n for n in os.listdir(os.path.join(dest, "records"))]
        self.assertLessEqual(len(files), 19)                     # FETCH.txt counts as one
        total = sum(os.path.getsize(os.path.join(dest, "records", n)) for n in files)
        self.assertLessEqual(total, 60000 + 4096)
        note = rd(os.path.join(dest, "FETCH.txt"))
        self.assertIn("left behind: ", note)
        self.assertIn(many, note)
        dest2 = os.path.join(self.tmp, "got2")
        self.assertTrue(bo.fetch_one("chopin", os.path.join(many, "big.log"), dest2, maxb=10000, maxf=5))
        data = rd(os.path.join(dest2, "records", "big.log"))
        self.assertTrue(data.startswith("HEAD") and data.rstrip().endswith("TAIL"))
        self.assertIn("bytes cut here", data)

    def test_safe_extract(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as t:
            for name in ("../evil", "/abs", "ok/fine.txt"):
                ti = tarfile.TarInfo(name)
                ti.size = 2
                t.addfile(ti, io.BytesIO(b"hi"))
        dest = os.path.join(self.tmp, "x")
        os.makedirs(dest)
        self.assertEqual(bo.safe_extract(buf.getvalue(), dest), ["ok/fine.txt"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil")))


if __name__ == "__main__":
    unittest.main()
