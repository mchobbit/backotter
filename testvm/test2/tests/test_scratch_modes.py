"""RAM first, the SSD for overflow (the user's choice, 2026-11-23; the host table's
scratch_mode and ssd_floor_gib).  What can run on the Mac:

- cmp-run.sh's ssd_room(), cut out of the script as facts_host.py cuts ceiling(): the SSD's
  free space above the floor, less what the --reserve-ssd rows hold; an older cmp-run.sh's
  rows and an empty ledger hold nothing; no room at all unless the host's mode is overflow or
  ssd; and --reserve-ssd's refusals;
- ht1-queue.sh's place() and place_lines(), cut out the same way: which lines take a RAM slot,
  which an SSD slot and which wait (the slot model; RAM first, no size cutoff);
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


class Place(Fakes):
    """ht1-queue.sh's place D M VARIANT (and place_lines under it), with a cmp-run.sh that answers
    --ssd-free and --free as told ("after": the room a new --reserve would be offered now, DISK
    MEM).  The slot model (the user's rule, 2026-11-23 evening), in mode overflow: a line takes a
    RAM slot when one is free and its D fits the RAM disk right now; else an SSD slot when one is
    free and the SSD has room; else it waits.  RS and SS: a RAM slot, an SSD slot is free."""

    def cmp_run(self, ssd, after, free_rc):
        fake = os.path.join(self.t, "cmp-run.sh")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\ncase $1 in\n"
                    f"--ssd-free) {'echo ' + ssd if ssd is not None else 'exit 1'} ;;\n"
                    f"--free) echo '9.0 10.1'; echo 'waiting 0'; echo 'after {after}'; exit {free_rc} ;;\n"
                    "esac\n")
        os.chmod(fake, 0o755)
        return fake

    FUNCS = 'eval "$(sed -n "/^place()/,/^}/p; /^place_lines()/,/^}/p; /^ram_room()/,/^}/p" "$1")"; NUM_RE="^[0-9]+([.][0-9]+)?$"; '

    def place(self, d, m, variant="", mode="overflow", ceil="29.0 23.2", ssd="184.0", after="29.0 52.2", free_rc=0,
              ram=1, disk_over=None, rs=1, ss=1):
        """place's answer: "ram", "ssd WHY", "none ROOM" or "wait WHY".  DISK_OVER as the runner
        sets it: 1 in a mode other than ram, else as given (the host's holt_disk_mode)."""
        fake = self.cmp_run(ssd, after, free_rc)
        over = (0 if mode == "ram" else 1) if disk_over is None else disk_over
        cmd = self.FUNCS + 'CMP_RUN=$2 MODE=$3 CEIL=$4 RAM=$5 DISK_OVER=$6 RSFREE=$7 SSFREE=$8; place "$9" "${10}" "${11}"'
        p = subprocess.run([BASH, "-c", cmd, "-", QUEUE, fake, mode, ceil, str(ram), str(over), str(rs), str(ss),
                            str(d), str(m), variant], capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual((p.stderr, p.returncode), ("", 0))
        return p.stdout.strip()

    def lines(self, lines, room="29.0 52.2", ssd="184.0", ceil="29.0 23.2", rs=1, ss=1, inline=0, mode="overflow"):
        """place_lines over "D M IMG" lines, as cant_scan asks it (INLINE 0 while a job of ours is here)."""
        text = "".join(f"{k} {x}\n" for k, x in enumerate(lines))
        cmd = self.FUNCS + 'MODE=$2 CEIL=$3 RAM=1 DISK_OVER=1 RSFREE=$4 SSFREE=$5 RAMROOM=$6 SSDROOM=$7 INLINE=$8; place_lines'
        p = subprocess.run([BASH, "-c", cmd, "-", QUEUE, mode, ceil, str(rs), str(ss), room, ssd, str(inline)],
                           input=text, capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual((p.stderr, p.returncode), ("", 0))
        return [x.split(" ", 1)[1] for x in p.stdout.splitlines()]

    NOROOM = "the RAM disk has room for {} GiB now, not its {}"

    def test_a_ram_slot_free_and_it_fits_in_ram(self):
        """A big host (ceiling 29.0 23.2) with an empty RAM disk: t2h-20261123T1713-3cd9's lines on
        chopin and cinqcent, which a cutoff of 12 GiB sent to the SSD."""
        for gv, d in (("truenas25", 27), ("pop24", 20), ("pikaos4-luks", 16), ("mint22+bigger", 16),
                      ("solus49", 16), ("neon24", 15), ("debian13", 4)):
            self.assertEqual(self.place(d, 4), "ram", f"{gv}: D {d} fits the empty RAM disk")
        self.assertEqual(self.place(13, 4, after="20.0 29.0"), "ram", "over 12 GiB, and room: in RAM (no size cutoff)")
        self.assertEqual(self.place(2, 4, after="2.0 21.2"), "ram")
        self.assertEqual(self.place(9, 4, ceil="9.0 10.1", after="9.0 19.1", ssd="156.0"), "ram", "a small host")
        self.assertEqual(self.place(27, 4, ss=0), "ram", "the SSD slots full, a RAM slot free and it fits: in RAM")
        self.assertEqual(self.place(4, 4, ss=0, after="5.0 11.1"), "ram")

    def test_the_memory_is_for_d_plus_m_in_ram(self):
        """cmp-run.sh's memory room is for a job's D and M together since 2026-11-24 (its RAM disk
        is memory too): a line whose D fits the RAM disk but whose D + M doesn't fit the memory
        takes an SSD slot, where it needs its M alone; one whose M alone doesn't fit waits for that
        in its RAM slot, as before."""
        self.assertEqual(self.place(13, 4, after="20.0 17.0"), "ram", "13 + 4 within 17.0: in RAM")
        self.assertEqual(self.place(13, 4, after="20.0 16.9"),
                         "ssd overflow: the memory has room for 16.9 GiB now, not its D + M 17")
        self.assertEqual(self.place(13, 4, after="20.0 16.9", ss=0, ssd="184.0"), "ram",
                         "no SSD slot free, INLINE 1: it starts and waits for the memory in cmp-run.sh's line")
        self.assertEqual(self.lines(["13 4 0"], room="20.0 16.9", ss=0),
                         ["wait the memory has room for 16.9 GiB now, not its D + M 17, and no SSD slot is free"])
        self.assertEqual(self.place(13, 4, after="20.0 3.9"), "ram", "even its M is short: it waits in its RAM slot")

    def test_the_ram_slot_taken_on_the_ssd_though_ram_has_room(self):
        self.assertEqual(self.place(4, 4, rs=0), "ssd no RAM slot free", "an empty RAM disk, but its RAM slots are taken")
        self.assertEqual(self.place(27, 4, rs=0), "ssd no RAM slot free")
        self.assertEqual(self.place(4, 4, rs=0, after="29.0 2.0"), "ssd no RAM slot free",
                         "its memory short: it waits for that in cmp-run.sh's line, in its SSD slot")
        self.assertEqual(self.place(4, 4, rs=0, ssd="3.0"),
                         "wait no RAM slot free, and /bkoscratch has room for 3.0 GiB of scratch now")
        self.assertEqual(self.place(4, 4, rs=0, ssd=None),
                         "wait no RAM slot free, and /bkoscratch has room for no GiB of scratch now")

    def test_both_taken_it_waits(self):
        self.assertEqual(self.place(4, 4, rs=0, ss=0), "wait no slot free")
        self.assertEqual(self.place(27, 4, rs=0, ss=0), "wait no slot free")
        self.assertEqual(self.place(30, 4, rs=0, ss=0),
                         "wait no SSD slot free (its D is over the RAM-disk ceiling 29.0 23.2)")
        self.assertEqual(self.place(4, 4, "img", rs=0, ss=0), "wait no RAM slot free (+img works only in the RAM disk)")

    def test_the_ram_disk_full_on_the_ssd(self):
        """truenas25 holds 27 of the 29: what doesn't fit now takes an SSD slot, what does stays."""
        self.assertEqual(self.place(15, 4, after="2.0 21.2"), "ssd overflow: " + self.NOROOM.format("2.0", 15))
        self.assertEqual(self.place(3, 4, after="2.0 21.2"), "ssd overflow: " + self.NOROOM.format("2.0", 3))
        self.assertEqual(self.place(15, 4, after="0.0 19.2"), "ssd overflow: " + self.NOROOM.format("0.0", 15))
        self.assertEqual(self.place(27, 4, after="26.9 35.9"), "ssd overflow: " + self.NOROOM.format("26.9", 27))
        self.assertEqual(self.place(6, 4, ceil="9.0 10.1", after="5.0 11.1", ssd="156.0"),
                         "ssd overflow: " + self.NOROOM.format("5.0", 6))
        self.assertEqual(self.place(15, 4, after="2.0 3.9"), "ram", "its memory is what it waits for: in its RAM slot")
        self.assertEqual(self.place(6, 4, after="5.0 11.1", free_rc=1), "ram", "no answer from --free: in RAM")
        self.assertEqual(self.place(6, 4, after="x y"), "ram")

    def test_over_the_ceiling_an_ssd_slot_only(self):
        self.assertEqual(self.place(30, 4), "ssd DISK_OVER: over the RAM-disk ceiling 29.0 23.2", "vanilla3: never fits")
        self.assertEqual(self.place(30, 4, rs=0), "ssd DISK_OVER: over the RAM-disk ceiling 29.0 23.2")
        small = dict(ceil="9.0 10.1", after="9.0 19.1", ssd="156.0")
        self.assertEqual(self.place(17, 4, **small), "ssd DISK_OVER: over the RAM-disk ceiling 9.0 10.1")
        self.assertEqual(self.place(9.1, 4, **small), "ssd DISK_OVER: over the RAM-disk ceiling 9.0 10.1")
        self.assertEqual(self.place(17, 4, ss=0, **small),
                         "wait no SSD slot free (its D is over the RAM-disk ceiling 9.0 10.1)", "though its RAM slot is free")
        # no room for it on the SSD now: nowhere here, whatever the slots (cant_scan leaves it to the others)
        self.assertEqual(self.place(17, 4, ceil="9.0 10.1", ssd="15.0"), "none 15.0")
        self.assertEqual(self.place(17, 4, ceil="9.0 10.1", ssd="15.0", ss=0), "none 15.0")
        self.assertEqual(self.place(17, 4, ceil="9.0 10.1", ssd=None), "none no")
        self.assertEqual(self.place(17, 4, ceil=""), "ram", "no ceiling known: the RAM disk's while there is room")

    def test_no_room_in_ram_and_no_ssd_to_go_to(self):
        """A RAM slot free, the RAM disk without room for it now, and no SSD slot (or no SSD room).
        While a job of ours is here (the scan's INLINE 0) the line waits in the todo and the next
        one is tried, when an SSD slot would take it; with none here, or with an SSD that never
        will, it starts and waits in cmp-run.sh's line as it always did (the queue's place there)."""
        self.assertEqual(self.lines(["15 4 0", "2 4 0", "30 4 0", "15 4 1"], room="2.0 21.2", ss=0),
                         ["wait " + self.NOROOM.format("2.0", 15) + ", and no SSD slot is free", "ram",
                          "wait no SSD slot free (its D is over the RAM-disk ceiling 29.0 23.2)",
                          "wait " + self.NOROOM.format("2.0", 15) + " (+img works only in the RAM disk)"])
        self.assertEqual(self.lines(["15 4 0", "15 4 1"], room="2.0 21.2", ss=0, inline=1), ["ram", "ram"])
        self.assertEqual(self.place(15, 4, after="2.0 21.2", ss=0), "ram", "launch's own asking: INLINE 1")
        # the SSD short of room (cinqcent's fast disk at 98%, choubox's 15 GiB): it waits for the RAM disk
        self.assertEqual(self.lines(["6 4 0"], room="5.0 11.1", ssd="5.0", ceil="9.0 10.1"), ["ram"])
        self.assertEqual(self.place(6, 4, ceil="9.0 10.1", ssd="5.0", after="5.0 11.1"), "ram")
        self.assertEqual(self.place(6, 4, ceil="9.0 10.1", ssd=None, after="5.0 11.1"), "ram", "no answer from --ssd-free")
        self.assertEqual(self.place(13, 4, ssd="15.0", after="5.0 11.1"), "ssd overflow: " + self.NOROOM.format("5.0", 13))
        # memory short: nothing would start anywhere; it holds its slot in cmp-run.sh's line
        self.assertEqual(self.lines(["15 4 0", "15 4 1"], room="2.0 3.9", ss=0), ["ram", "ram"])

    def test_no_job_here_nothing_waits(self):
        """With no job of ours on the host both slots are free and INLINE is 1: no line waits in the
        todo (else an idle runner would take what is left for other hosts' and end)."""
        for room in ("29.0 52.2", "2.0 21.2", "0.0 0.0", "2.0 3.9", ""):
            for ssd in ("184.0", "3.0", ""):
                got = self.lines(["2 4 0", "15 4 0", "30 4 0", "2 4 1", "15 4 1", "30 4 1"], room=room, ssd=ssd, inline=1)
                self.assertFalse([x for x in got if x.startswith("wait")], (room, ssd, got))

    def test_choubox_s_single_slot_either_way(self):
        """choubox: holt_jobs 1, a RAM slot and an SSD slot, a RAM-disk ceiling of 3.0 and 14.5 GiB
        of SSD room: its one job is a RAM job or an SSD job, and most of its lines are the SSD's."""
        box = dict(ceil="3.0 6.0", ssd="14.5", after="3.0 9.0")
        self.assertEqual(self.place(2, 4, **box), "ram")
        self.assertEqual(self.place(3, 4, **box), "ram")
        self.assertEqual(self.place(4, 4, **box), "ssd DISK_OVER: over the RAM-disk ceiling 3.0 6.0")
        self.assertEqual(self.place(14, 4, **box), "ssd DISK_OVER: over the RAM-disk ceiling 3.0 6.0")
        self.assertEqual(self.place(16, 4, **box), "none 14.5", "left for the other hosts")
        self.assertEqual(self.place(2, 4, **dict(box, after="0.4 5.4")), "ssd overflow: " + self.NOROOM.format("0.4", 2))
        self.assertEqual((H.holt_slots("choubox"), H.limit("choubox", "holt_jobs")), ((1, 1), 1))

    def test_img_and_the_other_modes(self):
        """+img only in the RAM disk; the modes ssd and ram count no slots; HT_RAM=0."""
        self.assertEqual(self.place(30, 4, "img"), "ram", "+img: the RAM disk's (cant_why gives one over the ceiling back)")
        self.assertEqual(self.place(15, 4, "img", after="2.0 21.2"), "ram")
        self.assertEqual(self.place(15, 4, "img", ss=0), "ram")
        self.assertEqual(self.place(15, 4, "bigger", after="2.0 21.2"), "ssd overflow: " + self.NOROOM.format("2.0", 15))
        for rs, ss in ((1, 1), (0, 0)):
            self.assertEqual(self.place(2, 4, mode="ssd", rs=rs, ss=ss), "ssd scratch_mode ssd")
            self.assertEqual(self.place(2, 4, "img", mode="ssd", rs=rs, ss=ss), "ram")
            self.assertEqual(self.place(30, 4, mode="ssd", rs=rs, ss=ss), "ssd DISK_OVER: over the RAM-disk ceiling 29.0 23.2")
            self.assertEqual(self.place(6, 4, mode="ssd", ssd="5.0", rs=rs, ss=ss), "ram", "no room on the SSD: in RAM")
            self.assertEqual(self.place(15, 4, mode="ram", after="2.0 21.2", rs=rs, ss=ss), "ram", "mode ram: it waits for its room")
            self.assertEqual(self.place(30, 4, mode="ram", rs=rs, ss=ss), "ram", "mode ram without holt_disk_mode: cant_why's")
            self.assertEqual(self.place(30, 4, mode="ram", disk_over=1, ssd="0.0", rs=rs, ss=ss),
                             "ssd DISK_OVER: over the RAM-disk ceiling 29.0 23.2", "holt_disk_mode: --reserve 0 M, as before")
        self.assertEqual(self.place(0, 4, mode="ram", ram=0), "ram", "HT_RAM=0: every line on /bkoscratch under --reserve 0 M")

    def test_the_runner_reads_its_mode_and_slots_from_the_table(self):
        with open(QUEUE) as f:
            q = f.read()
        self.assertIn('read -r tmode tram tssd < <(python3 -I -B "$HOSTS_PY" holt-slots "$H" 2>/dev/null) || true', q)
        self.assertIn("MODE=${SCRATCH_MODE:-${tmode:-}}", q)
        self.assertIn("RSLOTS=${HOLT_RAM_SLOTS:-${tram:-}} SSLOTS=${HOLT_SSD_SLOTS:-${tssd:-}}", q)
        self.assertIn("[[ $MODE == ram ]] || DISK_OVER=1", q)
        self.assertIn('res=(--reserve "$d" "$m"); [[ $MODE == ram || -z $ssd ]] || res=(--reserve-ssd "$ssd" "$m")', q)
        self.assertIn("! grep -q -- '--reserve-ssd' \"$CMP_RUN\"", q, "an older shared cmp-run.sh: mode ram")
        self.assertIn('pl=$(place "$d" "$m" "$v")', q)
        self.assertIn('"ssd "*) ssd=$d pwhy=${pl#ssd } d=0 jram=0 ;;', q)
        self.assertIn("[[ $MODE != overflow ]] || nb=0", q, "the slots stand in for the big ones' turns")
        self.assertIn('sk=("${skip[@]}" "${CANT[@]}" "${REFUSED[@]}" "${NOTNOW[@]}")', q, "the pop skips what can't start now")
        self.assertNotIn("CUTOFF", q, "no size sends a line to the SSD")
        self.assertNotIn("ssd_cutoff_gib", q)
        code = [l for l in q.split("\n") if not l.lstrip().startswith("#")]
        for h in ("chopin", "cinqcent", "copland", "cage", "czerny", "choubox"):
            self.assertFalse([l for l in code if h in l], f"{h} named in ht1-queue.sh's code")

    def test_holt_slots_cli(self):
        """hosts.py holt-slots NAME, "MODE RAM SSD": what the runner reads."""
        def cli(name, **cage):
            self.table(**cage)
            p = subprocess.run([sys.executable, "-I", "-B", HOSTS_PY, "holt-slots", name], capture_output=True, text=True,
                               env=self.env, timeout=60)
            self.assertEqual((p.stderr, p.returncode), ("", 0))
            return p.stdout.strip()
        self.assertEqual(cli("cage", scratch_mode="overflow", holt_jobs=2, holt_ram_slots=1, holt_ssd_slots=1), "overflow 1 1")
        self.assertEqual(cli("cage", scratch_mode="overflow", holt_jobs=4, holt_ram_slots=2, holt_ssd_slots=2), "overflow 2 2")
        self.assertEqual(cli("cage", scratch_mode="overflow"), "overflow 3 3", "no slots given: holt_jobs of each")
        self.assertEqual(cli("cage", scratch_mode="overflow", holt_jobs=1, holt_ram_slots=2, holt_ssd_slots=2), "overflow 1 1",
                         "never more than holt_jobs")
        self.assertEqual(cli("cage", scratch_mode="overflow", holt_ssd_slots=0), "ram 3 0", "overflow with 0 SSD slots is ram")
        self.assertEqual(cli("cage", scratch_mode="overflow", holt_ram_slots=0), "ssd 0 3", "with 0 RAM slots ssd")
        self.assertEqual(cli("cage", scratch_mode="ram", holt_ram_slots=1, holt_ssd_slots=1), "ram 1 1")
        self.assertEqual(cli("nosuch"), "ram 3 3")


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
