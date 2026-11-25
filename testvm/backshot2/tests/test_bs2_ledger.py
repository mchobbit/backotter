"""When bs2 writes Test2 ledger rows (bs2lib/ledger.py recording(), 2026-11-25): only for a
BackShot2 word that runs (BS2_WORD one of WORDS) or when asked (BS2_LEDGER=1, as DEFAULT_WORD);
never under BS2_NO_LEDGER; an unknown BS2_WORD is refused; each no but BS2_NO_LEDGER's says so on
stderr once.  And the Mac's bs2 sets BS2_WORD for each word and passes it to the hosts' commands.
Nothing here reaches a host (the ledger is a temporary T2_LEDGER):

    python3 -m unittest discover -s testvm/backshot2/tests
"""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BS2_DIR = os.path.dirname(HERE)
sys.path.insert(0, BS2_DIR)
from bs2lib import allrun as AR, ledger as L  # noqa: E402


def load(name, file):
    loader = importlib.machinery.SourceFileLoader(name, os.path.join(BS2_DIR, file))
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


B = load("bs2_ledger_cli", "bs2")
ENV = ("BS2_WORD", "BS2_LEDGER", "BS2_NO_LEDGER")
BINS = {"linux/bko-holt": "sha256:" + "a" * 64, "bs2_plan": "sha256:" + "b" * 64, "linux/rustic-bko": "sha256:" + "c" * 64}


class Recording(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bs2-ledger-")
        self.path = os.path.join(self.tmp, "host.jsonl")
        self.env = mock.patch.dict(os.environ, {"T2_LEDGER": self.path})
        self.env.start()
        for v in ENV:
            os.environ.pop(v, None)
        L._noted.clear()

    def tearDown(self):
        self.env.stop()
        L._noted.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, **env):
        """write_rows() of one PASS case under ENV: (the rows the ledger file has, stderr)."""
        os.environ.update(env)
        case = {"source": "debian13@gen:x", "edits": [], "target": {}}
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            L.write_rows("bs2a-x", "debian13", "gen:" + "d" * 64, [("debian13-x", case, "PASS", [])], 1, 60, BINS)
        try:
            with open(self.path) as f:
                rows = [json.loads(x) for x in f if x.strip()]
        except FileNotFoundError:
            rows = []
        return rows, err.getvalue()

    def test_a_running_word_writes(self):
        for w in L.WORDS:
            with self.subTest(word=w):
                if os.path.exists(self.path):
                    os.remove(self.path)
                rows, err = self.write(BS2_WORD=w)
                self.assertEqual([r["word"] for r in rows], [w], err)
                self.assertEqual(rows[0]["family"], "bs2")
                self.assertEqual(err, "")

    def test_no_word_writes_nothing_and_says_so(self):
        rows, err = self.write()
        self.assertEqual(rows, [])
        self.assertIn("bs2: no ledger rows written", err)
        self.assertIn("BS2_WORD isn't set", err)
        self.assertIn("set BS2_LEDGER=1 to record", err)
        self.assertEqual(err.count("\n"), 1, "one note")
        # once a process: a second case's skip is quiet
        rows, err = self.write()
        self.assertEqual((rows, err), ([], ""))

    def test_bs2_ledger_writes_as_the_default_word(self):
        rows, err = self.write(BS2_LEDGER="1")
        self.assertEqual([r["word"] for r in rows], [L.DEFAULT_WORD], err)
        self.assertEqual(err, "")
        rows, err = self.write(BS2_LEDGER="0")   # (only =1 asks)
        self.assertEqual(len(rows), 1)

    def test_bs2_ledger_with_a_word_names_the_word(self):
        rows, _ = self.write(BS2_LEDGER="1", BS2_WORD="BackShot2All")
        self.assertEqual([r["word"] for r in rows], ["BackShot2All"])

    def test_no_ledger_wins_silently(self):
        for env in ({"BS2_WORD": "BackShot2"}, {"BS2_LEDGER": "1"}, {"BS2_WORD": "BackShot2", "BS2_LEDGER": "1"}, {}):
            with self.subTest(env=env):
                for v in ENV:
                    os.environ.pop(v, None)
                L._noted.clear()
                rows, err = self.write(BS2_NO_LEDGER="1", **env)
                self.assertEqual((rows, err), ([], ""))

    def test_an_unknown_word_is_refused(self):
        for env in ({"BS2_WORD": "fix-xfslog"}, {"BS2_WORD": "backshot2", "BS2_LEDGER": "1"}):
            with self.subTest(env=env):
                for v in ENV:
                    os.environ.pop(v, None)
                L._noted.clear()
                rows, err = self.write(**env)
                self.assertEqual(rows, [])
                self.assertIn(f"BS2_WORD={env['BS2_WORD']!r} isn't a BackShot2 word", err)

    def test_the_writers_word_is_checked_too(self):
        os.environ["BS2_WORD"] = "BackShot2"
        self.assertEqual(L.recording("BackShot2All"), (True, None))
        self.assertFalse(L.recording("nope")[0])

    def test_done_is_unchanged(self):
        """done() (the PASS skip) still reads the ledger with no word set."""
        self.write(BS2_WORD="BackShot2")
        os.environ.pop("BS2_WORD")
        from bs2lib import case as C
        h = C.case_hash({"source": "debian13@gen:x", "edits": [], "target": {}})
        self.assertTrue(L.done("debian13", h, 1, "gen:" + "d" * 64, BINS, path=self.path))


