"""GuestTest2's log baselines on the hosts (guest.Runner.upload_baselines, c497b7b): one that
wasn't written on a host keeps the guest off that host when another may run it
(placed_by_baseline); one that ran there anyway says so in its cell (no_baseline) and a note,
as its new lines were judged as with no baseline.  The cell itself doesn't fail for it.

    cd testvm/test2/tests && python3 -B -m unittest test_guest_baselines
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import test_guest as tg  # noqa: E402


def setUpModule():
    tg.setUpModule()


def tearDownModule():
    tg.tearDownModule()


class HostIO(tg.FakeIO):
    """FakeIO whose writes of a baseline on BAD (a host) fail."""

    def __init__(self, bad):
        super().__init__()
        self.bad = bad

    def sh(self, host, cmd, timeout=60, input=None):
        if host in self.bad and "/baselines/" in cmd and "cat >" in cmd:
            return 1, "", "ssh: connect to host port 22: Connection reset\n"
        return super().sh(host, cmd, timeout=timeout, input=input)

    def sleep(self, s):
        pass


def make(bad, hosts=("chopin", "cinqcent")):
    io, jobs = HostIO(bad), tg.FakeJobs()
    io.summ["debian12"] = tg.summary()
    r, rs, _ctx, _ = tg.make(["debian12"], io, jobs, hosts=hosts)
    p = os.path.join(rs.mac_root, "guest-baselines", "debian12.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"lines": []}')
    return r, jobs


class Baselines(unittest.TestCase):
    def test_a_host_without_the_baseline_is_left_out(self):
        r, jobs = make({"chopin"})
        res = r.run()
        self.assertEqual([c["host"] for c in jobs.calls], ["cinqcent"])
        c = res["cells"][0]
        self.assertEqual((c["verdict"], c.get("no_baseline")), ("PASS", None))
        self.assertTrue(any("baseline not written on chopin" in n for n in res["notes"]), res["notes"])

    def test_one_that_ran_without_it_says_so_and_still_passes(self):
        r, jobs = make({"chopin"}, hosts=("chopin",))
        res = r.run()
        self.assertEqual([c["host"] for c in jobs.calls], ["chopin"])
        c = res["cells"][0]
        self.assertEqual((c["verdict"], c["no_baseline"]), ("PASS", True))
        self.assertTrue(any(n.startswith("guest:debian12: ran on chopin, where its baseline wasn't written")
                            for n in res["notes"]), res["notes"])
        self.assertEqual(res["exit"], 0)

    def test_every_host_written_changes_nothing(self):
        r, jobs = make(set())
        res = r.run()
        self.assertNotIn("no_baseline", res["cells"][0])
        self.assertFalse(any("baseline" in n for n in res["notes"]), res["notes"])


if __name__ == "__main__":
    unittest.main()
