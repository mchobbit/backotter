"""ostree (kinoite44; Bazzite on the Thinkpad): load restores /boot, the
ESP and /ostree verbatim and runs no initramfs, grub2-mkconfig or
grub2-install (holt/src/load/ostree.rs ostree_boot), then checks each loader
entry against the restored tree.

- Rule 3 texts: every ostree loader entry (boot.ostree.entries: options,
  linux, initrd, ostree=) and the booted karg.  The entries name / by
  root=UUID= and the deployment by path; one naming a device by its node
  would come back as written.
- No quirk of its own: the ESP that the firmware entry ("Fedora",
  shimx64.efi) names by start and number stays put (edits.movable), and
  bootupd's static grub.cfg finds /boot by label or UUID (boot.grub's
  search_uuids), which rule 3 keeps.  BIOS, LUKS and LVM ostree sources
  are load's plan problems, and no drift adds a layer.
"""

from .base import Trait, strings


def texts(dump):
    o = (dump.get("boot") or {}).get("ostree") or {}
    return strings(o.get("entries") or []) + [str(o.get("karg") or "")]


TRAITS = [Trait(
    name="ostree",
    matches=lambda source, dump: bool((dump.get("boot") or {}).get("ostree")),
    config_texts=texts,
)]
