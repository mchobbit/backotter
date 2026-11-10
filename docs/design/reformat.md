# bko-holt load --reformat: restoring onto another filesystem

Status: approved as proposed and being built (2026-11-10): `--reformat` in
`holt/src/load/reformat.rs`, the boot stage's rewriting in
`holt/src/load/rewrite.rs` (`bko-holt rewrite`), golden plans, and BackShot1's
`--reformat`.  Where the build differs from the text below, "Built so far" at
the end says so.
It's the first piece of what `layload.md` leaves to version 2 ("Changing the
layout: another filesystem, ..."), and it builds the rewriting that
`--new-uuids` needs too, which `plan` refuses today because it isn't built
("it also has to rewrite the restored fstab, crypttab and boot
configuration").

## Goal

Restore a backup onto a different filesystem type from the one it was taken
from, and have the restored system boot: EndeavourOS backed up from btrfs and
restored onto XFS, say, or a Rocky install moved from XFS to ext4.

```
bko-holt load layout.json --map nvme0n1=nvme0n1 --erase /dev/nvme0n1 \
    --reformat /=xfs --repo ... --through close --commit
```

This works because BackOtter never copies a filesystem image.  `format` makes
empty filesystems and the archiver puts the files back, so where a file lands
is the archiver's concern only.  What changes is everything on the restored
system that names, or depends on, the old filesystem: `fstab`, the kernel
command lines, the initramfs, and a few tools and services.  This document is
about those.

Today's restores need none of it, because every filesystem gets its old UUID
back and nothing on the restored system has to change (`layload.md`,
"Identity").

## Scope of this version

In:

- **Linux sources only**, filesystems of type ext4, XFS and btrfs, each
  convertible to either of the other two.
- **One filesystem at a time, named by its mount point**: `--reformat
  MOUNT=TYPE`, given once per filesystem.  It applies to the whole
  filesystem, every subvolume included.  `--reformat /=xfs` on a btrfs
  filesystem mounted as `/`, `/home`, `/var/cache` and `/var/log` (four
  subvolumes) reformats all four.
- **The same partition, so the same size.**  No repartitioning: that's the
  resizing work version 2 keeps.
- **Boot loaders:** systemd-boot (BLS entries on the ESP, with kernel-install
  or without), GRUB (UEFI and BIOS), and Limine.  **Initramfs generators:**
  dracut, mkinitcpio and initramfs-tools.

Out, refused by `plan` with a reason:

- a btrfs filesystem on more than one device (its RAID profile has no
  equivalent);
- a filesystem on ZFS, or one of the other systems' (UFS, HAMMER2, BFS);
- the ESP, and swap: they stay what they were;
- a filesystem the boot loader reads, unless the target type is one that
  loader reads with the features chosen (see "Features").  GRUB with `/boot`
  on the reformatted filesystem is the case; systemd-boot and Limine read only
  the FAT ESP, so for them the root filesystem is free;
- hibernation into a swap file on the reformatted filesystem: the file would
  move, and `resume_offset=` with it.  A swap file elsewhere, or a swap
  partition, is fine;
- a `--reformat` whose mount point isn't in the layout's `fstab`, or whose
  type the filesystem already has.

## What happens to btrfs subvolumes

A btrfs filesystem's subvolumes become **directories of the new filesystem**,
where the source had them mounted.  The four fstab mounts of EndeavourOS's
btrfs (`/@` on `/`, `/@home` on `/home`, `/@cache` on `/var/cache` and
`/@log` on `/var/log`) become one XFS mounted on `/`, with `home`,
`var/cache` and `var/log` as plain directories.  The restore fills them as it
does now: the backup was taken of the mounted tree.

Nested subvolumes that are never mounted on their own (systemd's
`@/var/lib/portables` and `@/var/lib/machines`) become plain directories in
the same way.

A subvolume that isn't mounted at all, such as a snapper or Timeshift
snapshot, has nowhere to go and its contents aren't in a backup of the
mounted tree anyway.  `plan` lists each one: "btrfs 1b64...: @snapshots/42
isn't mounted; it isn't recreated".

The other way, ext4 or XFS onto btrfs, makes **one btrfs with no subvolumes**
and leaves it at that.  Laying out `@`, `@home` and the rest is a policy
choice that belongs to the distro's installer, and inventing one would mean
rewriting even more of the restored system.

## Identity: the UUID stays

The new filesystem gets **the old one's UUID** (`mkfs.xfs -m uuid=`,
`mke2fs -U`, `mkfs.btrfs -U`), as every restore does now.  Then every
`root=UUID=...`, `UUID=` line in `fstab` and `/dev/disk/by-uuid/` link still
finds it, and the rewriting is limited to what really changed: the type, the
mount options, and the subvolume options.  The label is kept too, cut to the
new type's limit if it's longer (XFS: 12 bytes), and `plan` says so.

