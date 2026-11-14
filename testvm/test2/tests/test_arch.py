"""Hosts and guests have an architecture (docs/design/arm64.md A1): hosts.json's arch (amd64 when
a row has none) and job_slice, the readers (hosts.py, hosts.sh), placement (an -arm64 guest never
on an amd64 host, nor the reverse), and the consumers that keep to amd64 hosts (bs2, run-all.sh's
SPLIT, vm-update's merge hosts, holt's bko-holt shipping, drive.py) or take bigfoot as any host
(sync-hosts.sh).  No host needed."""

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

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
TESTVM = os.path.dirname(T2)
REPO = os.path.dirname(TESTVM)
sys.path.insert(0, T2)
sys.path.insert(0, os.path.join(HERE, "fixtures", "p2"))

from t2lib import facts as factsmod  # noqa: E402,F401  (t2lib's own import order)
from t2lib import holt, plan  # noqa: E402
from t2lib import hosts as H  # noqa: E402
import world  # noqa: E402

TABLE = os.path.join(TESTVM, "hosts.json")
HOSTS_PY = os.path.join(T2, "t2lib", "hosts.py")
HOSTS_SH = os.path.join(TESTVM, "hosts.sh")
FACTS = os.path.join(T2, "t2lib", "facts_host.py")
PREFIX = ["sudo", "-n", "systemd-run", "--scope", "--quiet", "--slice=bko.slice", "--", "setpriv", "--reuid=m",
          "--regid=m", "--init-groups", "--reset-env", "--"]


def load(name, path, env=None):
    with mock.patch.dict(os.environ, env or {}):
        loader = importlib.machinery.SourceFileLoader(name, path)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        m = importlib.util.module_from_spec(spec)
        loader.exec_module(m)
    return m


def table():
    with open(TABLE, encoding="utf-8") as f:
        return json.load(f)


def row_of(t, name):
    return next(r for r in t["hosts"] if r["name"] == name)


def clean_env(**extra):
    e = dict(os.environ)
    e.pop(H.ENV, None)
    e.update(extra)
    return e


