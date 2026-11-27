"""I4's unit tests for Haiku (docs/design/img-speed.md §4.2, D1, D2, §14 row I4): its build.sh
refresh's own parts and its place in the shared VM refresh.

stick/haiku/build.sh's functions (the checkfs check of a read-only mount before and after, the kernel
against the parent's haiku package, the fit, the in-place swap, the room line) run here with bash
against fakes of Haiku's diskimage, mount, unmount, checkfs and stat -f, on a plain directory as the
BFS; the MBR layout (the ESP hashed, the BFS's boot sector kept, the BFS and the config partition
rewritten) and the swap list's rows; img-one.sh --refresh-from haiku with test_img_refresh_bsd's stub
make-img-bsd.sh, made Haiku's; the planner taking haiku as refresh-ready and fitting its binaries,
which have no restic-bko.  The VM's own part (BFS, checkfs, diskimage) and E1 run on the farm.

No host, no VM.  Run from the worktree: python3 -m unittest discover testvm/test2/tests.
"""

import json
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
import test_img_refresh_bsd as TB  # noqa: E402  (the stub make-img-bsd.sh)

II = T.II
rd, wr = T.rd, T.wr
STICK = T.STICK
BUILD = os.path.join(STICK, "haiku", "build.sh")
REPO = os.path.dirname(STICK)
BINDIR = "/system/non-packaged/bin"
PKG = "haiku-r1~beta6_hrev59866_101-1-x86_64.hpkg"

CLEAN = """        386 nodes checked,
\t0 blocks not allocated,
\t0 blocks already set,
\t0 blocks could be freed

\tfiles\t\t300
"""

# Haiku's tools as build.sh calls them, on a plain directory ($FS) as the BFS: a mount makes the
# mount point a link to it (and logs whether read-only), checkfs prints $CHECKFS_OUT, stat -f gives
# 2048-byte blocks, $FREE_BLOCKS of them free.
FAKES = r'''
register() { printf -v "$2" %s /dev/disk/virtual/files/9; disks=("$1" "${disks[@]}"); }
mount() { local d=${@: -1}; echo "mount $1" >>"$LOG"; rmdir "$d" && ln -s "$FS" "$d"; }
unmount() { echo "unmount" >>"$LOG"; rm "$1" && mkdir "$1"; }
checkfs() { echo "checkfs $*" >>"$LOG"; cat "$CHECKFS_OUT"; }
diskimage() { :; }
stat() { if [ "$1" = -f ]; then echo "2048 $FREE_BLOCKS"; else command stat "$@"; fi; }
'''


def build_lib(dst):
    """build.sh's definitions without its PATH and its dispatch, to source."""
    text = rd(BUILD)
    text = text[:text.index("case ${1:-} in")]
    wr(dst, "\n".join(ln for ln in text.splitlines() if not ln.startswith("PATH=")) + "\n")


