"""I2's table tests (docs/design/img-speed.md §3.3, §5.2 steps 1-3, §14 row I2): stick/img_round.py's
plan over fixture facts (every row of §3.3's table, C4's ledger proof, C7's per-host inputs, the
round's sets), stick/deal-units.py's image units, stick/img_round_facts.py on a fixture world, and
img-round.sh plan end to end from a facts directory and a fixture ledger.

No host, no ssh, nothing built.  Run from the worktree: python3 -m unittest discover testvm/test2/tests.
"""

import copy
import datetime
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
WT = os.path.dirname(os.path.dirname(T2))
STICK = os.path.join(WT, "stick")
sys.path.insert(0, T2)
sys.path.insert(0, HERE)

from t2lib import ledger  # noqa: E402
import test_img_inputs as TI  # noqa: E402


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


R = _load("img_round_t", os.path.join(STICK, "img_round.py"))
D = R.DEAL
PLATS = R.PLATFORMS
TODAY = datetime.date(2026, 11, 27)
OPTS = {"full": set(), "no_refresh": False}
BUILDERS = ["chopin", "cinqcent", "copland", "cage", "czerny"]
CLASSES = {"chopin": "big", "cinqcent": "big", "copland": "small", "cage": "small", "czerny": "small"}
TABLE = {"hosts": [{"name": h, "class": CLASSES[h], "roles": ["build"] + (["fleet"] if h == "chopin" else [])}
                   for h in BUILDERS]}
SETS = {"bko-bin": "b" * 64, "holt-bin": "h" * 64}


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def inputs(p, **over):
    base = {"stick": {f"stick/{p}/build.sh": sha("build " + p), "stick/img-one.sh": sha("one")},
            "host_tools": {"dosfstools": "4.2", "mtools": "4.0"}}
    if p == "linux":
        base.update(iso=sha("iso"), slim={"path": "slim-20261120", "manifest": sha("slim")})
    else:
        base.update(media={"m": sha("media " + p)},
                    builder={"vm": "freebsd15-builder" if p == "freebsd" else p + "-g", "kind": "generation",
                             "generation": sha("gen " + p)})
    for k, v in over.items():
        base[k] = v
    return base


def dig(p, bins="n1", tests="t1", **over):
    """A platform's img_inputs.py digest, as a host gives it."""
    i = inputs(p, **over)
    return {"platform": p, "base": {"digest": sha(json.dumps(i, sort_keys=True)), "inputs": i},
            "bins": {"digest": sha("bins " + bins), "restic-bko": sha(bins)},
            "tests": {"digest": sha("tests " + tests), "files": {}, "modes": ["bios", "uefi"]}}


def provenance(p, d, epoch="2026-11-26", when="2026-11-26T10:00:00Z", tag="x"):
    """A provenance whose digests are D's (what the image was built from)."""
    return {"schema": 1, "platform": p, "mode": "full",
            "built": {"host": "chopin", "when": when},
            "release": {"file": f"rel-{tag}.img", "sha256": sha(f"rel {p} {tag}"), "image_id": f"id-{tag}"},
            "test": {"file": f"test/test-{tag}.img", "sha256": sha(f"test {p} {tag}")},
            "base": {"digest": d["base"]["digest"], "inputs": d["base"]["inputs"], "os_epoch": epoch},
            "bins": {"digest": d["bins"]["digest"]}, "tests": {"digest": d["tests"]["digest"]}, "parent": None}


def image(pv, done=0, reserve=6.0):
    if pv is None:
        return {"done_exit": done, "provenance": None, "test_sha256": None, "release_sha256": None,
                "reserve_gib": reserve, "why": None}
    return {"done_exit": done, "provenance": pv, "test_sha256": pv["test"]["sha256"],
            "release_sha256": pv["release"]["sha256"], "reserve_gib": reserve, "why": None}


def probe(ceiling=(9.0, 18.5), free=(9.0, 18.5), load=0.0, nproc=16):
    return {"ok": True, "ceiling": ceiling, "ceiling_rc": 0, "free": free, "line_room": free, "free_rc": 0,
            "waiting": 0, "load": load, "nproc": nproc, "caps": [], "vms": []}


