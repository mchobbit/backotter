"""stick/marker.py's dump_schemas (holt-json.md's open question 11), and every
platform's dump-schemas boot check (stick/PLATFORM/checks.sh).

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
           "dry_run": False, "documents": {"plan": [1]}, "dump_schemas": [1], "features": []}
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

    def test_a_bko_holt_that_names_none_reads_schema_1(self):
        doc = json.loads(api())
        del doc["dump_schemas"]
        r, m = self.run_marker(json.dumps(doc))
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

    def test_holt_api_is_required(self):
        env = dict(os.environ, PATH=self.stubs + ":" + os.environ["PATH"])
        r = subprocess.run([sys.executable, MARKER, "--platform", "linux", "--base-os", "ubuntu", "--base-release",
                            "26.04.1", "--kernel", "7.0.0-30-generic", "--config-part", "3",
                            os.path.join(self.tmp, "out")], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--holt-api", r.stderr)


def check_line(platform):
    """PLATFORM/checks.sh's dump-schemas check: its command, and the
    marker path and bko-holt it names."""
    with open(os.path.join(STICK, platform, "checks.sh")) as f:
        (line,) = [l for l in f.read().splitlines() if l.startswith("check dump-schemas ")]
    (cmd,) = shlex.split(line[len("check dump-schemas "):])
    marker = re.search(r"<(\S+backotter\.json)\)", cmd)[1]
    holt = re.search(r"h=\$\((\S+) --version --json", cmd)[1]
    return cmd, marker, holt


class BootCheck(unittest.TestCase):
    """The check as boot-test.py runs it (`sh -c`), against a marker as
    marker.py writes it and a bko-holt that prints its api document."""

    def run_check(self, platform, marker_doc, holt_api):
        cmd, marker, holt = check_line(platform)
        with tempfile.TemporaryDirectory(prefix="bko-dscheck-") as t:
            m = os.path.join(t, "backotter.json")
            with open(m, "w") as f:
                json.dump(marker_doc, f, indent=2)     # marker.py's layout: the list over three lines
                f.write("\n")
            fake = os.path.join(t, "bin", "bko-holt")
            wr(fake, "#!/bin/sh\ntest \"$*\" = '--version --json' || exit 9\nprintf '%s\\n' " + shlex.quote(holt_api) + "\n", 0o755)
            cmd = cmd.replace("<" + marker + ")", "<" + m + ")").replace("$(" + holt + " ", "$(" + fake + " ")
            return subprocess.run(["sh", "-c", cmd], capture_output=True, text=True)

    def test_every_platform(self):
        want = {"linux": "/cdrom/.disk/backotter.json", "haiku": "/boot/.disk/backotter.json"}
        base = {"format": "backotter-image", "schema": 1, "image_id": "x", "platform": "p",
                "zfs": {"version": "2.4.1", "features": ["a", "b"]}, "system_schemas": [1]}
        for p in PLATFORMS:
            with self.subTest(platform=p):
                self.assertEqual(check_line(p)[1], want.get(p, "/.disk/backotter.json"))
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


if __name__ == "__main__":
    unittest.main()
