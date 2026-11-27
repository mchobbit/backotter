"""The audit and compare scripts' hosts (docs/design/img-speed.md F10): rust-lint.sh,
stock-baseline.sh, rustic-exact-round.sh and compress-images.sh took Chopin (and Cinqcent)
by name.  Now testvm/audit/audit-hosts.py reads each host's probe (rustup's toolchains,
targets and components, a Go, the tools) and deals rust-lint.sh's targets over the hosts
that have what each needs, the primary first; stock-baseline.sh asks it which host can run
each stock job; the thermal wait and xz's threads are the host table's hot_max and
xz_threads; Chopin's address is its row's ip.  No host needed: the probes are fixtures."""

import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
TESTVM = os.path.dirname(T2)
REPO = os.path.dirname(TESTVM)
sys.path.insert(0, T2)

from t2lib import hosts as H  # noqa: E402

AUDIT = os.path.join(TESTVM, "audit")
AH_PY = os.path.join(AUDIT, "audit-hosts.py")


def _load():
    import importlib.util
    spec = importlib.util.spec_from_file_location("audit_hosts", AH_PY)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


A = _load()
STD = ["x86_64-unknown-linux-gnu", "x86_64-unknown-linux-musl", "aarch64-unknown-linux-musl",
       "x86_64-unknown-freebsd", "x86_64-unknown-netbsd", "x86_64-unknown-illumos"]
NIGHTLY_T = ["x86_64-unknown-openbsd", "x86_64-unknown-dragonfly", "x86_64-unknown-haiku"]
ALL = STD + NIGHTLY_T


def probe(tc_targets=STD, tc_comps=("clippy", "rustfmt"), floor=True, nightly=("clippy", "rust-src"),
          go=True, tools=("flock", "time", "cc"), machine="x86_64", end=True):
    """A probe's output: TOOLCHAIN with TC_TARGETS and TC_COMPS (None: no toolchain), the
    FLOOR toolchain, NIGHTLY's components (None: no nightly)."""
    lines = []
    if tc_targets is not None:
        lines.append("tc 1.94.1")
        lines += [f"target 1.94.1 {t}" for t in tc_targets]
        lines += [f"comp 1.94.1 {c}" for c in tc_comps]
    if floor:
        lines += ["tc 1.85.1", "target 1.85.1 x86_64-unknown-linux-gnu"]
    if nightly is not None:
        lines += ["tc nightly-2026-10-31", "target nightly-2026-10-31 x86_64-unknown-linux-gnu"]
        lines += [f"comp nightly-2026-10-31 {c}" for c in nightly]
    if go:
        lines.append("go /home/m/sdk/go1.26.10/bin/go")
    lines += [f"tool {t}" for t in tools]
    lines.append(f"machine {machine}")
    if end:
        lines.append("end")
    return "\n".join(lines) + "\n"


# The farm as probed 2026-11-27 (before copland got clippy, rustfmt and the BSD stds):
# chopin has everything, cinqcent no rustfmt and no nightly clippy, the small hosts no
# clippy at all.
FULL = probe()
CINQCENT = probe(tc_comps=("clippy",), nightly=("rust-src",))
SMALL = probe(tc_targets=STD[:3], tc_comps=())


def plan(probes, hosts, targets=ALL, **kw):
    err = io.StringIO()
    with redirect_stderr(err):
        p = A.lint_plan({h: A.parse(t) for h, t in probes.items()}, hosts, targets, **kw)
    return p, err.getvalue()


class Parse(unittest.TestCase):
    def test_probe_lines(self):
        p = A.parse(CINQCENT)
        self.assertTrue(p["complete"])
        self.assertEqual(p["tcs"]["1.94.1"]["comps"], {"clippy"})
        self.assertIn("x86_64-unknown-netbsd", p["tcs"]["1.94.1"]["targets"])
        self.assertEqual(p["tcs"]["nightly-2026-10-31"]["comps"], {"rust-src"})
        self.assertEqual(p["tools"], {"flock", "time", "cc"})
        self.assertEqual(p["go"], "/home/m/sdk/go1.26.10/bin/go")

    def test_cut_short_is_no_answer(self):
        self.assertFalse(A.parse(probe(end=False))["complete"])
        self.assertEqual(A.base_needs(A.parse(""))[0][0], "no answer to the probe (ssh failed or cut short)")

    def test_probe_shell_reads_only(self):
        sh = A.PROBE_SH
        self.assertIn("rustup toolchain list", sh)
        self.assertNotRegex(sh, r"toolchain install|component add|target add|\brm\b|mkdir|>\s*(?!/dev/null|&)\S",
                            "the probe changes nothing on the host")
        self.assertTrue(sh.rstrip().endswith("echo end"))


