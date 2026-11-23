"""`t2 WORD --lock-wait T` (run.with_lock_wait): a run another run's lock would refuse (R21)
waits for that run to end, then starts.  OtterGrind1 round 3, cycle 2: the cycle's `t2
GuestTest2 garuda ...` was refused at 20:20Z by a fixer's `t2 GuestTest2 oi2610 omnios58`
("another guest run holds its lock: run t2v-20261123T2014-2c2e") and ran nothing.

run.lock_busy / still_busy / taken_cell / wait_unlocked / with_lock_wait, lifecycle's and resume's
busy_raises, and the CLI's option."""

import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
sys.path.insert(0, T2)
sys.path.insert(0, os.path.join(HERE, "fixtures", "p2"))

from t2lib import batch, batchfile as bf, run, words   # noqa: E402
import world                                    # noqa: E402

FIXER, CYCLE, OTHER = "t2v-20261123T2014-2c2e", "t2v-20261123T2020-c2c2", "t2h-20261123T2005-7afb"


def load_t2():
    loader = importlib.machinery.SourceFileLoader("t2_lockwait_under_test", os.path.join(T2, "t2"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Clock:
    """A clock that only sleep() moves; THEN[n] runs in the n-th sleep (1-based)."""

    def __init__(self, then=None):
        self.t, self.sleeps, self.then = 1000.0, [], dict(then or {})

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s
        fn = self.then.pop(len(self.sleeps), None)
        if fn:
            fn()


def word(fam, update=False):
    return types.SimpleNamespace(family=fam, update=update)


class Probes(unittest.TestCase):
    def setUp(self):
        self.mac = tempfile.mkdtemp(prefix="t2-lockwait-")
        self.addCleanup(shutil.rmtree, self.mac, True)
        self.held = []
        self.addCleanup(lambda: [lk.release() for lk in reversed(self.held)])

    def take(self, lk):
        self.held.append(lk)
        return lk

    def test_lock_busy_names_the_holder_and_takes_nothing(self):
        self.assertIsNone(run.lock_busy(["guest"], self.mac))
        lk = self.take(run.FamilyLock("guest", self.mac).acquire(FIXER))
        b = run.lock_busy(["gate", "guest"], self.mac)
        self.assertEqual((b.name, b.what, b.holder["run"]), ("guest", None, FIXER))
        self.assertIn("another guest run holds its lock: run " + FIXER, str(b))
        self.assertIn(batch.LOST_RACE, str(b))
        self.assertEqual(run.read_json(lk.excl_info)["run"], FIXER)      # the holder's record stands
        lk.release()
        self.assertIsNone(run.lock_busy(["gate", "guest"], self.mac))
        run.FamilyLock("guest", self.mac).acquire(CYCLE, wait=0.1).release()   # and nothing was kept

    def test_a_shared_holder_refuses_only_who_wants_it_whole(self):
        self.take(run.FamilyLock("holt", self.mac).acquire(OTHER, shared=True))
        self.assertIsNone(run.lock_busy(["holt"], self.mac, shared=("holt",)))    # a HoltTest2 word's own run
        b = run.lock_busy(["img", "holt"], self.mac)                              # ImgTest2: holt whole
        self.assertEqual((b.name, b.holder["run"]), ("holt", OTHER))
        self.held.pop().release()
        self.take(run.FamilyLock("holt", self.mac).acquire(OTHER))                # and an exclusive one refuses both
        self.assertIsNotNone(run.lock_busy(["holt"], self.mac, shared=("holt",)))
        self.assertIsNotNone(run.lock_busy(["holt"], self.mac))

    def test_still_busy_follows_the_cell_or_the_family(self):
        cells = self.take(run.CellLocks("holt", self.mac).hold(["holt:debian13"], OTHER))
        try:
            run.CellLocks("holt", self.mac).hold(["holt:debian13"], "x", wait=0.05)
            self.fail("held")
        except run.Busy as e:
            b = e
        self.assertEqual((b.name, b.what), ("holt", "holt:debian13"))
        again = run.still_busy(b, self.mac, shared=("holt",))
        self.assertEqual((again.what, again.holder["run"]), ("holt:debian13", OTHER))
        cells.release()
        self.assertIsNone(run.still_busy(b, self.mac, shared=("holt",)))
        fam = self.take(run.FamilyLock("guest", self.mac).acquire(FIXER))
        gb = run.lock_busy(["guest"], self.mac)
        self.assertEqual(run.still_busy(gb, self.mac).holder["run"], FIXER)
        fam.release()
        self.assertIsNone(run.still_busy(gb, self.mac))

    def test_fmt_wait(self):
        self.assertEqual([run.fmt_wait(s) for s in (0, 20, 90, 3600, 5400, 7200)],
                         ["0 s", "20 s", "2 min", "1 h 00 min", "1 h 30 min", "2 h 00 min"])


class Waits(unittest.TestCase):
    def setUp(self):
        self.mac = tempfile.mkdtemp(prefix="t2-lockwait-")
        self.addCleanup(shutil.rmtree, self.mac, True)
        self.said = []

    def test_no_wait_is_the_plain_attempt(self):
        lk = run.FamilyLock("guest", self.mac).acquire(FIXER)
        self.addCleanup(lk.release)
        calls = []
        rc = run.with_lock_wait(word("guest"), 0, lambda raises: calls.append(raises) or run.EXIT_REFUSED,
                                mac_root=self.mac, out=self.said.append)
        self.assertEqual((rc, calls, self.said), (run.EXIT_REFUSED, [False], []))     # (lifecycle says R21's refusal)

    def test_it_waits_for_the_holder_then_runs(self):
        """Cycle 2's case: the fixer's guest run holds the lock, and ends 14 minutes later."""
        lk = run.FamilyLock("guest", self.mac).acquire(FIXER)
        self.addCleanup(lk.release)
        ck = Clock({168: lk.release})        # (14 min of 5 s looks)
        calls = []

        def attempt(raises):
            calls.append((raises, ck.now()))
            self.assertIsNone(run.lock_busy(["guest"], self.mac))
            return run.EXIT_OK

        rc = run.with_lock_wait(word("guest"), 7200, attempt, mac_root=self.mac, out=self.said.append,
                                clock=ck.now, sleep=ck.sleep)
        self.assertEqual(rc, run.EXIT_OK)
        self.assertEqual(calls, [(True, 1000.0 + 168 * run.LOCK_POLL_S)])
        self.assertEqual(len(self.said), 1, self.said)                    # one line for one holder
        self.assertIn(f"another guest run holds its lock: run {FIXER}", self.said[0])
        self.assertIn("waiting for it to end (--lock-wait, 2 h 00 min left)", self.said[0])

    def test_a_free_lock_costs_no_wait(self):
        ck = Clock()
        rc = run.with_lock_wait(word("guest"), 600, lambda raises: run.EXIT_FAIL if raises else None,
                                mac_root=self.mac, out=self.said.append, clock=ck.now, sleep=ck.sleep)
        self.assertEqual((rc, ck.sleeps, self.said), (run.EXIT_FAIL, [], []))

    def test_the_wait_runs_out_in_r21s_words(self):
        lk = run.FamilyLock("guest", self.mac).acquire(FIXER)
        self.addCleanup(lk.release)
        ck = Clock()
        calls = []
        rc = run.with_lock_wait(word("guest"), 60, lambda raises: calls.append(raises), mac_root=self.mac,
                                out=self.said.append, clock=ck.now, sleep=ck.sleep)
        self.assertEqual((rc, calls), (run.EXIT_REFUSED, []))
        self.assertAlmostEqual(sum(ck.sleeps), 60)
        self.assertIn(f"another guest run holds its lock: run {FIXER}", self.said[-1])
        self.assertIn("(--lock-wait 1 min ran out)", self.said[-1])
        self.assertIn(batch.LOST_RACE, self.said[-1])

    def test_a_lock_lost_to_another_waiter_is_waited_for_again(self):
        """Two waiters: the other one takes the lock between this one's look and its acquire."""
        other = run.FamilyLock("guest", self.mac)
        ck = Clock({3: other.release})
        self.addCleanup(other.release)
        calls = []

        def attempt(raises):
            calls.append(raises)
            if len(calls) == 1:
                other.acquire(CYCLE)
                raise run.Busy(other.holder(), "guest")
            return run.EXIT_OK

        rc = run.with_lock_wait(word("guest"), 600, attempt, mac_root=self.mac, out=self.said.append,
                                clock=ck.now, sleep=ck.sleep)
        self.assertEqual((rc, calls, len(ck.sleeps)), (run.EXIT_OK, [True, True], 3))
        self.assertEqual(len(self.said), 1, self.said)
        self.assertIn(CYCLE, self.said[0])

    def test_a_busy_that_is_free_at_once_still_waits_a_step(self):
        """(a plan that saw a cell held just before its holder ended: no spin of pulls and plans)"""
        ck = Clock()
        calls = []

        def attempt(raises):
            calls.append(ck.now())
            if len(calls) < 3:
                raise run.Busy({"run": OTHER}, "holt", "holt:debian13")
            return run.EXIT_OK

        rc = run.with_lock_wait(word("holt"), 600, attempt, mac_root=self.mac, out=self.said.append,
                                clock=ck.now, sleep=ck.sleep)
        self.assertEqual(rc, run.EXIT_OK)
        self.assertEqual(calls, [1000.0, 1000.0 + run.LOCK_POLL_S, 1000.0 + 2 * run.LOCK_POLL_S])
        self.assertIn("on holt:debian13", self.said[0])

    def test_a_busy_after_the_deadline_refuses(self):
        ck = Clock()

        def attempt(raises):
            ck.t += 700
            raise run.Busy({"run": OTHER}, "guest")

        rc = run.with_lock_wait(word("guest"), 600, attempt, mac_root=self.mac, out=self.said.append,
                                clock=ck.now, sleep=ck.sleep)
        self.assertEqual(rc, run.EXIT_REFUSED)
        self.assertIn("ran out", self.said[-1])

    def test_the_update_round_waits_for_every_family(self):
        lk = run.FamilyLock("stic", self.mac).acquire("t2s-20261123T2005-9de5")
        ck = Clock({2: lk.release})
        self.addCleanup(lk.release)
        calls = []
        rc = run.with_lock_wait(word("guest", update=True), 600, lambda raises: calls.append(raises) or 0,
                                mac_root=self.mac, out=self.said.append, clock=ck.now, sleep=ck.sleep)
        self.assertEqual((rc, calls, len(ck.sleeps)), (0, [True], 2))
        self.assertIn("another stic run holds its lock", self.said[0])


class Lifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t2-lockwait-world-")
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
        self.seen, self.said = [], []

        def fake_run(c, p):
            self.seen.append(sorted(pc.cell for pc in p.due()))
            return {"cells": [], "stops": [], "wear": {}, "exit": 0}

        run.runner = lambda fam: types.SimpleNamespace(run=fake_run, resume=lambda *a, **k: self.fail("resumed"))

    def tearDown(self):
        run.runner, run.copies.make, old = self.saved
        if old is None:
            os.environ.pop("T2_MAC_ROOT", None)
        else:
            os.environ["T2_MAC_ROOT"] = old

    def life(self, text, raises=False):
        ctx = self.w.ctx([])
        return run.lifecycle(self.w.word(text, ctx), ctx, mac_root=self.mac, out=self.said.append, busy_raises=raises)

    def waited(self, text, wait, ck):
        w = self.w.word(text, self.w.ctx([]))
        return run.with_lock_wait(w, wait, lambda raises: self.life(text, raises), mac_root=self.mac,
                                  out=self.said.append, clock=ck.now, sleep=ck.sleep)

    def test_a_held_guest_lock_refuses_without_the_option(self):
        lk = run.FamilyLock("guest", self.mac).acquire(FIXER)
        self.addCleanup(lk.release)
        self.assertEqual(self.life("GuestTest2 garuda proxmox9"), run.EXIT_REFUSED)
        self.assertIn(f"t2: another guest run holds its lock: run {FIXER}", self.said[0])
        self.assertEqual(self.seen, [])

    def test_a_held_guest_lock_is_raised_for_the_wait(self):
        lk = run.FamilyLock("guest", self.mac).acquire(FIXER)
        self.addCleanup(lk.release)
        with self.assertRaises(run.Busy) as cm:
            self.life("GuestTest2 garuda proxmox9", raises=True)
        self.assertEqual((cm.exception.name, cm.exception.what, cm.exception.holder["run"]), ("guest", None, FIXER))
        self.assertEqual((self.said, self.seen), ([], []))

    def test_the_cycles_guest_step_waits_out_a_fixers_run(self):
        lk = run.FamilyLock("guest", self.mac).acquire(FIXER)
        self.addCleanup(lk.release)
        ck = Clock({4: lk.release})
        rc = self.waited("GuestTest2 garuda nixos2605 proxmox9 haiku6 oi2610-bios uc24", 7200, ck)
        self.assertNotEqual(rc, run.EXIT_REFUSED, self.said)
        self.assertEqual(self.seen, [sorted("guest:" + g for g in "garuda nixos2605 proxmox9 haiku6 oi2610-bios uc24".split())])
        self.assertEqual(len(ck.sleeps), 4)
        self.assertIn("waiting for it to end", self.said[0])
        self.assertFalse(run.FamilyLock("guest", self.mac).held())

    def test_a_holt_cell_another_run_holds_is_waited_for_and_then_runs(self):
        fam = run.FamilyLock("holt", self.mac).acquire(OTHER, shared=True)
        cells = run.CellLocks("holt", self.mac).hold(["holt:debian12"], OTHER)
        self.addCleanup(fam.release)
        self.addCleanup(cells.release)
        # without the option: the cell is another run's, the word refuses (R21 as amended)
        self.assertEqual(self.life("HoltTest2 debian12"), run.EXIT_REFUSED)
        self.assertIn("running in " + OTHER, "\n".join(self.said))
        del self.said[:]
        # with it: a Busy on the cell, and nothing of this run's is left held
        with self.assertRaises(run.Busy) as cm:
            self.life("HoltTest2 debian12 arch", raises=True)
        self.assertEqual((cm.exception.name, cm.exception.what, cm.exception.holder["run"]),
                         ("holt", "holt:debian12", OTHER))
        self.assertEqual(run.CellLocks.held_by("holt", self.mac), {"holt:debian12": OTHER})
        self.assertEqual([h["run"] for h in run.FamilyLock("holt", self.mac).holders()], [OTHER])
        self.assertEqual(self.seen, [])

        def end():
            cells.release()
            fam.release()

        ck = Clock({3: end})
        rc = self.waited("HoltTest2 debian12 arch", 3600, ck)
        self.assertNotEqual(rc, run.EXIT_REFUSED, self.said)
        self.assertEqual(self.seen, [["holt:arch", "holt:debian12"]])       # both, not arch alone
        self.assertEqual(len(ck.sleeps), 3)
        self.assertIn(f"another holt run holds its lock on holt:debian12: run {OTHER}", self.said[0])
        self.assertEqual(run.CellLocks.held_by("holt", self.mac), {})

    def test_holt_runs_over_other_cells_wait_for_nothing(self):
        fam = run.FamilyLock("holt", self.mac).acquire(OTHER, shared=True)
        cells = run.CellLocks("holt", self.mac).hold(["holt:debian12"], OTHER)
        self.addCleanup(fam.release)
        self.addCleanup(cells.release)
        # (host work began: a live run beside a new one isn't "unfinished")
        run.write_json(os.path.join(run.run_dir(OTHER, self.mac), "plan.json"), {"family": "holt", "cells": []})
        ck = Clock()
        rc = self.waited("HoltTest2 arch", 3600, ck)
        self.assertNotEqual(rc, run.EXIT_REFUSED, self.said)
        self.assertEqual((self.seen, ck.sleeps), ([["holt:arch"]], []))

    def test_resume_raises_a_held_lock_for_the_wait(self):
        self.assertNotEqual(self.life("GuestTest2 garuda"), run.EXIT_REFUSED, self.said)
        runs = [n for n in os.listdir(run.runs_dir(self.mac)) if n.startswith("t2v-")]
        self.assertEqual(len(runs), 1, runs)
        lk = run.FamilyLock("guest", self.mac).acquire(FIXER)
        self.addCleanup(lk.release)
        del self.said[:]
        ctx = self.w.ctx([])
        self.assertEqual(run.resume(runs[0], ctx, mac_root=self.mac, out=self.said.append), run.EXIT_REFUSED)
        self.assertIn(f"another guest run holds its lock: run {FIXER}", self.said[0])
        del self.said[:]
        with self.assertRaises(run.Busy) as cm:
            run.resume(runs[0], self.w.ctx([]), mac_root=self.mac, out=self.said.append, busy_raises=True)
        self.assertEqual((cm.exception.holder["run"], self.said), (FIXER, []))


class Cli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t2 = load_t2()

    def test_the_options_durations(self):
        f = self.t2.lock_wait_opt
        self.assertEqual([f({}), f({"--lock-wait": "2h"}), f({"--lock-wait": "90m"}), f({"--lock-wait": "45"})],
                         [0, 7200, 5400, 45])
        with self.assertRaises(self.t2.Usage) as cm:
            f({"--lock-wait": "soon"})
        self.assertIn("--lock-wait: not a duration", str(cm.exception))

    def test_the_words_and_resume_take_it(self):
        opts, rest = self.t2.take_opts("GuestTest2 garuda --lock-wait 2h --harness-stop 4".split(),
                                       {"--harness-stop": True, "--lock-wait": True})
        self.assertEqual((opts, rest), ({"--lock-wait": "2h", "--harness-stop": "4"}, ["GuestTest2", "garuda"]))
        self.assertIn("--lock-wait T", self.t2.__doc__)
        with self.assertRaises(self.t2.Usage) as cm:
            self.t2.cmd_resume(["t2w-20261123T2005-7afb", "--lock-wait", "1h"])
        self.assertIn("not with a composite's resume", str(cm.exception))

    def test_a_batch_entry_cant_carry_it(self):
        """(the batch waits for a session's lock itself: its lock-wait setting)"""
        e = bf.parse("t2 GuestTest2 garuda --lock-wait 2h\n", "t.batch", None).entries[0]
        self.assertIn("--lock-wait: no such t2 option in a batch entry", bf._t2_refusals(e, words, None, False))


if __name__ == "__main__":
    unittest.main()
