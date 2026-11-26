"""I5's tests (docs/design/img-speed.md §7, C8, D6, D7, §14 row I5): img-one.sh's SHA256SUMS
and no compression by default, the D6 retention rule (stick/img-retain.sh, and after img-one.sh's
pass), img-publish.sh's rules (stick/img_publish.py: C4's rows, the cadence, the host) over
fixture facts, and img-publish-host.sh on a fixture images directory.

No host, no ssh.  Run from the worktree: python3 -m unittest discover testvm/test2/tests.
"""

import hashlib
import json
import lzma
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
WT = os.path.dirname(os.path.dirname(T2))
STICK = os.path.join(WT, "stick")
sys.path.insert(0, T2)
sys.path.insert(0, HERE)

import test_img_inputs as TI  # noqa: E402
import test_img_round as TR  # noqa: E402
from t2lib import facts_host  # noqa: E402

P = TR._load("img_publish_t", os.path.join(STICK, "img_publish.py"))
BASH = shutil.which("bash")
PLATS = TR.PLATS


def wr(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    if mode is not None:
        os.chmod(path, mode)


def rd(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def sha_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class ImgOneSums(TI.ImgOne):
    """img-one.sh writes SHA256SUMS itself, compresses only with BKO_IMG_COMPRESS=1, and keeps the
    last two prev-* sets after a pass (D6)."""

    def setUp(self):
        super().setUp()
        self.marker = os.path.join(self.t, "compressed")
        wr(os.path.join(self.w, "stick", "compress-images.sh"), f"#!/bin/sh\necho \"$@\" >>{self.marker}\n", 0o755)
        shutil.copy(os.path.join(STICK, "img-retain.sh"), os.path.join(self.w, "stick", "img-retain.sh"))
        self.d = os.path.join(self.i, "netbsd")

    def test_sha256sums_as_compress_images_wrote_them(self):
        r = self.run_one("netbsd")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(self.marker), "compressed with BKO_IMG_COMPRESS unset")
        want = sha_file(os.path.join(self.d, "x.img"))
        self.assertEqual(rd(os.path.join(self.d, "SHA256SUMS")), f"{want}  x.img\n")
        self.assertEqual(rd(os.path.join(self.d, "test", "SHA256SUMS")),
                         f"{sha_file(os.path.join(self.d, 'test', 'x.img'))}  x.img\n")
        # Facts and ImgTest2's release-image key read the same line, and it is the provenance's.
        f = facts_host.one_image(self.d, "netbsd", os.path.join(self.t, "host-root"))
        self.assertEqual(f["release"], {"name": "x.img", "sha256": want})
        self.assertEqual(json.loads(rd(os.path.join(self.d, "provenance.json")))["release"]["sha256"], want)
        self.assertFalse([n for n in os.listdir(self.d) if n.endswith(".xz")])

    def test_the_release_key_is_the_one_compress_images_gave(self):
        """An image's facts (so its keys) are the same from img-one.sh's SHA256SUMS as from the
        one compress-images.sh wrote (its .img line, then its .img.xz line)."""
        self.run_one("netbsd")
        mine = facts_host.one_image(self.d, "netbsd", os.path.join(self.t, "host-root"))
        h = sha_file(os.path.join(self.d, "x.img"))
        wr(os.path.join(self.d, "SHA256SUMS"), f"{h}  x.img\n{'0' * 64}  x.img.xz\n")
        old = facts_host.one_image(self.d, "netbsd", os.path.join(self.t, "host-root"))
        self.assertEqual(mine, old)

    def test_compress_one_restores_todays_behaviour(self):
        r = self.run_one("netbsd", BKO_IMG_COMPRESS="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(rd(self.marker).split()[:2], ["-p", "netbsd"])
        self.assertIn("compress: ok", rd(os.path.join(self.d, "result.txt")))

    def test_a_pass_keeps_the_last_two_prev_sets(self):
        for n in ("prev-20250101-000000", "prev-x20250102-000000", "prev-20250103-000000", "prev-x20250104-000000"):
            wr(os.path.join(self.d, n, "a.img"), "old\n")
        r = self.run_one("netbsd")
        self.assertEqual(r.returncode, 0, r.stderr)
        prev = sorted(n for n in os.listdir(self.d) if n.startswith("prev-"))
        self.assertEqual(prev, ["prev-20250103-000000", "prev-x20250104-000000"],
                         rd(os.path.join(self.d, "result.txt")))
        self.assertIn("retain: deleted 2 set(s)", rd(os.path.join(self.d, "result.txt")))
        self.assertTrue(os.path.isfile(os.path.join(self.d, "x.img")))

    def test_no_retention_after_a_failed_boot_or_with_retain_0(self):
        for n in ("prev-20250101-000000", "prev-20250102-000000", "prev-20250103-000000"):
            wr(os.path.join(self.d, n, "a.img"), "old\n")
        r = self.run_one("netbsd", BKO_IMG_RETAIN="0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len([n for n in os.listdir(self.d) if n.startswith("prev-")]), 3)
        wr(os.path.join(self.w, "stick", "boot-test.py"), "import sys\nprint('  FAIL tools')\nsys.exit(1)\n")
        r = self.run_one("netbsd")
        self.assertEqual(rd(os.path.join(self.d, "done.exit")), "2\n")
        self.assertEqual(len([n for n in os.listdir(self.d) if n.startswith("prev-")]), 4)


# (ImgOne's own tests run in test_img_inputs.py: only its world is borrowed here.)
for _n in [n for n in vars(TI.ImgOne) if n.startswith("test_")]:
    setattr(ImgOneSums, _n, None)


class Retain(unittest.TestCase):
    """img-retain.sh: the last two prev-* sets by their stamp, nothing else."""

    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="t2-i5-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.path = os.path.join(self.t, "path")
        wr(os.path.join(self.path, "flock"), "#!/bin/sh\nexit ${FLOCK_RC:-0}\n", 0o755)
        self.i = os.path.join(self.t, "images")
        self.d = os.path.join(self.i, "openbsd")
        for n in ("prev-20261031-pass1", "prev-20261101-0910", "prev-20261101-000000", "prev-x20261102-000000",
                  "prev-20261103-000000", "prev-x20261104-000000"):
            wr(os.path.join(self.d, n, "a.img"), n + "\n")
            wr(os.path.join(self.d, n, "test", "a.img"), n + "\n")
        os.makedirs(os.path.join(self.d, "prev-x20261105-000000"))          # an empty one, img-xcopy's
        wr(os.path.join(self.d, "prev-mine", "keep"), "a user's\n")
        wr(os.path.join(self.d, "x.img"), "current\n")
        wr(os.path.join(self.d, "test", "x.img"), "current\n")
        wr(os.path.join(self.i, "publish", "s1", "openbsd", "prev-20200101-000000", "x"), "published\n")

    def run_retain(self, *args, **env):
        e = dict(os.environ, PATH=self.path + ":" + os.environ["PATH"])
        e.pop("BKO_RETAIN_HELD", None)
        e.update(env)
        return subprocess.run([BASH, os.path.join(STICK, "img-retain.sh"), *args], env=e,
                              capture_output=True, text=True)

    def names(self):
        return sorted(os.listdir(self.d))

    def test_keeps_the_two_newest_and_the_current(self):
        r = self.run_retain(self.i)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.names(), ["prev-20261103-000000", "prev-mine", "prev-x20261104-000000", "test", "x.img"])
        self.assertIn("left alone: prev-mine", r.stderr)
        self.assertIn("deleted 4 set(s)", r.stdout.splitlines()[-1])
        self.assertTrue(os.path.isfile(os.path.join(self.i, "publish", "s1", "openbsd", "prev-20200101-000000", "x")))
        again = self.run_retain("-p", "openbsd", self.i)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("deleted 0 set(s)", again.stdout)

    def test_dry_run_deletes_nothing(self):
        before = self.names()
        r = self.run_retain("-n", self.i)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.names(), before)
        self.assertIn("would delete prev-20261101-000000", r.stdout)
        self.assertIn("would delete prev-x20261102-000000", r.stdout)
        self.assertIn("would delete prev-20261031-pass1", r.stdout)
        self.assertIn("(dry run) deleted 4 set(s)", r.stdout)

    def test_a_held_lock_skips_the_platform(self):
        before = self.names()
        r = self.run_retain("-p", "openbsd", self.i, FLOCK_RC="1")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.names(), before)
        self.assertIn("openbsd: skipped", r.stderr)
        # (img-one.sh, holding it, says so.)
        r = self.run_retain("-p", "openbsd", self.i, FLOCK_RC="1", BKO_RETAIN_HELD="openbsd")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("prev-20261101-000000", self.names())

    def test_usage(self):
        self.assertEqual(self.run_retain("-p", "beos", self.i).returncode, 2)
        self.assertEqual(self.run_retain(os.path.join(self.t, "nowhere")).returncode, 2)


