"""The hosts' limits in the host table (gaps P13, P14, P15): stic_slots, guest_slots, holt_jobs,
base_gib, holt_disk_mode, bs2_slots, max_wall_x, scratch_mode, ssd_floor_gib, holt_ram_slots and holt_ssd_slots, each a
row's field, else the table's class_limits, else the default (3, 3, 3, 6, false, 3, null, ram, 12, 80).
With a table of defaults every host has them, and the planners give
the plans they gave before (estimates.pack and drive.simulate against the code of 602922f, the
commit before the limits).  An alternative table (BKO_HOSTS_JSON) shows the consumers follow
it: drive.py's --slots and guest slots, the big-first preference, pack's per-host slots,
cmp-run.sh's BASE_GIB, HoltTest2's MAXJOBS and DISK_OVER per host, and placement holding a
disk-mode host's over-RAM-disk cells to their memory only."""

import copy
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
TESTVM = os.path.dirname(T2)
REPO = os.path.dirname(TESTVM)
sys.path.insert(0, T2)
sys.path.insert(0, HERE)

from t2lib import estimates, holt, plan  # noqa: E402
from t2lib import hosts as H  # noqa: E402

TABLE = os.path.join(TESTVM, "hosts.json")
HOSTS_PY = os.path.join(T2, "t2lib", "hosts.py")
HOSTS_SH = os.path.join(TESTVM, "hosts.sh")
CMP_RUN = os.path.join(TESTVM, "compare", "cmp-run.sh")
QUEUE = os.path.join(TESTVM, "holttest1d", "ht1-queue.sh")
DRIVE_PY = os.path.join(TESTVM, "stictest1", "drive.py")
BEFORE = "602922f"   # the commit before the limits
BASH = shutil.which("bash")
DEFAULTS = {"stic_slots": 3, "guest_slots": 3, "holt_jobs": 3, "base_gib": 6, "holt_disk_mode": False, "bs2_slots": 3,
            "max_wall_x": None, "scratch_mode": "ram", "ssd_floor_gib": 80, "holt_ram_slots": None,
            "holt_ssd_slots": None}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


DRIVE = load_module("hl_drive", DRIVE_PY)


def rd(path):
    with open(path) as f:
        return f.read()


def before(path):
    """PATH's text at BEFORE, or None (no such commit here: a shallow clone)."""
    p = subprocess.run(["git", "-C", REPO, "show", f"{BEFORE}:{path}"], capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else None


def table(**edit):
    """The real table with no class limits and no row limits (every host at the defaults) and
    EDIT applied: class_limits=..., or HOST={field: value} merged into that host's row."""
    with open(TABLE) as f:
        t = json.load(f)
    t["class_limits"] = {"big": {}, "small": {}}
    for r in t["hosts"]:
        for k in H.LIMITS:
            r.pop(k, None)
    for k, v in edit.items():
        if k == "class_limits":
            t["class_limits"] = v
        else:
            next(r for r in t["hosts"] if r["name"] == k).update(v)
    return t


class AltTable(unittest.TestCase):
    """setUp gives self.t (a temp dir); use_table(t) points BKO_HOSTS_JSON at T for the test."""

    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="t2-limits-")
        self.addCleanup(shutil.rmtree, self.t, True)

    def use_table(self, t, name="hosts.json"):
        p = os.path.join(self.t, name)
        with open(p, "w") as f:
            json.dump(t, f)
        patcher = mock.patch.dict(os.environ, {H.ENV: p})
        patcher.start()
        self.addCleanup(patcher.stop)
        return p


# --- the table ---------------------------------------------------------------------------------

