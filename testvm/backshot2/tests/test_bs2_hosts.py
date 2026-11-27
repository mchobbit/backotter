"""Unit tests for bs2's spreading of a run over the hosts: split() (disjoint, complete,
in proportion to the slots, only to hosts that hold the source), the slots from
testvm/hosts.json's bs2_slots, the leads, the pick lists, the plan, and bs2-host
queue's slot cap.  Nothing here reaches a host (held() is stood in for):

    python3 -m unittest discover -s testvm/backshot2/tests
"""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BS2_DIR = os.path.dirname(HERE)
TABLE = os.path.join(BS2_DIR, "..", "hosts.json")


def load_bs2():
    loader = importlib.machinery.SourceFileLoader("bs2_cli", os.path.join(BS2_DIR, "bs2"))
    spec = importlib.util.spec_from_loader("bs2_cli", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


B = load_bs2()
SLOTS = {"chopin": 5, "cinqcent": 5, "copland": 2, "cage": 2}
# What the hosts hold (2026-11-13): cage's cache has alpine324 only.
HELD = {"chopin": set(B.ALPINE), "cinqcent": set(B.ALPINE), "copland": set(B.ALPINE), "cage": {"alpine324"}}


def can_of(held):
    return {s: [h for h in SLOTS if s in held[h]] for s in B.ALPINE}


class Split(unittest.TestCase):
    def setUp(self):
        self.jobs = B.jobs_of(B.ALPINE, 200)
        self.can = can_of(HELD)
        self.parts = B.split(self.jobs, self.can, SLOTS)

    def test_jobs_of(self):
        self.assertEqual(len(self.jobs), 4 * 200 + 30)
        self.assertEqual({s: B.tier2_of(s, B.ALPINE) for s in B.ALPINE},
                         {"alpine324": 0, "alpine324-uefi": 10, "alpine324-lvm": 10, "alpine324-luks": 10})
        self.assertFalse([j for j in self.jobs if j[0] == "alpine324" and j[1] == 2], "alpine324 has no tier 2")

    def test_disjoint_and_complete(self):
        got = [j for js in self.parts.values() for j in js]
        self.assertEqual(len(got), len(set(got)), "a job on two hosts")
        self.assertEqual(sorted(got), sorted(self.jobs), "a job lost or made up")

    def test_honours_held(self):
        for h, js in self.parts.items():
            for s, _, _ in js:
                self.assertIn(s, HELD[h], f"{h} got {s}, which it doesn't hold")
        self.assertTrue(self.parts["cage"], "cage takes alpine324's")
        self.assertEqual({s for s, _, _ in self.parts["cage"]}, {"alpine324"})

    def test_keeps_the_run_order(self):
        for js in self.parts.values():
            self.assertEqual(js, sorted(js, key=self.jobs.index))

    def test_hosts_finish_about_together(self):
        walls = [B.wall(js, SLOTS[h]) for h, js in self.parts.items()]
        longest = max(B.secs(s, t) for s, t, _ in self.jobs)
        self.assertLessEqual(max(walls) - min(walls), longest / min(SLOTS.values()) + 1, walls)

    def test_in_proportion_to_the_slots(self):
        """Where every host holds the source, the cases go as the slots: 5:5:2:2."""
        jobs = [("alpine324", 1, i) for i in range(280)]
        parts = B.split(jobs, {"alpine324": list(SLOTS)}, SLOTS)
        self.assertEqual({h: len(js) for h, js in parts.items()}, {"chopin": 100, "cinqcent": 100, "copland": 40, "cage": 40})
        parts = B.split(jobs[:274], {"alpine324": list(SLOTS)}, SLOTS)
        for h, js in parts.items():
            self.assertLessEqual(abs(len(js) - 274 * SLOTS[h] / 14), 1, (h, len(js)))

    def test_a_lead_sweep_takes_its_share_down(self):
        jobs = [("alpine324", 1, i) for i in range(200)]
        even = B.split(jobs, {"alpine324": ["chopin", "cinqcent"]}, {"chopin": 5, "cinqcent": 5})
        swept = B.split(jobs, {"alpine324": ["chopin", "cinqcent"]}, {"chopin": 5, "cinqcent": 5}, {"chopin": 600})
        self.assertEqual(len(even["chopin"]), 100)
        self.assertLess(len(swept["chopin"]), 100)
        self.assertEqual(len(swept["chopin"]) + len(swept["cinqcent"]), 200)

    def test_one_host(self):
        parts = B.split(self.jobs, {s: ["cinqcent"] for s in B.ALPINE}, {"cinqcent": 5})
        self.assertEqual(parts["cinqcent"], self.jobs)

    def test_a_source_nobody_holds(self):
        with self.assertRaises(ValueError):
            B.split([("alpine324-lvm", 1, 0)], {"alpine324-lvm": ["copland"]}, {"cage": 2})


class GuixSources(unittest.TestCase):
    """Guix System's two sources beside Alpine's: their own HOME deal (Alpine's kept as it
    was), tier 2 for the UEFI one only, and --sweep and --tier2's counts."""

    def tearDown(self):
        B.COUNTS.clear()

    def test_home_and_firmware(self):
        self.assertEqual(B.ALPINE, list(B.ALPINE_SOURCES))
        self.assertEqual({s: B.HOME[s] for s in B.GUIX_SOURCES}, dict(zip(B.GUIX_SOURCES, B.LEAD_HOSTS[:2] * 2)))
        self.assertEqual(B.deal(B.ALPINE_SOURCES, B.LEAD_HOSTS), {s: B.HOME[s] for s in B.ALPINE})
        self.assertIn("guix15", B.UEFI)
        self.assertNotIn("guix15-bios", B.UEFI)
        self.assertNotIn("alpine324", B.UEFI)

    def test_counts(self):
        g = list(B.GUIX_SOURCES)
        self.assertEqual(B.tier2_of("guix15", g), B.TIER2_EACH)   # a UEFI source not Alpine's: 5
        self.assertEqual(B.tier2_of("guix15-bios", g), 0)
        jobs = B.jobs_of(g, 10)
        self.assertEqual(sum(1 for s, t, _ in jobs if s == "guix15-bios" and t == 2), 0)
        self.assertEqual(sum(1 for s, t, _ in jobs if s == "guix15" and t == 2), B.TIER2_EACH)
        self.assertEqual(B.sweep_n(10), 50)
        B.COUNTS.update(sweep=200, tier2=3)
        jobs = B.jobs_of(g, 10)
        self.assertEqual([(s, t) for s, t, _ in jobs].count(("guix15", 2)), 3)
        self.assertEqual([(s, t) for s, t, _ in jobs].count(("guix15-bios", 1)), 10)
        self.assertEqual(B.sweep_n(10), 200)
        self.assertEqual(B.sweep_secs(g, 10, {"guix15": "chopin", "guix15-bios": "cinqcent"}),
                         {"chopin": 200 * B.secs("guix15", 0), "cinqcent": 200 * B.secs("guix15-bios", 0)})
        B.COUNTS.update(tier2=0)
        self.assertFalse(any(t == 2 for _, t, _ in B.jobs_of(g, 10)))

    def test_rooms(self):
        from bs2lib import room as RM
        self.assertEqual(B.prep_room("alpine324"), "2 3")
        self.assertEqual(RM.of("alpine324"), (2, 3))   # bs2's own figures, not HoltTest1's MEM 4
        self.assertEqual(RM.of("guix15"), (7, 4))      # ram-sizes.tsv's
        self.assertEqual(RM.of("guix15-bios"), (6, 4))
        self.assertEqual(RM.of("no-such-guest"), (2, 3))
        self.assertEqual(B.prep_room("guix15"), "2 4")
        self.assertIn(f"reserve /x.case --mem 3 --words) && {B.HOST_BS2} lane /x.case -- ./cmp-run.sh $R sudo -E",
                      B.reserve_tier1("/x.case"))

    def test_holt_passed_on(self):
        with mock.patch.dict(os.environ, {"BS2_HOLT": "/bkoscratch/x/bko-holt", "BS2_FAST_DOWN": "0"}):
            self.assertEqual(B.reuse_env(), "BS2_FAST_DOWN=0 BS2_HOLT=/bkoscratch/x/bko-holt ")
            self.assertIn(f"BS2_HOLT=/bkoscratch/x/bko-holt {B.HOST_BS2} lane /x.case -- ./cmp-run.sh", B.reserve_tier1("/x.case"))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BS2_HOLT", None)
            self.assertNotIn("BS2_HOLT", B.reuse_env())

    def test_tree_name(self):
        self.assertEqual(B.WT, os.environ.get("BS2_WT") or "bs2-wt")
        self.assertTrue(B.HOST_BS2.startswith(f"~/backotter-work/{B.WT}/"))


class Leads(unittest.TestCase):
    def test_home_when_held_else_the_first(self):
        self.assertEqual(B.leads(can_of(HELD)), {s: B.HOME[s] for s in B.ALPINE})
        self.assertEqual(B.leads({"alpine324": ["copland", "cage"], "alpine324-lvm": ["copland"]}),
                         {"alpine324": "copland", "alpine324-lvm": "copland"})

    def test_no_big_default_host_refuses_the_run(self):
        """The leads hold every source: a table with no default amd64 host of the first class is
        a table to mend, not a run to deal to the small hosts (their caches hold alpine324 at
        most, so every case of another source would die on its host)."""
        with open(TABLE) as f:
            t = json.load(f)
        for r in t["hosts"]:
            if r["name"] in ("chopin", "cinqcent"):
                r["default"] = False
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "hosts.json")
            with open(p, "w") as f:
                json.dump(t, f)
            r = subprocess.run([sys.executable, os.path.join(BS2_DIR, "bs2"), "ls"], capture_output=True, text=True,
                               env=dict(os.environ, BKO_HOSTS_JSON=p))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("no default amd64 host of class big", r.stderr)
        self.assertNotIn("copland", r.stdout)

    def test_holders_refuses_a_source_nobody_has(self):
        with self.assertRaises(SystemExit):
            B.holders(["alpine324-uefi"], {"cage": {"alpine324"}}, ["cage"])

    def test_picks_of(self):
        text = ("pick-alpine324-uefi-t2.txt:2 /r/cases/alpine324-uefi-c.case\n"
                "pick-alpine324-uefi-t1.txt:1 /r/cases/alpine324-uefi-a.case\n"
                "pick-alpine324-t1.txt:1 /r/cases/alpine324-b.case\n"
                "pick-alpine324-uefi-t1.txt:1 /r/cases/alpine324-uefi-d.case\nnoise\n")
        self.assertEqual(B.picks_of(text, ["alpine324", "alpine324-uefi"]),
                         [("alpine324", 1, "/r/cases/alpine324-b.case"), ("alpine324-uefi", 1, "/r/cases/alpine324-uefi-a.case"),
                          ("alpine324-uefi", 1, "/r/cases/alpine324-uefi-d.case"), ("alpine324-uefi", 2, "/r/cases/alpine324-uefi-c.case")])


