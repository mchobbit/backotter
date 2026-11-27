"""I4's unit tests for OpenBSD (docs/design/img-speed.md §4.2, D1, D2, §14 row I4): stick/openbsd/build.sh's
list, room and refresh, the swap list's id sites and rewritten slices, and the dealer's refresh-ready.

build.sh refresh runs here with sh in a fake OpenBSD: vnconfig, disklabel, fsck_ffs, mount, umount and df
are stubs over a plain directory (the parent's root) and a label file, and the script's own PATH line is
dropped so that they are found.  What is checked is the script's logic: fsck before and after, the
kernel and the fstab held to the parent's marker and label, the fit (exit 3, nothing changed), the
in-place swap, and the new DUID in the disklabel (its other lines as they were) and in /etc/fstab (the
same inode).  The VM's own part (vnd, the real fsck_ffs and disklabel -E) and E1 run on the farm.

No host, no VM.  Run from the worktree: python3 -m unittest discover testvm/test2/tests.
"""

import os
import re
import shutil
import struct
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
import sys  # noqa: E402
sys.path.insert(0, HERE)
import test_img_inputs as T  # noqa: E402  (its fixture world)

II = T.II
rd, wr = T.rd, T.wr
STICK = T.STICK
BUILD = os.path.join(STICK, "openbsd", "build.sh")
LIB = os.path.join(STICK, "bsd", "refresh-lib.sh")

OLD = "0123456789abcdef"
LABEL = f"""# /dev/rvnd0c:
type: vnd
disk: vnd device
label: fictitious
duid: {OLD}
flags:
bytes/sector: 512
total sectors: 2000000
boundstart: 559104
boundend: 2000000

16 partitions:
#                size           offset  fstype [fsize bsize   cpg]
  a:          1440896           559104  4.2BSD   2048 16384 12960
  c:          2000000                0  unused
  i:            32768             2048   MSDOS
  j:           524288            34816   MSDOS
"""

# The stubs: $FAKE holds root/ (the parent's root file system), label (its disklabel) and log.
STUBS = {
    "vnconfig": '#!/bin/sh\necho "vnconfig $*" >>"$FAKE/log"\n[ "$1" = -u ] || echo vnd0\n',
    # disklabel -E reads the editor's commands: i, the DUID, w, q.
    "disklabel": '#!/bin/sh\necho "disklabel $*" >>"$FAKE/log"\n'
                 'if [ "$1" = -E ]; then read c; read d; read w; read q\n'
                 '  [ "$c $w $q" = "i w q" ] || exit 1\n'
                 '  sed "s/^duid: .*/duid: $d/" "$FAKE/label" >"$FAKE/label.new" && mv "$FAKE/label.new" "$FAKE/label"\n'
                 '  [ -z "$LABEL_DRIFT" ] || echo "flags: vendor" >>"$FAKE/label"\n'
                 'else cat "$FAKE/label"; fi\n',
    "fsck_ffs": '#!/bin/sh\necho "fsck_ffs $*" >>"$FAKE/log"\n'
                'if [ -n "$FSCK_BAD" ]; then echo "UNREF FILE I=5  OWNER=root MODE=100644"; echo "CLEAR? no"; fi\n'
                'echo "100 files, 4000 used, ${FREE:-90000} free (0 frags, 0 blocks, 0.0% fragmentation)"\n',
    "mount": '#!/bin/sh\necho "mount $*" >>"$FAKE/log"\nfor d; do :; done\nrmdir "$d" && ln -s "$FAKE/root" "$d"\n',
    "umount": '#!/bin/sh\necho "umount $*" >>"$FAKE/log"\n[ -L "$1" ] || exit 1\nrm "$1" && mkdir "$1"\n',
    "df": '#!/bin/sh\nfor d; do :; done\necho "Filesystem  1K-blocks      Used     Avail Capacity  Mounted on"\n'
          'echo "/dev/vnd0a     720000 $((720000 - ${FREE:-90000})) ${FREE:-90000}    88%    $d"\n',
}


