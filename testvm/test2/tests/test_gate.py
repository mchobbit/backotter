"""P3's tests (GateTest2, G1): U3.1 layers.json -> inputs on a fixture repo, U3.2 the
parsers on real (scrubbed) output from the hosts, U3.3 the fast/slow order, the rerun
and the stop codes with fake layer runners and a fake host.  No host needed.

Fixtures (tests/fixtures/p3/): fork/NAME.report are testvm/audit/results.py compare's
reports of the hosts' 2026-11-04/05 fork runs (failed tests' output cut to 4 lines);
bincheck/*.out bincheck.sh on Chopin's bko-bin, bko-bin.prev-20261105 and round14;
holt/cargo-test-pass.log and steps-pass.tsv holt-test.sh on Chopin (holt at 6fff54b);
loop/summary-linux.json run-all.sh's summary of a linux run on Chopin (2026-11-01).
The rest are written in those tools' formats."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from t2lib import canonical, gate, keys  # noqa: E402

FX = os.path.join(HERE, "fixtures", "p3")
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
ENV = ("T2_MAC_ROOT", "T2_HOST_ROOT", "T2_COPY_ROOT", "T2_LEDGER", "T2_WORK", "BKO_GIT", "T2_LOOP_LINUX_FS",
       "T2_LOOP_SPARSE_FS", "IMG_GB")
_saved, _tmp = {}, None
H = {c: c * 64 for c in "abcdef0123456789"}


def setUpModule():
    global _tmp
    _tmp = tempfile.mkdtemp(prefix="t2-test-gate-")
    for k in ENV:
        _saved[k] = os.environ.get(k)
        os.environ.pop(k, None)
    os.environ.update(T2_MAC_ROOT=os.path.join(_tmp, "mac"), T2_HOST_ROOT=os.path.join(_tmp, "host"),
                      T2_COPY_ROOT=os.path.join(_tmp, "copy"), BKO_GIT=os.path.join(_tmp, "git"))


def tearDownModule():
    for k, v in _saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    shutil.rmtree(_tmp, ignore_errors=True)


def fx(*p):
    with open(os.path.join(FX, *p), encoding="utf-8") as f:
        return f.read()


def read_file(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def fx_json(*p):
    return json.loads(fx(*p))


# Facts the way facts_host.py gives them (the fields keys.py reads).
SUMS = dict({f"{o}/{t}-bko": H["a"] for o in ("linux", "freebsd", "netbsd", "openbsd", "dragonfly", "illumos")
              for t in ("restic", "rustic")}, **{"haiku/rustic-bko": H["b"]})
SETS = {"bko-bin": {"path": "/home/m/backotter-work/bko-bin", "sums": SUMS, "commits": {}, "digests": {},
                    "build_info": "bko-bin set"}}


def facts(host="chopin", holt_code=None, raw=None, pkgs=None):
    f = {"host": host, "t": "2026-11-08T05:00:00Z",
         "versions": {"kernel": "6.12.115+deb13-amd64", "zfs": "zfs-2.3.9-1", "rustc": "rustc 1.94.1 (e408947bf 2026-03-25)",
                      "go": "go version go1.25.10 linux/amd64",
                      "pkgs": dict({"binutils": "2.44-3", "util-linux": "2.41-5", "fdisk": "2.41-5", "gdisk": "1.0.10-2",
                                    "e2fsprogs": "1.47.2-3", "xfsprogs": "6.13.0-2", "btrfs-progs": "6.14-1",
                                    "dosfstools": "4.2-1.2", "cryptsetup-bin": "2.7.5-2", "f2fs-tools": "1.16.0-1.1",
                                    "jfsutils": "1.1.15-6", "zfsutils-linux": "2.3.9-1", "attr": "1:2.5.2-3",
                                    "acl": "2.3.2-2"}, **(pkgs or {}))},
         "sets": json.loads(json.dumps(SETS)), "holt_bins": {}, "guests": {}, "rescuers": {"images": {}, "bin": {}},
         "plumbing": {}, "host_inputs": {"/bkoscratch/test2/h1-raw": raw or "absent",
                                         "~/backotter-work/bko-audit/stock/restic-0.19.1/results.tsv": "sha256:" + H["c"],
                                         "~/backotter-work/bko-audit/stock/rustic_core-0.13.0/results.tsv": "sha256:" + H["d"],
                                         "~/backotter-work/bko-audit/stock/rustic-0.11.4/results.tsv": "sha256:" + H["e"],
                                         "~/.rustup/toolchains/1.94.1-x86_64-unknown-linux-gnu/bin/rustc": "sha256:" + H["f"],
                                         "/bkoscratch/tools/arch-root/usr/bin/bcachefs": "absent"}}
    if holt_code:
        f["holt_bins"] = {holt_code: {"path": f"/bkoscratch/test2/holt-bin/{holt_code}",
                                      "sums": {"linux": H["1"], "freebsd": H["2"], "netbsd": H["3"]}}}
    return f


def baseline():
    with open(os.path.join(REPO, gate.BASELINE_PATH), encoding="utf-8") as f:
        return json.load(f)


def bko_bin_commits():
    """bko-bin's commit files as facts_host.py reads them (2026-11-08): round rel4, and
    Haiku's rustic-bko from rustic 6ccec58."""
    c = {f"{o}/restic-bko": "5253b20ac68a876a8f60b9c90747882710cddef7"
         for o in ("linux", "freebsd", "netbsd", "openbsd", "dragonfly", "illumos")}
    c.update({f"{o}/rustic-bko": {"commit": "b518f42bcb1eb1488ed9d766b87a5506e94fdd89",
                                  "rustic_core": "487ec7cbfcb00158bec01f1a4b1db08c1127ee7f"}
              for o in ("linux", "freebsd", "netbsd", "openbsd", "dragonfly", "illumos")})
    c["haiku/rustic-bko"] = {"commit": "6ccec58a83f81d7430b433096ec60ec04d3abc12",
                             "rustic_core": "96596bb14e1cbc3ff3c8fd47e33f1f37deb2ed33"}
    return c


GIT = {"gitrepos": {"restic": "1" * 40, "rustic_core": "2" * 40, "rustic": "3" * 40}}


# --- U3.1 ----------------------------------------------------------------------------------

class LayersJson(unittest.TestCase):
    def setUp(self):
        self.layers = gate.load_layers(REPO)

    def test_universe_is_thirteen_cells(self):
        u = gate.universe(self.layers)
        self.assertEqual(len(u), 13, u)
        self.assertEqual(u, ["gate:holt", "gate:fork:restic", "gate:fork:rustic_core", "gate:fork:rustic",
                             "gate:fork-root:restic", "gate:bin:abi", "gate:bin:static", "gate:bin:version",
                             "gate:loop:linux@chopin", "gate:loop:linux@cinqcent", "gate:loop:sparse@chopin",
                             "gate:loop:sparse@cinqcent", "gate:selftest"])

    def test_family_layers(self):
        self.assertEqual(gate.family_layers("stic", self.layers),
                         {"fast": ["fork:restic", "fork:rustic_core", "fork:rustic", "fork-root:restic", "bin:abi",
                                   "bin:static", "bin:version"], "slow": ["loop:linux", "loop:sparse"]})
        self.assertEqual(gate.family_layers("holt", self.layers), {"fast": ["holt"], "slow": []})
        g = gate.family_layers("gate", self.layers)
        self.assertEqual(len(g["fast"]) + len(g["slow"]), 11)
        self.assertEqual(g["slow"], ["loop:linux", "loop:sparse"])

    def test_pins_and_parts_as_the_planner_reads_them(self):
        e = dict(gate.layer_entries(self.layers))
        self.assertEqual(e["holt"]["pin"], "chopin")         # R18: h1-raw is on Chopin
        self.assertEqual(e["fork:restic"]["pin"], "chopin")
        self.assertEqual(e["fork:rustic_core"]["pin"], "cinqcent")
        self.assertEqual(e["fork:rustic"]["pin"], "cinqcent")
        self.assertEqual(e["fork-root:restic"]["pin"], "chopin")
        self.assertEqual(e["selftest"]["pin"], "mac")
        for n in ("loop:linux", "loop:sparse"):
            self.assertTrue(e[n]["per_host"])
            self.assertEqual(e[n]["part"], "slow")
        self.assertEqual(sorted(n for n, x in e.items() if x["part"] == "fast"),
                         sorted(["holt", "fork:restic", "fork:rustic_core", "fork:rustic", "fork-root:restic",
                                 "bin:abi", "bin:static", "bin:version", "selftest"]))
        self.assertEqual(e["holt"]["reserve"], [2, 4])
        self.assertEqual(e["loop:linux"]["reserve"], [14, 4])   # 6 x HOST_JOBS + 2, HOST_JOBS 2
        self.assertEqual(e["loop:sparse"]["reserve"], [20, 4])  # IMG_GB 16 + 4

    def test_every_path_it_names_is_in_the_tree(self):
        """Every repo path or glob of a harness or judge list exists in this checkout."""
        src = keys.TreeSource(REPO)
        names = src.files()
        for n, e in gate.layer_entries(self.layers):
            for g in ("harness", "judge"):
                for p in e.get(g) or []:
                    if p.startswith("?"):
                        continue
                    if keys.is_glob(p):
                        self.assertTrue(keys.expand_glob(p, names), f"{n}: {p} matches nothing")
                    else:
                        self.assertIsNotNone(src.sha256(p), f"{n}: no {p}")

    def test_every_cell_resolves_and_its_row_fits(self):
        src = keys.TreeSource(REPO)
        specs = keys.Specs(src)
        for cell in gate.universe(self.layers):
            r = keys.resolve(cell, src, facts(), specs, dict(GIT, set="bko-bin"))
            self.assertTrue(r.inputs, cell)
            bad = [f"{g}/{n}" for g, d in r.inputs.items() for n, v in d.items() if v == "missing"]
            self.assertEqual(bad, [], f"{cell}: missing inputs {bad}")
            self.assertEqual(r.inputs.get("soft", {}), {}, cell)       # the gate has no soft inputs
            self.assertEqual(r.inputs.get("plumbing", {}), {}, cell)   # nor plumbing
            row = {"inputs": r.inputs, "groups": r.groups, "key": r.key}
            # gate:selftest keys every fixture (leakscan reads them): its budget is larger, and
            # its whole row, at the most fails a row holds, still fits a ledger line.
            budget = 40 * 1024 if cell == "gate:selftest" else 24 * 1024
            self.assertLess(len(canonical(row)), budget, f"{cell}: its inputs alone take {len(canonical(row))} B")
            if cell == "gate:selftest":
                o = gate.Outcome({"t2-self-test": "FAIL", "drive-self-test": "FAIL"}, ["x" * 300] * 30)

                class E:
                    run, word, family, set_name = "t2g-20261108T0500-4f2a", "GateTest2All", "gate", "bko-bin"
                full = gate.make_row(E, cell, 2, o, {"inputs": r.inputs, "key": r.key, "spec": None},
                                     "2026-11-08T05:00:00Z", "rerun", ["y" * 300] * 30, r.key, "mac-x" * 10)
                full.update(groups=r.groups, key=r.key, host="mac", commit="0" * 40, committed=True,
                            dirty={}, copy="6fff54b")
                self.assertLess(len(canonical(full)), 60000)

    def test_the_judge_keys_every_layer(self):
        """gate_judge.py holds every layer's verdict rules: a change to it makes them all due."""
        src = keys.TreeSource(REPO)
        specs = keys.Specs(src)
        for cell in gate.universe(self.layers):
            r = keys.resolve(cell, src, facts(), specs, dict(GIT, set="bko-bin"))
            self.assertIn("testvm/test2/t2lib/gate_judge.py", r.inputs.get("judge", {}), cell)
            self.assertNotIn("testvm/test2/t2lib/gate.py", r.inputs.get("judge", {}), cell)
        st = keys.resolve("gate:selftest", src, {"host": "mac"}, specs, {"set": "bko-bin"})
        fixtures = [n for n in st.inputs["harness"] if n.startswith("testvm/test2/tests/fixtures/")]
        self.assertGreater(len(fixtures), 100)                           # leakscan's input is keyed


