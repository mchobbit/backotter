"""BackShot1's family P (bs1-luks; docs/design/bs1-luks-unattended.md,
"Built, family P"): booster on PikaOS, whose LUKS root gets the backup
drive's key through a FIFO that BackShot1's preinit (the kernel's rdinit=)
makes before booster's /init starts, and bs1-unlock-key --booster fills.
booster.yaml's and refind_linux.conf's edits, adopt and --luks-off on a fake
PikaOS, the helper's FIFOs and marks, the evidence, and install.sh's key-file
LUKS lines.  No VM, no root, no booster: fakes."""

import os
import re
import shutil
import stat
import subprocess
import textwrap
import threading
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_bs1_luks as base  # noqa: E402

BASH = base.BASH
BS1 = base.BS1
exe = base.exe
UUID = "7c1d2e3f-4a5b-4c6d-8e7f-a0b1c2d3e4f5"
INIT = "/usr/local/lib/backshot1/booster-preinit"

# What pikainstall 2.0.5 writes on LUKS (pika-install-host.sh's booster.yaml;
# _REFIND_CRYPT_SETUP_FILE's three lines, rootflags as findmnt gave them).
PIKA_YAML = textwrap.dedent("""\
    vconsole: true
    extra_files: busybox
    enable_lvm: true
    modules: usbhid,hid_generic
    modules_force_load: usbhid,hid_generic
    compression: lz4
    strip: true
    enable_plymouth: true
    """)
OPTS = "nowatchdog nvidia-drm.modeset=1 rootflags=subvol=@,compress=zstd root=/dev/mapper/crypt_root"
PIKA_REFIND = (
    f'"Boot with standard options"  "rd.luks.name={UUID}=crypt_root rd.luks.options=discard {OPTS} quiet splash ---"\n'
    f'"Boot with logging"  "rd.luks.name={UUID}=crypt_root rd.luks.options=discard {OPTS} ---"\n'
    f'"Boot with safe graphics"  "rd.luks.name={UUID}=crypt_root rd.luks.options=discard amd_pstate=active {OPTS} nomodeset ---"\n')
OURS = f"rdinit={INIT} rd.luks.key={UUID}=/backshot1-key.{UUID}"


class Lib(base.Tmp):
    def lib(self, expr, *args, **env):
        r = subprocess.run([BASH, "-c", f'. "{BS1}/bs1-luks"\n{expr}', "x", *args], capture_output=True, text=True,
                           env=self.env(**env))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout


@unittest.skipUnless(BASH, "needs bash 4 or newer")
class BoosterYaml(Lib):
    """lk_booster_yaml: extra_files and modules_force_load gain ours."""

    FILES = f"busybox /usr/bin/busybox {INIT} /usr/local/lib/backshot1/unlock-key /etc/backshot1/luks-unlock"

    def yaml(self, text, files=FILES, mods="usb_storage uas sd_mod xfs"):
        f = self.t / "booster.yaml"
        if text is None:
            f.unlink(missing_ok=True)
        else:
            f.write_text(text)
        return self.lib('lk_booster_yaml "$1" "$2" "$3"', str(f), files, mods)

    def test_pikas_yaml(self):
        out = self.yaml(PIKA_YAML)
        lines = out.splitlines()
        self.assertEqual(lines[1], f"extra_files: busybox,/usr/bin/busybox,{INIT},/usr/local/lib/backshot1/unlock-key,/etc/backshot1/luks-unlock")
        self.assertEqual(lines[4], "modules_force_load: usbhid,hid_generic,usb_storage,uas,sd_mod,xfs")
        # Every other line byte for byte.
        want = PIKA_YAML.splitlines()
        self.assertEqual([l for i, l in enumerate(lines) if i not in (1, 4)], [l for i, l in enumerate(want) if i not in (1, 4)])

    def test_twice_is_the_same(self):
        once = self.yaml(PIKA_YAML)
        self.assertEqual(self.yaml(once), once)

    def test_quotes_comments_and_spaces(self):
        out = self.yaml("extra_files: \"fsck, busybox\"  # tools\nmodules_force_load: 'xfs'\n")
        self.assertIn(f"extra_files: fsck,busybox,/usr/bin/busybox,{INIT},", out)
        self.assertIn("modules_force_load: xfs,usb_storage,uas,sd_mod\n", out)

    def test_keys_added_when_missing(self):
        out = self.yaml("universal: false\n")
        self.assertEqual(out.splitlines()[0], "universal: false")
        self.assertTrue(out.splitlines()[1].startswith("extra_files: busybox,"))
        self.assertEqual(out.splitlines()[2], "modules_force_load: usb_storage,uas,sd_mod,xfs")
        self.assertEqual(self.yaml(None).splitlines()[0][:12], "extra_files:")

    def test_no_modules_adds_no_key(self):
        self.assertNotIn("modules_force_load", self.yaml("vconsole: true\n", mods=""))


