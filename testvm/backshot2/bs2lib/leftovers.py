"""The leftovers set (backshot2.md, "Leftovers"): restores in a fixed order
onto ONE disk, each starting on the disk exactly as the one before left it,
to see whether what an old layout leaves behind (an LVM PV, a LUKS header,
md or ZFS labels, another filesystem's superblock) confuses bko-holt load
or the restored system.

A set is a list of steps.  A step is a restore of one Alpine source, with
edits that move its stacked partition to an exact start, and sometimes a
seed: a stale layer the rescuer writes onto the disk first (md, ZFS,
btrfs), for the layers no Alpine source has.  Nothing wipes the disk
between steps beyond what load (and the seed) do.

Every step names its alignment: the partition of this step's layout that
must begin at the same sector as a layer the disk holds when the step
starts (the previous step's layout, or this step's seed), so a leftover
signature sits exactly at a new partition's start.  aligned() checks it
when the set is generated, on the real dumps; a set whose sectors drift
(a source rebuilt with other partition sizes) is refused, never run
misaligned.

  steps(set)            the set's steps, in order (STEPS)
  generate(set, dumps)  [case]: each a BackShot2 case, with "chain" and
                        "seed", the edits built from the dumps
  layers(dump)          [(start, end, partition, what)]: what each
                        partition of the first disk holds, bottom up
"""

from . import dumpx, edits as E
from .geometry import Invalid

UEFI, LVM, LUKS = "alpine324-uefi", "alpine324-lvm", "alpine324-luks"
GENERATOR = "leftovers-1"

# How a stacked partition is moved so it begins where the plain layout's
# root (alpine324-uefi's vda3) begins and ends where it ends: shrunk by K
# sectors, then a gap of K before it.  K is worked out from the dumps
# (the root's start less the partition's), never written down here.
MOVE = {LVM: 2, LUKS: 3}

# The seeds: a stale layer written onto a partition the size and place of
# the next restore's root (alpine324-uefi's vda3), then the rescuer rebooted
# so it meets the layer as a booting machine would.  "what" is the
# signature the layer leaves at the partition's start (wipefs's TYPE).
SEEDS = {
    "md12": {"what": "linux_raid_member", "note": "md RAID1 (one of two, metadata 1.2: superblock 4 KiB in), ext4 on it"},
    "md10": {"what": "linux_raid_member", "note": "md RAID1 (one of two, metadata 1.0: superblock at the end), ext4 on it"},
    "zfs": {"what": "zfs_member", "note": "a ZFS pool bs2old (labels in the first and last 512 KiB), exported"},
    "btrfs": {"what": "btrfs", "note": "btrfs (superblock 64 KiB in, a copy at 64 MiB)"},
}

# The set: the user's trio first (non-LVM, LVM, non-LVM), then each other
# stacked layer followed by the plain layout.  align: (this step's
# partition, the layer it must start on: "prev" the last step's layout's
# partition holding WHAT, "seed" this step's seed).
STEPS = {
    "leftovers": [
        {"id": "L1-plain", "source": UEFI, "note": "the plain layout on a blank disk: the trio's first"},
        {"id": "L2-lvm", "source": LVM, "move": True, "align": (2, "prev", "ext4"),
         "note": "LVM onto the plain layout: the PV begins where the old ext4 root began"},
        {"id": "L3-plain", "source": UEFI, "align": (3, "prev", "LVM2_member"),
         "note": "plain onto LVM: the root begins where the old PV (VG vg0) began"},
        {"id": "L4-luks", "source": LUKS, "move": True, "align": (3, "prev", "ext4"),
         "note": "LUKS (LVM inside) onto the plain layout: the LUKS header where the old root began"},
        {"id": "L5-plain", "source": UEFI, "align": (3, "prev", "crypto_LUKS"),
         "note": "plain onto LUKS: the root begins where the old LUKS2 header (and its copy 16 KiB in) began"},
        {"id": "L6-md12", "source": UEFI, "seed": "md12", "align": (3, "seed", "linux_raid_member"),
         "note": "plain onto md RAID1, metadata 1.2"},
        {"id": "L7-md10", "source": UEFI, "seed": "md10", "align": (3, "seed", "linux_raid_member"),
         "note": "plain onto md RAID1, metadata 1.0 (superblock at the partition's end)"},
        {"id": "L8-zfs", "source": UEFI, "seed": "zfs", "align": (3, "seed", "zfs_member"),
         "note": "plain onto a ZFS pool (labels at both ends)"},
        {"id": "L9-btrfs", "source": UEFI, "seed": "btrfs", "align": (3, "seed", "btrfs"),
         "note": "ext4 onto btrfs at the same start"},
    ],
}