class BuildShParts(unittest.TestCase):
    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="t2-i4h-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.fs = os.path.join(self.t, "fs")
        self.w = os.path.join(self.t, "w")
        build_lib(os.path.join(self.t, "lib.sh"))
        wr(os.path.join(self.t, "fakes.sh"), FAKES)
        wr(os.path.join(self.fs, "system", "packages", PKG), "pkg\n")
        for n in ("rustic-bko", "bko-holt"):
            wr(os.path.join(self.fs, BINDIR[1:], n), f"old {n}\n", 0o755)
        # (0644, not the image's 0444: Haiku's builder writes as uid 0, these tests don't.)
        wr(os.path.join(self.fs, ".disk", "backotter.json"), '{"image_id": "old"}\n', 0o644)
        wr(os.path.join(self.fs, "trash", ".keep"), "")
        wr(os.path.join(self.w, "in", "refresh-lib.sh"), rd(os.path.join(STICK, "bsd", "refresh-lib.sh")))
        for n in ("rustic-bko", "bko-holt"):
            wr(os.path.join(self.w, "in", n), f"new {n}, longer\n", 0o755)
        self.marker('"kernel": "hrev59866+101"')
        wr(os.path.join(self.w, "out", "image.img"), "parent\n")
        self.checkfs = os.path.join(self.t, "checkfs.out")
        wr(self.checkfs, CLEAN)
        self.log = os.path.join(self.t, "log")

    def marker(self, kernel):
        wr(os.path.join(self.w, "in", "backotter.json"), '{\n  "image_id": "new",\n  ' + kernel + ',\n  "x": 1\n}\n')

    def run_sub(self, sub, free=150000):
        script = f'. "{self.t}/lib.sh"; set +u; . "{self.t}/fakes.sh"; {sub}'
        e = dict(os.environ, FS=self.fs, LOG=self.log, CHECKFS_OUT=self.checkfs, FREE_BLOCKS=str(free))
        return subprocess.run([T.BASH, "-c", script], cwd=self.w, env=e, capture_output=True, text=True)

    def bin(self, n):
        return os.path.join(self.fs, BINDIR[1:], n)

    def test_build_sh_has_the_subcommands_and_draws_no_gpt_ids(self):
        self.assertEqual(subprocess.run([T.BASH, "-n", BUILD]).returncode, 0)
        text = rd(BUILD)
        for c in ("list", "room", "refresh"):
            self.assertRegex(text, rf"(?m)^{c}\) {c} ;;$")
        self.assertNotIn("GPT_IDS", rd(os.path.join(STICK, "haiku", "platform.conf")))
        # The binaries' directory is the swap list's.
        bins = {r["path"] for r in II.swap_rows("haiku", REPO) if r["kind"] == "bin"}
        self.assertEqual(bins, {f"{BINDIR}/rustic-bko", f"{BINDIR}/bko-holt"})
        self.assertIn(f"BINDIR={BINDIR}\n", text)

    def test_the_swap_keeps_each_inode_and_checks_read_only_before_and_after(self):
        ino = {n: os.stat(self.bin(n)).st_ino for n in ("rustic-bko", "bko-holt")}
        r = self.run_sub("refresh")
        self.assertEqual(r.returncode, 0, r.stderr)
        for n in ("rustic-bko", "bko-holt"):
            self.assertEqual(rd(self.bin(n)), f"new {n}, longer\n")
            self.assertEqual(os.stat(self.bin(n)).st_ino, ino[n])
            self.assertEqual(os.stat(self.bin(n)).st_mode & 0o777, 0o755)
        self.assertIn('"image_id": "new"', rd(os.path.join(self.fs, ".disk", "backotter.json")))
        self.assertTrue(os.path.isdir(os.path.join(self.fs, "trash")))
        # checkfs only of a read-only mount; the one read-write mount between the two checks.
        self.assertEqual(rd(self.log).split("\n"), [
            "mount -ro", "checkfs -c ck", "unmount", "mount -t", "unmount", "mount -ro", "checkfs -c ck",
            "unmount", ""])
        self.assertRegex(r.stdout, r"fsck before: +386 nodes checked, 0 blocks not allocated")
        self.assertIn("kernel: hrev59866+101, as the parent's marker says", r.stdout)
        self.assertIn("fsck after:", r.stdout)

    def test_a_kernel_that_isnt_the_parents_package_is_refused(self):
        self.marker('"kernel": "hrev59866+79"')
        r = self.run_sub("refresh")
        self.assertEqual(r.returncode, 1)
        self.assertIn("the parent's haiku package is hrev59866+101, its marker says 'hrev59866+79'", r.stderr)
        self.assertEqual(rd(self.bin("rustic-bko")), "old rustic-bko\n")

    def test_binaries_that_dont_fit_exit_3_and_change_nothing(self):
        wr(os.path.join(self.w, "in", "rustic-bko"), "x" * (4 << 20), 0o755)
        r = self.run_sub("refresh", free=100)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("build it in full", r.stderr)
        self.assertEqual(rd(self.bin("rustic-bko")), "old rustic-bko\n")
        self.assertIn('"old"', rd(os.path.join(self.fs, ".disk", "backotter.json")))

    def test_a_checkfs_that_finds_something_or_checks_nothing_is_refused(self):
        for out in (CLEAN.replace("0 blocks could be freed", "3 blocks could be freed"), "checkfs: no such volume\n"):
            wr(self.checkfs, out)
            r = self.run_sub("refresh")
            self.assertEqual(r.returncode, 1)
            self.assertIn("checkfs before the swap: the BFS is not clean", r.stderr)
            self.assertEqual(rd(self.bin("rustic-bko")), "old rustic-bko\n")

    def test_room_line(self):
        r = self.run_sub("room", free=150000)
        self.assertEqual(r.returncode, 0, r.stderr)
        # free and total from stat -f's 2048-byte blocks; the binaries' blocks as du gives them.
        self.assertRegex(r.stdout, r"^BKO-ROOM free_kib=300000 total_kib=300000 bins_kib=\d+\n$")
        self.assertEqual(rd(self.log).split("\n")[0], "mount -ro")


