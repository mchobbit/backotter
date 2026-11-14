#!/usr/bin/env python3
"""judge.py --fit's self-test: hand-made dumps, no host and no VM.

    python3 -I testvm/holttest1d/judge-test.py        (or -m unittest discover)

Each case makes a source dump of one guest's disk, the restored dump a fit
would leave, and runs judge.py the way ht1.sh does.  A fit may change the
numbers that follow from the new size and nothing else, so every case has its
twin: the wrong partition fitted, the wrong amount, a changed UUID or an LV
that didn't give up its extents must all come back a DIFF.
"""

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
JUDGE = os.path.join(HERE, "judge.py")
GIB = 1 << 30
MIB = 1 << 20


def ext4(size, uuid="11111111-1111-4111-8111-111111111111"):
    """An ext4 superblock as the dump records it, filling SIZE bytes."""
    return {"type": "ext4", "usage": "filesystem", "uuid": uuid, "label": None, "version": "1.0",
            "block_size": 4096, "blocks": size // 4096, "size_bytes": size, "free_blocks": size // 8192,
            "inodes": size // 16384, "inodes_per_group": 8192, "free_inodes": 1000, "inode_size": 256, "reserved_blocks": size // 81920,
            "reserved_gdt_blocks": 127, "state": "clean"}


