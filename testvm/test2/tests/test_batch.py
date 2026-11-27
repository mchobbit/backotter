"""t2 batch, the pure parts (docs/design/batch.md §11.1; pieces B1 and the supervisor): the
parser, ids, settings, include, the refusal table, the lock matrix, pools, the resume table
(next_action), the beacon line.  No host, no child (the scheduler: tests/test_batchsched.py)."""

import copy
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
TESTVM = os.path.dirname(T2)
sys.path.insert(0, T2)

from t2lib import batch as B          # noqa: E402
from t2lib import batchfile as bf     # noqa: E402

with open(os.path.join(TESTVM, "hosts.json")) as _f:
    REAL = json.load(_f)


def table():
    """A fixture table: chopin, cinqcent (big), copland, cage (small), czerny (held back as a
    reserved row here, whatever the real table says, so the refusals cover one), bigfoot
    (arm64, not default), from the real table's rows."""
    t = copy.deepcopy(REAL)
    names = [r["name"] for r in t["hosts"]]
    for n in ("chopin", "cinqcent", "copland", "cage", "czerny", "bigfoot"):
        assert n in names, n
    next(r for r in t["hosts"] if r["name"] == "czerny")["enabled"] = False
    return t


def P(text, path="t.batch", files=()):
    return bf.parse(text, path, (lambda p: dict(files)[p]) if files is not None else None)


