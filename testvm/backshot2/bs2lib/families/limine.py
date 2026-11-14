"""Limine (cachyos-zfs, omarchy): on UEFI, its EFI binary and limine.conf
are files on the ESP, restored with it; load installs nothing and makes
only the boot entry (commands.rs: "Limine: its EFI binary and limine.conf
came back with the ESP; nothing to install").  limine.conf names each
kernel, initramfs and module by a path on a partition: boot():/ (the
partition Limine was loaded from), boot(N):/ (partition N of the boot
drive), hdd(D:P):/, or guid(), uuid(), fslabel() (limine-entry-tool's
entries; testvm/arch/install-cachyos-zfs.sh, limine-update), and the
cmdline: lines name the root.

- Dump gap: limine.conf's text isn't recorded (only the ESP's file list:
  boot.esps[].contents), nor /etc/default/limine.
- Quirk limine-conf: so renumber and swap_order are refused for every
  partition that holds something: limine.conf could name it boot(N) or
  hdd(D:N), which neither load nor the restore rewrites.  The ESP a UEFI
  entry names (Limine's own, by start and number) already stays put
  (edits.movable).
- Quirk limine-bios: BIOS Limine's stage 2 is found by sectors `limine
  bios-install` wrote into the MBR's stage 1, and load can't install BIOS
  Limine at all (a plan problem); every op that moves a partition's start
  is refused.  No corpus source boots BIOS Limine.
- Rule 3 patterns: boot(N) and hdd(D:N), for every text rule 3 reads (and
  for limine.conf once the dump has it).
"""

from .. import dumpx
from .base import Quirk, Trait, holds_something, loader, number_re, walk_moves


def check_conf(source, drifted, edits):
    return [(3, f"{e['op']} {p['name']}: limine.conf, which the dump doesn't record, may name it by number (boot(N), hdd(D:N))")
            for e, dk, p in walk_moves(source, edits) if holds_something(source, p)]


def check_bios(source, drifted, edits):
    if dumpx.firmware(source) == "uefi":
        return []
    return [(3, f"{e['op']} {p['name']}: BIOS Limine finds its stage 2 by the sectors bios-install wrote")
            for e, dk, p in walk_moves(source, edits, ("renumber", "swap_order", "gap_before", "align"))]


TRAITS = [Trait(
    name="limine",
    matches=lambda source, dump: loader(dump) == "limine",
    quirks=[
        Quirk("limine-conf", "limine.conf isn't in the dump and may name partitions by number", check_conf),
        Quirk("limine-bios", "BIOS Limine's stage 2 is found by sector", check_bios),
    ],
    number_patterns=number_re(r"(?<![A-Za-z])boot\({num}\)", r"hdd\(\d+:{num}\)"),
)]
