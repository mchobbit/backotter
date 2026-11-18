"""A failed run's kept state and the write stop rule (2026-11-18): RAM first moves a failed
run's state to /bkoscratch before its diagnostic rerun (ht1-lib.sh's R to K, `t2-host tool
guest failed-state`'s overlay).  The job marks those bytes (iolog.sh --keep-mark and
--keep-since) and its jobs.log line carries them as keep=DISK=BYTES.  The stop rule's total
leaves them out (run.RunGuards, run.StopEngine, kept=); the wear line, the budget line and
every other total keep them, saying how much of the total they were.

The case: t2h-20261118T2037-e7d1, holt:uc24 FAIL on cinqcent, whose 3.9 GiB job directory
moved from RAM to /bkoscratch as 4.19 GB, over 2 x the 2 GB budget: stop-starts, and the
reruns of guix15, leap16 and uc24 never ran.

No host: python3 -m unittest discover -s tests.
"""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
WT = os.path.dirname(os.path.dirname(T2))
sys.path.insert(0, T2)

from t2lib import bko, holt, hostjob, hostrun, keys, report, run, tool_guest, wear  # noqa: E402

POL = {"budget": {"run_gb": 2, "sweep_gb": 5, "update_gb": 40, "bind_from": None},
       "stop": {"os_need_guests": 2, "harness_results": 2, "swap_out_gib": 1, "write_budget_x": 2}}
RUN = "t2h-20261118T2037-e7d1"
PREFIX = f"ht1:{RUN}"
UC24_TOTAL, UC24_KEPT = 4_190_000_000, 4_170_000_000     # the job's line, and its move's share


def tmpdir(t):
    d = tempfile.mkdtemp(prefix="t2kept-")
    t.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
    return d


def line(end, disks, tag, keep=None, what="reserve 0 4 sudo -E ht1.sh uc24"):
    x = f"\ttag={tag}\tserial=sda:NOT-A-REAL-SERIAL" + (f"\tkeep={keep}" if keep else "") + "\tmpeak=1"
    return f"{end}\tcinqcent\trun-u1.scope\t1\t900\t{disks}\t{what}{x}\n"


JOBS = (line("2026-11-18T20:50:00Z", f"sda={UC24_TOTAL},zram0=77", f"{PREFIX}:uc24", f"sda={UC24_KEPT}")
        + line("2026-11-18T20:52:00Z", "sda=10000000", f"{PREFIX}:kali")
        # keep= over the line's own, on a virtual disk, on a disk the line never wrote: never more than it wrote
        + line("2026-11-18T20:53:00Z", "nvme0n1=1000", f"{PREFIX}-r2:uc24", "nvme0n1=5000,dm-0=9,sdb=7")
        + line("2026-11-18T20:54:00Z", "sda=999999999", "ht1:other-run:uc24", "sda=999999999"))


class WearLog(unittest.TestCase):
    """wear.py's reading of keep=."""

    def test_kept_disks(self):
        jobs = wear.parse_jobs(JOBS)
        self.assertEqual([j.kept_disks() for j in jobs], [{"sda": UC24_KEPT}, {}, {"nvme0n1": 1000}, {"sda": 999999999}])
        self.assertEqual(jobs[0].kept_bytes(), UC24_KEPT)
        self.assertEqual(jobs[0].physical_bytes(), UC24_TOTAL)      # the total still has it
        self.assertNotIn("serial", jobs[0].x)

    def test_an_older_line_has_none(self):
        j = wear.parse_jobs("2026-11-08T05:10:00Z\tlocal\trun-x.scope\t0\t60\tnvme0n1=1000\tcheck\ttag=stic:r:g\n")[0]
        self.assertEqual((j.kept_disks(), j.kept_bytes()), ({}, 0))

    def test_tag_sums_keep_the_true_total(self):
        self.assertEqual(wear.tag_sums(wear.parse_jobs(JOBS), [f"{PREFIX}:uc24"]), {f"{PREFIX}:uc24": UC24_TOTAL})


