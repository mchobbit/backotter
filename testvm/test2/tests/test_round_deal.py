"""A round's build units are dealt over every build host, not split two ways by name
(testvm/hosts.json gave copland and cage the build role on 2026-11-14): stick/deal-units.py's
rule, stick/build-round.sh over four hosts (each host's --units, the gather to one collector,
the round on every build host with the same SHA256SUMS), every --hosts value dealt with the old
fixed split left to --split legacy, and facts_host.py's reading of build-hosts and of the
platforms a whole round holds.

The shell runs against test_round_dist's fake hosts (ssh runs the command here, in a directory
that is the host's home), with BKO_CAPS instead of the read-only capability probe: no host
needed, and nothing is built."""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
TESTVM = os.path.dirname(T2)
REPO = os.path.dirname(TESTVM)
sys.path.insert(0, T2)
sys.path.insert(0, HERE)

from t2lib import facts_host  # noqa: E402
import test_arm64_builds as AB  # noqa: E402
import test_round_dist as RD  # noqa: E402

BUILD_ROUND = RD.BUILD_ROUND
DEAL = os.path.join(REPO, "stick", "deal-units.py")
AMD64_UNITS = ("restic", "linux", "dragonfly", "haiku", "freebsd", "netbsd", "openbsd", "illumos")
ARM64_UNITS = ("restic-arm64", "restic-arm64-check", "linux-arm64-check")


