"""families/guix.py: Guix System's own rules (swap and older generations'
names the dump lacks, the store initrd's layers), its rule 3 texts, its
recipe, tier 0's chroot through the system profile, and what it draws."""

import os
import tempfile
import unittest

import corpus_cases as K
from bs2lib import dumpx, families as F, rules, tables, tier0
from bs2lib.families import guix


def two_generations(d):
    """D with a second generation in boot.store (the corpus dumps have one)."""
    x = dumpx.clone(d)
    st = x["boot"]["store"]
    g = dict(st["generations"][0], number=2)
    st["generations"].append(g)
    st["current"] = 2
    return x


class TestGuix(unittest.TestCase):
    U = K.dump("guix15")
    B = K.dump("guix15-bios")

    def test_family(self):
        for g, d in (("guix15", self.U), ("guix15-bios", self.B)):
            h = F.hooks(g, d)
            self.assertEqual(h.names, ["guix"], g)   # no generic Linux trait: no initramfs generator, store GRUB
            self.assertEqual(h.tier0, "chroot")
            self.assertEqual(h.recipe(d), "guix-small-1")
            self.assertIsNone(F.skipped(g, d))
        self.assertEqual(K.of_family("guix"), ["guix15", "guix15-bios"])
        # The manifest's versions: e2fsprogs 1.47.2's rows; util-linux unrecorded.
        h = F.hooks("guix15", self.U)
        self.assertEqual(tables.version(self.U, "e2fsprogs", h), "1.47.2")
        self.assertEqual(tables.version(self.B, "btrfs-progs", F.hooks("guix15-bios", self.B)), "6.16")

    def test_swap_is_never_renumbered(self):
        e, dr = K.edited(self.U, "renumber", disk="vda", **{"from": 2, "to": 9})
        why = K.reasons(rules.check(self.U, dr, [e]), 3)
        self.assertTrue(any("swap-devices" in w for w in why), why)
        for g in ("guix15", "guix15-bios"):
            for c in K.cases(g):
                for x in c["edits"]:
                    if x["op"] == "renumber":
                        self.assertNotEqual(x["from"], 2, (g, c["seed"]))   # vda2, the swap, first in every chain
                    self.assertNotEqual(x["op"], "swap_order", (g, c["seed"]))   # each pair has the swap in it

    def test_root_renumbered_on_uefi(self):
        # Allowed: grub.cfg, fstab and root= name / by UUID.  load's keeping of
        # grubx64.efi's "(,gpt3)" prefix is a bko-holt finding, not a rule.
        e, dr = K.edited(self.U, "renumber", disk="vda", **{"from": 3, "to": 5})
        self.assertEqual(rules.check(self.U, dr, [e]), [])
        n = sum(1 for c in K.cases("guix15") if any(x["op"] == "renumber" and x["from"] == 3 for x in c["edits"]))
        self.assertGreater(n, 20)

    def test_bios_root_kept_by_the_generic_rule(self):
        # guix15-bios: grub.cfg searches by label, so the generic rule 3 sees no
        # search UUIDs and keeps /boot's partition number (core.img's prefix names it too).
        e, dr = K.edited(self.B, "renumber", disk="vda", **{"from": 3, "to": 5})
        self.assertTrue(any("partition number" in w for w in K.reasons(rules.check(self.B, dr, [e]), 3)))
        # The BIOS boot partition is found by its type: renumbered freely.
        e, dr = K.edited(self.B, "renumber", disk="vda", **{"from": 1, "to": 7})
        self.assertEqual(rules.check(self.B, dr, [e]), [])
        for c in K.cases("guix15-bios"):
            self.assertFalse(any(x["op"] == "renumber" and x["from"] == 3 for x in c["edits"]), c["seed"])

    def test_older_generations(self):
        self.assertEqual(guix.generations(self.U), {1})
        two = two_generations(self.B)
        self.assertEqual(guix.generations(two), {1, 2})
        # A partition mounted at /home (beside /): refused with two generations.
        found = guix.check_names(two, two, [{"op": "renumber", "disk": "vda", "from": 3, "to": 5}])
        self.assertTrue(any("/home" in w for _, w in found), found)
        self.assertEqual(guix.check_names(self.B, self.B, [{"op": "renumber", "disk": "vda", "from": 3, "to": 5}]), [])
        # / and the store alone (guix15): every generation's root device is recorded.
        two = two_generations(self.U)
        self.assertEqual(guix.check_names(two, two, [{"op": "renumber", "disk": "vda", "from": 3, "to": 5}]), [])
        # The ESP's mount isn't recorded for an older generation.
        self.assertTrue(guix.check_names(two, two, [{"op": "renumber", "disk": "vda", "from": 1, "to": 5}]))
        # A partition holding nothing (the BIOS boot partition): free.
        two = two_generations(self.B)
        self.assertEqual(guix.check_names(two, two, [{"op": "renumber", "disk": "vda", "from": 1, "to": 5}]), [])

    def test_store_texts(self):
        t = guix.store_texts(self.U)
        self.assertIn("console=ttyS0,115200n8", t)
        self.assertIn(self.U["boot"]["store"]["generations"][0]["root_device"]["value"], t)
        # A generation booting root=/dev/vda3 rules renumbering vda3 out (rule 3).
        x = two_generations(self.U)
        x["boot"]["store"]["generations"][0]["kernel_arguments"] = ["root=/dev/vda3"]
        dk = dumpx.disk(x)
        self.assertTrue(dumpx.names_by_number(x, dk, dumpx.part(dk, 3), F.hooks("guix15", x)))
        self.assertFalse(dumpx.names_by_number(self.U, dumpx.disk(self.U), dumpx.part(dumpx.disk(self.U), 3), F.hooks("guix15", self.U)))

    def test_store_initrd_layers(self):
        h = F.hooks("guix15", self.U)
        self.assertEqual(h.quirk_check(self.U, self.U, []), [])
        self.assertTrue(any("initrd" in w for w in K.reasons(h.quirk_check(self.U, K.with_type(self.U, "vda3", "xfs"), []))))

    def test_content_and_options(self):
        cs = K.cases("guix15")
        self.assertGreater(sum(1 for c in cs if (c.get("content") or {}).get("recipe") == "guix-small-1"), 150)
        self.assertTrue(any("--removable" in c["load_options"] for c in cs))
        self.assertTrue(any("--no-nvram" in c["load_options"] for c in cs))
        self.assertFalse(any({"--removable", "--no-nvram"} & set(c["load_options"]) for c in K.cases("guix15-bios")))
        self.assertTrue(any(x["op"] == "btrfs_geometry" for c in K.cases("guix15-bios") for x in c["edits"]))

    def test_every_case_keeps_the_rules(self):
        for g in ("guix15", "guix15-bios"):
            d = K.dump(g)
            h = F.hooks(g, d)
            from bs2lib import case as C
            for c in K.cases(g):
                self.assertEqual(rules.check(d, C.replay(c, d), c["edits"], hooks=h), [], (g, c["seed"]))


