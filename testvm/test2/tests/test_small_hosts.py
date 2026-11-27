"""Small hosts (copland, cage: a 10 GiB RAM disk, cmp-run.sh's ceiling 9.0 9.2): each
host's ceiling is a fact (facts_host.py runs the host's own cmp-run.sh ceiling()), and
placement keeps a cell off a host whose ceiling its reservation exceeds, in every family that
reserves: a SticTest guest (its check.sh --reserve), a HoltTest cell (ram-size.sh, the host's
row) and a per-host gate loop (the layer's host_reserve, else its reserve, else its peaks).  A
cell no host of the run fits is not runnable, with its needs and the ceilings; drive.py and
SticTest2's report name a guest over every host's ceiling as too big.  Old facts have no
ceiling: nothing is checked.  No host needed."""

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TEST2 = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(TEST2))
sys.path.insert(0, TEST2)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "fixtures", "p2"))

import t2lib  # noqa: E402
from t2lib import facts, gate, holt, report, stic  # noqa: E402
from t2lib import hosts as hosts_table  # noqa: E402
import test_stic as TS  # noqa: E402
import world  # noqa: E402

SMALL, BIG = [9.0, 9.2], [29.0, 20.1]


@contextlib.contextmanager
def scratch_mode(mode, **limits):
    """The real host table with every class's scratch_mode MODE (and LIMITS in both classes),
    for the block: "ram" is every host as it was before 2026-11-23."""
    with open(os.path.join(REPO, "testvm", "hosts.json")) as f:
        t = json.load(f)
    for c in t["class_limits"].values():
        c.update(scratch_mode=mode, **limits)
    d = tempfile.mkdtemp(prefix="t2-small-table-")
    try:
        with open(os.path.join(d, "hosts.json"), "w") as f:
            json.dump(t, f)
        with mock.patch.dict(os.environ, {hosts_table.ENV: os.path.join(d, "hosts.json")}):
            yield
    finally:
        shutil.rmtree(d, ignore_errors=True)
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
        with scratch_mode("ram"):   # every line in the RAM disk, as before 2026-11-23
            p = self.plan("HoltTest2 mint22 debian13 plan", ["copland"], {"copland": SMALL})
            m, d = p.cell("holt:mint22"), p.cell("holt:debian13")
            self.assertEqual((m.status, m.reasons), ("not-runnable", ["over copland's ceiling 9.0 9.2: needs 17 4"]))
            self.assertEqual((d.status, sorted(d.keys)), ("due", ["copland"]))
            self.assertEqual([c.cell for c in p.due()], ["holt:debian13"])   # nothing of mint22's is queued

    def test_holt_cells_over_the_ram_disk_go_to_the_ssd(self):
        """RAM first, the SSD for overflow (the table's scratch_mode, 2026-11-23): a holt cell over
        a host's RAM-disk ceiling works on /bkoscratch there (ht1-queue.sh, under cmp-run.sh
        --reserve-ssd D M), so only its memory is held to the ceiling; with too little free SSD
        above the host's ssd_floor_gib it is held to the RAM disk as before."""
        self.assertEqual(hosts_table.limit("copland", "scratch_mode"), "overflow")
        p = self.plan("HoltTest2 mint22 debian13 plan", ["copland"], {"copland": SMALL})   # 900 GB free there
        m, d = p.cell("holt:mint22"), p.cell("holt:debian13")
        self.assertEqual((m.status, sorted(m.keys)), ("due", ["copland"]))
        self.assertEqual((d.status, sorted(d.keys)), ("due", ["copland"]))
        fx = self.facts(["copland"], {"copland": SMALL})
        fx["copland"]["preflight"]["scratch_free_gb"] = 100.0   # 93.1 GiB: 13.1 above the floor of 80, under its 17
        p = self.w.plan("HoltTest2 mint22 debian13 plan", facts=fx, hosts=["copland"])
        m = p.cell("holt:mint22")
        self.assertEqual((m.status, m.reasons), ("not-runnable", ["over copland's ceiling 9.0 9.2: needs 17 4"]))
        self.assertEqual(p.cell("holt:debian13").status, "due")
        with scratch_mode("overflow", ssd_floor_gib=70):   # the floor is the host's own
            p = self.w.plan("HoltTest2 mint22 plan", facts=fx, hosts=["copland"])
            self.assertEqual(p.cell("holt:mint22").status, "due")

    def test_a_default_run_holds_the_small_hosts_and_keeps_the_big_work_big(self):
        """Six amd64 hosts are default (copland and cage since 2026-11-14, choubox
        since 2026-11-17, czerny since 2026-11-19), so a run without --hosts
        has small ones too.  What they cannot run is still kept off them, by their
        ceiling and their caps, never by their names: a stic check whose reservation is over
        9.0 9.2, and a holt source vm-mount reads on the host's ZFS."""
        hosts = list(t2lib.DEFAULT_HOSTS)
        big = [h for h in hosts if hosts_table.host_class(h) == "big"]        # the table's order
        small = [h for h in hosts if hosts_table.host_class(h) == "small"]
        self.assertTrue(big and small, hosts)
        fx = self.facts(hosts, {h: (BIG if h in big else SMALL) for h in hosts})
        for h in hosts:
            zfs = h in big
            fx[h]["caps"] = {"zfs": zfs, "zpool": "/usr/sbin/zpool" if zfs else None,
                             "zfs_module": zfs, "holt_rescuer": True, "holt_debs": True}
        p = self.w.plan("SticTest2 rel4 ubuntu24 debian13 plan", facts=fx, hosts=hosts)
        self.assertEqual(sorted(p.cell("stic:ubuntu24").keys), sorted(big))      # --reserve 13 6
        self.assertEqual(sorted(p.cell("stic:debian13").keys), sorted(hosts))
        self.assertIn(f"too big for {', '.join(small)} "
                      f"(ceilings {', '.join('9.0 9.2' for _ in small)}): needs 13 6",
                      "; ".join(p.cell("stic:ubuntu24").reasons))
        q = self.w.plan("HoltTest2 ubuntu24 debian13 plan", facts=fx, hosts=hosts)
        self.assertEqual(sorted(q.cell("holt:ubuntu24").keys), sorted(big))   # needs_host_zfs, and 13 6
        self.assertRegex("; ".join(q.cell("holt:ubuntu24").reasons), r"kept off \w+: no host OpenZFS")
        self.assertEqual(sorted(q.cell("holt:debian13").keys), sorted(hosts))
        # Every small host is named in what its runner must leave to the others.
        oe = holt.only_elsewhere(q.due(), hosts, {})
        self.assertEqual({h: oe[h] for h in big}, {h: [] for h in big})
        self.assertEqual({h: oe[h] for h in small}, {h: ["ubuntu24"] for h in small})

    def test_the_zfs_and_long_sources_reach_the_small_hosts(self):
        """F5 of docs/design/img-speed.md: every amd64 host runs ZFS 2.4.4 (the small ones since
        2026-11-14), so with each host's guests in its cache a host-ZFS source (truenas25,
        void-zbm) and a long one (slackware15, D 16) are keyed on the small hosts too: D over
        their RAM-disk ceiling goes to the SSD under overflow, and nothing keeps them big-only."""
        hosts = list(t2lib.DEFAULT_HOSTS)
        small = [h for h in hosts if hosts_table.host_class(h) == "small"]
        self.assertTrue(small, hosts)
        fx = self.facts(hosts, {h: (SMALL if h in small else BIG) for h in hosts})
        uefi = next(f["rescuers"]["images"]["rescuer-uefi.qcow2"] for f in fx.values()
                    if "rescuer-uefi.qcow2" in f["rescuers"]["images"])
        for h in hosts:   # as the farm's facts say: OpenZFS and both rescuers on every host
            fx[h]["caps"] = {"zfs": True, "zpool": "/usr/sbin/zpool", "zfs_module": True,
                             "holt_rescuer": True, "holt_debs": True}
            fx[h]["rescuers"]["images"]["rescuer-uefi.qcow2"] = uefi
        p = self.w.plan("HoltTest2 truenas25 void-zbm slackware15 plan", facts=fx, hosts=hosts)
        for g in ("truenas25", "void-zbm", "slackware15"):
            c = p.cell("holt:" + g)
            self.assertEqual((c.status, sorted(c.keys)), ("due", sorted(hosts)), (g, c.reasons))
            self.assertNotIn("no host OpenZFS", "; ".join(c.reasons))
        self.assertEqual({h: v for h, v in holt.only_elsewhere(p.due(), hosts, {}).items() if v}, {})

    def test_the_zfs_why_names_no_host_without_zfs(self):
        """classes.json's needs_host_zfs_why said "copland and cage have no OpenZFS" long after
        both had it (F5): the text names no host of the table as lacking it."""
        with open(os.path.join(TEST2, "classes.json")) as f:
            why = json.load(f)["holt"]["needs_host_zfs_why"]
        for h in hosts_table.names(all_=True):
            self.assertNotRegex(why, rf"\b{h}\b[^.]*\bno OpenZFS\b")
        self.assertIn("caps.zfs", why)

    def test_holt_only_elsewhere_from_ceilings(self):
        with scratch_mode("ram"):
            p = self.plan("HoltTest2 mint22 debian13 plan", ["chopin", "copland"], {"chopin": BIG, "copland": SMALL})
            m = p.cell("holt:mint22")
            self.assertEqual((m.status, sorted(m.keys)), ("due", ["chopin"]))
            self.assertIn("too big for copland (ceiling 9.0 9.2): needs 17 4", m.reasons)
            oe = holt.only_elsewhere(p.due(), ["chopin", "copland"], {})
            self.assertEqual(oe, {"chopin": [], "copland": ["mint22"]})
        # with the SSD for overflow the small host takes it too (on /bkoscratch), and nothing is left to the others
        p = self.plan("HoltTest2 mint22 debian13 plan", ["chopin", "copland"], {"chopin": BIG, "copland": SMALL})
        self.assertEqual(sorted(p.cell("holt:mint22").keys), ["chopin", "copland"])
        self.assertEqual(holt.only_elsewhere(p.due(), ["chopin", "copland"], {}), {"chopin": [], "copland": []})

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


