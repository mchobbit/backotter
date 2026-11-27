"""bs1-manifest's boot and login churn on fake trees: MX Linux's (lightdm's
early-bg /.fehbg and mx-goodies' xdg-user-dirs-update rewriting ~/.bashrc,
held to the same bytes and only on an MX baseline) and dbus-launch's
~/.dbus/session-bus file (BackShot1 debian-13-20261121-065025); and the
inode flags of every regular file, with the btrfs.compression property the
kernel writes for a restored compress flag (openSUSE Aeon,
aeon-20261121-20261123-131915); and gcov's counter files, rewritten by a
coverage-built program (PikaOS 4, pika-4-20261127-070909).  No root, no VM."""

import base64
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

if os.geteuid() == 0:
    # These run BackShot1's scripts against fakes.  As root, on a real
    # machine, one slip reaches its disks, its firmware or its reboot: a
    # background reboot outlived test_bs1_to_stick's fakes and rebooted the
    # Thinkpad (2026-11-22).
    raise unittest.SkipTest("BackShot1's tests don't run as root")

TESTS = Path(__file__).resolve().parent
BS1 = TESTS.parent.parent / "realworld" / "backshot1"


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


M = load("bs1_manifest_under_test", BS1 / "bs1-manifest")

FEHBG = "#!/bin/sh\nfeh --no-fehbg --bg-scale '/usr/share/backgrounds/default25.png'\n"
BASHRC = '[ -z "$LESS" ] && export LESS="-R -i"\n'
BUS = "760f66bc3e191a1d338d96056b013935-0"


def put(root, rel, content=""):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


class Churn(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t)
        self.root = self.t / "root"
        put(self.root, "usr/bin/early-bg", "#!/bin/sh\n")
        put(self.root, "usr/bin/xdg-user-dirs-update.real", "\x7fELF")
        put(self.root, ".fehbg", FEHBG)
        put(self.root, "home/m/.bashrc", BASHRC)
        put(self.root, "home/m/.profile", "# ~/.profile\n")
        put(self.root, "root/.bashrc", BASHRC)
        put(self.root, f"home/m/.dbus/session-bus/{BUS}", "DBUS_SESSION_BUS_PID=2948\n")

    def manifest(self, name):
        out = self.t / name
        if hasattr(os, "listxattr"):
            M.write(str(self.root), str(out))
        else:
            with mock.patch.object(M, "xattrs", lambda p: "-"):
                M.write(str(self.root), str(out))
        return out

    def touch(self, rel, content=None):
        p = self.root / rel
        if content is not None:
            p.write_text(content)
        t = time.time() + 100
        os.utime(p, (t, t))

    def after(self, *changes):
        a = self.manifest("a.tsv")
        for c in changes:
            self.touch(*c) if isinstance(c, tuple) else self.touch(c)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = M.compare(str(a), str(self.manifest("b.tsv")))
        return rc, buf.getvalue()

    def test_mx_login_rewrites(self):
        # The same bytes again, new mtimes: expected on MX.
        rc, out = self.after(".fehbg", "home/m/.bashrc")
        self.assertEqual(rc, 0, out)
        self.assertIn("MX Linux: written again", out)
        self.assertIn("changed: 0, of which unexpected: 0", out)

    def test_mx_other_bytes_are_a_change(self):
        rc, out = self.after((".fehbg", FEHBG.replace("default25", "other")),
                             ("home/m/.bashrc", BASHRC + "alias ll='ls -l'\n"))
        self.assertEqual(rc, 1, out)
        self.assertIn("changed: .fehbg", out)
        self.assertIn("changed: home/m/.bashrc", out)

    def test_mx_only_those_files(self):
        # Not root's .bashrc (no session runs the wrapper for root), nor the
        # rest of the home directory.
        rc, out = self.after("root/.bashrc", "home/m/.profile")
        self.assertEqual(rc, 1, out)
        self.assertIn("changed: root/.bashrc", out)
        self.assertIn("changed: home/m/.profile", out)

    def test_each_by_its_writer(self):
        # Without early-bg, /.fehbg is held; without the diversion, .bashrc.
        (self.root / "usr/bin/early-bg").unlink()
        rc, out = self.after(".fehbg", "home/m/.bashrc")
        self.assertEqual(rc, 1, out)
        self.assertIn("changed: .fehbg", out)
        self.assertNotIn("changed: home/m/.bashrc", out)
        put(self.root, "usr/bin/early-bg", "#!/bin/sh\n")
        (self.root / "usr/bin/xdg-user-dirs-update.real").unlink()
        rc, out = self.after(".fehbg", "home/m/.bashrc")
        self.assertEqual(rc, 1, out)
        self.assertNotIn("changed: .fehbg", out)
        self.assertIn("changed: home/m/.bashrc", out)

    def test_not_mx(self):
        for rel in ("usr/bin/early-bg", "usr/bin/xdg-user-dirs-update.real"):
            (self.root / rel).unlink()
        rc, out = self.after(".fehbg", "home/m/.bashrc")
        self.assertEqual(rc, 1, out)
        self.assertNotIn("MX Linux", out)

    def test_dbus_session_bus_file(self):
        # dbus-launch's file: other bytes at every X login, on any system.
        for rel in ("usr/bin/early-bg", "usr/bin/xdg-user-dirs-update.real"):
            (self.root / rel).unlink()
        rc, out = self.after((f"home/m/.dbus/session-bus/{BUS}", "DBUS_SESSION_BUS_PID=2995\n"))
        self.assertEqual(rc, 0, out)
        # A new display's, and one from a different machine ID.
        a = self.manifest("a.tsv")
        (self.root / f"home/m/.dbus/session-bus/{BUS}").rename(self.root / "home/m/.dbus/session-bus/0123-1")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = M.compare(str(a), str(self.manifest("b.tsv")))
        self.assertEqual(rc, 0, buf.getvalue())

    def test_dbus_only_session_bus(self):
        put(self.root, "home/m/.dbus/other", "x\n")
        rc, out = self.after(("home/m/.dbus/other", "y\n"))
        self.assertEqual(rc, 1, out)
        self.assertIn("changed: home/m/.dbus/other", out)



