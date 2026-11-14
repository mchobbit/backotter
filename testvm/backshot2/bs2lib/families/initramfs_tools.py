"""initramfs-tools, as the initramfs generator: Debian, Ubuntu 24.04, Mint,
MX, KDE neon, Pop!_OS, Zorin, Devuan, Chimera.

- Quirk initramfs-tools-layers: load runs `update-initramfs -u -k all` in
  the chroot.  MODULES=most (boot.initramfs.modules, on every corpus
  source) takes nearly every storage driver, but LUKS, LVM, md and ZFS
  come from their packages' hooks (cryptsetup-initramfs, lvm2, mdadm,
  zfs-initramfs), which a drift can't install: no drift adds or drops a
  filesystem type, stack layer or md level.
"""

from .base import Trait, initramfs, layers_quirk

TRAITS = [Trait(
    name="initramfs-tools",
    matches=lambda source, dump: initramfs(dump).get("generator") == "initramfs-tools",
    quirks=[layers_quirk("initramfs-tools", "update-initramfs takes LUKS, LVM, md and ZFS only from their packages' hooks")],
)]
