"""BackShot1 on Slackware (the Thinkpad on a pinned slackware64-current,
2026-11-22): bs1-adopt's Slackware arms (rc.d services, installpkg from the
pinned tree on the backup drive, /etc/HOSTNAME, avahi's needs), bs1-common's
this_host and pkgtools package list, and bs1-verify's wait for rc.M.  No
root, no Slackware: each function runs from the script's own text, with its
absolute paths moved under a temporary directory and fakes for the tools it
calls.  Nothing here needs more than bash 3.2."""

import hashlib
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
BS1 = TESTS.parent.parent / "realworld" / "backshot1"
ADOPT = BS1 / "bs1-adopt"
BASH = shutil.which("bash")


def function(path, name, moves=()):
    """NAME's text from a bash file (one line `NAME() { ...; }`, or from
    `NAME() {`, a comment after it allowed, to the first `}` alone on a
    line), each (old, new) of MOVES replaced; every old must be in it."""
    text = Path(path).read_text()
    m = (re.search(rf"^{re.escape(name)}\(\) {{ .*; }}\n", text, re.M)
         or re.search(rf"^{re.escape(name)}\(\) {{[^\n]*\n.*?^}}\n", text, re.M | re.S))
    assert m, f"no function {name} in {path}"
    body = m.group(0)
    for old, new in moves:
        assert old in body, f"{old} isn't in {name}"
        body = body.replace(old, new)
    return body


def exe(path, text):
    Path(path).write_text(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class Base(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="bs1-slk-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.bin = self.t / "bin"
        self.bin.mkdir()
        # Only these tools: the test host's own must not show.
        for tool in ("sh", "cat", "grep", "awk", "head", "chmod", "rm", "sort", "mkdir", "tr", "sleep", "seq", "ln"):
            os.symlink(shutil.which(tool), self.bin / tool)
        # Slackware's md5sum, which macOS lacks.
        exe(self.bin / "md5sum", f"""#!{shutil.which("python3")} -I
import hashlib, sys
print(hashlib.md5(sys.stdin.buffer.read()).hexdigest() + "  -")
""")

    def bash(self, script, timeout=30):
        return subprocess.run([BASH, "-c", "set -euo pipefail\n" + script], capture_output=True, text=True,
                              timeout=timeout, env={"PATH": str(self.bin), "LC_ALL": "C", "HOME": str(self.t)})


@unittest.skipUnless(BASH, "needs bash")
class InitSystem(Base):
    """Slackware's PID 1 is sysvinit's, but its services are rc.d's."""

    def init(self, distro, comm, rc_service=False):
        if rc_service:
            exe(self.bin / "rc-service", "#!/bin/sh\n")
        r = self.bash(f"distro={distro}\n{function(ADOPT, 'init_system')}\ninit_system {comm}\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_slackware_is_rc_d(self):
        self.assertEqual(self.init("slackware", "init"), "rc.d")

    def test_others_are_as_before(self):
        self.assertEqual(self.init("devuan", "init"), "sysv")
        self.assertEqual(self.init("gentoo", "init", rc_service=True), "openrc")
        self.assertEqual(self.init("debian", "systemd"), "systemd")
        self.assertEqual(self.init("artix", "s6-svscan"), "s6")
        self.assertEqual(self.init("void", "runit"), "runit")


@unittest.skipUnless(BASH, "needs bash")
class StartRcD(Base):
    """start_service under rc.d: the first rc.NAME there is, made executable
    (rc.M starts it at boot), restarted, or started when restart fails."""

    def setUp(self):
        super().setUp()
        self.rc = self.t / "rc.d"
        self.rc.mkdir()
        self.log = self.t / "log"

    def rc_script(self, name, restart_rc=0, start_rc=0):
        f = self.rc / f"rc.{name}"
        f.write_text(f"""#!/bin/sh
echo "{name} $1" >>"{self.log}"
case $1 in restart) exit {restart_rc} ;; start) exit {start_rc} ;; esac
""")
        os.chmod(f, 0o644)
        return f

    def start(self, *names):
        lib = function(ADOPT, "start_service")
        return self.bash(f'init=rc.d\nRC_D="{self.rc}"\n{lib}\nstart_service {" ".join(names)}\n')

    def test_restart_makes_it_executable(self):
        f = self.rc_script("sshd")
        r = self.start("ssh", "sshd")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.log.read_text().split("\n")[:-1], ["sshd restart"])
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o755)

    def test_start_when_none_runs(self):
        # rc.sshd restart fails when no listener runs.
        self.rc_script("sshd", restart_rc=1)
        r = self.start("ssh", "sshd")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.log.read_text().split("\n")[:-1], ["sshd restart", "sshd start"])

    def test_the_second_name(self):
        self.rc_script("messagebus")
        r = self.start("dbus", "messagebus")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.log.read_text().split("\n")[:-1], ["messagebus restart"])

    def test_none_starts(self):
        self.rc_script("avahidaemon", restart_rc=1, start_rc=1)
        r = self.start("avahi-daemon", "avahidaemon")
        self.assertEqual(r.returncode, 1)
        self.assertIn("couldn't start any of: avahi-daemon avahidaemon", r.stderr)

    def test_none_there(self):
        r = self.start("avahi-daemon", "avahidaemon")
        self.assertEqual(r.returncode, 1)
        self.assertIn("couldn't start any of", r.stderr)


