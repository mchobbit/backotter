"""Vanilla OS V11: BackShot1 on ABRoot (docs/design/vanillaos.md) on fake
inputs and the vanilla3 corpus dump: bs1-abroot's preflight refusals, its
record of the two roots and its boot check; bs1-manifest's present-root
mapping and held boot files; bs1-common's six-path tree from
backup_sources' mount hints; bs1-qemu.py's pick of the other GRUB entry.
No VM, no root, no ABRoot: fake findmnt, lsblk and mount."""

import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
TESTVM = TESTS.parent.parent
BS1 = TESTVM / "realworld" / "backshot1"
CORPUS = TESTVM.parent / "holt" / "tests" / "corpus" / "vanilla3" / "src.json"
K = "7.3.3+deb14-amd64"


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


A = load("bs1_abroot_under_test", BS1 / "bs1-abroot")
M = load("bs1_manifest_abroot_under_test", BS1 / "bs1-manifest")


def corpus():
    return json.loads(CORPUS.read_text())


MASTER = """set default=1
set timeout=5
serial --unit=0 --speed=115200
terminal_input serial console
terminal_output serial console

menuentry "Previous State (A)" --class abroot-a {
    set root=(lvm/vos--root-init)
    configfile "/vos-a/abroot.cfg"
}
menuentry "Current State (B)" --class abroot-b {
    set root=(lvm/vos--root-init)
    configfile "/vos-b/abroot.cfg"
}
"""


def put(root, rel, content=""):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def vanilla_tree(root, default=1):
    """A Vanilla OS 3 tree as BackShot1 lays it out: the present root (vos-b
    when DEFAULT is 1) at /, the other at part-future, the init LV at
    boot/init, the ESP at boot/efi, /var with both roots' /etc uppers."""
    put(root, "usr/share/abroot/abroot.json", '{"partLabelA": "vos-a"}\n')
    put(root, "part-future/usr/share/abroot/abroot.json", '{"partLabelA": "vos-a"}\n')
    put(root, "part-future/etc/hostname", "vanilla\n")
    put(root, "etc/hostname", "vanilla\n")
    put(root, "etc/fstab", "# the image's\n")
    put(root, "boot/grub/grub.cfg", MASTER.replace("set default=1", f"set default={default}"))
    put(root, "boot/grub/grub.cfg.future", MASTER.replace("set default=1", f"set default={1 - default}"))
    put(root, "boot/grub/grubenv", "# GRUB Environment Block\n")
    for lab in ("vos-a", "vos-b"):
        put(root, f"boot/init/{lab}/vmlinuz-{K}", f"kernel {lab}")
        put(root, f"boot/init/{lab}/initrd.img-{K}", f"initrd {lab}")
        put(root, f"boot/init/{lab}/abroot.cfg", f"linux /{lab}/vmlinuz-{K}\n")
        put(root, f"var/lib/abroot/etc/{lab}/hostname", "thinkpad\n")
        put(root, f"var/lib/abroot/etc/{lab}/bko-root", lab[-1] + "\n")
        put(root, f"var/lib/abroot/etc/{lab}/fstab", f"UUID={lab} / btrfs ro,defaults 0 0\n")
        (Path(root) / f"var/lib/abroot/etc/{lab}-work/work").mkdir(parents=True)
    put(root, "boot/efi/EFI/vanilla/shimx64.efi", "shim")
    put(root, "boot/efi/EFI/vanilla/grubx64.efi", "grub")
    put(root, "boot/efi/EFI/BOOT/BOOTX64.EFI", "shim")
    put(root, "var/home/m/.cache/x", "cache")
    put(root, "var/log/syslog", "log")
    (Path(root) / "var/mnt").mkdir(parents=True)
    put(root, "part-future/usr/bin/true", "#!")


