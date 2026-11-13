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
        self.assertEqual(B.tier2_each(B.ALPINE), 10)
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
        self.assertLessEqual(max(walls) - min(walls), max(B.SECS.values()) / min(SLOTS.values()) + 1, walls)

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


class Leads(unittest.TestCase):
    def test_home_when_held_else_the_first(self):
        self.assertEqual(B.leads(can_of(HELD)), B.HOME)
        self.assertEqual(B.leads({"alpine324": ["copland", "cage"], "alpine324-lvm": ["copland"]}),
                         {"alpine324": "copland", "alpine324-lvm": "copland"})

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
        self.assertEqual(B.HOSTS_TABLE.limit("czerny", "bs2_slots"), 2, "a reserved row: its class's")

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
            next(r for r in t["hosts"] if r["name"] == "cage")["bs2_slots"] = 7
            with open(p, "w") as f:
                json.dump(t, f)
            with self.assertRaises(B.HOSTS_TABLE.TableError):
                B.HOSTS_TABLE.load(p)


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