@unittest.skipUnless(BASH, "needs bash")
class PkgInstallArgs(unittest.TestCase):
    """Every pkg_install call names Slackware's packages too, the 11th."""

    def test_each_call_has_eleven(self):
        text = ADOPT.read_text().replace("\\\n", " ")
        calls = [l for l in text.splitlines() if re.search(r"(^\s*|\|\| *|; *)pkg_install ", l) and "pkg_install()" not in l]
        self.assertGreaterEqual(len(calls), 4)
        for l in calls:
            words = shlex.split(l[l.index("pkg_install ") + len("pkg_install "):].split(";")[0])
            self.assertEqual(len(words), 11, l)

    def test_slackware_goes_to_slk_install(self):
        lib = function(ADOPT, "pkg_install")
        script = f"""set -euo pipefail
distro=slackware
slk_install() {{ echo "slk_install $*"; }}
{lib}
pkg_install a b c d e f g h i j "avahi libdaemon nss-mdns dbus"
"""
        r = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=30, env={"PATH": "/nonexistent"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "slk_install avahi libdaemon nss-mdns dbus")


@unittest.skipUnless(BASH and shutil.which("python3"), "needs bash and python3")
class SlkInstall(Base):
    """installpkg from the tree this system was installed from, never
    slackpkg: the tree whose every package installed here is at its version."""

    PIN = {"a": ["aaa_base-15.1-x86_64-3", "dbus-1.16.4-x86_64-1"],
           "l": ["avahi-0.9_rc5-x86_64-1", "libdaemon-0.14-x86_64-2"],
           "n": ["nss-mdns-0.15.1-x86_64-2", "openssh-10.6p2-x86_64-1"]}
    OLD = {"a": ["aaa_base-15.0-x86_64-3", "dbus-1.12.20-x86_64-5"],
           "n": ["openssh-8.8p1-x86_64-6"]}
    INSTALLED = ["aaa_base-15.1-x86_64-3", "dbus-1.16.4-x86_64-1", "libdaemon-0.14-x86_64-2",
                 "openssh-10.6p2-x86_64-1", "mytool-1.0-x86_64-1_SBo"]

    def setUp(self):
        super().setUp()
        self.cache = self.t / "slackware-cache"
        self.pkgs = self.t / "pkgtools" / "packages"
        self.pkgs.mkdir(parents=True)
        for p in self.INSTALLED:
            (self.pkgs / p).write_text("PACKAGE NAME: " + p + "\n")
        self.pin = self.tree("current-20261121", self.PIN)
        self.old = self.tree("slackware64-15.0", self.OLD)
        self.log = self.t / "installpkg.log"
        exe(self.bin / "installpkg", f'#!/bin/sh\necho "$@" >>"{self.log}"\n')
        exe(self.bin / "slackpkg", f'#!/bin/sh\necho "slackpkg $*" >>"{self.log}"\nexit 1\n')

    def tree(self, name, series, extra=True):
        t = self.cache / name
        lines = ["These are the MD5 message digests for the files in this directory.", ""]
        for s, pkgs in series.items():
            d = t / "slackware64" / s
            d.mkdir(parents=True)
            for p in pkgs:
                f = d / f"{p}.txz"
                f.write_bytes(p.encode())
                lines.append(f"{hashlib.md5(p.encode()).hexdigest()}  ./slackware64/{s}/{p}.txz")
                lines.append(f"{hashlib.md5(b'asc').hexdigest()}  ./slackware64/{s}/{p}.txz.asc")
        if extra:
            # Other versions out of slackware64/: never the tree's.
            lines.append(f"{'0' * 32}  ./pasture/openssh-9.0p1-x86_64-1.txz")
            lines.append(f"{'0' * 32}  ./patches/packages/dbus-1.12.28-x86_64-1_slack15.0.txz")
            lines.append(f"{'0' * 32}  ./extra/avahi-0.1-x86_64-1.txz")
        (t / "CHECKSUMS.md5").write_text("\n".join(lines) + "\n")
        return t

    def install(self, *names):
        lib = "\n".join(function(ADOPT, n) for n in ("slk_tree", "slk_mismatch", "slk_install"))
        return self.bash(f'SLK_CACHE="{self.cache}"\nSLK_PKGS="{self.pkgs}"\n{lib}\nslk_install {" ".join(names)}\n')

    def installed(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_from_the_pin_only_what_is_missing(self):
        r = self.install("avahi", "libdaemon", "nss-mdns", "dbus")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.installed(), [f"{self.pin}/slackware64/l/avahi-0.9_rc5-x86_64-1.txz",
                                            f"{self.pin}/slackware64/n/nss-mdns-0.15.1-x86_64-2.txz"])
        self.assertIn(f"installing avahi nss-mdns from {self.pin}", r.stdout)

    def test_the_tree_it_came_from(self):
        # A 15.0 install: the pin, looked at first, is passed over, saying why.
        for p in self.pkgs.iterdir():
            p.unlink()
        for p in ("aaa_base-15.0-x86_64-3", "openssh-8.8p1-x86_64-6"):
            (self.pkgs / p).write_text("")
        r = self.install("dbus")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.installed(), [f"{self.old}/slackware64/a/dbus-1.12.20-x86_64-5.txz"])
        self.assertIn(f"not {self.pin}: this system has aaa_base-15.0-x86_64-3, the tree aaa_base-15.1-x86_64-3", r.stderr)

    def test_all_there_looks_at_no_tree(self):
        shutil.rmtree(self.cache)
        r = self.install("openssh", "dbus")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.installed(), [])

    def test_a_package_from_elsewhere_matches_no_tree(self):
        # An upgrade from the live mirror: the system is off its pin.
        (self.pkgs / "openssh-10.6p2-x86_64-1").rename(self.pkgs / "openssh-10.7p1-x86_64-1")
        r = self.install("avahi")
        self.assertEqual(r.returncode, 1)
        self.assertIn("this system has openssh-10.7p1-x86_64-1, the tree openssh-10.6p2-x86_64-1", r.stderr)
        self.assertIn("no tree in", r.stderr)
        self.assertIn("by hand", r.stderr)
        self.assertEqual(self.installed(), [], "installpkg or slackpkg ran")

    def test_a_file_that_fails_its_md5(self):
        (self.pin / "slackware64/l/avahi-0.9_rc5-x86_64-1.txz").write_bytes(b"cut short")
        r = self.install("avahi")
        self.assertEqual(r.returncode, 1)
        self.assertIn("doesn't match", r.stderr)
        self.assertEqual(self.installed(), [])

    def test_a_series_not_fetched(self):
        (self.pin / "slackware64/n/nss-mdns-0.15.1-x86_64-2.txz").unlink()
        r = self.install("nss-mdns")
        self.assertEqual(r.returncode, 1)
        self.assertIn("lacks ./slackware64/n/nss-mdns-0.15.1-x86_64-2.txz", r.stderr)

    def test_a_name_the_tree_lacks(self):
        r = self.install("nosuchpkg")
        self.assertEqual(r.returncode, 1)
        self.assertIn("has no package nosuchpkg", r.stderr)

    def test_a_name_is_a_whole_name(self):
        # "avahi" is not "avahi-something", nor the other way round.
        (self.pkgs / "avahi-ui-0.1-x86_64-1").write_text("")
        r = self.install("avahi")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.installed(), [f"{self.pin}/slackware64/l/avahi-0.9_rc5-x86_64-1.txz"])