class Slots(unittest.TestCase):
    def test_from_the_table(self):
        self.assertEqual({h: B.slots(h) for h in SLOTS}, SLOTS)
        self.assertEqual(B.HOSTS_TABLE.limit("czerny", "bs2_slots"), 2, "czerny: the small class's")

    def test_a_row_of_its_own_and_the_cap(self):
        with open(TABLE) as f:
            t = json.load(f)
        next(r for r in t["hosts"] if r["name"] == "cage")["bs2_slots"] = 6
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "hosts.json")
            with open(p, "w") as f:
                json.dump(t, f)
            with mock.patch.dict(os.environ, {B.HOSTS_TABLE.ENV: p}):
                self.assertEqual(B.slots("cage"), 6)
                self.assertEqual(B.slots("copland"), 2)
            # A path of its own: a bad re-read of p would keep the table read there (hosts.py
            # load keeps the last good table, for a file edited under a long run).
            next(r for r in t["hosts"] if r["name"] == "cage")["bs2_slots"] = 7
            bad = os.path.join(d, "hosts-7.json")
            with open(bad, "w") as f:
                json.dump(t, f)
            with self.assertRaisesRegex(B.HOSTS_TABLE.TableError, r"bs2_slots 7: 1 to 6"):
                B.HOSTS_TABLE.load(bad)


