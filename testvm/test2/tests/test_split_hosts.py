"""More hosts sharing the work (P16, P18, P20, P21, P23, P24): run-all.sh's SPLIT deals a
round over the hosts with no step twice; cmp-pop.sh's nodisk keeps "!" lines off a worker
without DISKS_DIR; a gate run that waited for a layer's lock adopts the other run's PASS of
the same cell, commit and key; bs2's --hosts; ram-size.sh's class:C rows and --host H.DOMAIN;
drive.py's ssh by ip when the name fails, and its temperature from coretemp too.  Without
the new settings nothing changes: those cases are here too."""

import fcntl
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
TESTVM = os.path.dirname(T2)
REPO = os.path.dirname(TESTVM)
sys.path.insert(0, T2)

from t2lib import gate, utc_now  # noqa: E402
from t2lib import hosts as H  # noqa: E402

RUN_ALL = os.path.join(TESTVM, "compare", "run-all.sh")
CMP_POP = os.path.join(TESTVM, "compare", "cmp-pop.sh")
CMP_RUN = os.path.join(TESTVM, "compare", "cmp-run.sh")
RAM_SIZE = os.path.join(TESTVM, "holttest1d", "ram-size.sh")
HT1_QUEUE = os.path.join(TESTVM, "holttest1d", "ht1-queue.sh")
ALL_LFS = {"zfs", "zfs-onto-ext4", "bcachefs", "f2fs", "ext4", "xfs", "btrfs", "tmpfs", "jfs", "nonroot"}
ALL_SFS = {"ext4", "xfs", "btrfs", "zfs", "zfs-nocomp"}
GUEST_PLATS = {"zfsguests": ("ubuntu24", "ubuntu26-zfsmirror"), "dragonfly": ("dragonfly6",),
               "openbsd_illumos": ("openbsd8", "omnios58"), "freebsd": ("freebsd15", "freebsd15-ufs"),
               "netbsd": ("netbsd11", "alpinebsd")}


def load(name, path, env=None):
    with mock.patch.dict(os.environ, env or {}):
        loader = importlib.machinery.SourceFileLoader(name, path)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        m = importlib.util.module_from_spec(spec)
        loader.exec_module(m)
    return m


class AltTable:
    """A changed copy of testvm/hosts.json (BKO_HOSTS_JSON)."""

    def __init__(self, change):
        with open(os.path.join(TESTVM, "hosts.json"), encoding="utf-8") as f:
            t = json.load(f)
        change(t)
        self.d = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.d.name, "hosts.json")
        with open(self.path, "w") as f:
            json.dump(t, f)

    def env(self):
        return {H.ENV: self.path}

    def close(self):
        self.d.cleanup()


def row_of(t, name):
    return next(r for r in t["hosts"] if r["name"] == name)


# --- run-all.sh SPLIT (P16) ----------------------------------------------------------------

