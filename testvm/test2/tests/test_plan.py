"""U2.2 (the scenario table, with rulings R4, R5, R6, R14, R15, R17, R27's cases), U2.4
(estimates) and U2.6 (every cell, 10,000 rows, < 2 s) over the synthetic world of
tests/fixtures/p2/world.py.  No host, no default root."""

import collections
import copy
import hashlib
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, "fixtures", "p2"))

from t2lib import estimates, keys, ledger, plan, run   # noqa: E402
import world                                     # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))


def by(p):
    return collections.Counter((c.status, c.rule) for c in p.cells)


def st(p, cell):
    c = p.cell(cell)
    return (c.status, c.rule)


class WorldCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t2p2-plan-")
        cls.w = world.World(cls.tmp)
        cls.base = cls.w.head()
        cls.p_stic = cls.w.plan("SticTest2 rel4 plan")
        cls.r_stic = cls.w.seed(cls.p_stic, age={"stic:freebsd15": 48, "stic:freebsd15-geli": 24,
                                                 "stic:ghostbsd26-zfs": 30, "stic:netbsd11": 48,
                                                 "stic:alpinebsd": 24})
        cls.p_holt = cls.w.plan("HoltTest2 plan")
        cls.r_holt = cls.w.seed(cls.p_holt)
        cls.p_gate = cls.w.plan("GateTest2 plan")
        cls.r_gate = cls.w.seed(cls.p_gate)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def tearDown(self):
        self.w.reset(self.base)
        self.w.set_heads("c0")


class Scenarios(WorldCase):
    """The contract's U2.2 rows, one test each."""

    def test_nothing_changed(self):
        for word, rows, n in (("SticTest2 rel4", self.r_stic, 47), ("HoltTest2", self.r_holt, 70),
                              ("GateTest2", self.r_gate, 13)):
            p = self.w.plan(word + " plan", rows)
            self.assertEqual(p.totals["due"], 0, word)
            self.assertEqual(by(p), {("current", None): n}, word)
            self.assertIn("nothing due", p.render())
        # the default round is the one at the fork heads (rel4's commits are c0)
        p = self.w.plan("SticTest2 plan", self.r_stic)
        self.assertEqual((p.set["round"], p.totals["due"]), ("rel4", 0))

    def test_a_linux_binary(self):
        rc = dict(self.w.forks["c0"], rustic=self.w.forks["c3"]["rustic"])
        f = self.w.facts(round_commits=rc)
        for h in f:
            f[h]["sets"]["roundrel4"] = self.w.binset("rel4", rc, only={"linux/rustic-bko": "rel4b"})
        p = self.w.plan("SticTest2 rel4 plan", self.r_stic, facts=f)
        due = {c.cell for c in p.due()}
        self.assertEqual(len(due), 34)   # 33 Linux checks and sculpt2610
        self.assertTrue(all(st(p, c) == ("due", "changed:bin") for c in due))
        self.assertEqual(st(p, "stic:freebsd15"), ("current", None))
        self.assertIn("(rustic", "; ".join(p.cell("stic:debian13").reasons))

    def test_version_only(self):
        f = self.w.facts(round_commits=self.w.forks["c1"], round_tag="rel5")
        p = self.w.plan("SticTest2 rel4 plan", self.r_stic, facts=f)
        self.assertEqual(by(p), {("carried", "carry:version-only"): 47})
        self.assertEqual(p.cell("stic:debian13").via, self.r_stic[[r["cell"] for r in self.r_stic].index("stic:debian13")]["id"])

    def test_affected_v1_and_b2_representative(self):
        f1 = self.w.facts(round_commits=self.w.forks["c1"], round_tag="rel5")
        p1 = self.w.plan("SticTest2 rel4 plan", facts=f1)
        rows = self.w.seed(p1, age={"stic:freebsd15": 48, "stic:freebsd15-geli": 24, "stic:ghostbsd26-zfs": 30})
        f2 = self.w.facts(round_commits=self.w.forks["c2"], round_tag="rel6")
        p = self.w.plan("SticTest2 rel4 plan", rows, facts=f2)
        self.assertEqual(st(p, "stic:debian13"), ("carried", "carry:affected-v1"))
        self.assertEqual(st(p, "stic:netbsd11"), ("carried", "carry:affected-v1"))
        self.assertEqual(st(p, "stic:haiku6"), ("due", "changed:bin"))      # affected.sh has no Haiku
        self.assertEqual(st(p, "stic:freebsd14"), ("due", "changed:bin"))   # alone
        self.assertEqual(st(p, "stic:freebsd15"), ("due", "class:B2"))      # the stalest of B2
        self.assertEqual(st(p, "stic:freebsd15-geli"), ("class-pending", "class:B2"))
        self.assertEqual(st(p, "stic:ghostbsd26-zfs"), ("class-pending", "class:B2"))
        self.assertEqual(p.cell("stic:ghostbsd26-zfs").rep, "stic:freebsd15")
        b2 = [c for c in p.classes if c["class"] == "B2"][0]
        self.assertEqual((b2["rep"], sorted(b2["members"])), ("stic:freebsd15", ["stic:freebsd15-geli", "stic:ghostbsd26-zfs"]))
        # fan-out: the representative's FAIL makes the others due in the r2 round
        rr = run.rerun_map(p, [{"cell": "stic:freebsd15", "verdict": "FAIL", "rows": ["x"], "fails": ["FAIL acl: x"]}])
        self.assertEqual(rr["stic:freebsd15"]["attempt"], 2)
        self.assertEqual(rr["stic:freebsd15-geli"], {"attempt": 1, "rerun_of": None, "flake": None, "rule": "fanout:B2"})
        run.fanout_due(p, rr)
        self.assertEqual(st(p, "stic:ghostbsd26-zfs"), ("due", "fanout:B2"))

    def test_need_set_split(self):
        f = self.w.facts(round_commits=self.w.forks["c3"], round_tag="rel7")
        rows = []
        for r in self.r_stic:
            r = dict(r)
            if r["cell"] in ("stic:freebsd15-geli", "stic:ghostbsd26-zfs"):
                r["needs"] = {"seeded": "PASS", "geli-extra": "PASS"}
                r.pop("c")
                r = ledger.complete(r)
            rows.append(r)
        p = self.w.plan("SticTest2 rel4 plan", rows, facts=f)
        self.assertEqual(st(p, "stic:freebsd15"), ("due", "changed:bin"))   # its own need set: alone
        self.assertEqual(st(p, "stic:ghostbsd26-zfs"), ("due", "class:B2.2"))
        self.assertEqual(st(p, "stic:freebsd15-geli"), ("class-pending", "class:B2.2"))
        self.assertTrue(any("splits by need set" in n for n in p.notes))

    def test_plumbing_canaries_then_verified(self):
        f = self.w.facts(plumb="p2")
        p = self.w.plan("SticTest2 rel4 plan", self.r_stic, facts=f)
        can = sorted(c.cell for c in p.due())
        self.assertEqual(can, sorted(f"stic:{g}" for g in ("debian12", "zorin18", "debian13-zfs", "freebsd15-ufs",
                                                          "omnios58", "haiku6", "sculpt2610", "debian13-stack",
                                                          "omarchy")))
        self.assertTrue(all(c.rule == "canary:plumbing" for c in p.due()))
        self.assertEqual(len(p.by_status("awaiting-canaries")), 38)
        rows = self.r_stic + [self.w.row(p.cell(c), "chopin", plan_=p) for c in can]
        p2 = self.w.plan("SticTest2 rel4 plan", rows, facts=f)
        self.assertEqual(p2.totals["due"], 0)
        self.assertEqual(len(p2.by_status("canary-verified")), 38)
        # R27: a canary-verified cell may ride along beside a bare run
        f3 = self.w.facts(plumb="p2", gens={"debian13": 2})
        p3 = self.w.plan("SticTest2 rel4 plan", rows, facts=f3)
        self.assertEqual(st(p3, "stic:debian13"), ("due", "changed:guest"))
        self.assertEqual(sum(1 for c in p3.due() if c.rule == "stale:beside"), 2)

    def test_host_change_canaries_and_soft_stale(self):
        f = self.w.facts(kernel="6.12.116")
        p = self.w.plan("SticTest2 rel4 plan", self.r_stic, facts=f)
        self.assertEqual(sum(1 for c in p.due() if c.rule == "canary:host"), 9)
        self.assertEqual(sum(1 for c in p.due() if c.rule == "stale:beside"), 2)
        self.assertEqual(len(p.by_status("soft-stale")), 36)
        self.assertIn("6.12.115->6.12.116", "; ".join(p.by_status("soft-stale")[0].reasons))
        p = self.w.plan("SticTest2 rel4 stale plan", self.r_stic, facts=f)
        self.assertEqual(sum(1 for c in p.due() if c.rule == "stale"), 38)

    def test_stale_with_names_adds_the_soft_stale_cells(self):
        f = self.w.facts(kernel="6.12.116")
        p = self.w.plan("SticTest2 rel4 debian13 stale plan", self.r_stic, facts=f)
        self.assertEqual(by(p), {("due", "fresh:names"): 1, ("due", "stale"): 37})
        p = self.w.plan("SticTest2 rel4 debian13 plan", self.r_stic, facts=f)
        self.assertEqual(by(p), {("due", "fresh:names"): 1})

    def test_dirty_fail_at_an_unchanged_key(self):
        """The committed rule says what counts as a PASS: a FAIL from a dirty tree at K is a FAIL."""
        p = self.w.plan("SticTest2 rel4 plan")
        pc = p.cell("stic:debian13")
        dirty = {"testvm/stictest1/README.md": hashlib.sha256(b"x\n").hexdigest()}
        fail = self.w.row(pc, "chopin", verdict="FAIL", committed=False, dirty=dirty, age_h=-2, plan_=p)
        got = self.w.plan("SticTest2 rel4 plan", self.r_stic + [fail])
        self.assertEqual(st(got, "stic:debian13"), ("due", "failed"))
        # a dirty PASS after it clears it, and is still only an uncommitted PASS for the counts
        good = self.w.row(pc, "chopin", committed=False, dirty=dirty, age_h=-3, plan_=p)
        got = self.w.plan("SticTest2 rel4 plan", self.r_stic + [fail, good])
        self.assertEqual(st(got, "stic:debian13"), ("current", None))
        # a committed FAIL isn't cleared by a dirty PASS
        cfail = self.w.row(pc, "chopin", verdict="FAIL", age_h=-2, plan_=p)
        got = self.w.plan("SticTest2 rel4 plan", self.r_stic + [cfail, good])
        self.assertEqual(st(got, "stic:debian13"), ("due", "failed"))

    def test_fail_at_one_hosts_key_isnt_hidden_by_a_pass_at_the_others(self):
        fa = self.w.facts()
        fa["chopin"]["guests"]["debian13"]["generation"] = world.H("gen debian13 2")
        p = self.w.plan("SticTest2 rel4 plan", self.r_stic, facts=fa)
        c = p.cell("stic:debian13")
        self.assertEqual(len(set(c.keys.values())), 2)
        fail = self.w.row(c, "chopin", verdict="FAIL", age_h=-2, plan_=p)
        good = self.w.row(c, "cinqcent", age_h=-3, plan_=p)
        got = self.w.plan("SticTest2 rel4 plan", self.r_stic + [fail, good], facts=fa)
        self.assertEqual(st(got, "stic:debian13"), ("due", "failed"))

    def test_a_canary_that_failed_since_verifies_nothing(self):
        fa = self.w.facts(plumb="p2")
        p = self.w.plan("SticTest2 rel4 plan", self.r_stic, facts=fa)
        rows = self.r_stic + [self.w.row(c, "chopin", age_h=-2, plan_=p) for c in p.due()]
        got = self.w.plan("SticTest2 rel4 plan", rows, facts=fa)
        self.assertEqual(sum(1 for c in got.cells if c.status == "canary-verified"), 38)
        bad = self.w.row(p.cell("stic:debian12"), "chopin", verdict="FAIL", age_h=-3, plan_=p)
        got = self.w.plan("SticTest2 rel4 plan", rows + [bad], facts=fa)
        self.assertEqual(st(got, "stic:debian12"), ("due", "failed"))
        self.assertEqual(sum(1 for c in got.cells if c.status == "canary-verified"), 0)

    def test_dirty_row_before_and_after_its_commit(self):
        self.w.edit("testvm/stictest1/debian12/inner.sh", "# debian12 changed\n")
        sha = hashlib.sha256(b"# debian12 changed\n").hexdigest()
        p = self.w.plan("SticTest2 rel4 plan", self.r_stic)
        self.assertEqual(st(p, "stic:debian12"), ("due", "changed:harness"))
        r = self.w.row(p.cell("stic:debian12"), "chopin", committed=False,
                       dirty={"testvm/stictest1/debian12/inner.sh": sha}, plan_=p)
        p = self.w.plan("SticTest2 rel4 plan", self.r_stic + [r])
        self.assertEqual(st(p, "stic:debian12"), ("due", "changed:harness"))
        self.assertIn("pass-uncommitted", "; ".join(p.cell("stic:debian12").reasons))
        self.w.edit("testvm/stictest1/debian12/inner.sh", "# debian12 changed\n", commit=True)
        p = self.w.plan("SticTest2 rel4 plan", self.r_stic + [r])
        self.assertEqual(st(p, "stic:debian12"), ("current", None))
        # with no older PASS at all, the uncommitted PASS shows as pass-uncommitted (R27)
        self.w.reset(self.base)
        self.w.edit("testvm/stictest1/debian12/inner.sh", "# debian12 changed\n")
        rows = [x for x in self.r_stic if x["cell"] != "stic:debian12"] + [r]
        p = self.w.plan("SticTest2 rel4 plan", rows)
        self.assertEqual(st(p, "stic:debian12"), ("due", "pass-uncommitted"))

    def test_flaky_fail_and_known_flake(self):
        pc = self.p_stic.cell("stic:debian13")
        cases = ((["FAIL"], ("due", "failed")), (["FAIL", "PASS"], ("current", None)),
                 (["HARNESS"], ("current", None)), (["LOST"], ("current", None)),
                 (["FLAKY-FAIL"], ("due", "failed")), ([("PASS", "known-flake")], ("current", None)))
        for seq, want in cases:
            extra = []
            for v in seq:
                if isinstance(v, tuple):
                    extra.append(self.w.row(pc, "chopin", verdict=v[0], klass=v[1], attempt=2, plan_=self.p_stic))
                else:
                    extra.append(self.w.row(pc, "chopin", verdict=v, plan_=self.p_stic))
            p = self.w.plan("SticTest2 rel4 plan", self.r_stic + extra)
            self.assertEqual(st(p, "stic:debian13"), want, seq)
            if want[1] == "failed":
                self.assertIn("verdict changed at an unchanged key", p.cell("stic:debian13").reasons)
                self.assertTrue(any(n.startswith("verdict changed") for n in p.notes))
        self.assertEqual(run.rerun_verdict("PASS", None), ("FLAKY-FAIL", "fail"))
        self.assertEqual(run.rerun_verdict("PASS", {"guest": "ghostbsd26-zfs"}), ("PASS", "known-flake"))
        self.assertEqual(run.rerun_verdict("FAIL", None), ("FAIL", "fail"))

    def test_a_fail_after_the_pass_survives_a_change(self):
        pc = self.p_stic.cell("stic:debian13")
        rows = self.r_stic + [self.w.row(pc, "chopin", verdict="FAIL", plan_=self.p_stic)]
        self.w.edit("testvm/compare/verdict.py", "# judge changed\n")
        import json as _j
        cl = _j.loads(self.w.read("testvm/test2/classes.json"))
        cl["policy"]["judge_change"] = "soft-stale"
        p = self.w.plan("SticTest2 rel4 plan", rows, classes=cl)
        self.assertEqual(st(p, "stic:debian13"), ("due", "failed"))
        self.assertEqual(st(p, "stic:debian12"), ("soft-stale", None))

    def test_unmapped_path(self):
        self.w.edit("testvm/compare/newhelper.sh", "# no spec names it\n", commit=True)
        p = self.w.plan("SticTest2 rel4 plan", self.r_stic)
        self.assertEqual(by(p), {("due", "changed:unmapped"): 47})
        self.assertEqual(p.unmapped, ["testvm/compare/newhelper.sh"])
        self.assertIn("unmapped: name them in a spec", p.render())
        # not under holt's watch: holt is unaffected; an ignored path is unmapped for nobody
        self.assertEqual(self.w.plan("HoltTest2 plan", self.r_holt).totals["due"], 0)
        self.w.reset(self.base)
        self.w.edit("testvm/compare/NOTES.md", "notes\n", commit=True)
        self.assertEqual(self.w.plan("SticTest2 rel4 plan", self.r_stic).totals["due"], 0)

    def test_judge_change_under_each_policy(self):
        self.w.edit("testvm/compare/verdict.py", "# judge changed\n")
        p = self.w.plan("SticTest2 rel4 plan", self.r_stic)
        self.assertEqual(st(p, "stic:debian13"), ("due", "changed:judge"))
        cl = json.loads(self.w.read("testvm/test2/classes.json"))
        cl["policy"]["judge_change"] = "soft-stale"
        p = self.w.plan("SticTest2 rel4 plan", self.r_stic, classes=cl)
        self.assertEqual(by(p), {("soft-stale", None): 47})
        self.w.edit("testvm/holttest1d/judge.py", "# holt judge changed\n")
        p = self.w.plan("HoltTest2 plan", self.r_holt)
        self.assertEqual(st(p, "holt:debian13"), ("due", "holt:judge"))
        self.assertIn("re-judging waits for G2", "; ".join(p.cell("holt:debian13").reasons))
        p = self.w.plan("HoltTest2 plan", self.r_holt, classes=cl)
        self.assertEqual(st(p, "holt:debian13"), ("soft-stale", None))

    def test_a_generation(self):
        f = self.w.facts(gens={"debian13": 2})
        p = self.w.plan("SticTest2 rel4 plan", self.r_stic, facts=f)
        self.assertEqual(st(p, "stic:debian13"), ("due", "changed:guest"))
        p = self.w.plan("HoltTest2 plan", self.r_holt, facts=f)
        self.assertEqual({c.cell for c in p.due()}, {"holt:debian13", "holt:debian13+oneshot"})
        self.assertTrue(all(c.rule == "holt:guest-update" for c in p.due()))

    def test_gate_unchanged_and_changed(self):
        p = self.w.plan("GateTest2 plan", self.r_gate)
        self.assertEqual(p.totals["due"], 0)
        self.assertEqual(p.render().strip(), "GateTest2: nothing due (13 current); round rel4, base " + self.base[:7]
                         + "\nsweep: SweepTest2 never ran, due now")
        self.w.edit("testvm/test2/gate/loop.sh", "# changed\n")
        p = self.w.plan("GateTest2 plan", self.r_gate)
        self.assertEqual({c.cell for c in p.due()}, {"gate:loop:linux@chopin", "gate:loop:linux@cinqcent",
                                                     "gate:loop:sparse@chopin", "gate:loop:sparse@cinqcent"})