class LayerInputs(unittest.TestCase):
    """U3.1: the inputs of each layer on a fixture repo (only the files layers.json names)."""

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="t2-gate-repo-", dir=_tmp)
        layers = gate.load_layers(REPO)
        files = {gate.LAYERS_PATH: json.dumps(layers)}
        for n, e in gate.layer_entries(layers):
            for g in ("harness", "judge"):
                for p in e.get(g) or []:
                    if not keys.is_glob(p):
                        files[p] = f"{p}\n"
        files.update({"holt/Cargo.toml": '[package]\nname = "bko-holt"\nversion = "0.2.4"\n',
                      "holt/src/main.rs": "fn main() {}\n", "holt/tests/cli.rs": "",
                      "holt/tests/corpus/debian13/src.json": "{}\n", "holt/README.md": "x\n",
                      "testvm/test2/t2lib/x.py": "", "testvm/stictest1/drive.py": "", "testvm/holttest1d/ht1.sh": "",
                      "testvm/test2/classes.json": "{}", "testvm/test2/estimates.tsv": "", "testvm/test2/tests/test_x.py": "",
                      "testvm/stictest1/debian12/check.sh": ""})
        for p, t in files.items():
            os.makedirs(os.path.dirname(os.path.join(cls.root, p)), exist_ok=True)
            with open(os.path.join(cls.root, p), "w") as f:
                f.write(t)
        cls.src = keys.TreeSource(cls.root)

    def res(self, cell, f=None, **opts):
        return keys.resolve(cell, self.src, f or facts(), keys.Specs(self.src), dict(GIT, set="bko-bin", **opts))

    def test_bin_abi(self):
        r = self.res("gate:bin:abi")
        b = r.inputs["bin"]
        self.assertEqual(b["freebsd/restic-bko"], "sha256:" + H["a"])
        self.assertEqual(b["freebsd/rustic-bko"], "sha256:" + H["a"])
        self.assertEqual(b["freebsd/bko-holt"], "absent")               # holt-bin/CODE not built
        self.assertNotIn("linux/restic-bko", b)
        self.assertEqual(r.inputs["judge"]["testvm/test2/gate/freebsd-14.5.abi"],
                         "sha256:" + keys.sha256_hex("testvm/test2/gate/freebsd-14.5.abi\n"))
        self.assertEqual(r.inputs["host"], {"pkg:binutils": "ver:2.44-3"})
        code = keys.holt_code(self.src)
        built = self.res("gate:bin:abi", facts(holt_code=code))
        self.assertEqual(built.inputs["bin"]["freebsd/bko-holt"], "sha256:" + H["2"])
        self.assertNotEqual(built.key, r.key)                            # R14: building bko-holt makes it due

    def test_bin_version_covers_every_os_and_bko_holt(self):
        r = self.res("gate:bin:version")
        b = r.inputs["bin"]
        for o in ("linux", "freebsd", "netbsd", "openbsd", "dragonfly", "illumos"):
            self.assertIn(f"{o}/restic-bko", b)
            self.assertIn(f"{o}/rustic-bko", b)
        self.assertEqual(b["haiku/rustic-bko"], "sha256:" + H["b"])
        self.assertNotIn("haiku/restic-bko", b)
        self.assertEqual(b["linux/bko-holt"], "absent")
        self.assertIn("holt/Cargo.toml", r.inputs["harness"])          # a version bump makes it due (T2.2)
        self.assertIn("testvm/test2/gate/baseline.json", r.inputs["judge"])

    def test_bin_static(self):
        r = self.res("gate:bin:static")
        self.assertEqual(sorted(n for n in r.inputs["bin"] if not n.endswith("#digest")),
                         ["linux/restic-bko", "linux/rustic-bko"])

    def test_holt_keys_its_tree_and_the_raw_dumps(self):
        a = self.res("gate:holt")
        self.assertIn("holt/src/main.rs", a.inputs["harness"])
        self.assertIn("holt/tests/corpus/debian13/src.json", a.inputs["harness"])
        self.assertIn("testvm/test2/gate/holt-test.sh", a.inputs["harness"])
        self.assertEqual(a.inputs["host"]["rustc"], "ver:rustc 1.94.1 (e408947bf 2026-03-25)")
        for p in ("util-linux", "fdisk", "gdisk", "e2fsprogs", "xfsprogs", "btrfs-progs", "dosfstools", "cryptsetup-bin"):
            self.assertIn("pkg:" + p, a.inputs["host"])                  # R23
        self.assertEqual(a.inputs["host"]["host:/bkoscratch/test2/h1-raw"], "absent")
        self.assertNotIn("bin", a.inputs)
        b = self.res("gate:holt", facts(raw="dir:" + H["9"]))
        self.assertNotEqual(a.key, b.key)                                # R18: h1-raw is a host input
        c = self.res("gate:holt", facts(pkgs={"gdisk": "1.0.10-3"}))
        self.assertNotEqual(a.key, c.key)
        self.assertEqual(a.pin, "chopin")

    def test_fork_layers_key_the_commits_tested(self):
        r = self.res("gate:fork:restic")
        self.assertEqual(r.inputs["harness"]["git:restic"], "git:" + "1" * 40)
        self.assertIn("testvm/audit/fork-tests.sh", r.inputs["harness"])
        self.assertIn("testvm/audit/audit-lib.sh", r.inputs["harness"])
        self.assertEqual(r.inputs["judge"]["host:~/backotter-work/bko-audit/stock/restic-0.19.1/results.tsv"],
                         "sha256:" + H["c"])
        self.assertEqual(r.inputs["host"], {"go": "ver:go version go1.25.10 linux/amd64"})
        u = self.res("gate:fork:rustic")
        self.assertEqual(u.inputs["harness"]["git:rustic"], "git:" + "3" * 40)
        self.assertEqual(u.inputs["harness"]["git:rustic_core"], "git:" + "2" * 40)   # rustic is built against it
        self.assertIn("stick/prep-rustic-src.sh", u.inputs["harness"])
        moved = keys.resolve("gate:fork:rustic", self.src, facts(), keys.Specs(self.src),
                             {"set": "bko-bin", "gitrepos": dict(GIT["gitrepos"], rustic_core="4" * 40)})
        self.assertNotEqual(moved.key, u.key)
        rc = self.res("gate:fork:rustic_core")
        self.assertNotIn("git:rustic", rc.inputs["harness"])

    def test_the_spec_checker_passes_it(self):
        """P4's check-manifests.py --spec over layers.json (R5 (c)): t2 self-test runs it."""
        chk = os.path.join(REPO, "testvm", "test2", "stic", "check-manifests.py")
        if not os.path.exists(chk):
            self.skipTest("no check-manifests.py (P4)")
        import subprocess
        r = subprocess.run([sys.executable, "-B", chk, "--spec", gate.LAYERS_PATH], cwd=REPO, capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, "\n".join(l for l in (r.stdout + r.stderr).splitlines() if "ERROR" in l)[:3000])

    def test_fork_root(self):
        r = self.res("gate:fork-root:restic")
        self.assertIn("testvm/test2/gate/restic-root-tests.txt", r.inputs["harness"])
        self.assertIn("testvm/test2/gate/fork-root.sh", r.inputs["harness"])
        self.assertEqual(r.inputs["harness"]["git:restic"], "git:" + "1" * 40)
        self.assertIn("kernel", r.inputs["host"])

    def test_loop_layers_are_per_host(self):
        a = self.res("gate:loop:linux@chopin", facts("chopin"))
        b = self.res("gate:loop:linux@cinqcent", facts("cinqcent"))
        self.assertNotEqual(a.key, b.key)                                # the cell name is in the key
        self.assertIn("testvm/compare/expected-coverage.json", a.inputs["judge"])
        self.assertIn("testvm/compare/verdict.py", a.inputs["judge"])
        self.assertIn("testvm/compare/compare.py", a.inputs["harness"])  # fs_roundtrip.py imports it (R5)
        self.assertEqual(a.inputs["host"]["kernel"], "ver:6.12.115+deb13-amd64")
        k = self.res("gate:loop:linux@chopin", facts("chopin", pkgs={"xfsprogs": "6.14.0-1"}))
        self.assertNotEqual(a.key, k.key)
        s = self.res("gate:loop:sparse@chopin", facts("chopin"))
        self.assertIn("testvm/compare/sparse-rt.sh", s.inputs["harness"])
        self.assertIn("testvm/compare/run-all.sh", s.inputs["harness"])     # run-all.sh as the spec checker reads it
        self.assertNotIn("pkg:f2fs-tools", s.inputs["host"])

    def test_selftest_keys_the_test2_code_and_specs(self):
        r = self.res("gate:selftest", {"host": "mac"})
        self.assertIn("testvm/test2/t2lib/x.py", r.inputs["harness"])
        self.assertIn("testvm/stictest1/drive.py", r.inputs["harness"])
        self.assertIn("testvm/test2/gate/layers.json", r.inputs["harness"])
        self.assertNotIn("holt/tests/corpus/debian13/src.json", r.inputs["harness"])   # gate:holt's, by holt/**
        self.assertNotIn("host", r.inputs)


