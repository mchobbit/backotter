"""Unit tests for how a BackShot2 run starts its hosts (bs2's start_hosts()): each host's
queue starts as soon as the cases it will run are drawn, not once every lead has drawn
every source's; the leads draw (and sweep) outside cmp-run.sh's reservations, the sources
are still prepared under one; a host's cases are the split of the planned cases, filled
from the pick lists in order, the same split the run made after every draw before; and a
host something fails for is the only one not started.  Nothing here reaches a host (the
ssh steps are stood in for):

    python3 -m unittest discover -s testvm/backshot2/tests
"""

import contextlib
import io
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_bs2_hosts import B, HELD, SLOTS, can_of  # noqa: E402

RUN = "bs2a-test"
N = 200
WAIT = 10   # seconds any wait here may take before it is a failure (none should come near)


def lead_of(can):
    return B.leads(can)


def placed(held=HELD, n=N):
    """(can, lead, parts) as run_words has them: split() of jobs_of(), the plan's."""
    can = can_of(held)
    lead = lead_of(can)
    parts = B.split(B.jobs_of(B.ALPINE, n), can, SLOTS, B.sweep_secs(B.ALPINE, n, lead))
    return can, lead, parts


def lists(n=N, short=None):
    """{(source, tier): [path...]}: every pick list a run of N draws (SHORT: {(s, t): drawn})."""
    out = {}
    for s in B.ALPINE:
        out[(s, 1)] = [f"/r/cases/{s}-t1-{i:03d}.case" for i in range(n)]
        if s != "alpine324":
            out[(s, 2)] = [f"/r/cases/{s}-t2-{i:03d}.case" for i in range(B.tier2_of(s, B.ALPINE))]
    for k, m in (short or {}).items():
        out[k] = out[k][:m]
    return out


class Fill(unittest.TestCase):
    def test_the_same_split_as_after_every_draw(self):
        """fill() of the plan's split is what split() of the drawn cases gave (the old way)."""
        can, lead, parts = placed()
        picks = lists()
        jobs = [(s, t, p) for (s, t), ps in sorted(picks.items(), key=lambda x: (B.ALPINE.index(x[0][0]), x[0][1]))
                for p in ps]
        old = B.split(jobs, can, SLOTS, B.sweep_secs(B.ALPINE, N, lead))
        for h in SLOTS:
            got, short = B.fill(parts[h], picks)
            self.assertEqual(got, old[h], h)
            self.assertEqual(short, 0)
        self.assertEqual(sum(len(B.fill(parts[h], picks)[0]) for h in SLOTS), 830)

    def test_tier_order_per_source(self):
        """Each host's queue: the sources in the run's order, a source's tier 1 before its tier 2,
        each list in its own order."""
        _, _, parts = placed()
        picks = lists()
        for h in SLOTS:
            got, _ = B.fill(parts[h], picks)
            keys = [(B.ALPINE.index(s), t, picks[(s, t)].index(p)) for s, t, p in got]
            self.assertEqual(keys, sorted(keys), h)

    def test_a_short_list(self):
        """A lead that drew fewer than asked: the missing cases are counted, the rest kept."""
        _, _, parts = placed()
        full, picks = lists(), lists(short={("alpine324-lvm", 1): 190})
        total = 0
        for h in SLOTS:
            got, short = B.fill(parts[h], picks)
            whole, _ = B.fill(parts[h], full)
            self.assertEqual(got, [x for x in whole if x[2] in picks.get((x[0], x[1]), [])], h)
            total += short
        self.assertEqual(total, 10)


