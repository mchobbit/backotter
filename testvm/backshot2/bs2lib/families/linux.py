"""Generic Linux: every Linux source no more specific family claims (the
lowest priority).  It holds nothing of its own: the generic generator,
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
"""

from .base import Family


def is_linux(source, dump):
    o = dump.get("os") or {}
    return bool(dump.get("storage")) and o.get("family") in (None, "linux")


FAMILY = Family(
    name="linux",
    priority=0,
    matches=is_linux,
    tier0="chroot",
)
