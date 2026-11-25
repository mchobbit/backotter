"""Unit tests for per-case placement (2026-11-25): a host whose cmp-run.sh --ceiling is under
a source's room (its own-backup cases') but holds its shared-backup case's (bs2lib/room.py
shared_need(): slim-sizes.tsv's d_shared) takes some of its tier 1 cases whose backup is
the preparation's: room.py's shared_need(), scratch(shared=True), shared_share() and
shared_slots(); bs2's held() (SHARED_ONLY), wide_of(), split()'s shared-backup slots and
their preparing, placement(), the plan, deal_source() (the drawn cases on those slots, by
the lead's and the host's halves of repo_shared()), prep_cmd() and draw_cmd(), start_hosts()
and the space preflight.  Nothing here reaches a host:

    python3 -m unittest discover -s testvm/backshot2/tests
"""

import contextlib
import io
import os
import subprocess
import sys
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from test_bs2_hosts import load_bs2  # noqa: E402
from bs2lib import room as RM, space as SP  # noqa: E402

B = load_bs2()
GiB = SP.GiB
BIG, SMALL = (29.0, 23.2), (9.0, 10.1)
HOSTS = {"chopin": 5, "cinqcent": 5, "copland": 2, "cage": 2}
RUN_HOSTS = {h: f"m@{h}.local" for h in HOSTS}
# mint22: slimmed, d 11, d_shared 9 (slim-sizes.tsv); pop24: d 14, d_shared 11; debian12: fits every host.
SOURCES = ["debian12", "mint22", "pop24"]


def ceilings_out(hosts=HOSTS):
    """held()'s ssh answers: the big hosts' and small hosts' ceilings, every source there."""
    return {h: f"@ceiling {(BIG if HOSTS[h] == 5 else SMALL)[0]} {(BIG if HOSTS[h] == 5 else SMALL)[1]}\n" +
            "\n".join(SOURCES) + "\n" for h in hosts}


@contextlib.contextmanager
def farm(env=None):
    """B's hosts and held() as HOSTS with every source there; SHARED_ONLY, SCRATCH and
    CEILINGS cleared after."""
    out = ceilings_out()

    def ssh(h, cmd, check=True, capture=False, input=None):
        return subprocess.CompletedProcess([], 0, out[h], "")
    with mock.patch.object(B, "ssh", ssh), mock.patch.object(B, "RUN_HOSTS", dict(RUN_HOSTS)), \
            mock.patch.object(B, "slots", lambda h: HOSTS[h]), mock.patch.dict(B.SHARED_ONLY, {}, clear=True), \
            mock.patch.dict(B.SCRATCH, {}, clear=True), mock.patch.dict(B.CEILINGS, {}, clear=True), \
            mock.patch.dict(os.environ, env or {}), contextlib.redirect_stderr(io.StringIO()) as err:
        for v in ("BS2_REUSE_SOURCE", "BS2_REUSE_BACKUP", "BS2_REPO_BIND"):
            if v not in (env or {}):
                os.environ.pop(v, None)
        yield err


class Room(unittest.TestCase):
    def test_shared_need_is_the_measured_d_shared_else_the_full_room(self):
        self.assertEqual(RM.need("mint22"), (11, 4))
        self.assertEqual(RM.shared_need("mint22"), (9, 4))
        self.assertEqual(RM.shared_need("pop24"), (11, 4))
        self.assertEqual(RM.shared_need("elementary8"), RM.need("elementary8"), "no d_shared measured: the full room")
        self.assertEqual(RM.shared_need("debian12"), RM.need("debian12"))

    def test_scratch_of_a_shared_backup_case(self):
        self.assertIsNone(RM.scratch("mint22", SMALL, False))
        self.assertEqual(RM.scratch("mint22", SMALL, False, shared=True), RM.RAM)
        self.assertIsNone(RM.scratch("pop24", SMALL, False, shared=True), "d_shared 11 over the small hosts' 9")
        self.assertEqual(RM.scratch("mint22", BIG, True, shared=True), RM.RAM)
        self.assertIsNone(RM.scratch("mint22", (3.0, 6.0), False, shared=True), "choubox's")
        self.assertIsNone(RM.scratch("mint22", (9.0, 3.0), False, shared=True), "memory under its 4")
        self.assertIsNone(RM.scratch("mint22", None, False, shared=True))

    def test_shared_slots_a_low_estimate(self):
        self.assertEqual(RM.shared_slots(20, 0.28), 3)      # 5.6 less 2.0
        self.assertEqual(RM.shared_slots(5, 0.28), 0)
        self.assertEqual(RM.shared_slots(200, 0.25), 43)    # 50 less 6.1
        self.assertEqual(RM.shared_slots(20, 0), 0)
        self.assertEqual(RM.shared_slots(0, 0.5), 0)

    def test_shared_share_from_the_generator(self):
        p = RM.shared_share("mint22", sample=40)
        self.assertGreater(p, 0.05)
        self.assertLess(p, 0.6)
        self.assertEqual(RM.shared_share("no-such-guest", sample=5), 0.0, "no dump: in doubt, none")


