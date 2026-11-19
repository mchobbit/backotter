"""The host rule, per host (run.py's HostWatch, 2026-11-19): a host that stops answering or
reboots is held alone, as the swap rule's hold is, and lifted once it has answered for a
settle period; the results of its jobs from that window are host-lost: no stop rule counts
them (SweepTest2 #4: chopin's maintenance power-off at 15:30Z gave two HARNESS and four LOST
results, and the run-wide harness count stopped stic, guest and holt on four hosts that
were fine), and each family requeues them once: GuestTest2 at once to a host it may run on,
SticTest2 and HoltTest2 in their r2 round (rerun_why host-lost: a pass then is a PASS),
HoltTest2's queue a runner again on its host once it is back, a gate layer once its host is
back (a slow one's starts no stop).  A result whose host the rule has looked at waits for the
host to be seen answering a while after it (a row written as the host shut down lands before
the host stops answering).  Real harness results on a healthy host still count.

    cd testvm/test2/tests && python3 -B -m unittest test_hostloss
"""

import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from t2lib import gate, holt, hostjob, rows_holt, rows_stic, run, stic, utc_now  # noqa: E402
import test_gate as tgate  # noqa: E402
import test_guest as tg  # noqa: E402

MIN = 60
POL = {"budget": {"run_gb": 2, "sweep_gb": 5, "update_gb": 40, "bind_from": None},
       "stop": {"os_need_guests": 2, "harness_results": 2, "swap_out_gib": 1, "write_budget_x": 2}}
A, B = "0b6c5f6e-1111-4c2e-9d6a-aaaaaaaaaaaa", "0b6c5f6e-2222-4c2e-9d6a-bbbbbbbbbbbb"


def setUpModule():
    tg.setUpModule()
    tgate.setUpModule()
    os.makedirs(os.environ["T2_MAC_ROOT"], exist_ok=True)


def tearDownModule():
    tgate.tearDownModule()
    tg.tearDownModule()


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def up(c, boot_at, boot=A):
    """A look at a host that booted at BOOT_AT (on clock C)."""
    return {"boot": boot, "up_s": c.t - boot_at}


def watch(stop=None):
    c = Clock()
    return run.HostWatch(stop or POL["stop"], clock=c), c


