"""mkinitcpio, as the initramfs generator: Arch, Artix, CachyOS, Manjaro,
Omarchy.

- Quirk mkinitcpio-layers: load runs `mkinitcpio -P` in the chroot, with
  the source's mkinitcpio.conf: its HOOKS (lvm2, encrypt or sd-encrypt,
  mdadm_udev, zfs, btrfs) and MODULES say what the image can open
  (testvm/arch/install-live.sh rewrites HOOKS; the cachyos-zfs guest adds
  zfs).  The dump doesn't record HOOKS or MODULES (a dump gap: with them,
  the quirk could check the hooks a drift needs), so it keeps the whole
  set: no drift adds or drops a filesystem type, stack layer or md level.
"""

from .base import Trait, initramfs, layers_quirk

TRAITS = [Trait(
    name="mkinitcpio",
    matches=lambda source, dump: initramfs(dump).get("generator") == "mkinitcpio",
    quirks=[layers_quirk("mkinitcpio", "load runs mkinitcpio -P with the source's HOOKS, which the dump doesn't record")],
)]
