"""U2.1: every word form of contract 2 and rulings R27, and every refusal."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from t2lib import words   # noqa: E402

KNOWN = {
    "stic": ["debian12", "debian13", "freebsd15", "freebsd15-geli", "ghostbsd26-zfs", "netbsd11", "alpinebsd",
             "haiku6", "sculpt2610"],
    "holt": {"sources": ["debian13", "debian13-zfs", "debian13-stack", "mint22", "arch", "alpine324", "aerynos",
                         "ubuntu26-raid", "freebsd15", "freebsd15-geli", "netbsd11"],
             "variants": {"bigger": ["debian13-stack", "aerynos", "mint22"],
                          "oneshot": ["debian13", "mint22", "arch", "alpine324"],
                          "degraded": ["debian13-stack", "ubuntu26-raid"]},
             "families": {"D": ["debian13", "debian13-zfs", "debian13-stack"], "M": ["mint22"]},
             "refused": {"freebsd15-ufs": "the FreeBSD rescuer"}},
    "gate": ["holt", "fork:restic", "fork:rustic_core", "fork:rustic", "fork-root:restic", "bin:abi", "bin:static",
             "bin:version", "loop:linux@chopin", "loop:linux@cinqcent", "loop:sparse@chopin", "loop:sparse@cinqcent",
             "selftest"],
    "guest": ["debian13", "arch", "haiku6"], "guest_refused": {"freebsd15-builder": "a build helper"},
    "img": ["linux", "freebsd", "netbsd", "openbsd", "dragonfly", "illumos", "haiku"],
}
KNOWN["holt"]["variants"]["img"] = ["debian13", "freebsd15"]


def P(text):
    return words.parse(text.split(), KNOWN)


class Forms(unittest.TestCase):
    def test_word_all_is_the_all_form(self):
        for t, fam in (("GateTest2 All", "gate"), ("SticTest2 all rel4", "stic"), ("HoltTest2 ALL", "holt")):
            w = P(t)
            self.assertEqual((w.family, w.form), (fam, "All"), t)
        self.assertEqual(P("SticTest2 All rel4").round, "rel4")

    def test_bare_words(self):
        for t, fam in (("GateTest2", "gate"), ("SticTest2", "stic"), ("HoltTest2", "holt")):
            w = P(t)
            self.assertEqual((w.family, w.form, w.all, w.stale, w.plan), (fam, "bare", False, False, False))
            self.assertEqual(w.word, t)

    def test_all_forms(self):
        for t in ("GateTest2All", "SticTest2All", "HoltTest2All"):
            w = P(t)
            self.assertTrue(w.all)
            self.assertEqual(w.form, "All")
            self.assertEqual(w.word, t)

    def test_plan_and_stale_keywords(self):
        w = P("SticTest2 stale plan")
        self.assertTrue(w.stale and w.plan)
        self.assertEqual(w.form, "bare")
        self.assertTrue(P("HoltTest2All plan").plan)

    def test_t2_plan_word_is_word_plan(self):
        self.assertEqual(words.split_plan_form(["plan", "SticTest2", "rel4"]), ["SticTest2", "rel4", "plan"])
        self.assertEqual(words.split_plan_form(["SticTest2", "plan"]), ["SticTest2", "plan"])

    def test_stic_round_and_guests(self):
        w = P("SticTest2 rel4 debian13 freebsd15")
        self.assertEqual(w.round, "rel4")
        self.assertEqual(w.names, ["stic:debian13", "stic:freebsd15"])
        self.assertEqual(w.form, "names")
        w = P("SticTest2 debian13")
        self.assertIsNone(w.round)
        self.assertEqual(w.names, ["stic:debian13"])

    def test_round_versus_name_known_names_first(self):
        # a token that is a check name is a guest, never the round
        w = P("SticTest2 haiku6")
        self.assertIsNone(w.round)
        self.assertEqual(w.names, ["stic:haiku6"])
        w = P("SticTest2All rel4 plan")
        self.assertEqual(w.round, "rel4")

    def test_stic_cell_prefix(self):
        self.assertEqual(P("SticTest2 stic:debian12").names, ["stic:debian12"])

    def test_holt_family_letters(self):
        w = P("HoltTest2D")
        self.assertEqual((w.letters, w.word, w.form), (["D"], "HoltTest2D", "names"))
        w = P("HoltTest2D 2M")
        self.assertEqual(w.letters, ["D", "M"])
        w = P("holttest2d m")
        self.assertEqual(w.letters, ["D", "M"])
        for x in words.HOLT_LETTERS:
            self.assertEqual(P(f"HoltTest2{x}").letters, [x])
            self.assertEqual(P(f"HoltTest2{x.lower()}").letters, [x])

    def test_holt_names_and_variants(self):
        w = P("HoltTest2 debian13 debian13+oneshot mint22+bigger")
        self.assertEqual(w.names, ["holt:debian13", "holt:debian13+oneshot", "holt:mint22+bigger"])
        w = P("HoltTest2 debian13-stack +bigger +degraded")
        self.assertEqual(w.names, ["holt:debian13-stack"])
        self.assertEqual(w.variants, ["bigger", "degraded"])
        w = P("HoltTest2 +oneshot")
        self.assertEqual((w.form, w.variants), ("bare", ["oneshot"]))

    def test_gate_layers_and_prefixes(self):
        w = P("GateTest2 holt")
        self.assertEqual(w.names, ["gate:holt"])
        w = P("GateTest2 fork")
        self.assertEqual(w.names, ["gate:fork:restic", "gate:fork:rustic_core", "gate:fork:rustic"])
        w = P("GateTest2 bin")
        self.assertEqual(w.names, ["gate:bin:abi", "gate:bin:static", "gate:bin:version"])
        w = P("GateTest2 loop:linux")
        self.assertEqual(w.names, ["gate:loop:linux@chopin", "gate:loop:linux@cinqcent"])
        w = P("GateTest2 loop:sparse@chopin fork-root:restic")
        self.assertEqual(w.names, ["gate:loop:sparse@chopin", "gate:fork-root:restic"])
        w = P("GateTest2 rel4 bin stale")
        self.assertEqual((w.round, w.stale), ("rel4", True))
        w = P("GateTest2All rel4 plan")
        self.assertEqual((w.all, w.round, w.plan), (True, "rel4", True))

    def test_to_json(self):
        j = P("HoltTest2D +bigger plan").to_json()
        self.assertEqual((j["word"], j["letters"], j["variants"], j["plan"]), ("HoltTest2D", ["D"], ["bigger"], True))


class NewWords(unittest.TestCase):
    def test_guest_forms(self):
        w = P("GuestTest2")
        self.assertEqual((w.family, w.form, w.word), ("guest", "bare", "GuestTest2"))
        w = P("GuestTest2 arch stale plan")
        self.assertEqual((w.form, w.names, w.stale, w.plan), ("names", ["guest:arch"], True, True))
        self.assertEqual(P("GuestTest2 guest:arch").names, ["guest:arch"])
        w = P("GuestTest2All plan")
        self.assertEqual((w.form, w.all), ("All", True))
        self.assertEqual(P("GuestTest2 All").form, "All")
        w = P("GuestTest2 update")
        self.assertEqual((w.update, w.form), (True, "bare"))
        w = P("GuestTest2 update arch plan")
        self.assertEqual((w.update, w.form, w.names, w.plan), (True, "names", ["guest:arch"], True))

    def test_img_forms(self):
        w = P("ImgTest2 linux netbsd release stale plan")
        self.assertEqual((w.family, w.names, w.release, w.stale, w.plan), ("img", ["img:linux", "img:netbsd"], True, True, True))
        self.assertEqual(P("ImgTest2").form, "bare")
        w = P("ImgTest2All release plan")
        self.assertEqual((w.all, w.release, w.form), (True, True, "All"))

    def test_composites(self):
        w = P("BkoTest2 rel4 stale plan")
        self.assertEqual((w.family, w.round, w.stale, w.plan, w.word, w.sweep), ("bko", "rel4", True, True, "BkoTest2", False))
        w = P("SweepTest2 rel4 plan")
        self.assertEqual((w.family, w.round, w.plan, w.word, w.sweep), ("sweep", "rel4", True, "SweepTest2", True))
        self.assertEqual(P("BkoTest2").form, "bare")

    def test_img_variant(self):
        self.assertEqual(P("HoltTest2 debian13+img").names, ["holt:debian13+img"])
        w = P("HoltTest2 +img")
        self.assertEqual((w.form, w.names, w.variants), ("names", ["holt:debian13+img", "holt:freebsd15+img"], []))
        w = P("HoltTest2 debian13 +img")
        self.assertEqual((w.names, w.variants), (["holt:debian13"], ["img"]))

    def test_refusals(self):
        def no(text, needle):
            with self.assertRaises(words.Refused) as cm:
                P(text)
            self.assertIn(needle, str(cm.exception))
        no("GuestTest2 update stale", "stale")
        no("GuestTest2All update", "audit")
        no("GuestTest2 nosuch", "no GuestTest2 guest")
        no("GuestTest2 freebsd15-builder", "build helper")
        no("GuestTest2All arch", "no guest names")
        no("ImgTest2 riscos", "no image")
        no("ImgTest2All linux", "every image")
        no("BkoTest2 debian13", "takes no names")
        no("BkoTest2 img:linux", "takes no names")
        no("BkoTest2 All", "no All form")
        no("BkoTest2All", "no All form")
        no("SweepTest2 stale", "stale")
        no("SweepTest2 arch", "takes no names")
        no("SweepTest2All", "every All form")
        no("SweepTest2 All", "every All form")
        no("BkoTest2 rel4 rel5", "already")
        no("HoltTest2 arch+img", "doesn't apply")
        no("HoltTest2All +img", "HoltTest2 +img")

    def test_to_json_keeps_the_new_fields(self):
        j = P("GuestTest2 update arch").to_json()
        self.assertEqual((j["update"], j["release"], j["sweep"]), (True, False, False))
        j = P("ImgTest2All release").to_json()
        self.assertTrue(j["release"])
        self.assertTrue(P("SweepTest2").to_json()["sweep"])


class Refusals(unittest.TestCase):
    def refused(self, text, *needle):
        with self.assertRaises(words.Refused) as cm:
            P(text)
        for n in needle:
            self.assertIn(n, str(cm.exception))

    def test_close(self):
        self.refused("HoltTest2 +close", "H3")
        self.refused("HoltTest2 debian13+close", "H3")

    def test_rescuer_refused_geli_a_source(self):
        self.refused("HoltTest2 freebsd15-ufs", "rescuer")
        self.assertEqual(P("HoltTest2 freebsd15-geli").names, ["holt:freebsd15-geli"])   # a source since GELI's G6

    def test_unknown_names(self):
        self.refused("HoltTest2 nosuchguest", "no HoltTest2 source")
        self.refused("HoltTest2 debian13+wider", "no variant")
        self.refused("HoltTest2 +wider", "no variant")
        self.refused("HoltTest2 arch+bigger", "doesn't apply")
        self.refused("SticTest2 stic:nosuch", "no SticTest check")
        self.refused("GateTest2 gate:nosuch", "no gate layer")

    def test_two_rounds(self):
        self.refused("SticTest2 rel4 rel5", "round is already")
        self.refused("GateTest2 rel4 rel5", "round is already")

    def test_bad_round(self):
        self.refused("SticTest2 -x", "bad ROUND")

    def test_all_takes_no_names(self):
        self.refused("SticTest2All debian13", "no guest names")
        self.refused("HoltTest2All debian13", "no guest names")
        self.refused("HoltTest2All 2D", "every family")
        self.refused("HoltTest2All +bigger", "every variant")
        self.refused("GateTest2All holt", "every layer")
        self.refused("SticTest2All stale", "fresh")

    def test_unknown_and_out_of_scope_words(self):
        self.refused("SticTest3", "unknown word")
        self.refused("HoltTest2Q", "no HoltTest2 family")
        self.refused("SticTest2D", "unknown word")
        with self.assertRaises(words.Refused):
            words.parse([], KNOWN)

    def test_is_word(self):
        for t in ("SticTest2", "holttest2all", "HoltTest2BSD", "GuestTest2", "SweepTest2All", "ImgTest2", "BkoTest2"):
            self.assertTrue(words.is_word(t), t)
        for t in ("pull", "ledger", "plan", "rel4"):
            self.assertFalse(words.is_word(t), t)


if __name__ == "__main__":
    unittest.main()
