"""The family interface (docs/design/bs2-all.md, "The family interface (A1)").

Two kinds of module live in this package, one file each:

- a Family: exactly one per source, the OS and distro (Alpine, NixOS,
  generic Linux; later FreeBSD, OpenBSD, illumos, Haiku ...).  The
  matching family with the highest priority wins.
- a Trait: any number per source, matched from what the dump records (an
  initramfs generator, a loader, snapper, ostree; later ZFS, md and
  multi-disk), shared across families.

Every field is optional and does nothing by default.  The registry
(families/__init__.py) merges a source's family and traits into one Hooks,
made once per generate() call; the generator, the rules, the tables, rule
3's texts, the content drift, the sweep and tier 0 read only that.

Rules for a module (the spec's "Rules for lanes"):
- it draws only from the generator's rng, passed to its draws;
- it never changes what an existing family draws: a quirk or a steer
  consumes no randomness, and a change to one is a new GENERATOR version
  with its family's golden (tests/golden/FAMILY.json) rewritten.
"""

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from .. import dumpx


def never(source, dump):
    return False


@dataclass(frozen=True)
class Quirk:
    """The source's own rule that a drift must keep (as Alpine's mkinitfs
    rule): check(source, drifted, edits) -> [(RULE, WHY)], run after the
    three rules on every draw.  NAME and WHY say what it refuses and why."""
    name: str
    why: str
    check: Callable


@dataclass
class Module:
    """What a family or trait holds (each optional).

    tables      rule 2 rows, as tables.TABLES: (TOOL, LOWEST_VERSION, {FIELD: allowed});
                a module's rows for a tool replace the generic rows for it
    packages    {TOOL: (package names in this family's dumps)}, added to tables.PACKAGES
    versions    versions(dump) -> {TOOL: version} where the dump's packages don't say
                (the family's only)
    quirks      [Quirk]
    ops         ops(dump, weights) -> weights: drops the edit ops the source can't take
    draws       {op: (draw(rng, d, hooks) -> [(op, params)] or None, weight, group)}
    edits       {op: EditOp} (as edits.OPS); an EditOp may have rule2_pairs(edit)
                -> [(TOOL, FIELD, value)] for rule 2
    config_texts     config_texts(dump) -> [str]: more configuration that names devices (rule 3)
    number_patterns  number_patterns(dump, dk, p) -> [regex]: what names P by number or path
    names_by_number  names_by_number(dump, dk, p) -> [what was found], for what a regex can't say
    recipes     {NAME: recipe} added to content.RECIPES
    fs_can      {FSTYPE: {what it holds}} added to content.FS_CAN
    content     content(dump, fstype) -> the recipe this source draws, or None (no
                content drift); the family's only
    known       {"fail-NNN": why}: known findings its steer avoids
    steer       steer(dump, drifted, edits, opts) -> opts
    sweep       sweep(dump, opts) -> opts: load's options this source can take
    tier0       "chroot" (dry runs in a chroot of the source's root), "guest" (in one
                booted source guest, batched: A4-A6), "plan" (load plan and invariants
                only); the family's only
    """
    name: str
    piece: str = "A1"
    matches: Callable = never
    tables: list = field(default_factory=list)
    packages: dict = field(default_factory=dict)
    versions: Optional[Callable] = None
    quirks: list = field(default_factory=list)
    ops: Optional[Callable] = None
    draws: dict = field(default_factory=dict)
    edits: dict = field(default_factory=dict)
    config_texts: Optional[Callable] = None
    number_patterns: Optional[Callable] = None
    names_by_number: Optional[Callable] = None
    recipes: dict = field(default_factory=dict)
    fs_can: dict = field(default_factory=dict)
    content: Optional[Callable] = None
    known: dict = field(default_factory=dict)
    steer: Optional[Callable] = None
    sweep: Optional[Callable] = None
    tier0: str = "chroot"


@dataclass
class Family(Module):
    """A source's OS and distro: exactly one per source, the most specific
    (highest priority) of those that match."""
    priority: int = 0


@dataclass
class Trait(Module):
    """Something a source has, matched from its dump, shared across families."""


# ---- helpers the modules share ---------------------------------------------------

def os_id(dump):
    return (dump.get("os") or {}).get("id")


def loader(dump):
    return ((dump.get("boot") or {}).get("loader") or {}).get("name")


def packages(dump):
    return ((dump.get("boot") or {}).get("packages")) or {}


def initramfs(dump):
    return ((dump.get("boot") or {}).get("initramfs")) or {}


def layers(d):
    """What the initramfs must know how to open, in D: every content type
    (each filesystem, swap, crypto_LUKS, LVM2_member, linux_raid_member,
    zfs_member), every device-mapper target and md level."""
    out = set()
    for x in dumpx.devices(d).values():
        t = (x.get("content") or {}).get("type")
        if t:
            out.add(("content", t))
        dm = x.get("dm") or {}
        if dm.get("target"):
            out.add(("dm", dm["target"]))
        md = x.get("md") or {}
        if md.get("level"):
            out.add(("md", str(md["level"])))
    return out


def layers_quirk(generator, why):
    """The rule an initramfs generator's own configuration makes: load
    regenerates the initramfs with the source's own module list, hooks or
    host-only choice, so a drift may change a geometry but never what needs
    a module that list may lack (no new filesystem type, stack layer or md
    level).  No phase 1 edit changes either, so it holds by construction;
    the check keeps it so for the edits later lanes add."""

    def check(source, drifted, edits):
        a, b = layers(source), layers(drifted)
        if a == b:
            return []
        new = sorted(b - a)
        gone = sorted(a - b)
        return [(3, f"{generator}: the drift changes what the initramfs must open "
                    f"({'adds ' + str(new) if new else ''}{' ' if new and gone else ''}{'drops ' + str(gone) if gone else ''}), "
                    f"which load's rebuild with the source's own configuration needn't include")]

    return Quirk(f"{generator}-layers", why, check)


def walk_moves(source, edits, ops=("renumber", "swap_order")):
    """[(edit, disk, partition)] for each partition the edits of OPS
    renumber or reorder, each as it was when its edit came (as rule 3
    walks them)."""
    from .. import edits as E
    if not any(e.get("op") in ops for e in edits):
        return []
    out = []
    cur = source
    for e in edits:
        op = e.get("op")
        if op in ops:
            dk = dumpx.disk(cur, e.get("disk"))
            nums = [e["from"]] if op == "renumber" else [e["a"], e["b"]] if op == "swap_order" else [e.get("partition")]
            for n in nums:
                p = dumpx.part(dk, n) if dk else None
                if p is not None:
                    out.append((e, dk, p))
        cur = E.apply(cur, [e])
    return out


def holds_something(d, p):
    """Whether partition P holds anything: content of its own, or a mount,
    swap or device built on it."""
    if (p.get("content") or {}).get("type"):
        return True
    if dumpx.children(d, p["name"]):
        return True
    return any(m.get("device") == p["name"] for m in dumpx.storage(d).get("mounts") or [])


def strings(v):
    return [s for s in dumpx.all_strings(v) if isinstance(s, str)]


def number_re(*fmts):
    """Regex builders for number_patterns: each FMT gets {num} and {disk}."""
    def pats(dump, dk, p):
        return [f.format(num=p["number"], disk=re.escape(dk["name"])) for f in fmts]
    return pats
