"""RAM first, the SSD for overflow (the user's choice, 2026-11-23; the host table's
scratch_mode, ssd_cutoff_gib and ssd_floor_gib).  What can run on the Mac:

- cmp-run.sh's ssd_room(), cut out of the script as facts_host.py cuts ceiling(): the SSD's
  free space above the floor, less what the --reserve-ssd rows hold; an older cmp-run.sh's
  rows and an empty ledger hold nothing; no room at all unless the host's mode is overflow or
  ssd; and --reserve-ssd's refusals;
- ht1-queue.sh's ssd_why(), cut out the same way: which lines leave the RAM disk, and why;
- wear.py: a job whose wear line says scratch=ssd is left out of the write stop rule, whole.

The table's own values and the planner's placement are in test_host_limits.py and
test_small_hosts.py.  ht1-queue.sh's launch itself runs only on a test host (bash 4, flock)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
TESTVM = os.path.dirname(T2)
sys.path.insert(0, T2)

from t2lib import run, wear  # noqa: E402
from t2lib import hosts as H  # noqa: E402

CMP_RUN = os.path.join(TESTVM, "compare", "cmp-run.sh")
QUEUE = os.path.join(TESTVM, "holttest1d", "ht1-queue.sh")
IOLOG = os.path.join(TESTVM, "compare", "iolog.sh")
HOSTS_PY = os.path.join(T2, "t2lib", "hosts.py")
BASH = shutil.which("bash")
GIB_K = 1048576


@unittest.skipUnless(BASH, "no bash")
class Fakes(unittest.TestCase):
    """A bin/ of stand-ins first in PATH (hostname: cage; df: /bkoscratch 882 GiB with 236 free,
    anything else a 10 GiB tmpfs), a table of defaults with cage's row edited, HOME a temp dir."""

    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="t2-scratch-")
        self.addCleanup(shutil.rmtree, self.t, True)
        self.bin = os.path.join(self.t, "bin")
        os.makedirs(self.bin)
        os.makedirs(os.path.join(self.t, "backotter-work"))
        os.makedirs(os.path.join(self.t, "ledger"))
        self.fake("mountpoint", "exit 0\n")
        self.fake("hostname", "echo cage\n")
        self.df(236.0)
        self.env = dict(os.environ, HOME=self.t, PATH=self.bin + os.pathsep + os.environ["PATH"],
                        CMP_LEDGER_DIR=os.path.join(self.t, "ledger"), BKO_HOSTS_PY=HOSTS_PY)
        for k in ("BASE_GIB", "SSD_FLOOR_GIB", "SCRATCH_MODE", "CMP_SSD_DIR", "WEAR_SCRATCH"):
            self.env.pop(k, None)
        self.table(scratch_mode="overflow")

    def fake(self, name, text):
        with open(os.path.join(self.bin, name), "w") as f:
            f.write("#!/bin/sh\n" + text)
        os.chmod(os.path.join(self.bin, name), 0o755)

    def df(self, free_gib, size_gib=882.0):
        self.fake("df", "echo 'Filesystem 1024-blocks Used Available Capacity Mounted on'\n"
                        "for a; do last=$a; done\n"
                        f"case $last in /bkoscratch*) echo '/dev/nvme0n1p2 {int(size_gib * GIB_K)} 1 {int(free_gib * GIB_K)} 74% /' ;;\n"
                        "*) echo 'tmpfs 10485760 0 10485760 0% /mnt/bko-ram' ;; esac\n")

    def table(self, **cage):
        with open(os.path.join(TESTVM, "hosts.json")) as f:
            t = json.load(f)
        t["class_limits"] = {"big": {}, "small": {}}
        for r in t["hosts"]:
            for k in H.LIMITS:
                r.pop(k, None)
            if r["name"] == "cage":
                r.update(cage)
        p = os.path.join(self.t, "hosts.json")
        with open(p, "w") as f:
            json.dump(t, f)
        self.env["BKO_HOSTS_JSON"] = p


