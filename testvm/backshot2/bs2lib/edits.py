"""The edits, and applying a list of them to a dump (backshot2.md, "A case"
and "The generator").

An edit is a JSON object: its op, the values drawn for it, and "sets", every
number that follows from them (a filesystem's new block count, a swap's
pages, a dm device's size), keyed by the locator of the device it's on
(dumpx.locator: vda5, VG/LV, dm-0):

    {"op": "resize", "disk": "vda", "partition": 3, "by_sectors": -409600,
     "grow_fs": true,
     "sets": {"vda3": {"content": {"blocks": 3356416, "size_bytes": ..., ...}}}}

apply(dump, edits) is a pure function of the two: it moves partitions,
renames them, rewrites segments and LUKS header sizes from the edit's own
values, then writes its sets.  Nothing is worked out at apply time, so a
later generator can't change what an old case means.  build(dump, op,
params) is where the numbers are worked out (geometry.py's models), for the
generator and the shrinker; it raises Invalid for a draw the source's tools
couldn't have made.

sets[LOC] may hold:
  "dev"      fields of the device record itself (size_bytes, logical_sector_size)
  "content"  fields of what it holds (the filesystem, swap, LUKS or PV header)
  "pv"       fields of the VG's record of the PV on it (pe_count, dev_size_bytes)
  "swap"     fields of storage.swaps' entry for it (size_bytes)
"""

import re

from . import dumpx, geometry
from .geometry import Invalid, cdiv

MIB_SECTORS = 2048   # 1 MiB in 512-byte sectors


# ---- applying ----------------------------------------------------------------

def apply(dump, edits):
    """DUMP with EDITS applied in order: a new dump; DUMP is untouched."""
    d = dumpx.clone(dump)
    for e in edits:
        apply_one(d, e)
    return d


def apply_one(d, e):
    """Applies edit E to D in place."""
    op = OPS.get(e.get("op"))
    if op is None:
        raise ValueError(f"no edit {e.get('op')!r}")
    op.apply(d, e)
    apply_sets(d, e.get("sets") or {})


def _words(rec):
    if "size_bytes" in rec and "size" in rec and isinstance(rec["size_bytes"], int):
        rec["size"] = dumpx.human(rec["size_bytes"])


def apply_sets(d, sets):
    for loc, groups in sets.items():
        dev = dumpx.resolve(d, loc)
        if dev is None:
            raise ValueError(f"sets: no device {loc}")
        for k, v in (groups.get("dev") or {}).items():
            dev[k] = v
        _words(dev)
        if groups.get("content"):
            if dev.get("content") is None:
                raise ValueError(f"sets: {loc} holds nothing")
            dev["content"].update(groups["content"])
        if groups.get("pv"):
            for v in dumpx.vgs(d):
                for pv in v.get("physical_volumes") or []:
                    if pv.get("device") == dev["name"]:
                        pv.update(groups["pv"])
        if groups.get("swap"):
            for w in dumpx.storage(d).get("swaps") or []:
                if w.get("device") == dev["name"]:
                    w.update(groups["swap"])


def set_part(dk, p, start, sectors):
    """Moves P (on disk DK) to START, SECTORS long, with its derived fields."""
    ss = dumpx.sector(dk)
    p["start_lba"] = start
    p["sectors"] = sectors
    if "end_lba" in p:
        p["end_lba"] = start + sectors - 1
    p["start_bytes"] = start * ss
    p["size_bytes"] = sectors * ss
    _words(p)


def sort_parts(dk):
    dk["partitions"].sort(key=lambda p: p["number"])


def _disk_minor(dk, number):
    """The devno of partition NUMBER, where the kernel's rule fixes it: 16
    minors per disk for virtio and SCSI disks, partitions 1 to 15."""
    dev = str(dk.get("devno") or "")
    m = re.fullmatch(r"(\d+):(\d+)", dev)
    if not m or number > 15 or not re.fullmatch(r"(vd|sd|xvd)[a-z]+", dk["name"]):
        return None
    return f"{m.group(1)}:{int(m.group(2)) + number}"


def part_name(dk, number):
    n = dk["name"]
    return f"{n}p{number}" if n[-1].isdigit() else f"{n}{number}"


