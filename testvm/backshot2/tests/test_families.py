"""The families registry (bs2lib/families/__init__.py; docs/design/bs2-all.md,
"The family interface (A1)"): discovery, one family per corpus dump, each
Linux guest's family and traits, PLANNED for the rest, the conflicts it
refuses; then the golden case hashes and the smoke run over every Linux
corpus dump."""

import json
import os
import unittest

import corpus_cases as K
from bs2lib import case as C, dumpx, edits as E, families as F, rules, tables
from bs2lib.families import Family, Quirk, Trait
from bs2lib.families.base import Module

# What each Linux corpus guest is: its family, then its traits (sorted by name).
EXPECTED = {
    "aerynos": ("linux", ["systemd-boot"]),
    "alpine324": ("alpine", []),
    "anduinos2": ("linux", ["dracut"]),
    "arch": ("linux", ["mkinitcpio"]),
    "artix-dinit": ("linux", ["mkinitcpio"]),
    "artix-s6": ("linux", ["mkinitcpio"]),
    "cachyos": ("linux", ["mkinitcpio"]),
    "cachyos-zfs": ("linux", ["limine", "mkinitcpio"]),
    "chimera": ("linux", ["initramfs-tools"]),
    "chimera-uefi": ("linux", ["initramfs-tools", "systemd-boot"]),
    "chimera-zfs": ("linux", ["grub_upstream", "initramfs-tools"]),
    "debian12": ("linux", ["initramfs-tools"]),
    "debian13": ("linux", ["initramfs-tools"]),
    "debian13-stack": ("linux", ["initramfs-tools"]),
    "debian13-zfs": ("linux", ["initramfs-tools"]),
    "devuan6": ("linux", ["initramfs-tools"]),
    "endeavouros": ("linux", ["dracut", "systemd-boot"]),
    "endeavouros-grub": ("linux", ["dracut", "grub_upstream"]),
    "fedora44": ("linux", ["dracut", "systemd-boot"]),
    "fedora44-raid": ("linux", ["dracut", "systemd-boot"]),
    "fedora44-uefi": ("linux", ["dracut", "systemd-boot"]),
    "garuda": ("linux", ["dracut", "grub_upstream", "snapper"]),
    "gentoo": ("linux", ["dracut", "grub_upstream"]),
    "gentoo-systemd": ("linux", ["dracut", "grub_upstream"]),
    "kali": ("linux", ["initramfs-tools"]),
    "kinoite44": ("linux", ["ostree"]),
    "manjaro": ("linux", ["grub_upstream", "mkinitcpio"]),
    "mint22": ("linux", ["initramfs-tools"]),
    "mx25": ("linux", ["initramfs-tools"]),
    "neon24": ("linux", ["initramfs-tools"]),
    "nixos2605": ("nixos", []),
    "omarchy": ("linux", ["limine", "mkinitcpio", "snapper"]),
    "pikaos4": ("linux", ["booster", "refind"]),
    "pikaos4-luks": ("linux", ["booster", "refind"]),
    "pop24": ("linux", ["initramfs-tools", "systemd-boot"]),
    "rocky10": ("linux", ["dracut", "systemd-boot"]),
    "rocky10-lvm": ("linux", ["dracut", "systemd-boot"]),
    "rocky10-raid5": ("linux", ["dracut", "systemd-boot"]),
    "tumbleweed": ("linux", ["dracut", "snapper"]),
    "ubuntu24": ("linux", ["initramfs-tools"]),
    "ubuntu26-luks": ("linux", ["dracut"]),
    "ubuntu26-raid": ("linux", ["dracut"]),
    "ubuntu26-uefi": ("linux", ["dracut"]),
    "ubuntu26-zfs": ("linux", ["dracut"]),
    "ubuntu26-zfsmirror": ("linux", ["dracut"]),
    "void-musl": ("linux", ["dracut"]),
    "zorin18": ("linux", ["initramfs-tools"]),
}

# The non-Linux corpus guests: no family yet, SKIPPED with the piece that builds it.
PLANNED_GUESTS = {
    "alpinebsd": "A4", "dragonfly6": "A4", "freebsd14": "A4", "freebsd15": "A4", "freebsd15-geli": "A4",
    "freebsd15-mirror": "A4", "ghostbsd26": "A4", "ghostbsd26-zfs": "A4", "hardenedbsd15": "A4",
    "netbsd11": "A4", "openbsd8": "A4", "omnios58": "A5", "omnios58-mirror": "A5",
    "haiku6": "A6", "redox": "A6", "sculpt2610": "A6",
}


