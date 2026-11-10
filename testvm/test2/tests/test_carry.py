"""The carry rule (contract 1.6.4) and its git diffs: version-only, affected-v1, dfly-image,
.digest lines, changed paths since a row's tree.  Fixture forks in a temporary BKO_GIT; the
holt examples are this repository's own 617c4de and 620ec60 when its history has them."""

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, "fixtures", "p2"))

from t2lib import carry   # noqa: E402
import world              # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))


def reader(d):
    return lambda p: None if p not in d else d[p].encode()


class VersionLines(unittest.TestCase):
    def check(self, old, new):
        return carry.version_only_files(sorted(set(old) | set(new)), reader(old), reader(new))

    def test_go_version_line(self):
        o = {"internal/global/global.go": 'package global\n\nconst Version = "0.19.1+backotter.4"\n'}
        n = {"internal/global/global.go": 'package global\n\nconst Version = "0.19.1+backotter.5"\n'}
        self.assertEqual(self.check(o, n), (True, []))
        n2 = {"internal/global/global.go": 'package global\n\nconst Version = "0.19.1+backotter.5"\nvar X = 1\n'}
        self.assertFalse(self.check(o, n2)[0])

    def test_markdown_only(self):
        self.assertTrue(self.check({"BACKOTTER.md": "a\n"}, {"BACKOTTER.md": "b\n"})[0])

    def test_cargo_package_version_and_lock(self):
        o = {"Cargo.toml": '[package]\nname = "rustic-rs"\nversion = "0.11.4+backotter.6"\n\n[dependencies]\nx = "1"\n',
             "Cargo.lock": 'version = 4\n\n[[package]]\nname = "rustic-rs"\nversion = "0.11.4+backotter.6"\n\n'
                           '[[package]]\nname = "x"\nversion = "1.0.0"\n'}
        n = {"Cargo.toml": o["Cargo.toml"].replace("backotter.6", "backotter.7"),
             "Cargo.lock": o["Cargo.lock"].replace("backotter.6", "backotter.7")}
        self.assertEqual(self.check(o, n), (True, []))
        # a dependency's version moving in the lock is not version-only
        n2 = dict(n, **{"Cargo.lock": n["Cargo.lock"].replace('"1.0.0"', '"1.0.1"')})
        self.assertFalse(self.check(o, n2)[0])
        # a dependency line in Cargo.toml is not either
        n3 = dict(n, **{"Cargo.toml": n["Cargo.toml"].replace('x = "1"', 'x = "2"')})
        self.assertFalse(self.check(o, n3)[0])
        # a lock change without the package's Cargo.toml version is a dependency change
        self.assertFalse(self.check({"Cargo.lock": o["Cargo.lock"]}, {"Cargo.lock": n["Cargo.lock"]})[0])

    def test_code_and_added_files(self):
        self.assertEqual(self.check({"src/a.rs": "1\n"}, {"src/a.rs": "2\n"}), (False, ["src/a.rs"]))
        self.assertFalse(self.check({}, {"src/new.rs": "x\n"})[0])

    def test_version_line_regex(self):
        self.assertTrue(carry.VERSION_LINE.match('+const Version = "0.19.1+backotter.5"'))
        self.assertTrue(carry.VERSION_LINE.match('-var Version = "x"'))
        self.assertFalse(carry.VERSION_LINE.match('+const Version2 = "x"'))


class Digests(unittest.TestCase):
    T = ("rustic-bko freebsd, native in freebsd14\nsources aaaa bbbb\nprofile release\n"
         "image vms-bko-build/freebsd14-rust.qcow2 1644363776 1793956462\nfile build.sh cccc\ndigest dddd\n")

    def test_non_source_lines(self):
        new_src = self.T.replace("sources aaaa", "sources eeee").replace("digest dddd", "digest ffff")
        self.assertTrue(carry.non_source_same(self.T + "built on chopin 2026-11-06 09:20 UTC\n",
                                              new_src + "reused from /x\n"))
        self.assertFalse(carry.non_source_same(self.T, self.T.replace("profile release", "profile fast")))
        self.assertFalse(carry.non_source_same(self.T, self.T.replace("build.sh cccc", "build.sh 0000")))

    def test_dfly_image(self):
        d = "rustic-bko x86_64-unknown-dragonfly, cross\nsources aaaa\nprofile release\nsysroot dragonfly6.qcow2 1 2\ndigest x\n"
        self.assertTrue(carry.dfly_image_only(d, d.replace("sysroot dragonfly6.qcow2 1 2", "sysroot dragonfly6.qcow2 3 4")
                                              .replace("digest x", "digest y")))
        self.assertFalse(carry.dfly_image_only(d, d))
        self.assertFalse(carry.dfly_image_only(d, d.replace("sources aaaa", "sources bbbb")))
        self.assertFalse(carry.dfly_image_only(d, None))


