# bko-holt load: rebuilding a machine's disks from bko-holt dump's JSON

Status: built (`holt/src/load/`): the argument parsing, the layout
contract, disk mapping with its refusals, the commands of every stage, `plan`
and `status`, and the writing stages, which run with `--commit`.  HoltTest1All
(`testvm/holttest1d/README.md`) round-trips whole machines of every system
bko-holt supports, and has which pass.  This document began as Debian 13's
design; the other systems follow it, with their own tools where Linux's
don't apply (`holt/README.md`).  The decisions taken so far are listed at the
end.

This was first written as two tools, `bko-laydump` and `bko-layload`.  They
are now one binary, `bko-holt`, with the subcommands `dump` and `load` (see
"Code layout").

## Goal

`bko-holt dump` (in `holt/`) records a machine's disk layout and boot setup
as JSON.  `bko-holt load` takes that JSON and rebuilds the layout on
blank disks: partition tables, md arrays, LUKS containers, LVM, ZFS pools,
filesystems, mounts, and, after the files are back, the boot loader.  The
restored machine should boot as before, with the same identifiers wherever
the tools allow it, so its fstab, crypttab, GRUB configuration, initramfs and
even its old UEFI boot entries keep working untouched.

It works in stages and does nothing it isn't told to.  Its `restore` stage
runs the archiver (rustic-bko) to put the files back.  An experienced
administrator can instead prepare the disks, stop, restore the files with the
archiver by hand, and come back for the boot loader.

Non-goals for version 1:

- Copying file data itself.  The archiver does that, in `restore` or by
  hand, between `mount` and `boot`.
- Disks of another shape: fewer or more disks.  Bigger disks are accepted,
  with the extra space left unused (see "Mapping disks"), and a disk a little
  smaller or bigger can be used with `--fit-largest PCT`, which resizes one
  partition per disk (see "Fitting a disk of another size").  Resizing
  anything else is version 2's, carefully.
- Changing the layout: another filesystem, another RAID level, dropping
  encryption.
- Key material.  LUKS and ZFS keys are never in the JSON, and TPM2/FIDO2
  enrollments can't be restored at all (see "Encryption").
- A source system bko-holt doesn't support: dump and load share one list
  (`SUPPORTED` in `holt/src/osrel.rs`).

## Where it runs

- Mainly on the rescue stick: the Linux image, one kernel and one OpenZFS
  (see the one-kernel decision), so its tools are newer than the machine's.
- On QEMU guests, for test backups and restores (`testvm/compare`).
- Never on the machine being backed up.

It doesn't check the os-release of the system it runs on.  It checks the JSON
instead: `"format": "bko-holt"` (or the older `"bko-laydump"`), a schema it
knows, and a source it supports (`os.id` and `os.version_id` on the list in
`holt/src/osrel.rs`).

### Newer tools than the machine

The stick's mkfs, mdadm, cryptsetup, LVM and OpenZFS are newer than the
machine's.  Their defaults turn on features an older target kernel may not
know, and the restored machine then can't mount its own root.  So load
never relies on a default.  It passes every feature explicitly, turning off
those the source didn't have, as "A stick newer than the machine" in
`stick-config.md` already requires for ZFS pools.  The details per layer are
under "Feature sets" below.

It also checks every one of its writes by reading the result back with
bko-holt dump's own parsers and comparing it with the JSON.  If the stick's
mkfs turned on something the source didn't have (a default added since this
was written), that check catches it and the stage stops.

## Stages

| Stage | Does | Writes to |
|---|---|---|
| `plan` | Reads the JSON, maps source disks to target disks, runs every check, prints every command the later stages would run | nothing |
| `status` | Reports what exists on the target disks, what's mounted where, and which stage comes next | nothing |
| `partition` | Wipes old signatures; writes each disk's partition table | partition tables |
| `assemble` | Creates md arrays, LUKS containers, LVM PVs/VGs/LVs and ZFS pools, and opens them | the new block layers |
| `format` | Makes filesystems and swap; creates btrfs subvolumes and ZFS datasets | filesystems |
| `mount` | Mounts the target tree under the target directory | mounts only |
| `restore` | Runs the archiver (rustic-bko, `--repo`) to restore a snapshot into the target tree; without `--repo`, the administrator restores by hand | the target tree, by the archiver |
| `boot` | Fixes up the restored system, rebuilds the initramfs, installs the boot loader, adds missing UEFI boot entries | the restored system, NVRAM |
| `close` | Unmounts, exports pools, deactivates VGs, closes LUKS, stops md arrays | nothing on disk |

`plan` and `status` never write.  Every other stage is a dry run that prints
its commands unless `--commit` is given.

### Stages check the disks, not just a state file

load keeps a state file of finished stages (under `/run/bko-holt/`,
named after the layout's `generated` time and host), but it doesn't trust
it.  Each stage first reads the target disks with bko-holt dump's scanner and
decides from what is actually there:

- Its work is already done and matches the JSON: it says so and moves on, so
  re-running after an interruption is safe.
- Its prerequisites aren't there (`format` with no partitions, `boot` with an
  empty root): it refuses.
- Something else is there (a disk that isn't blank, a filesystem with another
  UUID): it refuses unless that disk was named with `--erase`.

So a lost state file (a reboot in the middle) costs nothing, and the state
file mainly helps `status` explain what happened.

### `plan`

Checks, in this order, and reports all failures at once:

1. The JSON's format, schema and source system are ones load supports.
2. Every source disk maps to exactly one target disk (see "Mapping disks"),
   each at least as big, with the same logical sector size.  With
   `--fit-largest PCT` a target a little smaller is allowed too, and the
   layout is fitted to it (see "Fitting a disk of another size").