def host(digests=None, images=None, sets=None, pr=None, refresh=None, errors=None):
    return {"probe": pr or probe(),
            "facts": {"digests": digests if digests is not None else {p: dig(p) for p in PLATS},
                      "images": images or {}, "sets": dict(sets or SETS), "errors": errors or [],
                      "refresh": refresh if refresh is not None else {p: True for p in PLATS}}}


def pass_row(p, test_sha, verdict="PASS", panel=None, t="2026-11-26T12:00:00Z", committed=True, host_="chopin"):
    return {"cell": f"img:{p}", "valid": True, "kind": "run", "verdict": verdict, "committed": committed, "t": t,
            "host": host_, "inputs": {"bin": {f"{p}/test-image": "sha256:" + test_sha}},
            "extra": {"panel": panel if panel is not None else {"result": "pass", "needed": True}}}


def fresh_world(**kw):
    """Five builders right after a fresh set built from today's inputs, its proof in the ledger:
    (gathered, rows)."""
    pvs = {p: provenance(p, dig(p)) for p in PLATS}
    rows = [pass_row(p, pvs[p]["test"]["sha256"]) for p in PLATS]
    g = {h: host(images={p: image(pvs[p]) for p in PLATS}) for h in BUILDERS}
    return g, rows, pvs


def plan(g, rows, builders=BUILDERS, ref="chopin", opts=None, today=TODAY):
    return R.plan_round(g, builders, ref, rows, opts or OPTS, today, TABLE)


