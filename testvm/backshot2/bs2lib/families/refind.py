"""rEFInd (pikaos4, pikaos4-luks): its EFI files, drivers, theme and
refind.conf come back with the ESP and refind_linux.conf with /boot;
refind-install isn't run (commands.rs, "rEFInd ... nothing to install"),
and load checks refind_linux.conf's root=, rd.luks.name= and resume= UUIDs
against the layout (check_refind_uuids).

- Rule 3 texts: refind_linux.conf's options lines (boot.refind.linux_conf):
  a root=/dev/vda3 there would come back as written.
- Dump gap: refind.conf (EFI/refind/refind.conf, and its copy in EFI/BOOT)
  isn't recorded; its manual stanzas' volume and dont_scan_volumes name
  volumes by label or GUID, not number, so no quirk refuses for it.
"""

from .base import Trait, loader


def texts(dump):
    lc = ((dump.get("boot") or {}).get("refind") or {}).get("linux_conf") or {}
    return [str(l.get("options") or "") for l in lc.get("lines") or []]


TRAITS = [Trait(
    name="refind",
    matches=lambda source, dump: loader(dump) == "refind" or bool((dump.get("boot") or {}).get("refind")),
    config_texts=texts,
)]
