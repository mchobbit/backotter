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

    def test_k_is_the_keystore_hooks_mark(self):
        # Ubuntu's ZFS keystore (family S, volume HOW K): the dracut hooks' mark.
        self.names = "keystore-rpool"
        self.record("S", volumes=f"keystore-rpool:{UUID}:K")
        self.assertTrue(self.call("unlock_mode nvme0n1").startswith("attended: keystore-rpool was typed"))
        self.mark.write_text("keystore-rpool drive\n")
        out = self.call("unlock_mode nvme0n1")
        self.assertTrue(out.startswith("drive: ") and "(family S)" in out, out)
        self.assertIn("  keystore-rpool drive\n", out)
        self.mark.write_text("keystore-rpool fetched\n")
        self.assertTrue(self.call("unlock_mode nvme0n1").startswith("attended: keystore-rpool was fetched"))


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
    """luks_adopt and luks_off on a fake Ubuntu 26.10 with ZFS + encryption
    (the Thinkpad, 2026-11-19): the keystore, LUKS on rpool/keystore, is
    opened by Ubuntu's dracut zfs module with `systemd-cryptsetup attach` and
    no key file, so it is family S's volume HOW K: BackShot1's dracut module
    puts the drive's key in /run/cryptsetup-keys.d before that hook runs.
    Anything else about it stays attended (family A), nothing changed."""

    ZFS_HOOK = textwrap.dedent("""\
        #!/bin/sh
        _open_and_mount_luks_keystore() {
            pool="$1"
            ks="/dev/zvol/$pool/keystore"
            systemd-cryptsetup attach "keystore-${pool}" "${ks}"
        }
        case "$KEYLOCATION" in
            "file:///run/keystore/${ENCRYPTIONROOT}/"*)
                _open_and_mount_luks_keystore "${ENCRYPTIONROOT}" "${KEYLOCATION#file://}"
                ;;
        esac
        """)
    SWAP = "dm_crypt-0 PARTUUID=7d881a58 /dev/urandom cipher=aes-cbc-essiv:sha256,initramfs,plain,size=256,swap\n"

    def setUp(self):
        super().setUp()
        t = self.t
        self.here = t / "drive/backshot1"
        (self.here / "keys").mkdir(parents=True)
        shutil.copy(BS1 / "bs1-unlock-key", self.here / "bs1-unlock-key")
        (self.here / "keys/disk-passphrase").write_text("s3cret")
        (t / "proc-cmdline").write_text("BOOT_IMAGE=/vmlinuz-7.3.0-10-generic root=ZFS=rpool/ROOT/ubuntu_x ro quiet splash\n")
        (t / "crypttab").write_text(self.SWAP)
        self.mods = t / "usr/lib/dracut/modules.d"
        (self.mods / "90zfs").mkdir(parents=True)
        (self.mods / "90zfs/zfs-load-key.sh").write_text(self.ZFS_HOOK)
        (t / "zvol/rpool").mkdir(parents=True)
        (t / "zvol/rpool/keystore").symlink_to("/dev/zd0")
        (t / "boot").mkdir()
        self.conf = t / "etc/dracut.conf.d/90-backshot1.conf"
        self.ours = self.mods / "99backshot1"
        self.mapping = "keystore-rpool"
        self.gen = "dracut"
        self.zfs = {"encryptionroot rpool": "rpool", "keylocation rpool": "file:///run/keystore/rpool/system.key",
                    "encryptionroot rpool/ROOT/ubuntu_x": "rpool"}
        exe(self.bin / "findmnt", textwrap.dedent(f"""\
            #!/bin/sh
            case "$1 $2" in
            "-nro SOURCE") echo rpool/ROOT/ubuntu_x ;;
            "-no TARGET") echo "{t}/drive" ;;
            "-no SOURCE") echo /dev/sdb1 ;;
            "-no FSTYPE") echo xfs ;;
            esac
            """))
        exe(self.bin / "blkid", "#!/bin/sh\necho mybig512\n")
        exe(self.bin / "cryptsetup", f'#!/bin/sh\necho "$@" >>"{t}/cryptsetup-calls"\nexit 0\n')
        exe(self.bin / "modinfo", "#!/bin/sh\nexit 1\n")
        exe(self.bin / "sleep", "#!/bin/sh\nexit 0\n")
        # GNU readlink -f follows the zvol's link to /dev/zd0 whether it is
        # there or not; macOS's doesn't.
        exe(self.bin / "readlink", '#!/usr/bin/env python3\nimport os, sys\na = sys.argv[1:]\nprint(os.path.realpath(a[1]) if a[0] == "-f" else os.readlink(a[0]))\n')
        # The remade image's listing: BackShot1's hooks only when its module
        # is there and the drop-in asks for it, as dracut would take them.
        rel = str(t).lstrip("/")
        exe(self.bin / "lsinitrd", textwrap.dedent(f"""\
            #!/bin/sh
            [ "$1" = -m ] && {{ printf 'systemd\\nzfs\\n'; exit 0; }}
            echo "-rwxr-xr-x 1 root root 1 Nov 20 00:00 usr/bin/systemd-cryptsetup"
            echo "-rwxr-xr-x 1 root root 1 Nov 20 00:00 var/lib/dracut/hooks/pre-mount/90-zfs-load-key.sh"
            [ -e "{t}/no-hooks" ] && exit 0
            if [ -f "{self.ours}/module-setup.sh" ] && grep -q 'add_dracutmodules+=" backshot1 "' "{self.conf}"; then
                echo "-rwxr-xr-x 1 root root 1 Nov 20 00:00 var/lib/dracut/hooks/pre-mount/85-backshot1-keystore.sh"
                echo "-rwxr-xr-x 1 root root 1 Nov 20 00:00 var/lib/dracut/hooks/pre-mount/95-backshot1-keystore-done.sh"
                echo "-rwxr-xr-x 1 root root 1 Nov 20 00:00 {rel}/usr/local/lib/backshot1/unlock-key"
                echo "-rw-r--r-- 1 root root 1 Nov 20 00:00 {rel}/etc/backshot1/luks-unlock"
            fi
            """))

    def adopt(self, what="luks_adopt", rc=0):
        t = self.t
        (self.bin / "zfs").unlink(missing_ok=True)
        cases = "".join(f'"{k}") echo "{v}" ;;\n' for k, v in self.zfs.items())
        exe(self.bin / "zfs", f'#!/bin/sh\n[ "$1 $2 $3 $4" = "get -H -o value" ] || exit 1\ncase "$5 $6" in\n{cases}*) echo - ;;\nesac\n')
        script = textwrap.dedent(f"""\
            . "{BS1}/bs1-luks"
            LUKS_DIR="{t}/etc/backshot1" LUKS_ORIG="{t}/etc/backshot1/orig" LUKS_MADE="{t}/etc/backshot1/made" LUKS_REC="{t}/etc/backshot1/luks-unlock"
            LK_CMDLINE="{t}/proc-cmdline" LK_CRYPTTAB="{t}/crypttab" LK_IMG_DIRS="{t}/boot"
            LK_DRACUT_CONF="{self.conf}" LK_DRACUT_MODS="{self.mods}" UNLOCK_KEY="{t}/usr/local/lib/backshot1/unlock-key"
            ZVOL_DIR="{t}/zvol"
            here="{self.here}" init=systemd BACKUP_LABEL=mybig512 INTERNAL_SERIAL=S1
            internal_disk() {{ echo nvme0n1; }}
            is_abroot() {{ false; }}
            lk_generator() {{ echo {self.gen}; }}
            lk_dracut_systemd() {{ true; }}
            lk_luks_volumes() {{ echo "/dev/zd0 {UUID} {self.mapping}"; }}
            lk_early() {{ false; }}
            lk_remake() {{ echo "$1" >>"{t}/remakes"; date >"{t}/boot/initrd.img-7.3.0-10-generic"; }}
            {what}
            """)
        r = subprocess.run([BASH, "-c", script], capture_output=True, text=True, env=self.env())
        self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
        return r.stdout + r.stderr

    def remakes(self):
        p = self.t / "remakes"
        return len(p.read_text().splitlines()) if p.exists() else 0

    def rec(self):
        return (self.t / "etc/backshot1/luks-unlock").read_text()

    def test_adopt_rerun_and_off(self):
        t = self.t
        out = self.adopt()
        self.assertIn(f"family S (initramfs: dracut): keystore-rpool:{UUID}:K", out)
        self.assertIn("--test-passphrase", (t / "cryptsetup-calls").read_text())
        rec = self.rec()
        self.assertIn("family=S\n", rec)
        self.assertIn(f"volumes='keystore-rpool:{UUID}:K'\n", rec)
        self.assertIn("path=/backshot1/keys/disk-passphrase\n", rec)
        self.assertIn("fstype=xfs\n", rec)
        conf = self.conf.read_text()
        self.assertIn('add_dracutmodules+=" backshot1 "', conf)
        self.assertIn('filesystems+=" xfs "', conf)
        setup = (self.ours / "module-setup.sh").read_text()
        self.assertIn("check() { return 255; }", setup)
        self.assertIn('inst_hook pre-mount 85 "$moddir/backshot1-keystore.sh"', setup)
        self.assertIn('inst_hook pre-mount 95 "$moddir/backshot1-keystore-done.sh"', setup)
        self.assertIn(f"inst_simple {t}/etc/backshot1/luks-unlock", setup)
        self.assertTrue((self.ours / "backshot1-keystore.sh").read_text().endswith(f"{t}/usr/local/lib/backshot1/unlock-key --keystore\n"))
        self.assertTrue((self.ours / "backshot1-keystore-done.sh").read_text().endswith("unlock-key --keystore-done\n"))
        self.assertEqual((t / "usr/local/lib/backshot1/unlock-key").read_text(), (BS1 / "bs1-unlock-key").read_text())
        self.assertEqual((t / "crypttab").read_text(), self.SWAP, "crypttab can't reach the keystore: left alone")
        self.assertEqual(self.remakes(), 1)

        out = self.adopt()
        self.assertIn("unchanged; the initramfs isn't remade", out)
        self.assertEqual(self.remakes(), 1)

        self.adopt("luks_off")
        self.assertFalse(self.ours.exists(), "--luks-off removes BackShot1's dracut module")
        self.assertFalse(self.conf.exists())
        self.assertFalse((t / "usr/local/lib/backshot1/unlock-key").exists())
        self.assertFalse((t / "etc/backshot1").exists())
        self.assertTrue((self.mods / "90zfs/zfs-load-key.sh").exists())
        self.assertEqual((t / "crypttab").read_text(), self.SWAP)
        self.assertEqual(self.remakes(), 2)

    def test_an_image_without_the_hooks_puts_everything_back(self):
        (self.t / "no-hooks").write_text("")
        out = self.adopt(rc=1)
        self.assertIn("has no pre-mount hook 85-backshot1-keystore.sh", out)
        self.assertIn("putting everything back", out)
        self.assertFalse(self.ours.exists())
        self.assertFalse((self.t / "etc/backshot1").exists())

    def attended(self, why):
        out = self.adopt()
        self.assertIn("luks: A: ", out)
        rec = self.rec()
        self.assertIn("family=A\n", rec)
        self.assertIn(why, rec.replace("'\\''", "'"))
        self.assertFalse(self.ours.exists())
        self.assertFalse(self.conf.exists())
        self.assertEqual(self.remakes(), 0)
        self.assertEqual((self.t / "crypttab").read_text(), self.SWAP)

    def test_zfs_module_without_ubuntus_patch(self):
        (self.mods / "90zfs/zfs-load-key.sh").write_text("#!/bin/sh\nzfs load-key \"$ENCRYPTIONROOT\"\n")
        self.attended("doesn't open it with systemd-cryptsetup attach")

    def test_no_zfs_module(self):
        shutil.rmtree(self.mods / "90zfs")
        self.attended("there's no zfs dracut module")

    def test_initramfs_tools(self):
        self.gen = "initramfs-tools"
        self.attended("only on dracut with systemd, not on initramfs-tools's initramfs)")

    def test_pool_keyed_otherwise(self):
        self.zfs["keylocation rpool"] = "prompt"
        self.attended("isn't an encryption root keyed from /run/keystore/rpool/ (keylocation prompt)")

    def test_root_not_under_the_pool(self):
        self.zfs["encryptionroot rpool/ROOT/ubuntu_x"] = "-"
        self.attended("but / (rpool/ROOT/ubuntu_x) isn't encrypted under rpool")

    def test_open_under_another_name(self):
        self.mapping = "ks"
        self.attended("ks is on a ZFS volume (/dev/zd0), but isn't open as Ubuntu's keystore-POOL")

    def test_another_zvol(self):
        (self.t / "zvol/rpool/keystore").unlink()
        (self.t / "zvol/rpool/keystore").symlink_to("/dev/zd16")
        self.attended("not on the ZFS volume rpool/keystore")


