"""The sweep's --reformat (GENERATOR "9", decision 4, approved 2026-11-26):
what sweep.reformat_choices() draws and refuses, its own stream (a case
without one is GENERATOR 8's), the families that drop it, tier 0's dry run
of the new filesystem, the planter's limit and the shrinker's groups."""

import unittest
from unittest import mock

import corpus_cases as K
from bs2lib import case as C, content as CT, dumpx, families as F, generate as G, shrink, sweep, tier0 as T0
from bs2lib.families.base import without_option


def reformat_of(case):
    o = case.get("load_options") or []
    return [o[i + 1] for i, x in enumerate(o[:-1]) if x == "--reformat"]


def without_reformat(case):
    c = dict(case)
    c["load_options"] = without_option(list(case.get("load_options") or []), "--reformat")
    return c


class TestDraw(unittest.TestCase):

    def test_drawn_on_plain_ext4_and_xfs(self):
        # rocky10: XFS / and /boot under GRUB (ext4 only there); endeavouros: an
        # ext4 / under systemd-boot (XFS or btrfs); slackware15-uefi: XFS / under ELILO.
        for g, want in (("rocky10", {"/=ext4", "/boot=ext4"}), ("endeavouros", {"/=xfs", "/=btrfs"}),
                        ("slackware15-uefi", {"/=ext4", "/=btrfs"})):
            got = {r for c in K.cases(g) for r in reformat_of(c)}
            self.assertEqual(got, want, g)
            n = sum(1 for c in K.cases(g) if reformat_of(c))
            self.assertGreater(n, 5, g)
            self.assertLess(n, 0.2 * K.SEEDS, g)

    def test_never_where_load_refuses_the_layout(self):
        # ostree, ABRoot, Ubuntu Core, NixOS, Guix: the families' sweep hooks.
        for g in ("kinoite44", "vanilla3", "uc24", "nixos2605", "guix15", "guix15-bios"):
            self.assertFalse([c for c in K.cases(g) if "--reformat" in (c.get("load_options") or [])], g)
        # vanilla3's / alone would be taken (initramfs-tools, GRUB not reading it): the abroot trait drops it.
        d = K.dump("vanilla3")
        self.assertIn(("/", "ext4"), sweep.reformat_choices(d, d))
        self.assertEqual(F.hooks("vanilla3", d).sweep(d, ["--reformat", "/=ext4", "--removable"]), ["--removable"])

    def test_every_drawn_one_is_another_type_and_alone(self):
        for g in K.linux_guests():
            d = K.dump(g)
            for c in K.cases(g):
                rs = reformat_of(c)
                if not rs:
                    continue
                o = c["load_options"]
                self.assertEqual(len(rs), 1, g)
                self.assertNotIn("--fit-largest", o, g)
                self.assertNotIn("--new-uuids", o, g)
                mount, _, to = rs[0].partition("=")
                self.assertIn(to, sweep.REFORMAT_TYPES)
                drifted = C.replay(c, d)
                e = next(e for e in dumpx.storage(drifted)["fstab"] if e.get("file") == mount and e.get("type") != "swap")
                frm = dumpx.devices(drifted)[e["resolves_to"]]["content"]["type"]
                self.assertIn(frm, sweep.REFORMAT_TYPES, (g, c["seed"]))
                self.assertNotEqual(frm, to, (g, c["seed"]))
                self.assertIn((mount, to), sweep.reformat_choices(d, drifted, c["edits"], G.planted_of(d, c.get("content")), c.get("content")),
                              (g, c["seed"]))

    def test_other_cases_unchanged(self):
        # Its own stream: with REFORMAT_P 0 every case is the same but for the --reformat pair.
        for g in ("rocky10", "endeavouros", "fedora44", "ubuntu24-luks"):
            d = K.dump(g)
            with mock.patch.object(sweep, "REFORMAT_P", 0.0):
                off = [G.generate(g, None, d, s, caps={}) for s in range(K.SEEDS)]
            for s, (a, b) in enumerate(zip(off, K.cases(g))):
                self.assertNotIn("--reformat", a["load_options"])
                self.assertEqual(a, without_reformat(b), f"{g} seed {s}")
                if reformat_of(b):
                    self.assertEqual(b["load_options"][-2], "--reformat", f"{g} seed {s}: after the other options")

    def test_not_with_new_uuids(self):
        d = K.dump("endeavouros")
        for s in range(K.SEEDS):
            o = G.generate("endeavouros", None, d, s, caps={"new_uuids": True})["load_options"]
            self.assertFalse("--new-uuids" in o and "--reformat" in o, s)


