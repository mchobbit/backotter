"""I3's unit tests (docs/design/img-speed.md §4.1, C2-C5, §14 row I3): a Linux refresh.

stick/img_inputs.py's layout (a GPT image's partition table, boot code, ESP and El Torito images, with
positions and ids masked), its C5 checks (same-base, parent), its C2/C3 check (refresh-check), and
img-one.sh --refresh-from in a fixture world whose layer, image and xorriso are stubs that keep the
real flow: the parent's layer is what the refresh starts from, the tree listings are real, and so are
the checks.  E1 itself (real builds) runs on the farm.

No host, no VM.  Run from the worktree: python3 -m unittest discover testvm/test2/tests.
"""

import datetime
import json
import os
import re
import struct
import subprocess
import sys
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_img_inputs as T  # noqa: E402  (its fixture world)

II = T.II
rd, wr = T.rd, T.wr
EFI = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
DATA = "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7"


def gpt_image(path, parts, disk_guid=None, sig=b"\x01\x02\x03\x04", total=400, code=b"BOOT"):
    """A disk image with a protective MBR and a GPT: PARTS is [(type, first, last, attrs, name, bytes)];
    each partition's unique GUID and the disk's are random unless given."""
    img = bytearray(total * 512)
    img[0:440] = (code * 110)[:440]
    img[440:444] = sig
    img[446:462] = bytes([0, 0, 2, 0, 0xee, 0xff, 0xff, 0xff]) + struct.pack("<II", 1, total - 1)
    img[510:512] = b"\x55\xaa"
    ents = bytearray(128 * 128)
    for i, (typ, first, last, attrs, name, data) in enumerate(parts):
        ents[i * 128:(i + 1) * 128] = (uuid.UUID(typ).bytes_le + uuid.uuid4().bytes_le
                                       + struct.pack("<QQQ", first, last, attrs)
                                       + name.encode("utf-16-le").ljust(72, b"\0"))
        img[first * 512:first * 512 + len(data)] = data
    img[1024:1024 + len(ents)] = ents
    hdr = (b"EFI PART" + struct.pack("<IIIIQQQQ", 0x10000, 92, 0, 0, 1, total - 1, 34, total - 34)
           + uuid.UUID(disk_guid or str(uuid.uuid4())).bytes_le + struct.pack("<QIII", 2, 128, 128, 0))
    img[512:512 + len(hdr)] = hdr
    with open(path, "wb") as f:
        f.write(img)


def catalog(rbas, platform=0xEF):
    """An El Torito catalog: validation entry, default entry, one section header and its entry."""
    c = bytearray(2048)
    c[0] = 0x01
    c[30:32] = b"\x55\xaa"
    c[32] = 0x88
    c[38:40] = struct.pack("<H", 4)
    c[40:44] = struct.pack("<I", rbas[0])
    c[64], c[65] = 0x91, platform
    c[96] = 0x88
    c[102:104] = struct.pack("<H", 10296)
    c[104:108] = struct.pack("<I", rbas[1])
    return bytes(c)


class RepoCase(unittest.TestCase):
    """The repo's own stick/ (its img-swap.tsv) as S."""

    def setUp(self):
        old = os.environ.get("S")
        os.environ["S"] = T.STICK
        self.addCleanup(lambda: os.environ.pop("S") if old is None else os.environ.update(S=old))


