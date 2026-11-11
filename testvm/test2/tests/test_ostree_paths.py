"""Bazzite B4: testvm/ostree_paths.py (the default deployment, the etc mapping)
and bs1-manifest's use of it, on fake ostree trees.  No VM, no root."""

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
TESTVM = TESTS.parent.parent
sys.path.insert(0, str(TESTVM))
import ostree_paths as OP  # noqa: E402

CSUM = "10e6a37f" * 8
BOOTCSUM = "1cc09936" * 8
DEP0 = f"ostree/deploy/default/deploy/{CSUM}.0"
DEP1 = f"ostree/deploy/default/deploy/{CSUM}.1"


def load_manifest():
    loader = importlib.machinery.SourceFileLoader("bs1_manifest_under_test",
                                                  str(TESTVM / "realworld" / "backshot1" / "bs1-manifest"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def kinoite(root: Path, versions=(1, 2)) -> None:
    """kinoite44's shape (B1): two deployments of one commit, boot.1 ->
    boot.1.1, link 0 to .1 and 1 to .0; ostree-2.conf (version 2) is .1's."""
    for d in (DEP0, DEP1):
        (root / d / "etc").mkdir(parents=True)
        (root / d / "usr").mkdir()
        (root / d / "var").mkdir()
        (root / d / "sysroot").mkdir()
        (root / d / "home").symlink_to("var/home")
        (root / d / "etc" / "fstab").write_text("UUID=x / btrfs subvol=root,ro 0 0\nUUID=y /boot ext4 defaults 1 2\n")
        (root / d / "etc" / "machine-id").write_text("0123\n")
        (root / d / "etc" / "tuned").mkdir()
        (root / d / "etc" / "tuned" / "active_profile").write_text("virtual-guest\n")
        (root / (d + ".origin")).write_text("[origin]\n")
    (root / DEP1 / "etc" / "bko-deployment").write_text("1\n")
    (root / "ostree" / "repo").mkdir(parents=True)
    (root / "ostree" / "repo" / "config").write_text("[core]\nmode=bare\n")
    links = root / "ostree" / "boot.1.1" / "default" / BOOTCSUM
    links.mkdir(parents=True)
    (links / "0").symlink_to(f"../../../deploy/default/deploy/{CSUM}.1")
    (links / "1").symlink_to(f"../../../deploy/default/deploy/{CSUM}.0")
    (root / "ostree" / "boot.1").symlink_to("boot.1.1")
    entries = root / "boot" / "loader.1" / "entries"
    entries.mkdir(parents=True)
    (root / "boot" / "loader").symlink_to("loader.1")
    for v, n in zip(versions, (1, 0)):
        (entries / f"ostree-{v}.conf").write_text(
            f"title Fedora Linux 44 (ostree:{n})\nversion {v}\n"
            f"options root=UUID=x rootflags=subvol=root rw ostree=/ostree/boot.1/default/{BOOTCSUM}/{n}\n")


class TestDefault(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t)

    def test_highest_version_wins(self):
        kinoite(self.t)
        self.assertEqual(OP.deployments(str(self.t)), [DEP0, DEP1])
        self.assertEqual(OP.default_deployment(str(self.t)), DEP1)

    def test_the_other_way_round(self):
        kinoite(self.t, versions=(2, 1))      # the .0 entry is now version 2
        self.assertEqual(OP.default_deployment(str(self.t)), DEP0)

    def test_versions_compare_as_numbers(self):
        kinoite(self.t, versions=(10, 9))
        self.assertEqual(OP.default_deployment(str(self.t)), DEP0)

    def test_boot_elsewhere(self):
        kinoite(self.t)
        boot = self.t / "elsewhere"
        (self.t / "boot").rename(boot)
        self.assertEqual(OP.default_deployment(str(self.t)), None)      # two, and no entries to tell
        self.assertEqual(OP.default_deployment(str(self.t), str(boot)), DEP1)

    def test_no_entries_one_deployment(self):
        kinoite(self.t)
        shutil.rmtree(self.t / DEP0)
        shutil.rmtree(self.t / "boot")
        self.assertEqual(OP.default_deployment(str(self.t)), DEP1)

    def test_entry_leading_nowhere(self):
        kinoite(self.t)
        (self.t / "ostree" / "boot.1").unlink()
        self.assertIsNone(OP.default_deployment(str(self.t)))

    def test_not_ostree(self):
        (self.t / "etc").mkdir()
        self.assertFalse(OP.is_ostree(str(self.t)))
        self.assertIsNone(OP.default_deployment(str(self.t)))

    def test_absolute_link_stays_inside(self):
        kinoite(self.t)
        (self.t / "ostree" / "boot.1").unlink()
        (self.t / "ostree" / "boot.1").symlink_to("/ostree/boot.1.1")
        self.assertEqual(OP.default_deployment(str(self.t)), DEP1)
        self.assertEqual(OP.resolve(str(self.t / DEP1), "home"), None)          # var/home isn't there yet
        (self.t / DEP1 / "var" / "home").mkdir()
        self.assertEqual(OP.resolve(str(self.t / DEP1), "home"), "var/home")

    def test_cli(self):
        kinoite(self.t)
        r = subprocess.run([sys.executable, str(TESTVM / "ostree_paths.py"), "default", str(self.t)],
                           capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, DEP1 + "\n"))
        shutil.rmtree(self.t / "ostree")
        r = subprocess.run([sys.executable, str(TESTVM / "ostree_paths.py"), "default", str(self.t)],
                           capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (1, ""))

    def test_from_rows(self):
        links = {"boot/loader": "loader.1", "ostree/boot.1": "boot.1.1",
                 f"ostree/boot.1.1/default/{BOOTCSUM}/0": f"../../../deploy/default/deploy/{CSUM}.1",
                 f"ostree/boot.1.1/default/{BOOTCSUM}/1": f"../../../deploy/default/deploy/{CSUM}.0"}
        dirs = {DEP0, DEP1, "ostree", "boot"}
        self.assertEqual(OP.default_from_rows(links, dirs), DEP1)
        # Two boot checksums (after an update with a new kernel): each has a
        # link 0, so the listing can't tell.
        links[f"ostree/boot.1.1/default/{'f' * 64}/0"] = f"../../../deploy/default/deploy/{CSUM}.0"
        self.assertIsNone(OP.default_from_rows(links, dirs))
        self.assertEqual(OP.default_from_rows({}, {DEP1}), DEP1)


class TestLogical(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(OP.logical(f"{DEP1}/etc/machine-id", DEP1), "etc/machine-id")
        self.assertEqual(OP.logical(f"{DEP1}/etc", DEP1), "etc")
        self.assertEqual(OP.logical(f"{DEP0}/etc/machine-id", DEP1), f"{DEP0}/etc/machine-id")
        self.assertEqual(OP.logical(f"{DEP1}/etcx", DEP1), f"{DEP1}/etcx")
        self.assertEqual(OP.logical(f"{DEP1}/usr/etc/x", DEP1), f"{DEP1}/usr/etc/x")
        self.assertEqual(OP.logical("var/log/x", None), "var/log/x")
        self.assertEqual(OP.logical(f"{DEP1}/etc/x", "-"), f"{DEP1}/etc/x")


class TestManifest(unittest.TestCase):
    M = load_manifest()

    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t)
        self.root = self.t / "root"
        self.root.mkdir()

    def manifest(self, name):
        out = self.t / name
        if hasattr(os, "listxattr"):
            self.M.write(str(self.root), str(out))
        else:                                   # macOS: no xattrs to list
            with mock.patch.object(self.M, "xattrs", lambda p: "-"):
                self.M.write(str(self.root), str(out))
        return out

    def compare(self, a, b):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self.M.compare(str(a), str(b))
        return rc, buf.getvalue()

    def touch(self, rel, content=None):
        p = self.root / rel
        if content is not None:
            p.write_text(content)
        t = time.time() + 100
        os.utime(p, (t, t))

    def test_default_row(self):
        kinoite(self.root)
        a = self.manifest("a.tsv")
        self.assertEqual(a.read_text().splitlines()[0], f"/ostree-default\t{DEP1}")
        rows, default = self.M.read(str(a))
        self.assertEqual(default, DEP1)
        self.assertNotIn("/ostree-default", rows)

    def test_booted_etc_is_etc(self):
        kinoite(self.root)
        a = self.manifest("a.tsv")
        self.touch(f"{DEP1}/etc/tuned/active_profile")       # REMADE, as etc/tuned/*
        self.touch(f"{DEP1}/etc/machine-id")                 # REMADE, as etc/machine-id
        rc, out = self.compare(a, self.manifest("b.tsv"))
        self.assertEqual(rc, 0, out)
        self.assertIn(f"ostree: default deployment {DEP1}", out)

    def test_remade_content_is_not(self):
        kinoite(self.root)
        a = self.manifest("a.tsv")
        self.touch(f"{DEP1}/etc/tuned/active_profile", "powersave\n")
        rc, out = self.compare(a, self.manifest("b.tsv"))
        self.assertEqual(rc, 1)
        self.assertIn(f"changed: {DEP1}/etc/tuned/active_profile", out)

    def test_rollback_etc_gets_no_exceptions(self):
        kinoite(self.root)
        a = self.manifest("a.tsv")
        self.touch(f"{DEP0}/etc/machine-id")
        rc, out = self.compare(a, self.manifest("b.tsv"))
        self.assertEqual(rc, 1)
        self.assertIn(f"changed: {DEP0}/etc/machine-id", out)

    def test_expected_lists_apply_through_the_mapping(self):
        kinoite(self.root)
        a = self.manifest("a.tsv")
        (self.root / DEP1 / "etc" / ".updated").write_text("x")       # EXPECTED: etc/.updated
        rc, out = self.compare(a, self.manifest("b.tsv"))
        self.assertEqual(rc, 0, out)
        (self.root / DEP0 / "etc" / ".updated").write_text("x")
        rc, out = self.compare(a, self.manifest("c.tsv"))
        self.assertEqual(rc, 1)
        self.assertIn(f"added: {DEP0}/etc/.updated", out)

    def test_old_manifest_without_the_row(self):
        kinoite(self.root)
        a = self.manifest("a.tsv")
        lines = a.read_text().splitlines(keepends=True)
        a.write_text("".join(lines[1:]))          # as B1's were written
        self.touch(f"{DEP1}/etc/tuned/active_profile")
        rc, out = self.compare(a, self.manifest("b.tsv"))
        self.assertEqual(rc, 0, out)

    def test_not_ostree_unchanged(self):
        (self.root / "etc").mkdir()
        (self.root / "etc" / "tuned").mkdir()
        (self.root / "etc" / "tuned" / "active_profile").write_text("x\n")
        a = self.manifest("a.tsv")
        self.assertFalse(a.read_text().startswith("/ostree-default"))
        self.touch("etc/tuned/active_profile")    # ostree's REMADE only: unexpected here
        rc, out = self.compare(a, self.manifest("b.tsv"))
        self.assertEqual(rc, 1)
        self.assertNotIn("ostree", out)


if __name__ == "__main__":
    unittest.main()
