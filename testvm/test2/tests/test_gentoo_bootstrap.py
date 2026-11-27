"""gentoo-bootstrap.sh (testvm/realworld: Gentoo on LUKS on the Thinkpad, the
Lenovo A100, from the rescue stick) and the GENTOO_METAL path it takes
through bootstrap/gentoo.sh: its arguments, the disk it takes by serial and
the others it refuses, the passphrase file's shape, its pins against
make-bootstrap.sh's and backshot1/README.md's, the guest's build unchanged
by GENTOO_METAL, and nothing mounted on /mnt itself.  No root, no stick:
the script stops at its root check, and its functions run from its own
text against a fake lsblk and findmnt.  Nothing here needs more than bash
3.2."""

import os
import re
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

if os.geteuid() == 0:
    # A run past the root check erases a disk.
    raise unittest.SkipTest("gentoo-bootstrap.sh's tests don't run as root")

TESTVM = Path(__file__).resolve().parents[2]
SCRIPT = TESTVM / "realworld" / "gentoo-bootstrap.sh"
QEMU = TESTVM / "realworld" / "gentoo-bootstrap-qemu.sh"
GENTOO_SH = TESTVM / "bootstrap" / "gentoo.sh"
COMMON_SH = TESTVM / "bootstrap" / "common.sh"
README = TESTVM / "realworld" / "backshot1" / "README.md"
BASH = shutil.which("bash")
TEXT = SCRIPT.read_text()


def function(name, text=TEXT):
    """NAME's text from a bash file: one line `NAME() { ...; }`, or from
    `NAME() {` to the first `}` alone on a line."""
    m = (re.search(rf"^{re.escape(name)}\(\) {{ .*; }}\n", text, re.M)
         or re.search(rf"^{re.escape(name)}\(\) {{[^\n]*\n.*?^}}\n", text, re.M | re.S))
    assert m, f"no function {name}"
    return m.group(0)


def pinned(name):
    m = re.search(rf"^{name}=(\S+)$", TEXT, re.M)
    assert m, f"no {name}= in gentoo-bootstrap.sh"
    return m.group(1)


class Arguments(unittest.TestCase):
    """Everything before the root check: a good command line gets as far as
    "run it as root", a bad one stops with its reason first."""

    def run_script(self, *args, env=None):
        e = {k: v for k, v in os.environ.items() if not k.startswith("BKO_")}
        e.update(env or {})
        r = subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, env=e, timeout=30)
        return r.returncode, r.stdout + r.stderr

    def accepted(self, *args, env=None):
        rc, out = self.run_script(*args, env=env)
        self.assertEqual(rc, 1, out)
        self.assertIn("run it as root", out)

    def refused(self, want, *args, env=None):
        rc, out = self.run_script(*args, env=env)
        self.assertEqual(rc, 1, out)
        self.assertNotIn("run it as root", out)
        self.assertIn(want, out)

    def test_good_command_lines(self):
        self.accepted("S7XMNF0Y787118")
        self.accepted("S7XMNF0Y787118", "thinkpad")
        self.accepted("--detach", "S7XMNF0Y787118", "thinkpad")
        self.accepted("--inputs", "/mnt/bs1-backup/backshot1/gentoo", "BKOGENTOOTEST", "gentootest")
        self.accepted("BKOGENTOOTEST", "gentootest", env={"BKO_GENTOO_CMDLINE": "console=ttyS0,115200n8 console=tty0"})

    def test_usage(self):
        for args in ((), ("a", "b", "c"), ("--bogus", "S1"), ("-n", "S1"), ("--inputs",), ("S1", "--inputs")):
            with self.subTest(args=args):
                self.refused("usage: gentoo-bootstrap.sh", *args)

    def test_serial_is_plain(self):
        for s in ("", "a b", "../S1", "S1;reboot", "S1$(x)", "S1/2", "*"):
            with self.subTest(serial=s):
                self.refused("isn't a plain serial number", s)

    def test_hostname_is_plain(self):
        for h in ("Thinkpad", "think_pad", "a-", "a.b", "x" * 64, "a b"):
            with self.subTest(hostname=h):
                self.refused("isn't a plain host name", "S1", h)
        self.accepted("S1", "a" * 63)
        self.accepted("S1", "a1-b2")

    def test_inputs_absolute(self):
        self.refused("--inputs takes an absolute path", "--inputs", "backshot1/gentoo", "S1")

    def test_cmdline_env(self):
        for c in ("quiet; reboot", "a=$(id)", "x\ny", "a`id`"):
            with self.subTest(cmdline=c):
                self.refused("BKO_GENTOO_CMDLINE", "S1", env={"BKO_GENTOO_CMDLINE": c})


