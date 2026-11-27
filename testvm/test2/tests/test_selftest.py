"""t2 self-test's unit tests, one process per file (2026-11-18): the summary line, the pool,
each file's own TMPDIR, UNIT_SERIAL's tests alone after the pool, no reruns."""

import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(TESTS)


def load_t2():
    loader = importlib.machinery.SourceFileLoader("t2_selftest_under_test", os.path.join(T2, "t2"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


t2 = load_t2()
sys.path.insert(0, T2)
from t2lib import copies, gate  # noqa: E402
from t2lib import run as runmod  # noqa: E402

PASSING = '''import os, tempfile, unittest
class T(unittest.TestCase):
    def test_own_tmp(self):
        self.assertIn("t2-selftest-%s-", os.path.basename(tempfile.gettempdir()))
        self.assertEqual(os.environ["TMPDIR"], tempfile.gettempdir())
    @unittest.skip("x")
    def test_skipped(self):
        pass
'''

FAILING = '''import unittest
class T(unittest.TestCase):
    def test_fails(self):
        self.assertEqual(1, 2)
    def test_errs(self):
        raise ValueError("boom")
'''


class UnitSummary(unittest.TestCase):
    def test_one_line_as_one_run_prints_it(self):
        s = t2.unit_summary
        self.assertEqual(s(["....\nRan 4 tests in 0.1s\n\nOK\n", "Ran 2 tests\n\nOK (skipped=2)\n"]), "OK (skipped=2)")
        self.assertEqual(s(["Ran 3 tests\n\nFAILED (failures=1, skipped=1)\n", "Ran 1 test\n\nFAILED (errors=1)\n",
                            "Ran 1 test\n\nOK (skipped=3, expected failures=1)\n"]),
                         "FAILED (failures=1, errors=1, skipped=4, expected failures=1)")
        self.assertEqual(s(["Ran 1 test\n\nOK\n"]), "OK")
        self.assertEqual(s(["Traceback ...\nKilled\n"]), "FAILED (errors=1)")   # no result line: it died


class UnitTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="t2-selftest-test-")
        for name, body in (("test_a.py", PASSING % "test_a"), ("test_b.py", FAILING), ("test_c.py", PASSING % "test_c")):
            with open(os.path.join(self.d, name), "w") as f:
                f.write(body)
        with open(os.path.join(self.d, "helper.py"), "w") as f:   # not a test file: not run
            f.write("raise SystemExit(9)\n")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_each_file_its_own_process_and_tmpdir(self):
        res = t2.unit_tests(sys.executable, dict(os.environ), tests=self.d, jobs=2)
        self.assertEqual([(n, rc) for n, rc, _, _ in res], [("test_a.py", 0), ("test_b.py", 1), ("test_c.py", 0)])
        self.assertEqual(t2.unit_summary([t for _, _, t, _ in res]), "FAILED (failures=1, errors=1, skipped=2)")
        self.assertIn("ValueError: boom", res[1][2])
        left = [n for n in os.listdir(tempfile.gettempdir()) if n.startswith(("t2-selftest-test_a-", "t2-selftest-test_b-",
                                                                               "t2-selftest-test_c-"))]
        self.assertEqual(left, [])   # each file's TMPDIR is removed after it

    def test_a_failure_in_the_pool_is_not_run_again(self):
        """A FAIL that would pass on a second run is a FLAKY-FAIL (docs/design/test2.md): the
        pool's exit decides, nothing reruns."""
        flag = os.path.join(self.d, "busy")
        open(flag, "w").close()
        with open(os.path.join(self.d, "test_b.py"), "w") as f:   # fails the first time only
            f.write("import os, unittest\nclass T(unittest.TestCase):\n    def test_t(self):\n"
                    f"        p = {flag!r}\n        busy = os.path.exists(p)\n        if busy: os.remove(p)\n"
                    "        self.assertFalse(busy)\n")
        res = t2.unit_tests(sys.executable, dict(os.environ), tests=self.d, jobs=3)
        self.assertEqual([(n, rc) for n, rc, _, _ in res], [("test_a.py", 0), ("test_b.py", 1), ("test_c.py", 0)])
        self.assertFalse(os.path.exists(flag))   # it ran once

    def test_serial_tests_run_alone_after_the_pool(self):
        """UNIT_SERIAL's entries (test_plan's wall-clock class) leave the pool and run after it,
        alone; a whole file listed runs only then."""
        log = os.path.join(self.d, "log")
        body = ("import os, time, unittest\n"
                "def mark(w):\n"
                f"    with open({log!r}, 'a') as f: f.write(w + '\\n')\n"
                "class Fast(unittest.TestCase):\n"
                "    def test_fast(self):\n        time.sleep(0.5); mark('%s.Fast')\n"
                "class Timed(unittest.TestCase):\n"
                "    def test_timed(self):\n        mark('%s.Timed')\n"
                "        self.assertIn('t2-selftest-%s-', os.path.basename(os.environ['TMPDIR']))\n")
        for n in ("test_d", "test_e"):
            with open(os.path.join(self.d, n + ".py"), "w") as f:
                f.write(body % (n, n, n))
        res = t2.unit_tests(sys.executable, dict(os.environ), tests=self.d, jobs=4,
                            serial=("test_d.Timed", "test_e"))
        self.assertEqual([(n, rc) for n, rc, _, _ in res],
                         [("test_a.py", 0), ("test_b.py", 1), ("test_c.py", 0), ("test_d.py", 0),
                          ("test_d.py (alone: test_d.Timed)", 0), ("test_e.py (alone: test_e)", 0)])
        with open(log) as f:
            order = f.read().split()
        self.assertEqual(order[-3:], ["test_d.Timed", "test_e.Fast", "test_e.Timed"])   # after the pool, alone
        self.assertEqual(sorted(order[:-3]), ["test_d.Fast"])                         # the pool: Timed left out
        self.assertIn("Ran 1 test", res[4][2])
        self.assertEqual(t2.unit_summary([t for _, _, t, _ in res]), "FAILED (failures=1, errors=1, skipped=2)")

    def test_a_serial_entry_that_names_no_test_fails(self):
        res = t2.unit_tests(sys.executable, dict(os.environ), tests=self.d, jobs=2,
                            serial=("test_c.Gone", "test_z.Perf"))
        self.assertEqual([(n, rc) for n, rc, _, _ in res][3:],
                         [("test_c.py (alone: test_c.Gone)", 1), ("test_z.py (alone: test_z.Perf)", 2)])
        self.assertIn("UNIT_SERIAL names no test: test_c.Gone", res[3][2])
        self.assertEqual(res[2][1], 0)   # test_c's own tests still ran in the pool

    def test_the_real_serial_list(self):
        """test_plan's 2 s plan timings (Performance), and they exist."""
        self.assertEqual(t2.UNIT_SERIAL, ("test_plan.Performance",))
        with open(os.path.join(TESTS, "test_plan.py")) as f:   # (the alone run fails too when it's gone)
            self.assertIn("\nclass Performance(", f.read())

    def test_more_dirs_share_one_pool(self):
        """TESTS may be several dirs (t2 self-test: test2's and backshot2's, UNIT_DIRS): one
        pool over all their files, the first dir's named bare, another's by its path; SERIAL
        names the first dir's tests."""
        e = tempfile.mkdtemp(prefix="t2-selftest-test-more-")
        self.addCleanup(shutil.rmtree, e, True)
        with open(os.path.join(e, "test_x.py"), "w") as f:
            f.write(PASSING % "test_x")
        with open(os.path.join(e, "test_a.py"), "w") as f:     # the same name as the first dir's: its own file
            f.write(FAILING)
        res = t2.unit_tests(sys.executable, dict(os.environ), tests=(self.d, e), jobs=3, serial=("test_c",))
        self.assertEqual([(n, rc) for n, rc, _, _ in res],
                         [("test_a.py", 0), ("test_b.py", 1), (os.path.join(e, "test_a.py"), 1),
                          (os.path.join(e, "test_x.py"), 0), ("test_c.py (alone: test_c)", 0)])
        self.assertEqual(t2.unit_summary([t for _, _, t, _ in res]), "FAILED (failures=2, errors=2, skipped=3)")

    def test_the_real_dirs(self):
        """t2 self-test runs test2's unit tests and backshot2's (BS2's own README runs them so too)."""
        self.assertEqual(t2.UNIT_DIRS, (TESTS, os.path.join(os.path.dirname(os.path.dirname(T2)), "testvm", "backshot2", "tests")))
        self.assertTrue(all(os.path.isdir(d) for d in t2.UNIT_DIRS))

    def test_jobs_from_the_environment(self):
        old = os.environ.get("T2_SELFTEST_JOBS")
        os.environ["T2_SELFTEST_JOBS"] = "junk"   # (not a number: the default)
        try:
            res = t2.unit_tests(sys.executable, dict(os.environ), tests=self.d)
        finally:
            if old is None:
                os.environ.pop("T2_SELFTEST_JOBS", None)
            else:
                os.environ["T2_SELFTEST_JOBS"] = old
        self.assertEqual(len(res), 3)


class LocalIO:
    """gate's Mac commands, scripted: local() answers by the command's last word."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def local(self, argv, cwd=None, env=None, timeout=None):
        self.calls.append((argv[-1], cwd))
        return self.answers[argv[-1]]


class EarlySelftest(unittest.TestCase):
    """gate:selftest's attempt 1 beside a composite's children (bko.py), taken by the gate child."""

    def setUp(self):
        self.mac = tempfile.mkdtemp(prefix="t2-early-")
        self.run = "t2g-20261118T2000-abcd"
        self.tree = os.path.join(self.mac, "trees", self.run)
        os.makedirs(os.path.join(self.tree, "testvm", "test2"))
        open(os.path.join(self.tree, "testvm", "test2", "t2"), "w").close()
        self.saved = (copies.make_tree, copies.tree_info_ok)
        self.made = []

        def make_tree(checkout, dest, pv=None, log=None):
            self.made.append(dest)
            return {"path": dest, "id": "6fff54b", "digest": "d" * 64}
        copies.make_tree = make_tree
        copies.tree_info_ok = lambda info: os.path.isdir(info.get("path") or "")

    def tearDown(self):
        copies.make_tree, copies.tree_info_ok = self.saved
        shutil.rmtree(self.mac, ignore_errors=True)

    def env(self, io, digest="d" * 64, path=None):
        logs = []
        e = argparse_ns(mac_root=self.mac, run=self.run, attempt_run=lambda a: self.run if a == 1 else f"{self.run}-r{a}",
                        mac_tree={"path": path or self.tree, "digest": digest}, checkout=path or self.tree, io=io,
                        log=logs.append)
        return e, logs

    def test_early_then_the_gate_child_takes_it(self):
        io = LocalIO({"self-test": (0, "self-test: all ok"), "--self-test": (1, "a\nFAIL: queue")})
        said = []
        doc = gate.early_selftest("/checkout", self.run, self.mac, log=said.append, io=io)
        self.assertEqual(self.made, [self.tree])                         # the gate child's own tree, made now
        rec = runmod.read_json(os.path.join(runmod.run_dir(self.run, self.mac), "mac-tree.json"))
        self.assertEqual(rec["path"], self.tree)                         # run.make_mac_tree adopts it
        self.assertTrue(all(cwd == self.tree for _, cwd in io.calls))    # run from that tree
        self.assertEqual(doc["res"]["t2-self-test"], [0, "self-test: all ok"])
        again = LocalIO({})                                              # the gate child runs nothing itself
        e, logs = self.env(again)
        o = gate.run_selftest(e, 1)
        self.assertEqual(again.calls, [])
        self.assertEqual((o.verdict, o.needs), ("FAIL", {"t2-self-test": "PASS", "drive-self-test": "FAIL"}))
        self.assertEqual((o.start, o.wall_s), (doc["start"], doc["wall_s"]))
        self.assertTrue(any("attempt 1 ran beside the children" in x for x in logs), logs)
        both = LocalIO({"self-test": (0, "ok"), "--self-test": (0, "ok")})
        e2, _ = self.env(both)
        self.assertEqual(gate.run_selftest(e2, 2).verdict, "PASS")       # attempt 2 always runs
        self.assertEqual(len(both.calls), 2)

    def test_another_tree_runs_it_again(self):
        io = LocalIO({"self-test": (1, "x"), "--self-test": (1, "y")})
        gate.early_selftest("/checkout", self.run, self.mac, log=lambda m: None, io=io)
        fresh = LocalIO({"self-test": (0, "ok"), "--self-test": (0, "ok")})
        e, logs = self.env(fresh, digest="e" * 64)                       # the checkout changed: another tree
        self.assertEqual(gate.run_selftest(e, 1).verdict, "PASS")
        self.assertEqual(len(fresh.calls), 2)
        self.assertTrue(any("of another tree" in x for x in logs), logs)

    def test_a_stopped_run_keeps_nothing(self):
        io = LocalIO({"self-test": (130, "KeyboardInterrupt"), "--self-test": (0, "ok")})
        self.assertIsNone(gate.early_selftest("/checkout", self.run, self.mac, log=lambda m: None,
                                              cancelled=lambda: True, io=io))
        self.assertFalse(os.path.exists(os.path.join(runmod.run_dir(self.run, self.mac), "gate", "selftest", gate.EARLY)))

    def test_a_resume_keeps_the_tree_it_made(self):
        io = LocalIO({"self-test": (0, "ok"), "--self-test": (0, "ok")})
        gate.early_selftest("/checkout", self.run, self.mac, log=lambda m: None, io=io)
        gate.early_selftest("/checkout", self.run, self.mac, log=lambda m: None, io=io)
        self.assertEqual(self.made, [self.tree])                         # still there, unchanged: not made again


def argparse_ns(**kw):
    import argparse
    return argparse.Namespace(**kw)


if __name__ == "__main__":
    unittest.main()