# --- U3.2 ----------------------------------------------------------------------------------

class Parsers(unittest.TestCase):
    def test_cargo_pass_log_from_chopin(self):
        c = gate.parse_cargo(fx("holt", "cargo-test-pass.log"))
        self.assertEqual((c["results"], c["passed"], c["failed"], c["ignored"]), (8, 247, 0, 2))   # 249 #[test]
        self.assertEqual(c["failed_tests"], [])

    def test_cargo_failure_and_compile_error(self):
        c = gate.parse_cargo(fx("holt", "cargo-test-fail.log"))
        self.assertEqual((c["passed"], c["failed"]), (223, 1))
        self.assertEqual(c["failed_tests"], ["load::commands::tests::sfdisk_script_for_gpt"])
        e = gate.parse_cargo(fx("holt", "cargo-test-compile.log"))
        self.assertEqual(e["results"], 0)
        self.assertEqual(e["errors"][0], "cannot find value `stage` in this scope")

    def holt(self, steps, cargo="cargo-test-pass.log", diff=None, corpus=None, raw=None):
        logs = {"cargo-test.log": fx("holt", cargo)}
        if corpus:
            logs["redact-corpus.log"] = fx("holt", corpus)
        return gate.holt_outcome(gate.parse_steps(fx("holt", steps)), logs,
                                 fx_json("holt", diff) if diff else None, fx("holt", raw) if raw else None,
                                 {"ram_peak_kib": "812345"})

    def test_holt_without_h1_fails(self):
        """holt at 6fff54b had no golden test or corpus (golden and redact-corpus skip): with H1
        in the tree that is a broken copy, a FAIL, never a PASS on cargo test alone."""
        o = self.holt("steps-pass.tsv")
        self.assertEqual(o.needs, {"cargo-test": "PASS", "golden-plans": "FAIL", "invariants": "FAIL",
                                   "redact-dumps": "FAIL"})
        self.assertEqual(o.verdict, "FAIL")
        self.assertTrue(any(f.startswith("golden-plans: skipped") for f in o.fails), o.fails)
        self.assertTrue(any(f.startswith("redact-corpus: skipped") for f in o.fails), o.fails)
        self.assertEqual(o.extra["cargo"]["passed"], 247)
        self.assertEqual(o.ram_peak_kib, 812345)

    def test_holt_full_pass_and_diff(self):
        o = self.holt("steps-full.tsv", diff="diff-clean.json", corpus="redact-corpus-pass.log", raw="redact-raw-pass.tsv")
        self.assertEqual(o.needs, {"cargo-test": "PASS", "golden-plans": "PASS", "invariants": "PASS",
                                   "redact-dumps": "PASS"})
        self.assertEqual(o.extra["plan_diff"], {})
        self.assertEqual(o.extra["redact"], "raw 3 ok; corpus 3 ok")
        d = self.holt("steps-full.tsv", diff="diff-differ.json", corpus="redact-corpus-pass.log", raw="redact-raw-pass.tsv")
        self.assertEqual(d.needs["golden-plans"], "DIFF")
        self.assertEqual(d.verdict, "PASS")                              # R23: a DIFF doesn't fail the gate
        self.assertEqual(sorted(d.extra["plan_diff"]), ["fedora44", "rocky10-raid5"])
        self.assertEqual(d.note, "plan diff not yet accepted: fedora44, rocky10-raid5")
        self.assertEqual(d.extra["invariants_skipped"], {"debian13": 1})   # counts; messages stay on the host

    def test_holt_on_the_h1_corpus_from_chopin(self):
        """holt-test.sh on Chopin over WT's holt with H1's corpus (2026-11-08): 249 passed
        (golden_ filtered out of cargo-test), the golden plans and 45 redacted dumps clean."""
        steps = gate.parse_steps(fx("holt", "corpus", "steps.tsv"))
        logs = {n: fx("holt", "corpus", n) for n in ("cargo-test.log", "golden.log", "redact-corpus.log")}
        o = gate.holt_outcome(steps, logs, fx_json("holt", "corpus", "diff.json"), None,
                              gate.parse_kv(fx("holt", "corpus", "info.txt")))
        self.assertEqual(o.verdict, "PASS")
        self.assertEqual(o.needs, {"cargo-test": "PASS", "golden-plans": "PASS", "invariants": "PASS",
                                   "redact-dumps": "PASS"})
        self.assertEqual(o.extra["cargo"]["passed"], 249)
        self.assertEqual(o.extra["corpus"], 45)
        self.assertEqual(o.extra["redact"], "corpus 45 ok")
        self.assertEqual(o.extra["plan_diff"], {})
        self.assertTrue(all(isinstance(n, int) for n in o.extra["invariants_skipped"].values()))

    def test_holt_failures(self):
        o = self.holt("steps-full.tsv", diff="diff-invariants.json", corpus="redact-corpus-pass.log")
        self.assertEqual(o.needs["invariants"], "FAIL")
        self.assertEqual(o.verdict, "FAIL")
        self.assertTrue(any("omnios58-mirror: / mounted" in f for f in o.fails), o.fails)
        f = self.holt("steps-full.tsv", cargo="cargo-test-fail.log", diff="diff-clean.json",
                      corpus="redact-corpus-fail.log", raw="redact-raw-fail.tsv")
        self.assertEqual((f.needs["cargo-test"], f.needs["redact-dumps"]), ("FAIL", "FAIL"))
        self.assertIn("cargo-test: 1 failed: load::commands::tests::sfdisk_script_for_gpt", f.fails)
        joined = " ".join(f.fails)
        self.assertIn("redact-raw: exit 0, 1 ok: FAIL debian13", joined)   # only the guest's name leaves the host
        self.assertIn("FAIL debian13", joined)
        steps = gate.parse_steps("cargo-test\t0\t9\t\ngolden-plans\t101\t3\t\n")
        g = gate.holt_outcome(steps, {"cargo-test.log": fx("holt", "cargo-test-pass.log")}, None)
        self.assertEqual(g.needs["golden-plans"], "FAIL")

    def test_fork_tests_that_stopped_running(self):
        base = baseline()
        report = fx("fork", "restic.report")
        self.assertEqual(gate.fork_outcome("restic", report, base).needs["suite"], "PASS")
        skipped = report.replace("Skipped on the fork, passing on stock: 0\n",
                                 "Skipped on the fork, passing on stock: 1\n  a/b :: TestGone  (stock: pass)\n")
        o = gate.fork_outcome("restic", skipped, base)
        self.assertEqual((o.needs["suite"], o.verdict), ("FAIL", "FAIL"))
        self.assertIn("a/b :: TestGone (skipped on the fork, passing on stock)", "\n".join(o.fails))
        missing = report.replace("On stock, missing on the fork: 0\n",
                                 "On stock, missing on the fork: 1\n  a/b :: TestGone  (stock: pass)\n")
        self.assertEqual(gate.fork_outcome("restic", missing, base).needs["suite"], "FAIL")
        lines = report.splitlines(True)
        i = next(i for i, l in enumerate(lines) if l.startswith("fork:"))
        lines[i + 1] = "  tests: 0 pass, 0 fail, 0 skip; builds: 13 pass, 0 fail\n"
        o = gate.fork_outcome("restic", "".join(lines), base)
        self.assertEqual((o.needs["suite"], o.verdict), ("FAIL", "FAIL"))
        self.assertIn("the fork's suite passed no test", "\n".join(o.fails))

    def test_fork_reports(self):
        base = baseline()
        r = gate.fork_outcome("restic", fx("fork", "restic.report"), base)
        self.assertEqual(r.needs, {"suite": "PASS", "build-targets": "PASS"})
        self.assertEqual(r.extra["added"], 187)
        self.assertEqual(r.extra["counts"], "tests: 1873 pass, 0 fail, 48 skip; builds: 13 pass, 0 fail")
        u = gate.fork_outcome("rustic", fx("fork", "rustic.report"), base)
        self.assertEqual(u.needs, {"suite": "LIMIT", "build-targets": "PASS"})   # baseline.json's known failure
        self.assertEqual(u.verdict, "PASS")
        self.assertIn("tests/backup_restore.rs [backup_restore] :: test_backup_and_check_passes", u.extra["limits"])
        n = gate.fork_outcome("rustic", fx("fork", "rustic.report"), {})
        self.assertEqual((n.needs["suite"], n.verdict), ("FAIL", "FAIL"))
        self.assertEqual(n.fails, ["fork-only: tests/backup_restore.rs [backup_restore] :: test_backup_and_check_passes"
                                   " (stock: pass)"])
        # A baseline entry classes the one failure it names, not every failure of that test.
        damaged = "".join(("      | rustic check failed: repository is DAMAGED\n" if l.startswith("      |") else l)
                          for l in fx("fork", "rustic.report").splitlines(True))
        d = gate.fork_outcome("rustic", damaged, base)
        self.assertEqual((d.needs["suite"], d.verdict), ("FAIL", "FAIL"))
        c = gate.fork_outcome("rustic_core", fx("fork", "rustic_core.report"), base)
        self.assertEqual(c.needs, {"suite": "PASS", "build-targets": "PASS"})
        self.assertEqual(c.extra["added"], 186)
        built = fx("fork", "restic.report").replace(
            "Build targets that fail on the fork, not on stock: 0\n",
            "Build targets that fail on the fork, not on stock: 1\n  #cross freebsd/amd64  (stock: pass)\n      | x\n")
        b = gate.fork_outcome("restic", built, base)
        self.assertEqual(b.needs["build-targets"], "FAIL")
        self.assertEqual(b.fails, ["build: #cross freebsd/amd64 (stock: pass)"])
        h = gate.fork_outcome("restic", None, base, "fork-tests.sh exit 2, no restic.report")
        self.assertEqual(h.verdict, "HARNESS")

    def test_loop_summaries(self):
        o = gate.loop_outcome(fx_json("loop", "summary-linux.json"), 0, {"ram_peak_kib": "9123456"})
        self.assertEqual(len(o.needs), 15)
        self.assertTrue(all(v == "PASS" for v in o.needs.values()))
        self.assertIn("zfs-onto-ext4/restic-bko", o.needs)
        self.assertEqual(o.ram_peak_kib, 9123456)
        self.assertEqual(o.extra["peaks"], {"ram_kib": 9123456})
        f = gate.loop_outcome(fx_json("loop", "summary-linux-fail.json"), 1, {})
        self.assertEqual(f.verdict, "FAIL")
        self.assertEqual(f.needs["zfs-onto-ext4/restic-bko"], "FAIL")
        self.assertEqual(f.needs["bcachefs"], "FAIL")                    # a SKIP not allowed fails (verdict.py's rule)
        self.assertEqual(f.needs["jfs"], "SKIP")                          # an allowed one doesn't
        self.assertIn("zfs-onto-ext4/restic-bko: FAIL: xattr_order: user.zz-first not first; log tail: ...", f.fails)
        self.assertEqual(gate.loop_outcome(None, 2, {}, "run-all: refused: no /x\n").verdict, "HARNESS")
        self.assertEqual(gate.loop_outcome(fx_json("loop", "summary-linux.json"), 2, {}).verdict, "HARNESS")

    def test_bincheck_abi_and_static(self):
        o = gate.bin_outcome("abi", fx("bincheck", "abi-bko-bin.out"), 0)
        self.assertEqual(o.needs, {"freebsd/restic-bko": "PASS", "freebsd/rustic-bko": "PASS",
                                   "holt/freebsd/bko-holt": "PASS"})
        p = gate.bin_outcome("abi", fx("bincheck", "abi-prev.out"), 1)
        self.assertEqual(p.verdict, "FAIL")
        self.assertIn("setgroups@FBSD_1.8", p.fails[0])
        self.assertIn("posix_spawn_file_actions_addchdir@FBSD_1.9", p.fails[0])
        s = gate.bin_outcome("static", fx("bincheck", "static-bko-bin.out"), 0)
        self.assertEqual(s.verdict, "PASS")
        r = gate.bin_outcome("static", fx("bincheck", "static-round14.out"), 1)
        self.assertIn("NT_GNU_ABI_TAG", r.fails[0])
        self.assertIn("nsswitch.conf", r.fails[0])
        self.assertEqual(gate.bin_outcome("abi", "", 2).verdict, "HARNESS")
        for out, rc in (("abi-prev.out", 1), ("static-round14.out", 1)):  # never a LIMIT
            self.assertNotIn("LIMIT", gate.bin_outcome(out.split("-")[0], fx("bincheck", out), rc,
                                                      baseline()).needs.values())

    def bko_bin_commits(self):
        return bko_bin_commits()

    def test_bincheck_version_and_agreement(self):
        base = baseline()
        sums = {"haiku/rustic-bko": "12181a04a6f5143c5265c9ec38ca7af9f6d3ab560882326cf278aebb21386e65"}
        o = gate.bin_outcome("version", fx("bincheck", "version-bko-bin.out"), 0, base, sums, self.bko_bin_commits())
        self.assertEqual(o.needs["agree"], "LIMIT")                       # bko-bin's Haiku binary, classed
        self.assertEqual(o.verdict, "PASS")
        self.assertEqual(o.extra["versions"]["haiku/rustic-bko"], "0.11.4+backotter.7")
        self.assertEqual(o.extra["versions"]["holt/bko-holt"], "0.2.4")
        n = gate.bin_outcome("version", fx("bincheck", "version-bko-bin.out"), 0, {}, sums, self.bko_bin_commits())
        self.assertEqual(n.needs["agree"], "FAIL")
        self.assertIn("haiku/rustic-bko is rustic 6ccec58a83f8", n.fails[-1])
        other = dict(sums, **{"haiku/rustic-bko": "0" * 64})              # another binary than the one classed
        self.assertEqual(gate.bin_outcome("version", fx("bincheck", "version-bko-bin.out"), 0, base, other,
                                          self.bko_bin_commits()).needs["agree"], "FAIL")
        one = {k: v for k, v in self.bko_bin_commits().items() if not k.startswith("haiku/")}
        txt = "\n".join(l for l in fx("bincheck", "version-bko-bin.out").splitlines() if "haiku/rustic" not in l)
        self.assertEqual(gate.bin_outcome("version", txt, 0, base, sums, one).needs["agree"], "PASS")
        p = gate.bin_outcome("version", fx("bincheck", "version-planted.out"), 1, base, sums, self.bko_bin_commits())
        self.assertEqual(p.needs["linux/rustic-bko"], "FAIL")
        self.assertEqual(p.verdict, "FAIL")
        bad = txt.replace("PASS version freebsd/restic-bko [0.19.1+backotter.5]", "PASS version freebsd/restic-bko [0.19.1+backotter.4]")
        self.assertEqual(gate.bin_outcome("version", bad, 0, base, sums, one).needs["agree"], "FAIL")

    def test_root_list_is_reviewed(self):
        text = read_file(os.path.join(REPO, gate.ROOT_LIST_PATH))
        runs, outs = gate.parse_root_list(text)
        self.assertEqual(len(runs), 20)
        self.assertEqual(sorted({p for p, _ in runs}), ["internal/fs", "internal/restorer"])
        for line in text.splitlines():
            if line.startswith("run "):
                self.assertIn("#", line, line)                            # each run line carries its review
                self.assertGreater(len(line.split("#", 1)[1].strip()), 40, line)

    def forkroot(self, d):
        listing = read_file(os.path.join(REPO, gate.ROOT_LIST_PATH))
        files = {n: fx("forkroot", d, n) for n in os.listdir(os.path.join(FX, "forkroot", d))}
        return gate.fork_root_outcome(listing, files)

    def test_fork_root_pass(self):
        o = self.forkroot("pass")
        self.assertEqual(o.needs["TestFATAttributesOnVfat"], "PASS")
        self.assertEqual(o.needs["TestNodeProjectIDBcachefs"], "PASS")
        self.assertEqual(o.needs["TestRestorerOverRestrictingFlags"], "PASS")
        self.assertEqual(o.needs["root-list"], "PASS")
        self.assertEqual(len(o.needs), 21)
        self.assertEqual(o.verdict, "PASS")
        self.assertEqual(o.extra["cleanup"], "nothing was left mounted or attached")
        self.assertIsNone(o.note)

    def test_fork_root_a_listed_test_that_skips_did_not_run(self):
        listing = read_file(os.path.join(REPO, gate.ROOT_LIST_PATH))
        files = {n: fx("forkroot", "pass", n) for n in os.listdir(os.path.join(FX, "forkroot", "pass"))}
        files["run-internal_fs.log"] = files["run-internal_fs.log"].replace(
            "--- PASS: TestNodeProjectIDBcachefs (0.03s)",
            "    flags_linux_test.go:310: bcachefs format: exit status 134: malloc(): unaligned tcache chunk detected\n"
            "--- SKIP: TestNodeProjectIDBcachefs (0.03s)")
        o = gate.fork_root_outcome(listing, files)
        self.assertEqual((o.needs["TestNodeProjectIDBcachefs"], o.verdict), ("FAIL", "FAIL"))
        self.assertIn("TestNodeProjectIDBcachefs: skipped, so it did not run: ", "\n".join(o.fails))

    def test_fork_root_failures(self):
        o = self.forkroot("fail")
        self.assertEqual(o.verdict, "FAIL")
        self.assertEqual(o.needs["TestBcachefsDirOptionsInherited"], "FAIL")   # skipped for root, as root
        self.assertEqual(o.needs["TestFATAttributesOnVfat"], "FAIL")
        self.assertEqual(o.needs["TestXFSOptionsOnXFS"], "FAIL")           # the binary panicked in it
        self.assertEqual(o.needs["TestPreallocateBtrfs"], "FAIL")           # never ran: the binary ended
        self.assertEqual(o.needs["TestRestorerXFSOptions"], "FAIL")         # its package didn't compile
        self.assertEqual(o.needs["TestClearRestrictingFlagsFile"], "PASS")
        self.assertEqual(o.needs["root-list"], "FAIL")
        joined = "\n".join(o.fails)
        self.assertIn("TestPreallocateBtrfs: didn't run: the test binary ended first", joined)
        self.assertIn("internal/restorer didn't compile", joined)
        self.assertIn("internal/fs TestBrandNewMountTest", joined)
        self.assertIn("unmounted /mnt/bko-ram/", o.note)                   # R23: the row says what it removed
        self.assertEqual(len(o.extra["cleanup"]), 2)

    def test_root_list_on_the_m_run_of_d2112d6(self):
        """fork-root.sh's skips.tsv of Chopin's fork run of restic d2112d6 (2026-11-04): its 13
        root skips are all on the list (TestPreallocateBtrfs came after it)."""
        files = {n: fx("forkroot", "pass", n) for n in os.listdir(os.path.join(FX, "forkroot", "pass"))}
        files["skips.tsv"] = fx("forkroot", "skips-d2112d6.tsv")
        rootish = [l for l in files["skips.tsv"].splitlines() if gate.ROOTISH.search(l.split("\t")[2])]
        self.assertEqual(len(rootish), 13)
        self.assertFalse(any("TestZFSDosFlagsRoundTrip" in l or "TestBackupVSS" in l for l in rootish))
        o = gate.fork_root_outcome(read_file(os.path.join(REPO, gate.ROOT_LIST_PATH)), files)
        self.assertEqual(o.needs["root-list"], "PASS")

    def test_go_v_parser(self):
        r = gate.parse_go_v(fx("forkroot", "pass", "run-internal_restorer.log"))
        self.assertEqual(r["TestRestorerOverRestrictingFlags"][0], "PASS")
        self.assertEqual(r["TestRestorerTargetNodeMountedBelow"][0], "PASS")
        self.assertNotIn("TestRestorerOverRestrictingFlags/backotter", r)
        f = gate.parse_go_v(fx("forkroot", "fail", "run-internal_fs.log"))
        self.assertEqual(f["TestXFSOptionsOnXFS"][0], "NORESULT")
        self.assertEqual(f["TestBcachefsDirOptionsInherited"], ("SKIP", ["bcachefs_linux_test.go:55: needs root, to mount a file system"]))

    def test_selftest_outcome(self):
        o = gate.selftest_outcome({"t2-self-test": (0, "ok"), "drive-self-test": (1, "a\nFAIL: queue\n")})
        self.assertEqual(o.needs, {"t2-self-test": "PASS", "drive-self-test": "FAIL"})
        self.assertEqual(o.fails, ["drive-self-test: exit 1: a | FAIL: queue"])
        m = gate.selftest_outcome({"t2-self-test": (None, "no testvm/test2/t2")})
        self.assertEqual((m.needs, m.verdict), ({"t2-self-test": "FAIL"}, "FAIL"))   # in the tree: never a SKIP

    def test_no_needs_is_never_a_pass(self):
        self.assertEqual(gate.verdict_of({}), "HARNESS")
        self.assertEqual(gate.Outcome({}).verdict, "HARNESS")
        h = gate.harness(None)
        self.assertEqual((h.verdict, h.fails), ("HARNESS", ["harness: unknown harness failure"]))
        self.assertTrue(gate.harness(gate.JobFailure("x", still_running=True)).still_running)
        self.assertFalse(gate.harness("x").still_running)

    def test_version_unchecked_binaries_fail(self):
        commits = {"linux/restic-bko": "1" * 40, "openbsd/restic-bko": "1" * 40, "haiku.rel4/rustic-bko": "2" * 40}
        exp = {"linux/restic-bko": ("0.19.1+backotter.5", ""), "haiku.rel4/rustic-bko": ("0.11.4+backotter.6", "")}
        text = ("PASS version linux/restic-bko [0.19.1+backotter.5]: prints 0.19.1+backotter.5\n"
                "SKIP version openbsd/restic-bko [0.18.0+backotter.1]: holds 0.18.0+backotter.1; no expected version\n"
                "SKIP version netbsd/rustic-bko [0.11.4+backotter.6]: holds 0.11.4+backotter.6; no expected version\n"
                "SKIP version holt/freebsd/bko-holt: not built (no such file)\n")
        o = gate.version_unchecked(gate.bin_outcome("version", text, 0), commits, exp)
        self.assertEqual(o.verdict, "FAIL")
        self.assertEqual(o.needs["openbsd/restic-bko"], "FAIL")          # in commits, no expectation
        self.assertEqual(o.needs["haiku.rel4/rustic-bko"], "FAIL")       # expected, but no line
        self.assertEqual(o.needs["netbsd/rustic-bko"], "FAIL")           # a set binary that SKIPs
        self.assertEqual(o.needs["holt/freebsd/bko-holt"], "SKIP")       # bko-holt not built yet
        self.assertIn("haiku.rel4/rustic-bko: bincheck.sh gave it no result line", o.fails)

    def test_reserve_from_peaks(self):
        e = {"reserve": [14, 4], "peak_reserve": True}
        self.assertEqual(gate.reserve_for(e, [], "gate:loop:linux@chopin"), (14.0, 4.0))
        rows = [{"cell": "gate:loop:linux@chopin", "kind": "run", "t": f"2026-11-0{i}T00:00:00Z", "ram_peak_kib": k,
                 "verdict": "PASS", "meta": {"loop": {"linux_fs": "default", "sparse_fs": "default"}}}
                for i, k in ((1, 20 * 1048576), (2, 4 * 1048576), (3, 5 * 1048576), (4, 3 * 1048576))]
        self.assertEqual(gate.reserve_for(e, rows, "gate:loop:linux@chopin"), (5.8, 4.0))   # 1.1 x 5 + 0.25
        self.assertEqual(gate.reserve_for({"reserve": [2, 4]}, rows, "gate:holt"), (2.0, 4.0))
        # Cut-down runs and runs that didn't finish say nothing of a whole run's peak.
        short = [dict(rows[0], t=f"2026-11-1{i}T00:00:00Z", ram_peak_kib=1572864,
                      meta={"loop": {"linux_fs": "xfs", "sparse_fs": "default"}}) for i in range(3)]
        harn = [dict(rows[0], t=f"2026-11-2{i}T00:00:00Z", ram_peak_kib=200, verdict="HARNESS") for i in range(3)]
        self.assertEqual(gate.reserve_for(e, rows + short + harn, "gate:loop:linux@chopin"), (5.8, 4.0))
        self.assertTrue(gate.full_run({"verdict": "FAIL"}))
        self.assertFalse(gate.full_run({"verdict": "LOST"}))


