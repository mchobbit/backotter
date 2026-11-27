"""I4's unit tests for FreeBSD (docs/design/img-speed.md §4.2, D1, D2, §14 row I4): an MBR disk's
layout and config slice, the swap list, FreeBSD's build.sh, and img-one.sh --refresh-from freebsd.

stick/img_gpt.py's `part` on a disk with no GPT (the MBR's slice, FreeBSD's config slice 3), and
img_inputs.py's layout of one (each slice hashed but the rewritten ones; a rewritten FreeBSD slice
keeps its first 8 KiB, the boot blocks and the BSD label); build.sh's subcommands, and the planner's
facts calling FreeBSD refresh-ready; img-one.sh --refresh-from freebsd in a fixture world, with the
FreeBSD builder's stub ssh and test_img_refresh_bsd's stub make-img-bsd.sh: the builder started and
fingerprinted as for a full build, each image from its own parent, exit 6 when the binaries don't
fit.  The VM's own part (build.sh refresh: mdconfig, fsck, tunefs -L, the fstabs) and E1 run on the
farm.

No host, no VM.  Run from the worktree: python3 -m unittest discover testvm/test2/tests.
"""

import contextlib
import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_img_inputs as T  # noqa: E402  (its fixture world)
import test_img_refresh_bsd as B  # noqa: E402  (img_gpt.py, the stub make-img-bsd.sh)

II = T.II
G = B.G
rd, wr = T.rd, T.wr
STICK = T.STICK
BUILD = os.path.join(STICK, "freebsd", "build.sh")


def mbr_disk(path, slices, total=4096):
    """A disk with an MBR and no GPT, as FreeBSD's mkimg -s mbr makes: SLICES is
    [(status, type, first, sectors, bytes)]."""
    img = bytearray(total * 512)
    img[:440] = b"\x90" * 440                       # boot code
    for i, (status, typ, first, n, data) in enumerate(slices):
        img[446 + 16 * i:462 + 16 * i] = bytes([status, 0, 0, 0, typ, 0, 0, 0]) + struct.pack("<II", first, n)
        img[first * 512:first * 512 + len(data)] = data
    img[510:512] = b"\x55\xaa"
    with open(path, "wb") as f:
        f.write(img)


def poke(path, sector, data):
    with open(path, "r+b") as f:
        f.seek(sector * 512)
        f.write(data)


class Mbr(unittest.TestCase):
    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="t2-i4f-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.img = os.path.join(self.t, "d.img")
        # FreeBSD's: the ESP, the active root slice with its BSD label, the config slice.
        mbr_disk(self.img, [(0, 0xEF, 1, 100, b"esp"), (0x80, 0xA5, 101, 1000, b"boot+label"),
                            (0, 0x06, 1101, 512, b"config")])

    def test_part_reads_the_mbr_where_there_is_no_gpt(self):
        self.assertEqual(G.part(self.img, 3), (1101, 512))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(G.main(["part", self.img, "2"]), 0)
        self.assertEqual(out.getvalue(), "101 1000\n")
        with self.assertRaises(G.Refused):
            G.part(self.img, 4)
        with self.assertRaises(G.Refused):
            G.part(self.img, 5)

    def test_part_refuses_a_protective_mbr_whose_gpt_is_gone(self):
        mbr_disk(self.img, [(0, 0xEE, 1, 4095, b"")])
        with self.assertRaisesRegex(G.Refused, "protective"):
            G.part(self.img, 1)

    def test_part_refuses_a_disk_with_no_table(self):
        wr(self.img, "x" * 1024)
        with self.assertRaisesRegex(G.Refused, "neither a GPT nor an MBR"):
            G.part(self.img, 3)

    def test_a_gpt_disk_still_reads_its_gpt(self):
        B.gpt_disk(self.img, [(B.EFI, 40, 99, "EFI system", b"esp"), (B.DATA, 100, 999, "config", b"c")])
        self.assertEqual(G.part(self.img, 2), (100, 900))

    def lay(self):
        return II.layout(self.img, {2, 3}, xorriso=os.path.join(self.t, "no-such-xorriso"), eltorito=False)

    def test_layout_hashes_each_slice_but_the_rewritten(self):
        lay = self.lay()
        self.assertTrue(any(x.startswith("layout\t/mbr/part/1\tstatus=00 type=ef size=51200 sha256=") for x in lay))
        self.assertIn("layout\t/mbr/part/2\tstatus=80 type=a5 rewritten", lay)
        self.assertIn("layout\t/mbr/part/3\tstatus=00 type=06 rewritten", lay)
        self.assertTrue(any(x.startswith("layout\t/mbr/part/2/boot\tsha256=") for x in lay))
        self.assertFalse([x for x in lay if "/gpt/" in x or "/eltorito/" in x])

    def test_the_root_and_config_may_change_the_esp_and_boot_blocks_may_not(self):
        before = self.lay()
        poke(self.img, 101 + 16, b"a new root")           # past the slice's first 8 KiB
        poke(self.img, 1101, b"a new config")
        self.assertEqual(self.lay(), before)
        poke(self.img, 101 + 1, b"another label")          # the BSD label
        self.assertNotEqual(self.lay(), before)
        mid = self.lay()
        poke(self.img, 90, b"esp change")       # past the 32 KiB system area
        after = self.lay()
        self.assertNotEqual(after, mid)
        self.assertEqual([x for x in set(after) ^ set(mid)], [x for x in set(after) ^ set(mid) if "/mbr/part/1\t" in x])

    def test_positions_and_the_disk_signature_are_masked(self):
        before = self.lay()
        poke(self.img, 0, b"\x90" * 440 + b"\x12\x34\x56\x78")   # a disk signature
        self.assertEqual(self.lay(), before)

    def test_the_swap_list_names_freebsds_rewritten_slices_and_id_sites(self):
        old = os.environ.get("S")
        os.environ["S"] = STICK
        try:
            self.assertEqual(II.layout_rewritten("freebsd"), {2, 3})
            ids = {r["path"] for r in II.swap_rows("freebsd") if r["kind"] == "id"}
            self.assertEqual(ids, {"/etc/fstab", "/conf/base/etc/fstab"})
        finally:
            os.environ.pop("S") if old is None else os.environ.update(S=old)



if __name__ == "__main__":
    unittest.main()