@unittest.skipUnless(BASH, "needs bash 4 or newer")
class UnlockKeyKeystore(Tmp):
    """bs1-unlock-key --keystore and --keystore-done: BackShot1's dracut
    hooks either side of Ubuntu's zfs-load-key.sh."""

    def setUp(self):
        super().setUp()
        self.root = self.t / "root"
        for d in ("etc/backshot1", "run", "dev/disk/by-label", "dev/mapper", "dev/zvol/rpool", "drive/backshot1/keys"):
            (self.root / d).mkdir(parents=True)
        (self.root / "drive/backshot1/keys/disk-passphrase").write_text("s3cret")
        (self.root / "dev/zvol/rpool/keystore").write_text("")
        exe(self.bin / "mount", f'#!/bin/sh\nfor d; do :; done\ncp -R "{self.root}/drive/." "$d/"\n')
        exe(self.bin / "umount", '#!/bin/sh\nrm -rf "$1"/*\n')
        exe(self.bin / "modprobe", "#!/bin/sh\nexit 0\n")
        exe(self.bin / "sleep", "#!/bin/sh\nexit 0\n")
        exe(self.bin / "cryptsetup", f'#!/bin/sh\necho "$@" >>"{self.t}/cryptsetup-calls"\nexit "$(cat "{self.t}/cryptsetup-rc" 2>/dev/null || echo 0)"\n')
        self.record()
        self.key = self.root / "run/cryptsetup-keys.d/keystore-rpool.key"

    def record(self, volumes=f"keystore-rpool:{UUID}:K"):
        (self.root / "etc/backshot1/luks-unlock").write_text(
            f"family=S\nlabel=mybig512\npath=/backshot1/keys/disk-passphrase\nfstype=ext4\nwait=2\nvolumes='{volumes}'\n")

    def plug(self):
        (self.root / "dev/disk/by-label/mybig512").write_text("")

    def opened(self):
        """systemd-cryptsetup opened the keystore, as 90-zfs-load-key.sh does."""
        (self.root / "dev/mapper/keystore-rpool").write_text("")

    def run_key(self, *args):
        return subprocess.run(["sh", str(BS1 / "bs1-unlock-key"), *args], capture_output=True, text=True,
                              env=self.env(BS1_UNLOCK_ROOT=str(self.root)))

    def marks(self):
        p = self.root / "run/backshot1-unlock"
        return p.read_text().splitlines() if p.exists() else []

    def test_the_drive_opens_it(self):
        self.plug()
        r = self.run_key("--keystore")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.key.read_text(), "s3cret")
        self.assertEqual(stat.S_IMODE(self.key.stat().st_mode), 0o600)
        self.assertEqual(self.marks(), [])
        self.opened()
        r = self.run_key("--keystore-done")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.marks(), ["keystore-rpool drive"])
        self.assertIn(f"--test-passphrase --key-file {self.key} {self.root}/dev/zvol/rpool/keystore", (self.t / "cryptsetup-calls").read_text())
        self.assertFalse(self.key.exists(), "the key is taken away again")
        self.assertFalse(self.key.parent.exists())

    def test_a_key_that_doesnt_fit_was_typed(self):
        self.plug()
        self.run_key("--keystore")
        self.opened()
        (self.t / "cryptsetup-rc").write_text("2")
        self.run_key("--keystore-done")
        self.assertEqual(self.marks(), ["keystore-rpool typed"])
        self.assertFalse(self.key.exists())

    def test_not_opened_was_typed(self):
        self.plug()
        self.run_key("--keystore")
        self.run_key("--keystore-done")
        self.assertEqual(self.marks(), ["keystore-rpool typed"])
        self.assertFalse(self.key.exists())

    def test_cryptsetup_that_cant_tell_is_fetched(self):
        self.plug()
        self.run_key("--keystore")
        self.opened()
        (self.t / "cryptsetup-rc").write_text("127")
        self.run_key("--keystore-done")
        self.assertEqual(self.marks(), ["keystore-rpool fetched"])

    def test_without_the_drive(self):
        r = self.run_key("--keystore")
        self.assertEqual(r.returncode, 0)
        self.assertIn("waiting up to 2 s", r.stderr)
        self.assertFalse(self.key.exists())
        self.assertEqual(self.marks(), ["keystore-rpool typed"])
        self.opened()
        self.run_key("--keystore-done")
        self.assertEqual(self.marks(), ["keystore-rpool typed"])
        self.assertFalse((self.t / "cryptsetup-calls").exists())

    def test_no_keystore_in_the_record(self):
        self.record(volumes="root:abc:S")
        self.plug()
        self.assertEqual(self.run_key("--keystore").returncode, 0)
        self.assertFalse(self.key.exists())
        self.assertEqual(self.marks(), [])


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


