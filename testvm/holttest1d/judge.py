#!/usr/bin/env python3
"""Compare two bko-holt dumps of one machine: the source's, and the restored
copy's after it booted.  Prints one DIFF line per field that differs, and
exits 1 if any field outside IGNORED differs.

Usage: judge.py [--expect] [--new-uuids] [--removable] [--nvram-kept] [--reformat MOUNT=TYPE]...
                [--grow BYTES | --fit BYTES [--fit-pct PCT] | --degraded] SRC.json DST.json
       judge.py fs FSMETA.py SRC-META.json DST-META.json [--no-atime] [--new-uuids] [--reformat MOUNT=TYPE]...
       judge.py esp DIR
       judge.py bes SRC.json DST.json
       judge.py zbm SRC.json DST.json SRC-META.json DST-META.json
       judge.py zbm-boot STATE.txt SERIAL.log DATASET KVER PROMPTS
       judge.py qubes SRC.json DST.json SRC-RAW.txt DST-RAW.txt
       judge.py qubes-check SRC-CHECK.log DST-CHECK.log
       judge.py --fit-plan BYTES [--fit-pct PCT] LAYOUT.json

The fs form judges fsmeta.py's records of the source and the restored disk:
every path must match but what load's boot stage writes by design (BOOT_STAGE).
The esp form judges a Proxmox source's ESPs, which proxmox-boot-tool rebuilds
(judge_esp).  The bes form judges a TrueNAS source's boot-pool (judge_bes), the
zbm form a ZFSBootMenu source's boot environments, images and pool
properties, from both dumps and both fsmeta records (judge_zbm), the zbm-boot form
the passphrase prompts of one boot of a BE (judge_zbm_boot).  The qubes
form judges a Qubes OS source's thin snapshots, pools and raw volumes
(judge_qubes), the qubes-check form the restored disk's bko-qubes-check.service
lines against the source's (judge_qubes_check): ht1-offline.sh's, Q7 of
docs/design/qubes.md.

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
--reformat MOUNT=TYPE
               load's --reformat (docs/design/reformat.md): the filesystem at
               MOUNT is TYPE now, with its UUID and label; its geometry, its
               btrfs record and the subvolume mounts folded into MOUNT are
               the change's, and fstab, the mounts and the root's kernel
               command lines must be as load rewrites them (reformat_expect).
               The fs form allows the files the rewrite edits, mke2fs's
               lost+found, btrfs's inode flags gone, the snapshots left out,
               and XFS's copies of the ACLs (reformat_why)
--nvram-kept   tier 2's boots shared one NVRAM with the rescuer: the firmware's
               own boot entries (no HD() path: its disks, network, shell), the
               entries on a disk the machine doesn't have (the rescuer's own:
               its shim's fallback adds one), and the boot order and current
               entry are the rescue session's; the entries on the machine's
               disks (HD(), a partition the dump found) are compared, as a set

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
    (r"^boot\.proxmox_boot\.esps\.\d+\.contents\.files\.\d+\.size$", "the boot stage rebuilds the initramfs images, and proxmox-boot-tool refresh copies them to each ESP (judge.py esp holds every file's bytes to the source's)"),
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
    (r"^boot\.truenas\.bes\.\d+\.(origin|clones)(\.|$)", "TrueNAS: a boot environment's clones come back as plain datasets, their origin snapshots not being backed up (the spec's Decision 4; judge.py bes holds the rest)"),
    (r"^boot\.zfsbootmenu\.bes\.\d+\.origin$", "ZFSBootMenu: a clone boot environment comes back as a plain dataset, its origin snapshot not being backed up (docs/design/zfsbootmenu.md, Decision 3a; judge.py zbm holds the rest)"),
    (r"^storage\.luks_headers(\.|$)", "the LUKS2 headers the source's dump was asked to keep (--keep-luks-header: aeon's, for load to restore); the restored system's dump isn't asked: the containers themselves (UUID, keyslots, tokens) are compared"),
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
    """--nvram-kept: only the boot entries on the machine's disks, as a set; the rest is the rescue session's."""
    efi = (d.get("boot") or {}).get("efi")
    if not isinstance(efi, dict):
        return
    ents = [e for e in efi.get("entries") or [] if any("HD(" in str(p) for p in e.get("device_path") or [])
            and ((e.get("partition") or {}).get("device") or not isinstance(e.get("partition"), dict))]
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
    # Gentoo: load runs dracut itself, whose images are 0600 (its umask
    # 077); installkernel had left the source's 0644 (gentoo, G5's g5-1).
    (r"^boot/initramfs-[^/]*-gentoo[^/]*\.img$", {"mode"}, "dracut makes Gentoo's initramfs 0600 where installkernel had left it 0644"),
    # Garuda: load runs dracut-rebuild, whose dracut-install-garuda does
    # rm -f, dd and chmod 0644 on /boot/vmlinuz-PKGBASE before the images
    # (garuda, GA4's ga4-2): the same bytes in a new file, so only its times
    # move.  Content, mode and owner still count.
    (r"^boot/vmlinuz-[^/]+$", {"mtime", "atime"}, "Garuda's dracut-rebuild (garuda-dracut-support) copies each kernel to /boot again with dd before building its images"),
    # PikaOS: load runs `booster build` per kernel; its .dpkg-bak and .bak
    # copies it leaves alone.
    (r"^boot/booster\.img-(?![^/]*\.(dpkg-)?bak$)[^/]+$", _WRITTEN, "booster build rebuilds each kernel's booster.img (PikaOS)"),
    (r"^boot$", {"mtime (dirs)", "atime (dirs)"}, "update-initramfs replaces the initrds"),
    # Limine (CachyOS): limine-mkinitcpio-hook copies each initramfs that
    # mkinitcpio -P rebuilds into /boot/MACHINE-ID/KERNEL/, where
    # limine.conf boots it.  The kernel beside it stays the source's.
    (r"^boot/[0-9a-f]{32}/[^/]+/initramfs$", _WRITTEN, "limine-mkinitcpio-hook copies the rebuilt initramfs into /boot/MACHINE-ID/KERNEL/ (CachyOS's Limine)"),
    (r"^boot/[0-9a-f]{32}/[^/]+$", {"mtime (dirs)", "atime (dirs)"}, "limine-mkinitcpio-hook replaces the initramfs in its directory"),
    (r"^var/log/bko-holt(/|$)", _WRITTEN, "load's own log, copied in by the boot stage"),
    (r"^etc/zfs/zpool\.cache$", _WRITTEN, "the boot stage writes the new pool's zpool.cache"),
    (r"^var/log$", {"mtime (dirs)", "atime (dirs)"}, "var/log/bko-holt made"),
    (r"^var/lib/initramfs-tools(/|$)", _WRITTEN, "update-initramfs records the kernels it built for"),
    (r"^var/lib/dracut(/|$)", _WRITTEN, "dracut's console-setup module, run by the boot stage's initramfs rebuild"),
    # AnduinOS (a5-1, a5-2): the source's own dracut run left the module's
    # mktemp file behind; the boot stage's run makes the directory afresh.
    (r"^var/lib/dracut/console-setup-dir/etc/console-setup/tmpkbd\.[A-Za-z0-9]+$", {"missing", "extra"}, "dracut's console-setup module leaves its temporary tmpkbd.XXXXXX behind, and its run in the boot stage remakes the directory"),
    # openSUSE (Leap 16): the ESP's grub.cfg stub may change as
    # /boot/grub2/grub.cfg does; OPENSUSE_KEPT holds the rest of the ESP.
    (r"^boot/efi/EFI/opensuse/grub\.cfg$", _WRITTEN, "openSUSE's grub.cfg stub on the ESP, which only finds /boot/grub2's grub.cfg, may change with it (grub2-mkconfig)"),
    (r"^boot/efi[^/]*/EFI(/|$)", _WRITTEN, "grub-install writes the ESPs' loaders (a second ESP by the distribution's hook)"),
    (r"^(boot/)?efi/EFI(/(BOOT|Boot|systemd|Linux)(/.*)?)?$", _WRITTEN, "bootctl install rewrites systemd-boot and makes EFI/Linux in an ESP at /efi (AerynOS)"),
    (r"^(boot/)?efi/loader(/(random-seed|keys))?$", _WRITTEN, "bootctl install writes a new random seed and makes loader/keys"),
    # An ESP mounted as /boot itself (Chimera): its EFI/ is boot/EFI, which
    # nothing but an ESP has, and its loader/ is boot/loader.
    (r"^boot/EFI/(BOOT(/BOOT[A-Z0-9]+\.EFI)?|systemd(/systemd-boot[a-z0-9]+\.efi)?)$", _WRITTEN, "bootctl install rewrites systemd-boot in an ESP at /boot (Chimera)"),
    (r"^boot/loader/random-seed$", _WRITTEN, "bootctl install writes a new random seed in an ESP at /boot"),
    (r"^etc/kernel/entry-token$", _WRITTEN, "bootctl install writes the entry token (all it writes in /etc/kernel; install.conf it only reads)"),
    (r"^etc/kernel$", {"mtime (dirs)", "atime (dirs)"}, "bootctl install replaces etc/kernel/entry-token"),
    # Proxmox: `proxmox-boot-tool init` adds each ESP's line, and load writes
    # the file back as dumped; its bytes still count.
    (r"^etc/kernel/proxmox-boot-uuids$", {"mtime", "atime"}, "load writes proxmox-boot-uuids back as dumped after proxmox-boot-tool init"),
    (r"^etc/apt/apt\.conf\.d/76pveconf$", {"mtime", "atime"}, "Proxmox's kernel postinst hook (proxmox-auto-removal), run by proxmox-boot-tool, writes its list of kernels apt keeps again"),
    (r"^etc/apt/apt\.conf\.d$", {"mtime (dirs)", "atime (dirs)"}, "proxmox-auto-removal replaces 76pveconf"),
    (r"^var/tmp/espmounts$", {"mtime (dirs)", "atime (dirs)"}, "proxmox-boot-tool mounts each ESP under /var/tmp/espmounts/ID while it works"),
    (r"^efi/EFI/endeavouros(/|$)", _WRITTEN, "grub-install writes the loader in an ESP at /efi (EndeavourOS with GRUB)"),
    (r"^efi/EFI/gentoo(/|$)", _WRITTEN, "grub-install writes the loader in an ESP at /efi (Gentoo)"),
    (r"^boot/EFI/gentoo(/|$)", _WRITTEN, "grub-install writes the loader in an ESP at /boot (Gentoo with systemd)"),
    (r"^(boot/)?efi/[0-9a-f]{32}/[^/]+/initrd$", _WRITTEN, "dracut --regenerate-all rebuilds a Type 1 entry's initrd, on the ESP under the machine id (EndeavourOS)"),
    (r"^(boot/)?efi/[0-9a-f]{32}/[^/]+$", {"mtime (dirs)", "atime (dirs)"}, "dracut replaces the initrd in its directory"),
    (r"^(tmp|var/tmp)$", {"mtime (dirs)", "atime (dirs)"}, "update-initramfs and update-grub work in temporary directories"),
    (r"^var/lib/os-prober(/|$)", _WRITTEN, "update-grub runs os-prober (GRUB_DISABLE_OS_PROBER=false)"),
    (r"^(var/log/kernelstub\.log|etc/kernelstub/configuration)$", _WRITTEN, "Pop!_OS's kernelstub, run after update-initramfs, logs and rewrites its configuration"),
    (r"^boot/efi/loader/entries/[^/]+$", _WRITTEN, "kernelstub rewrites systemd-boot's entries"),
    (r"^usr/lib/modules/[^/]+(/modules\.[^/]+)?$", _WRITTEN, "depmod, run by the initramfs rebuild (Pop!_OS), rewrites modules.*"),
    (r"^boot(/EFI/Linux(/[^/]+\.efi)?|/limine\.conf(\.old)?)$", _WRITTEN, "Omarchy's mkinitcpio hook rebuilds the unified kernel image and Limine's entries"),
    (r"^boot(/|$)", {"selinux"}, "the boot stage relabels /boot (setfiles): the policy's labels, where the source's can be stale (tumbleweed's update left its new kernel's files modules_object_t)"),
    # Qubes OS (ht1-offline.sh's backup): the raw volumes' images go in the
    # backup under /.bko-holt/raw, which load writes into their LVs instead.
    (r"^\.bko-holt(/raw)?$", {"missing", "extra", "mtime (dirs)", "atime (dirs)"}, "the backup's raw-volume directory (/.bko-holt/raw: Qubes's vm-* images, docs/design/qubes.md), which load restores into the LVs, not as files"),
]
# A rEFInd source (PikaOS: EFI/refind, or "BOOTX64 mode"'s EFI/BOOT alone),
# and what load must leave as the restore made it there.
REFIND_SOURCE = r"(?i)^boot/(refind_linux\.conf|efi/EFI/[^/]+/refind(_x64\.efi|\.conf))$"
REFIND_KEPT = r"(?i)^boot/(refind_linux\.conf|efi/EFI/(refind|BOOT)(/.*)?)$"
# An AnduinOS source (2.0.5's anduinos-grub: GRUB signed on the machine with
# its own MOK, behind Ubuntu's shim; docs/design/anduinos.md): load keeps the
# restored ESP and runs no x86_64-efi grub-install, so shim, MokManager and
# the MOK-signed GRUB, in EFI/AnduinOS and in the fallback EFI/BOOT, must be
# byte for byte the source's.  EFI/AnduinOS's three must be in the source's
# record; EFI/BOOT's are held to it where the source has them (an install
# that wasn't "erase disk" has no fallback copies).
ANDUINOS_SOURCE = r"^boot/efi/EFI/AnduinOS/shimx64\.efi$"
ANDUINOS_KEPT = r"^boot/efi/EFI/(AnduinOS/(shimx64|grubx64|mmx64)\.efi|BOOT/(BOOTX64\.EFI|grubx64\.efi|mmx64\.efi))$"
ANDUINOS_REQUIRED = ("boot/efi/EFI/AnduinOS/shimx64.efi", "boot/efi/EFI/AnduinOS/grubx64.efi", "boot/efi/EFI/AnduinOS/mmx64.efi")
# An ABRoot source (Vanilla OS 3: docs/design/vanillaos.md, V4 and V7): load
# regenerates nothing (no initramfs, grub-install, update-grub or chroot), so
# the master grub.cfg and grub.cfg.future, each root's abroot.cfg, kernel,
# initramfs, config and System.map in the init LV (boot/init/LABEL/), and the
# ESP's EFI/vanilla and EFI/BOOT (the guest's fallback copy) must be byte for
# byte the source's: no ESP or /boot rule excuses them.  The master
# grub.cfg, shim and GRUB in EFI/vanilla, and a kernel and an initramfs
# beside each abroot.cfg must be in the source's record.
ABROOT_SOURCE = r"^boot/init/[^/]+/abroot\.cfg$"
ABROOT_KEPT = r"^boot/(grub/grub\.cfg(\.future)?|init/[^/]+/(abroot\.cfg|(vmlinuz|initrd\.img|config|System\.map)-[^/]+)|efi/EFI/(vanilla|BOOT)(/[^/]+)?)$"
ABROOT_REQUIRED = ("boot/grub/grub.cfg", "boot/efi/EFI/vanilla/shimx64.efi", "boot/efi/EFI/vanilla/grubx64.efi")
# A Solus source (4.9, clr-boot-manager; docs/design/solus.md, S5): the ESP
# is /boot itself (out of fstab: vm-mount mounts it there, ht1.sh runs
# `clr-boot-manager mount-boot` for the dumps), and load's boot stage makes
# only the NVRAM entry: no bootctl, no clr-boot-manager, no initramfs.  So
# the whole ESP must be byte for byte the source's: shim (bootloaderx64.efi
# and the EFI/BOOT fallback), systemd-boot (grubx64.efi), MokManager, the
# kernels and initrds, loader/ and Solus's MOK certificate.  None of the
# Chimera (boot/EFI/BOOT, boot/loader/random-seed) or Fedora (boot/loader)
# rules may excuse them.  A source is one with CBM's shim on the ESP or a
# Solus kernel in /usr/lib64/kernel; SOLUS_REQUIRED and a kernel, an initrd
# and an entry must be in its record.
SOLUS_SOURCE = r"^(boot/EFI/com\.solus-project/bootloaderx64\.efi|usr/lib64/kernel/com\.solus-project\.[^/]+)$"
SOLUS_KEPT = r"^boot/((EFI|loader)(/.*)?|solus-enroll-me\.cer)$"
SOLUS_REQUIRED = ("boot/EFI/com.solus-project/bootloaderx64.efi", "boot/EFI/com.solus-project/grubx64.efi",
                  "boot/EFI/com.solus-project/mmx64.efi", "boot/EFI/BOOT/BOOTX64.EFI", "boot/loader/loader.conf",
                  "boot/solus-enroll-me.cer")
SOLUS_GLOBS = ((r"^boot/EFI/com\.solus-project/kernel-com\.solus-project\.[^/]+$", "boot/EFI/com.solus-project/kernel-com.solus-project.*"),
               (r"^boot/EFI/com\.solus-project/initrd-com\.solus-project\.[^/]+$", "boot/EFI/com.solus-project/initrd-com.solus-project.*"),
               (r"^boot/loader/entries/Solus-[^/]+\.conf$", "boot/loader/entries/Solus-*.conf"))
# An openSUSE source booting GRUB 2 through shim from its ESP at /boot/efi
# (Leap 16: docs/design/leap16.md, L5): load's /boot/grub2 UEFI arm (the
# Fedora arm: no grub2-install, no shim-install) keeps the restored ESP and
# remakes only /boot/grub2/grub.cfg, then the NVRAM entry.  So every file in
# EFI/opensuse (SUSE's signed shim, GRUB and MokManager, boot.csv) and in
# the fallback EFI/boot must be byte for byte the source's: no ESP rule
# excuses them.  The grub.cfg stub there is the one exception: like
# /boot/grub2/grub.cfg it may change (BOOT_STAGE's rule for it).
# EFI/opensuse's shim, GRUB and MokManager and EFI/boot/bootx64.efi must be
# in the source's record.  (A transactional source, MicroOS, has its own
# rules: TU_STAGE excuses nothing there.)
OPENSUSE_SOURCE = r"^boot/efi/EFI/opensuse/shim\.efi$"
OPENSUSE_KEPT = r"^boot/efi/EFI/(opensuse/(?!grub\.cfg$)|boot/)[^/]+$"
OPENSUSE_REQUIRED = ("boot/efi/EFI/opensuse/shim.efi", "boot/efi/EFI/opensuse/grub.efi", "boot/efi/EFI/opensuse/MokManager.efi",
                     "boot/efi/EFI/boot/bootx64.efi")
# A ZFSBootMenu source (void-zbm: docs/design/zfsbootmenu.md): load installs
# no loader (its zfsbootmenu arm: the ESP's images come back with the
# restore, and the NVRAM entries are made by label), so EFI/ZBM (zbm,
# zfsbootmenu) and its images must be byte for byte the source's: no ESP
# rule excuses them.  (The inactive boot environments' /boot, under
# run/bko-be/NAME, no rule excuses anyway; judge.py zbm checks their
# kernels and initramfs files are there.)
ZBM_SOURCE = r"(?i)^(boot/efi|efi|boot)/EFI/(zbm|zfsbootmenu)/[^/]+\.efi$"
ZBM_KEPT = r"(?i)^(boot/efi|efi|boot)/EFI/(zbm|zfsbootmenu)(/.*)?$"
# LILO (slackware15: docs/design/slackware.md, S6): load writes a copy of
# /etc/lilo.conf with root= by name at the end of append=
# (lilo.conf.bko-holt: lilo stores root as the rescue kernel's device
# number), runs lilo -C on it, and removes it.  lilo writes the map (the
# restored kernels' sectors), and a backup of the MBR it replaces as
# /boot/boot.MMmm, named by the disk's device number in the rescue system,
# when there is none of that name (the source's is kept).  So /etc/lilo.conf
# is the source's (no rule excuses it), and the copy must not be left
# behind.  A source is one with /etc/lilo.conf and lilo's /boot/map.
LILO_SOURCE = ("etc/lilo.conf", "boot/map")
LILO_COPY = "etc/lilo.conf.bko-holt"
LILO_STAGE = [
    (r"^boot/map$", _WRITTEN - {"extra"}, "lilo writes its map: the restored kernels' sectors"),
    (r"^boot/boot\.[0-9a-f]{4}$", {"extra"}, "lilo backs up the MBR it replaces as /boot/boot.MMmm, named by the disk's device number in the rescue system"),
    (r"^etc$", {"mtime (dirs)", "atime (dirs)"}, "load writes etc/lilo.conf.bko-holt for lilo -C, then removes it"),
]
# mkinitrd (Slackware's generic kernel, slackware15-uefi): load runs the
# source's mkinitrd command again (the command generator's, in the dump),
# which remakes its tree in /boot/initrd-tree (-c clears it) and
# /boot/initrd.gz.  A source is one with /etc/slackware-version.
MKINITRD_STAGE = [
    (r"^boot/initrd\.gz$", _WRITTEN - {"extra"}, "load runs the source's mkinitrd command again: /boot/initrd.gz"),
    (r"^boot/initrd-tree(/.*)?$", _WRITTEN | {"missing"}, "mkinitrd remakes its tree, /boot/initrd-tree (its -c clears it first)"),
]
# ELILO (slackware15-uefi, S6): load installs nothing, so elilo.efi,
# elilo.conf and the kernel in EFI/Slackware and the fallback EFI/BOOT
# (BOOTX64.EFI) come back with the ESP and must be the source's bytes, which
# grub-install's ESP rule may not excuse.  It copies the remade
# /boot/initrd.gz over each ESP initrd.gz that was a copy of it on the source
# (cp, touch -r): such a one must have the restored /boot/initrd.gz's bytes;
# one that wasn't is held to the source's.  A source is one with an
# elilo.efi or elilo.conf in an ESP directory at /boot/efi; EFI/Slackware's
# four (and BOOTX64.EFI where EFI/BOOT has an elilo.conf) must be in its
# record.
ELILO_SOURCE = r"(?i)^boot/efi/EFI/[^/]+/elilo\.(efi|conf)$"
ELILO_KEPT = r"(?i)^boot/efi/EFI(/(Slackware|BOOT)(/[^/]+)?)?$"
ELILO_INITRD = r"(?i)^boot/efi/EFI/(Slackware|BOOT)/initrd\.gz$"
ELILO_REQUIRED = ("boot/efi/EFI/Slackware/elilo.efi", "boot/efi/EFI/Slackware/elilo.conf", "boot/efi/EFI/Slackware/vmlinuz",
                  "boot/efi/EFI/Slackware/initrd.gz")


def elilo_missing(before):
    """What an ELILO source's record lacks of ELILO_REQUIRED, and of
    EFI/BOOT/BOOTX64.EFI beside an EFI/BOOT/elilo.conf (vfat: by any case)."""
    files = {p.lower() for p, m in before.items() if m.get("type") == "file"}
    want = list(ELILO_REQUIRED)
    if "boot/efi/efi/boot/elilo.conf" in files:
        want.append("boot/efi/EFI/BOOT/BOOTX64.EFI")
    return [p for p in want if p.lower() not in files]


def elilo_initrd_copies(before):
    """The ESP initrd.gz files that were byte copies of /boot/initrd.gz on
    the source: the ones load copies the remade image over."""
    b = before.get("boot/initrd.gz") or {}
    if b.get("type") != "file":
        return set()
    return {p for p, m in before.items() if re.search(ELILO_INITRD, p) and m.get("type") == "file"
            and (m.get("size"), m.get("sha256")) == (b.get("size"), b.get("sha256"))}


# A transactional-update source (openSUSE MicroOS; docs/design/aeon.md, the
# transactional arm): load regenerates nothing in a snapshot (no
# grub2-mkconfig, grub2-install, shim-install, dracut, sdbootutil or relabel
# of the root), so no boot-stage rule excuses anything there: the ESP, every
# slot's /boot and /usr, grub.cfg and the initrds must be byte for byte the
# source's.  What load writes is its own log and health-check.state's
# LAST_WORKING_BTRFS_ID, rewritten to the restored ID of its
# LAST_WORKING_SNAPSHOT (ht1-tu.sh holds the rest of the file to the
# source's, and the ID to the restored subvolume's).
TU_SOURCE = r"^usr/sbin/tukit$"
TU_STAGE = [
    (r"^var/log/bko-holt(/|$)", _WRITTEN, "load's own log, copied in by the boot stage"),
    (r"^var/log$", {"mtime (dirs)", "atime (dirs)"}, "var/log/bko-holt made"),
    (r"^var/lib/misc/health-check\.state$", _WRITTEN, "the boot stage rewrites health-checker's LAST_WORKING_BTRFS_ID to the restored ID of its LAST_WORKING_SNAPSHOT (ht1-tu.sh checks the file)"),
    (r"^var/lib/misc$", {"mtime (dirs)", "atime (dirs)"}, "health-check.state rewritten"),
]
TU_HELD = r"^(boot|usr)(/|$)|^\.snapshots/\d+/snapshot/(boot|usr)(/|$)"
# An Ubuntu Core source (docs/design/ubuntu-core.md, U6): vm-mount's tree has
# the four roles under run/mnt/ (ubuntu-seed, ubuntu-boot, ubuntu-save, and
# ubuntu-data at run/mnt/data, whose system-data/ holds the writable paths:
# the running system's /var/log is run/mnt/data/system-data/var/log).
# Load's snapcore boot stage regenerates nothing (no grub-install, no
# initramfs, no NVRAM entry): it writes the boot areas back as dumped and
# copies its own log in, so no other boot-stage rule applies.  Everything on
# ubuntu-seed and ubuntu-boot, every .snap, modeenv and state.json must be
# byte for byte the source's, and SNAPCORE_REQUIRED must be in the source's
# record (with a .snap in the seed's snaps/ and in snapd's, and a kernel.efi
# on ubuntu-boot), so that a record missing a role can't pass by having
# nothing to compare.
SNAPCORE_SOURCE = "run/mnt/data/system-data/var/lib/snapd/modeenv"   # (load's snapcore::MARKER)
SNAPCORE_STAGE = [
    (r"^run/mnt/data/system-data/var/log/bko-holt(/|$)", _WRITTEN, "load's own log, copied in by the snapcore boot stage (Ubuntu Core's /var/log is ubuntu-data's system-data/var/log)"),
    (r"^run/mnt/data/system-data/var/log$", {"mtime (dirs)", "atime (dirs)"}, "system-data/var/log/bko-holt made"),
]
SNAPCORE_HELD = r"^run/mnt/(ubuntu-seed|ubuntu-boot)(/|$)|\.snap$|^run/mnt/data/system-data/var/lib/snapd/(modeenv|state\.json)$"
SNAPCORE_REQUIRED = (SNAPCORE_SOURCE, "run/mnt/data/system-data/var/lib/snapd/state.json",
                     "run/mnt/ubuntu-seed/EFI/ubuntu/grubenv", "run/mnt/ubuntu-seed/EFI/boot/bootx64.efi",
                     "run/mnt/ubuntu-boot/EFI/ubuntu/grubenv", "run/mnt/ubuntu-boot/EFI/ubuntu/grub.cfg")
SNAPCORE_GLOBS = [(r"^run/mnt/ubuntu-seed/snaps/[^/]+\.snap$", "run/mnt/ubuntu-seed/snaps/*.snap"),
                  (r"^run/mnt/data/system-data/var/lib/snapd/snaps/[^/]+\.snap$", "run/mnt/data/system-data/var/lib/snapd/snaps/*.snap"),
                  (r"^run/mnt/ubuntu-boot/EFI/ubuntu/[^/]+\.snap/kernel\.efi$", "run/mnt/ubuntu-boot/EFI/ubuntu/*.snap/kernel.efi")]


def snapcore_missing(before):
    """What an Ubuntu Core source's record lacks of SNAPCORE_REQUIRED and SNAPCORE_GLOBS."""
    out = [p for p in SNAPCORE_REQUIRED if before.get(p, {}).get("type") != "file"]
    return out + [g for r, g in SNAPCORE_GLOBS
                  if not any(re.search(r, p) and m.get("type") == "file" for p, m in before.items())]


