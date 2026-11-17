"""The batch scheduler (docs/design/batch.md §4.5, §11.2): t2lib/batchsched.py and the profiles of
t2lib/batchprof.py, over a fixture host table (BKO_HOSTS_JSON: two big, two small, a reserved row,
an arm64 Bigfoot, and a variant with another holt_jobs), fake ceilings, fake profiles, sessions
and a fake live probe.  No t2 plan of the real kind, no ssh.  Also B4's pure parts: hostjob's
unknown_max, the children's ssh options, the supervisor's 255-is-no-answer retries."""

import copy
import json
import os
import random
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
TESTVM = os.path.dirname(T2)
sys.path.insert(0, T2)

from t2lib import batch as B          # noqa: E402
from t2lib import batchfile as bf     # noqa: E402
from t2lib import batchprof as bp     # noqa: E402
from t2lib import batchsched as bs    # noqa: E402
from t2lib import hostjob             # noqa: E402

with open(os.path.join(TESTVM, "hosts.json")) as _f:
    REAL = json.load(_f)

HOSTS = ["chopin", "cinqcent", "copland", "cage"]
BIG, SMALL = (23.0, 23.0), (9.0, 9.2)
CEIL = {"chopin": BIG, "cinqcent": BIG, "copland": SMALL, "cage": SMALL}
H, M = 3600, 60


def table(holt_jobs_small=None):
    t = copy.deepcopy(REAL)
    names = [r["name"] for r in t["hosts"]]
    for n in HOSTS + ["czerny", "bigfoot"]:
        assert n in names, n
    if holt_jobs_small is not None:
        t["class_limits"]["small"]["holt_jobs"] = holt_jobs_small
    return t


class Fixture(unittest.TestCase):
    """The host table through BKO_HOSTS_JSON, as the scheduler's callers load it."""

    TABLE = None

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2sched-")
        self.tbl = os.path.join(self.tmp, "hosts.json")
        with open(self.tbl, "w") as f:
            json.dump(self.TABLE or table(), f)
        self.old = os.environ.get("BKO_HOSTS_JSON")
        os.environ["BKO_HOSTS_JSON"] = self.tbl
        from t2lib import hosts as Hm
        self.t = Hm.load()
        self.cap = bs.capacity(self.t, HOSTS, CEIL)

    def tearDown(self):
        if self.old is None:
            os.environ.pop("BKO_HOSTS_JSON", None)
        else:
            os.environ["BKO_HOSTS_JSON"] = self.old
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def prof(self, text, src=None, cap=None):
        b = bf.parse(text, "t.batch", None)
        return b, bp.build(b, HOSTS, cap or self.cap, src or bp.Sources())

    def sim(self, b, ps, cap=None, **kw):
        return bs.simulate(ps, cap or self.cap, [], 0, (), (), bf.lock_matrix(b.entries), **kw)


def assert_invariants(tc, sim, ps, cap, conflicts, what=""):
    """§11.2 "Limits, as invariants", at every instant the schedule has."""
    by = {p.id: p for p in ps}
    pts = sorted({r.start for r in sim.rows} | {g[1] for r in sim.rows for g in r.grows})
    for t in pts:
        live = [r for r in sim.rows if r.start <= t < r.end]
        own = {}
        on = {}
        for r in live:
            hs = list(r.hosts) + [g[0] for g in r.grows if g[1] <= t]
            for h in hs:
                tc.assertIn(h, cap, (what, t))
                tc.assertNotEqual(h, "bigfoot")
                tc.assertNotEqual(h, "czerny")
                on.setdefault(h, []).append(r.id)
                for pool in r.pools:
                    tc.assertNotIn((h, pool), own, (what, t, r.id))   # one owner a pool a host
                    own[(h, pool)] = r.id
            if by[r.id].serial:
                tc.assertEqual(len(live), 1, (what, t))
        for h, ids in on.items():
            if len(ids) < 2:
                continue
            c = cap[h].ceiling
            tc.assertIsNotNone(c, (what, t, h, "an unknown ceiling holds one entry"))
            d = sum(bs.share_on(by[i], h, cap)[0] for i in ids)
            m = sum(bs.share_on(by[i], h, cap)[1] for i in ids)
            tc.assertLessEqual(d, c[0] + 1e-6, (what, t, h, ids))
            tc.assertLessEqual(m, c[1] + 1e-6, (what, t, h, ids))
        for i, a in enumerate(live):
            for c2 in live[i + 1:]:
                tc.assertNotIn((a.id, c2.id), conflicts, (what, t))


