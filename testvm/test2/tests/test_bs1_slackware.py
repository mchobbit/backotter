"""BackShot1 on Slackware (the Thinkpad on a pinned slackware64-current,
2026-11-22): bs1-adopt's Slackware arms (rc.d services, installpkg from the
checked, pinned tree on the backup drive, /etc/HOSTNAME, avahi's needs), bs1-common's
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

if os.geteuid() == 0:
    # These run BackShot1's scripts against fakes.  As root, on a real
    # machine, one slip reaches its disks, its firmware or its reboot: a
    # background reboot outlived test_bs1_to_stick's fakes and rebooted the
    # Thinkpad (2026-11-22).
    raise unittest.SkipTest("BackShot1's tests don't run as root")

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
    slackpkg: of the trees checked as slackware-bootstrap.sh checks them (a
    pin bs1-adopt knows, its PINNED.txt, CHECKSUMS.md5 signed by Slackware's
    key), the one whose every package installed here is at its version; each
    package held to its MD5 and its .asc.  gpg and gpgv are fakes: a
    signature is the SHA-256 of what it signs, a key "KEY" and its
    fingerprint."""

    PIN = {"a": ["aaa_base-15.1-x86_64-3", "dbus-1.16.4-x86_64-1"],
           "l": ["avahi-0.9_rc5-x86_64-1", "libdaemon-0.14-x86_64-2"],
           "n": ["nss-mdns-0.15.1-x86_64-2", "openssh-10.6p2-x86_64-1"]}
    OLD = {"a": ["aaa_base-15.0-x86_64-3", "dbus-1.12.20-x86_64-5"],
           "n": ["openssh-8.8p1-x86_64-6"]}
    INSTALLED = ["aaa_base-15.1-x86_64-3", "dbus-1.16.4-x86_64-1", "libdaemon-0.14-x86_64-2",
                 "openssh-10.6p2-x86_64-1", "mytool-1.0-x86_64-1_SBo"]
    KEY = "EC5649DA401E22ABFA6736EF6A4463C040102233"
    REAL_PIN_SHA = "9418aee6f8c5a06d1b051c2d25064d468cc0b19946912fc2898a949ee7fb9917"

    def setUp(self):
        super().setUp()
        for tool in ("sed", "mktemp"):
            os.symlink(shutil.which(tool), self.bin / tool)
        py = shutil.which("python3")
        exe(self.bin / "sha256sum", f"""#!{py} -I
import hashlib, sys
print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest() + "  -")
""")
        # gpg: --import keeps the file as the home's key; --list-keys FPR
        # gives its fingerprint if the key is "KEY FPR"; --export prints it.
        exe(self.bin / "gpg", f"""#!{py} -I
import os, sys
home, a = os.environ["GNUPGHOME"], sys.argv[1:]
k = os.path.join(home, "key")
if "--import" in a:
    data = open(a[-1], "rb").read()
    if not data.startswith(b"KEY "):
        sys.exit(2)
    open(k, "wb").write(data)
elif "--list-keys" in a:
    key = open(k).read().split() if os.path.exists(k) else []
    if key[1:2] != [a[-1]]:
        sys.exit(2)
    print("pub:u:4096:1:6A4463C040102233:")
    print(f"fpr:::::::::{{key[1]}}:")
elif "--export" in a:
    sys.stdout.write(open(k).read())
""")
        exe(self.bin / "gpgv", f"""#!{py} -I
import hashlib, sys
a = sys.argv[1:]
key, sig, data = a[a.index("--keyring") + 1], a[-2], a[-1]
ok = (open(key).read().split()[:2] == ["KEY", "{self.KEY}"]
      and open(sig).read().strip() == hashlib.sha256(open(data, "rb").read()).hexdigest())
sys.exit(0 if ok else 1)
""")
        self.cache = self.t / "slackware-cache"
        self.pkgs = self.t / "pkgtools" / "packages"
        self.pkgs.mkdir(parents=True)
        for p in self.INSTALLED:
            (self.pkgs / p).write_text("PACKAGE NAME: " + p + "\n")
        self.pin = self.tree("current-20261121", self.PIN)
        self.old = self.tree("slackware64-15.0", self.OLD)
        (self.cache / "slackware-key.gpg").write_text(f"KEY {self.KEY}\n")
        self.pin_sha = self.sha(self.pin / "CHECKSUMS.md5")
        self.log = self.t / "installpkg.log"
        exe(self.bin / "installpkg", f'#!/bin/sh\necho "$@" >>"{self.log}"\n')
        exe(self.bin / "slackpkg", f'#!/bin/sh\necho "slackpkg $*" >>"{self.log}"\nexit 1\n')

    @staticmethod
    def sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def sign(self, path):
        Path(f"{path}.asc").write_text(self.sha(path) + "\n")

    def tree(self, name, series, extra=True):
        t = self.cache / name
        lines = ["These are the MD5 message digests for the files in this directory.", ""]
        for s, pkgs in series.items():
            d = t / "slackware64" / s
            d.mkdir(parents=True)
            for p in pkgs:
                f = d / f"{p}.txz"
                f.write_bytes(p.encode())
                self.sign(f)
                lines.append(f"{hashlib.md5(p.encode()).hexdigest()}  ./slackware64/{s}/{p}.txz")
                lines.append(f"{hashlib.md5(Path(f'{f}.asc').read_bytes()).hexdigest()}  ./slackware64/{s}/{p}.txz.asc")
        if extra:
            # Other versions out of slackware64/: never the tree's.
            lines.append(f"{'0' * 32}  ./pasture/openssh-9.0p1-x86_64-1.txz")
            lines.append(f"{'0' * 32}  ./patches/packages/dbus-1.12.28-x86_64-1_slack15.0.txz")
            lines.append(f"{'0' * 32}  ./extra/avahi-0.1-x86_64-1.txz")
        (t / "CHECKSUMS.md5").write_text("\n".join(lines) + "\n")
        self.sign(t / "CHECKSUMS.md5")
        if name.startswith("current-"):
            (t / "GPG-KEY").write_text(f"KEY {self.KEY}\n")
            (t / "PINNED.txt").write_text(f"PIN={name}\nCHECKSUMS_MD5_SHA256={self.sha(t / 'CHECKSUMS.md5')}\n")
        return t

    def install(self, *names, pin_sha=None):
        pin = function(ADOPT, "slk_pin_sha", [(self.REAL_PIN_SHA, pin_sha or self.pin_sha)])
        lib = "\n".join(function(ADOPT, n) for n in ("slk_keyring", "slk_checked", "slk_tree", "slk_mismatch",
                                                      "slk_install"))
        return self.bash(f'SLK_CACHE="{self.cache}"\nSLK_PKGS="{self.pkgs}"\nSLK_KEY={self.KEY}\n'
                         f'{pin}\n{lib}\nslk_install {" ".join(names)}\n')

    def installed(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def assert_none_installed(self, r, *why):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        for w in why:
            self.assertIn(w, r.stderr)
        self.assertEqual(self.installed(), [], "installpkg or slackpkg ran")

    def test_from_the_pin_only_what_is_missing(self):
        r = self.install("avahi", "libdaemon", "nss-mdns", "dbus")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.installed(), [f"{self.pin}/slackware64/l/avahi-0.9_rc5-x86_64-1.txz",
                                            f"{self.pin}/slackware64/n/nss-mdns-0.15.1-x86_64-2.txz"])
        self.assertIn(f"installing avahi nss-mdns from {self.pin}", r.stdout)

    def test_the_real_pin_is_slackware_bootstraps(self):
        boot = (BS1.parent / "slackware-bootstrap.sh").read_text()
        self.assertIn(f"[current-20261121]={self.REAL_PIN_SHA}", boot)
        self.assertIn(f"CHECKSUMS_MD5_SHA256={self.REAL_PIN_SHA}", (BS1.parent / "slackware-current.pin").read_text())
        self.assertIn(f"SLK_KEY={self.KEY}", ADOPT.read_text())

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
        self.assert_none_installed(self.install("avahi"),
                                   "this system has openssh-10.7p1-x86_64-1, the tree openssh-10.6p2-x86_64-1",
                                   "no checked tree in", "by hand")

    def test_an_unfinished_pin_is_passed_over(self):
        # slackware-pin.sh cut short: its .part has CHECKSUMS.md5 first, and
        # would match this system as well as the pin does; it sorts first.
        shutil.copytree(self.pin, self.cache / "current-20261121.part")
        shutil.rmtree(self.pin)
        r = self.install("avahi")
        self.assert_none_installed(r, "current-20261121.part: a pin slackware-pin.sh didn't finish")

    def test_a_pin_bs1_adopt_doesnt_know_is_passed_over(self):
        newer = self.tree("current-20261130", {"l": ["avahi-0.9_rc6-x86_64-1"]})
        shutil.rmtree(self.pin)
        self.assert_none_installed(self.install("avahi"), f"not {newer}: not a pin bs1-adopt knows")

    def test_a_pin_without_pinned_txt(self):
        os.chmod(self.pin, 0o755)
        (self.pin / "PINNED.txt").unlink()
        self.assert_none_installed(self.install("avahi"), f"not {self.pin}: it has no PINNED.txt")

    def test_a_pin_whose_checksums_changed(self):
        self.assert_none_installed(self.install("avahi", pin_sha="0" * 64), "isn't the pinned one")

    def test_checksums_not_signed(self):
        (self.pin / "CHECKSUMS.md5.asc").write_text("forged\n")
        self.assert_none_installed(self.install("avahi"), f"not {self.pin}: its CHECKSUMS.md5 isn't signed")

    def test_a_pin_whose_key_isnt_slackwares(self):
        (self.pin / "GPG-KEY").write_text("KEY 0123456789ABCDEF0123456789ABCDEF01234567\n")
        self.assert_none_installed(self.install("avahi"), "GPG-KEY is not Slackware's key")

    def test_no_gpg_installs_nothing(self):
        (self.bin / "gpgv").unlink()
        self.assert_none_installed(self.install("avahi"), "no gpg and gpgv")

    def test_a_package_whose_asc_fails(self):
        (self.pin / "slackware64/l/avahi-0.9_rc5-x86_64-1.txz.asc").write_text("forged\n")
        self.assert_none_installed(self.install("avahi"), "isn't signed by Slackware's key (its .asc)")

    def test_two_trees_that_match_are_not_guessed_between(self):
        for p in self.pkgs.iterdir():
            p.unlink()
        (self.pkgs / "openssh-10.6p2-x86_64-1").write_text("")
        shutil.rmtree(self.old)
        self.old = self.tree("slackware64-15.0", {"n": ["openssh-10.6p2-x86_64-1", "nss-mdns-0.14-x86_64-1"]})
        self.assert_none_installed(self.install("nss-mdns"), "more than one tree")

    def test_a_file_that_fails_its_md5(self):
        (self.pin / "slackware64/l/avahi-0.9_rc5-x86_64-1.txz").write_bytes(b"cut short")
        self.assert_none_installed(self.install("avahi"), "doesn't match")

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
