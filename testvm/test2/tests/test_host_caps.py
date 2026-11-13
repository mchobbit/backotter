"""What a host can do, for placement (gaps P1, P3, P4, P6's plan side): stic and holt cells are
kept off an image-cache host (hosts.json: not a fleet host) whose vms/ lacks a guest the cell
boots, off a host with no OpenZFS for a source vm-mount reads on ZFS (classes.json
holt.needs_host_zfs), and off a host without HoltTest's rescuer or debs (facts' caps).  A cell no
host can run says why.  The identity step compares only the hosts that hold a guest: a cache
that lacks it neither vouches for nor condemns it.  Old facts (no caps) check nothing of it, and
a fleet host that lacks a guest keeps the cell as before.  No host needed."""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TEST2 = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(TEST2))
sys.path.insert(0, TEST2)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "fixtures", "p2"))

from t2lib import guest, holt, plan, report  # noqa: E402
import test_guest as TG  # noqa: E402
import world  # noqa: E402

FACTS = os.path.join(TEST2, "t2lib", "facts_host.py")
CAPS = {"zfs": True, "zpool": "/usr/sbin/zpool", "zfs_module": True, "holt_rescuer": True, "holt_debs": True}
SMALL = dict(CAPS, zfs=False, zpool=None, zfs_module=False)


def setUpModule():
    TG.setUpModule()       # the identity step's fakes want test_guest's T2_MAC_ROOT


def tearDownModule():
    TG.tearDownModule()


