"""t2 batch end to end with no host (docs/design/batch.md §11.3): the real `t2 batch` CLI in a
temporary MAC_ROOT, `cmd` entries (some with hosts= claims) and `t2` entries run through a stub
t2 (T2_BATCH_T2, tests/fixtures/batch/stub-t2.py) that takes its family lock, makes a run dir
and sleeps.  ssh is /usr/bin/false here, so nothing can reach a host."""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
T2_DIR = os.path.dirname(HERE)
T2 = os.path.join(T2_DIR, "t2")
STUB = os.path.join(HERE, "fixtures", "batch", "stub-t2.py")
TESTVM = os.path.dirname(T2_DIR)
sys.path.insert(0, T2_DIR)

from t2lib import run as runmod   # noqa: E402


def rd(path):
    with open(path) as f:
        return f.read()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2batch-")
        self.root = os.path.join(self.tmp, "mac")
        os.makedirs(self.root)
        tbl = os.path.join(self.tmp, "hosts.json")
        shutil.copy(os.path.join(TESTVM, "hosts.json"), tbl)
        self.conf = os.path.join(self.tmp, "stub.json")
        self.set_conf({})
        self.facts = os.path.join(self.tmp, "facts.json")     # the stub facts source: the hosts' ceilings
        with open(self.facts, "w") as f:
            json.dump({h: [64, 64] for h in ("chopin", "cinqcent", "copland", "cage")}, f)
        self.env = dict(os.environ, T2_MAC_ROOT=self.root, T2_HOST_ROOT=os.path.join(self.tmp, "host"),
                        T2_BATCH_T2=f"{sys.executable} -B {STUB}", T2_STUB_CONF=self.conf, T2_BATCH_TICK="0.3",
                        T2_BATCH_POLL="0.1", T2_BATCH_STOP_WAIT="2", T2_BATCH_MIN_FREE_GB="0",
                        BKO_HOSTS_JSON=tbl, T2_SSH="/usr/bin/false", PYTHONDONTWRITEBYTECODE="1",
                        T2_BATCH_FACTS=self.facts)
        self.env.pop("T2_BATCH_HOST_PREFLIGHT", None)
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    pass
        # (stub children still running end by themselves within seconds)
        time.sleep(0.2)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def set_conf(self, c):
        with open(self.conf, "w") as f:
            json.dump(c, f)

    def file(self, text, name="b.batch"):
        p = os.path.join(self.tmp, name)
        with open(p, "w") as f:
            f.write(text)
        return p

    def t2(self, *args, timeout=120):
        r = subprocess.run([sys.executable, "-B", T2, "batch"] + list(args), cwd=self.tmp, env=self.env,
                           capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return r

    def start(self, *args):
        p = subprocess.Popen([sys.executable, "-B", T2, "batch"] + list(args), cwd=self.tmp, env=self.env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
                             stdin=subprocess.DEVNULL)
        self.procs.append(p)
        return p

    def bdir(self):
        d = os.path.join(self.root, "batches")
        names = [n for n in os.listdir(d) if n.startswith("bt-")]
        self.assertEqual(len(names), 1, names)
        return os.path.join(d, names[0])

    def state(self):
        with open(os.path.join(self.bdir(), "state.json")) as f:
            return json.load(f)["entries"]

    def wait_for(self, cond, timeout=30, what="condition"):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if cond():
                    return
            except (OSError, ValueError, KeyError, AssertionError):
                pass
            time.sleep(0.1)
        self.fail(f"timed out waiting for {what}")

    def calls(self):
        try:
            with open(os.path.join(self.root, "stub-calls.log")) as f:
                return [ln.split(None, 2)[2] for ln in f.read().splitlines()]
        except OSError:
            return []

    def blog(self):
        with open(os.path.join(self.bdir(), "batch.log")) as f:
            return f.read()



class E2E(Base):
    def test_clean_batch(self):
        self.set_conf({"HoltTest2": {"sleep": 0.5}})
        f = self.file("manual do the thing by hand\ncmd ok -- true\ncmd bad -- false\ncmd cat -- cat\n"
                      "t2 HoltTest2 est=1m\n")
        r = self.t2(f, "--dirty-ok", timeout=60)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        st = self.state()
        self.assertEqual({k: v["status"] for k, v in st.items()},
                         {"002-cmd-ok": "PASS", "003-cmd-bad": "FAIL", "004-cmd-cat": "PASS",
                          "005-t2-holttest2": "PASS"})
        d = self.bdir()
        e = os.path.join(d, "entries", "005-t2-holttest2")
        for n in ("placed", "child", "inner", "started", "run", "exit", "ended", "entry.log", "report.txt"):
            self.assertTrue(os.path.exists(os.path.join(e, n)), n)
        with open(os.path.join(e, "run")) as fh:
            run = fh.read().strip()
        self.assertRegex(run, r"^t2h-\d{8}T\d{4}-[0-9a-f]{4}$")
        self.assertEqual(st["005-t2-holttest2"]["run"], run)
        with open(os.path.join(e, "placed")) as fh:
            self.assertEqual(json.load(fh)["hosts"], ["chopin", "cinqcent", "copland", "cage"])
        start = [c for c in self.calls() if c.startswith("START HoltTest2")]
        self.assertEqual(len(start), 1)
        self.assertIn("chopin,cinqcent,copland,cage", start[0])
        self.assertIn("UNKNOWN_MAX=480", start[0])            # patience 2 h at hostjob's 15 s polls
        self.assertIn("T2_SSH=/usr/bin/false -o ServerAliveCountMax=4 -o ConnectionAttempts=3", start[0])
        for n in ("batch.json", "state.json", "batch.log", "schedule.txt", "report.txt"):
            self.assertTrue(os.path.exists(os.path.join(d, n)), n)
        self.assertFalse(os.path.exists(os.path.join(self.root, "batches", ".running")))
        with open(os.path.join(d, "report.txt")) as fh:
            rep = fh.read()
        self.assertIn("manual (not run): do the thing by hand", rep)
        self.assertIn(f"run {run}", self.blog())
        ls = self.t2("ls")
        self.assertIn(os.path.basename(d), ls.stdout)
        self.assertIn("exit 1", ls.stdout)
        self.assertIn("no batch is running", self.t2("status").stdout)
        # a finished batch: resume says so, and runs nothing
        r2 = self.t2("resume", os.path.basename(d))
        self.assertEqual(r2.returncode, 1)
        self.assertIn("has finished", r2.stdout)
        self.assertEqual(len([c for c in self.calls() if c.startswith("START")]), 1)

    def test_refusals_and_plan(self):
        f = self.file("ht1 R debian13 est=1h\ncmd x -- true\n")
        r = self.t2(f, "--dirty-ok")
        self.assertEqual(r.returncode, 2)
        self.assertIn("runs no real ht1 tool", r.stderr)      # (a test root never reaches the farm)
        p = self.t2(f, "plan")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("001-ht1-r-debian13", p.stdout)
        g = self.file("set hosts chopin,bigfoot\ncmd x -- sudo true\n", "g.batch")
        r = self.t2(g, "plan")
        self.assertEqual(r.returncode, 2)
        self.assertIn("bigfoot", r.stdout)
        self.assertIn("sudo without -n", r.stdout)
        self.assertEqual(self.t2(g, "--dirty-ok").returncode, 2)
        self.assertFalse(os.path.exists(os.path.join(self.root, "batches")) and
                         [n for n in os.listdir(os.path.join(self.root, "batches")) if n.startswith("bt-")])
        bad = self.file("frob\n", "bad.batch")
        r = self.t2(bad, "plan")
        self.assertEqual(r.returncode, 2)
        self.assertIn("bad.batch:1: unknown kind", r.stderr)

    def test_concurrency_matches_plan(self):
        f = self.file("cmd a hosts=copland est=3m -- sleep 1.5\ncmd b hosts=cage est=2m -- sleep 1.5\n"
                      "cmd c hosts=copland est=1m -- sleep 0.5\nserial cmd s est=1m -- sleep 0.3\n")
        plan = self.t2(f, "plan", "--json")
        self.assertEqual(plan.returncode, 0, plan.stderr)
        rows = json.loads(plan.stdout)["schedule"]["rows"]
        planned = [r["id"] for r in rows]
        r = self.t2(f, "--dirty-ok", timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        st = self.state()
        iv = {k: (v["started"], v["ended"]) for k, v in st.items()}

        def overlap(x, y):
            return iv[x][0] < iv[y][1] and iv[y][0] < iv[x][1]

        self.assertTrue(overlap("001-cmd-a", "002-cmd-b"))
        self.assertFalse(overlap("001-cmd-a", "003-cmd-c"))
        for x in ("001-cmd-a", "002-cmd-b", "003-cmd-c"):
            self.assertFalse(overlap(x, "004-cmd-s"))
        actual = sorted(iv, key=lambda k: iv[k][0])
        self.assertEqual(actual, planned)

    def test_lock_race_and_session_lock(self):
        marker = os.path.join(self.tmp, "busy-once")
        self.set_conf({"HoltTest2": {"sleep": 0.3, "busy_once": marker}, "SticTest2": {"sleep": 0.3}})
        f = self.file("cmd first -- sleep 1\nt2 HoltTest2 after=001-cmd-first\nt2 SticTest2 rel6 after=001-cmd-first\n")
        p = self.start(f, "--dirty-ok")
        bl = lambda: os.path.join(self.bdir(), "batch.log")   # noqa: E731
        self.wait_for(lambda: "001-cmd-first: start on" in rd(bl()), what="the first start")
        session = runmod.FamilyLock("stic", self.root).acquire("t2s-20261115T0000-cafe")
        try:
            self.wait_for(lambda: "waits: lock stic: session t2s-20261115T0000-cafe" in rd(bl()),
                          what="the session wait")
            time.sleep(1.0)
        finally:
            session.release()
        out, errs = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 0, out + errs)
        st = self.state()
        self.assertEqual(st["002-t2-holttest2"]["status"], "PASS")
        self.assertEqual(st["002-t2-holttest2"]["lost_races"], 1)
        self.assertIn("lost the lock race, back to ready", self.blog())
        self.assertTrue(os.path.isdir(os.path.join(self.bdir(), "entries", "002-t2-holttest2", "attempt-1")))
        self.assertEqual(st["003-t2-stictest2-rel6"]["status"], "PASS")
        self.assertGreater(st["003-t2-stictest2-rel6"]["lock_wait_s"], 0.5)
        self.assertEqual(st["002-t2-holttest2"].get("lock_wait_s"), 0.0)   # (the batch's own waits never count)

    def test_term_resume_adopt_and_two_drivers(self):
        mark = os.path.join(self.tmp, "quick.mark")
        self.set_conf({"HoltTest2": {"sleep": 5, "ignore_int": True}, "SticTest2": {"sleep": 30},
                       "resume": {"sleep": 0.3}})
        f = self.file(f"cmd quick -- sh -c 'echo x >> {mark}'\nt2 HoltTest2\nt2 SticTest2 rel6\n"
                      "cmd long -- sleep 30\ncmd later after=004-cmd-long -- true\n")
        p = self.start(f, "--dirty-ok")
        self.wait_for(lambda: self.state()["001-cmd-quick"]["status"] == "PASS"
                      and self.state()["002-t2-holttest2"].get("run")
                      and self.state()["003-t2-stictest2-rel6"].get("run")
                      and self.state()["004-cmd-long"]["status"] == "running", what="everything in flight")
        bid = os.path.basename(self.bdir())
        # the beacon: every entry in flight, with its hosts; refreshed
        with open(os.path.join(self.root, "batches", ".running")) as fh:
            b1 = json.load(fh)
        self.assertEqual(b1["batch"], bid)
        inflight = {r["entry"]: r["hosts"] for r in b1["running"]}
        self.assertEqual(set(inflight), {"002-t2-holttest2", "003-t2-stictest2-rel6", "004-cmd-long"})
        self.assertEqual(inflight["002-t2-holttest2"], ["chopin", "cinqcent", "copland", "cage"])
        self.wait_for(lambda: json.loads(rd(os.path.join(self.root, "batches", ".running")))["t"] != b1["t"],
                      what="a beacon refresh")
        st_line = self.t2("status")
        self.assertIn(f"batch {bid} (3 of 5 in flight", st_line.stdout)
        # a second driver is refused, naming the first
        r2 = self.t2("resume", bid)
        self.assertEqual(r2.returncode, 2)
        self.assertIn(f"another batch driver runs: batch {bid}, pid {p.pid}", r2.stderr)
        # TERM: no more starts, one signal to each child, exit 130
        p.send_signal(signal.SIGTERM)
        out, errs = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 130, out + errs)
        self.assertFalse(os.path.exists(os.path.join(self.root, "batches", ".running")))
        st = self.state()
        self.assertEqual(st["004-cmd-long"]["status"], "INCOMPLETE")
        self.assertEqual(st["003-t2-stictest2-rel6"]["status"], "interrupted")
        self.assertEqual(st["002-t2-holttest2"]["status"], "running")      # it ignored the interrupt: still runs
        self.assertEqual(st["005-cmd-later"]["status"], "pending")
        self.assertIn(f"INT {st['003-t2-stictest2-rel6']['run']}", self.calls())
        # resume: adopt the live child, `t2 resume RUN` the interrupted one, never re-run what ended
        r = self.t2("resume", bid, timeout=90)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)               # (004 is INCOMPLETE)
        st = self.state()
        self.assertEqual({k: v["status"] for k, v in st.items()},
                         {"001-cmd-quick": "PASS", "002-t2-holttest2": "PASS", "003-t2-stictest2-rel6": "PASS",
                          "004-cmd-long": "INCOMPLETE", "005-cmd-later": "PASS"})
        calls = self.calls()
        self.assertEqual(len([c for c in calls if c.startswith("START HoltTest2")]), 1)
        self.assertEqual(len([c for c in calls if c.startswith("START SticTest2")]), 1)
        self.assertIn(f"RESUME {st['003-t2-stictest2-rel6']['run']}", calls)
        self.assertNotIn(f"RESUME {st['002-t2-holttest2']['run']}", calls)
        with open(mark) as fh:
            self.assertEqual(fh.read(), "x\n")
        self.assertIn("adopted its child", self.blog())

    def test_edited_file_and_reparse(self):
        mark = os.path.join(self.tmp, "a.mark")
        text = f"cmd a -- sh -c 'echo 1 >> {mark}'\ncmd long -- sleep 30\n"
        f = self.file(text)
        p = self.start(f, "--dirty-ok")
        self.wait_for(lambda: self.state()["001-cmd-a"]["status"] == "PASS"
                      and self.state()["002-cmd-long"]["status"] == "running", what="a done, long running")
        p.send_signal(signal.SIGINT)
        p.communicate(timeout=30)
        self.assertEqual(p.returncode, 130)
        self.file(f"cmd a -- sh -c 'echo 2 >> {mark}'\ncmd long -- sleep 30\ncmd new -- true\n")
        r = self.t2(f, "--dirty-ok")
        self.assertEqual(r.returncode, 2)
        self.assertIn("changed since batch", r.stderr)
        self.assertIn(":1 was", r.stderr)
        r = self.t2(f, "--dirty-ok", "--reparse", timeout=60)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("continuing", r.stdout)
        st = self.state()
        self.assertEqual({k: v["status"] for k, v in st.items()},
                         {"001-cmd-a": "PASS", "002-cmd-long": "INCOMPLETE", "003-cmd-new": "PASS"})
        with open(mark) as fh:
            self.assertEqual(fh.read(), "1\n2\n")
        self.assertTrue(os.listdir(os.path.join(self.bdir(), "entries", ".dropped")))

    # --- the scheduler, the live join, sleep tolerance, the keeper ---------------------------------

    def real_ceilings(self):
        with open(self.facts, "w") as f:
            json.dump({"chopin": [23, 23], "cinqcent": [23, 23], "copland": [9, 9.2], "cage": [9, 9.2]}, f)

    def test_split_matches_plan(self):
        """Free hosts get work: SticTest2 on the small hosts (one of its seven sets) beside a long
        claim on the big ones, as the plan said."""
        self.real_ceilings()
        self.set_conf({"SticTest2": {"sleep": 0.3}})
        f = self.file("cmd hog hosts=chopin,cinqcent est=30m -- sleep 2\nt2 SticTest2 rel6 est=1m\n")
        plan = self.t2(f, "plan", "--json")
        self.assertEqual(plan.returncode, 0, plan.stderr)
        doc = json.loads(plan.stdout)
        rows = {r["id"]: r for r in doc["schedule"]["rows"]}
        self.assertEqual(rows["002-t2-stictest2-rel6"]["hosts"], ["copland", "cage"])
        self.assertEqual(rows["002-t2-stictest2-rel6"]["start"], rows["001-cmd-hog"]["start"])
        self.assertEqual(doc["capacity"]["cage"]["ceiling"], [9, 9.2])
        text = self.t2(f, "plan")
        self.assertIn("finish  +", text.stdout)
        self.assertRegex(text.stdout, r"002-t2-stictest2-rel6 +copland cage ")
        r = self.t2(f, "--dirty-ok", timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        start = [c for c in self.calls() if c.startswith("START SticTest2")]
        self.assertEqual(len(start), 1)
        self.assertIn(" copland,cage UNKNOWN_MAX", start[0])
        st = self.state()
        self.assertLess(st["002-t2-stictest2-rel6"]["started"], st["001-cmd-hog"]["ended"])
        with open(os.path.join(self.bdir(), "batch.json")) as fh:
            bj = json.load(fh)
        self.assertEqual(bj["ceilings"]["copland"], [9, 9.2])
        self.assertEqual({p["id"] for p in bj["profiles"]}, {"001-cmd-hog", "002-t2-stictest2-rel6"})
        with open(os.path.join(self.bdir(), "schedule.txt")) as fh:
            self.assertIn("finish", fh.read())

    def test_live_join(self):
        """Shares that don't fit copland keep SticTest2 off it in the plan; at run time pick-host's
        probe (a stub) shows an empty line, room and low load, and it joins live."""
        self.real_ceilings()
        self.set_conf({"HoltTest2": {"sleep": 2}, "SticTest2": {"sleep": 0.3}})
        # (--holt-bin: a HoltTest2 that builds needs a big build host, which copland isn't)
        f = self.file("t2 HoltTest2 --holt-bin=/x hosts=copland est=10m\nt2 SticTest2 rel6 hosts=copland est=5m\n")
        rows = {r["id"]: r for r in json.loads(self.t2(f, "plan", "--json").stdout)["schedule"]["rows"]}
        self.assertEqual(rows["002-t2-stictest2-rel6"]["start"], rows["001-t2-holttest2-holt-bin-x"]["end"])
        self.assertIn("shares", rows["002-t2-stictest2-rel6"]["waits"])
        live = os.path.join(self.tmp, "live.json")
        with open(live, "w") as fh:
            json.dump({"copland": {"ok": True, "waiting": 0, "line_room": [6, 9], "free": [6, 9], "load": 0.5,
                                   "nproc": 8}}, fh)
        self.env["T2_BATCH_LIVE"] = live
        r = self.t2(f, "--dirty-ok", timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        st = self.state()
        self.assertLess(st["002-t2-stictest2-rel6"]["started"], st["001-t2-holttest2-holt-bin-x"]["ended"])
        self.assertIn("live join on copland", self.blog())

    def test_mac_away_counts_no_lock_wait(self):
        """The lid rehearsal without a lid: the driver stopped (SIGSTOP) for 3 s while a session holds
        the lock its entry waits for; on SIGCONT it says the Mac was away and counts none of it."""
        self.set_conf({"SticTest2": {"sleep": 0.3}})
        self.env["T2_BATCH_AWAY_S"] = "1"
        f = self.file("cmd first -- sleep 0.3\nt2 SticTest2 rel6 after=001-cmd-first\n")
        p = self.start(f, "--dirty-ok")
        bl = lambda: os.path.join(self.bdir(), "batch.log")   # noqa: E731
        self.wait_for(lambda: "001-cmd-first: start on" in rd(bl()), what="the first start")
        session = runmod.FamilyLock("stic", self.root).acquire("t2s-20261115T0000-cafe")
        try:
            self.wait_for(lambda: "waits: lock stic: session" in rd(bl()), what="the session wait")
            os.kill(p.pid, signal.SIGSTOP)
            time.sleep(3)
            os.kill(p.pid, signal.SIGCONT)
            self.wait_for(lambda: "the Mac was away" in rd(bl()), what="the away line")
            time.sleep(0.5)
        finally:
            session.release()
        out, errs = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 0, out + errs)
        st = self.state()
        self.assertEqual(st["002-t2-stictest2-rel6"]["status"], "PASS")
        self.assertGreater(st["002-t2-stictest2-rel6"]["lock_wait_s"], 0.2)
        self.assertLess(st["002-t2-stictest2-rel6"]["lock_wait_s"], 2.5)

    def test_keeper_resumes_a_dead_driver(self):
        mark = os.path.join(self.tmp, "slow.mark")
        f = self.file(f"cmd slow rerun=yes -- sh -c 'echo x >> {mark}; sleep 1.5'\ncmd b after=001-cmd-slow -- true\n")
        p = self.start(f, "--dirty-ok")
        self.wait_for(lambda: self.state()["001-cmd-slow"]["status"] == "running", what="slow running")
        p.send_signal(signal.SIGTERM)
        p.communicate(timeout=30)
        self.assertEqual(p.returncode, 130)
        bid = os.path.basename(self.bdir())
        env = dict(self.env, T2_BATCH_KEEPER_SLEEP="0.2")
        k = subprocess.run([os.path.join(T2_DIR, "batch-keeper.sh"), bid], env=env, capture_output=True, text=True,
                           timeout=90, stdin=subprocess.DEVNULL)
        self.assertEqual(k.returncode, 0, k.stdout + k.stderr)
        self.assertIn(f"{bid} has finished (exit 0)", k.stderr)
        st = self.state()
        self.assertEqual({x: v["status"] for x, v in st.items()}, {"001-cmd-slow": "PASS", "002-cmd-b": "PASS"})
        with open(mark) as fh:
            self.assertEqual(fh.read(), "x\nx\n")                 # rerun=yes: once more, after the TERM
        self.assertTrue(os.path.exists(os.path.join(self.bdir(), "keeper.log")))
        k2 = subprocess.run([os.path.join(T2_DIR, "batch-keeper.sh"), "bt-nope"], env=env, capture_output=True,
                            text=True, timeout=30)
        self.assertEqual(k2.returncode, 2)


if __name__ == "__main__":
    unittest.main()


FIX = os.path.join(HERE, "fixtures", "batch")


class Kinds(Base):
    """ht1, stic1, bs2 and cmd entries placed by the scheduler and driven to the end through the
    stub tools (tests/fixtures/batch), the fake ssh running each "host" command here; then the
    output of §5: summary.tsv, the fetched logs, report.txt, ANALYSE.md, `t2 batch log`."""

    def setUp(self):
        super().setUp()
        self.env.update(T2_SSH=os.path.join(FIX, "fake-ssh"), T2_BATCH_HT1_ROOT=os.path.join(self.tmp, "ht1"),
                        T2_BATCH_BS2_ROOT=os.path.join(self.tmp, "bs2"),
                        T2_BATCH_HT1_FARM=os.path.join(FIX, "stub-ht1-farm.sh"),
                        T2_BATCH_DRIVE=f"{sys.executable} -B {os.path.join(FIX, 'stub-drive.py')}",
                        T2_BATCH_BS2=f"{sys.executable} -B {os.path.join(FIX, 'stub-bs2.py')}",
                        T2_BATCH_BS2_HOST=f"bash {os.path.join(FIX, 'bs2-host')}", T2_BATCH_KIND_POLL="0.2",
                        T2_BATCH_PULL="0", FAKE_JOB_S="0.2", FAKE_SSH_LOG=os.path.join(self.tmp, "ssh.log"),
                        T2_BATCH_FETCH_TRIES="1")

    def tearDown(self):
        for q in ("ht1", "bs2"):
            for dd, _ds, fs in os.walk(os.path.join(self.tmp, q)):
                if "todo" in fs or "queue.txt" in fs:
                    open(os.path.join(dd, "stop"), "w").close()
        super().tearDown()

    def test_every_kind_and_the_output(self):
        recs = os.path.join(self.tmp, "host-records", "holt-b")
        os.makedirs(recs)
        with open(os.path.join(recs, "run.out"), "w") as f:
            f.write("".join(f"verify line {i}\n" for i in range(300)))
        self.set_conf({"HoltTest2": {"sleep": 0.3, "exit": 1, "cells": {
            "holt:a": ["PASS", "", None], "holt:b": ["FAIL", "verify: 3 files differ", f"chopin:{recs}"],
            "holt:c": [None, "", None]}}})
        self.env.update(FAKE_FAIL="debian13", FAKE_STIC_FAIL="debian13")
        f = self.file("manual step 0: by hand\nt2 HoltTest2 hosts=chopin est=1m\nht1 h1a arch debian13 hosts=cage est=1m\n"
                      "stic1 rel6 alpine324 debian13 hosts=copland est=1m\nbs2 BackShot2Alpine 4 hosts=cinqcent est=1m\n"
                      "cmd ok -- true\n")
        r = self.t2(f, "--dirty-ok", timeout=120)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        st = self.state()
        self.assertEqual({k: v["status"] for k, v in st.items()},
                         {"002-t2-holttest2": "FAIL", "003-ht1-h1a-arch-debian13": "FAIL",
                          "004-stic1-rel6-alpine324-debian13": "FAIL", "005-bs2-backshot2alpine-4": "PASS",
                          "006-cmd-ok": "PASS"})
        d = self.bdir()
        tag = os.path.basename(d)[-4:]
        self.assertEqual(st["003-ht1-h1a-arch-debian13"]["run"], f"h1a-{tag}")       # the batch's own queue
        self.assertEqual(st["004-stic1-rel6-alpine324-debian13"]["run"], f"st1-rel6-{tag}")
        self.assertRegex(st["005-bs2-backshot2alpine-4"]["run"], r"^bs2a-")
        farm = [ln for ln in rd(os.path.join(self.root, "stub-calls.log")).splitlines() if " FARM " in ln]
        self.assertIn(f"FARM h1a-{tag} join=no hosts=cage q=cage arch debian13", farm[0])
        from t2lib import batchreport as br
        rows = br.parse_tsv(rd(os.path.join(d, "summary.tsv")))
        by = {(r["entry"][:3], r["cell"]): r for r in rows}
        self.assertEqual(by[("002", "holt:a")]["verdict"], "PASS")
        self.assertEqual(by[("002", "holt:b")]["since"], "new")
        self.assertEqual(by[("002", "holt:c")]["verdict"], "LOST")
        self.assertEqual(by[("003", "holt:arch")]["verdict"], "PASS")
        self.assertEqual((by[("003", "holt:debian13")]["verdict"], by[("003", "holt:debian13")]["host"]), ("FAIL", "cage"))
        self.assertEqual(by[("004", "stic:debian13")]["verdict"], "FAIL")
        self.assertEqual(by[("005", "-")]["verdict"], "PASS")
        self.assertEqual((by[("006", "-")]["class"], by[("006", "-")]["host"]), ("cmd", "mac"))
        log = by[("002", "holt:b")]["log"]
        self.assertEqual(log, "logs/002-t2-holttest2/holt-b")
        self.assertTrue(os.path.exists(os.path.join(d, log, "records", "run.out")))
        rep = rd(os.path.join(d, "report.txt"))
        self.assertIn("failing groups:", rep)
        self.assertIn("manual (not run): step 0: by hand", rep)
        self.assertIn(f"# Analysing batch {os.path.basename(d)}", rd(os.path.join(d, "ANALYSE.md")))
        lg = self.t2("log", os.path.basename(d), "002-t2-holttest2:holt:b", "--tail", "3")
        self.assertEqual(lg.returncode, 0, lg.stderr)
        self.assertIn("verify line 299", lg.stdout)
        rp = self.t2("report", os.path.basename(d))
        self.assertEqual(rp.stdout, rep)
        # the second batch of the file: the first is its baseline
        self.env.update(FAKE_FAIL="", FAKE_STIC_FAIL="")
        r = self.t2(f, "--dirty-ok", "--id", "second", timeout=120)
        self.assertIn(r.returncode, (0, 1), r.stdout + r.stderr)
        d2 = os.path.join(self.root, "batches", "second")
        self.assertEqual(rd(os.path.join(d2, "prev")).strip(), os.path.basename(d))
        rows2 = {(r["entry"][:3], r["cell"]): r for r in br.parse_tsv(rd(os.path.join(d2, "summary.tsv")))}
        self.assertEqual(rows2[("003", "holt:debian13")]["since"], "fixed")
        self.assertEqual(rows2[("002", "holt:b")]["since"], "known")

    def test_term_leaves_the_host_work_alone_and_resume_picks_it_up(self):
        self.env.update(FAKE_JOB_S="0.8")
        f = self.file("ht1 h1z a b c d e f hosts=cage est=1m\n")
        p = self.start(f, "--dirty-ok")
        self.wait_for(lambda: any(" FARM " in ln for ln in rd(os.path.join(self.root, "stub-calls.log")).splitlines()),
                      what="the farm started")
        d = self.bdir()
        e = os.path.join(d, "entries", "001-ht1-h1z-a-b-c-d-e-f")
        self.wait_for(lambda: os.path.exists(os.path.join(e, "inner")), what="the watcher's pid")
        os.killpg(p.pid, signal.SIGTERM)
        p.wait(20)
        self.assertEqual(p.returncode, 130)
        child = json.loads(rd(os.path.join(e, "child")))
        inner = int(rd(os.path.join(e, "inner")).strip())
        os.kill(inner, 0)                                    # the watcher was left alone: it still runs
        self.assertNotIn("SIGTERM sent", rd(os.path.join(e, "entry.log")))
        # its watcher dies too (a crash, not the batch): resume starts it again, joined back
        os.kill(inner, signal.SIGKILL)
        os.kill(child["pid"], signal.SIGKILL)
        time.sleep(0.3)
        r = self.t2("resume", os.path.basename(d), timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        farm = [ln for ln in rd(os.path.join(self.root, "stub-calls.log")).splitlines() if " FARM " in ln]
        self.assertEqual(len(farm), 2, farm)
        self.assertIn("join=yes hosts=cage q=cage", farm[1])
        self.assertEqual(self.state()["001-ht1-h1z-a-b-c-d-e-f"]["status"], "PASS")
        done = rd(os.path.join(e, "ht1-done.tsv")).splitlines()[1:]
        self.assertEqual(sorted({ln.split("\t")[2] for ln in done}), list("abcdef"))