class RunAllSplit(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.d, "work", "vms"))
        for o in ("linux", "dragonfly", "freebsd", "netbsd", "openbsd", "illumos"):
            os.makedirs(os.path.join(self.d, "bin", o))
            for t in ("restic", "rustic"):
                with open(os.path.join(self.d, "bin", o, f"{t}-bko"), "w") as f:
                    f.write("x\n")

    def tearDown(self):
        shutil.rmtree(self.d)

    def guests(self, *gs):
        for g in gs:
            os.makedirs(os.path.join(self.d, "work", "vms", g), exist_ok=True)

    def run_all(self, host, env=None, args=()):
        e = {k: v for k, v in os.environ.items()
             if k not in ("LINUX_FS", "SPARSE_FS", "PORT_BASE", "MAX_GUESTS", "COUNT_OTHERS", "GUEST_PORTS", "SPLIT",
                          "ANY_HOST", "STEP_TIMEOUT", "HOST_JOBS", "SPARSE_JOBS", "JOBS_CAP", "ALLOW_SKIP", H.ENV)}
        e.update(BKO_WORK=os.path.join(self.d, "work"), OLD_BIN="none", UNCHECKED_BINARIES="1", **(env or {}))
        return subprocess.run(["bash", RUN_ALL, "--host", host, "--dry-run", *args, os.path.join(self.d, "bin"),
                               os.path.join(self.d, "log")], capture_output=True, text=True, env=e, timeout=120)

    def share(self, host, env=None, args=()):
        """(rc, linux set, sparse set, platforms with steps, lost [(platform, step)], stdout)."""
        r = self.run_all(host, env, args)
        lfs = sfs = None
        for line in r.stdout.splitlines():
            m = re.match(rf"== {host}: ports .*; linux: (.*); sparse: (.*)$", line)
            if m:
                lfs, sfs = set(m.group(1).split()), set(m.group(2).split())
        plats = {m.group(1) for m in re.finditer(r"^-- ([a-z_]+)/", r.stdout, re.M)}
        lost = re.findall(r"^not run \(a FAIL row\): (\S+)\t(\S+)\t", r.stdout, re.M)
        return r.returncode, lfs, sfs, plats, lost, r.stdout + r.stderr

    def test_four_hosts_share_every_step_once(self):
        self.guests(*[g for gs in GUEST_PLATS.values() for g in gs])
        hosts = ("chopin", "cinqcent", "copland", "cage")
        seen = {"linux": [], "sparse": [], "plats": []}
        for h in hosts:
            rc, lfs, sfs, plats, lost, out = self.share(h, {"SPLIT": "chopin,cinqcent,copland,cage",
                                                            "ALLOW_SKIP": "sparse-netbsd11"})
            self.assertEqual(rc, 0, out)
            self.assertEqual(lost, [], out)
            self.assertIn("== SPLIT chopin cinqcent copland cage: " + h + "'s share:", out)
            seen["linux"] += sorted(lfs)
            seen["sparse"] += sorted(sfs)
            seen["plats"] += sorted(plats & set(GUEST_PLATS))
            if H.host_class(h) == "small":   # no host ZFS: none of ZFS's steps
                self.assertFalse(lfs & {"zfs", "zfs-onto-ext4", "nonroot", "bcachefs"}, (h, lfs))
                self.assertFalse(sfs & {"zfs", "zfs-nocomp"}, (h, sfs))
                self.assertIn("1 host jobs, 1 sparse jobs", out)   # JOBS_CAP as before
            if h == "chopin":
                self.assertIn("bcachefs", lfs)
            if h == "cinqcent":
                self.assertIn("netbsd", plats)
        for k, want in (("linux", ALL_LFS), ("sparse", ALL_SFS), ("plats", set(GUEST_PLATS))):
            self.assertEqual(sorted(seen[k]), sorted(want), k)   # each once, none left out

    def test_the_deal_is_the_same_whatever_the_order(self):
        a = self.share("copland", {"SPLIT": "cage copland chopin.local cinqcent"})
        b = self.share("copland", {"SPLIT": "chopin,cinqcent,copland,cage"})
        self.assertEqual(a[:4], b[:4])
        c = self.share("copland", {"SPLIT": "all"})
        self.assertEqual(c[:4], b[:4])

    def test_two_hosts_and_the_port_clash(self):
        self.guests(*[g for gs in GUEST_PLATS.values() for g in gs])
        got = {}
        for h in ("chopin", "cinqcent"):
            rc, lfs, sfs, plats, lost, out = self.share(h, {"SPLIT": "chopin,cinqcent", "ALLOW_SKIP": "sparse-netbsd11"})
            self.assertEqual((rc, lost), (0, []), out)
            got[h] = (lfs, sfs, plats & set(GUEST_PLATS))
        self.assertEqual(got["chopin"][0] | got["cinqcent"][0], ALL_LFS)
        self.assertFalse(got["chopin"][0] & got["cinqcent"][0])
        for h in got:   # freebsd and dragonfly both take PORT_BASE+10 and +11: never one host
            self.assertFalse({"freebsd", "dragonfly"} <= got[h][2], got)

    def test_small_hosts_alone_lose_zfs_on_the_first_only(self):
        rc, lfs, sfs, plats, lost, out = self.share("copland", {"SPLIT": "copland,cage"})
        self.assertEqual(rc, 0, out)
        want = {("linux", n) for n in ("zfs", "zfs-onto-ext4", "nonroot", "bcachefs")} | \
            {("sparse", "sparse-zfs"), ("sparse", "sparse-zfs-nocomp"), ("netbsd", "all")}
        self.assertTrue(want <= set(lost), lost)
        self.assertIn("it needs a big host", out)
        rc, lfs2, sfs2, _, lost2, out = self.share("cage", {"SPLIT": "copland,cage"})
        self.assertEqual(rc, 0, out)
        self.assertFalse({x for x in lost2 if x[1] in ("zfs", "all") or "zfs" in x[1]}, lost2)
        self.assertEqual(lfs | lfs2, ALL_LFS - {"zfs", "zfs-onto-ext4", "nonroot", "bcachefs"})
        self.assertFalse(lfs & lfs2)

    def test_a_guest_missing_here_is_a_lost_step(self):
        rc, _, _, plats, lost, out = self.share("cage", {"SPLIT": "all"})
        self.assertEqual(rc, 0, out)
        mine = {p for p, _ in lost}
        self.assertTrue(mine and mine <= set(GUEST_PLATS), lost)
        self.assertIn("a cache host's image may have been evicted", out)
        self.assertFalse(plats & set(GUEST_PLATS))   # no step of a guest that isn't there

    def test_refusals(self):
        r = self.run_all("copland", {"SPLIT": "chopin,cinqcent"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("SPLIT: copland is not one of its hosts (chopin cinqcent)", r.stderr)
        r = self.run_all("copland", {"SPLIT": "copland,czerny"})   # a reserved row
        self.assertEqual(r.returncode, 2)
        self.assertIn("SPLIT: no such host: czerny", r.stderr)
        r = self.run_all("copland", {"SPLIT": "all", "ANY_HOST": "1"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("SPLIT and ANY_HOST", r.stderr)

    def test_overrides_and_platforms_still_choose(self):
        mine = {h: self.share(h, {"SPLIT": "all"})[1] for h in ("copland", "cage")}
        self.assertIn("ext4", mine["copland"])
        self.assertNotIn("ext4", mine["cage"])
        x = sorted(mine["cage"])[0]   # one of cage's own
        rc, lfs, sfs, plats, lost, out = self.share("cage", {"SPLIT": "all", "LINUX_FS": f"ext4 {x}"},
                                                     ("--platforms", "linux,freebsd"))
        self.assertEqual(rc, 0, out)
        self.assertEqual(lfs, {x})   # among the share: copland's ext4 is not cage's to run
        self.assertIn("LINUX_FS ext4 is another host's share of SPLIT", out)
        self.assertIn("is another host's share of SPLIT", out)

    def test_overrides_never_reach_past_the_share(self):
        rc, lfs, sfs, plats, lost, out = self.share("cage", {"SPLIT": "all", "LINUX_FS": "ext4"})
        self.assertEqual(rc, 0, out)   # ext4 is copland's: no linux steps on cage
        self.assertFalse(lfs, out)
        self.assertNotIn("linux", plats)
        # ZFS's steps stay the big hosts', whatever LINUX_FS and SPARSE_FS say
        for h in ("copland", "cage"):
            rc, lfs, sfs, plats, lost, out = self.share(h, {"SPLIT": "all", "LINUX_FS": "zfs zfs-onto-ext4 nonroot",
                                                            "SPARSE_FS": "zfs zfs-nocomp"})
            self.assertEqual(rc, 0, out)
            self.assertFalse(lfs, (h, out))
            self.assertFalse(sfs, (h, out))
        # all: the whole share; every host together, each step once
        seen = []
        for h in ("chopin", "cinqcent", "copland", "cage"):
            rc, lfs, _, _, _, out = self.share(h, {"SPLIT": "all", "LINUX_FS": "all"})
            self.assertEqual(rc, 0, out)
            self.assertEqual(lfs, self.share(h, {"SPLIT": "all"})[1], h)
            seen += sorted(lfs)
        self.assertEqual(sorted(seen), sorted(set(seen)))
        r = self.run_all("cage", {"SPLIT": "all", "SPARSE_FS": "ext5"})
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("SPARSE_FS: no such step: ext5", r.stderr)

    def test_without_split_nothing_changes(self):
        rc, lfs, sfs, plats, lost, out = self.share("copland")
        self.assertEqual(rc, 0, out)
        self.assertEqual((lfs, sfs), ({"ext4", "xfs", "btrfs", "f2fs", "tmpfs", "jfs"}, {"ext4", "xfs", "btrfs"}))
        self.assertNotIn("SPLIT", out)
        rc, lfs, sfs, plats, lost, out = self.share("chopin")   # no guest here: still its steps, no lost rows
        self.assertEqual((rc, lost), (0, []), out)
        self.assertEqual(plats, {"linux", "sparse", "zfsguests", "dragonfly"})

    def test_czerny_joins_all_once_enabled(self):
        alt = AltTable(lambda t: row_of(t, "czerny").update(enabled=True))
        try:
            out = {}
            for h in ("chopin", "cinqcent", "copland", "cage", "czerny"):
                out[h] = self.share(h, dict(alt.env(), SPLIT="all"))
                self.assertEqual(out[h][0], 0, out[h][5])
            self.assertEqual(sorted(x for h in out for x in out[h][1]), sorted(ALL_LFS))
            self.assertTrue(out["czerny"][1], "czerny gets a share")
        finally:
            alt.close()


# --- cmp-pop.sh nodisk, cmp-run.sh DISKS_DIR (P18) -----------------------------------------

FAKE_FLOCK = "#!/bin/sh\nexit 0\n"


class CmpPop(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        with open(os.path.join(self.d, "flock"), "w") as f:
            f.write(FAKE_FLOCK)
        os.chmod(os.path.join(self.d, "flock"), 0o755)
        self.q = os.path.join(self.d, "queue")

    def tearDown(self):
        shutil.rmtree(self.d)

    def pop(self, lines, *args):
        with open(self.q, "w") as f:
            f.write("".join(x + "\n" for x in lines))
        e = dict(os.environ, PATH=self.d + os.pathsep + os.environ["PATH"])
        r = subprocess.run(["sh", CMP_POP, self.q, *args], capture_output=True, text=True, env=e, timeout=30)
        with open(self.q) as f:
            left = f.read().split()
        return r.returncode, r.stdout.strip(), left

    def test_nodisk_skips_bang_lines(self):
        q = ["fedora44!=4%5", "debian13:ext4@2!=3", "arch=4%5", "mint22"]
        self.assertEqual(self.pop(q, "8", "8", "cage", "nodisk"), (0, "arch=4%5", ["fedora44!=4%5", "debian13:ext4@2!=3",
                                                                                   "mint22"]))
        self.assertEqual(self.pop(q, "8", "8", "chopin"), (0, "fedora44!=4%5", q[1:]))   # as before
        self.assertEqual(self.pop(q, "8", "8", "chopin", ""), (0, "fedora44!=4%5", q[1:]))

    def test_nodisk_waits_with_the_first_line_it_may_take(self):
        rc, out, left = self.pop(["fedora44!=2%2", "arch=9%5"], "4", "8", "cage", "nodisk")
        self.assertEqual((rc, out, left), (1, "9 5", ["fedora44!=2%2", "arch=9%5"]))
        rc, out, left = self.pop(["fedora44!=2%2", "arch!"], "4", "8", "cage", "nodisk")
        self.assertEqual((rc, out, left), (1, "! 2 0", ["fedora44!=2%2", "arch!"]))
        rc, out, _ = self.pop(["fedora44!=9%2"], "4", "8", "chopin")
        self.assertEqual((rc, out), (1, "9 2"))   # without nodisk, as before

    def test_the_mark_of_a_host_that_can_take_bang_lines(self):
        bang = ["fedora44!=2%2", "arch!"]
        self.assertEqual(self.pop(bang, "4", "8", "cage", "nodisk")[:2], (1, "! 2 0"))
        self.assertFalse(os.path.exists(self.q + ".disks"))   # nodisk leaves no mark
        self.assertEqual(self.pop(["mint22"], "8", "8", "cage", "anydisk"), (0, "mint22", []))
        self.assertFalse(os.path.exists(self.q + ".disks"))   # nor does anydisk
        self.assertEqual(self.pop(["mint22"], "8", "8", "chopin"), (0, "mint22", []))
        self.assertTrue(os.path.exists(self.q + ".disks"))    # a worker that can: the mark
        self.assertEqual(self.pop(bang, "4", "8", "cage", "nodisk")[:2], (1, "! 2 1"))

    def test_anydisk_takes_bang_lines(self):
        self.assertEqual(self.pop(["fedora44!=2%2", "arch"], "4", "8", "cage", "anydisk"),
                         (0, "fedora44!=2%2", ["arch"]))

    def nodisk_wait(self, replies, polls=None):
        """Feed cmp-run.sh's own nodisk_wait the replies; the poll (1-based) it gives up at, else None."""
        with open(CMP_RUN) as f:
            s = f.read()
        i = s.index("nodisk_wait() {")
        fn = s[i:s.index("\n}\n", i) + 3]
        script = fn + "bangs=0 bangn=''\nn=0\nwhile read -r l; do n=$((n + 1)); nodisk_wait \"$l\" || { echo $n; exit; }; done\n"
        e = dict(os.environ)
        e.pop("NODISK_POLLS", None)
        if polls is not None:
            e["NODISK_POLLS"] = str(polls)
        r = subprocess.run(["bash", "-c", script], input="".join(x + "\n" for x in replies), capture_output=True,
                           text=True, env=e, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return int(r.stdout) if r.stdout.strip() else None

    def test_no_worker_has_disks_dir_gives_up(self):
        # No host with DISKS_DIR ever asked: about 10 minutes (20 polls) of the same "!" lines, then FAIL rows
        self.assertEqual(self.nodisk_wait(["! 3 0"] * 30), 20)
        self.assertEqual(self.nodisk_wait(["! 3 0"] * 5, polls=3), 3)
        self.assertEqual(self.nodisk_wait(["! 3 0"] * 10 + ["! 2 0"] * 19), None)   # one was taken: wait again
        self.assertEqual(self.nodisk_wait(["! 3 0"] * 10 + ["! 2 0"] * 20), 30)
        # One that can has asked: its lines, however long it takes
        self.assertEqual(self.nodisk_wait(["! 3 1"] * 100), None)
        self.assertEqual(self.nodisk_wait(["! 3 0"] * 19 + ["! 3 1"] + ["! 3 0"] * 19), None)

    def test_worker_gives_up_into_the_old_fail_row(self):
        with open(CMP_RUN) as f:
            s = f.read()
        w = s[s.index("worker() {"):]
        self.assertIn("if ! nodisk_wait \"$line\"; then", w)
        self.assertLess(w.index("gaveup=1"), w.index('echo "$g 99 $H" >>cmp-logs/$tag/done'))
        self.assertIn('[ -z "${gaveup:-}" ] || nd=anydisk', s)
        self.assertIn("rm -f $Q.disks", s)

    def test_bad_fifth_argument(self):
        rc, _, left = self.pop(["arch"], "8", "8", "cage", "yes")
        self.assertEqual((rc, left), (4, ["arch"]))

    def test_cmp_run_makes_disks_dir_and_says_nodisk(self):
        with open(CMP_RUN) as f:
            s = f.read()
        mk = s.index('mkdir -p "$DISKS_DIR"')
        self.assertLess(s.index("Q=cmp-logs/$tag/queue"), mk)
        self.assertLess(mk, s.index('[ -d "$DISKS_DIR" ] || { echo "$line: no $DISKS_DIR"'))   # before the check
        self.assertLess(s.index("if [ \"${1:-}\" = --reserve ]"), mk)   # not for --reserve or --ceiling
        self.assertIn("cmp-pop.sh $Q $1 $2 $H $nd", s)
        self.assertIn('cmp-pop.sh $Q "$1" "$2" $H $nd', s)
        self.assertIn('[ -d "$DISKS_DIR" ] || nd=nodisk', s)
        r = subprocess.run(["bash", "-n", CMP_RUN], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


# --- gate: adopt the other run's PASS (P20) ------------------------------------------------

CELL = "gate:loop:linux@copland"
TREE = {"commit": "a" * 40, "dirty": {}}


def prow(**k):
    r = {"id": "copland-1", "cell": CELL, "host": "copland", "family": "gate", "kind": "run", "verdict": "PASS",
         "valid": True, "commit": "a" * 40, "dirty": {}, "key": "K1", "meta": {"set": "bko-bin"},
         "t": "2026-11-13T04:00:00Z", "run": "t2s-other", "needs": {"n": 1}}
    r.update(k)
    return r


class Adoptable(unittest.TestCase):
    since = "2026-11-13T03:58:00Z"

    def adopt(self, rows, tree=TREE, planned="K1", run="t2g-me"):
        return gate.adoptable(rows, CELL, "copland", tree, planned, "bko-bin", self.since, run)

    def test_a_pass_of_the_same_cell_commit_and_key(self):
        self.assertEqual(self.adopt([prow()])["id"], "copland-1")
        newest = self.adopt([prow(), prow(id="copland-2", t="2026-11-13T05:00:00Z")])
        self.assertEqual(newest["id"], "copland-2")

    def test_nothing_else(self):
        for k in (dict(verdict="FAIL"), dict(verdict="FLAKY-FAIL"), dict(verdict="HARNESS"), dict(valid=False),
                  dict(key="K2"), dict(commit="b" * 40), dict(dirty={"x": "1"}), dict(host="cage"),
                  dict(cell="gate:loop:sparse@copland"), dict(meta={"set": "round6"}), dict(kind="carried"),
                  dict(t="2026-11-13T03:00:00Z"), dict(run="t2g-me"), dict(family="holt")):
            self.assertIsNone(self.adopt([prow(**k)]), k)
        self.assertIsNone(self.adopt([prow()], planned=None))
        self.assertIsNone(self.adopt([prow()], tree={"commit": None, "dirty": {}}))
        # Two trees whose dirty files aren't known (tree_info's None) never count as alike
        self.assertIsNone(self.adopt([prow(dirty=None)], tree={"commit": "a" * 40, "dirty": None}))
        self.assertIsNone(self.adopt([prow()], tree={"commit": "a" * 40, "dirty": None}))
        self.assertIsNone(self.adopt([prow(dirty=None)]))
        self.assertIsNone(self.adopt([prow()], tree={"commit": "a" * 40}))
        same = {"testvm/x.sh": "h1"}   # a known dirty tree, alike on both sides: still adoptable
        self.assertEqual(self.adopt([prow(dirty=same)], tree={"commit": "a" * 40, "dirty": dict(same)})["id"],
                         "copland-1")
        self.assertIsNone(self.adopt(None))


class FakeIO:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def script(self, host, script, data, timeout=None):
        self.calls.append((host, data))
        rows = self.rows() if callable(self.rows) else self.rows
        return {"tree": dict(TREE), "rows_of": rows}


class LockedAdopts(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.logs = []
        self.optin = mock.patch.dict(os.environ, {gate.ADOPT_ENV: "1"})
        self.optin.start()

    def tearDown(self):
        self.optin.stop()
        shutil.rmtree(self.d)

    def test_off_by_default_the_waiting_run_runs_it(self):
        io = FakeIO(lambda: [prow(t=utc_now())])
        for v in (None, "0", "yes"):
            with mock.patch.dict(os.environ):
                os.environ.pop(gate.ADOPT_ENV, None)
                if v is not None:
                    os.environ[gate.ADOPT_ENV] = v
                self.assertFalse(gate.adopt_enabled(), v)
                e, ran = self.run_locked(self.env(io))
                self.assertEqual((ran, e), ([1], {"outcome": "ran"}), v)
        self.assertEqual(io.calls, [])   # and no extra round trip to the host
        self.assertTrue(any("waiting for it" in x for x in self.logs))

    def env(self, io, planned="K1"):
        pc = types.SimpleNamespace(cell=CELL, rule="fresh:names", reasons=["due"], keys={"copland": planned}, key=None)
        return types.SimpleNamespace(
            mac_root=self.d, run="t2g-me", set_name="bko-bin", layers=gate.load_layers(), io=io, checkout=REPO,
            host_root="/bkoscratch/test2", copy_root="~/backotter-work/t2/copy", log=self.logs.append,
            host_for=lambda c: "copland", plan_cell=lambda c: pc, copy_path=lambda h: "~/backotter-work/t2/copy/X")

    def run_locked(self, env, hold=True):
        ran = []
        lk = gate.LayerLock(env, CELL).path
        fd = os.open(lk, os.O_RDWR | os.O_CREAT, 0o644)
        if hold:
            fcntl.flock(fd, fcntl.LOCK_EX)
        out = {}
        with mock.patch.object(gate, "LOCK_POLL_S", 0.05):
            th = threading.Thread(target=lambda: out.setdefault("e", gate.locked(env, CELL, lambda: ran.append(1) or
                                                                                   {"outcome": "ran"})))
            th.start()
            time.sleep(0.3)
            if hold:
                fcntl.flock(fd, fcntl.LOCK_UN)
            th.join(10)
        os.close(fd)
        return out.get("e"), ran

    def test_waited_and_adopted(self):
        io = FakeIO(lambda: [prow(t=utc_now())])
        e, ran = self.run_locked(self.env(io))
        self.assertEqual(ran, [])
        self.assertEqual((e["outcome"], e["verdict"], e["class"], e["rows"], e["valid"], e["host"]),
                         ("adopted", "PASS", "pass", ["copland-1"], True, "copland"))
        self.assertTrue(e["reasons"][0].startswith("adopted copland-1: run t2s-other passed it on copland"))
        self.assertEqual(io.calls[0][1]["rows_of"]["cell"], CELL)
        self.assertTrue(any("waiting for it" in x for x in self.logs))

    def test_waited_and_ran_when_the_other_failed(self):
        io = FakeIO(lambda: [prow(t=utc_now(), verdict="FAIL")])
        e, ran = self.run_locked(self.env(io))
        self.assertEqual((ran, e), ([1], {"outcome": "ran"}))

    def test_waited_and_ran_without_a_planned_key(self):
        io = FakeIO(lambda: [prow(t=utc_now())])
        e, ran = self.run_locked(self.env(io, planned=None))
        self.assertEqual(ran, [1])
        self.assertEqual(io.calls, [])

    def test_no_wait_no_lookup(self):
        io = FakeIO(lambda: [prow(t=utc_now())])
        e, ran = self.run_locked(self.env(io), hold=False)
        self.assertEqual((ran, io.calls), ([1], []))

    def test_host_not_answering_runs_it(self):
        class Broken(FakeIO):
            def script(self, *a, **k):
                raise RuntimeError("ssh: no route")
        e, ran = self.run_locked(self.env(Broken([])))
        self.assertEqual(ran, [1])

    def test_host_script_reads_rows_of(self):
        self.assertIn('if d.get("rows_of"):', gate.HOST_SCRIPT)
        compile(gate.HOST_SCRIPT, "HOST_SCRIPT", "exec")


# --- bs2 --hosts (P21) ---------------------------------------------------------------------

class Bs2Hosts(unittest.TestCase):
    def setUp(self):
        self.bs2 = load("st_bs2", os.path.join(TESTVM, "backshot2", "bs2"), {})

    def test_default_is_every_enabled_host(self):
        """A run's hosts: every enabled one (its cases go where their source is, bs2's split());
        replay and the corpus still go to the source's HOME host without --hosts."""
        b = self.bs2
        self.assertEqual(b.RUN_HOSTS, {h: f"m@{h}.local" for h in ("chopin", "cinqcent", "copland", "cage")})
        self.assertEqual(b.assign(b.ALPINE), b.HOME)
        self.assertEqual(b.assign(["alpine324", "alpine324-uefi"]), {"alpine324": "chopin", "alpine324-uefi": "chopin"})
        self.assertEqual(b.assign(["nosuch"]), {"nosuch": "chopin"})
        self.assertEqual((b.slots("chopin"), b.slots("cinqcent")), (5, 5))   # hosts.json's bs2_slots

    def test_four_hosts_one_source_each(self):
        b = self.bs2
        b.use_hosts("chopin,cinqcent,copland,cage")
        self.assertEqual(list(b.RUN_HOSTS), ["chopin", "cinqcent", "copland", "cage"])
        got = b.assign(b.ALPINE)
        self.assertEqual(sorted(got.values()), ["cage", "chopin", "cinqcent", "copland"])
        self.assertEqual((got["alpine324"], got["alpine324-lvm"]), ("chopin", "cinqcent"))
        self.assertEqual((b.slots("copland"), b.slots("cage")), (2, 2))

    def test_only_hosts_holding_the_source(self):
        b = self.bs2
        b.use_hosts("copland,cage,chopin")
        have = {"chopin": set(b.ALPINE), "copland": {"alpine324"}, "cage": {"alpine324"}}
        got = b.assign(b.ALPINE, have=have)
        self.assertEqual(got["alpine324"], "copland")   # its home chopin past its share: the first with none
        self.assertEqual({got[s] for s in b.ALPINE if s != "alpine324"}, {"chopin"})
        with self.assertRaises(SystemExit):
            b.assign(["alpine324-luks"], have={"copland": set(), "cage": set(), "chopin": set()})

    def test_held_asks_each_host(self):
        b = self.bs2
        b.use_hosts("copland,cage")
        calls = []

        def fake(host, cmd, check=True, capture=False):
            calls.append((host, cmd))
            return types.SimpleNamespace(returncode=0, stdout="alpine324\n" if host == "copland" else "", stderr="")
        with mock.patch.object(b, "ssh", fake):
            self.assertEqual(b.held(["alpine324", "alpine324-lvm"]), {"copland": {"alpine324"}, "cage": set()})
        self.assertEqual([h for h, _ in calls], ["copland", "cage"])
        self.assertIn("cd ~/backotter-work/vms", calls[0][1])

    def test_bad_hosts(self):
        with self.assertRaises(SystemExit):
            self.bs2.use_hosts("chopin,czerny")
        with self.assertRaises(SystemExit):
            self.bs2.use_hosts("")


# --- ram-size.sh class rows and --host (P23) -----------------------------------------------

TSV = ("# a comment\nguest\tvariant\thost\tleft_bytes\tproposed_D_GiB\tproposed_MEM_GiB\tsource\n"
       "g1\t-\tchopin\t0\t7\t4\tx\n"
       "g1\t-\tcinqcent\t0\t9\t5\tx\n"
       "g1\t-\tclass:small\t0\t6\t4\tx\n"
       "g2\t-\tchopin\t0\t5\t4\tx\n")


class RamSizeClass(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.t = os.path.join(self.d, "t.tsv")
        with open(self.t, "w") as f:
            f.write(TSV)

    def tearDown(self):
        shutil.rmtree(self.d)

    def size(self, host, *names, env=None, table=None):
        e = dict(os.environ)
        e.pop(H.ENV, None)
        e.update(env or {})
        r = subprocess.run(["bash", RAM_SIZE, "--table", table or self.t, "--peaks", "/dev/null", "--host", host,
                            *names], capture_output=True, text=True, env=e, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return {x.split()[0]: " ".join(x.split()[1:]) for x in r.stdout.splitlines()}

    def test_own_then_class_then_largest(self):
        self.assertEqual(self.size("chopin", "g1", "g2"), {"g1": "7 4 table", "g2": "5 4 table"})
        self.assertEqual(self.size("cinqcent", "g1"), {"g1": "9 5 table"})
        self.assertEqual(self.size("copland", "g1", "g2"), {"g1": "6 4 table", "g2": "5 4 table"})
        self.assertEqual(self.size("cage.local", "g1"), {"g1": "6 4 table"})   # H.DOMAIN is H
        self.assertEqual(self.size("czerny", "g1"), {"g1": "9 5 table"})   # reserved: no class, the largest

    def test_class_follows_the_table(self):
        alt = AltTable(lambda t: row_of(t, "czerny").update(enabled=True))
        try:
            self.assertEqual(self.size("czerny", "g1", env=alt.env()), {"g1": "6 4 table"})
        finally:
            alt.close()

    def test_the_real_table_without_class_rows_is_as_before(self):
        real = os.path.join(TESTVM, "holttest1d", "ram-sizes.tsv")
        with open(real) as f:
            self.assertFalse(any("\tclass:" in x for x in f if not x.startswith("#")))
        a = self.size("copland", "debian13", "fedora44+bigger", "freebsd15", table=real)
        b = self.size("chopin.local", "debian13", "fedora44+bigger", "freebsd15", table=real)
        self.assertEqual(a, b)

    def test_ht1_queue_passes_its_host(self):
        with open(HT1_QUEUE) as f:
            s = f.read()
        # bignames, the skip scan and launch size this host's jobs; --order only sorts
        self.assertEqual(s.count('"$HERE/ram-size.sh" --host "$H"'), 3)


# --- drive.py: ssh by ip, the temperature (P24) --------------------------------------------

FAKE_SSH = """#!/bin/sh
echo "$*" >> "$SSH_LOG"
case " $* " in
*" m@chopin.local "*) echo "ssh: Could not resolve hostname chopin.local: nodename nor servname provided" >&2; exit 255 ;;
*" m@cage.local "*) echo "remote said 255" >&2; exit 255 ;;
*" m@192.0.2.7 "*) echo via-ip; exit 0 ;;
esac
echo plain
"""


class DriveSsh(unittest.TestCase):
    def setUp(self):
        def ips(t):
            row_of(t, "chopin")["ip"] = "192.0.2.7"
            row_of(t, "cage")["ip"] = "192.0.2.9"
        self.alt = AltTable(ips)
        self.drive = load("st_drive_ip", os.path.join(TESTVM, "stictest1", "drive.py"), self.alt.env())
        self.d = tempfile.mkdtemp()
        with open(os.path.join(self.d, "ssh"), "w") as f:
            f.write(FAKE_SSH)
        os.chmod(os.path.join(self.d, "ssh"), 0o755)
        self.log = os.path.join(self.d, "log")
        self.env = dict(self.alt.env(), PATH=self.d + os.pathsep + os.environ["PATH"], SSH_LOG=self.log)

    def tearDown(self):
        self.alt.close()
        shutil.rmtree(self.d)

    def calls(self):
        with open(self.log) as f:
            return f.read().splitlines()

    def test_falls_back_to_the_ip_and_stays_there(self):
        with mock.patch.dict(os.environ, self.env):
            rc, out, err = self.drive.ssh("chopin", "true")
            self.assertEqual((rc, out), (0, "via-ip\n"))
            self.assertEqual(self.drive.ssh("chopin", "true")[:2], (0, "via-ip\n"))
        c = self.calls()
        self.assertEqual(len(c), 3)   # the name once, then the ip twice
        self.assertIn("m@chopin.local", c[0])
        self.assertIn("-o HostKeyAlias=chopin.local m@192.0.2.7", c[1])
        self.assertIn("m@192.0.2.7", c[2])

    def test_no_retry_for_a_commands_own_255_or_without_an_ip(self):
        with mock.patch.dict(os.environ, self.env):
            self.assertEqual(self.drive.ssh("cage", "false")[0], 255)
            self.assertEqual(self.drive.ssh("copland", "true")[:2], (0, "plain\n"))
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(self.drive.VIA_IP, {})

    def test_temperature_takes_the_hottest_cpu_sensor(self):
        out = "TEMP 61000\nTEMP 72500\nNVME 40000\nEND\n"
        with mock.patch.object(self.drive, "run_script", lambda *a, **k: (0, out, "")):
            s = self.drive.status("chopin", False, "m1", [])
        self.assertEqual((s["temp"], s["nvme"]), (72.5, [40.0]))
        with mock.patch.object(self.drive, "run_script", lambda *a, **k: (0, "END\n", "")):
            self.assertIsNone(self.drive.status("chopin", False, "m1", [])["temp"])
        self.assertIn("k10temp|coretemp|zenpower)", self.drive.STATUS_SH)
        with open(HT1_QUEUE) as f:
            self.assertIn("k10temp|coretemp|zenpower)", f.read())


if __name__ == "__main__":
    unittest.main()