class Table(unittest.TestCase):
    """§3.3, a row at a time."""

    def test_fresh_set_reuses_all_seven(self):
        g, rows, _ = fresh_world()
        p = plan(g, rows)
        self.assertIsNone(p["refused"])
        self.assertEqual({x: p["platforms"][x]["action"] for x in PLATS}, {x: "reuse" for x in PLATS})
        self.assertEqual([u for d in p["deal"] for u in d["units"]], [])
        self.assertIn("reuse x7", R.render(p)[-1])

    def test_tests_changed_is_a_retest_with_no_unit(self):
        g, rows, _ = fresh_world()
        g["chopin"]["facts"]["digests"]["openbsd"] = dig("openbsd", tests="t2")
        p = plan(g, rows)
        self.assertEqual(p["platforms"]["openbsd"]["action"], "retest")
        self.assertNotIn("minutes", p["platforms"]["openbsd"])
        self.assertEqual(sum(len(d["units"]) for d in p["deal"]), 0)

    def test_bins_changed_is_a_refresh_on_a_holder(self):
        g, rows, _ = fresh_world()
        for h in BUILDERS:
            g[h]["facts"]["digests"]["netbsd"] = dig("netbsd", bins="n2")
        del g["cage"]["facts"]["images"]["netbsd"]           # cage doesn't hold the parent
        p = plan(g, rows)
        r = p["platforms"]["netbsd"]
        self.assertEqual((r["action"], r["why"]), ("refresh", "bins changed"))
        self.assertEqual(r["able"]["cage"], "it doesn't hold the parent image")
        self.assertIn(r["host"], set(BUILDERS) - {"cage"})
        self.assertEqual(r["minutes"], 4.0)

    def test_bins_changed_with_tests_changed_is_still_a_refresh(self):
        g, rows, _ = fresh_world()
        for h in BUILDERS:
            g[h]["facts"]["digests"]["haiku"] = dig("haiku", bins="n2", tests="t2")
        self.assertEqual(plan(g, rows)["platforms"]["haiku"]["action"], "refresh")

    def test_a_refresh_no_holder_can_take_is_a_full_build(self):
        g, rows, _ = fresh_world()
        for h in BUILDERS:
            g[h]["facts"]["digests"]["netbsd"] = dig("netbsd", bins="n2")
            if h != "chopin":
                del g[h]["facts"]["images"]["netbsd"]
        g["chopin"]["probe"] = probe(ceiling=(3.0, 6.0))   # the one holder can't take it
        r = plan(g, rows)["platforms"]["netbsd"]
        self.assertEqual(r["action"], "full")
        self.assertIn("no able host holds the parent", r["why"])
        self.assertNotEqual(r["host"], "chopin")

    def test_no_refresh_and_no_refresh_yet_are_full_builds(self):
        g, rows, _ = fresh_world()
        for h in BUILDERS:
            g[h]["facts"]["digests"]["netbsd"] = dig("netbsd", bins="n2")
        p = plan(g, rows, opts={"full": set(), "no_refresh": True})
        self.assertEqual(p["platforms"]["netbsd"]["action"], "full")
        self.assertIn("--no-refresh", p["platforms"]["netbsd"]["why"])
        g["chopin"]["facts"]["refresh"] = {x: False for x in PLATS}
        r = plan(g, rows)["platforms"]["netbsd"]
        self.assertEqual(r["action"], "full")
        self.assertIn("no refresh of netbsd yet", r["why"])
        self.assertEqual(r["minutes"], D.img_minutes("netbsd", "full"))

    def test_base_changed_is_full_and_says_what(self):
        g, rows, _ = fresh_world()
        for h in BUILDERS:
            g[h]["facts"]["digests"]["illumos"] = dig(
                "illumos", stick={"stick/illumos/build.sh": sha("new"), "stick/img-one.sh": sha("one")})
        r = plan(g, rows)["platforms"]["illumos"]
        self.assertEqual(r["action"], "full")
        self.assertIn("base changed: stick/ illumos/build.sh", r["why"])
        # a base change with bins and tests changed too is still full
        for h in BUILDERS:
            g[h]["facts"]["digests"]["linux"] = dig("linux", bins="n2", tests="t2",
                                                    slim={"path": "slim-20261127", "manifest": sha("s2")})
        r = plan(g, rows)["platforms"]["linux"]
        self.assertEqual(r["action"], "full")
        self.assertIn("slim (slim-20261120 vs slim-20261127)", r["why"])

    def test_past_the_cadence_is_full(self):
        g, rows, pvs = fresh_world()
        old = provenance("dragonfly", dig("dragonfly"), epoch="2026-11-12")
        for h in BUILDERS:
            g[h]["facts"]["images"]["dragonfly"] = image(old)
        rows.append(pass_row("dragonfly", old["test"]["sha256"]))
        p = plan(g, rows)
        r = p["platforms"]["dragonfly"]
        self.assertEqual((r["action"], r["age_days"]), ("full", 15))
        self.assertIn("past the cadence", r["why"])
        self.assertEqual(plan(g, rows, today=datetime.date(2026, 11, 26))["platforms"]["dragonfly"]["action"], "reuse")

    def test_no_provenance_is_full(self):
        g, rows, _ = fresh_world()
        for h in BUILDERS:
            g[h]["facts"]["images"]["freebsd"] = image(None)
        r = plan(g, rows)["platforms"]["freebsd"]
        self.assertEqual((r["action"], r["why"]), ("full", "no provenance (an image from before I1)"))
        for h in BUILDERS:
            del g[h]["facts"]["images"]["freebsd"]
        self.assertEqual(plan(g, rows)["platforms"]["freebsd"]["why"], "no current image")

    def test_full_asked(self):
        g, rows, _ = fresh_world()
        p = plan(g, rows, opts={"full": {"haiku", "linux"}, "no_refresh": False})
        self.assertEqual({x: p["platforms"][x]["action"] for x in ("haiku", "linux", "netbsd")},
                         {"haiku": "full", "linux": "full", "netbsd": "reuse"})
        self.assertEqual(p["platforms"]["haiku"]["why"], "asked (--full)")


