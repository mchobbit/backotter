"""I4's unit tests for illumos (docs/design/img-speed.md §4.2, D1, D2, §14 row I4): its refresh swaps the
binaries in the boot archive, a gzipped UFS inside s2's UFS, and rewrites s2's id sites.

What runs here, with no VM: stick/illumos/build.sh's syntax, its subcommands and its headroom rule (the
function run with sh on a fake in/), the swap list's rows for illumos (the trees stick and archive, the
rewritten s2 and config partitions), stick/illumos/mkgpt.py's GPT against stick/img_gpt.py (the config
partition found, new GUIDs everywhere and nothing else moved), the planner's refresh-ready and room
check for illumos, and img-one.sh --refresh-from illumos in a fixture world whose make-img-bsd.sh is a
stub that keeps the contract (each image from its own parent, its listing first; the archive's bins and
marker, s2's marker, .volsetid, boot_archive.gz and its hash changed).  The VM's part (lofi, fsck, the
in-place swap and the archive back onto s2) and E1 run on the farm.

Run from the worktree: python3 -m unittest discover testvm/test2/tests.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_img_inputs as T  # noqa: E402  (its fixture world)
import test_img_refresh_bsd as TB  # noqa: E402  (the GPT reader, ImgOne's stub conventions)
import test_img_round as TR  # noqa: E402  (the planner)

II = T.II
G = TB.G
rd, wr = T.rd, T.wr
STICK = T.STICK
BUILD = os.path.join(STICK, "illumos", "build.sh")
MKGPT = os.path.join(STICK, "illumos", "mkgpt.py")


def function(name):
    """The text of NAME() { ... } in illumos's build.sh, to run alone."""
    m = re.search(rf"(?ms)^{name}\(\) {{\n.*?^}}\n", rd(BUILD))
    assert m, name
    return m.group(0)


