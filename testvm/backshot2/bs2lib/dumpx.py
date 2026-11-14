"""Reading a bko-holt dump the way the generator and the edits need it.

Every function here only reads; edits.py does the writing.  A device is a
record that can hold content: a partition (storage.disks[].partitions[]), a
device-mapper device (storage.dm_devices[]) or an md array
(storage.md_arrays[]).  Names are the kernel's (vda3, dm-1, md0).
"""

import copy
import json
import re

MIB = 1 << 20
GIB = 1 << 30
# The partition table types phase 1 drifts.
TABLES = ("dos", "gpt")


def load(path):
    with open(path) as f:
        return json.load(f)


def clone(d):
    return copy.deepcopy(d)


def human(n):
    """bko-holt's own size words (holt/src/report.rs human()): 1536 -> 1.5 KiB."""
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"]
    v, i = float(n), 0
    while v >= 1024.0 and i < len(units) - 1:
        v /= 1024.0
        i += 1
    if i == 0:
        return f"{n} B"
    if v >= 100.0 or abs(v - round(v)) < 0.05:
        return f"{int(round(v))} {units[i]}"
    return f"{v:.1f} {units[i]}"


def storage(d):
    return d.get("storage") or {}


def disks(d):
    """The dumped disks load maps (not RAM disks, not zvols): golden_plans.rs's dumped_disks."""
    out = []
    for x in storage(d).get("disks") or []:
        if x.get("kind") == "ram":
            continue
        if any(str(l).startswith("/dev/zvol/") for l in x.get("links") or []):
            continue
        out.append(x)
    return out


def disk(d, name=None):
    """The disk NAME, or the first dumped disk."""
    ds = disks(d)
    if name is None:
        return ds[0] if ds else None
    return next((x for x in ds if x.get("name") == name), None)


def table(dk):
    return dk.get("partition_table") or {}


def sector(dk):
    return int(table(dk).get("sector_size") or dk.get("logical_sector_size") or 512)


def parts(dk, in_disk_order=True):
    ps = [p for p in dk.get("partitions") or []]
    return sorted(ps, key=lambda p: p["start_lba"]) if in_disk_order else ps


def part(dk, number):
    return next((p for p in dk.get("partitions") or [] if p.get("number") == number), None)


def end(p):
    """The first sector after P."""
    return p["start_lba"] + p["sectors"]


def first_usable(dk):
    t = table(dk)
    if t.get("type") == "gpt":
        return int(t.get("first_usable_lba") or 34)
    return 1


def last_usable(dk):
    """The last sector a partition may use."""
    t = table(dk)
    if t.get("type") == "gpt" and t.get("last_usable_lba"):
        return int(t["last_usable_lba"])
    return dk["size_bytes"] // sector(dk) - 1


def space_after(dk, p):
    """Free sectors right after P, up to the next partition (disk order) or the last usable sector."""
    nxt = [q["start_lba"] for q in dk.get("partitions") or [] if q["start_lba"] > p["start_lba"] and not q.get("extended")]
    lim = min(nxt) if nxt else last_usable(dk) + 1
    return lim - end(p)


def tail_free(dk):
    ps = [p for p in dk.get("partitions") or [] if not p.get("extended")]
    if not ps:
        return last_usable(dk) + 1 - first_usable(dk)
    return last_usable(dk) + 1 - max(end(p) for p in ps)


def devices(d):
    """{name: device record}: every partition, dm device and md array."""
    out = {}
    for dk in storage(d).get("disks") or []:
        for p in dk.get("partitions") or []:
            out[p["name"]] = p
    for x in storage(d).get("dm_devices") or []:
        out[x["name"]] = x
    for x in storage(d).get("md_arrays") or []:
        out[x["name"]] = x
    return out


def disk_of_part(d, name):
    for dk in storage(d).get("disks") or []:
        for p in dk.get("partitions") or []:
            if p["name"] == name:
                return dk
    return None


def size_of(dev):
    """A device's size in bytes."""
    if "sectors" in dev and "start_lba" in dev:
        return dev.get("size_bytes") or dev["sectors"] * 512
    return dev.get("size_bytes") or 0