class Build(unittest.TestCase):
    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="t2-i4o-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.fake = os.path.join(self.t, "fake")
        self.bin = os.path.join(self.t, "bin")
        for n, s in STUBS.items():
            wr(os.path.join(self.bin, n), s, 0o755)
        r = os.path.join(self.fake, "root")
        wr(os.path.join(r, "bsd"), "\0\0OpenBSD 8.0 (GENERIC.MP) #84: Mon Nov  9 2026\n\0")
        wr(os.path.join(r, "etc", "fstab"), f"{OLD}.a / ffs ro 0 0\n")
        wr(os.path.join(r, "usr", "local", "bin", "restic-bko"), "old restic\n", 0o755)
        wr(os.path.join(r, "usr", "local", "bin", "rustic-bko"), "old rustic\n", 0o755)
        wr(os.path.join(r, "usr", "local", "bin", "bko-holt"), "old holt\n", 0o755)
        wr(os.path.join(r, ".disk", "backotter.json"), '{"image_id": "old"}\n', 0o644)
        wr(os.path.join(self.fake, "label"), LABEL)
        wr(os.path.join(self.fake, "log"), "")
        # The build directory make-img-bsd.sh makes: in/ and out/image.img, the parent.
        self.d = os.path.join(self.t, "build")
        script = re.sub(r"(?m)^PATH=/sbin:/bin:/usr/sbin:/usr/bin$", ": PATH kept", rd(BUILD), count=1)
        self.assertNotEqual(script, rd(BUILD))
        wr(os.path.join(self.d, "in", "build.sh"), script)
        shutil.copy(LIB, os.path.join(self.d, "in", "refresh-lib.sh"))
        wr(os.path.join(self.d, "in", "restic-bko"), "new restic\n", 0o755)
        wr(os.path.join(self.d, "in", "bko-holt"), "new holt\n", 0o755)
        wr(os.path.join(self.d, "in", "backotter.json"),
           '{"format": "backotter-image", "image_id": "new", "kernel": "8.0 GENERIC.MP#84"}\n')
        wr(os.path.join(self.d, "out", "image.img"), "parent")

    def run_(self, cmd, **env):
        e = dict(os.environ, FAKE=self.fake, PATH=self.bin + ":" + os.environ["PATH"], FREE="", FSCK_BAD="",
                 LABEL_DRIFT="")
        e.update(env)
        return subprocess.run(["sh", "in/build.sh", cmd], cwd=self.d, env=e, capture_output=True, text=True)

    def root(self, *p):
        return os.path.join(self.fake, "root", *p)

    def duid(self):
        return re.search(r"(?m)^duid: (\S+)$", rd(os.path.join(self.fake, "label"))).group(1)

    def test_the_syntax_and_the_subcommands(self):
        self.assertEqual(subprocess.run(["sh", "-n", BUILD]).returncode, 0)
        text = rd(BUILD)
        for c in ("list", "room", "refresh"):
            self.assertRegex(text, rf"(?m)^{c}\) {c}; exit 0 ;;$")
        # Before the media: a refresh has none.
        self.assertLess(text.index("refresh) refresh; exit 0 ;;"), text.index("set -- in/base"))

    def test_a_refresh_swaps_in_place_and_draws_a_new_duid(self):
        fstab_ino = os.stat(self.root("etc", "fstab")).st_ino
        restic_ino = os.stat(self.root("usr", "local", "bin", "restic-bko")).st_ino
        r = self.run_("refresh")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        new = self.duid()
        self.assertRegex(new, r"^[0-9a-f]{16}$")
        self.assertNotEqual(new, OLD)
        self.assertEqual(rd(self.root("etc", "fstab")), f"{new}.a / ffs ro 0 0\n")
        self.assertEqual(os.stat(self.root("etc", "fstab")).st_ino, fstab_ino)
        # The disklabel's other lines as they were.
        self.assertEqual(rd(os.path.join(self.fake, "label")).replace(new, OLD), LABEL)
        self.assertEqual(rd(self.root("usr", "local", "bin", "restic-bko")), "new restic\n")
        self.assertEqual(os.stat(self.root("usr", "local", "bin", "restic-bko")).st_ino, restic_ino)
        self.assertFalse(os.path.exists(self.root("usr", "local", "bin", "rustic-bko")))   # the set has none
        self.assertIn('"image_id": "new"', rd(self.root(".disk", "backotter.json")))
        self.assertIn(f"disklabel: DUID {OLD} -> {new}, the rest as it was", r.stdout)
        log = rd(os.path.join(self.fake, "log")).splitlines()
        # fsck before the mount and after the label; the label written with the root unmounted.
        fs = [i for i, x in enumerate(log) if x.startswith("fsck_ffs -n -f /dev/rvnd0a")]
        mnt = log.index("mount /dev/vnd0a mnt")
        um = log.index("umount mnt")
        lab = log.index("disklabel -E vnd0")
        self.assertEqual(len(fs), 2)
        self.assertLess(fs[0], mnt)
        self.assertLess(um, lab)
        self.assertLess(lab, fs[1])
        self.assertEqual(log[-1], "vnconfig -u vnd0")

    def test_a_binary_the_parent_lacks_is_installed_0755_as_assemble_does(self):
        os.remove(self.root("usr", "local", "bin", "rustic-bko"))
        wr(os.path.join(self.d, "in", "rustic-bko"), "new rustic\n", 0o700)
        r = self.run_("refresh")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.stat(self.root("usr", "local", "bin", "rustic-bko")).st_mode & 0o7777, 0o755)
        self.assertIn("install -m 755 in/restic-bko", rd(BUILD))

    def test_binaries_that_dont_fit_exit_3_and_change_nothing(self):
        wr(os.path.join(self.d, "in", "restic-bko"), "x" * (4 << 20), 0o755)
        r = self.run_("refresh", FREE="1000")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("build it in full", r.stderr)
        self.assertEqual(self.duid(), OLD)
        self.assertEqual(rd(self.root("etc", "fstab")), f"{OLD}.a / ffs ro 0 0\n")
        self.assertEqual(rd(self.root("usr", "local", "bin", "restic-bko")), "old restic\n")
        self.assertTrue(os.path.isdir(os.path.join(self.d, "mnt")) and not os.path.islink(os.path.join(self.d, "mnt")))

    def test_a_kernel_not_the_markers_is_refused(self):
        wr(os.path.join(self.d, "in", "backotter.json"), '{"kernel": "8.0 GENERIC.MP#85"}\n')
        r = self.run_("refresh")
        self.assertEqual(r.returncode, 1)
        self.assertIn("its marker says '8.0 GENERIC.MP#85'", r.stderr)
        self.assertEqual(self.duid(), OLD)

    def test_an_fstab_not_by_the_labels_duid_is_refused(self):
        wr(os.path.join(self.fake, "root", "etc", "fstab"), "/dev/sd0a / ffs ro 0 0\n")
        r = self.run_("refresh")
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"doesn't mount the root by its DUID {OLD}", r.stderr)

    def test_an_unclean_root_is_refused_before_anything_changes(self):
        r = self.run_("refresh", FSCK_BAD="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("fsck_ffs before the swap: the root is not clean", r.stderr)
        self.assertEqual(rd(self.root("usr", "local", "bin", "restic-bko")), "old restic\n")

    def test_a_label_that_changes_beyond_its_duid_is_refused(self):
        r = self.run_("refresh", LABEL_DRIFT="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("the label editor changed more than the DUID", r.stderr)

    def test_room(self):
        r = self.run_("room", FREE="88000")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"BKO-ROOM free_kib=88000 total_kib=720000 bins_kib=\d+")
        self.assertIn("mount -r /dev/vnd0a rmt/root", rd(os.path.join(self.fake, "log")))


def openbsd_disk(path, total=8192):
    """An image as OpenBSD's assemble lays it out (in small): slot 0 the ESP (EF), slot 1 the config
    partition (0E), slot 3 OpenBSD's (A6, active), whose sector 0 is biosboot and sector 1 the
    disklabel."""
    img = bytearray(total * 512)
    for slot, status, typ, first, n in ((0, 0, 0xEF, 64, 64), (1, 0, 0x0E, 128, 256), (3, 0x80, 0xA6, 384, total - 384)):
        img[446 + 16 * slot:462 + 16 * slot] = bytes([status, 0, 0, 0, typ, 0, 0, 0]) + struct.pack("<II", first, n)
    img[510:512] = b"\x55\xaa"
    img[64 * 512:64 * 512 + 3] = b"esp"
    img[128 * 512:128 * 512 + 3] = b"cfg"
    img[384 * 512:384 * 512 + 8] = b"biosboot"
    img[385 * 512:385 * 512 + 13] = b"label " + OLD[:7].encode()
    img[400 * 512:400 * 512 + 4] = b"root"
    with open(path, "wb") as f:
        f.write(img)


def poke(path, sector, data):
    with open(path, "r+b") as f:
        f.seek(sector * 512)
        f.write(data)


class Layout(unittest.TestCase):
    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="t2-i4ol-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.img = os.path.join(self.t, "d.img")
        openbsd_disk(self.img)
        self.lay = II.layout(self.img, {2, 4}, eltorito=False)

    def test_the_esp_is_hashed_and_the_rewritten_slots_are_not(self):
        lines = {x.split("\t")[1]: x.split("\t")[2] for x in self.lay}
        self.assertRegex(lines["/mbr/part/1"], r"^status=00 type=ef size=32768 sha256=[0-9a-f]{64}$")
        self.assertEqual(lines["/mbr/part/2"], "status=00 type=0e rewritten")
        self.assertEqual(lines["/mbr/part/4"], "status=80 type=a6 rewritten")
        self.assertIn("/mbr/part/4/boot", lines)

    def test_a_new_label_and_root_and_config_leave_the_layout_and_biosboot_does_not(self):
        poke(self.img, 385, b"label fedcba9")     # the DUID
        poke(self.img, 400, b"ROOT")              # the root file system
        poke(self.img, 128, b"CFG")               # the config partition
        self.assertEqual(II.layout(self.img, {2, 4}, eltorito=False), self.lay)
        poke(self.img, 384, b"BIOSBOOT")
        self.assertNotEqual(II.layout(self.img, {2, 4}, eltorito=False), self.lay)

    def test_a_changed_esp_moves_the_layout(self):
        poke(self.img, 64, b"ESP")
        self.assertNotEqual(II.layout(self.img, {2, 4}, eltorito=False), self.lay)

    def test_the_config_partition_is_the_mbrs_slot_1(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("img_gpt_o", os.path.join(STICK, "img_gpt.py"))
        G = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(G)
        self.assertIn("CONFIG_PART=2", rd(os.path.join(STICK, "openbsd", "platform.conf")))
        self.assertEqual(G.part(self.img, 2), (128, 256))


class SwapList(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("S")
        os.environ["S"] = STICK
        self.addCleanup(lambda: os.environ.pop("S") if self.old is None else os.environ.update(S=self.old))

    def test_openbsds_rewritten_slices_and_id_sites(self):
        # MBR slot 1 (the config partition) and slot 3 (OpenBSD's, whose disklabel holds the DUID).
        self.assertEqual(II.layout_rewritten("openbsd"), {2, 4})
        rows = {(r["tree"], r["path"]): r["kind"] for r in II.swap_rows("openbsd")}
        self.assertEqual(rows[("root", "/etc/fstab")], "id")
        self.assertEqual(rows[("root", "/.disk/backotter.json")], "marker")

    def test_the_dealer_treats_openbsd_as_refresh_ready(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("img_round_facts_t", os.path.join(STICK, "img_round_facts.py"))
        F = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(F)
        self.assertTrue(F.refresh_ready(STICK)["openbsd"])


if __name__ == "__main__":
    unittest.main()
