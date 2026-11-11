"""U2.3 (the plan form's golden text and JSON, 3 scenarios), U2.5 (the report and its wear
lines), and runs (run.py: run ids, the family lock, preflight, the stop rules, reruns, LIMITs,
cells.json, leftovers, the nothing-due lifecycle with its carried rows).

T2_GOLDEN=bless rewrites tests/fixtures/p2/golden/*.  The fixture checkout copies real files
(drive.py among them), so its base commit and drive.py's wall estimates are masked."""

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, "fixtures", "p2"))

from t2lib import estimates, ledger, plan, report, run   # noqa: E402
import world                                 # noqa: E402

GOLDEN = os.path.join(HERE, "fixtures", "p2", "golden")
BLESS = os.environ.get("T2_GOLDEN") == "bless"


def mask(text, base):
    text = text.replace(f"base {base[:7]}", "base BASE")
    return re.sub(r"~\d+(\.\d+)? (min|s|h) wall", "~N wall", text)


def plan_json(p, base):
    j = p.to_json()
    j.pop("made")
    j["header"]["base"] = "BASE"
    j["totals"].pop("wall_s")
    for c in j["cells"]:
        c.pop("placed")
    return json.loads(json.dumps(j, sort_keys=True))


def _only(p, cell):
    q = plan.Plan(p.word, p.family)
    q.cells = [p.cell(cell)]
    return q


class Golden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t2p2-golden-")
        cls.w = world.World(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def golden(self, name, p):
        base = self.w.head()
        text, js = mask(p.render(), base), plan_json(p, base)
        tp, jp = os.path.join(GOLDEN, name + ".txt"), os.path.join(GOLDEN, name + ".json")
        if BLESS:
            os.makedirs(GOLDEN, exist_ok=True)
            with open(tp, "w") as f:
                f.write(text)
            with open(jp, "w") as f:
                json.dump(js, f, indent=1, sort_keys=True)
                f.write("\n")
        with open(tp) as f:
            self.assertEqual(text, f.read(), f"{name}.txt (T2_GOLDEN=bless rewrites it)")
        with open(jp) as f:
            self.assertEqual(js, json.load(f), f"{name}.json (T2_GOLDEN=bless rewrites it)")

    def test_stic_classes_and_carried(self):
        f1 = self.w.facts(round_commits=self.w.forks["c1"], round_tag="rel5")
        p1 = self.w.plan("SticTest2 rel4 plan", facts=f1)
        rows = self.w.seed(p1, age={"stic:freebsd15": 48, "stic:freebsd15-geli": 24, "stic:ghostbsd26-zfs": 30})
        f2 = self.w.facts(round_commits=self.w.forks["c2"], round_tag="rel6")
        self.golden("stic-b2-affected", self.w.plan("SticTest2 rel4 plan", rows, facts=f2))

    def test_holt_plan_visible(self):
        base = self.w.head()
        try:
            g0 = self.w.plan("GateTest2 plan")
            grows = self.w.seed(g0)
            h0 = self.w.plan("HoltTest2 plan")
            hrows = self.w.seed(h0)
            self.w.edit("holt/src/load/commands.rs", "// prints the plan, changed\n", commit=True)
            g = self.w.plan("GateTest2 plan", grows)
            gh = self.w.row(g.cell("gate:holt"), "chopin", plan_=g, extra={"plan_diff": {
                "v": 1, "corpus": 45, "invariants": {},
                "differ": {"mint22": {"stages": ["partition"], "lines_changed": 2},
                           "arch": {"stages": ["format"], "lines_changed": 1}}}})
            self.golden("holt-plan-diff", self.w.plan("HoltTest2 plan", hrows + grows + [gh]))
        finally:
            self.w.reset(base)

    def test_guest_nothing_due_and_seven_days(self):
        p = self.w.plan("GuestTest2 plan")
        rows = self.w.seed(p, extra={"try_ran": True}, needs={"identity": "PASS"})
        self.golden("guest-nothing-due", self.w.plan("GuestTest2 plan", rows))
        old = [r for r in rows if r["cell"] != "guest:arch"]
        old += self.w.seed(_only(p, "guest:arch"), age={"guest:arch": 200}, extra={"try_ran": True}, needs={"identity": "PASS"})
        self.golden("guest-7day", self.w.plan("GuestTest2 plan", old))

    def test_img_refused(self):
        self.golden("img-refused", self.w.plan("ImgTest2 plan"))

    def test_bko_combined(self):
        """Every child's plan, then one total by estimates.pack over all their due cells."""
        out, jobs, gm, gbs = [], [], 0.0, 0.0
        for text in ("GateTest2 plan", "SticTest2 rel4 plan", "HoltTest2 plan", "GuestTest2 plan", "ImgTest2 plan"):
            p = self.w.plan(text)
            out.append(mask(p.render(), self.w.head()))
            jobs += [(c.cell, c.est["wall_s"], 4, sorted(c.keys) or None) for c in p.due()]
            gm += sum(c.est["gm"] for c in p.due())
            gbs += sum(c.est.get("gb") or 0.0 for c in p.due())
        wall, _, left = estimates.pack(sorted(jobs), list(world.HOSTS))
        total = f"combined: {len(jobs)} due, {gm:.1f} gm, {wall} s wall, {gbs:.1f} GB, {left or 'none'} left over\n"
        text = "".join(out) + total
        tp = os.path.join(GOLDEN, "bko-combined.txt")
        if BLESS:
            with open(tp, "w") as f:
                f.write(text)
        with open(tp) as f:
            self.assertEqual(text, f.read())

    def test_gate_nothing_due(self):
        g0 = self.w.plan("GateTest2 plan")
        p = self.w.plan("GateTest2 plan", self.w.seed(g0))
        self.golden("gate-nothing-due", p)
        self.assertEqual(p.render().count("\n"), 2)   # one line, and the sweep line


class Report(unittest.TestCase):
    RR = {"run": "t2s-20261108T0500-abcd", "word": "SticTest2", "family": "stic", "copy": "f1ed549.d0123abcd",
          "hosts": ["chopin", "cinqcent"],
          "cells": [{"cell": "stic:debian12", "outcome": "ran", "verdict": "PASS", "rule": "changed:harness",
                     "reasons": ["harness:testvm/stictest1/debian12/inner.sh 0bdd..->db03.."], "gm": 1.95, "gb": 0.0,
                     "host": "chopin", "rows": ["chopin-20261108T050101Z-00000001"]},
                    {"cell": "stic:freebsd15", "outcome": "ran", "verdict": "FAIL", "rule": "class:B2",
                     "reasons": ["bin:freebsd/rustic-bko 1a8e..->c55e.."], "gm": 12.7, "gb": 2.5, "host": "cinqcent",
                     "rows": ["cinqcent-20261108T051501Z-00000002"]}],
          "stops": ["stop-os freebsd: need acl failed on stic:freebsd14, stic:freebsd15"],
          "wear": {"chopin": {"nvme0n1": {"model": "FAKE_NVME_2TB", "window_bytes": 410000000, "ours_bytes": 20000000,
                                          "today_bytes": 37200000000}},
                   "cinqcent": {"nvme0n1": {"model": "WD_Blue_SN770", "window_bytes": None, "ours_bytes": 2500000000,
                                            "today_bytes": 61000000000}}}}

    def test_each_cell_says_its_gb(self):
        rr = {"run": "x", "word": "SticTest2", "cells": [
            {"cell": "stic:a", "outcome": "ran", "verdict": "PASS", "gm": 1, "gb": None, "host": "chopin", "rows": ["r1"]},
            {"cell": "stic:b", "outcome": "ran", "verdict": "PASS", "gm": 1, "gb": None, "host": "chopin", "rows": ["r2"]}]}
        led = [{"id": "r1", "wear": {"stic:x:a": 1500000000, "stic:x:b": 500000000}}, {"id": "r2", "wear": {"stic:x:c": None}}]
        text = report.render_report(rr, None, led)
        self.assertRegex(text, r"stic:a .*2\.00 GB")
        self.assertRegex(text, r"stic:b .* - GB")
        self.assertNotIn("None GB", text)
        self.assertEqual(report.run_bytes(rr), {})     # the budget lines still sum the wear tags

    def test_wear_lines(self):
        lines = report.wear_lines(self.RR["wear"])
        self.assertEqual(lines[0], "chopin nvme0n1 (FAKE_NVME_2TB): run window 0.41 GB (diskstats, all writers), "
                                   "our jobs 0.02 GB (wear tags), today 37.20 GB (SMART)")
        self.assertIn("run window ? (diskstats", lines[1])
        self.assertEqual(report.wear_lines({}), ["wear: no disk figures (facts or wear logs unreachable)"])

    def test_budget_and_sweep_lines(self):
        pol = {"budget": {"run_gb": 2, "sweep_gb": 5, "day_gb": 50, "bind_from": None}}
        b = report.budget_lines(self.RR, pol, now=1794182400)
        self.assertEqual(b, ["budget: cinqcent wrote 2.50 GB in this run's jobs, over 2 GB (warn only (bind_from not set))"])
        self.assertEqual(report.budget_lines(self.RR, pol, sweep=True), [])
        # the day's budget counts Test2's jobs of the day (other runs' rows), never SMART's whole disk
        day = [{"t": "2026-11-09T01:00:00Z", "run": "t2h-20261109T0100-0001", "host": "cinqcent", "cell": "holt:x",
                "wear": {"ht1:t2h-20261109T0100-0001:x": 48 * 10 ** 9}},
               {"t": "2026-11-09T02:00:00Z", "run": "r4all", "host": "cinqcent", "cell": "holt:y",
                "wear": {"ht1:r4all:y": 90 * 10 ** 9}}]
        b = report.budget_lines(self.RR, pol, ledger=day, now=1794182400)
        self.assertEqual(b[-1], "budget: cinqcent: Test2's jobs wrote 50.5 GB today, over 50 GB a day "
                                "(warn only (bind_from not set))")
    def test_sweep_line(self):
        pol = {"sweep": {"days": 7, "then_days": 14, "shadow_days": 14}}
        now = 1794182400   # 2026-11-09T00:00:00Z
        self.assertEqual(report.sweep_line(pol, None, now), "sweep: SweepTest2 never ran, due now")
        last = {"t": "2026-11-05T00:00:00Z"}
        self.assertEqual(report.sweep_line(pol, last, now),
                         "sweep: SweepTest2 last ran 2026-11-05 (4 days ago), due in 3 days")
        # shadow days after the first complete sweep: then_days
        last = {"t": "2026-11-05T00:00:00Z", "first_t": "2026-10-20T00:00:00Z"}
        self.assertEqual(report.sweep_line(pol, last, now),
                         "sweep: SweepTest2 last ran 2026-11-05 (4 days ago), due in 10 days")
        last = {"t": "2026-11-05T00:00:00Z", "first_t": "2026-11-01T00:00:00Z"}
        self.assertTrue(report.sweep_line(pol, last, now).endswith("due in 3 days"))

    def test_budget_update_round(self):
        pol = {"budget": {"run_gb": 2, "update_gb": 40}}
        self.assertEqual(report.budget_lines(self.RR, pol, update=True, now=1794182400), [])

    def test_blocks_after_the_cell_lines(self):
        rr = dict(self.RR, blocks=["grades:\n  arch solid"])
        lines = report.render_report(rr, None, [], {"budget": {"run_gb": 2}}, now=1794182400).splitlines()
        i = lines.index("grades:")
        self.assertTrue(lines[i - 1].startswith("  "))
        self.assertTrue(lines[i + 2].startswith("stops:"))

    def test_report_lines(self):
        text = report.render_report(self.RR, None, [], {"budget": {"run_gb": 2}}, now=1794182400)
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("SticTest2 report: run t2s-20261108T0500-abcd"))
        self.assertIn("ran      stic:debian12", lines[1])
        self.assertIn("PASS", lines[1])
        self.assertIn("chopin-20261108T050101Z-00000001", lines[1])
        self.assertIn("FAIL", lines[2])
        self.assertIn("stops: stop-os freebsd", text)
        self.assertIn("rows: 2 written (chopin 1, cinqcent 1)", text)
        self.assertTrue(lines[-1].startswith("cinqcent nvme0n1 (WD_Blue_SN770)"))
        self.assertIn("sweep: SweepTest2 never ran, due now", text)
        self.assertNotRegex(text, r"serial")