def _rename_strings(v, old, new):
    """V with every string equal to OLD or /dev/OLD renamed, recursively."""
    if isinstance(v, str):
        if v == old:
            return new
        if v == "/dev/" + old:
            return "/dev/" + new
        return v
    if isinstance(v, list):
        return [_rename_strings(x, old, new) for x in v]
    if isinstance(v, dict):
        return {k: _rename_strings(x, old, new) for k, x in v.items()}
    return v


def renumber_part(d, dk, p, to):
    """P becomes partition TO of DK: its name, path, devno, links and every
    reference to it in the dump.  On MBR its PARTUUID (signature-NN) too."""
    old, frm = p["name"], p["number"]
    new = part_name(dk, to)
    p["number"] = to
    links = []
    for l in p.get("links") or []:
        l = re.sub(rf"-part{frm}$", f"-part{to}", l)
        l = re.sub(rf"/by-partnum/{frm}$", f"/by-partnum/{to}", l)
        links.append(l)
    if dumpx.table(dk).get("type") == "dos" and isinstance(p.get("uuid"), str) and re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{2}", p["uuid"]):
        olduuid = p["uuid"]
        p["uuid"] = f"{olduuid[:8]}-{to:02x}"
        links = [l.replace(f"/by-partuuid/{olduuid}", f"/by-partuuid/{p['uuid']}") for l in links]
    p["links"] = sorted(links)
    dn = _disk_minor(dk, to)
    if dn:
        p["devno"] = dn
    st = d["storage"]
    for k in list(st):
        if k != "topology":
            st[k] = _rename_strings(st[k], old, new)
    d["boot"] = _rename_strings(d.get("boot"), old, new)
    topo = st.get("topology") or {}
    st["topology"] = _rename_topology(topo, old, new)
    sort_parts(dk)


def _rename_topology(t, old, new):
    out = {}
    for k, v in t.items():
        if k.startswith(old + " "):
            k = new + k[len(old):]
        out[k] = _rename_topology(v, old, new) if isinstance(v, dict) else v
    return out


# ---- the ops -----------------------------------------------------------------

class Op:
    name = ""
    # The edit's own numbers the shrinker moves halfway back (to zero, or to
    # source(d, e)'s value).
    numeric = ()

    def apply(self, d, e):
        raise NotImplementedError

    def build(self, d, params):
        """The whole edit for PARAMS on D, its sets worked out; Invalid if it can't be."""
        raise NotImplementedError

    def source(self, d, e, field):
        """FIELD's value in the source (what the shrinker moves towards)."""
        return 0


def _disk_part(d, e, key="partition"):
    dk = dumpx.disk(d, e.get("disk"))
    if dk is None:
        raise Invalid(f"no disk {e.get('disk')}")
    p = dumpx.part(dk, e[key])
    if p is None:
        raise Invalid(f"no partition {e[key]} on {dk['name']}")
    return dk, p


def movable(d, dk, p):
    """Why P can't be moved (None: it can).  Logical and extended
    partitions keep their EBR chain in phase 1; a partition a UEFI boot
    entry names by its start and number stays put."""
    if p.get("logical") or p.get("extended"):
        return "a logical or extended partition"
    if dumpx.table(dk).get("type") not in dumpx.TABLES:
        return f"a {dumpx.table(dk).get('type')} table"
    for e in ((d.get("boot") or {}).get("efi") or {}).get("entries") or []:
        for dp in e.get("device_path") or []:
            if p.get("uuid") and str(p["uuid"]).lower() in str(dp).lower():
                return "a UEFI boot entry names it by start and number"
    return None


def _moved(d, dk, moves):
    """The sets for partitions that moved (number -> new start): a FAT's
    hidden sectors are its start, as mkfs.fat records."""
    sets = {}
    for num, start in moves.items():
        p = dumpx.part(dk, num)
        c = p.get("content") or {}
        if c.get("type") == "vfat" and "hidden_sectors" in c:
            sets[p["name"]] = {"content": {"hidden_sectors": start}}
    return sets


def merge_sets(a, b):
    out = {k: {g: dict(x) for g, x in v.items()} for k, v in a.items()}
    for k, v in b.items():
        for g, x in v.items():
            out.setdefault(k, {}).setdefault(g, {}).update(x)
    return out


