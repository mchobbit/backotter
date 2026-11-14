"""An image cache at a stale image-state generation (the identity step, gap P1's cache side):
Cage's vms/void-musl was linked to an older vm-store version than Chopin, Cinqcent and Copland
on 2026-11-14, and the identity step failed void-musl outright ("the hosts' generations
differ"), which took its stic and holt cells off the whole four-host farm.  A cache that
disagrees with the fleet is now dropped like one whose entry is gone (guest._stale_caches): the
guest passes, its cells run on the hosts that remain, and the plan says the cache is kept off
for a stale generation and what mends it.  A fleet host that disagrees, or a guest no fleet host
of the run holds, still fails as before.  No host needed:

    cd testvm/test2 && python3 -m unittest tests.test_identity_stale
"""

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TEST2 = os.path.dirname(HERE)
sys.path.insert(0, TEST2)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "fixtures", "p2"))

from t2lib import bko, guest, holt, report  # noqa: E402
from t2lib import run as runmod  # noqa: E402
import test_bko as TB  # noqa: E402
import test_guest as TG  # noqa: E402
import world  # noqa: E402

FLEET, CACHE = "chopin", "cage"       # testvm/hosts.json: cage has no fleet role
OTHER_CACHE = "copland"
OLD = "g2aaa1b2c3d4" + "0" * 52       # the generation cage held on 2026-11-14
NEW = "if368" + "1" * 59


def setUpModule():
    TG.setUpModule()       # the identity step's fakes want test_guest's T2_MAC_ROOT


def tearDownModule():
    TG.tearDownModule()


class Compare(unittest.TestCase):
    """identity_compare on what the hosts answered, with no host and no facts.  The optional set
    is given here as the step gives it (may_lack_guests' answer for the run's hosts), so a name
    stands for the role the case wants, not for its row in today's table."""

    def info(self, gen=NEW, **d):
        st = {"generation": gen, "disks": {"a": {"size": 1, "mtime_ns": 2, "inode": 3}}}
        return dict({"image_state": {"generation": gen, "sha256": {"a": "S"}}, "stat": st,
                     "disks": {"a": {"size": 1, "mtime_ns": 2, "inode": 3}},
                     "qemu": {"a": {"ok": True, "format": "qcow2", "backing": None, "dirty": False,
                                    "corrupt": False, "snapshots": ["fresh-install"]}}}, **d)

    def test_a_stale_cache_is_dropped_not_failed(self):
        r = guest.identity_compare({FLEET: self.info(), "cinqcent": self.info(), CACHE: self.info(OLD)},
                                   optional={CACHE, OTHER_CACHE})
        self.assertTrue(r["ok"], r.get("why"))
        self.assertEqual(r["dropped"], {CACHE: "stale:" + OLD})
        self.assertEqual(guest.dropped_why("void-musl", r["dropped"][CACHE]),
                         "stale generation g2aaa1b2c3d4 (run vm-store link void-musl there)")

    def test_two_stale_caches(self):
        r = guest.identity_compare({FLEET: self.info(), CACHE: self.info(OLD), OTHER_CACHE: self.info("h" * 64)},
                                   optional={CACHE, OTHER_CACHE})
        self.assertTrue(r["ok"], r.get("why"))
        self.assertEqual(sorted(r["dropped"]), [CACHE, OTHER_CACHE])

    def test_a_stale_cache_beside_one_that_lacks_it(self):
        r = guest.identity_compare({FLEET: self.info(), CACHE: self.info(OLD), OTHER_CACHE: {"missing": True}},
                                   optional={CACHE, OTHER_CACHE})
        self.assertTrue(r["ok"], r.get("why"))
        self.assertEqual(r["dropped"], {CACHE: "stale:" + OLD, OTHER_CACHE: "gone"})

    def test_the_fleet_hosts_disagreeing_still_fails(self):
        r = guest.identity_compare({FLEET: self.info(), "cinqcent": self.info(OLD), CACHE: self.info()},
                                   optional={CACHE})
        self.assertIs(r["ok"], False)
        self.assertIn("the hosts' generations differ", r["why"])
        self.assertNotIn("dropped", r)

    def test_caches_only_still_fail(self):
        """No fleet host of the run holds it: there is no generation to be stale against."""
        r = guest.identity_compare({CACHE: self.info(OLD), OTHER_CACHE: self.info()},
                                   optional={CACHE, OTHER_CACHE})
        self.assertIs(r["ok"], False)
        self.assertIn("the hosts' generations differ", r["why"])

    def test_a_cache_with_no_image_state_is_dropped_too(self):
        """It has no version to name, but it differs from the fleet's generation all the same and
        the same `vm-store link` mends it: dropped, not a farm-wide failure."""
        i = self.info()
        i["image_state"] = None
        r = guest.identity_compare({FLEET: self.info(), CACHE: i}, optional={CACHE})
        self.assertTrue(r["ok"], r.get("why"))
        self.assertEqual(r["dropped"], {CACHE: "stale:"})
        self.assertEqual(guest.dropped_why("void-musl", r["dropped"][CACHE]),
                         "no image-state of its own (run vm-store link void-musl there)")

    def test_a_fleet_host_with_no_image_state_still_fails(self):
        i = self.info()
        i["image_state"] = None
        r = guest.identity_compare({FLEET: i, CACHE: self.info()}, optional={CACHE})
        self.assertIs(r["ok"], False)
        self.assertIn("the hosts' generations differ", r["why"])

    def test_a_silent_host_speaks_first(self):
        r = guest.identity_compare({FLEET: self.info(), "cinqcent": {"no_answer": True}, CACHE: self.info(OLD)},
                                   optional={CACHE})
        self.assertEqual((r["ok"], r["harness"]), (False, True))
        self.assertNotIn("dropped", r)

    def test_no_optional_host_changes_nothing(self):
        r = guest.identity_compare({FLEET: self.info(), CACHE: self.info(OLD)})
        self.assertIs(r["ok"], False)
        self.assertIn("the hosts' generations differ", r["why"])


