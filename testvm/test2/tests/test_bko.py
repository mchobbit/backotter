"""BkoTest2's and SweepTest2's conductor (contract 2.3, 2.4; package K6) with fake runners, and
stic.py's two pieces for it (guards.tripped() in drive_round, options.gate_fresh in gate_step).
No host, no ssh: every I/O goes through bko.Env, which these tests replace."""

import argparse
import json
import os
import shutil
import signal
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

TESTS = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(TESTS)
REPO = os.path.dirname(os.path.dirname(T2))
sys.path.insert(0, T2)

from t2lib import bko, report, stic, words   # noqa: E402
from t2lib import plan as planmod             # noqa: E402
from t2lib import run as runmod               # noqa: E402

HOSTS = ["chopin", "cinqcent"]
H64 = lambda c: c * 64   # noqa: E731
with open(os.path.join(REPO, "testvm", "test2", "classes.json")) as _f:
    CLASSES = json.load(_f)
ALL_LOCKS = ["bko", "gate", "stic", "holt", "guest", "img"]


class Crash(BaseException):
    """The Mac process dying mid-run (not an Exception).  The conductor catches a child's
    BaseException now, so a test of a death also uses dying() below."""


def _unguarded(self, fam, step=None):
    (step or (lambda: self.run_child(fam)))()


def dying():
    """The Mac process's death mid-child: run_child_safe doesn't catch it (a real death isn't an
    exception at all), and the dead threads' tracebacks aren't printed."""
    return mock.patch.multiple(bko.Conductor, run_child_safe=_unguarded)


def known():
    return {"stic": ["arch", "debian12"],
            "holt": {"sources": ["arch", "debian12"], "variants": {"img": ["debian12"]}, "families": {}, "refused": {}},
            "gate": ["bin:version", "holt", "selftest"], "guest": ["arch", "debian12", "redox"], "guest_refused": {},
            "img": list(words.PLATS)}


class Ctx:
    def __init__(self, world):
        self.checkout = REPO
        self.hosts = list(HOSTS)
        self.facts = {h: {"ok": True, "guests": {g: {"generation": "g1"} for g in ("arch", "debian12", "redox")}}
                      for h in HOSTS}
        self.ledger = list(world.rows)
        self.options = {"last_sweep": None}
        self.classes = CLASSES
        self.copy_preview = {"id": "c0ffee1.d00d", "dirty": {}, "commit": "c" * 40}
        self.now = time.time()
        self.drive = argparse.Namespace(all_guests=lambda: ["arch", "debian12"])
        self.src = self.specs = None
        self.run = None


class World:
    """Every family's cells, what a run of each does, and the rows: a fake of the planner, the
    runners and the ledger together."""

    CELLS = {"gate": ["gate:bin:version", "gate:holt", "gate:selftest"], "stic": ["stic:arch", "stic:debian12"],
             "holt": ["holt:arch", "holt:debian12"], "guest": ["guest:arch", "guest:debian12", "guest:redox"],
             "img": ["img:netbsd"]}
    GATE_OF = {"stic": ["gate:bin:version"], "holt": ["gate:holt"]}

    def __init__(self, tmp):
        self.tmp = tmp
        self.mac = os.path.join(tmp, "mac")
        self.rows, self.events, self.mu = [], [], threading.Lock()
        self.verdict = {}       # {cell: verdict} when it runs (PASS)
        self.gate_verdict = {}  # {gate cell: verdict} when a child's gate step runs it
        self.status = {}        # {cell: status} the planner gives whatever the rows say
        self.reasons = {}       # {cell: [reason]} with it
        self.invalid = {}       # {cell: note}: its rows are invalid (an input not measured)
        self.identity_res = {}  # {guest: identity_check's answer}
        self.behave = {}        # {family: fn(world, word, ctx, shared, out) -> exit or None}
        self.inp = {}           # {cell: {bin name: value}} its planned inputs
        self.binaries = {}      # {img cell: binaries}
        self.n = 0

    def ev(self, *what):
        with self.mu:
            self.events.append(what)

    def row(self, cell, run, verdict="PASS", kind="run", extra=None):
        with self.mu:
            self.n += 1
            r = {"id": f"chopin-20261110T0300{self.n:02d}Z-{self.n:08x}", "t": f"2026-11-10T03:{self.n:02d}:00Z",
                 "cell": cell, "run": run, "kind": kind, "verdict": verdict, "valid": True, "key": H64("1"),
                 "host": "chopin", "gm": 1.0, "wear": {"nvme0n1": 1e8}, "needs": {"a": verdict}, "class": "pass",
                 "groups": {}, "inputs": {}, "extra": extra or {}}
            if cell in self.invalid:
                r.update(valid=False, note=self.invalid[cell])
            self.rows.append(r)
            return r

    # -- the planner --
    def plan(self, word, ctx):
        fam = word.family
        uni = ctx.options.get("universe")
        cells = [c for c in self.CELLS[fam] if not uni or c in uni]
        p = planmod.Plan(word, fam)
        p.header = {"base": "abc1234", "facts": {}}
        bad = ctx.options.get("identity_bad") or {}
        extra = ctx.options.get("extra_due") or {}
        for c in cells:
            pc = planmod.PlanCell(c, fam)
            pc.keys = {"chopin": H64("1")}
            pc.est = {"gm": 1.0, "wall_s": 60, "gb": 0.0}
            pc.inp = {"chopin": {"bin": dict(self.inp.get(c) or {})}}
            mine = [r for r in ctx.ledger if r.get("cell") == c and r.get("kind") in ("run", "carried") and r.get("valid") is not False]
            last = max(mine, key=lambda r: (r["t"], r["id"]), default=None)
            if word.all:
                pc.status, pc.rule = "due", "fresh:Sweep" if word.sweep else "fresh:All"
            elif last is not None and last["verdict"] == "PASS":
                pc.status = "current"
            else:
                pc.status, pc.rule = "due", "new" if last is None else "verdict:FAIL"
            if c in extra:
                pc.status, pc.rule, pc.reasons = "due", "canary:carried", [extra[c]]
            if fam in ("stic", "holt") and bko.guest_of(c) in bad:
                pc.status, pc.rule, pc.reasons = "not-runnable", None, [f"its guest's identity failed: {bad[bko.guest_of(c)]}"]
            if c in self.status:
                pc.status = self.status[c]
            if c in self.reasons:
                pc.reasons = list(self.reasons[c])
            if c in self.binaries:
                pc.binaries = self.binaries[c]
            p.cells.append(pc)
        due = p.due()
        p.totals = {"due": len(due), "gm": float(len(due)), "wall_s": 60 * len(due), "gb": {}, "cells": len(p.cells),
                    "counts": {s: len(p.by_status(s)) for s in planmod.STATUSES if p.by_status(s)}}
        self.ev("plan", fam, tuple(c.cell for c in due), tuple(uni or ()), tuple(sorted(bad)), tuple(sorted(extra)))
        return p

    # -- a child's run --
    def child(self, word, ctx, shared, out, run=None, plan=None):
        fam = word.family
        plan = plan or shared.plan
        run = run or runmod.new_run_id(fam, stamp=shared.stamp)
        for n in ALL_LOCKS:     # the composite holds every lock; the child takes none
            assert runmod.FamilyLock(n, self.mac).held(), n
        self.ev("run", fam, run, shared.stamp, json.dumps(shared.copy, sort_keys=True), id(shared.guards),
                tuple(c.cell for c in plan.due()), tuple(ctx.options.get("universe") or ()),
                tuple(ctx.options.get("gate_universe") or ()) if "gate_universe" in ctx.options else None,
                bool(ctx.options.get("gate_fresh")))
        stops = []
        ctx.run = argparse.Namespace(stops=argparse.Namespace(stops=stops), plan=plan)
        if not plan.totals.get("due"):
            out(report.nothing_due_line(plan))
            return 0
        fn = self.behave.get(fam)
        ex = fn(self, word, ctx, shared, out) if fn else None
        bad = 0
        if fam in self.GATE_OF and plan.due():     # its gate step: its family's layers
            gu = ctx.options.get("gate_universe")
            for g in self.GATE_OF[fam]:
                if gu is not None and g not in gu:
                    continue
                if not ctx.options.get("gate_fresh") and any(r["cell"] == g and r["verdict"] == "PASS" for r in self.rows):
                    continue
                v = self.gate_verdict.get(g, "PASS")
                self.row(g, run, v)
                bad += v != "PASS"
        for c in plan.due():
            v = self.verdict.get(c.cell, "PASS")
            if v is None:
                continue
            self.row(c.cell, run, v)
            bad += v != "PASS"
        ex = ex if ex is not None else (1 if bad or stops else 0)
        runmod.write_text(os.path.join(runmod.run_dir(run, self.mac), "report.txt"),
                          f"{word.word} report: run {run}\n  cells\nstops: {'; '.join(stops) if stops else 'none'}\n")
        return ex


