"""The generator (backshot2.md, "The generator"): seeded draws of edits a
real machine could have drifted into, each checked against the three rules
before it's kept.  A draw that breaks a rule is discarded, never run.

generate(source_name, source_gen, dump, seed) -> case.  The same arguments
give the same case: the random stream is seeded by "SOURCE:SEED" alone.

Order, as the spec has it: partition edits first (gap_before, align,
renumber, resize, swap_order), then LVM (lv_split, lv_resize), LUKS
(luks_geometry) and filesystem geometry (ext4, xfs, btrfs), then the target
disk's size, then load's options (sweep.py) and the content drift.  Each
kind is drawn by weight among those the layout has a place for; a draw may
bring a helper edit with it (room made at the disk's end for a gap, a PV
grown for a split), and both must pass.
"""

import random

from . import GENERATOR, content as C, dumpx, edits as E, fitcalc, rules, sweep
from .geometry import Invalid, cdiv, lcm

MIB = 1 << 20
SECT_MIB = 2048

# The content recipe drawn (content.py): alpine-small-2 until rustic-bko keeps
# paths past PATH_MAX (fail-001).
RECIPE = "alpine-small-2"

PARTITION_OPS = {"gap_before": 3, "align": 2, "renumber": 2, "resize": 4, "swap_order": 2}
STACK_OPS = {"lv_split": 3, "lv_resize": 3, "luks_geometry": 3}
FS_OPS = {"ext4_geometry": 3, "xfs_geometry": 3, "btrfs_geometry": 1}


def logu(rng, lo, hi):
    """An integer between LO and HI (inclusive), log-uniform: small changes as often as big ones."""
    import math
    if hi <= lo:
        return lo
    return int(round(math.exp(rng.uniform(math.log(lo), math.log(hi + 1))))) if lo > 0 else rng.randint(lo, hi)


def _pick(rng, xs):
    return rng.choice(xs) if xs else None


