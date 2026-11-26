"""Ties (docs/design/img-speed.md F13, FD2): where a planner rates two hosts the same, the
tie goes by class (big first), then heat (the cooler first, hosts.json's heat limit), then
the table's order (hosts.tie_key), not by the table's order alone, so Chopin, the first row,
no longer takes every tie.  The table's order itself is unchanged.  Each planner that broke
a tie by the table's order (or by name) follows tie_key: drive.py's simulate, the gate's
fallback host, ImgTest2's target, run-all.sh's SPLIT deal and bs2's deals."""

import json
import os
import re
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
TESTVM = os.path.dirname(T2)
sys.path.insert(0, T2)
sys.path.insert(0, HERE)

from t2lib import hosts as H  # noqa: E402
from t2lib import img  # noqa: E402

import test_gate as tgate  # noqa: E402
from test_host_limits import DRIVE  # noqa: E402

TABLE = os.path.join(TESTVM, "hosts.json")


def setUpModule():
    tgate.setUpModule()


def tearDownModule():
    tgate.tearDownModule()


HOSTS_PY = os.path.join(T2, "t2lib", "hosts.py")
RUN_ALL = os.path.join(TESTVM, "compare", "run-all.sh")


def changed_table(change):
    """A copy of the real table, CHANGE(table) applied, in a temporary file: its path."""
    with open(TABLE) as f:
        t = json.load(f)
    change(t)
    fd, p = tempfile.mkstemp(prefix="t2-ties-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(t, f)
    return p


def row(t, name):
    return next(r for r in t["hosts"] if r["name"] == name)


def cool_chopin(t):
    row(t, "chopin").pop("heat", None)


class TieKey(unittest.TestCase):
    def test_the_real_table(self):
        """chopin's heat 1 (its faulty cooler) puts cinqcent first among the big hosts; the
        small ones keep the table's order; the table's own order is unchanged."""
        self.assertEqual(H.limit("chopin", "heat"), 1)
        self.assertEqual({h: H.limit(h, "heat") for h in H.names() if h != "chopin"},
                         {h: 0 for h in H.names() if h != "chopin"})
        self.assertEqual(H.defaults(), ("chopin", "cinqcent", "copland", "cage", "czerny", "choubox"))
        self.assertEqual(H.tie_order(H.defaults()), ["cinqcent", "chopin", "copland", "cage", "czerny", "choubox"])
        self.assertEqual(H.tie_first(["chopin", "cinqcent"]), "cinqcent")
        self.assertEqual(H.tie_first(["copland", "chopin"]), "chopin", "class before heat")
        self.assertEqual(H.tie_first(["cage", "copland"]), "copland", "then the table's order")
        self.assertIsNone(H.tie_first([]))
        self.assertEqual(H.tie_order(["zz", "chopin", "aa", "mac"]), ["chopin", "aa", "mac", "zz"],
                         "a name in no row: after every row, by name")

    def test_class_then_heat_then_order(self):
        def change(t):
            row(t, "chopin")["heat"] = 9
            row(t, "copland")["heat"] = 3
        p = changed_table(change)
        self.addCleanup(os.unlink, p)
        t = H.load(p)
        self.assertEqual(H.tie_order(["chopin", "cinqcent", "copland", "cage", "czerny"], t),
                         ["cinqcent", "chopin", "cage", "czerny", "copland"])
        p2 = changed_table(cool_chopin)
        self.addCleanup(os.unlink, p2)
        self.assertEqual(H.tie_order(["cinqcent", "chopin", "cage", "copland"], H.load(p2)),
                         ["chopin", "cinqcent", "copland", "cage"], "every heat 0: the table's order, as before")

    def test_heat_by_class(self):
        def change(t):
            t["class_limits"]["small"]["heat"] = 2
            row(t, "czerny")["heat"] = 0
        p = changed_table(change)
        self.addCleanup(os.unlink, p)
        t = H.load(p)
        self.assertEqual(H.limit("cage", "heat", t), 2)
        self.assertEqual(H.tie_order(["copland", "cage", "czerny"], t), ["czerny", "copland", "cage"])

    def test_a_bad_heat_is_refused(self):
        for bad in (10, -1, "1", True, 1.5, None):
            with open(TABLE) as f:
                t = json.load(f)
            row(t, "cinqcent")["heat"] = bad
            errs = H.check_table(t)
            self.assertTrue(any("heat" in e for e in errs), (bad, errs))

    def test_names_ties_on_the_command_line(self):
        r = subprocess.run([sys.executable, "-B", HOSTS_PY, "names", "--default", "--ties"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual((r.returncode, r.stdout.split()), (0, ["cinqcent", "chopin", "copland", "cage", "czerny", "choubox"]))
        r = subprocess.run([sys.executable, "-B", HOSTS_PY, "names", "--default"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.split()[:2], ["chopin", "cinqcent"], "without --ties: the table's order")
        r = subprocess.run([sys.executable, "-B", HOSTS_PY, "limit", "chopin", "heat"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.strip(), "1")


class Planners(unittest.TestCase):
    def test_drive_simulate(self):
        """Two idle hosts, one check: the tie's host takes it (it took the name's first)."""
        S = DRIVE.Spec
        spec = {"g1": S("linux", 2, 2, 1, True)}
        have = {f"linux/{x}" for x in ("restic-bko", "rustic-bko")}

        def hosts(*ns):
            return {h: dict(have=set(have), slots_free=3, room=None, ceil=None) for h in ns}
        for ns, want in ((("chopin", "cinqcent"), "cinqcent"), (("copland", "chopin"), "chopin"),
                         (("cage", "copland"), "copland")):
            rows, left = DRIVE.simulate(["g1"], spec, {"g1": 60}, hosts(*ns), 3)
            self.assertEqual((rows[0][1], left), (want, []), ns)

    def test_gate_fallback_host(self):
        env, _ = tgate.env_with(tgate.FakeIO(), ["gate:bin:abi"])
        self.assertEqual(env.host_for("gate:bin:abi"), "cinqcent")
        self.assertEqual(env.host_for("gate:loop:sparse@chopin"), "chopin", "a named host stays")
        p = changed_table(cool_chopin)
        self.addCleanup(os.unlink, p)
        with mock.patch.dict(os.environ, {H.ENV: p}):
            self.assertEqual(env.host_for("gate:bin:abi"), "chopin")

    def test_img_target(self):
        pc = types.SimpleNamespace(placed=None, host=None)
        self.assertEqual(img.Runner.target(None, pc, ["chopin", "cinqcent", "copland"]), "cinqcent")
        self.assertEqual(img.Runner.target(None, types.SimpleNamespace(placed="chopin", host=None),
                                           ["chopin", "cinqcent"]), "chopin", "the plan's placement first")
        self.assertIsNone(img.Runner.target(None, pc, []))


def split_script():
    """run-all.sh's split_share program (its heredoc)."""
    with open(RUN_ALL) as f:
        src = f.read()
    m = re.search(r"python3 -I -B - \"\$BKO_HOSTS_PY\" \"\$1\" \"\$2\" <<'EOF'\n(.*?)\nEOF\n", src, re.S)
    return m.group(1)


class RunAllSplit(unittest.TestCase):
    def deal(self, split, me, env=None):
        e = dict(os.environ, **(env or {}))
        r = subprocess.run([sys.executable, "-I", "-B", "-", HOSTS_PY, split, me], input=split_script(),
                           capture_output=True, text=True, env=e, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return {ln.split()[0]: ln.split()[1:] for ln in r.stdout.splitlines() if ln.strip()}

    def test_the_tie_goes_to_cinqcent(self):
        """SPLIT chopin,cinqcent: bcachefs stays at chopin and netbsd at cinqcent (HOME); the
        first unit both may take, sparse's zfs, is a tie, and goes to cinqcent now."""
        c, q = self.deal("chopin,cinqcent", "chopin"), self.deal("chopin,cinqcent", "cinqcent")
        self.assertEqual(c["hosts"], ["chopin", "cinqcent"], "the table's order, as listed")
        self.assertIn("bcachefs", c["linux"])
        self.assertIn("netbsd", q["plats"])
        self.assertIn("zfs", q["sparse"])
        self.assertNotIn("zfs", c["sparse"])
        p = changed_table(cool_chopin)
        self.addCleanup(os.unlink, p)
        old = self.deal("chopin,cinqcent", "chopin", {H.ENV: p})
        self.assertIn("zfs", old["sparse"], "with chopin cool: the table's order, as before F13")


if __name__ == "__main__":
    unittest.main()
