"""The other kinds of batch entry (docs/design/batch.md §3.1, §4.5 growth, §6.2; piece B6), with no
host: t2lib/batchkinds.py's ht1, stic1 and bs2 watchers over stub tools (tests/fixtures/batch:
stub-ht1-farm.sh and its fake runners, stub-drive.py, stub-bs2.py and its fake bs2-host queue)
and a fake ssh that runs each "host" command here; and ht1-farm.sh's --join, through a fake ssh
that answers its few commands and runs none of them."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
TESTVM = os.path.dirname(T2)
FIX = os.path.join(HERE, "fixtures", "batch")
KINDS = os.path.join(T2, "t2lib", "batchkinds.py")
sys.path.insert(0, T2)


def rd(p):
    with open(p) as f:
        return f.read()


class Kinds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t2kinds-")
        self.root = os.path.join(self.tmp, "mac")
        os.makedirs(self.root)
        self.edir = os.path.join(self.tmp, "entry")
        os.makedirs(self.edir)
        tbl = os.path.join(self.tmp, "hosts.json")
        shutil.copy(os.path.join(TESTVM, "hosts.json"), tbl)
        self.env = dict(os.environ, T2_MAC_ROOT=self.root, BKO_HOSTS_JSON=tbl, T2_SSH=os.path.join(FIX, "fake-ssh"),
                        FAKE_SSH_LOG=os.path.join(self.tmp, "ssh.log"), PYTHONDONTWRITEBYTECODE="1",
                        T2_BATCH_HT1_ROOT=os.path.join(self.tmp, "ht1"), T2_BATCH_BS2_ROOT=os.path.join(self.tmp, "bs2"),
                        T2_BATCH_HT1_FARM=os.path.join(FIX, "stub-ht1-farm.sh"),
                        T2_BATCH_DRIVE=f"{sys.executable} -B {os.path.join(FIX, 'stub-drive.py')}",
                        T2_BATCH_BS2=f"{sys.executable} -B {os.path.join(FIX, 'stub-bs2.py')}",
                        T2_BATCH_BS2_HOST=f"bash {os.path.join(FIX, 'bs2-host')}",
                        T2_BATCH_KIND_POLL="0.2", T2_BATCH_PATIENCE_S="3", FAKE_JOB_S="0.2")
        for k in ("FAKE_FAIL", "FAKE_SSH_DOWN", "FAKE_DRIVE_BUSY", "FAKE_STIC_FAIL", "FAKE_BS2_FAIL", "FAKE_FARM_RC"):
            self.env.pop(k, None)
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
                p.wait(10)
        for q in ("ht1", "bs2"):       # stop the fake runners still going (their stop files)
            for dd, _ds, fs in os.walk(os.path.join(self.tmp, q)):
                if "todo" in fs or "queue.txt" in fs:
                    open(os.path.join(dd, "stop"), "w").close()
        time.sleep(0.5)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def place(self, hosts, grows=()):
        with open(os.path.join(self.edir, "placed"), "w") as f:
            f.write(json.dumps({"hosts": hosts}) + "\n")
            for g in grows:
                f.write("grow " + json.dumps({"host": g}) + "\n")

    def grow(self, h):
        with open(os.path.join(self.edir, "placed"), "a") as f:
            f.write("grow " + json.dumps({"host": h}) + "\n")

    def kinds(self, *args, wait=True, **env):
        e = dict(self.env, **env)
        p = subprocess.Popen([sys.executable, "-B", KINDS] + list(args), env=e, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, stdin=subprocess.DEVNULL, cwd=self.tmp)
        self.procs.append(p)
        if not wait:
            return p
        out, _ = p.communicate(timeout=60)
        return p.returncode, out

    def calls(self, what):
        try:
            return [ln for ln in rd(os.path.join(self.root, "stub-calls.log")).splitlines() if f" {what} " in ln]
        except OSError:
            return []

    # -- ht1
    def test_ht1_runs_the_farm_and_reads_its_done_rows(self):
        self.place(["copland", "cage"])
        rc, out = self.kinds("ht1", self.edir, "--run", "h1a-9ab1", "--queue-host", "copland", "--",
                             "arch", "alpine324+bigger=3", "debian13", FAKE_FAIL="debian13")
        self.assertEqual(rc, 1, out)
        farm = self.calls("FARM")
        self.assertEqual(len(farm), 1, farm)
        self.assertIn("h1a-9ab1 join=no hosts=copland,cage q=copland arch alpine324+bigger=3 debian13", farm[0])
        done = [ln.split("\t") for ln in rd(os.path.join(self.edir, "ht1-done.tsv")).splitlines()[1:]]
        self.assertEqual(sorted(r[2] for r in done), ["alpine324-bigger", "arch", "debian13"])   # each once
        self.assertEqual({r[2]: r[3] for r in done}["debian13"], "1")
        self.assertEqual(rd(os.path.join(self.edir, "ht1-left.txt")), "")
        k = json.loads(rd(os.path.join(self.edir, "kind.json")))
        self.assertEqual((k["run"], k["queue_host"], k["ended"]), ("h1a-9ab1", "copland", True))

    def test_ht1_passes(self):
        self.place(["cage"])
        rc, out = self.kinds("ht1", self.edir, "--run", "h1b-9ab1", "--queue-host", "cage", "--", "arch")
        self.assertEqual(rc, 0, out)

    def test_ht1_grows_onto_a_new_host(self):
        self.place(["cage"])
        p = self.kinds("ht1", self.edir, "--run", "h1c-9ab1", "--queue-host", "cage", "--", "a", "b", "c", "d", "e",
                       wait=False, FAKE_JOB_S="0.6")
        t0 = time.time()
        while not self.calls("FARM") and time.time() - t0 < 20:
            time.sleep(0.1)
        self.grow("copland")
        out, _ = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 0, out)
        farm = self.calls("FARM")
        self.assertEqual(len(farm), 2, farm)
        self.assertIn("h1c-9ab1 join=yes hosts=copland q=cage", farm[1])
        self.assertIn("grew onto copland", out)
        hosts = {ln.split("\t")[1] for ln in rd(os.path.join(self.edir, "ht1-done.tsv")).splitlines()[1:]}
        self.assertEqual(hosts, {"cage", "copland"})

    def test_ht1_resume_joins_the_runners_back(self):
        self.place(["cage"], grows=["copland"])
        q = os.path.join(self.tmp, "ht1", "queue", "h1d-9ab1")
        os.makedirs(q)
        with open(os.path.join(q, "todo"), "w") as f:
            f.write("arch\n")
        with open(os.path.join(self.edir, "kind.json"), "w") as f:
            json.dump({"run": "h1d-9ab1", "queue_host": "cage", "started": True, "joined": ["cage"]}, f)
        rc, out = self.kinds("ht1", self.edir, "--run", "h1d-9ab1", "--queue-host", "cage", "--resume", "--", "arch")
        self.assertEqual(rc, 0, out)
        farm = self.calls("FARM")
        self.assertEqual(len(farm), 1, farm)
        self.assertIn("h1d-9ab1 join=yes hosts=cage,copland q=cage", farm[0])   # the claim and its grow

    def test_ht1_refused_and_a_silent_host(self):
        self.place(["cage"])
        rc, out = self.kinds("ht1", self.edir, "--run", "h1e-9ab1", "--queue-host", "cage", "--", "arch",
                             FAKE_FARM_RC="2")
        self.assertEqual(rc, 2, out)
        self.place(["cage", "copland"])
        rc, out = self.kinds("ht1", self.edir, "--run", "h1f-9ab1", "--queue-host", "cage", "--", "arch",
                             FAKE_SSH_DOWN="m@copland.local")
        self.assertEqual(rc, 0, out)                     # copland silent past the patience: gone, not "ended"
        self.assertIn("no answer from copland past the patience", out)

    def test_no_real_tool_on_a_test_root(self):
        env = {k: "" for k in ("T2_BATCH_HT1_FARM", "T2_BATCH_DRIVE", "T2_BATCH_BS2")}
        self.place(["cage"])
        rc, out = self.kinds("ht1", self.edir, "--run", "h1g-9ab1", "--queue-host", "cage", "--", "arch", **env)
        self.assertEqual(rc, 2, out)
        self.assertIn("a test never does", out)
        rc, out = self.kinds("stic1", self.edir, "--", "true", **env)
        self.assertEqual(rc, 2, out)
        rc, out = self.kinds("bs2", self.edir, "--hosts", "cage", "--", "BackShot2Alpine", "2", **env)
        self.assertEqual(rc, 2, out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "ssh.log")))   # nothing reached even the fake ssh

    # -- stic1
    def drive(self, *extra):
        return [sys.executable, "-B", os.path.join(FIX, "stub-drive.py"), "--private-copy", "--hosts", "copland,cage",
                "--out", os.path.join(self.edir, "stic1"), "--logs-mark", "st1-rel6-9ab1", "rel6"] + list(extra)

    def test_stic1(self):
        rc, out = self.kinds("stic1", self.edir, "--", *self.drive("alpine324", "debian13"))
        self.assertEqual(rc, 0, out)
        rc, out = self.kinds("stic1", self.edir, "--", *self.drive("alpine324", "debian13"), FAKE_STIC_FAIL="debian13")
        self.assertEqual(rc, 1, out)
        self.assertIn("debian13\tcage\t1", rd(os.path.join(self.edir, "stic1", "results.tsv")))

    def test_stic1_waits_for_its_private_copy(self):
        busy = os.path.join(self.tmp, "busy-once")
        rc, out = self.kinds("stic1", self.edir, "--", *self.drive("alpine324"), FAKE_DRIVE_BUSY=busy)
        self.assertEqual(rc, 0, out)
        self.assertIn("--private-copy waits", out)
        self.assertEqual(len(self.calls("DRIVE")), 2)
        self.assertTrue(all("st1-rel6-9ab1" in c for c in self.calls("DRIVE")))   # the same mark again

    def test_stic1_no_results_is_an_error(self):
        rc, out = self.kinds("stic1", self.edir, "--", sys.executable, "-c", "import sys; sys.exit(1)")
        self.assertEqual(rc, 3, out)

    # -- bs2
    def test_bs2(self):
        rc, out = self.kinds("bs2", self.edir, "--hosts", "cage", "--", "BackShot2Alpine", "4")
        self.assertEqual(rc, 0, out)
        run = rd(os.path.join(self.edir, "run")).strip()
        self.assertRegex(run, r"^bs2a-")
        st = json.loads(rd(os.path.join(self.edir, "bs2-status.json")))
        self.assertEqual(st["verdicts"], {"PASS": 4})
        self.assertIn("BS2 BackShot2Alpine 4 cage", self.calls("BS2")[0])

    def test_bs2_fail_refused_and_resume(self):
        rc, out = self.kinds("bs2", self.edir, "--hosts", "cage", "--", "BackShot2Alpine", "4", FAKE_BS2_FAIL="1")
        self.assertEqual(rc, 1, out)
        run = rd(os.path.join(self.edir, "run")).strip()
        os.remove(os.path.join(self.edir, "kind.json"))
        os.remove(os.path.join(self.edir, "run"))
        rc, out = self.kinds("bs2", self.edir, "--hosts", "cage", "--", "BackShot2Alpine", "4", FAKE_BS2_REFUSE="1")
        self.assertEqual(rc, 2, out)
        # a resume whose queue stopped half way (nothing of it runs): restarted from queue.next
        rdir = os.path.join(self.tmp, "bs2", "runs", run)
        with open(os.path.join(rdir, "queue.next"), "w") as f:
            f.write("2\n")
        with open(os.path.join(rdir, "tier1.jsonl"), "w") as f:
            f.write('{"verdict": "PASS"}\n{"verdict": "PASS"}\n')
        with open(os.path.join(self.edir, "kind.json"), "w") as f:
            json.dump({"run": run, "hosts": ["cage"]}, f)
        rc, out = self.kinds("bs2", self.edir, "--hosts", "cage", "--resume", "--", "BackShot2Alpine", "4",
                             PATH=os.path.join(FIX, "bin") + os.pathsep + os.environ["PATH"])   # (setsid -f)
        self.assertEqual(rc, 0, out)
        self.assertIn("its queue had stopped at 2 of 4: restarted", out)
        # (with the word, as bs2 started it: without one its cases write no ledger rows)
        self.assertRegex(rd(os.path.join(self.tmp, "ssh.log")), r"setsid -f bash -c 'BS2_WORD=BackShot2Alpine ")   # (the log cuts a command at 300)
        self.assertEqual(json.loads(rd(os.path.join(self.edir, "bs2-status.json")))["verdicts"], {"PASS": 4})
        self.assertEqual(len(self.calls("BS2")), 2)          # the word itself not run again


class FarmJoin(unittest.TestCase):
    """ht1-farm.sh --join (the batch's growth and resume): no todo written, the queue host may be
    outside --hosts, a host whose runner of RUN still runs is left as it is.  Through a fake ssh
    that answers and runs nothing."""

    FARM = os.path.join(TESTVM, "holttest1d", "ht1-farm.sh")
    FAKE = r'''#!/usr/bin/env bash
set -u
args=() nostdin=0
while [[ $# -gt 0 ]]; do
    case $1 in
    -n) nostdin=1; shift ;;
    -o) shift 2 ;;
    *) args+=("$1"); shift ;;
    esac
done
((nostdin == 0)) || exec </dev/null
printf '%s\t%s\n' "${args[0]}" "${args[1]:-}" >>"$FAKE_LOG"
case ${args[1]:-} in
*"pgrep -f"*) [[ ",$FAKE_RUNNING," == *",${args[0]},"* ]] && echo running ;;
*"cat "*/todo*) printf "$FAKE_TODO" ;;
*"ht1-queue.sh --holt"*) echo "0123456789ab /home/m/backotter-work/holt-bin/linux/bko-holt" ;;
esac
exit 0
'''

    def farm(self, *args, running="", todo="debian13\\nalpine324\\n"):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "ssh")
            with open(p, "w") as f:
                f.write(self.FAKE)
            os.chmod(p, 0o755)
            env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"], FAKE_LOG=os.path.join(d, "log"),
                       FAKE_RUNNING=running, FAKE_TODO=todo)
            r = subprocess.run(["/bin/bash", self.FARM] + list(args), capture_output=True, text=True, timeout=120,
                               env=env, cwd=os.path.dirname(TESTVM))
            calls = [ln.split("\t", 1) for ln in rd(env["FAKE_LOG"]).splitlines()] if os.path.exists(env["FAKE_LOG"]) else []
        return r, calls

    def test_usage(self):
        for args, want in ((("r1", "--join", "--queue-host", "cage", "--hosts", "cage", "debian13"), "takes no guests"),
                           (("r1", "--join", "--hosts", "cage"), "--join needs --queue-host"),
                           (("r1", "--join", "--queue-host", "nosuch", "--hosts", "cage"), "no such host nosuch")):
            r, calls = self.farm(*args)
            self.assertEqual(r.returncode, 2, (args, r.stdout, r.stderr))
            self.assertIn(want, r.stderr)
            self.assertEqual(calls, [])

    def test_join_starts_runners_and_writes_no_todo(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("hosts_tbl", os.path.join(T2, "t2lib", "hosts.py"))
        H = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(H)
        r, calls = self.farm("r1", "--join", "--queue-host", "chopin", "--hosts", "copland,cage",
                             running=H.ssh("cage"))
        self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
        self.assertFalse([c for c in calls if "cat >" in c[1] or "--order" in c[1]], calls)   # no todo written
        self.assertIn("cage's runner of r1 still runs: left as it is", r.stdout)
        started = [c for c in calls if "setsid" in c[1]]
        self.assertEqual([c[0] for c in started], [H.ssh("copland")], calls)
        self.assertIn(f" r1 2600 {H.limit('copland', 'holt_jobs')} ", started[0][1] + " ")   # its holt_jobs: 2 since 2026-11-23
        self.assertEqual(H.limit("copland", "holt_jobs"), 2)
        self.assertIn(H.row("chopin").get("host_key_alias") or "chopin", started[0][1])   # the queue host's address
        r, calls = self.farm("r1", "--join", "--queue-host", "chopin", "--hosts", "cage", running=H.ssh("cage"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no runner to start", r.stdout)
        r, calls = self.farm("r1", "--join", "--queue-host", "chopin", "--hosts", "cage", todo="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("the todo is empty", r.stdout)
        self.assertFalse([c for c in calls if "setsid" in c[1]])


if __name__ == "__main__":
    unittest.main()
