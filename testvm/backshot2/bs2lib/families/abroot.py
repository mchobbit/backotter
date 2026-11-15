"""ABRoot (vanilla3: Vanilla OS 3, ABRoot v2; docs/design/vanillaos.md):
load restores /boot (the master grub.cfg and grub.cfg.future), the ESP,
the init LV (init/vos-a, init/vos-b: kernels, initramfs, abroot.cfg) and
both roots verbatim, and runs no initramfs, grub-install, update-grub or
chroot (holt/src/load/abroot.rs abroot_boot); its check script then holds
the master grub.cfg's default entry to the dump's root and each root's
abroot.cfg to its own kernel, initramfs and filesystem.

- Rule 3 texts: the master grub.cfg's entries (`set root=(lvm/...)`,
  configfile), each root's abroot.cfg (its linux line: root=UUID= or
  /dev/mapper/, the kernel and initrd paths), ABRoot's kargs and
  PartCryptVar (boot.abroot).  They name the init LV by LV name, the roots
  by filesystem UUID or dm name, /var by label: none by number, and edits
  keep every UUID, label and LV name.  One naming a partition by node
  (root=/dev/vda3) or by GRUB number ((hd0,gpt3)) would come back as
  written: the generic patterns find it in these texts.
- No quirk of its own:
  - the restored initramfs is the source's (made by ABRoot in a chroot of
    the root at upgrade time), so it opens only what the source had; the
    initramfs-tools trait's layer rule (no new filesystem type, stack
    layer or md level) holds for it too, and matches it;
  - the thin pool's data, metadata and spare LVs are lvm_thin.py's;
  - the ESP's EFI/vanilla/grub.cfg and EFI/BOOT/grub.cfg (Debian's
    search.fs_uuid of /boot, a partition hint beside it) aren't in the
    dump; the generic "GRUB has no search UUIDs" rule refuses renumbering
    /boot's partition, which covers the hint;
  - the per-root fstab lives in /var/lib/abroot/etc/LABEL (the /etc
    overlay uppers, backed up and restored as files): the dump's
    storage.fstab is the present root's, by UUID; the other's isn't
    recorded (a dump gap, by UUID on every guest seen);
  - --removable is a no-op in load's ABRoot boot stage (no grub-install);
    EFI/BOOT is restored as a file, which judge.py's ABROOT_KEPT holds to
    the source's bytes; --no-nvram skips the efibootmgr entry as elsewhere.
  BIOS, an operation.lock, a non-thin or ABRoot v1 layout and --new-uuids
  are load's plan problems, and no drift adds a layer or a VG.
"""

from .base import Trait, strings


def texts(dump):
    a = (dump.get("boot") or {}).get("abroot") or {}
    out = strings((a.get("grub") or {}).get("entries") or [])
    for r in a.get("roots") or []:
        out += strings(((r.get("init") or {}).get("abroot_cfg")) or {})
    out.append(str(a.get("kargs") or ""))
    out.append(str((a.get("config") or {}).get("PartCryptVar") or ""))
    return out


TRAITS = [Trait(
    name="abroot",
    piece="V11",
    matches=lambda source, dump: bool((dump.get("boot") or {}).get("abroot")),
    config_texts=texts,
)]
