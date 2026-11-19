"""GuestTest2's network rule (guest.NetHold): a farm whose internet crawls starts no guest update.

GuestTest2 t2v-20261119T0157-c8c7: the internet ran at 5 to 100 KiB/s for hours (20 % ping
loss, 0.4 to 1.5 s RTT), and ten try jobs (microos, void-zbm, endeavouros-grub ...) each
downloaded for the harness's whole 5400 s, attempts 2 included."""

import os
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import test_guest as TG  # noqa: E402
from t2lib import guest  # noqa: E402

setUpModule, tearDownModule = TG.setUpModule, TG.tearDownModule
KIB, MIB = 1024, 1024 * 1024


class Clock:
    def __init__(self, t=1000.0):
        self.t = t
        self.mu = threading.Lock()

    def __call__(self):
        with self.mu:
            return self.t

    def add(self, s):
        with self.mu:
            self.t += s


class Probe:
    """A probe answering RATES[url] (bytes/s or None; a callable is asked), and the calls."""

    def __init__(self, rate=None, **by_url):
        self.rate, self.by_url, self.calls = rate, by_url, []

    def __call__(self, url, nbytes, secs):
        self.calls.append((url, nbytes, secs))
        r = self.by_url.get(url, self.rate)
        return r() if callable(r) else r


URLS = ["https://a.example/x", "https://b.example/y"]


def hold(clock, **policy):
    return guest.NetHold(dict({"net_probe_urls": URLS}, **policy), clock)


class Parse(unittest.TestCase):
    def test_the_line(self):
        self.assertEqual(guest.parse_net_probe("t2-net 78231 1173547 206\n"), 78231.0)
        self.assertEqual(guest.parse_net_probe("noise\nt2-net 4096.5 2097152 200"), 4096.5)

    def test_nothing_fetched_is_zero(self):
        """A fetch that never connected (curl 28, code 000) or a refusal is the internet at 0."""
        self.assertEqual(guest.parse_net_probe("t2-net 0 0 000"), 0.0)
        self.assertEqual(guest.parse_net_probe("t2-net 900000 512 404"), 0.0)

    def test_no_line_is_unknown(self):
        for t in ("", None, "bash: curl: command not found", "t2-net fast 1 200"):
            self.assertIsNone(guest.parse_net_probe(t), t)

    def test_command(self):
        c = guest.net_probe_cmd("https://h/p?a=1&b", 2 * MIB, 15)
        self.assertIn("-sSL -o /dev/null -r 0-2097151 --max-time 15", c)   # -L: a mirror that redirects
        self.assertIn("'https://h/p?a=1&b'", c)
        self.assertIn("%{speed_download} %{size_download} %{http_code}", c)
        self.assertTrue(c.endswith("; true"))

    def test_command_runs_in_a_shell(self):
        """The line as a host's shell runs it: curl's -w line comes out, and a failing curl is no failure."""
        import subprocess
        c = guest.net_probe_cmd("https://h/p", 10, 1)
        for body, want in (('printf "t2-net 1234.5 10 206\\n"', 1234.5), ('printf "t2-net 0 0 000\\n"; return 28', 0.0)):
            r = subprocess.run(["bash", "-c", f"curl() {{ {body}; }}\n" + c], capture_output=True, text=True)
            self.assertEqual((r.returncode, guest.parse_net_probe(r.stdout)), (0, want), r)


class Rule(unittest.TestCase):
    def test_slow_holds_and_fast_clears_after_the_minimum(self):
        c = Clock()
        n = hold(c)
        p = Probe(50 * KIB)
        n.check(p)
        self.assertRegex(n.held() or "", r"the internet ran at 50 KiB/s \(a\.example\), under 256")
        self.assertEqual(len(p.calls), 2)          # both URLs tried, the best counts
        c.add(5 * 60)
        p.rate = 8 * MIB
        n.check(p)
        self.assertIsNotNone(n.held())             # 5 min held: under net_hold_min
        c.add(5 * 60)
        n.check(p)
        self.assertIsNone(n.held())
        self.assertIn("started again after 10 min held", n.notes()[-1])

    def test_between_the_thresholds_stays_as_it_is(self):
        """The hysteresis: 512 KiB/s neither holds a free farm nor frees a held one."""
        c = Clock()
        n = hold(c)
        p = Probe(512 * KIB)
        n.check(p)
        self.assertIsNone(n.held())
        p.rate = 10 * KIB
        c.add(300)
        n.check(p)
        self.assertIsNotNone(n.held())
        p.rate = 512 * KIB
        for _ in range(6):
            c.add(300)
            n.check(p)
        self.assertIsNotNone(n.held())

    def test_rate_limited(self):
        c = Clock()
        n = hold(c)
        p = Probe(8 * MIB)
        n.check(p)
        n.check(p)
        c.add(299)
        n.check(p)
        self.assertEqual(len(p.calls), 1)          # fast at once: the second URL isn't read
        c.add(1)
        n.check(p)
        self.assertEqual(len(p.calls), 2)

    def test_best_url_counts(self):
        """One mirror's trouble isn't the internet's: the other URL's rate frees it."""
        n = hold(Clock())
        p = Probe(**{URLS[0]: 0.0, URLS[1]: 3 * MIB})
        n.check(p)
        self.assertIsNone(n.held())

    def test_unknown_changes_nothing(self):
        c = Clock()
        n = hold(c)
        n.check(Probe(None))
        self.assertIsNone(n.held())
        n2 = hold(c)
        n2.check(Probe(1.0))
        c.add(3600)
        n2.check(Probe(None))
        self.assertIsNotNone(n2.held())

    def test_gives_up_after_the_maximum(self):
        c = Clock()
        n = hold(c, net_hold_max_min=30)
        p = Probe(20 * KIB)
        for _ in range(6):
            n.check(p)
            c.add(300)
        self.assertIsNone(n.gave_up())
        n.check(p)
        self.assertRegex(n.gave_up() or "", r"under 1024 KiB/s for 30 min .*weren't run")
        calls = len(p.calls)
        c.add(300)
        n.check(p)
        self.assertEqual(len(p.calls), calls)       # given up: no more probes

    def test_off(self):
        n = hold(Clock(), net_min_kibs=0)
        p = Probe(0.0)
        n.check(p)
        self.assertEqual((n.held(), p.calls), (None, []))

    def test_policy_keys(self):
        n = guest.NetHold({"net_min_kibs": 100, "ram_gib_max": 16, "net_probe_urls": ["u"]}, Clock())
        self.assertEqual((n.min_b, n.urls, n.clear_b), (100 * KIB, ["u"], 1024 * KIB))
        d = guest.NetHold(None, Clock())
        self.assertTrue(d.on)
        self.assertTrue(all(u.startswith("https://") for u in d.urls))


