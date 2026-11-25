"""Content drift on every Linux source (A7, GENERATOR "6"): the recipe each
Linux corpus guest draws (families/linux.py choose()), what each filesystem
holds (content.FS_CAN), that a plan holds only that, the size bound the
choice reads, and that rule 1, with what is planted, refuses none of the
smoke's cases."""

import os
import tempfile
import unittest

import corpus_cases as K
from bs2lib import case as C, content as CT, families as F, generate as G, rules
from bs2lib.families import linux as L

# Each Linux corpus guest's recipe; None where it draws no content drift (why: linux.py).
EXPECTED = {
    "aerynos": "f2fs-1", "alpine324": "alpine-small-1", "alpine324-arm64": "alpine-small-1",
    "anduinos2": "btrfs-1", "arch": "ext-1",
    "arm64-grub-shim": None, "arm64-systemd-boot": None,          # 27 MiB synthetic roots: too small
    "artix-dinit": "jfs-1", "artix-s6": "ext-1", "biglinux": "btrfs-1",
    "bs1-slackware-current": "ext-1", "bs1-ubuntu2610-zfsenc": None,
    "cachyos": "jfs-1", "cachyos-zfs": None,
    "chimera": "xfs-1", "chimera-uefi": "ext-1", "chimera-zfs": None,
    "debian12": "ext-1", "debian13": "btrfs-1", "debian13-arm64": "ext-1", "debian13-stack": "xfs-1",
    "debian13-zfs": None, "debian13-zfs-arm64": None,
    "devuan6": "ext-1-32bit",                                     # ext3 without extra_isize
    "elementary8": "ext-1", "elementary8-luks": "ext-1", "endeavouros": "ext-1", "endeavouros-grub": "ext-1",
    "fedora44": "btrfs-1", "fedora44-arm64": "btrfs-1", "fedora44-raid": "btrfs-1", "fedora44-uefi": "btrfs-1",
    "garuda": "btrfs-1", "gentoo": "ext-1", "gentoo-systemd": "ext-1",
    "guix15": "guix-small-1", "guix15-bios": "guix-small-1", "guix15-luks": "guix-small-1",
    "kali": "ext-1", "kinoite44": None, "leap16": "btrfs-1", "manjaro": "btrfs-1", "microos": "btrfs-1",
    "mint22": "f2fs-1", "mx25": "ext-1-32bit",                    # ext2 without extra_isize
    "neon24": "ext-1", "nixos2605": "bcachefs-1", "omarchy": "btrfs-1",
    "pikaos4": "xfs-1", "pikaos4-luks": "btrfs-1", "pop24": "ext-1",
    "proxmox9": None, "proxmox9-mirror": None,
    "rocky10": "xfs-1", "rocky10-lvm": "xfs-1", "rocky10-raid5": "xfs-1",
    "slackware15": "ext-1", "slackware15-uefi": "xfs-1-32bit",   # XFS without bigtime
    "solus49": "ext-1", "truenas25": None, "tumbleweed": "btrfs-1",
    "ubuntu24": None, "ubuntu24-luks": "ext-1", "ubuntu24-uefi": "ext-1", "ubuntu26-arm64": "ext-1",
    "ubuntu26-luks": "ext-1", "ubuntu26-raid": "ext-1", "ubuntu26-uefi": "ext-1",
    "ubuntu26-zfs": None, "ubuntu26-zfsmirror": None,
    "uc24": None, "uc24-fde": None,
    "vanilla3": "btrfs-1", "void-musl": "btrfs-1", "void-zbm": None, "void-zbm-enc": None,
    "zorin18": "nilfs2-1",
}