With `--new-uuids` (once built) the same rewriting also replaces the UUIDs.
That's why it's built as one engine (see "Rewriting the restored system").

## Features

A restore normally recreates the source's own feature set, which its kernel
and tools already handle (`layload.md`, "Feature sets").  A filesystem of a
new type has no source feature set, and the rescue system's `mkfs` is
deliberately the newest, so its defaults could be ones the restored system
can't mount.  Debian 12's 6.1 kernel can't mount an XFS made by the newest
`mkfs.xfs` with its defaults.

So the features come from **the restored system's own kernels and tools**,
which the layout records (`boot.kernels`, `boot.packages`):

- **XFS**: the `mkfs.xfs` profile for the oldest kernel the restored system
  has installed, `-c options=/usr/share/xfsprogs/mkfs/lts_X.Y.conf` with X.Y
  the newest profile no newer than that kernel.  The rescue's xfsprogs ships
  profiles from 4.19 to 6.12.  EndeavourOS has 6.18 (LTS) and 7.2: the 6.12
  profile.
- **ext4**: the features the restored system's `e2fsprogs` version makes by
  default (its fsck has to know them).  `plan` names the list it uses.
- **btrfs**: `mkfs.btrfs -O` with the features that version of `btrfs-progs`
  makes by default.
- **The tools themselves**: the restored system must have the new type's
  userspace, `xfsprogs`, `e2fsprogs` or `btrfs-progs`, or its boot-time fsck
  and repair tools are missing.  `plan` checks `boot.packages` and refuses
  without it, naming the package to install before the backup.
- **GRUB**, when it reads the filesystem: only ext4 is allowed, with GRUB's
  supported feature set.  XFS and btrfs under GRUB wait for a test matrix of
  their own.

## Mount options

Each fstab entry of a reformatted filesystem keeps only the generic VFS
options: `ro`/`rw`, `noatime`/`relatime`/`strictatime`/`lazytime`,
`nodiratime`, `nodev`, `nosuid`, `noexec`, `nofail`, `noauto`, `defaults`,
and `x-systemd.*`/`x-*` ones.  Everything type-specific is dropped and listed
by `plan`: `subvol=`, `subvolid=`, `compress=zstd`, `space_cache=v2`, `ssd`,
`discard=async`, ext4's `data=`, `commit=`, XFS's `logbsize=`, and so on.
Nothing type-specific is added.

So EndeavourOS's `subvol=/@,noatime,compress=zstd` becomes `noatime`.  The
fsck pass becomes 0 for XFS and btrfs (their fsck does nothing at boot), and
1 for an ext4 `/`, 2 for any other ext4.

## Rewriting the restored system

A new step at the start of the `boot` stage, before the initramfs and the
boot loader, so that they're made from the rewritten files.  Each edit is a
small, exact change to a known file, logged with the old and new lines.  A
file that doesn't parse as expected stops the stage, rather than being edited
by guesswork.

| File | Edit |
|---|---|
| `/etc/fstab` | The reformatted filesystem's entries: one for each mount point that's still a mount (`/` here), with the new type, options and pass; the folded subvolume mounts (`/home`, `/var/cache`, `/var/log`) removed.  Comments and every other line kept byte for byte |
| `/etc/kernel/cmdline` (kernel-install) | `rootflags=` cut to its generic options (gone when none are left), `rootfstype=` set to the new type if present |
| BLS entries: `ESP/loader/entries/*.conf`, `/boot/loader/entries/*.conf` | The same on each `options` line.  Fedora's `$kernelopts` lines are left alone; their value is in `grubenv` |
| `/boot/grub2/grubenv` `kernelopts` (Fedora) | The same |
| `/etc/default/grub` `GRUB_CMDLINE_LINUX*` | The same.  `root=` isn't there: `grub-mkconfig` writes it from the mounted root |
| `limine.conf` on the ESP | The same on each `cmdline:`/`kernel_cmdline:` line |
| `/etc/systemd/system/*.mount` | `Type=` and `Options=` of a unit for a reformatted filesystem, by the same rules as fstab |
| `/etc/mkinitcpio.conf`, `/etc/mkinitcpio.conf.d/*` | `btrfs` dropped from `MODULES=` and `BINARIES=` when no btrfs is left.  The new type's module needs no entry, since `autodetect` finds it from the mounted root |