class Fakes(unittest.TestCase):
    """find_disk and check_passphrase from the script's text, with a fake
    lsblk and findmnt whose answers come from files in the temporary
    directory."""

    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="gentoo-bs-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.bin = self.t / "bin"
        self.bin.mkdir()
        for tool in ("sh", "cat", "grep", "awk", "wc", "tr", "paste", "tail", "od", "env"):
            p = shutil.which(tool)
            assert p, tool
            os.symlink(p, self.bin / tool)
        # lsblk: -dnrpo NAME,TYPE,SERIAL -> disks; -dno TRAN DEV -> tran.DEV's
        # name; -snlpo NAME,TYPE DEV -> holders.DEV's name (its parents).
        self.exe("lsblk", textwrap.dedent(f"""\
            #!/bin/sh
            d={self.t}
            case "$1" in
            -dnrpo) cat "$d/disks" ;;
            -dno) f=$(echo "$3" | tr / _); cat "$d/tran$f" 2>/dev/null ;;
            -snlpo) f=$(echo "$3" | tr / _); cat "$d/holders$f" 2>/dev/null ;;
            *) echo "fake lsblk: $*" >&2; exit 2 ;;
            esac
            """))
        # findmnt -nro SOURCE --target DRIVE / findmnt -nro SOURCE /cdrom.
        self.exe("findmnt", textwrap.dedent(f"""\
            #!/bin/sh
            d={self.t}
            case "$*" in
            *--target*) cat "$d/drive-source" ;;
            *" /cdrom") [ -e "$d/cdrom-source" ] || exit 1; cat "$d/cdrom-source" ;;
            *) echo "fake findmnt: $*" >&2; exit 2 ;;
            esac
            """))

    def exe(self, name, text):
        p = self.bin / name
        p.write_text(text)
        p.chmod(p.stat().st_mode | stat.S_IXUSR)

    def write(self, name, text):
        (self.t / name).write_text(text)

    def holders(self, dev, text):
        self.write("holders" + dev.replace("/", "_"), text)

    def tran(self, dev, text):
        self.write("tran" + dev.replace("/", "_"), text)

    def call(self, script):
        body = function("die") + function("holder") + function("find_disk") + function("check_passphrase") + script
        r = subprocess.run([BASH, "-c", "set -euo pipefail\n" + body], capture_output=True, text=True,
                           env={"PATH": str(self.bin), "LC_ALL": "C"}, timeout=30)
        return r.returncode, r.stdout, r.stderr

    def thinkpad(self, disks=None):
        """The A100 on the stick: its NVMe, the stick and mybig512 on USB."""
        self.write("disks", disks if disks is not None else textwrap.dedent("""\
            /dev/sda disk 4C530001230817116033
            /dev/sdb disk 20240528004A
            /dev/nvme0n1 disk S7XMNF0Y787118
            /dev/loop0 loop
            /dev/sr0 rom
            """))
        self.tran("/dev/nvme0n1", "nvme\n")
        self.tran("/dev/sda", "usb\n")
        self.tran("/dev/sdb", "usb\n")
        self.write("drive-source", "/dev/sdb1\n")
        self.holders("/dev/sdb1", "/dev/sdb1 part\n/dev/sdb disk\n")
        self.write("cdrom-source", "/dev/sda1\n")
        self.holders("/dev/sda1", "/dev/sda1 part\n/dev/sda disk\n")

    def test_the_thinkpads_disk(self):
        self.thinkpad()
        rc, out, err = self.call('find_disk S7XMNF0Y787118 /mnt/bs1-backup\n')
        self.assertEqual((rc, out), (0, "/dev/nvme0n1\n"), err)

    def test_no_such_serial(self):
        self.thinkpad()
        for s in ("S7XMNF0Y78711", "S7XMNF0Y7871180", "s7xmnf0y787118", "nvme0n1", "disk"):
            with self.subTest(serial=s):
                rc, out, err = self.call(f'find_disk {s} /mnt/bs1-backup\n')
                self.assertEqual(rc, 1)
                self.assertEqual(out, "")
                self.assertIn("not exactly one disk has serial", err)

    def test_two_disks_with_the_serial(self):
        self.thinkpad("/dev/nvme0n1 disk S7XMNF0Y787118\n/dev/nvme1n1 disk S7XMNF0Y787118\n")
        rc, out, err = self.call('find_disk S7XMNF0Y787118 /mnt/bs1-backup\n')
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("found: /dev/nvme0n1 /dev/nvme1n1", err)

    def test_a_disk_with_no_serial_isnt_matched(self):
        self.thinkpad("/dev/vda disk\n/dev/nvme0n1 disk S7XMNF0Y787118\n")
        rc, out, err = self.call('find_disk S7XMNF0Y787118 /mnt/bs1-backup\n')
        self.assertEqual((rc, out), (0, "/dev/nvme0n1\n"), err)
        rc, out, err = self.call('find_disk disk /mnt/bs1-backup\n')
        self.assertEqual(rc, 1)

    def test_refuses_usb_the_drive_and_the_stick(self):
        self.thinkpad()
        rc, out, err = self.call('find_disk 20240528004A /mnt/bs1-backup\n')
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("is a USB disk", err)
        # Not on USB, but the backup drive's (or the stick's) disk all the same.
        self.tran("/dev/sdb", "sata\n")
        rc, out, err = self.call('find_disk 20240528004A /mnt/bs1-backup\n')
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("the backup drive is on /dev/sdb", err)
        self.tran("/dev/sda", "sata\n")
        rc, out, err = self.call('find_disk 4C530001230817116033 /mnt/bs1-backup\n')
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("the stick is on /dev/sda", err)
        # The drive on the target's own disk.
        self.write("drive-source", "/dev/nvme0n1p3\n")
        self.holders("/dev/nvme0n1p3", "/dev/nvme0n1p3 part\n/dev/nvme0n1 disk\n")
        rc, out, err = self.call('find_disk S7XMNF0Y787118 /mnt/bs1-backup\n')
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("the backup drive is on /dev/nvme0n1", err)

    def test_no_cdrom_is_fine(self):
        self.thinkpad()
        (self.t / "cdrom-source").unlink()
        rc, out, err = self.call('find_disk S7XMNF0Y787118 /mnt/bs1-backup\n')
        self.assertEqual((rc, out), (0, "/dev/nvme0n1\n"), err)

    def passphrase(self, data):
        p = self.t / "disk-passphrase"
        p.write_bytes(data)
        return self.call(f'check_passphrase {p} && echo ok\n')

    def test_passphrase_shape(self):
        for good in (b"abcd1234", b"correct horse battery staple!", b"~" * 512, b"P@ss w0rd-#%"):
            with self.subTest(good=good):
                self.assertEqual(self.passphrase(good)[:2], (0, "ok\n"))
        for bad, why in ((b"abcd1234\n", "ends with a newline"), (b"abc\n1234", "a newline or CR"),
                         (b"abcdefg", "is 7 bytes"), (b"", "is 0 bytes"), (b"x" * 513, "is 513 bytes"),
                         ("pässwörd1".encode(), "isn't printable ASCII"), (b"tab\there1", "isn't printable ASCII"),
                         (b"abcd1234\r", "a newline or CR")):
            with self.subTest(bad=bad):
                rc, out, err = self.passphrase(bad)
                self.assertEqual((rc, out), (1, ""))
                self.assertIn(why, err)
                # Never its bytes in the message.
                if bad.strip():
                    self.assertNotIn(bad.decode(errors="replace").strip(), err.replace(str(self.t), ""))


