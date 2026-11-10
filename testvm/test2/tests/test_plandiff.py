"""U6.3: t2lib/plandiff.py splits golden plans into stages as
holt/tests/common/mod.rs does (golden_plans.rs's diff.json)."""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from t2lib import plandiff  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
CORPUS = os.path.join(REPO, "holt", "tests", "corpus")

# The same text as golden_plans.rs's golden_sections_and_stages.
OLD = ("# bko-holt load plan for layout.json\n# Source: x\n\n# ---- partition ----\nwipefs --all /dev/sdq\n\n"
       "# ---- format ----\nmkfs.ext4 /dev/sdq1\n\n# ---- close ----\numount /mnt/bko-target\n\n"
       "# Nothing was written.  To run it: ...\n# golden_plans.rs: exit 0, nothing on stderr\n")


def corpus_plans():
    out = []
    if os.path.isdir(CORPUS):
        for g in sorted(os.listdir(CORPUS)):
            d = os.path.join(CORPUS, g)
            if os.path.isdir(d):
                out += [os.path.join(d, n) for n in sorted(os.listdir(d)) if n.startswith("plan") and n.endswith(".txt")]
    return out


class Sections(unittest.TestCase):
    def test_names(self):
        self.assertEqual([n for n, _ in plandiff.sections(OLD)], ["header", "partition", "format", "close", "trailer"])
        secs = dict(plandiff.sections(OLD))
        self.assertEqual(secs["header"], ["# bko-holt load plan for layout.json", "# Source: x", ""])
        self.assertEqual(secs["trailer"][0], "# Nothing was written.  To run it: ...")

    def test_stages_changed(self):
        self.assertEqual(plandiff.stages(OLD, OLD), [])
        self.assertEqual(plandiff.stages(OLD, OLD.replace("mkfs.ext4", "mkfs.xfs")), ["format"])
        self.assertEqual(plandiff.stages(OLD, OLD.replace("exit 0", "exit 4")), ["trailer"])
        self.assertEqual(plandiff.stages(OLD, OLD.replace("# Source: x", "# Source: y")), ["header"])
        added = OLD.replace("\n\n# ---- format", "\n\n# ---- assemble ----\nmdadm --create /dev/md/0\n\n# ---- format")
        self.assertEqual(plandiff.stages(OLD, added), ["assemble"])
        self.assertEqual(plandiff.stages(added, OLD), ["assemble"])
        both = OLD.replace("wipefs", "sgdisk").replace("umount /mnt", "umount -R /mnt")
        self.assertEqual(plandiff.stages(OLD, both), ["partition", "close"])
        odd = OLD.replace("# ---- close ----", "# ---- verify ----")
        self.assertEqual(plandiff.stages(OLD, odd), ["close", "verify"])

    def test_lines_changed(self):
        self.assertEqual(plandiff.lines_changed(OLD, OLD), 0)
        self.assertEqual(plandiff.lines_changed(OLD, OLD.replace("mkfs.ext4", "mkfs.xfs")), 2)
        added = OLD.replace("\n\n# ---- format", "\n\n# ---- assemble ----\nmdadm --create /dev/md/0\n\n# ---- format")
        self.assertEqual(plandiff.lines_changed(OLD, added), 3)
        self.assertEqual(plandiff.lines_changed("", "a\nb\n"), 2)
        self.assertEqual(plandiff.lines_changed("a\nb\nc\n", "a\nc\n"), 1)

    def test_no_stages(self):
        refused = "# golden_plans.rs: exit 3; stderr:\n#   bko-holt load: layout.json: not a bko-holt dump\n"
        self.assertEqual([n for n, _ in plandiff.sections(refused)], ["header", "trailer"])
        self.assertEqual(plandiff.stages(OLD, refused), ["header", "partition", "format", "close", "trailer"])

    def test_headers(self):
        self.assertEqual(plandiff.stage_header("# ---- mount ----"), "mount")
        for not_one in ("# ---- a b ----", "# ---- Mount ----", "# ----  ----", "## ---- mount ----", "# ---- mount ---- "):
            self.assertIsNone(plandiff.stage_header(not_one), not_one)
        # A stage header after the trailer starts is the trailer's text.
        self.assertEqual([n for n, _ in plandiff.sections("# ---- boot ----\nx\n# Nothing was written.\n")], ["header", "boot", "trailer"])

    def test_variant_stages(self):
        self.assertTrue(plandiff.needs_variants(["header", "assemble"]))
        self.assertTrue(plandiff.needs_variants(["close"]))
        self.assertFalse(plandiff.needs_variants(["header", "format", "mount", "restore", "boot", "trailer"]))

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a"), os.path.join(d, "b")
            with open(a, "w") as f:
                f.write(OLD)
            with open(b, "w") as f:
                f.write(OLD.replace("mkfs.ext4", "mkfs.xfs"))
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(plandiff.main([a, b]), 1)
            self.assertEqual(out.getvalue(), "format\n2 lines changed\n")
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(plandiff.main([a, a, "--json"]), 0)
            self.assertIn('"stages": []', out.getvalue())
            with redirect_stderr(io.StringIO()):
                self.assertEqual(plandiff.main([a]), 2)