class Capacity(Fixture):
    def test_pools_ceilings_shared(self):
        c = self.cap
        self.assertEqual(c["chopin"].pools, {"stic": 3, "holt": 3, "bs2": 5, "gate": 1})
        self.assertEqual(c["cage"].pools, {"stic": 2, "holt": 1, "bs2": 2, "gate": 1})
        self.assertEqual(c["copland"].ceiling, SMALL)
        self.assertTrue(c["chopin"].shared)
        self.assertFalse(any(c[h].shared for h in HOSTS[1:]))
        self.assertIsNone(bs.capacity(self.t, HOSTS)["chopin"].ceiling)
        f = {"chopin": {"preflight": {"ram": {"ceiling": [22.5, 21]}}}}
        self.assertEqual(bs.capacity(self.t, ["chopin"], f)["chopin"].ceiling, (22.5, 21.0))

    def test_shares(self):
        _b, ps = self.prof("t2 SticTest2 est=1h\nht1 R a b c d est=1h\nbs2 B 1 est=1h\nt2 SweepTest2 est=1h\n")
        st, ht, b2, sw = ps
        self.assertEqual(bs.share_on(st, "chopin", self.cap), (9.0, 18.0))      # 3 x (3, 6)
        self.assertEqual(bs.share_on(st, "cage", self.cap), (6.0, 9.2))         # 2 x (3, 6), capped
        self.assertEqual(bs.share_on(ht, "chopin", self.cap), (12.0, 12.0))     # 3 of its 4 jobs x (4, 4)
        self.assertEqual(bs.share_on(ht, "cage", self.cap), (4.0, 4.0))         # holt_jobs 1
        self.assertEqual(bs.share_on(b2, "chopin", self.cap), (10.0, 15.0))     # 5 x (2, 3)
        self.assertEqual(bs.share_on(sw, "chopin", self.cap), BIG)              # capped at the ceiling
        _b, (img, gst) = self.prof("t2 ImgTest2 est=1h\nt2 GuestTest2 est=1h\n")
        self.assertEqual(bs.share_on(img, "chopin", self.cap), (3.0, 15.0))     # 3 x img's (1, 5)
        self.assertEqual(bs.share_on(gst, "copland", self.cap), (2.0, 8.0))
        # ram-sizes.tsv's D for an ht1 guest
        _b, (h2,) = self.prof("ht1 R big1 small1 est=1h\n", bp.Sources(ram_sizes={"big1": 17, "small1": 3}))
        self.assertEqual(bs.share_on(h2, "chopin", self.cap), (20.0, 8.0))
        self.assertEqual(bs.single_on(h2, "chopin", self.cap), (17.0, 4.0))


class Sets(Fixture):
    def test_candidate_sets(self):
        _b, ps = self.prof("t2 HoltTest2 est=1h\nht1 R x est=1h\nt2 GateTest2 est=1h\nt2 BkoTest2 rel6 est=1h\n"
                           "t2 SticTest2 hosts=copland,cage est=1h\nserial ht1 S y est=1h\ncmd c -- true\n")
        hol, h1, gate, bko, claim, ser, cmd = ps
        seven = bs.candidate_sets(hol, self.cap)
        # (never the small ones alone: a HoltTest2 builds bko-holt on a big build host)
        self.assertEqual(seven, [tuple(HOSTS), ("chopin", "cinqcent"),
                                 ("cinqcent", "copland", "cage"), ("chopin", "copland", "cage"),
                                 ("chopin", "cinqcent", "cage"), ("chopin", "cinqcent", "copland")])
        sti = bs.Profile("s", "t2", 1, pools={"stic"}, split="seven", walls={None: H})
        self.assertIn(("copland", "cage"), bs.candidate_sets(sti, self.cap))
        self.assertEqual({h: c.builder for h, c in self.cap.items()},
                         {"chopin": True, "cinqcent": True, "copland": False, "cage": False})
        nb = bs.Profile("n", "t2", 1, pools={"holt"}, split="seven", walls={None: H}, needs_builder=True)
        self.assertEqual(bs.Profile.from_json(json.loads(json.dumps(nb.to_json()))).needs_builder, True)
        anys = bs.candidate_sets(h1, self.cap)
        self.assertEqual(len(anys), 15)
        self.assertEqual(anys[0], tuple(HOSTS))
        self.assertEqual(bs.candidate_sets(gate, self.cap), [tuple(HOSTS)])     # never split
        self.assertEqual(bs.candidate_sets(bko, self.cap), [tuple(HOSTS)])
        self.assertEqual(bs.candidate_sets(claim, self.cap), [("copland", "cage")])
        self.assertEqual(bs.candidate_sets(ser, self.cap), [tuple(HOSTS)])
        self.assertEqual(bs.candidate_sets(cmd, self.cap), [()])

    def test_feasibility(self):
        # a job pinned to chopin: every set without chopin is out; a set marked not runnable too
        p = bs.Profile("x", "ht1", 1, pools={"holt"}, split="any",
                       jobs=[bs.Job("holt:a", 600, 4, 4, "holt", ["chopin"]), bs.Job("holt:b", 600, 4, 4, "holt")])
        sets = bs.candidate_sets(p, self.cap)
        self.assertTrue(sets and all("chopin" in s for s in sets))
        q = bs.Profile("y", "t2", 1, pools={"holt"}, split="seven",
                       walls={None: 3600, frozenset(["copland", "cage"]): None})
        self.assertNotIn(("copland", "cage"), bs.candidate_sets(q, self.cap))
        # a host none of the jobs can use holds no pool for nothing
        r = bs.Profile("z", "ht1", 1, pools={"holt"}, split="any",
                       jobs=[bs.Job("holt:a", 600, 4, 4, "holt", ["chopin", "cinqcent"])])
        self.assertEqual(bs.candidate_sets(r, self.cap), [("chopin", "cinqcent"), ("chopin",), ("cinqcent",)])

    def test_walls(self):
        _b, (hol,) = self.prof("t2 HoltTest2 est=2h\n")
        self.assertEqual(bs.wall_on(hol, tuple(HOSTS), self.cap), 2 * H)
        self.assertEqual(bs.wall_on(hol, ("chopin", "cinqcent"), self.cap), 2 * H * 8 / 6)   # 8 slots over 6
        jobs = [bs.Job(f"holt:g{i}", 1800, 4, 4, "holt") for i in range(8)]
        p = bs.Profile("p", "ht1", 1, pools={"holt"}, split="any", jobs=jobs, walls={})
        self.assertEqual(bs.wall_on(p, ("cage",), self.cap), 8 * 1800)
        self.assertEqual(bs.wall_on(p, ("chopin", "cinqcent"), self.cap), 2 * 1800)
        # hosts= takes its est= as given, unscaled
        _b, (c,) = self.prof("ht1 R x hosts=cage est=1h\n")
        self.assertEqual(bs.wall_on(c, ("cage",), self.cap), H)


