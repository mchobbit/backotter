"""bs2 over A2's sources (bs2lib/sources.py): SOURCES, HOME (Alpine's and Guix's as
before), the refusal of a skipped source, tier 2's counts and its ESP test on every built
UEFI source, each source's seconds a case in split(), and the /bkoscratch mode for a source
over every RAM disk (bs2lib/room.py scratch(); bs2's held() and plan; bs2-host's
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


class Bs2Sources(unittest.TestCase):
    def test_home_unchanged_for_alpine_and_guix(self):
        self.assertEqual(B.SOURCES[:6], B.ALPINE_SOURCES + B.GUIX_SOURCES)
        self.assertEqual(B.ALPINE_SOURCES, ("alpine324", "alpine324-uefi", "alpine324-lvm", "alpine324-luks"))
        self.assertEqual(B.GUIX_SOURCES, ("guix15", "guix15-bios"))
        self.assertEqual({s: B.HOME[s] for s in B.SOURCES[:6]},
                         {"alpine324": "chopin", "alpine324-uefi": "chopin", "alpine324-lvm": "cinqcent",
                          "alpine324-luks": "cinqcent", "guix15": "chopin", "guix15-bios": "cinqcent"})
        self.assertEqual({s: B.HOME[s] for s in B.OTHER_SOURCES}, B.deal(B.OTHER_SOURCES, B.LEAD_HOSTS))
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
    """vanilla3 (D 30) is over every RAM disk (the big hosts' ceiling 29.0): on /bkoscratch."""

    def test_scratch(self):
        self.assertEqual(RM.need("vanilla3"), (30, 4))
        big, small, chou = (29.0, 23.2), (9.0, 10.1), (3.0, 6.0)
        self.assertEqual(RM.scratch("vanilla3", big, True, 29.0), "ssd")
        self.assertEqual(RM.scratch("vanilla3", big, False, 29.0), None)     # its table lets it no SSD
        self.assertEqual(RM.scratch("vanilla3", big, True), "ssd")           # bs2-host's: its own ceiling
        self.assertEqual(RM.scratch("vanilla3", (29.0, 3.0), True, 29.0), None)   # its memory doesn't fit
        self.assertEqual(RM.scratch("vanilla3", (31.0, 23.2), True, 31.0), "ram")  # a RAM disk that holds it
        self.assertEqual(RM.scratch("guix15", small, True, 29.0), "ram")
        self.assertEqual(RM.scratch("guix15", chou, True, 29.0), None)       # a host of the run holds it in RAM
        self.assertEqual(RM.scratch("mint22", small, True, 29.0), None)
        self.assertEqual(RM.scratch("alpine324", chou, True, 29.0), "ram")
        self.assertIsNone(RM.scratch("vanilla3", None, True, 29.0))
        # roomy() without the run's figures is as before A2: RAM only.
        self.assertEqual(B.roomy({"vanilla3", "alpine324"}, big), ({"alpine324"}, {"vanilla3"}))
        self.assertEqual(B.roomy({"vanilla3", "alpine324"}, big, 29.0, True)[0], {"vanilla3", "alpine324"})

    def held(self, out, ok=lambda h: True):
        def ssh(h, cmd, check=True, capture=False, input=None):
            return subprocess.CompletedProcess([], 0, out[h], "")
        err = io.StringIO()
        with mock.patch.object(B, "ssh", ssh), mock.patch.object(B, "disk_ok", ok), mock.patch.dict(B.SCRATCH, clear=True), \
                mock.patch.object(B, "RUN_HOSTS", {h: f"m@{h}.local" for h in out}), contextlib.redirect_stderr(err):
            got = B.held(["vanilla3", "guix15", "debian13"], list(out))
            scratch = dict(B.SCRATCH)
            text = io.StringIO()
            with contextlib.redirect_stdout(text):
                B.plan_words(["vanilla3", "debian13"], 20)
        return got, scratch, err.getvalue(), text.getvalue()

    def test_held_and_the_plan(self):
        out = {"chopin": "@ceiling 29.0 23.2\nvanilla3\nguix15\ndebian13\n", "cinqcent": "@ceiling 29.0 23.2\nvanilla3\ndebian13\n",
               "choubox": "@ceiling 3.0 6.0\nguix15\ndebian13\n"}
        got, scratch, err, text = self.held(out)
        self.assertEqual(got, {"chopin": {"vanilla3", "guix15", "debian13"}, "cinqcent": {"vanilla3", "debian13"},
                               "choubox": {"debian13"}})
        self.assertEqual({k: v for k, v in scratch.items() if v == "ssd"}, {("chopin", "vanilla3"): "ssd", ("cinqcent", "vanilla3"): "ssd"})
        self.assertEqual(scratch[("choubox", "debian13")], "ram")
        self.assertIn("choubox has vms/guix15, but its cmp-run.sh --ceiling is 3 6, under guix15's cases' --reserve 7 4: "
                      "left out for guix15 (a host of the run holds it in its RAM disk)", err)
        line = next(l for l in text.splitlines() if l.startswith("vanilla3 "))
        self.assertIn("SSD", line)
        self.assertIn(f"{B.secs('vanilla3', 1)} est.", line)
        self.assertRegex(text, r"on the SSD \(/bkoscratch, cmp-run.sh --reserve-ssd\): 25 cases \(vanilla3 on chopin, cinqcent\), "
                               r"~\d+ GB written est\.")
        self.assertRegex(text, r"chopin: 5 slots, \d+ cases \(vanilla3 \d+\+\d SSD")

    def test_a_host_the_table_keeps_in_ram(self):
        out = {"chopin": "@ceiling 29.0 23.2\nvanilla3\ndebian13\n", "cinqcent": "@ceiling 29.0 23.2\nvanilla3\ndebian13\n"}
        got, scratch, err, text = self.held(out, ok=lambda h: h == "chopin")
        self.assertEqual(got, {"chopin": {"vanilla3", "debian13"}, "cinqcent": {"debian13"}})
        self.assertIn("left out for vanilla3 (and its holt_disk_mode isn't true in testvm/hosts.json", err)

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
        # holt_disk_mode true: the big hosts only; a scratch_mode overflow alone (the small ones') isn't enough.
        self.assertEqual({h: B.disk_ok(h) for h in ("chopin", "cinqcent", "copland", "cage", "czerny", "choubox", "nosuch")},
                         {"chopin": True, "cinqcent": True, "copland": False, "cage": False, "czerny": False, "choubox": False,
                          "nosuch": False})

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

        def run(cmd, **kw):
            calls.append((cmd, kw.get("env") or {}))
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