# Family Z: ZFSBootMenu's hooks for ZFS native encryption (bs1-luks).

FAKE_ZFS = r'''#!/bin/sh
# zfs get -H -o value PROP DS (T/zfs/DS/PROP); zfs load-key [-n] -L
# file://F ROOT (it opens when F holds T/want); zfs list -H -o COLUMNS ...
# (T/zfs-list-COLUMNS).
T=%(t)r
echo "$*" >>"$T/zfs-calls"
case $1 in
get)
    for a; do last=$a; done
    prop=$5
    f="$T/zfs/$last/$prop"
    [ -f "$f" ] && { cat "$f"; exit 0; }
    case $prop in keystatus) echo unavailable ;; *) echo - ;; esac
    ;;
load-key)
    for a; do last=$a; done
    for a; do case $a in file://*) key=${a#file://} ;; esac; done
    cmp -s "$key" "$T/want"
    ;;
list) cat "$T/zfs-list-$4" 2>/dev/null ;;
*) exit 1 ;;
esac
'''


@unittest.skipUnless(shutil.which("sh"), "no sh")
class UnlockKeyZfs(Tmp):
    """bs1-unlock-key --zfs (ZFSBootMenu's load-key.d hook): the drive's key
    loaded with zfs load-key -L file://, else ZFSBootMenu asks; never fails."""

    def setUp(self):
        super().setUp()
        t, self.root = self.t, self.t / "root"
        for d in ("etc/backshot1", "run", "dev/disk/by-label", "drive/backshot1/keys"):
            (self.root / d).mkdir(parents=True)
        (self.root / "drive/backshot1/keys/disk-passphrase").write_text("s3cret")
        (t / "want").write_text("s3cret")
        (t / "zfs").mkdir()
        exe(self.bin / "zfs", FAKE_ZFS % {"t": str(t)})
        exe(self.bin / "mount", f'#!/bin/sh\necho "$@" >>"{t}/mounts"\nfor d; do :; done\ncp -R "{self.root}/drive/." "$d/"\n')
        exe(self.bin / "umount", '#!/bin/sh\nrm -rf "$1"/*\n')
        exe(self.bin / "modprobe", "#!/bin/sh\nexit 0\n")
        exe(self.bin / "blkid", "#!/bin/sh\nexit 2\n")
        exe(self.bin / "sleep", f'#!/bin/sh\necho slept >>"{t}/sleeps"\n')
        (self.root / "etc/backshot1/luks-unlock").write_text(
            "family=Z\ngenerator=zfsbootmenu\nlabel=mybig512\npath=/backshot1/keys/disk-passphrase\nfstype=xfs\nwait=2\nvolumes='zroot:123:Z'\n")

    def plug(self):
        (self.root / "dev/disk/by-label/mybig512").write_text("")

    def run_key(self, *args, cmd=None, **env):
        cmd = cmd or ["sh", str(BS1 / "bs1-unlock-key")]
        return subprocess.run([*cmd, *args], capture_output=True, text=True, env=self.env(BS1_UNLOCK_ROOT=str(self.root), **env))

    def marks(self):
        p = self.root / "run/backshot1-unlock"
        return p.read_text().splitlines() if p.exists() else []

    def calls(self):
        p = self.t / "zfs-calls"
        return p.read_text().splitlines() if p.exists() else []

    def test_loads_the_drive_key(self):
        self.plug()
        r = self.run_key("--zfs", "zroot")
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertEqual(self.marks(), ["zroot drive"])
        loads = [c for c in self.calls() if c.startswith("load-key")]
        self.assertEqual(len(loads), 1)
        self.assertRegex(loads[0], rf"^load-key -L file://{re.escape(str(self.root))}/run/backshot1-key\.[0-9]+ zroot$")
        self.assertIn("-t xfs -o ro,norecovery", (self.t / "mounts").read_text())
        self.assertEqual(list((self.root / "run").glob("backshot1-key.*")), [], "the key's copy is left in /run")
        self.assertFalse((self.root / "run/backshot1-keydev").exists())

    def test_a_key_that_doesnt_open_is_typed(self):
        self.plug()
        (self.t / "want").write_text("other")
        r = self.run_key("--zfs", "zroot")
        self.assertEqual(r.returncode, 0)
        self.assertIn("doesn't open zroot; asking instead", r.stderr)
        self.assertEqual(self.marks(), ["zroot typed"])

    def test_without_the_drive_waits_once(self):
        r = self.run_key("--zfs", "zroot")
        self.assertEqual(r.returncode, 0)
        self.assertIn("waiting up to 2 s", r.stderr)
        self.assertEqual(len((self.t / "sleeps").read_text().splitlines()), 2)
        r = self.run_key("--zfs", "zroot/home")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("waiting", r.stderr)
        self.assertEqual(self.marks(), ["zroot typed", "zroot/home typed"])
        self.assertFalse((self.t / "mounts").exists())

    def test_a_loaded_key_is_left_alone(self):
        self.plug()
        (self.t / "zfs/zroot").mkdir()
        (self.t / "zfs/zroot/keystatus").write_text("available\n")
        r = self.run_key("--zfs", "zroot")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.marks(), [])
        self.assertFalse((self.t / "mounts").exists())

    def test_no_root_named_exits_0(self):
        r = self.run_key("--zfs")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.marks(), [])

    @unittest.skipUnless(BASH, "needs bash 4 or newer")
    def test_the_hook_form_carries_the_record(self):
        """lk_zbm_keyhook: ZFSBootMenu's initramfs has no /etc/backshot1."""
        r = subprocess.run([BASH, "-c", f'. "{BS1}/bs1-luks"; here="{BS1}" BACKUP_LABEL=mybig512 LK_PATH=/backshot1/keys/disk-passphrase LK_FSTYPE=xfs LUKS_WAIT=2; lk_zbm_keyhook'],
                           capture_output=True, text=True, env=self.env())
        self.assertEqual(r.returncode, 0, r.stderr)
        hook = self.t / "10-bs1-zfskey"
        hook.write_text(r.stdout)
        self.assertTrue(r.stdout.startswith("#!/bin/bash\n# BackShot1 (bs1-adopt): ZFSBootMenu's load-key.d hook"))
        (self.root / "etc/backshot1/luks-unlock").unlink()
        self.plug()
        r = self.run_key(cmd=[BASH, str(hook)], ZBM_ENCRYPTION_ROOT="zroot", ZBM_LOCKED_FS="zroot/ROOT/void")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.marks(), ["zroot drive"])
        self.assertIn("-t xfs -o ro,norecovery", (self.t / "mounts").read_text())
        # A wrong key, then no drive: still 0, ZFSBootMenu asks.
        (self.t / "want").write_text("other")
        self.assertEqual(self.run_key(cmd=[BASH, str(hook)], ZBM_ENCRYPTION_ROOT="zroot").returncode, 0)
        self.assertEqual(self.marks()[-1], "zroot typed")