class Order(Fixture):
    def test_lpt_longest_first_position_breaking_ties(self):
        b, ps = self.prof("cmd a hosts=cage est=1h -- true\ncmd b hosts=copland est=3h -- true\n"
                          "cmd c hosts=chopin est=3h -- true\nt2 SticTest2 est=30m\n")
        acts = bs.decide(ps, self.cap, [], (), None, 0, (), bf.lock_matrix(b.entries))
        self.assertEqual([a.id for a in acts], ["002-cmd-b", "003-cmd-c", "001-cmd-a", "004-t2-stictest2"])

    def test_lpt_makespan_and_serial(self):
        b, ps = self.prof("t2 HoltTest2 est=1h\nt2 SticTest2 est=3h\nbs2 BackShot2Alpine 200 est=2h\n"
                          "ht1 R x est=5h\n")
        sim = self.sim(b, ps)
        ser = self.sim(b, ps, serial_all=True)
        self.assertEqual(ser.finish, 11 * H)
        self.assertEqual([r.id for r in ser.rows], [e.id for e in b.entries])    # file order, one at a time
        self.assertLess(sim.finish, ser.finish)
        lb = max(max(bs.best_set(p, self.cap)[1] for p in ps), sum(p.wall(None) for p in ps) / len(HOSTS))
        self.assertLessEqual(sim.finish, 4 / 3 * lb + 1e-6 + 3 * H)   # (the holt pool is one resource here)
        st = {r.id: r for r in sim.rows}
        self.assertEqual(st["004-ht1-r-x"].start, 0)
        self.assertEqual(st["004-ht1-r-x"].hosts, tuple(HOSTS))
        # SticTest2 fits beside the one-guest ht1 on the big hosts only, and ends with it: no idle host
        self.assertEqual(st["002-t2-stictest2"].start, 0)
        self.assertEqual(st["002-t2-stictest2"].hosts, ("chopin", "cinqcent"))
        self.assertEqual(st["002-t2-stictest2"].end, 5 * H)
        # HoltTest2 wants the holt pool the ht1 holds everywhere
        self.assertEqual(st["001-t2-holttest2"].start, st["004-ht1-r-x"].end + 2 * H)
        assert_invariants(self, sim, ps, self.cap, bf.lock_matrix(b.entries))

    def test_serial_barrier_and_after(self):
        b, ps = self.prof("cmd a est=1h -- true\nserial cmd s est=10m -- true\ncmd c est=2h -- true\n"
                          "cmd d est=30m after=001-cmd-a -- true\n")
        sim = self.sim(b, ps)
        r = {x.id: x for x in sim.rows}
        self.assertEqual(r["002-cmd-s"].start, H)
        self.assertEqual(r["003-cmd-c"].start, H + 10 * M)
        self.assertEqual(r["004-cmd-d"].start, H + 10 * M)
        acts = bs.decide(ps, self.cap, [], (), None, 0)
        w = {a.id: a for a in acts if isinstance(a, bs.Wait)}
        self.assertEqual(w["002-cmd-s"].why, "serial: 001-cmd-a first")
        self.assertEqual(w["004-cmd-d"].why, "after 001-cmd-a")
        self.assertEqual(w["004-cmd-d"].kind, "after")
        self.assertTrue(w["003-cmd-c"].why.startswith("serial: 002-cmd-s"))

    def test_hosts_claims(self):
        b, ps = self.prof("cmd a hosts=copland est=1h -- true\ncmd b hosts=cage est=1h -- true\n"
                          "cmd c hosts=copland est=1h -- true\ncmd d est=1h -- true\n")
        r = {x.id: x for x in self.sim(b, ps).rows}
        self.assertEqual((r["001-cmd-a"].start, r["002-cmd-b"].start, r["003-cmd-c"].start, r["004-cmd-d"].start),
                         (0, 0, H, 0))
        self.assertEqual(r["004-cmd-d"].hosts, ())
        self.assertEqual(r["001-cmd-a"].pools, tuple(sorted(bs.POOLS)))

    def test_locks_and_builds(self):
        b, ps = self.prof("t2 ImgTest2All est=2h\nt2 HoltTest2 est=1h\nt2 SticTest2 est=1h\n")
        acts = bs.decide(ps, self.cap, [], (), None, 0, (), bf.lock_matrix(b.entries))
        st = [a.id for a in acts if isinstance(a, bs.Start)]
        self.assertIn("001-t2-imgtest2all", st)
        w = {a.id: a for a in acts if isinstance(a, bs.Wait)}
        self.assertEqual(w["002-t2-holttest2"].why, "lock holt: 001-t2-imgtest2all")
        self.assertEqual(w["002-t2-holttest2"].kind, "lock")
        self.assertFalse(w["002-t2-holttest2"].session)
        # two t2 entries whose plans build the same round conflict
        b2, ps2 = self.prof("t2 SticTest2 rel6 est=1h\nt2 GateTest2 rel6 est=1h\n")
        m = bf.lock_matrix(b2.entries, {"001-t2-stictest2-rel6": "rel6", "002-t2-gatetest2-rel6": "rel6"})
        acts = bs.decide(ps2, self.cap, [], (), None, 0, (), m)
        self.assertEqual([a.why for a in acts if isinstance(a, bs.Wait)], ["both build round rel6: 001-t2-stictest2-rel6"])