class Fake:
    """A module, as discover() hands them to build()."""
    def __init__(self, family=None, traits=()):
        self.FAMILY = family
        self.TRAITS = list(traits)


class TestRegistry(unittest.TestCase):
    def test_discovery_takes_every_module(self):
        here = os.path.dirname(F.__file__)
        files = sorted(f[:-3] for f in os.listdir(here) if f.endswith(".py") and f not in ("__init__.py", "base.py") and not f.startswith("_"))
        self.assertEqual([n for n, _ in F.discover()], files)
        names = [m.name for m in F.REGISTRY.families + F.REGISTRY.traits]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("alpine", names)
        self.assertEqual([f.name for f in F.REGISTRY.families if f.priority == min(x.priority for x in F.REGISTRY.families)], ["linux"])
        for m in F.REGISTRY.families + F.REGISTRY.traits:
            self.assertTrue(m.piece, m.name)

    def test_one_family_per_corpus_dump(self):
        # family_of raises when two families match at the top priority.
        for g in K.guests():
            d = K.dump(g)
            fam = F.family_of(g, d)
            if F.os_key(d) == "linux":
                self.assertIsNotNone(fam, g)
            else:
                self.assertIsNone(fam, g)

    def test_each_linux_guest(self):
        # Guests other sessions add to the corpus join the table when it's
        # next edited; until then test_one_family_per_corpus_dump covers them.
        here = set(K.linux_guests())
        got = {g: (F.family_of(g, K.dump(g)).name, sorted(t.name for t in F.traits_of(g, K.dump(g)))) for g in sorted(here & set(EXPECTED))}
        self.assertEqual(got, {g: v for g, v in EXPECTED.items() if g in here})
        self.assertGreaterEqual(len(got), 40)

    def test_planned_for_the_rest(self):
        got = {g: F.skipped(g, K.dump(g)) for g in K.guests() if F.os_key(K.dump(g)) != "linux"}
        self.assertEqual({g: v for g, v in got.items() if g in PLANNED_GUESTS}, {g: v for g, v in PLANNED_GUESTS.items() if g in got})
        self.assertGreaterEqual(len(got), 15)
        self.assertTrue(set(got.values()) <= set(F.PLANNED.values()), got)
        self.assertEqual(set(F.PLANNED.values()), {"A4", "A5", "A6"})
        for g in K.linux_guests():
            self.assertIsNone(F.skipped(g, K.dump(g)))

    def test_refuses_two_modules_of_a_name(self):
        with self.assertRaisesRegex(F.RegistryError, "two modules named 'x'"):
            F.build([("a", Fake(Family("x"))), ("b", Fake(None, [Trait("x")]))], set())

    def test_refuses_the_wrong_kind(self):
        with self.assertRaisesRegex(F.RegistryError, "wrong list"):
            F.build([("a", Fake(Trait("x")))], set())
        with self.assertRaisesRegex(F.RegistryError, "wrong list"):
            F.build([("a", Fake(None, [Family("x")]))], set())

    def test_refuses_an_op_taken_twice(self):
        op = E.OPS["resize"]
        with self.assertRaisesRegex(F.RegistryError, "taken twice"):
            F.build([("a", Fake(None, [Trait("t1", edits={"zz": op})])), ("b", Fake(None, [Trait("t2", edits={"zz": op})]))], set())
        with self.assertRaisesRegex(F.RegistryError, "already edits.OPS"):
            F.build([("a", Fake(None, [Trait("t1", edits={"resize": op})]))])
        with self.assertRaisesRegex(F.RegistryError, "no EditOp"):
            F.build([("a", Fake(None, [Trait("t1", draws={"zz": (None, 1, "fs")})]))], set())

    def test_refuses_table_conflicts(self):
        row = [("e2fsprogs", "1.0", {"block_size": [4096]})]
        with self.assertRaisesRegex(F.RegistryError, "in two traits"):
            F.build([("a", Fake(None, [Trait("t1", tables=row), Trait("t2", tables=row)]))], set())
        with self.assertRaisesRegex(F.RegistryError, "trait t1 and family f"):
            F.build([("a", Fake(Family("f", tables=row), [Trait("t1", tables=row)]))], set())
        # Two families may: a source has one.
        F.build([("a", Fake(Family("f", tables=row))), ("b", Fake(Family("g", tables=row)))], set())

    def test_two_families_at_one_priority(self):
        always = lambda s, d: True  # noqa: E731
        reg = F.build([("a", Fake(Family("f", matches=always, priority=5))), ("b", Fake(Family("g", matches=always, priority=5)))], set())
        with self.assertRaisesRegex(F.RegistryError, "all match at priority 5"):
            F.family_of("s", K.dump("debian13"), reg)
        reg = F.build([("a", Fake(Family("f", matches=always, priority=5))), ("b", Fake(Family("g", matches=always, priority=6)))], set())
        self.assertEqual(F.family_of("s", K.dump("debian13"), reg).name, "g")

    def test_a_familys_tables_packages_and_versions(self):
        d = K.dump("debian13")
        rows = [("e2fsprogs", "1.0", {"block_size": [2048]}), ("e2fsprogs", "1.47", {"block_size": [1024]})]
        fam = Family("f", matches=lambda s, x: True, tables=rows, packages={"xfsprogs": ("xfsprogs-core",)},
                     versions=lambda x: {"btrfs-progs": "4.0"})
        reg = F.build([("a", Fake(fam))], set())
        h = F.hooks("s", d, reg)
        self.assertEqual(tables.allowed(d, "e2fsprogs", "block_size", h), [1024])   # 1.47.2 here: the newer row
        self.assertEqual(tables.allowed(d, "e2fsprogs", "block_size"), [1024, 2048, 4096])   # the generic rows
        self.assertEqual(h.packages["xfsprogs"], ("xfsprogs", "xfsprogs-core"))
        self.assertEqual(tables.version(d, "btrfs-progs", h), "4.0")
        self.assertIsNone(tables.check(d, "btrfs-progs", "node_size", 4096, h))

    def test_hooks_merge_in_order(self):
        seen = []
        q1 = Quirk("q1", "", lambda s, d, e: seen.append("q1") or [(3, "one")])
        q2 = Quirk("q2", "", lambda s, d, e: seen.append("q2") or [])
        fam = Family("f", matches=lambda s, x: True, quirks=[q1], steer=lambda d, dr, e, o: o + ["--a"],
                     config_texts=lambda x: ["root=/dev/vda9"])
        tr = Trait("t", matches=lambda s, x: True, quirks=[q2], steer=lambda d, dr, e, o: o + ["--b"],
                   number_patterns=lambda x, dk, p: [rf"slot{p['number']}"], names_by_number=lambda x, dk, p: ["by magic"])
        reg = F.build([("a", Fake(fam, [tr]))], set())
        d = K.dump("debian13")
        h = F.hooks("s", d, reg)
        self.assertEqual(h.names, ["f", "t"])
        self.assertEqual(h.quirk_check(d, d, []), [(3, "one")])
        self.assertEqual(seen, ["q1", "q2"])
        self.assertEqual(h.steer(d, d, [], []), ["--a", "--b"])
        self.assertIn("root=/dev/vda9", dumpx.config_texts(d, h))
        dk = dumpx.disk(d)
        self.assertIn("by magic", dumpx.names_by_number(d, dk, dumpx.part(dk, 1), h))
        self.assertFalse(any("vda9" in x for x in dumpx.config_texts(d)))

    def test_a_familys_own_edit_replays(self):
        """A module's edit op: drawn through its draw, checked by its rule2_pairs, replayed by edits.apply."""
        class Nudge(E.Op):
            name = "bs2_test_nudge"

            def apply(self, d, e):
                dumpx.disk(d)["bs2_test"] = e["v"]

            def build(self, d, params):
                return {"op": self.name, "v": params["v"]}

            def rule2_pairs(self, e):
                return [("e2fsprogs", "block_size", e["v"])]

        nudge = Nudge()
        tr = Trait("nudger", matches=lambda s, x: True, edits={nudge.name: nudge},
                   ops=lambda d, w: {k: v for k, v in w.items() if k == nudge.name},
                   draws={nudge.name: (lambda rng, d, h: [(nudge.name, {"v": rng.choice([1024, 3])})], 100, "fs")})
        reg = F.build([("a", Fake(None, [tr]))], set())
        d = K.dump("debian13")
        h = F.hooks("s", d, reg)
        self.assertEqual(rules.rule2(d, [{"op": nudge.name, "v": 3}], h)[0][0], 2)
        self.assertEqual(rules.rule2(d, [{"op": nudge.name, "v": 1024}], h), [])
        old = F.REGISTRY
        try:
            F.REGISTRY = reg
            self.assertEqual(dumpx.disk(E.apply(d, [{"op": nudge.name, "v": 7}]))["bs2_test"], 7)
            from bs2lib import generate as G
            c = G.generate("s", None, d, 1, n_edits=3, with_content=False, with_options=False)
            self.assertTrue(c["edits"])
            self.assertTrue(all(e["op"] == nudge.name and e["v"] == 1024 for e in c["edits"]), c["edits"])
            self.assertEqual(C.replay(c, d)["storage"]["disks"][0]["bs2_test"], 1024)
        finally:
            F.REGISTRY = old

    def test_module_defaults_do_nothing(self):
        m = Module("m")
        self.assertFalse(m.matches("s", K.dump("debian13")))
        h = F.merge(None, [])
        d = K.dump("debian13")
        self.assertEqual(h.quirk_check(d, d, []), [])
        self.assertEqual(h.steer(d, d, [], ["--x"]), ["--x"])
        self.assertEqual(h.weights(d, {"a": 1}), {"a": 1})
        self.assertIsNone(h.recipe(d))
        self.assertEqual(h.tier0, "plan")