Then, as now, the boot stage remakes the initramfs and the boot loader's
configuration: `grub-mkconfig`, `bootctl install`, and so on.

**Read-back.** Afterwards, the stage lists each initramfs image
(`lsinitrd`, `lsinitcpio`, `lsinitramfs`) and requires the new type's kernel
module in it, unless that kernel has it built in (`modules.builtin`).  It
also requires every loader entry's `options` to have no `subvol=` left.  A
failure stops the stage before `close`, with the system mounted for a look.

**Leftovers.** Last, it searches the restored `/etc`, `/boot` and the ESP for
anything still tied to the old type: `subvol=`, and the old type's name next
to the UUID.  It lists each hit in the log and in `status`.  These are
warnings: they're for the administrator, and BackShot1 shows them in its
report.

### Tools that only make sense on the old type

These are left installed and enabled.  Disabling a user's services is a
policy choice, and most of them stop by themselves on a filesystem they don't
handle.  The boot stage lists the ones it finds: btrfs scrub and balance
timers, snapper configs, Timeshift in btrfs mode, `grub-btrfs` and its
`grub-btrfsd.service`.

One exception, to be confirmed in testing: if a `/etc/grub.d` script such as
`grub-btrfs`'s `41_snapshots-btrfs` makes `grub-mkconfig` fail on a
non-btrfs root, it's made non-executable for that run only and put back
afterwards.

## Plan output

`plan` prints the change before any commands, like this:

```
reformat nvme0n1p2: btrfs 1b64a017-... -> xfs (same UUID, label "")
  xfs features: the lts_6.12 profile (the oldest installed kernel is 6.18.60-1-lts)
  subvolumes folded into directories: @ -> /, @home -> /home, @cache -> /var/cache,
    @log -> /var/log, @/var/lib/portables, @/var/lib/machines
  fstab: / btrfs subvol=/@,noatime,compress=zstd -> xfs noatime (pass 0);
    /home, /var/cache, /var/log: removed (directories of /)
  dropped mount options: subvol=/@ compress=zstd (and the same on the others)
  kernel command lines: rootflags=subvol=/@ removed from /etc/kernel/cmdline
    and 4 loader entries
  space: the snapshot is 7.9 GiB of files; the partition is 236.5 GiB
```

The space check: with `--repo`, `plan` reads the snapshot's total file size
and refuses when it's more than 95 % of the partition.  Compression doesn't
come along to a filesystem without it.

## Code

- `load/mod.rs`: `--reformat MOUNT=TYPE`, repeatable.
- `load/reformat.rs` (new): turns the layout plus the `--reformat`s into an
  edited layout.  The reformatted filesystem gets its new type and features,
  its subvolumes are gone, and its fstab entries are rewritten.  It also
  works out the list of edits for `boot`.  Every other stage then works from
  the edited layout as it is, so `format` and `mount` need no changes.
- `load/rewrite.rs` (new): the edits, each a pure function from the old
  file's text to the new text, unit-tested on the real files of the corpus's
  layouts.  `--new-uuids` adds its UUID edits here later.
- `commands.rs`: the boot stage runs the edits first, then the read-back and
  the leftover search, at the end.  `status` shows the leftovers.

## Testing

1. **Unit tests** for each edit, on the files of every corpus system that
   has them.
2. **Golden plans**: a `reformat-xfs` and a `reformat-ext4` variant
   (`holt/tests/corpus/variants.txt`) for the corpus's btrfs roots: Debian
   13, Fedora 44, Tumbleweed and Void (GRUB, with `/boot` on ext4 or XFS of
   its own), and Omarchy (Limine, mkinitcpio).  EndeavourOS joins the corpus
   first, dumped from its QEMU guest (systemd-boot, kernel-install).  A
   `reformat-btrfs` variant goes on an XFS root (Rocky 10, Alpine) and an
   ext4 one (Ubuntu).  The refusals get golden plans too: Fedora 44 with
   `--reformat /boot=xfs` (GRUB reads `/boot`), and Rocky 10 with
   `--reformat /boot=ext4` allowed.
