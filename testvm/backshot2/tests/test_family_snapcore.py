"""families/snapcore.py: Ubuntu Core's gadget-fixed partitions keep their size
(only ubuntu-data may change size, as load requires: holt/src/load/snapcore.rs).
BackShot2All bs2all-20261125-120413 drew 465 uc24 cases that resized
ubuntu-boot or ubuntu-save, every one load's refusal at tier 0."""

import unittest

import corpus_cases as K
from bs2lib import case as C, dumpx, edits as E, families as F, rules
from bs2lib.families import snapcore


def gadget_reasons(found):
    return [w for r, w in found if r == 3 and "only ubuntu-data may change size" in w]


class TestSnapcore(unittest.TestCase):
    def test_matches(self):
        for g in ("uc24", "uc24-fde"):
            self.assertIn("snapcore", F.hooks(g, K.dump(g)).names, g)
        for g in ("debian13", "ubuntu24", "kinoite44"):
            self.assertNotIn("snapcore", F.hooks(g, K.dump(g)).names, g)
        d = K.dump("uc24")
        self.assertEqual(sorted(p["partlabel"] for p in snapcore.fixed(d)),
                         ["BIOS Boot", "ubuntu-boot", "ubuntu-save", "ubuntu-seed"])

    def test_only_data_resizes(self):
        d = K.dump("uc24")
        n = {p["partlabel"]: p["number"] for p in dumpx.disk(d)["partitions"]}
        # uc24-7287d64abb5f8844 of that run: ubuntu-boot 17 sectors less ("750 MiB instead of 750 MiB").
        e, dr = K.edited(d, "resize", disk="vda", partition=n["ubuntu-boot"], by_sectors=-17, grow_fs=True)
        w = gadget_reasons(rules.check(d, dr, [e]))
        self.assertEqual(len(w), 1, w)
        self.assertIn("ubuntu-boot (vda3) would be 1535983 sectors, not the gadget's 1536000", w[0])
        e, dr = K.edited(d, "resize", disk="vda", partition=n["ubuntu-save"], by_sectors=-1024, grow_fs=True)
        self.assertTrue(gadget_reasons(rules.check(d, dr, [e])))
        # (BIOS Boot, 1 MiB at the gadget's 1 MiB, can't be resized by any edit today; fixed() holds it too.)
        e, dr = K.edited(d, "resize", disk="vda", partition=n["ubuntu-data"], by_sectors=-2048, grow_fs=True)
        self.assertEqual(gadget_reasons(rules.check(d, dr, [e])), [])

    def test_found_when_renumbered(self):
        d = K.dump("uc24")
        e1, dr = K.edited(d, "renumber", disk="vda", **{"from": 4, "to": 9})
        e2 = E.build(dr, "resize", {"disk": "vda", "partition": 9, "by_sectors": -4096, "grow_fs": True})
        dr = E.apply(dr, [e2])
        w = gadget_reasons(rules.check(d, dr, [e1, e2]))
        self.assertTrue(w and "ubuntu-save (vda9)" in w[0], w)

    def test_snapcore_record_follows(self):
        # uc24-2e292af657cf51f9 (seed 14): tier 1's judge found the drifted dump's
        # boot.snapcore still the source's ("partitions.1.number: 3 -> 127").
        d = K.dump("uc24")
        es = []
        dr = d
        for op, params in (("renumber", {"disk": "vda", "from": 3, "to": 127}),
                           ("resize", {"disk": "vda", "partition": 5, "by_sectors": -28, "grow_fs": True}),
                           ("gap_before", {"disk": "vda", "partition": 127, "sectors": 28})):
            e = E.build(dr, op, params)
            dr = E.apply(dr, [e])
            es.append(e)
        dr = E.apply(d, es)
        dk = dumpx.disk(dr)
        for r in dr["boot"]["snapcore"]["partitions"]:
            p = next(q for q in dk["partitions"] if q["uuid"] == r["partuuid"])
            self.assertEqual((r["number"], r["device"], r["start_bytes"], r["size_bytes"]),
                             (p["number"], p["name"], p["start_bytes"], p["size_bytes"]), r["role"])
        boot = next(r for r in dr["boot"]["snapcore"]["partitions"] if r["role"] == "boot")
        self.assertEqual((boot["number"], boot["device"], boot["start_bytes"]), (127, "vda127", 1260388352 + 28 * 512))
        self.assertEqual(d["boot"]["snapcore"]["partitions"][1]["number"], 3, "the source is untouched")
        # uc24-50af002204b5525a: BIOS Boot renumbered ("boot_areas.bios_boot.number: 1 -> 8").
        e = E.build(d, "renumber", {"disk": "vda", "from": 1, "to": 8})
        bb = E.apply(d, [e])["boot"]["snapcore"]["boot_areas"]["bios_boot"]
        self.assertEqual((bb["partition"], bb["number"], bb["start_bytes"]), ("vda8", 8, 1048576))

    def test_no_draw_resizes_the_gadgets(self):
        for g in ("uc24", "uc24-fde"):
            d = K.dump(g)
            fixed = {p["uuid"]: p["sectors"] for p in snapcore.fixed(d)}
            resized = 0
            for c in K.cases(g):
                dr = C.replay(c, d)
                for p in dumpx.disk(dr)["partitions"]:
                    if p["uuid"] in fixed:
                        self.assertEqual(p["sectors"], fixed[p["uuid"]], f"{g} seed {c['seed']}: {p['partlabel']}")
                resized += any(e["op"] == "resize" for e in c["edits"])
            self.assertGreater(resized, 50, f"{g}: ubuntu-data is still resized")


if __name__ == "__main__":
    unittest.main()
