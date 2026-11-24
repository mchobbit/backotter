"""BackShot2All (piece A9; bs2lib/allrun.py and bs2's BackShot2All word): its counts
(decision 5), the SKIPPED list by piece from the real corpus, a built source no host
holds SKIPPED rather than an exit, the verdict and the report, the word on the ledger's
rows, and the draws never reading the ledger (fresh).  Nothing here reaches a host:

    python3 -m unittest discover -s testvm/backshot2/tests
"""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shutil
import tempfile
import unittest
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BS2_DIR = os.path.dirname(HERE)
sys.path.insert(0, BS2_DIR)
from bs2lib import allrun as AR, ledger as L, sources as S  # noqa: E402


def load(name, file):
    loader = importlib.machinery.SourceFileLoader(name, os.path.join(BS2_DIR, file))
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


B = load("bs2_all_cli", "bs2")
H = load("bs2_all_host", "bs2-host")
HOSTS = ("chopin", "cinqcent", "copland", "cage")
RUN_HOSTS = {h: f"m@{h}.local" for h in HOSTS}


def fake_held(have):
    """A held() that answers HAVE ({host: sources}) and records it as held() does."""
    def held(sources, hosts=None):
        for h in hosts or B.RUN_HOSTS:
            B.LISTED[h] = set(have.get(h, ()))
            B.CEILINGS[h] = (29.0, 23.2) if h in ("chopin", "cinqcent") else (9.0, 10.1)
        return {h: set(have.get(h, ())) & set(sources) for h in hosts or B.RUN_HOSTS}
    return held


class Counts(unittest.TestCase):
    def test_decision_5(self):
        c = AR.counts(B.SOURCES, B.tier2_of, B.ALPINE_SOURCES)
        self.assertEqual({s: c[s] for s in B.ALPINE_SOURCES},
                         {"alpine324": (1000, 200, 0), "alpine324-uefi": (1000, 200, 10),
                          "alpine324-lvm": (1000, 200, 10), "alpine324-luks": (1000, 200, 10)})
        self.assertEqual(sum(c[s][2] for s in B.ALPINE_SOURCES), 30, "BackShot2Alpine's 30 tier 2 cases")
        self.assertEqual(c["guix15"], (1000, 20, 5))
        self.assertEqual(c["guix15-bios"], (1000, 20, 0))
        for s in B.OTHER_SOURCES:
            self.assertEqual(c[s], (1000, 20, 5 if S.uefi(s) else 0), s)

    def test_per_source_counts_reach_the_jobs_the_draws_and_the_sweeps(self):
        n = B.all_counts(list(B.SOURCES))
        with mock.patch.dict(B.COUNTS, {"sweep": AR.T0}):
            jobs = B.jobs_of(list(B.SOURCES), n)
            self.assertEqual(len([j for j in jobs if j[1] == 1]), 4 * 200 + 20 * (len(B.SOURCES) - 4))
            self.assertEqual(len([j for j in jobs if j[0] == "alpine324-uefi" and j[1] == 2]), 10)
            self.assertEqual({B.sweep_n(n, s) for s in B.SOURCES}, {1000})
            c = B.draw_cmd("chopin", ["alpine324-uefi", "debian13"], list(B.SOURCES), n, "bs2all-x")
            self.assertIn("enqueue alpine324-uefi 200 --tier 1 ", c)
            self.assertIn("enqueue alpine324-uefi 10 --tier 2 ", c)
            self.assertIn("enqueue debian13 20 --tier 1 ", c)
            self.assertNotIn("enqueue debian13 5 --tier 2", c, "debian13 is BIOS: no tier 2")
        # (A plain int n is as before: BackShot2's N, 5 x N tier 0.)
        self.assertEqual(B.sweep_n(200), 1000)
        self.assertEqual(B.n_of(7, "debian13"), 7)


