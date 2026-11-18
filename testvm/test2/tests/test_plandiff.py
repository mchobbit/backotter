"""U6.3: t2lib/plandiff.py splits golden plans into stages as
holt/tests/common/mod.rs does (golden_plans.rs's diff.json)."""

import io
import os
import re
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
            # golden_plans.rs's lines end it: an "exit N; stderr:" one may be followed by load's
            # stderr, each line "#   " (a refusal: omarchy's +reformat plans)
            tail = dict(plandiff.sections(text))["trailer"]
            last = max(i for i, l in enumerate(tail) if l.startswith(plandiff.GOLDEN_MARK))
            self.assertTrue(all(l.startswith("#   ") for l in tail[last + 1:]), p)
            self.assertTrue(not tail[last + 1:] or tail[last].endswith("stderr:"), p)

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
    counts a missing one as a difference, which this catches before a host run).
    A dump of no source is allowed, as golden_plans.rs's unplanned() only notes
    it: the corpus README's "some are not sources yet" (guests dumped before
    they join classes.json, arm64.md A3's synthetic arm64-* dumps)."""

    def classes(self):
        import json
        with open(CLASSES, encoding="utf-8") as f:
            return json.load(f)

    def holt(self):
        return self.classes()["holt"]

    def test_variants_txt_is_holt_variants_bigger(self):
        with open(VARIANTS, encoding="utf-8") as f:
            listed = sorted(l.split("#")[0].strip() for l in f if l.split("#")[0].strip())
        # (+reformat-TYPE plans are golden-only, not HoltTest2 cells: docs/design/reformat.md)
        for v in listed:
            self.assertTrue(v.endswith("+bigger") or re.fullmatch(r"[a-z0-9-]+\+reformat-[a-z0-9]+", v), v)
        self.assertEqual([v for v in listed if v.endswith("+bigger")],
                         sorted(g + "+bigger" for g in self.holt()["variants"].get("bigger", [])))
        for v in listed:
            g, var = v.split("+", 1)
            self.assertTrue(os.path.isfile(os.path.join(CORPUS, g, f"plan+{var}.txt")), v)

    def test_every_source_has_a_dump(self):
        c = self.classes()
        h = c["holt"]
        # (plan.py's arch_classes merges the arm64 block's holt into holt for a run on an arm64 host)
        arm = c.get("arm64", {}).get("holt", {})
        sources = {g for hh in (h, arm) for gs in hh.get("families", {}).values() for g in gs} | set(h.get("no_family", []))
        have = {g for g in os.listdir(CORPUS) if os.path.isfile(os.path.join(CORPUS, g, "src.json"))}
        self.assertEqual(sorted(sources - have), [], "HoltTest2 sources without a corpus dump")
        # Extras are fine (golden_plans.rs: "note  G: in the corpus, but not one of HoltTest2's
        # sources"), but not a guest classes.json rules out as a source for good.
        self.assertEqual(sorted(have & set(h.get("excluded", {}))), [], "corpus dumps of holt.excluded guests")
        for g in sorted(have):
            self.assertTrue(os.path.isfile(os.path.join(CORPUS, g, "plan.txt")), g)

    def test_index_tsv_lists_exactly_the_dumps(self):
        # (golden_plans.rs's unverified_in counts a dump with no index.tsv row as verified)
        with open(os.path.join(CORPUS, "index.tsv"), encoding="utf-8") as f:
            rows = [l.split("\t")[0] for l in f.read().splitlines()[1:] if l.strip()]
        have = {g for g in os.listdir(CORPUS) if os.path.isfile(os.path.join(CORPUS, g, "src.json"))}
        self.assertEqual(len(rows), len(set(rows)), "a guest twice in index.tsv")
        self.assertEqual(sorted(rows), sorted(have), "index.tsv's guests against the dumps")


if __name__ == "__main__":
    unittest.main()
