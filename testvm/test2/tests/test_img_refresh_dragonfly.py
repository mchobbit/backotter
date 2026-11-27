"""I4's unit tests for DragonFly (docs/design/img-speed.md §4.2, D1, D2, §14 row I4): its build.sh
refresh's own parts and its place in the shared VM refresh.

stick/dragonfly/build.sh's functions (its fsck check, the id sites rewritten in place, the disklabel64
relabel with a new disk id and storage UUID) run here with sh against fakes of fsck, disklabel64 and
uuidgen; refresh-lib.sh's BKO_BIN_MODE; the MBR layout's slice 2 (its boot code kept, its label masked)
and the swap list's rewritten slices; img-one.sh --refresh-from dragonfly with test_img_refresh_bsd's
stub make-img-bsd.sh; the planner taking dragonfly as refresh-ready.  The VM's own part (vn, fsck,
disklabel64) and E1 run on the farm.

No host, no VM.  Run from the worktree: python3 -m unittest discover testvm/test2/tests.
"""

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
import test_img_refresh_bsd as TB  # noqa: E402  (refresh-lib.sh, the stub make-img-bsd.sh)

II = T.II
rd, wr = T.rd, T.wr
STICK = T.STICK
BUILD = os.path.join(STICK, "dragonfly", "build.sh")
OLD = "BKO_2faa250ece3b40aca6c378a2c446db15"
NEW = "BKO_11111111222233334444555555555555"

LABEL = f"""# /dev/vn1s2:
diskid: b36e47a0-e9c3-11f1-a1bb-010000000000
label: {OLD}
boot2 data base:      0x000000001000
16 partitions:
#          size     offset    fstype   fsuuid
  a:    1183744          0    4.2BSD	#    1156.000MB
  a-stor_uuid: b36f806b-e9c3-11f1-a1bb-010000000000
"""

CLEAN = """25743 files, 422125 used, 160498 free (242 frags, 20032 blocks, 0.0% fragmentation)
** /dev/vn1s2a (NO WRITE)
** Last Mounted on /var/tmp/x/dst
** Phase 1 - Check Blocks and Sizes
** Phase 2 - Check Pathnames
** Phase 3 - Check Connectivity
** Phase 4 - Check Reference Counts
** Phase 5 - Check Cyl groups
"""

FAKES = {
    "fsck": '#!/bin/sh\ncat "$FSCK_OUT"\n',
    # disklabel64 -r DEV prints $LBL; -R -r DEV FILE writes FILE to it, or ($NOIDS) with nil ids, as
    # the real one leaves a label whose id lines are gone.
    "disklabel64": '#!/bin/sh\nif [ "$1" = -R ]; then\n  if [ -n "$NOIDS" ]; then '
                   'sed -E "s/(diskid|stor_uuid): .*/\\1: 00000000-0000-0000-0000-000000000000/" "$4" >"$LBL"; '
                   'else cat "$4" >"$LBL"; fi\nelse cat "$LBL"; fi\n',
    "uuidgen": '#!/bin/sh\npython3 -c "import uuid; print(uuid.uuid1())"\n',
}


def build_lib(dst):
    """build.sh's definitions without its PATH and its dispatch, to source."""
    text = rd(BUILD)
    text = text[:text.index("case ${1:-} in")]
    wr(dst, "\n".join(ln for ln in text.splitlines() if not ln.startswith("PATH=")) + "\n")


