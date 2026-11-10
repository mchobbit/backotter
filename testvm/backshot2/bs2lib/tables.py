"""Rule 2's tables (backshot2.md, "The rules, as checks"): what values each
tool, at each version, lets an administrator choose.  The generator draws
only from these; tier 0's dry runs then check each case in the source's own
tools, and a value they refuse makes the case INVALID: a table to fix.

A table row is (TOOL, LOWEST_VERSION, {FIELD: allowed}).  The dump records
the source's versions in boot.packages (Alpine's apk names; dpkg's and rpm's
the same, where the dump has them).  The newest row at or below the source's
version applies; a source whose version isn't recorded gets the oldest row.
"""

import re

from .geometry import LUKS2_HEADER_SIZES

POW2 = lambda lo, hi: [1 << i for i in range(lo.bit_length() - 1, hi.bit_length()) if lo <= 1 << i <= hi]

TABLES = [
    ("e2fsprogs", "1.42", {
        "block_size": [1024, 2048, 4096],
        "inode_size": [128, 256, 512, 1024],
        "flex_bg_size": POW2(1, 1024),
    }),
    ("xfsprogs", "5.19", {
        # crc=1 filesystems: 512-byte inodes at least; 300 MiB at least (geometry.py).
        "inode_size": [512, 1024, 2048],
        "sector_size": [512, 4096],
        "ag_count": list(range(2, 65)),
    }),
    ("btrfs-progs", "5.0", {
        "node_size": POW2(4096, 65536),
    }),
    ("cryptsetup", "2.0", {
        "header_size": list(LUKS2_HEADER_SIZES),
        "sector_size": [512],
    }),
    ("cryptsetup", "2.0.6", {
        "header_size": list(LUKS2_HEADER_SIZES),
        "sector_size": [512, 4096],
    }),
    ("lvm2", "2.02", {
        "segments_per_lv": list(range(1, 5)),
    }),
    ("util-linux", "2.26", {
        "gpt_entries": 128,
        "mbr_primaries": 4,
    }),
]

# The dump's package names for each tool (Alpine, Debian, Fedora).
PACKAGES = {
    "e2fsprogs": ("e2fsprogs",),
    "xfsprogs": ("xfsprogs",),
    "btrfs-progs": ("btrfs-progs", "btrfs-progs"),
    "cryptsetup": ("cryptsetup", "cryptsetup-bin", "cryptsetup-libs"),
    "lvm2": ("lvm2",),
    "util-linux": ("util-linux", "util-linux-misc", "fdisk"),
}


def vkey(v):
    """A version as a tuple to compare: 1.47.4-r0 -> (1, 47, 4)."""
    v = str(v).split(":")[-1]
    v = re.split(r"[-_+~]", v)[0]
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4])


def version(dump, tool):
    pk = ((dump.get("boot") or {}).get("packages")) or {}
    for name in PACKAGES.get(tool, (tool,)):
        if pk.get(name):
            return pk[name]
    return None


def allowed(dump, tool, field):
    """The values the source's TOOL allows for FIELD."""
    ver = version(dump, tool)
    rows = [r for r in TABLES if r[0] == tool]
    if not rows:
        return None
    pick = rows[0]
    if ver is not None:
        for r in rows:
            if vkey(r[1]) <= vkey(ver):
                pick = r
    return pick[2].get(field)


def check(dump, tool, field, value):
    """None when VALUE is allowed, else why not."""
    a = allowed(dump, tool, field)
    if a is None or value in a:
        return None
    return f"{tool} {version(dump, tool) or '(version unknown)'} allows {field} {a!r:.80}, not {value}"