class TestPreflight(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t)
        (self.t / "boot" / "init").mkdir(parents=True)
        (self.t / "part-future").mkdir()
        self.d = corpus()
        self.a = self.d["boot"]["abroot"]

    def problems(self):
        return A.preflight(self.d, root=str(self.t))

    def test_corpus_is_clean(self):
        self.assertEqual(self.problems(), [])

    def test_operation_lock(self):
        self.a["operation_lock"] = True
        self.a["mounts_skipped"] = True
        p = self.problems()
        self.assertEqual(len(p), 1, p)
        self.assertIn("an ABRoot operation is running", p[0])

    def test_upgrade_waits_for_a_reboot(self):
        self.a["present"] = "vos-a"
        p = self.problems()
        self.assertEqual(len(p), 1, p)
        self.assertIn("boots vos-b next, not the running vos-a", p[0])

    def test_not_the_thin_two_root_layout(self):
        self.a["thin_two_roots"] = False
        self.a["v1_system"] = True
        p = self.problems()
        self.assertTrue(any("ABRoot v1" in x for x in p), p)
        self.assertTrue(any("two roots on a thin pool" in x for x in p), p)

    def test_no_default(self):
        self.a["grub"]["default_label"] = None
        self.assertEqual(self.problems(), ["the master grub.cfg has no default entry leading to a root"])

    def test_default_root_empty(self):
        self.a["roots"][1]["empty"] = True
        self.assertEqual(self.problems(), ["the master grub.cfg's default leads to vos-b, which is empty"])

    def test_future_root_missing_from_backup_sources(self):
        self.d["storage"]["backup_sources"] = [s for s in self.d["storage"]["backup_sources"] if s["path"] != "/part-future"]
        p = self.problems()
        self.assertEqual(p, ["the root vos-a (/dev/mapper/vos--root-root--a) isn't in the layout's storage.backup_sources, so it wouldn't be backed up"])

    def test_no_mount_hint(self):
        for s in self.d["storage"]["backup_sources"]:
            if s["path"] == "/boot/init":
                del s["mount"]
        self.assertEqual(self.problems(), ["backup_sources' /boot/init isn't mounted and has no mount hint"])

    def test_mount_point_missing(self):
        (self.t / "part-future").rmdir()
        self.assertEqual(self.problems(), ["/part-future isn't a directory on this system, so /dev/mapper/vos--root-root--a can't be mounted there in the tree"])

    def test_not_abroot(self):
        del self.d["boot"]["abroot"]
        self.assertIn("found no ABRoot", self.problems()[0])

    def main(self, layout):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = A.main(["preflight", str(layout)])
        return rc, buf.getvalue()

    def test_main_exit_codes(self):
        bad = self.t / "bad.json"
        bad.write_text("{not json")
        rc, out = self.main(bad)
        self.assertEqual(rc, 2)
        self.assertIn("couldn't tell", out)
        self.a["operation_lock"] = True
        lay = self.t / "lay.json"
        lay.write_text(json.dumps(self.d))
        rc, out = self.main(lay)
        self.assertEqual(rc, 1)
        self.assertIn("operation is running", out)