class SsdRoom(Fakes):
    def room(self, rows="", **env):
        cmd = 'eval "$(sed -n "/^ssd_room()/,/^}/p" "$1")" && SSD=/bkoscratch && ssd_room "$2"'
        p = subprocess.run([BASH, "-c", cmd, "-", CMP_RUN, rows], capture_output=True, text=True,
                           env=dict(self.env, **env), timeout=60)
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        a, b = p.stdout.split()
        return float(a), float(b)

    def test_free_space_above_the_floor(self):
        self.assertEqual(self.room(), (156.0, 802.0))          # 236 - 80; 882 - 80
        self.assertEqual(self.room(SSD_FLOOR_GIB="100"), (136.0, 782.0), "the environment's floor wins")
        self.table(scratch_mode="overflow", ssd_floor_gib=200.5)
        self.assertEqual(self.room(), (35.5, 681.5), "the host's own floor")
        self.df(95.0, 407.0)                                    # choubox's figures: 15 GiB of overflow
        self.table(scratch_mode="ssd")
        self.assertEqual(self.room(), (15.0, 327.0))
        self.df(60.0)
        self.assertEqual(self.room()[0], 0.0, "under the floor: no room, never a negative figure")

    def test_what_the_ssd_rows_hold(self):
        rows = ("101 0 4 - reserve ssd=27 sudo -E /x/ht1.sh truenas25 r1\n"
                "102 6 4 - reserve sudo -E /x/ht1.sh debian13 r1\n"                 # in the RAM disk
                "103 0 4 - reserve sudo -E /x/ht1.sh vanilla3 r1\n"                 # an older --reserve 0 M: none
                "104 4.7 3 - reserve bash -c echo ssd=99\n"                         # ssd= further on is CMD's
                "105 8 5 bko-u3-5-1.scope u3 mx25\n"                                # a queue job
                "106 0 4 - reserve ssd=12.5 sudo -E /x/ht1.sh mint22 r1\n"
                "107 0 4 - reserve ssd=lots true\n")                                # junk holds nothing
        self.assertEqual(self.room(rows), (116.5, 802.0))                           # 156 - 27 - 12.5
        self.assertEqual(self.room("101 0 4 - reserve ssd=200 x\n")[0], 0.0)

    def test_only_in_overflow_or_ssd(self):
        """A host put back to ram gets no SSD scratch from the ledger; nor one the table can't speak for."""
        self.table(scratch_mode="ram")
        self.assertEqual(self.room(), (0.0, 0.0))
        self.table()
        self.assertEqual(self.room(), (0.0, 0.0), "the default is ram")
        self.assertEqual(self.room(BKO_HOSTS_PY=os.path.join(self.t, "nothere.py")), (0.0, 0.0))
        self.assertEqual(self.room(SCRATCH_MODE="overflow"), (156.0, 802.0), "SCRATCH_MODE in the environment instead")
        self.table(scratch_mode="overflow")
        self.fake("df", "exit 1\n")
        self.assertEqual(self.room(), (0.0, 0.0), "no answer from df")

    def run_cmp(self, *args, **env):
        return subprocess.run([BASH, CMP_RUN] + list(args), capture_output=True, text=True, env=dict(self.env, **env), timeout=60)

    def test_ssd_free_writes_nothing(self):
        p = self.run_cmp("--ssd-free")
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "156.0\n", ""))
        self.assertEqual(os.listdir(os.path.join(self.t, "ledger")), [], "not even the ledger's files")
        self.assertEqual(self.run_cmp("--ssd-free", "x").returncode, 2)

    def test_reserve_ssd_refusals(self):
        p = self.run_cmp("--reserve-ssd", "900", "1", "true")
        self.assertEqual(p.returncode, 1)
        self.assertRegex(p.stderr, r"^--reserve-ssd 900 1 can never fit here \(at most 802\.0 of /bkoscratch")
        self.table(scratch_mode="ram")
        p = self.run_cmp("--reserve-ssd", "5", "1", "true")
        self.assertEqual(p.returncode, 1)
        self.assertIn("at most 0.0 of /bkoscratch", p.stderr)
        for bad in (["--reserve-ssd", "x", "1", "true"], ["--reserve-ssd", "5", "1"], ["--reserve-ssd", "5"]):
            p = self.run_cmp(*bad)
            self.assertEqual(p.returncode, 2, bad)
            self.assertIn("usage: cmp-run.sh --reserve-ssd SSD_GIB MEM_GIB CMD", p.stderr)

    def test_the_row_and_the_wear_word(self):
        """What can't run here (the ledger needs /proc and flock), read from the script: the row
        an SSD job takes, which every reader of the ledger can take, and scratch= for the wear log."""
        with open(CMP_RUN) as f:
            text = f.read()
        self.assertIn('add $LEDGER "$$ $d $m - reserve ${s:+ssd=$s }$*"', text)
        self.assertIn("s=$2 d=0 m=$3", text, "no RAM disk for a --reserve-ssd")
        self.assertIn('WEAR_SCRATCH="$ws" "$IOLOG"', text)
        with open(IOLOG) as f:
            io = f.read()
        self.assertIn("scratch=${WEAR_SCRATCH:-}; unset WEAR_SCRATCH", io, "the job's alone: CMD doesn't inherit it")
        self.assertIn('case $scratch in ram|ssd) x+=$\'\\t\'"scratch=$scratch" ;; esac', io)
        code = [l for l in text.split("\n") if not l.lstrip().startswith("#")]
        for h in H.names(all_=True):
            self.assertFalse([l for l in code if h in l], f"{h} named in cmp-run.sh's code: the table decides")