class SlackwareChurn(unittest.TestCase):
    """Slackware's boot: rc.M's caches and rc.udev's hwdb the same bytes
    again (new mtimes), adjtime and resolv.conf.bak other bytes, and
    ntp.conf other bytes where dhcpcd's ntp hook writes it; only on a
    Slackware baseline (the Thinkpad's -current, 2026-11-22: a reboot, 952
    paths apart; 2026-11-23: the lease's NTP server changed)."""

    REMADE = ["usr/share/mime/aliases", "usr/share/mime/application/pdf.xml", "usr/share/glib-2.0/schemas/gschemas.compiled",
              "usr/share/applications/intent.cache", "usr/lib64/gdk-pixbuf-2.0/2.10.0/loaders.cache", "etc/udev/hwdb.bin"]
    NTP_HOOK = "lib/dhcpcd/dhcpcd-hooks/50-ntp.conf"

    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t)
        self.root = self.t / "root"
        put(self.root, "etc/slackware-version", "Slackware 15.0+\n")
        for rel in self.REMADE + ["etc/resolv.conf.bak", "etc/rc.d/rc.local", "etc/ntp.conf", self.NTP_HOOK]:
            put(self.root, rel, f"{rel}\n")

    def compare(self, change):
        a = self.t / "a.tsv"
        b = self.t / "b.tsv"
        with mock.patch.object(M, "xattrs", lambda p: "-"):
            M.write(str(self.root), str(a))
            change()
            M.write(str(self.root), str(b))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = M.compare(str(a), str(b))
        return rc, buf.getvalue()

    def touch(self, rel, content=None):
        p = self.root / rel
        if content is not None:
            p.write_text(content)
        t = time.time() + 100
        os.utime(p, (t, t))

    def boot(self):
        for rel in self.REMADE:
            self.touch(rel)
        self.touch("etc/resolv.conf.bak", "nameserver 192.168.68.1\n")
        put(self.root, "etc/adjtime", "0.0 1795332742 0.0\n")

    def test_a_boot(self):
        rc, out = self.compare(self.boot)
        self.assertEqual(rc, 0, out)
        self.assertIn("Slackware: written again at every boot", out)

    def test_ntp_conf_with_dhcpcd_hook(self):
        rc, out = self.compare(lambda: self.touch("etc/ntp.conf", "server 172.232.44.156\n"))
        self.assertEqual(rc, 0, out)
        self.assertIn("other bytes expected: etc/adjtime etc/resolv.conf.bak boot/initrd.gz etc/ntp.conf", out)

    def test_ntp_conf_without_the_hook_is_held(self):
        (self.root / self.NTP_HOOK).unlink()
        rc, out = self.compare(lambda: self.touch("etc/ntp.conf", "server 172.232.44.156\n"))
        self.assertEqual(rc, 1, out)
        self.assertIn("changed: etc/ntp.conf", out)

    def test_remade_must_keep_its_bytes(self):
        rc, out = self.compare(lambda: self.touch("usr/share/mime/aliases", "other\n"))
        self.assertEqual(rc, 1, out)
        self.assertIn("changed: usr/share/mime/aliases", out)

    def test_the_rest_is_held(self):
        rc, out = self.compare(lambda: self.touch("etc/rc.d/rc.local"))
        self.assertEqual(rc, 1, out)
        self.assertIn("changed: etc/rc.d/rc.local", out)

    def test_not_slackware(self):
        (self.root / "etc/slackware-version").unlink()
        rc, out = self.compare(self.boot)
        self.assertEqual(rc, 1, out)
        self.assertNotIn("Slackware", out)
        self.assertIn("changed: usr/share/mime/aliases", out)
        self.assertIn("added: etc/adjtime", out)


