"""Upstream GRUB on UEFI, installed by load's grub-install (Arch-based
EndeavourOS, Garuda and Manjaro, Gentoo, Chimera's ZFS guest): GRUB
without Debian's patches.

- Known fail-003 (testvm/backshot2/cases/fail-003-removable-force-extra):
  load's `--removable` adds grub-install --force-extra-removable, Debian's
  own option, which upstream GRUB refuses ("unrecognized option").  Found
  on Alpine (whose family steers around it); the same grub-install runs
  for these sources, so the steer drops --removable here too.
- Matches a UEFI GRUB source whose boot stage is load's generic
  grub-install arm: not through shim (Fedora's and AnduinOS's arms run no
  grub-install), not Fedora's /boot/grub2, not a store system, and no
  Debian GRUB (whose packages include grub-common).  Alpine has its own.
"""

from .. import dumpx
from .base import Trait, loader, os_id, packages


def upstream_efi_grub(source, dump):
    boot = dump.get("boot") or {}
    return (dumpx.firmware(dump) == "uefi" and loader(dump) == "grub"
            and not (boot.get("loader") or {}).get("via_shim")
            and (boot.get("grub") or {}).get("dir") != "/boot/grub2"
            and os_id(dump) not in ("alpine", "nixos", "guix")
            and "grub-common" not in packages(dump)
            and not boot.get("proxmox_boot"))


def no_removable(dump, drifted, edits, opts):
    return [o for o in opts if o != "--removable"]


TRAITS = [Trait(
    name="grub_upstream",
    matches=upstream_efi_grub,
    known={"fail-003": "--removable runs grub-install --force-extra-removable, Debian's own option, which upstream GRUB refuses"},
    steer=no_removable,
)]