def xfs(size, uuid="22222222-2222-4222-8222-222222222222"):
    return {"type": "xfs", "usage": "filesystem", "uuid": uuid, "label": None, "version": "5",
            "block_size": 4096, "blocks": size // 4096, "size_bytes": size, "sector_size": 512,
            "inode_size": 512, "ag_count": 4, "ag_blocks": size // 4096 // 4, "log_internal": True,
            "log_blocks": 16384, "free_blocks": size // 8192}


def btrfs(size, uuid="33333333-3333-4333-8333-333333333333"):
    return {"type": "btrfs", "usage": "filesystem", "uuid": uuid, "label": None, "total_bytes": size,
            "bytes_used": size // 4, "num_devices": 1, "sector_size": 4096, "node_size": 16384,
            "device": {"devid": 1, "uuid": "44444444-4444-4444-8444-444444444444", "total_bytes": size,
                       "bytes_used": size // 4}}


def swap(size):
    return {"type": "swap", "usage": "other", "uuid": "55555555-5555-4555-8555-555555555555",
            "label": None, "version": "1", "page_size": 4096, "last_page": size // 4096 - 1,
            "size_bytes": size}


def part(name, number, start, sectors, content, gpt=False, **extra):
    p = {"name": name, "number": number, "start_lba": start, "sectors": sectors,
         "start_bytes": start * 512, "size_bytes": sectors * 512, "uuid": f"aaaaaaaa-{number:04d}",
         "content": content, "mounted_at": []}
    if gpt:
        p["end_lba"] = start + sectors - 1
        p["type"] = "0fc63daf-8483-4772-8e79-3d69d8477de4"
    else:
        p["type"] = "0x83"
        p["logical"] = False
        p["extended"] = False
    p.update(extra)
    return p


def disk(name, size, parts, gpt=False):
    d = {"name": name, "path": f"/dev/{name}", "kind": "disk", "size_bytes": size,
         "size": f"{size // GIB} GiB", "logical_sector_size": 512, "physical_sector_size": 512,
         "partitions": parts, "content": None, "mounted_at": []}
    if gpt:
        last = size // 512 - 1
        d["partition_table"] = {"type": "gpt", "sector_size": 512, "disk_guid": "66666666-6666-4666-8666-666666666666",
                                "first_usable_lba": 34, "last_usable_lba": last - 33, "backup_lba": last,
                                "entry_count": 128, "entry_size": 128,
                                "protective_mbr": {"present": True, "hybrid": False,
                                                   "entries": [{"number": 1, "start_lba": 1, "sectors": last,
                                                                "sectors_bytes": None, "size_bytes": last * 512,
                                                                "type": "0xee"}]}}
    else:
        d["partition_table"] = {"type": "dos", "sector_size": 512, "disk_signature": "c0ffee01"}
    return d


def dump(disks, **storage):
    """A dump with just what judge.py reads: the OS family, and the storage."""
    d = {"generated": "2026-11-09T00:00:00Z", "tool": {"version": "0.2.5"},
         "os": {"family": "linux", "pretty_name": "Test Linux"},
         "storage": {"disks": disks}, "warnings": []}
    d["storage"].update(storage)
    return d


def mbr_guest():
    """/boot, swap, then / filling the disk: the layout of most of the guests."""
    root = 27260928
    return dump([disk("vda", 16 * GIB, [
        part("vda1", 1, 2048, 2097152, ext4(1 * GIB, "77777777-7777-4777-8777-777777777777")),
        part("vda2", 2, 2099200, 4194304, swap(2 * GIB)),
        part("vda3", 3, 6293504, root, ext4(root * 512)),
    ])])


def gpt_guest():
    """An ESP, / and swap at the end of the disk: / is fitted and swap moves."""
    root = 27260928
    return dump([disk("vda", 16 * GIB, [
        part("vda1", 1, 2048, 2097152, ext4(1 * GIB, "88888888-8888-4888-8888-888888888888"), gpt=True),
        part("vda2", 2, 2099200, root, xfs(root * 512), gpt=True),
        part("vda3", 3, 29360128, 4192256, swap(4192256 * 512), gpt=True),
    ], gpt=True)])


def lvm_guest():
    """A PV holding one VG with swap and root, every extent allocated."""
    pv = 31451136
    extent = 4 * MIB
    root_lv, swap_lv = 3429 * extent, 410 * extent
    pvc = {"type": "LVM2_member", "usage": "raid", "uuid": "pvuuid-0001", "label": None,
           "version": "LVM2 001", "device_size": pv * 512, "data_area_offset": 1 * MIB,
           "vg_name": "vg0", "vg_uuid": "vguuid-0001"}
    lvm = {"volume_groups": [{
        "name": "vg0", "id": "vguuid-0001", "seqno": 3, "format": "lvm2",
        "extent_size_bytes": extent, "max_lv": 0, "max_pv": 0,
        "physical_volumes": [{"name": "pv0", "id": "pvuuid-0001", "device_hint": "/dev/vda3",
                              "dev_size_bytes": pv * 512, "pe_start_bytes": 1 * MIB,
                              "pe_count": 3839, "device": "vda3"}],
        "logical_volumes": [
            {"name": "swap", "id": "lvswap", "size_bytes": swap_lv, "active_device": "dm-1",
             "segments": [{"start_extent": 0, "extent_count": 410, "type": "striped",
                           "stripe_count": 1, "stripes": ["pv0", 3429]}]},
            {"name": "root", "id": "lvroot", "size_bytes": root_lv, "active_device": "dm-0",
             "segments": [{"start_extent": 0, "extent_count": 3429, "type": "striped",
                           "stripe_count": 1, "stripes": ["pv0", 0]}]}],
    }]}
    dms = [
        {"name": "dm-0", "path": "/dev/dm-0", "kind": "dm", "size_bytes": root_lv,
         "size": "13.4 GiB", "logical_sector_size": 512, "partition_table": None, "partitions": [],
         "content": xfs(root_lv, "99999999-9999-4999-8999-999999999999"), "mounted_at": ["/"],
         "dm": {"dm_name": "vg0-root", "target": "lvm", "vg": "vg0", "lv": "root"}},
        {"name": "dm-1", "path": "/dev/dm-1", "kind": "dm", "size_bytes": swap_lv,
         "size": "1.6 GiB", "logical_sector_size": 512, "partition_table": None, "partitions": [],
         "content": swap(swap_lv), "mounted_at": [],
         "dm": {"dm_name": "vg0-swap", "target": "lvm", "vg": "vg0", "lv": "swap"}},
    ]
    return dump([disk("vda", 16 * GIB, [
        part("vda1", 1, 2048, 2048, None, gpt=True),
        part("vda2", 2, 4096, 2097152, xfs(1 * GIB, "aaaaaaa1-0000-4000-8000-000000000001"), gpt=True),
        part("vda3", 3, 2101248, pv, pvc, gpt=True),
    ], gpt=True)], lvm=lvm, dm_devices=dms)


def luks_guest():
    """LUKS on a plain partition with btrfs inside it (Omarchy's layout)."""
    enc = 29356032
    inside = enc * 512 - 16 * MIB
    luks = {"type": "crypto_LUKS", "usage": "crypto", "uuid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "label": None, "version": "2", "header_size": 16384,
            "segments": [{"id": "0", "type": "crypt", "offset": "16777216", "size": "dynamic",
                          "encryption": "aes-xts-plain64", "sector_size": 512}],
            "keyslots_size": "16744448", "json_size": "12288"}
    dms = [{"name": "dm-0", "path": "/dev/dm-0", "kind": "dm", "size_bytes": inside, "size": "14 GiB",
            "logical_sector_size": 512, "partition_table": None, "partitions": [],
            "content": btrfs(inside), "mounted_at": ["/"],
            "dm": {"dm_name": "root", "target": "crypt", "crypt_type": "LUKS2",
                   "luks_uuid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"}}]
    return dump([disk("vda", 16 * GIB, [
        part("vda1", 1, 2048, 4194304, ext4(2 * GIB, "ccccccc1-0000-4000-8000-000000000001"), gpt=True),
        part("vda2", 2, 4196352, enc, luks, gpt=True),
    ], gpt=True)], dm_devices=dms)


def shrink_disk(d, i, delta):
    """Disk I of dump D as a target DELTA bytes bigger (negative: smaller):
    its size and, for GPT, the fields that sit at its end."""
    x = d["storage"]["disks"][i]
    x["size_bytes"] += delta
    x["size"] = f"{x['size_bytes'] // GIB} GiB"
    t = x["partition_table"]
    if t["type"] == "gpt":
        t["last_usable_lba"] += delta // 512
        t["backup_lba"] += delta // 512
        e = t["protective_mbr"]["entries"][0]
        e["sectors"] += delta // 512
        e["size_bytes"] += delta
    return x


def fit_part(x, j, amount):
    """Partition J of disk X fitted by AMOUNT bytes, and those after it moved."""
    p = x["partitions"][j]
    start = p["start_lba"]
    p["sectors"] += amount // 512
    p["size_bytes"] += amount
    if "end_lba" in p:
        p["end_lba"] += amount // 512
    for q in x["partitions"]:
        if q["start_lba"] > start:
            q["start_lba"] += amount // 512
            q["start_bytes"] += amount
            if "end_lba" in q:
                q["end_lba"] += amount // 512
    return p


def refit_fs(content, amount):
    """A filesystem remade at a size AMOUNT bytes from the one it had."""
    if content["type"] == "btrfs":
        content["total_bytes"] += amount
        content["device"]["total_bytes"] += amount
        return content
    content["size_bytes"] += amount
    if "blocks" in content:
        content["blocks"] += amount // content["block_size"]
    if content["type"] == "xfs":
        content["ag_blocks"] = content["blocks"] // content["ag_count"]
    if content["type"].startswith("ext"):
        content["inodes"] += amount // 16384
        content["inodes_per_group"] += 688  # load keeps the count (-N), over fewer groups
        content["reserved_blocks"] += amount // 81920
    return content


def run_plan(src, *args):
    """`judge.py --fit-plan ARGS` on the dump SRC: its exit code and stdout."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "layout.json")
        with open(p, "w") as f:
            json.dump(src, f)
        r = subprocess.run([sys.executable, "-I", JUDGE, "--fit-plan", *args, p],
                           capture_output=True, text=True)
    return r.returncode, r.stdout


def zvol(name, size):
    """A disk with no partition table (a zvol, a guest's /dev/ram0)."""
    return {"name": name, "path": f"/dev/{name}", "kind": "disk", "size_bytes": size,
            "size": f"{size // GIB} GiB", "logical_sector_size": 512, "partition_table": None,
            "partitions": [], "content": None, "mounted_at": []}


class Fit(unittest.TestCase):
    """judge.py --fit over the layouts the smaller and fitbigger variants run."""

    def judge(self, src, dst, *args):
        with tempfile.TemporaryDirectory() as tmp:
            s, t = os.path.join(tmp, "src.json"), os.path.join(tmp, "dst.json")
            for path, d in ((s, src), (t, dst)):
                with open(path, "w") as f:
                    json.dump(d, f)
            r = subprocess.run([sys.executable, "-I", JUDGE, *args, s, t], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def good(self, src, dst, *args):
        rc, out = self.judge(src, dst, *args)
        self.assertEqual(rc, 0, out)
        return out

    def bad(self, src, dst, *args):
        rc, out = self.judge(src, dst, *args)
        self.assertEqual(rc, 1, out)
        return out

    # A plain filesystem on the last partition: the usual guest, 1 GiB smaller.

    def test_mbr_shrink(self):
        src = mbr_guest()
        dst = copy.deepcopy(src)
        x = shrink_disk(dst, 0, -GIB)
        refit_fs(fit_part(x, 2, -GIB)["content"], -GIB)
        out = self.good(src, dst, "--fit", str(-GIB), "--fit-pct", "10")
        self.assertIn("fitted as --fit asks: vda by -1073741824 bytes, vda3 by -1073741824", out)

    def test_mbr_shrink_wrong_partition(self):
        """Swap shrunk instead of /: a DIFF, and / not fitted at all is another."""
        src = mbr_guest()
        dst = copy.deepcopy(src)
        x = shrink_disk(dst, 0, -GIB)
        x["partitions"][1]["sectors"] -= GIB // 512
        x["partitions"][1]["size_bytes"] -= GIB
        x["partitions"][1]["content"]["size_bytes"] -= GIB
        out = self.bad(src, dst, "--fit", str(-GIB))
        self.assertIn("DIFF storage.disks.0.partitions.1.sectors", out)
        self.assertIn("DIFF fit:storage.disks.0.partitions.2.sectors", out)

    def test_mbr_shrink_wrong_amount(self):
        """A megabyte too far: the amount judge.py works out is the only one."""
        src = mbr_guest()
        dst = copy.deepcopy(src)
        x = shrink_disk(dst, 0, -GIB)
        refit_fs(fit_part(x, 2, -GIB - MIB)["content"], -GIB - MIB)
        out = self.bad(src, dst, "--fit", str(-GIB))
        self.assertIn("DIFF fit:storage.disks.0.partitions.2.sectors", out)
        self.assertIn("DIFF storage.disks.0.partitions.2.sectors: 27260928 -> ", out)

    def test_mbr_shrink_changed_uuid(self):
        src = mbr_guest()
        dst = copy.deepcopy(src)
        x = shrink_disk(dst, 0, -GIB)
        c = refit_fs(fit_part(x, 2, -GIB)["content"], -GIB)
        c["uuid"] = "deadbeef-dead-4ead-8ead-deadbeefdead"
        out = self.bad(src, dst, "--fit", str(-GIB))
        self.assertIn("DIFF storage.disks.0.partitions.2.content.uuid", out)

    def test_mbr_shrink_nothing_fitted(self):
        """The target came back smaller with the source's partitions: not a fit."""
        src = mbr_guest()
        dst = copy.deepcopy(src)
        shrink_disk(dst, 0, -GIB)
        out = self.bad(src, dst, "--fit", str(-GIB))
        self.assertIn("DIFF fit:storage.disks.0.partitions.2.sectors: 27260928 should have been "
                      "fitted to 25163776, and is 27260928", out)

    def test_mbr_shrink_same_disk(self):
        """A target of the source's size, so nothing was fitted onto it."""
        src = mbr_guest()
        out = self.bad(src, copy.deepcopy(src), "--fit", str(-GIB))
        self.assertIn("DIFF fit:storage.disks.0.size_bytes: 17179869184 should have been "
                      "fitted to 16106127360, and is 17179869184", out)

    def test_mbr_other_partition_touched(self):
        """/boot keeps its size: only the largest partition is resized."""
        src = mbr_guest()
        dst = copy.deepcopy(src)
        x = shrink_disk(dst, 0, -GIB)
        refit_fs(fit_part(x, 2, -GIB)["content"], -GIB)
        x["partitions"][0]["sectors"] -= 2048
        out = self.bad(src, dst, "--fit", str(-GIB))
        self.assertIn("DIFF storage.disks.0.partitions.0.sectors", out)

    def test_mbr_grow(self):
        """1 GiB bigger: the largest partition takes the room, 10% being more."""
        src = mbr_guest()
        dst = copy.deepcopy(src)
        x = shrink_disk(dst, 0, GIB)
        refit_fs(fit_part(x, 2, GIB)["content"], GIB)
        self.good(src, dst, "--fit", str(GIB), "--fit-pct", "10")

    def test_mbr_grow_over_pct(self):
        """With PCT 5 the partition may take only 665 MiB of the 1 GiB."""
        src = mbr_guest()
        dst = copy.deepcopy(src)
        x = shrink_disk(dst, 0, GIB)
        refit_fs(fit_part(x, 2, GIB)["content"], GIB)
        out = self.bad(src, dst, "--fit", str(GIB), "--fit-pct", "5")
        self.assertIn("DIFF storage.disks.0.partitions.2.sectors", out)
        dst = copy.deepcopy(src)
        x = shrink_disk(dst, 0, GIB)
        refit_fs(fit_part(x, 2, 665 * MIB)["content"], 665 * MIB)
        self.good(src, dst, "--fit", str(GIB), "--fit-pct", "5")

    # GPT, with a partition after the one that is fitted.

    def test_gpt_shrink_moves_swap(self):
        src = gpt_guest()
        dst = copy.deepcopy(src)
        x = shrink_disk(dst, 0, -GIB)
        refit_fs(fit_part(x, 1, -GIB)["content"], -GIB)
        out = self.good(src, dst, "--fit", str(-GIB), "--fit-pct", "10")
        self.assertIn("vda2 by -1073741824", out)

    def test_gpt_shrink_swap_stayed(self):
        """Swap has to come down with /: its start is a DIFF where it didn't."""
        src = gpt_guest()
        dst = copy.deepcopy(src)
        x = shrink_disk(dst, 0, -GIB)
        p = x["partitions"][1]
        p["sectors"] -= GIB // 512
        p["size_bytes"] -= GIB
        p["end_lba"] -= GIB // 512
        refit_fs(p["content"], -GIB)
        out = self.bad(src, dst, "--fit", str(-GIB))
        self.assertIn("DIFF fit:storage.disks.0.partitions.2.start_lba", out)

    def test_gpt_shrink_swap_resized(self):
        """A partition that moves keeps its size."""
        src = gpt_guest()
        dst = copy.deepcopy(src)
        x = shrink_disk(dst, 0, -GIB)
        refit_fs(fit_part(x, 1, -GIB)["content"], -GIB)
        sw = x["partitions"][2]
        sw["sectors"] -= 2048
        sw["end_lba"] -= 2048
        out = self.bad(src, dst, "--fit", str(-GIB))
        self.assertIn("DIFF storage.disks.0.partitions.2.sectors", out)

    def test_gpt_table_stayed(self):
        """GPT's backup header has to move to the target's end."""
        src = gpt_guest()
        dst = copy.deepcopy(src)
        x = shrink_disk(dst, 0, -GIB)
        x["partition_table"]["backup_lba"] += GIB // 512
        refit_fs(fit_part(x, 1, -GIB)["content"], -GIB)
        out = self.bad(src, dst, "--fit", str(-GIB))
        self.assertIn("DIFF fit:storage.disks.0.partition_table.backup_lba", out)

    # LVM: the PV's extents, and the LV a shrink takes them from.

    def test_lvm_shrink(self):
        src = lvm_guest()
        dst = copy.deepcopy(src)
        x = shrink_disk(dst, 0, -GIB)
        p = fit_part(x, 2, -GIB)
        p["content"]["device_size"] -= GIB
        vg = dst["storage"]["lvm"]["volume_groups"][0]
        vg["physical_volumes"][0]["dev_size_bytes"] -= GIB
        vg["physical_volumes"][0]["pe_count"] -= 256
        root = vg["logical_volumes"][1]
        root["size_bytes"] -= GIB
        root["segments"][0]["extent_count"] -= 256
        vg["logical_volumes"][0]["segments"][0]["stripes"][1] -= 256   # compaction
        refit_fs(dst["storage"]["dm_devices"][0]["content"], -GIB)
        dst["storage"]["dm_devices"][0]["size_bytes"] -= GIB
        out = self.good(src, dst, "--fit", str(-GIB), "--fit-pct", "10")
        self.assertIn("vda3 by -1073741824", out)

    def test_lvm_shrink_wrong_lv(self):
        """Swap is never the LV a fit shrinks."""
        src = lvm_guest()
        dst = copy.deepcopy(src)
        x = shrink_disk(dst, 0, -GIB)
        p = fit_part(x, 2, -GIB)
        p["content"]["device_size"] -= GIB
        vg = dst["storage"]["lvm"]["volume_groups"][0]
        vg["physical_volumes"][0]["dev_size_bytes"] -= GIB
        vg["physical_volumes"][0]["pe_count"] -= 256
        sw = vg["logical_volumes"][0]
        sw["size_bytes"] -= GIB
        sw["segments"][0]["extent_count"] -= 256
        dst["storage"]["dm_devices"][1]["size_bytes"] -= GIB
        dst["storage"]["dm_devices"][1]["content"]["size_bytes"] -= GIB
        out = self.bad(src, dst, "--fit", str(-GIB))
        self.assertIn("DIFF storage.lvm.volume_groups.0.logical_volumes.0.size_bytes", out)

    def test_lvm_shrink_pe_count_kept(self):
        """The PV can't hold as many extents as it did."""
        src = lvm_guest()
        dst = copy.deepcopy(src)
        x = shrink_disk(dst, 0, -GIB)
        p = fit_part(x, 2, -GIB)
        p["content"]["device_size"] -= GIB
        vg = dst["storage"]["lvm"]["volume_groups"][0]
        vg["physical_volumes"][0]["dev_size_bytes"] -= GIB
        root = vg["logical_volumes"][1]
        root["size_bytes"] -= GIB
        root["segments"][0]["extent_count"] -= 256
        dst["storage"]["dm_devices"][0]["size_bytes"] -= GIB
        refit_fs(dst["storage"]["dm_devices"][0]["content"], -GIB)
        out = self.bad(src, dst, "--fit", str(-GIB))
        self.assertIn("DIFF fit:storage.lvm.volume_groups.0.physical_volumes.0.pe_count", out)

    def test_lvm_grow_keeps_lvs(self):
        """A bigger PV: the extents it gains stay free, and no LV changes."""
        src = lvm_guest()
        dst = copy.deepcopy(src)
        x = shrink_disk(dst, 0, GIB)
        p = fit_part(x, 2, GIB)
        p["content"]["device_size"] += GIB
        vg = dst["storage"]["lvm"]["volume_groups"][0]
        vg["physical_volumes"][0]["dev_size_bytes"] += GIB
        vg["physical_volumes"][0]["pe_count"] += 256
        self.good(src, dst, "--fit", str(GIB), "--fit-pct", "10")

    # LVM with free extents scattered over the PV (BackShot2's lv_split and
    # lv_resize): load drops the highest free extents first (or all of them,
    # and the rest from the largest LV's logical end), moves every extent
    # above a dropped one down, and LVM merges what that makes adjacent.

    @staticmethod
    def scattered(root, swap_at):
        """lvm_guest with root's and swap's segments [(extents, first PE)]."""
        src = lvm_guest()
        vg = src["storage"]["lvm"]["volume_groups"][0]
        for lv, segs in zip(vg["logical_volumes"], (swap_at, root)):
            le = 0
            lv["segments"] = []
            for n, pe in segs:
                lv["segments"].append({"start_extent": le, "extent_count": n, "type": "striped",
                                       "stripe_count": 1, "stripes": ["pv0", pe]})
                le += n
            lv["size_bytes"] = le * 4 * MIB
        dm_root, dm_swap = src["storage"]["dm_devices"]
        dm_root["size_bytes"] = vg["logical_volumes"][1]["size_bytes"]
        dm_root["content"] = xfs(dm_root["size_bytes"], dm_root["content"]["uuid"])
        dm_swap["size_bytes"] = vg["logical_volumes"][0]["size_bytes"]
        dm_swap["content"] = swap(dm_swap["size_bytes"])
        return src

    @staticmethod
    def fitted(src, amount, extents):
        """SRC's restored dump with the partition, PV and VG fitted by
        AMOUNT bytes (EXTENTS of them), and no LV touched yet."""
        dst = copy.deepcopy(src)
        x = shrink_disk(dst, 0, amount)
        p = fit_part(x, 2, amount)
        p["content"]["device_size"] += amount
        vg = dst["storage"]["lvm"]["volume_groups"][0]
        vg["physical_volumes"][0]["dev_size_bytes"] += amount
        vg["physical_volumes"][0]["pe_count"] += extents
        return dst, vg

    def test_lvm_scattered_free_covers_the_shrink(self):
        """Free [0,100), [1100,1300), [3710,3839): 256 extents go, 129 from the
        top run and 127 from the top of [1100,1300); what's above moves down."""
        src = self.scattered(root=[(1000, 100), (2000, 1300)], swap_at=[(410, 3300)])
        dst, vg = self.fitted(src, -GIB, -256)
        vg["logical_volumes"][1]["segments"][1]["stripes"][1] = 1173
        vg["logical_volumes"][0]["segments"][0]["stripes"][1] = 3173
        self.good(src, dst, "--fit", str(-GIB), "--fit-pct", "10")
        # A move by the wrong amount, or none, is a DIFF.
        vg["logical_volumes"][0]["segments"][0]["stripes"][1] = 3300 - 256
        out = self.bad(src, dst, "--fit", str(-GIB), "--fit-pct", "10")
        self.assertIn("DIFF fit:storage.lvm.volume_groups.0.logical_volumes.0.segments.0.stripes.1", out)
        vg["logical_volumes"][0]["segments"][0]["stripes"][1] = 3173
        vg["logical_volumes"][1]["segments"][1]["stripes"][1] = 1300
        out = self.bad(src, dst, "--fit", str(-GIB), "--fit-pct", "10")
        self.assertIn("DIFF fit:storage.lvm.volume_groups.0.logical_volumes.1.segments.1.stripes.1", out)

    def test_lvm_scattered_free_and_a_shrink_merge(self):
        """512 extents to go, 429 free: every free one and 83 from root's
        logical end.  Root's two segments end up adjacent, so they're one."""
        src = self.scattered(root=[(1000, 100), (2000, 1300)], swap_at=[(410, 3300)])
        dst, vg = self.fitted(src, -2 * GIB, -512)
        root = vg["logical_volumes"][1]
        root["segments"] = [{"start_extent": 0, "extent_count": 2917, "type": "striped",
                             "stripe_count": 1, "stripes": ["pv0", 0]}]
        root["size_bytes"] -= 83 * 4 * MIB
        vg["logical_volumes"][0]["segments"][0]["stripes"][1] = 2917
        dm = dst["storage"]["dm_devices"][0]
        dm["size_bytes"] -= 83 * 4 * MIB
        refit_fs(dm["content"], -83 * 4 * MIB)
        self.good(src, dst, "--fit", str(-2 * GIB), "--fit-pct", "20")
        # Left as two segments (LVM merges them), it's a DIFF.
        root["segments"] = [{"start_extent": 0, "extent_count": 1000, "type": "striped",
                             "stripe_count": 1, "stripes": ["pv0", 0]},
                            {"start_extent": 1000, "extent_count": 1917, "type": "striped",
                             "stripe_count": 1, "stripes": ["pv0", 1000]}]
        out = self.bad(src, dst, "--fit", str(-2 * GIB), "--fit-pct", "20")
        self.assertIn("DIFF fit:storage.lvm.volume_groups.0.logical_volumes.1.segments.0.extent_count", out)
        self.assertIn("DIFF fit:storage.lvm.volume_groups.0.logical_volumes.1.segments.1.extent_count", out)

    def test_lvm_dropped_gap_merges_segments(self):
        """Swap in two pieces with 200 free extents between them: a fit that
        drops every free extent makes them adjacent, and LVM makes them one
        (alpine324-lvm-2293ff504ed4970e); no LV shrinks."""
        src = self.scattered(root=[(3000, 0)], swap_at=[(200, 3000), (210, 3400)])
        dst, vg = self.fitted(src, -1716 * MIB, -429)
        sw = vg["logical_volumes"][0]
        sw["segments"] = [{"start_extent": 0, "extent_count": 410, "type": "striped",
                           "stripe_count": 1, "stripes": ["pv0", 3000]}]
        self.good(src, dst, "--fit", str(-1716 * MIB), "--fit-pct", "20")
        # Unmerged, but moved: a DIFF (the second segment should be gone).
        sw["segments"] = [{"start_extent": 0, "extent_count": 200, "type": "striped",
                           "stripe_count": 1, "stripes": ["pv0", 3000]},
                          {"start_extent": 200, "extent_count": 210, "type": "striped",
                           "stripe_count": 1, "stripes": ["pv0", 3200]}]
        out = self.bad(src, dst, "--fit", str(-1716 * MIB), "--fit-pct", "20")
        self.assertIn("DIFF fit:storage.lvm.volume_groups.0.logical_volumes.0.segments.1.start_extent", out)

    # A FAT after the fitted partition: its hidden sectors follow its start
    # only where they recorded it (holt/src/load/fit.rs); 0 stays 0.

    def fat_after(self, hidden):
        src = gpt_guest()
        x = src["storage"]["disks"][0]
        p = x["partitions"][2]
        p["content"] = {"type": "vfat", "usage": "filesystem", "uuid": "ABCD-0001", "label": None,
                        "version": "FAT32", "hidden_sectors": p["start_lba"] if hidden is None else hidden,
                        "total_sectors": p["sectors"]}
        dst = copy.deepcopy(src)
        y = shrink_disk(dst, 0, -GIB)
        refit_fs(fit_part(y, 1, -GIB)["content"], -GIB)
        return src, dst, y["partitions"][2]

    def test_moved_fat_hidden_sectors(self):
        src, dst, q = self.fat_after(None)
        q["content"]["hidden_sectors"] = q["start_lba"]
        self.good(src, dst, "--fit", str(-GIB), "--fit-pct", "10")
        # Left at the old start: a DIFF.
        q["content"]["hidden_sectors"] = src["storage"]["disks"][0]["partitions"][2]["start_lba"]
        out = self.bad(src, dst, "--fit", str(-GIB), "--fit-pct", "10")
        self.assertIn("DIFF fit:storage.disks.0.partitions.2.content.hidden_sectors", out)

    def test_moved_fat_hidden_sectors_zero_stays(self):
        src, dst, q = self.fat_after(0)
        self.assertEqual(q["content"]["hidden_sectors"], 0)
        self.good(src, dst, "--fit", str(-GIB), "--fit-pct", "10")
        q["content"]["hidden_sectors"] = q["start_lba"]
        self.bad(src, dst, "--fit", str(-GIB), "--fit-pct", "10")

    def test_moved_fat_other_hidden_sectors_refused(self):
        src, dst, _ = self.fat_after(63)
        out = self.bad(src, dst, "--fit", str(-GIB), "--fit-pct", "10")
        self.assertIn("records sector 63 as its first, which load refuses", out)

    # LUKS on a plain partition: what is inside follows, the header does not.

    def test_luks_shrink(self):
        src = luks_guest()
        dst = copy.deepcopy(src)
        x = shrink_disk(dst, 0, -GIB)
        fit_part(x, 1, -GIB)
        dm = dst["storage"]["dm_devices"][0]
        dm["size_bytes"] -= GIB
        refit_fs(dm["content"], -GIB)
        out = self.good(src, dst, "--fit", str(-GIB), "--fit-pct", "10")
        self.assertIn("vda2 by -1073741824", out)

    def test_luks_payload_moved(self):
        """The payload offset stays where it was, whatever the fit."""
        src = luks_guest()
        dst = copy.deepcopy(src)
        x = shrink_disk(dst, 0, -GIB)
        fit_part(x, 1, -GIB)
        x["partitions"][1]["content"]["segments"][0]["offset"] = "33554432"
        dm = dst["storage"]["dm_devices"][0]
        dm["size_bytes"] -= GIB
        refit_fs(dm["content"], -GIB)
        out = self.bad(src, dst, "--fit", str(-GIB))
        self.assertIn("DIFF storage.disks.0.partitions.1.content.segments.0.offset", out)

    def test_luks_inside_stayed(self):
        """The btrfs inside has to be remade at the partition's new size."""
        src = luks_guest()
        dst = copy.deepcopy(src)
        x = shrink_disk(dst, 0, -GIB)
        fit_part(x, 1, -GIB)
        out = self.bad(src, dst, "--fit", str(-GIB))
        self.assertIn("DIFF fit:storage.dm_devices.0.size_bytes", out)

    # What load refuses: judge.py says so rather than judging it.

    def test_tie_refused(self):
        src = mbr_guest()
        src["storage"]["disks"][0]["partitions"][1]["sectors"] = 27260928
        src["storage"]["disks"][0]["partitions"][1]["size_bytes"] = 27260928 * 512
        out = self.bad(src, copy.deepcopy(src), "--fit", str(-GIB))
        self.assertIn("tie for largest", out)

    def test_unsupported_stack_refused(self):
        src = mbr_guest()
        src["storage"]["disks"][0]["partitions"][2]["content"] = {
            "type": "zfs_member", "usage": "filesystem", "uuid": "eeeeeeee-0001", "label": "rpool"}
        out = self.bad(src, copy.deepcopy(src), "--fit", str(-GIB))
        self.assertIn("holds zfs_member, which load refuses to fit", out)

    def test_shrink_over_pct_refused(self):
        """1 GiB off a 13 GiB partition is more than 1%: load refuses it."""
        src = mbr_guest()
        dst = copy.deepcopy(src)
        x = shrink_disk(dst, 0, -GIB)
        refit_fs(fit_part(x, 2, -GIB)["content"], -GIB)
        out = self.bad(src, dst, "--fit", str(-GIB), "--fit-pct", "1")
        self.assertIn("more than 1% of vda3", out)

    # A target only the source's slack was lost from: nothing is fitted.

    def test_slack_absorbs_the_loss(self):
        src = gpt_guest()
        # / 2 GiB shorter, so the last partition ends 2 GiB before the end.
        x = src["storage"]["disks"][0]
        x["partitions"][1]["sectors"] -= 4194304
        x["partitions"][1]["size_bytes"] -= 2 * GIB
        x["partitions"][1]["end_lba"] -= 4194304
        x["partitions"][1]["content"] = xfs(x["partitions"][1]["size_bytes"])
        for q in x["partitions"][2:]:
            q["start_lba"] -= 4194304
            q["start_bytes"] -= 2 * GIB
            q["end_lba"] -= 4194304
        dst = copy.deepcopy(src)
        shrink_disk(dst, 0, -GIB)
        self.good(src, dst, "--fit", str(-GIB), "--fit-pct", "10")

    def test_table_less_disk(self):
        """A disk with no partition table: there is nothing to fit on it, so
        its new size is allowed either way (a target's changes, a guest's
        /dev/ram0, which is no target, keeps the size it had)."""
        src = mbr_guest()
        src["storage"]["disks"].append(
            {"name": "vdb", "path": "/dev/vdb", "kind": "disk", "size_bytes": 4 * GIB, "size": "4 GiB",
             "logical_sector_size": 512, "partition_table": None, "partitions": [], "content": None,
             "mounted_at": []})
        for second in (0, -GIB):
            dst = copy.deepcopy(src)
            x = shrink_disk(dst, 0, -GIB)
            refit_fs(fit_part(x, 2, -GIB)["content"], -GIB)
            if second:
                dst["storage"]["disks"][1]["size_bytes"] += second
                dst["storage"]["disks"][1]["size"] = "3 GiB"
            self.good(src, dst, "--fit", str(-GIB), "--fit-pct", "10")

    # --fit-plan: what the drivers check their own run against.

    def plan(self, src, *args):
        return run_plan(src, *args)

    def test_fit_plan(self):
        rc, out = self.plan(mbr_guest(), str(-GIB), "--fit-pct", "10")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.strip(), "vda vda3 -1073741824 13957595136 ext4")
        rc, out = self.plan(mbr_guest(), str(-GIB), "--fit-pct", "1")
        self.assertEqual(rc, 1, out)
        self.assertIn("problem: disk vda: 1073741824 is more than 1% of vda3", out)

    def test_fit_plan_names_the_stack(self):
        """A stack load won't fit: the line still names it, for the refuse
        variant to check the refusal against."""
        src = mbr_guest()
        src["storage"]["disks"][0]["partitions"][2]["content"] = {
            "type": "linux_raid_member", "usage": "raid", "uuid": "ffffffff-0001"}
        rc, out = self.plan(src, str(-GIB), "--fit-pct", "10")
        self.assertEqual(rc, 1, out)
        self.assertIn("vda vda3 -1073741824 13957595136 linux_raid_member", out)
        self.assertIn("problem: disk vda: vda3 holds linux_raid_member", out)

    # --grow still judges what it did, and the two modes aren't mixed.

    def test_grow_mode_untouched(self):
        src = mbr_guest()
        dst = copy.deepcopy(src)
        shrink_disk(dst, 0, GIB)
        self.good(src, dst, "--grow", str(GIB))

    def test_grow_and_fit_refused(self):
        src = mbr_guest()
        rc, out = self.judge(src, copy.deepcopy(src), "--grow", str(GIB), "--fit", str(GIB))
        self.assertEqual(rc, 1, out)
        self.assertIn("two modes", out)


class Rig(unittest.TestCase):
    """What ht1.sh makes of a --fit-plan: the stacks it checks the refuse and
    nearfull variants' refusal against.  The pattern is grepped for in
    load's output, so anything in it that matches text of any kind would
    pass a refusal given for quite another reason."""

    def holds(self, plan):
        """ht1.sh's own `holds` pipeline, taken from the script itself, over
        the plan text PLAN."""
        with open(os.path.join(HERE, "ht1.sh")) as f:
            m = re.search(r"^\s*holds=\$\((awk .*?) <<<", f.read(), re.M)
        self.assertTrue(m, "ht1.sh has no holds=$(awk ...) line to take")
        r = subprocess.run(["bash", "-c", f"{m.group(1)} | sort -u | paste -sd'|' -"],
                           input=plan, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def unrelated(self, want):
        """Whether the pattern WANT matches a refusal of another kind, as the
        grep in ht1.sh's fit_checks would."""
        r = subprocess.run(["grep", "-qiE", f"({want})"], input="bko-holt load: vda -> /dev/vdb: the target is read-only\n",
                           capture_output=True, text=True)
        return r.returncode == 0

    def test_a_partitionless_disk_names_no_stack(self):
        """A zvol beside the disks has no partition to fit, so the plan line
        for it holds a "-", which is no stack to check a refusal against."""
        src = mbr_guest()
        src["storage"]["disks"][0]["partitions"][2]["content"] = {
            "type": "zfs_member", "usage": "filesystem", "uuid": "eeeeeeee-0001", "label": "rpool"}
        src["storage"]["disks"].append(zvol("zd0", 4 * GIB))
        rc, out = run_plan(src, str(-GIB), "--fit-pct", "10")
        self.assertEqual(rc, 1, out)
        self.assertIn("zd0 - 0 0 -", out)
        holds = self.holds(out)
        self.assertEqual(holds, "zfs_member")
        self.assertFalse(self.unrelated(holds))

    def test_a_short_line_names_no_stack(self):
        """ht1.sh reads judge.py's stderr as part of the plan, and a line
        with no fifth field would leave an empty branch in the alternation,
        which matches every text there is."""
        holds = self.holds("vda vda3 -1073741824 13957595136 ext4\njudge.py: no such file\n")
        self.assertEqual(holds, "ext4")
        self.assertFalse(self.unrelated(holds))


FSMETA = os.path.join(os.path.dirname(HERE), "compare", "fsmeta.py")


def meta(kind, **kw):
    """One path's fsmeta.py record, as `fsmeta.py record` writes it."""
    e = {"type": kind, "mode": 0o777 if kind == "symlink" else 0o644, "uid": 0, "gid": 0,
         "mtime": 1, "xattrs": {"security.selinux": "73797374656d5f753a6f626a6563745f723a7573725f743a733000"}}
    if kind == "file":
        e.update(size=3, sha256="ab", holes=False)
    if kind == "symlink":
        e["target"] = "usr/bin"
    e.update(kw)
    return e


class Fs(unittest.TestCase):
    """judge.py fs over an ostree repository's links (kinoite44, B6)."""

    def judge(self, before, after):
        with tempfile.TemporaryDirectory() as tmp:
            s, t = os.path.join(tmp, "src-meta.json"), os.path.join(tmp, "dst-meta.json")
            for path, d in ((s, before), (t, after)):
                with open(path, "w") as f:
                    json.dump(d, f)
            r = subprocess.run([sys.executable, "-I", JUDGE, "fs", FSMETA, s, t], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    LINKED = ["ostree/deploy/default/deploy/c.0/bin", "ostree/deploy/default/deploy/c.1/bin",
              "ostree/repo/objects/ab/cd.file"]

    def test_hardlinked_symlinks_come_back_separate(self):
        before = {p: meta("symlink", links=self.LINKED) for p in self.LINKED}
        after = {p: meta("symlink") for p in self.LINKED}
        rc, out = self.judge(before, after)
        self.assertEqual(rc, 0, out)
        self.assertIn("hardlinked symlink restored as separate symlinks: the archivers relink regular files only): 3", out)

    def test_regular_files_must_stay_linked(self):
        before = {p: meta("file", links=self.LINKED) for p in self.LINKED}
        after = {p: meta("file") for p in self.LINKED}
        rc, out = self.judge(before, after)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF hard links: 3", out)

    def test_symlinks_linked_otherwise_are_a_difference(self):
        """Separate symlinks are what the archivers make; symlinks linked
        into another group than the source's aren't."""
        before = {p: meta("symlink", links=self.LINKED) for p in self.LINKED}
        after = {p: meta("symlink", links=self.LINKED[:2]) for p in self.LINKED[:2]}
        after[self.LINKED[2]] = meta("symlink")
        rc, out = self.judge(before, after)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF hard links: 2", out)

    def test_a_symlinks_target_still_counts(self):
        before = {p: meta("symlink", links=self.LINKED) for p in self.LINKED}
        after = {p: meta("symlink") for p in self.LINKED}
        after[self.LINKED[0]]["target"] = "usr/sbin"
        rc, out = self.judge(before, after)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF symlink target: 1", out)



class FsEspAtBoot(unittest.TestCase):
    """judge.py fs over what bootctl install does to an ESP mounted as /boot
    (chimera-uefi, C3): a new random seed, systemd-boot's loaders put back
    and /etc/kernel/entry-token written again."""

    SRC = {"boot": meta("dir"), "boot/EFI": meta("dir"), "boot/EFI/BOOT": meta("dir"),
           "boot/EFI/BOOT/BOOTX64.EFI": meta("file"), "boot/EFI/systemd": meta("dir"),
           "boot/EFI/systemd/systemd-bootx64.efi": meta("file"), "boot/EFI/chimera": meta("dir"),
           "boot/EFI/chimera/x": meta("file"), "boot/loader": meta("dir"),
           "boot/loader/random-seed": meta("file"), "boot/loader/loader.conf": meta("file"),
           "etc/kernel": meta("dir"), "etc/kernel/entry-token": meta("file"),
           "etc/kernel/cmdline": meta("file")}

    judge = Fs.judge

    def judge_changed(self, changes):
        a = copy.deepcopy(self.SRC)
        for p, kw in changes.items():
            a[p].update(kw)
        return self.judge(self.SRC, a)

    def test_bootctl_install_is_the_boot_stages(self):
        """chim3's three DIFF lines, now the boot stage's."""
        rc, out = self.judge_changed({
            "boot/loader/random-seed": {"sha256": "cd", "mtime": 2},
            "etc/kernel/entry-token": {"mtime": 2},
            "boot/EFI/BOOT": {"mtime": 2}, "boot/EFI/systemd": {"mtime": 2}, "etc/kernel": {"mtime": 2}})
        self.assertEqual(rc, 0, out)
        self.assertNotIn("DIFF", out)
        self.assertIn("bootctl install rewrites systemd-boot in an ESP at /boot (Chimera)): 2: boot/EFI/BOOT, boot/EFI/systemd", out)
        self.assertIn("bootctl install writes a new random seed in an ESP at /boot): 2: boot/loader/random-seed, boot/loader/random-seed", out)
        self.assertIn("1: etc/kernel/entry-token", out)
        self.assertIn("bootctl install replaces etc/kernel/entry-token): 1: etc/kernel", out)

    def test_the_loaders_themselves_may_be_rewritten(self):
        rc, out = self.judge_changed({"boot/EFI/BOOT/BOOTX64.EFI": {"sha256": "cd", "mtime": 2},
                                      "boot/EFI/systemd/systemd-bootx64.efi": {"sha256": "cd", "mtime": 2}})
        self.assertEqual(rc, 0, out)
        self.assertNotIn("DIFF", out)

    def test_another_loaders_directory_still_counts(self):
        rc, out = self.judge_changed({"boot/EFI/chimera": {"mtime": 2}, "boot/EFI/chimera/x": {"sha256": "cd"}})
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF content: 1: boot/EFI/chimera/x", out)
        self.assertIn("DIFF mtime (dirs): 1: boot/EFI/chimera", out)

    def test_the_rest_of_loader_and_etc_kernel_still_count(self):
        rc, out = self.judge_changed({"boot/loader/loader.conf": {"sha256": "cd"},
                                      "etc/kernel/cmdline": {"sha256": "cd"}})
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF content: 2: boot/loader/loader.conf, etc/kernel/cmdline", out)

    def test_a_missing_loader_still_counts(self):
        a = copy.deepcopy(self.SRC)
        del a["boot/EFI/BOOT/BOOTX64.EFI"], a["etc/kernel/entry-token"]
        rc, out = self.judge(self.SRC, a)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF missing: 2: boot/EFI/BOOT/BOOTX64.EFI, etc/kernel/entry-token", out)

    def test_a_grub_boots_lower_case_efi_is_not_an_esp_at_boot(self):
        """boot/efi/... (an ESP at /boot/efi) and boot/grub2 keep their own
        rules; boot/EFI's don't reach a GRUB system's other /boot files."""
        src = {"boot/efi/EFI/fedora": meta("dir"), "boot/efi/EFI/fedora/x": meta("file"),
               "boot/vmlinuz-6.1": meta("file"), "boot/config-6.1": meta("file")}
        a = copy.deepcopy(src)
        for p in a:
            a[p].update(sha256="cd", mtime=2)
        rc, out = self.judge(src, a)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF content: 2: boot/config-6.1, boot/vmlinuz-6.1", out)
        self.assertNotIn("in an ESP at /boot", out)


class FsLimine(unittest.TestCase):
    """judge.py fs over what load's boot stage does on CachyOS with Limine
    (czrt2, cachyos-zfs): mkinitcpio -P rebuilds the initramfs and
    limine-mkinitcpio-hook copies it into /boot/MACHINE-ID/KERNEL/."""

    M = "e464d0233015401f967ec0aedf7f3cda"
    SRC = {"boot": meta("dir"), f"boot/{M}": meta("dir"), f"boot/{M}/linux-cachyos": meta("dir"),
           f"boot/{M}/linux-cachyos/initramfs": meta("file"), f"boot/{M}/linux-cachyos/vmlinuz": meta("file"),
           f"boot/{M}/linux-cachyos-lts": meta("dir"), f"boot/{M}/linux-cachyos-lts/initramfs": meta("file"),
           "boot/limine.conf": meta("file"), "boot/notamachineid": meta("dir"),
           "boot/notamachineid/initramfs": meta("file")}

    judge = Fs.judge

    def changed(self, changes):
        a = copy.deepcopy(self.SRC)
        for p, kw in changes.items():
            a[p].update(kw)
        return self.judge(self.SRC, a)

    def test_the_hooks_copies_are_the_boot_stages(self):
        rc, out = self.changed({
            f"boot/{self.M}/linux-cachyos/initramfs": {"sha256": "cd", "mtime": 2},
            f"boot/{self.M}/linux-cachyos-lts/initramfs": {"sha256": "cd", "mtime": 2},
            f"boot/{self.M}/linux-cachyos": {"mtime": 2}, f"boot/{self.M}/linux-cachyos-lts": {"mtime": 2},
            "boot/limine.conf": {"sha256": "cd", "mtime": 2}})
        self.assertEqual(rc, 0, out)
        self.assertNotIn("DIFF", out)
        self.assertIn(f"(CachyOS's Limine)): 4: boot/{self.M}/linux-cachyos-lts/initramfs", out)

    def test_the_kernel_and_other_dirs_still_count(self):
        rc, out = self.changed({
            f"boot/{self.M}/linux-cachyos/vmlinuz": {"sha256": "cd"},
            "boot/notamachineid/initramfs": {"sha256": "cd"}, f"boot/{self.M}": {"mode": 0o700}})
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF content: 2: boot/{self.M}/linux-cachyos/vmlinuz, boot/notamachineid/initramfs", out)
        self.assertIn(f"DIFF mode: 1: boot/{self.M}", out)

    def test_a_missing_initramfs_still_counts(self):
        a = copy.deepcopy(self.SRC)
        del a[f"boot/{self.M}/linux-cachyos/initramfs"]
        rc, out = self.judge(self.SRC, a)
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF missing: 1: boot/{self.M}/linux-cachyos/initramfs", out)


class FsGentoo(unittest.TestCase):
    """judge.py fs over what load's boot stage does on Gentoo (G5):
    grub-install writes EFI/gentoo/grubx64.efi in an ESP at /efi (gentoo)
    or at /boot (gentoo-systemd), dracut rebuilds /boot/initramfs-K.img and
    grub-mkconfig rewrites /boot/grub."""

    K = "6.18.63-gentoo-dist-bin"
    SRC = {"boot": meta("dir"), f"boot/initramfs-{K}.img": meta("file"), f"boot/vmlinuz-{K}": meta("file"),
           f"boot/config-{K}": meta("file"), "boot/grub": meta("dir"), "boot/grub/grub.cfg": meta("file"),
           "efi": meta("dir"), "efi/EFI": meta("dir"), "efi/EFI/gentoo": meta("dir"),
           "efi/EFI/gentoo/grubx64.efi": meta("file"), "efi/EFI/BOOT": meta("dir"),
           "efi/EFI/BOOT/BOOTX64.EFI": meta("file"), "efi/NvVars": meta("file"),
           "etc/kernel": meta("dir"), "etc/kernel/install.conf": meta("file")}
    ESP_AT_BOOT = {"boot": meta("dir"), "boot/EFI": meta("dir"), "boot/EFI/gentoo": meta("dir"),
                   "boot/EFI/gentoo/grubx64.efi": meta("file"), "boot/EFI/BOOT": meta("dir"),
                   "boot/EFI/BOOT/BOOTX64.EFI": meta("file"), f"boot/kernel-{K}": meta("file"),
                   f"boot/initramfs-{K}.img": meta("file"), "boot/grub": meta("dir"),
                   "boot/grub/grub.cfg": meta("file")}

    judge = Fs.judge

    def changed(self, src, changes):
        a = copy.deepcopy(src)
        for p, kw in changes.items():
            a[p].update(kw)
        return self.judge(src, a)

    def test_the_boot_stage_at_efi(self):
        rc, out = self.changed(self.SRC, {
            "efi/EFI/gentoo/grubx64.efi": {"sha256": "cd", "mtime": 2}, "efi/EFI/gentoo": {"mtime": 2},
            f"boot/initramfs-{self.K}.img": {"sha256": "cd", "mtime": 2}, "boot/grub/grub.cfg": {"sha256": "cd"},
            "boot": {"mtime": 2}})
        self.assertEqual(rc, 0, out)
        self.assertNotIn("DIFF", out)
        self.assertIn("grub-install writes the loader in an ESP at /efi (Gentoo)): 3: efi/EFI/gentoo, efi/EFI/gentoo/grubx64.efi, efi/EFI/gentoo/grubx64.efi", out)

    def test_the_boot_stage_with_the_esp_at_boot(self):
        rc, out = self.changed(self.ESP_AT_BOOT, {
            "boot/EFI/gentoo/grubx64.efi": {"sha256": "cd", "mtime": 2}, "boot/EFI/gentoo": {"mtime": 2},
            f"boot/initramfs-{self.K}.img": {"sha256": "cd"}, "boot/grub/grub.cfg": {"sha256": "cd"}})
        self.assertEqual(rc, 0, out)
        self.assertNotIn("DIFF", out)
        self.assertIn("grub-install writes the loader in an ESP at /boot (Gentoo with systemd)): 3: boot/EFI/gentoo, boot/EFI/gentoo/grubx64.efi, boot/EFI/gentoo/grubx64.efi", out)

    def test_the_rest_of_the_esp_and_boot_still_count(self):
        """The kernel, its config, NvVars and install.conf aren't the boot
        stage's.  (EFI/BOOT/BOOTX64.EFI, which load leaves alone without
        --removable, is excused by bootctl's rules for AerynOS and Chimera,
        whatever the system.)"""
        rc, out = self.changed(self.SRC, {
            f"boot/vmlinuz-{self.K}": {"sha256": "cd"}, f"boot/config-{self.K}": {"sha256": "cd"},
            "efi/NvVars": {"sha256": "cd"}, "etc/kernel/install.conf": {"sha256": "cd"}})
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF content: 4: boot/config-{self.K}, boot/vmlinuz-{self.K}, efi/NvVars, etc/kernel/install.conf", out)
        rc, out = self.changed(self.ESP_AT_BOOT, {f"boot/kernel-{self.K}": {"sha256": "cd"}})
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF content: 1: boot/kernel-{self.K}", out)

    def test_dracut_makes_the_initramfs_0600(self):
        """g5-1: load's dracut writes the image 0600, installkernel's was 0644."""
        rc, out = self.changed(self.SRC, {f"boot/initramfs-{self.K}.img": {"sha256": "cd", "mode": 0o600}})
        self.assertEqual(rc, 0, out)
        self.assertIn(f"dracut makes Gentoo's initramfs 0600 where installkernel had left it 0644): 1: boot/initramfs-{self.K}.img", out)

    def test_other_modes_still_count(self):
        """Only a Gentoo kernel's initramfs; its kernel's mode, and another
        distribution's initramfs, still count."""
        src = dict(self.SRC, **{"boot/initramfs-linux.img": meta("file"), "boot/initramfs-6.17.1-300.fc44.x86_64.img": meta("file")})
        rc, out = self.changed(src, {f"boot/vmlinuz-{self.K}": {"mode": 0o600}, "boot/initramfs-linux.img": {"mode": 0o600},
                                     "boot/initramfs-6.17.1-300.fc44.x86_64.img": {"mode": 0o600}})
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF mode: 3: boot/initramfs-6.17.1-300.fc44.x86_64.img, boot/initramfs-linux.img, boot/vmlinuz-{self.K}", out)

    def test_a_missing_loader_still_counts(self):
        a = copy.deepcopy(self.SRC)
        del a["efi/EFI/gentoo/grubx64.efi"]
        rc, out = self.judge(self.SRC, a)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF missing: 1: efi/EFI/gentoo/grubx64.efi", out)

    def test_other_distributions_loaders_get_no_gentoo_rule(self):
        src = {"efi/EFI/gentoo-old": meta("dir"), "efi/EFI/gentoo-old/x": meta("file"),
               "boot/EFI/gentoox": meta("dir")}
        a = copy.deepcopy(src)
        for p in a:
            a[p].update(sha256="cd", mtime=2)
        rc, out = self.judge(src, a)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF content: 1: efi/EFI/gentoo-old/x", out)
        self.assertNotIn("(Gentoo", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
