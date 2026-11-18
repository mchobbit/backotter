"""The swap rule, per host (run.py's HostHolds, 1.9 revised 2026-11-18): a host's swap-out
over the last 10 min holds that host alone, with a hysteresis, and only a hold that lasts
is run-wide; and how SticTest2 (--hold-file), HoltTest2 (max-HOST), GuestTest2 and
ImgTest2 keep a held host from starting anything new.

    cd testvm/test2/tests && python3 -B -m unittest test_hostholds
"""

import os
import shutil
import sys
import tempfile
import threading
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from t2lib import holt, img, run, stic  # noqa: E402
import test_guest as tg  # noqa: E402

GIB = run.GIB
MIN = 60
POL = {"budget": {"run_gb": 2, "sweep_gb": 5, "update_gb": 40, "bind_from": None},
       "stop": {"os_need_guests": 2, "harness_results": 2, "swap_out_gib": 1, "write_budget_x": 2}}


def setUpModule():
    tg.setUpModule()


def tearDownModule():
    tg.tearDownModule()


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def holds(stop=None):
    c = Clock()
    return run.HostHolds(stop or POL["stop"], clock=c), c


class HostHoldsRule(unittest.TestCase):
    def test_a_burst_holds_the_host(self):
        h, c = holds()
        self.assertIsNone(h.feed("stic", "cage", 0))
        c.t += 2 * MIN
        self.assertIsNone(h.feed("stic", "cage", int(1.2 * GIB)))
        self.assertEqual(h.held(), {"cage": "cage: 1.20 GiB swapped out in the last 10 min"})
        self.assertEqual(len(h.notes()), 1)
        self.assertTrue(h.notes()[0].startswith("held cage: 1.20 GiB swapped out in the last 10 min"))

    def test_slow_swap_over_hours_holds_nothing(self):
        """The old rule's case: swap-out counted since the run began tripped on a slow trickle."""
        h, c = holds()
        for i in range(37):                     # 0.5 GiB every 10 min for 6 h: 18 GiB in all
            self.assertIsNone(h.feed("holt", "cage", int(i * 0.5 * GIB)))
            c.t += 10 * MIN
        self.assertEqual(h.held(), {})

    def test_hysteresis(self):
        h, c = holds()
        h.feed("stic", "cage", 0)
        c.t += MIN
        h.feed("stic", "cage", 2 * GIB)
        self.assertIn("cage", h.held())
        for _ in range(13):                     # no more swap-out, but held at least 15 min
            c.t += MIN
            h.feed("stic", "cage", 2 * GIB)
            self.assertIn("cage", h.held(), c.t)
        c.t += 2 * MIN                          # 15 min held, nothing in the window: free
        h.feed("stic", "cage", 2 * GIB)
        self.assertEqual(h.held(), {})
        self.assertTrue(h.notes()[-1].startswith("cage: started again after 15 min held (0.00 GiB"), h.notes())
        c.t += MIN                              # a little more swap: under the trip, no flapping
        h.feed("stic", "cage", int(2.6 * GIB))
        self.assertEqual(h.held(), {})

    def test_held_while_it_still_swaps_over_the_clear_mark(self):
        h, c = holds()
        h.feed("stic", "cage", 0)
        c.t += MIN
        v = 2 * GIB
        h.feed("stic", "cage", v)
        for _ in range(30):                     # 0.05 GiB a minute: 0.5 GiB per 10 min, over 0.25
            c.t += MIN
            v += int(0.05 * GIB)
            h.feed("stic", "cage", v)
        self.assertIn("cage", h.held())

    def test_a_hold_that_lasts_is_run_wide_once(self):
        h, c = holds()
        h.feed("stic", "cage", 0)
        v, said = 0, []
        for _ in range(130):
            c.t += MIN
            v += int(0.2 * GIB)                 # 2 GiB per 10 min, for two hours: held from minute 6
            w = h.feed("stic", "cage", v)
            if w:
                said.append((c.t, w))
        self.assertEqual(len(said), 1, said)
        self.assertEqual(said[0][0], 1000 + 126 * MIN)
        self.assertTrue(said[0][1].startswith("cage: held 120 min by the swap rule and swap-out hasn't eased"), said)

    def test_families_and_counter_resets(self):
        h, c = holds()
        h.feed("stic", "cage", 5 * GIB)         # stic's base is the run start facts: counts at once
        self.assertIn("cage", h.held())
        h, c = holds()
        h.feed("stic", "cage", 0)
        h.feed("holt", "cage", 0)               # holt's base is its own first reading
        c.t += MIN
        h.feed("stic", "cage", int(0.5 * GIB))
        h.feed("holt", "cage", int(0.5 * GIB))  # the same swap seen by two families: 0.5, not 1
        self.assertEqual(h.held(), {})
        c.t += MIN
        self.assertIsNone(h.feed("stic", "cage", 0))     # the counter went back: a new start
        self.assertEqual(h.held(), {})
        self.assertEqual(h.growth("other", c.t), 0)

    def test_policy_keys(self):
        h, c = holds({"swap_out_gib": 2, "swap_window_min": 5, "swap_clear_gib": 0.5, "swap_hold_min": 3,
                      "swap_hold_max_min": 30})
        self.assertEqual((h.trip_b, h.window_s, h.clear_b, h.min_s, h.max_s),
                         (2 * GIB, 5 * MIN, 0.5 * GIB, 3 * MIN, 30 * MIN))