def flip_row(i, run, verdict="PASS", needs=None, soft=None, plumbing=None, cell="stic:a", key="k", klass=None, valid=True,
             kind="run"):
    return {"id": f"h-{i}", "t": f"2026-11-0{i}T00:00:00Z", "run": run, "cell": cell, "key": key, "verdict": verdict,
            "kind": kind, "valid": valid, "needs": needs or {"x": "PASS"}, "class": klass or "pass",
            "inputs": {"soft": soft or {}, "plumbing": plumbing or {}}}


class Flips(unittest.TestCase):
    def one(self, a, b):
        got = report.verdict_flips([a, b], ["t2s-b"])
        self.assertEqual(len(got), 1, got)
        return got[0]

    def test_both_ways(self):
        f = self.one(flip_row(1, "t2s-a"), flip_row(2, "t2s-b", "FAIL", klass="fail"))
        self.assertEqual(f["kind"], "verdict")
        self.assertIn("new verdict at an unchanged key: stic:a PASS to FAIL (h-1, h-2)", f["text"])
        f = self.one(flip_row(1, "t2s-a", "FAIL", klass="fail"), flip_row(2, "t2s-b"))
        self.assertIn("FAIL to PASS", f["text"])

    def test_host_soft_and_guest_soft_wording(self):
        f = self.one(flip_row(1, "t2s-a", soft={"kernel": "ver:1"}), flip_row(2, "t2s-b", "FAIL", soft={"kernel": "ver:2"}))
        self.assertIn("host soft inputs differ (kernel)", f["text"])
        f = self.one(flip_row(1, "t2s-a", soft={"g:generation": "gen:" + "a" * 64}),
                     flip_row(2, "t2s-b", "FAIL", soft={"g:generation": "gen:" + "b" * 64}))
        self.assertIn("the fingerprint may miss an input", f["text"])
        self.assertEqual(f["guest_gens"], [("g", "a" * 64, "b" * 64)])
        f = self.one(flip_row(1, "t2s-a", plumbing={"p": "x"}), flip_row(2, "t2s-b", "FAIL", plumbing={"p": "y"}))
        self.assertIn("plumbing differs (p)", f["text"])
        f = self.one(flip_row(1, "t2s-a"), flip_row(2, "t2s-b", "FAIL"))
        self.assertIn("soft and plumbing inputs are the same", f["text"])

    def test_harness_on_its_own_line(self):
        f = self.one(flip_row(1, "t2s-a"), flip_row(2, "t2s-b", "HARNESS", klass="harness"))
        self.assertEqual(f["kind"], "harness")
        self.assertIn("harness result at an unchanged key", f["text"])

    def test_needs_quietly_and_flake_labelled(self):
        f = self.one(flip_row(1, "t2s-a"), flip_row(2, "t2s-b", needs={"y": "PASS"}))
        self.assertEqual(f["kind"], "needs")
        self.assertIn("needs differ", f["text"])
        f = self.one(flip_row(1, "t2s-a", "FAIL", klass="fail"), flip_row(2, "t2s-b", klass="known-flake"))
        self.assertIn("(known flake)", f["text"])

    def test_only_other_runs_valid_rows_at_that_key(self):
        self.assertEqual(report.verdict_flips([flip_row(1, "t2s-b"), flip_row(2, "t2s-b", "FAIL")], ["t2s-b"]), [])
        self.assertEqual(report.verdict_flips([flip_row(1, "t2s-a", key="o"), flip_row(2, "t2s-b", "FAIL")], ["t2s-b"]), [])
        self.assertEqual(report.verdict_flips([flip_row(1, "t2s-a", valid=False), flip_row(2, "t2s-b", "FAIL")], ["t2s-b"]), [])
        # an attempt of the run is the run
        self.assertEqual(report.verdict_flips([flip_row(1, "t2s-b-r2"), flip_row(2, "t2s-b", "FAIL")], ["t2s-b"]), [])

    def test_composite(self):
        pol = {"sweep": {"days": 7, "then_days": 14, "shadow_days": 14}}
        text = report.render_composite(
            [{"word": "SticTest2", "run": "t2s-x", "exit": 0, "text": "stic report\n", "gm": 3.0, "wall_s": 100, "gb": {"chopin": 0.5}},
             {"word": "HoltTest2", "run": "t2h-x", "exit": 1, "text": "holt report", "gm": 4.0, "wall_s": 200, "gb": {"chopin": 0.25, "cinqcent": 1}}],
            [{"text": "new verdict at an unchanged key: x", "pkgdiff": "package p 1 -> 2"}],
            {"word": "BkoTest2", "run": "t2b-x", "now": 1794182400}, ["stic: no check for manjaro", "coverage: not in any spec: z"], pol)
        lines = text.splitlines()
        self.assertEqual(lines[0], "BkoTest2 report: run t2b-x")
        self.assertIn("== SticTest2 t2s-x (exit 0)", lines)
        self.assertIn("total: 7.0 gm, ~5 min wall, chopin 0.75 GB, cinqcent 1.00 GB", lines)
        self.assertIn("new verdict at an unchanged key: x", lines)
        self.assertIn("  package p 1 -> 2", lines)
        self.assertIn("coverage: stic: no check for manjaro", lines)
        self.assertIn("coverage: not in any spec: z", lines)
        self.assertEqual(lines[-1], "sweep: SweepTest2 never ran, due now")


