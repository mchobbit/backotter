"""Bazzite B8: BackShot1's bs1-ostree (the preflight's refusals, the
deployments record's lines, the link counts, the rollback boot's check) on
fake inputs.  No VM, no root, no ostree."""

import importlib.machinery
import importlib.util
import json
import os
import shutil
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
TESTVM = TESTS.parent.parent

CSUM = "10e6a37f" * 8
DEP0 = f"ostree/deploy/default/deploy/{CSUM}.0"
DEP1 = f"ostree/deploy/default/deploy/{CSUM}.1"


def load_tool():
    loader = importlib.machinery.SourceFileLoader("bs1_ostree_under_test",
                                                  str(TESTVM / "realworld" / "backshot1" / "bs1-ostree"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


T = load_tool()


def dep(serial, booted=False, staged=False, pinned=False):
    return {"checksum": CSUM, "serial": serial, "booted": booted, "staged": staged, "pinned": pinned,
            "container-image-reference": "ostree-unverified-registry:quay.io/fedora/fedora-kinoite:44",
            "container-image-reference-digest": "sha256:c5e9"}


SOURCES = [
    {"path": "/", "from": "/sysroot", "device": "vda3", "fstype": "btrfs", "source": "/dev/vda3", "subvol": "/root"},
    {"path": "/boot", "from": "/boot", "device": "vda2", "fstype": "ext4", "source": "/dev/vda2", "subvol": None},
    {"path": "/home", "from": "/var/home", "device": "vda3", "fstype": "btrfs", "source": "/dev/vda3", "subvol": "/home"},
    {"path": "/var", "from": "/var", "device": "vda3", "fstype": "btrfs", "source": "/dev/vda3", "subvol": "/var"},
]
LISTING = "ID 256 gen 113 top level 5 path <FS_TREE>/var\nID 257 gen 88 top level 5 path <FS_TREE>/home\nID 258 gen 113 top level 5 path <FS_TREE>/root\n"


class TestPreflight(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t)
        self.layout = self.t / "layout.json"
        self.layout.write_text(json.dumps({"storage": {"backup_sources": SOURCES}}))

    def problems(self, deps, transaction=None, listing=LISTING):
        return T.preflight(str(self.layout), {"transaction": transaction, "deployments": deps}, {"/sysroot": listing})

    def test_clean(self):
        self.assertEqual(self.problems([dep(1, booted=True), dep(0)]), [])

    def test_staged(self):
        p = self.problems([dep(2, staged=True), dep(1, booted=True), dep(0)])
        self.assertEqual(len(p), 2, p)
        self.assertIn(f"deployment {CSUM}.2 is staged", p[0])
        self.assertIn(f"{CSUM}.2 is pending: the next boot starts it, not the booted {CSUM}.1", p[1])

    def test_pending(self):
        p = self.problems([dep(2), dep(1, booted=True)])
        self.assertEqual(len(p), 1)
        self.assertIn("is pending", p[0])

    def test_transaction(self):
        p = self.problems([dep(1, booted=True)], transaction=["upgrade", "rpm-ostree upgrade", "/org/x"])
        self.assertIn("an rpm-ostree transaction is running", p[0])

    def test_unmounted_subvolume(self):
        p = self.problems([dep(1, booted=True)], listing=LISTING + "ID 260 gen 9 top level 5 path <FS_TREE>/snapshots\n")
        self.assertEqual(p, ["btrfs subvolume snapshots on vda3 isn't mounted, so it wouldn't be backed up"])

    def test_nested_subvolume_is_in_the_backup(self):
        p = self.problems([dep(1, booted=True)], listing=LISTING + "ID 261 gen 9 top level 256 path <FS_TREE>/var/lib/machines\n")
        self.assertEqual(p, [])

    def test_nested_subvolume_under_another_mount_is_refused(self):
        # root/var/x would be at /var/x, where subvol var is mounted: not in the tree.
        p = self.problems([dep(1, booted=True)], listing=LISTING + "ID 262 gen 9 top level 258 path <FS_TREE>/root/var/x\n")
        self.assertEqual(p, ["btrfs subvolume root/var/x on vda3 is where another mount covers it, so it wouldn't be backed up"])
        # root/etc/x is at /etc/x, which no mount covers.
        p = self.problems([dep(1, booted=True)], listing=LISTING + "ID 262 gen 9 top level 258 path <FS_TREE>/root/etc/x\n")
        self.assertEqual(p, [])

    def test_listing_fails(self):
        p = self.problems([dep(1, booted=True)], listing=None)
        self.assertEqual(p, ["btrfs subvolume list -a /sysroot failed"])

    def test_top_level_mounted_holds_all(self):
        top = [dict(SOURCES[0], subvol="/")]
        self.layout.write_text(json.dumps({"storage": {"backup_sources": top}}))
        p = self.problems([dep(1, booted=True)], listing=LISTING + "ID 260 gen 9 top level 5 path <FS_TREE>/snapshots\n")
        self.assertEqual(p, [])


class TestPreflightMain(unittest.TestCase):
    """A preflight that breaks is a failure (exit 2, with the reason), never
    an empty list of problems: bs1-backup reads the exit status too."""

    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t)
        self.layout = self.t / "layout.json"
        self.layout.write_text(json.dumps({"storage": {"backup_sources": SOURCES}}))

    def run_main(self, rpm_status, listing=LISTING):
        def fake_out(cmd):
            return rpm_status if cmd[0] == "rpm-ostree" else listing
        buf = io.StringIO()
        with mock.patch.object(T, "out", fake_out), contextlib.redirect_stdout(buf):
            rc = T.main(["preflight", str(self.layout)])
        return rc, buf.getvalue()

    def test_clean(self):
        self.assertEqual(self.run_main(json.dumps({"deployments": [dep(1, booted=True)]})), (0, ""))

    def test_refusal(self):
        rc, text = self.run_main(json.dumps({"deployments": [dep(2, staged=True), dep(1, booted=True)]}))
        self.assertEqual(rc, 1)
        self.assertIn("is staged", text)

    def test_malformed_status(self):
        rc, text = self.run_main("{not json")
        self.assertEqual(rc, 2)
        self.assertIn("couldn't tell: JSONDecodeError", text)

    def test_status_not_an_object(self):
        rc, text = self.run_main("[]")
        self.assertEqual(rc, 2)
        self.assertIn("couldn't tell", text)

    def test_layout_without_storage(self):
        self.layout.write_text("{}")
        rc, text = self.run_main(json.dumps({"deployments": [dep(1, booted=True)]}))
        self.assertEqual(rc, 2)
        self.assertIn("couldn't tell: KeyError", text)

    def test_no_layout(self):
        self.layout.unlink()
        rc, text = self.run_main(json.dumps({"deployments": [dep(1, booted=True)]}))
        self.assertEqual((rc, "FileNotFoundError" in text), (2, True))


class TestRpmOstree(unittest.TestCase):
    def test_lines_leave_booted_out(self):
        a = T.rpm_ostree_lines({"deployments": [dep(1, booted=True), dep(0)]})
        b = T.rpm_ostree_lines({"deployments": [dep(1), dep(0, booted=True)]})
        self.assertEqual(a, b)
        self.assertEqual(a[0], f"rpm-ostree {CSUM}.1 staged=no pinned=no image=ostree-unverified-registry:quay.io/fedora/fedora-kinoite:44 digest=sha256:c5e9")


class TestLinks(unittest.TestCase):
    def test_histogram(self):
        t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, t)
        (t / "ostree" / "repo").mkdir(parents=True)
        (t / "a").write_text("x")
        os.link(t / "a", t / "ostree" / "repo" / "a1")
        os.link(t / "a", t / "ostree" / "repo" / "a2")
        (t / "b").write_text("y")
        (t / "c").symlink_to("b")
        self.assertEqual(T.links(str(t)), [". multi-link-inodes 1", ". nlink 3 files 3"])

    def test_nested_subvolume_has_its_own_label(self):
        # A subvolume nested in a source (var/lib/machines, a fake st_dev
        # here) is counted on its own: load makes it a subvolume again from
        # the dump's list, so the restored tree has the same label.
        t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, t)
        (t / "var" / "lib" / "machines").mkdir(parents=True)
        (t / "var" / "a").write_text("x")
        os.link(t / "var" / "a", t / "var" / "a1")
        (t / "var" / "lib" / "machines" / "m").write_text("y")
        os.link(t / "var" / "lib" / "machines" / "m", t / "var" / "lib" / "machines" / "m1")
        nested = str(t / "var" / "lib" / "machines")
        real = os.lstat

        def fake(p):
            r = real(p)
            if str(p) == nested or str(p).startswith(nested + "/"):
                f = list(r)
                f[2] = r.st_dev + 1  # st_dev
                return os.stat_result(f)
            return r
        with mock.patch.object(T.os, "lstat", fake):
            self.assertEqual(T.links(str(t)), [". multi-link-inodes 1", ". nlink 2 files 2",
                                               "var/lib/machines multi-link-inodes 1", "var/lib/machines nlink 2 files 2"])

    def test_kargs(self):
        self.assertEqual(T.kargs("BOOT_IMAGE=(hd1,gpt2)/ostree/x/vmlinuz ostree=/o rw\n"), "ostree=/o rw")


