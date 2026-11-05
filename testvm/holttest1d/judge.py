#!/usr/bin/env python3
"""Compare two bko-holt dumps of one machine: the source's, and the restored
copy's after it booted.  Prints one DIFF line per field that differs, and
exits 1 if any field outside IGNORED differs.

Usage: judge.py [--grow BYTES] SRC.json DST.json
       judge.py fs FSMETA.py SRC-META.json DST-META.json

The fs form judges fsmeta.py's records of the source and the restored disk:
every path must match but what load's boot stage writes by design (BOOT_STAGE).

--grow BYTES is for a restore onto disks BYTES bigger than the source's: a
disk's size, GPT's last usable LBA and backup header, and the protective MBR's
partition may then differ by exactly that much, and nothing else may.

IGNORED lists what may differ between two boots of the same machine, or can't
be kept by a restore, each with why.  judge.py prints which of them did differ,
so the list can't grow without anyone seeing it.
"""

import json
import re
import sys

# Field-path patterns (dot-separated, list indexes as numbers) and why
# they may differ.
IGNORED = [
    (r"^generated$", "when the dump was made"),
    (r"^tool\.version$", "the bko-holt build"),
    (r"(^|\.)(used_bytes|available_bytes|free_blocks|free_inodes|bytes_used)$", "usage changes as the machine runs"),
    (r"(^|\.)(mkfs_time|creation_time|update_time|generation|events)$", "when a filesystem or array was made or last written"),
    (r"^storage\.btrfs\.\d+\.subvolumes\.\d+\.gen$", "a btrfs subvolume's last-written generation"),
    (r"^storage\.btrfs\.\d+\.subvolumes\.\d+\.uuid$", "btrfs subvolume UUIDs can't be set: `btrfs subvolume create` makes new ones"),
    (r"^storage\.disks\.\d+\.boot_code\.first_bytes$", "grub-install keeps the MBR's BPB area (bytes 3-89) from whatever was there before; the kind and core.img fields are compared"),
    (r"(^|\.)state$", "a mounted filesystem's clean flag"),
    (r"(^|\.)(last_mounted_on)$", "ext4 records where it was last mounted; the restore mounted it under /mnt/bko-target"),
    (r"^warnings$", "compared separately"),
    (r"(^|\.)(sync_action|degraded)$", "md's background state"),
    (r"^storage\.loop_devices\.", "loop devices are attached at run time (snaps), in no fixed order"),
    (r"^storage\.mounts\.\d+\.(devno|size_bytes)$", "a mount's device number and size are the running system's (ZFS's change at each boot)"),
    (r"^storage\.disks\.\d+\.partitions\.\d+\.content\.device_uuid$", "an md member's own Device UUID: mdadm --create can't set it"),
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
    (r"^boot/(initrd\.img-[^/]+|initramfs-[^/]+)$", _WRITTEN, "the boot stage rebuilds every initramfs (Alpine's have no .img)"),
    (r"^boot$", {"mtime (dirs)", "atime (dirs)"}, "update-initramfs replaces the initrds"),
    (r"^var/log/bko-holt(/|$)", _WRITTEN, "load's own log, copied in by the boot stage"),
    (r"^etc/zfs/zpool\.cache$", _WRITTEN, "the boot stage writes the new pool's zpool.cache"),
    (r"^var/log$", {"mtime (dirs)", "atime (dirs)"}, "var/log/bko-holt made"),
    (r"^var/lib/initramfs-tools(/|$)", _WRITTEN, "update-initramfs records the kernels it built for"),
    (r"^var/lib/dracut(/|$)", _WRITTEN, "dracut's console-setup module, run by the boot stage's initramfs rebuild"),
    (r"^boot/efi[^/]*/EFI(/|$)", _WRITTEN, "grub-install writes the ESPs' loaders (a second ESP by the distribution's hook)"),
    (r"^(boot/)?efi/EFI(/(BOOT|Boot|systemd|Linux)(/.*)?)?$", _WRITTEN, "bootctl install rewrites systemd-boot and makes EFI/Linux in an ESP at /efi (AerynOS)"),
    (r"^(boot/)?efi/loader(/(random-seed|keys))?$", _WRITTEN, "bootctl install writes a new random seed and makes loader/keys"),
    (r"^(tmp|var/tmp)$", {"mtime (dirs)", "atime (dirs)"}, "update-initramfs and update-grub work in temporary directories"),
    (r"^var/lib/os-prober(/|$)", _WRITTEN, "update-grub runs os-prober (GRUB_DISABLE_OS_PROBER=false)"),
]