class HostWatchRule(unittest.TestCase):
    def test_no_answer_for_a_minute_holds_then_settles(self):
        w, c = watch()
        b0 = c.t - 99999
        w.feed("chopin", up(c, b0))
        c.t += MIN
        self.assertIsNone(w.feed("chopin", None))           # one failed look: not yet
        self.assertEqual(w.held(), {})
        c.t += 30
        w.feed("chopin", None)
        self.assertEqual(w.held(), {})
        c.t += 30
        w.feed("chopin", None)                               # a minute without an answer
        self.assertEqual(list(w.held()), ["chopin"])
        self.assertTrue(w.held()["chopin"].startswith("chopin: no answer since "))
        for _ in range(4):                                   # it answers (no reboot): settling
            c.t += MIN
            w.feed("chopin", up(c, b0))
            self.assertIn("chopin", w.held())
        c.t += MIN
        w.feed("chopin", None)                               # a failed look starts the settle again
        for _ in range(5):                                   # (5 min from the first answer after it)
            c.t += MIN
            w.feed("chopin", up(c, b0))
            self.assertIn("chopin", w.held(), c.t)
        c.t += MIN
        w.feed("chopin", up(c, b0))
        self.assertEqual(w.held(), {})
        self.assertTrue(any(n.startswith("chopin: started again after") for n in w.notes()), w.notes())

    def test_a_reboot_holds_until_it_has_been_up_the_settle_time(self):
        w, c = watch()
        w.feed("chopin", up(c, c.t - 3600, A))
        c.t += 10 * MIN
        boot = c.t - 30
        w.feed("chopin", up(c, boot, B))                     # back already, another boot id
        self.assertTrue(w.held()["chopin"].startswith("chopin: rebooted at "))
        c.t += 4 * MIN
        w.feed("chopin", up(c, boot, B))
        self.assertIn("chopin", w.held())
        c.t += 2 * MIN
        w.feed("chopin", up(c, boot, B))
        self.assertEqual(w.held(), {})
        self.assertTrue(w.rebooted("chopin", boot - 120))
        self.assertFalse(w.rebooted("chopin", boot + 10))

    def test_lost_windows_and_boots(self):
        w, c = watch()
        t0 = c.t
        w.feed("chopin", up(c, t0 - 3600))
        w.feed("cage", up(c, t0 - 3600))
        c.t += 10 * MIN
        boot = c.t - 60
        w.feed("chopin", up(c, boot, B))
        self.assertIsNotNone(w.lost("chopin", t0, c.t))                         # held now
        c.t += 10 * MIN
        w.feed("chopin", up(c, boot, B))                                         # settled: lifted
        self.assertEqual(w.held(), {})
        self.assertIn("rebooted", w.lost("chopin", t0 + MIN, c.t))               # began before the boot
        self.assertIsNone(w.lost("chopin", boot + 2 * MIN, c.t))                 # began after it
        self.assertIsNone(w.lost("cage", t0, c.t))                               # a healthy host
        self.assertIsNone(w.lost("nosuch", t0, c.t))
        self.assertIn("rebooted", w.lost("chopin", utc_now(t0 + MIN), utc_now(c.t)))   # UTC times too

    def test_a_host_that_booted_before_any_look_still_counts(self):
        """The first look after the reboot: no boot id to compare, but its uptime says when."""
        w, c = watch()
        start = c.t - 20 * MIN
        w.feed("chopin", up(c, c.t - 5 * MIN))
        self.assertIn("rebooted", w.lost("chopin", start, c.t))
        self.assertEqual(w.held(), {})                                           # (nothing to hold on)

    def test_a_hold_that_lasts_is_run_wide_once(self):
        w, c = watch()
        w.feed("chopin", up(c, c.t - 3600))
        whys = []
        for _ in range(125):
            c.t += MIN
            whys.append(w.feed("chopin", None))
        said = [x for x in whys if x]
        self.assertEqual(len(said), 1)
        self.assertTrue(said[0].startswith("chopin: held 120 min by the host rule"), said)

    def test_policy_keys(self):
        w, c = watch(dict(POL["stop"], host_away_s=10, host_settle_min=1, host_hold_max_min=30))
        self.assertEqual((w.away_s, w.settle_s, w.max_s), (10, 60, 1800))


