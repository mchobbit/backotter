#!/usr/bin/env python3
"""Compare two bko-holt dumps of one machine: the source's, and the restored
copy's after it booted.  Prints one DIFF line per field that differs, and
exits 1 if any field outside IGNORED differs.

Usage: judge.py [--expect] [--new-uuids] [--removable] [--nvram-kept]
                [--grow BYTES | --fit BYTES [--fit-pct PCT] | --degraded] SRC.json DST.json
       judge.py fs FSMETA.py SRC-META.json DST-META.json [--no-atime] [--new-uuids]
       judge.py --fit-plan BYTES [--fit-pct PCT] LAYOUT.json

The fs form judges fsmeta.py's records of the source and the restored disk:
every path must match but what load's boot stage writes by design (BOOT_STAGE).

--grow BYTES is for a restore onto disks BYTES bigger than the source's: a
disk's size, GPT's last usable LBA and backup header, and the protective MBR's
partition may then differ by exactly that much, and nothing else may.

--fit BYTES is for a restore onto disks BYTES bigger, or smaller when BYTES is
negative, with `load --fit-largest PCT`, which fits the layout by contracting
or expanding the largest partition of each disk.  Besides --grow's fields, that
partition's size, the start of the partitions after it, and the numbers that
follow from the new size (block counts, a filesystem's geometry, LUKS's and
LVM's sizes) may differ, each by the amount judge.py works out for itself the
way load does; nothing else may.  A disk that was resized and whose partitions
were not is a DIFF too, so the mode can't pass a run that fitted nothing.
--fit-pct PCT is load's own PCT, which caps how far a partition may expand.

--degraded is for a source dumped with a RAID member's disk detached, and
restored onto one disk fewer: each dump must show an md array with an empty
slot, and the same arrays with the same slots empty.

--expect is BackShot2's (docs/design/backshot2.md, "Oracles"): SRC.json is
then the drifted layout the case asked load to build (its edits applied to
the source's dump), not a dump of a running machine, and the restored dump
must match it.  The few fields a drifted layout can't predict are EXPECT_IGNORED
(the topology's words, a partition's kernel device number).

BackShot2's option sweep (B9) adds what each of load's flags changes on purpose:
--new-uuids    every identifier is new: each source identifier must map to one
               new value, the same wherever it appears, and differ from the old
               one; the fs form allows fstab, crypttab and the boot settings
               that name them
--removable    load also installed the ESP's removable-media loader: EFI/BOOT/
               files on the ESP may be new or rewritten
--nvram-kept   tier 2's boots shared one NVRAM with the rescuer: the firmware's
               own boot entries (no HD() path: its disks, network, shell) and
               the boot order and current entry are the rescue session's; the
               entries on a disk (HD()) are compared, as a set

IGNORED lists what may differ between two boots of the same machine, or can't
be kept by a restore, each with why.  judge.py prints which of them did differ,
so the list can't grow without anyone seeing it.
"""

import json
import re
import sys


def load_json(path):
    """A record, plain or zstd-compressed (a pass keeps fsmeta.py's as .zst)."""
    if not str(path).endswith(".zst"):
        with open(path) as f:
            return json.load(f)
    try:
        from compression import zstd  # Python 3.14
    except ImportError:  # the hosts' 3.13: the zstd command
        import subprocess
        return json.loads(subprocess.run(["zstd", "-dc", path], check=True, capture_output=True).stdout)
    with zstd.open(path) as f:
        return json.load(f)


# Field-path patterns (dot-separated, list indexes as numbers) and why
# they may differ.
IGNORED = [
    (r"^generated$", "when the dump was made"),
    (r"^tool\.version$", "the bko-holt build"),
    (r"(^|\.)(used_bytes|available_bytes|free_blocks|free_inodes|bytes_used)$", "usage changes as the machine runs"),
    (r"(^|\.)(mkfs_time|creation_time|update_time|generation|events)$", "when a filesystem or array was made or last written"),
    (r"^storage\.btrfs\.\d+\.(subvolumes\.\d+|default_subvolume)\.gen$", "a btrfs subvolume's last-written generation"),
    (r"^storage\.btrfs\.\d+\.qgroups\.\d+\.(referenced|exclusive)$", "usage changes as the machine runs (and a restored snapshot shares nothing with its origin)"),
    (r"^storage\.disks\.\d+\.boot_code\.post_mbr_gap\.nonzero_sectors_in_first_mib$", "core.img's size: the modules grub-install embeds depend on the GRUB build and the devices it finds"),
    (r"^storage\.btrfs\.\d+\.subvolumes\.\d+\.uuid$", "btrfs subvolume UUIDs can't be set: `btrfs subvolume create` makes new ones"),
    (r"^storage\.btrfs\.\d+\.subvolumes\.\d+\.parent_uuid:set$", "a snapshot's origin's UUID is new too (whether it is a snapshot is compared)"),
    (r"^storage\.(disks\.\d+\.partitions\.\d+\.content|zfs\.pools_from_labels\.\d+)\.hostname$", "a ZFS label names the host that last wrote it (in an initramfs that hasn't set the hostname yet, localhost)"),
    (r"^boot\.esps\.\d+\.contents\.files\.\d+\.size$", "the boot stage rebuilds the initramfs images, and Pop!_OS's kernelstub copies them and the kernels to the ESP"),
    (r"^storage\.disks\.\d+\.boot_code\.first_bytes$", "grub-install keeps the MBR's BPB area (bytes 3-89) from whatever was there before; the kind and core.img fields are compared"),
    (r"(^|\.)state$", "a mounted filesystem's clean flag"),
    (r"(^|\.)(last_mounted_on)$", "ext4 records where it was last mounted; the restore mounted it under /mnt/bko-target"),
    (r"^warnings$", "compared separately"),
    (r"(^|\.)(sync_action|degraded)$", "md's background state"),
    (r"^storage\.loop_devices\.", "loop devices are attached at run time (snaps), in no fixed order"),
    (r"^storage\.mounts\.\d+\.(devno|size_bytes)$", "a mount's device number and size are the running system's (ZFS's change at each boot)"),
    (r"^storage\.disks\.\d+\.partitions\.\d+\.content\.device_uuid$", "an md member's own Device UUID: mdadm --create can't set it"),
    (r"\.content\.internal_uuid$", "bcachefs's internal UUID: bcachefs format sets only the user-visible one"),
    (r"\.content\.u64s$", "the bcachefs superblock's size, which grows with what the kernel records in it"),
    (r"\.esp_image\.", "an ESP nothing mounts, kept byte for byte for load to write back: its loader rewrites it as the system runs (AlpineBSD's)"),
    (r"\.(local_)?properties\.bootsize$", "illumos' zpool create -B property: load writes the ESP as the source's instead"),
    (r"^_illumos\.", "illumos: a pool's GUID can't be set (zpool reguid takes no -g there), and a clone (an inactive boot environment) comes back as a plain dataset, its snapshot not being backed up"),
    (r"\.uuid:ufs$", "UFS's UUID (fs_id), HAMMER2's and RedoxFS's: their newfs makes a new one and takes none"),
    (r"\.links\.\d+:member$", "a by-uuid link of a filesystem's member UUID (bcachefs's), which can't be set"),
    (r"^storage\.lvm\.volume_groups\.\d+\.(creation_host|seqno)$", "LVM records the last host to write the VG's metadata, and counts the writes"),
    (r"^storage\.lvm\.volume_groups\.\d+\.physical_volumes\.\d+\.device_hint$", "LVM's hint is the PV's path when the metadata was last written"),
    (r"(^|[.:])(top_guid|vdev_guid|metaslab_array|txg|alloc_space|vdev_zap_leaf|vdev_zap_top|vdev_zap_root)$", "ZFS vdev GUIDs and pool history can't be kept (the design's Identity section)"),
    (r"^storage\.zfs\.(pools_from_labels|tools\.imported_pools)\.\d+\.(top_level_vdevs|vdevs)\..*\.guid$", "ZFS vdev GUIDs can't be kept"),
    (r"^storage\.zfs\.tools\.datasets\.\d+\.(used|available|referenced)$", "usage changes as the machine runs"),
    (r"^storage\.zfs\.pools_from_labels\.\d+\.top_level_vdevs\..*\.(DTL|whole_disk)$", "a vdev label's DTL object number, and whole_disk=0, which newer OpenZFS leaves out"),
    (r"^storage\.zfs\.zpool_cache\.[^.]+\.vdev_tree\.", "the boot stage writes zpool.cache for the new pool (the source's may be stale); the pool's vdev tree is compared from zpool status"),
    (r"^storage\.zfs\.zpool_cache\.[^.]+\.(hostid|hostname|txg|vdev_tree\..*\.(guid|path|metaslab_array))$", "the rescue system wrote zpool.cache: its host, its names for the vdevs, and their new GUIDs (ZFS finds vdevs by GUID)"),
    (r"^boot\.kernels\.installed\.\d+\.initrd_bytes$", "load's boot stage rebuilds every initramfs (update-initramfs -k all)"),
]