class TestChoices(unittest.TestCase):

    def test_grub_reads_kernels_ext4_only(self):
        d = K.dump("fedora44-uefi")   # /boot XFS, read by GRUB
        self.assertEqual([t for m, t in sweep.reformat_choices(d, d) if m == "/boot"], ["ext4"])
        d = K.dump("ubuntu24-uefi")   # / holds /boot under GRUB, and is ext4 already
        self.assertEqual(sweep.reformat_choices(d, d), [])

    def test_alpine_root_ext4_only(self):
        d = K.dump("alpine324")       # mkinitfs: ext4 is in its default features (no btrfs-progs either)
        self.assertEqual(sweep.reformat_choices(d, d), [("/", "ext4")])
        x = dumpx.clone(d)
        x["boot"]["packages"]["btrfs-progs"] = "6.17-r0"
        self.assertEqual(sweep.reformat_choices(x, x), [("/", "ext4")])

    def test_the_new_types_fsck_is_needed(self):
        d = K.dump("debian12")        # ext4 /, no xfsprogs, no btrfs-progs
        self.assertEqual(sweep.reformat_choices(d, d), [])
        d = K.dump("endeavouros")
        x = dumpx.clone(d)
        x["boot"]["packages"]["xfsprogs"] = None
        self.assertEqual(sweep.reformat_choices(x, x), [("/", "btrfs")])

    def test_hibernation_into_a_swap_file(self):
        d = K.dump("omarchy")         # resume_offset= into /swap/swapfile on the btrfs /
        self.assertEqual([m for m, _ in sweep.reformat_choices(d, d)], [])
        x = dumpx.clone(d)
        del x["boot"]["cmdline"]["storage"]["resume_offset"]
        # (Its 11 GB of zstd-compressed btrfs, x2.5, don't fit its 15 GB as ext4: _fits.)
        self.assertEqual(sweep.reformat_choices(x, x), [])
        for m in x["storage"]["mounts"]:
            if m.get("device") == "dm-0":
                m["used_bytes"] = 2 << 30
        self.assertEqual(sweep.reformat_choices(x, x), [("/", "ext4")])

    def test_not_convertible_or_mounted_elsewhere(self):
        for g in ("mx25", "devuan6", "zorin18", "mint22", "artix-dinit"):   # ext2, ext3, nilfs2, f2fs, jfs roots
            d = K.dump(g)
            self.assertNotIn("/", [m for m, _ in sweep.reformat_choices(d, d)], g)
        d = K.dump("fedora44")         # /home is the same btrfs: only / (which folds it) is drawn
        self.assertEqual({m for m, _ in sweep.reformat_choices(d, d)}, {"/"})

    def test_root_initramfs_and_loader(self):
        for g in ("solus49", "aerynos", "pikaos4", "microos"):   # prebuilt, f2fs, booster, an unknown loader
            d = K.dump(g)
            self.assertNotIn("/", [m for m, _ in sweep.reformat_choices(d, d)], g)

    def test_a_geometry_edit_on_it(self):
        d = K.dump("rocky10")
        root = next(e["resolves_to"] for e in d["storage"]["fstab"] if e["file"] == "/")
        e, drifted = K.edited(d, "xfs_geometry", fs=root, log_blocks=dumpx.devices(d)[root]["content"]["log_blocks"] * 2)
        self.assertNotIn("/", [m for m, _ in sweep.reformat_choices(d, drifted, [e])])
        self.assertIn("/", [m for m, _ in sweep.reformat_choices(d, d, [])])

    def test_small_or_full(self):
        d = K.dump("rocky10")
        x = dumpx.clone(d)
        boot = next(e["resolves_to"] for e in x["storage"]["fstab"] if e["file"] == "/boot")
        dev = dumpx.devices(x)[boot]
        dev["sectors"] = (sweep.REFORMAT_MIN_BYTES >> 9) - 8
        dev.pop("size_bytes", None)
        self.assertNotIn("/boot", [m for m, _ in sweep.reformat_choices(x, x)])
        y = dumpx.clone(d)
        root = next(e["resolves_to"] for e in y["storage"]["fstab"] if e["file"] == "/")
        for m in y["storage"]["mounts"]:
            if m.get("device") == root:
                m["used_bytes"] = int(dumpx.size_of(dumpx.devices(y)[root]) * 0.8)
        self.assertNotIn("/", [m for m, _ in sweep.reformat_choices(y, y)])

    def test_content_onto_xfs_without_bigtime(self):
        d = K.dump("endeavouros")     # ext4 /, content on it, kernel 6.x: XFS with bigtime
        content = {"recipe": "ext-1", "seed": 1}
        planted = G.planted_of(d, content)
        self.assertIn(("/", "xfs"), sweep.reformat_choices(d, d, [], planted, content))
        x = dumpx.clone(d)
        x["os"]["kernel_release"] = "5.10.200"
        self.assertNotIn(("/", "xfs"), sweep.reformat_choices(x, x, [], planted, content))
        self.assertIn(("/", "xfs"), sweep.reformat_choices(x, x, [], planted, {"recipe": "ext-1-32bit", "seed": 1}))
        self.assertIn(("/", "xfs"), sweep.reformat_choices(x, x, [], {}, None))

    def test_kernel_versions(self):
        self.assertEqual(sweep._version("6.18.60-1-lts"), (6, 18))
        self.assertIsNone(sweep._version("generic-5.15.19"))
        self.assertIsNone(sweep._version("0-rescue-bd00"))
        self.assertEqual(sweep.oldest_kernel(K.dump("slackware15-uefi")), (5, 15))