class Engines(unittest.TestCase):
    def sweep(self):
        g = run.RunGuards(POL)
        c = Clock()
        g.watch.clock = c
        return g, c, {f: run.StopEngine(f, POL, guards=g) for f in ("stic", "guest", "holt")}

    def test_sweeptest2_4(self):
        """Chopin goes away: its results are host-lost, no family stops, and only chopin is held;
        two real harness results on a healthy host still stop the run."""
        g, c, e = self.sweep()
        t0 = c.t
        for h in ("chopin", "cage"):
            self.assertEqual(e["guest"].feed_seen(h, up(c, t0 - 86400)), "ok")
        for _ in range(3):
            c.t += MIN
            e["guest"].feed_seen("chopin", None)
        self.assertEqual(list(e["stic"].held()), ["chopin"])                  # every family sees it
        for fam, cell, v in (("holt", "holt:vanilla3", "LOST"), ("guest", "guest:elementary8-luks", "HARNESS"),
                             ("stic", "stic:debian13-zfs", "LOST"), ("stic", "stic:garuda", "LOST"),
                             ("guest", "guest:ubuntu26-luks", "HARNESS")):
            why = e[fam].host_lost("chopin", t0 - 30 * MIN, c.t)
            self.assertTrue(why, cell)
            res = {"cell": cell, "verdict": v, "class": "harness" if v == "HARNESS" else "lost", "host": "chopin",
                   "host_lost": why}
            self.assertEqual(e[fam].feed(res), "ok", cell)
        self.assertEqual((g.harness, g.tripped()), (0, False))
        self.assertTrue(all(eng.may_start() for eng in e.values()))
        self.assertEqual(len([n for n in e["stic"].host_notes() if "host lost" in n]), 5)
        self.assertTrue(any(n.startswith("held chopin: no answer since") for n in e["holt"].host_notes()))
        self.assertIsNone(e["stic"].host_lost("cage", t0, c.t))
        self.assertEqual(e["stic"].feed({"cell": "stic:arch", "verdict": "HARNESS", "class": "harness", "host": "cage",
                                         "end": c.t}), "ok")
        self.assertEqual(e["guest"].feed({"cell": "guest:arch", "verdict": "HARNESS", "class": "harness",
                                          "host": "cage", "end": c.t}), "ok")
        self.assertEqual(g.harness, 0)                      # (pending: cage may be going away too)
        c.t += MIN
        self.assertEqual(e["guest"].feed_seen("cage", up(c, t0 - 86400)), "ok")
        c.t += 2 * MIN
        self.assertEqual(e["holt"].feed_seen("cage", up(c, t0 - 86400)), "ok")   # (holt has none pending)
        self.assertEqual(e["stic"].feed_seen("cage", up(c, t0 - 86400)), "ok")   # cage answers: they count
        self.assertEqual(g.harness, 1)
        self.assertEqual(e["guest"].feed_seen("cage", up(c, t0 - 86400)), "stop-run")
        self.assertTrue(g.tripped())

    def test_a_row_written_as_its_host_shut_down_is_host_lost(self):
        """holt:vanilla3's LOST row (exit 143, 15:30:21Z) landed before chopin stopped answering."""
        g, c, e = self.sweep()
        t0 = c.t
        e["holt"].feed_seen("chopin", up(c, t0 - 86400))
        c.t += 15
        res = {"cell": "holt:vanilla3", "verdict": "LOST", "class": "lost", "host": "chopin", "start": t0 - 3000,
               "end": c.t - 5}
        self.assertIsNone(e["holt"].host_lost("chopin", res["start"], res["end"]))
        self.assertEqual(e["holt"].feed(res), "ok")
        for _ in range(4):                                  # it shuts down
            c.t += 30
            e["guest"].feed_seen("chopin", None)
        self.assertEqual(g.harness, 0)
        self.assertEqual(e["holt"].feed_seen("chopin", None), "ok")
        self.assertEqual((e["holt"].pending, g.harness), ([], 0))
        self.assertTrue(any(n.startswith("holt:vanilla3 LOST on chopin: host lost") for n in e["holt"].host_notes()))

    def test_pending_results_count_at_the_end_or_after_the_wait(self):
        g, c, e = self.sweep()
        e["stic"].feed_seen("cage", up(c, c.t - 86400))
        e["stic"].feed({"cell": "stic:arch", "verdict": "HARNESS", "class": "harness", "host": "cage"})
        self.assertEqual(g.harness, 0)
        e["stic"].settle(final=True)
        self.assertEqual(g.harness, 1)
        e["guest"].feed({"cell": "guest:arch", "verdict": "HARNESS", "class": "harness", "host": "cage"})
        c.t += 16 * MIN                                     # no look since: host_confirm_max_min
        self.assertFalse(e["guest"].may_start())

    def test_a_hold_that_lasts_stops_starts(self):
        g, c, e = self.sweep()
        e["stic"].feed_seen("chopin", up(c, c.t - 3600))
        ans = []
        for _ in range(125):
            c.t += MIN
            ans.append(e["stic"].feed_seen("chopin", None))
        self.assertEqual(ans.count("stop-starts"), 1)
        self.assertTrue(g.tripped())
        self.assertFalse(e["holt"].may_start())

    def test_swap_and_host_holds_merge(self):
        g, c, e = self.sweep()
        g.holds.clock = c
        e["stic"].feed_host("chopin", swap_out_bytes=0)
        c.t += MIN
        e["stic"].feed_host("chopin", swap_out_bytes=3 * run.GIB)
        e["stic"].feed_seen("chopin", up(c, c.t - 3600, A))
        c.t += MIN
        e["stic"].feed_seen("chopin", up(c, c.t - 10, B))
        w = e["holt"].held()["chopin"]
        self.assertIn("swapped out", w)
        self.assertIn("rebooted", w)

    def test_engine_and_stics_own_stops_without_guards(self):
        for s in (run.StopEngine("guest", POL), stic.Stops(POL)):
            c = Clock()
            s.watch.clock = c
            s.feed_seen("chopin", up(c, c.t - 3600))
            c.t += 2 * MIN
            s.feed_seen("chopin", up(c, c.t - 30, B))
            self.assertEqual(list(s.held()), ["chopin"])
            why = s.host_lost("chopin", c.t - 30 * MIN, c.t)
            self.assertTrue(why)
            for _ in range(3):
                s.feed({"cell": "x:y", "verdict": "LOST", "class": "lost", "host": "chopin", "host_lost": why})
            self.assertFalse(s.stop_run)
            self.assertTrue(s.rebooted("chopin", c.t - 30 * MIN))
            self.assertEqual(len(s.host_notes()), 4)