@unittest.skipUnless(BASH, "needs bash 4 or newer")
class RefindText(Lib):
    """lk_p_refind_text and lk_p_volume on refind_linux.conf."""

    def setUp(self):
        super().setUp()
        self.conf = self.t / "refind_linux.conf"

    def text(self, conf, *uuids):
        self.conf.write_text(conf)
        return self.lib('lk_p_refind_text "$@"', str(self.conf), *uuids)

    def volume(self, conf, name="crypt_root"):
        self.conf.write_text(conf)
        return self.lib(f'LK_REFIND_CONF="{self.conf}"; lk_p_volume "$1" "$2"', UUID, name).strip()

    def test_pikas_three_lines(self):
        out = self.text(PIKA_REFIND, UUID).splitlines()
        self.assertEqual(out[0], f'"Boot with standard options"  "rd.luks.name={UUID}=crypt_root rd.luks.options=discard {OPTS} quiet splash {OURS} booster.log=warning ---"')
        self.assertEqual(out[1], f'"Boot with logging"  "rd.luks.name={UUID}=crypt_root rd.luks.options=discard {OPTS} {OURS} ---"')
        self.assertEqual(out[2], f'"Boot with safe graphics"  "rd.luks.name={UUID}=crypt_root rd.luks.options=discard amd_pstate=active {OPTS} nomodeset {OURS} ---"')

    def test_twice_is_the_same(self):
        once = self.text(PIKA_REFIND, UUID)
        self.assertEqual(self.text(once, UUID), once)

    def test_others_untouched(self):
        conf = f'# rEFInd\n\n"Other"  "root=UUID=abc quiet"\n"Mine" "rd.luks.uuid={UUID} root=/dev/mapper/root"\n'
        out = self.text(conf, UUID)
        self.assertEqual(out.splitlines()[:3], ["# rEFInd", "", '"Other"  "root=UUID=abc quiet"'])
        self.assertEqual(out.splitlines()[3], f'"Mine" "rd.luks.uuid={UUID} root=/dev/mapper/root {OURS}"')

    def test_a_log_level_of_its_own_stays(self):
        out = self.text(f'"x" "rd.luks.name={UUID}=r quiet booster.log=info"\n', UUID)
        self.assertNotIn("booster.log=warning", out)

    def test_volume_ok_and_ours(self):
        self.assertEqual(self.volume(PIKA_REFIND), "ok")
        self.assertEqual(self.volume(self.text(PIKA_REFIND, UUID)), "ok")

    def test_volume_own_key(self):
        self.assertEqual(self.volume(f'"x" "rd.luks.name={UUID}=crypt_root rd.luks.key={UUID}=/etc/root.key"\n'),
                         f"own rd.luks.key={UUID}=/etc/root.key")
        self.assertTrue(self.volume(f'"x" "rd.luks.name={UUID}=crypt_root rd.luks.key=/etc/root.key"\n').startswith("own "))

    def test_volume_refusals(self):
        self.assertIn("first line", self.volume(f'"x" "root=UUID=abc"\n"y" "rd.luks.name={UUID}=crypt_root"\n'))
        self.assertIn("no line", self.volume("# nothing\n"))
        self.assertIn("opens", self.volume(f'"x" "rd.luks.name={UUID}=cryptroot"\n'))
        self.assertIn("rdinit=/sbin/other", self.volume(f'"x" "rd.luks.name={UUID}=crypt_root rdinit=/sbin/other"\n'))

    def test_preinit_is_sh(self):
        text = self.lib("lk_p_preinit")
        self.assertTrue(text.startswith("#!/usr/bin/busybox sh\n"))
        self.assertIn('$b sh /usr/local/lib/backshot1/unlock-key --booster-fifos\n', text)
        self.assertIn('$b sh /usr/local/lib/backshot1/unlock-key --booster </dev/null &\n', text)
        self.assertTrue(text.rstrip().endswith('exec /init "$@"'))
        f = self.t / "preinit"
        f.write_text(text)
        self.assertEqual(subprocess.run(["sh", "-n", str(f)]).returncode, 0)