def abroot_missing(before):
    """What an ABRoot source's record lacks of ABROOT_REQUIRED, and of a
    kernel and an initramfs in each init/LABEL/ that has an abroot.cfg."""
    out = [p for p in ABROOT_REQUIRED if before.get(p, {}).get("type") != "file"]
    for cfg in sorted(p for p in before if re.search(ABROOT_SOURCE, p)):
        d = cfg.rsplit("/", 1)[0]
        for kind in ("vmlinuz", "initrd.img"):
            if not any(p.startswith(f"{d}/{kind}-") and before[p].get("type") == "file" for p in before):
                out.append(f"{d}/{kind}-*")
    return out


def solus_missing(before):
    """What a Solus source's record lacks of SOLUS_REQUIRED, and of a kernel,
    an initrd and a loader entry on the ESP (SOLUS_GLOBS)."""
    out = [p for p in SOLUS_REQUIRED if before.get(p, {}).get("type") != "file"]
    out += [name for r, name in SOLUS_GLOBS if not any(re.search(r, p) and m.get("type") == "file" for p, m in before.items())]
    return out


# systemd-boot's boot counting (BLS, Aeon's sdbootutil entries): an entry
# `ID+LEFT-DONE.conf` is ID.conf with tries left; systemd-boot renames it at
# each boot of it and systemd-bless-boot drops the counter once a boot of it
# is good.  The restored machine's first boot boots the source's pending
# slot, whose entry (`+3` on the source) is then counted and blessed: the
# same entry.  So on a BLS source the ESP's entry names are compared with
# their counters taken off (the files' bytes as restored are judge.py fs's).
BLS_COUNTER = re.compile(r"\+\d+(-\d+)?(?=\.conf$)")