class Holt(WorldCase):
    def test_missing_round_is_named_on_each_host(self):
        # facts_host.py names a set it can't find as null: a run must refuse (R3 of run.py's step 4)
        facts = self.w.facts()
        facts["chopin"]["sets"]["roundnosuch"] = None
        p = self.w.plan("SticTest2 nosuch plan", self.r_stic, facts=facts)
        self.assertEqual(p.set["missing_on"], ["chopin", "cinqcent"])

    def test_row_of_a_build_that_isnt_code_counts_only_where_equal(self):
        # R14: a HoltTest1 row (sha256:) is never carried by the holt code diff
        p0 = self.w.plan("HoltTest2 plan", self.r_holt)
        pc = p0.cell("holt:debian13")
        inp = copy.deepcopy(pc.res["chopin"].inputs)
        inp["bin"]["linux/bko-holt"] = "sha256:" + "0" * 64
        r1 = self.w.row(pc, "chopin", inputs=inp, meta={"holt": {"code": None, "commit": None}})
        self.w.edit("holt/Cargo.toml", self.w.read("holt/Cargo.toml").replace("0.2.4", "0.2.6"))
        self.w.edit("holt/Cargo.lock", self.w.read("holt/Cargo.lock").replace("0.2.4", "0.2.6"), commit=True)
        p = self.w.plan("HoltTest2 plan", [r1])
        self.assertEqual(st(p, "holt:debian13"), ("due", "holt:code"))
        self.assertIn("isn't a CODE build", "; ".join(p.cell("holt:debian13").reasons))

    def test_holt_bin_keys_a_ready_set(self):
        facts = self.w.facts()
        for f in facts.values():
            f["holt_bins"]["~/ready"] = {"path": "/x", "sums": {"linux": "a" * 64, "freebsd": "b" * 64}}
        p = self.w.plan("HoltTest2 debian13 plan", self.r_holt, facts=facts, holt_bin="~/ready")
        self.assertEqual(p.set["holt_bin"], "~/ready")
        res = p.cell("holt:debian13").res["chopin"]
        self.assertEqual(res.inputs["bin"]["linux/bko-holt"], "sha256:" + "a" * 64)
        with self.assertRaises(plan.PlanError):
            self.w.plan("HoltTest2 plan", self.r_holt, holt_bin="~/nowhere")

    def test_version_only_bump_carries_all_70(self):
        self.w.edit("holt/Cargo.toml", self.w.read("holt/Cargo.toml").replace("0.2.4", "0.2.5"))
        self.w.edit("holt/Cargo.lock", self.w.read("holt/Cargo.lock").replace("0.2.4", "0.2.5"), commit=True)
        p = self.w.plan("HoltTest2 plan", self.r_holt)
        self.assertEqual(by(p), {("carried", "carry:version-only"): 70})
        res = p.cell("holt:debian13").res["chopin"]
        self.assertTrue(res.inputs["bin"]["linux/bko-holt"].startswith("code:"))   # R14
        g = self.w.plan("GateTest2 plan", self.r_gate)
        self.assertEqual(st(g, "gate:holt"), ("due", "changed:harness"))

    def test_code_no_plan_shows_all_63(self):
        self.w.edit("holt/src/load/exec.rs", "// runs the plan, changed\n", commit=True)
        p = self.w.plan("HoltTest2 plan", self.r_holt)
        self.assertEqual(sum(1 for c in p.cells if c.rule == "holt:code"), 63)
        self.assertEqual(st(p, "holt:debian13+oneshot")[0] in ("soft-stale", "due"), True)
        self.assertNotEqual(st(p, "holt:debian13+oneshot")[1], "holt:variants")
        # the variants of a guest whose golden plan changed in partition, assemble or close run
        self.w.edit("holt/tests/corpus/debian13/plan.txt", self.w.plan_text("debian13", ["partition"]), commit=True)
        p = self.w.plan("HoltTest2 plan", self.r_holt)
        self.assertEqual(st(p, "holt:debian13+oneshot"), ("due", "holt:variants"))
        self.assertIn("holt:plan-diff (partition)", "; ".join(p.cell("holt:debian13").reasons))
        # a change in the format stage leaves the variants alone
        self.w.edit("holt/tests/corpus/mint22/plan.txt", self.w.plan_text("mint22", ["format"]), commit=True)
        p = self.w.plan("HoltTest2 plan", self.r_holt)
        self.assertNotEqual(st(p, "holt:mint22+oneshot")[1], "holt:variants")

    def test_commands_rs_with_and_without_the_gate_row(self):
        self.w.edit("holt/src/load/commands.rs", "// prints the plan, changed\n", commit=True)
        p = self.w.plan("HoltTest2 plan", self.r_holt + self.r_gate)
        self.assertEqual(sum(1 for c in p.cells if c.rule == "holt:code"), 63)
        self.assertIn("plan diff not known", "; ".join(p.cell("holt:arch").reasons))
        g = self.w.plan("GateTest2 plan", self.r_gate)
        gh = self.w.row(g.cell("gate:holt"), "chopin", plan_=g, extra={"plan_diff": {
            "v": 1, "corpus": 45, "invariants": {},
            "differ": {"mint22": {"stages": ["partition"], "lines_changed": 2},
                       "arch": {"stages": ["format"], "lines_changed": 1}}}})
        p = self.w.plan("HoltTest2 plan", self.r_holt + self.r_gate + [gh])
        due = {c.cell: c.rule for c in p.due()}
        self.assertEqual(due, {"holt:mint22": "holt:plan-diff", "holt:arch": "holt:plan-diff",
                               "holt:mint22+bigger": "holt:variants", "holt:mint22+oneshot": "holt:variants",
                               "holt:debian13-stack+degraded": "holt:variants",
                               "holt:ubuntu26-raid+degraded": "holt:variants"})
        self.assertEqual(by(p)[("carried", "carry:plan-diff")], 66)

    def test_rustic_only_change_panel_and_carried(self):
        f = self.w.facts(rustic_tag="bko2")
        p = self.w.plan("HoltTest2 plan", self.r_holt, facts=f)
        self.assertEqual({c.cell for c in p.due()},
                         {f"holt:{g}" for g in ("omarchy", "nixos2605", "freebsd15", "netbsd11", "openbsd8",
                                                "dragonfly6", "omnios58", "haiku6")})
        self.assertEqual(by(p)[("carried", "carry:rustic-panel")], 64)

    def test_rescuer_panel_and_soft_stale(self):
        f = self.w.facts(rescuer="r2")
        p = self.w.plan("HoltTest2 plan", self.r_holt, facts=f)
        resc = {c.cell for c in p.due() if c.rule == "holt:rescuer"}
        self.assertEqual(resc, {f"holt:{g}" for g in ("debian12", "ubuntu24", "rocky10-raid5", "nixos2605",
                                                      "debian13-stack", "aerynos")})
        self.assertEqual(st(p, "holt:freebsd15"), ("current", None))
        self.assertGreater(len(p.by_status("soft-stale")), 20)
        # a live BSD rescuer: the stalest FreeBSD-family source, plus freebsd14
        rows = self.w.seed(self.p_holt, age={"holt:ghostbsd26": 96})
        f = self.w.facts(gens={"freebsd15-ufs": 2})
        p = self.w.plan("HoltTest2 plan", rows, facts=f)
        due = {c.cell for c in p.due() if c.rule == "holt:rescuer"}
        self.assertEqual(due, {"holt:ghostbsd26", "holt:freebsd14"})
        self.assertIn(st(p, "holt:freebsd15"), (("soft-stale", None), ("due", "stale:beside")))

    def test_new_guest_with_all_variants(self):
        rows = [r for r in self.r_holt if not r["cell"].startswith("holt:debian13")]
        p = self.w.plan("HoltTest2 plan", rows)
        self.assertEqual({c.cell for c in p.due()},
                         {"holt:debian13", "holt:debian13-zfs", "holt:debian13-stack", "holt:debian13-stack+bigger",
                          "holt:debian13+oneshot", "holt:debian13-stack+degraded"})
        self.assertTrue(all(c.rule == "holt:new" for c in p.due()))

    def test_harness_change_selects_that_kind(self):
        self.w.edit("testvm/holttest1d/ht1-bsd.sh", "# changed\n")
        p = self.w.plan("HoltTest2 plan", self.r_holt)
        due = {c.cell for c in p.due() if c.rule == "holt:harness"}
        self.assertEqual(due, {f"holt:{g}" for g in world.BSD_PLAT})

    def test_pins_and_uefi_rescuer(self):
        p = self.w.plan("HoltTest2 plan")
        self.assertEqual(p.cell("holt:openbsd8").host, "chopin")
        self.assertEqual(sorted(p.cell("holt:openbsd8").keys), ["chopin"])
        self.assertEqual(p.cell("holt:redox").host, "cinqcent")
        # R17: a UEFI source keys only on the hosts with rescuer-uefi.qcow2
        f = self.w.facts(uefi_rescuer=("chopin",))
        p = self.w.plan("HoltTest2 plan", facts=f)
        self.assertEqual(sorted(p.cell("holt:debian13-stack").keys), ["chopin"])
        self.assertEqual(sorted(p.cell("holt:debian13").keys), ["chopin", "cinqcent"])
        f = self.w.facts(uefi_rescuer=())
        p = self.w.plan("HoltTest2 plan", facts=f)
        self.assertTrue(any("BIOS rescuer for a UEFI source" in n for n in p.notes))
        p = self.w.plan("HoltTest2 openbsd8 plan", hosts=("cinqcent",), facts=self.w.facts(hosts=("cinqcent",)))
        self.assertEqual(st(p, "holt:openbsd8")[0], "not-runnable")

    def test_family_letters_and_variant_words(self):
        p = self.w.plan("HoltTest2D plan", self.r_holt)
        self.assertEqual({c.cell for c in p.due()},
                         {"holt:debian13", "holt:debian13-zfs", "holt:debian13-stack", "holt:debian13-stack+bigger",
                          "holt:debian13+oneshot"})
        self.assertTrue(all(c.rule == "fresh:family:D" for c in p.due()))
        self.assertIn("debian13+oneshot runs once (HoltTest1D ran it on each host)", p.notes)
        p = self.w.plan("HoltTest2U plan", self.r_holt)
        self.assertIn("holt:ubuntu26-raid+degraded", {c.cell for c in p.due()})
        p = self.w.plan("HoltTest2 +oneshot plan", self.r_holt)
        self.assertEqual({c.cell for c in p.due()}, {f"holt:{g}+oneshot" for g in ("debian13", "mint22", "arch", "alpine324")})
        p = self.w.plan("HoltTest2 mint22 +bigger plan", self.r_holt)
        self.assertEqual({c.cell for c in p.due()}, {"holt:mint22", "holt:mint22+bigger"})
        # a variant word beside GUEST+VARIANT names: only their guests' cells of it
        for text, want in (("HoltTest2 debian13+oneshot +bigger plan", {"holt:debian13+oneshot"}),
                           ("HoltTest2 mint22+oneshot +bigger plan", {"holt:mint22+oneshot", "holt:mint22+bigger"}),
                           ("HoltTest2 debian13+oneshot +oneshot plan", {"holt:debian13+oneshot"})):
            self.assertEqual({c.cell for c in self.w.plan(text, self.r_holt).due()}, want, text)
        p = self.w.plan("HoltTest2All plan", self.r_holt)
        self.assertEqual(by(p), {("due", "fresh:All"): 70})