def local_script(script, log):
    """A host script that reads /bkoscratch/wear/jobs.log, reading LOG instead."""
    assert "/bkoscratch/wear/jobs.log" in script
    return script.replace("/bkoscratch/wear/jobs.log", log)


class HostScripts(unittest.TestCase):
    """The scripts the stop rules' feeds run on a host, here on this machine's jobs.log stand-in."""

    def setUp(self):
        self.log = os.path.join(tmpdir(self), "jobs.log")
        with open(self.log, "w") as f:
            f.write(JOBS)

    def test_hostjob_tag_bytes_kept(self):
        script = local_script(hostjob.TAG_SCRIPT, self.log)

        class IO:
            def sh(self, host, cmd, timeout=60, input=None):
                p = subprocess.run([sys.executable, "-I", "-B", "-", PREFIX, "0"], input=script.encode(),
                                   capture_output=True, timeout=60)
                return p.returncode, p.stdout.decode(), p.stderr.decode()
        total = UC24_TOTAL + 10000000 + 1000
        self.assertEqual(hostjob.tag_bytes_kept(IO(), "cinqcent", PREFIX, 0), (total, UC24_KEPT + 1000))
        self.assertEqual(hostjob.tag_bytes(IO(), "cinqcent", PREFIX, 0), total)

    def test_hostjob_reads_an_older_script_s_answer(self):
        class IO:
            def __init__(self, out, rc=0):
                self.out, self.rc = out, rc

            def sh(self, host, cmd, timeout=60, input=None):
                return self.rc, self.out, ""
        self.assertEqual(hostjob.tag_bytes_kept(IO("1234\n"), "h", "p", 0), (1234, 0))
        self.assertEqual(hostjob.tag_bytes_kept(IO("", rc=255), "h", "p", 0), (None, None))
        self.assertEqual(hostjob.tag_bytes_kept(IO("garbage\n"), "h", "p", 0), (None, None))
        self.assertIsNone(hostjob.tag_bytes(IO("", rc=1), "h", "p", 0))

    def remote(self):
        r = holt.Remote()
        log = self.log

        class H:
            rq = staticmethod(hostrun.rq)

            @staticmethod
            def ssh(host, cmd=None, script=None, args=(), timeout=60, **k):
                return hostrun.ssh(hostrun.LOCAL, script=local_script(script, log), args=args, timeout=timeout)
        r.h = H
        return r

    def test_holt_remote_tag_bytes_kept(self):
        r = self.remote()
        by, kept = r.tag_bytes("cinqcent", PREFIX, 0, by_tag=True, kept=True)
        self.assertEqual(by, {f"{PREFIX}:uc24": UC24_TOTAL, f"{PREFIX}:kali": 10000000, f"{PREFIX}-r2:uc24": 1000})
        self.assertEqual(kept, {f"{PREFIX}:uc24": UC24_KEPT, f"{PREFIX}-r2:uc24": 1000})
        self.assertEqual(r.tag_bytes("cinqcent", PREFIX, 0, kept=True), (UC24_TOTAL + 10001000, UC24_KEPT + 1000))
        # as before without KEPT
        self.assertEqual(r.tag_bytes("cinqcent", PREFIX, 0), UC24_TOTAL + 10001000)
        self.assertEqual(r.tag_bytes("cinqcent", PREFIX, 0, by_tag=True), by)

    def test_the_wear_line_keeps_the_total_and_says_the_kept(self):
        before = {"local": {"preflight": {"diskstats": {"sda": {"model": "FAKE_MODEL", "sectors_written": 0}}}}}
        after = {"local": {"preflight": {"diskstats": {"sda": {"model": "FAKE_MODEL", "sectors_written": 30000000}}}}}
        start = 1795034220   # 2026-11-18T20:37:00Z
        got = run.wear_lines_data(RUN, ["local"], before, after, start, now=start + 3600, log=self.log,
                                  smart=os.path.join(os.path.dirname(self.log), "no-smart.log"))
        sda = got["local"]["sda"]
        self.assertEqual(sda["ours_bytes"], UC24_TOTAL + 10000000)
        self.assertEqual(sda["kept_bytes"], UC24_KEPT)
        self.assertEqual(got["local"]["nvme0n1"]["kept_bytes"], 1000)   # (the -r2 attempt's, its own at most)
        lines = report.wear_lines(got)
        self.assertIn("our jobs 4.20 GB (wear tags; 4.17 GB of it a failed run's state kept), today ? (SMART)",
                      [x for x in lines if " sda " in x][0])
        del got["local"]["sda"]["kept_bytes"]
        self.assertIn("our jobs 4.20 GB (wear tags), today", report.wear_lines(got)[1])