class ProofC4(unittest.TestCase):
    """Only a proven parent (C4): the ledger, not done.exit."""

    def action(self, rows_for_linux):
        g, rows, pvs = fresh_world()
        rows = [r for r in rows if r["cell"] != "img:linux"] + rows_for_linux(pvs["linux"]["test"]["sha256"])
        return plan(g, rows)["platforms"]["linux"]

    def test_no_row_is_not_proven(self):
        r = self.action(lambda s: [])
        self.assertEqual(r["action"], "full")
        self.assertIn("no current image passed its proof (C4)", r["why"])
        self.assertIn("no ImgTest2 row", r["why"])

    def test_panel_owed_is_not_proven(self):
        r = self.action(lambda s: [pass_row("linux", s, panel={"result": "owed", "needed": True})])
        self.assertEqual(r["action"], "full")
        self.assertIn("panel is owed", r["why"])

    def test_panel_not_needed_is_proven(self):
        r = self.action(lambda s: [pass_row("linux", s, panel={"result": "none", "needed": False})])
        self.assertEqual(r["action"], "reuse")

    def test_a_row_recording_no_panel_is_not_proven(self):
        r = self.action(lambda s: [pass_row("linux", s, panel={})])
        self.assertEqual(r["action"], "full")
        self.assertIn("panel is not recorded", r["why"])

    def test_boot_row_then_panel_row(self):
        r = self.action(lambda s: [pass_row("linux", s, panel={"result": "owed", "needed": True}, t="2026-11-26T11:00:00Z"),
                                   pass_row("linux", s, t="2026-11-26T11:30:00Z")])
        self.assertEqual(r["action"], "reuse")

    def test_a_fail_since_the_pass_is_not_proven(self):
        r = self.action(lambda s: [pass_row("linux", s, t="2026-11-26T11:00:00Z"),
                                   pass_row("linux", s, verdict="FAIL", t="2026-11-26T13:00:00Z")])
        self.assertEqual(r["action"], "full")

    def test_uncommitted_and_other_images_dont_count(self):
        r = self.action(lambda s: [pass_row("linux", s, committed=False), pass_row("linux", sha("other"))])
        self.assertEqual(r["action"], "full")
        self.assertIn("uncommitted", r["why"])

    def test_a_build_under_way_is_not_current(self):
        g, rows, pvs = fresh_world()
        for h in BUILDERS:
            g[h]["facts"]["images"]["openbsd"]["done_exit"] = None
        r = plan(g, rows)["platforms"]["openbsd"]
        self.assertEqual(r["action"], "full")
        self.assertIn("done.exit is None", r["why"])

    def test_the_newest_proven_image_is_the_parent(self):
        g, rows, pvs = fresh_world()
        newer = provenance("netbsd", dig("netbsd"), when="2026-11-27T01:00:00Z", tag="new")
        g["cinqcent"]["facts"]["images"]["netbsd"] = image(newer)
        g["copland"]["facts"]["images"]["netbsd"] = image(newer)
        rows.append(pass_row("netbsd", newer["test"]["sha256"]))
        r = plan(g, rows)["platforms"]["netbsd"]
        self.assertEqual(r["parent"]["test_sha256"], newer["test"]["sha256"])
        self.assertEqual(r["holders"], ["cinqcent", "copland"])