@unittest.skipUnless(BASH, "needs bash")
class SlkHostname(Base):
    """/etc/HOSTNAME, fully qualified, as rc.M reads it; the domain kept."""

    def setUp(self):
        super().setUp()
        self.etc = self.t / "etc"
        self.etc.mkdir()
        (self.t / "proc/sys/kernel").mkdir(parents=True)
        self.kernel = self.t / "proc/sys/kernel/hostname"
        self.kernel.write_text("darkstar\n")
        (self.t / "uc").mkdir()
        self.upper = self.t / "uc" / "HOSTNAME"

    def run_it(self):
        # /etc/HOSTNAME elsewhere: macOS's file system takes it for /etc/hostname.
        lib = function(ADOPT, "slk_hostname", [("/etc/HOSTNAME", str(self.upper)), ("/etc/", f"{self.etc}/"),
                                               ("/proc/sys/kernel/hostname", str(self.kernel))])
        return self.bash(f"SOURCE_HOST=thinkpad\n{lib}\nslk_hostname\n")

    def test_as_slackware_bootstrap_left_it(self):
        self.upper.write_text("thinkpad.localdomain\n")
        os.symlink(self.upper, self.etc / "hostname")
        hosts = "127.0.0.1\t\tlocalhost thinkpad.localdomain thinkpad\n::1\t\tlocalhost\n"
        (self.etc / "hosts").write_text(hosts)
        before = self.upper.stat().st_mtime_ns
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.upper.stat().st_mtime_ns, before, "an unchanged /etc/HOSTNAME was written")
        self.assertTrue((self.etc / "hostname").is_symlink())
        self.assertEqual((self.etc / "hosts").read_text(), hosts)
        self.assertEqual(self.kernel.read_text(), "thinkpad.localdomain\n")

    def test_another_name_keeps_the_domain(self):
        self.upper.write_text("darkstar.example.net\n")
        os.symlink(self.upper, self.etc / "hostname")
        (self.etc / "hosts").write_text("127.0.0.1\t\tlocalhost\n# 127.0.0.1 thinkpad.example.net\n")
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.upper.read_text(), "thinkpad.example.net\n")
        self.assertTrue((self.etc / "hostname").is_symlink())
        self.assertEqual((self.etc / "hosts").read_text().splitlines()[-1], "127.0.0.1\t\tthinkpad.example.net thinkpad")
        self.assertEqual(self.kernel.read_text(), "thinkpad.example.net\n")

    def test_none_yet(self):
        (self.etc / "hostname").write_text("darkstar\n")
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.upper.read_text(), "thinkpad.localdomain\n")
        self.assertEqual((self.etc / "hostname").read_text(), "thinkpad\n")
        self.assertIn("thinkpad.localdomain thinkpad", (self.etc / "hosts").read_text())


