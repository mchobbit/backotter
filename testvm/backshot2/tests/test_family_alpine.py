"""families/alpine.py: the recipe, the mkinitfs rule, fail-003's steer (lifted)."""

import unittest

import corpus_cases as K
from bs2lib import content as CT, families as F, generate as G, rules


class TestAlpine(unittest.TestCase):
    D = K.dump("alpine324")

    def test_family(self):
        h = F.hooks("alpine324", self.D)
        self.assertEqual(h.names, ["alpine"])
        self.assertEqual(h.tier0, "chroot")
        # fail-001 fixed (rustic_core 6b1e432): the deep tree goes past PATH_MAX.
        self.assertEqual(h.recipe(self.D), "alpine-small-1")
        self.assertNotIn("fail-003", h.known)

    def test_recipes_are_alpines(self):
        self.assertEqual(CT.recipe_of("alpine-small-2")["deep_bytes"], 3800)
        self.assertEqual(CT.recipe_of("alpine-small-1")["deep_bytes"], 4300)
        self.assertNotIn("alpine-small-2", CT.RECIPES)
        with self.assertRaises(KeyError):
            CT.recipe_of("no-such-recipe")

    def test_content_on_most_cases(self):
        n = sum(1 for c in K.cases("alpine324") if (c.get("content") or {}).get("recipe") == "alpine-small-1")
        self.assertGreater(n, 150)
        self.assertFalse(any((c.get("content") or {}).get("recipe") == "alpine-small-2" for c in K.cases("alpine324")))

    def test_mkinitfs_rule(self):
        h = F.hooks("alpine324", self.D)
        self.assertEqual(h.quirk_check(self.D, self.D, []), [])
        bad = h.quirk_check(self.D, K.with_type(self.D, "vda1", "btrfs"), [])
        self.assertEqual(len(bad), 1)
        self.assertIn("mkinitfs", bad[0][1])
        self.assertTrue(any("mkinitfs" in w for w in K.reasons(rules.check(self.D, K.with_type(self.D, "vda3", "ext4"), []), 3)))

    def test_no_removable_only_while_known(self):
        # fail-003 is fixed (bko-holt e8986b2): its steer drops --removable only
        # while generate.KNOWN names it, for a replay of an older bko-holt.
        h = F.hooks("alpine324", self.D)
        self.assertNotIn("fail-003", G.KNOWN)
        self.assertEqual(h.steer(self.D, self.D, [], ["--removable", "--no-nvram"]), ["--removable", "--no-nvram"])
        known = G.KNOWN
        try:
            G.KNOWN = tuple(known) + ("fail-003",)
            self.assertEqual(h.steer(self.D, self.D, [], ["--removable", "--no-nvram"]), ["--no-nvram"])
        finally:
            G.KNOWN = known


if __name__ == "__main__":
    unittest.main()