class FakeEnv(bko.Env):
    def __init__(self, world, hosts=HOSTS):
        super().__init__(REPO, hosts, mac_root=world.mac)
        self.w = world
        self.copies, self.lock_calls, self.resumed, self.pulls = [], [], [], 0
        self.guest_mod = argparse.Namespace(pre_plan=lambda ctx, word, out: world.ev("pre_plan", word.family),
                                            preflight=lambda ctx, out: world.ev("preflight", "guest"),
                                            resume=lambda ctx, plan: None)
        self.other_mod = argparse.Namespace(resume=lambda ctx, plan: None)   # stic, holt, img: a resume; gate: none
        self.refeed = {}    # {family: fn(shared)}: what its resume feeds again (SticTest2's refeed)

    def now(self):
        return time.time()

    def light_ctx(self):
        return Ctx(self.w)

    def known(self, ctx):
        return known()

    def make_ctx(self, word, log=None):
        self.w.ev("ctx", word.family)
        return Ctx(self.w)

    def pull(self):
        self.pulls += 1
        return list(self.w.rows)

    def refresh_facts(self, ctx, log=None):
        self.w.ev("facts")
        return dict(ctx.facts)

    def plan(self, word, ctx):
        return self.w.plan(word, ctx)

    def make_copy(self, hosts, preview, log=None):
        self.copies.append(list(hosts))
        return {h: {"id": "c0ffee1.d00d", "path": "/home/m/c/c0ffee1.d00d", "commit": "c" * 40, "dirty": {}} for h in hosts}

    def locks(self, names, run):
        self.lock_calls.append(list(names))
        return super().locks(names, run)

    def leftovers(self, live, out):
        pass

    def running(self, family):
        return {}

    def preflight(self, facts, hosts):
        return []

    def runner(self, family):
        if family == "guest":
            return self.guest_mod
        return None if family == "gate" else self.other_mod

    def lifecycle(self, word, ctx, shared, out):
        return self.w.child(word, ctx, shared, out)

    def resume(self, run, ctx, shared, out):
        self.resumed.append(run)
        pj = runmod.read_json(os.path.join(runmod.run_dir(run, self.w.mac), "plan.json"), {})
        fn = self.refeed.get(pj.get("family"))
        if fn:
            fn(shared)
        w = words.parse(pj["word"]["text"].split(), known())
        p = self.w.plan(w, ctx)
        return self.w.child(w, ctx, runmod.Shared(shared.stamp, shared.copy, shared.guards, plan=p), out, run=run)

    def identity(self, ctx, guests, run, log):
        self.w.ev("identity", tuple(guests), run, getattr(ctx.run, "word", None).text)
        out = {}
        for g in guests:
            r = self.w.identity_res.get(g, {"ok": True})
            if not r.get("ok") and not r.get("harness"):
                row = self.w.row(f"guest:{g}", run, "FAIL")
                r = dict(r, row=row["id"])
            out[g] = r
        return out

    def pkgdiff(self, guest, a, b):
        return {"added": ["pkg zlib"], "removed": [], "changed": ["pkg openssl"]}