class Classes(WorldCase):
    """R6: class reduction only for identical changes."""

    def test_members_fan_out_when_the_representative_failed_since(self):
        w = self.w
        f2 = w.facts(round_commits=w.forks["c2"], round_tag="rel6")
        ctx = w.ctx(self.r_stic, f2)
        wd = w.word("SticTest2 rel4", ctx)
        p = plan.make(wd, ctx)
        good = w.row(p.cell("stic:freebsd15"), "chopin", plan_=p)
        res = {"cells": [{"cell": "stic:freebsd15", "rows": [good["id"]], "final_row": good["id"], "outcome": "ran",
                          "verdict": "PASS", "valid": True}], "stops": []}
        out = run.derived_rows(p, "t2s-20261109T0001-abcd", wd, ctx, res, self.r_stic + [good])
        rows = self.r_stic + [good] + out
        now = w.plan("SticTest2 rel4 plan", rows, facts=f2)
        self.assertEqual(st(now, "stic:freebsd15-geli"), ("current", None))
        bad = w.row(now.cell("stic:freebsd15"), "chopin", verdict="FAIL", plan_=now)
        now = w.plan("SticTest2 rel4 plan", rows + [bad], facts=f2)
        self.assertEqual(st(now, "stic:freebsd15"), ("due", "failed"))
        self.assertEqual(st(now, "stic:freebsd15-geli"), ("due", "fanout:B2"))
        self.assertEqual(st(now, "stic:ghostbsd26-zfs"), ("due", "fanout:B2"))

    def test_a_carried_row_keeps_its_passes_plumbing_and_soft_inputs(self):
        """Nothing ran with the new plumbing or host: the next plan must still see the change."""
        w = self.w
        for fa, rule in ((w.facts(round_commits=w.forks["c1"], round_tag="rel5", plumb="p2"), "canary:plumbing"),
                         (w.facts(round_commits=w.forks["c1"], round_tag="rel5", kernel="6.12.116"), "canary:host")):
            ctx = w.ctx(self.r_stic, fa)
            wd = w.word("SticTest2 rel4", ctx)
            p = plan.make(wd, ctx)
            self.assertEqual(len(p.by_status("carried")), 47)
            rows = run.derived_rows(p, "t2s-20261109T0000-abcd", wd, ctx, {"cells": [], "stops": []}, self.r_stic)
            self.assertEqual(len(rows), 47)
            now = w.plan("SticTest2 rel4 plan", self.r_stic + rows, facts=fa)
            self.assertEqual(sum(1 for c in now.due() if c.rule == rule), 9, rule)

    def test_no_panel_carry_until_the_panel_passes(self):
        w = self.w
        fa = w.facts(rustic_tag="bko2")
        ctx = w.ctx(self.r_holt, fa)
        wd = w.word("HoltTest2", ctx)
        p = plan.make(wd, ctx)
        panel = [c for c in p.due() if c.rule == "holt:rustic"]
        self.assertEqual(len(panel), 8)
        rows = {c.cell: w.row(c, next(iter(c.res)), plan_=p) for c in panel}
        om = p.cell("holt:omarchy")
        rows["holt:omarchy"] = w.row(om, next(iter(om.res)), verdict="FAIL", plan_=p)

        def derive(rows):
            cells = [{"cell": k, "rows": [r["id"]], "final_row": r["id"], "outcome": "ran", "verdict": r["verdict"]}
                     for k, r in rows.items()]
            notes = []
            out = run.derived_rows(p, "t2h-20261109T0000-abcf", wd, ctx, {"cells": cells, "stops": []},
                                   self.r_holt + list(rows.values()), notes=notes)
            return [r for r in out if r["kind"] == "carried"], notes

        got, notes = derive(rows)
        self.assertEqual(got, [])
        self.assertTrue(any("the panel didn't pass (holt:omarchy)" in n for n in notes), notes)
        rows["holt:omarchy"] = w.row(om, next(iter(om.res)), plan_=p)
        got, _ = derive(rows)
        self.assertEqual(len(got), 64)

    def test_shared_helper_one_representative(self):
        self.w.edit("testvm/stictest1/freebsd15/extra.sh", "# shared, changed\n")
        p = self.w.plan("SticTest2 rel4 plan", self.r_stic)
        self.assertEqual(st(p, "stic:freebsd15"), ("due", "class:B2"))
        self.assertEqual(st(p, "stic:freebsd15-geli")[0], "class-pending")
        self.assertEqual(st(p, "stic:ghostbsd26-zfs")[0], "class-pending")

    def test_own_checks_changed_all_run(self):
        for g in ("freebsd15", "freebsd15-geli", "ghostbsd26-zfs"):
            self.w.edit(f"testvm/stictest1/{g}/check.sh", self.w.read(f"testvm/stictest1/{g}/check.sh") + "# edited\n")
        p = self.w.plan("SticTest2 rel4 plan", self.r_stic)
        for g in ("freebsd15", "freebsd15-geli", "ghostbsd26-zfs"):
            self.assertEqual(st(p, f"stic:{g}"), ("due", "changed:harness"), g)

    def test_shared_binary_plus_one_own_file(self):
        rc = dict(self.w.forks["c0"], restic=self.w.forks["c3"]["restic"])
        f = self.w.facts(round_commits=rc)
        for h in f:
            f[h]["sets"]["roundrel4"] = self.w.binset("rel4", rc, only={"freebsd/restic-bko": "rel4b"})
        self.w.edit("testvm/stictest1/freebsd15-geli/inner.sh", "# geli's own, changed\n")
        p = self.w.plan("SticTest2 rel4 plan", self.r_stic, facts=f)
        self.assertEqual(st(p, "stic:freebsd15-geli"), ("due", "changed:bin+harness"))
        self.assertEqual(st(p, "stic:freebsd15"), ("due", "class:B2"))
        self.assertEqual(st(p, "stic:ghostbsd26-zfs"), ("class-pending", "class:B2"))

    def test_linux_never_reduced(self):
        self.w.edit("testvm/compare/fsmeta.py", "# changed\n")
        p = self.w.plan("SticTest2 rel4 plan", self.r_stic)
        linux = [c for c in p.cells if c.cell[5:] not in world.STIC_OS]
        self.assertTrue(all(c.status == "due" and c.rule == "changed:harness" for c in linux))

    def test_derived_class_and_carried_rows(self):
        f1 = self.w.facts(round_commits=self.w.forks["c1"], round_tag="rel5")
        p1 = self.w.plan("SticTest2 rel4 plan", facts=f1)
        rows = self.w.seed(p1, age={"stic:freebsd15": 48})
        f2 = self.w.facts(round_commits=self.w.forks["c2"], round_tag="rel6")
        ctx = self.w.ctx(rows, facts=f2)
        wd = self.w.word("SticTest2 rel4", ctx)
        p = plan.make(wd, ctx)
        rep_row = self.w.row(p.cell("stic:freebsd15"), "chopin", plan_=p, needs={"seeded": "PASS", "x": "PASS"})
        result = {"cells": [{"cell": "stic:freebsd15", "verdict": "PASS", "rows": [rep_row["id"]], "attempt": 1}]}
        out = run.derived_rows(p, "t2s-20261108T0500-abcd", wd, ctx, result, rows + [rep_row])
        kinds = collections.Counter(r["kind"] for r in out)
        self.assertEqual(kinds["class"], 2)
        self.assertEqual(kinds["carried"], len(p.by_status("carried")))
        for r in out:
            self.assertEqual(ledger.validate(r), [], r["cell"])
        cr = [r for r in out if r["kind"] == "class"][0]
        self.assertEqual((cr["via"], cr["needs"], cr["key"]), (rep_row["id"], {"seeded": "PASS", "x": "PASS"},
                                                              p.cell(cr["cell"]).key))
        # they make the members current on the next plan
        p2 = self.w.plan("SticTest2 rel4 plan", rows + [rep_row] + out, facts=f2)
        self.assertEqual(st(p2, "stic:freebsd15-geli"), ("current", None))
        self.assertEqual(st(p2, "stic:debian13"), ("current", None))
        # a FAIL writes no class row
        result = {"cells": [{"cell": "stic:freebsd15", "verdict": "FAIL", "rows": [rep_row["id"]], "attempt": 1}]}
        out = run.derived_rows(p, "t2s-20261108T0500-abcd", wd, ctx, result, rows + [rep_row])
        self.assertFalse([r for r in out if r["kind"] == "class"])


