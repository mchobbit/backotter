"""BackShot2's source table (bs2lib/sources.py, A2) on the real corpus: each guest's class
(built, or skipped with the piece that takes it), its firmware, and its seconds a case.
Pure: nothing here reaches a host or loads bs2.

    python3 -m unittest discover -s testvm/backshot2/tests
"""

import unittest

import corpus_cases as K
import test_families as TF
from bs2lib import dumpx, sources as S

# The Linux sources with ZFS, md or more than one disk (docs/design/bs2-all.md "Sources").
A3 = ["cachyos-zfs", "chimera-zfs", "debian13-zfs", "debian13-stack", "fedora44-raid", "proxmox9", "proxmox9-mirror",
      "rocky10-raid5", "ubuntu26-raid", "ubuntu26-zfs", "ubuntu26-zfsmirror"]


class Table(unittest.TestCase):
    def test_every_linux_guest_has_a_class(self):
        t = S.table()
        for g in TF.EXPECTED:
            if g in K.guests():
                self.assertIn(g, t, g)
                self.assertIn(t[g].status, (S.BUILT, S.SKIPPED), g)
                if not t[g].built:
                    self.assertTrue(t[g].piece and t[g].why, t[g])

    def test_a3_from_the_dumps(self):
        for g in A3:
            c = S.classify(g)
            self.assertEqual((c.status, c.piece), (S.SKIPPED, "A3"), c)
        self.assertIn("ZFS", S.classify("ubuntu26-zfs").why)
        self.assertIn("md", S.classify("fedora44-raid").why)
        self.assertIn("2 disks", S.classify("proxmox9-mirror").why)
        self.assertIn("ZFS", S.classify("ubuntu24").why)   # (its root is a pool)

    def test_non_linux_take_their_planned_piece(self):
        for g, piece in TF.PLANNED_GUESTS.items():
            if g in K.guests():
                self.assertEqual((S.classify(g).status, S.classify(g).piece), (S.SKIPPED, piece), g)

    def test_the_rest_skipped_with_a_lane(self):
        c = S.classify("guix15-luks")
        self.assertEqual((c.status, c.piece), (S.SKIPPED, "later"))
        self.assertIn("LUKS1", c.why)
        self.assertEqual(S.classify("uc24-fde").piece, "later")          # not a HoltTest2 source
        self.assertEqual(S.classify("bs1-ubuntu2610-zfsenc").piece, "A8")   # the Thinkpad's
        self.assertIsNone(S.classify("debian13-arm64").piece)
        self.assertNotIn("debian13-arm64", S.table())
        self.assertNotIn("arm64-grub-shim", S.table())                    # (an aarch64 fixture)
        self.assertIsNone(S.classify("nosuch").piece)
        self.assertIn("skipped (A3)", S.classify("cachyos-zfs").phrase())
        # Only guix15-luks is LUKS1 among the corpus's LUKS sources: the rest are LUKS2.
        l1 = [g for g in S.candidates() if any(v != "2" for v in S.luks_versions(S.dump(g)).values())]
        self.assertEqual(l1, ["guix15-luks"])

    def test_built(self):
        b = S.built()
        self.assertEqual(b, sorted(b))
        for g in ("alpine324", "alpine324-uefi", "guix15", "guix15-bios", "debian13", "mint22", "vanilla3", "kinoite44",
                  "fedora44-uefi", "nixos2605", "solus49", "ubuntu26-luks"):
            self.assertIn(g, b)
        for g in b:
            if g not in S.ALPINE_SOURCES:
                d = S.dump(g)
                self.assertIsNone(S.multi(d), g)
                self.assertEqual(len(dumpx.disks(d)), 1, g)
        self.assertGreaterEqual(len(b), 45)
        self.assertEqual(set(S.others()) & set(S.ALPINE_SOURCES + S.GUIX_SOURCES), set())
        self.assertEqual(list(S.others()), sorted(S.others()))

    def test_uefi_as_before_for_alpine_and_guix(self):
        self.assertEqual({s for s in S.ALPINE_SOURCES + S.GUIX_SOURCES if S.uefi(s)},
                         {"alpine324-uefi", "alpine324-lvm", "alpine324-luks", "guix15"})
        self.assertTrue(S.uefi("vanilla3"))
        self.assertFalse(S.uefi("debian13"))


class Seconds(unittest.TestCase):
    def test_measured_and_estimated(self):
        self.assertEqual(S.secs("alpine324-uefi", 1), (86, "measured"))
        self.assertEqual(S.secs("alpine324-luks", 2), (98, "measured"))
        self.assertEqual(S.secs("guix15", 1), (235, "measured"))
        self.assertEqual(S.secs("guix15-bios", 1), (153, "measured"))
        self.assertEqual(S.secs("guix15-bios", 2), (153, "est."))
        self.assertEqual(S.secs("mint22", 1), (round(0.55 * S.holt_rows()["mint22"][0]), "est."))
        self.assertEqual(S.secs("debian13", 1), (S.FLOOR_S, "est."))   # 0.55 x 163 under the floor
        self.assertEqual(S.secs("vanilla3", 2), S.secs("vanilla3", 1))
        self.assertEqual(S.secs("debian13", 0), (0.3, "measured"))
        for s in S.built():
            self.assertGreaterEqual(S.secs(s, 1)[0], 60, s)


if __name__ == "__main__":
    unittest.main()