def deal_mod():
    """stick/deal-units.py as a module (its name has a hyphen)."""
    spec = importlib.util.spec_from_file_location("deal_units", DEAL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


D = deal_mod()


def hosts(*spec):
    """Host objects from ("NAME CLASS cap,cap", ...)."""
    out = []
    for s in spec:
        name, cls, caps = (s.split(" ") + ["", ""])[:3]
        out.append(D.Host(name, cls, [c for c in caps.split(",") if c]))
    return out


FULL = RD.ALL_CAPS   # every capability a build host can have


def four(**less):
    """The four build hosts, fully capable unless a keyword gives fewer caps."""
    return hosts(*[f"{n} {c} {less.get(n, FULL)}" for n, c in
                   (("chopin", "big"), ("cinqcent", "big"), ("copland", "small"), ("cage", "small"))])


def dealt(hs):
    return {h.name: h.names() for h in hs}


class DealRule(unittest.TestCase):
    """stick/deal-units.py: longest first, each unit to the host whose estimated wall time ends
    up smallest, ties by the table's order; a unit only where its requirement is met; a check
    never on the host that builds what it checks."""

    def test_every_unit_is_dealt_once_and_each_host_gets_work(self):
        hs, skipped = D.deal(four())
        got = dealt(hs)
        self.assertEqual(sorted(u for us in got.values() for u in us), sorted(AMD64_UNITS))
        self.assertEqual(skipped, [])
        for name, us in got.items():
            self.assertTrue(us, f"{name} got nothing: {got}")

    def test_the_deal_is_the_same_every_time(self):
        self.assertEqual(dealt(D.deal(four())[0]), dealt(D.deal(four())[0]))

    def test_a_small_host_takes_one_guest_build_at_a_time(self):
        """Its class's lanes: two units at once, one of them a native guest build.  So three
        guest builds on a small host are three in a row, and the deal avoids that."""
        small = D.Host("cage", "small", FULL.split(","))
        big = D.Host("chopin", "big", FULL.split(","))
        self.assertEqual((small.cpu_lanes, small.guest_lanes), (2, 1))
        self.assertEqual((big.cpu_lanes, big.guest_lanes), (3, 2))
        guests = [u for u in D.UNITS if u[3] == "guest" and u[6] == "amd64"]
        for u in guests[:2]:
            small.units.append(u)
            big.units.append(u)
        self.assertGreater(small.minutes(), big.minutes())   # one lane against two

    def test_a_unit_stays_where_its_requirement_is_met(self):
        # only cage has DragonFly's nightly and sysroot; only chopin has haiku6
        hs, _ = D.deal(four(chopin="go,musl,haiku,vm-freebsd,vm-netbsd,vm-openbsd,vm-illumos",
                            cinqcent="go,musl,vm-freebsd,vm-netbsd,vm-openbsd,vm-illumos",
                            copland="go,musl,vm-freebsd,vm-netbsd,vm-openbsd,vm-illumos",
                            cage="go,musl,dragonfly,vm-freebsd,vm-netbsd,vm-openbsd,vm-illumos"))
        got = dealt(hs)
        self.assertIn("dragonfly", got["cage"])
        self.assertIn("haiku", got["chopin"])

    def test_a_shipped_unit_no_host_can_build_is_an_error(self):
        with self.assertRaisesRegex(D.DealError, r"no build host can build openbsd: .*cage: no vm-openbsd"):
            D.deal(four(chopin="go,musl,dragonfly,haiku", cinqcent="go,musl", copland="go,musl", cage="go,musl"))

    def test_a_check_goes_to_another_host(self):
        hs, skipped = D.deal(four(), arch=("amd64", "arm64"))
        got = dealt(hs)
        self.assertEqual(skipped, [])
        ships = [n for n, us in got.items() if "restic-arm64" in us]
        checks = [n for n, us in got.items() if "restic-arm64-check" in us]
        self.assertEqual((len(ships), len(checks)), (1, 1))
        self.assertNotEqual(ships[0], checks[0])

    def test_a_check_with_no_other_host_is_left_out_and_said(self):
        hs, skipped = D.deal(hosts(f"chopin big {FULL}"), arch=("arm64",))
        self.assertIn("restic-arm64", hs[0].names())
        self.assertEqual([n for n, _ in skipped], ["restic-arm64-check"])
        self.assertIn("it builds restic-arm64 itself", skipped[0][1])

    def test_the_arm64_cross_check_is_a_check_too(self):
        """linux-arm64-check checks the arm64 host's own rustic-bko, which no build host is
        dealt: a host without zig leaves it out and says so, and the round (the arm64 host's
        build and a build host's restic-bko) is still dealt, never refused."""
        self.assertEqual([u.checks for u in D.UNITS if u.name == "linux-arm64-check"], ["linux-arm64"])
        hs, skipped = D.deal(hosts("cinqcent big go,musl"), arch=("arm64",))
        self.assertEqual(hs[0].names(), ["restic-arm64"])
        self.assertEqual(sorted(n for n, _ in skipped), ["linux-arm64-check", "restic-arm64-check"])
        why = dict(skipped)["linux-arm64-check"]
        self.assertIn("no other host can build it", why)
        self.assertIn("cinqcent: no zig and cargo-zigbuild", why)
        # with zig it is dealt, and never to a host that builds what it checks (no host does)
        hs, skipped = D.deal(hosts(f"cinqcent big {FULL}"), arch=("arm64",))
        self.assertIn("linux-arm64-check", hs[0].names())

    def test_only_names_components(self):
        hs, _ = D.deal(four(), arch=("amd64", "arm64"), only=["restic", "openbsd"])
        got = sorted(u for us in dealt(hs).values() for u in us)
        self.assertEqual(got, ["openbsd", "restic", "restic-arm64", "restic-arm64-check"])

    def test_legacy_is_the_old_two_way_split(self):
        hs, skipped = D.deal(hosts("chopin big", "cinqcent big"), arch=("amd64", "arm64"), legacy=True)
        self.assertEqual(dealt(hs), {
            "chopin": ["restic", "linux", "dragonfly", "haiku", "restic-arm64", "linux-arm64-check"],
            "cinqcent": ["freebsd", "netbsd", "openbsd", "illumos", "restic-arm64-check"]})
        self.assertEqual(skipped, [])
        # --hosts chopin alone: Cinqcent's share is left out and said, as it always was
        hs, skipped = D.deal(hosts("chopin big"), legacy=True)
        self.assertEqual(dealt(hs), {"chopin": ["restic", "linux", "dragonfly", "haiku"]})
        self.assertEqual([n for n, _ in skipped], ["freebsd", "netbsd", "openbsd", "illumos"])
        self.assertIn("the legacy split builds it on cinqcent", skipped[0][1])
        with self.assertRaisesRegex(D.DealError, "--legacy is the split of chopin and cinqcent alone, not cage"):
            D.deal(hosts("cage small"), legacy=True)

    def test_the_command_line(self):
        def run(*args):
            return subprocess.run([sys.executable, "-I", "-B", DEAL] + list(args),
                                  capture_output=True, text=True)
        r = run("deal", f"chopin=big={FULL}", f"cage=small={FULL}")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = dict(l.split("\t") for l in r.stdout.splitlines())
        self.assertEqual(sorted(u for us in rows.values() for u in us.split(",")), sorted(AMD64_UNITS))
        r = run("table", f"chopin=big={FULL}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("The round's build units, dealt over the build hosts", r.stdout)
        self.assertIn("chopin  big    3/2", r.stdout)
        r = run("units", "--arch", "all")
        self.assertEqual([l.split("\t")[0] for l in r.stdout.splitlines()], list(AMD64_UNITS) + list(ARM64_UNITS))
        for args, msg in ((["deal"], "no build host"),
                          (["deal", "chopin"], "a host is NAME=CLASS=CAPS"),
                          (["deal", "--only", "bsd", f"chopin=big={FULL}"], "--only: of restic linux"),
                          (["deal", "--arch", "sparc", f"chopin=big={FULL}"], "--arch: amd64, arm64"),
                          (["what"], "usage: deal-units.py")):
            r = run(*args)
            self.assertEqual(r.returncode, 2, (args, r.stdout))
            self.assertIn(msg, r.stderr, args)


@unittest.skipUnless(RD.REAL_RSYNC, "no rsync here")
class BuildRoundFourHosts(unittest.TestCase):
    """build-round.sh over four build hosts: the deal printed, each host sent its own --units,
    every builder's files gathered to the collector, and the whole round on every build host
    with the same SHA256SUMS."""

    def setUp(self):
        self.w = RD.World()
        self.addCleanup(self.w.close)
        self.env = RD.BuildRoundDist.git_trees(self)   # (it uses self.w alone)
        self.b = "backotter-work/roundr3/bin"

    def br(self, *args, env=None):
        return self.w.run([BUILD_ROUND] + list(args), dict(self.env, **(env or {})))

    def files_of(self, host):
        t = self.w.read(host, f"{self.b}/build-{host}.files") or ""
        return sorted(l for l in t.split("\n") if l.strip())

    def test_all_four_build_and_all_four_end_with_the_round(self):
        r = self.br("--hosts", "all", "r3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("The round's build units, dealt over the build hosts", r.stdout)
        # each host built its own share, and no unit twice
        shares = {h: self.files_of(h) for h in ("chopin", "cinqcent", "copland", "cage")}
        every = [f for fs in shares.values() for f in fs]
        self.assertEqual(len(every), len(set(every)))
        self.assertEqual(len(every), len(AMD64_UNITS))
        for h, fs in shares.items():
            self.assertTrue(fs, f"{h} built nothing: {shares}")
        # the collector holds the whole round, and so does every other build host
        self.assertEqual(self.w.read("chopin", f"{self.b}/build-hosts"), "chopin\ncinqcent\ncopland\ncage\n")
        sums = self.w.read("chopin", f"{self.b}/SHA256SUMS")
        self.assertEqual(sorted(l.split("  ")[1] for l in sums.splitlines()),
                         sorted("./" + f for f in every))
        for h in ("cinqcent", "copland", "cage"):
            self.assertEqual(self.w.read(h, f"{self.b}/SHA256SUMS"), sums, h)
            self.assertEqual(self.w.files(h, self.b), self.w.files("chopin", self.b), h)
            self.assertIn(f"{h}: {self.b} copied from chopin", r.stdout)

    def test_the_default_is_every_build_host(self):
        r = self.br("r3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for h in ("chopin", "cinqcent", "copland", "cage"):
            self.assertTrue(self.files_of(h), h)

    def whole_round(self):
        """Every unit's file, as the deal over any hosts must leave between them."""
        return sorted(["linux/restic-bko"] + [f"{u}/rustic-bko" for u in AMD64_UNITS if u != "restic"])

    OLD_CHOPIN = ["dragonfly/rustic-bko", "haiku/rustic-bko", "linux/restic-bko", "linux/rustic-bko"]
    OLD_CINQCENT = ["freebsd/rustic-bko", "illumos/rustic-bko", "netbsd/rustic-bko", "openbsd/rustic-bko"]

    def test_hosts_both_deals_over_the_two(self):
        """both is chopin,cinqcent, and like any other --hosts value it is now dealt: the two
        share the whole round, and Chopin's share is not its old fixed one."""
        r = self.br("--hosts", "both", "r3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        both = {h: self.files_of(h) for h in ("chopin", "cinqcent")}
        self.assertEqual(sorted(f for fs in both.values() for f in fs), self.whole_round())
        self.assertNotEqual(both["chopin"], self.OLD_CHOPIN)
        self.assertFalse(os.path.exists(os.path.join(self.w.home("cage"), "backotter-work")))

    def test_hosts_chopin_deals_the_whole_round_to_chopin(self):
        """One host named is one host dealt to: it builds every unit, and nothing is left out
        (the old --hosts chopin built Chopin's half of the fixed split: --split legacy now)."""
        r = self.br("--hosts", "chopin", "r3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.files_of("chopin"), self.whole_round())
        self.assertNotIn("not built:", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.w.home("cinqcent"), "backotter-work")))

    def test_a_comma_list_of_two_is_dealt_not_split(self):
        """--hosts chopin,cinqcent is the dealing over those two, not the old split: each gets
        units of both kinds."""
        r = self.br("--hosts", "chopin,cinqcent", "r3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        both = {h: self.files_of(h) for h in ("chopin", "cinqcent")}
        self.assertEqual(sorted(f for fs in both.values() for f in fs), self.whole_round())
        self.assertNotEqual(both["chopin"], self.OLD_CHOPIN)

    def test_split_legacy_is_the_old_two_way_split(self):
        """--split legacy, without --hosts, is what every round was built with before
        2026-11-14: Chopin's four and Cinqcent's four, the other build hosts untouched."""
        r = self.br("--split", "legacy", "r3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.files_of("chopin"), self.OLD_CHOPIN)
        self.assertEqual(self.files_of("cinqcent"), self.OLD_CINQCENT)
        for h in ("copland", "cage"):
            self.assertFalse(os.path.exists(os.path.join(self.w.home(h), "backotter-work")), h)

    def test_split_legacy_with_hosts_chopin_builds_chopins_old_share(self):
        r = self.br("--hosts", "chopin", "--split", "legacy", "r3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.files_of("chopin"), self.OLD_CHOPIN)
        self.assertIn("not built: freebsd: the legacy split builds it on cinqcent", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.w.home("cinqcent"), "backotter-work")))

    def test_split_legacy_takes_chopin_and_cinqcent_alone(self):
        r = self.br("--hosts", "all", "--split", "legacy", "r3")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("--split legacy is the old split of chopin and cinqcent alone, not copland",
                      r.stderr)
        self.assertEqual([x for x in self.w.log() if "--host" in x], [])   # no host part started
        r = self.br("--split", "sideways", "r3")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("--split: deal or legacy, not sideways", r.stderr)

    def test_a_unit_no_host_can_build_refuses_the_round(self):
        r = self.br("--hosts", "all", "r3", env={"BKO_CAPS": RD.caps(
            chopin="go,musl", cinqcent="go,musl", copland="go,musl", cage="go,musl")})
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("no build host can build haiku", r.stderr)
        self.assertEqual([x for x in self.w.log() if "--host" in x], [])   # no host part started

    def test_the_gather_goes_through_the_mac_when_hosts_cant_reach_each_other(self):
        self.w.flag("noh2h")
        r = self.br("--hosts", "chopin,cage", "r3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("cage: chopin can't copy from m@cage.local: through the Mac", r.stdout)
        self.assertEqual(self.w.files("cage", self.b), self.w.files("chopin", self.b))

    def test_a_dry_run_builds_nothing(self):
        r = self.br("--dry-run", "--hosts", "all", "r3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("The round's build units, dealt over the build hosts", r.stdout)
        for h in ("chopin", "cinqcent", "copland", "cage"):
            self.assertIsNone(self.w.read(h, f"{self.b}/build-{h}.files"), h)
            self.assertIsNone(self.w.read(h, f"{self.b}/build-hosts"), h)


@unittest.skipUnless(RD.REAL_RSYNC, "no rsync here")
class BuildRoundArm64Dealt(unittest.TestCase):
    """--arch with the units dealt (what every --hosts does without --split legacy): the part
    each build host is given, the arm64 host's own unit, and --only.  Dry runs: the deal and
    the host parts are what is looked at, and nothing is built (tests/test_arm64_builds.py
    has the gather, the cross-checks and the copy back with the legacy split)."""

    def setUp(self):
        self.w = RD.World()
        self.addCleanup(self.w.close)
        self.env = RD.BuildRoundDist.git_trees(self)
        self.w.put("bigfoot", "w/stick/build-round.sh", RD.FAKE_PART, 0o755)
        # the arm64 host's part runs in its job_slice: sudo systemd-run, stood in for
        self.w.put("bigfoot", "bin/sudo", AB.FAKE_SUDO, 0o755)

    def br(self, *args, env=None):
        return self.w.run([BUILD_ROUND, "--dry-run"] + list(args), dict(self.env, **(env or {})))

    def parts(self):
        """{host: its --units}, from the host parts the Mac started (the ssh log)."""
        out = {}
        for l in self.w.log():
            m = re.search(r"^ssh (\S+) .*--host (\S+) --units (\S+)", l)
            if m:
                out[m.group(2)] = m.group(3)
        return out

    def test_the_arm64_host_builds_its_own_unit_and_the_build_hosts_the_rest(self):
        r = self.br("--hosts", "all", "--arch", "arm64", "r3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        p = self.parts()
        self.assertEqual(p.get("bigfoot"), "linux-arm64")
        dealt = sorted(u for h, us in p.items() if h != "bigfoot" for u in us.split(","))
        self.assertEqual(dealt, sorted(ARM64_UNITS))

    def test_only_restic_leaves_the_arm64_host_alone(self):
        """--only restic asks for restic-bko linux-arm64 and its check, not the arm64 host's
        rustic-bko (the linux component): no part is started there at all."""
        r = self.br("--hosts", "all", "--arch", "arm64", "--only", "restic", "r3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        p = self.parts()
        self.assertNotIn("bigfoot", p)
        self.assertEqual(sorted(u for us in p.values() for u in us.split(",")),
                         ["restic-arm64", "restic-arm64-check"])
        self.assertFalse(any("bigfoot" in l for l in self.w.log()), self.w.log())

    def test_only_linux_builds_the_arm64_host_and_asks_for_no_restic(self):
        """--only linux is the arm64 rustic-bko and its cross-build check: the arm64 host's
        part runs, and the round isn't refused for want of the restic-arm64 unit."""
        r = self.br("--hosts", "all", "--arch", "arm64", "--only", "linux", "r3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        p = self.parts()
        self.assertEqual(p.get("bigfoot"), "linux-arm64")
        self.assertEqual(sorted(u for h, us in p.items() if h != "bigfoot" for u in us.split(",")),
                         ["linux-arm64-check"])

    def test_no_restic_arm64_anywhere_refuses(self):
        """No build host with Go: restic-bko linux-arm64 is a shipped unit, so the deal
        refuses the round (the arm64 host's own build is no round on its own) and no part
        starts anywhere, the arm64 host's included."""
        r = self.br("--hosts", "all", "--arch", "arm64", "r3",
                    env={"BKO_CAPS": RD.caps(chopin="zig,arm64-musl", cinqcent="", copland="", cage="")})
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("no build host can build restic-arm64: chopin: no Go", r.stderr)
        self.assertEqual(self.parts(), {})

    def test_a_cross_check_no_host_can_build_is_left_out_not_refused(self):
        """One build host, without zig: its restic-bko linux-arm64 is built, both checks are
        left out and said, and the arm64 host still builds its own."""
        r = self.br("--hosts", "cinqcent", "--arch", "arm64", "r3",
                    env={"BKO_CAPS": RD.caps(cinqcent="go,musl")})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.parts(), {"cinqcent": "restic-arm64", "bigfoot": "linux-arm64"})
        self.assertIn("not built: linux-arm64-check: no other host can build it", r.stdout)
        self.assertIn("not built: restic-arm64-check:", r.stdout)


class GuestSlots(unittest.TestCase):
    """A native guest build takes one of its host's own guest slots (the header: BKO_GUEST_MAX
    on BKO_GUEST_RANGE, the host's slot_range).  The deal puts those builds on any build host,
    each with a slot_range of its own, so a fixed range would take the wrong host's slots and
    let a fourth guest run beside the three run-all.sh allows."""

    def test_a_build_guest_takes_a_slot_on_the_hosts_own_range(self):
        with open(BUILD_ROUND, encoding="utf-8") as f:
            code = f.read()
        calls = re.findall(r"slot_take .*", code)
        self.assertTrue(calls)
        for c in calls:
            self.assertEqual(c, 'slot_take "${BKO_GUEST_MAX:-3}" "$grange"', c)
        body = re.sub(r"(?m)^\s*#.*$", "", code)
        self.assertNotRegex(body, r"\b[23]\d00-[23]\d99\b")   # no host's range by name
        self.assertIn('grange=${BKO_GUEST_RANGE:-$(hosts_get "$mode" slot_range)}', body)



if __name__ == "__main__":
    unittest.main()