class GapBefore(Op):
    """gap_before P: P and everything after it on the disk move SECTORS on."""
    name = "gap_before"
    numeric = ("sectors",)

    def apply(self, d, e):
        dk, p = _disk_part(d, e)
        k = e["sectors"]
        for q in dk["partitions"]:
            if q["start_lba"] >= p["start_lba"] and not q.get("extended"):
                set_part(dk, q, q["start_lba"] + k, q["sectors"])

    def build(self, d, params):
        dk, p = _disk_part(d, params)
        k = params["sectors"]
        if k < 1:
            raise Invalid("no gap")
        after = [q for q in dk["partitions"] if q["start_lba"] >= p["start_lba"]]
        for q in after:
            why = movable(d, dk, q)
            if why:
                raise Invalid(f"{q['name']}: {why}")
        if dumpx.tail_free(dk) < k:
            raise Invalid("no room at the end of the disk")
        e = {"op": self.name, "disk": dk["name"], "partition": p["number"], "sectors": k}
        sets = _moved(d, dk, {q["number"]: q["start_lba"] + k for q in after})
        if sets:
            e["sets"] = sets
        return e


class Align(Op):
    """align P: P's start moves SECTORS on, off 1 MiB alignment; its end stays."""
    name = "align"
    numeric = ("sectors",)

    def apply(self, d, e):
        dk, p = _disk_part(d, e)
        set_part(dk, p, p["start_lba"] + e["sectors"], p["sectors"] - e["sectors"])

    def build(self, d, params):
        dk, p = _disk_part(d, params)
        k = params["sectors"]
        why = movable(d, dk, p)
        if why:
            raise Invalid(why)
        if k < 1 or k >= p["sectors"] // 2:
            raise Invalid("align by too much")
        if (p["start_lba"] + k) % MIB_SECTORS == 0:
            raise Invalid("still on 1 MiB")
        c = p.get("content") or {}
        if c.get("type") == "vfat":
            raise Invalid("a FAT shrinks only with fat_geometry (tier 3)")
        ss = dumpx.sector(dk)
        t = dumpx.clone(d)
        tdk, tp = _disk_part(t, params)
        set_part(tdk, tp, tp["start_lba"] + k, tp["sectors"] - k)
        sets = propagate(t, tp, tp["sectors"] * ss, grow_fs=False)
        e = {"op": self.name, "disk": dk["name"], "partition": p["number"], "sectors": k}
        if sets:
            e["sets"] = sets
        return e


class Renumber(Op):
    """renumber P to N: the same partition in another table entry."""
    name = "renumber"

    def apply(self, d, e):
        dk, p = _disk_part(d, e, "from")
        renumber_part(d, dk, p, e["to"])

    def build(self, d, params):
        dk, p = _disk_part(d, params, "from")
        to = params["to"]
        why = movable(d, dk, p)
        if why:
            raise Invalid(why)
        t = dumpx.table(dk).get("type")
        used = {q["number"] for q in dk["partitions"]}
        if to in used:
            raise Invalid(f"entry {to} is in use")
        if t == "dos" and not 1 <= to <= 4:
            raise Invalid("MBR primaries are 1 to 4")
        if t == "gpt" and not 1 <= to <= int(dumpx.table(dk).get("entry_count") or 128):
            raise Invalid("past the GPT's entries")
        return {"op": self.name, "disk": dk["name"], "from": p["number"], "to": to}


class Resize(Op):
    """resize P: BY_SECTORS more or fewer sectors; what's on it follows
    (a shrink shrinks the filesystem first, a grow grows it when GROW_FS)."""
    name = "resize"
    numeric = ("by_sectors",)

    def apply(self, d, e):
        dk, p = _disk_part(d, e)
        set_part(dk, p, p["start_lba"], p["sectors"] + e["by_sectors"])

    def build(self, d, params):
        dk, p = _disk_part(d, params)
        by = params["by_sectors"]
        if by == 0:
            raise Invalid("no change")
        if p.get("extended") or p.get("logical"):
            raise Invalid("a logical or extended partition")
        c = p.get("content") or {}
        if c.get("type") == "vfat":
            raise Invalid("a FAT resizes only with fat_geometry (tier 3)")
        if by > 0 and dumpx.space_after(dk, p) < by:
            raise Invalid("no room after it")
        if p["sectors"] + by < MIB_SECTORS:
            raise Invalid("too small")
        ss = dumpx.sector(dk)
        t = dumpx.clone(d)
        tdk, tp = _disk_part(t, params)
        set_part(tdk, tp, tp["start_lba"], tp["sectors"] + by)
        sets = propagate(t, tp, tp["sectors"] * ss, grow_fs=params.get("grow_fs", True), grow_pv=params.get("grow_pv", True))
        e = {"op": self.name, "disk": dk["name"], "partition": p["number"], "by_sectors": by}
        if "grow_fs" in params:
            e["grow_fs"] = params["grow_fs"]
        if sets:
            e["sets"] = sets
        return e