class NoIdle(Fixture):
    def big_running(self, end):
        return [bs.Running("001-t2-holttest2", ["chopin", "cinqcent", "copland"], set(bs.POOLS), 0, end)]

    def test_waits_when_the_small_host_would_take_ten_hours(self):
        # a SticTest2 of 2 h on all hosts is 10 h on cage alone (2 of 10 slots): it waits for the farm
        p = bs.Profile("002-t2-stictest2", "t2", 2, pools={"stic"}, split="seven", walls={None: 2 * H})
        run = self.big_running(3 * H)
        q = bs.Profile("001-t2-holttest2", "t2", 1, pools=set(bs.POOLS), serial=False, walls={None: 3 * H})
        # cage alone isn't among the seven sets; let the scheduler see it as an any-subset entry
        p.split = "any"
        acts = bs.decide([q, p], self.cap, run, (), None, 0, finish=3 * H)
        a = next(x for x in acts if x.id == p.id)
        self.assertIsInstance(a, bs.Wait)
        self.assertEqual(a.kind, "later")
        self.assertIn("cage", a.why)
        sim = bs.simulate([q, p], self.cap, run, 0)
        self.assertEqual(sim.row(p.id).start, 3 * H)
        self.assertEqual(sim.row(p.id).hosts, tuple(HOSTS))

    def test_starts_when_it_ends_within_the_finish(self):
        p = bs.Profile("002-stic1-x", "stic1", 2, pools={"stic"}, split="any", walls={None: 0.4 * H})
        run = self.big_running(3 * H)
        q = bs.Profile("001-t2-holttest2", "t2", 1, pools=set(bs.POOLS), walls={None: 3 * H})
        a = next(x for x in bs.decide([q, p], self.cap, run, (), None, 0, finish=3 * H) if x.id == p.id)
        self.assertIsInstance(a, bs.Start)
        self.assertEqual(a.hosts, ("cage",))                      # 2 h on cage: sooner than waiting (3.4 h)
        self.assertEqual(a.why, "LPT")
        # 5 h on cage is later than waiting for the farm (4 h), but within a finish of 8 h: a free
        # host gets the work when that doesn't push the finish later
        p.walls = {None: H}
        a = next(x for x in bs.decide([q, p], self.cap, run, (), None, 0, finish=8 * H) if x.id == p.id)
        self.assertIsInstance(a, bs.Start)
        self.assertEqual(a.hosts, ("cage",))
        self.assertIn("within the finish", a.why)
        a = next(x for x in bs.decide([q, p], self.cap, run, (), None, 0, finish=3 * H) if x.id == p.id)
        self.assertIsInstance(a, bs.Wait)


class Chopin(Fixture):
    def test_ties_go_against_it(self):
        two = {"chopin": BIG, "cinqcent": BIG}
        cap = bs.capacity(self.t, ["chopin", "cinqcent"], two)
        p = bs.Profile("x", "stic1", 1, pools={"stic"}, split="any",
                       walls={frozenset(["chopin"]): H, frozenset(["cinqcent"]): H,
                              frozenset(["chopin", "cinqcent"]): H})
        a = bs.decide([p], cap, [], (), None, 0)[0]
        self.assertEqual(a.hosts, ("cinqcent",))
        p.walls[frozenset(["chopin"])] = H - 1
        self.assertEqual(bs.decide([p], cap, [], (), None, 0)[0].hosts, ("chopin",))   # an earlier end wins

    def test_bigger_set_then_table_order(self):
        p = bs.Profile("x", "stic1", 1, pools={"stic"}, split="any",
                       walls={frozenset(s): H for s in bs._subsets(tuple(HOSTS))})
        self.assertEqual(bs.decide([p], self.cap, [], (), None, 0)[0].hosts, ("cinqcent", "copland", "cage"))


class Live(Fixture):
    def setUp(self):
        super().setUp()
        self.ht1 = bs.Profile("001-ht1-r", "ht1", 1, pools={"holt"}, split="none", walls={None: 5 * H},
                              share={h: (8.0, 9.0) for h in ("copland", "cage")})
        self.st = bs.Profile("002-stic1-r", "stic1", 2, pools={"stic"}, split="none", hosts=("copland",),
                             walls={None: H}, jobs=[bs.Job("stic:a", 600, 3, 4, "stic")])
        self.run = [bs.Running("001-ht1-r", ["copland"], {"holt"}, 0, 5 * H)]
        self.ok = {"ok": True, "waiting": 0, "line_room": (5.0, 6.0), "free": (5.0, 6.0), "load": 1.0, "nproc": 8}

    def test_live_join(self):
        acts = bs.decide([self.ht1, self.st], self.cap, self.run, (), None, 0)
        self.assertIsInstance(acts[0], bs.Wait)
        self.assertIn("shares", acts[0].why)
        acts = bs.decide([self.ht1, self.st], self.cap, self.run, (), {"copland": self.ok}, 0)
        self.assertIsInstance(acts[0], bs.Start)
        self.assertIn("live join on copland", acts[0].why)
        for bad in ({"waiting": 1}, {"line_room": (2.0, 6.0), "free": (2.0, 6.0)}, {"load": 9.0}, {"ok": False}):
            pr = dict(self.ok, **bad)
            a = bs.decide([self.ht1, self.st], self.cap, self.run, (), {"copland": pr}, 0)[0]
            self.assertIsInstance(a, bs.Wait, bad)

    def test_chopin_penalty(self):
        st = bs.Profile("002-stic1-r", "stic1", 2, pools={"stic"}, hosts=("chopin",), walls={None: H},
                        jobs=[bs.Job("stic:a", 600, 3, 4, "stic")])
        ht = bs.Profile("001-ht1-r", "ht1", 1, pools={"holt"}, walls={None: 5 * H}, share={"chopin": (20.0, 20.0)})
        run = [bs.Running("001-ht1-r", ["chopin"], {"holt"}, 0, 5 * H)]
        a = bs.decide([ht, st], self.cap, run, (), {"chopin": self.ok}, 0)[0]
        self.assertIsInstance(a, bs.Start)                      # (1.0 + 4.0) / 8 cores is under 1.0
        hot = dict(self.ok, load=4.5)                           # (4.5 + 4.0) / 8 isn't
        self.assertIsInstance(bs.decide([ht, st], self.cap, run, (), {"chopin": hot}, 0)[0], bs.Wait)
        ok, why = bs.live_ok(st, "chopin", self.cap, hot)
        self.assertFalse(ok)
        self.assertIn("penalty", why)
        ok, _ = bs.live_ok(st, "cinqcent", self.cap, hot)       # the same figures on another host
        self.assertTrue(ok)

    def test_never_in_the_plan(self):
        sim = bs.simulate([self.ht1, self.st], self.cap, self.run, 0)
        self.assertEqual(sim.row("002-stic1-r").start, 5 * H)