class Parse(unittest.TestCase):
    def test_kinds_ids_and_manual_numbering(self):
        b = P("# c\n\nmanual step 1: refresh\n"
              "t2 GateTest2All rel6 why=\"step 2\"\n"
              "t2 SweepTest2 rel6\nht1 HoltTest1All debian13 arch\nstic1 rel6 alpine324\n"
              "bs2 BackShot2Alpine 200\ncmd rebuild-img -- make -C x img\nmanual step 5\n")
        self.assertEqual([e.id for e in b.entries],
                         ["002-t2-gatetest2all-rel6", "003-t2-sweeptest2-rel6", "004-ht1-holttest1all-debian13-arch",
                          "005-stic1-rel6-alpine324", "006-bs2-backshot2alpine-200", "007-cmd-rebuild-img"])
        self.assertEqual([m["text"] for m in b.manual], ["step 1: refresh", "step 5"])
        self.assertEqual([m["pos"] for m in b.manual], [1, 8])
        self.assertEqual(b.entries[0].why, "step 2")
        self.assertEqual(b.entries[-1].cmd, ["make", "-C", "x", "img"])
        self.assertEqual(b.entries[-1].args, ["rebuild-img"])
        self.assertEqual(b.name, "t")

    def test_slug_cut_and_chars(self):
        b = P("t2 HoltTest2 " + " ".join(f"guest{i}+bigger" for i in range(10)) + "\n")
        e = b.entries[0]
        self.assertLessEqual(len(e.slug), 40)
        self.assertTrue(e.id.startswith("001-t2-holttest2-guest0+bigger"))
        self.assertRegex(e.slug, r"^[a-z0-9+._-]+$")

    def test_duplicates_refused(self):
        with self.assertRaises(bf.BatchError) as c:
            P("cmd x -- true\ncmd x -- false\n")
        self.assertIn("slugifies as", str(c.exception))
        b = P("cmd x -- true\ncmd x id=other -- false\n")
        self.assertEqual([e.id for e in b.entries], ["001-cmd-x", "other"])
        with self.assertRaises(bf.BatchError) as c:
            P("cmd x id=a -- true\ncmd y id=a -- false\n")
        self.assertIn("ids are unique", str(c.exception))

    def test_errors_collected(self):
        with self.assertRaises(bf.BatchError) as c:
            P("frob x\nset nope 1\nset mode fast\nt2 HoltTest2 colour=red\ncmd x\ncmd y -- \n"
              "t2 HoltTest2 est=soon\ncmd z rerun=maybe -- true\nt2 HoltTest2 rerun=yes\n"
              "t2 HoltTest2 retry=x\nserial\nt2 HoltTest2 id=a hosts= \nt2 GateTest2 id=b why=x extra\n")
        es = c.exception.errors
        for frag in ("t.batch:1: unknown kind 'frob'", "t.batch:2: no setting 'nope'", "t.batch:3: set mode",
                     "t.batch:4: unknown option colour=", "t.batch:5: cmd NAME -- COMMAND",
                     "t.batch:6: cmd y --: no COMMAND", "t.batch:7: est=", "t.batch:8: rerun=: yes or no",
                     "t.batch:9: rerun= goes with cmd", "t.batch:10: retry=", "t.batch:11: serial needs",
                     "t.batch:12: hosts= names no host", "t.batch:13: 'extra' after the options"):
            self.assertTrue(any(frag in x for x in es), (frag, es))

    def test_settings_and_defaults(self):
        b = P("set mode serial\nset round rel6\nset hosts copland,cage\nset retry 2\nset lock-wait 90m\n"
              "set patience 1h30m\nset log-fetch all\nset prev bt-20261101T0000-abcd\ncmd x -- true\n")
        s = b.settings
        self.assertEqual((s["mode"], s["round"], s["hosts"], s["retry"], s["lock-wait"], s["patience"],
                          s["log-fetch"], s["prev"]),
                         ("serial", "rel6", ["copland", "cage"], 2, 5400, 5400, "all", "bt-20261101T0000-abcd"))
        d = P("cmd x -- true\n").settings
        self.assertEqual((d["mode"], d["retry"], d["lock-wait"], d["patience"], d["log-fetch"]),
                         ("parallel", 0, 4 * 3600, 2 * 3600, "non-pass"))
        with self.assertRaises(bf.BatchError) as c:
            P("set retry 1\nset retry 2\n")
        self.assertIn("set retry twice", str(c.exception))

    def test_include_depth_and_precedence(self):
        files = {"d/a.batch": "set round rel5\nset retry 3\ncmd a -- true\ninclude sub/b.batch\n",
                 "d/sub/b.batch": "cmd b -- true\ninclude c.batch\n",
                 "d/sub/c.batch": "cmd c -- true\n",
                 "d/top.batch": "set round rel6\ninclude a.batch\ncmd t -- true\n"}
        with self.assertRaises(bf.BatchError) as c:
            bf.parse(files["d/top.batch"], "d/top.batch", lambda p: files[p])
        self.assertIn("nest at most 2", str(c.exception))
        files["d/sub/b.batch"] = "cmd b -- true\n"
        b = bf.parse(files["d/top.batch"], "d/top.batch", lambda p: files[p])
        self.assertEqual([e.id for e in b.entries], ["001-cmd-a", "002-cmd-b", "003-cmd-t"])
        self.assertEqual(b.settings["round"], "rel6")       # the including file's wins
        self.assertEqual(b.settings["retry"], 3)            # an include's own still counts
        self.assertEqual(sorted(b.sources), ["d/a.batch", "d/sub/b.batch", "d/top.batch"])
        with self.assertRaises(bf.BatchError) as c:
            bf.parse("include top.batch\n", "d/top.batch", lambda p: files[p])
        self.assertIn("includes itself", str(c.exception))
        with self.assertRaises(bf.BatchError) as c:
            bf.parse("include nothere.batch\n", "d/top.batch", lambda p: (_ for _ in ()).throw(OSError(2, "No such file")))
        self.assertIn("No such file", str(c.exception))
        with self.assertRaises(bf.BatchError):
            P("include x.batch\n", files=None)

    def test_serial_after_cycle(self):
        b = P("serial cmd a -- true\ncmd b after=001-cmd-a -- true\nt2 GuestTest2 update\n")
        a, bb, u = b.entries
        self.assertTrue(a.serial and a.is_serial)
        self.assertEqual(bb.after, ["001-cmd-a"])
        self.assertTrue(u.implied_serial and u.is_serial and not u.serial)
        with self.assertRaises(bf.BatchError) as c:
            P("cmd a id=a after=b -- true\ncmd b id=b after=a -- true\n")
        self.assertIn("cycle", str(c.exception))
        with self.assertRaises(bf.BatchError) as c:
            P("cmd a after=zzz -- true\n")
        self.assertIn("no entry has that id", str(c.exception))

    def test_durations(self):
        self.assertEqual([bf.parse_duration(x) for x in ("90m", "2h", "1h30m", "45s", "1d", "120")],
                         [5400, 7200, 5400, 45, 86400, 120])
        for x in ("", "soon", "1x", "h"):
            with self.assertRaises(ValueError):
                bf.parse_duration(x)
        self.assertEqual(bf.fmt_hm(22200), "6:10")

    def test_digest_and_diff(self):
        a = P("cmd a -- true\ncmd b -- true\n")
        b = P("cmd a -- true\ncmd b -- false\n")
        self.assertNotEqual(a.digest, b.digest)
        self.assertEqual(a.digest, P("cmd a -- true\ncmd b -- true\n").digest)
        self.assertIn("t.batch:2 was 'cmd b -- true', is 'cmd b -- false'", bf.diff_lines(a.lines, b.lines))
        self.assertIsNone(bf.diff_lines(a.lines, a.lines))

    def test_json_round_trip(self):
        b = P("set round rel6\nmanual m1\nserial t2 SweepTest2 rel6 est=2h\ncmd x hosts=cage rerun=yes -- true\nmanual m2\n")
        b2 = bf.Batch.from_json(json.loads(json.dumps(b.to_json())))
        self.assertEqual(b2.to_json(), b.to_json())
        self.assertTrue(b2.entries[0].same_as(b.entries[0]))