class Table(unittest.TestCase):
    def test_bigfoot_row(self):
        r = H.row("bigfoot")
        self.assertEqual((r["ssh"], r["host_key_alias"], r["slot_range"], r["stic_ports"], r["class"], r["roles"],
                          r["default"], r["enabled"], r["arch"], r["job_slice"]),
                         ("m@bigfoot.local", "bigfoot.local", "3200-3299", [3280, 3299], "small", [], False, True,
                          "arm64", "bko.slice"))
        for role in ("build", "fleet", "cache", "store-mirror", "bigfoot-link"):
            self.assertNotIn("bigfoot", H.with_role(role), role)
        self.assertNotIn("bigfoot", H.defaults())
        self.assertEqual(H.limits("bigfoot"), dict(H.LIMITS, stic_slots=2, holt_jobs=1, base_gib=4, bs2_slots=2))

    def test_arch_defaults_to_amd64(self):
        self.assertEqual({h: H.host_arch(h) for h in H.names(all_=True)},
                         {"chopin": "amd64", "cinqcent": "amd64", "copland": "amd64", "cage": "amd64", "czerny": "amd64",
                          "bigfoot": "arm64"})
        self.assertFalse(any("arch" in row_of(table(), h) for h in ("chopin", "cinqcent", "copland", "cage", "czerny")))
        self.assertEqual(H.host_arch("bigfoot.local"), "arm64")
        self.assertEqual(H.host_arch("nosuch"), "amd64")   # every host was before the field
        self.assertEqual(H.names(arch="arm64"), ("bigfoot",))
        self.assertEqual(H.names(arch="amd64"), ("chopin", "cinqcent", "copland", "cage"))
        self.assertEqual(H.names(arch="amd64", all_=True)[-1], "czerny")

    def test_guest_arch(self):
        for g, a in (("debian13", "amd64"), ("debian13-arm64", "arm64"), ("debian13-zfs-arm64", "arm64"),
                     ("alpine324-arm64+img", "arm64"), ("pop24+img", "amd64"), ("vms/fedora44-arm64/", "arm64"),
                     ("arm64", "amd64"), ("debian13-arm64x", "amd64"), ("", "amd64"), (None, "amd64")):
            self.assertEqual(H.guest_arch(g), a, g)
        self.assertTrue(H.runs_on("debian13-arm64", "bigfoot"))
        self.assertFalse(H.runs_on("debian13-arm64", "chopin"))
        self.assertFalse(H.runs_on("debian13", "bigfoot"))
        self.assertTrue(H.runs_on("debian13", "cage.local"))

    def test_machine_arch(self):
        self.assertEqual([H.machine_arch(m) for m in ("x86_64", "aarch64", "arm64", "AARCH64\n", "riscv64", "", None)],
                         ["amd64", "arm64", "arm64", "arm64", None, None, None])

    def test_job_prefix(self):
        self.assertEqual(H.job_slice("bigfoot"), "bko.slice")
        self.assertEqual(H.job_prefix("bigfoot"), PREFIX)
        self.assertEqual(H.job_prefix("bigfoot", "root")[8:10], ["--reuid=root", "--regid=root"])
        for h in ("chopin", "cage", "czerny", "nosuch"):
            self.assertIsNone(H.job_slice(h), h)
            self.assertEqual(H.job_prefix(h), [], h)

    def test_bad_arch_and_slice(self):
        def errs(change):
            t = table()
            change(t)
            return " | ".join(H.check_table(t))
        self.assertIn("arch 'x86_64': one of amd64, arm64", errs(lambda t: row_of(t, "cage").update(arch="x86_64")))
        self.assertIn("job_slice 'bko': a systemd slice's name (NAME.slice) or null",
                      errs(lambda t: row_of(t, "cage").update(job_slice="bko")))
        self.assertIn("job_slice 'a b.slice'", errs(lambda t: row_of(t, "cage").update(job_slice="a b.slice")))
        self.assertEqual(errs(lambda t: row_of(t, "cage").update(arch="amd64", job_slice=None)), "")

    def test_command_line(self):
        def run(*args):
            r = subprocess.run([sys.executable, "-I", "-B", HOSTS_PY] + list(args), capture_output=True, text=True,
                               env=clean_env())
            return r.returncode, r.stdout, r.stderr
        self.assertEqual(run("names", "--arch", "arm64"), (0, "bigfoot\n", ""))
        self.assertEqual(run("names", "--arch", "amd64"), (0, "chopin\ncinqcent\ncopland\ncage\n", ""))
        self.assertEqual(run("names", "--arch", "sparc")[0], 2)
        self.assertEqual(run("names", "--field", "arch"), (0, "amd64\namd64\namd64\namd64\narm64\n", ""))
        self.assertEqual(run("get", "chopin", "arch"), (0, "amd64\n", ""))   # the default, not an empty line
        self.assertEqual(run("get", "chopin", "job_slice"), (0, "\n", ""))
        self.assertEqual(run("get", "bigfoot", "job_slice"), (0, "bko.slice\n", ""))
        self.assertEqual(run("arch", "bigfoot.local"), (0, "arm64\n", ""))
        self.assertEqual(run("arch", "nosuch"), (0, "amd64\n", ""))
        self.assertEqual(run("guest-arch", "ubuntu26-arm64"), (0, "arm64\n", ""))
        self.assertEqual(run("job-prefix", "bigfoot"), (0, "\n".join(PREFIX) + "\n", ""))
        self.assertEqual(run("job-prefix", "chopin"), (0, "", ""))
        self.assertEqual(run("job-prefix", "nosuch", "m"), (0, "", ""))
        self.assertEqual(run("job-prefix")[0], 2)

    def test_shell_reader(self):
        r = subprocess.run(["bash", "-c", '. "$1"; hosts_arch bigfoot; hosts_arch chopin; hosts_guest_arch x-arm64; '
                            'hosts_names --arch arm64; p=(); while IFS= read -r w; do p+=("$w"); done '
                            '< <(hosts_job_prefix bigfoot m); echo "${#p[@]} ${p[5]} ${p[12]}"; hosts_job_prefix cage',
                            "-", HOSTS_SH], capture_output=True, text=True, env=clean_env(), timeout=60)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(r.stdout, "arm64\namd64\narm64\nbigfoot\n13 --slice=bko.slice --\n")


