"""The write stop rule's per-cell write allowance (2026-11-17): a known disk-heavy holt cell
(holt/spec.json write_allow, holt:vanilla3 36 GB) has its own bytes, up to the allowance, left
out of the rule (run.RunGuards, run.StopEngine); everything over it, and every other cell's
bytes, still count, and the stop's words and the report's budget line keep the true total.

No host: python3 -m unittest discover -s tests.
"""

import os
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
WT = os.path.dirname(os.path.dirname(T2))
sys.path.insert(0, T2)

from t2lib import bko, holt, keys, report, run  # noqa: E402

POL = {"budget": {"run_gb": 2, "sweep_gb": 5, "update_gb": 40, "bind_from": None},
       "stop": {"os_need_guests": 2, "harness_results": 2, "swap_out_gib": 1, "write_budget_x": 2}}
RUN = "t2h-20261117T1200-abcd"
PREFIX = f"ht1:{RUN}"
V3 = "holt:vanilla3"
ALLOW = 36e9


def spec(entries):
    return keys.Specs(None, overrides={"holt": {"v": 1, "family": "holt", "kinds": {"linux": {}},
                                                "entries": entries}}).get("holt")


SF = spec({"vanilla3": {"kind": "linux", "write_allow": {"gb": 36, "measured": "test"}},
           "debian13": {"kind": "linux"},
           "bare": {"kind": "linux", "write_allow": 5},
           "bad": {"kind": "linux", "write_allow": {"gb": "lots"}}})


class Spec(unittest.TestCase):
    def test_the_real_spec_gives_vanilla3_36_gb(self):
        sf = keys.Specs(keys.TreeSource(WT)).get("holt")
        self.assertEqual(holt.write_allow_bytes(sf, V3), int(36e9))
        self.assertTrue(sf.entry("vanilla3").get("write_allow", {}).get("measured"))
        self.assertEqual(holt.write_allow_bytes(sf, "holt:debian13"), 0)

    def test_write_allow_bytes(self):
        self.assertEqual(holt.write_allow_bytes(SF, V3), int(36e9))
        self.assertEqual(holt.write_allow_bytes(SF, "holt:bare"), int(5e9))      # a bare number is GB too
        self.assertEqual(holt.write_allow_bytes(SF, "holt:debian13"), 0)
        self.assertEqual(holt.write_allow_bytes(SF, "holt:bad"), 0)
        self.assertEqual(holt.write_allow_bytes(SF, "holt:nosuch"), 0)
        self.assertEqual(holt.write_allow_bytes(SF, "holt:vanilla3+img"), 0)    # variants have none
        self.assertEqual(holt.write_allow_bytes(None, V3), 0)


class AllowedBytes(unittest.TestCase):
    allow = {"vanilla3": (V3, int(ALLOW))}

    def test_only_the_cells_own_tags(self):
        by = {f"{PREFIX}:vanilla3": int(30e9), f"{PREFIX}:debian13": int(5e9), f"{PREFIX}:build-linux": int(1e9)}
        self.assertEqual(holt.allowed_bytes(by, PREFIX, self.allow), {V3: int(30e9)})

    def test_capped_at_the_allowance(self):
        self.assertEqual(holt.allowed_bytes({f"{PREFIX}:vanilla3": int(40e9)}, PREFIX, self.allow), {V3: int(36e9)})

    def test_each_attempt_has_its_own(self):
        by = {f"{PREFIX}:vanilla3": int(30e9), f"{PREFIX}-r2:vanilla3": int(40e9)}
        self.assertEqual(holt.allowed_bytes(by, PREFIX, self.allow), {V3: int(66e9)})

    def test_not_a_prefix_lookalike(self):
        by = {f"{PREFIX}:vanilla3-img": int(3e9), f"{PREFIX}x:vanilla3": int(3e9), f"{PREFIX}:vanilla3:sub": int(1e9),
              PREFIX: int(2e9), "ht1:other:vanilla3": int(9e9)}
        self.assertEqual(holt.allowed_bytes(by, PREFIX, self.allow), {V3: int(1e9)})
        self.assertEqual(holt.allowed_bytes(None, PREFIX, self.allow), {})
        self.assertEqual(holt.allowed_bytes(by, PREFIX, {}), {})