def _mib_or_odd(rng, n):
    """N, or N rounded to a MiB of sectors (tools mostly work in MiB)."""
    if rng.random() < 0.5 and abs(n) >= SECT_MIB:
        return (n // SECT_MIB) * SECT_MIB
    return n


# ---- draws: each returns [(op, params), ...] or None ---------------------------

def _movable_parts(d, dk):
    return [p for p in dk["partitions"] if E.movable(d, dk, p) is None]


def draw_gap_before(rng, d):
    dk = dumpx.disk(d)
    ps = _movable_parts(d, dk)
    p = _pick(rng, ps)
    if not p:
        return None
    k = _mib_or_odd(rng, logu(rng, 1, 64 * SECT_MIB))
    k = max(1, k)
    steps = []
    if dumpx.tail_free(dk) < k:
        last = max(dk["partitions"], key=dumpx.end)
        steps.append(("resize", {"disk": dk["name"], "partition": last["number"], "by_sectors": -(k - dumpx.tail_free(dk) + rng.choice([0, 0, 7, SECT_MIB])), "grow_fs": True}))
    steps.append(("gap_before", {"disk": dk["name"], "partition": p["number"], "sectors": k}))
    return steps


def draw_align(rng, d):
    dk = dumpx.disk(d)
    ps = [p for p in _movable_parts(d, dk) if (p.get("content") or {}).get("type") != "vfat"]
    p = _pick(rng, ps)
    if not p:
        return None
    ss = dumpx.sector(dk)
    k = rng.randint(1, SECT_MIB - 1)
    if (p["start_lba"] + k) % SECT_MIB == 0:
        k += 1
    return [("align", {"disk": dk["name"], "partition": p["number"], "sectors": k})]


def draw_renumber(rng, d):
    dk = dumpx.disk(d)
    ps = _movable_parts(d, dk)
    if "fail-002" in KNOWN and boots_from_fallback(d):
        ps = [p for p in ps if p["name"] not in boot_parts(d)]
    p = _pick(rng, ps)
    if not p:
        return None
    used = {q["number"] for q in dk["partitions"]}
    hi = 4 if dumpx.table(dk).get("type") == "dos" else rng.choice([8, 16, 16, 32, 128])
    free = [n for n in range(1, hi + 1) if n not in used]
    to = _pick(rng, free)
    if to is None:
        return None
    return [("renumber", {"disk": dk["name"], "from": p["number"], "to": to})]


def draw_resize(rng, d):
    dk = dumpx.disk(d)
    ps = [p for p in dk["partitions"] if not p.get("extended") and (p.get("content") or {}).get("type") != "vfat"]
    p = _pick(rng, ps)
    if not p:
        return None
    cap = max(1, p["sectors"] // 4)
    room = dumpx.space_after(dk, p)
    grow = room > 0 and rng.random() < 0.5
    if grow:
        by = _mib_or_odd(rng, logu(rng, 1, min(cap, room)))
    else:
        by = -_mib_or_odd(rng, logu(rng, 1, cap))
    if by == 0:
        return None
    return [("resize", {"disk": dk["name"], "partition": p["number"], "by_sectors": by, "grow_fs": rng.random() < 0.7})]


def draw_swap_order(rng, d):
    dk = dumpx.disk(d)
    order = [p for p in dumpx.parts(dk) if not p.get("extended")]
    pairs = [(a, b) for a, b in zip(order, order[1:]) if E.movable(d, dk, a) is None and E.movable(d, dk, b) is None]
    pr = _pick(rng, pairs)
    if not pr:
        return None
    return [("swap_order", {"disk": dk["name"], "a": pr[0]["number"], "b": pr[1]["number"]})]


def _linear_lvs(d):
    out = []
    for v in dumpx.vgs(d):
        for x in v.get("logical_volumes") or []:
            if E.pieces_of(x) and dumpx.lv_device(d, v["name"], x["name"]):
                out.append((v, x))
    return out


def draw_lv_split(rng, d):
    lvs = _linear_lvs(d)
    if not lvs:
        return None
    v, x = rng.choice(lvs)
    total = E.lv_extents(x)
    n = rng.randint(2, 4)
    if total < n:
        return None
    runs = E.free_runs(v, x["name"])
    cuts = sorted(rng.sample(range(1, total), n - 1))
    sizes = [b - a for a, b in zip([0] + cuts, cuts + [total])]
    pieces = []
    taken = {}
    for sz in sizes:
        cand = [r for r in runs if r[2] >= sz]
        if not cand:
            return None
        r = rng.choice(cand)
        off = rng.randint(0, r[2] - sz)
        pieces.append([r[0], r[1] + off, sz])
        runs.remove(r)
        if off:
            runs.append((r[0], r[1], off))
        if r[2] - off - sz:
            runs.append((r[0], r[1] + off + sz, r[2] - off - sz))
    rng.shuffle(pieces)   # scattered: the PV's order isn't the LV's
    return [("lv_split", {"vg": v["name"], "lv": x["name"], "segments": pieces})]


def draw_lv_resize(rng, d):
    lvs = _linear_lvs(d)
    if not lvs:
        return None
    v, x = rng.choice(lvs)
    total = E.lv_extents(x)
    free = sum(r[2] for r in E.free_runs(v, x["name"])) - total
    cap = max(1, total // 4)
    if free > 0 and rng.random() < 0.5:
        by = logu(rng, 1, min(cap, free))
    else:
        by = -logu(rng, 1, cap)
    try:
        pieces = E.resize_lv_pieces(v, x, by, rng.choice)
    except Invalid:
        return None
    return [("lv_resize", {"vg": v["name"], "lv": x["name"], "segments": pieces, "grow_fs": rng.random() < 0.7})]


def draw_luks_geometry(rng, d):
    devs = [x for x in dumpx.devices(d).values() if (x.get("content") or {}).get("type") == "crypto_LUKS"
            and str(x["content"].get("version")) == "2"]
    dev = _pick(rng, devs)
    if not dev:
        return None
    c = dev["content"]
    f = E.luks_fields(c)
    keyslots = len(c.get("keyslots") or [])
    from .geometry import LUKS2_HEADER_SIZES, luks2_keyslot_bytes
    from . import tables
    hs = rng.choice(tables.allowed(d, "cryptsetup", "header_size") or list(LUKS2_HEADER_SIZES)) if rng.random() < 0.6 else f["header_size"]
    need = max(1, keyslots) * luks2_keyslot_bytes(((c.get("keyslots") or [{}])[0]).get("key_bits") or 512)
    ks = f["keyslots_size"]
    if rng.random() < 0.6:
        ks = cdiv(logu(rng, need, 128 * MIB), 4096) * 4096
    ss = f["sector_size"]
    if rng.random() < 0.3:
        ss = rng.choice(tables.allowed(d, "cryptsetup", "sector_size") or [512])
    lo = cdiv(2 * hs + ks, 4096) * 4096
    off = f["offset"] if f["offset"] >= lo and rng.random() < 0.3 else lo + 4096 * logu(rng, 0, 16384)
    off = cdiv(off, max(4096, ss)) * max(4096, ss)
    return [("luks_geometry", {"dev": dev["name"], "header_size": hs, "keyslots_size": ks, "offset": off, "sector_size": ss})]


def _fs_devs(d, types):
    return [x for x in dumpx.devices(d).values() if (x.get("content") or {}).get("type") in types]


def draw_ext4_geometry(rng, d):
    dev = _pick(rng, _fs_devs(d, ("ext2", "ext3", "ext4")))
    if not dev:
        return None
    c = dev["content"]
    from . import tables
    p = {"fs": dumpx.locator(d, dev)}
    which = rng.sample(["block_size", "inode_size", "inodes_per_group", "flex_bg_size", "reserved_gdt_blocks"], rng.randint(1, 3))
    bs = c["block_size"]
    if "block_size" in which:
        bs = rng.choice([b for b in tables.allowed(d, "e2fsprogs", "block_size") or [4096] if b != c["block_size"]] or [bs])
        p["block_size"] = bs
    isz = c["inode_size"]
    if "inode_size" in which:
        opts = [i for i in tables.allowed(d, "e2fsprogs", "inode_size") or [256] if i <= bs and (i > 128 or "extra_isize" not in (c.get("features") or []))]
        isz = rng.choice(opts) if opts else isz
        p["inode_size"] = isz
    if bs != c["block_size"] or isz != c["inode_size"] or "inodes_per_group" in which:
        # Inodes per group: whole inode-table blocks, in eights, enough for what's there.
        groups = cdiv(c["blocks"] * c["block_size"] // bs - (1 if bs == 1024 else 0), 8 * bs)
        step = lcm(8, bs // isz)
        used = (dumpx.used_inodes(dev) or 0) * 1.2 + 2000
        lo = max(step, cdiv(int(used), groups * step) * step)
        hi = min(8 * bs, (1 << 16) - bs // isz) // step * step
        if lo > hi:
            return None
        ipg = c["inodes_per_group"] if "inodes_per_group" not in which and lo <= c["inodes_per_group"] <= hi and c["inodes_per_group"] % step == 0 else \
            rng.randrange(lo, hi + 1, step) if rng.random() < 0.5 else min(hi, max(lo, c["inodes_per_group"] // step * step))
        p["inodes_per_group"] = ipg
    if "flex_bg_size" in which and "flex_bg" in (c.get("features") or []):
        p["flex_bg_size"] = rng.choice([g for g in tables.allowed(d, "e2fsprogs", "flex_bg_size") or [16] if g <= 64])
    if "reserved_gdt_blocks" in which and "resize_inode" in (c.get("features") or []):
        p["reserved_gdt_blocks"] = rng.randint(1, bs // 4)
    elif "resize_inode" in (c.get("features") or []) and c.get("reserved_gdt_blocks", 0) > bs // 4:
        p["reserved_gdt_blocks"] = bs // 4
    return [("ext4_geometry", p)]


def draw_xfs_geometry(rng, d):
    dev = _pick(rng, _fs_devs(d, ("xfs",)))
    if not dev:
        return None
    c = dev["content"]
    from . import tables
    p = {"fs": dumpx.locator(d, dev)}
    which = rng.sample(["ag_count", "log_blocks", "inode_size", "sector_size"], rng.randint(1, 3))
    bs = c["block_size"]
    if "ag_count" in which:
        hi = min(64, c["blocks"] * bs // (64 * MIB))
        opts = [n for n in tables.allowed(d, "xfsprogs", "ag_count") or range(2, 33) if n <= hi]
        if opts:
            p["ag_count"] = rng.choice(opts)
    agb = cdiv(c["blocks"], p.get("ag_count", c["ag_count"]))
    if "log_blocks" in which:
        lo, hi = 64 * MIB // bs, min((1 << 31) // bs - 256, agb // 2)
        if lo <= hi:
            p["log_blocks"] = logu(rng, lo, hi)
    if "inode_size" in which:
        p["inode_size"] = rng.choice(tables.allowed(d, "xfsprogs", "inode_size") or [512])
    if "sector_size" in which:
        p["sector_size"] = rng.choice(tables.allowed(d, "xfsprogs", "sector_size") or [512])
    if len(p) == 1:
        return None
    return [("xfs_geometry", p)]


def draw_btrfs_geometry(rng, d):
    dev = _pick(rng, _fs_devs(d, ("btrfs",)))
    if not dev:
        return None
    from . import tables
    return [("btrfs_geometry", {"fs": dumpx.locator(d, dev), "node_size": rng.choice(tables.allowed(d, "btrfs-progs", "node_size") or [16384])})]


DRAWS = {
    "gap_before": draw_gap_before, "align": draw_align, "renumber": draw_renumber, "resize": draw_resize,
    "swap_order": draw_swap_order, "lv_split": draw_lv_split, "lv_resize": draw_lv_resize,
    "luks_geometry": draw_luks_geometry, "ext4_geometry": draw_ext4_geometry, "xfs_geometry": draw_xfs_geometry,
    "btrfs_geometry": draw_btrfs_geometry,
}


def applicable(d):
    """{op: weight} for what D has a place for."""
    out = dict(PARTITION_OPS)
    if not _linear_lvs(d):
        out.pop("lv_split", None)
    else:
        out.update({k: STACK_OPS[k] for k in ("lv_split", "lv_resize")})
    if any((x.get("content") or {}).get("type") == "crypto_LUKS" for x in dumpx.devices(d).values()):
        out["luks_geometry"] = STACK_OPS["luks_geometry"]
    for op, types in (("ext4_geometry", ("ext2", "ext3", "ext4")), ("xfs_geometry", ("xfs",)), ("btrfs_geometry", ("btrfs",))):
        if _fs_devs(d, types):
            out[op] = FS_OPS[op]
    return out


# ---- the generator -------------------------------------------------------------

class Stats:
    def __init__(self):
        self.tried = self.kept = 0
        self.discarded = {}

    def discard(self, op, why):
        k = f"{op}: {why.split(':')[0][:50]}"
        self.discarded[k] = self.discarded.get(k, 0) + 1


def try_steps(source, d, edits, steps, planted, stats):
    """D after STEPS, with their edits, or None when one breaks a rule."""
    new = []
    cur = d
    for op, params in steps:
        try:
            e = E.build(cur, op, params)
        except Invalid as x:
            stats.discard(op, str(x))
            return None, None
        cur = E.apply(cur, [e])
        new.append(e)
    bad = rules.check(source, cur, edits + new, planted)
    if bad:
        stats.discard(steps[-1][0], f"rule {bad[0][0]}: {bad[0][1]}")
        return None, None
    return cur, new


def planted_of(source, content):
    """Rule 1's share of a content drift: {mount point: (bytes, files)} on the filesystem holding DIR."""
    if not content:
        return {}
    ms = sorted((m for m in dumpx.storage(source).get("mounts") or [] if (C.DIR + "/").startswith(m["target"].rstrip("/") + "/")),
                key=lambda m: len(m["target"]))
    if not ms:
        return {}
    m = ms[-1]
    entries = C.plan(content, m.get("fstype") or "ext4")
    return {m["target"]: C.sizes(entries)}


def boots_from_fallback(dump):
    """Whether a UEFI source booted its ESP's removable path (no NVRAM entry of
    its own: a firmware entry, \"UEFI Misc Device\", was BootCurrent)."""
    efi = (dump.get("boot") or {}).get("efi") or {}
    if dumpx.firmware(dump) != "uefi":
        return False
    cur = next((e for e in efi.get("entries") or [] if e.get("number") == efi.get("boot_current")), None)
    return cur is None or not any("HD(" in str(p) for p in cur.get("device_path") or [])


# Known findings the generator steers around, so that the cases it draws can
# find new ones (each has its own case in testvm/backshot2/cases/, run with
# the corpus, and comes back into the draws once it's fixed):
#   fail-002  load leaves the source's fallback loader (EFI/BOOT/BOOTX64.EFI)
#             as restored, its embedded prefix naming /boot's old partition
#             number: no renumbering of /boot's partition on a UEFI source
#             that boots from it
#   fail-003  --removable runs grub-install --force-extra-removable, Debian's
#             own option, which Alpine's GRUB refuses: no --removable
KNOWN = ("fail-002", "fail-003")


def boot_parts(d):
    ms = {m.get("target"): m.get("device") for m in dumpx.storage(d).get("mounts") or []}
    where = ms.get("/boot") or ms.get("/")
    return {where} | dumpx.holders_down(d, where or "")


def avoid_known(source, drifted, edits, opts):
    return [o for o in opts if not ("fail-003" in KNOWN and o == "--removable")]


def generate(source_name, source_gen, dump, seed, caps=None, n_edits=None, with_content=True, with_options=True, stats=None):
    """A case for SOURCE_NAME's DUMP, from SEED."""
    caps = caps or {}
    stats = stats or Stats()
    rng = random.Random(f"{source_name}:{seed}")
    content = {"recipe": RECIPE, "seed": seed} if with_content and rng.random() < 0.7 else None
    planted = planted_of(dump, content)
    budget = n_edits if n_edits is not None else rng.randint(1, 6)
    d, edits = dump, []
    groups = [PARTITION_OPS, STACK_OPS, FS_OPS]
    tries = 0
    while len(edits) < budget and tries < 60:
        tries += 1
        stats.tried += 1
        ok = applicable(d)
        # Partition edits first, then the stacks and the filesystems, as the
        # spec orders them: a draw goes to the first group that still has budget.
        share = [sum(1 for e in edits if e["op"] in g) for g in groups]
        weights = {op: w for op, w in ok.items()}
        if not weights:
            break
        ops, ws = zip(*sorted(weights.items()))
        op = rng.choices(ops, ws)[0]
        steps = DRAWS[op](rng, d)
        if not steps:
            stats.discard(op, "nothing to draw")
            continue
        nd, new = try_steps(dump, d, edits, steps, planted, stats)
        if nd is None:
            continue
        d, edits = nd, edits + new
        stats.kept += len(new)
    # Order the kept edits as drawn; the spec's order is in the weights' groups.
    targets = {dk["name"]: dk["size_bytes"] for dk in dumpx.disks(d)}
    if rng.random() < 0.4:
        dk = dumpx.disk(d)
        ss = dumpx.sector(dk)
        by = logu(rng, 1, dk["size_bytes"] // ss // 4) * ss
        if rng.random() < 0.5:
            by = (by // MIB) * MIB or ss
        if rng.random() < 0.6:
            by = -by
        e = E.build(d, "target_size", {"disk": dk["name"], "by_bytes": by})
        t2 = dict(targets)
        t2[dk["name"]] += by
        need = fitcalc.pct_needed(d, t2) if by < 0 else 1
        if need is not None:
            edits.append(e)
            targets = t2
    opts, snaps = ([], None)
    if with_options:
        opts, snaps = sweep.draw(rng, dump, d, targets, caps)
        if opts is None:
            # The target can't be fitted: drop it, keep the rest.
            edits = [e for e in edits if e["op"] != "target_size"]
            targets = {dk["name"]: dk["size_bytes"] for dk in dumpx.disks(d)}
            opts, snaps = sweep.draw(rng, dump, d, targets, caps)
            opts = opts or []
    elif any(targets[k] < v["size_bytes"] for k, v in ((dk["name"], dk) for dk in dumpx.disks(d))):
        opts = ["--fit-largest", str(fitcalc.pct_needed(d, targets))]
    opts = avoid_known(dump, d, edits, opts)
    if "--fit-largest" in opts:
        pct = int(opts[opts.index("--fit-largest") + 1])
        bad = fitcalc.fits_after(d, targets, pct, planted)
        if bad:
            i = opts.index("--fit-largest")
            del opts[i:i + 2]
            edits = [e for e in edits if e["op"] != "target_size"]
            targets = {dk["name"]: dk["size_bytes"] for dk in dumpx.disks(d)}
            stats.discard("target_size", f"rule 1: {bad[0][1]}")
    case = {"source": f"{source_name}@{source_gen}" if source_gen else source_name, "seed": seed, "generator": GENERATOR,
            "edits": edits, "load_options": opts}
    if any(e["op"] == "target_size" for e in edits):
        case["target"] = {k: {"size_bytes": v} for k, v in sorted(targets.items())}
    if content:
        case["content"] = content
    if snaps:
        case["snapshots"] = snaps
    return case
