"""A guest update the runner stopped at its timeout while it waited on the network
(t2v-20261119T0157-c8c7, 2026-11-19: the farm's link dropped 40% of packets; seven updates
crawled at 31 B/s-50 kB/s for 5400 s each, were stopped by t2's job timeout, judged
"harness: stopped from outside", and the composite's run-wide harness count then stopped
SticTest2 and HoltTest2, which download nothing).

  - host_job's give_up writes why it stopped the job (.stop-why) and marks the JobFailure stopped;
  - the guest row says "stopped by t2: no end after 5400 s", and what in update.log says the
    network stalled (rows_guest.net_stall);
  - GuestTest2's runner takes the stopped job's own row and feeds net=True;
  - StopEngine counts such results toward the family's own stop only, never the run-wide count.
"""

import json
import os
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import test_guest as TG  # noqa: E402
from t2lib import guest, hostjob, rows_guest  # noqa: E402
from t2lib import run as runmod  # noqa: E402

setUpModule, tearDownModule = TG.setUpModule, TG.tearDownModule

STALLED_DNF = ("in-guest: Linux 6.12.0 x86_64\n"
               "[MIRROR] kernel-core.rpm: Curl error (28): Timeout was reached for https://mirror.example/k.rpm "
               "[Operation too slow. Less than 1000 bytes/sec transferred the last 30 seconds]\n"
               "Connection to 127.0.0.1 closed by remote host.\n")
SLOW_APT = ("Get:1 http://archive.ubuntu.com/ubuntu resolute InRelease [126 kB]\n"
            "Fetched 32.8 MB in 1h 13min 51s (7,400 B/s)\n"
            "Adding boot menu entry for UEFI Firmware Settings ...\ndone\n")
FAST_APT = "Fetched 14.4 MB in 4s (3,600 kB/s)\nReading package lists...\n"


class NetStall(unittest.TestCase):
    def setUp(self):
        self.out = TG.tmpdir()

    def log(self, text):
        with open(os.path.join(self.out, "update.log"), "w", encoding="utf-8") as f:
            f.write(text)

    def test_curl_stall_lines(self):
        self.log(STALLED_DNF)
        self.assertIn("Timeout was reached", rows_guest.net_stall(self.out))

    def test_a_slow_apt_fetch(self):
        self.log(SLOW_APT)
        self.assertIn("7,400 B/s", rows_guest.net_stall(self.out))

    def test_in_guest_retries_and_go_timeouts(self):
        self.log("Pulling manifest: x\nerror: pinging container registry quay.io: net/http: TLS handshake timeout\n")
        self.assertIn("TLS handshake timeout", rows_guest.net_stall(self.out))
        self.log("in-guest: a download failed: rpm-ostree again in 30 s (1 of 10)\n")
        self.assertIn("rpm-ostree again", rows_guest.net_stall(self.out))

    def test_a_known_network_flake(self):
        self.log("E: Failed to fetch https://ppa.example/Components-amd64.yml  403  Forbidden\n")
        self.assertIsNone(rows_guest.net_stall(self.out, []))
        flakes = rows_guest.flakes_of(TG.REPO)
        self.assertIn("Failed to fetch", rows_guest.net_stall(self.out, flakes))

    def test_a_fast_or_quiet_log_is_none(self):
        self.log(FAST_APT + "Fetched 54 kB in 2s (27 kB/s)\n")   # slow, but over in seconds
        self.assertIsNone(rows_guest.net_stall(self.out, rows_guest.flakes_of(TG.REPO)))
        self.log("ssh: connect to host 127.0.0.1 port 2222: Connection timed out\n")   # the guest's, not the internet's
        self.assertIsNone(rows_guest.net_stall(self.out, rows_guest.flakes_of(TG.REPO)))
        os.remove(os.path.join(self.out, "update.log"))
        self.assertIsNone(rows_guest.net_stall(self.out))

    def test_stop_words(self):
        self.assertEqual(rows_guest.stop_words(self.out), "stopped from outside")
        with open(os.path.join(self.out, rows_guest.STOP_WHY), "w", encoding="utf-8") as f:
            f.write("no end after 5400 s (/bkoscratch/test2/runs/t2v-x/guest/debian12/job.log)\n")
        self.assertEqual(rows_guest.stop_words(self.out), "stopped by t2: no end after 5400 s")
        self.assertEqual(rows_guest.STOP_WHY, hostjob.STOP_WHY)


