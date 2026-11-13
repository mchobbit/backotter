"""Unit tests for BackShot2's library: the edits and replay (B1), the
generator and the rules (B2), the shrinker (B6), content drift's plan (B8),
the sweep (B9) and judge.py's --expect and flag modes (B4, B9).  Stdlib
only, on the Mac or a host, from the corpus's redacted dumps:

    python3 -m unittest discover -s testvm/backshot2/tests
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BS2 = os.path.dirname(HERE)
sys.path.insert(0, BS2)
CORPUS = os.path.join(BS2, "..", "..", "holt", "tests", "corpus")
JUDGE = os.path.join(BS2, "..", "holttest1d", "judge.py")

from bs2lib import case as C, content as CT, dumpx, edits as E, fitcalc, generate as G, geometry as GE, rules, shrink as S, sweep  # noqa: E402
from bs2lib.geometry import Invalid  # noqa: E402


def dump(v, path):
    with open(path, "w") as f:
        json.dump(v, f)


def corpus(name):
    return dumpx.load(os.path.join(CORPUS, name, "src.json"))


ALPINE = corpus("alpine324")
LUKS = corpus("ubuntu26-luks")
STACK = corpus("debian13-stack")
LVM = corpus("rocky10-lvm")
VOID = corpus("void-musl")


class TestApply(unittest.TestCase):
    def test_pure(self):
        before = json.dumps(ALPINE, sort_keys=True)
        e = E.build(ALPINE, "resize", {"disk": "vda", "partition": 3, "by_sectors": -409600, "grow_fs": True})
        d = E.apply(ALPINE, [e])
        self.assertEqual(json.dumps(ALPINE, sort_keys=True), before)
        self.assertEqual(dumpx.part(dumpx.disk(d), 3)["sectors"], 27260928 - 409600)
        # Replay is the same function of the same edits, every time.
        self.assertEqual(json.dumps(E.apply(ALPINE, [e]), sort_keys=True), json.dumps(d, sort_keys=True))

    def test_resize_shrinks_the_xfs_with_it(self):
        e = E.build(ALPINE, "resize", {"disk": "vda", "partition": 3, "by_sectors": -409600})
        c = dumpx.part(dumpx.disk(E.apply(ALPINE, [e])), 3)["content"]
        self.assertEqual(c["blocks"], (27260928 - 409600) * 512 // 4096)
        self.assertEqual(c["ag_blocks"], 851904)
        self.assertEqual(c["ag_count"], 4)

    def test_renumber_renames_everywhere(self):
        e = E.build(ALPINE, "renumber", {"disk": "vda", "from": 3, "to": 4})
        d = E.apply(ALPINE, [e])
        p = dumpx.part(dumpx.disk(d), 4)
        self.assertEqual(p["name"], "vda4")
        self.assertEqual(p["devno"], "253:4")
        self.assertEqual(p["uuid"], "bc000001-04")   # MBR: signature and number
        self.assertIsNone(dumpx.part(dumpx.disk(d), 3))
        self.assertEqual(next(m for m in d["storage"]["mounts"] if m["target"] == "/")["source"], "/dev/vda4")
        self.assertEqual(next(f for f in d["storage"]["fstab"] if f["file"] == "/")["resolves_to"], "vda4")
        self.assertNotIn("vda3", json.dumps(d["storage"]))

    def test_gap_and_swap_order(self):
        e = E.build(ALPINE, "resize", {"disk": "vda", "partition": 3, "by_sectors": -4096})
        d = E.apply(ALPINE, [e])
        g = E.build(d, "gap_before", {"disk": "vda", "partition": 2, "sectors": 2049})
        d = E.apply(d, [g])
        self.assertEqual(dumpx.part(dumpx.disk(d), 2)["start_lba"], 2099200 + 2049)
        self.assertEqual(dumpx.part(dumpx.disk(d), 3)["start_lba"], 6293504 + 2049)
        sw = E.build(d, "swap_order", {"disk": "vda", "a": 2, "b": 3})
        d2 = E.apply(d, [sw])
        dk = dumpx.disk(d2)
        self.assertLess(dumpx.part(dk, 3)["start_lba"], dumpx.part(dk, 2)["start_lba"])
        self.assertEqual(dumpx.part(dk, 3)["start_lba"], 2099200 + 2049)
        with self.assertRaises(Invalid):
            E.build(ALPINE, "gap_before", {"disk": "vda", "partition": 2, "sectors": 1 << 20})   # no room

    def test_lv_split_keeps_the_size(self):
        v = dumpx.vg(LUKS, "ubuntu-vg")
        x = v["logical_volumes"][0]
        total = E.lv_extents(x)
        runs = E.free_runs(v, x["name"])
        free = [r for r in runs if r[1] >= total] or runs
        self.assertTrue(free)
        e = E.build(LUKS, "lv_split", {"vg": "ubuntu-vg", "lv": x["name"], "segments": [["pv0", 0, 100], [free[-1][0], free[-1][1] + free[-1][2] - (total - 100), total - 100]]})
        d = E.apply(LUKS, [e])
        x2 = dumpx.lv(dumpx.vg(d, "ubuntu-vg"), x["name"])
        self.assertEqual(len(x2["segments"]), 2)
        self.assertEqual(x2["size_bytes"], x["size_bytes"])
        with self.assertRaises(Invalid):
            E.build(LUKS, "lv_split", {"vg": "ubuntu-vg", "lv": x["name"], "segments": [["pv0", 0, total]]})   # one piece

    def test_luks_offset_moves_the_payload(self):
        dev = "vda3"
        c = dumpx.devices(LUKS)[dev]["content"]
        f = E.luks_fields(c)
        e = E.build(LUKS, "luks_geometry", {"dev": dev, "header_size": 65536, "keyslots_size": f["keyslots_size"], "offset": f["offset"] + (1 << 20), "sector_size": 512})
        d = E.apply(LUKS, [e])
        kid = dumpx.devices(d)["dm-0"]
        self.assertEqual(kid["size_bytes"], dumpx.devices(LUKS)["dm-0"]["size_bytes"] - (1 << 20))
        pv = dumpx.vg(d, "ubuntu-vg")["physical_volumes"][0]
        self.assertEqual(pv["dev_size_bytes"], kid["size_bytes"])
        self.assertEqual(dumpx.devices(d)[dev]["content"]["json_size"], str(65536 - 4096))
        with self.assertRaises(Invalid):
            E.build(LUKS, "luks_geometry", {"dev": dev, "header_size": 12345, "keyslots_size": f["keyslots_size"], "offset": f["offset"], "sector_size": 512})

    def test_renumber_keeps_partitions_in_number_order(self):
        # bko-holt's dump lists partitions by number (natural name order), and
        # judge.py compares them by index: vda2 renumbered to 4 comes last.
        e = E.build(ALPINE, "renumber", {"disk": "vda", "from": 2, "to": 4})
        d = E.apply(ALPINE, [e])
        dk = dumpx.disk(d)
        self.assertEqual([p["number"] for p in dk["partitions"]], [1, 3, 4])
        self.assertEqual([p["name"] for p in dk["partitions"]], ["vda1", "vda3", "vda4"])
        self.assertEqual(dumpx.part(dk, 4)["start_lba"], 2099200)
        # On GPT too, and a later edit works on the reordered list.
        uefi = copy.deepcopy(corpus("ubuntu26-uefi"))
        uefi["boot"]["efi"]["entries"] = []
        e1 = E.build(uefi, "renumber", {"disk": "vda", "from": 1, "to": 4})
        d1 = E.apply(uefi, [e1])
        e2 = E.build(d1, "resize", {"disk": "vda", "partition": 2, "by_sectors": -32768 * 8})
        dk = dumpx.disk(E.apply(d1, [e2]))
        self.assertEqual([p["number"] for p in dk["partitions"]], [2, 4])
        self.assertEqual(dk["partitions"][0]["sectors"], 31987712 - 32768 * 8)

    def test_moving_the_first_partition_moves_the_mbr_gap(self):
        # holt/src/bootcode.rs: post_mbr_gap.sectors is the first partition's start less one.
        self.assertEqual(dumpx.disk(ALPINE)["boot_code"]["post_mbr_gap"]["sectors"], 2047)
        d = E.apply(ALPINE, [E.build(ALPINE, "resize", {"disk": "vda", "partition": 3, "by_sectors": -4096})])
        g = E.apply(d, [E.build(d, "gap_before", {"disk": "vda", "partition": 1, "sectors": 2})])
        bc = dumpx.disk(g)["boot_code"]
        self.assertEqual(bc["post_mbr_gap"]["sectors"], 2049)
        self.assertEqual(bc["grub_core_in"], "post-mbr-gap")
        a = E.apply(ALPINE, [E.build(ALPINE, "align", {"disk": "vda", "partition": 1, "sectors": 7})])
        self.assertEqual(dumpx.disk(a)["boot_code"]["post_mbr_gap"]["sectors"], 2054)
        # A gap later on the disk leaves it be; so does swapping the first two
        # partitions (the lowest start stays).
        g2 = E.apply(d, [E.build(d, "gap_before", {"disk": "vda", "partition": 2, "sectors": 5})])
        self.assertEqual(dumpx.disk(g2)["boot_code"]["post_mbr_gap"]["sectors"], 2047)
        s = E.apply(ALPINE, [E.build(ALPINE, "swap_order", {"disk": "vda", "a": 1, "b": 2})])
        self.assertEqual(dumpx.disk(s)["boot_code"]["post_mbr_gap"]["sectors"], 2047)

    def test_xfs_4k_sectors_set_the_log_sector_too(self):
        # mkfs.xfs -s size=4096: the log's sector size, a 4 KiB log stripe
        # unit and the "sector" version bit, as the restored dump has them.
        e = E.build(ALPINE, "xfs_geometry", {"fs": "vda3", "sector_size": 4096})
        c = dumpx.part(dumpx.disk(E.apply(ALPINE, [e])), 3)["content"]
        self.assertEqual((c["sector_size"], c["log_sector_size"], c["log_stripe_unit"]), (4096, 4096, 4096))
        self.assertEqual(c["version_flags"], ["attr", "nlink", "align", "logv2", "sector", "extflg", "dirv2", "morebits"])
        # A resize after it keeps them; back to 512 drops them again.
        d = E.apply(ALPINE, [e])
        d = E.apply(d, [E.build(d, "resize", {"disk": "vda", "partition": 3, "by_sectors": -4096})])
        self.assertEqual(dumpx.part(dumpx.disk(d), 3)["content"]["log_stripe_unit"], 4096)
        back = E.apply(d, [E.build(d, "xfs_geometry", {"fs": "vda3", "sector_size": 512})])
        c0, c2 = dumpx.part(dumpx.disk(ALPINE), 3)["content"], dumpx.part(dumpx.disk(back), 3)["content"]
        for k in ("log_sector_size", "log_stripe_unit", "version_flags"):
            self.assertEqual(c2[k], c0[k], k)

    def test_xfs_sector_fields_only_when_the_sector_size_changes(self):
        # A log stripe unit set by hand (4096 on 512-byte sectors) stays
        # through a resize and an xfs_geometry that leaves the sector size.
        src = copy.deepcopy(ALPINE)
        c = dumpx.part(dumpx.disk(src), 3)["content"]
        c["log_stripe_unit"] = 4096
        d = E.apply(src, [E.build(src, "resize", {"disk": "vda", "partition": 3, "by_sectors": -4096})])
        self.assertEqual(dumpx.part(dumpx.disk(d), 3)["content"]["log_stripe_unit"], 4096)
        e = E.build(src, "xfs_geometry", {"fs": "vda3", "ag_count": 5})
        self.assertNotIn("log_stripe_unit", e["sets"]["vda3"]["content"])
        self.assertEqual(dumpx.part(dumpx.disk(E.apply(src, [e])), 3)["content"]["log_stripe_unit"], 4096)
        # A change of sector size sets it as mkfs.xfs -s does.
        e = E.build(src, "xfs_geometry", {"fs": "vda3", "sector_size": 4096})
        self.assertEqual(e["sets"]["vda3"]["content"]["log_stripe_unit"], 4096)

    def test_swap_size_is_whole_pages(self):
        # bko-holt's dump: a swap's size_bytes is (last_page + 1) pages
        # (holt/src/probe/misc.rs), not its partition's bytes.
        e = E.build(ALPINE, "align", {"disk": "vda", "partition": 2, "sectors": 940})
        p = dumpx.part(dumpx.disk(E.apply(ALPINE, [e])), 2)
        self.assertEqual(p["size_bytes"], 2147002368)
        self.assertEqual(p["content"]["last_page"], 524169)
        self.assertEqual(p["content"]["size_bytes"], 2147000320)
        self.assertEqual(e["sets"]["vda2"]["swap"]["size_bytes"], 2146996224)

    def test_chained_swap_order_moves_the_esp_and_its_hidden_sectors(self):
        uefi = copy.deepcopy(corpus("ubuntu26-uefi"))
        uefi["boot"]["efi"]["entries"] = []
        e = E.build(uefi, "swap_order", {"disk": "vda", "a": 1, "b": 2})
        d = E.apply(uefi, [e])
        esp = dumpx.part(dumpx.disk(d), 1)
        self.assertEqual(esp["start_lba"], 2048 + 31987712)
        self.assertEqual(esp["content"]["hidden_sectors"], esp["start_lba"])
        # judge.py --fit: the largest partition shrinks, the ESP after it
        # moves down, and its FAT's hidden sectors with it.
        J = fitcalc.judge()
        plan = J.fit_plan(d, -(1 << 30), 10)
        flat = {}
        J.flatten(d, "", flat)
        want = J.fit_expect(plan, flat)
        i = [k for k, dk in enumerate(d["storage"]["disks"]) if dk["name"] == "vda"][0]
        j = [k for k, p in enumerate(dumpx.disk(d)["partitions"]) if p["number"] == 1][0]
        at = f"storage.disks.{i}.partitions.{j}."
        self.assertEqual(want[at + "content.hidden_sectors"], want[at + "start_lba"])
        self.assertLess(want[at + "start_lba"], esp["start_lba"])


class TestModels(unittest.TestCase):
    def test_ext4_matches_the_corpus(self):
        c = dumpx.part(dumpx.disk(ALPINE), 1)["content"]
        g = GE.ext4(c["blocks"], 4096, c["inodes_per_group"], 256, c["reserved_gdt_blocks"], c["features"], 64, 16, c["reserved_blocks"])
        for k, v in g.items():
            self.assertEqual(c.get(k), v, k)

    def test_ext4_refuses_a_short_last_group(self):
        c = dumpx.part(dumpx.disk(ALPINE), 1)["content"]
        with self.assertRaises(Invalid):
            GE.ext4(32768 * 3 + 30, 4096, 8192, 256, 127, c["features"])

    def test_ext4_resize2fs_keeps_the_groups_geometry(self):
        # fail-005's layout (ipg 32096, 108 groups) on a 4 KiB-block source's
        # features, fitted 444 MiB smaller: resize2fs keeps 32768 blocks and
        # 32096 inodes a group, with fewer groups (mke2fs given the source's
        # inode count instead has to cut blocks per group).
        base = dumpx.part(dumpx.disk(corpus("ubuntu26-uefi")), 2)["content"]
        c = dict(base, blocks=3538432, inodes_per_group=32096, inodes=3466368, reserved_gdt_blocks=261, reserved_blocks=176921)
        n = 3538432 - 444 * 256
        g = GE.ext4_resize2fs(c, n)
        self.assertEqual((g["blocks"], g["blocks_per_group"], g["inodes_per_group"]), (n, 32768, 32096))
        self.assertEqual(g["inodes"], 105 * 32096)
        self.assertGreater(c["inodes"], 105 * 8 * 4096)   # what fail-005 trips on: -N past groups x 8 x block size
        # Descriptors: 108 and 105 groups both take 2 blocks (64 to a block), so the reserve stays.
        self.assertEqual(g["reserved_gdt_blocks"], 261)
        # The same numbers as the generator's own mke2fs model at that size.
        m = GE.ext4(n, 4096, 32096, 256, 261, c["features"], 64)
        for k in ("blocks", "inodes", "inodes_per_group", "blocks_per_group"):
            self.assertEqual(g[k], m[k], k)
        # A last group too small for its overhead is dropped; growing past a
        # descriptor block's 64 groups takes a reserved GDT block.
        self.assertEqual(GE.ext4_resize2fs(c, 32768 * 100 + 10)["blocks"], 32768 * 100)
        self.assertEqual(GE.ext4_resize2fs(c, 32768 * 129)["reserved_gdt_blocks"], 260)

    def test_mke2fs_resize_inverts_its_own_formula(self):
        c = dumpx.part(dumpx.disk(ALPINE), 1)["content"]
        c1 = dict(c, block_size=1024, blocks=1048574, blocks_per_group=8192, first_data_block=1, reserved_gdt_blocks=127)
        r = GE.mke2fs_resize_for(c1)
        # mke2fs: rsv = ceil(ceil(resize/bpg)/gdpb) - ceil(ceil(blocks/bpg)/gdpb)
        gdpb = 1024 // 64
        rsv = GE.cdiv(GE.cdiv(r, 8192), gdpb) - GE.cdiv(GE.cdiv(1048574, 8192), gdpb)
        self.assertEqual(rsv, 127)

    def test_xfs(self):
        c = dumpx.part(dumpx.disk(ALPINE), 3)["content"]
        g = GE.xfs(c["blocks"], 4096, c["ag_blocks"], c["log_blocks"], 512, 512, c["features"])
        self.assertEqual(g["ag_count"], 4)
        with self.assertRaises(Invalid):
            GE.xfs(c["blocks"], 4096, c["blocks"] - 100, c["log_blocks"], 512, 512, c["features"])   # last AG under 16 MiB
        with self.assertRaises(Invalid):
            GE.xfs(60000, 4096, 30000, 16384, 512, 512, c["features"])   # under 300 MiB

    def test_luks2(self):
        self.assertEqual(GE.luks2(1 << 30, 16384, 16744448, 16777216, 512, 1)["payload_bytes"], (1 << 30) - 16777216)
        with self.assertRaises(Invalid):
            GE.luks2(1 << 30, 16384, 16744448, 16384, 512, 1)   # offset inside the header


class TestRules(unittest.TestCase):
    def test_the_source_is_valid(self):
        for d in (ALPINE, LUKS, STACK, LVM, VOID):
            self.assertEqual(rules.check(d, d, []), [], d["os"]["id"])

    def test_rule1_data_must_fit(self):
        d = copy.deepcopy(ALPINE)
        m = next(m for m in d["storage"]["mounts"] if m["target"] == "/")
        m["used_bytes"] = 13 << 30
        self.assertTrue([r for r in rules.rule1(d) if r[0] == 1])

    def test_rule1_planted_content_counts(self):
        self.assertEqual(rules.rule1(ALPINE), [])
        self.assertTrue(rules.rule1(ALPINE, {"/boot": (2 << 30, 10)}))

    def test_rule2_tables(self):
        e = {"op": "ext4_geometry", "fs": "vda1", "block_size": 8192}
        self.assertTrue(rules.rule2(ALPINE, [e]))
        self.assertFalse(rules.rule2(ALPINE, [dict(e, block_size=2048)]))

    def test_rule3_identifiers(self):
        d = copy.deepcopy(ALPINE)
        dumpx.part(dumpx.disk(d), 1)["content"]["uuid"] = "00000000-0000-4000-8000-000000000000"
        self.assertTrue([r for r in rules.rule3(ALPINE, d, []) if r[0] == 3])

    def test_rule3_named_by_number(self):
        d = copy.deepcopy(ALPINE)
        d["storage"]["fstab"][0]["spec"] = "/dev/vda3"
        e = E.build(d, "renumber", {"disk": "vda", "from": 3, "to": 4})
        bad = rules.rule3(d, E.apply(d, [e]), [e])
        self.assertTrue(any("names it by number" in r[1] for r in bad), bad)
        # Through PARTUUID too: on MBR it's the number.
        d = copy.deepcopy(ALPINE)
        d["boot"]["cmdline"]["raw"] = "root=PARTUUID=bc000001-03 ro"
        e = E.build(d, "renumber", {"disk": "vda", "from": 3, "to": 4})
        self.assertTrue(rules.rule3(d, E.apply(d, [e]), [e]))

    def test_rule3_grub_without_search(self):
        d = copy.deepcopy(ALPINE)
        d["boot"]["grub"]["grub_cfg"]["search_uuids"] = []
        e = E.build(d, "swap_order", {"disk": "vda", "a": 1, "b": 2})
        self.assertTrue(rules.rule3(d, E.apply(d, [e]), [e]))


class TestGenerator(unittest.TestCase):
    def test_deterministic(self):
        a = G.generate("alpine324", "gen:x", ALPINE, 4217)
        b = G.generate("alpine324", "gen:x", ALPINE, 4217)
        self.assertEqual(C.text(a), C.text(b))
        self.assertNotEqual(C.case_hash(a), C.case_hash(G.generate("alpine324", "gen:x", ALPINE, 4218)))

    def test_every_case_keeps_the_rules(self):
        for d, name in ((ALPINE, "alpine324"), (LUKS, "ubuntu26-luks"), (STACK, "debian13-stack"), (LVM, "rocky10-lvm"), (VOID, "void-musl")):
            ops = set()
            for seed in range(60):
                c = G.generate(name, None, d, seed)
                dr = C.replay(c, d)
                self.assertEqual(rules.check(d, dr, c["edits"], G.planted_of(d, c.get("content"))), [], (name, seed))
                ops |= {e["op"] for e in c["edits"]}
                if any(C.targets(c, dr)[dk["name"]] < dk["size_bytes"] for dk in dumpx.disks(dr)):
                    self.assertIn("--fit-largest", c["load_options"], (name, seed))
            self.assertGreaterEqual(len(ops), 4, (name, ops))

    def test_lvm_and_luks_edits_are_drawn(self):
        ops = set()
        for seed in range(80):
            ops |= {e["op"] for e in G.generate("ubuntu26-luks", None, LUKS, seed)["edits"]}
        self.assertTrue({"lv_split", "lv_resize", "luks_geometry", "ext4_geometry"} <= ops, ops)

    def test_case_file_round_trip(self):
        c = G.generate("alpine324", "gen:" + "a" * 64, ALPINE, 7)
        c["name"] = C.name_of(c)
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "x.case")
            C.write(p, c)
            back = C.read(p)
        self.assertEqual(back, json.loads(json.dumps(c)))
        self.assertEqual(C.case_hash(back), C.case_hash(c))

    def test_steers_around_fail_005(self):
        # An ext4 / whose inode count needs nearly all of its groups at 8 x
        # block size each: a fit that takes a few groups away trips fail-005.
        d = copy.deepcopy(corpus("ubuntu26-uefi"))
        d["boot"]["efi"]["entries"] = []
        d = E.apply(d, [E.build(d, "resize", {"disk": "vda", "partition": 2, "by_sectors": -6144})])
        rsv = dumpx.part(dumpx.disk(d), 2)["content"]["reserved_gdt_blocks"]
        d = E.apply(d, [E.build(d, "ext4_geometry", {"fs": "vda2", "inodes_per_group": 32096, "reserved_gdt_blocks": rsv})])
        size = dumpx.disk(d)["size_bytes"]
        small, big = {"vda": size - (64 << 20)}, {"vda": size - (1 << 30)}
        self.assertEqual(fitcalc.ext_inodes_past_groups(d, small, fitcalc.pct_needed(d, small)), [])
        self.assertEqual(fitcalc.ext_inodes_past_groups(d, big, fitcalc.pct_needed(d, big)),
                         ["fail-005: vda2's 3915712 inodes in 114 groups of at most 32768"])
        # Tier 0's dry run expects the fitted size judge.py works out, as
        # resize2fs would leave it: 114 groups of 32,096 inodes, 32,768 blocks.
        from bs2lib import tier0 as T0
        nb = T0.fit_blocks(d, big, {"load_options": ["--fit-largest", "7"]})
        self.assertEqual(nb, {"vda2": 3736320})
        g = GE.ext4_resize2fs(dumpx.part(dumpx.disk(d), 2)["content"], nb["vda2"])
        self.assertEqual((g["blocks_per_group"], g["inodes"]), (32768, 114 * 32096))
        # Seed 99 draws a target that far down; with fail-005 known, the draw
        # keeps the rest of the case and drops the target and its fit.
        known = G.KNOWN
        try:
            G.KNOWN = tuple(k for k in known if k != "fail-005")
            c = G.generate("t", None, d, 99, n_edits=0, with_content=False)
            self.assertIn("--fit-largest", c["load_options"])
            G.KNOWN = known
            st = G.Stats()
            c = G.generate("t", None, d, 99, n_edits=0, with_content=False, stats=st)
        finally:
            G.KNOWN = known
        self.assertNotIn("--fit-largest", c["load_options"])
        self.assertFalse(any(e["op"] == "target_size" for e in c["edits"]))
        self.assertIn("target_size: fail-005", st.discarded)

    def test_new_uuids_only_when_built(self):
        for seed in range(100):
            self.assertNotIn("--new-uuids", G.generate("ubuntu26-luks", None, LUKS, seed)["load_options"])
        self.assertTrue(any("--new-uuids" in G.generate("ubuntu26-luks", None, LUKS, s, caps={"new_uuids": True})["load_options"] for s in range(100)))


class TestShrinker(unittest.TestCase):
    def test_drops_what_doesnt_matter(self):
        # A fake failure: "fails" whenever an edit resizes partition 3 by over 1000 sectors.
        c = None
        for seed in range(300):
            x = G.generate("alpine324", None, ALPINE, seed)
            if any(e["op"] == "resize" and e["partition"] == 3 and abs(e["by_sectors"]) > 1000 for e in x["edits"]) and len(x["edits"]) >= 3:
                c = x
                break
        self.assertIsNotNone(c)

        def fails(case):
            return any(e["op"] == "resize" and e["partition"] == 3 and abs(e["by_sectors"]) > 1000 for e in case["edits"])
        sh = S.Shrinker(ALPINE, fails, budget=200, log=lambda *a: None)
        best = sh.shrink(c)
        self.assertTrue(fails(best))
        self.assertEqual([e["op"] for e in best["edits"]], ["resize"])
        # Moved halfway back towards zero while it still failed.
        self.assertLessEqual(abs(best["edits"][0]["by_sectors"]), 2000)
        self.assertFalse(best.get("content"))

    def test_ddmin(self):
        self.assertEqual(S.ddmin(list(range(10)), lambda xs: 3 in xs and 7 in xs), [3, 7])
        self.assertEqual(S.ddmin([1, 2], lambda xs: True), [])

    def test_rebuild_refuses_what_no_longer_fits(self):
        e1 = E.build(ALPINE, "renumber", {"disk": "vda", "from": 3, "to": 4})
        e2 = E.build(E.apply(ALPINE, [e1]), "resize", {"disk": "vda", "partition": 4, "by_sectors": -2048})
        self.assertIsNotNone(S.rebuild(ALPINE, [e1, e2]))
        self.assertIsNone(S.rebuild(ALPINE, [e2]))   # partition 4 isn't there without the renumber


class TestContent(unittest.TestCase):
    def test_plan_is_seeded_and_fits_the_fs(self):
        a = CT.plan({"recipe": "alpine-small-2", "seed": 3}, "xfs")
        self.assertEqual(a, CT.plan({"recipe": "alpine-small-2", "seed": 3}, "xfs"))
        self.assertNotEqual(a, CT.plan({"recipe": "alpine-small-2", "seed": 4}, "xfs"))
        kinds = {e["kind"] for e in a}
        self.assertTrue({"file", "dir", "hardlink", "symlink", "fifo", "chr", "blk"} <= kinds, kinds)
        longest = max(len(bytes.fromhex(e["path"])) for e in a)
        self.assertGreater(longest, 3500)
        self.assertLess(longest, 3900)
        deep = max(len(bytes.fromhex(e["path"])) for e in CT.plan({"recipe": "alpine-small-1", "seed": 3}, "xfs"))
        self.assertGreater(deep, 4096)
        self.assertLessEqual(max(len(bytes.fromhex(e["to"])) for e in a if e["kind"] == "symlink"), 1024)   # XFS's limit
        self.assertFalse(any(e.get("xattrs") for e in CT.plan({"recipe": "alpine-small-2", "seed": 3}, "vfat")))

    def test_long_symlink_fits_the_restored_fs(self):
        c = {"recipe": "alpine-small-2", "seed": 3}

        def long(entries):
            return len(bytes.fromhex(next(e for e in entries if bytes.fromhex(e["path"]) == b"a/long")["to"]))
        # The recipes stay as they were where nothing shrinks.
        self.assertEqual(long(CT.plan(c, "ext4")), 4083)
        self.assertEqual(long(CT.plan(c, "xfs")), 1003)
        self.assertEqual(long(CT.plan(c, "ext4", symlink_max=4095)), 4083)
        self.assertEqual(CT.symlink_max(ALPINE), 1024)   # / (srv/bs2) is XFS
        # An ext4 root drifted to 2 KiB blocks stores 2,047 bytes at most.
        uefi = corpus("ubuntu26-uefi")
        self.assertEqual(CT.symlink_max(uefi), 4095)
        rc = dumpx.part(dumpx.disk(uefi), 2)["content"]
        e = E.build(uefi, "ext4_geometry", {"fs": "vda2", "block_size": 2048, "inodes_per_group": rc["inodes_per_group"] // 2,
                                             "reserved_gdt_blocks": 256})
        lim = CT.symlink_max(E.apply(uefi, [e]))
        self.assertEqual(lim, 2047)
        a, b = CT.plan(c, "ext4"), CT.plan(c, "ext4", symlink_max=lim)
        self.assertLessEqual(long(b), 2047)
        self.assertGreater(long(b), 2000)
        # The same entries and indexes, so a shrunk case's "only" still means the same.
        self.assertEqual([(x["i"], x["path"]) for x in a], [(x["i"], x["path"]) for x in b])
        self.assertEqual(long(CT.plan(c, "ext4", symlink_max=1023)), 1023)
        # bs2-host plant takes the tighter of the source's limit (planted
        # there first) and the restored layout's: a 1 KiB-block source.
        self.assertEqual(CT.fs_symlink_max("ext4", 1024), 1023)
        self.assertEqual(CT.fs_symlink_max("xfs", 4096), 1024)
        self.assertIsNone(CT.fs_symlink_max("btrfs", 4096))
        self.assertEqual(CT.tightest(CT.fs_symlink_max("ext4", 1024), lim), 1023)
        self.assertEqual(CT.tightest(CT.fs_symlink_max("ext4", 4096), lim), 2047)
        self.assertEqual(CT.tightest(None, None), None)
        self.assertEqual(long(CT.plan(c, "ext4", symlink_max=CT.tightest(CT.fs_symlink_max("ext4", 1024), lim))), 1023)

    def test_only_keeps_hardlinks_whole(self):
        a = CT.plan({"recipe": "alpine-small-2", "seed": 3}, "ext4")
        keep = [e["i"] for e in a if e["kind"] == "hardlink"][:1]
        b = CT.plan({"recipe": "alpine-small-2", "seed": 3, "only": keep}, "ext4")
        self.assertEqual(b, [])


class TestDeepTree(unittest.TestCase):
    """The deep tree's compare (fail-001's shrink): whether there is a tree
    to compare comes from the case's plan, not an empty source record."""

    C1 = {"recipe": "alpine-small-1", "seed": 3}

    def deep_ix(self, content):
        return [e["i"] for e in CT.plan(content, "ext4") if bytes.fromhex(e["path"]).startswith(b"deep")]

    def test_deep_planned_follows_only(self):
        full = CT.deep_planned(self.C1)
        self.assertGreater(max(len(bytes.fromhex(k)) for k in full), 4096)
        self.assertEqual(full, [e["path"] for e in CT.plan(self.C1, "xfs") if bytes.fromhex(e["path"]).startswith(b"deep")])
        self.assertEqual(CT.deep_planned(dict(self.C1, only=[])), [])
        shallow = [e["i"] for e in CT.plan(self.C1, "ext4") if not bytes.fromhex(e["path"]).startswith(b"deep")]
        self.assertEqual(CT.deep_planned(dict(self.C1, only=shallow)), [])
        ix = self.deep_ix(self.C1)
        self.assertEqual(len(CT.deep_planned(dict(self.C1, only=ix[:2]))), 2)
        self.assertEqual(CT.deep_planned(None), [])
        self.assertEqual(CT.deep_planned({}), [])

    def test_verdict(self):
        planned = CT.deep_planned(dict(self.C1, only=self.deep_ix(self.C1)[:2]))
        rec = {k: {"mode": 0o40755} for k in planned}
        # No deep tree planned: an empty record is what's expected, not a failure.
        self.assertEqual(CT.deep_verdict([], {}, {}), ("none", []))
        # Planned and recorded empty: missing, not "differs".
        v, lines = CT.deep_verdict(planned, {}, {})
        self.assertEqual(v, "missing")
        self.assertEqual(len(lines), 2)
        self.assertEqual(CT.deep_verdict(planned, {planned[0]: {}}, {planned[0]: {}})[0], "missing")
        self.assertEqual(CT.deep_verdict(planned, rec, dict(rec)), ("agrees", []))
        v, lines = CT.deep_verdict(planned, rec, {planned[0]: rec[planned[0]]})
        self.assertEqual(v, "differs")
        self.assertIn("-> None", lines[0])

    def test_bs2_host_deep_compare_exit_codes(self):
        host = os.path.join(BS2, "bs2-host")
        with tempfile.TemporaryDirectory() as t:
            src, empty = os.path.join(t, "src"), os.path.join(t, "empty")
            os.makedirs(src)
            os.makedirs(empty)
            some = dict(self.C1, only=self.deep_ix(self.C1)[:3])
            self.assertEqual(CT.plant(src, CT.plan(some, "ext4")), [])

            def case(content):
                p = os.path.join(t, f"c{len(os.listdir(t))}.case")
                C.write(p, {"source": "alpine324", "edits": [], "content": content})
                return p

            def deep(*args):
                return subprocess.run([sys.executable, host, "deep", *args], capture_output=True, text=True)
            rec, rec0 = os.path.join(t, "src.json"), os.path.join(t, "empty.json")
            self.assertEqual(deep("record", src, rec).returncode, 0)
            self.assertEqual(deep("record", empty, rec0).returncode, 0)
            with open(rec0) as f:
                self.assertEqual(json.load(f), {})
            with_some, with_none = case(some), case(dict(self.C1, only=[]))
            self.assertEqual(deep("compare", src, rec, "--case", with_some).returncode, 0)
            r = deep("compare", empty, rec, "--case", with_some)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            # fail-001's shrink: "only": [] plants no deep tree, so nothing to compare.
            r = deep("compare", empty, rec0, "--case", with_none)
            self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
            self.assertIn("plants no deep tree", r.stdout)
            # Planted, but the source's record is empty: its own check, not "differs".
            r = deep("compare", empty, rec0, "--case", with_some)
            self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
            self.assertIn("planned entries not in the source's record", r.stdout.splitlines()[-1])
            self.assertEqual(deep("compare", src, rec).returncode, 2)   # --case is required


