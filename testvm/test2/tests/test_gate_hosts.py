"""The per-host gate cells (gate:loop:linux@HOST, gate:loop:sparse@HOST) follow a run's
--hosts: the default hosts' cells always (a default host --hosts leaves out keeps its
cells, not runnable, as a pinned layer's are), and a host not joined yet (bigfoot)
gets its own only when --hosts names it, so that HostTest2 can run there before it joins.
Six amd64 hosts are default (copland and cage since 2026-11-14, choubox since
2026-11-17, czerny since 2026-11-19), so the universe holds a loop cell for each of the six,
and `--hosts copland`
is now a subset of the defaults, not an addition to them."""

import os
import shutil
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, "fixtures", "p2"))

import t2lib  # noqa: E402
from t2lib import gate, plan, words  # noqa: E402
import world  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
PINNED = ["gate:holt", "gate:fork:restic", "gate:fork:rustic_core", "gate:fork:rustic", "gate:fork-root:restic",
          "gate:bin:abi", "gate:bin:static", "gate:bin:version"]
DEFAULT_LOOP = [f"gate:loop:{n}@{h}" for n in ("linux", "sparse") for h in t2lib.DEFAULT_HOSTS]
COPLAND_LOOP = ["gate:loop:linux@copland", "gate:loop:sparse@copland"]      # a default host of its own
BIGFOOT_LOOP = ["gate:loop:linux@bigfoot", "gate:loop:sparse@bigfoot"]      # not joined yet


def loop_cells(p):
    return [c.cell for c in p.cells if c.cell.startswith("gate:loop:")]


class GateHosts(unittest.TestCase):
    def test_gate_hosts(self):
        self.assertEqual(t2lib.gate_hosts(), t2lib.DEFAULT_HOSTS)
        self.assertEqual(t2lib.gate_hosts(list(t2lib.DEFAULT_HOSTS)), t2lib.DEFAULT_HOSTS)
        self.assertEqual(t2lib.gate_hosts(["chopin"]), t2lib.DEFAULT_HOSTS)       # a subset: the defaults
        self.assertEqual(t2lib.gate_hosts(["copland"]), t2lib.DEFAULT_HOSTS)      # a default host now
        self.assertEqual(t2lib.gate_hosts(["bigfoot"]), t2lib.DEFAULT_HOSTS + ("bigfoot",))
        self.assertEqual(t2lib.gate_hosts(["bigfoot", "chopin", "bigfoot"]), t2lib.DEFAULT_HOSTS + ("bigfoot",))

    def test_gate_universe_of_the_layer_table(self):
        default = gate.universe()
        self.assertEqual(default, gate.universe(hosts=list(t2lib.DEFAULT_HOSTS)))
        self.assertEqual([c for c in default if "@" in c], DEFAULT_LOOP)
        self.assertEqual(default, gate.universe(hosts=["copland"]))   # a default host adds nothing
        self.assertFalse([c for c in default if c.endswith("@bigfoot")])
        mixed = gate.universe(hosts=["chopin", "bigfoot"])
        self.assertEqual(mixed, gate.universe(hosts=["bigfoot"]))
        self.assertEqual([c for c in mixed if "@" in c],
                         [f"gate:loop:{n}@{h}" for n in ("linux", "sparse")
                          for h in t2lib.DEFAULT_HOSTS + ("bigfoot",)])
        self.assertEqual([c for c in mixed if c not in BIGFOOT_LOOP], default)

    def test_runner_cells_and_hosts(self):
        """gate.py's runner already follows ctx.hosts: a layer name gives each host's cell."""
        fx = {"copland": {"host": "copland"}}
        ctx = types.SimpleNamespace(checkout=REPO, hosts=["copland"], facts=fx, ledger=[], run=None)
        env = gate.RunEnv(ctx, log=lambda _: None)
        self.assertEqual(gate.cells_of(env, ["loop:linux", "loop:sparse"]), COPLAND_LOOP)
        self.assertEqual(env.host_for("gate:loop:sparse@copland"), "copland")
        ctx.hosts, ctx.facts = ["chopin", "copland"], dict(fx, chopin={"host": "chopin"})
        env = gate.RunEnv(ctx, log=lambda _: None)
        self.assertEqual(gate.cells_of(env, ["loop:linux"]), ["gate:loop:linux@chopin", "gate:loop:linux@copland"])