class SsdWhy(Fakes):
    """ht1-queue.sh's ssd_why D M, with a cmp-run.sh that answers --ssd-free and --free as told."""

    def why(self, d, m, mode="overflow", cutoff="12", ssd="156.0", after="5.0 6.1", free_rc=0):
        fake = os.path.join(self.t, "cmp-run.sh")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\ncase $1 in\n"
                    f"--ssd-free) {'echo ' + ssd if ssd is not None else 'exit 1'} ;;\n"
                    f"--free) echo '9.0 10.1'; echo 'waiting 0'; echo 'after {after}'; exit {free_rc} ;;\n"
                    "esac\n")
        os.chmod(fake, 0o755)
        cmd = ('eval "$(sed -n "/^ssd_why()/,/^}/p; /^ge()/p" "$1")"; NUM_RE="^[0-9]+([.][0-9]+)?$"; '
               'CMP_RUN=$2 MODE=$3 CUTOFF=$4; ssd_why "$5" "$6"')
        p = subprocess.run([BASH, "-c", cmd, "-", QUEUE, fake, mode, cutoff, str(d), str(m)], capture_output=True,
                           text=True, env=self.env, timeout=60)
        self.assertEqual(p.stderr, "")
        return p.returncode, p.stdout.strip()

    def test_overflow(self):
        self.assertEqual(self.why(4, 4), (1, ""), "it fits the RAM disk now: in RAM")
        self.assertEqual(self.why(5, 4), (1, ""))
        self.assertEqual(self.why(6, 4), (0, "overflow: the RAM disk has room for 5.0 GiB now, not its 6"))
        self.assertEqual(self.why(6, 7), (1, ""), "its memory is what it would wait for: no use leaving the RAM disk")
        self.assertEqual(self.why(13, 4), (0, "over ssd_cutoff_gib 12"))
        self.assertEqual(self.why(12, 4, after="20.0 9.0"), (1, ""), "at the cutoff, and room: in RAM")
        self.assertEqual(self.why(27, 4, after="29.0 9.0"), (0, "over ssd_cutoff_gib 12"),
                         "truenas25 on a big host: off the RAM disk though it would fit")
        self.assertEqual(self.why(8, 4, cutoff="6"), (0, "over ssd_cutoff_gib 6"), "the host's own cutoff")
        self.assertEqual(self.why(6, 4, free_rc=1), (1, ""), "no answer from --free: in RAM")
        self.assertEqual(self.why(6, 4, after="x y"), (1, ""))

    def test_no_room_on_the_ssd_no_overflow(self):
        """cinqcent's fast disk at 98%, choubox's 15 GiB: a host short of free SSD keeps its lines in RAM."""
        self.assertEqual(self.why(6, 4, ssd="0.0"), (1, ""))
        self.assertEqual(self.why(27, 4, ssd="15.0"), (1, ""))
        self.assertEqual(self.why(13, 4, ssd="15.0"), (0, "over ssd_cutoff_gib 12"))
        self.assertEqual(self.why(6, 4, ssd=None), (1, ""), "no answer from --ssd-free")
        self.assertEqual(self.why(6, 4, mode="ssd", ssd="5.0"), (1, ""))

    def test_ssd_and_ram(self):
        self.assertEqual(self.why(2, 4, mode="ssd"), (0, "scratch_mode ssd"))
        self.assertEqual(self.why(27, 4, mode="ram"), (1, ""))
        self.assertEqual(self.why(6, 4, mode="ram"), (1, ""))

    def test_the_runner_reads_its_mode_from_the_table(self):
        with open(QUEUE) as f:
            q = f.read()
        self.assertIn('MODE=${SCRATCH_MODE:-$(python3 -I -B "$HOSTS_PY" limit "$H" scratch_mode 2>/dev/null)}', q)
        self.assertIn("[[ $MODE == ram ]] || DISK_OVER=1", q)
        self.assertIn('res=(--reserve "$d" "$m"); [[ $MODE == ram || -z $ssd ]] || res=(--reserve-ssd "$ssd" "$m")', q)
        self.assertIn("! grep -q -- '--reserve-ssd' \"$CMP_RUN\"", q, "an older shared cmp-run.sh: mode ram")
        self.assertIn('elif ((RAM)) && [[ $MODE != ram && $v != img ]] && pwhy=$(ssd_why "$d" "$m"); then', q, "never +img")
        code = [l for l in q.split("\n") if not l.lstrip().startswith("#")]
        for h in ("chopin", "cinqcent", "copland", "cage", "czerny", "choubox"):
            self.assertFalse([l for l in code if h in l], f"{h} named in ht1-queue.sh's code")


