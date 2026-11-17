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


def geli_guest(where="content"):
    """freebsd15-geli's layout (docs/design/geli.md): an ESP, swap, and zroot's
    member inside a GELI provider on p3, the geli record WHERE the dump may
    put it ("content": in the partition's content; "partition": on the
    partition; "flat": the content itself)."""
    p3 = 28823552
    zm = {"type": "zfs_member", "usage": "filesystem", "uuid": "1000000000000000001", "label": "zroot",
          "version": "5000", "pool_name": "zroot", "pool_guid": "1000000000000000001",
          "vdev_guid": "1000000000000000002", "top_guid": "1000000000000000002", "txg": 388, "hostname": "host1"}
    rec = {"version": 7, "flags": ["BOOT", "GELIBOOT", "AUTORESIZE"], "ealgo": "AES-XTS", "keylen": 256,
           "aalgo": None, "provsize": p3 * 512, "sectorsize": 4096, "keys": [0], "iterations": 3031038,
           "provider": "vtbd0p3.eli", "attached": True, "content": [zm], "keyed_by": "passphrase", "keyfiles": []}
    extra = {}
    if where == "content":
        content = {"type": "geli", "usage": "crypto", "geli": rec}
    elif where == "partition":
        content, extra = {"type": "geli", "usage": "crypto"}, {"geli": rec}
    else:
        content = dict(rec, type="geli", usage="crypto")
    d = dump([disk("vtbd0", 16 * GIB, [
        part("vtbd0p1", 1, 40, 532480, {"type": "vfat"}, gpt=True),
        part("vtbd0p2", 2, 532520, 4194304, swap(4194304 * 512), gpt=True),
        part("vtbd0p3", 3, 4726824, p3, content, gpt=True, **extra),
    ], gpt=True)])
    d["os"] = {"family": "freebsd", "pretty_name": "FreeBSD 15.1-RELEASE-p6"}
    return d


def geli_rec(d, where="content"):
    p = d["storage"]["disks"][0]["partitions"][2]
    return {"content": lambda: p["content"]["geli"], "partition": lambda: p["geli"], "flat": lambda: p["content"]}[where]()


class Geli(unittest.TestCase):
    """judge.py over GELI's record: every field compared, the probe inside it
    judged as a partition's content, provsize allowed to follow its partition."""

    def judge(self, src, dst):
        with tempfile.TemporaryDirectory() as tmp:
            s, t = os.path.join(tmp, "src.json"), os.path.join(tmp, "dst.json")
            for path, d in ((s, src), (t, dst)):
                with open(path, "w") as f:
                    json.dump(d, f)
            r = subprocess.run([sys.executable, "-I", JUDGE, s, t], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def test_inner_probe_as_a_partitions(self):
        for where in ("content", "partition", "flat"):
            src = geli_guest(where)
            dst = copy.deepcopy(src)
            zm = geli_rec(dst, where)["content"][0]
            zm.update(hostname="localhost", txg=12)
            rc, out = self.judge(src, dst)
            self.assertEqual(rc, 0, f"{where}: {out}")
            self.assertIn("a ZFS label names the host", out)

    def test_geli_fields_compared(self):
        for field, value in (("iterations", 0), ("flags", ["BOOT"]), ("ealgo", "AES-CBC"), ("keys", [0, 1]), ("sectorsize", 512)):
            for where in ("content", "partition", "flat"):
                src = geli_guest(where)
                dst = copy.deepcopy(src)
                geli_rec(dst, where)[field] = value
                rc, out = self.judge(src, dst)
                self.assertEqual(rc, 1, f"{where} {field}: {out}")
                self.assertIn(f".{field}", out)

    def test_inner_pool_compared(self):
        src = geli_guest()
        dst = copy.deepcopy(src)
        geli_rec(dst)["content"][0]["pool_name"] = "zroot2"
        rc, out = self.judge(src, dst)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF storage.disks.0.partitions.2.content.geli.content.0.pool_name", out)

    def test_provsize_follows_its_partition(self):
        src = geli_guest()
        dst = copy.deepcopy(src)
        p = dst["storage"]["disks"][0]["partitions"][2]
        p["sectors"] += 2048
        p["size_bytes"] += 2048 * 512
        p["end_lba"] += 2048
        geli_rec(dst)["provsize"] = p["size_bytes"]
        rc, out = self.judge(src, dst)
        self.assertEqual(rc, 1, out)   # the partition's own size is a DIFF ...
        self.assertIn("DIFF storage.disks.0.partitions.2.size_bytes", out)
        self.assertNotIn("DIFF storage.disks.0.partitions.2.content.geli.provsize", out)   # ... its provider's, not
        self.assertIn("a GELI provider's provsize", out)

    def test_provsize_alone_is_a_difference(self):
        src = geli_guest()
        dst = copy.deepcopy(src)
        geli_rec(dst)["provsize"] -= 4096
        rc, out = self.judge(src, dst)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF storage.disks.0.partitions.2.content.geli.provsize", out)


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


class FsGaruda(unittest.TestCase):
    """judge.py fs over what load's boot stage does on Garuda (ga4-2):
    dracut-rebuild dd-copies the kernel to /boot again, the same bytes in a
    new file, and rebuilds both images."""

    SRC = {"boot": meta("dir"), "boot/vmlinuz-linux-garuda": meta("file", atime=1),
           "boot/initramfs-linux-garuda.img": meta("file"), "boot/initramfs-linux-garuda-fallback.img": meta("file")}

    judge = Fs.judge
    changed = FsLimine.changed

    def test_the_kernels_new_times_are_the_boot_stages(self):
        rc, out = self.changed({
            "boot/vmlinuz-linux-garuda": {"mtime": 2, "atime": 2},
            "boot/initramfs-linux-garuda.img": {"sha256": "cd", "mtime": 2},
            "boot/initramfs-linux-garuda-fallback.img": {"sha256": "cd", "mtime": 2}, "boot": {"mtime": 2}})
        self.assertEqual(rc, 0, out)
        self.assertNotIn("DIFF", out)
        self.assertIn("copies each kernel to /boot again with dd before building its images): 2: boot/vmlinuz-linux-garuda, boot/vmlinuz-linux-garuda", out)

    def test_the_kernels_content_mode_and_owner_still_count(self):
        rc, out = self.changed({"boot/vmlinuz-linux-garuda": {"sha256": "cd", "mtime": 2, "mode": 0o600, "uid": 1}})
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF content: 1: boot/vmlinuz-linux-garuda", out)
        self.assertIn("DIFF mode: 1: boot/vmlinuz-linux-garuda", out)
        self.assertIn("DIFF owner: 1: boot/vmlinuz-linux-garuda", out)


class SwapOnZram(unittest.TestCase):
    """pikaos4 (p6-3): zram-tools sizes its swap from MemTotal, which moved
    4 KiB with the rebuilt booster.img's size; a partition's swap still counts."""

    judge = Geli.judge

    def swaps(self, device, size):
        return dump([disk("vda", 16 * GIB, [part("vda1", 1, 2048, 2097152, ext4(1 * GIB), gpt=True)], gpt=True)],
                    swaps=[{"file": f"/dev/{device}", "type": "partition", "size_bytes": size, "used_bytes": 0, "priority": 100, "device": device}])

    def test_a_zram_swaps_size_may_move(self):
        rc, out = self.judge(self.swaps("zram0", 2044968960), self.swaps("zram0", 2044964864))
        self.assertEqual(rc, 0, out)
        self.assertIn("a swap on zram is made anew at each boot", out)

    def test_a_partitions_swap_size_still_counts(self):
        rc, out = self.judge(self.swaps("vda2", 2044968960), self.swaps("vda2", 2044964864))
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF storage.swaps.0.size_bytes", out)


class FsRefind(unittest.TestCase):
    """judge.py fs over what load's boot stage does on PikaOS (P6): booster
    rebuilds /boot/booster.img-K, and rEFInd is kept, not reinstalled: its
    ESP files and refind_linux.conf must be the source's."""

    K = "7.1.8-pikaos"
    SRC = {"boot": meta("dir"), f"boot/booster.img-{K}": meta("file"), f"boot/booster.img-{K}.dpkg-bak": meta("file"),
           f"boot/vmlinuz-{K}": meta("file"), "boot/refind_linux.conf": meta("file"),
           "boot/efi": meta("dir"), "boot/efi/EFI": meta("dir"), "boot/efi/EFI/refind": meta("dir"),
           "boot/efi/EFI/refind/refind_x64.efi": meta("file"), "boot/efi/EFI/refind/refind.conf": meta("file"),
           "boot/efi/EFI/refind/drivers_x64": meta("dir"), "boot/efi/EFI/refind/drivers_x64/ext4_x64.efi": meta("file"),
           "boot/efi/EFI/BOOT": meta("dir"), "boot/efi/EFI/BOOT/bootx64.efi": meta("file"),
           "boot/efi/EFI/BOOT/refind.conf": meta("file"), "boot/efi/EFI/other": meta("dir"),
           "boot/efi/EFI/other/x.efi": meta("file")}
    # pikaos4-luks's "BOOTX64 mode": rEFInd in the fallback path only.
    FALLBACK = {p: m for p, m in SRC.items() if not p.startswith("boot/efi/EFI/refind")}

    judge = Fs.judge

    def changed(self, src, changes):
        a = copy.deepcopy(src)
        for p, kw in changes.items():
            a[p].update(kw)
        return self.judge(src, a)

    def test_booster_is_the_boot_stages(self):
        rc, out = self.changed(self.SRC, {f"boot/booster.img-{self.K}": {"sha256": "cd", "mtime": 2}, "boot": {"mtime": 2}})
        self.assertEqual(rc, 0, out)
        self.assertNotIn("DIFF", out)
        self.assertIn(f"booster build rebuilds each kernel's booster.img (PikaOS)): 2: boot/booster.img-{self.K}", out)
        self.assertIn("rEFInd kept: 9 paths", out)

    def test_booster_backups_and_the_kernel_still_count(self):
        rc, out = self.changed(self.SRC, {f"boot/booster.img-{self.K}.dpkg-bak": {"sha256": "cd"}, f"boot/vmlinuz-{self.K}": {"sha256": "cd"}})
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF content: 2: boot/booster.img-{self.K}.dpkg-bak, boot/vmlinuz-{self.K}", out)

    def test_refind_must_be_kept(self):
        """Every file and directory of EFI/refind and EFI/BOOT, and
        refind_linux.conf: no ESP rule excuses them on a rEFInd source."""
        for src in (self.SRC, self.FALLBACK):
            kept = [p for p in src if p.startswith(("boot/efi/EFI/refind", "boot/efi/EFI/BOOT")) or p == "boot/refind_linux.conf"]
            rc, out = self.changed(src, {p: {"sha256": "cd", "mtime": 2} for p in kept})
            files = [p for p in kept if src[p]["type"] == "file"]
            self.assertEqual(rc, 1, out)
            self.assertIn(f"DIFF content: {len(files)}:", out)
            self.assertIn(f"DIFF mtime (dirs): {len(kept) - len(files)}:", out)
            self.assertIn(f"rEFInd kept: {len(kept)} paths", out)
            self.assertIn(" 0 from the boot stage", out)

    def test_refind_extra_or_missing_files_count(self):
        a = copy.deepcopy(self.SRC)
        del a["boot/efi/EFI/BOOT/refind.conf"]
        a["boot/efi/EFI/refind/refind.conf-sample"] = meta("file")
        a["boot/efi/EFI/BOOT/grubx64.efi"] = meta("file")
        rc, out = self.judge(self.SRC, a)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF missing: 1: boot/efi/EFI/BOOT/refind.conf", out)
        self.assertIn("DIFF extra: 2:", out)

    def test_other_loaders_on_a_refind_esp_keep_their_rule(self):
        rc, out = self.changed(self.SRC, {"boot/efi/EFI/other/x.efi": {"sha256": "cd"}})
        self.assertEqual(rc, 0, out)
        self.assertIn("grub-install writes the ESPs' loaders", out)

    def test_other_systems_esp_rules_are_unchanged(self):
        """No rEFInd: EFI/BOOT is grub-install's or bootctl's to rewrite, as before."""
        src = {"boot/efi/EFI": meta("dir"), "boot/efi/EFI/BOOT": meta("dir"), "boot/efi/EFI/BOOT/BOOTX64.EFI": meta("file"),
               "boot/efi/EFI/debian": meta("dir"), "boot/efi/EFI/debian/grubx64.efi": meta("file")}
        a = copy.deepcopy(src)
        for p in a:
            a[p].update(sha256="cd", mtime=2)
        rc, out = self.judge(src, a)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("rEFInd", out)


class FsAnduinos(unittest.TestCase):
    """judge.py fs over what load's boot stage does on AnduinOS 2.0.5 (A5):
    update-grub and the i386-pc grub-install rewrite /boot/grub, dracut
    rebuilds /boot/initrd.img-K (0600 on the source and after), and the ESP
    is kept: shim, MokManager and the MOK-signed GRUB in EFI/AnduinOS and
    EFI/BOOT must be the source's bytes."""

    K = "7.0.0-38-generic"
    LOADERS = ["boot/efi/EFI/AnduinOS/shimx64.efi", "boot/efi/EFI/AnduinOS/grubx64.efi", "boot/efi/EFI/AnduinOS/mmx64.efi",
               "boot/efi/EFI/BOOT/BOOTX64.EFI", "boot/efi/EFI/BOOT/grubx64.efi", "boot/efi/EFI/BOOT/mmx64.efi"]
    SRC = {"boot": meta("dir"), f"boot/initrd.img-{K}": meta("file", mode=0o600), f"boot/vmlinuz-{K}": meta("file", mode=0o600),
           f"boot/System.map-{K}": meta("file", mode=0o600), f"boot/config-{K}": meta("file"),
           "boot/grub": meta("dir"), "boot/grub/grub.cfg": meta("file"), "boot/grub/grubenv": meta("file"),
           "boot/grub/i386-pc": meta("dir"), "boot/grub/i386-pc/core.img": meta("file"),
           "boot/grub/x86_64-efi": meta("dir"), "boot/grub/x86_64-efi/normal.mod": meta("file"),
           "boot/efi": meta("dir"), "boot/efi/EFI": meta("dir"), "boot/efi/EFI/AnduinOS": meta("dir"),
           "boot/efi/EFI/AnduinOS/grub.cfg": meta("file"), "boot/efi/EFI/AnduinOS/BOOTX64.CSV": meta("file"),
           "boot/efi/EFI/BOOT": meta("dir"), "boot/efi/EFI/BOOT/grub.cfg": meta("file"),
           **{p: meta("file", mode=0o755) for p in LOADERS}}
    # A disk where the installer didn't write the fallback path.
    NO_FALLBACK = {p: m for p, m in SRC.items() if not p.startswith("boot/efi/EFI/BOOT")}

    judge = Fs.judge

    def changed(self, src, changes):
        a = copy.deepcopy(src)
        for p, kw in changes.items():
            a[p].update(kw)
        return self.judge(src, a)

    def test_the_boot_stage_and_the_kept_loaders(self):
        rc, out = self.changed(self.SRC, {
            f"boot/initrd.img-{self.K}": {"sha256": "cd", "mtime": 2}, "boot": {"mtime": 2},
            "boot/grub/grub.cfg": {"sha256": "cd", "mtime": 2}, "boot/grub/i386-pc/core.img": {"sha256": "cd"},
            "boot/grub/i386-pc": {"mtime": 2}})
        self.assertEqual(rc, 0, out)
        self.assertNotIn("DIFF", out)
        self.assertIn(f"2: boot/initrd.img-{self.K}, boot/initrd.img-{self.K}", out)
        self.assertIn("AnduinOS's MOK-signed loaders kept: 6 paths held to the source's bytes: AnduinOS/grubx64.efi, AnduinOS/mmx64.efi, AnduinOS/shimx64.efi, BOOT/BOOTX64.EFI, BOOT/grubx64.efi, BOOT/mmx64.efi", out)

    def test_the_mok_signed_loaders_must_be_the_sources(self):
        """grub-install --target=x86_64-efi (an unsigned GRUB), or shim from
        another package, on any of the six: no ESP rule excuses it here."""
        for p in self.LOADERS:
            rc, out = self.changed(self.SRC, {p: {"sha256": "cd", "mtime": 2}})
            self.assertEqual(rc, 1, f"{p}: {out}")
            self.assertIn(f"DIFF content: 1: {p}", out)
            self.assertIn(f"DIFF mtime: 1: {p}", out)
            self.assertIn(" 0 from the boot stage", out)

    def test_a_missing_or_extra_loader_counts(self):
        a = copy.deepcopy(self.SRC)
        del a["boot/efi/EFI/BOOT/mmx64.efi"]
        rc, out = self.judge(self.SRC, a)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF missing: 1: boot/efi/EFI/BOOT/mmx64.efi", out)
        a = copy.deepcopy(self.NO_FALLBACK)
        a["boot/efi/EFI/BOOT/BOOTX64.EFI"] = meta("file")
        rc, out = self.judge(self.NO_FALLBACK, a)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF extra: 1: boot/efi/EFI/BOOT/BOOTX64.EFI", out)

    def test_the_esp_dirs_and_stub_keep_their_rule(self):
        """EFI/AnduinOS's grub.cfg stub, BOOTX64.CSV and the directories
        keep the ESP rule (load writes none of them, but nothing of the MOK
        is in them)."""
        rc, out = self.changed(self.SRC, {"boot/efi/EFI/AnduinOS/grub.cfg": {"sha256": "cd"}, "boot/efi/EFI/AnduinOS": {"mtime": 2},
                                          "boot/efi/EFI/BOOT/grub.cfg": {"mtime": 2}})
        self.assertEqual(rc, 0, out)
        self.assertIn("grub-install writes the ESPs' loaders", out)

    def test_the_fallback_copies_are_held_where_the_source_has_them(self):
        rc, out = self.changed(self.NO_FALLBACK, {f"boot/initrd.img-{self.K}": {"sha256": "cd"}})
        self.assertEqual(rc, 0, out)
        self.assertIn("AnduinOS's MOK-signed loaders kept: 3 paths", out)

    def test_efi_anduinos_loaders_must_be_in_the_sources_record(self):
        """shim there but not GRUB or MokManager: the check would have
        nothing to hold them to."""
        src = {p: m for p, m in self.SRC.items() if p not in ("boot/efi/EFI/AnduinOS/grubx64.efi", "boot/efi/EFI/AnduinOS/mmx64.efi")}
        rc, out = self.judge(src, copy.deepcopy(src))
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF not in the source's record (AnduinOS's MOK-signed loaders): 2: boot/efi/EFI/AnduinOS/grubx64.efi, boot/efi/EFI/AnduinOS/mmx64.efi", out)

    def test_dracuts_initrd_mode_and_the_kernels_still_count(self):
        """The rebuilt initrd stays 0600 as on the source: a mode change of
        it, the kernel or System.map is the restore's."""
        rc, out = self.changed(self.SRC, {f"boot/initrd.img-{self.K}": {"mode": 0o644}, f"boot/vmlinuz-{self.K}": {"mode": 0o644},
                                          f"boot/System.map-{self.K}": {"sha256": "cd"}})
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF mode: 2: boot/initrd.img-{self.K}, boot/vmlinuz-{self.K}", out)
        self.assertIn(f"DIFF content: 1: boot/System.map-{self.K}", out)

    def test_dracuts_console_setup_temp_file(self):
        """a5-1: the source's dracut left console-setup's tmpkbd.XXXXXX;
        the boot stage's dracut remade the directory without it.  Other
        files there still have to come back."""
        d = "var/lib/dracut/console-setup-dir/etc/console-setup"
        src = dict(self.SRC, **{d: meta("dir"), f"{d}/tmpkbd.Ggc8Lu": meta("file"), f"{d}/cached_UTF-8_del.kmap": meta("file")})
        a = copy.deepcopy(src)
        del a[f"{d}/tmpkbd.Ggc8Lu"]
        rc, out = self.judge(src, a)
        self.assertEqual(rc, 0, out)
        self.assertIn(f"leaves its temporary tmpkbd.XXXXXX behind, and its run in the boot stage remakes the directory): 1: {d}/tmpkbd.Ggc8Lu", out)
        del a[f"{d}/cached_UTF-8_del.kmap"]
        a[f"{d}/tmpkbd.Q1w2E3"] = meta("file")
        rc, out = self.judge(src, a)
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF missing: 1: {d}/cached_UTF-8_del.kmap", out)
        self.assertNotIn("DIFF extra", out)

    def test_other_systems_esp_rules_are_unchanged(self):
        """No AnduinOS shim on the source (Ubuntu's EFI/ubuntu, or a lower-case
        anduinos directory): grub-install may rewrite EFI/BOOT as before."""
        for d in ("ubuntu", "anduinos"):
            src = {"boot/efi/EFI": meta("dir"), "boot/efi/EFI/BOOT": meta("dir"), "boot/efi/EFI/BOOT/BOOTX64.EFI": meta("file"),
                   f"boot/efi/EFI/{d}": meta("dir"), f"boot/efi/EFI/{d}/shimx64.efi": meta("file"), f"boot/efi/EFI/{d}/grubx64.efi": meta("file")}
            a = copy.deepcopy(src)
            for p in a:
                a[p].update(sha256="cd", mtime=2)
            rc, out = self.judge(src, a)
            self.assertEqual(rc, 0, f"{d}: {out}")
            self.assertNotIn("AnduinOS", out)


