"""BackShot2's sources (docs/design/bs2-all.md, "Sources"; piece A2): which
corpus guests bs2 runs at tiers 1 and 2 now, which it skips and the piece
that will take each, whether a source is UEFI, and its seconds a case.
Pure: it reads the repository's corpus and tables, never a host.

The candidates are holt/tests/corpus/index.tsv's guests at run time that
have a GUEST/src.json, amd64 only (a guest named *-arm64, or a dump of an
aarch64 machine, is arm64: BackShot2's rescuer and bko-holt are amd64), so a
guest added to the corpus joins without an edit here.  Alpine's four
(BackShot2Alpine's) are sources too; three of them are not corpus guests.

  classify(name) -> Class   "built" (bs2 runs it now), or "skipped" with
                            the piece that builds it and why:
      - not a Linux family: families.skipped()'s piece (A4 BSD, A5
        illumos, A6 Haiku, Redox, Sculpt OS);
      - a real machine's dump (index.tsv's host "realworld": BackShot1's
        Thinkpad): no QEMU guest; A8's tier 3 takes BackShot1 library runs;
      - not a HoltTest2 source (testvm/test2/classes.json holt.families
        and no_family): ht1.sh, which runs a case's tiers 1 and 2, doesn't
        take it ("later");
      - ZFS, md or more than one dumped disk: A3;
      - EXCLUDE's, then any LUKS1 source: bs2's LUKS model is LUKS2's only.
  table()          {name: Class} for Alpine's four and every candidate
  built()          the built names, sorted (Alpine's and Guix's among them)
  others()         built() less Alpine's and Guix's: what bs2 deals after them
  uefi(name)       its dump's firmware is UEFI
  secs(name, tier) -> (seconds a case, "measured" | "est.")
"""

import functools
import json
import os
from dataclasses import dataclass

from . import dumpx

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
CORPUS = os.path.join(REPO, "holt", "tests", "corpus")
INDEX = os.path.join(CORPUS, "index.tsv")
CLASSES = os.path.join(REPO, "testvm", "test2", "classes.json")
ESTIMATES = os.path.join(REPO, "testvm", "test2", "estimates.tsv")

ALPINE_SOURCES = ("alpine324", "alpine324-uefi", "alpine324-lvm", "alpine324-luks")
GUIX_SOURCES = ("guix15", "guix15-bios")
# BackShot2Alpine's three variants have no corpus dump (only alpine324 has): their firmware.
ALPINE_UEFI = {"alpine324": False, "alpine324-uefi": True, "alpine324-lvm": True, "alpine324-luks": True}

# Linux sources bs2's models can't take yet, each with the lane that takes it.
EXCLUDE = {
    "guix15-luks": ("later", "LUKS1 cryptroot under the Btrfs, GRUB opening it: bs2's LUKS model is LUKS2's only "
                             "(edits.LuksGeometry, geometry.luks2), nothing models a LUKS1 payload offset or load's "
                             "luks1-digest step, the Guix fixtures and golden lack its shape, and its boots type the "
                             "passphrase at GRUB and the initrd (README, \"guix15-luks\"): a later lane"),
}

BUILT, SKIPPED = "built", "skipped"


@dataclass(frozen=True)
class Class:
    """A source's class: STATUS built or skipped; PIECE the piece that builds a skipped
    one (A3, A4, A5, A6, A8, "later": a lane not planned yet; None: not a source at all,
    an unknown or arm64 name); WHY in words."""
    name: str
    status: str
    piece: object = None
    why: str = ""

    @property
    def built(self):
        return self.status == BUILT

    def phrase(self):
        """A refusal's words: "NAME: skipped (PIECE): WHY"."""
        if self.built:
            return f"{self.name}: built"
        return f"{self.name}: {'skipped (' + self.piece + ')' if self.piece else 'not a source'}: {self.why}"


# ---- the corpus ---------------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def index_rows(path=INDEX):
    """{guest: {column: value}} of index.tsv, its rows with a GUEST/src.json beside it."""
    out = {}
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    if not lines:
        return out
    head = lines[0].split("\t")
    for line in lines[1:]:
        w = line.split("\t")
        if not w or not w[0] or w[0] in out:
            continue
        if os.path.exists(os.path.join(os.path.dirname(path), w[0], "src.json")):
            out[w[0]] = dict(zip(head, w))
    return out