class Limits(AltTable):
    def test_the_real_table(self):
        """The user's choice (2026-11-13): the small hosts run 2 stic slots and 1 holt job with a
        BASE_GIB of 4; the big hosts keep the defaults. bs2 runs 5 cases at once on a big host,
        2 on a small one. The rows that set a limit of their own: chopin's and cinqcent's
        holt_disk_mode (the user's choice, 2026-11-14, for vanilla3's 30 GiB disk) and
        choubox's smaller own four (2026-11-17: the smallest host, 1 of each and BASE_GIB 3) and
        its max_wall_x 2 (2026-11-18: the long and the slow cells left to the other hosts).
        RAM first, the SSD for overflow (the user's choice, 2026-11-23): both classes set
        scratch_mode overflow, the floor at its default (80 GiB; no size cutoff: RAM first), and
        with scratch off the RAM disk the holt jobs at once went from 3 to 4 on the big hosts and
        from 1 to 2 on the M900s; choubox keeps its 1, and bigfoot (not a test host of the six)
        stays as it was: scratch_mode ram, 1 holt job, in its row.
        Slots (the user's choice, 2026-11-23 evening): a big host has 2 RAM slots and 2 SSD
        slots (holt_jobs 4), a small one 1 and 1 (holt_jobs 2); choubox's holt_jobs 1 is one
        job, a RAM job or an SSD job (its class's slot of each); bigfoot's mode ram counts none."""
        small = dict(DEFAULTS, stic_slots=2, holt_jobs=2, base_gib=4, bs2_slots=2, scratch_mode="overflow",
                     holt_ram_slots=1, holt_ssd_slots=1)
        big = dict(DEFAULTS, bs2_slots=5, holt_disk_mode=True, holt_jobs=4, scratch_mode="overflow",
                   holt_ram_slots=2, holt_ssd_slots=2)
        choubox = dict(small, stic_slots=1, holt_jobs=1, base_gib=3, bs2_slots=1, max_wall_x=2)
        bigfoot = dict(small, holt_jobs=1, scratch_mode="ram")
        own = {"chopin": {"holt_disk_mode"}, "cinqcent": {"holt_disk_mode"},
               "choubox": {"stic_slots", "holt_jobs", "base_gib", "bs2_slots", "max_wall_x"},
               "bigfoot": {"holt_jobs", "scratch_mode"}}
        self.assertEqual(H.LIMITS, DEFAULTS)
        self.assertEqual([h for h in H.names(all_=True) if H.limit(h, "scratch_mode") == "overflow"],
                         ["chopin", "cinqcent", "copland", "cage", "czerny", "choubox"], "the six test hosts")
        self.assertEqual({h: H.limit(h, "holt_jobs") for h in H.names(all_=True)},
                         {"chopin": 4, "cinqcent": 4, "copland": 2, "cage": 2, "czerny": 2, "choubox": 1, "bigfoot": 1})
        self.assertEqual({h: (H.holt_scratch(h),) + H.holt_slots(h) for h in H.names(all_=True)},
                         {"chopin": ("overflow", 2, 2), "cinqcent": ("overflow", 2, 2), "copland": ("overflow", 1, 1),
                          "cage": ("overflow", 1, 1), "czerny": ("overflow", 1, 1), "choubox": ("overflow", 1, 1),
                          "bigfoot": ("ram", 1, 1)}, "MODE, RAM slots, SSD slots, as ht1-queue.sh reads them")
        for h in H.names(all_=True):   # (never more of a kind than the total; the six can run a job of either kind)
            self.assertLessEqual(max(H.holt_slots(h)), H.limit(h, "holt_jobs"), h)
        for r in H.rows(all_=True):
            want = {"choubox": choubox, "bigfoot": bigfoot}.get(r["name"], small if r["class"] == "small" else big)
            self.assertEqual(H.limits(r["name"]), want, r["name"])
            self.assertEqual(set(H.LIMITS) & set(r), own.get(r["name"], set()),
                             f"{r['name']}: a limit set in its row")
        self.assertEqual(H.limits("nosuch"), DEFAULTS)
        self.assertEqual(H.load()["class_limits"],
                         {"big": {"bs2_slots": 5, "holt_jobs": 4, "scratch_mode": "overflow", "holt_ram_slots": 2,
                                  "holt_ssd_slots": 2},
                          "small": {"stic_slots": 2, "holt_jobs": 2, "base_gib": 4, "bs2_slots": 2, "scratch_mode": "overflow",
                                    "holt_ram_slots": 1, "holt_ssd_slots": 1}})
        self.assertEqual(set(H.load()["limits"]), set(H.LIMITS), "hosts.json's limits describe each one")

    def test_row_then_class_then_default(self):
        self.use_table(table(class_limits={"small": {"stic_slots": 2, "holt_jobs": 1, "base_gib": 4}},
                             cage={"holt_jobs": 2, "holt_disk_mode": True}))
        self.assertEqual(H.limits("copland"), dict(DEFAULTS, stic_slots=2, holt_jobs=1, base_gib=4))
        self.assertEqual(H.limits("cage"), dict(DEFAULTS, stic_slots=2, holt_jobs=2, base_gib=4, holt_disk_mode=True))
        self.assertEqual(H.limits("cage.local"), H.limits("cage"))
        self.assertEqual(H.limits("czerny"), H.limits("copland"), "czerny: the small class's, like copland")
        self.assertEqual(H.limits("chopin"), DEFAULTS)
        self.assertEqual(H.limits("nosuch"), DEFAULTS)
        self.assertEqual(H.limit("cage", "holt_jobs"), 2)
        with self.assertRaises(H.HostsError):
            H.limit("cage", "nope")

    def test_bad_limits_are_refused(self):
        bad = [dict(cage={"stic_slots": 0}), dict(cage={"stic_slots": "3"}), dict(cage={"stic_slots": True}),
               dict(cage={"holt_jobs": 17}), dict(cage={"guest_slots": 4}), dict(cage={"base_gib": -1}),
               dict(cage={"base_gib": "6"}), dict(cage={"base_gib": True}), dict(cage={"holt_disk_mode": "yes"}),
               dict(cage={"holt_disk_mode": 1}), dict(class_limits={"tiny": {}}),
               dict(class_limits={"small": {"nope": 1}}), dict(class_limits={"small": {"holt_jobs": 0}}),
               dict(class_limits={"small": 3}), dict(class_limits=[]), dict(cage={"bs2_slots": 7}),
               dict(cage={"bs2_slots": 0}), dict(cage={"max_wall_x": 0.4}), dict(cage={"max_wall_x": 11}),
               dict(cage={"max_wall_x": "2"}), dict(cage={"max_wall_x": True}),
               dict(cage={"scratch_mode": "disk"}), dict(cage={"scratch_mode": True}), dict(cage={"scratch_mode": None}),
               dict(cage={"ssd_floor_gib": -1}), dict(cage={"ssd_floor_gib": "80"}), dict(cage={"ssd_floor_gib": True}),
               dict(class_limits={"small": {"ssd_cutoff_gib": 12}}),   # (gone 2026-11-23: no size sends a job to the SSD)
               dict(cage={"holt_ram_slots": -1}), dict(cage={"holt_ssd_slots": 17}), dict(cage={"holt_ram_slots": "2"}),
               dict(cage={"holt_ssd_slots": 1.5}), dict(cage={"holt_ram_slots": True}),
               dict(class_limits={"small": {"scratch_mode": "all-ssd"}})]
        for i, e in enumerate(bad):
            with self.subTest(e=e):
                with self.assertRaises(H.TableError):
                    H.load(self.use_table(table(**e), f"bad{i}.json"))
        ok = table(class_limits={"small": {"base_gib": 4.5, "guest_slots": 1, "max_wall_x": 1.5}},
                   cage={"base_gib": 0, "max_wall_x": None, "scratch_mode": "ssd", "ssd_floor_gib": 99.5},
                   copland={"max_wall_x": 2, "scratch_mode": "overflow"})
        H.load(self.use_table(ok, "ok.json"))
        self.assertEqual([H.scratch_mode(h) for h in ("cage", "copland", "chopin", "nosuch")], ["ssd", "overflow", "ram", "ram"])
        self.assertEqual([H.disk_scratch(h) for h in ("cage", "copland", "chopin", "nosuch")], [True, True, False, False])
        # the slots: null is holt_jobs, never more than holt_jobs; overflow without a kind's slots is the other's mode
        H.load(self.use_table(table(cage={"scratch_mode": "overflow", "holt_jobs": 4, "holt_ram_slots": 2, "holt_ssd_slots": 0},
                                    copland={"scratch_mode": "overflow", "holt_ram_slots": 0, "holt_ssd_slots": None},
                                    czerny={"scratch_mode": "overflow", "holt_jobs": 1, "holt_ram_slots": 5},
                                    chopin={"scratch_mode": "overflow", "holt_ram_slots": 0, "holt_ssd_slots": 0}), "slots.json"))
        self.assertEqual([(H.holt_scratch(h),) + H.holt_slots(h) for h in ("cage", "copland", "czerny", "chopin", "nosuch")],
                         [("ram", 2, 0), ("ssd", 0, H.limit("copland", "holt_jobs")), ("overflow", 1, 1), ("ram", 0, 0),
                          ("ram", 3, 3)])
        self.assertEqual([H.disk_scratch(h) for h in ("cage", "copland")], [False, True])

    def test_class_rank(self):
        self.assertEqual([H.class_rank(h) for h in ("chopin", "cinqcent", "copland", "cage", "czerny", "nosuch")],
                         [0, 0, 1, 1, 1, 0])

    def test_command_line_and_hosts_sh(self):
        p = self.use_table(table(cage={"base_gib": 4.5, "holt_disk_mode": True}))

        def cli(*a):
            r = subprocess.run([sys.executable, "-I", "-B", HOSTS_PY] + list(a), capture_output=True, text=True,
                               env=dict(os.environ, BKO_HOSTS_JSON=p))
            return r.returncode, r.stdout.strip()
        self.assertEqual(cli("limit", "cage", "base_gib"), (0, "4.5"))
        self.assertEqual(cli("limit", "cage.local", "holt_disk_mode"), (0, "true"))
        self.assertEqual(cli("limit", "chopin", "base_gib"), (0, "6"))
        self.assertEqual(cli("limit", "nosuch", "stic_slots"), (0, "3"), "a name in no row: the default")
        self.assertEqual(cli("limit", "cage", "nope")[0], 2)
        self.assertEqual(cli("limit", "cage")[0], 2)
        self.assertEqual(cli("limit", "cage", "base_gib", "--all")[0], 2)
        self.assertEqual(cli("get", "cage", "base_gib"), (0, "4.5"), "get: the row's own field")
        self.assertEqual(cli("get", "copland", "base_gib"), (0, ""), "get: not set there")
        if BASH:
            r = subprocess.run([BASH, "-c", f"source {HOSTS_SH}; hosts_limit cage base_gib; hosts_limit copland holt_jobs"],
                               capture_output=True, text=True, env=dict(os.environ, BKO_HOSTS_JSON=p))
            self.assertEqual(r.stdout.split(), ["4.5", "3"])


