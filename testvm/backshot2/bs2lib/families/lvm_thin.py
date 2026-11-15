"""LVM thin pools (vanilla3's vos-root/root; any source whose dump has a
thin-pool segment: only vanilla3 in today's corpus, though an LVM-thin
Proxmox install would match too).

load restores a volume group without its thin part by vgcfgrestore, then
makes each pool with `lvcreate --type thin-pool -L DATA
--poolmetadatasize META` (DATA and META from the pool's _tdata and _tmeta
LVs) and each thin LV with `lvcreate --type thin -V`, and lvcreate makes
the pool metadata spare (lvolN_pmspare) itself, sized for the largest
pool's metadata (holt/src/load/commands.rs thin_of and its lvcreate
lines; docs/design/vanillaos.md, "Thin pools in `load`").  lvcreate places
all of these itself, and judge.py lets their placement differ
(lvcreate_made); their sizes it compares.

- Quirk lvm-thin-pool-parts: no lv_split or lv_resize of a pool's data,
  metadata or spare LV (rule 2: lvcreate, not vgcfgrestore, makes them).
  A split is a placement lvcreate doesn't repeat; a shrink of the data or
  metadata LV is one LVM never makes (a thin pool doesn't reduce) and may
  not hold the pool's data, which the dump doesn't measure; a grown data
  LV leaves the pool LV's own size behind it in the drifted layout, and a
  grown metadata LV the spare (lvextend grows both), so the restored
  pool, its spare or both would differ from the drifted layout by the
  generator's doing.  A thin-aware grow (pool, _tdata and spare together)
  is a later edit.  The thin LVs and the pool LV have no linear segments,
  so lv_split and lv_resize never draw them; linear LVs beside the pool
  (vanilla3's init) keep both edits.
- --fit-largest: load refuses to shrink a group with a thin pool past its
  free extents; judge.py's fit plan (fitcalc) refuses it too (its largest
  LV is a tie of the pool, its data LV and the thin LVs), so the generator
  draws no such fit.
"""

from .. import dumpx
from .base import Quirk, Trait

EDITS = ("lv_split", "lv_resize")


def pool_parts(dump):
    """{VG name: {LV names lvcreate makes}}: each thin pool's data and
    metadata LVs and the group's hidden pool metadata spares."""
    out = {}
    for v in dumpx.vgs(dump):
        lvs = v.get("logical_volumes") or []
        parts = set()
        for x in lvs:
            for s in x.get("segments") or []:
                if s.get("type") == "thin-pool":
                    parts.update(n for n in (s.get("pool"), s.get("metadata")) if n)
        if parts:
            parts.update(x["name"] for x in lvs if str(x.get("name") or "").endswith("_pmspare") and "VISIBLE" not in (x.get("status") or []))
            out[v["name"]] = parts
    return out


def check_parts(source, drifted, edits):
    parts = pool_parts(source)
    return [(2, f"{e['op']} {e['vg']}/{e['lv']}: lvcreate makes a thin pool's data, metadata and spare LVs, sizing and placing them itself")
            for e in edits if e.get("op") in EDITS and e.get("lv") in parts.get(e.get("vg"), ())]


TRAITS = [Trait(
    name="lvm-thin",
    piece="V11",
    matches=lambda source, dump: bool(pool_parts(dump)),
    quirks=[Quirk("lvm-thin-pool-parts", "lvcreate makes a thin pool's data, metadata and spare LVs, sizing and placing them itself", check_parts)],
)]