class Skipped(unittest.TestCase):
    def test_grouped_by_piece_from_the_real_corpus(self):
        sk = AR.skipped(S.table())
        pieces = [p for p, _ in AR.groups(sk)]
        for p in ("A3", "A4", "A5", "A6", "A8"):
            self.assertIn(p, pieces)
        self.assertEqual(pieces, sorted(pieces, key=list(AR.PIECES).index), "in PIECES' order")
        names = {x["name"] for x in sk}
        self.assertEqual(names - {"tier 3"}, {n for n, c in S.skipped().items() if c.piece})
        self.assertFalse(names & set(S.built()), "a built source is never SKIPPED by the table")
        self.assertIn({"name": "tier 3", "piece": "A8", "why": AR.TIER3["why"]}, sk)
        by = dict(AR.groups(sk))
        self.assertIn("freebsd15", [x["name"] for x in by["A4"]])
        self.assertIn("omnios58", [x["name"] for x in by["A5"]])
        self.assertIn("haiku6", [x["name"] for x in by["A6"]])
        self.assertIn("ubuntu26-zfs", [x["name"] for x in by["A3"]])
        self.assertIn("guix15-luks", [x["name"] for x in by["later"]])
        text = "\n".join(AR.skipped_words(sk))
        self.assertIn("SKIPPED (A4: the BSDs): ", text)
        self.assertIn("tier 3: the Thinkpad tier", text)
        self.assertTrue(AR.best(sk).startswith(f"PARTIAL PASS at best ({AR.sources_skipped(sk)} sources SKIPPED: A3 "))
        self.assertIn("tier 3 SKIPPED", AR.best(sk))
        self.assertEqual(AR.best([]), "PASS")

    def test_a_built_source_no_host_holds_is_skipped_not_an_exit(self):
        have = {h: set(B.SOURCES) - {"zorin18"} for h in HOSTS}
        with mock.patch.object(B, "RUN_HOSTS", RUN_HOSTS), mock.patch.dict(B.LISTED, clear=True), \
                mock.patch.dict(B.CEILINGS, clear=True), mock.patch.dict(B.SCRATCH, clear=True):
            fake_held(have)(B.SOURCES)
            run, missing, sk = B.all_layout(have)
            self.assertNotIn("zorin18", run)
            self.assertEqual(len(run), len(B.SOURCES) - 1)
            self.assertEqual(list(missing), ["zorin18"])
            self.assertIn("no vms/zorin18 on chopin, cinqcent, copland, cage", missing["zorin18"])
            z = [x for x in sk if x["name"] == "zorin18"]
            self.assertEqual(z[0]["piece"], "missing")
            self.assertTrue(z[0]["why"].startswith("not on any host in this run (no vms/zorin18 on "))
            # The room, and a host that didn't answer, in words.
            B.LISTED["copland"] = {"zorin18"}
            B.LISTED["cage"] = None
            w = B.missing_why("zorin18")
            self.assertIn("on copland, under its cases' --reserve ", w)
            self.assertIn("copland's cmp-run.sh --ceiling is 9 10.1", w)
            self.assertIn("cage didn't answer", w)

    def test_the_plan_lists_it_and_runs_on(self):
        have = {h: set(B.SOURCES) - {"zorin18"} for h in HOSTS}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(B, "RUN_HOSTS", RUN_HOSTS), mock.patch.object(B, "held", fake_held(have)), \
                mock.patch.dict(B.LISTED, clear=True), mock.patch.dict(B.CEILINGS, clear=True), \
                mock.patch.dict(B.SCRATCH, clear=True), mock.patch.dict(B.COUNTS, {"sweep": AR.T0}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            B.all_plan()
        t = out.getvalue()
        self.assertIn("SKIPPED (missing: built, but not on any host in this run): 1", t)
        self.assertIn("zorin18: not on any host in this run (no vms/zorin18", t)
        self.assertNotRegex(t, r"(?m)^zorin18 ", "not in the source table")
        self.assertRegex(t, r"(?m)^alpine324-uefi +chopin +1000 +200 +10 ")
        self.assertRegex(t, r"(?m)^debian13 +\S+ +1000 +20 +0 ")
        self.assertIn("then the corpus: ", t)
        self.assertIn("then the leftovers set: 9 steps on one disk on chopin", t)
        self.assertRegex(t, r"in all: ~[\d.]+ h est\.")
        self.assertRegex(t, r"(?m)^verdict: PARTIAL PASS at best \(\d+ sources SKIPPED")


class Verdict(unittest.TestCase):
    def test_the_order(self):
        self.assertEqual(AR.verdict(1, ["x"], 3), "FAIL")
        self.assertEqual(AR.verdict(1, [], 0), "FAIL")
        self.assertEqual(AR.verdict(0, ["x"], 3), "INCOMPLETE")
        self.assertEqual(AR.verdict(0, ["x"], 0), "INCOMPLETE")
        self.assertEqual(AR.verdict(0, [], 3), "PARTIAL PASS")
        self.assertEqual(AR.verdict(0, [], 0), "PASS")


def row(source, tier, verdict="PASS", i=0, check=None):
    return {"case": f"{source}-{i:016x}", "source": source, "tier": tier, "verdict": verdict, "check": check,
            "why": [f"FAIL {check}"] if check else []}


class Summary(unittest.TestCase):
    def setUp(self):
        self.m = {"run": "bs2all-20261124-1500", "hosts": ["chopin"],
                  "planned": {"debian13": [3, 2, 0], "ubuntu26-uefi": [3, 1, 1]},
                  "skipped": [dict(AR.TIER3)], "corpus": ["fail-001-x"],
                  "leftovers": {"set": "leftovers", "steps": 2, "host": "chopin"}}
        self.rows = ([row("debian13", 0, i=i) for i in range(5)] + [row("debian13", 1, i=10 + i) for i in range(2)] +
                     [row("ubuntu26-uefi", 0, i=20 + i) for i in range(5)] + [row("ubuntu26-uefi", 1, i=30),
                                                                              row("ubuntu26-uefi", 2, i=31)])
        self.queued = [(1, "debian13-000000000000000a"), (1, "debian13-000000000000000b"), (1, "ubuntu26-uefi-000000000000001e"),
                       (2, "ubuntu26-uefi-000000000000001f")]
        self.corpus = [{"case": "fail-001-x", "source": "alpine324", "tier": 1, "verdict": "PASS"}]
        self.lo = [{"id": "L1-plain", "verdict": "PASS"}, {"id": "L2-lvm", "verdict": "PASS"}]

    def sm(self, **kw):
        a = dict(manifest=self.m, rows=self.rows, queued=self.queued, corpus_rows=self.corpus, leftovers_rows=self.lo)
        a.update(kw)
        return AR.summary(**a)

    def test_all_passed_but_skipped_is_partial(self):
        sm = self.sm()
        self.assertEqual(sm["verdict"], "PARTIAL PASS", sm["incomplete"])
        self.assertEqual(sm["sources"]["debian13"][1], dict(planned=2, PASS=2, FAIL=0, HARNESS=0, INVALID=0, UNSUPPORTED=0))
        self.assertIn("verdict: PARTIAL PASS (PARTIAL PASS at best (0 sources SKIPPED; tier 3 SKIPPED))",
                      "\n".join(AR.words(sm)))

    def test_nothing_skipped_is_pass(self):
        self.assertEqual(self.sm(manifest=dict(self.m, skipped=[]))["verdict"], "PASS")

    def test_a_fail_with_its_check(self):
        rows = self.rows[:-1] + [row("ubuntu26-uefi", 2, "FAIL", i=31, check="the restored disk boots by itself")]
        sm = self.sm(rows=rows)
        self.assertEqual(sm["verdict"], "FAIL")
        self.assertEqual([(f["case"], f["tier"], f["check"]) for f in sm["fails"]],
                         [("ubuntu26-uefi-000000000000001f", 2, "the restored disk boots by itself")])
        self.assertIn("FAIL ubuntu26-uefi-000000000000001f tier 2 (): the restored disk boots by itself", "\n".join(AR.words(sm)))
        self.assertEqual(self.sm(rows=rows, queued=[])["verdict"], "FAIL", "FAIL wins over INCOMPLETE")

    def test_a_queued_case_with_no_row_is_incomplete(self):
        sm = self.sm(rows=self.rows[:-1])
        self.assertEqual(sm["verdict"], "INCOMPLETE")
        self.assertTrue(any("1 queued cases have no row yet (ubuntu26-uefi-000000000000001f t2)" in x for x in sm["pending"]))
        self.assertEqual(sm["no_verdict"], [])
        self.assertIn("still to run: ubuntu26-uefi tier 2: 0 of 1", "\n".join(AR.words(sm)))
        self.assertTrue(any(x.startswith("ubuntu26-uefi tier 2: 0 of 1") for x in sm["incomplete"]))

    def test_a_short_tier_0_is_incomplete(self):
        sm = self.sm(rows=self.rows[1:])
        self.assertEqual(sm["verdict"], "INCOMPLETE")
        self.assertTrue(any(x.startswith("debian13 tier 0: 4 of 5 cases") for x in sm["incomplete"]))

    def test_harness_and_invalid_are_no_verdict(self):
        for v in ("HARNESS", "INVALID", "UNSUPPORTED"):
            rows = self.rows[:-1] + [row("ubuntu26-uefi", 2, v, i=31)]
            sm = self.sm(rows=rows)
            self.assertEqual(sm["verdict"], "INCOMPLETE", v)
            self.assertEqual(sm["sources"]["ubuntu26-uefi"][2][v], 1)
            self.assertIn(f"ubuntu26-uefi tier 2: 1 {v}", sm["no_verdict"])
            self.assertEqual(sm["pending"], [], "a final non-verdict is not a pending case")
            self.assertIn(f"no verdict (needs a fix or rerun): ubuntu26-uefi tier 2: 1 {v}", "\n".join(AR.words(sm)))

    def test_the_corpus_and_the_leftovers(self):
        sm = self.sm(corpus_rows=[], leftovers_rows=[])
        self.assertEqual(sm["verdict"], "INCOMPLETE")
        self.assertIn("the corpus not run (1 cases: bs2 BackShot2All corpus bs2all-20261124-1500)", sm["pending"])
        self.assertIn("the leftovers set not run (2 steps: bs2 BackShot2All leftovers bs2all-20261124-1500)", sm["pending"])
        # A step without a verdict stops the set: no verdict, and the steps after it aren't pending.
        sm = self.sm(leftovers_rows=[{"id": "L1-plain", "verdict": "HARNESS"}])
        self.assertEqual((sm["verdict"], sm["pending"]), ("INCOMPLETE", []))
        self.assertEqual(sm["no_verdict"], ["the leftovers set: L1-plain without a verdict; the set stopped there"])
        sm = self.sm(leftovers_rows=self.lo[:1])
        self.assertEqual(sm["pending"], ["the leftovers set: 1 of 2 steps have a row"])
        sm = self.sm(leftovers_rows=[{"id": "L1-plain", "verdict": "FAIL", "check": "load restore", "case": "L1"}])
        self.assertEqual(sm["verdict"], "FAIL")
        self.assertEqual(sm["fails"][-1]["where"], "leftovers L1-plain")
        sm = self.sm(corpus_rows=[dict(self.corpus[0], verdict="FAIL", check="the deep tree differs")])
        self.assertEqual(sm["verdict"], "FAIL")
        self.assertEqual(sm["fails"][-1]["where"], "corpus")

    def test_an_unanswering_host(self):
        self.assertEqual(self.sm(unread=["cage didn't answer"])["verdict"], "INCOMPLETE")

    def test_one_row_a_case(self):
        rows = self.rows + [row("debian13", 1, "HARNESS", i=10)]   # (the queue's, after: the last wins)
        self.assertEqual(AR.tally(rows)[("debian13", 1)], {"PASS": 1, "HARNESS": 1})

    def test_parse_files(self):
        text = ("@file /tier1.jsonl\n" + json.dumps(row("debian13", 1)) + "\nnot json\n@file /queue.txt\n"
                "1 /bkoscratch/backshot2/runs/R/cases/debian13-0000000000000000.case\n@file -lo/leftovers.jsonl\n")
        f = AR.parse_files(text)
        self.assertEqual(f["/tier1.jsonl"], [row("debian13", 1)])
        self.assertEqual(f["/queue.txt"], [(1, "debian13-0000000000000000")])
        self.assertEqual(f["-lo/leftovers.jsonl"], [])
        self.assertEqual(AR.source_of_case("ubuntu26-uefi-000000000000001f"), "ubuntu26-uefi")
        self.assertIsNone(AR.source_of_case("fail-001-x"))


class Report(unittest.TestCase):
    def test_from_the_hosts(self):
        m = {"run": "bs2all-20261124-1500", "hosts": ["chopin", "cage"], "planned": {"debian13": [1, 1, 0]},
             "skipped": [], "corpus": [], "leftovers": {"steps": 0, "host": None}}
        files = {"chopin": {"-t0/tier0.jsonl": [row("debian13", 0, i=1), row("debian13", 0, i=2)],
                            "/queue.txt": ["1 /x/cases/debian13-0000000000000002.case"]},
                 "cage": {"/tier1.jsonl": [row("debian13", 1, i=2)]}}

        def par(cmds):
            out = {}
            for h, c in cmds.items():
                if "bs2all.json" in c and "@file" not in c:
                    out[h] = (0, json.dumps(m) + "\n", "")
                else:
                    out[h] = (0, "".join(f"@file {f}\n" + "".join((json.dumps(x) if isinstance(x, dict) else x) + "\n"
                                                                  for x in files.get(h, {}).get(f, [])) for f in AR.FILES), "")
            return out
        out = io.StringIO()
        with mock.patch.object(B, "RUN_HOSTS", dict(RUN_HOSTS)), mock.patch.object(B, "CHOSEN", False), \
                mock.patch.object(B, "par", par), contextlib.redirect_stdout(out):
            rc = B.all_report("bs2all-20261124-1500")
            self.assertEqual(list(B.RUN_HOSTS), ["chopin", "cage"], "the manifest's hosts")
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("verdict: PASS", out.getvalue())
        self.assertRegex(out.getvalue(), r"(?m)^debian13 +2/2 +1/1 +-$")
        out = io.StringIO()
        with mock.patch.object(B, "RUN_HOSTS", dict(RUN_HOSTS)), mock.patch.object(B, "CHOSEN", False), \
                mock.patch.object(B, "par", par), contextlib.redirect_stdout(out):
            B.all_report("bs2all-20261124-1500", as_json=True)
        self.assertEqual(json.loads(out.getvalue())["verdict"], "PASS")
        self.assertIn('cat /bkoscratch/backshot2/runs/R-t0/tier0.jsonl', B.gather_cmd("R"))

    def test_not_an_all_run(self):
        with mock.patch.object(B, "RUN_HOSTS", dict(RUN_HOSTS)), mock.patch.object(B, "par", lambda c: {h: (1, "", "") for h in c}):
            with self.assertRaises(SystemExit) as e:
                B.read_manifest("bs2a-20261124-1500")
        self.assertIn("not a BackShot2All run", str(e.exception))


class Start(unittest.TestCase):
    def test_manifest_and_name(self):
        have = {h: set(B.SOURCES) for h in HOSTS}
        got = {}

        def run_words(sources, n, run, have_=None, manifest=None):
            got.update(sources=sources, n=n, run=run, manifest=manifest)
        out = io.StringIO()
        with mock.patch.object(B, "RUN_HOSTS", RUN_HOSTS), mock.patch.object(B, "held", fake_held(have)), \
                mock.patch.object(B, "run_words", run_words), mock.patch.dict(B.LISTED, clear=True), \
                mock.patch.dict(B.CEILINGS, clear=True), mock.patch.dict(B.COUNTS, {"sweep": AR.T0}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            B.all_start()
        self.assertRegex(got["run"], r"^bs2all-\d{8}-\d{6}$")
        m = got["manifest"]
        self.assertEqual(m["planned"]["alpine324-luks"], [1000, 200, 10])
        self.assertEqual(m["planned"]["kali"], [1000, 20, 5 if S.uefi("kali") else 0])
        self.assertEqual(set(m["planned"]), set(B.SOURCES))
        self.assertEqual(m["leftovers"], {"set": "leftovers", "steps": 9, "host": "chopin"})
        self.assertEqual(sorted(m["corpus"]), sorted(c[1] for c in B.corpus_cases()))
        self.assertIn("tier 3", [x["name"] for x in m["skipped"]])
        self.assertIn(f"bs2 BackShot2All corpus {got['run']}", out.getvalue())
        self.assertIn(f"bs2 BackShot2All leftovers {got['run']}", out.getvalue())


class Word(unittest.TestCase):
    BINS = {"linux/bko-holt": "sha256:" + "a" * 64, "bs2_plan": "sha256:" + "b" * 64, "linux/rustic-bko": "sha256:" + "c" * 64}

    def rows(self):
        case = {"source": "debian13@gen:x", "edits": [], "target": {}}
        return L.rows("bs2all-20261124-1500", "debian13", "gen:" + "d" * 64, [("debian13-x", case, "PASS", [])], 1, 60, self.BINS)

    def test_the_word_reaches_the_row(self):
        from t2lib import ledger
        with mock.patch.dict(os.environ, {"BS2_WORD": "BackShot2All"}):
            r = self.rows()[0]
            self.assertEqual(r["word"], "BackShot2All")
            self.assertEqual(r["family"], "bs2")
            self.assertEqual(ledger.validate(r), [])
            self.assertIn("BS2_WORD=BackShot2All ", B.reuse_env())
            self.assertIn("BS2_WORD=BackShot2All ", B.light("chopin", "x", "t"))
            with mock.patch.object(B, "ssh", lambda h, c, check=True, capture=False, input=None: got.append(c) or
                                   mock.Mock(returncode=0)):
                got = []
                B.launch("chopin", [("debian13", 1, "/x.case")], [], 20, "bs2all-x")
            self.assertRegex(got[0], r"BS2_WORD=BackShot2All \S+/bs2-host queue bs2all-x")
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("BS2_WORD", None)
            self.assertEqual(self.rows()[0]["word"], "BackShot2Alpine")
            self.assertNotIn("BS2_WORD", B.reuse_env())


class Fresh(unittest.TestCase):
    def test_the_draws_never_read_the_ledger(self):
        """An All run is fresh: enqueue keeps a case for passing tier 0, whatever the ledger
        says (ledger.done() is never asked)."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)

        a = mock.Mock(source="debian13", n=3, tier=1, start=100000, run="bs2all-x", to=f"{d}/pick.txt")

        def boom(*a, **k):
            raise AssertionError("enqueue read the ledger")
        with mock.patch.object(H, "ROOT", d), mock.patch.object(H, "load_source", lambda s: ("gen:x", S.dump("debian13"))), \
                mock.patch.object(H, "caps_of", lambda: {}), \
                mock.patch.object(H, "tier0_cases", lambda s, g, dp, cs, run: [(n, "PASS", []) for n, _ in cs]), \
                mock.patch.object(H.L, "done", boom), contextlib.redirect_stdout(io.StringIO()):
            H.cmd_enqueue(a)
        with open(f"{d}/pick.txt") as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(re.match(r"1 .+/cases/debian13-[0-9a-f]{16}\.case$", x) for x in lines), lines)


class Leftovers(unittest.TestCase):
    M = {"run": "bs2all-20261124-150000", "hosts": ["chopin", "cage"], "planned": {}, "skipped": [], "corpus": [],
         "leftovers": {"set": "leftovers", "steps": 9, "host": "chopin"}}

    def test_a_tier_0_fail_is_the_sets_fail_not_not_run(self):
        t0 = [{"case": "L1-plain-x", "source": "alpine324-uefi", "tier": 0, "verdict": "FAIL",
               "why": ["vda3 ext4: load's mke2fs: blocks_per_group: layout 32768, made 32592"]}]
        sm = AR.summary(self.M, [], [], [], [], leftovers_t0=t0)
        self.assertEqual(sm["verdict"], "FAIL")
        self.assertEqual(sm["fails"][0]["where"], "leftovers tier 0")
        self.assertIn("blocks_per_group", sm["fails"][0]["check"])
        self.assertFalse([x for x in sm["incomplete"] if "not run" in x], sm["incomplete"])
        self.assertIn("-lo-t0/tier0.jsonl", AR.FILES)
        sm = AR.summary(self.M, [], [], [], [], leftovers_t0=[dict(t0[0], verdict="INVALID")])
        self.assertEqual(sm["verdict"], "INCOMPLETE")
        self.assertFalse(sm["fails"])

    def test_leftovers_start_passes_the_vars_and_takes_2949(self):
        got = []
        with mock.patch.object(B, "ssh", lambda h, c, check=True, capture=False, input=None: got.append((h, c))), \
                mock.patch.dict(os.environ, {"BS2_WORD": "BackShot2All", "BS2_HOLT": "/x/bko-holt"}), \
                contextlib.redirect_stdout(io.StringIO()):
            B.leftovers_start("chopin", "leftovers", "bs2all-x-lo")
        h, c = got[0]
        self.assertEqual(h, "chopin")
        self.assertRegex(c, r"BS2_HOLT=/x/bko-holt BS2_WORD=BackShot2All PYTHONUNBUFFERED=1 setsid -f \S+/bs2-host "
                            r"leftovers leftovers --run bs2all-x-lo --port 2949 > /bkoscratch/backshot2/logs/bs2all-x-lo.log")

    def test_all_leftovers_on_the_manifests_host_as_run_lo(self):
        got = {}
        with mock.patch.object(B, "RUN_HOSTS", dict(RUN_HOSTS)), mock.patch.object(B, "CHOSEN", False), \
                mock.patch.object(B, "par", lambda c: {h: (0, json.dumps(self.M), "") for h in c}), \
                mock.patch.object(B, "sync", lambda hs: got.setdefault("sync", hs)), \
                mock.patch.object(B, "leftovers_start", lambda h, name, run: got.update(h=h, name=name, run=run)):
            B.all_leftovers("bs2all-20261124-150000")
        self.assertEqual(got, {"sync": ["chopin"], "h": "chopin", "name": "leftovers", "run": "bs2all-20261124-150000-lo"})
        with mock.patch.object(B, "RUN_HOSTS", dict(RUN_HOSTS)), mock.patch.object(B, "CHOSEN", False), \
                mock.patch.object(B, "par", lambda c: {h: (0, json.dumps(dict(self.M, leftovers={"steps": 0, "host": None})), "")
                                                        for h in c}):
            with self.assertRaises(SystemExit) as e:
                B.all_leftovers("bs2all-20261124-150000")
        self.assertIn("no host for the leftovers set", str(e.exception))


class Corpus(unittest.TestCase):
    def test_the_name_is_the_rows(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        os.makedirs(f"{d}/cases")
        case = {"source": "alpine324@gen:x", "edits": [], "target": {}}
        for f, c in (("a.case", dict(case, name="fail-009-x")), ("b.case", case)):
            with open(f"{d}/cases/{f}", "w") as fh:
                json.dump(c, fh)
        with mock.patch.object(B, "HERE", d):
            got = B.corpus_cases()
        self.assertEqual(got[0], ("a.case", "fail-009-x", "alpine324"))
        self.assertRegex(got[1][1], r"^alpine324-[0-9a-f]{16}$", "case.name_of(), as bs2-host tier1 names it")
        self.assertEqual(got[1][1], H.C.name_of(case))

    def test_all_corpus_on_the_runs_hosts_as_run_corpus(self):
        cs = B.corpus_cases()
        m = {"run": "bs2all-20261124-150000", "hosts": ["chopin", "cage"], "corpus": [c[1] for c in cs],
             "leftovers": {"host": "chopin"}}
        have = {"chopin": {c[2] for c in cs}, "cage": {"alpine324"}}
        got = {}
        with mock.patch.object(B, "RUN_HOSTS", dict(RUN_HOSTS)), mock.patch.object(B, "CHOSEN", False), \
                mock.patch.object(B, "par", lambda c: {h: (0, json.dumps(m), "") for h in c}), \
                mock.patch.object(B, "held", lambda ss, hosts=None: {h: have.get(h, set()) & set(ss) for h in B.RUN_HOSTS}), \
                mock.patch.object(B, "corpus", lambda plan, tier, stamp=None, have=None, only=None:
                                  got.update(plan=plan, tier=tier, stamp=stamp, hosts=list(B.RUN_HOSTS), only=only)), \
                contextlib.redirect_stdout(io.StringIO()):
            B.all_corpus("bs2all-20261124-150000")
        self.assertEqual(got, {"plan": False, "tier": 1, "stamp": "bs2all-20261124-150000-corpus",
                               "hosts": ["chopin", "cage"], "only": {c[0] for c in cs}})
        # (and corpus() sends each case to a host of those that holds its source, at port 2949)
        sent = []
        with mock.patch.object(B, "RUN_HOSTS", {"chopin": "m@chopin.local", "cage": "m@cage.local"}), \
                mock.patch.object(B, "sync", lambda hs: None), \
                mock.patch.object(B, "ssh", lambda h, c, check=True, capture=False, input=None: sent.append((h, c))):
            B.corpus(False, 1, stamp="bs2all-x-corpus", have=have, only={cs[0][0]})
        self.assertEqual(len(sent), 1)
        self.assertIn(sent[0][0], [h for h in have if cs[0][2] in have[h]])
        self.assertRegex(sent[0][1], r"tier1 \S+/cases/" + re.escape(cs[0][0]) + r" --tier 1 --run bs2all-x-corpus --port 2949")


class Manifest(unittest.TestCase):
    def test_written_to_every_host_then_refused_naming_each(self):
        tried = []

        def ssh(h, c, check=True, capture=False, input=None):
            tried.append(h)
            self.assertFalse(check)
            self.assertEqual(json.loads(input)["run"], "bs2all-x")
            self.assertIn("then echo \"/bkoscratch/backshot2/runs/bs2all-x is there already\"; exit 3", c)
            return mock.Mock(returncode={"cinqcent": 3, "cage": 255}.get(h, 0),
                             stdout="/bkoscratch/backshot2/runs/bs2all-x is there already" if h == "cinqcent" else "",
                             stderr="ssh: connect to host cage port 22: timed out" if h == "cage" else "")
        with mock.patch.object(B, "ssh", ssh):
            with self.assertRaises(SystemExit) as e:
                B.write_manifest({"run": "bs2all-x"}, list(HOSTS), "bs2all-x")
        self.assertEqual(tried, list(HOSTS), "every host tried before failing")
        t = str(e.exception)
        self.assertIn("not written on 2 of 4 hosts", t)
        self.assertIn("cinqcent: exit 3: /bkoscratch/backshot2/runs/bs2all-x is there already", t)
        self.assertIn("cage: exit 255: ssh: connect to host cage port 22: timed out", t)
        tried.clear()
        with mock.patch.object(B, "ssh", lambda h, c, check=True, capture=False, input=None:
                               tried.append(h) or mock.Mock(returncode=0, stdout="", stderr="")):
            B.write_manifest({"run": "bs2all-x"}, list(HOSTS), "bs2all-x")
        self.assertEqual(tried, list(HOSTS))

    def test_run_words_writes_it_to_every_host_in_use_before_any_starts(self):
        ev = []
        parts = {"chopin": [("debian13", 1, 0)], "cage": [("debian13", 1, 1)], "copland": []}
        with mock.patch.object(B, "placement", lambda s, n, have=None: ({"debian13": ["chopin", "cage"]},
                                                                        {"debian13": "chopin"}, parts, {})), \
                mock.patch.object(B, "preflight", lambda hs: ev.append(("preflight", tuple(hs)))), \
                mock.patch.object(B, "sync", lambda hs: ev.append(("sync", tuple(hs)))), \
                mock.patch.object(B, "write_manifest", lambda m, hs, run: ev.append(("manifest", tuple(hs), tuple(m["hosts"])))), \
                mock.patch.object(B, "start_hosts", lambda *a: ev.append(("start",)) or {}), \
                contextlib.redirect_stdout(io.StringIO()):
            B.run_words(["debian13"], {"debian13": 20}, "bs2all-x", have={}, manifest={"run": "bs2all-x"})
        self.assertEqual(ev, [("preflight", ("chopin", "cage")), ("sync", ("chopin", "cage")),
                              ("manifest", ("chopin", "cage"), ("chopin", "cage")), ("start",)])
        ev.clear()
        with mock.patch.object(B, "placement", lambda s, n, have=None: ({"debian13": ["chopin"]}, {"debian13": "chopin"},
                                                                        {"chopin": [("debian13", 1, 0)]}, {})), \
                mock.patch.object(B, "preflight", lambda hs: None), mock.patch.object(B, "sync", lambda hs: None), \
                mock.patch.object(B, "write_manifest", mock.Mock(side_effect=SystemExit("bs2: not written"))), \
                mock.patch.object(B, "start_hosts", lambda *a: ev.append("start") or {}):
            with self.assertRaises(SystemExit):
                B.run_words(["debian13"], {"debian13": 20}, "bs2all-x", have={}, manifest={"run": "bs2all-x"})
        self.assertEqual(ev, [], "no host started")


class ReportMore(unittest.TestCase):
    def test_a_host_that_doesnt_answer(self):
        m = {"run": "bs2all-x", "hosts": ["chopin", "cage"], "planned": {"debian13": [1, 1, 0]}, "skipped": [],
             "corpus": [], "leftovers": {"steps": 0, "host": None}}
        ok = "".join(f"@file {f}\n" for f in AR.FILES)
        ok = ok.replace("@file -t0/tier0.jsonl\n", "@file -t0/tier0.jsonl\n" + json.dumps(row("debian13", 0, i=1)) + "\n"
                        + json.dumps(row("debian13", 0, i=2)) + "\n")
        ok = ok.replace("@file /tier1.jsonl\n", "@file /tier1.jsonl\n" + json.dumps(row("debian13", 1, i=2)) + "\n")

        def par(cmds):
            if all("@file" not in c for c in cmds.values()):
                return {h: (0, json.dumps(m), "") for h in cmds}
            return {h: (0, ok, "") if h == "chopin" else (255, "", "ssh: connect to host cage port 22: timed out")
                    for h in cmds}
        out = io.StringIO()
        with mock.patch.object(B, "RUN_HOSTS", dict(RUN_HOSTS)), mock.patch.object(B, "CHOSEN", False), \
                mock.patch.object(B, "par", par), contextlib.redirect_stdout(out):
            rc = B.all_report("bs2all-x")
        self.assertEqual(rc, 1)
        t = out.getvalue()
        self.assertIn("still to run: cage didn't answer (ssh: connect to host cage port 22: timed out): its rows aren't counted", t)
        self.assertIn("verdict: INCOMPLETE", t)

    def test_queue_rows_through_parse_files(self):
        q = {"case": "debian13-000000000000000a", "source": "debian13", "tier": 1, "verdict": "HARNESS",
             "check": "the queue's cmp-run.sh exited 1 with no row from the case", "why": ["exit 1"], "attempt": 0}
        text = ("@file /tier1.jsonl\n@file /tier1-queue.jsonl\n" + json.dumps(q) + "\n@file /tier2-queue.jsonl\n"
                "@file /queue.txt\n1 /r/cases/debian13-000000000000000a.case\n")
        f = AR.parse_files(text)
        self.assertEqual(f["/tier1-queue.jsonl"], [q])
        rows = [r for k in AR.ROW_FILES for r in f.get(k, [])]
        m = {"run": "R", "planned": {"debian13": [0, 1, 0]}, "skipped": [], "corpus": [], "leftovers": {}}
        sm = AR.summary(m, rows, f["/queue.txt"], [], [])
        self.assertEqual(sm["sources"]["debian13"][1]["HARNESS"], 1)
        self.assertEqual(sm["pending"], [], "the queue's row is the case's row: not still to run")
        self.assertEqual(sm["no_verdict"], ["debian13 tier 1: 1 HARNESS"])
        self.assertEqual(sm["verdict"], "INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