class SwapOrder(Op):
    """swap_order P Q: two partitions next to each other on the disk exchange
    places, keeping their numbers and the gap between them."""
    name = "swap_order"

    def apply(self, d, e):
        dk = dumpx.disk(d, e.get("disk"))
        a, b = dumpx.part(dk, e["a"]), dumpx.part(dk, e["b"])
        if a["start_lba"] > b["start_lba"]:
            a, b = b, a
        gap = b["start_lba"] - dumpx.end(a)
        start = a["start_lba"]
        set_part(dk, b, start, b["sectors"])
        set_part(dk, a, start + b["sectors"] + gap, a["sectors"])

    def build(self, d, params):
        dk = dumpx.disk(d, params.get("disk"))
        a, b = dumpx.part(dk, params["a"]), dumpx.part(dk, params["b"])
        if not a or not b or a is b:
            raise Invalid("two partitions")
        for q in (a, b):
            why = movable(d, dk, q)
            if why:
                raise Invalid(f"{q['name']}: {why}")
        order = [q for q in dumpx.parts(dk) if not q.get("extended")]
        ia, ib = order.index(a), order.index(b)
        if abs(ia - ib) != 1:
            raise Invalid("not next to each other")
        lo, hi = (a, b) if ia < ib else (b, a)
        gap = hi["start_lba"] - dumpx.end(lo)
        e = {"op": self.name, "disk": dk["name"], "a": params["a"], "b": params["b"]}
        sets = _moved(d, dk, {hi["number"]: lo["start_lba"], lo["number"]: lo["start_lba"] + hi["sectors"] + gap})
        if sets:
            e["sets"] = sets
        return e


# ---- LVM ---------------------------------------------------------------------

def lv_extents(lvrec):
    return sum(s["extent_count"] for s in lvrec.get("segments") or [])


def linear_segments(pieces):
    """[[PV, PE, COUNT], ...] as the dump's segment records; contiguous
    neighbours on one PV are merged, as LVM merges them."""
    merged = []
    for pv, pe, n in pieces:
        if merged and merged[-1][0] == pv and merged[-1][1] + merged[-1][2] == pe:
            merged[-1][2] += n
        else:
            merged.append([pv, pe, n])
    out, se = [], 0
    for pv, pe, n in merged:
        out.append({"start_extent": se, "extent_count": n, "type": "striped", "stripe_count": 1, "stripes": [pv, pe]})
        se += n
    return out


def pieces_of(lvrec):
    """An LV's segments as [[PV, PE, COUNT], ...]; None when one isn't linear."""
    out = []
    for s in lvrec.get("segments") or []:
        if s.get("type") != "striped" or s.get("stripe_count") != 1 or len(s.get("stripes") or []) != 2:
            return None
        out.append([s["stripes"][0], s["stripes"][1], s["extent_count"]])
    return out


def used_extents(v, skip=None):
    """{PV: set of extents the VG's LVs use}, leaving out LV SKIP."""
    used = {}
    for x in v.get("logical_volumes") or []:
        if x.get("name") == skip:
            continue
        for s in x.get("segments") or []:
            st = s.get("stripes") or []
            n = s.get("extent_count", 0) // max(1, s.get("stripe_count", 1))
            for i in range(0, len(st), 2):
                used.setdefault(st[i], set()).update(range(st[i + 1], st[i + 1] + n))
    return used


def free_runs(v, skip=None):
    """[(PV, PE, COUNT)]: the VG's free extents, as runs, with LV SKIP's counted free."""
    used = used_extents(v, skip)
    out = []
    for pv in v.get("physical_volumes") or []:
        u, n, run = used.get(pv["name"], set()), pv.get("pe_count", 0), None
        for i in range(n):
            if i in u:
                if run:
                    out.append(tuple(run))
                    run = None
            elif run:
                run[2] += 1
            else:
                run = [pv["name"], i, 1]
        if run:
            out.append(tuple(run))
    return out