# --- cmp-run.sh's BASE_GIB (P14) -----------------------------------------------------------------

@unittest.skipUnless(BASH, "no bash")
class CmpRunBase(AltTable):
    """ceiling()'s and budget()'s BASE_GIB: the environment's, else the host's base_gib, else 6.
    (No /proc/meminfo here: ceiling()'s MEM is negative, but it moves with BASE_GIB all the same.)"""

    def setUp(self):
        super().setUp()
        self.bin = os.path.join(self.t, "bin")
        os.makedirs(self.bin)
        for name, text in (("mountpoint", "exit 0\n"), ("hostname", "echo cage\n"),
                           ("df", "echo 'Filesystem 1024-blocks Used Available Capacity Mounted on'\n"
                                  "echo 'tmpfs 10485760 0 10485760 0% /mnt/bko-ram'\n")):
            with open(os.path.join(self.bin, name), "w") as f:
                f.write("#!/bin/sh\n" + text)
            os.chmod(os.path.join(self.bin, name), 0o755)
        os.makedirs(os.path.join(self.t, "backotter-work"))
        os.makedirs(os.path.join(self.t, "ledger"))
        self.env = dict(os.environ, HOME=self.t, PATH=self.bin + os.pathsep + os.environ["PATH"],
                        CMP_LEDGER_DIR=os.path.join(self.t, "ledger"))
        self.env.pop("BASE_GIB", None)
        self.env.pop("BKO_HOSTS_PY", None)

    def ceiling(self, alone=False, **env):
        e = dict(self.env, **env)
        if alone:   # facts_host.py's CEILING_SH: the function by itself
            cmd = [BASH, "-c", 'eval "$(sed -n "/^ceiling()/,/^}/p" "$1")" && RAM=$2 && ceiling', "-", CMP_RUN,
                   "/mnt/bko-ram"]
        else:
            cmd = [BASH, CMP_RUN, "--ceiling"]
        p = subprocess.run(cmd, capture_output=True, text=True, env=e, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        d, m = p.stdout.split()
        return float(d), float(m)

    def test_default_is_six(self):
        p = self.use_table(table())
        self.assertEqual(self.ceiling(BKO_HOSTS_JSON=p), self.ceiling(BKO_HOSTS_JSON=p, BASE_GIB="6"))

    def test_the_hosts_base_gib(self):
        p = self.use_table(table(cage={"base_gib": 4}))
        six = self.ceiling(BKO_HOSTS_JSON=p, BASE_GIB="6")
        four = self.ceiling(BKO_HOSTS_JSON=p)
        self.assertEqual(four[0], six[0])
        self.assertAlmostEqual(four[1] - six[1], 2.0, places=5)
        self.assertAlmostEqual(self.ceiling(BKO_HOSTS_JSON=p, BASE_GIB="5")[1] - six[1], 1.0, places=5,
                               msg="BASE_GIB in the environment wins")
        # By itself (facts_host.py, drive.py's probe): the host table at BKO_HOSTS_PY, else
        # ~/backotter-work/testvm's (none here: 6).
        self.assertEqual(self.ceiling(alone=True, BKO_HOSTS_JSON=p, BKO_HOSTS_PY=HOSTS_PY), four)
        self.assertEqual(self.ceiling(alone=True, BKO_HOSTS_JSON=p), six)
        self.assertEqual(self.ceiling(alone=True, BKO_HOSTS_JSON=p, BKO_HOSTS_PY=HOSTS_PY, BASE_GIB="6"), six)

    def test_a_bad_value_is_six(self):
        p = self.use_table(table())
        self.assertEqual(self.ceiling(BKO_HOSTS_JSON=p, BASE_GIB="lots"), self.ceiling(BKO_HOSTS_JSON=p))
        broken = os.path.join(self.t, "broken.json")
        with open(broken, "w") as f:
            f.write("{")
        self.assertEqual(self.ceiling(BKO_HOSTS_JSON=broken), self.ceiling(BKO_HOSTS_JSON=p))

    def test_budget_takes_it_as_ceiling_does(self):
        """budget() can't run here (no /proc/meminfo); its BASE_GIB lines are ceiling()'s."""
        text = rd(CMP_RUN)

        def body(fn):
            return re.search(rf"^{fn}\(\) {{.*?^}}", text, re.S | re.M).group(0)
        look = re.compile(r'^    \[ -n "\$base" \] \|\| base=.*?\n    \[\[ \$base =~ .*?\]\] \|\| base=6$', re.S | re.M)
        self.assertEqual(look.search(body("budget")).group(0), look.search(body("ceiling")).group(0))
        for fn in ("budget", "ceiling"):
            self.assertIn('-v base="$base"', body(fn))
            self.assertNotIn("BASE_GIB:-6", body(fn))


# --- the planners: today's plans, and per-host figures (P13) -------------------------------------

def random_jobs(rng, hosts, n):
    jobs = []
    for i in range(n):
        allowed = None if rng.random() < 0.5 else rng.sample(hosts, rng.randint(1, len(hosts)))
        jobs.append((f"j{i:02d}", rng.choice([60, 300, 600, 900, 1800, 2400]), rng.choice([3, 6, 9, 13, 17]), allowed))
    return jobs


class Pack(unittest.TestCase):
    def test_same_as_before_with_the_defaults(self):
        old_text = before("testvm/test2/t2lib/estimates.py")
        if old_text is None:
            self.skipTest(f"no commit {BEFORE} here")
        old = types.ModuleType("old_estimates")
        exec(compile(old_text, "old_estimates.py", "exec"), old.__dict__)
        rng = random.Random(7)
        hosts = ["chopin", "cinqcent"]
        ranks = {h: H.class_rank(h) for h in hosts}
        slots = {h: H.LIMITS["holt_jobs"] for h in hosts}   # (the defaults: the real table's are 4 since 2026-11-23)
        for _ in range(200):
            jobs = random_jobs(rng, hosts, rng.randint(0, 14))
            want = old.pack(jobs, hosts)
            self.assertEqual(estimates.pack(jobs, hosts), want)
            self.assertEqual(estimates.pack(jobs, hosts, slots=slots, ranks=ranks), want, jobs)
            self.assertEqual(estimates.pack(jobs, hosts, slots=2), old.pack(jobs, hosts, slots=2))

    def test_per_host_slots(self):
        jobs = [(f"j{i}", 600, 6, None) for i in range(6)]
        wall, place, left = estimates.pack(jobs, ["big", "small"], slots={"big": 3, "small": 1})
        self.assertEqual(sum(1 for h in place.values() if h == "small"), 2, place)   # 3+1 at once, twice
        self.assertEqual((wall, left), (1200, []))
        wall, place, _ = estimates.pack(jobs, ["big", "small"], slots={"big": 3})   # a host it lacks: HOLT_JOBS
        self.assertEqual(wall, 600)

    def test_big_only_cells_start_first_on_the_big_hosts(self):
        # j-big may run on big only and is the shortest; the others may run anywhere.
        jobs = [("a", 1800, 6, None), ("b", 1700, 6, None), ("big", 600, 6, ["big"])]
        ranks = {"big": 0, "small": 1}
        wall, place, _ = estimates.pack(jobs, ["big", "small"], slots={"big": 1, "small": 1}, ranks=ranks)
        self.assertEqual(place["big"], "big")
        self.assertEqual(wall, 600 + 1700, "big first on big, a on small, then b on big")
        w0, _, _ = estimates.pack(jobs, ["big", "small"], slots={"big": 1, "small": 1})
        self.assertGreater(w0, wall, "without classes the longest go first and big waits")


class Simulate(unittest.TestCase):
    def test_same_as_before_with_the_defaults(self):
        old_text = before("testvm/stictest1/drive.py")
        if old_text is None:
            self.skipTest(f"no commit {BEFORE} here")
        d = tempfile.mkdtemp(prefix="t2-olddrive-")
        self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(d, "drive.py"), "w") as f:
            f.write(old_text)
        old = load_module("hl_old_drive", os.path.join(d, "drive.py"))
        S = DRIVE.Spec
        rng = random.Random(11)
        oses = ["linux", "freebsd", "netbsd"]
        for _ in range(100):
            n = rng.randint(1, 12)
            spec = {f"g{i:02d}": S(rng.choice(oses), rng.choice([2, 3, 6, 13]), rng.choice([2, 4, 6]),
                                   rng.choice([1, 1, 1, 3]), rng.random() > 0.1) for i in range(n)}
            est = {g: rng.choice([60, 300, 600, 900]) for g in spec}
            have = lambda: {f"{o}/{x}" for o in rng.sample(oses, rng.randint(1, 3)) for x in ("restic-bko", "rustic-bko")}
            hosts = {h: dict(have=have(), slots_free=3, room=rng.choice([None, (26, 20), (12, 9)]),
                             ceil=rng.choice([None, (29.0, 23.0)])) for h in ("chopin", "cinqcent")}
            first = set(rng.sample(sorted(spec), rng.randint(0, min(2, n))))
            want = old.simulate(list(spec), spec, est, copy.deepcopy(hosts), 3, first)
            self.assertEqual(DRIVE.simulate(list(spec), spec, est, copy.deepcopy(hosts), 3, first), want)
            self.assertEqual(DRIVE.simulate(list(spec), spec, est, copy.deepcopy(hosts),
                                            DRIVE.parse_slots([], list(hosts)), first), want)

    def test_stic_wall_per_host(self):
        guests = ["debian13", "alpine324", "fedora44", "mint22"]
        guests = [g for g in guests if os.path.isfile(os.path.join(TESTVM, "stictest1", g, "check.sh"))]
        if len(guests) < 2:
            self.skipTest("too few guests here")
        have = {h: {f"linux/{x}" for x in ("restic-bko", "rustic-bko")} for h in ("chopin", "copland")}
        est = {g: 600 for g in guests}
        w3, _, _ = estimates.stic_wall(DRIVE, guests, est, have)
        w1, place, left = estimates.stic_wall(DRIVE, guests, est, have, slots={"chopin": 1, "copland": 1},
                                              guest_slots={"chopin": 3, "copland": 1},
                                              ranks={"chopin": 0, "copland": 1})
        self.assertEqual(left, [])
        self.assertGreaterEqual(w1, w3)
        self.assertEqual(set(place.values()) <= {"chopin", "copland"}, True)