class GcovChurn(unittest.TestCase):
    """gcov's counter files, written at every run of a coverage-built
    program to its build tree's absolute path (PikaOS 4's
    fwupd-detect-cet at each boot, pika-4-20261127-070909): changed or
    added as regular files, expected on any system; gone, or with another
    mode or type, still unexpected."""

    DIR = "workspace/repo-tools/run-upstream-build/fwupd-2.1.6/obj-x86_64-linux-gnu/plugins/cpu/fwupd-detect-cet.p"
    GCDA = f"{DIR}/fu-cpu-helper-cet.c.gcda"

    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t)
        self.root = self.t / "root"
        put(self.root, self.GCDA, "adcg\x01")
        put(self.root, f"{self.DIR}/fu-cpu-helper-cet.c.gcno", "oncg")
        put(self.root, "usr/libexec/fwupd/fwupd-detect-cet", "\x7fELF")

    def compare(self, change):
        a = self.t / "a.tsv"
        b = self.t / "b.tsv"
        with mock.patch.object(M, "xattrs", lambda p: "-"):
            M.write(str(self.root), str(a))
            change()
            M.write(str(self.root), str(b))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = M.compare(str(a), str(b))
        return rc, buf.getvalue()

    def rewrite(self, rel, content):
        p = self.root / rel
        p.write_text(content)
        t = time.time() + 100
        os.utime(p, (t, t))

    def test_counters_rewritten_at_a_boot(self):
        rc, out = self.compare(lambda: self.rewrite(self.GCDA, "adcg\x02 more counters"))
        self.assertEqual(rc, 0, out)
        self.assertIn("gcov: 1 counter files", out)
        self.assertIn("changed: 1, of which unexpected: 0", out)

    def test_new_counters_in_a_tree_already_there(self):
        rc, out = self.compare(lambda: put(self.root, f"{self.DIR}/fu-cpu.c.gcda", "adcg"))
        self.assertEqual(rc, 0, out)
        self.assertIn("added: 1, of which unexpected: 0", out)

    def test_new_counter_tree_is_still_added(self):
        # The counter file is expected; the directories made for it aren't.
        rc, out = self.compare(lambda: put(self.root, "build/obj/x.c.gcda", "adcg"))
        self.assertEqual(rc, 1, out)
        self.assertIn("added: build/obj", out)
        self.assertNotIn("added: build/obj/x.c.gcda", out)

    def test_gone_counters_are_a_loss(self):
        rc, out = self.compare(lambda: (self.root / self.GCDA).unlink())
        self.assertEqual(rc, 1, out)
        self.assertIn(f"missing: {self.GCDA}", out)

    def test_another_mode_is_a_change(self):
        def change():
            self.rewrite(self.GCDA, "adcg\x02")
            os.chmod(self.root / self.GCDA, 0o600)
        rc, out = self.compare(change)
        self.assertEqual(rc, 1, out)
        self.assertIn(f"changed: {self.GCDA}", out)

    def test_a_link_in_its_place_is_a_change(self):
        def change():
            (self.root / self.GCDA).unlink()
            os.symlink("elsewhere", self.root / self.GCDA)
        rc, out = self.compare(change)
        self.assertEqual(rc, 1, out)
        self.assertIn(f"changed: {self.GCDA}", out)

    def test_only_gcda(self):
        # The compile-time notes beside them and the program itself are held.
        def change():
            self.rewrite(f"{self.DIR}/fu-cpu-helper-cet.c.gcno", "other")
            self.rewrite("usr/libexec/fwupd/fwupd-detect-cet", "other")
        rc, out = self.compare(change)
        self.assertEqual(rc, 1, out)
        self.assertIn(f"changed: {self.DIR}/fu-cpu-helper-cet.c.gcno", out)
        self.assertIn("changed: usr/libexec/fwupd/fwupd-detect-cet", out)
        self.assertNotIn("gcov:", out)


