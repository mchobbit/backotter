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
                           ("debian13-stack", "md1_crypt"), ("gentoo-systemd", "cryptroot"), ("omarchy", "root")]:
            with self.subTest(name):
                self.assertEqual(self.keys(name), [f"{want}=FILE"])

    def test_zfs_encryption_corpus_unchanged(self):
        # rpool's key is raw (from the LUKS keystore): only the keystore asks.
        self.assertEqual(self.keys("ubuntu26-zfsmirror"), ["keystore-rpool=FILE"])

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