class TestSweep(unittest.TestCase):
    def test_expected(self):
        c = {"load_options": ["--new-uuids", "--fit-largest", "10", "--snapshot", "@1", "--removable"]}
        self.assertEqual(sweep.expected(c), {"new_uuids": True, "no_nvram": False, "removable": True, "fit": 10, "snapshot": 1})

    def test_touches_esp(self):
        uefi = corpus("ubuntu26-uefi")
        # (Its NVRAM names vda1 by number: such an ESP isn't moved at all.)
        with self.assertRaises(Invalid):
            E.build(uefi, "renumber", {"disk": "vda", "from": 1, "to": 9})
        uefi = copy.deepcopy(uefi)
        uefi["boot"]["efi"]["entries"] = []
        e = E.build(uefi, "renumber", {"disk": "vda", "from": 1, "to": 9})
        self.assertTrue(C.touches_esp({"edits": [e]}, uefi))
        self.assertFalse(C.touches_esp({"edits": []}, uefi))
        self.assertTrue(C.touches_esp({"edits": [], "load_options": ["--removable"]}, uefi))


class TestJudgeExpect(unittest.TestCase):
    def judge(self, *args):
        return subprocess.run([sys.executable, JUDGE] + list(args), capture_output=True, text=True)

    def test_expect_against_the_drifted_layout(self):
        e = E.build(ALPINE, "renumber", {"disk": "vda", "from": 3, "to": 4})
        d = E.apply(ALPINE, [e])
        with tempfile.TemporaryDirectory() as t:
            a, b = os.path.join(t, "drifted.json"), os.path.join(t, "dst.json")
            dump(d, a)
            dst = copy.deepcopy(d)
            dumpx.part(dumpx.disk(dst), 4)["devno"] = "259:7"
            dst["storage"]["topology"] = {"something": {}}
            dump(dst, b)
            r = self.judge("--expect", a, b)
            self.assertEqual(r.returncode, 0, r.stdout)
            # Without --expect the kernel's numbers are compared.
            self.assertEqual(self.judge(a, b).returncode, 1)
            # The source isn't the drifted layout.
            dump(ALPINE, a)
            self.assertEqual(self.judge("--expect", a, b).returncode, 1)

    def test_new_uuids_maps_identifiers(self):
        with tempfile.TemporaryDirectory() as t:
            a, b = os.path.join(t, "a.json"), os.path.join(t, "b.json")
            dump(ALPINE, a)
            txt = json.dumps(ALPINE).replace("bc0000", "cd0000")   # every identifier new, and what names them
            dump(json.loads(txt), b)
            self.assertEqual(self.judge("--expect", a, b).returncode, 1)
            r = self.judge("--expect", "--new-uuids", a, b)
            self.assertEqual(r.returncode, 0, r.stdout)
            # An identifier kept under --new-uuids is a DIFF.
            self.assertEqual(self.judge("--expect", "--new-uuids", a, a).returncode, 1)

    def test_removable_and_nvram_kept(self):
        uefi = corpus("ubuntu26-uefi")
        with tempfile.TemporaryDirectory() as t:
            a, b = os.path.join(t, "a.json"), os.path.join(t, "b.json")
            dump(uefi, a)
            dst = copy.deepcopy(uefi)
            dst["boot"]["esps"][0]["contents"]["files"].insert(0, {"path": "EFI/BOOT/grubx64.efi", "size": 5})
            dst["boot"]["efi"]["boot_order"] = ["0001"]
            dst["boot"]["efi"]["entries"].append({"number": "0009", "description": "UEFI Misc Device 2", "device_path": ["PciRoot(0x0)/Pci(0x5,0x0)"]})
            dump(dst, b)
            self.assertEqual(self.judge("--expect", a, b).returncode, 1)
            r = self.judge("--expect", "--grow", "0", "--removable", "--nvram-kept", a, b)
            self.assertEqual(r.returncode, 0, r.stdout)


