"""testvm/pick-host: the probe's reader and the ranking, without any host.

pick-host asks each candidate host, over one ssh, what room it has
(`cmp-run.sh --free`), what it could ever hold (`--ceiling`), its load, its guests and
its HoltTest inputs, then prints the host to use.  The probe and the decision are
separate on purpose: parse_probe() reads one host's answer, rank() takes the parsed
answers as plain dicts and returns the order and every dropped host's reasons, so all of
it is checked here with no ssh at all.  main() is checked with probe_hosts() replaced,
against the real host table.

testvm/holttest1d/ht1-farm.sh, which starts one HoltTest1 queue on every default host, is
checked here too: its own refusals, which all happen before it reaches a host, and that it
names no host itself (it reads the table through testvm/hosts.sh)."""

import importlib.machinery
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TEST2 = os.path.dirname(HERE)
TESTVM = os.path.dirname(TEST2)
PICK = os.path.join(TESTVM, "pick-host")
sys.path.insert(0, TEST2)


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


ph = load("pick_host_under_test", PICK)

# One host's answer, as the probe prints it.
FULL = """host cage
machine x86_64
ceiling_rc 0
ceiling 9.0 11.2
free_rc 0
free 8.9 10.4
waiting 0
nproc 16
load 2.44
memavail_kib 23000684
cap rescuer
cap rescuer-uefi
cap debs
vm debian13
vm alpine324
end
"""


def probe(host="cage", free=(9.0, 9.0), ceiling=(9.0, 11.2), load=1.0, nproc=16, caps=(), vms=(),
          waiting=0, **kw):
    """A parsed probe, as parse_probe() returns one."""
    out = {"ok": True, "host": host, "machine": "x86_64", "nproc": nproc, "load": load,
           "waiting": waiting, "caps": list(caps), "vms": list(vms)}
    if free is not None:
        out["free"] = free
    if ceiling is not None:
        out["ceiling"] = ceiling
    out.update(kw)
    return out


def rows(**per):
    """{host: a row}, each with the roles and arch rank() reads."""
    return {h: dict({"roles": [], "arch": "amd64"}, **r) for h, r in per.items()}


class ReadingTheProbe(unittest.TestCase):
    def test_a_whole_answer(self):
        got = ph.parse_probe(FULL)
        self.assertEqual(got["host"], "cage")
        self.assertEqual((got["free"], got["ceiling"], got["waiting"]), ((8.9, 10.4), (9.0, 11.2), 0))
        self.assertEqual((got["nproc"], got["load"], got["memavail_gib"]), (16, 2.44, 21.9))
        self.assertEqual(got["caps"], ["rescuer", "rescuer-uefi", "debs"])
        self.assertEqual(got["vms"], ["debian13", "alpine324"])
        self.assertEqual((got["ceiling_rc"], got["free_rc"]), (0, 0))
        self.assertTrue(got["ok"])

    def test_what_cmp_run_said_when_it_failed(self):
        """A host with no tmpfs: cmp-run.sh exits 1 before it reads the option, and the
        probe reports the status and the line it died on."""
        got = ph.parse_probe("host cage\nmachine x86_64\nceiling_rc 1\n"
                             "ceiling_err no tmpfs at /mnt/bko-ram\nnproc 16\nend\n")
        self.assertEqual((got["ceiling_rc"], got["ceiling_err"]), (1, "no tmpfs at /mnt/bko-ram"))
        self.assertNotIn("ceiling", got)
        got = ph.parse_probe("free_rc 1\nfree_err cmp-run.sh --free: can't tell what room there is\nend\n")
        self.assertEqual((got["free_rc"], got["free_err"]),
                         (1, "cmp-run.sh --free: can't tell what room there is"))

    def test_an_answer_that_did_not_finish_is_not_ok(self):
        self.assertFalse(ph.parse_probe(FULL.replace("end\n", ""))["ok"])
        self.assertFalse(ph.parse_probe("")["ok"])

    def test_what_is_missing_is_left_out_and_junk_is_ignored(self):
        got = ph.parse_probe("host cage\nceiling 9.0 11.2\nno-cmp-run /home/m/x/cmp-run.sh\n"
                             "free lots\nwaiting many\ncap nosuchcap\nvm\nmumble\nend\n")
        self.assertNotIn("free", got)
        self.assertNotIn("waiting", got)
        self.assertEqual((got["caps"], got["vms"]), ([], []))
        self.assertEqual(got["no_cmp_run"], "/home/m/x/cmp-run.sh")
        self.assertTrue(got["ok"])