# --- placement and the holt runner (P15, MAXJOBS) ------------------------------------------------

def fake_planner(facts, need):
    p = object.__new__(plan.Planner)
    p.ctx = types.SimpleNamespace(facts=facts)
    p.holt_need = lambda v, h: need[h] if isinstance(need, dict) else need
    return p


def view(hosts, variant=None):
    return types.SimpleNamespace(hosts=list(hosts), why_not=None, name="mint22", variant=variant,
                                 pc=types.SimpleNamespace(host="any", reasons_extra=None))


def facts_with(ceil):
    return {"preflight": {"ram": {"ceiling": list(ceil)}}}


class DiskMode(AltTable):
    FACTS = {"chopin": facts_with((29.0, 23.0)), "cage": facts_with((9.0, 9.2)), "copland": facts_with((9.0, 9.2))}

    def place(self, need, variant=None):
        p = fake_planner(self.FACTS, need)
        v = view(["chopin", "cage", "copland"], variant)
        p.fit_ceilings(v, lambda h: p.holt_need_here(v, h))
        return v

    def test_off_by_default(self):
        self.use_table(table())
        v = self.place((17.0, 4.0))
        self.assertEqual(v.hosts, ["chopin"])
        self.assertIn("cage", " ".join(v.pc.reasons_extra))
        self.assertEqual(holt.disk_mode("cage"), False)

    def test_scratch_modes(self):
        """RAM first, the SSD for overflow (2026-11-23).  overflow: a cell over the RAM-disk
        ceiling is the SSD's (only its memory is held to the ceiling); one that fits the ceiling
        stays the RAM disk's, whatever its size (no cutoff: ht1-queue.sh's place moves it to
        the SSD only when the RAM disk has no room for it as it starts).  ssd: every cell.
        ram: none.  Never +img, and only with room for the cell's D above the host's
        ssd_floor_gib (the facts' free space)."""
        facts = {h: dict(f, preflight=dict(f["preflight"], scratch_free_gb=300.0)) for h, f in self.FACTS.items()}
        self.use_table(table(class_limits={"big": {"scratch_mode": "overflow"}, "small": {"scratch_mode": "overflow"}},
                             copland={"scratch_mode": "ram"}))
        v = view(["chopin", "cage", "copland"])

        def need(d, m, h, f=facts, variant=None):
            return fake_planner(f, (d, m)).holt_need_here(view(["chopin", "cage", "copland"], variant), h)
        self.assertEqual(need(17.0, 4.0, "cage"), (0.0, 4.0), "over cage's RAM disk")
        self.assertEqual(need(17.0, 4.0, "chopin"), (17.0, 4.0), "under chopin's ceiling: the RAM disk's, though over 12")
        self.assertEqual(need(27.0, 4.0, "chopin"), (27.0, 4.0), "truenas25 on a big host: it fits the RAM disk")
        self.assertEqual(need(30.0, 4.0, "chopin"), (0.0, 4.0), "vanilla3: over chopin's RAM disk too")
        self.assertEqual(need(12.0, 4.0, "chopin"), (12.0, 4.0))
        self.assertEqual(need(6.0, 4.0, "cage"), (6.0, 4.0))
        self.assertEqual(need(17.0, 4.0, "copland"), (17.0, 4.0), "copland put back to ram: as before")
        self.assertEqual(need(17.0, 4.0, "cage", variant="img"), (17.0, 4.0), "+img: only in the RAM disk")
        p = fake_planner(facts, (17.0, 4.0))
        p.fit_ceilings(v, lambda h: p.holt_need_here(v, h))
        self.assertEqual(v.hosts, ["chopin", "cage"])
        # a host short of free SSD gets no overflow: 100 GB is 93.1 GiB, 13.1 above the floor of 80
        short = dict(facts, cage=dict(facts["cage"], preflight=dict(facts["cage"]["preflight"], scratch_free_gb=100.0)))
        self.assertEqual(need(17.0, 4.0, "cage", short), (17.0, 4.0))
        self.assertEqual(need(13.0, 4.0, "cage", short), (0.0, 4.0))
        self.assertEqual(need(17.0, 4.0, "cage", self.FACTS), (0.0, 4.0), "free space not known: not checked")
        self.use_table(table(cage={"scratch_mode": "ssd"}, chopin={"scratch_mode": "overflow"}), "t2.json")
        self.assertEqual(need(2.0, 4.0, "cage"), (0.0, 4.0), "ssd: every cell")
        self.assertEqual(need(2.0, 4.0, "cage", variant="img"), (2.0, 4.0))
        self.assertEqual(need(5.0, 4.0, "chopin"), (5.0, 4.0))
        # the slots change no cell's place in the plan (a cell that fits the ceiling is the RAM disk's, one over
        # it the SSD's), but overflow with no SSD slot plans as mode ram, and with no RAM slot as mode ssd
        self.use_table(table(cage={"scratch_mode": "overflow", "holt_ram_slots": 1, "holt_ssd_slots": 1},
                             copland={"scratch_mode": "overflow", "holt_ram_slots": 1, "holt_ssd_slots": 0},
                             chopin={"scratch_mode": "overflow", "holt_ram_slots": 0, "holt_ssd_slots": 2}), "t4.json")
        self.assertEqual([need(17.0, 4.0, "cage"), need(6.0, 4.0, "cage")], [(0.0, 4.0), (6.0, 4.0)])
        self.assertEqual(need(17.0, 4.0, "copland"), (17.0, 4.0), "no SSD slot: held to the RAM-disk ceiling, as mode ram")
        self.assertEqual(need(5.0, 4.0, "chopin"), (0.0, 4.0), "no RAM slot: every cell the SSD's, as mode ssd")
        with self.assertRaises(H.TableError):   # (no such limit any more)
            H.load(self.use_table(table(chopin={"scratch_mode": "overflow", "ssd_cutoff_gib": 4}), "t3.json"))

    def test_on_for_a_host_its_memory_only(self):
        self.use_table(table(cage={"holt_disk_mode": True}))
        v = self.place((17.0, 4.0))
        self.assertEqual(v.hosts, ["chopin", "cage"], "cage: on /bkoscratch, --reserve 0 4")
        p = fake_planner(self.FACTS, (17.0, 4.0))
        self.assertEqual(p.holt_need_here(v, "cage"), (0.0, 4.0))
        self.assertEqual(p.holt_need_here(v, "copland"), (17.0, 4.0))
        self.assertEqual(p.holt_need_here(v, "chopin"), (17.0, 4.0), "under chopin's ceiling: in RAM")
        self.assertEqual(fake_planner(self.FACTS, (6.0, 4.0)).holt_need_here(v, "cage"), (6.0, 4.0),
                         "a line that fits the RAM disk stays in RAM")
        v = self.place((17.0, 9.5))
        self.assertEqual(v.hosts, ["chopin"], "its memory is still held to the ceiling")
        self.assertIsNone(fake_planner(self.FACTS, None).holt_need_here(v, "cage"))
        self.assertTrue(holt.disk_mode("cage") and not holt.disk_mode("copland"))

    def test_img_stays_held_to_the_ram_disk(self):
        """ht1.sh runs +img only in the RAM disk (not HT_RAM=0): disk mode doesn't take it."""
        self.use_table(table(cage={"holt_disk_mode": True}))
        v = self.place((17.0, 4.0), "img")
        self.assertEqual(v.hosts, ["chopin"], "+img over cage's RAM disk: kept off cage")
        self.assertIn("cage", " ".join(v.pc.reasons_extra))
        self.assertEqual(fake_planner(self.FACTS, (17.0, 4.0)).holt_need_here(v, "cage"), (17.0, 4.0))
        self.assertEqual(self.place((6.0, 4.0), "img").hosts, ["chopin", "cage", "copland"],
                         "a +img that fits the RAM disk still goes anywhere")
        self.assertEqual(self.place((17.0, 4.0), "bigger").hosts, ["chopin", "cage"], "other variants: on /bkoscratch")