class Rows(unittest.TestCase):
    setUp = TG.Rows.setUp
    write_cells, hooks, result, build, baseline, log = (TG.Rows.write_cells, TG.Rows.hooks, TG.Rows.result, TG.Rows.build,
                                                        TG.Rows.baseline, TG.Rows.log)

    def stopped(self, update_log, why=None):
        self.result(phase="update", tail=["stopped"], hooks={"before": {"rc": 0, "sentinel": True}})
        self.log(".stopped", "")
        if why:
            self.log(rows_guest.STOP_WHY, why + "\n")
        self.log("update.log", update_log)
        r = self.build(rc=1)
        with open(os.path.join(self.out, "row-summary.json"), encoding="utf-8") as f:
            return r, json.load(f)

    def test_stopped_by_t2_while_the_network_stalled(self):
        r, s = self.stopped(STALLED_DNF, "no end after 5400 s (/bkoscratch/x/job.log)")
        self.assertEqual((r["verdict"], r["class"]), ("HARNESS", "harness"))
        self.assertTrue(r["fails"][0].startswith("harness: stopped by t2: no end after 5400 s; waiting on the network: "), r["fails"][0])
        self.assertIn("Timeout was reached", r["extra"]["net_stall"])
        self.assertEqual(s["net_stall"], r["extra"]["net_stall"])
        self.assertIn("waiting on the network", s["harness"])

    def test_stopped_from_outside_with_no_network_trouble(self):
        r, s = self.stopped(FAST_APT)
        self.assertEqual(r["verdict"], "HARNESS")
        self.assertEqual(r["fails"][0], "harness: stopped from outside")
        self.assertNotIn("net_stall", r["extra"])
        self.assertIsNone(s["net_stall"])

    def test_a_failed_update_is_no_stall(self):
        # not stopped: a FAIL, its rerun and flakes.json's tail match are the rule, as before
        self.result(phase="update", tail=["E: Failed to fetch x"], hooks={"before": {"rc": 0, "sentinel": True}})
        self.log("update.log", STALLED_DNF)
        r = self.build(rc=1)
        self.assertNotIn("net_stall", r["extra"])


class StopRules(unittest.TestCase):
    def res(self, cell, net):
        return {"cell": cell, "family": "guest", "part": None, "verdict": "HARNESS", "class": "harness", "needs": {},
                "os": "dnf", "host": "chopin", "flake": False, "net": net}

    def test_network_stalls_stop_only_their_family(self):
        guards = runmod.RunGuards({})
        g = runmod.StopEngine("guest", {}, guards=guards)
        s = runmod.StopEngine("stic", {}, guards=guards)
        self.assertEqual(g.feed(self.res("guest:elementary8-luks", True)), "ok")
        self.assertEqual(g.feed(self.res("guest:rocky10-lvm", True)), "stop-family")
        self.assertFalse(guards.tripped())
        self.assertFalse(g.may_start("dnf"))
        self.assertTrue(s.may_start("apt"))                          # SticTest2 goes on
        self.assertEqual(g.stops, ["stop-family: 2 results stalled on the network (last guest:rocky10-lvm): "
                                   "nothing more of this family starts"])
        self.assertEqual(guards.harness, 0)

    def test_other_harness_results_are_still_run_wide(self):
        guards = runmod.RunGuards({})
        g = runmod.StopEngine("guest", {}, guards=guards)
        s = runmod.StopEngine("stic", {}, guards=guards)
        g.feed(self.res("guest:pikaos4", True))
        g.feed(self.res("guest:pikaos4-luks", False))
        self.assertFalse(guards.tripped())
        self.assertEqual(g.feed(self.res("guest:kinoite44", False)), "stop-run")
        self.assertTrue(guards.tripped())
        self.assertFalse(s.may_start("apt"))


class Env:
    def __init__(self, io):
        self.io, self.host_root, self.lines, self.rs = io, "/bkoscratch/t2", [], None

    def attempt_run(self, attempt):
        return "t2v-x" if attempt == 1 else f"t2v-x-r{attempt}"

    def copy_path(self, host):
        return "/copy"

    def log(self, t):
        self.lines.append(t)


