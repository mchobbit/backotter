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

from t2lib import estimates, ledger, plan, run   # noqa: E402
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
        for word, rows, n in (("SticTest2 rel4", self.r_stic, 41), ("HoltTest2", self.r_holt, 54),
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
        self.assertEqual(len(due), 28)   # 27 Linux checks and sculpt2610
        self.assertTrue(all(st(p, c) == ("due", "changed:bin") for c in due))
        self.assertEqual(st(p, "stic:freebsd15"), ("current", None))
        self.assertIn("(rustic", "; ".join(p.cell("stic:debian13").reasons))

    def test_version_only(self):
        f = self.w.facts(round_commits=self.w.forks["c1"], round_tag="rel5")
        p = self.w.plan("SticTest2 rel4 plan", self.r_stic, facts=f)
        self.assertEqual(by(p), {("carried", "carry:version-only"): 41})
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
        self.assertEqual(len(p.by_status("awaiting-canaries")), 32)
        rows = self.r_stic + [self.w.row(p.cell(c), "chopin", plan_=p) for c in can]
        p2 = self.w.plan("SticTest2 rel4 plan", rows, facts=f)
        self.assertEqual(p2.totals["due"], 0)
        self.assertEqual(len(p2.by_status("canary-verified")), 32)
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
        self.assertEqual(len(p.by_status("soft-stale")), 30)
        self.assertIn("6.12.115->6.12.116", "; ".join(p.by_status("soft-stale")[0].reasons))
        p = self.w.plan("SticTest2 rel4 stale plan", self.r_stic, facts=f)
        self.assertEqual(sum(1 for c in p.due() if c.rule == "stale"), 32)

    def test_stale_with_names_adds_the_soft_stale_cells(self):
        f = self.w.facts(kernel="6.12.116")
        p = self.w.plan("SticTest2 rel4 debian13 stale plan", self.r_stic, facts=f)
        self.assertEqual(by(p), {("due", "fresh:names"): 1, ("due", "stale"): 31})
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
        self.assertEqual(sum(1 for c in got.cells if c.status == "canary-verified"), 32)
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
        self.assertEqual(by(p), {("due", "changed:unmapped"): 41})
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
        self.assertEqual(by(p), {("soft-stale", None): 41})
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
        self.assertEqual(p.render().strip(), "GateTest2: nothing due (13 current); round rel4, base " + self.base[:7])
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

    def test_version_only_bump_carries_all_54(self):
        self.w.edit("holt/Cargo.toml", self.w.read("holt/Cargo.toml").replace("0.2.4", "0.2.5"))
        self.w.edit("holt/Cargo.lock", self.w.read("holt/Cargo.lock").replace("0.2.4", "0.2.5"), commit=True)
        p = self.w.plan("HoltTest2 plan", self.r_holt)
        self.assertEqual(by(p), {("carried", "carry:version-only"): 54})
        res = p.cell("holt:debian13").res["chopin"]
        self.assertTrue(res.inputs["bin"]["linux/bko-holt"].startswith("code:"))   # R14
        g = self.w.plan("GateTest2 plan", self.r_gate)
        self.assertEqual(st(g, "gate:holt"), ("due", "changed:harness"))

    def test_code_no_plan_shows_all_45(self):
        self.w.edit("holt/src/load/exec.rs", "// runs the plan, changed\n", commit=True)
        p = self.w.plan("HoltTest2 plan", self.r_holt)
        self.assertEqual(sum(1 for c in p.cells if c.rule == "holt:code"), 45)
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
        self.assertEqual(sum(1 for c in p.cells if c.rule == "holt:code"), 45)
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
        self.assertEqual(by(p)[("carried", "carry:plan-diff")], 48)

    def test_rustic_only_change_panel_and_carried(self):
        f = self.w.facts(rustic_tag="bko2")
        p = self.w.plan("HoltTest2 plan", self.r_holt, facts=f)
        self.assertEqual({c.cell for c in p.due()},
                         {f"holt:{g}" for g in ("omarchy", "nixos2605", "freebsd15", "netbsd11", "openbsd8",
                                                "dragonfly6", "omnios58", "haiku6")})
        self.assertEqual(by(p)[("carried", "carry:rustic-panel")], 46)

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
        self.assertEqual(by(p), {("due", "fresh:All"): 54})


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
            self.assertEqual(len(p.by_status("carried")), 41)
            rows = run.derived_rows(p, "t2s-20261109T0000-abcd", wd, ctx, {"cells": [], "stops": []}, self.r_stic)
            self.assertEqual(len(rows), 41)
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
        self.assertEqual(len(got), 46)

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
        self.assertEqual(by(p), {("carried", "carry:version-only"): 41})
        # heads equal to the last PASS's commits: the binaries are predicted unchanged
        self.w.set_heads("c0")
        f = self.w.facts()
        for h in f:
            f[h]["rounds"] = {}
        p = self.w.plan("SticTest2 plan", self.r_stic, facts=f)
        self.assertTrue(p.set["pending"])
        self.assertEqual(by(p), {("current", None): 41})
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
            fam[c.split(":")[0]] += e["gm"]
        self.assertAlmostEqual(fam["stic"], 281.9, delta=0.1)     # test2.md: SticTest2All 282 gm
        self.assertAlmostEqual(fam["holt"], 246.7, delta=0.5)     # test2.md: HoltTest2All ~247 gm
        self.assertEqual(sum(1 for c in tsv if c.startswith("holt:")), 54)
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
    """U2.6: a plan over every cell (41 stic, 45 holt with 9 variants, 13 gate) and 10,000 rows
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
        self.assertEqual(len(cells), 41 + 54 + 13)
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


class ClassTable(unittest.TestCase):
    def test_the_real_class_table(self):
        ctx = plan.Ctx(REPO, hosts=[], options={})
        self.assertEqual(plan.check_classes(ctx, holt_names=world.GUESTS), [])
        h = ctx.classes["holt"]
        src = {g for f in h["families"].values() for g in f} | set(h["no_family"])
        self.assertEqual(len(src), 45)
        self.assertEqual(sum(len(v) for v in h["variants"].values()), 9)
        self.assertEqual(sorted(set(world.GUESTS) - src), ["freebsd15-builder", "freebsd15-geli", "freebsd15-ufs"])

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
