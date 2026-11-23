"""RAM first, the SSD for overflow (the user's choice, 2026-11-23; the host table's
scratch_mode and ssd_floor_gib).  What can run on the Mac:

- cmp-run.sh's ssd_room(), cut out of the script as facts_host.py cuts ceiling(): the SSD's
  free space above the floor, less what the --reserve-ssd rows hold; an older cmp-run.sh's
  rows and an empty ledger hold nothing; no room at all unless the host's mode is overflow or
  ssd; and --reserve-ssd's refusals;
- ht1-queue.sh's ssd_why() and place(), cut out the same way: which lines leave the RAM disk,
  and why (RAM first: only one that can't be in the RAM disk now; no size cutoff);
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
    """ht1-queue.sh's ssd_why D M and place D M VARIANT, with a cmp-run.sh that answers --ssd-free
    and --free as told ("after": the room a new --reserve would be offered now, DISK MEM)."""

    def cmp_run(self, ssd, after, free_rc):
        fake = os.path.join(self.t, "cmp-run.sh")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\ncase $1 in\n"
                    f"--ssd-free) {'echo ' + ssd if ssd is not None else 'exit 1'} ;;\n"
                    f"--free) echo '9.0 10.1'; echo 'waiting 0'; echo 'after {after}'; exit {free_rc} ;;\n"
                    "esac\n")
        os.chmod(fake, 0o755)
        return fake

    def why(self, d, m, mode="overflow", ssd="156.0", after="5.0 6.1", free_rc=0):
        fake = self.cmp_run(ssd, after, free_rc)
        cmd = ('eval "$(sed -n "/^ssd_why()/,/^}/p; /^ge()/p" "$1")"; NUM_RE="^[0-9]+([.][0-9]+)?$"; '
               'CMP_RUN=$2 MODE=$3; ssd_why "$4" "$5"')
        p = subprocess.run([BASH, "-c", cmd, "-", QUEUE, fake, mode, str(d), str(m)], capture_output=True,
                           text=True, env=self.env, timeout=60)
        self.assertEqual(p.stderr, "")
        return p.returncode, p.stdout.strip()

    def place(self, d, m, variant="", mode="overflow", ceil="29.0 23.2", ssd="184.0", after="29.0 23.2", free_rc=0,
              ram=1, disk_over=None):
        """place's answer: "ram", "ssd WHY" or "none ROOM".  DISK_OVER as the runner sets it: 1 in a
        mode other than ram, else as given (the host's holt_disk_mode)."""
        fake = self.cmp_run(ssd, after, free_rc)
        over = (0 if mode == "ram" else 1) if disk_over is None else disk_over
        cmd = ('eval "$(sed -n "/^place()/,/^}/p; /^ssd_why()/,/^}/p; /^ge()/p" "$1")"; NUM_RE="^[0-9]+([.][0-9]+)?$"; '
               'CMP_RUN=$2 MODE=$3 CEIL=$4 RAM=$5 DISK_OVER=$6; place "$7" "$8" "$9"')
        p = subprocess.run([BASH, "-c", cmd, "-", QUEUE, fake, mode, ceil, str(ram), str(over), str(d), str(m), variant],
                           capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual((p.stderr, p.returncode), ("", 0))
        return p.stdout.strip()

    def test_overflow(self):
        self.assertEqual(self.why(4, 4), (1, ""), "it fits the RAM disk now: in RAM")
        self.assertEqual(self.why(5, 4), (1, ""))
        self.assertEqual(self.why(6, 4), (0, "overflow: the RAM disk has room for 5.0 GiB now, not its 6"))
        self.assertEqual(self.why(6, 7), (1, ""), "its memory is what it would wait for: no use leaving the RAM disk")
        self.assertEqual(self.why(13, 4, after="20.0 9.0"), (1, ""), "over 12 GiB, and room: in RAM (no size cutoff)")
        self.assertEqual(self.why(27, 4, after="29.0 9.0"), (1, ""), "truenas25 on a big host with an empty RAM disk: in RAM")
        self.assertEqual(self.why(27, 4, after="26.9 9.0"),
                         (0, "overflow: the RAM disk has room for 26.9 GiB now, not its 27"))
        self.assertEqual(self.why(13, 4), (0, "overflow: the RAM disk has room for 5.0 GiB now, not its 13"))
        self.assertEqual(self.why(6, 4, free_rc=1), (1, ""), "no answer from --free: in RAM")
        self.assertEqual(self.why(6, 4, after="x y"), (1, ""))

    def test_ram_first(self):
        """The user's rule (2026-11-23): in the RAM disk whenever its D fits there right now (and
        its memory fits); on the SSD only when it doesn't: the RAM disk is full, or its D is
        over the host's RAM-disk ceiling and can never fit.  A big host (ceiling 29.0 23.2) with
        an empty RAM disk, then one that truenas25 holds, then a small host (9.0 10.1)."""
        # t2h-20261123T1713-3cd9's lines on chopin and cinqcent, which the cutoff of 12 sent to the SSD
        for gv, d in (("truenas25", 27), ("pop24", 20), ("pikaos4-luks", 16), ("mint22+bigger", 16),
                      ("solus49", 16), ("neon24", 15), ("debian13", 4)):
            self.assertEqual(self.place(d, 4), "ram", f"{gv}: D {d} fits the empty RAM disk")
        self.assertEqual(self.place(30, 4), "ssd DISK_OVER: over the RAM-disk ceiling 29.0 23.2", "vanilla3: never fits")
        # the RAM disk full (truenas25's 27 of 29 held): what doesn't fit now overflows, what does stays
        self.assertEqual(self.place(15, 4, after="2.0 19.2"),
                         "ssd overflow: the RAM disk has room for 2.0 GiB now, not its 15")
        self.assertEqual(self.place(3, 4, after="2.0 19.2"),
                         "ssd overflow: the RAM disk has room for 2.0 GiB now, not its 3")
        self.assertEqual(self.place(2, 4, after="2.0 19.2"), "ram")
        self.assertEqual(self.place(15, 4, after="0.0 19.2"),
                         "ssd overflow: the RAM disk has room for 0.0 GiB now, not its 15")
        self.assertEqual(self.place(15, 4, after="2.0 3.9"), "ram", "its memory is what it waits for: no use leaving RAM")
        # a small host: over its ceiling of 9.0 is the SSD's, under it the RAM disk's while there is room
        small = dict(ceil="9.0 10.1", ssd="156.0")
        self.assertEqual(self.place(17, 4, after="9.0 10.1", **small), "ssd DISK_OVER: over the RAM-disk ceiling 9.0 10.1")
        self.assertEqual(self.place(9, 4, after="9.0 10.1", **small), "ram")
        self.assertEqual(self.place(9.1, 4, after="9.0 10.1", **small), "ssd DISK_OVER: over the RAM-disk ceiling 9.0 10.1")
        self.assertEqual(self.place(6, 4, after="5.0 6.1", **small),
                         "ssd overflow: the RAM disk has room for 5.0 GiB now, not its 6")

    def test_place_keeps_the_rest(self):
        """+img only in the RAM disk; a host short of free SSD; the modes ssd and ram; HT_RAM=0."""
        self.assertEqual(self.place(30, 4, "img"), "ram", "+img: the RAM disk's (cant_why gives one over the ceiling back)")
        self.assertEqual(self.place(15, 4, "img", after="2.0 19.2"), "ram")
        self.assertEqual(self.place(15, 4, "bigger", after="2.0 19.2"),
                         "ssd overflow: the RAM disk has room for 2.0 GiB now, not its 15")
        # over the ceiling with no room for its D on the SSD now: nowhere here
        self.assertEqual(self.place(17, 4, ceil="9.0 10.1", ssd="15.0"), "none 15.0")
        self.assertEqual(self.place(17, 4, ceil="9.0 10.1", ssd=None), "none no")
        self.assertEqual(self.place(6, 4, ceil="9.0 10.1", ssd="5.0", after="5.0 6.1"), "ram", "no room on the SSD: it waits in RAM")
        self.assertEqual(self.place(2, 4, mode="ssd"), "ssd scratch_mode ssd")
        self.assertEqual(self.place(2, 4, "img", mode="ssd"), "ram")
        self.assertEqual(self.place(30, 4, mode="ssd"), "ssd DISK_OVER: over the RAM-disk ceiling 29.0 23.2")
        self.assertEqual(self.place(15, 4, mode="ram", after="2.0 19.2"), "ram", "mode ram: it waits for its room")
        self.assertEqual(self.place(30, 4, mode="ram"), "ram", "mode ram without holt_disk_mode: cant_why's")
        self.assertEqual(self.place(30, 4, mode="ram", disk_over=1, ssd="0.0"),
                         "ssd DISK_OVER: over the RAM-disk ceiling 29.0 23.2", "holt_disk_mode, as before 2026-11-23: --reserve 0 M")
        self.assertEqual(self.place(0, 4, mode="ram", ram=0), "ram", "HT_RAM=0: every line on /bkoscratch under --reserve 0 M")
        self.assertEqual(self.place(17, 4, ceil=""), "ram", "no ceiling known: the RAM disk's while there is room")

    def test_no_room_on_the_ssd_no_overflow(self):
        """cinqcent's fast disk at 98%, choubox's 15 GiB: a host short of free SSD keeps its lines in RAM."""
        self.assertEqual(self.why(6, 4, ssd="0.0"), (1, ""))
        self.assertEqual(self.why(27, 4, ssd="15.0"), (1, ""))
        self.assertEqual(self.why(13, 4, ssd="15.0"), (0, "overflow: the RAM disk has room for 5.0 GiB now, not its 13"))
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
        self.assertIn('((RAM)) && [[ $3 != img ]] || { echo ram; return 0; }', q, "never +img")
        self.assertIn('pl=$(place "$d" "$m" "$v")', q)
        self.assertIn('"ssd "*) ssd=$d pwhy=${pl#ssd } d=0 jram=0 ;;', q)
        self.assertNotIn("CUTOFF", q, "no size sends a line to the SSD")
        self.assertNotIn("ssd_cutoff_gib", q)
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