def mbr_disk(path, parts, total=4096):
    """A disk like Haiku's: an MBR of PARTS [(type, first, sectors, active, bytes)], no GPT."""
    img = bytearray(total * 512)
    for i, (typ, first, n, active, data) in enumerate(parts):
        img[446 + 16 * i:462 + 16 * i] = struct.pack("<B3sB3sII", 0x80 if active else 0, b"\xfe\xff\xff", typ,
                                                     b"\xfe\xff\xff", first, n)
        img[first * 512:first * 512 + len(data)] = data
    img[510:512] = b"\x55\xaa"
    with open(path, "wb") as f:
        f.write(img)


class Layout(unittest.TestCase):
    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="t2-i4hl-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.parts = [[0xEB, 64, 1024, True, b"\xebBOOT" + bytes(505) + b"\x55\xaa" + b"BFS super"],
                      [0xEF, 1088, 512, False, b"ESP"], [0x0E, 1600, 512, False, b"config"]]

    def lay(self, name):
        p = os.path.join(self.t, name)
        mbr_disk(p, self.parts)
        return II.layout(p, II.layout_rewritten("haiku", REPO), eltorito=False)

    def test_the_swap_list_names_haikus_rewritten_partitions(self):
        self.assertEqual(II.layout_rewritten("haiku", REPO), {1, 3})

    def test_the_esp_is_hashed_and_the_rewritten_partitions_are_not(self):
        a = self.lay("a.img")
        self.assertTrue(any(x.startswith("layout\t/mbr/part/2\t") and "sha256=" in x for x in a))
        # The BFS past its boot sector and the config partition: rewritten, not compared.
        self.parts[0][4] = self.parts[0][4][:512] + b"BFS super, mounted since"
        self.parts[2][4] = b"another config"
        self.assertEqual(self.lay("b.img"), a)
        self.parts[1][4] = b"another ESP"
        self.assertNotEqual(self.lay("c.img"), a)

    def test_the_bfs_boot_sector_is_kept(self):
        a = self.lay("a.img")
        self.assertTrue(any(x.startswith("layout\t/mbr/part/1/boot\t") for x in a), a)
        self.parts[0][4] = b"\xebB00T" + self.parts[0][4][5:]
        self.assertNotEqual(self.lay("b.img"), a)

    def test_the_boot_codes_second_part_is_kept_and_the_superblock_is_not(self):
        # makebootable's 1 KiB: bytes 0-511 and 676-1023, the BFS's 164-byte superblock between.
        boot = self.parts[0][4][:512]
        self.parts[0][4] = boot + b"S" * 164 + b"stage1, part 2"
        a = self.lay("a.img")
        self.parts[0][4] = boot + b"T" * 164 + b"stage1, part 2"
        self.assertEqual(self.lay("b.img"), a)
        self.parts[0][4] = boot + b"S" * 164 + b"stage1, part 3"
        self.assertNotEqual(self.lay("c.img"), a)

    def test_config_part_is_found_in_the_mbr(self):
        p = os.path.join(self.t, "a.img")
        mbr_disk(p, self.parts)
        r = subprocess.run([sys.executable, os.path.join(STICK, "img_gpt.py"), "part", p, "3"],
                           capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, "1600 512\n"), r.stderr)