class Plan(unittest.TestCase):
    def test_plan_adds_up(self):
        out = io.StringIO()
        with mock.patch.object(B, "held", lambda sources, hosts=None: {h: set(HELD[h]) & set(sources) for h in SLOTS}), \
                mock.patch.object(B, "RUN_HOSTS", {h: f"m@{h}.local" for h in SLOTS}), contextlib.redirect_stdout(out):
            B.plan_words(B.ALPINE, 200)
        text = out.getvalue()
        got = {}
        for line in text.splitlines():
            if line.split(":")[0] in SLOTS and " cases (" in line:
                h = line.split(":")[0]
                got[h] = int(line.split(" slots, ")[1].split(" cases")[0])
                self.assertIn(f"{SLOTS[h]} slots", line)
        self.assertEqual(sum(got.values()), 830, text)
        self.assertEqual(set(got), set(SLOTS), text)
        self.assertIn("the run: ~", text)


def load_host():
    loader = importlib.machinery.SourceFileLoader("bs2_host_mod", os.path.join(BS2_DIR, "bs2-host"))
    spec = importlib.util.spec_from_loader("bs2_host_mod", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


class Room(unittest.TestCase):
    """A host's cmp-run.sh --ceiling against a source's room (the review of 96b13ec: choubox,
    3.0 6.0, has both Guix guests cached; their cases reserve 7 4 and 6 4, which cmp-run.sh
    refuses there at once, and the queue left no row)."""

    def test_need(self):
        from bs2lib import room as RM
        self.assertEqual(RM.need("guix15"), (7, 4))
        self.assertEqual(RM.need("guix15-bios"), (6, 4))
        self.assertEqual(RM.need("alpine324"), (2, 3))

    def test_ceiling_of(self):
        self.assertEqual(B.ceiling_of("guix15\n@ceiling 9.0 10.1\n"), (9.0, 10.1))
        self.assertIsNone(B.ceiling_of("@ceiling \nguix15\n"))
        self.assertIsNone(B.ceiling_of("guix15\n"))

    def test_roomy(self):
        both = {"guix15", "guix15-bios", "alpine324"}
        self.assertEqual(B.roomy(both, (3.0, 6.0)), ({"alpine324"}, {"guix15", "guix15-bios"}))   # choubox
        self.assertEqual(B.roomy(both, (9.0, 10.1))[0], both)   # copland, cage, czerny
        self.assertEqual(B.roomy(both, (29.0, 23.2))[0], both)  # chopin, cinqcent
        self.assertEqual(B.roomy({"guix15"}, (6.9, 23.0))[0], set())
        self.assertEqual(B.roomy({"guix15"}, (7.0, 3.9))[0], set())
        self.assertEqual(B.roomy(both, None), (set(), both))

    def test_held_leaves_out_what_a_host_cant_hold(self):
        out = {"chopin": "@ceiling 29.0 23.2\nguix15\nguix15-bios\n", "choubox": "@ceiling 3.0 6.0\nguix15\nguix15-bios\nalpine324\n",
               "copland": "@ceiling 9.0 10.1\nguix15-bios\n", "cage": "@ceiling \nguix15\n"}

        def ssh(h, cmd, check=True, capture=False, input=None):
            self.assertIn("cmp-run.sh --ceiling", cmd)
            return subprocess.CompletedProcess([], 0, out[h], "")

        # img-speed F3: the table's scratch_mode overflow lets choubox take Guix's cases on its SSD.
        err = io.StringIO()
        with mock.patch.object(B, "ssh", ssh), mock.patch.dict(B.SCRATCH, clear=True), contextlib.redirect_stderr(err):
            got = B.held(["guix15", "guix15-bios", "alpine324"], list(out))
            self.assertEqual(B.SCRATCH[("choubox", "guix15")], "ssd")
            self.assertEqual(B.SCRATCH[("copland", "guix15-bios")], "ram")
        self.assertEqual(got, {"chopin": {"guix15", "guix15-bios"}, "choubox": {"alpine324", "guix15", "guix15-bios"},
                               "copland": {"guix15-bios"}, "cage": set()})
        self.assertIn("cage has vms/guix15, but its cmp-run.sh --ceiling didn't answer", err.getvalue())
        # A host that takes no SSD cases (a scratch_mode ram row) leaves them out, and the plan deals it none.
        err = io.StringIO()
        with mock.patch.object(B, "ssh", ssh), mock.patch.object(B, "disk_ok", lambda h: h != "choubox"), \
                mock.patch.dict(B.SCRATCH, clear=True), contextlib.redirect_stderr(err):
            got = B.held(["guix15", "guix15-bios", "alpine324"], list(out))
        self.assertEqual(got, {"chopin": {"guix15", "guix15-bios"}, "choubox": {"alpine324"}, "copland": {"guix15-bios"}, "cage": set()})
        self.assertIn("choubox has vms/guix15, but its cmp-run.sh --ceiling is 3 6, under guix15's cases' --reserve 7 4", err.getvalue())
        with mock.patch.object(B, "ssh", ssh), mock.patch.object(B, "RUN_HOSTS", {h: f"m@{h}.local" for h in out}), \
                mock.patch.object(B, "disk_ok", lambda h: h != "choubox"), mock.patch.dict(B.SCRATCH, clear=True), \
                mock.patch.object(B, "slots", lambda h: 2), contextlib.redirect_stderr(io.StringIO()):
            can, lead, parts, _ = B.placement(["guix15", "guix15-bios"], 10)
        self.assertEqual(can, {"guix15": ["chopin"], "guix15-bios": ["chopin", "copland"]})
        self.assertFalse(parts.get("choubox"), parts)


class QueueRows(unittest.TestCase):
    """bs2-host queue's row for a case that wrote none (cmp-run.sh refused its --reserve)."""
    H = load_host()

    def case(self, d):
        from bs2lib import case as C
        p = os.path.join(d, "cases", "guix15-abc.case")
        os.makedirs(os.path.dirname(p))
        C.write(p, {"name": "guix15-abc", "source": "guix15@g1", "seed": 1, "edits": [], "load_options": []})
        return p

    def test_a_refused_case_gets_a_harness_row(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.case(d)
            row = self.H.unrowed(d, "1", p, 1, "--reserve 7 4 can never fit here (at most 3.0 6.0)\n")
            self.assertEqual((row["case"], row["verdict"], row["tier"], row["source"]), ("guix15-abc", "HARNESS", 1, "guix15"))
            self.assertIn("can never fit here", row["why"][0])
            with open(os.path.join(d, "tier1-queue.jsonl")) as f:
                self.assertEqual(json.loads(f.read()), row)

    def test_a_case_with_its_own_row_or_a_pass_gets_none(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.case(d)
            self.assertIsNone(self.H.unrowed(d, "1", p, 0, ""))
            with open(os.path.join(d, "tier1.jsonl"), "w") as f:
                f.write(json.dumps({"case": "guix15-abc", "verdict": "FAIL"}) + "\n")
            self.assertIsNone(self.H.unrowed(d, "1", p, 1, "boom"))
            self.assertFalse(os.path.exists(os.path.join(d, "tier1-queue.jsonl")))


class QueueSlots(unittest.TestCase):
    def test_queue_refuses_more_slots_than_ports(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ("7", "0"):
                r = subprocess.run([sys.executable, os.path.join(BS2_DIR, "bs2-host"), "queue", "r", "--slots", n],
                                   env=dict(os.environ, BS2_ROOT=d), capture_output=True, text=True)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("2943-2948", r.stderr)


if __name__ == "__main__":
    unittest.main()
