"""NixOS (nixos2605): a store system, whose generations each carry their
own boot parameters and their own /etc (holt/src/store.rs).

- Quirk generations: refuses renumber and swap_order of any partition that
  holds something (content, a mount, a device on it) when the dump lists
  two or more generations.  Why: load restores grub.cfg (or systemd-boot's
  entries) as it was and rebuilds nothing (commands.rs: "the restored
  grub.cfg stays"; store.rs), and every generation boots with its own
  kernel parameters and mounts its own fileSystems (each generation's
  closure has its own etc/fstab).  The dump records only the current
  generation's: fstab, the command line and grub.cfg's default linux line
  (boot.grub.grub_cfg.default_linux); of the others only their menu
  titles ("NixOS - Configuration 2 (...)") or their loader entries.  holt
  records boot.store (each generation's parameters) for Guix System only
  (store.rs: "NixOS's dump records neither").  A dump gap: with each
  generation's kernel parameters and fstab recorded, rule 3 could check
  them instead.  With one generation, the generic rule 3 sees all there is.
- Quirk initrd layers: each generation's initrd is a store item, built
  from its configuration's boot.initrd modules, and isn't rebuilt by
  load: a drift may not add or drop a filesystem type or stack layer.
- Rule 3 texts: boot.store's generations (kernel arguments, root and store
  devices) and grub.cfg's searches when a dump has them (Guix's format;
  none of the corpus's NixOS dumps do), and systemd-boot's
  nixos-generation-N entries (systemd_boot.py).
- versions(): nothing in the dump says (boot.packages is empty: NixOS has
  no package database, and holt reads tools from /run/current-system), so
  every tool takes its oldest row: the most conservative values.
- tier0 "plan": the source's tools are in /nix/store, reached through
  /run/current-system, which a chroot of the copied root hasn't (its /run
  is empty), so tier 0's chrooted dry runs can't find mke2fs or sfdisk.
  /nix/var/nix/profiles/system/sw/bin would reach them: A2 can add that.
"""

import re

from .base import Family, Quirk, holds_something, layers_quirk, os_id, walk_moves

GEN_TITLE = re.compile(r"Configuration (\d+)")
GEN_ENTRY = re.compile(r"nixos-generation-(\d+)")


def generations(dump):
    """The generation numbers the dump lists: GRUB's menu titles, systemd-boot's entries, boot.store's."""
    boot = dump.get("boot") or {}
    out = set()
    for t in ((boot.get("grub") or {}).get("grub_cfg") or {}).get("entries") or []:
        m = GEN_TITLE.search(str(t))
        if m:
            out.add(int(m.group(1)))
    for e in (boot.get("systemd_boot") or {}).get("entries") or []:
        m = GEN_ENTRY.search(str(e.get("file") or ""))
        if m:
            out.add(int(m.group(1)))
    for g in (boot.get("store") or {}).get("generations") or []:
        if g.get("number") is not None:
            out.add(int(g["number"]))
    return out


def check_generations(source, drifted, edits):
    if len(generations(source)) < 2:
        return []
    out = []
    for e, dk, p in walk_moves(source, edits):
        if holds_something(source, p):
            out.append((3, f"{e['op']} {p['name']}: NixOS lists {len(generations(source))} generations, and the dump "
                           f"records only the current one's kernel parameters and fstab"))
    return out


def store_texts(dump):
    st = (dump.get("boot") or {}).get("store") or {}
    out = []
    for g in st.get("generations") or []:
        params = g.get("parameters") or g
        out += [str(a) for a in params.get("kernel_arguments") or []]
        for k in ("root_device", "store_device"):
            v = params.get(k)
            if isinstance(v, dict):
                out.append(str(v.get("value") or ""))
    for s in (st.get("grub_cfg") or {}).get("searches") or []:
        out.append(str(s.get("value") or ""))
    return out


FAMILY = Family(
    name="nixos",
    priority=10,
    matches=lambda source, dump: os_id(dump) == "nixos",
    versions=lambda dump: {},
    quirks=[
        Quirk("generations", "every generation boots with its own kernel parameters and fstab, which the dump doesn't record", check_generations),
        layers_quirk("NixOS's initrd", "each generation's initrd is a store item built from its configuration, not rebuilt by load"),
    ],
    config_texts=store_texts,
    tier0="plan",
)