class Pins(unittest.TestCase):
    def test_stage3_is_make_bootstraps(self):
        mb = (TESTVM / "make-bootstrap.sh").read_text()
        date = re.search(r"^GENTOO_STAGE3=(\S+)$", mb, re.M)[1]
        self.assertEqual(pinned("STAGE3"), f"stage3-amd64-systemd-{date}.tar.xz")

    def test_readme_names_the_pins(self):
        """backshot1/README.md's staging list has each pinned file with its SHA-256."""
        readme = README.read_text()
        sec = readme[readme.index("## Gentoo on the A100"):]
        sec = sec[:sec.index("\n## ", 1)]
        stage3 = pinned("STAGE3")
        for name, sha in ((stage3, pinned("STAGE3_SHA256")), (stage3 + ".asc", pinned("STAGE3_ASC_SHA256")),
                          (pinned("RELEASE_KEY"), pinned("RELEASE_KEY_SHA256"))):
            with self.subTest(name=name):
                self.assertRegex(sec, rf"{re.escape(sha)}\s+{re.escape(name)}")
        self.assertIn("gentoo-bootstrap.sh --detach S7XMNF0Y787118 thinkpad", sec)

    def test_rehearsal_stages_what_readme_says(self):
        q = QEMU.read_text()
        self.assertIn('put "$HERE/../bootstrap/gentoo.sh" "$G/gentoo.sh"', q)
        self.assertIn('for f in "$STAGE3" "$STAGE3.asc" gentoo-release-key.gpg', q)
        self.assertIn("gentoo-bootstrap.sh --detach $SERIAL $HOST", q)