class Guards(unittest.TestCase):
    """RunGuards, StopEngine with and without guards, bko.Guards: KEPT isn't counted."""

    def engines(self):
        g, b = run.RunGuards(POL), bko.Guards(POL)
        return [("guards", run.StopEngine("holt", POL, guards=g), g),
                ("bko.Guards", run.StopEngine("holt", POL, guards=b), b),
                ("plain", run.StopEngine("holt", POL), None)]

    def test_uc24_s_move_alone_trips_nothing(self):
        for name, s, g in self.engines():
            with self.subTest(name):
                self.assertEqual(s.feed_host("cinqcent", ours_bytes=UC24_TOTAL, kept=UC24_KEPT), "ok")
                self.assertTrue(s.may_start())
                if g is not None:
                    self.assertFalse(g.tripped())
                    self.assertEqual(g.host_bytes("cinqcent"), UC24_TOTAL)   # the true total is kept

    def test_without_kept_it_trips_as_it_did(self):
        for name, s, g in self.engines():
            with self.subTest(name):
                self.assertEqual(s.feed_host("cinqcent", ours_bytes=UC24_TOTAL), "stop-starts")
                self.assertIn("cinqcent: this run's jobs wrote 4.19 GB, over 2 x the 2 GB budget", s.stops[-1])

    def test_the_rest_still_counts(self):
        for name, s, g in self.engines():
            with self.subTest(name):
                self.assertEqual(s.feed_host("cinqcent", ours_bytes=UC24_TOTAL + int(3.9e9), kept=UC24_KEPT), "ok")
                self.assertEqual(s.feed_host("cinqcent", ours_bytes=UC24_TOTAL + int(4.1e9), kept=UC24_KEPT),
                                 "stop-starts")
                why = s.stops[-1]
                self.assertIn("wrote 8.29 GB (4.17 GB of it a failed run's state kept on /bkoscratch, not counted; "
                              "4.12 GB counted), over 2 x the 2 GB budget", why)

    def test_kept_never_more_than_the_total(self):
        for name, s, g in self.engines():
            with self.subTest(name):
                self.assertEqual(s.feed_host("cinqcent", ours_bytes=int(1e9), kept=int(9e9)), "ok")
                self.assertEqual(s.feed_host("cinqcent", ours_bytes=int(5e9), kept=int(9e9)), "ok")

    def test_beside_a_write_allowance(self):
        v3 = "holt:vanilla3"
        for name, s, g in self.engines():
            with self.subTest(name):
                self.assertEqual(s.feed_host("chopin", ours_bytes=int(35e9), allowed={v3: int(30e9)}, kept=int(3e9)),
                                 "ok")
                self.assertEqual(s.feed_host("chopin", ours_bytes=int(38e9), allowed={v3: int(30e9)}, kept=int(3e9)),
                                 "stop-starts")
                self.assertIn("wrote 38.00 GB (3.00 GB of it a failed run's state kept on /bkoscratch; 30.00 GB of it "
                              "within the write allowance of holt:vanilla3, not counted; 5.00 GB counted)", s.stops[-1])

    def test_the_bound_budget_counts_it_out_too(self):
        pol = dict(POL, budget=dict(POL["budget"], bind_from="2026-11-01"))
        for g in (None, run.RunGuards(pol)):
            with self.subTest(guards=g is not None):
                s = run.StopEngine("holt", pol, guards=g)
                self.assertEqual(s.feed_host("cinqcent", ours_bytes=UC24_TOTAL, kept=UC24_KEPT), "ok")
                self.assertEqual(s.feed_host("cinqcent", ours_bytes=UC24_TOTAL + int(2.1e9), kept=UC24_KEPT),
                                 "stop-starts")
                self.assertIn("over the 2 GB budget (bound)", s.stops[-1])

    def test_kept_per_family_in_a_composite(self):
        g = run.RunGuards(POL)
        self.assertIsNone(g.feed_host("holt", "cinqcent", ours_bytes=UC24_TOTAL, kept=UC24_KEPT))
        self.assertIsNone(g.feed_host("guest", "cinqcent", ours_bytes=int(1e9), kept=int(0.9e9)))
        self.assertIsNone(g.feed_host("stic", "cinqcent", ours_bytes=int(3.8e9)))
        self.assertIsNotNone(g.feed_host("stic", "cinqcent", ours_bytes=int(3.9e9)))   # 0.02 + 0.1 + 3.9 > 4
        self.assertIn("5.07 GB of it a failed run's state kept", g.why()[0])

    def test_json_keeps_the_kept(self):
        g = run.RunGuards(POL)
        g.feed_host("holt", "cinqcent", ours_bytes=UC24_TOTAL, kept=UC24_KEPT)
        doc = g.to_json()
        self.assertEqual(doc["kept"], [["holt", "cinqcent", UC24_KEPT]])
        g2 = run.RunGuards.from_json(doc, POL)
        self.assertIsNone(g2.feed_host("guest", "cinqcent", ours_bytes=int(3.9e9)))
        self.assertIsNotNone(g2.feed_host("guest", "cinqcent", ours_bytes=int(4.1e9)))
        old = {k: v for k, v in doc.items() if k != "kept"}            # a resume of an older run
        self.assertEqual(run.RunGuards.from_json(old, POL).kept, {})

    def test_an_older_guards_object_gets_no_kept_unless_there_is_one(self):
        class Old:
            def __init__(self):
                self.fed = []

            def feed_host(self, family, host, swap_out_bytes=None, ours_bytes=None, allowed=None):
                self.fed.append((family, host, ours_bytes, allowed))

            def tripped(self):
                return False
        o = Old()
        s = run.StopEngine("holt", POL, guards=o)
        self.assertEqual(s.feed_host("h", ours_bytes=5, allowed={"c": 1}), "ok")
        self.assertEqual(s.feed_host("h", ours_bytes=5), "ok")
        self.assertEqual(o.fed, [("holt", "h", 5, {"c": 1}), ("holt", "h", 5, None)])