class Sessions(Fixture):
    def test_session_pools_and_locks(self):
        b, ps = self.prof("t2 HoltTest2 est=1h\nht1 R x est=1h\nt2 SticTest2 est=1h\nbs2 B 1 est=1h\n")
        sess = [bs.Session("t2h-20261115T0000-aaaa", {"holt"}, {"holt"}, ["cinqcent"])]
        acts = bs.decide(ps, self.cap, [], sess, None, 0, (), bf.lock_matrix(b.entries))
        w = {a.id: a for a in acts if isinstance(a, bs.Wait)}
        st = {a.id: a for a in acts if isinstance(a, bs.Start)}
        self.assertTrue(w["001-t2-holttest2"].session)            # its lock is the session's: lock-wait counts
        self.assertIn("lock holt: session", w["001-t2-holttest2"].why)
        self.assertIn("002-ht1-r-x", st)                          # the ht1 takes no lock: off cinqcent only
        self.assertNotIn("cinqcent", st["002-ht1-r-x"].hosts)
        self.assertIn("003-t2-stictest2", st)
        self.assertIn("cinqcent", st["003-t2-stictest2"].hosts)  # the session holds no stic pool there

    def test_session_on_every_host_while_planning(self):
        b, ps = self.prof("ht1 R x est=1h\n")
        sess = [bs.Session("t2h-20261115T0000-bbbb", {"holt"}, {"holt"}, None)]
        a = bs.decide(ps, self.cap, [], sess, None, 0)[0]
        self.assertIsInstance(a, bs.Wait)
        self.assertTrue(a.session)


class Growth(Fixture):
    def ht1(self):
        jobs = [bs.Job(f"holt:g{i}", 1800, 4, 4, "holt") for i in range(12)]
        return bs.Profile("001-ht1-r", "ht1", 1, pools={"holt"}, split="any", growable=True, jobs=jobs, walls={})

    def test_grows_onto_a_freed_pool(self):
        p = self.ht1()
        run = [bs.Running("001-ht1-r", ["chopin", "cinqcent"], {"holt"}, 0, 2 * H)]
        acts = bs.decide([p], self.cap, run, (), None, 0)
        g = [a for a in acts if isinstance(a, bs.Grow)]
        self.assertEqual([a.host for a in g], ["copland", "cage"])
        self.assertAlmostEqual(g[0].end, 2 * H * 6 / 7)
        self.assertAlmostEqual(g[-1].end, 2 * H * 6 / 8)
        # near its end (less than GROW_MIN_S left) or with no lines left over: no growth
        run = [bs.Running("001-ht1-r", ["chopin", "cinqcent"], {"holt"}, 0, 600)]
        self.assertFalse([a for a in bs.decide([p], self.cap, run, (), None, 0) if isinstance(a, bs.Grow)])
        p.jobs = p.jobs[:6]
        run = [bs.Running("001-ht1-r", ["chopin", "cinqcent"], {"holt"}, 0, 2 * H)]
        self.assertFalse([a for a in bs.decide([p], self.cap, run, (), None, 0) if isinstance(a, bs.Grow)])

    def test_ready_entries_first(self):
        p = self.ht1()
        q = bs.Profile("002-t2-holttest2", "t2", 2, pools={"holt"}, hosts=("copland",), walls={None: H})
        run = [bs.Running("001-ht1-r", ["chopin", "cinqcent"], {"holt"}, 0, 2 * H)]
        acts = bs.decide([p, q], self.cap, run, (), None, 0)
        self.assertEqual([(type(a).__name__, getattr(a, "host", None) or getattr(a, "hosts", None)) for a in acts],
                         [("Start", ("copland",)), ("Grow", "cage")])
        # a ready entry that waits for a host's holt pool keeps the ht1 off it
        q.hosts = ("cage",)
        busy = [bs.Running("009-cmd-x", ["cage"], set(bs.POOLS), 0, 3 * H)]
        acts = bs.decide([p, q, bs.Profile("009-cmd-x", "cmd", 9, pools=set(bs.POOLS), hosts=("cage",))],
                         self.cap, run + busy, (), None, 0)
        self.assertNotIn("cage", [a.host for a in acts if isinstance(a, bs.Grow)])

    def test_simulated_repack(self):
        p = self.ht1()
        p.jobs = [bs.Job(f"holt:g{i}", 1800, 4, 4, "holt") for i in range(24)]
        busy = bs.Profile("002-cmd-b", "cmd", 2, pools=set(bs.POOLS), hosts=("copland", "cage"), walls={None: H})
        run = [bs.Running("002-cmd-b", ["copland", "cage"], set(bs.POOLS), 0, H)]
        sim = bs.simulate([p, busy], self.cap, run, 0)
        r = sim.row("001-ht1-r")
        self.assertEqual(r.hosts, ("chopin", "cinqcent"))                 # 2 h there, sooner than waiting
        self.assertEqual([g[0] for g in r.grows], ["copland", "cage"])    # and both small hosts at +1:00
        self.assertEqual({g[1] for g in r.grows}, {H})
        self.assertAlmostEqual(r.end, H + H * 6 / 8)                      # the hour left over 8 slots, not 6
        assert_invariants(self, sim, [p, busy], self.cap, {})


