"""The option sweep (backshot2.md, "Option sweep"): load's flags, drawn per case.

- --new-uuids, when the bko-holt under test has it (caps["new_uuids"]: tier 0
  finds out once per build; load says "isn't built yet" until it is);
- --no-nvram and --removable on a UEFI source.  Tier 1 boots each VM with a
  fresh NVRAM, so it adds --no-nvram itself there, as HoltTest1 does; tier 2
  keeps the NVRAM and runs exactly the case's flags;
- --fit-largest PCT whenever the target is smaller than the layout's disk
  (load refuses it without), and sometimes when it's bigger;
- --snapshot of an older backup: the case's "snapshots" ({"count": N,
  "restore": I}, I counted from the oldest, 0) has the source backed up N
  times with content changed between, and load's option is "--snapshot @I",
  which the tier 1 runner turns into that snapshot's ID;
- --reformat MOUNT=TYPE (drawn since GENERATOR "9", approved 2026-11-26;
  decision 4): in REFORMAT_P of the cases, one mount of the drifted
  layout's fstab made another of ext4, XFS and btrfs, among those
  reformat_choices() finds load is meant to take (docs/design/reformat.md,
  holt/src/load/reformat.rs): so a refusal at tier 0 or 1 is a finding.
  It's drawn from a stream of its own (the case's "SOURCE:SEED:reformat"),
  after every other draw, so a case without it is GENERATOR 8's but for
  the "generator" field.  Never with --fit-largest (load refuses the two
  together) nor --new-uuids (judge.py's reformat_expect finds the new
  filesystem by the source's UUID).  expected() carries it to the judges
  (bs2-host apply's BS2_REFORMAT, ht1-bs2.sh's HT_REFORMAT), tier 0 makes
  its new filesystem with the source's own tools (tier0.dry_runs), and the
  planter caps the long symlink for the new type (reformatted()).

A source's family and traits (bs2lib/families) may take options out
(their `sweep`: --reformat on ostree, ABRoot, Ubuntu Core, NixOS and
Guix); those a known finding rules out are the generator's steer.

What each flag changes on purpose, for the judges (B9): expected(case) below.
"""

import random
import re

from . import dumpx, fitcalc, geometry

# How often a case draws --reformat (among those with a mount load takes):
# about one in seven, as --new-uuids and --snapshot.  A reformat throws the
# filesystem's drifted geometry away (load makes the new type with mkfs's
# own), so it stays a minority of the cases a source draws: the rest keep
# testing that load makes each geometry as the layout has it.  Over a
# 200-case tier 1 sweep that is still ~30 reformats.
REFORMAT_P = 0.15
# What load converts between (reformat.rs TYPES), and the new type's
# fsck and repair, which the source must have (its "no_fsck" refusal).
REFORMAT_TYPES = ("ext4", "xfs", "btrfs")
REFORMAT_TOOLS = {"ext4": "e2fsprogs", "xfs": "xfsprogs", "btrfs": "btrfs-progs"}
# The initramfs generators load remakes the image with for a new root type
# (reformat.md's three, and Slackware's mkinitrd and geninitrd, which
# commands.rs makes again for it since 8ff49dc).  The rest keep an image
# that mounts the old type (prebuilt, a store item) or one load doesn't
# read back for the new module (booster).  Alpine's mkinitfs takes ext4
# only: its image has what mkinitfs.conf's features list, which the dump
# doesn't record, and ext4 is in mkinitfs's default list.
ROOT_GENERATORS = {"dracut": REFORMAT_TYPES, "mkinitcpio": REFORMAT_TYPES, "initramfs-tools": REFORMAT_TYPES,
                   "mkinitrd": REFORMAT_TYPES, "geninitrd": REFORMAT_TYPES, "mkinitfs": ("ext4",)}
# The loaders whose kernel command lines boot's `bko-holt rewrite` takes
# (reformat.md's table, the port's rEFInd, Slackware's ELILO and LILO).
ROOT_LOADERS = ("grub", "systemd-boot", "limine", "refind", "elilo", "lilo")
# The types the filesystem holding the kernels (/boot's, else /'s) may be
# made, by loader: GRUB reads it, and load makes ext4 only under it
# ("grub_reads"); LILO maps the kernel by its sectors on it, so ext4 only
# here too (a steer of ours: load doesn't refuse it); systemd-boot, Limine
# and ELILO read only the ESP, so a root holding /boot is free (a /boot of
# its own that isn't the ESP, an XBOOTLDR, isn't drawn); rEFInd reads it
# with its own EFI driver, which load doesn't change: never drawn.
KERNEL_FS = {"grub": ("ext4",), "lilo": ("ext4",), "systemd-boot": REFORMAT_TYPES, "limine": REFORMAT_TYPES,
             "elilo": REFORMAT_TYPES}
