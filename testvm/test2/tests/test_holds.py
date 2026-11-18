"""testvm/test2/holds.json (2026-11-18): a held guest's stic, holt and guest cells are not
runnable in every word, named ones included, whatever the specs say; T2_UNHOLD runs them;
a file that doesn't check is a PlanError, no file holds nothing.  The P2 fixture world's
checkout has no holds.json (its goldens hold nothing); these tests give the holds as an
option or write the file into a world of their own."""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(T2))
sys.path.insert(0, T2)
sys.path.insert(0, os.path.join(HERE, "fixtures", "p2"))

from t2lib import plan  # noqa: E402
import world  # noqa: E402

WHY = "another session's guest, in progress"


def status(p, cell):
    c = next(c for c in p.cells if c.cell == cell)
    return c.status, "; ".join(c.reasons)


class Holds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t2-holds-")
        cls.w = world.World(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def plan(self, text, **opts):
        opts.setdefault("unhold", "")
        return self.w.plan(text, **opts)

    def test_every_family_and_variant(self):
        held = {"mint22": WHY}
        for text, cells in (("HoltTest2All plan", ["holt:mint22", "holt:mint22+bigger", "holt:mint22+oneshot"]),
                            ("SticTest2All rel4 plan", ["stic:mint22"]),
                            ("GuestTest2All plan", ["guest:mint22"])):
            p = self.plan(text, holds=held)
            for c in cells:
                with self.subTest(word=text, cell=c):
                    st, why = status(p, c)
                    self.assertEqual(st, "not-runnable")
                    self.assertIn(f"held: mint22: {WHY}", why)
            self.assertIn("held (testvm/test2/holds.json; T2_UNHOLD=GUEST runs one anyway): mint22", p.notes)
            others = [c for c in p.cells if "mint22" not in c.cell]
            self.assertTrue(others and all("held" not in "; ".join(c.reasons) for c in others), text)

    def test_a_named_word_is_held_too(self):
        p = self.plan("HoltTest2 mint22 debian13 plan", holds={"mint22": WHY})
        self.assertEqual(status(p, "holt:mint22")[0], "not-runnable")
        self.assertEqual(status(p, "holt:debian13")[0], "due")

    def test_unhold(self):
        held = {"mint22": WHY, "debian13": WHY}
        p = self.plan("HoltTest2 mint22 debian13 plan", holds=held, unhold="mint22")
        self.assertEqual([status(p, c)[0] for c in ("holt:mint22", "holt:debian13")], ["due", "not-runnable"])
        p = self.plan("HoltTest2 mint22 debian13 plan", holds=held, unhold="all")
        self.assertEqual([status(p, c)[0] for c in ("holt:mint22", "holt:debian13")], ["due", "due"])
        with mock.patch.dict(os.environ, {"T2_UNHOLD": "mint22, debian13"}):
            c = self.w.ctx(holds=held)
            self.assertEqual(c.holds, {})

    def test_the_file(self):
        path = os.path.join(self.w.repo, plan.HOLDS)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        self.assertEqual(self.w.ctx(unhold="").holds, {}, "no file: nothing held")
        with open(path, "w") as f:
            f.write('{"v": 1, "notes": "n", "guests": {"mint22": "%s"}}\n' % WHY)
        self.assertEqual(self.w.ctx(unhold="").holds, {"mint22": WHY})
        p = self.w.plan("GuestTest2 mint22 plan", unhold="")
        self.assertEqual(status(p, "guest:mint22")[0], "not-runnable")
        for bad in ('{"v": 1, "guests": {"mint22": ""}}', '{"v": 1, "guests": ["mint22"]}', '{"guests": {}}',
                    '{"v": 1, "guests": {"Mint 22": "x"}}', "{not json"):
            with self.subTest(bad=bad):
                with open(path, "w") as f:
                    f.write(bad)
                with self.assertRaises(plan.PlanError):
                    self.w.ctx(unhold="").holds


class TheRealHolds(unittest.TestCase):
    def test_it_checks(self):
        """The checkout's holds.json, if there is one, loads (t2 self-test runs this)."""
        plan.load_holds(os.path.join(REPO, plan.HOLDS))


if __name__ == "__main__":
    unittest.main()