def publish_world(**kw):
    """Four non-fleet builders; cinqcent and copland hold today's proven set."""
    g, rows, pvs = TR.fresh_world()
    g = {h: g[h] for h in ("cinqcent", "copland", "cage", "czerny")}
    for h in ("cage", "czerny"):
        g[h]["facts"]["images"] = {}
    for p in PLATS:
        g["cinqcent"]["facts"]["images"][p]["release_name"] = pvs[p]["release"]["file"]
    return g, rows, pvs


class Rules(unittest.TestCase):
    """img_publish.py: what may be published, and where (C4, D3, D7)."""

    def choose(self, g, rows, plats=PLATS, host=None, today=TR.TODAY):
        return P.choose(g, list(plats), rows, today, host, TR.TABLE)

    def test_a_proven_current_set_is_published_from_the_least_loaded_holder(self):
        g, rows, pvs = publish_world()
        g["cinqcent"]["probe"] = TR.probe(load=12.0)
        h, e = self.choose(g, rows)
        self.assertEqual(h, "copland")
        self.assertEqual({p: e[p]["release_sha256"] for p in PLATS}, {p: pvs[p]["release"]["sha256"] for p in PLATS})
        self.assertEqual(e["linux"]["release"], "rel-x.img")
        g["copland"]["probe"] = TR.probe(load=15.9)
        self.assertEqual(self.choose(g, rows)[0], "cinqcent")
        self.assertEqual(self.choose(g, rows, host="copland")[0], "copland")

    def test_an_unproven_image_is_refused_by_platform(self):
        g, rows, pvs = publish_world()
        rows = [r for r in rows if r["cell"] != "img:openbsd"]
        with self.assertRaises(P.Refused) as c:
            self.choose(g, rows)
        self.assertIn("openbsd (", str(c.exception))
        self.assertIn("not proven (C4)", str(c.exception))
        self.assertNotIn("linux (", str(c.exception))
        h, e = self.choose(g, rows, plats=[p for p in PLATS if p != "openbsd"])
        self.assertNotIn("openbsd", e)

    def test_a_failed_panel_is_refused(self):
        g, rows, pvs = publish_world()
        rows = [r for r in rows if r["cell"] != "img:haiku"] + \
            [TR.pass_row("haiku", pvs["haiku"]["test"]["sha256"], panel={"result": "fail", "needed": True})]
        with self.assertRaises(P.Refused) as c:
            self.choose(g, rows)
        self.assertIn("haiku", str(c.exception))
        self.assertIn("+img panel", str(c.exception))

    def test_a_set_past_the_cadence_is_refused_naming_the_platform(self):
        g, rows, pvs = publish_world()
        old = TR.provenance("dragonfly", TR.dig("dragonfly"), epoch="2026-11-01")
        for h in ("cinqcent", "copland"):
            g[h]["facts"]["images"]["dragonfly"] = TR.image(old)
        rows = rows + [TR.pass_row("dragonfly", old["test"]["sha256"])]
        with self.assertRaises(P.Refused) as c:
            self.choose(g, rows)
        self.assertIn("dragonfly (", str(c.exception))
        self.assertIn("past the cadence", str(c.exception))
        self.assertIn("26 days old", str(c.exception))
        # Within the cadence it publishes: 14 days is still in.
        self.assertEqual(len(self.choose(g, rows, today=TR.datetime.date(2026, 11, 15))[1]), 7)

    def test_no_host_with_every_platform_is_refused(self):
        g, rows, pvs = publish_world()
        del g["cinqcent"]["facts"]["images"]["linux"]
        del g["copland"]["facts"]["images"]["netbsd"]
        with self.assertRaises(P.Refused) as c:
            self.choose(g, rows)
        self.assertIn("no host holds a publishable image of every platform", str(c.exception))
        self.assertIn("cinqcent lacks linux", str(c.exception))

    def test_a_provenance_that_isnt_the_image_is_refused(self):
        g, rows, pvs = publish_world()
        for h in ("cinqcent", "copland"):
            g[h]["facts"]["images"]["illumos"]["release_sha256"] = "0" * 64
        with self.assertRaises(P.Refused) as c:
            self.choose(g, rows)
        self.assertIn("its release image is not the one its provenance names", str(c.exception))

    def test_cli_dry_run_and_usage(self):
        t = tempfile.mkdtemp(prefix="t2-i5-cli-")
        self.addCleanup(shutil.rmtree, t, True)
        cli = TR.Cli()
        cli.t, cli.facts = t, os.path.join(t, "facts")
        os.makedirs(os.path.join(t, "mac", "ledger"))
        os.makedirs(cli.facts)
        g, rows, pvs = publish_world()
        cli.write(g, rows)
        env = dict(os.environ, T2_MAC_ROOT=os.path.join(t, "mac"))
        sh = os.path.join(STICK, "img-publish.sh")

        def run(*a):
            return subprocess.run([sh, *a], env=env, capture_output=True, text=True, timeout=120)
        r = run("--facts-dir", cli.facts, "--today", "2026-11-27", "--dry-run", "s20261127")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("img-publish s20261127: on ", r.stdout)
        r = run("--facts-dir", cli.facts, "--today", "2026-12-31", "--dry-run", "s1")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("past the cadence", r.stderr)
        self.assertEqual(run("--host", "chopin", "s1").returncode, 2)
        self.assertEqual(run("--host", "choubox", "s1").returncode, 2)
        self.assertEqual(run("--only", "beos", "s1").returncode, 2)
        self.assertEqual(run("../x").returncode, 2)