class TestGolden(unittest.TestCase):
    """Each family's case hashes, seeds 0-199 over its corpus dumps
    (tests/golden/FAMILY.json).  Rewritten only on purpose, with a new
    GENERATOR: `python3 -c 'import corpus_cases as K; K.write_golden("FAMILY")'`
    in tests/.  Alpine's are the hashes the generator drew before the
    family interface."""

    def test_golden(self):
        from bs2lib import GENERATOR
        files = sorted(f for f in os.listdir(K.GOLDEN) if f.endswith(".json"))
        self.assertIn("alpine.json", files)
        for f in files:
            with open(os.path.join(K.GOLDEN, f)) as fh:
                g = json.load(fh)
            fam = f[:-5]
            self.assertEqual(g["generator"], GENERATOR, f"{f}: a new GENERATOR rewrites the golden")
            for guest, want in sorted(g["guests"].items()):
                if not os.path.exists(os.path.join(K.CORPUS, guest, "src.json")):
                    continue   # a guest gone from the corpus
                if g.get("dumps", {}).get(guest) != K.dump_sum(guest):
                    # The corpus dump was refreshed (HoltTest2 rewrites them): the golden
                    # was drawn from another dump, so it says nothing.  Rewrite it.
                    print(f"note: golden {fam}/{guest}: its corpus dump changed; skipped (rewrite with write_golden)")
                    continue
                self.assertEqual((F.family_of(guest, K.dump(guest)) or Family("")).name, fam, guest)
                got = [K.case_digest(c) for c in K.cases(guest)[:len(want)]]
                bad = [s for s, (a, b) in enumerate(zip(want, got)) if a != b]
                self.assertEqual(bad, [], f"{fam}/{guest}: seeds {bad[:10]} draw other cases than the golden's")


class TestSmoke(unittest.TestCase):
    """Every Linux corpus dump: seeds 0-299 (caps {}) draw without an
    exception, the same twice, and every case's drifted layout passes the
    rules and the source's quirks."""

    def test_every_linux_dump(self):
        from bs2lib import generate as G
        for g in K.linux_guests():
            d = K.dump(g)
            h = F.hooks(g, d)
            cs = K.cases(g)
            self.assertEqual(len(cs), K.SEEDS)
            for seed in range(0, K.SEEDS, 10):
                self.assertEqual(G.generate(g, None, d, seed, caps={}), cs[seed], f"{g} seed {seed}: not deterministic")
            for seed, c in enumerate(cs):
                bad = rules.check(d, C.replay(c, d), c["edits"], hooks=h)
                self.assertEqual(bad, [], f"{g} seed {seed}")


if __name__ == "__main__":
    unittest.main()
