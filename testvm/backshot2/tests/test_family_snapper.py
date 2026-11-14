"""families/snapper.py: a btrfs whose snapshot load makes as a full copy doesn't shrink."""

import unittest

import corpus_cases as K
from bs2lib import dumpx, families as F, rules
from bs2lib.families import snapper


class TestSnapper(unittest.TestCase):
    def test_matches(self):
        for g in ("tumbleweed", "garuda", "omarchy"):
            self.assertIn("snapper", F.hooks(g, K.dump(g)).names, g)
        self.assertNotIn("snapper", F.hooks("fedora44", K.dump("fedora44")).names)

    def test_orphans(self):
        d = K.dump("tumbleweed")
        self.assertEqual(snapper.orphaned(d), {})
        # (No phase 1 edit resizes a btrfs; the quirk guards the edits later lanes add.)
        small = shrunk(d, "vda3", 409600)
        self.assertEqual(snapper.check_orphans(d, small, []), [])
        # A snapshot whose origin is gone: load makes it a plain subvolume, a full copy.
        x = dumpx.clone(d)
        sub = next(s for s in x["storage"]["btrfs"][0]["subvolumes"] if s.get("parent_uuid"))
        sub["parent_uuid"] = "bc000000-0000-4000-8000-0000000000ff"
        self.assertTrue(snapper.orphaned(x))
        self.assertTrue(any("full copies" in w for w in K.reasons(rules.check(x, shrunk(x, "vda3", 409600), []), 1)))
        e, dr = K.edited(x, "renumber", disk="vda", **{"from": 3, "to": 4})
        self.assertEqual(snapper.check_orphans(x, shrunk(x, "vda3", -100), [e]), [])


def shrunk(d, name, sectors):
    x = dumpx.clone(d)
    p = dumpx.devices(x)[name]
    p["sectors"] -= sectors
    if p.get("size_bytes"):
        p["size_bytes"] -= sectors * 512
    return x


if __name__ == "__main__":
    unittest.main()