# booster on a fake root: `build --force --kernel-version K OUT` writes OUT as
# the listing booster ls gives (init, extra_files' paths, busybox at
# usr/bin/busybox) and the modules_force_load it read, from ROOT's
# etc/booster.yaml; ROOT/no-extra makes it leave extra_files out.
BOOSTER = r'''#!/usr/bin/env python3
import os, sys
ROOT = %(root)r
a = sys.argv[1:]
y = {}
p = ROOT + "/etc/booster.yaml"
if os.path.exists(p):
    for l in open(p):
        if ":" in l and not l.startswith("#"):
            k, v = l.split(":", 1)
            y[k.strip()] = v.strip()
if a[0] == "build":
    assert a[1:4] == ["--force", "--kernel-version", a[3]] and len(a) == 5, a
    open(ROOT + "/builds", "a").write(a[3] + "\n")
    files = [] if os.path.exists(ROOT + "/no-extra") else [f for f in y.get("extra_files", "").split(",") if f]
    names = ["init", "etc/booster.init.yaml"] + [("usr/bin/" + f if not f.startswith("/") else f.lstrip("/")) for f in files]
    force = [m for m in y.get("modules_force_load", "").split(",") if m]
    with open(a[4], "w") as o:
        o.write("LS " + " ".join(names) + "\nFORCE " + " ".join(force) + "\n")
elif a[0] == "ls":
    for l in open(a[1]):
        if l.startswith("LS "):
            for n in l.split()[1:]:
                print(n)
elif a[0] == "cat":
    assert a[2] == "etc/booster.init.yaml", a
    print("kernel: 7.3.3-pikaos")
    print("modulesforceload:")
    for l in open(a[1]):
        if l.startswith("FORCE "):
            for m in l.split()[1:]:
                print("    - " + m)
    print("mounttimeout: 180")
else:
    sys.exit(2)
'''


