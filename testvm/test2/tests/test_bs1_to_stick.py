"""bs1-to-stick stops before any boot variable changes unless the stick's
BACKOTTER partition has the SSH keys that make it start sshd
(stick/layer/usr/lib/backotter/key-access): a reflashed stick had none,
booted with SSH off, and the controller waited 7 min for nothing, twice
(2026-11-21).  No root, no firmware: the whole script runs against a fake
bs1-common, /sys under a temporary directory, and fake blkid, lsblk, mount,
umount and efibootmgr.  Needs bash 4 and ssh-keygen (Linux hosts)."""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
BS1 = TESTS.parent.parent / "realworld" / "backshot1"


def modern_bash():
    for b in ("bash", "/usr/local/bin/bash", "/opt/homebrew/bin/bash"):
        try:
            out = subprocess.run([b, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0 and out.stdout.strip().isdigit() and int(out.stdout.strip()) >= 4:
            return b
    return None


BASH = modern_bash()
KEYGEN = shutil.which("ssh-keygen")


def exe(path, text):
    Path(path).write_text(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def keygen(path, comment):
    subprocess.run([KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(path)], check=True)


@unittest.skipUnless(BASH and KEYGEN and shutil.which("readlink"), "needs bash 4 and ssh-keygen")
class ToStick(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="bs1-to-stick-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        t = self.t
        # The controller's keys (make-keys), and a stranger's.
        self.ctl = t / "ctl"
        self.ctl.mkdir()
        keygen(self.ctl / "id_ed25519", "backshot1-controller")
        keygen(self.ctl / "ssh_host_ed25519_key", "root@bs1-thinkpad")
        keygen(self.ctl / "other", "someone-else")
        # The drive's backshot1/, as `backshot1 stage` leaves it.
        self.here = t / "backshot1"
        (self.here / "keys").mkdir(parents=True)
        shutil.copy(self.ctl / "id_ed25519.pub", self.here / "keys" / "authorized_keys")
        shutil.copy(self.ctl / "ssh_host_ed25519_key", self.here / "keys")
        shutil.copy(self.ctl / "ssh_host_ed25519_key.pub", self.here / "keys")
        text = (BS1 / "bs1-to-stick").read_text().replace("/sys/", f"{t}/sys/")
        exe(self.here / "bs1-to-stick", text)
        (self.here / "bs1-common").write_text(f"""set -uo pipefail
HERE={self.here}
LOG=/dev/null STEP=to-stick INTERNAL_SERIAL=X
say() {{ printf '%s\\n' "$*"; }}
die() {{ say "STOPPED: $*"; exit 1; }}
internal_disk() {{ echo nvme0n1; }}
disk_of() {{ echo sdz; }}
""")
        (t / "sys/firmware/efi/efivars").mkdir(parents=True)
        (t / "sys/class/block/sdz2").mkdir(parents=True)
        (t / "sys/class/block/sdz2/partition").write_text("2\n")
        # The stick's BACKOTTER, as the reflash left it, keys added by each test.
        self.part = t / "backotter-part" / "backotter"
        self.part.mkdir(parents=True)
        (self.part / "settings.toml").write_text("")
        (t / "tmp").mkdir()
        b = self.bin = t / "bin"
        b.mkdir()
        exe(b / "blkid", '#!/bin/sh\n[ "$*" = "-L BACKOTTER" ] && echo /dev/sdz3\n')
        exe(b / "lsblk", """#!/bin/sh
case "$*" in
"-dno TRAN /dev/sdz") echo usb ;;
"-lnpo NAME,FSTYPE,LABEL /dev/sdz") printf '/dev/sdz1 iso9660 BKO\\n/dev/sdz2 vfat ESP\\n/dev/sdz3 vfat BACKOTTER\\n' ;;
*) exit 1 ;;
esac
""")
        exe(b / "findmnt", "#!/bin/sh\nexit 0\n")
        exe(b / "mount", f"""#!/bin/sh
echo "mount $*" >>{t}/mounts
for a; do dev=$tgt; tgt=$a; done
case $dev in
/dev/sdz3) cp -R {t}/backotter-part/. "$tgt"/ ;;
/dev/sdz2) mkdir -p "$tgt/EFI/BOOT" && : >"$tgt/EFI/BOOT/BOOTX64.EFI" ;;
*) exit 32 ;;
esac
""")
        exe(b / "umount", f'#!/bin/sh\necho "umount $*" >>{t}/mounts\nfind "$1" -mindepth 1 -delete\n')
        exe(b / "efibootmgr", f"""#!/bin/sh
[ $# -eq 0 ] || echo "$*" >>{t}/efi.log
case "$*" in *--create*) : >{t}/created ;; esac
echo "BootCurrent: 0001"
echo "BootOrder: 0001"
echo "Boot0001* Linux"
[ ! -e {t}/created ] || echo "Boot0002* BKO rescue"
""")
        for stub in ("systemctl", "reboot", "sleep"):
            exe(b / stub, "#!/bin/sh\nexit 0\n")

    def stick_keys(self, auth=True, host=True, auth_text=None, host_from=None):
        if auth:
            text = auth_text if auth_text is not None else (self.ctl / "id_ed25519.pub").read_text()
            (self.part / "authorized_keys").write_text(text)
        if host:
            shutil.copy(host_from or self.ctl / "ssh_host_ed25519_key", self.part / "ssh_host_ed25519_key")
            # FAT has no modes: anyone may read it.
            os.chmod(self.part / "ssh_host_ed25519_key", 0o644)

    def run_it(self):
        env = {"PATH": f"{self.bin}:/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "TMPDIR": str(self.t / "tmp")}
        return subprocess.run([BASH, str(self.here / "bs1-to-stick")], capture_output=True, text=True,
                              timeout=30, env=env)

    def efi_calls(self):
        p = self.t / "efi.log"
        return p.read_text() if p.exists() else ""

    def assert_clean(self):
        mounts = (self.t / "mounts").read_text().splitlines()
        self.assertEqual(sum(m.startswith("mount ") for m in mounts),
                         sum(m.startswith("umount ") for m in mounts), mounts)
        self.assertIn("mount -t vfat -o ro /dev/sdz3", (self.t / "mounts").read_text())
        self.assertEqual(list((self.t / "tmp").iterdir()), [], "temporary files left")

    def assert_stopped(self, r, *why):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.efi_calls(), "", "efibootmgr changed something")
        self.assertIn("BootNext is left alone", r.stdout)
        self.assertIn("stick/add-ssh-key.sh --host-key ~/.ssh/backshot1/ssh_host_ed25519_key /dev/<stick> "
                      "~/.ssh/backshot1/id_ed25519.pub", r.stdout)
        self.assertIn("/dev/sdz", r.stdout)
        for w in why:
            self.assertIn(w, r.stdout)
        self.assert_clean()

    def test_keys_present_proceeds(self):
        self.stick_keys()
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("has the SSH keys", r.stdout)
        self.assertNotIn("not checking", r.stdout)
        calls = self.efi_calls()
        self.assertIn("--create --disk /dev/sdz --part 2 --label BKO rescue", calls)
        self.assertIn("--bootnext 0002", calls)
        self.assertIn("rebooting into the stick", r.stdout)
        self.assert_clean()

    def test_windows_line_ends_and_comments_count(self):
        pub = (self.ctl / "id_ed25519.pub").read_text().strip()
        self.stick_keys(auth_text=f"# BackShot1\r\n{pub}\r\n")
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("--bootnext", self.efi_calls())

    def test_no_authorized_keys_stops_before_efibootmgr(self):
        self.stick_keys(auth=False)
        self.assert_stopped(self.run_it(), "no backotter/authorized_keys with a public key in it")

    def test_authorized_keys_without_a_key_line_stops(self):
        self.stick_keys(auth_text="# nothing here\n\n")
        self.assert_stopped(self.run_it(), "no backotter/authorized_keys with a public key in it")

    def test_no_host_key_stops(self):
        self.stick_keys(host=False)
        r = self.run_it()
        self.assert_stopped(r, "no backotter/ssh_host_ed25519_key holding an OpenSSH private key")
        self.assertNotIn("authorized_keys with", r.stdout)

    def test_reflashed_stick_names_both(self):
        self.stick_keys(auth=False, host=False)
        self.assert_stopped(self.run_it(), "no backotter/authorized_keys", "no backotter/ssh_host_ed25519_key")

    def test_not_the_controllers_key_stops(self):
        self.stick_keys(auth_text=(self.ctl / "other.pub").read_text())
        self.assert_stopped(self.run_it(), "hasn't the controller's key")

    def test_not_the_pinned_host_key_stops(self):
        self.stick_keys(host_from=self.ctl / "other")
        self.assert_stopped(self.run_it(), "isn't the host key the controller pins")

    def test_no_keys_on_the_drive_checks_only_presence(self):
        shutil.rmtree(self.here / "keys")
        self.stick_keys(auth_text=(self.ctl / "other.pub").read_text(), host_from=self.ctl / "other")
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("not checking that the stick lets the controller's key in", r.stdout)
        self.assertIn("not checking the stick's host key against the pinned one", r.stdout)
        self.assertIn("--bootnext", self.efi_calls())


if __name__ == "__main__":
    unittest.main()