class Refusals(unittest.TestCase):
    def R(self, text, **kw):
        return bf.refusals(P(text), table(), **kw)

    def test_clean(self):
        self.assertEqual(self.R("t2 SweepTest2 rel6\ncmd x hosts=cage -- sudo -n true\n"), [])

    def test_hosts(self):
        r = self.R("set hosts chopin,bigfoot,czerny,nosuch\ncmd x hosts=bigfoot -- true\n")
        txt = "\n".join(r)
        self.assertIn("hosts names bigfoot: Bigfoot is the user's machine", txt)
        self.assertIn("hosts names czerny: a reserved row", txt)
        self.assertIn("hosts names nosuch: no such host", txt)
        self.assertIn("hosts= names bigfoot", txt)
        r = self.R("set hosts copland,cage\ncmd x hosts=chopin -- true\n")
        self.assertTrue(any("not among the batch's hosts" in x for x in r), r)
        self.assertEqual(self.R("cmd x hosts=chopin -- true\n", hosts=["chopin", "cage"]), [])

    def test_holt_build_needs_a_build_host(self):
        """bt-20261115T1132-e4ca: HoltTest2 on hosts with no build host stops 9 s in (holt.py's
        stop rule); the batch refuses it first, by the same predicate, unless it has --holt-bin.
        A small build host is one since img-speed.md F8 (2026-11-27): an all-small set builds
        (build-holt.sh puts its scratch on the SSD when the RAM disk has no room); choubox, with
        no build role, is not."""
        self.assertEqual(self.R("set hosts copland,cage\nt2 HoltTest2 alpine324\n"), [])     # (F8)
        self.assertEqual(self.R("t2 HoltTest2 alpine324 hosts=copland\n"), [])
        self.assertEqual(self.R("set hosts copland,cage\nt2 BkoTest2 rel6\n"), [])
        r = self.R("set hosts choubox\nt2 HoltTest2 alpine324\n")
        self.assertEqual(len(r), 1, r)
        for frag in ("001-t2-holttest2-alpine324", "on a build host (role build in testvm/hosts.json)",
                     "its hosts choubox have none", "add one (chopin, cinqcent, copland, cage)", "--holt-bin DIR"):
            self.assertIn(frag, r[0])
        r = self.R("set hosts copland,cage\nt2 HoltTest2 alpine324 hosts=choubox\n")
        self.assertTrue(any("hosts choubox have none" in x and "its hosts=" in x for x in r), r)
        self.assertEqual(self.R("set hosts choubox\nt2 HoltTest2 alpine324 --holt-bin=/x\n"), [])
        self.assertEqual(self.R("set hosts choubox,cinqcent\nt2 HoltTest2 alpine324\n"), [])
        # (+img cells alone run the image's own bko-holt: nothing to build)
        self.assertEqual(self.R("set hosts choubox\nt2 HoltTest2 +img\n"), [])
        self.assertEqual(self.R("set hosts choubox\nt2 HoltTest2 alpine324+img\n"), [])
        self.assertEqual(len(self.R("set hosts choubox\nt2 HoltTest2 alpine324 alpine324+img\n")), 1)
        r = self.R("set hosts choubox\nt2 BkoTest2 rel6\n")
        self.assertTrue(any("HoltTest2 only" in x for x in r), r)
        self.assertEqual(self.R("set hosts choubox\nht1 R1 debian13\nt2 GuestTest2\n"), [])
        self.assertEqual(bf.holt_builders(["copland", "cinqcent", "choubox", "cage", "chopin", "bigfoot"], table()),
                         ["copland", "cinqcent", "cage", "chopin"])
        self.assertEqual(bf.word_argv(["HoltTest2", "a", "--holt-bin", "/d", "b"]), ["HoltTest2", "a", "b"])

    def test_table_rows(self):
        cases = {
            "cmd bs1 -- testvm/realworld/backshot1/run.sh": "BackShot1 is real hardware",
            "bs2 BackShot2Alpine 200 --tier 3": "bs2 tier 3",
            "bs2 replay CASE --tier=3": "bs2 tier 3",
            "t2 GuestTest2 update": "fleet lock",
            "t2 guest-lines t2v-20261115T0000-abcd --accept neon24": "person's judgement",
            "t2 HoltTest2 --universe holt:alpine324": "--universe is test-only",
            "cmd x -- sudo true": "sudo without -n",
            "cmd x -- sh -c 'cat /dev/tty'": "/dev/tty",
            "cmd x -- read answer": "reads a tty",
            "cmd x -- ssh -t chopin top": "ssh -t",
            "t2 pull": "not a Test2 word",
            "t2 HoltTest2 plan": "'plan' in an entry",
            "t2 HoltTest2 --hosts chopin": "--hosts in an entry",
        }
        for text, frag in cases.items():
            r = self.R(text + "\n")
            self.assertTrue(any(frag in x for x in r), (text, r))
        r = self.R("ht1 R1 debian13\nht1 R1 alpine324\n")
        self.assertTrue(any("one queue would serve both" in x for x in r), r)
        self.assertEqual(self.R("t2 GuestTest2 update\n", allow_update=True), [])
        self.assertEqual(self.R("cmd x -- sudo -n true\n"), [])

    def test_words_checked_against_names(self):
        known = {"stic": ["alpine324"], "holt": {"sources": ["alpine324"], "variants": {}, "families": {},
                                                 "refused": {}}, "gate": ["holt"], "guest": [], "img": ["linux"]}
        self.assertEqual(self.R("t2 HoltTest2 alpine324\n", known=known), [])
        r = self.R("t2 HoltTest2 nosuchguest\n", known=known)
        self.assertTrue(any("nosuchguest" in x for x in r), r)