class Check(unittest.TestCase):
    """identity_check and the plan on the fixture world: chopin a fleet host, cage a cache."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t2-identity-stale-")
        cls.w = world.World(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def check(self, g="mint22"):
        """identity_check for G with cage's copy at an older generation: (result, ctx, lines, io)."""
        fx = self.w.facts(hosts=[FLEET, CACHE])
        io = TG.FakeIO()
        real = io.identity
        io.identity = lambda host, name, args: (dict(real(host, name, args),
                                                     image_state={"generation": OLD, "sha256": {"a.qcow2": "s" * 64}},
                                                     stat={"generation": OLD, "disks": {"a.qcow2": {"size": 1, "mtime_ns": 2, "inode": 3}}})
                                                if host == CACHE else real(host, name, args))
        c = TG.Ctx(TG.RS(TG.Word(), hosts=(FLEET, CACHE)))
        c.facts = fx
        lines = []
        return guest.identity_check(c, [g], io=io, log=lines.append), c, lines, io

    def test_the_guest_passes_and_the_cache_is_left_out(self):
        r, c, lines, io = self.check()
        why = f"stale generation {OLD[:12]} (run vm-store link mint22 there)"
        self.assertEqual(r, {"mint22": {"ok": True, "why": None, "dropped": {CACHE: "stale:" + OLD},
                                        "lacking": {CACHE: why}}})
        self.assertIn(f"guest:mint22: {CACHE}: {why}: its stic and holt cells stay off {CACHE}", lines)
        self.assertNotIn("mint22", c.facts[CACHE]["guests"], "it leaves the cache's facts, as its next facts would")
        self.assertIn("mint22", c.facts[FLEET]["guests"])
        self.assertEqual(c.facts[CACHE]["guests_stale"], {"mint22": why})
        self.assertEqual(c.facts[CACHE]["guests_gone"], {"mint22": why})
        self.assertFalse(io.rows, "no FAIL row for a cache's own stale link")

    def test_the_cells_run_on_the_rest_of_the_farm(self):
        r, c, _, _ = self.check()
        self.assertTrue(r["mint22"]["ok"])
        p = self.w.plan("SticTest2 rel4 debian13 mint22 plan", facts=c.facts, hosts=[FLEET, CACHE])
        m, d = p.cell("stic:mint22"), p.cell("stic:debian13")
        self.assertEqual((m.status, sorted(m.keys), m.host), ("due", [FLEET], FLEET))
        self.assertEqual(sorted(d.keys), sorted([FLEET, CACHE]))
        reason = " ".join(m.reasons)
        self.assertIn(f"kept off {CACHE}:", reason)
        self.assertIn(f"stale generation {OLD[:12]} (run vm-store link mint22 there)", reason)

    def test_holt_keeps_the_cell_off_the_cache_too(self):
        r, c, _, _ = self.check()
        self.assertTrue(r["mint22"]["ok"])
        p = self.w.plan("HoltTest2 mint22 debian13 plan", facts=c.facts, hosts=[FLEET, CACHE])
        self.assertEqual(sorted(p.cell("holt:mint22").keys), [FLEET])
        self.assertEqual(holt.only_elsewhere(p.due(), [FLEET, CACHE], {}), {FLEET: [], CACHE: ["mint22"]})
        self.assertIn("stale generation", report.render_plan(p))

    def test_the_drop_outlives_a_facts_refresh(self):
        """A stale cache's vms/mint22 is still there -- only its link is old -- so a probe after
        the step answers with the guest again and would put the cells back on the cache.  The
        result's "dropped" is what the caller keeps; guest.apply_drops puts the drop back."""
        r, c, _, _ = self.check()
        drops = {"mint22": r["mint22"]["dropped"]}
        self.assertEqual(drops, {"mint22": {CACHE: "stale:" + OLD}})
        c.facts = dict(c.facts, **self.w.facts(hosts=[FLEET, CACHE]))     # Env.refresh_facts
        self.assertIn("mint22", c.facts[CACHE]["guests"], "the probe answers with the guest again")
        why = f"stale generation {OLD[:12]} (run vm-store link mint22 there)"
        self.assertEqual(guest.apply_drops(c.facts, drops), {"mint22": {CACHE: why}})
        self.assertNotIn("mint22", c.facts[CACHE]["guests"])
        self.assertEqual(c.facts[CACHE]["guests_stale"], {"mint22": why})
        p = self.w.plan("SticTest2 rel4 debian13 mint22 plan", facts=c.facts, hosts=[FLEET, CACHE])
        self.assertEqual(sorted(p.cell("stic:mint22").keys), [FLEET])
        self.assertEqual(guest.apply_drops(c.facts, drops), {}, "twice says nothing twice")


