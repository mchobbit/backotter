"""Small hosts (copland, cage: a 10 GiB RAM disk, cmp-run.sh's ceiling 9.0 9.2): each
host's ceiling is a fact (facts_host.py runs the host's own cmp-run.sh ceiling()), and
placement keeps a cell off a host whose ceiling its reservation exceeds, in every family that
reserves: a SticTest guest (its check.sh --reserve), a HoltTest cell (ram-size.sh, the host's
row) and a per-host gate loop (the layer's host_reserve, else its reserve, else its peaks).  A
cell no host of the run fits is not runnable, with its needs and the ceilings; drive.py and
SticTest2's report name a guest over every host's ceiling as too big.  Old facts have no
ceiling: nothing is checked.  No host needed."""

import json
import os
import shutil
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

from t2lib import facts, gate, holt, report, stic  # noqa: E402
import test_stic as TS  # noqa: E402
import world  # noqa: E402

SMALL, BIG = [9.0, 9.2], [29.0, 20.1]
FACTS = os.path.join(TEST2, "t2lib", "facts_host.py")


class Ceilings(unittest.TestCase):
    def test_reading_and_words(self):
        self.assertEqual(facts.ceiling({"preflight": {"ram": {"ceiling": [9.0, 9.2]}}}), (9.0, 9.2))
        for old in (None, {}, {"preflight": {"ram": {"mounted": True}}}, {"preflight": None},
                    {"preflight": {"ram": {"ceiling": None}}}, {"preflight": {"ram": {"ceiling": ["x", 1]}}}):
            self.assertIsNone(facts.ceiling(old), old)   # old facts: not known, not checked
        self.assertTrue(facts.over_ceiling((13, 6), (9.0, 9.2)))
        self.assertTrue(facts.over_ceiling((2, 9.3), (9.0, 9.2)))
        self.assertFalse(facts.over_ceiling((9.0, 9.2), (9.0, 9.2)))
        self.assertFalse(facts.over_ceiling((13, 6), None))
        self.assertFalse(facts.over_ceiling(None, (9.0, 9.2)))
        self.assertEqual(facts.too_big_words((13, 6), {"copland": SMALL}, True), "over copland's ceiling 9.0 9.2: needs 13 6")
        self.assertEqual(facts.too_big_words((23, 4.5), {"copland": SMALL, "cage": SMALL}, True),
                         "over the ceilings of copland 9.0 9.2, cage 9.0 9.2: needs 23 4.5")
        self.assertEqual(facts.too_big_words((13, 6), {"copland": SMALL}, False),
                         "too big for copland (ceiling 9.0 9.2): needs 13 6")
        self.assertEqual(facts.too_big_words({"copland": (17, 4), "chopin": (30, 4)}, {"copland": SMALL, "chopin": BIG}, True),
                         "over the ceilings of copland 9.0 9.2 (needs 17 4), chopin 29.0 20.1 (needs 30 4)")


class ProbeCeiling(unittest.TestCase):
    """facts_host.py's preflight.ram.ceiling is the host's cmp-run.sh ceiling(), run as it is."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2-small-facts-")
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
        return json.loads(p.stdout)

    def cmp_run(self, text):
        p = os.path.join(self.work, "cmp-run.sh")
        os.remove(p)
        with open(p, "w") as f:
            f.write(text)

    def test_the_hosts_ceiling_function(self):
        self.cmp_run('#!/usr/bin/env bash\nbudget() { echo 1 1; }\n'
                     'ceiling() {  # -> "DISK MEM"\n    [ -n "$RAM" ] && echo "9.0 9.2"\n}\nexit 3\n')
        f = self.probe()
        self.assertEqual(f["preflight"]["ram"]["ceiling"], [9.0, 9.2])
        self.assertEqual(facts.ceiling(f), (9.0, 9.2))

    def test_no_ceiling_function_or_no_answer(self):
        self.assertIsNone(self.probe()["preflight"]["ram"]["ceiling"])     # the fixture's: no ceiling()
        self.cmp_run('ceiling() {\n    echo nonsense\n}\n')
        self.assertIsNone(self.probe()["preflight"]["ram"]["ceiling"])
        os.remove(os.path.join(self.work, "cmp-run.sh"))
        self.assertIsNone(self.probe()["preflight"]["ram"]["ceiling"])

    def test_no_ram_disk(self):
        self.cmp_run('ceiling() {\n    echo "9.0 9.2"\n}\n')
        with open(os.path.join(self.root, "proc", "mounts"), "w") as f:
            f.write("/dev/nvme0n1p1 /bkoscratch xfs rw 0 0\n")
        self.assertIsNone(self.probe()["preflight"]["ram"]["ceiling"])

    def test_the_real_ceiling_is_one_function(self):
        """facts_host.py and drive.py's status read the same function out of cmp-run.sh."""
        with open(os.path.join(REPO, "testvm", "compare", "cmp-run.sh")) as f:
            text = f.read()
        body = subprocess.run(["sed", "-n", "/^ceiling()/,/^}/p"], input=text, capture_output=True, text=True).stdout
        self.assertTrue(body.startswith("ceiling() {") and body.rstrip().endswith("}"), body[:80])
        self.assertIn('fits "$cd" "$d" "$cm" "$m" || { echo "--reserve $d $m can never fit here', text)


