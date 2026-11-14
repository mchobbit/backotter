# Vanilla OS support (ABRoot A/B roots)

2026-11-14. Asked for by the user: support Vanilla OS 2, the immutable
Debian-sid system whose ABRoot keeps two root filesystems and switches
between them. It is written the way Bazzite was (docs/design/bazzite.md, the
ostree precedent for immutable systems), and borrows PikaOS's
(docs/design/pikaos.md) Debian-sid notes. **The user approved this spec
on 2026-11-14 with every recommended decision** (the last section).

**Conventions.** est. marks an estimate. *Check* marks a fact the sources
don't settle, to be settled on the first guest (V1) before the code that
depends on it. Vanilla OS facts come from its sources, read 2026-11-14, and
nothing was downloaded or installed: `Vanilla-OS/ABRoot` (main, v2.6.0:
`core/{disk-manager,root,grub,kargs,integrity,system,oci,packages}.go`,
`cmd/{mount-sys,unlock-var,update-initramfs}.go`, `settings/config.go`,
`config/abroot.json`), `Vanilla-OS/Albius` (the installer backend:
`albius.go`, `RECIPE.md`, `core/system/grub.go`, `post_install.go`),
`Vanilla-OS/vanilla-installer` (main, `vanilla_installer/utils/processor.py`,
the recipe the GUI hands to Albius, and `recipe.json`), `core-image` and
`desktop-image` (dev: the init wrapper, the initramfs hooks, image
`abroot.json`), `base-files` v1.1.0 (os-release), `live-iso` (3.0 release
page, `terraform.conf`), and `vanilla-system-operator` (`core/updater.go`).
Code references are to local `main` at 1edb4b5. Copies of the sources read
are in the session's scratchpad, not in the repo.

## Summary

- **Vanilla OS 2 "Orchid" is no longer the current release.** Vanilla OS 3
  "Reunion" came out on 2026-08-24, also on Debian sid, and still uses
  **ABRoot v2** (v2.6.0) and the same disk layout. The recommendation is
  to support **Vanilla OS 3** with a guest `vanilla3` (Decision 1). Orchid
  ISOs and images may no longer be published (*check*).
- ABRoot keeps **two btrfs roots on LVM thin volumes** (`vos-root/root-a`
  and `root-b`, labels `vos-a` and `vos-b`), a linear `init` LV that holds
  both roots' kernels, initramfs and GRUB configs, an ext4 `/boot` with a
  static master `grub.cfg` whose literal `set default=0|1` picks the
  root, the ESP, and a separate VG `vos-var` for `/var`.
- **bko-holt can't restore it today.** `load` makes linear and striped
  LVs only (`vg_metadata`, `holt/src/load/commands.rs:713`, "version 1
  makes linear and striped LVs only"), and Vanilla's roots are thin LVs.
  Thin pools in `load` are new generic work (V3).
- **The backup is the physical filesystems, as for ostree.** The running
  `/` is the present root mounted read-only, with an **overlay on `/etc`**
  whose upper lives in `/var/lib/abroot/etc/vos-X`. The inactive root and
  the `init` LV aren't mounted at all. dump lists all six filesystems in
  `storage.backup_sources` at ABRoot's own mount points (`/part-future`
  and `/boot/init`), and load mounts them the same way.
- **The boot stage regenerates nothing.** Kernels and initramfs are
  ABRoot's, in the `init` LV; the master `grub.cfg`, each root's
  `abroot.cfg`, shim and GRUB on the ESP all come back byte for byte, and
  every UUID, label and LV name they name is kept. `update-grub` is a stub
  that fails on Vanilla anyway. The stage checks that the restored GRUB
  configs lead to kernels and roots that exist, makes the NVRAM entry, and
  writes its log.
- **Both roots boot after the restore.** A fresh install leaves root B
  formatted but **empty** and `init/vos-b/` empty. So the guest is given
  one `abroot upgrade`, which fills B and makes it the present root, with
  A as the previous one. HoltTest1 then boots the restored guest twice,
  once into each root.