# With --expect (BackShot2): what a drifted layout can't predict.
EXPECT_IGNORED = [
    (r"^storage\.topology\.", "the topology's words: the dump writes them from fields compared on their own"),
    (r"^storage\.disks\.\d+\.partitions\.\d+\.devno$", "a partition's device number is the kernel's (past 15 partitions, an extended number)"),
]

# What --new-uuids rewrites on the restored system, for the fs form.
NEW_UUIDS_FILES = (r"^etc/(fstab|crypttab|mdadm\.conf|mdadm/mdadm\.conf|default/grub|kernel/cmdline|initramfs-tools/conf\.d/resume)$",)

# Identifier fields: what --new-uuids changes.
ID_FIELD = re.compile(r"(^|\.)(uuid|disk_guid|disk_signature|hash_seed|luks_uuid|vg_uuid|lv_uuid|id|dm_uuid)$")


def id_map(a, b):
    """{old: new} from the flattened source A and restored B, for --new-uuids:
    each identifier field's value at the same place.  Problems: an old value
    mapped two ways, or kept."""
    m, bad = {}, []
    for k, va in a.items():
        if not k.startswith("storage.") or not ID_FIELD.search(k) or not isinstance(va, str) or not va:
            continue
        vb = b.get(k)
        if not isinstance(vb, str):
            continue
        if va == vb:
            bad.append(f"{k}: kept {va} under --new-uuids")
        elif m.setdefault(va, vb) != vb:
            bad.append(f"{k}: {va} became both {m[va]} and {vb}")
    return m, bad


def remap(a, m):
    """A's string values with every old identifier replaced by its new one."""
    if not m:
        return a
    pat = re.compile("|".join(re.escape(k) for k in sorted(m, key=len, reverse=True)))
    return {k: (pat.sub(lambda x: m[x.group(0)], v) if isinstance(v, str) else v) for k, v in a.items()}


def drop_removable(d):
    """--removable: the ESPs' EFI/BOOT/ files left out of the comparison."""
    for e in (d.get("boot") or {}).get("esps") or []:
        fs = (e.get("contents") or {}).get("files")
        if isinstance(fs, list):
            e["contents"]["files"] = [f for f in fs if not str(f.get("path", "")).upper().startswith("EFI/BOOT/")]
            for f in e["contents"]["files"]:
                if str(f.get("same_as", "")).upper().startswith("EFI/BOOT/"):
                    f.pop("same_as")


def nvram_kept(d):
    """--nvram-kept: only the boot entries on a disk, as a set; the rest is the rescue session's."""
    efi = (d.get("boot") or {}).get("efi")
    if not isinstance(efi, dict):
        return
    ents = [e for e in efi.get("entries") or [] if any("HD(" in str(p) for p in e.get("device_path") or [])]
    efi["entries"] = sorted(({k: v for k, v in e.items() if k not in ("number", "in_boot_order")} for e in ents),
                            key=lambda e: json.dumps(e, sort_keys=True))
    for k in ("boot_order", "boot_current", "boot_next"):
        efi.pop(k, None)


# Fields added to the dump on 2026-11-05: a source dumped before then lacks them.
NEW_FIELDS = [r"^boot\.grub\.dir$", r"^boot\.grub\.grub_cfg\.lang$", r"\.content\.logical_block_size$"]