class TestRecord(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t)
        self.tree = self.t / "tree"
        vanilla_tree(self.tree)
        self.lines = A.record(corpus(), str(self.tree), cmdline="BOOT_IMAGE=/vos-b/vmlinuz root=/dev/mapper/vos--root-root--b")
        self.keyed = {}
        for ln in self.lines:
            k, _, v = ln.partition(" ")
            self.keyed.setdefault(k, []).append(v)

    def test_roots_and_grub(self):
        self.assertEqual(self.keyed["present"], ["vos-b"])
        self.assertEqual(self.keyed["future"], ["vos-a"])
        self.assertEqual(self.keyed["default"], ["1 vos-b"])
        self.assertEqual(self.keyed["entry"], ["0 vos-a Previous State (A)", "1 vos-b Current State (B)"])
        a, b = self.keyed["root"]
        self.assertTrue(a.startswith("vos-a uuid=bc000000-0000-4000-8000-00000000000c fstype=btrfs dm=vos--root-root--a empty=no abimage=sha256:f1be77e7"), a)
        self.assertTrue(b.startswith("vos-b "), b)
        self.assertEqual(self.keyed["init"], ["vos-init uuid=bc000000-0000-4000-8000-000000000008 fstype=ext4 dm=vos--root-init"])
        self.assertIn(f"vos-a kernel=(lvm/vos--root-init)/vos-a/vmlinuz-{K} initrd=(lvm/vos--root-init)/vos-a/initrd.img-{K} "
                      f"root=UUID=bc000000-0000-4000-8000-00000000000c search=-", self.keyed["abroot-cfg"])

    def test_files_by_their_bytes(self):
        want = hashlib.sha256(b"kernel vos-a").hexdigest()
        self.assertIn(f"vos-a vmlinuz-{K} {want}", self.keyed["init-file"])
        self.assertEqual(len(self.keyed["init-file"]), 6)
        grub = dict(v.split(" ", 1) for v in self.keyed["grub"])
        self.assertEqual(grub["boot/grub/grub.cfg"], hashlib.sha256((self.tree / "boot/grub/grub.cfg").read_bytes()).hexdigest())
        self.assertNotEqual(grub["boot/grub/grub.cfg.future"], "-")
        j = {v.rsplit(" ", 1)[0]: v.rsplit(" ", 1)[1] for v in self.keyed["abroot-json"]}
        self.assertNotEqual(j["vos-a image"], "-")         # part-future's
        self.assertNotEqual(j["vos-b image"], "-")         # /'s
        self.assertEqual(j["vos-a etc"], "-")              # no /etc/abroot/abroot.json in the uppers

    def test_config_hostnames_marks(self):
        self.assertIn('thinProvisioning=true', self.keyed["config"])
        self.assertFalse(any(v.startswith("files=") for v in self.keyed["config"]))
        self.assertEqual(self.keyed["hostname"], ["vos-a thinkpad", "vos-b thinkpad"])
        self.assertEqual(self.keyed["bko-root"], ["vos-a a", "vos-b b"])
        self.assertEqual(self.keyed["cmdline"], ["BOOT_IMAGE=/vos-b/vmlinuz root=/dev/mapper/vos--root-root--b"])

    def test_upper_hostname_wins_over_the_image(self):
        (self.tree / "var/lib/abroot/etc/vos-a/hostname").unlink()
        lines = A.record(corpus(), str(self.tree), cmdline="x")
        self.assertIn("hostname vos-a vanilla", lines)     # part-future/etc/hostname

    def test_empty_root_dir(self):
        shutil.rmtree(self.tree / "boot/init/vos-a")
        lines = A.record(corpus(), str(self.tree), cmdline="x")
        self.assertIn("init-file vos-a - -", lines)

    def test_not_abroot(self):
        d = corpus()
        del d["boot"]["abroot"]
        with self.assertRaises(ValueError):
            A.record(d, str(self.tree), cmdline="x")

    def test_state(self):
        self.assertEqual(A.state(str(self.tree))[-1], "lock no")
        put(self.tree, "run/abroot/operation.lock")
        s = A.state(str(self.tree))
        self.assertEqual(s[-1], "lock yes")
        self.assertEqual(len(s), 3)