class Held(unittest.TestCase):
    def test_a_small_host_holds_the_shared_backup_cases_only(self):
        with farm() as err:
            got = B.held(SOURCES)
            self.assertEqual(got["chopin"], set(SOURCES))
            self.assertEqual(got["copland"], {"debian12"}, "its own cases: the full room only")
            self.assertEqual(B.SHARED_ONLY["copland"], {"mint22"}, "pop24's d_shared 11 is over 9")
            self.assertEqual(B.SHARED_ONLY["chopin"], set())
            self.assertEqual(B.SCRATCH[("copland", "mint22")], RM.RAM)
        self.assertIn("copland holds vms/mint22's shared-backup cases only", err.getvalue())
        self.assertIn("copland has vms/pop24, but", err.getvalue())

    def test_reuse_off_places_by_the_full_room(self):
        for v in ("BS2_REUSE_SOURCE", "BS2_REUSE_BACKUP", "BS2_REPO_BIND"):
            with self.subTest(v=v), farm({v: "0"}):
                B.held(SOURCES)
                self.assertEqual(B.SHARED_ONLY["copland"], set())
                self.assertFalse(B.wide_of(SOURCES, 20))

    def test_held_briefly_says_which(self):
        with farm() as err:
            B.held_briefly(SOURCES)
        self.assertIn("of them it holds the shared-backup cases of mint22", err.getvalue())


class Split(unittest.TestCase):
    def setUp(self):
        self.jobs = [("mint22", 1, i) for i in range(20)] + [("debian12", 1, i) for i in range(40)]
        self.can = {"mint22": ["chopin", "cinqcent"], "debian12": list(HOSTS)}
        self.wide = {("mint22", 1): (3, ["copland", "cage"])}

    def split(self, wide=None, prep=0):
        with mock.patch.dict(B.SHARED_ONLY, {"copland": {"mint22"}, "cage": {"mint22"}}, clear=True), \
                mock.patch.object(B, "prep_secs", lambda s: prep):
            return B.split(self.jobs, self.can, HOSTS, None, wide)

    def test_only_shared_backup_slots_reach_a_shared_only_host(self):
        parts = self.split(self.wide)
        got = [j for js in parts.values() for j in js]
        self.assertEqual(sorted(got), sorted(self.jobs), "every job once")
        for h in ("copland", "cage"):
            for s, t, i in parts[h]:
                if s == "mint22":
                    self.assertLess(i, 3, f"{h} got an own-backup slot of mint22")
        self.assertTrue(any(j[0] == "mint22" for h in ("copland", "cage") for j in parts[h]))

    def test_without_wide_none_there(self):
        parts = self.split(None)
        self.assertFalse([j for h in ("copland", "cage") for j in parts[h] if j[0] == "mint22"])

    def test_preparing_costs_its_first_case(self):
        """A preparation longer than the run is never worth it; one that a host pays, it
        pays once a source (busy: its slot-seconds)."""
        parts = self.split(self.wide, prep=10 ** 6)
        self.assertFalse([j for h in ("copland", "cage") for j in parts[h] if j[0] == "mint22"])
        parts = self.split(self.wide, prep=0)
        self.assertTrue([j for h in ("copland", "cage") for j in parts[h] if j[0] == "mint22"])
        x = B.secs("mint22", 1)
        with mock.patch.dict(B.SHARED_ONLY, {"copland": {"mint22"}}, clear=True), \
                mock.patch.object(B, "prep_secs", lambda s: 7 * x):
            one = B.split([("mint22", 1, 0), ("mint22", 1, 1)], {"mint22": ["chopin"]}, {"chopin": 1, "copland": 2},
                          {"chopin": 9 * x}, {("mint22", 1): (2, ["copland"])})
        self.assertEqual(one["copland"], [("mint22", 1, 0), ("mint22", 1, 1)], "the second case pays no preparing")

    def test_wall_counts_the_preparing(self):
        js = [("mint22", 1, 0)]
        with mock.patch.dict(B.SHARED_ONLY, {"copland": {"mint22"}}, clear=True), \
                mock.patch.object(B, "prep_secs", lambda s: 500):
            self.assertEqual(B.wall(js, 2, 0, "copland"), 500 + B.secs("mint22", 1))
            self.assertEqual(B.wall(js, 2, 0, "chopin"), B.secs("mint22", 1))