# --- U3.3 ----------------------------------------------------------------------------------

class FakeIO:
    """A host without a host: sh, start, alive and fetch answer from canned state; script
    measures and appends the way HOST_SCRIPT would."""

    def __init__(self, key="k" * 64, exit_after=1, never_exit=False):
        self.calls, self.rows, self.key = [], [], key
        self.exit_after, self.never_exit = exit_after, never_exit
        self.polls, self.needles = {}, []
        self.lock = threading.Lock()

    def sh(self, host, cmd, timeout=60, input=None):
        with self.lock:
            self.calls.append(("sh", host, cmd))
        if "cat .exit" in cmd:
            n = self.polls.get(cmd, 0) + 1
            self.polls[cmd] = n
            if self.never_exit or n < self.exit_after:
                return 0, "\n", ""
            return 0, "0\n1000\n", ""
        return 0, "", ""

    def start(self, host, cmd, log, pidfile):
        self.calls.append(("start", host, cmd))
        return 4242

    def alive(self, host, pid, needle=None):
        self.needles.append(needle)
        return not self.never_exit

    def fetch(self, host, d, names):
        out = {}
        if ".submitted" in names:
            out = {".submitted": "990\n", ".began": "1000\n", ".ended": "1100\n", ".exit": "0\n"}
        return out

    def script(self, host, script, data, timeout=120):
        with self.lock:
            if data.get("measure"):
                return {"measure": {c: {"inputs": {"harness": {"x": "absent"}}, "key": self.key,
                                        "groups": {}, "spec": None, "unknown": []} for c in data["measure"]}}
            if data.get("append"):
                self.rows += data["append"]
                return {"ids": [[f"{host}-20261108T050000Z-{len(self.rows):08x}", True] for _ in data["append"]]}
        return {}

    def local(self, argv, cwd=None, env=None, timeout=None):
        return 0, ""

    def sleep(self, s):
        pass


