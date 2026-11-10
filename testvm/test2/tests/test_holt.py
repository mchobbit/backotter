"""P5's unit tests (contract U5.1-U5.3): rows_holt over fixture records of all three
drivers, the todo (order, variants, pins), holt.run against fake hosts, spec.json,
and ht1-lib.sh's ht_t2_row hook (where a bash 4 or newer is at hand).

No host, no VM: python3 -m unittest discover testvm/test2/tests (Python 3.9+).
The fixture records (fixtures/p5/k/) are copies of r4all's on Chopin (report.txt
and logs/ only, no dumps) and two made in their format (an overrun, a swept run).
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
WT = os.path.dirname(os.path.dirname(T2))
sys.path.insert(0, T2)

from t2lib import holt, keys, rows_holt  # noqa: E402

try:
    from t2lib import ledger
except ImportError:   # P1's writer isn't built: its checks are skipped
    ledger = None
try:
    from t2lib import facts_host
except ImportError:
    facts_host = None

FX = os.path.join(HERE, "fixtures", "p5", "k")
_SAVED, _TMP = {}, None


def setUpModule():
    """No test here may reach a host or a default root: every root is a temporary
    directory's, and an ssh that slips past the fakes fails at once (hostrun's T2_SSH)."""
    global _TMP
    _TMP = tempfile.mkdtemp(prefix="t2-p5-roots-")
    env = {"T2_MAC_ROOT": os.path.join(_TMP, "mac"), "T2_HOST_ROOT": os.path.join(_TMP, "host"),
           "T2_COPY_ROOT": os.path.join(_TMP, "copy"), "T2_SSH": "false"}
    for k, v in env.items():
        _SAVED[k] = os.environ.get(k)
        os.environ[k] = v
    for k in ("T2_LEDGER", "T2_CELLS", "T2_RUN", "T2_WORK", "HT_WORK", "HT_RECORDS"):
        _SAVED[k] = os.environ.pop(k, None)


def tearDownModule():
    for k, v in _SAVED.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    shutil.rmtree(_TMP, True)
OLD_ROOT = "/home/m/backotter-work"
GEN = "0c453f0127b0a3151883b4bd971a90558aa3312d2cb00f85e0a333d5b7ecb425"
RUSTIC = {"linux": "886ef2281a118c55d084295e17d286f7360cd1511a95b73be5d15b15784857f8",
          "freebsd": "f6411bf9e2560599c050f1e2890118e35a19eaea74a8fe4b355f08ad25c99201"}
RESCUER = "ddc4bd53fdfc05b432929fa1c3be766fcf415aae04cc98aed8212964e04638e3"
DEBS = {"f2fs-tools_1.16.0-1.1+b1_amd64.deb": "2470227305f92e0399147bd5f74a4b1c423d081a61ce705c0e689f2b408ead9e",
        "jfsutils_1.1.15-6_amd64.deb": "a454f4d57a1fe0af4be7cf9de50f0a30de1d2245f40f690fb6efcb1993de6751",
        "bcachefs": "ee95b52870a885949383d88ed4af1d08b7541ba3b19317ecf051c1a8b46e3728"}


def rd(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def wr(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def jload(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def jdump(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def facts_for(guests, host="chopin", uefi_rescuer=True):
    """A host's facts as facts_host.py gives them, synthetic."""
    g = {}
    for name in guests:
        g[name] = {"generation": GEN, "files": {n: "absent" for n in keys.GUEST_FILES}, "firmware": "bios",
                   "disks": [], "imgsum": None}
    images = {"rescuer.qcow2": {"sha256": RESCUER, "imgsum": None}}
    if uefi_rescuer:
        images["rescuer-uefi.qcow2"] = {"sha256": "1" * 64, "imgsum": None}
    for n in ("openbsd8-newduid.qcow2", "dragonfly6-newfsid.qcow2", "omnios58-reguid.qcow2"):
        images[n] = {"sha256": None, "imgsum": "img:" + "2" * 64}
    return {"host": host, "versions": {"kernel": "6.12.115+deb13.1-amd64", "zfs": "2.4.0-1", "qemu": "QEMU 10.0.13",
                                       "ovmf": "2025.02-8", "pkgs": {}},
            "guests": g, "rescuers": {"images": images, "bin": dict(DEBS)},
            "sets": {"bko-bin": {"sums": {f"{p}/rustic-bko": RUSTIC.get(p) or hashlib.sha256(p.encode()).hexdigest()
                                          for p in ("linux", "freebsd", "netbsd", "openbsd", "dragonfly", "illumos",
                                                    "haiku")}, "digests": {}}},
            "plumbing": {"shared:cmp-run.sh": "a" * 64, "shared:testvm/compare/iolog.sh": "b" * 64},
            "host_inputs": {"~/backotter-work/tools/redox/bin": "dir:" + "3" * 64}}


class FakeHostCtx:
    """What t2-host gives a row builder (hostrun.HostCtx), with synthetic facts."""

    def __init__(self, work, host_root, facts, tree=None):
        self.host, self.work, self.host_root, self._facts = "chopin", work, host_root, facts
        self.tree = tree or {"commit": "7a322d1", "committed": True, "dirty": {}, "copy": "synced"}   # r4all's tree

    def facts(self, want=None):
        return self._facts

    def tree_info(self, base):
        return dict(self.tree, path=base)