def _check_pieces(v, lvname, pieces):
    used = used_extents(v, lvname)
    pes = {pv["name"]: pv.get("pe_count", 0) for pv in v.get("physical_volumes") or []}
    seen = {}
    for pv, pe, n in pieces:
        if pv not in pes or n < 1 or pe < 0 or pe + n > pes[pv]:
            raise Invalid(f"segment {pv}:{pe}+{n} outside the PV")
        r = set(range(pe, pe + n))
        if r & used.get(pv, set()) or r & seen.get(pv, set()):
            raise Invalid(f"segment {pv}:{pe}+{n} overlaps")
        seen.setdefault(pv, set()).update(r)


class LvSegments(Op):
    """lv_split and lv_resize: the LV's whole new list of linear segments,
    [[PV, PE, COUNT], ...]; its size follows, and lv_resize's sets carry
    what that does to what's on it."""
    numeric = ()

    def apply(self, d, e):
        v = dumpx.vg(d, e["vg"])
        x = dumpx.lv(v, e["lv"])
        x["segments"] = linear_segments(e["segments"])
        x["size_bytes"] = lv_extents(x) * v["extent_size_bytes"]

    def _common(self, d, params):
        v = dumpx.vg(d, params["vg"])
        if not v:
            raise Invalid(f"no VG {params['vg']}")
        x = dumpx.lv(v, params["lv"])
        if not x or pieces_of(x) is None:
            raise Invalid(f"{params['vg']}/{params['lv']}: not a linear LV")
        _check_pieces(v, params["lv"], params["segments"])
        return v, x


class LvSplit(LvSegments):
    name = "lv_split"

    def build(self, d, params):
        v, x = self._common(d, params)
        if sum(p[2] for p in params["segments"]) != lv_extents(x):
            raise Invalid("a split keeps the LV's size")
        if len(linear_segments(params["segments"])) < 2:
            raise Invalid("one piece")
        return {"op": self.name, "vg": params["vg"], "lv": params["lv"], "segments": [list(p) for p in params["segments"]]}


class LvResize(LvSegments):
    name = "lv_resize"
    numeric = ("by_extents",)

    def build(self, d, params):
        v, x = self._common(d, params)
        total = sum(p[2] for p in params["segments"])
        by = total - lv_extents(x)
        if by == 0:
            raise Invalid("no change")
        t = dumpx.clone(d)
        tv = dumpx.vg(t, params["vg"])
        tx = dumpx.lv(tv, params["lv"])
        tx["segments"] = linear_segments(params["segments"])
        new = total * v["extent_size_bytes"]
        dev = dumpx.lv_device(t, params["vg"], params["lv"])
        if dev is None:
            raise Invalid("the LV has no device in the dump")
        sets = {dumpx.locator(t, dev): {"dev": {"size_bytes": new}}}
        dev["size_bytes"] = new
        sets = merge_sets(sets, propagate(t, dev, new, grow_fs=params.get("grow_fs", True)))
        e = {"op": self.name, "vg": params["vg"], "lv": params["lv"], "by_extents": by,
             "segments": [list(p) for p in params["segments"]], "sets": sets}
        if "grow_fs" in params:
            e["grow_fs"] = params["grow_fs"]
        return e


def resize_lv_pieces(v, x, by, rng_pick=None):
    """X's pieces BY extents longer (from the VG's free runs, first fit, or
    RNG_PICK(runs) -> run) or shorter (from its end)."""
    pieces = [list(p) for p in pieces_of(x)]
    if by < 0:
        left = -by
        while left and pieces:
            if pieces[-1][2] > left:
                pieces[-1][2] -= left
                left = 0
            else:
                left -= pieces[-1][2]
                pieces.pop()
        if not pieces:
            raise Invalid("shrunk to nothing")
        return pieces
    runs = free_runs(v, x["name"])
    used_by_x = {(p[0], e) for p in pieces for e in range(p[1], p[1] + p[2])}
    runs = [r for r in runs if (r[0], r[1]) not in used_by_x]
    left = by
    while left:
        cand = [r for r in runs if r[2] > 0 and not any((r[0], e) in used_by_x for e in range(r[1], r[1] + r[2]))]
        if not cand:
            raise Invalid("not enough free extents")
        r = rng_pick(cand) if rng_pick else cand[0]
        n = min(left, r[2])
        pieces.append([r[0], r[1], n])
        used_by_x.update((r[0], e) for e in range(r[1], r[1] + n))
        runs.remove(r)
        if r[2] > n:
            runs.append((r[0], r[1] + n, r[2] - n))
        left -= n
    return pieces