class PC:
    def __init__(self, cell, status="due", rule="changed:harness", key="k" * 64, host=None):
        self.cell, self.status, self.rule, self.reasons = cell, status, rule, ["testvm/x 4be1..->9a07.."]
        self.keys = {host: key} if host else {}
        self.key, self.placed, self.host = key, None, "any"


class Plan:
    def __init__(self, cells):
        self.cells = cells
        self.family, self.word = "gate", "GateTest2"
        self.set = {"name": "bko-bin", "dir": "~/backotter-work/bko-bin"}


class Ctx:
    def __init__(self):
        self.checkout = REPO
        self.hosts = ["chopin", "cinqcent"]
        self.facts = {"chopin": facts("chopin"), "cinqcent": facts("cinqcent")}
        self.ledger = []
        self.options = {}


class RS:
    """run.py's RunState, as much of it as the gate reads."""

    def __init__(self, run="t2g-20261108T0500-4f2a"):
        self.run, self.family = run, "gate"
        self.word = type("W", (), {"text": "GateTest2", "word": "GateTest2"})()
        self.mac_root = os.environ["T2_MAC_ROOT"]
        self.host_root = "/bkoscratch/t2core-p3-test"
        self.copy = {"chopin": {"id": "6fff54b", "path": "/home/m/c/6fff54b"},
                     "cinqcent": {"id": "6fff54b", "path": "/home/m/c/6fff54b"}}
        self.logs = []
        self.cells_written = 0

    def log(self, text):
        self.logs.append(text)

    def write_cells(self, plan, attempt=1):
        self.cells_written += 1


def env_with(io, cells=(), ctx=None):
    ctx = ctx or Ctx()
    ctx.run = RS()
    return gate.RunEnv(ctx, Plan([PC(c) for c in cells]), io=io), ctx