def slurp(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def word(text, universe=None):
    w = words.parse(text.split(), known())
    w.universe = universe
    return w


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2-bko-")
        self.w = World(self.tmp)
        self.env = FakeEnv(self.w)
        self.said = []
        self.mu = threading.Lock()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def say(self, *a):
        with self.mu:
            self.said.append(" ".join(str(x) for x in a))

    def conduct(self, text, universe=None, env=None):
        return bko.conduct(word(text, universe), HOSTS, REPO, out=self.say, env=env or self.env)

    def only_run(self, prefix="t2b-"):
        runs = [n for n in os.listdir(os.path.join(self.w.mac, "runs")) if n.startswith(prefix)]
        self.assertEqual(len(runs), 1, runs)
        return runs[0], runmod.read_json(os.path.join(self.w.mac, "runs", runs[0], "bko.json"))

    def runs_of(self):
        return [e for e in self.w.events if e[0] == "run"]


# --- small pieces ---------------------------------------------------------------------------------

class TestPieces(unittest.TestCase):
    def test_child_words(self):
        k = known()
        b = word("BkoTest2 rel4 stale")
        self.assertEqual([bko.child_text(b, f) for f in bko.PLAN_ORDER["bko"]],
                         ["GateTest2 rel4 stale", "SticTest2 rel4 stale", "HoltTest2 stale", "GuestTest2 stale", "ImgTest2 stale"])
        s = word("SweepTest2 rel4")
        ws = [bko.child_word(s, f, k) for f in bko.PLAN_ORDER["sweep"]]
        self.assertEqual([x.word for x in ws], ["GateTest2All", "SticTest2All", "HoltTest2All", "GuestTest2All"])
        self.assertTrue(all(x.sweep and x.all for x in ws))
        self.assertEqual([x.round for x in ws], ["rel4", "rel4", None, None])
        self.assertTrue(json.loads(json.dumps(ws[1].to_json()))["sweep"])   # resume keeps it

    def test_universe_split(self):
        u = bko.split_universe(["gate:bin:version", "stic:arch", "holt:arch", "guest:arch", "img:netbsd"], "bko")
        self.assertEqual(u, {"gate": ["gate:bin:version"], "stic": ["stic:arch"], "holt": ["holt:arch"],
                             "guest": ["guest:arch"], "img": ["img:netbsd"]})
        with self.assertRaises(bko.Refused) as cm:
            bko.split_universe(["img:netbsd"], "sweep")
        self.assertIn("no ImgTest2 child", str(cm.exception))
        self.assertIsNone(bko.split_universe(None, "bko"))

    def test_stamps(self):
        self.assertEqual(bko.stamp_of("t2h-20261110T0312-4f2a-img"), "20261110T0312-4f2a")
        self.assertEqual(bko.stamp_of("t2s-20261110T0312-4f2a-r2"), "20261110T0312-4f2a")
        self.assertTrue(bko.of_stamp("t2g-20261110T0312-4f2a", "20261110T0312-4f2a"))
        self.assertFalse(bko.of_stamp("t2g-20261110T0313-4f2a", "20261110T0312-4f2a"))
        self.assertFalse(bko.of_stamp(None, None))

    def test_report_stops(self):
        tmp = tempfile.mkdtemp(prefix="t2-bko-r-")
        try:
            p = os.path.join(tmp, "report.txt")
            with open(p, "w") as f:
                f.write("X report\n  ran stic:a PASS\nstops: stop-os linux: need x failed; stop-starts: swap\nrows: 2\n")
            self.assertEqual(bko.report_stops(p), ["stop-os linux: need x failed", "stop-starts: swap"])
            with open(p, "w") as f:
                f.write("X report\nstops: none\n")
            self.assertEqual(bko.report_stops(p), [])
            self.assertEqual(bko.report_stops(os.path.join(tmp, "none.txt")), [])
        finally:
            shutil.rmtree(tmp)

    def test_combined_line(self):
        w = word("GuestTest2")
        p = planmod.Plan(w, "guest")
        for i, (c, keys) in enumerate((("guest:a", {"chopin": "k"}), ("guest:b", {"cinqcent": "k"}),
                                       ("gate:selftest", {"mac": "k"}))):
            pc = planmod.PlanCell(c, c.split(":")[0])
            pc.status, pc.keys, pc.est = "due", keys, {"gm": 1.5, "wall_s": 100 * (i + 1), "gb": 0.25}
            p.cells.append(pc)
        p.totals = {"due": 3, "gm": 4.5}
        self.assertEqual(bko.combined_line([p], HOSTS), "combined: 3 due, 4.5 gm, ~5 min wall, 0.8 GB")


class TestCarriedCanaries(unittest.TestCase):
    """BkoTest2 step 5: extra_due only at the child's digest."""

    def plans(self, value):
        img = planmod.Plan(word("ImgTest2"), "img")
        pc = planmod.PlanCell("img:netbsd", "img")
        pc.binaries = {"rustic-bko": {"state": "carried-other", "digest": H64("a"), "canary": "stic:netbsd11"},
                       "restic-bko": {"state": "real", "digest": H64("b"), "canary": None},
                       "bko-holt": {"state": "none", "digest": H64("c"), "canary": "holt:alpinebsd"}}
        img.cells.append(pc)
        st = planmod.Plan(word("SticTest2 rel4"), "stic")
        c = planmod.PlanCell("stic:netbsd11", "stic")
        c.inp = {"chopin": {"bin": {"netbsd/rustic-bko": value}}}
        st.cells.append(c)
        return img, {"stic": st}

    def test_at_the_digest(self):
        img, kids = self.plans("sha256:" + H64("a"))
        extra, notes = bko.carried_canaries(img, kids)
        self.assertEqual(list(extra), ["stic"])
        self.assertEqual(list(extra["stic"]), ["stic:netbsd11"])
        self.assertIn("img:netbsd's rustic-bko", extra["stic"]["stic:netbsd11"])
        self.assertEqual(notes, [])     # (bko-holt's "none" isn't a carried canary)

    def test_another_digest_is_a_note(self):
        img, kids = self.plans("sha256:" + H64("d"))
        extra, notes = bko.carried_canaries(img, kids)
        self.assertEqual(extra, {})
        self.assertEqual(notes, ["img:netbsd needs a rebuild: its rustic-bko isn't what this run tests"])

    def test_not_in_the_run(self):
        img, _ = self.plans("x")
        extra, notes = bko.carried_canaries(img, {})
        self.assertEqual((extra, notes), ({}, ["img:netbsd: its rustic-bko's canary stic:netbsd11 isn't in this run"]))

    def test_illumos_and_bko_holt(self):
        img = planmod.Plan(word("ImgTest2"), "img")
        pc = planmod.PlanCell("img:illumos", "img")
        pc.binaries = {"rustic-bko": {"state": "carried-other", "digest": H64("a"), "canary": "stic:omnios58"},
                       "bko-holt": {"state": "carried-other", "digest": H64("c"), "canary": "holt:omnios58"}}
        img.cells.append(pc)
        st, ho = planmod.Plan(word("SticTest2 rel4"), "stic"), planmod.Plan(word("HoltTest2"), "holt")
        a, b = planmod.PlanCell("stic:omnios58", "stic"), planmod.PlanCell("holt:omnios58", "holt")
        a.inp = {"chopin": {"bin": {"omnios/rustic-bko": "sha256:" + H64("a")}}}
        b.inp = {"cinqcent": {"bin": {"illumos/bko-holt": "code:" + H64("e")}}}
        st.cells.append(a)
        ho.cells.append(b)
        extra, notes = bko.carried_canaries(img, {"stic": st, "holt": ho}, lambda plat: H64("e"))
        self.assertEqual(sorted(extra), ["holt", "stic"])
        self.assertEqual(notes, [])
        extra, notes = bko.carried_canaries(img, {"stic": st, "holt": ho}, lambda plat: None)
        self.assertEqual(sorted(extra), ["stic"])
        self.assertEqual(notes, ["img:illumos needs a rebuild: its bko-holt isn't what this run tests"])


# --- the conductor --------------------------------------------------------------------------------

class TestBko(Base):
    def test_phase_order_one_copy_locks_once(self):
        ex = self.conduct("BkoTest2 rel4")
        self.assertEqual(ex, 0, "\n".join(self.said))
        kinds = [e[0] for e in self.w.events]
        # the provisional plans of all five, then pre_plan and identity, then the children
        first_run = kinds.index("run")
        plans = [e for e in self.w.events[:kinds.index("pre_plan")] if e[0] == "plan"]
        self.assertEqual([e[1] for e in plans], ["gate", "stic", "holt", "guest", "img"])
        self.assertLess(kinds.index("pre_plan"), kinds.index("identity"))
        self.assertLess(kinds.index("identity"), first_run)
        self.assertEqual(self.w.events[kinds.index("identity")][1], ("arch", "debian12"))   # due stic and holt guests
        runs = self.runs_of()
        self.assertEqual(sorted(e[1] for e in runs[:3]), ["guest", "holt", "stic"])
        self.assertEqual([e[1] for e in runs[3:]], ["gate", "img"])
        run, doc = self.only_run()
        stamp = run[len("t2b-"):]
        self.assertEqual({e[2] for e in runs}, {f"{runmod.RUN_PREFIX[e[1]]}-{stamp}" for e in runs})
        self.assertEqual(len({e[4] for e in runs}), 1)      # one copy
        self.assertEqual(len({e[5] for e in runs}), 1)      # one RunGuards
        self.assertEqual(self.env.copies, [HOSTS])
        self.assertLess(kinds.index("preflight"), first_run)   # guest.preflight, once, before any child
        self.assertEqual(self.env.lock_calls, [ALL_LOCKS])  # once
        for n in ALL_LOCKS:
            self.assertFalse(runmod.FamilyLock(n, self.w.mac).held(), n)   # released
        self.assertEqual(doc["phase"], "done")
        self.assertEqual(set(doc["children"]), {"stic", "holt", "guest", "gate", "img"})
        text = slurp(os.path.join(self.w.mac, "runs", run, "report.txt"))
        self.assertTrue(text.startswith(f"BkoTest2 report: run {run}"), text)
        for w_ in ("SticTest2", "HoltTest2", "GuestTest2", "GateTest2", "ImgTest2"):
            self.assertIn(f"== {w_} ", text)
        self.assertIn("coverage: stic: no check for redox", text)
        self.assertTrue(text.rstrip().splitlines()[-1].startswith("sweep: SweepTest2 never ran"), text)

    def test_plan_form_writes_nothing(self):
        ex = self.conduct("BkoTest2 rel4 plan")
        self.assertEqual(ex, 0)
        self.assertEqual((self.env.lock_calls, self.env.copies, self.runs_of()), ([], [], []))
        self.assertFalse(os.path.exists(os.path.join(self.w.mac, "runs")))
        out = "\n".join(self.said)
        for w_ in ("GateTest2 plan", "SticTest2 plan", "HoltTest2 plan", "GuestTest2 plan", "ImgTest2 plan"):
            self.assertIn(w_, out)
        self.assertTrue(self.said[-2].startswith("combined: 11 due, 11.0 gm"), self.said[-2])
        self.assertTrue(self.said[-1].startswith("sweep: SweepTest2 "), self.said[-1])
        self.assertEqual(out.count("sweep: SweepTest2"), 1, out)     # one sweep line, last
        self.assertNotIn("pre_plan", [e[0] for e in self.w.events])
        self.assertNotIn("preflight", [e[0] for e in self.w.events])

    def test_nothing_due_is_one_line_each_then_the_sweep_line(self):
        for fam, cells in World.CELLS.items():
            for c in cells:
                self.w.row(c, "t2x-old", "PASS")
        t = time.time()
        ex = self.conduct("BkoTest2 rel4")
        self.assertEqual(ex, 0)
        self.assertLess(time.time() - t, 10)
        run, doc = self.only_run()
        text = slurp(os.path.join(self.w.mac, "runs", run, "report.txt")).splitlines()
        self.assertEqual([x.split(":")[0] for x in text[:5]], ["SticTest2", "HoltTest2", "GuestTest2", "GateTest2", "ImgTest2"])
        self.assertTrue(all("nothing due" in x for x in text[:5]), text)
        self.assertTrue(text[-1].startswith("sweep: "))
        self.assertEqual(len(text), 6)
        self.assertNotIn("identity", [e[0] for e in self.w.events])   # no due stic or holt cell
        self.assertNotIn("facts", [e[0] for e in self.w.events])      # nothing ran: no refresh
        self.assertEqual(self.env.copies, [])                          # no copy
        self.assertEqual([e[0] for e in self.w.events].count("preflight"), 1)   # step 2's guest.preflight, always

    def test_identity_failure(self):
        self.w.identity_res = {"arch": {"ok": False, "why": "disk differs from image-state (sha256)"}}
        ex = self.conduct("BkoTest2 rel4")
        self.assertEqual(ex, 1)
        run, doc = self.only_run()
        fail = [r for r in self.w.rows if r["cell"] == "guest:arch" and r["verdict"] == "FAIL"]
        self.assertEqual([r["run"] for r in fail], [run])   # under the composite's id
        self.assertEqual(doc["identity_bad"], {"arch": "disk differs from image-state (sha256)"})
        finals = [e for e in self.w.events if e[0] == "plan" and e[4]]
        self.assertEqual(sorted(e[1] for e in finals), ["holt", "stic"])   # the final plans carry identity_bad
        stic_run = [e for e in self.runs_of() if e[1] == "stic"][0]
        self.assertEqual(stic_run[6], ("stic:debian12",))   # stic:arch not-runnable
        ident = [i for i, e in enumerate(self.w.events) if e[0] == "identity"][0]
        self.assertTrue(all(i > ident for i, e in enumerate(self.w.events) if e[0] == "plan" and e[4]))
        text = slurp(os.path.join(self.w.mac, "runs", run, "report.txt"))
        self.assertIn("guest:arch: identity: disk differs from image-state (sha256)", text)
        self.assertIn("its guest's identity failed", text)   # the not-runnable coverage line

    def test_run_wide_stop_reaches_the_others_and_skips_gate_and_img(self):
        seen = {}

        def stic_trips(world, w_, ctx, shared, out):
            shared.guards.trip("chopin: 1.50 GiB swapped out since the run began")
            ctx.run.stops.stops.append("stop-starts: chopin: 1.50 GiB swapped out since the run began")
            return 1

        def holt_waits(world, w_, ctx, shared, out):
            t = time.time()
            while not shared.guards.tripped() and time.time() - t < 5:
                time.sleep(0.01)
            seen["holt"] = shared.guards.tripped()
            return 1

        self.w.behave = {"stic": stic_trips, "holt": holt_waits}
        ex = self.conduct("BkoTest2 rel4")
        self.assertEqual(ex, 1)
        self.assertTrue(seen["holt"])
        self.assertEqual(sorted(e[1] for e in self.runs_of()), ["guest", "holt", "stic"])   # no gate, no img
        run, doc = self.only_run()
        for f in ("gate", "img"):
            self.assertEqual(doc["children"][f]["state"], "not-run")
            self.assertIn("a run-wide stop rule tripped", doc["children"][f]["why"])
        text = slurp(os.path.join(self.w.mac, "runs", run, "report.txt"))
        self.assertIn("== GateTest2 - (exit 1)\nnot run: a run-wide stop rule tripped (chopin: 1.50 GiB swapped out", text)
        self.assertIn("stops: chopin: 1.50 GiB swapped out since the run began; SticTest2: stop-starts", text)
        self.assertEqual(doc["guards"]["reasons"], ["chopin: 1.50 GiB swapped out since the run began"])

    def test_a_family_stop_stays_in_its_family(self):
        def stic_stops(world, w_, ctx, shared, out):
            ctx.run.stops.stops.append("stop-family: fast gate gate:bin:version FAIL")
            return 1

        self.w.behave = {"stic": stic_stops}
        ex = self.conduct("BkoTest2 rel4")
        self.assertEqual(ex, 1)
        self.assertEqual(sorted(e[1] for e in self.runs_of()), ["gate", "guest", "holt", "img", "stic"])
        run, doc = self.only_run()
        self.assertEqual(doc["guards"]["reasons"], [])
        self.assertEqual(doc["children"]["stic"]["stops"], ["stop-family: fast gate gate:bin:version FAIL"])

    def test_gate_child_leaves_out_what_this_stamp_ran(self):
        self.w.gate_verdict = {"gate:holt": "FAIL"}     # holt's gate step failed it: not run twice
        ex = self.conduct("BkoTest2 rel4")
        self.assertEqual(ex, 1)
        gate = [e for e in self.runs_of() if e[1] == "gate"][0]
        self.assertEqual(gate[6], ("gate:selftest",))
        self.assertEqual(gate[7], ("gate:selftest",))      # its universe is the rest
        self.assertIn("facts", [e[0] for e in self.w.events])   # facts again before it
        stic_run = [e for e in self.runs_of() if e[1] == "stic"][0]
        self.assertIsNone(stic_run[8])   # no --universe: no gate_universe
        self.assertFalse(stic_run[9])    # BkoTest2: no gate_fresh

    def test_an_invalid_pass_in_a_gate_step_runs_again_and_fails_the_exit(self):
        """rel4's gate:bin:version: PASS, but 'not measured: bin/haiku/*-bko'.  Not left out of the
        gate child as if it were done, and no child's exit 0 hides it."""
        self.w.invalid = {"gate:bin:version": "not measured: bin/haiku/*-bko"}
        ex = self.conduct("BkoTest2 rel4")
        self.assertEqual(ex, 1)
        gate = [e for e in self.runs_of() if e[1] == "gate"][0]
        self.assertEqual(gate[6], ("gate:bin:version", "gate:selftest"))   # gate:holt passed in holt's step: left out
        run, doc = self.only_run()
        self.assertEqual(doc["children"]["stic"]["exit"], 1)               # its own plan's cells all passed
        self.assertTrue(any(n.startswith("SticTest2: gate:bin:version PASS, but its row ") and "not measured: bin/haiku" in n
                            for n in doc["notes"]), doc["notes"])
        self.assertTrue(any("ran in this run, but no valid row: again: gate:bin:version" in x for x in self.said), self.said[-20:])

    def test_a_sweep_with_an_invalid_gate_pass_is_incomplete(self):
        self.w.invalid = {"gate:bin:version": "not measured: bin/haiku/*-bko"}
        self.assertEqual(self.conduct("SweepTest2 rel4"), 1)
        run, _ = self.only_run("t2w-")
        sw = runmod.read_json(os.path.join(self.w.mac, "runs", run, "sweep.json"))
        self.assertFalse(sw["complete"])
        self.assertIn("1 due cell(s) with no decisive row: gate:bin:version", sw["missing"])
        doc = runmod.read_json(os.path.join(self.w.mac, "runs", run, "bko.json"))
        self.assertNotEqual(doc["children"]["gate"].get("state"), "skipped")

    def test_no_gate_child_when_nothing_is_left(self):
        self.w.row("gate:selftest", "t2x-old", "PASS")
        self.w.gate_verdict = {"gate:holt": "FAIL"}     # due again, but holt's gate step ran it
        self.assertEqual(self.conduct("BkoTest2 rel4"), 1)
        self.assertNotIn("gate", [e[1] for e in self.runs_of()])
        run, doc = self.only_run()
        self.assertEqual(doc["children"]["gate"]["state"], "skipped")

    def test_extra_due_reaches_the_child_only_at_its_digest(self):
        self.w.binaries = {"img:netbsd": {"rustic-bko": {"state": "carried-other", "digest": H64("a"),
                                                         "canary": "stic:debian12"}}}
        self.w.inp = {"stic:debian12": {"netbsd/rustic-bko": "sha256:" + H64("a")}}
        self.w.row("stic:debian12", "t2x-old", "PASS")
        self.conduct("BkoTest2 rel4")
        run, doc = self.only_run()
        self.assertEqual(list(doc["extra_due"]["stic"]), ["stic:debian12"])
        stic_run = [e for e in self.runs_of() if e[1] == "stic"][0]
        self.assertIn("stic:debian12", stic_run[6])     # canary:carried

    def test_extra_due_at_another_digest_is_a_note(self):
        self.w.binaries = {"img:netbsd": {"rustic-bko": {"state": "carried-other", "digest": H64("a"),
                                                         "canary": "stic:debian12"}}}
        self.w.inp = {"stic:debian12": {"netbsd/rustic-bko": "sha256:" + H64("9")}}
        self.w.row("stic:debian12", "t2x-old", "PASS")
        self.conduct("BkoTest2 rel4")
        run, doc = self.only_run()
        self.assertEqual(doc["extra_due"], {})
        self.assertIn("img:netbsd needs a rebuild: its rustic-bko isn't what this run tests", doc["notes"])
        stic_run = [e for e in self.runs_of() if e[1] == "stic"][0]
        self.assertNotIn("stic:debian12", stic_run[6])

    def test_universe_cuts_each_child_and_the_gate_steps(self):
        ex = self.conduct("BkoTest2 rel4", ["gate:bin:version", "stic:arch", "holt:arch", "guest:arch"])
        self.assertEqual(ex, 0)
        runs = {e[1]: e for e in self.runs_of()}
        self.assertEqual(sorted(runs), ["gate", "guest", "holt", "stic"])   # no img: not named
        self.assertEqual(runs["gate"][6], ())     # its layer ran (and passed) in stic's gate step: nothing due
        self.assertEqual(runs["stic"][7], ("stic:arch",))
        self.assertEqual(runs["stic"][8], ("gate:bin:version",))
        self.assertEqual(runs["holt"][8], ("gate:bin:version",))
        self.assertEqual([r["cell"] for r in self.w.rows if r["cell"].startswith("gate:")], ["gate:bin:version"])

    def test_refused_while_another_run_holds_a_lock(self):
        lk = runmod.FamilyLock("holt", self.w.mac).acquire("t2h-20261110T0000-0000")
        try:
            self.assertEqual(self.conduct("BkoTest2 rel4"), 2)
            self.assertIn("another holt run holds its lock", "\n".join(self.said))
            self.assertEqual(self.runs_of(), [])
            for n in ("bko", "gate", "stic"):
                self.assertFalse(runmod.FamilyLock(n, self.w.mac).held(), n)   # none kept
        finally:
            lk.release()

    def test_a_childs_crash_is_its_own(self):
        def boom(world, w_, ctx, shared, out):
            raise RuntimeError("holt runner broke")

        self.w.behave = {"holt": boom}
        ex = self.conduct("BkoTest2 rel4")
        self.assertEqual(ex, 3)
        run, doc = self.only_run()
        self.assertEqual(doc["children"]["holt"]["exit"], 3)
        self.assertEqual(doc["children"]["stic"]["exit"], 0)
        self.assertEqual(doc["children"]["img"]["state"], "done")


class TestResume(Base):
    def test_a_part_done_parent(self):
        def stic_harness(world, w_, ctx, shared, out):
            shared.guards.add_harness("stic:arch")     # one: below the stop
            return None

        def holt_dies(world, w_, ctx, shared, out):
            runmod.write_json(os.path.join(runmod.run_dir(runmod.new_run_id("holt", stamp=shared.stamp), world.mac),
                                           "plan.json"), shared.plan.to_json())
            raise Crash()

        def gate_dies(world, w_, ctx, shared, out):
            raise Crash()

        self.w.behave = {"stic": stic_harness, "holt": holt_dies, "gate": gate_dies}
        hook = threading.excepthook
        threading.excepthook = lambda a: None
        try:
            with dying(), self.assertRaises(Crash):
                self.conduct("BkoTest2 rel4")
        finally:
            threading.excepthook = hook
        run, doc = self.only_run()
        self.assertEqual(doc["phase"], "gate")
        self.assertEqual((doc["children"]["holt"]["state"], doc["children"]["gate"]["state"]), ("running", "running"))
        self.assertEqual(doc["guards"]["harness"], 1)
        for n in ALL_LOCKS:
            self.assertFalse(runmod.FamilyLock(n, self.w.mac).held(), n)
        before = len(self.runs_of())
        self.w.behave = {}
        env2 = FakeEnv(self.w)
        ex = bko.resume(run, HOSTS, out=self.say, env=env2)
        self.assertEqual(ex, 0, "\n".join(self.said[-30:]))
        again = self.runs_of()[before:]
        self.assertEqual(sorted(e[1] for e in again), ["gate", "holt", "img"])   # stic and guest had ended
        self.assertEqual(env2.resumed, [doc["children"]["holt"]["run"]])          # holt had begun: run.resume
        self.assertEqual({e[3] for e in again}, {run[4:]})                         # the same stamp
        self.assertEqual(env2.lock_calls, [ALL_LOCKS])
        doc2 = runmod.read_json(os.path.join(self.w.mac, "runs", run, "bko.json"))
        self.assertEqual(doc2["guards"]["harness"], 1)     # rebuilt from bko.json, not new
        self.assertEqual(doc2["phase"], "done")
        self.assertTrue(os.path.exists(os.path.join(self.w.mac, "runs", run, "report.txt")))
        self.assertEqual(bko.resume(run, HOSTS, out=self.say, env=FakeEnv(self.w)), 0)   # done: said so
        self.assertIn(f"t2: {run} had finished", self.said)


class TestSweep(Base):
    def sweep_doc(self):
        run, doc = self.only_run("t2w-")
        return run, runmod.read_json(os.path.join(self.w.mac, "runs", run, "sweep.json"))

    def test_complete(self):
        ex = self.conduct("SweepTest2 rel4")
        self.assertEqual(ex, 0, "\n".join(self.said))
        run, sw = self.sweep_doc()
        self.assertTrue(sw["complete"], sw["missing"])
        self.assertEqual(sorted(sw["children"]), ["gate", "guest", "holt", "stic"])
        runs = {e[1]: e for e in self.runs_of()}
        self.assertTrue(runs["stic"][9] and runs["holt"][9])   # gate_fresh
        self.assertEqual(runs["gate"][6], ("gate:selftest",))  # the layers no child ran
        self.assertNotIn("img", runs)
        self.assertEqual(runmod.last_sweep(self.w.mac)["run"], run)
        text = slurp(os.path.join(self.w.mac, "runs", run, "report.txt"))
        self.assertIn("sweep: complete", text)
        self.assertRegex(text.rstrip().splitlines()[-1], r"sweep: SweepTest2 last ran \S+ \(0 days ago\), due in 7 days")
        self.assertIn("coverage: img: panels never passed: linux, freebsd, netbsd,", text)

    def test_panel_ages(self):
        r = self.w.row("img:netbsd", "t2i-20261101T0000-aaaa", "PASS", extra={"panel": {"result": "pass"}})
        r["t"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3 * 86400 - 60))
        self.conduct("SweepTest2 rel4")
        run, _ = self.sweep_doc()
        text = slurp(os.path.join(self.w.mac, "runs", run, "report.txt"))
        self.assertIn("coverage: img: panels passed: netbsd 3 days ago", text)
        self.assertIn("coverage: img: panels never passed: linux, freebsd, dragonfly, illumos, haiku", text)

    def test_a_flip_and_its_packages(self):
        old = self.w.row("stic:arch", "t2s-20261101T0000-aaaa", "PASS")
        old["inputs"] = {"soft": {"arch:generation": "gen:" + "1" * 12}}

        def stic_fails(world, w_, ctx, shared, out):
            world.verdict["stic:arch"] = "FAIL"
            return None

        self.w.behave = {"stic": stic_fails}
        orig = self.w.row

        def row(cell, run, verdict="PASS", kind="run", extra=None):
            r = orig(cell, run, verdict, kind, extra)
            if cell == "stic:arch" and run.startswith("t2s-2026111"):
                r["inputs"] = {"soft": {"arch:generation": "gen:" + "2" * 12}}
            return r

        self.w.row = row
        ex = self.conduct("SweepTest2 rel4")
        self.assertEqual(ex, 1)
        run, sw = self.sweep_doc()
        self.assertTrue(sw["complete"], sw["missing"])   # a FAIL is decisive: the sweep is whole
        text = slurp(os.path.join(self.w.mac, "runs", run, "report.txt"))
        self.assertIn("new verdict at an unchanged key: stic:arch PASS to FAIL", text)
        self.assertIn("packages of arch 111111111111 -> 222222222222: added 1 (pkg zlib); changed 1 (pkg openssl)", text)

    def check_incomplete(self, want):
        run, sw = self.sweep_doc()
        self.assertFalse(sw["complete"])
        self.assertTrue(any(want in m for m in sw["missing"]), sw["missing"])
        self.assertIsNone(runmod.last_sweep(self.w.mac))     # an incomplete sweep doesn't count
        return sw

    def test_incomplete_for_each_stop_kind(self):
        for kind in ("stop-family: fast gate gate:bin:version FAIL", "stop-os linux: need xattrs failed on arch, debian12",
                     "stop-run: 2 harness-class results (last stic:arch)", "stop-starts: chopin: over the budget"):
            with self.subTest(kind=kind):
                self.tearDown()
                self.setUp()

                def stops(world, w_, ctx, shared, out, kind=kind):
                    ctx.run.stops.stops.append(kind)
                    return 1

                self.w.behave = {"holt": stops}
                self.assertEqual(self.conduct("SweepTest2 rel4"), 1)
                self.check_incomplete(f"HoltTest2All: stopped: {kind[:40]}")

    def test_incomplete_when_refused_or_not_started(self):
        self.w.behave = {"guest": lambda *a: 2}
        self.assertEqual(self.conduct("SweepTest2 rel4"), 2)
        self.check_incomplete("GuestTest2All: refused")
        self.tearDown()
        self.setUp()

        def trips(world, w_, ctx, shared, out):
            shared.guards.trip("cinqcent: this run's jobs wrote 11.00 GB, over 2 x the 5 GB budget")
            return 1

        self.w.behave = {"stic": trips}
        self.assertEqual(self.conduct("SweepTest2 rel4"), 1)
        sw = self.check_incomplete("GateTest2All: not run: a run-wide stop rule tripped")
        self.assertNotIn("gate", [e[1] for e in self.runs_of()])

    def test_incomplete_without_a_decisive_row(self):
        self.w.verdict = {"guest:redox": None}      # its job wrote no row
        self.conduct("SweepTest2 rel4")
        self.check_incomplete("due cell(s) with no decisive row: guest:redox")
        self.tearDown()
        self.setUp()
        self.w.verdict = {"holt:arch": "HARNESS"}   # not decisive
        self.conduct("SweepTest2 rel4")
        self.check_incomplete("holt:arch")

    def test_incomplete_on_an_identity_failure(self):
        self.w.identity_res = {"debian12": {"ok": False, "harness": True, "why": "chopin didn't answer"}}
        self.assertEqual(self.conduct("SweepTest2 rel4"), 1)
        self.check_incomplete("guest:debian12: identity: identity unknown: chopin didn't answer")
        self.assertEqual([r for r in self.w.rows if r["cell"] == "guest:debian12" and r["run"].startswith("t2w-")], [])

    def test_incomplete_on_a_hosts_subset(self):
        """`t2 SweepTest2 rel4 --hosts chopin`: every due cell may get its row, yet nothing ran on cinqcent."""
        ex = self.conduct("SweepTest2 rel4", env=FakeEnv(self.w, hosts=["chopin"]))
        self.assertEqual(ex, 1)
        sw = self.check_incomplete("ran on chopin only: cinqcent not swept (--hosts)")
        self.assertEqual((sw["hosts"], sw["hosts_with_facts"]), (["chopin"], ["chopin"]))
        text = slurp(os.path.join(self.w.mac, "runs", sw["run"], "report.txt"))
        self.assertIn("sweep: INCOMPLETE", text)

    def test_a_host_with_no_facts_is_not_swept(self):
        env = FakeEnv(self.w)
        real = env.make_ctx

        def no_cinqcent(word, log=None):
            c = real(word, log)
            c.facts.pop("cinqcent")
            return c
        env.make_ctx = no_cinqcent
        self.conduct("SweepTest2 rel4", env=env)
        sw = self.check_incomplete("no facts from cinqcent: not swept")
        self.assertEqual(sw["hosts_with_facts"], ["chopin"])

    def test_cells_a_host_kept_from_running(self):
        self.w.status = {"holt:debian12": "not-runnable", "guest:redox": "not-runnable"}
        self.w.reasons = {"holt:debian12": ["debian12 runs on cinqcent only, which is unreachable"],
                          "guest:redox": ["blocked: its console probe isn't built"]}
        self.conduct("SweepTest2 rel4")
        sw = self.check_incomplete("1 cell(s) not runnable for want of a host: holt:debian12")
        self.assertFalse([m for m in sw["missing"] if "guest:redox" in m], sw["missing"])   # its own reason: coverage's

    def test_a_cell_not_runnable_for_its_own_reason_leaves_it_complete(self):
        self.w.status = {"guest:redox": "not-runnable"}
        self.w.reasons = {"guest:redox": ["blocked: its console probe isn't built"]}
        self.conduct("SweepTest2 rel4")
        run, sw = self.sweep_doc()
        self.assertTrue(sw["complete"], sw["missing"])
        self.assertEqual(sw["hosts"], HOSTS)

    def test_last_sweep_skips_a_hosts_subset(self):
        d = os.path.join(self.w.mac, "runs")
        for n, hosts in (("t2w-20261101T0000-aaaa", HOSTS), ("t2w-20261102T0000-bbbb", ["chopin"]),
                         ("t2w-20261103T0000-cccc", None)):
            doc = {"run": n, "complete": True, "missing": [], "t": n[4:8] + "-" + n[8:10] + "-" + n[10:12] + "T00:00:00Z"}
            if hosts is not None:
                doc["hosts"] = hosts
            runmod.write_json(os.path.join(d, n, "sweep.json"), doc)
        self.assertEqual(runmod.last_sweep(self.w.mac)["run"], "t2w-20261103T0000-cccc")   # (before hosts were recorded)
        os.remove(os.path.join(d, "t2w-20261103T0000-cccc", "sweep.json"))
        self.assertEqual(runmod.last_sweep(self.w.mac)["run"], "t2w-20261101T0000-aaaa")

    def test_refusals_of_the_words(self):
        for text in ("SweepTest2 stale", "SweepTest2 arch", "SweepTest2All", "BkoTest2 All", "BkoTest2 stic:arch"):
            with self.assertRaises(words.Refused, msg=text):
                words.parse(text.split(), known())


