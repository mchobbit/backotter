"""bs1-to-stick stops before any boot variable changes unless the stick's
BACKOTTER partition has the SSH keys that make it start sshd
(stick/layer/usr/lib/backotter/key-access): a reflashed stick had none,
booted with SSH off, and the controller waited 7 min for nothing, twice
(2026-11-21).  No root, no firmware: the whole script runs against a fake
bs1-common, /sys under a temporary directory, and fake blkid, lsblk, mount,
umount, efibootmgr and python3 (for bs1-efi).  Needs bash (3.2, the Mac's,
will do) and ssh-keygen.  Its reboot can't reach the machine: it runs in the
background, after the script has exited, so the copy here calls the fake
systemctl and reboot by path, PATH has no directory of the system's (only
the fakes, and the system's tools less every one that reboots, halts or
powers off), and each test waits for it before its fakes go (it rebooted
the Thinkpad, run as root there, 2026-11-22; no bs1 test runs as root
now)."""

import os
import shutil
import stat
import subprocess
import tempfile
import time
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


def newest_bash():
    """The newest bash here: the laptops run 5, but the script keeps to 3.2."""
    best = None
    for b in ("/opt/homebrew/bin/bash", "/usr/local/bin/bash", "bash", "/bin/bash"):
        try:
            out = subprocess.run([b, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        v = out.stdout.strip()
        if out.returncode == 0 and v.isdigit() and (best is None or int(v) > best[0]):
            best = (int(v), b)
    return best and best[1]


BASH = newest_bash()
KEYGEN = shutil.which("ssh-keygen")
# What the script could reboot, halt or power off with; never on its PATH.
NEVER = {"systemctl", "reboot", "shutdown", "halt", "poweroff", "init", "telinit", "kexec", "loginctl",
         "efibootmgr", "launchctl", "pmset"}
REBOOT = "(sleep 2; systemctl reboot -i || reboot) >/dev/null 2>&1 </dev/null &"


def system_tools(into):
    """INTO: a link to each of the system's tools, but none of NEVER."""
    into.mkdir()
    for d in ("/usr/sbin", "/usr/bin", "/sbin", "/bin"):
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            src = os.path.join(d, n)
            if n in NEVER or (into / n).exists() or not os.access(src, os.X_OK) or os.path.isdir(src):
                continue
            os.symlink(src, into / n)


def exe(path, text):
    Path(path).write_text(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def keygen(path, comment):
    subprocess.run([KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(path)], check=True)


@unittest.skipUnless(BASH and KEYGEN and shutil.which("readlink"), "needs bash and ssh-keygen")
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
        b = self.bin = t / "bin"
        b.mkdir()
        self.sys_bin = t / "sysbin"
        system_tools(self.sys_bin)
        text = (BS1 / "bs1-to-stick").read_text().replace("/sys/", f"{t}/sys/")
        # The reboot, by the fakes' paths: gone with them, it is "not found".
        self.assertIn(REBOOT, text)
        text = text.replace(REBOOT, REBOOT.replace("systemctl reboot", f"{b}/systemctl reboot")
                            .replace("|| reboot", f"|| {b}/reboot"))
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
        exe(b / "blkid", '#!/bin/sh\n[ "$*" = "-L BACKOTTER" ] && echo /dev/sdz3\n')
        exe(b / "lsblk", """#!/bin/sh
case "$*" in
"-dno TRAN /dev/sdz") echo usb ;;
"-lnpo NAME,FSTYPE,LABEL /dev/sdz") printf '/dev/sdz1 iso9660 BKO\\n/dev/sdz2 vfat ESP\\n/dev/sdz3 vfat BACKOTTER\\n' ;;
*) exit 1 ;;
esac
""")
        # BACKOTTER is mounted already (a desktop's udisks) where mounted-at says.
        exe(b / "findmnt", f"""#!/bin/sh
case "$*" in
*"-S /dev/sdz3"*) [ -e {t}/mounted-at ] || exit 1; cat {t}/mounted-at ;;
esac
exit 0
""")
        exe(b / "mount", f"""#!/bin/sh
echo "mount $*" >>{t}/mounts
for a; do dev=$tgt; tgt=$a; done
case $dev in
/dev/sdz3) [ ! -e {t}/mount-fails ] || exit 32; cp -R {t}/backotter-part/. "$tgt"/ ;;
/dev/sdz2) mkdir -p "$tgt/EFI/BOOT" && : >"$tgt/EFI/BOOT/BOOTX64.EFI" ;;
*) exit 32 ;;
esac
""")
        exe(b / "umount", f"""#!/bin/sh
echo "umount $*" >>{t}/mounts
# Busy, the first time only.
if [ -e {t}/umount-fails-once ]; then rm {t}/umount-fails-once; exit 32; fi
find "$1" -mindepth 1 -delete
""")
        exe(b / "python3", f"""#!/bin/sh
echo "$*" >>{t}/efivarfs.log
case "$*" in *" list") echo "BootNext: 0002"; echo "BootOrder: 0001" ;; esac
""")
        exe(b / "efibootmgr", f"""#!/bin/sh
[ $# -eq 0 ] || echo "$*" >>{t}/efi.log
case "$*" in *--create*) : >{t}/created ;; esac
echo "BootCurrent: 0001"
echo "BootOrder: 0001"
echo "Boot0001* Linux"
[ ! -e {t}/created ] || echo "Boot0002* BKO rescue"
""")
        for stub in ("systemctl", "reboot", "sleep"):
            exe(b / stub, f'#!/bin/sh\necho "{stub} $*" >>{t}/reboot.log\n')

    def stick_keys(self, auth=True, host=True, auth_text=None, host_from=None):
        if auth:
            text = auth_text if auth_text is not None else (self.ctl / "id_ed25519.pub").read_text()
            (self.part / "authorized_keys").write_text(text)
        if host:
            shutil.copy(host_from or self.ctl / "ssh_host_ed25519_key", self.part / "ssh_host_ed25519_key")
            # FAT has no modes: anyone may read it.
            os.chmod(self.part / "ssh_host_ed25519_key", 0o644)

    def run_it(self, **extra):
        env = {"PATH": f"{self.bin}:{self.sys_bin}", "LC_ALL": "C", "TMPDIR": str(self.t / "tmp"), **extra}
        r = subprocess.run([BASH, str(self.here / "bs1-to-stick")], capture_output=True, text=True,
                           timeout=30, env=env)
        if "rebooting into the stick" in r.stdout:
            # The background reboot: waited for, so it runs while the fakes are here.
            for _ in range(100):
                if "systemctl reboot -i" in self.reboots():
                    break
                time.sleep(0.1)
            self.assertIn("systemctl reboot -i", self.reboots(), "the reboot never ran")
        return r

    def reboots(self):
        p = self.t / "reboot.log"
        return p.read_text() if p.exists() else ""

    def efi_calls(self, log="efi.log"):
        p = self.t / log
        return p.read_text() if p.exists() else ""

    def mounts(self):
        p = self.t / "mounts"
        return p.read_text().splitlines() if p.exists() else []

    def assert_no_temp(self):
        self.assertEqual(list((self.t / "tmp").iterdir()), [], "temporary files left")

    def assert_clean(self):
        mounts = self.mounts()
        self.assertEqual(sum(m.startswith("mount ") for m in mounts),
                         sum(m.startswith("umount ") for m in mounts), mounts)
        self.assertIn("mount -t vfat -o ro,nosuid,nodev,noexec /dev/sdz3", mounts[0])
        self.assert_no_temp()

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

    def desktop_mounted(self):
        """BACKOTTER read-write under /run/media, where findmnt -r writes a space as \\x20."""
        at = self.t / "run" / "media" / "m e" / "BACKOTTER"
        shutil.copytree(self.part.parent, at)
        (self.t / "mounted-at").write_text(str(at).replace(" ", "\\x20") + "\n")
        return at

    def test_desktop_mounted_stick_is_read_where_it_is(self):
        self.stick_keys()
        at = self.desktop_mounted()
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"is mounted at {at} already", r.stdout)
        self.assertIn("has the SSH keys", r.stdout)
        self.assertFalse([m for m in self.mounts() if "sdz3" in m or str(at) in m], self.mounts())
        self.assertTrue((at / "backotter" / "authorized_keys").exists(), "the desktop's mount was touched")
        self.assertIn("--bootnext 0002", self.efi_calls())
        self.assert_no_temp()

    def test_desktop_mounted_stick_without_keys_stops(self):
        self.stick_keys(auth=False, host=False)
        at = self.desktop_mounted()
        r = self.run_it()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("BootNext is left alone", r.stdout)
        self.assertEqual(self.efi_calls(), "")
        self.assertFalse([m for m in self.mounts() if "sdz3" in m or str(at) in m], self.mounts())
        self.assertTrue((at / "backotter" / "settings.toml").exists(), "the desktop's mount was touched")
        self.assert_no_temp()

    def test_mount_failure_stops_and_cleans_up(self):
        self.stick_keys()
        (self.t / "mount-fails").touch()
        r = self.run_it()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("can't mount /dev/sdz3, the stick's BACKOTTER partition", r.stdout)
        self.assertEqual(self.efi_calls(), "")
        self.assert_no_temp()

    def test_busy_unmount_stops_and_the_trap_unmounts(self):
        self.stick_keys()
        (self.t / "umount-fails-once").touch()
        r = self.run_it()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("can't unmount /dev/sdz3", r.stdout)
        self.assertEqual(self.efi_calls(), "")
        mounts = self.mounts()
        self.assertEqual(sum(m.startswith("umount ") for m in mounts), 2, mounts)
        self.assert_no_temp()

    def test_efivarfs_no_keys_stops_before_bs1_efi(self):
        self.stick_keys(auth=False, host=False)
        r = self.run_it(BS1_EFI="efivarfs")
        self.assert_stopped(r, "no backotter/authorized_keys", "no backotter/ssh_host_ed25519_key")
        self.assertEqual(self.efi_calls("efivarfs.log"), "", "bs1-efi ran")

    def test_efivarfs_keys_present_proceeds(self):
        self.stick_keys()
        r = self.run_it(BS1_EFI="efivarfs")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.efi_calls(), "")
        self.assertIn(" oneshot BKO rescue /dev/sdz2 \\EFI\\BOOT\\BOOTX64.EFI", self.efi_calls("efivarfs.log"))
        self.assert_clean()


if __name__ == "__main__":
    unittest.main()