@unittest.skipUnless(BASH, "needs bash 4 or newer")
class AdoptP(base.Tmp):
    """luks_adopt and luks_off on a fake PikaOS 4 on LUKS (pika-bootstrap.sh
    --luks): family P writes the preinit, bs1-unlock-key, booster.yaml's two
    keys, remakes and checks each kernel's image, then refind_linux.conf."""

    def setUp(self):
        super().setUp()
        t = self.t
        self.here = t / "drive/backshot1"
        (self.here / "keys").mkdir(parents=True)
        shutil.copy(BS1 / "bs1-unlock-key", self.here / "bs1-unlock-key")
        (self.here / "keys/disk-passphrase").write_text("s3cret")
        for d in ("boot", "etc", "usr/bin", "lib/modules/7.3.3-pikaos/kernel", "lib/modules/7.1.8-pikaos/kernel"):
            (t / d).mkdir(parents=True)
        self.refind = t / "boot/refind_linux.conf"
        self.refind.write_text(PIKA_REFIND)
        self.yaml = t / "etc/booster.yaml"
        self.yaml.write_text(PIKA_YAML)
        (t / "crypttab").write_text(f"crypt_root\tUUID={UUID}\tnone\tluks,discard\n")
        exe(t / "usr/bin/busybox", "#!/bin/sh\nexit 0\n")
        exe(self.bin / "booster", BOOSTER % {"root": str(t)})
        exe(self.bin / "findmnt", f'#!/bin/sh\ncase "$2" in TARGET) echo "{t}/drive" ;; SOURCE) echo /dev/sdb1 ;; FSTYPE) echo xfs ;; esac\n')
        exe(self.bin / "blkid", "#!/bin/sh\necho mybig512\n")
        exe(self.bin / "cryptsetup", "#!/bin/sh\nexit 0\n")
        exe(self.bin / "sleep", "#!/bin/sh\nexit 0\n")
        # xhci_pci built in, the rest modules; ext4 never asked for.
        exe(self.bin / "modinfo", textwrap.dedent("""\
            #!/bin/sh
            [ "$1 $2" = "-F filename" ] || exit 1
            case $3 in xhci_pci) echo "(builtin)" ;; usb_storage|uas|sd_mod|xfs) echo "/lib/modules/7.3.3-pikaos/kernel/$3.ko.zst" ;; *) exit 1 ;; esac
            """))
        (t / "proc-cmdline").write_text(f"initrd=\\booster.img-7.3.3-pikaos rd.luks.name={UUID}=crypt_root quiet splash ---\n")

    def adopt(self, what="luks_adopt", rc=0):
        t = self.t
        script = textwrap.dedent(f"""\
            . "{BS1}/bs1-luks"
            LUKS_DIR="{t}/etc/backshot1" LUKS_ORIG="{t}/etc/backshot1/orig" LUKS_MADE="{t}/etc/backshot1/made" LUKS_REC="{t}/etc/backshot1/luks-unlock"
            LK_CMDLINE="{t}/proc-cmdline" LK_CRYPTTAB="{t}/crypttab" LK_IMG_DIRS="{t}/boot"
            LK_REFIND_CONF="{self.refind}" LK_BOOSTER_YAML="{self.yaml}" LK_BOOSTER_INIT="{t}{INIT}"
            LK_BUSYBOX="{t}/usr/bin/busybox" LK_MODULES_DIR="{t}/lib/modules" LK_BOOT="{t}/boot"
            UNLOCK_KEY="{t}/usr/local/lib/backshot1/unlock-key"
            here="{self.here}" init=systemd BACKUP_LABEL=mybig512 INTERNAL_SERIAL=S1
            internal_disk() {{ echo nvme0n1; }}
            is_abroot() {{ false; }}
            lk_generator() {{ echo booster; }}
            lk_luks_volumes() {{ echo "/dev/nvme0n1p3 {UUID} crypt_root"; }}
            lk_early() {{ [[ $1 == crypt_root ]]; }}
            {what}
            """)
        r = subprocess.run([BASH, "-c", script], capture_output=True, text=True, env=self.env())
        self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
        return r.stdout + r.stderr

    def builds(self):
        p = self.t / "builds"
        return p.read_text().split() if p.exists() else []

    def rec(self):
        return (self.t / "etc/backshot1/luks-unlock").read_text()

    def test_adopt_rerun_and_off(self):
        t = self.t
        out = self.adopt()
        self.assertIn(f"family P (initramfs: booster): crypt_root:{UUID}:P", out)
        self.assertIn("family=P\ngenerator=booster\n", self.rec())
        self.assertIn(f"volumes='crypt_root:{UUID}:P'", self.rec())
        self.assertEqual(sorted(self.builds()), ["7.1.8-pikaos", "7.3.3-pikaos"])
        for k in ("7.1.8-pikaos", "7.3.3-pikaos"):
            self.assertIn(f"{t}/boot/booster.img-{k}: ok", out)
        y = self.yaml.read_text().splitlines()
        self.assertEqual(y[1], f"extra_files: busybox,{t}/usr/bin/busybox,{t}{INIT},{t}/usr/local/lib/backshot1/unlock-key,{t}/etc/backshot1/luks-unlock")
        self.assertEqual(y[4], "modules_force_load: usbhid,hid_generic,usb_storage,uas,sd_mod,xfs")
        lines = self.refind.read_text().splitlines()
        ours = f"rdinit={t}{INIT} rd.luks.key={UUID}=/backshot1-key.{UUID}"
        self.assertTrue(lines[0].endswith(f"quiet splash {ours} booster.log=warning ---\""), lines[0])
        self.assertTrue(lines[1].endswith(f"root=/dev/mapper/crypt_root {ours} ---\""), lines[1])
        pre = (t / INIT.lstrip("/")).read_text()
        self.assertTrue(pre.startswith(f"#!{t}/usr/bin/busybox sh\n# BackShot1"))
        self.assertEqual(stat.S_IMODE((t / INIT.lstrip("/")).stat().st_mode), 0o755)
        self.assertEqual((t / "usr/local/lib/backshot1/unlock-key").read_text(), (BS1 / "bs1-unlock-key").read_text())
        # crypttab: booster reads none, and the booted system finds it open.
        self.assertEqual((t / "crypttab").read_text(), f"crypt_root\tUUID={UUID}\tnone\tluks,discard\n")

        out = self.adopt()
        self.assertIn("unchanged; the initramfs isn't remade", out)
        self.assertEqual(len(self.builds()), 2)

        out = self.adopt("luks_off")
        self.assertEqual(self.refind.read_text(), PIKA_REFIND)
        self.assertEqual(self.yaml.read_text(), PIKA_YAML)
        self.assertFalse((t / INIT.lstrip("/")).exists())
        self.assertFalse((t / "usr/local/lib/backshot1").exists())
        self.assertFalse((t / "etc/backshot1").exists())
        self.assertEqual(len(self.builds()), 4)

    def test_refind_put_back_by_hand_is_written_again(self):
        self.adopt()
        self.refind.write_text(PIKA_REFIND)
        out = self.adopt()
        self.assertIn(f"wrote {self.refind}", out)
        self.assertIn(f"rdinit={self.t}{INIT}", self.refind.read_text())
        # The images are remade and checked before it names the preinit.
        self.assertEqual(len(self.builds()), 4)

    def test_an_image_without_the_preinit_puts_everything_back(self):
        (self.t / "no-extra").write_text("")
        out = self.adopt(rc=1)
        self.assertIn(f"booster.img-7.3.3-pikaos has no {str(self.t).lstrip('/')}{INIT}", out)
        self.assertIn("putting everything back", out)
        self.assertEqual(self.refind.read_text(), PIKA_REFIND)
        self.assertEqual(self.yaml.read_text(), PIKA_YAML)
        self.assertFalse((self.t / "etc/backshot1").exists())

    def test_without_refind_attended(self):
        self.refind.unlink()
        out = self.adopt()
        self.assertIn("A: booster's initramfs without rEFInd's", out)
        self.assertEqual(self.builds(), [])
        self.assertEqual(self.yaml.read_text(), PIKA_YAML)

    def test_without_busybox_attended(self):
        (self.t / "usr/bin/busybox").unlink()
        self.assertIn("for BackShot1's preinit", self.adopt())
        self.assertEqual(self.builds(), [])

    def test_a_key_of_its_own_is_own(self):
        self.refind.write_text(f'"x" "rd.luks.name={UUID}=crypt_root rd.luks.key={UUID}=/etc/root.key quiet"\n')
        out = self.adopt()
        self.assertIn("own: every LUKS volume unlocks with no one typing", out)
        self.assertEqual(self.builds(), [])

    def test_a_first_line_without_the_volume_attended(self):
        self.refind.write_text('"Other" "root=UUID=abc"\n' + PIKA_REFIND)
        out = self.adopt()
        self.assertIn("rEFInd's default", out)
        self.assertNotIn("rdinit", self.refind.read_text())


