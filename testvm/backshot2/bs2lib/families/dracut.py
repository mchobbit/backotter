"""dracut, as the initramfs generator (boot.initramfs.generator = dracut):
Fedora, Rocky, openSUSE, Void, EndeavourOS, Garuda, Gentoo, Ubuntu 26.04.

- Quirk dracut-layers: load regenerates the image in the chroot, with the
  source's own dracut.conf(.d) (commands.rs initramfs(): the dracut loop
  over /lib/modules, dracut-rebuild for eos-dracut and
  garuda-dracut-support, Gentoo's per kernel).  Host-only images (Fedora's
  and Gentoo's default) take the modules of the storage and filesystems
  they see, by the configuration's add_dracutmodules and
  omit_dracutmodules; the dump doesn't record that configuration (a dump
  gap), so the quirk keeps the whole set: no drift adds or drops a
  filesystem type, stack layer or md level.  A host-only image names /,
  LUKS, LVM and md by UUID and name (rd.luks.uuid, rd.lvm.lv, rd.md.uuid),
  never by partition number, and those are in the command line rule 3
  reads.
"""

from .base import Trait, initramfs, layers_quirk

TRAITS = [Trait(
    name="dracut",
    matches=lambda source, dump: initramfs(dump).get("generator") == "dracut",
    quirks=[layers_quirk("dracut", "load rebuilds the image with the source's dracut.conf; host-only images take only what they saw")],
)]