class Runner(unittest.TestCase):
    def wait_for(self, fn, t=5):
        t0 = time.time()
        while not fn():
            self.assertLess(time.time() - t0, t, "timed out")
            time.sleep(0.002)

    def test_held_farm_starts_no_update_until_the_internet_is_back(self):
        names = ["debian12", "arch"]
        jobs, io, c = TG.FakeJobs(), TG.FakeIO(), Clock()
        for g in names:
            io.summ[g] = TG.summary()
        p = Probe(40 * KIB)
        r, rs, _, _ = TG.make(names, io, jobs, clock=c, net_probe=p)
        th = threading.Thread(target=r.run, daemon=True)
        th.start()
        self.wait_for(lambda: p.calls)
        time.sleep(0.1)
        self.assertEqual(jobs.calls, [])
        self.assertIsNotNone(r.net.held())
        p.rate = 6 * MIB
        c.add(11 * 60)
        th.join(10)
        self.assertFalse(th.is_alive())
        self.assertEqual(sorted(x["guest"] for x in jobs.calls), sorted(names))
        out = r.result("s")
        self.assertTrue(any(n.startswith("network rule: held every guest update") for n in out["notes"]), out["notes"])
        self.assertTrue(any("started again" in n for n in out["notes"]), out["notes"])

    def test_console_boot_starts_while_held(self):
        """redox (console: no mirror read) runs; debian12 waits."""
        jobs, io, c = TG.FakeJobs(), TG.FakeIO(), Clock()
        for g in ("redox", "debian12"):
            io.summ[g] = TG.summary()
        p = Probe(1.0)
        r, rs, _, _ = TG.make(["redox", "debian12"], io, jobs, clock=c, net_probe=p)
        th = threading.Thread(target=r.run, daemon=True)
        th.start()
        self.wait_for(lambda: jobs.calls)
        time.sleep(0.1)
        self.assertEqual([x["guest"] for x in jobs.calls], ["redox"])
        p.rate = 6 * MIB
        c.add(11 * 60)
        th.join(10)
        self.assertEqual(sorted(x["guest"] for x in jobs.calls), ["debian12", "redox"])

    def test_given_up_skips_the_waiting_updates(self):
        jobs, io, c = TG.FakeJobs(), TG.FakeIO(), Clock()
        io.summ["debian12"] = TG.summary()
        p = Probe(lambda: (c.add(300), 1.0)[1])   # each probe: 5 min on
        r, rs, _, _ = TG.make(["debian12"], io, jobs, clock=c, net_probe=p)
        r.net.max_s = 15 * 60
        th = threading.Thread(target=r.run, daemon=True)
        th.start()
        th.join(10)
        self.assertFalse(th.is_alive())
        self.assertEqual(jobs.calls, [])
        self.assertEqual([s["cell"] for s in r.skipped], ["guest:debian12"])
        self.assertIn("the internet ran under", r.skipped[0]["note"])

    def test_probe_asks_a_host_and_reads_its_line(self):
        class IO(TG.FakeIO):
            def sh(self, host, cmd, timeout=60, input=None):
                if cmd.startswith("curl "):
                    self.shell.append((host, cmd, input))
                    return (255, "", "no route") if host == "chopin" else (0, "t2-net 3000000 2097152 206\n", "")
                return super().sh(host, cmd, timeout, input)
        io = IO()
        r, rs, _, _ = TG.make(["debian12"], io, TG.FakeJobs())
        self.assertEqual(r.probe_net("https://x/y", 2 * MIB, 15), 3000000.0)
        self.assertEqual([h for h, _c, _i in io.shell], ["chopin", "cinqcent"])
        self.assertIsNone(TG.make(["debian12"], TG.FakeIO(), TG.FakeJobs())[0].probe_net("https://x/y", 1, 1))


if __name__ == "__main__":
    unittest.main()
