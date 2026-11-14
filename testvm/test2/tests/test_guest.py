"""K3's tests: the runner, the row builder and the host tools (no host, no VM)."""

import contextlib
import io as io_mod
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from t2lib import guest, guest_judge as J, hostjob, rows_guest, tool_guest  # noqa: E402
from t2lib import run as runmod  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
FX = os.path.join(HERE, "fixtures", "guest")
_tmp, _saved = None, {}
ENV = ("T2_MAC_ROOT", "T2_HOST_ROOT", "T2_COPY_ROOT", "T2_LEDGER", "T2_WORK")


def setUpModule():
    global _tmp
    _tmp = tempfile.mkdtemp(prefix="t2-test-guest-")
    for k in ENV:
        _saved[k] = os.environ.get(k)
        os.environ.pop(k, None)
    os.environ.update(T2_MAC_ROOT=os.path.join(_tmp, "mac"), T2_HOST_ROOT=os.path.join(_tmp, "host"))
    os.makedirs(os.environ["T2_MAC_ROOT"], exist_ok=True)


def tearDownModule():
    for k, v in _saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    shutil.rmtree(_tmp, ignore_errors=True)


def tmpdir():
    return tempfile.mkdtemp(dir=_tmp)


# --- the spec --------------------------------------------------------------------------------------

class Spec(unittest.TestCase):
    def setUp(self):
        self.sf = rows_guest.load_spec(REPO)

    def test_sixty_two_guests_and_the_exclusion(self):
        self.assertEqual(len(self.sf.names()), 62)
        self.assertNotIn("freebsd15-builder", self.sf.names())
        self.assertEqual(self.sf.data["excluded"], {"freebsd15-builder": "a build helper"})

    def test_every_rule_is_the_judges(self):
        loaders, sources = set(J.LOADER_RULES) | {"L-none"}, set(J.SOURCE_RULES) | {"S-none"}
        kernels = set(J.KERNELS_MAX) | {"K-none"}
        boots = {"B-lin", "B-bsd", "B-obsd", "B-ill", "B-alpbsd", "B-none"}
        mounts = {"M-lin", "M-zfs", "M-alp", "M-bsd", "M-ill", "M-haiku", "M-none"}
        for n in self.sf.names():
            r = self.sf.entry(n)["checks"]["rules"]
            self.assertIn(r["source"], sources, n)
            self.assertIn(r["loader"], loaders, n)
            self.assertIn(r["kernel"], kernels, n)
            self.assertIn(r["boot"], boots, n)
            self.assertIn(r["mounts"], mounts, n)

    def test_new_guests(self):
        new = sorted(n for n in self.sf.names() if not self.sf.entry(n)["checks"]["established"])
        self.assertEqual(new, sorted(["endeavouros", "endeavouros-grub", "manjaro", "fedora44-raid", "ubuntu26-raid", "rocky10-raid5",
                                      "freebsd15-mirror", "omnios58-mirror", "ubuntu26-luks", "chimera", "kinoite44",
                                      "chimera-uefi", "chimera-zfs", "gentoo", "gentoo-systemd", "cachyos-zfs", "anduinos2", "neon24", "garuda", "biglinux"]))
        for n in new:
            self.assertIn("testvm/test2/guest/new-guest.sh", self.sf.entry(n)["harness"], n)

    def test_kinds_and_update(self):
        self.assertEqual(self.sf.entry("haiku6")["kind"], "probe")
        self.assertEqual(self.sf.entry("haiku6")["update"], "name-only")
        self.assertEqual(self.sf.entry("redox")["kind"], "redox")
        self.assertIs(self.sf.entry("redox")["update"], False)
        self.assertEqual(self.sf.entry("sculpt2610")["kind"], "sculpt")
        self.assertIs(self.sf.entry("sculpt2610")["update"], False)
        d = self.sf.entry("dragonfly6")
        self.assertEqual((d["cpus"], d["memory"], d["timeout_s"]), (4, 6144, 7200))
        self.assertEqual(self.sf.entry("manjaro")["checks"]["kernels_max"], 3)
        self.assertTrue(self.sf.entry("arch")["try"])

    def test_sections_are_the_guests(self):
        h = self.sf.entry("debian12")["harness"]
        self.assertIn("testvm/update/in-guest.sh#prologue", h)
        self.assertIn("testvm/update/in-guest.sh#Linux.common", h)
        self.assertIn("testvm/update/in-guest.sh#Linux.apt", h)
        self.assertIn("testvm/update/in-guest.sh#NixOS", self.sf.entry("nixos2605")["harness"])
        self.assertNotIn("testvm/update/in-guest.sh#Linux.common", self.sf.entry("nixos2605")["harness"])
        self.assertIn("testvm/update/in-guest.sh#NetBSD", self.sf.entry("alpinebsd")["harness"])

    def test_every_file_it_names_exists(self):
        for n in self.sf.names():
            for x in self.sf.entry(n)["harness"]:
                p = x.split("#")[0]
                base = p[:p.index("*")].rstrip("/") if "*" in p else p
                self.assertTrue(os.path.exists(os.path.join(REPO, base)), f"{n}: {x}")

    def test_limits_class_only_kinoite44s_standing_lines(self):
        # Empty until kinoite44's first GuestTest2 run (docs/design/bazzite.md, What B7 found):
        # its composefs / and the failed remount, degraded state and fstab order every ostree boot has.
        from t2lib import guest_judge as J
        with open(os.path.join(REPO, "testvm/test2/guest/limits.json"), encoding="utf-8") as f:
            d = json.load(f)
        self.assertEqual(sorted(d), ["kinoite44"])
        self.assertEqual(sorted(d["kinoite44"]), ["after:boot-state", "after:failed-units", "after:fstab", "image:free"])
        for n, e in d["kinoite44"].items():
            self.assertIn(n, J.IMAGE_NEEDS + J.AFTER_NEEDS)
            self.assertTrue(e["lines"] and all(isinstance(x, str) and x for x in e["lines"]), n)
            self.assertTrue(e.get("why"), n)


class CheckAfterKernelString(unittest.TestCase):
    """check-after.sh's same(): the running kernel's `uname -v` against the kernel file (K-nbsd, K-dfly)."""

    def run_same(self, uname_v, kernel_bytes):
        import subprocess
        with open(os.path.join(REPO, "testvm/test2/guest/check-after.sh"), encoding="utf-8") as f:
            fn = re.search(r"^same\(\) \{.*?^\}", f.read(), re.S | re.M).group(0)
        d = tempfile.mkdtemp(prefix="t2-same-", dir=_tmp)
        with open(os.path.join(d, "uname"), "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\nprintf '%s\\n' \"$UNAME_V\"\n")
        os.chmod(os.path.join(d, "uname"), 0o755)
        with open(os.path.join(d, "kernel"), "wb") as f:
            f.write(b"\x7fELF\0" + kernel_bytes + b"\0rest")
        env = dict(os.environ, PATH=d + os.pathsep + os.environ.get("PATH", ""), UNAME_V=uname_v)
        return subprocess.run(["sh", "-c", fn + '\nsame "$1"', "sh", os.path.join(d, "kernel")],
                              env=env, capture_output=True, text=True, check=True).stdout.strip()

    def test_netbsd_two_spaces_where_the_kernel_has_newline_tab(self):
        k = b"NetBSD 11.1 (GENERIC) #0: Mon Nov  2 16:34:32 UTC 2026\n\tuser@host1:/usr/src/sys/arch/amd64/compile/GENERIC\n"
        self.assertEqual(self.run_same("NetBSD 11.1 (GENERIC) #0: Mon Nov  2 16:34:32 UTC 2026  user@host1:"
                                       "/usr/src/sys/arch/amd64/compile/GENERIC", k), "match yes")

    def test_dragonfly_newline_and_indent(self):
        k = b"DragonFly v6.4.2-RELEASE #0: Mon Nov  2 10:00:00 UTC 2026\n    user@host1:/usr/obj/usr/src/sys/X86_64_GENERIC\n"
        self.assertEqual(self.run_same("DragonFly v6.4.2-RELEASE #0: Mon Nov  2 10:00:00 UTC 2026     user@host1:"
                                       "/usr/obj/usr/src/sys/X86_64_GENERIC", k), "match yes")

    def test_another_kernel_is_no_match(self):
        k = b"NetBSD 11.0 (GENERIC) #0: Tue Aug  4 09:00:00 UTC 2026\n\tuser@host1:/usr/src/sys/arch/amd64/compile/GENERIC\n"
        self.assertEqual(self.run_same("NetBSD 11.1 (GENERIC) #0: Mon Nov  2 16:34:32 UTC 2026  user@host1:"
                                       "/usr/src/sys/arch/amd64/compile/GENERIC", k), "match no")


class CheckAfterOstree(unittest.TestCase):
    """check-after.sh's K-ostree reading of `rpm-ostree status --json`: booted and default, as
    CHECKSUM.SERIAL, the default being the first deployment that isn't staged."""

    def run_py(self, doc):
        import subprocess
        with open(os.path.join(REPO, "testvm/test2/guest/check-after.sh"), encoding="utf-8") as f:
            text = f.read()
        branch = text[text.index("elif has_rule K-ostree"):]
        code = re.search(r"python3 -c '(.*?)'\n", branch, re.S).group(1)
        return subprocess.run([sys.executable, "-I", "-c", code], input=doc, capture_output=True, text=True,
                              check=True).stdout.splitlines()

    def test_booted_and_default(self):
        d = {"deployments": [{"checksum": "ef56", "serial": 0, "staged": True},
                             {"checksum": "ab12", "serial": 1, "booted": True},
                             {"checksum": "ab12", "serial": 0}]}
        self.assertEqual(self.run_py(json.dumps(d)), ["booted ab12.1", "default ab12.1"])
        d["deployments"][1]["booted"], d["deployments"][2]["booted"] = False, True
        self.assertEqual(self.run_py(json.dumps(d)), ["booted ab12.0", "default ab12.1"])

    def test_no_answer_prints_nothing(self):
        self.assertEqual(self.run_py(""), [])

    def test_grubenv_keys(self):
        import subprocess
        with open(os.path.join(REPO, "testvm/test2/guest/check-after.sh"), encoding="utf-8") as f:
            line = next(x for x in f if "grubenv-key" in x).strip()
        d = tempfile.mkdtemp(prefix="t2-grubenv-", dir=_tmp)
        env = os.path.join(d, "grubenv")
        with open(env, "w", encoding="utf-8") as f:
            f.write("# GRUB Environment Block\nboot_success=1\nnext_entry=1\ndefault=ostree-1\nsaved_entry=x\n" + "#" * 64)
        out = subprocess.run(["sh", "-c", line.replace('"$f"', '"$1"'), "sh", env], capture_output=True, text=True,
                             check=True).stdout.splitlines()
        self.assertEqual(out, ["grubenv-key next_entry", "grubenv-key default"])


# --- a run on fake hosts ---------------------------------------------------------------------------

class PC:
    def __init__(self, cell, status="due", rule="fresh:names", gm=1.0):
        self.cell, self.status, self.rule, self.reasons = cell, status, rule, ["why"]
        self.keys, self.host, self.placed, self.est = {"chopin": "a" * 64, "cinqcent": "a" * 64}, "any", None, {"gm": gm}


class Plan:
    def __init__(self, cells):
        self.cells = cells


class Word:
    def __init__(self, update=False, all_=False):
        self.text, self.word, self.update, self.all = "GuestTest2", "GuestTest2", update, all_
        self.family = "guest"


class Stops:
    def __init__(self):
        self.fed, self.hosted, self.stops, self.stop_os = [], [], [], set()
        self.go = True

    def feed(self, r):
        self.fed.append(r)
        return "ok"

    def feed_host(self, h, swap_out_bytes=None, ours_bytes=None):
        self.hosted.append((h, swap_out_bytes, ours_bytes))
        return "ok"

    def may_start(self, os_=None):
        return self.go and os_ not in self.stop_os


class RS:
    def __init__(self, word, hosts=("chopin", "cinqcent")):
        self.run, self.word, self.family = "t2v-20261110T0000-abcd", word, "guest"
        self.hosts = list(hosts)
        self.mac_root = os.environ["T2_MAC_ROOT"]
        self.mac_dir = os.path.join(tmpdir(), "run")
        os.makedirs(self.mac_dir)
        self.host_root = "/bkoscratch/t2rest-test"
        self.copy = {h: {"id": "cp", "path": f"/copy/{h}", "commit": "c" * 40, "dirty": {}} for h in self.hosts}
        self.stops = Stops()
        self.flakes, self.limits, self.plan = [], {}, None
        self.wrote = []
        self.guards = None

    def log(self, t):
        pass

    def write_cells(self, plan):
        self.wrote.append(plan)


class Ctx:
    def __init__(self, rs, facts=None):
        self.run, self.hosts, self.ledger, self.checkout = rs, list(rs.hosts), [], REPO
        self.classes = {"policy": {"guest": {"run": dict(guest.RUN_DEFAULTS)}}}
        self.facts = facts or {h: {"guests": {}} for h in rs.hosts}
        self.options = {}


def summary(v="PASS", klass="pass", **kw):
    d = {"verdict": v, "class": klass, "needs": {"identity": "PASS", "try": "PASS", "logs": "PASS"}, "fails": [], "to_class_only": False, "overrun": False,
         "harness": None, "tail": [], "wall_s": 60}
    d.update(kw)
    return d


class FakeIO:
    """Answers host_job's helper calls; fetch returns what the script for (guest, attempt) says."""

    def __init__(self):
        self.summ, self.scripts, self.shell, self.rows = {}, [], [], []
        self.mu = threading.Lock()

    def sh(self, host, cmd, timeout=60, input=None):
        with self.mu:
            self.shell.append((host, cmd, input))
        if " append" in cmd and input is not None:
            self.rows.append(json.loads(input))
            return 0, "chopin-20261110T000000Z-00000001\n", ""
        return 0, "", ""

    def script(self, host, script, data, timeout=120):
        self.scripts.append((host, data))
        args = data["args"]
        if args and args[0] == "identity":
            names = [a for a in args[1:] if not a.startswith("--")]
            return {"rc": 0, "out": json.dumps({g: self.identity(host, g, args) for g in names})}
        return {"rc": 0, "out": ""}

    def identity(self, host, g, args):
        st = {"generation": "g" * 64, "sha256": {"a.qcow2": "s" * 64}}
        d = {"size": 1, "mtime_ns": 2, "inode": 3}
        return {"image_state": st, "stat": {"generation": "g" * 64, "disks": {"a.qcow2": d}}, "disks": {"a.qcow2": d},
                "qemu": {"a.qcow2": {"ok": True, "format": "qcow2", "backing": None, "dirty": False, "corrupt": False, "snapshots": ["fresh-install"]}}}

    def fetch(self, host, d, names):
        base = os.path.basename(d)
        m = re.search(r"-r(\d+)/guest/", d)
        attempt = int(m.group(1)) if m else 1
        s = self.summ.get((base, attempt)) or self.summ.get(base)
        if callable(s):
            s = s(attempt)
        out = {}
        if s and "row-summary.json" in names:
            out = {"row-summary.json": json.dumps(s), "row.id": f"{host}-20261110T000000Z-{attempt:08x}\n"}
        if "new-lines.json" in names:
            out["new-lines.json"] = json.dumps({"lines": []})
        return out

    def sleep(self, s):
        time.sleep(0.002)

    def start(self, *a):
        return 1

    def alive(self, *a, **k):
        return True