# --- the planner's own plans (tests/fixtures/p2's world) ------------------------------------------------

class RealPlans(FakeEnv):
    """FakeEnv with the real planner over the p2 fixture world; the children only record."""

    def __init__(self, world, p2):
        super().__init__(world)
        self.p2, self.kids = p2, []

    def light_ctx(self):
        return self.p2.ctx()

    def known(self, ctx):
        return planmod.known_names(ctx)

    def make_ctx(self, word, log=None):
        return self.p2.ctx()

    def plan(self, word, ctx):
        return planmod.make(word, ctx)

    def lifecycle(self, word, ctx, shared, out):
        assert isinstance(ctx, planmod.Ctx) and ctx.options is not self.p2_opts, "a ctx of its own"
        self.kids.append((word.word, shared.plan.totals.get("due"), word.sweep, tuple(ctx.options.get("universe") or ())))
        out(report.nothing_due_line(shared.plan) if not shared.plan.totals.get("due") else f"{word.word}: ran")
        return runmod.plan_exit(shared.plan, 0)

    p2_opts = None


class TestRealPlans(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(TESTS, "fixtures", "p2"))
        import world as p2world
        cls.tmp = tempfile.mkdtemp(prefix="t2-bko-p2-")
        cls.p2 = p2world.World(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.mt = tempfile.mkdtemp(prefix="t2-bko-p2m-")
        self.w = World(self.mt)
        self.said = []

    def tearDown(self):
        shutil.rmtree(self.mt, ignore_errors=True)

    def go(self, text):
        env = RealPlans(self.w, self.p2)
        w = words.parse(text.split(), planmod.known_names(self.p2.ctx()))
        w.universe = None
        return bko.conduct(w, HOSTS, self.p2.repo, out=self.said.append, env=env), env

    def test_plan_form(self):
        ex, env = self.go("BkoTest2 rel4 plan")
        self.assertEqual(ex, 0)
        out = "\n".join(self.said)
        for w_ in ("GateTest2 plan:", "SticTest2 plan:", "HoltTest2 plan:", "GuestTest2 plan:", "ImgTest2: nothing due"):
            self.assertIn(w_, out)
        # the golden's 165 cells; its gm summed the cells' raw figures, this the plans' printed ones
        self.assertTrue(self.said[-2].startswith("combined: 166 due, 666.9 gm, "), self.said[-2])
        self.assertTrue(self.said[-1].startswith("sweep: SweepTest2 "), self.said[-1])
        self.assertEqual(out.count("sweep: SweepTest2"), 1)
        self.assertEqual(env.kids, [])

    def test_run_with_real_plans(self):
        ex, env = self.go("BkoTest2 rel4")
        self.assertEqual(sorted(k[0] for k in env.kids), ["GateTest2", "GuestTest2", "HoltTest2", "ImgTest2", "SticTest2"])
        self.assertEqual(ex, 2, "\n".join(self.said[-20:]))     # every image refused: no PASS at its binaries' digests
        run = [n for n in os.listdir(os.path.join(self.w.mac, "runs")) if n.startswith("t2b-")][0]
        text = slurp(os.path.join(self.w.mac, "runs", run, "report.txt"))
        self.assertIn("coverage: not runnable: img:", text)
        self.assertIn("img:openbsd", text)
        self.assertIn("coverage: stic: no check for", text)

    def test_sweep_with_real_plans(self):
        ex, env = self.go("SweepTest2 rel4")
        self.assertEqual(sorted(k[0] for k in env.kids),
                         ["GateTest2All", "GuestTest2All", "HoltTest2All", "SticTest2All"])
        self.assertTrue(all(k[2] for k in env.kids))
        run = [n for n in os.listdir(os.path.join(self.w.mac, "runs")) if n.startswith("t2w-")][0]
        sw = runmod.read_json(os.path.join(self.w.mac, "runs", run, "sweep.json"))
        self.assertFalse(sw["complete"])     # the children wrote no rows
        self.assertEqual(ex, 1)
        text = slurp(os.path.join(self.w.mac, "runs", run, "report.txt"))
        self.assertIn("coverage: img: panels never passed: linux, freebsd, netbsd, dragonfly, illumos, haiku", text)


# --- stic.py's two pieces -------------------------------------------------------------------------

STAMP = "20261110T0312-4f2a"
SRUN = f"t2s-{STAMP}"


class SPC:
    def __init__(self, cell, status="due"):
        self.cell, self.status, self.rule, self.reasons = cell, status, "new", []
        self.keys = {"chopin": H64("9")}
        self.res = {}


class SPlan:
    def __init__(self, cells):
        self.cells, self.classes = cells, []
        self.set = {"name": "roundrel4", "round": "rel4", "dir": "~/backotter-work/roundrel4/bin", "pending": None}
        self.word = argparse.Namespace(text="SticTest2All rel4", form="All", family="stic")

    def cell(self, n):
        return next((c for c in self.cells if c.cell == n), None)

    def due(self):
        return [c for c in self.cells if c.status == "due"]

    def by_status(self, s):
        return [c for c in self.cells if c.status == s]


class SRS:
    """run.py's RunState as SticRun reads it, with the composite's guards."""

    def __init__(self, ctx, plan, mac_dir, guards):
        self.run, self.word, self.plan, self.ctx = SRUN, argparse.Namespace(text="SticTest2All rel4", family="stic"), plan, ctx
        self.mac_dir, self.hosts, self.flakes = mac_dir, ["chopin"], []
        self.copy = {"chopin": {"id": "c1", "path": "/home/m/c/c1", "commit": "c" * 40, "dirty": {}}}
        self.guards = guards
        self.stops = runmod.StopEngine("stic", CLASSES.get("policy"), guards=guards)

    def host_run_dir(self, h):
        return "/home/m/s/runs/" + SRUN

    def write_cells(self, plan, attempt=1, name=None, reruns=None):
        return {"chopin": "/home/m/s/runs/" + SRUN + "/" + (name or "cells.json")}

    def log(self, text):
        pass


class SGate:
    def __init__(self, due):
        self.due_cells, self.ran = list(due), []

    def family_layers(self, family):
        return {"fast": ["fork:restic", "bin:abi"], "slow": ["loop:linux"]}

    def gate_plan(self, ctx):
        return argparse.Namespace(due=lambda: [argparse.Namespace(cell=c) for c in self.due_cells])

    def run_layers(self, ctx, names, part=None, plan=None):
        self.ran.append((part, list(names)))
        return {"ok": True, "exit": 0, "cells": []}


class SProc:
    def __init__(self, env):
        self.env, self.n, self.pid = env, 0, 4242

    def poll(self):
        self.n += 1
        if self.n == 1 and self.env.trip_at_first_poll:
            self.env.guards.trip("cinqcent: 1.20 GiB swapped out since the run began")
        if self.n >= 3:
            self.env.landed.append({"id": "chopin-1", "t": "2026-11-10T03:20:00Z", "run": SRUN, "origin": f"stic:{SRUN}:arch:1",
                                    "cell": "stic:arch", "verdict": "PASS", "class": "pass", "needs": {"a": "PASS"},
                                    "valid": True, "host": "chopin", "gm": 1.0, "wall_s": 60, "kind": "run"})
            return 0
        return None


class SEnv:
    def __init__(self, gate, guards, rows=()):
        self.gate_mod, self.guards, self.landed = gate, guards, list(rows)
        self.spawned, self.logs, self.trip_at_first_poll = [], [], False

    def now(self):
        return time.time()

    def sleep(self, s):
        pass

    def log(self, m):
        self.logs.append(m)

    def rows(self, hosts):
        return list(self.landed)

    def spawn(self, argv, log, env):
        self.spawned.append(argv)
        return SProc(self)

    def gate(self):
        return self.gate_mod


class SCtx:
    def __init__(self, options):
        self.checkout, self.hosts, self.facts, self.ledger = REPO, ["chopin"], {"chopin": {}}, []
        self.classes, self.options = CLASSES, dict(options)
        self.drive = argparse.Namespace(all_guests=lambda: ["arch"], load_specs=lambda gs: {g: argparse.Namespace(os="linux") for g in gs})


class TestSticPieces(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2-bko-stic-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sticrun(self, options, gate, rows=(), guards=None):
        guards = guards or runmod.RunGuards(CLASSES.get("policy"), sweep=True)
        ctx = SCtx(options)
        plan = SPlan([SPC("stic:arch")])
        ctx.run = SRS(ctx, plan, self.tmp, guards)
        env = SEnv(gate, guards, rows)
        return stic.SticRun(ctx, plan, env), env, guards

    def gate_row(self, cell, run, verdict="PASS"):
        return {"id": f"x-{cell}", "t": "2026-11-10T03:15:00Z", "cell": cell, "run": run, "kind": "run",
                "verdict": verdict, "valid": True}

    def test_gate_fresh_runs_what_this_stamp_didnt(self):
        gate = SGate(["gate:bin:abi", "gate:fork:restic", "gate:holt"])
        rows = [self.gate_row("gate:bin:abi", f"t2s-{STAMP}"),               # its own first part
                self.gate_row("gate:fork:restic", "t2s-20261101T0000-aaaa")]  # another sweep's: runs again
        sr, env, _ = self.sticrun({"gate_fresh": True}, gate, rows)
        sr.setup()
        ok, refused = sr.gate_step("fast")
        self.assertEqual((ok, refused), (True, None))
        self.assertEqual(gate.ran, [("fast", ["gate:fork:restic"])])
        self.assertTrue(any("gate:bin:abi already ran in this sweep" in m for m in env.logs), env.logs)

    def test_gate_fresh_doesnt_run_a_failed_layer_twice(self):
        gate = SGate(["gate:bin:abi", "gate:fork:restic"])
        sr, env, _ = self.sticrun({"gate_fresh": True}, gate, [self.gate_row("gate:bin:abi", f"t2g-{STAMP}", "FAIL")])
        sr.setup()
        self.assertEqual(sr.gate_step("fast"), (False, None))
        self.assertEqual(gate.ran, [])
        res = sr.go()   # the family stops: no guest starts
        self.assertEqual(env.spawned, [])
        self.assertTrue(res["stops"][0].startswith("stop-family: the fast archiver gate failed"), res["stops"])

    def test_without_gate_fresh_the_rows_arent_read(self):
        gate = SGate(["gate:bin:abi"])
        sr, env, _ = self.sticrun({}, gate, [self.gate_row("gate:bin:abi", f"t2s-{STAMP}")])
        sr.setup()
        self.assertEqual(sr.gate_step("fast"), (True, None))
        self.assertEqual(gate.ran, [("fast", ["gate:bin:abi"])])

    def test_gate_universe_cuts_the_layers(self):
        gate = SGate(["gate:bin:abi", "gate:fork:restic"])
        sr, env, _ = self.sticrun({"gate_universe": ["gate:bin:abi"]}, gate)
        sr.setup()
        sr.gate_step("fast")
        self.assertEqual(gate.ran, [("fast", ["gate:bin:abi"])])
        gate2 = SGate(["gate:bin:abi"])
        sr, env, _ = self.sticrun({"gate_universe": []}, gate2)
        sr.setup()
        self.assertEqual(sr.gate_step("fast"), (True, None))
        self.assertEqual(gate2.ran, [])

    def test_guards_tripped_before_the_round_start_no_drive(self):
        guards = runmod.RunGuards(CLASSES.get("policy"), sweep=True)
        guards.trip("chopin: 1.10 GiB swapped out since the run began")
        sr, env, _ = self.sticrun({}, SGate([]), guards=guards)
        res = sr.go()
        self.assertEqual(env.spawned, [])
        self.assertEqual(res["exit"], 1)
        self.assertEqual(res["stops"], ["stop-starts: a run-wide stop rule tripped: chopin: 1.10 GiB swapped out since the run began"])
        self.assertEqual([c["outcome"] for c in res["cells"] if c["cell"] == "stic:arch"], ["not-started"])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "stop")))

    def test_guards_tripped_by_another_family_mid_round(self):
        sr, env, guards = self.sticrun({}, SGate([]))
        env.trip_at_first_poll = True
        res = sr.go()
        self.assertEqual(len(env.spawned), 1)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "stop")))    # drive.py's --stop-file
        self.assertEqual(res["stops"], ["stop-starts: a run-wide stop rule tripped: cinqcent: 1.20 GiB swapped out since the run began"])
        self.assertEqual(res["exit"], 1)