def flatten(v, path, out):
    if isinstance(v, dict):
        for k, x in v.items():
            flatten(x, f"{path}.{k}" if path else k, out)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            flatten(x, f"{path}.{i}" if path else str(i), out)
    else:
        out[path] = v


def judge_fs(fsmeta_py, src_path, dst_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("fsmeta", fsmeta_py)
    fsmeta = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fsmeta)
    before = json.load(open(src_path))
    diff = fsmeta.differences(before, json.load(open(dst_path)))
    real, allowed = {}, {}
    for kind, paths in diff.items():
        for p in paths:
            why = next((w for r, kinds, w in BOOT_STAGE if kind in kinds and re.search(r, p)), None)
            if not why and kind == "missing" and before.get(p, {}).get("type") == "socket":
                why = "a socket is made by whatever listens on it; no archiver restores one"
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


def main():
    if sys.argv[1] == "fs":
        judge_fs(*sys.argv[2:5])
    grow = 0
    if sys.argv[1] == "--grow":
        grow = int(sys.argv[2])
        del sys.argv[1:3]
    src = json.load(open(sys.argv[1]))
    dst = json.load(open(sys.argv[2]))
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
    a, b = {}, {}
    flatten(src, "", a)
    flatten(dst, "", b)
    real, ignored = [], {}
    for k in sorted(set(a) | set(b)):
        if a.get(k, "<absent>") == b.get(k, "<absent>"):
            continue
        why = next((w for p, w in IGNORED if re.search(p, k)), None)
        va, vb = a.get(k), b.get(k)
        if not why and grow:
            why = grown(k, va, vb, grow, a)
        if not why and k.startswith("storage.zfs.zpool_cache.") and k not in a:
            why = "zpool.cache fields only the rescue system's newer OpenZFS writes"
        if not why and re.search(r"\.features\.[a-z_]+$", k) and {va, vb} <= {"enabled", "active"}:
            why = "a ZFS feature is active once something uses it (block cloning: cp)"
        if not why and k.endswith(".version") and a.get(k[: -len("version")] + "type") == "f2fs":
            why = "an F2FS superblock records the f2fs-tools release that made it (the rescuer's mkfs.f2fs, not the source's), and its checksum covers it"
        if not why and k.endswith(".inodes") and a.get(k[: -len("inodes")] + "type") == "xfs":
            why = "XFS's sb_icount counts the inodes allocated so far (in chunks of 64, as files are made): usage, not layout"
        m = re.match(r"^(storage\.other_devices\.\d+)\.", k)
        if not why and m and a.get(m.group(1) + ".kind") == "zram":
            why = "zram is made anew at each boot"
        if not why and k not in a and any(re.search(r, k) for r in NEW_FIELDS):
            why = "a field bko-holt began recording after the source's dump was made"
        if not why and all(isinstance(v, str) and v.startswith("/dev/disk/by-diskseq/") for v in (va, vb)):
            why = "by-diskseq links count the disks the kernel has seen since it booted"
        if why:
            ignored.setdefault(why, []).append(k)
        else:
            real.append(k)
            print(f"DIFF {k}: {json.dumps(a.get(k, '<absent>'))[:200]} -> {json.dumps(b.get(k, '<absent>'))[:200]}")
    for why, keys in ignored.items():
        print(f"ignored ({why}): {len(keys)}: {', '.join(keys[:6])}{' ...' if len(keys) > 6 else ''}")
    if dst.get("warnings"):
        print(f"restored dump's warnings: {dst['warnings']}")
    print(f"{len(real)} differences, {sum(len(v) for v in ignored.values())} ignored, of {len(set(a) | set(b))} fields")
    sys.exit(1 if real else 0)


if __name__ == "__main__":
    main()