class Locks(unittest.TestCase):
    def E(self, text):
        return P(text + "\n").entries[0]

    def test_locks_and_pools(self):
        cases = {"t2 GateTest2All": ({"gate"}, {"gate"}), "t2 SticTest2 rel6": ({"stic"}, {"stic"}),
                 "t2 HoltTest2D": ({"holt"}, {"holt"}), "t2 GuestTest2All": ({"guest"}, {"holt"}),
                 "t2 ImgTest2All release": ({"img", "holt"}, {"holt"}),
                 "t2 SweepTest2 rel6": (set(bf.ALL_LOCKS), {"stic", "holt", "gate"}),
                 "t2 BkoTest2": (set(bf.ALL_LOCKS), {"stic", "holt", "gate"}),
                 "t2 GuestTest2 update": (set(bf.ALL_LOCKS), set(bf.POOLS)),
                 "ht1 R debian13": (set(), {"holt"}), "stic1 rel6": (set(), {"stic"}), "bs2 BackShot2Alpine 200": (set(), {"bs2"}),
                 "cmd x -- true": (set(), set()), "cmd x hosts=cage -- true": (set(), set(bf.POOLS)),
                 "serial stic1 rel6": (set(), set(bf.POOLS)), "serial cmd x -- true": (set(), set())}
        for text, (locks, pools) in cases.items():
            e = self.E(text)
            self.assertEqual((bf.locks_of(e), bf.pools_of(e)), (locks, pools), text)
        self.assertEqual(bf.lock_list(self.E("t2 SweepTest2"))[0], "bko")

    def test_matrix(self):
        words = ["t2 GateTest2", "t2 SticTest2", "t2 HoltTest2", "t2 GuestTest2", "t2 ImgTest2", "t2 BkoTest2",
                 "t2 SweepTest2", "t2 GuestTest2 update", "ht1 R x", "cmd c -- true"]
        b = P("\n".join(f"{w} id=e{i}" if w.startswith("t2") or w.startswith("ht1") else w.replace(" --", f" id=e{i} --")
                        for i, w in enumerate(words)) + "\n")
        m = bf.lock_matrix(b.entries)
        ids = {w: f"e{i}" for i, w in enumerate(words)}

        def clash(a, c):
            return (ids[a], ids[c]) in m

        fams = words[:5]
        for i, a in enumerate(fams):
            for c in fams[i + 1:]:
                expect = {a, c} == {"t2 ImgTest2", "t2 HoltTest2"}
                self.assertEqual(clash(a, c), expect, (a, c))
        self.assertIn("holt", m[(ids["t2 ImgTest2"], ids["t2 HoltTest2"])])
        for comp in ("t2 BkoTest2", "t2 SweepTest2", "t2 GuestTest2 update"):
            for f in fams:
                self.assertTrue(clash(comp, f), (comp, f))
            self.assertTrue(clash(f, comp) for f in fams)
        self.assertTrue(clash("t2 BkoTest2", "t2 SweepTest2"))
        self.assertFalse(clash("ht1 R x", "t2 HoltTest2"))    # (pools, not locks: §4.5)
        self.assertFalse(clash("cmd c -- true", "t2 GuestTest2 update"))   # (update is serial instead)
        b2 = P("t2 SticTest2 rel6 id=a\nt2 GateTest2 rel6 id=b\nt2 GateTest2 rel7 id=c\n")
        m2 = bf.lock_matrix(b2.entries, builds={"a": "rel6", "b": "rel6", "c": "rel7"})
        self.assertEqual(m2[("a", "b")], "both build round rel6")
        self.assertNotIn(("a", "c"), m2)

    def test_effective_args(self):
        s = dict(bf.SETTINGS, round="rel6")
        for text, want in (("t2 SweepTest2", ["SweepTest2", "rel6"]), ("t2 SweepTest2 rel5", ["SweepTest2", "rel5"]),
                           ("t2 HoltTest2 x", ["HoltTest2", "x"]), ("t2 GateTest2All", ["GateTest2All"])):
            self.assertEqual(bf.effective_args(self.E(text), s), want, text)
        known = {"stic": ["alpine324"], "gate": ["holt"], "holt": {}, "guest": [], "img": []}
        self.assertEqual(bf.effective_args(self.E("t2 GateTest2All"), s, known), ["GateTest2All", "rel6"])
        self.assertEqual(bf.effective_args(self.E("t2 SticTest2 alpine324"), s, known), ["SticTest2", "rel6", "alpine324"])
        self.assertEqual(bf.effective_args(self.E("t2 SticTest2 rel5 alpine324"), s, known), ["SticTest2", "rel5", "alpine324"])