class KeptRemote:
    def __init__(self, by, kept):
        self.by, self.kb, self.asked = by, kept, []

    def pswpout(self, host):
        return 0

    def tag_bytes(self, host, prefix, since, by_tag=False, kept=False):
        self.asked.append((host, by_tag, kept))
        by = {t: n for t, n in self.by.get(host, {}).items() if t.startswith(prefix)}
        kb = {t: n for t, n in self.kb.get(host, {}).items() if t.startswith(prefix)}
        if kept:
            return (by, kb) if by_tag else (sum(by.values()), sum(kb.values()))
        return by if by_tag else sum(by.values())


SF = keys.Specs(None, overrides={"holt": {"v": 1, "family": "holt", "kinds": {"linux": {}}, "entries": {
    "uc24": {"kind": "linux"}, "kali": {"kind": "linux"},
    "vanilla3": {"kind": "linux", "write_allow": {"gb": 36, "measured": "test"}}}}}).get("holt")


class HoltHostChecks(unittest.TestCase):
    """holt.Runner.host_checks feeds KEPT, keeps it in holt.json, and the result has it."""

    def runner(self, cells, by, kept, stops=None):
        tmp = tmpdir(self)
        r = holt.Runner.__new__(holt.Runner)
        r.run_id, r.hosts, r.t0, r.swap0, r.sf = RUN, ["cinqcent", "chopin"], 1000.0, {}, SF
        r.plan = types.SimpleNamespace(cells=[types.SimpleNamespace(cell=c) for c in cells])
        r.remote = KeptRemote(by, kept)
        r.state = holt.State(os.path.join(tmp, "holt.json"))
        r.allowed, r.kept, r.acted = {}, {}, []
        r.act = lambda qrun, what: r.acted.append(what)
        r.rs = types.SimpleNamespace(stops=stops or run.StopEngine("holt", POL, guards=run.RunGuards(POL)))
        return r

    def test_uc24_s_failure_stops_nothing(self):
        r = self.runner(["holt:uc24", "holt:kali"], {"cinqcent": {f"{PREFIX}:uc24": UC24_TOTAL}},
                        {"cinqcent": {f"{PREFIX}:uc24": UC24_KEPT}})
        r.host_checks(RUN)
        self.assertEqual(r.acted, ["ok", "ok"])
        self.assertFalse(r.rs.stops.guards.tripped())
        self.assertEqual(r.state["write_kept"], {"cinqcent": UC24_KEPT})
        self.assertEqual(holt.State(r.state.path)["write_kept"], {"cinqcent": UC24_KEPT})   # (a resume reads it)
        self.assertTrue(all(k for _, _, k in r.remote.asked))

    def test_uc24_s_failure_and_others_over_still_stop(self):
        r = self.runner(["holt:uc24", "holt:kali"],
                        {"cinqcent": {f"{PREFIX}:uc24": UC24_TOTAL, f"{PREFIX}:kali": int(4.1e9)}},
                        {"cinqcent": {f"{PREFIX}:uc24": UC24_KEPT}})
        r.host_checks(RUN)
        self.assertEqual(r.acted[0], "stop-starts")
        self.assertIn("wrote 8.29 GB (4.17 GB of it a failed run's state kept on /bkoscratch, not counted; "
                      "4.12 GB counted)", r.rs.stops.stops[0])

    def test_kept_isn_t_excused_twice_by_a_write_allowance(self):
        # vanilla3 (allowance 36 GB) fails: of 71 GB, 30 its move; the allowance takes 36 of the
        # other 41, so 5 count.  Of 40 GB, 30 its move: the allowance takes the other 10, 0 count.
        v3 = f"{PREFIX}:vanilla3"
        r = self.runner(["holt:vanilla3"], {"chopin": {v3: int(71e9)}}, {"chopin": {v3: int(30e9)}})
        r.host_checks(RUN)
        self.assertEqual(r.acted, ["ok", "stop-starts"])
        self.assertIn("30.00 GB of it a failed run's state kept on /bkoscratch; 36.00 GB of it within the write "
                      "allowance of holt:vanilla3, not counted; 5.00 GB counted", r.rs.stops.stops[0])
        self.assertEqual(r.allowed, {"chopin": {"holt:vanilla3": int(36e9)}})
        r = self.runner(["holt:vanilla3"], {"chopin": {v3: int(40e9)}}, {"chopin": {v3: int(30e9)}})
        r.host_checks(RUN)
        self.assertEqual(r.acted, ["ok", "ok"])
        self.assertEqual(r.allowed, {"chopin": {"holt:vanilla3": int(10e9)}})   # its bytes less the kept

    def test_the_result_and_the_budget_line(self):
        r = self.runner(["holt:uc24"], {"cinqcent": {f"{PREFIX}:uc24": UC24_TOTAL}},
                        {"cinqcent": {f"{PREFIX}:uc24": UC24_KEPT}})
        r.host_checks(RUN)
        rr = {"run": RUN, "cells": [{"cell": "holt:uc24", "host": "cinqcent", "gb": 4.21}],
              "write_kept": dict(r.kept)}
        b = report.budget_lines(rr, POL, now=1795034220)
        self.assertEqual(b, ["budget: cinqcent wrote 4.21 GB in this run's jobs, over 2 GB (warn only (bind_from not "
                             "set)); 4.17 GB of it a failed run's state kept on /bkoscratch, which the stop rule doesn't "
                             "count"])
        self.assertEqual(report.budget_lines({"run": RUN, "cells": rr["cells"]}, POL, now=1795034220),
                         ["budget: cinqcent wrote 4.21 GB in this run's jobs, over 2 GB (warn only (bind_from not set))"])

    def test_an_older_remote_feeds_as_before(self):
        class Old(KeptRemote):
            def tag_bytes(self, host, prefix, since, by_tag=False):
                return KeptRemote.tag_bytes(self, host, prefix, since, by_tag)
        r = self.runner(["holt:uc24"], {"cinqcent": {f"{PREFIX}:uc24": UC24_TOTAL}}, {})
        r.remote = Old(r.remote.by, {})
        r.host_checks(RUN)
        self.assertEqual(r.acted[0], "stop-starts")
        self.assertEqual(r.kept, {})

    def test_a_failed_read_feeds_nothing(self):
        class Down(KeptRemote):
            def tag_bytes(self, host, prefix, since, by_tag=False, kept=False):
                return None
        r = self.runner(["holt:uc24"], {}, {})
        r.remote = Down({}, {})
        r.host_checks(RUN)
        self.assertEqual(r.acted, ["ok", "ok"])


