"""families/lvm_thin.py: no drift of the LVs lvcreate makes for a thin pool (vanilla3's vos-root/root)."""

import unittest

import corpus_cases as K
from bs2lib import dumpx, edits as E, families as F, fitcalc, rules
from bs2lib.families import lvm_thin

PARTS = {"vos-root": {"root_tdata", "root_tmeta", "lvol0_pmspare"}}


def thin_reasons(found):
    return [w for r, w in found if r == 2 and "lvcreate makes" in w]


class TestLvmThin(unittest.TestCase):
    def test_matches(self):
        d = K.dump("vanilla3")
        self.assertIn("lvm-thin", F.hooks("vanilla3", d).names)
        self.assertEqual(lvm_thin.pool_parts(d), PARTS)
        for g in ("rocky10-lvm", "ubuntu26-luks", "debian13"):
            self.assertNotIn("lvm-thin", F.hooks(g, K.dump(g)).names, g)
            self.assertEqual(lvm_thin.pool_parts(K.dump(g)), {}, g)

    def test_pool_parts_refused(self):
        d = K.dump("vanilla3")
        v = dumpx.vg(d, "vos-root")
        for lv, by in (("root_tdata", 5), ("root_tdata", -5), ("root_tmeta", 3)):
            e, dr = K.edited(d, "lv_resize", vg="vos-root", lv=lv, segments=E.resize_lv_pieces(v, dumpx.lv(v, lv), by))
            self.assertTrue(thin_reasons(rules.check(d, dr, [e])), (lv, by))
        x = dumpx.lv(v, "root_tmeta")
        pv, pe, n = E.pieces_of(x)[0]
        e, dr = K.edited(d, "lv_split", vg="vos-root", lv="root_tmeta", segments=[[pv, pe, n - 4], [pv, 5657 - 4, 4]])
        self.assertTrue(thin_reasons(rules.check(d, dr, [e])))
        # The linear LV beside the pool keeps both edits.
        e, dr = K.edited(d, "lv_resize", vg="vos-root", lv="init", segments=E.resize_lv_pieces(v, dumpx.lv(v, "init"), 4))
        self.assertEqual(thin_reasons(rules.check(d, dr, [e])), [])

    def test_draws(self):
        lvs = {(e["vg"], e["lv"]) for c in K.cases("vanilla3") for e in c["edits"] if e["op"] in lvm_thin.EDITS}
        self.assertEqual(lvs, {("vos-root", "init")})

    def test_no_fit_shrinks_the_thin_group(self):
        # The thin group's PV made the largest partition: judge.py's fit plan
        # (and load) refuse to shrink it, so the generator draws no such fit.
        d = dumpx.clone(K.dump("vanilla3"))
        dk = dumpx.disk(d)
        p4 = dumpx.part(dk, 4)
        p4["sectors"] = 2097152
        end = p4["start_lba"] + p4["sectors"]
        dk["size_bytes"] = (end + 33) * 512
        dk["partition_table"]["last_usable_lba"] = end - 1
        targets = {"vda": dk["size_bytes"] - (64 << 20)}
        self.assertEqual(fitcalc.plan(d, targets, None)["disks"][0]["name"], "vda3")
        self.assertIsNone(fitcalc.pct_needed(d, targets))


if __name__ == "__main__":
    unittest.main()