def children(d, name):
    """The devices built directly on NAME (dm devices and md arrays whose slaves hold it)."""
    out = []
    for x in (storage(d).get("dm_devices") or []) + (storage(d).get("md_arrays") or []):
        if name in (x.get("slaves") or []):
            out.append(x)
    return out


def vgs(d):
    return (storage(d).get("lvm") or {}).get("volume_groups") or []


def vg(d, name):
    return next((v for v in vgs(d) if v.get("name") == name), None)


def lv(v, name):
    return next((x for x in v.get("logical_volumes") or [] if x.get("name") == name), None)


def lv_device(d, vgname, lvname):
    for x in storage(d).get("dm_devices") or []:
        dm = x.get("dm") or {}
        if dm.get("target") == "lvm" and dm.get("vg") == vgname and dm.get("lv") == lvname:
            return x
    return None


def pv_device(d, pvrec):
    return devices(d).get(pvrec.get("device"))


def resolve(d, loc):
    """A device by locator: a kernel name (vda3, dm-1, md0), VG/LV, or a dm name (cryptroot)."""
    devs = devices(d)
    if loc in devs:
        return devs[loc]
    if "/" in loc:
        a, b = loc.split("/", 1)
        x = lv_device(d, a, b)
        if x:
            return x
    for x in storage(d).get("dm_devices") or []:
        if (x.get("dm") or {}).get("dm_name") == loc:
            return x
    return None


def locator(d, dev):
    """The stable locator of DEV: VG/LV for an LV, its kernel name otherwise."""
    dm = dev.get("dm") or {}
    if dm.get("target") == "lvm":
        return f"{dm['vg']}/{dm['lv']}"
    return dev["name"]


FS_TYPES = ("ext2", "ext3", "ext4", "xfs", "btrfs", "vfat", "f2fs", "jfs", "nilfs2")


def filesystems(d):
    """[(device, content)] for every device holding a filesystem or swap."""
    out = []
    for dev in devices(d).values():
        c = dev.get("content") or {}
        if c.get("type") in FS_TYPES + ("swap",):
            out.append((dev, c))
    return out


def mount_of(d, dev):
    """The mounts of DEV (by its kernel name)."""
    return [m for m in storage(d).get("mounts") or [] if m.get("device") == dev["name"]]


def used_bytes(d, dev):
    """What the filesystem on DEV holds, in bytes: its mount's used_bytes, or
    from its block counts when it isn't mounted.  None for no filesystem."""
    c = dev.get("content") or {}
    ms = [m for m in mount_of(d, dev) if m.get("used_bytes") is not None]
    if ms:
        return max(m["used_bytes"] for m in ms)
    t = c.get("type")
    if t in ("ext2", "ext3", "ext4", "xfs") and c.get("blocks") is not None and c.get("free_blocks") is not None:
        return (c["blocks"] - c["free_blocks"]) * c["block_size"]
    if t == "vfat":
        # Unmounted ESP: no usage recorded; the whole of what its files hold.
        esp = [e for e in (d.get("boot") or {}).get("esps") or [] if e.get("device") == dev["name"]]
        files = (esp[0].get("contents") or {}).get("files") if esp else None
        if files:
            return sum(int(f.get("size") or 0) for f in files)
        return (c.get("total_sectors") or 0) * (c.get("bytes_per_sector") or 512) // 2
    if t == "btrfs" and c.get("bytes_used") is not None:
        return c["bytes_used"]
    if t == "swap":
        return 0
    return None


def used_inodes(dev):
    c = dev.get("content") or {}
    if c.get("inodes") is not None and c.get("free_inodes") is not None:
        return c["inodes"] - c["free_inodes"]
    return None


def all_strings(v):
    """Every string in V, recursively."""
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from all_strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from all_strings(x)


def _hooks(d, hooks):
    if hooks is None:
        from .families import hooks as of
        hooks = of(None, d)
    return hooks


