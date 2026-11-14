"""The three rules (backshot2.md, "The rules, as checks"), over a drifted
layout and the source it came from:

  1. the data fits: each filesystem's used bytes x 1.1 plus its metadata,
     and the content drift planted on it, fit its new size; its inodes
     likewise; every device fits what holds it, every partition its disk;
  2. the source's tools could make it: the values are in tables.py, and the
     models (geometry.py, at build time) took them; tier 0's dry runs do
     the rest;
  3. nothing needs editing: no identifier changed, and no partition the
     source names by number or path was renumbered or reordered.

After the three, the source's own rules: its family's and traits' quirks
(bs2lib/families; Alpine's mkinitfs rule is one).

check() returns [(RULE, WHY)]; empty is a valid case.  It takes the
source's HOOKS (families.hooks), made once per case by the generator, and
works them out from the source dump when not given.
"""

import re

from . import dumpx, edits as E, geometry, tables

MARGIN = 1.1


def fs_bytes(c):
    t = c.get("type")
    if t in ("ext2", "ext3", "ext4", "xfs"):
        return c["blocks"] * c["block_size"]
    if t == "vfat":
        return (c.get("total_sectors") or 0) * (c.get("bytes_per_sector") or 512)
    return c.get("size_bytes")


def overhead(c):
    t = c.get("type")
    if t in ("ext2", "ext3", "ext4"):
        # No journal where the features say so (a 4 MiB ext4, as
        # ubuntu26-zfsmirror's ZFS keystore, has none).
        journal = not c.get("features") or "has_journal" in c["features"]
        return geometry.ext4_overhead(c["blocks"], c["block_size"], c.get("inodes") or 0, c.get("inode_size") or 256, journal)
    if t == "xfs":
        return geometry.xfs_overhead(c["blocks"], c["block_size"], c.get("ag_count") or 4, c.get("log_blocks") or 0)
    return 0


def rule1(drifted, planted=None):
    """PLANTED: {mount point: (bytes, files)} that content drift adds."""
    out = []
    planted = planted or {}
    mounts = dumpx.storage(drifted).get("mounts") or []
    for dev, c in dumpx.filesystems(drifted):
        if c.get("type") == "swap":
            continue
        size = fs_bytes(c)
        if size is None:
            continue
        cap = dumpx.size_of(dev)
        if size > cap:
            out.append((1, f"{dev['name']}: its {c['type']} ({size} bytes) is bigger than the device ({cap})"))
        used = dumpx.used_bytes(drifted, dev) or 0
        extra = files = 0
        for m in mounts:
            if m.get("device") == dev["name"] and m.get("target") in planted:
                extra += planted[m["target"]][0]
                files += planted[m["target"]][1]
        need = int(used * MARGIN) + overhead(c) + extra
        if need > size:
            out.append((1, f"{dev['name']}: {used} bytes used (x{MARGIN}) + {overhead(c)} of metadata + {extra} planted don't fit {size}"))
        ui = dumpx.used_inodes(dev)
        if c.get("type") in ("ext2", "ext3", "ext4") and ui is not None:
            if int(ui * MARGIN) + files + 16 > c["inodes"]:
                out.append((1, f"{dev['name']}: {ui} inodes used (x{MARGIN}) + {files} planted don't fit {c['inodes']}"))
    for dk in dumpx.disks(drifted):
        ps = [p for p in dumpx.parts(dk) if not p.get("extended")]
        lo, hi = dumpx.first_usable(dk), dumpx.last_usable(dk)
        for i, p in enumerate(ps):
            if p["start_lba"] < lo or dumpx.end(p) - 1 > hi:
                out.append((1, f"{p['name']}: outside the disk's usable sectors {lo}-{hi}"))
            if i and p["start_lba"] < dumpx.end(ps[i - 1]):
                out.append((1, f"{p['name']} overlaps {ps[i - 1]['name']}"))
    for x in dumpx.storage(drifted).get("dm_devices") or []:
        for s in x.get("slaves") or []:
            under = dumpx.devices(drifted).get(s)
            if under is not None and (x.get("dm") or {}).get("target") == "crypt":
                if x["size_bytes"] > dumpx.size_of(under):
                    out.append((1, f"{x['name']}: bigger than {s}"))
    for v in dumpx.vgs(drifted):
        for pv in v.get("physical_volumes") or []:
            if pv["pe_start_bytes"] + pv["pe_count"] * v["extent_size_bytes"] > pv["dev_size_bytes"]:
                out.append((1, f"PV {pv['name']}: {pv['pe_count']} extents don't fit {pv['dev_size_bytes']} bytes"))
            dev = dumpx.devices(drifted).get(pv.get("device"))
            if dev is not None and pv["dev_size_bytes"] != dumpx.size_of(dev):
                out.append((1, f"PV {pv['name']}: the VG says {pv['dev_size_bytes']} bytes, {dev['name']} is {dumpx.size_of(dev)}"))
        for x in v.get("logical_volumes") or []:
            dev = dumpx.lv_device(drifted, v["name"], x["name"])
            if dev is not None and dev["size_bytes"] != x["size_bytes"]:
                out.append((1, f"LV {v['name']}/{x['name']}: {x['size_bytes']} bytes, its device {dev['size_bytes']}"))
        try:
            for x in v.get("logical_volumes") or []:
                E._check_pieces(v, x["name"], E.pieces_of(x) or [])
        except geometry.Invalid as e:
            out.append((1, f"VG {v['name']}: {e}"))
    return out