class FsOpensuseShim(unittest.TestCase):
    """judge.py fs over what load's boot stage does on openSUSE Leap 16 (L5,
    docs/design/leap16.md): dracut rebuilds /boot/initrd-K, grub2-mkconfig
    remakes /boot/grub2/grub.cfg, setfiles relabels /boot, and the ESP is
    kept: every file of EFI/opensuse and EFI/boot must be the source's but
    EFI/opensuse/grub.cfg, the stub that finds /boot/grub2, which may change
    as /boot/grub2/grub.cfg does."""

    STUB = "boot/efi/EFI/opensuse/grub.cfg"

    K = "6.12.0-160100.9-default"
    LOADERS = ["boot/efi/EFI/opensuse/shim.efi", "boot/efi/EFI/opensuse/grub.efi", "boot/efi/EFI/opensuse/grubx64.efi",
               "boot/efi/EFI/opensuse/MokManager.efi", "boot/efi/EFI/opensuse/grub.cfg", "boot/efi/EFI/opensuse/boot.csv",
               "boot/efi/EFI/boot/bootx64.efi", "boot/efi/EFI/boot/fallback.efi", "boot/efi/EFI/boot/MokManager.efi"]
    SRC = {"boot": meta("dir"), f"boot/initrd-{K}": meta("file", mode=0o600), f"boot/vmlinuz-{K}": meta("file"),
           f"boot/System.map-{K}": meta("file"), "boot/grub2": meta("dir"), "boot/grub2/grub.cfg": meta("file", mode=0o600),
           "boot/grub2/grubenv": meta("file"), "boot/grub2/x86_64-efi": meta("dir"), "boot/grub2/x86_64-efi/normal.mod": meta("file"),
           "boot/efi": meta("dir"), "boot/efi/EFI": meta("dir"), "boot/efi/EFI/opensuse": meta("dir"), "boot/efi/EFI/boot": meta("dir"),
           "var/lib/dracut": meta("dir"),
           **{p: meta("file", xattrs={}) for p in LOADERS}}

    judge = Fs.judge

    def changed(self, src, changes):
        a = copy.deepcopy(src)
        for p, kw in changes.items():
            a[p].update(kw)
        return self.judge(src, a)

    def test_the_boot_stage_and_the_kept_esp(self):
        rc, out = self.changed(self.SRC, {
            f"boot/initrd-{self.K}": {"sha256": "cd", "mtime": 2}, "boot": {"mtime": 2},
            "boot/grub2/grub.cfg": {"sha256": "cd", "mtime": 2}, "boot/grub2": {"mtime": 2},
            "boot/grub2/grubenv": {"xattrs": {"security.selinux": "00"}}, "var/lib/dracut": {"mtime": 2},
            self.STUB: {"sha256": "cd", "mtime": 2}})
        self.assertEqual(rc, 0, out)
        self.assertNotIn("DIFF", out)
        self.assertIn(f"boot stage (openSUSE's grub.cfg stub on the ESP, which only finds /boot/grub2's grub.cfg, may change with it "
                      f"(grub2-mkconfig)): 2: {self.STUB}, {self.STUB}", out)     # (content, mtime)
        self.assertIn("openSUSE's shim and signed GRUB kept: 8 paths of EFI/opensuse and EFI/boot held to the source's bytes: "
                      "boot/MokManager.efi, boot/bootx64.efi, boot/fallback.efi, opensuse/MokManager.efi", out)

    def test_every_esp_file_must_be_the_sources(self):
        """grub2-install or shim-install (an unsigned or another GRUB, a
        new MokManager or fallback) on any of them: no ESP rule excuses it
        here."""
        for p in self.LOADERS:
            if p == self.STUB:
                continue
            rc, out = self.changed(self.SRC, {p: {"sha256": "cd", "mtime": 2}})
            self.assertEqual(rc, 1, f"{p}: {out}")
            self.assertIn(f"DIFF content: 1: {p}", out)
            self.assertIn(" 0 from the boot stage", out)

    def test_the_stub_may_change_but_not_go(self):
        """The stub's content and times may change; its mode still counts,
        and a stub gone is a difference."""
        rc, out = self.changed(self.SRC, {self.STUB: {"sha256": "cd", "mtime": 2, "mode": 0o600}})
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF mode: 1: {self.STUB}", out)
        a = copy.deepcopy(self.SRC)
        del a[self.STUB]
        rc, out = self.judge(self.SRC, a)
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF missing: 1: {self.STUB}", out)

    def test_a_missing_or_extra_esp_file_counts(self):
        a = copy.deepcopy(self.SRC)
        del a["boot/efi/EFI/boot/fallback.efi"]
        a["boot/efi/EFI/opensuse/shimx64.efi"] = meta("file", xattrs={})
        rc, out = self.judge(self.SRC, a)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF missing: 1: boot/efi/EFI/boot/fallback.efi", out)
        self.assertIn("DIFF extra: 1: boot/efi/EFI/opensuse/shimx64.efi", out)

    def test_the_signed_loaders_must_be_in_the_sources_record(self):
        src = {p: m for p, m in self.SRC.items() if p not in ("boot/efi/EFI/opensuse/MokManager.efi", "boot/efi/EFI/boot/bootx64.efi")}
        rc, out = self.judge(src, copy.deepcopy(src))
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF not in the source's record (openSUSE's shim and signed GRUB): 2: "
                      "boot/efi/EFI/opensuse/MokManager.efi, boot/efi/EFI/boot/bootx64.efi", out)

    def test_the_esp_dirs_keep_their_rule(self):
        rc, out = self.changed(self.SRC, {"boot/efi/EFI/opensuse": {"mtime": 2}, "boot/efi/EFI": {"mtime": 2}})
        self.assertEqual(rc, 0, out)
        self.assertIn("grub-install writes the ESPs' loaders", out)

    def test_the_kernel_still_counts(self):
        rc, out = self.changed(self.SRC, {f"boot/vmlinuz-{self.K}": {"sha256": "cd"}, f"boot/initrd-{self.K}": {"mode": 0o644}})
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF content: 1: boot/vmlinuz-{self.K}", out)
        self.assertIn(f"DIFF mode: 1: boot/initrd-{self.K}", out)

    def test_other_systems_esp_rules_are_unchanged(self):
        """No openSUSE shim at EFI/opensuse (Fedora's EFI/fedora, or MicroOS's
        transactional source): the ESP rule or TU_STAGE as before."""
        src = {"boot/efi/EFI": meta("dir"), "boot/efi/EFI/BOOT": meta("dir"), "boot/efi/EFI/BOOT/BOOTX64.EFI": meta("file"),
               "boot/efi/EFI/fedora": meta("dir"), "boot/efi/EFI/fedora/shimx64.efi": meta("file"),
               "boot/efi/EFI/fedora/grubx64.efi": meta("file")}
        a = copy.deepcopy(src)
        for p in a:
            a[p].update(sha256="cd", mtime=2)
        rc, out = self.judge(src, a)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("openSUSE", out)
        tu = dict(self.SRC, **{"usr": meta("dir"), "usr/sbin": meta("dir"), "usr/sbin/tukit": meta("file")})
        rc, out = self.judge(tu, copy.deepcopy(tu))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("openSUSE", out)


SOLUS_V =("current.7.3.4-373", "lts.6.18.63-322")
SOLUS_CBM = "boot/EFI/com.solus-project"


