"""The host table, testvm/hosts.json (t2lib/hosts.py, testvm/hosts.sh): it checks, and every
list, range and profile derived from it equals the constants the scripts had before it
(chopin and cinqcent the default hosts, copland and cage known, czerny's row reserved), with
bigfoot, the arm64 host, known since 2026-11-14 (an amd64-only list leaves it out).
An alternative table (BKO_HOSTS_JSON) shows that the consumers follow it: czerny enabled
gets its own range and the small profile, a host without the build role is refused."""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
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
from t2lib import gate, plan  # noqa: E402
from t2lib import hosts as H  # noqa: E402

TABLE = os.path.join(TESTVM, "hosts.json")
GUEST_SLOT = os.path.join(TESTVM, "compare", "guest-slot.sh")
BEFORE_DRIVE = {   # drive.py's HOSTS before the table
    "chopin": dict(ssh="m@chopin.local", slot_range="2700-2799", ports=(2780, 2799)),
    "cinqcent": dict(ssh="m@cinqcent.local", slot_range="2900-2999", ports=(2980, 2999)),
    "copland": dict(ssh="m@copland.local", slot_range="2800-2899", ports=(2880, 2899)),
    "cage": dict(ssh="m@cage.local", slot_range="3000-3099", ports=(3080, 3099)),
}
BIGFOOT_DRIVE = dict(ssh="m@bigfoot.local", slot_range="3200-3299", ports=(3280, 3299))
KNOWN = ("chopin", "cinqcent", "copland", "cage", "bigfoot")
BEFORE_GATE_FALLBACK = ("holt", "fork:restic", "fork:rustic_core", "fork:rustic", "fork-root:restic", "bin:abi",
                        "bin:static", "bin:version", "loop:linux@chopin", "loop:linux@cinqcent", "loop:sparse@chopin",
                        "loop:sparse@cinqcent", "selftest")


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


def bash(script, *args, env=None):
    e = dict(os.environ)
    e.pop(H.ENV, None)
    e.update(env or {})
    return subprocess.run(["bash", "-c", script, "-"] + list(args), capture_output=True, text=True, env=e, timeout=120)


class AltTable:
    """A changed copy of the table in a temporary file: BKO_HOSTS_JSON=path."""

    def __init__(self, change):
        self.t = table()
        change(self.t)
        self.d = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.d.name, "hosts.json")
        with open(self.path, "w") as f:
            json.dump(self.t, f)

    def env(self):
        return {H.ENV: self.path}

    def close(self):
        self.d.cleanup()


def row_of(t, name):
    return next(r for r in t["hosts"] if r["name"] == name)


def enable_czerny(t):
    row_of(t, "czerny")["enabled"] = True