class BuildSh(unittest.TestCase):
    def test_syntax_and_subcommands(self):
        self.assertEqual(subprocess.run(["sh", "-n", BUILD]).returncode, 0)
        text = rd(BUILD)
        for c in ("info", "assemble", "list", "room", "refresh"):
            self.assertRegex(text, rf"(?m)^{c}\) {c} ;;$")
        self.assertIn("GPT_IDS=random", rd(os.path.join(STICK, "illumos", "platform.conf")))
        # The refresh's steps, in order: fsck of s2, the archive's hash, fsck of the archive, the marker's
        # fields against it, the fit, the swap, fsck again, the archive back onto s2 if it fits, s2's
        # id sites, fsck of s2 again.
        body = function("refresh")
        steps = ['fsck_clean "${rdisk}s2" "the stick root" before', "boot_archive.hash is not its archive's",
                 'fsck_clean "$rba" "the boot archive" before', 'check_marker "$here/ba"', "bko_fit ",
                 "bko_swap ", 'fsck_clean "$rba" "the boot archive" after', "exit $BKO_NO_ROOM",
                 "bko_put \"$here/ba.gz\"", "digest -a sha1 ba.ufs >$a/boot_archive.hash", "mnt/.volsetid",
                 'fsck_clean "${rdisk}s2" "the stick root" after']
        at = [body.find(s) for s in steps]
        self.assertNotIn(-1, at, [s for s, i in zip(steps, at) if i < 0])
        self.assertEqual(at, sorted(at))

    def test_the_marker_is_checked_against_the_archives_kernel_zfs_and_features(self):
        body = function("check_marker")
        for want in ("platform/i86pc/kernel/amd64/unix", "etc/os-release", "kernel/fs/amd64/zfs"):
            self.assertIn(want, body)

    def headroom(self, sizes):
        t = os.path.realpath(tempfile.mkdtemp(prefix="t2-i4i-"))
        self.addCleanup(shutil.rmtree, t, True)
        for n, size in sizes.items():
            with open(os.path.join(t, n), "wb") as f:
                f.truncate(size)
        r = subprocess.run(["sh", "-c", f'BINS="restic-bko rustic-bko bko-holt"; HEADROOM_MIN_KIB=65536; in={t}\n'
                            + function("headroom") + "headroom\n"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return int(r.stdout)

    def test_headroom_is_64_mib_or_half_the_binaries(self):
        self.assertEqual(self.headroom({"restic-bko": 50839817, "rustic-bko": 58093304, "bko-holt": 5050320}), 65536)
        self.assertEqual(self.headroom({"restic-bko": 170839817, "rustic-bko": 58093304}),
                         ((170839817 + 1023) // 1024 + (58093304 + 1023) // 1024) // 2)
        self.assertEqual(self.headroom({}), 65536)

    def test_assemble_makes_both_file_systems_again_when_short(self):
        body = function("assemble")
        self.assertIn('kb=$((kb + (want - ba_free) * 5 / 4 + 4096))', body)
        self.assertIn('root_sectors=$((root_sectors + ((want - s2_free) * 5 / 4 + 4096) * 2))', body)
        self.assertLess(body.find("make_archive $kb"), body.find("gzip -9c ba.ufs"))

    def test_room_is_the_less_of_the_archives_and_s2s(self):
        body = function("room")
        self.assertIn('[ "$s2" -ge "$free" ] || free=$s2', body)
        self.assertIn('bko_room "$here/rm/archive" /usr/local/bin "$free" "$2" $BINS', body)


class SwapList(T.EnvCase):
    def setUp(self):
        super().setUp()
        os.environ["S"] = STICK

    def test_illumos_rewrites_s2_and_the_config_partition(self):
        self.assertEqual(II.layout_rewritten("illumos"), {3, 4})
        rows = {(r["tree"], r["path"]): r["kind"] for r in II.swap_rows("illumos")}
        for (tree, path), kind in {("stick", "/.volsetid"): "id", ("stick", "/.disk/backotter.json"): "marker",
                                   ("stick", "/platform/i86pc/amd64/boot_archive.gz"): "rewritten",
                                   ("stick", "/platform/i86pc/amd64/boot_archive.hash"): "rewritten",
                                   ("archive", "/.disk/backotter.json"): "marker",
                                   ("archive", "/usr/local/bin/rustic-bko"): "bin"}.items():
            self.assertEqual(rows.get((tree, path)), kind, (tree, path))


class MkgptAndImgGpt(unittest.TestCase):
    """illumos's own GPT (mkgpt.py) read by the host's img_gpt.py, as make-img-bsd.sh does after a refresh."""

    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="t2-i4ig-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.img = os.path.join(self.t, "i.img")
        r = subprocess.run([sys.executable, MKGPT, self.img, "4096", "2048"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.layout = [tuple(map(int, x.split())) for x in r.stdout.split("\n") if x]
        with open(self.img, "r+b") as f:          # something in each partition, for the layout's hashes
            for n, first, _ in self.layout:
                f.seek(first * 512)
                f.write(f"partition {n}".encode())

    def test_the_config_partition_is_partition_4(self):
        n, first, size = self.layout[3]
        self.assertEqual((n, size), (4, 2048))
        self.assertEqual(G.part(self.img, 4), (first, size))

    def test_new_ids_are_new_everywhere_and_the_rest_is_kept(self):
        before = G.ids(self.img)
        lay = II.layout(self.img, {3, 4}, eltorito=False)
        after = G.new_ids(self.img)
        self.assertEqual([n for n, _ in after], [0, 1, 2, 3, 4])
        self.assertFalse({g for _, g in before} & {g for _, g in after})
        self.assertEqual(II.layout(self.img, {3, 4}, eltorito=False), lay)
        self.assertTrue(any(x.startswith("layout\t/gpt/part/2\t") and "sha256=" in x for x in lay))
        self.assertTrue(any(x.startswith("layout\t/gpt/part/3\t") and x.endswith(" rewritten") for x in lay))


class Planner(unittest.TestCase):
    def test_illumos_is_refresh_ready(self):
        f = TB._load("img_round_facts_t", os.path.join(STICK, "img_round_facts.py"))
        self.assertTrue(f.refresh_ready(STICK)["illumos"])

    def test_bins_changed_is_a_refresh_that_fits_and_a_full_build_that_doesnt(self):
        g, rows, _ = TR.fresh_world()
        for h in TR.BUILDERS:
            g[h]["facts"]["digests"]["illumos"] = TR.dig("illumos", bins="n2")
        p = TR.plan(g, rows)
        self.assertEqual((p["platforms"]["illumos"]["action"], p["platforms"]["illumos"]["why"]),
                         ("refresh", "bins changed"))
        g["chopin"]["facts"]["bin_sizes"]["illumos"] = dict(TR.SIZES, **{"restic-bko": 200 << 20})
        r = TR.plan(g, rows)["platforms"]["illumos"]
        self.assertEqual(r["action"], "full")
        self.assertRegex(r["why"], r"^bins changed; the new binaries need \d+ KiB, and the parent's release image has")


# The stub make-img-bsd.sh for illumos: as test_img_refresh_bsd's, with illumos's two trees.  A full build
# lists the archive's bins and marker, s2's marker, .volsetid, boot archive and hash; a refresh copies its
# parent (whose listing must be BKO_TREE_PARENT) and changes exactly those.  $EXTRA changes s2's
# loader.conf.local too, which no refresh may.
STUB = r'''#!/bin/bash
t=0 parent=""
while [[ $1 == --* ]]; do case $1 in --test) t=1; shift ;; --refresh-from) parent=$2; shift 2 ;; esac; done
d=$2
echo "make-img-bsd t=$t parent=$parent tp=$BKO_TREE_PARENT" >>"$CALLS"
h() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1 || shasum -a 256 "$1" | cut -d' ' -f1; }
id=$(python3 -c 'import uuid; print(uuid.uuid4())')
ids() {
    for f in "$BKO_BIN/illumos/restic-bko" "$BKO_BIN/illumos/rustic-bko" "$BKO_HOLT_BIN/illumos/bko-holt"; do
        [ -f "$f" ] && printf 'archive\t/usr/local/bin/%s\tf\t0755\t0:0\t1\t%s\t-\n' "${f##*/}" "$(h "$f")"
    done
    for x in archive stick; do printf '%s\t/.disk/backotter.json\tf\t0444\t0:0\t9\t%s\t-\n' $x "$id"; done
    printf 'stick\t/.volsetid\tf\t0644\t0:0\t37\tv%s\t-\n' "$id"
    printf 'stick\t/platform/i86pc/amd64/boot_archive.gz\tf\t0644\t0:0\t9\tgz%s\t-\n' "$id"
    printf 'stick\t/platform/i86pc/amd64/boot_archive.hash\tf\t0644\t0:0\t41\th%s\t-\n' "$id"
}
if [[ -n $parent ]]; then
    [[ -f $BKO_TREE_PARENT ]] || { echo "no parent listing" >&2; exit 1; }
    cp "$parent" "$d/x.img"
    { grep -v -e '/usr/local/bin/' -e '/.disk/backotter.json' -e '/.volsetid' -e '/boot_archive' -e loader.conf.local "$BKO_TREE_PARENT"
      ids
      if [[ -n $EXTRA ]]; then printf 'stick\t/boot/loader.conf.local\tf\t0644\t0:0\t1\textra\t-\n'
      else grep loader.conf.local "$BKO_TREE_PARENT"; fi; } | LC_ALL=C sort >"$BKO_TREE_OUT"
else
    echo "image $t" >"$d/x.img"
    { printf 'archive\t/\td\t0755\t0:0\t0\t-\t-\nstick\t/\td\t0755\t0:0\t0\t-\t-\n'
      printf 'stick\t/boot/loader.conf.local\tf\t0644\t0:0\t1\tdelay%s\t-\n' "$t"
      ids; } | LC_ALL=C sort >"$BKO_TREE_OUT"
fi
echo "{\"free_kib\": 90000, \"total_kib\": 480000, \"bins_kib\": 10, \"headroom_kib\": 65536}" >"$BKO_ROOM_OUT"
echo "Wrote $d/x.img (1 MiB), image $id"
'''


class IllumosImgOne(TB.NetbsdImgOne):
    """img-one.sh --refresh-from P illumos, with the stub make-img-bsd.sh."""

    def setUp(self):
        super().setUp()
        wr(os.path.join(self.w, "stick", "make-img-bsd.sh"), STUB, 0o755)
        T.set_generation(self.w, "omnios58", "cd" * 32)
        wr(os.path.join(self.w, "img-media", "illumos", "kayak-fea4340.tar"), "kayak\n")
        wr(os.path.join(self.w, "img-media", "illumos", "MEDIA"), "kayak-fea4340.tar\n")

    def d(self, *p):
        return os.path.join(self.i, "illumos", *p)

    def full(self):
        r = self.one("illumos")
        self.assertEqual(r.returncode, 0, r.stderr + rd(self.d("result.txt")))
        return self.prov("illumos")

    def new_bins(self, big=False):
        wr(os.path.join(self.w, "bko-bin", "illumos", "rustic-bko"), "new rustic\n", 0o755)

    def test_a_refresh_from_the_test_image(self):
        a = self.full()
        self.new_bins()
        r = self.one("--refresh-from", a["test"]["sha256"], "illumos")
        res = rd(self.d("result.txt"))
        self.assertEqual(r.returncode, 0, r.stderr + res)
        self.assertEqual(rd(self.d("done.exit")), "0\n")
        p = self.prov("illumos")
        pd = os.path.join(self.d(), sorted(x for x in os.listdir(self.d()) if x.startswith("prev-"))[-1])
        self.assertEqual((p["mode"], p["parent"]["test_sha256"]), ("refresh", a["test"]["sha256"]))
        self.assertEqual(p["base"]["os_manifest"], a["base"]["os_manifest"])
        self.assertIn("refresh check: release:", res)
        self.assertEqual(rd(self.calls).splitlines()[-2:],
                         [f"make-img-bsd t=0 parent={pd}/{a['release']['file']} tp={pd}/os-tree.txt",
                          f"make-img-bsd t=1 parent={pd}/{a['test']['file']} tp={pd}/test/os-tree.txt"])

    def test_an_extra_change_is_refused_as_c2(self):
        a = self.full()
        self.new_bins()
        r = self.one("--refresh-from", a["test"]["sha256"], "illumos", EXTRA="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("C2", rd(self.d("result.txt")))
        self.assertIn("/boot/loader.conf.local", rd(self.d("refresh-check.txt")))

    def test_binaries_that_dont_fit_by_the_record_refuse_before_anything_moves(self):
        a = self.full()
        wr(os.path.join(self.w, "bko-bin", "illumos", "restic-bko"), "x" * (200 << 20), 0o755)
        before = sorted(os.listdir(self.d()))
        r = self.one("--refresh-from", a["test"]["sha256"], "illumos")
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn("build it in full", r.stderr)
        self.assertEqual(sorted(os.listdir(self.d())), before)


# Only the tests written for illumos run here.
for _n in [n for n in dir(TB.NetbsdImgOne) if n.startswith("test")]:
    if _n not in IllumosImgOne.__dict__:
        setattr(IllumosImgOne, _n, None)

if __name__ == "__main__":
    unittest.main()