class RecordsMixin:
    """Fixture records in a temporary K, their manifest made to name this checkout (its
    files' sha256s as they are now) and a guest image of the temporary directory's."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2-p5-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work)
        for v in ("T2_CELLS", "T2_RUN", "WEAR_TAG"):
            os.environ.pop(v, None)

    def k(self, name, env=None, drop_env=(), seeded=False, changed=None, holt_bin=None):
        self.nk = getattr(self, "nk", 0) + 1
        k = os.path.join(self.tmp, f"K{self.nk}-{name}")
        shutil.copytree(os.path.join(FX, name), k)
        mp = os.path.join(k, "logs", "manifest.txt")
        if os.path.exists(mp):
            out = []
            for line in rd(mp).splitlines():
                line = line.replace(OLD_ROOT + "/testvm/", WT + "/testvm/")
                m = re.match(r"sha256 ([0-9a-f]{64}) (\S+)$", line)
                if m and m.group(2).startswith(WT + "/") and os.path.isfile(m.group(2)):
                    h = sha(m.group(2))
                    if changed and m.group(2).endswith(changed):
                        h = "0" * 64
                    line = f"sha256 {h} {m.group(2)}"
                m = re.match(r"image (\S+/vms/(\S+?)/\S+) \d+ \d+ \d+(.*)$", line)
                if m:
                    img = os.path.join(self.work, "vms", m.group(2), os.path.basename(m.group(1)))
                    os.makedirs(os.path.dirname(img), exist_ok=True)
                    wr(img, "qcow2")
                    s = os.stat(img)
                    line = f"image {img} {s.st_size} {int(s.st_mtime)} {s.st_ino}{m.group(3)}"
                if line.startswith("env ") and line[4:].split("=")[0] in drop_env:
                    continue
                if seeded and line.startswith("driver "):
                    line += " seeded from /bkoscratch/holttest1d/r4all/debian13.1"
                if holt_bin and line.startswith("sha256 ") and line.endswith("/bin/bko-holt"):
                    line = line.replace("/bkoscratch/holttest1d/bin/bko-holt", holt_bin + "/bko-holt")
                out.append(line)
            for k_, v in (env or {}).items():
                out.insert(2, f"env {k_}={v}")
            wr(mp, "\n".join(out) + "\n")
        return k

    def build(self, k, gv, driver="ht1.sh", rc=0, code=0, run="r4all", now="2026-11-08T03:10:00Z", extra=(),
              facts=None, guests=None, tree=None):
        ctx = FakeHostCtx(self.work, self.tmp, facts or facts_for(guests or [gv.split("-")[0], "freebsd15-ufs",
                                                                              "netbsd11", "haiku6", gv]), tree)
        argv = ["--k", k, "--r", "/mnt/bko-ram/x", "--run", run, "--gv", gv, "--driver", driver, "--rc", str(rc),
                "--code", str(code), "--now", now] + list(extra)
        return rows_holt.build(argv, ctx)

    def complete_ok(self, row):
        if ledger is None:
            return row
        c = ledger.complete(row, host="chopin")
        self.assertEqual(ledger.validate(c), [], c)
        return c


class U51RowsHolt(RecordsMixin, unittest.TestCase):
    def test_linux_pass_test1(self):
        row = self.build(self.k("linux-pass"), "debian13")
        self.complete_ok(row)
        self.assertEqual((row["verdict"], row["class"], row["cell"], row["rule"], row["kind"]),
                         ("PASS", "pass", "holt:debian13", "test1", "run"))
        self.assertTrue(row["valid"], row["note"])
        self.assertEqual(row["origin"], "ht1:r4all:debian13:1:20261108T025034Z")   # a Test1 run's: with its start
        self.assertEqual(row["word"], "HoltTest1 r4all")
        self.assertIsNone(row["planned_key"])
        self.assertEqual(row["start"], "2026-11-08T02:50:34Z")
        self.assertEqual(row["wall_s"], 1166)   # 02:50:34 to the row's 03:10:00
        self.assertEqual(row["ram_peak_kib"], 2072352)
        self.assertEqual(row["counts"], {"PASS": 22, "FAIL": 0, "LIMIT": 0})
        self.assertTrue({n for n, v in row["needs"].items() if v == "PASS"} >= {
            "refuse-nomap", "refuse-root", "refuse-decoy", "first-last-mib", "fsmeta", "boots-by-itself",
            "boot-state", "health", "dump-judge", "stage:close", "no-write-without-commit"}, row["needs"])
        b = row["inputs"]["bin"]
        self.assertEqual(b["linux/bko-holt"], "sha256:f341270ccedbbe34446f9d4e11595150a7c7cf5a82c63c7458a2d8ed478d661d")
        self.assertEqual(b["linux/rustic-bko"], "sha256:" + RUSTIC["linux"])
        self.assertEqual(row["inputs"]["rescuer"]["rescuer/rescuer.qcow2"], "sha256:" + RESCUER)
        self.assertEqual(row["inputs"]["guest"]["debian13:generation"], "gen:" + GEN)
        self.assertIn("testvm/holttest1d/ht1.sh", row["inputs"]["harness"])
        self.assertIn("testvm/holttest1d/judge.py", row["inputs"]["judge"])
        self.assertIn("shared:cmp-run.sh", row["inputs"]["plumbing"])
        self.assertEqual(row["key"], keys.key("holt:debian13", row["inputs"]))
        self.assertEqual(row["meta"]["bin"]["linux/rustic-bko"]["set"], "bko-bin")
        self.assertEqual(row["meta"]["dumps"], {})   # the fixtures keep no dumps (R8)
        self.assertEqual(row["logs"].split(":", 1)[1], row["logs"][row["logs"].index(":") + 1:])

    def test_bsd_pass(self):
        row = self.build(self.k("bsd-pass"), "freebsd15", driver="ht1-bsd.sh")
        self.complete_ok(row)
        self.assertEqual((row["verdict"], row["cell"], row["meta"]["kind"]), ("PASS", "holt:freebsd15", "bsd"))
        self.assertTrue(row["valid"], row["note"])
        self.assertIn("freebsd/bko-holt", row["inputs"]["bin"])
        self.assertEqual(row["inputs"]["bin"]["freebsd/rustic-bko"], "sha256:" + RUSTIC["freebsd"])
        self.assertEqual(row["inputs"]["rescuer"], {"freebsd15-ufs:generation": "gen:" + GEN})
        self.assertIn("testvm/holttest1d/ht1-bsd.sh", row["inputs"]["harness"])
        self.assertEqual(row["needs"]["fsmeta"], "PASS")   # mtree's line

    def test_offline_pass(self):
        row = self.build(self.k("offline-pass"), "sculpt2610", driver="ht1-offline.sh")
        self.complete_ok(row)
        self.assertEqual((row["verdict"], row["meta"]["kind"]), ("PASS", "offline"))
        self.assertTrue(row["valid"], row["note"])
        h = row["inputs"]["harness"]
        for p in ("testvm/holttest1d/ht1-offline.sh", "testvm/sculpt-check.sh", "testvm/haiku/drive.py"):
            self.assertIn(p, h)
        self.assertEqual(list(row["inputs"]["rescuer"]), ["rescuer/rescuer.qcow2"])

    def test_linux_fail_early_stop_is_valid(self):
        row = self.build(self.k("linux-fail"), "fedora44-raid", rc=1, code=1, guests=["fedora44-raid"],
                         tree={"commit": "6fff54b", "committed": True, "dirty": {}, "copy": "synced"})
        self.complete_ok(row)
        self.assertEqual((row["verdict"], row["class"]), ("FAIL", "fail"))
        self.assertEqual(row["fails"], ["mount the source read-only"])
        self.assertEqual(row["needs"]["backup-mount"], "FAIL")
        self.assertTrue(row["valid"], row["note"])   # asked for all four phases, stopped after backup

    def test_term_is_lost(self):
        row = self.build(self.k("bsd-term"), "freebsd15-geli", driver="ht1-bsd.sh", rc=143, code=143,
                         guests=["freebsd15-geli", "freebsd15-ufs"])
        self.complete_ok(row)
        self.assertEqual((row["verdict"], row["class"]), ("LOST", "lost"))
        self.assertEqual(row["needs"].get("harness"), "FAIL")

    def test_overrun_is_harness(self):
        row = self.build(self.k("linux-overrun"), "mint22", rc=143, code=1, run="t2h-20261108T0500-0a1b")
        self.complete_ok(row)
        self.assertEqual((row["verdict"], row["class"]), ("HARNESS", "harness"))
        self.assertIn("over its reservation", row["note"])

    def test_swept_is_lost(self):
        # (No exit: LOST from kept.txt's swept: alone, not from a signal's exit code.)
        k = self.k("linux-swept")
        argv = ["--k", k, "--run", "t2h-20261108T0500-0a1b", "--gv", "arch", "--driver", "ht1.sh",
                "--now", "2026-11-08T03:10:00Z"]
        row = rows_holt.build(argv, FakeHostCtx(self.work, self.tmp, facts_for(["arch"])))
        self.complete_ok(row)
        self.assertIsNone(row["extra"]["rc"])
        self.assertEqual((row["verdict"], row["class"]), ("LOST", "lost"))
        self.assertIn("swept", row["note"])

    def test_overrun_after_result_is_pass(self):
        # ht_end: stopped over its reservation only after its RESULT line, "that verdict stands".
        k = self.k("linux-pass")
        wr(os.path.join(k, "logs", "kept.txt"), "overrun: records only, 2026-11-08T02:53:12Z; harness: RAM disk "
                                                "use 3.1 GiB over the 3 GiB reserved; deleted share\n")
        row = self.build(k, "debian13")
        self.complete_ok(row)
        self.assertEqual((row["verdict"], row["class"]), ("PASS", "pass"))
        self.assertTrue(row["valid"], row["note"])
        self.assertIn("overran its reservation after its RESULT line", row["note"])

    def test_phases_unknown_is_invalid(self):
        k = self.k("linux-pass")
        rep_ = os.path.join(k, "report.txt")
        wr(rep_, "\n".join(x for x in rd(rep_).splitlines() if not x.startswith("HoltTest1 ")) + "\n")
        row = self.build(k, "debian13")
        self.assertEqual(row["verdict"], "PASS")
        self.assertFalse(row["valid"])
        self.assertIn("the phases it ran are unknown", row["note"])

    def test_holt_commit_only_for_a_code_build(self):
        # A HoltTest1 run's bko-holt is bin/'s, built by hand: the tree's commit isn't its source,
        # so plan.py's version-only rule must never read it as one (R14).
        row = self.build(self.k("linux-pass"), "debian13")
        self.assertEqual((row["meta"]["holt"]["code"], row["meta"]["holt"]["commit"]), (None, None))
        self.assertEqual(row["commit"], "7a322d1")

    def test_refused_and_lost(self):
        # (--k K given but a refusal reads no records: a dead run's R may still be there.)
        row = self.build(self.k("linux-fail"), "debian13", rc=2, code=2, extra=["--refused"])
        self.complete_ok(row)
        self.assertEqual((row["verdict"], row["class"], row["needs"]), ("HARNESS", "harness", {}))
        self.assertIn("refused before any phase", row["note"])
        self.assertEqual((row["start"], row["fails"], row["counts"]["PASS"]), ("2026-11-08T03:10:00Z", [], 0))
        row = self.build(os.path.join(self.tmp, "nothing"), "debian13", rc=1, code=1, extra=["--lost"])
        self.complete_ok(row)
        self.assertEqual((row["verdict"], row["class"]), ("LOST", "lost"))

    def cells(self, gv, planned=None, attempt=1, flake=None, cell=None, run="t2h-20261108T0300-abcd"):
        doc = {"run": run, "word": "HoltTest2 debian13", "attempt": attempt,
               "copy": {"id": "6fff54b", "path": WT, "commit": "6fff54b" + "0" * 33, "dirty": {}},
               "cells": {gv: {"cell": cell or rows_holt.cell_of(gv), "rule": "holt:code",
                              "reasons": ["holt code changed: holt/src/load/exec.rs"], "planned_key": planned,
                              "attempt": attempt, "rerun_of": "chopin-20261108T030000Z-0123abcd" if attempt == 2 else None,
                              "flake": flake, "limits_classed": []}}}
        p = os.path.join(self.tmp, f"cells-{attempt}.json")
        jdump(doc, p)
        os.environ["T2_CELLS"] = p
        os.environ["T2_RUN"] = run
        self.addCleanup(os.environ.pop, "T2_CELLS", None)
        self.addCleanup(os.environ.pop, "T2_RUN", None)
        return p

    def test_t2_row_planned_key(self):
        k = self.k("linux-pass")
        first = self.build(k, "debian13")
        self.cells("debian13", planned=first["key"])
        row = self.build(k, "debian13", run="t2h-20261108T0300-abcd")
        self.complete_ok(row)
        self.assertEqual(row["origin"], "ht1:t2h-20261108T0300-abcd:debian13:1")   # R7's: no start
        self.assertEqual((row["rule"], row["word"], row["run"]), ("holt:code", "HoltTest2 debian13",
                                                                  "t2h-20261108T0300-abcd"))
        self.assertEqual(row["planned_key"], row["key"])
        self.assertTrue(row["valid"], row["note"])
        self.cells("debian13", planned="f" * 64)
        row = self.build(k, "debian13", run="t2h-20261108T0300-abcd")
        self.assertFalse(row["valid"])
        self.assertIn("key differs from the plan's", row["note"])

    def test_attempt_two(self):
        k = self.k("linux-pass")
        self.cells("debian13", attempt=2)
        row = self.build(k, "debian13", run="t2h-20261108T0300-abcd-r2")
        self.complete_ok(row)
        self.assertEqual((row["verdict"], row["class"], row["attempt"]), ("FLAKY-FAIL", "fail", 2))
        self.assertEqual(row["origin"], "ht1:t2h-20261108T0300-abcd-r2:debian13:2")
        self.assertEqual(row["run"], "t2h-20261108T0300-abcd")
        self.assertEqual(row["extra"]["rerun_of"], "chopin-20261108T030000Z-0123abcd")
        self.cells("debian13", attempt=2, flake={"why": "a known flake"})
        row = self.build(k, "debian13", run="t2h-20261108T0300-abcd-r2")
        self.assertEqual((row["verdict"], row["class"]), ("PASS", "known-flake"))

    def test_invalid_runs(self):
        row = self.build(self.k("linux-pass", env={"HT_QUICK": "1"}), "debian13")
        self.assertFalse(row["valid"])
        self.assertIn("HT_QUICK=1", row["note"])
        row = self.build(self.k("linux-pass", seeded=True), "debian13")
        self.assertFalse(row["valid"])
        self.assertIn("earlier attempt", row["note"])
        row = self.build(self.k("linux-pass", changed="testvm/vm-mount"), "debian13")
        self.assertFalse(row["valid"])
        self.assertIn("changed during the run: testvm/vm-mount", row["note"])
        rep = os.path.join(self.k("linux-pass"), "report.txt")
        t = rd(rep).replace("phases: source backup rebuild verify", "phases: rebuild verify")
        wr(rep, t)
        row = self.build(os.path.dirname(rep), "debian13")
        self.assertFalse(row["valid"])
        self.assertIn("phases rebuild verify, not all four", row["note"])

    def test_code_dir_and_variant(self):
        code = "c" * 64
        hb = os.path.join(self.tmp, "holt-bin", code)
        os.makedirs(hb)
        k = self.k("linux-pass", env={"HT_HOLT_BIN": hb, "HT_VARIANT": "oneshot"}, drop_env=("HT_VARIANT",),
                   holt_bin=hb)
        wr(os.path.join(hb, "SHA256SUMS"),
           "f341270ccedbbe34446f9d4e11595150a7c7cf5a82c63c7458a2d8ed478d661d *bko-holt\n")
        row = self.build(k, "debian13-oneshot", guests=["debian13"])
        self.complete_ok(row)
        self.assertEqual(row["cell"], "holt:debian13+oneshot")
        self.assertEqual(row["inputs"]["bin"]["linux/bko-holt"], "code:" + code)
        self.assertEqual(row["meta"]["holt"]["code"], code)
        self.assertTrue(row["valid"], row["note"])
        base = self.build(self.k("linux-pass"), "debian13")
        self.assertNotEqual(row["key"], base["key"])
        wr(os.path.join(hb, "bko-holt"), "another build")   # the set's binary, replaced during the run
        row = self.build(k, "debian13-oneshot", guests=["debian13"])
        self.assertFalse(row["valid"])
        self.assertIn("bko-holt changed during the run", row["note"])
        os.remove(os.path.join(hb, "bko-holt"))
        wr(os.path.join(hb, "SHA256SUMS"), "e" * 64 + " *bko-holt\n")
        row = self.build(k, "debian13-oneshot", guests=["debian13"])
        self.assertFalse(row["valid"])
        self.assertIn("SHA256SUMS", row["note"])
        wr(os.path.join(hb, "SHA256SUMS"), "")   # no line for the platform: nothing vouches for the binary
        row = self.build(k, "debian13-oneshot", guests=["debian13"])
        self.assertFalse(row["valid"])
        self.assertIn("has no SHA256SUMS line for linux/bko-holt", row["note"])

    def test_plan_and_row_keys_agree(self):
        # The planner's key (plan.py's opts for holt: rustic_set bko-bin, CODE from the tree)
        # and the row's (HT_HOLT_BIN=.../CODE) on the same facts: equal, or every HoltTest2 row
        # would be invalid.
        src = keys.TreeSource(WT)
        code = keys.holt_code(src)
        hb = os.path.join(self.tmp, "holt-bin", code)
        os.makedirs(hb)
        wr(os.path.join(hb, "SHA256SUMS"),
           "f341270ccedbbe34446f9d4e11595150a7c7cf5a82c63c7458a2d8ed478d661d *bko-holt\n")
        facts = facts_for(["debian13"])
        for cell, gv, var in (("holt:debian13", "debian13", None), ("holt:debian13+oneshot", "debian13-oneshot", "oneshot")):
            env = {"HT_HOLT_BIN": hb}
            if var:
                env["HT_VARIANT"] = var
            k = self.k("linux-pass", env=env, drop_env=("HT_VARIANT",), holt_bin=hb)
            row = self.build(k, gv, facts=facts)
            planned = keys.resolve(cell, src, facts, keys.Specs(src), {"rustic_set": "bko-bin"})
            self.assertEqual(row["inputs"], planned.inputs, cell)
            self.assertEqual(row["key"], planned.key, cell)
            self.assertEqual(row["meta"]["holt"]["code"], code)

    def test_tree_moved(self):
        # The manifest's tree 7a322d1 against the tree's commit now.
        row = self.build(self.k("linux-pass"), "debian13", tree={"commit": "6ad28d7", "committed": True, "dirty": {},
                                                               "copy": "synced"})
        self.assertFalse(row["valid"])
        self.assertIn("the tree changed during the run (tree 7a322d1 at its start, 6ad28d7 now)", row["note"])
        row = self.build(self.k("linux-pass"), "debian13", tree={"commit": "7a322d1" + "0" * 33, "committed": True,
                                                               "dirty": {}, "copy": "7a322d1"})
        self.assertTrue(row["valid"], row["note"])

    def test_dirty_copy(self):
        tree = {"commit": "7a322d1" + "0" * 33, "committed": False, "dirty": {"testvm/holttest1d/ht1.sh": "d" * 64},
                "copy": "7a322d1.d0123abcd"}
        row = self.build(self.k("linux-pass"), "debian13", tree=tree)
        self.complete_ok(row)
        self.assertEqual((row["committed"], row["copy"]), (False, "7a322d1.d0123abcd"))
        self.assertEqual(row["dirty"], tree["dirty"])
        self.assertIn("the tree changed during the run (tree 7a322d1 at its start, 7a322d1.dirty now)", row["note"])

    def test_needs_names(self):
        cases = {
            "load refuse-decoy: refused, but not for the decoy's filesystem": "refuse-decoy",
            "status after the one-command rebuild: next: format": "status:oneshot",
            "status after assemble: assemble  todo": "status:assemble",
            "load oneshot (exit 0)": "stage:oneshot",
            "mtree: 3 differences: x": "fsmeta",
            "the restored pool zroot imported from all its disks (ONLINE, 2 of 2 disks)": "verify-pool",
            "restored arrays degraded as the source's: md127 raid1 [U_]": "degraded-arrays",
            "source's arrays degraded: md127 raid1 [U_]": "source-degraded",
            "the source's pool zroot imported from all its disks (ONLINE, 2 of 2 disks)": "backup-mount",
            "dump of the restored disks: x": "restored-dump",
            "the decoy changed (logs/edges-before.txt, edges-end.txt)": "decoy",
            "something new: odd": "other:something-new",
        }
        for text, want in cases.items():
            self.assertEqual(rows_holt.need_of(text), want, text)

    def test_backfill_rows(self):
        ce = {"cell": "holt:debian13+oneshot", "rule": "holt:code", "reasons": ["x"], "planned_key": "a" * 64}
        for rc, kept, want in ((2, "", "HARNESS"), (143, "", "LOST"), (0, "swept: records only (the run died)", "LOST")):
            r = rows_holt.backfill_row("chopin", "t2h-20261108T0300-abcd", "t2h-20261108T0300-abcd", "debian13-oneshot",
                                       1, rc, kept, "2026-11-08T03:00:00Z", ce, "HoltTest2D",
                                       copy={"id": "6fff54b", "commit": "6fff54b"}, logs="chopin:/k")
            c = self.complete_ok(r)
            self.assertEqual((c["verdict"], c["valid"], c["origin"], c["planned_key"]),
                             (want, False, "ht1:t2h-20261108T0300-abcd:debian13-oneshot:1", "a" * 64))


class U52Todo(unittest.TestCase):
    class PC:
        def __init__(self, cell, rule="holt:code", reasons=(), keys_=None):
            self.cell, self.rule, self.reasons, self.keys, self.status, self.family = cell, rule, list(reasons), \
                dict(keys_ or {}), "due", "holt"

    def test_names(self):
        for cell, gv in (("holt:debian13", "debian13"), ("holt:debian13-stack+bigger", "debian13-stack-bigger"),
                         ("holt:ubuntu26-raid+degraded", "ubuntu26-raid-degraded"), ("holt:omnios58-mirror", "omnios58-mirror")):
            self.assertEqual(rows_holt.gv_of(cell), gv)
            self.assertEqual(rows_holt.cell_of(gv), cell)
        self.assertEqual(rows_holt.cell_of("debian13-oneshot", "oneshot"), "holt:debian13+oneshot")
        self.assertEqual(holt.todo_line("holt:debian13-stack+bigger"), "debian13-stack+bigger")
        self.assertEqual(holt.base_guest("holt:debian13-stack+bigger"), "debian13-stack")

    def test_order_groups(self):
        pcs = [self.PC("holt:mint22"), self.PC("holt:debian13", "canary:plumbing"),
               self.PC("holt:rocky10", "holt:plan-diff"), self.PC("holt:arch", "holt:code", ["x; holt:plan-diff (format)"]),
               self.PC("holt:debian13-stack+bigger", "holt:variants"), self.PC("holt:redox", "canary:host")]
        self.assertEqual(holt.order_groups(pcs), [["holt:debian13", "holt:redox"],
                                                  ["holt:rocky10", "holt:arch", "holt:debian13-stack+bigger"],
                                                  ["holt:mint22"]])

    def test_only_elsewhere(self):
        both = {"chopin": "k", "cinqcent": "k"}
        pcs = [self.PC("holt:openbsd8", keys_={"chopin": "k"}), self.PC("holt:dragonfly6", keys_={"cinqcent": "k"}),
               self.PC("holt:redox", keys_={"cinqcent": "k"}), self.PC("holt:debian13", keys_=both),
               self.PC("holt:debian13-stack", keys_={"chopin": "k"}),
               self.PC("holt:debian13-stack+bigger", keys_={"chopin": "k"}), self.PC("holt:mint22")]
        oe = holt.only_elsewhere(pcs, ["chopin", "cinqcent"])
        self.assertEqual(oe, {"chopin": ["dragonfly6", "redox"], "cinqcent": ["debian13-stack", "openbsd8"]})
        # classes.json's pins hold even for a cell the plan gave no keys.
        pcs = [self.PC("holt:openbsd8"), self.PC("holt:mint22")]
        self.assertEqual(holt.only_elsewhere(pcs, ["chopin", "cinqcent"], {"openbsd8": "chopin"}),
                         {"chopin": [], "cinqcent": ["openbsd8"]})

    def test_platforms(self):
        sf = keys.Specs(keys.TreeSource(WT)).get("holt")
        cells = ["holt:debian13", "holt:redox", "holt:freebsd15", "holt:alpinebsd", "holt:omnios58-mirror",
                 "holt:haiku6", "holt:debian13+oneshot"]
        self.assertEqual(holt.needed_platforms(sf, cells), ["freebsd", "haiku", "illumos", "linux", "netbsd"])
        self.assertEqual(holt.holt_rel("linux"), "bko-holt")
        self.assertEqual(holt.holt_rel("freebsd"), "freebsd/bko-holt")

    def test_reserve(self):
        self.assertEqual(holt.reserve_gib([]), 3.0)
        self.assertEqual(holt.reserve_gib([2 << 20]), 2.5)       # 1.1 x 2 GiB + 0.25 = 2.45, up to 2.5
        self.assertEqual(holt.reserve_gib([1000, 2000]), 1.0)    # at least 1 GiB

    def test_build_info(self):
        code = "c" * 64
        infos = {"linux": {"sha256": "1" * 64, "rustic": None, "rustc": "rustc 1.94.1 (e408947bf 2026-03-25)",
                           "host": "chopin", "time": "2026-11-08T03:00:00Z"},
                 "freebsd": {"sha256": "2" * 64, "rustc": "rustc 1.91.0", "host": "cinqcent", "time": "2026-11-08T03:05:00Z"}}
        text = holt.build_info_text(code, "6fff54b" + "0" * 33, {}, infos, "t2h-20261108T0300-abcd")
        self.assertTrue(text.startswith(f"bko-holt code {code} (holt 6fff54b)"))
        sums = holt.sha256sums_text(infos)
        self.assertEqual(sums, "2" * 64 + " *freebsd/bko-holt\n" + "1" * 64 + " *bko-holt\n")
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        cd = os.path.join(d, code)
        os.makedirs(os.path.join(cd, "freebsd"))
        wr(os.path.join(cd, "BUILD-INFO"), text)
        wr(os.path.join(cd, "SHA256SUMS"), sums)
        self.assertEqual(rows_holt._holt_sums(cd), {"linux": "1" * 64, "freebsd": "2" * 64})
        if facts_host is not None and hasattr(facts_host, "build_info_sums"):
            self.assertEqual(facts_host.build_info_sums(cd), {"linux": "1" * 64, "freebsd": "2" * 64})


class SpecJson(unittest.TestCase):
    """testvm/test2/holt/spec.json against the drivers and classes.json."""

    @classmethod
    def setUpClass(cls):
        cls.src = keys.TreeSource(WT)
        cls.sf = keys.Specs(cls.src).get("holt")
        p = os.path.join(T2, "classes.json")
        cls.classes = jload(p) if os.path.exists(p) else None

    def test_entries_and_kinds(self):
        names = self.sf.names()
        self.assertEqual(len(names), 45)
        text = rd(os.path.join(WT, "testvm", "holttest1d", "ht1.sh"))
        m = re.search(r'case \$G in\n(.*?)\nesac', text, re.S)
        bsd = re.search(r"^\s*(\S+)\)\s*\n\s*exec .*ht1-bsd\.sh", m.group(1), re.M).group(1).split("|")
        off = re.search(r"^\s*(\S+)\)\s*\n\s*exec .*ht1-offline\.sh", m.group(1), re.M).group(1).split("|")
        import fnmatch
        for n in names:
            e = self.sf.entry(n)
            want = "bsd" if any(fnmatch.fnmatch(n, p) for p in bsd) else "offline" if any(
                fnmatch.fnmatch(n, p) for p in off) else "linux"
            self.assertEqual(e["kind"], want, n)
            self.assertTrue(e["driver"].endswith({"linux": "ht1.sh", "bsd": "ht1-bsd.sh", "offline": "ht1-offline.sh"}[want]))
            self.assertIn(e.get("plat"), ("linux", "freebsd", "netbsd", "openbsd", "dragonfly", "illumos", "haiku"), n)

    def test_paths_exist(self):
        for n in self.sf.names():
            e = self.sf.entry(n)
            for g in ("harness", "judge"):
                for p in e.get(g) or []:
                    self.assertTrue(os.path.isfile(os.path.join(WT, p)), f"{n}: {g} {p}")
            for p in list(e.get("per_guest") or {}):
                self.assertTrue(os.path.isfile(os.path.join(WT, p)), p)
        for p in self.sf.data["plumbing"]:
            if not p.startswith("shared:"):
                self.assertTrue(os.path.isfile(os.path.join(WT, p)), p)

    def test_classes_agree(self):
        if self.classes is None:
            self.skipTest("no classes.json")
        h = self.classes["holt"]
        fams = set(x for v in h["families"].values() for x in v) | set(h.get("no_family") or ())
        self.assertEqual(fams, set(self.sf.names()))
        uefi = {n for n in self.sf.names() if self.sf.entry(n).get("firmware") == "uefi"
                and self.sf.entry(n)["kind"] == "linux"}
        self.assertEqual(uefi, set(h.get("uefi") or ()))
        for var, gs in h["variants"].items():
            for g in gs:
                self.assertIn(var, self.sf.entry(g)["variants_run"], f"{g}+{var}")

    def test_every_cell_measures(self):
        facts = facts_for(self.sf.names() + ["freebsd15-ufs"])
        for n in self.sf.names():
            m = keys.measure(f"holt:{n}", self.src, facts, keys.Specs(self.src), {"holt_code": "c" * 64})
            self.assertEqual(m["unknown"], [], n)
            for g in ("bin", "harness", "judge", "guest", "plumbing", "soft"):
                self.assertTrue(m["inputs"].get(g), f"{n}: no {g}")
            self.assertEqual(m["inputs"]["bin"][f"{self.sf.entry(n)['plat']}/bko-holt"], "code:" + "c" * 64)


# --- U5.3: holt.run against fake hosts ---------------------------------------------------------------

class FakeStops:
    def __init__(self):
        self.stop_run = self.stop_family = self.stop_starts = False
        self.stops, self.fed = [], []

    def feed(self, res):
        self.fed.append(res)
        return "ok"

    def feed_host(self, host, swap_out_bytes=None, ours_bytes=None):
        return "ok"


class FakeWord:
    family, word, text, all = "holt", "HoltTest2", "HoltTest2 debian13 freebsd15", False


class FakeRS:
    def __init__(self, mac_dir, plan, stops=None):
        self.run, self.word, self.family = "t2h-20261108T0300-abcd", FakeWord(), "holt"
        self.mac_dir, self.host_root, self.hosts = mac_dir, "/bkoscratch/t2test", ["chopin", "cinqcent"]
        self.copy = {h: {"id": "6fff54b", "path": "/home/m/backotter-work/t2/copy/6fff54b", "commit": "6fff54b"}
                     for h in self.hosts}
        self.stops = stops or FakeStops()
        self.plan, self.cells_written, self.logged, self.made = plan, [], [], []

    def log(self, text):
        self.logged.append(text)

    def host_run_dir(self, host):
        return f"{self.host_root}/runs/{self.run}"

    def record_made(self, host, path, pid=None):
        self.made.append((host, path, pid))

    def replan(self, facts=None, ledger_rows=None):
        self.replans = getattr(self, "replans", 0) + 1
        nxt = getattr(self, "replan_to", None)
        if nxt is None:
            return self.plan, [], []
        was = {c.cell for c in self.plan.cells if c.status == "due"}
        now = {c.cell for c in nxt.cells if c.status == "due"}
        self.plan = nxt
        return nxt, sorted(now - was), sorted(was - now)

    def write_cells(self, plan, attempt=1, name=None, reruns=None):
        name = name or ("cells.json" if attempt == 1 else "cells-r2.json")
        self.cells_written.append((attempt, dict(reruns or {})))
        out = {}
        for h in self.hosts:
            doc = {"run": self.run, "word": self.word.text, "attempt": attempt, "copy": self.copy[h], "cells": {}}
            for pc in plan.cells:
                if pc.status != "due" or (pc.keys and h not in pc.keys):
                    continue
                if reruns is not None and pc.cell not in reruns:
                    continue
                rr = (reruns or {}).get(pc.cell) or {}
                doc["cells"][rows_holt.gv_of(pc.cell)] = {"cell": pc.cell, "rule": pc.rule, "reasons": pc.reasons,
                                                          "planned_key": pc.keys.get(h), "attempt": rr.get("attempt", attempt),
                                                          "rerun_of": rr.get("rerun_of"), "flake": rr.get("flake")}
            jdump(doc, os.path.join(self.mac_dir, f"{name[:-5]}-{h}.json"))
            out[h] = f"{self.host_run_dir(h)}/{name}"
        return out


class FakeRemote:
    """Two hosts' files and processes, as holt.run sees them.  Builds end on the next look;
    a queue runner runs one todo line per look (its outcome from OUTCOMES), then ends."""

    def __init__(self, outcomes):
        self.files = {h: {} for h in ("chopin", "cinqcent")}
        self.procs = {}          # (host, pid) -> {kind, ...}
        self.pid = 1000
        self.calls = []
        self.outcomes = outcomes   # {gv or gv@r2: (rc, verdict or None: no row)}
        self.ledger = {h: [] for h in self.files}
        self.appended = []
        self.written = {}        # (host, path) -> the first text written
        self.blips = {}          # (op, host) -> how many of the next calls fail as an ssh would
        self.valid = None        # the ledger rows' valid (None: not set)
        self.imgsums = []        # (host, paths) asked of t2-host imgsum
        self.from_k = []         # (host, args) of t2-host row holt runs

    def _new(self, host, info):
        self.pid += 1
        self.procs[(host, self.pid)] = dict(info, alive=True)
        return self.pid

    def sh(self, host, cmd, input=None, timeout=60):
        self.calls.append((host, "sh", cmd))
        return 0, "", ""

    def read(self, host, path):
        self.tick()
        return self.files[host].get(path)

    def write(self, host, path, text):
        self.calls.append((host, "write", path))
        self.written.setdefault((host, path), text)
        self.files[host][path] = text

    def exists(self, host, path):
        return path in self.files[host]

    def touch(self, host, path):
        self.calls.append((host, "touch", path))
        self.files[host][path] = ""

    def sha256(self, host, path):
        t = self.files[host].get(path)
        return hashlib.sha256(t.encode()).hexdigest() if t is not None else None

    def start(self, host, cmd, log, pidfile):
        self.calls.append((host, "start", cmd))
        m = re.search(r"build-holt\.sh --reserved (\S+) (\S+)$", cmd)
        if m:
            env = dict(re.findall(r"(\w+)=(\S+)", cmd))
            return self._new(host, {"kind": "build", "plat": m.group(2), "env": env})
        m = re.search(r"ht1-queue\.sh (\S+) 2600 3", cmd)
        if m:
            oe = re.search(r"ONLY_ELSEWHERE='([^']*)'", cmd)
            return self._new(host, {"kind": "queue", "qrun": m.group(1),
                                    "skip": set((oe.group(1) if oe else "").split()), "cmd": cmd})
        raise AssertionError(cmd)

    def blip(self, op, host):
        n = self.blips.get((op, host), 0)
        if n:
            self.blips[(op, host)] = n - 1
            return True
        return False

    def alive(self, host, pid, needle):
        if self.blip("alive", host):   # (an ssh that failed: nothing ran meanwhile)
            return None
        self.tick()
        return self.procs.get((host, pid), {}).get("alive", False)

    def glob_read(self, host, pattern):
        rx = re.compile("^" + re.escape(pattern).replace("\\*", "[^/]*") + "$")
        return {p: t for p, t in self.files[host].items() if rx.match(p)}

    def copy(self, src_host, src, dst_host, dst):
        self.calls.append((src_host, "copy", src, dst_host))
        self.files[dst_host][dst] = self.files[src_host][src]
        return True

    def order(self, host, script, lines):
        return sorted(lines, reverse=True)   # a size order of the fake's own

    def ledger_rows(self, host, ledger_, needle):
        if self.blip("ledger", host):
            return None
        return [r for r in self.ledger[host] if needle in json.dumps(r)]

    def row_from_k(self, host, tool, args, env):
        self.from_k.append((host, list(args)))
        a = dict(zip(args[::2], args[1::2]))
        if (a["--k"] + "/report.txt") not in self.files[host]:
            return None
        rid = f"{host}-20261108T041000Z-{len(self.from_k):08x}"
        att = 2 if a["--run"].endswith("-r2") else 1
        self.ledger[host].append({"id": rid, "origin": f"ht1:{a['--run']}:{a['--gv']}:{att}",
                                  "cell": rows_holt.cell_of(a["--gv"]), "verdict": "PASS", "class": "pass", "needs": {},
                                  "run": "t2h-20261108T0300-abcd", "gm": 4.0, "wall_s": 240})
        return [rid]

    def imgsum(self, host, tool, paths, env):
        self.imgsums.append((host, list(paths)))
        return {p: hashlib.sha256(p.encode()).hexdigest() for p in paths}

    def append_rows(self, host, tool, rows, env):
        self.appended.append((host, rows))
        for r in rows:
            r = dict(r, id=f"{host}-20261108T040000Z-{len(self.appended):08x}")
            self.ledger[host].append(r)
        return [f"{host}-20261108T040000Z-{len(self.appended):08x}"]

    def take_lines(self, host, todo, guests, why):
        lines = [x for x in (self.files[host].get(todo) or "").splitlines()]
        took = [x for x in lines if x.split("+")[0] in guests]
        self.files[host][todo] = "".join(x + "\n" for x in lines if x not in took)
        return took

    def pswpout(self, host):
        return 0

    def tag_bytes(self, host, prefix, since):
        return 0

    def tick(self):
        for (host, pid), p in list(self.procs.items()):
            if not p["alive"]:
                continue
            if p["kind"] == "build":
                env = p["env"]
                out = env["HOLT_OUT"]
                path = out + "/bko-holt"
                self.files[host][path] = f"bko-holt {p['plat']}"
                self.files[host][env["HOLT_LOGS"] + "/exit"] = "0\n"
                self.files[host][env["HOLT_INFO"]] = json.dumps(
                    {"platform": p["plat"], "sha256": hashlib.sha256(f"bko-holt {p['plat']}".encode()).hexdigest(),
                     "rustc": "rustc 1.94.1", "host": host, "time": "2026-11-08T03:01:00Z"})
                p["alive"] = False
            elif p["kind"] == "queue":
                q = holt.QUEUE + "/" + p["qrun"]
                qm = re.search(r" (\w+)\.local$", p["cmd"])
                qh = qm.group(1) if qm else host   # the todo is on the first host (QHOST)
                todo = (self.files[qh].get(q + "/todo") or "").splitlines()
                take = next((x for x in todo if x.split("+")[0] not in p["skip"]), None)
                if take is None:
                    p["alive"] = False
                    continue
                self.files[qh][q + "/todo"] = "".join(x + "\n" for x in todo if x != take)
                gv = take.replace("+", "-")
                att = 2 if p["qrun"].endswith("-r2") else 1
                rc, verdict = self.outcomes.get(gv + ("@r2" if att == 2 else ""), (0, "PASS"))
                d = self.files[host].get(q + "/done") or "end_utc\thost\tgv\trc\td_gib\tm_gib\tport\twait_s\trun_s\n"
                self.files[host][q + "/done"] = d + f"2026-11-08T04:00:00Z\t{host}\t{gv}\t{rc}\t3\t4\t2600\t15\t300\n"
                if verdict:
                    row = {"id": f"{host}-20261108T040000Z-{len(self.ledger[host]):08x}",
                           "origin": f"ht1:{p['qrun']}:{gv}:{att}", "cell": rows_holt.cell_of(gv),
                           "verdict": verdict, "class": "pass" if verdict == "PASS" else "fail",
                           "needs": {"stage:boot": "FAIL"} if verdict == "FAIL" else {},
                           "run": "t2h-20261108T0300-abcd", "gm": 5.0, "wall_s": 300}
                    if self.valid is not None:
                        row.update(valid=self.valid, note=None if self.valid else "not measured: guest/x:generation")
                    self.ledger[host].append(row)


class FakePlan:
    def __init__(self, cells):
        self.cells, self.set, self.notes = cells, {"holt_code": "c" * 64}, []


class U53HoltRun(unittest.TestCase):
    PC = U52Todo.PC

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2-p5-run-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        both = {"chopin": "1" * 64, "cinqcent": "1" * 64}
        self.cells = [self.PC("holt:debian13", "holt:code", keys_=both),
                      self.PC("holt:debian13+oneshot", "holt:code", keys_=both),
                      self.PC("holt:freebsd15", "holt:code", keys_=both),
                      self.PC("holt:openbsd8", "holt:code", keys_={"chopin": "2" * 64}),
                      self.PC("holt:redox", "canary:plumbing", keys_={"cinqcent": "3" * 64}),
                      self.PC("holt:mint22", "holt:code", keys_=both)]
        self.plan = FakePlan(self.cells)
        self.rs = FakeRS(self.tmp, self.plan)
        src = keys.TreeSource(WT)
        self.ctx = type("Ctx", (), {"run": self.rs, "specs": keys.Specs(src), "src": src, "head": "6fff54b" + "0" * 33,
                                    "classes": jload(os.path.join(T2, "classes.json")) if os.path.exists(
                                        os.path.join(T2, "classes.json")) else {},
                                    "copy_preview": {"commit": "6fff54b" + "0" * 33, "dirty": {}},
                                    "options": {"pull": lambda: []}, "facts": {}})()

    def go(self, outcomes, remote=None):
        self.remote = remote or FakeRemote(outcomes)
        return holt.run(self.ctx, self.plan, remote=self.remote, sleep=lambda s: None, poll=0, gate=self.gate)

    def gate(self, ctx):   # the fast holt gate: never a host's in a unit test
        self.gated = getattr(self, "gated", 0) + 1
        return None

    def test_flow(self):
        res = self.go({"mint22": (1, "FAIL"), "mint22@r2": (0, "FLAKY-FAIL"), "debian13-oneshot": (2, None)})
        r = self.remote
        starts = [(h, c) for h, op, *c in r.calls if op == "start"]
        builds = [(h, re.search(r"--reserved \S+ (\S+)", c[0]).group(1)) for h, c in starts if "--reserved" in c[0]]
        self.assertEqual(builds, [("chopin", "linux"), ("cinqcent", "freebsd"), ("chopin", "openbsd")])
        hb = "/bkoscratch/t2test/holt-bin/" + "c" * 64
        for h in ("chopin", "cinqcent"):
            for p in ("bko-holt", "freebsd/bko-holt", "openbsd/bko-holt"):
                self.assertIn(f"{hb}/{p}", r.files[h], (h, p))
            self.assertEqual(r.files[h][hb + "/SHA256SUMS"], r.files["chopin"][hb + "/SHA256SUMS"])
            self.assertTrue(r.files[h][hb + "/BUILD-INFO"].startswith("bko-holt code " + "c" * 64))
        # The todo: the canary first, then the rest in --order's order.
        todo = r.written[("chopin", holt.QUEUE + "/t2h-20261108T0300-abcd/todo")].split()
        self.assertEqual(todo, ["redox", "openbsd8", "mint22", "freebsd15", "debian13+oneshot", "debian13"])
        qs = [c[0] for h, c in starts if "ht1-queue.sh" in c[0]]
        self.assertEqual(len(qs), 4)   # attempt 1 and the rerun, on both hosts
        q1 = [c for c in qs if " t2h-20261108T0300-abcd 2600 3" in c]
        self.assertTrue(all(f"HT_HOLT_BIN={hb}" in c and "HT_RAM=1" in c and "T2_RUN=t2h-20261108T0300-abcd" in c
                            and "T2_CELLS=/bkoscratch/t2test/runs/t2h-20261108T0300-abcd/cells.json" in c for c in q1))
        self.assertIn("ONLY_ELSEWHERE=redox", q1[0])
        self.assertIn("ONLY_ELSEWHERE=openbsd8", q1[1])
        self.assertTrue(q1[1].endswith("chopin.local"))
        # The job that left no row got one (R7); the FAIL a rerun with cells-r2.json, the
        # HARNESS none (a rerun that passed would read as FLAKY-FAIL).
        bf = [row for _, rows in r.appended for row in rows]
        self.assertEqual([(x["origin"], x["verdict"]) for x in bf],
                         [("ht1:t2h-20261108T0300-abcd:debian13-oneshot:1", "HARNESS")])
        self.assertEqual([a for a, _ in self.rs.cells_written], [1, 2])
        reruns = self.rs.cells_written[1][1]
        self.assertEqual(set(reruns), {"holt:mint22"})
        self.assertTrue(reruns["holt:mint22"]["rerun_of"])
        self.assertTrue(any("not rerun (reruns are for FAILs): holt:debian13+oneshot HARNESS" in n
                            for n in res["notes"]), res["notes"])
        by = {c["cell"]: c for c in res["cells"]}
        self.assertEqual(by["holt:mint22"]["verdict"], "FLAKY-FAIL")
        self.assertEqual(by["holt:mint22"]["attempts"], 2)
        self.assertEqual(by["holt:debian13+oneshot"]["verdict"], "HARNESS")
        self.assertEqual(by["holt:debian13"]["verdict"], "PASS")
        self.assertEqual(res["exit"], 1)
        self.assertEqual(len(self.rs.stops.fed), 7)

    def test_all_pass_and_resume(self):
        res = self.go({})
        self.assertEqual(res["exit"], 0, res)
        self.assertTrue(all(c["verdict"] == "PASS" for c in res["cells"]))
        n_builds = len([1 for _, op, *c in self.remote.calls if op == "start" and "--reserved" in c[0]])
        # Again from holt.json: nothing is built or queued twice.
        res2 = holt.resume(self.ctx, self.plan, remote=self.remote, sleep=lambda s: None, poll=0, gate=self.gate)
        self.assertEqual(res2["exit"], 0)
        again = len([1 for _, op, *c in self.remote.calls if op == "start" and "--reserved" in c[0]])
        self.assertEqual(again, n_builds)
        self.assertEqual(len([1 for _, op, *c in self.remote.calls if op == "start" and "ht1-queue.sh" in c[0]]), 2)

    def test_stop_os(self):
        class Stops(FakeStops):
            def feed(self, res):
                self.fed.append(res)
                return "stop-os linux" if res["verdict"] == "FAIL" else "ok"
        self.rs.stops = Stops()
        res = self.go({"redox": (1, "FAIL")})
        self.assertTrue(self.rs.stops.fed)
        self.assertTrue(any("stop-os linux" in s for s in res["stops"]), res["stops"])
        skipped = [c["cell"] for c in res["cells"] if c["outcome"] == "skipped"]
        self.assertTrue(skipped and all(holt.plat_of(holt.spec_of(self.ctx), c) == "linux" for c in skipped), skipped)

    def test_one_host_ships_to_the_other(self):
        self.rs.hosts = ["chopin"]
        for pc in self.cells:
            pc.keys = {"chopin": "1" * 64}
        res = self.go({})
        self.assertEqual(res["exit"], 0, res)
        hb = "/bkoscratch/t2test/holt-bin/" + "c" * 64
        self.assertEqual(sorted(p for p in self.remote.files["cinqcent"] if p.startswith(hb)),
                         sorted(p for p in self.remote.files["chopin"] if p.startswith(hb)))
        self.assertEqual(self.remote.files["cinqcent"][hb + "/BUILD-INFO"], self.remote.files["chopin"][hb + "/BUILD-INFO"])
        self.assertFalse([1 for h, op, *c in self.remote.calls if op == "start" and h == "cinqcent"])

    def test_build_failure_starts_nothing(self):
        class Broken(FakeRemote):
            def tick(self):
                for (host, pid), p in list(self.procs.items()):
                    if p["alive"] and p["kind"] == "build":
                        self.files[host][p["env"]["HOLT_LOGS"] + "/exit"] = "1\n"
                        p["alive"] = False
        res = self.go({}, Broken({}))
        self.assertEqual(res["exit"], 1)
        self.assertTrue(res["stops"][0].startswith("stop-family: bko-holt"), res["stops"])
        self.assertFalse([1 for _, op, *c in self.remote.calls if op == "start" and "ht1-queue.sh" in c[0]])


    # (The reviews' cases: a run must never pass on cells that didn't run or rows that don't count.)
    def test_runners_that_run_nothing_fail_the_run(self):
        class Dead(FakeRemote):
            def start(self, host, cmd, log, pidfile):
                pid = FakeRemote.start(self, host, cmd, log, pidfile)
                if "ht1-queue.sh" in cmd:   # dies at its start (a die line of ht1-queue.sh's)
                    self.procs[(host, pid)]["alive"] = False
                    self.files[host][log] = "ht1-queue: no /home/m/backotter-work/bko-bin/SHA256SUMS: every job would refuse\n"
                return pid
        res = self.go({}, Dead({}))
        self.assertEqual(res["exit"], 1, res)
        self.assertEqual({c["outcome"] for c in res["cells"]}, {"not-started"})
        self.assertTrue(any(n.startswith("not passed: ") for n in res["notes"]), res["notes"])
        self.assertTrue(any("6 todo line(s) left" in n for n in res["notes"]), res["notes"])

    def test_a_runner_that_didnt_start_fails_the_run(self):
        class NoStart(FakeRemote):
            def start(self, host, cmd, log, pidfile):
                if "ht1-queue.sh" in cmd and host == "cinqcent":
                    self.calls.append((host, "start", cmd))
                    return None
                return FakeRemote.start(self, host, cmd, log, pidfile)
        res = self.go({}, NoStart({}))
        self.assertEqual(res["exit"], 1)
        self.assertTrue(any("the runner on cinqcent didn't start" in n for n in res["notes"]), res["notes"])

    def test_failed_ssh_doesnt_end_the_wait(self):
        class AliveTicks(FakeRemote):   # (jobs run only between the alive looks)
            def read(self, host, path):
                return self.files[host].get(path)
        r = AliveTicks({})
        r.blips = {("alive", "chopin"): 2, ("alive", "cinqcent"): 2}
        res = self.go({}, r)
        self.assertEqual(res["exit"], 0, res)
        self.assertTrue(all(c["verdict"] == "PASS" and c["outcome"] == "ran" for c in res["cells"]), res["cells"])

    def test_unreadable_ledger_writes_no_backfill(self):
        r = FakeRemote({})
        r.blips = {("ledger", "chopin"): 2, ("ledger", "cinqcent"): 2}
        res = self.go({}, r)
        self.assertEqual(r.appended, [])
        self.assertEqual(res["exit"], 0, res)
        self.assertEqual([f for f in self.rs.stops.fed if f["class"] != "pass"], [])

    def test_hook_that_left_no_row_gets_one_from_k(self):
        class K(FakeRemote):
            def tick(self):
                FakeRemote.tick(self)
                for h in self.files:   # mint22's K holds its records: the hook timed out
                    self.files[h]["/bkoscratch/holttest1d/t2h-20261108T0300-abcd/mint22/report.txt"] = "x"
        r = K({"mint22": (0, None)})
        res = self.go({}, r)
        self.assertEqual(r.appended, [])
        self.assertEqual([a[a.index("--gv") + 1] for _, a in r.from_k], ["mint22"])
        by = {c["cell"]: c for c in res["cells"]}
        self.assertEqual(by["holt:mint22"]["verdict"], "PASS")
        self.assertEqual(res["exit"], 0, res)

    def test_invalid_pass_rows_fail_the_run(self):
        r = FakeRemote({})
        r.valid = False
        res = self.go({}, r)
        self.assertEqual(res["exit"], 1)
        self.assertTrue(all(c["verdict"] == "PASS" and c["valid"] is False for c in res["cells"]))
        self.assertTrue(any("doesn't count (valid false" in n for n in res["notes"]), res["notes"])

    def test_no_rerun_after_stop_starts(self):
        class Stops(FakeStops):
            def feed_host(self, host, swap_out_bytes=None, ours_bytes=None):
                if not self.stop_starts:
                    self.stop_starts = True
                    return "stop-starts"
                return "ok"
        self.rs.stops = Stops()
        res = self.go({"mint22": (1, "FAIL")})
        starts = [c for _, op, *c in self.remote.calls if op == "start" and "ht1-queue.sh" in c[0]]
        self.assertFalse([c for c in starts if "-r2 2600" in c[0]], starts)
        self.assertTrue(any("no rerun of holt:mint22: stop-starts" in n for n in res["notes"]), res["notes"])
        self.assertEqual(res["exit"], 1)

    def test_no_rerun_of_a_stopped_os(self):
        class Stops(FakeStops):
            stopped_os = set()

            def feed(self, res):
                self.fed.append(res)
                if res["verdict"] == "FAIL" and "linux" not in self.stopped_os:
                    self.stopped_os.add("linux")
                    return "stop-os linux"
                return "ok"
        self.rs.stops = Stops()
        res = self.go({"debian13": (1, "FAIL")})
        self.assertFalse(self.rs.cells_written[1:], self.rs.cells_written)
        self.assertTrue(any("stopped by stop-os): holt:debian13" in n for n in res["notes"]), res["notes"])

    def test_offline_guests_are_an_os_of_their_own(self):
        sf = holt.spec_of(self.ctx)
        self.assertEqual([holt.os_of(sf, c) for c in ("holt:redox", "holt:sculpt2610", "holt:debian13",
                                                      "holt:debian13+oneshot", "holt:freebsd15")],
                         ["redox", "sculpt2610", "linux", "linux", "freebsd"])
        self.assertEqual(holt.plat_of(sf, "holt:redox"), "linux")   # (its bko-holt stays linux's)

    def test_replan_after_the_gate(self):
        # R15: the re-plan drops mint22 and adds netbsd11, a platform not built yet.
        both = {"chopin": "1" * 64, "cinqcent": "1" * 64}
        after = FakePlan([pc for pc in self.cells if pc.cell != "holt:mint22"]
                         + [self.PC("holt:netbsd11", "holt:plan-diff", keys_=both)])
        self.rs.replan_to = after
        res = self.go({})
        r = self.remote
        builds = [re.search(r"--reserved \S+ (\S+)", c[0]).group(1) for _, op, *c in r.calls
                  if op == "start" and "--reserved" in c[0]]
        self.assertEqual(builds, ["linux", "freebsd", "openbsd", "netbsd"])
        todo = r.written[("chopin", holt.QUEUE + "/t2h-20261108T0300-abcd/todo")].split()
        self.assertIn("netbsd11", todo)
        self.assertNotIn("mint22", todo)
        self.assertTrue(any(n == "due after the re-plan: holt:netbsd11" for n in res["notes"]), res["notes"])
        self.assertTrue(any(n == "dropped after the re-plan: holt:mint22" for n in res["notes"]), res["notes"])
        self.assertEqual(sorted(c["cell"] for c in res["cells"]), sorted(c.cell for c in after.cells))
        self.assertEqual(res["exit"], 0, res)

    def test_a_build_on_a_host_outside_hosts_is_copied_not_redone(self):
        hb = "/bkoscratch/t2test/holt-bin/" + "c" * 64
        r = FakeRemote({})
        old = "bko-holt linux, an earlier run's"
        info = {"platform": "linux", "sha256": hashlib.sha256(old.encode()).hexdigest(), "host": "chopin"}
        r.files["chopin"][hb + "/bko-holt"] = old
        r.files["chopin"][hb + "/build-linux.json"] = json.dumps(info, sort_keys=True) + "\n"
        self.rs.hosts = ["cinqcent"]
        self.cells[:] = [pc for pc in self.cells if pc.cell in ("holt:debian13", "holt:mint22")]
        for pc in self.cells:
            pc.keys = {"cinqcent": "1" * 64}
        res = self.go({}, r)
        self.assertEqual(res["exit"], 0, res)
        self.assertFalse([1 for _, op, *c in r.calls if op == "start" and "--reserved" in c[0]])
        self.assertEqual(r.files["chopin"][hb + "/bko-holt"], old)       # never written over
        self.assertEqual(r.files["cinqcent"][hb + "/bko-holt"], old)     # copied from there
        self.assertIn(info["sha256"] + " *bko-holt", r.files["cinqcent"][hb + "/SHA256SUMS"])

    def test_resume_before_cells_json_keeps_the_copy(self):
        class Slow(FakeRemote):
            builds_seen = 0

            def tick(self):
                if any(p["kind"] == "build" and p["alive"] for p in self.procs.values()):
                    self.builds_seen += 1
                    if self.builds_seen < 6:
                        return
                FakeRemote.tick(self)

        r = Slow({})
        copy = dict(self.rs.copy)
        clock = iter(range(0, 10 ** 9, 3600 * 5))   # the first wait for a build runs past BUILD_TIMEOUT_S
        res = holt.run(self.ctx, self.plan, remote=r, sleep=lambda s: None, poll=0, gate=self.gate,
                       clock=lambda: next(clock))
        self.assertEqual(res["exit"], 1)
        self.assertTrue(res["stops"][0].startswith("stop-family: bko-holt builds still running"), res["stops"])
        # run.py's resume: the copy from cells-HOST.json, which isn't written yet.
        self.rs.copy = {h: {} for h in self.rs.hosts}
        res = holt.resume(self.ctx, self.plan, remote=r, sleep=lambda s: None, poll=0, gate=self.gate)
        self.assertEqual(self.rs.copy, copy)
        self.assertEqual(res["exit"], 0, res)

    def test_image_sums_before_the_replan(self):
        disks = ["/home/m/backotter-work/vms/redox/redox.qcow2"]
        self.ctx.facts = {h: {"guests": {"redox": {"generation": None, "imgsum": None,
                                                   "disks": [{"path": disks[0], "size": 1, "mtime": 1}]},
                                         "openbsd8": {"generation": GEN, "imgsum": None, "disks": []}},
                              "rescuers": {"images": {"openbsd8-newduid.qcow2": {"sha256": None, "imgsum": None},
                                                      "rescuer.qcow2": {"sha256": RESCUER, "imgsum": None}}}}
                          for h in ("chopin", "cinqcent")}
        res = self.go({})
        r = self.remote
        self.assertEqual(sorted(r.imgsums), [("chopin", ["/bkoscratch/holttest1d/rescuer/openbsd8-newduid.qcow2"]),
                                             ("cinqcent", disks)])   # openbsd8 runs on chopin, redox on cinqcent
        f = self.ctx.facts
        self.assertEqual(f["cinqcent"]["guests"]["redox"]["imgsum"],
                         "img:" + hashlib.sha256(f"redox.qcow2\t{hashlib.sha256(disks[0].encode()).hexdigest()}\n"
                                                 .encode()).hexdigest())
        self.assertTrue(f["chopin"]["rescuers"]["images"]["openbsd8-newduid.qcow2"]["imgsum"].startswith("img:"))
        self.assertIsNone(f["chopin"]["guests"]["redox"]["imgsum"])   # (redox isn't chopin's)
        self.assertEqual(res["exit"], 0, res)

# --- ht1-lib.sh's ht_t2_row (bash 4 or newer) --------------------------------------------------------

def modern_bash():
    for b in ("bash", "/usr/local/bin/bash", "/opt/homebrew/bin/bash"):
        try:
            out = subprocess.run([b, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0 and out.stdout.strip().isdigit() and int(out.stdout.strip()) >= 4:
            if subprocess.run([b, "-c", "type timeout realpath"], capture_output=True).returncode == 0:
                return b
    return None


@unittest.skipUnless(modern_bash(), "needs bash 4 or newer with timeout and realpath (the test hosts')")
class HookShell(unittest.TestCase):
    def run_hook(self, tool, env=None, args="0 0 --k /x/K"):
        d = tempfile.mkdtemp(prefix="t2-p5-hook-")
        self.addCleanup(shutil.rmtree, d, True)
        t = os.path.join(d, "testvm")
        os.makedirs(os.path.join(t, "test2"))
        if tool:
            wr(os.path.join(t, "test2", "t2-host"), tool)
        lib = os.path.join(WT, "testvm", "holttest1d", "ht1-lib.sh")
        script = (f". {lib}; T={t} W={d}/work R=/x/R RUN=${{RUN:-r1}} GV=fake; ht_t2_row {args}; echo rc=$?")
        e = dict(os.environ, **(env or {}))
        p = subprocess.run([modern_bash(), "-c", script], capture_output=True, text=True, env=e, timeout=60)
        args_file = os.path.join(d, "args")
        return p, (rd(args_file) if os.path.exists(args_file) else None), d

    TOOL = ("import os, sys\nopen(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'args'), 'w')"
            ".write(' '.join(sys.argv[1:]) + ' T2_WORK=' + os.environ.get('T2_WORK', '') + '\\n')\n")

    def test_ok(self):
        p, args, d = self.run_hook(self.TOOL + "print('chopin-20261108T040000Z-0123abcd')\n")
        self.assertIn("rc=0", p.stdout)
        self.assertEqual(p.stderr.strip().splitlines()[-1].split(": ", 1)[1],
                         "note: Test2 ledger row chopin-20261108T040000Z-0123abcd")
        self.assertEqual(args.strip(), f"row holt --k /x/K --r /x/R --run r1 --gv fake --driver bash --rc 0 --code 0 "
                                       f"T2_WORK={d}/work")

    def test_failing_missing_and_off(self):
        p, _, _ = self.run_hook(self.TOOL + "sys.stderr.write('boom\\n'); sys.exit(3)\n")
        self.assertIn("rc=0", p.stdout)
        self.assertRegex(p.stderr, r"note: no Test2 ledger row \(\S+ exit 3: boom\)")
        p, _, _ = self.run_hook(None)
        self.assertIn("rc=0", p.stdout)
        self.assertRegex(p.stderr, r"note: no Test2 ledger row \(\S+/test2/t2-host isn't there\)")
        for env in ({"HT_CLEAN": "0"}, {"RUN": "debug"}):
            p, args, _ = self.run_hook(self.TOOL, env=env)
            self.assertEqual((p.stderr.strip(), args), ("", None), env)


if __name__ == "__main__":
    unittest.main()
