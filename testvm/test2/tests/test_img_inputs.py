"""I1's unit tests (docs/design/img-speed.md §3, C7, §14 row I1): stick/img_inputs.py's digests,
lint, tree listing and provenance, and img-one.sh / make-img-bsd.sh's build-time identity checks and
guest lock, in a fixture world (BKO_WORK, BKO_IMG_DIR and a test T2_HOST_ROOT).

No host, no VM.  Run from the worktree: python3 -m unittest discover testvm/test2/tests.
"""

import fcntl
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
STICK = os.path.join(WT, "stick")
BASH = shutil.which("bash")

_spec = importlib.util.spec_from_file_location("img_inputs", os.path.join(STICK, "img_inputs.py"))
II = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(II)

ENV_KEYS = ("BKO_WORK", "S", "BKO_BIN", "BKO_HOLT_BIN", "SL", "ISO", "BKO_DIGEST_CACHE", "BKO_IMG_HOST_TOOLS",
            "BKO_IMG_DIR", "T2_HOST_ROOT", "BKO_PLAN_BUILDER", "BKO_BUILDER_ID", "BKO_TREE_OUT")


def rd(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def wr(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


def copy_repo(dst):
    """The repo's stick/ and testvm/vm-run under DST, as a host's copy has them."""
    shutil.copytree(STICK, os.path.join(dst, "stick"), ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    os.makedirs(os.path.join(dst, "testvm"))
    shutil.copy2(os.path.join(WT, "testvm", "vm-run"), os.path.join(dst, "testvm", "vm-run"))


def make_world(root, gen="ab" * 32):
    """A host: BKO_WORK with bko-bin, holt-bin, media and the snapshot builders, its stick/ copy."""
    w = os.path.join(root, "work")
    copy_repo(w)
    for p in II.PLATFORMS:
        wr(os.path.join(w, "bko-bin", p, "restic-bko"), f"restic {p}\n", 0o755)
        wr(os.path.join(w, "bko-bin", p, "rustic-bko"), f"rustic {p}\n", 0o755)
        wr(os.path.join(w, "holt-bin", p, "bko-holt"), f"holt {p}\n", 0o755)
        wr(os.path.join(w, "holt-bin", p, "COMMIT"), "cd" * 20 + "\n")
    wr(os.path.join(w, "bko-bin", "BUILD-INFO"), "bko-bin set from round rel99, assembled now\n")
    wr(os.path.join(w, "img-media", "netbsd", "11.1", "base.tar.xz"), "base\n")
    wr(os.path.join(w, "img-media", "netbsd", "MEDIA"), "11.1/base.tar.xz\n")
    set_generation(w, "netbsd11", gen)
    os.makedirs(os.path.join(w, "vms", "freebsd15-builder"), exist_ok=True)
    wr(os.path.join(w, "vms", "freebsd15-builder", "freebsd15.qcow2"), "builder disk\n")
    wr(os.path.join(w, "vms", "freebsd15-builder", "fingerprint"), "15.1-RELEASE-p3\nSHA256 (/usr/sbin/makefs) = 00\n")
    wr(os.path.join(w, "server", "slim", "report.txt"), f"Source ISO: {w}/server/u.iso\n")
    for n in II.SLIM_FILES:
        wr(os.path.join(w, "server", "slim", n), n + "\n")
    wr(os.path.join(w, "server", "u.iso"), "iso\n")
    return w


def set_generation(w, g, gen, disk_text=None):
    """vms/G as vm-update leaves it: image-state's generation, image-stat.json's stat of the disk."""
    gdir = os.path.join(w, "vms", g)
    disk = os.path.join(gdir, g + ".qcow2")
    if disk_text is not None or not os.path.exists(disk):
        wr(disk, disk_text or f"{g} disk\n")
    st = os.stat(disk)
    wr(os.path.join(gdir, "image-state"), json.dumps({"generation": gen, "sha256": {}}))
    wr(os.path.join(gdir, "image-stat.json"), json.dumps(
        {"generation": gen, "disks": {g + ".qcow2": {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
                                                     "inode": st.st_ino}}}))


class EnvCase(unittest.TestCase):
    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="t2-i1-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        saved = {k: os.environ.get(k) for k in ENV_KEYS}

        def restore():
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.addCleanup(restore)
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        os.environ["T2_HOST_ROOT"] = os.path.join(self.t, "host-root")
        os.environ["BKO_IMG_HOST_TOOLS"] = json.dumps({"dosfstools": "4.2-1.2", "mtools": "4.0.48-1",
                                                       "xorriso": "1.5.6", "squashfs-tools": "4.6.1"})

    def use(self, w):
        os.environ["BKO_WORK"] = w
        os.environ["S"] = os.path.join(w, "stick")
        os.environ["BKO_DIGEST_CACHE"] = os.path.join(w, ".cache.json")

    def dig(self, platform):
        return II.digest(platform)


class Digests(EnvCase):
    """The three digests: stable, each input class moving only its own, the same on two hosts."""

    def setUp(self):
        super().setUp()
        self.w = make_world(os.path.join(self.t, "a"))
        self.use(self.w)

    def three(self, platform):
        d = self.dig(platform)
        return d["base"]["digest"], d["bins"]["digest"], d["tests"]["digest"]

    def assert_moves(self, platform, change, which):
        before = self.three(platform)
        change()
        after = self.three(platform)
        names = ("base", "bins", "tests")
        moved = [n for n, a, b in zip(names, before, after) if a != b]
        self.assertEqual(moved, [which], f"{platform}: {change.__name__} moved {moved}, not [{which}]")

    def test_stable_across_two_runs(self):
        for p in II.PLATFORMS:
            self.assertEqual(self.three(p), self.three(p), p)

    def test_a_base_stick_file_moves_base_only(self):
        def edit_build_sh():
            with open(os.path.join(self.w, "stick", "netbsd", "build.sh"), "a") as f:
                f.write("# changed\n")
        self.assert_moves("netbsd", edit_build_sh, "base")

    def test_another_platforms_file_moves_nothing(self):
        before = self.three("haiku")
        with open(os.path.join(self.w, "stick", "netbsd", "build.sh"), "a") as f:
            f.write("# changed\n")
        with open(os.path.join(self.w, "stick", "layer", "etc", "sudoers.d", "backotter"), "a") as f:
            f.write("# changed\n")
        self.assertEqual(self.three("haiku"), before)

    def test_a_none_file_moves_nothing(self):
        before = [self.three(p) for p in II.PLATFORMS]
        with open(os.path.join(self.w, "stick", "netbsd", "README.md"), "a") as f:
            f.write("more\n")
        with open(os.path.join(self.w, "stick", "img-xcopy.sh"), "a") as f:
            f.write("# changed\n")
        self.assertEqual([self.three(p) for p in II.PLATFORMS], before)

    def test_a_linux_layer_file_moves_linux_base(self):
        def edit_layer():
            with open(os.path.join(self.w, "stick", "layer", "etc", "sudoers.d", "backotter"), "a") as f:
                f.write("# changed\n")
        self.assert_moves("linux", edit_layer, "base")

    def test_the_media_move_base_only(self):
        def new_media():
            wr(os.path.join(self.w, "img-media", "netbsd", "11.1", "base.tar.xz"), "base, patched\n")
        self.assert_moves("netbsd", new_media, "base")

    def test_a_builder_generation_moves_base_only(self):
        def vm_update():
            set_generation(self.w, "netbsd11", "ef" * 32, disk_text="netbsd11 disk, updated\n")
        self.assert_moves("netbsd", vm_update, "base")

    def test_a_disk_changed_since_its_generation_moves_base(self):
        def touched():
            with open(os.path.join(self.w, "vms", "netbsd11", "netbsd11.qcow2"), "a") as f:
                f.write("written by a boot\n")
        self.assert_moves("netbsd", touched, "base")
        self.assertEqual(II.identity("netbsd")["kind"], "qcow2")

    def test_host_tools_move_base_only(self):
        def new_mtools():
            os.environ["BKO_IMG_HOST_TOOLS"] = json.dumps({"dosfstools": "4.2-1.2", "mtools": "4.0.49-1"})
        self.assert_moves("openbsd", new_mtools, "base")

    def test_slim_moves_linux_base_only(self):
        def new_slim():
            wr(os.path.join(self.w, "server", "slim", "vmlinuz"), "another kernel\n")
        self.assert_moves("linux", new_slim, "base")

    def test_the_iso_moves_linux_base_only(self):
        def new_iso():
            wr(os.path.join(self.w, "server", "u.iso"), "another iso\n")
        self.assert_moves("linux", new_iso, "base")

    def test_a_binary_moves_bins_only(self):
        def new_holt():
            wr(os.path.join(self.w, "holt-bin", "dragonfly", "bko-holt"), "holt, rebuilt\n", 0o755)
        self.assert_moves("dragonfly", new_holt, "bins")

    def test_a_check_moves_tests_only(self):
        def edit_checks():
            with open(os.path.join(self.w, "stick", "illumos", "checks.sh"), "a") as f:
                f.write("# one more\n")
        self.assert_moves("illumos", edit_checks, "tests")

    def test_boot_test_moves_every_tests_digest(self):
        before = {p: self.three(p) for p in II.PLATFORMS}
        with open(os.path.join(self.w, "stick", "boot-test.py"), "a") as f:
            f.write("# changed\n")
        for p in II.PLATFORMS:
            a = self.three(p)
            self.assertEqual((a[0], a[1]), before[p][:2], p)
            self.assertNotEqual(a[2], before[p][2], p)

    def test_the_mode_list_is_in_tests(self):
        modes = self.dig("linux")["tests"]["modes"]
        self.assertEqual(modes, ["bios", "uefi", "uefi-secureboot", "cdrom"])
        self.assertEqual(self.dig("freebsd")["tests"]["modes"], ["bios", "uefi"])

    def test_two_hosts_with_the_same_inputs_agree(self):
        w2 = make_world(os.path.join(self.t, "b"))
        for p in II.PLATFORMS:
            self.use(self.w)
            a = self.three(p)
            self.use(w2)
            self.assertEqual(self.three(p), a, p)

    def test_freebsd_builder_runs_leave_the_digest(self):
        """The builder's qcow2 is written at every boot: two builds with no builder change agree."""
        a = self.three("freebsd")
        with open(os.path.join(self.w, "vms", "freebsd15-builder", "freebsd15.qcow2"), "a") as f:
            f.write("a boot's writes\n")
        self.assertEqual(self.three("freebsd"), a)
        wr(os.path.join(self.w, "vms", "freebsd15-builder", "fingerprint"), "15.1-RELEASE-p4\n")
        self.assertNotEqual(self.three("freebsd")[0], a[0])

    def test_the_digest_reads_only(self):
        img = os.path.join(self.w, "images")
        os.makedirs(img)
        for p in II.PLATFORMS:
            self.dig(p)
        self.assertEqual(os.listdir(img), [])

    def test_the_cache_is_used(self):
        self.dig("linux")
        cache = json.loads(rd(os.path.join(self.w, ".cache.json")))
        self.assertTrue(any(k.endswith("u.iso") for k in cache))


class Lint(EnvCase):
    def test_the_repo_passes(self):
        os.environ["S"] = STICK
        self.assertEqual(II.lint(WT), [])

    def test_a_new_unlisted_stick_file_fails(self):
        root = os.path.join(self.t, "r")
        copy_repo(root)
        wr(os.path.join(root, "stick", "netbsd", "new-helper.sh"), "#!/bin/sh\n")
        wr(os.path.join(root, "stick", "brand-new.sh"), "#!/bin/sh\n")
        bad = II.lint(root)
        self.assertEqual(len(bad), 1, bad)       # netbsd's ** takes the first
        self.assertIn("stick/brand-new.sh", bad[0])

    def test_a_none_row_needs_a_reason(self):
        root = os.path.join(self.t, "r")
        copy_repo(root)
        with open(os.path.join(root, "stick", "img-inputs.tsv"), "a") as f:
            f.write("-\tstick/brand-new.sh\tnone\n")
        with self.assertRaises(II.Refused):
            II.lint(root)

    def test_a_build_file_must_be_base(self):
        root = os.path.join(self.t, "r")
        copy_repo(root)
        with open(os.path.join(root, "stick", "img-inputs.tsv"), "a") as f:
            f.write("haiku\tstick/haiku/mbr.py\tnone\tpretend\n")
        bad = II.lint(root)
        self.assertTrue(any("stick/haiku/mbr.py" in b and "BUILD_FILES" in b for b in bad), bad)

    def test_the_lint_script(self):
        r = subprocess.run([BASH, os.path.join(STICK, "img-digest.sh"), "--lint"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TreeList(unittest.TestCase):
    def tar(self, build):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as t:
            build(t)
        buf.seek(0)
        return buf

    @staticmethod
    def add(t, name, kind=tarfile.REGTYPE, data=b"", mode=0o644, **kw):
        ti = tarfile.TarInfo(name)
        ti.type, ti.mode, ti.uid, ti.gid = kind, mode, kw.pop("uid", 0), kw.pop("gid", 0)
        for k, v in kw.items():
            setattr(ti, k, v)
        if kind == tarfile.REGTYPE:
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
        else:
            t.addfile(ti)

    def test_the_listing(self):
        def build(t):
            self.add(t, "./root", tarfile.DIRTYPE, mode=0o755)
            self.add(t, "./root/etc/fstab", data=b"label / ufs\n", uid=0, gid=5)
            self.add(t, "./root/conf/base/etc/fstab", tarfile.LNKTYPE, linkname="./root/etc/fstab")
            self.add(t, "./root/bin/sh", data=b"sh", mode=0o4755,
                     pax_headers={"SCHILY.fflags": "schg", "SCHILY.xattr.user.x": "v"})
            self.add(t, "./root/link", tarfile.SYMTYPE, linkname="etc/fstab", mode=0o777)
            self.add(t, "./root/dev/null", tarfile.CHRTYPE, devmajor=2, devminor=2)
            self.add(t, "./other/x", data=b"x")
        lines = II.tree_list(self.tar(build))
        rows = {(ln.split("\t")[0], ln.split("\t")[1]): ln.split("\t") for ln in lines}
        self.assertEqual(lines, sorted(lines))
        fstab = rows[("root", "/etc/fstab")]
        self.assertEqual(fstab[2:6], ["f", "0644", "0:5", "12"])
        self.assertEqual(rows[("root", "/conf/base/etc/fstab")][6], fstab[6])
        self.assertEqual(rows[("root", "/bin/sh")][3], "4755")
        self.assertIn("flags=schg", rows[("root", "/bin/sh")][7])
        self.assertIn("xattr:user.x=", rows[("root", "/bin/sh")][7])
        self.assertEqual(rows[("root", "/link")][2:3] + rows[("root", "/link")][6:7], ["l", "etc/fstab"])
        self.assertEqual(rows[("root", "/dev/null")][6], "2,2")
        self.assertEqual(rows[("root", "/")][2], "d")
        self.assertIn(("other", "/x"), rows)
        no_owner = II.tree_list(self.tar(build), no_owner=True)
        self.assertTrue(all(ln.split("\t")[4] == "-" for ln in no_owner))

    def test_the_os_manifest_leaves_the_swap_list_out(self):
        swaps = II.swap_rows("freebsd", WT)
        base = ["root\t/bin/sh\tf\t0755\t0:0\t2\taa\t-",
                "root\t/usr/local/bin/bko-holt\tf\t0755\t0:0\t5\tbb\t-",
                "root\t/usr/local/bin/restic-bko\tf\t0755\t0:0\t5\tcc\t-",
                "root\t/.disk/backotter.json\tf\t0644\t0:0\t5\tdd\t-",
                "root\t/etc/fstab\tf\t0644\t0:0\t5\tee\t-",
                "root\t/conf/base/etc/fstab\tf\t0644\t0:0\t5\tee\t-"]
        m, missing, trees = II.os_manifest(base, swaps)
        self.assertEqual((missing, trees), ([], ["root"]))
        swapped = [ln.replace("\tbb\t", "\tb2\t").replace("\tee\t", "\te2\t") for ln in base]
        self.assertEqual(II.os_manifest(swapped, swaps)[0], m)
        other = [ln.replace("\taa\t", "\ta2\t") for ln in base]
        self.assertNotEqual(II.os_manifest(other, swaps)[0], m)
        self.assertEqual(II.os_manifest(base[:1], swaps)[1],
                         ["root:/usr/local/bin/restic-bko", "root:/usr/local/bin/bko-holt",
                          "root:/.disk/backotter.json", "root:/etc/fstab", "root:/conf/base/etc/fstab"])

    def test_every_platform_has_a_swap_list(self):
        for p in II.PLATFORMS:
            rows = II.swap_rows(p, WT)
            self.assertTrue(any(r["kind"] == "bin" for r in rows), p)
            self.assertTrue(any(r["kind"] == "marker" for r in rows), p)


class ImgOne(EnvCase):
    """img-one.sh with stub builders, in a fixture world: provenance, and the refusals of C7."""

    def setUp(self):
        super().setUp()
        self.w = make_world(self.t)
        self.use(self.w)
        self.i = os.path.join(self.t, "images")
        self.path = os.path.join(self.t, "path")
        s = os.path.join(self.w, "stick")
        self.calls = os.path.join(self.t, "calls")
        # The stub build: one image and a listing, both copies; $HOOK runs between them.
        wr(os.path.join(s, "make-img-bsd.sh"),
           '#!/bin/sh\nif [ "$1" = --test ]; then d=$3; else d=$2; fi\n'
           'echo "$BKO_BUILDER_ID" >>' + self.calls + '\n'
           'printf "root\\t/\\td\\t0755\\t0:0\\t0\\t-\\t-\\nroot\\t/usr/local/bin/bko-holt\\tf\\t0755\\t0:0\\t1\\taa\\t-\\n" >"$BKO_TREE_OUT"\n'
           'echo image >"$d/x.img"\n[ -z "$HOOK" ] || sh -c "$HOOK"\n'
           'echo "Wrote $d/x.img (1 MiB), image 0123abcd-0000-4000-8000-000000000000"\n', 0o755)
        wr(os.path.join(s, "boot-test.py"), "print('  ok   tools  all')\n")
        wr(os.path.join(s, "compress-images.sh"), "#!/bin/sh\nexit 0\n", 0o755)
        wr(os.path.join(self.path, "flock"), "#!/bin/sh\nexit 0\n", 0o755)
        wr(os.path.join(self.path, "timeout"), '#!/bin/sh\nshift\nexec "$@"\n', 0o755)
        wr(os.path.join(self.path, "ss"), "#!/bin/sh\nexit 0\n", 0o755)
        wr(os.path.join(self.path, "setsid"), "#!/bin/sh\nexit 0\n", 0o755)
        # The FreeBSD builder's ssh: its fingerprint from $FP (a file), the rest succeeds.
        self.fp = os.path.join(self.t, "fp")
        wr(self.fp, "15.1-RELEASE-p3\n15.1-RELEASE-p3\nSHA256 (/usr/sbin/makefs) = 00\n")
        wr(os.path.join(self.path, "ssh"),
           '#!/bin/sh\nfor a; do last=$a; done\ncase $last in *freebsd-version*) cat "$FP" ;; esac\nexit 0\n', 0o755)
        wr(os.path.join(self.w, "vms", "freebsd15-builder", "vm-run-options"), "--uefi\n")

    def run_one(self, platform, **env):
        e = dict(os.environ, BKO_IMG_DIR=self.i, FP=self.fp, HOOK="", PATH=self.path + ":" + os.environ["PATH"])
        e.update(env)
        return subprocess.run([BASH, os.path.join(self.w, "stick", "img-one.sh"), platform], env=e,
                              capture_output=True, text=True)

    def prov(self, platform):
        return json.loads(rd(os.path.join(self.i, platform, "provenance.json")))

    def test_provenance_of_a_full_build(self):
        r = self.run_one("netbsd", BKO_IMG_RUN="img-test")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(rd(os.path.join(self.i, "netbsd", "done.exit")), "0\n")
        p = self.prov("netbsd")
        self.assertEqual((p["schema"], p["platform"], p["mode"], p["parent"]), (1, "netbsd", "full", None))
        self.assertEqual(p["built"]["run"], "img-test")
        self.assertEqual(p["release"]["file"], "x.img")
        self.assertEqual(p["test"]["file"], "test/x.img")
        self.assertEqual(len(p["release"]["sha256"]), 64)
        self.assertEqual(p["release"]["image_id"], "0123abcd-0000-4000-8000-000000000000")
        self.assertEqual(p["base"]["digest"], II.digest("netbsd")["base"]["digest"])
        self.assertEqual(p["bins"]["digest"], II.digest("netbsd")["bins"]["digest"])
        self.assertEqual(p["bins"]["bko_bin_round"], "rel99")
        self.assertEqual(p["bins"]["holt_commit"], "cd" * 20)
        self.assertEqual(p["tests"]["boot"], {"bios": 0, "uefi": 0})
        self.assertEqual(len(p["base"]["os_manifest"]), 64)
        self.assertEqual(p["base"]["inputs"]["builder"]["kind"], "generation")
        self.assertNotIn(".inputs-start.json", os.listdir(os.path.join(self.i, "netbsd")))
        self.assertIn("provenance: base", rd(os.path.join(self.i, "netbsd", "result.txt")))
        # make-img-bsd.sh got the identity to recheck, for both copies.
        ids = rd(self.calls).splitlines()
        self.assertEqual(len(ids), 2)
        self.assertEqual(json.loads(ids[0])["generation"], "ab" * 32)

    def test_a_vm_update_during_the_build_refuses(self):
        hook = f"python3 -c 'import sys; sys.path.insert(0, \"{HERE}\"); import test_img_inputs as t; " \
               f"t.set_generation(\"{self.w}\", \"netbsd11\", \"ef\" * 32, \"updated\\n\")'"
        r = self.run_one("netbsd", HOOK=hook)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(rd(os.path.join(self.i, "netbsd", "done.exit")), "1\n")
        res = rd(os.path.join(self.i, "netbsd", "result.txt"))
        self.assertIn("inputs moved", res)
        self.assertIn("the builder moved", res)
        self.assertFalse(os.path.exists(os.path.join(self.i, "netbsd", "provenance.json")))

    def test_a_stick_change_during_the_build_refuses(self):
        r = self.run_one("netbsd", HOOK=f"echo '# synced' >>{self.w}/stick/netbsd/build.sh")
        self.assertEqual(r.returncode, 1)
        self.assertIn("stick/netbsd/build.sh", rd(os.path.join(self.i, "netbsd", "result.txt")))

    def test_a_vm_update_during_the_boot_tests_writes_no_provenance(self):
        # The guest lock is gone by then: provenance must not record the new generation.
        wr(os.path.join(self.w, "stick", "boot-test.py"),
           "import os, subprocess\nsubprocess.run(os.environ['BOOT_HOOK'], shell=True)\nprint('  ok   tools  all')\n")
        hook = f"python3 -c 'import sys; sys.path.insert(0, \"{HERE}\"); import test_img_inputs as t; " \
               f"t.set_generation(\"{self.w}\", \"netbsd11\", \"ef\" * 32, \"updated\\n\")'"
        r = self.run_one("netbsd", BOOT_HOOK=hook)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(rd(os.path.join(self.i, "netbsd", "done.exit")), "0\n")
        res = rd(os.path.join(self.i, "netbsd", "result.txt"))
        self.assertIn("provenance: FAILED", res)
        self.assertIn("the builder moved", res)
        self.assertFalse(os.path.exists(os.path.join(self.i, "netbsd", "provenance.json")))

    def test_not_the_plans_builder_refuses(self):
        r = self.run_one("netbsd", BKO_PLAN_BUILDER='{"generation":"' + "ef" * 32 + '"}')
        self.assertEqual(r.returncode, 1)
        self.assertIn("not the plan's", rd(os.path.join(self.i, "netbsd", "result.txt")))
        ok = self.run_one("netbsd", BKO_PLAN_BUILDER=II.canon(II.identity("netbsd")))
        self.assertEqual(ok.returncode, 0, rd(os.path.join(self.i, "netbsd", "result.txt")))

    def test_two_freebsd_builds_with_no_builder_change_agree(self):
        os.remove(os.path.join(self.w, "vms", "freebsd15-builder", "fingerprint"))
        r = self.run_one("freebsd")
        self.assertEqual(r.returncode, 0, rd(os.path.join(self.i, "freebsd", "result.txt")))
        self.assertEqual(rd(os.path.join(self.w, "vms", "freebsd15-builder", "fingerprint")), rd(self.fp))
        first = self.prov("freebsd")["base"]["digest"]
        with open(os.path.join(self.w, "vms", "freebsd15-builder", "freebsd15.qcow2"), "a") as f:
            f.write("this boot's writes\n")
        r = self.run_one("freebsd")
        self.assertEqual(r.returncode, 0, rd(os.path.join(self.i, "freebsd", "result.txt")))
        self.assertEqual(self.prov("freebsd")["base"]["digest"], first)
        self.assertEqual(self.prov("freebsd")["base"]["inputs"]["builder"]["kind"], "fingerprint")

    def test_a_freebsd_builder_changed_during_the_build_refuses(self):
        r = self.run_one("freebsd", HOOK=f"echo 15.1-RELEASE-p4 >{self.fp}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("fingerprint changed during the build", rd(os.path.join(self.i, "freebsd", "result.txt")))

    def test_a_freebsd_builder_not_the_plans_refuses(self):
        r = self.run_one("freebsd", BKO_PLAN_BUILDER=II.canon(II.identity("freebsd")), FP=os.devnull)
        self.assertEqual(r.returncode, 1)
        self.assertIn("couldn't fingerprint", rd(os.path.join(self.i, "freebsd", "result.txt")))
        wr(os.path.join(self.t, "fp2"), "15.0-RELEASE\n")
        r = self.run_one("freebsd", BKO_PLAN_BUILDER=II.canon(II.identity("freebsd")), FP=os.path.join(self.t, "fp2"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("not the plan's", rd(os.path.join(self.i, "freebsd", "result.txt")))

    def test_the_images_are_unchanged_by_provenance(self):
        """Every file a pre-I1 img-one.sh wrote is still written, and the image's bytes are the build's."""
        self.run_one("netbsd")
        d = os.path.join(self.i, "netbsd")
        names = set(os.listdir(d)) - {"prev-" + x for x in ()}
        for n in ("x.img", "bko-bin.sha256", "bko-holt.commit", "build.log", "result.txt", "done.exit", "test"):
            self.assertIn(n, names)
        self.assertEqual(names - {"x.img", "bko-bin.sha256", "bko-holt.commit", "build.log", "result.txt",
                                  "done.exit", "test", "compress.log", "provenance.json", "os-tree.txt", "inputs.log"}, set())
        self.assertEqual(rd(os.path.join(d, "x.img")), "image\n")


@unittest.skipUnless(shutil.which("flock") and BASH, "no flock here")
class GuestLock(EnvCase):
    """make-img-bsd.sh holds a snapshot builder's vm-update lock, and rechecks its identity once the VM
    is up: a fake platform whose builder never boots (stub pgrep, ssh, setsid)."""

    def setUp(self):
        super().setUp()
        self.w = os.path.join(self.t, "work")
        s = os.path.join(self.w, "stick")
        os.makedirs(s)
        for f in ("make-img-bsd.sh", "img_inputs.py"):
            shutil.copy2(os.path.join(STICK, f), os.path.join(s, f))
        wr(os.path.join(s, "fakebsd", "build.sh"), "exit 0\n")
        wr(os.path.join(s, "fakebsd", "platform.conf"),
           "BASE_OS=fake BASE_RELEASE=1 CONFIG_PART=3 BUILD_VM=fakevm BUILD_VM_IMAGE=fakevm.qcow2\n"
           "BUILD_VM_PORT=2999 BUILD_VM_MODE=snapshot\nfetch() { MEDIA=(m.img); }\n")
        wr(os.path.join(self.w, "bko-bin", "fakebsd", "restic-bko"), "r\n", 0o755)
        wr(os.path.join(self.w, "holt-bin", "fakebsd", "bko-holt"), "h\n", 0o755)
        set_generation(self.w, "fakevm", "12" * 32)
        wr(os.path.join(self.w, "vms", "fakevm", "id_ed25519"), "key\n")
        self.path = os.path.join(self.t, "path")
        n = os.path.join(self.t, "n")
        # pgrep: free port at the first call, QEMU up at the second, gone after.
        wr(os.path.join(self.path, "pgrep"),
           f'#!/bin/sh\nc=$(cat {n} 2>/dev/null || echo 0); c=$((c + 1)); echo $c >{n}\n[ $c -eq 2 ]\n', 0o755)
        for x in ("ssh", "setsid", "pkill", "timeout"):
            wr(os.path.join(self.path, x), "#!/bin/sh\nexit 0\n", 0o755)
        self.lock = os.path.join(self.w, "vms", "fakevm", ".vm-update.lock")

    def run_bsd(self, **env):
        e = dict(os.environ, BKO_WORK=self.w, PATH=self.path + ":" + os.environ["PATH"])
        e.update(env)
        out = os.path.join(self.t, "out")
        os.makedirs(out, exist_ok=True)
        return subprocess.run([BASH, os.path.join(self.w, "stick", "make-img-bsd.sh"), "fakebsd", out], env=e,
                              capture_output=True, text=True, timeout=120)

    def test_a_held_lock_refuses(self):
        with open(self.lock, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            r = self.run_bsd(BKO_GUEST_LOCK_WAIT="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("a vm-update is working on fakevm", r.stderr)
        self.assertEqual(rd(os.path.join(self.w, "img-media", "fakebsd", "MEDIA")), "m.img\n")

    def test_a_builder_that_moved_by_boot_refuses(self):
        os.environ["S"] = os.path.join(self.w, "stick")
        os.environ["BKO_WORK"] = self.w
        want = II.canon(II.identity("fakebsd"))
        set_generation(self.w, "fakevm", "34" * 32, disk_text="updated\n")
        r = self.run_bsd(BKO_BUILDER_ID=want)
        self.assertEqual(r.returncode, 1)
        self.assertIn("fakevm moved while its VM came up", r.stderr)


class VmUpdateWording(unittest.TestCase):
    def test_the_refusal_names_image_builds(self):
        self.assertIn("an image build that boots it", rd(os.path.join(WT, "testvm", "vm-update")))


if __name__ == "__main__":
    unittest.main()