class FakeJobs:
    """host_job with blocks: hold=True keeps each job running until release()."""

    def __init__(self, hold=False, began=True):
        self.calls, self.hold, self.began = [], hold, began
        self.running, self.peak = {}, {}
        self.waiters = {}
        self.mu = threading.Lock()
        self.ev = threading.Event()

    def __call__(self, env, host, name, attempt, cmd, reserve, wear_tag, timeout_s, wait_s, extra_env, ram, subdir, on_began=None):
        with self.mu:
            self.calls.append(dict(host=host, guest=name, attempt=attempt, cmd=cmd, d=reserve[0], m=reserve[1], tag=wear_tag, env=dict(extra_env),
                                   subdir=subdir, timeout=timeout_s, began=on_began))
            self.running[host] = self.running.get(host, 0) + 1
            self.peak[host] = max(self.peak.get(host, 0), self.running[host])
        if self.began and on_began:
            on_began()
        if self.hold:
            self.ev.wait(20)
        with self.mu:
            self.running[host] -= 1
        return 0, hostjob.job_dir(env, name, attempt, subdir), {"submitted": 1, "began": 2, "ended": 3, "exit": 0}, None

    def release(self):
        self.ev.set()


def make(cells, io=None, jobs=None, word=None, hosts=("chopin", "cinqcent"), **kw):
    rs = RS(word or Word(), hosts)
    ctx = Ctx(rs)
    plan = Plan([PC(f"guest:{g}") for g in cells])
    io = io or FakeIO()
    r = guest.Runner(ctx, plan, io=io, job=jobs or FakeJobs(), poll=0, **kw)
    return r, rs, ctx, io


class Pool(unittest.TestCase):
    def wait_for(self, fn, t=5):
        t0 = time.time()
        while not fn():
            self.assertLess(time.time() - t0, t, "timed out")
            time.sleep(0.002)

    def run_bg(self, r):
        th = threading.Thread(target=r.run, daemon=True)
        th.start()
        return th

    def test_at_most_three_per_host(self):
        names = ["debian12", "arch", "rocky10", "fedora44", "void-musl", "alpine324", "mint22", "cachyos"]
        jobs = FakeJobs(hold=True)
        io = FakeIO()
        for n in names:
            io.summ[n] = summary()
        r, rs, _, _ = make(names, io, jobs, hosts=("chopin",))
        th = self.run_bg(r)
        self.wait_for(lambda: len(jobs.calls) >= 3)
        time.sleep(0.05)
        self.assertEqual(len(jobs.calls), 3)
        jobs.release()
        th.join(10)
        self.assertEqual(jobs.peak["chopin"], 3)
        self.assertEqual(len(jobs.calls), 8)

    def test_no_new_job_while_one_is_in_line(self):
        names = ["debian12", "arch", "rocky10"]
        jobs = FakeJobs(hold=True, began=False)
        io = FakeIO()
        for n in names:
            io.summ[n] = summary()
        r, rs, _, _ = make(names, io, jobs, hosts=("chopin",))
        th = self.run_bg(r)
        self.wait_for(lambda: len(jobs.calls) >= 1)
        time.sleep(0.05)
        self.assertEqual(len(jobs.calls), 1)   # the first is in cmp-run.sh's line
        jobs.calls[0]["began"]()
        self.wait_for(lambda: len(jobs.calls) >= 2)
        time.sleep(0.05)
        self.assertEqual(len(jobs.calls), 2)
        jobs.calls[1]["began"]()
        self.wait_for(lambda: len(jobs.calls) >= 3)
        jobs.calls[2]["began"]()
        jobs.release()
        th.join(10)

    def test_big_jobs_one_at_a_time(self):
        names = ["zorin18", "pop24", "debian12"]
        jobs = FakeJobs(hold=True)
        io = FakeIO()
        for n in names:
            io.summ[n] = summary()
        r, rs, ctx, _ = make(names, io, jobs, hosts=("chopin",))
        r.policy = dict(r.policy, big_gib=12)
        r.big = 12.0
        th = self.run_bg(r)
        self.wait_for(lambda: len(jobs.calls) >= 2)
        time.sleep(0.05)
        started = {c["guest"] for c in jobs.calls}
        self.assertEqual(len(started & {"zorin18", "pop24"}), 1)   # zorin18 13, pop24 12: both big
        self.assertIn("debian12", started)
        jobs.release()
        th.join(10)
        self.assertEqual(len(jobs.calls), 3)

    def test_d_and_m(self):
        e = {"memory": 6144}
        pol = dict(guest.RUN_DEFAULTS)
        self.assertEqual(guest.reserve_for([], "guest:x", e, pol), (8.0, 7))
        self.assertEqual(guest.reserve_for([], "guest:x", {"ram_gib": 13}, pol), (13.0, 3))
        rows = [{"cell": "guest:x", "kind": "run", "verdict": "PASS", "ram_peak_kib": 2 * 1048576, "t": "1"}]
        self.assertEqual(guest.reserve_for(rows, "guest:x", {"ram_gib": 13}, pol)[0], 2.5)
        big = [{"cell": "guest:x", "kind": "run", "verdict": "PASS", "ram_peak_kib": 40 * 1048576, "t": "1"}]
        self.assertEqual(guest.reserve_for(big, "guest:x", {}, pol)[0], 16.0)
        harness = [{"cell": "guest:x", "kind": "run", "verdict": "HARNESS", "ram_peak_kib": 40 * 1048576, "t": "1"}]
        self.assertEqual(guest.reserve_for(harness, "guest:x", {}, pol)[0], 8.0)
        self.assertEqual(guest.reserve_for(rows, "guest:x", {}, pol, update=True)[0], 6.0)

    def test_the_job_is_guest_job_sh_under_its_reservation(self):
        jobs, io = FakeJobs(), FakeIO()
        io.summ["debian12"] = summary()
        r, rs, _, _ = make(["debian12"], io, jobs)
        r.run()
        c = jobs.calls[0]
        self.assertIn("/testvm/test2/guest/job.sh try debian12 " + rs.run + " 1 ", c["cmd"])
        self.assertEqual(c["tag"], f"guest:{rs.run}:debian12")
        self.assertEqual(c["subdir"], "guest")
        self.assertEqual(c["env"]["VM_UPDATE_RAM"], "8")
        self.assertEqual(c["env"]["T2_GUEST_RULES"], "S-apt L-grub K-deb B-lin M-lin")
        self.assertEqual(c["d"], 8.0)
        self.assertEqual(c["m"], 3)

    def test_kinds_give_modes(self):
        self.assertEqual(guest.mode_of({"kind": "ssh"}), "try")
        self.assertEqual(guest.mode_of({"kind": "probe"}), "probe")
        self.assertEqual(guest.mode_of({"kind": "redox"}), "console")
        self.assertEqual(guest.mode_of({"kind": "sculpt"}), "console")
        self.assertEqual(guest.mode_of({"kind": "ssh"}, update=True), "update")