class GuestHostChecks(unittest.TestCase):
    """hostjob.host_checks (GuestTest2, ImgTest2): KEPT fed, and kept on the env for the report."""

    def test_feeds_kept(self):
        class Stops:
            def __init__(self):
                self.fed = []

            def feed_host(self, h, swap_out_bytes=None, ours_bytes=None, kept=None):
                self.fed.append((h, ours_bytes, kept))
                return "ok"

        class IO:
            def sh(self, host, cmd, timeout=60, input=None):
                if "pswpout" in cmd:
                    return 0, "0\n", ""
                return 0, ("1500000000 1000000000\n" if host == "chopin" else "7\n"), ""
        rs = types.SimpleNamespace(stops=Stops(), hosts=["chopin", "cinqcent"])
        env = types.SimpleNamespace(io=IO(), hosts=["chopin", "cinqcent"], log=lambda m: None)
        self.assertEqual(hostjob.host_checks(env, rs, "guest:RUN", 1000), [])
        self.assertEqual(rs.stops.fed, [("chopin", 1500000000, 1000000000), ("cinqcent", 7, None)])
        self.assertEqual(env.kept, {"chopin": 1000000000})

    def test_a_real_engine(self):
        class IO:
            def sh(self, host, cmd, timeout=60, input=None):
                return (0, "0\n", "") if "pswpout" in cmd else (0, f"{UC24_TOTAL} {UC24_KEPT}\n", "")
        s = run.StopEngine("guest", POL, guards=run.RunGuards(POL))
        rs = types.SimpleNamespace(stops=s, hosts=["cinqcent"])
        env = types.SimpleNamespace(io=IO(), hosts=["cinqcent"], log=lambda m: None)
        self.assertEqual(hostjob.host_checks(env, rs, "guest:RUN", 1000), [])
        self.assertTrue(s.may_start())