@unittest.skipUnless(shutil.which("sh") and shutil.which("mkfifo"), "no sh or mkfifo")
class UnlockKeyBooster(base.Tmp):
    """bs1-unlock-key --booster-fifos and --booster: the drive's key through
    the FIFO booster reads, or an empty one; a mark for each."""

    def setUp(self):
        super().setUp()
        self.root = self.t / "root"
        for d in ("etc/backshot1", "run", "proc", "dev/disk/by-label", "drive/backshot1/keys"):
            (self.root / d).mkdir(parents=True)
        (self.root / "drive/backshot1/keys/disk-passphrase").write_text("s3cret")
        (self.root / "proc/mounts").write_text("proc /proc proc rw 0 0\nrun /run tmpfs rw 0 0\n")
        exe(self.bin / "mount", f'#!/bin/sh\necho "$@" >>"{self.t}/mounts"\nfor d; do :; done\ncp -R "{self.root}/drive/." "$d/"\n')
        exe(self.bin / "umount", '#!/bin/sh\nrm -rf "$1"/*\n')
        exe(self.bin / "modprobe", "#!/bin/sh\nexit 0\n")
        exe(self.bin / "sleep", "#!/bin/sh\nexit 0\n")
        exe(self.bin / "findfs", "#!/bin/sh\nexit 1\n")
        (self.root / "etc/backshot1/luks-unlock").write_text(textwrap.dedent(f"""\
            family=P
            generator=booster
            label=mybig512
            path=/backshot1/keys/disk-passphrase
            fstype=xfs
            wait=2
            volumes='crypt_root:{UUID}:P home:0000:S'
            """))
        self.fifo = self.root / f"backshot1-key.{UUID}"

    def run_key(self, *args):
        return subprocess.run(["sh", str(BS1 / "bs1-unlock-key"), *args], capture_output=True, text=True, timeout=60,
                              env=self.env(BS1_UNLOCK_ROOT=str(self.root), BS1_GIVE_WAIT="20"))

    def marks(self):
        p = self.root / "run/backshot1-unlock"
        return p.read_text().splitlines() if p.exists() else []

    def booster_reads(self):
        """booster's read of its key file, in a thread: what it got."""
        got = {}

        def read():
            with open(self.fifo, "rb") as f:
                got["key"] = f.read()
        th = threading.Thread(target=read, daemon=True)
        th.start()
        return th, got

    def test_fifos(self):
        self.assertEqual(self.run_key("--booster-fifos").returncode, 0)
        self.assertTrue(stat.S_ISFIFO(os.stat(self.fifo).st_mode))
        self.assertEqual(stat.S_IMODE(os.stat(self.fifo).st_mode), 0o600)
        self.assertEqual(sorted(p.name for p in self.root.glob("backshot1-key*")), [self.fifo.name])

    def test_the_drive_key_through_the_fifo(self):
        (self.root / "dev/disk/by-label/mybig512").write_text("")
        self.run_key("--booster-fifos")
        th, got = self.booster_reads()
        r = self.run_key("--booster")
        th.join(10)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(got.get("key"), b"s3cret")
        self.assertEqual(self.marks(), ["crypt_root drive"])
        self.assertIn("-o ro,norecovery", (self.t / "mounts").read_text())
        self.assertFalse(self.fifo.exists())
        self.assertFalse((self.root / "backshot1-key.tmp").exists())
        self.assertIn("handed to booster", (self.root / "dev/kmsg").read_text())

    def test_without_the_drive_an_empty_key(self):
        self.run_key("--booster-fifos")
        th, got = self.booster_reads()
        r = self.run_key("--booster")
        th.join(10)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(got.get("key"), b"")
        self.assertEqual(self.marks(), ["crypt_root typed"])
        self.assertIn("no backup drive mybig512", r.stderr)

    def test_no_fifo_is_typed(self):
        (self.root / "dev/disk/by-label/mybig512").write_text("")
        r = self.run_key("--booster")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.marks(), ["crypt_root typed"])