@unittest.skipUnless(corpus_plans(), "no golden plans in holt/tests/corpus")
class CorpusPlans(unittest.TestCase):
    """The real plans: header, the seven stages in order, a trailer; an edit
    in a stage is charged to that stage alone."""

    def test_every_plan_has_the_stages(self):
        for p in corpus_plans():
            with open(p, encoding="utf-8") as f:
                text = f.read()
            names = [n for n, _ in plandiff.sections(text)]
            self.assertEqual(names, ["header"] + list(plandiff.STAGES) + ["trailer"], p)
            self.assertTrue(dict(plandiff.sections(text))["trailer"][-1].startswith(plandiff.GOLDEN_MARK), p)

    def test_an_edit_is_charged_to_its_stage(self):
        for p in corpus_plans()[:12]:
            with open(p, encoding="utf-8") as f:
                text = f.read()
            secs = plandiff.sections(text)
            self.assertEqual("\n".join(x for _, l in secs for x in l) + "\n", text, p)
            for i, (name, lines) in enumerate(secs):
                if name not in plandiff.STAGES:
                    continue
                j = next((k for k in range(1, len(lines)) if lines[k] and not lines[k].startswith("#")), None)
                if j is None:
                    continue
                parts = [list(l) for _, l in secs]
                parts[i][j] += " --edited"
                edited = "\n".join(x for l in parts for x in l) + "\n"
                self.assertEqual(plandiff.stages(text, edited), [name], "%s: %s" % (p, name))
                self.assertEqual(plandiff.lines_changed(text, edited), 2)


CLASSES = os.path.join(REPO, "testvm", "test2", "classes.json")
VARIANTS = os.path.join(CORPUS, "variants.txt")


@unittest.skipUnless(os.path.isfile(CLASSES) and os.path.isfile(VARIANTS), "no classes.json or no corpus variants.txt")
class CorpusMatchesClasses(unittest.TestCase):
    """The corpus's own lists agree with the class table's: variants.txt is
    holt.variants.bigger, and every HoltTest2 source has its dump (golden_plans.rs
    counts a missing one as a difference, which this catches before a host run)."""

    def holt(self):
        import json
        with open(CLASSES, encoding="utf-8") as f:
            return json.load(f)["holt"]

    def test_variants_txt_is_holt_variants_bigger(self):
        with open(VARIANTS, encoding="utf-8") as f:
            listed = sorted(l.split("#")[0].strip() for l in f if l.split("#")[0].strip())
        self.assertEqual(listed, sorted(g + "+bigger" for g in self.holt()["variants"].get("bigger", [])))
        for v in listed:
            g = v.split("+")[0]
            self.assertTrue(os.path.isfile(os.path.join(CORPUS, g, "plan+bigger.txt")), v)

    def test_every_source_has_a_dump(self):
        h = self.holt()
        sources = {g for gs in h["families"].values() for g in gs} | set(h.get("no_family", []))
        have = {g for g in os.listdir(CORPUS) if os.path.isfile(os.path.join(CORPUS, g, "src.json"))}
        self.assertEqual(sorted(sources - have), [], "HoltTest2 sources without a corpus dump")
        self.assertEqual(sorted(have - sources), [], "corpus dumps of no HoltTest2 source")
        for g in sorted(have):
            self.assertTrue(os.path.isfile(os.path.join(CORPUS, g, "plan.txt")), g)


if __name__ == "__main__":
    unittest.main()
