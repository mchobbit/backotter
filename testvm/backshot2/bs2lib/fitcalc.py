"""`load --fit-largest`'s arithmetic, as judge.py works it out (its
fit_plan, the same numbers load uses), for the generator: what PCT a target
needs, and whether the data still fits once the largest partition shrinks.
"""

import importlib.util
import os

from . import dumpx, rules

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