def bls_uncount(d):
    """Take the boot counters off D's BLS entry names (boot.esps' files and
    boot.systemd_boot.entries), each list re-sorted by name; -> how many."""
    n = 0
    boot = d.get("boot") or {}
    for e in boot.get("esps") or []:
        fs = (e.get("contents") or {}).get("files")
        if not isinstance(fs, list):
            continue
        for f in fs:
            for k in ("path", "same_as"):
                v = f.get(k)
                if isinstance(v, str) and v.startswith("loader/entries/") and BLS_COUNTER.search(v):
                    f[k] = BLS_COUNTER.sub("", v)
                    n += 1
        fs.sort(key=lambda f: str(f.get("path") or ""))
    sb = boot.get("systemd_boot") or {}
    ents = sb.get("entries") if isinstance(sb, dict) else None
    if isinstance(ents, list):
        for x in ents:
            v = x.get("file")
            if isinstance(v, str) and BLS_COUNTER.search(v):
                x["file"] = BLS_COUNTER.sub("", v)
                n += 1
        ents.sort(key=lambda x: str(x.get("file") or ""))
    return n


def transactional_judge(src, dst):
    """A transactional-update source (docs/design/aeon.md): boot.transactional
    taken off both dumps and judged here.  The restored machine must boot
    the source's default (its pending update, when one was pending), as its
    default, with nothing pending: a health-checker rollback on its first
    boot would boot another slot and still look like a working machine.
    health-checker's state (GRUB's) must then name that slot by its
    restored ID.  The slots' IDs are new (btrfs_by_path compares them by
    path), and transactional-update's own state moves as the machine boots.
    -> (problems, notes)."""
    ts = (src.get("boot") or {}).pop("transactional", None)
    td = (dst.get("boot") or {}).pop("transactional", None)
    if ts is None:
        return ([] if td is None else ["the restored system is transactional, the source wasn't"]), []
    if td is None:
        return ["the source is transactional (boot.transactional), the restored system isn't"], []
    bad, notes = [], []
    if ts.get("loader_mode") == "bls":
        ns, nd = bls_uncount(src), bls_uncount(dst)
        if ns or nd:
            notes.append(f"transactional-update: BLS entries compared without their boot counters ({ns} on the source, {nd} restored): systemd-boot counts a boot of an entry, and systemd-bless-boot drops the counter once one is good")
    for k in ("filesystem", "fstab_root_options", "tools", "etc", "loader_mode", "fde_seal_pcr_list"):
        if ts.get(k) != td.get(k):
            bad.append(f"{k}: {json.dumps(ts.get(k))} -> {json.dumps(td.get(k))}")
    path = lambda t, k: ((t.get(k) or {}).get("path"))
    want = path(ts, "default")
    if not want:
        bad.append("the source's dump has no default slot")
    if path(td, "booted") != want:
        bad.append(f"booted {path(td, 'booted')}, not the source's default {want}"
                   + (f" (the source booted {path(ts, 'booted')})" if path(ts, "booted") != want else ""))
    if path(td, "default") != want:
        bad.append(f"the default is {path(td, 'default')}, not the source's {want}")
    if td.get("pending_update"):
        bad.append("an update is pending on the restored system")
    hs, hd = ts.get("health_check_state"), td.get("health_check_state")
    if ts.get("loader_mode") == "grub" and hs:
        if not hd:
            bad.append("no health-check.state on the restored system")
        elif hd.get("last_working_snapshot") != want or hd.get("last_working_btrfs_id") != (td.get("default") or {}).get("id"):
            bad.append(f"health-check.state names {hd.get('last_working_snapshot')} ID {hd.get('last_working_btrfs_id')}, "
                       f"not the booted {want} ID {(td.get('default') or {}).get('id')}")
    # The source's next boot would have had its default at / (and its /etc
    # at /etc): the mounts, the subvolumes mounted and the backup's sources
    # say the restored machine's booted slot where the source's said its own.
    b = path(ts, "booted")
    if b and want and b != want and path(td, "booted") == want:
        old, new = "/" + b.strip("/"), "/" + want.strip("/")
        moved = 0
        st = src.get("storage") or {}
        rows = [(m, ("root", "super_options", "options")) for m in st.get("mounts") or []]
        rows += [(m, ("subvol",)) for m in st.get("backup_sources") or []]
        rows += [(m, ("subvol",)) for fs in st.get("btrfs") or [] for m in fs.get("mounted_subvolumes") or []]
        for m, keys in rows:
            for k in keys:
                v = m.get(k)
                if isinstance(v, str) and re.search(re.escape(old) + r"(/|,|$)", v):
                    m[k] = re.sub(re.escape(old) + r"(?=/|,|$)", new, v)
                    moved += 1
        if moved:
            notes.append(f"transactional-update: {moved} fields of the source's mounts, mounted subvolumes and backup sources read with its default {want} at / for its booted {b}, as its next boot would have had them")
    # transactional-update-sync-etc-state, at the first boot of a slot,
    # syncs its /etc from the slot before and deletes its etc.syncpoint
    # (the snapshot of that /etc it compares with): booted on the restored
    # machine, the pending slot's is gone, as it would be on the source.
    if want and path(td, "booted") == want:
        sp = want.strip("/") + "/etc/etc.syncpoint"
        for fs in (src.get("storage") or {}).get("btrfs") or []:
            have = lambda d: any(str(v.get("path") or "") == sp for f in (d.get("storage") or {}).get("btrfs") or [] for v in f.get("subvolumes") or [])
            if any(str(v.get("path") or "") == sp for v in fs.get("subvolumes") or []) and not have(dst):
                fs["subvolumes"] = [v for v in fs["subvolumes"] if str(v.get("path") or "") != sp]
                if isinstance(fs.get("qgroups"), list):
                    fs["qgroups"] = [q for q in fs["qgroups"] if str(q.get("path") or "") != sp]
                notes.append(f"transactional-update: {sp} consumed by the restored first boot's /etc sync (transactional-update-sync-etc-state deletes a slot's etc.syncpoint when it first boots), as the source's next boot would have")
    # A slot's subvolume ID in a mount's options follows the slots'.
    for d in (src, dst):
        for m in (d.get("storage") or {}).get("mounts") or []:
            for k in ("super_options", "options"):
                if isinstance(m.get(k), str) and re.search(r"/\.snapshots/\d+/snapshot", m[k]):
                    m[k] = re.sub(r"(^|,)subvolid=\d+", r"\1subvolid=<a slot's>", m[k])
    if not bad:
        notes.append(f"transactional-update: booted = default = the source's default {want}"
                     + (f" (pending there; the source booted {path(ts, 'booted')})" if ts.get("pending_update") else "")
                     + (f", health-checker's last good it (ID {hd.get('last_working_btrfs_id')}; the source's {hs.get('last_working_snapshot')} ID {hs.get('last_working_btrfs_id')})" if hs and hd else ""))
    if ts.get("transactional_update_state") != td.get("transactional_update_state"):
        notes.append("transactional-update's state moved as the restored machine booted (not compared): "
                     f"{json.dumps(ts.get('transactional_update_state'))[:160]} -> {json.dumps(td.get('transactional_update_state'))[:160]}")
    return bad, notes


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
# A subvolume nested in a snapper slot (a transactional-update slot's /etc,
# and the etc.syncpoint in it): made with its slot, as a snapshot of the
# slot before's (docs/design/aeon.md), so its ID, its parent's and whether
# it has an origin follow the slots'.
IN_SLOT = re.compile(r"(^|/)\.snapshots/\d+/snapshot/.")


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
            # (btrfs subvolume list -a writes <FS_TREE>/ before the paths
            # outside the subvolume mounted at /: which those are moves with
            # the root, a transactional system's booted slot.)
            if "path" in v:
                v["path"] = path
            if SNAPPER_SLOT.search(path):
                v["id"] = "<a snapper snapshot's>"
                v["parent_uuid"] = "<a snapper snapshot's>"
            elif IN_SLOT.search(path):
                for k in ("id", "parent", "top_level", "parent_uuid"):
                    if k in v:
                        v[k] = "<in a snapper snapshot>"
        subs.sort(key=lambda v: str(v.get("path") or ""))
        ds = fs.get("default_subvolume")
        if isinstance(ds, dict) and SNAPPER_SLOT.search(str(ds.get("path") or "")):
            ds["id"] = "<a snapper snapshot's>"
        qs = fs.get("qgroups") or []
        for q in qs:
            path = str(q.get("path") or "")
            if str(q.get("qgroupid") or "").startswith("0/") and (SNAPPER_SLOT.search(path) or IN_SLOT.search(path)):
                q["qgroupid"] = f"0/<{path}>"
        qs.sort(key=lambda q: (str(q.get("qgroupid") or "")[:2], str(q.get("path") or ""), str(q.get("qgroupid") or "")))
    return d


# A Timeshift snapshot in btrfs mode: TOP/timeshift-btrfs/snapshots/NAME/@...
# (load's timeshift_snapshot()).
TIMESHIFT_SNAPSHOT = re.compile(r"^(<FS_TREE>/)?timeshift-btrfs/snapshots/[^/]+/@[^/]*$")


def timeshift_left_out(src):
    """Timeshift's snapshots (BigLinux's; docs/design/biglinux.md, decision
    3) aren't in the backup, so load leaves them out with a note ("N
    Timeshift snapshots (NAMES) are not in the backup; not made"): they
    come off the source's subvolume list, and their level-0 qgroups with
    them, where nothing mounts them, as load decides.  One the restored
    dump has all the same is a DIFF.  The paths it took off."""
    out = []
    for fs in (src.get("storage") or {}).get("btrfs") or []:
        mounted = {str(m.get("subvol") or "").lstrip("/") for m in fs.get("mounted_subvolumes") or []}
        gone = [v for v in fs.get("subvolumes") or []
                if TIMESHIFT_SNAPSHOT.search(str(v.get("path") or "")) and str(v.get("path")).removeprefix("<FS_TREE>/") not in mounted]
        if not gone:
            continue
        ids = {f"0/{v.get('id')}" for v in gone}
        fs["subvolumes"] = [v for v in fs["subvolumes"] if not any(v is g for g in gone)]
        if isinstance(fs.get("qgroups"), list):
            fs["qgroups"] = [q for q in fs["qgroups"] if str(q.get("qgroupid")) not in ids]
        out += [str(v.get("path")).removeprefix("<FS_TREE>/") for v in gone]
    return out


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


def dm_names(d):
    """{dm-N: its device-mapper name} of the dump's dm_devices."""
    return {x.get("name"): (x.get("dm") or {}).get("dm_name") for x in (d.get("storage") or {}).get("dm_devices") or []
            if (x.get("dm") or {}).get("dm_name")}


