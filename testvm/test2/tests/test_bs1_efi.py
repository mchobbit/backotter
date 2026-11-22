"""bs1-efi (testvm/realworld/backshot1): boot entries through efivarfs, for
a system with no efibootmgr (AerynOS).  On a fake sysfs and a plain
directory for efivarfs: the load option's bytes against efibootmgr
--create's (captured on OVMF), the HD() node of an MBR and a 4Kn disk,
oneshot's number, BootOrder and BootNext, delete's tidying, and a list that
survives entries it can't parse.  No root, no firmware."""

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

if os.geteuid() == 0:
    # These run BackShot1's scripts against fakes.  As root, on a real
    # machine, one slip reaches its disks, its firmware or its reboot: a
    # background reboot outlived test_bs1_to_stick's fakes and rebooted the
    # Thinkpad (2026-11-22).
    raise unittest.SkipTest("BackShot1's tests don't run as root")

TESTS = Path(__file__).resolve().parent
BS1 = TESTS.parent.parent / "realworld" / "backshot1"


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


E = load("bs1_efi_under_test", BS1 / "bs1-efi")
G = E.GLOBAL

# The rescuer's efibootmgr, OVMF, 2026-11-18: efibootmgr --create --disk
# /dev/nvme0n1 --part 1 --label "BKO cmp nvme0n1p1" --loader
# '\EFI\Boot\BOOTX64.EFI' for the aerynos guest's ESP, read back from
# efivarfs, attributes (07000000) and all.  bs1-efi oneshot's was the same
# (cmp), there and for the stick's ESP.
EFIBOOTMGR = bytes.fromhex(
    "07000000010000005e0042004b004f00200063006d00700020006e0076006d0065003000"
    "6e00310070003100000004012a000100000000080000000000000000200000000000828e"
    "1c061c3d464198836b455a66d0920202040430005c004500460049005c0042006f006f00"
    "74005c0042004f004f0054005800360034002e0045004600490000007fff0400")
PARTUUID = "061c8e82-3d1c-4146-9883-6b455a66d092"


class Fake:
    """A sysfs with one disk and its partition, an efivarfs directory, and
    lsblk answering for the partition."""

    def __init__(self, root, num=1, start=2048, size=2097152, lbs=512, pttype="gpt", partuuid=PARTUUID):
        self.sys = Path(root, "sys")
        disk = self.sys / "devices" / "pci0000:00" / "nvme0n1"
        (disk / "queue").mkdir(parents=True)
        (disk / "queue" / "logical_block_size").write_text(f"{lbs}\n")
        p = disk / "nvme0n1p1"
        p.mkdir()
        (p / "partition").write_text(f"{num}\n")
        (p / "start").write_text(f"{start}\n")
        (p / "size").write_text(f"{size}\n")
        (self.sys / "class" / "block").mkdir(parents=True)
        (self.sys / "class" / "block" / "nvme0n1").symlink_to(disk)
        (self.sys / "class" / "block" / "nvme0n1p1").symlink_to(p)
        (self.sys / "firmware" / "efi").mkdir(parents=True)
        self.vars = Path(root, "efivars")     # not SYS's: nothing is mounted
        self.vars.mkdir()
        self.lsblk = f"{pttype} {partuuid}\n"

    def put(self, name, data, attrs=7):
        (self.vars / f"{name}-{G}").write_bytes(struct.pack("<I", attrs) + data)

    def get(self, name):
        p = self.vars / f"{name}-{G}"
        return p.read_bytes()[4:] if p.exists() else None

    def names(self):
        return sorted(f.split("-")[0] for f in os.listdir(self.vars))

    def run(self, argv):
        def fake_run(cmd, **kw):
            assert cmd[0] == "lsblk", cmd
            return subprocess.CompletedProcess(cmd, 0, self.lsblk, "")
        real_open = os.open

        # A plain file keeps a longer old value's tail; efivarfs replaces it.
        def trunc_open(p, flags, mode=0o777, **kw):
            return real_open(p, flags | os.O_TRUNC, mode, **kw)
        out = io.StringIO()
        with mock.patch.object(E, "SYS", str(self.sys)), mock.patch.object(E, "VARS", str(self.vars)), \
                mock.patch.object(E.subprocess, "run", fake_run), mock.patch.object(E.os, "open", trunc_open), \
                contextlib.redirect_stdout(out):
            E.main(argv)
        return out.getvalue()


def option(label, dp=b"\x7f\xff\x04\x00", attrs=1):
    return struct.pack("<IH", attrs, len(dp)) + (label + "\0").encode("utf-16-le") + dp


def u16s(*xs):
    return b"".join(struct.pack("<H", x) for x in xs)