class GatePlacement(unittest.TestCase):
    """F7 (docs/design/img-speed.md, FD6): the fast gate layers unpinned.  The plan keeps each
    off a host without a host input it needs (a fork's stock baseline) and over whose ceiling
    its reserve is, puts the shared host (Chopin) last and spreads the forks, which run at once."""

    STOCK = {"restic": "~/b/stock/restic-0.20.0/results.tsv", "rustic_core": "~/b/stock/rustic_core-0.14.0/results.tsv",
             "rustic": "~/b/stock/rustic-0.12.0/results.tsv"}
    HOSTS = ["chopin", "cinqcent", "copland", "cage"]

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t2-small-gate-")
        cls.w = world.World(cls.tmp)
        layers = json.loads(cls.w.read("testvm/test2/gate/layers.json"))
        e = layers["entries"]
        for n, path in cls.STOCK.items():
            e["fork:" + n].pop("pin")
            e["fork:" + n].update(reserve=[6, 12], host_reserve={"small": [6, 10]},
                                  host_inputs=[{"path": path, "group": "judge", "need": True}])
        for n in ("holt", "fork-root:restic"):
            e[n].pop("pin")
        e["fork-root:restic"]["place_with"] = "fork:restic"
        cls.w.edit("testvm/test2/gate/layers.json", json.dumps(layers, indent=1, sort_keys=True), commit=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def plan(self, have, hosts=None):
        """GateTest2's fast layers on HOSTS (default all four), HAVE {host: [fork names whose
        baseline it has]}; the big hosts' ceilings BIG, the small ones' 9.0 10.1."""
        hosts = hosts or self.HOSTS
        fx = self.w.facts(hosts=hosts)
        for h in hosts:
            fx[h]["preflight"]["ram"]["ceiling"] = BIG if hosts_table.host_class(h) == "big" else [9.0, 10.1]
            fx[h]["host_inputs"] = {p: ("sha256:" + "ab" * 32 if n in have.get(h, ()) else "absent")
                                    for n, p in self.STOCK.items()}
        return self.w.plan("GateTest2 rel4 holt fork:restic fork:rustic_core fork:rustic fork-root:restic plan",
                           facts=fx, hosts=hosts)

    def test_forks_go_where_their_baselines_are_spread_and_off_the_shared_host(self):
        every = ("restic", "rustic_core", "rustic")
        p = self.plan({"chopin": ["restic"], "cinqcent": ["rustic_core", "rustic"],
                       "cage": ["restic"], "copland": ["rustic_core", "rustic"]})
        placed = {c: p.cell(c).placed for c in ("gate:fork:restic", "gate:fork:rustic_core", "gate:fork:rustic",
                                                "gate:holt", "gate:fork-root:restic")}
        self.assertEqual(placed, {"gate:fork:restic": "cage",          # chopin has it too: the shared host last
                                  "gate:fork:rustic_core": "cinqcent",  # a big host first
                                  "gate:fork:rustic": "copland",        # not beside rustic_core: they run at once
                                  "gate:holt": "cinqcent",
                                  "gate:fork-root:restic": "cage"})     # place_with: beside fork:restic's m run
        self.assertEqual(sorted(p.cell("gate:fork:restic").keys), ["cage", "chopin"])
        self.assertTrue(any("kept off cinqcent, copland" in r for r in p.cell("gate:fork:restic").reasons),
                        p.cell("gate:fork:restic").reasons)
        p = self.plan({h: every for h in self.HOSTS})
        self.assertEqual(len({p.cell(f"gate:fork:{n}").placed for n in every}), 3)
        self.assertNotIn("chopin", {p.cell(f"gate:fork:{n}").placed for n in every})

    def test_place_with_a_layer_not_due_goes_to_its_newest_row(self):
        """fork-root:restic alone: where fork:restic's newest row ran (its m run of the commit
        is there), not where the tie would put it."""
        have = {h: ("restic", "rustic_core", "rustic") for h in self.HOSTS}
        p = self.plan(have)
        fr = p.cell("gate:fork:restic")
        self.assertNotEqual(fr.placed, "copland")
        rows = [self.w.row(fr, "copland", plan_=p)]
        fx = self.w.last_facts
        p2 = self.w.plan("GateTest2 rel4 fork-root:restic plan", rows=rows, facts=fx, hosts=self.HOSTS)
        self.assertEqual(p2.cell("gate:fork-root:restic").placed, "copland")
        p3 = self.w.plan("GateTest2 rel4 fork-root:restic plan", facts=fx, hosts=self.HOSTS)
        self.assertEqual(p3.cell("gate:fork-root:restic").placed, "cinqcent")   # no row: the tie, Chopin last

    def test_the_small_hosts_alone(self):
        """--hosts copland,cage: each fork on a host with its baseline, its reserve within the
        small ceiling (host_reserve 6 10; the big reserve 6 12 is over MEM 10.1)."""
        p = self.plan({"cage": ["restic"], "copland": ["rustic_core", "rustic"]}, ["copland", "cage"])
        self.assertEqual({n: p.cell(f"gate:fork:{n}").placed for n in self.STOCK},
                         {"restic": "cage", "rustic_core": "copland", "rustic": "copland"})
        self.assertEqual(p.cell("gate:fork:rustic").status, "due")
        self.assertEqual(gate.reserve_for({"reserve": [6, 12], "host_reserve": {"small": [6, 10]}}, [],
                                          "gate:fork:rustic", "copland"), (6.0, 10.0))
        self.assertTrue(facts.over_ceiling((6.0, 12.0), [9.0, 10.1]))

    def test_no_host_with_the_baseline_is_not_runnable(self):
        p = self.plan({"cage": ["restic"]}, ["copland", "cage"])
        c = p.cell("gate:fork:rustic")
        self.assertEqual(c.status, "not-runnable")
        self.assertIn("no host in --hosts has ~/b/stock/rustic-0.12.0/results.tsv (testvm/audit/stock-baseline.sh makes it)",
                      c.reasons)


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