class Dropping(unittest.TestCase):
    def test_a_host_that_did_not_answer(self):
        ranked, dropped = ph.rank({"cage": {"ok": False, "error": "timed out after 20s"}})
        self.assertEqual((ranked, dropped), ([], {"cage": ["timed out after 20s"]}))
        self.assertEqual(ph.rank({"cage": {"ok": False}})[1], {"cage": ["no answer"]})

    def test_a_missing_need_a_missing_guest_and_both(self):
        ps = {"cage": probe(caps=["rescuer", "debs"], vms=["debian13"])}
        self.assertEqual(ph.rank(ps, needs=["zfs"])[1], {"cage": ["no host OpenZFS (no zpool)"]})
        self.assertEqual(ph.rank(ps, guests=["ubuntu24"])[1], {"cage": ["no vms/ubuntu24 image"]})
        self.assertEqual(ph.rank(ps, needs=["rescuer-uefi", "debs"], guests=["debian13", "void"])[1],
                         {"cage": ["no rescuer-uefi.qcow2", "no vms/void image"]})
        self.assertEqual(ph.rank(ps, needs=["rescuer", "debs"], guests=["debian13"])[0][0]["host"], "cage")

    def test_a_reserve_over_the_ceiling_can_never_fit(self):
        ps = {"cage": probe(ceiling=(9.0, 11.2), free=(9.0, 11.0)),
              "cinqcent": probe(host="cinqcent", ceiling=(29.0, 23.2), free=(2.0, 2.0))}
        ranked, dropped = ph.rank(ps, reserve=(13, 6))
        self.assertEqual([x["host"] for x in ranked], ["cinqcent"])      # full now, but it can hold it
        self.assertEqual(dropped, {"cage": ["--reserve 13 6 is over its ceiling 9 11.2"]})
        self.assertFalse(ranked[0]["fits_now"])

    def test_the_wrong_architecture_and_a_machine_that_disagrees(self):
        ps = {"bigfoot": probe(host="bigfoot", **{"machine": "aarch64"})}
        rs = rows(bigfoot={"arch": "arm64", "_want_arch": "amd64"})
        self.assertEqual(ph.rank(ps, rows=rs)[1], {"bigfoot": ["an arm64 host, and the guests are amd64"]})
        rs = rows(bigfoot={"arch": "amd64", "_want_arch": "amd64"})
        self.assertEqual(ph.rank(ps, rows=rs)[1],
                         {"bigfoot": ["says it is aarch64 (arm64), and its row is amd64"]})

    def test_a_host_without_cmp_run_at_all(self):
        ps = {"cage": probe(no_cmp_run="/home/m/backotter-work/testvm/compare/cmp-run.sh")}
        self.assertEqual(ph.rank(ps)[1],
                         {"cage": ["no /home/m/backotter-work/testvm/compare/cmp-run.sh"]})

    def test_a_host_whose_ceiling_failed_can_hold_no_job_at_all(self):
        """cmp-run.sh exits 1 with "no tmpfs at /mnt/bko-ram" before it reads --ceiling or
        --free: such a host has no room ever, not merely none now, and saying "sync
        testvm/ there" instead would have it printed as the host to use."""
        ps = {"cage": probe(free=None, ceiling=None, ceiling_rc=1,
                            ceiling_err="no tmpfs at /mnt/bko-ram")}
        self.assertEqual(ph.rank(ps), ([], {"cage": ["no tmpfs at /mnt/bko-ram"]}))
        ps = {"cage": probe(free=None, ceiling=None, ceiling_rc=2)}
        self.assertEqual(ph.rank(ps)[1], {"cage": ["cmp-run.sh --ceiling failed (exit 2)"]})
        ps = {"cage": probe(free=None, ceiling=None, ceiling_rc=0)}
        self.assertEqual(ph.rank(ps)[1], {"cage": ["cmp-run.sh --ceiling said no figures"]})