class LoadOption(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)

    def node(self, **kw):
        f = Fake(self.d.name, **kw)
        with mock.patch.object(E, "SYS", str(f.sys)), \
                mock.patch.object(E.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, f.lsblk, "")):
            return E.hd_node("/dev/nvme0n1p1")

    def test_is_efibootmgrs(self):
        f = Fake(self.d.name)
        f.run(["oneshot", "BKO cmp nvme0n1p1", "/dev/nvme0n1p1", "\\EFI\\Boot\\BOOTX64.EFI"])
        b = (f.vars / f"Boot0000-{G}").read_bytes()
        self.assertEqual(b, EFIBOOTMGR)

    def test_mbr(self):
        n = self.node(num=2, start=4096, size=8192, pttype="dos", partuuid="a1b2c3d4-02")
        self.assertEqual(n, struct.pack("<BBHIQQ16sBB", 4, 1, 42, 2, 4096, 8192,
                                        bytes.fromhex("d4c3b2a1") + bytes(12), 1, 1))

    def test_4kn_disk_counts_its_own_blocks(self):
        # sysfs counts 512-byte sectors whatever the disk; HD() its logical blocks.
        n = self.node(start=2048, size=2097152, lbs=4096)
        self.assertEqual(struct.unpack_from("<QQ", n, 8), (256, 262144))

    def test_neither_table(self):
        with self.assertRaises(SystemExit):
            self.node(pttype="atari")

    def test_loader_must_be_an_efi_path(self):
        with self.assertRaises(SystemExit):
            E.file_node("/EFI/BOOT/BOOTX64.EFI")


class Variables(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.f = Fake(self.d.name)
        self.f.put("Boot0000", option("UiApp"))
        self.f.put("Boot0001", option("UEFI QEMU NVMe Ctrl BKOINTERNAL 1"))
        self.f.put("Boot0002", option("BKO rescue"))      # an older stick's, in BootOrder
        self.f.put("BootOrder", u16s(2, 1, 0))

    def test_oneshot(self):
        out = self.f.run(["oneshot", "BKO rescue", "/dev/nvme0n1p1", "\\EFI\\boot\\bootx64.efi"])
        self.assertIn("deleted Boot0002 (BKO rescue)", out)
        self.assertIn("BootNext: 0002", out)
        # The old one's number is free again, and the lowest; out of BootOrder.
        self.assertEqual(self.f.get("BootOrder"), u16s(1, 0))
        self.assertEqual(self.f.get("BootNext"), u16s(2))
        attrs, desc, dp = E.parse_option(self.f.get("Boot0002"))
        self.assertEqual((attrs, desc), (1, "BKO rescue"))
        self.assertEqual(E.show_path(dp), f"HD(1,GPT,{PARTUUID},0x800,0x200000)/File(\\EFI\\boot\\bootx64.efi)")

    def test_lowest_free_number(self):
        self.f.put("Boot0002", option("something else"))
        self.f.put("Boot0004", option("and another"))
        self.f.run(["oneshot", "BKO restored", "/dev/nvme0n1p1", "\\EFI\\BOOT\\BOOTX64.EFI"])
        self.assertEqual(self.f.get("BootNext"), u16s(3))
        self.assertEqual(E.parse_option(self.f.get("Boot0003"))[1], "BKO restored")

    def test_delete_takes_bootorder_and_bootnext_too(self):
        self.f.put("BootNext", u16s(2))
        out = self.f.run(["delete", "BKO rescue"])
        self.assertIn("deleted Boot0002", out)
        self.assertNotIn("Boot0002", self.f.names())
        self.assertEqual(self.f.get("BootOrder"), u16s(1, 0))
        self.assertIsNone(self.f.get("BootNext"))

    def test_delete_of_none_changes_nothing(self):
        self.f.put("BootNext", u16s(1))
        self.assertEqual(self.f.run(["delete", "BKO restored"]), "")
        self.assertEqual(self.f.get("BootOrder"), u16s(2, 1, 0))
        self.assertEqual(self.f.get("BootNext"), u16s(1))

    def test_list_skips_what_isnt_an_entry(self):
        self.f.put("BootCurrent", u16s(1))
        self.f.put("Boot0005", b"\x01\x00\x00\x00\x04\x00B\x00K\x00")   # no end to its description
        self.f.put("Boot0006", b"\x01\x00")                             # too short
        (self.f.vars / f"Boot000a-{G}").write_bytes(b"\7\0\0\0" + option("lower-case"))
        (self.f.vars / "Boot0007-01234567-89ab-cdef-0123-456789abcdef").write_bytes(b"\7\0\0\0" + option("another GUID's"))
        out = self.f.run(["list"])
        self.assertEqual(out.splitlines()[:2], ["BootCurrent: 0001", "BootOrder: 0002,0001,0000"])
        self.assertEqual([ln.split("\t")[0] for ln in out.splitlines()[2:]],
                         ["Boot0000* UiApp", "Boot0001* UEFI QEMU NVMe Ctrl BKOINTERNAL 1", "Boot0002* BKO rescue"])

    def test_global_guid(self):
        # EFI_GLOBAL_VARIABLE, UEFI 2.10 section 3.3.
        self.assertEqual(G, "8be4df61-93ca-11d2-aa0d-00e098032b8c")


if __name__ == "__main__":
    unittest.main()