3. **HoltTest round trips** in QEMU, the same variants: back up, restore
   with `--reformat`, boot, and compare files.  The comparison expects the
   rewritten files and the missing subvolume mounts, and nothing else.
4. **BackShot1 on the Thinkpad**: EndeavourOS from btrfs onto XFS.  It needs
   `backshot1 phase2 RUN --reformat /=xfs` and a verify that expects the new
   type, the rewritten files and the folded mounts.

## Decisions to take

1. **The option**: `--reformat MOUNT=TYPE`.  Not `--format`, which is a
   stage's name.
2. **UUIDs**: the new filesystem keeps the old UUID.  This is proposed above,
   and it keeps the rewriting small.
3. **Subvolumes**: folded into directories, and nothing invented on the way
   to btrfs.
4. **Old-type tools**: left as they are and listed, not disabled.
5. **Scope**: ext4, XFS and btrfs, either way; GRUB-read filesystems onto
   ext4 only.  Or narrower still to start: btrfs onto XFS or ext4 only.
6. **Build order**: btrfs onto XFS first, carried through all four test
   layers on EndeavourOS (systemd-boot, dracut).  Then Fedora (GRUB, BLS,
   dracut), Omarchy (Limine, mkinitcpio) and Debian 13 (GRUB,
   initramfs-tools).  Then the other directions.

## Built so far (2026-11-10)

- `--reformat MOUNT=TYPE` with every check and refusal above, except the
  space check: a snapshot bigger than the partition fails in the restore
  stage instead, with the archiver's ENOSPC.
- The plan's lines are Notes: `reformat SRC: OLD UUID -> NEW ...`, the new
  fstab entry and dropped options, one `fstab's MOUNT: a directory of ...`
  per folded mount, the nested and unmounted subvolumes, and that chattr C/c
  and `btrfs.*` xattrs don't come along.  rustic-bko warns about each of
  those and exits 0; the Restic & Rustic session tested a btrfs restore
  onto XFS with rustic-bko 0.11.4+backotter.6 and .8, strict exit or not.
- `bko-holt rewrite` makes the edits in the table, and with `--check` lists
  each initramfs image and fails on a missing module or a command line still
  asking for a subvolume.  The leftovers and the old type's tools are
  listed in load.log, not yet in `status`.  The `grub.d` exception is waiting
  for a case that needs it.
- The oldest kernel is the lowest of `boot.kernels.installed` and
  `os.kernel_release`: an Arch dump lists no versions (vmlinuz-linux).
- Golden plans (golden-only; a tree without `--reformat` skips them):
  `fedora44` and `void-musl` onto XFS, `debian13` and `tumbleweed` onto ext4
  (their dumps have no xfsprogs, which is refused), and Omarchy's two
  refusals: no xfsprogs (XFS), and hibernation into a swap file on its btrfs
  (ext4).  EndeavourOS joins once the Test2 session's corpus build lands
  with its dump.
- BackShot1: `bs1-restore --reformat`, `backshot1 plan|phase2 NAME
  --reformat ...` and `bs1-qemu.py --reformat`.  bs1-verify checks each
  reformatted mount's type and has `bs1-manifest --compare --reformatted`
  expect the rewritten files and the lost `btrfs.*` xattrs.

### Test results (2026-11-10)

- Golden plans: the six variants above; the existing 48 plans unchanged.
- QEMU (bs1-qemu.py): EndeavourOS ext4 onto XFS, and Manjaro btrfs onto
  ext4 (GRUB, mkinitcpio, four subvolumes folded; the fallback GRUB renewed),
  PASS.
- The Thinkpad (BackShot1, run endeavouros-20261110-090500): EndeavourOS
  backed up live from btrfs (@, @home, @cache, @log; systemd-boot; dracut
  through kernel-install; kernels 6.18 LTS and 7.2), restored onto XFS with
  the lts_6.12 profile: fstab and `/etc/kernel/cmdline` and four loader
  entries rewritten, all four initramfs images read back with xfs.ko, booted
  from NVRAM; bs1-verify PASS, 325,867 files compared, none unexpected.
