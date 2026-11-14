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


if __name__ == "__main__":
    unittest.main(verbosity=2)
