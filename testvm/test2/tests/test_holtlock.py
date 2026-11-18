"""HoltTest2's cell locks (R21 as amended 2026-11-18): runs over disjoint cells go side by side,
two that share a cell (or the +img pins) don't, and a word that needs the whole family still
waits out every holt run.  run.FamilyLock shared, run.CellLocks, the plan's running_skip,
lifecycle and RunState.replan under them, and batch's view of shared holders."""

import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, "fixtures", "p2"))

from t2lib import batch, batchfile as bf, plan, run   # noqa: E402
import world                                         # noqa: E402

A, B, C = "t2h-20261118T0100-aaaa", "t2h-20261118T0101-bbbb", "t2h-20261118T0102-cccc"


class Locks(unittest.TestCase):
    def setUp(self):
        self.mac = tempfile.mkdtemp(prefix="t2-holtlock-")
        self.addCleanup(shutil.rmtree, self.mac, True)
        self.held = []

    def tearDown(self):
        for lk in reversed(self.held):
            lk.release()

    def take(self, lk):
        self.held.append(lk)
        return lk

    def test_shared_holders_go_together_and_exclude_an_exclusive_one(self):
        a = self.take(run.FamilyLock("holt", self.mac).acquire(A, shared=True))
        b = self.take(run.FamilyLock("holt", self.mac).acquire(B, wait=0.1, shared=True))
        self.assertTrue(run.FamilyLock("holt", self.mac).held())
        self.assertEqual(sorted(h["run"] for h in run.FamilyLock("holt", self.mac).holders()), [A, B])
        self.assertTrue({A, B} <= run.held_runs(self.mac))
        with self.assertRaises(run.Busy) as cm:                       # ImgTest2's panel, BkoTest2 ...
            run.FamilyLock("holt", self.mac).acquire(C, wait=0.1)
        self.assertIn("another holt run holds its lock", str(cm.exception))
        self.assertTrue(A in str(cm.exception) or B in str(cm.exception), str(cm.exception))
        a.release()
        b.release()
        self.assertFalse(run.FamilyLock("holt", self.mac).held())
        self.assertEqual(run.FamilyLock("holt", self.mac).holders(), [])
        x = self.take(run.FamilyLock("holt", self.mac).acquire(C, wait=0.1))
        with self.assertRaises(run.Busy) as cm:                       # and the other way round
            run.FamilyLock("holt", self.mac).acquire(A, wait=0.1, shared=True)
        self.assertIn(C, str(cm.exception))
        x.release()

    def test_a_dead_holders_record_is_no_holder(self):
        lk = run.FamilyLock("holt", self.mac)
        os.makedirs(lk.dir, exist_ok=True)
        run.write_json(os.path.join(lk.dir, "pid-999999-dead00.json"), {"run": A, "pid": 999999, "start": "x"})
        self.assertEqual(lk.holders(), [])
        run.write_json(lk.excl_info, {"run": B, "pid": 1, "start": "x"})    # a crashed exclusive holder's
        self.take(run.FamilyLock("holt", self.mac).acquire(C, shared=True))
        self.assertEqual([h["run"] for h in lk.holders()], [C])

    def test_cells_all_or_none_and_the_img_pins(self):
        a = self.take(run.CellLocks("holt", self.mac).hold(["holt:arch", "holt:nixos2605+img"], A))
        b = self.take(run.CellLocks("holt", self.mac).hold(["holt:debian13"], B))    # disjoint: fine
        c = self.take(run.CellLocks("holt", self.mac))
        with self.assertRaises(run.Busy) as cm:
            c.hold(["holt:debian12", "holt:debian13"], C, wait=0.1)
        self.assertIn("another holt run holds its lock on holt:debian13: run " + B, str(cm.exception))
        self.assertIn(batch.LOST_RACE, str(cm.exception))      # the batch's lost-race words
        self.assertEqual(c.units(), [])                         # none of them kept
        with self.assertRaises(run.Busy) as cm:                 # another +img cell: the pins are A's
            c.hold(["holt:debian12+img"], C, wait=0.1)
        self.assertIn("on +img", str(cm.exception))
        self.assertEqual(run.CellLocks.held_by("holt", self.mac),
                         {"holt:arch": A, "holt:nixos2605+img": A, "+img": A, "holt:debian13": B})
        self.assertEqual(run.CellLocks.held_by("holt", self.mac, exclude=(A,)), {"holt:debian13": B})
        a.retag("t2h-20261118T0100-a2a2")
        self.assertEqual(run.CellLocks.held_by("holt", self.mac)["holt:arch"], "t2h-20261118T0100-a2a2")
        a.release()
        c.hold(["holt:debian12+img"], C, wait=0.1)
        self.assertEqual(c.units(), ["+img", "holt:debian12+img"])
        b.release()
        c.release()
        self.assertEqual(run.CellLocks.held_by("holt", self.mac), {})

    def test_running_cells_reads_the_cell_holders(self):
        fl = self.take(run.FamilyLock("holt", self.mac).acquire(A, shared=True))
        self.take(run.CellLocks("holt", self.mac).hold(["holt:arch", "holt:mint22+img"], A))
        self.assertEqual(run.running_cells("holt", self.mac),
                         {"holt:arch": A, "holt:mint22+img": A, "+img": A})
        self.assertEqual(run.running_cells("holt", self.mac, exclude=(A,)), {})
        self.assertEqual(fl.holder()["run"], A)

    def test_cell_locked_words(self):
        w = lambda fam, update=False: types.SimpleNamespace(family=fam, update=update)
        self.assertTrue(run.cell_locked(w("holt")))
        for fam in ("img", "stic", "guest", "gate", "bko", "sweep"):
            self.assertFalse(run.cell_locked(w(fam)), fam)

    def test_batch_finds_a_shared_holders_run(self):
        e = bf.parse("t2 HoltTest2 debian13\n", "t.batch", None).entries[0]
        self.take(run.FamilyLock("holt", self.mac).acquire(A, shared=True))
        self.assertEqual(batch.discover_run(e, os.getpid(), self.mac), A)
        self.assertEqual([s.run for s in batch.session_claims(self.mac)], [A])
        self.assertEqual(batch.session_claims(self.mac, ours_runs={A}), [])

    def test_the_build_lock_is_no_family_lock(self):
        self.take(run.FamilyLock("holt-bin-" + "c" * 16, self.mac).acquire(A))
        self.assertFalse(run.FamilyLock("holt", self.mac).held())
        self.assertEqual(run.held_runs(self.mac), set())