class Layout(RepoCase):
    def setUp(self):
        super().setUp()
        self.t = os.path.realpath(__import__("tempfile").mkdtemp(prefix="t2-i3-"))
        self.addCleanup(__import__("shutil").rmtree, self.t, True)
        # The stub xorriso: IMG.boot/'s files are its boot images.
        self.x = os.path.join(self.t, "xorriso")
        wr(self.x, '#!/bin/sh\nwhile [ $# -gt 0 ]; do case $1 in -indev) i=$2; shift ;; '
                   '-extract_boot_images) d=$2; shift ;; esac; shift; done\ncp "$i.boot"/* "$d"\n', 0o755)

    def img(self, name, p1, efi, p3, rbas=(190, 210), **kw):
        path = os.path.join(self.t, name)
        a = 64 + (len(p1) + 511) // 512
        gpt_image(path, [(DATA, 64, a - 1, 1 << 60 | 1, "ISO9660", p1), (EFI, a, a + 9, 0, "Appended2", efi),
                         (DATA, a + 10, a + 19, 0, "Appended3", p3)], **kw)
        os.makedirs(path + ".boot")
        for n, data in (("eltorito_catalog.img", catalog(rbas)), ("eltorito_img1_bios.img", b"bios"),
                        ("eltorito_img2_uefi.img", efi), ("systemarea.img", b"sys")):
            with open(os.path.join(path + ".boot", n), "wb") as f:
                f.write(data)
        return path

    def lay(self, path, rewritten=(1, 3)):
        return II.layout(path, set(rewritten), xorriso=self.x)

    def test_positions_ids_and_rewritten_partitions_are_masked(self):
        a = self.img("a.img", b"iso" * 3000, b"efi" * 100, b"config A", sig=b"\x01\x01\x01\x01")
        b = self.img("b.img", b"ISO" * 9000, b"efi" * 100, b"config B", rbas=(190, 260), sig=b"\x09\x09\x09\x09")
        self.assertEqual(self.lay(a), self.lay(b))
        self.assertTrue(any("/gpt/part/2\ttype=" + EFI in x for x in self.lay(a)))
        self.assertTrue(any(x.endswith(" rewritten") and "/gpt/part/1\t" in x for x in self.lay(a)))

    def test_the_esp_is_compared_byte_for_byte_and_is_signed(self):
        a = self.img("a.img", b"iso", b"efi" * 100, b"c")
        b = self.img("b.img", b"iso", b"efi" * 99 + b"EFI", b"c")
        diff = set(self.lay(a)) ^ set(self.lay(b))
        self.assertTrue(diff)
        self.assertTrue(all(II.signed(x) for x in diff), diff)

    def test_a_partition_not_rewritten_is_compared(self):
        a = self.img("a.img", b"iso", b"e", b"config A")
        b = self.img("b.img", b"iso", b"e", b"config B")
        self.assertNotEqual(self.lay(a, rewritten=(1,)), self.lay(b, rewritten=(1,)))

    def test_the_boot_code_is_compared(self):
        a = self.img("a.img", b"iso", b"e", b"c")
        b = self.img("b.img", b"iso", b"e", b"c", code=b"BOOX")
        self.assertEqual([x for x in set(self.lay(a)) ^ set(self.lay(b)) if "/system-area" not in x], [])
        self.assertNotEqual(self.lay(a), self.lay(b))

    def test_the_catalog_masks_positions_only(self):
        self.assertEqual(II.catalog_view(catalog((1, 2))), II.catalog_view(catalog((7, 9))))
        self.assertNotEqual(II.catalog_view(catalog((1, 2))), II.catalog_view(catalog((1, 2), platform=0x00)))

    def test_the_swap_list_names_linux_partitions(self):
        self.assertEqual(II.layout_rewritten("linux"), {1, 3})
        self.assertEqual(II.layout_rewritten("netbsd"), set())
        # Layout rows never enter the OS manifest (and are never "missing" from a listing).
        _, missing, _ = II.os_manifest(["iso\t/x\tf\t0644\t-\t1\taa\t-"], II.swap_rows("linux"))
        self.assertFalse([m for m in missing if m.startswith("layout:")])


def line(tree, path, h="aa", size=1, typ="f"):
    return "\t".join([tree, path, typ, "0644", "0:0", str(size), h, "-"])