class ProbeCaps(unittest.TestCase):
    """facts_host.py's caps part, on the fake root."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2-caps-facts-")
        self.root = os.path.join(self.tmp, "root")
        shutil.copytree(os.path.join(HERE, "fixtures", "p1", "factsroot"), self.root, symlinks=True)
        self.work = os.path.join(self.root, "home", "m", "backotter-work")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def probe(self):
        args = [sys.executable, "-I", "-", "--work", self.work, "--user", "m", "--home", os.path.dirname(self.work)]
        env = dict(os.environ, T2_FACTS_ROOT=self.root)
        with open(FACTS, "rb") as f:
            p = subprocess.run(args, stdin=f, capture_output=True, env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        f = json.loads(p.stdout)
        self.assertFalse([e for e in f["errors"] if e.startswith("caps")], f["errors"])
        return f["caps"]

    def test_no_zpool_then_a_zpool(self):
        c = self.probe()
        self.assertEqual((c["zfs"], c["zpool"], c["zfs_module"]), (False, None, True))   # the module alone isn't enough
        self.assertEqual((c["holt_rescuer"], c["holt_debs"]), (True, True))
        zp = os.path.join(self.root, "usr", "sbin", "zpool")
        os.makedirs(os.path.dirname(zp))
        with open(zp, "w") as f:
            f.write("#!/bin/sh\n")
        self.assertFalse(self.probe()["zfs"])                 # not executable
        os.chmod(zp, 0o755)
        c = self.probe()
        self.assertEqual((c["zfs"], c["zpool"]), (True, "/usr/sbin/zpool"))

    def test_no_holttest_inputs(self):
        hd = os.path.join(self.root, "bkoscratch", "holttest1d")
        os.remove(os.path.join(hd, "rescuer", "rescuer.qcow2"))
        for n in os.listdir(os.path.join(hd, "bin")):
            if n.endswith(".deb"):
                os.remove(os.path.join(hd, "bin", n))
        c = self.probe()
        self.assertEqual((c["holt_rescuer"], c["holt_debs"]), (False, False))
        shutil.rmtree(hd)                                      # cage: no /bkoscratch/holttest1d at all
        c = self.probe()
        self.assertEqual((c["holt_rescuer"], c["holt_debs"]), (False, False))


class Placement(unittest.TestCase):
    """The plan on the fixture world: chopin a fleet host, copland and cage image caches."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t2-caps-plan-")
        cls.w = world.World(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def plan(self, text, hosts, lacks=None, caps=None):
        """LACKS: {host: [guests its facts don't have]}; CAPS: {host: caps}."""
        fx = self.w.facts(hosts=hosts)
        for h, gs in (lacks or {}).items():
            for g in gs:
                fx[h]["guests"].pop(g, None)
        for h, c in (caps or {}).items():
            fx[h]["caps"] = dict(c)
        return self.w.plan(text, facts=fx, hosts=hosts)

    # --- P1: the guest must be on the host ---

    def test_cells_kept_off_a_cache_whose_guest_went_since_facts(self):
        """The identity step found copland's mint22 gone (guest._gone_since_facts): its cells are
        placed off copland, and the reason says the entry went since the facts."""
        fx = self.w.facts(hosts=["chopin", "copland"])
        io = TG.FakeIO()
        real = io.identity
        io.identity = lambda host, g, args: {"missing": True, "dangling": True} if host == "copland" else real(host, g, args)
        c = TG.Ctx(TG.RS(TG.Word(), hosts=("chopin", "copland")))
        c.facts = fx
        r = guest.identity_check(c, ["mint22"], io=io)
        self.assertTrue(r["mint22"]["ok"])
        self.assertEqual(r["mint22"]["lacking"], {"copland": "its vms/ link dangles, gone since facts"})
        p = self.w.plan("SticTest2 rel4 debian13 mint22 plan", facts=fx, hosts=["chopin", "copland"])
        m, d = p.cell("stic:mint22"), p.cell("stic:debian13")
        self.assertEqual((m.status, sorted(m.keys), m.host), ("due", ["chopin"], "chopin"))
        self.assertIn("kept off copland: no mint22 in its vms/ (its vms/ link dangles, gone since facts)", m.reasons)
        self.assertEqual(sorted(d.keys), ["chopin", "copland"])
        p = self.w.plan("SticTest2 rel4 mint22 plan", facts={"copland": fx["copland"]}, hosts=["copland"])
        m = p.cell("stic:mint22")
        self.assertEqual((m.status, m.reasons), ("not-runnable",
                         ["no host of the run can run it: copland: no mint22 in its vms/ (its vms/ link dangles, gone since facts)"]))

    def test_stic_kept_off_a_cache_without_the_guest(self):
        p = self.plan("SticTest2 rel4 debian13 mint22 plan", ["chopin", "copland"], {"copland": ["mint22"]})
        m, d = p.cell("stic:mint22"), p.cell("stic:debian13")
        self.assertEqual((m.status, sorted(m.keys), m.host), ("due", ["chopin"], "chopin"))
        self.assertIn("kept off copland: no mint22 in its vms/", m.reasons)
        self.assertEqual(sorted(d.keys), ["chopin", "copland"])

    def test_stic_on_no_host_says_why(self):
        p = self.plan("SticTest2 rel4 mint22 plan", ["copland", "cage"], {"copland": ["mint22"], "cage": ["mint22"]})
        m = p.cell("stic:mint22")
        self.assertEqual(m.status, "not-runnable")
        why = "no host of the run can run it: copland: no mint22 in its vms/; cage: no mint22 in its vms/"
        self.assertEqual(m.reasons, [why])
        self.assertIn(f"not runnable: stic:mint22 ({why})", report.render_plan(p))

    def test_a_fleet_host_without_the_guest_keeps_the_cell(self):
        """chopin holds every guest: one it lacks is a loss its key shows (missing), as before."""
        p = self.plan("SticTest2 rel4 mint22 plan", ["chopin", "cinqcent"], {"chopin": ["mint22"]})
        m = p.cell("stic:mint22")
        self.assertEqual(sorted(m.keys), ["chopin", "cinqcent"])
        self.assertEqual(m.res["chopin"].inputs["guest"]["mint22:generation"], "missing")

    def test_facts_with_no_guests_part_check_nothing(self):
        fx = self.w.facts(hosts=["copland"])
        fx["copland"]["guests"] = None                     # the part failed
        p = self.w.plan("SticTest2 rel4 debian13 plan", facts=fx, hosts=["copland"])
        self.assertNotIn("no debian13 in its vms/", " ".join(p.cell("stic:debian13").reasons))

    def test_holt_needs_its_rescuer_guest(self):
        """freebsd15 is rescued by the freebsd15-ufs guest: a cache without it can't run it."""
        p = self.plan("HoltTest2 freebsd15 plan", ["chopin", "copland"], {"copland": ["freebsd15-ufs"]})
        c = p.cell("holt:freebsd15")
        self.assertEqual(sorted(c.keys), ["chopin"])
        self.assertIn("kept off copland: no freebsd15-ufs in its vms/", c.reasons)

    def test_holt_only_elsewhere_follows(self):
        p = self.plan("HoltTest2 mint22 debian13 plan", ["chopin", "copland"], {"copland": ["mint22"]})
        self.assertEqual(sorted(p.cell("holt:mint22").keys), ["chopin"])
        self.assertEqual(holt.only_elsewhere(p.due(), ["chopin", "copland"], {}), {"chopin": [], "copland": ["mint22"]})

    def test_the_cells_guests(self):
        pl = plan.Planner.__new__(plan.Planner)
        pl.ctx = plan.Ctx(self.w.repo, hosts=[], options={})
        self.assertEqual(pl.cell_guests(plan.View("holt:freebsd15", "holt")), ["freebsd15", "freebsd15-ufs"])
        self.assertEqual(pl.cell_guests(plan.View("holt:debian13", "holt")), ["debian13"])
        self.assertEqual(pl.cell_guests(plan.View("stic:debian13", "stic")), ["debian13"])

    # --- P4: host OpenZFS ---

    def test_zfs_source_kept_off_a_host_without_openzfs(self):
        p = self.plan("HoltTest2 ubuntu24 debian13 plan", ["chopin", "copland"], caps={"chopin": CAPS, "copland": SMALL})
        u, d = p.cell("holt:ubuntu24"), p.cell("holt:debian13")
        self.assertEqual((u.status, sorted(u.keys)), ("due", ["chopin"]))
        self.assertIn("kept off copland: no host OpenZFS (vm-mount reads the ZFS source on the host)", u.reasons)
        self.assertEqual(sorted(d.keys), ["chopin", "copland"])
        self.assertEqual(holt.only_elsewhere(p.due(), ["chopin", "copland"], {}), {"chopin": [], "copland": ["ubuntu24"]})

    def test_zfs_source_with_no_openzfs_host_is_not_runnable(self):
        p = self.plan("HoltTest2 debian13-zfs plan", ["copland", "cage"], caps={"copland": SMALL, "cage": SMALL})
        c = p.cell("holt:debian13-zfs")
        self.assertEqual(c.status, "not-runnable")
        self.assertEqual(c.reasons, ["no host of the run can run it: copland: no host OpenZFS (vm-mount reads the ZFS "
                                     "source on the host); cage: no host OpenZFS (vm-mount reads the ZFS source on the host)"])
        self.assertEqual(p.due(), [])

    def test_zfs_needed_by_every_variant_and_not_by_stic(self):
        p = self.plan("HoltTest2 ubuntu24+img plan", ["chopin", "copland"], caps={"chopin": CAPS, "copland": SMALL})
        c = p.cell("holt:ubuntu24+img")
        self.assertEqual((c.status, sorted(c.keys)), ("due", ["chopin"]))
        self.assertIn("kept off copland: no host OpenZFS (vm-mount reads the ZFS source on the host)", c.reasons)
        p = self.plan("SticTest2 rel4 debian13-zfs plan", ["copland"], caps={"copland": SMALL})
        self.assertEqual(sorted(p.cell("stic:debian13-zfs").keys), ["copland"])   # SticTest reads it in the guest

    def test_old_facts_have_no_caps(self):
        p = self.plan("HoltTest2 ubuntu24 plan", ["chopin", "copland"])
        self.assertEqual(sorted(p.cell("holt:ubuntu24").keys), ["chopin", "copland"])

    # --- P6: HoltTest's rescuer and debs ---

    def test_holt_inputs(self):
        bare = dict(SMALL, holt_rescuer=False, holt_debs=False)
        p = self.plan("HoltTest2 debian13 freebsd15 sculpt2610 plan", ["chopin", "copland"],
                      caps={"chopin": CAPS, "copland": bare})
        d = p.cell("holt:debian13")
        self.assertEqual(sorted(d.keys), ["chopin"])
        self.assertIn("kept off copland: no /bkoscratch/holttest1d/rescuer/rescuer.qcow2, "
                      "no debs in /bkoscratch/holttest1d/bin", d.reasons)
        self.assertEqual(sorted(p.cell("holt:freebsd15").keys), ["chopin", "copland"])    # a BSD needs neither
        s = p.cell("holt:sculpt2610")                                                      # offline: the rescuer
        self.assertEqual(sorted(s.keys), ["chopin"])
        self.assertIn("kept off copland: no /bkoscratch/holttest1d/rescuer/rescuer.qcow2", s.reasons)

    def test_uefi_source_with_the_uefi_rescuer_needs_no_bios_one(self):
        nob = dict(CAPS, holt_rescuer=False)
        p = self.plan("HoltTest2 pop24 plan", ["chopin", "cinqcent"], caps={"chopin": nob, "cinqcent": nob})
        self.assertEqual(sorted(p.cell("holt:pop24").keys), ["chopin", "cinqcent"])  # both have rescuer-uefi.qcow2

    def test_reasons_add_up(self):
        """A UEFI source kept off a host for a missing rescuer, with the UEFI note beside it."""
        hs = ["chopin", "cinqcent", "copland", "cage"]
        fx = self.w.facts(hosts=hs, uefi_rescuer=("chopin", "cinqcent"))
        fx["copland"]["guests"].pop("pop24")
        p = self.w.plan("HoltTest2 pop24 plan", facts=fx, hosts=hs)
        c = p.cell("holt:pop24")
        self.assertEqual(sorted(c.keys), ["chopin", "cinqcent"])
        self.assertEqual(c.reasons[:2], ["kept off copland: no pop24 in its vms/",
                                         "UEFI source: only chopin, cinqcent have rescuer-uefi.qcow2"])

    # --- the class table ---

    def test_needs_host_zfs_in_the_class_table(self):
        ctx = plan.Ctx(REPO, hosts=[], options={})
        self.assertEqual(sorted(ctx.classes["holt"]["needs_host_zfs"]),
                         ["cachyos-zfs", "chimera-zfs", "debian13-zfs", "ubuntu24", "ubuntu26-zfs", "ubuntu26-zfsmirror"])
        cl = json.loads(json.dumps(ctx.classes))
        cl["holt"]["needs_host_zfs"] += ["freebsd15", "nosuch"]
        errs = plan.check_classes(plan.Ctx(REPO, hosts=[], options={"classes": cl}), holt_names=world.GUESTS)
        self.assertTrue(any("needs_host_zfs nosuch is no source" in e for e in errs), errs)
        self.assertTrue(any("needs_host_zfs freebsd15 is a bsd source" in e for e in errs), errs)

    def test_needs_host_zfs_matches_the_zfs_builders(self):
        """Every Linux holt source whose builder makes a ZFS pool is listed (ubuntu24's name doesn't say so)."""
        sf = plan.Ctx(REPO, hosts=[], options={}).specs.get("holt")
        linux = {g for g in sf.names() if sf.entry(g).get("kind") == "linux"}
        listed = set(plan.Ctx(REPO, hosts=[], options={}).classes["holt"]["needs_host_zfs"])
        self.assertLessEqual(listed, linux)
        self.assertLessEqual({g for g in linux if "zfs" in g}, listed)


class Identity(unittest.TestCase):
    """P3: the identity step compares the hosts that hold the guest."""

    def info(self, gen="g"):
        return TG.Identity.info(None, gen)

    def test_may_lack_guests(self):
        self.assertEqual([h for h in ("chopin", "cinqcent", "copland", "cage") if guest.may_lack_guests(h)],
                         ["cinqcent", "copland", "cage"])          # cinqcent a cache since 2026-11-13
        self.assertFalse(guest.may_lack_guests("nosuch"))        # unknown: a fleet host, as before
        self.assertFalse(guest.may_lack_guests("czerny"))        # reserved: not a host yet

    def test_compare_leaves_out_a_cache_without_it(self):
        per = {"chopin": self.info(), "cinqcent": self.info(), "copland": {"missing": True}}
        r = guest.identity_compare(per, optional={"copland", "cage"})
        self.assertEqual((r["ok"], r["dropped"]), (True, {"copland": "gone"}))
        per["copland"] = {"missing": True, "dangling": True}
        self.assertEqual(guest.identity_compare(per, optional={"copland"})["dropped"], {"copland": "dangles"})
        per["copland"] = self.info()
        self.assertNotIn("dropped", guest.identity_compare(per, optional={"copland"}))   # it holds it: compared
        per["copland"] = {"missing": True}
        r = guest.identity_compare(per)                        # no optional hosts: as before
        self.assertEqual((r["ok"], r["why"]), (False, "copland: no such guest"))
        per["chopin"] = {"missing": True}                      # a fleet host without it still condemns
        r = guest.identity_compare(per, optional={"copland"})
        self.assertEqual((r["ok"], r["why"]), (False, "chopin: no such guest"))
        r = guest.identity_compare({"copland": {"missing": True, "dangling": True}, "cage": {"missing": True}},
                                   optional={"copland", "cage"})
        self.assertEqual((r["ok"], r["why"]), (False, "copland: no such guest (its vms/ link dangles); cage: no such guest"))

    def test_compare_still_fails_differing_generations(self):
        per = {"chopin": self.info("a"), "copland": self.info("b"), "cage": {"missing": True}}
        r = guest.identity_compare(per, optional={"copland", "cage"})
        self.assertIs(r["ok"], False)
        self.assertIn("generations differ", r["why"])

    def ctx(self, hosts, have):
        rs = TG.RS(TG.Word(), hosts=hosts)
        c = TG.Ctx(rs)
        c.facts = {h: {"guests": {g: {} for g in have.get(h, ())}} for h in hosts}
        return c

    def test_check_asks_only_the_holders(self):
        io = TG.FakeIO()
        c = self.ctx(("chopin", "cinqcent", "copland"), {"chopin": ["g1", "g2"], "cinqcent": ["g1", "g2"], "copland": ["g2"]})
        r = guest.identity_check(c, ["g1", "g2"], io=io)
        self.assertEqual(r, {"g1": {"ok": True, "why": None}, "g2": {"ok": True, "why": None}})
        asked = {h: [a for a in d["args"][1:] if not a.startswith("--")] for h, d in io.scripts}
        self.assertEqual(asked, {"chopin": ["g1", "g2"], "cinqcent": ["g1", "g2"], "copland": ["g2"]})
        self.assertEqual(io.rows, [])                          # no FAIL row

    def test_check_with_a_cache_whose_entry_went(self):
        """Facts said copland had it; the tool finds vms/g1's link dangling: the guest isn't
        condemned (chopin holds it), but copland's facts lose g1, so placement keeps g1's cells
        off copland, saying why."""
        io = TG.FakeIO()
        real = io.identity
        io.identity = lambda host, g, args: {"missing": True, "dangling": True} if host == "copland" else real(host, g, args)
        c = self.ctx(("chopin", "copland"), {"chopin": ["g1", "g2"], "copland": ["g1", "g2"]})
        lines = []
        r = guest.identity_check(c, ["g1"], io=io, log=lines.append)
        why = "its vms/ link dangles, gone since facts"
        self.assertEqual(r, {"g1": {"ok": True, "why": None, "lacking": {"copland": why}}})
        self.assertEqual(io.rows, [])                          # no FAIL row
        self.assertEqual(sorted(c.facts["copland"]["guests"]), ["g2"])
        self.assertEqual(c.facts["copland"]["guests_gone"], {"g1": why})
        self.assertEqual(sorted(c.facts["chopin"]["guests"]), ["g1", "g2"])
        self.assertIn(f"guest:g1: copland: no g1 in its vms/ ({why}): its stic and holt cells stay off copland", lines)

    def test_check_cache_with_no_guests_part_lacking_it(self):
        """Old facts (no guests part): asked without knowing; the cache is left out, facts untouched."""
        io = TG.FakeIO()
        real = io.identity
        io.identity = lambda host, g, args: {"missing": True} if host == "copland" else real(host, g, args)
        c = self.ctx(("chopin", "copland"), {"chopin": ["g1"]})
        c.facts["copland"] = {}
        self.assertEqual(guest.identity_check(c, ["g1"], io=io), {"g1": {"ok": True, "why": None}})
        self.assertEqual(c.facts["copland"], {})

    def test_check_no_holder_at_all_fails(self):
        io = TG.FakeIO()
        io.identity = lambda host, g, args: {"missing": True}
        c = self.ctx(("copland", "cage"), {})
        r = guest.identity_check(c, ["g1"], io=io)
        self.assertIs(r["g1"]["ok"], False)
        self.assertEqual(r["g1"]["why"], "copland: no such guest; cage: no such guest")
        self.assertEqual(len(io.rows), 1)                      # its FAIL row

    def test_fleet_hosts_as_before(self):
        io = TG.FakeIO()
        io.identity = lambda host, g, args: {"missing": True} if host == "chopin" else TG.FakeIO.identity(io, host, g, args)
        c = self.ctx(("chopin", "cinqcent"), {"cinqcent": ["g1"]})
        r = guest.identity_check(c, ["g1"], io=io)
        self.assertEqual((r["g1"]["ok"], r["g1"]["why"]), (False, "chopin: no such guest"))

    def test_tool_says_a_link_dangles(self):
        from t2lib import tool_guest
        t = tempfile.mkdtemp(prefix="t2-caps-tool-")
        try:
            os.makedirs(os.path.join(t, "vms"))
            os.symlink(os.path.join(t, "cache", "g1"), os.path.join(t, "vms", "g1"))
            self.assertEqual(tool_guest.identity_of(t, "g1"), {"missing": True, "dangling": True})
            self.assertEqual(tool_guest.identity_of(t, "g2"), {"missing": True})
        finally:
            shutil.rmtree(t, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
