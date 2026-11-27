"""img-speed.md I12 (§8.3, D11, D17): t2lib/holtbuild.py, which platforms ship their cross-built
bko-holt, and the weekly VM reference's records (MAC_ROOT/holt-ref/PLAT.json).

No host.  Run from the worktree: python3 -m unittest discover testvm/test2/tests.
"""

import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
WT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))

from t2lib import holtbuild, keys  # noqa: E402

K = "cross:1.94.1:freebsd14@32199a051848"
NOW = datetime.datetime(2026, 11, 27, 12, 0, tzinfo=datetime.timezone.utc)


class Src:
    def __init__(self, files):
        self.f = files

    def read(self, path):
        t = self.f.get(path)
        return None if t is None else t.encode()


def builds(**cross):
    return Src({holtbuild.BUILDS: json.dumps({"schema": 1, "reference_days": 7, "cross": cross})})


class Kinds(unittest.TestCase):
    def setUp(self):
        self.mr = tempfile.mkdtemp(prefix="t2-holtbuild-")
        self.addCleanup(shutil.rmtree, self.mr, True)

    def kinds(self, src, env=None):
        return holtbuild.kinds(src, env=env or {}, mac_root=self.mr, now=NOW)

    def ref(self, plat, result, when="2026-11-27T08:00:00Z", kind=K, why=None):
        holtbuild.write_ref(self.mr, plat, {"when": when, "result": result, "kind": kind, "why": why})

    def test_no_file_and_no_cross_ship_vm_builds(self):
        self.assertEqual(self.kinds(Src({})), ({}, []))
        self.assertEqual(self.kinds(builds()), ({}, []))
        self.assertEqual(self.kinds(Src({holtbuild.BUILDS: "{not json"})), ({}, []))

    def test_a_switched_platform_with_a_passing_reference(self):
        self.ref("freebsd", "PASS")
        self.assertEqual(self.kinds(builds(freebsd={"kind": K})), ({"freebsd": K}, []))

    def test_reference_due_keeps_the_cross_build_with_a_note(self):
        k, notes = self.kinds(builds(freebsd={"kind": K}))
        self.assertEqual(k, {"freebsd": K})
        self.assertEqual(len(notes), 1)
        self.assertIn("VM reference due (no VM reference has passed)", notes[0])
        self.assertIn("holt-ref.py run freebsd", notes[0])
        self.ref("freebsd", "PASS", when="2026-11-19T08:00:00Z")   # 8 days ago
        self.assertIn("over 7 days ago", self.kinds(builds(freebsd={"kind": K}))[1][0])
        self.ref("freebsd", "PASS", kind="cross:1.94.1:freebsd14@000000000000")   # another sysroot
        self.assertIn("the pinned kind is " + K, self.kinds(builds(freebsd={"kind": K}))[1][0])

    def test_a_failed_reference_sends_it_back_to_the_vm_build(self):
        self.ref("freebsd", "PASS", when="2026-11-26T08:00:00Z")
        self.ref("freebsd", "FAIL", why="step 3: DIFF freebsd14 src")
        k, notes = self.kinds(builds(freebsd={"kind": K}, netbsd={"kind": "cross:1.94.1:netbsd11@bcec88bbe419"}))
        self.assertEqual(sorted(k), ["netbsd"])
        self.assertTrue(any("freebsd: its VM build" in n and "step 3: DIFF freebsd14 src" in n for n in notes), notes)
        d = holtbuild.read_ref(self.mr, "freebsd")
        self.assertEqual((d["last"]["result"], d["pass"]["result"]), ("FAIL", "PASS"))
        self.ref("freebsd", "PASS")   # a reference that passes again: back on the cross build
        self.assertIn("freebsd", self.kinds(builds(freebsd={"kind": K}))[0])

    def test_t2_holt_build_vm_and_what_cant_ship(self):
        self.ref("freebsd", "PASS")
        k, notes = self.kinds(builds(freebsd={"kind": K}), env={"T2_HOLT_BUILD": "vm"})
        self.assertEqual(k, {})
        self.assertIn("T2_HOLT_BUILD=vm", notes[0])
        k, notes = self.kinds(builds(haiku={"kind": "cross:x"}, netbsd={"kind": "vm"}, linux={"kind": K}))
        self.assertEqual(k, {})
        self.assertEqual(len(notes), 3)

    def test_kind_of(self):
        self.assertEqual(holtbuild.kind_of({"kind": "cross", "build": K, "sha256": "a"}), K)
        self.assertEqual(holtbuild.kind_of({"sha256": "a", "rustc": "rustc 1.100.0"}), "vm")
        self.assertEqual(holtbuild.kind_of(None), "vm")

    def test_the_repos_file(self):
        """testvm/test2/holt/builds.json parses, names only platforms that can ship a cross
        build, each with a holt-cross.sh kind and the two E2 rounds D11 asks for."""
        d = holtbuild.load(keys.TreeSource(WT))
        self.assertEqual(d.get("schema"), 1)
        for p, e in (d.get("cross") or {}).items():
            self.assertIn(p, holtbuild.CROSS_ABLE)
            self.assertRegex(e["kind"], r"^cross:[0-9.]+:[a-z0-9]+@[0-9a-f]{12}$")
            self.assertGreaterEqual(len(e.get("e2") or []), 2, p)
            self.assertEqual(len({r["code"] for r in e["e2"]}), len(e["e2"]), f"{p}: two rounds, two codes")


class HoltRefCli(unittest.TestCase):
    TOOL = os.path.join(WT, "testvm", "holttest1d", "holt-ref.py")

    def run_(self, *args, **env):
        e = dict(os.environ, T2_REF_MAC_ROOT=self.mr, **env)
        return subprocess.run([sys.executable, self.TOOL, *args], capture_output=True, text=True, env=e, timeout=60)

    def setUp(self):
        self.mr = tempfile.mkdtemp(prefix="t2-holtref-")
        self.addCleanup(shutil.rmtree, self.mr, True)

    def test_usage(self):
        for args in ((), ("run",), ("run", "haiku"), ("run", "--name", "a b", "freebsd"), ("run", "--bogus", "freebsd")):
            r = self.run_(*args)
            self.assertEqual(r.returncode, 2, (args, r.stderr))
            self.assertIn("holt-ref:", r.stderr)

    def test_due_reads_the_records(self):
        d = holtbuild.load(keys.TreeSource(WT))
        r = self.run_("due")
        if not d.get("cross"):
            self.assertEqual((r.returncode, r.stdout), (0, ""))
            return
        self.assertEqual(r.returncode, 1, r.stdout)   # no reference in an empty MAC_ROOT
        self.assertIn("due: no VM reference has passed", r.stdout)
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for p, e in d["cross"].items():
            holtbuild.write_ref(self.mr, p, {"when": now, "result": "PASS", "kind": e["kind"]})
        r = self.run_("due")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("due:", r.stdout)


if __name__ == "__main__":
    unittest.main()