# --- the review's findings (K6 fix round) ---------------------------------------------------------

def quiet_threads():
    hook = threading.excepthook
    threading.excepthook = lambda a: None
    return hook


class TestFixPieces(unittest.TestCase):
    def test_an_invalid_row_isnt_decisive(self):
        rows = [{"cell": "stic:a", "run": f"t2s-{STAMP}", "kind": "run", "verdict": "PASS", "valid": False},
                {"cell": "stic:b", "run": f"t2s-{STAMP}", "kind": "run", "verdict": "FAIL", "valid": True},
                {"cell": "stic:c", "run": f"t2s-{STAMP}", "kind": "carried", "verdict": "PASS", "valid": True}]
        self.assertEqual(bko.decisive_cells(rows, STAMP), {"stic:b", "stic:c"})

    def test_judged_exit_is_never_0_by_default(self):
        ok = {"cell": "stic:a", "kind": "run", "verdict": "PASS", "valid": True, "t": "1", "id": "a"}
        self.assertEqual(bko.judged_exit({"due_cells": ["stic:a"]}, [ok]), 0)
        self.assertEqual(bko.judged_exit({"due_cells": ["stic:a"], "stops": ["stop-starts: x"]}, [ok]), 1)
        self.assertEqual(bko.judged_exit({"due_cells": ["stic:a", "stic:b"]}, [ok]), 1)        # b never ran
        self.assertEqual(bko.judged_exit({"due_cells": None}, [ok]), 1)                         # not known
        self.assertEqual(bko.judged_exit({"due_cells": ["stic:a"]}, [dict(ok, valid=False)]), 1)
        self.assertEqual(bko.judged_exit({"due_cells": []}, []), 0)

    def test_guards_count_each_familys_share_and_say_when_they_change(self):
        g = bko.Guards(CLASSES.get("policy"))
        seen = []
        g.on_change = lambda: seen.append(g.to_json()["harness"])
        g.add_harness("stic:arch")
        g.add_harness("holt:arch")
        self.assertEqual(seen, [1, 2])
        self.assertEqual(g.to_json()["by_family"], {"stic": 1, "holt": 1})
        g2 = bko.Guards.from_json(json.loads(json.dumps(g.to_json())), CLASSES.get("policy"))
        self.assertIsInstance(g2, bko.Guards)
        self.assertEqual(g2.forget("stic"), 1)
        self.assertEqual((g2.harness, g2.by_family), (1, {"holt": 1}))