# The smallest filesystem drawn: mkfs.xfs refuses one under 300 MiB, a
# btrfs under ~110 MiB, and mke2fs makes a "small" one (1 KiB blocks)
# under 512 MiB, whose long symlinks the planter would have to know.
REFORMAT_MIN_BYTES = 1 << 30
# The longest label each new type keeps (reformat.rs new_label): a longer
# one is cut, which an fstab LABEL= would no longer find.
LABEL_MAX = {"xfs": 12, "ext4": 16, "btrfs": 255}
FS_OPS = ("ext4_geometry", "xfs_geometry", "btrfs_geometry")


def draw(rng, source, drifted, targets, caps, hooks=None, edits=(), planted=None, content=None, reformat_seed=None):
    """(load_options, snapshots or None) for a case.  HOOKS (the source's,
    families.hooks) may narrow the options to what the source can take.
    REFORMAT_SEED (generate's "SOURCE:SEED:reformat") seeds --reformat's
    own stream; None draws none.  EDITS, PLANTED (rule 1's share of the
    content drift, generate.planted_of) and CONTENT (the case's) are what
    reformat_choices() reads."""
    opts = []
    uefi = dumpx.firmware(source) == "uefi"
    if caps.get("new_uuids") and rng.random() < 0.15:
        opts.append("--new-uuids")
    if uefi and rng.random() < 0.2:
        opts.append("--removable")
    if uefi and rng.random() < 0.25:
        opts.append("--no-nvram")
    smaller = any(targets[dk["name"]] < dk["size_bytes"] for dk in dumpx.disks(drifted))
    bigger = any(targets[dk["name"]] > dk["size_bytes"] for dk in dumpx.disks(drifted))
    if smaller or (bigger and rng.random() < 0.5):
        need = fitcalc.pct_needed(drifted, targets)
        if need is None:
            return None, None
        pct = min(50, need + rng.choice([0, 0, 1, 2, 5, 10]))
        if pct < need:
            return None, None
        opts += ["--fit-largest", str(max(1, pct))]
    snaps = None
    if rng.random() < 0.15:
        n = rng.choice([2, 3])
        snaps = {"count": n, "restore": rng.randrange(0, n - 1)}
        opts += ["--snapshot", f"@{snaps['restore']}"]
    if reformat_seed is not None and "--fit-largest" not in opts and "--new-uuids" not in opts:
        r = random.Random(reformat_seed)
        if r.random() < REFORMAT_P:
            choices = reformat_choices(source, drifted, edits, planted, content)
            if choices:
                m, t = r.choice(choices)
                opts += ["--reformat", f"{m}={t}"]
    if hooks is not None:
        opts = hooks.sweep(source, opts)
    return opts, snaps


# ---- --reformat: what load takes ------------------------------------------------------

def _version(v):
    """reformat.rs kernel_version: "6.18.60-1-lts" -> (6, 18); None unless it starts with two numbers."""
    w = re.split(r"[^0-9]", str(v or ""))
    try:
        return int(w[0]), int(w[1])
    except (IndexError, ValueError):
        return None


def oldest_kernel(d):
    """reformat.rs oldest_kernel: the lowest of those installed and the one it ran."""
    b = d.get("boot") or {}
    vs = [k.get("version") for k in (b.get("kernels") or {}).get("installed") or [] if isinstance(k, dict)]
    vs.append((d.get("os") or {}).get("kernel_release"))
    have = [x for x in (_version(v) for v in vs if v) if x]
    return min(have) if have else None


def _under(path, top):
    return top == "/" or path == top or path.startswith(top.rstrip("/") + "/")


def _root_boot_ok(d):
    """reformat.rs root_boot_refusal, None: what boots / lets load make it another type
    (no LILO or ELILO append= naming the root's type or options; a Slackware initrd load
    can make again for it)."""
    b = d.get("boot") or {}
    appends = []
    for conf in ([b["lilo"]] if b.get("lilo") else []) + list((b.get("elilo") or {}).get("configs") or []):
        for x in [conf] + list(conf.get("images") or []):
            appends += [x.get(k) for k in ("append", "addappend") if isinstance(x.get(k), str)]
    if any(w.startswith(("rootfstype=", "rootfs=", "rootflags=")) for a in appends for w in a.split()):
        return False
    ir = b.get("initramfs") or {}
    if ir.get("generator") == "mkinitrd":
        m = ir.get("mkinitrd") or {}
        words = str(m.get("command_line") or "").split()
        if not words or words[0].rsplit("/", 1)[-1] != "mkinitrd" or "-F" in words or not m.get("kernels"):
            return False
    if ir.get("generator") == "geninitrd" and (ir.get("geninitrd") or {}).get("runs") != "mkinitrd_command_generator.sh":
        return False
    return True