def dm_by_name(d):
    """A device-mapper device's kernel number (dm-N) is the order the
    volume groups came up at boot, which needn't be the source's (vanilla3:
    vos-var before vos-root after the restore): each dm-N is called
    dm:NAME (its device-mapper name) throughout the dump instead, its device
    number 253:<dm:NAME>, and dm_devices are sorted by those names.  main
    does it only when the two dumps number the same devices differently."""
    ren = {k: f"dm:{v}" for k, v in dm_names(d).items()}
    if not ren:
        return d
    word = re.compile(r"\b(" + "|".join(map(re.escape, sorted(ren, key=len, reverse=True))) + r")\b")

    def walk(v):
        if isinstance(v, dict):
            return {(word.sub(lambda m: ren[m.group(1)], k) if isinstance(k, str) else k): walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return word.sub(lambda m: ren[m.group(1)], v) if isinstance(v, str) else v

    d = walk(d)
    devs = d["storage"]["dm_devices"]
    for x in devs:
        if x.get("name") in ren.values():
            x["devno"] = f"253:<{x['name']}>"
    devs.sort(key=lambda x: x.get("name") or "")
    return d


def lvcreate_made(d):
    """The LVs load makes anew by lvcreate, which vgcfgrestore can't
    (docs/design/vanillaos.md, V3): each thin pool, its data and metadata
    LVs, the group's pool metadata spare and the thin LVs, as {(VG, LV)},
    with their LV UUIDs; their groups' LV lists sorted by name (lvcreate
    writes them in its own order).  Returns (names, uuids)."""
    names, uuids = set(), set()
    for vg in ((d.get("storage") or {}).get("lvm") or {}).get("volume_groups") or []:
        lvs = vg.get("logical_volumes") or []
        made = set()
        for lv in lvs:
            for s in lv.get("segments") or []:
                if s.get("type") == "thin-pool":
                    made |= {lv.get("name"), s.get("metadata"), s.get("pool")}
                elif s.get("type") == "thin":
                    made.add(lv.get("name"))
        if not made:
            continue
        made |= {lv.get("name") for lv in lvs if re.fullmatch(r"lvol\d+_pmspare", lv.get("name") or "")}
        lvs.sort(key=lambda lv: lv.get("name") or "")
        for lv in lvs:
            if lv.get("name") in made:
                names.add((vg.get("name"), lv.get("name")))
                uuids.add(lv.get("id"))
    return names, uuids


def fs_thin_lvs(d):
    """{(VG, LV)} of dump D's thin LVs that hold a filesystem the backup
    reads: its dm device has a filesystem content, isn't a raw volume, and
    is a backup source's device (so judge.py fs compares what it holds).
    Such an LV's mapped_bytes is how much its restored filesystem wrote,
    not layout; an LV holding anything else (a raw volume, nothing) keeps
    its mapping compared, which is all that would show it came back empty."""
    st = d.get("storage") or {}
    backed = {s.get("device") for s in st.get("backup_sources") or [] if s.get("device")}
    dms = {}
    for x in st.get("dm_devices") or []:
        dm = x.get("dm") or {}
        c = x.get("content") if isinstance(x.get("content"), dict) else {}
        if dm.get("vg") and dm.get("lv") and c.get("usage") == "filesystem" and c.get("type") \
                and not x.get("raw_volume") and x.get("name") in backed:
            dms[(dm.get("vg"), dm.get("lv"))] = c.get("type")
    return {w for w, (_, seg) in thin_lvs(d).items() if seg.get("type") == "thin" and w in dms}


def fs_thin_pools(d, fs_thins):
    """{(VG, POOL)} of dump D's thin pools whose every thin LV is in
    FS_THINS (fs_thin_lvs), and that have one at least: their used data and
    metadata are only what those filesystems wrote."""
    pools = {}
    for (vg, _), (lv, seg) in thin_lvs(d).items():
        if seg.get("type") == "thin":
            pools.setdefault((vg, seg.get("thin_pool")), []).append((vg, lv.get("name")) in fs_thins)
    return {w for w, ok in pools.items() if ok and all(ok)}


def geli_records(d):
    """GELI providers (docs/design/geli.md) in dump D: (holder, record), the
    flattened paths of the disk or partition that holds one and of its geli
    record: a geli object on it or in its content, or a content of type geli.
    The same reading as ht1-bsd.sh's geli_provs, where the shape may move."""
    out = []
    for i, disk in enumerate((d.get("storage") or {}).get("disks") or []):
        for at, x in [(f"storage.disks.{i}", disk)] + [(f"storage.disks.{i}.partitions.{j}", p) for j, p in enumerate(disk.get("partitions") or [])]:
            c = x.get("content") if isinstance(x.get("content"), dict) else {}
            if isinstance(x.get("geli"), dict):
                out.append((at, at + ".geli"))
            elif isinstance(c.get("geli"), dict):
                out.append((at, at + ".content.geli"))
            elif c.get("type") == "geli":
                out.append((at, at + ".content"))
    return out


def geli_key(k, records):
    """K, a field inside a GELI provider's own probe (its zfs_member, its
    UFS), as the same field of a partition's content, so IGNORED's rules for
    those apply there too; else K."""
    for holder, rec in records:
        m = re.match(re.escape(rec) + r"\.content\.(?:\d+\.)?(.+)$", k)
        if m:
            return f"{holder}.content.{m.group(1)}"
    return k


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


# A path in a snapshot tree: PREFIX.snapshots/.../(root|snapshot)/REST, the
# first root or snapshot component after .snapshots/ the snapshot's own
# root (snapper's .snapshots/N/snapshot, AnduinOS's
# .snapshots/anduinos-btrfs-snapshots-manager/deployments/ID/root), its
# live counterpart PREFIX + REST.
SNAPSHOT_TREE = re.compile(r"^((?:.*/)?)\.snapshots/(?:[^/]+/)*?(?:root|snapshot)/(.+)$")
SNAPSHOT_HOLE_KINDS = ("holes gained", "allocation lost", "holes lost", "holes lost (sub-block)")
FS_NOCOW_FL = 0x00800000


def snapshot_holes_why(kind, p, before, after):
    """judge_fs's excuse for KIND, a sparseness difference of P, a file in
    a snapshot tree (SNAPSHOT_TREE), or None.  Load makes such a snapshot as
    a snapshot of the restored live subvolume, then restores the snapshot's
    own files into it with --exact --clone-from: a file whose bytes already
    match is kept as it is, its extents shared with the live copy (the
    restore punches no holes into it, and on btrfs fills them only in a
    NOCOW file: rustic_core's sparse.rs).  So where the source's two copies
    had the same bytes but not the same holes, the restored snapshot's file
    has the restored live copy's allocation.  Excused only when it is exactly that: the live
    counterpart is in both records, the source's two copies have the same
    size and SHA-256 and differ in holes or blocks, neither snapshot copy is
    NOCOW, and the restored snapshot copy has the restored live copy's
    size, SHA-256, blocks and holes."""
    if kind not in SNAPSHOT_HOLE_KINDS:
        return None
    m = SNAPSHOT_TREE.match(p)
    if not m:
        return None
    live = m.group(1) + m.group(2)
    b, a, bl, al = (r.get(x) or {} for r, x in ((before, p), (after, p), (before, live), (after, live)))
    if not all(e.get("type") == "file" for e in (b, a, bl, al)):
        return None
    if any(not isinstance(e.get("flags"), int) or e.get("flags") & FS_NOCOW_FL for e in (b, a)):
        return None
    if b.get("sha256") is None or (b.get("size"), b.get("sha256")) != (bl.get("size"), bl.get("sha256")):
        return None
    alloc = ("holes", "hole_blocks", "blocks")
    if all(b.get(f) == bl.get(f) for f in alloc):
        return None
    if any(a.get(f) is None or a.get(f) != al.get(f) for f in ("size", "sha256") + alloc) or a.get("sha256") != b.get("sha256"):
        return None
    return ("a snapshot's file with its live copy's bytes but not its holes on the source: load makes the snapshot from the restored "
            "live tree and the restore keeps a file whose bytes match as it is (--clone-from), so it has the restored live copy's "
            "allocation, which it does")


# Snapper's slot of the booted root, the root itself again (openSUSE's /:
# its default subvolume, .snapshots/N/snapshot), and a path in it.
ROOT_ALIAS = re.compile(r"^(\.snapshots/\d+/snapshot)/(.+)$")
ALIAS_SAME = ("type", "size", "sha256", "mtime", "mode", "uid", "gid", "target")


def root_alias_slots(before, after):
    """The snapper slots that are the booted root met again (ROOT_ALIAS):
    each one's every file and symlink, in both records, the same file as
    the live path (ALIAS_SAME; not atime, which the walk's own reads can
    move between its two visits of one inode).  Its directories may differ
    (the mount points hidden under it: /boot/efi, /var), so aren't asked.
    A read-only snapshot of the root is another subvolume: the files the
    boot stage wrote are new in the live tree only, so its copies aren't
    the live ones in the restored record."""
    seen, bad = set(), set()
    for rec in (before, after):
        for p, e in rec.items():
            m = ROOT_ALIAS.match(p)
            if not m or e.get("type") == "dir" or m.group(1) in bad:
                continue
            live = rec.get(m.group(2))
            if live and all(e.get(f) == live.get(f) for f in ALIAS_SAME):
                seen.add(m.group(1))
            else:
                bad.add(m.group(1))
    return seen - bad


def root_alias_live(kind, p, before, after, diff, slots):
    """judge_fs's live counterpart of P when P is a path in one of SLOTS
    (root_alias_slots), the booted root seen again through its snapper
    slot, or None: the live path differs the same way (KIND), and P and it
    are the same in each record (a directory too)."""
    m = ROOT_ALIAS.match(p)
    if not m or m.group(1) not in slots or m.group(2) not in diff.get(kind, ()):
        return None
    live = m.group(2)
    for rec in (before, after):
        a, b = rec.get(p), rec.get(live)
        if not a or not b or any(a.get(f) != b.get(f) for f in ALIAS_SAME):
            return None
    return live


def judge_fs(fsmeta_py, src_path, dst_path, no_atime=False, new_uuids=False, reforms=()):
    import importlib.util
    spec = importlib.util.spec_from_file_location("fsmeta", fsmeta_py)
    fsmeta = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fsmeta)
    before = load_json(src_path)
    after = load_json(dst_path)
    diff = fsmeta.differences(before, after)
    # rEFInd (PikaOS) is kept, not reinstalled: on a source that has it, its
    # ESP files and refind_linux.conf get no boot-stage excuse.
    refind = any(re.search(REFIND_SOURCE, p) for p in before)
    anduin = any(re.search(ANDUINOS_SOURCE, p) for p in before)
    abroot = any(re.search(ABROOT_SOURCE, p) for p in before)
    solus = any(re.search(SOLUS_SOURCE, p) for p in before)
    tu = any(re.search(TU_SOURCE, p) for p in before)
    snapcore = before.get(SNAPCORE_SOURCE, {}).get("type") == "file"
    opensuse = not tu and any(re.search(OPENSUSE_SOURCE, p) for p in before)
    zbm = any(re.search(ZBM_SOURCE, p) for p in before)
    # Slackware's loaders (S6): LILO's own rules, mkinitrd's, ELILO kept.
    lilo = all(before.get(p, {}).get("type") == "file" for p in LILO_SOURCE)
    slack = before.get("etc/slackware-version", {}).get("type") == "file"
    elilo = any(re.search(ELILO_SOURCE, p) for p in before)
    copies = elilo_initrd_copies(before) if elilo else set()
    own_stage = (LILO_STAGE if lilo else []) + (MKINITRD_STAGE if slack else [])
    real, allowed = {}, {}
    if lilo and LILO_COPY in after:
        real.setdefault("left behind by load (lilo's copy of lilo.conf)", []).append(LILO_COPY)
    if elilo:
        for p in elilo_missing(before):
            real.setdefault("not in the source's record (ELILO's files on the ESP)", []).append(p)
        bi = after.get("boot/initrd.gz") or {}
        for p in sorted(copies):
            a = after.get(p) or {}
            if a.get("type") == "file" and (a.get("size"), a.get("sha256")) != (bi.get("size"), bi.get("sha256")):
                real.setdefault("not the restored boot/initrd.gz's bytes (ELILO's initrd on the ESP, a copy of it on the source)", []).append(p)
    # A transactional source's slot at / (the booted one) is in the backup
    # through / only, where @/var and the other subvolumes fstab mounts hide
    # its own directories under them (transactional-update writes into a new
    # snapshot's /var, which @/var then shadows): the source's record reaches
    # them through /.snapshots/N/snapshot, which load leaves out as that
    # slot's alias.  Such a directory comes back an empty mount point.
    hidden = set()
    if tu:
        filled = {k.rsplit("/", 1)[0] for k in after if "/" in k}
        for k in before:
            m = re.match(r"^(\.snapshots/\d+/snapshot/[^/]+)/", k)
            if m and m.group(1) not in filled and after.get(m.group(1), {}).get("type") == "dir" \
                    and before.get(m.group(1).split("/snapshot/", 1)[1], {}).get("type") == "dir" and not m.group(1).endswith("/etc"):
                hidden.add(m.group(1))
    if anduin:
        for p in ANDUINOS_REQUIRED:
            if before.get(p, {}).get("type") != "file":
                real.setdefault("not in the source's record (AnduinOS's MOK-signed loaders)", []).append(p)
    if abroot:
        for p in abroot_missing(before):
            real.setdefault("not in the source's record (ABRoot's boot files)", []).append(p)
    if solus:
        for p in solus_missing(before):
            real.setdefault("not in the source's record (Solus's ESP at /boot)", []).append(p)
    if opensuse:
        for p in OPENSUSE_REQUIRED:
            if before.get(p, {}).get("type") != "file":
                real.setdefault("not in the source's record (openSUSE's shim and signed GRUB)", []).append(p)
    if snapcore:
        for p in snapcore_missing(before):
            real.setdefault("not in the source's record (Ubuntu Core's seed, boot and snaps)", []).append(p)
    def is_kept(q):
        return (refind and re.search(REFIND_KEPT, q)) or (anduin and re.search(ANDUINOS_KEPT, q)) or (abroot and re.search(ABROOT_KEPT, q)) \
            or (solus and re.search(SOLUS_KEPT, q)) or (opensuse and re.search(OPENSUSE_KEPT, q)) or (zbm and re.search(ZBM_KEPT, q)) \
            or (elilo and re.search(ELILO_KEPT, q) and q not in copies)

    def stage_why(kind, q):
        return next((w for r, kinds, w in own_stage + (TU_STAGE if tu else SNAPCORE_STAGE if snapcore else BOOT_STAGE)
                     if kind in kinds and re.search(r, q)), None)
    alias_slots = set() if tu else root_alias_slots(before, after)
    for kind, paths in diff.items():
        for p in paths:
            kept = is_kept(p)
            if lilo and p == LILO_COPY:
                continue   # (counted above)
            why = None if kept else \
                next((w for r, kinds, w in own_stage if kind in kinds and re.search(r, p)), None)
            if not why and p in copies:
                # Load's cp and touch -r of the remade initrd; its bytes are judged above.
                why = "load copies the remade /boot/initrd.gz over the ESP's (as eliloconfig does)" if kind in ("content", "mtime", "atime") else None
            elif not why and not kept:
                why = next((w for r, kinds, w in (TU_STAGE if tu else SNAPCORE_STAGE if snapcore else BOOT_STAGE) if kind in kinds and re.search(r, p)), None)
            if not why and not kept and not tu:
                # The booted root seen again through its snapper slot
                # (openSUSE's / is the subvolume .snapshots/N/snapshot, and
                # the walk of / meets it once more under /.snapshots, where
                # its own /boot is no mount point on Leap 16): the same
                # files, so what the boot stage wrote shows there too, with
                # the live path's difference.  Excused as the live path is,
                # only when it is that alias in both records.
                live = root_alias_live(kind, p, before, after, diff, alias_slots)
                lw = live and not is_kept(live) and stage_why(kind, live)
                if lw:
                    why = f"the booted root again at {p[:-len(live) - 1]}, its snapper slot (the same subvolume as /): {lw}"
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
            if not why and hidden and kind in ("missing", "mtime (dirs)", "atime (dirs)", "selinux", "other-xattr") \
                    and any(p == h or p.startswith(h + "/") for h in hidden):
                why = ("a directory of the booted slot hidden under a subvolume fstab mounts on it (MicroOS's /var: transactional-update "
                       "wrote into the snapshot's own /var, which @/var shadows): the running system, and so its backup, can't reach it")
            if not why and kind == "hard links" and before.get(p, {}).get("type") == "symlink" and not after.get(p, {}).get("links"):
                # rustic-bko (and restic-bko) relink regular files only: an
                # ostree repository's symlink objects, hardlinked into each
                # deployment, come back as separate symlinks, each with its
                # target and metadata (SticTest1's kinoite44 LIMIT).
                why = "a hardlinked symlink restored as separate symlinks: the archivers relink regular files only"
            if not why and kind == "inode flags":
                # btrfs sets FS_NOCOMP_FL itself on a file whose data didn't
                # compress, as it was written: not the restore's to keep.
                af, bf = before.get(p, {}).get("flags"), after.get(p, {}).get("flags")
                if isinstance(af, int) and isinstance(bf, int) and (af ^ bf) & ~0x1400 == 0:
                    why = "btrfs's own no-compress flag, which its compression heuristic sets as data is written"
            if not why:
                why = snapshot_holes_why(kind, p, before, after)
            if not why and reforms:
                why = reformat_why(kind, p, before, after, reforms)
            (allowed.setdefault(why, []) if why else real.setdefault(kind, [])).append(p)
    for kind, paths in sorted(real.items()):
        print(f"DIFF {kind}: {len(paths)}: {', '.join(paths[:5])}{' ...' if len(paths) > 5 else ''}")
    for why, paths in allowed.items():
        print(f"boot stage ({why}): {len(paths)}: {', '.join(paths[:4])}{' ...' if len(paths) > 4 else ''}")
    if refind:
        print(f"rEFInd kept: {sum(1 for p in before if re.search(REFIND_KEPT, p))} paths of refind_linux.conf, EFI/refind and EFI/BOOT held to the source's")
    if anduin:
        held = sorted(p for p in before if re.search(ANDUINOS_KEPT, p))
        print(f"AnduinOS's MOK-signed loaders kept: {len(held)} paths held to the source's bytes: {', '.join(p.removeprefix('boot/efi/EFI/') for p in held)}")
    if abroot:
        held = [p for p in before if re.search(ABROOT_KEPT, p)]
        print(f"ABRoot kept: {len(held)} paths of the master grub.cfg, init's abroot.cfgs, kernels and initramfs, and EFI/vanilla and EFI/BOOT held to the source's")
    if solus:
        held = [p for p in before if re.search(SOLUS_KEPT, p)]
        print(f"Solus's ESP at /boot kept: {len(held)} paths of EFI/com.solus-project, EFI/BOOT, loader/ and solus-enroll-me.cer held to the source's bytes")
    if opensuse:
        held = sorted(p for p in before if re.search(OPENSUSE_KEPT, p))
        print(f"openSUSE's shim and signed GRUB kept: {len(held)} paths of EFI/opensuse and EFI/boot held to the source's bytes: {', '.join(p.removeprefix('boot/efi/EFI/') for p in held)}")
    if zbm:
        held = sorted(p for p in before if re.search(ZBM_SOURCE, p))
        print(f"ZFSBootMenu's images kept: {len(held)} held to the source's bytes: {', '.join(held)}")
    if lilo:
        print(f"LILO: etc/lilo.conf held to the source's bytes, no {LILO_COPY} left behind; lilo's map and MBR backup the boot stage's")
    if elilo:
        held = sorted(p for p in before if re.search(ELILO_KEPT, p) and p not in copies)
        print(f"ELILO kept: {len(held)} paths of EFI/Slackware and EFI/BOOT held to the source's bytes; "
              f"{len(copies)} ESP initrd.gz (copies of /boot/initrd.gz on the source) held to the restored /boot/initrd.gz's")
    if tu:
        held = [p for p in before if re.search(TU_HELD, p)]
        print(f"transactional-update: no boot-stage excuse but load's log and health-check.state: {len(held)} paths of the ESP and every slot's /boot and /usr held to the source's")
    if snapcore:
        held = [p for p in before if re.search(SNAPCORE_HELD, p)]
        print(f"Ubuntu Core: no boot-stage excuse but load's log: {len(held)} paths of ubuntu-seed, ubuntu-boot, the .snaps, modeenv and state.json held to the source's")
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


