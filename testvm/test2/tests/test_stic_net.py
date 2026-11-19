"""A SticTest2 check whose test tools' install waited on the network
(t2s-20261119T0726-e436, 2026-11-19: the farm's internet at 10-200 KB/s with 20-50% loss;
stic:alpine324's and stic:chimera's apk add ran out of their step's 300 s, chimera's
/root/c4 was never made so every copy failed too, and the two HARNESS rows' run-wide count
stopped the whole sweep: HoltTest2, GuestTest2 and the gates).

  - stictest1/apk-add.sh: the image's index, this host's package cache, 4 tries, and a
    "FAIL harness-tools: ... (waiting on the network: LINE)" line when the network stalled;
  - rows_stic.net_stall reads that line: the row's extra.net_stall;
  - SticRun.res_of feeds net=True, which counts toward SticTest2's own stop only (run.py's
    StopEngine, and stic.Stops when it runs alone), never the run-wide harness count.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import test_stic as TS  # noqa: E402
from t2lib import rows_stic, stic  # noqa: E402
from t2lib import run as runmod  # noqa: E402

REPO = TS.REPO
APK_ADD = os.path.join(REPO, "testvm", "stictest1", "apk-add.sh")
APK_CHECKS = ("alpine324", "alpine324-arm64", "chimera", "chimera-uefi", "chimera-zfs")

NET_CHIMERA = ("FAIL harness-tools: apk add attr-progs acl-progs libcap-progs failed (waiting on the network: "
               "WARNING: fetching https://repo.chimera-linux.org/current/main: network error)\n"
               "log: /home/m/backotter-work/stictest1/chimera/logs/x/check-1.log\n")
NET_ALPINE = ("FAIL harness-tools: apk add bash attr acl libcap-utils python3 e2fsprogs-extra xfsprogs-extra "
              "coreutils file failed (waiting on the network: no end after 1800 s)\n")


def lines(text):
    return rows_stic.parse_lines(text)


class NetStall(unittest.TestCase):
    def test_the_install_line(self):
        self.assertEqual(rows_stic.net_stall("HARNESS", lines(NET_CHIMERA)),
                         "WARNING: fetching https://repo.chimera-linux.org/current/main: network error")
        self.assertEqual(rows_stic.net_stall("HARNESS", lines(NET_ALPINE)), "no end after 1800 s")

    def test_with_other_harness_lines(self):
        t = NET_CHIMERA + "FAIL harness-copy: mkdir /root/c4 in the guest\nFAIL check ran: stopped early\n"
        self.assertIsNotNone(rows_stic.net_stall("HARNESS", lines(t)))

    def test_no_stall(self):
        # a failed install that doesn't say the network stalled (the old lines, an unknown package)
        self.assertIsNone(rows_stic.net_stall("HARNESS", lines("FAIL harness-tools: apk add failed\n")))
        self.assertIsNone(rows_stic.net_stall("HARNESS", lines(
            "FAIL harness-tools: apk add attr-progs acl-progs libcap-progs failed\n"
            "FAIL harness-copy: /home/m/backotter-work/roundrel9/bin/linux/restic-bko\n")))
        # the phrase on another need's line, a real FAIL beside it, or a verdict that isn't HARNESS
        self.assertIsNone(rows_stic.net_stall("HARNESS", lines("FAIL harness: x (waiting on the network: y)\n")))
        self.assertIsNone(rows_stic.net_stall("FAIL", lines(NET_CHIMERA + "FAIL xattrs: lost\n")))
        self.assertIsNone(rows_stic.net_stall("PASS", lines("PASS xattrs: kept\n")))
        self.assertIsNone(rows_stic.net_stall("HARNESS", []))


class Row(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2-sticnet-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_row_says_so(self):
        w = TS.World(self.tmp, "chimera", NET_CHIMERA, code=1)
        r = TS.checked(w.build(cells=w.cells()))
        self.assertEqual((r["verdict"], r["class"]), ("HARNESS", "harness"))
        self.assertEqual(r["extra"]["net_stall"],
                         "WARNING: fetching https://repo.chimera-linux.org/current/main: network error")
        self.assertIn("waiting on the network: WARNING: fetching", r["note"])

    def test_a_harness_row_without_it(self):
        w = TS.World(self.tmp, "chimera", "FAIL harness-tools: apk add attr-progs failed\n", code=1)
        r = TS.checked(w.build(cells=w.cells()))
        self.assertEqual(r["verdict"], "HARNESS")
        self.assertNotIn("net_stall", r["extra"])


def net_row(guest, n, net=True):
    return TS.row(guest, "HARNESS", n=n, needs={"harness-tools": "FAIL"},
                  extra={"net_stall": "no end after 1800 s"} if net else {})


class Stops(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2-sticnet-run-")
        self.sr = stic.SticRun(TS.RunCtx(self.tmp), TS.Plan([]), TS.FakeEnv({}))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_res_of_feeds_net(self):
        self.assertTrue(self.sr.res_of(net_row("alpine324", 1))["net"])
        self.assertFalse(self.sr.res_of(net_row("alpine324", 1, net=False))["net"])
        r = net_row("alpine324", 1)
        r["verdict"], r["class"] = "FAIL", "fail"
        self.assertFalse(self.sr.res_of(r)["net"])

    def test_under_run_pys_engine_only_sticTest2_stops(self):
        guards = runmod.RunGuards({})
        s = runmod.StopEngine("stic", {}, guards=guards)
        h = runmod.StopEngine("holt", {}, guards=guards)
        self.assertEqual(s.feed(self.sr.res_of(net_row("chimera", 1))), "ok")
        self.assertEqual(s.feed(self.sr.res_of(net_row("alpine324", 2))), "stop-family")
        self.assertFalse(guards.tripped())
        self.assertEqual(guards.harness, 0)
        self.assertFalse(s.may_start("linux"))
        self.assertTrue(h.may_start("linux"))                         # HoltTest2 goes on
        self.assertEqual(s.stops, ["stop-family: 2 results stalled on the network (last stic:alpine324): "
                                   "nothing more of this family starts"])

    def test_other_harness_rows_are_still_run_wide(self):
        guards = runmod.RunGuards({})
        s = runmod.StopEngine("stic", {}, guards=guards)
        s.feed(self.sr.res_of(net_row("chimera", 1)))
        s.feed(self.sr.res_of(net_row("debian12", 2, net=False)))
        self.assertFalse(guards.tripped())
        self.assertEqual(s.feed(self.sr.res_of(net_row("zorin18", 3, net=False))), "stop-run")
        self.assertTrue(guards.tripped())

    def test_sticTest2_alone(self):
        st = stic.Stops({})
        self.assertEqual(st.feed(self.sr.res_of(net_row("chimera", 1))), "ok")
        self.assertEqual(st.feed(self.sr.res_of(net_row("debian12", 2, net=False))), "ok")
        self.assertEqual(st.feed(self.sr.res_of(net_row("alpine324", 3))), "stop-family")
        self.assertFalse(st.stop_run)
        self.assertEqual(st.harness, 1)
        self.assertEqual(st.stops, ["stop-family: 2 results stalled on the network (last stic:alpine324): "
                                    "nothing more of this family starts"])

    def test_a_run_stops_starting(self):
        plan = TS.Plan([TS.PC(f"stic:{g}", "due", "new") for g in ("chimera", "alpine324", "debian12")])
        env = TS.FakeEnv({TS.MARK: [(1, net_row("chimera", 1)), (1, net_row("alpine324", 2))]})
        res = stic.SticRun(TS.RunCtx(self.tmp), plan, env).go()
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "stop")))
        self.assertTrue(res["stops"][0].startswith("stop-family: after stic:alpine324 HARNESS"), res["stops"])


# --- apk-add.sh's host side, with vm_sh and vm_ssh faked ------------------------------------

FAKE = r'''
set -uo pipefail
GUEST=chimera WORK=$1
. "$2"
G=$3            # the fake guest's files
vm_sh() { printf '%s\n' "$1" >"$G/script"; cat "$G/out" 2>/dev/null; return "$(cat "$G/rc")"; }
vm_ssh() {
    case $1 in
        "cat /root/apk.log") cat "$G/apk.log" ;;
        "cat /root/apk.keep") cat "$G/apk.keep" ;;
        "mkdir -p /root/bko-apk && tar -C /root/bko-apk -xf -") mkdir -p "$G/in" && tar -C "$G/in" -xf - ;;
        "cd /root/bko-apk && tar -cf - "*) (cd "$G/cache" && tar -cf - ${1#cd /root/bko-apk && tar -cf - } 2>/dev/null) ;;
        *) echo "unexpected vm_ssh: $1" >&2; return 99 ;;
    esac
}
stic_apk_add attr-progs acl-progs
echo "status $?"
'''


class ApkAdd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2-apkadd-")
        self.work, self.g = os.path.join(self.tmp, "work"), os.path.join(self.tmp, "guest")
        os.makedirs(os.path.join(self.g, "cache"))
        self.cache = os.path.join(self.work, "stictest1", "chimera", "apk-cache")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, name, text):
        with open(os.path.join(self.g, name), "w") as f:
            f.write(text)

    def run_it(self, rc, log="", keep="", cached=()):
        self.put("rc", f"{rc}\n")
        self.put("apk.log", log)
        self.put("apk.keep", keep)
        for n in cached:
            self.put(os.path.join("cache", n), f"pkg {n}\n")
        r = subprocess.run(["bash", "-c", FAKE, "fake", self.work, APK_ADD, self.g], capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.stderr, "")
        return r.stdout

    def test_a_network_error(self):
        out = self.run_it(3, log="apk-add: try 1 of 4 (the image index)\n"
                                 "ERROR: attr-progs-2.5.2-r0: network error (check Internet connection and firewall)\n")
        self.assertIn("FAIL harness-tools: apk add attr-progs acl-progs failed (waiting on the network: ERROR: "
                      "attr-progs-2.5.2-r0: network error (check Internet connection and firewall))", out)
        self.assertTrue(out.endswith("status 1\n"))
        line = [ln for ln in out.splitlines() if ln.startswith("FAIL ")]
        self.assertIsNotNone(rows_stic.net_stall("HARNESS", lines("\n".join(line))))

    def test_the_step_ran_out(self):
        out = self.run_it(124)
        self.assertIn("failed (waiting on the network: no end after 1800 s)", out)

    def test_another_failure(self):
        out = self.run_it(1, log="ERROR: unable to select packages:\n  nosuch (no such package):\n")
        self.assertIn("FAIL harness-tools: apk add attr-progs acl-progs failed\n", out)
        self.assertNotIn("waiting on the network", out)

    def test_the_guest_script(self):
        self.run_it(0)
        with open(os.path.join(self.g, "script")) as f:
            s = f.read()
        self.assertTrue(s.startswith("P='attr-progs acl-progs'\n"))
        self.assertIn("--cache-max-age $age", s)
        self.assertIn("--cache-packages", s)
        r = subprocess.run(["sh", "-n"], input=s, capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_the_cache_goes_both_ways(self):
        a, b = "attr-progs-2.5.2-r0.4ce55459.apk", "acl-progs-2.4.0-r0.b504b9a4.apk"
        out = self.run_it(0, keep=f"{a}\n{b}\n", cached=(a, b))
        self.assertIn("the host's cache has the 2 package(s)", out)
        self.assertEqual(sorted(os.listdir(self.cache)), sorted([a, b]))
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.cache))), ["apk-cache"])   # no .new/.old left
        # next run: the host's cache goes in, and nothing new comes back
        out = self.run_it(0, keep=f"{a}\n{b}\n")
        self.assertEqual(sorted(os.listdir(os.path.join(self.g, "in"))), sorted([a, b]))
        self.assertNotIn("the host's cache has", out)
        # an update: the image's index names a newer acl-progs; the old one leaves the host's cache
        c = "acl-progs-2.4.1-r0.0000beef.apk"
        self.run_it(0, keep=f"{a}\n{c}\n", cached=(c,))
        self.assertEqual(sorted(os.listdir(self.cache)), sorted([a, c]))

    def test_a_failed_copy_back_keeps_the_cache(self):
        a = "attr-progs-2.5.2-r0.4ce55459.apk"
        self.run_it(0, keep=f"{a}\n", cached=(a,))
        out = self.run_it(0, keep=f"{a}\nmissing-1-r0.00000000.apk\n")
        self.assertIn("didn't copy back", out)
        self.assertEqual(os.listdir(self.cache), [a])


class Checks(unittest.TestCase):
    def test_every_apk_check_uses_the_helper(self):
        for g in APK_CHECKS:
            with open(os.path.join(REPO, "testvm", "stictest1", g, "check.sh")) as f:
                s = f.read()
            self.assertIn('. "$HERE/../apk-add.sh"', s, g)
            self.assertIn("if stic_apk_add ", s, g)
            self.assertNotIn("apk add", s.replace("# ", "#"), g)     # no install of its own


if __name__ == "__main__":
    unittest.main()
