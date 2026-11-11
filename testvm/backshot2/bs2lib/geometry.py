"""Models of what each source tool makes from the options it's given: the
numbers a filesystem's superblock (and so a dump) then records.

The generator draws a few values (a block size, an AG count, a LUKS
offset) and asks these models for every number that follows from them; the
edit then stores all of them ("sets"), so replay never runs a model again
(backshot2.md, "A case": a later generator can't change an old case).
Tier 0 checks each model against the source's own tools, run on scratch
files: a mismatch is an INVALID case, the generator's bug to fix.

A model raises Invalid when the tool would refuse the values or quietly
make something else (mke2fs trimming a last group that's too small,
mkfs.xfs dropping a short last AG).  The draw is then discarded.
"""

MIB = 1 << 20


class Invalid(Exception):
    """The source's tool would refuse these values, or make something else."""


def cdiv(a, b):
    return -(-a // b)


def lcm(a, b):
    import math
    return a * b // math.gcd(a, b)


# ---- ext2/3/4 (e2fsprogs' ext2fs_initialize and mke2fs) -------------------

def _ext_has_super(group, sparse):
    """Whether GROUP holds a superblock backup (sparse_super: 0, 1 and powers of 3, 5, 7)."""
    if group <= 1 or not sparse:
        return True
    for b in (3, 5, 7):
        n = b
        while n < group:
            n *= b
        if n == group:
            return True
    return False


def ext4(blocks, block_size, inodes_per_group, inode_size, reserved_gdt, features, desc_size=64,
         flex_bg_size=None, reserved_blocks=None):
    """The superblock numbers of an ext2/3/4 made by mke2fs with -b BLOCK_SIZE
    -I INODE_SIZE -N (INODES_PER_GROUP x groups) -g (8 x BLOCK_SIZE) -G FLEX
    -E resize=(what gives RESERVED_GDT) on BLOCKS blocks.  Raises Invalid
    where mke2fs refuses or changes them."""
    bs, isz = block_size, inode_size
    if bs not in (1024, 2048, 4096):
        raise Invalid(f"block size {bs}")
    if isz < 128 or isz & (isz - 1) or isz > bs:
        raise Invalid(f"inode size {isz} with {bs}-byte blocks")
    if isz == 128 and "extra_isize" in features:
        raise Invalid("128-byte inodes have no room for extra_isize")
    first = 1 if bs == 1024 else 0
    bpg = 8 * bs
    if blocks - first <= 0:
        raise Invalid("no blocks")
    groups = cdiv(blocks - first, bpg)
    is64 = "64bit" in features
    dsz = desc_size if is64 else 32
    gdpb = bs // dsz
    desc_blocks = cdiv(groups, gdpb)
    ipb = bs // isz
    ipg = inodes_per_group
    if ipg % lcm(8, ipb):
        raise Invalid(f"{ipg} inodes per group don't fill whole inode table blocks in eights")
    if ipg > 8 * bs or ipg > (1 << 16) - ipb or ipg < 16:
        raise Invalid(f"{ipg} inodes per group")
    if blocks > 0xFFFFFFFF and not is64:
        raise Invalid("over 2^32 blocks without 64bit")
    ibpg = ipg * isz // bs
    rsv = reserved_gdt if "resize_inode" in features else 0
    if "resize_inode" in features:
        if not 1 <= rsv <= bs // 4:
            raise Invalid(f"{rsv} reserved GDT blocks (1 to {bs // 4})")
    if 3 + ibpg + rsv > bpg:
        raise Invalid("too many inodes for a group")
    rem = (blocks - first) % bpg
    over = 2 + ibpg
    if _ext_has_super(groups - 1, "sparse_super" in features):
        over += 1 + desc_blocks + rsv
    if groups == 1 and rem and rem < over:
        raise Invalid("too small")
    if rem and rem < over + 50:
        raise Invalid(f"the last group ({rem} blocks) is too small: mke2fs would drop it")
    if flex_bg_size is not None and "flex_bg" in features:
        if flex_bg_size < 1 or flex_bg_size & (flex_bg_size - 1) or flex_bg_size > (1 << 31):
            raise Invalid(f"flex_bg size {flex_bg_size}")
    out = {
        "block_size": bs, "blocks": blocks, "size_bytes": blocks * bs, "blocks_per_group": bpg,
        "first_data_block": first, "inode_size": isz, "inodes_per_group": ipg, "inodes": ipg * groups,
    }
    if "resize_inode" in features:
        out["reserved_gdt_blocks"] = rsv
    if flex_bg_size is not None:
        out["flex_bg_size"] = flex_bg_size
    if reserved_blocks is not None:
        if reserved_blocks > blocks // 2:
            raise Invalid("over half the blocks reserved")
        out["reserved_blocks"] = reserved_blocks
    return out


def ext4_resize2fs(c, blocks):
    """C (an ext2/3/4 content record) as resize2fs leaves it on BLOCKS
    blocks (resize2fs.c adjust_fs_info): the same blocks and inodes per
    group, so as many groups as the blocks need, a last group too small for
    its own overhead dropped, and their inodes; the reserved GDT blocks take
    up what the descriptors no longer need (or give it), the reserved
    blocks keep their share.  What --fit-largest should make of a fitted
    ext filesystem, and what mke2fs -N (groups x inodes per group) -g makes
    at that size."""
    bs, bpg, ipg = c["block_size"], c["blocks_per_group"], c["inodes_per_group"]
    feats = c.get("features") or []
    first = c.get("first_data_block", 1 if bs == 1024 else 0)
    dsz = (c.get("descriptor_size") or 64) if "64bit" in feats else 32
    gdpb = bs // dsz
    rsv = c.get("reserved_gdt_blocks", 0) if "resize_inode" in feats else 0
    old_db = cdiv(ext4_groups(c), gdpb)
    ibpg = cdiv(ipg * c["inode_size"], bs)
    while True:
        if blocks - first <= 0:
            raise Invalid("no blocks")
        groups = cdiv(blocks - first, bpg)
        db = cdiv(groups, gdpb)
        rem = (blocks - first) % bpg
        over = 2 + ibpg
        if _ext_has_super(groups - 1, "sparse_super" in feats):
            over += 1 + db + rsv
        if groups == 1 and rem and rem < over:
            raise Invalid("too small")
        if groups > 1 and rem and rem < over + 50:
            blocks -= rem
            continue
        break
    out = {"blocks": blocks, "size_bytes": blocks * bs, "blocks_per_group": bpg, "first_data_block": first,
           "inodes_per_group": ipg, "inodes": groups * ipg}
    if "resize_inode" in feats:
        out["reserved_gdt_blocks"] = max(0, min(bs // 4, rsv + old_db - db))
    if c.get("reserved_blocks") is not None and c.get("blocks"):
        out["reserved_blocks"] = c["reserved_blocks"] * blocks // c["blocks"]
    return out


def ext4_groups(c):
    return cdiv(c["blocks"] - c.get("first_data_block", 0), c["blocks_per_group"])


def ext4_resized(c, new_bytes):
    """C (an ext4 content record) resized as resize2fs would to fill
    NEW_BYTES: the same inodes per group and reserved GDT blocks, the
    reserved blocks scaled.  Raises Invalid like ext4()."""
    bs = c["block_size"]
    blocks = new_bytes // bs
    rb = c.get("reserved_blocks")
    if rb is not None and c.get("blocks"):
        rb = rb * blocks // c["blocks"]
    return ext4(blocks, bs, c["inodes_per_group"], c["inode_size"], c.get("reserved_gdt_blocks", 0),
                c.get("features") or [], c.get("descriptor_size") or 64, c.get("flex_bg_size"), rb)


def ext4_overhead(blocks, block_size, inodes, inode_size):
    """Rule 1's estimate of what ext4's own metadata takes, in bytes: inode
    tables, a journal, bitmaps and descriptors, the superblock backups."""
    journal = 64 * MIB if blocks * block_size > 1 << 30 else 16 * MIB
    groups = cdiv(blocks, 8 * block_size)
    return inodes * inode_size + journal + groups * 3 * block_size + 64 * block_size


def mke2fs_resize_for(c):
    """The -E resize=BLOCKS that gives C's reserved GDT blocks, worked out
    as mke2fs itself works them out (parse_extended_opts): from
    ceil(blocks / blocks_per_group) groups, without the first data block."""
    bs, bpg, blocks = c["block_size"], c["blocks_per_group"], c["blocks"]
    dsz = (c.get("descriptor_size") or 64) if "64bit" in (c.get("features") or []) else 32
    gdpb = bs // dsz
    gdc = cdiv(blocks, bpg)
    db = cdiv(gdc, gdpb)
    return (db + c["reserved_gdt_blocks"]) * gdpb * bpg


# ---- XFS (xfsprogs' mkfs.xfs) ---------------------------------------------

XFS_MIN_BYTES = 300 * MIB      # mkfs.xfs refuses smaller filesystems (xfsprogs 5.19 and later)
XFS_AG_MIN_BYTES = 16 * MIB
XFS_AG_MAX_BYTES = 1 << 40
XFS_LOG_MIN_BYTES = 64 * MIB   # the smallest log the generator draws (mkfs.xfs's realistic minimum)
XFS_LOG_MAX_BYTES = (1 << 31) - (1 << 20)


# The superblock's version bits, in bko-holt's order (holt/src/probe/xfs.rs
# VERSION_BITS): the dump's version_flags list them so.
XFS_VERSION_BITS = ("attr", "nlink", "quota", "align", "dalign", "shared", "logv2", "sector",
                    "extflg", "dirv2", "asciici", "morebits")


def xfs_sector_fields(c, sector_size):
    """The superblock fields mkfs.xfs -s size=SECTOR_SIZE sets beside the
    sector size itself, for an XFS whose dump record is C.  -s sets the log's
    sector size too; mkfs.xfs writes sb_logsectsize 0 and no "sector" version
    bit for 512-byte sectors and logs, else the size and the bit, and a v2
    log on 4 KiB sectors gets a 4 KiB stripe unit of its own (1 otherwise,
    "none").  A log stripe unit the source set by hand is kept."""
    out = {}
    big = sector_size != 512
    if "log_sector_size" in c:
        out["log_sector_size"] = sector_size if big else 0
    lsu = c.get("log_stripe_unit")
    if lsu in (0, 1, 4096):
        out["log_stripe_unit"] = 4096 if big else 1
    vf = c.get("version_flags")
    if isinstance(vf, list):
        have = set(vf) - {"sector"}
        if big:
            have.add("sector")
        out["version_flags"] = [b for b in XFS_VERSION_BITS if b in have] + [b for b in vf if b not in XFS_VERSION_BITS]
    return out


def xfs(blocks, block_size, ag_blocks, log_blocks, inode_size, sector_size, features, c=None):
    """The superblock numbers of an XFS made by mkfs.xfs -d size=,agsize=
    -l size= -i size= -s size=, and with C (the source's dump record) what
    the sector size sets beside itself (xfs_sector_fields).  Raises Invalid
    where it refuses, or would drop a short last AG."""
    bs = block_size
    if blocks * bs < XFS_MIN_BYTES:
        raise Invalid(f"XFS of {blocks * bs} bytes: mkfs.xfs wants 300 MiB")
    if ag_blocks * bs < XFS_AG_MIN_BYTES or ag_blocks * bs > XFS_AG_MAX_BYTES or ag_blocks > blocks:
        raise Invalid(f"AG of {ag_blocks} blocks")
    rem = blocks % ag_blocks
    if rem and rem * bs < XFS_AG_MIN_BYTES:
        raise Invalid(f"the last AG ({rem} blocks) is under 16 MiB: mkfs.xfs would drop it")
    agcount = cdiv(blocks, ag_blocks)
    if agcount < 2 and blocks * bs >= (1 << 30):
        raise Invalid("one AG")
    if log_blocks * bs < XFS_LOG_MIN_BYTES or log_blocks * bs > XFS_LOG_MAX_BYTES:
        raise Invalid(f"log of {log_blocks} blocks")
    if log_blocks > ag_blocks // 2:
        raise Invalid("log over half an AG")
    crc = "crc" in features
    if inode_size & (inode_size - 1) or not (512 if crc else 256) <= inode_size <= 2048 or inode_size > bs:
        raise Invalid(f"inode size {inode_size}")
    if sector_size not in (512, 4096) or sector_size > bs:
        raise Invalid(f"sector size {sector_size}")
    out = {"blocks": blocks, "size_bytes": blocks * bs, "ag_blocks": ag_blocks, "ag_count": agcount,
           "log_blocks": log_blocks, "inode_size": inode_size, "sector_size": sector_size}
    if c is not None:
        out.update(xfs_sector_fields(c, sector_size))
    return out


def xfs_overhead(blocks, block_size, ag_count, log_blocks):
    """Rule 1's estimate of XFS's own metadata, in bytes."""
    return log_blocks * block_size + ag_count * 8 * MIB + blocks * block_size // 50


# ---- btrfs -----------------------------------------------------------------

def btrfs(node_size, sector_size, features):
    if node_size & (node_size - 1) or not max(4096, sector_size) <= node_size <= 65536:
        raise Invalid(f"node size {node_size}")
    if "mixed_groups" in features and node_size != sector_size:
        raise Invalid("mixed block groups want node size = sector size")
    return {"node_size": node_size}


# ---- swap --------------------------------------------------------------------

def swap(container_bytes, page_size=4096):
    """mkswap on the whole of a device of CONTAINER_BYTES (load gives it no
    size): whole pages, the last one LAST_PAGE.  bko-holt's dump gives the
    swap's size as its pages, (last_page + 1) x page size
    (holt/src/probe/misc.rs), so a device that isn't whole pages has a tail
    the swap doesn't count."""
    pages = container_bytes // page_size
    if pages < 10:
        raise Invalid("swap too small")
    return {"last_page": pages - 1, "size_bytes": pages * page_size}


# ---- LUKS2 (cryptsetup) ------------------------------------------------------

LUKS2_HEADER_SIZES = (16384, 32768, 65536, 131072, 262144, 524288, 1048576, 2097152, 4194304)
LUKS2_KEYSLOTS_MAX = 128 * MIB
AF_STRIPES = 4000


def luks2_keyslot_bytes(key_bits):
    """What one keyslot's anti-forensic area takes: key bytes x 4000 stripes, rounded to 4 KiB."""
    return cdiv(key_bits // 8 * AF_STRIPES, 4096) * 4096


def luks2(container_bytes, header_size, keyslots_size, offset, sector_size, keyslots, key_bits=512):
    """A LUKS2 header with these sizes: the payload's size, or Invalid."""
    if header_size not in LUKS2_HEADER_SIZES:
        raise Invalid(f"metadata size {header_size}")
    if keyslots_size % 4096 or keyslots_size > LUKS2_KEYSLOTS_MAX or keyslots_size < keyslots * luks2_keyslot_bytes(key_bits):
        raise Invalid(f"keyslots size {keyslots_size}")
    if sector_size not in (512, 4096):
        raise Invalid(f"sector size {sector_size}")
    if offset % 4096 or offset % sector_size or offset < 2 * header_size + keyslots_size:
        raise Invalid(f"offset {offset}")
    payload = container_bytes - offset
    if payload <= 0 or payload % sector_size:
        raise Invalid(f"payload of {payload} bytes with {sector_size}-byte sectors")
    return {"payload_bytes": payload}


# ---- LVM ---------------------------------------------------------------------

def pv_extents(dev_bytes, pe_start, extent_size):
    """How many extents a PV of DEV_BYTES holds (pvresize's count)."""
    return max(0, (dev_bytes - pe_start) // extent_size)