# Paths (relative to /) load's boot stage writes, and the kinds of difference
# each may show.  Anything else that differs is the restore's fault.
# (A file the boot stage writes gets the SELinux policy's label for its path,
# system_u, where the source's had the user that last wrote it.)
_WRITTEN = {"content", "mtime", "mtime (dirs)", "atime", "atime (dirs)", "extra", "selinux"}
BOOT_STAGE = [
    (r"^boot/grub2?(/|$)", _WRITTEN | {"missing"}, "grub-install and update-grub rewrite /boot/grub (Fedora's /boot/grub2)"),
    (r"^boot/loader(/entries(/[^/]+)?)?$", _WRITTEN, "grub2-mkconfig rewrites Fedora's BLS entries' options lines"),
    (r"^boot/(initrd\.img-[^/]+|initramfs-[^/]+|initrd-[^/]+)$", _WRITTEN, "the boot stage rebuilds every initramfs (Alpine's have no .img; openSUSE's are initrd-VERSION)"),
    (r"^boot$", {"mtime (dirs)", "atime (dirs)"}, "update-initramfs replaces the initrds"),
    (r"^var/log/bko-holt(/|$)", _WRITTEN, "load's own log, copied in by the boot stage"),
    (r"^etc/zfs/zpool\.cache$", _WRITTEN, "the boot stage writes the new pool's zpool.cache"),
    (r"^var/log$", {"mtime (dirs)", "atime (dirs)"}, "var/log/bko-holt made"),
    (r"^var/lib/initramfs-tools(/|$)", _WRITTEN, "update-initramfs records the kernels it built for"),
    (r"^var/lib/dracut(/|$)", _WRITTEN, "dracut's console-setup module, run by the boot stage's initramfs rebuild"),
    (r"^boot/efi[^/]*/EFI(/|$)", _WRITTEN, "grub-install writes the ESPs' loaders (a second ESP by the distribution's hook)"),
    (r"^(boot/)?efi/EFI(/(BOOT|Boot|systemd|Linux)(/.*)?)?$", _WRITTEN, "bootctl install rewrites systemd-boot and makes EFI/Linux in an ESP at /efi (AerynOS)"),
    (r"^(boot/)?efi/loader(/(random-seed|keys))?$", _WRITTEN, "bootctl install writes a new random seed and makes loader/keys"),
    (r"^efi/EFI/endeavouros(/|$)", _WRITTEN, "grub-install writes the loader in an ESP at /efi (EndeavourOS with GRUB)"),
    (r"^(boot/)?efi/[0-9a-f]{32}/[^/]+/initrd$", _WRITTEN, "dracut --regenerate-all rebuilds a Type 1 entry's initrd, on the ESP under the machine id (EndeavourOS)"),
    (r"^(boot/)?efi/[0-9a-f]{32}/[^/]+$", {"mtime (dirs)", "atime (dirs)"}, "dracut replaces the initrd in its directory"),
    (r"^(tmp|var/tmp)$", {"mtime (dirs)", "atime (dirs)"}, "update-initramfs and update-grub work in temporary directories"),
    (r"^var/lib/os-prober(/|$)", _WRITTEN, "update-grub runs os-prober (GRUB_DISABLE_OS_PROBER=false)"),
    (r"^(var/log/kernelstub\.log|etc/kernelstub/configuration)$", _WRITTEN, "Pop!_OS's kernelstub, run after update-initramfs, logs and rewrites its configuration"),
    (r"^boot/efi/loader/entries/[^/]+$", _WRITTEN, "kernelstub rewrites systemd-boot's entries"),
    (r"^usr/lib/modules/[^/]+(/modules\.[^/]+)?$", _WRITTEN, "depmod, run by the initramfs rebuild (Pop!_OS), rewrites modules.*"),
    (r"^boot(/EFI/Linux(/[^/]+\.efi)?|/limine\.conf(\.old)?)$", _WRITTEN, "Omarchy's mkinitcpio hook rebuilds the unified kernel image and Limine's entries"),
    (r"^boot(/|$)", {"selinux"}, "the boot stage relabels /boot (setfiles): the policy's labels, where the source's can be stale (tumbleweed's update left its new kernel's files modules_object_t)"),
]


def illumos_normalise(d):
    """illumos: what can't be kept moves to keys ending in :illumos, which
    IGNORED names; boot environments compared by name, flags and mount
    point; mount options without their device numbers."""
    if (d.get("os") or {}).get("family") != "illumos":
        return d
    import copy
    d = copy.deepcopy(d)
    st, extra = d.get("storage") or {}, {}
    for x in st.get("disks") or []:
        # illumos' loader stage1 (eb 3c 90) carries STAGE1_STAGE2_UUID at
        # 262..277, which installboot makes up afresh at every install.
        bc = x.get("boot_code") or {}
        if isinstance(bc.get("code_b64"), str):
            import base64
            code = bytearray(base64.b64decode(bc["code_b64"]))
            if code[:3] == b"\xeb\x3c\x90" and len(code) >= 278:
                extra[f"{x['name']}.stage2_uuid"] = code[262:278].hex()
                code[262:278] = bytes(16)
                bc["code_b64"] = base64.b64encode(bytes(code)).decode()
        for p in x.get("partitions") or []:
            c = p.get("content") or {}
            if c.get("type") == "zfs_member":
                for k in ("pool_guid", "uuid"):
                    if k in c:
                        extra[f"{x['name']}.{p['name']}.{k}"] = c.pop(k)
    z = st.get("zfs") or {}
    for i, p in enumerate(z.get("pools_from_labels") or []):
        extra[f"label{i}.pool_guid"] = p.pop("pool_guid", None)
    for n, c in (z.get("zpool_cache") or {}).items():
        if isinstance(c, dict):
            extra[f"cache.{n}.pool_guid"] = c.pop("pool_guid", None)
    for i, p in enumerate((z.get("tools") or {}).get("imported_pools") or []):
        extra[f"pool{i}.guid"] = (p.get("properties") or {}).pop("guid", None)
    for i, ds in enumerate((z.get("tools") or {}).get("datasets") or []):
        extra[f"ds{i}.origin"] = ds.pop("origin", None)
    for m in st.get("mounts") or []:
        if isinstance(m.get("options"), str):
            m["options"] = re.sub(r"(^|,)dev=[0-9a-f]+", "", m["options"])
    b = d.get("boot") or {}
    if isinstance(b.get("boot_environments"), str):
        b["boot_environments"] = sorted(";".join(l.split(";")[:4:3] + [l.split(";")[2]]) for l in b["boot_environments"].splitlines() if l)
    d["_illumos"] = {k: str(v) for k, v in extra.items()}
    return d


SNAPPER_SLOT = re.compile(r"(^|/)\.snapshots/\d+/snapshot$")


def btrfs_by_path(d):
    """btrfs subvolumes and level-0 qgroups compared by path, not in ID
    order: load makes snapper's snapshots after the restore, as snapshots of
    one another (docs/design/btrfs-snapshots.md), and one made after a
    subvolume that comes later on the source gets a later ID.  A snapper
    snapshot's ID, its qgroup's, and whether it has an origin (each is a
    snapshot of the one before it; after a rollback the live root, made
    first, has none) are not compared; its path, flags and qgroup
    membership are."""
    for fs in (d.get("storage") or {}).get("btrfs") or []:
        subs = fs.get("subvolumes") or []
        for v in subs:
            path = str(v.get("path") or "").removeprefix("<FS_TREE>/")
            if SNAPPER_SLOT.search(path):
                v["id"] = "<a snapper snapshot's>"
                v["parent_uuid"] = "<a snapper snapshot's>"
        subs.sort(key=lambda v: str(v.get("path") or ""))
        qs = fs.get("qgroups") or []
        for q in qs:
            path = str(q.get("path") or "")
            if str(q.get("qgroupid") or "").startswith("0/") and SNAPPER_SLOT.search(path):
                q["qgroupid"] = f"0/<{path}>"
        qs.sort(key=lambda q: (str(q.get("qgroupid") or "")[:2], str(q.get("path") or ""), str(q.get("qgroupid") or "")))
    return d