class Placement(unittest.TestCase):
    """The plan, on the fixture world: chopin big, copland and cage small."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t2-small-plan-")
        cls.w = world.World(cls.tmp)
        w = cls.w
        w.edit("testvm/stictest1/ubuntu24/check.sh",
               "#!/usr/bin/env bash\n# check.sh BIN_DIR PORT PREFIX: BIN_DIR is roundN/bin/linux\n"
               "slot_take 3\n~/backotter-work/cmp-run.sh --reserve 13 6 true\n")
        shutil.copy(os.path.join(REPO, "testvm", "holttest1d", "ram-size.sh"),
                    os.path.join(w.repo, "testvm", "holttest1d", "ram-size.sh"))
        layers = json.loads(w.read("testvm/test2/gate/layers.json"))
        layers["entries"]["loop:linux"].update(reserve=[14, 4], host_reserve={"copland": [8.5, 4], "cage": [8.5, 4]})
        layers["entries"]["loop:sparse"].update(reserve=[20, 4])   # no host_reserve: over a small host's ceiling
        w.edit("testvm/test2/gate/layers.json", json.dumps(layers, indent=1, sort_keys=True), commit=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def facts(self, hosts, ceilings=None):
        fx = self.w.facts(hosts=hosts)
        for h, c in (ceilings or {}).items():
            fx[h]["preflight"]["ram"]["ceiling"] = c
        return fx

    def plan(self, text, hosts, ceilings=None):
        return self.w.plan(text, facts=self.facts(hosts, ceilings), hosts=hosts)

    def test_stic_guest_over_every_hosts_ceiling(self):
        p = self.plan("SticTest2 rel4 ubuntu24 debian13 plan", ["copland"], {"copland": SMALL})
        u, d = p.cell("stic:ubuntu24"), p.cell("stic:debian13")
        self.assertEqual(u.status, "not-runnable")
        self.assertEqual(u.reasons, ["over copland's ceiling 9.0 9.2: needs 13 6"])
        self.assertEqual((d.status, sorted(d.keys)), ("due", ["copland"]))
        self.assertIn("not runnable: stic:ubuntu24 (over copland's ceiling 9.0 9.2: needs 13 6)", report.render_plan(p))
        p = self.plan("SticTest2 rel4 ubuntu24 plan", ["copland", "cage"], {"copland": SMALL, "cage": SMALL})
        self.assertEqual(p.cell("stic:ubuntu24").reasons, ["over the ceilings of copland 9.0 9.2, cage 9.0 9.2: needs 13 6"])

    def test_stic_guest_placed_on_the_host_it_fits(self):
        p = self.plan("SticTest2 rel4 ubuntu24 plan", ["chopin", "copland"], {"chopin": BIG, "copland": SMALL})
        u = p.cell("stic:ubuntu24")
        self.assertEqual((u.status, sorted(u.keys), u.host), ("due", ["chopin"], "chopin"))
        self.assertIn("too big for copland (ceiling 9.0 9.2): needs 13 6", u.reasons)

    def test_old_facts_are_not_checked(self):
        p = self.plan("SticTest2 rel4 ubuntu24 plan", ["copland"])
        self.assertEqual((p.cell("stic:ubuntu24").status, sorted(p.cell("stic:ubuntu24").keys)), ("due", ["copland"]))
        p = self.plan("SticTest2 rel4 ubuntu24 plan", ["chopin", "copland"], {"chopin": BIG})
        self.assertEqual(sorted(p.cell("stic:ubuntu24").keys), ["chopin", "copland"])   # copland's ceiling unknown

    def test_holt_cells_by_ram_size(self):
        p = self.plan("HoltTest2 mint22 debian13 plan", ["copland"], {"copland": SMALL})
        m, d = p.cell("holt:mint22"), p.cell("holt:debian13")
        self.assertEqual((m.status, m.reasons), ("not-runnable", ["over copland's ceiling 9.0 9.2: needs 17 4"]))
        self.assertEqual((d.status, sorted(d.keys)), ("due", ["copland"]))
        self.assertEqual([c.cell for c in p.due()], ["holt:debian13"])   # nothing of mint22's is queued

    def test_holt_only_elsewhere_from_ceilings(self):
        p = self.plan("HoltTest2 mint22 debian13 plan", ["chopin", "copland"], {"chopin": BIG, "copland": SMALL})
        m = p.cell("holt:mint22")
        self.assertEqual((m.status, sorted(m.keys)), ("due", ["chopin"]))
        self.assertIn("too big for copland (ceiling 9.0 9.2): needs 17 4", m.reasons)
        oe = holt.only_elsewhere(p.due(), ["chopin", "copland"], {})
        self.assertEqual(oe, {"chopin": [], "copland": ["mint22"]})

    def test_holt_only_elsewhere_names_a_variant(self):
        """A base guest that fits with a variant that doesn't: the variant alone is left to the others."""
        PC = TS.PC
        both, one = {"chopin": "k", "copland": "k"}, {"chopin": "k"}
        pcs = [PC("holt:debian13", "due"), PC("holt:debian13+nearfull", "due"), PC("holt:mint22", "due")]
        pcs[0].keys, pcs[1].keys, pcs[2].keys = both, one, one
        self.assertEqual(holt.only_elsewhere(pcs, ["chopin", "copland"]),
                         {"chopin": [], "copland": ["debian13+nearfull", "mint22"]})

    def test_gate_loops_on_a_small_host(self):
        p = self.plan("GateTest2 rel4 plan", ["copland"], {"copland": SMALL})
        lin, sp = p.cell("gate:loop:linux@copland"), p.cell("gate:loop:sparse@copland")
        self.assertEqual((lin.status, sorted(lin.keys)), ("due", ["copland"]))   # host_reserve 8.5 4 fits
        self.assertEqual((sp.status, sp.reasons), ("not-runnable", ["over copland's ceiling 9.0 9.2: needs 20 4"]))
        p = self.plan("GateTest2 rel4 plan", ["chopin", "copland"], {"chopin": BIG, "copland": SMALL})
        self.assertEqual(p.cell("gate:loop:sparse@chopin").status, "due")     # 20 4 fits chopin

    def test_gate_reserve_per_host(self):
        e = {"reserve": [14, 4], "host_reserve": {"copland": [8.5, 4], "cage": [8.5, 4]}, "peak_reserve": True}
        self.assertEqual(gate.reserve_for(e, [], "gate:loop:linux@copland"), (8.5, 4.0))
        self.assertEqual(gate.reserve_for(e, [], "gate:loop:linux@cage"), (8.5, 4.0))
        self.assertEqual(gate.reserve_for(e, [], "gate:loop:linux@chopin"), (14.0, 4.0))
        rows = [{"cell": "gate:loop:linux@copland", "kind": "run", "verdict": "PASS", "t": "2026-11-13T05:00:00Z",
                 "id": "copland-1", "ram_peak_kib": 6 << 20, "meta": {"loop": {"linux_fs": "default"}}}]
        self.assertEqual(gate.reserve_for(e, rows, "gate:loop:linux@copland"), (6.9, 4.0))   # its own peaks: 1.1 x 6 + 0.25
        self.assertEqual(gate.reserve_for(e, rows, "gate:loop:linux@chopin"), (14.0, 4.0))    # not chopin's

    def test_the_layers_fit_the_small_hosts(self):
        """testvm/test2/gate/layers.json: both loops' reserves fit copland's and cage's ceiling."""
        with open(os.path.join(TEST2, "gate", "layers.json")) as f:
            layers = json.load(f)["layers"]
        for n in ("loop:linux", "loop:sparse"):
            for h in ("copland", "cage"):
                need = gate.reserve_for(layers[n], [], f"gate:{n}@{h}")
                self.assertFalse(facts.over_ceiling(need, SMALL), (n, h, need))
            self.assertTrue(facts.over_ceiling(gate.reserve_for(layers[n], [], f"gate:{n}@chopin"), SMALL), n)


