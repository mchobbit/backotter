"""Unit tests for what a run puts on the hosts' /bkoscratch, bs2's side (2026-11-25: a
BackShot2All start filled cinqcent's): tools only on a source's lead (tools_of(),
prep_cmd(), start_hosts()), and the space preflight (bs2lib/space.py; space_of(),
run_words() refusing, the plan's lines).  bs2-host's side: test_bs2_tools.py.  Nothing
here reaches a host:

    python3 -m unittest discover -s testvm/backshot2/tests
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from test_bs2_hosts import load_bs2  # noqa: E402
from bs2lib import space as SP  # noqa: E402

B = load_bs2()
GiB = SP.GiB


class Estimates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tsv = os.path.join(self.tmp, "sizes.tsv")
        with open(self.tsv, "w") as f:
            f.write("# test\nsource\ttools_mib\tprep_mib\twhere\ndebian12\t330\t512\tchopin\nnixos2605\t-\t700\tchopin\n")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_table_and_measured(self):
        self.assertEqual(SP.table(self.tsv)["debian12"], (330 << 20, 512 << 20))
        self.assertEqual(SP.table(self.tsv)["nixos2605"], (0, 700 << 20))
        self.assertEqual(SP.estimate("debian12", self.tsv), (330 << 20, 512 << 20, "measured"))

    def test_dump_used_counts_each_filesystem_once_and_no_boot(self):
        u = SP.dump_used("vanilla3")   # / (6.4 GB) and /var (8.4 GB); /boot, /boot/efi and an alias out
        self.assertEqual(u, 6425239552 + 8405016576)
        self.assertEqual(SP.dump_used("guix15"), 1484038144)   # /gnu/store is the root's alias
        self.assertEqual(SP.dump_used("alpine324-lvm"), SP.dump_used("alpine324"))
        self.assertIsNone(SP.dump_used("no-such-guest"))

    def test_estimate_from_the_dump_by_a_high_quantile(self):
        rt, rp = SP.ratios(self.tsv)   # (one measured source with tools: debian12's ratio)
        self.assertAlmostEqual(rt, (330 << 20) / SP.dump_used("debian12"))
        t, p, kind = SP.estimate("vanilla3", self.tsv)
        self.assertEqual(kind, "est.")
        self.assertEqual(t, int(SP.dump_used("vanilla3") * rt))
        self.assertEqual(SP.estimate("no-such-guest", self.tsv), (20 * GiB, 5 * GiB, "est."))
        self.assertEqual(SP.pctl([5, 1, 4, 2, 3, 6, 7, 8, 9, 10]), 10)
        self.assertEqual(SP.pctl(list(range(1, 21))), 19)
        self.assertIsNone(SP.pctl([]))

    def test_the_repository_table_reads(self):
        t = SP.table()
        self.assertIn("vanilla3", t)
        self.assertTrue(all(b >= 0 for row in t.values() for b in row))


class Need(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tsv = os.path.join(self.tmp, "sizes.tsv")
        with open(self.tsv, "w") as f:
            f.write("a\t10240\t2048\th\nb\t20480\t4096\th\nc\t1024\t1024\th\n")   # MiB: a 10+2, b 20+4, c 1+1 GiB

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_nothing_there_yet(self):
        n = SP.need(["a", "b"], {"a"}, {}, 0, 0, self.tsv)
        self.assertEqual(n.end, (2 + 10 + 4) * GiB)   # a's prep and tools, b's prep alone: b isn't led here
        self.assertEqual(n.peak, n.end)

    def test_all_current(self):
        f = {s: {"prep_ok": True, "prep_bytes": GiB, "tools_fresh": True, "tools_bytes": 3 * GiB} for s in "ab"}
        n = SP.need(["a", "b"], {"a", "b"}, f, 0, 0, self.tsv)
        self.assertEqual((n.peak, n.end, n.lines), (0, 0, []))

    def test_tools_dropped_where_not_led_and_old_preps_freed(self):
        f = {"a": {"prep_ok": True, "prep_bytes": GiB, "old_bytes": 2 * GiB, "tools_fresh": True, "tools_bytes": 9 * GiB},
             "b": {"prep_ok": True, "prep_bytes": GiB, "tools_bytes": 19 * GiB}}
        n = SP.need(["a", "b"], {"a"}, f, 0, 0, self.tsv)
        self.assertEqual(n.end, -(2 + 19) * GiB)
        self.assertEqual(n.peak, 0)
        self.assertEqual(n.lines[0], "b tools -19.0")
        self.assertIn("a old preps -2.0", n.lines)

    def test_a_stale_preparation_is_made_before_the_old_one_goes(self):
        # b's prep is stale: a new one (4) first, then its old one (3) goes: the peak holds both.
        f = {"b": {"prep_ok": False, "prep_bytes": 3 * GiB, "tools_fresh": True, "tools_bytes": 20 * GiB}}
        n = SP.need(["b"], {"b"}, f, 0, 0, self.tsv)
        self.assertEqual(n.peak, 4 * GiB)
        self.assertEqual(n.end, GiB)

    def test_old_tools_copied_anew(self):
        # a's tools are a version-1 copy of 15: removed, then the 10 copied: no growth.
        f = {"a": {"prep_ok": True, "prep_bytes": GiB, "tools_fresh": False, "tools_bytes": 15 * GiB}}
        n = SP.need(["a"], {"a"}, f, 0, 0, self.tsv)
        self.assertEqual((n.peak, n.end), (0, -5 * GiB))

    def test_running_sum_peak(self):
        # c first grows 2, then a drops 9 of tools: the peak is after c, not the end.
        f = {"a": {"prep_ok": True, "prep_bytes": GiB, "tools_bytes": 9 * GiB}}
        n = SP.need(["c", "a"], {"c"}, f, 0, 0, self.tsv)
        self.assertEqual(n.peak, 2 * GiB)
        self.assertEqual(n.end, (2 - 9) * GiB)

    def test_the_cases_come_after_the_preparing(self):
        n = SP.need(["c"], {"c"}, {}, 30 * GiB, 200, self.tsv)
        self.assertEqual(n.ssd, 30 * GiB)
        self.assertEqual(n.records, 200 * SP.RECORDS)
        self.assertEqual(n.peak, 2 * GiB + 30 * GiB + 200 * SP.RECORDS)

    def test_check(self):
        n = SP.Need(peak=100 * GiB)
        self.assertIsNone(SP.check(n, 131 * GiB, 30))
        w = SP.check(n, 129 * GiB, 30)
        self.assertIn("needs ~100 GiB on /bkoscratch at its peak + 30 GiB margin = 130 GiB, has 129 GiB free", w)
        self.assertIn("unknown", SP.check(n, None, 30))


class ToolsWhere(unittest.TestCase):
    """bs2: only a source's lead copies its tools, and only when its tier 0 has dry runs."""

    def test_wants_tools(self):
        self.assertTrue(B.wants_tools("debian12"))
        self.assertTrue(B.wants_tools("guix15"))
        self.assertTrue(B.wants_tools("alpine324-luks"))   # (no dump of its own: alpine324's family)
        self.assertFalse(B.wants_tools("nixos2605"))       # (tier 0 "plan": the plan and invariants only)
        self.assertEqual(B.tools_of(["debian12", "nixos2605"]), {"debian12"})

    def test_prep_cmd_copies_the_led_and_drops_the_rest(self):
        c = B.prep_cmd("chopin", ["alpine324", "alpine324-uefi"], "r1", {"alpine324"})
        self.assertIn("source alpine324 --port 2949 --tools", c)
        self.assertIn("source alpine324-uefi --port 2949 --drop-tools", c)
        self.assertNotIn("--tools --", c)

    def test_start_hosts_passes_the_led_ones(self):
        got = {}

        def prepare_one(h, ss, run, tools=()):
            got[h] = set(tools)
            return {}, "stop here"
        can = {"debian12": ["chopin", "cinqcent"], "nixos2605": ["chopin", "cinqcent"], "arch": ["chopin", "cinqcent"]}
        lead = {"debian12": "chopin", "nixos2605": "chopin", "arch": "cinqcent"}
        with mock.patch.object(B, "prepare_one", prepare_one), contextlib.redirect_stdout(io.StringIO()):
            B.start_hosts(list(can), 1, "r1", can, lead, {"chopin": [], "cinqcent": []})
        self.assertEqual(got, {"chopin": {"debian12"}, "cinqcent": {"arch"}})