# Kept out, and why (README and the spec say the same).
NOT_BUILT = {
    "ext4 under btrfs": "no Alpine source restores btrfs, and an edit never changes a filesystem's type (Alpine's own rule)",
    "md or ZFS restored": "no Alpine source has md or a ZFS root; the stale layer is seeded instead",
}


def steps(name):
    if name not in STEPS:
        raise KeyError(f"no leftovers set {name} (of {', '.join(STEPS)})")
    return STEPS[name]


def sources(name):
    return sorted({s["source"] for s in steps(name)})


def _what(d, p):
    """What partition P holds at its start, as wipefs names it."""
    c = p.get("content") or {}
    return c.get("type") or "nothing"


def layers(d):
    """[(start, end, number, what)] of the first disk's partitions, end exclusive."""
    dk = dumpx.disks(d)[0]
    return [(p["start_lba"], dumpx.end(p), p["number"], _what(d, p)) for p in dumpx.parts(dk)]


def _moved(d, number, to_start):
    """Edits that put partition NUMBER at TO_START with its end kept."""
    p = dumpx.part(dumpx.disks(d)[0], number)
    k = to_start - p["start_lba"]
    if k <= 0:
        raise Invalid(f"partition {number} already starts at or after {to_start}")
    disk = dumpx.disks(d)[0]["name"]
    e1 = E.build(d, "resize", {"disk": disk, "partition": number, "by_sectors": -k})
    e2 = E.build(E.apply(d, [e1]), "gap_before", {"disk": disk, "partition": number, "sectors": k})
    return [e1, e2]


def aligned(prev_layers, this_layers, number, what):
    """The explicit check: this step's partition NUMBER begins at the sector
    where a layer holding WHAT begins on the disk as it stands.  Returns the
    sector; raises Invalid, naming both, when it doesn't."""
    mine = [x for x in this_layers if x[2] == number]
    if not mine:
        raise Invalid(f"this layout has no partition {number}")
    start = mine[0][0]
    theirs = [x for x in prev_layers if x[3] == what]
    if not theirs:
        raise Invalid(f"the disk holds no {what} for partition {number} to start on ({prev_layers})")
    if not any(x[0] == start for x in theirs):
        raise Invalid(f"partition {number} starts at {start}, the {what} at {[x[0] for x in theirs]}: not aligned")
    return start


def generate(name, dumps, gens=None):
    """The set's cases, in order.  DUMPS: {source: its live dump}; GENS:
    {source: generation}.  Raises Invalid when a step can't be aligned."""
    gens = gens or {}
    root = dumpx.part(dumpx.disks(dumps[UEFI])[0], 3)
    out, prev = [], None
    for i, st in enumerate(steps(name)):
        d = dumps[st["source"]]
        es = _moved(d, MOVE[st["source"]], root["start_lba"]) if st.get("move") else []
        mine = layers(E.apply(d, es))
        case = {"name": f"lo-{name}-{st['id']}", "source": f"{st['source']}@{gens.get(st['source'], 'unknown')}",
                "generator": GENERATOR, "seed": i, "edits": es, "note": st["note"],
                "chain": {"set": name, "step": i + 1, "of": len(steps(name)), "id": st["id"]}}
        seed_layers = None
        if st.get("seed"):
            p = dumpx.part(dumpx.disks(E.apply(d, es))[0], st["align"][0])
            case["layer_seed"] = {"kind": st["seed"], "start_lba": p["start_lba"], "sectors": p["sectors"]}
            seed_layers = [(p["start_lba"], dumpx.end(p), 1, SEEDS[st["seed"]]["what"])]
        if st.get("align"):
            number, against, what = st["align"]
            on = seed_layers if against == "seed" else prev
            at = aligned(on, mine, number, what)
            case["chain"]["aligned"] = {"partition": number, "on": what, "from": against, "sector": at}
        out.append(case)
        prev = mine
    return out


def expected_types(drifted):
    """{partition number: the signature load leaves at its start} of the first disk."""
    return {x[2]: x[3] for x in layers(drifted)}