@unittest.skipUnless(BASH, "needs bash 4 or newer")
class EvidenceZ(Tmp):
    """unlock_how Z and unlock_mode on ZFSBootMenu: bs1.zbm= on the booted
    kernel's command line, the ZFS roots that ask as the volumes."""

    def setUp(self):
        super().setUp()
        self.rec = self.t / "luks-unlock"
        self.cmdline = self.t / "cmdline"
        self.cmdline.write_text("root=zfs:zroot/ROOT/void spl.spl_hostid=0x00bab10c loglevel=4\n")
        self.roots = "zroot"

    def call(self, expr):
        lib = functions(BS1 / "bs1-common", "luks_rec", "unlock_how", "unlock_mode")
        script = (f'LUKS_REC="{self.rec}" UNLOCK_MARK="{self.t}/mark" UNLOCK_CMDLINE="{self.cmdline}"\n{lib}\n'
                  f'luks_names() {{ :; }}\nzfs_passphrase_roots() {{ [ -n "{self.roots}" ] && printf "%s\\n" {self.roots}; }}\n'
                  f'layout_zfs_passphrase() {{ echo zroot; }}\n{expr}')
        r = subprocess.run([BASH, "-c", script], capture_output=True, text=True, env=self.env())
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def record(self, family, volumes="zroot:123:Z", own="", reason="", generator="zfsbootmenu"):
        self.rec.write_text(f"family={family}\ngenerator={generator}\nvolumes='{volumes}'\nown='{own}'\nreason='{reason}'\nesp=ABCD-1234\n")

    def test_how_is_the_command_line(self):
        self.assertEqual(self.call("unlock_how zroot Z"), "typed\n")
        self.cmdline.write_text("root=zfs:zroot/ROOT/void bs1.zbm=zroot:drive quiet\n")
        self.assertEqual(self.call("unlock_how zroot Z"), "drive\n")
        self.assertEqual(self.call("unlock_how zroot/home Z"), "typed\n")
        self.cmdline.write_text("bs1.zbm=zroot:drive bs1.zbm=zroot/home:typed,zroot:typed\n")
        self.assertEqual(self.call("unlock_how zroot Z"), "typed\n")
        self.cmdline.write_text("bs1.zbm=zroot/home:typed,zroot:drive\n")
        self.assertEqual(self.call("unlock_how zroot Z"), "drive\n")

    def test_mode_drive(self):
        self.record("Z")
        self.cmdline.write_text("root=zfs:zroot/ROOT/void bs1.zbm=zroot:drive\n")
        for expr in ("unlock_mode vda", "unlock_mode vda layout.json"):
            out = self.call(expr)
            self.assertTrue(out.startswith("drive: ") and "(family Z)" in out, out)
            self.assertIn("\n  zroot drive\n", out)

    def test_mode_typed_this_boot(self):
        self.record("Z")
        self.cmdline.write_text("bs1.zbm=zroot:typed\n")
        self.assertTrue(self.call("unlock_mode vda").startswith("attended: zroot was typed at this boot"))

    def test_mode_family_a_says_why(self):
        self.record("A", volumes="", reason="zroot holds a boot environment and has keylocation=prompt")
        out = self.call("unlock_mode vda")
        self.assertTrue(out.startswith("attended: zroot holds a boot environment"), out)
        self.assertIn("  zroot typed", out)

    def test_without_a_zfsbootmenu_record_zfs_asks(self):
        self.assertTrue(self.call("unlock_mode vda").startswith("attended: ZFS native encryption asks for zroot"))
        self.record("D", generator="initramfs-tools")
        self.assertTrue(self.call("unlock_mode vda").startswith("attended: ZFS native encryption asks for zroot"))

    def test_nothing_encrypted(self):
        self.roots = ""
        self.assertTrue(self.call("unlock_mode vda").startswith("none: "))