- ~6.4 d (est.), with no BackShot1 (the Thinkpad isn't proposed).

## Why

- **A/B roots are the other immutable design.** ostree has one physical
  root with a content-addressed repository. ABRoot keeps two whole root
  filesystems plus an OCI image cache. Users choose Vanilla OS for exactly
  that rollback, so a restore that boots only one root has lost the
  feature.
- **Filesystems that nothing mounts.** The inactive root and the `init`
  LV are never in `/proc/self/mountinfo` or fstab. Every tool that builds
  its tree from mounts or fstab (dump, BackShot1, vm-mount, HoltTest1's
  backup) would skip them, and the restored system would have no kernels.
- **Thin LVM.** No guest has a thin pool, and `load` refuses one. Thin
  pools are also common outside Vanilla: Fedora Server's and Proxmox's
  defaults (`pve/data`). The Proxmox session will hit the same wall.
- **Fixed names on every machine.** Every Vanilla install has VGs
  `vos-root` and `vos-var`, filesystem labels `vos-a`, `vos-b`, `vos-var`,
  `vos-boot` and `vos-init`, and ABRoot finds its roots **by label**,
  first match in `lsblk`. Two Vanilla disks attached at once confuse
  ABRoot and LVM alike.

## What Vanilla OS is (as far as bko cares)

| | Vanilla OS 3 "Reunion" (ABRoot v2.6.0) | Nearest supported system |
|---|---|---|
| Release | 3.0, 2026-08-24, Debian sid snapshots (`repo3.vanillaos.org`); Orchid (2.0) was 2024 to 2026, also on ABRoot v2 | PikaOS 4 (sid) |
| os-release | `ID=vanilla`, `ID_LIKE=debian`, `VERSION_ID="3.0"`, `VERSION="3.0 Reunion"`, `VERSION_CODENAME=reunion`, `PRETTY_NAME="Vanilla OS 3.0"` (base-files v1.1.0; *check* the installed one, and whether it lives in each root's `/usr/lib`) | Devuan, PikaOS |
| ISO | `Vanilla-OS-3-stable-amd64.20261106.iso`, 1,756,446,720 bytes, SHA-256 `78e81fc5...f70d` (a `.sha256.txt` only, no signature found). Live GNOME session, user `vanilla` with an empty password; no openssh-server and no serial console found in the live system | PikaOS (MD5 only) |
| Disk | GPT, from `processor.py`'s auto recipe, sizes in MiB: p1 `vos-boot` 1-1025, ext4 label `vos-boot`, /boot; p2 `vos-efi` 1025-1537, FAT32 ESP at /boot/efi; p3 `vos-root` 1537-24168 (~22 GiB), PV of VG **`vos-root`**; p4 `vos-var` to the end, PV of VG **`vos-var`**. The partition names are GPT partlabels, and Albius sets each one as the filesystem label too. Minimum disk 51,200 MiB. No swap | elementary8 (LVM), but no thin |
| VG `vos-root` | `init` 1024, linear, ext4 label `vos-init`; `root-meta` 1024 linear, joined into thin pool **`root`** (data 19,456) by `make-thin-pool`, plus LVM's `lvol0_pmspare`; thin LVs **`root-a`** and **`root-b`**, 19,456 virtual each (over-provisioned: their sum exceeds the pool), btrfs labels `vos-a`, `vos-b`, no subvolumes | none (thin) |
| VG `vos-var` | `var`, 100%FREE, btrfs label `vos-var`, /var. With the installer's encryption option, LUKS2 on the LV `/dev/vos-var/var`, opened as `luks-<UUID>` (crypttab `UUID=... none luks,discard`), btrfs inside. Roots, `init` and /boot are never encrypted | ubuntu26-luks (LVM and LUKS, the other order) |
| Each root | an extracted OCI image (`ghcr.io/vanilla-os/gnome:latest`), not a btrfs snapshot: `/usr`, `/etc` (the lower), `/sysconf` (a pristine copy of /etc), `/abimage.abr` (JSON: image digest and time), the empty mount point `/part-future`; `home mnt opt root srv` link into `var/`, `usr/local -> var/usrlocal`, `media -> run/media`, merged `/usr`. No `chattr +i` anywhere | |
| Runtime | `/usr/sbin/init` is a bash wrapper that runs `/usr/share/init.d/*`, then systemd. `090` unlocks /var if it is LUKS (`abroot unlock-var`, a Plymouth prompt); `100` runs `abroot mount-sys`, which mounts /var by label, repairs the root's links, and mounts **`/etc` as overlay** `lowerdir=/etc,upperdir=/var/lib/abroot/etc/<label>,workdir=.../<label>-work`. / is ro | Bazzite's /etc bind (an alias) |
| fstab | **per root, in its /etc upper** (`/var/lib/abroot/etc/vos-a/fstab`): UUID lines for /boot, /boot/efi, both roots and /var. `mount-sys` rewrites it at every boot (drops /, /var, /usr, /etc, then prepends `UUID=<present> / btrfs ro,defaults 0 0`) | |
| Boot | shim and Debian's signed GRUB, `grub-install --no-nvram --bootloader-id=vanilla --uefi-secure-boot`, NVRAM entry `vanilla` `\EFI\vanilla\shimx64.efi` (Albius `grub.go`). Secure Boot works. BIOS isn't offered | AnduinOS, neon (shim) |
| Master grub.cfg | `/boot/grub/grub.cfg` on `vos-boot`, written by the installer, static: `set default=0`, entries "Current State (A)" and "Previous State (B)", each `set root=(lvm/vos--root-init); configfile /vos-a/abroot.cfg` (or `vos-b`). An upgrade writes `grub.cfg.future` with `default` and the Current/Previous text swapped, then swaps the two files. ABRoot parses those strings to tell present from future | bootupd's static grub.cfg |
| `init` LV | `/vos-a/` and `/vos-b/`: `vmlinuz-K`, `initrd.img-K`, `config-K`, `System.map-K`, `abroot.cfg`. The installer's `abroot.cfg`: `linux (lvm/vos--root-init)/vos-a/vmlinuz-K root=UUID=<root-a fs UUID> quiet splash bgrt_disable $vt_handoff lsm=integrity`. After an upgrade it is the future root's `grub-mkconfig` output, with `search --fs-uuid --set=root <vos-init UUID>` and `root=/dev/mapper/vos--root-root--b`. Kargs from `/etc/abroot/kargs`. Mounted only by ABRoot, at `<root>/boot/init` (a directory on `vos-boot`) | ostree's `/boot/ostree/` |
| Initramfs | initramfs-tools, made by ABRoot in a chroot of the future root during an upgrade; `dm_thin_pool` and friends in `etc/initramfs-tools/modules`; the hook `vanilla-lvm-fix` adds `thin_repair` and runs `lvconvert --repair vos-root/root` when activation fails | Debian |
| ABRoot config | `/usr/share/abroot/abroot.json`, overlaid by `/etc/abroot/abroot.json`: `partLabelA vos-a`, `partLabelB vos-b`, `partLabelVar vos-var`, `partLabelBoot vos-boot`; the installer adds `thinProvisioning: true`, `thinInitVolume: vos-init`, `PartCryptVar` | |
| State | `/var/lib/abroot/etc/{vos-a,vos-b}{,-work}` (each root's /etc changes, holding overlay **whiteouts**, char 0:0, and `trusted.overlay.*` xattrs); `/var/lib/abroot/storage` (containers storage: the cached OCI image, more whiteouts); `abroot pkg`'s lists in `/etc/abroot/packages.{add,remove}`; `/abimage.abr` per root; `/run/abroot/*` at run time only | |
| Updates | `vanilla-updates.timer` (OnBootSec 1h, every 1h) runs `vso upgrade`, which gates on `/etc/vso/config.json` (`schedule weekly`) and then runs `abroot upgrade`: pull, `rsync --delete --checksum` into the **inactive** root, chroot `grub-mkconfig` and `update-initramfs`, kernel into `init/<label>/`, `abroot.cfg`, a three-way /etc merge into the future upper, then the grub.cfg swap. A stop is requested by `/run/abroot/userStop`; `/run/abroot/operation.lock` while running | uupd on Bazzite |
| Install | the GUI writes a recipe and runs `sudo albius RECIPE.json`, which is non-interactive (setup, mount points, install by `oci` pull, post-install). `VANILLA_FAKE=1` prints the recipe without running it. No text or OEM installer | pikainstall's answers file |
| First boot | user `vanilla` (UID 1200) autologged into the `firstsetup` session; `vanilla-first-setup` (GUI) names the host, makes the real user and removes `vanilla`. openssh-server is in the image but `ssh.service` is disabled | |

**A fresh install's root B is empty.** Albius extracts the image into A only;
B gets `mkfs.btrfs`, and `init/vos-b/` stays empty until the first
`abroot upgrade`. The "Previous State (B)" entry then boots nothing. A dump
of such a system is faithful if it keeps B empty: the plan says so in a
note and doesn't treat it as damage.

## What works today, and what breaks

**`bko-holt dump`:**

- `osrel.rs` `SUPPORTED` has no `vanilla` row: dump refuses it
  (`ID_LIKE=debian` doesn't count). It needs `("vanilla", "3", "Vanilla OS
  3")` (Decision 1). *Check* that the version match takes `3.0` as major `3`, as
  `alpine`'s `3` does.
- **The present root is found, the rest isn't.** `/` is a btrfs mount of
  `/dev/mapper/vos--root-root--a` (a real block device, unlike
  composefs), so `storage.mounts` has `/`. The `/etc` overlay is dropped as
  a pseudo filesystem (`mounts.rs:67-70`), which is right. But the inactive
  root and `init` are in no mount and no fstab line, so nothing in the
  dump says they must be backed up. fstab does list both roots (*check*
  under which mount points: `/` is rewritten, but the other root's line?).
- **LVM is read, thin included.** The dump parses the VG's own metadata
  text (`probe/lvm.rs`), so the thin pool's segments (`thin-pool` with
  `metadata`, `pool`, `transaction_id`, `chunk_size`, `discards`,
  `zero_new_blocks`; each `thin` with `thin_pool`, `device_id`) come
  through in `storage.lvm` as they are. *Check* on the guest's dump.
- **btrfs on an LV:** generic, works (no subvolumes, so nothing to list).
- **No kernels.** `kernels_json` lists `/boot`'s top level; Vanilla's are
  in the unmounted `init` LV. `initramfs_json` says initramfs-tools, which
  is true, but `load` must not run it.
- **GRUB looks ordinary.** `detect_loader` finds GRUB via shim
  (`\EFI\vanilla\shimx64.efi`); `load` would run `grub-install` and
  `update-grub` (a stub that fails on Vanilla).
- **Works as is:** packages (`dpkg`, read-only but readable), crypttab
  (through the /etc overlay), efibootmgr, the cmdline.

**`bko-holt load`:**

- **The assemble stage refuses** VG `vos-root` (`thin-pool` segment,
  commands.rs:713). `--fit-largest` also refuses it (`fit.rs:946`), and
  should go on doing so in v1.
- **The mount stage** mounts fstab's tree: `/` (present root), `/boot`,
  `/boot/efi`. Not the other root, not `init`, and not `/var` either
  (mount-sys drops `/var` from fstab and mounts it by label).
- **The gate** needs `etc/fstab` (`restored_marker()`,
  `other/mod.rs:202`). The raw root's `etc/fstab` is the image's lower
  (*check* that it exists); the real fstab is in `/var`.
- **The boot stage** would chroot into the present root (which works: it
  has `/bin/sh`), run `update-initramfs` (writes `/boot/initrd.img-K` into
  `vos-boot`, where Vanilla never looks), `grub-install` (rewrites the
  ESP) and `update-grub` (fails). All harmful or useless.
- **NVRAM:** `efi_entries` recreates `vanilla` `\EFI\vanilla\shimx64.efi`.
  Right as is.

**rustic-bko** (the fork):

- Device nodes come back (overlay whiteouts are char 0:0). *Check* that
  `trusted.overlay.*` xattrs are backed up and restored: they need root and
  `CAP_SYS_ADMIN`, which the rescue image has, but the fork may filter
  xattr namespaces. Without them, opaque directories in the /etc uppers
  lose their opacity, and files deleted from /etc reappear after the
  restore. This is the one archiver risk (V1 settles it on the guest; a
  fork fix would be its own piece).
- No `+i`, no hardlinks between roots (each root is its own filesystem),
  no fs-verity. `security.ima` xattrs if IMA writes them (`lsm=integrity`
  on the cmdline): *check*; kept like any `security.*`.

**Guest tools, SticTest1, HoltTest1:**

- **vm-mount** finds `etc/fstab` at the root of a filesystem and mounts by
  fstab. On Vanilla it would mount root A, find the image's fstab, and miss
  `/var`, B and `init`. It also can't activate the thin pool unless the
  host has `thin-provisioning-tools` (*check* all four hosts: `thin_check`
  runs at activation).
- **vm-update** takes `has apt` and runs `apt full-upgrade` against a
  read-only root with apt locked (`lpkg --lock`). It needs an ABRoot branch.
- **compare.py** walks one tree; it needs the six filesystems.

## The design

### One tree convention: ABRoot's own mount points

Every tool that backs up or mounts a Vanilla system builds the same tree,
using the paths ABRoot itself uses during an upgrade:

| Path in the tree | Filesystem | Found by |
|---|---|---|
| `/` | the **present** root (`vos-a` or `vos-b`) | the device mounted at `/` (running), or the root that the master grub.cfg's `default` entry leads to (offline) |
| `/boot` | ext4 `vos-boot` | fstab |
| `/boot/efi` | ESP | fstab |
| `/boot/init` | ext4 `vos-init` (LV `vos-root/init`) | `thinInitVolume` in abroot.json; ABRoot mounts it here itself, and `/boot/init` is a directory on `vos-boot` |
| `/part-future` | the **other** root | the other of `partLabelA`/`partLabelB`; ABRoot mounts the future root here itself |
| `/var` | btrfs `vos-var` (on LUKS or not) | the mount at `/var` (running); `partLabelVar` when fstab has no `/var` (load), since mount-sys drops `/var` from fstab |

The tree is read **raw**: `/etc` is the present root's lower, not the
overlay, and `/var/lib/abroot/etc/*` holds both uppers. Restoring the raw
filesystems restores exactly the composed view after the next boot. Every
filesystem keeps its UUID and label (load already makes filesystems with
the source's UUID and label), and every LV keeps its name.

**Who builds it.** As for ostree (Bazzite's decision 4): on the running
source, `dump` writes `storage.backup_sources` with the two extra entries
(their `from` is a mount dump makes read-only under its run directory, or
none if the user's backup script mounts them: Decision 4). On a disk, load
and vm-mount derive the same tree from the layout and the restored
filesystems. A corpus test ties the two: for every ABRoot corpus dump,
`backup_sources`' paths equal the mount points load plans.

### Detection

ABRoot is recognised by its own markers, not by os-release:
`/usr/share/abroot/abroot.json` (or `/etc/abroot/abroot.json`) on the
running root. `boot.abroot` is then written whatever the layout, and its
`thin_two_roots` says whether it is the scheme version 1 restores:
`thinProvisioning` true, a filesystem labelled `thinInitVolume`, and two
labelled `partLabelA` and `partLabelB` (each found by label, first match,
as ABRoot does; an LV's pool layers are left out). Load refuses the rest.
`boot.abroot` is absent on every other system, so nothing else changes.
The two files are merged as viper does: keys matched whatever their
case, the later file's and the later key's value winning (the
installer's `/etc` copy repeats the image's keys, `thinProvisioning:
false` first and `true` after).

### `dump`

- `boot.abroot`, a new section (`holt/src/abroot.rs`): abroot.json's merged
  keys (labels, `thinProvisioning`, `thinInitVolume`, `PartCryptVar`); the
  present root's label (the device at `/`) and the future's; the master
  grub.cfg's `default` and its two entries' text (`Current State (A)` and
  so on); for each of `vos-a` and `vos-b`, what `init/<label>/` holds (the
  kernels, initramfs, `abroot.cfg`'s `linux` line and `root=`), and the
  root's `/abimage.abr` (digest, time), or `empty: true` when the root
  has no `/usr`; the kargs file; whether `/run/abroot/operation.lock`
  exists (a backup taken during an upgrade). `init` and the other root are
  read through a read-only mount in dump's run directory
  (`mount -t TYPE -o ro,EXTRA,nodev,nosuid,noexec NODE /run/bko-holt/abroot-LABEL`
  through `Sys`, then `umount` and the directory removed, as for
  Proxmox's unmounted ESPs); one already mounted is read where it is.
  EXTRA (`abroot::ro_options`) is `noload` on ext3/ext4 and
  `rescue=nologreplay` on btrfs (Linux 5.9+; the bare `nologreplay` is its
  deprecated alias; both need `ro`), since `ro` alone still replays a
  dirty journal or log tree, a write to the source disk after an unclean
  shutdown; other types get plain `ro`. The lock is looked at **before**
  any mount: with `/run/abroot/operation.lock` there, dump mounts nothing
  of its own (a running `abroot upgrade` would find the init LV or the
  other root busy), warns, and records `mounts_skipped: true`; load
  refuses such a dump anyway. In each `abroot.cfg`, dump and the check
  read the first `linux` line whose kernel is in `/LABEL/` (with or
  without a `(device)` prefix), the first `initrd` after it, and the last
  `search --fs-uuid` before it in its block: an upgrade's abroot.cfg is a
  whole generated GRUB config whose 00_header can search for its font's
  filesystem first.
  As built, the JSON is: `config` (the merged keys and the `files` read),
  `thin_two_roots`, `v1_system` (`/.system` exists), `present` and
  `future` (labels), `grub` (`default`, `default_label` = the label the
  default entry's configfile leads to, `entries` with `title`, `class`,
  `root`, `configfile`, `label`, and `future_cfg`), `init` and `var`
  (label, device, uuid, fstype, dm_name, mounted_at; init's `read`), and
  `roots`: per label the same plus `present`, `read`, `abimage` (digest,
  timestamp, image, or null), `empty` (no `usr/`; null when unread) and
  `init` (`exists`, `files`, `kernels`, `abroot_cfg`: `linux`, `kernel`,
  `initrd`, `root`, `search_uuid`); `kargs`; `operation_lock`;
  `mounts_skipped`.
- `storage.backup_sources`: the six entries above. `/`, `/boot`,
  `/boot/efi` and `/var` come from the running mounts as today (`/var` is
  mounted though fstab doesn't list it). `/boot/init` and `/part-future`
  aren't mounted, so their entry has `"from": null` and a `mount` object,
  `{"device": "/dev/mapper/vos--root-init", "fstype": "ext4", "options":
  "ro,noload,nodev,nosuid,noexec"}` (the other root's
  `ro,rescue=nologreplay,...`), the mount a backup makes at that path before
  it reads the tree (and undoes after), with a `note` saying so; an entry
  for a filesystem that is mounted (ABRoot's own mount of the future root
  at `/part-future` during an upgrade) is the running mount instead. The
  `/` entry's `note` says its `/etc` is the overlay, so a backup reads `/`
  through a non-recursive bind mount (`mount --bind / DIR`) to get the raw
  lower `/etc` that load restores. `mount` and `note` are absent on every
  other system's entries, and `from` is a string there as before.
- `kernels_json`: also `init/<label>/vmlinuz-*` per root, marked with
  the root's label (`dir` `/boot/init/<label>`, `root_label`); the other
  root's `modules_installed` is null (its mount is gone by then).
- The loader: `grub` via shim, as today; the presence of `boot.abroot`
  is what makes load take the ABRoot path (no `abroot` flag on the loader).
- `redact.rs`: `vos-*` labels and the VG names are generic words; the
  hostname `vanilla` too.

### Thin pools in `load` (generic)

v1 of thin support, for any system:

1. `vg_metadata` writes the VG without its thin pool, the pool's hidden
   `_tdata`/`_tmeta` LVs, `lvol0_pmspare` and the thin LVs, so
   `vgcfgrestore` restores the PVs (their UUIDs), the VG (its UUID) and
   the linear and striped LVs (with their UUIDs) as today.
2. Then, for each pool, `lvcreate --type thin-pool -n POOL -L DATA
   --poolmetadatasize META --chunksize CHUNK --discards D --zero Z VG`,
   with the dump's sizes, and for each thin LV `lvcreate --type thin -V
   VSIZE -n NAME --thinpool POOL VG`, in the dump's `device_id` order. All
   with `--devices` as the other LVM commands, and before anything formats.
3. Plan problems: a thin LV that is a snapshot of another (`origin`), an
   external origin, a pool on RAID or cached, or a pool whose
   used data can't fit (a dump's used size above the pool's): refused in v1.
   `--fit-largest` keeps refusing thin pools.

The pool's and thin LVs' **LV UUIDs** and device ids are new. Nothing on
Vanilla names them: GRUB finds LVs by name (`lvm/vos--root-init`,
`init` keeps its UUID anyway), the kernel by fs UUID or `/dev/mapper`
name, ABRoot by label. *Check* in V1 that nothing else does (grep the
installed tree for the dump's LV UUIDs). Decision 3 has the alternative
that keeps them.

### `load`

- **Assemble:** the thin pools above.
- **Mount stage:** fstab's tree, then `boot/init` and `part-future`
  from `boot.abroot` (the mount points exist on the restored `vos-boot`
  and present root only after the restore, so they are made with
  `mkdir -p` first, which leaves the same empty directories ABRoot has),
  and `/var` from `boot.abroot.var` when fstab has none (mount-sys drops
  it), all with `-o defaults`, in the usual depth order.
- **Gate and status:** on ABRoot, `restored_marker()` returns
  `abimage.abr` (on the present root), or `var/lib/abroot` when the present
  root's `/abimage.abr` didn't exist in the dump (*check* that a fresh
  install has it).
- **The boot stage**, an early `if self.abroot { return self.abroot_boot(); }`
  beside ostree's, in the same order:
  1. The atimes listed `if-missing` first.
  2. A note: `/boot`, the ESP, `init` and both roots are restored as they
     were; no initramfs, no `grub-install`, no `update-grub`, no chroot.
  3. A consistency check, as a shell test the plan shows: the master
     grub.cfg has `default=0` or `1` and two `configfile` lines; each
     entry's `init/<label>/abroot.cfg` exists unless the dump said that
     root is empty; each `abroot.cfg`'s `linux` and `initrd` files exist in
     `init/<label>/`; its `root=` names **that root's own** filesystem
     (`UUID=`, `/dev/mapper/` or `/dev/VG/LV`, stricter than "one of the
     layout's"); a `search --fs-uuid` in it names the init LV's UUID; each
     `set root=(lvm/...)` names the init LV; and the `default` entry leads
     to the root the dump's own grub.cfg led to (`grub.default_label`).
     That is usually the present root; when it isn't (an upgrade finished
     and not yet rebooted into), the plan has a note and the restored
     system boots the new root first, as the source would have. A
     mismatch fails the stage (a backup taken across an upgrade's
     grub.cfg swap shows here). An empty root is a `warning:` line on the
     terminal, and an error only if the default entry leads to it; a dump
     whose own grub.cfg default leads to an empty root is already a plan
     problem, so the load fails before partitioning.
  4. `efibootmgr` entries as today, unless `--no-nvram`.
  5. The log into `{t}/var/log/bko-holt` (the `vos-var` filesystem).
  6. The generic tail (atimes put back, read-only subvolumes; none here).
- **Plan problems on ABRoot, v1:** thin snapshots (above); an
  `operation.lock` in the dump (taken during an upgrade); BIOS; a layout
  that isn't ABRoot's two-label scheme (manual partitioning without thin,
  or ABRoot v1's `/.system`); `--new-uuids`; and, when the target system
  already holds a VG named `vos-root` or `vos-var` (the existing
  target.rs refusal, which covers it). The LUKS `/var` is the generic LUKS
  path (crypttab, `luksFormat --uuid`), which already exists; a crypttab
  key file other than `none` is refused (the ABRoot boot stage doesn't put
  key files back). `--new-uuids` is refused by the generic refusal that
  every layout gets. Also refused: a dump with no master grub.cfg default
  or no present root.

## Guests

**`vanilla3`**, the installer's default layout, Secure Boot on (shim and
Debian's signed GRUB; `vm-run-options`: `--uefi --memory 4096`), disk
**64G** sparse (the installer's minimum is 50 GiB), from the 3.0 ISO with
its SHA-256 pinned in the script and the ISO in
`/bkoscratch/rebuild-inputs/vanilla3/` on Chopin and Cinqcent.

How it's built (Decision 2):

1. **Albius from the live ISO, unattended** (recommended): a new
   `testvm/make-vanilla.sh FLAVOR ISO WORK_DIR` remasters the ISO's GRUB
   config with xorriso (adds `console=ttyS0,115200n8`, drops `quiet
   splash`), boots it under QEMU with OVMF and `-cpu host`, logs in on the
   serial console as `vanilla` (empty password, sudo), and runs `sudo
   albius /media/answers/recipe.json` from a second CD. The recipe is the
   one `processor.py` makes for the auto layout, captured once with
   `VANILLA_FAKE=1` (*check* that the live GUI prints it from a shell
   without a display; else it is written by hand from `processor.py`'s
   steps and diffed against the GUI's in V1), with disk `/dev/vda`, user
   `vanilla`, and no encryption. The install pulls the OCI image over
   QEMU's user network. Our own post-install steps, in the recipe's
   `postInstallation` as chroot shell steps: `ssh.service` enabled, root's
   SSH key and generated password, a `tester` user,
   `console=ttyS0,115200n8` in `/etc/abroot/kargs` and in
   `init/vos-a/abroot.cfg`, `terminal serial console` in the master
   grub.cfg (so HoltTest1 can pick an entry), the first-setup session
   skipped (*check* how: remove `vanilla-first-setup` autologin from GDM's
   config in the upper, or let it run headless), boot to
   `multi-user.target`. Prints BACKOTTER-INSTALL-OK.
2. **The fallback through `make-from-live.sh`**: a `vanilla` family that
   unsquashes the live system on the build host and runs its `albius`
   binary in a chroot against a loop-attached disk (with host network for
   the image pull, and the host's LVM kept off it with `--devices`).
   Nothing to log into over serial, but it runs the live image's code on
   the host, and the host's `lvm2` sees the guest's VGs while it runs.
   est. +0.5 d.
3. **By hand once in the GUI**, kept as a pinned image: not rebuildable.

**Both roots in use.** After the install, `make-vanilla.sh` boots the guest,
writes `/etc/bko-root` with `a` (into vos-a's upper), and runs `abroot
upgrade` (with `--force` if there is no newer image: *check* the flag),
which fills `vos-b`, builds its kernel and initramfs into `init/vos-b/`,
and swaps grub.cfg. After a reboot into B it writes `/etc/bko-root` with
`b` (into vos-b's upper) and powers off. So B is present (`default=1`?
*check* which the swap writes), A is previous, both boot, and each has its
own /etc. The `fresh-install` snapshot is taken then. Every later
vm-update flips the roots again, which is fine: HoltTest1 asks the guest
which is present.

**Room** (est., *check* in V1): each root ~4-6 GiB (GNOME image),
`/var/lib/abroot/storage` another ~4-6 GiB (the cached image), `init`
~0.3 GiB: ~12-18 GiB written, qcow2 about the same. HoltTest1's D would
then be ~25-35 GiB, too big for a shared 30G RAM disk. Decision 5 decides
whether `/var/lib/abroot/storage` is kept (faithful) or left out of the
backup (re-pulled at the next upgrade). V7 measures and sets
`ram-sizes.tsv`; if it doesn't fit, its runs go to `/bkoscratch` with
`cmp-run.sh`'s disk reservation, as `bazzite44` would have.

**`vanilla3-luks`** (Decision 6, optional, est. +0.5 d): the installer's
encryption option, LUKS2 on `vos-var/var`. The passphrase is asked by
`abroot unlock-var` through Plymouth from the init wrapper, not from the
initramfs: *check* whether it reaches the serial console, so
`console-boot.py` can type it ([[front-load-manual-tests]] puts its runs
first).

Each guest gets: `rebuild-one.sh` lines and its ISO check, the rebuild
inputs on Chopin and Cinqcent, `ram-sizes.tsv` rows, both hosts through
`vm-store` publish, a GuestTest1 grade. Only Chopin and Cinqcent: the
guest needs `thin-provisioning-tools` on the host for vm-mount (*check*
whether cage and copland have it; they could hold it too, since it needs no
OpenZFS).

**Guest tools:**

- **vm-mount, ABRoot mode:** when the disk has a VG with an `init` LV and
  two filesystems labelled from abroot.json's keys (read from `vos-a`'s
  `/usr/share/abroot/abroot.json`), activate it (`--devices`, thin
  checked), read the master grub.cfg's default, and mount the six-path
  tree at MP. vm-chroot chroots into `/` with `/etc` as the overlay the
  guest would see (an overlay mount of `/etc` over MP's etc with the
  present root's upper), so commands see the real config. HoltTest1's
  backup and compare.py follow.
- **vm-update, an ABRoot branch** before `has apt`: hold nothing, run
  `abroot upgrade` (an `apt` run would fail), the clean reboot vm-update
  does, then `qcow2-punch.py`. `fingerprint.py`'s `LINUX_KINDS`,
  `pkgset.sh` (dpkg reads the present root) and vm-update's header name
  it. GuestTest2: a `K-abroot` rule in `t2lib/guest_judge.py` (kernels
  in `init/<label>/`, one per root).

## Tests

**SticTest1** (`testvm/stictest1/vanilla3`): one `inner.sh`,
`fixture.sh` and `judge.py`. The scratch disk is laid out like the guest:
a VG with a thin pool and two over-provisioned thin LVs (btrfs), plus a
linear ext4 LV; a tree with an `/etc` overlay upper in a `var` volume
holding a whiteout (char 0:0), an opaque directory
(`trusted.overlay.opaque=y`) and a redirect, and a cached containers
storage layer. Checks: both thin LVs' files, the whiteout and its
`trusted.overlay.*` xattrs, the opaque directory, labels and UUIDs; both
archivers, both hosts. What restic does with `trusted.*` is recorded, not
fixed ([[cross-tool-compat-courtesy]]). `stic/manifests.json`,
`limits.json`, and `check-manifests.py`'s count up by one (the sibling
sessions add theirs: whoever lands last sets the number).

**HoltTest1** (Decision 7: **1Obscure**): ht1.sh backs up the guest through
vm-mount's tree, loads it onto a blank disk, and boots the restored disk
alone, **twice**: the default entry, then the other one picked on GRUB's
serial menu (by `console-boot.py`, a down-arrow and Enter at the menu: the
master grub.cfg reads `default` literally, so `grub-reboot` would do
nothing, as on Bazzite). Each boot must report, over SSH, the root label
`abroot status` names as present, that root's `/abimage.abr` digest,
`/etc/bko-root` (`a` or `b`), and `/proc/cmdline`, all equal to the
source's for that root; and `abroot status` must call the other root
"previous", with no integrity repair logged. judge.py allows
`var/log/bko-holt` and what each boot writes in `/var` (the journal);
it **requires** the master grub.cfg, `grub.cfg.future` if any, both
`abroot.cfg`s, the `init` LV's kernels and initramfs, and the ESP's
files to be byte-identical to the source's: the test that nothing was
regenerated. The two boots don't write grub.cfg (only an upgrade or
`abroot rollback` does).

**Test2:** golden plan in `holt/tests/corpus/vanilla3/` (`src.json`,
`plan.txt`, its `index.tsv` row, the corpus README), from `bko-holt dump`
of the guest (it shows the thin `lvcreate` lines and no `grub-install`);
`classes.json` (`holt.families.Obscure`, the `uefi` list), `holt/spec.json`,
`guest/spec.json`, `estimates.tsv`, `t2lib/plan.py`'s source and guest
counts (with README and tests), `tests/fixtures/p2/world.py`'s `GUESTS`,
and the re-blessed p2 goldens. Like PikaOS's, the registration goes into
the sibling sessions' **one batched Test2 pass**, after the uncommitted
Test2 work in the tree lands. Done when two corpus builds have the same
sha256, leak scans are 0 on both hosts and the Mac, no other plan changes,
and Test2's own tests pass. A thin-pool scenario in `scenarios.rs` and
`fit.rs`' tests come with V3.

**BackShot1:** not proposed. The Thinkpad would need a reinstall, and the
guest covers the layout; BackShot1's `make_tree` would need the six-path
tree from `backup_sources`, as for Bazzite. A later piece if the user
wants it.

## Shared lines (with the other new-distro sessions)

Per the shared notes folder: `osrel.rs` `SUPPORTED` gets its own row
(the second to land rebases, and appends to `supported_names`' assert
string, never overwrites it); `redact.rs`'s word lists edited once,
re-read first; `classes.json`, the holttest1d README's 1Obscure row and
`check-manifests.py`'s count one session at a time. **The thin-pool piece
(V3) is the one most likely to overlap**: the Proxmox session's default
layout has thin pool `pve/data`. Before V3 starts, a note goes into the
shared folder so only one session writes `vg_metadata`'s thin support,
and the other reuses it. Vanilla doesn't touch `update_grub()`'s list.

## What V1 found

2026-11-14, from the guest `vanilla3` (`testvm/make-vanilla.sh`, with
`testvm/vanilla/{install.sh,probe.sh,console-keys.py}`), built once on
Cinqcent (Chopin's RAM disk was taken; the same script, the same ISO),
copied to Chopin: qcow2 SHA-256
`de1bf22b5b4324eb7e2a8e533bc3a63d26e0406d9aa5853dec39eae185e5ee0f` on both, in
`~/backotter-work/vms/vanilla3`. It boots alone into both roots on both
hosts, Secure Boot on, SSH as root. The build's own record is the guest's
`first-boot.txt`, `install.log` and `recipe.json`.

**How it's built (Decision 2, path 1, changed in one place).** The live
system isn't driven over its serial console: it is given a systemd unit
as an SMBIOS credential (`systemd.extra-unit.bko-install.service`; the live
systemd is 262) and `systemd.wants=` on the directly booted live kernel's
command line (the ISO's GRUB options less `quiet splash findiso=`, plus the
serial console, `multi-user.target` and `noprompt`). The unit mounts a
second CD (BKOINST) and runs `install.sh` as root, its output on the second
serial port. No remastered ISO, no login. live-boot's shutdown still waits
for Enter ("Please remove the live-medium"), `noprompt` or not, so the
unit ends with `systemctl poweroff -ff` (the target is unmounted first).

- **The recipe** is the installer's own: the ISO's `processor.py`
  (vanilla-installer 2.4.0, albius 0.3.2) imports with its real
  `core.disks` and `core.system` (no GTK), and `gen_install_recipe` is
  called with the pages' `get_finals()` answers (auto `/dev/vda`, no
  encryption, en_US.UTF-8, us/pc105, Etc/UTC, no VM tools, the default
  image). `VANILLA_FAKE` isn't needed. 20 installer steps plus this guest's
  two (a copy of `guest.sh`, then it in the chroot); no hand-written steps.
  The image pull is 98 layers, ~10 min.
- **os-release:** `/usr/lib/os-release` is base-files' as the spec says
  (`ID=vanilla`, `VERSION_ID="3.0"`, `VERSION="3.0 Reunion"`), and each
  root's `/etc/os-release` is a symlink to it. The **live** system's
  `/etc/os-release` is live-build's (`ID=ubuntu`, `VERSION_ID="3"`), which
  `install.sh` allows for. `/etc/issue` still says "vanilla-os 2.0".
  Orchid's downloads weren't looked at (Decision 1 settled it).
- **ABRoot is v2.6.0** in the image. `/etc/abroot/abroot.json` has keys
  v2.6.0's `config/abroot.json` lacks (`autoRepair: true`,
  `libPathStates`, `iPkgMngStatus: 2`, image `vanilla-os/gnome:latest`),
  plus the installer's three.
- **Layout, as the spec says:** GPT vda1-4 at the sizes given; VG
  `vos-root` 22.1 GiB with 100 MiB free, VG `vos-var` 40.4 GiB. The thin
  pool `root`: 19 GiB data, 1 GiB `root_tmeta`, `lvol0_pmspare` 1 GiB
  (present), **chunk 64 KiB, discards passdown, zero on**, transaction_id
  2; `root-a` thin id 1, `root-b` thin id 2, 19 GiB virtual each. Pool data
  65 % after the upgrade (each root ~6 GiB). Btrfs on both roots and /var
  mounted `discard=async`. That a dump carries these is V2's to show.
- **fstab:** the root's **lower** `/etc/fstab` (Albius' `GenFstab`)
  exists, so the gate's `etc/fstab` is there; it lists `/boot`,
  `/boot/efi`, **both roots under `/`** and `/var`. The live one, in the
  present root's upper, is `mount-sys`'s at every boot:
  `UUID=<present> / btrfs ro,defaults 0 0` and `/boot`, `/boot/efi`
  only: **no `/var` and no other root** (mount-sys mounts `/var` by
  label). The upper has no `fstab` until the first boot (plus an
  `fstab.old`).
- **`/abimage.abr`:** on vos-a right after the install (digest
  `sha256:f1be77e7...414c`), and on vos-b after the upgrade (the same
  digest, the upgrade's time). Each root also has `/part-future`, `.local/`
  and an `/etc/ld.so.preload`.
- **The upgrade:** `abroot upgrade --force` (v2.6.0 has `--force`,
  `--dry-run`, `--check-only`, `--delete-old-system`) filled B from the
  cached image in ~2 min, no new pull (the tag hadn't moved). The swap
  writes **`set default=1`** and keeps the entries' order: entry 0 is now
  "Previous State (A)", entry 1 "Current State (B)"; `grub.cfg.future` is
  the old file (`default=0`, "Current State (A)"). Booting entry 0 writes
  nothing. `grub.cfg.future` is made from the current grub.cfg by text
  replacement, so this guest's serial lines carry over.
- **The two `abroot.cfg`s differ.** vos-a's is the installer's four lines:
  `linux (lvm/vos--root-init)/vos-a/vmlinuz-7.3.3+deb14-amd64
  root=UUID=<vos-a fs UUID> ... lsm=integrity`. vos-b's is the future
  root's whole `grub-mkconfig` output with ABRoot's lines in it: a second
  menu (timeout 0, `terminal_output gfxterm`, one entry "Vanilla OS")
  with `search --fs-uuid --set=root <vos-init UUID>`, `linux /vos-b/...
  root=/dev/mapper/vos--root-root--b` and `/etc/abroot/kargs`. ABRoot's
  `DefaultKargs` has no `lsm=integrity`, so B's command line lacks it:
  upstream's behaviour, kept. The kargs file did reach B.
- **`abroot status` says Present and Future, never "previous".** Booted
  into B: present vos-b, future vos-a. Booted into A by "Previous State
  (A)": present **vos-a**, future vos-b, with the ✓ on the future (the
  root GRUB's default boots). So HoltTest1 (V7) checks "present" against
  the booted root and the ✓ against the default, not a "previous".
- **The `/etc` merge** of the upgrade copied vos-a's upper changes into
  vos-b's (`/etc/bko-root` "a", the timer's mask), as it should.
- **Overlay xattrs and whiteouts** (python over `/var/lib/abroot`; the
  image has no `getfattr`): the `/etc` uppers hold `trusted.overlay.origin`
  (48), `.impure` (12) and `.uuid` (2), and **no whiteouts, no opaque
  directory, no redirect** on a fresh install: those come only when a user
  deletes from `/etc` or replaces a directory. `origin` is a file handle of
  the lower btrfs (its fs UUID and inode), which a restore can't keep
  valid; overlayfs without `index` ignores a stale origin. The storage
  holds **6948 whiteouts (char 0:0)**, 8 real device nodes in a layer's
  `/dev`, one `security.capability`, and `trusted.overlay.*` on one layer
  `diff` (left by Albius mounting the image). No `user.overlay.*`. **Whether
  rustic-bko keeps `trusted.overlay.*` is still open**: that is SticTest1's
  (V6), and a real opaque directory has to be made there by hand.
- **IMA:** the kernel's LSMs include `ima,evm`, but no `security.ima`
  xattrs were found on `/usr/bin`, `/usr/sbin` or `/etc` entries.
- **LV UUIDs named anywhere:** only by LVM's own `/etc/lvm/backup` and
  `archive` (written at the first boot, so in vos-a's upper). Not by the
  master grub.cfg, either `abroot.cfg`, abroot.json, fstab or
  `/abimage.abr`. Decision 3's recommended way holds. The restored
  system's LVM backups would name the old thin UUIDs: stale but harmless.
- **Boot:** EFI/vanilla holds shimx64, grubx64, mmx64, fbx64,
  BOOTX64.CSV and grub.cfg; Albius' NVRAM entry `vanilla`
  `\EFI\vanilla\shimx64.efi` is in the install's `uefi-vars.fd` (Secure
  Boot off there: the live kernel boots directly). The installer makes no
  `EFI/BOOT`, so the guest adds one (shim as BOOTX64.EFI with grubx64,
  mmx64 and grub.cfg, no fbx64). Under Secure Boot shim, Debian's GRUB and
  the 7.3.3+deb14 kernel boot. GRUB's serial console needed only
  `serial`/`terminal_*` lines in the master grub.cfg.
- **First setup:** with `multi-user.target` the default, GDM and the
  first-setup session never start. The installer's user `vanilla` (UID
  1200) gets the password `vanilla` from the recipe (not empty, as the
  live user's is).
- `systemd-networkd-wait-online.service` failed on some boots (the system
  is "degraded"); not on others.
- **Host tools:** `thin-provisioning-tools` 1.1.0 is installed on all four
  hosts (`thin_check` in `/usr/sbin`).
- **Room:** root A 5.9 GiB, root B 6.0 GiB, `/var` 7.8 GiB (7.6 GiB of
  it the cached image), `init` 226 MiB, `/boot` 15 MiB, the ESP 9.6 MiB.
  The qcow2 is 20.9 GiB on disk (64 GiB virtual), **more than the spec's
  12-18 GiB**; the build's peak in the RAM disk 21.5 GiB; the build took
  1031 s (install 523 s, three boots and the upgrade 508 s). A HoltTest1
  run holds a backup and a restored disk of about that size each, so it
  doesn't fit a 30 GiB RAM disk: V7 runs from `/bkoscratch` unless
  Decision 5 changes.
- **Not done in V1:** `vm-update --try` (vm-update has no ABRoot branch
  until V5: it would run apt on the read-only root); `vanilla3-luks`'s
  prompt (V10); the guest isn't in vm-store yet.

## What V5 found

2026-11-14, on Chopin, from `vanilla3` (throwaway `--snapshot` boots and
read-only mounts; the dumps, plans and redacted `report-bug` files are in
`~/backotter-work/vanilla-wt/v5/` there, not in the repo).

- **A thin pool is never a read-only table.** The kernel refuses it
  ("thin-pool: target type may not be included in a read-only table"),
  and its data and metadata need writable devices, so `vm-mount --ro`'s
  `read_only_volume_list` can't activate `vos-root` (nor could
  `qemu-nbd --read-only`'s devices). With `--ro` a thin group is activated
  from device-mapper snapshots of its PVs (the RAID path's throwaway COW),
  so nothing reaches the image; the filesystems are mounted `ro,noload` and
  `ro,rescue=nologreplay` as before. Without `--ro` LVM activates it as it
  did.
- **The host's event activation is real**: Chopin's LVM activated
  `vos--var-var` on the nbd device by itself. vm-mount's "is that group
  mine" test followed `dmsetup deps` one level, which for a thin LV is the
  pool, so it took its own group for another host's and refused; it now
  walks the dm stack to the PVs (and reads all of its output: `grep -q`
  under `pipefail` broke the pipe). A second mount of a Vanilla disk is
  refused, the first left as it was.
- **No apt, dpkg or dpkg-query on the root** (`lpkg` locks them away), so
  pkgset.sh reads the status file. Root B (written by `abroot upgrade`)
  has its own `var/lib/dpkg/status` under the `/var` mount point; root A
  (the installer's) has none, only the install's copy on `vos-var`, which
  is read there. 1697 packages installed.
- **`abroot upgrade` with nothing newer exits 1** ("No update available"),
  and it **reads stdin**: run under `ssh sh -s`, it ate the rest of the
  script. in-guest.sh gives it `/dev/null`.
- **The dumps** (B booted by default, A by "Previous State (A)"): exit 0,
  no warnings, osrel accepted (`vanilla` `3.0`). `boot.abroot`: present
  `vos-b` (A: `vos-a`), future the other, `grub.default` `1` and
  `default_label` `vos-b` in both, `future_cfg` true, both entries with
  `(lvm/vos--root-init)` and their configfiles; both roots `read`, not
  empty, kernel `7.3.3+deb14-amd64` and its five files each, A's
  `abroot.cfg` the installer's (`root=UUID=...`, `lsm=integrity`, no
  search), B's grub-mkconfig's (`search_uuid` the init LV's,
  `root=/dev/mapper/vos--root-root--b`); both `abimage` digests
  `sha256:f1be77e7...414c` (A 06:02, B 06:13); `config.files` both
  abroot.json files. `storage.backup_sources`: the six, `/boot/init` and
  `/part-future` with `from: null` and the `ro,noload,...` and
  `ro,rescue=nologreplay,...` hints. `storage.lvm`: the pool's thin-pool
  segment (chunk 128 sectors, passdown, zero, transaction 2). The
  kernels: both roots', the other's `modules_installed` null. Dump left no
  mount behind; dmesg shows only "ro without journal" (ext4) and
  "disabling log replay" (btrfs), no replay.
- **The plan in the running guest has 3 problems, all of the place**:
  `vos-root` and `vos-var` on the source disk (target.rs's refusal, as the
  spec wants) and no `sfdisk` on Vanilla. In HoltTest1's UEFI rescuer (it
  has `thin_check`, the thin segtypes and dm-thin-pool) both dumps plan
  with **no problems**: vgcfgrestore of `init` and `var`, the
  `thin-pool` and two `thin` lvcreate lines (64k chunks, passdown), every
  filesystem with its label and UUID, the six mounts, and a boot stage
  of atimes, the check script, the log and the tail: no grub-install,
  initramfs, update-grub or chroot.
- **Fixed in holt:** A's plan notes that grub.cfg boots vos-b next, not
  vos-a, "an upgrade had finished and not been rebooted into"; booted by
  hand from GRUB's menu it is a rollback, so the note now says either.
- **Open, low:** the btrfs allocation profiles come from
  `/sys/fs/btrfs/UUID/allocation`, there only while mounted, so the
  future root (always unmounted on the running system) is planned
  without `-d`/`-m`: mkfs.btrfs's defaults (single data, DUP metadata on
  one device since btrfs-progs 5.15) are what the source has, but they are
  the rescuer's, not the dump's. abroot::read has the root mounted and
  could record them.
- `vm-update --try vanilla3`: ok, "no newer image, nothing changed";
  nothing to merge, so its read-only vm-mount of the updated disk didn't
  run (vm-mount `--ro` and read-write were run by hand: both trees, then
  vm-chroot, vm-umount leaving no dm device). An update that does pull an
  image waits for the tag to move.

## What V7 found

2026-11-14, HoltTest1 of `vanilla3` on Chopin (`v7-1`) and Cinqcent
(`v7-2`), with the UEFI rescuer (Ubuntu 26.04, lvm2 2.03).

- **The two-root boot works as V1 said.**  `abroot status --json`'s
  `present` is the booted root; the ✓ is on vos-b in both boots (GRUB's
  default); B's command line is `root=/dev/mapper/vos--root-root--b`
  without `lsm=integrity`, A's `root=UUID=...` with it; `/abimage.abr`
  the same digest in both roots.  console-keys.py's Up, Up, Enter at
  "Previous State (A)" picked A on all four tries.  No boot changed
  grub.cfg or grub.cfg.future (the restored boots' sha256 are the
  source's).  GRUB rewrites `/boot/grub/grubenv` at boot (its mtime moves):
  not held.
- **mount-sys logs to the console only**: it runs before systemd and
  before /var is mounted, so neither the journal nor `/var/log/abroot.log.*`
  has it.  Its output in each boot: the config warning, the /var and
  /etc overlay mounts, the fstab rewrite (it writes the present root's
  `/` line into the upper's fstab at every boot) and its success line; no
  repair on any boot, source or restored.
- **Two load bugs in V3's thin pools, fixed in holt:** lvm2 2.03's
  `lvcreate --type thin-pool` refuses `--errorwhenfull` ("unsupported with
  thin pools"), and `lvchange --errorwhenfull n` on a pool already so fails
  ("already unset"); so lvcreate no longer gets it, and only a source pool
  with ERROR_WHEN_FULL gets `lvchange --errorwhenfull y` after it.  The
  Proxmox session's `pve/data` plans take the same lines.
- **The dump check needed two rules, both generic:** the restored machine
  can activate its groups in another order (Cinqcent's came up vos-var
  first: dm-0 and dm-1 swapped), so judge.py compares device-mapper devices
  by name when the two dumps number the same ones differently; and the thin
  pool, its `_tdata`/`_tmeta`, `lvol0_pmspare` and the thin LVs are made by
  lvcreate, so their LV UUIDs, creation host and time, their order in the
  metadata and where lvcreate put the hidden LVs on the PV are its own.
  Sizes, segments, chunk size, discards, transaction and device ids are
  compared and equal; vgcfgrestore's LVs (`init`, `var`) keep their UUIDs.
- **The files:** 0 differences but load's log; the 24 held paths (grub.cfg,
  grub.cfg.future, both `init/vos-*/` sets, EFI/vanilla, EFI/BOOT)
  byte-identical.  The overlay uppers' `trusted.overlay.*` (51 `origin`,
  14 `impure`, 4 `uuid` in the source's record) came back with rustic-bko
  as they were.
- **The V5 btrfs-profiles finding, fixed:** abroot::read reads the future
  root's `/sys/fs/btrfs/UUID/allocation` while its own mount is there;
  storage.btrfs takes it for a filesystem sysfs no longer shows.  The live
  dumps have `single`/`dup`/`dup` for vos-a (unmounted) as for vos-b, so the
  plan's mkfs.btrfs gets `-d single -m dup` for both.
- `systemd-networkd-wait-online.service` failed on every source boot of
  these runs and on no restored boot: ht1.sh's `FLAKY_UNITS` leaves it out.
- **Room:** the peak was 27.0 GiB on /bkoscratch (Cinqcent 26.9), in
  rebuild; the source phase 3 boots in ~5 min, backup ~2.5 min (18.7 GiB in
  494,348 files, 3.2 GiB added to the repository), rebuild 7-10 min, verify 6-8 min. D 30 is over the RAM-disk
  ceiling (29), as V1 expected.
- **Dumps for V8:** `/bkoscratch/holttest1d/v7-1/vanilla3/dumps/src.json`
  (Chopin) and `/bkoscratch/holttest1d/v7-2/vanilla3/dumps/src.json`
  (Cinqcent), copies in `~/backotter-work/vanilla-wt/v7/v7-1/` and `v7-2/`;
  made by `323e4069c953` (this tree's dump code), B booted.

## What V6 found

2026-11-14, SticTest1 `vanilla3` (`testvm/stictest1/vanilla3`), run from
`~/backotter-work/vanilla-wt/` on Chopin and Cinqcent with
`roundt2r-aa72d65-3bb01f1-99b9b56` (restic aa72d65, rustic_core 3bb01f1,
rustic 99b9b56).  Logs in `~/backotter-work/stictest1/vanilla3/logs/`
there.

- **Results: 46 PASS, 5 LIMIT, 0 FAIL on both hosts** (Chopin
  `v6b-20261114-090804`, 1400 s, its scratch on `/bkoscratch` through the
  check's `STC_SCRATCH_DIR` because Chopin's RAM disk was taken; Cinqcent
  `v6b-20261114-092119`, 1100 s, in the RAM disk).  The run's scratch peaked
  at 2.3 GiB (QEMU's `--snapshot` overlay of the guest and both spare
  disks), under the check's `--reserve 6 6`.  The LIMITs: birth times,
  project IDs (btrfs, and ext4 without the feature), symlinks' atimes on
  relatime, whole-disk needs, and the hard-linked whiteouts below; all in
  `limits.json`.
- **`trusted.overlay.*`: both archivers keep all of it.**  In all four
  directions and both restores over existing: the fixture's /etc uppers
  (made by overlayfs itself: 6 whiteouts; origin x7, impure x3, opaque x3,
  redirect x1, uuid x2), the guest's own uppers `vos-a`/`vos-b` with their
  workdirs (50 entries with origin, impure, uuid), and containers storage
  (6461 whiteouts, 6459 of them the guest's, and the layer stack's opaque
  directory).  The two snapshots are node-identical (8984 nodes, those
  xattrs among them).  The real test passes too: each restore's /etc
  overlays (`vos-b` over `tree/etc`, `vos-a` over `part-future/etc`) and
  the containers layer stack, mounted read-only upper over lower, show
  merged views identical to the source's (deleted files stay deleted,
  opaque directories hide the lower, the redirect shows the renamed
  directory); and each restored `vos-b` mounts read-write as mount-sys
  mounts `/etc`, with its stale origin file handles (a new btrfs: overlayfs
  without `index` ignores them), shows the same view, and takes a write
  and a delete.  So nothing is a LIMIT for restic-bko, and rustic-bko
  needs no fix: it filters no namespace (`xattr_node_name` in rustic_core's
  `crates/core/src/backend/osattrs.rs` drops only bcachefs'
  `bcachefs_effective.*` and `bcachefs.project`; Linux's `xattr_list` in
  `osattrs/linux.rs` lists every name `llistxattr` gives; the restore's
  `xattr_restore_names` in `local_destination.rs` sets each node name as
  it is).
- **Whiteouts are hard links.**  overlayfs makes each whiteout by linking
  its workdir's one (`work/#NN`): 5 of the fixture's 6 (counting the two
  in the workdirs) are 2 groups of linked names; the rename's whiteout
  (`olddir`, made by `RENAME_WHITEOUT`) is alone.  Both snapshots record the links (inode, `links=2`/`3`), but both
  tools restore hard links of regular files only (restic's restorer;
  rustic_core's `hardlink_key` in `commands/restore.rs`, as upstream):
  each comes back a char 0:0 node of its own.  overlayfs treats both the
  same (the merged views pass), so it is a LIMIT, judged apart from the
  generic hard-link need.  The guest's own uppers had none, its storage's
  whiteouts aren't linked.
- **Vanilla's `/mnt` is a symlink** (to `var/mnt`, as `home`, `opt`, `srv`
  and `root` are).  The first run backed up `/mnt/src/...`: restic-bko kept
  the path as given, **rustic-bko stored the resolved `var/mnt/src/...`**,
  so every rustic restore landed beside its target and the snapshots
  weren't node-identical.  The check now works in `realpath /mnt`.  A
  cross-tool difference only (bko uses rustic-bko, on paths it mounted
  itself), not chased ([[cross-tool-compat-courtesy]]).
- **The guest's udev doesn't make the nodes of an LV that lvcreate or
  lvconvert activates only to wipe it** (the thin pool's metadata: "not
  found: device not cleared", for `lvconvert --type thin-pool` as Albius
  does it and for `lvcreate --type thin-pool` alike).  inner.sh runs both
  with `activation{verify_udev_operations=1}`, LVM then making the nodes
  itself.  Not seen in the rescuer (V7).
- **Overlayfs here:** the module defaults are `redirect_dir=N`, `index=N`,
  `metacopy=N`, `redirect_always_follow=Y`, and mount-sys mounts `/etc` with
  `lowerdir`, `upperdir` and `workdir` only, so a real upper never gets a
  redirect (renaming a lower directory in `/etc` copies it); the fixture
  makes one with `redirect_dir=on` so that a restore has one to keep.
  mount(8)'s fsconfig takes at most 256 bytes a value: overlay options with
  long absolute paths fail with "wrong fs type" and nothing in dmesg, so
  the check mounts with paths relative to the tree.
- **The guest's storage is too big to copy whole** (~220,000 entries,
  7.6 GiB; its 6948 whiteouts sit in ~31,000 directories), and the check's
  node listing (`rt_nodes`: a restic call a directory) would take hours:
  `nodecopy.py --nodes 600` keeps the device nodes and `trusted.*` entries
  and then whiteouts while their directories fit in 600 (6459 of 6948).
- **The scratch stack matches the guest's** in every compared field: GPT
  names and types (parted 3.8: the guest has no sfdisk or sgdisk), PV and VG
  fields, every LV's attributes and segment, the pool's 64 KiB chunks,
  passdown, zeroing and `lvol0_pmspare`, virtual/pool 2.00 as the guest's,
  btrfs flags (incompat 0x361, compat_ro 0xb, crc32c, 16 KiB nodes), ext4
  features of `/boot` and `init`, FAT32 geometry, and mount options.  Labels
  are the guest's with `-SET` added.  The guest has no attr package
  (bkoshim.py is setfattr), no xfsprogs (the work disk is ext4).
- **Open, low:** both tools restore hard-linked device nodes (and FIFOs,
  symlinks) unlinked, as upstream: harmless for overlayfs, but a restore
  isn't node-for-node there.  rustic-bko resolving a backed-up path's
  symlinks, where restic-bko doesn't (above).

## What V8 found

2026-11-14, the corpus entry `holt/tests/corpus/vanilla3` from `v7-1`'s
source dump (Chopin, warm build `/bkoscratch/holt-warm/vanilla`).

- **Two redaction bugs, both generic, fixed in `redact.rs`:**
  `lvol0_pmspare`'s LV UUID was all letters, and `lvm_spans` wants a digit
  and a letter, so the raw UUID stayed in the redacted dump (leakscan's (b)
  found it on Chopin; it never left the host).  An `id` or `*uuid` key whose
  whole value has an LVM UUID's shape now counts, whatever its letters.
  And check_plan of the corpus dump (a redacted dump redacted again) gave
  `vos-root`'s VG UUID the pseudonym `vos-var`'s PV already had in dashed
  form only: the LVM pseudonym allocator now avoids both forms, as the UUID
  one does.  Tests `lvm_uuid_without_digits` and
  `lvm_pseudonym_not_taken_dashed`; no other corpus dump changes (their
  raw dumps have neither case).
- **The golden plan** (297 lines, no problems): both groups by
  vgcfgrestore without the thin part, then `lvcreate --type thin-pool -n
  root -L 19456m --poolmetadatasize 1024m --chunksize 64k --discards
  passdown --zero y --poolmetadataspare y` and the two `--type thin`
  LVs; `-d single -m dup` on both roots (V7's fix); the init LV, root A and
  /var mounted beside fstab's tree; the boot stage's atimes, the check, the
  log and the tail.  No NVRAM entry: the source (HoltTest1's fresh variable
  store) booted the fallback path, so its dump has no `vanilla` entry.
- Not done here: Test2's registration (classes.json Obscure and uefi,
  holt/spec.json, guest/spec.json, estimates.tsv, plan.py's counts,
  world.py); done later the same day, on its own (the V8 row).

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| V1 | Fetch the 3.0 ISO into rebuild-inputs on Chopin (the user's OK first), pin its SHA-256. `make-vanilla.sh` by Decision 2's path, the post-install steps, the upgrade into B and the two `/etc/bko-root` marks; `rebuild-one.sh`; copy to Cinqcent (the same SHA-256). Settle every *check* (os-release, the recipe, fstab's lines, `trusted.overlay.*` in rustic-bko, the thin segments in a dump, LV UUIDs named nowhere, room) and write "What V1 found" here. Done when it boots alone into both roots on both hosts, SSH works, and `vm-update --try` is ok. **Done 2026-11-14** (uncommitted): `make-vanilla.sh` and `testvm/vanilla/`, the `rebuild-one.sh` line, the ISO in rebuild-inputs on Chopin and Cinqcent; the guest on both hosts, both roots booted on both; "What V1 found" above. `vm-update --try` waits for V5's ABRoot branch | 1.25 d (+0.5 d on path 2) |
| V2 | dump (High-effort subagent): the `SUPPORTED` row and tests; ABRoot detection; `holt/src/abroot.rs` and `boot.abroot`; `backup_sources`' two entries and their read-only mounts; kernels from `init`; `redact.rs`'s words; a Vanilla fake tree in `scenarios.rs`. **Built 2026-11-14** (uncommitted): `osrel.rs` row `("vanilla", "3", "Vanilla OS 3")` after Pop!_OS's (3.0 is taken by its major) and test `vanilla`; `holt/src/abroot.rs` (`config` with viper's last-key-wins merge, `parse_master`, `parse_abroot_cfg`, `read` with its own read-only mounts through `Sys`, `add_to_boot`); `report.rs` `BackupSource.from` now an `Option`, with `mount` (`MountHint`) and `note`, set only on ABRoot; the init LV's kernels in `boot.kernels.installed`; `redact.rs` GENERIC_LABELS (+ the seven `vos-*` names) and GENERIC_HOSTS (+`vanilla`); scenarios `vanilla_tree(b_used)` (both VGs, the /etc overlay, mount-sys's fstab, the installer's master grub.cfg and abroot.cfg, an upgrade's abroot.cfg for vos-b, or a fresh install's empty vos-b) in `dumps()` as `vanilla` and `vanilla_fresh`; the 11 new tests are listed in V4's row; 458 lib tests pass on Chopin (golden_plans' one failure is the known `ubuntu24-luks`/`ubuntu24-uefi` "no corpus dump"; no golden plan changed). A High review's fixes the same day: read-only mounts by type (`ro_options`: `noload` for ext3/ext4, `rescue=nologreplay` for btrfs) in dump's mounts and the backup_sources hints; the lock looked at before any mount, and no mount of dump's own while it's there (`mounts_skipped`, a warning); `parse_abroot_cfg(text, label)` takes the linux line with a kernel in `/LABEL/`, the initrd after it and the last `search --fs-uuid` before it in its block, not the file's first (an upgrade's 00_header font search); BackShot1's `bs1-common` stops with a message naming the mount hint on a `from: null` source, and `bs1-ostree`'s subvolume check skips such sources (ostree's all have a `from`: unchanged); 459 lib tests pass (+`ro_options_by_type`; the header case in `root_cfgs`, the check-script test and the scenario's vos-b abroot.cfg) | 0.75 d |
| V3 | load's thin pools (generic, High): `vg_metadata` without the thin part, the `lvcreate` lines, the plan problems, `fit.rs` unchanged but tested; scenario and unit tests; shared with Proxmox (above). **Built 2026-11-14** (uncommitted): `thin_of`, `vg_metadata` without the thin part, one `lvcreate --type thin-pool` per pool and `--type thin` per LV in `device_id` order with exact `m`/`k`/`s` sizes and the source's chunk size, discards, zero, spare and error-when-full; pool layers never formatted; refusals for thin snapshots, external origins, RAID or cache under a pool, multi-segment pools; scenario `thin_lvm`; 424 lib tests pass (golden_plans' one failure is the pre-existing `ubuntu24-luks`/`ubuntu24-uefi` cells with no corpus dump). Not built: the "used data can't fit" refusal (the dump records no pool usage). A High review's six findings fixed the same day: `--zero` and `--errorwhenfull` always passed (zero from `zero_new_blocks`, error-when-full from the pool LV's `ERROR_WHEN_FULL` flag), a striped `_tdata` made with `-i`/`-I` (a striped `_tmeta` refused), `--poolmetadataspare n` with a note when the source's spare was smaller than its metadata and the free extents can't take it, PVs without ALLOCATABLE under a pool refused, tags as `--addtag`, activation skip and read-only applied by `lvchange` before the group's `vgchange -an`; plan.rs's `thin_problems` names a rescue system without `thin-pool` in `lvm segtypes`, `thin_check` or dm-thin-pool (the slim base has all three: lvm2 2.03.31, thin-provisioning-tools 1.1.0); 434 lib tests pass | 1 d |
| V4 | load's ABRoot path (High): mount stage extras, the marker, `abroot_boot` (note, consistency check, NVRAM, log, tail), the v1 plan problems; tests. **Built 2026-11-14** (uncommitted): `holt/src/load/abroot.rs` (`of`, `marker`, `check_script`, `Builder::abroot_mounts` called from the mount stage before its sort, `abroot_problems`, `abroot_boot` returned early from the boot stage after ostree's, reusing `boot_tail`); `restored_marker()` and status.rs take `abimage.abr` (or `var/lib/abroot`); the scenario plans both VGs (thin `lvcreate` lines), every filesystem with its label and UUID, mounts `boot/init`, `part-future` and `/var` after fstab's tree, and a boot stage with no update-initramfs, grub-install, update-grub, grub-mkconfig or chroot: the atimes, the check, `efibootmgr ... --label vanilla --loader '\EFI\vanilla\shimx64.efi'`, the log, the tail. The check script runs against real fake trees in `check_passes_and_fails` (pass, empty root, wrong default, five mismatches at once). Refusals tested: operation.lock, BIOS, not thin two-root, v1 `/.system`, no grub default, `--new-uuids`. Tests: 2 in load/abroot.rs, 5 in abroot.rs, `osrel::vanilla`, `redact::vanilla_labels_and_host_are_kept`, scenarios `vanilla_guest` and `vanilla_fresh_install_and_refusals` (11 in all). Review fixes the same day: the check script's awk picks lines as `parse_abroot_cfg` does (a 00_header font search no longer fails a consistent tree; a cfg with only another root's kernel fails), and `abroot_problems` refuses a dump whose grub.cfg default leads to an empty root, before partitioning (tested in the fresh-install scenario) | 0.75 d |
| V5 | Guest tools: vm-mount's ABRoot mode and vm-chroot's /etc overlay, vm-update's branch, compare.py's tree, fingerprint/pkgset, `K-abroot`. **Built 2026-11-14** (uncommitted): `vm-mount`'s `mount_abroot` (gated on an LV named `init`, then a root holding `usr/share/abroot/abroot.json`; abroot.json merged viper's way, the upper's `/etc` copy over the lower's; the master grub.cfg's default picks the present root; / , /var, the live fstab's /boot and /boot/efi, /boot/init, /part-future), `--ro` activating a thin group from throwaway dm snapshots of its PVs, LUKS on an LV opened after LVM, and the "already active" test walking dm stacks to the PVs; `vm-chroot`'s /etc overlay (read-only as two lower layers under `--ro`); in-guest.sh's `Linux.abroot` section before Linux.common (`abroot upgrade </dev/null`, "No update available" is success, never `--force`, the timer kept masked, a step when grub.cfg's default moves); pkgset.sh's `base pm abroot` (the root's own dpkg status through a bind of /, else /var's; `base abimage DIGEST`); fingerprint.py's `abroot` kind (abimage in boot); vm-update's header; compare.py's `fstab_options` reads the live fstab; `K-abroot` in guest_judge.py, check-image.sh (kernels per root, through a `ro,noload` mount of the init LV) and check-after.sh (present against grub.cfg's default); tests in test_vm_update.py and test_guest_judge.py. `vm-update --try vanilla3` on Chopin: ok, nothing to merge (79 s). The real dumps and plans: "What V5 found" | 0.75 d |
| V6 | SticTest1 `vanilla3` entry and runs, both archivers, both hosts, 0 FAIL. **Done 2026-11-14** (uncommitted): `testvm/stictest1/vanilla3/` (check.sh, inner.sh, fixture.sh, judge.py, nodecopy.py; gpt.py, fatattr.py, bkoshim.py as pikaos4-luks's), its `stic/manifests.json` and `limits.json` entries, check-manifests.py's count (58), the README's row and estimate. Runs `v6b-20261114-090804` (Chopin, scratch on `/bkoscratch`: its RAM disk was taken) and `v6b-20261114-092119` (Cinqcent, RAM disk), binaries `roundt2r-aa72d65-3bb01f1-99b9b56`: **46 PASS, 5 LIMIT, 0 FAIL on each host**, every need covering both archivers in all four directions; restic-bko and rustic-bko both keep every `trusted.overlay.*` xattr and whiteout. "What V6 found" above | 0.35 d |
| V7 | HoltTest1: 1Obscure, the two-root boot in ht1.sh, judge.py's rules, `ram-sizes.tsv`; runs on both hosts. **Done 2026-11-14** (uncommitted): runs `v7-1` (Chopin) and `v7-2` (Cinqcent) PASS, on /bkoscratch (`HT_RAM=0`, `cmp-run.sh --reserve 0 5`), bko-holt `9fbeac3f5b54` from this tree by `HT_HOLT` (source and backup by `323e4069c953`, whose dump is the same code; rebuild and verify rerun after the two load fixes, verify again after judge.py's): 0 file and 0 dump differences, every load stage, the restored disk booting B by default and A by "Previous State (A)" picked on GRUB's serial menu, each the source's. ht1.sh: `abroot_source`/`abroot_verify`, `ABROOT_STATE`, `mountsys_check`, vm_up's `GRUB_PICK` (console-keys.py), `FLAKY_UNITS`; judge.py: `ABROOT_KEPT` (24 paths held), `dm_by_name`, `lvcreate_made`, with judge-test.py's `FsAbroot`, `DmByName`, `ThinPoolMade`; holt: the future root's btrfs profiles (`report::btrfs_allocation`, `abroot::Dump::btrfs_profiles`, scenario `vanilla_future_root_btrfs_profiles`) and the thin pool's `--errorwhenfull` (below); `ram-sizes.tsv` D 30; the holttest1d README's ABRoot section, 1Obscure row and HoltTest1All. "What V7 found" above | 0.6 d |
| V8 | Golden plan and the corpus test; Test2 registration in the batched pass. **Done 2026-11-14** (uncommitted): `holt/tests/corpus/vanilla3/{src.json,plan.txt}` from `v7-1`'s source dump by collect-dumps.sh and corpus_build (two builds, the same sha256 `a95eef1e...`; 36 identifiers, `ok:36:276`), leakscan 0 findings on Chopin, Cinqcent and the Mac (its plan too), its index.tsv row in guest order, the corpus README's provenance and leak paragraphs; two redaction fixes in `redact.rs` with two tests ("What V8 found"). Warm build on Chopin: 465 lib tests pass, golden_plans 14 pass and 1 fails with only the known `ubuntu24-luks`/`ubuntu24-uefi` "no corpus dump" (vanilla3 ok, 297 lines); the other test targets pass. **Test2 registered 2026-11-14** (uncommitted, vanilla3 alone, at the user's request, ahead of the batched pass): classes.json (holt.families.Obscure, holt.uefi), holt/spec.json (UEFI, console-keys.py in its entry and in the linux kind's per_guest), guest/spec.json (pm `abroot`, S-none, L-none, K-abroot, uefi-sb, memory 4096, harness in-guest.sh#Linux.abroot; known-lines.json has no `abroot` section yet, so it keys as absent), estimates.tsv (holt 1628 s from v7-1's passing phases, 28.9 GB on /bkoscratch; guest 3.32 gm), plan.py's and README's counts (64 sources, 65 guests), the tests' counts, world.py, the p2 goldens re-blessed. `t2 plan HoltTest2 vanilla3`: at first not runnable (D 30 over chopin's and cinqcent's ceiling 29); the user then turned `holt_disk_mode` on for chopin and cinqcent in testvm/hosts.json (2026-11-14), and the plan shows `holt:vanilla3` due on /bkoscratch (27.1 gm, 28.9 GB on chopin) | 0.4 d |
| V9 | Docs: holt/README.md's supported list, `osrel.rs`'s comment, HoltTest1's 1Obscure row and HoltTest1All, SticTest1's README, `layload.md`'s boot stage and thin pools, memory. **Done 2026-11-14** (uncommitted) but SticTest1's README (V6's): holt/README.md (Vanilla OS 3 in the list, an ABRoot bullet, `vanilla3` among the tested-by guests); layload.md (`assemble`'s thin pools: vgcfgrestore without the thin part, lvcreate by name, `--errorwhenfull` by lvchange; the boot stage's ABRoot paragraph; "Out of scope" narrowed); `osrel.rs`'s comment already had it (V2); HoltTest1's README already had the 1Obscure row and HoltTest1All (V7); memory | 0.1 d |
| V10 | *Optional* (Decision 6): `vanilla3-luks`, its typed passphrase, its golden plan, SticTest1 and HoltTest1 runs | 0.5 d |
| | *≈ 5.95 d without V10, ≈ 6.45 d with it* | |

V2 and V3 can start beside V1 from the source facts; their golden plan
(V8) waits for V1's dump. Each piece is committed on main when its tests
pass and the user says to commit; nothing is pushed until the user says
so. Source-code changes go to High-effort subagents
([[effort-max-when-it-pays]]).

**Costs** (est.): V1 a 30-60 min build on Chopin (the image pull, then an
upgrade's second pull), `--reserve 20 4` in /mnt/bko-ram for the build,
moved to disk when done; the qcow2 ~12-18 GiB a host, the ISO 1.8 GB a
host. V6 and V7 about 1.5 h of queue a host. None of the user's time but
the OKs.

## Risks

- **Size.** A Vanilla guest is two full root images plus a cached image.
  If HoltTest1 doesn't fit in the RAM disk, it runs from `/bkoscratch`
  (more wear and slower), unless Decision 5 drops the cache.
- **`trusted.overlay.*` xattrs.** If rustic-bko doesn't keep them, the
  restored /etc of both roots is subtly wrong (deleted files come back,
  opaque directories merge). That would be a fork fix first, before V2,
  the way the OpenBSD restore waits on the fork ([[openbsd-restore-oom]]).
- **The unattended install.** No serial console or SSH in the live ISO,
  and the GUI is the only documented installer. The remastered GRUB
  config and the recipe should do; if the live session won't log in on
  ttyS0, path 2 costs +0.5 d.
- **Sid churn and the registry.** `ghcr.io/vanilla-os/gnome:latest` moves;
  a rebuild or an update gets newer content. The build records the
  `/abimage.abr` digests of both roots. Every update flips the roots,
  and a failing upgrade (a bad image) blocks vm-update until upstream
  fixes it.
- **Live backups across an upgrade.** The hourly vso timer can start an
  `abroot upgrade` while a running system is backed up: the inactive root
  and `init` change under the backup, and grub.cfg may swap. dump records
  `operation.lock`; load's consistency check catches a mismatched grub.cfg.
  Holding updates during a backup (mask `vanilla-updates.timer`, touch
  `/run/abroot/userStop`) is the user's step, and the docs say so. HoltTest1
  backs up offline, so it isn't affected.
- **Thin LV UUIDs.** If something does name them after all (V1's grep),
  Decision 3's second option is needed: +0.5 d.
- **Fixed names.** Restoring onto a machine whose other disk has a Vanilla
  install is refused (target.rs). Booting a restored disk with the
  source's disk still attached would confuse ABRoot's label search; the
  plan's note says to detach it.

## Out of scope

- Vanilla OS 1 (ABRoot v1, `/.system`) and Orchid installs without thin
  provisioning (if any exist: refused in v1).
- Manual partitioning layouts, BIOS installs, arm64.
- Thin snapshots, external origins, thin pools on RAID or cache;
  `--fit-largest` and `--new-uuids` with thin pools or on ABRoot.
- Re-pulling an image instead of backing up a root.
- Apx/distrobox containers in users' homes beyond what a file backup keeps
  (their files, whiteouts and subuid owners are kept like any).
- BackShot1 on the Thinkpad (later, if asked).

## Decisions for the user

**Approved by the user on 2026-11-14, with every recommended option.**

1. **Which release:** **Vanilla OS 3 "Reunion"** (`vanilla` `3`, guest
   `vanilla3`) (recommended: it is the current release, it is still ABRoot
   v2 with the same layout, and Orchid may no longer be downloadable); or
   Vanilla OS 2 "Orchid" as asked (`vanilla` `2`, guest `vanilla2`, if its
   ISO and image are still published: *check*); or both (+0.75 d, a second
   guest and row).
2. **How the guest is built:** Albius from the live ISO over a remastered
   serial console, with the installer's own recipe (recommended: the real
   layout and post-install steps, made by Vanilla's own code); or a
   `vanilla` family in `make-from-live.sh` that runs the live image's
   `albius` on the build host (+0.5 d, host-side LVM risk); or one GUI
   install kept as a pinned image (not rebuildable).
3. **Thin pools in load:** `vgcfgrestore` the rest, then `lvcreate` the
   pool and thin LVs by name with the dump's sizes and chunk size
   (recommended: LVM's own tools make consistent pool metadata; only the
   pool's and thin LVs' LV UUIDs and device ids are new, and nothing on
   Vanilla names them); or keep every UUID by restoring the full metadata
   with `vgcfgrestore --force` and writing empty pool metadata with
   `thin_restore` from an XML of the dump's device ids, swapped in with
   `lvconvert --swapmetadata` (+0.5 d, LVM's repair path, less tested).
4. **The two unmounted filesystems in a live backup:** dump mounts them
   read-only for its own reading and lists them in `backup_sources` with a
   `from` the user's backup must mount the same way (recommended: one
   rule, as for ostree; the docs give the mount commands); or bko-holt's
   docs only, no `backup_sources` entries (less tied, easier to get wrong).
5. **`/var/lib/abroot/storage`, the cached OCI image:** kept (recommended:
   faithful, and `abroot upgrade` and `abroot pkg` work offline after the
   restore as before), at ~4-6 GiB more per backup; or left out of the
   backup and re-pulled at the next upgrade (smaller, fits the RAM disk
   more easily, but the restored system differs and an offline system
   can't upgrade).
6. **Guests:** `vanilla3` alone (recommended: the default layout covers
   ABRoot, thin pools and both roots; the LUKS option encrypts only /var
   and its prompt's path to the serial console is unknown); or
   `vanilla3-luks` too (V10, +0.5 d).
7. **HoltTest1's group:** **1Obscure** (recommended: as PikaOS, a sid
   derivative not on the Thinkpad); or **1D** beside Debian; or a new
   **1Immutable** with `kinoite44` moved into it.
8. **Secure Boot on the guest:** on (recommended: the installer's default,
   shim and Debian's signed GRUB, as AnduinOS and neon run); or off.