class TestFixes(Base):
    def dies_after_plan(self, fam, then=None):
        def fn(world, w_, ctx, shared, out):
            run = runmod.new_run_id(fam, stamp=shared.stamp)
            runmod.write_json(os.path.join(runmod.run_dir(run, world.mac), "plan.json"), shared.plan.to_json())
            runmod.write_json(os.path.join(runmod.run_dir(run, world.mac), "cells-chopin.json"), {"copy": {}})
            if then:
                then(world, shared, run)
            raise Crash()
        return fn

    def crash(self, text, behave):
        """TEXT run until BEHAVE's child dies; the main thread dies too (in the gate step, when
        BEHAVE has none for it), as the Mac process would."""
        def gate_dies(world, w_, ctx, shared, out):
            raise Crash()

        self.w.behave = dict({"gate": gate_dies}, **behave)
        hook = quiet_threads()
        try:
            with dying(), self.assertRaises(Crash):
                self.conduct(text)
        finally:
            threading.excepthook = hook
        self.w.behave = {}
        return self.only_run("t2w-" if text.startswith("Sweep") else "t2b-")

    # high: a child's report.txt, finished by hand, keeps its stop and its unrun cells
    def test_resume_takes_a_hand_finished_childs_stops_and_due_cells(self):
        def holt_partly(world, w_, ctx, shared, out):
            world.row("holt:arch", runmod.new_run_id("holt", stamp=shared.stamp), "PASS")   # holt:debian12 never ran
            return None

        run, doc = self.crash("SweepTest2 rel4", {"holt": self.dies_after_plan("holt", lambda w, sh, r: holt_partly(w, None, None, sh, None))})
        hrun = doc["children"]["holt"]["run"]
        # the next run names the composite, not the child
        self.said = []
        self.assertEqual(self.conduct("BkoTest2 rel4"), 2)
        self.assertIn(f"`t2 resume {run}` finishes it (not `t2 resume {hrun}`", "\n".join(self.said))
        # the child finished by hand anyway: its report names a stop
        runmod.write_text(os.path.join(runmod.run_dir(hrun, self.w.mac), "report.txt"),
                          f"HoltTest2All report: run {hrun}\nstops: stop-starts: chopin: 1.20 GiB swapped out since the run began\n")
        ex = bko.resume(run, HOSTS, out=self.say, env=FakeEnv(self.w))
        self.assertEqual(ex, 1, "\n".join(self.said[-20:]))
        doc2 = runmod.read_json(os.path.join(self.w.mac, "runs", run, "bko.json"))
        h = doc2["children"]["holt"]
        self.assertEqual((h["state"], h["exit"]), ("done", 1))
        self.assertEqual(h["stops"], ["stop-starts: chopin: 1.20 GiB swapped out since the run began"])
        self.assertEqual(sorted(h["due_cells"]), ["holt:arch", "holt:debian12"])
        sw = runmod.read_json(os.path.join(self.w.mac, "runs", run, "sweep.json"))
        self.assertFalse(sw["complete"])
        self.assertTrue(any("holt:debian12" in m for m in sw["missing"]), sw["missing"])
        self.assertIsNone(runmod.last_sweep(self.w.mac))

    def test_a_report_without_a_stop_but_an_unrun_cell_isnt_0(self):
        run, doc = self.crash("BkoTest2 rel4", {"holt": self.dies_after_plan("holt")})
        hrun = doc["children"]["holt"]["run"]
        runmod.write_text(os.path.join(runmod.run_dir(hrun, self.w.mac), "report.txt"), "HoltTest2 report\nstops: none\n")
        self.assertEqual(bko.resume(run, HOSTS, out=self.say, env=FakeEnv(self.w)), 1)

    # high: SticTest2's refeed doesn't count a harness result twice
    def test_resume_counts_sticks_harness_once(self):
        def harness(world, shared, run):
            shared.guards.add_harness("stic:arch")
        run, doc = self.crash("BkoTest2 rel4", {"stic": self.dies_after_plan("stic", harness)})
        self.assertEqual(doc["guards"]["harness"], 1)              # saved when it changed
        self.assertEqual(doc["guards"]["by_family"], {"stic": 1})
        env2 = FakeEnv(self.w)
        env2.refeed = {"stic": lambda shared: shared.guards.add_harness("stic:arch")}   # stic.resume's refeed
        ex = bko.resume(run, HOSTS, out=self.say, env=env2)
        self.assertEqual(ex, 0, "\n".join(self.said[-20:]))
        doc2 = runmod.read_json(os.path.join(self.w.mac, "runs", run, "bko.json"))
        self.assertEqual((doc2["guards"]["harness"], doc2["guards"]["reasons"]), (1, []))
        self.assertEqual(doc2["children"]["img"]["state"], "done")    # not stopped by a false trip

    def test_a_holt_harness_isnt_forgotten(self):
        def harness(world, shared, run):
            shared.guards.add_harness("holt:arch")
        run, doc = self.crash("BkoTest2 rel4", {"holt": self.dies_after_plan("holt", harness)})
        env2 = FakeEnv(self.w)
        env2.refeed = {"stic": lambda shared: shared.guards.add_harness("stic:arch")}   # (stic had ended: no refeed)
        bko.resume(run, HOSTS, out=self.say, env=env2)
        doc2 = runmod.read_json(os.path.join(self.w.mac, "runs", run, "bko.json"))
        self.assertEqual(doc2["guards"]["harness"], 1)

    # high: Ctrl-C trips the guards and keeps the locks until the children end
    def test_interrupt_waits_for_the_children_before_the_locks_go(self):
        main = threading.main_thread().ident
        seen = {}

        def holt(world, w_, ctx, shared, out):
            signal.pthread_kill(main, signal.SIGINT)
            t = time.time()
            while not shared.guards.tripped() and time.time() - t < 5:
                time.sleep(0.01)
            seen["tripped"] = shared.guards.why()
            time.sleep(0.3)
            seen["locks"] = [n for n in ALL_LOCKS if runmod.FamilyLock(n, world.mac).held()]
            seen["sigint"] = signal.getsignal(signal.SIGINT)
            return 1

        self.w.behave = {"holt": holt}
        old = signal.signal(signal.SIGINT, signal.default_int_handler)   # (whatever an earlier test left)
        self.addCleanup(signal.signal, signal.SIGINT, old)
        before = signal.getsignal(signal.SIGINT)
        ex = self.conduct("BkoTest2 rel4")
        self.assertEqual(ex, 130)
        self.assertEqual(seen["tripped"], [bko.INTERRUPTED])
        self.assertEqual(seen["locks"], ALL_LOCKS)              # held while a child still ran
        self.assertEqual(seen["sigint"], signal.SIG_IGN)        # a second Ctrl-C is ignored meanwhile
        self.assertIs(signal.getsignal(signal.SIGINT), before)  # and restored after
        for n in ALL_LOCKS:
            self.assertFalse(runmod.FamilyLock(n, self.w.mac).held(), n)
        run, doc = self.only_run()
        self.assertEqual(doc["phase"], "children")
        self.assertIn("interrupted", doc)
        self.assertFalse(os.path.exists(os.path.join(self.w.mac, "runs", run, "report.txt")))
        self.assertNotIn("gate", [e[1] for e in self.runs_of()])
        self.assertTrue(all(doc["children"][f]["state"] == "done" for f in ("stic", "holt", "guest")))
        # the resume goes on: the interrupt isn't a stop rule
        self.w.behave = {}
        ex = bko.resume(run, HOSTS, out=self.say, env=FakeEnv(self.w))
        doc2 = runmod.read_json(os.path.join(self.w.mac, "runs", run, "bko.json"))
        self.assertEqual(doc2["guards"]["reasons"], [])
        self.assertEqual(doc2["phase"], "done")
        self.assertIn("gate", [e[1] for e in self.runs_of()])
        self.assertEqual(ex, 1)     # holt's own exit 1

    # medium: a child thread's BaseException is its exit 3
    def test_a_system_exit_in_a_child_is_exit_3(self):
        def boom(world, w_, ctx, shared, out):
            raise SystemExit(5)

        self.w.behave = {"holt": boom}
        self.assertEqual(self.conduct("BkoTest2 rel4"), 3)
        run, doc = self.only_run()
        self.assertEqual((doc["children"]["holt"]["state"], doc["children"]["holt"]["exit"]), ("done", 3))
        self.assertIn("SystemExit", doc["children"]["holt"]["why"])

    # medium: pre_plan's identity failures reach the composite
    def test_pre_plans_identity_failure_counts(self):
        for c in [c for cs in World.CELLS.values() for c in cs]:
            self.w.row(c, "t2x-old", "PASS")

        def pre_plan(ctx, word_, out):
            self.w.ev("pre_plan", word_.family)
            self.w.row("guest:redox", runmod.new_run_id("guest"), "FAIL")
            out("disks differ from their merge records: redox")
            out("guest:redox: identity FAIL: disk differs from image-stat.json")

        self.env.guest_mod.pre_plan = pre_plan
        pulls = self.env.pulls
        self.assertEqual(self.conduct("BkoTest2 rel4"), 1)
        run, doc = self.only_run()
        self.assertEqual(doc["identity_bad"], {"redox": "disk differs from image-stat.json"})
        self.assertGreater(self.env.pulls, pulls)                    # the rows again: its FAIL row is seen
        self.assertNotIn("redox", [g for e in self.w.events if e[0] == "identity" for g in e[1]])   # not asked twice
        text = slurp(os.path.join(self.w.mac, "runs", run, "report.txt"))
        self.assertIn("guest:redox: identity: disk differs from image-stat.json", text)

    # medium: a carried canary's guest is identity-checked before it runs
    def test_a_carried_canarys_guest_is_identity_checked(self):
        for c in [c for cs in World.CELLS.values() for c in cs]:
            self.w.row(c, "t2x-old", "PASS")
        self.w.binaries = {"img:netbsd": {"rustic-bko": {"state": "carried-other", "digest": H64("a"),
                                                         "canary": "stic:debian12"}}}
        self.w.inp = {"stic:debian12": {"netbsd/rustic-bko": "sha256:" + H64("a")}}
        self.w.identity_res = {"debian12": {"ok": False, "why": "disk differs from image-state (sha256)"}}
        self.assertEqual(self.conduct("BkoTest2 rel4"), 1)
        ident = [e for e in self.w.events if e[0] == "identity"]
        self.assertEqual([e[1] for e in ident], [("debian12",)])
        stic_runs = [e for e in self.runs_of() if e[1] == "stic"]
        self.assertTrue(all("stic:debian12" not in e[6] for e in stic_runs), stic_runs)

    # medium: --universe only in a test root; sweep.json says it was cut
    def test_universe_is_refused_in_the_default_root(self):
        self.env.mac_root = None
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("T2_MAC_ROOT", None)
            self.assertEqual(self.conduct("SweepTest2 rel4", ["stic:arch"]), 2)
        self.assertIn("--universe is test-only", "\n".join(self.said))
        self.assertEqual(self.env.lock_calls, [])

    def test_a_cut_sweep_records_its_universe(self):
        self.conduct("SweepTest2 rel4", ["stic:arch", "holt:arch", "guest:arch", "gate:bin:version"])
        run, _ = self.only_run("t2w-")
        sw = runmod.read_json(os.path.join(self.w.mac, "runs", run, "sweep.json"))
        self.assertEqual(sw["universe"], ["stic:arch", "holt:arch", "guest:arch", "gate:bin:version"])

    # medium: a resume isn't refused by its own img panel run
    def test_resume_during_the_panel(self):
        def img_dies(world, w_, ctx, shared, out):
            panel = runmod.new_run_id("holt", stamp=shared.stamp, suffix="img")
            runmod.write_json(os.path.join(runmod.run_dir(panel, world.mac), "cells-chopin.json"), {"copy": {}})
            raise Crash()

        run, doc = self.crash("BkoTest2 rel4", {"img": img_dies, "gate": None})
        self.assertEqual(doc["phase"], "img")
        ex = bko.resume(run, HOSTS, out=self.say, env=FakeEnv(self.w))
        self.assertNotEqual(ex, 2, "\n".join(self.said[-10:]))
        self.assertNotIn("is unfinished", "\n".join(self.said))

    # medium: a gate child that began is set aside (gate.py has no resume)
    def test_a_begun_gate_child_is_set_aside(self):
        run, doc = self.crash("BkoTest2 rel4", {"gate": self.dies_after_plan("gate")})
        grun = doc["children"]["gate"]["run"]
        self.assertEqual(runmod.unfinished_runs("gate", self.w.mac), [grun])
        before = len(self.runs_of())
        ex = bko.resume(run, HOSTS, out=self.say, env=FakeEnv(self.w))
        self.assertEqual(ex, 1, "\n".join(self.said[-10:]))
        self.assertEqual(runmod.unfinished_runs("gate", self.w.mac), [])
        self.assertIn("has no resume()", slurp(os.path.join(runmod.run_dir(grun, self.w.mac), "report.txt")))
        doc2 = runmod.read_json(os.path.join(self.w.mac, "runs", run, "bko.json"))
        self.assertEqual(doc2["children"]["gate"]["exit"], 1)
        self.assertNotIn(grun, [e[2] for e in self.runs_of()[before:]])
        self.assertEqual(self.conduct("BkoTest2 rel4"), 0, "\n".join(self.said[-10:]))   # nothing blocks the next

    def test_a_begun_gate_child_whose_job_may_run_isnt_set_aside(self):
        run, doc = self.crash("BkoTest2 rel4", {"gate": self.dies_after_plan("gate")})
        grun = doc["children"]["gate"]["run"]
        env2 = FakeEnv(self.w)
        env2.set_aside_leftovers = lambda r, out: ("chopin", 4242)
        self.assertEqual(bko.resume(run, HOSTS, out=self.say, env=env2), 2)
        self.assertEqual(runmod.unfinished_runs("gate", self.w.mac), [grun])     # its host work may go on
        doc2 = runmod.read_json(os.path.join(self.w.mac, "runs", run, "bko.json"))
        self.assertEqual(doc2["children"]["gate"]["exit"], 2)
        self.assertIn("pid 4242 on chopin", doc2["children"]["gate"]["why"])

    # low: an error before any child leaves a finished record
    def test_an_error_before_the_children_finishes_the_record(self):
        def bad_identity(ctx, guests, run, log):
            raise KeyError("x")

        self.env.identity = bad_identity
        with self.assertRaises(KeyError):
            self.conduct("BkoTest2 rel4")
        run, doc = self.only_run()
        self.assertEqual(doc["phase"], "done")
        self.assertIn("stopped before any child began: KeyError", slurp(os.path.join(self.w.mac, "runs", run, "report.txt")))
        self.assertEqual(runmod.unfinished_runs(None, self.w.mac), [])

    # low: a stamp a run dir of this root has isn't used again
    def test_a_used_stamp_isnt_taken(self):
        os.makedirs(os.path.join(self.w.mac, "runs", "t2h-20261110T0300-aaaa"))
        stamps = iter(["20261110T0300-aaaa", "20261110T0300-bbbb"])
        with mock.patch.object(runmod, "new_stamp", lambda: next(stamps)):
            self.conduct("BkoTest2 rel4")
        run, _ = self.only_run()
        self.assertEqual(run, "t2b-20261110T0300-bbbb")

    def test_the_env_pull_is_said(self):
        e = bko.Env(REPO, HOSTS, mac_root=self.w.mac)
        c = bko.Conductor(word("BkoTest2 rel4"), HOSTS, REPO, out=self.say, env=e)
        self.assertEqual(e.log, c.envlog)