class Runner(unittest.TestCase):
    def test_env_reads_runstate(self):
        env, _ = env_with(FakeIO())
        self.assertEqual(env.run, "t2g-20261108T0500-4f2a")
        self.assertEqual(env.copy_path("chopin"), "/home/m/c/6fff54b")
        self.assertEqual(env.out_dir("loop:linux", 2), "/bkoscratch/t2core-p3-test/runs/t2g-20261108T0500-4f2a-r2/gate/loop-linux")
        self.assertEqual(env.ram_dir("fork-root:restic", 1), "/mnt/bko-ram/t2g-20261108T0500-4f2a-fork-root-restic")
        self.assertEqual(env.host_for("gate:holt"), "chopin")
        self.assertEqual(env.host_for("gate:fork:rustic"), "cinqcent")
        self.assertEqual(env.host_for("gate:loop:sparse@cinqcent"), "cinqcent")
        self.assertEqual(env.host_for("gate:selftest"), "mac")
        self.assertEqual(env.set_dir, "~/backotter-work/bko-bin")

    def test_host_job_waits_and_times(self):
        io = FakeIO(exit_after=3)
        env, _ = env_with(io)
        rc, out, t, why = gate.host_job(env, "chopin", "holt", 1, "bash testvm/test2/gate/holt-test.sh", (2, 4),
                                        "gate:t2g-20261108T0500-4f2a:holt")
        self.assertEqual((rc, why), (0, None))
        self.assertEqual(gate.job_times(t), (100, 10))
        start = [c for c in io.calls if c[0] == "start"][0][2]
        self.assertIn("WEAR_TAG=gate:t2g-20261108T0500-4f2a:holt ~/backotter-work/cmp-run.sh --reserve 2 4", start)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", start)
        self.assertNotIn("export WEAR_TAG", start)                        # per command, never exported
        # cmp-run.sh runs its command in ~/backotter-work: the job goes back to the copy first
        self.assertIn("bash -c 'cd /home/m/c/6fff54b || exit 2; date +%s >", start.replace("'\"'\"'", "'"))
        dead = FakeIO(never_exit=True)
        env, _ = env_with(dead)
        rc, _, _, why = gate.host_job(env, "chopin", "holt", 1, "true")
        self.assertIsNone(rc)
        self.assertIn("ended without an exit status", why)
        self.assertTrue(dead.needles[0].endswith("/gate/holt/"))      # alive() checks the PID is still ours

    def outcomes(self, *verdicts):
        seq = iter(verdicts)

        def fn(a):
            v = next(seq)
            if v == "HARNESS":
                return gate.harness("ssh dropped")
            return gate.Outcome({"x": "PASS" if v == "PASS" else "FAIL"}, [] if v == "PASS" else ["x: FAIL"])
        return fn

    def test_rerun_once_flaky_and_harness(self):
        io = FakeIO()
        env, _ = env_with(io, ["gate:bin:abi"])
        r = gate.run_one(env, "gate:bin:abi", self.outcomes("FAIL", "PASS"))
        self.assertEqual(r["verdict"], "FLAKY-FAIL")
        self.assertEqual(len(r["rows"]), 2)
        a1, a2 = io.rows
        self.assertEqual((a1["attempt"], a1["verdict"], a1["rule"]), (1, "FAIL", "changed:harness"))
        self.assertEqual((a2["attempt"], a2["verdict"], a2["rule"]), (2, "FLAKY-FAIL", "rerun"))
        self.assertEqual(a1["origin"], "gate:t2g-20261108T0500-4f2a:gate:bin:abi:1")
        self.assertEqual(a2["origin"], "gate:t2g-20261108T0500-4f2a:gate:bin:abi:2")
        self.assertEqual(a2["extra"]["rerun_of"], r["rows"][0])
        self.assertEqual(a2["class"], "fail")
        self.assertTrue(a1["valid"])                                      # measured key == planned
        io2 = FakeIO()
        env2, _ = env_with(io2, ["gate:bin:abi"])
        self.assertEqual(gate.run_one(env2, "gate:bin:abi", self.outcomes("HARNESS", "PASS"))["verdict"], "PASS")
        self.assertEqual([x["verdict"] for x in io2.rows], ["HARNESS", "PASS"])
        io3 = FakeIO()
        env3, _ = env_with(io3, ["gate:bin:abi"])
        self.assertEqual(gate.run_one(env3, "gate:bin:abi", self.outcomes("FAIL", "FAIL"))["verdict"], "FAIL")
        io4 = FakeIO()
        env4, _ = env_with(io4, ["gate:bin:abi"])
        self.assertEqual(gate.run_one(env4, "gate:bin:abi", self.outcomes("PASS"))["verdict"], "PASS")
        self.assertEqual(len(io4.rows), 1)

    def test_row_invalid_when_keys_differ_or_unmeasured(self):
        io = FakeIO(key="z" * 64)
        env, _ = env_with(io, ["gate:bin:abi"])
        entry = gate.run_one(env, "gate:bin:abi", self.outcomes("PASS"))
        row = io.rows[0]
        self.assertFalse(row["valid"])
        self.assertIn("differ from the plan's", row["note"])
        # the entry says so too, so a child's report doesn't print a bare "ran ... PASS"
        self.assertFalse(entry["valid"])
        self.assertTrue(entry["reasons"][0].startswith("row invalid: its inputs as run differ"), entry["reasons"])
        ok_io = FakeIO()
        ok_env, _ = env_with(ok_io, ["gate:bin:abi"])
        ok = gate.run_one(ok_env, "gate:bin:abi", self.outcomes("PASS"))
        self.assertTrue(ok["valid"])
        self.assertFalse([x for x in ok["reasons"] if x.startswith("row invalid")])
        self.assertEqual(row["planned_key"], "k" * 64)
        self.assertEqual(row["gm"], 0.0)
        self.assertEqual(row["counts"], {"PASS": 1, "FAIL": 0, "LIMIT": 0})

    def test_fast_in_order_slow_at_once_and_stops(self):
        order, live, peak = [], [0], [0]
        lock = threading.Lock()

        def fake_cell(env, cell, commits=None):
            with lock:
                order.append(cell)
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.05)
            with lock:
                live[0] -= 1
            v = "FAIL" if cell in ("gate:bin:static", "gate:loop:sparse@cinqcent") else "PASS"
            return {"cell": cell, "verdict": v, "rows": [cell + "-row"], "host": "chopin"}

        def fake_forks(env, cells, commits):
            order.extend(cells)
            return {c: {"cell": c, "verdict": "PASS", "rows": [], "host": "cinqcent"} for c in cells}

        saved = (gate.run_cell, gate.run_fork_cells, gate.fork_commits)
        gate.run_cell, gate.run_fork_cells = fake_cell, fake_forks
        gate.fork_commits = lambda env, names: {n: "1" * 40 for n in names}
        try:
            env, ctx = env_with(FakeIO())
            r = gate.run_layers(ctx, ["selftest", "bin:version", "bin:static", "fork:restic", "holt",
                                      "fork-root:restic", "fork:rustic"], "fast", env=env)
            self.assertEqual(order, ["gate:holt", "gate:fork:restic", "gate:fork:rustic", "gate:fork-root:restic",
                                     "gate:bin:static", "gate:bin:version", "gate:selftest"])
            self.assertEqual(peak[0], 1)                                  # one after another
            self.assertEqual(r["stop"], "stop-family")
            self.assertEqual(r["stops"], ["stop-family: fast gate gate:bin:static FAIL"])
            self.assertFalse(r["ok"])
            self.assertIn("gate:bin:static-row", r["rows"])
            order.clear()
            peak[0] = 0
            s = gate.run_layers(ctx, ["loop:linux", "loop:sparse"], "slow", env=env)
            self.assertEqual(sorted(order), ["gate:loop:linux@chopin", "gate:loop:linux@cinqcent",
                                             "gate:loop:sparse@chopin", "gate:loop:sparse@cinqcent"])
            self.assertGreater(peak[0], 1)                                # all at once
            self.assertEqual(s["stop"], "stop-starts")
            self.assertEqual(s["exit"], 1)
            ok = gate.run_layers(ctx, ["loop:linux"], "slow", env=env)
            self.assertEqual((ok["stop"], ok["ok"], ok["exit"]), (None, True, 0))
        finally:
            gate.run_cell, gate.run_fork_cells, gate.fork_commits = saved

    def test_run_the_word(self):
        seen = []

        def fake_layers(ctx, names, part=None, plan=None, env=None, io=None, log=None):
            seen.append((part, list(names)))
            return {"cells": [{"cell": c, "verdict": "PASS", "rows": ["r"], "host": "chopin", "note": None}
                              for c in names], "stops": [], "stop": None, "ok": True, "exit": 0}

        saved = gate.run_layers
        gate.run_layers = fake_layers
        try:
            ctx = Ctx()
            ctx.run = RS()
            plan = Plan([PC("gate:loop:linux@chopin"), PC("gate:holt"), PC("gate:bin:abi", status="current"),
                         PC("gate:selftest")])
            r = gate.run(ctx, plan, io=FakeIO())
            self.assertEqual(seen, [("fast", ["gate:holt", "gate:selftest"]), ("slow", ["gate:loop:linux@chopin"])])
            self.assertEqual(r["exit"], 0)
            self.assertEqual(r["family"], "gate")
            self.assertEqual(r["run"], "t2g-20261108T0500-4f2a")
            self.assertEqual(ctx.run.cells_written, 1)                    # R1's step 6
            self.assertEqual(r["copy"], "6fff54b")
        finally:
            gate.run_layers = saved

    def test_layer_lock_makes_a_second_run_wait(self):
        env, _ = env_with(FakeIO())
        got = []
        saved, gate.LOCK_POLL_S = gate.LOCK_POLL_S, 0.05
        self.addCleanup(setattr, gate, "LOCK_POLL_S", saved)
        with gate.LayerLock(env, "gate:bin:abi"):
            t = threading.Thread(target=lambda: got.append(gate.locked(env, "gate:bin:abi", lambda: "ran")))
            t.start()
            time.sleep(0.3)
            self.assertEqual(got, [])                                     # waits while held
        t.join(15)
        self.assertEqual(got, ["ran"])

    def test_bin_layer_end_to_end_with_a_fake_host(self):
        out = fx("bincheck", "version-bko-bin.out")

        class IO(FakeIO):
            def fetch(self, host, d, names):
                return {"bincheck.out": out, ".exit": "0\n"}

            def local(self, argv, cwd=None, env=None, timeout=None):
                if argv[:2] == ["git", "-C"] and argv[3] == "show":
                    rev, path = argv[4].split(":", 1)
                    if path.endswith("global.go"):
                        return 0, 'const Version = "0.19.1+backotter.5"\n'
                    if path == "Cargo.toml":
                        return 0, '[package]\nversion = "%s"\n' % ("0.11.4+backotter.7" if rev.startswith("6ccec58")
                                                                   else "0.11.4+backotter.6")
                    return 0, '[package]\nversion = "%s"\n' % ("0.13.0+backotter.6" if rev.startswith("96596bb")
                                                               else "0.13.0+backotter.5")
                return 1, ""

        io = IO()
        ctx = Ctx()
        f = ctx.facts["chopin"]["sets"]["bko-bin"]
        f["commits"] = bko_bin_commits()
        f["sums"]["haiku/rustic-bko"] = "12181a04a6f5143c5265c9ec38ca7af9f6d3ab560882326cf278aebb21386e65"
        env, _ = env_with(io, ["gate:bin:version"], ctx)
        o = gate.run_bin(env, "version", "chopin", 1)
        self.assertEqual(o.verdict, "PASS")
        self.assertEqual(o.needs["agree"], "LIMIT")
        expect = [c for c in io.calls if c[0] == "sh" and "expect.tsv" in c[2] and c[2].startswith("cat >")]
        self.assertTrue(expect)
        cmd = [c for c in io.calls if c[0] == "sh" and "bincheck.sh version" in c[2]][0][2]
        self.assertIn('--set "$HOME"/backotter-work/bko-bin', cmd)
        self.assertIn("--expect", cmd)
        exp = gate.expected_versions(env, f["commits"])
        self.assertEqual(exp["haiku/rustic-bko"], ("0.11.4+backotter.7", "0.13.0+backotter.6"))
        self.assertEqual(exp["linux/restic-bko"], ("0.19.1+backotter.5", ""))