class Unbuilt(WorldCase):
    def test_default_round_not_built(self):
        self.w.set_heads("c1")
        p = self.w.plan("SticTest2 plan", self.r_stic)
        self.assertTrue(p.set["pending"])
        self.assertTrue(p.set["round"].startswith("t2r-"))
        self.assertTrue(any("isn't built" in n for n in p.notes))
        # rel4's PASSes ran c0; the heads are a version bump: carried, by source diff
        self.assertEqual(by(p), {("carried", "carry:version-only"): 47})
        # heads equal to the last PASS's commits: the binaries are predicted unchanged
        self.w.set_heads("c0")
        f = self.w.facts()
        for h in f:
            f[h]["rounds"] = {}
        p = self.w.plan("SticTest2 plan", self.r_stic, facts=f)
        self.assertTrue(p.set["pending"])
        self.assertEqual(by(p), {("current", None): 47})
        self.assertIn("not built: assumed reused", "; ".join(p.cell("stic:debian13").reasons))


class Estimates(unittest.TestCase):
    def test_history_tsv_readme_default(self):
        rows = {"stic:a": [{"kind": "run", "verdict": "PASS", "valid": True, "gm": g, "wall_s": w, "waits_s": 10,
                            "wear": {"t": int(b * 1e9)}} for g, w, b in ((1.0, 70, 0.1), (2.0, 130, 0.2),
                                                                         (3.0, 190, 0.3), (9.0, 600, 9.0))],
                "stic:b": [{"kind": "run", "verdict": "FAIL", "valid": True, "gm": 50.0, "wall_s": 3000}]}
        tsv = {"stic:b": {"gm": 4.0, "wall_s": 240.0, "gb": 0.0}, "stic:a": {"gm": 7.0, "wall_s": 1.0, "gb": 1.0}}
        e = estimates.Estimator(rows, tsv, {"c": 120})
        self.assertEqual(e.cell("stic:a"), {"gm": 3.0, "wall_s": 180, "gb": 0.3, "src": "history:3"})
        self.assertEqual(e.cell("stic:b"), {"gm": 4.0, "wall_s": 240, "gb": 0.0, "src": "tsv"})
        self.assertEqual(e.cell("stic:c"), {"gm": 2.0, "wall_s": 120, "gb": 0.0, "src": "README"})
        self.assertEqual(e.cell("holt:z"), {"gm": 5.0, "wall_s": 300, "gb": 0.0, "src": "default"})

    def test_tsv_is_seeded_from_the_design_data(self):
        """estimates.tsv's stic rows are st1_marks.tsv's medians, holt's r3all's sec (R2.8)."""
        data = os.path.join(REPO, "docs", "design", "test2", "data")
        if not os.path.isdir(data):
            self.skipTest("no docs/design/test2/data")
        tsv = estimates.load_tsv(os.path.join(REPO, "testvm", "test2", "estimates.tsv"))
        runs = collections.defaultdict(list)
        with open(os.path.join(data, "st1_marks.tsv")) as f:
            for line in f:
                h, g, mark, s, e, x = line.rstrip("\n").split("\t")
                if x == "0" and s and e:
                    runs[g].append(int(e) - int(s))
        for g, d in runs.items():
            self.assertAlmostEqual(tsv[f"stic:{g}"]["gm"], round(statistics.median(d) / 60, 2), places=2)
        with open(os.path.join(data, "ht_rows.json")) as f:
            r3 = {r["guest"]: r for r in json.load(f) if r["run"] == "r3all"}
        for g, r in r3.items():
            self.assertAlmostEqual(tsv[f"holt:{g}"]["gm"], round(r["sec"] / 60, 2), places=2)
        fam = collections.defaultdict(float)
        for c, e in tsv.items():
            if not c.endswith("+img") and c not in ("holt:endeavouros", "holt:endeavouros-grub", "holt:manjaro",
                                                   "holt:ubuntu26-luks", "holt:chimera", "holt:kinoite44",
                                                   "holt:chimera-uefi", "holt:chimera-zfs", "holt:gentoo", "holt:gentoo-systemd",
                                                   "holt:cachyos-zfs", "holt:anduinos2", "holt:neon24", "holt:garuda", "holt:biglinux",
                                                   "holt:oi2610", "holt:oi2610-bios", "stic:oi2610",
                                                   "holt:freebsd15-geli"):
                fam[c.split(":")[0]] += e["gm"]     # (test2.md's figures are of the 45 sources)
        self.assertAlmostEqual(fam["stic"], 281.9, delta=0.1)     # test2.md: SticTest2All 282 gm
        self.assertAlmostEqual(fam["holt"], 246.7, delta=0.5)     # test2.md: HoltTest2All ~247 gm
        self.assertEqual(sum(1 for c in tsv if c.startswith("holt:") and not c.endswith("+img")), 70)
        self.assertEqual(sum(1 for c in tsv if c.startswith("gate:")), 13)

    def test_pack_and_gate_wall(self):
        jobs = [("a", 600, 20, None), ("b", 600, 20, None), ("c", 300, 2, None), ("d", 300, 2, ["h2"])]
        wall, place, left = estimates.pack(jobs, ["h1", "h2"], slots=3, big=13)
        self.assertEqual((wall, left), (600, []))
        self.assertEqual(place["d"], "h2")
        self.assertNotEqual(place["a"], place["b"])      # two big guests: one per host at a time
        wall, place, left = estimates.pack(jobs[:2], ["h1"], slots=3, big=13)
        self.assertEqual(wall, 1200)
        est = {"x": {"wall_s": 100}, "y": {"wall_s": 50}, "s1": {"wall_s": 900}, "s2": {"wall_s": 700}}
        self.assertEqual(estimates.gate_wall(["x", "y", "s1", "s2"], est, {"s1": "slow", "s2": "slow"},
                                             {"s1": "h1", "s2": "h2"}), 150 + 900)


class Performance(unittest.TestCase):
    """U2.6: a plan over every cell (47 stic, 60 holt with 9 variants, 13 gate) and 10,000 rows
    in under 2 s, without ssh."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t2p2-perf-")
        cls.w = world.World(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_cell_and_10000_rows(self):
        rows = []
        for word in ("SticTest2 rel4 plan", "HoltTest2 plan", "GateTest2 plan"):
            rows += self.w.seed(self.w.plan(word))
        cells = sorted({r["cell"] for r in rows})
        self.assertEqual(len(cells), 47 + 70 + 13)
        filler = []
        i = 0
        while len(rows) + len(filler) < 10000:
            r = dict(rows[i % len(rows)])
            r["id"] = f"{r['host']}-20261101T000000Z-{i:08x}"
            r["origin"] = f"filler:{i}"
            r["t"] = "2026-11-01T00:00:00Z"
            r["verdict"], r["class"] = ("FAIL", "fail") if i % 3 else ("HARNESS", "harness")
            r["key"] = "0" * 64 if i % 2 else r["key"]
            r.pop("c")
            filler.append(ledger.complete(r))
            i += 1
        allrows = rows + filler
        self.assertEqual(len(allrows), 10000)
        t0 = time.time()
        for word in ("SticTest2 rel4 plan", "HoltTest2 plan", "GateTest2 plan"):
            p = self.w.plan(word, allrows)
            p.render()
            p.to_json()
        took = time.time() - t0
        self.assertLess(took, 2.0, f"three plans took {took:.2f} s")

    def test_rows_at_many_commits(self):
        """The current PASS rows at 12 distinct commits (cells that stopped rerunning keep old
        ones): R5's unmapped check hashes only watched paths no spec names, per commit."""
        base = self.w.head()
        try:
            commits = []
            for i in range(12):
                self.w.edit(f"testvm/stictest1/notes-{i}.md", f"note {i}\n", commit=True)
                self.w.edit(f"holt/src/doc-{i}.md", f"doc {i}\n", commit=True)
                commits.append(self.w.head())
            rows = []
            for word in ("SticTest2 rel4 plan", "HoltTest2 plan", "GateTest2 plan"):
                for i, r in enumerate(self.w.seed(self.w.plan(word))):
                    r = dict(r, commit=commits[i % len(commits)])
                    r.pop("c")
                    rows.append(ledger.complete(r))
            t0 = time.time()
            for word in ("SticTest2 rel4 plan", "HoltTest2 plan", "GateTest2 plan"):
                p = self.w.plan(word, rows)
                self.assertEqual(p.totals.get("due", 0), 0, word)
            took = time.time() - t0
            self.assertLess(took, 2.0, f"three nothing-due plans over 12 commits took {took:.2f} s")
        finally:
            self.w.reset(base)


