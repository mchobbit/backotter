"""bs2 over A2's sources (bs2lib/sources.py): SOURCES, HOME (Alpine's and Guix's as
before), the refusal of a skipped source, tier 2's counts and its ESP test on every built
UEFI source, each source's seconds a case in split(), and the /bkoscratch mode for a source
over a host's RAM disk (bs2lib/room.py scratch(), img-speed F3; bs2's held() and plan; bs2-host's
reservation and queue; tier1's HT_RAM=0).  Nothing here reaches a host:

    python3 -m unittest discover -s testvm/backshot2/tests
"""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import corpus_cases as K
from bs2lib import case as C, dumpx, room as RM, sources as S, tier1 as T1

HERE = os.path.dirname(os.path.abspath(__file__))
BS2_DIR = os.path.dirname(HERE)


def load(name, file):
    loader = importlib.machinery.SourceFileLoader(name, os.path.join(BS2_DIR, file))
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


B = load("bs2_src_cli", "bs2")
H = load("bs2_src_host", "bs2-host")


# vanilla3 stands for a source over every RAM disk here: the full guest's room (D 30), as an
# unmeasured slimmed source takes (bs2lib/slim.py), not its slimmed row in slim-sizes.tsv
# (test_bs2_slim.py tests that one).
_NO_SLIM = mock.patch.object(RM, "SLIM_SIZES", os.devnull)


def setUpModule():
    _NO_SLIM.start()


def tearDownModule():
    _NO_SLIM.stop()