VM_MOUNT = os.path.join(BS2, "..", "vm-mount")


class TestVmMount(unittest.TestCase):
    """testvm/vm-mount's mount_fs and try_mount, with blkid and mount stubbed."""

    def mount(self, fstype, ro, fails=""):
        with open(VM_MOUNT) as f:
            src = f.read()
        import re
        funcs = "".join(re.findall(r"^(?:mount_fs|try_mount)\(\) \{\n.*?^\}\n", src, re.M | re.S))
        self.assertIn("try_mount()", funcs)
        sh = f"""set -euo pipefail
blkid() {{ [[ $* == *TYPE* ]] && echo {fstype}; true; }}
mount() {{ if [[ -n "{fails}" ]]; then echo "{fails}" >&2; echo second line >&2; return 32; fi; echo "mount $*"; }}
ro={ro} parts=() mount_err=""
{funcs}
try_mount /dev/nbd9p3 /mnt/x "" && echo ok || echo "failed: $mount_err"
"""
        return subprocess.run(["bash", "-c", sh], capture_output=True, text=True).stdout

    def test_xfs_mounts_nouuid(self):
        # Another slot's copy of the same guest has this XFS's UUID mounted.
        self.assertIn("mount -o ro,norecovery,nouuid /dev/nbd9p3 /mnt/x", self.mount("xfs", "yes"))
        self.assertIn("mount -o nouuid /dev/nbd9p3 /mnt/x", self.mount("xfs", "no"))
        # Nothing else changes.
        self.assertIn("mount -o ro,noload /dev/nbd9p3 /mnt/x", self.mount("ext4", "yes"))
        self.assertIn("mount /dev/nbd9p3 /mnt/x\nok", self.mount("ext4", "no"))

    def test_a_failed_mount_keeps_its_error(self):
        out = self.mount("xfs", "yes", fails="mount: Filesystem has duplicate UUID")
        self.assertIn("failed: /dev/nbd9p3: mount: Filesystem has duplicate UUID; second line", out)
        with open(VM_MOUNT) as f:
            self.assertIn('die "no partition holds /etc/fstab${mount_err:+', f.read())


if __name__ == "__main__":
    unittest.main()