class RunnerFailures(unittest.TestCase):
    """The review's failure paths (P3 fix round): a fetch or an ssh that fails, a job that
    has to be given up, fork-tests.sh that didn't finish, a ~/ HOST_ROOT, cut-down loops."""

    def test_a_fetch_that_fails_after_the_exit_is_no_pass(self):
        class IO(FakeIO):
            def fetch(self, host, d, names):
                return {}                                                 # HostIO.fetch after an ssh drop
        io = IO()
        env, _ = env_with(io, ["gate:holt"])
        rc, _, t, why = gate.host_job(env, "chopin", "holt", 1, "true")
        self.assertEqual((rc, why, t), (0, None, {"exit": 0}))           # the polled exit stands
        r = gate.run_one(env, "gate:holt", lambda a: gate.run_holt(env, "chopin", a))
        self.assertEqual([x["verdict"] for x in io.rows], ["HARNESS", "HARNESS"])
        self.assertNotEqual(r["verdict"], "PASS")
        self.assertTrue(all(x["needs"] == {} and x["fails"] for x in io.rows))

    def test_a_job_waiting_in_line_is_an_answer(self):
        """A job still in cmp-run.sh's line has no .began: the poll exits 0 (not 'no answer'),
        and with .exit there it gives the status (real sh, as on the host)."""
        import subprocess
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        cmd = gate.job_poll_cmd(d)
        r = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, "\n"))
        with open(os.path.join(d, ".exit"), "w", encoding="utf-8") as f:
            f.write("3\n")
        with open(os.path.join(d, ".began"), "w", encoding="utf-8") as f:
            f.write("1000\n")
        r = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, "3\n\n1000\n"))
        r = subprocess.run(["/bin/sh", "-c", gate.job_poll_cmd(d + "/gone")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)

    def test_an_ssh_that_fails_is_no_answer(self):
        class IO(FakeIO):
            def __init__(self):
                super().__init__(exit_after=4)
                self.answers = [None, None, True]

            def alive(self, host, pid, needle=None):
                return self.answers.pop(0) if self.answers else True
        io = IO()
        env, _ = env_with(io)
        rc, _, _, why = gate.host_job(env, "chopin", "holt", 1, "true")
        self.assertEqual((rc, why), (0, None))                            # never "ended without an exit status"

    def give_up(self, gone):
        class IO(FakeIO):
            def __init__(self):
                super().__init__(never_exit=True)
                self.stopped = []

            def alive(self, host, pid, needle=None):
                self.needles.append(needle)
                return True                                               # still queued in cmp-run.sh

            def stop(self, host, pid, needle, wait_s=300):
                self.stopped.append((host, pid, needle))
                return gone
        io = IO()
        env, _ = env_with(io, ["gate:holt"])
        starts = []

        def fn(a):
            starts.append(a)
            rc, out, t, why = gate.host_job(env, "chopin", "holt", a, "true", (2, 4), wait_s=-1)
            return gate.harness(why)
        r = gate.run_one(env, "gate:holt", fn)
        return io, r, starts

    def test_a_job_given_up_is_stopped_and_not_run_twice_beside_itself(self):
        io, r, starts = self.give_up(gone=False)
        self.assertEqual(starts, [1])                                     # no attempt 2 beside it
        self.assertEqual(len(io.stopped), 1)
        self.assertEqual(io.stopped[0][:2], ("chopin", 4242))
        self.assertIn("no room for it on chopin", r["fails"][0])
        self.assertIn("may still run", r["fails"][0])
        io, r, starts = self.give_up(gone=True)
        self.assertEqual(starts, [1, 2])                                  # stopped: the rerun may start
        self.assertEqual(len(io.stopped), 2)

    def test_a_host_root_under_home(self):
        env, ctx = env_with(FakeIO())
        ctx.run.host_root = "~/backotter-work/t2core-p3/state"
        io = FakeIO()
        env = gate.RunEnv(ctx, Plan([]), io=io)
        gate.host_job(env, "chopin", "loop:linux", 1, "true")
        start = [c for c in io.calls if c[0] == "start"][0][2]
        self.assertTrue(io.needles == [] or all(n in start for n in io.needles))
        needle = "/runs/t2g-20261108T0500-4f2a/gate/loop-linux/"
        self.assertIn(needle, start)                                      # as written: "$HOME"/'backotter-work/...'
        home = os.path.join(_tmp, "home")
        d = os.path.join(home, "x")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, ".exit"), "w") as f:
            f.write("0\n")
        import contextlib
        import io as _io
        buf, saved = _io.StringIO(), os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            with contextlib.redirect_stdout(buf):
                exec(gate.FETCH_SCRIPT, {"_T2_DATA": json.dumps({"dir": "~/x", "names": [".exit", "none"]})})
        finally:
            os.environ["HOME"] = saved
        self.assertEqual(json.loads(buf.getvalue()), {".exit": "0\n"})

    def fork_env(self, rc, info_date=None, write=True, ans=None):
        local_calls, sh_calls = [], []

        class IO(FakeIO):
            def local(self, argv, cwd=None, env=None, timeout=None):
                if argv and argv[0] == "bash" and argv[1].endswith("fork-tests.sh"):
                    local_calls.append(argv)
                    d = os.path.join(env["BKO_AUDIT_LOCAL"], "fork-tests")
                    if write:
                        os.makedirs(os.path.join(d, "rustic-x"), exist_ok=True)
                        with open(os.path.join(d, "rustic.report"), "w") as f:
                            f.write(fx("fork", "rustic.report"))
                        if info_date:
                            with open(os.path.join(d, "rustic-x", "info.txt"), "w") as f:
                                f.write(f"name=rustic\ndate={info_date}\ncommit={'3' * 40}\n")
                    return rc, "cinqcent t2g-20261108T0500-4f2a-cinqcent: failed; see its log" if rc == 2 else "done"
                return 0, ""

            def sh(self, host, cmd, timeout=60, input=None):
                sh_calls.append(cmd)
                return 0, (("over" if rc == 2 else "runs") if ans is None else ans) if "echo over" in cmd else "", ""
        env, _ = env_with(IO(), ["gate:fork:rustic"])
        out = gate.run_forks(env, ["rustic"], 1, {"rustic": "3" * 40, "rustic_core": "2" * 40})
        return out["rustic"], local_calls, sh_calls

    def test_wait_tag_takes_no_answer_for_no_answer(self):
        """ssh exit 255 isn't 'the job ended': wait_tag asks again until the host answers."""
        d = tempfile.mkdtemp(prefix="t2-wait-tag-")
        self.addCleanup(shutil.rmtree, d, True)
        ssh = os.path.join(d, "ssh")
        with open(ssh, "w") as f:
            f.write(f'#!/bin/bash\nn=$(cat {d}/n 2>/dev/null); n=$((n + 1)); echo $n > {d}/n\n'
                    f'(( n <= 3 )) && exit 255\necho 7\n')
        os.chmod(ssh, 0o755)
        lib = os.path.join(REPO, "testvm", "audit", "audit-lib.sh")
        script = f'sleep() {{ :; }}; log() {{ :; }}; RWORK=w; SSH=({ssh}); ' \
                 f'eval "$(sed -n "/^wait_tag()/,/^}}/p" {lib})"; wait_tag h t; echo "rc=$?"'
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.strip(), "rc=7", r.stderr)

    def test_cleanup_is_not_cut_by_the_stops_second_signal(self):
        for f in ("fork-root.sh", "holt-test.sh", "loop.sh"):
            text = read_file(os.path.join(REPO, "testvm", "test2", "gate", f))
            body = text[text.index("cleanup() {\n") + len("cleanup() {\n"):]
            self.assertTrue(body.lstrip().startswith("trap '' INT TERM HUP"), f)
            # The handler too: a signal between its exit and cleanup's first line ends the shell.
            self.assertIn("""trap 'trap "" INT TERM HUP; exit 130' INT TERM HUP""", text, f)

    def test_run_all_and_a_stopped_loop_clean_up_too(self):
        text = read_file(os.path.join(REPO, "testvm", "compare", "run-all.sh"))
        self.assertIn("""trap 'trap "" INT TERM HUP; echo "run-all: interrupted\"""", text)
        text = read_file(os.path.join(REPO, "testvm", "test2", "gate", "loop.sh"))
        self.assertIn('[[ -z ${stopped:-} ]] || sudo -n rm -rf --one-file-system /mnt/bko-ram/"$pfx"-*', text)

    def test_fork_job_that_may_still_run_keeps_its_tools(self):
        o, _, sh = self.fork_env(2, ans="runs")
        self.assertEqual((o.verdict, o.still_running), ("HARNESS", True))
        self.assertFalse(any("rm -rf" in c for c in sh))
        o, _, sh = self.fork_env(2, ans="")          # the host didn't answer
        self.assertTrue(o.still_running)
        self.assertFalse(any("rm -rf" in c for c in sh))
        o, _, sh = self.fork_env(2, ans="over")
        self.assertFalse(o.still_running)
        self.assertTrue(any("bin-t2g-20261108T0500-4f2a" in c for c in sh))

    def test_fork_tests_that_didnt_finish_is_harness_whatever_report_is_there(self):
        o, calls, _ = self.fork_env(2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(o.verdict, "HARNESS")                            # never a PASS from older results
        self.assertIn("fork-tests.sh exit 2 on cinqcent", o.fails[0])
        self.assertFalse(o.still_running)
        ok, _, _ = self.fork_env(1, time.strftime("%Y-%m-%d %H:%M:%S +0000", time.gmtime()))
        self.assertNotEqual(ok.verdict, "HARNESS")                        # this attempt's results: judged
        self.assertEqual(ok.needs["suite"], "LIMIT")
        self.assertRegex(ok.start, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")  # the attempt's own start
        old, _, _ = self.fork_env(1, "2026-11-01 10:00:00 +0100")
        self.assertEqual(old.verdict, "HARNESS")
        self.assertIn("before this attempt", old.fails[0])
        late, _, sh = self.fork_env(124)
        self.assertTrue(late.still_running)                               # its host jobs may still run
        self.assertFalse(any("bin-t2g" in c for c in sh))                 # their tools are left to them
        _, _, sh = self.fork_env(1, time.strftime("%Y-%m-%d %H:%M:%S +0000", time.gmtime()))
        self.assertTrue(any("bin-t2g-20261108T0500-4f2a" in c for c in sh))

    def test_a_row_takes_its_attempts_own_start(self):
        io = FakeIO()
        env, _ = env_with(io, ["gate:fork:restic"])

        def fn(a):
            o = gate.Outcome({"suite": "FAIL" if a == 1 else "PASS"}, ["x"] if a == 1 else [])
            o.start = f"2026-11-08T0{a + 2}:16:40Z"
            return o
        gate.run_one(env, "gate:fork:restic", fn)
        self.assertEqual([x["start"] for x in io.rows], ["2026-11-08T03:16:40Z", "2026-11-08T04:16:40Z"])

    def test_a_cut_down_loop_run_is_no_valid_row(self):
        summary = fx("loop", "summary-linux.json")

        class IO(FakeIO):
            def fetch(self, host, d, names):
                if "log/summary.json" in names:
                    return {"log/summary.json": summary, "info.txt": "linux_fs=xfs\nsparse_fs=default\n"}
                return super().fetch(host, d, names)
        io = IO()
        env, _ = env_with(io, ["gate:loop:linux@chopin"])
        os.environ["T2_LOOP_LINUX_FS"] = "xfs"
        try:
            gate.run_one(env, "gate:loop:linux@chopin", lambda a: gate.run_loop(env, "linux", "chopin", a))
        finally:
            os.environ.pop("T2_LOOP_LINUX_FS", None)
        row = io.rows[0]
        self.assertFalse(row["valid"])
        self.assertIn("a cut-down run (T2_LOOP_LINUX_FS=xfs)", row["note"])
        self.assertEqual(row["meta"]["loop"]["linux_fs"], "xfs")
        self.assertFalse(gate.full_run(row))
        start = [c for c in io.calls if c[0] == "start"][0][2]
        self.assertIn("T2_LOOP_LINUX_FS=xfs", start)
        self.assertIn("--prefix bkog05004f2al ", start)
        self.assertEqual(gate.loop_prefix("t2g-20261108T0500-4f2a", "sparse", 2), "bkog05004f2as2")

    def test_an_unwritten_row_is_lost(self):
        class IO(FakeIO):
            def script(self, host, script, data, timeout=120):
                if data.get("append"):
                    raise RuntimeError("chopin: script exit 255: ssh: connect timed out")
                return super().script(host, script, data, timeout)
        env, ctx = env_with(IO(), ["gate:bin:abi"])
        r = gate.run_one(env, "gate:bin:abi", lambda a: gate.Outcome({"x": "PASS"}))
        self.assertEqual((r["verdict"], r["class"], r["unwritten"]), ("PASS", "lost", 1))
        self.assertTrue(r["rows"][0].startswith("unwritten"))

    def test_hostio_alive_and_stop(self):
        class H(gate.HostIO):
            def __init__(self, answers):
                self.answers, self.cmds = list(answers), []

            def sh(self, host, cmd, timeout=60, input=None):
                self.cmds.append(cmd)
                return self.answers.pop(0)

            def sleep(self, s):
                pass
        self.assertIs(H([(0, "alive\n", "")]).alive("chopin", 42, "/gate/x/"), True)
        self.assertIs(H([(0, "gone\n", "")]).alive("chopin", 42, "/gate/x/"), False)
        self.assertIsNone(H([(255, "", "ssh: connect to host chopin.local port 22: timed out")]).alive("chopin", 42, "n"))
        h = H([(0, "alive\n", ""), (0, "sent\n", ""), (0, "bash: kill: (-42) - Operation not permitted\nrc=1\n", ""),
               (0, "bash: kill: (-42) - No such process\nrc=1\n", "")])
        self.assertTrue(h.stop("chopin", 42, "/gate/x/"))
        self.assertIn("kill -TERM -- -42", h.cmds[1])
        self.assertIn("ps -o pgid= -p 42", h.cmds[1])                     # only while 42 leads its group
        self.assertFalse(H([(255, "", "")]).stop("chopin", 42, "/gate/x/"))   # no answer: nothing sent
        self.assertFalse(H([]).stop("chopin", 1, "x"))


class HostScript(unittest.TestCase):
    """HOST_SCRIPT measured and appended through this tree's own t2lib, run locally
    (hostrun's host "local"): a gate row as a host writes it, valid against the plan."""

    def test_measure_and_append_locally(self):
        from t2lib import hostrun, ledger
        work = os.path.join(_tmp, "work")
        os.makedirs(os.path.join(work, "bko-bin", "linux"), exist_ok=True)
        for n in ("restic-bko", "rustic-bko"):
            with open(os.path.join(work, "bko-bin", "linux", n), "wb") as f:
                f.write(n.encode())
        with open(os.path.join(work, "bko-bin", "SHA256SUMS"), "w") as f:
            for n in ("restic-bko", "rustic-bko"):
                f.write(f"{keys.sha256_hex(n)} *linux/{n}\n")
        led = os.path.join(_tmp, "hostledger.jsonl")
        env_vars = {"T2_HOST_ROOT": os.path.join(_tmp, "host"), "T2_COPY_ROOT": os.path.join(_tmp, "copy"),
                    "T2_LEDGER": led, "T2_WORK": work, "PYTHONDONTWRITEBYTECODE": "1"}
        cell = "gate:bin:static"
        data = {"test2": os.path.join(REPO, "testvm", "test2"), "measure": [cell], "opts": {"set": "bko-bin"},
                "want": {"sets": ["bko-bin"], "plumbing": [], "host_inputs": []}, "env": env_vars}
        r = hostrun.ssh("local", script=gate.HOST_SCRIPT, data=data, timeout=120)
        self.assertEqual(r.rc, 0, r.errtext[-800:])
        m = json.loads(r.text)["measure"][cell]
        self.assertNotIn("error", m)
        self.assertEqual(m["inputs"]["bin"]["linux/restic-bko"], "sha256:" + keys.sha256_hex("restic-bko"))
        o = gate.Outcome({"linux/restic-bko": "PASS", "linux/rustic-bko": "PASS"})
        o.logs = "local:/tmp/x"

        class E:
            run, word, family, set_name = "t2g-20261108T0500-4f2a", "GateTest2 bin", "gate", "bko-bin"

        row = gate.make_row(E, cell, 1, o, m, "2026-11-08T05:00:00Z", "new", ["no PASS ever"], m["key"])
        data = {"test2": os.path.join(REPO, "testvm", "test2"), "append": [row], "env": env_vars}
        a = hostrun.ssh("local", script=gate.HOST_SCRIPT, data=data, timeout=120)
        self.assertEqual(a.rc, 0, a.errtext[-800:])
        rid, appended = json.loads(a.text)["ids"][0]
        self.assertNotEqual(rid, "error", appended)
        self.assertTrue(appended)
        rows = ledger.read(led).rows
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cell"], cell)
        self.assertTrue(rows[0]["valid"])
        self.assertEqual(rows[0]["key"], m["key"])
        self.assertEqual(ledger.validate(rows[0]), [])
        again = hostrun.ssh("local", script=gate.HOST_SCRIPT, data=data, timeout=120)   # origin: no second row
        self.assertEqual(json.loads(again.text)["ids"][0], [rid, False])


if __name__ == "__main__":
    unittest.main()