class SpaceOf(unittest.TestCase):
    def test_tools_only_on_the_lead_and_the_ssd_lane(self):
        can = {"vanilla3": ["chopin", "cinqcent"], "debian12": ["chopin", "cinqcent"]}
        lead = {"vanilla3": "cinqcent", "debian12": "chopin"}
        parts = {"chopin": [("vanilla3", 1, 0), ("debian12", 1, 0)], "cinqcent": [("debian12", 1, 1)]}
        rooms = {"chopin": {"free": 500 * GiB, "sources": {}}, "cinqcent": {"free": 10 * GiB, "sources": {}}}
        with mock.patch.dict(B.SCRATCH, {("chopin", "vanilla3"): "ssd", ("cinqcent", "vanilla3"): "ssd"}):
            sp = B.space_of(["vanilla3", "debian12"], can, lead, parts, rooms, 30)
        v_t, v_p, _ = SP.estimate("vanilla3")
        d_t, d_p, _ = SP.estimate("debian12")
        n, free, why = sp["chopin"]
        self.assertEqual(n.ssd, 30 * GiB)   # vanilla3's D, one case at a time
        self.assertEqual(n.end, v_p + d_p + d_t)   # chopin leads debian12 alone
        self.assertIsNone(why)
        n, free, why = sp["cinqcent"]
        self.assertEqual(n.ssd, 0)   # no vanilla3 case of its own
        self.assertEqual(n.end, v_p + v_t + d_p)
        self.assertIn("has 10 GiB free", why)

    def test_run_refuses_naming_the_host(self):
        hosts = {"chopin": "m@chopin.local", "cinqcent": "m@cinqcent.local"}
        have = {h: {"alpine324", "debian12"} for h in hosts}
        started = []

        def rooms(sources_of):
            return {h: {"free": (900 if h == "chopin" else 20) * GiB, "sources": {}} for h in sources_of}
        with mock.patch.object(B, "RUN_HOSTS", hosts), mock.patch.object(B, "preflight", lambda hs: None), \
                mock.patch.object(B, "sync", lambda hs=None: None), mock.patch.object(B, "rooms_of", rooms), \
                mock.patch.object(B, "slots", lambda h: 2), \
                mock.patch.object(B, "write_manifest", lambda *a: started.append("manifest")), \
                mock.patch.object(B, "start_hosts", lambda *a: started.append("start") or {}), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as e:
                B.run_words(["alpine324", "debian12"], 2, "r1", have, manifest={})
        msg = str(e.exception)
        self.assertIn("/bkoscratch lacks room", msg)
        self.assertRegex(msg, r"cinqcent: needs ~\d+ GiB on /bkoscratch at its peak \+ 30 GiB margin = \d+ GiB, has 20 GiB free")
        self.assertNotIn("chopin:", msg)
        self.assertEqual(started, [], "nothing written or started")

    def test_plan_prints_without_exiting(self):
        out = io.StringIO()
        hosts = {"chopin": "m@chopin.local"}
        with mock.patch.object(B, "RUN_HOSTS", hosts), mock.patch.object(B, "rooms_of", lambda so: {h: None for h in so}), \
                mock.patch.object(B, "slots", lambda h: 2), contextlib.redirect_stdout(out):
            B.space_plan(["debian12"], 2, {"chopin": {"debian12"}})
        self.assertIn("chopin: peak +", out.getvalue())
        self.assertIn("not checked", out.getvalue())


if __name__ == "__main__":
    unittest.main()
