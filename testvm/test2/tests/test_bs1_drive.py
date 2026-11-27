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


# The FreeBSD lane (docs/design/freebsd-bs1.md): DRIVE for /dev/ufs/LABEL,
# fsck -p before the mount, an existing mount reused (mount -p).
BSD_FAKES = {
    "mount": """#!/bin/sh
if [ "$1" = -p ]; then [ -z "$FAKE_MOUNTED" ] || printf '%s\\t%s\\tufs\\trw\\t2\\t2\\n' "$FAKE_SRC" "$FAKE_MOUNTED"; exit 0; fi
echo "mount $*" >>"$FAKE_LOG"
exit "${FAKE_MOUNT_RC:-0}"
""",
    "fsck": '#!/bin/sh\necho "fsck $*" >>"$FAKE_LOG"\necho "** $4: FILE SYSTEM CLEAN; SKIPPING CHECKS"\nexit "${FAKE_FSCK_RC:-0}"\n',
    "mkdir": '#!/bin/sh\nfor a; do [ "$a" = -p ] || echo "mkdir $a" >>"$FAKE_LOG"; done\n',
}


def bsd_drive_text():
    """The FreeBSD lane's DRIVE, as backshot1 sends it for LABEL."""
    text = (BS1 / "backshot1").read_text()
    lane = re.search(r"^    lane=.*$", text, re.M)
    drive = re.search(r"^    DRIVE=.*$", text, re.M)
    assert lane and drive, "no FreeBSD lane= and DRIVE= in backshot1"
    out = subprocess.run([BASH, "-c", f'BACKUP_LABEL={LABEL}\n{lane.group(0)}\n{drive.group(0)}\nprintf %s "$DRIVE"'],
                         capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    return out.stdout


@unittest.skipUnless(BASH, "needs bash")
class BsdDrive(Drive):
    def setUp(self):
        super().setUp()
        for name, text in BSD_FAKES.items():
            p = self.bin / name
            p.write_text(text)
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)

    def bsd(self, text=None, **env):
        return self.drive(bsd_drive_text() if text is None else text, **env)

    def test_mounts_at_mnt(self):
        """Checked by fsck -p first, then mounted as UFS; fsck's words on stderr."""
        rc, out, err, calls = self.bsd()
        self.assertEqual((rc, out), (0, "d=/mnt/bs1-backup\n"))
        self.assertIn("FILE SYSTEM CLEAN", err)
        self.assertEqual(calls, ["mkdir /mnt/bs1-backup", f"fsck -p -t ufs /dev/ufs/{LABEL}",
                                 f"mount -t ufs /dev/ufs/{LABEL} /mnt/bs1-backup"])

    def test_mounted_already(self):
        """GhostBSD's automount, or an earlier step: reused, no fsck, no mount."""
        rc, out, err, calls = self.bsd(FAKE_SRC=f"/dev/ufs/{LABEL}", FAKE_MOUNTED="/media/BS1BSD")
        self.assertEqual((rc, out, err), (0, "d=/media/BS1BSD\n", ""))
        self.assertEqual(calls, [])

    def test_other_mount_is_not_it(self):
        rc, out, _, calls = self.bsd(FAKE_SRC="/dev/ufs/other", FAKE_MOUNTED="/media/other")
        self.assertEqual((rc, out), (0, "d=/mnt/bs1-backup\n"))
        self.assertEqual(calls[-1], f"mount -t ufs /dev/ufs/{LABEL} /mnt/bs1-backup")

    def test_unclean_stops_before_the_mount(self):
        rc, out, err, calls = self.bsd(FAKE_FSCK_RC="8")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("fsck_ufs -y", err)
        self.assertFalse(any(c.startswith("mount ") for c in calls), calls)

    def test_mount_fails(self):
        rc, out, _, calls = self.bsd(FAKE_MOUNT_RC="1")
        self.assertEqual((rc, out), (1, ""))

    def test_inside_single_quotes(self):
        """run_on_drive sends it to the stick as mdo sh -c '...'."""
        text = bsd_drive_text()
        self.assertNotIn("'", text)
        rc, out, _, _ = self.bsd(f"/bin/sh -c '{text}; echo \"inner=$d\"'")
        self.assertEqual((rc, out), (0, "inner=/mnt/bs1-backup\nd=\n"))

    def test_read_only_mnt_falls_back_to_run(self):
        self.skipTest("FreeBSD has no /run fallback")

    def test_neither_can_be_made(self):
        self.skipTest("FreeBSD has no /run fallback")