class Runs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t2p2-runs-")
        cls.w = world.World(cls.tmp)
        cls.mac = os.path.join(cls.tmp, "mac")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_run_ids(self):
        for fam, pre in (("gate", "t2g-"), ("stic", "t2s-"), ("holt", "t2h-")):
            r = run.new_run_id(fam)
            self.assertTrue(r.startswith(pre) and run.RUN_RE.match(r), r)
        self.assertTrue(run.RUN_RE.match("t2s-20261108T0315-4f2a-r2"))

    def test_new_families_ids(self):
        for fam, pre in (("guest", "t2v-"), ("img", "t2i-"), ("bko", "t2b-"), ("sweep", "t2w-")):
            r = run.new_run_id(fam)
            self.assertTrue(r.startswith(pre) and run.RUN_RE.match(r), r)
        self.assertEqual(run.new_run_id("stic", stamp="20261110T0312-4f2a"), "t2s-20261110T0312-4f2a")
        panel = run.new_run_id("holt", stamp="20261110T0312-4f2a", suffix="img")
        self.assertEqual(panel, "t2h-20261110T0312-4f2a-img")
        self.assertTrue(run.RUN_RE.match(panel))
        self.assertTrue(run.RUN_RE.match(panel + "-r2"))
        self.assertFalse(run.RUN_RE.match("t2x-20261110T0312-4f2a"))

    def test_family_locks(self):
        class W:
            update = False
        def lk(fam, update=False):
            w = W()
            w.family, w.update = fam, update
            return run.family_locks(w)
        self.assertEqual(lk("stic"), ["stic"])
        self.assertEqual(lk("img"), ["img", "holt"])
        self.assertEqual(lk("guest"), ["guest"])
        every = ["bko", "gate", "stic", "holt", "guest", "img"]
        self.assertEqual(lk("guest", update=True), every)
        self.assertEqual(lk("bko"), every)
        self.assertEqual(lk("sweep"), every)

    def test_acquire_locks_all_or_none(self):
        hold = run.FamilyLock("holt", self.mac).acquire("t2h-20261108T0315-4f2a")
        try:
            with self.assertRaises(run.Busy):
                run.acquire_locks(["img", "holt"], "x", self.mac, wait=0.1)
            self.assertFalse(run.FamilyLock("img", self.mac).held())
        finally:
            hold.release()
        got = run.acquire_locks(["img", "holt"], "x", self.mac, wait=0.1)
        self.assertTrue(run.FamilyLock("holt", self.mac).held())
        for lk in got:
            lk.release()

    def test_run_guards(self):
        pol = {"budget": {"run_gb": 2, "sweep_gb": 5, "update_gb": 40, "bind_from": None},
               "stop": {"os_need_guests": 2, "harness_results": 2, "swap_out_gib": 1, "write_budget_x": 2}}
        g = run.RunGuards(pol)
        a, b = run.StopEngine("stic", pol, guards=g), run.StopEngine("holt", pol, guards=g)
        self.assertEqual(a.feed({"cell": "stic:x", "verdict": "HARNESS", "class": "harness"}), "ok")
        self.assertFalse(g.tripped())
        self.assertEqual(b.feed({"cell": "holt:y", "verdict": "HARNESS", "class": "harness"}), "stop-run")
        self.assertTrue(g.tripped())
        self.assertFalse(a.may_start())
        # write bytes sum per host across the families; another family's trip stops this one's feed
        g = run.RunGuards(pol)
        a, b = run.StopEngine("stic", pol, guards=g), run.StopEngine("holt", pol, guards=g)
        self.assertEqual(a.feed_host("chopin", ours_bytes=int(3e9)), "ok")
        self.assertEqual(b.feed_host("chopin", ours_bytes=int(2e9)).split()[0], "stop-starts")
        self.assertEqual(g.host_bytes("chopin"), int(5e9))
        self.assertEqual(a.feed_host("chopin").split()[0], "stop-starts")
        # the same family's bytes are a running figure, not added up
        g = run.RunGuards(pol)
        g.feed_host("stic", "h", ours_bytes=int(3e9))
        g.feed_host("stic", "h", ours_bytes=int(3e9))
        self.assertFalse(g.tripped())
        # swap, one host
        g = run.RunGuards(pol)
        self.assertIn("swapped out", g.feed_host("stic", "h", swap_out_bytes=2 << 30))
        self.assertIsNone(g.feed_host("stic", "h", swap_out_bytes=2 << 30))     # says it once
        # a per-family stop leaves the guards alone
        g = run.RunGuards(pol)
        a = run.StopEngine("gate", pol, guards=g)
        a.feed({"cell": "gate:holt", "part": "fast", "verdict": "FAIL", "class": "fail"})
        self.assertTrue(a.stop_family)
        self.assertFalse(g.tripped())
        # to and from JSON
        g = run.RunGuards(pol)
        g.add_harness("c")
        g.feed_host("stic", "h", ours_bytes=7)
        g2 = run.RunGuards.from_json(g.to_json(), pol)
        self.assertEqual((g2.harness, g2.host_bytes("h"), g2.tripped()), (1, 7, False))

    def test_update_budget(self):
        pol = {"budget": {"run_gb": 2, "update_gb": 40, "bind_from": None}, "stop": {"write_budget_x": 2}}
        s = run.StopEngine("guest", pol, update=True)
        self.assertEqual(s.run_gb, 40.0)
        self.assertEqual(s.feed_host("h", ours_bytes=int(60e9)), "ok")

    def test_last_sweep(self):
        mac = os.path.join(self.tmp, "mac-sweep")
        self.assertIsNone(run.last_sweep(mac))
        for rid, t, ok in (("t2w-20261101T0000-aaaa", "2026-11-01T00:00:00Z", True),
                           ("t2w-20261105T0000-aaaa", "2026-11-05T00:00:00Z", True),
                           ("t2w-20261107T0000-aaaa", "2026-11-07T00:00:00Z", False)):
            run.write_json(os.path.join(run.run_dir(rid, mac), "sweep.json"), {"stamp": rid[4:], "complete": ok, "t": t})
        d = run.last_sweep(mac)
        self.assertEqual((d["t"], d["first_t"]), ("2026-11-05T00:00:00Z", "2026-11-01T00:00:00Z"))
        cut = "t2w-20261108T0000-aaaa"     # a test's --universe sweep: counts in its own root, never the default's
        run.write_json(os.path.join(run.run_dir(cut, mac), "sweep.json"),
                       {"stamp": cut[4:], "complete": True, "t": "2026-11-08T00:00:00Z", "universe": ["guest:arch"]})
        self.assertEqual(run.last_sweep(mac)["t"], "2026-11-08T00:00:00Z")
        from unittest import mock
        with mock.patch.object(run, "_is_default_root", lambda m=None: True):
            self.assertEqual(run.last_sweep(mac)["t"], "2026-11-05T00:00:00Z")

    def test_guest_and_img_flakes_come_from_their_own_family(self):
        ctx = self.w.ctx([])
        self.w.edit("testvm/test2/guest/flakes.json", json.dumps([{"guest": "*", "pattern": "x", "why": "w", "ref": "r"}]))
        p = self.w.plan("GuestTest2 arch plan")
        rs = run.RunState("t2v-20261108T0500-abcd", self.w.word("GuestTest2 arch", ctx), p, ctx, mac_root=self.mac, log=None)
        self.assertEqual(rs.flakes[0]["pattern"], "x")
        self.assertEqual(rs.limits, {})
        self.assertEqual(rs.attempt_run(1), "t2v-20261108T0500-abcd")
        self.assertEqual(rs.attempt_run(3), "t2v-20261108T0500-abcd-r3")

    def test_update_cells_have_no_planned_key(self):
        ctx = self.w.ctx([])
        w = self.w.word("GuestTest2 update arch", ctx)
        p = plan.make(w, ctx)
        rs = run.RunState("t2v-20261108T0500-abcd", w, p, ctx, mac_root=self.mac, log=None)
        self.assertIsNone(rs.cells_doc(p, "chopin")["cells"]["arch"]["planned_key"])

    def test_family_lock(self):
        a = run.FamilyLock("stic", self.mac).acquire("t2s-20261108T0315-4f2a")
        try:
            self.assertTrue(run.FamilyLock("stic", self.mac).held())
            with self.assertRaises(run.Busy) as cm:
                run.FamilyLock("stic", self.mac).acquire("t2s-20261108T0316-0000")
            self.assertIn("t2s-20261108T0315-4f2a", str(cm.exception))
            self.assertIn(str(os.getpid()), str(cm.exception))
            run.write_json(os.path.join(run.run_dir("t2s-20261108T0315-4f2a", self.mac), "plan.json"),
                           {"cells": [{"cell": "stic:debian12", "status": "due"},
                                      {"cell": "stic:debian13", "status": "current"}]})
            self.assertEqual(run.running_cells("stic", self.mac), {"stic:debian12": "t2s-20261108T0315-4f2a"})
            self.assertFalse(run.FamilyLock("holt", self.mac).held())
        finally:
            a.release()
        self.assertFalse(run.FamilyLock("stic", self.mac).held())
        self.assertEqual(run.running_cells("stic", self.mac), {})

    def test_preflight(self):
        f = self.w.facts()
        self.assertEqual(run.preflight(f, ["chopin", "cinqcent"]), [])
        f["chopin"]["preflight"]["tctl_c"] = 91.0
        f["cinqcent"]["preflight"]["ram"]["mounted"] = False
        f["cinqcent"]["preflight"]["kvm"] = False
        f["cinqcent"]["preflight"]["scratch_free_gb"] = 79.0
        out = run.preflight(f, ["chopin", "cinqcent", "nosuch"])
        self.assertEqual(out, ["chopin: Tctl 91.0 C (>= 90)", "cinqcent: no /mnt/bko-ram",
                               "cinqcent: m is not in group kvm", "cinqcent: /bkoscratch has 79 GB free (< 80)",
                               "nosuch: unreachable (no facts)"])

    def test_stop_rules(self):
        pol = {"stop": {"os_need_guests": 2, "harness_results": 2, "swap_out_gib": 1, "write_budget_x": 2},
               "budget": {"run_gb": 2, "bind_from": None}}
        s = run.StopEngine("stic", pol)
        self.assertEqual(s.feed({"cell": "gate:fork:restic", "part": "fast", "verdict": "FAIL", "class": "fail"}),
                         "stop-family")
        self.assertFalse(s.may_start())
        s = run.StopEngine("stic", pol)
        self.assertEqual(s.feed({"cell": "gate:loop:linux@chopin", "part": "slow", "verdict": "FAIL"}), "stop-starts")
        s = run.StopEngine("stic", pol)
        r1 = {"cell": "stic:freebsd15", "verdict": "FAIL", "class": "fail", "os": "freebsd", "needs": {"acl": "FAIL"}}
        self.assertEqual(s.feed(r1), "ok")
        self.assertEqual(s.feed(dict(r1, cell="stic:freebsd14")), "stop-os freebsd")
        self.assertFalse(s.may_start("freebsd"))
        self.assertTrue(s.may_start("linux"))
        s = run.StopEngine("stic", pol)
        h = {"cell": "stic:debian12", "verdict": "HARNESS", "class": "harness"}
        self.assertEqual(s.feed(h), "ok")
        self.assertEqual(s.feed(dict(h, cell="stic:x", flake={"guest": "x"})), "ok")    # a known flake: not counted
        self.assertEqual(s.feed(dict(h, cell="stic:debian13")), "stop-run")
        s = run.StopEngine("stic", pol)
        self.assertEqual(s.feed_host("chopin", swap_out_bytes=2 << 30), "stop-starts")
        s = run.StopEngine("stic", pol)
        self.assertEqual(s.feed_host("chopin", ours_bytes=3e9), "ok")         # over the budget: warn only
        self.assertEqual(s.feed_host("chopin", ours_bytes=4.1e9), "stop-starts")   # twice it: stop
        pol["budget"]["bind_from"] = "2026-11-22"
        s = run.StopEngine("stic", pol, now=1794182400)        # 2026-11-08: still warning only
        self.assertEqual(s.feed_host("chopin", ours_bytes=3e9), "ok")
        s = run.StopEngine("stic", pol, now=1794182400 + 15 * 86400)   # 2026-11-23: it binds
        self.assertEqual(s.feed_host("chopin", ours_bytes=3e9), "stop-starts")

    def test_flakes_limits(self):
        fl = [{"guest": "ghostbsd26-zfs", "pattern": r"FAIL boot: .*exit 255", "why": "w", "ref": "r"}]
        self.assertEqual(run.match_flake("ghostbsd26-zfs", "FAIL boot: ssh exit 255 after boot", fl), fl[0])
        self.assertIsNone(run.match_flake("freebsd15", "FAIL boot: ssh exit 255 after boot", fl))
        self.assertIsNone(run.match_flake("ghostbsd26-zfs", "FAIL acl: x", fl))
        needs, new = run.apply_limits({"a": "PASS", "b": "LIMIT", "c": "LIMIT"}, ["b"])
        self.assertEqual((needs, new), ({"a": "PASS", "b": "LIMIT", "c": "LIMIT-NEW"}, True))
        rows = [{"cell": "stic:x", "kind": "run", "verdict": "PASS", "t": "2026-11-01T00:00:00Z", "id": "a",
                 "needs": {"old": "LIMIT"}},
                {"cell": "stic:x", "kind": "run", "verdict": "PASS", "t": "2026-11-02T00:00:00Z", "id": "b",
                 "needs": {"p": "LIMIT", "q": "PASS"}}]
        self.assertEqual(run.limits_classed("stic:x", {"x": {"r": "why"}}, rows), ["p", "r"])

    def test_cells_json(self):
        p = self.w.plan("HoltTest2D plan", facts=self.w.facts())
        ctx = self.w.ctx([])
        wd = self.w.word("HoltTest2D", ctx)
        rs = run.RunState("t2h-20261108T0500-abcd", wd, p, ctx, mac_root=self.mac, host_root="/x/state", log=None)
        rs.copy = {"chopin": {"id": "abc1234", "path": "/c/abc1234", "commit": "a" * 40, "dirty": {}}}
        doc = rs.cells_doc(p, "chopin")
        self.assertEqual(sorted(doc["cells"]), ["debian13", "debian13-oneshot", "debian13-stack",
                                                "debian13-stack-bigger", "debian13-zfs"])
        e = doc["cells"]["debian13-stack-bigger"]
        self.assertEqual((e["cell"], e["attempt"], e["planned_key"], e["rule"]),
                         ("holt:debian13-stack+bigger", 1, p.cell("holt:debian13-stack+bigger").keys["chopin"],
                          "fresh:family:D"))
        self.assertEqual(doc["copy"]["id"], "abc1234")
        self.assertEqual(rs.host_run_dir("chopin"), "/x/state/runs/t2h-20261108T0500-abcd")
        r2 = rs.cells_doc(p, "chopin", attempt=2, reruns={"holt:debian13": {"rerun_of": "chopin-x", "flake": None}})
        self.assertEqual(list(r2["cells"]), ["debian13"])
        self.assertEqual((r2["cells"]["debian13"]["attempt"], r2["cells"]["debian13"]["rerun_of"]), (2, "chopin-x"))
        self.assertEqual(run.driver_id("gate:loop:linux@chopin"), "loop:linux")
        sp = self.w.plan("SticTest2 rel4 debian12 plan")
        sd = run.RunState("t2s-20261108T0500-abcd", self.w.word("SticTest2 rel4 debian12", ctx), sp, ctx,
                          mac_root=self.mac, log=None).cells_doc(sp, "cinqcent")
        self.assertEqual(sd["cells"]["debian12"]["limits_classed"], ["selinux"])

    def test_cells_json_gives_a_stic_cell_its_prev_needs(self):
        """The writer real runs use, not SticRun.entry: a need that vanishes must reach the judge."""
        ctx = self.w.ctx([])
        sp = self.w.plan("SticTest2 rel4 debian12 plan")
        rs = run.RunState("t2s-20261108T0500-abcd", self.w.word("SticTest2 rel4 debian12", ctx), sp, ctx,
                          mac_root=self.mac, log=None)
        g = {"harness": "h", "judge": "j"}
        pc = argparse.Namespace(cell="stic:x", family="stic", status="due", rule="r", reasons=[],
                                keys={"cinqcent": "k"}, res={"cinqcent": argparse.Namespace(groups=g)})
        plan = argparse.Namespace(cells=[pc])
        rs.ctx.ledger = [{"cell": "stic:x", "kind": "run", "verdict": "PASS", "valid": True, "t": "1",
                          "needs": {"b": "PASS", "a": "LIMIT"}, "groups": dict(g)}]
        self.assertEqual(rs.cells_doc(plan, "cinqcent")["cells"]["x"]["prev_needs"], ["b"])
        rs.ctx.ledger[0]["groups"] = dict(g, harness="edited")      # an edited check may change its needs
        self.assertIsNone(rs.cells_doc(plan, "cinqcent")["cells"]["x"]["prev_needs"])
        hp = argparse.Namespace(cells=[argparse.Namespace(**dict(vars(pc), cell="holt:x", family="holt"))])
        self.assertNotIn("prev_needs", rs.cells_doc(hp, "cinqcent")["cells"]["x"])

    def test_leftovers(self):
        rid = "t2s-20261101T0000-dead"
        run.write_json(os.path.join(run.run_dir(rid, self.mac), "made.json"),
                       {"chopin": [{"path": f"/mnt/bko-ram/{rid}-debian12", "pid": 4242},
                                   {"path": "/mnt/bko-ram/someone-elses", "pid": 1}]})
        self.assertEqual(run.our_leftovers(self.mac), [(rid, "chopin", f"/mnt/bko-ram/{rid}-debian12", 4242)])
        self.assertEqual(run.our_leftovers(self.mac, live={rid}), [])
        with open(os.path.join(run.run_dir(rid, self.mac), "report.txt"), "w") as f:
            f.write("done\n")
        self.assertEqual(run.our_leftovers(self.mac), [])

    def test_nothing_due_writes_carried_rows(self):
        """A bare run with nothing due writes its carried rows to mac.jsonl and starts nothing."""
        mac = os.path.join(self.tmp, "mac-life")
        old = os.environ.get("T2_MAC_ROOT")
        os.environ["T2_MAC_ROOT"] = mac
        try:
            p0 = self.w.plan("SticTest2 rel4 plan")
            rows = self.w.seed(p0)
            f = self.w.facts(round_commits=self.w.forks["c1"], round_tag="rel5")
            ctx = self.w.ctx(rows, facts=f)
            wd = self.w.word("SticTest2 rel4", ctx)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = run.lifecycle(wd, ctx, mac_root=mac, out=print)
            self.assertEqual(code, 0)
            self.assertIn("SticTest2: nothing due (42 carried)", out.getvalue())
            self.assertIn("42 carried row(s) written", out.getvalue())
            got = ledger.read(os.path.join(mac, "ledger", "mac.jsonl")).rows
            self.assertEqual(len(got), 42)
            self.assertTrue(all(r["kind"] == "carried" and r["host"] == "mac" and r["via"] for r in got))
            self.assertEqual([ledger.validate(r) for r in got], [[]] * 42)
            p = self.w.plan("SticTest2 rel4 plan", rows + got, facts=f)
            self.assertEqual(p.totals["counts"]["current"], 42)
            self.assertFalse(run.FamilyLock("stic", mac).held())
        finally:
            if old is None:
                os.environ.pop("T2_MAC_ROOT", None)
            else:
                os.environ["T2_MAC_ROOT"] = old

    def test_wear_lines_data(self):
        """This run's jobs by wear tag (its RUN and RUN-r2 only), diskstats between two facts,
        SMART's day: wear.py's functions on the host ("local" here), no serial back."""
        d = os.path.join(self.tmp, "wear")
        os.makedirs(d, exist_ok=True)
        run_id = "t2s-20261108T0500-abcd"
        jl = os.path.join(d, "jobs.log")
        with open(jl, "w") as f:
            f.write("2026-11-08T05:10:00Z\tlocal\trun-x.scope\t0\t60\tnvme0n1=1000,zram0=5\tcheck\t"
                    f"tag=stic:{run_id}:debian12\tserial=nvme0n1:NOT-A-REAL-SERIAL\n")
            f.write("2026-11-08T05:20:00Z\tlocal\trun-y.scope\t0\t60\tnvme0n1=500\tcheck\t"
                    f"tag=stic:{run_id}-r2:debian12\tserial=nvme0n1:NOT-A-REAL-SERIAL\n")
            f.write("2026-11-08T05:30:00Z\tlocal\trun-z.scope\t0\t60\tnvme0n1=99999\tcheck\t"
                    "tag=ht1:r4all:pop24\tserial=nvme0n1:NOT-A-REAL-SERIAL\n")
        sl = os.path.join(d, "smart.log")
        with open(sl, "w") as f:
            f.write("2026-11-07T23:00:00Z\tlocal\tnvme0n1\tFAKE_MODEL\tNOT-A-REAL-SERIAL\t1000000000\t1\t1\t1\n")
            f.write("2026-11-08T06:00:00Z\tlocal\tnvme0n1\tFAKE_MODEL\tNOT-A-REAL-SERIAL\t3000000000\t1\t1\t1\n")
        before = {"local": {"preflight": {"diskstats": {"nvme0n1": {"model": "FAKE_MODEL", "sectors_written": 1000}}}}}
        after = {"local": {"preflight": {"diskstats": {"nvme0n1": {"model": "FAKE_MODEL", "sectors_written": 3000}}}}}
        start = 1794114000   # 2026-11-08T05:00:00Z
        got = run.wear_lines_data(run_id, ["local"], before, after, start, now=start + 3600, log=jl, smart=sl)
        self.assertEqual(got, {"local": {"nvme0n1": {"model": "FAKE_MODEL", "window_bytes": 2000 * 512,
                                                     "ours_bytes": 1500, "today_bytes": 2000000000}}})
        self.assertNotIn("SERIAL", json.dumps(got))
        line = report.wear_lines(got)[0]
        self.assertEqual(line, "local nvme0n1 (FAKE_MODEL): run window 0.00 GB (diskstats, all writers), "
                               "our jobs 0.00 GB (wear tags), today 2.00 GB (SMART)")

    def test_refuses_on_preflight_and_a_held_lock(self):
        mac = os.path.join(self.tmp, "mac-refuse")
        f = self.w.facts()
        f["chopin"]["preflight"]["tctl_c"] = 95.0
        ctx = self.w.ctx([], facts=f)
        out = []
        self.assertEqual(run.lifecycle(self.w.word("GateTest2", ctx), ctx, mac_root=mac, out=out.append), 2)
        self.assertIn("preflight refuses", out[0])
        lk = run.FamilyLock("gate", mac).acquire("t2g-20261108T0500-beef")
        try:
            out = []
            self.assertEqual(run.lifecycle(self.w.word("GateTest2", ctx), self.w.ctx([]), mac_root=mac,
                                           out=out.append), 2)
            self.assertIn("t2g-20261108T0500-beef", out[0])
        finally:
            lk.release()