class Guards(unittest.TestCase):
    """RunGuards (a composite's, and every plain run's StopEngine with guards) and the plain StopEngine."""

    def engines(self):
        g = run.RunGuards(POL)
        return [("guards", run.StopEngine("holt", POL, guards=g), g), ("plain", run.StopEngine("holt", POL), None)]

    def test_vanilla3_30_gb_alone_trips_nothing(self):
        for name, s, g in self.engines():
            with self.subTest(name):
                self.assertEqual(s.feed_host("chopin", ours_bytes=int(30e9), allowed={V3: int(30e9)}), "ok")
                self.assertTrue(s.may_start())
                if g is not None:
                    self.assertFalse(g.tripped())
                    self.assertEqual(g.host_bytes("chopin"), int(30e9))     # the true total is kept

    def test_vanilla3_30_gb_and_others_5_trips(self):
        for name, s, g in self.engines():
            with self.subTest(name):
                self.assertEqual(s.feed_host("chopin", ours_bytes=int(35e9), allowed={V3: int(30e9)}), "stop-starts")
                why = s.stops[-1]
                self.assertIn("wrote 35.00 GB", why)                      # the true total
                self.assertIn("30.00 GB of it within the write allowance of holt:vanilla3", why)
                self.assertIn("5.00 GB counted", why)
                self.assertIn("over 2 x the 2 GB budget", why)

    def test_others_under_twice_the_budget_beside_vanilla3_trip_nothing(self):
        for name, s, g in self.engines():
            with self.subTest(name):
                self.assertEqual(s.feed_host("chopin", ours_bytes=int(33.9e9), allowed={V3: int(30e9)}), "ok")

    def test_vanilla3_40_gb_trips_once_its_excess_passes_the_budget(self):
        # the runner caps the allowed part at 36 GB (allowed_bytes): 40 GB leaves 4 GB counted, which
        # is not over 4; 41 GB leaves 5
        for name, s, g in self.engines():
            with self.subTest(name):
                self.assertEqual(s.feed_host("chopin", ours_bytes=int(40e9), allowed={V3: int(36e9)}), "ok")
                self.assertEqual(s.feed_host("chopin", ours_bytes=int(41e9), allowed={V3: int(36e9)}), "stop-starts")
                self.assertIn("wrote 41.00 GB", s.stops[-1])

    def test_no_allowance_unchanged(self):
        for name, s, g in self.engines():
            with self.subTest(name):
                self.assertEqual(s.feed_host("chopin", ours_bytes=int(3e9)), "ok")
                self.assertEqual(s.feed_host("chopin", ours_bytes=int(4.1e9)), "stop-starts")
                self.assertEqual(s.stops[-1], "stop-starts: chopin: this run's jobs wrote 4.10 GB, "
                                              "over 2 x the 2 GB budget")
                self.assertNotIn("allowance", s.stops[-1])
        for name, s, g in self.engines():
            with self.subTest(name + " empty"):
                self.assertEqual(s.feed_host("chopin", ours_bytes=int(30e9), allowed={}), "stop-starts")

    def test_bound_budget_counts_what_the_allowance_leaves(self):
        pol = dict(POL, budget=dict(POL["budget"], bind_from="2026-11-01"))
        now = 1794182400 + 9 * 86400
        for s in (run.StopEngine("holt", pol, now=now), run.StopEngine("holt", pol, now=now, guards=run.RunGuards(pol, now=now))):
            self.assertEqual(s.feed_host("h", ours_bytes=int(31e9), allowed={V3: int(30e9)}), "ok")
            self.assertEqual(s.feed_host("h", ours_bytes=int(32.5e9), allowed={V3: int(30e9)}), "stop-starts")
            self.assertIn("(bound)", s.stops[-1])

    def test_another_familys_bytes_count_in_full(self):
        g = run.RunGuards(POL)
        h, st = run.StopEngine("holt", POL, guards=g), run.StopEngine("stic", POL, guards=g)
        self.assertEqual(h.feed_host("chopin", ours_bytes=int(30e9), allowed={V3: int(30e9)}), "ok")
        self.assertEqual(st.feed_host("chopin", ours_bytes=int(3e9)), "ok")
        self.assertEqual(st.feed_host("chopin", ours_bytes=int(4.5e9)), "stop-starts")
        self.assertIn("wrote 34.50 GB (30.00 GB of it within", st.stops[-1])

    def test_a_feed_without_allowed_drops_the_familys_old_allowance(self):
        g = run.RunGuards(POL)
        self.assertIsNone(g.feed_host("holt", "h", ours_bytes=int(30e9), allowed={V3: int(30e9)}))
        self.assertIsNotNone(g.feed_host("holt", "h", ours_bytes=int(30e9)))

    def test_json_keeps_the_allowed(self):
        g = run.RunGuards(POL)
        g.feed_host("holt", "h", ours_bytes=int(30e9), allowed={V3: int(30e9)})
        doc = g.to_json()
        self.assertEqual(doc["allowed"], [["holt", "h", {V3: int(30e9)}]])
        g2 = run.RunGuards.from_json(doc, POL)
        self.assertIsNone(g2.feed_host("stic", "h", ours_bytes=int(3.9e9)))
        self.assertIsNotNone(g2.feed_host("stic", "h", ours_bytes=int(4.1e9)))
        old = {k: v for k, v in doc.items() if k != "allowed"}            # a resume of an older run
        self.assertEqual(run.RunGuards.from_json(old, POL).allowed, {})


