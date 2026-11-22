"""bs1-manifest's boot and login churn on fake trees: MX Linux's (lightdm's
early-bg /.fehbg and mx-goodies' xdg-user-dirs-update rewriting ~/.bashrc,
held to the same bytes and only on an MX baseline) and dbus-launch's
~/.dbus/session-bus file (BackShot1 debian-13-20261121-065025).  No root, no
VM."""

import contextlib
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
    """Slackware's boot: rc.M's caches, rc.udev's hwdb and dhcpcd's ntp.conf
    the same bytes again (new mtimes), adjtime and resolv.conf.bak other
    bytes; only on a Slackware baseline (the Thinkpad's -current,
    2026-11-22: a reboot, 952 paths apart)."""

    REMADE = ["usr/share/mime/aliases", "usr/share/mime/application/pdf.xml", "usr/share/glib-2.0/schemas/gschemas.compiled",
              "usr/share/applications/intent.cache", "usr/lib64/gdk-pixbuf-2.0/2.10.0/loaders.cache", "etc/udev/hwdb.bin",
              "etc/ntp.conf"]

    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t)
        self.root = self.t / "root"
        put(self.root, "etc/slackware-version", "Slackware 15.0+\n")
        for rel in self.REMADE + ["etc/resolv.conf.bak", "etc/rc.d/rc.local"]:
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

if __name__ == "__main__":
    unittest.main()