class Forks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t2p2-carry-")
        cls.w = world.World(cls.tmp)
        cls.c = cls.w.forks

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_fork_version_only(self):
        ok, why = carry.fork_version_only(self.w.bko_git, self.c["c0"], self.c["c1"], carry.FORKS)
        self.assertTrue(ok, why)
        ok, why = carry.fork_version_only(self.w.bko_git, self.c["c1"], self.c["c2"], ("restic",))
        self.assertFalse(ok)
        self.assertIn("attrs_freebsd.go", why)
        ok, why = carry.fork_version_only(self.w.bko_git, self.c["c0"], self.c["c0"], ("restic",))
        self.assertFalse(ok)
        self.assertIn("no fork commit moved", why)
        ok, why = carry.fork_version_only(self.w.bko_git, {"restic": "0" * 40}, self.c["c1"], ("restic",))
        self.assertFalse(ok)

    def test_affected_v1(self):
        plats, why = carry.affected_v1(self.w.repo, self.w.bko_git, self.c["c1"], self.c["c2"])
        self.assertEqual(plats, {"freebsd"}, why)
        plats, why = carry.affected_v1(self.w.repo, self.w.bko_git, self.c["c2"], self.c["c3"])
        self.assertIn("linux", plats)
        self.assertEqual(carry.affected_oses({"openbsd_illumos"}), {"openbsd", "illumos", "omnios"})
        self.assertEqual(carry.affected_oses({"sparse", "zfsguests"}), {"linux"})

    def test_binary_rules(self):
        ac = carry.ArchiverCarry(self.w.repo, self.w.bko_git)
        c0, c1, c2 = self.c["c0"], self.c["c1"], self.c["c2"]
        d0 = "restic-bko linux\nsources aaaa\nprofile release\ndigest 1\n"
        d1 = "restic-bko linux\nsources bbbb\nprofile release\ndigest 2\n"
        own = lambda c, n: {r: c[r] for r in carry.binary_repos(n)}
        r, why = ac.binary("linux/restic-bko", own(c0, "linux/restic-bko"), own(c1, "linux/restic-bko"), d0, d1, c0, c1)
        self.assertEqual(r, "carry:version-only", why)
        r, why = ac.binary("linux/rustic-bko", own(c1, "linux/rustic-bko"), own(c2, "linux/rustic-bko"), d0, d1, c1, c2)
        self.assertEqual(r, "carry:affected-v1", why)
        r, why = ac.binary("freebsd/rustic-bko", own(c1, "freebsd/rustic-bko"), own(c2, "freebsd/rustic-bko"), d0, d1,
                           c1, c2)
        self.assertIsNone(r)
        self.assertIn("freebsd", why)
        r, why = ac.binary("haiku/rustic-bko", own(c1, "haiku/rustic-bko"), own(c2, "haiku/rustic-bko"), d0, d1, c1, c2)
        self.assertIsNone(r)
        self.assertIn("haiku", why)
        # a binary that changed while its commits didn't: a toolchain or script change, never carried
        r, why = ac.binary("linux/restic-bko", own(c1, "linux/restic-bko"), own(c1, "linux/restic-bko"), d1, d1, c1, c1)
        self.assertIsNone(r)
        self.assertIn("source commits didn't", why)
        # its non-source digest lines changed too
        r, why = ac.binary("linux/restic-bko", own(c0, "linux/restic-bko"), own(c1, "linux/restic-bko"), d0,
                           d1.replace("profile release", "profile fast"), c0, c1)
        self.assertIsNone(r)
        # dfly-image: only the sysroot line moved, the commits stayed
        dd = "rustic-bko dragonfly\nsources aaaa\nsysroot dragonfly6.qcow2 1 2\ndigest x\n"
        r, why = ac.binary("dragonfly/rustic-bko", own(c1, "dragonfly/rustic-bko"), own(c1, "dragonfly/rustic-bko"), dd,
                           dd.replace("1 2", "3 4").replace("digest x", "digest y"), c1, c1)
        self.assertEqual(r, "carry:dfly-image", why)
        # affected-v1 without the .digest texts can't check their other lines
        r, why = ac.binary("linux/rustic-bko", own(c1, "linux/rustic-bko"), own(c2, "linux/rustic-bko"), None, None, c1, c2)
        self.assertIsNone(r)
        self.assertIn(".digest", why)