class Conductor(unittest.TestCase):
    """The composite's own step 4 (bko.Conductor): its facts refresh after the identity step, and
    the one before the gate child, probe every host afresh, so the drop has to be put back on the
    facts the final plans are made from (bko.json's identity_drops, apply_identity_drops)."""

    class Env(TB.FakeEnv):
        """FakeEnv whose refresh_facts really probes again (the real Env's does), and that keeps
        the facts each plan was made from."""

        def __init__(self, world):
            super().__init__(world)
            self.seen = []

        def refresh_facts(self, ctx, log=None):
            self.w.ev("facts")
            return {h: {"ok": True, "guests": {g: {"generation": "g1"} for g in ("arch", "debian12", "redox")}}
                    for h in ctx.facts}

        def plan(self, word, ctx):
            self.seen.append((word.family, ctx.facts))
            return super().plan(word, ctx)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2-bko-stale-")
        self.w = TB.World(self.tmp)
        self.env = self.Env(self.w)
        self.said = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_final_plans_keep_the_cells_off_the_stale_cache(self):
        self.w.identity_res = {"debian12": {"ok": True, "dropped": {CACHE: "stale:" + OLD}}}
        ex = bko.conduct(TB.word("BkoTest2 rel4"), TB.HOSTS, TB.REPO, out=self.said.append, env=self.env)
        self.assertEqual(ex, 0, "\n".join(self.said))
        run, doc = self.only_run()
        self.assertEqual(doc["identity_drops"], {"debian12": {CACHE: "stale:" + OLD}})
        self.assertEqual(doc["identity_bad"], {}, "a cache's own stale link fails no guest")
        self.assertIn(("facts",), self.w.events, "the drop changed the facts: the conductor probes again")
        why = f"stale generation {OLD[:12]} (run vm-store link debian12 there)"
        after = [f for fam, f in self.env.seen if fam in ("stic", "holt")][-2:]
        self.assertTrue(after, "the final stic and holt plans")
        for f in after:
            self.assertNotIn("debian12", f[CACHE]["guests"], "the cells stay off the stale cache")
            self.assertEqual(f[CACHE]["guests_stale"], {"debian12": why})
            self.assertIn("debian12", f[FLEET]["guests"])

    def only_run(self, prefix="t2b-"):
        runs = [n for n in os.listdir(os.path.join(self.w.mac, "runs")) if n.startswith(prefix)]
        self.assertEqual(len(runs), 1, runs)
        return runs[0], runmod.read_json(os.path.join(self.w.mac, "runs", runs[0], "bko.json"))


if __name__ == "__main__":
    unittest.main()