def config_texts(d, hooks=None):
    """The texts of the source's own configuration the dump records: what
    names devices on the restored system (rule 3).  fstab and crypttab
    specs and options, the kernel command line, /etc/kernel/cmdline,
    GRUB's settings and its configuration's linux line, resume=, mdadm.conf;
    then what the source's family and traits add (HOOKS, families.hooks):
    loader entries (systemd_boot.py, ostree.py), refind_linux.conf, ..."""
    st, boot = storage(d), d.get("boot") or {}
    out = []
    for e in st.get("fstab") or []:
        out += [str(e.get("spec") or ""), str(e.get("options") or "")]
    for e in st.get("crypttab") or []:
        out += [str(e.get("device") or ""), str(e.get("key_file") or "")] + [str(o) for o in e.get("options") or []]
    out.append(str((boot.get("cmdline") or {}).get("raw") or ""))
    out.append(json.dumps(boot.get("kernel_cmdline_file")))
    grub = boot.get("grub") or {}
    out += [str(v) for v in (grub.get("default_grub") or {}).values()]
    cfg = grub.get("grub_cfg") or {}
    out += [str(cfg.get("default_linux") or "")]
    out.append(json.dumps((boot.get("initramfs") or {}).get("resume")))
    out.append(json.dumps(st.get("mdadm_conf")))
    return out + _hooks(d, hooks).config_texts(d)


def names_by_number(d, dk, p, hooks=None):
    """Where the source's configuration names partition P by its number or
    device path (rule 3), as a list of what was found.  HOOKS (the
    source's, families.hooks) add texts, patterns and finders of their own."""
    hooks = _hooks(d, hooks)
    num = p["number"]
    pname = p["name"]
    pats = [
        rf"(?<![A-Za-z0-9]){re.escape(pname)}(?![0-9])",
        rf"\(hd\d+,(gpt|msdos)?{num}\)",
    ]
    if table(dk).get("type") == "dos" and p.get("uuid"):
        # An MBR partition's PARTUUID is the disk signature and its number.
        pats.append(re.escape(str(p["uuid"])))
    pats += hooks.number_patterns(d, dk, p)
    found = []
    for t in config_texts(d, hooks):
        for pat in pats:
            if re.search(pat, t):
                found.append(f"{pat!s} in {t[:80]!r}")
    # GRUB that finds /boot (or /) by number: no search UUIDs at all.  (Only
    # a GRUB source's: a dump records boot.grub, empty, for every loader.)
    grub = (d.get("boot") or {}).get("grub") or {}
    cfg = grub.get("grub_cfg") or {}
    is_grub = (((d.get("boot") or {}).get("loader") or {}).get("name") or "grub") == "grub" or bool(cfg)
    if grub and is_grub and not cfg.get("search_uuids"):
        ms = {m.get("target"): m.get("device") for m in storage(d).get("mounts") or []}
        where = ms.get("/boot") or ms.get("/")
        if where and (pname == where or pname in holders_down(d, where)):
            found.append("GRUB has no search UUIDs: it finds /boot by its partition number")
    return found + hooks.names_by_number(d, dk, p)


def holders_down(d, name):
    """The devices NAME sits on, down to the partitions."""
    out, todo, devs = set(), [name], devices(d)
    while todo:
        n = todo.pop()
        x = devs.get(n)
        if not x:
            continue
        for s in x.get("slaves") or []:
            if s not in out:
                out.add(s)
                todo.append(s)
        for m in (x.get("md") or {}).get("members") or []:
            s = m.get("device") or m.get("name")
            if s and s not in out:
                out.add(s)
                todo.append(s)
    return out


def boot_on_lv(d):
    """Whether /boot (or / without a /boot) is on an LV."""
    ms = storage(d).get("mounts") or []
    b = next((m for m in ms if m.get("target") == "/boot"), None) or next((m for m in ms if m.get("target") == "/"), None)
    if not b:
        return False
    x = devices(d).get(b.get("device"))
    return bool(x and (x.get("dm") or {}).get("target") == "lvm")


def esp_devices(d):
    return {e.get("device") for e in (d.get("boot") or {}).get("esps") or []}


def firmware(d):
    return ((d.get("firmware") or {}).get("mode")) or "bios"