class Placement(unittest.TestCase):
    def test_placement_and_the_plan(self):
        with farm():
            can, lead, parts, _ = B.placement(SOURCES, 20)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                B.plan_words(SOURCES, 20, B.held(SOURCES))
        self.assertIn(lead["mint22"], ("chopin", "cinqcent"), "a lead holds the source's own cases")
        small = [j for h in ("copland", "cage") for j in parts[h] if j[0] == "mint22"]
        self.assertTrue(small, parts)
        self.assertTrue(all(t == 1 for _, t, _ in small), "tier 2 by the full room")
        for h in ("copland", "cage"):
            self.assertEqual(h in can["mint22"], any(j[0] == "mint22" for j in parts[h]),
                             "a shared-only host prepares the source only when it has its cases")
        self.assertFalse([j for h in ("copland", "cage") for j in parts[h] if j[0] == "pop24"])
        self.assertIn("shared-backup cases also on", out.getvalue())
        self.assertRegex(out.getvalue(), r"copland: .*mint22 \d+ shared-backup|cage: .*mint22 \d+ shared-backup")

    def test_wide_of_tier_1_only(self):
        with farm():
            B.held(SOURCES)
            w = B.wide_of(SOURCES, {"mint22": 20, "pop24": 20, "debian12": 20})
        self.assertEqual(list(w), [("mint22", 1)])
        k, hs = w[("mint22", 1)]
        self.assertEqual(hs, ["copland"], "one host a source: it prepares it once")
        self.assertEqual(k, RM.shared_slots(20, RM.shared_share("mint22")))

    def test_wide_of_deals_the_sources_in_turns(self):
        with mock.patch.dict(B.SHARED_ONLY, {"copland": {"mint22", "mx25", "neon24"}, "cage": {"mint22", "mx25", "neon24"}},
                             clear=True), mock.patch.object(B, "RUN_HOSTS", dict(RUN_HOSTS)), \
                mock.patch.object(RM, "shared_share", lambda s, *a: 0.3):
            w = B.wide_of(["mint22", "mx25", "neon24"], 20)
        self.assertEqual([w[(s, 1)][1] for s in ("mint22", "mx25", "neon24")], [["copland"], ["cage"], ["copland"]])

    def test_per_case_only_when_it_ends_sooner(self):
        """A farm already even by seconds: the preparing costs more than the shared-backup
        cases save, so the split is the per-source one; the plan says so."""
        with farm():
            with mock.patch.object(B, "prep_secs", lambda s: 10 ** 6):
                can, _, parts, _ = B.placement(SOURCES, 20)
                self.assertEqual(B.PER_CASE["wide"], {})
                self.assertGreaterEqual(B.PER_CASE["per_case"], B.PER_CASE["per_source"])
                self.assertEqual(can["mint22"], ["chopin", "cinqcent"])
                self.assertFalse([j for h in ("copland", "cage") for j in parts[h] if j[0] == "mint22"])
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    B.plan_words(SOURCES, 20, B.held(SOURCES))
            self.assertIn("per-case placement: not used: shared-backup cases on copland", out.getvalue())
            self.assertNotIn("shared-backup cases also on", out.getvalue())
            B.placement(SOURCES, 20)
            self.assertTrue(B.PER_CASE["wide"])
            self.assertLess(B.PER_CASE["per_case"], B.PER_CASE["per_source"])


