"""systemd-boot and Boot Loader Specification entries (boot.systemd_boot):
systemd-boot's own (AerynOS, EndeavourOS, Pop!_OS, chimera-uefi), and the
BLS entries GRUB's blscfg reads on Fedora and Rocky, which the dump records
in the same place.

- Rule 3 texts: every string of every entry (linux, initrd, options:
  root=UUID=, root=PARTUUID=, root=/dev/mapper/..., resume=, rd.*): load
  keeps the entries as restored, except Chimera's, which gen-systemd-boot
  rewrites, and Fedora's, which grub2-mkconfig doesn't touch either.  (They
  were in dumpx.config_texts before the family interface; the same texts,
  now the trait's.)  An entry's linux and initrd are paths on the ESP or
  XBOOTLDR, never a partition number.
- No quirk: load runs `bootctl install --no-variables` (or nothing, through
  shim or on a store system) and makes the boot entry; the ESP a UEFI
  entry names by start and number stays put (edits.movable).
- Dump gap: Chimera's /etc/default/systemd-boot (SD_BOOT_CMDLINE) isn't
  recorded; a root= written there stays as written on the rewritten
  entries.
"""

from .base import Trait, loader, strings


def has_entries(dump):
    return bool(((dump.get("boot") or {}).get("systemd_boot") or {}).get("entries"))


def texts(dump):
    return strings(((dump.get("boot") or {}).get("systemd_boot") or {}).get("entries") or [])


TRAITS = [Trait(
    name="systemd-boot",
    matches=lambda source, dump: loader(dump) == "systemd-boot" or has_entries(dump),
    config_texts=texts,
)]