class NextAction(unittest.TestCase):
    """§6.2's table, row by row."""

    def E(self, text):
        return P(text + "\n").entries[0]

    def test_rows(self):
        t2 = self.E("t2 HoltTest2")
        gate = self.E("t2 GateTest2")
        cmd = self.E("cmd x -- true")
        cmd_r = self.E("cmd x rerun=yes -- true")
        ht1 = self.E("ht1 R debian13")
        rows = [
            (t2, {"ended": True, "alive": True}, "none"),
            (t2, {"placed": True, "started": True, "alive": True, "run": "t2h-x"}, "adopt"),
            (t2, {"placed": True}, "replace"),
            (t2, {"placed": True, "exit": 0}, "finish"),
            (t2, {"placed": True, "started": True, "run": "t2h-x", "exit": 1}, "finish"),
            (t2, {"placed": True, "started": True, "run": "t2h-x"}, "resume"),
            (t2, {"placed": True, "started": True, "run": "t2h-x", "exit": 130}, "resume"),
            (t2, {"placed": True, "started": True, "run": "t2h-x", "run_finished": True}, "finish-unknown"),
            (t2, {"placed": True, "started": True}, "replace"),
            (gate, {"placed": True, "started": True, "run": "t2g-x"}, "gate-incomplete"),
            (ht1, {"placed": True, "started": True}, "restart"),
            (ht1, {"placed": True}, "replace"),
            (cmd, {"placed": True, "started": True}, "incomplete"),
            (cmd, {"placed": True, "started": True, "exit": 143}, "incomplete"),
            (cmd_r, {"placed": True, "started": True}, "rerun"),
            (cmd, {"placed": True, "started": True, "exit": 0}, "finish"),
            (cmd, {}, "run"),
        ]
        for e, st, want in rows:
            self.assertEqual(B.next_action(st, e), want, (e.id, st))

    def test_status_and_retry(self):
        t2, cmd = self.E("t2 HoltTest2"), self.E("cmd x -- true")
        self.assertEqual([B.status_of(t2, rc) for rc in (0, 1, 2, 3, 130, 9)],
                         ["PASS", "FAIL", "REFUSED", "ERROR", "INCOMPLETE", "FAIL"])
        self.assertEqual([B.status_of(cmd, rc) for rc in (0, 1, 2, 143)], ["PASS", "FAIL", "FAIL", "INCOMPLETE"])
        self.assertTrue(B.retry_shaped("ERROR"))
        self.assertFalse(B.retry_shaped("FAIL"))


