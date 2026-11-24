"""Generic Linux: every Linux source no more specific family claims (the
lowest priority).  It holds little of its own (its content drift's choice
of recipe, below): the generic generator,
rules (rules.py), tables (tables.py) and rule 3 texts (dumpx.config_texts)
are Linux's, and its sources' own rules come from their traits (the
initramfs generator, the loader, snapper, ostree).

A dump is Linux when it records no os.family (holt's dumps of the other
systems say freebsd, openbsd, netbsd, dragonfly, illumos, haiku, redox or
genode).

Why some sources the A1 brief named have no family of their own (each read
from the corpus dumps, holt/src/load and the guests' build scripts):
- Gentoo (gentoo, gentoo-systemd).  load remakes each kernel's initramfs
  with dracut under installkernel's name (commands.rs gentoo_initramfs),
  host-only by Gentoo's 01-gentoo.conf (testvm/bootstrap/gentoo.sh): a
  host-only image names the root, LUKS and LVM by UUID and name, never by
  number, and the dracut trait's layer rule covers what it includes.  A
  kernel without /lib/modules keeps its restored image, which was made the
  same way.  grub-mkconfig remakes grub.cfg.  Its package names are the
  plain ones (e2fsprogs 1.47.4, lvm2 2.03.39, cryptsetup 2.8.8), at or
  above every generic row.  Its UEFI GRUB is upstream's: grub_upstream.py.
- Chimera (chimera, chimera-uefi, chimera-zfs).  apk names as Alpine's,
  versions at or above the generic rows.  On BIOS its command line names
  / by an MBR PARTUUID (root=PARTUUID=SIG-03), which the generic rule 3
  already refuses to renumber (dumpx.names_by_number's PARTUUID pattern).
  On UEFI load runs gen-systemd-boot, which rewrites the entries from the
  restored root (docs/design/chimera.md: kernel-root-detect), so they
  follow the drift; its /etc/default/systemd-boot isn't in the dump (a
  root= written there stays as written: a dump gap, rule 3 can't see it).
- AerynOS (aerynos).  Its dump records no packages (moss isn't read), so
  every tool's oldest row applies; nothing drawn on it needs one (/ is
  f2fs, which no geometry edit draws, and the ESP's FAT isn't drawn).
  Its initramfs is prebuilt, not regenerated.
- shim.  Its one drift rule, that the partition a UEFI boot entry names by
  start and number stays put, is every UEFI entry's: edits.movable refuses
  it for every op that moves a partition.

Content drift (A7, GENERATOR "6"): choose() picks content.RECIPES' recipe
for the filesystem of the mount holding /srv/bs2 (Hooks.recipe's fstype),
which ht1-bs2.sh plants read-write through vm-mount, the guest's fstab
mounted as the guest mounts it:
  ext2, ext3, ext4  ext-1; ext-1-32bit without extra_isize (or with
                    128-byte inodes): such a source's ext4_geometry may
                    draw 128-byte inodes, which hold no time past 2038
  xfs               xfs-1; xfs-1-32bit without bigtime (slackware15-uefi)
  btrfs             btrfs-1, in whichever subvolume holds /srv
  f2fs, jfs, nilfs2, bcachefs  f2fs-1, jfs-1, nilfs2-1, bcachefs-1, with
                    what content.FS_CAN says each holds
  zfs               None: ZFS's recipe comes with A3's storage layers
  vfat, squashfs, overlay, anything else  None: no recipe (/srv on FAT is
                    no Linux root; a read-only or synthesized root can't be
                    planted)
and None where it can't be planted or judged:
- ostree (kinoite44): the dump records no mount holding /srv (/ is the
  composefs made at boot from the deployment; the physical root is at
  /sysroot), and on Fedora's atomic desktops /srv is a link to var/srv, so
  neither the planter's filesystem nor rule 1's share would be known.
- Ubuntu Core (uc24, uc24-fde): no root filesystem (the base snap's
  squashfs); vm-mount's MOUNTPOINT is a read-only tmpfs.
- A /srv filesystem too small for the recipe's bound (content.bound(), at
  any seed): the undrifted source must hold it, by its free space
  (available_bytes, x rules.MARGIN) and by rule 1, or no case could plant
  it.  Of the corpus, only the synthetic 27 MiB arm64 boot fixtures.
Where content can be planted, on purpose:
- ABRoot (vanilla3): each root's /srv is a relative link to var/srv (the
  abroot trait's srv()), so the content lands on /var's btrfs (btrfs-1),
  which rule 1 charges and plant() reaches through the link, inside the
  source.
- snapper and btrfs subvolumes: /srv has its own subvolume on garuda
  (@srv), leap16, microos and tumbleweed (@/srv): the mount holding /srv is
  it, and vm-mount mounts it from fstab.  On microos / is a read-only
  snapshot; /srv's subvolume is read-write.
- Chimera, Gentoo, Solus: plain roots (xfs or ext4, chimera-zfs ZFS):
  nothing of theirs touches /srv.
content.plant() follows a relative link on the way to /srv/bs2 only inside
the source, by fd: an absolute one, or one leading out, is refused at
planting, never planted on the host.
"""