# Proxmox's proxmox-boot-tool ESPs (docs/design/proxmox.md): no backup source,
# rebuilt by load's `proxmox-boot-tool init` and `refresh`, so judged by
# content, ESP by ESP of the same volume ID.  What each may show (any other
# path must be on both with the same type and bytes; times, which every
# rebuilt file has new, aren't compared):
ESP_REBUILT = [
    (r"^EFI/proxmox/[^/]+/initrd\.img-[^/]+$", {"content"}, "update-initramfs rebuilt it in the boot stage, and refresh copied it"),
    (r"^loader/random-seed$", {"content", "missing", "extra"}, "bootctl install writes a new random seed"),
    (r"^loader/entries\.srel$", {"content", "missing", "extra"}, "bootctl install's entry-layout stamp"),
]


# TrueNAS's ESPs (docs/design/truenas.md): mounted by nothing, so judged by
# content too, under their volume IDs (load's mkfs.fat -i keeps them);
# load's boot stage runs grub-install --target=x86_64-efi on each and copies
# EFI/debian/grubx64.efi to EFI/boot/bootx64.efi, so their loaders may
# differ in bytes, but not the set of paths, and in each restored ESP the
# fallback must be grubx64.efi.
ESP_REBUILT_TRUENAS = [
    (r"^EFI/(debian/[^/]+|boot/bootx64\.efi)$", {"content"}, "grub-install rebuilt it in the boot stage (EFI/boot/bootx64.efi: its copy of grubx64.efi)"),
]
TRUENAS_FALLBACK = ("EFI/boot/bootx64.efi", "EFI/debian/grubx64.efi")


def esp_files(rec):
    """A record's paths (fsmeta.py's, of one ESP mounted alone) by type and,
    for files, their bytes; its root '.' left out."""
    return {p: (e.get("type"), e.get("sha256") if e.get("type") == "file" else None) for p, e in rec.items() if p != "."}


def esp_compare(a, b, rules):
    """{kind: [paths]} that differ between esp_files A and B, and
    {why: [paths]} of those RULES allow."""
    real, allowed = {}, {}
    for p in sorted(set(a) | set(b)):
        if p not in b:
            kind = "missing"
        elif p not in a:
            kind = "extra"
        elif a[p][0] != b[p][0]:
            kind = "type"
        elif a[p][1] != b[p][1]:
            kind = "content"
        else:
            continue
        why = next((w for r, kinds, w in rules if kind in kinds and re.search(r, p)), None)
        (allowed.setdefault(why, []) if why else real.setdefault(kind, [])).append(p)
    return real, allowed


def judge_esp(esp_dir):
    """`judge.py esp DIR`: ht1.sh's records of a proxmox-boot-tool source's
    ESPs (DIR/src-uuids and dst-uuids, the two /etc/kernel/proxmox-boot-uuids;
    DIR/src-ID.json and dst-ID.json, fsmeta.py's record of each ESP): the
    UUID files byte-identical, every source ESP restored under its ID with
    the same files, the kernels byte for byte (ESP_REBUILT names what may
    differ), and with more than one, the restored ESPs equal to each other
    but for their random seeds.  With DIR/src-kind "truenas" (TrueNAS's
    ESPs, the uuids files ht1.sh's lists of their volume IDs):
    ESP_REBUILT_TRUENAS's rules, each restored ESP's EFI/boot/bootx64.efi
    equal to its EFI/debian/grubx64.efi, and the restored ESPs equal to
    each other."""
    import os
    out, n = [], 0

    def read(name):
        try:
            with open(os.path.join(esp_dir, name), "rb") as f:
                return f.read()
        except OSError:
            return None
    su, du = read("src-uuids"), read("dst-uuids")
    truenas = (read("src-kind") or b"").strip() == b"truenas"
    if su is None:
        print(f"DIFF no src-uuids in {esp_dir}: nothing to judge")
        sys.exit(1)
    if su != du:
        out.append(f"DIFF {'the ESPs (volume IDs)' if truenas else 'etc/kernel/proxmox-boot-uuids'}: source {su!r}, restored {du!r}")
        n += 1
    ids = [l.strip() for l in su.decode(errors="replace").splitlines() if l.strip()]
    restored = {}
    for i in ids:
        try:
            s, d = esp_files(load_json(os.path.join(esp_dir, f"src-{i}.json"))), esp_files(load_json(os.path.join(esp_dir, f"dst-{i}.json")))
        except OSError as e:
            out.append(f"DIFF ESP {i}: no record ({e.filename})")
            n += 1
            continue
        restored[i] = d
        real, allowed = esp_compare(s, d, ESP_REBUILT_TRUENAS if truenas else ESP_REBUILT)
        for kind, paths in real.items():
            out.append(f"DIFF ESP {i}: {kind}: {len(paths)}: {', '.join(paths)}")
            n += len(paths)
        if truenas:
            fb, grub = (d.get(p) for p in TRUENAS_FALLBACK)
            if not fb or not grub or fb[0] != "file" or fb != grub:
                out.append(f"DIFF ESP {i}: {TRUENAS_FALLBACK[0]} isn't {TRUENAS_FALLBACK[1]}: {fb} and {grub}")
                n += 1
            same = sum(1 for p in d if p in s and s[p] == d[p] and d[p][0] == "file")
            out.append(f"ESP {i}: {len(d)} paths, {len(s)} on the source's; {same} of its {sum(1 for p in d if d[p][0] == 'file')} files byte for byte, "
                       f"{TRUENAS_FALLBACK[0]} its {TRUENAS_FALLBACK[1]}" + "".join(f"; {why}: {len(paths)}" for why, paths in allowed.items()))
            continue
        kernels = sorted(p for p in d if re.search(r"^EFI/proxmox/[^/]+/vmlinuz-[^/]+$", p) and s.get(p) == d[p])
        entries = sorted(p for p in d if re.search(r"^loader/entries/proxmox-[^/]+\.conf$", p) and s.get(p) == d[p])
        out.append(f"ESP {i}: {len(d)} paths, {len(s)} on the source's; {len(kernels)} kernels and {len(entries)} proxmox entries byte for byte"
                   + "".join(f"; {why}: {len(paths)}" for why, paths in allowed.items()))
    if len(restored) > 1:
        first, *rest = restored
        m = 0
        for i in rest:
            real, _ = esp_compare(restored[first], restored[i], [] if truenas else ESP_REBUILT[1:2])
            for kind, paths in real.items():
                out.append(f"DIFF ESPs {first} and {i} (restored): {kind}: {len(paths)}: {', '.join(paths)}")
                m += len(paths)
        n += m
        if m == 0:
            out.append(f"the {len(restored)} restored ESPs are equal" + ("" if truenas else " but for their random seeds"))
    print("\n".join(out))
    print(f"{n} differences in {len(ids)} ESPs")
    sys.exit(1 if n else 0)


# --reformat MOUNT=TYPE (docs/design/reformat.md): load made the filesystem
# mounted at MOUNT anew as TYPE, with the old one's UUID and label, and its
# boot stage rewrote what named the old type.  The dump form holds the
# restored dump to what that change predicts from the source's (as load's
# reformat::work_out and rewrite.rs work it out), and the rest to the
# source's as ever: reformat_expect edits the source's dump into that.
REFORMAT_TYPES = ("ext4", "xfs", "btrfs")
# reformat.rs's GENERIC_OPTIONS: what an fstab entry and rootflags= keep.
REFORMAT_GENERIC = {"defaults", "ro", "rw", "noatime", "relatime", "strictatime", "lazytime", "nolazytime", "nodiratime", "diratime",
                    "nodev", "dev", "nosuid", "suid", "noexec", "exec", "nofail", "noauto", "auto", "sync"}


def reformat_args(args):
    """[(MOUNT, TYPE)] from ARGS' --reformat MOUNT=TYPE pairs; exits on a bad one."""
    out = []
    for i, x in enumerate(args):
        if x != "--reformat":
            continue
        v = args[i + 1] if i + 1 < len(args) else ""
        m, _, t = v.partition("=")
        if not m.startswith("/") or t not in REFORMAT_TYPES:
            sys.exit(f"judge.py: --reformat MOUNT=TYPE (TYPE {', '.join(REFORMAT_TYPES)}), not {v!r}")
        out.append((m.rstrip("/") or "/", t))
    return out


def generic_options(o):
    keep = [x for x in str(o or "").split(",") if x and (x in REFORMAT_GENERIC or x.startswith("x-") or x.startswith("comment="))]
    return ",".join(keep) or "defaults"


def reformat_cmdline(line, frm, to):
    """rewrite.rs's cmdline(), line by line: rootflags= cut to its generic
    options (gone when none are left), rootfstype=FROM set to TO."""
    def word(w):
        if w.startswith("rootflags="):
            keep = [x for x in w[len("rootflags="):].split(",") if x and x != "defaults" and generic_options(x) != "defaults"]
            return f"rootflags={','.join(keep)}" if keep else None
        return f"rootfstype={to}" if w == f"rootfstype={frm}" else w
    out = []
    for ln in line.split("\n"):
        words = ln.split()
        new = [x for x in map(word, words) if x is not None]
        if new == words:
            out.append(ln)
            continue
        lead, trail = ln[: len(ln) - len(ln.lstrip())], ln[len(ln.rstrip()):]
        out.append(lead + " ".join(new) + trail)
    return "\n".join(out)


def _dicts(v):
    if isinstance(v, dict):
        yield v
        for x in v.values():
            yield from _dicts(x)
    elif isinstance(v, list):
        for x in v:
            yield from _dicts(x)


def _under(p, m):
    return m == "/" or p == m or p.startswith(m + "/")