class Resume(Fixture):
    def test_claims_before_new_starts(self):
        b, ps = self.prof("t2 HoltTest2 est=1h\nht1 R x est=1h\n")
        run = [bs.Running("001-t2-holttest2", ["chopin", "cage"], {"holt"}, 0, 1800)]
        acts = bs.decide(ps, self.cap, run, (), None, 100)
        st = [a for a in acts if isinstance(a, bs.Start)]
        for a in st:                       # the resumed run keeps its hosts; new work fills around it
            self.assertFalse(set(a.hosts) & {"chopin", "cage"})
        sim = bs.simulate(ps, self.cap, run, 3000)
        self.assertEqual(sim.row("001-t2-holttest2").end, 3060)     # max(predicted end, now + 60 s)
        self.assertEqual(sim.row("001-t2-holttest2").hosts, ("chopin", "cage"))


class Random(Fixture):
    KINDS = ["t2 HoltTest2", "t2 SticTest2", "t2 GateTest2", "t2 ImgTest2", "t2 GuestTest2", "ht1 R{i} x y z",
             "stic1 r{i}", "bs2 B {i}", "cmd c{i} hosts={h} -- true", "cmd m{i} -- true", "t2 SweepTest2"]

    def run_trials(self, cap, n, seed):
        rnd = random.Random(seed)
        for trial in range(n):
            lines = []
            for i in range(rnd.randint(2, 9)):
                k = rnd.choice(self.KINDS).format(i=i, h=rnd.choice(HOSTS))
                ser = "serial " if rnd.random() < 0.1 else ""
                est = f"est={rnd.randint(1, 300)}m"
                lines.append(ser + (k.replace(" -- ", f" id=x{i} {est} -- ") if k.startswith("cmd") else f"{k} id=x{i} {est}"))
            b, ps = self.prof("\n".join(lines) + "\n", cap=cap)
            m = bf.lock_matrix(b.entries)
            sim = bs.simulate(ps, cap, [], 0, (), (), m)
            again = bs.simulate(ps, cap, [], 0, (), (), m)
            self.assertEqual(json.dumps(sim.to_json(), sort_keys=True), json.dumps(again.to_json(), sort_keys=True))
            self.assertEqual(len(sim.rows), len(ps), (lines, sim.stuck))
            assert_invariants(self, sim, ps, cap, m, lines)
            ser = bs.simulate(ps, cap, [], 0, (), (), m, True)
            self.assertLessEqual(sim.finish, ser.finish + 1e-6, lines)

    def test_invariants(self):
        self.run_trials(self.cap, 60, 7)

    def test_invariants_unknown_ceiling(self):
        cap = bs.capacity(self.t, HOSTS, {"chopin": BIG})
        self.run_trials(cap, 30, 11)


class OtherHoltJobs(Fixture):
    TABLE = table(holt_jobs_small=2)

    def test_variant_table(self):
        self.assertEqual(self.cap["cage"].pools["holt"], 2)
        _b, (ht,) = self.prof("ht1 R a b c est=1h\n")
        self.assertEqual(bs.share_on(ht, "cage", self.cap), (8.0, 8.0))
        Random.run_trials(self, self.cap, 20, 3)

    KINDS = Random.KINDS