class Planned(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t2-holtlock-world-")
        cls.w = world.World(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.saved = (run.runner, run.copies.make, os.environ.get("T2_MAC_ROOT"))
        self.mac = tempfile.mkdtemp(prefix="mac-", dir=self.tmp)
        os.environ["T2_MAC_ROOT"] = self.mac
        run.copies.make = lambda checkout, hosts, log=None, pv=None: {
            h: types.SimpleNamespace(id="fixture", path=f"/fake/{h}") for h in hosts}

    def tearDown(self):
        run.runner, run.copies.make, old = self.saved
        if old is None:
            os.environ.pop("T2_MAC_ROOT", None)
        else:
            os.environ["T2_MAC_ROOT"] = old

    def test_running_skip_makes_another_runs_cells_not_runnable(self):
        p = self.w.plan("HoltTest2 debian13 debian12 nixos2605+img plan",
                        running={"holt:debian13": A, "+img": B}, running_skip=True)
        st = {c.cell: (c.status, c.running) for c in p.cells}
        self.assertEqual(st["holt:debian12"], ("due", None))
        self.assertEqual(st["holt:debian13"], ("not-runnable", A))
        self.assertEqual(st["holt:nixos2605+img"], ("not-runnable", B))
        self.assertIn("running in " + A, p.cell("holt:debian13").reasons)
        self.assertEqual(p.totals["due"], 1)
        # `t2 plan` (no running_skip) only says so
        p = self.w.plan("HoltTest2 debian13 plan", running={"holt:debian13": A})
        self.assertEqual((p.cell("holt:debian13").status, p.cell("holt:debian13").running), ("due", A))

    def life(self, text, out):
        ctx = self.w.ctx([])
        return run.lifecycle(self.w.word(text, ctx), ctx, mac_root=self.mac, out=out.append)

    def test_two_runs_over_other_cells_go_together(self):
        seen, outs = {}, {}

        def fake_run(c, p):
            due = sorted(pc.cell for pc in p.due())
            seen[tuple(due)] = c.run.run
            if due == ["holt:arch", "holt:debian12"]:
                # (host work began: a live run beside a new one isn't "unfinished")
                run.write_json(os.path.join(c.run.mac_dir, "cells-chopin.json"), {"cells": {}})
                for text in ("HoltTest2 debian13", "HoltTest2 debian12", "HoltTest2 debian12 mint22"):
                    outs[text] = []
                    outs[text + " rc"] = self.life(text, outs[text])
                # the whole family (ImgTest2's panel): refused while a holt run holds cells
                with self.assertRaises(run.Busy):
                    run.acquire_locks(["img", "holt"], "pending", self.mac, wait=0.1)
            else:
                outer = seen[("holt:arch", "holt:debian12")]
                running = run.running_cells("holt", self.mac)
                self.assertEqual(running.get("holt:arch"), outer)
                self.assertEqual(running.get(due[0]), c.run.run)
            return {"cells": [], "stops": [], "wear": {}, "exit": 0}

        run.runner = lambda fam: types.SimpleNamespace(run=fake_run)
        out = []
        self.assertNotEqual(self.life("HoltTest2 arch debian12", out), run.EXIT_REFUSED, out)
        outer = seen[("holt:arch", "holt:debian12")]
        self.assertIn(("holt:debian13",), seen)                        # disjoint: it ran beside
        self.assertNotEqual(outs["HoltTest2 debian13 rc"], run.EXIT_REFUSED, outs["HoltTest2 debian13"])
        self.assertEqual(outs["HoltTest2 debian12 rc"], run.EXIT_REFUSED)  # the same cell: refused, and why
        self.assertIn("running in " + outer, "\n".join(outs["HoltTest2 debian12"]))
        self.assertIn(("holt:mint22",), seen)                           # the shared cell left out, the rest ran
        self.assertNotIn(("holt:debian12", "holt:mint22"), seen)
        self.assertFalse(run.FamilyLock("holt", self.mac).held())
        self.assertEqual(run.CellLocks.held_by("holt", self.mac), {})
        pj = run.read_json(os.path.join(run.run_dir(seen[("holt:mint22",)], self.mac), "plan.json"))
        d12 = [c for c in pj["cells"] if c["cell"] == "holt:debian12"][0]
        self.assertEqual((d12["status"], d12["running"]), ("not-runnable", outer))

    def test_a_held_cell_costs_a_new_plan_not_the_run(self):
        """Two runs planning at once: the one that loses a cell plans again without it."""
        other = run.CellLocks("holt", self.mac)
        seen = []

        def fake_run(c, p):
            seen.append(sorted(pc.cell for pc in p.due()))
            return {"cells": [], "stops": [], "wear": {}, "exit": 0}

        real = run.running_cells
        calls = []

        def racing(fam, mac_root=None, exclude=()):
            calls.append(exclude)
            if len(calls) == 1:   # the first look sees nothing; then B takes debian12
                other.hold(["holt:debian12"], B)
                return {}
            return real(fam, mac_root, exclude)

        run.runner = lambda fam: types.SimpleNamespace(run=fake_run)
        try:
            with mock.patch.object(run, "running_cells", racing):
                out = []
                self.assertNotEqual(self.life("HoltTest2 arch debian12", out), run.EXIT_REFUSED, out)
        finally:
            other.release()
        self.assertEqual(seen, [["holt:arch"]])

    def test_replan_holds_what_it_adds_and_leaves_what_others_hold(self):
        ctx = self.w.ctx([])
        wd = self.w.word("HoltTest2 arch debian12", ctx)
        p = plan.make(wd, ctx)
        rs = run.RunState(A, wd, p, ctx, mac_root=self.mac, log=None)
        rs.cell_locks = run.CellLocks("holt", self.mac).hold(["holt:arch"], A)
        other = run.CellLocks("holt", self.mac).hold(["holt:debian12"], B)
        try:
            new, added, dropped = rs.replan()
            self.assertEqual(new.cell("holt:debian12").status, "not-runnable")
            self.assertEqual(rs.cell_locks.units(), ["holt:arch"])
            # a stale look at the others (a race): the re-plan fails and the plan stays
            with mock.patch.object(run, "running_cells", lambda *a, **k: {}):
                with self.assertRaises(run.Busy):
                    rs.replan()
            self.assertIs(rs.plan, new)
            other.release()
            new, added, dropped = rs.replan()
            self.assertEqual(added, ["holt:debian12"])
            self.assertEqual(rs.cell_locks.units(), ["holt:arch", "holt:debian12"])
            with self.assertRaises(run.Busy):
                run.CellLocks("holt", self.mac).hold(["holt:debian12"], C, wait=0.1)
        finally:
            other.release()
            rs.cell_locks.release()


if __name__ == "__main__":
    unittest.main()
