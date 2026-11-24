"""families/linux.py: generic Linux, the lowest priority; its content drift (A7) is test_family_content.py's."""

import unittest

import corpus_cases as K
from bs2lib import families as F, rules


class TestLinux(unittest.TestCase):
    def test_matches_linux_only(self):
        self.assertEqual(F.family_of("debian13", K.dump("debian13")).name, "linux")
        self.assertIsNone(F.family_of("freebsd15", K.dump("freebsd15")))
        lin = next(f for f in F.REGISTRY.families if f.name == "linux")
        self.assertTrue(lin.matches("alpine324", K.dump("alpine324")))   # but alpine is more specific
        self.assertEqual(F.family_of("alpine324", K.dump("alpine324")).name, "alpine")

    def test_generic(self):
        for g in ("debian13", "gentoo", "chimera", "aerynos"):
            h = F.hooks(g, K.dump(g))
            self.assertEqual(h.family.name, "linux")
            self.assertEqual(h.tier0, "chroot")
        # Content drift since GENERATOR "6": the recipe of /srv's filesystem.
        self.assertEqual(F.hooks("debian13", K.dump("debian13")).recipe(K.dump("debian13")), "btrfs-1")
        self.assertEqual(F.hooks("gentoo", K.dump("gentoo")).recipe(K.dump("gentoo")), "ext-1")
        self.assertTrue(any("content" in c for c in K.cases("chimera")))
        self.assertFalse(any("content" in c for c in K.cases("chimera-zfs")))

    def test_chimera_mbr_partuuid_is_generic_rule3(self):
        # chimera's command line names / as PARTUUID=SIG-03: renumbering it is refused by the generic rule 3.
        d = K.dump("chimera")
        e, dr = K.edited(d, "renumber", disk="vda", **{"from": 3, "to": 4})
        self.assertTrue(any("names it by number" in w for w in K.reasons(rules.rule3(d, dr, [e]), 3)))


if __name__ == "__main__":
    unittest.main()