@unittest.skipUnless(BASH, "needs bash 4 or newer")
class EvidenceP(base.Tmp):
    """unlock_how P: the helper's mark, unless booster logged that the key
    didn't fit."""

    def setUp(self):
        super().setUp()
        self.mark = self.t / "backshot1-unlock"
        self.klog = self.t / "klog"
        self.klog.write_text("booster: Starting booster initramfs\n")
        exe(self.bin / "journalctl", f'#!/bin/sh\ncat "{self.klog}"\n')

    def how(self):
        lib = base.functions(BS1 / "bs1-common", "unlock_klog", "unlock_how")
        r = subprocess.run([BASH, "-c", f'UNLOCK_MARK="{self.mark}"\n{lib}\nunlock_how crypt_root P'],
                           capture_output=True, text=True, env=self.env())
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_drive(self):
        self.mark.write_text("crypt_root drive\n")
        self.assertEqual(self.how(), "drive")

    def test_a_key_that_didnt_fit(self):
        self.mark.write_text("crypt_root drive\n")
        self.klog.write_text("booster: password in keyfile #{keyfile} was unable to unseal #{mappingName}\n")
        self.assertEqual(self.how(), "typed")

    def test_typed_and_no_mark(self):
        self.assertEqual(self.how(), "typed")
        self.mark.write_text("crypt_root typed\n")
        self.assertEqual(self.how(), "typed")


class InstallShKeyFile(unittest.TestCase):
    """install.sh's key-file LUKS (pika-bootstrap.sh --luks): its sed takes
    each of the GTK script's printf | cryptsetup lines, and nothing else."""

    INSTALL = BS1.parent.parent / "pika" / "install.sh"

    def test_the_four_lines(self):
        text = self.INSTALL.read_text()
        lines = [l for l in text.splitlines() if l.startswith('printf "$PIKA_INSTALL_AUTO_LUKS_PASSWORD" | cryptsetup')]
        self.assertEqual(len(lines), 4)
        m = re.search(r"sed -i -E 's/(.*?)/(.*?)/' \"\$gtk\"", text)
        self.assertTrue(m)
        # sed -E's pattern as Python's: \$ and \| are literal there too.
        pat = m.group(1)
        rep = m.group(2).replace(r"\1", r"\g<1>").replace(r"\2", r"\g<2>")
        for l in lines:
            out = re.sub(pat, rep, l)
            self.assertNotIn("PASSWORD", out)
            self.assertRegex(out, r'^cryptsetup -q -v (--type luks2 )?(luksFormat|luksOpen) --key-file "\$PIKA_INSTALL_AUTO_LUKS_KEYFILE" /dev/')
        self.assertIn('[[ $LAYOUT == btrfs-luks && -z $KEYFILE ]] && crypt=', text)


if __name__ == "__main__":
    unittest.main()