class Reruns(unittest.TestCase):
    def go(self, script, word=None, name="debian12"):
        jobs, io = FakeJobs(), FakeIO()
        io.summ[name] = script
        r, rs, ctx, _ = make([name], io, jobs, word=word)
        res = r.run()
        return res, jobs, rs, r

    def test_fail_reruns_once(self):
        res, jobs, rs, _ = self.go(lambda a: summary("FAIL", "fail"))
        self.assertEqual([c["attempt"] for c in jobs.calls], [1, 2])
        self.assertEqual(res["cells"][0]["verdict"], "FAIL")
        self.assertEqual(len(rs.stops.fed), 1)

    def test_a_pass_on_the_rerun(self):
        res, jobs, rs, _ = self.go(lambda a: summary("FAIL", "fail") if a == 1 else summary("FLAKY-FAIL", "fail"))
        self.assertEqual(res["cells"][0]["verdict"], "FLAKY-FAIL")

    def test_a_known_flake_goes_to_the_rerun(self):
        calls = []
        io = FakeIO()
        io.summ["debian12"] = lambda a: summary("FAIL", "fail", tail=["E: Failed to fetch http://x"]) if a == 1 else summary("PASS", "known-flake")
        jobs = FakeJobs()
        r, rs, _, _ = make(["debian12"], io, jobs)
        res = r.run()
        self.assertEqual(res["cells"][0]["class"], "known-flake")
        cells = [json.loads(i) for _h, c, i in io.shell if "cells-debian12-2.json" in c]
        ent = cells[0]["cells"]["debian12"]
        self.assertEqual(ent["prev_verdict"], "FAIL")
        self.assertTrue(ent["flake"])
        self.assertEqual(ent["rule"], "rerun")

    def test_to_class_only_never_reruns_or_feeds(self):
        s = summary("FAIL", "fail", to_class_only=True, needs={"logs": "DIFF"})
        res, jobs, rs, _ = self.go(lambda a: s)
        self.assertEqual(len(jobs.calls), 1)
        self.assertEqual(rs.stops.fed, [])
        self.assertEqual(res["cells"][0]["verdict"], "FAIL")

    def test_stop_rules_get_only_fail_needs(self):
        s = summary("FAIL", "fail", needs={"try": "FAIL", "after:x": "LIMIT-NEW"})
        _, _, rs, _ = self.go(lambda a: s)
        self.assertEqual(rs.stops.fed[0]["needs"], {"try": "FAIL"})
        self.assertEqual(rs.stops.fed[0]["os"], "apt")

    def test_overrun_retries_doubled_and_feeds_nothing(self):
        res, jobs, rs, _ = self.go(lambda a: summary("HARNESS", "harness", overrun=True) if a == 1 else summary())
        self.assertEqual([(c["attempt"], c["d"]) for c in jobs.calls], [(1, 8.0), (2, 16.0)])
        self.assertEqual(res["cells"][0]["verdict"], "PASS")
        self.assertEqual([f["class"] for f in rs.stops.fed], ["pass"])

    def test_overrun_at_the_cap_counts(self):
        res, jobs, rs, _ = self.go(lambda a: summary("HARNESS", "harness", overrun=True))
        self.assertEqual([(c["attempt"], c["d"]) for c in jobs.calls], [(1, 8.0), (2, 16.0)])   # 16 is the cap: no third
        self.assertEqual(res["cells"][0]["verdict"], "HARNESS")
        self.assertEqual([f["class"] for f in rs.stops.fed], ["harness"])

    def test_attempts_at_most_three(self):
        res, jobs, rs, _ = self.go(lambda a: summary("HARNESS", "harness", overrun=True) if a == 1 else summary("FAIL", "fail"))
        self.assertEqual([c["attempt"] for c in jobs.calls], [1, 2, 3])

    def test_update_never_reruns(self):
        w = Word(update=True)
        io, jobs = FakeIO(), FakeJobs()
        io.summ["debian12"] = summary("FAIL", "fail")
        r, rs, ctx, _ = make(["debian12"], io, jobs, word=w, fleet=mock.Mock(take=lambda: True))
        with mock.patch.object(guest.Runner, "tree_matches", lambda *a: None), mock.patch.object(guest.Runner, "reproject_if_needed", lambda *a: None):
            r.run()
        self.assertEqual(len(jobs.calls), 1)
        self.assertIn(" update debian12 ", jobs.calls[0]["cmd"])
        self.assertEqual(jobs.calls[0]["env"]["VM_UPDATE_ROUND"], rs.run)

    def test_a_job_that_left_no_row_gets_a_harness_row(self):
        jobs = FakeJobs()
        io = FakeIO()
        r, rs, ctx, _ = make(["debian12"], io, jobs)
        with mock.patch.object(guest, "harness_row", lambda *a, **k: {"needs": {"harness": "FAIL"}, "fails": ["x"]}), \
                mock.patch.object(guest, "append_row", lambda *a, **k: "rid"):
            res = r.run()
        self.assertEqual(res["cells"][0]["verdict"], "HARNESS")
        self.assertEqual(res["cells"][0]["rows"], ["rid"])

    def test_a_stop_takes_the_rest_out(self):
        io, jobs = FakeIO(), FakeJobs()
        io.summ["debian12"] = summary()
        r, rs, _, _ = make(["debian12", "arch"], io, jobs, hosts=("chopin",))
        rs.stops.go = False
        res = r.run()
        self.assertEqual(jobs.calls, [])
        self.assertEqual({c["outcome"] for c in res["cells"]}, {"skipped"})

    def test_guards_stop_starting(self):
        io, jobs = FakeIO(), FakeJobs()
        r, rs, _, _ = make(["debian12"], io, jobs)
        rs.guards = mock.Mock(tripped=lambda: True)
        res = r.run()
        self.assertEqual(jobs.calls, [])
        self.assertEqual(res["cells"][0]["reasons"], ["a run-wide stop rule fired first"])

    def test_stop_os_groups_by_package_manager(self):
        io, jobs = FakeIO(), FakeJobs()
        io.summ["arch"] = summary()
        r, rs, _, _ = make(["debian12", "arch"], io, jobs, hosts=("chopin",))
        rs.stops.stop_os = {"apt"}
        res = r.run()
        self.assertEqual([c["guest"] for c in jobs.calls], ["arch"])

    def resume_run(self, reattach, io=None, word=None, st=None):
        io = io or FakeIO()
        jobs = FakeJobs()
        r, rs, ctx, _ = make(["debian12", "arch"], io, jobs, word=word, hosts=("chopin",), reattach=reattach)
        r.state["ended"]["debian12"] = {"cell": "guest:debian12", "verdict": "PASS", "host": "chopin", "rows": ["x"]}
        r.state["started"]["arch"] = dict({"host": "chopin", "attempt": 1, "d": 8.0}, **(st or {}))
        r.state["identity_done"] = True
        return r, jobs, io

    def test_resume_adopts_a_started_job_and_starts_nothing_beside_it(self):
        seen = []

        def reattach(env, host, name, attempt, timeout, wait, subdir, on_began=None):
            seen.append((host, name, attempt))
            on_began()
            return 0, hostjob.job_dir(env, name, attempt, subdir), {}, None
        io = FakeIO()
        io.summ["arch"] = summary()
        r, jobs, _ = self.resume_run(reattach, io)
        res = r.run(resume=True)
        self.assertEqual(seen, [("chopin", "arch", 1)])
        self.assertEqual(jobs.calls, [])
        self.assertEqual(sorted(c["cell"] for c in res["cells"]), ["guest:arch", "guest:debian12"])
        self.assertEqual(r.line["chopin"], 0)

    def test_resume_a_job_proven_gone_starts_the_next_attempt(self):
        def reattach(env, host, name, attempt, timeout, wait, subdir, on_began=None):
            return None, hostjob.job_dir(env, name, attempt, subdir), {}, hostjob.JobFailure("gone", gone=True)
        io = FakeIO()
        io.summ[("arch", 2)] = summary()
        r, jobs, io = self.resume_run(reattach, io)
        with mock.patch.object(guest, "harness_row", lambda *a, **k: {"x": 1, "needs": {"harness": "FAIL"}, "fails": ["f"]}), mock.patch.object(guest, "append_row", lambda *a, **k: "rid"):
            r.run(resume=True)
        self.assertEqual([(c["guest"], c["attempt"]) for c in jobs.calls], [("arch", 2)])

    def test_resume_never_starts_a_second_real_update(self):
        def reattach(env, host, name, attempt, timeout, wait, subdir, on_began=None):
            return None, hostjob.job_dir(env, name, attempt, subdir), {}, hostjob.JobFailure("gone", gone=True)
        r, jobs, io = self.resume_run(reattach, word=Word(update=True))
        r.fleet = mock.Mock(take=lambda: True)
        r.tree_matches = lambda h: None
        r.reproject_if_needed = lambda h, i: None
        r.state["wave1"] = []
        with mock.patch.object(guest, "harness_row", lambda *a, **k: {"x": 1, "needs": {"harness": "FAIL"}, "fails": ["f"]}), mock.patch.object(guest, "append_row", lambda *a, **k: "rid"):
            res = r.run(resume=True)
        self.assertEqual(jobs.calls, [])
        self.assertEqual({c["cell"]: c["verdict"] for c in res["cells"]}["guest:arch"], "HARNESS")

    def test_resume_a_job_still_running_is_no_rerun(self):
        def reattach(env, host, name, attempt, timeout, wait, subdir, on_began=None):
            return None, hostjob.job_dir(env, name, attempt, subdir), {}, hostjob.JobFailure("no answer", still_running=True)
        r, jobs, io = self.resume_run(reattach)
        with mock.patch.object(guest, "harness_row", lambda *a, **k: {"x": 1, "needs": {"harness": "FAIL"}, "fails": ["f"]}), mock.patch.object(guest, "append_row", lambda *a, **k: "rid"):
            r.run(resume=True)
        self.assertEqual(jobs.calls, [])

    def test_resume_keeps_a_reruns_context(self):
        def reattach(env, host, name, attempt, timeout, wait, subdir, on_began=None):
            return 0, hostjob.job_dir(env, name, attempt, subdir), {}, None
        io = FakeIO()
        io.summ["arch"] = lambda a: summary("FAIL", "fail", needs={"try": "FAIL"})
        r, jobs, _ = self.resume_run(reattach, io, st={"attempt": 2, "prev_verdict": "FAIL", "rerun_done": True, "rows": ["r1"]})
        res = r.run(resume=True)
        self.assertEqual(jobs.calls, [])   # its one rerun was this attempt
        arch = [c for c in res["cells"] if c["cell"] == "guest:arch"][0]
        self.assertEqual(len(arch["rows"]), 2)

    def test_a_skipped_guest_stays_in_the_resumed_report(self):
        r, rs, ctx, io = make(["arch"], hosts=("chopin",))
        r.state["skipped"] = {"arch": "a stop rule stopped the run first"}
        r.state["identity_done"] = True
        res = r.run(resume=True)
        self.assertEqual([(c["cell"], c["outcome"]) for c in res["cells"]], [("guest:arch", "skipped")])

    def test_an_identity_failure_survives_a_resume(self):
        io, jobs = FakeIO(), FakeJobs()
        io.identity = lambda h, g, a: {"missing": True}
        r, rs, ctx, _ = make(["debian12"], io, jobs, hosts=("chopin",))
        with mock.patch.object(guest, "harness_row", lambda *a, **k: {"x": 1}), mock.patch.object(guest, "append_row", lambda *a, **k: "rid"):
            r.run()
        r2 = guest.Runner(ctx, r.plan, io=FakeIO(), job=jobs, poll=0)
        res = r2.run(resume=True)
        self.assertEqual(jobs.calls, [])
        self.assertEqual([(c["cell"], c["verdict"]) for c in res["cells"]], [("guest:debian12", "FAIL")])

    def test_a_host_that_didnt_answer_identity_is_harness_and_no_fail(self):
        io, jobs = FakeIO(), FakeJobs()
        io.script = mock.Mock(side_effect=RuntimeError("ssh timed out"))
        written = []
        r, rs, ctx, _ = make(["debian12"], io, jobs, hosts=("chopin",))

        def row(*a, **k):
            written.append(k)
            return {"x": 1}
        with mock.patch.object(guest, "harness_row", row), mock.patch.object(guest, "append_row", lambda *a, **k: "rid"):
            res = r.run()
        self.assertEqual(jobs.calls, [])
        c = res["cells"][0]
        self.assertEqual(c["verdict"], "HARNESS")
        self.assertIn("no answer", c["note"])
        self.assertEqual(io.script.call_count, 2)    # asked again before giving up
        self.assertNotIn("verdict", written[0])      # harness_row's default: HARNESS, not FAIL

    def test_a_job_that_raises_gives_its_line_slot_back(self):
        io, jobs = FakeIO(), FakeJobs()
        io.summ["arch"] = summary()
        r, rs, ctx, _ = make(["debian12", "arch"], io, jobs, hosts=("chopin",))
        real = r.write_job_cells
        r.write_job_cells = lambda it, h: (_ for _ in ()).throw(RuntimeError("no copy")) if it.guest == "debian12" else real(it, h)
        with mock.patch.object(guest, "harness_row", lambda *a, **k: {"x": 1, "needs": {"harness": "FAIL"}, "fails": ["f"]}), mock.patch.object(guest, "append_row", lambda *a, **k: "rid"):
            done = threading.Event()
            t = threading.Thread(target=lambda: (r.run(), done.set()), daemon=True)
            t.start()
            self.assertTrue(done.wait(10), "the run never ended")
        self.assertEqual(r.line["chopin"], 0)
        self.assertEqual([c["guest"] for c in jobs.calls], ["arch"])

    def test_an_overrun_doesnt_wipe_an_earlier_fail(self):
        io, jobs = FakeIO(), FakeJobs()
        seq = {1: summary("FAIL", "fail", needs={"try": "FAIL"}), 2: summary("HARNESS", "harness", overrun=True), 3: summary()}
        io.summ["debian12"] = lambda a: seq[a]
        r, rs, ctx, _ = make(["debian12"], io, jobs, hosts=("chopin",))
        files = []
        real = r.write_job_cells

        def spy(it, h):
            files.append((it.attempt, it.prev_verdict))
            return real(it, h)
        r.write_job_cells = spy
        r.run()
        self.assertEqual(files, [(1, None), (2, "FAIL"), (3, "FAIL")])

    def test_an_overrun_below_the_cap_never_feeds_even_when_attempts_ran_out(self):
        io, jobs = FakeIO(), FakeJobs()
        io.summ["debian12"] = summary("HARNESS", "harness", overrun=True)
        r, rs, ctx, _ = make(["debian12"], io, jobs, hosts=("chopin",))
        r.cap = 64.0
        with mock.patch.object(guest, "reserve_for", lambda *a, **k: (1.0, 3)):
            r.run_items = None
            r.pending.append(r.item_of(r.plan.cells[0]))
            r.state["identity_done"] = True
            r.drive()
        self.assertEqual([c["d"] for c in jobs.calls], [1.0, 2.0, 4.0])
        self.assertEqual(rs.stops.fed, [])

    def test_a_console_guests_fail_doesnt_rerun(self):
        io, jobs = FakeIO(), FakeJobs()
        io.summ["redox"] = summary("FAIL", "fail", needs={"new:probe": "FAIL"})
        r, rs, ctx, _ = make(["redox"], io, jobs, hosts=("chopin",))
        r.run()
        self.assertEqual(len(jobs.calls), 1)

    def test_a_summary_with_no_row_is_harness(self):
        io, jobs = FakeIO(), FakeJobs()

        def fetch(host, d, names):
            return {"row-summary.json": json.dumps(summary())}
        io.fetch = fetch
        jobs_rc = FakeJobs()
        real = jobs_rc.__call__

        def job(*a, **k):
            rc, odir, t, why = real(*a, **k)
            return 3, odir, t, why
        r, rs, ctx, _ = make(["debian12"], io, job, hosts=("chopin",))
        with mock.patch.object(guest, "harness_row", lambda *a, **k: {"x": 1, "needs": {"harness": "FAIL"}, "fails": ["f"]}), mock.patch.object(guest, "append_row", lambda *a, **k: "rid"):
            res = r.run()
        self.assertEqual(res["cells"][0]["verdict"], "HARNESS")

    def test_a_failed_fetch_is_tried_again(self):
        io, jobs = FakeIO(), FakeJobs()
        io.summ["debian12"] = summary()
        calls = []
        real = io.fetch

        def flaky(host, d, names):
            calls.append(d)
            return {} if len(calls) == 1 else real(host, d, names)
        io.fetch = flaky
        r, rs, ctx, _ = make(["debian12"], io, jobs, hosts=("chopin",))
        res = r.run()
        self.assertEqual(res["cells"][0]["verdict"], "PASS")

    def test_identity_failure_runs_no_job(self):
        io, jobs = FakeIO(), FakeJobs()
        io.identity = lambda h, g, a: {"missing": True}
        r, rs, ctx, _ = make(["debian12"], io, jobs)
        with mock.patch.object(guest, "harness_row", lambda *a, **k: {"x": 1}), mock.patch.object(guest, "append_row", lambda *a, **k: "rid"):
            res = r.run()
        self.assertEqual(jobs.calls, [])
        self.assertEqual(res["cells"][0]["needs"], {"identity": "FAIL"})
        self.assertEqual(res["exit"], 1)

    def test_baselines_go_to_the_hosts(self):
        io, jobs = FakeIO(), FakeJobs()
        io.summ["debian12"] = summary()
        r, rs, ctx, _ = make(["debian12"], io, jobs)
        p = os.path.join(rs.mac_root, "guest-baselines", "debian12.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write('{"lines": []}')
        r.run()
        self.assertTrue(any("baselines/debian12.json" in c for _h, c, _i in io.shell))


class Waves(unittest.TestCase):
    def test_first_of_each_package_manager(self):
        io = FakeIO()
        r, rs, ctx, _ = make(["debian12", "mint22", "arch", "cachyos"], io)
        items = [r.item_of(pc) for pc in r.plan.cells]
        for it, gm in zip(items, (3.0, 1.0, 2.0, 5.0)):
            it.pc.est = {"gm": gm}
        w1, w2 = r.waves(items)
        self.assertEqual(sorted(i.guest for i in w1), ["arch", "mint22"])
        self.assertEqual(sorted(i.guest for i in w2), ["cachyos", "debian12"])

    def test_a_blocked_package_manager_doesnt_go_on(self):
        io, jobs = FakeIO(), FakeJobs()
        io.summ["mint22"] = summary("FAIL", "fail")
        io.summ["arch"] = summary()
        io.summ["cachyos"] = summary()
        r, rs, ctx, _ = make(["debian12", "mint22", "arch", "cachyos"], io, jobs, word=Word(update=True), fleet=mock.Mock(take=lambda: True))
        for pc, gm in zip(r.plan.cells, (3.0, 1.0, 2.0, 5.0)):
            pc.est = {"gm": gm}
        with mock.patch.object(guest.Runner, "tree_matches", lambda *a: None), mock.patch.object(guest.Runner, "reproject_if_needed", lambda *a: None):
            res = r.run()
        ran = [c["guest"] for c in jobs.calls]
        self.assertNotIn("debian12", ran)
        self.assertIn("cachyos", ran)
        skip = [c for c in res["cells"] if c["outcome"] == "skipped"]
        self.assertEqual([c["cell"] for c in skip], ["guest:debian12"])
        self.assertIn("not run", "\n".join(res["blocks"]))

    def test_only_wave_one_blocks(self):
        io, jobs = FakeIO(), FakeJobs()
        for g in ("debian12", "mint22"):
            io.summ[g] = summary(merged=True)
        r, rs, ctx, _ = make(["debian12", "mint22", "zorin18"], io, jobs, word=Word(update=True), hosts=("chopin",))
        r.fleet = mock.Mock(take=lambda: True)
        r.tree_matches = lambda h: None
        r.reproject_if_needed = lambda h, i: None
        r.plan.cells[1].est = {"gm": 0.1}   # mint22 is apt's wave 1
        real = r.identity_entries

        def identity(items):
            r.cells.append({"cell": "guest:zorin18", "verdict": "FAIL", "pm": "apt", "host": "chopin"})
            return real([it for it in items if it.guest != "zorin18"])
        r.identity_entries = identity
        r.run()
        self.assertEqual([c["guest"] for c in jobs.calls], ["mint22", "debian12"])

    def test_a_lost_fleet_lock_holder_starts_no_more_jobs(self):
        io, jobs = FakeIO(), FakeJobs()
        for g in ("debian12", "mint22", "arch", "cachyos"):
            io.summ[g] = summary()

        class Fleet:
            def take(self):
                return True

            def lost(self):
                return "its ssh ended" if len(jobs.calls) >= 2 else None

            def release(self):
                pass
        r, rs, ctx, _ = make(["debian12", "mint22", "arch", "cachyos"], io, jobs, word=Word(update=True), fleet=Fleet())
        for pc, gm in zip(r.plan.cells, (3.0, 1.0, 2.0, 5.0)):
            pc.est = {"gm": gm}
        with mock.patch.object(guest.Runner, "tree_matches", lambda *a: None), mock.patch.object(guest.Runner, "reproject_if_needed", lambda *a: None):
            res = r.run()
        self.assertEqual(sorted(c["guest"] for c in jobs.calls), ["arch", "mint22"], "wave 2 started without a holder")
        skip = sorted(c["cell"] for c in res["cells"] if c["outcome"] == "skipped")
        self.assertEqual(skip, ["guest:cachyos", "guest:debian12"])
        self.assertTrue(all("fleet-lock holder is gone" in c["note"] for c in res["cells"] if c["outcome"] == "skipped"))
        self.assertEqual(sum("fleet-lock holder was lost" in n for n in res["notes"]), 1, res["notes"])
        self.assertIn("not run: the round's fleet-lock holder is gone", "\n".join(res["blocks"]))
        self.assertEqual(res["exit"], 1)

    def test_a_tree_that_isnt_this_checkout_refuses(self):
        io, jobs = FakeIO(), FakeJobs()
        fl = mock.Mock(take=lambda: True)
        r, rs, ctx, _ = make(["debian12"], io, jobs, word=Word(update=True), fleet=fl)
        with mock.patch.object(guest.Runner, "tree_matches", lambda *a: "differs"):
            res = r.run()
        self.assertEqual(jobs.calls, [])
        self.assertEqual(res["exit"], 2)


# --- the row builder ---------------------------------------------------------------------------------

class HC:
    """A HostCtx with no host."""

    def __init__(self, work, hroot):
        self.base, self.work, self.host_root, self.host = REPO, work, hroot, "chopin"

    def now(self):
        return "2026-11-10T01:00:00Z"

    def source(self):
        from t2lib import keys
        return keys.TreeSource(REPO)

    def measure(self, cell, opts=None, src=None, specs=None, want=None, facts=None):
        return {"inputs": {"harness": {"x": "sha256:" + "a" * 64}}, "key": "k" * 64, "unknown": [], "spec": None, "notes": []}

    def row(self, **f):
        return f


class Rows(unittest.TestCase):
    def setUp(self):
        self.t = tmpdir()
        self.work, self.hroot = os.path.join(self.t, "w"), os.path.join(self.t, "h")
        self.out = os.path.join(self.t, "out")
        os.makedirs(self.out)
        os.makedirs(os.path.join(self.work, "vms", "debian12"))
        with open(os.path.join(self.work, "vms", "debian12", "image-state"), "w", encoding="utf-8") as f:
            json.dump({"generation": "g" * 64, "updates": 2}, f)
        self.cells = os.path.join(self.t, "cells.json")
        self.write_cells({"rule": "guest:7day", "reasons": ["r"], "attempt": 1, "planned_key": "k" * 64})
        self.hooks()

    def write_cells(self, ent):
        with open(self.cells, "w", encoding="utf-8") as f:
            json.dump({"word": "GuestTest2", "cells": {"debian12": ent}}, f)

    def hooks(self, before=None, after=None):
        for n, t in (("before", before), ("after", after)):
            with open(os.path.join(FX, f"hook-{n}.out"), encoding="utf-8") as f:
                text = f.read()
            with open(os.path.join(self.out, f"hook-{n}.out"), "w", encoding="utf-8") as f:
                f.write(text if t is None else t)

    def result(self, **kw):
        d = dict(guest="debian12", mode="try", rc=0, phase="done", merge={"decided": False, "why": "nothing", "pkg_changed": False, "steps": [], "unknown": False, "gate": None},
                 merged=False, overlay_mib=1, overlay_dir=None, ram_peak_kib=100000, reserved_gib=8, overrun=False, generation_before="g", generation_after="g",
                 trim_rc=None, wall_s=120, tail=[], hooks={"before": {"rc": 0, "sentinel": True}, "after": {"rc": 0, "sentinel": True}})
        d.update(kw)
        with open(os.path.join(self.out, "result.json"), "w", encoding="utf-8") as f:
            json.dump(d, f)

    def build(self, *extra, rc=0, attempt=1, mode="try"):
        argv = ["--guest", "debian12", "--run", "t2v-x", "--attempt", str(attempt), "--out", self.out, "--mode", mode, "--rc", str(rc), "--cells", self.cells] + list(extra)
        return rows_guest.build(argv, HC(self.work, self.hroot))

    def baseline(self, lines):
        p = os.path.join(self.hroot, "runs", "t2v-x", "baselines")
        os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, "debian12.json"), "w", encoding="utf-8") as f:
            json.dump({"lines": lines}, f)

    def log(self, name, text):
        with open(os.path.join(self.out, name), "w", encoding="utf-8") as f:
            f.write(text)

    def test_pass(self):
        self.result()
        self.baseline([])
        self.log("update.log", "Fetched 1 kB in 1s\n")
        r = self.build()
        self.assertEqual((r["verdict"], r["class"]), ("PASS", "pass"))
        self.assertEqual(r["needs"]["image:sources"], "PASS")
        self.assertEqual(r["needs"]["after:boot-state"], "PASS")
        self.assertEqual(r["needs"]["try"], "PASS")
        self.assertEqual(r["needs"]["logs"], "PASS")
        self.assertTrue(r["extra"]["try_ran"])
        self.assertEqual(r["origin"], "guest:t2v-x:debian12:1")
        self.assertEqual(r["gm"], 2.0)
        self.assertEqual(r["ram_peak_kib"], 100000)
        self.assertTrue(r["valid"])

    def new_guest(self, hook, rc=0, **kw):
        g = "ubuntu26-raid"
        os.makedirs(os.path.join(self.work, "vms", g), exist_ok=True)
        with open(os.path.join(self.work, "vms", g, "image-state"), "w", encoding="utf-8") as f:
            json.dump({"generation": "g" * 64, "updates": 2}, f)
        with open(self.cells, "w", encoding="utf-8") as f:
            json.dump({"word": "GuestTest2", "cells": {g: {"rule": "guest:7day", "reasons": ["r"], "attempt": 1, "planned_key": "k" * 64}}}, f)
        hooks = dict({"before": {"rc": 0, "sentinel": True}, "after": {"rc": 0, "sentinel": True}}, **({"new": {"rc": 0, "sentinel": True}} if hook is not None else {}))
        self.result(guest=g, hooks=hooks, **kw)
        if hook is not None:
            self.log("hook-new.out", hook)
        argv = ["--guest", g, "--run", "t2v-x", "--attempt", "1", "--out", self.out, "--mode", "try", "--rc", str(rc), "--cells", self.cells]
        return rows_guest.build(argv, HC(self.work, self.hroot))

    GOOD_NEW = "=== cmd firmware\nuefi\n=== cmd restic-version\nrestic 0.18\n=== cmd rustic-version\nrustic 0.10\n=== cmd smoke\nok restic\nok rustic\n=== end\n"

    def test_a_new_guests_hook_is_read_under_the_name_vm_update_gives_it(self):
        bad = "=== cmd firmware\nbios\n=== cmd restic-version\nfailed /tmp/restic-bko\n=== cmd rustic-version\nfailed /tmp/rustic-bko\n=== cmd smoke\nfailed restic\nfailed rustic\n=== end\n"
        r = self.new_guest(bad)
        for n in ("new:restic", "new:rustic", "new:smoke"):
            self.assertEqual(r["needs"][n], "FAIL", n)
        self.assertEqual(r["needs"]["new:ssh"], "PASS")
        self.assertIn("new:firmware", r["needs"])

    def test_a_new_guests_hook_that_never_ran_is_harness(self):
        r = self.new_guest(None)
        self.assertEqual(r["verdict"], "HARNESS")
        self.assertEqual(r["extra"]["hook_failed"], ["new"])
        self.assertEqual(r["needs"]["new:smoke"], "FAIL")

    def test_hook_names_are_the_ones_vm_update_makes(self):
        """job.sh's real --hook arguments through vm-update's real hooks_of."""
        import ast
        import pathlib
        with open(os.path.join(REPO, "testvm", "vm-update"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "hooks_of")
        ns = {"Path": pathlib.Path}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "vm-update", "exec"), ns)
        with open(os.path.join(REPO, "testvm", "test2", "guest", "job.sh"), encoding="utf-8") as f:
            specs = re.findall(r'--hook "(\w+=)\$H/(\w+)"', f.read())
        self.assertEqual(sorted(x for _, x in specs), ["after", "before", "new"])
        got = ns["hooks_of"](mock.Mock(hook=[f"{a}/o/hooks/{b}" for a, b in specs]))
        names = {n for v in got.values() for n, _ in v}
        self.assertEqual(names, set(rows_guest.HOOK_FILE.values()))

    def test_rc_0_with_a_hook_that_never_ran_is_harness(self):
        os.remove(os.path.join(self.out, "hook-after.out"))
        self.result(hooks={"before": {"rc": 0, "sentinel": True}})
        r = self.build()
        self.assertEqual(r["verdict"], "HARNESS")
        self.assertEqual(r["extra"]["hook_failed"], ["after"])
        self.assertEqual(r["needs"]["after:boot-state"], "FAIL")

    def test_nothing_updates_this_guest_is_no_try(self):
        os.remove(os.path.join(self.out, "hook-after.out"))
        self.result(hooks={"before": {"rc": 0, "sentinel": True}}, merge={"decided": False, "why": "nothing updates this guest"})
        self.baseline([])
        r = self.build()
        self.assertEqual(r["needs"]["try"], "SKIP")
        self.assertFalse(r["extra"]["try_ran"])
        self.assertEqual(r["extra"]["skips"]["try"], "nothing updates this guest")
        self.assertEqual(r["needs"]["after:boot-state"], "PASS")   # the before reading, judged as a probe's

    def test_u0_sources_read_the_count_before_the_merge(self):
        with open(os.path.join(self.work, "vms", "debian12", "image-state"), "w", encoding="utf-8") as f:
            json.dump({"generation": "g" * 64, "updates": 1}, f)
        self.result(merged=True)
        r = self.build(mode="update")
        self.assertEqual(r["needs"]["image:sources"], "SKIP")
        self.assertIn("not updated yet", r["extra"]["skips"]["image:sources"])

    def test_a_skip_names_why_and_a_pass_keeps_its_note(self):
        with open(os.path.join(self.work, "vms", "debian12", "image-state"), "w", encoding="utf-8") as f:
            json.dump({"generation": "g" * 64, "updates": 0}, f)
        self.result()
        r = self.build()
        self.assertIn("not updated yet", r["extra"]["skips"]["image:sources"])
        self.assertIn("kernel", r["extra"]["notes"]["image:kernels"])

    def test_a_lost_hook_is_named_and_its_needs_fail(self):
        os.remove(os.path.join(self.out, "hook-before.out"))
        os.remove(os.path.join(self.out, "hook-after.out"))
        self.result(phase="before-hook", hooks={})
        r = self.build(rc=1)
        self.assertEqual(r["verdict"], "HARNESS")
        self.assertEqual(r["extra"]["hook_failed"], ["before"])
        self.assertEqual(r["needs"]["image:free"], "FAIL")

    def test_a_probe_that_ends_badly_is_harness(self):
        self.result(mode="probe", phase="poweroff", hooks={"before": {"rc": 0, "sentinel": True}})
        r = self.build(rc=1, mode="probe")
        self.assertEqual(r["verdict"], "HARNESS")

    def test_stopped_from_outside_during_the_update(self):
        for how in ("tail", "mark"):
            self.result(phase="update", tail=["stopped before merging"] if how == "tail" else ["the update failed (exit status 1)"],
                        hooks={"before": {"rc": 0, "sentinel": True}})
            if how == "mark":
                self.log(".stopped", "")
            r = self.build(rc=1)
            self.assertEqual(r["verdict"], "HARNESS", how)
            self.assertIn("stopped from outside", r["fails"][0], how)

    def test_no_log_is_a_skip_with_why(self):
        self.result()
        self.baseline([])
        r = self.build()
        self.assertEqual(r["needs"]["logs"], "SKIP")
        self.assertEqual(r["extra"]["skips"]["logs"], "no log was read")

    def test_a_log_that_was_cut_says_so(self):
        self.result()
        self.baseline([])
        self.log("update.log", "Fetched 1 kB in 1s\n" * (J.LINES_MAX + 5))
        r = self.build()
        self.assertEqual(r["needs"]["logs"], "PASS")
        self.assertIn("only the last", r["extra"]["notes"]["logs"])

    def test_the_builder_digest_is_only_the_builder(self):
        raw = {"harness": ["testvm/make-debian.sh", "testvm/debian/**", "testvm/compare/boot-state.sh", "testvm/console-boot.py", "testvm/haiku/drive.py",
                           "testvm/update/in-guest.sh#Linux.apt", "testvm/test2/guest/new-guest.sh"]}
        base = {"testvm/make-debian.sh": "sha256:1", "testvm/debian/a": "sha256:2", "testvm/compare/boot-state.sh": "sha256:3", "testvm/console-boot.py": "sha256:4"}
        d0 = rows_guest.builder_digest(raw, {"harness": base})
        for k in ("testvm/compare/boot-state.sh", "testvm/console-boot.py"):
            self.assertEqual(rows_guest.builder_digest(raw, {"harness": dict(base, **{k: "sha256:9"})}), d0, k)
        for k in ("testvm/make-debian.sh", "testvm/debian/a"):
            self.assertNotEqual(rows_guest.builder_digest(raw, {"harness": dict(base, **{k: "sha256:9"})}), d0, k)

    def test_cut_off_hook_is_harness_and_its_needs_fail(self):
        with open(os.path.join(FX, "hook-before.out"), encoding="utf-8") as f:
            cut = f.read().replace("=== end\n", "")
        self.hooks(before=cut)
        self.result(hooks={"before": {"rc": 0, "sentinel": False}, "after": {"rc": 0, "sentinel": True}})
        r = self.build()
        self.assertEqual(r["verdict"], "HARNESS")
        self.assertEqual(r["extra"]["hook_failed"], ["before"])
        self.assertEqual(r["needs"]["image:sources"], "FAIL")

    def test_empty_hook_is_harness(self):
        self.hooks(after="")
        self.result(hooks={"before": {"rc": 0, "sentinel": True}, "after": {"rc": 0, "sentinel": False}})
        r = self.build()
        self.assertEqual(r["verdict"], "HARNESS")
        self.assertIn("after", r["extra"]["hook_failed"])
        self.assertEqual(r["needs"]["after:fstab"], "FAIL")

    def test_nonzero_hook_is_harness(self):
        self.result(hooks={"before": {"rc": 3, "sentinel": True}, "after": {"rc": 0, "sentinel": True}})
        self.assertEqual(self.build()["verdict"], "HARNESS")

    def test_no_result_json_is_harness(self):
        r = self.build(rc=2)
        self.assertEqual(r["verdict"], "HARNESS")

    def test_no_boot_is_harness(self):
        self.result(rc=1, phase="boot", hooks={})
        os.remove(os.path.join(self.out, "hook-before.out"))
        os.remove(os.path.join(self.out, "hook-after.out"))
        r = self.build(rc=1)
        self.assertEqual(r["verdict"], "HARNESS")

    def test_overrun_is_harness(self):
        self.result(rc=1, overrun=True)
        r = self.build(rc=1)
        self.assertEqual(r["verdict"], "HARNESS")
        self.assertTrue(r["extra"]["overrun"])

    def test_failed_reboot_is_a_fail_of_try(self):
        self.result(rc=1, phase="reboot", hooks={"before": {"rc": 0, "sentinel": True}})
        os.remove(os.path.join(self.out, "hook-after.out"))
        r = self.build(rc=1)
        self.assertEqual((r["verdict"], r["needs"]["try"]), ("FAIL", "FAIL"))
        self.assertEqual(r["needs"]["after:fstab"], "SKIP")
        self.assertFalse(r["extra"]["try_ran"])

    def test_no_baseline_is_diff_and_to_class(self):
        self.log("update.log", "new line 1\n")
        self.result()
        r = self.build()
        self.assertEqual(r["needs"]["logs"], "DIFF")
        self.assertEqual(r["verdict"], "FAIL")
        self.assertTrue(r["extra"]["to_class_only"])
        with open(os.path.join(self.out, "new-lines.json"), encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual([x["line"] for x in doc["lines"]], ["new line N"])

    def test_seen_before_from_older_logs(self):
        self.log("update.log", "new line 1\n")
        with open(os.path.join(self.work, "vms", "debian12", "update.log"), "w", encoding="utf-8") as f:
            f.write("new line 7\n")
        self.result()
        self.build()
        with open(os.path.join(self.out, "new-lines.json"), encoding="utf-8") as f:
            self.assertTrue(json.load(f)["lines"][0]["seen_before"])

    def test_accepted_line_clears_logs(self):
        self.log("update.log", "new line 1\n")
        self.result()
        self.baseline(["new line N"])
        r = self.build()
        self.assertEqual(r["needs"]["logs"], "PASS")

    def test_changed_builder_resets_the_baseline(self):
        self.log("update.log", "new line 1\n")
        self.result()
        p = os.path.join(self.hroot, "runs", "t2v-x", "baselines")
        os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, "debian12.json"), "w", encoding="utf-8") as f:
            json.dump({"lines": ["new line N"], "builder": "another"}, f)
        self.assertEqual(self.build()["needs"]["logs"], "DIFF")

    def test_standing_line_is_limit_new_and_to_class(self):
        with open(os.path.join(FX, "hook-before.out"), encoding="utf-8") as f:
            b = f.read().replace("state running", "state degraded\nfailed foo.service")
        with open(os.path.join(FX, "hook-after.out"), encoding="utf-8") as f:
            a = f.read().replace("state running", "state degraded\nfailed foo.service")
        self.hooks(before=b, after=a)
        self.result()
        self.baseline([])
        r = self.build()
        self.assertEqual(r["needs"]["after:failed-units"], "LIMIT-NEW")
        self.assertEqual(r["verdict"], "FAIL")
        self.assertTrue(r["extra"]["to_class_only"])
        self.assertEqual(r["extra"]["standing"]["after:failed-units"], ["failed foo.service"])

    def test_new_line_is_fail_not_to_class(self):
        with open(os.path.join(FX, "hook-after.out"), encoding="utf-8") as f:
            a = f.read().replace("state running", "state degraded")
        self.hooks(after=a)
        self.result()
        self.baseline([])
        r = self.build()
        self.assertEqual(r["needs"]["after:boot-state"], "FAIL")
        self.assertFalse(r["extra"]["to_class_only"])

    def test_probe_one_reading_and_try_skip(self):
        os.remove(os.path.join(self.out, "hook-after.out"))
        self.result(mode="probe", hooks={"before": {"rc": 0, "sentinel": True}})
        self.baseline([])
        r = self.build(mode="probe")
        self.assertEqual(r["needs"]["try"], "SKIP")
        self.assertEqual(r["verdict"], "PASS")
        self.assertFalse(r["extra"]["try_ran"])

    def test_a_merged_update_isnt_a_try(self):
        self.result(mode="update", merged=True, merge={"decided": True, "why": "pkgs", "pkg_changed": True, "steps": ["s"], "unknown": False, "gate": 0})
        self.baseline([])
        r = self.build(mode="update")
        self.assertTrue(r["extra"]["merged"])
        self.assertFalse(r["extra"]["try_ran"])
        self.assertEqual(r["verdict"], "PASS")

    def test_an_update_that_merged_nothing_is_a_try(self):
        self.result(mode="update")
        self.baseline([])
        r = self.build(mode="update")
        self.assertFalse(r["extra"]["merged"])
        self.assertTrue(r["extra"]["try_ran"])

    def test_a_blocked_merge_is_a_fail(self):
        self.result(mode="update", rc=10, phase="gate", merge={"decided": False, "why": "the merge gate blocked it: x", "pkg_changed": True, "steps": [], "unknown": False, "gate": 10})
        self.baseline([])
        r = self.build(rc=10, mode="update")
        self.assertEqual(r["verdict"], "FAIL")
        self.assertEqual(r["extra"]["merge_why"], "the merge gate blocked it: x")
        self.assertFalse(r["extra"]["merged"])

    def test_rerun_verdicts(self):
        self.result()
        self.baseline([])
        self.write_cells({"rule": "rerun", "attempt": 2, "prev_verdict": "FAIL", "flake": None, "planned_key": "k" * 64})
        self.assertEqual(self.build(attempt=2)["verdict"], "FLAKY-FAIL")
        self.write_cells({"rule": "rerun", "attempt": 2, "prev_verdict": "FAIL", "flake": {"pattern": "x"}, "planned_key": "k" * 64})
        r = self.build(attempt=2)
        self.assertEqual((r["verdict"], r["class"]), ("PASS", "known-flake"))

    def test_changed_key_is_invalid(self):
        self.result()
        self.write_cells({"rule": "x", "planned_key": "b" * 64})
        r = self.build()
        self.assertFalse(r["valid"])
        self.assertIn("differ from the plan", r["note"])

    def test_by_hand(self):
        self.result()
        r = rows_guest.build(["--guest", "debian12", "--out", self.out, "--mode", "try", "--rc", "0", "--by-hand"], HC(self.work, self.hroot))
        self.assertEqual(r["rule"], "test1")
        self.assertFalse(r["valid"])
        self.assertEqual(r["note"], "by hand: no Test2 checks ran")

    def test_console(self):
        os.remove(os.path.join(self.out, "hook-before.out"))
        os.remove(os.path.join(self.out, "hook-after.out"))
        self.log("hook-probe.out", "=== cmd probe\nok\n=== end\n")
        self.result(mode="console", hooks={})
        self.baseline([])
        r = rows_guest.build(["--guest", "redox", "--run", "t2v-x", "--out", self.out, "--mode", "console", "--rc", "0", "--cells", self.cells], HC(self.work, self.hroot))
        self.assertEqual(r["needs"]["new:probe"], "PASS")
        self.assertEqual(r["needs"]["try"], "SKIP")


