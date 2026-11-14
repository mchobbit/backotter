"""The loaders' traits: systemd_boot.py (BLS entries), limine.py, refind.py,
ostree.py, grub_upstream.py, on their corpus dumps."""

import unittest

import corpus_cases as K
from bs2lib import dumpx, families as F, rules


def with_text(d, path, text):
    """D with TEXT set at PATH (a list of keys and indexes)."""
    x = dumpx.clone(d)
    cur = x
    for k in path[:-1]:
        cur = cur[k]
    cur[path[-1]] = text
    return x


def named(d, g, num):
    dk = dumpx.disk(d)
    return dumpx.names_by_number(d, dk, dumpx.part(dk, num), F.hooks(g, d))


class TestSystemdBoot(unittest.TestCase):
    def test_entries_are_rule3_texts(self):
        for g in ("aerynos", "fedora44-uefi", "pop24", "rocky10-lvm"):
            d = K.dump(g)
            h = F.hooks(g, d)
            self.assertIn("systemd-boot", h.names)
            opts = [e.get("options") for e in d["boot"]["systemd_boot"]["entries"] if e.get("options")]
            self.assertTrue(opts and all(o in dumpx.config_texts(d, h) for o in opts), g)
        d = K.dump("aerynos")
        self.assertEqual(named(d, "aerynos", 3), [])
        x = with_text(d, ["boot", "systemd_boot", "entries", 0, "options"], "root=/dev/vda3 rw")
        self.assertTrue(named(x, "aerynos", 3))

    def test_no_entries_no_trait(self):
        d = K.dump("debian13")
        self.assertNotIn("systemd-boot", F.hooks("debian13", d).names)


class TestLimine(unittest.TestCase):
    def test_conf_not_in_dump(self):
        for g in ("omarchy", "cachyos-zfs"):
            d = K.dump(g)
            self.assertIn("limine", F.hooks(g, d).names)
            e, dr = K.edited(d, "renumber", disk="vda", **{"from": 2, "to": 5})
            self.assertTrue(any("limine.conf" in w for w in K.reasons(rules.check(d, dr, [e]), 3)), g)
            for c in K.cases(g):
                self.assertFalse(any(x["op"] in ("renumber", "swap_order") for x in c["edits"]), (g, c["seed"]))

    def test_patterns(self):
        d = K.dump("omarchy")
        x = with_text(d, ["boot", "kernel_cmdline_file"], "module_path: boot(2):/initramfs.img")
        self.assertTrue(any("boot" in w for w in named(x, "omarchy", 2)))
        x = with_text(d, ["boot", "kernel_cmdline_file"], "kernel_path: hdd(1:2):/vmlinuz")
        self.assertTrue(named(x, "omarchy", 2))
        self.assertEqual(named(x, "omarchy", 1), [])

    def test_bios(self):
        d = with_text(K.dump("omarchy"), ["firmware", "mode"], "bios")
        d["boot"]["efi"] = None
        e, dr = K.edited(d, "swap_order", disk="vda", a=1, b=2)
        self.assertTrue(any("stage 2" in w for w in K.reasons(rules.check(d, dr, [e]), 3)))
        self.assertFalse(any("stage 2" in w for w in K.reasons(rules.check(K.dump("omarchy"), dr, [e]), 3)))


class TestRefind(unittest.TestCase):
    def test_refind_linux_conf(self):
        for g in ("pikaos4", "pikaos4-luks"):
            d = K.dump(g)
            h = F.hooks(g, d)
            self.assertIn("refind", h.names)
            for line in d["boot"]["refind"]["linux_conf"]["lines"]:
                self.assertIn(line["options"], dumpx.config_texts(d, h))
        d = K.dump("pikaos4")
        self.assertEqual(named(d, "pikaos4", 3), [])
        x = with_text(d, ["boot", "refind", "linux_conf", "lines", 1, "options"], "root=/dev/vda3 ro")
        self.assertTrue(named(x, "pikaos4", 3))


class TestOstree(unittest.TestCase):
    def test_entries(self):
        d = K.dump("kinoite44")
        h = F.hooks("kinoite44", d)
        self.assertIn("ostree", h.names)
        texts = dumpx.config_texts(d, h)
        self.assertTrue(any("ostree=/ostree/boot.1/" in t for t in texts))
        x = with_text(d, ["boot", "ostree", "entries", 0, "options"], "root=/dev/vda3 rw")
        self.assertTrue(any("vda3" in w for w in named(x, "kinoite44", 3)))


class TestGrubUpstream(unittest.TestCase):
    def test_matches(self):
        want = {"chimera-zfs", "endeavouros-grub", "garuda", "gentoo", "gentoo-systemd", "manjaro"}
        got = {g for g in K.linux_guests() if "grub_upstream" in F.hooks(g, K.dump(g)).names}
        self.assertEqual(got & set(want), want)
        for g in ("alpine324", "debian13-stack", "ubuntu26-uefi", "fedora44-uefi", "kinoite44", "anduinos2", "nixos2605"):
            self.assertNotIn("grub_upstream", F.hooks(g, K.dump(g)).names, g)

    def test_no_removable(self):
        for g in ("gentoo", "manjaro", "garuda"):
            self.assertFalse(any("--removable" in c["load_options"] for c in K.cases(g)), g)
        self.assertTrue(any("--removable" in c["load_options"] for c in K.cases("ubuntu26-uefi")))


if __name__ == "__main__":
    unittest.main()