class Table(unittest.TestCase):
    def test_the_table_checks(self):
        self.assertEqual(H.check_table(table()), [])
        self.assertEqual(H.path(), TABLE)
        self.assertEqual(os.path.dirname(H.TABLE), TESTVM)   # found beside testvm/, wherever the tree is

    def test_names_and_order(self):
        self.assertEqual(H.names(all_=True), ("chopin", "cinqcent", "copland", "cage", "czerny", "bigfoot"))
        self.assertEqual(H.known(), KNOWN)
        self.assertEqual(H.defaults(), ("chopin", "cinqcent"))
        self.assertEqual(H.names(cls="small"), ("copland", "cage", "bigfoot"))
        self.assertEqual(H.names(arch="amd64"), ("chopin", "cinqcent", "copland", "cage"))
        self.assertEqual(H.names(arch="arm64"), ("bigfoot",))
        self.assertEqual(H.names(cls="big"), ("chopin", "cinqcent"))
        self.assertEqual(H.with_role("build"), ("chopin", "cinqcent"))
        self.assertEqual(H.with_role("fleet"), ("chopin",))
        self.assertEqual(H.with_role("store-mirror"), ("chopin",))
        self.assertEqual(H.with_role("bigfoot-link"), ("chopin",))
        self.assertEqual(H.with_role("cache"), ("cinqcent", "copland", "cage"))

    def test_czerny_is_reserved(self):
        r = H.row("czerny", all_=True)
        self.assertEqual((r["slot_range"], r["stic_ports"], r["class"], r["default"], r["enabled"]),
                         ("3100-3199", [3180, 3199], "small", False, False))
        self.assertIsNone(H.find("czerny"))
        self.assertIsNone(H.host_class("czerny"))
        with self.assertRaisesRegex(H.HostsError, "czerny is not enabled"):
            H.row("czerny")
        with self.assertRaisesRegex(H.HostsError, r"no such host chopin2 \(known: chopin, cinqcent, copland, cage, bigfoot\)"):
            H.row("chopin2")

    def test_lookups(self):
        self.assertEqual(H.row("copland.local")["name"], "copland")
        self.assertEqual(H.short("cage.local"), "cage")
        self.assertEqual(H.ssh("cage"), "m@cage.local")
        self.assertEqual(H.addr("chopin"), "chopin.local")
        self.assertEqual(H.slot_range("cinqcent"), (2900, 2999))
        self.assertEqual(H.slot_range("czerny", all_=True), (3100, 3199))
        self.assertEqual(H.stic_ports("copland"), (2880, 2899))
        self.assertEqual([H.host_class(h) for h in ("chopin", "cinqcent", "copland", "cage", "nope", None)],
                         ["big", "big", "small", "small", None, None])
        self.assertEqual(H.phrase(["a", "b", "c"]), "a, b or c")
        self.assertEqual(H.phrase(["a"]), "a")
        self.assertIsNone(H.ssh_fallback("chopin"))   # no ip known yet
        self.assertEqual(H.ssh_fallback("bigfoot"), ["-o", "HostKeyAlias=bigfoot.local", "m@192.168.68.197"])

    def test_ssh_fallback(self):
        t = table()
        row_of(t, "copland")["ip"] = "192.0.2.7"
        self.assertEqual(H.ssh_fallback("copland", table=t), ["-o", "HostKeyAlias=copland.local", "m@192.0.2.7"])
        row_of(t, "copland")["host_key_alias"] = None
        self.assertEqual(H.ssh_fallback("copland", table=t), ["-o", "HostKeyAlias=copland.local", "m@192.0.2.7"])

    def test_bad_tables(self):
        def errs(change):
            t = table()
            change(t)
            return " | ".join(H.check_table(t))
        self.assertIn("name copland twice", errs(lambda t: row_of(t, "cage").update(name="copland")))
        self.assertIn("overlaps chopin's", errs(lambda t: row_of(t, "czerny").update(slot_range="2750-2850")))
        self.assertIn("stic_ports [3080, 3099] outside slot_range 3100-3199",
                      errs(lambda t: row_of(t, "czerny").update(stic_ports=[3080, 3099])))
        self.assertIn("slot_range '3199-3100'", errs(lambda t: row_of(t, "czerny").update(slot_range="3199-3100")))
        self.assertIn("class 'medium'", errs(lambda t: row_of(t, "cage").update({"class": "medium"})))
        self.assertIn("role 'builder'", errs(lambda t: row_of(t, "cage").update(roles=["builder"])))
        self.assertIn("no such field 'defualt'", errs(lambda t: row_of(t, "cage").update(defualt=True)))
        self.assertIn("a default host must be enabled", errs(lambda t: row_of(t, "czerny").update(default=True)))
        self.assertIn("default: true or false", errs(lambda t: row_of(t, "cage").update(default="no")))
        self.assertIn("ssh 'cage.local': USER@HOST", errs(lambda t: row_of(t, "cage").update(ssh="cage.local")))
        self.assertIn("no default host", errs(lambda t: [r.update(default=False) for r in t["hosts"]]))
        self.assertIn("hosts[3] (cage): no slot_range", errs(lambda t: row_of(t, "cage").pop("slot_range")))
        self.assertIn("name 'Cage'", errs(lambda t: row_of(t, "cage").update(name="Cage")))

    def test_a_bad_or_missing_table_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "hosts.json")
            with self.assertRaisesRegex(H.TableError, "the host table .*hosts.json"):
                H.load(p)
            with open(p, "w") as f:
                f.write('{"v": 1, "classes": {"big": ""}, "roles": {}, "hosts": []}')
            with self.assertRaisesRegex(H.TableError, "hosts: a list of rows"):
                H.load(p)
            r = subprocess.run([sys.executable, "-I", "-B", os.path.join(T2, "t2lib", "hosts.py"), "names"],
                               capture_output=True, text=True, env=dict(os.environ, **{H.ENV: p}))
            self.assertEqual(r.returncode, 2)
            self.assertIn("hosts.py: the host table", r.stderr)

    def test_load_rereads_a_changed_file(self):
        alt = AltTable(lambda t: None)
        try:
            self.assertEqual(H.known(H.load(alt.path)), H.known())
            enable_czerny(alt.t)
            with open(alt.path, "w") as f:
                json.dump(alt.t, f, indent=2)   # another size, so another key whatever the clock
            self.assertEqual(H.known(H.load(alt.path))[-2:], ("czerny", "bigfoot"))
            with mock.patch.dict(os.environ, alt.env()):
                self.assertEqual(H.path(), alt.path)
                self.assertIn("czerny", H.known())
        finally:
            alt.close()

    def test_command_line(self):
        py = os.path.join(T2, "t2lib", "hosts.py")

        def run(*args):
            e = dict(os.environ)
            e.pop(H.ENV, None)
            r = subprocess.run([sys.executable, "-I", "-B", py] + list(args), capture_output=True, text=True, env=e)
            return r.returncode, r.stdout, r.stderr
        self.assertEqual(run("names"), (0, "chopin\ncinqcent\ncopland\ncage\nbigfoot\n", ""))
        self.assertEqual(run("names", "--default"), (0, "chopin\ncinqcent\n", ""))
        self.assertEqual(run("names", "--all")[1].split(), ["chopin", "cinqcent", "copland", "cage", "czerny", "bigfoot"])
        self.assertEqual(run("names", "--role", "fleet", "--field", "ssh"), (0, "m@chopin.local\n", ""))
        self.assertEqual(run("names", "--class", "small", "--field", "slot_range"),
                         (0, "2800-2899\n3000-3099\n3200-3299\n", ""))
        self.assertEqual(run("get", "copland.local", "slot_range"), (0, "2800-2899\n", ""))
        self.assertEqual(run("get", "chopin", "roles"), (0, "build fleet store-mirror bigfoot-link\n", ""))
        self.assertEqual(run("get", "chopin", "ip"), (0, "\n", ""))
        self.assertEqual(run("get", "chopin", "default"), (0, "true\n", ""))
        self.assertEqual(run("get", "czerny", "slot_range", "--all"), (0, "3100-3199\n", ""))
        rc, out, err = run("get", "czerny", "slot_range")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("czerny is not enabled", err)
        rc, out, err = run("get", "nope", "ssh")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("no such host nope (known: chopin, cinqcent, copland, cage, bigfoot)", err)
        self.assertEqual(run("get", "chopin", "bogus")[0], 2)
        self.assertEqual(run("names", "--role")[0], 2)
        self.assertEqual(run("frob")[0], 2)
        self.assertEqual(run("phrase"), (0, "chopin, cinqcent, copland, cage or bigfoot\n", ""))
        self.assertEqual(run("check"), (0, TABLE + "\n", ""))