class TestBootCheck(unittest.TestCase):
    CMD_B = f"BOOT_IMAGE=/vos-b/vmlinuz-{K} root=/dev/mapper/vos--root-root--b console=tty0 console=ttyS0,115200n8 bgrt_disable"
    CMD_A = (f"BOOT_IMAGE=(lvm/vos--root-init)/vos-a/vmlinuz-{K} root=UUID=bc000000-0000-4000-8000-00000000000c "
             "console=tty0 console=ttyS0,115200n8 bgrt_disable lsm=integrity")

    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t)
        tree = self.t / "tree"
        vanilla_tree(tree)
        self.base = self.t / "abroot.txt"
        self.base.write_text("\n".join(A.record(corpus(), str(tree), cmdline=self.CMD_B)) + "\n")
        self.grub = {rel: A.sha256(str(tree / rel)) for rel in A.GRUB_FILES}
        self.digest = corpus()["boot"]["abroot"]["roots"][0]["abimage"]["digest"]

    def now(self, label="vos-b", **kw):
        n = {"label": label, "status_present": label, "cmdline": self.CMD_B if label == "vos-b" else self.CMD_A,
             "hostname": "thinkpad", "bko_root": label[-1], "abimage": self.digest, "grub": dict(self.grub),
             "root_fs": "btrfs ro,relatime,discard=async,space_cache=v2,subvolid=5,subvol=/",
             "etc_fs": f"overlay rw,relatime,lowerdir=/etc,upperdir=/var/lib/abroot/etc/{label},workdir=/var/lib/abroot/etc/{label}-work"}
        n.update(kw)
        return n

    def check(self, **kw):
        return A.boot_check(str(self.base), self.now(**kw))

    def test_default(self):
        kind, label, lines, ok = self.check()
        self.assertEqual((kind, label), ("default", "vos-b"))
        self.assertTrue(ok, lines)
        self.assertTrue(any("the kernel command line is the baseline's" in ln for ln in lines))

    def test_other(self):
        kind, label, lines, ok = self.check(label="vos-a")
        self.assertEqual((kind, label), ("other", "vos-a"))
        self.assertTrue(ok, lines)
        self.assertFalse(any("the kernel command line is the baseline's" in ln for ln in lines))

    def test_unknown_root(self):
        kind, _, lines, ok = self.check(label="vos-c")
        self.assertEqual(kind, "unknown")
        self.assertFalse(ok)

    def test_wrong_kernel_and_root(self):
        cmd = self.CMD_A.replace("vos-a/vmlinuz", "vos-b/vmlinuz").replace("UUID=bc000000-0000-4000-8000-00000000000c", "UUID=x")
        _, _, lines, ok = self.check(label="vos-a", cmdline=cmd)
        self.assertFalse(ok)
        fails = [ln for ln in lines if ln.startswith("  FAIL")]
        self.assertEqual(len(fails), 2, lines)

    def test_default_cmdline_changed(self):
        _, _, lines, ok = self.check(cmdline=self.CMD_B + " quiet")
        self.assertFalse(ok)
        self.assertEqual([ln for ln in lines if ln.startswith("  FAIL")], [f"  FAIL  the kernel command line is the baseline's: {self.CMD_B} quiet"])

    def test_grub_written(self):
        g = dict(self.grub)
        g["boot/grub/grub.cfg"] = "0" * 64
        _, _, lines, ok = self.check(grub=g)
        self.assertFalse(ok)

    def test_mark_host_digest_status(self):
        _, _, lines, ok = self.check(bko_root="a", hostname="other", abimage="sha256:0", status_present="vos-a")
        self.assertEqual(len([ln for ln in lines if ln.startswith("  FAIL")]), 4, lines)

    def test_root_and_etc_mounts(self):
        _, _, lines, ok = self.check(root_fs="btrfs rw,relatime", etc_fs="btrfs rw")
        self.assertEqual(len([ln for ln in lines if ln.startswith("  FAIL")]), 2, lines)
        _, _, lines, ok = self.check(etc_fs="overlay rw,lowerdir=/etc,upperdir=/var/lib/abroot/etc/vos-a,workdir=/w")
        self.assertFalse(ok)


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t)
        self.root = self.t / "root"
        vanilla_tree(self.root)

    def manifest(self, name):
        out = self.t / name
        if hasattr(os, "listxattr"):
            M.write(str(self.root), str(out))
        else:
            with mock.patch.object(M, "xattrs", lambda p: "-"):
                M.write(str(self.root), str(out))
        return out

    def compare(self, a, b):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = M.compare(str(a), str(b))
        return rc, buf.getvalue()

    def touch(self, rel, content=None):
        p = self.root / rel
        if content is not None:
            p.write_text(content)
        t = time.time() + 100
        os.utime(p, (t, t))

    def after(self, *changes):
        a = self.manifest("a.tsv")
        for c in changes:
            self.touch(*c) if isinstance(c, tuple) else self.touch(c)
        return self.compare(a, self.manifest("b.tsv"))

    def test_present_row(self):
        a = self.manifest("a.tsv")
        self.assertEqual(a.read_text().splitlines()[0], "/abroot-present\tvos-b")
        rows, default = M.read(str(a))
        self.assertIsNone(default)
        self.assertNotIn("/abroot-present", rows)
        self.assertEqual(M.read_present(str(a)), "vos-b")

    def test_present_from_the_default(self):
        self.assertEqual(M.abroot_present(str(self.root)), "vos-b")
        put(self.root, "boot/grub/grub.cfg", MASTER.replace("set default=1", "set default=0"))
        self.assertEqual(M.abroot_present(str(self.root)), "vos-a")
        put(self.root, "boot/grub/grub.cfg", MASTER.replace("set default=1", "set default=7"))
        self.assertEqual(M.abroot_present(str(self.root)), "-")
        (self.root / "usr/share/abroot/abroot.json").unlink()
        self.assertIsNone(M.abroot_present(str(self.root)))

    def test_unchanged(self):
        rc, out = self.after()
        self.assertEqual(rc, 0, out)
        self.assertIn("ABRoot: present root vos-b, after vos-b", out)
        self.assertIn("ABRoot: 7 paths of the master grub.cfg", out)

    def test_present_upper_boot_churn_is_expected(self):
        # mount-sys rewrites fstab (same content); a boot's machine-id and
        # resolv.conf; GRUB's grubenv; the journal; a home's cache.
        put(self.root, "var/lib/abroot/etc/vos-b/machine-id", "1\n")
        a = self.manifest("a.tsv")
        for rel in ("var/lib/abroot/etc/vos-b/fstab", "var/lib/abroot/etc/vos-b/machine-id", "boot/grub/grubenv",
                    "var/log/syslog", "var/home/m/.cache/x"):
            self.touch(rel)
        put(self.root, "var/lib/abroot/etc/vos-b/resolv.conf", "nameserver 1\n")
        put(self.root, "var/lib/abroot/etc/vos-b-work/work/x", "")
        (self.root / "var/mnt/bs1-backup").mkdir()
        rc, out = self.compare(a, self.manifest("b.tsv"))
        self.assertEqual(rc, 0, out)

    def test_present_upper_change_is_a_change(self):
        # var/lib/* no longer excuses the present root's /etc.
        rc, out = self.after(("var/lib/abroot/etc/vos-b/hostname", "other\n"))
        self.assertEqual(rc, 1, out)
        self.assertIn("changed: var/lib/abroot/etc/vos-b/hostname", out)
        rc, out = self.after(("var/lib/abroot/etc/vos-b/fstab", "UUID=new / btrfs ro 0 0\n"))
        self.assertEqual(rc, 1, out)

    def test_other_root_gets_no_exceptions(self):
        for rel in ("var/lib/abroot/etc/vos-a/fstab", "part-future/etc/hostname", f"boot/init/vos-a/initrd.img-{K}"):
            rc, out = self.after(rel)
            self.assertEqual(rc, 1, (rel, out))
            self.assertIn(f"changed: {rel}", out)
        put(self.root, "var/lib/abroot/etc/vos-a-work/work/x", "")
        rc, out = self.compare(self.manifest("c.tsv"), self.manifest("d.tsv"))
        self.assertEqual(rc, 0, out)
        a = self.manifest("e.tsv")
        put(self.root, "var/lib/abroot/etc/vos-a-work/work/y", "")
        rc, out = self.compare(a, self.manifest("f.tsv"))
        self.assertEqual(rc, 1, out)
        self.assertIn("added: var/lib/abroot/etc/vos-a-work/work/y", out)

    def test_boot_files_are_held(self):
        for rel in ("boot/grub/grub.cfg.future", "boot/efi/EFI/vanilla/shimx64.efi", "boot/efi/EFI/BOOT/BOOTX64.EFI"):
            p = self.root / rel
            old = p.read_text()
            rc, out = self.after((rel, old + "x"))
            self.assertEqual(rc, 1, (rel, out))
            p.write_text(old)
        # A new mtime alone is no change for a held file.
        rc, out = self.after("boot/efi/EFI/vanilla/grubx64.efi")
        self.assertEqual(rc, 0, out)

    def test_not_abroot_unchanged(self):
        (self.root / "usr/share/abroot/abroot.json").unlink()
        rc, out = self.after("var/lib/abroot/etc/vos-a/fstab", ("boot/grub/grub.cfg.future", "x"))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("ABRoot", out)

    def test_logical(self):
        L = M.abroot_logical
        self.assertEqual(L("var/lib/abroot/etc/vos-b/ssh/x", "vos-b"), "etc/ssh/x")
        self.assertEqual(L("var/lib/abroot/etc/vos-b", "vos-b"), "etc")
        self.assertEqual(L("var/lib/abroot/etc/vos-b-work/work", "vos-b"), "var/lib/abroot/etc/vos-b-work/work")
        self.assertTrue(L("var/lib/abroot/etc/vos-a/x", "vos-b").startswith(M.ABROOT_HELD))
        self.assertEqual(L("var/root/.bash_history", "vos-b"), "root/.bash_history")
        self.assertEqual(L("var/mnt", "vos-b"), "mnt")
        self.assertEqual(L("var/lib/abroot/etc/vos-a/x", "-"), "var/lib/abroot/etc/vos-a/x")
        self.assertEqual(L("var/lib/abroot/etc/vos-a/x", None), "var/lib/abroot/etc/vos-a/x")


