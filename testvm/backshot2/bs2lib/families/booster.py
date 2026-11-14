"""booster, as the initramfs generator: PikaOS (pikaos4, pikaos4-luks).

- Quirk booster-layers: load runs `booster build` for each kernel in the
  chroot, as booster.yaml says (commands.rs: "as booster.yaml says, from
  the chroot's /sys").  The dump records booster.yaml's universal,
  enable_lvm, modules and modules_force_load (boot.initramfs.booster); a
  non-universal image has the modules of what it sees plus those, so no
  drift may add or drop a filesystem type, stack layer or md level.
"""

from .base import Trait, initramfs, layers_quirk

TRAITS = [Trait(
    name="booster",
    matches=lambda source, dump: initramfs(dump).get("generator") == "booster",
    quirks=[layers_quirk("booster", "load rebuilds the image as booster.yaml says, host-specific unless universal")],
)]
