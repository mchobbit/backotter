"""proxmox-boot-tool (proxmox9, proxmox9-mirror; docs/design/proxmox.md):
Proxmox VE on ZFS root keeps one ESP per disk, none of them mounted or in
fstab, listed by vfat volume ID in /etc/kernel/proxmox-boot-uuids
(boot.proxmox_boot).  The ESPs aren't backup sources: load formats each
with its volume ID, then in the chroot runs `proxmox-boot-tool init` on
each by /dev/disk/by-uuid and one `refresh` (holt/src/load/commands.rs
proxmox_boot_cmds), which writes systemd-boot, every kernel and initrd and
the loader entries onto every ESP from /etc/kernel/cmdline.  The source is
Debian 13 to the rest (linux family; initramfs-tools and systemd-boot
traits: its entries are proxmox-boot-tool's, remade by the refresh with the
same options).

- Rule 3 texts: proxmox_boot's cmdline (the same as /etc/kernel/cmdline,
  which dumpx.config_texts reads), its pin and its manual kernels: a root=
  written there by device node would come back as written.  An ESP's
  device in proxmox_boot.esps is where the dump found the volume ID, not
  configuration: renumbering an ESP needs no edit (renumber renames it in
  the dump with every other reference), and every ESP a UEFI entry names
  stays put anyway (edits.movable; on the mirror both have one).
- Quirk proxmox-boot-esps: load finds each listed ESP by the partition
  proxmox_boot.esps names and formats it with that volume ID, so in the
  drifted layout the list must be the source's, and each ESP's partition
  must still hold that vfat.  No phase 1 edit changes either (no FAT is
  resized or reformatted, no UUID changes), so it holds by construction;
  the check keeps it so for what later lanes add (A3's per-disk drift on
  the mirror's second disk).
- Rule 1 already sizes an unmounted ESP by the files the dump listed on it
  (dumpx.used_bytes), which are what the refresh writes back.
- Load's own refusals are plan problems, not drift: a release other than
  Proxmox VE 9, GRUB mode or shim (no corpus source).  When load builds
  --new-uuids, it must rewrite proxmox-boot-uuids with the new volume IDs
  too.
"""

from .. import dumpx
from .base import Quirk, Trait


def proxmox(dump):
    return (dump.get("boot") or {}).get("proxmox_boot") or {}


def esps(dump):
    """[(volume ID, partition or None)] in the list's order."""
    by = {e.get("uuid"): e.get("device") for e in proxmox(dump).get("esps") or []}
    return [(u, by.get(u)) for u in proxmox(dump).get("uuids") or []]


def check_esps(source, drifted, edits):
    out = []
    a, b = [u for u, _ in esps(source)], [u for u, _ in esps(drifted)]
    if a != b:
        out.append((3, f"proxmox-boot-tool's ESPs {a} became {b}: load formats and initialises the ones the list names"))
    was = dict(esps(source))
    devs = dumpx.devices(drifted)
    for u, dev in esps(drifted):
        if was.get(u) is None:
            continue   # the source's dump didn't find it either: load's plan problem, not the drift's
        c = ((devs.get(dev) or {}) if dev else {}).get("content") or {}
        if c.get("type") != "vfat" or str(c.get("uuid") or "").upper() != str(u).upper():
            out.append((3, f"proxmox-boot-tool's ESP {u}: the drifted layout's {dev or 'list'} doesn't hold it "
                           f"({c.get('type') or 'nothing'} {c.get('uuid') or ''}), so load couldn't make it with its volume ID"))
    return out


def texts(dump):
    pb = proxmox(dump)
    out = [str(pb.get("cmdline") or ""), str(pb.get("pin") or "")]
    mk = pb.get("manual_kernels")
    out += [str(k) for k in mk] if isinstance(mk, list) else [str(mk or "")]
    return out


TRAITS = [Trait(
    name="proxmox-boot",
    matches=lambda source, dump: bool(proxmox(dump)),
    quirks=[Quirk("proxmox-boot-esps", "load makes each listed ESP again with its volume ID, from the partition the dump found it on", check_esps)],
    config_texts=texts,
)]