class HostJob(unittest.TestCase):
    def test_parse_state(self):
        seen, sw = hostjob.parse_state(f"pswpout 10\n\nboot {A}\n\nup 1234.56\n\n")
        self.assertEqual((seen, sw), ({"boot": A, "up_s": 1234.56}, 40960))
        self.assertEqual(hostjob.parse_state("7\n"), ({"boot": None, "up_s": None}, 28672))   # a bare pswpout
        self.assertEqual(hostjob.parse_state(""), ({"boot": None, "up_s": None}, None))

    def test_host_state_no_answer(self):
        class IO:
            def sh(self, host, cmd, timeout=60, input=None):
                self.cmd = cmd
                return (255, "", "ssh: connect to host chopin.local port 22: Host is down") if host == "chopin" \
                    else (0, f"pswpout 1\nboot {A}\nup 5\n", "")
        io = IO()
        self.assertEqual(hostjob.host_state(io, "chopin"), (None, None))
        self.assertEqual(hostjob.host_state(io, "cage"), ({"boot": A, "up_s": 5.0}, 4096))
        self.assertIn("pswpout", io.cmd)
        self.assertIn("boot_id", io.cmd)

    def test_host_lost_text_and_a_look(self):
        s = run.StopEngine("guest", POL)
        c = Clock()
        s.watch.clock = c
        self.assertEqual(hostjob.host_lost(s, "chopin", c.t - 60, c.t, text="harness: no answer from chopin in 20 polls; "
                                           "pid 4 may still run"), "no answer from chopin in 20 polls")
        self.assertIsNone(hostjob.host_lost(s, "chopin", c.t - 60, c.t, text="no answer from cage in 20 polls"))
        looks = []
        self.assertIsNone(hostjob.host_lost(s, "chopin", c.t - 600, c.t,
                                            seen=lambda h: looks.append(h) or up(c, c.t - 86400)))
        self.assertEqual(looks, ["chopin"])
        self.assertIn("rebooted", hostjob.host_lost(s, "chopin", c.t - 600, c.t, seen=lambda h: up(c, c.t - 60, B)))
        self.assertIsNone(hostjob.host_lost(object(), "chopin", c.t - 600, c.t))      # an engine without the rule

    def test_host_checks_feed_the_rule(self):
        state = {"down": True}

        class IO:
            def sh(self, host, cmd, timeout=60, input=None):
                if host == "chopin" and state["down"]:
                    return 255, "", "Host is down"
                return 0, f"pswpout 0\nboot {A}\nup 99999\n", ""
        s = run.StopEngine("guest", POL)
        c = Clock()
        s.watch.clock = c
        hosted = []
        s.feed_host = lambda h, **k: hosted.append(h) or "ok"
        env = types.SimpleNamespace(io=IO(), hosts=["chopin", "cage"], swap0={}, log=lambda t: None)
        rs = types.SimpleNamespace(stops=s, hosts=env.hosts)
        hostjob.host_checks(env, rs, "guest:t2v-x", c.t)
        c.t += 2 * MIN
        hostjob.host_checks(env, rs, "guest:t2v-x", c.t)
        self.assertEqual(list(s.held()), ["chopin"])
        self.assertEqual(hosted, ["cage", "cage"])                    # (no answer: nothing more read there)
        state["down"] = False
        for _ in range(7):
            c.t += MIN
            hostjob.host_checks(env, rs, "guest:t2v-x", c.t)
        self.assertEqual(s.held(), {})

    def test_wait_back(self):
        s = run.StopEngine("gate", POL)
        c = Clock()
        s.watch.clock = c
        s.watch.feed("chopin", up(c, c.t - 3600))
        c.t += 2 * MIN
        s.watch.feed("chopin", None)
        c.t += 2 * MIN
        s.watch.feed("chopin", None)
        self.assertIn("chopin", s.held())
        said = []

        def sleep(x):
            c.t += MIN
        self.assertTrue(hostjob.wait_back(s, "chopin", lambda h: up(c, c.t - 99999), sleep, 3600, said.append, clock=c))
        self.assertEqual(len(said), 1)
        c.t += MIN
        s.watch.feed("chopin", None)
        c.t += 2 * MIN
        s.watch.feed("chopin", None)
        self.assertFalse(hostjob.wait_back(s, "chopin", lambda h: None, sleep, 300, clock=c))
        self.assertFalse(hostjob.wait_back(s, "chopin", lambda h: None, sleep, 3600, cancelled=lambda: True, clock=c))