@unittest.skipUnless(BASH and shutil.which("xz") and shutil.which("sha256sum"), "no xz or sha256sum here")
class HostSide(unittest.TestCase):
    """img-publish-host.sh on a fixture images directory: copy, xz, the C8 check, SHA256SUMS."""

    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="t2-i5-h-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.path = os.path.join(self.t, "path")
        # (FLOCK_RC is a shared lock's answer: images/.lock-P's.)
        wr(os.path.join(self.path, "flock"), '#!/bin/sh\ncase " $* " in *" -s "*) exit ${FLOCK_RC:-0} ;; esac\nexit 0\n', 0o755)
        # cmp-run.sh --reserve D M CMD...: CMD, or $CMP_HOOK instead.
        self.cmp = os.path.join(self.t, "cmp-run.sh")
        wr(self.cmp, '#!/bin/sh\necho "$WEAR_TAG $*" >>' + os.path.join(self.t, "cmp.log") +
           '\nshift 3\n[ -z "$CMP_HOOK" ] || exec sh -c "$CMP_HOOK" sh "$@"\nexec "$@"\n', 0o755)
        self.i = os.path.join(self.t, "images")
        self.sums = {}
        for p in ("netbsd", "haiku"):
            d = os.path.join(self.i, p)
            wr(os.path.join(d, f"bko-rescue-{p}.img"), (p + " image\n") * 5000)
            wr(os.path.join(d, "done.exit"), "0\n")
            wr(os.path.join(d, "provenance.json"), '{"platform": "%s"}\n' % p)
            wr(os.path.join(d, "bko-bin.sha256"), "x\n")
            self.sums[p] = sha_file(os.path.join(d, f"bko-rescue-{p}.img"))
        self.before = {p: os.stat(os.path.join(self.i, p, f"bko-rescue-{p}.img")) for p in self.sums}

    def run_host(self, *entries, **env):
        e = dict(os.environ, PATH=self.path + ":" + os.environ["PATH"], CMP_RUN=self.cmp, BKO_IMG_DIR=self.i,
                 CMP_HOOK="")
        e.update(env)
        ent = entries or tuple(f"{p}=bko-rescue-{p}.img={self.sums[p]}" for p in sorted(self.sums))
        return subprocess.run([BASH, os.path.join(STICK, "img-publish-host.sh"), "s1", '{"set": "s1"}', *ent],
                              env=e, capture_output=True, text=True, timeout=120)

    def test_publishes_and_checks(self):
        r = self.run_host()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = os.path.join(self.i, "publish", "s1")
        self.assertEqual(sorted(os.listdir(out)), ["PUBLISHED.json", "haiku", "netbsd"])
        for p, h in self.sums.items():
            n = f"bko-rescue-{p}.img"
            o = os.path.join(out, p)
            self.assertEqual(sorted(os.listdir(o)), sorted(["SHA256SUMS", n + ".xz", "provenance.json", "bko-bin.sha256"]))
            with lzma.open(os.path.join(o, n + ".xz")) as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(), h)
            self.assertEqual(rd(os.path.join(o, "SHA256SUMS")),
                             f"{h}  {n}\n{sha_file(os.path.join(o, n + '.xz'))}  {n}.xz\n")
            # images/P only read: the raw image is there, the same file, and nothing new beside it.
            st = os.stat(os.path.join(self.i, p, n))
            self.assertEqual((st.st_ino, st.st_size, st.st_mtime_ns, st.st_nlink),
                             (self.before[p].st_ino, self.before[p].st_size, self.before[p].st_mtime_ns, 1))
            self.assertEqual(sorted(os.listdir(os.path.join(self.i, p))),
                             sorted([n, "done.exit", "provenance.json", "bko-bin.sha256"]))
        self.assertEqual(json.loads(rd(os.path.join(out, "PUBLISHED.json"))), {"set": "s1"})
        self.assertIn("publish:s1:netbsd --reserve 0 8", rd(os.path.join(self.t, "cmp.log")))
        # A set is published once.
        r = self.run_host()
        self.assertEqual(r.returncode, 3)
        self.assertIn("is there already", r.stderr)

    def test_an_image_that_isnt_the_proven_one_is_refused(self):
        r = self.run_host(f"netbsd=bko-rescue-netbsd.img={'0' * 64}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not the proven", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.i, "publish", "s1")))
        self.assertFalse(os.path.exists(os.path.join(self.i, "publish", ".s1.part")))

    def test_a_bad_xz_fails_c8(self):
        # (The hook's $5 is the image: sh -c SCRIPT sh IMAGE LIMIT.)
        r = self.run_host(CMP_HOOK='printf "%s" "$5" | xz -c >"$5.xz.part"')
        self.assertEqual(r.returncode, 1)
        self.assertIn("(C8)", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.i, "publish", "s1")))

    def test_a_build_under_way_or_failed_refuses(self):
        r = self.run_host(FLOCK_RC="1")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("a build of it is under way", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.i, "publish", ".s1.part")))
        wr(os.path.join(self.i, "haiku", "done.exit"), "1\n")
        r = self.run_host()
        self.assertEqual(r.returncode, 1)
        self.assertIn("done.exit is not 0", r.stderr)

    def test_usage(self):
        self.assertEqual(self.run_host("netbsd=../x.img=" + "0" * 64).returncode, 2)
        self.assertEqual(self.run_host("beos=x.img=" + "0" * 64).returncode, 2)


if __name__ == "__main__":
    unittest.main()