class Commands(unittest.TestCase):
    def test_drawing_takes_no_reservation(self):
        with mock.patch.object(B.HOSTS_TABLE, "job_slice", lambda h, table=None: "bko.slice" if h == "cinqcent" else None):
            c = B.draw_cmd("cinqcent", ["alpine324-lvm", "alpine324-luks"], B.ALPINE, N, RUN)
            self.assertNotIn("--slice", B.draw_cmd("cage", ["alpine324"], B.ALPINE, N, RUN), "no job_slice: none")
        self.assertNotIn("cmp-run.sh", c)
        self.assertEqual(c.count("systemd-run --quiet --scope --collect"), 4)
        self.assertEqual(c.count(f"WEAR_TAG=bs2:{RUN}:pick"), 4)
        self.assertIn("--slice=bko.slice", c, "hosts.json's job_slice")
        self.assertIn(B.HOST_IOLOG, c)
        order = [c.index(f"enqueue {s} {k} --tier {t} ") for s in ("alpine324-lvm", "alpine324-luks")
                 for t, k in ((1, N), (2, B.tier2_of(s, B.ALPINE)))]
        self.assertEqual(order, sorted(order), "each source's tier 1, then its tier 2, in order")
        self.assertIn("grep -H . pick-alpine324-lvm-t[12].txt;", c)

    def test_light_runs_with_roots_sbin(self):
        """light()'s "$PATH" expands in m's ssh shell (no sbin on Debian): the dry runs'
        sfdisk and mkfs are in root's sbin dirs, first, m's PATH kept after them."""
        import re
        import subprocess
        c = B.light("chopin", "true", "bs2:t")
        word = re.search(r' env (PATH="[^"]*") ', c).group(1)
        m_path = "/usr/local/bin:/usr/bin:/bin:/usr/games"
        got = subprocess.run(["/bin/sh", "-c", f'{word}; printf %s "$PATH"'], env={"PATH": m_path},
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(got.split(":")[:3], ["/usr/local/sbin", "/usr/sbin", "/sbin"])
        self.assertTrue(got.endswith(":" + m_path), got)

    def test_alpine324_draws_tier_1_only(self):
        c = B.draw_cmd("chopin", ["alpine324"], B.ALPINE, N, RUN)
        self.assertIn("enqueue alpine324 200 --tier 1 ", c)
        self.assertNotIn("--tier 2", c)

    def test_preparing_keeps_its_reservation(self):
        """A preparation may boot the source: it still waits for room."""
        c = B.prep_cmd("chopin", ["alpine324", "alpine324-uefi"], RUN)
        self.assertEqual(c.count("./cmp-run.sh --reserve 2 3 sudo -E"), 2)
        self.assertIn('echo "gen alpine324-uefi ', c)

    def test_draw_parses_its_lists(self):
        out = ("pick-alpine324-uefi-t2.txt:2 /r/cases/u2.case\npick-alpine324-uefi-t1.txt:1 /r/cases/u1.case\n"
               "pick-alpine324-t1.txt:1 /r/cases/a1.case\npick-alpine324-t1.txt:1 /r/cases/a2.case\n")
        with mock.patch.object(B, "par", lambda cmds: {h: (0, out, "") for h in cmds}):
            picks, err = B.draw("chopin", ["alpine324", "alpine324-uefi"], B.ALPINE, N, RUN)
        self.assertIsNone(err)
        self.assertEqual(picks, {("alpine324", 1): ["/r/cases/a1.case", "/r/cases/a2.case"],
                                 ("alpine324-uefi", 1): ["/r/cases/u1.case"], ("alpine324-uefi", 2): ["/r/cases/u2.case"]})
        with mock.patch.object(B, "par", lambda cmds: {h: (3, "", "boom") for h in cmds}):
            picks, err = B.draw("chopin", ["alpine324"], B.ALPINE, N, RUN)
        self.assertEqual(picks, {})
        self.assertIn("exit 3", err)

    def test_launch_sweeps_outside_reservations_then_queues(self):
        sent = []
        with mock.patch.object(B, "ssh", lambda h, cmd, check=True, capture=False, input=None:
                               sent.append((h, cmd)) or mock.Mock(returncode=0)):
            self.assertTrue(B.launch("chopin", [("alpine324", 1, "/r/a.case")], ["alpine324", "alpine324-uefi"], N, RUN))
            self.assertFalse(B.launch("cage", [], [], N, RUN))
        self.assertEqual(len(sent), 1)
        cmd = sent[0][1]
        self.assertNotIn("cmp-run.sh", cmd)
        self.assertEqual(cmd.count(f"WEAR_TAG=bs2:{RUN}:tier0"), 2)
        self.assertIn(f"tier0 alpine324-uefi {N * B.SWEEP} --run {RUN}-t0", cmd)
        self.assertLess(cmd.index("tier0 alpine324-uefi"), cmd.index(f"queue {RUN} --slots 5"))


class Start(unittest.TestCase):
    """start_hosts() with its ssh steps stood in for: chopin leads alpine324 and -uefi,
    cinqcent -lvm and -luks, cage holds alpine324 alone (HELD)."""

    def setUp(self):
        self.can, self.lead, self.parts = placed()
        self.picks = lists()
        self.gens = {h: {s: "g1" for s in B.ALPINE if s in HELD[h]} for h in SLOTS}
        self.fail_prep, self.fail_draw, self.fail_ship = set(), set(), set()
        self.gate = {}            # {host: Event}: its draw waits for it
        self.started = {}         # {host: (jobs, sweeps)}
        self.events = []          # ("draw-end" | "start", host), in order
        self.lock = threading.Lock()

    def prepare_one(self, h, ss, run, tools=()):
        self.assertEqual(ss, [s for s in B.ALPINE if s in HELD[h]])
        # (Tools for the sources it leads alone: their draws and sweeps run there.)
        self.assertEqual(set(tools), {s for s in ss if self.lead[s] == h})
        if h in self.fail_prep:
            return {}, "preparing its sources: exit 1: no room"
        return {s: self.gens[h][s] for s in ss}, None

    def draw(self, h, ss, sources, n, run):
        if h in self.gate:
            self.assertTrue(self.gate[h].wait(WAIT), f"{h}'s draw was never let go")
        with self.lock:
            self.events.append(("draw-end", h))
        if h in self.fail_draw:
            return {}, "drawing the cases: exit 1: boom"
        return {k: v for k, v in self.picks.items() if k[0] in ss}, None

    def ship(self, h, js, lead, run):
        if h in self.fail_ship:
            raise RuntimeError("copying 3 cases from chopin failed")

    def launch(self, h, js, sweeps, n, run):
        with self.lock:
            self.started[h] = (js, sweeps)
            self.events.append(("start", h))
        if h == "cage" and "cinqcent" in self.gate:
            self.gate["cinqcent"].set()   # cage started while cinqcent still drew: let it go
        return True

    def run_start(self):
        out = io.StringIO()
        with mock.patch.multiple(B, prepare_one=self.prepare_one, draw=self.draw, ship=self.ship, launch=self.launch), \
                contextlib.redirect_stdout(out):
            why = B.start_hosts(B.ALPINE, N, RUN, self.can, self.lead, self.parts)
        return why, out.getvalue()

    def test_every_host_starts_with_its_share(self):
        why, out = self.run_start()
        self.assertEqual(why, {})
        self.assertEqual(set(self.started), set(SLOTS))
        for h in SLOTS:
            self.assertEqual(self.started[h][0], B.fill(self.parts[h], self.picks)[0], h)
        self.assertEqual(self.started["chopin"][1], ["alpine324", "alpine324-uefi"], "a lead sweeps its own sources")
        self.assertEqual(self.started["cinqcent"][1], ["alpine324-lvm", "alpine324-luks"])
        self.assertEqual(self.started["cage"][1], [])
        self.assertIn("cage: started", out)

    def test_a_host_starts_before_another_lead_has_drawn(self):
        """cage runs alpine324's cases alone: it starts while cinqcent still draws (its draw
        is let go only by cage's start), and chopin, which runs cinqcent's sources too,
        only after."""
        self.gate["cinqcent"] = threading.Event()
        why, _ = self.run_start()
        self.assertEqual(why, {})
        ev = self.events
        self.assertLess(ev.index(("start", "cage")), ev.index(("draw-end", "cinqcent")))
        self.assertLess(ev.index(("draw-end", "cinqcent")), ev.index(("start", "chopin")))
        self.assertLess(ev.index(("draw-end", "cinqcent")), ev.index(("start", "cinqcent")))

    def test_a_lead_that_cannot_prepare(self):
        """cinqcent's preparing fails: it and the hosts with its sources' cases don't start;
        cage, with chopin's alone, does."""
        self.fail_prep.add("cinqcent")
        why, _ = self.run_start()
        self.assertEqual(set(why), {"cinqcent", "chopin", "copland"})
        self.assertIn("no room", why["cinqcent"])
        self.assertIn("its lead cinqcent didn't prepare alpine324-lvm", why["chopin"])
        self.assertEqual(set(self.started), {"cage"})

    def test_a_lead_whose_draw_fails(self):
        self.fail_draw.add("chopin")
        why, _ = self.run_start()
        self.assertIn("boom", why["chopin"])
        self.assertIn("no cases of alpine324: not drawn (see its lead chopin)", why["cage"])
        self.assertIn("no cases of alpine324, alpine324-uefi: not drawn (see its lead chopin)", why["cinqcent"])
        self.assertEqual(set(self.started), set(), "every host has chopin's sources' cases")

    def test_a_generation_that_differs_stops_that_host_alone(self):
        self.gens["copland"]["alpine324-luks"] = "g0"
        why, _ = self.run_start()
        self.assertEqual(set(why), {"copland"})
        self.assertIn("its alpine324-luks is at g0, its lead cinqcent's at g1", why["copland"])
        self.assertEqual(set(self.started), {"chopin", "cinqcent", "cage"})

    def test_a_failed_copy_stops_that_host_alone(self):
        self.fail_ship.add("copland")
        why, _ = self.run_start()
        self.assertEqual(set(why), {"copland"})
        self.assertIn("RuntimeError: copying 3 cases", why["copland"])
        self.assertEqual(set(self.started), {"chopin", "cinqcent", "cage"})


class RunWords(unittest.TestCase):
    def test_a_host_not_started_is_named_and_the_run_exits(self):
        hosts = {h: f"m@{h}.local" for h in SLOTS}
        with mock.patch.object(B, "held", lambda sources, hosts=None: {h: set(HELD[h]) & set(sources) for h in SLOTS}), \
                mock.patch.object(B, "RUN_HOSTS", hosts), mock.patch.object(B, "preflight", lambda hs: None), \
                mock.patch.object(B, "sync", lambda hs=None: None), \
                mock.patch.object(B, "space_check", lambda *a, **k: {}), \
                mock.patch.object(B, "start_hosts", lambda *a: {"copland": "its alpine324-luks is at g0"}), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as e:
                B.run_words(B.ALPINE, N, RUN)
        msg = str(e.exception)
        self.assertIn("not started on 1 of 4 hosts (the others run: bs2 status bs2a-test)", msg)
        self.assertRegex(msg, r"copland \(\d+ cases\): its alpine324-luks is at g0")


if __name__ == "__main__":
    unittest.main()
