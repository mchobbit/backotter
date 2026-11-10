"""P1's ledger tests: U1.1 schema, U1.2 round trip, checksum, torn tail, 8 x 500
writers at once and a writer killed after half a row, U1.3 merge and dedupe;
pull (incremental, full, lost rows, unreachable host), the wear fill, the root
writer's ownership rule, serial scrubbing and the t2-host CLI.  No host needed:
the "local" host stands in for one."""

import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
TEST2 = os.path.dirname(HERE)
sys.path.insert(0, TEST2)

from t2lib import GROUPS, canonical, keys, ledger, short_host  # noqa: E402

FX = os.path.join(HERE, "fixtures", "p1")
T2HOST = os.path.join(TEST2, "t2-host")
ENV = ("T2_MAC_ROOT", "T2_HOST_ROOT", "T2_COPY_ROOT", "T2_LEDGER", "T2_WORK", "T2_WEAR_LOG", "SUDO_UID", "SUDO_GID")
_saved, _tmp = {}, None


def setUpModule():
    global _tmp
    _tmp = tempfile.mkdtemp(prefix="t2-test-ledger-")
    for k in ENV:
        _saved[k] = os.environ.get(k)
        os.environ.pop(k, None)
    os.environ.update(T2_MAC_ROOT=os.path.join(_tmp, "mac"), T2_HOST_ROOT=os.path.join(_tmp, "host"),
                      T2_COPY_ROOT=os.path.join(_tmp, "copy"))


def tearDownModule():
    for k, v in _saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    shutil.rmtree(_tmp, ignore_errors=True)


