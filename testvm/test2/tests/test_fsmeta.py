"""testvm/compare/fsmeta.py's inode-flags judgement (2026-11-18): an m (FS_NOCOMP_FL) a restore
gains alone on Btrfs is Btrfs's own and left out; an m lost, any other flag, or an m on another
file system still differs."""

import importlib.util
import os
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
FSMETA = os.path.join(os.path.dirname(os.path.dirname(TESTS)), "compare", "fsmeta.py")
spec = importlib.util.spec_from_file_location("fsmeta_under_test", FSMETA)
fsmeta = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fsmeta)

M = fsmeta.FS_NOCOMP_FL
IMMUTABLE, APPEND, NOCOW, COMPR = 0x10, 0x20, 0x00800000, 0x4


def entry(kind="file", flags=0, btrfs=None):
    e = {"type": kind, "mode": 0o644, "uid": 0, "gid": 0, "mtime": 1, "atime": 1, "xattrs": {}, "flags": flags,
         "projid": 0}
    if kind == "file":
        e.update(size=3, blocks=8, sha256="ab", holes=False, hole_blocks=0)
    if btrfs:
        e["btrfs"] = True
    return e


def judged(bflags, aflags, after_btrfs=True, kind="file"):
    before = {"t/f": entry(kind, bflags, btrfs=True)}
    after = {"t/f": entry(kind, aflags, btrfs=after_btrfs)}
    return fsmeta.differences(before, after), fsmeta.nocomp_gained(before, after)


class NocompGained(unittest.TestCase):
    def test_m_gained_on_btrfs_is_left_out_and_named(self):
        for kind in ("file", "dir"):
            for b in (0, NOCOW, IMMUTABLE | COMPR):
                diff, gained = judged(b, b | M, kind=kind)
                self.assertEqual(diff, {}, (kind, hex(b)))
                self.assertEqual(gained, ["t/f"])

    def test_m_gained_beside_ext4s_own_index_flag(self):
        # FLAGS_FS_INTERNAL is never compared: m alone is still what changed.
        diff, gained = judged(fsmeta.FLAGS_FS_INTERNAL, M)
        self.assertEqual((diff, gained), ({}, ["t/f"]))

    def test_m_lost_still_differs(self):
        diff, gained = judged(M, 0)
        self.assertEqual((diff, gained), ({"inode flags": ["t/f"]}, []))
        diff, gained = judged(M | NOCOW, NOCOW)
        self.assertEqual((diff, gained), ({"inode flags": ["t/f"]}, []))

    def test_m_gained_off_btrfs_still_differs(self):
        # An after record without the btrfs mark: f2fs (which has m too), ext4, XFS, an old record.
        diff, gained = judged(0, M, after_btrfs=False)
        self.assertEqual((diff, gained), ({"inode flags": ["t/f"]}, []))

    def test_m_gained_with_another_flag_still_differs(self):
        for other in (IMMUTABLE, APPEND, NOCOW, COMPR):
            diff, gained = judged(0, M | other)
            self.assertEqual((diff, gained), ({"inode flags": ["t/f"]}, []), hex(other))
            diff, gained = judged(other, M)          # m gained, the other lost
            self.assertEqual((diff, gained), ({"inode flags": ["t/f"]}, []), hex(other))

    def test_every_other_flag_still_differs_on_btrfs(self):
        """The planted inode-flag difference: each user-visible flag but m and ext4's index,
        gained or lost, onto Btrfs."""
        bits = [1 << i for i in range(32) if fsmeta.FS_FL_USER_VISIBLE & (1 << i)
                and (1 << i) not in (M, fsmeta.FLAGS_FS_INTERNAL)]
        self.assertGreater(len(bits), 10)
        for bit in bits:
            for b, a in ((0, bit), (bit, 0), (M, M | bit), (M | bit, M)):
                diff, gained = judged(b, a)
                self.assertEqual(diff.get("inode flags"), ["t/f"], (hex(b), hex(a)))
                self.assertEqual(gained, [], (hex(b), hex(a)))

    def test_flags_unknown_on_a_side(self):
        # As before: only where both sides have flags (a file system without the ioctl).
        for b, a in ((None, M), (0, None)):
            diff, gained = judged(b, a)
            self.assertEqual((diff, gained), ({}, []))

    def test_type_changed_is_not_named(self):
        before = {"t/f": entry("file", 0, btrfs=True)}
        after = {"t/f": entry("dir", M, btrfs=True)}
        self.assertEqual(fsmeta.differences(before, after), {"type": ["t/f"]})
        self.assertEqual(fsmeta.nocomp_gained(before, after), [])

    def test_other_kinds_beside_still_count(self):
        before = {"t/f": entry("file", 0, btrfs=True), "t/g": entry("file", 0, btrfs=True)}
        after = {"t/f": entry("file", M, btrfs=True), "t/g": dict(entry("file", M, btrfs=True), mode=0o600)}
        self.assertEqual(fsmeta.differences(before, after), {"mode": ["t/g"]})
        self.assertEqual(fsmeta.nocomp_gained(before, after), ["t/f", "t/g"])


if __name__ == "__main__":
    unittest.main()