@unittest.skipUnless(BASH, "needs bash")
class SlkMdns(Base):
    """avahi's user and group, rc.messagebus executable, nss-mdns asked."""

    def setUp(self):
        super().setUp()
        self.etc = self.t / "etc"
        self.rc = self.etc / "rc.d"
        self.rc.mkdir(parents=True)
        self.log = self.t / "log"
        for tool in ("groupadd", "useradd"):
            exe(self.bin / tool, f'#!/bin/sh\necho "{tool} $*" >>"{self.log}"\n')
        (self.rc / "rc.messagebus").write_text("#!/bin/sh\n")
        os.chmod(self.rc / "rc.messagebus", 0o644)

    def run_it(self):
        lib = function(ADOPT, "slk_mdns", [("/etc/", f"{self.etc}/")])
        return self.bash(f'RC_D="{self.rc}"\n{lib}\nslk_mdns\n')

    def test_a_system_without_them(self):
        (self.etc / "passwd").write_text("root:x:0:0::/root:/bin/bash\n")
        (self.etc / "group").write_text("root::0:\n")
        (self.etc / "nsswitch.conf").write_text("passwd: files\nhosts: files dns\nnetworks: files\n")
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.log.read_text().splitlines(),
                         ["groupadd -g 214 avahi", "useradd -u 214 -g avahi -d /dev/null -s /bin/false -c User for avahi avahi"])
        self.assertEqual((self.etc / "nsswitch.conf").read_text(),
                         "passwd: files\nhosts:          files mdns4_minimal [NOTFOUND=return] dns mdns4\nnetworks: files\n")
        self.assertEqual(stat.S_IMODE((self.rc / "rc.messagebus").stat().st_mode), 0o755)
        self.assertFalse((self.etc / "nsswitch.conf.bs1").exists())

    def test_current_as_shipped_changes_nothing(self):
        (self.etc / "passwd").write_text("avahi:x:214:214:User for avahi:/dev/null:/bin/false\n")
        (self.etc / "group").write_text("avahi:x:214:\n")
        ns = "hosts:          files mdns4_minimal [NOTFOUND=return] dns mdns4\n"
        (self.etc / "nsswitch.conf").write_text(ns)
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.log.exists())
        self.assertEqual((self.etc / "nsswitch.conf").read_text(), ns)
        self.assertEqual(r.stdout, "")