class FakeRemote:
    def __init__(self, by):
        self.by, self.asked = by, []

    def pswpout(self, host):
        return 0

    def tag_bytes(self, host, prefix, since, by_tag=False):
        self.asked.append((host, prefix, by_tag))
        got = {t: n for t, n in self.by.get(host, {}).items() if t.startswith(prefix)}
        return got if by_tag else sum(got.values())


class OldRemote(FakeRemote):
    def tag_bytes(self, host, prefix, since):       # a Remote with no by_tag
        return FakeRemote.tag_bytes(self, host, prefix, since)


class HostChecks(unittest.TestCase):
    """holt.Runner.host_checks: what it feeds the stop rules."""

    def runner(self, cells, by, remote=FakeRemote, stops=None):
        tmp = tempfile.mkdtemp(prefix="t2wa-")
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        r = holt.Runner.__new__(holt.Runner)
        r.run_id, r.hosts, r.t0, r.swap0, r.sf = RUN, ["chopin", "cinqcent"], 1000.0, {}, SF
        r.plan = types.SimpleNamespace(cells=[types.SimpleNamespace(cell=c) for c in cells])
        r.remote = remote(by)
        r.state = holt.State(os.path.join(tmp, "holt.json"))
        r.allowed, r.acted = {}, []
        r.act = lambda qrun, what: r.acted.append(what)
        r.rs = types.SimpleNamespace(stops=stops or run.StopEngine("holt", POL, guards=run.RunGuards(POL)))
        return r

    def test_vanilla3_alone(self):
        r = self.runner([V3, "holt:debian13"], {"chopin": {f"{PREFIX}:vanilla3": int(29.57e9)}})
        r.host_checks(RUN)
        self.assertEqual(r.acted, ["ok", "ok"])
        self.assertEqual(r.allowed, {"chopin": {V3: int(29.57e9)}})
        self.assertEqual(r.state["write_allowed"], {"chopin": {V3: int(29.57e9)}})
        self.assertTrue(all(b for _, _, b in r.remote.asked))
        res_allowed = {h: dict(v) for h, v in r.allowed.items()}
        self.assertEqual(res_allowed, {"chopin": {V3: int(29.57e9)}})

    def test_vanilla3_and_others_over(self):
        r = self.runner([V3, "holt:debian13"], {"chopin": {f"{PREFIX}:vanilla3": int(30e9),
                                                           f"{PREFIX}:debian13": int(5e9)}})
        r.host_checks(RUN)
        self.assertEqual(r.acted[0], "stop-starts")
        self.assertIn("wrote 35.00 GB (30.00 GB of it within the write allowance of holt:vanilla3", r.rs.stops.stops[0])

    def test_vanilla3_over_its_allowance(self):
        r = self.runner([V3], {"chopin": {f"{PREFIX}:vanilla3": int(40.5e9)}})
        r.host_checks(RUN)
        self.assertEqual(r.acted[0], "stop-starts")
        self.assertIn("36.00 GB of it within", r.rs.stops.stops[0])
        self.assertIn("4.50 GB counted", r.rs.stops.stops[0])

    def test_a_plan_without_allowance_feeds_as_before(self):
        class Stops:
            def __init__(self):
                self.fed = []

            def feed_host(self, host, swap_out_bytes=None, ours_bytes=None):     # the old signature
                self.fed.append((host, ours_bytes))
                return "ok"
        st = Stops()
        r = self.runner(["holt:debian13"], {"chopin": {f"{PREFIX}:debian13": int(5e9)}}, remote=OldRemote, stops=st)
        r.host_checks(RUN)
        self.assertEqual(st.fed, [("chopin", int(5e9)), ("cinqcent", 0)])
        self.assertEqual(r.allowed, {})
        self.assertIsNone(r.state["write_allowed"])
        r = self.runner(["holt:debian13"], {"chopin": {f"{PREFIX}:debian13": int(5e9)}})
        r.host_checks(RUN)
        self.assertEqual(r.acted[0], "stop-starts")
        self.assertNotIn("allowance", r.rs.stops.stops[0])

    def test_tag_bytes_failing_feeds_nothing(self):
        class Down(FakeRemote):
            def tag_bytes(self, host, prefix, since, by_tag=False):
                return None
        r = self.runner([V3], {}, remote=Down)
        r.host_checks(RUN)
        self.assertEqual(r.acted, ["ok", "ok"])