class Hosts(unittest.TestCase):
    """The round's sets and each host's inputs (C1, C7)."""

    def test_a_mismatched_holt_bin_refuses_the_plan(self):
        g, rows, _ = fresh_world()
        g["cage"]["facts"]["sets"]["holt-bin"] = "0" * 64
        p = plan(g, rows)
        self.assertIn("the builders' sets differ", p["refused"])
        self.assertIn("cage (holt-bin)", p["refused"])
        self.assertEqual(R.render(p)[0][:26], "img-round plan: refused: t")

    def test_a_mismatched_bko_bin_refuses_too(self):
        g, rows, _ = fresh_world()
        g["czerny"]["facts"]["sets"]["bko-bin"] = "0" * 64
        self.assertIn("czerny (bko-bin)", plan(g, rows)["refused"])

    def test_a_host_left_out_with_hosts_is_not_asked(self):
        g, rows, _ = fresh_world()
        g["cage"]["facts"]["sets"]["holt-bin"] = "0" * 64
        self.assertIsNone(plan(g, rows, builders=["chopin", "cinqcent", "copland", "czerny"])["refused"])

    def test_the_reference_unreached_refuses(self):
        g, rows, _ = fresh_world()
        g["chopin"] = {"probe": {"ok": False}, "facts": None, "error": "timed out after 600s"}
        p = plan(g, rows)
        self.assertIn("the reference chopin gave no facts (timed out", p["refused"])

    def test_an_unreached_builder_is_named_and_skipped(self):
        g, rows, _ = fresh_world()
        g["cage"] = {"probe": {"ok": False}, "facts": None, "error": "no route"}
        g["copland"]["facts"]["errors"] = ["no /s/img_inputs.py: sync stick/ there"]
        p = plan(g, rows, opts={"full": set(PLATS), "no_refresh": False})
        self.assertIsNone(p["refused"])
        self.assertEqual(p["hosts"]["cage"]["why"], "not reached: no route")
        self.assertIn("sync stick/ there", p["hosts"]["copland"]["why"])
        placed = {p["platforms"][x]["host"] for x in PLATS}
        self.assertFalse(placed & {"cage", "copland"})

    def test_a_stale_slim_refuses_the_host_for_linux_only(self):
        g, rows, _ = fresh_world()
        g["copland"]["facts"]["digests"]["linux"] = dig("linux", slim={"path": "slim-20261101", "manifest": sha("old")})
        p = plan(g, rows, opts={"full": set(PLATS), "no_refresh": False})
        lin = p["platforms"]["linux"]
        self.assertIn("slim (slim-20261120 vs slim-20261101)", lin["able"]["copland"])
        self.assertNotEqual(lin["host"], "copland")
        self.assertIsNone(p["platforms"]["netbsd"]["able"]["copland"])
        self.assertTrue(any("linux not on: copland" in x for x in R.render(p)))

    def test_no_slim_at_all_is_refused(self):
        g, rows, _ = fresh_world()
        g["cage"]["facts"]["digests"]["linux"] = dig("linux", iso=None, slim={"path": "slim", "manifest": sha("none")})
        p = plan(g, rows, opts={"full": {"linux"}, "no_refresh": False})
        self.assertIn("ISO", p["platforms"]["linux"]["able"]["cage"])

    def test_another_freebsd_fingerprint_gets_no_freebsd_unit(self):
        g, rows, _ = fresh_world()
        for h in ("copland", "cage", "czerny"):
            g[h]["facts"]["digests"]["freebsd"] = dig("freebsd", builder={"vm": "freebsd15-builder", "kind": "fingerprint",
                                                                        "sha256": sha("fp " + h)})
        p = plan(g, rows, opts={"full": {"freebsd"}, "no_refresh": False})
        r = p["platforms"]["freebsd"]
        self.assertEqual(sorted(h for h, w in r["able"].items() if w is None), ["chopin", "cinqcent"])
        self.assertIn("builder freebsd15-builder", r["able"]["cage"])

    def test_the_media_dont_refuse_a_host_nor_move_the_base(self):
        g, rows, _ = fresh_world()
        g["copland"]["facts"]["digests"]["netbsd"] = dig("netbsd", media=None)    # no MEDIA there yet
        g["chopin"]["facts"]["digests"]["haiku"] = dig("haiku", media={"m": sha("other")})
        p = plan(g, rows, opts={"full": {"netbsd"}, "no_refresh": False})
        self.assertIsNone(p["platforms"]["netbsd"]["able"]["copland"])
        self.assertEqual(p["platforms"]["haiku"]["action"], "reuse")

    def test_unsynced_tests_or_bins_refuse_the_host(self):
        g, rows, _ = fresh_world()
        g["cage"]["facts"]["digests"]["haiku"] = dig("haiku", tests="t9")
        g["czerny"]["facts"]["digests"]["haiku"] = dig("haiku", bins="n9")
        r = plan(g, rows, opts={"full": {"haiku"}, "no_refresh": False})["platforms"]["haiku"]
        self.assertIn("its tests", r["able"]["cage"])
        self.assertIn("its binaries", r["able"]["czerny"])

    def test_a_ceiling_under_the_reservation_refuses_the_host(self):
        g, rows, _ = fresh_world()
        g["czerny"]["probe"] = probe(ceiling=(3.0, 6.0))     # choubox's figures
        r = plan(g, rows, opts={"full": {"illumos"}, "no_refresh": False})["platforms"]["illumos"]
        self.assertIn("is over its ceiling 3 6", r["able"]["czerny"])

    def test_every_unit_unplaceable_is_said(self):
        g, rows, _ = fresh_world()
        for h in BUILDERS:
            g[h]["probe"] = probe(ceiling=(3.0, 6.0))
        p = plan(g, rows, opts={"full": {"haiku"}, "no_refresh": False})
        self.assertEqual(p["left"][0]["platform"], "haiku")
        self.assertIsNone(p["platforms"]["haiku"]["host"])
        self.assertTrue(any(x.startswith("  NOT PLACED: haiku") for x in R.render(p)))


    def test_no_reference_digest_is_said_and_left(self):
        g, rows, _ = fresh_world()
        g["chopin"]["facts"]["digests"]["haiku"] = {"error": "OSError: a glob names no file"}
        p = plan(g, rows)
        r = p["platforms"]["haiku"]
        self.assertEqual(r["action"], "none")
        self.assertIn("a glob names no file", r["why"])
        self.assertEqual([x["platform"] for x in p["left"]], ["haiku"])   # (the CLI exits 1)