class SticReport(unittest.TestCase):
    """SticTest2's result names a guest drive.py found over every host's ceiling as too big."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2-small-stic-")
        self.ctx = TS.RunCtx(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_too_big_in_the_report(self):
        big = {"ubuntu24": {"needs": [13, 6], "ceilings": {"copland": SMALL, "cage": SMALL}}}

        class Env(TS.FakeEnv):
            def drive_state(self, out):
                return {"jobs": {"debian13": {}}, "too_big": big}
        env = Env({TS.MARK: [(1, TS.row("debian13", "PASS", n=1))]})
        plan = TS.Plan([TS.PC("stic:debian13", "due", "fresh:names"), TS.PC("stic:ubuntu24", "due", "fresh:names")])
        res = stic.SticRun(self.ctx, plan, env).go()
        cells = {c["cell"]: c for c in res["cells"]}
        self.assertEqual(cells["stic:debian13"]["outcome"], "ran")
        u = cells["stic:ubuntu24"]
        self.assertEqual(u["outcome"], "too-big")
        self.assertEqual(u["reasons"][0], "over the ceilings of copland 9.0 9.2, cage 9.0 9.2: needs 13 6")
        self.assertEqual(res["exit"], 1)
        notes = " | ".join(res["notes"])
        self.assertIn("too big for every host, not started: ubuntu24 "
                      "(over the ceilings of copland 9.0 9.2, cage 9.0 9.2: needs 13 6)", notes)
        self.assertIn("stic:ubuntu24 too big: over the ceilings of copland", notes)


if __name__ == "__main__":
    unittest.main()