class CompositeGuards(unittest.TestCase):
    """bko.Guards (a composite's RunGuards) under holt.Runner.host_checks: the allowance and the
    stop rules work as under a plain RunGuards (its feed_host once took no ALLOWED, and the
    TypeError host_checks swallowed turned both rules off)."""

    def runner(self, cells, by, remote=FakeRemote):
        r = HostChecks.runner(self, cells, by, remote=remote,
                              stops=run.StopEngine("holt", POL, guards=bko.Guards(POL)))
        r.logged = []
        r.rs.log = r.logged.append
        return r

    def check(self, by, acted, tripped):
        for name, g in (("bko.Guards", None), ("RunGuards", run.RunGuards(POL))):
            with self.subTest(name, by=by):
                if g is None:
                    r = self.runner([V3, "holt:debian13"], {"chopin": by})
                else:
                    r = HostChecks.runner(self, [V3, "holt:debian13"], {"chopin": by},
                                          stops=run.StopEngine("holt", POL, guards=g))
                    r.logged = []
                    r.rs.log = r.logged.append
                r.host_checks(RUN)
                self.assertEqual(r.acted[0], acted)
                self.assertEqual(r.rs.stops.guards.tripped(), tripped)
                self.assertEqual(r.logged, [])                       # no feed failed

    def test_vanilla3_30_alone(self):
        self.check({f"{PREFIX}:vanilla3": int(30e9)}, "ok", False)
        self.check({f"{PREFIX}:vanilla3": int(29.57e9)}, "ok", False)

    def test_vanilla3_30_and_others_5(self):
        self.check({f"{PREFIX}:vanilla3": int(30e9), f"{PREFIX}:debian13": int(5e9)}, "stop-starts", True)

    def test_vanilla3_over_its_allowance(self):
        self.check({f"{PREFIX}:vanilla3": int(40e9)}, "ok", False)
        self.check({f"{PREFIX}:vanilla3": int(41e9)}, "stop-starts", True)

    def test_swap_still_trips_beside_an_allowance(self):
        class Swappy(FakeRemote):
            n = 0

            def pswpout(self, host):
                Swappy.n += 1
                return 0 if Swappy.n <= 2 else 3 * run.GIB
        r = self.runner([V3], {"chopin": {f"{PREFIX}:vanilla3": int(1e9)}}, remote=Swappy)
        r.host_checks(RUN)
        r.host_checks(RUN)
        self.assertEqual(r.acted, ["ok", "ok", "stop-starts", "ok"])
        self.assertIn("swapped out", r.rs.stops.stops[0])
        self.assertTrue(r.rs.stops.guards.tripped())

    def test_bko_guards_takes_allowed(self):
        g = bko.Guards(POL)
        self.assertIsNone(g.feed_host("holt", "h", None, int(30e9), {V3: int(30e9)}))
        self.assertIsNone(g.feed_host("holt", "h", ours_bytes=int(30e9), allowed={V3: int(30e9)}))
        self.assertIsNotNone(g.feed_host("holt", "h", None, int(35e9), {V3: int(30e9)}))
        self.assertIsNone(bko.Guards(POL).feed_host("stic", "h", None, int(3e9)))    # the old call