class Words(unittest.TestCase):
    def test_the_words_are_bs2s(self):
        self.assertIn(AR.WORD, L.WORDS)
        self.assertEqual(L.DEFAULT_WORD, "BackShot2Alpine")
        sys.path.insert(0, os.path.join(os.path.dirname(BS2_DIR), "test2"))
        from t2lib import batchkinds
        self.assertEqual(tuple(batchkinds.BS2_WORDS), tuple(L.WORDS))

    def run_main(self, *argv, **patches):
        """bs2's main() with ARGV, PATCHES replacing what would reach a host; each patched
        function records reuse_env() (what the hosts' commands are prefixed with) when called."""
        seen = []
        ps = [mock.patch.object(B, n, (lambda *a, **k: seen.append(B.reuse_env()))) for n in patches]
        with mock.patch.dict(os.environ), mock.patch.dict(B.COUNTS), mock.patch.object(sys, "argv", ["bs2"] + list(argv)), \
                contextlib.redirect_stdout(io.StringIO()):
            for v in ENV:
                os.environ.pop(v, None)
            for p in ps:
                p.start()
            try:
                B.main()
                cmds = (B.reserve_tier1("/x.case"), B.root("bs2-host tier0-cases /x.case"), B.light("chopin", "x", "t"))
            finally:
                for p in ps:
                    p.stop()
        return seen, cmds

    def test_each_word_passes_bs2_word_to_the_hosts(self):
        for argv, fn in ((("BackShot2Alpine", "4"), "run_words"), (("BackShot2", "debian13", "4"), "run_words"),
                         (("BackShot2", "corpus"), "corpus"), (("BackShot2All",), "all_start"),
                         (("BackShot2All", "corpus", "bs2all-20261125-100000"), "all_corpus"),
                         (("BackShot2All", "leftovers", "bs2all-20261125-100000"), "all_leftovers")):
            with self.subTest(argv=argv):
                seen, cmds = self.run_main(*argv, **{fn: True})
                w = f"BS2_WORD={argv[0]} "
                self.assertEqual(len(seen), 1)
                self.assertIn(w, seen[0])
                for c in cmds:   # the corpus's and a replay's tier 1, root()'s tier 0, light()'s draws
                    self.assertIn(w, c)

    def test_no_word_passes_none(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("BS2_WORD", None)
            os.environ.pop("BS2_LEDGER", None)
            self.assertNotIn("BS2_WORD", B.reuse_env())
            self.assertNotIn("BS2_LEDGER", B.reuse_env())
            os.environ["BS2_LEDGER"] = "1"   # (a replay's or soak's rows, asked for on the Mac)
            self.assertIn("BS2_LEDGER=1 ", B.reserve_tier1("/x.case"))


if __name__ == "__main__":
    unittest.main()