class Verdicts(unittest.TestCase):
    def test_stic_a_pass_after_a_host_lost_attempt_is_a_pass(self):
        lines = [("PASS", "backup", "ok")]
        self.assertEqual(rows_stic.judge(lines, 0, attempt=2)[0], "FLAKY-FAIL")
        v, cls, _n, _c, _f, notes = rows_stic.judge(lines, 0, attempt=2, rerun_why="host-lost")
        self.assertEqual((v, cls), ("PASS", "pass"))
        self.assertTrue(any("host lost" in n for n in notes))

    def test_holt_a_pass_after_a_host_lost_attempt_is_a_pass(self):
        rep = {"result": "RESULT: PASS"}
        self.assertEqual(rows_holt.classify(rep, "", 0, 0, attempt=2)[0], "FLAKY-FAIL")
        self.assertEqual(rows_holt.classify(rep, "", 0, 0, attempt=2, rerun_why="host-lost")[:2], ("PASS", "pass"))


class Guest(unittest.TestCase):
    def jobs(self, outcome):
        """host_job: attempt N's (rc, why) from OUTCOME(host, attempt); FakeIO's rows otherwise."""
        calls = []

        def job(env, host, name, attempt, cmd, reserve, wear_tag, timeout_s, wait_s, extra_env, ram, subdir, on_began=None):
            calls.append((host, attempt))
            if on_began:
                on_began()
            why = outcome(host, attempt)
            d = hostjob.job_dir(env, name, attempt, subdir)
            if why is not None:
                return None, d, {}, why
            return 0, d, {"submitted": 1, "began": 2, "ended": 3, "exit": 0}, None
        job.calls = calls
        return job

    def make(self, outcome):
        io = tg.FakeIO()
        io.summ["debian12"] = tg.summary()
        job = self.jobs(outcome)
        r, rs, _, _ = tg.make(["debian12"], io, job)
        rs.stops = run.StopEngine("guest", POL)
        rs.stops.watch.confirm_s = 0.05      # (a result waits that long for its host's confirm)
        return r, rs, job

    def test_no_answer_requeues_once_to_another_host(self):
        def outcome(host, attempt):
            if attempt == 1:
                return hostjob.JobFailure(f"no answer from {host} in 20 polls; pid 4 may still run", still_running=True)
            return None
        r, rs, job = self.make(outcome)
        res = r.run()
        self.assertEqual(len(job.calls), 2)
        (h1, a1), (h2, a2) = job.calls
        self.assertEqual((a1, a2), (1, 2))
        self.assertNotEqual(h1, h2)                     # its job may still run there: never beside it
        c = res["cells"][0]
        self.assertEqual((c["verdict"], c["attempt"]), ("PASS", 2))
        self.assertTrue(any("host lost" in n and "requeued as attempt 2" in n for n in res["notes"]), res["notes"])
        self.assertEqual(rs.stops.harness, 0)

    def test_lost_twice_is_not_counted_and_not_requeued_again(self):
        r, rs, job = self.make(lambda host, attempt: hostjob.JobFailure(f"no answer from {host} in 20 polls; pid 4 may "
                                                                        "still run", still_running=True))
        res = r.run()
        self.assertEqual(len(job.calls), 2)
        c = res["cells"][0]
        self.assertEqual(c["verdict"], "HARNESS")
        self.assertEqual((rs.stops.harness, rs.stops.stop_run), (0, False))
        self.assertTrue(any("not requeued (requeued once already)" in n for n in res["notes"]), res["notes"])

    def test_a_job_ended_as_its_host_shut_down_is_requeued(self):
        """job.sh's row, written as its TERM came from the shutdown, reaches the Mac before the
        host stops answering: the attempt waits for the host's confirm, and the host goes away."""
        state = {"down": False}
        r, rs, job = self.make(lambda host, attempt: None)
        r.io.summ["debian12"] = lambda a: (tg.summary("HARNESS", "harness", harness="stopped from outside")
                                           if a == 1 else tg.summary())
        sh = r.io.sh

        def down_sh(host, cmd, timeout=60, input=None):
            if host == "chopin" and state["down"] and "boot_id" in cmd:
                return 255, "", "Host is down"
            return sh(host, cmd, timeout, input)
        r.io.sh = down_sh
        rs.stops.watch.away_s = 0.02
        rs.stops.watch.confirm_s = 0.5

        def go_down():
            while not r.unsure:
                time.sleep(0.001)
            state["down"] = True
        th = threading.Thread(target=go_down, daemon=True)
        th.start()
        res = r.run()
        self.assertEqual([a for _, a in job.calls], [1, 2])
        self.assertEqual(job.calls[1][0], "cinqcent")                  # (chopin is held)
        self.assertEqual(res["cells"][0]["verdict"], "PASS")
        self.assertEqual(rs.stops.harness, 0)

    def test_a_real_harness_result_still_counts(self):
        r, rs, job = self.make(lambda host, attempt: hostjob.JobFailure("the job didn't start on " + host))
        res = r.run()
        self.assertEqual(len(job.calls), 1)
        self.assertEqual(res["cells"][0]["verdict"], "HARNESS")
        self.assertEqual((rs.stops.harness, len(rs.stops.pending)), (0, 1))   # its host was looked at: it waits
        rs.stops.settle(final=True)                                            # (run.finish)
        self.assertEqual(rs.stops.harness, 1)