@functools.lru_cache(maxsize=None)
def dump(name, corpus=CORPUS):
    """NAME's corpus dump, or None (no GUEST/src.json)."""
    p = os.path.join(corpus, name, "src.json")
    return dumpx.load(p) if os.path.exists(p) else None


def arm64(name, d=None):
    """Whether NAME is an arm64 guest: its name (hosts.py guest_arch's *-arm64) or its dump's machine."""
    if str(name).endswith("-arm64"):
        return True
    d = d if d is not None else dump(name)
    return ((d or {}).get("os") or {}).get("architecture") in ("aarch64", "arm64")


def candidates():
    """The amd64 corpus guests, sorted."""
    return sorted(g for g in index_rows() if not arm64(g))


@functools.lru_cache(maxsize=None)
def holt_sources(path=CLASSES):
    """HoltTest2's sources: classes.json holt.families' guests and its no_family ones."""
    try:
        with open(path) as f:
            h = json.load(f).get("holt") or {}
    except (OSError, ValueError):
        return frozenset()
    return frozenset([g for v in (h.get("families") or {}).values() for g in v] + list(h.get("no_family") or []))


# ---- what the dump records ----------------------------------------------------------

def _contents(d):
    st = dumpx.storage(d)
    out = []
    for dk in st.get("disks") or []:
        out.append((dk.get("name"), dk.get("content") or {}))
        for p in dk.get("partitions") or []:
            out.append((p.get("name"), p.get("content") or {}))
    for x in (st.get("dm_devices") or []) + (st.get("md_arrays") or []):
        out.append((x.get("name"), x.get("content") or {}))
    return out


def multi(d):
    """Why D is A3's (ZFS, md, more than one disk), or None."""
    st = dumpx.storage(d)
    why = []
    zfs = sorted({n for n, c in _contents(d) if c.get("type") == "zfs_member"})
    zm = sorted({m.get("target") for m in st.get("mounts") or [] if m.get("fstype") == "zfs"})
    pools = (st.get("zfs") or {}).get("pools_from_labels") if isinstance(st.get("zfs"), dict) else None
    if zfs or zm or pools:
        why.append("ZFS" + (f" (zfs_member {', '.join(zfs)})" if zfs else "") + (f" mounted at {', '.join(zm[:3])}" if zm else ""))
    md = sorted({x.get("name") for x in st.get("md_arrays") or []} |
                {n for n, c in _contents(d) if c.get("type") == "linux_raid_member"})
    if md:
        why.append(f"md ({', '.join(md)})")
    ds = dumpx.disks(d)
    if len(ds) > 1:
        why.append(f"{len(ds)} disks ({', '.join(x.get('name') for x in ds)})")
    return "; ".join(why) or None


def luks_versions(d):
    """{device: LUKS version (a string)} of D's LUKS containers."""
    return {n: str(c.get("version")) for n, c in _contents(d) if c.get("type") == "crypto_LUKS"}


# ---- the classes ----------------------------------------------------------------------

def classify(name):
    """NAME's Class (the module's docstring has the rules, in their order)."""
    from . import families as F
    if name in ALPINE_SOURCES:
        return Class(name, BUILT, why="BackShot2Alpine's")
    d = dump(name)
    if d is None or name not in index_rows():
        return Class(name, SKIPPED, None, "no such source: not a corpus guest (holt/tests/corpus/index.tsv, GUEST/src.json)")
    if arm64(name, d):
        return Class(name, SKIPPED, None, "an arm64 guest: BackShot2's sources are amd64")
    if (index_rows().get(name) or {}).get("host") == "realworld":
        return Class(name, SKIPPED, "A8", "a real machine's dump (BackShot1's Thinkpad), no QEMU guest: "
                                          "tier 3 (A8) takes BackShot1 library runs")
    piece = F.skipped(name, d)
    if piece:
        o = d.get("os") or {}
        return Class(name, SKIPPED, piece, f"{o.get('family') or o.get('id') or 'its OS'}: no bs2 family yet ({piece})")
    if name not in holt_sources():
        return Class(name, SKIPPED, "later", "not a HoltTest2 source (testvm/test2/classes.json holt.families): "
                                             "ht1.sh, which runs tiers 1 and 2, doesn't take it; a later lane")
    m = multi(d)
    if m:
        return Class(name, SKIPPED, "A3", f"{m}: ZFS, md and multi-disk drift is A3's")
    if name in EXCLUDE:
        piece, why = EXCLUDE[name]
        return Class(name, SKIPPED, piece, why)
    l1 = sorted(dev for dev, v in luks_versions(d).items() if v != "2")
    if l1:
        return Class(name, SKIPPED, "later", f"LUKS{luks_versions(d)[l1[0]]} on {', '.join(l1)}: bs2's LUKS model is "
                                             "LUKS2's only (edits.LuksGeometry, geometry.luks2); a later lane")
    return Class(name, BUILT, why=f"one disk, Linux ({F.family_of(name, d).name})")