def st(p, cell):
    c = p.cell(cell)
    return (c.status, c.rule) if c else None


class Derived(unittest.TestCase):
    """The reviews of P2: derived rows from the runner's last plan, per host, from a valid
    decisive row only, after a resume; the exit from the final plan; leftovers and unfinished
    runs; budgets from the wear tags; two hosts' wear."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t2p2-derived-")
        cls.w = world.World(cls.tmp)
        cls.base = cls.w.head()
        cls.r_stic = cls.w.seed(cls.w.plan("SticTest2 rel4 plan"), age={"stic:freebsd15": 48})
        cls.r_holt = cls.w.seed(cls.w.plan("HoltTest2 plan"))
        cls.r_gate = cls.w.seed(cls.w.plan("GateTest2 plan"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.saved = (run.runner, run.copies.make, os.environ.get("T2_MAC_ROOT"))
        self.mac = tempfile.mkdtemp(prefix="mac-", dir=self.tmp)
        os.environ["T2_MAC_ROOT"] = self.mac

    def tearDown(self):
        run.runner, run.copies.make, old = self.saved
        if old is None:
            os.environ.pop("T2_MAC_ROOT", None)
        else:
            os.environ["T2_MAC_ROOT"] = old
        self.w.reset(self.base)
        self.w.set_heads("c0")

    def fake_copies(self):
        import types
        run.copies.make = lambda checkout, hosts, log=None: {
            h: types.SimpleNamespace(id="fixture", path=f"/fake/{h}") for h in hosts}

    def t2cli(self):
        import importlib.machinery
        import importlib.util
        path = os.path.join(os.path.dirname(HERE), "t2")
        spec = importlib.util.spec_from_loader("t2cli", importlib.machinery.SourceFileLoader("t2cli", path))
        t2 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(t2)
        return t2

    def test_seed_current_guest_sets_try_ran(self):
        ctx = self.w.ctx([])
        p = plan.make(self.w.word("GuestTest2 plan", ctx), ctx)
        rows = {r["cell"]: r for r in self.t2cli().seed_rows(p, ctx)}
        self.assertTrue(rows["guest:arch"]["extra"]["try_ran"])
        self.assertNotIn("try_ran", rows["guest:haiku6"].get("extra") or {})   # a probe, no --try
        self.assertEqual(self.w.plan("GuestTest2 plan", list(rows.values())).totals["due"], 0)

    def test_seed_current_img_passes_the_refusal(self):
        ctx = self.w.ctx([], universe=["img:netbsd"])
        p = plan.make(self.w.word("ImgTest2 plan", ctx), ctx)
        rows = self.t2cli().seed_img_rows(p, ctx)
        self.assertEqual({r["family"] for r in rows}, {"stic", "holt"})
        self.assertEqual([ledger.validate(r) for r in rows], [[]] * len(rows))
        q = self.w.plan("ImgTest2 plan", rows, universe=["img:netbsd"])
        self.assertEqual(st(q, "img:netbsd"), ("due", "img:new"))

    def test_shared_child_takes_no_lock_and_uses_the_composites_things(self):
        import types
        w = self.w
        ctx = w.ctx(self.r_holt)
        wd = w.word("GateTest2", ctx)
        p = plan.make(wd, ctx)
        guards = run.RunGuards(ctx.classes.get("policy"))
        seen = {}

        def fake_run(c, pl):
            seen.update(run=c.run.run, copy=dict(c.run.copy), guards=c.run.stops.guards, plan=pl)
            return {"cells": [], "stops": [], "wear": {"chopin": {}}, "exit": 0}
        calls = []
        run.runner = lambda fam: types.SimpleNamespace(run=fake_run, pre_plan=lambda *a: calls.append("pre"),
                                                       preflight=lambda *a: calls.append("pf"))
        run.copies.make = lambda *a, **k: self.fail("no copy for a child")
        hold = run.FamilyLock("gate", self.mac).acquire("t2b-other")
        try:
            shared = run.Shared("20261110T0312-4f2a", {"chopin": {"id": "c1"}}, guards, plan=p)
            run.lifecycle(wd, ctx, mac_root=self.mac, out=lambda *_: None, shared=shared)
        finally:
            hold.release()
        self.assertEqual(seen["run"], "t2g-20261110T0312-4f2a")
        self.assertEqual(seen["copy"], {"chopin": {"id": "c1"}})
        self.assertIs(seen["guards"], guards)
        self.assertIs(seen["plan"], p)
        self.assertEqual(calls, [])

    def test_plain_run_calls_pre_plan_and_preflight(self):
        import types
        ctx = self.w.ctx(self.r_gate)
        calls = []
        run.runner = lambda fam: types.SimpleNamespace(run=lambda c, p: {}, pre_plan=lambda *a: calls.append("pre"),
                                                       preflight=lambda *a: calls.append("pf"))
        self.assertEqual(run.lifecycle(self.w.word("GateTest2", ctx), ctx, mac_root=self.mac, out=lambda *_: None), 0)
        self.assertEqual(calls, ["pf", "pre"])

    def test_nothing_due_exits_to_class_and_refused(self):
        import types
        w = self.w
        p = w.plan("GuestTest2 plan")
        rows = w.seed(p, extra={"try_ran": True}, needs={"identity": "PASS"})
        pc = p.cell("guest:arch")
        bad = w.row(pc, sorted(pc.res)[0], verdict="FAIL", age_h=1, needs={"logs": "DIFF"},
                    extra={"to_class_only": True, "try_ran": True})
        run.runner = lambda fam: types.SimpleNamespace(run=lambda c, q: self.fail("nothing is due"))
        ctx = w.ctx(rows + [bad])
        out = []
        self.assertEqual(run.lifecycle(w.word("GuestTest2", ctx), ctx, mac_root=self.mac, out=out.append), 1)
        self.assertIn("to-class: guest:arch", "\n".join(out))
        self.assertIn("sweep: SweepTest2 never ran", "\n".join(out))
        ctx = w.ctx([])
        out = []
        self.assertEqual(run.lifecycle(w.word("ImgTest2", ctx), ctx, mac_root=self.mac, out=out.append), 2)
        self.assertIn("refused: img:linux", "\n".join(out))

    def test_a_bare_update_nothing_can_run_says_why_and_exits_2(self):
        import types
        run.runner = lambda fam: types.SimpleNamespace(run=lambda c, q: self.fail("nothing is due"))
        ctx = self.w.ctx([])
        out = []
        self.assertEqual(run.lifecycle(self.w.word("GuestTest2 update", ctx), ctx, mac_root=self.mac, out=out.append), 2)
        text = "\n".join(out)
        self.assertIn("refused: guest:arch: no --try PASS within 7 days: run t2 GuestTest2 arch first", text)
        self.assertEqual(text.count("refused: guest:"), 49)

    def test_a_refused_image_exits_2_after_the_due_ones_finish(self):
        import types
        w = self.w
        ctx = w.ctx([], universe=["img:netbsd"])
        rows = self.t2cli().seed_img_rows(plan.make(w.word("ImgTest2 plan", ctx), ctx), ctx)
        ctx = w.ctx(rows)
        out = []
        ran = []
        run.runner = lambda fam: types.SimpleNamespace(run=lambda c, q: ran.append(c.run.run) or
                                                       {"cells": [], "stops": [], "wear": {"chopin": {}}, "exit": 0})
        self.fake_copies()
        self.assertEqual(run.lifecycle(w.word("ImgTest2", ctx), ctx, mac_root=self.mac, out=out.append), 2)
        self.assertEqual(len(ran), 1)
        self.assertIn("refused: img:linux", "\n".join(out))    # the report says why the exit is 2

    def test_update_waits_for_every_unfinished_run(self):
        rid = "t2s-20261110T0312-4f2a"
        run.write_json(os.path.join(run.run_dir(rid, self.mac), "cells-chopin.json"), {})
        ctx = self.w.ctx([])
        out = []
        import types
        run.runner = lambda fam: types.SimpleNamespace(run=lambda c, q: {})
        self.assertEqual(run.lifecycle(self.w.word("GuestTest2 update arch", ctx), ctx, mac_root=self.mac, out=out.append), 2)
        self.assertIn("unfinished", "\n".join(out))

    def test_a_composites_child_is_resumed_through_the_composite(self):
        rid, comp = "t2s-20261110T0312-4f2a", "t2b-20261110T0312-4f2a"
        run.write_json(os.path.join(run.run_dir(rid, self.mac), "cells-chopin.json"), {})
        run.write_json(os.path.join(run.run_dir(rid, self.mac), "plan.json"), {"family": "stic", "word": {"text": "SticTest2"}})
        self.assertIsNone(run.composite_of(rid, self.mac))
        run.write_json(os.path.join(run.run_dir(comp, self.mac), "bko.json"), {"run": comp})
        self.assertEqual(run.composite_of(rid, self.mac), comp)
        self.assertEqual(run.composite_of("t2h-20261110T0312-4f2a-img-r2", self.mac), comp)
        self.assertIsNone(run.composite_of(comp, self.mac))
        ctx = self.w.ctx([])
        out = []
        import types
        run.runner = lambda fam: types.SimpleNamespace(run=lambda c, q: {})
        self.assertEqual(run.lifecycle(self.w.word("SticTest2 debian13", ctx), ctx, mac_root=self.mac, out=out.append), 2)
        self.assertIn(f"`t2 resume {comp}` finishes it", "\n".join(out))
        out = []
        self.assertEqual(run.resume(rid, ctx, mac_root=self.mac, out=out.append), 2)
        self.assertIn(f"{rid} is a child of {comp}, which hasn't finished: `t2 resume {comp}`", "\n".join(out))
        run.write_text(os.path.join(run.run_dir(comp, self.mac), "report.txt"), "done\n")
        self.assertIsNone(run.composite_of(rid, self.mac))

    def test_resume_restores_what_a_parse_loses(self):
        ctx = self.w.ctx([])
        w = self.w.word("GuestTest2All", ctx)
        w.sweep = True
        ctx2 = self.w.ctx([], extra_due={"guest:arch": "x"}, identity_bad={"arch": "y"})
        pj = plan.make(w, ctx2).to_json()
        self.assertTrue(pj["word"]["sweep"])
        self.assertEqual(pj["options"], {"extra_due": {"guest:arch": "x"}, "identity_bad": {"arch": "y"}})
        ctx3 = self.w.ctx([])
        w2 = run.restore_word(self.w.word("GuestTest2All", ctx3), pj, ctx3)
        self.assertTrue(w2.sweep)
        self.assertEqual(ctx3.options["identity_bad"], {"arch": "y"})
        u = self.w.word("GuestTest2 update arch", ctx3)
        self.assertTrue(u.to_json()["update"])

    def test_carried_rows_of_run_attempts(self):
        self.assertTrue(run._of_run("t2s-x-r3", "t2s-x"))
        self.assertTrue(run._of_run("t2s-x", "t2s-x"))
        self.assertFalse(run._of_run("t2s-xy", "t2s-x"))

    def test_lifecycle_uses_the_runners_last_plan(self):
        """A commands.rs fix with debian13's plan blessed: the step-4 plan carries mint22, the gate's
        plan_diff names it, the runner's re-plan runs it and it FAILs.  No carried PASS lands
        after that FAIL, the report says what the re-plan carried, and the next plan says failed."""
        import types
        w = self.w
        w.edit("holt/tests/corpus/debian13/plan.txt", w.plan_text("debian13", ["format"]))
        w.edit("holt/src/load/commands.rs", "// prints the plan, changed\n", commit=True)
        g = w.plan("GateTest2 plan", self.r_gate)
        gh = w.row(g.cell("gate:holt"), "chopin", plan_=g, extra={"plan_diff": {
            "v": 1, "corpus": 45, "invariants": {}, "differ": {"mint22": {"stages": ["format"], "lines_changed": 1}}}})
        ctx = w.ctx(self.r_holt + self.r_gate)
        wd = w.word("HoltTest2", ctx)
        state = {"rows": self.r_holt + self.r_gate}

        def fake_run(c, p):
            self.assertEqual(st(p, "holt:mint22"), ("carried", "carry:plan-diff"))
            state["rows"] = state["rows"] + [gh]
            new, added, dropped = c.run.replan(c.facts, state["rows"])
            self.assertEqual(st(new, "holt:mint22"), ("due", "holt:plan-diff"))
            fail = w.row(new.cell("holt:mint22"), "chopin", verdict="FAIL", plan_=new)
            state["rows"] = state["rows"] + [fail]
            return {"cells": [{"cell": "holt:mint22", "outcome": "ran", "verdict": "FAIL", "rows": [fail["id"]],
                               "host": "chopin"}], "stops": [], "wear": {"chopin": {}}, "exit": 1}

        run.runner = lambda fam: types.SimpleNamespace(run=fake_run)
        self.fake_copies()
        out = []
        code = run.lifecycle(wd, ctx, mac_root=self.mac, out=out.append, pull=lambda: state["rows"])
        self.assertEqual(code, 1)
        derived = ledger.read(os.path.join(self.mac, "ledger", "mac.jsonl")).rows
        self.assertFalse([r for r in derived if r["cell"] == "holt:mint22"])
        self.assertTrue(derived and all(r["kind"] == "carried" and r["rule"] == "carry:plan-diff" for r in derived))
        text = "\n".join(out)
        self.assertRegex(text, r"skipped +holt:arch +- +carry:plan-diff")
        self.assertEqual(st(w.plan("HoltTest2 plan", state["rows"] + derived), "holt:mint22"), ("due", "failed"))

    def test_carried_rows_only_where_the_hosts_change_is_the_judged_one(self):
        w = self.w
        fa = w.facts()
        fa["cinqcent"]["guests"]["debian13"]["generation"] = world.H("gen debian13 on cinqcent only")
        seed = w.seed(w.plan("SticTest2 rel4 plan", facts=fa), hosts=["chopin"])
        fb = w.facts(round_commits=w.forks["c1"], round_tag="rel5")
        fb["cinqcent"]["guests"]["debian13"]["generation"] = world.H("gen debian13 on cinqcent only")
        ctx = w.ctx(seed, facts=fb)
        wd = w.word("SticTest2 rel4", ctx)
        p = plan.make(wd, ctx)
        pc = p.cell("stic:debian13")
        self.assertEqual((pc.status, pc.rule, len(set(pc.keys.values()))), ("carried", "carry:version-only", 2))
        notes = []
        out = [r for r in run.derived_rows(p, "t2s-20261108T0500-abcd", wd, ctx, {"cells": []}, seed, notes=notes)
               if r["cell"] == "stic:debian13"]
        self.assertEqual([r["key"] for r in out], [pc.keys["chopin"]])
        self.assertTrue(any("stic:debian13 at cinqcent's key" in n for n in notes), notes)
        # an unknown identity never derives a row, and never stays current
        fc = w.facts(round_commits=w.forks["c1"], round_tag="rel5")
        fc["cinqcent"]["guests"]["debian13"].pop("generation", None)
        fc["cinqcent"]["guests"]["debian13"].pop("imgsum", None)
        ctx = w.ctx(seed, facts=fc)
        p = plan.make(wd, ctx)
        out = [r for r in run.derived_rows(p, "t2s-20261108T0500-abcd", wd, ctx, {"cells": []}, seed)
               if r["cell"] == "stic:debian13"]
        self.assertFalse(any("img:unknown" in json.dumps(r["inputs"]) for r in out))

    def test_class_rows_need_a_valid_decisive_pass(self):
        w = self.w
        f2 = w.facts(round_commits=w.forks["c2"], round_tag="rel6")
        ctx = w.ctx(self.r_stic, facts=f2)
        wd = w.word("SticTest2 rel4", ctx)
        p = plan.make(wd, ctx)
        self.assertEqual(st(p, "stic:freebsd15"), ("due", "class:B2"))
        rep = p.cell("stic:freebsd15")
        good = w.row(rep, "chopin", plan_=p, needs={"seeded": "PASS"})
        bad = w.row(rep, "chopin", plan_=p, valid=False)
        fail1 = w.row(rep, "chopin", verdict="FAIL", plan_=p, needs={"acl": "FAIL"})
        other = w.row(rep, "chopin", plan_=p, inputs=dict(rep.res["chopin"].inputs, judge={"x": "sha256:00"}))
        unc = w.row(rep, "chopin", plan_=p, committed=False, dirty={"testvm/vm-run": "sha256:" + "0" * 64})

        def classes(rows, ids, verdict="PASS", final=None):
            res = {"cells": [{"cell": "stic:freebsd15", "verdict": verdict, "rows": ids, "final_row": final}]}
            return [r for r in run.derived_rows(p, "t2s-20261108T0500-abcd", wd, ctx, res, self.r_stic + rows)
                    if r["kind"] == "class"]
        got = classes([good], [good["id"]])
        self.assertEqual(sorted(r["cell"] for r in got), ["stic:freebsd15-geli", "stic:ghostbsd26-zfs"])
        self.assertTrue(all(r["via"] == good["id"] and r["key"] == p.cell(r["cell"]).key for r in got))
        self.assertEqual(classes([bad], [bad["id"]]), [])            # an invalid PASS
        self.assertEqual(classes([other], [other["id"]]), [])        # not at the planned key
        self.assertEqual(classes([unc], [unc["id"]]), [])            # from an uncommitted copy
        self.assertEqual(classes([good], [good["id"]], verdict="FAIL"), [])
        # attempt 1 FAILed, attempt 2 passed as a known flake: via and needs are attempt 2's
        got = classes([fail1, good], [fail1["id"], good["id"]])
        self.assertTrue(got and all(r["via"] == good["id"] and r["needs"] == {"seeded": "PASS"} for r in got))
        got = classes([fail1, good], [good["id"], fail1["id"]], final=good["id"])
        self.assertTrue(got and all(r["via"] == good["id"] for r in got))

    def test_resume_derives_from_a_fresh_plan_only(self):
        w = self.w
        f1 = w.facts(round_commits=w.forks["c1"], round_tag="rel5")
        ctx = w.ctx(self.r_stic, facts=f1)
        wd = w.word("SticTest2 rel4", ctx)
        p = plan.make(wd, ctx)
        fresh = run.derived_rows(p, "t2s-20261108T0500-abcd", wd, ctx, {"cells": []}, self.r_stic)
        self.assertEqual(len(fresh), 42)
        saved = run.plan_from_json(json.loads(json.dumps(p.to_json())), wd)
        self.assertEqual(run.derived_rows(saved, "t2s-20261108T0500-abcd", wd, ctx, {"cells": []}, self.r_stic), [])
        run.adopt_inputs(saved, plan.make(wd, w.ctx(self.r_stic, facts=f1)))
        again = run.derived_rows(saved, "t2s-20261108T0500-abcd", wd, ctx, {"cells": []}, self.r_stic)
        self.assertEqual(sorted((r["cell"], r["key"]) for r in again), sorted((r["cell"], r["key"]) for r in fresh))
        # a fresh plan that disagrees (another round) adopts nothing
        saved = run.plan_from_json(json.loads(json.dumps(p.to_json())), wd)
        run.adopt_inputs(saved, plan.make(wd, w.ctx(self.r_stic, facts=w.facts(round_commits=w.forks["c2"],
                                                                               round_tag="rel6"))))
        self.assertEqual(run.derived_rows(saved, "t2s-20261108T0500-abcd", wd, ctx, {"cells": []}, self.r_stic), [])

    def test_resume_on_the_runs_hosts_writes_rows_from_the_fresh_plan(self):
        import types
        w = self.w
        f1 = w.facts(round_commits=w.forks["c1"], round_tag="rel5")
        ctx = w.ctx(self.r_stic, facts={"chopin": f1["chopin"]}, hosts=["chopin"])
        wd = w.word("SticTest2 rel4", ctx)
        p = plan.make(wd, ctx)          # the run's plan, on its one host
        rid = "t2s-20261108T0700-cccc"
        run.save_plan(p, rid, self.mac)
        run.write_json(os.path.join(run.run_dir(rid, self.mac), "cells-chopin.json"), {"copy": {"id": "x"}, "cells": {}})
        seen = {}

        def fake_resume(c, pl):
            seen["hosts"] = list(c.run.hosts)
            return {"cells": [], "stops": [], "wear": {"chopin": {}}, "exit": 0}
        run.runner = lambda fam: types.SimpleNamespace(run=None, resume=fake_resume)
        out = []
        self.assertEqual(run.resume(rid, w.ctx(self.r_stic, facts=f1), mac_root=self.mac, out=out.append), 2)
        self.assertIn("ran on chopin", out[0])            # its hosts: chopin only
        c1 = w.ctx(self.r_stic, facts={"chopin": f1["chopin"]}, hosts=["chopin"])
        self.assertEqual(run.resume(rid, c1, mac_root=self.mac, out=out.append), 0)
        self.assertEqual(seen["hosts"], ["chopin"])
        got = ledger.read(os.path.join(self.mac, "ledger", "mac.jsonl")).rows
        self.assertEqual(len(got), 42)
        self.assertTrue(all(r["inputs"] and r["key"] == p.cell(r["cell"]).keys["chopin"] for r in got))
        self.assertTrue(os.path.exists(os.path.join(run.run_dir(rid, self.mac), "report.txt")))

    def test_gate_fork_keys_read_the_contexts_bko_git(self):
        """keys.resolve gets the forks' heads from ctx's BKO_GIT (never the env's): a fork move
        in the world makes its fork layers due."""
        old = os.environ.get("BKO_GIT")
        os.environ["BKO_GIT"] = "/nonexistent"
        try:
            self.w.set_heads("c1")
            p = self.w.plan("GateTest2 plan", self.r_gate)
        finally:
            if old is None:
                os.environ.pop("BKO_GIT", None)
            else:
                os.environ["BKO_GIT"] = old
        forks = [c for c in p.cells if c.cell.startswith("gate:fork:")]
        self.assertTrue(forks)
        for c in forks:
            self.assertEqual((c.status, c.rule), ("due", "changed:harness"), c.cell)
            self.assertIn("harness:git:", "; ".join(c.reasons))

    def test_unbuilt_round_predicts_per_binary_and_writes_no_carried_rows(self):
        w = self.w
        d = os.path.join(w.bko_git, "rustic")       # only rustic moves (a version bump)
        h = w.forks["c1"]["rustic"]
        world.git(d, "checkout", "-q", h)
        world.git(d, "branch", "-q", "-f", "backotter", h)
        world.git(d, "checkout", "-q", "backotter")
        f = w.facts()
        for x in f:
            f[x]["rounds"] = {}
        ctx = w.ctx(self.r_stic, facts=f)
        wd = w.word("SticTest2", ctx)
        p = plan.make(wd, ctx)
        self.assertTrue(p.set["pending"])
        self.assertEqual({(c.status, c.rule) for c in p.cells}, {("carried", "carry:version-only")})
        self.assertIn("linux/restic-bko assumed reused", "; ".join(p.cell("stic:debian13").reasons))
        out = []
        self.assertEqual(run.lifecycle(wd, ctx, mac_root=self.mac, out=out.append), 0)
        self.assertIn("nothing due (42 carried)", out[0])
        self.assertNotIn("row(s) written", out[0])
        self.assertFalse(os.path.exists(os.path.join(self.mac, "ledger", "mac.jsonl")))

    def test_outcome_exit_and_names_refused_when_not_runnable(self):
        p = self.w.plan("SticTest2 rel4 debian12 debian13 freebsd15 plan")
        cells = [{"cell": "stic:debian12", "outcome": "ran", "verdict": "HARNESS", "rows": ["a"]},
                 {"cell": "stic:debian13", "outcome": "ran", "verdict": "PASS", "rows": ["b"]},
                 {"cell": "stic:freebsd15", "outcome": "not-started", "rows": []}]
        rows = [{"id": "a", "verdict": "HARNESS", "valid": True}, {"id": "b", "verdict": "PASS", "valid": False}]
        ex, bad = run.outcome_exit(p, {"cells": cells}, rows)
        self.assertEqual(ex, 1)
        self.assertEqual(len(bad), 3)
        rows[1]["valid"] = True
        cells[0]["verdict"], rows[0]["verdict"] = "PASS", "PASS"
        cells[2] = {"cell": "stic:freebsd15", "outcome": "ran", "verdict": "PASS", "rows": ["a"]}
        self.assertEqual(run.outcome_exit(p, {"cells": cells}, rows), (0, []))
        # every named cell not-runnable: refused, with the reason
        f = self.w.facts()
        for h in f:
            f[h]["sets"]["roundrel4"]["sums"].pop("haiku/rustic-bko", None)
        ctx = self.w.ctx([], facts=f)
        out = []
        self.assertEqual(run.lifecycle(self.w.word("SticTest2 rel4 haiku6", ctx), ctx, mac_root=self.mac,
                                       out=out.append), 2)
        self.assertIn("not runnable: stic:haiku6", "\n".join(out))

    def test_leftovers_of_live_or_unknown_runs_are_kept(self):
        rid = "t2g-20261108T0500-aaaa"
        run.write_json(os.path.join(run.run_dir(rid, self.mac), "made.json"),
                       {"chopin": [{"path": f"/mnt/bko-ram/{rid}-loop_linux", "pid": 4242},
                                   {"path": f"/mnt/bko-ram/{rid}-other", "pid": None}]})
        lk = run.FamilyLock("gate", self.mac).acquire(rid)
        try:
            self.assertIn(rid, run.held_runs(self.mac))
            self.assertEqual(run.our_leftovers(self.mac, run.held_runs(self.mac)), [])
        finally:
            lk.release()
        old = os.environ.get("T2_SSH")
        os.environ["T2_SSH"] = "false"
        try:
            self.assertFalse(run.pid_dead("chopin", 4242))     # no answer is not "dead"
        finally:
            if old is None:
                os.environ.pop("T2_SSH", None)
            else:
                os.environ["T2_SSH"] = old
        log = []
        items = run.our_leftovers(self.mac)
        self.assertEqual(len(items), 2)
        self.assertEqual(run.remove_leftovers(items, log.append, dead=lambda h, p: False), [])
        self.assertIn("kept", log[0])
        # an unfinished run of the family (host work began, no report) refuses a new run
        sid = "t2s-20261108T0600-bbbb"
        run.write_json(os.path.join(run.run_dir(sid, self.mac), "cells-chopin.json"), {"cells": {}})
        self.assertEqual(run.unfinished_runs("stic", self.mac), [sid])
        self.assertEqual(run.unfinished_runs("stic", self.mac, now=time.time() + 13 * 3600), [])
        ctx = self.w.ctx(self.r_stic)
        out = []
        self.assertEqual(run.lifecycle(self.w.word("SticTest2 rel4", ctx), ctx, mac_root=self.mac,
                                       out=out.append), 2)
        self.assertIn(f"t2 resume {sid}", out[0])
        self.assertEqual(run.run_hosts(sid, self.mac), ["chopin"])
        with open(os.path.join(run.run_dir(sid, self.mac), "report.txt"), "w") as fh:
            fh.write("set aside\n")
        self.assertEqual(run.unfinished_runs("stic", self.mac), [])

    def test_budgets_from_the_wear_tags_and_bind_from(self):
        rr = {"run": "t2s-20261108T0500-abcd", "cells": [{"cell": "stic:debian12", "host": "chopin", "gb": None}],
              "wear": {"chopin": {"nvme0n1": {"ours_bytes": 6.5e9}, "sda": {"ours_bytes": None}}}}
        pol = {"budget": {"run_gb": 2, "sweep_gb": 5, "day_gb": 50, "bind_from": None}}
        day = [{"t": "2026-11-08T01:00:00Z", "run": "t2h-20261108T0100-0001", "host": "chopin",
                "wear": {"x": 45e9}},
               {"t": "2026-11-08T02:00:00Z", "run": "t2s-20261108T0500-abcd", "host": "chopin",
                "wear": {"x": 6.5e9}}]          # this run's own row: in its wear tags already
        b = report.budget_lines(rr, pol, ledger=day, now=1794182400 - 86400)
        self.assertEqual(b, ["budget: chopin wrote 6.50 GB in this run's jobs, over 2 GB (warn only (bind_from not set))",
                             "budget: chopin: Test2's jobs wrote 51.5 GB today, over 50 GB a day "
                             "(warn only (bind_from not set))"])
        pol["budget"]["bind_from"] = "2026-11-22"
        self.assertIn("warn only until 2026-11-22", report.budget_lines(rr, pol, now=1794182400)[0])
        self.assertIn("(binds)", report.budget_lines(rr, pol, now=1794182400 + 20 * 86400)[0])
        self.assertTrue(report.budget_binds("2026-11-22T00:00:00Z", 1794182400 + 20 * 86400))
        self.assertFalse(report.budget_binds(None))

    def test_wear_lines_data_on_two_hosts(self):
        d = os.path.join(self.tmp, "wear2")
        os.makedirs(d, exist_ok=True)
        rid = "t2s-20261108T0500-abcd"
        jl, sl = os.path.join(d, "jobs.log"), os.path.join(d, "smart.log")
        with open(jl, "w") as f:
            f.write(f"2026-11-08T05:10:00Z\tlocal\trun-x.scope\t0\t60\tnvme0n1=1000\tcheck\ttag=stic:{rid}:debian12\n")
        with open(sl, "w") as f:
            f.write("2026-11-07T23:00:00Z\tlocal\tnvme0n1\tFAKE_MODEL\tX\t1000000000\t1\t1\t1\n")
            f.write("2026-11-08T06:00:00Z\tlocal\tnvme0n1\tFAKE_MODEL\tX\t3000000000\t1\t1\t1\n")
        fx = {"local": {"preflight": {"diskstats": {"nvme0n1": {"model": "FAKE_MODEL", "sectors_written": 1}}}}}
        start = 1794114000
        got = run.wear_lines_data(rid, ["local", "local"], fx, fx, start, now=start + 3600, log=jl, smart=sl)
        self.assertEqual((got["local"]["nvme0n1"]["ours_bytes"], got["local"]["nvme0n1"]["today_bytes"]),
                         (1000, 2000000000))

    def test_cli_options(self):
        import importlib.machinery
        import importlib.util
        path = os.path.join(os.path.dirname(HERE), "t2")
        spec = importlib.util.spec_from_loader("t2cli", importlib.machinery.SourceFileLoader("t2cli", path))
        t2 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(t2)
        self.assertEqual(t2.hosts_opt({}), ["chopin", "cinqcent"])
        self.assertEqual(t2.hosts_opt({"--hosts": "chopin,chopin, cinqcent"}), ["chopin", "cinqcent"])
        with self.assertRaises(t2.Usage):
            t2.hosts_opt({"--hosts": ""})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(t2.main(["ledger", "show", "--last", "abc"]), 2)
            self.assertEqual(t2.main(["ledger", "show", "--last", "0"]), 2)
        # seed-current's guard compares real paths
        for spelling in (os.path.expanduser("~/backotter-work//test2"), os.path.expanduser("~/backotter-work/test2/.")):
            os.environ["T2_MAC_ROOT"] = spelling
            with contextlib.redirect_stderr(err):
                self.assertEqual(t2.main(["ledger", "seed-current", "--family", "stic"]), 2)
        self.assertIn("seed-current is test-only", err.getvalue())

    def test_seed_imgsums_fill_the_test_roots_cache(self):
        """T2.2: seed-current gives a guest of unknown identity a sum in the test HOST_ROOT's
        cache, which facts_host reads back; the default root is never written."""
        import importlib.machinery
        import importlib.util
        from t2lib import facts_host, hostrun
        path = os.path.join(os.path.dirname(HERE), "t2")
        spec = importlib.util.spec_from_loader("t2cli", importlib.machinery.SourceFileLoader("t2cli", path))
        t2 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(t2)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        disk = os.path.join(d, "g.qcow2")
        with open(disk, "w", encoding="utf-8") as f:
            f.write("x")
        facts = {"guests": {"g": {"generation": None, "imgsum": None, "disks": [{"path": disk, "size": 1, "mtime": 0}]},
                            "other": {"generation": "7", "disks": [{"path": "/no/such", "size": 1}]}},
                 "rescuers": {"images": {"r.qcow2": {"sha256": "ab"}}}}
        ctx = type("C", (), {"facts": {hostrun.LOCAL: facts}})()
        keep = os.environ.get("T2_HOST_ROOT")
        self.addCleanup(lambda: os.environ.pop("T2_HOST_ROOT", None) if keep is None else os.environ.update(T2_HOST_ROOT=keep))
        os.environ.pop("T2_HOST_ROOT", None)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(t2.seed_imgsums(ctx, [hostrun.LOCAL]), 0)
        os.environ["T2_HOST_ROOT"] = d
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(t2.seed_imgsums(ctx, [hostrun.LOCAL]), 1)
        h = facts_host.cached_sum(facts_host.imgsum_cache(d), disk)
        self.assertRegex(h or "", "^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