class Stic(unittest.TestCase):
    def runner(self, seen=None):
        r = stic.SticRun.__new__(stic.SticRun)
        r.state, r.stops, r.seen = {}, [], set()
        r.mu = threading.RLock()
        r.engine = run.StopEngine("stic", POL)
        r.env = types.SimpleNamespace(host_seen=seen) if seen else types.SimpleNamespace()
        r._os = {}
        r.said = []
        r.log = r.said.append
        r.policy = {}
        r.plan = types.SimpleNamespace(classes=[])
        return r

    def test_a_lost_row_after_a_reboot_is_host_lost_and_rerun_once(self):
        now = time.time()
        r = self.runner(seen=lambda h: {"boot": B, "up_s": 600.0} if h == "chopin" else {"boot": A, "up_s": 9e5})
        lost = {"id": "chopin-1", "cell": "stic:garuda", "host": "chopin", "verdict": "LOST", "class": "harness",
                "start": utc_now(now - 1800), "t": utc_now(now - 60), "note": "lost: no exit file"}
        real = {"id": "cage-1", "cell": "stic:arch", "host": "cage", "verdict": "HARNESS", "class": "harness",
                "start": utc_now(now - 1800), "t": utc_now(now - 60)}
        fail = {"id": "cage-2", "cell": "stic:alpine324", "host": "cage", "verdict": "FAIL", "class": "fail",
                "start": utc_now(now - 1800), "t": utc_now(now - 60)}
        for row in (lost, real, fail):
            r.judge_lost(row)
        self.assertEqual(list(r.state["host_lost"]), ["chopin-1"])
        self.assertTrue(r.res_of(lost)["host_lost"])
        self.assertIsNone(r.res_of(real)["host_lost"])
        self.assertEqual(r.engine.feed(r.res_of(lost)), "ok")
        self.assertEqual(r.engine.harness, 0)
        rr = r.reruns({"garuda": lost, "arch": real, "alpine324": fail})
        self.assertEqual(sorted(rr), ["stic:alpine324", "stic:garuda"])
        self.assertEqual(rr["stic:garuda"]["rerun_why"], "host-lost")
        self.assertEqual((rr["stic:garuda"]["attempt"], rr["stic:garuda"]["rerun_of"]), (2, "chopin-1"))
        self.assertNotIn("rerun_why", rr["stic:alpine324"])

    def test_check_hosts_feed_the_rule_and_the_hold_file(self):
        tmp = tempfile.mkdtemp(prefix="t2hl-")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        r = self.runner()
        c = Clock()
        r.engine.watch.clock = c
        r.info = {"hosts": ["chopin", "cage"], "id": "t2s-x", "mac_dir": tmp}
        r.state = {"t0": c.t}
        r.ctx = types.SimpleNamespace(options={}, facts={})
        r.save = lambda: None
        loads = {"chopin": {"answer": False}, "cage": {"pswpout": 0, "ours": 0, "page": 4096, "boot": A, "up_s": 9e5}}
        r.env = types.SimpleNamespace(host_load=lambda h, run_, lo: loads[h])
        r.check_hosts()
        c.t += 2 * MIN
        r.check_hosts()
        with open(os.path.join(tmp, "hold")) as f:
            self.assertTrue(f.read().startswith("chopin chopin: no answer since"))
        self.assertEqual(list(r.state["host_load"]), ["cage"])