SELINUX = {"security.selinux": b"system_u:object_r:usr_t:s0\x00"}


class CompressProperty(unittest.TestCase):
    """mkfs.btrfs --rootdir --compress leaves files with the compress flag
    (0x4) and no btrfs.compression property; restoring the flag makes the
    kernel write the property.  Expected only so: a regular file, the flag
    before and after, that one attribute gained, an algorithm's name.  The
    flags and attributes are faked per path, so this runs anywhere."""

    FILES = ["usr/bin/a", "usr/bin/b", "usr/lib/c", "etc/d"]

    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t)
        self.root = self.t / "root"
        for rel in self.FILES:
            put(self.root, rel, f"{rel}\n")

    def manifest(self, name, flags=None, xattrs=None):
        """The tree's manifest, with FLAGS ({rel: "0x.."}, else "0x0") and
        XATTRS ({rel: {name: bytes}}, else the SELinux label) as its files'."""
        flags, xattrs = flags or {}, xattrs or {}
        rel = lambda p: os.path.relpath(p, self.root)

        def digest(p, want=False):
            return hashlib.sha256(Path(p).read_bytes()).hexdigest(), flags.get(rel(p), "0x0") if want else None

        def field(p):
            x = {**SELINUX, **xattrs.get(rel(p), {})}
            return ",".join(f"{k}={base64.b64encode(v).decode()}" for k, v in sorted(x.items()))

        out = self.t / name
        with mock.patch.object(M, "digest", digest), mock.patch.object(M, "xattrs", field):
            M.write(str(self.root), str(out))
        return out

    def compare(self, a, b):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = M.compare(str(a), str(b))
        return rc, buf.getvalue()

    def rows(self, path):
        return {ln.split("\t")[0]: ln.split("\t") for ln in path.read_text().splitlines()}

    def old_format(self, path):
        """As written before the flags were recorded outside ostree/."""
        path.write_text("".join("\t".join(ln.split("\t")[:8]) + "\n" for ln in path.read_text().splitlines()))
        return path

    def test_flags_recorded_for_every_regular_file(self):
        rows = self.rows(self.manifest("a.tsv", flags={"usr/bin/a": "0x4"}))
        self.assertEqual(rows["usr/bin/a"][8:], ["-", "0x4"])
        self.assertEqual(rows["etc/d"][8:], ["-", "0x0"])
        self.assertEqual(len(rows["usr/bin"]), 8)              # a directory

    def test_real_flags_or_none(self):
        # The real ioctl: a number on Linux, "-" where there are no flags to
        # read (macOS, vfat), never a crash.
        out = self.t / "real.tsv"
        with mock.patch.object(M, "xattrs", lambda p: "-"):
            M.write(str(self.root), str(out))
        for rel in self.FILES:
            link, fl = self.rows(out)[rel][8:]
            self.assertEqual(link, "-")
            self.assertTrue(fl == "-" or int(fl, 16) >= 0, fl)

    def test_unreadable_file_has_no_flags(self):
        def digest(p, want=False):
            raise PermissionError(13, "Permission denied")
        out = self.t / "a.tsv"
        with mock.patch.object(M, "digest", digest), mock.patch.object(M, "xattrs", lambda p: "-"):
            M.write(str(self.root), str(out))
        self.assertEqual(self.rows(out)["etc/d"][6:], ["unreadable:13", "-", "-", "-"])

    def test_flag_kept_and_property_gained(self):
        flagged = {"usr/bin/a": "0x4", "usr/bin/b": "0x4", "usr/lib/c": "0x4"}
        a = self.manifest("a.tsv", flags=flagged)
        b = self.manifest("b.tsv", flags=flagged, xattrs={
            "usr/bin/a": {"btrfs.compression": b"zstd"}, "usr/bin/b": {"btrfs.compression": b"zlib"},
            "usr/lib/c": {"btrfs.compression": b"lzo"}})
        rc, out = self.compare(a, b)
        self.assertEqual(rc, 0, out)
        self.assertIn("btrfs: 3 regular files with the compress flag and no btrfs.compression property came back with the flag and the property", out)
        self.assertIn("xattrs: 3, of which unexpected: 0", out)
        self.assertNotIn("ostree:", out)

    def test_other_flag_bits_beside_the_compress_flag(self):
        # btrfs's own FS_NOCOMP_FL beside it, after: still the flag, both sides.
        a = self.manifest("a.tsv", flags={"usr/bin/a": "0x4"})
        b = self.manifest("b.tsv", flags={"usr/bin/a": "0x404"}, xattrs={"usr/bin/a": {"btrfs.compression": b"zstd"}})
        rc, out = self.compare(a, b)
        self.assertEqual(rc, 0, out)
        self.assertIn("btrfs: 1 regular files", out)

    def rejected(self, a, b, rel="usr/bin/a"):
        rc, out = self.compare(a, b)
        self.assertEqual(rc, 1, out)
        self.assertIn(f"xattrs: {rel} (", out)
        self.assertIn("xattrs: 1, of which unexpected: 1", out)
        self.assertNotIn("btrfs:", out)
        return out

    def test_no_flag_in_the_baseline(self):
        # A file that inherited compression it never had.
        a = self.manifest("a.tsv")
        b = self.manifest("b.tsv", flags={"usr/bin/a": "0x4"}, xattrs={"usr/bin/a": {"btrfs.compression": b"zstd"}})
        out = self.rejected(a, b)
        self.assertIn("btrfs.compression: (none) -> b'zstd'", out)

    def test_no_flag_after(self):
        a = self.manifest("a.tsv", flags={"usr/bin/a": "0x4"})
        b = self.manifest("b.tsv", xattrs={"usr/bin/a": {"btrfs.compression": b"zstd"}})
        self.rejected(a, b)

    def test_no_flags_to_read(self):
        a = self.manifest("a.tsv", flags={"usr/bin/a": "-"})
        b = self.manifest("b.tsv", flags={"usr/bin/a": "-"}, xattrs={"usr/bin/a": {"btrfs.compression": b"zstd"}})
        self.rejected(a, b)

    def test_another_attribute_differs_too(self):
        a = self.manifest("a.tsv", flags={"usr/bin/a": "0x4"})
        b = self.manifest("b.tsv", flags={"usr/bin/a": "0x4"}, xattrs={
            "usr/bin/a": {"btrfs.compression": b"zstd", "security.selinux": b"system_u:object_r:unlabeled_t:s0\x00"}})
        self.rejected(a, b)
        b = self.manifest("c.tsv", flags={"usr/bin/a": "0x4"}, xattrs={
            "usr/bin/a": {"btrfs.compression": b"zstd", "user.new": b"1"}})
        self.rejected(a, b)

    def test_value_is_no_algorithm(self):
        a = self.manifest("a.tsv", flags={"usr/bin/a": "0x4"})
        for i, value in enumerate((b"none", b"", b"zstd:3", b"ZSTD")):
            b = self.manifest(f"b{i}.tsv", flags={"usr/bin/a": "0x4"}, xattrs={"usr/bin/a": {"btrfs.compression": value}})
            self.rejected(a, b)

    def test_property_was_there_before(self):
        # Changed, not gained: another algorithm than the source's.
        a = self.manifest("a.tsv", flags={"usr/bin/a": "0x4"}, xattrs={"usr/bin/a": {"btrfs.compression": b"zlib"}})
        b = self.manifest("b.tsv", flags={"usr/bin/a": "0x4"}, xattrs={"usr/bin/a": {"btrfs.compression": b"zstd"}})
        self.rejected(a, b)
        # And lost.
        self.rejected(b, self.manifest("c.tsv", flags={"usr/bin/a": "0x4"}))

    def test_directory(self):
        # A directory has no flags in the manifest, so nothing vouches for it.
        a = self.manifest("a.tsv")
        b = self.manifest("b.tsv", xattrs={"usr/bin": {"btrfs.compression": b"zstd"}})
        self.rejected(a, b, "usr/bin")
        # Nor would a row that had them.
        d = ["0o40755", "0", "0", "0", "1", "-", "-", "-", "0x4"]
        f = ["0o100644"] + d[1:]
        gained = f"btrfs.compression={base64.b64encode(b'zstd').decode()}"
        self.assertTrue(M.compress_property(f, f[:6] + [gained] + f[7:]))
        self.assertFalse(M.compress_property(d, d[:6] + [gained] + d[7:]))
        self.assertFalse(M.compress_property(d, f[:6] + [gained] + f[7:]))

    def test_old_format_baseline(self):
        # No flags column in the baseline: the difference stays unexpected,
        # against an after in the new format, and nothing crashes.
        flagged = {"usr/bin/a": "0x4"}
        a = self.old_format(self.manifest("a.tsv", flags=flagged))
        b = self.manifest("b.tsv", flags=flagged, xattrs={"usr/bin/a": {"btrfs.compression": b"zstd"}})
        self.rejected(a, b)
        # Both old: as before.
        self.rejected(a, self.old_format(b))
        # Nothing else differs between an old baseline and a new after.
        rc, out = self.compare(a, self.manifest("c.tsv", flags={rel: "0x80000" for rel in self.FILES}))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("ostree:", out)

    def test_flag_differences_alone_are_not_reported(self):
        # Outside ostree/ the flags are the filesystem's: gained, lost,
        # unreadable, none of it is a change.
        a = self.manifest("a.tsv", flags={"usr/bin/a": "0x4", "usr/bin/b": "0x80000", "usr/lib/c": "0x0", "etc/d": "0x10"})
        b = self.manifest("b.tsv", flags={"usr/bin/a": "0x0", "usr/bin/b": "0x0", "usr/lib/c": "0x10000004", "etc/d": "-"})
        rc, out = self.compare(a, b)
        self.assertEqual(rc, 0, out)
        self.assertIn("changed: 0, of which unexpected: 0", out)
        self.assertIn("xattrs: 0, of which unexpected: 0", out)
        # No link counts here to have compared.
        self.assertNotIn("ostree:", out)
        self.assertNotIn("btrfs:", out)

    def test_held_path_gets_no_exception(self):
        # rEFInd's files are held as the ESP brought them back.
        put(self.root, "boot/efi/EFI/refind/refind.conf", "timeout 5\n")
        rel = "boot/efi/EFI/refind/refind.conf"
        a = self.manifest("a.tsv", flags={rel: "0x4"})
        b = self.manifest("b.tsv", flags={rel: "0x4"}, xattrs={rel: {"btrfs.compression": b"zstd"}})
        self.rejected(a, b, rel)

    def test_a_changed_file_is_still_a_change(self):
        # The exception is the attribute's alone.
        a = self.manifest("a.tsv", flags={"usr/bin/a": "0x4"})
        (self.root / "usr/bin/a").write_text("other\n")
        b = self.manifest("b.tsv", flags={"usr/bin/a": "0x4"}, xattrs={"usr/bin/a": {"btrfs.compression": b"zstd"}})
        rc, out = self.compare(a, b)
        self.assertEqual(rc, 1, out)
        self.assertIn("changed: usr/bin/a", out)
        self.assertIn("xattrs: 1, of which unexpected: 0", out)


if __name__ == "__main__":
    unittest.main()