def reformat_expect(src, dst, reforms):
    """SRC's dump edited into what each --reformat (MOUNT, TYPE) of REFORMS
    predicts, and the fields it can't predict taken out of both: problems
    (each a DIFF) and notes."""
    bad, notes = [], []
    ss, ds = src.setdefault("storage", {}), dst.setdefault("storage", {})
    for mount, to in reforms:
        entry = next((e for e in ss.get("fstab") or [] if e.get("file") == mount), None)
        if not entry or not entry.get("resolves_to"):
            bad.append(f"--reformat {mount}={to}: the source's fstab doesn't mount {mount}")
            continue
        dev, frm = entry["resolves_to"], entry.get("type")
        # The filesystem's record: the dump's content of the device fstab names.
        holder = next((d for d in _dicts(ss) if d.get("name") == dev and isinstance(d.get("content"), dict)), None)
        uuid = (holder or {}).get("content", {}).get("uuid")
        if not uuid:
            bad.append(f"--reformat {mount}={to}: no filesystem record of {dev} in the source's dump")
            continue
        # Every other fstab mount of the filesystem is folded into MOUNT's.
        folded = [e.get("file") for e in ss.get("fstab") or [] if e.get("resolves_to") == dev and e.get("type") != "swap" and e.get("file") != mount]
        for f in folded:
            if not _under(f, mount):
                bad.append(f"--reformat {mount}={to}: {dev} is mounted at {f} too, outside {mount}")
        sides = {"source": (ss, src), "restored": (ds, dst)}
        found = {}
        for who, (st, _) in sides.items():
            for d in _dicts(st):
                c = d.get("content")
                if isinstance(c, dict) and c.get("uuid") == uuid and c.get("type") in REFORMAT_TYPES + ("ext2", "ext3"):
                    found.setdefault(who, []).append(d)
        rec = found.get("restored") or []
        if not rec:
            bad.append(f"--reformat {mount}={to}: the restored dump has no filesystem with the source's UUID {uuid}")
        for d in rec:
            if d["content"].get("type") != to:
                bad.append(f"--reformat {mount}={to}: {d.get('name')}'s filesystem {uuid} is {d['content'].get('type')}, not {to}")
        # Its record is the new type's: only what load keeps is compared
        # (the type, UUID, usage and label; XFS's label cut to 12 bytes).
        for who, ds_ in found.items():
            for d in ds_:
                c = d["content"]
                label = c.get("label")
                if who == "source":
                    c = dict(c, type=to)
                    if to == "xfs" and isinstance(label, str) and len(label.encode()) > 12:
                        label = label.encode()[:12].decode(errors="ignore")
                d["content"] = {"type": c.get("type"), "uuid": uuid, "usage": c.get("usage"), "label": label}
                if who == "source" and isinstance(d.get("mounted_at"), list):
                    d["mounted_at"] = [m for m in d["mounted_at"] if m not in folded]
        notes.append(f"--reformat {mount}={to}: {dev} ({frm}, {uuid}) compared by type, UUID and label only; "
                     f"folded into {mount}: {', '.join(folded) or 'none'}")
        # btrfs's own record: the old one's is gone; a new btrfs is one with no subvolumes.
        for st in (ss, ds):
            if isinstance(st.get("btrfs"), list):
                st["btrfs"] = [f for f in st["btrfs"] if f.get("uuid") != uuid]
        # fstab: MOUNT's entry with the new type, generic options and pass; the folded ones gone.
        if isinstance(ss.get("fstab"), list):
            ss["fstab"] = [e for e in ss["fstab"] if not (e.get("resolves_to") == dev and e.get("file") in folded)]
            entry.update(type=to, options=generic_options(entry.get("options")),
                         passno=(1 if mount == "/" else 2) if to == "ext4" else 0)
        # The running mounts and what a backup of the restored system would take.
        for key, field in (("mounts", "target"), ("backup_sources", "path")):
            if isinstance(ss.get(key), list):
                ss[key] = [m for m in ss[key] if not (m.get("device") == dev and m.get(field) in folded)]
            for st in (ss, ds):
                for m in st.get(key) or []:
                    if m.get("device") != dev:
                        continue
                    m["fstype"] = to if st is ss else m.get("fstype")
                    if key == "mounts":
                        m.pop("super_options", None)
                        if st is ss:
                            m["root"] = "/"
                    elif st is ss:
                        m["subvol"] = None
        if mount != "/":
            continue
        # The root's kernel command lines, wherever the dump found one.
        def walk(v):
            if isinstance(v, str):
                return reformat_cmdline(v, frm, to) if ("rootflags=" in v or "rootfstype=" in v) else v
            if isinstance(v, list):
                return [x for x in (walk(x) for x in v) if x is not None]
            if isinstance(v, dict):
                return {k: walk(x) for k, x in v.items()}
            return v
        boot = src.get("boot") or {}
        for d in _dicts(boot):
            if isinstance(d.get("words"), list):
                # One word each: a word rewrite.rs drops comes out empty.
                d["words"] = [w for w in (reformat_cmdline(x, frm, to) if isinstance(x, str) else x for x in d["words"])
                              if not (isinstance(w, str) and not w.strip())]
            st = d.get("storage")
            if isinstance(st, dict) and "rootflags" in st:
                v = reformat_cmdline(f"rootflags={st['rootflags']}", frm, to)
                if v:
                    st["rootflags"] = v[len("rootflags="):]
                else:
                    st.pop("rootflags")
            if isinstance(st, dict) and st.get("rootfstype") == frm:
                st["rootfstype"] = to
        src["boot"] = walk(boot)
        # GRUB: snapper's snapshot entries (openSUSE's 80_suse_btrfs_snapshot,
        # grub-btrfs) are made for a btrfs root only.  The dump's default is
        # its first entry's, which on the source was snapper's help entry.
        g = ((src.get("boot") or {}).get("grub") or {}).get("grub_cfg")
        h = ((dst.get("boot") or {}).get("grub") or {}).get("grub_cfg")
        if frm == "btrfs" and to != "btrfs" and isinstance(g, dict) and isinstance(g.get("entries"), list):
            snap = [e for e in g["entries"] if re.search(r"(?i)snapshot", str(e))]
            if snap:
                first = g["entries"][0] in snap
                g["entries"] = [e for e in g["entries"] if e not in snap]
                notes.append(f"--reformat {mount}={to}: GRUB's btrfs snapshot entries left out: {'; '.join(map(str, snap))}")
                if first and g.get("default_linux") is None and isinstance(h, dict):
                    g["default_linux"], g["default_initrd"] = h.get("default_linux"), h.get("default_initrd")
                    notes.append(f"--reformat {mount}={to}: the source's first GRUB entry was a snapshot entry, with no kernel: the restored default isn't compared")
    return bad, notes


# The fs form's --reformat: what load's rewrite of the restored system
# writes (rewrite.rs's files), whatever the type.
REFORMAT_FILES = (r"^etc/(fstab|kernel/cmdline|cmdline\.d/[^/]+|default/grub|default/limine|mkinitcpio\.conf|mkinitcpio\.conf\.d/[^/]+|systemd/system/[^/]+\.mount)$",
                  r"^(boot/|efi/|boot/efi/)?(loader/entries/[^/]+\.conf|limine\.conf|EFI/[^/]+/limine\.conf|refind_linux\.conf|EFI/[^/]+/refind\.conf)$")
# btrfs's inode flags no other type keeps: no-copy-on-write (C), compress (c), no-compress.
BTRFS_ONLY_FLAGS = 0x00800000 | 0x4 | 0x400
# A snapper snapshot (and the root's own alias among them): left out of a
# restore onto another type, its .snapshots/N directory kept, empty.
LEFT_OUT_SNAPSHOT = re.compile(r"(^|/)\.snapshots/\d+/snapshot(/|$)")
# XFS keeps each POSIX ACL under its own trusted.SGI_ACL_* name as well.
XFS_ACL_COPY = {"trusted.SGI_ACL_FILE": "system.posix_acl_access", "trusted.SGI_ACL_DEFAULT": "system.posix_acl_default"}


def reformat_why(kind, p, before, after, reforms):
    """Why the difference KIND at path P is a --reformat's, or None."""
    for mount, to in reforms:
        rel = mount.strip("/")
        inside = not rel or p == rel or p.startswith(rel + "/")
        if kind in _WRITTEN and any(re.search(r, p) for r in REFORMAT_FILES):
            return "--reformat: load's boot stage rewrites what names the old type (rewrite.rs)"
        if not inside:
            continue
        if kind == "extra" and to == "ext4" and p == (rel + "/" if rel else "") + "lost+found":
            return "--reformat onto ext4: mke2fs's lost+found"
        if kind == "missing" and LEFT_OUT_SNAPSHOT.search(p):
            return "--reformat: a btrfs snapshot (snapper's, or the root's alias at .snapshots/N/snapshot) isn't restored onto another type (docs/design/reformat.md)"
        if kind == "hard links":
            def kept(links):
                left = [x for x in links or [] if not LEFT_OUT_SNAPSHOT.search(x)]
                return left if len(left) > 1 else None
            if before.get(p, {}).get("links") and kept(before[p]["links"]) == (after.get(p, {}).get("links") or None):
                return "--reformat: a hard-link group without its left-out snapshots' paths"
        if kind == "inode flags":
            af, bf = before.get(p, {}).get("flags"), after.get(p, {}).get("flags")
            # (Less ext4's hashed-directory index, which fsmeta.py doesn't compare.)
            if isinstance(af, int) and isinstance(bf, int) and not (bf & ~af & ~0x1000) and not ((af ^ bf) & ~(BTRFS_ONLY_FLAGS | 0x1000)):
                return "--reformat off btrfs: its no-copy-on-write and compression flags, which no other type keeps"
        if kind == "trusted-xattr" and to == "xfs":
            bx, ax = before.get(p, {}).get("xattrs") or {}, after.get(p, {}).get("xattrs") or {}
            names = {n for n in set(bx) | set(ax) if n.startswith("trusted.") and bx.get(n) != ax.get(n)}
            if names and all(n in XFS_ACL_COPY and n not in bx and XFS_ACL_COPY[n] in ax for n in names):
                return "--reformat onto XFS: XFS lists each POSIX ACL again as trusted.SGI_ACL_* (the system.posix_acl_* are compared)"
    return None


# TrueNAS's boot pool (docs/design/truenas.md): what judge.py bes compares.
TRUENAS_POOL = "boot-pool"


def judge_bes(src_path, dst_path):
    """`judge.py bes SRC DST`: a TrueNAS source's boot-pool in the two
    dumps: the same datasets (every boot environment, its children, grub,
    .system), each with the source's mountpoint, canmount and local and
    user properties (readonly, truenas:kernel_version, zectl:keep, ...),
    and the source's bootfs; a clone's origin (and so whether it is one)
    and the snapshots may differ (the spec's Decision 4)."""
    src, dst = load_json(src_path), load_json(dst_path)

    def pool(d):
        t = ((d.get("storage") or {}).get("zfs") or {}).get("tools") or {}
        bootfs = next((((p.get("properties") or {}).get("bootfs")) for p in t.get("imported_pools") or [] if p.get("name") == TRUENAS_POOL), None)
        ds = {x.get("name"): x for x in t.get("datasets") or [] if str(x.get("name", "")).split("/")[0] == TRUENAS_POOL}
        return bootfs, ds
    sb, sd = pool(src)
    db, dd = pool(dst)
    bad = []
    if not sd:
        print(f"DIFF the source's dump has no {TRUENAS_POOL} datasets")
        sys.exit(1)
    if sb != db:
        bad.append(f"bootfs: {sb} -> {db}")
    for name in sorted(set(sd) - set(dd)):
        bad.append(f"{name}: missing")
    for name in sorted(set(dd) - set(sd)):
        bad.append(f"{name}: extra")
    for name in sorted(set(sd) & set(dd)):
        a, b = sd[name], dd[name]
        for k in ("type", "mountpoint", "canmount"):
            if a.get(k) != b.get(k):
                bad.append(f"{name}: {k} {a.get(k)} -> {b.get(k)}")
        pa, pb = a.get("properties") or {}, b.get("properties") or {}
        for k in sorted(set(pa) | set(pb)):
            if pa.get(k) != pb.get(k):
                bad.append(f"{name}: property {k} {json.dumps(pa.get(k))} -> {json.dumps(pb.get(k))}")
    for x in bad:
        print(f"DIFF {x}")
    bes = sorted(n.split("/")[2] for n in sd if n.count("/") == 2 and n.startswith(TRUENAS_POOL + "/ROOT/"))
    clones = sorted(n for n, x in sd.items() if x.get("origin"))
    ro = sorted(n for n, x in sd.items() if ((x.get("properties") or {}).get("readonly") or {}).get("value") == "on")
    print(f"{len(bes)} boot environments ({', '.join(bes)}), bootfs {sb}; {len(sd)} datasets of {TRUENAS_POOL}, {len(ro)} readonly=on; "
          f"{len(clones)} clones on the source, {sum(1 for n in clones if n in dd and dd[n].get('origin'))} restored as clones")
    print(f"{len(bad)} differences")
    sys.exit(1 if bad else 0)


# ZFSBootMenu (docs/design/zfsbootmenu.md, "HoltTest1 1V"): what judge.py zbm
# compares, besides the dump form's every field and the fs form's every path.
ZBM_PREFIX = "org.zfsbootmenu:"


def zbm_kver(name):
    """A sort key for a kernel's version (vmlinuz-6.18.63_1: its numbers)."""
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", name)]


def zbm_newest(be):
    """The newest kernel version in a dump's BE record (its /boot's
    vmlinuz-VERSION), the one ZFSBootMenu boots; None without one."""
    ks = sorted((k.removeprefix("vmlinuz-") for k in be.get("kernels") or [] if k.startswith("vmlinuz-")), key=zbm_kver)
    return ks[-1] if ks else None


def zbm_be_path(d, be):
    """Where a dump's backup sources have an inactive BE (its record's
    path, without the leading /): /run/bko-be/NAME unless they say else."""
    for s in (d.get("storage") or {}).get("backup_sources") or []:
        if s.get("source") == be.get("dataset") and not s.get("from"):
            return str(s.get("path") or "").lstrip("/")
    return f"run/bko-be/{be.get('name')}"


def zbm_props(d):
    """{(dataset, property): (value, source)} for every org.zfsbootmenu:*
    property, as set locally on the dataset or inherited from the nearest
    ancestor that sets it ("inherited from DATASET"), over the dump's
    datasets (it records the local ones)."""
    ds = {x.get("name"): x for x in ((((d.get("storage") or {}).get("zfs") or {}).get("tools") or {}).get("datasets") or [])}
    local = {(n, k): v.get("value") for n, x in ds.items() for k, v in (x.get("properties") or {}).items()
             if k.startswith(ZBM_PREFIX) and isinstance(v, dict) and v.get("source") == "local"}
    keys = sorted({k for _, k in local})
    out = {}
    for n in ds:
        for k in keys:
            a = n
            while a and (a, k) not in local:
                a = a.rpartition("/")[0]
            if a:
                out[(n, k)] = (local[(a, k)], "local" if a == n else f"inherited from {a}")
    return out