# ---- LUKS --------------------------------------------------------------------

def luks_fields(c):
    seg = (c.get("segments") or [{}])[0]
    return {"header_size": int(c.get("header_size") or 16384), "keyslots_size": int(c.get("keyslots_size") or 0),
            "offset": int(seg.get("offset") or 0), "sector_size": int(seg.get("sector_size") or 512)}


class LuksGeometry(Op):
    """luks_geometry DEV: another LUKS2 metadata size, keyslots area, data
    offset or sector size; the payload's size, and so what's on it, follow."""
    name = "luks_geometry"
    numeric = ("offset", "keyslots_size")

    def apply(self, d, e):
        dev = dumpx.resolve(d, e["dev"])
        c = dev["content"]
        c["header_size"] = e["header_size"]
        c["json_size"] = str(e["header_size"] - 4096)
        c["keyslots_size"] = str(e["keyslots_size"])
        c["segments"][0]["offset"] = str(e["offset"])
        c["segments"][0]["sector_size"] = e["sector_size"]

    def source(self, d, e, field):
        dev = dumpx.resolve(d, e["dev"])
        return luks_fields(dev["content"])[field]

    def build(self, d, params):
        dev = dumpx.resolve(d, params["dev"])
        c = (dev or {}).get("content") or {}
        if c.get("type") != "crypto_LUKS" or str(c.get("version")) != "2":
            raise Invalid("not LUKS2")
        kids = dumpx.children(d, dev["name"])
        if len(kids) != 1:
            raise Invalid("the LUKS device isn't open once")
        keyslots = len(c.get("keyslots") or [])
        key_bits = ((c.get("keyslots") or [{}])[0]).get("key_bits") or 512
        size = dumpx.size_of(dev)
        g = geometry.luks2(size, params["header_size"], params["keyslots_size"], params["offset"], params["sector_size"], keyslots, key_bits)
        if luks_fields(c) == {k: params[k] for k in ("header_size", "keyslots_size", "offset", "sector_size")}:
            raise Invalid("no change")
        t = dumpx.clone(d)
        tdev = dumpx.resolve(t, dev["name"])
        tdev["content"]["segments"][0]["offset"] = str(params["offset"])
        tdev["content"]["segments"][0]["sector_size"] = params["sector_size"]
        below = dev.get("logical_sector_size") or dumpx.sector(dumpx.disk_of_part(d, dev["name"]) or {"size_bytes": 0})
        ss = max(params["sector_size"], below)
        sets = propagate(t, tdev, size, grow_fs=params.get("grow_fs", True), sector=ss)
        return {"op": self.name, "dev": dev["name"], "header_size": params["header_size"],
                "keyslots_size": params["keyslots_size"], "offset": params["offset"],
                "sector_size": params["sector_size"], "sets": sets}


# ---- filesystem geometry -----------------------------------------------------

def _fs(d, loc, types):
    dev = dumpx.resolve(d, loc)
    c = (dev or {}).get("content") or {}
    if c.get("type") not in types:
        raise Invalid(f"{loc}: not {'/'.join(types)}")
    return dev, c