# --- max_wall_x: the long and the slow cells kept off a slow host (2026-11-18) ------------------

T_NOW = 1_800_000_000


def utc(age_h):
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(T_NOW - age_h * 3600))


def wall_row(cell, host, wall, verdict="PASS", age_h=1.0, waits=0):
    return {"cell": cell, "host": host, "kind": "run", "verdict": verdict, "wall_s": wall, "waits_s": waits,
            "t": utc(age_h), "id": f"{host}-{cell}-{wall}-{age_h}"}


def wall_planner(rows, est=300):
    p = object.__new__(plan.Planner)
    p.ctx = types.SimpleNamespace(now=T_NOW, facts={})
    p.rows_by_cell = {}
    for r in rows:
        p.rows_by_cell.setdefault(r["cell"], []).append(r)
    p.estimator = lambda: types.SimpleNamespace(cell=lambda c: {"wall_s": est})
    return p


def wall_view(cell, hosts=("chopin", "cage", "copland")):
    fam, name = cell.split(":", 1)
    return types.SimpleNamespace(cell=cell, family=fam, name=name, variant=None, hosts=list(hosts), why_not=None,
                                 pc=types.SimpleNamespace(host="any", reasons_extra=None))


# the guest family elsewhere: ten cells of 100 s on chopin and copland, so its median is 100 s
FAMILY = [wall_row(f"guest:g{i}", h, 100) for i in range(10) for h in ("chopin", "copland")]


