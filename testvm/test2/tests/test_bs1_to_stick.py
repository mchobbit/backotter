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

import json
import os
import shutil
import stat
import subprocess
import sys
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


class StickFakes(unittest.TestCase):
    """The fakes and helpers both classes use."""

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


@unittest.skipUnless(BASH and KEYGEN and shutil.which("readlink"), "needs bash and ssh-keygen")
class ToStick(StickFakes):
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


FAKE_EFIBOOTMGR = r"""#!%(py)s
# FreeBSD's efibootmgr, on a state file: entries, BootOrder, BootNext.
import json, sys
st_path = %(state)r
st = json.load(open(st_path))
a = sys.argv[1:]
if a and a != ["-v"]:
    open(%(log)r, "a").write(" ".join(a) + "\n")
def arg(f):
    return a[a.index(f) + 1]
ents = st["entries"]
if "-B" in a:
    n = arg("-b")
    if n not in ents:
        sys.exit(1)
    del ents[n]
    st["order"] = [o for o in st["order"] if o != n]
elif "-c" in a:
    n = next("%%04X" %% i for i in range(0x10000) if "%%04X" %% i not in ents)
    ents[n] = {"desc": arg("-L"), "active": "-a" in a, "path": arg("-l")}
    if not st.get("drop-create"):
        st["order"].insert(0, n)
    else:
        del ents[n]
elif "-o" in a:
    st["order"] = arg("-o").split(",")
elif "-n" in a:
    st["next"] = arg("-b")
json.dump(st, open(st_path, "w"))
if a and a != ["-v"]:
    sys.exit(0)
print("BootCurrent: 0001")
print("Timeout    : 0 seconds")
if st.get("next"):
    print("BootNext : " + st["next"])
print("BootOrder  : " + ", ".join(st["order"]))
# As FreeBSD's does: BootOrder's entries, then the others with their paths
# (with -v, every entry's).
def line(n, e, path):
    s = "%%sBoot%%s%%s %%s" %% ("+" if n == "0001" else " ", n, "*" if e.get("active", True) else " ", e["desc"])
    return s + (" HD(1,MBR,0x0,0x20,0x20000)/File(" + e.get("path", "x") + ")" if path else "")
for n in st["order"]:
    print(line(n, ents[n], a == ["-v"]))
print("\n\nUnreferenced Variables:")
for n, e in sorted(ents.items()):
    if n not in st["order"]:
        print(line(n, e, True))
"""