class RefreshCheck(RepoCase):
    """C2 and C3 over fixture listings, layouts given."""

    BINS = {"restic-bko": "11" * 32, "rustic-bko": "22" * 32, "bko-holt": "33" * 32}

    def setUp(self):
        super().setUp()
        self.t = os.path.realpath(__import__("tempfile").mkdtemp(prefix="t2-i3c-"))
        self.addCleanup(__import__("shutil").rmtree, self.t, True)
        self.p, self.c = os.path.join(self.t, "p"), os.path.join(self.t, "c")
        old = {"restic-bko": "aa" * 32, "rustic-bko": "bb" * 32, "bko-holt": "cc" * 32}
        for d, bins, idx in ((self.p, old, "1"), (self.c, self.BINS, "2")):
            for sub, cfg in (("", "normal"), ("test", "serial")):
                lines = [line("iso", "/casper/vmlinuz", "4b"), line("iso", "/casper/initrd", "1d"),
                         line("iso", "/boot/grub/grub.cfg", cfg), line("iso", "/.disk/backotter.json", idx + sub),
                         line("iso", "/boot.catalog", idx), line("layer", "/etc/os-release", "05")]
                lines += [line("layer", "/usr/local/bin/" + n, h) for n, h in bins.items()]
                wr(os.path.join(d, sub, "os-tree.txt"), "\n".join(sorted(lines)) + "\n")
                wr(os.path.join(d, sub, "x.img"), "img\n")
            wr(os.path.join(d, "bko-bin.sha256"), "".join(f"{h}  ./{n}\n" for n, h in bins.items()))
        wr(os.path.join(self.p, "provenance.json"), json.dumps({"release": {"file": "x.img"},
                                                                "test": {"file": "test/x.img"}}))
        self.layouts = {}

    def lay(self, img):
        return self.layouts.get(img, ["layout\t/gpt/part/2\ttype=" + EFI + " attrs=0 name=A size=1 sha256=ee"])

    def check(self):
        return II.refresh_check("linux", self.p, self.c, layouts=self.lay)

    def edit(self, sub, fn):
        f = os.path.join(self.c, sub, "os-tree.txt")
        wr(f, "\n".join(sorted(fn(T.rd(f).splitlines()))) + "\n")

    def test_only_the_swap_list_differs(self):
        rep = self.check()
        self.assertIn("release and test differ as their parents did", rep[-1])

    def test_an_extra_change_in_the_layer_is_refused(self):
        self.edit("", lambda ls: ls + [line("layer", "/etc/extra")])
        with self.assertRaisesRegex(II.Refused, r"C2: the release image's OS file systems.*layer\t/etc/extra"):
            self.check()

    def test_a_signed_object_is_refused_as_c3(self):
        self.edit("test", lambda ls: [x.replace("1d", "1e") if "/casper/initrd" in x else x for x in ls])
        with self.assertRaisesRegex(II.Refused, r"C3: the test image's signed objects changed.*initrd.*build it in full"):
            self.check()

    def test_an_esp_change_is_refused_as_c3(self):
        self.layouts[os.path.join(self.c, "x.img")] = ["layout\t/gpt/part/2\ttype=" + EFI + " attrs=0 name=A size=1 sha256=ef"]
        with self.assertRaisesRegex(II.Refused, "C3"):
            self.check()

    def test_a_layout_change_is_refused_as_c2(self):
        self.layouts[os.path.join(self.c, "test", "x.img")] = self.lay("") + ["layout\t/mbr/part/2\tstatus=00 type=0c"]
        with self.assertRaisesRegex(II.Refused, r"C2: the test image's layout"):
            self.check()

    def test_a_binary_not_the_recorded_one_is_refused(self):
        self.edit("", lambda ls: [x.replace("33" * 32, "34" * 32) for x in ls])
        with self.assertRaisesRegex(II.Refused, r"C1: the release image's layer:/usr/local/bin/bko-holt"):
            self.check()

    def test_siblings_must_differ_as_their_parents_did(self):
        # The test copy made like the release (its menu's line): refused, as its parent's differs.
        self.edit("test", lambda ls: [x.replace("serial", "normal") for x in ls])
        with self.assertRaisesRegex(II.Refused, r"C2: the test image's OS file systems.*grub.cfg"):
            self.check()

    def test_no_parent_listing_is_refused(self):
        os.remove(os.path.join(self.p, "test", "os-tree.txt"))
        with self.assertRaisesRegex(II.Refused, "the parent has no test/os-tree.txt"):
            self.check()