def _hooks(source, hooks):
    if hooks is None:
        from .families import hooks as of
        hooks = of(None, source)
    return hooks


def rule2(source, edits, hooks=None):
    """The edits' drawn values against the source's tables."""
    hooks = _hooks(source, hooks)
    out = []
    for e in edits:
        op = e.get("op")
        pairs = []
        if op == "ext4_geometry":
            pairs = [("e2fsprogs", k, e[k]) for k in ("block_size", "inode_size", "flex_bg_size") if k in e]
        elif op == "xfs_geometry":
            pairs = [("xfsprogs", k, e[k]) for k in ("inode_size", "sector_size", "ag_count") if k in e]
        elif op == "btrfs_geometry":
            pairs = [("btrfs-progs", "node_size", e["node_size"])]
        elif op == "luks_geometry":
            pairs = [("cryptsetup", "header_size", e["header_size"]), ("cryptsetup", "sector_size", e["sector_size"])]
        elif op in ("lv_split", "lv_resize"):
            pairs = [("lvm2", "segments_per_lv", len(E.linear_segments(e["segments"])))]
        elif op in hooks.edits and hasattr(hooks.edits[op], "rule2_pairs"):
            pairs = hooks.edits[op].rule2_pairs(e)
        for tool, field, value in pairs:
            why = tables.check(source, tool, field, value, hooks)
            if why:
                out.append((2, why))
    return out


# What names a layer: compared between source and drifted, which must agree.
IDENTITY = re.compile(r"(^|\.)(uuid|label|partlabel|disk_guid|disk_signature|id|vg_uuid|lv_uuid|luks_uuid|dm_name|dm_uuid|"
                      r"mapper_path|hash_seed|name|vg_name|vg|lv)$")


def _ids(d):
    """{(what, which): value} of every identifier in D, by stable keys (not list positions)."""
    out = {}
    for dk in dumpx.disks(d):
        t = dumpx.table(dk)
        out[("disk", dk["name"], "guid")] = t.get("disk_guid") or t.get("disk_signature")
        for p in dk.get("partitions") or []:
            # GPT partitions by their GUID; MBR's have none of their own (PARTUUID is
            # the signature and the number), so by number, which rule3 leaves out.
            key = ("part", p.get("uuid") if t.get("type") == "gpt" else f"{dk['name']}:{p['number']}")
            out[key + ("partlabel",)] = p.get("partlabel")
    for dev, c in [(x, x.get("content") or {}) for x in dumpx.devices(d).values()]:
        if c.get("uuid"):
            out[("content", c.get("type"), c["uuid"], "label")] = c.get("label")
            out[("content", c.get("type"), c["uuid"], "hash_seed")] = c.get("hash_seed")
    for x in dumpx.storage(d).get("dm_devices") or []:
        dm = x.get("dm") or {}
        out[("dm", dm.get("dm_uuid"), "name")] = dm.get("dm_name")
    for v in dumpx.vgs(d):
        out[("vg", v.get("id"))] = v.get("name")
        for pv in v.get("physical_volumes") or []:
            out[("pv", pv.get("id"))] = pv.get("name")
        for x in v.get("logical_volumes") or []:
            out[("lv", x.get("id"))] = x.get("name")
    return out


def _uuids(d):
    out = set()
    for x in dumpx.devices(d).values():
        c = x.get("content") or {}
        if c.get("uuid"):
            out.add((c.get("type"), c["uuid"]))
    return out


def rule3(source, drifted, edits, hooks=None):
    hooks = _hooks(source, hooks)
    out = []
    if _uuids(source) != _uuids(drifted):
        out.append((3, f"filesystem UUIDs changed: {sorted(_uuids(source) ^ _uuids(drifted))[:4]}"))
    a, b = _ids(source), _ids(drifted)
    for k in sorted(set(a) | set(b), key=str):
        if k[0] == "part" and isinstance(k[1], str) and ":" in k[1]:
            continue   # MBR partitions are keyed by number, which renumber moves on purpose
        if a.get(k) != b.get(k):
            out.append((3, f"identifier {k}: {a.get(k)!r} -> {b.get(k)!r}"))
    cur = source
    for e in edits:
        if e.get("op") in ("renumber", "swap_order"):
            dk = dumpx.disk(cur, e.get("disk"))
            nums = [e["from"]] if e["op"] == "renumber" else [e["a"], e["b"]]
            for n in nums:
                p = dumpx.part(dk, n)
                if p is None:
                    continue
                found = dumpx.names_by_number(cur, dk, p, hooks)
                if found:
                    out.append((3, f"{e['op']} {p['name']}: the source names it by number ({found[0]})"))
                if dumpx.boot_on_lv(cur) and e["op"] == "renumber":
                    ms = {m.get("target"): m.get("device") for m in dumpx.storage(cur).get("mounts") or []}
                    under = dumpx.holders_down(cur, ms.get("/boot") or ms.get("/") or "")
                    if p["name"] in under:
                        out.append((3, f"renumber {p['name']}: /boot is on an LV on it, and GRUB finds it by number"))
        cur = E.apply(cur, [e])
    return out


def check(source, drifted, edits, planted=None, hooks=None):
    hooks = _hooks(source, hooks)
    return (rule1(drifted, planted) + rule2(source, edits, hooks) + rule3(source, drifted, edits, hooks)
            + hooks.quirk_check(source, drifted, edits))