class Engines(unittest.TestCase):
    def test_guards_hold_per_host_and_trip_only_when_it_lasts(self):
        g = run.RunGuards(POL)
        c = Clock()
        g.holds.clock = c
        a, b = run.StopEngine("stic", POL, guards=g), run.StopEngine("holt", POL, guards=g)
        self.assertEqual(a.feed_host("cage", swap_out_bytes=0), "ok")
        c.t += MIN
        self.assertEqual(a.feed_host("cage", swap_out_bytes=3 * GIB), "ok")
        self.assertEqual(list(b.held()), ["cage"])          # another family sees the hold
        self.assertTrue(a.may_start() and b.may_start())    # and every other host starts on
        self.assertEqual((a.stops, b.stops, g.tripped()), ([], [], False))
        v = 3 * GIB
        for _ in range(121):
            c.t += MIN
            v += GIB // 5
            ans = a.feed_host("cage", swap_out_bytes=v)
        self.assertTrue(g.tripped())
        self.assertEqual(ans, "ok")                          # (said once, at the minute it tripped)
        self.assertTrue(a.stops[0].startswith("stop-starts: cage: held 120 min by the swap rule"), a.stops)
        self.assertEqual(b.feed_host("chopin", swap_out_bytes=0), "stop-starts")
        self.assertFalse(b.may_start())
        self.assertTrue(any(n.startswith("held cage") for n in b.hold_notes()))

    def test_engine_without_guards(self):
        s = run.StopEngine("guest", POL)
        s.holds.clock = Clock()
        self.assertEqual(s.feed_host("cage", swap_out_bytes=2 * GIB), "ok")
        self.assertEqual(list(s.held()), ["cage"])
        self.assertEqual(s.stops, [])

    def test_stics_own_stops(self):
        s = stic.Stops(POL)
        s.holds.clock = Clock()
        self.assertEqual(s.feed_host("cage", swap_out_bytes=2 * GIB), "ok")
        self.assertEqual(list(s.held()), ["cage"])
        self.assertFalse(s.stop_starts)


class FakeRemote:
    def __init__(self, files=None):
        self.files = dict(files or {})
        self.writes = []

    def read(self, host, path):
        return self.files.get((host, path))

    def write(self, host, path, text):
        self.writes.append((host, path, text))
        self.files[(host, path)] = text