class Placement(unittest.TestCase):
    """An -arm64 guest's cells only on an arm64 host, every other one only on an amd64 host; the
    gate's cells (amd64 binaries) off an arm64 host."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t2-arch-plan-")
        cls.w = world.World(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def planner(self, facts):
        pl = plan.Planner.__new__(plan.Planner)
        pl.ctx = plan.Ctx(self.w.repo, hosts=list(facts), options={})
        pl.ctx.facts = facts
        pl.cls = pl.ctx.classes
        return pl

    def fit(self, cell, hosts, facts=None):
        fam = cell.split(":", 1)[0]
        v = plan.View(cell, fam)
        v.hosts = list(hosts)
        self.planner(facts or {h: {} for h in hosts}).fit_capabilities(v)
        return v

    def test_an_arm64_guest_never_on_an_amd64_host(self):
        for cell in ("stic:debian13-arm64", "holt:debian13-arm64", "holt:alpine324-arm64+img"):
            v = self.fit(cell, ["chopin", "cinqcent", "copland", "cage", "bigfoot"])
            self.assertEqual((v.hosts, v.why_not, v.pc.host), (["bigfoot"], None, "bigfoot"), cell)
            self.assertIn("chopin: an amd64 host, not arm64 (hosts.json's arch)", v.pc.reasons_extra[0])
            v = self.fit(cell, ["chopin", "cage"])
            self.assertEqual(v.hosts, [], cell)
            self.assertEqual(v.why_not, "no host of the run can run it: chopin: an amd64 host, not arm64 (hosts.json's "
                                        "arch); cage: an amd64 host, not arm64 (hosts.json's arch)")

    def test_an_amd64_guest_never_on_an_arm64_host(self):
        for cell in ("stic:debian13", "holt:debian13", "holt:freebsd15", "holt:ubuntu24+img"):
            v = self.fit(cell, ["chopin", "bigfoot"])
            self.assertEqual(v.hosts, ["chopin"], cell)
            self.assertIn("kept off bigfoot: an arm64 host, not amd64 (hosts.json's arch)", v.pc.reasons_extra, cell)
            v = self.fit(cell, ["bigfoot"])
            self.assertEqual((v.hosts, v.why_not), ([], "no host of the run can run it: bigfoot: an arm64 host, not "
                                                         "amd64 (hosts.json's arch)"), cell)

    def test_facts_that_contradict_the_table(self):
        v = self.fit("stic:debian13", ["chopin", "cinqcent"], {"chopin": {"machine": "aarch64"},
                                                                "cinqcent": {"machine": "x86_64"}})
        self.assertEqual(v.hosts, ["cinqcent"])
        self.assertIn("kept off chopin: its facts say machine aarch64, not amd64 (hosts.json's arch is wrong?)",
                      v.pc.reasons_extra)
        v = self.fit("stic:debian13-arm64", ["bigfoot"], {"bigfoot": {"machine": "aarch64"}})
        self.assertEqual(v.hosts, ["bigfoot"])
        v = self.fit("stic:debian13", ["chopin"], {"chopin": {}})   # old facts: no machine, the table alone
        self.assertEqual(v.hosts, ["chopin"])

    def test_whole_plans_with_bigfoot(self):
        hs = ["chopin", "bigfoot"]
        fx = self.w.facts(hosts=hs)
        for text, cell in (("SticTest2 rel4 debian13 plan", "stic:debian13"), ("HoltTest2 debian13 plan", "holt:debian13")):
            c = self.w.plan(text, facts=fx, hosts=hs).cell(cell)
            self.assertEqual((c.status, sorted(c.keys), c.host), ("due", ["chopin"], "chopin"), cell)
            self.assertIn("kept off bigfoot: an arm64 host, not amd64 (hosts.json's arch)", c.reasons)
        c = self.w.plan("SticTest2 rel4 debian13 plan", facts={"bigfoot": fx["bigfoot"]}, hosts=["bigfoot"]).cell(
            "stic:debian13")
        self.assertEqual(c.status, "not-runnable")
        p = self.w.plan("GateTest2 plan", facts=fx, hosts=hs)
        g = p.cell("gate:loop:linux@bigfoot")
        self.assertEqual((g.status, g.reasons), ("not-runnable", ["no host of the run can run it: bigfoot: an arm64 "
                                                                  "host, not amd64 (hosts.json's arch)"]))
        self.assertEqual(sorted(p.cell("gate:loop:linux@chopin").keys), ["chopin"])
        self.assertEqual(sorted(p.cell("gate:bin:abi").keys), ["chopin"])


class Consumers(unittest.TestCase):
    def test_bs2_keeps_to_amd64_hosts(self):
        bs2 = load("arch_bs2", os.path.join(TESTVM, "backshot2", "bs2"), {H.ENV: TABLE})
        self.assertNotIn("bigfoot", bs2.RUN_HOSTS)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            bs2.use_hosts("chopin,bigfoot")
        self.assertIn("bigfoot: not an amd64 host", str(cm.exception))
        bs2.use_hosts("all")
        self.assertEqual(list(bs2.RUN_HOSTS), ["chopin", "cinqcent", "copland", "cage"])

    def test_vm_update_merges_on_hosts_of_the_guests_arch(self):
        vmu = load("arch_vm_update", os.path.join(TESTVM, "vm-update"), {H.ENV: TABLE})
        self.assertEqual(vmu.ALL_HOSTS, ["chopin.local"])   # bigfoot has no fleet role
        hs = [None, "chopin.local", "bigfoot.local", "cage.local"]
        self.assertEqual(vmu.arch_hosts("debian13", hs), [None, "chopin.local", "cage.local"])
        self.assertEqual(vmu.arch_hosts("debian13-arm64", hs), [None, "bigfoot.local"])

    def test_holt_ships_bko_holt_to_amd64_hosts_only(self):
        r = holt.Runner.__new__(holt.Runner)
        r.hosts, r.opts = ["chopin"], {"holt_ship": "known"}
        self.assertEqual(r.all_hosts(), ["chopin", "cinqcent", "copland", "cage"])

    def test_drive_py(self):
        drive = load("arch_drive", os.path.join(TESTVM, "stictest1", "drive.py"), {H.ENV: TABLE})
        both = drive.parse_probe("VMS\nVM debian13\nVM debian13-arm64\nEND\n")
        self.assertEqual(drive.arch_only("bigfoot", both)["vms"], {"debian13-arm64"})
        self.assertEqual(drive.arch_only("cage", drive.parse_probe("VMS\nVM debian13\nVM debian13-arm64\nEND\n"))["vms"],
                         {"debian13"})
        self.assertEqual(sorted(drive.no_arch_host(["debian13", "debian13-arm64"], ["chopin"])), ["debian13-arm64"])
        self.assertEqual(drive.HOSTS["bigfoot"], dict(ssh="m@bigfoot.local", slot_range="3200-3299", ports=(3280, 3299)))
        self.assertNotIn("bigfoot", drive.DEFAULT_HOSTS)

    def test_run_all_split_keeps_to_amd64_hosts(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "bin", "linux"))
            os.makedirs(os.path.join(d, "work", "vms"))
            e = {k: v for k, v in clean_env().items() if k not in ("SPLIT", "ANY_HOST", "PORT_BASE", "GUEST_PORTS")}
            e.update(BKO_WORK=os.path.join(d, "work"), OLD_BIN="none", UNCHECKED_BINARIES="1", SPLIT="chopin,bigfoot")
            r = subprocess.run(["bash", os.path.join(TESTVM, "compare", "run-all.sh"), "--host", "chopin", "--dry-run",
                                os.path.join(d, "bin"), os.path.join(d, "log")], capture_output=True, text=True, env=e,
                               timeout=120)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("SPLIT: bigfoot: not an amd64 host", r.stderr)

    def sync(self, *args):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "log")
            for tool in ("ssh", "rsync"):
                p = os.path.join(d, tool)
                with open(p, "w") as f:
                    f.write(f'#!/bin/sh\necho "{tool} $*" >> "{log}"\n')
                os.chmod(p, 0o755)
            e = clean_env()
            e["PATH"] = d + os.pathsep + e["PATH"]
            r = subprocess.run(["bash", os.path.join(TESTVM, "sync-hosts.sh"), "--dry-run"] + list(args),
                               capture_output=True, text=True, env=e, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with open(log) as f:
                return [x.split()[3] for x in f if x.startswith("ssh ")]

    def test_sync_hosts_takes_a_tables_name(self):
        self.assertEqual(self.sync("bigfoot"), ["m@bigfoot.local"])
        self.assertEqual(self.sync("m@bigfoot.local", "cage", "other.example", "nosuch"),
                         ["m@bigfoot.local", "m@cage.local", "other.example", "nosuch"])
        self.assertIn("m@bigfoot.local", self.sync())

    def test_facts_record_the_machine(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "root")
            shutil.copytree(os.path.join(HERE, "fixtures", "p1", "factsroot"), root, symlinks=True)
            work = os.path.join(root, "home", "m", "backotter-work")

            def probe():
                args = [sys.executable, "-I", "-", "--work", work, "--user", "m", "--home", os.path.dirname(work)]
                with open(FACTS, "rb") as f:
                    p = subprocess.run(args, stdin=f, capture_output=True, env=dict(os.environ, T2_FACTS_ROOT=root),
                                       timeout=60)
                self.assertEqual(p.returncode, 0, p.stderr)
                return json.loads(p.stdout)
            f = probe()
            self.assertIsNone(f["machine"])   # a fake root without /proc/sys/kernel/arch
            self.assertFalse([e for e in f["errors"] if e.startswith("machine")], f["errors"])
            os.makedirs(os.path.join(root, "proc", "sys", "kernel"), exist_ok=True)
            with open(os.path.join(root, "proc", "sys", "kernel", "arch"), "w") as fh:
                fh.write("aarch64\n")
            self.assertEqual(probe()["machine"], "aarch64")


if __name__ == "__main__":
    unittest.main()