class Deal(unittest.TestCase):
    """deal_source(): mint22's 6 tier 1 cases, slots 0-1 on copland (shared-backup only), 2-5 on chopin."""

    def setUp(self):
        self.parts = {"chopin": [("mint22", 1, i) for i in range(2, 6)], "copland": [("mint22", 1, 0), ("mint22", 1, 1)],
                      "cinqcent": []}
        self.picks = {("mint22", 1): [f"/c/{i}.case" for i in range(6)]}
        self.lead = {"mint22": "cinqcent"}

    def deal(self, shared, serves=None, parts=None, picks=None):
        with mock.patch.dict(B.SHARED_ONLY, {"copland": {"mint22"}}, clear=True):
            return B.deal_source("mint22", parts or self.parts, picks or self.picks, shared,
                                 {"copland": {("mint22", 1): True}} if serves is None else serves, self.lead)

    def test_shared_cases_fill_the_shared_only_slots_first(self):
        out, short, over = self.deal({"/c/3.case": True, "/c/5.case": True})
        self.assertEqual(out["copland"], [("mint22", 1, "/c/3.case"), ("mint22", 1, "/c/5.case")])
        self.assertEqual([p for _, _, p in out["chopin"]], ["/c/0.case", "/c/1.case", "/c/2.case", "/c/4.case"])
        self.assertEqual((short, over), ({}, 0))

    def test_fewer_shared_than_planned_the_rest_on_the_lead(self):
        out, short, over = self.deal({"/c/4.case": True})
        self.assertEqual(out["copland"], [("mint22", 1, "/c/4.case")])
        self.assertEqual(len(out["chopin"]), 4)
        self.assertEqual(out["cinqcent"], [("mint22", 1, "/c/5.case")], "an own-backup case over the slots: its lead")
        self.assertEqual((short, over), ({"copland": 1}, 1))
        every = [j for js in out.values() for j in js]
        self.assertEqual(sorted(p for _, _, p in every), sorted(self.picks[("mint22", 1)]), "every drawn case once")

    def test_a_host_whose_preparation_doesnt_serve_takes_none(self):
        out, short, over = self.deal({"/c/0.case": True, "/c/1.case": True}, serves={"copland": {("mint22", 1): False}})
        self.assertNotIn("copland", out)
        self.assertEqual(short, {"copland": 2})
        self.assertEqual(over, 2)
        out, short, over = self.deal({"/c/0.case": True}, serves={})
        self.assertNotIn("copland", out, "a host that didn't say (or failed): none")

    def test_more_shared_than_planned_go_anywhere(self):
        out, short, over = self.deal({f"/c/{i}.case": True for i in range(6)})
        self.assertEqual(len(out["copland"]), 2)
        self.assertEqual(len(out["chopin"]), 4)
        self.assertEqual((short, over), ({}, 0))

    def test_a_short_list(self):
        picks = {("mint22", 1): [f"/c/{i}.case" for i in range(4)]}
        out, short, over = self.deal({"/c/0.case": True, "/c/1.case": True}, picks=picks)
        self.assertEqual(len(out["copland"]), 2)
        self.assertEqual(len(out["chopin"]), 2)
        self.assertEqual((short, over), ({"chopin": 2}, 0))

    def test_without_shared_only_hosts_it_is_fill(self):
        parts = {"chopin": [("debian12", 1, i) for i in range(0, 6, 2)], "cinqcent": [("debian12", 1, i) for i in range(1, 6, 2)]}
        picks = {("debian12", 1): [f"/d/{i}.case" for i in range(5)]}
        with mock.patch.dict(B.SHARED_ONLY, {}, clear=True):
            out, short, over = B.deal_source("debian12", parts, picks, {}, {}, {"debian12": "chopin"})
        for h in parts:
            self.assertEqual(out.get(h, []), B.fill(parts[h], picks)[0], h)
        self.assertEqual((short, over), ({"cinqcent": 1}, 0))