class FeedFailures(unittest.TestCase):
    def test_a_failing_feed_is_logged_once(self):
        class Broken:
            stops = []

            def feed_host(self, host, swap_out_bytes=None, ours_bytes=None, allowed=None):
                raise RuntimeError("boom")
        r = HostChecks.runner(self, [V3], {"chopin": {f"{PREFIX}:vanilla3": int(1e9)}}, stops=Broken())
        r.logged = []
        r.rs.log = r.logged.append
        r.host_checks(RUN)
        r.host_checks(RUN)
        self.assertEqual(r.acted, ["ok"] * 4)
        self.assertEqual(r.logged, ["host checks on chopin: the stop rules' feed failed: RuntimeError: boom",
                                    "host checks on cinqcent: the stop rules' feed failed: RuntimeError: boom"])

    def test_a_remote_without_by_tag_feeds_the_plain_sum(self):
        r = HostChecks.runner(self, [V3], {"chopin": {f"{PREFIX}:vanilla3": int(30e9)}}, remote=OldRemote)
        r.host_checks(RUN)                          # no TypeError out of the poll loop
        self.assertEqual(r.acted[0], "stop-starts")  # no allowance without by_tag: the old rule
        self.assertNotIn("allowance", r.rs.stops.stops[0])
        self.assertEqual(r.allowed, {})


class Report(unittest.TestCase):
    def test_the_noted_allowance_never_exceeds_the_total(self):
        rr = {"run": RUN, "cells": [], "wear": {"chopin": {"nvme0n1": {"ours_bytes": int(29.5e9)}}},
              "write_allowed": {"chopin": {V3: int(29.57e9)}}, "write_allow_gb": {V3: 36.0}}
        line = report.budget_lines(rr, POL)[0]
        self.assertIn("wrote 29.50 GB", line)
        self.assertIn("; 29.50 GB of it within the write allowance", line)

    def test_the_budget_line_keeps_the_true_total_and_notes_the_allowance(self):
        rr = {"run": RUN, "cells": [], "wear": {"chopin": {"nvme0n1": {"ours_bytes": int(29.57e9)}}},
              "write_allowed": {"chopin": {V3: int(29.57e9)}}, "write_allow_gb": {V3: 36.0}}
        lines = report.budget_lines(rr, POL)
        self.assertEqual(lines, ["budget: chopin wrote 29.57 GB in this run's jobs, over 2 GB (warn only (bind_from "
                                 "not set)); 29.57 GB of it within the write allowance of holt:vanilla3's 36 GB, "
                                 "which the stop rule doesn't count"])

    def test_no_allowance_line_unchanged(self):
        rr = {"run": RUN, "cells": [], "wear": {"chopin": {"nvme0n1": {"ours_bytes": int(3e9)}}}}
        self.assertEqual(report.budget_lines(rr, POL),
                         ["budget: chopin wrote 3.00 GB in this run's jobs, over 2 GB (warn only (bind_from not set))"])


if __name__ == "__main__":
    unittest.main()