class Linux(T.EnvCase):
    """img-one.sh linux, full and --refresh-from, with stub layer, image and xorriso."""

    def setUp(self):
        super().setUp()
        self.w = T.make_world(self.t)
        self.use(self.w)
        self.i = os.path.join(self.t, "images")
        self.path = os.path.join(self.t, "path")
        self.calls = os.path.join(self.t, "calls")
        s = os.path.join(self.w, "stick")
        # The layer: a tar standing for the squashfs.  A full build makes a tree with a build-time
        # stamp (the noise of two full builds); a refresh unpacks the parent image's.  $EXTRA adds a file.
        wr(os.path.join(s, "build-backotter-layer.sh"), r'''#!/bin/bash
set -e
L=ubuntu-server-minimal.backotter parent=""
if [ "$1" = --refresh-from ]; then parent=$2; shift 2; fi
echo "layer ${parent:-full} $BKO_PARENT_PROVENANCE" >>"$CALLS"
OUT=$3; rm -rf "$OUT"; mkdir -p "$OUT/t"
if [ -n "$parent" ]; then
    x=$(mktemp -d); tail -c +32769 "$parent" | tar -xf - -C "$x"; tar -xf "$x/casper/$L.squashfs" -C "$OUT/t"; rm -rf "$x"
    { echo "== Refreshed from $parent"; sed '/^== Archivers/,$d' "$BKO_PARENT_REPORT"; } >"$OUT/report.txt"
else
    mkdir -p "$OUT/t/etc" "$OUT/t/var/log"; echo ubuntu >"$OUT/t/etc/os-release"
    date +%s%N >"$OUT/t/var/log/dpkg.log"; echo "== Packages added" >"$OUT/report.txt"
fi
mkdir -p "$OUT/t/usr/local/bin"
cp "$BKO_BIN/linux/restic-bko" "$BKO_BIN/linux/rustic-bko" "$BKO_HOLT_BIN/linux/bko-holt" "$OUT/t/usr/local/bin/"
[ -z "$EXTRA" ] || echo extra >"$OUT/t/etc/extra"
echo "== Archivers, and bko-holt" >>"$OUT/report.txt"
tar -cf "$OUT/$L.squashfs" -C "$OUT/t" . ; rm -rf "$OUT/t"
echo "linux-image-7.3.0-1-generic 7.3" >"$OUT/$L.manifest"; echo 1 >"$OUT/$L.size"
''', 0o755)
        # The image: 32 KiB of system area, then the ISO tree as a tar.  $VMLINUZ changes the kernel.
        wr(os.path.join(s, "make-img.sh"), r'''#!/bin/bash
set -e
t=0; [ "$1" = --test ] && { t=1; shift; }
ISO=$1 SL=$2 LAYER=$3 OUT=$4 L=ubuntu-server-minimal.backotter
x=$(mktemp -d); mkdir -p "$x/casper" "$x/.disk" "$x/boot/grub"
cp "$LAYER/$L.squashfs" "$LAYER/$L.size" "$x/casper/"; cp "$SL/vmlinuz" "$SL/initrd" "$x/casper/"
[ -z "$VMLINUZ" ] || echo "$VMLINUZ" >>"$x/casper/vmlinuz"
id=$(python3 -c 'import uuid; print(uuid.uuid4())'); echo "{\"image_id\": \"$id\"}" >"$x/.disk/backotter.json"
echo "timeout test=$t" >"$x/boot/grub/grub.cfg"; date +%s%N >"$x/boot.catalog"
(cd "$x" && find . -type f | sort | xargs md5 -r 2>/dev/null || true) >"$x/md5sum.txt"
img=$OUT/x.img; head -c 32768 /dev/zero >"$img"; tar -cf - -C "$x" . >>"$img"; rm -rf "$x"
echo "Wrote $img (1 MiB), image $id"
''', 0o755)
        wr(os.path.join(s, "boot-test.py"), "print('  ok   tools  all')\n")
        wr(os.path.join(self.path, "flock"), "#!/bin/sh\nexit 0\n", 0o755)
        wr(os.path.join(self.path, "sudo"), '#!/bin/sh\n[ "$1" = -n ] && shift\nexec "$@"\n', 0o755)
        wr(os.path.join(self.path, "chown"), "#!/bin/sh\nexit 0\n", 0o755)
        # GNU mv's -t (img-one.sh's move to prev-STAMP), where mv is BSD's.
        wr(os.path.join(self.path, "mv"), '#!/bin/sh\nif [ "$1" = -t ]; then d=$2; shift 2; set -- "$@" "$d"; fi\n'
                                          'exec /bin/mv "$@"\n', 0o755)
        wr(os.path.join(self.path, "unsquashfs"), '#!/bin/sh\nmkdir -p "$3" && tar -xf "$4" -C "$3"\n', 0o755)
        wr(os.path.join(self.path, "xorriso"), r'''#!/bin/sh
while [ $# -gt 0 ]; do case $1 in -indev) i=$2; shift ;; -extract) d=$3; shift 2 ;; -extract_boot_images) b=$2; shift ;; esac; shift; done
if [ -n "$b" ]; then echo bios >"${b}eltorito_img1_bios.img"; exit 0; fi
mkdir -p "$d" && tail -c +32769 "$i" | tar -xf - -C "$d"
''', 0o755)

    def run_one(self, *args, **env):
        e = dict(os.environ, BKO_IMG_DIR=self.i, CALLS=self.calls, EXTRA="", VMLINUZ="",
                 PATH=self.path + ":" + os.environ["PATH"])
        e.update(env)
        return subprocess.run(["bash", os.path.join(self.w, "stick", "img-one.sh"), *args], env=e,
                              capture_output=True, text=True)

    def d(self, *p):
        return os.path.join(self.i, "linux", *p)

    def prov(self, *p):
        return json.loads(rd(self.d(*p, "provenance.json")))

    def full(self):
        r = self.run_one("linux")
        self.assertEqual(r.returncode, 0, r.stderr + rd(self.d("result.txt")))
        return self.prov()

    def new_bins(self):
        for p, n in (("bko-bin", "restic-bko"), ("bko-bin", "rustic-bko"), ("holt-bin", "bko-holt")):
            wr(os.path.join(self.w, p, "linux", n), f"new {n}\n", 0o755)

    def test_a_refresh_from_the_test_image(self):
        a = self.full()
        self.assertEqual(a["mode"], "full")
        self.new_bins()
        r = self.run_one("--refresh-from", a["test"]["sha256"], "linux")
        res = rd(self.d("result.txt"))
        self.assertEqual(r.returncode, 0, r.stderr + res)
        self.assertEqual(rd(self.d("done.exit")), "0\n")
        p = self.prov()
        self.assertEqual(p["mode"], "refresh")
        self.assertEqual(p["parent"]["test_sha256"], a["test"]["sha256"])
        self.assertEqual(p["parent"]["sha256"], a["release"]["sha256"])
        self.assertEqual(p["parent"]["provenance_sha256"],
                         II.sha256_file(os.path.join(self.d(), self.parent_dir(), "provenance.json")))
        self.assertEqual((p["base"]["os_epoch"], p["base"]["os_manifest"]), (a["base"]["os_epoch"], a["base"]["os_manifest"]))
        self.assertEqual(p["base"]["digest"], a["base"]["digest"])
        self.assertNotEqual(p["bins"]["digest"], a["bins"]["digest"])
        self.assertNotEqual(p["release"]["image_id"], a["release"]["image_id"])
        self.assertIn("refresh check: release:", res)
        self.assertIn("provenance: a refresh of " + a["test"]["sha256"][:12], res)
        # The layer is the parent's: its build-time file came along, and the layer saw the parent.
        tree = rd(self.d("os-tree.txt"))
        old = [x for x in rd(os.path.join(self.d(), self.parent_dir(), "os-tree.txt")).splitlines() if "/var/log/dpkg.log" in x]
        self.assertIn(old[0], tree.splitlines())
        self.assertIn(f"layer {os.path.join(self.d(), self.parent_dir())}/{a['release']['file']}", rd(self.calls))
        self.assertIn("== Packages added", rd(self.d("layer-report.txt")))

    def parent_dir(self):
        return sorted(x for x in os.listdir(self.d()) if x.startswith("prev-"))[-1]

    def test_a_refresh_from_the_set_itself(self):
        a = self.full()
        self.new_bins()
        r = self.run_one("--refresh-from", self.d(), "linux")
        self.assertEqual(r.returncode, 0, r.stderr + rd(self.d("result.txt")))
        self.assertEqual(self.prov()["parent"]["test_sha256"], a["test"]["sha256"])

    def test_a_refresh_of_a_refresh(self):
        self.full()
        self.new_bins()
        b = self.run_one("--refresh-from", self.d(), "linux")
        self.assertEqual(b.returncode, 0, b.stderr)
        r = self.prov()
        wr(os.path.join(self.w, "holt-bin", "linux", "bko-holt"), "newer\n", 0o755)
        c = self.run_one("--refresh-from", r["test"]["sha256"], "linux")
        self.assertEqual(c.returncode, 0, c.stderr + rd(self.d("result.txt")))
        self.assertEqual(self.prov()["parent"]["mode"], "refresh")

    def test_an_extra_change_in_the_layer_is_refused(self):
        a = self.full()
        self.new_bins()
        r = self.run_one("--refresh-from", a["test"]["sha256"], "linux", EXTRA="1")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(rd(self.d("done.exit")), "1\n")
        res = rd(self.d("result.txt"))
        self.assertIn("the refresh is refused: C2:", res)
        self.assertIn("/etc/extra", res)
        self.assertFalse(os.path.exists(self.d("provenance.json")))
        self.assertFalse(os.path.exists(self.d("test", "boot-bios.out")))

    def test_a_changed_signed_object_is_refused(self):
        a = self.full()
        r = self.run_one("--refresh-from", a["test"]["sha256"], "linux", VMLINUZ="patched")
        self.assertEqual(r.returncode, 1)
        self.assertIn("C3:", rd(self.d("result.txt")))

    def refused_before_the_move(self, *args, why):
        before = sorted(os.listdir(self.d()))
        r = self.run_one(*args)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertRegex(r.stderr, why)
        self.assertEqual(sorted(os.listdir(self.d())), before)
        self.assertEqual(rd(self.d("done.exit")), "0\n")

    def test_a_changed_base_refuses_before_anything_moves(self):
        a = self.full()
        with open(os.path.join(self.w, "stick", "layer", "etc", "zz-i3-test"), "w") as f:
            f.write("x\n")
        self.refused_before_the_move("--refresh-from", a["test"]["sha256"], "linux",
                                     why=r"the base changed since the parent \(C5\): stick/ \(stick/layer/etc/zz-i3-test\)")

    def test_a_new_slim_refuses(self):
        a = self.full()
        wr(os.path.join(self.w, "server", "slim", "vmlinuz"), "new kernel\n")
        self.refused_before_the_move("--refresh-from", a["test"]["sha256"], "linux", why=r"base changed.*slim")

    def test_past_the_cadence_refuses(self):
        a = self.full()
        a["base"]["os_epoch"] = str(datetime.date.today() - datetime.timedelta(days=15))
        wr(self.d("provenance.json"), json.dumps(a))
        self.refused_before_the_move("--refresh-from", a["test"]["sha256"], "linux", why=r"past the cadence \(D3: 14\)")

    def test_an_image_not_its_provenances_refuses(self):
        a = self.full()
        with open(self.d(a["test"]["file"]), "ab") as f:
            f.write(b"x")
        self.refused_before_the_move("--refresh-from", self.d(), "linux", why="not the one its provenance records")

    def test_no_provenance_refuses(self):
        self.full()
        os.remove(self.d("provenance.json"))
        self.refused_before_the_move("--refresh-from", self.d(), "linux", why="an image from before I1")

    def test_an_unknown_parent_refuses(self):
        self.full()
        self.refused_before_the_move("--refresh-from", "ab" * 32, "linux", why="no set here has a provenance")

    def test_only_linux_refreshes_yet(self):
        r = self.run_one("--refresh-from", "ab" * 32, "netbsd")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no refresh of netbsd yet", r.stderr)