def bash_functions(path, *names):
    text = Path(path).read_text()
    out = []
    for n in names:
        m = re.search(rf"^{re.escape(n)}\(\) {{\n.*?^}}\n", text, re.M | re.S)
        assert m, f"no function {n} in {path}"
        out.append(m.group(0))
    return "\n".join(out)


def exe(path, text):
    Path(path).write_text(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


class TestTree(unittest.TestCase):
    """bs1-common's internal_mounts, check_coverage and mount_hints on the
    vanilla3 dump, with fake findmnt, lsblk and mount."""

    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t)
        self.bin = self.t / "bin"
        self.bin.mkdir()
        self.layout = self.t / "layout.json"
        self.layout.write_text(CORPUS.read_text())
        self.ro = self.t / "ro"
        self.log = self.t / "mount.log"
        exe(self.bin / "mount", f'#!/bin/sh\necho "$*" >>"{self.log}"\n')
        # Mounted: the running system's four; findmnt -rn PATH: nothing there.
        exe(self.bin / "findmnt", '#!/bin/sh\ncase "$*" in\n-rno\\ SOURCE) printf "%s\\n" /dev/mapper/vos--root-root--b /dev/vda1 /dev/vda2 /dev/mapper/vos--var-var ;;\n*) exit 1 ;;\nesac\n')
        exe(self.bin / "lsblk", '#!/bin/sh\nprintf "%s\\n" "/dev/vda" "/dev/vda1 ext4" "/dev/vda2 vfat" "/dev/vda3 LVM2_member" "/dev/vda4 LVM2_member" '
                                '"/dev/mapper/vos--root-init ext4" "/dev/mapper/vos--root-root--a btrfs" "/dev/mapper/vos--root-root--b btrfs" "/dev/mapper/vos--var-var btrfs"\n')

    def sh(self, body, on_disk="return 0"):
        lib = bash_functions(BS1 / "bs1-common", "internal_mounts", "check_coverage", "mount_hints")
        script = (f'set -uo pipefail\nREAL_FS="ext2|ext3|ext4|xfs|btrfs|vfat"\nRO_MOUNTS="{self.ro}"\n'
                  'is_ostree() { return 1; }\nis_abroot() { return 0; }\n'
                  f'on_disk() {{ {on_disk}; }}\ndie() {{ echo "DIE: $*"; exit 1; }}\nmust() {{ "$@" || die "this failed: $*"; }}\n{lib}\n{body}\n')
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env={**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}"})

    def test_six_paths(self):
        r = self.sh(f'internal_mounts vda "{self.layout}"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [
            "/\t/\tbtrfs", "/boot\t/boot\text4", "/boot/efi\t/boot/efi\tvfat",
            f"/boot/init\t{self.ro}/boot/init\text4", f"/part-future\t{self.ro}/part-future\tbtrfs", "/var\t/var\tbtrfs"])

    def test_from_null_without_a_hint_fails(self):
        d = corpus()
        for s in d["storage"]["backup_sources"]:
            s.pop("mount", None)
        self.layout.write_text(json.dumps(d))
        r = self.sh(f'internal_mounts vda "{self.layout}" >/dev/null; echo "rc=$?"')
        self.assertIn("rc=1", r.stdout)
        self.assertIn("has no mount hint", r.stderr)

    def test_coverage_counts_the_hints(self):
        r = self.sh('check_coverage vda; echo passed')
        self.assertIn("DIE: /dev/mapper/vos--root-init (ext4) isn't mounted", r.stdout)
        r = self.sh(f'check_coverage vda "{self.layout}"; echo passed')
        self.assertEqual(r.stdout.strip(), "passed", r.stdout + r.stderr)

    def test_mount_hints(self):
        r = self.sh(f'mount_hints vda "{self.layout}"; echo done')
        self.assertEqual(r.stdout.strip().splitlines()[-1], "done", r.stdout + r.stderr)
        self.assertEqual(self.log.read_text().splitlines(), [
            f"-t ext4 -o ro,noload,nodev,nosuid,noexec /dev/mapper/vos--root-init {self.ro}/boot/init",
            f"-t btrfs -o ro,rescue=nologreplay,nodev,nosuid,noexec /dev/mapper/vos--root-root--a {self.ro}/part-future"])
        self.assertTrue((self.ro / "boot" / "init").is_dir())

    def test_mount_hints_only_on_the_disk(self):
        r = self.sh(f'mount_hints vda "{self.layout}"; echo done', on_disk='[[ $1 != *root--a ]]')
        self.assertIn("done", r.stdout, r.stderr)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)

    def test_mount_hints_already_mounted(self):
        exe(self.bin / "findmnt", '#!/bin/sh\nexit 0\n')
        r = self.sh(f'mount_hints vda "{self.layout}"; echo done')
        self.assertIn("done", r.stdout, r.stderr)
        self.assertFalse(self.log.exists())


class TestQemuPick(unittest.TestCase):
    Q = load("bs1_qemu_under_test", BS1 / "bs1-qemu.py")

    def test_other_entry_up(self):
        title, pattern, keys = self.Q.abroot_pick("1\nPrevious State (A)\nCurrent State (B)\n")
        self.assertEqual(title, "Previous State (A)")
        self.assertRegex("   Previous State (A)   ", pattern)
        self.assertEqual(keys, r"^|^|\r")

    def test_other_entry_down(self):
        title, _, keys = self.Q.abroot_pick("0\nCurrent State (A)\nPrevious State (B)\n")
        self.assertEqual(title, "Previous State (B)")
        self.assertEqual(keys, r"v|v|\r")

    def test_not_two_entries(self):
        self.assertIsNone(self.Q.abroot_pick("1\nOnly\n"))
        self.assertIsNone(self.Q.abroot_pick("2\nA\nB\n"))


if __name__ == "__main__":
    unittest.main()
