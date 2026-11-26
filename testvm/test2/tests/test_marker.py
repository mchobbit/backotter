"""stick/marker.py's dump_schemas (holt-json.md's open question 11), every
platform's dump-schemas boot check (stick/PLATFORM/checks.sh), and the config
partition's size: 256 MiB on every image, as each build script and bko-holt's
room check take it (open question 13).

marker.py runs with stand-ins for mkfs.vfat and mtools (it only runs them), so
this needs python3 3.11 or later and a POSIX sh, sed and tr.  No VM, no image."""

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
STICK = os.path.join(REPO, "stick")
MARKER = os.path.join(STICK, "marker.py")
PLATFORMS = ("linux", "freebsd", "netbsd", "openbsd", "dragonfly", "illumos", "haiku")


def wr(path, data, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(data)
    if mode is not None:
        os.chmod(path, mode)


def api(**over):
    """A bko-holt-api document, as `bko-holt --version --json` prints it."""
    doc = {"format": "bko-holt-api", "schema": 1, "version": "0.3.0", "generated": "2026-11-26T10:00:00Z",
           "dry_run": False, "documents": {"plan": [1]}, "dump_schemas": [1], "features": ["stick", "luks_headers"]}
    doc.update(over)
    return json.dumps(doc, separators=(",", ":"))


class Marker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bko-marker-")
        self.addCleanup(subprocess.run, ["rm", "-rf", self.tmp])
        self.stubs = os.path.join(self.tmp, "stubs")
        for c in ("mkfs.vfat", "mmd", "mcopy"):
            wr(os.path.join(self.stubs, c), "#!/bin/sh\nexit 0\n", 0o755)

    def run_marker(self, api_text, *extra):
        a = os.path.join(self.tmp, "api.json")
        if api_text is not None:
            wr(a, api_text)
        out = os.path.join(self.tmp, "out")
        env = dict(os.environ, PATH=self.stubs + ":" + os.environ["PATH"])
        r = subprocess.run([sys.executable, MARKER, "--platform", "freebsd", "--base-os", "freebsd",
                            "--base-release", "15.1-RELEASE", "--kernel", "15.1-RELEASE", "--config-part", "3",
                            "--holt-api", a, *extra, out], capture_output=True, text=True, env=env)
        m = None
        if r.returncode == 0:
            with open(os.path.join(out, "backotter.json")) as f:
                m = json.load(f)
        return r, m

    def test_taken_from_bko_holt(self):
        r, m = self.run_marker(api())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        self.assertEqual(m["dump_schemas"], [1])
        self.assertEqual(m["system_schemas"], [1])
        # Beside system_schemas, before the config partition.
        keys = list(m)
        self.assertEqual(keys.index("dump_schemas"), keys.index("system_schemas") + 1)
        r, m = self.run_marker(api(dump_schemas=[1, 2]))
        self.assertEqual(m["dump_schemas"], [1, 2])

    def test_a_bko_holt_that_names_none(self):
        doc = json.loads(api())
        del doc["dump_schemas"]
        # An image build refuses it at once: its image would fail the boot
        # check dump-schemas (review of oq11) ...
        r, m = self.run_marker(json.dumps(doc))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("names no dump_schemas (it predates them), and its image would fail the boot check dump-schemas", r.stderr)
        self.assertIn("img-refresh-holt.sh", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "out", "backotter.json")))
        # ... and a recording (record-stick.py) takes it as reading schema 1.
        r, m = self.run_marker(json.dumps(doc), "--holt-api-unnamed-ok")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(m["dump_schemas"], [1])
        self.assertIn("names no dump_schemas", r.stderr)

    def test_refused(self):
        for text, why in ((None, "not bko-holt --version --json's document"),
                          ("bko-holt 0.2.6\n", "not bko-holt --version --json's document"),
                          (json.dumps({"format": "bko-holt-error", "schema": 1}), "not a bko-holt-api document"),
                          (api(dump_schemas=[]), "isn't a list of schema numbers"),
                          (api(dump_schemas=["1"]), "isn't a list of schema numbers"),
                          (api(dump_schemas=[0]), "isn't a list of schema numbers"),
                          (api(dump_schemas=1), "isn't a list of schema numbers")):
            with self.subTest(text=text):
                r, _ = self.run_marker(text)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(why, r.stderr)
                self.assertFalse(os.path.exists(os.path.join(self.tmp, "out", "backotter.json")))

    def test_a_bko_holt_that_cant_fetch_kept_headers(self):
        """features without luks_headers (a bko-holt from before 3100b25):
        its image couldn't restore a dump that kept LUKS2 headers (review
        of oq12).  A recording takes it."""
        for over in ({"features": ["stick"]}, {"features": None}):
            doc = json.loads(api())
            doc.update(over)
            if over["features"] is None:
                del doc["features"]
            with self.subTest(over=over):
                subprocess.run(["rm", "-rf", os.path.join(self.tmp, "out")])
                r, m = self.run_marker(json.dumps(doc))
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("lists no luks_headers in its features", r.stderr)
                self.assertIn("3100b25", r.stderr)
                self.assertFalse(os.path.exists(os.path.join(self.tmp, "out", "backotter.json")))
                r, m = self.run_marker(json.dumps(doc), "--holt-api-unnamed-ok")
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_config_partition_is_256_mib(self):
        # Every image's since open question 13: the marker says so, and
        # config.img is that big (the stand-in mkfs leaves it as truncated).
        r, m = self.run_marker(api())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(m["config_partition"]["sectors"], 256 * 2048)
        self.assertEqual(os.path.getsize(os.path.join(self.tmp, "out", "config.img")), 256 << 20)

    def test_holt_api_is_required(self):
        env = dict(os.environ, PATH=self.stubs + ":" + os.environ["PATH"])
        r = subprocess.run([sys.executable, MARKER, "--platform", "linux", "--base-os", "ubuntu", "--base-release",
                            "26.04.1", "--kernel", "7.0.0-30-generic", "--config-part", "3",
                            os.path.join(self.tmp, "out")], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--holt-api", r.stderr)


HEREDOC = re.compile(r"^cat >(/tmp/\S+) <<'EOF'\n(.*?)^EOF\n", re.M | re.S)


def check_parts(platform):
    """PLATFORM/checks.sh's dump-schemas check: its command, the helpers it
    runs (typed earlier as heredocs: {path: text}), and the marker path and
    bko-holt it names."""
    with open(os.path.join(STICK, platform, "checks.sh")) as f:
        text = f.read()
    (line,) = [l for l in text.splitlines() if l.startswith("check dump-schemas ")]
    (cmd,) = shlex.split(line[len("check dump-schemas "):])
    helpers = {p: t for p, t in HEREDOC.findall(text) if p in cmd}
    body = "\n".join([cmd, *helpers.values()])
    marker = re.search(r"\bf=(\S+backotter\.json)", body)[1]
    holt = re.search(r"\$\((\S+) --version --json", body)[1]
    return cmd, helpers, marker, holt


class BootCheck(unittest.TestCase):
    """The check as boot-test.py runs it (`sh -c`, after its heredocs), against
    a marker as marker.py writes it and a bko-holt that prints its api document."""

    def run_check(self, platform, marker_doc, holt_api):
        cmd, helpers, marker, holt = check_parts(platform)
        with tempfile.TemporaryDirectory(prefix="bko-dscheck-") as t:
            m = os.path.join(t, "backotter.json")
            with open(m, "w") as f:
                json.dump(marker_doc, f, indent=2)     # marker.py's layout: the list over three lines
                f.write("\n")
            fake = os.path.join(t, "bin", "bko-holt")
            wr(fake, "#!/bin/sh\ntest \"$*\" = '--version --json' || exit 9\nprintf '%s\\n' " + shlex.quote(holt_api) + "\n", 0o755)

            def local(text):
                text = text.replace("f=" + marker, "f=" + m).replace("$(" + holt + " ", "$(" + fake + " ")
                for path in helpers:
                    text = text.replace(path, os.path.join(t, os.path.basename(path)))
                return text
            for path, text in helpers.items():
                wr(os.path.join(t, os.path.basename(path)), local(text))
            return subprocess.run(["sh", "-c", local(cmd)], capture_output=True, text=True)

    def test_every_platform(self):
        want = {"linux": "/cdrom/.disk/backotter.json", "haiku": "/boot/.disk/backotter.json"}
        # Platforms whose typed lines must stay short run it from a helper.
        helper = {"freebsd", "dragonfly", "illumos", "haiku"}
        base = {"format": "backotter-image", "schema": 1, "image_id": "x", "platform": "p",
                "zfs": {"version": "2.4.1", "features": ["a", "b"]}, "system_schemas": [1]}
        old_holt = json.loads(api())
        del old_holt["dump_schemas"]
        old_holt = json.dumps(old_holt, separators=(",", ":"))
        for p in PLATFORMS:
            with self.subTest(platform=p):
                cmd, helpers, marker, _ = check_parts(p)
                self.assertEqual(marker, want.get(p, "/.disk/backotter.json"))
                self.assertEqual(bool(helpers), p in helper, cmd)
                r = self.run_check(p, dict(base, dump_schemas=[1], config_partition={"number": 3}), api())
                self.assertEqual((r.returncode, r.stdout), (0, "marker [1], bko-holt [1]\n"), r.stderr)
                r = self.run_check(p, dict(base, dump_schemas=[1, 2]), api(dump_schemas=[1, 2]))
                self.assertEqual((r.returncode, r.stdout), (0, "marker [1,2], bko-holt [1,2]\n"), r.stderr)
                r = self.run_check(p, dict(base, dump_schemas=[1]), api(dump_schemas=[1, 2]))
                self.assertEqual((r.returncode, r.stdout), (1, "marker [1], bko-holt [1,2]\n"))
                r = self.run_check(p, base, api())
                self.assertEqual((r.returncode, r.stdout), (1, "marker none, bko-holt [1]\n"))
                r = self.run_check(p, dict(base, dump_schemas=[1]), "bko-holt 0.2.6")
                self.assertEqual((r.returncode, r.stdout), (1, "marker [1], bko-holt none\n"))
                r = self.run_check(p, dict(base, dump_schemas=[1]), old_holt)
                self.assertEqual((r.returncode, r.stdout), (1, "marker [1], bko-holt none\n"))
                # An image from before dump_schemas (every image built before
                # ae051fb): neither names them, and that passes.
                r = self.run_check(p, base, old_holt)
                self.assertEqual((r.returncode, r.stdout), (0, "marker none, bko-holt none\n"), r.stderr)
                # A marker that has the key but no list it can read is no such image.
                r = self.run_check(p, dict(base, dump_schemas="1"), old_holt)
                self.assertEqual((r.returncode, r.stdout), (1, "marker none, bko-holt none\n"))

    def test_typed_lines_stay_short(self):
        # The lines boot-test.py types (review of oq11): FreeBSD's uart(4)
        # keeps 300 characters; DragonFly, illumos and Haiku stay at their
        # old longest, under a 256-byte canonical line.
        limit = {"freebsd": 299, "dragonfly": 250, "illumos": 250, "haiku": 250}
        for p in PLATFORMS:
            with open(os.path.join(STICK, p, "checks.sh")) as f:
                (line,) = [l for l in f.read().splitlines() if l.startswith("check dump-schemas ")]
            with self.subTest(platform=p):
                self.assertLessEqual(len(line), limit.get(p, 400))


def assigned(path, name):
    """The number NAME is set to in the shell script PATH (one assignment)."""
    with open(path) as f:
        (v,) = re.findall(r"^\s*(?:\w+=\S+\s+)*" + name + r"=(\d+)\b", f.read(), re.M)
    return int(v)


class ConfigSize(unittest.TestCase):
    """Open question 13: one size, marker.py's CONFIG_MIB, everywhere it is
    written down.  FreeBSD's mkimg takes config.img's own size."""

    def setUp(self):
        with open(MARKER) as f:
            (mib,) = re.findall(r"^CONFIG_MIB = (\d+)$", f.read(), re.M)
        self.sectors = int(mib) * 2048

    def test_marker_default(self):
        self.assertEqual(self.sectors, 256 * 2048)

    def test_every_build_script(self):
        self.assertEqual(assigned(os.path.join(STICK, "make-img.sh"), "CONFIG_MIB") * 2048, self.sectors)
        for p in ("dragonfly", "haiku", "illumos", "netbsd"):
            with self.subTest(platform=p):
                build = os.path.join(STICK, p, "build.sh")
                self.assertEqual(assigned(build, "CONFIG_SECTORS"), self.sectors)
                with open(build) as f:
                    # Each checks config.img is that size before it lays it down.
                    self.assertRegex(f.read(), r"config\.img isn't \$CONFIG_SECTORS sectors")
        ob = os.path.join(STICK, "openbsd", "build.sh")
        self.assertEqual(assigned(ob, "CFG_SIZE"), self.sectors)
        with open(ob) as f:
            text = f.read()
        self.assertIn("ROOT_START=$((CFG_START + CFG_SIZE))", text)
        self.assertEqual((assigned(ob, "CFG_START") + self.sectors) % 2048, 0, "OpenBSD's root starts 1 MiB aligned")
        with open(os.path.join(STICK, "freebsd", "build.sh")) as f:
            self.assertIn("-p fat16b:=in/config.img", f.read())

    def test_bko_holt_and_bko_rescue_agree(self):
        with open(os.path.join(REPO, "holt", "src", "stick.rs")) as f:
            holt = f.read()
        with open(os.path.join(REPO, "rescue", "src", "app", "restore.rs")) as f:
            rescue = f.read()
        self.assertIn("pub const CONFIG_BYTES: u64 = 256 << 20;", holt)
        self.assertEqual(self.sectors * 512, 256 << 20)
        # The logs room_check keeps room for are the ones bko-rescue writes.
        self.assertIn("pub const LOG_MAX: u64 = 2 << 20;", holt)
        self.assertIn("pub const LOG_GZ_MAX: u64 = 2 * 1024 * 1024;", rescue)
        self.assertIn("pub const LOGS_KEPT: u64 = 10;", holt)
        self.assertIn("pub const LOGS_KEPT: usize = 10;", rescue)
        # The reports room_check's words count are the ones bko-rescue
        # keeps, and its save leaves the logs' room (open question 17).
        with open(os.path.join(REPO, "rescue", "src", "app", "report.rs")) as f:
            report = f.read()
        self.assertIn("pub const REPORTS_KEPT: u64 = 5;", holt)
        self.assertIn("pub const REPORTS_KEPT: usize = 5;", report)
        self.assertIn("pub const REPORT_MAX: u64 = 64 << 20;", holt)
        self.assertIn("pub const REPORT_MAX: u64 = 64 * 1024 * 1024;", report)
        self.assertIn("pub const REPORT_ROOM_MIN: u64 = LOG_GZ_MAX * (LOGS_KEPT as u64 + 1);", report)


if __name__ == "__main__":
    unittest.main()
