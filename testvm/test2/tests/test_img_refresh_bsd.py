"""I4's unit tests (docs/design/img-speed.md §4.2, D1, D2, §14 row I4): a VM platform's refresh, NetBSD
first, and the parts every BSD-family refresh shares.

stick/bsd/refresh-lib.sh's fit and swap (run here with sh, on plain directories), stick/img_gpt.py's new
GPT GUIDs, img_inputs.py's layout without El Torito and its room (the provenance's, and the parent
check's fit), make-img-bsd.sh's refusal of a platform without a refresh, and img-one.sh --refresh-from
netbsd in a fixture world whose make-img-bsd.sh is a stub that keeps the contract (the parent's listing
first, each image from its own parent, room.json, exit 6 when the binaries don't fit).  The VM's own
part (build.sh refresh: vnd, fsck, the in-place swap) and E1 run on the farm.

No host, no VM.  Run from the worktree: python3 -m unittest discover testvm/test2/tests.
"""

import json
import os
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import uuid
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_img_inputs as T  # noqa: E402  (its fixture world)
import test_img_round as TR  # noqa: E402  (the planner)

II = T.II
rd, wr = T.rd, T.wr
STICK = T.STICK
LIB = os.path.join(STICK, "bsd", "refresh-lib.sh")
GPT = os.path.join(STICK, "img_gpt.py")
EFI = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
FFS = "49f48d5a-b10e-11dc-b99b-0019d1879648"
DATA = "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7"


def _load(name, path):
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


G = _load("img_gpt_t", GPT)


def gpt_disk(path, parts, total=2048):
    """A disk image with a protective MBR and a GPT whose CRCs check, primary and backup: PARTS is
    [(type, first, last, name, bytes)]."""
    img = bytearray(total * 512)
    img[446:462] = bytes([0, 0, 2, 0, 0xee, 0xff, 0xff, 0xff]) + struct.pack("<II", 1, total - 1)
    img[510:512] = b"\x55\xaa"
    ents = bytearray(128 * 128)
    for i, (typ, first, last, name, data) in enumerate(parts):
        ents[i * 128:(i + 1) * 128] = (uuid.UUID(typ).bytes_le + uuid.uuid4().bytes_le
                                       + struct.pack("<QQQ", first, last, 0) + name.encode("utf-16-le").ljust(72, b"\0"))
        img[first * 512:first * 512 + len(data)] = data
    disk = uuid.uuid4().bytes_le
    ecrc = zlib.crc32(bytes(ents)) & 0xFFFFFFFF
    for cur, alt, elba in ((1, total - 1, 2), (total - 1, 1, total - 33)):
        h = bytearray(b"EFI PART" + struct.pack("<IIIIQQQQ", 0x10000, 92, 0, 0, cur, alt, 34, total - 34)
                      + disk + struct.pack("<QIII", elba, 128, 128, ecrc))
        struct.pack_into("<I", h, 16, zlib.crc32(bytes(h)) & 0xFFFFFFFF)
        img[cur * 512:cur * 512 + 92] = h
        img[elba * 512:elba * 512 + len(ents)] = ents
    with open(path, "wb") as f:
        f.write(img)


def sh(script, cwd, **env):
    e = dict(os.environ, **env)
    return subprocess.run(["sh", "-c", script], cwd=cwd, env=e, capture_output=True, text=True)