def judge_zbm(src_path, dst_path, smeta_path, dmeta_path):
    """`judge.py zbm SRC DST SRC-META DST-META`: a ZFSBootMenu source's
    restore, from its dump and the restored one's (each of a boot through
    the source's NVRAM entries) and fsmeta.py's records of the source and
    the restored disk (vm-mount mounts each inactive BE at run/bko-be/NAME,
    where dump's backup sources have it):

    - the restored dump names the loader zfsbootmenu, and has each of the
      source's images (ESP path, size, SHA-256), which both records hold
      with those bytes;
    - every BE of the source's boot.zfsbootmenu.bes, with the same
      dataset, bootfs, mounted, command line (and where it comes from),
      kernels and initramfs files, and no other; a clone's origin may be
      gone (Decision 3a);
    - the booted BE's kernels byte for byte (its initramfs files are
      rebuilt by the boot stage, but must be there); each inactive BE's
      kernels and initramfs files in both records under its path, byte for
      byte (the boot stage rebuilds nothing there, Decision 6);
    - each pool's bootfs and compatibility, and every org.zfsbootmenu:*
      property (keysource too) at the same dataset with the same value and
      source (local, or inherited from the same dataset);
    - each encryption root's encryption, keyformat and keylocation, and
      every dataset's encryptionroot."""
    src, dst = load_json(src_path), load_json(dst_path)
    smeta, dmeta = load_json(smeta_path), load_json(dmeta_path)
    sz, dz = (src.get("boot") or {}).get("zfsbootmenu"), (dst.get("boot") or {}).get("zfsbootmenu")
    if not sz:
        print("DIFF the source's dump has no boot.zfsbootmenu: nothing to judge")
        sys.exit(1)
    bad, out = [], []
    if not dz:
        print("DIFF the restored dump has no boot.zfsbootmenu")
        sys.exit(1)
    loader = ((dst.get("boot") or {}).get("loader") or {}).get("name")
    if loader != "zfsbootmenu":
        bad.append(f"the restored dump's loader is {loader!r}, not zfsbootmenu")

    def rec(meta, p):
        e = meta.get(p) or {}
        return e.get("sha256") if e.get("type") == "file" else None

    # The images.
    dimg = {(i.get("esp_mount"), i.get("path")): i for i in dz.get("images") or []}
    for i in sz.get("images") or []:
        at = (i.get("esp_mount"), i.get("path"))
        j = dimg.pop(at, None)
        name = f"{str(at[0] or '').rstrip('/')}/{at[1]}"
        if not j:
            bad.append(f"image {name}: not in the restored dump")
            continue
        for k in ("size", "sha256", "cmdline", "kernel"):
            if i.get(k) != j.get(k):
                bad.append(f"image {name}: {k} {i.get(k)!r} -> {j.get(k)!r}")
        p = name.lstrip("/")
        hs, hd = rec(smeta, p), rec(dmeta, p)
        if hs != i.get("sha256") or hd != i.get("sha256"):
            bad.append(f"image {name}: the records' bytes: source {hs}, restored {hd}, the dump's {i.get('sha256')}")
    for at in dimg:
        bad.append(f"image {str(at[0] or '').rstrip('/')}/{at[1]}: only in the restored dump")
    out.append(f"{len(sz.get('images') or [])} images byte for byte (records and dumps): "
               + ", ".join(sorted(str(i.get("path")) for i in sz.get("images") or [])))

    # The boot environments.
    dbe = {b.get("dataset"): b for b in dz.get("bes") or []}
    for b in sz.get("bes") or []:
        ds = b.get("dataset")
        c = dbe.pop(ds, None)
        if not c:
            bad.append(f"BE {ds}: not in the restored dump")
            continue
        for k in ("name", "pool", "bootfs", "mounted", "commandline", "commandline_from", "locked", "read", "kernels", "initramfs"):
            if b.get(k) != c.get(k):
                bad.append(f"BE {ds}: {k} {json.dumps(b.get(k))} -> {json.dumps(c.get(k))}")
        if b.get("origin") != c.get("origin") and c.get("origin") is not None:
            bad.append(f"BE {ds}: origin {b.get('origin')} -> {c.get('origin')}")
        if b.get("mounted"):
            base, what = "boot", "booted"
        else:
            root = zbm_be_path(src, b)
            base, what = root + "/boot", "inactive"
            for who, meta in (("source", smeta), ("restored", dmeta)):
                if not any(p.startswith(root + "/") for p in meta):
                    bad.append(f"BE {ds}: the {who} record has nothing under {root} (vm-mount didn't mount it there)")
        held, rebuilt = [], []
        for f in (b.get("kernels") or []) + (b.get("initramfs") or []):
            p = f"{base}/{f}"
            hs, hd = rec(smeta, p), rec(dmeta, p)
            if hs is None or hd is None:
                bad.append(f"BE {ds} ({what}): {p}: not a file in the {'source' if hs is None else 'restored'} record")
            elif what == "booted" and f in (b.get("initramfs") or []):
                rebuilt.append(f)
            elif hs != hd:
                bad.append(f"BE {ds} ({what}): {p}: its bytes differ ({'the boot stage rebuilds nothing in an inactive BE' if what == 'inactive' else 'a kernel'})")
            else:
                held.append(f)
        out.append(f"BE {ds} ({what}{', bootfs' if b.get('bootfs') else ''}; origin {b.get('origin') or '-'} -> {c.get('origin') or '-'}): "
                   f"newest kernel {zbm_newest(b)}; {len(held)} byte for byte under {base}" + (f", {len(rebuilt)} initramfs rebuilt" if rebuilt else ""))
    for ds in dbe:
        bad.append(f"BE {ds}: only in the restored dump")

    # The pools and the properties.
    def pools(d):
        t = (((d.get("storage") or {}).get("zfs") or {}).get("tools") or {})
        return {p.get("name"): p.get("properties") or {} for p in t.get("imported_pools") or []}, \
               {x.get("name"): x for x in t.get("datasets") or []}
    sp, sds = pools(src)
    dp, dds = pools(dst)
    for p in sz.get("pools") or []:
        n = p.get("name")
        for k in ("bootfs", "compatibility"):
            a, b2 = (sp.get(n) or {}).get(k), (dp.get(n) or {}).get(k)
            if a != b2:
                bad.append(f"pool {n}: {k} {a} -> {b2}")
        if (sp.get(n) or {}).get("bootfs") != p.get("bootfs"):
            bad.append(f"pool {n}: bootfs {(sp.get(n) or {}).get('bootfs')}, boot.zfsbootmenu's {p.get('bootfs')}")
        out.append(f"pool {n}: bootfs {(dp.get(n) or {}).get('bootfs')}, compatibility {(dp.get(n) or {}).get('compatibility')}")
    spr, dpr = zbm_props(src), zbm_props(dst)
    for k in sorted(set(spr) | set(dpr)):
        if spr.get(k) != dpr.get(k):
            bad.append(f"{k[0]}: {k[1]} {spr.get(k)} -> {dpr.get(k)}")
    out.append(f"{len(spr)} {ZBM_PREFIX}* settings (local and inherited) as the source's: "
               + "; ".join(f"{n} {k.removeprefix(ZBM_PREFIX)}={v} ({w})" for (n, k), (v, w) in sorted(spr.items()) if w == "local"))
    roots = sorted(n for n, x in sds.items() if x.get("encryptionroot") == n)
    for n in sorted(set(sds) & set(dds)):
        if sds[n].get("encryptionroot") != dds[n].get("encryptionroot"):
            bad.append(f"{n}: encryptionroot {sds[n].get('encryptionroot')} -> {dds[n].get('encryptionroot')}")
    for n in roots:
        for k in ("encryption", "keyformat", "keylocation"):
            a, b2 = sds[n].get(k), (dds.get(n) or {}).get(k)
            if a != b2:
                bad.append(f"{n}: {k} {a} -> {b2}")
        out.append(f"encryption root {n}: {sds[n].get('encryption')}, {sds[n].get('keyformat')}, keylocation {(dds.get(n) or {}).get('keylocation')}")
    for x in out:
        print(x)
    for x in bad:
        print(f"DIFF {x}")
    print(f"{len(bad)} differences; {len(sz.get('bes') or [])} BEs, {len(sz.get('images') or [])} images")
    sys.exit(1 if bad else 0)


# Qubes OS (docs/design/qubes.md, "Tests": HoltTest1 1Obscure, Q7).  The
# dump fields are bko-holt's Q2-Q4 (thin snapshots, raw volumes, boot.xen),
# by the names that work gave (2026-11-15, before it was built): a thin LV's
# segments[0].origin, ACTIVATION_SKIP in an LV's flags, a thin pool's
# data_used_bytes and metadata_used_bytes, a thin LV's mapped_bytes, a
# dm device's raw_volume, storage.qubes, boot.xen.  If Q2-Q4 change a name,
# change it here too.
QUBES_POOL_PCT = 5      # a pool's used data, restored, within this much of the source's (sharing kept)


def thin_lvs(d):
    """{(VG, LV): (lv, its first segment)} of the dump's LVs."""
    out = {}
    for vg in ((d.get("storage") or {}).get("lvm") or {}).get("volume_groups") or []:
        for lv in vg.get("logical_volumes") or []:
            out[(vg.get("name"), lv.get("name"))] = (lv, ((lv.get("segments") or [{}])[0]) or {})
    return out


def thin_snapshot_pools(d):
    """{(VG, POOL)} of the thin pools that hold a thin snapshot (an LV with an origin)."""
    return {(vg, seg.get("thin_pool")) for (vg, _), (_, seg) in thin_lvs(d).items() if seg.get("type") == "thin" and seg.get("origin")}


def qubes_thin_why(k, va, vb, a, snap_pools):
    """main's excuse for K, a field of an LV in a VG with thin snapshots
    (Qubes's vm-pool), or None.  Load makes a snapshot with lvcreate -s after
    its origin's content is restored (Q2), so in such a pool the thin device
    ids and the transaction ids are load's order; a pool's used data and
    metadata may move a little (judge.py qubes holds them within
    QUBES_POOL_PCT), and a thin LV's own mapping more (a zero run the source
    had provisioned stays unprovisioned)."""
    m = re.match(r"^(storage\.lvm\.volume_groups\.\d+)\.logical_volumes\.(\d+)\.(.+)$", k)
    if not m or not snap_pools:
        return None
    vg, lvk = a.get(m.group(1) + ".name"), f"{m.group(1)}.logical_volumes.{m.group(2)}"
    seg_type, pool = a.get(lvk + ".segments.0.type"), a.get(lvk + ".segments.0.thin_pool")
    field = m.group(3)
    if seg_type == "thin-pool" and (vg, a.get(lvk + ".name")) in snap_pools:
        if re.fullmatch(r"(segments\.\d+\.)?transaction_id", field):
            return "a thin pool with snapshots counts load's own lvcreates (Qubes: snapshots made after their origins' restore)"
        if re.fullmatch(r"(segments\.\d+\.)?(data|metadata)_used_bytes", field) and isinstance(va, int) and isinstance(vb, int):
            return f"a thin pool's used data and metadata, within {QUBES_POOL_PCT} % of the source's or not (judge.py qubes holds them)"
    if seg_type == "thin" and (vg, pool) in snap_pools:
        if re.fullmatch(r"segments\.\d+\.(device_id|transaction_id)", field):
            return "a thin LV's device id in a pool with snapshots is load's order of lvcreates (Qubes: snapshots made after their origins' restore)"
        if re.fullmatch(r"(segments\.\d+\.)?mapped_bytes", field):
            return "a thin LV's mapped blocks: a zero run the source had provisioned stays unprovisioned (the pool's total is held by judge.py qubes)"
    return None


def read_sums(path):
    """{VG/LV: sha256} of ht1-offline.sh's raw-volume list ("VG/LV SHA256" lines)."""
    out = {}
    try:
        with open(path) as f:
            for line in f:
                w = line.split()
                if len(w) == 2:
                    out[w[0]] = w[1]
    except OSError:
        pass
    return out


def judge_qubes(src_path, dst_path, sraw_path, draw_path):
    """A Qubes OS source's LVM thin layout and raw volumes, against the
    restored disk's (docs/design/qubes.md, Tests): each thin snapshot (an LV
    with an origin) has a new LV UUID (made by lvcreate -s, not vgcfgrestore)
    and its origin by name; every LV's activation-skip flag is the source's;
    each thin pool's used data and metadata are within QUBES_POOL_PCT % of
    the source's (the snapshots share their origins' blocks again); each raw
    volume's SHA-256 (ht1-offline.sh's, read from the LVs) is the source's;
    the raw volumes, storage.qubes and boot.xen are the source's."""
    src, dst = load_json(src_path), load_json(dst_path)
    sl, dl = thin_lvs(src), thin_lvs(dst)
    bad, out = [], []
    snaps = [(w, lv, seg) for w, (lv, seg) in sl.items() if seg.get("type") == "thin" and seg.get("origin")]
    if not snaps:
        bad.append("the source's dump has no thin snapshot (no LV with an origin: bko-holt's Q2, or not a Qubes OS dump)")
    for (vg, name), lv, seg in snaps:
        got = dl.get((vg, name))
        if not got:
            bad.append(f"{vg}/{name}: not in the restored dump")
            continue
        if got[1].get("origin") != seg.get("origin"):
            bad.append(f"{vg}/{name}: origin {seg.get('origin')!r} -> {got[1].get('origin')!r}")
        if lv.get("id") and got[0].get("id") == lv.get("id"):
            bad.append(f"{vg}/{name}: the source's LV UUID: a snapshot is made anew by lvcreate -s")
    for w, (lv, _) in sl.items():
        got = dl.get(w)
        if got and ("ACTIVATION_SKIP" in (lv.get("flags") or [])) != ("ACTIVATION_SKIP" in (got[0].get("flags") or [])):
            bad.append(f"{w[0]}/{w[1]}: activation skip {'set' if 'ACTIVATION_SKIP' in (lv.get('flags') or []) else 'unset'} on the source, not on the restored disk")
    out.append(f"{len(snaps)} thin snapshots made anew, their origins as the source's")
    pools = [(w, lv, seg) for w, (lv, seg) in sl.items() if seg.get("type") == "thin-pool"]
    for (vg, name), lv, seg in pools:
        got = dl.get((vg, name))
        if not got:
            bad.append(f"pool {vg}/{name}: not in the restored dump")
            continue
        for f in ("data_used_bytes", "metadata_used_bytes"):
            va = lv.get(f, seg.get(f))
            vb = got[0].get(f, got[1].get(f))
            if not isinstance(va, int) or not isinstance(vb, int):
                bad.append(f"pool {vg}/{name}: no {f} in {'the source' if not isinstance(va, int) else 'the restored'} dump (bko-holt's Q2)")
            elif abs(vb - va) > va * QUBES_POOL_PCT / 100:
                bad.append(f"pool {vg}/{name}: {f} {va} -> {vb}, more than {QUBES_POOL_PCT} % apart (sharing lost?)")
            else:
                out.append(f"pool {vg}/{name} {f} {va} -> {vb}")
    sraw, draw = read_sums(sraw_path), read_sums(draw_path)
    if not sraw:
        bad.append(f"no raw volumes' checksums in {sraw_path}")
    for lv, h in sorted(sraw.items()):
        if draw.get(lv) != h:
            bad.append(f"raw volume {lv}: sha256 {h[:16]}... -> {(draw.get(lv) or 'missing')[:16]}")
    for lv in sorted(set(draw) - set(sraw)):
        bad.append(f"raw volume {lv}: only on the restored disk")
    out.append(f"{len(sraw)} raw volumes' checksums as the source's")

    def raws(d):
        return {x.get("dm", {}).get("dm_name") or x.get("name"): x.get("raw_volume") for x in (d.get("storage") or {}).get("dm_devices") or [] if x.get("raw_volume")}
    rs, rd = raws(src), raws(dst)
    if rs != rd:
        bad.append(f"raw_volume records: {sorted(rs)} -> {sorted(rd)}")
    for path, what in ((("storage", "qubes"), "storage.qubes"), (("boot", "xen"), "boot.xen")):
        vs, vd = (src.get(path[0]) or {}).get(path[1]), (dst.get(path[0]) or {}).get(path[1])
        if vs is None:
            bad.append(f"no {what} in the source's dump (bko-holt's Q4)")
        elif vs != vd:
            bad.append(f"{what}: {json.dumps(vs)[:160]} -> {json.dumps(vd)[:160]}")
    for b in bad:
        print(f"DIFF {b}")
    for o in out:
        if o.startswith("pool "):
            print(o)
    print(f"{len(bad)} differences; " + "; ".join(x for x in out if not x.startswith("pool ")))
    sys.exit(1 if bad else 0)


# zbm-boot: a console's escapes (ZBM's colours, cursor moves, charset picks).
ZBM_ANSI = re.compile(r"\x1b\[[0-9;?=]*[A-Za-z]|\x1b[()%][A-Za-z0-9@]")
# The dracut zfs module's own prompt (mount-zfs.sh's ask_for_password, tries
# 5), printed before each `zfs load-key` it runs, also when the keylocation
# is a file and nothing is read: "Encrypted ZFS password for zroot:  [1/5]:".
ZBM_INITRD_PROMPT = re.compile(r"Encrypted ZFS password for (\S+?):\s*(?:\[(\d+)/(\d+)\])?")
# What console-boot.py and console-keys.py --passphrase answer (their PROMPT).
ZBM_TYPED_PROMPT = re.compile(r"(?i)(?:please unlock disk|geli passphrase for|(?:^|\n)passphrase:|enter passphrase)[^\n]*")


