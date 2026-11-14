"""`load --fit-largest`'s arithmetic, as judge.py works it out (its
fit_plan, the same numbers load uses), for the generator: what PCT a target
needs, and whether the data still fits once the largest partition shrinks.
"""

import importlib.util
import os

from . import dumpx, geometry, rules

_judge = None


def judge():
    """testvm/holttest1d/judge.py, as a module."""
    global _judge
    if _judge is None:
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, "..", "..", "holttest1d", "judge.py")
        spec = importlib.util.spec_from_file_location("bs2_judge", path)
        _judge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_judge)
    return _judge


def delta_of(drifted, targets):
    """The one target delta judge.py's --fit takes (every disk the same), or None."""
    ds = {targets[dk["name"]] - dk["size_bytes"] for dk in dumpx.disks(drifted)}
    return ds.pop() if len(ds) == 1 else None


def plan(drifted, targets, pct):
    delta = delta_of(drifted, targets)
    if delta is None:
        return None
    return judge().fit_plan(drifted, delta, pct)


def pct_needed(drifted, targets):
    """The smallest --fit-largest PCT load takes for these targets, or None
    when it can't fit them at all (a tie for largest, a stack it won't fit)."""
    p = plan(drifted, targets, None)
    if p is None or p["problems"]:
        return None
    need = 1
    for dd in p["disks"].values():
        if dd["amount"] < 0:
            if dd.get("holds") not in judge().FIT_FS + ("crypto_LUKS", "LVM2_member"):
                return None
            need = max(need, -(-(-dd["amount"]) * 100 // dd["bytes"]))
    return need if need <= 50 else None


def fits_after(drifted, targets, pct, planted=None):
    """Rule 1 after the fit: [(1, why)], empty when the shrunk largest
    partition (and what's on it) still holds its data."""
    p = plan(drifted, targets, pct)
    if p is None:
        return [(1, "targets of different deltas: judge.py's --fit takes one")]
    if p["problems"]:
        return [(1, f"the fit: {x}") for x in p["problems"]]
    out = []
    for i, dd in p["disks"].items():
        if dd["amount"] >= 0 or dd["name"] is None:
            continue
        dk = dumpx.disk(drifted, dd["disk"])
        part = next(x for x in dk["partitions"] if x["name"] == dd["name"])
        # The chain on it shrinks by the amount: find the filesystem at the bottom.
        devs = [part] + _down(drifted, part)
        for dev in devs:
            c = dev.get("content") or {}
            if c.get("type") in ("ext2", "ext3", "ext4", "xfs"):
                size = rules.fs_bytes(c) + dd["amount"]
                used = dumpx.used_bytes(drifted, dev) or 0
                extra = 0
                for m in dumpx.mount_of(drifted, dev):
                    if planted and m.get("target") in planted:
                        extra += planted[m["target"]][0]
                need = int(used * rules.MARGIN) + rules.overhead(c) + extra
                if need > size:
                    out.append((1, f"after --fit-largest, {dev['name']} has {size} bytes for {need}"))
            elif c.get("type") in judge().FIT_FS:
                # The rest (f2fs, btrfs, jfs, nilfs2) by load's own rule
                # (holt/src/load/fit.rs data_fits): a shrink needs it free,
                # and 1 GiB or 5% of what's used to spare.
                why = shrink_room(drifted, dev, c, -dd["amount"])
                if why:
                    out.append((1, why))
    return out


SPARE_BYTES = 1 << 30
SPARE_PCT = 5


def shrink_room(d, dev, c, shrink):
    """Why load refuses to shrink DEV's filesystem by SHRINK bytes (None: it doesn't)."""
    used = free = None
    for m in dumpx.mount_of(d, dev):
        if m.get("size_bytes") is not None and m.get("used_bytes") is not None:
            used, free = m["used_bytes"], max(0, m["size_bytes"] - m["used_bytes"])
            break
    if used is None:
        if None in (c.get("free_blocks"), c.get("block_size"), c.get("blocks")):
            return f"--fit-largest would shrink {dev['name']} ({c.get('type')}), whose use the dump doesn't say"
        used, free = (c["blocks"] - c["free_blocks"]) * c["block_size"], c["free_blocks"] * c["block_size"]
    need = shrink + max(SPARE_BYTES, used * SPARE_PCT // 100)
    if free < need:
        return f"after --fit-largest, {dev['name']} ({c.get('type')}) has {free} bytes free for a {shrink}-byte shrink and its spare"
    return None


def fs_blocks(drifted, targets, pct):
    """{device name: blocks} the fit should give each ext or XFS filesystem
    it remakes, from judge.py's plan of what each device gains or loses: the
    source's own slack is kept, so a filesystem loses what its device does,
    and gains it only where it filled the device (holt/src/load/fit.rs
    resize_fs, in whole blocks)."""
    pl = plan(drifted, targets, pct)
    if not pl or pl["problems"]:
        return {}
    st = dumpx.storage(drifted)
    devs = []
    for i, dd in pl["disks"].items():
        if dd.get("amount") and dd.get("big") is not None:
            devs.append((st["disks"][i]["partitions"][dd["big"]], dd["amount"]))
    for i, amount in pl["dm"].items():
        devs.append((st["dm_devices"][i], amount))
    out = {}
    for dev, amount in devs:
        c = dev.get("content") or {}
        bs = c.get("block_size")
        if c.get("type") not in ("ext2", "ext3", "ext4", "xfs") or not bs or not c.get("blocks"):
            continue
        size = c.get("size_bytes") or c["blocks"] * bs
        if amount < 0 or size == dev.get("size_bytes"):
            size += amount
        out[dev["name"]] = size // bs
    return out


def ext_inodes_past_groups(drifted, targets, pct):
    """fail-005's trigger: an ext filesystem the fit shrinks to fewer groups
    than its inodes fit in at 8 x block size a group (the most an inode
    bitmap block holds).  load's mke2fs keeps the source's inode count
    (-N) at the source's blocks per group (-g), so mke2fs cuts blocks per
    group until they fit, where resize2fs keeps it.  [why] for each."""
    out = []
    devs = dumpx.devices(drifted)
    for name, nb in fs_blocks(drifted, targets, pct).items():
        c = (devs.get(name) or {}).get("content") or {}
        if c.get("type") not in ("ext2", "ext3", "ext4") or not c.get("blocks_per_group") or nb >= c["blocks"]:
            continue
        try:
            g = geometry.ext4_resize2fs(c, nb)
        except geometry.Invalid:
            continue
        groups = g["inodes"] // c["inodes_per_group"]
        if c["inodes"] > groups * 8 * c["block_size"]:
            out.append(f"fail-005: {name}'s {c['inodes']} inodes in {groups} groups of at most {8 * c['block_size']}")
    return out


def _down(d, dev):
    """What sits on DEV, through LUKS and (for a PV) the LV the fit shrinks, which judge.py says."""
    out = []
    for k in dumpx.children(d, dev["name"]):
        out.append(k)
        out += _down(d, k)
    c = dev.get("content") or {}
    if c.get("type") == "LVM2_member":
        for v in dumpx.vgs(d):
            for x in v.get("logical_volumes") or []:
                kid = dumpx.lv_device(d, v["name"], x["name"])
                if kid and kid not in out:
                    out.append(kid)
    return out