class Deal(unittest.TestCase):
    """deal-units.py's image units."""

    def test_the_minutes(self):
        self.assertEqual(D.img_minutes("illumos", "full"), 16.8)
        self.assertEqual(D.img_minutes("linux", "refresh"), 4.0)
        with self.assertRaises(D.DealError):
            D.img_minutes("linux", "reuse")
        out = subprocess.run([sys.executable, os.path.join(STICK, "deal-units.py"), "img-units"],
                             capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertEqual(sorted(x.split("\t")[0] for x in out), sorted(PLATS))

    def test_a_full_set_over_five_hosts(self):
        hs = [D.ImgHost(h, CLASSES[h]) for h in BUILDERS]
        units = [{"platform": p, "action": "full", "able": BUILDERS} for p in PLATS]
        hs, placed, left = D.deal_images(hs, units)
        self.assertEqual(left, [])
        self.assertEqual(set(placed), set(PLATS))
        by = {h.name: [u[0] for u in h.units] for h in hs}
        self.assertTrue(all(len(by[h]) <= 2 for h in BUILDERS))
        self.assertEqual(by["chopin"][0], "illumos")             # longest first, the first host of the tie
        # the longest single unit bounds the round
        self.assertAlmostEqual(max(h.minutes() for h in hs), 16.8)

    def test_chopin_is_penalised(self):
        def first(lpc_chopin):
            hs = [D.ImgHost("chopin", "big", lpc_chopin), D.ImgHost("cinqcent", "big", 0.0)]
            return D.deal_images(hs, [{"platform": "illumos", "action": "full", "able": ["chopin", "cinqcent"]}])[1]
        self.assertEqual(first(0.0), {"illumos": "chopin"})
        self.assertEqual(first(4.0 / 32), {"illumos": "cinqcent"})

    def test_room_free_now_is_preferred(self):
        hs = [D.ImgHost("copland", "small", 0.0, {"linux": False}), D.ImgHost("cage", "small", 0.0, {"linux": True})]
        _, placed, _ = D.deal_images(hs, [{"platform": "linux", "action": "full", "able": ["copland", "cage"]}])
        self.assertEqual(placed, {"linux": "cage"})

    def test_only_able_hosts(self):
        hs = [D.ImgHost("chopin", "big"), D.ImgHost("copland", "small")]
        _, placed, left = D.deal_images(hs, [{"platform": "freebsd", "action": "full", "able": ["copland"]},
                                             {"platform": "haiku", "action": "full", "able": []}])
        self.assertEqual(placed, {"freebsd": "copland"})
        self.assertEqual(left, [("haiku", "no able host")])

    def test_the_plan_penalises_chopin_through_pick_host(self):
        g, rows, _ = fresh_world()
        for h in BUILDERS:
            g[h]["probe"] = probe(load=0.0, nproc=32 if CLASSES[h] == "big" else 16)
        p = plan(g, rows, opts={"full": {"illumos"}, "no_refresh": False})
        self.assertEqual(p["platforms"]["illumos"]["host"], "cinqcent")
        self.assertEqual(p["hosts"]["chopin"]["load_per_core"], 0.12)


class ReserveGib(unittest.TestCase):
    """img_round_facts.reserve_gib: img-lanes.sh's D, at least the test image + 0.5 GiB (I6, §9)."""

    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="t2-i6-res-")
        self.addCleanup(shutil.rmtree, self.t, True)
        self.F = _load("img_round_facts_t", os.path.join(STICK, "img_round_facts.py"))

    def test_the_peak_rule_or_the_image(self):
        d = os.path.join(self.t, "linux")
        os.makedirs(os.path.join(d, "test"))
        self.assertEqual(self.F.reserve_gib(d, "linux"), 1.0)
        with open(os.path.join(d, "test", "t.img"), "wb") as f:
            f.truncate(1374389535)                      # 1.28 GiB, sparse
        self.assertEqual(self.F.reserve_gib(d, "linux"), 1.8)
        TI.wr(os.path.join(d, "peak-kib"), str(4 << 20) + "\n")   # 1.1 x 4 + 0.25 = 4.65: the peak's larger
        self.assertEqual(self.F.reserve_gib(d, "linux"), 4.7)
        self.assertEqual(self.F.reserve_gib(os.path.join(self.t, "netbsd"), "netbsd"), 6.0)