class MaxWall(AltTable):
    def place(self, cell, rows, hosts=("chopin", "cage", "copland"), est=300):
        p = wall_planner(FAMILY + rows, est)
        v = wall_view(cell, hosts)
        p.fit_wall(v)
        return v

    def test_off_without_a_cap(self):
        self.use_table(table())
        v = self.place("guest:long", [wall_row("guest:long", "chopin", 900)])
        self.assertEqual(v.hosts, ["chopin", "cage", "copland"])
        self.assertIsNone(v.pc.reasons_extra)

    def test_a_long_cell_goes_elsewhere(self):
        self.use_table(table(cage={"max_wall_x": 2}))
        v = self.place("guest:long", [wall_row("guest:long", "chopin", 250), wall_row("guest:long", "copland", 210),
                                      wall_row("guest:long", "chopin", 230, "FAIL")])
        self.assertEqual(v.hosts, ["chopin", "copland"])
        self.assertEqual(v.pc.reasons_extra,
                         ["kept off cage: ~230 s elsewhere, over its max_wall_x 2 x the guest median 100 s"])
        v = self.place("guest:short", [wall_row("guest:short", "chopin", 190)])
        self.assertEqual(v.hosts, ["chopin", "cage", "copland"], "under twice the median: stays")
        v = self.place("guest:long", [wall_row("guest:long", "chopin", 250, waits=60)])
        self.assertEqual(v.hosts, ["chopin", "cage", "copland"], "a wall less its waits (190 s)")

    def test_the_slow_hosts_own_rows_never_make_it_long(self):
        self.use_table(table(cage={"max_wall_x": 2}))
        v = self.place("guest:mid", [wall_row("guest:mid", "cage", 280), wall_row("guest:mid", "chopin", 150)])
        self.assertEqual(v.hosts, ["chopin", "cage", "copland"],
                         "150 s elsewhere (with cage's 280 s it would be 215 s, over 200); 280 s under 2 x 150 s")
        v = self.place("guest:mid", [wall_row("guest:mid", "cage", 400, "HARNESS"), wall_row("guest:mid", "chopin", 199),
                                     wall_row("guest:mid", "chopin", 900, "HARNESS"), wall_row("guest:mid", "chopin", 900, "LOST")])
        self.assertEqual(v.hosts, ["chopin", "cage", "copland"], "HARNESS and LOST rows measure no wall")

    def test_a_cell_the_host_is_slow_at_goes_elsewhere(self):
        self.use_table(table(cage={"max_wall_x": 2}))
        slow = [wall_row("guest:slow", "cage", w, age_h=a) for w, a in ((330, 1), (310, 2), (120, 3))] + [
            wall_row("guest:slow", "chopin", 140)]
        v = self.place("guest:slow", slow)
        self.assertEqual(v.hosts, ["chopin", "copland"])
        self.assertEqual(v.pc.reasons_extra,
                         ["kept off cage: ~310 s there lately, over its max_wall_x 2 x its ~140 s elsewhere"])
        old = [dict(r, t=utc(15 * 24 + 1)) if r["host"] == "cage" else r for r in slow]
        self.assertEqual(self.place("guest:slow", old).hosts, ["chopin", "cage", "copland"],
                         "rows older than 14 days: tried there again")
        tiny = [wall_row("guest:tiny", "cage", 90), wall_row("guest:tiny", "chopin", 20)]
        self.assertEqual(self.place("guest:tiny", tiny).hosts, ["chopin", "cage", "copland"],
                         "slow there but under the family's median: a minute is no reason")

    def test_the_estimate_when_nothing_was_measured(self):
        self.use_table(table(cage={"max_wall_x": 2}))
        self.assertEqual(self.place("guest:new", [], est=300).hosts, ["chopin", "copland"])
        self.assertEqual(self.place("guest:new", [], est=150).hosts, ["chopin", "cage", "copland"])

    def test_never_strands_a_cell(self):
        self.use_table(table(cage={"max_wall_x": 2}, copland={"max_wall_x": 2}))
        rows = [wall_row("guest:long", "chopin", 900)]
        self.assertEqual(self.place("guest:long", rows, hosts=("cage",)).hosts, ["cage"], "--hosts cage: runs there")
        self.assertEqual(self.place("guest:long", rows, hosts=("cage", "copland")).hosts, ["cage", "copland"],
                         "every host capped: the cap moves nothing")
        v = self.place("guest:long", rows)
        self.assertEqual((v.hosts, v.pc.host), (["chopin"], "chopin"))

    def test_holt_cells_by_their_own_family(self):
        self.use_table(table(cage={"max_wall_x": 2}))
        rows = [wall_row(f"holt:h{i}", "chopin", 300) for i in range(5)] + [wall_row("holt:big", "copland", 500)]
        p = wall_planner(FAMILY + rows)
        v = wall_view("holt:big")
        p.fit_wall(v)
        self.assertEqual(v.hosts, ["chopin", "cage", "copland"], "500 s: under 2 x the holt median (300 s)")
        self.assertEqual(p.family_wall("holt", "cage"), 300)
        self.assertEqual(p.family_wall("guest", "cage"), 100)