class Profiles(Fixture):
    class FakePlanner:
        """Plans per set: wall 6 h on all, more on fewer; on the small hosts a cell is not runnable."""

        def __init__(self):
            self.calls = []

        def plan(self, entry, args, hosts):
            self.calls.append((entry.id, tuple(hosts)))
            n = len(hosts)
            cells = [{"cell": "holt:big1", "status": "due", "est": {"wall_s": 3000}, "host": "any"},
                     {"cell": "holt:a", "status": "due", "est": {"wall_s": 1200}, "host": "any"}]
            if set(hosts) <= {"copland", "cage"}:
                cells[0]["status"] = "not-runnable"
            return {"totals": {"wall_s": 6 * H * 4 // n}, "cells": cells,
                    "set": {"pending": True, "round": "rel6"} if entry.family == "stic" else None}

    def test_t2_planned_per_set(self):
        fp = self.FakePlanner()
        b, (hol, gate) = self.prof("t2 HoltTest2\nt2 GateTest2\n", bp.Sources(planner=fp))
        self.assertEqual(len([c for c in fp.calls if c[0] == hol.id]), 6)   # (not the small set: no big build host)
        self.assertEqual([c[1] for c in fp.calls if c[0] == gate.id], [tuple(HOSTS)])   # never split
        self.assertEqual(hol.wall_src, "plan")
        self.assertEqual(hol.walls[frozenset(HOSTS)], 6 * H)
        self.assertIsNone(hol.walls[frozenset(["copland", "cage"])])                  # infeasible
        self.assertNotIn(("copland", "cage"), bs.candidate_sets(hol, self.cap))
        self.assertEqual([j.name for j in hol.jobs], ["holt:big1", "holt:a"])
        js = json.dumps(hol.to_json(), sort_keys=True)
        self.assertEqual(json.dumps(bs.Profile.from_json(json.loads(js)).to_json(), sort_keys=True), js)

    def test_builds_conflict(self):
        fp = self.FakePlanner()
        b, ps = self.prof("t2 SticTest2 rel6\nt2 SticTest2 rel6 id=again\n", bp.Sources(planner=fp))
        self.assertEqual([p.builds for p in ps], ["rel6", "rel6"])

    def test_est_wins(self):
        fp = self.FakePlanner()
        b, (hol,) = self.prof("t2 HoltTest2 est=1h\n", bp.Sources(planner=fp))
        self.assertEqual(hol.walls, {None: H})
        self.assertTrue(all(j.wall == 0 for j in hol.jobs))

    def test_ht1_and_stic1_from_estimates(self):
        class Est:
            def cell(self, c):
                return {"wall_s": 1800 if c.startswith("holt:") else 600, "src": "tsv"}
        b, (h1, s1) = self.prof("ht1 R a b c d\nstic1 rel6 x y\n", bp.Sources(est=Est()))
        self.assertTrue(h1.growable)
        self.assertEqual(h1.wall_src, "tsv")
        self.assertEqual(bs.wall_on(h1, tuple(HOSTS), self.cap), 1800)
        self.assertEqual(bs.wall_on(h1, ("cage",), self.cap), 4 * 1800)
        self.assertEqual(bs.wall_on(s1, ("cage",), self.cap), 600)

    def test_stic1_defaulted_guests_drop_other_arch(self):
        # A6: drive.py's all_guests() has the -arm64 guests, which a round naming no guests
        # leaves out on x86 hosts (drive.py main()); the estimate must count the same jobs.
        class Est:
            def cell(self, c):
                return {"wall_s": 600, "src": "tsv"}

        class Drive:
            calls = []

            @staticmethod
            def all_guests():
                return ["debian13", "alpine324", "debian13-arm64", "alpine324-arm64"]

            @classmethod
            def no_arch_host(cls, guests, hosts):
                cls.calls.append(tuple(hosts))
                return {g: "arm64" for g in guests if g.endswith("-arm64")}

        b, (s1,) = self.prof("stic1 rel9\n", bp.Sources(est=Est(), drive=Drive))
        self.assertEqual([j.name for j in s1.jobs], ["stic:debian13", "stic:alpine324"])
        self.assertEqual(set(Drive.calls), {tuple(HOSTS)})
        b, (s2,) = self.prof("stic1 rel9 debian13-arm64\n", bp.Sources(est=Est(), drive=Drive))
        self.assertEqual([j.name for j in s2.jobs], ["stic:debian13-arm64"])   # named: kept (drive.py refuses)

    def test_stic1_bkotest1all_real_drive_no_arm64(self):
        # The real drive.py on the four x86 hosts: no -arm64 job in bkotest1all's stic1.
        drive = os.path.join(TESTVM, "stictest1", "drive.py")
        mod = bp.load_path("t2_test_drive_a6", drive)
        want = [g for g in mod.all_guests() if not g.endswith("-arm64")]
        b, (s1,) = self.prof("stic1 rel9\n", bp.Sources(est=type("E", (), {"cell": lambda s, c: {"wall_s": 1, "src": "t"}})(),
                                                        drive=mod))
        self.assertEqual([j.name for j in s1.jobs], [f"stic:{g}" for g in want])

    def test_bs2_walls(self):
        mod = bp.load_path("t2_test_bs2", os.path.join(TESTVM, "backshot2", "bs2"))
        b, (p,) = self.prof("bs2 BackShot2Alpine 20\n", bp.Sources(bs2=mod))
        self.assertEqual(p.wall_src, "bs2")
        full, one = p.walls[frozenset(HOSTS)], p.walls[frozenset(["cage"])]
        self.assertLess(full, one)
        b, (q,) = self.prof("bs2 BackShot2Alpine 20\n", bp.Sources(bs2=mod, have={"chopin": set(mod.ALPINE),
                                                                                 "cinqcent": set(mod.ALPINE),
                                                                                 "copland": set(mod.ALPINE),
                                                                                 "cage": {"alpine324"}}))
        self.assertIsNone(q.walls[frozenset(["cage"])])           # cage holds alpine324 alone


class PlanRender(Fixture):
    def test_carpetbeater(self):
        b = bf.parse("set round rel6\nmanual step 1\nt2 GateTest2All rel6 est=65m\nt2 SweepTest2 rel6 est=6h10m\n"
                     "t2 ImgTest2All release est=2h10m\nbs2 BackShot2Alpine 200 est=2h55m\nmanual step 5\n",
                     "t.batch", None)
        sim, ser, ps, cap, notes = B.plan_schedule(b, HOSTS, self.t, os.path.dirname(TESTVM), self.tmp, None, CEIL,
                                                   planner=False, now=0)
        txt = B.render_schedule(b, sim, HOSTS, 0, serial_finish=ser.finish, notes=notes)
        lines = txt.splitlines()
        self.assertEqual(lines[0], "batch t  hosts chopin cinqcent copland cage  mode parallel  round rel6")
        self.assertEqual(lines[1], "manual  step 1")
        self.assertTrue(lines[3].startswith("003-t2-sweeptest2-rel6"))
        self.assertIn("shares: 003-t2-sweeptest2-rel6 fills", txt)
        self.assertIn("lock gate: 003-t2-sweeptest2-rel6", txt)
        self.assertIn("manual  step 5", txt)
        self.assertRegex(txt, r"finish  \+\d+:\d\d")
        self.assertIn("mode serial: +12:20", txt)
        self.assertLess(sim.finish, ser.finish)
        self.assertNotIn("ceiling is not known", txt)
        sim2, _s, _p, _c, notes2 = B.plan_schedule(b, HOSTS, self.t, os.path.dirname(TESTVM), self.tmp, None, {},
                                                   planner=False, now=0)
        self.assertTrue(any("ceiling is not known" in n for n in notes2))

    def test_grows_rendered(self):
        jobs = [bs.Job(f"holt:g{i}", 1800, 4, 4, "holt") for i in range(24)]
        p = bs.Profile("001-ht1-r-a", "ht1", 1, pools={"holt"}, split="any", growable=True, jobs=jobs, walls={})
        busy = bs.Profile("002-cmd-b", "cmd", 2, pools=set(bs.POOLS), hosts=("copland", "cage"), walls={None: H})
        b = bf.parse("ht1 R a\ncmd b hosts=copland,cage -- true\n", "g.batch", None)
        sim = bs.simulate([p, busy], self.cap, [bs.Running("002-cmd-b", ["copland", "cage"], set(bs.POOLS), 0, H)], 0)
        txt = B.render_schedule(b, sim, HOSTS, 0)
        self.assertIn("chopin cinqcent +copl", txt)


class SleepTolerance(unittest.TestCase):
    """B4's pure parts."""

    def test_unknown_max(self):
        old = os.environ.pop("T2_UNKNOWN_MAX", None)
        try:
            self.assertEqual(hostjob.unknown_max(), hostjob.UNKNOWN_MAX)       # a session's default
            os.environ["T2_UNKNOWN_MAX"] = "480"
            self.assertEqual(hostjob.unknown_max(), 480)
            os.environ["T2_UNKNOWN_MAX"] = "3"
            self.assertEqual(hostjob.unknown_max(), hostjob.UNKNOWN_MAX)       # never lower
            os.environ["T2_UNKNOWN_MAX"] = "junk"
            self.assertEqual(hostjob.unknown_max(), hostjob.UNKNOWN_MAX)

            class IO:
                unknown_max = 100
            self.assertEqual(hostjob.unknown_max(IO()), 100)                  # the HostIO field wins
        finally:
            os.environ.pop("T2_UNKNOWN_MAX", None)
            if old is not None:
                os.environ["T2_UNKNOWN_MAX"] = old

    def test_reattach_waits_out_a_sleep(self):
        """reattach_job keeps waiting through more silent polls than UNKNOWN_MAX when the patience
        says so, and still gets the job's real exit."""
        class Env:
            host_root = "/h"

            def __init__(self, io):
                self.io = io

            def attempt_run(self, a):
                return "t2h-20261115T0000-aaaa"

            def log(self, *_a):
                pass

        class IO:
            def __init__(self, silent, unknown_max=None):
                self.n, self.silent, self.unknown_max = 0, silent, unknown_max

            def sh(self, host, cmd, timeout=60, input=None):
                self.n += 1
                if self.n <= self.silent:
                    return 255, "", "no route"
                return 0, ".exit=0\n.began=1\n.pid=9\n", ""

            def sleep(self, s):
                pass

            def fetch(self, host, out, names):
                return {}

        ex, _out, _t, why = hostjob.reattach_job(Env(IO(30, unknown_max=60)), "cage", "holt:x", 1, subdir="holt")
        self.assertEqual((ex, why), (0, None))
        ex, _out, _t, why = hostjob.reattach_job(Env(IO(30)), "cage", "holt:x", 1, subdir="holt")
        self.assertIsNone(ex)
        self.assertTrue(why.still_running)                                    # given up, never "ended"

    def test_ssh_options(self):
        self.assertEqual(B.ssh_with_patience(None), "ssh -o ServerAliveCountMax=4 -o ConnectionAttempts=3")
        self.assertEqual(B.ssh_with_patience("/usr/bin/false"),
                         "/usr/bin/false -o ServerAliveCountMax=4 -o ConnectionAttempts=3")
        once = B.ssh_with_patience("ssh")
        self.assertEqual(B.ssh_with_patience(once), once)

    def test_255_is_no_answer(self):
        class R:
            def __init__(self, rc):
                self.rc = rc
        seq = [R(255), R(124), R(0)]
        slept = []
        r = B.ssh_retry(lambda: seq.pop(0), tries=4, backoff=1, sleep=slept.append)
        self.assertEqual(r.rc, 0)
        self.assertEqual(slept, [1, 2])                                        # doubling backoff
        r = B.ssh_retry(lambda: R(1), tries=4, backoff=1, sleep=slept.append)
        self.assertEqual(r.rc, 1)                                              # an answer, at once
        self.assertEqual(len(slept), 2)
        r = B.ssh_retry(lambda: R(255), tries=3, backoff=1, sleep=lambda s: None)
        self.assertEqual(r.rc, 255)
        boom = B.ssh_retry(lambda: (_ for _ in ()).throw(OSError("x")), tries=2, backoff=0, sleep=lambda s: None)
        self.assertIsNone(boom)


if __name__ == "__main__":
    unittest.main()