class TestToolsPath(unittest.TestCase):
    """tier0.Tools finds a store system's tools through its profile's
    absolute links, resolved inside the copied root; the usual dirs as before."""

    def test_in_root(self):
        with tempfile.TemporaryDirectory() as r:
            store = f"{r}/gnu/store/abc-util-linux/sbin"
            os.makedirs(store)
            open(f"{store}/sfdisk", "w").close()
            os.makedirs(f"{r}/gnu/store/def-profile")
            os.symlink("/gnu/store/abc-util-linux/sbin", f"{r}/gnu/store/def-profile/sbin")
            os.makedirs(f"{r}/var/guix/profiles")
            os.symlink("/gnu/store/xyz-system", f"{r}/var/guix/profiles/system-2-link")
            os.symlink("system-2-link", f"{r}/var/guix/profiles/system")
            os.makedirs(f"{r}/gnu/store/xyz-system")
            os.symlink("/gnu/store/def-profile", f"{r}/gnu/store/xyz-system/profile")
            self.assertTrue(tier0.in_root(r, "/var/guix/profiles/system/profile/sbin/sfdisk"))
            self.assertFalse(tier0.in_root(r, "/var/guix/profiles/system/profile/sbin/mke2fs"))
            self.assertFalse(tier0.in_root(r, "/var/guix/profiles/system/profile/sbin"))   # a dir, not a tool
            os.symlink("loop", f"{r}/loop")
            self.assertFalse(tier0.in_root(r, "/loop/x"))
            t = tier0.Tools(r, f"{r}/scratch")
            t.root = r
            self.assertTrue(t.has("sfdisk"))
            self.assertFalse(t.has("mkfs.xfs"))
        self.assertEqual(tier0.PATH[:4], ("/usr/sbin", "/usr/bin", "/sbin", "/bin"))


if __name__ == "__main__":
    unittest.main()