def md_by_name(d):
    """A named md array (Anaconda's /dev/md/boot, /dev/md/pv00) gets its
    kernel number, md127 downwards, in the order the arrays come up at boot,
    as mdadm.conf gives it none: each such mdN is called md:NAME throughout
    the dump instead (its device number too), and md_arrays are sorted by
    those names.  A numbered array (/dev/md/0, md0) keeps its name."""
    ren = {}
    for a in (d.get("storage") or {}).get("md_arrays") or []:
        dn = str((a.get("md") or {}).get("devname") or "")
        if dn and not dn.isdigit() and re.fullmatch(r"md\d+", a.get("name") or ""):
            ren[a["name"]] = f"md:{dn}"
    if not ren:
        return d
    word = re.compile(r"\b(" + "|".join(map(re.escape, ren)) + r")\b")

    def walk(v):
        if isinstance(v, dict):
            return {(word.sub(lambda m: ren[m.group(1)], k) if isinstance(k, str) else k): walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return word.sub(lambda m: ren[m.group(1)], v) if isinstance(v, str) else v

    d = walk(d)
    arrays = d["storage"]["md_arrays"]
    for a in arrays:
        if a.get("name") in ren.values():
            a["devno"] = f"9:<{a['name']}>"
    arrays.sort(key=lambda a: a.get("name") or "")
    return d


def flatten(v, path, out):
    if isinstance(v, dict) and isinstance(v.get("links"), list) and isinstance(v.get("content"), dict):
        # A by-uuid link that isn't the content's own UUID names a member
        # (bcachefs's device UUID): compared as such, its value ignored.
        own = v["content"].get("uuid")
        links = []
        for x in v["links"]:
            if isinstance(x, str) and x.startswith("/dev/disk/by-uuid/") and x[len("/dev/disk/by-uuid/"):] != own:
                out[f"{path}.links.{len(links)}:member"] = x
                links.append("/dev/disk/by-uuid/<member>")
            else:
                links.append(x)
        # Sorted again: the member's new UUID sorts elsewhere among the links.
        v = dict(v, links=sorted(links, key=str))
    if isinstance(v, dict) and v.get("type") in ("ufs", "hammer2", "redoxfs") and "uuid" in v:
        # UFS's UUID is its fs_id, which newfs picks at random; HAMMER2's and
        # RedoxFS's newfs take none either.
        out[f"{path}.uuid:ufs"] = v["uuid"]
        v = {k: x for k, x in v.items() if k != "uuid"}
    if isinstance(v, dict):
        for k, x in v.items():
            flatten(x, f"{path}.{k}" if path else k, out)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            flatten(x, f"{path}.{i}" if path else str(i), out)
    elif re.search(r"\.subvolumes\.\d+\.parent_uuid$", path):
        # Whether it's a snapshot; the origin's UUID itself is new.
        out[path] = v is not None
        out[path + ":set"] = v
    else:
        out[path] = v


def judge_fs(fsmeta_py, src_path, dst_path, no_atime=False, new_uuids=False):
    import importlib.util
    spec = importlib.util.spec_from_file_location("fsmeta", fsmeta_py)
    fsmeta = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fsmeta)
    before = load_json(src_path)
    after = load_json(dst_path)
    diff = fsmeta.differences(before, after)
    real, allowed = {}, {}
    for kind, paths in diff.items():
        for p in paths:
            why = next((w for r, kinds, w in BOOT_STAGE if kind in kinds and re.search(r, p)), None)
            if not why and kind == "missing" and before.get(p, {}).get("type") == "socket":
                why = "a socket is made by whatever listens on it; no archiver restores one"
            if not why and new_uuids and any(re.search(r, p) for r in NEW_UUIDS_FILES) and kind in _WRITTEN:
                why = "--new-uuids: load rewrites what names the new identifiers"
            if not why and no_atime and kind.startswith("atime"):
                why = "the filesystem's driver here updates atimes on every read (RedoxFS's FUSE driver): the source's moved as it was backed up"
            if not why and kind in ("mtime (dirs)", "atime (dirs)", "selinux") and re.search(r"^(.*/)?\.snapshots/\d+/snapshot/.", p) \
                    and before.get(p, {}).get("type") == "dir" and not any(k.startswith(p + "/") for k in before):
                why = ("a mount point hidden under its mount in a btrfs subvolume mounted elsewhere too (openSUSE's root, again at .snapshots/N/snapshot): "
                       "the backup holds it only through that alias, which load leaves out (docs/design/btrfs-snapshots.md)")
            if not why and kind == "inode flags":
                # btrfs sets FS_NOCOMP_FL itself on a file whose data didn't
                # compress, as it was written: not the restore's to keep.
                af, bf = before.get(p, {}).get("flags"), after.get(p, {}).get("flags")
                if isinstance(af, int) and isinstance(bf, int) and (af ^ bf) & ~0x1400 == 0:
                    why = "btrfs's own no-compress flag, which its compression heuristic sets as data is written"
            (allowed.setdefault(why, []) if why else real.setdefault(kind, [])).append(p)
    for kind, paths in sorted(real.items()):
        print(f"DIFF {kind}: {len(paths)}: {', '.join(paths[:5])}{' ...' if len(paths) > 5 else ''}")
    for why, paths in allowed.items():
        print(f"boot stage ({why}): {len(paths)}: {', '.join(paths[:4])}{' ...' if len(paths) > 4 else ''}")
    n = sum(len(v) for v in real.values())
    print(f"{n} differences, {sum(len(v) for v in allowed.values())} from the boot stage")
    sys.exit(1 if n else 0)


# With --grow: the fields a bigger disk moves, and the unit of each.
GROWN = [
    (r"^storage\.disks\.\d+\.size_bytes$", "bytes"),
    (r"^storage\.disks\.\d+\.partition_table\.(last_usable_lba|backup_lba)$", "sectors"),
    (r"^storage\.disks\.\d+\.partition_table\.protective_mbr\.entries\.0\.sectors$", "sectors"),
    (r"^storage\.disks\.\d+\.partition_table\.protective_mbr\.entries\.0\.size_bytes$", "bytes"),
]


