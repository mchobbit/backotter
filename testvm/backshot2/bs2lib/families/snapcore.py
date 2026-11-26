"""Ubuntu Core (uc24, uc24-fde; docs/design/ubuntu-core.md): the pc gadget's
GPT, which load makes again as the dump has it (holt/src/load/snapcore.rs):
every partition's GPT name, type, PARTUUID and size, only ubuntu-data
growing or shrinking under --fit-largest.  The source is generic Linux to
the rest (no initramfs is made, no loader installed: load's boot stage
regenerates nothing), so this is a trait, matched by the dump's
boot.snapcore.

- Quirk snapcore-gadget-sizes: the gadget fixes the size of every partition
  but ubuntu-data (mbr's BIOS Boot, ubuntu-seed, ubuntu-boot, ubuntu-save),
  snapd's install makes them so, and load refuses a layout where one of
  ubuntu-seed, -boot or -save has another size than the dump's
  boot.snapcore says ("only ubuntu-data may change size").  So a drift
  may resize ubuntu-data alone (a resize, or the room a gap's draw makes at
  the disk's end), as a real Ubuntu Core disk has drifted: a case that
  resizes another is the generator's bug, not a finding (BackShot2All
  bs2all-20261125-120413: 465 of uc24's tier 0 cases were load's refusal).
  The partitions are found by PARTUUID, which no edit changes, so renumbered
  or reordered ones are still found.
- Tier 0's dry runs run in the base snap (bs2-host snapcore_base()): the
  partitions hold no /usr.
- No content drift (families/linux.py choose()): no mounted filesystem
  holds /srv.
- Sweep: no --reformat.  snap-bootstrap's fstab names no device load could
  reformat (/ is the base snap's squashfs), and ubuntu-data's ext4 is the
  gadget's: load's boot stage, which rewrites nothing here, couldn't follow.
"""

from .. import dumpx
from .base import Quirk, Trait, no_reformat


def snapcore(dump):
    return (dump.get("boot") or {}).get("snapcore") or {}


def data_partuuid(dump):
    """ubuntu-data's PARTUUID (boot.snapcore's data role), or None."""
    p = next((p for p in snapcore(dump).get("partitions") or [] if p.get("role") == "data"), None)
    if p is None:
        return None
    return str(p.get("partuuid") or "").lower() or None


def fixed(dump):
    """[partition] of the Ubuntu Core disk whose size the gadget fixes: all but ubuntu-data."""
    dk = dumpx.disk(dump, snapcore(dump).get("disk"))
    data = data_partuuid(dump)
    if dk is None or data is None:
        return []
    return [p for p in dk.get("partitions") or [] if str(p.get("uuid") or "").lower() != data]


def check_sizes(source, drifted, edits):
    out = []
    dk = dumpx.disk(drifted, snapcore(source).get("disk"))
    if dk is None:
        return out
    by = {str(p.get("uuid") or "").lower(): p for p in dk.get("partitions") or [] if p.get("uuid")}
    for p in fixed(source):
        q = by.get(str(p.get("uuid") or "").lower())
        if q is None or q.get("sectors") == p.get("sectors"):
            continue
        out.append((3, f"Ubuntu Core: {p.get('partlabel') or p['name']} ({q['name']}) would be {q.get('sectors')} sectors, "
                       f"not the gadget's {p.get('sectors')}: only ubuntu-data may change size, and load refuses another"))
    return out


TRAITS = [Trait(
    name="snapcore",
    matches=lambda source, dump: bool(snapcore(dump)),
    quirks=[Quirk("snapcore-gadget-sizes",
                  "the pc gadget fixes every partition's size but ubuntu-data's, and load makes them as the dump has them",
                  check_sizes)],
    sweep=no_reformat,
)]
