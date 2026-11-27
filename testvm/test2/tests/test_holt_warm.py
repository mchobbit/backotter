"""F14's tests (docs/design/img-speed.md §12 F14): testvm/holt-warm, the warm bko-holt build dirs
on any build host (chosen by pick-host --reserve 2 4, recorded per tag), and
testvm/holt-warm-host.sh, its host side: the warm cargo test, and the prune of stale tags (only
cargo's target dirs, or --whole tags, and never a tag in use, open or locked).

No host, no ssh: ssh, rsync, pick-host, cmp-run.sh, rustup and cargo are stand-ins that work in a
temporary directory.  Run from the worktree: python3 -m unittest discover testvm/test2/tests.
"""

import os
import shutil
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
TESTVM = os.path.dirname(T2)
WT = os.path.dirname(TESTVM)
HOST_SH = os.path.join(TESTVM, "holt-warm-host.sh")
MAC = os.path.join(TESTVM, "holt-warm")
SIG = "Signature: 8a477f597d28d172789f06886806bc55\n# This file is a cache directory tag created by cargo.\n"
DAY = 86400


def wr(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    if mode is not None:
        os.chmod(path, mode)


def rd(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def age(top, days):
    """Every file and directory under TOP (TOP too) last changed DAYS days ago."""
    t = time.time() - days * DAY
    for d, dirs, files in os.walk(top, topdown=False):
        for n in files + dirs:
            os.utime(os.path.join(d, n), (t, t), follow_symlinks=False)
        os.utime(d, (t, t))


def tag(root, name, target="target", src=True, days=10):
    """A tag ROOT/NAME: src/holt, a log, and a cargo target dir named TARGET (None: none), all
    DAYS days old."""
    t = os.path.join(root, name)
    if src:
        wr(os.path.join(t, "src", "holt", "Cargo.toml"), "[package]\n")
    wr(os.path.join(t, "test.log"), "ok\n")
    if target:
        wr(os.path.join(t, target, "CACHEDIR.TAG"), SIG)
        wr(os.path.join(t, target, "debug", "bko-holt"), "x" * 4096)
    age(t, days)
    return t


class Prune(unittest.TestCase):
    """holt-warm-host.sh prune ROOT DAYS: a stale tag loses its cargo target dirs, and only those."""

    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="holt-warm-")
        self.addCleanup(shutil.rmtree, self.t, True)
        self.root = os.path.join(self.t, "holt-warm")
        os.makedirs(self.root)

    def prune(self, *args, days="7"):
        return subprocess.run(["bash", HOST_SH, "prune", self.root, days, *args],
                              capture_output=True, text=True, timeout=60)

    def test_stale_tag_loses_its_target_only(self):
        t = tag(self.root, "old", target="target")
        wr(os.path.join(t, "target-185", "CACHEDIR.TAG"), SIG)
        age(t, 10)
        r = self.prune()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(t, "target")))
        self.assertFalse(os.path.exists(os.path.join(t, "target-185")))
        self.assertTrue(os.path.isfile(os.path.join(t, "src", "holt", "Cargo.toml")))
        self.assertEqual(rd(os.path.join(t, "test.log")), "ok\n")
        self.assertRegex(r.stdout, r"prune old: (old/target old/target-185|old/target-185 old/target) ")
        self.assertRegex(r.stdout, r"freed [1-9][0-9]* KiB")

    def test_recent_tag_kept(self):
        t = tag(self.root, "busy")
        wr(os.path.join(t, "src", "holt", "src", "main.rs"), "fn main() {}\n")  # changed now
        r = self.prune()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isdir(os.path.join(t, "target")))
        self.assertIn("keep busy: changed in the last 7 days", r.stdout)
        self.assertIn("freed 0 KiB", r.stdout)

    def test_last_used_stamp_keeps_a_tag_of_old_files(self):
        t = tag(self.root, "reused")
        wr(os.path.join(t, ".last-used"), "")
        r = self.prune()
        self.assertTrue(os.path.isdir(os.path.join(t, "target")))
        self.assertIn("keep reused: used in the last 7 days", r.stdout)

    def test_days_threshold(self):
        t = tag(self.root, "five", days=5)
        r = self.prune(days="7")
        self.assertTrue(os.path.isdir(os.path.join(t, "target")), r.stdout)
        r = self.prune(days="3")
        self.assertFalse(os.path.exists(os.path.join(t, "target")), r.stdout)

    def test_tag_that_is_a_target_dir_goes(self):
        t = os.path.join(self.root, "a6gp")
        wr(os.path.join(t, "CACHEDIR.TAG"), SIG)
        wr(os.path.join(t, "debug", "x"), "x")
        age(t, 10)
        r = self.prune()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(t))

    def test_cachedir_tag_not_cargos_kept(self):
        t = tag(self.root, "other", target=None)
        wr(os.path.join(t, "cache", "CACHEDIR.TAG"), "Signature: something else\n")
        age(t, 10)
        r = self.prune()
        self.assertTrue(os.path.isfile(os.path.join(t, "cache", "CACHEDIR.TAG")))
        self.assertIn("keep other: stale, no cargo target dir", r.stdout)

    def test_dry_run_removes_nothing(self):
        t = tag(self.root, "old")
        r = self.prune("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isdir(os.path.join(t, "target")))
        self.assertIn("would prune old: old/target", r.stdout)
        self.assertRegex(r.stdout, r"would free [1-9][0-9]* KiB")

    def test_whole_removes_stale_tags_only(self):
        old = tag(self.root, "old", target=None)
        new = tag(self.root, "new", days=0)
        r = self.prune("--whole")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.isdir(os.path.join(new, "target")))

    def test_symlinks_not_followed(self):
        elsewhere = tag(self.t, "elsewhere")
        os.symlink(elsewhere, os.path.join(self.root, "link"))
        t = tag(self.root, "old", target=None)
        os.symlink(os.path.join(elsewhere, "target"), os.path.join(t, "target"))
        age(t, 10)
        r = self.prune("--whole")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isdir(os.path.join(elsewhere, "target")))
        self.assertTrue(os.path.islink(os.path.join(self.root, "link")))

    def test_tag_with_a_process_in_it_kept(self):
        t = tag(self.root, "running")
        p = subprocess.Popen(["sleep", "30"], cwd=os.path.join(t, "src", "holt"))
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        r = self.prune()
        self.assertTrue(os.path.isdir(os.path.join(t, "target")), r.stdout)
        self.assertIn("keep running: a process has it open", r.stdout)

    @unittest.skipUnless(shutil.which("flock"), "no flock(1) here")
    def test_held_cargo_lock_kept(self):
        t = tag(self.root, "locked")
        lock = os.path.join(t, "target", "debug", ".cargo-lock")
        wr(lock, "")
        age(t, 10)
        p = subprocess.Popen(["flock", lock, "sleep", "30"], cwd=self.t)
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        time.sleep(0.5)
        r = self.prune()
        self.assertTrue(os.path.isdir(os.path.join(t, "target")), r.stdout)
        self.assertIn("keep locked:", r.stdout)

    def test_bad_usage(self):
        self.assertEqual(self.prune(days="0").returncode, 2)
        self.assertEqual(self.prune("--frobnicate").returncode, 2)
        r = subprocess.run(["bash", HOST_SH, "prune", "relative", "7"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


class HostTest(unittest.TestCase):
    """holt-warm-host.sh test DIR TOOLCHAIN ARGS: cargo test --offline in DIR/src/holt, the
    target dir DIR/target, the toolchain named, TMPDIR in the RAM disk and removed after."""

    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="holt-warm-")
        self.addCleanup(shutil.rmtree, self.t, True)
        self.home = os.path.join(self.t, "home")
        self.ram = os.path.join(self.t, "ram")
        os.makedirs(self.ram)
        self.log = os.path.join(self.t, "cargo.log")
        bin_ = os.path.join(self.home, ".cargo", "bin")
        wr(os.path.join(bin_, "rustup"), "#!/bin/sh\n[ \"$1 $2\" = \"run 1.94.1\" ] || exit 1\necho rustc 1.94.1\n", 0o755)
        wr(os.path.join(bin_, "cargo"),
           f"#!/bin/sh\n{{ echo \"pwd=$(pwd -P)\"; echo \"tc=$RUSTUP_TOOLCHAIN\"; echo \"target=$CARGO_TARGET_DIR\";"
           f" echo \"tmp=$TMPDIR\"; [ -d \"$TMPDIR\" ] && echo tmpdir-exists; echo \"args=$*\"; }} >>{self.log}\n"
           "exit ${CARGO_RC:-0}\n", 0o755)
        self.dir = os.path.join(os.path.realpath(self.t), "holt-warm", "f14")
        wr(os.path.join(self.dir, "src", "holt", "Cargo.toml"), "[package]\n")

    def run_test(self, *args, tc="1.94.1", rc="0"):
        env = dict(os.environ, HOME=self.home, BKO_HOLT_WARM_TMP=self.ram, CARGO_RC=rc,
                   BKO_TESTVM=os.path.join(self.t, "no-testvm"))
        return subprocess.run(["bash", HOST_SH, "test", self.dir, tc, *args], env=env,
                              capture_output=True, text=True, timeout=60)

    def test_cargo_test_warm(self):
        r = self.run_test("--lib", "stick::")
        self.assertEqual(r.returncode, 0, r.stderr)
        log = rd(self.log)
        self.assertIn(f"pwd={os.path.join(self.dir, 'src', 'holt')}\n", log)
        self.assertIn("tc=1.94.1\n", log)
        self.assertIn(f"target={self.dir}/target\n", log)
        self.assertIn(f"tmp={self.ram}/holt-warm-f14.", log)
        self.assertIn("tmpdir-exists\n", log)
        self.assertIn("args=test --offline --lib stick::\n", log)
        self.assertTrue(os.path.isfile(os.path.join(self.dir, ".last-used")))
        self.assertEqual(os.listdir(self.ram), [], "TMPDIR left behind")

    def test_cargo_failure_is_the_status(self):
        r = self.run_test(rc="101")
        self.assertEqual(r.returncode, 101)
        self.assertEqual(os.listdir(self.ram), [])

    def test_missing_toolchain_refused(self):
        r = self.run_test(tc="1.99.0")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no Rust 1.99.0 here", r.stderr)
        self.assertFalse(os.path.exists(self.log))

    def test_no_tree_refused(self):
        shutil.rmtree(os.path.join(self.dir, "src"))
        self.assertEqual(self.run_test().returncode, 2)


class Mac(unittest.TestCase):
    """testvm/holt-warm on the Mac: the host is pick-host --reserve 2 4's, recorded per tag; test
    syncs the tree and runs the host side under cmp-run.sh --reserve 2 4; prune runs on each host."""

    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="holt-warm-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.repo = os.path.join(self.t, "repo")
        tv = os.path.join(self.repo, "testvm")
        os.makedirs(tv)
        shutil.copy(MAC, os.path.join(tv, "holt-warm"))
        shutil.copy(HOST_SH, os.path.join(tv, "holt-warm-host.sh"))
        os.symlink(T2, os.path.join(tv, "test2"))  # hosts.py and classes.json
        wr(os.path.join(self.repo, "holt", "Cargo.toml"), "[package]\n")
        wr(os.path.join(self.repo, "holt", "src", "main.rs"), "fn main() {}\n")
        wr(os.path.join(self.repo, "holt", "target", "debug", "big"), "not synced\n")
        wr(os.path.join(self.repo, "docs", "design", "holt-json.md"), "# codes\n")
        self.bin = os.path.join(self.t, "bin")
        self.calls = os.path.join(self.t, "calls")
        # ssh [-o X]... HOST CMD: CMD here, with HOST logged; rsync with HOST:PATH as PATH.
        wr(os.path.join(self.bin, "ssh"),
           "#!/bin/bash\nwhile [ \"$1\" = -o ]; do shift 2; done\n"
           f"h=$1; shift; echo \"ssh $h\" >>{self.calls}\nexec bash -c \"$*\"\n", 0o755)
        wr(os.path.join(self.bin, "rsync"),
           "#!/bin/bash\na=()\nfor x; do case $x in *:/*) x=/${x#*:/} ;; esac; a+=(\"$x\"); done\n"
           f"echo \"rsync ${{a[*]}}\" >>{self.calls}\nexec /usr/bin/rsync \"${{a[@]}}\"\n", 0o755)
        self.pick = os.path.join(self.t, "pick-host")
        wr(self.pick, f"#!/bin/sh\necho \"pick $*\" >>{self.calls}\necho ${{PICKED:-m@copland.local}}\n", 0o755)
        self.cmp = os.path.join(self.t, "cmp-run.sh")
        wr(self.cmp, f"#!/bin/bash\necho \"cmp $1 $2 $3 tag=$WEAR_TAG\" >>{self.calls}\n"
                     "[ \"$1 $2 $3\" = \"--reserve 2 4\" ] || exit 9\nshift 3\nexec \"$@\"\n", 0o755)
        self.home = os.path.join(self.t, "home")
        cb = os.path.join(self.home, ".cargo", "bin")
        wr(os.path.join(cb, "rustup"), "#!/bin/sh\necho rustc\n", 0o755)
        wr(os.path.join(cb, "cargo"), f"#!/bin/sh\necho \"cargo $* in $(pwd -P)\" >>{self.calls}\n", 0o755)
        self.root = os.path.join(self.t, "hostroot")
        self.state = os.path.join(self.t, "state")
        self.ram = os.path.join(self.t, "ram")
        os.makedirs(self.ram)

    def hw(self, *args, **extra):
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", HOME=self.home,
                   BKO_PICK_HOST=self.pick, BKO_CMP_RUN=self.cmp, BKO_HOLT_WARM_ROOT=self.root,
                   BKO_HOLT_WARM_STATE=self.state, BKO_HOLT_WARM_TMP=self.ram,
                   BKO_TESTVM=os.path.join(self.t, "no-testvm"),
                   BKO_HOSTS_JSON=os.path.join(TESTVM, "hosts.json"), **extra)
        return subprocess.run(["bash", os.path.join(self.repo, "testvm", "holt-warm"), *args], env=env,
                              capture_output=True, text=True, timeout=60)

    def calls_(self):
        return rd(self.calls).splitlines() if os.path.exists(self.calls) else []

    def test_host_picked_once_then_recorded(self):
        r = self.hw("host", "f14")
        self.assertEqual((r.returncode, r.stdout), (0, "m@copland.local\n"), r.stderr)
        self.assertEqual(self.calls_(), ["pick --reserve 2 4"])
        self.assertEqual(rd(os.path.join(self.state, "f14.host")), "m@copland.local\n")
        r = self.hw("host", "f14", PICKED="m@cage.local")
        self.assertEqual(r.stdout, "m@copland.local\n")
        self.assertEqual(len(self.calls_()), 1, "pick-host asked again for a recorded tag")
        r = self.hw("host", "f14", "--repick", PICKED="m@cage.local")
        self.assertEqual(r.stdout, "m@cage.local\n")
        self.assertEqual(rd(os.path.join(self.state, "f14.host")), "m@cage.local\n")

    def test_bad_tags_refused(self):
        for bad in ("", "-x", ".hidden", "a/b", "a b", ".."):
            r = self.hw("host", bad)
            self.assertEqual(r.returncode, 2, bad)
        self.assertEqual(self.calls_(), [])

    def test_test_syncs_and_runs_under_a_reservation(self):
        r = self.hw("test", "f14", "--", "--lib", "stick::")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        d = os.path.join(self.root, "f14")
        self.assertEqual(rd(os.path.join(d, "src", "holt", "src", "main.rs")), "fn main() {}\n")
        self.assertFalse(os.path.exists(os.path.join(d, "src", "holt", "target")), "target/ synced")
        self.assertTrue(os.path.isfile(os.path.join(d, "src", "testvm", "test2", "classes.json")))
        self.assertTrue(os.path.isfile(os.path.join(d, "src", "docs", "design", "holt-json.md")))
        self.assertTrue(os.path.isfile(os.path.join(d, ".holt-warm-host.sh")))
        self.assertTrue(os.path.isfile(os.path.join(d, ".last-used")))
        c = self.calls_()
        self.assertEqual(c[0], "pick --reserve 2 4")
        self.assertIn("cmp --reserve 2 4 tag=holt-warm:f14", c)
        self.assertIn(f"cargo test --offline --lib stick:: in {d}/src/holt", c)
        self.assertEqual({x for x in c if x.startswith("ssh ")}, {"ssh m@copland.local"})

    def test_test_host_given_by_name_is_recorded(self):
        r = self.hw("test", "f14", "--host", "czerny")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("pick --reserve 2 4", self.calls_())
        self.assertIn("ssh m@czerny.local", self.calls_())
        self.assertEqual(rd(os.path.join(self.state, "f14.host")), "m@czerny.local\n")

    def test_test_failure_is_the_status(self):
        wr(os.path.join(self.home, ".cargo", "bin", "cargo"), "#!/bin/sh\nexit 101\n", 0o755)
        r = self.hw("test", "f14")
        self.assertEqual(r.returncode, 101)

    def test_prune_on_named_hosts(self):
        t = tag(self.root, "old")
        r = self.hw("prune", "--host", "m@chopin.local", "--days", "7")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("== m@chopin.local", r.stdout)
        self.assertIn("prune old: old/target", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(t, "target")))
        self.assertTrue(os.path.isdir(os.path.join(t, "src")))

    def test_prune_defaults_to_the_default_hosts(self):
        r = self.hw("prune", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        hosts = [x[3:] for x in r.stdout.splitlines() if x.startswith("== ")]
        want = subprocess.run(["python3", "-I", "-B", os.path.join(T2, "t2lib", "hosts.py"), "names",
                               "--default", "--arch", "amd64", "--field", "ssh"],
                              capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(hosts, want)
        self.assertEqual(r.stdout.count("no " + self.root), len(want))

    def test_prune_bad_usage(self):
        self.assertEqual(self.hw("prune", "--days", "0").returncode, 2)
        self.assertEqual(self.hw("prune", "--bogus").returncode, 2)


if __name__ == "__main__":
    unittest.main()
