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

    def thinkpad_churn(self):
        """The Thinkpad's Bazzite 44 (B9, run bazzite-44-20261111-173140):
        what its first boot after the restore changed, in both deployments'
        etc; returns the baseline."""
        kinoite(self.root)
        (self.root / "var" / "mnt").mkdir(parents=True)
        for d in (DEP0, DEP1):
            etc = self.root / d / "etc"
            (etc / "modprobe.d").mkdir()
            (etc / "modprobe.d" / "tuned.conf").write_text("")
            (etc / "motd.d").mkdir()
            (etc / "motd.d" / "boot-status").write_text("Greenboot healthcheck passed - status is GREEN\n")
            slice_d = etc / "systemd" / "system.control" / "user-967.slice.d"
            slice_d.mkdir(parents=True)
            (slice_d / "50-MemoryMin.conf").write_text("[Slice]\nMemoryMin=262144000\n")
            svc = etc / "systemd" / "system.control" / "foo.service.d"
            svc.mkdir()
            (svc / "50-CPUWeight.conf").write_text("[Service]\nCPUWeight=50\n")
        return self.manifest("a.tsv")

    def test_thinkpad_boot_churn_is_expected(self):
        a = self.thinkpad_churn()
        self.touch(f"{DEP1}/etc/modprobe.d/tuned.conf")             # REMADE
        self.touch(f"{DEP1}/etc/motd.d/boot-status")                # REMADE
        self.touch(f"{DEP1}/etc/systemd/system.control/foo.service.d/50-CPUWeight.conf")     # REMADE
        self.touch(f"{DEP1}/etc/systemd/system.control/user-967.slice.d/50-MemoryMin.conf",
                   "[Slice]\nMemoryMin=274726912\n")                 # EXPECTED: new values
        (self.root / "var" / "mnt" / "bs1-backup").mkdir()          # EXPECTED: verify's mountpoint
        rc, out = self.compare(a, self.manifest("b.tsv"))
        self.assertEqual(rc, 0, out)

    def test_thinkpad_churn_content_is_not(self):
        a = self.thinkpad_churn()
        self.touch(f"{DEP1}/etc/motd.d/boot-status", "Greenboot healthcheck failed - status is RED\n")
        self.touch(f"{DEP1}/etc/modprobe.d/tuned.conf", "options x y\n")
        # An administrator's set-property drop-in keeps its values.
        self.touch(f"{DEP1}/etc/systemd/system.control/foo.service.d/50-CPUWeight.conf", "[Service]\nCPUWeight=10\n")
        (self.root / "var" / "mnt" / "bs1-backup" / "x").mkdir(parents=True)     # only the mountpoint
        rc, out = self.compare(a, self.manifest("b.tsv"))
        self.assertEqual(rc, 1)
        for p in (f"changed: {DEP1}/etc/motd.d/boot-status", f"changed: {DEP1}/etc/modprobe.d/tuned.conf",
                  f"changed: {DEP1}/etc/systemd/system.control/foo.service.d/50-CPUWeight.conf",
                  "added: var/mnt/bs1-backup/x"):
            self.assertIn(p, out)
        self.assertNotIn("added: var/mnt/bs1-backup\n", out)

    def test_thinkpad_churn_in_rollback_etc_is_not(self):
        a = self.thinkpad_churn()
        self.touch(f"{DEP0}/etc/modprobe.d/tuned.conf")
        self.touch(f"{DEP0}/etc/systemd/system.control/user-967.slice.d/50-MemoryMin.conf",
                   "[Slice]\nMemoryMin=274726912\n")
        rc, out = self.compare(a, self.manifest("b.tsv"))
        self.assertEqual(rc, 1)
        self.assertIn(f"changed: {DEP0}/etc/modprobe.d/tuned.conf", out)
        self.assertIn(f"changed: {DEP0}/etc/systemd/system.control/user-967.slice.d/50-MemoryMin.conf", out)

    def test_var_mnt_is_ostree_only(self):
        (self.root / "var" / "mnt").mkdir(parents=True)
        a = self.manifest("a.tsv")
        (self.root / "var" / "mnt" / "bs1-backup").mkdir()
        rc, out = self.compare(a, self.manifest("b.tsv"))
        self.assertEqual(rc, 1)
        self.assertIn("added: var/mnt/bs1-backup", out)

    def test_var_mnt_added_on_ostree(self):
        kinoite(self.root)
        (self.root / "var").mkdir()
        a = self.manifest("a.tsv")
        (self.root / "var" / "mnt" / "bs1-backup").mkdir(parents=True)     # ADDED: /mnt -> var/mnt
        rc, out = self.compare(a, self.manifest("b.tsv"))
        self.assertEqual(rc, 0, out)

    def test_bs1_mountpoint_where_there_was_no_mnt(self):
        """NixOS (run nixos-26.05-20261113-095929): no /mnt at backup time,
        and verify's DRIVE made /mnt/bs1-backup."""
        (self.root / "etc").mkdir()
        a = self.manifest("a.tsv")
        (self.root / "mnt" / "bs1-backup").mkdir(parents=True)
        rc, out = self.compare(a, self.manifest("b.tsv"))
        self.assertEqual(rc, 0, out)
        self.assertIn("added: 2, of which unexpected: 0", out)

    def test_bs1_mountpoint_in_an_existing_mnt(self):
        (self.root / "mnt").mkdir()
        a = self.manifest("a.tsv")
        (self.root / "mnt" / "bs1-backup").mkdir()
        self.touch("mnt")
        rc, out = self.compare(a, self.manifest("b.tsv"))
        self.assertEqual(rc, 0, out)

    def test_only_the_bs1_mountpoint_under_mnt(self):
        a = self.manifest("a.tsv")
        (self.root / "mnt" / "bs1-backup" / "x").mkdir(parents=True)
        (self.root / "mnt" / "other").mkdir()
        rc, out = self.compare(a, self.manifest("b.tsv"))
        self.assertEqual(rc, 1)
        self.assertIn("added: mnt/bs1-backup/x", out)
        self.assertIn("added: mnt/other", out)
        self.assertNotIn("added: mnt\n", out)

    def test_mnt_missing_is_not_expected(self):
        (self.root / "mnt").mkdir()
        a = self.manifest("a.tsv")
        (self.root / "mnt").rmdir()
        rc, out = self.compare(a, self.manifest("b.tsv"))
        self.assertEqual(rc, 1)
        self.assertIn("missing: mnt", out)

    def test_mnt_mode_change_is_not_expected(self):
        (self.root / "mnt").mkdir(mode=0o755)
        os.chmod(self.root / "mnt", 0o755)
        a = self.manifest("a.tsv")
        os.chmod(self.root / "mnt", 0o700)
        rc, out = self.compare(a, self.manifest("b.tsv"))
        self.assertEqual(rc, 1)
        self.assertIn("changed: mnt", out)

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

    def objects(self):
        """A repository object hardlinked into both deployments (nlink 3), as
        a bare repository has them, and a file outside ostree/."""
        kinoite(self.root)
        obj = self.root / "ostree" / "repo" / "objects" / "ab" / "cdef.file"
        obj.parent.mkdir(parents=True)
        obj.write_text("object\n")
        for d in (DEP0, DEP1):
            os.link(obj, self.root / d / "usr" / "bash")
        (self.root / "boot" / "vmlinuz").write_text("k\n")
        return obj

    def test_link_and_flag_columns_under_ostree_only(self):
        self.objects()
        rows = {ln.split("\t")[0]: ln.split("\t") for ln in self.manifest("a.tsv").read_text().splitlines()}
        obj = rows["ostree/repo/objects/ab/cdef.file"]
        self.assertEqual(len(obj), 10)
        self.assertEqual(obj[8], "3")
        self.assertEqual(rows[f"{DEP1}/usr/bash"][8], "3")
        self.assertEqual(len(rows["boot/vmlinuz"]), 8)
        self.assertEqual(len(rows[f"{DEP1}/usr"]), 8)          # a directory

    def test_unlinked_object_is_a_change(self):
        obj = self.objects()
        a = self.manifest("a.tsv")
        # A restore that wrote the deployment's file apart: same content and
        # metadata, one link fewer everywhere.
        dep = self.root / DEP0 / "usr" / "bash"
        st = os.stat(obj)
        dep.unlink()
        dep.write_text("object\n")
        os.utime(dep, ns=(st.st_atime_ns, st.st_mtime_ns))
        rc, out = self.compare(a, self.manifest("b.tsv"))
        self.assertEqual(rc, 1)
        self.assertIn("changed: ostree/repo/objects/ab/cdef.file", out)
        self.assertIn(f"changed: {DEP0}/usr/bash", out)
        self.assertIn("link counts and inode flags compared for", out)

    def test_baseline_without_link_columns(self):
        obj = self.objects()
        a = self.manifest("a.tsv")
        a.write_text("".join("\t".join(ln.split("\t")[:8]) + "\n" for ln in a.read_text().splitlines()))
        dep = self.root / DEP0 / "usr" / "bash"
        st = os.stat(obj)
        dep.unlink()
        dep.write_text("object\n")
        os.utime(dep, ns=(st.st_atime_ns, st.st_mtime_ns))
        rc, out = self.compare(a, self.manifest("b.tsv"))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("link counts", out)

    def test_flags(self):
        f = self.M.flags_same
        self.assertTrue(f("0x0", "0x0"))
        self.assertTrue(f("-", "-"))
        self.assertTrue(f("0x0", "0x400"))           # FS_NOCOMP_FL, btrfs's own
        self.assertTrue(f("0x10", "0x410"))
        self.assertFalse(f("0x400", "0x0"))          # lost
        self.assertFalse(f("0x0", "0x10"))           # immutable gained
        self.assertFalse(f("0x10", "0x400"))
        self.assertFalse(f("0x0", "-"))

    def test_flag_change_is_a_change(self):
        self.objects()
        a = self.manifest("a.tsv")
        # The immutable flag (0x10) on every file: neither "0x0" (Linux) nor
        # "-" (macOS, no flags to read) is that.
        with mock.patch.object(self.M, "digest", lambda p, flags=False: (self.M.hashlib.sha256(Path(p).read_bytes()).hexdigest(), "0x10" if flags else None)):
            b = self.manifest("b.tsv")
        rc, out = self.compare(a, b)
        self.assertEqual(rc, 1)
        self.assertIn("changed: ostree/repo/objects/ab/cdef.file", out)
        self.assertNotIn("changed: boot/vmlinuz", out)

    def test_linked_file_is_read_once(self):
        self.objects()
        calls = []
        real = self.M.digest
        with mock.patch.object(self.M, "digest", lambda p, flags=False: calls.append(p) or real(p, flags)):
            self.manifest("a.tsv")
        self.assertEqual(sum(1 for p in calls if p.endswith(("cdef.file", "usr/bash"))), 1)


if __name__ == "__main__":
    unittest.main()