@unittest.skipUnless(BASH, "needs bash")
class Common(Base):
    """bs1-common on Slackware: the host name up to its first dot, and the
    package list from pkgtools' database, not Slackware's rpm."""

    def test_this_host_is_the_short_name(self):
        (self.t / "hostname").write_text("thinkpad.localdomain\n")
        lib = function(BS1 / "bs1-common", "this_host", [("/proc/sys/kernel/hostname", str(self.t / "hostname"))])
        for name in ("thinkpad.localdomain", "thinkpad"):
            (self.t / "hostname").write_text(name + "\n")
            r = self.bash(f"{lib}\nthis_host\n")
            self.assertEqual(r.stdout, "thinkpad\n", r.stderr)

    def test_package_list_is_pkgtools(self):
        pk = self.t / "pkgtools" / "packages"
        pk.mkdir(parents=True)
        for p in ("zlib-1.3.1-x86_64-1", "aaa_base-15.1-x86_64-3", "Cython-3.3.1-x86_64-1"):
            (pk / p).write_text("")
        exe(self.bin / "rpm", "#!/bin/sh\necho rpm-pkg-1.0\n")
        lib = function(BS1 / "bs1-common", "package_list", [("/var/lib/pkgtools", str(self.t / "pkgtools"))])
        r = self.bash(f"{lib}\npackage_list\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["Cython-3.3.1-x86_64-1", "aaa_base-15.1-x86_64-3", "zlib-1.3.1-x86_64-1"])

    def test_qemu_conf_takes_the_short_name(self):
        self.assertIn('splitlines()[-1].split(".")[0]', (BS1 / "bs1-qemu.py").read_text())


@unittest.skipUnless(BASH, "needs bash")
class RcM(Base):
    """bs1-verify waits for rc.M, which goes on after sshd is up."""

    def setUp(self):
        super().setUp()
        self.proc = self.t / "proc"
        self.rc_m = self.t / "etc/rc.d/rc.M"
        self.rc_m.parent.mkdir(parents=True)
        self.rc_m.write_text("")
        self.ps(1, ["init"])

    def ps(self, pid, argv):
        d = self.proc / str(pid)
        d.mkdir(parents=True)
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")

    def lib(self):
        v = BS1 / "bs1-verify"
        rc = function(v, "rc_m_running", [("/proc/[0-9]*", f"{self.proc}/[0-9]*")])
        return f'RC_M="{self.rc_m}"\n{rc}\n{function(v, "system_settled")}\n'

    def test_running(self):
        self.ps(40, ["/bin/sh", str(self.rc_m), "multi"])
        r = self.bash(f"{self.lib()}\nrc_m_running && echo running\n")
        self.assertEqual(r.stdout, "running\n", r.stderr)

    def test_its_own_grep_doesnt_count(self):
        # grep's command line has the pattern, escaped.
        esc = str(self.rc_m).replace(".", "\\.")
        self.ps(41, ["grep", "-qsE", "-e", esc, f"{self.proc}/1/cmdline"])
        r = self.bash(f"{self.lib()}\nsystem_settled\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "rc.M-finished\n")

    def test_without_rc_m_as_before(self):
        self.rc_m.unlink()
        r = self.bash(f"{self.lib()}\nsystem_settled\n")
        self.assertEqual(r.stdout, "no-systemd\n", r.stderr)


if __name__ == "__main__":
    unittest.main()