class Wear(unittest.TestCase):
    """A job whose scratch was on the SSD wrote all of its bytes on purpose: the write stop rule
    leaves them out (wear.Job.kept_bytes, as a failed run's kept state), the totals keep them."""

    LOG = ("2026-11-24T08:00:00Z\tchopin\trun-u1.scope\t0\t900\tnvme0n1=27000000000,loop3=26000000000,zram0=5\t"
           "reserve ssd=27 sudo -E ht1.sh truenas25 r\ttag=ht1:t2h-x:truenas25\tscratch=ssd\tserial=nvme0n1:NOT-A-REAL-SERIAL\tmpeak=1\n"
           "2026-11-24T08:10:00Z\tchopin\trun-u2.scope\t0\t300\tnvme0n1=30000000\t"
           "reserve sudo -E ht1.sh debian13 r\ttag=ht1:t2h-x:debian13\tscratch=ram\tmpeak=1\n"
           "2026-11-24T08:20:00Z\tchopin\trun-u3.scope\t1\t300\tnvme0n1=4000000000\t"
           "reserve sudo -E ht1.sh uc24 r\ttag=ht1:t2h-x:uc24\tscratch=ram\tkeep=nvme0n1=3900000000\n"
           "2026-11-24T08:30:00Z\tchopin\trun-u4.scope\t0\t300\tnvme0n1=50000000\treserve sudo -E ht1.sh arch r\ttag=ht1:t2h-x:arch\n")

    def test_scratch_is_read_and_ssd_bytes_are_excused(self):
        jobs = wear.parse_jobs(self.LOG)
        self.assertEqual([j.x.get("scratch") for j in jobs], ["ssd", "ram", "ram", None])
        self.assertEqual([j.physical_bytes() for j in jobs], [27000000000, 30000000, 4000000000, 50000000])
        self.assertEqual([j.kept_bytes() for j in jobs], [27000000000, 0, 3900000000, 0])
        self.assertEqual(jobs[0].kept_disks(), {"nvme0n1": 27000000000}, "its physical disks only")

    def test_the_stop_rule_counts_the_rest(self):
        """31.08 GB written on the host, 30.9 of it SSD scratch and kept state: 0.18 GB counted, under 4."""
        jobs = wear.parse_jobs(self.LOG)
        total, kept = sum(j.physical_bytes() for j in jobs), sum(j.kept_bytes() for j in jobs)
        pol = {"budget": {"run_gb": 2}, "stop": {"write_budget_x": 2}}
        s = run.StopEngine("holt", pol)
        self.assertEqual(s.feed_host("chopin", ours_bytes=total, kept=kept), "ok")
        s = run.StopEngine("holt", pol)
        self.assertEqual(s.feed_host("chopin", ours_bytes=total), "stop-starts", "without the excuse it would stop the run")


if __name__ == "__main__":
    unittest.main()