class Ranking(unittest.TestCase):
    def test_room_now_comes_before_room_later(self):
        ps = {"cage": probe(free=(9.0, 9.0), load=0.1),
              "chopin": probe(host="chopin", free=(3.0, 2.5), ceiling=(29.0, 23.2), load=0.1)}
        ranked, dropped = ph.rank(ps, reserve=(8, 5), rows=rows(cage={}, chopin={"roles": ["fleet"]}))
        self.assertEqual(dropped, {})
        self.assertEqual([x["host"] for x in ranked], ["cage", "chopin"])
        self.assertEqual([x["fits_now"] for x in ranked], [True, False])

    def test_then_the_roomiest_then_the_least_loaded(self):
        ps = {"cage": probe(free=(9.0, 9.0), load=8.0), "copland": probe(host="copland", free=(16.0, 9.0), load=8.0)}
        self.assertEqual([x["host"] for x in ph.rank(ps, reserve=(8, 5))[0]], ["copland", "cage"])
        ps["copland"]["free"] = (9.0, 9.0)                      # the same room: the quieter host
        ps["copland"]["load"] = 1.0
        self.assertEqual([x["host"] for x in ph.rank(ps, reserve=(8, 5))[0]], ["copland", "cage"])
        ps["copland"]["nproc"] = 4                              # load a core, not load
        ps["cage"]["load"], ps["cage"]["nproc"] = 2.0, 16
        self.assertEqual([x["host"] for x in ph.rank(ps, reserve=(8, 5))[0]], ["cage", "copland"])

    def test_the_shared_host_is_treated_as_busier(self):
        ps = {"cage": probe(free=(9.0, 9.0), load=3.0, nproc=12),
              "chopin": probe(host="chopin", free=(9.0, 9.0), load=3.0, nproc=12)}
        rs = rows(cage={}, chopin={"roles": ["build", "fleet", "store-mirror"]})
        self.assertEqual([x["host"] for x in ph.rank(ps, reserve=(8, 5), rows=rs)[0]], ["cage", "chopin"])
        self.assertAlmostEqual(ph.load_per_core(ps["chopin"], rs["chopin"]), (3.0 + ph.LOAD_PENALTY) / 12)
        self.assertAlmostEqual(ph.load_per_core(ps["cage"], rs["cage"]), 3.0 / 12)
        ps["chopin"]["free"] = (29.0, 20.0)                     # room still wins over the penalty
        self.assertEqual([x["host"] for x in ph.rank(ps, reserve=(8, 5), rows=rs)[0]], ["chopin", "cage"])

    def test_without_a_reserve_the_roomiest_host_wins_and_a_full_one_does_not_fit(self):
        ps = {"cage": probe(free=(9.0, 9.0)), "copland": probe(host="copland", free=(0.0, 4.0))}
        ranked, _ = ph.rank(ps)
        self.assertEqual([x["host"] for x in ranked], ["cage", "copland"])
        self.assertEqual([x["fits_now"] for x in ranked], [True, False])

    def test_a_host_whose_cmp_run_has_no_free_keeps_its_place_last(self):
        ps = {"cage": probe(free=None), "copland": probe(host="copland", free=(1.0, 1.0))}
        ranked, dropped = ph.rank(ps, reserve=(8, 5))
        self.assertEqual(dropped, {})
        self.assertEqual([x["host"] for x in ranked], ["copland", "cage"])
        self.assertEqual(ranked[-1]["score"], 0.0)
        self.assertIn("no --free", ranked[-1]["note"])
        self.assertIsNone(ranked[0]["note"])

    def test_the_note_of_a_host_whose_ceiling_worked_and_whose_free_did_not(self):
        """The host stays a candidate (its ceiling answered), and the note says which it
        was: a cmp-run.sh too old to know --free, or a --free that failed."""
        ps = {"cage": probe(free=None, ceiling_rc=0)}
        self.assertIn("no --free (sync testvm/ there)", ph.rank(ps)[0][0]["note"])
        ps = {"cage": probe(free=None, ceiling_rc=0, free_rc=1,
                            free_err="cmp-run.sh --free: can't tell what room there is here")}
        ranked, dropped = ph.rank(ps)
        self.assertEqual(dropped, {})
        self.assertIn("--free failed: cmp-run.sh --free: can't tell", ranked[0]["note"])
        ps = {"cage": probe(free=None, ceiling_rc=0, free_rc=3)}
        self.assertIn("--free failed (exit 3)", ph.rank(ps)[0][0]["note"])

    def test_the_figures_it_reports(self):
        ranked, _ = ph.rank({"cage": probe(free=(9.0, 10.0), waiting=3)}, reserve=(3, 5))
        self.assertEqual(ranked[0], {"host": "cage", "free": (9.0, 10.0), "ceiling": (9.0, 11.2),
                                     "waiting": 3, "load": 1.0, "nproc": 16, "fits_now": True,
                                     "score": 2.0, "load_per_core": 0.06, "note": None})