# bs1-lane, D3's guard and format-lane, against fake FreeBSD tools: the
# internal disk nda0, the stick da0, the lane drive da1, and whatever a test
# plugs in besides.
LANE_FAKES = {
    "uname": "#!/bin/sh\necho FreeBSD\n",
    "id": "#!/bin/sh\necho 0\n",
    "sysctl": '#!/bin/sh\necho "$FAKE_DISKS"\n',
    "diskinfo": """#!/bin/sh
d=${2#/dev/}
eval "i=\\${FAKE_IDENT_$d-}"
printf '\\t512\\t# sectorsize\\n\\t1000\\t# mediasize in bytes (1.0K)\\n\\t%s\\t# Disk descr.\\n\\t%s\\t# Disk ident.\\n' "Fake $d" "$i"
""",
    "ls": '#!/bin/sh\nfor n in $FAKE_DEVS; do echo "$n"; done\n',
    "dd": """#!/bin/sh
for a; do case $a in if=/dev/*) p=${a#if=/dev/} ;; of=*) echo "dd $*" >>"$FAKE_LOG"; exit 0 ;; esac; done
for x in $FAKE_XFS; do [ "$x" = "$p" ] && { printf 'XFSB\\000\\000'; exit 0; }; done
printf 'FAKE\\000\\000'
""",
    "gpart": """#!/bin/sh
case $1 in
list) eval "u=\\${FAKE_UUID_$2-}"; for x in $u; do printf '1. Name: %sp1\\n   rawuuid: %s\\n' "$2" "$x"; done ;;
show) echo "=>  40  1000  $2  GPT  (1.0K)" ;;
*) echo "gpart $*" >>"$FAKE_LOG" ;;
esac
""",
    "camcontrol": """#!/bin/sh
echo "scbus0 on nvme0 bus 0:"
echo "<Samsung PM9C1a>  at scbus0 target 0 lun 1 (pass0,nda0)"
n=1
for d in $FAKE_UMASS; do echo "scbus$n on umass-sim$n bus 0:"; echo "<USB Disk 1.00>  at scbus$n target 0 lun 0 ($d,pass$n)"; n=$((n + 1)); done
""",
    "glabel": "#!/bin/sh\nexit 0\n",
    "mount": "#!/bin/sh\nexit 0\n",
    "swapctl": "#!/bin/sh\necho 'Device:       1024-blocks     Used:'\n",
    "zpool": "#!/bin/sh\nexit 0\n",
    "newfs": '#!/bin/sh\necho "newfs $*" >>"$FAKE_LOG"\n',
}
CONF = """LANE_OS=freebsd
SOURCE_HOST=thinkpad
INTERNAL_SERIAL=S7XMNF0Y787118
BACKUP_LABEL=BS1BSD
LANE_SERIAL=LANE0001
STICK_SERIAL=STICK001
NEVER_SERIAL=012345678921
NEVER_PARTUUIDS="11111111-2222-3333-4444-555555555555 AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
"""


