"""The initramfs generators' traits (dracut.py, mkinitcpio.py, booster.py,
initramfs_tools.py): each refuses a drift that changes the layers its
image must open, and lets every drawn case through."""

import unittest

import corpus_cases as K
from bs2lib import families as F

CASES = {
    "dracut": ("fedora44-uefi", "vda2"),
    "mkinitcpio": ("arch", "vda1"),
    "booster": ("pikaos4", "vda2"),
    "initramfs-tools": ("debian13", "vda1"),
}


class TestInitramfsTraits(unittest.TestCase):
    def test_layers(self):
        for trait, (g, dev) in CASES.items():
            d = K.dump(g)
            h = F.hooks(g, d)
            self.assertIn(trait, h.names)
            self.assertEqual(h.quirk_check(d, d, []), [], g)
            bad = K.reasons(h.quirk_check(d, K.with_type(d, dev, "jfs"), []), 3)
            self.assertTrue(any(w.startswith(trait + ":") for w in bad), (trait, bad))

    def test_matches_by_generator(self):
        for g in K.linux_guests():
            d = K.dump(g)
            gen = (d.get("boot") or {}).get("initramfs", {}).get("generator")
            names = F.hooks(g, d).names
            for trait in CASES:
                self.assertEqual(trait in names, gen == trait, (g, gen))

    def test_drawn_cases_keep_layers(self):
        from bs2lib.families.base import layers
        from bs2lib import case as C
        for g, _ in CASES.values():
            d = K.dump(g)
            for c in K.cases(g)[:100]:
                self.assertEqual(layers(C.replay(c, d)), layers(d), (g, c["seed"]))


if __name__ == "__main__":
    unittest.main()
