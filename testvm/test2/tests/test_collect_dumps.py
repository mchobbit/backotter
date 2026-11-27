"""F16's tests (docs/design/img-speed.md §12 F16): testvm/test2/h1/collect-dumps.sh's hosts come
from testvm/hosts.json (the default amd64 hosts with HoltTest records), each reached by its row's
ssh target, and the collector is an argument (--collector H): any other host than this one gets a
private copy of the tree and runs the job there, so the raw dumps stay on it, never here; a host
that isn't in the table collects nothing itself.

No host, no ssh: ssh, scp and hostname are stand-ins, and each host is a directory
T/hosts/HOST (ssh and scp move a path of this host's to the other's).  Run from the worktree:
python3 -m unittest discover testvm/test2/tests.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
TESTVM = os.path.dirname(T2)

SSH = r'''#!/usr/bin/env python3
import os, re, subprocess, sys
a = sys.argv[1:]
i = 0
while a[i].startswith("-"):
    i += 2 if a[i] == "-o" else 1
tgt, cmd = a[i], " ".join(a[i + 1:])
root, me = os.environ["FAKE_ROOT"], os.environ["FAKE_ME"]
with open(os.path.join(root, "ssh.log"), "a") as f:
    f.write(f"{me} {tgt} {cmd}\n")
h = re.fullmatch(r"m@([a-z0-9-]+)\.local", tgt).group(1)
h = dict(x.split("=") for x in os.environ.get("FAKE_ALIAS", "").split(",") if x).get(h, h)
if h in os.environ.get("FAKE_DOWN", "").split(",") or not os.path.isdir(os.path.join(root, "hosts", h)):
    sys.exit(255)
cmd = cmd.replace(f"{root}/hosts/{me}/", f"{root}/hosts/{h}/")
env = {k: v for k, v in os.environ.items() if k not in ("BKO_HOSTS_JSON", "T2_HOST_ROOT", "H1_COLLECTOR")}
env.update(FAKE_ME=h, TMPDIR=os.path.join(root, "hosts", h, "tmp"))
sys.exit(subprocess.run(["bash", "-c", cmd], env=env).returncode)
'''

SCP = r'''#!/usr/bin/env python3
import os, re, shutil, sys
root, me = os.environ["FAKE_ROOT"], os.environ["FAKE_ME"]
a = [x for x in sys.argv[1:] if not x.startswith("-") and not x.startswith("BatchMode") and not x.startswith("ConnectTimeout")]
src, dest = a
m = re.fullmatch(r"m@([a-z0-9-]+)\.local:(.*)", src)
h = m.group(1)
path = m.group(2).replace(f"{root}/hosts/{me}/", f"{root}/hosts/{h}/")
with open(os.path.join(root, "scp.log"), "a") as f:
    f.write(f"{me} {src}\n")
try:
    shutil.copy2(path, dest)
except OSError:
    sys.exit(1)
'''

HOSTNAME = '#!/bin/sh\necho "$FAKE_ME"\n'

# leakscan.py's stand-in: it says where it ran and with what, and SCAN_RC is its scan's exit.
LEAKSCAN = r'''import os, sys
root, me = os.environ["FAKE_ROOT"], os.environ["FAKE_ME"]
with open(os.path.join(root, "leakscan.log"), "a") as f:
    f.write(me + " " + " ".join(sys.argv[1:]) + "\n")
if sys.argv[1] == "strings":
    print('{"class": "mac", "hex": "00"}')
    sys.exit(0)
sys.exit(int(os.environ.get("SCAN_RC_" + me, "0")))
'''

CLASSES = {"holt": {"families": {"a": ["g1", "g2"]}, "no_family": ["g3"], "excluded": {}}}
DUMP = {"format": "bko-holt", "storage": {}, "tool": {"version": "0.1"}, "generated": "2026-11-27"}


def wr(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    if mode is not None:
        os.chmod(path, mode)


def rd(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


class Collect(unittest.TestCase):
    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="h1-collect-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        tree = os.path.join(self.t, "tree", "testvm")
        os.makedirs(os.path.join(tree, "test2", "h1"))
        os.makedirs(os.path.join(tree, "test2", "t2lib"))
        shutil.copy(os.path.join(T2, "h1", "collect-dumps.sh"), os.path.join(tree, "test2", "h1"))
        shutil.copy(os.path.join(T2, "t2lib", "hosts.py"), os.path.join(tree, "test2", "t2lib"))
        shutil.copy(os.path.join(TESTVM, "hosts.sh"), tree)
        with open(os.path.join(TESTVM, "hosts.json")) as f:
            self.table = json.load(f)
        self.tree = tree
        wr(os.path.join(tree, "test2", "t2lib", "leakscan.py"), LEAKSCAN)
        wr(os.path.join(tree, "test2", "classes.json"), json.dumps(CLASSES))
        self.script = os.path.join(tree, "test2", "h1", "collect-dumps.sh")
        b = os.path.join(self.t, "bin")
        wr(os.path.join(b, "ssh"), SSH, 0o755)
        wr(os.path.join(b, "scp"), SCP, 0o755)
        wr(os.path.join(b, "hostname"), HOSTNAME, 0o755)
        self.env = {"PATH": b + ":" + os.environ["PATH"], "HOME": self.t, "FAKE_ROOT": self.t, "FAKE_ME": "chopin",
                    "LC_ALL": "C"}
        self.amd64 = [r["name"] for r in self.table["hosts"] if r["enabled"] and r["default"] and r.get("arch", "amd64") == "amd64"]
        for h in self.amd64 + ["mymac"]:
            os.makedirs(os.path.join(self.t, "hosts", h, "tmp"))
            os.makedirs(os.path.join(self.t, "hosts", h, "V"))
        self.save_table()

    def save_table(self):
        wr(os.path.join(self.tree, "hosts.json"), json.dumps(self.table))

    def p(self, h, *rest):
        return os.path.join(self.t, "hosts", h, *rest)

    def record(self, h, run, guest, result="PASS", age_s=0):
        k = self.p(h, "R", run, guest)
        wr(os.path.join(k, "dumps", "src.json"), json.dumps(dict(DUMP, run=run, host=h)))
        wr(os.path.join(k, "report.txt"), "== backup\nRESULT: %s\n" % result)
        t = time.time() - 1000 + age_s
        for n in ("report.txt", "dumps/src.json"):
            os.utime(os.path.join(k, n), (t, t))

    def run_it(self, *args, me="chopin", env=None, records=None):
        e = dict(self.env, FAKE_ME=me, TMPDIR=self.p(me, "tmp"), **(env or {}))
        base = ["--records", records or self.p(me, "R"), "--vms", self.p(me, "V")]
        return subprocess.run(["bash", self.script, *base, *args], capture_output=True, text=True, env=e, timeout=120)

    def prov(self, out):
        lines = rd(os.path.join(out, "provenance.tsv")).splitlines()
        cols = lines[0].split("\t")
        return {r["guest"]: r for r in (dict(zip(cols, l.split("\t"))) for l in lines[1:])}

    def standard(self):
        """g1 newest on copland, g2 on cinqcent, g3 only on choubox; cage has no records."""
        self.record("chopin", "r1", "g1", age_s=10)
        self.record("copland", "r2", "g1", age_s=500)
        self.record("cinqcent", "r3", "g2", age_s=300)
        self.record("chopin", "r1", "g2", age_s=10)
        self.record("choubox", "r4", "g3", age_s=200)
        self.record("czerny", "r5", "g1", age_s=900, result="FAIL")
        os.makedirs(self.p("czerny", "R"), exist_ok=True)

    def test_default_hosts_are_the_tables_with_records(self):
        self.standard()
        out = self.p("chopin", "out")
        r = self.run_it("--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("none there: cage", r.stderr)
        pv = self.prov(out)
        self.assertEqual((pv["g1"]["host"], pv["g1"]["run"]), ("copland", "r2"))
        self.assertEqual((pv["g2"]["host"], pv["g2"]["run"]), ("cinqcent", "r3"))
        self.assertEqual((pv["g3"]["host"], pv["g3"]["run"]), ("choubox", "r4"))
        self.assertEqual(json.loads(rd(os.path.join(out, "g3", "src.json")))["host"], "choubox")
        self.assertEqual(os.stat(out).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(os.path.join(out, "g1", "src.json")).st_mode & 0o777, 0o600)
        log = rd(os.path.join(self.t, "ssh.log"))
        for h in self.amd64:
            if h != "chopin":
                self.assertIn(f"chopin m@{h}.local test -d", log)
        self.assertNotIn("bigfoot", log)
        # The inventory runs on every host with records, not on cage.
        inv = [l for l in log.splitlines() if "python3 -I -" in l]
        self.assertEqual(sorted(l.split()[1] for l in inv), sorted(f"m@{h}.local" for h in self.amd64 if h not in ("chopin", "cage")))

    def test_non_default_host_is_not_asked(self):
        self.standard()
        for r in self.table["hosts"]:
            if r["name"] == "choubox":
                r["default"] = False
        self.save_table()
        out = self.p("chopin", "out")
        r = self.run_it("--out", out)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("no dump for: g3", r.stderr)
        self.assertNotIn("choubox", rd(os.path.join(self.t, "ssh.log")))

    def test_rows_ssh_target(self):
        self.standard()
        for r in self.table["hosts"]:
            if r["name"] == "copland":
                r["ssh"] = "m@cpl.local"
        self.save_table()
        out = self.p("chopin", "out")
        r = self.run_it("--out", out, env={"FAKE_ALIAS": "cpl=copland"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.prov(out)["g1"]["host"], "copland")
        self.assertIn("chopin m@cpl.local:", rd(os.path.join(self.t, "scp.log")))
        self.assertNotIn("m@copland.local", rd(os.path.join(self.t, "ssh.log")))

    def test_default_host_down_is_exit_1(self):
        self.standard()
        out = self.p("chopin", "out")
        r = self.run_it("--out", out, env={"FAKE_DOWN": "czerny"})
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("out of reach: czerny", r.stderr)
        self.assertFalse(os.path.exists(out))
        # Named hosts without it work.
        r = self.run_it("--out", out, "--hosts", "chopin,copland,cinqcent,choubox", env={"FAKE_DOWN": "czerny"})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_explicit_hosts_only(self):
        self.standard()
        out = self.p("chopin", "out")
        r = self.run_it("--out", out, "--hosts", "chopin,cinqcent")
        self.assertEqual(r.returncode, 1, r.stderr)
        pv = self.prov(out)
        self.assertEqual((pv["g1"]["host"], pv["g2"]["host"]), ("chopin", "cinqcent"))
        log = rd(os.path.join(self.t, "ssh.log"))
        self.assertNotIn("test -d", log)
        self.assertEqual({l.split()[1] for l in log.splitlines()}, {"m@cinqcent.local"})

    def test_not_a_farm_host_collects_nothing_itself(self):
        self.standard()
        out = self.p("mymac", "out")
        r = self.run_it("--out", out, me="mymac")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("--collector H", r.stderr)
        self.assertFalse(os.path.exists(out))
        self.assertEqual(rd(os.path.join(self.t, "ssh.log")), "")

    def test_collector_elsewhere_keeps_the_dumps_there(self):
        self.standard()
        src = os.path.join(self.t, "sources.txt")
        wr(src, "g1\ng3\n")
        out = self.p("mymac", "out")    # a path on the collector: copland's
        r = self.run_it("--out", out, "--collector", "copland", "--sources", src, me="mymac")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("collecting on copland (m@copland.local)", r.stderr)
        self.assertFalse(os.path.exists(out))
        pv = self.prov(self.p("copland", "out"))
        self.assertEqual(sorted(pv), ["g1", "g3"])
        self.assertEqual(pv["g1"]["host"], "copland")
        # copland's own dump was copied directly, the others' by scp from copland.
        scp = rd(os.path.join(self.t, "scp.log")).splitlines()
        self.assertEqual({l.split()[0] for l in scp}, {"copland"})
        # Nothing left of the private copies.
        self.assertEqual(os.listdir(self.p("copland", "tmp")), [])
        self.assertEqual(os.listdir(self.p("mymac", "tmp")), [])

    def test_collector_refuses_a_loop(self):
        r = self.run_it("--collector", "copland", me="cage", env={"H1_COLLECTOR": "1"})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("not the collector copland", r.stderr)

    def test_scan_on_each_host_with_its_own_strings(self):
        self.standard()
        out = self.p("chopin", "out")
        self.assertEqual(self.run_it("--out", out).returncode, 0)
        corpus = os.path.join(self.t, "corpus")
        wr(os.path.join(corpus, "g1", "src.json"), "{}")
        r = self.run_it("--out", out, "--scan", corpus, env={"SCAN_RC_choubox": "1"})
        self.assertEqual(r.returncode, 1, r.stderr)
        log = rd(os.path.join(self.t, "leakscan.log")).splitlines()
        scans = {l.split()[0]: l for l in log if l.split()[1] == "scan"}
        self.assertEqual(sorted(scans), sorted(h for h in self.amd64 if h != "cage"))
        self.assertIn("--raw " + out, scans["chopin"])
        self.assertNotIn("--raw", scans["copland"])
        self.assertEqual([n for n in os.listdir(out) if n.startswith(".h1-strings")], [])
        for h in self.amd64:
            self.assertEqual([n for n in os.listdir(self.p(h, "tmp")) if n.startswith("h1-scan")], [], h)

    def test_scan_via_a_collector_elsewhere(self):
        self.standard()
        out = self.p("mymac", "out")
        self.assertEqual(self.run_it("--out", out, "--collector", "czerny", me="mymac").returncode, 0)
        corpus = os.path.join(self.t, "corpus")
        wr(os.path.join(corpus, "g1", "src.json"), "{}")
        r = self.run_it("--out", out, "--collector", "czerny", "--scan", corpus, "--hosts", "czerny,cage", me="mymac")
        self.assertEqual(r.returncode, 0, r.stderr)
        log = rd(os.path.join(self.t, "leakscan.log")).splitlines()
        scans = {l.split()[0]: l for l in log if l.split()[1] == "scan"}
        self.assertEqual(sorted(scans), ["cage", "czerny"])
        self.assertIn("--raw " + self.p("czerny", "out"), scans["czerny"])
        self.assertFalse(os.path.exists(out))
        self.assertEqual(os.listdir(self.p("czerny", "tmp")), [])
        self.assertEqual(os.listdir(self.p("mymac", "tmp")), [])

    def test_scan_on_a_collector_without_records(self):
        """cage has no records, so it isn't a default host, but its raw twins are scanned all the same."""
        self.standard()
        out = self.p("cage", "out")
        self.assertEqual(self.run_it("--out", out, "--collector", "cage", me="mymac").returncode, 0)
        corpus = os.path.join(self.t, "corpus")
        wr(os.path.join(corpus, "g1", "src.json"), "{}")
        r = self.run_it("--out", out, "--collector", "cage", "--scan", corpus, me="mymac")
        self.assertEqual(r.returncode, 0, r.stderr)
        log = rd(os.path.join(self.t, "leakscan.log")).splitlines()
        scans = {l.split()[0]: l for l in log if l.split()[1] == "scan"}
        self.assertEqual(sorted(scans), sorted(self.amd64))
        self.assertIn("--raw " + out, scans["cage"])
        self.assertEqual([h for h in scans if "--raw" in scans[h]], ["cage"])

    def test_collector_elsewhere_refuses_a_relative_out(self):
        self.standard()
        r = self.run_it("--out", "raw", "--collector", "czerny", me="mymac")
        self.assertEqual(r.returncode, 2)
        self.assertIn("an absolute path", r.stderr)
        self.assertEqual(rd(os.path.join(self.t, "ssh.log")), "")


if __name__ == "__main__":
    unittest.main()