class FsSolus(unittest.TestCase):
    """judge.py fs over a Solus 4.9 restore (S5, docs/design/solus.md): the
    ESP is /boot itself (out of fstab), and load's boot stage makes only the
    NVRAM entry, so every file of it must be the source's bytes: shim
    (bootloaderx64.efi and EFI/BOOT/BOOTX64.EFI), systemd-boot (grubx64.efi),
    MokManager, the kernels and initrds, loader/ and solus-enroll-me.cer.
    Neither Chimera's boot/EFI rules nor Fedora's boot/loader one excuse
    them."""

    V, CBM = SOLUS_V, SOLUS_CBM
    SRC = {"boot": meta("dir"), "boot/EFI": meta("dir"), SOLUS_CBM: meta("dir"),
           **{f"{SOLUS_CBM}/{f}": meta("file") for f in ("bootloaderx64.efi", "grubx64.efi", "mmx64.efi", "BOOTX64.CSV")},
           **{f"{SOLUS_CBM}/{k}-com.solus-project.{v}": meta("file") for k in ("kernel", "initrd") for v in SOLUS_V},
           "boot/EFI/BOOT": meta("dir"), "boot/EFI/BOOT/BOOTX64.EFI": meta("file"), "boot/EFI/BOOT/fbx64.efi": meta("file"),
           "boot/loader": meta("dir"), "boot/loader/loader.conf": meta("file"), "boot/loader/entries": meta("dir"),
           **{f"boot/loader/entries/Solus-{v.replace('.', '-', 1)}.conf": meta("file") for v in SOLUS_V},
           "boot/solus-enroll-me.cer": meta("file"),
           "usr/lib64/kernel": meta("dir"), **{f"usr/lib64/kernel/com.solus-project.{v}": meta("file") for v in SOLUS_V},
           "var/log": meta("dir")}
    ESP = sorted(p for p in SRC if p.startswith("boot/"))

    judge = Fs.judge

    def changed(self, src, changes):
        a = copy.deepcopy(src)
        for p, kw in changes.items():
            a[p].update(kw)
        return self.judge(src, a)

    def test_an_untouched_esp_passes_and_is_held(self):
        a = copy.deepcopy(self.SRC)
        a["var/log/bko-holt"] = meta("dir")
        a["var/log/bko-holt/load.log"] = meta("file")
        a["var/log"]["mtime"] = 2
        a["boot"]["mtime"] = 2
        rc, out = self.judge(self.SRC, a)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("DIFF", out)
        self.assertIn(f"Solus's ESP at /boot kept: {len(self.ESP)} paths of EFI/com.solus-project, EFI/BOOT, loader/ and solus-enroll-me.cer held to the source's bytes", out)

    def test_every_esp_file_must_be_the_sources(self):
        """bootctl install, clr-boot-manager update, a rebuilt initrd or an
        unsigned loader on any of them: no boot-stage rule excuses it."""
        for p in self.ESP:
            if self.SRC[p]["type"] != "file":
                continue
            rc, out = self.changed(self.SRC, {p: {"sha256": "cd", "mtime": 2}})
            self.assertEqual(rc, 1, f"{p}: {out}")
            self.assertIn(f"DIFF content: 1: {p}", out)
            self.assertIn(f"DIFF mtime: 1: {p}", out)
            self.assertIn(" 0 from the boot stage", out)

    def test_the_esp_at_boot_rules_of_others_dont_excuse_it(self):
        """What Chimera's bootctl rules (boot/EFI/BOOT, boot/EFI/systemd,
        boot/loader/random-seed) and Fedora's BLS rule (boot/loader/entries)
        would pass is a DIFF on a Solus ESP."""
        src = dict(self.SRC, **{"boot/loader/random-seed": meta("file")})
        a = copy.deepcopy(src)
        a["boot/EFI/BOOT"]["mtime"] = 2
        a["boot/loader/entries"]["mtime"] = 2
        a["boot/loader/random-seed"].update(sha256="cd")
        a["boot/EFI/systemd"] = meta("dir")
        a["boot/EFI/systemd/systemd-bootx64.efi"] = meta("file")
        rc, out = self.judge(src, a)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF mtime (dirs): 2: boot/EFI/BOOT, boot/loader/entries", out)
        self.assertIn("DIFF content: 1: boot/loader/random-seed", out)
        self.assertIn("DIFF extra: 2: boot/EFI/systemd, boot/EFI/systemd/systemd-bootx64.efi", out)
        self.assertIn(" 0 from the boot stage", out)

    def test_the_aerynos_and_grub_esp_rules_dont_match_boot_EFI(self):
        """The ESP rules written for an ESP at /efi or /boot/efi (AerynOS's
        bootctl rule, grub-install's) are lower-case efi/: none of them
        reaches a path of an ESP at /boot."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("judge_under_test", JUDGE)
        judge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(judge)
        aeryn = [r for r, _, w in judge.BOOT_STAGE if "(AerynOS)" in w]
        self.assertEqual(aeryn, [r"^(boot/)?efi/EFI(/(BOOT|Boot|systemd|Linux)(/.*)?)?$"])
        lower = [r for r, _, _ in judge.BOOT_STAGE if re.match(r"\^(\(boot/\)\?|boot/)efi", r)]
        self.assertTrue(len(lower) >= 4, lower)
        for p in self.ESP:
            for r in lower:
                self.assertIsNone(re.search(r, p), f"{r} matches {p}")
        self.assertTrue(re.search(aeryn[0], "efi/EFI/BOOT/BOOTX64.EFI"))
        for p in self.ESP:
            self.assertTrue(p == "boot" or re.search(judge.SOLUS_KEPT, p), p)

    def test_a_missing_esp_file_counts(self):
        a = copy.deepcopy(self.SRC)
        del a["boot/EFI/BOOT/fbx64.efi"], a[f"{self.CBM}/initrd-com.solus-project.{self.V[1]}"]
        rc, out = self.judge(self.SRC, a)
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF missing: 2: boot/EFI/BOOT/fbx64.efi, {self.CBM}/initrd-com.solus-project.{self.V[1]}", out)

    def test_the_source_must_have_its_loaders_and_an_entry(self):
        src = {p: m for p, m in self.SRC.items() if p != f"{self.CBM}/grubx64.efi" and not p.startswith("boot/loader/entries/")}
        rc, out = self.judge(src, copy.deepcopy(src))
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF not in the source's record (Solus's ESP at /boot): 2: {self.CBM}/grubx64.efi, boot/loader/entries/Solus-*.conf", out)

    def test_a_source_whose_esp_wasnt_mounted(self):
        """vm-mount put no ESP at /boot (or the backup's tree lacks it): a
        Solus kernel in /usr/lib64/kernel still marks the source, and every
        ESP file it should have is missing from its record."""
        src = {p: m for p, m in self.SRC.items() if not p.startswith("boot/")}
        rc, out = self.judge(src, copy.deepcopy(src))
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF not in the source's record (Solus's ESP at /boot): 9: boot/EFI/com.solus-project/bootloaderx64.efi", out)

    def test_the_kernels_in_usr_still_count(self):
        rc, out = self.changed(self.SRC, {f"usr/lib64/kernel/com.solus-project.{self.V[0]}": {"sha256": "cd"}})
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF content: 1: usr/lib64/kernel/com.solus-project.{self.V[0]}", out)

    def test_other_esps_at_boot_keep_their_rules(self):
        """No Solus shim nor kernel (Chimera's ESP at /boot): bootctl install
        may still rewrite EFI/BOOT and the random seed."""
        src = {"boot": meta("dir"), "boot/EFI": meta("dir"), "boot/EFI/BOOT": meta("dir"),
               "boot/EFI/BOOT/BOOTX64.EFI": meta("file"), "boot/loader": meta("dir"), "boot/loader/random-seed": meta("file")}
        rc, out = self.changed(src, {"boot/EFI/BOOT/BOOTX64.EFI": {"sha256": "cd", "mtime": 2}, "boot/loader/random-seed": {"sha256": "cd"}})
        self.assertEqual(rc, 0, out)
        self.assertNotIn("Solus", out)


SLACK_K = "5.15.193"
SLACK_ESP = "boot/efi/EFI"


class FsSlackware(unittest.TestCase):
    """judge.py fs over the Slackware 15.0 restores (S6, docs/design/slackware.md).
    slackware15: load runs lilo -C on lilo.conf.bko-holt (root= by name) and
    removes it, so /etc/lilo.conf stays the source's, the copy is gone, and
    only lilo's map and an MBR backup under the rescue system's device number
    may differ.  slackware15-uefi: ELILO is kept (EFI/Slackware and EFI/BOOT
    held to the source's bytes), mkinitrd remakes /boot/initrd.gz and its
    tree, and each ESP initrd.gz that was its copy must be the new one."""

    judge = Fs.judge
    LILO = {"etc": meta("dir"), "etc/slackware-version": meta("file"), "etc/lilo.conf": meta("file"),
            "boot": meta("dir"), "boot/map": meta("file"), "boot/boot.fe00": meta("file"),
            f"boot/vmlinuz-huge-{SLACK_K}": meta("file"), "boot/vmlinuz": meta("symlink", target=f"vmlinuz-huge-{SLACK_K}"),
            "var/log": meta("dir")}
    ESP = SLACK_ESP
    UEFI = {"etc": meta("dir"), "etc/slackware-version": meta("file"),
            "boot": meta("dir"), f"boot/vmlinuz-generic-{SLACK_K}": meta("file", sha256="k1", size=9),
            "boot/initrd.gz": meta("file", sha256="i1", size=7), "boot/initrd-tree": meta("dir"),
            "boot/initrd-tree/init": meta("file"), "boot/initrd-tree/rootdev": meta("file"),
            "boot/efi": meta("dir"), SLACK_ESP: meta("dir"),
            **{f"{SLACK_ESP}/{d}": meta("dir") for d in ("Slackware", "BOOT")},
            **{f"{SLACK_ESP}/{d}/{f}": meta("file") for d, f in (("Slackware", "elilo.efi"), ("BOOT", "BOOTX64.EFI"))},
            **{f"{SLACK_ESP}/{d}/elilo.conf": meta("file", sha256="c1") for d in ("Slackware", "BOOT")},
            **{f"{SLACK_ESP}/{d}/vmlinuz": meta("file", sha256="k1", size=9) for d in ("Slackware", "BOOT")},
            **{f"{SLACK_ESP}/{d}/initrd.gz": meta("file", sha256="i1", size=7) for d in ("Slackware", "BOOT")},
            "var/log": meta("dir")}
    INITRDS = [f"{SLACK_ESP}/BOOT/initrd.gz", f"{SLACK_ESP}/Slackware/initrd.gz"]

    def remade(self, src=None):
        """What load leaves on slackware15-uefi: /boot/initrd.gz and its tree
        remade, and copied over both ESP initrd.gz (cp, touch -r)."""
        a = copy.deepcopy(src or self.UEFI)
        new = {"sha256": "i2", "size": 8, "mtime": 5}
        a["boot/initrd.gz"].update(new)
        a["boot/initrd-tree/rootdev"].update(sha256="cd", mtime=5)
        del a["boot/initrd-tree/init"]
        a["boot/initrd-tree"]["mtime"] = 5
        a["boot"]["mtime"] = 5
        for p in self.INITRDS:
            a[p].update(new)
        a["var/log/bko-holt"] = meta("dir")
        a["var/log/bko-holt/load.log"] = meta("file")
        a["var/log"]["mtime"] = 5
        return a

    def lilo_ran(self):
        a = copy.deepcopy(self.LILO)
        a["boot/map"].update(sha256="cd", mtime=5)
        a["boot/boot.fd00"] = meta("file")
        a["boot"]["mtime"] = 5
        a["etc"]["mtime"] = 5
        return a

    def test_lilo_run_passes(self):
        rc, out = self.judge(self.LILO, self.lilo_ran())
        self.assertEqual(rc, 0, out)
        self.assertIn("boot stage (lilo writes its map: the restored kernels' sectors): 2: boot/map, boot/map", out)   # (content, mtime)
        self.assertIn("boot stage (lilo backs up the MBR it replaces as /boot/boot.MMmm, named by the disk's device number in the rescue system): 1: boot/boot.fd00", out)
        self.assertIn("boot stage (load writes etc/lilo.conf.bko-holt for lilo -C, then removes it): 1: etc", out)
        self.assertIn("LILO: etc/lilo.conf held to the source's bytes, no etc/lilo.conf.bko-holt left behind", out)

    def test_lilo_copy_left_behind(self):
        a = self.lilo_ran()
        a["etc/lilo.conf.bko-holt"] = meta("file")
        rc, out = self.judge(self.LILO, a)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF left behind by load (lilo's copy of lilo.conf): 1: etc/lilo.conf.bko-holt", out)
        self.assertNotIn("DIFF extra", out)

    def test_lilo_conf_and_kernel_held(self):
        a = self.lilo_ran()
        a["etc/lilo.conf"].update(sha256="cd")
        a[f"boot/vmlinuz-huge-{SLACK_K}"].update(sha256="cd")
        a["boot/boot.fe00"].update(sha256="cd")
        rc, out = self.judge(self.LILO, a)
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF content: 3: boot/boot.fe00, boot/vmlinuz-huge-{SLACK_K}, etc/lilo.conf", out)

    def test_etc_mtime_without_lilo_counts(self):
        src = {p: m for p, m in self.LILO.items() if p != "boot/map"}
        a = copy.deepcopy(src)
        a["etc"]["mtime"] = 5
        rc, out = self.judge(src, a)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF mtime (dirs): 1: etc", out)
        self.assertNotIn("LILO:", out)

    def test_elilo_remade_initrd_passes(self):
        rc, out = self.judge(self.UEFI, self.remade())
        self.assertEqual(rc, 0, out)
        self.assertNotIn("DIFF", out)
        self.assertIn("boot stage (load runs the source's mkinitrd command again: /boot/initrd.gz): 2: boot/initrd.gz, boot/initrd.gz", out)
        self.assertIn("boot stage (mkinitrd remakes its tree, /boot/initrd-tree (its -c clears it first)): 4: boot/initrd-tree/init, boot/initrd-tree, boot/initrd-tree/rootdev, boot/initrd-tree/rootdev", out)
        self.assertIn(f"boot stage (load copies the remade /boot/initrd.gz over the ESP's (as eliloconfig does)): 4: {self.INITRDS[0]}", out)
        self.assertIn("ELILO kept: 9 paths of EFI/Slackware and EFI/BOOT held to the source's bytes; 2 ESP initrd.gz", out)

    def test_elilo_files_held(self):
        """grub-install's ESP rule must not excuse ELILO, its elilo.conf or
        the ESP's kernel; nor an EFI/BOOT the boot stage made up."""
        for p in (f"{self.ESP}/Slackware/elilo.efi", f"{self.ESP}/BOOT/BOOTX64.EFI", f"{self.ESP}/BOOT/elilo.conf",
                  f"{self.ESP}/Slackware/elilo.conf", f"{self.ESP}/Slackware/vmlinuz", f"{self.ESP}/BOOT/vmlinuz"):
            a = self.remade()
            a[p].update(sha256="cd", mtime=6)
            rc, out = self.judge(self.UEFI, a)
            self.assertEqual(rc, 1, f"{p}: {out}")
            self.assertIn(f"DIFF content: 1: {p}", out)
            self.assertIn(f"DIFF mtime: 1: {p}", out)
        a = self.remade()
        a[f"{self.ESP}/BOOT/grubx64.efi"] = meta("file")
        rc, out = self.judge(self.UEFI, a)
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF extra: 1: {self.ESP}/BOOT/grubx64.efi", out)

    def test_esp_initrd_must_be_the_remade_one(self):
        a = self.remade()
        a[self.INITRDS[1]].update(sha256="i3")
        rc, out = self.judge(self.UEFI, a)
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF not the restored boot/initrd.gz's bytes (ELILO's initrd on the ESP, a copy of it on the source): 1: {self.INITRDS[1]}", out)

    def test_esp_initrd_left_as_restored_counts(self):
        """Load didn't copy the remade initrd: the ESP's is the source's, no
        longer /boot/initrd.gz's."""
        a = self.remade()
        for p in self.INITRDS:
            a[p] = copy.deepcopy(self.UEFI[p])
        rc, out = self.judge(self.UEFI, a)
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF not the restored boot/initrd.gz's bytes (ELILO's initrd on the ESP, a copy of it on the source): 2: {', '.join(self.INITRDS)}", out)

    def test_esp_initrd_that_was_no_copy_is_held(self):
        src = copy.deepcopy(self.UEFI)
        src[self.INITRDS[0]].update(sha256="old")
        a = self.remade(src)
        a[self.INITRDS[0]] = copy.deepcopy(src[self.INITRDS[0]])
        rc, out = self.judge(src, a)
        self.assertEqual(rc, 0, out)
        self.assertIn("1 ESP initrd.gz (copies of /boot/initrd.gz on the source)", out)
        a[self.INITRDS[0]].update(sha256="i2", size=8, mtime=5)
        rc, out = self.judge(src, a)
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF content: 1: {self.INITRDS[0]}", out)

    def test_esp_initrd_missing_counts(self):
        a = self.remade()
        del a[self.INITRDS[0]]
        rc, out = self.judge(self.UEFI, a)
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF missing: 1: {self.INITRDS[0]}", out)

    def test_the_source_must_have_elilos_files(self):
        src = {p: m for p, m in self.UEFI.items() if p not in (f"{self.ESP}/Slackware/vmlinuz", f"{self.ESP}/BOOT/BOOTX64.EFI")}
        rc, out = self.judge(src, copy.deepcopy(src))
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF not in the source's record (ELILO's files on the ESP): 2: {self.ESP}/Slackware/vmlinuz, {self.ESP}/BOOT/BOOTX64.EFI", out)

    def test_other_esps_keep_grub_installs_rule(self):
        """No ELILO on the ESP (a GRUB machine's EFI/BOOT): grub-install's
        rule still excuses it, and mkinitrd's rules need Slackware."""
        src = {"boot": meta("dir"), "boot/efi": meta("dir"), "boot/efi/EFI": meta("dir"), "boot/efi/EFI/BOOT": meta("dir"),
               "boot/efi/EFI/BOOT/BOOTX64.EFI": meta("file"), "boot/initrd.gz": meta("file")}
        a = copy.deepcopy(src)
        a["boot/efi/EFI/BOOT/BOOTX64.EFI"].update(sha256="cd")
        a["boot/initrd.gz"].update(sha256="cd")
        rc, out = self.judge(src, a)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF content: 1: boot/initrd.gz", out)
        self.assertNotIn("ELILO", out)
        self.assertIn("boot stage (grub-install writes the ESPs' loaders", out)


VANILLA_K = "7.3.3+deb14-amd64"


class FsAbroot(unittest.TestCase):
    """judge.py fs over a Vanilla OS 3 restore (V7): load's boot stage
    regenerates nothing, so the master grub.cfg and grub.cfg.future, both
    roots' abroot.cfg, kernels and initramfs in the init LV, and the ESP's
    EFI/vanilla and EFI/BOOT must be the source's; var/log/bko-holt is
    load's."""

    K = VANILLA_K
    BOOT = ["boot/grub/grub.cfg", "boot/grub/grub.cfg.future",
            *[f"boot/init/{r}/{f}" for r in ("vos-a", "vos-b") for f in ("abroot.cfg", f"vmlinuz-{VANILLA_K}", f"initrd.img-{VANILLA_K}", f"config-{VANILLA_K}", f"System.map-{VANILLA_K}")],
            *[f"boot/efi/EFI/vanilla/{f}" for f in ("shimx64.efi", "grubx64.efi", "mmx64.efi", "fbx64.efi", "BOOTX64.CSV", "grub.cfg")],
            *[f"boot/efi/EFI/BOOT/{f}" for f in ("BOOTX64.EFI", "grubx64.efi", "mmx64.efi", "grub.cfg")]]
    SRC = {"boot": meta("dir"), "boot/grub": meta("dir"), "boot/grub/grubenv": meta("file"), "boot/init": meta("dir"),
           "boot/init/vos-a": meta("dir"), "boot/init/vos-b": meta("dir"), "boot/efi": meta("dir"), "boot/efi/EFI": meta("dir"),
           "boot/efi/EFI/vanilla": meta("dir"), "boot/efi/EFI/BOOT": meta("dir"), "var": meta("dir"), "var/log": meta("dir"),
           **{p: meta("file") for p in BOOT}}

    judge = Fs.judge

    def changed(self, src, changes):
        a = copy.deepcopy(src)
        for p, kw in changes.items():
            a[p].update(kw)
        return self.judge(src, a)

    def test_the_boot_stages_log_only(self):
        a = copy.deepcopy(self.SRC)
        a["var/log/bko-holt"] = meta("dir")
        a["var/log/bko-holt/load.log"] = meta("file")
        a["var/log"]["mtime"] = 2
        rc, out = self.judge(self.SRC, a)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("DIFF", out)
        self.assertIn("load's own log, copied in by the boot stage", out)
        self.assertIn(f"ABRoot kept: {len(self.BOOT) + 2} paths", out)

    def test_every_boot_file_must_be_the_sources(self):
        """A grub-install, update-grub, update-initramfs or a rewritten
        abroot.cfg on any of them: no ESP or /boot rule excuses it."""
        for p in self.BOOT:
            rc, out = self.changed(self.SRC, {p: {"sha256": "cd", "mtime": 2}})
            self.assertEqual(rc, 1, f"{p}: {out}")
            self.assertIn(f"DIFF content: 1: {p}", out)
            self.assertIn(" 0 from the boot stage", out)
        rc, out = self.changed(self.SRC, {"boot/efi/EFI/vanilla": {"mtime": 2}})
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF mtime (dirs): 1: boot/efi/EFI/vanilla", out)

    def test_a_missing_or_extra_file_counts(self):
        a = copy.deepcopy(self.SRC)
        del a["boot/grub/grub.cfg.future"]
        a["boot/efi/EFI/BOOT/fbx64.efi"] = meta("file")
        a[f"boot/init/vos-a/initrd.img-{self.K}.bak"] = meta("file")
        rc, out = self.judge(self.SRC, a)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF missing: 1: boot/grub/grub.cfg.future", out)
        self.assertIn("DIFF extra: 2:", out)

    def test_the_boot_files_must_be_in_the_sources_record(self):
        src = {p: m for p, m in self.SRC.items() if p not in ("boot/grub/grub.cfg", f"boot/init/vos-b/vmlinuz-{self.K}")}
        rc, out = self.judge(src, copy.deepcopy(src))
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF not in the source's record (ABRoot's boot files): 2: boot/grub/grub.cfg, boot/init/vos-b/vmlinuz-*", out)

    def test_grubenv_and_other_systems_keep_their_rules(self):
        """grubenv isn't held (GRUB writes it at boot, as on any Debian): it
        keeps /boot/grub's rule; without an abroot.cfg in boot/init/ the ESP
        is grub-install's to rewrite, as before."""
        rc, out = self.changed(self.SRC, {"boot/grub/grubenv": {"sha256": "cd"}})
        self.assertEqual(rc, 0, out)
        self.assertIn("rewrite /boot/grub", out)
        src = {p: m for p, m in self.SRC.items() if not p.startswith("boot/init/")}
        rc, out = self.changed(src, {"boot/efi/EFI/vanilla/grubx64.efi": {"sha256": "cd"}})
        self.assertEqual(rc, 0, out)
        self.assertNotIn("ABRoot", out)
        self.assertIn("grub-install writes the ESPs' loaders", out)


def btrfs_root_guest():
    """/boot, swap, and a btrfs root with @ on / and @home on /home (Fedora's
    layout), booted with rootflags=subvol=@: the source of a --reformat."""
    root = 27260928
    fs = btrfs(root * 512, "dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    fs["label"] = "fedora"
    d = dump([disk("vda", 16 * GIB, [
        part("vda1", 1, 2048, 2097152, ext4(1 * GIB, "77777777-7777-4777-8777-777777777777"), mounted_at=["/boot"]),
        part("vda2", 2, 2099200, 4194304, swap(2 * GIB)),
        part("vda3", 3, 6293504, root, fs, mounted_at=["/", "/home"]),
    ])])
    u = "UUID=dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    d["storage"]["fstab"] = [
        {"spec": u, "file": "/", "type": "btrfs", "options": "subvol=@,compress=zstd:1,noatime", "freq": 0, "passno": 0, "resolves_to": "vda3"},
        {"spec": "UUID=77777777-7777-4777-8777-777777777777", "file": "/boot", "type": "ext4", "options": "defaults", "freq": 1, "passno": 2, "resolves_to": "vda1"},
        {"spec": u, "file": "/home", "type": "btrfs", "options": "subvol=@home,compress=zstd:1", "freq": 0, "passno": 0, "resolves_to": "vda3"},
        {"spec": "UUID=55555555-5555-4555-8555-555555555555", "file": "none", "type": "swap", "options": "defaults", "freq": 0, "passno": 0, "resolves_to": "vda2"},
    ]
    d["storage"]["mounts"] = [
        {"target": "/", "source": "/dev/vda3", "fstype": "btrfs", "device": "vda3", "root": "/@", "options": "rw,noatime",
         "super_options": "rw,compress=zstd:1,space_cache=v2,subvolid=256,subvol=/@"},
        {"target": "/boot", "source": "/dev/vda1", "fstype": "ext4", "device": "vda1", "root": "/", "options": "rw,relatime", "super_options": "rw"},
        {"target": "/home", "source": "/dev/vda3", "fstype": "btrfs", "device": "vda3", "root": "/@home", "options": "rw,relatime",
         "super_options": "rw,compress=zstd:1,space_cache=v2,subvolid=257,subvol=/@home"},
    ]
    d["storage"]["backup_sources"] = [
        {"path": "/", "from": "/", "device": "vda3", "fstype": "btrfs", "source": "/dev/vda3", "subvol": "/@"},
        {"path": "/boot", "from": "/boot", "device": "vda1", "fstype": "ext4", "source": "/dev/vda1", "subvol": None},
        {"path": "/home", "from": "/home", "device": "vda3", "fstype": "btrfs", "source": "/dev/vda3", "subvol": "/@home"},
    ]
    d["storage"]["btrfs"] = [{"uuid": "dddddddd-dddd-4ddd-8ddd-dddddddddddd", "label": "fedora", "devices": ["vda3"],
                              "mounted_subvolumes": [{"target": "/", "subvol": "/@"}, {"target": "/home", "subvol": "/@home"}],
                              "subvolumes": [{"id": 256, "path": "<FS_TREE>/@", "parent_uuid": None, "readonly": False},
                                             {"id": 257, "path": "<FS_TREE>/@home", "parent_uuid": None, "readonly": False}]}]
    line = f"root={u} ro rootflags=subvol=@ console=ttyS0"
    d["boot"] = {"cmdline": {"raw": f"BOOT_IMAGE=/vmlinuz-7 {line}", "words": ["BOOT_IMAGE=/vmlinuz-7", *line.split()[:-1], "console=ttyS0\n"],
                             "storage": {"BOOT_IMAGE": "/vmlinuz-7", "root": u, "rootflags": "subvol=@"}},
                 "kernel_cmdline_file": line,
                 "grub": {"grub_cfg": {"entries": ["Fedora Linux"], "default_linux": f"/vmlinuz-7 {line}", "default_initrd": "/initramfs-7.img"}},
                 "systemd_boot": {"entries": [{"file": "x-7.conf", "options": line}]}}
    return d