class Bs2Sources(unittest.TestCase):
    def test_home_unchanged_for_alpine_and_guix(self):
        self.assertEqual(B.SOURCES[:6], B.ALPINE_SOURCES + B.GUIX_SOURCES)
        self.assertEqual(B.ALPINE_SOURCES, ("alpine324", "alpine324-uefi", "alpine324-lvm", "alpine324-luks"))
        self.assertEqual(B.GUIX_SOURCES, ("guix15", "guix15-bios"))
        self.assertEqual({s: B.HOME[s] for s in B.SOURCES[:6]},
                         {"alpine324": "chopin", "alpine324-uefi": "chopin", "alpine324-lvm": "cinqcent",
                          "alpine324-luks": "cinqcent", "guix15": "chopin", "guix15-bios": "cinqcent"})
        self.assertEqual({s: B.HOME[s] for s in B.OTHER_SOURCES}, B.deal(B.OTHER_SOURCES, B.HOME_HOSTS))
        self.assertEqual(set(B.HOME), set(B.SOURCES))
        self.assertEqual(B.assign(B.SOURCES), B.HOME)
        self.assertEqual(B.UEFI, {s for s in B.SOURCES if S.uefi(s)})
        self.assertEqual(list(B.OTHER_SOURCES), [s for s in S.built() if s not in B.SOURCES[:6]])

    def test_a_skipped_source_is_refused_naming_its_piece(self):
        for src, piece in (("cachyos-zfs", "skipped (A3)"), ("freebsd15", "skipped (A4)"), ("guix15-luks", "skipped (later)"),
                           ("nosuch", "not a source")):
            r = subprocess.run([sys.executable, os.path.join(BS2_DIR, "bs2"), "BackShot2", src, "20", "plan"],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn(f"{src}: {piece}", r.stderr)
            self.assertEqual(r.stdout, "")

    def test_tier2_counts(self):
        run = ["debian13", "vanilla3", "kinoite44", "alpine324-uefi"]
        self.assertEqual({s: B.tier2_of(s, run) for s in run}, {"debian13": 0, "vanilla3": 5, "kinoite44": 5, "alpine324-uefi": 30})


class Seconds(unittest.TestCase):
    def test_split_by_each_sources_seconds(self):
        """Two hosts of the same slots, each holding both sources: the split evens the seconds,
        not the counts (vanilla3's ~895 s a case against debian13's 120)."""
        jobs = [("vanilla3", 1, i) for i in range(6)] + [("debian13", 1, i) for i in range(60)]
        can = {"vanilla3": ["chopin", "cinqcent"], "debian13": ["chopin", "cinqcent"]}
        parts = B.split(jobs, can, {"chopin": 5, "cinqcent": 5})
        walls = {h: B.wall(js, 5) for h, js in parts.items()}
        self.assertLessEqual(abs(walls["chopin"] - walls["cinqcent"]), B.secs("vanilla3", 1) / 5 + 1, walls)
        self.assertEqual(sorted(j for js in parts.values() for j in js), sorted(jobs))
        self.assertEqual({h: sum(1 for s, _, _ in js if s == "vanilla3") for h, js in parts.items()}, {"chopin": 3, "cinqcent": 3})
        self.assertEqual(B.wall([("vanilla3", 1, 0)], 5), B.secs("vanilla3", 1))   # one case is one slot's
        self.assertEqual(B.sweep_secs(["vanilla3"], 20, {"vanilla3": "chopin"}), {"chopin": 100 * 0.3})


class TierTwo(unittest.TestCase):
    def test_every_built_uefi_sources_esp(self):
        """Every built UEFI source has its ESP in the dump wherever it is mounted (/boot/efi, /efi,
        /boot, systemd-boot's ESP that holds the kernels), and a case that renumbers it, or asks
        for --no-nvram, goes to tier 2 (case.touches_esp)."""
        seen = set()
        for s in S.built():
            if s in S.ALPINE_SOURCES or not S.uefi(s):
                continue
            d = S.dump(s)
            esps = dumpx.esp_devices(d)
            self.assertTrue(esps, s)
            dk = next(x for x in dumpx.disks(d) if any(p["name"] in esps for p in x["partitions"]))
            p = next(p for p in dk["partitions"] if p["name"] in esps)
            to = max(q["number"] for q in dk["partitions"]) + 1
            self.assertTrue(C.touches_esp({"edits": [{"op": "renumber", "disk": dk["name"], "from": p["number"], "to": to}]}, d), s)
            self.assertTrue(C.touches_esp({"edits": [], "load_options": ["--no-nvram"]}, d), s)
            self.assertFalse(C.touches_esp({"edits": [], "load_options": []}, d), s)
            seen |= {e.get("mounted_at") for e in d["boot"]["esps"]}
        self.assertTrue({"/boot", "/efi", "/boot/efi"} <= seen, seen)


class DiskMode(unittest.TestCase):
    """A source over a host's RAM-disk ceiling works on that host's /bkoscratch wherever
    hosts.py disk_scratch() lets it (img-speed F3, FD3): vanilla3 (D 30) on the big hosts'
    29.0, mint22 (D 17 unslimmed) and guix15 (D 7) on a small host's 9.0 or choubox's 3.0."""

    BIG, SMALL, CHOU = (29.0, 23.2), (9.0, 10.1), (3.0, 6.0)

    def test_scratch(self):
        self.assertEqual(RM.need("vanilla3"), (30, 4))
        big, small, chou = self.BIG, self.SMALL, self.CHOU
        self.assertEqual(RM.scratch("vanilla3", big, True), "ssd")
        self.assertEqual(RM.scratch("vanilla3", big, False), None)     # its table lets it no SSD
        self.assertEqual(RM.scratch("vanilla3", (29.0, 3.0), True), None)   # its memory doesn't fit
        self.assertEqual(RM.scratch("vanilla3", (31.0, 23.2), True), "ram")  # a RAM disk that holds it
        self.assertEqual(RM.scratch("guix15", small, True), "ram")
        # F3: over this host's RAM disk, on its SSD, though another host's RAM disk holds it.
        self.assertEqual(RM.scratch("guix15", chou, True), "ssd")
        self.assertEqual(RM.scratch("guix15", chou, False), None)
        self.assertEqual(RM.scratch("mint22", small, True), "ssd")
        self.assertEqual(RM.scratch("mint22", small, False), None)
        self.assertEqual(RM.scratch("vanilla3", small, True), "ssd")
        # Stays big-only: a room whose memory is over a small host's 10.1.
        with mock.patch.object(RM, "need", lambda s, path=None: (12, 12)):
            self.assertIsNone(RM.scratch("qubes-like", small, True))
            self.assertEqual(RM.scratch("qubes-like", big, True), "ram")
        self.assertEqual(RM.scratch("alpine324", chou, True), "ram")
        self.assertIsNone(RM.scratch("vanilla3", None, True))
        # A shared-backup case goes where the source's own cases do: the SSD when it takes them.
        self.assertEqual(RM.scratch("mint22", small, True, shared=True), "ssd")
        # roomy() without disk_ok: RAM only.
        self.assertEqual(B.roomy({"vanilla3", "alpine324"}, big), ({"alpine324"}, {"vanilla3"}))
        self.assertEqual(B.roomy({"vanilla3", "alpine324"}, big, True)[0], {"vanilla3", "alpine324"})
        self.assertEqual(B.roomy({"mint22", "alpine324"}, small, True), ({"mint22", "alpine324"}, set()))

    SOURCES = ["vanilla3", "guix15", "debian13", "mint22"]

    def held(self, out, ok=lambda h: True, plan=("vanilla3", "debian13"), n=20):
        def ssh(h, cmd, check=True, capture=False, input=None):
            return subprocess.CompletedProcess([], 0, out[h], "")
        err = io.StringIO()
        with mock.patch.object(B, "ssh", ssh), mock.patch.object(B, "disk_ok", ok), mock.patch.dict(B.SCRATCH, clear=True), \
                mock.patch.dict(B.SHARED_ONLY, clear=True), \
                mock.patch.object(B, "RUN_HOSTS", {h: f"m@{h}.local" for h in out}), contextlib.redirect_stderr(err):
            got = B.held(self.SOURCES, list(out))
            scratch = dict(B.SCRATCH)
            text = io.StringIO()
            with contextlib.redirect_stdout(text):
                B.plan_words(list(plan), n)
        return got, scratch, err.getvalue(), text.getvalue()

    def test_held_and_the_plan(self):
        out = {"chopin": "@ceiling 29.0 23.2\nvanilla3\nguix15\ndebian13\n", "cinqcent": "@ceiling 29.0 23.2\nvanilla3\ndebian13\n",
               "choubox": "@ceiling 3.0 6.0\nguix15\ndebian13\n"}
        got, scratch, err, text = self.held(out)
        self.assertEqual(got, {"chopin": {"vanilla3", "guix15", "debian13"}, "cinqcent": {"vanilla3", "debian13"},
                               "choubox": {"guix15", "debian13"}})
        self.assertEqual({k: v for k, v in scratch.items() if v == "ssd"},
                         {("chopin", "vanilla3"): "ssd", ("cinqcent", "vanilla3"): "ssd", ("choubox", "guix15"): "ssd"})
        self.assertEqual(scratch[("choubox", "debian13")], "ram")
        self.assertNotIn("left out", err)
        line = next(l for l in text.splitlines() if l.startswith("vanilla3 "))
        self.assertIn("SSD", line)
        self.assertIn(f"{B.secs('vanilla3', 1)} est.", line)
        self.assertRegex(text, r"on the SSD \(/bkoscratch, cmp-run.sh --reserve-ssd\): 25 cases \(vanilla3 on chopin, cinqcent\), "
                               r"~\d+ GB written est\.")
        self.assertRegex(text, r"chopin: 5 slots, \d+ cases \(vanilla3 \d+\+\d SSD")

    def test_a_large_source_on_the_small_hosts(self):
        """F3's proof in miniature: mint22 (D 17, over the small hosts' 9.0) is on all five
        hosts; the small ones take some of its cases on their SSD, one at a time, while the
        big ones hold it in RAM."""
        big, small = "@ceiling 29.0 23.2\nmint22\ndebian13\n", "@ceiling 9.0 10.1\nmint22\ndebian13\n"
        out = {"chopin": big, "cinqcent": big, "copland": small, "cage": small, "czerny": small}
        got, scratch, err, text = self.held(out, plan=("mint22",), n=60)
        self.assertEqual({h for h, ss in got.items() if "mint22" in ss}, set(out))
        self.assertEqual({h for (h, s), v in scratch.items() if s == "mint22" and v == "ssd"}, {"copland", "cage", "czerny"})
        self.assertEqual({h for (h, s), v in scratch.items() if s == "mint22" and v == "ram"}, {"chopin", "cinqcent"})
        line = next(l for l in text.splitlines() if l.startswith("mint22 "))
        self.assertIn("RAM, SSD on copland, cage, czerny", line)
        self.assertRegex(text, r"copland: 2 slots, \d+ cases \(mint22 \d+ SSD")
        self.assertRegex(text, r"on the SSD \(/bkoscratch, cmp-run.sh --reserve-ssd\): \d+ cases \(mint22 on [a-z, ]+\)")
        # With the small hosts' SSD off (scratch_mode ram), mint22 is the big pair's alone, as before F3.
        got, scratch, err, _ = self.held(out, ok=lambda h: h in ("chopin", "cinqcent"), plan=("mint22",), n=60)
        self.assertEqual({h for h, ss in got.items() if "mint22" in ss}, {"chopin", "cinqcent"})
        self.assertIn("copland has vms/mint22, but its cmp-run.sh --ceiling is 9 10.1, under mint22's cases' --reserve 17 4: "
                      "left out for mint22 (and its scratch_mode is ram in testvm/hosts.json: no SSD cases there)", err)

    def test_a_host_the_table_keeps_in_ram(self):
        out = {"chopin": "@ceiling 29.0 23.2\nvanilla3\ndebian13\n", "cinqcent": "@ceiling 29.0 23.2\nvanilla3\ndebian13\n"}
        got, scratch, err, text = self.held(out, ok=lambda h: h == "chopin")
        self.assertEqual(got, {"chopin": {"vanilla3", "debian13"}, "cinqcent": {"debian13"}})
        self.assertIn("left out for vanilla3 (and its scratch_mode is ram in testvm/hosts.json", err)

    def test_all_in_ram_says_so(self):
        out = {"chopin": "@ceiling 29.0 23.2\ndebian13\n"}
        text = io.StringIO()

        def ssh(h, cmd, check=True, capture=False, input=None):
            return subprocess.CompletedProcess([], 0, out[h], "")
        with mock.patch.object(B, "ssh", ssh), mock.patch.dict(B.SCRATCH, clear=True), \
                mock.patch.object(B, "RUN_HOSTS", {"chopin": "m@chopin.local"}), contextlib.redirect_stdout(text):
            B.plan_words(["debian13"], 20)
        self.assertIn("in RAM: ~0 GB written to the SSDs", text.getvalue())
        self.assertNotIn("on the SSD", text.getvalue())

    def test_the_table_says_which_hosts(self):
        # hosts.py disk_scratch(): scratch_mode overflow (every amd64 host's) or holt_disk_mode;
        # bigfoot's scratch_mode ram (and a host the table doesn't know): no.
        self.assertEqual({h: B.disk_ok(h) for h in ("chopin", "cinqcent", "copland", "cage", "czerny", "choubox", "bigfoot",
                                                    "nosuch")},
                         {"chopin": True, "cinqcent": True, "copland": True, "cage": True, "czerny": True, "choubox": True,
                          "bigfoot": False, "nosuch": False})
        for h, want in (("copland", True), ("bigfoot", False), ("nosuch", False)):
            with mock.patch.dict(os.environ, {"BS2_HOSTNAME": h}):
                self.assertEqual(H.disk_ok(), want, h)

    def test_the_ssd_lane_is_one_case_at_a_time(self):
        """split() and wall() run a host's SSD cases one after another (bs2-host's ssd_token),
        beside its RAM cases on the other slots."""
        jobs = [("vanilla3", 1, i) for i in range(10)] + [("debian13", 1, i) for i in range(100)]
        can = {"vanilla3": ["chopin", "cinqcent"], "debian13": ["chopin", "cinqcent"]}
        ssd = {("chopin", "vanilla3"): "ssd", ("cinqcent", "vanilla3"): "ssd"}
        v = B.secs("vanilla3", 1)
        with mock.patch.dict(B.SCRATCH, ssd, clear=True):
            parts = B.split(jobs, can, {"chopin": 5, "cinqcent": 5})
            self.assertEqual({h: sum(1 for s, _, _ in js if s == "vanilla3") for h, js in parts.items()}, {"chopin": 5, "cinqcent": 5})
            for h, js in parts.items():
                self.assertGreaterEqual(B.wall(js, 5, 0, h), 5 * v)          # the lane: 5 cases end to end
                self.assertLess(B.wall(js, 5, 0, h) - 5 * v, v)              # the RAM cases beside it, not after it
                self.assertLess(B.wall(js, 5), 5 * v)                        # (without the host: no lane)
            # A host with a long lane already takes the next SSD case less readily than an idle one.
            p2 = B.split([("vanilla3", 1, i) for i in range(4)], {"vanilla3": ["chopin", "cinqcent"]},
                         {"chopin": 5, "cinqcent": 5})
            self.assertEqual({h: len(js) for h, js in p2.items()}, {"chopin": 2, "cinqcent": 2})
            self.assertEqual(B.wall(p2["chopin"], 5, 0, "chopin"), 2 * v)
        with mock.patch.dict(B.SCRATCH, {}, clear=True):   # in RAM: as before
            self.assertEqual(B.wall([("vanilla3", 1, 0), ("vanilla3", 1, 1)], 5, 0, "chopin"), v)


class HostReservation(unittest.TestCase):
    """bs2-host: a case's reservation and ht1.sh's place from its source and this host."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        for s in ("vanilla3", "alpine324"):
            os.makedirs(f"{self.d}/sources/{s}")
            shutil.copy(os.path.join(K.CORPUS, s, "src.json"), f"{self.d}/sources/{s}/src.json")
        self.cases = {}
        for s in ("vanilla3", "alpine324"):
            p = f"{self.d}/{s}.case"
            C.write(p, {"name": f"{s}-x", "source": f"{s}@g", "seed": 1, "edits": [], "load_options": []})
            self.cases[s] = p
        H._DUMPS.clear()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)
        H._DUMPS.clear()

    def words(self, s, ceil=(29.0, 23.2), ok=True, how="--reserve-ssd"):
        with mock.patch.object(H, "ROOT", self.d), mock.patch.object(H, "ceiling", lambda: ceil), \
                mock.patch.object(H, "disk_ok", lambda: ok), mock.patch.object(H, "ssd_reserve", lambda: how):
            return H.reserve_words(self.cases[s]), H.case_scratch(self.cases[s])

    def test_words(self):
        self.assertEqual(self.words("vanilla3"), ((["--reserve-ssd", "30", "4"], "ssd"), "ssd"))
        self.assertEqual(self.words("vanilla3", how="--reserve 0"), ((["--reserve", "0", "4"], "ssd"), "ssd"))
        self.assertEqual(self.words("vanilla3", ok=False), ((["--reserve", "30", "4"], "ram"), "ram"))   # as before: cmp-run.sh refuses it
        self.assertEqual(self.words("alpine324"), ((["--reserve", "2", "3"], "ram"), "ram"))
        self.assertEqual(self.words("alpine324", ok=False), ((["--reserve", "2", "3"], "ram"), "ram"))

    def test_the_queue_passes_its_scratch(self):
        rd = f"{self.d}/runs/r"
        os.makedirs(rd)
        with open(f"{rd}/queue.txt", "w") as f:
            f.write(f"1 {self.cases['vanilla3']}\n2 {self.cases['alpine324']}\n")
        calls = []

        kws = []

        def run(cmd, **kw):
            calls.append((cmd, kw.get("env") or {}))
            kws.append(kw)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        args = type("A", (), {"run": "r", "ports": "2943-2948", "slots": 1, "disk": "2", "mem": "3"})()
        with mock.patch.object(H, "ROOT", self.d), mock.patch.object(H, "ceiling", lambda: (29.0, 23.2)), \
                mock.patch.object(H, "disk_ok", lambda: True), mock.patch.object(H, "ssd_reserve", lambda: "--reserve-ssd"), \
                mock.patch.object(H.subprocess, "run", run), mock.patch.object(H.time, "sleep", lambda s: None), \
                contextlib.redirect_stdout(io.StringIO()):
            H.cmd_queue(args)
        (c1, e1), (c2, e2) = calls
        self.assertEqual(c1[1:4], ["--reserve-ssd", "30", "4"])
        self.assertEqual(c1[-2:], ["--scratch", "ssd"])
        self.assertEqual((e1.get("WEAR_SCRATCH"), e1.get("BS2_SSD_HELD")), ("ssd", "1"))
        self.assertEqual(len(kws[0]["pass_fds"]), 1)   # (the lane's fd: held by the case, should the queue die)
        self.assertEqual(kws[1]["pass_fds"], ())
        self.assertFalse(os.path.exists(f"{rd}/queue.taken.new"))
        with open(f"{rd}/queue.taken") as f:
            self.assertEqual(f.read().split(), ["0", "1"])
        self.assertEqual(c2[1:4], ["--reserve", "2", "3"])
        self.assertEqual(c2[-2:], ["--scratch", "ram"])
        self.assertNotIn("WEAR_SCRATCH", {k: v for k, v in e2.items() if k not in os.environ})

    def test_pick(self):
        lines = [["1", "a.ssd"], ["1", "b.ssd"], ["1", "c"], ["2", "d"]]
        ssd = lambda p: p.endswith(".ssd")   # noqa: E731
        self.assertEqual(H.pick(lines, set(), ssd, lambda: "T"), (0, "T"))
        self.assertEqual(H.pick(lines, {0}, ssd, lambda: None), (2, None))    # the lane busy: the next RAM case
        self.assertEqual(H.pick(lines, {0, 2, 3}, ssd, lambda: None), (None, None))   # only SSD left, lane busy: wait
        self.assertEqual(H.pick(lines, {0, 2, 3}, ssd, lambda: "T"), (1, "T"))
        self.assertEqual(H.pick(lines, {0, 1, 2, 3}, ssd, lambda: "T"), (None, None))
        # The lane free: its earliest case first, wherever it is in queue.txt (vanilla3's sit late),
        # as split() and wall() plan the lane from the start.
        late = [["1", "c"], ["1", "d"], ["1", "e"], ["1", "a.ssd"], ["2", "b.ssd"]]
        self.assertEqual(H.pick(late, set(), ssd, lambda: "T"), (3, "T"))
        self.assertEqual(H.pick(late, {3}, ssd, lambda: "T"), (4, "T"))
        self.assertEqual(H.pick(late, {3}, ssd, lambda: None), (0, None))
        asked = []
        self.assertEqual(H.pick([["1", "c"]], set(), ssd, lambda: asked.append(1)), (0, None))
        self.assertEqual(asked, [], "no SSD case left: the lane isn't asked for")

    def test_the_lane_lock_unopenable_is_busy(self):
        with mock.patch.object(H, "ROOT", "/nonexistent/no/such"), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertIsNone(H.ssd_token(wait=False))
            self.assertIsNone(H.ssd_token(wait=True))
        self.assertIn("taken as busy", err.getvalue())

    def test_lane_takes_it_before_the_command(self):
        """bs2-host lane (bs2's replay, soak and corpus): the lane taken outside cmp-run.sh,
        its fd passed on, BS2_SSD_HELD set; a RAM case's command runs as it is."""
        seen = []

        def run(cmd, env=None, pass_fds=()):
            seen.append((cmd, env, pass_fds, H.ssd_token(wait=False)))
            return subprocess.CompletedProcess(cmd, 4)
        with mock.patch.object(H, "ROOT", self.d), mock.patch.object(H, "case_scratch", lambda p: "ssd"), \
                mock.patch.object(H.subprocess, "run", run), self.assertRaises(SystemExit) as cm:
            H.cmd_lane(type("A", (), {"case": self.cases["vanilla3"], "command": ["--", "./cmp-run.sh", "x"]})())
        self.assertEqual(cm.exception.code, 4)
        cmd, env, fds, other = seen[0]
        self.assertEqual(cmd, ["./cmp-run.sh", "x"])
        self.assertEqual((env["BS2_SSD_HELD"], len(fds)), ("1", 1))
        self.assertIsNone(other, "the lane was held while the command ran")
        with mock.patch.object(H, "ROOT", self.d), mock.patch.object(H, "case_scratch", lambda p: "ram"), \
                mock.patch.object(H.subprocess, "run", run), self.assertRaises(SystemExit):
            H.cmd_lane(type("A", (), {"case": self.cases["alpine324"], "command": ["--", "true"]})())
        self.assertEqual((seen[1][2], "BS2_SSD_HELD" in seen[1][1] and seen[1][1]["BS2_SSD_HELD"] == "1"), ((), False))
        if seen[1][3]:
            seen[1][3].close()

    def test_one_ssd_case_at_a_time_ram_cases_beside_it(self):
        """Two vanilla3 cases and an alpine324 one on 2 slots: the second vanilla3 case waits for
        the lane while the alpine324 case runs beside the first."""
        import threading
        rd = f"{self.d}/runs/r"
        os.makedirs(rd)
        v2 = f"{self.d}/vanilla3-2.case"
        C.write(v2, {"name": "vanilla3-y", "source": "vanilla3@g", "seed": 2, "edits": [], "load_options": []})
        with open(f"{rd}/queue.txt", "w") as f:
            f.write(f"1 {self.cases['vanilla3']}\n1 {v2}\n1 {self.cases['alpine324']}\n")
        order, ram_ran, lock = [], threading.Event(), threading.Lock()
        live = []

        def run(cmd, **kw):
            path = cmd[cmd.index("tier1") + 1]
            with lock:
                order.append(os.path.basename(path))
                live.append(path)
                self.assertLessEqual(sum(1 for p in live if "vanilla3" in p), 1, live)
            if path == self.cases["vanilla3"]:
                self.assertTrue(ram_ran.wait(10), "the RAM case didn't run beside the SSD one")
            if path == self.cases["alpine324"]:
                ram_ran.set()
            with lock:
                live.remove(path)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        args = type("A", (), {"run": "r", "ports": "2943-2948", "slots": 2, "disk": "2", "mem": "3"})()
        with mock.patch.object(H, "ROOT", self.d), mock.patch.object(H, "ceiling", lambda: (29.0, 23.2)), \
                mock.patch.object(H, "disk_ok", lambda: True), mock.patch.object(H, "ssd_reserve", lambda: "--reserve-ssd"), \
                mock.patch.object(H.subprocess, "run", run), mock.patch.object(H.time, "sleep", lambda s: None), \
                mock.patch.object(H, "SSD_POLL_S", 0), contextlib.redirect_stdout(io.StringIO()):
            H.cmd_queue(args)
        self.assertEqual(order, ["vanilla3.case", "alpine324.case", "vanilla3-2.case"])
        with open(f"{rd}/queue.next") as f:
            self.assertEqual(f.read(), "3")
        with mock.patch.object(H, "ROOT", self.d):
            t = H.ssd_token(wait=False)
            self.assertIsNotNone(t, "the lane given back")
            self.assertIsNone(H.ssd_token(wait=False), "and one holder at a time")
            t.close()


class Plant(unittest.TestCase):
    """bs2-host plant asks the filesystem that really holds content.DIR (content.real_dir:
    vanilla3's /srv is a link to var/srv, so /var's), and a DIR it can't reach is a planting
    failure that says why."""

    def test_the_real_dirs_filesystem(self):
        from bs2lib import content as CT
        seen = {}
        with tempfile.TemporaryDirectory() as d:
            case = f"{d}/c.case"
            C.write(case, {"name": "x", "source": "vanilla3@g", "seed": 1, "edits": [], "load_options": [], "content": {}})
            a = type("A", (), {"case": case, "root": f"{d}/root", "layout": None})()
            with mock.patch.object(CT, "real_dir", lambda root: d), \
                    mock.patch.object(H, "_content_fs", lambda at: seen.setdefault("fs_at", at) and "btrfs"), \
                    mock.patch.object(CT, "plan", lambda content, fs, symlink_max=None: seen.update(fs=fs, lim=symlink_max) or []), \
                    mock.patch.object(CT, "plant", lambda root, entries: []), contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit) as cm:
                H.cmd_plant(a)
            self.assertEqual(cm.exception.code, 0)
            self.assertEqual((seen["fs_at"], seen["fs"]), (d, "btrfs"))

            def refuse(root):
                raise OSError(18, "Invalid cross-device link")
            out = io.StringIO()
            with mock.patch.object(CT, "real_dir", refuse), contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
                H.cmd_plant(a)
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("could not plant: /srv/bs2 under", out.getvalue())
            self.assertIn("an absolute /srv link", out.getvalue())


class Tier1Scratch(unittest.TestCase):
    def test_env(self):
        self.assertEqual(T1.env_of("ram", "/bkoscratch/backshot2"), {"HT_WORK": "/mnt/bko-ram/backshot2"})
        self.assertEqual(T1.env_of("ssd", "/bkoscratch/backshot2"), {"HT_RAM": "0", "HT_WORK": "/bkoscratch/backshot2/work"})

    def test_run_once_and_its_row(self):
        seen = []

        def run(cmd, env=None, **kw):
            seen.append(env)
            return subprocess.CompletedProcess(cmd, 0)
        with tempfile.TemporaryDirectory() as d:
            case = {"source": "vanilla3@g", "seed": 1, "edits": [], "load_options": []}
            k = f"{d}/r-t1/vanilla3-x"
            os.makedirs(f"{k}/logs")
            with open(f"{k}/report.txt", "w") as f:
                f.write("HoltTest1 vanilla3, phases: source\n== source\nRESULT: PASS\n")
            with open(f"{k}/logs/phases.tsv", "w") as f:
                f.write("phase\tx\nsource\t1\n")
            with mock.patch.object(T1.subprocess, "run", run), mock.patch.object(T1.L, "write_rows", lambda *a, **k: None), \
                    contextlib.redirect_stdout(io.StringIO()):
                rc = T1.run(case, "vanilla3-x", "vanilla3", "g", {}, 1, "r", 2943, BS2_DIR, os.path.dirname(BS2_DIR), d, "/h", {},
                            scratch="ssd")
            self.assertEqual(rc, 0)
            self.assertEqual((seen[0]["HT_RAM"], seen[0]["HT_WORK"]), ("0", f"{d}/work"))
            with open(f"{d}/runs/r/tier1.jsonl") as f:
                row = json.loads(f.read())
            self.assertEqual((row["verdict"], row["scratch"]), ("PASS", "ssd"))


if __name__ == "__main__":
    unittest.main()