class HoltHolds(unittest.TestCase):
    QRUN = "t2h-20261118T0000-abcd"

    def runner(self, held, files=None):
        tmp = tempfile.mkdtemp(prefix="t2hh-")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        r = holt.Runner.__new__(holt.Runner)
        r.state = holt.State(os.path.join(tmp, "holt.json"))
        r.state["queues"] = {self.QRUN: {"runners": {"chopin": {"pid": 1}, "cage": {"pid": 2}}}}
        r.qhost = "chopin"
        r.remote = FakeRemote(files)
        r.said = []
        r.log = r.said.append
        r.held = dict(held)
        r.rs = types.SimpleNamespace(stops=types.SimpleNamespace(held=lambda: dict(r.held)))
        return r

    def path(self, h):
        return f"{holt.QUEUE}/{self.QRUN}/max-{h}"

    def todo(self, text):
        return {("chopin", f"{holt.QUEUE}/{self.QRUN}/todo"): text}

    def test_a_held_host_gets_max_0_then_its_own_back(self):
        r = self.runner({"cage": "cage: 1.50 GiB swapped out in the last 10 min"}, self.todo("debian13\n"))
        r.sync_holds(self.QRUN)
        self.assertEqual(r.remote.writes, [("cage", self.path("cage"), "0\n")])
        self.assertEqual(list(r.state["queues"][self.QRUN]["held"]), ["cage"])
        self.assertTrue(any("HOLD cage" in s for s in r.said))
        r.sync_holds(self.QRUN)                                    # nothing changed: nothing written
        self.assertEqual(len(r.remote.writes), 1)
        r.held = {}
        r.sync_holds(self.QRUN)
        self.assertEqual(r.remote.writes[-1], ("cage", self.path("cage"), f"{holt.max_jobs('cage')}\n"))
        self.assertEqual(r.state["queues"][self.QRUN]["held"], {})

    def test_a_max_file_there_before_comes_back(self):
        files = dict(self.todo("debian13\n"))
        files[("cage", self.path("cage"))] = "1\n"
        r = self.runner({"cage": "why"}, files)
        r.sync_holds(self.QRUN)
        r.held = {}
        r.sync_holds(self.QRUN)
        self.assertEqual(r.remote.writes[-1], ("cage", self.path("cage"), "1\n"))

    def test_an_empty_todo_ends_the_hold(self):
        r = self.runner({"cage": "why"}, self.todo("debian13\n"))
        r.sync_holds(self.QRUN)
        r.remote.files.update(self.todo("# all taken\n"))
        r.sync_holds(self.QRUN)
        self.assertEqual(r.remote.writes[-1][2], f"{holt.max_jobs('cage')}\n")
        self.assertTrue(any("the todo is empty" in s for s in r.said), r.said)
        r.sync_holds(self.QRUN)
        self.assertEqual(len(r.remote.writes), 2)

    def test_a_host_with_no_runner_and_no_engine_hold_nothing(self):
        r = self.runner({"nosuch": "why"}, self.todo("x\n"))
        r.sync_holds(self.QRUN)
        r.rs = types.SimpleNamespace(stops=object())
        r.sync_holds(self.QRUN)
        r.sync_holds("t2h-no-such-queue")
        self.assertEqual(r.remote.writes, [])


class GuestHolds(unittest.TestCase):
    def test_a_held_hosts_jobs_go_to_the_other(self):
        names = ["debian12", "arch", "rocky10", "fedora44"]
        jobs = tg.FakeJobs()
        io = tg.FakeIO()
        for n in names:
            io.summ[n] = tg.summary()
        r, rs, _, _ = tg.make(names, io, jobs)
        rs.stops.held = lambda: {"chopin": "chopin: 2.00 GiB swapped out in the last 10 min"}
        th = threading.Thread(target=r.run, daemon=True)
        th.start()
        th.join(20)
        self.assertFalse(th.is_alive())
        self.assertEqual(sorted(c["guest"] for c in jobs.calls), sorted(names))
        self.assertEqual({c["host"] for c in jobs.calls}, {"cinqcent"})


class ImgHolds(unittest.TestCase):
    def test_a_held_hosts_next_image_waits(self):
        r = img.Runner.__new__(img.Runner)
        state = {"n": 0}

        def held():
            return {"cage": "cage: 1.10 GiB swapped out in the last 10 min"} if state["n"] < 2 else {}

        def sleep(s):
            state["n"] += 1
        booted, said = [], []
        r.rs = types.SimpleNamespace(stops=types.SimpleNamespace(held=held))
        r.sleep, r.poll, r.log = sleep, 0, said.append
        r.plat = lambda pc: "linux"
        r.state = types.SimpleNamespace(cell=lambda plat: {})
        r.may_start = lambda plat: True
        r.boot_cell = lambda pc, host: booted.append((state["n"], host))
        r.worker("cage", [types.SimpleNamespace(cell="img:linux", rule="img:new")])
        self.assertEqual(booted, [(2, "cage")])
        self.assertEqual(said, ["cage: HOLD: cage: 1.10 GiB swapped out in the last 10 min: its next image waits"])
        self.assertIsNone(img.Runner.held(types.SimpleNamespace(rs=types.SimpleNamespace(stops=object())), "cage"))


if __name__ == "__main__":
    unittest.main()
