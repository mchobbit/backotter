"""Unit tests for bs2-host's tools and preparations on /bkoscratch (2026-11-25: a
BackShot2All start filled cinqcent's): tools only where a source's tier 0 runs (--tools,
--drop-tools, tier0_cases() copying them where it runs, never running without them), the
leaner copy (tools_rsync_args(), ostree_deployment()), and older preparations pruned once
nothing uses them (prune_preps(), prep_refs(), qcow2_backing()).  Nothing here reaches a
host; the rsync test runs where rsync is GNU rsync 3:

    python3 -m unittest discover -s testvm/backshot2/tests
"""

import contextlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

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


class HostTools(unittest.TestCase):
    def test_rsync_args(self):
        a = H.tools_rsync_args("/w/mnt/ostree/deploy/x.0", "/s/tools")
        self.assertEqual(a[:2], ["rsync", "-aH"])
        self.assertEqual(a[-2:], ["/w/mnt/ostree/deploy/x.0/", "/s/tools/"])
        inc = [i for i, x in enumerate(a) if x.startswith("--include=/usr/share/")]
        exc = a.index("--exclude=/usr/share/*")
        self.assertTrue(inc and max(inc) < exc, "the kept /usr/share dirs before the rest of it is left out")
        for x in ("/boot", "/usr/lib/modules", "/usr/lib/firmware", "/var/lib/flatpak", "/.snapshots", "/part-future",
                  "/swapfile", "/home", "/root", "/srv"):
            self.assertIn(f"--exclude={x}", a)
        for x in ("/gnu", "/nix", "/usr/lib", "/usr/bin", "/etc", "/var/guix"):
            self.assertFalse([w for w in a if w in (f"--exclude={x}", f"--exclude={x}/")], x)
        self.assertIn("--include=/usr/share/xfsprogs/***", a)

    @unittest.skipUnless(shutil.which("rsync") and "rsync  version 3" in
                         subprocess.run(["rsync", "--version"], capture_output=True, text=True).stdout, "GNU rsync 3")
    def test_rsync_keeps_what_the_dry_runs_need(self):
        with tempfile.TemporaryDirectory() as t:
            src, dst = f"{t}/src", f"{t}/dst"
            for p in ("usr/sbin/mke2fs", "etc/mke2fs.conf", "usr/share/xfsprogs/mkfs/lts.conf", "usr/share/icons/x.png",
                      "usr/lib/modules/7/k.ko", "usr/lib/x86_64-linux-gnu/libext2fs.so", "gnu/store/abc/bin/sfdisk",
                      "var/lib/flatpak/x", "var/lib/dpkg/status", ".snapshots/1/snapshot/x", "swapfile"):
                os.makedirs(os.path.dirname(f"{src}/{p}"), exist_ok=True)
                with open(f"{src}/{p}", "w") as f:
                    f.write(p)
            os.link(f"{src}/usr/sbin/mke2fs", f"{src}/usr/sbin/mkfs.ext4")
            subprocess.run(H.tools_rsync_args(src, dst), check=True)
            got = {os.path.relpath(os.path.join(dp, f), dst) for dp, _, fs in os.walk(dst) for f in fs}
            self.assertEqual(got, {"usr/sbin/mke2fs", "usr/sbin/mkfs.ext4", "etc/mke2fs.conf", "usr/share/xfsprogs/mkfs/lts.conf",
                                   "usr/lib/x86_64-linux-gnu/libext2fs.so", "gnu/store/abc/bin/sfdisk", "var/lib/dpkg/status"})
            self.assertEqual(os.stat(f"{dst}/usr/sbin/mke2fs").st_ino, os.stat(f"{dst}/usr/sbin/mkfs.ext4").st_ino)

    def test_ostree_deployment(self):
        out = ("Mounted kinoite44.qcow2 (/dev/nbd1, read-only) at /w/mnt:\n"
               "ostree: the default deployment is ostree/deploy/default/deploy/10e6a3.1 (its etc/fstab mounted the rest)\n")
        self.assertEqual(H.ostree_deployment(out), "ostree/deploy/default/deploy/10e6a3.1")
        self.assertEqual(H.ostree_deployment("Mounted x at /w/mnt:\n"), "")
        self.assertEqual(H.ostree_deployment("ostree: the default deployment is ../../etc (x)\n"), "")
        self.assertEqual(H.ostree_deployment("ostree: the default deployment is /etc (x)\n"), "")

    def test_tools_fresh(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(H, "ROOT", root), \
                mock.patch.object(H, "generation", lambda s: "gen:aa"):
            d = H.source_dir("debian12")
            os.makedirs(f"{d}/tools/usr/bin")
            with open(f"{d}/tools/usr/bin/env", "w"):
                pass
            self.assertFalse(H.tools_fresh("debian12"), "a copy with no tools.json (version 1)")
            for meta, fresh in (({"gen": "gen:aa", "version": H.TOOLS_VERSION}, True),
                                ({"gen": "gen:bb", "version": H.TOOLS_VERSION}, False),
                                ({"gen": "gen:aa", "version": 1}, False)):
                with open(f"{d}/tools.json", "w") as f:
                    json.dump(meta, f)
                self.assertEqual(H.tools_fresh("debian12"), fresh, meta)
            note = H.drop_tools(d, "test")
            self.assertIn("its tools removed", note)
            self.assertFalse(os.path.exists(f"{d}/tools") or os.path.exists(f"{d}/tools.json"))
            self.assertEqual(H.drop_tools(d, "test"), "")

    def test_no_env_is_never_fresh(self):
        # (uc24's copy before snapcore_base(): its four partitions, no /usr; every dry run refused.)
        with tempfile.TemporaryDirectory() as root, mock.patch.object(H, "ROOT", root), \
                mock.patch.object(H, "generation", lambda s: "gen:aa"):
            d = H.source_dir("uc24")
            os.makedirs(f"{d}/tools/run/mnt/data")
            with open(f"{d}/tools.json", "w") as f:
                json.dump({"gen": "gen:aa", "version": H.TOOLS_VERSION}, f)
            self.assertFalse(H.tools_fresh("uc24"))
            os.makedirs(f"{d}/tools/usr/bin")
            os.symlink("/gnu/store/x-coreutils/bin/env", f"{d}/tools/usr/bin/env")   # (Guix's: absolute, inside)
            self.assertFalse(H.tools_fresh("uc24"), "a link to nothing in the copy")
            os.makedirs(f"{d}/tools/gnu/store/x-coreutils/bin")
            with open(f"{d}/tools/gnu/store/x-coreutils/bin/env", "w"):
                pass
            self.assertTrue(H.tools_fresh("uc24"))

    def snapcore_tree(self, t, base="core24_2307.snap", where=("data",)):
        mp = f"{t}/mnt"
        os.makedirs(f"{mp}/{H.SNAPCORE_DATA}/snaps")
        os.makedirs(f"{mp}/{H.SNAPCORE_SEED}/snaps")
        with open(f"{mp}/{H.SNAPCORE_DATA}/modeenv", "w") as f:
            f.write(f"mode=run\ncurrent_recovery_systems=20240603\n{'base=' + base if base is not None else ''}\ngadget=pc_196.snap\n")
        at = {"data": f"{mp}/{H.SNAPCORE_DATA}/snaps", "seed": f"{mp}/{H.SNAPCORE_SEED}/snaps"}
        for w in where:
            with open(f"{at[w]}/{base or 'core24_1.snap'}", "w") as f:
                f.write("hsqs")
        return mp

    def test_snapcore_base(self):
        with tempfile.TemporaryDirectory() as t:
            mp = self.snapcore_tree(t)
            self.assertEqual(H.snapcore_base(mp), (f"{mp}/{H.SNAPCORE_DATA}/snaps/core24_2307.snap", None))
        with tempfile.TemporaryDirectory() as t:   # not refreshed since the install: the seed's
            mp = self.snapcore_tree(t, where=("seed",))
            self.assertEqual(H.snapcore_base(mp), (f"{mp}/{H.SNAPCORE_SEED}/snaps/core24_2307.snap", None))
        with tempfile.TemporaryDirectory() as t:
            mp = self.snapcore_tree(t, where=())
            snap, why = H.snapcore_base(mp)
            self.assertIsNone(snap)
            self.assertIn("core24_2307.snap", why)
        for bad in ("../../../etc/passwd", "core24", ""):
            with tempfile.TemporaryDirectory() as t:
                mp = self.snapcore_tree(t, base=bad, where=())
                snap, why = H.snapcore_base(mp)
                self.assertIsNone(snap)
                self.assertTrue(why, bad)
        with tempfile.TemporaryDirectory() as t:   # another source: a root of its own, or no modeenv
            os.makedirs(f"{t}/usr/bin")
            self.assertEqual(H.snapcore_base(t), (None, None))
            mp = self.snapcore_tree(t)
            os.makedirs(f"{mp}/usr")
            self.assertEqual(H.snapcore_base(mp), (None, None))

    def test_drop_waits_for_no_tier0(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(f"{d}/tools/usr")
            held = H.tools_lock(d, False)   # a tier 0 in them
            try:
                self.assertIn("kept", H.drop_tools(d, "test"))
                self.assertTrue(os.path.isdir(f"{d}/tools/usr"))
            finally:
                held.close()


class Tier0Tools(unittest.TestCase):
    """tier0_cases(): dry runs as root in tools copied where it runs; never silently none."""

    def run_t0(self, euid, src="debian12", fresh=True):
        seen = {}

        def t0run(dump, cases, w, planner, tools, caps):
            seen["tools"] = tools
            return [], {}
        dump = H.dumpx.load(os.path.join(H.TESTVM, "..", "holt", "tests", "corpus", src, "src.json"))
        with tempfile.TemporaryDirectory() as root, mock.patch.object(H, "ROOT", root), mock.patch.object(H, "RAM", root), \
                mock.patch.object(H.os, "geteuid", lambda: euid), mock.patch.object(H.T0, "run", t0run), \
                mock.patch.object(H, "planner", lambda: "/bin/true"), mock.patch.object(H.L, "write_rows", lambda *a, **k: None), \
                mock.patch.object(H, "ensure_tools", lambda s: seen.setdefault("ensured", s)), \
                mock.patch.object(H, "tools_fresh", lambda s, g=None: fresh), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            os.makedirs(H.source_dir(src), exist_ok=True)
            if fresh:
                H.tier0_cases(src, "gen:aa", dump, [], "r1")
            else:
                with self.assertRaises(SystemExit):
                    H.tier0_cases(src, "gen:aa", dump, [], "r1")
            seen["err"] = err.getvalue()
            seen["d"] = H.source_dir(src)
        return seen

    def test_root_gets_its_tools_here(self):
        s = self.run_t0(0)
        self.assertEqual(s["ensured"], "debian12")
        self.assertEqual(s["tools"], f"{s['d']}/tools")

    def test_root_dies_when_they_cant_be_had(self):
        s = self.run_t0(0, fresh=False)
        self.assertIn("aren't its generation", s["err"])

    def test_as_m_says_so(self):
        s = self.run_t0(1000)
        self.assertIsNone(s["tools"])
        self.assertNotIn("ensured", s)
        self.assertIn("no dry runs", s["err"])

    def test_a_plan_family_needs_none(self):
        s = self.run_t0(0, "nixos2605")
        self.assertIsNone(s["tools"])
        self.assertNotIn("ensured", s)


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

    def test_room_says_which_preparations_are_in_use(self):
        """bs2-host room: the preparations live work refers to (referenced), the current one's
        among them (prep_referenced), and old_bytes only the unused older ones'."""
        refs = {os.path.realpath(f"{self.d}/preps/p-2/src-0.qcow2"), os.path.realpath(f"{self.d}/preps/p-4") + "/backup/x"}
        cur = os.path.realpath(f"{self.d}/preps/p-4")
        out = io.StringIO()
        with mock.patch.object(H, "ROOT", self.tmp), mock.patch.object(H, "source_dir", lambda s: self.d), \
                mock.patch.object(H, "prep_refs", lambda: refs), mock.patch.object(H, "generation", lambda s: "g1"), \
                mock.patch.object(H, "prep_reasons", lambda s, h: (cur, {}, ["stale"])), \
                mock.patch.object(H, "source_ready", lambda s: "g1"), mock.patch.object(H, "tools_fresh", lambda s, g: True), \
                mock.patch.object(H, "du_bytes", lambda p: 1 << 30), contextlib.redirect_stdout(out):
            H.cmd_room(mock.Mock(sources=["guix15"]))
        e = json.loads(out.getvalue())["sources"]["guix15"]
        self.assertEqual(e["referenced"], ["p-2", "p-4"])
        self.assertTrue(e["prep_referenced"])
        self.assertFalse(e["prep_ok"])
        self.assertEqual(e["old_bytes"], 2 << 30, "p-1 and p-3: p-2 is in use, p-4 the current")
        refs.clear()
        out = io.StringIO()
        with mock.patch.object(H, "ROOT", self.tmp), mock.patch.object(H, "source_dir", lambda s: self.d), \
                mock.patch.object(H, "prep_refs", lambda: refs), mock.patch.object(H, "generation", lambda s: "g1"), \
                mock.patch.object(H, "prep_reasons", lambda s, h: (cur, {}, ["stale"])), \
                mock.patch.object(H, "source_ready", lambda s: "g1"), mock.patch.object(H, "tools_fresh", lambda s, g: True), \
                mock.patch.object(H, "du_bytes", lambda p: 1 << 30), contextlib.redirect_stdout(out):
            H.cmd_room(mock.Mock(sources=["guix15"]))
        e = json.loads(out.getvalue())["sources"]["guix15"]
        self.assertEqual((e["referenced"], e["prep_referenced"], e["old_bytes"]), ([], False, 3 << 30))

    def test_prune_with_no_current(self):
        os.remove(f"{self.d}/prep")
        got = H.prune_preps(self.d, set())
        self.assertEqual(len(got), 4, "a source with no current preparation keeps none of the old ones")


if __name__ == "__main__":
    unittest.main()