3. No target disk is in use, and each is named with `--erase` unless it's
   blank (see "Safety").
4. The stick's tools can recreate every feature the source had: ext4, XFS
   and btrfs feature names, LUKS ciphers and KDFs, md metadata versions,
   ZFS pool features (the check `stick-config.md`'s "Is this stick good
   enough?" makes at backup time, made again here).
5. Everything in the layout is something version 1 can build (see "Out of
   scope").
6. Each mounted filesystem's recorded used space fits in its new size.  With
   same-size or bigger disks it always does; with `--fit-largest` it's the
   check that decides whether a shrink is allowed at all.

Then it prints the commands of every later stage, with the target devices
filled in.

### `partition`

Per disk: `wipefs -a` on the disk (its O_EXCL open also refuses a disk
something still holds, before anything else writes), then zeroes with `dd`
through the disk's own node: its first 4 MiB and last 1 MiB, and the first
4 MiB (all of it, when smaller) and last 1 MiB of each partition the new
table will have.  wipefs erases only the magic it recognizes on the device
it's given, and the old partitions' content is still there: a PV label, a
LUKS header, an md superblock (1.0 and 0.90 at the end), ZFS labels (two at
each end) or a filesystem's superblock at a new partition's start or end
would be found by udev, LVM autoactivation, md's incremental assembly or a
pool import as soon as the kernel showed the partition.  Zeroed before the
table is written, the kernel never shows them.  Then `udevadm settle`, and
the table:

- GPT: written with `sfdisk` from a script built from the JSON, with the
  disk GUID (`label-id`), each partition's start, size, type GUID, partition
  GUID, name and attributes, and the same number of entries.  On a bigger
  disk, the backup header goes to the disk's end (`sfdisk` does that for a
  new table; dump's `backup_at_end` says whether the source had it there).
- MBR: the disk signature, primaries, the extended partition and its logical
  partitions at their recorded sectors, types and boot flags.
- Hybrid MBRs: recreated from `protective_mbr.entries`.

Partitions keep their exact sectors, so alignment and every offset recorded
inside them stay valid.  The BIOS boot partition and the gap after the MBR
are left empty: `boot` writes GRUB there.

Then `partprobe`/`udevadm settle`, and a check that the kernel shows every
partition at its recorded start and size (dump's `matches_kernel`).

### `assemble`

Bottom-up, in the order the topology needs: md first, then LUKS, then LVM.
ZFS pools come in this stage too, since a pool is the layer its datasets
live on.

- md: `mdadm --create` with the level, member count, metadata version,
  layout, chunk size, data offset, bitmap, name, homehost and UUID, and the
  members in their recorded roles.  It runs with `--assume-clean` (the members
  are blank, so there's nothing to resync); a resync would only slow the
  restore down.
- LUKS: see "Encryption".  Opened under the name crypttab gives it, so the
  restored system's crypttab matches what's open.
- LVM: from metadata, not from `lvcreate`.  load writes a VG metadata file
  from the JSON (`storage.lvm.volume_groups`, which carries every segment as
  LVM recorded it), then runs `pvcreate --uuid ... --restorefile` per PV and
  `vgcfgrestore`.  That keeps the VG, PV and LV UUIDs, the extent size and
  every LV's exact extents.  Then `vgchange -ay`.  Every LVM command load
  runs sees the group's own PVs only (`--devicesfile "" --devices PV,...`),
  so a group elsewhere on the machine, or a PV with one of the group's
  UUIDs on another disk, is never what it acts on.
- ZFS: `zpool create -d` with exactly the recorded features, the
  `compatibility` property, `ashift`, the vdev tree (mirrors, raidz, log,
  cache and spare vdevs) and the other locally set pool properties.  It's
  created with `-R` (an altroot under the target directory) and
  `cachefile=none`.  Then `zpool reguid -g` puts back the pool's GUID.

### `format`

- Filesystems and swap: see "Feature sets" and "Identity".  Each one is read
  back with bko-holt dump's parser right away, and its UUID, label, version and
  feature list compared with the JSON.
- btrfs: subvolumes created with their recorded paths and IDs where btrfs
  allows it, read-only flags and properties set, and the default subvolume.
  With `--repo`, snapshots the backup holds below a mounted subvolume
  (snapper's `/.snapshots/N/snapshot`) wait for `restore`, which makes each
  one a snapshot of the one before it and restores only its changes, so they
  share their extents; quotas go on after them
  (docs/design/btrfs-snapshots.md; `--btrfs-snapshots copy` restores each in
  full instead).
- ZFS: datasets and volumes created top-down with every locally set
  property, encryption roots with their key format and location, and
  `canmount`/`mountpoint` exactly as recorded.

### `mount`

Mounts the target root at the target directory (`--target`, default
`/mnt/bko-target`) the way the restored system will mount itself: from the
JSON's fstab, with btrfs subvolumes and ZFS datasets, ordered by mount point.
Swap isn't activated.  The ESP and `/boot` are mounted, because the archiver
restores their files like any others.

It ends by printing the mount tree and the archiver's target directory.  The
restore's job is to fill exactly that tree.

### `boot`

Runs only after a restore, and checks that one happened first: the target
root's `etc/os-release` matches the JSON's `os`, and `/boot` holds at least
the kernel the source was running (`boot.kernels.installed[].running`).
Then, chrooted into the target (with `/dev`, `/proc`, `/sys`, `/run` and
efivarfs bound in):

1. Identifier fix-ups (only with `--new-uuids`, or where a layer couldn't
   keep its old identifier; see "Identity"): rewrite fstab, crypttab and
   `mdadm.conf`, and regenerate `/etc/zfs/zpool.cache`.
2. Key files: containers and encryption roots that were created with a
   temporary key get the restored key file added and the temporary key
   destroyed (see "Keys follow the original system").  If the restore didn't
   bring the key file back, `boot` stops here and says which one is missing.
3. The initramfs, remade by the source's own generator, as the dump's
   `boot.initramfs.generator` names it (the restore changed identifiers the
   image embeds: crypttab, mdadm.conf, the zpool cache, LVM and md UUIDs):
   - Debian's family (and Chimera): `update-initramfs -u -k all`;
   - dracut (Fedora, Rocky, Void, EndeavourOS, openSUSE): `dracut --force
     --kver K` for each kernel in `/lib/modules` (EndeavourOS with GRUB:
     `dracut-rebuild`);
   - mkinitcpio (Arch and its family): `mkinitcpio -P`; mkinitfs (Alpine):
     `mkinitfs K` for each kernel;
   - Gentoo (docs/design/gentoo.md): installkernel's `install.conf` names
     the generator.  dracut makes each kernel's image under installkernel's
     name, `dracut --force /boot/initramfs-K.img K`, for each kernel that
     had one; `none` makes nothing.  genkernel, ugrd, a unified kernel
     image, an installkernel layout other than `grub` or `compat`, and an
     image at `/lib/modules/K/initrd` are refused by name.
     installkernel's `.old` copies come back as they were, with a note;
   - nothing where the image is prebuilt with the kernel (AerynOS), comes
     with the image (ostree) or is in the store (NixOS).
4. The boot loader, as `boot.loader` recorded it:
   - GRUB on BIOS: `grub-install` on the target disks that map to debconf's
     `grub-pc/install_devices`, then `update-grub`.
   - GRUB on UEFI: `grub-install --target=x86_64-efi` into the mounted ESP,
     with the bootloader ID taken from the directory of the entry the firmware
     booted (`\EFI\DEBIAN\SHIMX64.EFI` gives `debian`), with shim when
     `boot.loader.via_shim` says the source booted through shim, then
     `update-grub` on Debian's family, elsewhere `grub-mkconfig` (Fedora's
     `grub2-mkconfig`) into the restored grub.cfg.  This also embeds the restored LV's
     `(lvmid/VG-UUID/LV-UUID)` when /boot/grub is on LVM (Gentoo's
     Thinkpad).  On Gentoo through shim it refuses: it would write an
     unsigned GRUB over the signed one.
   - systemd-boot: `bootctl install`.
5. UEFI boot entries (see "UEFI boot entries").

### UEFI boot entries

On the same machine with the original partition GUIDs, the firmware's old
entries (`boot.efi.entries`) already point at the right partition and file,
and keep working.  So `boot` changes NVRAM as little as it can:

1. For each recorded entry whose partition GUID belongs to a restored
   partition: if an entry with the same description, partition GUID and file
   still exists, leave it; if not, create it (`efibootmgr --create` with the
   recorded description, disk, partition and file).
2. Restore the recorded `BootOrder` for these entries, keeping every other
   entry where the firmware has it.
3. Never delete or change entries that don't point at a restored partition:
   another operating system's (the stale "Windows Boot Manager" on Chopin,
   say), the firmware's own (USB, network), or the rescue stick's.

`grub-install` and `bootctl install` would create entries of their own;
`boot` runs them with `--no-nvram` / `--no-variables` and does the above
instead, so the result doesn't depend on their habits.  With `--no-nvram`,
step 1 to 3 are skipped (and `--removable` is the way such a machine finds
its loader).

### `close`

Unmounts the target tree, exports the pools (setting `cachefile` back to its
recorded value first), deactivates the VGs, closes the LUKS containers, and
stops the md arrays.  The disks are then ready for a reboot into the
restored system.

`close` runs only when asked: `--through close` (the normal full run) or
`--only close`.  `--through boot` stops after `boot` and leaves everything
open, so an administrator can look around the restored tree or chroot into
it first.

Skipping `close` is mostly harmless (md, LVM and LUKS survive an unclean
stop), except for ZFS: a pool the stick imported and didn't export is
marked as in use by the stick's host ID.  The restored system's initramfs
then refuses to import it without `-f`, and the first boot stops at an
initramfs prompt.  So whenever a pool is still imported, the end of `boot`
and every `status` say so plainly ("rpool is still imported: run `--only
close` before rebooting, or the restored system won't import it").  That
warning can't be turned off.  load never reboots anything itself.

## Logs

Each stage logs every command it ran, with its output and exit status, and
every check and its result.  Logs name devices, identifiers and paths, never
keys or passphrases.  They go to:

1. `/run/bko-holt/` while the stages run, since `partition`, `assemble`
   and `format` run before there is any target filesystem to write to;
2. the restored system's `/var/log/bko-holt/`, copied there once `mount`
   has mounted it and again after each later stage, so the machine keeps the
   record of how it was rebuilt;
3. on the rescue stick, the restore log on its config partition
   (`restore-logs/` in `stick-config.md`), the only storage that survives a
   reboot when a rebuild fails before the target is mounted.

## Command line

```
bko-holt load LAYOUT.json [--through STAGE | --only STAGE] [--commit]
            [--map SRC=DST]... [--erase DEV]... [--fit-largest PCT]
            [--target DIR] [--new-uuids] [--key-file NAME=FILE]...
            [--no-nvram] [--removable]
            [--repo REPO [--password-file FILE] [--snapshot ID]
             [--archiver PROGRAM]]
```

- With neither `--through` nor `--only`, it runs `plan`.  A whole restore is
  `--through close` with `--repo`.  By hand, it's `--through mount`, the
  archiver run by the administrator into the target tree, then `--only boot`
  and `--only close`.  A run that includes `restore` without `--repo` is
  refused (exit 2).
- `--through STAGE` runs every stage up to and including STAGE, skipping
  those already done.  `--only STAGE` runs just that stage.
- `--commit` lets the writing stages write.  Without it they print their
  commands.
- `--map SRC=DST` maps a source disk (its kernel name in the JSON, e.g. `vda`)
  to a target disk (`/dev/nvme0n1`, or a `/dev/disk/by-id` link).
- `--erase DEV` allows a target disk that isn't blank to be wiped.  It names
  the whole disk, once per disk.  There's no "erase everything" flag.
- `--fit-largest PCT`: a target disk a little smaller or bigger than its
  source may be used, by contracting or expanding each disk's largest
  partition by at most PCT percent (1 to 50).  Linux layouts only; `plan`
  prints what it changed (see "Fitting a disk of another size").
- `--target DIR`: where `mount` builds the tree, default `/mnt/bko-target`.
- `--new-uuids`: give every layer new identifiers, and have `boot` rewrite
  everything that names them.  Off by default.
- `--key-file NAME=FILE`: a LUKS container's (by its crypttab name) or a ZFS
  encryption root's (by dataset) key or passphrase from a file, instead of a
  prompt.
- `--repo REPO`: the repository `restore` restores from (rustic-bko's
  `--repository`), with `--password-file FILE` (else rustic-bko asks),
  `--snapshot ID` (default `latest`) and `--archiver PROGRAM` (default
  `rustic-bko`).  `restore` runs `ARCHIVER --repository REPO restore
  --numeric-id SNAPSHOT TARGET`, BackOtter's restore flags, and `plan`
  checks that `ARCHIVER --version` says it's BackOtter's.
- `--no-nvram`: `boot` writes no UEFI variables (QEMU guests without
  persistent variables).  `--removable` also installs to the removable-media
  path `EFI/BOOT/BOOTX64.EFI`.

Exit status: 0 done, 1 a stage failed, 2 bad arguments, 3 the layout isn't
one load supports, 4 `plan` found problems.

## Safety

The tool erases disks.  So:

1. Dry run by default.  Nothing is written without `--commit`.
2. A disk that isn't blank is only wiped when named with `--erase` (blank
   means no partition table, no signature in its first and last MiB, and no
   holders).  Naming it on the command line is the confirmation; load
   doesn't ask again.
3. These are refused even with `--erase`:
   - the disk the stick booted from (found the way `stick-config.md`'s
     "Finding the config at boot" finds it);
   - any disk with a mounted partition or active swap, unless load made
     those itself in an earlier stage of this layout (mounted under
     `--target`);
   - any disk holding the running system's `/`;
   - any disk whose holders (below) can't all be let go of.
   And the plan is refused when a volume group with a name or UUID the
   layout's has, or a PV with one of its PVs' UUIDs, is on a disk that
   isn't a target (most often the source's own disk, still attached):
   load's own commands wouldn't see it, but the restored system's tools in
   `boot` (its initramfs generator; GRUB, which finds LVs by UUID) and its
   first boot would find it twice.  LVM has no in-core name to give the
   layout's for a while, as ZFS has (a pool of the layout's name imported
   here is imported as NAME_bko).  The way out is to disconnect that disk,
   or `vgimportclone` its group (a new name and new UUIDs; `vgrename`
   keeps the UUIDs).
4. Holders, with `--erase` only.  A disk whose partitions (or itself) are
   held in sysfs (LVM, LUKS/dm-crypt, md, other device-mapper) or are vdevs
   of an imported ZFS pool, none of which load made in an earlier stage of
   this layout, is refused without `--erase` ("give --erase DISK to let go
   of what holds it").  A rescue system's udev starts these at boot: LVM
   autoactivates an old volume group, md assembles an old array
   (BackShot2's leftovers set, L3 and L6; backshot2.md).  With `--erase`
   the partition stage lets go of them first, from the top of each stack
   down, then wipes the disk as before.
   A target that carries this layout's own identifiers (its partition
   table's, as the source's own disk does on a bare-metal restore, or only
   the layout's filesystem and container UUIDs) goes the same way, with the
   same refusals, mounted partitions and swap included, when the partition
   stage will run.  The stage runs under `--only partition`, and under
   `--through` (and the plan) unless status shows it done on every target.
   Done is each table the source's, every partition at its sectors (the
   check `--through` skips it by), and, on a disk named with `--erase`, a
   record that this rescue session's partition stage wrote that table:
   `RUN_DIR/partitioned/DISK` (`/run/bko-holt` on Linux), which the stage
   writes once its commands have run (and removes before they start),
   naming the layout (host and dump time), the source disk, the target and
   the table and partitions written.  A source disk with no table (its
   content on the whole disk) has no table to check, but its target is
   wiped and zeroed by the partition stage too: named with `--erase`, it
   counts (in status, as one of the disks the stage is done on or not) only
   by its record, which says the disk was wiped.  The target is named by
   what sysfs says of it, which holds while the disk stays attached: its
   kernel name, its size, and the serial and WWID its driver gives
   (`device/serial`, `device/wwid`, empty when there are none); not by
   udev's `ID_SERIAL_SHORT` or `ID_WWN`, which change with udev's database
   and would make a rerun wipe a disk load had restored.  The source's own
   disk as found has the source's table (or, with no table, the source's
   filesystem) too, the usual case on a bare-metal restore, but no record,
   so with `--erase` the stage runs on it: what the old system has there
   (an active volume group, an LV mounted elsewhere) is let go of or
   refused, and the disk is wiped as asked; the plan says so ("the
   partition stage runs: --erase and no record that load wrote this table",
   or "wiped this disk"), and status shows the stage not done (or partly,
   for some of the disks).  A rerun after the stage, in the same session,
   finds the records, so it lets go of nothing and refuses nothing.
   RUN_DIR is a tmpfs: a reboot forgets the records along with the
   session's mounts, imports and activations, so after one the disks count
   as found again and `--erase` starts over; so does a disk that comes back
   under another kernel name.  Once the partition stage runs with
   `--erase`, `--through` skips no stage after it as done (a stage the
   layout needs nothing from is still skipped): before the wipe (and in a
   dry run), status would read the old system's filesystems, `/etc/fstab`
   and `/etc/os-release` as done.  Without `--erase`, `--through` skips as
   it always did.  Load's own is anything in the disk's stack mounted
   under `--target`, or a pool of the layout's imported there, from an
   earlier partial run: never let go of; when the partition stage won't
   run, nothing is refused either, and when it will (`--only partition`,
   say), the plan refuses a disk named with `--erase` that has it ("load's
   own is on it ... `--only close` first lets go of it"), rather than
   wipefs failing on it.  Without `--erase` such a target is taken as it
   is, with or without a record.  `plan` works the chain out from
   `/sys/block/*/holders` and `slaves`, the mount table, the swaps, the PVs
   and `zpool status`, and prints each step; nothing runs without
   `--commit`.  The whole plan is refused, naming the device and the other
   disk, if any device in the chain (every LV of a volume group in it, too):
   - is mounted or in use as swap (a pool: any of its datasets mounted);
   - is open beyond what's stacked on it (`dmsetup info`'s open count);
   - also sits on a disk that isn't named with `--erase` (a volume group
     with a PV elsewhere, an md array with a member elsewhere, a LUKS
     mapping on an LV of such a group, a pool with a vdev elsewhere).
   The stick and the backup drive are never reached: neither is ever a
   target named with `--erase` (the stick is refused as a target, a local
   backup drive is mounted while load restores from it), and no step
   touches a device that sits on any other disk.  The steps, the commands load's own close stage runs
   for what it makes (plain, so the plan reads as typed), after a `udevadm
   settle`:
   `zpool export POOL`; `cryptsetup close NAME`; `vgchange --devicesfile ""
   --devices PVS --activate n VG` (LVM seeing that group's PVs only, so a
   same-named group elsewhere is never the one stopped); `mdadm --stop
   /dev/mdN`; `dmsetup remove NAME` (other device-mapper).  Then `udevadm
   settle`, and the partition stage as before: `wipefs --all`, whose O_EXCL
   open fails on a disk anything still holds, so if a step didn't take, or
   something started it again, nothing is written.  Each command refuses a
   device in use, and the stage stops there.
   Autoactivation racing back: the old PV label (sector 1 of the old
   partition) survives the release; nothing in it is erased.  `wipefs
   --all` on the disk erases the old table and re-reads it, so the old
   partitions leave the kernel and udev has no device that starts at the
   label.  After that, LVM (and blkid) look for a label only in a device's
   first four sectors.  A new partition that starts where an old PV did
   would show it again, but the partition stage zeroes each new partition's
   first 4 MiB, and the disk's own, through the disk before sfdisk writes
   the table (8b7e177); anywhere else the old label is inside a new
   partition, not at its start, and is never seen (a loop device on Chopin,
   2026-11-13: `blkid -p` found LVM2_member on a new partition at the old
   PV's start without the zeroing, nothing with it, and nothing on one
   starting 1 MiB before).  The same holds for md 1.1/1.2 and LUKS headers
   at a partition's start; md 0.90/1.0 superblocks at an old partition's
   end are seen only by a new partition ending there, whose last 1 MiB is
   zeroed too.
5. A target disk smaller than its source is refused unless `--fit-largest
   PCT` says by how much the layout may be fitted to it (see "Fitting a disk
   of another size"); one with another logical sector size is always refused.
6. `boot` refuses to run on a target root that doesn't hold the restored
   system (see `boot`).
7. Each stage reads the disks back before doing anything (see "Stages check
   the disks, not just a state file").

In QEMU guests the same rules hold.  Test harnesses pass `--map` and
`--erase` for the guest's blank disks like anyone else.

## Mapping disks

Version 1 needs the same number of disks as the source, each at least as big
(or within `--fit-largest PCT` of it), with the same logical sector size.

A source disk maps to a target disk:

1. by `--map`, when given; otherwise
2. by identity: the same WWID, or the same serial and model.  That's a
   restore onto the same machine, or onto its disks moved to another.

Anything left unmapped is an error that lists the source disks (name, size,
model, serial) and the candidate target disks.  load never guesses by size
or order: two same-size disks swapped would put the wrong data on each.

QEMU guests' disks have QEMU's serials, so test restores always use `--map`.

A bigger target keeps the source's partitions where they were and leaves the
rest unused.  For GPT that means a new backup header and last usable LBA at
the new disk's end.  For MBR nothing changes.  `--fit-largest PCT` will grow
the largest partition into that space instead; growing a pool, an md array or
anything else is version 2's job.

## Fitting a disk of another size

`--fit-largest PCT` (an integer 1 to 50, with an optional `%`) lets a target
disk a little smaller or bigger than its source be used: per disk, the
largest partition contracts or expands by at most PCT percent of its own
size, and everything else keeps the size it had.  The motivating case is a
replacement disk a gigabyte smaller than the one that died: `/boot` 300 MB,
swap 16 GB, `/` the rest.

Nothing is resized in place.  `load` remakes every filesystem with mkfs and
the archiver refills it, so the fit only chooses other numbers before
anything is erased, in one pure function of (the dump, the targets' sizes,
PCT) in `holt/src/load/fit.rs`.  It rewrites the dump `load` works from, once,
where every stage reads it (`target::prepare`), so `partition`, `assemble`,
`format`, `mount`, `boot`, `close` and `status` all see the same table.

Without the flag nothing changes at all: the same commands, the same output,
except that the "target is smaller" problem now names the flag.

- **Scope.** Linux-family layouts, the ones whose table goes on with an
  `sfdisk` script.  A BSD, illumos or Haiku layout (`other/`, whose table
  `bko-holt write-table` writes itself) is still refused a smaller target,
  and a bigger one is left as it is, with a note.
- **Which partition.** Per disk, independently: the largest by sectors, which
  an MBR's extended container never is.  An exact tie is refused, naming
  them.  Nothing else on the disk is resized: `/boot`, the ESP, swap and
  every UUID, GUID, label and attribute keep their values.
- **Shrinking.** If the last partition already ends inside the new disk, no
  partition changes at all (only the source's own slack was lost), and a note
  says so.  Otherwise the overrun is rounded up to a whole MiB; that much is
  refused if it's more than PCT percent of the largest partition.  The
  largest keeps its start and loses it from its end; the partitions after it
  move down by as much and keep their sizes, since swap at the end of a disk
  is common.
- **Growing.** The room past the last partition, rounded down to a whole MiB,
  capped at PCT percent of the largest partition, and on an MBR by the last
  sector a partition entry can reach (its start and its sector count are
  both 32-bit, so `sfdisk` refuses a table that goes further).  Whatever is
  left over stays unused, with a note.
- **What the largest partition may hold.** A filesystem among ext2/3/4, XFS,
  btrfs on one device, f2fs, JFS and NILFS2; LUKS with one of those inside;
  or LVM as below, directly or inside LUKS.  Swap, an md member, ZFS, btrfs
  over several devices, bcachefs, anything else, and any layout where the
  largest partition or one that would have to move is an MBR logical or
  extended partition, are refused with the reason.  So is moving a partition
  whose bytes the dump holds to put back where they were (an unmounted ESP, a
  boot area): a BIOS boot partition is not one of those, since `grub-install`
  in the `boot` stage makes it again.  A FAT that moves is the one other
  thing that knows where it is: its BPB records its partition's first
  sector, which `mkfs.fat` is given as `-h`, so the fit writes the new one
  down, and refuses the move if what's recorded was never the partition's
  own start.
- **Everything the size is pinned to changes with it.** The partition's
  sectors and end, the disk's size, a GPT's last usable and backup LBAs (and
  its protective MBR's own entry), XFS's `size=` and `agsize=`, `mke2fs -E
  resize=` and the ext block count, btrfs, f2fs, JFS and NILFS2 sizes, the
  LUKS payload offset (unchanged, so the plaintext device loses the
  difference) and LVM's `dev_size`, `pe_count` and segments.  A filesystem
  the source left smaller than its container keeps that slack; one that
  filled its container fills it again.  XFS gets one more rule: a last
  allocation group that would be a stub (under 16 MiB, xfsprogs'
  `XFS_AG_MIN_BYTES`) is left out, because `mkfs.xfs` leaves it out too, and
  the dump has to say what the tool will make.  Where that last group is the
  one `mkfs.xfs` puts the internal log in (group `agcount/2`, so the last of
  two) it has to hold the log as well, or it goes the same way.  JFS sizes its own log from
  the device, so the size written down for it is the fit's arithmetic rather
  than the byte-exact figure `mkfs.jfs` will record (nothing reads it back:
  `mkfs.jfs` takes no size).
- **LVM.** The partition (or the LUKS inside it) is a PV of a volume group
  that lies wholly on that one partition; a VG with more than one PV is
  refused.  Shrinking takes nothing at all where the room the PV already
  leaves past its last extent covers what the disk lost; otherwise it takes
  the group's free extents first, from the highest down, and if they don't
  cover it the largest logical volume gives up the rest from its end, which
  PCT limits as well.  That volume must be linear, hold a
  filesystem the fit resizes, and never be swap; a tie for largest is
  refused.  The extents that go are dropped and the ones above them
  renumbered downwards in order, keeping `pe_start` and the extent size, with
  the segments, `pe_count` and `dev_size` worked out again.  A striped, thin,
  cached, RAID or snapshot volume that would have to change or move is
  refused.  Growing adds extents, which stay free in the group, and no
  logical volume changes.
- **Does the data still fit.** For every filesystem that would shrink, the
  dump's own figures (`storage.mounts`' `size_bytes` and `used_bytes`, else
  the superblock's free blocks) must leave `shrink + max(1 GiB, 5% of what's
  used)` free, at plan time, before anything is erased.  Figures the dump
  doesn't have refuse the shrink.  Working from free space rather than
  capacity accounts for each filesystem's own overhead.
- **Reporting.** `plan` prints a `# Fit` block: each disk's old and new
  geometry, what shrank or grew, what moved, and the data-fit figures.  A
  table an earlier stage wrote with `--fit-largest` is recognised on the disk
  (`fit::wrote`: the geometry a fit of this layout onto a disk of that size
  comes to, for some PCT and no other, so a partition somebody resized by
  hand is nothing of ours and keeps the rows it always had), so a later
  stage run without the same flag is refused with that reason instead of
  reading the disk as the source's.  `--erase` for that disk clears the
  refusal, since the table is then wiped anyway.

## Identity

Restoring the old identifiers is what keeps the restored system's own
configuration valid.  Not every layer allows it:

| Layer | Original kept by | Notes |
|---|---|---|
| GPT disk GUID, partition GUIDs | `sfdisk` script (`label-id`, `uuid=`) | |
| MBR disk signature | `sfdisk` script (`label-id`) | Gives the PARTUUIDs `SIG-NN` back too |
| ext2/3/4 | `mke2fs -U`, `-E hash_seed=` | The hash seed too, so directory indexes match |
| XFS | `mkfs.xfs -m uuid=` | A `meta_uuid` source needs `xfs_admin -U` after mkfs |
| btrfs | `mkfs.btrfs -U`, `--device-uuid` | `--device-uuid` only for single-device filesystems |
| FAT (ESP) | `mkfs.fat -i` (volume ID) | |
| swap | `mkswap -U -L` | |
| LUKS | `cryptsetup luksFormat --uuid --label --subsystem` | |
| md | `mdadm --create --uuid --name --homehost` | dump's `mdadm_uuid` is already in mdadm's form |
| LVM PV, VG, LV | `pvcreate --uuid --restorefile`, `vgcfgrestore` | Plain `vgcreate`/`lvcreate` can't set UUIDs |
| ZFS pool | `zpool reguid -g GUID` after `zpool create` | In OpenZFS 2.3 and later |
| ZFS vdevs | Can't be kept | They get new GUIDs; only the zpool cache names them, and `boot` regenerates it |

With `--new-uuids`, every layer gets fresh identifiers, and `boot` rewrites
everything that names the old ones:

- `fstab`, `crypttab` and `mdadm.conf`;
- the zpool cache;
- `/etc/initramfs-tools/conf.d/resume`;
- `/etc/kernel/cmdline`, and GRUB's configuration through `update-grub`;
- the initramfs.

The UEFI boot entries are created for the new partition GUIDs.  The old
ones, which now point at no partition, are left alone (load never deletes
an entry) and listed by `status`, for the administrator to remove with
`efibootmgr -b NNNN -B`.

Names (md array names, VG and LV names, pool names, filesystem labels,
partition names) are always kept.

## Feature sets

Never the stick's defaults: every feature on or off as the source had it.

- **ext2/3/4**: `mke2fs -t TYPE -O none,F1,F2,...` from dump's
  `features`, minus the ones that record state rather than design
  (`needs_recovery`, `orphan_present`).  Plus block size (`-b`), inode size
  (`-I`), inode count (`-N`), cluster size (`-C`, with bigalloc), flex group
  size (`-G`), reserved blocks (`-m`/`-r`), casefold encoding (`-E
  encoding=`), and the hash seed and default hash (`-E hash_seed=`, through
  `tune2fs` if mke2fs can't).
- **XFS**: every feature named on the command line, on or off:
  `-m crc=,finobt=,rmapbt=,reflink=,bigtime=,inobtcount=`, `-i sparse=,nrext64=`,
  `-n ftype=` (and the same for any feature the stick's mkfs.xfs knows that
  the JSON doesn't list: off).  Plus block, sector and inode size, AG count,
  stripe unit and width, internal or external log and its size, directory
  block size and `imax_pct`.  xfsprogs' `-c options=` profiles
  (`lts_6.12.conf`, for example) are a fallback when a feature can't be named
  on the command line.
- **btrfs**: `mkfs.btrfs -O` with every incompat and compat-ro feature, on
  (`F`) or off (`^F`); `--csum`; node and sector size; data and metadata
  profiles from `allocation_profiles`.
- **FAT**: FAT size, sector size, cluster size, reserved sectors, FAT count,
  hidden sectors.
- **swap**: page size (`--pagesize`).
- **md, LUKS, LVM, ZFS**: as in `assemble` and "Encryption".

The read-back after each write is the real guarantee: dump's parser lists
the new filesystem's features, and they must be exactly the recorded ones.

## Encryption

The JSON records how each container was made but never a key.

- LUKS: `cryptsetup luksFormat` with the recorded type (1 or 2), cipher, key
  size, sector size, data offset (`--offset`), header and keyslot area sizes,
  label, subsystem and UUID, and the recorded KDF type (argon2id, argon2i or
  pbkdf2).  The KDF's cost is benchmarked anew on this machine rather than
  copied.  The keys follow how the original system unlocked itself (below).
- Tokens (systemd-tpm2, systemd-fido2, clevis) can't be restored: they're
  bound to hardware or to keys the backup doesn't hold.  `plan` lists them,
  and `status` reminds the administrator to enroll them again after the
  first boot (`systemd-cryptenroll`).
- ZFS native encryption: each encryption root is created with its recorded
  `encryption` and `keyformat`, and its key as below.

### Keys follow the original system

load asks for keys the way the original system asked for them at boot,
as its crypttab and its encryption roots' `keylocation` recorded it:

- Containers that shared one passphrase at boot (crypttab lines with
  `keyscript=decrypt_keyctl` and the same key name in the third field) get
  one prompt between them, and the same new passphrase.
- Containers whose crypttab line says `none` (or `-`), containers not in
  crypttab, and encryption roots with `keylocation=prompt` get a prompt each.
- Containers and encryption roots unlocked by a key file (a path in
  crypttab's third field, or `keylocation=file://...`) have a problem: that
  file usually lives on the root filesystem, which the archiver restores only
  after `assemble` created them.  So load creates them with a temporary
  random key kept in `/run/bko-holt/`, and in `boot`, once the restored
  key file exists, adds it (`cryptsetup luksAddKey`, `zfs change-key -o
  keylocation=file://...`) and destroys the temporary key.  The restored
  system then unlocks exactly as before, and nobody has to handle the key
  file.

The new passphrases may differ from the old ones.  `--key-file NAME=FILE`
gives a container's or encryption root's key from a file instead of a
prompt, for scripted test restores.

## The JSON as a contract

Once `load` reads it, `dump`'s output is an interface:

- load reads `"format": "bko-holt"` (and `"bko-laydump"`, the same document
  from before the rename) and refuses any `schema` it doesn't know.
- New fields don't change the schema number.  Renaming or removing a field,
  or changing what it means, does.
- The fields load reads are listed in a schema description kept next to
  dump's code and tested both ways: dump's tests check its output has
  them, and load's tests read fixtures made by dump.

### What dump must record first

Gaps found while writing this, to close before load's stages need them.  All
five were closed on 2026-11-05 (bko-holt 0.2.0):

1. **ZFS**: every locally set and received property of every pool and
   dataset (`zpool get`/`zfs get -s local,received all`), with user
   properties and encryption roots.  Today it asks for a fixed list of
   columns, which loses everything else.  It also needs the full vdev tree of
   each imported pool, including log, cache and spare vdevs.
2. **btrfs**: subvolume flags (read-only), properties (compression), and
   qgroups.
3. **XFS**: the external log's device, when there is one.
4. **ext4**: the RAID stride and stripe width hints (`-E stride=,stripe_width=`).
5. **EFI**: the raw device path of each boot entry, so `boot` can recreate
   entries whose device path isn't a plain HD/File pair (dump keeps only
   its text form today).

Already there and enough: LUKS cipher, sector size, KDF and data offset; md
level, layout, chunk, bitmap, data offset and member slots; LVM metadata
with every segment; GPT and MBR tables with every identifier; ext4, XFS and
btrfs geometry and features.

## Out of scope for version 1

`plan` refuses layouts that need any of these, and says which:

- a different number of disks, or a different logical sector size; a disk
  smaller than the source, unless `--fit-largest PCT` fits the layout to it;
- LVM thin pools, cache volumes, RAID LVs and snapshots (version 1 builds
  linear and striped LVs);
- ZFS dRAID and special/dedup vdevs;
- filesystems load can't make: on Linux it makes ext2/3/4, XFS, btrfs,
  f2fs, nilfs2, JFS (not with an external log), bcachefs (on one device,
  unencrypted), FAT and swap, and on the other systems their own (UFS/FFS,
  HAMMER2, BFS, ZFS).  NTFS and exFAT partitions, say a Windows install next
  to Linux, are left empty, and `plan` says so;
- multipath, dm-integrity and dm-verity devices.

## Testing

The same layers as dump's tests:

1. `plan` and `status` read only: tested on the fake Debian 13 trees
   dump's scenarios already build.
2. Each writing stage: root-only tests on loop devices in throwaway guests,
   like `holt/tests/root_stack.rs`.  Each one builds a layout, dumps it with
   dump, tears it down, rebuilds it with load on fresh loop devices,
   dumps again, and requires the two dumps to agree on every identifier and
   feature (ZFS vdev GUIDs excepted: see "Identity").
3. Whole machines: HoltTest1D (`testvm/holttest1d/README.md`) dumps a
   Debian 13 guest, backs it up with rustic-bko, runs load through `close`
   from the rescue image on blank disks, boots the result, and compares its
   dump, files and boot with the original's.  `testvm/compare` does the same
   with its own Python `recreate()`; load is meant to replace that code, and
   so gets exercised in every compare round.  HoltTest1All does the same
   for a guest of every supported system.

## Code layout

`holt/`, named after an otter's den, is one crate, `bko-holt`, with one
binary:

- the library: the JSON reader and writer, the on-disk parsers and the
  device scanner (`src/`), shared by both halves;
- `bko-holt dump`, the dumper (`src/report.rs` and the probes);
- `bko-holt load` (`src/load/`): `layout.rs` reads the dump, `target.rs`
  scans the disks here and maps source disks onto them, `commands.rs` works
  out every stage's commands, `plan.rs` and `status.rs` are those stages.

One binary, because the halves share the parsers (load checks every write by
reading it back with dump's) and the rescue stick ships one file.  The cost
is that the half that erases disks is also on every machine that's backed
up, which is why `plan` is the default, nothing is written without
`--commit`, and disks in use are refused.

It keeps dump's rules: no dependencies, builds with Debian 13's cargo, and
the system's own tools for every write.

## Decisions (2026-11-05)

- load runs the archiver's restore itself, in its `restore` stage, when given
  `--repo`, and never copies file data itself.  Without `--repo` an
  administrator can still prepare the disks and drive the archiver by hand.
  (Changed later on 2026-11-05: at first the restore was to stay outside.)
- It runs on the rescue stick, and on QEMU guests for test restores.
- Version 1 rebuilds the same shape: the same disks, each the same size or
  bigger, with the extra space left unused.  Resizing is version 2's.
- The original identifiers are the default; `--new-uuids` gives new ones.
- `holt/` holds one crate and one binary, `bko-holt`, with `dump` and `load`
  subcommands.  (Changed later on 2026-11-05: at first `bko-laydump` and
  `bko-layload` were to stay two tools.)
- The default target directory is `/mnt/bko-target`, for now.
- `close` runs only when asked (`--through close` is the full run), and an
  imported pool left behind is always warned about (see `close`).
- Logs go to the restored system's `/var/log/bko-holt/`, and on the stick
  to its restore logs too (see "Logs"); not to `/boot`.
- Keys follow how the original system unlocked itself (see "Keys follow the
  original system").

## Decisions (2026-11-09)

- A target disk a little smaller or bigger is taken with `--fit-largest PCT`,
  which resizes exactly one partition per disk, the largest, and leaves every
  other partition, every identifier and every other layer alone (see "Fitting
  a disk of another size").  Without the flag everything is as it was.
- The fit is one pure function of the dump, the targets' sizes and PCT,
  applied once to the dump every stage then works from, so no two stages can
  disagree about the geometry.
- Whether the data still fits is decided from free space, not capacity, with
  `max(1 GiB, 5%)` to spare; figures the dump doesn't have refuse the shrink.