class Beacon(unittest.TestCase):
    def test_line(self):
        b = {"batch": "bt-20261115T0312-9ab1", "pid": os.getpid(), "hosts": ["chopin", "cinqcent", "copland", "cage"],
             "running": [{"entry": "a", "hosts": ["chopin", "cinqcent"], "pools": ["stic", "holt"]},
                         {"entry": "b", "hosts": ["copland"], "pools": ["bs2"]}],
             "entries_left": 3, "entries_done": 1, "eta": 1794543000}
        self.assertEqual(B.beacon_line(b), "batch bt-20261115T0312-9ab1 (2 of 6 in flight, ETA "
                         + __import__("time").strftime("%H:%MZ", __import__("time").gmtime(1794543000))
                         + ") holds holt,stic on chopin,cinqcent and bs2 on copland; cage is free")

    def test_dead_pid_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            B.write_json(B.beacon_path(d), {"batch": "x", "pid": 2 ** 22 + 12345})
            self.assertIsNone(B.read_beacon(d))
            B.write_json(B.beacon_path(d), {"batch": "x", "pid": os.getpid()})
            self.assertEqual(B.read_beacon(d)["batch"], "x")


# The scheduler's rules (pools, shares, splits, LPT, no idle host, live join, growth, sessions, the
# plan render) are tests/test_batchsched.py's (docs/design/batch.md §11.2).


if __name__ == "__main__":
    unittest.main()