class Ext4Geometry(Op):
    """ext4_geometry FS: block size, inode size, inodes per group (so the
    inode count), flex group size, reserved GDT blocks; the filesystem keeps
    its byte size as near as whole blocks allow."""
    name = "ext4_geometry"
    numeric = ()

    def apply(self, d, e):
        pass   # all in sets

    def build(self, d, params):
        dev, c = _fs(d, params["fs"], ("ext2", "ext3", "ext4"))
        bs = params.get("block_size", c["block_size"])
        isz = params.get("inode_size", c["inode_size"])
        fsbytes = c["blocks"] * c["block_size"]
        blocks = fsbytes // bs
        ipg = params.get("inodes_per_group", c["inodes_per_group"])
        flex = params.get("flex_bg_size", c.get("flex_bg_size"))
        rsv = params.get("reserved_gdt_blocks", c.get("reserved_gdt_blocks", 0))
        rb = c.get("reserved_blocks")
        if rb is not None:
            rb = rb * c["block_size"] // bs
        sector = dev.get("logical_sector_size") or 512
        if bs < sector:
            raise Invalid(f"{bs}-byte blocks on {sector}-byte sectors")
        g = geometry.ext4(blocks, bs, ipg, isz, rsv, c.get("features") or [], c.get("descriptor_size") or 64, flex, rb)
        changed = {k: v for k, v in g.items() if c.get(k) != v}
        if not changed:
            raise Invalid("no change")
        e = {"op": self.name, "fs": dumpx.locator(d, dev)}
        for k in ("block_size", "inode_size", "inodes_per_group", "flex_bg_size", "reserved_gdt_blocks"):
            if k in params:
                e[k] = params[k]
        e["sets"] = {dumpx.locator(d, dev): {"content": g}}
        return e


class XfsGeometry(Op):
    """xfs_geometry FS: AG count (so AG size), log size, inode size, sector size."""
    name = "xfs_geometry"
    numeric = ("log_blocks",)

    def apply(self, d, e):
        pass

    def source(self, d, e, field):
        dev = dumpx.resolve(d, e["fs"])
        return dev["content"][field]

    def build(self, d, params):
        dev, c = _fs(d, params["fs"], ("xfs",))
        blocks, bs = c["blocks"], c["block_size"]
        agc = params.get("ag_count")
        agb = cdiv(blocks, agc) if agc else c["ag_blocks"]
        lb = params.get("log_blocks", c["log_blocks"])
        isz = params.get("inode_size", c["inode_size"])
        ss = params.get("sector_size", c["sector_size"])
        sector = dev.get("logical_sector_size") or 512
        if ss < sector:
            raise Invalid(f"{ss}-byte XFS sectors on a {sector}-byte device")
        g = geometry.xfs(blocks, bs, agb, lb, isz, ss, c.get("features") or [])
        if all(c.get(k) == v for k, v in g.items()):
            raise Invalid("no change")
        e = {"op": self.name, "fs": dumpx.locator(d, dev)}
        for k in ("ag_count", "log_blocks", "inode_size", "sector_size"):
            if k in params:
                e[k] = params[k]
        e["sets"] = {dumpx.locator(d, dev): {"content": g}}
        return e


class BtrfsGeometry(Op):
    name = "btrfs_geometry"

    def apply(self, d, e):
        pass

    def build(self, d, params):
        dev, c = _fs(d, params["fs"], ("btrfs",))
        g = geometry.btrfs(params["node_size"], c.get("sector_size") or 4096, c.get("features") or [])
        if c.get("node_size") == g["node_size"]:
            raise Invalid("no change")
        return {"op": self.name, "fs": dumpx.locator(d, dev), "node_size": params["node_size"],
                "sets": {dumpx.locator(d, dev): {"content": g}}}


class FatGeometry(Op):
    """fat_geometry: cluster size and FAT type.  The ESP's, so tier 3's
    (backshot2.md): the format has it, milestone 1's generator draws none."""
    name = "fat_geometry"

    def apply(self, d, e):
        pass

    def build(self, d, params):
        raise Invalid("fat_geometry is tier 3's, not drawn in milestone 1")


class TargetSize(Op):
    """target_size DISK: the target disk is SIZE_BYTES; the layout itself is unchanged."""
    name = "target_size"
    numeric = ("by_bytes",)

    def apply(self, d, e):
        pass

    def build(self, d, params):
        dk = dumpx.disk(d, params.get("disk"))
        by = params["by_bytes"]
        ss = dumpx.sector(dk)
        if by == 0 or by % ss:
            raise Invalid("not a whole number of sectors")
        return {"op": self.name, "disk": dk["name"], "by_bytes": by}


OPS = {o.name: o for o in (GapBefore(), Align(), Renumber(), Resize(), SwapOrder(), LvSplit(), LvResize(),
                           LuksGeometry(), Ext4Geometry(), XfsGeometry(), BtrfsGeometry(), FatGeometry(), TargetSize())}


def build(d, op, params):
    return OPS[op].build(d, params)


# ---- what a resize does to what's on a device ---------------------------------