@unittest.skipUnless(BASH and KEYGEN and shutil.which("readlink"), "needs bash and ssh-keygen")
class ToStickBsd(StickFakes):
    """The FreeBSD branch: base efibootmgr's options, the stick by
    /dev/msdosfs/BACKOTTER and its ident, the ESP by MBR slice 1, behind
    bs1-lane's guard, and the reboot by daemon(8) and shutdown -r."""

    def setUp(self):
        super().setUp()
        t, b = self.t, self.bin
        text = (BS1 / "bs1-to-stick").read_text()
        self.assertIn("BSD_CONFIG=/dev/msdosfs/BACKOTTER\n", text)
        text = text.replace("BSD_CONFIG=/dev/msdosfs/BACKOTTER\n", f"BSD_CONFIG={t}/dev/msdosfs/BACKOTTER\n")
        exe(self.here / "bs1-to-stick", text)
        (t / "dev/msdosfs").mkdir(parents=True)
        (t / "dev/msdosfs/BACKOTTER").touch()
        (self.here / "bs1-common").write_text(f"""set -uo pipefail
OSTYPE=freebsd15.0
HERE={self.here}
LOG=/dev/null STEP=to-stick INTERNAL_SERIAL=X STICK_SERIAL=STICK001
say() {{ printf '%s\\n' "$*"; }}
die() {{ say "STOPPED: $*"; exit 1; }}
internal_disk() {{ echo nda0; }}
disk_of() {{ echo da1; }}
bsd_umass() {{ [ ! -e {t}/not-usb ]; }}
bsd_ident() {{ cat {t}/ident 2>/dev/null || echo STICK001; }}
bsd_scheme() {{ echo MBR; }}
bsd_provider() {{ case $1 in */BACKOTTER) echo da1s3 ;; *) echo "${{1#/dev/}}" ;; esac; }}
bsd_mounts() {{ cat {t}/mount-p 2>/dev/null; }}
""")
        exe(self.here / "bs1-lane", f'#!/bin/sh\necho "lane $*" >>{t}/lane.log\n[ ! -e {t}/guard-fails ]\n')
        for f in ("blkid", "lsblk", "findmnt", "python3"):
            (b / f).unlink()
        exe(b / "sysctl", f'#!/bin/sh\n[ "$*" = "-n machdep.bootmethod" ] && {{ cat {t}/bootmethod 2>/dev/null || echo UEFI; }}\n')
        exe(b / "fstyp", '#!/bin/sh\n[ "$1" = /dev/da1s1 ] && echo msdosfs\n')
        exe(b / "mount", f"""#!/bin/sh
echo "mount $*" >>{t}/mounts
for a; do dev=$tgt; tgt=$a; done
case $dev in
*/msdosfs/BACKOTTER) [ ! -e {t}/mount-fails ] || exit 32; cp -R {t}/backotter-part/. "$tgt"/ ;;
/dev/da1s1) mkdir -p "$tgt/EFI/BOOT" && : >"$tgt/EFI/BOOT/BOOTX64.EFI" ;;
*) exit 32 ;;
esac
""")
        self.state = t / "efi-state.json"
        self.efi_state(entries={"0001": {"desc": "FreeBSD"}, "0000": {"desc": "UiApp"},
                                "0003": {"desc": "BKO rescue", "active": False},
                                "0004": {"desc": "BKO rescue"}},
                       order=["0001", "0003", "0000"])
        exe(b / "efibootmgr", FAKE_EFIBOOTMGR % {"py": sys.executable, "state": str(self.state), "log": str(t / "efi.log")})
        exe(b / "daemon", f'#!/bin/sh\necho "daemon $*" >>{t}/reboot.log\n')

    def efi_state(self, **st):
        self.state.write_text(json.dumps(st))

    def st(self):
        return json.loads(self.state.read_text())

    def run_it(self, **extra):
        env = {"PATH": f"{self.bin}:{self.sys_bin}", "LC_ALL": "C", "TMPDIR": str(self.t / "tmp"), **extra}
        return subprocess.run([BASH, str(self.here / "bs1-to-stick")], capture_output=True, text=True,
                              timeout=30, env=env)

    def assert_clean(self):
        mounts = self.mounts()
        self.assertEqual(sum(m.startswith("mount ") for m in mounts),
                         sum(m.startswith("umount ") for m in mounts), mounts)
        self.assertIn(f"mount -t msdosfs -o ro,nosuid,noexec {self.t}/dev/msdosfs/BACKOTTER", mounts[0])
        self.assert_no_temp()

    def test_keys_present_proceeds(self):
        self.stick_keys()
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("has the SSH keys", r.stdout)
        self.assertEqual((self.t / "lane.log").read_text(), "lane guard --lane\n")
        st = self.st()
        # The stale entries gone, the new one at the lowest free number, active,
        # out of BootOrder, and BootNext.
        self.assertEqual(sorted(st["entries"]), ["0000", "0001", "0002"])
        new = st["entries"]["0002"]
        self.assertEqual(new["desc"], "BKO rescue")
        self.assertTrue(new["active"])
        self.assertTrue(new["path"].endswith("/EFI/BOOT/BOOTX64.EFI"), new)
        self.assertEqual(st["order"], ["0001", "0000"])
        self.assertEqual(st["next"], "0002")
        calls = self.efi_calls().splitlines()
        # Both stale ones, in BootOrder or not (an out-of-order one is
        # listed with its path after the label).
        self.assertEqual(calls[:2], ["-B -b 0003", "-B -b 0004"])
        self.assertTrue(calls[2].startswith("-a -c -l ") and calls[2].endswith(" -L BKO rescue"), calls)
        self.assertEqual(calls[3:], ["-o 0001,0000", "-n -b 0002"])
        self.assertIn("BootNext : 0002", r.stdout)
        self.assertIn("rebooting into the stick (/dev/da1s1, \\EFI\\BOOT\\BOOTX64.EFI), once", r.stdout)
        self.assertEqual(self.reboots(), "daemon -f /bin/sh -c sleep 2; exec /sbin/shutdown -r now\n")
        self.assertIn("mount -t msdosfs -o ro /dev/da1s1", "\n".join(self.mounts()))
        self.assert_clean()

    def test_twice_makes_one_entry(self):
        self.stick_keys()
        for _ in range(3):
            self.assertEqual(self.run_it().returncode, 0)
        st = self.st()
        self.assertEqual([e["desc"] for e in st["entries"].values()].count("BKO rescue"), 1)
        self.assertEqual(st["order"], ["0001", "0000"])

    def stopped_unchanged(self, r, why):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(why, r.stdout)
        self.assertEqual(self.efi_calls(), "", "efibootmgr changed something")
        self.assertEqual(self.reboots(), "")
        self.assert_no_temp()

    def test_guard_refuses(self):
        self.stick_keys()
        (self.t / "guard-fails").touch()
        self.stopped_unchanged(self.run_it(), "bs1-lane's guard refused: BootNext is left alone")
        self.assertEqual(self.mounts(), [])

    def test_not_uefi(self):
        (self.t / "bootmethod").write_text("BIOS\n")
        self.stopped_unchanged(self.run_it(), "this system wasn't booted by UEFI")
        self.assertFalse((self.t / "lane.log").exists())

    def test_not_the_sticks_ident(self):
        self.stick_keys()
        (self.t / "ident").write_text("012345678921\n")
        self.stopped_unchanged(self.run_it(), "whose ident 012345678921 isn't the stick's, STICK001")
        self.assertEqual(self.mounts(), [])

    def test_not_usb(self):
        self.stick_keys()
        (self.t / "not-usb").touch()
        self.stopped_unchanged(self.run_it(), "BACKOTTER is on da1, which isn't a USB disk")

    def test_reflashed_stick_stops(self):
        self.stick_keys(auth=False, host=False)
        r = self.run_it()
        self.stopped_unchanged(r, "BootNext is left alone")
        self.assertIn("no backotter/authorized_keys", r.stdout)
        self.assert_clean()

    def test_mounted_stick_and_esp_are_read_where_they_are(self):
        self.stick_keys()
        at = self.t / "media" / "B K O"
        shutil.copytree(self.part.parent, at)
        esp = self.t / "media" / "E S P"
        (esp / "efi/boot").mkdir(parents=True)
        (esp / "efi/boot/bootx64.efi").touch()
        enc = lambda p: str(p).replace(" ", "\\040")
        (self.t / "mount-p").write_text(f"/dev/da1s3\t{enc(at)}\tmsdosfs\trw\t0\t0\n/dev/da1s1\t{enc(esp)}\tmsdosfs\trw\t0\t0\n")
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"is mounted at {at} already", r.stdout)
        self.assertEqual(self.mounts(), [])
        self.assertEqual(self.st()["entries"]["0002"]["path"], f"{esp}/efi/boot/bootx64.efi")
        self.assert_no_temp()

    def test_firmware_drops_the_new_entry(self):
        self.stick_keys()
        st = self.st()
        st["drop-create"] = True
        self.efi_state(**st)
        r = self.run_it()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("the firmware didn't keep the new entry", r.stdout)
        self.assertNotIn("next", self.st())
        self.assertEqual(self.reboots(), "")
        self.assert_clean()


if __name__ == "__main__":
    unittest.main()
