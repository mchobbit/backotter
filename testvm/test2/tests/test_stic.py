"""SticTest2's package (P4): check-manifests.py, the BKO_TESTVM lint, drive.py's
self-test, rows_stic over fixture log dirs, and stic.run with fake drive events
(contract U4.1-U4.5; rulings R3, R5, R6, R10, R26, R28).  No host, no ssh."""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(TESTS)
REPO = os.path.dirname(os.path.dirname(T2))
FIX = os.path.join(TESTS, "fixtures", "p4")
sys.path.insert(0, T2)

from t2lib import keys, rows_stic, stic  # noqa: E402


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


CM = load_module("check_manifests", os.path.join(T2, "stic", "check-manifests.py"))
DRIVE = load_module("st1_drive_test", os.path.join(REPO, "testvm", "stictest1", "drive.py"))
MARK = "t2s-20261108T0315-4f2a"
H64 = lambda c: c * 64


def load_json(path):
    with open(path) as f:
        return json.load(f)


def fixture(name):
    with open(os.path.join(FIX, "runouts", name)) as f:
        return f.read()


# --- U4.1: check-manifests.py --------------------------------------------------------------

class TestManifests(unittest.TestCase):
    def test_58_checks_have_complete_manifests(self):
        files = CM.repo_files()
        errs, reps, data = CM.check_stic(files)
        self.assertEqual(errs, [], "\n".join(errs[:20]))
        entries = CM.spec_entries(data)
        self.assertEqual(len(entries), 58)
        self.assertEqual(sorted(entries), DRIVE.all_guests())

    def test_entries_name_what_the_checks_share(self):
        data = CM.load_json(CM.MANIFESTS)
        e = CM.spec_entries(data)
        self.assertIn("testvm/stictest1/ubuntu24/real.sh", e["ubuntu26-zfs"]["harness"])   # ubuntu26-zfs runs it
        self.assertIn("testvm/stictest1/freebsd15/extra.sh", e["ghostbsd26-zfs"]["harness"])
        self.assertIn("testvm/stictest1/freebsd15-ufs/extras.sh", e["hardenedbsd15"]["harness"])
        self.assertEqual(e["haiku6"]["bins"], ["haiku/rustic-bko", "linux/restic-bko"])
        self.assertEqual(e["alpinebsd"]["xguest"], ["netbsd11", "host:~/backotter-work/netbsd-tools"])
        for g in ("sculpt2610", "haiku6", "artix-dinit", "artix-s6", "arch"):
            self.assertIn("kernel", e[g]["host"], g)
        self.assertNotIn("host", e["debian12"])
        for g, ent in e.items():   # R5 (d): what reads records is judge
            self.assertIn(f"entry:testvm/test2/stic/limits.json#{g}", ent["judge"])
            for p in ent["harness"]:
                self.assertFalse(p.endswith(("verdict.py", "expected-coverage.json")), (g, p))
        self.assertNotIn("testvm/vm-lib.sh", sum((x["harness"] for x in e.values()), []))   # plumbing only

    def test_p1_resolves_every_entry(self):
        src = keys.TreeSource(REPO)
        specs = keys.Specs(src)
        facts = fake_facts(DRIVE.all_guests(), "roundtest")
        for g in DRIVE.all_guests():
            m = keys.measure("stic:" + g, src, facts, specs, {"set": "roundtest"})
            self.assertEqual(m["unknown"], [], g)
            self.assertTrue(any(n.endswith("/check.sh") for n in m["inputs"]["harness"]), g)
            self.assertIn(f"testvm/test2/stic/limits.json#{g}", m["inputs"]["judge"])

    def _repo(self, tmp, check_sh, manifest, extra=None):
        os.makedirs(os.path.join(tmp, "testvm", "stictest1", "g1"))
        os.makedirs(os.path.join(tmp, "testvm", "compare"))
        os.makedirs(os.path.join(tmp, "testvm", "test2", "stic"))
        shutil.copy(os.path.join(REPO, "testvm", "stictest1", "drive.py"), os.path.join(tmp, "testvm", "stictest1"))
        os.makedirs(os.path.join(tmp, "testvm", "test2", "t2lib"))   # drive.py's host table
        shutil.copy(os.path.join(REPO, "testvm", "hosts.json"), os.path.join(tmp, "testvm"))
        shutil.copy(os.path.join(REPO, "testvm", "test2", "t2lib", "hosts.py"), os.path.join(tmp, "testvm", "test2", "t2lib"))
        for p, t in dict({"testvm/stictest1/g1/check.sh": check_sh, "testvm/compare/helper.sh": "#!/bin/sh\necho\n",
                          "testvm/compare/other.sh": "#!/bin/sh\n", "testvm/vm-lib.sh": "# lib\n",
                          "testvm/test2/stic/manifests.json": json.dumps(manifest),
                          "testvm/test2/stic/limits.json": "{}"}, **(extra or {})).items():
            with open(os.path.join(tmp, p), "w") as f:
                f.write(t)
        return tmp

    def _check(self, tmp):
        old = CM.REPO
        CM.REPO = tmp
        try:
            return CM.check_stic(CM.repo_files(tmp))
        finally:
            CM.REPO = old

    CHECK = ("#!/usr/bin/env bash\n# BIN_DIR: roundN/bin/linux\nHERE=$(cd \"$(dirname \"$0\")\" && pwd)\n"
             "WORK=$HOME/backotter-work\nC=${BKO_TESTVM:-$WORK/testvm}/compare\n"
             ". \"${BKO_TESTVM:-$WORK/testvm}/vm-lib.sh\"\nfor f in helper.sh; do vm_put \"$C/$f\" /root/$f; done\n"
             "\"$WORK/cmp-run.sh\" --reserve 2 2 true\n")

    def _manifest(self, **over):
        e = {"os": "linux", "bins": ["linux/restic-bko", "linux/rustic-bko"],
             "harness": ["testvm/stictest1/g1/**", "testvm/compare/helper.sh"],
             "judge": ["entry:testvm/test2/stic/limits.json#g1"]}
        e.update(over)
        return {"v": 1, "plumbing": ["testvm/vm-lib.sh", "shared:cmp-run.sh"], "entries": {"g1": e}}

    def test_fixture_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            errs, _, _ = self._check(self._repo(tmp, self.CHECK, self._manifest()))
            self.assertEqual(errs, [])

    def test_fixture_finds_a_missing_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = self._manifest(harness=["testvm/stictest1/g1/**"])
            errs, _, _ = self._check(self._repo(tmp, self.CHECK, m))
            self.assertTrue(any("uses testvm/compare/helper.sh: not in its manifest" in e for e in errs), errs)

    def test_fixture_follows_source_and_python_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            sh = self.CHECK + "source \"$C/lib2.sh\"\npython3 \"$HERE/judge.py\"\n"
            extra = {"testvm/compare/lib2.sh": "here=$(cd \"$(dirname \"$0\")\" && pwd)\n. \"$here/other.sh\"\n",
                     "testvm/stictest1/g1/judge.py": "import json\nimport fsmeta\n",
                     "testvm/compare/fsmeta.py": "x = 1\n"}
            m = self._manifest(harness=["testvm/stictest1/g1/**", "testvm/compare/helper.sh"])
            errs, _, _ = self._check(self._repo(tmp, sh, m, extra))
            text = "\n".join(errs)
            self.assertIn("uses testvm/compare/lib2.sh", text)
            self.assertIn("uses testvm/compare/other.sh", text)      # through the source chain
            self.assertIn("uses testvm/compare/fsmeta.py", text)     # a Python import, against testvm/compare
            self.assertIn("judge.py reads records: list it under judge too", text)

    def test_fixture_dead_paths_bins_and_plumbing(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = self._manifest(harness=["testvm/stictest1/g1/**", "testvm/compare/helper.sh", "testvm/compare/gone.sh",
                                        "testvm/vm-lib.sh"], bins=["linux/rustic-bko"])
            errs, _, _ = self._check(self._repo(tmp, self.CHECK, m))
            text = "\n".join(errs)
            self.assertIn("harness testvm/compare/gone.sh: matches no file", text)
            self.assertIn("bins ['linux/rustic-bko'], drive.py hands the check", text)
            self.assertIn("harness testvm/vm-lib.sh is family plumbing", text)

    def test_spec_holt_kind_against_ht1_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "testvm", "holttest1d"))
            os.makedirs(os.path.join(tmp, "testvm", "test2", "holt"))
            shutil.copy(os.path.join(REPO, "testvm", "holttest1d", "ht1.sh"), os.path.join(tmp, "testvm", "holttest1d"))
            drv = "#!/usr/bin/env bash\nHERE=$(dirname \"$(realpath \"$0\")\")\nT=$(dirname \"$HERE\")\n. \"$HERE/lib.sh\"\n\"$T/vm-mount\" x\n"
            for p, t in {"testvm/holttest1d/ht1-bsd.sh": drv, "testvm/holttest1d/lib.sh": "#\n", "testvm/vm-mount": "#!/bin/sh\n"}.items():
                with open(os.path.join(tmp, p), "w") as f:
                    f.write(t)
            spec = {"v": 1, "family": "holt", "plumbing": ["testvm/holttest1d/lib.sh"],
                    "kinds": {"bsd": {"harness": ["testvm/holttest1d/ht1-bsd.sh"]},
                              "linux": {"harness": ["testvm/holttest1d/ht1.sh"]}},
                    "entries": {"netbsd11": {"kind": "bsd"}, "debian13": {"kind": "bsd"}}}
            with open(os.path.join(tmp, "testvm/test2/holt/spec.json"), "w") as f:
                json.dump(spec, f)
            old = CM.REPO
            CM.REPO = tmp
            try:
                errs, _ = CM.check_spec("testvm/test2/holt/spec.json", CM.repo_files(tmp))
            finally:
                CM.REPO = old
            text = "\n".join(errs)
            self.assertIn("uses testvm/vm-mount: not in the entry", text)
            self.assertIn("ht1.sh sends debian13 to ht1.sh", text)
            self.assertNotIn("sends netbsd11", text)

    def test_ht1_dispatch(self):
        self.assertEqual(CM.ht1_dispatch("netbsd11"), "ht1-bsd.sh")
        self.assertEqual(CM.ht1_dispatch("redox"), "ht1-offline.sh")
        self.assertEqual(CM.ht1_dispatch("sculpt2610"), "ht1-offline.sh")
        self.assertEqual(CM.ht1_dispatch("debian13"), "ht1.sh")

    def test_command_line(self):
        r = subprocess.run([sys.executable, "-B", os.path.join(T2, "stic", "check-manifests.py"), "-q"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("check-manifests: 0 errors", r.stdout)


# --- U4.2: the BKO_TESTVM lint -------------------------------------------------------------------

class TestLint(unittest.TestCase):
    def test_tree_clean(self):
        self.assertEqual(CM.lint(), [])

    def test_61_files_take_bko_testvm(self):
        n = 0
        base = os.path.join(REPO, "testvm", "stictest1")
        for g in os.listdir(base):
            for fn in (os.listdir(os.path.join(base, g)) if os.path.isdir(os.path.join(base, g)) else []):
                with open(os.path.join(base, g, fn), errors="replace") as f:
                    n += "${BKO_TESTVM:-" in f.read()
        self.assertEqual(n, 61)

    def test_planted_references(self):
        lines = ['. "$WORK/testvm/vm-lib.sh"', "C=$W/testvm/compare", 'source ~/backotter-work/testvm/compare/x.sh',
                 'VMRUN=$HOME/backotter-work/testvm/vm-run', 'x="\'"$W"\'/testvm/vm-lib.sh"', 'y=${WORK}/testvm/z']
        ok = ['. "${BKO_TESTVM:-$WORK/testvm}/vm-lib.sh"', "# a comment: $WORK/testvm/vm-lib.sh",
              'x="\'"${BKO_TESTVM:-$W/testvm}"\'/vm-lib.sh"', '"$WORK/cmp-run.sh" --reserve 2 2']
        with tempfile.TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "testvm", "stictest1", "g")
            os.makedirs(d)
            with open(os.path.join(d, "check.sh"), "w") as f:
                f.write("\n".join(lines + ok) + "\n")
            bad = CM.lint(tmp)
        self.assertEqual([n for _, n, _ in bad], list(range(1, len(lines) + 1)))


# --- U4.3: drive.py's self-test ---------------------------------------------------------------------

class TestDriveSelfTest(unittest.TestCase):
    def test_self_test(self):
        r = subprocess.run([sys.executable, "-B", os.path.join(REPO, "testvm", "stictest1", "drive.py"), "--self-test"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertIn("self-test: all ok", r.stdout)
        for want in ("stop file: start_some starts nothing", "skip file: skipped guests", "row writer: its command line",
                     "launch without --t2-copy: the command SticTest1 always ran"):
            self.assertIn("ok   " + want, r.stdout)

    def test_signatures_kept(self):   # P2 imports these
        import inspect
        self.assertEqual(list(inspect.signature(DRIVE.load_specs).parameters), ["guests"])
        sim = inspect.signature(DRIVE.simulate).parameters
        self.assertEqual(list(sim)[:6], ["guests", "spec", "est", "hosts", "cap", "first"])
        self.assertTrue(all(p.default is not p.empty for p in list(sim.values())[6:]), "later ones optional (ranks)")
        self.assertEqual(list(inspect.signature(DRIVE.readme_estimates).parameters), [])
        self.assertEqual(list(inspect.signature(DRIVE.all_guests).parameters), [])


# --- U4.4: rows_stic over fixture log dirs ---------------------------------------------------------

def fake_facts(guests, set_name, sums=None, host="chopin"):
    oses = ("linux", "freebsd", "netbsd", "openbsd", "dragonfly", "omnios", "haiku")
    sums = sums or {f"{o}/{n}": H64("a") for o in oses for n in ("restic-bko", "rustic-bko")}
    return {"host": host, "versions": {"kernel": "6.12.57+deb13-amd64", "zfs": "2.3.4-1", "qemu": "QEMU emulator version 10.0.2",
                                       "ovmf": "2025.02-8", "pkgs": {"e2fsprogs": "1.47.2-3", "util-linux": "2.41-5"}},
            "sets": {set_name: {"sums": sums, "commits": {"linux/restic-bko": "5" * 40}, "digests": {},
                                "digest_texts": {}, "build_info": "round test"}},
            "guests": {g: {"generation": H64("c"), "files": {"vm-run-options": H64("e")}} for g in guests},
            "plumbing": {"shared:cmp-run.sh": H64("1"), "shared:testvm/compare/iolog.sh": H64("2")},
            "host_inputs": {"~/backotter-work/netbsd-tools": "dir:" + H64("3"), "/etc/nsswitch.conf": "sha256:" + H64("4")}}


class Ctx:
    """HostCtx's part rows_stic uses."""

    def __init__(self, work, tree=None):
        self.host, self.work = "chopin", work
        self.ledger_path = os.path.join(work, "ledger.jsonl")
        self.tree = tree or {"commit": "6fff54b" + "0" * 33, "committed": False,
                             "dirty": {"testvm/stictest1/drive.py": H64("d")}, "copy": "6fff54b.d0123abcd", "path": REPO}

    def tree_info(self, base=None):
        return dict(self.tree)

    def facts(self, want=None):
        raise AssertionError("tests give --facts")


class World:
    """A work dir with one guest's run under MARK, a round's binaries and facts.  The run
    began START (default: a few seconds ago, after the repository's files were last
    written) and its binaries are older: rows_stic flags an input changed after the start."""

    def __init__(self, tmp, guest, runout, code=0, start=None, wall=117, lost=False, end=None):
        self.tmp, self.guest = tmp, guest
        self.start = start = int(time.time()) - 5 if start is None else start
        end = start + wall if end is None else end
        self.work = os.path.join(tmp, "work")
        L = os.path.join(self.work, "stictest1", guest, "logs", MARK)
        os.makedirs(L)
        self.L = L
        for n, v in (("run.out", runout), ("run.out.start", f"{start}\n"), ("run.out.end", None if lost else f"{end}\n"),
                     ("run.out.exit", None if lost or code is None else f"{code}\n")):
            if v is not None:
                with open(os.path.join(L, n), "w") as f:
                    f.write(v)
        self.bin = os.path.join(self.work, "roundtest", "bin")
        sums = {}
        entry = keys.Specs(keys.TreeSource(REPO)).get("stic").entry(guest)
        for name in entry["bins"]:
            p = os.path.join(self.bin, name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(f"fake {name}\n")
            sums[name] = keys.sha256_hex(f"fake {name}\n")
            self.age(p)
        with open(os.path.join(self.bin, "SHA256SUMS"), "w") as f:
            f.write("".join(f"{h}  ./{n}\n" for n, h in sums.items()))
        self.age(os.path.join(self.bin, "SHA256SUMS"))
        self.facts = fake_facts(DRIVE.all_guests(), "roundtest", sums)
        self.facts_path = os.path.join(tmp, "facts.json")
        with open(self.facts_path, "w") as f:
            json.dump(self.facts, f)

    def age(self, path, dt=-600):
        """PATH's mtime set to the run's start + DT seconds (default: well before it)."""
        os.utime(path, (self.start + dt, self.start + dt))

    def guest_file(self, guest, name, text="x\n", dt=-600):
        d = os.path.join(self.work, "vms", guest)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        with open(p, "w") as f:
            f.write(text)
        self.age(p, dt)
        return p

    def key(self):
        src = keys.TreeSource(REPO)
        return keys.measure(f"stic:{self.guest}", src, self.facts, keys.Specs(src), {"set": "roundtest"})["key"]

    def cells(self, copy=REPO, **entry):
        e = {"cell": f"stic:{self.guest}", "rule": "changed:harness", "reasons": ["testvm/stictest1/x 4be1..->9a07.."],
             "planned_key": self.key(), "attempt": 1, "rerun_of": None, "flake": None, "limits_classed": None}
        e.update(entry)
        doc = {"run": MARK, "word": f"SticTest2 test {self.guest}", "copy": {"id": "6fff54b.d0123abcd", "path": copy},
               "cells": {self.guest: e}}
        p = os.path.join(self.tmp, "cells.json")
        with open(p, "w") as f:
            json.dump(doc, f)
        return p

    def build(self, *extra, waits=0, cells=None, ctx=None, attempt=1):
        argv = ["--mark", MARK, "--guest", self.guest, "--bin", self.bin, "--load", "1", "--waits", str(waits),
                "--attempt", str(attempt), "--checks", os.path.join(REPO, "testvm", "stictest1"),
                "--work", self.work, "--facts", self.facts_path, "--now", "2026-11-08T04:00:00Z"]
        if cells:
            argv += ["--run", MARK, "--cells", cells]
        return rows_stic.build(argv + list(extra), ctx or Ctx(self.work))


def checked(row):
    """The row as t2-host would append it (P1's complete and validate)."""
    from t2lib import ledger
    c = ledger.complete(row, host="chopin")
    e = ledger.validate(c)
    assert not e, e
    return c


class TestRowsStic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2-p4-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pass(self):
        w = World(self.tmp, "debian12", fixture("debian12-pass.out"))
        r = checked(w.build(waits=17, cells=w.cells()))
        self.assertEqual((r["verdict"], r["class"], r["kind"], r["rule"]), ("PASS", "pass", "run", "changed:harness"))
        self.assertTrue(r["valid"], r.get("note"))
        self.assertEqual(r["key"], r["planned_key"])
        self.assertEqual(r["counts"], {"PASS": 16, "FAIL": 0, "LIMIT": 3})
        self.assertEqual(r["needs"]["SELinux labels"], "LIMIT")
        self.assertEqual(r["origin"], f"stic:{MARK}:debian12:1")
        self.assertEqual((r["wall_s"], r["waits_s"]), (117, 17))
        self.assertAlmostEqual(r["gm"], round((117 - 17) / 60, 3))
        self.assertEqual(r["wear"], {f"stic:{MARK}:debian12": None})
        self.assertEqual(r["copy"], "6fff54b.d0123abcd")
        self.assertFalse(r["committed"])
        self.assertIn("testvm/stictest1/debian12/inner.sh", r["inputs"]["harness"])
        self.assertTrue(r["logs"].startswith("chopin:"))
        self.assertEqual(r["spec"]["path"], "testvm/test2/stic/manifests.json#debian12")

    def test_sculpt_results_layout(self):
        w = World(self.tmp, "sculpt2610", fixture("sculpt2610-pass.out"), wall=50)
        r = checked(w.build(cells=w.cells()))
        self.assertEqual(r["verdict"], "PASS")
        self.assertEqual(r["counts"], {"PASS": 5, "FAIL": 0, "LIMIT": 5})
        self.assertIn("kernel", r["inputs"]["host"])   # host-side: host versions are hard inputs

    def test_fail(self):
        w = World(self.tmp, "debian12", fixture("debian12-fail.out"), code=1)
        r = checked(w.build(cells=w.cells()))
        self.assertEqual((r["verdict"], r["class"]), ("FAIL", "fail"))
        self.assertEqual(len(r["fails"]), 1)
        self.assertTrue(r["fails"][0].startswith("FAIL Round trip restic-rustic"))
        self.assertTrue(all(len(x) <= 200 for x in r["fails"]))

    def test_harness(self):
        w = World(self.tmp, "debian12", fixture("debian12-boot.out"), code=1)
        r = checked(w.build(cells=w.cells()))
        self.assertEqual((r["verdict"], r["class"]), ("HARNESS", "harness"))

    def test_lost(self):
        w = World(self.tmp, "debian12", "PASS a: b\n", lost=True)
        with self.assertRaises(SystemExit):   # no exit file: still running, unless drive.py says lost
            w.build(cells=w.cells())
        r = checked(w.build("--lost", cells=w.cells()))
        self.assertEqual((r["verdict"], r["class"]), ("LOST", "harness"))

    def test_limit_new(self):
        w = World(self.tmp, "debian12", fixture("debian12-limitnew.out"))
        r = checked(w.build(cells=w.cells()))
        self.assertEqual(r["verdict"], "FAIL")
        self.assertEqual(r["needs"]["Sparse files"], "LIMIT-NEW")
        self.assertEqual(r["needs"]["Birth times"], "LIMIT")
        # classed in cells.json: no longer new
        r = checked(w.build(cells=w.cells(limits_classed=["SELinux labels", "Birth times", "Sparse files",
                                                          "Project IDs, compression, nocow, casefold"])))
        self.assertEqual(r["verdict"], "PASS")

    def test_flake(self):
        w = World(self.tmp, "ghostbsd26", fixture("ghostbsd26-flake.out"), code=1)
        r = checked(w.build(cells=w.cells()))
        self.assertEqual(r["verdict"], "FAIL")
        self.assertEqual(r["extra"]["flake"]["guest"], "ghostbsd26")
        fl = load_json(os.path.join(T2, "stic", "flakes.json"))[2]
        shutil.rmtree(os.path.dirname(w.L))
        os.makedirs(os.path.join(self.tmp, "b"))
        w2 = World(os.path.join(self.tmp, "b"), "ghostbsd26", "PASS roundtrip-rustic->restic: ok\n")
        r2 = checked(w2.build(attempt=2, cells=w2.cells(attempt=2, flake=fl, rule="rerun", rerun_of=r["id"])))
        self.assertEqual((r2["verdict"], r2["class"], r2["attempt"]), ("PASS", "known-flake", 2))
        self.assertEqual(r2["origin"], f"stic:{MARK}:ghostbsd26:2")
        r3 = checked(w2.build(attempt=2, cells=w2.cells(attempt=2, rule="rerun")))
        self.assertEqual((r3["verdict"], r3["class"]), ("FLAKY-FAIL", "fail"))

    def test_ssh255_flake_is_ghostbsd26_zfs_only(self):
        t = fixture("ghostbsd26-zfs-ssh255.out")
        fails = [f"FAIL {n}: {d}" for v, n, d in rows_stic.parse_lines(t) if v == "FAIL"]
        fl = load_json(os.path.join(T2, "stic", "flakes.json"))
        self.assertEqual(rows_stic.known_flake(fl, "ghostbsd26-zfs", fails, t)["guest"], "ghostbsd26-zfs")
        self.assertIsNone(rows_stic.known_flake(fl, "freebsd15", fails, t))
        self.assertIsNone(rows_stic.known_flake(fl, "ghostbsd26-zfs", fails[:3] + ["FAIL Sparse files: holes"], t))

    def test_a_need_the_check_stopped_reporting_fails(self):
        v = rows_stic.judge([("PASS", "a", "")], 0, limits_classed=[], prev_needs=["a", "b"])
        self.assertEqual((v[0], v[2]["b"]), ("FAIL", "FAIL"))
        self.assertIn("FAIL b: the check printed no verdict", "\n".join(v[4]))
        self.assertEqual(rows_stic.judge([("PASS", "a", "")], 0, limits_classed=[])[0], "PASS")
        self.assertEqual(rows_stic.judge([("PASS", "a", "")], 0, limits_classed=[], prev_needs=["a"])[0], "PASS")

    def test_prev_needs_only_while_its_check_is_the_same(self):
        now = argparse.Namespace(groups={"harness": "h", "judge": "j"})
        pc = argparse.Namespace(cell="stic:x", res={"chopin": now})
        base = {"cell": "stic:x", "kind": "run", "verdict": "PASS", "valid": True}
        led = [dict(base, t="1", needs={"old": "PASS"}, groups={"harness": "h", "judge": "j"}),
               dict(base, t="2", needs={"b": "PASS", "a": "LIMIT"}, groups={"harness": "h", "judge": "j"}),
               dict(base, t="3", needs={"bad": "PASS"}, valid=False, groups={"harness": "h", "judge": "j"})]
        me = argparse.Namespace(ctx=argparse.Namespace(ledger=led))
        self.assertEqual(stic.SticRun.prev_needs(me, pc, "chopin"), ["b"])    # a LIMIT may stop applying
        led[1]["groups"] = {"harness": "edited", "judge": "j"}          # an edit of the check: its needs may change
        self.assertIsNone(stic.SticRun.prev_needs(me, pc, "chopin"))

    def test_waits_and_load(self):
        self.assertEqual(rows_stic.gm_of(3, 1000, 1600, 120), round(480 / 60, 3))    # unweighted: load isn't counted
        self.assertEqual(rows_stic.gm_of(1, 1000, None, 0), 0.0)
        self.assertEqual(rows_stic.gm_of(1, 1000, 1010, 60), 0.0)

    def test_planned_key_and_binaries(self):
        w = World(self.tmp, "debian12", fixture("debian12-pass.out"))
        r = checked(w.build(cells=w.cells(planned_key=H64("f"))))
        self.assertFalse(r["valid"])
        self.assertIn("isn't the planned", r["note"])
        with open(os.path.join(w.bin, "linux", "rustic-bko"), "a") as f:
            f.write("rebuilt meanwhile\n")
        r = checked(w.build(cells=w.cells()))
        self.assertFalse(r["valid"])
        self.assertIn("binaries differ from SHA256SUMS: linux/rustic-bko", r["note"])

    def test_test1_row(self):
        w = World(self.tmp, "debian12", fixture("debian12-pass.out"))
        # the synced tree: copies (keys' tree walk lists a linked directory as a link), with
        # their times (copy2): older than the run's start
        os.makedirs(os.path.join(w.work, "testvm"))
        skip = shutil.ignore_patterns("__pycache__", "*.pyc", "logs")
        for d in ("stictest1", "test2", "compare"):
            shutil.copytree(os.path.join(REPO, "testvm", d), os.path.join(w.work, "testvm", d), symlinks=True, ignore=skip)
        for f in ("vm-run", "vm-lib.sh", "console-boot.py", "hosts.sh", "hosts.json"):
            shutil.copy2(os.path.join(REPO, "testvm", f), os.path.join(w.work, "testvm", f))
        ctx = Ctx(w.work, tree={"commit": "6ad28d7", "committed": True, "dirty": {}, "copy": "synced"})
        argv = ["--mark", MARK, "--guest", "debian12", "--bin", w.bin, "--checks", os.path.join(w.work, "testvm", "stictest1"),
                "--work", w.work, "--facts", w.facts_path]
        r = checked(rows_stic.build(argv, ctx))
        self.assertEqual((r["rule"], r["planned_key"], r["copy"], r["commit"], r["committed"]),
                         ("test1", None, "synced", "6ad28d7", True))
        self.assertEqual(r["word"], f"SticTest1 drive.py {MARK}")
        self.assertTrue(r["valid"])
        # R28: checks from st1-wt at another commit: never counts
        wt = os.path.join(w.work, "st1-wt", "testvm")
        os.makedirs(wt)
        shutil.copytree(os.path.join(REPO, "testvm", "stictest1"), os.path.join(wt, "stictest1"))
        with open(os.path.join(w.work, "st1-wt", "COMMIT"), "w") as f:
            f.write("4649d3b" + "0" * 33 + "\n")
        argv[argv.index("--checks") + 1] = os.path.join(wt, "stictest1")
        r = checked(rows_stic.build(argv, ctx))
        self.assertEqual((r["copy"], r["committed"], r["dirty"]), ("st1-wt", False, None))

    def test_inputs_changed_after_the_start(self):
        """The inputs are measured when the row is written: a guest updated, or a round rebuilt,
        after its check began makes the row invalid (review: rows keyed at write time)."""
        w = World(self.tmp, "debian12", fixture("debian12-pass.out"))
        w.guest_file("debian12", "image-state", '{"generation": "cc"}\n')            # before the start
        r = checked(w.build(cells=w.cells()))
        self.assertTrue(r["valid"], r.get("note"))
        self.assertNotIn("changed", r["extra"])
        w.guest_file("debian12", "image-state", '{"generation": "cc"}\n', dt=+40)     # vm-update during the run
        r = checked(w.build(cells=w.cells()))
        self.assertFalse(r["valid"])
        self.assertEqual(r["extra"]["changed"], ["debian12:image-state"])
        self.assertIn("inputs changed after the check began (1: debian12:image-state)", r["note"])
        w.guest_file("debian12", "image-state", '{"generation": "cc"}\n')
        w.age(os.path.join(w.bin, "linux", "rustic-bko"), dt=+3600)                  # the round rebuilt after it
        r = checked(w.build(cells=w.cells()))
        self.assertFalse(r["valid"])
        self.assertEqual(r["extra"]["changed"], ["linux/rustic-bko"])
        w.age(os.path.join(w.bin, "linux", "rustic-bko"))
        r = checked(w.build(cells=w.cells()))
        self.assertTrue(r["valid"], r.get("note"))

    def test_adopted_old_run(self):
        """drive.py run again on an old mark adopts checks that ended days ago and writes their
        rows now: such a row counts only if nothing the check ran with changed since its start."""
        old = int(time.time()) - 3 * 86400
        w = World(self.tmp, "debian12", fixture("debian12-pass.out"), start=old)
        copy = os.path.join(self.tmp, "copy")   # the copy's files, written before the run, as rsync -rlp leaves them
        src = keys.TreeSource(REPO)
        m = keys.measure("stic:debian12", src, w.facts, keys.Specs(src), {"set": "roundtest"})
        paths = {rows_stic.SPEC, rows_stic.LIMITS, rows_stic.FLAKES}
        for g in ("harness", "judge", "plumbing"):
            paths |= {n.split("#", 1)[0] for n in m["inputs"].get(g) or {} if not n.startswith(("shared:", "host:"))}
        for p in paths:
            os.makedirs(os.path.dirname(os.path.join(copy, p)), exist_ok=True)
            shutil.copyfile(os.path.join(REPO, p), os.path.join(copy, p))
            os.utime(os.path.join(copy, p), (old - 7200, old - 7200))
        w.guest_file("debian12", "vm-run-options", "--q35\n", dt=-3600)
        r = checked(w.build(cells=w.cells(copy=copy)))
        self.assertTrue(r["valid"], r.get("note"))
        self.assertEqual(r["key"], r["planned_key"])
        w.guest_file("debian12", "vm-run-options", "--q35\n", dt=86400)   # the same content, a day after the start
        r = checked(w.build(cells=w.cells(copy=copy)))
        self.assertFalse(r["valid"])
        self.assertEqual(r["extra"]["changed"], ["debian12:vm-run-options"])
        w.guest_file("debian12", "vm-run-options", "--q35\n", dt=-3600)
        os.utime(os.path.join(copy, "testvm/stictest1/debian12/check.sh"), (old + 60, old + 60))
        r = checked(w.build(cells=w.cells(copy=copy)))
        self.assertEqual((r["valid"], r["extra"].get("changed")), (False, ["testvm/stictest1/debian12/check.sh"]))

    def test_t2_row_without_a_planned_key_or_cells(self):
        w = World(self.tmp, "debian12", fixture("debian12-pass.out"))
        r = checked(w.build(cells=w.cells(planned_key=None)))
        self.assertFalse(r["valid"])
        self.assertIn("no planned key in cells.json: never counts", r["note"])
        torn = os.path.join(self.tmp, "cells-torn.json")
        with open(torn, "w") as f:
            f.write('{"run": "x", "cel')
        r = checked(w.build(cells=torn))
        self.assertEqual((r["rule"], r["word"], r["run"], r["valid"], r["planned_key"]),
                         ("unplanned", f"SticTest2 run {MARK}", MARK, False, None))
        self.assertIn("no readable cells file", r["note"])

    def test_results_json_layouts(self):
        self.assertEqual(rows_stic.results_json_lines({"results": [{"verdict": "PASS", "need": "a", "detail": "x"}]}),
                         [("PASS", "a", "x")])
        self.assertEqual(rows_stic.results_json_lines({"needs": [{"need": "b", "result": "LIMIT", "details": ["y", "z"]}]}),
                         [("LIMIT", "b", "y; z")])
        self.assertEqual(rows_stic.results_json_lines({"c": {"verdict": "FAIL", "detail": "w"}}), [("FAIL", "c", "w")])
        self.assertEqual(rows_stic.results_json_lines({"items": [{"status": "PASS", "need": "d", "detail": ""}]}),
                         [("PASS", "d", "")])


# --- U4.5: stic.run with fake drive events ------------------------------------------------------

class PC:
    def __init__(self, cell, status, rule=None, reasons=(), key=None, klass=None, rep=None, via=None, inputs=None):
        self.cell, self.status, self.rule, self.reasons = cell, status, rule, list(reasons)
        self.keys = {"chopin": key or H64("9")}
        self.klass, self.rep, self.via = klass, rep, via
        self.res = {"chopin": argparse.Namespace(inputs=inputs or {"bin": {"x/restic-bko": "sha256:" + H64("a")}},
                                                 spec={"path": "testvm/test2/stic/manifests.json#x", "sha256": H64("b")})}

    @property
    def key(self):
        return self.keys["chopin"]


class Plan:
    def __init__(self, cells, classes=(), form="bare"):
        self.cells, self.classes = cells, list(classes)
        self.set = {"name": "roundrel4", "round": "rel4", "dir": "~/backotter-work/roundrel4/bin", "pending": None}
        self.word = argparse.Namespace(text="SticTest2 rel4", form=form, family="stic")

    def cell(self, name):
        return next((c for c in self.cells if c.cell == name), None)

    def due(self):
        return [c for c in self.cells if c.status == "due"]

    def by_status(self, s):
        return [c for c in self.cells if c.status == s]


class RunCtx:
    def __init__(self, mac_dir):
        self.checkout = REPO
        self.hosts = ["chopin"]
        self.facts = {"chopin": {}}
        self.ledger = []
        with open(os.path.join(REPO, "testvm", "test2", "classes.json")) as f:
            self.classes = json.load(f)
        self.drive = DRIVE
        self.options = {"run": {"id": MARK, "word": "SticTest2 rel4", "mac_dir": mac_dir, "hosts": ["chopin"],
                                "host_root": "/home/m/backotter-work/t2core-p4/state",
                                "copy": {"id": "6fff54b", "path": "/home/m/backotter-work/t2core-p4/copy/6fff54b",
                                         "commit": "6fff54b" + "0" * 33, "dirty": {}}}}


class Proc:
    """drive.py, scripted: rows land at their poll numbers; it ends (with RC) after the last."""

    def __init__(self, env, mark, rc=0):
        self.env, self.mark, self.n, self.rc, self.pid = env, mark, 0, rc, 4242

    def poll(self):
        self.n += 1
        if self.mark in self.env.fail_at and self.n >= self.env.fail_at[self.mark]:
            raise OSError("ssh: the Mac lost the network")
        script = self.env.script.get(self.mark, [])
        while script and script[0][0] <= self.n:
            _, row = script.pop(0)
            self.env.landed.append(row)
        return self.rc if not script else None


class FakeGate:
    """t2lib.gate's part stic.run uses: DUE gate cells in its plan, run_layers OK (or not);
    BROKEN: family_layers raises (a layers.json that doesn't parse)."""

    def __init__(self, due=(), ok=True, broken=None):
        self.due_cells, self.ok, self.broken, self.ran = list(due), ok, broken, []

    def family_layers(self, family):
        if self.broken:
            raise ValueError(self.broken)
        return {"fast": ["fork:restic", "fork-root:restic", "bin:abi"], "slow": ["loop:linux"]}

    def gate_plan(self, ctx):
        return argparse.Namespace(due=lambda: [argparse.Namespace(cell=c) for c in self.due_cells])

    def run_layers(self, ctx, names, part=None, plan=None):
        self.ran.append((part, list(names)))
        return {"ok": self.ok, "exit": 0 if self.ok else 1}


class FakeEnv:
    """Hosts, ledger and drive.py, scripted: script[mark] = [(poll number, row)]; rc[mark]:
    drive.py's exit; started[mark]: the guests its state.json names; busy: driver_busy's
    answers; loads: host_load's; fail_at[mark]: the poll an error comes at."""

    def __init__(self, script, gate=None):
        self.script, self.landed, self.files, self.mac, self.argvs = script, [], {}, [], []
        self.gate_mod = FakeGate() if gate is None else gate
        self.logs, self.rc, self.started, self.busy, self.loads = [], {}, {}, [], []
        self.fail_at, self.stopped, self.imgsums, self.outs = {}, [], [], {}

    def now(self):
        return time.time()

    def sleep(self, s):
        pass

    def log(self, m):
        self.logs.append(m)

    def put(self, host, path, text):
        self.files[(host, path)] = json.loads(text)

    def rows(self, hosts):
        return list(self.landed)

    def append_mac(self, rows):
        out = []
        for r in rows:
            dup = [m for m in self.mac if m["origin"] == r["origin"]]
            out.append((dup[0]["id"] if dup else f"mac-20261108T040000Z-{len(self.mac):08x}", not dup))
            if not dup:
                self.mac.append(dict(r, id=out[-1][0]))
        return out

    def spawn(self, argv, log, env):
        self.argvs.append(argv)
        mark = argv[argv.index("--logs-mark") + 1]
        self.outs[argv[argv.index("--out") + 1]] = mark
        return Proc(self, mark, self.rc.get(mark, 0))

    def stop_child(self, p, log=None):
        self.stopped.append(p.mark)

    def driver_busy(self, out):
        return self.busy.pop(0) if self.busy else False

    def drive_state(self, out):
        mark = self.outs.get(out)
        return {"jobs": {g: {} for g in self.started.get(mark, [])}} if mark in self.started else None

    def tail(self, path, n=3):
        return ["drive.py: cannot read cinqcent (m@cinqcent.local)"]

    def host_load(self, host, run, lo):
        return self.loads.pop(0) if self.loads else None

    def imgsum(self, host, tool, host_root, paths):
        self.imgsums.append((host, tool, host_root, list(paths)))
        return True, ""

    def gate(self):
        return self.gate_mod

    def replan(self, word, ctx):
        raise AssertionError("no replan in these tests")


def row(guest, verdict, mark=MARK, attempt=1, needs=None, cls=None, n=0, extra=None):
    return {"id": f"chopin-20261108T03{n:02d}00Z-{n:08x}", "t": f"2026-11-08T03:{n:02d}:00Z", "run": MARK,
            "origin": f"stic:{mark}:{guest}:{attempt}", "cell": f"stic:{guest}", "verdict": verdict,
            "class": cls or {"PASS": "pass", "FAIL": "fail", "HARNESS": "harness", "LOST": "harness",
                             "FLAKY-FAIL": "fail"}[verdict],
            "needs": needs or {"a": "PASS"}, "valid": True, "host": "chopin", "gm": 1.0, "wall_s": 60,
            "planned_key": H64("9"), "fails": [], "extra": extra or {}, "rule": "x", "reasons": []}


class TestSticRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2-p4-run-")
        self.ctx = RunCtx(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def b2_plan(self):
        return Plan([PC("stic:freebsd15", "due", "class:B2", ["stalest of B2"], klass="B2"),
                     PC("stic:freebsd15-geli", "class-pending", "class:B2", klass="B2", rep="stic:freebsd15"),
                     PC("stic:ghostbsd26-zfs", "class-pending", "class:B2", klass="B2", rep="stic:freebsd15"),
                     PC("stic:debian12", "current")],
                    classes=[{"class": "B2", "rep": "stic:freebsd15",
                              "members": ["stic:freebsd15", "stic:freebsd15-geli", "stic:ghostbsd26-zfs"]}])

    def test_representative_pass_writes_class_rows(self):
        env = FakeEnv({MARK: [(1, row("freebsd15", "PASS", needs={"x": "PASS", "y": "LIMIT"}, n=1))]})
        res = stic.SticRun(self.ctx, self.b2_plan(), env).go()
        cells = env.files[("chopin", "/home/m/backotter-work/t2core-p4/state/runs/" + MARK + "/cells.json")]
        self.assertEqual(sorted(cells["cells"]), ["freebsd15"])
        e = cells["cells"]["freebsd15"]
        self.assertEqual((e["rule"], e["attempt"], e["planned_key"]), ("class:B2", 1, H64("9")))
        self.assertIn("Linux-only metadata (SELinux, capabilities, inode flags, POSIX ACL default on ZFS)", e["limits_classed"])
        self.assertEqual(len(env.argvs), 1)   # no rerun round
        a = env.argvs[0]
        for flag, v in (("--t2-run", MARK), ("--logs-mark", MARK), ("--t2-copy", "/home/m/backotter-work/t2core-p4/copy/6fff54b"),
                        ("--bin", "~/backotter-work/roundrel4/bin")):
            self.assertEqual(a[a.index(flag) + 1], v)
        self.assertEqual(a[-2:], ["rel4", "freebsd15"])
        cls = [m for m in env.mac if m["kind"] == "class"]
        self.assertEqual(sorted(m["cell"] for m in cls), ["stic:freebsd15-geli", "stic:ghostbsd26-zfs"])
        for m in cls:
            self.assertEqual((m["via"], m["rule"], m["verdict"], m["origin"].split(":")[0]),
                             ("chopin-20261108T030100Z-00000001", "class:B2", "PASS", "derived"))
            self.assertEqual(m["needs"], {"x": "PASS", "y": "LIMIT"})
        self.assertEqual(res["exit"], 0)
        out = {c["cell"]: c["outcome"] for c in res["cells"]}
        self.assertEqual(out, {"stic:freebsd15": "ran", "stic:freebsd15-geli": "class", "stic:ghostbsd26-zfs": "class",
                               "stic:debian12": "skipped"})

    def test_representative_fail_reruns_and_fans_out(self):
        env = FakeEnv({MARK: [(1, row("freebsd15", "FAIL", needs={"x": "FAIL"}, n=1))],
                       MARK + "-r2": [(1, row("freebsd15", "PASS", mark=MARK + "-r2", attempt=2, cls="fail", n=2)),
                                      (1, row("freebsd15-geli", "PASS", mark=MARK + "-r2", n=3)),
                                      (2, row("ghostbsd26-zfs", "PASS", mark=MARK + "-r2", n=4))]})
        env.script[MARK + "-r2"][0][1]["verdict"] = "FLAKY-FAIL"
        res = stic.SticRun(self.ctx, self.b2_plan(), env).go()
        r2 = env.files[("chopin", "/home/m/backotter-work/t2core-p4/state/runs/" + MARK + "/cells-r2.json")]["cells"]
        self.assertEqual((r2["freebsd15"]["attempt"], r2["freebsd15"]["rule"], r2["freebsd15"]["rerun_of"]),
                         (2, "rerun", "chopin-20261108T030100Z-00000001"))
        self.assertEqual((r2["freebsd15-geli"]["attempt"], r2["freebsd15-geli"]["rule"]), (1, "fanout:B2"))
        self.assertEqual(r2["ghostbsd26-zfs"]["rule"], "fanout:B2")
        a2 = env.argvs[1]
        self.assertEqual((a2[a2.index("--logs-mark") + 1], a2[a2.index("--t2-attempt") + 1]), (MARK + "-r2", "2"))
        self.assertTrue(a2[a2.index("--t2-cells") + 1].endswith("/cells-r2.json"))
        self.assertEqual([m for m in env.mac if m["kind"] == "class"], [])   # they ran themselves
        self.assertEqual(res["exit"], 1)   # FLAKY-FAIL fails the run
        verdicts = {c["cell"]: c["verdict"] for c in res["cells"]}
        self.assertEqual(verdicts["stic:freebsd15"], "FLAKY-FAIL")

    def test_known_flake_representative_fans_out_beside_its_rerun(self):
        """A representative's attempt-1 FAIL fans its class out in the r2 round even when it is a
        known flake: the run has one more round only, so its rerun can't decide for the others."""
        fl = {"guest": "freebsd15", "pattern": "x", "why": "known"}
        env = FakeEnv({MARK: [(1, row("freebsd15", "FAIL", n=1, extra={"flake": fl}))],
                       MARK + "-r2": [(1, row("freebsd15", "PASS", mark=MARK + "-r2", attempt=2, cls="known-flake", n=2)),
                                      (1, row("freebsd15-geli", "PASS", mark=MARK + "-r2", n=3)),
                                      (1, row("ghostbsd26-zfs", "PASS", mark=MARK + "-r2", n=4))]})
        res = stic.SticRun(self.ctx, self.b2_plan(), env).go()
        r2 = env.files[("chopin", "/home/m/backotter-work/t2core-p4/state/runs/" + MARK + "/cells-r2.json")]["cells"]
        self.assertEqual(sorted(r2), ["freebsd15", "freebsd15-geli", "ghostbsd26-zfs"])
        self.assertEqual((r2["freebsd15"]["flake"], r2["freebsd15"]["attempt"]), (fl, 2))
        self.assertEqual((r2["freebsd15-geli"]["rule"], r2["freebsd15-geli"]["attempt"]), ("fanout:B2", 1))
        self.assertIn("a known flake", r2["freebsd15-geli"]["reasons"][0])
        self.assertEqual([m for m in env.mac if m["kind"] == "class"], [])   # they ran themselves
        self.assertEqual(res["exit"], 0)

    def test_known_flake_representative_failing_again_covers_nothing(self):
        """Review repro: attempt 1 a known flake, the rerun FAILs too: its members ran in the r2
        round, so none is left class-pending without a row."""
        fl = {"guest": "freebsd15", "pattern": "x", "why": "known"}
        env = FakeEnv({MARK: [(1, row("freebsd15", "FAIL", n=1, needs={"x": "FAIL"}, extra={"flake": fl}))],
                       MARK + "-r2": [(1, row("freebsd15", "FAIL", mark=MARK + "-r2", attempt=2, n=2, needs={"x": "FAIL"})),
                                      (1, row("freebsd15-geli", "PASS", mark=MARK + "-r2", n=3)),
                                      (1, row("ghostbsd26-zfs", "FAIL", mark=MARK + "-r2", n=4, needs={"y": "FAIL"}))]})
        res = stic.SticRun(self.ctx, self.b2_plan(), env).go()
        out = {c["cell"]: (c["outcome"], c["verdict"]) for c in res["cells"]}
        self.assertEqual(out["stic:freebsd15-geli"], ("ran", "PASS"))
        self.assertEqual(out["stic:ghostbsd26-zfs"], ("ran", "FAIL"))
        self.assertEqual([m for m in env.mac if m["kind"] == "class"], [])
        self.assertEqual(res["exit"], 1)

    def test_stop_rules(self):
        lin = ["debian12", "debian13", "mint22", "pop24"]
        plan = Plan([PC(f"stic:{g}", "due", "changed:bin") for g in lin])
        env = FakeEnv({MARK: [(1, row("debian12", "FAIL", needs={"xattrs": "FAIL"}, n=1)),
                              (2, row("debian13", "FAIL", needs={"xattrs": "FAIL"}, n=2))],
                       MARK + "-r2": []})
        res = stic.SticRun(self.ctx, plan, env).go()
        with open(os.path.join(self.tmp, "skip")) as f:
            skipped = set(f.read().split())
        self.assertIn("mint22", skipped)
        self.assertTrue(all(o == "linux" for g, o in self.ctx.drive.load_specs(sorted(skipped)).items()
                            for o in [o.os]))
        self.assertTrue(res["stops"][0].startswith("stop-os linux: after stic:debian13 FAIL"), res["stops"])
        self.assertEqual(res["exit"], 1)

    def test_two_harness_results_stop_the_run(self):
        plan = Plan([PC(f"stic:{g}", "due", "new") for g in ("debian12", "zorin18", "mint22")])
        env = FakeEnv({MARK: [(1, row("debian12", "HARNESS", n=1)), (1, row("zorin18", "LOST", n=2))]})
        res = stic.SticRun(self.ctx, plan, env).go()
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "stop")))
        self.assertEqual(len(env.argvs), 1)   # stopped: no rerun round
        self.assertTrue(res["stops"][0].startswith("stop-run"), res["stops"])

    def test_carried_rows_on_a_bare_word(self):
        plan = Plan([PC("stic:dragonfly6", "carried", "carry:affected-v1", ["affected.sh says linux freebsd"],
                        via="chopin-20261107T010000Z-0000abcd")])
        env = FakeEnv({})
        stic.SticRun(self.ctx, plan, env).go()
        self.assertEqual([(m["kind"], m["cell"], m["via"], m["rule"]) for m in env.mac],
                         [("carried", "stic:dragonfly6", "chopin-20261107T010000Z-0000abcd", "carry:affected-v1")])
        env2 = FakeEnv({})
        plan.word.form = "names"
        stic.SticRun(self.ctx, plan, env2).go()
        self.assertEqual(env2.mac, [])   # only bare runs write carried rows

    def test_derived_rows_are_valid_rows(self):
        from t2lib import ledger
        env = FakeEnv({MARK: [(1, row("freebsd15", "PASS", n=1))]})
        stic.SticRun(self.ctx, self.b2_plan(), env).go()
        for m in env.mac:
            c = ledger.complete({k: v for k, v in m.items() if k != "id"}, host="mac")
            self.assertEqual(ledger.validate(c), [], m["origin"])

    def test_a_pending_round_needs_a_copy_on_both_build_hosts(self):
        """--hosts chopin: the round is still built on both build hosts (tests/test_round_dist.py
        has the rest); without a copy on cinqcent nothing is built."""
        old = Plan([PC("stic:debian12", "due", "changed:bin", key=H64("1"))])
        old.set = {"name": "roundt2r-x", "round": "t2r-x", "dir": "~/backotter-work/roundt2r-x/bin", "pending": "restic=x"}
        env = FakeEnv({MARK: []})
        env.run = lambda *a, **k: self.fail("build-round.sh started")
        with self.assertRaisesRegex(stic.SticError, "no private copy on cinqcent to build the round from"):
            stic.SticRun(self.ctx, old, env).go()

    def test_pending_round_is_built_then_rekeyed(self):
        """R1 5a: the default round is built, the plan made again from the real binaries; cells.json
        carries the new keys, a cell that turned due is added, one that turned current dropped."""
        old = Plan([PC("stic:debian12", "due", "changed:bin", key=H64("1")),
                    PC("stic:zorin18", "due", "changed:bin", key=H64("2")),
                    PC("stic:mint22", "due", "fresh:names", key=H64("3")),
                    PC("stic:pop24", "current")])
        old.set = {"name": "roundt2r-5253b20-96596bb-6ccec58", "round": "t2r-5253b20-96596bb-6ccec58",
                   "dir": "~/backotter-work/roundt2r-5253b20-96596bb-6ccec58/bin", "pending": "restic=5253b20..."}
        new = Plan([PC("stic:debian12", "due", "changed:bin", key=H64("4")),
                    PC("stic:zorin18", "current", key=H64("5")),
                    PC("stic:mint22", "current", key=H64("6")),
                    PC("stic:pop24", "due", "changed:bin", key=H64("7"))])
        new.set = dict(old.set, pending=None)
        env = FakeEnv({MARK: [(1, row(g, "PASS", n=i)) for i, g in enumerate(("debian12", "mint22", "pop24"), 1)]})
        self.ctx.options["run"]["hosts"] = ["chopin", "cinqcent"]
        built = []
        env.run = lambda argv, env=None, timeout=None: (built.append((argv, env)), argparse.Namespace(returncode=0))[1]
        env.refresh_facts = lambda ctx, sets: {"chopin": {"sets": {s: {} for s in sets}}}
        env.replan = lambda word, ctx: new
        res = stic.SticRun(self.ctx, old, env).go()
        argv, benv = built[0]
        self.assertTrue(argv[1].endswith("stick/build-round.sh") and argv[2] == "t2r-5253b20-96596bb-6ccec58")
        self.assertEqual((benv["BKO_PROFILE"], benv["BKO_GUEST_PROFILE"], benv["BKO_REMOTE_DIR"]),
                         ("release", "release", "/home/m/backotter-work/t2core-p4/copy/6fff54b"))
        cells = env.files[("chopin", "/home/m/backotter-work/t2core-p4/state/runs/" + MARK + "/cells.json")]["cells"]
        self.assertEqual(sorted(cells), ["debian12", "mint22", "pop24"])   # zorin18 current now; mint22 named fresh
        self.assertEqual(cells["debian12"]["planned_key"], H64("4"))       # the built binaries' key
        self.assertEqual(cells["mint22"]["rule"], "fresh:names")
        self.assertEqual(res["notes"][-1], "rekey: {'added': ['stic:pop24'], 'dropped': ['stic:zorin18']}")

    def test_under_run_py(self):
        """Under run.py's lifecycle: its RunState writes cells.json and cells-r2.json, its
        StopEngine answers, and run.py (not stic) writes the derived rows."""
        from t2lib import run as runmod

        class RS:
            def __init__(rs, ctx, plan):
                rs.run, rs.word, rs.plan, rs.ctx = MARK, argparse.Namespace(text="SticTest2 rel4", family="stic"), plan, ctx
                rs.mac_dir, rs.hosts, rs.flakes = self.tmp, ["chopin"], []
                rs.copy = {"chopin": {"id": "6fff54b", "path": "/home/m/c/6fff54b", "commit": "6fff54b" + "0" * 33,
                                      "dirty": {}}}
                rs.stops = runmod.StopEngine("stic", ctx.classes.get("policy"))
                rs.written = []

            def host_run_dir(rs, h):
                return "/home/m/s/runs/" + MARK

            def write_cells(rs, plan, attempt=1, name=None, reruns=None):
                rs.written.append((attempt, name, dict(reruns) if reruns is not None else None,
                                   sorted(c.cell for c in plan.cells if c.status == "due")))
                return {"chopin": "/home/m/s/runs/" + MARK + "/" + name}

            def log(rs, text):
                pass

        plan = self.b2_plan()
        ctx = RunCtx(self.tmp)
        ctx.options = {}
        ctx.run = RS(ctx, plan)
        env = FakeEnv({MARK: [(1, row("freebsd15", "FAIL", needs={"x": "FAIL"}, n=1))],
                       MARK + "-r2": [(1, row("freebsd15", "PASS", mark=MARK + "-r2", attempt=2, n=2)),
                                      (1, row("freebsd15-geli", "PASS", mark=MARK + "-r2", n=3)),
                                      (1, row("ghostbsd26-zfs", "PASS", mark=MARK + "-r2", n=4))]})
        res = stic.run(ctx, plan, env)
        w = ctx.run.written
        self.assertEqual((w[0][0], w[0][1], w[0][2], w[0][3]), (1, "cells.json", None, ["stic:freebsd15"]))
        self.assertEqual((w[1][0], w[1][1]), (2, "cells-r2.json"))
        self.assertEqual(w[1][2]["stic:freebsd15"]["rerun_of"], "chopin-20261108T030100Z-00000001")
        self.assertEqual(w[1][2]["stic:freebsd15-geli"]["rule"], "fanout:B2")
        self.assertEqual(w[1][3], ["stic:freebsd15", "stic:freebsd15-geli", "stic:ghostbsd26-zfs"])   # fanout_due
        a = env.argvs[0]
        self.assertEqual((a[a.index("--t2-copy") + 1], a[a.index("--t2-cells") + 1]),
                         ("/home/m/c/6fff54b", "/home/m/s/runs/" + MARK + "/cells.json"))
        self.assertEqual(env.mac, [])   # run.py's finish() writes the derived rows
        rows = {c["cell"]: c["rows"] for c in res["cells"]}
        # the decisive row last (run.py's decisive_id and the report read rows[-1]), and named as final_row
        self.assertEqual(rows["stic:freebsd15"], ["chopin-20261108T030100Z-00000001", "chopin-20261108T030200Z-00000002"])
        self.assertEqual([c["final_row"] for c in res["cells"] if c["cell"] == "stic:freebsd15"],
                         ["chopin-20261108T030200Z-00000002"])
        # run.py's derived_rows reads the result: the final verdict is attempt 2's row's (a fake PASS here)
        self.assertEqual([c["verdict"] for c in res["cells"] if c["cell"] == "stic:freebsd15"], ["PASS"])

    def test_stop_engine_of_run_py(self):
        """stic's results as run.py's StopEngine reads them: stop-os after the second guest."""
        from t2lib import run as runmod
        eng = runmod.StopEngine("stic", RunCtx(self.tmp).classes.get("policy"))
        sr = stic.SticRun(self.ctx, Plan([]), FakeEnv({}))
        a1 = eng.feed(sr.res_of(row("debian12", "FAIL", needs={"xattrs": "FAIL"}, n=1)))
        a2 = eng.feed(sr.res_of(row("debian13", "FAIL", needs={"xattrs": "FAIL"}, n=2)))
        self.assertEqual((a1, a2), ("ok", "stop-os linux"))
        self.assertEqual(eng.feed(sr.res_of(row("zorin18", "HARNESS", n=3))), "ok")
        self.assertEqual(eng.feed(sr.res_of(row("mint22", "LOST", n=4))), "stop-run")

    def test_resume_runs_the_same_marks(self):
        env = FakeEnv({MARK: [(1, row("freebsd15", "PASS", n=1))]})
        sr = stic.SticRun(self.ctx, self.b2_plan(), env)
        sr.setup()
        sr.state = {"phase": "drive1", "due": ["stic:freebsd15"], "start": "2026-11-08T03:00:00Z"}
        sr.save()
        res = stic.resume(self.ctx, self.b2_plan(), env=env)
        a = env.argvs[0]
        self.assertEqual(a[a.index("--logs-mark") + 1], MARK)
        self.assertEqual(a[a.index("--out") + 1], os.path.join(self.tmp, "drive"))
        self.assertEqual(sorted(c["cell"] for c in res["cells"] if c["outcome"] == "class"),
                         ["stic:freebsd15-geli", "stic:ghostbsd26-zfs"])
        res2 = stic.resume(self.ctx, self.b2_plan(), env=env)   # done: nothing again
        self.assertEqual(len(env.argvs), 1)
        self.assertEqual(len(env.mac), 2)


# --- stic.run's exit, gate, host rules, resume and threads (the reviews of P4) ------------------------

def two_linux():
    return Plan([PC("stic:debian12", "due", "changed:harness"), PC("stic:zorin18", "due", "new")])


class TestSticRunExits(unittest.TestCase):
    """The run's exit (contract 1.7): 2 refused, 1 when any cell it had to run didn't end in a
    valid PASS or a stop fired, 0 otherwise."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2-p4-exit-")
        self.ctx = RunCtx(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def go(self, env, plan=None):
        return stic.SticRun(self.ctx, plan or two_linux(), env).go()

    def test_all_pass(self):
        env = FakeEnv({MARK: [(1, row("debian12", "PASS", n=1)), (1, row("zorin18", "PASS", n=2))]})
        res = self.go(env)
        self.assertEqual((res["exit"], res["stops"]), (0, []))
        self.assertIn(f"drive.py {MARK}: exit 0, 2 row(s)", res["notes"])

    def test_drive_py_refused(self):
        """drive.py ends at once (cannot read a host, another driver, an unrunnable guest): exit 2."""
        env = FakeEnv({MARK: []})
        env.rc[MARK] = 1
        res = self.go(env)
        self.assertEqual(res["exit"], 2)
        self.assertEqual(len(env.argvs), 1)   # no rerun round
        self.assertTrue(any(n.startswith("refused: drive.py exit 1, nothing started, no row: drive.py: cannot read")
                            for n in res["notes"]), res["notes"])
        self.assertEqual({c["outcome"] for c in res["cells"]}, {"not-started"})

    def test_nothing_ran(self):
        res = self.go(FakeEnv({MARK: []}))
        self.assertEqual(res["exit"], 1)
        self.assertEqual({c["outcome"] for c in res["cells"]}, {"not-started"})
        self.assertIn("not passed: stic:debian12 not-started; stic:zorin18 not-started", res["notes"])

    def test_started_but_no_row(self):
        env = FakeEnv({MARK: [(1, row("debian12", "PASS", n=1))]})
        env.rc[MARK], env.started[MARK] = 1, ["debian12", "zorin18"]
        res = self.go(env)
        self.assertEqual(res["exit"], 1)
        self.assertEqual({c["cell"]: c["outcome"] for c in res["cells"]}, {"stic:debian12": "ran", "stic:zorin18": "no-row"})

    def test_harness_and_lost(self):
        for v in ("HARNESS", "LOST"):
            env = FakeEnv({MARK: [(1, row("debian12", v, n=1)), (1, row("zorin18", "PASS", n=2))]})
            res = self.go(env)
            self.assertEqual((res["exit"], res["stops"]), (1, []), v)
            self.assertTrue(any(f"stic:debian12 {v}" in n for n in res["notes"]), res["notes"])

    def test_invalid_pass(self):
        bad = dict(row("debian12", "PASS", n=1), valid=False, note="inputs changed after the check began")
        env = FakeEnv({MARK: [(1, bad), (1, row("zorin18", "PASS", n=2))]})
        res = self.go(env)
        self.assertEqual(res["exit"], 1)
        self.assertTrue(any("stic:debian12 PASS, but its row chopin-20261108T030100Z-00000001 is invalid" in n
                            for n in res["notes"]), res["notes"])

    def test_attempt2_harness_leaves_attempt1_fail(self):
        for v2 in ("HARNESS", "LOST"):
            env = FakeEnv({MARK: [(1, row("debian12", "FAIL", needs={"xattrs": "FAIL"}, n=1))],
                           MARK + "-r2": [(1, row("debian12", v2, mark=MARK + "-r2", attempt=2, n=2))]})
            res = self.go(env, Plan([PC("stic:debian12", "due", "changed:harness")]))
            c = res["cells"][0]
            self.assertEqual((res["exit"], c["verdict"], c["final_row"], c["rows"]),
                             (1, "FAIL", "chopin-20261108T030100Z-00000001",
                              ["chopin-20261108T030200Z-00000002", "chopin-20261108T030100Z-00000001"]), v2)
            self.assertTrue(any("attempt 1's FAIL stands" in n for n in res["notes"]))

    def test_sticerror_is_refused(self):
        from t2lib import run as runmod
        self.assertTrue(issubclass(stic.SticError, runmod.Refused))


class TestSticRunGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2-p4-gate-")
        self.ctx = RunCtx(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_gate_that_cant_load_refuses(self):
        for name, gate in (("no gate.py", False), ("layers.json", FakeGate(broken="layers.json: Expecting value"))):
            env = FakeEnv({MARK: [(1, row("debian12", "PASS", n=1))]}, gate=gate)
            if gate is False:
                env.gate = lambda: None
            res = stic.SticRun(RunCtx(self.tmp), Plan([PC("stic:debian12", "due", "changed:bin")]), env).go()
            self.assertEqual((res["exit"], env.argvs), (2, []), name)   # no guest starts
            self.assertTrue(res["stops"][0].startswith("stop-family: the fast archiver gate can't"), res["stops"])
            os.remove(os.path.join(self.tmp, "stop"))

    def test_a_gate_that_cant_be_planned_refuses(self):
        class G(FakeGate):
            def gate_plan(self, ctx):
                return None

        gate = G(due=["gate:bin:abi"])
        env = FakeEnv({MARK: [(1, row("debian12", "PASS", n=1))]}, gate=gate)
        res = stic.SticRun(self.ctx, Plan([PC("stic:debian12", "due", "changed:bin")]), env).go()
        self.assertEqual((res["exit"], env.argvs, gate.ran), (2, [], []))
        self.assertTrue(res["stops"][0].startswith("stop-family: the fast archiver gate can't be planned"), res["stops"])

    def test_fast_layers_due_run_first(self):
        gate = FakeGate(due=["gate:bin:abi", "gate:loop:linux@chopin", "gate:holt"])
        env = FakeEnv({MARK: [(1, row("debian12", "PASS", n=1))]}, gate=gate)
        res = stic.SticRun(self.ctx, Plan([PC("stic:debian12", "due", "changed:bin")]), env).go()
        self.assertEqual(sorted(gate.ran), [("fast", ["gate:bin:abi"]), ("slow", ["gate:loop:linux@chopin"])])
        self.assertEqual((res["exit"], len(env.argvs)), (0, 1))

    def test_a_failed_fast_gate_starts_no_guest(self):
        gate = FakeGate(due=["gate:bin:abi"], ok=False)
        env = FakeEnv({MARK: [(1, row("debian12", "PASS", n=1))]}, gate=gate)
        res = stic.SticRun(self.ctx, Plan([PC("stic:debian12", "due", "changed:bin")]), env).go()
        self.assertEqual((res["exit"], env.argvs), (1, []))
        self.assertTrue(res["stops"][0].startswith("stop-family: the fast archiver gate failed"))

    def test_the_gate_plans_on_facts_with_its_own_wants(self):
        """A SticTest2 word's facts hold stic's wants only: the gate is planned and run on the
        facts probed again for every family (once per run), the run's own ctx left as it was."""
        seen = []

        class G(FakeGate):
            def gate_plan(self, ctx):
                seen.append(("plan", ctx.facts))
                return super().gate_plan(ctx)

            def run_layers(self, ctx, names, part=None, plan=None):
                seen.append((part, ctx.facts))
                return super().run_layers(ctx, names, part, plan)

        env = FakeEnv({MARK: [(1, row("debian12", "PASS", n=1))]}, gate=G(due=["gate:bin:abi", "gate:loop:linux@chopin"]))
        probes = []
        env.refresh_facts = lambda ctx, sets: probes.append(list(sets)) or {"chopin": {"ok": True, "gate": "wants"}}
        self.ctx.facts = {"chopin": {"ok": True, "stic": "only"}, "cinqcent": {"ok": True}}
        res = stic.SticRun(self.ctx, Plan([PC("stic:debian12", "due", "changed:bin")]), env).go()
        self.assertEqual(res["exit"], 0)
        self.assertEqual(len(probes), 1)
        want = {"chopin": {"ok": True, "gate": "wants"}, "cinqcent": {"ok": True}}
        self.assertEqual(sorted(p for p, _ in seen), ["fast", "plan", "plan", "slow"])
        self.assertTrue(all(f == want for _, f in seen), seen)
        self.assertEqual(self.ctx.facts["chopin"], {"ok": True, "stic": "only"})


class TestSticRunHosts(unittest.TestCase):
    """R22: swap-out on a host since the run began, and this run's own jobs' bytes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2-p4-hosts-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_with(self, loads, ctx=None):
        lin = ["debian12", "debian13", "mint22"]
        env = FakeEnv({MARK: [(6, row(g, "PASS", n=i)) for i, g in enumerate(lin, 1)]})
        env.loads = list(loads)
        res = stic.SticRun(ctx or RunCtx(self.tmp), Plan([PC(f"stic:{g}", "due", "new") for g in lin]), env).go()
        return res, env

    def test_swap_out(self):
        res, env = self.run_with([{"pswpout": 100, "ours": 0, "page": 4096},
                                  {"pswpout": 100 + (2 << 30) // 4096, "ours": 0, "page": 4096}])
        self.assertEqual(res["stops"], ["stop-starts: chopin: 2.00 GiB swapped out since the run began"])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "stop")))
        self.assertEqual(res["exit"], 1)

    def test_swap_base_from_the_run_start_facts(self):
        ctx = RunCtx(self.tmp)
        ctx.options["facts_start"] = {"chopin": {"preflight": {"pswpout": 10}}}
        res, env = self.run_with([{"pswpout": 10 + (1 << 30) // 4096 + 1, "ours": 0, "page": 4096}], ctx)
        self.assertEqual(len(res["stops"]), 1)
        self.assertTrue(res["stops"][0].startswith("stop-starts: chopin: 1.00 GiB swapped out"), res["stops"])

    def test_write_budget_counts_our_jobs_only(self):
        res, env = self.run_with([{"pswpout": 0, "ours": 3.9e9, "page": 4096},
                                  {"pswpout": 0, "ours": 4.2e9, "page": 4096}])
        self.assertEqual(res["stops"], ["stop-starts: chopin: this run's jobs wrote 4.20 GB, over 4 GB"])

    def test_under_run_pys_stop_engine(self):
        from t2lib import run as runmod
        eng = runmod.StopEngine("stic", RunCtx(self.tmp).classes.get("policy"))
        sr = stic.SticRun(RunCtx(self.tmp), Plan([]), FakeEnv({}))
        sr.engine = eng
        sr.setup()
        sr.env.loads = [{"pswpout": 0, "ours": 0, "page": 4096}, {"pswpout": (3 << 30) // 4096, "ours": 0, "page": 4096}]
        sr.check_hosts()
        sr.check_hosts()
        self.assertEqual(len(sr.stops), 1)
        self.assertTrue(sr.stops[0].startswith("stop-starts: chopin: 3.00 GiB swapped out"), sr.stops)

    def test_no_host_figures_stop_nothing(self):
        res, env = self.run_with([None, {"pswpout": None, "ours": None}])
        self.assertEqual((res["stops"], res["exit"]), ([], 0))


class TestSticRunResume(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2-p4-resume-")
        self.ctx = RunCtx(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_resume_counts_the_rows_fed_before(self):
        """Review repro: one harness result before the Mac-side t2 died, one after: stop-run."""
        plan = Plan([PC(f"stic:{g}", "due", "new") for g in ("debian12", "zorin18", "mint22")])
        first = row("debian12", "HARNESS", n=1)
        env = FakeEnv({MARK: [(1, first)]})
        sr = stic.SticRun(self.ctx, plan, env)
        sr.setup()
        sr.state = {"phase": "drive1", "due": [c.cell for c in plan.cells], "start": "2026-11-08T03:00:00Z",
                    "fed": [first["id"]]}
        sr.save()
        env2 = FakeEnv({MARK: [(1, row("zorin18", "HARNESS", n=2))]})
        env2.landed.append(first)   # in the ledger already
        res = stic.resume(self.ctx, plan, env=env2)
        self.assertEqual(len(res["stops"]), 1)
        self.assertTrue(res["stops"][0].startswith("stop-run: after stic:zorin18 HARNESS"), res["stops"])
        self.assertEqual(res["exit"], 1)

    def test_resume_waits_for_a_live_driver(self):
        env = FakeEnv({MARK: [(1, row("freebsd15", "PASS", n=1))]})
        env.busy = [True, True, True, False]
        env.landed.append(row("debian12", "HARNESS", n=9))   # landed while the old driver ran
        plan = Plan([PC("stic:freebsd15", "due", "new"), PC("stic:debian12", "due", "new")])
        sr = stic.SticRun(self.ctx, plan, env)
        sr.setup()
        sr.state = {"phase": "drive1", "due": ["stic:freebsd15", "stic:debian12"], "start": "2026-11-08T03:00:00Z"}
        sr.save()
        res = stic.resume(self.ctx, plan, env=env)
        self.assertEqual(len(env.argvs), 1)   # one drive.py, after the earlier one ended
        self.assertTrue(any("an earlier drive.py still runs" in m for m in env.logs))
        self.assertEqual({c["cell"]: c["verdict"] for c in res["cells"]}, {"stic:freebsd15": "PASS", "stic:debian12": "HARNESS"})

    def test_resume_under_run_py_without_cells_files(self):
        """run.py's resume rebuilds the copy from cells-HOST.json; a run that died before
        cells.json has none: the copy stic.json kept is used."""
        from t2lib import run as runmod
        plan = Plan([PC("stic:debian12", "due", "new")])
        sr = stic.SticRun(self.ctx, plan, FakeEnv({}))
        sr.setup()
        sr.state = {"phase": "start", "start": "2026-11-08T03:00:00Z"}
        sr.save()

        class RS:
            run, mac_dir, hosts, flakes = MARK, self.tmp, ["chopin"], []
            word = argparse.Namespace(text="SticTest2 rel4", family="stic")
            copy = {"chopin": {}}
            stops = runmod.StopEngine("stic", self.ctx.classes.get("policy"))
            written = []

            def host_run_dir(rs, h):
                return "/home/m/s/runs/" + MARK

            def write_cells(rs, plan, attempt=1, name=None, reruns=None):
                rs.written.append((attempt, dict(rs.copy)))
                return {}

            def log(rs, text):
                pass

        ctx = RunCtx(self.tmp)
        ctx.options = {}
        ctx.run = RS()
        env = FakeEnv({MARK: [(1, row("debian12", "PASS", n=1))]})
        res = stic.resume(ctx, plan, env=env)
        self.assertEqual(ctx.run.copy["chopin"]["path"], "/home/m/backotter-work/t2core-p4/copy/6fff54b")
        a = env.argvs[0]
        self.assertEqual(a[a.index("--t2-copy") + 1], "/home/m/backotter-work/t2core-p4/copy/6fff54b")
        self.assertEqual(res["exit"], 0)


class TestSticRunMisc(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2-p4-misc-")
        self.ctx = RunCtx(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_an_error_stops_our_drive_py(self):
        env = FakeEnv({MARK: [(5, row("debian12", "PASS", n=1))]})
        env.fail_at[MARK] = 2
        with self.assertRaises(OSError):
            stic.SticRun(self.ctx, two_linux(), env).go()
        self.assertEqual(env.stopped, [MARK])

    def test_save_from_two_threads(self):
        """Review repro: the slow gate's thread and the drive thread both save stic.json."""
        import threading
        sr = stic.SticRun(self.ctx, Plan([]), FakeEnv({}))
        sr.setup()
        errs = []

        def worker(k):
            for i in range(300):
                try:
                    if k == 0:
                        with sr.mu:
                            sr.state[f"k{i}"] = i
                        sr.save()
                    else:
                        sr.act("stop-starts", f"tick {i}") if i % 50 == 0 else sr.save()
                except Exception as e:   # noqa: BLE001
                    errs.append(repr(e))
        ths = [threading.Thread(target=worker, args=(k,)) for k in range(2)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        self.assertEqual(errs, [])
        with open(os.path.join(self.tmp, "stic.json")) as f:
            self.assertEqual(json.load(f)["state"]["k299"], 299)
        self.assertEqual([n for n in os.listdir(self.tmp) if ".tmp" in n], [])

    def test_reruns_use_rows_stic_flake_only(self):
        """Review repro: rows_stic decided not the known flake (a real FAIL beyond the 20 lines
        a row keeps): the runner doesn't match again over the cut lines."""
        sr = stic.SticRun(self.ctx, Plan([]), FakeEnv({}))
        sr.flakes = lambda: [{"guest": "freebsd15-ufs", "pattern": "^FAIL roundtrip-rustic->restic: no verdict"}]
        r = row("freebsd15-ufs", "FAIL", n=1)
        r["fails"] = ["FAIL roundtrip-rustic->restic: no verdict (a)"] * 20
        self.assertIsNone(sr.reruns({"freebsd15-ufs": r})["stic:freebsd15-ufs"]["flake"])

    def test_image_sums_before_cells_json(self):
        """Contract 1.4: a due guest without image-state and no cached sum (sculpt2610) gets its
        sum made on the host by the copy's t2-host, then every key again, before cells.json."""
        disk = "/home/m/backotter-work/vms/sculpt2610/sculpt2610.qcow2"
        self.ctx.facts = {"chopin": {"guests": {"sculpt2610": {"generation": None, "imgsum": None,
                                                               "disks": [{"path": disk, "size": 52953166, "mtime": 1}]},
                                                "debian12": {"generation": H64("c"), "imgsum": None, "disks": []}}}}
        old = Plan([PC("stic:sculpt2610", "due", "fresh:names", key=H64("1")), PC("stic:debian12", "due", "fresh:names")])
        new = Plan([PC("stic:sculpt2610", "due", "fresh:names", key=H64("2")), PC("stic:debian12", "due", "fresh:names")])
        env = FakeEnv({MARK: [(1, row("sculpt2610", "PASS", n=1)), (1, row("debian12", "PASS", n=2))]})
        env.refresh_facts = lambda ctx, sets: {"chopin": {"guests": {"sculpt2610": {"imgsum": "img:" + H64("d")}}}}
        env.replan = lambda word, ctx: new
        res = stic.SticRun(self.ctx, old, env).go()
        self.assertEqual(env.imgsums, [("chopin", "/home/m/backotter-work/t2core-p4/copy/6fff54b/testvm/test2/t2-host",
                                        "/home/m/backotter-work/t2core-p4/state", [disk])])
        cells = env.files[("chopin", "/home/m/backotter-work/t2core-p4/state/runs/" + MARK + "/cells.json")]["cells"]
        self.assertEqual(cells["sculpt2610"]["planned_key"], H64("2"))
        self.assertIn("imgsum: {'made': ['sculpt2610@chopin'], 'failed': []}", res["notes"])
        self.assertEqual(res["exit"], 0)

    def test_no_image_sums_needed(self):
        self.ctx.facts = {"chopin": {"guests": {"debian12": {"generation": H64("c"), "disks": []}}}}
        env = FakeEnv({MARK: [(1, row("debian12", "PASS", n=1))]})
        stic.SticRun(self.ctx, Plan([PC("stic:debian12", "due", "new")]), env).go()
        self.assertEqual(env.imgsums, [])


# --- R10: drive.py writes a row as each guest ends; a resumed round writes only the missing --------------

class TestDriveRows(unittest.TestCase):
    def test_killed_driver_then_resume(self):
        writes = []
        snaps = {"n": 0}

        def fake_ssh(host, cmd, script=None, t=90):
            if " row stic " in cmd:
                g = cmd.split(" --guest ")[1].split()[0]
                writes.append(g)
                return 0, f"chopin-20261108T0400{len(writes):02d}Z-{len(writes):08x}\n", ""
            return 0, "", ""

        def fake_status(host, full, mark, guests):
            jobs = {"debian12": dict(start=1000, exit=0, end=1100, ports=[2780], alive=False)}
            if snaps["n"] >= 1:
                jobs["zorin18"] = dict(start=1000, exit=0, end=1300, ports=[2781], alive=False)
            else:
                jobs["zorin18"] = dict(start=1000, exit=None, end=None, ports=[2781], alive=True)
            return dict(full=full, temp=50.0, nvme=[], budget=None, room=(20.0, 20.0, 0, 0), ceil=None, noram=False,
                        ledger=[], waits=[], slots_other=0, slots_ours=[], qemu=[], listen=set(), jobs=jobs,
                        slotwait=[])

        def fake_script(host, script, *args, t=90):
            return 0, "END\n", ""

        old = (DRIVE.ssh, DRIVE.status, DRIVE.run_script)
        DRIVE.ssh, DRIVE.status, DRIVE.run_script = fake_ssh, fake_status, fake_script
        try:
            with tempfile.TemporaryDirectory() as tmp:
                a = argparse.Namespace(out=tmp, logs_mark=MARK, hosts=["chopin"], slots=3, first=set(), poll=1, hot_c=90,
                                       round="rel4", bin="~/backotter-work/roundrel4/bin", t2_run=MARK,
                                       t2_copy="/home/m/c/6fff54b", t2_cells="/home/m/s/runs/x/cells.json", t2_attempt=1,
                                       stop_file=None, skip_file=None)
                specs = DRIVE.load_specs(["debian12", "zorin18"])
                est = {"debian12": 100, "zorin18": 300}
                probes = {"chopin": dict(have={"linux/restic-bko", "linux/rustic-bko"})}
                with contextlib.redirect_stdout(io.StringIO()):
                    d1 = DRIVE.Driver(a, ["debian12", "zorin18"], specs, est, probes, "x/testvm/stictest1")
                    for g in ("debian12", "zorin18"):   # both started by this driver
                        d1.jobs()[g] = d1.new_job("chopin", [2780], time.time())
                    d1.take_status("chopin", time.time(), 15)   # debian12 exits: its row; then the driver dies
                    d1.save()
                self.assertEqual(writes, ["debian12"])
                snaps["n"] = 1
                with contextlib.redirect_stdout(io.StringIO()):
                    d2 = DRIVE.Driver(a, ["debian12", "zorin18"], specs, est, probes, "x/testvm/stictest1")
                    d2.take_status("chopin", time.time(), 15)   # the resumed driver: zorin18's exit
                    d2.collect()
                self.assertEqual(writes, ["debian12", "zorin18"])   # no duplicate for debian12
                d1.logf.close()
                d2.logf.close()
        finally:
            DRIVE.ssh, DRIVE.status, DRIVE.run_script = old


if __name__ == "__main__":
    unittest.main()