class TestSticGuardPieces(TestSticPieces):
    def test_the_slow_gate_doesnt_start_after_a_trip(self):
        guards = runmod.RunGuards(CLASSES.get("policy"), sweep=True)
        guards.trip("chopin: 1.10 GiB swapped out since the run began")
        gate = SGate(["gate:loop:linux"])
        sr, env, _ = self.sticrun({}, gate, guards=guards)
        res = sr.go()
        self.assertNotIn("slow", [p for p, _ in gate.ran])
        self.assertFalse(any("slow gate" in s for s in res["stops"]), res["stops"])

    def test_after_an_earlier_drive_a_trip_keeps_what_it_did(self):
        sr, env, guards = self.sticrun({}, SGate([]))
        polls = {"n": 0}

        def busy(out):
            polls["n"] += 1
            if polls["n"] == 2:
                guards.trip("chopin: 1.10 GiB swapped out since the run began")
            return polls["n"] <= 3

        env.driver_busy = busy
        env.drive_state = lambda out: {"jobs": {"arch": {}}}
        sr.setup()
        sr.drive_round(SRUN, ["arch"], "/x/cells.json", 1)
        d = sr.state["drives"][SRUN]
        self.assertEqual(env.spawned, [])
        self.assertEqual(d["started"], ["arch"])
        self.assertIn("the earlier drive.py ended", d["skipped"])


if __name__ == "__main__":
    unittest.main()