class RefreshLib(unittest.TestCase):
    """stick/bsd/refresh-lib.sh on a directory standing for the parent's mounted root."""

    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="t2-i4-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.inn, self.root = os.path.join(self.t, "in"), os.path.join(self.t, "root")
        for n in ("restic-bko", "rustic-bko", "bko-holt"):
            wr(os.path.join(self.root, "usr/local/bin", n), "old " + n + "\n", 0o755)   # (0555 in the image; the VM writes as root)
        wr(os.path.join(self.root, ".disk/backotter.json"), '{"image_id": "old"}\n', 0o644)
        wr(os.path.join(self.inn, "restic-bko"), "new restic\n")
        wr(os.path.join(self.inn, "bko-holt"), "new holt\n")
        wr(os.path.join(self.inn, "backotter.json"), '{"image_id": "new"}\n')

    def lib(self, call):
        return sh(f'in={self.inn}; . {LIB}; {call}', self.t)

    def test_the_swap_keeps_each_inode_removes_what_the_set_lacks_and_writes_the_marker(self):
        b = os.path.join(self.root, "usr/local/bin")
        ino = os.stat(os.path.join(b, "restic-bko")).st_ino
        os.chmod(os.path.join(b, "restic-bko"), 0o750)       # the parent's mode stays
        r = self.lib(f"bko_swap {self.root} /usr/local/bin restic-bko rustic-bko bko-holt")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(rd(os.path.join(b, "restic-bko")), "new restic\n")
        st = os.stat(os.path.join(b, "restic-bko"))
        self.assertEqual((st.st_ino, stat.S_IMODE(st.st_mode)), (ino, 0o750))
        self.assertFalse(os.path.exists(os.path.join(b, "rustic-bko")))   # this set has none
        self.assertIn("rustic-bko removed", r.stdout)
        self.assertEqual(rd(os.path.join(self.root, ".disk/backotter.json")), '{"image_id": "new"}\n')

    def test_a_new_file_is_0555(self):
        os.remove(os.path.join(self.root, "usr/local/bin/bko-holt"))
        r = self.lib(f"bko_swap {self.root} /usr/local/bin bko-holt")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.root, "usr/local/bin/bko-holt")).st_mode), 0o555)

    def test_fit_refuses_with_its_code_by_the_planners_sum(self):
        wr(os.path.join(self.inn, "restic-bko"), "x" * (3 << 20))
        sizes = {n: os.path.getsize(os.path.join(self.inn, n)) for n in ("restic-bko", "bko-holt", "backotter.json")}
        olds = sum(int(sh(f"du -k {os.path.join(self.root, p)}", self.t).stdout.split()[0])
                   for p in ("usr/local/bin/restic-bko", "usr/local/bin/rustic-bko", "usr/local/bin/bko-holt",
                             ".disk/backotter.json"))
        need = sum(TR.R.kib(v) + TR.R.kib(v) // 50 + 256 for v in sizes.values())
        free = need - olds              # exactly enough
        r = self.lib(f"bko_fit {self.root} /usr/local/bin {free} restic-bko rustic-bko bko-holt")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.lib(f"bko_fit {self.root} /usr/local/bin {free - 1} restic-bko rustic-bko bko-holt")
        self.assertEqual(r.returncode, 3)
        self.assertIn("build it in full", r.stderr)
        # The planner's sum, from the same sizes (the marker counted as 257 KiB there), refuses no later.
        room = {w: {"free_kib": free - 1 + olds, "bins_kib": 0} for w in ("release", "test")}
        self.assertIsNotNone(TR.R.room_why("netbsd", {"room": room},
                                           {k: v for k, v in sizes.items() if k != "backotter.json"}))

    def test_room_line(self):
        r = self.lib(f"bko_room {self.root} /usr/local/bin 100 900 restic-bko rustic-bko bko-holt")
        self.assertRegex(r.stdout.strip(), r"^BKO-ROOM free_kib=100 total_kib=900 bins_kib=\d+$")


class Gpt(unittest.TestCase):
    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="t2-i4g-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.img = os.path.join(self.t, "d.img")
        gpt_disk(self.img, [(EFI, 40, 99, "EFI system", b"esp"), (FFS, 100, 999, "BackOtter root", b"root"),
                            (DATA, 1000, 1999, "BackOtter config", b"cfg")])

    def test_part(self):
        self.assertEqual(G.part(self.img, 3), (1000, 1000))
        with self.assertRaises(G.Refused):
            G.part(self.img, 4)

    def test_new_ids_are_new_everywhere_and_the_rest_is_kept(self):
        before = G.ids(self.img)
        lay = II.layout(self.img, {2, 3}, eltorito=False)
        after = G.new_ids(self.img)
        self.assertEqual([n for n, _ in after], [0, 1, 2, 3])
        self.assertFalse({g for _, g in before} & {g for _, g in after})
        p, b = G.read(self.img)                     # both tables check, and agree
        self.assertEqual(p["entries"], b["entries"])
        self.assertEqual(p["raw"][56:72], b["raw"][56:72])
        self.assertEqual(II.layout(self.img, {2, 3}, eltorito=False), lay)   # C2 masks the ids, nothing else moved
        self.assertEqual(G.part(self.img, 2), (100, 900))

    def test_a_table_that_doesnt_check_is_refused_and_left(self):
        with open(self.img, "r+b") as f:
            f.seek(2 * 512 + 200)
            f.write(b"\xff")
        data = open(self.img, "rb").read()
        r = subprocess.run([sys.executable, GPT, "new-ids", self.img], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("fail their CRC", r.stderr)
        self.assertEqual(open(self.img, "rb").read(), data)

    def test_usage(self):
        self.assertEqual(subprocess.run([sys.executable, GPT], capture_output=True).returncode, 2)


class Layout(unittest.TestCase):
    def test_a_vm_image_has_no_el_torito_and_needs_no_xorriso(self):
        t = os.path.realpath(tempfile.mkdtemp(prefix="t2-i4l-"))
        self.addCleanup(shutil.rmtree, t, True)
        img = os.path.join(t, "d.img")
        gpt_disk(img, [(EFI, 40, 99, "EFI system", b"esp"), (FFS, 100, 999, "BackOtter root", b"root")])
        lay = II.layout(img, {2}, xorriso=os.path.join(t, "no-such-xorriso"), eltorito=False)
        self.assertFalse([x for x in lay if "/eltorito/" in x])
        self.assertTrue(any(x.startswith("layout\t/gpt/part/1\t") and "sha256=" in x for x in lay))
        self.assertTrue(any(x.startswith("layout\t/gpt/part/2\t") and x.endswith(" rewritten") for x in lay))

    def test_the_swap_list_names_netbsds_rewritten_partitions(self):
        old = os.environ.get("S")
        os.environ["S"] = STICK
        try:
            self.assertEqual(II.layout_rewritten("netbsd"), {2, 3})
        finally:
            os.environ.pop("S") if old is None else os.environ.update(S=old)


class MakeImgBsd(unittest.TestCase):
    def test_a_platform_without_a_refresh_is_refused(self):
        t = os.path.realpath(tempfile.mkdtemp(prefix="t2-i4m-"))
        self.addCleanup(shutil.rmtree, t, True)
        wr(os.path.join(t, "p.img"), "x")
        sub = "haiku"
        if subprocess.run(["grep", "-Eq", r"^[[:space:]]*refresh\)", os.path.join(STICK, sub, "build.sh")]).returncode == 0:
            self.skipTest(f"{sub} refreshes now")
        r = subprocess.run(["bash", os.path.join(STICK, "make-img-bsd.sh"), "--refresh-from", os.path.join(t, "p.img"),
                            sub, t], capture_output=True, text=True, env=dict(os.environ, BKO_WORK=t))
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"stick/{sub}/build.sh has no refresh", r.stderr)

    def test_netbsds_build_sh_has_the_subcommands(self):
        b = os.path.join(STICK, "netbsd", "build.sh")
        self.assertEqual(subprocess.run(["sh", "-n", b]).returncode, 0)
        text = rd(b)
        for c in ("list", "room", "refresh"):
            self.assertRegex(text, rf"(?m)^{c}\) {c} ;;$")
        self.assertIn("GPT_IDS=random", rd(os.path.join(STICK, "netbsd", "platform.conf")))


# The stub make-img-bsd.sh: a full build lists bins, a marker with a new id and the OS files; a refresh
# copies its parent image (whose listing must be BKO_TREE_PARENT), swaps the bins' lines for the set's
# and the marker's for a new id; both write room.json from $FREE.  $NOFIT makes a refresh exit 6.
STUB = r'''#!/bin/bash
t=0 parent=""
while [[ $1 == --* ]]; do case $1 in --test) t=1; shift ;; --refresh-from) parent=$2; shift 2 ;; esac; done
d=$2
echo "make-img-bsd t=$t parent=$parent tp=$BKO_TREE_PARENT" >>"$CALLS"
h() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1 || shasum -a 256 "$1" | cut -d' ' -f1; }
id=$(python3 -c 'import uuid; print(uuid.uuid4())')
bins() {
    for f in "$BKO_BIN/netbsd/restic-bko" "$BKO_BIN/netbsd/rustic-bko" "$BKO_HOLT_BIN/netbsd/bko-holt"; do
        [ -f "$f" ] && printf 'root\t/usr/local/bin/%s\tf\t0555\t0:0\t1\t%s\t-\n' "${f##*/}" "$(h "$f")"
    done
}
if [[ -n $parent ]]; then
    [[ -z $NOFIT ]] || { echo "the new binaries don't fit" >&2; exit 6; }
    [[ -f $BKO_TREE_PARENT ]] || { echo "no parent listing" >&2; exit 1; }
    cp "$parent" "$d/x.img"
    { grep -v -e '/usr/local/bin/' -e '/.disk/backotter.json' "$BKO_TREE_PARENT"
      bins; printf 'root\t/.disk/backotter.json\tf\t0444\t0:0\t9\t%s\t-\n' "$id"
      [[ -z $EXTRA ]] || printf 'root\t/etc/extra\tf\t0644\t0:0\t1\taa\t-\n'; } | LC_ALL=C sort >"$BKO_TREE_OUT"
else
    echo "image $t" >"$d/x.img"
    { printf 'root\t/\td\t0755\t0:0\t0\t-\t-\nroot\t/boot.cfg\tf\t0644\t0:0\t1\tcfg%s\t-\n' "$t"
      bins; printf 'root\t/.disk/backotter.json\tf\t0444\t0:0\t9\t%s\t-\n' "$id"; } | LC_ALL=C sort >"$BKO_TREE_OUT"
fi
echo "{\"free_kib\": ${FREE:-90000}, \"total_kib\": 480000, \"bins_kib\": 10, \"headroom_kib\": 65536}" >"$BKO_ROOM_OUT"
echo "Wrote $d/x.img (1 MiB), image $id"
'''


class NetbsdImgOne(T.ImgOne):
    """img-one.sh --refresh-from P netbsd, with the stub make-img-bsd.sh."""

    def setUp(self):
        super().setUp()
        wr(os.path.join(self.w, "stick", "make-img-bsd.sh"), STUB, 0o755)
        # GNU mv's -t and sha256sum where the host is a Mac.
        wr(os.path.join(self.path, "mv"), '#!/bin/sh\nif [ "$1" = -t ]; then d=$2; shift 2; set -- "$@" "$d"; fi\n'
                                          'exec /bin/mv "$@"\n', 0o755)
        if not shutil.which("sha256sum"):
            wr(os.path.join(self.path, "sha256sum"), '#!/bin/sh\nexec shasum -a 256 "$@"\n', 0o755)

    def one(self, *args, **env):
        e = dict(os.environ, BKO_IMG_DIR=self.i, FP=self.fp, HOOK="", CALLS=self.calls, NOFIT="", EXTRA="",
                 PATH=self.path + ":" + os.environ["PATH"])
        e.update(env)
        return subprocess.run([T.BASH, os.path.join(self.w, "stick", "img-one.sh"), *args], env=e,
                              capture_output=True, text=True)

    def d(self, *p):
        return os.path.join(self.i, "netbsd", *p)

    def full(self):
        r = self.one("netbsd")
        self.assertEqual(r.returncode, 0, r.stderr + rd(self.d("result.txt")))
        return self.prov("netbsd")

    def new_bins(self, big=False):
        wr(os.path.join(self.w, "bko-bin", "netbsd", "restic-bko"), "x" * (200 << 20) if big else "new restic\n", 0o755)
        wr(os.path.join(self.w, "holt-bin", "netbsd", "bko-holt"), "new holt\n", 0o755)

    def test_a_full_build_records_each_images_room(self):
        a = self.full()
        self.assertEqual(a["room"]["release"]["free_kib"], 90000)
        self.assertEqual(set(a["room"]), {"release", "test"})
        self.assertIn("tp=\n", rd(self.calls))

    def test_a_refresh_from_the_test_image(self):
        a = self.full()
        self.new_bins()
        r = self.one("--refresh-from", a["test"]["sha256"], "netbsd")
        res = rd(self.d("result.txt"))
        self.assertEqual(r.returncode, 0, r.stderr + res)
        self.assertEqual(rd(self.d("done.exit")), "0\n")
        p = self.prov("netbsd")
        pd = os.path.join(self.d(), sorted(x for x in os.listdir(self.d()) if x.startswith("prev-"))[-1])
        self.assertEqual((p["mode"], p["parent"]["test_sha256"]), ("refresh", a["test"]["sha256"]))
        self.assertEqual((p["base"]["os_epoch"], p["base"]["os_manifest"]), (a["base"]["os_epoch"], a["base"]["os_manifest"]))
        self.assertNotEqual(p["release"]["image_id"], a["release"]["image_id"])
        self.assertIn("refresh check: release:", res)
        self.assertEqual(p["room"]["test"]["free_kib"], 90000)
        calls = rd(self.calls).splitlines()[-2:]
        # Each image from its own parent, its listing checked against the parent's.
        self.assertEqual(calls, [f"make-img-bsd t=0 parent={pd}/{a['release']['file']} tp={pd}/os-tree.txt",
                                 f"make-img-bsd t=1 parent={pd}/{a['test']['file']} tp={pd}/test/os-tree.txt"])

    def test_an_extra_change_is_refused_as_c2(self):
        a = self.full()
        self.new_bins()
        r = self.one("--refresh-from", a["test"]["sha256"], "netbsd", EXTRA="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("C2", rd(self.d("result.txt")))
        self.assertIn("/etc/extra", rd(self.d("refresh-check.txt")))

    def test_binaries_that_dont_fit_by_the_record_refuse_before_anything_moves(self):
        a = self.full()
        self.new_bins(big=True)
        before = sorted(os.listdir(self.d()))
        r = self.one("--refresh-from", a["test"]["sha256"], "netbsd")
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertRegex(r.stderr, r"the new binaries need \d+ KiB, and the parent's release image has \d+ .*build it in full")
        self.assertEqual(sorted(os.listdir(self.d())), before)

    def test_binaries_that_dont_fit_in_the_vm_exit_6(self):
        a = self.full()
        self.new_bins()
        r = self.one("--refresh-from", a["test"]["sha256"], "netbsd", NOFIT="1")
        self.assertEqual(r.returncode, 6, r.stderr)
        self.assertEqual(rd(self.d("done.exit")), "1\n")
        self.assertIn("don't fit the parent's free space", rd(self.d("result.txt")))

    def test_a_platform_without_a_refresh_is_refused(self):
        r = self.one("--refresh-from", "ab" * 32, "freebsd")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no refresh of freebsd yet", r.stderr)


# ImgOne's own tests run once, in test_img_inputs: not again here with this stub.
for _n in [n for n in dir(T.ImgOne) if n.startswith("test")]:
    if _n not in NetbsdImgOne.__dict__:
        setattr(NetbsdImgOne, _n, None)

if __name__ == "__main__":
    unittest.main()
