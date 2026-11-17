"""The KVM test hosts: all four amd64 hosts (chopin, cinqcent, copland and cage) are
default hosts since 2026-11-14, so every run without --hosts uses the small hosts too, and
choubox (a small amd64 cache host, 2026-11-17) and bigfoot (the arm64 host) are known,
selectable hosts that no default list holds yet (a host joins the defaults only after
HostTest2 and one representative per family pass on it,
docs/design/test2/design-classes.md).  What only a big host can run stays there by each
cell's ceiling and caps, never by a host's name (test_small_hosts.py, test_host_caps.py):
the checks here are that nothing names the hosts itself.  sync-hosts.sh, which only copies
code, has bigfoot too."""

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import re
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

import t2lib  # noqa: E402
from t2lib import gate, guest, leakscan, plan  # noqa: E402


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


class Hosts(unittest.TestCase):
    def test_known_and_default(self):
        self.assertEqual(t2lib.KNOWN_HOSTS, ("chopin", "cinqcent", "copland", "cage", "choubox", "bigfoot"))
        self.assertEqual(t2lib.DEFAULT_HOSTS, ("chopin", "cinqcent", "copland", "cage"))
        self.assertTrue(set(t2lib.DEFAULT_HOSTS) < set(t2lib.KNOWN_HOSTS))
        # The enabled hosts that are not default ones: choubox (its trial runs are still to
        # come) and bigfoot (docs/design/arm64.md A6).
        self.assertEqual([h for h in t2lib.KNOWN_HOSTS if h not in t2lib.DEFAULT_HOSTS], ["choubox", "bigfoot"])

    def test_t2_hosts_option(self):
        with contextlib.redirect_stderr(io.StringIO()):
            t2 = load("t2cli_hosts", os.path.join(T2, "t2"))
        self.assertEqual(t2.hosts_opt({}), ["chopin", "cinqcent", "copland", "cage"])
        self.assertEqual(t2.hosts_opt({"--hosts": "copland"}), ["copland"])
        self.assertEqual(t2.hosts_opt({"--hosts": "chopin,copland,cinqcent"}), ["chopin", "copland", "cinqcent"])
        self.assertEqual(t2.hosts_opt({"--hosts": "cage,copland"}), ["cage", "copland"])
        self.assertEqual(t2.hosts_opt({"--hosts": "bigfoot"}), ["bigfoot"])
        self.assertEqual(t2.hosts_opt({"--hosts": "choubox"}), ["choubox"])
        with self.assertRaisesRegex(t2.Usage,
                                    r"no such host czerny \(known: chopin, cinqcent, copland, cage, choubox, "
                                    r"bigfoot\)"):
            t2.hosts_opt({"--hosts": "chopin,czerny"})

    def test_plan_gate_and_guest_defaults(self):
        self.assertEqual(plan.Ctx.__init__.__defaults__[1], t2lib.DEFAULT_HOSTS)
        cells = gate.universe()
        # A per-host gate cell on every default host, the small ones too (they run the
        # loops with their class's smaller host_reserve, gate/layers.json).
        self.assertEqual([c for c in cells if "@" in c],
                         [f"gate:loop:{n}@{h}" for n in ("linux", "sparse") for h in t2lib.DEFAULT_HOSTS])
        self.assertEqual(gate.universe(hosts=["cage"]), cells)   # already a default host
        self.assertEqual([c for c in gate.universe(hosts=["bigfoot"]) if c.endswith("@bigfoot")],
                         ["gate:loop:linux@bigfoot", "gate:loop:sparse@bigfoot"])
        self.assertEqual(guest.hostjob_hosts(), t2lib.DEFAULT_HOSTS)
        self.assertEqual([n for n in plan.GATE_FALLBACK if "@" in n],
                         [f"loop:{n}@{h}" for n in ("linux", "sparse") for h in t2lib.DEFAULT_HOSTS])

    def test_leakscan_knows_every_host(self):
        self.assertEqual(leakscan.HOSTS, t2lib.KNOWN_HOSTS)
        self.assertEqual([c for c, _ in leakscan.pattern_findings("made on Copland, copied to copland.local")],
                         ["host", "host"])
        self.assertEqual(leakscan.pattern_findings("made on copland", True), [])
        self.assertEqual([c for c, _ in leakscan.pattern_findings("made on Cage, copied to cage.local")], ["host", "host"])
        self.assertEqual([c for c, _ in leakscan.pattern_findings("made on Bigfoot, copied to bigfoot.local")],
                         ["host", "host"])
        self.assertEqual([c for c, _ in leakscan.pattern_findings("made on Choubox, copied to choubox.local")],
                         ["host", "host"])
        self.assertEqual(leakscan.pattern_findings("cages, encaged, a birdcage"), [])   # cage counts as a word only

    def test_drive_hosts(self):
        drive = load("st1_drive_hosts", os.path.join(TESTVM, "stictest1", "drive.py"))
        self.assertEqual(drive.HOSTS["copland"],
                         dict(ssh="m@copland.local", slot_range="2800-2899", ports=(2880, 2899)))
        self.assertEqual(drive.HOSTS["cage"], dict(ssh="m@cage.local", slot_range="3000-3099", ports=(3080, 3099)))
        self.assertEqual(drive.DEFAULT_HOSTS, ("chopin", "cinqcent", "copland", "cage"))
        err = io.StringIO()
        with mock.patch.object(sys, "argv", ["drive.py", "r1", "--hosts", "cage,czerny"]), \
                contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            drive.main()
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("unknown host czerny (known: chopin, cinqcent, copland, cage, choubox, bigfoot)",
                      err.getvalue())

    def test_guest_slot_ranges(self):
        gs = os.path.join(TESTVM, "compare", "guest-slot.sh")
        got = {h: subprocess.run(["bash", "-c", '. "$1"; slot_range_here "$2"', "-", gs, h],
                                 capture_output=True, text=True, check=True).stdout.strip()
               for h in ("chopin", "copland", "cinqcent", "copland.local", "cage", "cage.local")}
        self.assertEqual(got, {"chopin": "2700-2799", "copland": "2800-2899", "cinqcent": "2900-2999",
                               "copland.local": "2800-2899", "cage": "3000-3099", "cage.local": "3000-3099"})

    def run_all_dry(self, host, extra_env=None):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "bin", "linux"))
            os.makedirs(os.path.join(d, "work", "vms"))
            for t in ("restic", "rustic"):
                with open(os.path.join(d, "bin", "linux", f"{t}-bko"), "w") as f:
                    f.write("x\n")
            env = {k: v for k, v in os.environ.items()
                   if k not in ("LINUX_FS", "SPARSE_FS", "PORT_BASE", "MAX_GUESTS", "COUNT_OTHERS", "GUEST_PORTS",
                                "ANY_HOST", "STEP_TIMEOUT", "HOST_JOBS", "SPARSE_JOBS", "JOBS_CAP")}
            env.update(BKO_WORK=os.path.join(d, "work"), OLD_BIN="none", UNCHECKED_BINARIES="1", **(extra_env or {}))
            return subprocess.run(["bash", os.path.join(TESTVM, "compare", "run-all.sh"), "--host", host, "--dry-run",
                                   os.path.join(d, "bin"), os.path.join(d, "log")],
                                  capture_output=True, text=True, env=env, timeout=120)

    def test_run_all_small_profiles(self):
        for host, base, ports in (("copland", 2800, "2800-2899"), ("cage", 3000, "3000-3099"),
                                  ("choubox", 3300, "3300-3399")):
            r = self.run_all_dry(host, {"HOST_JOBS": "2"})   # as gate/loop.sh sets it
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            head = [x for x in r.stdout.splitlines() if x.startswith(f"== {host}: ")]
            self.assertEqual(len(head), 1, r.stdout)
            self.assertIn(f"ports {base}+, at most 3 guests (others counted: 1, {ports}), 1 host jobs, 1 sparse jobs",
                          head[0])
            self.assertIn(f"run-all: HOST_JOBS 2 -> 1 on {host} (JOBS_CAP)", r.stdout)
            self.assertTrue(head[0].endswith("linux: ext4 xfs btrfs f2fs tmpfs jfs; sparse: ext4 xfs btrfs"), head[0])
            steps = re.findall(r"^-- (\S+): ", r.stdout, re.M)
            self.assertEqual(sorted({s.split("/")[0] for s in steps}), ["linux", "sparse"])
            self.assertFalse([s for s in steps if re.search(r"zfs|bcachefs|nonroot", s)], steps)
            r = self.run_all_dry(host, {"HOST_JOBS": "2", "JOBS_CAP": "0"})   # JOBS_CAP=0: no cap
            self.assertIn("2 host jobs, 2 sparse jobs", r.stdout)
        r = self.run_all_dry("cinqcent")   # the big hosts: no cap
        self.assertIn("4 host jobs, 2 sparse jobs", r.stdout)
        self.assertNotIn("JOBS_CAP", r.stdout)
        r = subprocess.run(["bash", os.path.join(TESTVM, "compare", "run-all.sh"), "--host", "czerny", "--dry-run",
                            REPO, "x"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--host chopin, cinqcent, copland, cage, choubox or bigfoot", r.stderr)

    def test_sync_hosts_and_vm_update(self):
        sync = read("testvm", "sync-hosts.sh")   # its default: every enabled host of testvm/hosts.json
        self.assertIn("list=$(hosts_names --field ssh)", sync)
        self.assertEqual(t2lib.hosts_table.names(),
                         ("chopin", "cinqcent", "copland", "cage", "choubox", "bigfoot"))   # test_host_table runs it
        vmu = load("vm_update_hosts", os.path.join(TESTVM, "vm-update"))
        self.assertEqual(vmu.ALL_HOSTS, ["chopin.local"])   # cinqcent, copland, cage and choubox are caches

    def test_checks_take_their_hosts_range(self):
        d = os.path.join(TESTVM, "stictest1")
        for g in sorted(os.listdir(d)):
            p = os.path.join(d, g, "check.sh")
            if not os.path.isfile(p):
                continue
            with open(p, encoding="utf-8") as f:
                code = re.sub(r"(?m)^\s*#.*$", "", f.read())
            self.assertNotRegex(code, r"\b2\d00-2\d99\b", g)
            if "slot_take" in code:
                self.assertIn('slot_take 3 "$(slot_range_here)"', code, g)


if __name__ == "__main__":
    unittest.main()