from .. import content as C, dumpx
from .base import Family

# The recipe for each filesystem the mount holding /srv/bs2 may have.
BY_FS = {"ext2": "ext-1", "ext3": "ext-1", "ext4": "ext-1", "xfs": "xfs-1", "btrfs": "btrfs-1",
         "f2fs": "f2fs-1", "jfs": "jfs-1", "nilfs2": "nilfs2-1", "bcachefs": "bcachefs-1"}
NONE_BY_FS = {
    "zfs": "ZFS: its recipe comes with A3's storage layers",
    "vfat": "FAT holds none of the extras, and /srv on FAT is no Linux root",
}


def _variant(name, fstype, c):
    """NAME, or its -32bit form where the filesystem (C, its content record) or a drift of it holds no time past 2038."""
    feats = c.get("features") or []
    if fstype in ("ext2", "ext3", "ext4") and ("extra_isize" not in feats or (c.get("inode_size") or 256) <= 128):
        return "ext-1-32bit"
    if fstype == "xfs" and "bigtime" not in feats:
        return "xfs-1-32bit"
    return name


def choose(dump, fstype):
    """(recipe, None), or (None, why not): the content recipe this source
    draws, FSTYPE being the filesystem of the mount holding /srv/bs2."""
    from .. import rules
    if (dump.get("boot") or {}).get("ostree"):
        return None, "ostree: no mount holds /srv (the composefs / is made at boot; /srv links to var/srv)"
    m = C.srv_mount(dump)
    if m is None or fstype is None:
        return None, "no mounted filesystem holds /srv in the dump"
    if fstype not in BY_FS:
        return None, NONE_BY_FS.get(fstype, f"no recipe for {fstype}")
    dev = dumpx.devices(dump).get(m.get("device"))
    c = (dev or {}).get("content") or {}
    name = _variant(BY_FS[fstype], fstype, c)
    size, inodes = C.bound(name)
    avail = m.get("available_bytes")
    if avail is not None and avail < size * rules.MARGIN:
        return None, f"{m['target']} has {avail} bytes free, the recipe takes up to {size}"
    if avail is None and (dev is None or rules.fs_bytes(c) is None):
        return None, f"the dump says nothing of {m['target']}'s free space"
    if len(rules.rule1(dump, {m["target"]: (size, inodes)})) > len(rules.rule1(dump)):
        return None, f"rule 1: {m['target']} can't hold the recipe's {size} bytes and {inodes} inodes"
    return name, None


def content(dump, fstype):
    return choose(dump, fstype)[0]


def is_linux(source, dump):
    o = dump.get("os") or {}
    return bool(dump.get("storage")) and o.get("family") in (None, "linux")


FAMILY = Family(
    name="linux",
    priority=0,
    matches=is_linux,
    content=content,
    tier0="chroot",
)