class Holt(unittest.TestCase):
    Q = "t2h-20261119T1434-e089"

    def runner(self, stops):
        tmp = tempfile.mkdtemp(prefix="t2hl-")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        r = holt.Runner.__new__(holt.Runner)
        r.state = holt.State(os.path.join(tmp, "holt.json"))
        r.rs = types.SimpleNamespace(stops=stops)
        r.said, r.notes, r.stops = [], [], []
        r.log = r.said.append
        r.plan = types.SimpleNamespace(cells=[])
        r.qhost = "cage"
        r.sf = None
        return r

    def test_a_lost_row_is_host_lost_and_rerun_once(self):
        s = run.StopEngine("holt", POL)
        c = time.time()
        r = self.runner(s)
        r.remote = types.SimpleNamespace(seen=lambda h: {"boot": B, "up_s": 300.0})
        o = f"ht1:{self.Q}:vanilla3:1"
        row = {"origin": o, "cell": "holt:vanilla3", "verdict": "LOST", "class": "lost", "host": "chopin",
               "start": utc_now(c - 3000), "t": utc_now(c - 400), "note": "ended by a signal (exit 143)"}
        self.assertTrue(r.judge_lost(row, "chopin"))
        self.assertEqual(r.feed(row, "chopin"), "ok")
        self.assertEqual(s.harness, 0)
        r.done = {self.Q: {("chopin", "vanilla3"): {"rc": "143"}, ("cage", "arch"): {"rc": "1"}}}
        r.rows = {o: row, f"ht1:{self.Q}:arch:1": {"verdict": "HARNESS"}}
        self.assertEqual(r.host_lost_cells(self.Q), {"holt:vanilla3": ("chopin", "vanilla3")})
        self.assertEqual(r.failed(self.Q), {})
        self.assertEqual(r.notes, ["not rerun (reruns are for FAILs): holt:arch HARNESS (exit 1)"])

    def test_a_runner_again_on_a_host_that_rebooted_once_it_is_back(self):
        held = {"chopin": "chopin: rebooted at ..."}
        reb = {"chopin": True}
        stops = types.SimpleNamespace(rebooted=lambda h, since: reb.get(h, False), held=lambda: dict(held),
                                      stop_run=False, stop_family=False, stop_starts=False)
        r = self.runner(stops)
        files = {("cage", f"{holt.QUEUE}/{self.Q}/todo"): "dragonfly6\n"}
        r.remote = types.SimpleNamespace(read=lambda h, p: files.get((h, p)), exists=lambda h, p: False)
        r.state["queues"] = {self.Q: {"started": "2026-11-19T14:38:35Z", "runners": {"chopin": {"pid": 1}},
                                      "cells_name": "cells.json", "elsewhere": {"chopin": ["redox"]}}}
        started = []
        r.start_runner = lambda q, h, cn, oe=None: started.append((q, h, cn, oe)) or 77
        self.assertEqual(r.restart_runner(self.Q, "chopin"), "wait")           # back, but still settling
        held.clear()
        self.assertTrue(r.restart_runner(self.Q, "chopin"))
        self.assertEqual(started, [(self.Q, "chopin", "cells.json", ["redox"])])
        self.assertFalse(r.restart_runner(self.Q, "chopin"))                    # once per host and queue
        self.assertFalse(r.restart_runner(self.Q, "cage"))                      # no reboot: as before
        r.state["queues"][self.Q]["restarted"] = {}
        files[("cage", f"{holt.QUEUE}/{self.Q}/todo")] = "# all taken\n"
        self.assertFalse(r.restart_runner(self.Q, "chopin"))                    # nothing left to run