class Figures(unittest.TestCase):
    def test_fits_and_score_and_num(self):
        self.assertTrue(ph.fits((9.0, 9.2), (9.0, 9.2)))
        self.assertFalse(ph.fits((9.0, 9.2), (9.0, 9.3)))
        self.assertFalse(ph.fits(None, (0, 0)))
        self.assertEqual(ph.score_of((9.0, 10.0), (3, 5)), 2.0)
        self.assertEqual(ph.score_of((9.0, 10.0), (0, 5)), 2.0)      # a 0 asks for nothing
        self.assertEqual(ph.score_of((9.0, 10.0), (0, 0)), 0.0)
        self.assertEqual(ph.score_of(None, (1, 1)), 0.0)
        self.assertEqual([ph.num(x) for x in (13.0, 9.5, 0.0)], ["13", "9.5", "0"])

    def test_split_list_keeps_order_and_drops_repeats(self):
        self.assertEqual(ph.split_list(["a,b", " c ", "a", ""]), ["a", "b", "c"])
        self.assertEqual(ph.split_list(None), [])


class TheProbeScript(unittest.TestCase):
    def test_it_is_a_shell_script_that_parses(self):
        r = subprocess.run(["sh", "-n", "-c", ph.PROBE_SH], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_it_writes_nothing_on_the_host(self):
        """Every session may run it, on a host in the middle of a run: it only reads."""
        code = re.sub(r"(?m)^\s*#.*$", "", ph.PROBE_SH)
        self.assertNotRegex(code, r"\b(rm|mkdir|touch|dd|tee|mv|cp|install|chmod|sudo|setsid|nohup)\b")
        self.assertNotRegex(code, r">(?!/dev/null)(?!&)")       # no redirection but to /dev/null
        self.assertIn("cmp-run.sh --free", code)
        self.assertIn("cmp-run.sh --ceiling", code)
        # An un-synced host's older cmp-run.sh takes --free for a queue TAG and makes and
        # chmods the ledger's files before it dies, so the option is looked for in the
        # script itself before it is ever asked.
        self.assertIn("grep -q -- --free", code)
        self.assertLess(code.index("grep -q -- --free"), code.index("cmp-run.sh --free"))

    def test_it_reports_what_cmp_run_said(self):
        """Its status and the line it died on, so a host that can hold no job at all (no
        tmpfs) is told from one that is merely out of room."""
        code = re.sub(r"(?m)^\s*#.*$", "", ph.PROBE_SH)
        for key in ("ceiling_rc", "ceiling_err", "free_rc", "free_err"):
            self.assertIn(key, code, key)

    def run_probe_sh(self, script, *guests):
        """The probe run here, against a HOME of our own whose backotter-work holds that
        cmp-run.sh: its parsed answer.  (The probe only reads, and everything else it
        looks at -- /proc, /bkoscratch, vms/ -- is simply missing.)"""
        with tempfile.TemporaryDirectory() as d:
            where = os.path.join(d, "backotter-work", "testvm", "compare")
            os.makedirs(where)
            cmp_run = os.path.join(where, "cmp-run.sh")
            with open(cmp_run, "w", encoding="utf-8") as f:
                f.write(script)
            os.chmod(cmp_run, 0o755)
            r = subprocess.run(["sh", "-s", "--", *guests], input=ph.PROBE_SH,
                               capture_output=True, text=True, timeout=60,
                               env=dict(os.environ, HOME=d))
            return ph.parse_probe(r.stdout), r

    # A login shell of the host's own writing to stderr while cmp-run.sh succeeds anyway:
    # the classic locale warning when the ssh client forwards LC_* to a host without it.
    NOISY = """#!/bin/sh
echo 'bash: warning: setlocale: LC_CTYPE: cannot change locale (UTF-8)' >&2
case ${1:-} in
--ceiling) echo '9.0 11.2' ;;
--free) echo '8.9 10.4'; echo 'waiting 2' ;;
esac
"""

    def test_the_figures_are_found_past_a_shell_warning_on_stderr(self):
        """stderr is folded into stdout (nothing here may write a file), so the figures
        are taken by shape, not from the first line: a host whose login shell warns is a
        host with room, and was dropped as "--ceiling said no figures"."""
        got, r = self.run_probe_sh(self.NOISY, "debian13")
        self.assertTrue(got["ok"], r.stdout + r.stderr)
        self.assertEqual((got["ceiling_rc"], got["ceiling"]), (0, (9.0, 11.2)))
        self.assertEqual((got["free_rc"], got["free"], got["waiting"]), (0, (8.9, 10.4), 2))
        # and it is not dropped for want of figures (machine: this is run wherever)
        self.assertEqual(ph.rank({"cage": dict(got, machine="x86_64")})[1], {})

    def test_what_a_failing_cmp_run_said_is_its_last_line_not_the_warning(self):
        """A host with no tmpfs: the reason is cmp-run.sh's own, which it says last, not
        whatever the shell printed before it."""
        got, r = self.run_probe_sh("""#!/bin/sh
echo 'bash: warning: setlocale: LC_CTYPE: cannot change locale (UTF-8)' >&2
echo 'cmp-run.sh: no tmpfs at /mnt/bko-ram' >&2
exit 1
""")
        self.assertTrue(got["ok"], r.stdout + r.stderr)
        self.assertEqual((got["ceiling_rc"], got["ceiling_err"]),
                         (1, "cmp-run.sh: no tmpfs at /mnt/bko-ram"))
        self.assertNotIn("ceiling", got)
        self.assertEqual(ph.rank({"cage": dict(got, machine="x86_64")})[1],
                         {"cage": ["cmp-run.sh: no tmpfs at /mnt/bko-ram"]})

    def test_it_is_executable_and_runs_isolated(self):
        self.assertTrue(os.stat(PICK).st_mode & stat.S_IXUSR)
        r = subprocess.run([sys.executable, "-I", "-B", PICK, "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--reserve", r.stdout)


class OneHostsProbe(unittest.TestCase):
    """probe_one()'s budget, with ssh replaced: --timeout is the whole of one host's probe,
    both the row's ssh destination and its ip fallback together.  Bigfoot is the row with
    two of them (its mDNS name fails on the user's network often enough)."""

    def setUp(self):
        self.table = ph._host_table()
        self.assertTrue(self.table.ssh_fallback("bigfoot"), "the row with an ip fallback")

    def run_probe(self, timeout, make):
        """One probe with every ssh raising MAKE(the timeout it was given): the facts, and
        the timeout of each try."""
        seen = []

        def fake(argv, **kw):
            seen.append(kw.get("timeout"))
            raise make(kw.get("timeout"))

        with mock.patch.object(ph.subprocess, "run", fake):
            return ph.probe_one(self.table, "bigfoot", (), timeout), seen

    def test_a_destination_that_runs_the_budget_out_ends_the_probe(self):
        got, seen = self.run_probe(4.0, lambda t: subprocess.TimeoutExpired("ssh", t))
        self.assertEqual(len(seen), 1, seen)                 # not 4 s on each name: 4 s in all
        self.assertLessEqual(seen[0], 4.0)
        self.assertEqual(got["error"], "timed out after 4.0s")
        self.assertFalse(got["ok"])

    def test_the_fallback_gets_what_is_left_of_the_budget(self):
        got, seen = self.run_probe(10.0, lambda t: OSError("no route to host"))
        self.assertEqual(len(seen), 2, seen)                 # ssh failed at once: try the ip
        self.assertLessEqual(seen[1], seen[0])
        self.assertIn("no route to host", got["error"])


class TheCommandLine(unittest.TestCase):
    """main() against the real host table, with the probe replaced: no ssh, no host."""

    def run_main(self, argv, probes, asked=None):
        def fake(table, hosts, guests, timeout):
            if asked is not None:
                asked.extend(hosts)
            return {h: probes[h] for h in hosts if h in probes}

        with mock.patch.object(ph, "probe_hosts", fake):
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
                rc = ph.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_the_default_hosts_of_the_table_are_the_candidates(self):
        table = ph._host_table()
        defaults = list(table.names(default=True, arch="amd64"))
        self.assertTrue(len(defaults) >= 2, defaults)
        ps = {h: probe(host=h, free=(9.0, 9.0), load=i, vms=["debian13"], caps=["debs"], nproc=12)
              for i, h in enumerate(defaults)}
        rc, out, err = self.run_main(["--guest", "debian13", "--needs", "debs", "--all", "--field", "name"], ps)
        self.assertEqual(rc, 0, err)
        self.assertEqual(sorted(out.split()), sorted(defaults))
        rc, out, _ = self.run_main(["--guest", "debian13", "--needs", "debs"], ps)
        self.assertEqual((rc, out.count("\n")), (0, 1))
        self.assertIn("@", out)                                  # the ssh destination by default

    def test_count_and_json_and_no_capable_host(self):
        table = ph._host_table()
        defaults = list(table.names(default=True, arch="amd64"))
        ps = {h: probe(host=h, free=(9.0, 9.0)) for h in defaults}
        rc, out, _ = self.run_main(["--count", "2", "--field", "name"], ps)
        self.assertEqual((rc, len(out.split())), (0, 2))
        rc, out, _ = self.run_main(["--json", "--field", "name"], ps)
        got = json.loads(out)
        self.assertEqual(sorted(got["candidates"]), sorted(defaults))
        self.assertEqual(got["dropped"], {})
        self.assertEqual(len(got["ranked"]), len(defaults))
        rc, out, err = self.run_main(["--guest", "nosuchguest", "--field", "name"], ps)
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("no host can take this job now or ever", err)
        self.assertIn("no vms/nosuchguest image", err)

    def test_bad_usage_and_unknown_hosts(self):
        for argv in (["--needs", "nope"], ["--count", "0"], ["--field", "x"], ["--timeout", "0"],
                     ["--reserve", "-1", "2"], ["--guest", "-x"]):
            with mock.patch.object(sys, "stderr", io.StringIO()), self.assertRaises(SystemExit) as cm:
                ph.main(argv)
            self.assertEqual(cm.exception.code, 2, argv)
        rc, _, err = self.run_main(["--hosts", "czerny"], {})
        self.assertEqual(rc, 2)
        self.assertIn("no such host czerny", err)
        rc, _, err = self.run_main(["--guest", "debian13,debian13-arm64"], {})
        self.assertEqual(rc, 2)
        self.assertIn("both architectures", err)

    def test_class_and_exclude_narrow_the_candidates(self):
        table = ph._host_table()
        small = [h for h in table.names(default=True, arch="amd64") if table.host_class(h) == "small"]
        big = [h for h in table.names(default=True, arch="amd64") if table.host_class(h) == "big"]
        ps = {h: probe(host=h, free=(9.0, 9.0)) for h in small + big}
        if small:
            rc, out, _ = self.run_main(["--class", "small", "--all", "--field", "name"], ps)
            self.assertEqual((rc, sorted(out.split())), (0, sorted(small)))
        rc, out, _ = self.run_main(["--exclude", ",".join(big + small[:1]), "--all", "--field", "name"], ps)
        self.assertEqual(sorted(out.split()), sorted(small[1:]))

    def test_an_arm64_guest_has_no_default_host_yet(self):
        rc, out, err = self.run_main(["--guest", "debian13-arm64", "--field", "name"], {})
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("no candidate host at all", err)

    def test_hosts_keeps_the_architecture_check_and_asks_no_wrong_arch_host(self):
        """--hosts chooses among the hosts; it is not a way past the check that a host can
        run the guests at all.  Bigfoot is arm64: an amd64 run doesn't even probe it."""
        ps = {h: probe(host=h, free=(9.0, 9.0)) for h in ("cage", "bigfoot")}
        asked = []
        rc, out, err = self.run_main(["--hosts", "cage,bigfoot", "--all", "--field", "name"], ps, asked)
        self.assertEqual((rc, out.split()), (0, ["cage"]), err)
        self.assertEqual(asked, ["cage"])
        asked = []
        rc, out, err = self.run_main(["--hosts", "bigfoot", "--field", "name"], ps, asked)
        self.assertEqual((rc, out, asked), (1, "", []))
        self.assertIn("bigfoot: an arm64 host, and the guests are amd64", err)
        # Its own guests, though, and it is the host (and it still has to be enabled).
        ps = {"bigfoot": probe(host="bigfoot", free=(9.0, 9.0), vms=["debian13-arm64"],
                               **{"machine": "aarch64"})}
        rc, out, err = self.run_main(["--hosts", "bigfoot", "--guest", "debian13-arm64",
                                      "--field", "name"], ps)
        self.assertEqual((rc, out.split()), (0, ["bigfoot"]), err)

    def test_a_host_that_can_hold_no_job_at_all_is_not_printed(self):
        """Its cmp-run.sh --ceiling failed (no tmpfs at /mnt/bko-ram): it used to be
        printed, with exit 0, as the host to send the job to."""
        table = ph._host_table()
        defaults = list(table.names(default=True, arch="amd64"))
        ps = {h: probe(host=h, free=None, ceiling=None, ceiling_rc=1,
                       ceiling_err="no tmpfs at /mnt/bko-ram") for h in defaults}
        rc, out, err = self.run_main(["--field", "name"], ps)
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("no tmpfs at /mnt/bko-ram", err)

    def test_json_comes_out_on_every_exit_path(self):
        table = ph._host_table()
        defaults = list(table.names(default=True, arch="amd64"))
        rc, out, err = self.run_main(["--guest", "debian13-arm64", "--json"], {})
        got = json.loads(out)                                    # no candidate at all
        self.assertEqual((rc, got["candidates"], got["ranked"]), (1, [], []))
        self.assertIn("no candidate host at all", got["error"])
        rc, out, _ = self.run_main(["--hosts", "czerny", "--json"], {})       # bad --hosts
        self.assertEqual(rc, 2)
        self.assertIn("no such host czerny", json.loads(out)["error"])
        ps = {h: probe(host=h, free=(9.0, 9.0)) for h in defaults}
        rc, out, _ = self.run_main(["--guest", "nosuchguest", "--json"], ps)  # all dropped
        got = json.loads(out)
        self.assertEqual((rc, got["ranked"], got["error"]), (1, [], None))
        self.assertEqual(sorted(got["dropped"]), sorted(defaults))



class TheHoltFarm(unittest.TestCase):
    """testvm/holttest1d/ht1-farm.sh, the one command that starts a HoltTest1 queue on every
    default host: its own checks, which happen before it reaches any host.  It takes the
    hosts from the table (testvm/hosts.sh), so no host name is written in it."""

    FARM = os.path.join(TESTVM, "holttest1d", "ht1-farm.sh")

    def run_farm(self, *args):
        return subprocess.run(["bash", self.FARM, *args], capture_output=True, text=True, timeout=60,
                              cwd=os.path.dirname(TESTVM))

    def test_it_parses_and_is_executable(self):
        self.assertTrue(os.stat(self.FARM).st_mode & stat.S_IXUSR)
        r = subprocess.run(["bash", "-n", self.FARM], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_it_refuses_bad_usage_before_it_reaches_a_host(self):
        for args, want in ((("r1",), "no guests"),
                           (("debug", "debian13"), "is ht1-debug.sh"),
                           (("bad run", "debian13"), "RUN: a letter or digit"),
                           (("r1", "not a guest"), "not GUEST[+VARIANT]"),
                           (("r1", "debian13", "--hosts", "nosuch"), "no such host nosuch"),
                           (("r1", "debian13", "--hosts", ","), "names no host"),
                           (("--frobnicate", "r1"), "no such option")):
            r = self.run_farm(*args)
            self.assertEqual(r.returncode, 2, (args, r.stdout, r.stderr))
            self.assertIn(want, r.stderr, args)
        self.assertEqual(self.run_farm().returncode, 2)      # no arguments: the usage

    def test_no_host_name_is_written_in_it(self):
        with open(self.FARM, encoding="utf-8") as f:
            code = re.sub(r"(?m)^\s*#.*$", "", f.read())
        for h in ("chopin", "cinqcent", "copland", "cage", "bigfoot"):
            self.assertNotIn(h, code, h)
        self.assertIn("hosts_names --default --arch amd64", code)
        self.assertIn("hosts_limit", code)

    # A stand-in for ssh: every call is logged (DEST TAB COMMAND), and the few commands the
    # farm makes are answered.  So the whole run is checked here, with no host: what it
    # asks each host before it starts anything, and what arrives in the todo.
    FAKE_SSH = r'''#!/usr/bin/env bash
set -u
args=() nostdin=0
while [[ $# -gt 0 ]]; do
    case $1 in
    -n) nostdin=1; shift ;;
    -T) shift ;;
    -o) shift 2 ;;
    *) args+=("$1"); shift ;;
    esac
done
((nostdin == 0)) || exec </dev/null      # -n, as ssh does it: the command gets nothing

printf '%s\t%s\n' "${args[0]}" "${args[1]:-}" >>"$FAKE_LOG"
# FAKE_DOWN: that destination answers nothing, as ssh to a host that is
# rebooting doesn't (255).
[[ -z ${FAKE_DOWN:-} || ${args[0]} != "$FAKE_DOWN" ]] || exit 255
case ${args[1]:-} in
*"ht1-queue.sh --order"*) printf 'debian13\nalpine324\n' ;;
*"cat >"*) cat >"$FAKE_TODO"; wc -l <"$FAKE_TODO" ;;
esac
exit 0
'''

    def run_fake_farm(self, *args, down=None):
        """The farm with that ssh in its PATH: (result, the ssh calls, the todo that
        arrived on the queue host).  DOWN is an ssh destination that answers nothing."""
        with tempfile.TemporaryDirectory() as d:
            ssh = os.path.join(d, "ssh")
            with open(ssh, "w", encoding="utf-8") as f:
                f.write(self.FAKE_SSH)
            os.chmod(ssh, 0o755)
            env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"],
                       FAKE_LOG=os.path.join(d, "log"), FAKE_TODO=os.path.join(d, "todo"),
                       FAKE_DOWN=down or "")
            r = subprocess.run(["bash", self.FARM, *args], capture_output=True, text=True,
                               timeout=120, cwd=os.path.dirname(TESTVM), env=env)
            calls = []
            if os.path.exists(env["FAKE_LOG"]):
                with open(env["FAKE_LOG"], encoding="utf-8") as f:
                    calls = [l.rstrip("\n").split("\t", 1) for l in f if l.strip()]
            todo = ""
            if os.path.exists(env["FAKE_TODO"]):
                with open(env["FAKE_TODO"], encoding="utf-8") as f:
                    todo = f.read()
        return r, calls, todo

    def test_the_todo_arrives_whole_on_the_queue_host(self):
        """ssh -n hands the remote `cat` an empty file, so the todo was written empty and
        every runner exited at once; the lines are piped in and counted back."""
        r, calls, todo = self.run_fake_farm("r1", "debian13", "alpine324")
        self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
        self.assertEqual(todo, "debian13\nalpine324\n")
        self.assertIn("2 lines", r.stdout)
        writes = [c for c in calls if "cat >" in c[1]]
        self.assertEqual(len(writes), 1, calls)
        self.assertIn("wc -l", writes[0][1])                  # the count is read back

    def test_every_host_is_asked_about_stop_and_abort_first(self):
        """ht1-queue.sh reads $Q/stop and $Q/abort on its own host, so a leftover one
        anywhere would end that host's runner at once: the todo is asked for on the queue
        host, stop and abort on every one, and all of it before any runner starts."""
        r, calls, _ = self.run_fake_farm("r1", "debian13")
        self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
        table = ph._host_table()
        hosts = list(table.names(default=True, arch="amd64"))
        asked = [i for i, c in enumerate(calls) if "stop abort" in c[1]]
        self.assertEqual(len(asked), len(hosts), calls)
        self.assertEqual(len([i for i in asked if "todo stop abort" in calls[i][1]]), 1, calls)
        self.assertEqual(sorted(calls[i][0] for i in asked), sorted(table.ssh(h) for h in hosts))
        started = [i for i, c in enumerate(calls) if "setsid" in c[1]]
        self.assertEqual(len(started), len(hosts), calls)
        self.assertGreater(min(started), max(asked), calls)   # asked first, then started

    def test_a_host_that_does_not_answer_is_left_out_and_the_rest_go_ahead(self):
        """One host rebooting is no reason to refuse the whole run: it is named, the hosts
        that answer get their runners, and the exit status says so at the end (as a host
        whose runner failed to start has always been handled).  --dry-run, which writes
        nothing and costs nothing, doesn't die of it either."""
        table = ph._host_table()
        hosts = list(table.names(default=True, arch="amd64"))
        self.assertGreater(len(hosts), 1, hosts)
        down = hosts[-1]                                      # not the queue host, hosts[0]
        r, calls, todo = self.run_fake_farm("r1", "debian13", down=table.ssh(down))
        self.assertEqual(r.returncode, 1, (r.stdout, r.stderr))
        self.assertIn(f"can't reach {down}", r.stderr)
        self.assertIn(f"no runner on {down}", r.stderr)
        self.assertEqual(todo, "debian13\nalpine324\n")       # the run went ahead (--order's)
        started = [c for c in calls if "setsid" in c[1]]
        self.assertEqual(len(started), len(hosts) - 1, calls)
        self.assertNotIn(table.ssh(down), [c[0] for c in started])
        r, _, todo = self.run_fake_farm("--dry-run", "r1", "debian13", down=table.ssh(down))
        self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
        self.assertEqual(todo, "")

    def test_a_queue_host_that_does_not_answer_ends_the_run(self):
        """The queue host is the exception: its $Q/todo is the whole run."""
        table = ph._host_table()
        down = list(table.names(default=True, arch="amd64"))[0]
        r, calls, todo = self.run_fake_farm("r1", "debian13", down=table.ssh(down))
        self.assertEqual(r.returncode, 2, (r.stdout, r.stderr))
        self.assertIn(f"can't reach the queue host {down}", r.stderr)
        self.assertEqual(todo, "")
        self.assertEqual([c for c in calls if "setsid" in c[1]], [])


if __name__ == "__main__":
    unittest.main()