class GuestCase(WorldCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.p_guest = cls.w.plan("GuestTest2 plan")
        cls.r_guest = cls.w.seed(cls.p_guest, extra={"try_ran": True},
                                 needs={"identity": "PASS", "try": "PASS", "logs": "PASS"})


class Guests(GuestCase):
    """GuestTest2's planner arm (contract 1.7)."""

    def plan(self, rows=None, text="GuestTest2 plan", **kw):
        return self.w.plan(text, self.r_guest if rows is None else rows, **kw)

    def aged(self, cell, days):
        """The seed with CELL's row DAYS old (now is T0 + 8 days; a row's age_h is before T0 + 7)."""
        out = [r for r in self.r_guest if r["cell"] != cell]
        return out + self.w.seed(_only(self.p_guest, cell), age={cell: (days - 1) * 24}, extra={"try_ran": True},
                                 needs={"identity": "PASS"})

    def test_new_guests_sixty_four_cells(self):
        self.assertEqual(self.p_guest.totals["cells"], 64)
        self.assertEqual(by(self.p_guest), {("due", "new"): 64})
        self.assertNotIn("freebsd15-builder", {c.cell[6:] for c in self.p_guest.cells})

    def test_nothing_due_after_a_pass(self):
        p = self.plan()
        self.assertEqual(p.totals["due"], 0)
        self.assertEqual(p.totals["counts"]["current"], 64)

    def test_not_in_any_spec(self):
        f = self.w.facts()
        for h in f:
            f[h]["guests"]["newguest"] = {"generation": "a" * 64, "files": {}}
        self.assertIn("not in any spec: newguest", self.plan(facts=f).notes)

    def test_seven_days(self):
        p = self.plan(self.aged("guest:arch", 6.9))
        self.assertEqual(st(p, "guest:arch"), ("current", None))
        p = self.plan(self.aged("guest:arch", 7.1))
        self.assertEqual(st(p, "guest:arch"), ("due", "guest:7day"))
        self.assertEqual(p.totals["due"], 1)

    def test_seven_days_only_where_the_spec_has_try(self):
        p = self.plan(self.aged("guest:haiku6", 30))
        self.assertEqual(st(p, "guest:haiku6"), ("current", None))

    def test_seven_days_counts_any_valid_try_row_uncommitted_too(self):
        rows = self.aged("guest:arch", 8)
        pc = self.p_guest.cell("guest:arch")
        h = sorted(pc.res)[0]
        rows.append(self.w.row(pc, h, verdict="FAIL", age_h=1, committed=False, dirty={"testvm/x": None},
                               needs={"try": "FAIL"}, extra={"try_ran": True, "to_class_only": True}))
        p = self.plan(rows)
        self.assertNotEqual(st(p, "guest:arch"), ("due", "guest:7day"))

    def test_to_class_is_not_due_and_fails_the_exit(self):
        pc = self.p_guest.cell("guest:arch")
        h = sorted(pc.res)[0]
        row = self.w.row(pc, h, verdict="FAIL", age_h=1, needs={"logs": "DIFF"},
                         extra={"to_class_only": True, "try_ran": True})
        p = self.plan(self.r_guest + [row])
        self.assertEqual(st(p, "guest:arch"), ("to-class", None))
        self.assertEqual(p.totals["due"], 0)
        self.assertEqual(run.plan_exit(p, 0), 1)
        self.assertIn("to-class: guest:arch", p.render())
        self.assertIn("1 to-class", p.render())

    def test_an_old_to_class_cell_is_due_at_seven_days(self):
        rows = self.aged("guest:arch", 8)
        pc = self.p_guest.cell("guest:arch")
        h = sorted(pc.res)[0]
        rows.append(self.w.row(pc, h, verdict="FAIL", age_h=170, needs={"logs": "DIFF"}, klass="fail",
                               extra={"to_class_only": True, "try_ran": True}))
        p = self.plan(rows)
        self.assertEqual(st(p, "guest:arch"), ("due", "guest:7day"))   # its newest --try row is 8 days old

    def test_qemu_moved_on_one_host(self):
        f = self.w.facts(qemu={"chopin": "QEMU 10.0.3"})
        p = self.plan(facts=f)
        pc = p.cell("guest:debian13")
        self.assertEqual((pc.status, pc.rule), ("due", "changed:host"))
        self.assertEqual(pc.host, "chopin")
        self.assertEqual(p.totals["due"], 64)

    def test_one_section_selects_only_its_guests(self):
        text = self.w.read("testvm/update/in-guest.sh").replace("update with moss", "update with moss, changed")
        self.w.edit("testvm/update/in-guest.sh", text, commit=True)
        p = self.plan()
        self.assertEqual({c.cell for c in p.due()}, {"guest:aerynos"})
        self.assertEqual(st(p, "guest:aerynos"), ("due", "changed:harness"))
        self.assertIn("in-guest.sh#Linux.moss", "; ".join(p.cell("guest:aerynos").reasons))

    def test_a_prologue_change_selects_every_ssh_guest(self):
        text = self.w.read("testvm/update/in-guest.sh").replace("say hello", "say hello there")
        self.w.edit("testvm/update/in-guest.sh", text, commit=True)
        p = self.plan()
        self.assertGreater(len(p.due()), 40)
        self.assertEqual(st(p, "guest:redox")[0], "current")

    def test_a_new_generation_is_due(self):
        f = self.w.facts(gens={"arch": 2})
        p = self.plan(facts=f)
        self.assertEqual(st(p, "guest:arch"), ("due", "changed:guest"))
        self.assertEqual(p.totals["due"], 1)

    def test_a_merged_row_is_not_a_pass(self):
        pc = self.p_guest.cell("guest:arch")
        h = sorted(pc.res)[0]
        rows = [r for r in self.r_guest if r["cell"] != "guest:arch"]
        merged = self.w.row(pc, h, age_h=1, needs={"try": "PASS"}, extra={"merged": True, "try_ran": False})
        p = self.plan(rows + [merged])
        self.assertEqual(st(p, "guest:arch"), ("due", "new"))
        # and the run that made it passes
        rr = {"cells": [{"cell": "guest:arch", "rows": [merged["id"]], "verdict": "PASS"}]}
        self.assertEqual(run.outcome_exit(_only(p, "guest:arch"), rr, [merged]), (0, []))

    def class_rows(self, days):
        """A to-class --try DAYS old, and the class row that accepted its lines just now."""
        pc = self.p_guest.cell("guest:arch")
        h = sorted(pc.res)[0]
        bad = self.w.row(pc, h, verdict="FAIL", age_h=(days - 1) * 24, needs={"logs": "DIFF"},
                         extra={"to_class_only": True, "try_ran": True})
        ok = self.w.row(pc, h, verdict="PASS", kind="class", via=bad["id"], rule="guest:lines-classed", age_h=0.5,
                        needs={"logs": "PASS"}, extra={"try_ran": True})
        return [bad, ok]

    def test_accepting_lines_does_not_restart_the_seven_days(self):
        rows = self.aged("guest:arch", 30) + self.class_rows(10)
        self.assertEqual(st(self.plan(rows), "guest:arch"), ("due", "guest:7day"))
        self.assertEqual(st(self.plan(rows, text="GuestTest2 update plan"), "guest:arch"), ("not-runnable", None))

    def test_a_recent_try_whose_lines_were_accepted_counts(self):
        rows = self.aged("guest:arch", 30) + self.class_rows(2)
        self.assertEqual(st(self.plan(rows), "guest:arch"), ("current", None))
        self.assertEqual(st(self.plan(rows, text="GuestTest2 update plan"), "guest:arch"), ("due", "fresh:update"))

    def test_a_to_class_fail_does_not_hide_a_real_fail_at_the_other_key(self):
        f = self.w.facts(qemu={"chopin": "QEMU 10.0.3"})
        pc = self.plan(facts=f).cell("guest:arch")
        hosts = sorted(pc.keys, key=lambda h: pc.keys[h])
        self.assertEqual(len(hosts), 2)
        for classed, real in (hosts, reversed(hosts)):
            rows = [self.w.row(pc, classed, verdict="FAIL", age_h=2, needs={"logs": "DIFF"},
                               extra={"to_class_only": True, "try_ran": True}),
                    self.w.row(pc, real, verdict="FAIL", age_h=2, needs={"try": "FAIL"}, extra={"try_ran": True})]
            self.assertEqual(st(self.plan(rows, facts=f), "guest:arch"), ("due", "failed"), (classed, real))

    def test_to_class_on_every_failing_key_is_to_class(self):
        f = self.w.facts(qemu={"chopin": "QEMU 10.0.3"})
        pc = self.plan(facts=f).cell("guest:arch")
        rows = [self.w.row(pc, h, verdict="FAIL", age_h=2, needs={"logs": "DIFF"},
                           extra={"to_class_only": True, "try_ran": True}) for h in sorted(pc.keys)]
        self.assertEqual(st(self.plan(rows, facts=f), "guest:arch"), ("to-class", None))

    def test_bare_update_with_no_recent_try_is_refused_naming_each_guest(self):
        p = self.plan([], text="GuestTest2 update plan")
        self.assertEqual(p.totals["due"], 0)
        self.assertEqual(run.plan_exit(p, 0), 2)
        text = p.render()
        for c in p.by_status("not-runnable"):
            self.assertIn(f"t2 GuestTest2 {c.cell[6:]} first", text)

    def test_a_section_with_no_marker_is_a_plan_note(self):
        text = self.w.read("testvm/update/in-guest.sh").replace("# t2-section: Linux.apk\n", "# t2-section: Linux.apkX\n")
        self.w.edit("testvm/update/in-guest.sh", text)
        p = self.plan([], text="GuestTest2 alpine324 plan")
        self.assertTrue(any(n.startswith("no section Linux.apk in testvm/update/in-guest.sh") for n in p.notes), p.notes)
        self.assertIn("no section Linux.apk", p.render())
        rows = self.w.seed(self.plan([]), extra={"try_ran": True}, needs={"identity": "PASS"})
        q = self.plan(rows)
        self.assertEqual(q.totals["due"], 0)
        self.assertIn("no section Linux.apk", q.render())       # the nothing-due form too

    def test_a_disk_the_record_never_saw_differs(self):
        f = self.w.facts()
        for h in f:
            g = f[h]["guests"]["debian12"]
            g["disks"] = [{"path": "/x/debian12.qcow2", "size": 1, "mtime_ns": 1, "inode": 1},
                          {"path": "/x/extra.qcow2", "size": 9, "mtime_ns": 9, "inode": 9}]
            g["stat"] = {"generation": g["generation"], "disks": {"debian12.qcow2": {"size": 1, "mtime_ns": 1, "inode": 1}}}
        p = self.plan([], text="GuestTest2 debian12 plan", facts=f)
        self.assertIn("disk differs from its merge record: debian12", p.notes)

    def test_bare_update_needs_a_recent_try(self):
        p = self.plan(text="GuestTest2 update plan")
        self.assertEqual(st(p, "guest:arch"), ("due", "fresh:update"))
        names = {c.cell for c in p.cells}
        self.assertNotIn("guest:redox", names)
        self.assertNotIn("guest:haiku6", names)
        self.assertTrue(any(n.startswith("not in a bare update round") for n in p.notes))
        p = self.plan(self.aged("guest:arch", 9), text="GuestTest2 update plan")
        self.assertEqual(st(p, "guest:arch"), ("not-runnable", None))
        self.assertIn("t2 GuestTest2 arch", p.cell("guest:arch").reasons[0])

    def test_named_update(self):
        p = self.plan(text="GuestTest2 update haiku6 plan")
        self.assertEqual(st(p, "guest:haiku6"), ("due", "fresh:update"))
        p = self.plan(text="GuestTest2 update redox plan")
        self.assertEqual(st(p, "guest:redox")[0], "not-runnable")

    def test_named_and_all(self):
        p = self.plan(text="GuestTest2 arch plan")
        self.assertEqual(st(p, "guest:arch"), ("due", "fresh:names"))
        self.assertEqual(len(p.cells), 1)
        p = self.plan(text="GuestTest2All plan")
        self.assertEqual(by(p), {("due", "fresh:All"): 64})

    def test_sweep_child(self):
        ctx = self.w.ctx(self.r_guest)
        w = self.w.word("GuestTest2All plan", ctx)
        w.sweep = True
        p = plan.make(w, ctx)
        self.assertEqual(by(p), {("due", "fresh:Sweep"): 64})

    def test_a_disk_changed_behind_its_generation(self):
        f = self.w.facts()
        g = f["chopin"]["guests"]["arch"]
        g["disks"] = [{"path": "/v/arch/arch.qcow2", "size": 9, "mtime_ns": 9, "inode": 9}]
        g["stat"] = {"generation": g["generation"], "disks": {"arch.qcow2": {"size": 9, "mtime_ns": 8, "inode": 9}}}
        self.assertIn("disk differs from its merge record: arch", self.plan(facts=f).notes)
        g["stat"]["disks"]["arch.qcow2"]["mtime_ns"] = 9
        self.assertFalse(any("disk differs" in n for n in self.plan(facts=f).notes))


def _only(p, cell):
    q = plan.Plan(p.word, p.family)
    q.cells = [p.cell(cell)]
    q.totals = {"due": 1}
    return q


class Fingerprints(WorldCase):
    def facts(self, gen=1, **kw):
        return self.w.facts(gens={"debian12": gen}, fps={"debian12": self.w.fp(gen, **kw)})

    def seed(self, word, facts):
        return self.w.seed(self.w.plan(word, facts=facts))

    def test_generation_then_fingerprint_carries_and_the_next_plan_is_current(self):
        for word, rows, rule in (("HoltTest2", self.r_holt, "carry:fingerprint"), ("SticTest2 rel4", self.r_stic, "carry:fingerprint")):
            cell = "holt:debian12" if word == "HoltTest2" else "stic:debian12"
            f = self.facts()
            ctx = self.w.ctx(rows, facts=f)
            wd = self.w.word(word + " plan", ctx)
            p = plan.make(wd, ctx)
            self.assertEqual(st(p, cell), ("carried", rule), word)
            self.assertEqual(p.totals["due"], 0)
            notes = []
            der = run.derived_rows(p, "t2x-fixture", wd, ctx, rows=rows, notes=notes)
            self.assertEqual([r["cell"] for r in der], [cell], notes)
            self.assertIn("debian12:generation", der[0]["inputs"]["soft"])
            q = self.w.plan(word + " plan", rows + der, facts=f)
            self.assertEqual(st(q, cell), ("current", None), word)

    def test_a_resumed_carry_takes_the_new_soft_inputs(self):
        rows = self.r_holt
        f = self.facts()
        ctx = self.w.ctx(rows, facts=f)
        wd = self.w.word("HoltTest2 plan", ctx)
        fresh = plan.make(wd, ctx)
        saved = run.plan_from_json(json.loads(json.dumps(fresh.to_json())), wd)
        run.adopt_inputs(saved, fresh)
        der = run.derived_rows(saved, "t2x-fixture", wd, ctx, rows=rows)
        d = [r for r in der if r["cell"] == "holt:debian12"]
        self.assertIn("debian12:generation", d[0]["inputs"]["soft"])
        self.assertEqual(st(self.w.plan("HoltTest2 plan", rows + der, facts=f), "holt:debian12"), ("current", None))

    def test_a_changed_fingerprint_with_the_old_one_in_was(self):
        f1 = self.facts(holt="h1")
        rows = self.seed("HoltTest2 plan", f1)
        f2 = self.facts(holt="h2", holt_was=("h1",))
        ctx = self.w.ctx(rows, facts=f2)
        wd = self.w.word("HoltTest2 plan", ctx)
        p = plan.make(wd, ctx)
        self.assertEqual(st(p, "holt:debian12"), ("carried", "carry:fingerprint"))
        der = run.derived_rows(p, "t2x-fixture", wd, ctx, rows=rows)
        q = self.w.plan("HoltTest2 plan", rows + der, facts=f2)
        self.assertEqual(st(q, "holt:debian12"), ("current", None))

    def test_a_changed_fingerprint_is_due(self):
        rows = self.seed("HoltTest2 plan", self.facts(holt="h1"))
        p = self.w.plan("HoltTest2 plan", rows, facts=self.facts(holt="h2"))
        self.assertEqual(st(p, "holt:debian12"), ("due", "holt:guest-update"))

    def test_a_new_generation_with_the_same_fingerprint_is_soft_stale_and_no_canary(self):
        rows = self.seed("SticTest2 rel4 plan", self.facts(1))
        p = self.w.plan("SticTest2 rel4 plan", rows, facts=self.facts(2))
        c = p.cell("stic:debian12")       # debian12 is a stic canary
        self.assertEqual((c.status, c.rule), ("soft-stale", None))
        self.assertIn("debian12 soft inputs changed", c.reasons[0])
        self.assertEqual(p.totals["due"], 0)

    def test_a_fine_move_is_soft_stale(self):
        rows = self.seed("SticTest2 rel4 plan", self.facts(1, fine="f1"))
        p = self.w.plan("SticTest2 rel4 plan", rows, facts=self.facts(1, fine="f2"))
        self.assertEqual(st(p, "stic:debian12"), ("soft-stale", None))

    def test_a_host_soft_change_still_runs_the_canary(self):
        p = self.w.plan("SticTest2 rel4 plan", self.r_stic, facts=self.w.facts(kernel="6.12.200"))
        self.assertEqual(st(p, "stic:debian12"), ("due", "canary:host"))

    def test_rules_changed_is_noted(self):
        self.w.edit("testvm/update/fingerprint.py", "# fixture\n", commit=True)
        p = self.w.plan("HoltTest2 plan", facts=self.facts(rules="old"))
        self.assertIn("fingerprint rules changed: run t2 guest-fingerprints --reproject", p.notes)


class ImgCase(WorldCase):
    def bin_row(self, cell, name, val, verdict="PASS", kind="run", rule=None, via=None, age_h=1.0, committed=True, dirty=None):
        p = self.p_stic if cell.startswith("stic:") else self.p_holt
        pc = p.cell(cell)
        h = sorted(pc.res)[0]
        inp = copy.deepcopy(pc.res[h].inputs)
        inp["bin"][name] = val
        return self.w.row(pc, h, inputs=inp, verdict=verdict, kind=kind, rule=rule, via=via, age_h=age_h, plan_=p,
                          committed=committed, dirty=dirty, klass=None)

    def good(self, plat="netbsd", guest=None, images=None, skip=()):
        """Rows that give every binary of PLAT's image a real PASS, less SKIP."""
        im = (images or self.w.images())[plat]
        os_ = "omnios" if plat == "illumos" else plat
        g = guest or {"netbsd": "netbsd11", "illumos": "omnios58", "haiku": "haiku6"}.get(plat, "debian12")
        code = "code:" + keys.holt_code(keys.GitSource(self.w.repo, im["holt_commit"], scope=("holt",)))
        rows = [self.bin_row(f"stic:{g}", f"{os_}/{n}", "sha256:" + h) for n, h in im["bins"].items()
                if n != "bko-holt" and n not in skip]
        rows.append(self.bin_row(f"holt:{g}", f"{plat}/bko-holt", code))
        return rows

    def plan_img(self, rows=(), text="ImgTest2 netbsd plan", **kw):
        return self.w.plan(text, rows, **kw)


class ImgRefusal(ImgCase):
    def test_empty_ledger_refuses_every_image_naming_binaries(self):
        p = self.plan_img(text="ImgTest2 plan")
        self.assertEqual(len(p.cells), 7)
        self.assertEqual(by(p), {("not-runnable", None): 7})
        self.assertTrue(all(c.refused for c in p.cells))
        why = "; ".join(p.cell("img:netbsd").reasons)
        for n in ("restic-bko", "rustic-bko", "bko-holt"):
            self.assertIn("netbsd/" + n, why)
        self.assertIn("t2 ", why)
        self.assertEqual(run.plan_exit(p, 0), 2)

    def test_real_passes_at_the_digests(self):
        p = self.plan_img(self.good())
        self.assertEqual(st(p, "img:netbsd"), ("due", "fresh:names"))
        self.assertEqual({r["state"] for r in p.cell("img:netbsd").binaries.values()}, {"real"})

    def test_bare_runs_its_due_image(self):
        p = self.plan_img(self.good(), text="ImgTest2 plan")
        self.assertEqual(st(p, "img:netbsd"), ("due", "img:new"))
        self.assertEqual(st(p, "img:linux")[0], "not-runnable")
        self.assertEqual(run.plan_exit(p, 0), 2)

    def test_gate_rows_do_not_count(self):
        im = self.w.images()["netbsd"]
        rows = [r for r in self.good() if r["cell"].startswith("holt:")]
        g = self.w.plan("GateTest2 plan")
        pc = g.cell("gate:bin:version")
        inp = copy.deepcopy(pc.res["chopin"].inputs)
        inp["bin"]["netbsd/restic-bko"] = "sha256:" + im["bins"]["restic-bko"]
        inp["bin"]["netbsd/rustic-bko"] = "sha256:" + im["bins"]["rustic-bko"]
        p = self.plan_img(rows + [self.w.row(pc, "chopin", inputs=inp, plan_=g)])
        self.assertEqual(st(p, "img:netbsd")[0], "not-runnable")

    def test_a_newest_decisive_fail_is_none(self):
        rows = self.good()
        restic = "netbsd/restic-bko"
        val = "sha256:" + self.w.images()["netbsd"]["bins"]["restic-bko"]
        rows.append(self.bin_row("stic:alpinebsd", restic, val, verdict="FAIL", age_h=0.5))
        p = self.plan_img(rows)
        self.assertEqual(st(p, "img:netbsd")[0], "not-runnable")
        self.assertIn("is FAIL", "; ".join(p.cell("img:netbsd").reasons))

    def test_a_pass_after_the_fail_at_that_cell_clears_it(self):
        val = "sha256:" + self.w.images()["netbsd"]["bins"]["restic-bko"]
        rows = [self.bin_row("stic:alpinebsd", "netbsd/restic-bko", val, verdict="FAIL", age_h=5)]
        rows.append(self.bin_row("stic:alpinebsd", "netbsd/restic-bko", val, age_h=1))
        rows += self.good(skip=("restic-bko",))
        p = self.plan_img(rows)
        self.assertEqual(st(p, "img:netbsd")[0], "due", p.cell("img:netbsd").reasons)

    def test_carried_version_only_is_allowed(self):
        val = "sha256:" + self.w.images()["netbsd"]["bins"]["restic-bko"]
        base = self.bin_row("stic:alpinebsd", "netbsd/restic-bko", "sha256:" + "9" * 64, age_h=9)
        link = self.bin_row("stic:netbsd11", "netbsd/restic-bko", val, kind="carried", rule="carry:version-only", via=base["id"])
        rows = self.good(skip=("restic-bko",)) + [base, link]
        p = self.plan_img(rows)
        pc = p.cell("img:netbsd")
        self.assertEqual(pc.status, "due", pc.reasons)
        self.assertEqual(pc.binaries["restic-bko"]["state"], "carried")

    def test_any_other_carried_is_not_runnable_and_names_the_canary(self):
        val = "sha256:" + self.w.images()["netbsd"]["bins"]["restic-bko"]
        base = self.bin_row("stic:alpinebsd", "netbsd/restic-bko", "sha256:" + "9" * 64, age_h=9)
        link = self.bin_row("stic:netbsd11", "netbsd/restic-bko", val, kind="carried", rule="carry:affected-v1", via=base["id"])
        rows = self.good(skip=("restic-bko",)) + [base, link]
        pc = self.plan_img(rows).cell("img:netbsd")
        self.assertEqual(pc.status, "not-runnable")
        self.assertEqual(pc.binaries["restic-bko"]["state"], "carried-other")
        self.assertIn(pc.binaries["restic-bko"]["canary"], ("stic:netbsd11", "stic:alpinebsd"))

    def test_a_via_walk_through_two_links(self):
        val = "sha256:" + self.w.images()["netbsd"]["bins"]["restic-bko"]
        base = self.bin_row("stic:alpinebsd", "netbsd/restic-bko", "sha256:" + "9" * 64, age_h=9)
        mid = self.bin_row("stic:netbsd11", "netbsd/restic-bko", "sha256:" + "8" * 64, kind="carried", rule="carry:version-only",
                           via=base["id"], age_h=5)
        for rule, state in (("carry:version-only", "carried"), ("carry:dfly-image", "carried-other")):
            link = self.bin_row("stic:netbsd11", "netbsd/restic-bko", val, kind="carried", rule=rule, via=mid["id"], age_h=2)
            rows = self.good(skip=("restic-bko",)) + [base, mid, link]
            self.assertEqual(self.plan_img(rows).cell("img:netbsd").binaries["restic-bko"]["state"], state)
        bad = self.bin_row("stic:netbsd11", "netbsd/restic-bko", "sha256:" + "8" * 64, kind="carried", rule="carry:affected-v1",
                           via=base["id"], age_h=5)
        link = self.bin_row("stic:netbsd11", "netbsd/restic-bko", val, kind="carried", rule="carry:version-only", via=bad["id"], age_h=2)
        rows = self.good(skip=("restic-bko",)) + [base, bad, link]
        self.assertEqual(self.plan_img(rows).cell("img:netbsd").binaries["restic-bko"]["state"], "carried-other")

    def test_illumos_maps_to_omnios_for_stic_rows(self):
        rows = self.good("illumos")
        self.assertTrue(any(k.startswith("omnios/") for r in rows for k in r["inputs"]["bin"]))
        p = self.plan_img(rows, text="ImgTest2 illumos plan")
        self.assertEqual(st(p, "img:illumos"), ("due", "fresh:names"))

    def test_bko_holt_needs_its_commit_in_this_repo(self):
        for commit, needle in (("", "no bko-holt.commit"), ("1" * 40, "isn't in this repository")):
            im = self.w.images(commit=commit)
            p = self.plan_img(self.good(), facts=self.w.facts(images=im))
            self.assertEqual(st(p, "img:netbsd")[0], "not-runnable")
            self.assertIn(needle, "; ".join(p.cell("img:netbsd").reasons))

    def test_bko_holt_by_sha256_from_a_holt_bin_row(self):
        im = self.w.images()["netbsd"]
        rows = [r for r in self.good() if not r["cell"].startswith("holt:")]
        rows.append(self.bin_row("holt:netbsd11", "netbsd/bko-holt", "sha256:" + im["bins"]["bko-holt"]))
        self.assertEqual(st(self.plan_img(rows), "img:netbsd")[0], "due")

    def test_an_uncommitted_row_does_not_count(self):
        rows = self.good()
        rows2 = [r for r in rows if not r["cell"].startswith("holt:")]
        val = "code:" + keys.holt_code(keys.GitSource(self.w.repo, self.w.head(), scope=("holt",)))
        rows2.append(self.bin_row("holt:netbsd11", "netbsd/bko-holt", val, committed=False, dirty={"holt/src/main.rs": "a" * 64}))
        self.assertEqual(st(self.plan_img(rows2), "img:netbsd")[0], "not-runnable")

    def test_a_build_under_way(self):
        im = self.w.images()
        im["netbsd"]["done_exit"] = None
        p = self.plan_img(self.good(), facts=self.w.facts(images=im))
        c = p.cell("img:netbsd")
        self.assertEqual(c.status, "not-runnable")
        self.assertIn("done.exit", c.reasons[0])
        self.assertTrue(c.refused)      # named: the word can't do what it says
        c = self.plan_img(self.good(), text="ImgTest2 plan", facts=self.w.facts(images=im)).cell("img:netbsd")
        self.assertEqual(c.status, "not-runnable")
        self.assertFalse(c.refused)     # bare: the build is simply not ready
        c = self.plan_img(self.good(), text="ImgTest2 release plan", facts=self.w.facts(images=im)).cell("img:netbsd")
        self.assertTrue(c.refused)      # release can't vouch for it

    def test_a_named_image_that_cannot_run_exits_2_beside_one_that_can(self):
        im = self.w.images()
        im["linux"]["done_exit"] = None
        p = self.plan_img(self.good(), text="ImgTest2 netbsd linux release plan", facts=self.w.facts(images=im))
        self.assertEqual(st(p, "img:netbsd")[0], "due")
        self.assertEqual(run.plan_exit(p, 0), 2)

    def test_no_commit_names_only_the_rebuild(self):
        im = self.w.images()
        im["netbsd"]["holt_commit"] = None
        p = self.plan_img(self.good(skip=("bko-holt",)), facts=self.w.facts(images=im))
        c = p.cell("img:netbsd")
        why = "; ".join(c.reasons)
        self.assertIn("rebuild", why)
        self.assertNotIn("t2 HoltTest2", why)
        self.assertIsNone(c.binaries["bko-holt"]["canary"])

    def test_a_fail_at_the_digest_names_the_failing_cell(self):
        val = "sha256:" + self.w.images()["netbsd"]["bins"]["restic-bko"]
        bad = self.bin_row("stic:alpinebsd", "netbsd/restic-bko", val, verdict="FAIL", age_h=0.5)
        c = self.plan_img(self.good() + [bad]).cell("img:netbsd")
        self.assertEqual(c.status, "not-runnable")
        self.assertIn("run t2 SticTest2 ROUND alpinebsd", "; ".join(c.reasons))
        self.assertEqual(c.binaries["restic-bko"]["canary"], "stic:alpinebsd")

    def test_a_binary_the_sums_file_omits_is_refused(self):
        im = self.w.images()
        del im["netbsd"]["bins"]["bko-holt"]
        c = self.plan_img(self.good(), facts=self.w.facts(images=im)).cell("img:netbsd")
        self.assertEqual(c.status, "not-runnable")
        why = "; ".join(c.reasons)
        self.assertIn("netbsd/bko-holt", why)
        self.assertIn("bko-bin.sha256", why)
        im = self.w.images()
        im["netbsd"]["bins"] = {}
        c = self.plan_img(facts=self.w.facts(images=im)).cell("img:netbsd")
        self.assertEqual(sorted(c.binaries), ["bko-holt", "restic-bko", "rustic-bko"])

    def test_haiku_lists_no_restic(self):
        self.assertNotIn("restic-bko", self.plan_img(text="ImgTest2 haiku plan").cell("img:haiku").binaries)

    def test_a_failed_boot_with_no_pass_exits_2_on_a_bare_word(self):
        pc = self.w.plan("ImgTest2 plan", self.good()).cell("img:netbsd")
        fail = self.w.row(pc, sorted(pc.res)[0], verdict="FAIL", age_h=2, needs={"boot:bios:x": "FAIL"},
                          extra={"panel": {"result": "none"}})
        val = "sha256:" + self.w.images()["netbsd"]["bins"]["restic-bko"]
        bad = self.bin_row("stic:alpinebsd", "netbsd/restic-bko", val, verdict="FAIL", age_h=0.5)
        p = self.plan_img(self.good() + [fail, bad], text="ImgTest2 plan")
        self.assertEqual(st(p, "img:netbsd")[0], "not-runnable")
        self.assertTrue(p.cell("img:netbsd").refused)
        self.assertEqual(run.plan_exit(p, 0), 2)

    def test_no_current_image(self):
        p = self.plan_img(text="ImgTest2 netbsd plan", facts=self.w.facts(images={}))
        self.assertIn("no current image", p.cell("img:netbsd").reasons[0])


class ImgPanels(ImgCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.p_img = cls.w.plan("ImgTest2 plan")
        cls.good_rows = []
        probe = ImgCase("run")
        probe.w, probe.p_stic, probe.p_holt = cls.w, cls.p_stic, cls.p_holt
        for g in world.PLATS:
            cls.good_rows += probe.good(g)
        cls.p_ok = cls.w.plan("ImgTest2 plan", cls.good_rows)

    def row(self, res, verdict="PASS", age=2.0):
        pc = self.p_ok.cell("img:netbsd")
        h = sorted(pc.res)[0]
        return self.w.row(pc, h, verdict=verdict, age_h=age, needs={"boot:bios:x": "PASS"}, extra={"panel": {"result": res}})

    def plan(self, rows, **kw):
        return self.w.plan("ImgTest2 plan", self.good_rows + rows, **kw)

    def panel_row(self, verdict, rows):
        q = self.w.plan("HoltTest2 netbsd11+img plan", rows)
        c = q.cell("holt:netbsd11+img")
        return self.w.row(c, sorted(c.res)[0], verdict=verdict, age_h=1.5, needs={"stage:restore": verdict}, plan_=q)

    def test_a_panels_own_fail_is_no_refusal(self):
        boot = self.row("owed", age=3.0)
        fail = self.row("fail", verdict="FAIL", age=1.0)
        rows = [boot, fail, self.panel_row("FAIL", self.good_rows + [boot, fail])]
        p = self.plan(rows)
        self.assertEqual(st(p, "img:netbsd"), ("due", "img:panel-owed"))

    def test_a_panels_own_pass_is_no_evidence_for_its_image(self):
        netbsd = [r for r in self.good_rows if not (r["cell"].startswith("holt:netbsd11"))]
        panel = self.panel_row("PASS", netbsd)
        p = self.w.plan("ImgTest2 plan", netbsd + [panel])
        self.assertEqual(st(p, "img:netbsd")[0], "not-runnable")
        self.assertEqual(p.cell("img:netbsd").binaries["bko-holt"]["state"], "none")

    def test_an_owed_panel_whose_binary_went_bad_exits_2(self):
        val = "sha256:" + self.w.images()["netbsd"]["bins"]["restic-bko"]
        bad = self.w.row(self.p_stic.cell("stic:alpinebsd"), sorted(self.p_stic.cell("stic:alpinebsd").res)[0], verdict="FAIL", age_h=0.5,
                         inputs=dict(self.p_stic.cell("stic:alpinebsd").res[sorted(self.p_stic.cell("stic:alpinebsd").res)[0]].inputs,
                                     bin={"netbsd/restic-bko": val}), plan_=self.p_stic, klass=None)
        p = self.plan([self.row("owed"), bad])
        self.assertEqual(st(p, "img:netbsd")[0], "not-runnable")
        self.assertEqual(run.plan_exit(p, 0), 2)

    def test_release_requires_the_panel(self):
        for res in ("none", "blocked"):
            p = self.w.plan("ImgTest2 release plan", self.good_rows + [self.row(res)])
            self.assertEqual(st(p, "img:netbsd"), ("due", "img:panel-owed"), res)
        p = self.w.plan("ImgTest2 release plan", self.good_rows + [self.row("pass")])
        self.assertEqual(st(p, "img:netbsd"), ("current", None))
        p = self.w.plan("ImgTest2 plan", self.good_rows + [self.row("none")])
        self.assertEqual(st(p, "img:netbsd"), ("current", None))
        cl = copy.deepcopy(json.load(open(os.path.join(self.w.repo, "testvm/test2/classes.json"))))
        cl["holt"]["img_panel"]["netbsd"]["blocked"] = "a defect"
        p = self.w.plan("ImgTest2 release plan", self.good_rows + [self.row("blocked")], classes=cl)
        self.assertEqual(st(p, "img:netbsd"), ("due", "img:panel-owed"))    # the runner's release check ends it nonzero
        p = self.w.plan("ImgTest2 release plan", self.good_rows + [self.row("none", verdict="FAIL")])
        self.assertEqual(st(p, "img:netbsd"), ("due", "failed"))

    def test_release_over_a_soft_stale_image_with_no_panel_boots_it(self):
        ok = self.row("none")
        self.w.edit("stick/boot-test.py", "# changed\n", commit=True)
        p = self.w.plan("ImgTest2 release plan", self.good_rows + [ok], universe=["img:netbsd"])
        self.assertEqual(st(p, "img:netbsd"), ("due", "stale"))
        p = self.w.plan("ImgTest2 release plan", self.good_rows + [self.row("pass")], universe=["img:netbsd"])
        self.assertEqual(st(p, "img:netbsd"), ("soft-stale", None))

    def test_owed_then_fail_then_pass(self):
        owed = self.row("owed")
        p = self.plan([owed])
        self.assertEqual(st(p, "img:netbsd"), ("due", "img:panel-owed"))
        fail = self.row("fail", age=1.0)
        self.assertEqual(st(self.plan([owed, fail]), "img:netbsd"), ("due", "img:panel-owed"))
        ok = self.row("pass", age=0.5)
        self.assertEqual(st(self.plan([owed, fail, ok]), "img:netbsd"), ("current", None))

    def test_a_boot_rows_own_panel_row_answers_it_at_any_time_order(self):
        # (a tie at equal times breaks on the row ids, which needn't put the .panel row last)
        owed = self.row("owed", age=1.0)
        ok = dict(self.row("pass", age=2.0), origin=owed["origin"] + ".panel")
        self.assertEqual(st(self.plan([owed, ok]), "img:netbsd"), ("current", None))
        other = dict(ok, origin="img:t2i-other:netbsd:1.panel")
        self.assertEqual(st(self.plan([owed, other]), "img:netbsd"), ("due", "img:panel-owed"))

    def test_a_failed_panel_row_with_a_fail_verdict_stays_panel_owed(self):
        self.assertEqual(st(self.plan([self.row("fail", verdict="FAIL")]), "img:netbsd"), ("due", "img:panel-owed"))

    def test_a_failed_boot_is_failed(self):
        self.assertEqual(st(self.plan([self.row("none", verdict="FAIL")]), "img:netbsd"), ("due", "failed"))

    def test_blocked_then_unblocked(self):
        cl = copy.deepcopy(json.load(open(os.path.join(self.w.repo, "testvm/test2/classes.json"))))
        cl["holt"]["img_panel"]["netbsd"]["blocked"] = "a defect"
        r = self.row("blocked")
        self.assertEqual(st(self.plan([r], classes=cl), "img:netbsd"), ("current", None))
        self.assertEqual(st(self.plan([r]), "img:netbsd"), ("due", "img:panel-owed"))
        q = self.w.plan("HoltTest2 netbsd11+img plan", classes=cl)
        self.assertEqual(st(q, "holt:netbsd11+img")[0], "not-runnable")
        self.assertIn("blocked: a defect", q.cell("holt:netbsd11+img").reasons[0])

    def test_no_pass_is_new_a_harness_change_soft_stale(self):
        self.assertEqual(st(self.p_ok, "img:netbsd"), ("due", "img:new"))
        ok = self.row("none")
        self.assertEqual(st(self.plan([ok]), "img:netbsd"), ("current", None))
        self.w.edit("stick/boot-test.py", "# changed\n", commit=True)
        p = self.plan([ok], universe=["img:netbsd"])
        self.assertEqual(st(p, "img:netbsd"), ("soft-stale", None))

    def test_a_rebuilt_image_is_new(self):
        ok = self.row("none")
        im = self.w.images()
        im["netbsd"]["bins"]["rustic-bko"] = world.H("rebuilt")
        p = self.plan([ok], facts=self.w.facts(images=im))
        self.assertEqual(st(p, "img:netbsd")[0], "not-runnable")   # rebuilt, and no PASS at the new digest
        self.assertTrue(p.cell("img:netbsd").refused)

    def test_an_image_rebuilt_on_one_host(self):
        ok = self.row("none")
        im = self.w.images()
        other = self.w.images(tag="i2")
        other["netbsd"]["bins"] = dict(im["netbsd"]["bins"])
        f = self.w.facts(images=im)
        f["cinqcent"]["images"] = other
        p = self.plan([ok], facts=f)
        self.assertEqual(st(p, "img:netbsd"), ("due", "img:new"))
        self.assertEqual(p.cell("img:netbsd").host, "cinqcent")

    def test_panel_line_in_the_plan(self):
        p = self.plan([])
        self.assertIn("panel: decided after the boot tests' manifests", p.render())
        q = self.w.plan("ImgTest2 netbsd release plan", self.good_rows)
        self.assertIn("panel: 1 cells", q.render())


class ImgCells(WorldCase):
    def test_img_beside_names_and_letters(self):
        p = self.w.plan("HoltTest2 debian12 +img plan")
        self.assertEqual(sorted(c.cell for c in p.due()), ["holt:debian12", "holt:debian12+img"])
        p = self.w.plan("HoltTest2 +img debian12 plan")
        self.assertEqual(sorted(c.cell for c in p.due()), ["holt:debian12", "holt:debian12+img"])
        p = self.w.plan("HoltTest2A +img plan")
        self.assertEqual(st(p, "holt:aerynos+img")[0], "due")
        self.assertEqual([c.cell for c in p.due() if c.cell.endswith("+img")], ["holt:aerynos+img"])

    def test_only_when_asked(self):
        self.assertFalse([c.cell for c in self.w.plan("HoltTest2 plan").cells if c.cell.endswith("+img")])
        self.assertFalse([c.cell for c in self.w.plan("HoltTest2All plan").cells if c.cell.endswith("+img")])
        p = self.w.plan("HoltTest2 debian12+img plan")
        self.assertEqual([c.cell for c in p.cells], ["holt:debian12+img"])
        self.assertEqual(st(p, "holt:debian12+img"), ("due", "fresh:names"))
        p = self.w.plan("HoltTest2 +img plan")
        self.assertEqual(len(p.cells), 14)     # freebsd15-geli joined the FreeBSD panel 2026-11-14 (geli.md D6), oi2610 the illumos one (openindiana.md)
        p = self.w.plan("HoltTest2 plan", universe=["holt:debian12+img"])
        self.assertEqual(st(p, "holt:debian12+img"), ("due", "img:panel"))

    def test_panel_is_due_when_a_hard_group_changes(self):
        p = self.w.plan("HoltTest2 debian12+img plan")
        rows = self.w.seed(p)
        q = self.w.plan("HoltTest2 plan", rows, universe=["holt:debian12+img"])
        self.assertEqual(st(q, "holt:debian12+img"), ("current", None))
        im = self.w.images(tag="i2")
        q = self.w.plan("HoltTest2 plan", rows, facts=self.w.facts(images=im), universe=["holt:debian12+img"])
        self.assertEqual(st(q, "holt:debian12+img"), ("due", "img:panel"))

    def test_no_uefi_filter_but_pins_apply(self):
        p = self.w.plan("HoltTest2 debian13-stack+img plan", facts=self.w.facts(uefi_rescuer=()))
        self.assertEqual(sorted(p.cell("holt:debian13-stack+img").keys), ["chopin", "cinqcent"])
        p = self.w.plan("HoltTest2 dragonfly6+img plan")
        self.assertEqual(sorted(p.cell("holt:dragonfly6+img").keys), ["cinqcent"])

    def test_bins_stale(self):
        im = self.w.images()
        for h in im.values():
            h["pin"]["bins_stale"] = True
        p = self.w.plan("HoltTest2 debian12+img plan", facts=self.w.facts(images=im))
        self.assertEqual(st(p, "holt:debian12+img")[0], "not-runnable")
        self.assertIn("bins-stale", p.cell("holt:debian12+img").reasons[0])


class IdentityBad(WorldCase):
    def test_a_bad_identity_makes_stic_and_holt_cells_not_runnable(self):
        for text, cell in (("SticTest2 rel4 plan", "stic:debian12"), ("HoltTest2 plan", "holt:debian12")):
            p = self.w.plan(text, identity_bad={"debian12": "disk differs"})
            self.assertEqual(st(p, cell)[0], "not-runnable")
            self.assertIn("disk differs", p.cell(cell).reasons[0])
        p = self.w.plan("HoltTest2 plan", identity_bad={"debian13": "x"})
        self.assertEqual(st(p, "holt:debian13+oneshot")[0], "not-runnable")


class ExtraDue(WorldCase):
    def test_canary_carried(self):
        p = self.w.plan("SticTest2 rel4 plan", self.r_stic, extra_due={"stic:debian12": "netbsd's canary"})
        self.assertEqual(st(p, "stic:debian12"), ("due", "canary:carried"))
        self.assertEqual(p.totals["due"], 1)
        self.assertEqual(p.to_json()["options"]["extra_due"], {"stic:debian12": "netbsd's canary"})


class Named(WorldCase):
    def test_guest_and_img_paths_are_named(self):
        ctx = self.w.ctx()
        pl = plan.Planner(self.w.word("GuestTest2 plan", ctx), ctx)
        pl.setup_set()
        named = pl.named_paths()
        for p in ("testvm/update/in-guest.sh", "testvm/test2/guest/job.sh", "stick/netbsd/checks.sh",
                  "testvm/holttest1d/img-boot.py", "testvm/holttest1d/img-boot.d/netbsd.json"):
            self.assertIn(p, named)
        self.assertFalse([p for p in named if "#" in p])


class ClassTable(unittest.TestCase):
    def test_the_real_class_table(self):
        ctx = plan.Ctx(REPO, hosts=[], options={})
        self.assertEqual(plan.check_classes(ctx, holt_names=world.GUESTS), [])
        h = ctx.classes["holt"]
        src = {g for f in h["families"].values() for g in f} | set(h["no_family"])
        self.assertEqual(len(src), 62)
        self.assertEqual(sum(len(v) for v in h["variants"].values()), 9)
        self.assertEqual(sorted(set(world.GUESTS) - src), ["freebsd15-builder", "freebsd15-ufs"])

    def test_check_catches_errors(self):
        ctx = plan.Ctx(REPO, hosts=[], options={})
        cl = copy.deepcopy(ctx.classes)
        cl["holt"]["variants"]["bigger"].append("freebsd15-ufs")
        cl["stic"]["classes"]["B9"] = ["debian13", "nosuch"]
        ctx2 = plan.Ctx(REPO, hosts=[], options={"classes": cl})
        errs = plan.check_classes(ctx2, holt_names=world.GUESTS)
        self.assertTrue(any("bigger variant of freebsd15-ufs" in e for e in errs), errs)
        self.assertTrue(any("no check nosuch" in e for e in errs), errs)
        self.assertTrue(any("Linux guests in a class" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main()