class BuildShParts(unittest.TestCase):
    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="t2-i4d-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.bin = os.path.join(self.t, "bin")
        for n, s in FAKES.items():
            wr(os.path.join(self.bin, n), s, 0o755)
        self.lib = os.path.join(self.t, "lib.sh")
        build_lib(self.lib)
        self.lbl = os.path.join(self.t, "label")
        wr(self.lbl, LABEL)
        self.fsck = os.path.join(self.t, "fsck.txt")
        wr(self.fsck, CLEAN)

    def sh(self, call, **env):
        e = dict(PATH=self.bin + ":" + os.environ["PATH"], LBL=self.lbl, FSCK_OUT=self.fsck, NOIDS="")
        e.update(env)
        return TB.sh(f". {self.lib}; {call}", self.t, **e)

    def test_build_sh_has_the_subcommands_and_draws_no_gpt_ids(self):
        self.assertEqual(subprocess.run(["sh", "-n", BUILD]).returncode, 0)
        text = rd(BUILD)
        for c in ("list", "room", "refresh"):
            self.assertRegex(text, rf"(?m)^{c}\) {c} ;;$")
        self.assertIn("BKO_BIN_MODE=755 bko_swap", text)
        self.assertNotIn("GPT_IDS", rd(os.path.join(STICK, "dragonfly", "platform.conf")))

    def test_a_clean_fsck_passes(self):
        r = self.sh("fsck_clean vn1 before")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "fsck before: 25743 files, 422125 used, 160498 free\n")

    def test_an_fsck_that_finds_something_or_checks_nothing_fails(self):
        for out in (CLEAN.replace("** Phase 5", "UNREF FILE I=12  OWNER=root MODE=100644\nCLEAR? no\n\n** Phase 5"),
                    "Can't stat /dev/vn1s2a: No such file or directory\n"):
            wr(self.fsck, out)
            r = self.sh("fsck_clean vn1 after")
            self.assertEqual(r.returncode, 1, out)
            self.assertIn("fsck after the swap: the root is not clean", r.stderr)

    def test_an_id_site_is_rewritten_in_place(self):
        f = os.path.join(self.t, "mnt", "etc", "fstab")
        wr(f, f"/dev/part-by-label/{OLD}.a\t/\tufs\tro,noatime\t0\t0\ntmpfs\t/tmp\ttmpfs\trw\t0\t0\n", 0o644)
        ino = os.stat(f).st_ino
        r = self.sh(f"id_site mnt/etc/fstab {OLD} {NEW}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(rd(f), f"/dev/part-by-label/{NEW}.a\t/\tufs\tro,noatime\t0\t0\ntmpfs\t/tmp\ttmpfs\trw\t0\t0\n")
        self.assertEqual((os.stat(f).st_ino, os.stat(f).st_mode & 0o777), (ino, 0o644))
        self.assertIn(f"id: etc/fstab names {NEW}", r.stdout)

    def test_an_id_site_that_doesnt_name_the_parents_label_is_refused(self):
        f = os.path.join(self.t, "mnt", "boot", "loader.conf")
        wr(f, 'vfs.root.mountfrom="ufs:part-by-label/BKO_ffff.a"\n')
        r = self.sh(f"id_site mnt/boot/loader.conf {OLD} {NEW}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("doesn't name the parent's label", r.stderr)
        self.assertEqual(rd(f), 'vfs.root.mountfrom="ufs:part-by-label/BKO_ffff.a"\n')

    def test_the_relabel_draws_a_new_disk_id_and_storage_uuid(self):
        r = self.sh(f"relabel vn1 {NEW}")
        self.assertEqual(r.returncode, 0, r.stderr)
        new = rd(self.lbl)
        self.assertIn(f"label: {NEW}\n", new)
        old_ids = [ln for ln in LABEL.splitlines() if "diskid" in ln or "stor_uuid" in ln]
        new_ids = [ln for ln in new.splitlines() if "diskid" in ln or "stor_uuid" in ln]
        self.assertEqual(len(new_ids), 2)
        self.assertFalse(set(old_ids) & set(new_ids))
        # Nothing else in the label moved.
        strip = lambda s: [ln for ln in s.splitlines() if not ln.startswith(("diskid", "label", "  a-stor_uuid"))]
        self.assertEqual(strip(new), strip(LABEL))

    def test_a_relabel_that_keeps_no_ids_is_refused(self):
        r = self.sh(f"relabel vn1 {NEW}", NOIDS="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("disklabel64 -R didn't take the new", r.stderr)

    def test_a_label_with_nothing_to_rewrite_is_refused(self):
        wr(self.lbl, LABEL.replace("  a-stor_uuid: b36f806b-e9c3-11f1-a1bb-010000000000\n", ""))
        r = self.sh(f"relabel vn1 {NEW}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no label, diskid or a-stor_uuid line", r.stderr)


class BinMode(TB.RefreshLib):
    """refresh-lib.sh's BKO_BIN_MODE: DragonFly installs its binaries 0755."""

    def test_a_new_file_takes_bko_bin_mode(self):
        b = os.path.join(self.root, "usr/local/bin")
        os.unlink(os.path.join(b, "bko-holt"))
        r = self.lib("BKO_BIN_MODE=755 bko_swap $PWD/root /usr/local/bin restic-bko rustic-bko bko-holt")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.stat(os.path.join(b, "bko-holt")).st_mode & 0o777, 0o755)


for _n in [n for n in dir(TB.RefreshLib) if n.startswith("test")]:
    if _n not in BinMode.__dict__:
        setattr(BinMode, _n, None)


def mbr_disk(path, total=8192, boot2=b"boot2", label=b"label"):
    """An MBR disk as DragonFly's image lays it out: slice 1 an ESP (0xEF), slice 2 a disklabel64
    slice (0x6C, active: boot1 in its sector 0, the label in sector 1, boot2 from 0x1000), slice 3
    the config partition (0x0E)."""
    img = bytearray(total * 512)
    img[0:4] = b"MBR!"
    for i, (st, typ, first, n) in enumerate(((0, 0xEF, 63, 1985), (0x80, 0x6C, 2048, 4096), (0, 0x0E, 6144, 2048))):
        img[446 + 16 * i:462 + 16 * i] = bytes([st, 0, 0, 0, typ, 0, 0, 0]) + struct.pack("<II", first, n)
    img[510:512] = b"\x55\xaa"
    img[63 * 512:63 * 512 + 3] = b"esp"
    img[2048 * 512:2048 * 512 + 5] = b"boot1"
    img[2049 * 512:2049 * 512 + len(label)] = label
    img[2048 * 512 + 0x1000:2048 * 512 + 0x1000 + len(boot2)] = boot2
    img[(2048 + 2048) * 512:(2048 + 2048) * 512 + 4] = b"root"
    img[6144 * 512:6144 * 512 + 3] = b"cfg"
    with open(path, "wb") as f:
        f.write(img)


class Layout(unittest.TestCase):
    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="t2-i4dl-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.old = os.environ.get("S")
        os.environ["S"] = STICK
        self.addCleanup(lambda: os.environ.pop("S") if self.old is None else os.environ.update(S=self.old))

    def lay(self, **kw):
        p = os.path.join(self.t, "d.img")
        mbr_disk(p, **kw)
        return II.layout(p, II.layout_rewritten("dragonfly"), eltorito=False)

    def test_the_swap_list_names_dragonflys_rewritten_slices(self):
        self.assertEqual(II.layout_rewritten("dragonfly"), {2, 3})

    def test_slice_2s_label_is_masked_and_its_boot_code_kept(self):
        a = self.lay()
        self.assertTrue(any(x.startswith("layout\t/mbr/part/1\t") and "sha256=" in x for x in a))
        self.assertTrue(any(x.startswith("layout\t/mbr/part/2/boot\t") for x in a))
        self.assertEqual(self.lay(label=b"another label"), a)        # a relabel, the root and config aside
        self.assertNotEqual(self.lay(boot2=b"BOOT2"), a)             # but never boot2


class DragonflyImgOne(TB.NetbsdImgOne):
    """img-one.sh --refresh-from P dragonfly, with the stub make-img-bsd.sh (made dragonfly's)."""

    def setUp(self):
        super().setUp()
        wr(os.path.join(self.w, "stick", "make-img-bsd.sh"), TB.STUB.replace("/netbsd/", "/dragonfly/"), 0o755)
        build = os.path.join(self.w, "stick", "dragonfly", "build.sh")
        if "refresh)" not in (rd(build) if os.path.exists(build) else ""):
            wr(build, rd(BUILD))

    def d(self, *p):
        return os.path.join(self.i, "dragonfly", *p)

    def full(self):
        r = self.one("dragonfly")
        self.assertEqual(r.returncode, 0, r.stderr + rd(self.d("result.txt")))
        return self.prov("dragonfly")

    def new_bins(self, big=False):
        wr(os.path.join(self.w, "bko-bin", "dragonfly", "restic-bko"), "x" * (200 << 20) if big else "new restic\n", 0o755)
        wr(os.path.join(self.w, "holt-bin", "dragonfly", "bko-holt"), "new holt\n", 0o755)

    def test_a_refresh_of_dragonfly(self):
        a = self.full()
        self.new_bins()
        r = self.one("--refresh-from", a["test"]["sha256"], "dragonfly")
        res = rd(self.d("result.txt"))
        self.assertEqual(r.returncode, 0, r.stderr + res)
        p = self.prov("dragonfly")
        self.assertEqual((p["mode"], p["parent"]["test_sha256"]), ("refresh", a["test"]["sha256"]))
        self.assertNotEqual(p["test"]["image_id"], a["test"]["image_id"])
        self.assertIn("refresh check: release:", res)

    def test_binaries_that_dont_fit_by_the_record_refuse_before_anything_moves(self):
        a = self.full()
        self.new_bins(big=True)
        r = self.one("--refresh-from", a["test"]["sha256"], "dragonfly")
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn("build it in full", r.stderr)

    def test_binaries_that_dont_fit_in_the_vm_exit_6(self):
        a = self.full()
        self.new_bins()
        r = self.one("--refresh-from", a["test"]["sha256"], "dragonfly", NOFIT="1")
        self.assertEqual(r.returncode, 6, r.stderr)
        self.assertEqual(rd(self.d("done.exit")), "1\n")


for _n in [n for n in dir(TB.NetbsdImgOne) if n.startswith("test")]:
    if _n not in DragonflyImgOne.__dict__:
        setattr(DragonflyImgOne, _n, None)


class Planner(unittest.TestCase):
    def test_dragonfly_is_refresh_ready(self):
        sys.path.insert(0, STICK)
        import img_round_facts as F
        self.assertTrue(F.refresh_ready(STICK)["dragonfly"])


if __name__ == "__main__":
    unittest.main()