class ChangedSince(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t2p2-changed-")
        cls.w = world.World(cls.tmp)
        cls.base = cls.w.head()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def tearDown(self):
        self.w.reset(self.base)

    def test_worktree_commit_and_dirty(self):
        g = carry.Git(self.w.repo)
        self.assertEqual(carry.changed_since(g, self.base, self.base, {}, ("testvm",)), {})
        self.w.edit("testvm/vm-run", "# changed\n")
        self.w.edit("testvm/compare/new.sh", "# new\n")
        self.w.edit("testvm/compare/__pycache__/x.pyc", "x")
        ch = carry.changed_since(g, self.base, self.base, {}, ("testvm",))
        self.assertEqual(sorted(ch), ["testvm/compare/new.sh", "testvm/vm-run"])
        self.w.edit("testvm/vm-run", "# changed\n", commit=True)
        head = self.w.head()
        # a row from a dirty copy whose diff is now committed: nothing changed since it
        sha = carry.sha256_bytes(b"# changed\n")
        self.assertNotIn("testvm/vm-run", carry.changed_since(g, head, self.base, {"testvm/vm-run": sha}, ("testvm",)))
        self.assertIn("testvm/vm-run", carry.changed_since(g, head, self.base, {}, ("testvm",)))

    def test_holt_code_paths_and_digest(self):
        paths = ["holt/src/a.rs", "holt/tests/x.rs", "holt/README.md", "holt/target/x", "holt/LICENSE", "testvm/x"]
        self.assertEqual(carry.holt_code_paths(paths), ["holt/src/a.rs", "holt/LICENSE"])
        d1 = carry.holt_code_digest({"holt/src/a.rs": "1", "holt/LICENSE": "2", "holt/README.md": "3"})
        d2 = carry.holt_code_digest({"holt/src/a.rs": "1", "holt/LICENSE": "2", "holt/README.md": "4"})
        self.assertEqual(d1, d2)

    def test_excluded(self):
        for p in ("a/__pycache__/b.pyc", "x.pyc", "holt/target/debug/x", "d/.DS_Store"):
            self.assertTrue(carry.excluded(p), p)
        self.assertFalse(carry.excluded("holt/src/target.rs"))


class HoltHistory(unittest.TestCase):
    """617c4de and 620ec60 bumped holt's version and nothing else (contract 1.6.4)."""

    def test_real_version_bumps(self):
        g = carry.Git(REPO)
        for c in ("617c4de", "620ec60"):
            full = g.rev(c)
            if not full:
                self.skipTest(f"{c} isn't in this repository's history")
            paths = carry.holt_code_paths(g.names(full + "^", full, ["holt"]))
            ok, bad = carry.version_only_files(paths, lambda p: g.show(full + "^", p), lambda p: g.show(full, p))
            self.assertTrue(ok, f"{c}: {bad}")
        full = g.rev("3671e96")
        if full:
            paths = carry.holt_code_paths(g.names(full + "^", full, ["holt"]))
            ok, bad = carry.version_only_files(paths, lambda p: g.show(full + "^", p), lambda p: g.show(full, p))
            self.assertFalse(ok)
            self.assertEqual(bad, ["holt/src/load/commands.rs"])


if __name__ == "__main__":
    unittest.main()