def fake_iolog(d, since_out="sda=123\nnvme0n1=4"):
    """A stand-in for iolog.sh's --keep-mark and --keep-since: it logs its argv."""
    p = os.path.join(d, "iolog.sh")
    with open(p, "w") as f:
        f.write("#!/bin/sh\n"
                f"echo \"$*\" >>{d}/argv\n"
                "case $1 in\n"
                "--keep-mark) echo @sda=1000 ;;\n"
                f"--keep-since) printf '%s\\n' '{since_out}' | tee -a \"$WEAR_KEEP\" ;;\n"
                "esac\n")
    os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
    return p


class GuestFailedState(unittest.TestCase):
    """tool guest failed-state marks what it moves as the job's keep=."""

    def test_keep_mark_and_since(self):
        d = tmpdir(self)
        keep = os.path.join(d, "keep")
        open(keep, "w").close()
        env = {"WEAR_IOLOG": fake_iolog(d), "WEAR_KEEP": keep}
        m = tool_guest.keep_mark(env)
        self.assertEqual(m, [env["WEAR_IOLOG"], "@sda=1000"])
        with mock.patch.dict(os.environ, env):
            self.assertEqual(tool_guest.keep_since(m), "sda=123\nnvme0n1=4")
        with open(keep) as f:
            self.assertEqual(f.read(), "sda=123\nnvme0n1=4\n")

    def test_no_iolog_above_marks_nothing(self):
        d = tmpdir(self)
        self.assertIsNone(tool_guest.keep_mark({}))
        self.assertIsNone(tool_guest.keep_mark({"WEAR_IOLOG": fake_iolog(d)}))                     # no WEAR_KEEP
        self.assertIsNone(tool_guest.keep_mark({"WEAR_IOLOG": fake_iolog(d), "WEAR_KEEP": d + "/nope"}))
        self.assertIsNone(tool_guest.keep_mark({"WEAR_IOLOG": d + "/no-such", "WEAR_KEEP": __file__}))
        self.assertEqual(tool_guest.keep_since(None), "")

    def test_failed_state_keeps_and_marks(self):
        d = tmpdir(self)
        out, hroot = os.path.join(d, "out"), os.path.join(d, "hroot")
        ram = os.path.join(d, "ram", ".vm-update-run.x")
        os.makedirs(out)
        os.makedirs(ram)
        with open(os.path.join(ram, "overlay.qcow2"), "w") as f:
            f.write("x" * 4096)
        with open(os.path.join(out, "result.json"), "w") as f:
            f.write('{"overlay_dir": "%s", "tail": ["boom"]}' % ram)
        keep = os.path.join(d, "keep")
        open(keep, "w").close()
        env = {"WEAR_IOLOG": fake_iolog(d), "WEAR_KEEP": keep}
        with mock.patch.dict(os.environ, env):
            a = tool_guest.failed_state("g1", "t2v-x", 1, out, hroot, say=lambda x: None)
        self.assertTrue(a["overlay"])
        self.assertEqual(a["keep"], "sda=123,nvme0n1=4")
        self.assertFalse(os.path.exists(ram))
        with open(os.path.join(d, "argv")) as f:
            self.assertEqual(f.read().split("\n")[:2], ["--keep-mark", "--keep-since @sda=1000"])
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WEAR_KEEP", None)
            os.environ.pop("WEAR_IOLOG", None)
            os.makedirs(ram)
            a = tool_guest.failed_state("g1", "t2v-x", 2, out, hroot, say=lambda x: None)
        self.assertNotIn("keep", a)


