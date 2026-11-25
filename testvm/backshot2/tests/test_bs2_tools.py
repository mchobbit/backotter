"""Unit tests for bs2-host's preparations on /bkoscratch (2026-11-25: a BackShot2All
start filled cinqcent's): older preparations pruned once nothing uses them (prune_preps(),
prep_refs(), qcow2_backing()).  Nothing here reaches a host:

    python3 -m unittest discover -s testvm/backshot2/tests
"""

import os
import shutil
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_bs2_hosts import load_host  # noqa: E402

H = load_host()


def qcow2(path, backing=None):
    """A qcow2 header (version 3) naming BACKING, enough for qcow2_backing()."""
    b = (backing or "").encode()
    off = 104 if b else 0
    hdr = b"QFI\xfb" + struct.pack(">I", 3) + struct.pack(">QI", off, len(b))
    with open(path, "wb") as f:
        f.write(hdr.ljust(104, b"\0") + b)


class Prune(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.d = f"{self.tmp}/sources/guix15"
        for n in ("p-1", "p-2", "p-3", "p-4"):
            os.makedirs(f"{self.d}/preps/{n}/dumps")
            qcow2(f"{self.d}/preps/{n}/src-0.qcow2")
            with open(f"{self.d}/preps/{n}.log", "w") as f:
                f.write("log")
        os.symlink("preps/p-4", f"{self.d}/prep")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_qcow2_backing(self):
        p = f"{self.tmp}/o.qcow2"
        qcow2(p, f"{self.d}/preps/p-2/src-0.qcow2")
        self.assertEqual(H.qcow2_backing(p), f"{self.d}/preps/p-2/src-0.qcow2")
        self.assertIsNone(H.qcow2_backing(f"{self.d}/preps/p-1/src-0.qcow2"))   # no backing
        with open(f"{self.tmp}/x.qcow2", "w") as f:
            f.write("not a qcow2")
        self.assertIsNone(H.qcow2_backing(f"{self.tmp}/x.qcow2"))

    def test_refs_from_overlays_and_processes(self):
        work = f"{self.tmp}/ram/case1"
        os.makedirs(work)
        qcow2(f"{work}/src-0.qcow2", f"{self.d}/preps/p-2/src-0.qcow2")   # a case's overlay on p-2
        proc = f"{self.tmp}/proc"
        os.makedirs(f"{proc}/123/fd")
        os.symlink(f"{self.d}/preps/p-3/backup/repo/config", f"{proc}/123/fd/5")   # a cp reading p-3's backup
        os.symlink("/", f"{proc}/123/cwd")
        refs = H.prep_refs([f"{self.tmp}/ram"], proc=proc)
        self.assertIn(os.path.realpath(f"{self.d}/preps/p-2/src-0.qcow2"), refs)
        self.assertIn(f"{self.d}/preps/p-3/backup/repo/config", refs)

    def test_prune_keeps_the_current_and_the_used(self):
        refs = {os.path.realpath(f"{self.d}/preps/p-2/src-0.qcow2")}
        got = H.prune_preps(self.d, refs)
        self.assertEqual([n for n, _ in got], ["p-1", "p-3"])
        left = sorted(os.listdir(f"{self.d}/preps"))
        self.assertEqual(left, ["p-2", "p-2.log", "p-4", "p-4.log"])
        # Once the case on p-2 is done, the next preparing removes it.
        self.assertEqual([n for n, _ in H.prune_preps(self.d, set())], ["p-2"])
        self.assertEqual(sorted(os.listdir(f"{self.d}/preps")), ["p-4", "p-4.log"])

    def test_prune_with_no_current(self):
        os.remove(f"{self.d}/prep")
        got = H.prune_preps(self.d, set())
        self.assertEqual(len(got), 4, "a source with no current preparation keeps none of the old ones")


if __name__ == "__main__":
    unittest.main()