@unittest.skipUnless(BASH, "needs bash")
class Lane(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="bs1-lane-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.bin = self.t / "bin"
        self.bin.mkdir()
        for name, text in LANE_FAKES.items():
            p = self.bin / name
            p.write_text(text)
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
        for tool in ("awk", "cat", "grep", "head", "sed", "tr", "dirname", "sleep"):
            os.symlink(shutil.which(tool), self.bin / tool)
        self.log = self.t / "log"
        self.log.write_text("")
        self.env = {"FAKE_DISKS": "nda0 da0 da1", "FAKE_UMASS": "da0 da1",
                    "FAKE_IDENT_nda0": "S7XMNF0Y787118", "FAKE_IDENT_da0": "STICK001", "FAKE_IDENT_da1": "LANE0001",
                    "FAKE_DEVS": "nda0 nda0p1 nda0p2 da0 da0s1 da0s2 da0s2a da1 da1p1 null zero"}

    def lane(self, *args, conf=CONF, **env):
        """{ conf; bs1-lane; } | sh -s -- ARGS, as backshot1's lane() sends it."""
        script = conf + (BS1 / "bs1-lane").read_text()
        out = subprocess.run(["/bin/sh", "-s", "--", *args], input=script, capture_output=True, text=True, timeout=20,
                             env={"PATH": str(self.bin), "BS1_LANE_PATH": str(self.bin), "FAKE_LOG": str(self.log),
                                  **self.env, **env})
        return out.returncode, out.stdout, out.stderr, self.log.read_text().splitlines()

    def test_the_three_disks_pass(self):
        """And an optical drive, which has no ident (QEMU's cd0)."""
        rc, out, err, _ = self.lane("guard", FAKE_DISKS="cd0 nda0 da0 da1")
        self.assertEqual(rc, 0, err)
        self.assertIn("every disk here is", out)

    def test_stand_in_mybig512_is_refused(self):
        """Its serial, an unknown ident, XFS, a GUID of mybig512's: each alone stops it."""
        cases = [
            ({"FAKE_DISKS": "nda0 da0 da1 da2", "FAKE_IDENT_da2": "012345678921"}, "mybig512's (NEVER_SERIAL)"),
            ({"FAKE_DISKS": "nda0 da0 da1 da2", "FAKE_IDENT_da2": "SOMEOTHER"}, "ident SOMEOTHER is none of"),
            ({"FAKE_DISKS": "nda0 da0 da1 da2", "FAKE_IDENT_da2": ""}, "da2: no ident"),
            ({"FAKE_XFS": "da1p1"}, "da1p1: holds XFS"),
            ({"FAKE_XFS": "da1"}, "da1: holds XFS"),
            ({"FAKE_UUID_da1": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}, "is mybig512's (NEVER_PARTUUIDS)"),
        ]
        for env, why in cases:
            with self.subTest(why=why):
                rc, out, err, _ = self.lane("guard", **env)
                self.assertEqual(rc, 1, out)
                self.assertIn(why, err)
                self.assertIn("unplug it (D3)", err)

    def test_unset_stick_serial_refuses_the_stick(self):
        rc, _, err, _ = self.lane("guard", conf=CONF.replace("STICK_SERIAL=STICK001", "STICK_SERIAL="))
        self.assertEqual(rc, 1)
        self.assertIn("da0: ident STICK001 is none of", err)

    def test_conf_naming_mybig512_is_refused(self):
        rc, _, err, _ = self.lane("guard", conf=CONF.replace("LANE_SERIAL=LANE0001", "LANE_SERIAL=012345678921"))
        self.assertEqual(rc, 1)
        self.assertIn("names mybig512's serial", err)

    def test_format_shows_and_writes_nothing(self):
        rc, out, err, calls = self.lane("format", "LANE0001")
        self.assertEqual(rc, 0, err)
        self.assertIn("the lane drive to format: /dev/da1", out)
        self.assertIn("LANE0001", out)
        self.assertIn("nothing written", out)
        self.assertEqual(calls, [])

    def test_format_refuses(self):
        cases = [
            (("format", "OTHER"), {}, "LANE_SERIAL is LANE0001"),
            (("format", "LANE0001", "--yes"), {"FAKE_UUID_da1": "11111111-2222-3333-4444-555555555555"}, "NEVER_PARTUUIDS"),
            (("format", "LANE0001", "--yes"), {"FAKE_XFS": "da1p1"}, "holds XFS"),
            (("format", "LANE0001", "--yes"), {"FAKE_UMASS": "da0"}, "isn't a USB disk"),
        ]
        for args, env, why in cases:
            with self.subTest(why=why):
                self.log.write_text("")
                rc, _, err, calls = self.lane(*args, **env)
                self.assertEqual(rc, 1)
                self.assertIn(why, err)
                self.assertEqual(calls, [], "nothing may be written")

    def test_format_needs_conf_serials_apart(self):
        rc, _, err, calls = self.lane("format", "STICK001", conf=CONF.replace("LANE_SERIAL=LANE0001", "LANE_SERIAL=STICK001"))
        self.assertEqual(rc, 1)
        self.assertIn("the internal disk's or the stick's", err)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