def judge_zbm_boot(state_path, log_path, ds, kver, prompts):
    """`judge.py zbm-boot STATE LOG DATASET KVER PROMPTS`: the passphrase
    prompts of one boot of a ZFSBootMenu BE (ht1.sh's zbm_judge_boot): its
    serial console LOG and the guest's state after it (STATE, ht1.sh's
    ZBM_STATE: the "key" line).  ZBM's line booting vmlinuz-KVER for DATASET
    splits the console: before it, PROMPTS (0 or 1) of ZBM's "Enter
    passphrase"; after it, in the BE's own initramfs, nothing typed (no
    console-keys.py line, no prompt console-boot.py answers) and, on an
    encrypted pool (PROMPTS 1), the dracut zfs module's "Encrypted ZFS
    password for POOL: [1/5]" once and never a retry ([2/5]...: the key
    file didn't open the pool), none on a plain one.  STATE's key line: on
    an encrypted pool, the encryption root of / with a file:// keylocation,
    its key available, and that file in the BE's initramfs for KVER (the
    one ZBM loaded); on a plain one "key -".  Prints DIFF lines and a
    summary; exit 1 on any DIFF."""
    want = int(prompts)
    bad, said = [], []
    try:
        with open(log_path, "rb") as f:
            con = ZBM_ANSI.sub("", f.read().decode("utf-8", "replace")).replace("\r", "")
    except OSError as e:
        bad.append(f"the console log unread ({e.strerror})")
        con = ""
    m = re.search(r"Booting /boot/vmlinuz-" + re.escape(kver) + r" for " + re.escape(ds) + r"(?=\s|$)", con)
    if not m:
        bad.append(f'no "Booting /boot/vmlinuz-{kver} for {ds}" on the console')
        before, after = con, ""
    else:
        before, after = con[:m.start()], con[m.end():]
    n = len(re.findall(r"(?i)enter passphrase", before))
    if n != want:
        bad.append(f"{n} of ZFSBootMenu's passphrase prompts before it booted the BE, not {want}")
    else:
        said.append(f"{n} ZFSBootMenu passphrase prompt(s)")
    if ZBM_TYPED_PROMPT.search(after):
        bad.append(f"a passphrase prompt in the BE's initramfs: {ZBM_TYPED_PROMPT.search(after).group(0).strip()[:60]!r} (the key file didn't open the pool)")
    if "[console-keys.py:" in after:
        bad.append("console-keys.py typed after ZFSBootMenu booted the BE")
    tries = ZBM_INITRD_PROMPT.findall(after)
    if want:
        if not tries:
            bad.append("the BE's initramfs never ran its load-key (no \"Encrypted ZFS password for POOL: [1/5]\" after ZFSBootMenu booted it)")
        elif len(tries) != 1 or tries[0][1] not in ("", "1"):
            shown = ", ".join(f"for {p}" + (f" [{t}/{o}]" if t else "") for p, t, o in tries)
            bad.append(f"the BE's initramfs asked {len(tries)} time(s) ({shown}), not once at [1/5] (a retry: its key file didn't open the pool)")
        else:
            said.append(f"the initramfs's load-key once ({tries[0][0]} [1/{tries[0][2] or '?'}]), no retry")
    elif tries:
        bad.append(f"the BE's initramfs asked for a ZFS password on a pool without one ({len(tries)} time(s))")
    try:
        with open(state_path, errors="replace") as f:
            key = next((ln.split()[1:] for ln in f if ln.startswith("key ")), None)
    except OSError:
        key = None
    if key is None:
        bad.append("no key line in the boot's state (unread)")
    elif want:
        if len(key) != 4 or not key[1].startswith("file://") or key[2] != "available" or key[3] != "initramfs:yes":
            bad.append(f"key {' '.join(key)}, not an encryption root with a file:// keylocation, available, and the file in initramfs-{kver}.img")
        else:
            said.append(f"{key[0]}'s key available from {key[1]}, in initramfs-{kver}.img")
    elif key != ["-"]:
        bad.append(f"key {' '.join(key)} on a pool ZFSBootMenu never asked for")
    for b in bad:
        print(f"DIFF {b}")
    print(f"{len(bad)} differences; " + "; ".join(said))
    sys.exit(1 if bad else 0)


QUBES_CHECK = re.compile(r"^BKO-QUBES-CHECK (\S+) (PASS|FAIL)(?: (.*))?$")


def judge_qubes_check(src_path, dst_path):
    """bko-qubes-check.service's lines from the restored disk's boot against
    the source's (ht1-offline.sh's logs/*-check.log): DONE PASS; every item
    the source passed passes, none fails; what the qubes print (run-*: each
    qube's hostname and its seeded file's hash) and the lvm item (thin LV
    and origin counts, work's chain) are the source's.  Times (start-*,
    shutdown, qubesd) may differ."""
    def items(path):
        out = {}
        with open(path, errors="replace") as f:
            for line in f:
                m = QUBES_CHECK.match(line.strip())
                if m:
                    out[m.group(1)] = (m.group(2), m.group(3) or "")
        return out
    s, d = items(src_path), items(dst_path)
    bad = []
    if d.get("DONE", ("",))[0] != "PASS":
        bad.append(f"the restored boot's DONE is {d.get('DONE', ('missing',))[0]}")
    for k, (r, rest) in sorted(s.items()):
        if k == "DONE":
            continue
        got = d.get(k)
        if not got:
            bad.append(f"{k}: missing on the restored boot")
        elif got[0] != "PASS" and r == "PASS":
            bad.append(f"{k}: {got[0]} {got[1]}")
        elif (k.startswith("run-") or k == "lvm" or k == "qvm-ls") and got[1] != rest:
            bad.append(f"{k}: {rest!r} -> {got[1]!r}")
    for k, (r, rest) in sorted(d.items()):
        if r == "FAIL" and k not in s:
            bad.append(f"{k}: FAIL {rest} (not on the source)")
    for b in bad:
        print(f"DIFF {b}")
    print(f"{len(bad)} differences; {sum(1 for v in d.values() if v[0] == 'PASS')} items PASS, "
          + ", ".join(f"{k} {v[1]}" for k, v in sorted(d.items()) if k == "lvm"))
    sys.exit(1 if bad else 0)


def main():
    if sys.argv[1] == "esp":
        judge_esp(sys.argv[2])
    if sys.argv[1] == "bes":
        judge_bes(sys.argv[2], sys.argv[3])
    if sys.argv[1] == "zbm":
        judge_zbm(*sys.argv[2:6])
    if sys.argv[1] == "zbm-boot":
        judge_zbm_boot(*sys.argv[2:7])
    if sys.argv[1] == "qubes":
        judge_qubes(*sys.argv[2:6])
    if sys.argv[1] == "qubes-check":
        judge_qubes_check(*sys.argv[2:4])
    if sys.argv[1] == "fs":
        judge_fs(*sys.argv[2:5], no_atime="--no-atime" in sys.argv[5:], new_uuids="--new-uuids" in sys.argv[5:],
                 reforms=reformat_args(sys.argv[5:]))
    grow, fit, pct, plan_only = 0, None, None, None
    flags = set()
    reforms = []
    while sys.argv[1] in ("--expect", "--new-uuids", "--removable", "--nvram-kept", "--grow", "--fit", "--fit-pct", "--fit-plan", "--reformat"):
        if sys.argv[1] in ("--expect", "--new-uuids", "--removable", "--nvram-kept"):
            flags.add(sys.argv.pop(1))
            continue
        if sys.argv[1] == "--reformat":
            reforms += reformat_args(sys.argv[1:3])
        elif sys.argv[1] == "--grow":
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
    # The same device-mapper devices numbered in another order: by name.
    ns, nd = dm_names(src), dm_names(dst)
    if ns != nd and sorted(ns.values()) == sorted(nd.values()):
        src, dst = dm_by_name(src), dm_by_name(dst)
        print(f"device-mapper devices compared by name: their dm-N differ ({', '.join(f'{v} {k}' for k, v in sorted(ns.items()) if nd.get(k) != v)} on the source)")
    tu_bad, tu_notes = transactional_judge(src, dst)
    rf_bad, rf_notes = reformat_expect(src, dst, reforms) if reforms else ([], [])
    made_src, made_uuid_src = lvcreate_made(src)
    made_dst, made_uuid_dst = lvcreate_made(dst)
    snap_pools = thin_snapshot_pools(src)
    # Thin LVs holding a backed-up filesystem on both sides, and the pools
    # (without snapshots: Qubes's keep qubes_thin_why) holding only those.
    src_thins = thin_lvs(src)
    fs_thins = {w for w in fs_thin_lvs(src) & fs_thin_lvs(dst) if (w[0], src_thins[w][1].get("thin_pool")) not in snap_pools}
    fs_pools = fs_thin_pools(src, fs_thins) & fs_thin_pools(dst, fs_thins)
    timeshift = timeshift_left_out(src)
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
    geli = geli_records(src)
    for k in sorted(set(a) | set(b)):
        if a.get(k, "<absent>") == b.get(k, "<absent>"):
            continue
        kk = geli_key(k, geli)
        why = next((w for p, w in IGNORED + (EXPECT_IGNORED if "--expect" in flags else []) if re.search(p, kk)), None)
        # GELI's provsize is its consumer's size: it may differ where the
        # partition's own size does, which is compared (or allowed) itself.
        gh = next((h for h, r in geli if k in (r + ".provsize", r + ".provsize_bytes")), None)
        if not why and gh and a.get(k) == a.get(gh + ".size_bytes") and b.get(k) == b.get(gh + ".size_bytes"):
            why = "a GELI provider's provsize, its partition's size (compared on its own)"
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
        # A zvol (Proxmox's VM disk) load makes empty: its bytes aren't in the backup.
        m = re.match(r"^(storage\.disks\.\d+)\.boot_code\.(kind|first_bytes)$", k)
        if not why and m and b.get(m.group(1) + ".boot_code.kind") == "none" \
                and any(str(v).startswith("/dev/zvol/") for x, v in a.items() if x.startswith(m.group(1) + ".links.")):
            why = "a zvol's bytes: load makes it empty (archivers back up files, not zvols: the plan's note)"
        m = re.match(r"^(storage\.swaps\.\d+)\.size_bytes$", k)
        if not why and m and str(a.get(m.group(1) + ".device") or "").startswith("zram"):
            why = "a swap on zram is made anew at each boot, sized from MemTotal (zram-tools' PERCENT), which moves with the initramfs's size (pikaos4)"
        if not why and k not in a and any(re.search(r, kk) for r in NEW_FIELDS):
            why = "a field bko-holt began recording after the source's dump was made"
        if not why and re.match(r"^storage\.zfs\.tools\.datasets\.\d+\.origin$", k) and va and vb is None:
            why = "a ZFS clone (a boot environment) comes back as a plain dataset: its origin snapshot isn't in the backup"
        if not why and all(isinstance(v, str) and v.startswith("/dev/disk/by-diskseq/") for v in (va, vb)):
            why = "by-diskseq links count the disks the kernel has seen since it booted"
        # Thin pools and their LVs, made anew by lvcreate (lvcreate_made).
        m = re.match(r"^(storage\.lvm\.volume_groups\.\d+)\.logical_volumes\.(\d+)\.(.+)$", k)
        if not why and m:
            lvk = f"{m.group(1)}.logical_volumes.{m.group(2)}"
            who = (a.get(m.group(1) + ".name"), a.get(lvk + ".name"))
            if who in made_src and who == (b.get(m.group(1) + ".name"), b.get(lvk + ".name")) and who in made_dst:
                if m.group(3) in ("id", "creation_host", "creation_time"):
                    why = "a thin pool, its data and metadata LVs, the pool metadata spare and the thin LVs are made anew by lvcreate: new LV UUIDs, the rescuer's host and time"
                elif re.fullmatch(r"segments\.\d+\.stripes\.\d+", m.group(3)) and isinstance(va, int) and isinstance(vb, int):
                    why = "where lvcreate puts a thin pool's data, metadata and spare LVs on the PV is its own"
                elif re.fullmatch(r"(segments\.\d+\.)?mapped_bytes", m.group(3)) and who in fs_thins and isinstance(va, int) and isinstance(vb, int):
                    why = ("a thin LV's mapped blocks, where it holds a filesystem the backup reads (judge.py fs compares its files): "
                           "what the restore wrote into a new LV, not the source's history of writes and discards")
                elif re.fullmatch(r"(segments\.\d+\.)?(data|metadata)_used_bytes", m.group(3)) and who in fs_pools and isinstance(va, int) and isinstance(vb, int):
                    why = ("a thin pool's used data and metadata, where its every thin LV holds a filesystem the backup reads "
                           "(judge.py fs compares their files): what the restore wrote into a new pool")
        if not why:
            why = qubes_thin_why(k, va, vb, a, snap_pools)
        m = re.match(r"^(storage\.dm_devices\.\d+)\.(dm\.dm_uuid|dm\.lv_uuid|links\.\d+)$", k)
        if not why and m and a.get(m.group(1) + ".dm.lv_uuid") in made_uuid_src and b.get(m.group(1) + ".dm.lv_uuid") in made_uuid_dst \
                and (not m.group(2).startswith("links") or all(isinstance(v, str) and v.startswith("/dev/disk/by-id/dm-uuid-LVM-") for v in (va, vb))):
            why = "a thin pool's or thin LV's device-mapper UUID holds its LV UUID, new from lvcreate"
        if why:
            ignored.setdefault(why, []).append(k)
        else:
            real.append(k)
            print(f"DIFF {k}: {json.dumps(a.get(k, '<absent>'))[:200]} -> {json.dumps(b.get(k, '<absent>'))[:200]}")
    for x in tu_bad:
        real.append("transactional")
        print(f"DIFF transactional: {x}")
    for x in tu_notes:
        print(x)
    for x in rf_bad:
        real.append("reformat")
        print(f"DIFF reformat: {x}")
    for x in rf_notes:
        print(x)
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
    if timeshift:
        print(f"left out (Timeshift's snapshots aren't in the backup, and load doesn't make them): {len(timeshift)}: {', '.join(timeshift)}")
    if dst.get("warnings"):
        print(f"restored dump's warnings: {dst['warnings']}")
    print(f"{len(real)} differences, {sum(len(v) for v in ignored.values())} ignored, of {len(set(a) | set(b))} fields")
    sys.exit(1 if real else 0)


if __name__ == "__main__":
    main()
