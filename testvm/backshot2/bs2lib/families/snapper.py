"""snapper (openSUSE Tumbleweed, Garuda, Omarchy) and btrfs snapshots it
keeps under .snapshots/N/snapshot.

load makes each subvolume in the source's ID order, a snapshot as a
snapshot of its origin when that is made first, and the shared snapshots
after the restore with the archiver's --clone-from, so they share their
extents as on the source (holt/src/load/commands.rs, format's btrfs
subvolumes; load/shared.rs).  A snapshot whose origin is gone (its
parent_uuid no listed subvolume's uuid) is made as a plain subvolume
("was a snapshot of a subvolume that's gone"): the restore fills it with a
full copy, so the filesystem holds more than the source's bytes_used.

- Quirk snapper-orphans: refuses any drift that shrinks a btrfs holding
  such a snapshot (rule 1: its used bytes don't count the copy).  The dump
  records a subvolume's exclusive bytes only where quotas are on
  (storage.btrfs[].qgroups, Tumbleweed's), so the copy's size isn't known
  in general: the shrink is refused rather than sized.  (load's --fit-largest
  shrink is load's own and outside the drift; not checked here.)
- No rule 3 texts: snapper's configs and grub-btrfs's menu name snapshots
  by subvolume path and the filesystem by UUID, both kept.
"""

import re

from .. import dumpx
from .base import Quirk, Trait, packages

SNAP = re.compile(r"(^|/)\.snapshots/\d+/snapshot$")


def has_snapper(source, dump):
    if "snapper" in packages(dump):
        return True
    return any(SNAP.search(str(s.get("path") or "")) for b in dumpx.storage(dump).get("btrfs") or [] for s in b.get("subvolumes") or [])


def orphaned(dump):
    """{btrfs uuid: [paths]} of snapshots whose origin isn't listed."""
    out = {}
    for b in dumpx.storage(dump).get("btrfs") or []:
        subs = b.get("subvolumes") or []
        have = {s.get("uuid") for s in subs if s.get("uuid")}
        lost = [s.get("path") for s in subs if s.get("parent_uuid") and s["parent_uuid"] not in have]
        if lost:
            out[b.get("uuid")] = lost
    return out


def sizes(d, uuids):
    """{btrfs uuid: bytes of the devices holding it} (by UUID: a renumber renames the device)."""
    out = {}
    for x in dumpx.devices(d).values():
        c = x.get("content") or {}
        if c.get("type") == "btrfs" and c.get("uuid") in uuids:
            out[c["uuid"]] = out.get(c["uuid"], 0) + dumpx.size_of(x)
    return out


def check_orphans(source, drifted, edits):
    lost = orphaned(source)
    if not lost:
        return []
    a, b = sizes(source, lost), sizes(drifted, lost)
    return [(1, f"btrfs {u}: it shrinks, and holds snapshots load makes as full copies ({', '.join(map(str, lost[u][:2]))})")
            for u in a if u in b and b[u] < a[u]]


TRAITS = [Trait(
    name="snapper",
    matches=has_snapper,
    quirks=[Quirk("snapper-orphans", "a snapshot whose origin is gone is restored as a full copy", check_orphans)],
)]