FS_CAN = {
    "ext4": {"xattr", "acl", "caps", "chattr", "pre1970", "post2038", "nodes", "rawnames"},
    "ext3": {"xattr", "acl", "caps", "chattr", "pre1970", "nodes", "rawnames"},
    "ext2": {"xattr", "acl", "caps", "chattr", "pre1970", "nodes", "rawnames"},
    "xfs": {"xattr", "acl", "caps", "chattr", "pre1970", "post2038", "nodes", "rawnames"},
    "btrfs": {"xattr", "acl", "caps", "chattr", "pre1970", "post2038", "nodes", "rawnames"},
    "f2fs": {"xattr", "acl", "caps", "chattr", "pre1970", "post2038", "nodes", "rawnames"},
    "jfs": {"xattr", "acl", "caps", "chattr", "post2038", "nodes"},
    "nilfs2": {"chattr", "pre1970", "post2038", "nodes", "rawnames"},
    "bcachefs": {"xattr", "acl", "caps", "chattr", "pre1970", "post2038", "nodes", "rawnames"},
    "vfat": set(),
    "zfs": set(),       # A3's
}

Y2038_NS = (1 << 31) * 10**9


def holds_only(test, entries, can, what):
    """ENTRIES plant nothing beyond CAN."""
    for e in entries:
        xs = set(e.get("xattrs") or {})
        if "nodes" not in can:
            test.assertNotIn(e["kind"], ("chr", "blk"), what)
        if "xattr" not in can:
            test.assertNotIn("user.bs2", xs, what)
        if "acl" not in can:
            test.assertFalse({"system.posix_acl_access", "system.posix_acl_default"} & xs, what)
        if "caps" not in can:
            test.assertNotIn("security.capability", xs, what)
        if "chattr" not in can:
            test.assertNotIn("flags", e, what)
        mt = e.get("mtime_ns")
        if mt is not None:
            if "pre1970" not in can:
                test.assertGreaterEqual(mt, 0, what)
            if "post2038" not in can:
                test.assertLess(mt, Y2038_NS, what)
        if "rawnames" not in can:
            bytes.fromhex(e["path"]).decode("utf-8")   # raises: a name the filesystem refuses