class LintPlan(unittest.TestCase):
    def test_farm_of_the_day(self):
        """chopin is the primary; cinqcent takes the std targets it has (not the
        nightly ones: no clippy there); the small hosts are left out, each with the
        rustup command that would add clippy."""
        p, err = plan({"chopin": FULL, "cinqcent": CINQCENT, "copland": SMALL, "cage": SMALL},
                      ["chopin", "cinqcent", "copland", "cage"])
        self.assertEqual([(h, r) for h, r, _ in p], [("chopin", "primary"), ("cinqcent", "clippy")])
        dealt = {h: ts for h, _, ts in p}
        self.assertEqual(sorted(dealt["chopin"] + dealt["cinqcent"]), sorted(ALL), "every target once")
        self.assertEqual(dealt["chopin"][0], "x86_64-unknown-linux-gnu")
        self.assertFalse(set(NIGHTLY_T) & set(dealt["cinqcent"]))
        self.assertIn("copland: not used: no clippy in 1.94.1 (there: rustup +1.94.1 component add clippy)", err)

    def test_the_best_host_that_can_be_primary(self):
        """pick-host's order goes first, but a host without rustfmt can't be the primary:
        cinqcent ranked first stays a clippy host."""
        p, err = plan({"chopin": FULL, "cinqcent": CINQCENT}, ["cinqcent", "chopin"])
        self.assertEqual(p[0][:2], ("chopin", "primary"))
        self.assertEqual(p[1][:2], ("cinqcent", "clippy"))
        self.assertIn("cinqcent: not the primary", err)
        self.assertIn("rustup +1.94.1 component add rustfmt", err)

    def test_a_small_host_alone(self):
        """A small host with the toolchain's parts is a primary like Chopin (the proof's
        run: rust-lint.sh --host copland)."""
        p, _ = plan({"copland": FULL}, ["copland"])
        self.assertEqual(p, [("copland", "primary", ALL)])

    def test_spread_1_is_one_host(self):
        p, _ = plan({"chopin": FULL, "cinqcent": FULL, "copland": FULL}, ["chopin", "cinqcent", "copland"], spread=1)
        self.assertEqual(p, [("chopin", "primary", ALL)])

    def test_balanced_deal(self):
        """Three full hosts: the primary starts with its once-only steps' load, so it gets
        the fewest targets; every target goes to exactly one host, in order."""
        p, _ = plan({h: FULL for h in "abc"}, ["a", "b", "c"])
        n = {h: len(ts) for h, _, ts in p}
        self.assertEqual(sum(n.values()), len(ALL))
        self.assertEqual(n, {"a": 1, "b": 4, "c": 4})
        two, _ = plan({h: FULL for h in "ab"}, ["a", "b"])
        self.assertEqual([len(ts) for _, _, ts in two], [3, 6])
        two, _ = plan({h: FULL for h in "ab"}, ["a", "b"], tests=True)
        self.assertEqual([len(ts) for _, _, ts in two], [2, 7], "--tests weigh on the primary")

    def test_spread_caps_the_hosts(self):
        p, _ = plan({h: FULL for h in "abcd"}, ["a", "b", "c", "d"], spread=2)
        self.assertEqual([h for h, _, _ in p], ["a", "b"])

    def test_no_primary(self):
        with self.assertRaisesRegex(ValueError, "no host can be the primary"):
            plan({"cinqcent": CINQCENT, "copland": SMALL}, ["cinqcent", "copland"])
        with self.assertRaisesRegex(ValueError, "no host can be the primary"):
            plan({}, [])

    def test_a_target_no_host_has(self):
        """The primary lacks the nightly's clippy and nobody else has it: refused, not
        silently left out (rust-lint.sh then dies before starting anything)."""
        with self.assertRaisesRegex(ValueError, "no host for target x86_64-unknown-openbsd"):
            plan({"chopin": probe(nightly=("rust-src",)), "cinqcent": CINQCENT}, ["chopin", "cinqcent"])

    def test_missing_std_goes_elsewhere(self):
        """A host without netbsd's std isn't dealt netbsd."""
        p, err = plan({"a": FULL, "b": probe(tc_targets=[t for t in STD if "netbsd" not in t])}, ["a", "b"],
                      targets=["x86_64-unknown-linux-gnu", "x86_64-unknown-netbsd", "x86_64-unknown-freebsd"])
        dealt = {h: ts for h, _, ts in p}
        self.assertIn("x86_64-unknown-netbsd", dealt["a"])
        self.assertEqual(dealt["b"], ["x86_64-unknown-freebsd"])

    def test_no_tools_no_host(self):
        for p in (probe(tools=("flock", "cc")), probe(machine="aarch64"), probe(tc_targets=None)):
            with self.assertRaises(ValueError):
                plan({"x": p}, ["x"])

    def test_only_once_steps(self):
        """--targets without linux: the primary still runs the once-only steps (its
        TARGETS "-" on the command line)."""
        p, _ = plan({"a": FULL}, ["a"], targets=["x86_64-unknown-freebsd"])
        self.assertEqual(p, [("a", "primary", ["x86_64-unknown-freebsd"])])
        p, _ = plan({"a": FULL, "b": FULL}, ["a", "b"], targets=["x86_64-unknown-freebsd"])
        self.assertEqual(p, [("a", "primary", []), ("b", "clippy", ["x86_64-unknown-freebsd"])])

    def test_command_line(self):
        d = tempfile.mkdtemp(prefix="t2-audit-hosts-")
        for h, t in (("chopin", FULL), ("cinqcent", CINQCENT), ("copland", SMALL)):
            with open(os.path.join(d, h + ".txt"), "w") as f:
                f.write(t)
        r = subprocess.run([sys.executable, "-I", "-B", AH_PY, "lint-plan", "--probe-dir", d,
                            "--hosts", "chopin,cinqcent,copland,nosuch", "--targets", ",".join(ALL)],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = [ln.split() for ln in r.stdout.splitlines()]
        self.assertEqual([x[:2] for x in rows], [["chopin", "primary"], ["cinqcent", "clippy"]])
        self.assertIn("nosuch: not used: no answer to the probe", r.stderr)
        r = subprocess.run([sys.executable, "-I", "-B", AH_PY, "lint-plan", "--probe-dir", d, "--hosts", "copland"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no host can be the primary", r.stderr)
        r = subprocess.run([sys.executable, "-I", "-B", AH_PY, "lint-plan", "--probe-dir", d, "--hosts", "chopin",
                            "--targets", "x86_64-unknown-freebsd", "--spread", "1"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout, "chopin primary x86_64-unknown-freebsd\n")


class StockNeed(unittest.TestCase):
    def test_go_and_rust(self):
        self.assertEqual(A.stock_need(A.parse(FULL), "restic"), [])
        self.assertEqual(A.stock_need(A.parse(FULL), "rustic"), [])
        self.assertTrue(A.stock_need(A.parse(probe(go=False)), "restic"))
        self.assertEqual(A.stock_need(A.parse(probe(tc_targets=None)), "rustic_core")[0][1],
                         "rustup toolchain install 1.94.1 --profile minimal")
        self.assertTrue(A.stock_need(A.parse(""), "restic"))
        with self.assertRaises(ValueError):
            A.stock_need(A.parse(FULL), "nosuch")

    def test_command_line(self):
        d = tempfile.mkdtemp(prefix="t2-audit-stock-")
        f = os.path.join(d, "cage.txt")
        with open(f, "w") as fh:
            fh.write(probe(go=False))
        r = subprocess.run([sys.executable, "-I", "-B", AH_PY, "stock-need", "--probe", f, "restic"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn("cage: restic: no go", r.stderr)
        r = subprocess.run([sys.executable, "-I", "-B", AH_PY, "stock-need", "--probe", f, "rustic"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)


class HeatFields(unittest.TestCase):
    """hot_max and xz_threads, the per-host fields that replaced the scripts' Chopin
    checks, and Chopin's address in its row."""

    def test_the_real_table(self):
        self.assertEqual(H.limit("chopin", "hot_max"), 95)
        self.assertEqual(H.limit("chopin", "xz_threads"), 2)
        for h in H.names():
            if h != "chopin":
                self.assertIsNone(H.limit(h, "hot_max"), h)
                self.assertEqual(H.limit(h, "xz_threads"), 4, h)
        self.assertEqual(H.ssh_fallback("chopin"), ["-o", "HostKeyAlias=chopin.local", "m@192.168.68.194"])

    def test_bad_values_refused(self):
        for bad in (49, 111, 95.5, "95", True):
            self.assertIsNotNone(H.limit_error("hot_max", bad), bad)
        for good in (None, 50, 95, 110):
            self.assertIsNone(H.limit_error("hot_max", good), good)
        for bad in (0, 17, 2.0, None):
            self.assertIsNotNone(H.limit_error("xz_threads", bad), bad)

    def test_hosts_sh(self):
        r = subprocess.run(["bash", "-c", f'. "{TESTVM}/hosts.sh"; hosts_limit chopin hot_max; hosts_limit cage hot_max;'
                            ' hosts_limit chopin xz_threads; hosts_limit nosuch xz_threads'],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout, "95\n\n2\n4\n", r.stderr)


class NoHostNames(unittest.TestCase):
    """The four scripts' code names no host: they take them from the table (comments
    may still say Chopin)."""
    FILES = ("testvm/audit/rust-lint.sh", "testvm/audit/rust-lint-host.sh", "testvm/audit/stock-baseline.sh",
             "testvm/audit/audit-pick.sh", "testvm/compare/rustic-exact-round.sh", "stick/compress-images.sh")

    def test_no_names_in_code(self):
        names = set(H.names(all_=True))
        pat = re.compile(r"\b(" + "|".join(sorted(names)) + r")\b|192\.168\.")
        for rel in self.FILES:
            with open(os.path.join(REPO, rel)) as f:
                for i, line in enumerate(f, 1):
                    code = line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
                    # A heredoc's text is the job's code too; its comments are skipped the same way.
                    self.assertIsNone(pat.search(code), f"{rel}:{i}: {line.strip()}")

    def test_rust_lint_host_options(self):
        r = subprocess.run(["bash", os.path.join(AUDIT, "rust-lint-host.sh"), "--tests", "--clippy-only", "/nonexistent",
                            "holt:/nonexistent"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn("--tests and --clippy-only don't go together", r.stderr)


if __name__ == "__main__":
    unittest.main()
