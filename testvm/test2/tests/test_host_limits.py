"""The hosts' limits in the host table (gaps P13, P14, P15): stic_slots, guest_slots, holt_jobs,
base_gib, holt_disk_mode and bs2_slots, each a row's field, else the table's class_limits, else the
default (3, 3, 3, 6, false, 3).  With the table as it is every host has the defaults, and the planners give
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
DEFAULTS = {"stic_slots": 3, "guest_slots": 3, "holt_jobs": 3, "base_gib": 6, "holt_disk_mode": False, "bs2_slots": 3}


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
        2 on a small one. No row sets a limit of its own but chopin's and cinqcent's
        holt_disk_mode (the user's choice, 2026-11-14, for vanilla3's 30 GiB disk)."""
        small = dict(DEFAULTS, stic_slots=2, holt_jobs=1, base_gib=4, bs2_slots=2)
        big = dict(DEFAULTS, bs2_slots=5, holt_disk_mode=True)
        self.assertEqual(H.LIMITS, DEFAULTS)
        for r in H.rows(all_=True):
            self.assertEqual(H.limits(r["name"]), small if r["class"] == "small" else big, r["name"])
            self.assertEqual(set(H.LIMITS) & set(r), {"holt_disk_mode"} if r["name"] in ("chopin", "cinqcent") else set(),
                             f"{r['name']}: a limit set in its row")
        self.assertEqual(H.limits("nosuch"), DEFAULTS)
        self.assertEqual(H.load()["class_limits"], {"big": {"bs2_slots": 5},
                                                    "small": {"stic_slots": 2, "holt_jobs": 1, "base_gib": 4, "bs2_slots": 2}})
        self.assertEqual(set(H.load()["limits"]), set(H.LIMITS), "hosts.json's limits describe each one")

    def test_row_then_class_then_default(self):
        self.use_table(table(class_limits={"small": {"stic_slots": 2, "holt_jobs": 1, "base_gib": 4}},
                             cage={"holt_jobs": 2, "holt_disk_mode": True}))
        self.assertEqual(H.limits("copland"), dict(DEFAULTS, stic_slots=2, holt_jobs=1, base_gib=4))
        self.assertEqual(H.limits("cage"), dict(DEFAULTS, stic_slots=2, holt_jobs=2, base_gib=4, holt_disk_mode=True))
        self.assertEqual(H.limits("cage.local"), H.limits("cage"))
        self.assertEqual(H.limits("czerny"), H.limits("copland"), "a reserved row: its class's still")
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
               dict(cage={"bs2_slots": 0})]
        for i, e in enumerate(bad):
            with self.subTest(e=e):
                with self.assertRaises(H.TableError):
                    H.load(self.use_table(table(**e), f"bad{i}.json"))
        ok = table(class_limits={"small": {"base_gib": 4.5, "guest_slots": 1}}, cage={"base_gib": 0})
        H.load(self.use_table(ok, "ok.json"))

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
        slots = {h: H.limit(h, "holt_jobs") for h in hosts}
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

    def test_off_today(self):
        v = self.place((17.0, 4.0))
        self.assertEqual(v.hosts, ["chopin"])
        self.assertIn("cage", " ".join(v.pc.reasons_extra))
        self.assertEqual(holt.disk_mode("cage"), False)

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