@unittest.skipUnless(BASH, "needs bash 4 or newer")
class ZbmPieces(Tmp):
    """Family Z's small parts: the image's command line, the key file
    against the drive's key, .cmdline's room, a signed image."""

    def sh(self, expr, rc=0):
        r = subprocess.run([BASH, "-c", f'. "{BS1}/bs1-luks"\n{expr}'], capture_output=True, text=True, env=self.env())
        self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
        return r.stdout

    def test_cmdline_adds_ours_once(self):
        hr, wf = "zbm.hookroot=UUID=ABCD-1234//EFI/ZBM/backshot1", "zbm.wait_for=UUID=ABCD-1234"
        new = self.sh(f'lk_zbm_cmdline " quiet loglevel=0 nomodeset console=ttyS0,115200n8 zbm.timeout=5" {hr} {wf}')
        self.assertEqual(new, f"quiet loglevel=0 nomodeset console=ttyS0,115200n8 zbm.timeout=5 {hr} {wf}")
        self.assertEqual(self.sh(f'lk_zbm_cmdline "{new}" {hr} {wf}'), new)
        self.assertEqual(self.sh(f'lk_zbm_cmdline "{wf} quiet {hr}" {hr} {wf}'), f"quiet {hr} {wf}")

    def test_cmdline_refuses_a_hook_root_of_its_own(self):
        out = self.sh('lk_zbm_cmdline "quiet zbm.hookroot=LABEL=X//hooks" zbm.hookroot=UUID=A//B zbm.wait_for=UUID=A', rc=1)
        self.assertIn("zbm.hookroot=LABEL=X//hooks of its own", out)
        self.sh('lk_zbm_cmdline "quiet zbm.wait_for=/dev/sda" zbm.hookroot=UUID=A//B zbm.wait_for=UUID=A', rc=1)

    def test_key_file_one_newline_either_way(self):
        key, f = self.t / "key", self.t / "file"
        key.write_bytes(b"s3cret")
        for content, same in ((b"s3cret", True), (b"s3cret\n", True), (b"s3cret\n\n", False), (b"s3cret\r\n", False), (b"s3cre", False), (b"s3cretx", False)):
            f.write_bytes(content)
            self.sh(f'lk_key_same "{key}" "{f}"', rc=0 if same else 1)
        key.write_bytes(b"s3cret\n")
        f.write_bytes(b"s3cret")
        self.sh(f'lk_key_same "{key}" "{f}"')

    def test_gap_is_up_to_the_next_section(self):
        exe(self.bin / "objdump", textwrap.dedent("""\
            #!/bin/sh
            cat <<'EOF'

            zfsbootmenu.EFI:     file format pei-x86-64

            Sections:
            Idx Name          Size      VMA               LMA               File off  Algn  Flags
              0 .text         0000b9c8  0000000140001000  0000000140001000  00000400  2**4  CONTENTS, ALLOC, LOAD, READONLY, CODE
              1 .osrel        00000119  0000000140016000  0000000140016000  0000e400  2**2  CONTENTS, ALLOC, LOAD, READONLY, DATA
              2 .cmdline      00000040  0000000140017000  0000000140017000  0000e600  2**2  CONTENTS, ALLOC, LOAD, READONLY, DATA
              3 .linux        00d6a000  0000000140018000  0000000140018000  0000e800  2**2  CONTENTS, ALLOC, LOAD, READONLY, DATA
              4 .initrd       0307e042  0000000140d82000  0000000140d82000  00d78800  2**2  CONTENTS, ALLOC, LOAD, READONLY, DATA
            EOF
            """))
        self.assertEqual(self.sh(f'lk_zbm_gap "{self.t}/x"'), "0x140017000 4096\n")
        exe(self.bin / "objdump", "#!/bin/sh\nprintf 'Idx Name Size VMA\\n  0 .linux 00000010 0000000140018000\\n'\n")
        self.sh(f'lk_zbm_gap "{self.t}/x"', rc=1)

    def test_signed(self):
        img = self.t / "img.efi"
        b = bytearray(pe([(".cmdline", b"quiet\0"), (".linux", b"MZk")]))
        img.write_bytes(bytes(b))
        self.sh(f'lk_pe_signed "{img}"', rc=1)
        # PE32+: the certificate table's size, data directory 4.
        opt = 0x40 + 24
        struct.pack_into("<H", b, opt, 0x20B)
        img.write_bytes(bytes(b))
        self.sh(f'lk_pe_signed "{img}"', rc=1)
        struct.pack_into("<I", b, opt + 112 + 4 * 8 + 4, 0x2000)
        img.write_bytes(bytes(b))
        self.sh(f'lk_pe_signed "{img}"')