class Gate(unittest.TestCase):
    def test_a_layer_whose_host_rebooted_reruns_once_it_is_back(self):
        io = tgate.FakeIO()
        env, _ = tgate.env_with(io, ["gate:bin:abi"])
        s = run.StopEngine("gate", POL)
        s.watch.settle_s = 0
        env.rs.stops = s
        s.feed_seen("chopin", {"boot": A, "up_s": 86400.0})
        n = {"a": 0}

        def attempt(a):
            n["a"] = a
            if a == 1:
                s.feed_seen("chopin", {"boot": B, "up_s": 5.0})               # the host came back from a reboot
                o = gate.harness(hostjob.JobFailure("no answer from chopin in 20 polls; pid 9 may still run",
                                                    still_running=True))
                o.start = utc_now(time.time() - 600)
                return o
            return gate.Outcome({"x": "PASS"}, [])
        e = gate.run_one(env, "gate:bin:abi", attempt)
        self.assertEqual(n["a"], 2)
        self.assertEqual(e["verdict"], "PASS")                                 # (a HARNESS before it: no FLAKY-FAIL)
        self.assertIsNone(e["host_lost"])
        self.assertTrue(any("host lost" in n_ for n_ in s.host_notes()))

    def test_a_slow_layer_lost_with_its_host_stops_nothing(self):
        def fake_cell(env, cell, commits=None):
            if env.on_submitted is not None:
                env.on_submitted(env.host_for(cell), gate.split_cell(cell)[0])
            lost = cell.endswith("@chopin")
            return {"cell": cell, "verdict": "HARNESS" if lost else "PASS", "rows": [], "host": env.host_for(cell),
                    "host_lost": "chopin: no answer since ..." if lost else None}
        saved = gate.run_cell
        gate.run_cell = fake_cell
        try:
            env, ctx = tgate.env_with(tgate.FakeIO())
            r = gate.run_layers(ctx, ["loop:linux"], "slow", env=env)
        finally:
            gate.run_cell = saved
        self.assertEqual((r["stop"], r["stops"], r["ok"], r["exit"]), (None, [], False, 1))


if __name__ == "__main__":
    unittest.main()