class FakeRunner(holt.Runner):
    def __init__(self, hosts):   # noqa: D107 - only what start_queue and queue_env use
        class State(dict):
            def save(self):
                pass
        self.hosts, self.qhost, self.run_id = list(hosts), hosts[0], "t2h-x"
        self.state = State(queues={}, all_img=False, pin_bad={})
        self.cls, self.problems, self.pin_bad = {}, [], None
        self.started = []
        runner = self

        class Remote:
            def start(self, host, cmd, out, pidfile):
                runner.started.append((host, cmd))
                return 4242
        self.remote = Remote()
        self.rs = types.SimpleNamespace(host_run_dir=lambda h: f"/hr/runs/{self.run_id}")

    def host_root(self):
        return "/hr"

    def holt_dir(self):
        return "/hr/holt-bin/c0de"

    def tool(self, host, name):
        return f"/copy/testvm/holttest1d/{name}"

    def log(self, text):
        pass


class Runners(AltTable):
    def start(self, hosts):
        r = FakeRunner(hosts)
        r.state["queues"]["q1"] = {"todo_written": "x", "todo": []}
        r.start_queue("q1", [], "cells.json")
        return {h: cmd for h, cmd in r.started}

    def test_maxjobs_3_everywhere_without_class_limits(self):
        self.use_table(table())
        cmds = self.start(["chopin", "cinqcent", "copland", "cage"])
        for h, cmd in cmds.items():
            self.assertRegex(cmd, r"ht1-queue\.sh q1 2600 3( chopin\.local)?$", h)
            self.assertNotIn("DISK_OVER", cmd)
            self.assertIn("HT_RAM=1", cmd)
        self.assertEqual([holt.max_jobs(h) for h in ("chopin", "cage", "nosuch")], [3, 3, 3])

    def test_per_host_maxjobs_and_disk_over(self):
        self.use_table(table(class_limits={"small": {"holt_jobs": 1}}, cage={"holt_jobs": 2, "holt_disk_mode": True}))
        cmds = self.start(["chopin", "cinqcent", "copland", "cage"])
        self.assertRegex(cmds["chopin"], r"ht1-queue\.sh q1 2600 3$")
        self.assertRegex(cmds["cinqcent"], r"ht1-queue\.sh q1 2600 3 chopin\.local$")
        self.assertRegex(cmds["copland"], r"ht1-queue\.sh q1 2600 1 chopin\.local$")
        self.assertRegex(cmds["cage"], r"ht1-queue\.sh q1 2600 2 chopin\.local$")
        self.assertIn(" DISK_OVER=1 ", cmds["cage"])
        self.assertIn(" HT_RAM=1 ", cmds["cage"], "the rest of cage's lines stay in RAM")
        self.assertTrue(all("DISK_OVER" not in c for h, c in cmds.items() if h != "cage"))

    def test_the_queue_takes_disk_over(self):
        """DISK_OVER is no HT_ setting (those refuse the start), and ht1-queue.sh reads it."""
        text = rd(QUEUE)
        self.assertIn('DISK_OVER=${DISK_OVER:-0}', text)
        self.assertRegex(text, r'\[\[ \$DISK_OVER == \[01\] \]\] \|\| die')
        self.assertIn('HT_RAM="$jram"', text)
        self.assertNotRegex(text, r"HT_DISK_OVER")


