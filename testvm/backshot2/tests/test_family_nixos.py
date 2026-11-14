"""families/nixos.py: generations, the initrd's layers, no versions, tier 0 plan only."""

import unittest

import corpus_cases as K
from bs2lib import dumpx, families as F, rules, tables
from bs2lib.families import nixos


class TestNixOS(unittest.TestCase):
    D = K.dump("nixos2605")

    def test_family(self):
        h = F.hooks("nixos2605", self.D)
        self.assertEqual(h.names, ["nixos"])
        self.assertEqual(h.tier0, "plan")
        self.assertEqual(h.versions, {})
        self.assertIsNone(tables.version(self.D, "e2fsprogs", h))
        # No version: the oldest row (e2fsprogs 1.42's).
        self.assertEqual(tables.allowed(self.D, "e2fsprogs", "block_size", h), [1024, 2048, 4096])

    def test_generations(self):
        self.assertEqual(nixos.generations(self.D), {2, 3})
        e, dr = K.edited(self.D, "renumber", disk="vda", **{"from": 2, "to": 4})
        self.assertTrue(any("generations" in w for w in K.reasons(rules.check(self.D, dr, [e]), 3)))
        # One generation: the dump holds all its parameters, and the quirk is silent.
        one = dumpx.clone(self.D)
        g = one["boot"]["grub"]["grub_cfg"]
        g["entries"] = [t for t in g["entries"] if "Configuration 2" not in t]
        self.assertEqual(nixos.generations(one), {3})
        self.assertEqual(nixos.check_generations(one, dr, [e]), [])

    def test_no_renumber_drawn(self):
        for c in K.cases("nixos2605"):
            self.assertFalse(any(e["op"] in ("renumber", "swap_order") for e in c["edits"]), c["seed"])

    def test_initrd_layers(self):
        h = F.hooks("nixos2605", self.D)
        self.assertTrue(any("initrd" in w for w in K.reasons(h.quirk_check(self.D, K.with_type(self.D, "vda1", "xfs"), []))))

    def test_store_texts(self):
        x = dumpx.clone(self.D)
        x["boot"]["store"] = {"generations": [{"number": 1, "parameters": {"kernel_arguments": ["root=/dev/vda3"]}}]}
        self.assertEqual(nixos.generations(x), {1, 2, 3})
        dk = dumpx.disk(x)
        self.assertTrue(dumpx.names_by_number(x, dk, dumpx.part(dk, 3), F.hooks("n", x)))


if __name__ == "__main__":
    unittest.main()