def _geometry_edited(source, edits):
    """The UUIDs of the filesystems a geometry edit drew (an edit names its
    filesystem by the locator it had then: each is resolved as it applies)."""
    from . import edits as E
    out, cur = set(), source
    for e in edits or ():
        if e.get("op") in FS_OPS:
            x = dumpx.resolve(cur, str(e.get("fs")))
            if x is not None:
                out.add((x.get("content") or {}).get("uuid"))
        cur = E.apply(cur, [e])
    return out


def _fits(d, dev, c, frm, to, entries, planted):
    """Whether what DEV's filesystem holds (and the content planted on it)
    fits a new TO filesystem of the whole device, with room to spare: load
    has no space check yet (reformat.md), a btrfs's compressed bytes come
    back whole, and a new ext4's inodes are mke2fs's default ratio's."""
    size = dumpx.size_of(dev)
    used = dumpx.used_bytes(d, dev)
    if used is None:
        return False
    compressed = frm == "btrfs" and any("compress" in str(e.get("options") or "") for e in entries)
    extra = files = 0
    for m in dumpx.storage(d).get("mounts") or []:
        if m.get("device") == dev["name"] and m.get("target") in (planted or {}):
            extra += planted[m["target"]][0]
            files += planted[m["target"]][1]
    blocks = size // 4096
    if to == "ext4":
        over = geometry.ext4_overhead(blocks, 4096, size // 16384, 256) + size // 20
    elif to == "xfs":
        over = geometry.xfs_overhead(blocks, 4096, 4, 16384)
    else:
        over = size // 10 + (256 << 20)
    if int(used * (2.5 if compressed else 1.25)) + extra + over > size * 0.9:
        return False
    ui = dumpx.used_inodes(dev)
    if to == "ext4" and ui is not None and int(ui * 1.25) + files + 16 > size // 16384:
        return False
    return True


def reformat_choices(source, drifted, edits=(), planted=None, content=None):
    """[(MOUNT, TYPE)]: each --reformat MOUNT=TYPE load is meant to take on
    DRIFTED (the layout it's given), as reformat.rs works it out, and what
    BackShot2 can judge of it.  A MOUNT is an fstab entry's (not swap):
    - its filesystem ext4, XFS or btrfs (not ext2/3, f2fs, ZFS, the ESP),
      with a UUID, a btrfs on one device, every other mount of it under
      MOUNT (they fold into it), and TYPE another of the three;
    - the source has TYPE's fsck (boot.packages) and a known kernel (XFS:
      4.19 or later), and doesn't hibernate into a swap file on it;
    - / (or a mount holding it): an initramfs generator load remakes for
      the new type (ROOT_GENERATORS), a loader whose command lines boot
      rewrites (ROOT_LOADERS), and no LILO/ELILO append= or Slackware initrd
      load refuses (root_boot_refusal);
    - the filesystem holding the kernels: KERNEL_FS's types for the loader;
    - at least REFORMAT_MIN_BYTES, what it holds fitting the new type
      (_fits), its label within TYPE's limit, and no geometry edit of the
      case on it (a geometry the reformat throws away);
    - with content planted on it, an XFS only with bigtime (its kernel's
      mkfs.xfs profile, 5.15 on) unless the recipe drops post-2038 times.
      (The long symlink's limit is the planter's: reformatted().)
    The layout-wide refusals (ostree, ABRoot, the store systems) are the
    families' sweep hooks."""
    if (drifted.get("os") or {}).get("family") not in (None, "linux"):
        return []
    st = dumpx.storage(drifted)
    b = drifted.get("boot") or {}
    fstab = [e for e in st.get("fstab") or [] if e.get("type") != "swap"]
    devs = dumpx.devices(drifted)
    pk = b.get("packages") if isinstance(b.get("packages"), dict) else {}
    kv = oldest_kernel(drifted)
    if kv is None:
        return []
    loader = (b.get("loader") or {}).get("name")
    gen = (b.get("initramfs") or {}).get("generator")
    on = lambda f: next((e.get("resolves_to") for e in fstab if e.get("file") == f), None)
    kernel_dev = on("/boot") or on("/")
    kernel_mount = "/boot" if on("/boot") else "/"
    resume_offset = ((b.get("cmdline") or {}).get("storage") or {}).get("resume_offset") is not None
    geo = _geometry_edited(source, edits)
    recipe_drops = set()
    if content:
        from . import content as CT
        try:
            recipe_drops = set(CT.recipe_of(content["recipe"]).get("drop") or ())
        except KeyError:
            pass
    out, seen = [], set()
    for e in fstab:
        mount, src = e.get("file"), e.get("resolves_to")
        if not isinstance(mount, str) or not mount.startswith("/") or mount in seen or not src:
            continue
        seen.add(mount)
        dev = devs.get(src)
        c = (dev or {}).get("content") or {}
        frm, uuid = c.get("type"), c.get("uuid")
        if frm not in REFORMAT_TYPES or not uuid or uuid in geo:
            continue
        if frm == "btrfs":
            fs = next((x for x in st.get("btrfs") or [] if x.get("uuid") == uuid), {})
            if len(fs.get("devices") or []) > 1:
                continue
        mine = [x for x in fstab if x.get("resolves_to") == src]
        if any(not _under(str(x.get("file")), mount) for x in mine):
            continue
        files = {x.get("file") for x in mine}
        if dumpx.size_of(dev) < REFORMAT_MIN_BYTES:
            continue
        if any(s.get("type") == "file" and _under(str(s.get("file")), mount) for s in st.get("swaps") or []) and resume_offset:
            continue
        holds_root = "/" in files
        planted_here = any(m.get("device") == src and m.get("target") in (planted or {}) for m in st.get("mounts") or [])
        for to in REFORMAT_TYPES:
            if to == frm or pk.get(REFORMAT_TOOLS[to]) is None:
                continue
            if to == "xfs" and kv < (4, 19):
                continue
            if holds_root and (loader not in ROOT_LOADERS or to not in ROOT_GENERATORS.get(gen, ()) or not _root_boot_ok(drifted)):
                continue
            if src == kernel_dev:
                ok = KERNEL_FS.get(loader, ())
                if kernel_mount == "/boot" and loader not in ("grub", "lilo"):
                    ok = ()
                if to not in ok:
                    continue
            label = c.get("label")
            if isinstance(label, str) and len(label.encode()) > LABEL_MAX[to]:
                continue
            if planted_here and to == "xfs" and kv < (5, 15) and "post2038" not in recipe_drops:
                continue
            if not _fits(drifted, dev, c, frm, to, mine, planted):
                continue
            out.append((mount, to))
    return out


def reformatted(layout, reformats):
    """LAYOUT (a drifted layout) as load's --reformat leaves it for what the
    planter asks (content.symlink_max): each MOUNT=TYPE of REFORMATS (as
    expected() has them) the new type on its device, with mkfs's default
    block (ext4: 4 KiB, at REFORMAT_MIN_BYTES and up) or node size (btrfs:
    16 KiB).  Every mount of that device sees it, the folded ones too."""
    if not reformats:
        return layout
    x = dumpx.clone(layout)
    devs = dumpx.devices(x)
    for r in reformats:
        mount, _, to = str(r).partition("=")
        src = next((e.get("resolves_to") for e in dumpx.storage(x).get("fstab") or []
                    if e.get("file") == mount and e.get("type") != "swap"), None)
        dev = devs.get(src)
        if dev is None:
            continue
        c = dev.get("content") or {}
        new = {"type": to, "uuid": c.get("uuid"), "label": c.get("label"), "reformatted_from": c.get("type")}
        if to == "ext4":
            new["block_size"] = 4096
        elif to == "btrfs":
            new["node_size"] = 16384
        dev["content"] = new
    return x


def expected(case):
    """What the judges must allow, from the case's flags: a dict of
    {"new_uuids": bool, "no_nvram": bool, "removable": bool,
     "fit": PCT or None, "snapshot": index or None,
     "reformat": [MOUNT=TYPE, ...]: each --reformat's value, in order}."""
    o = case.get("load_options") or []
    fit = None
    if "--fit-largest" in o:
        fit = int(str(o[o.index("--fit-largest") + 1]).rstrip("%"))
    snap = None
    if "--snapshot" in o:
        v = o[o.index("--snapshot") + 1]
        snap = int(v[1:]) if str(v).startswith("@") else v
    reformat = [str(o[i + 1]) for i, x in enumerate(o[:-1]) if x == "--reformat"]
    return {"new_uuids": "--new-uuids" in o, "no_nvram": "--no-nvram" in o, "removable": "--removable" in o,
            "fit": fit, "snapshot": snap, "reformat": reformat}