# The fake objcopy for AdoptZ: a .cmdline rewrite as zbm-kcl's two calls do
# it (--remove-section .cmdline, then --add-section .cmdline=F), on the test's
# minimal PE images (pe()).
MKPE = r'''#!/usr/bin/env python3
import struct, sys
%(pe)s
def sections(b):
    pe_at = struct.unpack_from("<I", b, 0x3C)[0]
    n = struct.unpack_from("<H", b, pe_at + 6)[0]
    opt = struct.unpack_from("<H", b, pe_at + 20)[0]
    out = []
    for i in range(n):
        o = pe_at + 24 + opt + 40 * i
        name, vsize, _, _, ptr = struct.unpack_from("<8sIIII", b, o)
        out.append((name.rstrip(b"\0").decode(), b[ptr:ptr + vsize]))
    return out
a = sys.argv[1:]
img = a[-1]
secs = sections(open(img, "rb").read())
if a[0] == "--remove-section":
    secs = [s for s in secs if s[0] != a[1]]
elif a[0] == "--add-section":
    name, f = a[1].split("=", 1)
    data = open(f, "rb").read()
    secs.insert(0, (name, data))
else:
    sys.exit(2)
open(img, "wb").write(pe(secs))
'''


@unittest.skipUnless(BASH, "needs bash 4 or newer")
class AdoptZ(Tmp):
    """luks_adopt and luks_off on a fake void-zbm-enc: Void on a ZFS root
    booted by ZFSBootMenu's release image at /boot/efi/EFI/ZBM/VMLINUZ.EFI,
    zroot encrypted with a passphrase, keylocation=file:///etc/zfs/zroot.key."""

    CMDLINE = "quiet loglevel=0 nomodeset console=ttyS0,115200n8 zbm.timeout=5"

    def setUp(self):
        super().setUp()
        t = self.t
        self.drive = t / "drive"
        here = self.drive / "backshot1"
        (here / "keys").mkdir(parents=True)
        shutil.copy(BS1 / "bs1-unlock-key", here / "bs1-unlock-key")
        (here / "keys/disk-passphrase").write_text("s3cret")
        (t / "want").write_text("s3cret")
        self.esp = t / "boot/efi"
        (self.esp / "EFI/ZBM").mkdir(parents=True)
        self.img = self.esp / "EFI/ZBM/VMLINUZ.EFI"
        self.img.write_bytes(pe([(".osrel", b"ID=void\n"), (".cmdline", f" {self.CMDLINE}\0".encode()), (".linux", b"MZkernel"), (".initrd", b"zstd")]))
        self.backup_img = self.esp / "EFI/ZBM/VMLINUZ-BACKUP.EFI"
        shutil.copy(self.img, self.backup_img)
        self.orig_bytes = self.img.read_bytes()
        self.keyfile = t / "etc/zfs/zroot.key"
        self.keyfile.parent.mkdir(parents=True)
        self.keyfile.write_text("s3cret")
        self.keyfile.chmod(0o400)
        (t / "zfs").mkdir()
        self.prop("zroot", "keylocation", f"file://{self.keyfile}")
        self.prop("zroot", "guid", "1234567890")
        self.prop("zroot/ROOT/void", "encryptionroot", "zroot")
        (t / "zfs-list-name,mountpoint,encryptionroot").write_text(
            "zroot\tnone\tzroot\nzroot/ROOT\tnone\tzroot\nzroot/ROOT/void\t/\tzroot\nzroot/ROOT/void-6.18\t/\tzroot\nzroot/home\t/home\tzroot\n")
        (t / "zfs-list-name,encryptionroot,keyformat").write_text(
            "zroot\tzroot\tpassphrase\nzroot/ROOT\tzroot\tpassphrase\nzroot/ROOT/void\tzroot\tpassphrase\nzroot/home\tzroot\tpassphrase\n")
        exe(self.bin / "zfs", FAKE_ZFS % {"t": str(t)})
        exe(self.bin / "zpool", '#!/bin/sh\ncase "$*" in "list -Ho name") echo zroot ;; "list -vHPL zroot") printf "zroot\\t15G\\n\\t/dev/vda2\\t15G\\n" ;; *) exit 1 ;; esac\n')
        exe(self.bin / "findmnt", textwrap.dedent(f"""\
            #!/bin/sh
            case "$*" in
            "-no TARGET --target "*) echo "{self.drive}" ;;
            "-no SOURCE --target "*) echo /dev/sdb1 ;;
            "-no FSTYPE --target "*) echo xfs ;;
            "-nro FSTYPE /") cat "{t}/root-fstype" 2>/dev/null || echo zfs ;;
            "-nro SOURCE /") echo zroot/ROOT/void ;;
            "-rn -t vfat -o TARGET,SOURCE") [ -e "{t}/no-esp" ] || echo "{self.esp} /dev/vda1" ;;
            "-rn -M {self.esp} -o FSTYPE,SOURCE") [ -e "{t}/no-esp" ] || echo "vfat /dev/vda1" ;;
            *) exit 1 ;;
            esac
            """))
        exe(self.bin / "blkid", f'#!/bin/sh\ncase "$*" in *"-s UUID /dev/vda1") cat "{t}/esp-uuid" 2>/dev/null || echo ABCD-1234 ;; *"-s LABEL /dev/sdb1") echo mybig512 ;; *) exit 2 ;; esac\n')
        exe(self.bin / "objdump", textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import struct, sys
            b = open(sys.argv[-1], "rb").read()
            pe_at = struct.unpack_from("<I", b, 0x3C)[0]
            n = struct.unpack_from("<H", b, pe_at + 6)[0]
            opt = struct.unpack_from("<H", b, pe_at + 20)[0]
            print("Idx Name Size VMA LMA File off Algn")
            vma = {{".osrel": 0x1000, ".cmdline": 0x2000, ".linux": 0x3000, ".initrd": 0x4000}}
            for i in range(n):
                name, vsize = struct.unpack_from("<8sI", b, pe_at + 24 + opt + 40 * i)
                name = name.rstrip(b"\\0").decode()
                print(f"  {{i}} {{name}} {{vsize:08x}} {{vma[name]:016x}} {{vma[name]:016x}} 0 2**2")
            """))
        exe(self.bin / "objcopy", MKPE % {"pe": PE_SRC})
        exe(self.bin / "sync", "#!/bin/sh\nexit 0\n")

    def prop(self, ds, name, value):
        d = self.t / "zfs" / ds
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(value + "\n")

    def adopt(self, what="luks_adopt", rc=0):
        t = self.t
        lib = functions(BS1 / "bs1-common", "zfs_passphrase_roots", "zfs_key_test")
        script = textwrap.dedent(f"""\
            . "{BS1}/bs1-luks"
            LUKS_DIR="{t}/etc/backshot1" LUKS_ORIG="{t}/etc/backshot1/orig" LUKS_MADE="{t}/etc/backshot1/made" LUKS_REC="{t}/etc/backshot1/luks-unlock"
            here="{self.drive}/backshot1" init=runit BACKUP_LABEL=mybig512 INTERNAL_SERIAL=S1
            internal_disk() {{ echo vda; }}
            on_disk() {{ true; }}
            lk_luks_volumes() {{ :; }}
            lk_generator() {{ echo dracut; }}
            lk_remake() {{ echo "remade $1" >>"{t}/remakes"; }}
            lsblk() {{ echo vda; }}
            """) + lib + f"\n{what}\n"
        r = subprocess.run([BASH, "-c", script], capture_output=True, text=True, env=self.env())
        self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
        return r.stdout + r.stderr

    def cmdline(self, img=None):
        r = subprocess.run([BASH, "-c", f'. "{BS1}/bs1-luks"; lk_pe_section "{img or self.img}" .cmdline'], capture_output=True, text=True)
        return r.stdout

    def rec(self):
        return (self.t / "etc/backshot1/luks-unlock").read_text()

    def hooks(self):
        return self.esp / "EFI/ZBM/backshot1"

    def test_adopt_rerun_and_off(self):
        out = self.adopt()
        self.assertIn("family Z (ZFSBootMenu): zroot:1234567890:Z", out)
        rec = self.rec()
        for line in ("family=Z", "generator=zfsbootmenu", "label=mybig512", "path=/backshot1/keys/disk-passphrase", "fstype=xfs",
                     "volumes='zroot:1234567890:Z'", "esp=ABCD-1234", f"image='{self.img}'", f"hooks='{self.hooks()}'"):
            self.assertIn(line + "\n", rec)
        self.assertEqual(self.cmdline(), f"{self.CMDLINE} zbm.hookroot=UUID=ABCD-1234//EFI/ZBM/backshot1 zbm.wait_for=UUID=ABCD-1234")
        self.assertEqual(self.backup_img.read_bytes(), self.orig_bytes, "VMLINUZ-BACKUP.EFI must stay as it was")
        key = (self.hooks() / "load-key.d/10-bs1-zfskey").read_text()
        self.assertTrue(key.startswith("#!/bin/bash\n"))
        self.assertIn("BS1_UNLOCK_SET=1 label='mybig512' path='/backshot1/keys/disk-passphrase' fstype='xfs' wait='30'", key)
        self.assertIn((BS1 / "bs1-unlock-key").read_text().split("\n", 1)[1].strip(), key)
        mark = (self.hooks() / "boot-sel.d/10-bs1-mark").read_text()
        self.assertIn("bs1.zbm=", mark)
        self.assertFalse((t := self.t / "remakes").exists(), "family Z remakes no initramfs")
        self.assertTrue((self.t / "etc/backshot1/orig/" / str(self.img).replace("/", "%")).exists())

        out = self.adopt()
        self.assertIn("unchanged; the initramfs isn't remade", out)
        self.assertNotIn("wrote", out)

        out = self.adopt("luks_off")
        self.assertEqual(self.img.read_bytes(), self.orig_bytes)
        self.assertFalse(self.hooks().exists(), out)
        self.assertFalse((self.t / "etc/backshot1").exists())
        self.assertFalse(t.exists(), "--luks-off remakes no initramfs on ZFSBootMenu")
        self.assertTrue((self.esp / "EFI/ZBM").is_dir())

    def test_a_key_file_with_a_newline_is_the_same_key(self):
        self.keyfile.chmod(0o600)
        self.keyfile.write_text("s3cret\n")
        self.assertIn("family Z (ZFSBootMenu)", self.adopt())

    def test_a_key_file_that_isnt_the_drive_key_stops(self):
        self.keyfile.chmod(0o600)
        self.keyfile.write_text("other")
        out = self.adopt(rc=1)
        self.assertIn("zroot's key file", out)
        self.assertFalse((self.t / "etc/backshot1").exists())
        self.assertEqual(self.img.read_bytes(), self.orig_bytes)

    def test_a_drive_key_that_doesnt_open_stops(self):
        (self.t / "want").write_text("other")
        out = self.adopt(rc=1)
        self.assertIn("the drive's key doesn't open the ZFS encryption root zroot", out)
        self.assertFalse((self.t / "etc/backshot1").exists())

    def test_keylocation_prompt_is_attended(self):
        self.prop("zroot", "keylocation", "prompt")
        out = self.adopt()
        self.assertIn("A: zroot holds a boot environment and has keylocation=prompt", out)
        self.assertIn("family=A\n", self.rec())
        self.assertIn("generator=zfsbootmenu\n", self.rec())
        self.assertEqual(self.img.read_bytes(), self.orig_bytes)
        self.assertFalse(self.hooks().exists())
        self.adopt("luks_off")
        self.assertFalse((self.t / "etc/backshot1").exists())

    def test_a_hook_root_of_its_own_is_attended(self):
        self.img.write_bytes(pe([(".cmdline", b" quiet zbm.hookroot=LABEL=X//hooks\0"), (".linux", b"MZk")]))
        out = self.adopt()
        self.assertIn("zbm.hookroot=LABEL=X//hooks of its own, which bs1-adopt doesn't merge", out)
        self.assertFalse(self.hooks().exists())

    def test_without_zfsbootmenu_nothing_changes(self):
        (self.t / "no-esp").write_text("")
        out = self.adopt()
        self.assertIn("not booted by ZFSBootMenu's EFI/ZBM/VMLINUZ.EFI: attended; nothing changes", out)
        self.assertFalse((self.t / "etc/backshot1").exists())

    def test_luks_off_with_the_esp_unmounted_changes_nothing(self):
        self.adopt()
        patched = self.img.read_bytes()
        (self.t / "no-esp").write_text("")
        out = self.adopt("luks_off", rc=1)
        self.assertIn("isn't mounted at", out)
        self.assertIn("nothing changed", out)
        self.assertTrue((self.t / "etc/backshot1/orig/" / str(self.img).replace("/", "%")).exists(), "the original must be kept")
        self.assertTrue((self.t / "etc/backshot1/luks-unlock").exists())
        self.assertEqual(self.img.read_bytes(), patched)
        (self.t / "no-esp").unlink()
        self.adopt("luks_off")
        self.assertEqual(self.img.read_bytes(), self.orig_bytes)
        self.assertFalse(self.hooks().exists())

    def test_a_failed_put_back_keeps_the_record(self):
        self.adopt()
        orig = self.t / "etc/backshot1/orig" / str(self.img).replace("/", "%")
        self.img.unlink()
        self.img.mkdir()  # cat can't write over a directory
        out = self.adopt("luks_off", rc=1)
        self.assertIn("couldn't put back", out)
        self.assertTrue(orig.exists())
        self.assertTrue((self.t / "etc/backshot1/luks-unlock").exists())
        self.assertTrue((self.hooks() / "load-key.d/10-bs1-zfskey").exists())

    def test_an_updated_image_is_the_new_original(self):
        self.adopt()
        newer = pe([(".osrel", b"ID=void\n"), (".cmdline", f" {self.CMDLINE} zbm.newer=1\0".encode()), (".linux", b"MZkernel2"), (".initrd", b"zstd")])
        self.img.write_bytes(newer)
        out = self.adopt()
        self.assertIn("replaced since the last adopt", out)
        self.assertEqual(self.cmdline(), f"{self.CMDLINE} zbm.newer=1 zbm.hookroot=UUID=ABCD-1234//EFI/ZBM/backshot1 zbm.wait_for=UUID=ABCD-1234")
        self.adopt("luks_off")
        self.assertEqual(self.img.read_bytes(), newer, "--luks-off puts back the update, not the older release")

    def test_a_changed_volume_id_stays_z(self):
        self.adopt()
        (self.t / "esp-uuid").write_text("EF01-2345\n")
        out = self.adopt()
        self.assertIn("family Z (ZFSBootMenu)", out)
        self.assertNotIn("replaced since", out)
        self.assertEqual(self.cmdline(), f"{self.CMDLINE} zbm.hookroot=UUID=EF01-2345//EFI/ZBM/backshot1 zbm.wait_for=UUID=EF01-2345")
        self.assertIn("esp=EF01-2345\n", self.rec())
        self.adopt("luks_off")
        self.assertEqual(self.img.read_bytes(), self.orig_bytes)

    def test_z_then_a_puts_back_the_hooks_and_image(self):
        self.adopt()
        self.prop("zroot", "keylocation", "prompt")
        out = self.adopt()
        self.assertIn("family Z from before", out)
        self.assertIn("family=A\n", self.rec())
        self.assertEqual(self.img.read_bytes(), self.orig_bytes)
        self.assertFalse(self.hooks().exists())
        self.adopt("luks_off")
        self.assertFalse((self.t / "etc/backshot1").exists())

    def test_a_failed_rewrite_puts_everything_back(self):
        exe(self.bin / "objcopy", "#!/bin/sh\nexit 1\n")
        out = self.adopt(rc=1)
        self.assertIn("objcopy couldn't rewrite", out)
        self.assertEqual(self.img.read_bytes(), self.orig_bytes)
        self.assertFalse(self.hooks().exists())
        self.assertFalse((self.t / "etc/backshot1").exists())


@unittest.skipUnless(BASH, "needs bash 4 or newer")
class ZbmMarkHook(Tmp):
    """The boot-sel.d hook on a fake ZFSBootMenu root: bs1.zbm= into the
    chosen BE's command line cache, which load_be_cmdline makes first."""

    def setUp(self):
        super().setUp()
        r = self.root = self.t / "zbm"
        for d in ("lib", "etc", "run", "zfsbootmenu"):
            (r / d).mkdir(parents=True)
        for f in ("profiling-lib.sh", "kmsg-log-lib.sh"):
            (r / "lib" / f).write_text(":\n")
        (r / "etc/zfsbootmenu.conf").write_text(f"export BASE='{r}/zfsbootmenu'\n")
        # load_be_cmdline: writes the BE's cache, as ZFSBootMenu's does.
        (r / "lib/zfsbootmenu-core.sh").write_text(textwrap.dedent("""\
            load_be_cmdline() {
                mkdir -p "$BASE/environments/$1"
                printf 'loglevel=4\\nconsole=ttyS0,115200n8\\nspl.spl_hostid=0x00bab10c\\n' >"$BASE/environments/$1/cmdline"
            }
            """))
        r2 = subprocess.run([BASH, "-c", f'. "{BS1}/bs1-luks"; lk_zbm_markhook'], capture_output=True, text=True)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.hook = self.t / "10-bs1-mark"
        self.hook.write_text(r2.stdout)

    def run_hook(self, be="zroot/ROOT/void"):
        r = subprocess.run([BASH, str(self.hook)], capture_output=True, text=True, env=self.env(BS1_ZBM_ROOT=str(self.root), ZBM_SELECTED_BE=be))
        self.assertEqual(r.returncode, 0, r.stderr)

    def cache(self, be="zroot/ROOT/void"):
        return (self.root / "zfsbootmenu/environments" / be / "cmdline").read_text().splitlines()

    def test_the_mark_goes_into_the_cache(self):
        (self.root / "run/backshot1-unlock").write_text("zroot drive\n")
        self.run_hook()
        self.assertEqual(self.cache(), ["loglevel=4", "console=ttyS0,115200n8", "spl.spl_hostid=0x00bab10c", "bs1.zbm=zroot:drive"])
        # Again (back to the menu, another try): one word, the last marks.
        (self.root / "run/backshot1-unlock").write_text("zroot drive\nzroot/home typed\nzroot typed\n")
        self.run_hook()
        self.assertEqual(self.cache()[-1], "bs1.zbm=zroot:typed,zroot/home:typed")
        self.assertEqual(sum(1 for w in self.cache() if w.startswith("bs1.zbm=")), 1)

    def test_an_edited_command_line_gets_it(self):
        (self.root / "run/backshot1-unlock").write_text("zroot drive\n")
        (self.root / "zfsbootmenu/cmdline").write_text("quiet\n")
        self.run_hook()
        self.assertEqual((self.root / "zfsbootmenu/cmdline").read_text().splitlines(), ["quiet", "bs1.zbm=zroot:drive"])
        self.assertFalse((self.root / "zfsbootmenu/environments").exists())

    def test_base_defaults_to_zfsbootmenus(self):
        (self.root / "etc/zfsbootmenu.conf").write_text(":\n")
        (self.root / "run/backshot1-unlock").write_text("zroot drive\n")
        self.run_hook()
        self.assertEqual(self.cache()[-1], "bs1.zbm=zroot:drive")

    def test_no_marks_changes_nothing(self):
        self.run_hook()
        self.assertFalse((self.root / "zfsbootmenu/environments").exists())
        (self.root / "run/backshot1-unlock").write_text("zroot drive\n")
        (self.root / "lib/zfsbootmenu-core.sh").unlink()
        self.run_hook()
        self.assertFalse((self.root / "zfsbootmenu/environments").exists())


# pe()'s own source, for the fake objcopy.
import inspect  # noqa: E402
PE_SRC = "import struct\n" + inspect.getsource(pe)


if __name__ == "__main__":
    unittest.main()