def reformatted(src, to="ext4"):
    """SRC's btrfs root as load --reformat /=TO leaves it, booted."""
    d = copy.deepcopy(src)
    st = d["storage"]
    p = st["disks"][0]["partitions"][2]
    size = p["size_bytes"]
    p["content"] = dict((ext4 if to == "ext4" else xfs)(size, "dddddddd-dddd-4ddd-8ddd-dddddddddddd"), label="fedora")
    p["mounted_at"] = ["/"]
    st["fstab"] = [e for e in st["fstab"] if e["file"] != "/home"]
    st["fstab"][0].update(type=to, options="noatime", passno=1 if to == "ext4" else 0)
    st["mounts"] = [m for m in st["mounts"] if m["target"] != "/home"]
    st["mounts"][0].update(fstype=to, root="/", super_options="rw")
    st["backup_sources"] = [b for b in st["backup_sources"] if b["path"] != "/home"]
    st["backup_sources"][0].update(fstype=to, subvol=None)
    st["btrfs"] = []
    u = "UUID=dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    line = f"root={u} ro console=ttyS0"
    b = d["boot"]
    b["cmdline"] = {"raw": f"BOOT_IMAGE=/vmlinuz-7 {line}", "words": ["BOOT_IMAGE=/vmlinuz-7", *line.split()[:-1], "console=ttyS0\n"],
                    "storage": {"BOOT_IMAGE": "/vmlinuz-7", "root": u}}
    b["kernel_cmdline_file"] = line
    b["grub"]["grub_cfg"]["default_linux"] = f"/vmlinuz-7 {line}"
    b["systemd_boot"]["entries"][0]["options"] = line
    return d


class Reformat(unittest.TestCase):
    """judge.py --reformat MOUNT=TYPE over a btrfs root made ext4 or XFS
    (docs/design/reformat.md), and its twins: what load wouldn't have done."""

    judge = Fit.judge

    def good(self, src, dst, *args):
        rc, out = self.judge(src, dst, *args)
        self.assertEqual(rc, 0, out)
        return out

    def bad(self, src, dst, *args):
        rc, out = self.judge(src, dst, *args)
        self.assertEqual(rc, 1, out)
        return out

    def test_onto_ext4(self):
        src = btrfs_root_guest()
        out = self.good(src, reformatted(src), "--reformat", "/=ext4")
        self.assertIn("folded into /: /home", out)

    def test_onto_xfs(self):
        src = btrfs_root_guest()
        self.good(src, reformatted(src, "xfs"), "--reformat", "/=xfs")

    def test_without_the_flag_it_differs(self):
        src = btrfs_root_guest()
        self.bad(src, reformatted(src))

    def test_wrong_type(self):
        src = btrfs_root_guest()
        out = self.bad(src, reformatted(src, "xfs"), "--reformat", "/=ext4")
        self.assertIn("DIFF reformat: --reformat /=ext4: vda3's filesystem dddddddd-dddd-4ddd-8ddd-dddddddddddd is xfs, not ext4", out)

    def test_new_uuid(self):
        src = btrfs_root_guest()
        dst = reformatted(src)
        dst["storage"]["disks"][0]["partitions"][2]["content"]["uuid"] = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        self.assertIn("no filesystem with the source's UUID", self.bad(src, dst, "--reformat", "/=ext4"))

    def test_label_lost(self):
        src = btrfs_root_guest()
        dst = reformatted(src)
        dst["storage"]["disks"][0]["partitions"][2]["content"]["label"] = None
        self.assertIn("DIFF storage.disks.0.partitions.2.content.label", self.bad(src, dst, "--reformat", "/=ext4"))

    def test_fstab_options_kept_the_subvolume(self):
        src = btrfs_root_guest()
        dst = reformatted(src)
        dst["storage"]["fstab"][0]["options"] = "subvol=@,noatime"
        self.assertIn("DIFF storage.fstab.0.options", self.bad(src, dst, "--reformat", "/=ext4"))

    def test_fstab_wrong_pass(self):
        src = btrfs_root_guest()
        dst = reformatted(src)
        dst["storage"]["fstab"][0]["passno"] = 0
        self.assertIn("DIFF storage.fstab.0.passno", self.bad(src, dst, "--reformat", "/=ext4"))

    def test_home_not_folded(self):
        src = btrfs_root_guest()
        dst = reformatted(src)
        dst["storage"]["fstab"].insert(2, copy.deepcopy(src["storage"]["fstab"][2]))
        self.bad(src, dst, "--reformat", "/=ext4")

    def test_rootflags_left_on_a_loader_entry(self):
        src = btrfs_root_guest()
        dst = reformatted(src)
        dst["boot"]["systemd_boot"]["entries"][0]["options"] = src["boot"]["systemd_boot"]["entries"][0]["options"]
        self.assertIn("DIFF boot.systemd_boot.entries.0.options", self.bad(src, dst, "--reformat", "/=ext4"))

    def test_another_filesystem_still_compared(self):
        src = btrfs_root_guest()
        dst = reformatted(src)
        dst["storage"]["disks"][0]["partitions"][0]["content"]["inode_size"] = 128
        self.assertIn("DIFF storage.disks.0.partitions.0.content.inode_size", self.bad(src, dst, "--reformat", "/=ext4"))

    def test_mount_not_in_fstab(self):
        src = btrfs_root_guest()
        self.assertIn("doesn't mount /srv", self.bad(src, reformatted(src), "--reformat", "/=ext4", "--reformat", "/srv=xfs"))

    def test_bad_argument(self):
        src = btrfs_root_guest()
        rc, out = self.judge(src, reformatted(src), "--reformat", "/=vfat")
        self.assertEqual(rc, 1, out)
        self.assertIn("--reformat MOUNT=TYPE", out)

    def test_grub_snapshot_entries(self):
        """openSUSE's snapper entries come first on btrfs, and with no kernel."""
        src = btrfs_root_guest()
        dst = reformatted(src)
        g = src["boot"]["grub"]["grub_cfg"]
        g["entries"].insert(0, "Help on bootable snapshot #$snapshot_num")
        g["default_linux"] = g["default_initrd"] = None
        self.assertIn("GRUB's btrfs snapshot entries left out", self.good(src, dst, "--reformat", "/=ext4"))
        dst["boot"]["grub"]["grub_cfg"]["entries"].append("Advanced options")
        self.bad(src, dst, "--reformat", "/=ext4")