class TestPlanterAndShrinker(unittest.TestCase):

    def test_reformatted_symlink_limit(self):
        d = K.dump("endeavouros")     # ext4 / holds /srv
        self.assertEqual(CT.symlink_max(sweep.reformatted(d, [])), CT.symlink_max(d))
        self.assertEqual(CT.symlink_max(sweep.reformatted(d, ["/=xfs"])), 1024)
        self.assertEqual(CT.symlink_max(sweep.reformatted(d, ["/=ext4"])), 4095)
        self.assertIsNone(CT.symlink_max(sweep.reformatted(d, ["/=btrfs"])))
        d = K.dump("garuda")          # /srv a subvolume of the btrfs /: folded, the new type's too
        self.assertEqual(CT.symlink_max(sweep.reformatted(d, ["/=xfs"])), 1024)

    def test_opt_groups(self):
        self.assertEqual(shrink._opt_groups(["--removable", "--reformat", "/=ext4", "--snapshot", "@0"]),
                         [["--removable"], ["--reformat", "/=ext4"], ["--snapshot", "@0"]])


class FakeTools:
    """tier0.Tools for the dry runs: RC and TEXT for each command whose name is in it."""
    inside, scratch = "/s", "/nowhere"

    def __init__(self, answers):
        self.answers, self.ran = answers, []

    def scratch_file(self, name, size):
        return f"/s/{name}", f"/nowhere/{name}"

    def drop(self, path):
        pass

    def has(self, prog):
        return True

    def run(self, argv, stdin=None, timeout=120):
        self.ran.append(argv)
        return self.answers.get(argv[0], (0, ""))

    run_host_or_mine = run


class TestTier0(unittest.TestCase):

    def setUp(self):
        self.d = K.dump("rocky10")
        self.root = next(e["resolves_to"] for e in self.d["storage"]["fstab"] if e["file"] == "/")
        self.uuid = dumpx.devices(self.d)[self.root]["content"]["uuid"]
        self.plan = f"# ---- format ----\nmke2fs -F -t ext4 -U {self.uuid} -O has_journal /dev/vdb3\n"

    def test_reformat_uuids(self):
        self.assertEqual(T0.reformat_uuids({"load_options": ["--reformat", "/=ext4"]}, self.d), {self.uuid: "ext4"})
        self.assertEqual(T0.reformat_uuids({"load_options": []}, self.d), {})

    def test_made_by_the_sources_tools(self):
        t = FakeTools({})
        with mock.patch.object(T0, "read_ext", return_value=({"blocks": 1}, "")):
            out = T0.dry_runs(t, "c", self.plan, self.d, {"vda": 1 << 34}, set(), reformats={self.uuid: "ext4"})
        self.assertEqual(out, [])
        self.assertEqual([a[0] for a in t.ran], ["mke2fs"])   # no canon_ run: nothing to hold it to

    def test_refused_is_a_fail(self):
        t = FakeTools({"mke2fs": (1, "mke2fs: Invalid filesystem option set: orphan_file")})
        out = T0.dry_runs(t, "c", self.plan, self.d, {"vda": 1 << 34}, set(), reformats={self.uuid: "ext4"})
        self.assertEqual([v for v, _ in out], ["FAIL"])
        self.assertIn("--reformat ext4", out[0][1])
        self.assertIn("orphan_file", out[0][1])


if __name__ == "__main__":
    unittest.main()
