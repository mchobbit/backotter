"""The KVM test hosts: copland is a known, selectable host everywhere hosts are
named or checked, and no default host list holds it yet (a new host joins the
defaults only after HostTest2 and one representative per family pass on it,
docs/design/test2/design-classes.md).  sync-hosts.sh, which only copies code,
is the exception."""

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
        self.assertEqual(t2lib.KNOWN_HOSTS, ("chopin", "cinqcent", "copland"))
        self.assertEqual(t2lib.DEFAULT_HOSTS, ("chopin", "cinqcent"))
        self.assertTrue(set(t2lib.DEFAULT_HOSTS) < set(t2lib.KNOWN_HOSTS))

    def test_t2_hosts_option(self):
        with contextlib.redirect_stderr(io.StringIO()):
            t2 = load("t2cli_hosts", os.path.join(T2, "t2"))
        self.assertEqual(t2.hosts_opt({}), ["chopin", "cinqcent"])
        self.assertEqual(t2.hosts_opt({"--hosts": "copland"}), ["copland"])
        self.assertEqual(t2.hosts_opt({"--hosts": "chopin,copland,cinqcent"}), ["chopin", "copland", "cinqcent"])
        with self.assertRaisesRegex(t2.Usage, r"no such host cage \(known: chopin, cinqcent, copland\)"):
            t2.hosts_opt({"--hosts": "chopin,cage"})

    def test_plan_gate_and_guest_defaults(self):
        self.assertEqual(plan.Ctx.__init__.__defaults__[1], t2lib.DEFAULT_HOSTS)
        cells = gate.universe()
        self.assertTrue(any(c.endswith("@chopin") for c in cells) and any(c.endswith("@cinqcent") for c in cells))
        self.assertFalse([c for c in cells if c.endswith("@copland")])   # per-host gate cells: the default hosts
        self.assertTrue(any(c.endswith("@copland") for c in gate.universe(hosts=["copland"])))
        self.assertEqual(guest.hostjob_hosts(), t2lib.DEFAULT_HOSTS)
        self.assertFalse(any("copland" in n for n in plan.GATE_FALLBACK))

    def test_leakscan_knows_every_host(self):
        self.assertEqual(leakscan.HOSTS, t2lib.KNOWN_HOSTS)
        self.assertEqual([c for c, _ in leakscan.pattern_findings("made on Copland, copied to copland.local")],
                         ["host", "host"])
        self.assertEqual(leakscan.pattern_findings("made on copland", True), [])

    def test_drive_hosts(self):
        drive = load("st1_drive_hosts", os.path.join(TESTVM, "stictest1", "drive.py"))
        self.assertEqual(drive.HOSTS["copland"],
                         dict(ssh="m@copland.local", slot_range="2800-2899", ports=(2880, 2899)))
        self.assertEqual(drive.DEFAULT_HOSTS, ("chopin", "cinqcent"))
        err = io.StringIO()
        with mock.patch.object(sys, "argv", ["drive.py", "r1", "--hosts", "copland,cage"]), \
                contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            drive.main()
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("unknown host cage (known: chopin, cinqcent, copland)", err.getvalue())

    def test_guest_slot_ranges(self):
        gs = os.path.join(TESTVM, "compare", "guest-slot.sh")
        got = {h: subprocess.run(["bash", "-c", '. "$1"; slot_range_here "$2"', "-", gs, h],
                                 capture_output=True, text=True, check=True).stdout.strip()
               for h in ("chopin", "copland", "cinqcent", "copland.local")}
        self.assertEqual(got, {"chopin": "2700-2799", "copland": "2800-2899", "cinqcent": "2900-2999",
                               "copland.local": "2800-2899"})

    def test_run_all_copland_profile(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "bin", "linux"))
            os.makedirs(os.path.join(d, "work", "vms"))
            for t in ("restic", "rustic"):
                with open(os.path.join(d, "bin", "linux", f"{t}-bko"), "w") as f:
                    f.write("x\n")
            env = {k: v for k, v in os.environ.items()
                   if k not in ("LINUX_FS", "SPARSE_FS", "PORT_BASE", "MAX_GUESTS", "COUNT_OTHERS", "GUEST_PORTS",
                                "ANY_HOST", "STEP_TIMEOUT")}
            env.update(BKO_WORK=os.path.join(d, "work"), OLD_BIN="none", UNCHECKED_BINARIES="1")
            r = subprocess.run(["bash", os.path.join(TESTVM, "compare", "run-all.sh"), "--host", "copland", "--dry-run",
                                os.path.join(d, "bin"), os.path.join(d, "log")],
                               capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        head = [x for x in r.stdout.splitlines() if x.startswith("== copland: ")]
        self.assertEqual(len(head), 1, r.stdout)
        self.assertIn("ports 2800+, at most 3 guests (others counted: 1, 2800-2899)", head[0])
        self.assertTrue(head[0].endswith("linux: ext4 xfs btrfs f2fs tmpfs jfs; sparse: ext4 xfs btrfs"), head[0])
        steps = re.findall(r"^-- (\S+): ", r.stdout, re.M)
        self.assertEqual(sorted({s.split("/")[0] for s in steps}), ["linux", "sparse"])
        self.assertFalse([s for s in steps if re.search(r"zfs|bcachefs|nonroot", s)], steps)
        r = subprocess.run(["bash", os.path.join(TESTVM, "compare", "run-all.sh"), "--host", "cage", "--dry-run",
                            REPO, "x"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--host chopin, cinqcent or copland", r.stderr)

    def test_sync_hosts_and_vm_update(self):
        sync = read("testvm", "sync-hosts.sh")
        self.assertIn("hosts=(m@chopin.local m@cinqcent.local m@copland.local)", sync)
        vmu = load("vm_update_hosts", os.path.join(TESTVM, "vm-update"))
        self.assertEqual(vmu.ALL_HOSTS, ["chopin.local", "cinqcent.local"])   # copland has no guests yet

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