class TestRecipes(unittest.TestCase):
    def test_each_linux_guest(self):
        here = set(K.linux_guests())
        got = {g: F.hooks(g, K.dump(g)).recipe(K.dump(g)) for g in sorted(here & set(EXPECTED))}
        self.assertEqual(got, {g: v for g, v in EXPECTED.items() if g in here})
        self.assertGreaterEqual(len(got), 60)

    def test_why_not(self):
        why = {g: L.choose(K.dump(g), (CT.srv_mount(K.dump(g)) or {}).get("fstype"))[1] for g in EXPECTED if g in K.linux_guests()}
        self.assertIn("ostree", why["kinoite44"])
        self.assertIn("no mounted filesystem holds /srv", why["uc24"])
        self.assertIn("A3", why["debian13-zfs"])
        self.assertIn("rule 1", why["arm64-grub-shim"])
        for g, r in EXPECTED.items():
            if g in why and F.family_of(g, K.dump(g)).name == "linux":
                self.assertEqual(why[g] is None, r is not None, g)

    def test_fs_can(self):
        for fs, want in FS_CAN.items():
            self.assertEqual(CT.fs_can_of(fs), want, fs)

    def test_generic_recipes(self):
        for name, r in CT.RECIPES.items():
            self.assertEqual({k: v for k, v in r.items() if k not in ("fs", "drop")}, CT.SHAPE, name)
            self.assertTrue(set(r["fs"]) <= set(CT.FS_CAN), name)
            self.assertEqual(L.BY_FS.get(r["fs"][0]), name.replace("-32bit", ""), name)
        # Every filesystem generic Linux names has a recipe, and no recipe is a family's too.
        self.assertTrue(set(L.BY_FS.values()) <= set(CT.RECIPES))
        self.assertFalse(set(CT.RECIPES) & set(F.REGISTRY.recipes))

    def test_plans_hold_only_what_the_filesystem_does(self):
        for name, r in sorted(CT.RECIPES.items()):
            for fs in r["fs"]:
                can = CT.fs_can_of(fs) - set(r.get("drop") or ())
                self.assertIn("nodes", can)
                for seed in range(12):
                    es = CT.plan({"recipe": name, "seed": seed}, fs)
                    holds_only(self, es, can, (name, fs, seed))
                    # The same entries and indexes as on ext4 (deep_planned() reads ext4's
                    # plan, and an "only" must mean the same entries on each).
                    self.assertEqual([(e["i"], e["kind"]) for e in es],
                                     [(e["i"], e["kind"]) for e in CT.plan({"recipe": name, "seed": seed}, "ext4")], (name, fs, seed))

    def test_jfs_names_are_utf8(self):
        ext = CT.plan({"recipe": "jfs-1", "seed": 3}, "ext4")
        jfs = CT.plan({"recipe": "jfs-1", "seed": 3}, "jfs")
        self.assertEqual([(e["i"], e["kind"]) for e in ext], [(e["i"], e["kind"]) for e in jfs])
        raw = [bytes.fromhex(e["path"]) for e in ext]
        self.assertTrue(any(b"\xff" in p for p in raw))
        for p in (bytes.fromhex(e["path"]) for e in jfs):
            p.decode("utf-8")
        self.assertEqual(len({e["path"] for e in jfs}), len(jfs))

    def test_thirty_two_bit_times(self):
        for name, fs in (("ext-1-32bit", "ext4"), ("xfs-1-32bit", "xfs")):
            for seed in range(20):
                self.assertTrue(all((e.get("mtime_ns") or 0) < Y2038_NS for e in CT.plan({"recipe": name, "seed": seed}, fs)))
        self.assertTrue(any((e.get("mtime_ns") or 0) >= Y2038_NS for e in CT.plan({"recipe": "xfs-1", "seed": 0}, "xfs")))

    def test_bound(self):
        for name in sorted(CT.RECIPES) + ["alpine-small-1", "guix-small-1"]:
            b, n = CT.bound(name)
            for seed in range(100):
                got = CT.sizes(CT.plan({"recipe": name, "seed": seed}, "ext4"))
                self.assertLessEqual(got[0], b, (name, seed))
                self.assertLessEqual(got[1], n, (name, seed))

    def test_plant_never_follows_a_symlink_to_srv(self):
        with tempfile.TemporaryDirectory() as t:
            root, elsewhere = os.path.join(t, "root"), os.path.join(t, "elsewhere")
            os.makedirs(root)
            os.makedirs(elsewhere)
            os.symlink(elsewhere, os.path.join(root, "srv"))
            # Directories only, without their ACLs (this may run where there are no xattrs).
            es = [dict(e, xattrs={}) for e in CT.plan({"recipe": "ext-1", "seed": 1}, "ext4") if e["kind"] == "dir"][:3]
            fails = CT.plant(root, es)
            self.assertEqual(len(fails), 1)
            self.assertEqual(fails[0][0], -1)
            self.assertIn("never followed", fails[0][1])
            self.assertIn("EXDEV", fails[0][1])
            self.assertEqual(os.listdir(elsewhere), [])
            os.unlink(os.path.join(root, "srv"))
            self.assertEqual(CT.plant(root, es), [])
            self.assertTrue(os.path.isdir(os.path.join(root, "srv", "bs2", "a")))

    def test_plant_follows_a_relative_link_inside_the_source(self):
        # ABRoot's layout: /srv -> var/srv, /var a filesystem of its own.
        with tempfile.TemporaryDirectory() as t:
            root = os.path.join(t, "root")
            os.makedirs(os.path.join(root, "var"))
            os.symlink("var/srv", os.path.join(root, "srv"))
            es = [dict(e, xattrs={}) for e in CT.plan({"recipe": "btrfs-1", "seed": 2}, "btrfs") if e["kind"] == "dir"][:3]
            self.assertEqual(CT.plant(root, es), [])
            self.assertTrue(os.path.isdir(os.path.join(root, "var", "srv", "bs2", "a")))
            self.assertTrue(os.path.islink(os.path.join(root, "srv")))
            CT.change(root, {"seed": 5}, 1)
            CT.change(root, {"seed": 5}, 2)
            self.assertEqual(sorted(os.listdir(os.path.join(root, "var", "srv", "bs2", "rounds"))), ["changing", "r1", "r2"])
            CT.change(root, {"seed": 5}, 3)   # r1 goes
            self.assertEqual(sorted(os.listdir(os.path.join(root, "var", "srv", "bs2", "rounds"))), ["changing", "r2", "r3"])
            self.assertEqual(CT.deep_record(root), {})   # no deep tree planted here, and none outside
            # A link whose .. leaves the source, or a chain of them, is refused.
            os.unlink(os.path.join(root, "srv"))
            os.symlink("../../elsewhere", os.path.join(root, "srv"))
            os.makedirs(os.path.join(t, "elsewhere"))
            fails = CT.plant(root, es)
            self.assertEqual([i for i, _ in fails], [-1])
            self.assertIn("EXDEV", fails[0][1])
            self.assertEqual(os.listdir(os.path.join(t, "elsewhere")), [])
            os.unlink(os.path.join(root, "srv"))
            os.symlink("var/../srv2", os.path.join(root, "srv"))
            os.symlink("srv", os.path.join(root, "srv2"))
            self.assertEqual([i for i, _ in CT.plant(root, es)], [-1])

    def test_abroot_srv_is_on_var(self):
        d = K.dump("vanilla3")
        h = F.hooks("vanilla3", d)
        self.assertEqual(h.srv_dir(d), "/var/srv/bs2")
        self.assertEqual(CT.srv_mount(d)["target"], "/var")
        self.assertEqual(CT.srv_mount(K.dump("debian13"))["target"], "/")
        c = next(c for c in K.cases("vanilla3") if c.get("content"))
        self.assertEqual(list(G.planted_of(d, c["content"])), ["/var"])

    # fsmeta.py leaves out the deep tree where the source really has it: on
    # vanilla3 srv/bs2/deep0 is only the srv link's way there, which os.walk
    # never takes, so that skip missed the tree and the record of the source
    # died past PATH_MAX (run slim-after, vanilla3-3d8bc8ee41a0850c).
    def test_fsmeta_skips_follow_the_family_srv(self):
        c = next(c for c in K.cases("vanilla3") if c.get("content"))["content"]
        self.assertEqual(CT.fsmeta_skips(K.dump("vanilla3"), c), ["var/srv/bs2/deep0"])
        self.assertEqual(CT.fsmeta_skips(K.dump("debian13"), c), ["srv/bs2/deep0"])
        self.assertEqual(CT.fsmeta_skips(K.dump("vanilla3"), None), [])
        self.assertEqual(CT.fsmeta_skips(K.dump("vanilla3"), {"seed": 1}), [])   # no recipe, no deep tree

    def test_apply_sets_bs2_fsmeta(self):
        import subprocess
        import sys
        host = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bs2-host")
        src = os.path.join(K.CORPUS, "vanilla3", "src.json")
        content = next(c for c in K.cases("vanilla3") if c.get("content"))
        bare = next(c for c in K.cases("vanilla3") if not c.get("content"))
        with tempfile.TemporaryDirectory() as t:
            def fsmeta(case):
                C.write(f"{t}/c.case", case)
                r = subprocess.run([sys.executable, host, "apply", f"{t}/c.case", src, f"{t}/out.json"], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                line = [l for l in r.stdout.splitlines() if l.startswith("BS2_FSMETA=")]
                self.assertEqual(len(line), 1, r.stdout)
                # As ht1-bs2.sh's bs2_after_source sources it.
                b = subprocess.run(["bash", "-c", line[0] + '\nfor x in "${BS2_FSMETA[@]}"; do echo "$x"; done'], capture_output=True, text=True)
                return b.stdout.split("\n")[:-1]
            self.assertEqual(fsmeta(content), ["--skip", "var/srv/bs2/deep0"])
            self.assertEqual(fsmeta(bare), [])

    def abroot_deep(self, t):
        """An ABRoot-shaped root (srv -> var/srv) with vanilla3's deep tree planted."""
        root = os.path.join(t, "root")
        os.makedirs(os.path.join(root, "var"))
        os.symlink("var/srv", os.path.join(root, "srv"))
        content = next(c for c in K.cases("vanilla3") if c.get("content"))["content"]
        deep = set(CT.deep_planned(content))
        es = [dict(e, xattrs={}) for e in CT.plan(content, "btrfs") if e["path"] in deep]
        self.assertEqual(CT.plant(root, es), [])
        return root, CT.fsmeta_skips(K.dump("vanilla3"), content)

    def test_the_skip_is_where_fsmeta_walks(self):
        # fsmeta.py's walk (os.walk, links not followed) meets the tree at the skip, never under srv/.
        with tempfile.TemporaryDirectory() as t:
            root, skips = self.abroot_deep(t)
            walked, left = [], []
            for d, ns, _ in os.walk(root, onerror=self.fail):
                rel = [os.path.relpath(os.path.join(d, n), root) for n in ns]
                left += [r for r in rel if r in skips]
                ns[:] = [n for n, r in zip(ns, rel) if r not in skips]
                walked += [r for r in rel if r not in skips]
            self.assertEqual(left, ["var/srv/bs2/deep0"])
            self.assertIn("var/srv/bs2", walked)
            self.assertFalse([w for w in walked if w.startswith("srv/") or "deep0" in w])

    @unittest.skipUnless(os.path.exists("/proc/self/mounts"), "fsmeta.py records on Linux")
    def test_fsmeta_records_vanilla3_content(self):
        import errno
        import importlib.util
        spec = importlib.util.spec_from_file_location("fsmeta", os.path.join(K.BS2, "..", "compare", "fsmeta.py"))
        fm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fm)
        with tempfile.TemporaryDirectory() as t:
            root, skips = self.abroot_deep(t)
            rec = fm.record(root, skips)
            self.assertIn("var/srv/bs2", rec)
            self.assertEqual(rec["srv"]["type"], "symlink")
            self.assertFalse([k for k in rec if k.startswith("var/srv/bs2/deep0")])
            with self.assertRaises(OSError) as x:
                fm.record(root, ["srv/bs2/deep0"])   # the old, literal skip
            self.assertEqual(x.exception.errno, errno.ENAMETOOLONG)

    def test_symlink_limits(self):
        self.assertEqual(CT.fs_symlink_max("f2fs", 4096), 4095)
        self.assertEqual(CT.fs_symlink_max("nilfs2", 1024), 1023)
        self.assertIsNone(CT.fs_symlink_max("jfs", 4096))


class TestSmoke(unittest.TestCase):
    """The smoke's cases (test_families.TestSmoke), with what each plants:
    most draw content where there's a recipe, and rule 1, given the planted
    share (generate.planted_of), refuses none of them."""

    def test_rule1_with_what_is_planted(self):
        for g in K.linux_guests():
            d = K.dump(g)
            h = F.hooks(g, d)
            cs = K.cases(g)
            recipe = h.recipe(d)
            with_content = [c for c in cs if c.get("content")]
            if recipe is None:
                self.assertEqual(with_content, [], g)
                continue
            self.assertGreater(len(with_content), 150, g)
            self.assertTrue(all(c["content"]["recipe"] == recipe for c in with_content), g)
            for c in with_content:
                planted = G.planted_of(d, c["content"])
                self.assertTrue(planted, g)
                bad = [b for b in rules.check(d, C.replay(c, d), c["edits"], planted, h) if b[0] == 1]
                self.assertEqual(bad, [], f"{g} seed {c['seed']}")


if __name__ == "__main__":
    unittest.main()