@unittest.skipUnless(sys.platform.startswith("linux"), "iolog.sh reads /proc and cgroup v2")
class IologKeep(unittest.TestCase):
    """iolog.sh's own --keep-mark/--keep-since and the line's keep= (Linux; nothing written
    to a disk here is the point: the deltas may be 0, so only the shape is checked)."""

    def test_shape(self):
        d = tmpdir(self)
        iolog = os.path.join(WT, "testvm", "compare", "iolog.sh")
        log = os.path.join(d, "jobs.log")
        job = ('m=$("$WEAR_IOLOG" --keep-mark); case $m in @*) ;; *) exit 7 ;; esac; '
               '[ -f "$WEAR_KEEP" ] || exit 8; echo sda=5 >>"$WEAR_KEEP"; '
               '"$WEAR_IOLOG" --keep-since "$m" >/dev/null')
        p = subprocess.run(["bash", iolog, "t", "--", "bash", "-c", job], env=dict(os.environ, WEAR_LOG=log,
                           WEAR_TAG="test:kept"), capture_output=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        if os.path.exists(log):     # (no io.stat where this runs: no line)
            with open(log) as f:
                text = f.read()
            self.assertIn("tag=test:kept", text)
            self.assertIn("\tkeep=sda=5", text)


if __name__ == "__main__":
    unittest.main()