@functools.lru_cache(maxsize=None)
def table():
    """{name: Class} for Alpine's four and every candidate, in that order (candidates sorted)."""
    out = {s: classify(s) for s in ALPINE_SOURCES}
    for g in candidates():
        out.setdefault(g, classify(g))
    return out


def built():
    return sorted(n for n, c in table().items() if c.built)


def skipped():
    """{name: Class} of the skipped candidates."""
    return {n: c for n, c in table().items() if not c.built}


def others():
    """The built sources other than Alpine's and Guix's, sorted: what bs2 deals after them."""
    return tuple(n for n in built() if n not in ALPINE_SOURCES and n not in GUIX_SOURCES)


def uefi(name):
    """Whether NAME boots UEFI: its corpus dump's firmware (Alpine's variants: ALPINE_UEFI)."""
    if name in ALPINE_UEFI:
        return ALPINE_UEFI[name]
    d = dump(name)
    return d is not None and dumpx.firmware(d) == "uefi"


# ---- seconds a case -------------------------------------------------------------------

# Measured (bs2's own runs; tier 1 and 2 contended big-host means where there are any: plan
# by the contended figure, A0's rule).  A0 (docs/design/bs2-all.md "A0 and A1, measured",
# bs2a-20261114-0827): alpine324's 65 is its free-host mean (40.3 s) times the measured
# contention (1.5-1.7), the others Cinqcent's contended means; tier 2 the top of the measured
# 52-71 s (uefi, lvm) and 74-98 s (luks), on the free small hosts.  Guix (README,
# bs2a-20261124-1111, contended big hosts): guix15 ~235 / ~230, guix15-bios ~153.
MEASURED = {
    "alpine324": {1: 65},
    "alpine324-uefi": {1: 86, 2: 71},
    "alpine324-lvm": {1: 114, 2: 71},
    "alpine324-luks": {1: 141, 2: 98},
    "guix15": {1: 235, 2: 230},
    "guix15-bios": {1: 153},
}
TIER0_S = 0.3        # a tier 0 case (measured 2026-11-10; A0: ~0.28 s)
# The estimate for the rest: a cold HoltTest1's seconds for the guest (estimates.tsv's
# holt:GUEST wall_s) times REUSE, at least FLOOR_S (A0: what a case reuses saved ~45% on a free
# host, but Guix's contended cases took 0.63-0.77 of HoltTest1's: 0.7 since 2026-11-24); tier 2
# as tier 1 (measured alike: guix15's 230 to 235, Alpine's tier 2 no longer than its tier 1).
REUSE = 0.7
FLOOR_S = 120
NO_ROW_S = 360       # a guest with no holt row: estimates.tsv's default for a big guest (6 gm)


@functools.lru_cache(maxsize=None)
def holt_rows(path=ESTIMATES):
    """{guest: (wall_s, gb_disk or None)} of estimates.tsv's holt:GUEST rows (no variant)."""
    out = {}
    try:
        with open(path) as f:
            for line in f:
                w = line.rstrip("\n").split("\t")
                if len(w) < 6 or w[0] != "holt" or not w[1].startswith("holt:") or "+" in w[1]:
                    continue
                try:
                    wall = float(w[3])
                except ValueError:
                    continue
                try:
                    gb = float(w[5])
                except ValueError:
                    gb = None
                out.setdefault(w[1][len("holt:"):], (wall, gb))
    except OSError:
        pass
    return out


def secs(name, tier):
    """(seconds a case of NAME takes at TIER, "measured" or "est.")."""
    if tier == 0:
        return TIER0_S, "measured"
    m = MEASURED.get(name) or {}
    if tier in m:
        return m[tier], "measured"
    if tier == 2 and 1 in m:
        return m[1], "est."
    wall = (holt_rows().get(name) or (NO_ROW_S, None))[0]
    return max(FLOOR_S, int(round(REUSE * wall))), "est."


def disk_gb(name):
    """The GB one HoltTest1 run of NAME writes on /bkoscratch (estimates.tsv's gb_disk), or None."""
    return (holt_rows().get(name) or (None, None))[1]