# --- the host tools --------------------------------------------------------------------------------

class Identity(unittest.TestCase):
    def info(self, gen="g", stat=True, **d):
        st = {"generation": gen, "disks": {"a": {"size": 1, "mtime_ns": 2, "inode": 3}}} if stat else None
        return dict({"image_state": {"generation": gen, "sha256": {"a": "S"}}, "stat": st, "disks": {"a": {"size": 1, "mtime_ns": 2, "inode": 3}},
                     "qemu": {"a": {"ok": True, "format": "qcow2", "backing": None, "dirty": False, "corrupt": False, "snapshots": ["fresh-install"]}}}, **d)

    def test_match(self):
        self.assertTrue(guest.identity_compare({"chopin": self.info(), "cinqcent": self.info()})["ok"])

    def test_generations_differ(self):
        r = guest.identity_compare({"chopin": self.info("a"), "cinqcent": self.info("b")})
        self.assertIs(r["ok"], False)

    def test_mismatch_asks_for_a_hash(self):
        i = self.info()
        i["disks"]["a"] = {"size": 1, "mtime_ns": 9, "inode": 3}
        r = guest.identity_compare({"chopin": self.info(), "cinqcent": i})
        self.assertIsNone(r["ok"])
        self.assertEqual(r["rehash"], ["cinqcent"])

    def test_missing_record_asks_for_a_hash(self):
        r = guest.identity_compare({"chopin": self.info(stat=False)})
        self.assertIsNone(r["ok"])

    def test_hash_decides(self):
        good = self.info(sha256={"a": "S"})
        self.assertTrue(guest.identity_compare({"chopin": good}, hashed=True)["ok"])
        bad = self.info(sha256={"a": "T"})
        self.assertIs(guest.identity_compare({"chopin": bad}, hashed=True)["ok"], False)

    def test_qemu_problems(self):
        for field, val in (("format", "raw"), ("backing", "x"), ("dirty", True), ("corrupt", True), ("snapshots", [])):
            i = self.info()
            i["qemu"]["a"][field] = val
            self.assertIs(guest.identity_compare({"chopin": i})["ok"], False, field)

    def thin(self, **base):
        """A world's thin disk (contract 4) over the fleet's, no image-state in the world."""
        bq = dict({"ok": True, "format": "qcow2", "backing": None, "dirty": False, "corrupt": False, "snapshots": ["fresh-install"]},
                  **base.pop("qemu", {}))
        b = dict({"path": "/r/vms/g/a", "qemu": bq, "generation": "G"}, **base)
        q = {"ok": True, "format": "qcow2", "backing": "/r/vms/g/a", "dirty": False, "corrupt": False, "snapshots": [], "base": b}
        return {"image_state": None, "stat": None, "disks": {"a": {}}, "qemu": {"a": q}, "sha256": {"a": "S"}}

    def test_world_thin_disk_over_the_fleet(self):
        self.assertTrue(guest.identity_compare({"chopin": self.thin()}, hashed=True)["ok"])
        self.assertTrue(guest.identity_compare({"chopin": self.thin(), "cinqcent": self.thin()}, hashed=True)["ok"])
        i = self.thin()   # after a merge in the world: its image-state, the snapshot still in the base
        d = {"size": 1, "mtime_ns": 2, "inode": 3}
        i.update(image_state={"generation": "w", "sha256": {"a": "S"}}, disks={"a": d}, stat={"generation": "w", "disks": {"a": d}})
        self.assertTrue(guest.identity_compare({"chopin": i})["ok"])

    def test_world_thin_disk_refused(self):
        cases = (("not over the fleet's disk", self.thin(), None),
                 ("base unreadable", self.thin(qemu={"ok": False, "why": "gone"}), "gone"),
                 ("base has a backing file", self.thin(qemu={"backing": "y"}), "sound"),
                 ("base dirty", self.thin(qemu={"dirty": True}), "sound"),
                 ("base corrupt", self.thin(qemu={"corrupt": True}), "sound"),
                 ("base raw", self.thin(qemu={"format": "raw"}), "sound"),
                 ("base has no image-state", self.thin(generation=None), "image-state"),
                 ("no fresh-install in the base", self.thin(qemu={"snapshots": []}), "fresh-install"))
        cases[0][1]["qemu"]["a"]["base"] = None
        for name, i, word in cases:
            r = guest.identity_compare({"chopin": i}, hashed=True)
            self.assertIs(r["ok"], False, name)
            self.assertIn(word or "it has a backing file", r["why"], name)
        i = self.thin()
        i["qemu"]["a"]["dirty"] = True
        self.assertIn("dirty", guest.identity_compare({"chopin": i}, hashed=True)["why"])
        r = guest.identity_compare({"chopin": self.thin(), "cinqcent": self.thin(generation="H")}, hashed=True)
        self.assertIs(r["ok"], False)
        self.assertIn("generations differ", r["why"])
        plain = dict(self.thin(), qemu={"a": {"ok": True, "format": "qcow2", "backing": None, "dirty": False, "corrupt": False, "snapshots": []}})
        r = guest.identity_compare({"chopin": self.thin(), "cinqcent": plain}, hashed=True)
        self.assertIs(r["ok"], False, "thin on one host only")

    def test_tool_world_base(self):
        home = tmpdir()
        real = os.path.join(home, "backotter-work", "vms", "g1")
        world = os.path.join(home, "backotter-work", "t2rest-x", "world")
        os.makedirs(real)
        os.makedirs(os.path.join(world, "vms", "g1"))
        with open(os.path.join(real, "image-state"), "w", encoding="utf-8") as f:
            json.dump({"generation": "G"}, f)
        thin = os.path.join(world, "vms", "g1", "a.qcow2")
        ok = {"ok": True}
        with mock.patch.object(tool_guest, "_home", lambda: home), mock.patch.object(tool_guest, "qemu_info", lambda p: ok):
            b = tool_guest.world_base(world, "g1", "a.qcow2", thin, {"backing": os.path.join(real, "a.qcow2")})
            self.assertEqual((b["path"], b["generation"], b["qemu"]), (os.path.join(real, "a.qcow2"), "G", ok))
            self.assertIsNone(tool_guest.world_base(world, "g1", "a.qcow2", thin, {"backing": os.path.join(real, "b.qcow2")}))
            self.assertIsNone(tool_guest.world_base(world, "g1", "a.qcow2", thin, {"backing": "/elsewhere/a.qcow2"}))
            self.assertIsNone(tool_guest.world_base(world, "g2", "a.qcow2", thin, {"backing": os.path.join(real, "a.qcow2")}))
            fleet = os.path.join(home, "backotter-work")
            self.assertIsNone(tool_guest.world_base(fleet, "g1", "a.qcow2", os.path.join(real, "a.qcow2"),
                                                    {"backing": os.path.join(real, "a.qcow2")}), "the fleet vouches for no backing file")

    def test_no_image_state_compares_the_hosts(self):
        a = {"image_state": None, "disks": {"a": {}}, "qemu": {}, "stat": None, "sha256": {"a": "S"}}
        b = dict(a, sha256={"a": "T"})
        self.assertIsNone(guest.identity_compare({"chopin": a, "cinqcent": a})["ok"])
        self.assertTrue(guest.identity_compare({"chopin": a, "cinqcent": a}, hashed=True)["ok"])
        self.assertIs(guest.identity_compare({"chopin": a, "cinqcent": b}, hashed=True)["ok"], False)

    def test_a_hash_nobody_made_vouches_for_nothing(self):
        a = dict(self.info(), image_state=None)
        for name, per in (("absent", {"chopin": a, "cinqcent": a}), ("none", {"chopin": dict(a, sha256={"a.qcow2": None}), "cinqcent": dict(a, sha256={"a.qcow2": None})}),
                          ("one host", {"chopin": a})):
            self.assertIs(guest.identity_compare(per, hashed=True)["ok"], False, name)

    def test_a_host_with_no_answer_is_harness(self):
        r = guest.identity_compare({"chopin": self.info(), "cinqcent": {"no_answer": True}})
        self.assertEqual((r["ok"], r["harness"]), (False, True))
        self.assertIn("cinqcent", r["why"])

    def test_tool_backfills_after_a_match(self):
        t = tmpdir()
        g = os.path.join(t, "vms", "g1")
        os.makedirs(g)
        with open(os.path.join(g, "a.qcow2"), "wb") as f:
            f.write(b"x")
        sha = tool_guest.sha256_file(os.path.join(g, "a.qcow2"))
        with open(os.path.join(g, "image-state"), "w", encoding="utf-8") as f:
            json.dump({"generation": "g", "updates": 1, "sha256": {"a.qcow2": sha}}, f)
        with mock.patch.object(tool_guest, "qemu_info", lambda p: {"ok": True}):
            r = tool_guest.identity_of(t, "g1", backfill=True)
        self.assertTrue(r["backfilled"])
        with open(os.path.join(g, "image-stat.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["generation"], "g")

    def test_tool_no_backfill_on_a_mismatch(self):
        t = tmpdir()
        g = os.path.join(t, "vms", "g1")
        os.makedirs(g)
        with open(os.path.join(g, "a.qcow2"), "wb") as f:
            f.write(b"x")
        with open(os.path.join(g, "image-state"), "w", encoding="utf-8") as f:
            json.dump({"generation": "g", "sha256": {"a.qcow2": "0" * 64}}, f)
        with mock.patch.object(tool_guest, "qemu_info", lambda p: {"ok": True}):
            r = tool_guest.identity_of(t, "g1", backfill=True)
        self.assertNotIn("backfilled", r)
        self.assertFalse(os.path.exists(os.path.join(g, "image-stat.json")))

    def test_identity_check_rehash_then_ok(self):
        io = FakeIO()
        rs = RS(Word())
        ctx = Ctx(rs)
        ctx.facts = {h: {"guests": {"g1": {}}} for h in rs.hosts}
        d = {"size": 1, "mtime_ns": 2, "inode": 3}

        def identity(host, g, args):
            base = {"image_state": {"generation": "g", "sha256": {"a": "S"}}, "disks": {"a": d}, "qemu": {},
                    "stat": {"generation": "g", "disks": {"a": dict(d, mtime_ns=(9 if host == "chopin" else 2))}}}
            if "--sha256" in args:
                base.update(sha256={"a": "S"}, backfilled=True, stat={"generation": "g", "disks": {"a": d}})
            return base
        io.identity = identity
        r = guest.identity_check(ctx, ["g1"], io=io)
        self.assertEqual(r, {"g1": {"ok": True, "why": None}})
        self.assertEqual(ctx.facts["chopin"]["guests"]["g1"]["stat"]["disks"]["a"], d)


class RemoteTool(unittest.TestCase):
    def test_it_runs_over_stdin_with_no_copy(self):
        t = tmpdir()
        os.makedirs(os.path.join(t, "vms", "g1"))
        with open(os.path.join(t, "vms", "g1", "a.qcow2"), "w", encoding="utf-8") as f:
            f.write("x")
        with mock.patch.dict(os.environ, {"T2_WORK": t}):
            rc, out = guest.remote_tool(hostjob.HostIO(), "local", ["identity", "g1"], timeout=60)
        self.assertEqual(rc, 0)
        self.assertIn("a.qcow2", json.loads(out)["g1"]["disks"])

    def test_preflight_says_what_the_sweep_said(self):
        io, said = FakeIO(), []
        rs = RS(Word(), hosts=("chopin",))
        ctx = Ctx(rs)
        ctx.facts = {"chopin": {}}
        io.script = lambda host, script, data, timeout=120: {"rc": 0, "out": "removed /x, a dead run's folder\nsweep: {}\n"}
        guest.preflight(ctx, said.append, io=io)
        self.assertEqual(said, ["chopin: removed /x, a dead run's folder"])


class PrePlan(unittest.TestCase):
    def test_disk_differs(self):
        g = {"generation": "g", "stat": {"generation": "g", "disks": {"a": {"size": 1, "mtime_ns": 2, "inode": 3}}},
             "disks": [{"path": "/x/a", "size": 1, "mtime_ns": 2, "inode": 3}]}
        self.assertFalse(guest._disk_differs(g))
        g["disks"][0]["mtime_ns"] = 5
        self.assertTrue(guest._disk_differs(g))
        g["stat"]["generation"] = "other"
        self.assertFalse(guest._disk_differs(g))

    def test_image_sums_for_guests_without_state(self):
        io = FakeIO()
        rs = RS(Word(), hosts=("chopin",))
        ctx = Ctx(rs)
        ctx.facts = {"chopin": {"guests": {"manjaro": {"generation": None, "imgsum": None, "disks": [{"path": "/w/manjaro.qcow2"}]}}}}
        ctx.options = {"universe": ["guest:manjaro"]}

        def sh(host, cmd, timeout=60, input=None):
            return 0, json.dumps({"/w/manjaro.qcow2": "ab" * 32}) + "\n", ""
        io.sh = sh
        guest.pre_plan(ctx, Word(), lambda x: None, io=io)
        self.assertTrue(ctx.facts["chopin"]["guests"]["manjaro"]["imgsum"])


class Sweep(unittest.TestCase):
    def setUp(self):
        self.t = tmpdir()
        self.work = os.path.join(self.t, "w")
        self.ram = os.path.join(self.t, "ram")
        self.hroot = os.path.join(self.t, "h")
        self.vms = os.path.join(self.work, "vms", "g1")
        os.makedirs(self.vms)
        os.makedirs(self.ram)
        self.tag = tool_guest.realhash8(os.path.join(self.work, "vms"))
        self.d = os.path.join(self.ram, f"vm-update.{self.tag}.g1")
        p = mock.patch.object(tool_guest, "qemu_holds", lambda path: False)   # (macOS has no OFD locks: it would answer "can't tell")
        p.start()
        self.addCleanup(p.stop)

    def rundir(self, owner=None):
        rd = os.path.join(self.d, ".vm-update-run.abc")
        os.makedirs(rd)
        with open(os.path.join(rd, "o.qcow2"), "w", encoding="utf-8") as f:
            f.write("x")
        if owner:
            with open(os.path.join(rd, "owner.json"), "w", encoding="utf-8") as f:
                json.dump(owner, f)
        return rd

    def sweep(self, **k):
        return tool_guest.sweep(self.work, self.hroot, ram=self.ram, say=lambda x: None, **k)

    def test_a_dead_run_folder_goes(self):
        rd = self.rundir({"run": "r", "vm_update_pid": 999999, "vm_run_pid": 999998})
        with mock.patch.object(tool_guest, "pid_cmdline", lambda p: None):
            r = self.sweep()
        self.assertIn(rd, r["removed"])
        self.assertFalse(os.path.exists(rd))

    def test_a_live_vm_update_stays(self):
        rd = self.rundir({"vm_update_pid": 123})
        with mock.patch.object(tool_guest, "pid_cmdline", lambda p: "python3 /x/testvm/vm-update --try g1"):
            r = self.sweep()
        self.assertTrue(os.path.exists(rd))
        self.assertTrue(r["kept"])

    def test_a_held_guest_lock_keeps_it(self):
        rd = self.rundir({"vm_update_pid": 123})
        import fcntl
        lk = open(os.path.join(self.vms, ".vm-update.lock"), "a")
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            with mock.patch.object(tool_guest, "pid_cmdline", lambda p: None):
                r = self.sweep()
        finally:
            lk.close()
        self.assertTrue(os.path.exists(rd))
        self.assertTrue(r["kept"])

    def real_argv(self):   # vm-update's: setpriv execs vm-run, a bash script; the folder is in its RAM_DIR
        return "/bin/bash /x/testvm/vm-run --ram --keep --discard g1.qcow2"

    def test_a_vm_run_of_a_dead_run_gets_term_by_its_ram_dir(self):
        rd = self.rundir({"vm_update_pid": 1, "vm_run_pid": 4242})
        killed = []

        def cmdline(p):
            return None if p == 1 or killed else self.real_argv()
        with mock.patch.object(tool_guest, "pid_cmdline", cmdline), mock.patch.object(tool_guest, "pid_environ", lambda p, n: rd),\
                mock.patch.object(tool_guest.os, "kill", lambda p, s: killed.append(p)):
            self.sweep()
        self.assertEqual(killed, [4242])
        self.assertFalse(os.path.exists(rd))

    def test_a_vm_run_naming_it_in_its_argv_gets_term_too(self):
        rd = self.rundir({"vm_update_pid": 1, "vm_run_pid": 4242})
        killed = []

        def cmdline(p):
            return None if p == 1 or killed else f"vm-run -monitor unix:{rd}/mon"
        with mock.patch.object(tool_guest, "pid_cmdline", cmdline), mock.patch.object(tool_guest, "pid_environ", lambda p, n: None),\
                mock.patch.object(tool_guest.os, "kill", lambda p, s: killed.append(p)):
            self.sweep()
        self.assertEqual(killed, [4242])

    def test_another_runs_ram_dir_gets_nothing(self):
        self.rundir({"vm_update_pid": 1, "vm_run_pid": 4242})
        killed = []
        with mock.patch.object(tool_guest, "pid_cmdline", lambda p: None if p == 1 else self.real_argv()), \
                mock.patch.object(tool_guest, "pid_environ", lambda p, n: "/mnt/bko-ram/vm-update.other/.vm-update-run.zzz"), \
                mock.patch.object(tool_guest.os, "kill", lambda p, s: killed.append(p)):
            self.sweep()
        self.assertEqual(killed, [])

    def test_a_reused_pid_gets_nothing(self):
        rd = self.rundir({"vm_update_pid": 1, "vm_run_pid": 4242})
        killed = []
        with mock.patch.object(tool_guest, "pid_cmdline", lambda p: None if p == 1 else "something else"), \
                mock.patch.object(tool_guest.os, "kill", lambda p, s: killed.append(p)):
            self.sweep()
        self.assertEqual(killed, [])

    def test_a_qemu_lock_keeps_it(self):
        rd = self.rundir()
        with mock.patch.object(tool_guest, "qemu_holds", lambda p: True):
            r = self.sweep()
        self.assertTrue(os.path.exists(rd))
        self.assertTrue(r["kept"])

    def test_orphan_shipped_overlay_goes_pending_stays(self):
        os.makedirs(self.d)
        ov = os.path.join(self.d, ".vm-update.g1.qcow2")
        with open(ov, "w", encoding="utf-8") as f:
            f.write("x")
        p = os.path.join(self.vms, ".vm-update.pending")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{}")
        r = self.sweep()
        self.assertTrue(os.path.exists(ov))
        self.assertEqual(r["pending"], [ov])
        os.remove(p)
        r = self.sweep()                                  # just shipped: the sender may not have written pending yet
        self.assertTrue(os.path.exists(ov))
        self.assertTrue(r["kept"])
        r = self.sweep(now=time.time() + tool_guest.SHIPPED_MIN_AGE_S + 60)
        self.assertFalse(os.path.exists(ov))

    def test_qemu_holds_says_yes_when_it_cant_tell(self):
        mock.patch.stopall()
        self.assertTrue(tool_guest.qemu_holds(os.path.join(self.t, "not-there")))

    def probe_dir(self, pid, work=None):
        d = os.path.join(self.ram, "t2-guest-probe.abc123")
        os.makedirs(d)
        with open(os.path.join(d, "owner.json"), "w", encoding="utf-8") as f:
            json.dump({"pid": pid, "work": work or self.work}, f)
        return d

    def test_a_dead_probes_folder_goes(self):
        d = self.probe_dir(7)
        with mock.patch.object(tool_guest, "pid_cmdline", lambda p: None):
            r = self.sweep()
        self.assertFalse(os.path.exists(d))
        self.assertIn(d, r["removed"])

    def test_a_live_probes_folder_stays(self):
        d = self.probe_dir(7)
        with mock.patch.object(tool_guest, "pid_cmdline", lambda p: "bash /x/testvm/test2/guest/probe-redox.sh redox /o"):
            self.sweep()
        self.assertTrue(os.path.exists(d))

    def test_another_worlds_probe_folder_stays(self):
        d = self.probe_dir(7, work="/somewhere/else")
        with mock.patch.object(tool_guest, "pid_cmdline", lambda p: None):
            self.sweep()
        self.assertTrue(os.path.exists(d))

    def test_another_worlds_folders_are_left(self):
        other = os.path.join(self.ram, "vm-update.g1")
        os.makedirs(other)
        self.sweep()
        self.assertTrue(os.path.exists(other))

    def test_old_failed_records_go(self):
        p = os.path.join(self.hroot, "failed", "t2v-x", "g1")
        os.makedirs(p)
        os.utime(p, (time.time() - 20 * 86400, time.time() - 20 * 86400))
        q = os.path.join(self.hroot, "failed", "t2v-y", "g1")
        os.makedirs(q)
        self.sweep()
        self.assertFalse(os.path.exists(p))
        self.assertTrue(os.path.exists(q))


class FailedState(unittest.TestCase):
    def setUp(self):
        self.t = tmpdir()
        self.out = os.path.join(self.t, "out")
        os.makedirs(self.out)
        self.hroot = os.path.join(self.t, "h")
        self.ram = os.path.join(self.t, ".vm-update-run.q")
        os.makedirs(self.ram)
        with open(os.path.join(self.ram, "o.qcow2"), "wb") as f:
            f.write(b"x" * 4096)
        with open(os.path.join(self.out, "update.log"), "w", encoding="utf-8") as f:
            f.write("log\n")

    def res(self, tail=()):
        with open(os.path.join(self.out, "result.json"), "w", encoding="utf-8") as f:
            json.dump({"overlay_dir": self.ram, "tail": list(tail)}, f)

    def go(self, **k):
        k.setdefault("cap_mib", 1024)
        return tool_guest.failed_state("g1", "t2v-x", 1, self.out, self.hroot, say=lambda x: None, **k)

    def dest(self, *p):
        return os.path.join(self.hroot, "failed", "t2v-x", "g1", *p)

    def test_under_the_cap_keeps_the_overlay(self):
        self.res()
        a = self.go()
        self.assertTrue(os.path.exists(self.dest("overlay-a1", "o.qcow2")))
        self.assertTrue(os.path.exists(self.dest("a1-update.log")))
        self.assertFalse(os.path.exists(self.ram))
        self.assertTrue(a["overlay"])

    def test_over_the_cap_only_a_note(self):
        self.res()
        a = self.go(cap_mib=0)
        self.assertFalse(os.path.exists(self.dest("overlay-a1")))
        self.assertIn("cap", a["note"])
        self.assertFalse(os.path.exists(self.ram))

    def test_a_failed_copy_still_gives_the_ram_back(self):
        self.res()
        with mock.patch.object(tool_guest.shutil, "copytree", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(OSError):
                self.go()
        self.assertFalse(os.path.exists(self.ram))

    def test_an_unwritable_host_root_still_gives_the_ram_back(self):
        """makedirs of failed/RUN/G raising (a full or read-only /bkoscratch, a bad T2_HOST_ROOT)."""
        self.res()
        os.makedirs(self.hroot)
        os.chmod(self.hroot, 0o555)
        self.addCleanup(os.chmod, self.hroot, 0o755)
        with self.assertRaises(OSError):
            self.go()
        self.assertFalse(os.path.exists(self.ram))

    def test_a_bad_flake_list_still_gives_the_ram_back(self):
        self.res(["x"])
        with self.assertRaises(Exception):
            self.go(flakes=["not an entry"])
        self.assertFalse(os.path.exists(self.ram))

    def test_the_cli_frees_the_ram_when_the_policy_cant_be_read(self):
        self.res()
        from t2lib import rows_guest
        ctx = mock.Mock(base=REPO, host_root=self.hroot, work=self.t)
        with mock.patch.object(rows_guest, "policy_of", side_effect=ValueError("bad policy")), \
                contextlib.redirect_stderr(io_mod.StringIO()) as err, contextlib.redirect_stdout(io_mod.StringIO()):
            rc = tool_guest.main(["failed-state", "--guest", "g1", "--run", "t2v-x", "--attempt", "1", "--out", self.out], ctx)
        self.assertEqual(rc, 0)
        self.assertIn("couldn't be read", err.getvalue())
        self.assertFalse(os.path.exists(self.ram))

    def test_a_flake_keeps_logs_only(self):
        self.res(["E: Failed to fetch http://x"])
        a = self.go(flakes=[{"guest": "*", "pattern": "E: Failed to fetch"}])
        self.assertTrue(a["flake"])
        self.assertFalse(os.path.exists(self.dest("overlay-a1")))
        self.assertTrue(os.path.exists(self.dest("a1-update.log")))
        self.assertFalse(os.path.exists(self.ram))

    def test_a_flake_earlier_in_the_log_doesnt_count(self):
        self.res(["the update failed (exit status 1)"])
        with open(os.path.join(self.out, "update.log"), "a", encoding="utf-8") as f:
            f.write("E: Failed to fetch http://x\n")
        a = self.go(flakes=[{"guest": "*", "pattern": "E: Failed to fetch"}])
        self.assertFalse(a["flake"])

    def test_known_flake_pass_deletes_the_overlay(self):
        self.res()
        self.go()
        with open(os.path.join(self.out, "row-summary.json"), "w", encoding="utf-8") as f:
            json.dump({"class": "known-flake"}, f)
        tool_guest.failed_state("g1", "t2v-x", 1, self.out, self.hroot, after_row=True, say=lambda x: None)
        self.assertFalse(os.path.exists(self.dest("overlay-a1")))
        self.assertTrue(os.path.exists(self.dest("a1-update.log")))

    def test_an_ordinary_pass_leaves_it(self):
        self.res()
        self.go()
        with open(os.path.join(self.out, "row-summary.json"), "w", encoding="utf-8") as f:
            json.dump({"class": "pass"}, f)
        tool_guest.failed_state("g1", "t2v-x", 1, self.out, self.hroot, after_row=True, say=lambda x: None)
        self.assertTrue(os.path.exists(self.dest("overlay-a1")))


class Gate(unittest.TestCase):
    def setUp(self):
        self.out = tmpdir()
        for n in ("before", "after"):
            shutil.copy(os.path.join(FX, f"hook-{n}.out"), self.out)

    def go(self):
        return tool_guest.gate("debian12", self.out, REPO, say=lambda x: None)

    def test_clean_merges(self):
        self.assertEqual(self.go(), 0)

    def test_new_line_blocks(self):
        p = os.path.join(self.out, "hook-after.out")
        with open(p, encoding="utf-8") as f:
            t = f.read().replace("state running", "state degraded")
        with open(p, "w", encoding="utf-8") as f:
            f.write(t)
        self.assertEqual(self.go(), 10)

    def test_cut_off_hook_blocks(self):
        p = os.path.join(self.out, "hook-after.out")
        with open(p, encoding="utf-8") as f:
            t = f.read().replace("=== end\n", "")
        with open(p, "w", encoding="utf-8") as f:
            f.write(t)
        self.assertEqual(self.go(), 10)


class PkgDiff(unittest.TestCase):
    def test_diff(self):
        h = tmpdir()
        d = os.path.join(h, "guest-dumps", "g")
        os.makedirs(d)
        for g, t in (("A", "pkg a 1\npkg b 1\n"), ("B", "pkg a 2\npkg c 1\n")):
            with open(os.path.join(d, f"{g}.pkgset"), "w", encoding="utf-8") as f:
                f.write(t)
        self.assertEqual(tool_guest.pkgdiff(h, "g", "A", "B"), {"added": ["pkg c"], "removed": ["pkg b"], "changed": ["pkg a"]})
        self.assertIsNone(tool_guest.pkgdiff(h, "g", "A", "C"))


class Accept(unittest.TestCase):
    def row(self, needs, **kw):
        return dict({"id": "chopin-20261110T000000Z-00000001", "c": "x", "t": "t", "host": "chopin", "cell": "guest:g1", "kind": "run", "run": "R", "attempt": 1,
                     "key": "k" * 64, "groups": {}, "inputs": {}, "needs": needs, "family": "guest", "origin": "o", "word": "w", "valid": True,
                     "extra": {"to_class_only": True}, "commit": None, "committed": False, "copy": "c", "verdict": "FAIL", "class": "fail"}, **kw)

    def setUp(self):
        self.mac = tmpdir()
        self.lines = {"builder": "b", "lines": [{"line": "new N", "source": "update", "seen_before": False}]}

    def go(self, needs):
        with mock.patch.object(guest, "class_row", lambda r, m=None: dict(r, kind="class", via=r["id"], needs=dict(r["needs"], logs="PASS"))):
            return guest.accept([self.row(needs)], "R", "g1", self.lines, self.mac)

    def test_logs_the_only_unmet_need(self):
        ok, r = self.go({"try": "PASS", "logs": "DIFF", "after:x": "LIMIT", "image:x": "SKIP"})
        self.assertTrue(ok)
        self.assertEqual(r["needs"]["logs"], "PASS")
        self.assertEqual(r["kind"], "class")
        with open(os.path.join(self.mac, "guest-baselines", "g1.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["lines"], ["new N"])

    def test_refused_with_another_unmet_need(self):
        ok, why = self.go({"try": "FAIL", "logs": "DIFF"})
        self.assertFalse(ok)
        self.assertIn("try", why)
        self.assertFalse(os.path.exists(os.path.join(self.mac, "guest-baselines", "g1.json")))

    def test_refused_with_a_limit_new(self):
        ok, why = self.go({"after:x": "LIMIT-NEW", "logs": "DIFF"})
        self.assertFalse(ok)

    def test_refused_when_logs_is_met(self):
        ok, why = self.go({"try": "PASS", "logs": "PASS"})
        self.assertFalse(ok)

    def test_refused_on_a_harness_row(self):
        with mock.patch.object(guest, "class_row", lambda r, m=None: r):
            ok, why = guest.accept([self.row({"identity": "PASS", "logs": "DIFF"}, verdict="HARNESS", **{"class": "harness"})], "R", "g1", self.lines, self.mac)
        self.assertFalse(ok)
        self.assertIn("HARNESS", why)
        self.assertFalse(os.path.exists(os.path.join(self.mac, "guest-baselines", "g1.json")))

    def test_refused_on_an_invalid_row(self):
        ok, why = guest.accept([self.row({"try": "PASS", "logs": "DIFF"}, valid=False)], "R", "g1", self.lines, self.mac)
        self.assertFalse(ok)
        self.assertIn("invalid", why)

    def test_refused_unless_to_class_only(self):
        ok, why = guest.accept([self.row({"try": "PASS", "logs": "DIFF"}, extra={"to_class_only": False})], "R", "g1", self.lines, self.mac)
        self.assertFalse(ok)

    def test_the_class_row_is_valid(self):
        from t2lib import ledger, keys
        r = self.row({"try": "PASS", "logs": "DIFF"}, inputs={"harness": {"x": "sha256:" + "a" * 64}}, reasons=["x"], rule="r", start="2026-11-10T00:00:00Z",
                     planned_key=None, fails=["logs"], gm=0.0, wall_s=0, waits_s=0, wear={}, ram_peak_kib=None, seed=None, dirty=None, logs=None, meta={}, spec=None,
                     counts={}, v=1, note=None, via=None)
        r["key"] = keys.key("guest:g1", r["inputs"])
        r["groups"] = keys.groups(r["inputs"])
        c = guest.class_row(r)
        self.assertEqual(ledger.validate(c), [])
        self.assertEqual((c["kind"], c["verdict"], c["via"], c["rule"]), ("class", "PASS", r["id"], "guest:lines-classed"))

    def test_accept_all_lists_what_it_skipped(self):
        d = runmod.run_dir("t2v-20261110T0000-abcd", os.environ["T2_MAC_ROOT"])
        os.makedirs(os.path.join(d, "guest-lines"), exist_ok=True)
        for g in ("g1", "g2"):
            with open(os.path.join(d, "guest-lines", f"{g}.json"), "w", encoding="utf-8") as f:
                json.dump(self.lines, f)
        rows = [self.row({"logs": "DIFF", "try": "FAIL"}, run="t2v-20261110T0000-abcd", cell="guest:g1"),
                self.row({"logs": "PASS"}, run="t2v-20261110T0000-abcd", cell="guest:g2")]
        said = []
        with mock.patch.object(guest, "_rows", lambda: rows):
            rc = guest.cmd_lines(["t2v-20261110T0000-abcd", "--accept-all"], out=said.append)
        self.assertEqual(rc, 0)
        self.assertEqual(sum(x.startswith("skipped:") for x in said), 2)

    def test_accept_names_a_refusal_with_exit_2(self):
        d = runmod.run_dir("t2v-20261110T0000-abce", os.environ["T2_MAC_ROOT"])
        os.makedirs(os.path.join(d, "guest-lines"), exist_ok=True)
        with open(os.path.join(d, "guest-lines", "g1.json"), "w", encoding="utf-8") as f:
            json.dump(self.lines, f)
        rows = [self.row({"logs": "DIFF", "try": "FAIL"}, run="t2v-20261110T0000-abce")]
        with mock.patch.object(guest, "_rows", lambda: rows):
            self.assertEqual(guest.cmd_lines(["t2v-20261110T0000-abce", "--accept", "g1"], out=lambda x: None), 2)


class HostJobEnv(unittest.TestCase):
    def test_env_paths(self):
        rs = RS(Word())
        e = hostjob.Env(rs, "/h", io=FakeIO())
        self.assertEqual(e.attempt_run(1), rs.run)
        self.assertEqual(e.attempt_run(3), f"{rs.run}-r3")
        self.assertEqual(e.copy_path("chopin"), "/copy/chopin")
        self.assertEqual(hostjob.job_dir(e, "g1", 2, "guest"), f"/h/runs/{rs.run}-r2/guest/g1")

    def test_host_checks_feed_the_stops(self):
        rs = RS(Word(), hosts=("chopin",))

        class IO(FakeIO):
            n = 0

            def sh(self, host, cmd, timeout=60, input=None):
                if "pswpout" in cmd:
                    IO.n += 1
                    return 0, f"{IO.n * 10}\n", ""
                if "python3" in cmd:
                    return 0, "1234\n", ""
                return 0, "", ""
        e = hostjob.Env(rs, "/h", io=IO())
        hostjob.host_checks(e, rs, "guest:RUN", 1000)
        hostjob.host_checks(e, rs, "guest:RUN", 1000)
        self.assertEqual(rs.stops.hosted, [("chopin", 0, 1234), ("chopin", 40960, 1234)])

    def test_tripped(self):
        self.assertFalse(hostjob.tripped(None))
        rs = RS(Word())
        self.assertFalse(hostjob.tripped(rs))
        rs.guards = mock.Mock(tripped=lambda: True)
        self.assertTrue(hostjob.tripped(rs))

    def test_on_began_fires_once_at_began(self):
        class IO(FakeIO):
            polls = 0

            def sh(self, host, cmd, timeout=60, input=None):
                if "cat .exit" in cmd:
                    IO.polls += 1
                    return (0, "\n100\n", "") if IO.polls == 1 else (0, "0\n100\n", "")
                return 0, "", ""

            def fetch(self, *a):
                return {}
        rs = RS(Word())
        e = hostjob.Env(rs, "/h", io=IO())
        seen = []
        rc, out, t, why = hostjob.host_job(e, "chopin", "g1", 1, "true", (1, 1), "tag", 60, 60, {"A": "1"}, None, "guest", on_began=lambda: seen.append(1))
        self.assertEqual((rc, why, out), (0, None, f"/h/runs/{rs.run}/guest/g1"))
        self.assertEqual(seen, [1])


class GateGuards(unittest.TestCase):
    def test_gate_plan_reads_gate_fresh(self):
        from t2lib import gate
        ctx = mock.Mock()
        ctx.options = {"gate_fresh": True, "universe": ["x"]}
        ctx.run = mock.Mock(plan=mock.Mock(set={}))
        seen = {}

        def make(w, c):
            seen["w"], seen["c"] = w, c
            return "plan"
        with mock.patch("t2lib.plan.make", make):
            self.assertEqual(gate.gate_plan(ctx), "plan")
        self.assertTrue(seen["w"].all and seen["w"].sweep)
        self.assertNotIn("universe", seen["c"].options)


class Reattach(unittest.TestCase):
    """hostjob.reattach_job against a scripted host."""

    class IO:
        def __init__(self, answers, alive=True):
            self.answers, self.alive_now, self.stopped = list(answers), alive, []

        def sh(self, host, cmd, timeout=60, input=None):
            a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
            return a if isinstance(a, tuple) else (0, a, "")

        def alive(self, host, pid, needle=None):
            return self.alive_now

        def stop(self, host, pid, needle):
            self.stopped.append(pid)
            return True

        def fetch(self, host, d, names):
            return {".exit": "0\n", ".began": "5\n"}

        def sleep(self, s):
            pass

    def env(self, io):
        return mock.Mock(host_root="/h", attempt_run=lambda a: "R", io=io, log=lambda t: None)

    def test_it_ended(self):
        began = []
        io = self.IO([".exit=0\n\n.began=5\n\n.pid=9\n\n"])
        rc, out, t, why = hostjob.reattach_job(self.env(io), "chopin", "arch", 1, subdir="guest", on_began=lambda: began.append(1))
        self.assertEqual((rc, why, t["exit"]), (0, None, 0))
        self.assertEqual(out, "/h/runs/R/guest/arch")
        self.assertEqual(began, [1])

    def test_it_runs_then_ends(self):
        io = self.IO([".exit=\n.began=5\n\n.pid=9\n\n", ".exit=3\n\n.began=5\n\n.pid=9\n\n"])
        rc, _, _, why = hostjob.reattach_job(self.env(io), "chopin", "arch", 1, subdir="guest")
        self.assertEqual((rc, why), (3, None))

    def test_gone_with_no_exit(self):
        io = self.IO([".exit=\n.began=5\n\n.pid=9\n\n"], alive=False)
        rc, _, _, why = hostjob.reattach_job(self.env(io), "chopin", "arch", 1, subdir="guest")
        self.assertIsNone(rc)
        self.assertTrue(why.gone)

    def test_no_directory_never_started(self):
        rc, _, _, why = hostjob.reattach_job(self.env(self.IO(["nodir\n"])), "chopin", "arch", 1, subdir="guest")
        self.assertTrue(why.gone)

    def test_a_host_that_doesnt_answer_isnt_an_end(self):
        rc, _, _, why = hostjob.reattach_job(self.env(self.IO([(255, "", "")])), "chopin", "arch", 1, subdir="guest")
        self.assertIsNone(rc)
        self.assertFalse(why.gone)
        self.assertTrue(why.still_running)


class RunnerBlocks(unittest.TestCase):
    """What the runner reports: grades, the update block, the lines it fetches."""

    def rows(self):
        def row(cell, v="PASS", needs=None):
            return {"cell": cell, "verdict": v, "valid": True, "kind": "run", "t": "2026-11-09T00:00:00Z", "id": cell, "key": "a" * 64, "needs": needs or {"try": "PASS", "logs": "PASS"},
                    "extra": {}, "inputs": {"guest": {}}}
        return [row("guest:debian12"), row("stic:debian12"), row("holt:debian12")]

    def block(self, stic, holt):
        r, rs, ctx, io = make(["debian12"], hosts=("chopin",))
        with mock.patch.object(r, "other_statuses", lambda: (stic, holt)):
            return r.grades_block(self.rows())

    def test_solid_needs_stic_and_holt_current(self):
        self.assertRegex(self.block({"debian12": "current"}, {"debian12": "current"}), r"debian12\s+solid\s+stic PASS 2026-11-09\s+holt PASS 2026-11-09")

    def test_backup_not_current_is_usable(self):
        self.assertRegex(self.block({"debian12": "due"}, {"debian12": "current"}), r"debian12\s+usable")

    def test_grades_ask_for_facts_of_stic_and_holt_and_the_round_set(self):
        r, rs, ctx, io = make(["debian12"], hosts=("chopin",))
        wants = []

        def facts(hosts, want):
            wants.append(dict(want))
            return {"chopin": {"ok": True, "rounds": {"rel9": {"x": 1}}, "sets": {"bko-bin": {}}, "guests": {}}}
        io.facts = facts
        seen = []

        class P:
            def __init__(self, w, c):
                pass

            def fork_heads(self):
                return {"restic": "r"}

        def make_plan(w, c):
            seen.append((w.family, c.facts["chopin"]["sets"]))
            return Plan([PC(f"{w.family}:debian12", status="current")])
        with mock.patch("t2lib.plan.Planner", P), mock.patch("t2lib.plan.round_commits", lambda e: {"restic": "r"}), mock.patch("t2lib.plan.make", make_plan):
            st, ho = r.other_statuses()
        self.assertEqual((st, ho), ({"debian12": "current"}, {"debian12": "current"}))
        self.assertIn("roundrel9", wants[-1]["sets"])
        self.assertIn("bko-bin", wants[0]["sets"])
        self.assertEqual(r.notes, [])

    def test_every_cell_not_runnable_gets_a_note(self):
        r, rs, ctx, io = make(["debian12"], hosts=("chopin",))
        with mock.patch("t2lib.plan.make", lambda w, c: Plan([PC(f"{w.family}:debian12", status="not-runnable")])):
            r.other_statuses()
        self.assertTrue(any("not-runnable" in n for n in r.notes))

    def test_take_lines(self):
        io, jobs = FakeIO(), FakeJobs()
        real = io.fetch

        def fetch(host, d, names):
            out = real(host, d, names)
            if "new-lines.json" in names:
                out["new-lines.json"] = json.dumps({"builder": "b", "lines": [{"line": "x N", "source": "update", "seen_before": True}]})
            return out
        io.fetch, io.summ["debian12"] = fetch, summary()
        r, rs, ctx, _ = make(["debian12"], io, jobs, hosts=("chopin",))
        r.run()
        with open(os.path.join(rs.mac_dir, "guest-lines", "debian12.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "[seen before] update: x N\n")

    def test_update_block_says_what_changed(self):
        r, rs, ctx, io = make(["debian12"], word=Word(update=True), hosts=("chopin",))
        io.script = lambda h, s, d, timeout=120: {"rc": 0, "out": json.dumps({"added": ["a"], "removed": [], "changed": ["linux-image"]})}
        r.cells.append({"cell": "guest:debian12", "merged": True, "host": "chopin", "steps": ["s"], "fingerprint": "f", "generation_before": "g1", "generation_after": "g2"})
        t = r.update_block()
        self.assertIn("merged", t)
        self.assertIn("changed 1, added 1, removed 0: linux-image, a", t)

    def test_skips_block_groups_by_reason(self):
        r, rs, ctx, io = make(["a", "b"], hosts=("chopin",))
        r.cells += [{"cell": "guest:a", "skips": {"image:sources": "not updated"}, "need_notes": {"image:free": "/ 85% full", "image:kernels": "2 kernels"}},
                    {"cell": "guest:b", "skips": {"image:sources": "not updated"}}]
        t = r.skips_block()
        self.assertIn("image:sources SKIP: not updated: a, b", t)
        self.assertIn("image:free: / 85% full: a", t)
        self.assertNotIn("kernels", t)

    def test_tree_matches(self):
        r, rs, ctx, io = make(["debian12"], word=Word(update=True), hosts=("chopin",))
        from t2lib import copies
        with mock.patch.object(copies, "tree_hashes", lambda c, p: {"a": "1"}):
            io.sh = lambda h, c, timeout=60, input=None: (0, json.dumps({"files": {"a": "1"}}), "")
            self.assertIsNone(r.tree_matches("chopin"))
            io.sh = lambda h, c, timeout=60, input=None: (0, json.dumps({"files": {"a": "2"}}), "")
            self.assertIn("differs", r.tree_matches("chopin"))

    def test_tree_matches_covers_what_the_merge_gate_runs(self):
        """The gate (t2-host tool guest gate: tool_guest, guest_judge, rows_guest, keys) runs from the synced tree."""
        r, rs, ctx, io = make(["debian12"], word=Word(update=True), hosts=("chopin",))
        from t2lib import copies
        asked = {}
        io.sh = lambda h, c, timeout=60, input=None: asked.setdefault("cmd", c) and (0, json.dumps({"files": {}}), "")
        with mock.patch.object(copies, "tree_hashes", lambda c, p: asked.setdefault("paths", list(p)) and {}):
            r.tree_matches("chopin")
        for p in ("testvm/vm-update", "testvm/update", "testvm/test2/guest", "testvm/test2/t2-host", "testvm/test2/t2lib",
                  "testvm/test2/classes.json"):
            self.assertIn(p, asked["paths"])
            self.assertIn(p, asked["cmd"])
        # and a real tree's hashes do hold the judge's files
        h = copies.tree_hashes(REPO, list(guest.TREE_PATHS))
        for f in ("testvm/test2/t2lib/guest_judge.py", "testvm/test2/t2lib/tool_guest.py", "testvm/test2/t2lib/rows_guest.py",
                  "testvm/test2/t2lib/keys.py", "testvm/test2/t2-host", "testvm/test2/guest/limits.json", "testvm/test2/guest/job.sh"):
            self.assertIn(f, h)
        self.assertFalse([k for k in h if "__pycache__" in k or k.endswith(".pyc")])

    def test_fingerprint_rules_with_a_file_missing(self):
        self.assertIsNone(guest.fingerprint_rules(tmpdir()))

    def test_fleet_lock_takes_and_lets_go(self):
        class P:
            def __init__(self):
                self.stdin, self.stdout, self.waited = mock.Mock(), mock.Mock(), False
                self.stdout.readline = lambda: b"held\n"

            def wait(self, timeout=None):
                self.waited = True

        p = P()
        with mock.patch.object(guest.subprocess, "Popen", lambda *a, **k: p):
            f = guest.FleetLock("chopin", "/w", "R", beat_s=0.01)
            self.assertTrue(f.take())
            time.sleep(0.05)
            f.release()
        p.stdin.close.assert_called()
        self.assertTrue(p.waited)
        self.assertTrue(p.stdin.write.called)

    def test_fleet_lock_sees_its_holder_go(self):
        class P:
            """After "held": nothing (the ssh ended), or a quiet line now and then (alive)."""
            def __init__(self, alive):
                self.stdin, self.stdout, self.rc, self.said = mock.Mock(), mock.Mock(), None, False
                self.stdout.readline = lambda: self.line(alive)

            def line(self, alive):
                if not self.said:
                    self.said = True
                    return b"held\n"
                if alive:
                    time.sleep(0.05)
                    return b"\n"
                return b""

            def poll(self):
                return self.rc

            def wait(self, timeout=None):
                pass

        def lost_soon(f):
            for _ in range(200):
                if f.lost():
                    break
                time.sleep(0.01)
            return f.lost()

        p = P(alive=False)
        with mock.patch.object(guest.subprocess, "Popen", lambda *a, **k: p):
            f = guest.FleetLock("chopin", "/w", "R", beat_s=30)
            self.assertTrue(f.take())
            self.assertEqual(lost_soon(f), "its ssh ended")
            f.release()
        p = P(alive=True)
        p.stdin.write.side_effect = BrokenPipeError()
        with mock.patch.object(guest.subprocess, "Popen", lambda *a, **k: p):
            f = guest.FleetLock("chopin", "/w", "R", beat_s=0.01)
            self.assertTrue(f.take())
            self.assertIn("heartbeat", lost_soon(f))
            f.release()
        p = P(alive=True)
        with mock.patch.object(guest.subprocess, "Popen", lambda *a, **k: p):
            f = guest.FleetLock("chopin", "/w", "R", beat_s=30)
            self.assertTrue(f.take())
            self.assertIsNone(f.lost())
            p.rc = 75
            self.assertEqual(f.lost(), "it exited (75)")
            f.release()

    def test_a_released_fleet_lock_isnt_lost(self):
        class P:
            def __init__(self):
                self.stdin, self.stdout = mock.Mock(), mock.Mock()
                self.lines = [b"held\n"]
                self.stdout.readline = lambda: self.lines.pop(0) if self.lines else (time.sleep(0.02) or b"")

            def poll(self):
                return 0

            def wait(self, timeout=None):
                pass
        p = P()
        with mock.patch.object(guest.subprocess, "Popen", lambda *a, **k: p):
            f = guest.FleetLock("chopin", "/w", "R", beat_s=30)
            self.assertTrue(f.take())
            f.release()
            time.sleep(0.1)
            self.assertIsNone(f.lost())

    def test_fleet_lock_refused(self):
        class P:
            def __init__(self):
                self.stdin, self.stdout = mock.Mock(), mock.Mock()
                self.stdout.readline = lambda: b""

            def wait(self, timeout=None):
                pass
        with mock.patch.object(guest.subprocess, "Popen", lambda *a, **k: P()):
            self.assertFalse(guest.FleetLock("chopin", "/w", "R").take(timeout=2))

    def test_harness_row_and_append_row(self):
        from t2lib import copies, ledger
        r, rs, ctx, io = make(["debian12"], hosts=("chopin",))
        ctx.facts = {"chopin": {"guests": {}}}
        pv = mock.Mock(commit="c" * 40, dirty={}, id="cp")
        with mock.patch.object(copies, "preview", lambda c: pv):
            row = guest.harness_row(ctx, "debian12", "chopin", rs.run, 1, "why", "fresh:names")
        self.assertEqual((row["verdict"], row["class"], row["cell"], row["origin"]), ("HARNESS", "harness", "guest:debian12", f"guest:{rs.run}:debian12:1"))
        with mock.patch.object(ledger, "complete", lambda rw, host=None: dict(rw, host=host)):
            rid = guest.append_row(ctx, io, "chopin", row)
        self.assertEqual(rid, "chopin-20261110T000000Z-00000001")
        self.assertEqual(io.rows[-1]["cell"], "guest:debian12")

    def test_cmd_fingerprints_probes_guests_with_no_sidecar(self):
        from t2lib import facts as factsmod
        calls = []

        class IO:
            def sh(self, host, cmd, timeout=60, input=None):
                calls.append(cmd)
                return 0, "", ""

            def script(self, host, script, data, timeout=120):
                return {"rc": 0, "out": ""}
        gs = {"debian12": {"generation": "g"}, "arch": {"generation": "g", "fingerprint": {"holt": "h"}}, "redox": {}}
        with mock.patch.object(factsmod, "probe", lambda h, w, save=True: {"ok": True, "guests": gs}):
            rc = guest.cmd_fingerprints([], out=lambda x: None, io=IO())
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("--probe --write-fingerprint", calls[0])
        self.assertIn("WEAR_TAG=guest:", calls[0])
        self.assertIn("debian12", calls[0])

    def test_a_worlds_other_host_path_reaches_every_vm_update(self):
        # (vm-update refuses a world with no VM_UPDATE_REMOTE_VMS: the other host's default is the real fleet)
        from t2lib import facts as factsmod
        calls = []

        class IO:
            def sh(self, host, cmd, timeout=60, input=None):
                calls.append(cmd)
                return 0, "", ""

            def script(self, host, script, data, timeout=120):
                return {"rc": 0, "out": ""}
        gs = {"debian12": {"generation": "g"}, "arch": {"generation": "g", "fingerprint": {"holt": "h", "rules": "old"}}}
        env = {"VM_UPDATE_REMOTE_VMS": "backotter-work/t2rest-x/world/vms", "T2_WORK": "/w"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(factsmod, "probe", lambda h, w, save=True: {"ok": True, "guests": gs}):
            guest.cmd_fingerprints([], out=lambda x: None, io=IO())
            guest.cmd_fingerprints(["--reproject"], out=lambda x: None, io=IO())
            seen = []

            class P:
                stdin = stdout = mock.Mock()

                def wait(self, timeout=None):
                    pass
            P.stdout.readline = lambda: b""
            with mock.patch.object(guest.subprocess, "Popen", lambda argv, **k: seen.append(argv[-1]) or P()):
                guest.FleetLock("chopin", "/w", "R").take(timeout=1)
        self.assertIn("VM_UPDATE_REMOTE_VMS=backotter-work/t2rest-x/world/vms", seen[0])
        self.assertEqual(len(calls), 2)
        for c in calls:
            self.assertIn("VM_UPDATE_REMOTE_VMS=backotter-work/t2rest-x/world/vms", c)


class GateNotStarted(unittest.TestCase):
    def test_a_gate_that_didnt_run_didnt_pass(self):
        from t2lib import gate
        with open(gate.__file__, encoding="utf-8") as f:
            self.assertIn('"ok": not stops and not unran', f.read())


if __name__ == "__main__":
    unittest.main()