def tmpdir(name):
    d = os.path.join(_tmp, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    return d


def run_row(i=0, **over):
    inputs = {"bin": {"linux/restic-bko": "sha256:" + "a" * 64},
              "harness": {"testvm/stictest1/debian13/check.sh": "sha256:" + "%064x" % i},
              "soft": {"kernel": "ver:6.12.115"}}
    r = {"origin": f"stic:t2s-20261108T0315-4f2a:debian13:{i}", "family": "stic", "cell": "stic:debian13",
         "kind": "run", "run": "t2s-20261108T0315-4f2a", "word": "SticTest2 rel4 debian13", "rule": "new",
         "reasons": ["no PASS ever"], "attempt": 1, "inputs": inputs, "valid": True, "verdict": "PASS",
         "class": "pass", "needs": {"acl": "PASS", "xattr": "LIMIT"}, "counts": {"PASS": 1, "LIMIT": 1},
         "start": "2026-11-08T03:15:00Z", "commit": "6fff54b" + "0" * 33, "committed": True, "dirty": {},
         "copy": "6fff54b", "logs": "chopin:/home/m/backotter-work/stictest1/debian13/logs/x", "gm": 8.0,
         "wall_s": 480, "wear": {"stic:t2s-20261108T0315-4f2a:debian13": None}}
    r.update(over)
    return r


class Schema(unittest.TestCase):
    """U1.1."""

    def test_complete_row_is_valid(self):
        r = ledger.complete(run_row(), host="chopin")
        self.assertEqual(ledger.validate(r), [])
        self.assertTrue(ledger.ID_RE.match(r["id"]) and r["id"].startswith("chopin-"))
        self.assertEqual(r["key"], keys.key("stic:debian13", r["inputs"]))
        self.assertEqual(set(r["groups"]), set(GROUPS))
        self.assertEqual(r["counts"], {"PASS": 1, "FAIL": 0, "LIMIT": 1})
        self.assertEqual(r["c"], ledger.row_c(r))
        self.assertIsNone(r["seed"])
        self.assertEqual(r["v"], 1)
        line = ledger.encode(r)
        self.assertTrue(line.endswith(b"\n") and line.count(b"\n") == 1)
        self.assertEqual(line.decode("ascii").strip(), canonical(r))

    def test_each_required_field(self):
        r = ledger.complete(run_row(), host="chopin")
        for f in ledger.FIELDS:
            bad = dict(r)
            del bad[f]
            self.assertTrue(any(e.startswith(f"no {f}") for e in ledger.validate(bad)), f)

    def test_bad_values(self):
        base = ledger.complete(run_row(), host="chopin")
        cases = {
            "family": ("bko", "family"), "kind": ("rerun", "kind"), "verdict": ("OK", "verdict"),
            "class": ("passed", "class"), "attempt": (0, "attempt"), "id": ("chopin-x", "id"),
            "t": ("2026-11-08 03:15", "t"), "key": ("0" * 64, "key"), "planned_key": ("xyz", "planned_key"),
            "needs": ({"acl": "OK"}, "needs"), "counts": ({"PASS": -1}, "counts"), "commit": ("zz", "commit"),
            "wear": ({"t": -5}, "wear"), "dirty": ({"p": "x"}, "dirty"), "logs": ("nohost", "logs"),
            "cell": ("holt:debian13", "cell"), "gm": (-1.0, "gm"), "valid": (1, "valid"), "attempt ": None,
        }
        for f, case in cases.items():
            if case is None:
                continue
            v, word = case
            r = dict(base, **{f: v})
            r["c"] = ledger.row_c(r)
            e = ledger.validate(r)
            self.assertTrue(any(x.startswith(word) for x in e), f"{f}={v!r}: {e}")
        r = dict(base, inputs=dict(base["inputs"], bogus={"x": "absent"}))
        self.assertTrue(any("unknown group" in x for x in ledger.validate(r)))
        r = dict(base, inputs={"bin": {"x": "sha256:short"}})
        self.assertTrue(any("bad value" in x for x in ledger.validate(r)))
        r = dict(base, groups=dict(base["groups"], bin="0" * 16))
        r["c"] = ledger.row_c(r)
        self.assertTrue(any(x.startswith("groups") for x in ledger.validate(r)))
        r = dict(base, note="changed after c")
        self.assertTrue(any(x.startswith("c:") for x in ledger.validate(r)))
        self.assertEqual(ledger.validate("x"), ["not a JSON object"])

    def test_cross_field_rules(self):
        base = ledger.complete(run_row(), host="chopin")

        def errs(**over):
            r = dict(base, **over)
            r["c"] = ledger.row_c(r)
            return ledger.validate(r)
        self.assertEqual(errs(planned_key=base["key"]), [])
        self.assertTrue(any(e.startswith("valid: true") for e in errs(planned_key="f" * 64)))
        self.assertEqual(errs(planned_key="f" * 64, valid=False), [])          # a measured key that moved
        self.assertTrue(any("doesn't go with verdict PASS" in e for e in errs(**{"class": "harness"})))
        self.assertEqual(errs(**{"class": "known-flake"}), [])
        self.assertEqual(errs(verdict="FAIL", **{"class": "fail"}), [])
        self.assertEqual(errs(verdict="LOST", **{"class": "harness"}), [])      # rows_stic's lost
        self.assertTrue(any("doesn't go" in e for e in errs(verdict="HARNESS", **{"class": "pass"})))
        self.assertTrue(any(e.startswith("needs: a stic PASS") for e in errs(needs={}, counts={"PASS": 0, "FAIL": 0, "LIMIT": 0})))
        self.assertTrue(any("FAIL or LIMIT-NEW" in e for e in errs(needs={"acl": "PASS", "x": "LIMIT-NEW"})))
        self.assertEqual(errs(family="gate", cell="gate:holt", needs={},
                              key=keys.key("gate:holt", base["inputs"])), [])   # gate rows may have none
        self.assertTrue(any(e.startswith("committed: true") for e in errs(commit=None)))
        self.assertEqual(errs(commit=None, committed=False, dirty=None), [])

    def test_kinds(self):
        r = ledger.complete(run_row(start=None), host="chopin")
        self.assertTrue(any("start" in e for e in ledger.validate(r)))
        r = ledger.complete(run_row(kind="carried", start=None, origin="derived:t2s-x:stic:debian13:carried"),
                            host="mac")
        self.assertTrue(any(e.startswith("via") for e in ledger.validate(r)))
        r = ledger.complete(run_row(kind="carried", start=None, via="chopin-20261108T031500Z-0123abcd",
                                    origin="derived:t2s-x:stic:debian13:carried"), host="mac")
        self.assertEqual(ledger.validate(r), [])
        self.assertTrue(r["id"].startswith("mac-"))

    def test_cut_and_size(self):
        r = ledger.complete(run_row(reasons=["x" * 500] * 30, fails=["FAIL " + "y" * 300] * 25), host="chopin")
        self.assertEqual(len(r["reasons"]), 20)
        self.assertTrue(all(len(x) <= 200 for x in r["reasons"] + r["fails"]))
        self.assertEqual(ledger.validate(r), [])
        big = ledger.complete(run_row(meta={"pad": "z" * 70000}), host="chopin")
        self.assertTrue(any("over 65536" in e for e in ledger.validate(big)))
        with self.assertRaises(ledger.InvalidRow):
            ledger.check(big)

    def test_scrub(self):
        r = ledger.complete(run_row(fails=["FAIL disk FAKESER-NOT-REAL-01 gone"], note="on FAKESER-NOT-REAL-01",
                                    extra={"x": ["FAKESER-NOT-REAL-01"]}, reasons=["FAKESER-NOT-REAL-01"]),
                            host="chopin", serials=["FAKESER-NOT-REAL-01"])
        self.assertNotIn("FAKESER", canonical(r))
        self.assertEqual(r["fails"], ["FAIL disk [serial] gone"])
        self.assertEqual(ledger.validate(r), [])


class NewFamilies(unittest.TestCase):
    def row(self, **over):
        r = run_row(0, family="guest", cell="guest:arch", needs={"identity": "PASS", "logs": "PASS"})
        r.update(over)
        return ledger.complete(r, host="chopin")

    def test_guest_and_img_rows(self):
        self.assertEqual(ledger.validate(self.row()), [])
        self.assertEqual(ledger.validate(self.row(family="img", cell="img:linux", needs={"refuse:restic-bko": "PASS"})), [])

    def test_a_pass_without_needs_is_refused(self):
        for f, c in (("guest", "guest:arch"), ("img", "img:linux")):
            self.assertTrue(any("names what passed" in e for e in ledger.validate(self.row(family=f, cell=c, needs={}))))

    def test_a_class_row_with_a_fail_need_is_refused(self):
        for v in ("FAIL", "LIMIT-NEW"):
            errs = ledger.validate(self.row(kind="class", via="x", start=None, needs={"after:fstab": v}))
            self.assertTrue(any("class row" in e for e in errs), errs)
        self.assertEqual(ledger.validate(self.row(kind="class", via="x", start=None, needs={"logs": "PASS", "x": "LIMIT"})), [])


class Files(unittest.TestCase):
    """U1.2."""

    def test_round_trip(self):
        d = tmpdir("u12-round")
        p = os.path.join(d, "a", "b", "chopin.jsonl")   # directories made as needed
        rows = [ledger.complete(run_row(i), host="chopin") for i in range(5)]
        res = ledger.append(p, rows)
        self.assertEqual([x for x, _ in res], [r["id"] for r in rows])
        self.assertTrue(all(w for _, w in res))
        rd = ledger.read(p)
        self.assertEqual(rd.rows, rows)
        self.assertEqual((rd.bad, rd.torn), (0, False))
        self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o644")
        self.assertEqual(oct(os.stat(os.path.dirname(p)).st_mode & 0o777), "0o755")

    def test_origin_once(self):
        d = tmpdir("u12-origin")
        p = os.path.join(d, "l.jsonl")
        a = ledger.complete(run_row(1), host="chopin")
        b = ledger.complete(run_row(1, note="a retry of the same writer"), host="chopin")
        (ia, wa), = ledger.append(p, [a])
        (ib, wb), = ledger.append(p, [b])
        self.assertEqual((ia, wa, ib, wb), (a["id"], True, a["id"], False))
        self.assertEqual(len(ledger.read(p).rows), 1)
        # an origin seen only in a bad line doesn't count
        with open(p, "ab") as f:
            f.write(ledger.encode(dict(ledger.complete(run_row(2), host="chopin"), c="0" * 16)))
        (ic, wc), = ledger.append(p, [ledger.complete(run_row(2), host="chopin")])
        self.assertTrue(wc)
        self.assertEqual(ledger.read(p).bad, 1)

    def test_checksum_and_bad_lines(self):
        d = tmpdir("u12-bad")
        p = os.path.join(d, "l.jsonl")
        rows = [ledger.complete(run_row(i), host="chopin") for i in range(3)]
        ledger.append(p, rows)
        with open(p, "rb") as f:
            lines = f.read().split(b"\n")
        lines[1] = lines[1].replace(b'"gm":8.0', b'"gm":9.0')   # c no longer matches
        with open(p, "wb") as f:
            f.write(b"\n".join(lines[:3]) + b"\nnot json\n{\"v\": 1}\n[1, 2]\n")
        rd = ledger.read(p)
        self.assertEqual([r["id"] for r in rd.rows], [rows[0]["id"], rows[2]["id"]])
        self.assertEqual(rd.bad, 4)

    def test_torn_tail(self):
        d = tmpdir("u12-torn")
        p = os.path.join(d, "l.jsonl")
        rows = [ledger.complete(run_row(i), host="chopin") for i in range(2)]
        ledger.append(p, rows[:1])
        with open(p, "ab") as f:
            f.write(ledger.encode(rows[1])[:40])   # a writer died here
        rd = ledger.read(p)
        self.assertEqual((len(rd.rows), rd.bad, rd.torn), (1, 0, True))   # skipped silently
        r3 = ledger.complete(run_row(3), host="chopin")
        ledger.append(p, [r3])
        rd = ledger.read(p)
        self.assertEqual([r["id"] for r in rd.rows], [rows[0]["id"], r3["id"]])
        self.assertEqual((rd.bad, rd.torn), (1, False))   # the torn line ends at the repair's newline

    def test_torn_whole_row_counts_its_origin(self):
        # A writer that stopped just before its newline left a whole row: an append of
        # the same origin finds it, finishes its line, and appends nothing.
        d = tmpdir("u12-torn-whole")
        p = os.path.join(d, "l.jsonl")
        first = ledger.complete(run_row(0), host="chopin")
        ledger.append(p, [first])
        fail = ledger.complete(run_row(1, verdict="FAIL", **{"class": "fail"}), host="chopin")
        with open(p, "ab") as f:
            f.write(ledger.encode(fail)[:-1])
        again = ledger.complete(run_row(1), host="chopin")   # the same origin, now a PASS
        self.assertEqual(ledger.append(p, [again]), [(fail["id"], False)])
        rd = ledger.read(p)
        self.assertEqual([r["id"] for r in rd.rows], [first["id"], fail["id"]])
        self.assertEqual((rd.bad, rd.torn), (0, False))
        # a whole torn row of another origin still becomes a row, and the new one follows
        other = ledger.complete(run_row(2), host="chopin")
        with open(p, "ab") as f:
            f.write(ledger.encode(other)[:-1])
        r3 = ledger.complete(run_row(3), host="chopin")
        self.assertEqual(ledger.append(p, [r3]), [(r3["id"], True)])
        self.assertEqual([r["id"] for r in ledger.read(p).rows], [first["id"], fail["id"], other["id"], r3["id"]])

    def test_concurrency_8x500(self):
        d = tmpdir("u12-conc")
        res = ledger.selftest_concurrency(os.path.join(d, "l.jsonl"), 8, 500)
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["rows"], res["bad"], res["dup_ids"], res["dup_origins"]), (4000, 0, 0, 0))

    def test_killed_writer(self):
        d = tmpdir("u12-kill")
        res = ledger.selftest_kill(os.path.join(d, "l.jsonl"))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["killed_rc"], -9)

    def test_lock_timeout(self):
        import fcntl
        d = tmpdir("u12-lock")
        p = os.path.join(d, "l.jsonl")
        ledger.append(p, [ledger.complete(run_row(0), host="chopin")])
        fd = os.open(p, os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            with self.assertRaises(ledger.LockTimeout):
                ledger.append(p, [ledger.complete(run_row(1), host="chopin")], lock_wait=0.3)
        finally:
            os.close(fd)

    def test_owner_rule(self):
        d = tmpdir("u12-owner")
        st = os.stat(d)
        self.assertEqual(ledger._owner_for(os.path.join(d, "x", "y", "l.jsonl")), (st.st_uid, st.st_gid))
        os.environ.update(SUDO_UID="1000", SUDO_GID="1001")
        try:
            self.assertEqual(ledger._owner_for(os.path.join(d, "x", "l.jsonl")), (1000, 1001))
        finally:
            os.environ.pop("SUDO_UID")
            os.environ.pop("SUDO_GID")


class Merge(unittest.TestCase):
    """U1.3, pull and the wear fill."""

    def test_merge_dedupe(self):
        a = [ledger.complete(run_row(i, t=f"2026-11-08T0{i}:00:00Z"), host="chopin") for i in (3, 1)]
        b = [a[0], ledger.complete(run_row(2, t="2026-11-08T02:00:00Z"), host="cinqcent")]
        other = dict(a[1], note="the same id, other content")
        other["c"] = ledger.row_c(other)
        rows, warn = ledger.merge([("chopin.jsonl", a), ("cinqcent.jsonl", b), ("x.jsonl", [other])])
        self.assertEqual([r["t"][11:13] for r in rows], ["01", "02", "03"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(warn), 1)
        self.assertIn("chopin.jsonl's kept", warn[0])
        self.assertEqual([r for r in rows if r["id"] == a[1]["id"]][0].get("note"), None)

    def host_file(self, hroot):
        return os.path.join(hroot, "ledger", short_host() + ".jsonl")

    def test_pull(self):
        mac, hroot = tmpdir("pull-mac"), tmpdir("pull-host")
        hf = self.host_file(hroot)
        rows = [ledger.complete(run_row(i, wear={}), host=short_host()) for i in range(4)]
        ledger.append(hf, rows[:2])
        rec = ledger.pull_one("local", mac, hroot)
        self.assertTrue(rec["ok"], rec)
        self.assertEqual((rec["mode"], rec["rows"], rec["bad"], rec["lost"]), ("full", 2, 0, 0))
        ledger.append(hf, rows[2:])
        with open(hf, "ab") as f:
            f.write(b'{"torn')   # a writer in the middle: not pulled yet
        rec = ledger.pull_one("local", mac, hroot)
        self.assertEqual((rec["mode"], rec["rows"]), ("incr", 4), rec)
        with open(os.path.join(mac, "ledger", "local.jsonl"), "rb") as f:
            self.assertTrue(f.read().endswith(b"\n"))
        rec = ledger.pull_one("local", mac, hroot)
        self.assertEqual((rec["mode"], rec["rows"]), ("incr", 4))
        # the host's file loses rows (rewritten): they stay, in local.lost.jsonl, with a warning
        os.unlink(hf)
        ledger.append(hf, [rows[3]])
        rec = ledger.pull_one("local", mac, hroot)
        self.assertEqual((rec["mode"], rec["rows"], rec["lost"]), ("full", 1, 3), rec)
        self.assertIn("no longer has", rec["warning"])
        v = ledger.view(mac)
        self.assertEqual(sorted(r["id"] for r in v.rows), sorted(r["id"] for r in rows))
        self.assertTrue(any("no longer has" in w for w in v.warnings))
        # an unreachable host keeps its mirror
        os.environ["T2_SSH"] = "/bin/false"
        try:
            rec = ledger.pull_one("nosuchhost", mac, hroot)
        finally:
            del os.environ["T2_SSH"]
        self.assertFalse(rec["ok"])
        self.assertEqual(ledger.view(mac).unreachable(), ["nosuchhost"])
        self.assertEqual(len(ledger.view(mac).rows), 4)

    def test_pull_never_holds_the_lock_on_output(self):
        # A pull whose reader stalls (a Mac asleep, a dead ssh) blocks in write();
        # by then it has let go of the ledger's lock, so writers go on.
        import socket
        import time
        hroot = tmpdir("pull-stall-host")
        hf = os.path.join(hroot, "ledger", socket.gethostname().split(".")[0] + ".jsonl")
        big = [ledger.complete(run_row(i, meta={"pad": "p" * 3000}), host="chopin") for i in range(400)]
        ledger.append(hf, big)
        self.assertGreater(os.path.getsize(hf), 1 << 20)   # far over a pipe's buffer
        p = subprocess.Popen([sys.executable, "-I", "-B", "-c", ledger.PULL_SCRIPT, hroot, "0", ""],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            time.sleep(1.5)                                   # it has read the file and blocks on its output
            self.assertIsNone(p.poll())
            t0 = time.monotonic()
            res = ledger.append(hf, [ledger.complete(run_row(9999), host="chopin")], lock_wait=3)
            self.assertTrue(res[0][1])
            self.assertLess(time.monotonic() - t0, 3)
        finally:
            p.kill()   # our own child
            p.communicate()

    def test_wear_fill_error_records_nothing(self):
        mac, hroot = tmpdir("wear-err-mac"), tmpdir("wear-err-host")
        hf = self.host_file(hroot)
        tag = "ht1:r4all:pop24"
        r = ledger.complete(run_row(1, origin="ht1:r4all:pop24:1", cell="holt:pop24", family="holt",
                                    start="2026-11-08T02:09:50Z", t="2026-11-08T02:16:58Z", wear={tag: None}),
                            host=short_host())
        ledger.append(hf, [r])
        self.assertTrue(ledger.pull_one("local", mac, hroot)["ok"])
        later = ledger.parse_utc("2026-11-09T00:00:00Z")
        out = ledger.fill_wear("local", mac, jobs_log=os.path.join(hroot, "no-such-jobs.log"), now=later)
        self.assertEqual((out["asked"], out.get("error")), (1, "no jobs.log"))
        self.assertFalse(os.path.exists(os.path.join(mac, "ledger", "wear-local.json")))
        out = ledger.fill_wear("local", mac, jobs_log=os.path.join(FX, "jobs.log"), now=later)
        self.assertEqual((out["asked"], out.get("error")), (1, None))   # asked again, and answered

    def test_pull_none(self):
        mac, hroot = tmpdir("pull-none-mac"), tmpdir("pull-none-host")
        rec = ledger.pull_one("local", mac, hroot)
        self.assertEqual((rec["ok"], rec["mode"], rec["rows"]), (True, "none", 0))
        self.assertFalse(os.path.exists(os.path.join(hroot, "ledger")))   # the pull wrote nothing there

    def test_wear_fill(self):
        mac, hroot = tmpdir("wear-mac"), tmpdir("wear-host")
        hf = self.host_file(hroot)
        tag = "ht1:r4all:pop24"
        rows = [
            ledger.complete(run_row(1, origin="ht1:r4all:pop24:1", cell="holt:pop24", family="holt",
                                    start="2026-11-08T02:09:50Z", t="2026-11-08T02:16:58Z", wear={tag: None}),
                            host=short_host()),
            ledger.complete(run_row(2, origin="ht1:r4all:pop24:2", cell="holt:pop24", family="holt",
                                    start="2026-11-08T04:58:00Z", t="2026-11-08T04:59:00Z", wear={tag: None}),
                            host=short_host()),
            ledger.complete(run_row(3, origin="ht1:r4all:none:1", cell="holt:none", family="holt",
                                    start="2026-11-08T04:58:00Z", t="2026-11-08T04:59:00Z", wear={"ht1:r4all:none": None}),
                            host=short_host()),
        ]
        ledger.append(hf, rows)
        os.environ["T2_WEAR_LOG"] = os.path.join(FX, "jobs.log")
        later = ledger.parse_utc("2026-11-09T00:00:00Z")   # every row's window has closed
        try:
            early = ledger.pull(["local"], mac, hroot, now=ledger.parse_utc("2026-11-08T05:00:00Z"))
            self.assertEqual(early["local"]["wear"]["asked"], 3)
            out = ledger.pull(["local"], mac, hroot, now=later)   # not final the first time: asked again
        finally:
            del os.environ["T2_WEAR_LOG"]
        self.assertTrue(out["local"]["ok"], out)
        self.assertEqual(out["local"]["wear"]["asked"], 2)               # rows[0]'s window closed at 02:32Z
        v = {r["id"]: r for r in ledger.view(mac).rows}
        self.assertEqual(v[rows[0]["id"]]["wear"], {tag: 22504448})
        self.assertEqual(v[rows[1]["id"]]["wear"], {tag: 1000})          # its own run's line, the nbd's virtual
        self.assertEqual(v[rows[2]["id"]]["wear"], {"ht1:r4all:none": None})
        with open(os.path.join(mac, "ledger", "wear-local.json")) as f:
            cache = json.load(f)
        self.assertTrue(all(e["final"] for e in cache["rows"].values()))
        self.assertNotIn("FAKESER", json.dumps(cache))
        again = ledger.fill_wear("local", mac, jobs_log=os.path.join(FX, "jobs.log"), now=later)
        self.assertEqual(again["asked"], 0)                             # final: not asked again
        self.assertEqual(len(ledger.view(mac, fill_wear=False).rows), 3)


def load_t2host():
    loader = importlib.machinery.SourceFileLoader("t2host_under_test", T2HOST)
    spec = importlib.util.spec_from_loader("t2host_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class CLI(unittest.TestCase):
    """t2-host append, row, hash, wear, self-test (as a host runs them)."""

    def run_cli(self, args, stdin=b"", env=None):
        e = dict(os.environ, **(env or {}))
        return subprocess.run([sys.executable, "-B", T2HOST] + args, input=stdin, capture_output=True, env=e)

    def test_append(self):
        d = tmpdir("cli-append")
        led = os.path.join(d, "l.jsonl")
        env = {"T2_LEDGER": led}
        rows = [run_row(i) for i in range(3)]
        p = self.run_cli(["append"], json.dumps(rows).encode(), env)
        self.assertEqual(p.returncode, 0, p.stderr)
        ids = p.stdout.decode().split()
        self.assertEqual(len(ids), 3)
        self.assertEqual([r["id"] for r in ledger.read(led).rows], ids)
        # JSON lines; the same origins again: the old ids, nothing appended
        p = self.run_cli(["append"], b"\n".join(json.dumps(r).encode() for r in rows), env)
        self.assertEqual((p.returncode, p.stdout.decode().split()), (0, ids))
        self.assertIn(b"was there already", p.stderr)
        self.assertEqual(len(ledger.read(led).rows), 3)
        bad = run_row(9, verdict="GOOD")
        p = self.run_cli(["append"], json.dumps([run_row(8), bad]).encode(), env)
        self.assertEqual(p.returncode, 2)
        self.assertIn(b"verdict", p.stderr)
        self.assertEqual(len(ledger.read(led).rows), 3)   # nothing of an invalid batch
        p = self.run_cli(["append"], json.dumps(run_row(7, host="otherhost")).encode(), env)
        self.assertEqual(p.returncode, 2)
        p = self.run_cli(["append"], b"{not json", env)
        self.assertEqual(p.returncode, 2)
        os.chmod(d, 0o555)
        try:
            p = self.run_cli(["append"], json.dumps(run_row(6)).encode(), {"T2_LEDGER": os.path.join(d, "x", "l.jsonl")})
            self.assertEqual(p.returncode, 1, p.stderr)
        finally:
            os.chmod(d, 0o755)

    def test_row(self):
        d = tmpdir("cli-row")
        led = os.path.join(d, "l.jsonl")
        t2host = load_t2host()
        fake = types.ModuleType("t2lib.rows_fake")
        seen = {}

        def build(argv, hostctx):
            seen["argv"], seen["ctx"] = argv, hostctx
            return hostctx.row(**run_row(int(argv[0])))
        fake.build = build
        sys.modules["t2lib.rows_fake"] = fake
        os.environ["T2_LEDGER"] = led
        try:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = t2host.main(["row", "fake", "5", "--x"])
            self.assertEqual(rc, 0, err.getvalue())
            self.assertEqual(seen["argv"], ["5", "--x"])
            self.assertEqual(seen["ctx"].ledger_path, led)
            rid = out.getvalue().strip()
            self.assertEqual(ledger.read(led).rows[0]["id"], rid)
            fake.build = lambda argv, ctx: run_row(1, verdict="nope")
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                self.assertEqual(t2host.main(["row", "fake"]), 2)

            fake.build = lambda argv, ctx: None
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(t2host.main(["row", "fake"]), 4)   # nothing written: never "0"

            def nothing(argv, ctx):
                raise SystemExit(0)
            fake.build = nothing
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(t2host.main(["row", "fake"]), 4)
            self.assertEqual(len(ledger.read(led).rows), 1)

            def boom(argv, ctx):
                raise RuntimeError("builder failed")
            fake.build = boom
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(t2host.main(["row", "fake"]), 3)
                self.assertEqual(t2host.main(["row", "nosuchkind"]), 3)
                self.assertEqual(t2host.main(["row", "Bad-Kind"]), 2)
        finally:
            del sys.modules["t2lib.rows_fake"]
            del os.environ["T2_LEDGER"]

    def test_hash_wear_usage(self):
        p = self.run_cli(["hash", "--root", os.path.join(FX, "factsroot", "home", "m", "backotter-work"), "testvm"])
        self.assertEqual(p.returncode, 0, p.stderr)
        h = json.loads(p.stdout)
        self.assertEqual(sorted(h["files"]), ["testvm/.backotter-commit", "testvm/compare/cmp-run.sh",
                                              "testvm/compare/iolog.sh"])
        p = self.run_cli(["wear", "--tags", "ht1:r4all:redox", "ht1:r4all:nothing", "--log", os.path.join(FX, "jobs.log")])
        self.assertEqual(json.loads(p.stdout), {"ht1:r4all:nothing": None, "ht1:r4all:redox": 1862656})
        self.assertEqual(self.run_cli([]).returncode, 2)
        self.assertEqual(self.run_cli(["nosuch"]).returncode, 2)

    def test_self_test(self):
        d = tmpdir("cli-selftest")
        p = self.run_cli(["self-test", "--concurrency", "3x40", "--kill"], env={"T2_HOST_ROOT": d})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(b"self-test: 120 rows, 0 bad, torn tail repaired", p.stdout)
        self.assertEqual(os.listdir(os.path.join(d, "self-test")), [])   # its scratch is gone


if __name__ == "__main__":
    unittest.main()