def flock_bash():
    from test_holt import modern_bash
    b = modern_bash()
    return b if b and shutil.which("flock") else None


@unittest.skipUnless(flock_bash(), "needs bash 4 or newer with timeout, realpath and flock (the test hosts')")
class QueueDiskOver(unittest.TestCase):
    """cant_scan with DISK_OVER=1: a line over the RAM disk whose memory fits is runnable here."""

    def test_cant_scan(self):
        from test_runner_hosts import QueueShell
        qs = QueueShell("test_cant_scan")
        qs.setUp()
        self.addCleanup(qs.doCleanups)
        script = qs.scan_script('cant_scan; printf "%s\\n" "${CANT[@]}"; echo "--"\n'
                                'DISK_OVER=1; cant_scan; printf "%s\\n" "${CANT[@]}"\n')
        with open(os.path.join(qs.q, "todo"), "w") as f:
            f.write("debian13\nmint22=17%4\nmint22+bigger=17%9.5\nmint22+img=17%4\n")
        p = subprocess.run([flock_bash(), "-c", script], capture_output=True, text=True, timeout=120)
        self.assertEqual(p.stderr, "")
        off, on = p.stdout.split("--\n")
        self.assertEqual(off.splitlines(), ["=mint22=17%4", "=mint22+bigger=17%9.5", "=mint22+img=17%4"])
        self.assertEqual(on.splitlines(), ["=mint22+bigger=17%9.5", "=mint22+img=17%4"],
                         "+img: only in the RAM disk (ht1.sh), so DISK_OVER doesn't take it")


if __name__ == "__main__":
    unittest.main()