class Commands(unittest.TestCase):
    def test_prep_cmd_asks_whether_the_preparation_serves(self):
        c = B.prep_cmd("copland", ["debian12", "mint22"], "r1", (), ["mint22"])
        self.assertIn("source mint22 --port 2949 --drop-tools", c)
        self.assertIn("shared --source mint22 || true; }", c)
        self.assertLess(c.index('echo "gen mint22'), c.index("shared --source mint22"))
        self.assertNotIn(" shared ", B.prep_cmd("copland", ["debian12"], "r1"))

    def test_prepare_one_reads_serves(self):
        out = "gen mint22 g1\n@serves mint22 1 1\n@serves mint22 2 0\n"
        with mock.patch.object(B, "par", lambda cmds: {h: (0, out, "") for h in cmds}):
            gens, err, serves = B.prepare_one("copland", ["mint22"], "r1", (), ["mint22"])
        self.assertEqual((gens, err), ({"mint22": "g1"}, None))
        self.assertEqual(serves, {("mint22", 1): True, ("mint22", 2): False})

    def test_draw_classifies_the_wide_sources_only(self):
        c = B.draw_cmd("chopin", ["debian12", "mint22"], SOURCES, 20, "r1", ["mint22"])
        self.assertIn("shared --list /bkoscratch/backshot2/runs/r1/pick-mint22-t1.txt", c)
        self.assertNotIn("pick-debian12-t1.txt &&", c.split("chown")[1].split("cd ")[0])
        self.assertLess(c.index("chown -R m:"), c.index("shared --list"))
        out = "pick-mint22-t1.txt:1 /r/a.case\npick-mint22-t1.txt:1 /r/b.case\n@shared 1 /r/a.case 1\n@shared 1 /r/b.case 0\n"
        with mock.patch.object(B, "par", lambda cmds: {h: (0, out, "") for h in cmds}):
            picks, err, shared = B.draw("chopin", ["mint22"], SOURCES, 20, "r1", ["mint22"])
        self.assertEqual(shared, {"/r/a.case": True, "/r/b.case": False})
        self.assertEqual(picks, {("mint22", 1): ["/r/a.case", "/r/b.case"]})


class Start(unittest.TestCase):
    """start_hosts(): chopin leads mint22 and debian12; copland holds mint22's shared-backup
    cases only (slots 0 and 1), and debian12's own."""

    def setUp(self):
        self.can = {"debian12": ["chopin", "copland"], "mint22": ["chopin", "copland"]}
        self.lead = {"debian12": "chopin", "mint22": "chopin"}
        self.parts = {"chopin": [("debian12", 1, 0), ("mint22", 1, 2), ("mint22", 1, 3)],
                      "copland": [("debian12", 1, 1), ("mint22", 1, 0), ("mint22", 1, 1)]}
        self.picks = {("debian12", 1): ["/d/0.case", "/d/1.case"], ("mint22", 1): [f"/m/{i}.case" for i in range(4)]}
        self.shared = {"/m/2.case": True, "/m/3.case": True}
        self.gens = {"chopin": {"debian12": "g1", "mint22": "g1"}, "copland": {"debian12": "g1", "mint22": "g1"}}
        self.serves = {("mint22", 1): True, ("mint22", 2): False}
        self.started, self.asked = {}, {}
        self.lock = threading.Lock()

    def prepare_one(self, h, ss, run, tools=(), serve=()):
        self.asked[h] = list(serve)
        return {s: self.gens[h][s] for s in ss}, None, (self.serves if h == "copland" else {})

    def draw(self, h, ss, sources, n, run, classify=()):
        self.assertEqual(list(classify), ["mint22"])
        return {k: v for k, v in self.picks.items() if k[0] in ss}, None, self.shared

    def launch(self, h, js, sweeps, n, run):
        with self.lock:
            self.started[h] = js
        return True

    def run_start(self):
        with mock.patch.dict(B.SHARED_ONLY, {"copland": {"mint22"}}, clear=True), \
                mock.patch.multiple(B, prepare_one=self.prepare_one, draw=self.draw, ship=lambda *a: None,
                                    launch=self.launch), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            why = B.start_hosts(["debian12", "mint22"], 20, "r1", self.can, self.lead, self.parts)
        return why, out.getvalue()

    def test_the_shared_only_host_gets_the_shared_backup_cases(self):
        why, out = self.run_start()
        self.assertEqual(why, {})
        self.assertEqual(self.asked, {"chopin": [], "copland": ["mint22"]})
        self.assertEqual(self.started["copland"], [("debian12", 1, "/d/1.case"), ("mint22", 1, "/m/2.case"),
                                                   ("mint22", 1, "/m/3.case")])
        self.assertEqual(self.started["chopin"], [("debian12", 1, "/d/0.case"), ("mint22", 1, "/m/0.case"),
                                                  ("mint22", 1, "/m/1.case")])
        self.assertIn("mint22: 2 of its 4 tier 1 cases back up the preparation's tree", out)

    def test_a_generation_that_differs_leaves_only_those_slots_empty(self):
        self.gens["copland"]["mint22"] = "g0"
        why, out = self.run_start()
        self.assertEqual(why, {}, "copland still runs debian12's")
        self.assertEqual(self.started["copland"], [("debian12", 1, "/d/1.case")])
        self.assertEqual(len(self.started["chopin"]), 5, "mint22's every case on its lead")
        self.assertIn("none of mint22's shared-backup cases here", out)

    def test_an_own_generation_that_differs_still_stops_the_host(self):
        self.gens["copland"]["debian12"] = "g0"
        why, _ = self.run_start()
        self.assertEqual(set(why), {"copland"})