BASELINE = f"""booted {DEP1}
default {DEP1}
deployment {DEP0} hostname=kinoite44
deployment {DEP1} hostname=kinoite44
entry ostree-1.conf {DEP0} options=ostree=/ostree/boot.1/default/b/1 root=UUID=x rw
entry ostree-2.conf {DEP1} options=ostree=/ostree/boot.1/default/b/0 root=UUID=x rw bko.rollback=1
selinux Enforcing
rpm-ostree {CSUM}.1 staged=no pinned=no image=i digest=d
rpm-ostree {CSUM}.0 staged=no pinned=no image=i digest=d
"""
RPM = [f"{CSUM}.1 staged=no pinned=no image=i digest=d", f"{CSUM}.0 staged=no pinned=no image=i digest=d"]


class TestBootCheck(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t)
        self.base = self.t / "deployments.txt"
        self.base.write_text(BASELINE)

    def check(self, booted, cmdline, **kw):
        args = dict(hostname="kinoite44", selinux="Enforcing", rpm=RPM, root_fs=["overlay", "composefs"])
        args.update(kw)
        return T.boot_check(str(self.base), booted, "BOOT_IMAGE=(hd0,gpt2)/ostree/x/vmlinuz " + cmdline, **args)

    def test_rollback(self):
        kind, lines, ok = self.check(DEP0, "ostree=/ostree/boot.1/default/b/1 root=UUID=x rw")
        self.assertEqual(kind, "rollback")
        self.assertTrue(ok, lines)

    def test_default(self):
        kind, _, ok = self.check(DEP1, "ostree=/ostree/boot.1/default/b/0 root=UUID=x rw bko.rollback=1")
        self.assertEqual((kind, ok), ("default", True))

    def test_wrong_kargs(self):
        # The rollback booted with the default's kargs: not what its entry says.
        kind, lines, ok = self.check(DEP0, "ostree=/ostree/boot.1/default/b/1 root=UUID=x rw bko.rollback=1")
        self.assertEqual(kind, "rollback")
        self.assertFalse(ok)
        self.assertTrue(any("FAIL  the kernel command line" in ln for ln in lines))

    def test_unknown_deployment(self):
        kind, lines, ok = self.check("ostree/deploy/default/deploy/ffff.0", "ostree=/x")
        self.assertEqual(kind, "unknown")
        self.assertFalse(ok)

    def test_hostname_selinux_root_and_rpm(self):
        _, lines, ok = self.check(DEP0, "ostree=/ostree/boot.1/default/b/1 root=UUID=x rw", hostname="bazzite",
                                  selinux="Permissive", root_fs=["btrfs", "/dev/vda3[/root]"], rpm=RPM + ["x.2 staged=yes"])
        self.assertFalse(ok)
        self.assertEqual(sum(1 for ln in lines if ln.startswith("  FAIL")), 4, lines)

    def test_no_hostname_recorded(self):
        self.base.write_text(BASELINE.replace(f"{DEP0} hostname=kinoite44", f"{DEP0} hostname=-"))
        _, lines, ok = self.check(DEP0, "ostree=/ostree/boot.1/default/b/1 root=UUID=x rw", hostname="fedora")
        self.assertTrue(ok, lines)
        self.assertFalse(any("hostname" in ln for ln in lines))


if __name__ == "__main__":
    unittest.main()