class FsReformat(unittest.TestCase):
    """judge.py fs --reformat: the restored files after a btrfs root became ext4 or XFS."""

    judge_plain = Fs.judge

    def judge(self, before, after, *args):
        with tempfile.TemporaryDirectory() as tmp:
            s, t = os.path.join(tmp, "src-meta.json"), os.path.join(tmp, "dst-meta.json")
            for path, d in ((s, before), (t, after)):
                with open(path, "w") as f:
                    json.dump(d, f)
            r = subprocess.run([sys.executable, "-I", JUDGE, "fs", FSMETA, s, t, *args], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def base(self):
        return {"etc": meta("dir"), "etc/fstab": meta("file"), "etc/passwd": meta("file"),
                "var": meta("dir", flags=0x00800000), "var/log": meta("dir", flags=0x00800000 | 0x10),
                ".snapshots": meta("dir"), ".snapshots/1": meta("dir"), ".snapshots/1/snapshot": meta("dir"),
                ".snapshots/1/snapshot/usr/bin/perl": meta("file", links=[".snapshots/1/snapshot/usr/bin/perl", "usr/bin/perl", "usr/bin/perl5"]),
                "usr/bin/perl": meta("file", links=[".snapshots/1/snapshot/usr/bin/perl", "usr/bin/perl", "usr/bin/perl5"]),
                "usr/bin/perl5": meta("file", links=[".snapshots/1/snapshot/usr/bin/perl", "usr/bin/perl", "usr/bin/perl5"]),
                "var/lib/k": meta("dir", xattrs={"system.posix_acl_default": "02"})}

    def restored(self, to):
        before = self.base()
        after = copy.deepcopy(before)
        for p in [p for p in after if p.startswith(".snapshots/1/snapshot")]:
            del after[p]
        after["etc/fstab"].update(sha256="cd", mtime=2)
        after["var"]["flags"] = 0x1000 if to == "ext4" else 0
        after["var/log"]["flags"] = 0x10
        for p in ("usr/bin/perl", "usr/bin/perl5"):
            after[p]["links"] = ["usr/bin/perl", "usr/bin/perl5"]
        if to == "ext4":
            after["lost+found"] = meta("dir")
        else:
            after["var/lib/k"]["xattrs"]["trusted.SGI_ACL_DEFAULT"] = "03"
        return before, after

    def test_onto_ext4(self):
        before, after = self.restored("ext4")
        rc, out = self.judge(before, after, "--reformat", "/=ext4")
        self.assertEqual(rc, 0, out)
        self.assertIn("mke2fs's lost+found", out)
        self.assertEqual(self.judge(before, after)[0], 1)

    def test_onto_xfs(self):
        before, after = self.restored("xfs")
        rc, out = self.judge(before, after, "--reformat", "/=xfs")
        self.assertEqual(rc, 0, out)
        self.assertIn("trusted.SGI_ACL_*", out)

    def test_lost_found_only_onto_ext4(self):
        before, after = self.restored("xfs")
        after["lost+found"] = meta("dir")
        self.assertIn("DIFF extra: 1: lost+found", self.judge(before, after, "--reformat", "/=xfs")[1])

    def test_sgi_acl_only_onto_xfs_and_beside_an_acl(self):
        before, after = self.restored("ext4")
        after["var/lib/k"]["xattrs"]["trusted.SGI_ACL_DEFAULT"] = "03"
        self.assertIn("DIFF trusted-xattr", self.judge(before, after, "--reformat", "/=ext4")[1])
        before, after = self.restored("xfs")
        after["etc"]["xattrs"]["trusted.SGI_ACL_FILE"] = "03"
        self.assertIn("DIFF trusted-xattr: 1: etc", self.judge(before, after, "--reformat", "/=xfs")[1])

    def test_other_flags_still_count(self):
        before, after = self.restored("ext4")
        after["var/log"]["flags"] = 0
        self.assertIn("DIFF inode flags: 1: var/log", self.judge(before, after, "--reformat", "/=ext4")[1])

    def test_other_files_still_count(self):
        before, after = self.restored("ext4")
        after["etc/passwd"]["sha256"] = "ef"
        del after["etc"]
        out = self.judge(before, after, "--reformat", "/=ext4")[1]
        self.assertIn("DIFF content: 1: etc/passwd", out)
        self.assertIn("DIFF missing: 1: etc", out)

    def test_links_broken_otherwise_still_count(self):
        before, after = self.restored("ext4")
        after["usr/bin/perl"]["links"] = after["usr/bin/perl5"]["links"] = None
        self.assertIn("DIFF hard links: 2", self.judge(before, after, "--reformat", "/=ext4")[1])

    def test_a_mount_elsewhere(self):
        """--reformat /home=ext4: lost+found is /home's, not /'s."""
        before, after = self.restored("ext4")
        out = self.judge(before, after, "--reformat", "/home=ext4")[1]
        self.assertIn("DIFF extra: 1: lost+found", out)
        self.assertIn("DIFF inode flags", out)



class TimeshiftLeftOut(unittest.TestCase):
    """biglinux (bl4-1): Timeshift's snapshots under the top level's
    timeshift-btrfs/snapshots/ aren't in the backup, and load doesn't make
    them; they, and their qgroups, come off the source's dump.  Anything else
    missing still counts."""

    judge = Geli.judge
    TS = ["timeshift-btrfs/snapshots/2026-11-14_04-34-40/@", "timeshift-btrfs/snapshots/2026-11-14_04-57-19/@"]

    def biglinux(self, paths, mounted=("@", "@home"), qgroups=None):
        subs = [{"id": 256 + i, "gen": 10, "parent": 5, "top_level": 5, "uuid": f"u{i}",
                 "parent_uuid": "u0" if "timeshift" in p else None, "path": f"<FS_TREE>/{p}", "readonly": False, "properties": None}
                for i, p in enumerate(paths)]
        fs = {"uuid": "ac84b05f-63a3-4f7d-bafa-1c337613895c", "devices": ["vda2"], "mounted": True,
              "mounted_subvolumes": [{"target": "/" + m.lstrip("@"), "subvol": "/" + m} for m in mounted],
              "subvolumes": subs, "default_subvolume": {"id": 5}, "quota_enabled": qgroups is not None, "qgroups": qgroups}
        return dump([disk("vda", 24 * GIB, [part("vda2", 2, 616448, 49711104, btrfs(49711104 * 512), gpt=True)], gpt=True)], btrfs=[fs])

    def test_timeshifts_snapshots_are_left_out(self):
        rc, out = self.judge(self.biglinux(["@", "@home", "@swapfile", *self.TS]), self.biglinux(["@", "@home", "@swapfile"]))
        self.assertEqual(rc, 0, out)
        self.assertIn(f"left out (Timeshift's snapshots aren't in the backup, and load doesn't make them): 2: {', '.join(self.TS)}", out)

    def test_their_qgroups_go_with_them(self):
        q = lambda *ids: [{"qgroupid": f"0/{i}", "referenced": 1, "exclusive": 1} for i in ids]
        rc, out = self.judge(self.biglinux(["@", "@home", *self.TS], qgroups=q(256, 257, 258, 259)), self.biglinux(["@", "@home"], qgroups=q(256, 257)))
        self.assertEqual(rc, 0, out)
        rc, out = self.judge(self.biglinux(["@", "@home", *self.TS], qgroups=q(256, 257, 258, 259)), self.biglinux(["@", "@home"], qgroups=q(256)))
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF storage.btrfs.0.qgroups.1.qgroupid", out)

    def test_one_made_after_all_is_a_difference(self):
        rc, out = self.judge(self.biglinux(["@", "@home", *self.TS]), self.biglinux(["@", "@home", *self.TS]))
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF storage.btrfs.0.subvolumes.2.path", out)

    def test_other_subvolumes_still_count(self):
        """An unmounted subvolume that isn't Timeshift's, one a mount reaches
        under timeshift-btrfs, and a Timeshift path below the top level."""
        for gone in ("@snapshots", "timeshift-btrfs/snapshots/X/@", "@/timeshift-btrfs/snapshots/X/@", "timeshift-btrfs/X/@"):
            mounted = ("@", "@home", gone) if gone == "timeshift-btrfs/snapshots/X/@" else ("@", "@home")
            rc, out = self.judge(self.biglinux(["@", "@home", gone], mounted), self.biglinux(["@", "@home"], mounted))
            self.assertEqual(rc, 1, f"{gone}: {out}")
            self.assertIn("DIFF storage.btrfs.0.subvolumes.2.path", out)
            self.assertNotIn("left out", out)


class EspProxmox(unittest.TestCase):
    """judge.py esp over proxmox9 and proxmox9-mirror's ESPs (P6): rebuilt by
    proxmox-boot-tool, judged by content under their volume IDs."""

    K = "7.0.14-28-pve"

    def esp(self, **change):
        """One ESP's fsmeta record, as proxmox-boot-tool leaves it; CHANGE
        maps a path to its new sha256, or None to leave it out."""
        files = {"EFI/BOOT/BOOTX64.EFI": "sd", "EFI/systemd/systemd-bootx64.efi": "sd", "EFI/memtest86+x64.efi": "mt",
                 f"EFI/proxmox/{self.K}/vmlinuz-{self.K}": "vm", f"EFI/proxmox/{self.K}/initrd.img-{self.K}": "in",
                 "loader/loader.conf": "lc", f"loader/entries/proxmox-{self.K}.conf": "en",
                 "loader/entries/memtest86+x64.efi.conf": "me", "loader/entries.srel": "sr", "loader/random-seed": "rs"}
        rec = {".": meta("dir")}
        for d in ("EFI", "EFI/BOOT", "EFI/systemd", "EFI/proxmox", f"EFI/proxmox/{self.K}", "loader", "loader/entries"):
            rec[d] = meta("dir")
        for p, h in files.items():
            h = change.get(p, h)
            if h is not None:
                rec[p] = meta("file", sha256=h, mtime=7 if change else 1)
        return rec

    def judge(self, ids, src, dst, su=None, du=None):
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in (("src-uuids", su), ("dst-uuids", du)):
                with open(os.path.join(tmp, name), "w") as f:
                    f.write(text if text is not None else "".join(i + "\n" for i in ids))
            for who, recs in (("src", src), ("dst", dst)):
                for i, rec in recs.items():
                    with open(os.path.join(tmp, f"{who}-{i}.json"), "w") as f:
                        json.dump(rec, f)
            r = subprocess.run([sys.executable, "-I", JUDGE, "esp", tmp], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    REBUILT = {f"EFI/proxmox/{K}/initrd.img-{K}": "in2", "loader/random-seed": "rs2"}

    def test_a_rebuilt_esp_passes(self):
        rc, out = self.judge(["06D4-31B8"], {"06D4-31B8": self.esp()}, {"06D4-31B8": self.esp(**self.REBUILT)})
        self.assertEqual(rc, 0, out)
        self.assertIn("1 kernels and 1 proxmox entries byte for byte", out)
        self.assertIn("update-initramfs rebuilt it in the boot stage, and refresh copied it: 1", out)

    def test_the_mirrors_two_pass_when_equal(self):
        ids = ["2D89-C7E2", "2D8B-1862"]
        dst = {ids[0]: self.esp(**self.REBUILT), ids[1]: self.esp(**dict(self.REBUILT, **{"loader/random-seed": "rs3"}))}
        rc, out = self.judge(ids, {i: self.esp() for i in ids}, dst)
        self.assertEqual(rc, 0, out)
        self.assertIn("the 2 restored ESPs are equal but for their random seeds", out)

    def test_the_mirrors_two_must_hold_the_same_initrd(self):
        ids = ["2D89-C7E2", "2D8B-1862"]
        dst = {ids[0]: self.esp(**self.REBUILT), ids[1]: self.esp(**dict(self.REBUILT, **{f"EFI/proxmox/{self.K}/initrd.img-{self.K}": "in3"}))}
        rc, out = self.judge(ids, {i: self.esp() for i in ids}, dst)
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF ESPs 2D89-C7E2 and 2D8B-1862 (restored): content: 1: EFI/proxmox/{self.K}/initrd.img-{self.K}", out)

    def test_a_kernel_must_be_the_sources(self):
        rc, out = self.judge(["A"], {"A": self.esp()}, {"A": self.esp(**{f"EFI/proxmox/{self.K}/vmlinuz-{self.K}": "vm2"})})
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF ESP A: content: 1: EFI/proxmox/{self.K}/vmlinuz-{self.K}", out)

    def test_whatever_is_gone_counts(self):
        """memtest86+'s file and entry, a loader, an entry, a kernel's initrd."""
        for p in ("EFI/memtest86+x64.efi", "loader/entries/memtest86+x64.efi.conf", "EFI/BOOT/BOOTX64.EFI",
                  f"loader/entries/proxmox-{self.K}.conf", f"EFI/proxmox/{self.K}/initrd.img-{self.K}"):
            rc, out = self.judge(["A"], {"A": self.esp()}, {"A": self.esp(**{p: None})})
            self.assertEqual(rc, 1, f"{p}: {out}")
            self.assertIn(f"DIFF ESP A: missing: 1: {p}", out)

    def test_a_new_file_counts(self):
        dst = self.esp()
        dst["EFI/proxmox/7.0.2-6-pve/vmlinuz-7.0.2-6-pve"] = meta("file", sha256="x")
        rc, out = self.judge(["A"], {"A": self.esp()}, {"A": dst})
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF ESP A: extra: 1", out)

    def test_the_uuid_list_is_byte_for_byte(self):
        rc, out = self.judge(["A", "B"], {"A": self.esp(), "B": self.esp()}, {"A": self.esp(), "B": self.esp()}, du="A\nB\nA\n")
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF etc/kernel/proxmox-boot-uuids", out)

    def test_an_esp_not_restored_counts(self):
        rc, out = self.judge(["A", "B"], {"A": self.esp(), "B": self.esp()}, {"A": self.esp()})
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF ESP B: no record", out)

    def test_fs_lets_the_uuid_list_be_rewritten_not_changed(self):
        before = {"etc/kernel/proxmox-boot-uuids": meta("file", sha256="u")}
        rc, out = Fs.judge(self, before, {"etc/kernel/proxmox-boot-uuids": meta("file", sha256="u", mtime=9)})
        self.assertEqual(rc, 0, out)
        rc, out = Fs.judge(self, before, {"etc/kernel/proxmox-boot-uuids": meta("file", sha256="v", size=4, mtime=9)})
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF content: 1: etc/kernel/proxmox-boot-uuids", out)

    def test_fs_lets_proxmox_boot_tool_rewrite_76pveconf_not_change_it(self):
        """pve6-5: proxmox-auto-removal writes apt's list of kept kernels
        again, the same bytes; and proxmox-boot-tool's mount directory."""
        before = {"etc/apt/apt.conf.d": meta("dir"), "etc/apt/apt.conf.d/76pveconf": meta("file", sha256="k"), "var/tmp/espmounts": meta("dir")}
        after = {"etc/apt/apt.conf.d": meta("dir", mtime=9), "etc/apt/apt.conf.d/76pveconf": meta("file", sha256="k", mtime=9),
                 "var/tmp/espmounts": meta("dir", mtime=9)}
        rc, out = Fs.judge(self, before, after)
        self.assertEqual(rc, 0, out)
        after["etc/apt/apt.conf.d/76pveconf"] = meta("file", sha256="k2", mtime=9)
        rc, out = Fs.judge(self, before, after)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF content: 1: etc/apt/apt.conf.d/76pveconf", out)

    @staticmethod
    def zvol(first, kind):
        d = disk("zd0", 64 * MIB, [])
        d.update(partition_table=None, links=["/dev/zvol/rpool/data/vm-100-disk-0"], boot_code={"kind": kind, "first_bytes": first})
        return d

    def test_a_zvol_made_empty(self):
        """pve6-5: the source's zvol held bytes ("unknown" boot code); load
        makes it empty.  A copy that isn't empty, or a disk that isn't a
        zvol, still counts."""
        src = dump([gpt_guest()["storage"]["disks"][0], self.zvol("4261636b", "unknown")])
        rc, out = Geli.judge(self, src, dump([src["storage"]["disks"][0], self.zvol("00000000", "none")]))
        self.assertEqual(rc, 0, out)
        self.assertIn("a zvol's bytes: load makes it empty", out)
        rc, out = Geli.judge(self, src, dump([src["storage"]["disks"][0], self.zvol("eb63", "grub")]))
        self.assertEqual(rc, 1, out)
        plain = copy.deepcopy(src)
        plain["storage"]["disks"][1]["links"] = []
        dst = copy.deepcopy(plain)
        dst["storage"]["disks"][1]["boot_code"] = {"kind": "none", "first_bytes": "00000000"}
        rc, out = Geli.judge(self, plain, dst)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF storage.disks.1.boot_code.kind", out)

    def test_proxmox_boot_esp_sizes(self):
        """The initrds' sizes in boot.proxmox_boot's own ESP list, as in boot.esps."""
        def d(size):
            x = dump([])
            x["boot"] = {"proxmox_boot": {"uuids": ["A"], "esps": [{"uuid": "A", "contents": {"files": [{"path": "EFI/proxmox/K/initrd.img-K", "size": size}]}}]}}
            return x
        rc, out = Geli.judge(self, d(1), d(2))
        self.assertEqual(rc, 0, out)


def renumbered(d, ren):
    """D with its dm-N renamed by REN ({old: new}) everywhere, as a boot that
    activates the groups in another order numbers them."""
    w = re.compile(r"\b(" + "|".join(map(re.escape, ren)) + r")\b")

    def walk(v):
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return w.sub(lambda m: ren[m.group(1)], v) if isinstance(v, str) else v
    return walk(d)


class DmByName(unittest.TestCase):
    """vanilla3 (V7): the restored machine activated its volume groups in
    another order, so the same LVs have other dm-N: compared by their
    device-mapper names instead; a device renamed or gone still counts."""

    judge = Geli.judge

    def test_renumbered_devices_compare_by_name(self):
        src = lvm_guest()
        dst = renumbered(copy.deepcopy(src), {"dm-0": "dm-1", "dm-1": "dm-0"})
        dst["storage"]["dm_devices"].reverse()
        rc, out = self.judge(src, dst)
        self.assertEqual(rc, 0, out)
        self.assertIn("device-mapper devices compared by name: their dm-N differ (vg0-root dm-0, vg0-swap dm-1 on the source)", out)

    def test_same_numbers_change_nothing(self):
        src = lvm_guest()
        rc, out = self.judge(src, copy.deepcopy(src))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("compared by name", out)

    def test_a_renamed_device_still_counts(self):
        src = lvm_guest()
        dst = renumbered(copy.deepcopy(src), {"dm-0": "dm-1", "dm-1": "dm-0"})
        dst["storage"]["dm_devices"][1]["dm"]["dm_name"] = "vg0-swap2"
        rc, out = self.judge(src, dst)
        self.assertEqual(rc, 1, out)
        self.assertNotIn("compared by name", out)


class ThinPoolMade(unittest.TestCase):
    """vanilla3 (V7): load makes a thin pool, its data and metadata LVs, the
    spare and the thin LVs anew by lvcreate, so their LV UUIDs, host and
    time, their order in the group's metadata and where lvcreate put the
    pool's hidden LVs are its own; their sizes, segments and the thin LVs'
    device ids, and every other LV, still count."""

    judge = Geli.judge

    @staticmethod
    def thin(order=("init", "root", "root-a", "root_tmeta", "lvol0_pmspare", "root_tdata"), at=(5120, 5376, 256), host="vanillaos", ids="s"):
        def seg(n, pe):
            return [{"start_extent": 0, "extent_count": n, "type": "striped", "stripe_count": 1, "stripes": ["pv0", pe]}]
        lvs = {
            "init": {"name": "init", "id": "lvinit", "creation_host": "vanillaos", "segments": seg(256, 0), "active_device": "dm-0"},
            "root": {"name": "root", "id": f"{ids}pool", "creation_host": host,
                     "segments": [{"start_extent": 0, "extent_count": 4864, "type": "thin-pool", "metadata": "root_tmeta", "pool": "root_tdata", "transaction_id": 2, "chunk_size": 128}]},
            "root-a": {"name": "root-a", "id": f"{ids}thina", "creation_host": host, "active_device": "dm-1",
                       "segments": [{"start_extent": 0, "extent_count": 4864, "type": "thin", "thin_pool": "root", "transaction_id": 0, "device_id": 1}]},
            "root_tmeta": {"name": "root_tmeta", "id": f"{ids}tmeta", "creation_host": host, "segments": seg(256, at[0])},
            "lvol0_pmspare": {"name": "lvol0_pmspare", "id": f"{ids}spare", "creation_host": host, "segments": seg(256, at[1])},
            "root_tdata": {"name": "root_tdata", "id": f"{ids}tdata", "creation_host": host, "segments": seg(4864, at[2])},
        }
        lvm = {"volume_groups": [{"name": "vos-root", "id": "vg1", "physical_volumes": [{"name": "pv0", "id": "pv1"}],
                                  "logical_volumes": [copy.deepcopy(lvs[n]) for n in order]}]}
        dms = [{"name": "dm-0", "dm": {"dm_name": "vos--root-init", "lv_uuid": "lvinit", "dm_uuid": "LVM-vg1lvinit"}, "links": ["/dev/disk/by-id/dm-uuid-LVM-vg1lvinit"]},
               {"name": "dm-1", "dm": {"dm_name": "vos--root-root--a", "lv_uuid": f"{ids}thina", "dm_uuid": f"LVM-vg1{ids}thina"}, "links": [f"/dev/disk/by-id/dm-uuid-LVM-vg1{ids}thina"]}]
        return dump([disk("vda", 16 * GIB, [])], lvm=lvm, dm_devices=dms)

    def test_made_anew(self):
        dst = self.thin(order=("init", "root", "root-a", "lvol0_pmspare", "root_tmeta", "root_tdata"), at=(5376, 256, 512), host="ubuntu26-uefi", ids="d")
        rc, out = self.judge(self.thin(), dst)
        self.assertEqual(rc, 0, out)
        self.assertIn("made anew by lvcreate: new LV UUIDs, the rescuer's host and time): 10:", out)
        self.assertIn("where lvcreate puts a thin pool's data, metadata and spare LVs on the PV is its own): 3:", out)
        self.assertIn("device-mapper UUID holds its LV UUID, new from lvcreate): 3:", out)

    def test_sizes_device_ids_and_other_lvs_count(self):
        src = self.thin()
        # (Sorted by name: init, lvol0_pmspare, root, root-a, root_tdata, root_tmeta.)
        for change in (lambda lvs: lvs["root_tdata"]["segments"][0].update(extent_count=4000),
                       lambda lvs: lvs["root-a"]["segments"][0].update(device_id=3),
                       lambda lvs: lvs["init"].update(id="lvinit2"),
                       lambda lvs: lvs["init"]["segments"][0]["stripes"].__setitem__(1, 8)):
            dst = self.thin(ids="d")
            change({lv["name"]: lv for lv in dst["storage"]["lvm"]["volume_groups"][0]["logical_volumes"]})
            rc, out = self.judge(src, dst)
            self.assertEqual(rc, 1, out)
            self.assertEqual(out.count("DIFF "), 1, out)


class ThinCounters(unittest.TestCase):
    """vanilla3 (2026-11-17): bko-holt's thin_usage (08f427f) records a pool's
    data_used_bytes and metadata_used_bytes and a thin LV's mapped_bytes.  A
    thin LV that holds a filesystem the backup reads comes back with what
    the restore wrote, not the source's mapping (judge.py fs compares its
    files); one that holds anything else keeps its mapping compared, the
    only sign it came back empty, and so does its pool."""

    judge = Geli.judge

    @staticmethod
    def counted(fs=True, used=(13333233664, 21262336), mapped=6400507904, raw=False, **kw):
        d = ThinPoolMade.thin(**kw)
        lvs = {lv["name"]: lv for lv in d["storage"]["lvm"]["volume_groups"][0]["logical_volumes"]}
        lvs["root"].update(data_used_bytes=used[0], metadata_used_bytes=used[1])
        lvs["root-a"].update(mapped_bytes=mapped, size_bytes=20401094656)
        dm = d["storage"]["dm_devices"][1]
        dm["dm"].update(vg="vos-root", lv="root-a")
        if fs:
            dm["content"] = {"type": "btrfs", "usage": "filesystem", "uuid": "32e2ca47-95b4-4079-a174-e313954c37e0"}
            d["storage"]["backup_sources"] = [{"path": "/part-future", "device": "dm-1", "fstype": "btrfs"}]
        if raw:
            dm["raw_volume"] = {"image": "/.bko-holt/raw/vos-root/root-a.img", "why": "test"}
        return d

    DST = dict(order=("init", "root", "root-a", "lvol0_pmspare", "root_tmeta", "root_tdata"), at=(5376, 256, 512), host="ubuntu26-uefi", ids="d")

    def test_counters_of_a_filesystem_pool(self):
        rc, out = self.judge(self.counted(), self.counted(used=(13046317056, 20819968), mapped=6499926016, **self.DST))
        self.assertEqual(rc, 0, out)
        self.assertIn("a thin LV's mapped blocks, where it holds a filesystem the backup reads", out)
        self.assertIn("what the restore wrote into a new LV, not the source's history of writes and discards): 1: storage.lvm.volume_groups.0.logical_volumes.3.mapped_bytes", out)
        self.assertIn("what the restore wrote into a new pool): 2:", out)

    def test_size_still_counts(self):
        dst = self.counted(used=(13046317056, 20819968), mapped=6499926016, **self.DST)
        next(lv for lv in dst["storage"]["lvm"]["volume_groups"][0]["logical_volumes"] if lv["name"] == "root-a")["size_bytes"] = 10 * GIB
        rc, out = self.judge(self.counted(), dst)
        self.assertEqual(rc, 1, out)
        self.assertEqual(out.count("DIFF "), 1, out)
        self.assertIn("DIFF storage.lvm.volume_groups.0.logical_volumes.3.size_bytes:", out)

    def test_a_thin_lv_without_a_filesystem_back_empty(self):
        """No filesystem, or a raw volume, or not a backup source: an LV's
        mapping going to 0 is a DIFF, and so are its pool's counters."""
        for src, dst in ((self.counted(fs=False), self.counted(fs=False, used=(0, 1 << 20), mapped=0, **self.DST)),
                         (self.counted(raw=True), self.counted(raw=True, used=(0, 1 << 20), mapped=0, **self.DST))):
            rc, out = self.judge(src, dst)
            self.assertEqual(rc, 1, out)
            self.assertIn("DIFF storage.lvm.volume_groups.0.logical_volumes.3.mapped_bytes: 6400507904 -> 0", out)
            self.assertIn("DIFF storage.lvm.volume_groups.0.logical_volumes.2.data_used_bytes:", out)
            self.assertIn("DIFF storage.lvm.volume_groups.0.logical_volumes.2.metadata_used_bytes:", out)
            self.assertEqual(out.count("DIFF "), 3, out)
        src, dst = self.counted(), self.counted(used=(0, 1 << 20), mapped=0, **self.DST)
        for d in (src, dst):
            d["storage"]["backup_sources"] = []
        rc, out = self.judge(src, dst)
        self.assertEqual(rc, 1, out)
        self.assertEqual(out.count("DIFF "), 3, out)

    def test_restored_without_its_filesystem(self):
        """The source's LV held a filesystem, the restored one doesn't: no excuse."""
        dst = self.counted(fs=False, used=(13046317056, 20819968), mapped=6499926016, **self.DST)
        rc, out = self.judge(self.counted(), dst)
        self.assertEqual(rc, 1, out)
        self.assertIn("logical_volumes.3.mapped_bytes: 6400507904 -> 6499926016", out)
        self.assertIn("logical_volumes.2.data_used_bytes:", out)


class FsSnapshotHoles(unittest.TestCase):
    """anduinos2 (2026-11-17): load makes a deployment's snapshot from the
    restored live tree, and the restore keeps a file whose bytes match
    (--clone-from), filling holes only in NOCOW files.  A snapshot's file
    with its live copy's bytes but other holes on the source comes back with
    the restored live copy's allocation: excused only when that is so."""

    judge = Fs.judge
    SNAP = ".snapshots/anduinos-btrfs-snapshots-manager/deployments/e707f13a-749e-46de-95b3-963c1ed78b68/root/usr/lib/locale/locale-archive"
    LIVE = "usr/lib/locale/locale-archive"
    DENSE = dict(size=42580048, blocks=83168, sha256="016e9d61", holes=False, hole_blocks=0, flags=0)
    SPARSE = dict(DENSE, blocks=83104, holes=True, hole_blocks=64)
    RESTORED = dict(DENSE, blocks=83008, holes=True, hole_blocks=160)
    WHY = "a snapshot's file with its live copy's bytes but not its holes on the source"

    def records(self, snap, live, rsnap, rlive, snap_path=None, live_path=None):
        sp, lp = snap_path or self.SNAP, live_path or self.LIVE
        return ({sp: meta("file", **snap), lp: meta("file", **live)},
                {sp: meta("file", **rsnap), lp: meta("file", **rlive)})

    def test_dense_snapshot_sparse_live(self):
        rc, out = self.judge(*self.records(self.DENSE, self.SPARSE, self.RESTORED, self.RESTORED))
        self.assertEqual(rc, 0, out)
        self.assertIn(self.WHY, out)
        self.assertIn("0 differences", out)
        self.assertEqual(out.count(self.SNAP), 2, out)     # holes gained, allocation lost

    def test_sparse_snapshot_dense_live(self):
        dense = dict(self.DENSE)
        rc, out = self.judge(*self.records(self.SPARSE, dense, dense, dense))
        self.assertEqual(rc, 0, out)
        self.assertIn(self.WHY, out)

    def test_snapper_layout(self):
        rc, out = self.judge(*self.records(self.DENSE, self.SPARSE, self.RESTORED, self.RESTORED,
                                           snap_path=".snapshots/3/snapshot/usr/lib/locale/locale-archive"))
        self.assertEqual(rc, 0, out)
        self.assertIn(self.WHY, out)

    def test_differences(self):
        nocow = dict(self.DENSE, flags=0x00800000)
        for name, recs in (
                ("other bytes on the source", self.records(dict(self.DENSE, sha256="aa"), self.SPARSE, dict(self.RESTORED, sha256="aa"), self.RESTORED)),
                ("NOCOW", self.records(nocow, dict(self.SPARSE, flags=0x00800000), dict(self.RESTORED, flags=0x00800000), dict(self.RESTORED, flags=0x00800000))),
                ("not the restored live copy's allocation", self.records(self.DENSE, self.SPARSE, self.RESTORED, dict(self.RESTORED, blocks=83040, hole_blocks=128))),
                ("no root or snapshot component", self.records(self.DENSE, self.SPARSE, self.RESTORED, self.RESTORED,
                                                              snap_path=".snapshots/anduinos/deployments/e707/usr/lib/locale/locale-archive")),
                ("the same holes on the source", self.records(self.SPARSE, self.SPARSE, dict(self.DENSE), dict(self.SPARSE)))):
            rc, out = self.judge(*recs)
            self.assertEqual(rc, 1, f"{name}: {out}")
            self.assertNotIn(self.WHY, out, name)
            self.assertIn("DIFF ", out, name)

    def test_live_counterpart_missing(self):
        before, after = self.records(self.DENSE, self.SPARSE, self.RESTORED, self.RESTORED)
        for d in (before, after):
            del d[self.LIVE]
        rc, out = self.judge(before, after)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF holes gained: 1", out)
        self.assertIn("DIFF allocation lost: 1", out)


class FsTransactional(unittest.TestCase):
    """judge.py fs over a MicroOS restore (docs/design/aeon.md, M5): load's
    transactional arm regenerates nothing in a snapshot, so the ESP, every
    slot's /boot and /usr, grub.cfg and the initrds get no boot-stage
    excuse; load's log and health-check.state's rewritten ID do."""

    HELD = ["boot/efi/EFI/BOOT/bootx64.efi", "boot/efi/EFI/BOOT/grub.efi", "boot/efi/EFI/BOOT/grub.cfg",
            "boot/grub2/grub.cfg", "boot/initrd-7.3.3-1-default", "boot/vmlinuz-7.3.3-1-default",
            "usr/sbin/tukit", "usr/share/bko-tu/base.bin",
            ".snapshots/3/snapshot/boot/grub2/grub.cfg", ".snapshots/3/snapshot/boot/initrd-7.3.3-1-default",
            ".snapshots/3/snapshot/usr/share/bko-tu/base.bin"]
    SRC = {"boot": meta("dir"), "boot/efi": meta("dir"), "boot/efi/EFI": meta("dir"), "boot/efi/EFI/BOOT": meta("dir"),
           "boot/grub2": meta("dir"), "var": meta("dir"), "var/log": meta("dir"), "var/lib": meta("dir"),
           "var/lib/misc": meta("dir"), "var/lib/misc/health-check.state": meta("file"),
           **{p: meta("file") for p in HELD}}

    judge = Fs.judge

    def changed(self, src, changes):
        a = copy.deepcopy(src)
        for p, kw in changes.items():
            a[p].update(kw)
        return self.judge(src, a)

    def test_loads_log_and_health_check_state_only(self):
        a = copy.deepcopy(self.SRC)
        a["var/log/bko-holt"] = meta("dir")
        a["var/log/bko-holt/load.log"] = meta("file")
        a["var/log"]["mtime"] = 2
        a["var/lib/misc"]["mtime"] = 2
        a["var/lib/misc/health-check.state"].update(sha256="cd", mtime=2)
        rc, out = self.judge(self.SRC, a)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("DIFF", out)
        self.assertIn("rewrites health-checker's LAST_WORKING_BTRFS_ID", out)
        held = [p for p in self.SRC if re.search(r"^(boot|usr)(/|$)|^\.snapshots/\d+/snapshot/(boot|usr)(/|$)", p)]
        self.assertIn(f": {len(held)} paths of the ESP and every slot's /boot and /usr held to the source's", out)

    def test_nothing_in_a_snapshot_or_the_esp_may_change(self):
        """grub2-mkconfig, grub2-install, shim-install or dracut on any of
        them: the rules that excuse them on other systems don't apply."""
        for p in self.HELD:
            rc, out = self.changed(self.SRC, {p: {"sha256": "cd", "mtime": 2}})
            self.assertEqual(rc, 1, f"{p}: {out}")
            self.assertIn(f"DIFF content: 1: {p}", out)
            self.assertIn(" 0 from the boot stage", out)
        rc, out = self.changed(self.SRC, {"boot/initrd-7.3.3-1-default": {"xattrs": {"security.selinux": "00"}}})
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF selinux: 1:", out)

    def test_the_booted_slots_dirs_under_a_mount(self):
        """Slot 3, at / on the source, has files in its own var/ that @/var
        hides: the record reaches them through .snapshots/3/snapshot, the
        restore can't, and its var/ comes back an empty mount point.  The
        same files missing where nothing hides them (slot 4's var/, which
        is in the backup) still count."""
        src = copy.deepcopy(self.SRC)
        src.update({"var/lib/misc/x": meta("file"), ".snapshots/3/snapshot/var": meta("dir"), ".snapshots/3/snapshot/var/lib": meta("dir"),
                    ".snapshots/3/snapshot/var/lib/zypp": meta("file"), ".snapshots/4/snapshot/var": meta("dir"),
                    ".snapshots/4/snapshot/var/lib": meta("dir"), ".snapshots/4/snapshot/var/lib/zypp": meta("file")})
        a = {p: m for p, m in src.items() if not p.startswith(".snapshots/3/snapshot/var/")}
        a[".snapshots/3/snapshot/var"] = meta("dir", mtime=2)
        rc, out = self.judge(src, a)
        self.assertEqual(rc, 0, out)
        self.assertIn("hidden under a subvolume fstab mounts on it", out)
        del a[".snapshots/4/snapshot/var/lib/zypp"]
        rc, out = self.judge(src, a)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF missing: 1: .snapshots/4/snapshot/var/lib/zypp", out)

    def test_other_systems_keep_their_rules(self):
        src = {p: m for p, m in self.SRC.items() if p != "usr/sbin/tukit"}
        rc, out = self.changed(src, {"boot/grub2/grub.cfg": {"sha256": "cd"}, "boot/initrd-7.3.3-1-default": {"sha256": "cd"}})
        self.assertEqual(rc, 0, out)
        self.assertNotIn("transactional", out)


class Transactional(unittest.TestCase):
    """judge.py's boot.transactional (M5): the restored machine boots the
    source's default, its pending update, as its default, and
    health-checker's state names it by its restored ID.  A health-checker
    rollback on the restored first boot (another slot booted, the default
    set back) must be a DIFF, as must the source's booted slot booted."""

    judge = Fit.judge

    @staticmethod
    def tu(booted, default, hc=None, pending=None, tu_state=None):
        slot = lambda n, i: {"path": f"@/.snapshots/{n}/snapshot", "id": i, "snapshot": n}
        t = {"filesystem": "bc000000-0000-4000-8000-000000000005", "fstab_root_options": "ro=vfs",
             "tools": ["/usr/sbin/tukit", "/usr/sbin/transactional-update"], "booted": slot(*booted),
             "default": slot(*default), "pending_update": booted[0] != default[0] if pending is None else pending,
             "etc": "subvolume", "loader_mode": "grub",
             "transactional_update_state": tu_state or {"LAST_BOOTED": str(booted[0])},
             "health_check_state": None if hc is None else {"last_working_btrfs_id": hc[1], "last_working_btrfs_id_raw": str(hc[1]),
                                                            "last_working_snapshot": f"@/.snapshots/{hc[0]}/snapshot"}}
        d = dump([disk("vda", 16 * GIB, [])])
        d["boot"] = {"transactional": t}
        return d

    def src(self):
        return self.tu((3, 273), (7, 284), hc=(3, 273))

    def test_the_pending_update_booted(self):
        out = Fit.good(self, self.src(), self.tu((7, 280), (7, 280), hc=(7, 280)))
        self.assertIn("booted = default = the source's default @/.snapshots/7/snapshot (pending there; the source booted @/.snapshots/3/snapshot)", out)
        self.assertIn("transactional-update's state moved", out)

    def test_a_silent_health_checker_rollback_is_a_difference(self):
        """The first boot failed, health-checker set the default back to the
        last good slot and booted it: a running machine, the wrong slot."""
        out = Fit.bad(self, self.src(), self.tu((3, 270), (3, 270), hc=(3, 270)))
        self.assertIn("DIFF transactional: booted @/.snapshots/3/snapshot, not the source's default @/.snapshots/7/snapshot", out)
        self.assertIn("DIFF transactional: the default is @/.snapshots/3/snapshot", out)

    def test_the_sources_booted_slot_with_its_default_kept(self):
        out = Fit.bad(self, self.src(), self.tu((3, 270), (7, 280), hc=(3, 270)))
        self.assertIn("DIFF transactional: booted @/.snapshots/3/snapshot", out)
        self.assertIn("an update is pending", out)

    def test_health_check_state_must_name_the_restored_id(self):
        out = Fit.bad(self, self.src(), self.tu((7, 280), (7, 280), hc=(7, 284)))
        self.assertIn("DIFF transactional: health-check.state names @/.snapshots/7/snapshot ID 284, not the booted @/.snapshots/7/snapshot ID 280", out)
        out = Fit.bad(self, self.src(), self.tu((7, 280), (7, 280), hc=(3, 280)))
        self.assertIn("health-check.state names @/.snapshots/3/snapshot", out)
        out = Fit.bad(self, self.src(), self.tu((7, 280), (7, 280)))
        self.assertIn("DIFF transactional: no health-check.state on the restored system", out)

    def test_the_rest_must_match(self):
        dst = self.tu((7, 280), (7, 280), hc=(7, 280))
        dst["boot"]["transactional"]["etc"] = "overlay"
        out = Fit.bad(self, self.src(), dst)
        self.assertIn('DIFF transactional: etc: "subvolume" -> "overlay"', out)
        dst = self.tu((7, 280), (7, 280), hc=(7, 280))
        del dst["boot"]["transactional"]
        out = Fit.bad(self, self.src(), dst)
        self.assertIn("the source is transactional (boot.transactional), the restored system isn't", out)

    @staticmethod
    def with_btrfs(d, root, ids, syncpoint=False):
        """D with the btrfs and mounts of a MicroOS guest booted from slot
        ROOT: slots 3 and 7 and their /etc, IDS their IDs; 7's etc.syncpoint
        while 7 hasn't booted (SYNCPOINT)."""
        sub = lambda path, i, parent, ro: {"id": i, "parent": parent, "top_level": parent, "parent_uuid": True, "readonly": ro,
                                          "path": path if path.startswith(f"@/.snapshots/{root}/snapshot/") else f"<FS_TREE>/{path}"}
        subs = [sub("@/.snapshots", 257, 256, False)]
        for n, i in zip((3, 7), ids):
            subs += [sub(f"@/.snapshots/{n}/snapshot", i, 257, True), sub(f"@/.snapshots/{n}/snapshot/etc", i + 1, i, False)]
        if syncpoint:
            subs.append(sub("@/.snapshots/7/snapshot/etc/etc.syncpoint", ids[1] + 2, ids[1] + 1, False))
        rid = ids[(3, 7).index(root)]
        d["storage"]["btrfs"] = [{"uuid": "bc000000-0000-4000-8000-000000000005", "subvolumes": subs,
                                  "default_subvolume": {"id": ids[1], "path": "@/.snapshots/7/snapshot"},
                                  "mounted_subvolumes": [{"target": "/", "subvol": f"/@/.snapshots/{root}/snapshot"},
                                                         {"target": "/etc", "subvol": f"/@/.snapshots/{root}/snapshot/etc"}],
                                  "qgroups": [{"qgroupid": f"0/{s['id']}", "path": s["path"].removeprefix("<FS_TREE>/")} for s in subs]}]
        d["storage"]["mounts"] = [{"target": "/", "root": f"/@/.snapshots/{root}/snapshot",
                                   "super_options": f"rw,seclabel,subvolid={rid},subvol=/@/.snapshots/{root}/snapshot"},
                                  {"target": "/etc", "root": f"/@/.snapshots/{root}/snapshot/etc",
                                   "super_options": f"rw,seclabel,subvolid={rid + 1},subvol=/@/.snapshots/{root}/snapshot/etc"}]
        d["storage"]["backup_sources"] = [{"target": "/", "subvol": f"/@/.snapshots/{root}/snapshot"}]
        return d

    def test_the_restored_boot_moves_the_root(self):
        """The source booted 3 with 7 pending; the restored machine boots 7,
        whose IDs are new, and whose etc.syncpoint its /etc sync consumed:
        the mounts at / and /etc, the listing's <FS_TREE>/ and the nested
        subvolumes' IDs follow, nothing else."""
        src = self.with_btrfs(self.src(), 3, (273, 284), syncpoint=True)
        dst = self.with_btrfs(self.tu((7, 290), (7, 290), hc=(7, 290)), 7, (275, 290))
        out = Fit.good(self, src, dst)
        self.assertIn("7 fields of the source's mounts, mounted subvolumes and backup sources read with its default @/.snapshots/7/snapshot at / for its booted @/.snapshots/3/snapshot", out)
        self.assertIn("@/.snapshots/7/snapshot/etc/etc.syncpoint consumed by the restored first boot's /etc sync", out)

    def test_the_root_moved_elsewhere_or_a_syncpoint_kept(self):
        src = self.with_btrfs(self.src(), 3, (273, 284), syncpoint=True)
        # Booted 3 again (a health-checker rollback): its mounts don't move.
        dst = self.with_btrfs(self.tu((3, 273), (3, 273), hc=(3, 273)), 3, (273, 290))
        out = Fit.bad(self, src, dst)
        self.assertIn("DIFF transactional: booted @/.snapshots/3/snapshot", out)
        self.assertNotIn("read with its default", out)
        # 7 booted, its etc.syncpoint still there: the source's is compared as it was.
        dst = self.with_btrfs(self.tu((7, 290), (7, 290), hc=(7, 290)), 7, (275, 290), syncpoint=True)
        out = Fit.good(self, src, dst)
        self.assertNotIn("consumed", out)
        # A slot that lost its /etc.
        dst = self.with_btrfs(self.tu((7, 290), (7, 290), hc=(7, 290)), 7, (275, 290))
        dst["storage"]["btrfs"][0]["subvolumes"] = [v for v in dst["storage"]["btrfs"][0]["subvolumes"] if v["path"] != "<FS_TREE>/@/.snapshots/3/snapshot/etc"]
        out = Fit.bad(self, src, dst)
        self.assertIn('"@/.snapshots/3/snapshot/etc"', out)

    def test_no_update_pending(self):
        src = self.tu((5, 279), (5, 279), hc=(5, 279))
        out = Fit.good(self, src, self.tu((5, 281), (5, 281), hc=(5, 281)))
        self.assertIn("booted = default = the source's default @/.snapshots/5/snapshot, health-checker's", out)


class TransactionalBls(unittest.TestCase):
    """judge.py's BLS side of boot.transactional (aeon, A4): no
    health-check.state on either side (the BLS path keeps no IDs), the
    entries' boot counters taken off (the pending slot's `+3` is counted
    and blessed by the restored first boot), the kept LUKS2 headers of the
    source's dump, and the PCR list (fde-tools) held to the source's."""

    judge = Fit.judge

    @staticmethod
    def aeon(booted, default, entries, pcrs="4,5,7,9", headers=False):
        d = Transactional.tu(booted, default)
        t = d["boot"]["transactional"]
        t["loader_mode"] = "bls"
        if pcrs is not None:
            t["fde_seal_pcr_list"] = pcrs
        files = [{"path": "EFI/BOOT/BOOTX64.EFI", "size": 1042040}]
        files += [{"path": f"loader/entries/{e}", "size": 509} for e in entries]
        files += [{"path": "loader/loader.conf", "size": 31}]
        d["boot"]["esps"] = [{"device": "vda1", "mounted_at": "/boot/efi", "contents": {"files": files}}]
        d["boot"]["systemd_boot"] = {"entries": [{"file": e, "linux": "/aeon/7.3.3-1-default/linux-1fe2", "options": "rootflags=subvol=@/.snapshots/%s/snapshot" % e.split("-")[-1].split("+")[0].split(".")[0]} for e in entries]}
        if headers:
            d["storage"]["luks_headers"] = [{"device": "vda2", "file": "luks2-header-bc000000-0000-4000-8000-000000000007.img", "sha256": "ab" * 32, "written_to": "/run/bko-holt/luks-headers/luks2-header-bc000000-0000-4000-8000-000000000007.img"}]
        return d

    K = "aeon-7.3.3-1-default-"

    def src(self):
        # Booted 3 (rolled back), 7 pending and never booted (+3), 6 bad (+0-3).
        return self.aeon((3, 273), (7, 284), [f"{self.K}{n}.conf" for n in (1, 2, 3, 4, 5)] + [f"{self.K}6+0-3.conf", f"{self.K}7+3.conf"], headers=True)

    def test_counters_off_and_no_state(self):
        dst = self.aeon((7, 290), (7, 290), [f"{self.K}{n}.conf" for n in (1, 2, 3, 4, 5)] + [f"{self.K}6+0-3.conf", f"{self.K}7.conf"])
        out = Fit.good(self, self.src(), dst)
        self.assertIn("BLS entries compared without their boot counters (4 on the source, 2 restored)", out)
        self.assertIn("booted = default = the source's default @/.snapshots/7/snapshot (pending there", out)
        self.assertNotIn("health-check.state", out)
        # The kept headers are the source dump's own.
        self.assertIn("the LUKS2 headers the source's dump was asked to keep", out)

    def test_an_entry_gone_or_another_pcr_list(self):
        dst = self.aeon((7, 290), (7, 290), [f"{self.K}{n}.conf" for n in (1, 2, 3, 4, 5)] + [f"{self.K}7.conf"])
        out = Fit.bad(self, self.src(), dst)
        self.assertIn("DIFF boot.esps.0.contents.files.", out)
        dst = self.aeon((7, 290), (7, 290), [f"{self.K}{n}.conf" for n in (1, 2, 3, 4, 5)] + [f"{self.K}6+0-3.conf", f"{self.K}7.conf"], pcrs="7")
        out = Fit.bad(self, self.src(), dst)
        self.assertIn('DIFF transactional: fde_seal_pcr_list: "4,5,7,9" -> "7"', out)

    def test_a_bls_rollback_on_the_first_boot(self):
        """The pending slot failed health-checker on the restored first boot
        and systemd-boot booted 3 again: a DIFF, whatever the counters."""
        dst = self.aeon((3, 273), (3, 273), [f"{self.K}{n}.conf" for n in (1, 2, 3, 4, 5)] + [f"{self.K}6+0-3.conf", f"{self.K}7+0-3.conf"])
        out = Fit.bad(self, self.src(), dst)
        self.assertIn("DIFF transactional: booted @/.snapshots/3/snapshot, not the source's default @/.snapshots/7/snapshot", out)

    def test_grub_names_untouched(self):
        """bls_uncount itself; and a GRUB source gets no counter note."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("judge_under_test", JUDGE)
        judge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(judge)
        src = Transactional.tu((5, 279), (5, 279), hc=(5, 279))
        d = {"boot": {"esps": [{"contents": {"files": [{"path": "loader/entries/x+3.conf"}]}}]}}
        self.assertEqual(judge.bls_uncount(d), 1)
        self.assertEqual(d["boot"]["esps"][0]["contents"]["files"][0]["path"], "loader/entries/x.conf")
        self.assertEqual(judge.BLS_COUNTER.sub("", "aeon-1+2-1.conf"), "aeon-1.conf")
        out = Fit.good(self, src, Transactional.tu((5, 281), (5, 281), hc=(5, 281)))
        self.assertNotIn("boot counters", out)


class TrueNas(unittest.TestCase):
    """judge.py esp over TrueNAS's ESPs and judge.py bes over its boot-pool
    (docs/design/truenas.md, T6)."""

    def esp(self, **change):
        """truenas25's ESP as its installer leaves it: shim's files, GRUB and
        its copy in the fallback path.  CHANGE maps a path to its new
        sha256, or None to leave it out."""
        files = {"EFI/debian/shimx64.efi": "sh", "EFI/debian/grubx64.efi": "gr", "EFI/debian/mmx64.efi": "mm",
                 "EFI/debian/fbx64.efi": "fb", "EFI/debian/BOOTX64.CSV": "cs", "EFI/debian/grub.cfg": "cf", "EFI/boot/bootx64.efi": "gr"}
        rec = {".": meta("dir"), "EFI": meta("dir"), "EFI/debian": meta("dir"), "EFI/boot": meta("dir")}
        for p, h in files.items():
            h = change.get(p, h)
            if h is not None:
                rec[p] = meta("file", sha256=h)
        return rec

    def judge(self, ids, src, dst):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("src", "dst"):
                with open(os.path.join(tmp, f"{name}-uuids"), "w") as f:
                    f.write("".join(i + "\n" for i in ids))
                with open(os.path.join(tmp, f"{name}-kind"), "w") as f:
                    f.write("truenas\n")
            for who, recs in (("src", src), ("dst", dst)):
                for i, rec in recs.items():
                    with open(os.path.join(tmp, f"{who}-{i}.json"), "w") as f:
                        json.dump(rec, f)
            r = subprocess.run([sys.executable, "-I", JUDGE, "esp", tmp], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def test_the_same_esp_passes(self):
        rc, out = self.judge(["AD14-E6D9"], {"AD14-E6D9": self.esp()}, {"AD14-E6D9": self.esp()})
        self.assertEqual(rc, 0, out)
        self.assertIn("7 of its 7 files byte for byte, EFI/boot/bootx64.efi its EFI/debian/grubx64.efi", out)

    def test_a_rebuilt_grub_passes_with_its_copy(self):
        rc, out = self.judge(["A"], {"A": self.esp()}, {"A": self.esp(**{"EFI/debian/grubx64.efi": "g2", "EFI/boot/bootx64.efi": "g2"})})
        self.assertEqual(rc, 0, out)
        self.assertIn("grub-install rebuilt it in the boot stage", out)

    def test_the_fallback_must_be_grub(self):
        rc, out = self.judge(["A"], {"A": self.esp()}, {"A": self.esp(**{"EFI/debian/grubx64.efi": "g2"})})
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF ESP A: EFI/boot/bootx64.efi isn't EFI/debian/grubx64.efi", out)

    def test_a_missing_loader_counts(self):
        rc, out = self.judge(["A"], {"A": self.esp()}, {"A": self.esp(**{"EFI/debian/shimx64.efi": None})})
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF ESP A: missing: 1: EFI/debian/shimx64.efi", out)

    def test_two_restored_esps_must_be_equal(self):
        ids = ["A", "B"]
        rc, out = self.judge(ids, {i: self.esp() for i in ids}, {"A": self.esp(), "B": self.esp(**{"EFI/debian/grub.cfg": "c2"})})
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF ESPs A and B (restored): content: 1: EFI/debian/grub.cfg", out)

    def pool(self, restored=False, **props):
        """A dump of a boot-pool with two BEs, the older's data a clone (none
        restored), PROPS (name -> {prop: value}) changing local properties."""
        def ds(name, mp, local, origin=None):
            p = {k: {"value": v, "source": "local"} for k, v in dict(local, **props.get(name, {})).items() if v is not None}
            return {"name": name, "type": "filesystem", "mountpoint": mp, "canmount": "noauto" if "/ROOT/" in name and name.count("/") > 2 else "on",
                    "origin": None if restored else origin, "used": 7 if restored else 1, "properties": p}
        dss = [ds("boot-pool", "none", {"compression": "on"}), ds("boot-pool/ROOT", "none", {"canmount": "off"}), ds("boot-pool/grub", "legacy", {"mountpoint": "legacy"})]
        for v in ("25.10.7", "25.10.8"):
            dss.append(ds(f"boot-pool/ROOT/{v}", "legacy", {"readonly": "on", "truenas:kernel_version": f"6.12-{v}", "zectl:keep": "False"}))
            dss.append(ds(f"boot-pool/ROOT/{v}/data", "/data", {}, origin="boot-pool/ROOT/25.10.8/data@install" if v == "25.10.7" else None))
            dss.append(ds(f"boot-pool/ROOT/{v}/usr", "/usr", {"readonly": "on"}))
        return {"storage": {"zfs": {"tools": {"imported_pools": [{"name": "boot-pool", "properties": {"bootfs": "boot-pool/ROOT/25.10.8"}}], "datasets": dss}}}}

    def bes(self, src, dst):
        with tempfile.TemporaryDirectory() as tmp:
            for name, d in (("src.json", src), ("dst.json", dst)):
                with open(os.path.join(tmp, name), "w") as f:
                    json.dump(d, f)
            r = subprocess.run([sys.executable, "-I", JUDGE, "bes", os.path.join(tmp, "src.json"), os.path.join(tmp, "dst.json")], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def test_bes_pass_with_clones_flattened(self):
        rc, out = self.bes(self.pool(), self.pool(restored=True))
        self.assertEqual(rc, 0, out)
        self.assertIn("2 boot environments (25.10.7, 25.10.8), bootfs boot-pool/ROOT/25.10.8", out)
        self.assertIn("1 clones on the source, 0 restored as clones", out)

    def test_bes_hold_readonly_and_user_properties(self):
        for change in ({"boot-pool/ROOT/25.10.7/usr": {"readonly": None}}, {"boot-pool/ROOT/25.10.7": {"zectl:keep": "True"}}):
            rc, out = self.bes(self.pool(), self.pool(restored=True, **change))
            self.assertEqual(rc, 1, f"{change}: {out}")
            self.assertIn("DIFF boot-pool/ROOT/25.10.7", out)

    def test_bes_hold_bootfs_and_the_list(self):
        dst = self.pool(restored=True)
        dst["storage"]["zfs"]["tools"]["imported_pools"][0]["properties"]["bootfs"] = "boot-pool/ROOT/25.10.7"
        dst["storage"]["zfs"]["tools"]["datasets"] = [d for d in dst["storage"]["zfs"]["tools"]["datasets"] if d["name"] != "boot-pool/ROOT/25.10.7/data"]
        rc, out = self.bes(self.pool(), dst)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF bootfs: boot-pool/ROOT/25.10.8 -> boot-pool/ROOT/25.10.7", out)
        self.assertIn("DIFF boot-pool/ROOT/25.10.7/data: missing", out)


class ZfsBootMenu(unittest.TestCase):
    """judge.py fs and zbm over a ZFSBootMenu restore (void-zbm, Z7 of
    docs/design/zfsbootmenu.md): the ESP's images kept byte for byte, every
    boot environment there, the inactive one's /boot untouched, the pool's
    bootfs, compatibility and org.zfsbootmenu:* properties, and the
    encryption root's key settings."""

    IMG = "d4c2af93"
    K = ("6.18.63_1", "7.2.15_1")

    def rec(self, rebuilt=False, **change):
        """fsmeta.py's record of the guest: the default BE void at /, its
        clone void-6.18 at run/bko-be/void-6.18, the ESP at boot/efi.
        REBUILT: the default BE's initramfs files rebuilt (the boot
        stage's); CHANGE maps a path to its new sha256, or None to leave it
        out."""
        r = {"boot": meta("dir"), "boot/efi": meta("dir"), "boot/efi/EFI": meta("dir"), "boot/efi/EFI/ZBM": meta("dir"),
             "boot/efi/EFI/ZBM/VMLINUZ.EFI": meta("file", sha256=self.IMG), "boot/efi/EFI/ZBM/VMLINUZ-BACKUP.EFI": meta("file", sha256=self.IMG),
             "run": meta("dir"), "run/bko-be": meta("dir"), "run/bko-be/void-6.18": meta("dir"), "run/bko-be/void-6.18/boot": meta("dir"),
             f"run/bko-be/void-6.18/boot/vmlinuz-{self.K[0]}": meta("file", sha256="k0"),
             f"run/bko-be/void-6.18/boot/initramfs-{self.K[0]}.img": meta("file", sha256="i0")}
        for k in self.K:
            r[f"boot/vmlinuz-{k}"] = meta("file", sha256="k" + k[0])
            r[f"boot/initramfs-{k}.img"] = meta("file", sha256=("r" if rebuilt else "i") + k[0], mtime=2 if rebuilt else 1)
        for p, h in change.items():
            if h is None:
                r.pop(p, None)
            else:
                r[p] = meta("file", sha256=h)
        return r

    def fs(self, before, after):
        return Fs.judge(self, before, after)

    def test_fs_the_boot_stage_rebuilds_the_default_bes_initramfs_only(self):
        rc, out = self.fs(self.rec(), self.rec(rebuilt=True))
        self.assertEqual(rc, 0, out)
        self.assertIn("ZFSBootMenu's images kept: 2 held to the source's bytes: boot/efi/EFI/ZBM/VMLINUZ-BACKUP.EFI, boot/efi/EFI/ZBM/VMLINUZ.EFI", out)
        rc, out = self.fs(self.rec(), self.rec(rebuilt=True, **{f"run/bko-be/void-6.18/boot/initramfs-{self.K[0]}.img": "r6"}))
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF content: 1: run/bko-be/void-6.18/boot/initramfs-{self.K[0]}.img", out)

    def test_fs_no_esp_rule_excuses_the_images(self):
        rc, out = self.fs(self.rec(), self.rec(**{"boot/efi/EFI/ZBM/VMLINUZ.EFI": "ff"}))
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF content: 1: boot/efi/EFI/ZBM/VMLINUZ.EFI", out)
        # Elsewhere the ESP rule stands (a GRUB source's EFI/ files).
        src = {"boot/efi/EFI": meta("dir"), "boot/efi/EFI/void": meta("dir"), "boot/efi/EFI/void/grubx64.efi": meta("file")}
        rc, out = self.fs(src, dict(src, **{"boot/efi/EFI/void/grubx64.efi": meta("file", sha256="cd")}))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("ZFSBootMenu", out)

    def dump(self, restored=False, enc=False, **change):
        """The two dumps' fields judge.py zbm reads.  CHANGE: a field path
        (dots) to a new value."""
        def ds(name, mp, local, origin=None, **kw):
            d = {"name": name, "type": "filesystem", "mountpoint": mp, "canmount": "noauto" if mp == "/" else "on", "origin": origin,
                 "encryption": "aes-256-gcm" if enc else "off", "encryptionroot": "zroot" if enc else None,
                 "keyformat": "passphrase" if enc else "none", "keylocation": "none",
                 "properties": {k: {"value": v, "source": "local"} for k, v in local.items()}}
            d.update(kw)
            return d
        top = {"mountpoint": "none"}
        if enc:
            top["org.zfsbootmenu:keysource"] = "zroot/ROOT/void"
        dss = [ds("zroot", "none", top, keylocation="file:///etc/zfs/zroot.key" if enc else "none"),
               ds("zroot/ROOT", "none", {"mountpoint": "none", "org.zfsbootmenu:commandline": "loglevel=4"}),
               ds("zroot/ROOT/void", "/", {"mountpoint": "/", "canmount": "noauto"}),
               ds("zroot/ROOT/void-6.18", "/", {"mountpoint": "/", "canmount": "noauto", "org.zfsbootmenu:commandline": "%{parent} bko.be=void-6.18"},
                  origin=None if restored else "zroot/ROOT/void@pre-mainline"),
               ds("zroot/home", "/home", {"mountpoint": "/home"})]
        img = {"esp_mount": "/boot/efi", "path": "EFI/ZBM/VMLINUZ.EFI", "size": 64871424, "sha256": self.IMG, "kernel": "6.12.63_1", "cmdline": "quiet"}
        bes = [{"name": "void", "dataset": "zroot/ROOT/void", "pool": "zroot", "bootfs": True, "mounted": True, "origin": None,
                "commandline": "loglevel=4", "commandline_from": "zroot/ROOT", "locked": False, "read": True,
                "kernels": [f"vmlinuz-{k}" for k in self.K], "initramfs": [f"initramfs-{k}.img" for k in self.K]},
               {"name": "void-6.18", "dataset": "zroot/ROOT/void-6.18", "pool": "zroot", "bootfs": False, "mounted": False,
                "origin": None if restored else "zroot/ROOT/void@pre-mainline", "commandline": "%{parent} bko.be=void-6.18",
                "commandline_from": "zroot/ROOT/void-6.18", "locked": False, "read": True,
                "kernels": [f"vmlinuz-{self.K[0]}"], "initramfs": [f"initramfs-{self.K[0]}.img"]}]
        d = {"boot": {"loader": {"name": "zfsbootmenu"},
                      "zfsbootmenu": {"images": [img, dict(img, path="EFI/ZBM/VMLINUZ-BACKUP.EFI")], "pools": [{"name": "zroot", "bootfs": "zroot/ROOT/void"}], "bes": bes}},
             "storage": {"backup_sources": [{"path": "/run/bko-be/void-6.18", "from": None, "source": "zroot/ROOT/void-6.18"}],
                         "zfs": {"tools": {"imported_pools": [{"name": "zroot", "properties": {"bootfs": "zroot/ROOT/void", "compatibility": "openzfs-2.3-linux"}}],
                                           "datasets": dss}}}}
        for path, v in change.items():
            *head, last = path.split(".")
            at = d
            for h in head:
                at = at[int(h)] if isinstance(at, list) else at[h]
            if isinstance(at, list):
                at[int(last)] = v
            else:
                at[last] = v
        return d

    def zbm(self, src, dst, smeta, dmeta):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name, d in (("src.json", src), ("dst.json", dst), ("src-meta.json", smeta), ("dst-meta.json", dmeta)):
                paths.append(os.path.join(tmp, name))
                with open(paths[-1], "w") as f:
                    json.dump(d, f)
            r = subprocess.run([sys.executable, "-I", JUDGE, "zbm", *paths], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def test_zbm_passes_with_the_clone_flattened(self):
        for enc in (False, True):
            rc, out = self.zbm(self.dump(enc=enc), self.dump(restored=True, enc=enc), self.rec(), self.rec(rebuilt=True))
            self.assertEqual(rc, 0, out)
            self.assertIn("2 images byte for byte (records and dumps): EFI/ZBM/VMLINUZ-BACKUP.EFI, EFI/ZBM/VMLINUZ.EFI", out)
            self.assertIn("BE zroot/ROOT/void-6.18 (inactive; origin zroot/ROOT/void@pre-mainline -> -): newest kernel 6.18.63_1; 2 byte for byte under run/bko-be/void-6.18/boot", out)
            self.assertIn("BE zroot/ROOT/void (booted, bootfs; origin - -> -): newest kernel 7.2.15_1; 2 byte for byte under boot, 2 initramfs rebuilt", out)
            self.assertIn("pool zroot: bootfs zroot/ROOT/void, compatibility openzfs-2.3-linux", out)
            self.assertIn("zroot/ROOT/void-6.18 commandline=%{parent} bko.be=void-6.18 (local)", out)
            if enc:
                self.assertIn("zroot keysource=zroot/ROOT/void (local)", out)
                self.assertIn("encryption root zroot: aes-256-gcm, passphrase, keylocation file:///etc/zfs/zroot.key", out)

    def test_zbm_inactive_be_must_be_in_both_records_byte_for_byte(self):
        p = f"run/bko-be/void-6.18/boot/initramfs-{self.K[0]}.img"
        rc, out = self.zbm(self.dump(), self.dump(restored=True), self.rec(), self.rec(rebuilt=True, **{p: "r6"}))
        self.assertEqual(rc, 1, out)
        self.assertIn(f"DIFF BE zroot/ROOT/void-6.18 (inactive): {p}: its bytes differ", out)
        empty = {k: v for k, v in self.rec().items() if not k.startswith("run/bko-be/void-6.18/")}
        rc, out = self.zbm(self.dump(), self.dump(restored=True), empty, self.rec())
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF BE zroot/ROOT/void-6.18: the source record has nothing under run/bko-be/void-6.18 (vm-mount didn't mount it there)", out)

    def test_zbm_holds_images_bes_and_properties(self):
        cases = [
            ({"boot.zfsbootmenu.images.0.sha256": "ff"}, "DIFF image /boot/efi/EFI/ZBM/VMLINUZ.EFI: sha256 "),
            ({"boot.loader": {"name": "grub"}}, "DIFF the restored dump's loader is 'grub', not zfsbootmenu"),
            ({"boot.zfsbootmenu.bes.1.kernels": []}, "DIFF BE zroot/ROOT/void-6.18: kernels "),
            ({"boot.zfsbootmenu.bes.1.origin": "zroot/ROOT/void@x"}, "DIFF BE zroot/ROOT/void-6.18: origin "),
            ({"storage.zfs.tools.imported_pools.0.properties": {"bootfs": "zroot/ROOT/void-6.18", "compatibility": "openzfs-2.3-linux"}},
             "DIFF pool zroot: bootfs zroot/ROOT/void -> zroot/ROOT/void-6.18"),
            ({"storage.zfs.tools.imported_pools.0.properties": {"bootfs": "zroot/ROOT/void", "compatibility": "off"}},
             "DIFF pool zroot: compatibility openzfs-2.3-linux -> off"),
            ({"storage.zfs.tools.datasets.3.properties": {"mountpoint": {"value": "/", "source": "local"}}},
             "DIFF zroot/ROOT/void-6.18: org.zfsbootmenu:commandline ('%{parent} bko.be=void-6.18', 'local') -> ('loglevel=4', 'inherited from zroot/ROOT')"),
        ]
        for change, want in cases:
            rc, out = self.zbm(self.dump(), self.dump(restored=True, **change), self.rec(), self.rec(rebuilt=True))
            self.assertEqual(rc, 1, f"{change}: {out}")
            self.assertIn(want, out, change)

    def test_zbm_holds_the_key_settings(self):
        for change, want in (({"storage.zfs.tools.datasets.0.keylocation": "file:///mnt/share/passphrase"}, "DIFF zroot: keylocation file:///etc/zfs/zroot.key -> file:///mnt/share/passphrase"),
                             ({"storage.zfs.tools.datasets.0.properties": {"mountpoint": {"value": "none", "source": "local"}}}, "DIFF zroot: org.zfsbootmenu:keysource")):
            rc, out = self.zbm(self.dump(enc=True), self.dump(restored=True, enc=True, **change), self.rec(), self.rec(rebuilt=True))
            self.assertEqual(rc, 1, f"{change}: {out}")
            self.assertIn(want, out)

    def test_the_dump_form_lets_the_clones_origin_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = os.path.join(tmp, "a.json"), os.path.join(tmp, "b.json")
            for path, d in ((a, self.dump()), (b, self.dump(restored=True))):
                with open(path, "w") as f:
                    json.dump(d, f)
            r = subprocess.run([sys.executable, "-I", JUDGE, a, b], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ZFSBootMenu: a clone boot environment comes back as a plain dataset", r.stdout)


class ZfsBootMenuBoot(unittest.TestCase):
    """judge.py zbm-boot over one boot of a ZFSBootMenu BE (ht1.sh's
    zbm_judge_boot, docs/design/zfsbootmenu.md Z7): ZBM's passphrase prompt
    before it boots the BE, then the BE's initramfs's own load-key once,
    nothing typed, no retry, and the key loaded from the file in that
    initramfs.  The consoles are z7b's (void-zbm-enc) and z7a's (void-zbm),
    cut down."""

    DS, K = "zroot/ROOT/void-6.18", "6.18.63_1"
    KEY = "key zroot file:///etc/zfs/zroot.key available initramfs:yes"

    def console(self, enc=True, initrd=None):
        """The serial console of a menu boot of void-6.18; INITRD replaces
        what follows ZBM's line booting it."""
        zbm = ("\x1b[2J\x1b[01;01HBdsDxe: starting Boot0008 \"ZFSBootMenu\" from HD(1,GPT,5650DFA9,0x800,0x100000)/\\EFI\\ZBM\\VMLINUZ.EFI\r\n"
               "\x1b%G\x1b[?25l\x1b[10;32H\x1b[0;32m[RETURN]\x1b[0m boot now \x1b[13;23HBooting \x1b[0;36mzroot/ROOT/void\x1b[0m in \x1b[1;33m5\x1b[0m seconds\r\n")
        if enc:
            zbm += "[console-keys.py: sent '\\x1b', the rest once the menu is drawn]\nEnter passphrase for 'zroot': \r\n[console-keys.py: passphrase typed]\n"
        zbm += "[console-keys.py: typed '\\x1b|||||||\\x1b[B||\\r']\n\x1b[H\x1b[J\r\nBooting \x1b[1;33m/boot/vmlinuz-6.18.63_1\x1b[0m for \x1b[0;36mzroot/ROOT/void-6.18\x1b[0m ...\r\n\r\n"
        if initrd is None:
            initrd = "Encrypted ZFS password for zroot:  [1/5]:" if enc else ""
        return zbm + initrd + "- runit: $Id: c66c1e3b $: booting.\r\n=> Scanning for and importing ZFS pools...\r\n"

    def run_judge(self, con, state, prompts=1, ds=DS, kver=K):
        with tempfile.TemporaryDirectory() as tmp:
            log, st = os.path.join(tmp, "serial.log"), os.path.join(tmp, "state.txt")
            with open(log, "w") as f:
                f.write(con)
            with open(st, "w") as f:
                f.write(f"bko-be void-6.18\nuname {kver}\nroot {ds}\ncmdline root=zfs:{ds}\n{state}\n")
            r = subprocess.run([sys.executable, "-I", JUDGE, "zbm-boot", st, log, ds, kver, str(prompts)], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def test_zbm_boot_passes(self):
        rc, out = self.run_judge(self.console(), self.KEY)
        self.assertEqual(rc, 0, out)
        self.assertIn("0 differences; 1 ZFSBootMenu passphrase prompt(s); the initramfs's load-key once (zroot [1/5]), no retry; "
                      "zroot's key available from file:///etc/zfs/zroot.key, in initramfs-6.18.63_1.img", out)
        rc, out = self.run_judge(self.console(enc=False), "key -", prompts=0)
        self.assertEqual(rc, 0, out)
        self.assertIn("0 differences; 0 ZFSBootMenu passphrase prompt(s)", out)

    def test_zbm_boot_second_prompt(self):
        cases = [
            ("Encrypted ZFS password for zroot:  [1/5]:Key load error: Failed to open key material file: No such file or directory\r\n"
             "Encrypted ZFS password for zroot:  [2/5]:", "asked 2 time(s) (for zroot [1/5], for zroot [2/5]), not once at [1/5]"),
            ("Encrypted ZFS password for zroot:  [1/5]:Enter passphrase for 'zroot': ", "a passphrase prompt in the BE's initramfs: \"Enter passphrase for 'zroot':"),
            ("Encrypted ZFS password for zroot:  [1/5]:\n[console-keys.py: passphrase typed]\n", "console-keys.py typed after ZFSBootMenu booted the BE"),
            ("", "the BE's initramfs never ran its load-key"),
        ]
        for initrd, want in cases:
            rc, out = self.run_judge(self.console(initrd=initrd), self.KEY)
            self.assertEqual(rc, 1, f"{initrd!r}: {out}")
            self.assertIn(want, out, initrd)

    def test_zbm_boot_key_state(self):
        for state in ("key zroot file:///etc/zfs/zroot.key available initramfs:no", "key zroot file:///etc/zfs/zroot.key unavailable initramfs:yes",
                      "key zroot prompt available initramfs:no", "key -", ""):
            rc, out = self.run_judge(self.console(), state)
            self.assertEqual(rc, 1, f"{state!r}: {out}")
            self.assertIn("DIFF key " if state else "DIFF no key line", out, state)
        rc, out = self.run_judge(self.console(enc=False), self.KEY, prompts=0)
        self.assertEqual(rc, 1, out)
        self.assertIn("on a pool ZFSBootMenu never asked for", out)

    def test_zbm_boot_counts_and_lines(self):
        rc, out = self.run_judge(self.console(enc=False), "key -", prompts=1)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF 0 of ZFSBootMenu's passphrase prompts before it booted the BE, not 1", out)
        rc, out = self.run_judge(self.console(), "key -", prompts=0)
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF the BE's initramfs asked for a ZFS password on a pool without one", out)
        # void's line is not void-6.18's (a BE's name the prefix of another's).
        rc, out = self.run_judge(self.console(), self.KEY, ds="zroot/ROOT/void")
        self.assertEqual(rc, 1, out)
        self.assertIn('DIFF no "Booting /boot/vmlinuz-6.18.63_1 for zroot/ROOT/void" on the console', out)


class Qubes(unittest.TestCase):
    """Qubes OS (docs/design/qubes.md, Q7): judge.py qubes holds a thin
    snapshot's origin and new LV UUID, activation skip, a pool's used data
    within 5 % and each raw volume's checksum; judge.py qubes-check the
    restored boot's bko-qubes-check lines; main lets a pool with snapshots
    renumber its thin devices.  The field names are bko-holt Q2-Q4's."""

    @staticmethod
    def layout(ids="s", origin="vm-work-private-2-back", used=12 * GIB, skip=False, dev=3, tid=40):
        def thin(name, did, origin=None, flags=()):
            seg = {"start_extent": 0, "extent_count": 512, "type": "thin", "thin_pool": "vm-pool", "transaction_id": tid, "device_id": did}
            if origin:
                seg["origin"] = origin
            return {"name": name, "id": f"{ids}{name}", "flags": list(flags), "creation_host": "dom0" if ids == "s" else "rescuer",
                    "segments": [seg], "mapped_bytes": 100 * MIB if ids == "s" else 90 * MIB}
        lvs = [
            {"name": "vm-pool", "id": f"{ids}pool", "flags": [], "data_used_bytes": used, "metadata_used_bytes": 9 * MIB,
             "segments": [{"start_extent": 0, "extent_count": 8826, "type": "thin-pool", "metadata": "vm-pool_tmeta", "pool": "vm-pool_tdata",
                           "transaction_id": tid, "chunk_size": 128}]},
            thin("vm-work-private", dev, origin=origin),
            thin("vm-work-private-2-back", 2, origin="vm-work-private-1-back", flags=("ACTIVATION_SKIP",) if skip else ()),
            thin("vm-work-private-1-back", 1),
        ]
        lvm = {"volume_groups": [{"name": "qubes_dom0", "id": "vg1", "physical_volumes": [{"name": "pv0", "id": "pv1"}], "logical_volumes": lvs}]}
        dms = [{"name": "dm-5", "dm": {"dm_name": "qubes_dom0-vm--work--private", "lv_uuid": f"{ids}vm-work-private", "dm_uuid": f"LVM-vg1{ids}vm-work-private"},
                "raw_volume": {"image": "/.bko-holt/raw/qubes_dom0/vm-work-private.img", "why": "qubes.xml", "qube": "work", "volume": "private"}}]
        d = dump([disk("vda", 64 * GIB, [])], lvm=lvm, dm_devices=dms,
                 qubes={"pools": ["vm-pool"], "volumes": ["vm-work-private"]})
        d["boot"] = {"xen": {"version": "4.19.5", "hypervisor": "/boot/xen-4.19.5.gz", "cmdline_xen_default": "console=com1"}}
        return d

    def run_judge(self, *args, src=None, dst=None, sraw="qubes_dom0/vm-work-private aa\n", draw="qubes_dom0/vm-work-private aa\n"):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name, v in (("src.json", src), ("dst.json", dst)):
                if v is not None:
                    paths.append(os.path.join(tmp, name))
                    with open(paths[-1], "w") as f:
                        json.dump(v, f)
            raws = []
            if args and args[0] == "qubes":
                for name, v in (("src-raw.txt", sraw), ("dst-raw.txt", draw)):
                    raws.append(os.path.join(tmp, name))
                    with open(raws[-1], "w") as f:
                        f.write(v)
            r = subprocess.run([sys.executable, "-I", JUDGE, *args, *paths, *raws], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def test_main_lets_a_snapshot_pool_renumber(self):
        rc, out = self.run_judge(src=self.layout(), dst=self.layout(ids="d", used=12 * GIB + 50 * MIB, dev=7, tid=4))
        self.assertEqual(rc, 0, out)
        self.assertIn("a thin LV's device id in a pool with snapshots", out)
        self.assertIn("a thin pool's used data and metadata, within 5 % of the source's", out)
        self.assertIn("a thin LV's mapped blocks: a zero run the source had provisioned stays unprovisioned", out)
        self.assertNotIn("holds a filesystem the backup reads", out)

    @classmethod
    def with_root_pool(cls, ids="s", backed=True, **kw):
        """layout plus Qubes's root-pool: a second thin pool, no snapshots,
        its thin LV root holding dom0's ext4 /, a backup source if BACKED."""
        d = cls.layout(ids=ids, **kw)
        src = ids == "s"
        d["storage"]["lvm"]["volume_groups"][0]["logical_volumes"] += [
            {"name": "root-pool", "id": f"{ids}rootpool", "flags": [], "creation_host": "dom0" if src else "rescuer",
             "data_used_bytes": 7 * GIB if src else 6 * GIB + 900 * MIB, "metadata_used_bytes": 4 * MIB if src else 3 * MIB,
             "segments": [{"start_extent": 0, "extent_count": 5120, "type": "thin-pool", "metadata": "root-pool_tmeta", "pool": "root-pool_tdata",
                           "transaction_id": 1, "chunk_size": 128}]},
            {"name": "root", "id": f"{ids}root", "flags": [], "creation_host": "dom0" if src else "rescuer",
             "mapped_bytes": 7 * GIB if src else 6 * GIB + 880 * MIB,
             "segments": [{"start_extent": 0, "extent_count": 5120, "type": "thin", "thin_pool": "root-pool", "transaction_id": 1, "device_id": 1}]},
        ]
        d["storage"]["dm_devices"].append({"name": "dm-1", "dm": {"dm_name": "qubes_dom0-root", "vg": "qubes_dom0", "lv": "root",
                                                                  "lv_uuid": f"{ids}root", "dm_uuid": f"LVM-vg1{ids}root"},
                                           "content": {"type": "ext4", "usage": "filesystem", "uuid": "5f0c0e3a-1d2b-4c3d-9e4f-0a1b2c3d4e5f"}})
        if backed:
            d["storage"]["backup_sources"] = [{"path": "/", "device": "dm-1", "fstype": "ext4"}]
        return d

    def test_main_root_pool_of_a_filesystem(self):
        """root-pool (no snapshots) holds dom0's backed-up ext4: its counters
        take the filesystem excuse; vm-pool keeps the Qubes one."""
        rc, out = self.run_judge(src=self.with_root_pool(), dst=self.with_root_pool(ids="d", used=12 * GIB + 50 * MIB, dev=7, tid=4))
        self.assertEqual(rc, 0, out)
        self.assertIn("a thin LV's mapped blocks, where it holds a filesystem the backup reads", out)
        self.assertIn("a thin pool's used data and metadata, where its every thin LV holds a filesystem the backup reads", out)
        self.assertIn("a thin pool's used data and metadata, within 5 % of the source's", out)
        self.assertIn("a thin LV's mapped blocks: a zero run the source had provisioned stays unprovisioned", out)
        fs_lines = [l for l in out.splitlines() if "holds a filesystem the backup reads" in l]
        self.assertEqual(len(fs_lines), 2, out)
        self.assertTrue(all("logical_volumes.0." in l or "logical_volumes.1." in l for l in fs_lines), out)    # root, root-pool

    def test_main_root_pool_not_backed_up(self):
        rc, out = self.run_judge(src=self.with_root_pool(backed=False), dst=self.with_root_pool(ids="d", backed=False))
        self.assertEqual(rc, 1, out)
        self.assertNotIn("holds a filesystem the backup reads", out)
        self.assertEqual(out.count("DIFF "), 3, out)
        self.assertIn("DIFF storage.lvm.volume_groups.0.logical_volumes.0.mapped_bytes:", out)

    def test_main_still_holds_origins(self):
        rc, out = self.run_judge(src=self.layout(), dst=self.layout(ids="d", origin="vm-work-private-1-back"))
        self.assertEqual(rc, 1, out)
        self.assertIn("DIFF storage.lvm.volume_groups.0.logical_volumes.1.segments.0.origin", out)

    def test_qubes_passes(self):
        rc, out = self.run_judge("qubes", src=self.layout(), dst=self.layout(ids="d", used=12 * GIB + 50 * MIB, dev=7))
        self.assertEqual(rc, 0, out)
        self.assertIn("2 thin snapshots made anew", out)
        self.assertIn("1 raw volumes' checksums as the source's", out)

    def test_qubes_diffs(self):
        cases = (
            (dict(dst=self.layout(ids="d", origin="vm-work-private-1-back")), "origin"),
            (dict(dst=self.layout(ids="s")), "the source's LV UUID"),
            (dict(dst=self.layout(ids="d", used=14 * GIB)), "more than 5 % apart"),
            (dict(dst=self.layout(ids="d", skip=True)), "activation skip"),
            (dict(dst=self.layout(ids="d"), draw="qubes_dom0/vm-work-private bb\n"), "raw volume qubes_dom0/vm-work-private"),
        )
        for kw, want in cases:
            rc, out = self.run_judge("qubes", src=self.layout(), **kw)
            self.assertEqual(rc, 1, out)
            self.assertIn(want, out)

    def test_qubes_without_q2_fields(self):
        src = self.layout()
        del src["storage"]["lvm"]["volume_groups"][0]["logical_volumes"][0]["data_used_bytes"]
        del src["boot"]["xen"]
        rc, out = self.run_judge("qubes", src=src, dst=self.layout(ids="d"))
        self.assertEqual(rc, 1, out)
        self.assertIn("no data_used_bytes in the source dump", out)
        self.assertIn("no boot.xen in the source's dump", out)

    CHECK = """BKO-QUBES-CHECK BEGIN 2026-11-14T20:29:30Z boot x
BKO-QUBES-CHECK qubesd PASS answered after 0 s
BKO-QUBES-CHECK start-work PASS 13 s
BKO-QUBES-CHECK run-work PASS hostname=work seed=23b32456e48391da
BKO-QUBES-INFO lv vm-work-private|vm-pool|vm-work-private-1794688234-back|Vwi-a-tz--|2.00g
BKO-QUBES-CHECK lvm PASS thin=26 with-origin=14 work-private-chain=3
BKO-QUBES-CHECK DONE PASS
"""

    def check(self, dst):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = os.path.join(tmp, "s.log"), os.path.join(tmp, "d.log")
            for path, v in ((a, self.CHECK), (b, dst)):
                with open(path, "w") as f:
                    f.write(v)
            r = subprocess.run([sys.executable, "-I", JUDGE, "qubes-check", a, b], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def test_check_passes_with_other_times(self):
        rc, out = self.check(self.CHECK.replace("13 s", "40 s").replace("1794688234", "1794699999"))
        self.assertEqual(rc, 0, out)
        self.assertIn("lvm thin=26 with-origin=14 work-private-chain=3", out)

    def test_check_diffs(self):
        for dst, want in ((self.CHECK.replace("seed=23b32456e48391da", "seed=ffff"), "run-work"),
                          (self.CHECK.replace("DONE PASS", "DONE FAIL"), "DONE is FAIL"),
                          (self.CHECK.replace("start-work PASS", "start-work FAIL"), "start-work: FAIL"),
                          (self.CHECK.replace("with-origin=14", "with-origin=0"), "lvm:")):
            rc, out = self.check(dst)
            self.assertEqual(rc, 1, out)
            self.assertIn(want, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