class HangingIO:
    def __init__(self, gone):
        self.gone, self.shell, self.stops = gone, [], []

    def sh(self, host, cmd, timeout=60, input=None):
        self.shell.append(cmd)
        if "cat .exit" in cmd:
            return 0, "\n1000\n", ""                                 # began, no end
        return 0, "", ""

    def start(self, host, cmd, log, pidfile):
        return 4242

    def alive(self, host, pid, needle=None):
        return True

    def stop(self, host, pid, needle):
        self.stops.append(pid)
        return self.gone

    def sleep(self, s):
        pass


class GiveUp(unittest.TestCase):
    def test_the_reason_is_written_before_the_term(self):
        for gone in (True, False):
            io = HangingIO(gone)
            rc, out, _, why = hostjob.host_job(Env(io), "chopin", "debian12", 1, "true", timeout_s=-1, subdir="guest")
            self.assertIsNone(rc)
            self.assertIn("no end after -1 s", why)
            self.assertEqual(why.stopped, gone)
            self.assertEqual(why.still_running, not gone)
            w = [c for c in io.shell if hostjob.STOP_WHY in c and "printf" in c]
            self.assertEqual(len(w), 1)
            self.assertIn("no end after -1 s", w[0])
            self.assertIn(f"{out}/{hostjob.STOP_WHY}", w[0])
            self.assertEqual(io.stops, [4242])
        self.assertFalse(hostjob.JobFailure("x").stopped)

    def test_a_resumed_job_past_its_time_too(self):
        class IO(HangingIO):
            def sh(self, host, cmd, timeout=60, input=None):
                self.shell.append(cmd)
                if "for f in .exit .began .pid" in cmd:
                    return 0, ".exit=\n.began=1000\n.pid=4242\n", ""
                return 0, "", ""
        io = IO(True)
        rc, out, _, why = hostjob.reattach_job(Env(io), "chopin", "debian12", 1, timeout_s=-1, wait_s=0, subdir="guest")
        self.assertIsNone(rc)
        self.assertTrue(why.stopped)
        self.assertFalse(why.still_running)
        w = [c for c in io.shell if hostjob.STOP_WHY in c]
        self.assertEqual(len(w), 1)
        self.assertIn("no end after -1 s (resume)", w[0])


class StoppedJobs(TG.FakeJobs):
    def __call__(self, env, host, name, attempt, cmd, reserve, wear_tag, timeout_s, wait_s, extra_env, ram, subdir, on_began=None):
        super().__call__(env, host, name, attempt, cmd, reserve, wear_tag, timeout_s, wait_s, extra_env, ram, subdir, on_began)
        why = hostjob.JobFailure(f"no end after {timeout_s} s (x/job.log); stopped")
        why.stopped = True
        return None, hostjob.job_dir(env, name, attempt, subdir), {}, why


class Runner(unittest.TestCase):
    def run_pool(self, summ):
        io = TG.FakeIO()
        io.summ["rocky10-lvm"] = summ
        r, rs, _, _ = TG.make(["rocky10-lvm"], io, StoppedJobs(), hosts=("chopin",))
        th = threading.Thread(target=r.run, daemon=True)
        th.start()
        th.join(10)
        self.assertFalse(th.is_alive())
        return rs, io

    def test_the_stopped_jobs_own_row_is_the_result(self):
        why = "stopped by t2: no end after 5400 s; waiting on the network: Curl error (28): Timeout was reached"
        rs, io = self.run_pool(TG.summary("HARNESS", "harness", harness=why, fails=[f"harness: {why}"], net_stall="Curl error (28)"))
        self.assertEqual(io.rows, [])                                # no Mac row beside the job's own
        fed = rs.stops.fed[-1]
        self.assertEqual((fed["cell"], fed["verdict"], fed["class"], fed["net"]), ("guest:rocky10-lvm", "HARNESS", "harness", True))

    def test_a_stall_free_stop_feeds_the_harness_count(self):
        rs, io = self.run_pool(TG.summary("HARNESS", "harness", harness="stopped by t2: no end after 5400 s", net_stall=None))
        self.assertFalse(rs.stops.fed[-1]["net"])


if __name__ == "__main__":
    unittest.main()