class PlannedGateHosts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t2-gatehosts-")
        cls.w = world.World(cls.tmp)
        cls.p_default = cls.w.plan("GateTest2 rel4 plan")
        cls.rows = cls.w.seed(cls.p_default)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def plan_on(self, hosts, rows=None):
        fx = self.w.facts(hosts=hosts)
        return self.w.plan("GateTest2 rel4 plan", self.rows if rows is None else rows, facts=fx, hosts=hosts)

    def test_default_universe(self):
        """Without --hosts: the pinned layers and a loop cell per default host, all six."""
        p = self.p_default
        self.assertEqual([c.cell for c in p.cells if "@" in c.cell], DEFAULT_LOOP)
        self.assertEqual(len(p.cells), len(PINNED) + len(DEFAULT_LOOP) + 1)   # + gate:selftest
        explicit = self.w.plan("GateTest2 rel4 plan", hosts=tuple(t2lib.DEFAULT_HOSTS))
        self.assertEqual([(c.cell, c.keys) for c in explicit.cells], [(c.cell, c.keys) for c in p.cells])
        again = self.w.plan("GateTest2 rel4 plan", self.rows)
        self.assertEqual(again.due(), [])   # the seeded rows make every cell current: no due-churn
        self.assertIn("loop:linux@copland", plan.known_names(self.w.ctx())["gate"])
        self.assertIn("loop:linux@czerny", plan.known_names(self.w.ctx())["gate"])   # a default host since 2026-11-19
        with self.assertRaises(words.Refused):   # bigfoot is a host, but not a default one: no cell here
            self.w.word("GateTest2 loop:linux@bigfoot plan")

    def test_hosts_copland(self):
        """--hosts copland is a subset of the defaults: its cells run, the other default
        hosts' keep theirs, not runnable, as a pinned layer's are."""
        others = [c for c in DEFAULT_LOOP if c not in COPLAND_LOOP]
        p = self.plan_on(["copland"], [])
        self.assertEqual(loop_cells(p), DEFAULT_LOOP)
        for c in COPLAND_LOOP:
            pc = p.cell(c)
            self.assertEqual((pc.status, pc.rule), ("due", "new"), c)
            self.assertEqual(sorted(pc.keys), ["copland"], c)
            self.assertEqual(pc.host, "copland", c)
        for c in PINNED + others:
            pc = p.cell(c)
            self.assertEqual(pc.status, "not-runnable", c)
            self.assertRegex("; ".join(pc.reasons), r"(chopin|cinqcent|cage|czerny|choubox) is not in --hosts", c)
        # a copland row is judged as any host's: its cell is current on the next plan
        rows = [self.w.row(p.cell(c), "copland", plan_=p) for c in COPLAND_LOOP]
        q = self.plan_on(["copland"], rows)
        self.assertEqual({c: q.cell(c).status for c in COPLAND_LOOP}, {c: "current" for c in COPLAND_LOOP})
        # The p2 world is a two-host world (world.HOSTS, the hosts it has facts for), so its
        # default plan still holds every default host's cell and keeps the other two off:
        # the cell names and keys don't depend on which hosts a run names.
        d = self.w.plan("GateTest2 rel4 plan", rows)
        self.assertEqual([c.cell for c in d.cells], [c.cell for c in self.p_default.cells])
        self.assertEqual({c: d.cell(c).status for c in COPLAND_LOOP},
                         {c: "not-runnable" for c in COPLAND_LOOP})

    def test_the_names_hold_every_default_hosts_loop(self):
        ctx = self.w.ctx(facts=self.w.facts(hosts=["copland"]), hosts=["copland"])
        gl = plan.known_names(ctx)["gate"]
        self.assertIn("loop:linux@copland", gl)
        self.assertIn("loop:linux@chopin", gl)
        w = self.w.word("GateTest2 rel4 loop:linux@copland plan", ctx)
        self.assertEqual(w.names, ["gate:loop:linux@copland"])
        w = self.w.word("GateTest2 rel4 loop plan", ctx)
        self.assertEqual(sorted(w.names), sorted(DEFAULT_LOOP))

    def test_mixed_hosts(self):
        p = self.plan_on(["chopin", "copland"])
        self.assertEqual(sorted(loop_cells(p)), sorted(DEFAULT_LOOP))
        for c in ("gate:loop:linux@chopin", "gate:loop:sparse@chopin"):
            self.assertEqual(p.cell(c).status, "current", c)   # the same key as the default plan's
            self.assertEqual(p.cell(c).keys, self.p_default.cell(c).keys, c)
        for c in ("gate:loop:linux@cinqcent", "gate:loop:sparse@cinqcent", "gate:fork:rustic"):
            self.assertEqual(p.cell(c).status, "not-runnable", c)
            self.assertIn("cinqcent is not in --hosts", "; ".join(p.cell(c).reasons), c)
        every = self.plan_on(list(t2lib.DEFAULT_HOSTS))
        # Due: the loops of the hosts the p2 world's default plan couldn't run, so nothing
        # seeded a row for them.
        self.assertEqual(sorted(c.cell for c in every.due()),
                         sorted(c for c in DEFAULT_LOOP if c.rsplit("@", 1)[1] not in world.HOSTS))
        # Every cell the two-host world could key keeps that key: naming more hosts adds
        # keys (copland's and cage's loops get theirs) and changes none.
        for c in self.p_default.cells:
            if c.keys:
                self.assertEqual(every.cell(c.cell).keys, c.keys, c.cell)


if __name__ == "__main__":
    unittest.main()