def propagate(d, dev, new_bytes, grow_fs=True, grow_pv=True, sector=None):
    """The sets for what DEV holds once DEV is NEW_BYTES (DEV's own record
    already says so in D), and, with SECTOR, once its logical sectors are
    SECTOR bytes.  Invalid when what's on it can't follow."""
    c = dev.get("content") or {}
    t = c.get("type")
    loc = dumpx.locator(d, dev)
    sets = {}
    if sector and (dev.get("logical_sector_size") or 512) != sector and dev.get("start_lba") is None:
        dev["logical_sector_size"] = sector
        dev["physical_sector_size"] = max(sector, dev.get("physical_sector_size") or sector)
        sets[loc] = {"dev": {"logical_sector_size": sector, "physical_sector_size": dev["physical_sector_size"]}}
    if t is None:
        return sets
    if t == "swap":
        if new_bytes is None:
            return sets
        g = geometry.swap(new_bytes, c.get("page_size") or 4096)
        return merge_sets(sets, {loc: {"content": g, "swap": {"size_bytes": g["last_page"] * (c.get("page_size") or 4096)}}})
    if t == "crypto_LUKS":
        f = luks_fields(c)
        for k in dumpx.children(d, dev["name"]):
            if new_bytes is None:
                sets = merge_sets(sets, propagate(d, k, None, grow_fs, grow_pv, sector))
                continue
            payload = new_bytes - f["offset"]
            if payload <= 0 or payload % f["sector_size"]:
                raise Invalid("LUKS payload no longer fits its sectors")
            k["size_bytes"] = payload
            sets = merge_sets(sets, {k["name"]: {"dev": {"size_bytes": payload}}})
            sets = merge_sets(sets, propagate(d, k, payload, grow_fs, grow_pv, sector))
        return sets
    if t == "LVM2_member":
        rec = vgr = None
        for v in dumpx.vgs(d):
            for pv in v.get("physical_volumes") or []:
                if pv.get("device") == dev["name"]:
                    rec, vgr = pv, v
        if rec is None:
            raise Invalid("a PV the VG doesn't list")
        if new_bytes is not None:
            ext = vgr["extent_size_bytes"]
            fit = geometry.pv_extents(new_bytes, rec["pe_start_bytes"], ext)
            n = rec["pe_count"]
            if fit < n:
                used = used_extents(vgr).get(rec["name"], set())
                if used and max(used) >= fit:
                    raise Invalid("the PV's used extents don't fit")
                n = fit
            elif grow_pv:
                n = fit
            sets = merge_sets(sets, {loc: {"content": {"device_size": new_bytes}, "pv": {"dev_size_bytes": new_bytes, "pe_count": n}}})
        if sector:
            for x in vgr.get("logical_volumes") or []:
                kid = dumpx.lv_device(d, vgr["name"], x["name"])
                if kid is not None and rec["name"] in [s["stripes"][0] for s in x.get("segments") or [] if s.get("stripes")]:
                    sets = merge_sets(sets, propagate(d, kid, None, grow_fs, grow_pv, sector))
        return sets
    if t in ("ext2", "ext3", "ext4"):
        if sector and c["block_size"] < sector:
            raise Invalid(f"{c['block_size']}-byte blocks on {sector}-byte sectors")
        if new_bytes is None:
            return sets
        cur = c["blocks"] * c["block_size"]
        if new_bytes < cur or (grow_fs and new_bytes // c["block_size"] != c["blocks"]):
            sets = merge_sets(sets, {loc: {"content": geometry.ext4_resized(c, new_bytes)}})
        return sets
    if t == "xfs":
        if sector and c["sector_size"] < sector:
            raise Invalid(f"{c['sector_size']}-byte XFS sectors on {sector}-byte sectors")
        if new_bytes is None:
            return sets
        bs = c["block_size"]
        cur = c["blocks"] * bs
        if new_bytes < cur or (grow_fs and new_bytes // bs != c["blocks"]):
            g = geometry.xfs(new_bytes // bs, bs, c["ag_blocks"], c["log_blocks"], c["inode_size"], c["sector_size"], c.get("features") or [])
            sets = merge_sets(sets, {loc: {"content": g}})
        return sets
    if new_bytes is None:
        return sets
    raise Invalid(f"{loc}: {t} can't follow a resize in phase 1")
