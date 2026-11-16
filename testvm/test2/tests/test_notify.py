"""t2's completion notification (t2lib/notify.py, the hook in t2's main): one pushover.sh call by the
outermost t2 once a run's work began, whatever its end; never on a plan, a refusal before any work,
a nested t2, T2_NOTIFY=0 or a test root it isn't forced on.  The client is always a fake here
(T2_PUSHOVER), which records its arguments."""

import importlib.machinery
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
T2_DIR = os.path.dirname(HERE)
T2 = os.path.join(T2_DIR, "t2")
sys.path.insert(0, T2_DIR)

from t2lib import notify   # noqa: E402

RUN = "t2s-20261116T1010-ab12"
FAKE = '#!/bin/sh\nfor a in "$@"; do printf "%s\\0" "$a"; done >> "{log}"\nprintf "\\n\\0" >> "{log}"\nexit {rc}\n'


def load_t2():
    loader = importlib.machinery.SourceFileLoader("t2_under_test", T2)
    spec = importlib.util.spec_from_loader("t2_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


t2 = load_t2()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2notify-")
        self.log = os.path.join(self.tmp, "calls")
        self.fake = self.client(0)
        env = {k: v for k, v in os.environ.items() if k not in ("T2_NOTIFIED_BY", "T2_NOTIFY", "T2_PUSHOVER")}
        env.update(T2_MAC_ROOT=os.path.join(self.tmp, "mac"), T2_NOTIFY="1", T2_PUSHOVER=self.fake)
        self.env = env
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def client(self, rc, name="pushover.sh", body=None):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as f:
            f.write(body or FAKE.format(log=self.log, rc=rc))
        os.chmod(path, 0o755)
        return path

    def calls(self):
        """[[arg, ...]] of each fake client call."""
        try:
            with open(self.log) as f:
                parts = f.read().split("\0")
        except OSError:
            return []
        out, cur = [], []
        for p in parts:
            if p == "\n":
                out.append(cur)
                cur = []
            elif p:
                cur.append(p)
        return out


class Main(Base):
    """t2's main with a fake word: the hook around the dispatch."""

    def run_main(self, fake, argv=("SticTest2", "rel8")):
        out, errs = io.StringIO(), io.StringIO()
        with mock.patch.object(t2, "cmd_word", fake), redirect_stdout(out), redirect_stderr(errs):
            rc = t2.main(list(argv))
        return rc, out.getvalue(), errs.getvalue()

    @staticmethod
    def word(rc, begin=True, detail=None, raise_=None):
        def fake(argv):
            if begin:
                notify.begun(RUN, "SticTest2")
                if detail:
                    notify.outcome(RUN, detail)
            print("the run's own output")
            if raise_ is not None:
                raise raise_
            return rc
        return fake

    def test_pass(self):
        rc, out, errs = self.run_main(self.word(0, detail="3 PASS"))
        self.assertEqual((rc, out, errs), (0, "the run's own output\n", ""))
        (c,) = self.calls()
        self.assertEqual(c[:3], ["-T", "BackOtter", "-m"])
        self.assertRegex(c[3], r"\ACommand-line test is complete\. Send logs to Claude\. "
                               rf"SticTest2 run {RUN}: PASS: 3 PASS \(\d+s\)\Z")
        self.assertNotIn("T2_NOTIFIED_BY", os.environ)   # (the outermost t2 took its marker back)

    def test_fail(self):
        rc, out, errs = self.run_main(self.word(1, detail="2 FAIL, 5 PASS"))
        self.assertEqual((rc, errs), (1, ""))
        (c,) = self.calls()
        self.assertIn(f"SticTest2 run {RUN}: FAIL: 2 FAIL, 5 PASS (", c[3])

    def test_refused_after_work_began(self):
        rc, _, _ = self.run_main(self.word(2))
        self.assertEqual(rc, 2)
        self.assertIn(f"run {RUN}: REFUSED (", self.calls()[0][3])

    def test_interrupt_and_error(self):
        rc, _, errs = self.run_main(self.word(0, raise_=KeyboardInterrupt()))
        self.assertEqual(rc, 130)
        self.assertIn("interrupted", errs)
        self.assertIn(": INTERRUPTED (", self.calls()[0][3])
        with self.assertRaises(ValueError):
            self.run_main(self.word(0, raise_=ValueError("boom")))
        self.assertIn(": ERROR (ValueError: boom) (", self.calls()[1][3])

    def test_not_before_work(self):
        # a plan, or a run refused before it began (a held lock, a PlanError): no notification
        rc, _, errs = self.run_main(self.word(2, begin=False), ["SticTest2", "plan"])
        self.assertEqual((rc, errs), (2, ""))
        self.assertEqual(self.calls(), [])

    def test_not_for_other_commands(self):
        out, errs = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(errs):
            self.assertEqual(t2.main(["--help"]), 0)
            self.assertEqual(t2.main(["ledger", "nope"]), 2)
        self.assertEqual(self.calls(), [])

    def test_off(self):
        os.environ["T2_NOTIFY"] = "0"
        self.assertEqual(self.run_main(self.word(0))[0], 0)
        os.environ["T2_NOTIFY"] = "1"
        os.environ["T2_PUSHOVER"] = "none"
        self.assertEqual(self.run_main(self.word(1))[:3:2], (1, ""))
        self.assertEqual(self.calls(), [])

    def test_test_root_unforced(self):
        del os.environ["T2_NOTIFY"]
        self.assertEqual(self.run_main(self.word(0))[0], 0)
        os.environ["T2_NOTIFY"] = "1"
        del os.environ["T2_PUSHOVER"]           # forced, but a test root never reaches a client off PATH
        self.assertEqual(self.run_main(self.word(0))[:3:2], (0, ""))
        self.assertEqual(self.calls(), [])

    def test_nested(self):
        os.environ["T2_NOTIFIED_BY"] = "12345"
        self.assertEqual(self.run_main(self.word(1))[0], 1)
        self.assertEqual(self.calls(), [])
        self.assertEqual(os.environ["T2_NOTIFIED_BY"], "12345")

    def test_marker_inherited(self):
        seen = {}

        def fake(argv):
            notify.begun(RUN, "SticTest2")
            seen["env"] = os.environ.get("T2_NOTIFIED_BY")
            seen["child"] = subprocess.run([sys.executable, "-c", "import os; print(os.environ['T2_NOTIFIED_BY'])"],
                                           capture_output=True, text=True).stdout.strip()
            return 0
        self.run_main(fake)
        self.assertEqual(seen["env"], str(os.getpid()))
        self.assertEqual(seen["child"], str(os.getpid()))
        self.assertEqual(len(self.calls()), 1)

    def test_bad_clients_keep_the_exit(self):
        os.environ["T2_PUSHOVER"] = self.client(7, name="failing.sh")
        rc, out, errs = self.run_main(self.word(1))
        self.assertEqual((rc, out), (1, "the run's own output\n"))
        self.assertEqual(errs.count("\n"), 1)
        self.assertIn("exited 7", errs)
        os.environ["T2_PUSHOVER"] = os.path.join(self.tmp, "missing.sh")
        rc, out, errs = self.run_main(self.word(0))
        self.assertEqual((rc, out), (0, "the run's own output\n"))
        self.assertEqual(errs.count("\n"), 1)
        self.assertIn("not an executable file", errs)
        os.environ["T2_PUSHOVER"] = self.client(0, name="slow.sh", body="#!/bin/sh\nexec sleep 30\n")
        with mock.patch.object(notify, "TIMEOUT_S", 0.5):
            rc, _, errs = self.run_main(self.word(1))
        self.assertEqual(rc, 1)
        self.assertIn("took over 0.5 s", errs)


class Message(unittest.TestCase):
    def test_format(self):
        m = notify.message(RUN, "HoltTest2D", 1, detail="1 FAIL", secs=3725)
        self.assertEqual(m, "Command-line test is complete. Send logs to Claude. "
                            f"HoltTest2D run {RUN}: FAIL: 1 FAIL (1h 02m)")
        self.assertTrue(notify.message(RUN, "x", 0, secs=59).endswith(": PASS (59s)"))
        self.assertTrue(notify.message(RUN, "x", 0, secs=61).endswith(": PASS (1m 01s)"))
        self.assertTrue(notify.message(RUN, "x", 9).endswith(": exit 9 (0s)"))

    def test_json_safe(self):   # pushover.sh embeds -m in JSON unescaped
        m = notify.message(RUN, "x", None, exc=RuntimeError('a "b"\\c\nd\te'))
        self.assertNotRegex(m, r'["\\\x00-\x1f]')
        self.assertIn("a 'b'/c d e", m)
        self.assertLessEqual(len(notify.message(RUN, "x", 1, detail="y" * 2000)), notify.LIMIT)
        self.assertLessEqual(len(notify.message(RUN, "x", 3, exc=RuntimeError("z" * 2000))), notify.LIMIT)

    def test_client_search(self):
        with mock.patch.dict(os.environ, {"T2_PUSHOVER": ""}), mock.patch.object(notify.shutil, "which", return_value=None), \
                mock.patch.object(notify, "FALLBACK", ("/nonexistent/a/pushover.sh",)):
            path, why = notify.client()
        self.assertIsNone(path)
        self.assertIn("no pushover.sh", why)


class Batch(Base):
    """The real `t2 batch` CLI in a test root: one notification for the batch; none for its plan."""

    def t2(self, *args):
        env = dict(self.env, T2_SSH="/usr/bin/false", T2_BATCH_TICK="0.3", T2_BATCH_POLL="0.1",
                   T2_BATCH_STOP_WAIT="2", T2_BATCH_MIN_FREE_GB="0", PYTHONDONTWRITEBYTECODE="1")
        facts = os.path.join(self.tmp, "facts.json")
        with open(facts, "w") as f:
            f.write('{"chopin": [64, 64], "cinqcent": [64, 64], "copland": [64, 64], "cage": [64, 64]}')
        env["T2_BATCH_FACTS"] = facts
        os.makedirs(env["T2_MAC_ROOT"], exist_ok=True)
        return subprocess.run([sys.executable, "-B", T2, "batch"] + list(args), cwd=self.tmp, env=env,
                              capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)

    def test_batch(self):
        mark = os.path.join(self.tmp, "mark")
        path = os.path.join(self.tmp, "n.batch")
        with open(path, "w") as f:
            f.write(f"cmd ok -- true\ncmd bad -- false\ncmd mark -- sh -c 'echo $T2_NOTIFIED_BY > {mark}'\n")
        r = self.t2(path, "plan", "--dirty-ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.calls(), [])
        r = self.t2(path, "--dirty-ok")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        (c,) = self.calls()
        self.assertRegex(c[3], r"Claude\. t2 batch n\.batch run bt-\S+: FAIL: 1 FAIL, 2 PASS \(\d+s\)\Z")
        with open(mark) as f:
            self.assertRegex(f.read().strip(), r"^\d+$")   # (the children see the outermost t2's marker)


if __name__ == "__main__":
    unittest.main()