class Space(unittest.TestCase):
    def test_a_shared_only_host_counts_the_preparation_not_the_tools(self):
        can = {"mint22": ["chopin", "copland"]}
        lead = {"mint22": "chopin"}
        parts = {"chopin": [("mint22", 1, 2)], "copland": [("mint22", 1, 0)]}
        rooms = {h: {"free": 200 * GiB, "sources": {}} for h in parts}
        sp = B.space_of(["mint22"], can, lead, parts, rooms, 30)
        t, p, _ = SP.estimate("mint22")
        self.assertEqual(sp["copland"][0].end, p)
        self.assertEqual(sp["chopin"][0].end, p + t)
        self.assertIsNone(sp["copland"][2])


class HostHalves(unittest.TestCase):
    """bs2-host's repo_shared() is its case_shared() and prep_shared(), the halves bs2's
    placement asks for (bs2-host shared)."""
    H = None

    @classmethod
    def setUpClass(cls):
        from test_bs2_hosts import load_host
        cls.H = load_host()

    def test_repo_shared_is_both_halves(self):
        import tempfile
        from bs2lib import case as C
        H = self.H
        with tempfile.TemporaryDirectory() as d:
            own, sh = f"{d}/own.case", f"{d}/sh.case"
            C.write(own, {"source": "mint22", "edits": [], "load_options": [], "content": {"recipe": "x", "seed": 1}})
            C.write(sh, {"source": "mint22", "edits": [], "load_options": []})
            for prep in (None, "/p/backup/repo"):
                with mock.patch.object(H, "prep_shared", lambda s, t=None, prep=prep: prep), \
                        mock.patch.object(H, "du_bytes", lambda p: 5):
                    self.assertIsNone(H.repo_shared(own, 1))
                    self.assertEqual(H.repo_shared(sh, 1), (prep, 5) if prep else None)
            with mock.patch.object(H, "prep_shared", lambda s, t=None: "/p/backup/repo" if t == 1 else None), \
                    contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
                H.cmd_shared(mock.Mock(cases=[own, sh, f"{d}/missing.case"], tier=1, list=None, source=["mint22"]))
            self.assertEqual(out.getvalue().splitlines(), [
                f"@shared 1 {own} 0", f"@shared 1 {sh} 1", f"@shared 1 {d}/missing.case 0",
                "@serves mint22 1 1", "@serves mint22 2 0"])
            with open(f"{d}/pick.txt", "w") as f:
                f.write(f"1 {sh}\n1 {own}\n")
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
                H.cmd_shared(mock.Mock(cases=[], tier=1, list=[f"{d}/pick.txt"], source=None))
            self.assertEqual(out.getvalue().splitlines(), [f"@shared 1 {sh} 1", f"@shared 1 {own} 0"])


if __name__ == "__main__":
    unittest.main()
