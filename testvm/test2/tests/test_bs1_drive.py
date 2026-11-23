"""backshot1's DRIVE, the shell the dispatcher sends ahead of every step to
find the backup drive on the machine, or mount it: at /mnt/bs1-backup, or at
/run/bs1-backup where that can't be made.  openSUSE Aeon's /mnt is on its
read-only root, and every step stopped at "mkdir: cannot create directory
'/mnt/bs1-backup': Read-only file system" (2026-11-23).  No root, no drive:
the text runs under sh with only fakes on its PATH (findmnt, mkdir,
modprobe, mount), which make and mount nothing."""

import os
import re
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
BASH = shutil.which("bash")
LABEL = "bs1testdrive"

FAKES = {
    "findmnt": '#!/bin/sh\necho "findmnt $*" >>"$FAKE_LOG"\n[ -z "$FAKE_MOUNTED" ] || echo "$FAKE_MOUNTED"\n',
    # Makes nothing; refuses what is under one of FAKE_RO's directories.
    "mkdir": """#!/bin/sh
for a; do
    [ "$a" = -p ] && continue
    for ro in $FAKE_RO; do
        case $a in "$ro"/*) echo "mkdir: cannot create directory '$a': Read-only file system" >&2; exit 1 ;; esac
    done
    echo "mkdir $a" >>"$FAKE_LOG"
done
""",
    "modprobe": "#!/bin/sh\nexit 0\n",
    "mount": '#!/bin/sh\necho "mount $*" >>"$FAKE_LOG"\nexit "${FAKE_MOUNT_RC:-0}"\n',
}


def drive_text():
    """DRIVE's value, as backshot1 would send it for LABEL."""
    m = re.search(r"^DRIVE=.*$", (BS1 / "backshot1").read_text(), re.M)
    assert m, "no DRIVE= in backshot1"
    out = subprocess.run([BASH, "-c", f'BACKUP_LABEL={LABEL}\n{m.group(0)}\nprintf %s "$DRIVE"'],
                         capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    return out.stdout


@unittest.skipUnless(BASH, "needs bash")
class Drive(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="bs1-drive-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        # Only the fakes, and head: no real mkdir or mount can be reached.
        self.bin = self.t / "bin"
        self.bin.mkdir()
        for name, text in FAKES.items():
            p = self.bin / name
            p.write_text(text)
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
        os.symlink(shutil.which("head"), self.bin / "head")
        self.log = self.t / "log"
        self.log.write_text("")

    def drive(self, text=None, **env):
        """Runs DRIVE, then prints d: (exit status, stdout, stderr, the fakes' calls)."""
        text = drive_text() if text is None else text
        out = subprocess.run(["/bin/sh", "-c", text + '; echo "d=$d"'], capture_output=True, text=True, timeout=10,
                             env={"PATH": str(self.bin), "FAKE_LOG": str(self.log), **env})
        return out.returncode, out.stdout, out.stderr, self.log.read_text().splitlines()

    def test_mounts_at_mnt(self):
        rc, out, err, calls = self.drive()
        self.assertEqual((rc, out, err), (0, "d=/mnt/bs1-backup\n", ""))
        self.assertEqual(calls[1:], ["mkdir /mnt/bs1-backup", f"mount LABEL={LABEL} /mnt/bs1-backup"])
        self.assertEqual(calls[0], f"findmnt -nro TARGET -S LABEL={LABEL}")

    def test_read_only_mnt_falls_back_to_run(self):
        """Aeon, MicroOS: /mnt can't take a directory, /run can; the first
        mkdir's complaint isn't shown."""
        rc, out, err, calls = self.drive(FAKE_RO="/mnt")
        self.assertEqual((rc, out, err), (0, "d=/run/bs1-backup\n", ""))
        self.assertEqual(calls[1:], ["mkdir /run/bs1-backup", f"mount LABEL={LABEL} /run/bs1-backup"])

    def test_neither_can_be_made(self):
        """Stops, saying why, before any mount."""
        rc, out, err, calls = self.drive(FAKE_RO="/mnt /run")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("/run/bs1-backup", err)
        self.assertNotIn("/mnt/bs1-backup", err)
        self.assertEqual(len(calls), 1, calls)

    def test_mounted_already(self):
        """Wherever something has it (udisks, an earlier step): nothing made, nothing mounted."""
        for at in ("/run/media/user/" + LABEL, "/run/bs1-backup", "/var/mnt/bs1-backup"):
            self.log.write_text("")
            rc, out, err, calls = self.drive(FAKE_MOUNTED=at, FAKE_RO="/mnt /run")
            self.assertEqual((rc, out, err), (0, f"d={at}\n", ""))
            self.assertEqual(len(calls), 1, calls)

    def test_mount_fails(self):
        for ro, at in (("", "/mnt/bs1-backup"), ("/mnt", "/run/bs1-backup")):
            self.log.write_text("")
            rc, out, _, calls = self.drive(FAKE_RO=ro, FAKE_MOUNT_RC="32")
            self.assertEqual((rc, out), (1, ""))
            self.assertEqual(calls[-1], f"mount LABEL={LABEL} {at}")

    def test_inside_single_quotes(self):
        """run_on_drive sends it to the stick as sudo -n bash -c '...'."""
        text = drive_text()
        self.assertNotIn("'", text)
        rc, out, err, _ = self.drive(f"/bin/sh -c '{text}; echo \"inner=$d\"'", FAKE_RO="/mnt")
        self.assertEqual((rc, out, err), (0, "inner=/run/bs1-backup\nd=\n", ""))


if __name__ == "__main__":
    unittest.main()