class FactsScript(TI.EnvCase):
    """img_round_facts.py on a fixture host: what the planner reads, and it reads only."""

    def test_facts_of_a_world(self):
        w = TI.make_world(os.path.join(self.t, "a"))
        self.use(w)
        imgs = os.path.join(self.t, "images")
        d = os.path.join(imgs, "netbsd")
        TI.wr(os.path.join(d, "test", "t.img"), "test image\n")
        TI.wr(os.path.join(d, "r.img"), "release image\n")
        TI.wr(os.path.join(d, "SHA256SUMS"), f"{sha('release image' + chr(10))}  ./r.img\n")
        TI.wr(os.path.join(d, "done.exit"), "0\n")
        TI.wr(os.path.join(d, "peak-kib"), "1048576\n")
        TI.wr(os.path.join(d, "provenance.json"), json.dumps(
            {"schema": 1, "test": {"file": "test/t.img", "sha256": sha("test image\n")},
             "release": {"sha256": sha("release image\n")}, "base": {"trees": {"x": 1}, "os_epoch": "2026-11-26"}}))
        TI.wr(os.path.join(imgs, "haiku", "test", ".keep"), "")
        TI.wr(os.path.join(w, "bko-bin", "SHA256SUMS"), "sums\n")
        before = sorted(os.path.join(a, f) for a, _, fs in os.walk(imgs) for f in fs)
        env = dict(os.environ, BKO_IMG_DIR=imgs)
        r = subprocess.run([sys.executable, os.path.join(w, "stick", "img_round_facts.py")], env=env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        f = R.parse_facts(r.stdout)["facts"]
        self.assertEqual(f["errors"], [])
        self.assertEqual(set(f["digests"]), set(PLATS))
        self.assertEqual(f["digests"]["netbsd"], TI.II.digest("netbsd"))
        nb = f["images"]["netbsd"]
        self.assertEqual((nb["done_exit"], nb["test_sha256"], nb["release_sha256"]),
                         (0, sha("test image\n"), sha("release image\n")))
        self.assertEqual(nb["reserve_gib"], 1.4)
        self.assertNotIn("trees", nb["provenance"]["base"])
        self.assertEqual(f["images"]["haiku"]["why"], "no test image (a build moved it away)")
        self.assertEqual(f["sets"]["bko-bin"], sha("sums\n"))
        self.assertIsNone(f["sets"]["holt-bin"])
        self.assertEqual(f["refresh"], {p: p == "linux" for p in PLATS})   # I3: Linux refreshes
        self.assertEqual(before, sorted(os.path.join(a, x) for a, _, fs in os.walk(imgs) for x in fs))

    def test_no_img_inputs_is_said(self):
        s = os.path.join(self.t, "s")
        os.makedirs(s)
        shutil.copy2(os.path.join(STICK, "img_round_facts.py"), s)
        env = dict(os.environ, S=s, BKO_WORK=self.t)
        r = subprocess.run([sys.executable, os.path.join(s, "img_round_facts.py")], env=env,
                           capture_output=True, text=True, timeout=60)
        f = R.parse_facts(r.stdout)["facts"]
        self.assertIn("sync stick/ there", f["errors"][0])

    def test_gather_uses_the_tables_ssh(self):
        H = R.hosts_mod()
        seen = []
        real = R.subprocess.run

        def fake(argv, input=None, **kw):
            seen.append((argv, input))
            return subprocess.CompletedProcess(argv, 0, "host x\nend\nfacts {\"errors\": []}\n", "")
        R.subprocess.run = fake
        try:
            g = R.gather(H, ["copland"], [("S", "/p/stick")], timeout=5)
        finally:
            R.subprocess.run = real
        self.assertEqual(g["copland"]["facts"], {"errors": []})
        self.assertIn(H.ssh("copland"), seen[0][0])
        self.assertEqual(seen[0][0][-1], "sh -s")
        self.assertTrue(seen[0][1].startswith("export S=/p/stick\n"))

    def test_the_script_sent_is_probe_then_facts(self):
        s = R.facts_script([("S", "/x y")])
        self.assertTrue(s.startswith("export S='/x y'\n"))
        self.assertIn("cmp-run.sh --ceiling", s)
        self.assertIn("python3 -I -B - <<'IMG_ROUND_EOF'", s)


class Cli(unittest.TestCase):
    """img-round.sh plan from a facts directory and a fixture ledger: plan.json, the exit codes."""

    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="t2-i2-")
        self.addCleanup(shutil.rmtree, self.t, True)
        self.facts = os.path.join(self.t, "facts")
        os.makedirs(os.path.join(self.t, "mac", "ledger"))
        os.makedirs(self.facts)

    def write(self, g, rows):
        for h, x in g.items():
            p = x["probe"]
            text = (f"host {h}\nceiling_rc 0\nceiling {p['ceiling'][0]} {p['ceiling'][1]}\nfree_rc 0\n"
                    f"free {p['free'][0]} {p['free'][1]}\nwaiting 0\nafter {p['free'][0]} {p['free'][1]}\n"
                    f"line_room {p['free'][0]} {p['free'][1]}\nnproc 16\nload 0.5\nend\n"
                    "facts " + json.dumps(x["facts"]) + "\n")
            with open(os.path.join(self.facts, f"{h}.facts"), "w") as f:
                f.write(text)
        with open(os.path.join(self.t, "mac", "ledger", "mac.jsonl"), "w") as f:
            for i, r in enumerate(rows):
                r = dict(r, v=1, id=f"mac-20261126T120000Z-{i:08x}")
                r["c"] = ledger.row_c(r)
                f.write(json.dumps(r, sort_keys=True) + "\n")

    def plan_json(self):
        with open(os.path.join(self.t, "rounds", "r1", "plan.json"), encoding="utf-8") as f:
            return json.load(f)

    def run_plan(self, *args):
        env = dict(os.environ, T2_MAC_ROOT=os.path.join(self.t, "mac"), BKO_ROUND_DIR=os.path.join(self.t, "rounds"))
        return subprocess.run([os.path.join(STICK, "img-round.sh"), "plan", "--facts-dir", self.facts,
                               "--hosts", "chopin,cinqcent,copland", "--today", "2026-11-27", *args, "r1"],
                              env=env, capture_output=True, text=True, timeout=120)

    def test_reuse_x7_and_plan_json(self):
        g, rows, _ = fresh_world()
        self.write({h: g[h] for h in ("chopin", "cinqcent", "copland")}, rows)
        r = self.run_plan()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("reuse x7", r.stdout)
        pj = self.plan_json()
        self.assertEqual(pj["run"], "r1")
        self.assertEqual(sorted(pj["builders"]), ["chopin", "cinqcent", "copland"])   # (in hosts.py's tie order)
        self.assertTrue(os.path.isfile(os.path.join(self.t, "rounds", "r1", "facts.json")))

    def test_full_builds_dealt(self):
        g, rows, _ = fresh_world()
        self.write({h: g[h] for h in ("chopin", "cinqcent", "copland")}, rows)
        r = self.run_plan("--full", "all")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        pj = self.plan_json()
        self.assertEqual({pj["platforms"][p]["action"] for p in PLATS}, {"full"})
        self.assertEqual(sorted(u["platform"] for d in pj["deal"] for u in d["units"]), sorted(PLATS))

    def test_refused_exits_3_and_usage_2(self):
        g, rows, _ = fresh_world()
        g["copland"]["facts"]["sets"]["holt-bin"] = "0" * 64
        self.write({h: g[h] for h in ("chopin", "cinqcent", "copland")}, rows)
        r = self.run_plan()
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("copland (holt-bin)", r.stdout)
        self.assertEqual(self.run_plan("--full", "beos").returncode, 2)
        r = subprocess.run([os.path.join(STICK, "img-round.sh"), "plan", "--hosts", "choubox", "x"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("choubox is not a build host", r.stderr)
        r = subprocess.run([os.path.join(STICK, "img-round.sh"), "deal", "x"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("img-round.sh run", r.stderr)   # (the usage: run and fanout are I8's, built)


if __name__ == "__main__":
    unittest.main()