class HaikuImgOne(TB.NetbsdImgOne):
    """img-one.sh --refresh-from P haiku, with the stub make-img-bsd.sh made Haiku's (its BFS tree,
    its binaries' directory, no restic-bko)."""

    def setUp(self):
        super().setUp()
        stub = (TB.STUB.replace("/netbsd/", "/haiku/").replace("/usr/local/bin/", BINDIR + "/")
                .replace("root\\t", "bfs\\t"))
        wr(os.path.join(self.w, "stick", "make-img-bsd.sh"), stub, 0o755)
        build = os.path.join(self.w, "stick", "haiku", "build.sh")
        if "refresh)" not in (rd(build) if os.path.exists(build) else ""):
            wr(build, rd(BUILD))
        os.remove(os.path.join(self.w, "bko-bin", "haiku", "restic-bko"))
        T.set_generation(self.w, "haiku6", "cd" * 32)
        wr(os.path.join(self.w, "img-media", "haiku", "haiku.iso"), "iso\n")
        wr(os.path.join(self.w, "img-media", "haiku", "MEDIA"), "haiku.iso\n")

    def d(self, *p):
        return os.path.join(self.i, "haiku", *p)

    def full(self):
        r = self.one("haiku")
        self.assertEqual(r.returncode, 0, r.stderr + rd(self.d("result.txt")))
        return self.prov("haiku")

    def new_bins(self, big=False):
        wr(os.path.join(self.w, "bko-bin", "haiku", "rustic-bko"), "x" * (400 << 20) if big else "new rustic\n", 0o755)
        wr(os.path.join(self.w, "holt-bin", "haiku", "bko-holt"), "new holt\n", 0o755)

    def test_a_refresh_of_haiku(self):
        a = self.full()
        self.new_bins()
        r = self.one("--refresh-from", a["test"]["sha256"], "haiku")
        res = rd(self.d("result.txt"))
        self.assertEqual(r.returncode, 0, r.stderr + res)
        p = self.prov("haiku")
        self.assertEqual((p["mode"], p["parent"]["test_sha256"]), ("refresh", a["test"]["sha256"]))
        self.assertNotEqual(p["test"]["image_id"], a["test"]["image_id"])
        self.assertIn("refresh check: release:", res)
        tree = rd(os.path.join(self.d(), "os-tree.txt"))
        self.assertIn(f"bfs\t{BINDIR}/rustic-bko\t", tree)
        self.assertNotIn("restic-bko", tree)

    def test_an_extra_change_is_refused_as_c2(self):
        a = self.full()
        self.new_bins()
        r = self.one("--refresh-from", a["test"]["sha256"], "haiku", EXTRA="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("/etc/extra", rd(self.d("refresh-check.txt")))

    def test_binaries_that_dont_fit_by_the_record_refuse_before_anything_moves(self):
        a = self.full()
        self.new_bins(big=True)
        before = sorted(os.listdir(self.d()))
        r = self.one("--refresh-from", a["test"]["sha256"], "haiku")
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn("build it in full", r.stderr)
        self.assertEqual(sorted(os.listdir(self.d())), before)

    def test_binaries_that_dont_fit_in_the_vm_exit_6(self):
        a = self.full()
        self.new_bins()
        r = self.one("--refresh-from", a["test"]["sha256"], "haiku", NOFIT="1")
        self.assertEqual(r.returncode, 6, r.stderr)
        self.assertEqual(rd(self.d("done.exit")), "1\n")


for _n in [n for n in dir(TB.NetbsdImgOne) if n.startswith("test")]:
    if _n not in HaikuImgOne.__dict__:
        setattr(HaikuImgOne, _n, None)


class Planner(unittest.TestCase):
    def test_haiku_is_refresh_ready(self):
        sys.path.insert(0, STICK)
        import img_round_facts as F
        self.assertTrue(F.refresh_ready(STICK)["haiku"])

    def test_haikus_binaries_fit_without_a_restic(self):
        sys.path.insert(0, STICK)
        import img_round as R
        pv = {"room": {w: {"free_kib": 311548, "bins_kib": 36888} for w in ("release", "test")}}
        self.assertIsNone(R.room_why("haiku", pv, {"rustic-bko": 33248264, "bko-holt": 4518664}))
        self.assertRegex(R.room_why("haiku", pv, {"rustic-bko": 400 << 20, "bko-holt": 4518664}),
                         r"the new binaries need \d+ KiB, and the parent's release image has 348436")
        self.assertIn("records no room", R.room_why("haiku", {}, {"rustic-bko": 1}))


if __name__ == "__main__":
    unittest.main()