class SameBase(T.EnvCase):
    """build-backotter-layer.sh's C5 check: the ISO and slim of the parent's provenance."""

    def setUp(self):
        super().setUp()
        self.w = T.make_world(self.t)
        self.use(self.w)
        d = II.digest("linux")
        self.pv = os.path.join(self.t, "provenance.json")
        self.img = os.path.join(self.t, "x.img")
        wr(self.img, "image\n")
        wr(self.pv, json.dumps({"schema": 1, "platform": "linux", "base": {"inputs": d["base"]["inputs"]},
                                "release": {"sha256": II.sha256_file(self.img)}, "test": {"sha256": "00"}}))
        self.iso, self.sl = os.path.join(self.w, "server", "u.iso"), os.path.join(self.w, "server", "slim")

    def test_the_same_iso_and_slim_pass(self):
        II.same_base(self.pv, self.iso, self.sl, self.img)

    def test_another_iso_is_refused(self):
        wr(self.iso, "another iso\n")
        with self.assertRaisesRegex(II.Refused, "the ISO is"):
            II.same_base(self.pv, self.iso, self.sl)

    def test_another_slim_is_refused(self):
        wr(os.path.join(self.sl, "initrd"), "another\n")
        with self.assertRaisesRegex(II.Refused, "slim is"):
            II.same_base(self.pv, self.iso, self.sl)

    def test_another_image_is_refused(self):
        wr(self.img, "other\n")
        with self.assertRaisesRegex(II.Refused, "neither image"):
            II.same_base(self.pv, self.iso, self.sl, self.img)

    def test_the_cache_is_not_written(self):
        os.remove(os.environ["BKO_DIGEST_CACHE"])
        II.same_base(self.pv, self.iso, self.sl, self.img)
        self.assertFalse(os.path.exists(os.environ["BKO_DIGEST_CACHE"]))