class GentooSh(unittest.TestCase):
    def test_nothing_on_mnt_itself(self):
        """gentoo.sh's target is ROOT_MNT (default /mnt, the guest's); the
        bootstrap's every mount is under /run/bko-gentoo, but the drive's."""
        for n, line in enumerate(GENTOO_SH.read_text().splitlines(), 1):
            code = line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
            if "/mnt" in code:
                self.assertEqual(line.split("#")[0].strip(), "ROOT_MNT=${ROOT_MNT:-/mnt}", f"gentoo.sh:{n}: {line}")
        for n, line in enumerate(TEXT.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for m in re.finditer(r"(?<![\w/$])/mnt\S*", line):
                self.assertTrue(m.group(0).startswith("/mnt/bs1-backup"), f"gentoo-bootstrap.sh:{n}: {line}")
        self.assertRegex(TEXT, r"(?m)^MNT=\$STATE/mnt\b")
        self.assertRegex(TEXT, r"(?m)^STATE=/run/bko-gentoo$")
        self.assertRegex(TEXT, r"(?m)^ROOT_MNT=\$MNT/root\b")

    def test_the_bootstraps_check_of_gentoo_sh(self):
        """The two greps gentoo-bootstrap.sh makes of gentoo.sh hold of the real one."""
        m = re.search(r"^if ! grep -q '(.*?)' \"\$GENTOO_SH\" \|\| ! grep -q \"(.*?)\" \"\$GENTOO_SH\"; then$", TEXT, re.M)
        self.assertIsNotNone(m)
        for pat in m.groups():
            r = subprocess.run(["grep", "-q", pat, str(GENTOO_SH)])
            self.assertEqual(r.returncode, 0, pat)

    def test_metal_fill(self):
        """The script's fill of gentoo.sh leaves no placeholder, and parses."""
        filled = GENTOO_SH.read_text().replace("@GENTOO_INIT@", "systemd")
        self.assertEqual(re.findall(r"@[A-Z_]+@", filled), [])
        r = subprocess.run([BASH, "-n"], input=filled, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_guest_fill_unchanged_where_it_matters(self):
        """make-bootstrap.sh's fill (common.sh + gentoo.sh) still parses, and
        the guest's make.conf lines are what they were: ROOT_MNT /mnt,
        GENTOO_METAL no, MAKEOPTS -j4, the key file in crypttab."""
        settings = {"DISTRO": "gentoo-systemd", "ARCH": "amd64", "SERIAL_TTY": "ttyS0", "ROOT_FS": "ext4",
                    "FIRMWARE": "uefi", "LAYOUT": "luks-esp", "LUKS_KEY": "k", "HOSTNAME": "gentoo-systemd",
                    "ROOT_PASSWORD": "p", "ROOT_PASSWORD_HASH": "h", "AUTHORIZED_KEY": "ssh-ed25519 A",
                    "SERVER": "http://10.0.2.2:1", "GENTOO_INIT": "systemd"}
        text = COMMON_SH.read_text() + GENTOO_SH.read_text()
        text = re.sub(r"@([A-Z_]+)@", lambda m: settings[m.group(1)], text)
        r = subprocess.run([BASH, "-n"], input=text, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        g = GENTOO_SH.read_text()
        self.assertIn("GENTOO_METAL=${GENTOO_METAL:-no}", g)
        self.assertIn("    make_jobs=4\n", g)
        self.assertIn("# BackShot1 Thinkpad: binary packages first, UEFI.\"", g)
        self.assertIn("printf 'cryptroot\\tUUID=%s\\t/etc/cryptsetup-keys.d/cryptroot.key\\tluks,discard\\n'", g)
        self.assertIn("printf 'cryptroot\\tUUID=%s\\tnone\\tluks,discard\\n'", g)

    def test_metal_make_conf_heredoc(self):
        """The make.conf block, run for both: the guest's text as before, and
        the metal one with its own comment and the machine's CPUs."""
        g = GENTOO_SH.read_text()
        m = re.search(r"^if \[\[ \$GENTOO_METAL == no \]\]; then\n    made_by=.*?^EOF\n", g, re.M | re.S)
        self.assertIsNotNone(m)
        for metal, want in (("no", 'MAKEOPTS="-j4 -l4"'), ("yes", 'MAKEOPTS="-j7 -l7"')):
            with tempfile.TemporaryDirectory() as d:
                os.makedirs(f"{d}/etc/portage")
                script = (f'GENTOO_METAL={metal}\nROOT_MNT={d}\nnproc() {{ echo 7; }}\n' + m.group(0))
                r = subprocess.run([BASH, "-c", script], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                conf = Path(d, "etc/portage/make.conf").read_text()
            self.assertIn(want, conf)
            self.assertIn('FEATURES="${FEATURES} getbinpkg binpkg-request-signature"', conf)
            if metal == "no":
                self.assertEqual(conf.splitlines()[1:3], [
                    "# BackOtter's gentoo test guest (testvm/bootstrap/gentoo.sh), as the",
                    "# BackShot1 Thinkpad: binary packages first, UEFI."])


if __name__ == "__main__":
    unittest.main()