class DerivedEqualsBefore(unittest.TestCase):
    """What each consumer derives from the table is what it had as constants before."""

    def test_t2lib(self):
        self.assertEqual(t2lib.KNOWN_HOSTS, KNOWN)
        self.assertEqual(t2lib.DEFAULT_HOSTS, ("chopin", "cinqcent"))
        self.assertEqual(t2lib.gate_hosts(), ("chopin", "cinqcent"))
        self.assertEqual(t2lib.host_class("cage"), "small")

    def test_plan_gate_fallback(self):
        self.assertEqual(plan.GATE_FALLBACK, BEFORE_GATE_FALLBACK)

    def test_drive(self):
        drive = load("ht_drive", os.path.join(TESTVM, "stictest1", "drive.py"), {H.ENV: TABLE})
        self.assertEqual(drive.HOSTS, dict(BEFORE_DRIVE, bigfoot=BIGFOOT_DRIVE))
        self.assertEqual(list(drive.HOSTS), list(KNOWN))
        self.assertEqual(drive.DEFAULT_HOSTS, ("chopin", "cinqcent"))

    def test_vm_update(self):
        vmu = load("ht_vm_update", os.path.join(TESTVM, "vm-update"), {H.ENV: TABLE})
        self.assertEqual(vmu.ALL_HOSTS, ["chopin.local"])   # chopin alone since 2026-11-13: the fleet lock

    def test_bs2(self):
        bs2 = load("ht_bs2", os.path.join(TESTVM, "backshot2", "bs2"), {H.ENV: TABLE})
        # (Since 2026-11-13 a run's hosts are every enabled amd64 one; HOME's leads stay chopin and cinqcent.)
        self.assertEqual(bs2.RUN_HOSTS, {h: f"m@{h}.local" for h in ("chopin", "cinqcent", "copland", "cage")})
        self.assertEqual(list(dict.fromkeys(bs2.HOME.values())), ["chopin", "cinqcent"])

    def test_guest_slot(self):
        got = {h: bash('. "$1"; slot_range_here "$2"', GUEST_SLOT, h).stdout.strip()
               for h in ("chopin", "cinqcent", "copland", "cage", "chopin.local", "cinqcent.local", "bigfoot")}
        self.assertEqual(got, {"chopin": "2700-2799", "cinqcent": "2900-2999", "copland": "2800-2899",
                               "cage": "3000-3099", "chopin.local": "2700-2799", "cinqcent.local": "2900-2999",
                               "bigfoot": "3200-3299"})

    def test_guest_slot_refuses_an_unknown_host(self):
        for h in ("czerny", "chopin2", "cinqcent-old", "bigfoot2"):   # once: any of these got 2900-2999 (or chopin's)
            r = bash('. "$1"; slot_range_here "$2"', GUEST_SLOT, h)
            self.assertEqual((r.returncode, r.stdout), (1, ""), h)
            self.assertIn(f"guest-slot: no slot range for host {h}", r.stderr)
        r = bash('. "$1"; slot_take 3 "$(slot_range_here czerny)"; echo took', GUEST_SLOT, env={"BKO_SLOT_DIR": "/nonexistent/x"})
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("took", r.stdout)
        self.assertIn("guest-slot: slot_take: no port range ''", r.stderr)

    def test_layers_host_reserve_by_class(self):
        with open(os.path.join(T2, "gate", "layers.json")) as f:
            layers = json.load(f)["layers"]
        before = {"loop:linux": ([14, 4], [8.5, 4]), "loop:sparse": ([20, 4], [6, 4])}
        for n, (big, small) in before.items():
            self.assertEqual(set(layers[n]["host_reserve"]), {"small"})
            for h in ("chopin", "cinqcent"):
                self.assertEqual(gate.base_reserve(layers[n], f"gate:{n}@{h}"), big, (n, h))
            for h in ("copland", "cage"):
                self.assertEqual(gate.base_reserve(layers[n], f"gate:{n}@{h}"), small, (n, h))
            self.assertEqual(gate.base_reserve(layers[n], f"gate:{n}@czerny"), big)   # not a known host (yet)
            self.assertEqual(gate.base_reserve(layers[n], f"gate:{n}"), big)

    def test_host_reserve_reads_host_keys_first(self):
        e = {"reserve": [14, 4], "host_reserve": {"copland": [7, 4], "small": [8.5, 4]}}
        self.assertEqual(gate.base_reserve(e, "gate:loop:linux@copland"), [7, 4])   # its name beats its class
        self.assertEqual(gate.base_reserve(e, "gate:loop:linux@cage"), [8.5, 4])
        self.assertEqual(gate.base_reserve(e, "gate:loop:linux@chopin"), [14, 4])
        e = {"reserve": [14, 4], "host_reserve": {"copland": [8.5, 4], "cage": [8.5, 4]}}   # the old form
        self.assertEqual(gate.reserve_for(e, [], "gate:loop:linux@cage"), (8.5, 4.0))

    def run_all(self, host, env=None):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "work", "vms"))
            for o in ("linux", "dragonfly", "freebsd", "netbsd", "openbsd", "illumos"):   # every host's platforms
                os.makedirs(os.path.join(d, "bin", o))
                for t in ("restic", "rustic"):
                    with open(os.path.join(d, "bin", o, f"{t}-bko"), "w") as f:
                        f.write("x\n")
            e = {k: v for k, v in os.environ.items()
                 if k not in ("LINUX_FS", "SPARSE_FS", "PORT_BASE", "MAX_GUESTS", "COUNT_OTHERS", "GUEST_PORTS",
                              "ANY_HOST", "STEP_TIMEOUT", "HOST_JOBS", "SPARSE_JOBS", "JOBS_CAP", H.ENV)}
            e.update(BKO_WORK=os.path.join(d, "work"), OLD_BIN="none", UNCHECKED_BINARIES="1", **(env or {}))
            r = subprocess.run(["bash", os.path.join(TESTVM, "compare", "run-all.sh"), "--host", host, "--dry-run",
                                os.path.join(d, "bin"), os.path.join(d, "log")],
                               capture_output=True, text=True, env=e, timeout=120)
            head = [x for x in r.stdout.splitlines() if x.startswith(f"== {host.split('.')[0]}: ")]
            return r, head

    def test_run_all_profiles(self):
        want = {
            "chopin": "ports 2700+, at most 6 guests (others counted: 0, 2700-2799), 4 host jobs, 2 sparse jobs; prefix "
                      "bkora; OLD_BIN none; linux: zfs zfs-onto-ext4 bcachefs xfs btrfs nonroot; sparse: xfs btrfs",
            "cinqcent": "ports 2900+, at most 3 guests (others counted: 1, 2900-2999), 4 host jobs, 2 sparse jobs; prefix "
                        "bkora; OLD_BIN none; linux: ext4 f2fs tmpfs jfs; sparse: zfs zfs-nocomp ext4",
            "copland": "ports 2800+, at most 3 guests (others counted: 1, 2800-2899), 1 host jobs, 1 sparse jobs; prefix "
                       "bkora; OLD_BIN none; linux: ext4 xfs btrfs f2fs tmpfs jfs; sparse: ext4 xfs btrfs",
            "cage": "ports 3000+, at most 3 guests (others counted: 1, 3000-3099), 1 host jobs, 1 sparse jobs; prefix "
                    "bkora; OLD_BIN none; linux: ext4 xfs btrfs f2fs tmpfs jfs; sparse: ext4 xfs btrfs",
        }
        for h, line in want.items():
            for name in (h, h + ".local"):
                r, head = self.run_all(name)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(head, [f"== {h}: {line}"], name)
        r, head = self.run_all("chopin", {"PORT_BASE": "2750", "GUEST_PORTS": "2750-2759"})   # still overridable
        self.assertIn("ports 2750+, at most 6 guests (others counted: 0, 2750-2759)", head[0])

    def test_run_all_refuses_unknown_hosts(self):
        for h in ("czerny", "chopin2", "cinqcentx"):   # once: chopin* and cinqcent* matched by prefix
            r, _ = self.run_all(h)
            self.assertEqual(r.returncode, 2, h)
            self.assertIn(f"no such host: {h} (--host chopin, cinqcent, copland, cage or bigfoot)", r.stderr)
        r, _ = self.run_all("bigfoot")   # known, but an arm64 host: its guests and binaries are amd64
        self.assertEqual(r.returncode, 2)
        self.assertIn("bigfoot is an arm64 host (testvm/hosts.json's arch): run-all.sh's guests and binaries are amd64",
                      r.stderr)

    def sync_hosts(self, env=None):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "log")
            for tool in ("ssh", "rsync"):
                p = os.path.join(d, tool)
                with open(p, "w") as f:
                    f.write(f'#!/bin/sh\necho "{tool} $*" >> "{log}"\n')
                os.chmod(p, 0o755)
            e = dict(os.environ)
            e.pop(H.ENV, None)
            e.update(PATH=d + os.pathsep + e["PATH"], **(env or {}))
            r = subprocess.run(["bash", os.path.join(TESTVM, "sync-hosts.sh"), "--dry-run"], capture_output=True,
                               text=True, env=e, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with open(log) as f:
                return [x.split()[3] for x in f if x.startswith("ssh ")]

    def test_sync_hosts_default(self):
        self.assertEqual(self.sync_hosts(), ["m@chopin.local", "m@cinqcent.local", "m@copland.local", "m@cage.local",
                                             "m@bigfoot.local"])   # code only: the arm64 host gets it too


class FollowsTheTable(unittest.TestCase):
    """With czerny enabled (an alternative table), the consumers take it in."""

    def setUp(self):
        self.alt = AltTable(enable_czerny)

    def tearDown(self):
        self.alt.close()

    def test_python_consumers(self):
        drive = load("ht_drive_alt", os.path.join(TESTVM, "stictest1", "drive.py"), self.alt.env())
        self.assertEqual(drive.HOSTS["czerny"], dict(ssh="m@czerny.local", slot_range="3100-3199", ports=(3180, 3199)))
        self.assertEqual(drive.DEFAULT_HOSTS, ("chopin", "cinqcent"))
        self.assertEqual(H.known(H.load(self.alt.path)), ("chopin", "cinqcent", "copland", "cage", "czerny", "bigfoot"))
        with mock.patch.dict(os.environ, self.alt.env()):
            self.assertEqual(H.host_class("czerny"), "small")
            e = {"reserve": [14, 4], "host_reserve": {"small": [8.5, 4]}}
            self.assertEqual(gate.base_reserve(e, "gate:loop:linux@czerny"), [8.5, 4])

    def test_vm_update_fleet_follows_the_role(self):
        def two_fleets(t):
            row_of(t, "chopin")["roles"].remove("fleet")
            row_of(t, "cage")["roles"].append("fleet")
            row_of(t, "cinqcent")["roles"].append("fleet")
            t["hosts"].insert(0, t["hosts"].pop(3))   # cage first: it would hold the lock
        alt = AltTable(two_fleets)
        try:
            vmu = load("ht_vm_update_alt", os.path.join(TESTVM, "vm-update"), alt.env())
            self.assertEqual(vmu.ALL_HOSTS, ["cage.local", "cinqcent.local"])
        finally:
            alt.close()

    def test_shell_consumers(self):
        r = bash('. "$1"; slot_range_here czerny', GUEST_SLOT, env=self.alt.env())
        self.assertEqual((r.returncode, r.stdout), (0, "3100-3199\n"), r.stderr)
        r, head = DerivedEqualsBefore.run_all(self, "czerny", self.alt.env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(head, ["== czerny: ports 3100+, at most 3 guests (others counted: 1, 3100-3199), 1 host jobs, "
                                "1 sparse jobs; prefix bkora; OLD_BIN none; linux: ext4 xfs btrfs f2fs tmpfs jfs; "
                                "sparse: ext4 xfs btrfs"])
        self.assertEqual(DerivedEqualsBefore.sync_hosts(self, self.alt.env())[-2:], ["m@czerny.local", "m@bigfoot.local"])

    def test_run_all_big_host_without_a_profile(self):
        alt = AltTable(lambda t: (enable_czerny(t), row_of(t, "czerny").update({"class": "big"})))
        try:
            r, _ = DerivedEqualsBefore.run_all(self, "czerny", alt.env())
            self.assertEqual(r.returncode, 2)
            self.assertIn("no run-all.sh profile for czerny, a big host", r.stderr)
        finally:
            alt.close()

    def test_build_round_needs_build_hosts(self):
        def run(hosts, env):
            with tempfile.TemporaryDirectory() as d:
                e = dict(os.environ)
                e.pop(H.ENV, None)
                e.pop("CHOPIN", None)
                e.pop("CINQCENT", None)
                e.update(BKO_GIT=d, **env)
                return subprocess.run(["bash", os.path.join(REPO, "stick", "build-round.sh"), "--dry-run", "--hosts",
                                       hosts, "r1"], capture_output=True, text=True, env=e, timeout=60)
        r = run("both", {})
        self.assertEqual(r.returncode, 2)
        self.assertIn("no checkout", r.stderr)   # past the host checks, before anything is synced
        alt = AltTable(lambda t: row_of(t, "cinqcent")["roles"].remove("build"))
        try:
            r = run("both", alt.env())
            self.assertEqual(r.returncode, 2)
            self.assertIn("cinqcent is not a build host in testvm/hosts.json (build hosts: chopin)", r.stderr)
            r = run("chopin", alt.env())
            self.assertIn("no checkout", r.stderr)
        finally:
            alt.close()

    def test_t2_hosts_option_follows_the_table(self):
        with mock.patch.dict(os.environ, self.alt.env()):
            ns = H.known()
        self.assertIn("czerny", ns)
        with contextlib.redirect_stderr(io.StringIO()):
            t2 = load("t2cli_table", os.path.join(T2, "t2"))
        with mock.patch.object(t2, "KNOWN_HOSTS", ns):
            self.assertEqual(t2.hosts_opt({"--hosts": "czerny"}), ["czerny"])


if __name__ == "__main__":
    unittest.main()