class LayerScript(unittest.TestCase):
    def test_refresh_usage(self):
        e = dict(os.environ, BKO_BIN="/nonexistent", BKO_HOLT_BIN="/nonexistent")
        s = os.path.join(T.STICK, "build-backotter-layer.sh")
        r = subprocess.run(["bash", s, "--refresh-from", "/nonexistent.img", "a", "b", "c"], env=e,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no such parent image", r.stderr)
        r = subprocess.run(["bash", s, "--refresh-from", s, "a", "b"], env=e, capture_output=True, text=True)
        self.assertIn("usage", r.stderr)

    def test_the_refresh_checks_are_the_full_builds(self):
        """The binaries' checks are one code path, called by both (factored out, not copied)."""
        text = T.rd(os.path.join(T.STICK, "build-backotter-layer.sh"))
        for fn, calls in (("check_bin_files", 1), ("install_bins", 2), ("check_bins_run", 2), ("holt_api", 2),
                          ("check_whiteouts", 2), ("pack_layer", 2)):
            self.assertEqual(text.count(f"{fn}() {{"), 1, fn)
            self.assertEqual(len(re.findall(rf"^\s*{fn}\b(?!\(\))", text, re.M)), calls, fn)
        self.assertEqual(text.count("bko-holt --version --json"), 1)
        self.assertEqual(text.count("mksquashfs \"$upper\""), 1)


if __name__ == "__main__":
    unittest.main()