def grown(k, va, vb, grow, flat):
    """Why field K may differ by GROW, or None."""
    if re.search(r"^storage\.disks\.\d+\.size$", k):
        return "the disk's size, in words"
    for pat, unit in GROWN:
        if re.search(pat, k) and isinstance(va, int) and isinstance(vb, int):
            ss = flat.get(re.sub(r"\.partition_table\..*|\.size_bytes$", "", k) + ".logical_sector_size", 512)
            if vb - va == (grow if unit == "bytes" else grow // ss):
                return "the target disk is bigger by --grow: GPT's backup header and last usable LBA move to its end"
    return None


MIB = 1 << 20
# What load can remake at another size, so what the largest partition may hold
# (docs/design/layload.md's fitting section): one of these filesystems, plain,
# inside LUKS, or holding LVM; btrfs on one device only.  Anything else is
# refused, so a dump that shows it means the run should never have got here.
# A field a dump doesn't have, as the comparison prints it (and a fit's WANT
# names one that must be gone).
ABSENT = "<absent>"
FIT_FS = ("ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "jfs", "nilfs2")
# The numbers a mkfs picks from the size it is given, which a fit therefore
# changes.  Everything that makes up the filesystem's identity (its UUID,
# label, block and inode size, features) is compared as always.
FIT_GEOMETRY = {
    "ext2": ("inodes", "inodes_per_group", "reserved_blocks", "reserved_gdt_blocks"),
    "ext3": ("inodes", "inodes_per_group", "reserved_blocks", "reserved_gdt_blocks"),
    "ext4": ("inodes", "inodes_per_group", "reserved_blocks", "reserved_gdt_blocks"),
    "xfs": ("ag_count", "ag_blocks", "log_blocks"),
    "f2fs": ("segment_count",),
    "nilfs2": ("segments",),
    "btrfs": (),
    "jfs": (),
}


def _int(v):
    """V when it is a plain integer, else None (a bool is not a size)."""
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _step(va, vb):
    """How much integer field value VA moved to VB, or None."""
    a, b = _int(va), _int(vb)
    return None if a is None or b is None else b - a


def _round(n, unit, up):
    """N rounded to a whole UNIT, up or down.  N may be negative."""
    q, r = divmod(abs(n), unit)
    return (q + (1 if r and up else 0)) * unit * (-1 if n < 0 else 1)


def _dm_of(st, want):
    """The index of the dm device WANT(its dm map) picks, or None."""
    for i, dm in enumerate(st.get("dm_devices") or []):
        if want(dm.get("dm") or {}):
            return i
    return None


def fit_plan(src, delta, pct):
    """What `load --fit-largest PCT` does to SRC's layout on targets DELTA
    bytes bigger (negative: smaller), worked out as load works it out: per
    disk, the largest partition and the bytes it gains or loses, the
    partitions that move with it, and the dm, LUKS and LVM objects whose sizes
    follow.  What load would refuse outright is a problem here, not a guess:
    the run should not have reached the judging."""
    plan = {"delta": delta, "disks": {}, "dm": {}, "vgs": {}, "problems": []}
    st = src.get("storage") or {}
    for i, disk in enumerate(st.get("disks") or []):
        who = disk.get("name")
        ss = _int(disk.get("logical_sector_size")) or 512
        size = _int(disk.get("size_bytes"))
        if size is None or delta % ss or (size + delta) % ss:
            plan["problems"].append(f"disk {who}: its size {size} and the target's {delta} aren't whole {ss}-byte sectors")
            continue
        parts = [(j, p) for j, p in enumerate(disk.get("partitions") or []) if not p.get("extended")]
        if not parts:
            # No partition table (or an MBR extended container alone): there is
            # nothing to fit, and the room a bigger target has goes unused, as
            # it does without --fit-largest.  Such a disk may be no target at
            # all (a guest's /dev/ram0, a zvol), so its new size is allowed,
            # not expected.
            plan["disks"][i] = {"disk": who, "ss": ss, "amount": 0, "big": None,
                                "name": None, "moved": set(), "table": False}
            continue
        # new_last: the target's last usable LBA, with the source's table
        # length kept (GPT's tail of entries and backup header), else its last
        # sector.
        table = disk.get("partition_table") or {}
        tail = 0
        if table.get("type") == "gpt":
            tail = size // ss - 1 - (_int(table.get("last_usable_lba")) or size // ss - 1)
        new_last = (size + delta) // ss - 1 - tail
        big = max(parts, key=lambda jp: _int(jp[1].get("sectors")) or 0)
        sectors = _int(big[1].get("sectors")) or 0
        tied = [p.get("name") for _, p in parts if (_int(p.get("sectors")) or 0) == sectors]
        if len(tied) > 1:
            plan["problems"].append(f"disk {who}: {', '.join(tied)} tie for largest, which load refuses: nothing to judge")
            continue
        if big[1].get("logical"):
            plan["problems"].append(f"disk {who}: its largest partition {tied[0]} is an MBR logical one, which load refuses")
            continue
        end = max((_int(p.get("start_lba")) or 0) + (_int(p.get("sectors")) or 0) - 1 for _, p in parts)
        bytes_big = _int(big[1].get("size_bytes")) or sectors * ss
        if delta < 0:
            # The overrun past the target's end, rounded up to a whole MiB; a
            # target that lost only the source's own slack changes nothing.
            amount = -_round((end - new_last) * ss, MIB, True) if end > new_last else 0
            if pct is not None and -amount * 100 > pct * bytes_big:
                plan["problems"].append(
                    f"disk {who}: {-amount} is more than {pct}% of {tied[0]}, which load refuses: nothing to judge")
                continue
        elif delta > 0:
            amount = _round((new_last - end) * ss, MIB, False) if end < new_last else 0
            if pct is not None:
                amount = min(amount, _round(bytes_big * pct // 100, MIB, False))
        else:
            amount = 0
        moved = {j for j, p in parts if (_int(p.get("start_lba")) or 0) > (_int(big[1].get("start_lba")) or 0)}
        if amount:
            # A filesystem's BPB that records a first sector other than its
            # partition's start (or 0) isn't one load can move (fit.rs).
            for j, p in parts:
                hs = _int((p.get("content") or {}).get("hidden_sectors"))
                if j in moved and hs not in (None, 0, _int(p.get("start_lba"))):
                    plan["problems"].append(f"disk {who}: {p.get('name')} would have to move, and its filesystem records "
                                            f"sector {hs} as its first, which load refuses")
        plan["disks"][i] = {
            "disk": who, "ss": ss, "amount": amount, "big": big[0], "name": tied[0], "table": True,
            "bytes": bytes_big, "holds": (big[1].get("content") or {}).get("type") or "-", "moved": moved}
        if amount:
            fit_stack(plan, st, big[1], amount, who)
    return plan


def fit_plan_lines(delta, pct, path):
    """`judge.py --fit-plan DELTA [--fit-pct PCT] LAYOUT.json`: what the fit
    comes to on that layout, for the test rig to check its own run against.
    One line "DISK LARGEST AMOUNT LARGEST_BYTES HOLDS" per disk, then a
    "problem:" line for each thing load would refuse; exit 1 if there was one."""
    plan = fit_plan(load_json(path), delta, pct)
    for d in plan["disks"].values():
        print(d["disk"], d["name"] or "-", d["amount"], d.get("bytes") or 0, d.get("holds") or "-")
    for p in plan["problems"]:
        print(f"problem: {p}")
    sys.exit(1 if plan["problems"] else 0)


def fit_stack(plan, st, part, amount, who):
    """The dm, LUKS and LVM objects on PART whose sizes follow the fit, and the
    problems a stack load refuses leaves (the judging would be meaningless)."""
    content = part.get("content") or {}
    kind, holder, inner = content.get("type"), part.get("name"), content
    if kind == "crypto_LUKS":
        # The payload offset doesn't move: what is inside loses or gains what
        # the partition does.
        i = _dm_of(st, lambda m: m.get("target") == "crypt" and m.get("luks_uuid") == content.get("uuid"))
        if i is None:
            plan["problems"].append(f"disk {who}: {part.get('name')} is LUKS with nothing open on it in the dump")
            return
        plan["dm"][i] = amount
        holder = (st["dm_devices"][i].get("name"))
        inner = st["dm_devices"][i].get("content") or {}
        kind = inner.get("type")
    if kind == "LVM2_member":
        fit_vg(plan, st, inner, holder, amount, who)
    elif kind not in FIT_FS:
        plan["problems"].append(f"disk {who}: {part.get('name')} holds {kind}, which load refuses to fit")
    elif kind == "btrfs" and (_int(inner.get("num_devices")) or 1) > 1:
        plan["problems"].append(f"disk {who}: {part.get('name')} is one device of a btrfs of {inner['num_devices']}, which load refuses to fit")


def fit_vg(plan, st, pv_content, holder, amount, who):
    """The volume group on HOLDER: the PV's new size and extent count, and the
    logical volume a shrink takes the rest out of (item 8 of the feature:
    free extents first, then the largest LV, by whole extents from its end)."""
    groups = ((st.get("lvm") or {}).get("volume_groups")) or []
    found = [(v, vg) for v, vg in enumerate(groups)
             if any(pv.get("device") == holder for pv in vg.get("physical_volumes") or [])]
    if len(found) != 1:
        plan["problems"].append(f"disk {who}: {len(found)} volume groups on {holder} in the dump")
        return
    v, vg = found[0]
    pvs = vg.get("physical_volumes") or []
    if len(pvs) > 1:
        plan["problems"].append(f"disk {who}: volume group {vg.get('name')} has {len(pvs)} PVs, which load refuses to fit")
        return
    extent = _int(vg.get("extent_size_bytes"))
    pe_count = _int(pvs[0].get("pe_count"))
    pe_start = _int(pvs[0].get("pe_start_bytes"))
    dev_size = _int(pvs[0].get("dev_size_bytes"))
    lvs = vg.get("logical_volumes") or []
    if not extent or pe_count is None or pe_start is None or dev_size is None:
        plan["problems"].append(f"disk {who}: volume group {vg.get('name')} is missing its extent or PV sizes in the dump")
        return
    # The PV covers the whole of HOLDER, so the extents it gains or loses are
    # what fits in the new area; its leftover tail stays whatever it was.
    spare = (dev_size - pe_start) - pe_count * extent
    new_count = pe_count + (spare + amount) // extent
    used = sum(_int(s.get("extent_count")) or 0 for lv in lvs for s in lv.get("segments") or [])
    free = pe_count - used
    lost = pe_count - new_count
    shrink, lv, need = 0, None, 0
    if lost > free:
        # The largest LV gives up the rest, and must be one load can remake.
        big = max(lvs, key=lambda x: _int(x.get("size_bytes")) or 0, default=None)
        sizes = [_int(x.get("size_bytes")) or 0 for x in lvs]
        if big is None or sizes.count(_int(big.get("size_bytes")) or 0) > 1:
            plan["problems"].append(f"disk {who}: volume group {vg.get('name')} has no single largest LV, which load refuses")
            return
        segs = big.get("segments") or []
        if any((s.get("type") or "") != "striped" or (_int(s.get("stripe_count")) or 1) != 1 for s in segs):
            plan["problems"].append(f"disk {who}: LV {big.get('name')} isn't linear, which load refuses to shrink")
            return
        need = lost - free
        shrink = need * extent
        lv = lvs.index(big)
        dm = _dm_of(st, lambda m: m.get("target") == "lvm" and m.get("vg") == vg.get("name") and m.get("lv") == big.get("name"))
        if dm is not None:
            plan["dm"][dm] = -shrink
    segments = {}
    if lost > 0:
        segments = fit_segments(plan, lvs, pe_count, lost, lv, need, f"disk {who}: volume group {vg.get('name')}")
    plan["vgs"][v] = {"amount": amount, "extent": extent, "dev_size": dev_size + amount,
                      "pe_count": new_count, "lv": lv, "shrink": shrink, "segments": segments}


def _stripe_runs(seg):
    """[(pv, first physical extent, extents)] of segment SEG, one per stripe."""
    st = seg.get("stripes") or []
    n = max(_int(seg.get("stripe_count")) or 1, 1)
    count = _int(seg.get("extent_count")) or 0
    return [(st[2 * k], _int(st[2 * k + 1]), count // n) for k in range(n)
            if 2 * k + 1 < len(st) and _int(st[2 * k + 1]) is not None]


def _below(runs, x):
    """How many of RUNS' extents lie below extent X."""
    return sum(min(n, max(0, x - s)) for s, n in runs)


def _mergeable(a, b):
    """Whether LVM keeps segments A and B (in that logical order) as one: of
    one type and stripe set, and next to each other in both logical and
    physical extents on each PV (lv_merge_segments)."""
    if a.get("type") != b.get("type") or a.get("type") != "striped":
        return False
    rest = lambda x: {k: v for k, v in x.items() if k not in ("start_extent", "extent_count", "stripes")}
    if rest(a) != rest(b):
        return False
    if (_int(a.get("start_extent")) or 0) + (_int(a.get("extent_count")) or 0) != _int(b.get("start_extent")):
        return False
    ra, rb = _stripe_runs(a), _stripe_runs(b)
    return len(ra) == len(rb) and len(ra) > 0 and all(
        pa == pb and sa + na == sb for (pa, sa, na), (pb, sb, _) in zip(ra, rb))


def fit_segments(plan, lvs, pe_count, lost, lv, need, who):
    """Every LV's segments once the fit drops LOST extents from the PV, as
    load drops them (holt/src/load/fit.rs resize_vg and rewrite_lvs): the
    highest free extents, or every free one and NEED from the logical end of
    LV (the index of the one that shrinks); every extent above a dropped one
    moves down by as many as were dropped below it; then segments made
    adjacent, in logical and physical extents on the same PV, are one, as
    LVM merges them.  {LV index: segments}, for the LVs whose segments change."""
    used = sorted((s, n) for x in lvs for seg in x.get("segments") or [] for _, s, n in _stripe_runs(seg))
    free, at = [], 0
    for s, n in used:
        if s > at:
            free.append((at, s - at))
        at = max(at, s + n)
    if at < pe_count:
        free.append((at, pe_count - at))
    if lv is None:
        gone, left = [], lost
        for s, n in reversed(free):
            take = min(left, n)
            if take:
                gone.append((s + n - take, take))
                left -= take
    else:
        gone, left = list(free), need
        for seg in sorted(lvs[lv].get("segments") or [], key=lambda x: -(_int(x.get("start_extent")) or 0)):
            for _, s, n in _stripe_runs(seg):
                take = min(left, n)
                if take:
                    gone.append((s + n - take, take))
                    left -= take
    gone.sort()
    out = {}
    for i, x in enumerate(lvs):
        segs = json.loads(json.dumps(x.get("segments") or []))
        left = need if i == lv else 0
        for seg in sorted(segs, key=lambda y: -(_int(y.get("start_extent")) or 0)):
            n = _int(seg.get("extent_count")) or 0
            take = min(left, n)
            left -= take
            if take:
                seg["extent_count"] = n - take
        segs = [y for y in segs if (_int(y.get("extent_count")) or 0) > 0]
        for seg in segs:
            st = seg.get("stripes")
            for k in range(1, len(st or []), 2):
                if _int(st[k]) is not None:
                    st[k] -= _below(gone, st[k])
        merged = []
        for seg in segs:
            if merged and _mergeable(merged[-1], seg):
                merged[-1]["extent_count"] += seg["extent_count"]
            else:
                merged.append(seg)
        if merged != (x.get("segments") or []):
            if any((_int(y.get("stripe_count")) or 1) != 1 for y in x.get("segments") or []):
                plan["problems"].append(f"{who}: LV {x.get('name')} is striped and would have to move, which load refuses")
            out[i] = merged
    return out


def fit_fs_expect(want, flat, at, container, amount):
    """What the fit decides about the sizes of the filesystem (or LVM PV) whose
    fields start at AT, inside a CONTAINER that changed by AMOUNT bytes: one
    that filled its container goes on filling it, with the block count that
    follows.  One the source left short of its end is its mkfs's business, not
    the fit's (JFS's aggregate is 52 MiB under its partition), so nothing is
    expected of it; fit_may_differ lets it move."""
    kind = flat.get(at + "type")
    if kind == "LVM2_member":
        fields = ("device_size",)
    elif kind == "btrfs":
        fields = ("total_bytes", "device.total_bytes")
    elif kind in FIT_FS:
        fields = ("size_bytes",)
    else:
        return
    if container is None or _int(flat.get(at + fields[0])) != container:
        return
    for f in fields:
        want[at + f] = container + amount
    block = _int(flat.get(at + "block_size"))
    blocks = _int(flat.get(at + "blocks"))
    if block and blocks is not None and amount % block == 0:
        want[at + "blocks"] = blocks + amount // block


def fit_expect(plan, flat):
    """Every field the fit must change, with the value it must then have, from
    the source's own numbers.  A restored dump that left one of them as it was
    is as much a DIFF as one that moved it by the wrong amount, so a run that
    fitted nothing can't pass."""
    want = {}

    def step(k, by):
        v = _int(flat.get(k))
        if v is not None:
            want[k] = v + by

    delta = plan["delta"]
    for i, d in plan["disks"].items():
        at, ss, amount = f"storage.disks.{i}.", d["ss"], d["amount"]
        if delta and d["table"]:
            step(at + "size_bytes", delta)
            for f in ("partition_table.last_usable_lba", "partition_table.backup_lba",
                      "partition_table.protective_mbr.entries.0.sectors"):
                step(at + f, delta // ss)
            step(at + "partition_table.protective_mbr.entries.0.size_bytes", delta)
        if not amount:
            continue
        p = at + f"partitions.{d['big']}."
        step(p + "sectors", amount // ss)
        step(p + "end_lba", amount // ss)
        step(p + "size_bytes", amount)
        fit_fs_expect(want, flat, p + "content.", _int(flat.get(p + "size_bytes")), amount)
        for j in d["moved"]:
            q = at + f"partitions.{j}."
            step(q + "start_lba", amount // ss)
            step(q + "end_lba", amount // ss)
            step(q + "start_bytes", amount)
            # A FAT records its start (mkfs.fat's hidden sectors), and load
            # makes a moved one where it now starts: only one that recorded
            # its own start (0 stays 0; anything else, load refuses to move,
            # fit_plan's problem).  holt/src/load/fit.rs
            hs = _int(flat.get(q + "content.hidden_sectors"))
            if hs is not None and hs != 0 and hs == _int(flat.get(q + "start_lba")):
                step(q + "content.hidden_sectors", amount // ss)
    for i, amount in plan["dm"].items():
        at = f"storage.dm_devices.{i}."
        step(at + "size_bytes", amount)
        fit_fs_expect(want, flat, at + "content.", _int(flat.get(at + "size_bytes")), amount)
    for v, g in plan["vgs"].items():
        at = f"storage.lvm.volume_groups.{v}."
        want[at + "physical_volumes.0.dev_size_bytes"] = g["dev_size"]
        want[at + "physical_volumes.0.pe_count"] = g["pe_count"]
        if g["shrink"]:
            step(at + f"logical_volumes.{g['lv']}.size_bytes", -g["shrink"])
        # Each LV's segments as the fit leaves them: a segment that's gone
        # (merged with the one before it, or the shrink's) must be absent.
        for li, segs in g.get("segments", {}).items():
            sp = at + f"logical_volumes.{li}.segments"
            pred = {}
            flatten(segs, sp, pred)
            for k in flat:
                if k.startswith(sp + "."):
                    want[k] = ABSENT
            want.update(pred)
    return want


def fit_may_differ(k, vb, plan, flat, want):
    """Why field K may differ under --fit by an amount judge.py doesn't work
    out, or None.  The sizes it does work out are WANT's, fit_expect's map."""
    m = re.match(r"^storage\.(disks|dm_devices)\.(\d+)\.", k)
    if m:
        dm = m.group(1) == "dm_devices"
        where = plan["dm"] if dm else plan["disks"]
        if int(m.group(2)) not in where:
            return None
        rest = k[m.end():]
        if rest == "size":
            return "a disk's or volume's size, in words"
        if not dm and rest == "size_bytes" and not plan["disks"][int(m.group(2))]["table"] \
                and _step(flat.get(k), vb) == plan["delta"]:
            # A target with no partition table: nothing to fit on it, so it is
            # only bigger or smaller, as it is without --fit-largest.
            return "the target disk is of another size, with no partition table to fit"
        # The filesystem the fit remade: the fitted partition's, or the one in
        # what that partition holds (LUKS, an LVM volume).
        if dm:
            fs = re.fullmatch(r"content\.(.+)", rest)
        else:
            big = plan["disks"][int(m.group(2))]["big"]
            fs = big is not None and re.fullmatch(rf"partitions\.{big}\.content\.(.+)", rest)
        at = re.sub(r"[^.]+$", "", k)
        kind = flat.get(at + "type")
        if fs and "." not in fs.group(1) and kind in FIT_FS:
            field = fs.group(1)
            if field in FIT_GEOMETRY[kind]:
                return "a mkfs picks this from the size it is given"
            if field in ("size_bytes", "blocks") and at + field not in want:
                return "the source's filesystem was short of its container's end: its new size is its mkfs's"
        return None
    # An LV's segments, under a fit, are WANT's (fit_segments): where the
    # extents left were renumbered, merged, or the shrink taken from.
    return None


def main():
    if sys.argv[1] == "fs":
        judge_fs(*sys.argv[2:5], no_atime="--no-atime" in sys.argv[5:], new_uuids="--new-uuids" in sys.argv[5:])
    grow, fit, pct, plan_only = 0, None, None, None
    flags = set()
    while sys.argv[1] in ("--expect", "--new-uuids", "--removable", "--nvram-kept", "--grow", "--fit", "--fit-pct", "--fit-plan"):
        if sys.argv[1] in ("--expect", "--new-uuids", "--removable", "--nvram-kept"):
            flags.add(sys.argv.pop(1))
            continue
        if sys.argv[1] == "--grow":
            grow = int(sys.argv[2])
        elif sys.argv[1] == "--fit":
            fit = int(sys.argv[2])
        elif sys.argv[1] == "--fit-plan":
            plan_only = int(sys.argv[2])
        else:
            pct = int(sys.argv[2].rstrip("%"))
        del sys.argv[1:3]
    if plan_only is not None:
        fit_plan_lines(plan_only, pct, sys.argv[1])
    if grow and fit is not None:
        sys.exit("judge.py: --grow and --fit are two modes (--fit allows --grow's fields too)")
    if pct is not None and fit is None:
        sys.exit("judge.py: --fit-pct is load's PCT, for --fit")
    degraded = sys.argv[1] == "--degraded"
    if degraded:
        del sys.argv[1]
    src = btrfs_by_path(md_by_name(illumos_normalise(load_json(sys.argv[1]))))
    dst = btrfs_by_path(md_by_name(illumos_normalise(load_json(sys.argv[2]))))
    for d in (src, dst):
        if "--removable" in flags:
            drop_removable(d)
        if "--nvram-kept" in flags:
            nvram_kept(d)
    # Lists whose order is the boot's, not the layout's: ZFS mounts its
    # datasets in parallel.
    for d in (src, dst):
        st = d.get("storage") or {}
        if isinstance((d.get("boot") or {}).get("esps"), list):
            d["boot"]["esps"].sort(key=lambda e: e.get("mounted_at") or "")
        if isinstance(st.get("swaps"), list):
            st["swaps"].sort(key=lambda w: w.get("file") or "")
        if isinstance(st.get("mounts"), list):
            st["mounts"].sort(key=lambda m: (m.get("target") or "", m.get("source") or ""))
    # The fit is worked out from the source's layout alone, before anything is
    # compared, as load works it out from the same numbers.
    plan = fit_plan(src, fit, pct) if fit is not None else None
    a, b = {}, {}
    flatten(src, "", a)
    flatten(dst, "", b)
    want = fit_expect(plan, a) if plan is not None else {}
    idbad = []
    if "--new-uuids" in flags:
        m, idbad = id_map(a, b)
        a = remap(a, m)
        want = {k: (remap({0: v}, m)[0]) for k, v in want.items()}
        if not m:
            idbad.append("--new-uuids, and no identifier changed")
    # md: "active" is "clean" with writes in flight (it turns clean after
    # safe_mode_delay idle), so either is a running, healthy array.
    for m in (a, b):
        for k, v in m.items():
            if k.endswith(".md.array_state") and v in ("active", "clean", "active-idle"):
                m[k] = "clean (or active)"
    real, ignored = [], {}
    for k in sorted(set(a) | set(b)):
        if a.get(k, "<absent>") == b.get(k, "<absent>"):
            continue
        why = next((w for p, w in IGNORED + (EXPECT_IGNORED if "--expect" in flags else []) if re.search(p, k)), None)
        if not why and k.endswith(".content.device.uuid") and (a.get(k[: -len("device.uuid")] + "num_devices") or 1) > 1:
            why = "a multi-device btrfs member's own device UUID: mkfs.btrfs --device-uuid sets it for a single device only"
        va, vb = a.get(k), b.get(k)
        if not why and grow:
            why = grown(k, va, vb, grow, a)
        if not why and plan is not None:
            if k in want and b.get(k, ABSENT) == want[k]:
                why = "the fit's own: the size judge.py works out for it (checked below)"
            else:
                why = fit_may_differ(k, vb, plan, a, want)
        if not why and k.startswith("storage.zfs.zpool_cache.") and k not in a:
            why = "zpool.cache fields only the rescue system's newer OpenZFS writes"
        if not why and re.search(r"\.features\.[a-z_]+$", k) and {va, vb} <= {"enabled", "active"}:
            why = "a ZFS feature is active once something uses it (block cloning: cp)"
        if not why and k.endswith(".content.device_number") and a.get(k[: -len("device_number")] + "type") == "linux_raid_member":
            why = "an md member's device number, which mdadm --create gives in order (the source's RAID 5, made without --assume-clean, got its last member as a spare to rebuild, numbered after the others); its role is compared"
        if not why and k.endswith(".version") and a.get(k[: -len("version")] + "type") == "f2fs":
            why = "an F2FS superblock records the f2fs-tools release that made it (the rescuer's mkfs.f2fs, not the source's), and its checksum covers it"
        if not why and k.endswith(".inodes") and a.get(k[: -len("inodes")] + "type") == "xfs":
            why = "XFS's sb_icount counts the inodes allocated so far (in chunks of 64, as files are made): usage, not layout"
        m = re.match(r"^(storage\.other_devices\.\d+)\.", k)
        if not why and m and a.get(m.group(1) + ".kind") == "zram":
            why = "zram is made anew at each boot"
        if not why and k not in a and any(re.search(r, k) for r in NEW_FIELDS):
            why = "a field bko-holt began recording after the source's dump was made"
        if not why and re.match(r"^storage\.zfs\.tools\.datasets\.\d+\.origin$", k) and va and vb is None:
            why = "a ZFS clone (a boot environment) comes back as a plain dataset: its origin snapshot isn't in the backup"
        if not why and all(isinstance(v, str) and v.startswith("/dev/disk/by-diskseq/") for v in (va, vb)):
            why = "by-diskseq links count the disks the kernel has seen since it booted"
        if why:
            ignored.setdefault(why, []).append(k)
        else:
            real.append(k)
            print(f"DIFF {k}: {json.dumps(a.get(k, '<absent>'))[:200]} -> {json.dumps(b.get(k, '<absent>'))[:200]}")
    for x in idbad:
        real.append("new-uuids")
        print(f"DIFF new-uuids: {x}")
    if degraded:
        # Both dumps' arrays with empty slots: name -> the slots filled, of
        # how many.  (The fields themselves are compared above.)
        def empty_slots(d):
            out = {}
            for a in (d.get("storage") or {}).get("md_arrays") or []:
                md = a.get("md") or {}
                n = md.get("raid_disks") or 0
                filled = sorted(int(m["slot"]) for m in md.get("members") or [] if str(m.get("slot", "")).isdigit())
                if len(filled) < n:
                    out[a.get("name")] = f"slots {filled} of {n}"
            return out
        es, ed = empty_slots(src), empty_slots(dst)
        if not es:
            real.append("md:degraded")
            print("DIFF md:degraded: the source has no md array with an empty slot (--degraded)")
        elif es != ed:
            real.append("md:degraded")
            print(f"DIFF md:degraded: source {es} -> restored {ed}")
        else:
            print(f"degraded as the source, as --degraded asks: {es}")
    if plan is not None:
        # A fit that didn't happen must not pass: every size the fit decides
        # has to be the one judge.py worked out, whether the restored dump
        # moved it elsewhere or left it alone.
        for p in plan["problems"]:
            real.append("fit:plan")
            print(f"DIFF fit:plan: {p}")
        missed = [k for k in sorted(want) if b.get(k, ABSENT) != want[k]]
        for k in missed:
            real.append(f"fit:{k}")
            print(f"DIFF fit:{k}: {json.dumps(a.get(k, '<absent>'))} should have been fitted to "
                  f"{want[k]}, and is {json.dumps(b.get(k, '<absent>'))}")
        if not missed and not plan["problems"]:
            for d in plan["disks"].values():
                what = f"{d['name']} by {d['amount']}" if d["amount"] else "no partition of it resized"
                print(f"fitted as --fit asks: {d['disk']} by {plan['delta']} bytes, {what}")
    for why, keys in ignored.items():
        print(f"ignored ({why}): {len(keys)}: {', '.join(keys[:6])}{' ...' if len(keys) > 6 else ''}")
    if dst.get("warnings"):
        print(f"restored dump's warnings: {dst['warnings']}")
    print(f"{len(real)} differences, {sum(len(v) for v in ignored.values())} ignored, of {len(set(a) | set(b))} fields")
    sys.exit(1 if real else 0)


if __name__ == "__main__":
    main()
