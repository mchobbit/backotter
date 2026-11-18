"""BackShot1's unattended LUKS unlock (docs/design/bs1-luks-unattended.md):
bs1-luks's crypttab edits, bs1-unlock-key's fallbacks and bs1-common's
evidence of how a boot opened each volume and its layout_keys, on fake
inputs and the corpus's dumps.  No VM, no root, no initramfs: fake mount,
askpass, cryptsetup, journalctl and lsblk."""

import json
import os
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
BS1 = TESTS.parent.parent / "realworld" / "backshot1"
KEYSPEC = "/backshot1/keys/disk-passphrase:LABEL=mybig512"


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


def functions(path, *names):
    """The named functions' text from a bash file (each from `NAME() {` to
    the first `}` alone on a line)."""
    text = Path(path).read_text()
    out = []
    for n in names:
        m = re.search(rf"^{re.escape(n)}\(\) {{\n.*?^}}\n", text, re.M | re.S)
        assert m, f"no function {n} in {path}"
        out.append(m.group(0))
    return "\n".join(out)


def exe(path, text):
    Path(path).write_text(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="bs1-luks-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.bin = self.t / "bin"
        self.bin.mkdir()

    def env(self, **kw):
        return {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", **kw}


@unittest.skipUnless(BASH, "needs bash 4 or newer")
class TabEdit(Tmp):
    """lk_tab_edit: crypttab's key field and options, as adopt writes them."""

    def edit(self, text, *edits):
        tab = self.t / "crypttab"
        tab.write_text(text)
        script = f'. "{BS1}/bs1-luks"; LK_KEYSPEC={KEYSPEC}; lk_tab_edit "$1" "${{@:2}}"'
        r = subprocess.run([BASH, "-c", script, "x", str(tab), *edits], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_s_puts_the_drive_key_and_timeout(self):
        out = self.edit("# <target> <source> <key> <options>\ndm_crypt-0 UUID=abc none luks,discard\nswap /dev/sdb2 /dev/urandom swap\n",
                        "dm_crypt-0|S|abc|1|0")
        self.assertEqual(out.splitlines(), ["# <target> <source> <key> <options>",
                                            f"dm_crypt-0\tUUID=abc\t{KEYSPEC}\tluks,discard,keyfile-timeout=30s",
                                            "swap /dev/sdb2 /dev/urandom swap"])

    def test_s_twice_is_the_same(self):
        once = self.edit("cryptroot\tUUID=abc\tnone\tluks,discard,keyfile-timeout=10s\n", "cryptroot|S|abc|1|1")
        self.assertIn("luks,discard,keyfile-timeout=30s", once)
        self.assertEqual(once.count("keyfile-timeout"), 1)
        self.assertEqual(self.edit(once, "cryptroot|S|abc|1|1"), once)

    def test_s_names_the_device_by_uuid_for_the_command_line(self):
        out = self.edit("root /dev/nvme0n1p3 none\n", "root|S|abc|1|1")
        self.assertEqual(out, f"root\tUUID=abc\t{KEYSPEC}\tkeyfile-timeout=30s\n")
        out = self.edit("root /dev/nvme0n1p3 none\n", "root|S|abc|1|0")
        self.assertTrue(out.startswith("root\t/dev/nvme0n1p3\t"))

    def test_s_adds_a_line_where_there_is_none(self):
        self.assertEqual(self.edit("", "root|S|abc|1|0"), f"root\tUUID=abc\t{KEYSPEC}\tkeyfile-timeout=30s\n")

    def test_d_adds_the_keyscript_and_leaves_the_key_field(self):
        out = self.edit("nvme0n1p3_crypt UUID=abc none luks,discard\n", "nvme0n1p3_crypt|D|abc|0|0")
        self.assertEqual(out, "nvme0n1p3_crypt\tUUID=abc\tnone\tluks,discard,keyscript=/usr/local/lib/backshot1/unlock-key\n")
        self.assertEqual(self.edit(out, "nvme0n1p3_crypt|D|abc|0|0"), out)

    def test_d_adds_no_line(self):
        self.assertEqual(self.edit("other UUID=x none luks\n", "root|D|abc|0|0"), "other UUID=x none luks\n")


@unittest.skipUnless(shutil.which("sh"), "no sh")
class UnlockKey(Tmp):
    """bs1-unlock-key: the drive's key when it's there, else the prompt; a
    mark for each in run/backshot1-unlock."""

    def setUp(self):
        super().setUp()
        self.root = self.t / "root"
        for d in ("etc/backshot1", "run", "dev/disk/by-label", "lib/cryptsetup", "drive/backshot1/keys"):
            (self.root / d).mkdir(parents=True)
        (self.root / "drive/backshot1/keys/disk-passphrase").write_text("s3cret")
        exe(self.root / "lib/cryptsetup/askpass", '#!/bin/sh\necho "ASKED: $1" >&2\nprintf typed-pass\n')
        # mount -t T -o O DEV DIR: the drive's files into DIR.
        exe(self.bin / "mount", f'#!/bin/sh\necho "$@" >>"{self.t}/mounts"\nfor d; do :; done\ncp -R "{self.root}/drive/." "$d/"\n')
        exe(self.bin / "umount", '#!/bin/sh\nrm -rf "$1"/*\n')
        exe(self.bin / "modprobe", "#!/bin/sh\nexit 0\n")
        exe(self.bin / "sleep", "#!/bin/sh\nexit 0\n")
        exe(self.bin / "cryptsetup", f'#!/bin/sh\nexit "$(cat "{self.t}/cryptsetup-rc" 2>/dev/null || echo 0)"\n')
        self.record()

    def record(self, wait=2, volumes="root:abc:E"):
        (self.root / "etc/backshot1/luks-unlock").write_text(textwrap.dedent(f"""\
            family=D
            label=mybig512
            path=/backshot1/keys/disk-passphrase
            fstype=ext4
            wait={wait}
            volumes='{volumes}'
            """))

    def plug(self):
        (self.root / "dev/disk/by-label/mybig512").write_text("")

    def run_key(self, *args, **env):
        return subprocess.run(["sh", str(BS1 / "bs1-unlock-key"), *args], capture_output=True, text=True,
                              env=self.env(BS1_UNLOCK_ROOT=str(self.root), **env))

    def marks(self):
        p = self.root / "run/backshot1-unlock"
        return p.read_text().splitlines() if p.exists() else []

    def test_keyscript_reads_the_drive(self):
        self.plug()
        r = self.run_key("none", CRYPTTAB_NAME="root", CRYPTTAB_TRIED="0")
        self.assertEqual((r.returncode, r.stdout), (0, "s3cret"), r.stderr)
        self.assertEqual(self.marks(), ["root drive"])
        self.assertIn("-o ro,noload", (self.t / "mounts").read_text())
        self.assertFalse((self.root / "run/backshot1-keydev").exists())

    def test_keyscript_asks_without_the_drive(self):
        r = self.run_key("none", CRYPTTAB_NAME="root", CRYPTTAB_TRIED="0")
        self.assertEqual((r.returncode, r.stdout), (0, "typed-pass"), r.stderr)
        self.assertIn("waiting up to 2 s", r.stderr)
        self.assertIn("ASKED: Please unlock disk root: ", r.stderr)
        self.assertEqual(self.marks(), ["root typed"])

    def test_keyscript_asks_on_a_second_try(self):
        self.plug()
        r = self.run_key("none", CRYPTTAB_NAME="root", CRYPTTAB_TRIED="1")
        self.assertEqual(r.stdout, "typed-pass")
        self.assertFalse((self.t / "mounts").exists(), "the second try mustn't read the drive again")
        self.assertEqual(self.marks(), ["root typed"])

    def test_keyscript_asks_when_the_drive_has_no_key(self):
        self.plug()
        (self.root / "drive/backshot1/keys/disk-passphrase").unlink()
        r = self.run_key("none", CRYPTTAB_NAME="root", CRYPTTAB_TRIED="0")
        self.assertEqual(r.stdout, "typed-pass")
        self.assertEqual(self.marks(), ["root typed"])

    def test_keyscript_without_a_record_asks(self):
        (self.root / "etc/backshot1/luks-unlock").unlink()
        r = self.run_key("none", CRYPTTAB_NAME="root", CRYPTTAB_TRIED="0")
        self.assertEqual(r.stdout, "typed-pass")

    def test_hook_writes_a_key_that_opens(self):
        self.plug()
        out = self.root / "crypto_keyfile.bin"
        r = self.run_key("--to", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(out.read_text(), "s3cret")
        self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)
        self.assertEqual(self.marks(), ["root drive"])

    def test_hook_leaves_no_file_for_a_wrong_key(self):
        self.plug()
        (self.t / "cryptsetup-rc").write_text("2")
        out = self.root / "crypto_keyfile.bin"
        r = self.run_key("--to", str(out))
        self.assertEqual(r.returncode, 0)
        self.assertFalse(out.exists())
        self.assertEqual(self.marks(), ["root typed"])

    def test_hook_without_the_drive(self):
        out = self.root / "crypto_keyfile.bin"
        r = self.run_key("--to", str(out))
        self.assertEqual(r.returncode, 0)
        self.assertFalse(out.exists())
        self.assertEqual(self.marks(), ["root typed"])


@unittest.skipUnless(BASH, "needs bash 4 or newer")
class Evidence(Tmp):
    """unlock_how and unlock_mode: how this boot opened each volume."""

    def setUp(self):
        super().setUp()
        self.rec = self.t / "luks-unlock"
        self.mark = self.t / "backshot1-unlock"
        self.journal = self.t / "journal"
        self.journal.write_text("")
        exe(self.bin / "journalctl", f'#!/bin/sh\ncat "{self.journal}"\n')
        self.names = "root"

    def call(self, expr):
        lib = functions(BS1 / "bs1-common", "luks_rec", "unlock_how", "unlock_mode")
        # luks_names: the open LUKS mappings on the disk.
        script = f'LUKS_REC="{self.rec}" UNLOCK_MARK="{self.mark}"\n{lib}\nluks_names() {{ printf "%s" "{self.names}"; }}\n{expr}'
        r = subprocess.run([BASH, "-c", script], capture_output=True, text=True, env=self.env())
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def record(self, family, volumes="", own="", reason=""):
        self.rec.write_text(f"family={family}\ngenerator=dracut\nvolumes='{volumes}'\nown='{own}'\nreason='{reason}'\n")

    def test_s_mounted_key_device_is_the_drive(self):
        for line in (r"Mounted run-systemd-cryptsetup-keydev\x2droot.mount - /run/systemd/cryptsetup/keydev-root.",
                     "Mounted /run/systemd/cryptsetup/keydev-root."):
            self.journal.write_text(f"Starting something\n{line}\nFinished systemd-cryptsetup@root.service\n")
            self.assertEqual(self.call("unlock_how root S"), "drive\n", line)

    def test_s_key_that_failed_was_typed(self):
        self.journal.write_text("Mounted /run/systemd/cryptsetup/keydev-root.\n"
                                "Failed to activate with key file '/run/systemd/cryptsetup/keydev-root/backshot1/keys/disk-passphrase'. (Key data incorrect?)\n")
        self.assertEqual(self.call("unlock_how root S"), "typed\n")

    def test_s_no_key_device_was_typed(self):
        self.journal.write_text("Mounted /run/systemd/cryptsetup/keydev-rootx.\nTimed out waiting for device /dev/disk/by-label/mybig512.\n")
        self.assertEqual(self.call("unlock_how root S"), "typed\n")

    def test_d_and_e_read_the_last_mark(self):
        self.assertEqual(self.call("unlock_how root D"), "typed\n")
        self.mark.write_text("root drive\nswap drive\n")
        self.assertEqual(self.call("unlock_how root D"), "drive\n")
        self.mark.write_text("root drive\nroot typed\n")
        self.assertEqual(self.call("unlock_how root E"), "typed\n")

    def test_mode_drive(self):
        self.record("D", volumes="root:abc:D")
        self.mark.write_text("root drive\n")
        out = self.call("unlock_mode nvme0n1")
        self.assertTrue(out.startswith("drive: "), out)
        self.assertIn("\n  root drive\n", out)

    def test_mode_attended_when_typed_this_boot(self):
        self.record("D", volumes="root:abc:D")
        self.mark.write_text("root typed\n")
        out = self.call("unlock_mode nvme0n1")
        self.assertTrue(out.startswith("attended: root was typed at this boot"), out)

    def test_mode_without_a_record(self):
        out = self.call("unlock_mode nvme0n1")
        self.assertTrue(out.startswith("attended: not set up"), out)

    def test_mode_family_a_says_why(self):
        self.record("A", reason="the initramfs is mkinitfs")
        self.assertTrue(self.call("unlock_mode nvme0n1").startswith("attended: the initramfs is mkinitfs"))

    def test_mode_own(self):
        self.record("own", own="root:abc")
        out = self.call("unlock_mode nvme0n1")
        self.assertTrue(out.startswith("own: "), out)
        self.assertIn("  root own", out)

    def test_mode_none(self):
        self.names = ""
        self.assertTrue(self.call("unlock_mode nvme0n1").startswith("none: "))

    def test_mode_volume_missing_from_record(self):
        self.record("S", volumes="other:abc:S")
        self.assertIn("root isn't in", self.call("unlock_mode nvme0n1"))


    def test_b_is_the_key_device_mount_as_s(self):
        # AerynOS: blsforme's rd.luks.uuid names the mapping luks-UUID.
        self.names = f"luks-{UUID}"
        esc = UUID.replace("-", "\\x2d")
        self.journal.write_text(f"Mounted run-systemd-cryptsetup-keydev\\x2dluks\\x2d{esc}.mount - /run/systemd/cryptsetup/keydev-luks-{UUID}.\n"
                                f"Finished systemd-cryptsetup@luks\\x2d{esc}.service - Cryptography Setup for luks-{UUID}.\n")
        self.assertEqual(self.call(f"unlock_how luks-{UUID} B"), "drive\n")
        self.record("B", volumes=f"luks-{UUID}:{UUID}:B")
        out = self.call("unlock_mode nvme0n1")
        self.assertTrue(out.startswith("drive: ") and "(family B)" in out, out)
        self.assertIn(f"  luks-{UUID} drive\n", out)
        self.journal.write_text(f"Finished systemd-cryptsetup@luks\\x2d{esc}.service - Cryptography Setup for luks-{UUID}.\n")
        self.assertTrue(self.call("unlock_mode nvme0n1").startswith(f"attended: luks-{UUID} was typed"))


UUID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
OTHER = "11111111-2222-3333-4444-555555555555"


@unittest.skipUnless(BASH, "needs bash 4 or newer")
class ZvolDevices(Tmp):
    """disk_devices and luks_devices: a pool's ZFS volumes count as its
    disk's (Ubuntu's keystore, LUKS on rpool/keystore, holding rpool's key:
    the Thinkpad on 26.10's ZFS + encryption), though lsblk shows no zvol
    under the disk."""

    def setUp(self):
        super().setUp()
        t = self.t
        self.zvol = t / "zvol"
        (self.zvol / "rpool").mkdir(parents=True)
        (t / "dev").mkdir()
        self.zd0 = t / "dev/zd0"
        self.zd0.write_text("")
        (self.zvol / "rpool/keystore").symlink_to(self.zd0)
        (self.zvol / "rpool/keystore-part1").symlink_to(self.zd0)
        self.vdev_disk = "nvme0n1"
        exe(self.bin / "zpool", textwrap.dedent(f"""\
            #!/bin/sh
            case "$*" in
            "list -Ho name") echo rpool ;;
            "list -vHPL rpool") printf 'rpool\t476G\n\t/dev/nvme0n1p4\t470G\n' ;;
            *) exit 1 ;;
            esac
            """))
        exe(self.bin / "lsblk", textwrap.dedent(f"""\
            #!/bin/sh
            case "$*" in
            "-lnpo NAME /dev/nvme0n1") printf '/dev/nvme0n1\n/dev/nvme0n1p1\n/dev/nvme0n1p3\n/dev/mapper/dm_crypt-0\n/dev/nvme0n1p4\n' ;;
            "-snlo NAME /dev/nvme0n1p4") echo nvme0n1p4; cat "{t}/vdev-disk"; echo ;;
            "-lnpo NAME {self.zd0}") printf '{self.zd0}\n/dev/mapper/keystore-rpool\n' ;;
            *) exit 1 ;;
            esac
            """))
        (t / "vdev-disk").write_text("nvme0n1")
        exe(self.bin / "blkid", f'#!/bin/sh\nfor d; do :; done\n[ "$d" = "{self.zd0}" ] && echo crypto_LUKS\nexit 0\n')

    def call(self, expr):
        lib = functions(BS1 / "bs1-common", "zvols_on", "disk_devices", "luks_devices")
        r = subprocess.run([BASH, "-c", f'ZVOL_DIR="{self.zvol}"\n{lib}\n{expr}'], capture_output=True, text=True, env=self.env())
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    def test_the_keystore_is_on_the_disk(self):
        self.assertEqual(self.call("zvols_on nvme0n1"), [str(self.zd0)])
        devs = self.call("disk_devices nvme0n1")
        self.assertEqual(devs[:5], ["/dev/nvme0n1", "/dev/nvme0n1p1", "/dev/nvme0n1p3", "/dev/mapper/dm_crypt-0", "/dev/nvme0n1p4"])
        self.assertEqual(devs[5:], [str(self.zd0), "/dev/mapper/keystore-rpool"])
        self.assertEqual(self.call("luks_devices nvme0n1"), [str(self.zd0)])

    def test_another_disks_pool_is_not(self):
        (self.t / "vdev-disk").write_text("sda")
        self.assertEqual(self.call("zvols_on nvme0n1"), [])
        self.assertEqual(self.call("luks_devices nvme0n1"), [])

    def test_no_pool_imported(self):
        exe(self.bin / "zpool", "#!/bin/sh\nexit 1\n")
        self.assertEqual(self.call("zvols_on nvme0n1"), [])
        self.assertEqual(self.call("disk_devices nvme0n1")[-1], "/dev/nvme0n1p4")


@unittest.skipUnless(BASH, "needs bash 4 or newer")
class AdoptZfsKeystore(Tmp):
    """luks_adopt on Ubuntu 26.10's ZFS + encryption (dracut, systemd in the
    initramfs): the keystore on rpool/keystore is opened by the initramfs's
    ZFS code after it imports the pool, which asks for its passphrase, so
    the install is attended (family A) and nothing changes."""

    def test_attended(self):
        t = self.t
        here = t / "drive/backshot1"
        (here / "keys").mkdir(parents=True)
        shutil.copy(BS1 / "bs1-unlock-key", here / "bs1-unlock-key")
        (here / "keys/disk-passphrase").write_text("s3cret")
        (t / "proc-cmdline").write_text("BOOT_IMAGE=/vmlinuz-7.3.0-10-generic root=ZFS=rpool/ROOT/ubuntu_x ro quiet splash\n")
        (t / "crypttab").write_text("dm_crypt-0 PARTUUID=7d881a58 /dev/urandom cipher=aes-cbc-essiv:sha256,initramfs,plain,size=256,swap\n")
        exe(self.bin / "findmnt", f'#!/bin/sh\ncase "$2" in TARGET) echo "{t}/drive" ;; SOURCE) echo /dev/sdb1 ;; FSTYPE) echo xfs ;; esac\n')
        exe(self.bin / "blkid", "#!/bin/sh\necho mybig512\n")
        exe(self.bin / "cryptsetup", f'#!/bin/sh\necho "$@" >>"{t}/cryptsetup-calls"\nexit 0\n')
        script = textwrap.dedent(f"""\
            . "{BS1}/bs1-luks"
            LUKS_DIR="{t}/etc/backshot1" LUKS_ORIG="{t}/etc/backshot1/orig" LUKS_MADE="{t}/etc/backshot1/made" LUKS_REC="{t}/etc/backshot1/luks-unlock"
            LK_CMDLINE="{t}/proc-cmdline" LK_CRYPTTAB="{t}/crypttab"
            here="{here}" init=systemd BACKUP_LABEL=mybig512 INTERNAL_SERIAL=S1
            internal_disk() {{ echo nvme0n1; }}
            is_abroot() {{ false; }}
            lk_generator() {{ echo dracut; }}
            lk_dracut_systemd() {{ true; }}
            lk_luks_volumes() {{ echo "/dev/zd0 {UUID} keystore-rpool"; }}
            lk_early() {{ false; }}
            luks_adopt
            """)
        r = subprocess.run([BASH, "-c", script], capture_output=True, text=True, env=self.env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("luks: A: keystore-rpool is on a ZFS volume (/dev/zd0: Ubuntu's ZFS keystore)", r.stdout)
        rec = (t / "etc/backshot1/luks-unlock").read_text()
        self.assertIn("family=A\n", rec)
        self.assertIn("keystore-rpool is on a ZFS volume (/dev/zd0: Ubuntu", rec)
        self.assertIn("--test-passphrase", (t / "cryptsetup-calls").read_text())
        self.assertEqual((t / "crypttab").read_text(), "dm_crypt-0 PARTUUID=7d881a58 /dev/urandom cipher=aes-cbc-essiv:sha256,initramfs,plain,size=256,swap\n")


@unittest.skipUnless(BASH, "needs bash 4 or newer")
class BlsCmdline(Tmp):
    """Family B (AerynOS): the cmdline.d line, the options in force merged
    into it, and the boot entries' check."""

    def sh(self, expr, stdin=None, rc=0):
        script = (f'. "{BS1}/bs1-luks"; LK_KEYSPEC={KEYSPEC}; LK_BLS_DIRS="{self.t}/usr {self.t}/etc"; '
                  f'LK_BLS_FILE="{self.t}/etc/90-backshot1-luks.cmdline"\n{expr}')
        r = subprocess.run([BASH, "-c", script], input=stdin, capture_output=True, text=True, env=self.env())
        self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
        return r.stdout + r.stderr

    def eff(self, *args, uuid=UUID):
        return self.sh(f"lk_bls_eff {uuid}", "\n".join(args) + "\n").strip()

    def test_eff_none(self):
        self.assertEqual(self.eff("root=UUID=x", f"rd.luks.uuid={UUID}", "quiet"), "- -")

    def test_eff_the_last_of_each_wins(self):
        self.assertEqual(self.eff(f"rd.luks.options={UUID}=discard", f"rd.luks.options={UUID}=discard,no-read-workqueue",
                                  f"luks.key={UUID}=/k:LABEL=x"), "/k:LABEL=x discard,no-read-workqueue")

    def test_eff_a_volume_own_over_every_volume(self):
        self.assertEqual(self.eff("rd.luks.options=discard", f"rd.luks.options={OTHER}=tpm2-device=auto"), "- discard")
        self.assertEqual(self.eff(f"rd.luks.options={UUID}=x-own", "rd.luks.options=discard"), "- x-own")
        self.assertEqual(self.eff("rd.luks.key=/etc/all.key", f"rd.luks.key={OTHER}=/o.key"), "/etc/all.key -")

    def test_opts_merge(self):
        self.assertEqual(self.sh("lk_bls_opts -").strip(), "keyfile-timeout=30s")
        self.assertEqual(self.sh("lk_bls_opts discard,keyfile-timeout=10s,discard,tries=2").strip(),
                         "discard,tries=2,keyfile-timeout=30s")
        self.assertEqual(self.sh("LUKS_WAIT=5; lk_bls_opts keyfile-timeout=30s").strip(), "keyfile-timeout=5s")

    def test_text_is_the_thinkpad_trial(self):
        # The hand-written 20-luks.cmdline that unlocked the Thinkpad.
        self.assertEqual(self.sh(f"lk_bls_text '{UUID}|discard'"),
                         f"rd.luks.uuid={UUID} rd.luks.key={UUID}={KEYSPEC} rd.luks.options={UUID}=discard,keyfile-timeout=30s")

    def test_text_two_volumes(self):
        out = self.sh(f"lk_bls_text '{UUID}|-' '{OTHER}|discard,keyfile-timeout=9s'")
        self.assertEqual(out.split(" "), [f"rd.luks.uuid={UUID}", f"rd.luks.key={UUID}={KEYSPEC}", f"rd.luks.options={UUID}=keyfile-timeout=30s",
                                          f"rd.luks.uuid={OTHER}", f"rd.luks.key={OTHER}={KEYSPEC}", f"rd.luks.options={OTHER}=discard,keyfile-timeout=30s"])

    def test_args_in_blsforme_order_without_ours(self):
        (self.t / "usr").mkdir()
        (self.t / "etc").mkdir()
        (self.t / "usr/10-aerynos.cmdline").write_text("quiet splash\n")
        (self.t / "etc/20-luks.cmdline").write_text("# a note\nrd.luks.options=discard\trw\n")
        (self.t / "etc/90-backshot1-luks.cmdline").write_text(f"rd.luks.key={UUID}=old\n")
        (self.t / "etc/notes.txt").write_text("nothing\n")
        self.assertEqual(self.sh("lk_bls_args").split(), ["quiet", "splash", "rd.luks.options=discard", "rw"])

    def entry(self, name, options):
        d = self.t / "efi/loader/entries"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(f"title AerynOS\nlinux /EFI/aerynos/kernel\noptions {options}\n")

    def check(self, rc, vols=(f"{UUID}|discard",)):
        return self.sh(f'lk_bls_entries_ok "{self.t}/efi/loader/entries" ' + " ".join(f"'{v}'" for v in vols), rc=rc)

    def test_entries_ok(self):
        self.entry("a.conf", f"root=UUID=r rd.luks.uuid={UUID} rw rd.luks.uuid={UUID} rd.luks.key={UUID}={KEYSPEC} rd.luks.options={UUID}=discard,keyfile-timeout=30s")
        self.assertIn("a.conf: ok", self.check(0))

    def test_entries_overridden_later(self):
        self.entry("a.conf", f"rd.luks.uuid={UUID} rd.luks.key={UUID}={KEYSPEC} rd.luks.options={UUID}=discard,keyfile-timeout=30s")
        self.entry("b.conf", f"rd.luks.uuid={UUID} rd.luks.key={UUID}={KEYSPEC} rd.luks.options={UUID}=discard,keyfile-timeout=30s rd.luks.options={UUID}=discard")
        out = self.check(1)
        self.assertIn("a.conf: ok", out)
        self.assertIn(f'b.conf gives {UUID} key and options "{KEYSPEC} discard", not', out)

    def test_entries_must_name_the_volume(self):
        self.entry("a.conf", f"rd.luks.key={UUID}={KEYSPEC} rd.luks.options={UUID}=discard,keyfile-timeout=30s")
        self.assertIn(f"has no rd.luks.uuid={UUID}", self.check(1))

    def test_no_entries(self):
        self.assertIn("no boot entry", self.check(1))


@unittest.skipUnless(BASH, "needs bash 4 or newer")
class AdoptB(Tmp):
    """luks_adopt and luks_off on a fake AerynOS: moss boot sync writes an
    entry from the cmdline.d files as blsforme does, root's rd.luks.uuid
    first."""

    def setUp(self):
        super().setUp()
        t = self.t
        self.drive = t / "drive"
        here = self.drive / "backshot1"
        (here / "keys").mkdir(parents=True)
        shutil.copy(BS1 / "bs1-unlock-key", here / "bs1-unlock-key")
        self.key = here / "keys/disk-passphrase"
        self.key.write_text("s3cret")
        self.key.chmod(0o644)
        for d in ("usr/lib/kernel/cmdline.d", "etc/kernel/cmdline.d", "efi/loader/entries"):
            (t / d).mkdir(parents=True)
        self.cmdline_d = t / "etc/kernel/cmdline.d"
        self.ours = self.cmdline_d / "90-backshot1-luks.cmdline"
        (t / "usr/lib/kernel/cmdline.d/10-default.cmdline").write_text("quiet\n")
        (t / "proc-cmdline").write_text(f"initrd=/x root=UUID=r rd.luks.uuid={UUID} quiet\n")
        exe(self.bin / "findmnt", f'#!/bin/sh\ncase "$2" in TARGET) echo "{self.drive}" ;; SOURCE) echo /dev/sdb1 ;; FSTYPE) echo xfs ;; esac\n')
        exe(self.bin / "blkid", "#!/bin/sh\necho mybig512\n")
        exe(self.bin / "cryptsetup", "#!/bin/sh\nexit 0\n")
        exe(self.bin / "modinfo", "#!/bin/sh\nexit 1\n")
        exe(self.bin / "sleep", "#!/bin/sh\nexit 0\n")
        self.moss_extra = t / "moss-extra"
        exe(self.bin / "moss", textwrap.dedent(f"""\
            #!/bin/sh
            [ "$1 $2" = "boot sync" ] || exit 2
            echo sync >>"{t}/moss-calls"
            o="root=UUID=r rd.luks.uuid={UUID} rw $(cat "{t}"/usr/lib/kernel/cmdline.d/*.cmdline "{t}"/etc/kernel/cmdline.d/*.cmdline 2>/dev/null | grep -v '^#' | tr '\\n' ' ') $(cat "{self.moss_extra}" 2>/dev/null)"
            printf 'title AerynOS\\nlinux /EFI/aerynos/kernel\\noptions %s\\n' "$o" >"{t}/efi/loader/entries/aerynos-6.17.conf"
            """))

    def adopt(self, what="luks_adopt", moss=True, rc=0):
        t = self.t
        if not moss:
            (self.bin / "moss").unlink()
        script = textwrap.dedent(f"""\
            . "{BS1}/bs1-luks"
            LUKS_DIR="{t}/etc/backshot1" LUKS_ORIG="{t}/etc/backshot1/orig" LUKS_MADE="{t}/etc/backshot1/made" LUKS_REC="{t}/etc/backshot1/luks-unlock"
            LK_BLS_DIRS="{t}/usr/lib/kernel/cmdline.d {t}/etc/kernel/cmdline.d" LK_BLS_FILE="{self.ours}" LK_BLS_ESP="{t}/efi" LK_CMDLINE="{t}/proc-cmdline"
            here="{self.drive}/backshot1" init=systemd BACKUP_LABEL=mybig512 INTERNAL_SERIAL=S1
            internal_disk() {{ echo nvme0n1; }}
            is_abroot() {{ false; }}
            lk_generator() {{ echo prebuilt; }}
            lk_luks_volumes() {{ echo "/dev/nvme0n1p2 {UUID} luks-{UUID}"; }}
            lk_early() {{ [[ $1 == luks-{UUID} ]]; }}
            {what}
            """)
        r = subprocess.run([BASH, "-c", script], capture_output=True, text=True, env=self.env())
        self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
        return r.stdout + r.stderr

    def syncs(self):
        p = self.t / "moss-calls"
        return len(p.read_text().splitlines()) if p.exists() else 0

    def rec(self):
        return (self.t / "etc/backshot1/luks-unlock").read_text()

    def test_adopt_rerun_and_off(self):
        installer = self.cmdline_d / "20-luks.cmdline"
        installer.write_text(f"rd.luks.options={UUID}=discard\n")
        out = self.adopt()
        self.assertIn("family B (initramfs: prebuilt)", out)
        self.assertEqual(self.ours.read_text(),
                         f"rd.luks.uuid={UUID} rd.luks.key={UUID}={KEYSPEC} rd.luks.options={UUID}=discard,keyfile-timeout=30s\n")
        self.assertIn("aerynos-6.17.conf: ok", out)
        self.assertEqual(self.syncs(), 1)
        self.assertIn("family=B\n", self.rec())
        self.assertIn(f"volumes='luks-{UUID}:{UUID}:B'", self.rec())
        self.assertEqual(stat.S_IMODE(self.key.stat().st_mode), 0o600)
        self.assertNotIn("of its own", out)

        out = self.adopt()
        self.assertIn("unchanged; the initramfs isn't remade", out)
        self.assertEqual(self.syncs(), 1)

        out = self.adopt("luks_off")
        self.assertIn(f"removed {self.ours}", out)
        self.assertFalse(self.ours.exists())
        self.assertFalse((self.t / "etc/backshot1").exists())
        self.assertEqual(installer.read_text(), f"rd.luks.options={UUID}=discard\n")
        self.assertEqual(self.syncs(), 2)
        self.assertNotIn("rd.luks.key", (self.t / "efi/loader/entries/aerynos-6.17.conf").read_text())

    def test_unsynced_entries_are_synced_again(self):
        self.adopt()
        (self.t / "efi/loader/entries/aerynos-6.17.conf").write_text(f"options root=UUID=r rd.luks.uuid={UUID}\n")
        out = self.adopt()
        self.assertIn("moss boot sync again", out)
        self.assertEqual(self.syncs(), 2)

    def test_hand_written_key_is_merged_and_warned_of(self):
        hand = self.cmdline_d / "20-luks.cmdline"
        hand.write_text(f"rd.luks.uuid={UUID} rd.luks.key={UUID}={KEYSPEC} rd.luks.options={UUID}=discard,keyfile-timeout=30s\n")
        out = self.adopt()
        self.assertIn(f"{hand} has an rd.luks.key= of its own", out)
        self.assertIn(f"rd.luks.options={UUID}=discard,keyfile-timeout=30s\n", self.ours.read_text())

    def test_a_key_of_its_own_is_own(self):
        (self.cmdline_d / "20-luks.cmdline").write_text(f"rd.luks.key={UUID}=/etc/keys/root.key\n")
        out = self.adopt()
        self.assertIn("own: every LUKS volume unlocks with no one typing", out)
        self.assertFalse(self.ours.exists())
        self.assertEqual(self.syncs(), 0)

    def test_an_entry_that_overrides_puts_everything_back(self):
        self.moss_extra.write_text(f"rd.luks.options={UUID}=discard\n")
        out = self.adopt(rc=1)
        self.assertIn("lacks what unlocking from the drive needs", out)
        self.assertFalse(self.ours.exists())
        self.assertFalse((self.t / "etc/backshot1").exists())
        self.assertEqual(self.syncs(), 2)

    def test_without_moss_attended(self):
        out = self.adopt(moss=False)
        self.assertIn("A: the initramfs is prebuilt's; nothing else changes", out)
        self.assertFalse(self.ours.exists())


def pe(sections):
    """A minimal PE image, as systemd-stub's unified images are laid out:
    the DOS header's e_lfanew, PE\\0\\0, the COFF header, a zeroed optional
    header and the section table, each section's data 512-aligned (raw size
    padded, virtual size the data's own)."""
    opt = 240
    table = 0x40 + 24 + opt
    data_at = (table + 40 * len(sections) + 511) // 512 * 512
    head = bytearray(data_at)
    head[0:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x40)
    head[0x40:0x44] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", head, 0x44, 0x8664, len(sections), 0, 0, 0, opt, 0x22)
    body = bytearray()
    for i, (name, data) in enumerate(sections):
        raw = (len(data) + 511) // 512 * 512
        struct.pack_into("<8sIIII", head, table + 40 * i, name.encode(), len(data), 0x1000 * (i + 1), raw, data_at + len(body))
        body += data + b"\0" * (raw - len(data))
    return bytes(head + body)


# mkinitcpio -P on a fake root: each unified image's .cmdline from the files
# build_uki reads (the first of etc/kernel/cmdline and usr/lib/kernel/cmdline,
# then etc/cmdline.d/*.conf, # lines left out, newlines made spaces), or from
# ROOT/stale when it's there (an image that ignores the rewrite); its initrd's
# etc/crypttab, for the fake lsinitcpio, from etc/crypttab.initramfs.
MKINITCPIO = r'''#!/usr/bin/env python3
import os, re, shutil, struct, sys
ROOT = %(root)r
%(pe)s
assert sys.argv[1:] == ["-P"], sys.argv
open(os.path.join(ROOT, "mkinitcpio-calls"), "a").write("P\n")
files = [f for f in (ROOT + "/etc/kernel/cmdline", ROOT + "/usr/lib/kernel/cmdline") if os.path.isfile(f)][:1]
d = ROOT + "/etc/cmdline.d"
if os.path.isdir(d):
    files += sorted(os.path.join(d, n) for n in os.listdir(d) if n.endswith(".conf"))
if os.path.exists(ROOT + "/stale"):
    files = [ROOT + "/stale"]
lines = [l for f in files for l in open(f, encoding="utf-8").read().split("\n") if l and not l.startswith("#")]
cmdline = re.sub(r"\n+", " ", "\n".join(lines) + "\n")
for name in ("arch-linux.efi", "arch-linux-fallback.efi"):
    out = os.path.join(ROOT, "efi/EFI/Linux", name)
    with open(out, "wb") as f:
        f.write(pe([(".osrel", b"ID=arch\n"), (".cmdline", (cmdline + "\n").encode() + b"\0"), (".initrd", b"070701"), (".linux", b"MZkernel")]))
    ird = os.path.join(ROOT, "initrd", name)
    shutil.rmtree(ird, ignore_errors=True)
    os.makedirs(ird + "/etc")
    if os.path.exists(ROOT + "/etc/crypttab.initramfs"):
        shutil.copy(ROOT + "/etc/crypttab.initramfs", ird + "/etc/crypttab")
'''


@unittest.skipUnless(BASH, "needs bash 4 or newer")
class UkiCmdline(Tmp):
    """Family S on mkinitcpio's unified images: the files the command line
    is baked from, taking the rd.luks arguments out of them, their crypttab
    lines, the boot loader's own, and reading an image's .cmdline."""

    def sh(self, expr, stdin=None, rc=0):
        t = self.t
        script = (f'. "{BS1}/bs1-luks"; LK_KEYSPEC={KEYSPEC}; LUKS_ORIG="{t}/orig"; LK_CMDLINE="{t}/proc-cmdline"; '
                  f'LK_UKI_PRESETS="{t}/etc/mkinitcpio.d"; LK_UKI_FILES="{t}/etc/kernel/cmdline {t}/usr/lib/kernel/cmdline"; '
                  f'LK_UKI_DROPIN="{t}/etc/cmdline.d"\n{expr}')
        r = subprocess.run([BASH, "-c", script], input=stdin, capture_output=True, text=True, env=self.env())
        self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
        return r.stdout

    def file(self, rel, text):
        p = self.t / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def drop(self, text):
        f = self.file("in", text)
        self.sh(f'lk_uki_newfile "{f}" "{self.t}/out"')
        return (self.t / "out").read_text()

    def test_drop_the_thinkpad_line(self):
        self.assertEqual(self.drop(f"rd.luks.name={UUID}=root rd.luks.options={UUID}=discard root=/dev/mapper/root "
                                   "rootflags=subvol=/@,compress=zstd:3 rw quiet\n"),
                         "root=/dev/mapper/root rootflags=subvol=/@,compress=zstd:3 rw quiet\n")

    def test_drop_keeps_the_rest_byte_for_byte(self):
        self.assertEqual(self.drop(f"# rd.luks.options={UUID}=discard, a note\n  quiet\t rd.luks.uuid={UUID}  rw  \n"
                                   f"rd.luks.options=discard\nsplash rd.luks.key=/k luks.options=x\n"),
                         f"# rd.luks.options={UUID}=discard, a note\n  quiet  rw  \n\nsplash\n")
        self.assertEqual(self.drop(f"root=/dev/mapper/root rd.luks.options={UUID}=discard"), "root=/dev/mapper/root")
        self.assertEqual(self.drop("rd.luksfoo=1 xrd.luks.uuid=2 rw\n"), "rd.luksfoo=1 xrd.luks.uuid=2 rw\n")

    def test_files_in_mkinitcpio_order(self):
        self.assertEqual(self.sh("lk_uki_files"), "")
        self.file("usr/lib/kernel/cmdline", "quiet\n")
        self.assertEqual(self.sh("lk_uki_files"), f"{self.t}/usr/lib/kernel/cmdline\n")
        self.file("etc/kernel/cmdline", "root=x\n")
        self.file("etc/cmdline.d/10-b.conf", "b\n")
        self.file("etc/cmdline.d/9-a.conf", "# note\na\n")
        self.file("etc/cmdline.d/notes.txt", "c\n")
        files = self.sh("lk_uki_files").split()
        self.assertEqual(files, [f"{self.t}/etc/kernel/cmdline", f"{self.t}/etc/cmdline.d/9-a.conf", f"{self.t}/etc/cmdline.d/10-b.conf"])
        self.assertEqual(self.sh("lk_uki_text " + " ".join(files)).split(), ["root=x", "a", "b"])

    def test_presets(self):
        self.assertEqual(self.sh("lk_uki_presets").strip(), "no no")
        p = self.file("etc/mkinitcpio.d/linux.preset", "ALL_kver=/boot/vmlinuz-linux\nPRESETS=('default' 'fallback')\n"
                      "default_image=/boot/initramfs-linux.img\nfallback_image=/boot/initramfs-linux-fallback.img\nfallback_options=\"-S autodetect\"\n")
        self.assertEqual(self.sh("lk_uki_presets").strip(), "no no")
        p.write_text("PRESETS=('default' 'fallback')\ndefault_uki=/efi/EFI/Linux/arch-linux.efi\n"
                     "fallback_uki=/efi/EFI/Linux/arch-linux-fallback.efi\nfallback_options=\"-S autodetect\"\n")
        self.assertEqual(self.sh("lk_uki_presets").strip(), "yes no")
        for own in ('ALL_cmdline=/etc/kernel/other\n', 'fallback_cmdline=/etc/kernel/other\n',
                    'fallback_options=(-S autodetect --cmdline /etc/kernel/other)\n', 'default_options="--no-cmdline"\n'):
            with self.subTest(own):
                p.write_text("PRESETS=('default' 'fallback')\ndefault_uki=/efi/a.efi\nfallback_uki=/efi/b.efi\n" + own)
                self.assertEqual(self.sh("lk_uki_presets").strip(), "yes yes")

    def map(self, args, vols=(f"root:{UUID}",), ini_text=None):
        ini = self.t / "crypttab.initramfs"
        if ini_text is not None:
            ini.write_text(ini_text)
        out = self.sh(f'lk_uki_map "{ini}" ' + " ".join(vols), "\n".join(args) + "\n")
        return sorted(out.splitlines())

    def test_map_the_thinkpad_line(self):
        self.assertEqual(self.map([f"rd.luks.name={UUID}=root", f"rd.luks.options={UUID}=discard"]), [f"vol root {UUID} discard"])

    def test_map_uuid_and_luks_prefix(self):
        self.assertEqual(self.map([f"rd.luks.uuid=luks-{UUID}"], vols=(f"luks-{UUID}:{UUID}",)), [f"vol luks-{UUID} {UUID} -"])
        self.assertEqual(self.map([f"luks.uuid={UUID}", f"luks.options={UUID}=discard,keyfile-timeout=5s"]),
                         [f"vol root {UUID} discard,keyfile-timeout=5s"])

    def test_map_options_for_every_volume(self):
        # Only to a volume crypttab doesn't list (systemd-cryptsetup-generator).
        self.assertEqual(self.map([f"rd.luks.uuid={UUID}", "rd.luks.options=discard"]), [f"vol root {UUID} discard"])
        self.assertEqual(self.map([f"rd.luks.uuid={UUID}", "rd.luks.options=discard"], ini_text=f"root UUID={UUID} none luks\n"),
                         [f"vol root {UUID} -"])
        self.assertEqual(self.map([f"rd.luks.uuid={UUID}", "rd.luks.options=discard", f"rd.luks.options={UUID}="]), [f"vol root {UUID} ,"])

    def test_map_two_volumes(self):
        self.assertEqual(self.map([f"rd.luks.name={UUID}=root", f"rd.luks.name={OTHER}=swap", f"rd.luks.options={OTHER}=discard"],
                                  vols=(f"root:{UUID}", f"swap:{OTHER}")),
                         [f"vol root {UUID} -", f"vol swap {OTHER} discard"])

    def test_map_refusals(self):
        for args, why in [
            ([f"rd.luks.name={UUID}=root", f"rd.luks.options={UUID}=tpm2-device=auto"], "a token opens it"),
            ([f"rd.luks.name={UUID}=root", f"rd.luks.options={UUID}=discard,fido2-device=auto"], "a token opens it"),
            ([f"rd.luks.name={UUID}=root", f"rd.luks.options={UUID}=pkcs11-uri=auto"], "a token opens it"),
            ([f"rd.luks.name={UUID}=root", f"rd.luks.key={UUID}=/etc/keys/root.key"], "a key of its own"),
            ([f"rd.luks.name={UUID}=root", "rd.luks.key=/etc/all.key"], "a key of its own"),
            ([f"rd.luks.name={OTHER}=root"], f"{OTHER} isn't a LUKS volume here"),
            ([f"rd.luks.name={UUID}=cryptroot"], "open as root, not cryptroot"),
            ([f"rd.luks.options={OTHER}=discard"], f"{OTHER} isn't a LUKS volume here"),
            ([f"rd.luks.name={UUID}=root", "rd.luks.crypttab=no"], "only rd.luks.name"),
            ([f"rd.luks.name={UUID}=root", f"rd.luks.data={UUID}=/dev/sdb1"], "only rd.luks.name"),
            (["luks=no"], "only rd.luks.name"),
        ]:
            with self.subTest(args):
                out = self.map(args)
                self.assertTrue(any(l.startswith("bad ") and why in l for l in out), out)

    def test_map_a_line_the_command_line_leaves_out(self):
        out = self.map([f"rd.luks.name={UUID}=root"], ini_text=f"root UUID={UUID} none\nhome UUID={OTHER} none\n")
        self.assertTrue(any("home isn't named on the command line" in l for l in out), out)
        out = self.map([f"rd.luks.name={UUID}=root"], ini_text=f"cryptroot UUID={UUID} none\n")
        self.assertTrue(any(f"has {UUID} as cryptroot, but it's open as root" in l for l in out), out)

    def test_stray_is_the_boot_loader(self):
        k = self.file("etc/kernel/cmdline", "root=/dev/mapper/root rw\n")
        self.file("proc-cmdline", f"rd.luks.name={UUID}=root rd.luks.options={UUID}=discard root=/dev/mapper/root rw\n")
        self.assertEqual(self.sh(f'lk_uki_stray "{k}"').strip(), f"rd.luks.name={UUID}=root rd.luks.options={UUID}=discard")
        # adopt's copy of the file as it was: the arguments are only waiting for the next boot.
        self.file(f"orig/{str(k).replace('/', '%')}", f"rd.luks.name={UUID}=root\nrd.luks.options={UUID}=discard root=/dev/mapper/root rw\n")
        self.assertEqual(self.sh(f'lk_uki_stray "{k}"').strip(), "")

    def test_pe_section(self):
        img = self.t / "arch-linux.efi"
        img.write_bytes(pe([(".osrel", b"ID=arch\n"), (".cmdline", b"root=/dev/mapper/root rw quiet\n\0"), (".linux", b"MZ" + b"k" * 3000)]))
        self.assertEqual(self.sh(f'lk_pe_section "{img}" .cmdline'), "root=/dev/mapper/root rw quiet\n")
        self.assertEqual(self.sh(f'lk_pe_section "{img}" .osrel'), "ID=arch\n")
        self.sh(f'lk_pe_section "{img}" .initrd', rc=1)
        (self.t / "not-pe").write_bytes(b"\x7fELF" + b"\0" * 200)
        self.sh(f'lk_pe_section "{self.t}/not-pe" .cmdline', rc=1)

    @unittest.skipUnless(shutil.which("objcopy"), "no objcopy")
    def test_pe_section_as_objcopy_reads_it(self):
        """systemd-boot's stub, where the host has one, read both ways."""
        stubs = [p for p in Path("/usr/lib/systemd/boot/efi").glob("linux*.efi.stub")] if Path("/usr/lib/systemd/boot/efi").is_dir() else []
        if not stubs:
            self.skipTest("no systemd-boot stub here")
        out = self.t / "sdmagic"
        subprocess.run(["objcopy", "-O", "binary", "--only-section=.sdmagic", str(stubs[0]), str(out)], check=True)
        self.assertEqual(self.sh(f'lk_pe_section "{stubs[0]}" .sdmagic'), out.read_bytes().replace(b"\0", b"").decode())


@unittest.skipUnless(BASH, "needs bash 4 or newer")
class AdoptUki(Tmp):
    """luks_adopt and luks_off on a fake Arch: sd-encrypt, mkinitcpio presets
    building unified images, systemd-boot, the command line in
    /etc/kernel/cmdline: the Thinkpad's, 2026-11-18."""

    LINE = (f"rd.luks.name={UUID}=root rd.luks.options={UUID}=discard root=/dev/mapper/root "
            "rootflags=subvol=/@,compress=zstd:3 rw quiet\n")

    def setUp(self):
        super().setUp()
        t = self.t
        self.drive = t / "drive"
        here = self.drive / "backshot1"
        (here / "keys").mkdir(parents=True)
        shutil.copy(BS1 / "bs1-unlock-key", here / "bs1-unlock-key")
        (here / "keys/disk-passphrase").write_text("s3cret")
        for d in ("etc/kernel", "etc/mkinitcpio.d", "etc/mkinitcpio.conf.d", "efi/EFI/Linux", "efivars"):
            (t / d).mkdir(parents=True)
        self.cmdline = t / "etc/kernel/cmdline"
        self.cmdline.write_text(self.LINE)
        self.ini = t / "etc/crypttab.initramfs"
        self.preset = t / "etc/mkinitcpio.d/linux.preset"
        self.preset.write_text(textwrap.dedent(f"""\
            # mkinitcpio preset file for the 'linux' package
            ALL_kver="/boot/vmlinuz-linux"
            PRESETS=('default' 'fallback')
            default_uki="{t}/efi/EFI/Linux/arch-linux.efi"
            fallback_uki="{t}/efi/EFI/Linux/arch-linux-fallback.efi"
            fallback_options="-S autodetect"
            """))
        # systemd-stub's cmdline is the image's .cmdline, as it booted.
        self.boot()
        self.stub = t / "efivars/StubInfo-4a67b082-0a4c-41cf-b6c7-440b29bb8c4f"
        self.stub.write_bytes(b"\x06\0\0\0s\0d\0")
        src = Path(__file__).read_text()
        pe_src = re.search(r"^def pe\(sections\):\n.*?\n    return bytes\(head \+ body\)\n", src, re.M | re.S).group(0)
        exe(self.bin / "mkinitcpio", MKINITCPIO % {"root": str(t), "pe": pe_src})
        exe(self.bin / "lsinitcpio", textwrap.dedent(f"""\
            #!/bin/sh
            if [ "$1" = -x ]; then cp -R "{t}/initrd/$(basename "$2")/." .; exit 0; fi
            [ -d "{t}/initrd/$(basename "$1")" ] || exit 1
            echo etc/crypttab
            echo usr/lib/modules/6.17.8-arch1-1/kernel/drivers/usb/storage/usb-storage.ko.zst
            """))
        exe(self.bin / "findmnt", f'#!/bin/sh\ncase "$2" in TARGET) echo "{self.drive}" ;; SOURCE) echo /dev/sdb1 ;; FSTYPE) echo xfs ;; esac\n')
        exe(self.bin / "blkid", "#!/bin/sh\necho mybig512\n")
        exe(self.bin / "cryptsetup", "#!/bin/sh\nexit 0\n")
        exe(self.bin / "modinfo", "#!/bin/sh\nexit 1\n")
        exe(self.bin / "sleep", "#!/bin/sh\nexit 0\n")
        self.vols = f"/dev/nvme0n1p2 {UUID} root"
        self.early = "root"

    def boot(self):
        """A reboot: the running command line is the default image's (the
        file's before the first remake)."""
        img = self.t / "efi/EFI/Linux/arch-linux.efi"
        if img.exists():
            line = self.cmdline_of(img)
        else:
            line = " ".join(self.cmdline.read_text().split()) if self.cmdline.exists() else self.LINE.strip()
        (self.t / "proc-cmdline").write_text(line + "\n")

    def cmdline_of(self, img):
        data = img.read_bytes()
        i = data.index(b".cmdline")
        size, _, _, ptr = struct.unpack_from("<IIII", data, i + 8)
        return data[ptr:ptr + size].rstrip(b"\0").decode().strip()

    def adopt(self, what="luks_adopt", rc=0):
        t = self.t
        # As bs1-adopt runs it: bs1-common's set -uo pipefail.
        script = textwrap.dedent(f"""\
            set -uo pipefail
            . "{BS1}/bs1-luks"
            LUKS_DIR="{t}/etc/backshot1" LUKS_ORIG="{t}/etc/backshot1/orig" LUKS_MADE="{t}/etc/backshot1/made" LUKS_REC="{t}/etc/backshot1/luks-unlock"
            LK_CMDLINE="{t}/proc-cmdline" LK_CRYPTTAB="{t}/etc/crypttab" LK_CRYPTTAB_INI="{self.ini}" LK_MKI_CONF_D="{t}/etc/mkinitcpio.conf.d"
            LK_IMG_DIRS="{t}/efi" LK_UKI_PRESETS="{t}/etc/mkinitcpio.d" LK_UKI_FILES="{self.cmdline} {t}/usr/lib/kernel/cmdline"
            LK_UKI_DROPIN="{t}/etc/cmdline.d" LK_STUB_INFO="{self.stub}"
            here="{self.drive}/backshot1" init=systemd BACKUP_LABEL=mybig512 INTERNAL_SERIAL=S1
            internal_disk() {{ echo nvme0n1; }}
            is_abroot() {{ false; }}
            lk_generator() {{ echo mkinitcpio; }}
            lk_mkinitcpio_conf() {{ echo "HOOKS base systemd autodetect microcode modconf kms keyboard sd-vconsole block sd-encrypt filesystems fsck"; echo "FILES "; }}
            lk_luks_volumes() {{ printf '%s\\n' "{self.vols}"; }}
            lk_early() {{ [[ " {self.early} " == *" $1 "* ]]; }}
            {what}
            """)
        r = subprocess.run([BASH, "-c", script], capture_output=True, text=True, env=self.env())
        self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
        return r.stdout + r.stderr

    def remakes(self):
        p = self.t / "mkinitcpio-calls"
        return len(p.read_text().splitlines()) if p.exists() else 0

    def rec(self):
        return (self.t / "etc/backshot1/luks-unlock").read_text()

    def uki(self, name="arch-linux.efi"):
        return self.cmdline_of(self.t / "efi/EFI/Linux" / name)

    def test_the_thinkpad_adopt_rerun_reboot_and_off(self):
        out = self.adopt()
        self.assertIn("family S (initramfs: mkinitcpio)", out)
        self.assertEqual(self.ini.read_text(), f"root\tUUID={UUID}\t{KEYSPEC}\tdiscard,keyfile-timeout=30s\n")
        self.assertEqual(stat.S_IMODE(self.ini.stat().st_mode), 0o600)
        self.assertEqual(self.cmdline.read_text(), "root=/dev/mapper/root rootflags=subvol=/@,compress=zstd:3 rw quiet\n")
        self.assertEqual(self.remakes(), 1)
        for img in ("arch-linux.efi", "arch-linux-fallback.efi"):
            self.assertEqual(self.uki(img), "root=/dev/mapper/root rootflags=subvol=/@,compress=zstd:3 rw quiet")
            self.assertIn(f"{img}: ok", out)
        self.assertIn(f"volumes='root:{UUID}:S'", self.rec())
        self.assertIn(f"wrote {self.cmdline}", out)
        self.assertEqual((self.t / "etc/backshot1/orig" / str(self.cmdline).replace("/", "%")).read_text(), self.LINE)
        self.assertIn(str(self.ini), (self.t / "etc/backshot1/made").read_text())

        # Before the reboot /proc/cmdline still has the arguments: the same S, nothing remade.
        out = self.adopt()
        self.assertIn("family S", out)
        self.assertIn("unchanged; the initramfs isn't remade", out)
        self.assertEqual(self.remakes(), 1)

        self.boot()
        self.assertNotIn("rd.luks", (self.t / "proc-cmdline").read_text())
        out = self.adopt()
        self.assertIn("unchanged; the initramfs isn't remade", out)
        self.assertEqual(self.remakes(), 1)

        out = self.adopt("luks_off")
        self.assertIn(f"put back {self.cmdline}", out)
        self.assertEqual(self.cmdline.read_text(), self.LINE)
        self.assertFalse(self.ini.exists())
        self.assertFalse((self.t / "etc/mkinitcpio.conf.d/zz-backshot1.conf").exists())
        self.assertFalse((self.t / "etc/backshot1").exists())
        self.assertEqual(self.remakes(), 2)
        self.assertEqual(self.uki(), self.LINE.strip())

    def test_an_existing_crypttab_initramfs_line(self):
        # The command line's options were in force, not the line's; its
        # key-file options go with the drive's key.
        before = f"# <name> <device> <password> <options>\nroot\tUUID={UUID}\tnone\tluks,keyfile-size=512,keyfile-offset=8\n"
        self.ini.write_text(before)
        self.cmdline.write_text(f"rd.luks.uuid={UUID} rd.luks.options={UUID}=discard,no-read-workqueue root=/dev/mapper/root rw\n")
        self.boot()
        self.adopt()
        self.assertEqual(self.ini.read_text(), f"# <name> <device> <password> <options>\nroot\tUUID={UUID}\t{KEYSPEC}\tdiscard,no-read-workqueue,keyfile-timeout=30s\n")
        self.assertEqual(self.cmdline.read_text(), "root=/dev/mapper/root rw\n")
        self.adopt("luks_off")
        self.assertEqual(self.ini.read_text(), before)
        self.assertEqual(self.cmdline.read_text(), f"rd.luks.uuid={UUID} rd.luks.options={UUID}=discard,no-read-workqueue root=/dev/mapper/root rw\n")

    def test_a_cmdline_d_file(self):
        self.cmdline.write_text("root=/dev/mapper/root rw quiet\n")
        conf = self.t / "etc/cmdline.d/20-luks.conf"
        conf.parent.mkdir()
        conf.write_text(f"# the installer's\nrd.luks.name={UUID}=root\nrd.luks.options={UUID}=discard\n")
        self.boot()
        out = self.adopt()
        self.assertIn(f"wrote {conf}", out)
        self.assertEqual(conf.read_text(), "# the installer's\n\n\n")
        self.assertEqual(self.cmdline.read_text(), "root=/dev/mapper/root rw quiet\n")
        self.assertEqual(self.uki(), "root=/dev/mapper/root rw quiet")
        self.adopt("luks_off")
        self.assertEqual(conf.read_text(), f"# the installer's\nrd.luks.name={UUID}=root\nrd.luks.options={UUID}=discard\n")

    def test_adopt_with_no_reboot_since_the_hand_made_state(self):
        # The Thinkpad now: the arguments are back in the file, the running
        # command line (from images made without them) has none.
        (self.t / "proc-cmdline").write_text("root=/dev/mapper/root rootflags=subvol=/@,compress=zstd:3 rw quiet\n")
        out = self.adopt()
        self.assertIn("family S", out)
        self.assertEqual(self.uki(), "root=/dev/mapper/root rootflags=subvol=/@,compress=zstd:3 rw quiet")

    def test_an_image_that_still_overrides_puts_everything_back(self):
        (self.t / "stale").write_text(self.LINE)
        out = self.adopt(rc=1)
        self.assertIn(f"rd.luks.options={UUID}=discard, which overrides crypttab", out)
        self.assertIn("putting everything back", out)
        self.assertEqual(self.cmdline.read_text(), self.LINE)
        self.assertFalse(self.ini.exists())
        self.assertFalse((self.t / "etc/backshot1").exists())
        self.assertEqual(self.remakes(), 2)

    def test_the_boot_loader_adds_them(self):
        self.cmdline.write_text("root=/dev/mapper/root rw\n")
        (self.t / "proc-cmdline").write_text(f"rd.luks.name={UUID}=root rd.luks.options={UUID}=discard root=/dev/mapper/root rw\n")
        out = self.adopt()
        self.assertIn("the boot loader adds", out)
        self.assertIn("family=A\n", self.rec())
        self.assertEqual(self.cmdline.read_text(), "root=/dev/mapper/root rw\n")
        self.assertEqual(self.remakes(), 0)

    def test_refusals_stay_attended(self):
        cases = [
            ("tpm2", lambda: self.cmdline.write_text(f"rd.luks.name={UUID}=root rd.luks.options={UUID}=tpm2-device=auto root=/dev/mapper/root\n"), "a token opens it"),
            ("key", lambda: self.cmdline.write_text(f"rd.luks.name={UUID}=root rd.luks.key={UUID}=/etc/cryptsetup-keys.d/root.key root=/dev/mapper/root\n"), "a key of its own"),
            ("unknown", lambda: self.cmdline.write_text(f"rd.luks.name={OTHER}=root rd.luks.options={OTHER}=discard root=/dev/mapper/root\n"), "isn't a LUKS volume here"),
            ("usr-lib", lambda: (self.cmdline.unlink(), (self.t / "usr/lib/kernel").mkdir(parents=True), (self.t / "usr/lib/kernel/cmdline").write_text(self.LINE)), "doesn't rewrite"),
            ("no-stub", lambda: self.stub.unlink(), "didn't come through a unified kernel image"),
            ("preset", lambda: self.preset.write_text(self.preset.read_text() + "ALL_cmdline=/etc/kernel/cmdline-arch\n"), "a command line of its own"),
            ("no-uki", lambda: self.preset.write_text("PRESETS=('default')\ndefault_image=/boot/initramfs-linux.img\n"), "the boot loader's entry or GRUB's"),
        ]
        for name, setup, why in cases:
            with self.subTest(name):
                self.tearDown_case()
                setup()
                self.boot()
                out = self.adopt()
                self.assertIn(why, out)
                self.assertIn("family=A\n", self.rec())
                self.assertFalse(self.ini.exists())
                self.assertEqual(self.remakes(), 0)
                self.assertFalse((self.t / "etc/mkinitcpio.conf.d/zz-backshot1.conf").exists())

    def tearDown_case(self):
        """Each subTest from the Thinkpad's state, nothing adopted."""
        shutil.rmtree(self.t / "etc/backshot1", ignore_errors=True)
        shutil.rmtree(self.t / "usr", ignore_errors=True)
        self.cmdline.write_text(self.LINE)
        self.stub.write_bytes(b"\x06\0\0\0s\0d\0")
        self.preset.write_text(textwrap.dedent(f"""\
            PRESETS=('default' 'fallback')
            default_uki="{self.t}/efi/EFI/Linux/arch-linux.efi"
            fallback_uki="{self.t}/efi/EFI/Linux/arch-linux-fallback.efi"
            fallback_options="-S autodetect"
            """))

    def test_no_override_is_plain_s(self):
        # rd.luks.uuid alone (as gentoo-systemd's): the line's device made UUID=, the file as it is.
        self.cmdline.write_text(f"rd.luks.uuid={UUID} root=/dev/mapper/root rw\n")
        self.boot()
        self.adopt()
        self.assertEqual(self.ini.read_text(), f"root\tUUID={UUID}\t{KEYSPEC}\tkeyfile-timeout=30s\n")
        self.assertEqual(self.cmdline.read_text(), f"rd.luks.uuid={UUID} root=/dev/mapper/root rw\n")
        self.assertFalse((self.t / "etc/backshot1/orig").exists() and any((self.t / "etc/backshot1/orig").iterdir()))


CORPUS = TESTS.parent.parent.parent / "holt" / "tests" / "corpus"


class LayoutKeys(Tmp):
    """layout_keys: load's --key-file values from a layout dump, over the
    corpus's live dumps and edits of them.  Any bash runs it (a python3
    heredoc)."""

    def keys(self, layout):
        if isinstance(layout, dict):
            path = self.t / "layout.json"
            path.write_text(json.dumps(layout))
        else:
            path = CORPUS / layout / "src.json"
        lib = functions(BS1 / "bs1-common", "layout_keys")
        r = subprocess.run([BASH or "bash", "-c", f'{lib}\nlayout_keys "$1" FILE', "x", str(path)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    def geli(self):
        """The freebsd15-geli dump and its provider's partition (vtbd0p3)."""
        d = json.loads((CORPUS / "freebsd15-geli" / "src.json").read_text())
        p3 = next(p for p in d["storage"]["disks"][0]["partitions"] if p["name"] == "vtbd0p3")
        return d, p3

    def test_geli_corpus_names_the_partition(self):
        self.assertEqual(self.keys("freebsd15-geli"), ["vtbd0p3=FILE"])

    def test_geli_onetime_is_skipped(self):
        d, p3 = self.geli()
        p3["geli"]["onetime"] = True
        self.assertEqual(self.keys(d), [])

    def test_geli_keyfile_only_is_skipped(self):
        d, p3 = self.geli()
        p3["geli"].update(keyed_by="keyfile", keyfiles=["/boot/keys/root.key"])
        self.assertEqual(self.keys(d), [])

    def test_geli_keyfile_and_passphrase_is_listed(self):
        d, p3 = self.geli()
        p3["geli"].update(keyed_by="both", keyfiles=["/boot/keys/root.key"])
        self.assertEqual(self.keys(d), ["vtbd0p3=FILE"])

    def test_geli_slot_1_adds_no_line(self):
        d, p3 = self.geli()
        p3["geli"]["keys"] = [0, 1]
        self.assertEqual(self.keys(d), ["vtbd0p3=FILE"])

    def test_geli_content_without_a_record_is_listed(self):
        d, p3 = self.geli()
        del p3["geli"]
        self.assertEqual(p3["content"]["type"], "geli")
        self.assertEqual(self.keys(d), ["vtbd0p3=FILE"])

    def test_geli_whole_disk_is_listed(self):
        d, p3 = self.geli()
        rec = dict(p3["geli"], provider="vtbd1.eli")
        d["storage"]["disks"].append({"name": "vtbd1", "content": dict(p3["content"]), "geli": rec, "partitions": []})
        self.assertEqual(self.keys(d), ["vtbd0p3=FILE", "vtbd1=FILE"])

    def test_luks_corpus_unchanged(self):
        for name, want in [("ubuntu26-luks", "dm_crypt-0"), ("pikaos4-luks", "crypt_root"), ("chimera-uefi", "cryptroot"),
                           ("debian13-stack", "md1_crypt"), ("gentoo-systemd", "cryptroot"), ("omarchy", "root"),
                           ("elementary8-luks", "cryptdata")]:
            with self.subTest(name):
                self.assertEqual(self.keys(name), [f"{want}=FILE"])

    def test_zfs_encryption_corpus_unchanged(self):
        # rpool's key is raw (from the LUKS keystore): only the keystore asks.
        self.assertEqual(self.keys("ubuntu26-zfsmirror"), ["keystore-rpool=FILE"])

    def test_zfs_keystore_and_plain_swap_corpus(self):
        # The Thinkpad's 26.10 ZFS + encryption: the keystore asks, the
        # plain swap (its crypttab name redacted to label-1) doesn't.
        self.assertEqual(self.keys("bs1-ubuntu2610-zfsenc"), ["keystore-rpool=FILE"])

    def test_plain_random_key_swap_is_not_listed(self):
        # Ubuntu 26.10's ZFS + encryption: dm_crypt-0, plain dm-crypt swap
        # keyed from /dev/urandom at every boot, beside the keystore.
        d = json.loads((CORPUS / "ubuntu26-zfsmirror" / "src.json").read_text())
        s = d["storage"]
        tab = {"name": "dm_crypt-0", "device": "PARTUUID=7d881a58-39a3-4e53-8607-0f698dc7ae88", "key_file": "/dev/urandom",
               "options": ["cipher=aes-cbc-essiv:sha256", "initramfs", "plain", "size=256", "swap"], "resolves_to": "vda3"}
        s["crypttab"] = [tab]
        s["dm_devices"].append({"name": "dm-9", "slaves": ["vda3"], "content": {"type": "swap"},
                                "dm": {"dm_name": "dm_crypt-0", "dm_uuid": "CRYPT-PLAIN-dm_crypt-0", "target": "crypt", "crypt_type": "PLAIN"}})
        self.assertEqual(self.keys(d), ["keystore-rpool=FILE"])
        tab["key_file"] = "/etc/keys/swap.key"
        self.assertEqual(self.keys(d), ["dm_crypt-0=FILE", "keystore-rpool=FILE"])

    def test_zfs_passphrase_root_is_listed(self):
        d = json.loads((CORPUS / "ubuntu26-zfsmirror" / "src.json").read_text())
        for ds in d["storage"]["zfs"]["tools"]["datasets"]:
            if ds["name"] == "rpool":
                ds.update(keyformat="passphrase", keylocation="prompt")
            elif ds.get("encryptionroot") == "rpool":
                ds["keyformat"] = "passphrase"
        self.assertEqual(self.keys(d), ["keystore-rpool=FILE", "rpool=FILE"])


if __name__ == "__main__":
    unittest.main()
