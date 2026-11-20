#!/usr/bin/env python3
"""Tests of rust-filter.py on hand-made inputs: python3 -I test_rust_filter.py"""

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("rust_filter", os.path.join(HERE, "rust-filter.py"))
rf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rf)

SCOPE = """\
# rust-scope.sh: test
# name=rustic_core commit=0123456789abcdef0123 stock=v1 abc date=x
# PATH START-END
crates/core/src/a.rs 10-20
crates/core/src/a.rs 30-30
crates/core/src/new.rs 1-50
# per file: lines OURS TOTAL HOW PATH
# lines     12    100 blame crates/core/src/a.rs
# lines     50     50 added crates/core/src/new.rs
# lines      0     80 blame crates/core/src/upstream_only.rs
"""


def jmsg(path, line, code="clippy::doc_markdown", level="warning", msg="item in documentation is missing backticks",
         col=5, expansion=None):
    span = {"file_name": path, "line_start": line, "column_start": col, "is_primary": True}
    if expansion:
        span = {"file_name": "<::core::macros::panic>", "line_start": 1, "column_start": 1, "is_primary": True,
                "expansion": {"span": expansion}}
    return json.dumps({"reason": "compiler-message", "package_id": "x",
                       "message": {"level": level, "message": msg, "code": {"code": code} if code else None,
                                   "spans": [span], "rendered": "..."}})


class FilterTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.scope = self.write("scope.txt", SCOPE)

    def write(self, name, text):
        p = os.path.join(self.d, name)
        with open(p, "w") as f:
            f.write(text)
        return p

    def run_filter(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = rf.main(list(args))
        return rc, buf.getvalue()

    def jsonout(self, *args):
        out = os.path.join(self.d, "out.json")
        rc, text = self.run_filter("--scope", self.scope, "--json", out, *args)
        self.assertEqual(rc, 0, text)
        with open(out) as f:
            return json.load(f), text

    def test_scope_parse(self):
        s = rf.Scope(self.scope)
        self.assertTrue(s.ours("crates/core/src/a.rs", 10))
        self.assertTrue(s.ours("crates/core/src/a.rs", 20))
        self.assertTrue(s.ours("crates/core/src/a.rs", 30))
        self.assertFalse(s.ours("crates/core/src/a.rs", 21))
        self.assertFalse(s.ours("crates/core/src/a.rs", 9))
        self.assertIn("crates/core/src/upstream_only.rs", s.files)
        self.assertEqual(s.info["commit"], "0123456789abcdef0123")

    def test_json_classes(self):
        inp = self.write("clippy.json", "\n".join([
            jmsg("crates/core/src/a.rs", 15),                       # ours
            jmsg("crates/core/src/a.rs", 25),                       # upstream line of a touched file
            jmsg("crates/core/src/upstream_only.rs", 3),            # upstream line, touched file
            jmsg("crates/core/src/other.rs", 3),                    # a file we never touched
            jmsg("/home/m/.cargo/registry/src/x/dep.rs", 3),        # outside
            jmsg("crates/core/src/new.rs", 7, code="clippy::too_many_lines", msg="this function has too many lines (110/100)"),
            jmsg("crates/core/src/new.rs", 7, code="clippy::too_many_lines", msg="this function has too many lines (110/100)"),
            json.dumps({"reason": "compiler-artifact"}),
            jmsg("crates/core/src/new.rs", 9, code=None, level="warning", msg="3 warnings emitted").replace(
                '"spans": [{', '"spans": [], "x": [{'),
            "not json at all {",
        ]) + "\n")
        doc, text = self.jsonout("--tool", "clippy", inp)
        self.assertEqual(doc["ours"], 2)
        self.assertEqual(doc["upstream"], 2)
        self.assertEqual(doc["other_file"], 1)
        self.assertEqual(doc["outside"], 1)
        self.assertEqual(doc["duplicates"], 1)
        self.assertEqual(doc["by_lint"], {"clippy::doc_markdown": 1, "clippy::too_many_lines": 1})
        self.assertIn("crates/core/src/new.rs\tclippy::too_many_lines\tthis function has too many lines (N/N)", doc["keys"])
        self.assertIn("crates/core/src/a.rs:15:5: warning[clippy::doc_markdown]:", text)

    def test_macro_expansion_followed(self):
        inp = self.write("m.json", jmsg(None, 0, expansion={"file_name": "crates/core/src/a.rs", "line_start": 12,
                                                             "column_start": 9, "is_primary": True}) + "\n")
        doc, _ = self.jsonout(inp)
        self.assertEqual(doc["items"][0].split(":")[:2], ["crates/core/src/a.rs", "12"])

    def test_short_format_and_roots(self):
        inp = self.write("short.txt", "\n".join([
            "/bkoscratch/bko-audit/src/rustic_core-abc/crates/core/src/a.rs:11:1: warning: unused import: `x`",
            "crates/core/src/a.rs:30:2: error[E0308]: mismatched types",
            "./crates/core/src/new.rs:1:1: warning: missing docs",
            "../rustic_core/crates/core/src/a.rs:11:1: warning: path dependency",
            "warning: `rustic_core` (lib) generated 3 warnings",
        ]) + "\n")
        doc, _ = self.jsonout("--root", "/bkoscratch/bko-audit/src/rustic_core-abc", inp)
        self.assertEqual(doc["ours"], 3)
        self.assertEqual(doc["errors"], 1)
        self.assertEqual(doc["outside"], 1)
        self.assertEqual(doc["by_lint"].get("E0308"), 1)
        # without a --root, the absolute path is matched by its suffix
        doc, _ = self.jsonout(inp)
        self.assertEqual(doc["ours"], 3)
        # with another root, it is outside
        doc, _ = self.jsonout("--root", "/elsewhere", inp)
        self.assertEqual(doc["ours"], 2)

    def test_fmt_hunks(self):
        inp = self.write("fmt.txt", "\n".join([
            "Diff in /t/crates/core/src/a.rs:9:",
            " context line 9",
            "-old line 10",
            "+new line 10",
            " context 11",
            "",
            "Diff in /t/crates/core/src/a.rs at line 40:",   # the old header form
            " context 40",
            "-old 41",
            "-old 42",
            "+new",
            "Diff in /t/crates/core/src/a.rs:29:",           # a pure insertion before line 30
            " context 29",
            "+inserted",
            " context 30",
            "Diff in /t/crates/core/src/upstream_only.rs:1:",
            "-x",
            "+y",
            "    Finished something",
        ]) + "\n")
        doc, text = self.jsonout("--root", "/t", "--tool", "fmt", inp)
        self.assertEqual(doc["ours"], 2)          # the hunks at 10 and the insertion at 30
        self.assertEqual(doc["upstream"], 2)      # line 41-42 and upstream_only.rs
        self.assertEqual(doc["fmt"]["hunks"], 2)
        self.assertEqual(doc["fmt"]["minus"], 1)
        self.assertEqual(doc["fmt"]["plus"], 2)
        self.assertEqual(doc["fmt"]["by_file"]["crates/core/src/a.rs"], [2, 1, 2])
        self.assertIn("-- rustfmt", text)

    def test_ansi_stripped(self):
        inp = self.write("c.txt", "\x1b[1mcrates/core/src/a.rs:12:1: warning: thing\x1b[0m\n")
        doc, _ = self.jsonout(inp)
        self.assertEqual(doc["ours"], 1)

    def test_files_and_lints(self):
        inp = self.write("p.json", "\n".join([
            jmsg("crates/core/src/new.rs", 2, code="clippy::indexing_slicing"),
            jmsg("crates/core/src/new.rs", 3, code="clippy::doc_markdown"),
            jmsg("crates/core/src/a.rs", 12, code="clippy::indexing_slicing"),
        ]) + "\n")
        doc, _ = self.jsonout("--files", "crates/core/src/new*,x/*", "--lints", "clippy::indexing-slicing", inp)
        self.assertEqual(doc["ours"], 1)
        self.assertEqual(doc["by_file"], {"crates/core/src/new.rs": 1})

    def test_groups(self):
        groups = self.write("help.txt", "\n".join([
            "Lint checks provided by rustc:",
            "    name  default  meaning",
            "Lint groups provided by rustc:",
            "                       name  sub-lints",
            "                       ----  ---------",
            "                   warnings  all lints that are set to issue warnings",
            "                     unused  unused-imports, unused-variables",
            "Lint groups loaded by this crate:",
            "                clippy::all  clippy::doc-lazy-continuation, clippy::too-many-arguments",
            "           clippy::pedantic  clippy::doc-markdown, clippy::too-many-lines",
            "              clippy::style  clippy::doc-lazy-continuation",
        ]) + "\n")
        inp = self.write("g.json", "\n".join([
            jmsg("crates/core/src/new.rs", 2, code="clippy::doc_markdown"),
            jmsg("crates/core/src/new.rs", 3, code="clippy::doc_lazy_continuation"),
            jmsg("crates/core/src/new.rs", 4, code="unused_imports"),
            jmsg("crates/core/src/new.rs", 5, code="missing_docs"),
            jmsg("crates/core/src/new.rs", 6, code="rustdoc::broken_intra_doc_links"),
        ]) + "\n")
        doc, _ = self.jsonout("--groups", groups, inp)
        self.assertEqual(doc["by_group"], {"clippy::pedantic": 1, "clippy::style": 1, "unused": 1,
                                           "rustc": 1, "rustdoc": 1})

    def test_compare(self):
        old = {"keys": {"a.rs\tclippy::x\tm": 1, "b.rs\trustfmt\t": 2}}
        new = {"keys": {"a.rs\tclippy::x\tm": 2, "b.rs\trustfmt\t": 1, "c.rs\tclippy::y\tn": 1}}
        od, nd = os.path.join(self.d, "old"), os.path.join(self.d, "new")
        os.mkdir(od)
        os.mkdir(nd)
        for d, doc in ((od, old), (nd, new)):
            with open(os.path.join(d, "clippy-linux.json"), "w") as f:
                json.dump(doc, f)
        rc, text = self.run_filter("--compare", od, nd)
        self.assertEqual(rc, 1)
        self.assertIn("new clippy-linux: a.rs: clippy::x: m (1 -> 2)", text)
        self.assertIn("c.rs: clippy::y", text)
        self.assertNotIn("b.rs", text)
        rc, text = self.run_filter("--compare", nd, nd)
        self.assertEqual(rc, 0)

    def test_bad_scope_line(self):
        bad = self.write("bad.txt", "x.rs ten-twenty\n")
        inp = self.write("i.txt", "x.rs:1:1: warning: y\n")
        with self.assertRaises(SystemExit):
            self.run_filter("--scope", bad, inp)


if __name__ == "__main__":
    unittest.main()
