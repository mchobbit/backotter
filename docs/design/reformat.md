# bko-holt load --reformat: restoring onto another filesystem

Status: approved as proposed (2026-11-10), built on `claude/holt-reformat`
(2026-11-10), and **ported onto main on 2026-11-14**: `--reformat` in
`holt/src/load/reformat.rs`, the boot stage's rewriting in
`holt/src/load/rewrite.rs` (`bko-holt rewrite`), golden plans, and BackShot1's
`--reformat`.  Where the build differs from the text below, "Built so far" at
the end says so, and "Ported to main" says what the port changed.
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

A subvolume that isn't mounted at all, nor below a mount, such as a
Timeshift snapshot, has nowhere to go and its contents aren't in a backup of
the mounted tree anyway.  `plan` lists each one: "reformat nvme0n1p2:
subvolume @snapshots/42 isn't mounted, nor below a mount, so it isn't in the
backup; it isn't recreated".

A snapshot below a mount (snapper's `@/.snapshots/N/snapshot` under a
mounted `/.snapshots`, or any subvolume made as a snapshot of another) *is*
in the backup, as a full tree (`btrfs-snapshots.md`).  The new type can't
hold it as a snapshot, so it isn't restored as a directory either: `plan`
names each one, and the restore leaves its contents out (its
`.snapshots/N` directory stays, empty).  So does a mounted subvolume seen
again below another mount (openSUSE's root at `/.snapshots/1/snapshot`), as
for a btrfs load remakes.  Added in the port (see "Ported to main").

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
  has installed, `lts_X.Y.conf` with X.Y the newest profile no newer than
  that kernel, from 4.19 to 6.12.  EndeavourOS has 6.18 (LTS) and 7.2: the
  6.12 profile.  As built, the profile's features are written into the
  edited layout and `mkfs.xfs` gets each one by name, on or off (exchange,
  parent and metadir off), as for any XFS load makes, rather than
  `-c options=`.
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

## Ported to main (2026-11-14)

The branch was 4 commits on 7a674ad; main had 187 more, many in
`holt/src/load`.  The feature was ported, not merged: the same design and
the same files (`reformat.rs`, `rewrite.rs`, the boot stage's two `bko-holt
rewrite` steps, GRUB's fallback copy renewed, BackShot1's `--reformat`),
fitted to main's load.  What had to change, and why:

- **XFS feature names, for format's read-back** (8c0d005).  `format` now
  reads every filesystem back with dump's own probe and stops on a feature
  it didn't ask for.  A v5 XFS always has `attr2` and `projid32bit`, which
  the branch's profiles left out, so every `--reformat ...=xfs` would have
  stopped in `format`.  The profiles now list them; `mkfs.xfs` names
  exchange, parent and metadir off (main's `mkfs_xfs`), so the plan's
  command lines differ from the branch's.  The read-back's plan line says
  "the features --reformat chose" for a reformatted filesystem.
- **btrfs's free_space_tree** goes in `compat_ro_features`, as a dump and
  the read-back have it.
- **btrfs snapshots and aliases** (main's shared snapshots,
  `btrfs-snapshots.md`): the branch restored snapper's snapshots as plain
  directories, one full copy each, and openSUSE's root twice (once through
  its alias under `/.snapshots`).  `btrfs-snapshots.md` ("Onto another
  filesystem") asks that a `--reformat` name its snapshots and leave them
  out instead: the edited layout carries `load_reformat.restore_globs`
  (shared.rs's `backup_path` and `aliases`), which the restore stage adds to
  its `--glob-file`.  Tumbleweed's golden plan shows it.
- **Refused for more sources**: ostree (Kinoite, Bazzite), ABRoot
  (Vanilla OS) and the store systems (NixOS, Guix), whose fstab and boot
  configuration their own tools write, and whose boot stages aren't the
  one that runs `bko-holt rewrite`.  And `--reformat` with `--fit-largest`
  (main's), which would resize the new filesystem from figures it doesn't
  have.
- **More files rewritten**, for boot setups main gained: `/etc/default/limine`
  (CachyOS's limine-entry-tool, from which `mkinitcpio -P`'s hook writes
  `limine.conf` again), `/etc/cmdline.d/*.conf`, and rEFInd's
  `refind_linux.conf` and `refind.conf` (PikaOS).  The initramfs check
  lists dracut's, mkinitcpio's and initramfs-tools' images (and
  limine-mkinitcpio-hook's `MACHINE-ID/KERNEL/initramfs`); booster,
  mkinitfs and tiny-initramfs images are logged as not checked.
- **plan**: `--reformat` is applied to the JSON in `load::run`, before
  `target::prepare` (main's --fit-largest step); `plan::check` applies it
  itself for a caller that didn't.
- **Golden plans**: the six of the branch, blessed again against main's
  corpus dumps, plus `endeavouros+reformat-xfs` (its dump is in the corpus
  now: ext4 onto XFS, systemd-boot).  `golden_plans.rs` tells a guest's
  plan from a variant's by name, since a +reformat plan has no growth
  either.  The 72 existing plans are unchanged.
- **BackShot1**: `bs1-restore --reformat` next to main's `--fit-largest`;
  `bs1-manifest --compare --reformatted` on main's comparison (aliases,
  snapper, ostree), expecting `etc/default/limine`, `etc/cmdline.d/*`,
  `boot/refind_linux.conf` and the left-out `.snapshots/N/snapshot/*` too.
  GRUB's renewed fallback (`EFI/BOOT/BOOTX64.EFI`) was already expected
  (`boot/efi/*`, `efi/*`), as judge.py's `BOOT_STAGE` excuses it
  (`^boot/efi[^/]*/EFI`).

Not done in the port: the space check; `status` showing the leftovers; and
a Thinkpad run of the ported code.  (judge.py's dump and fs forms were
taught the type change afterwards: "Judged by judge.py", below.)

### Port test results (2026-11-14, on cinqcent)

- `cargo build --offline`, `cargo test --offline`: every test passes, the
  new ones included (reformat.rs: snapper snapshots and aliases left out,
  btrfs features, the other systems refused; rewrite.rs: limine defaults,
  rEFInd; load's `--reformat` arguments).  golden_plans passes: 79 plans of
  69 dumps.  (With main's `testvm/test2/classes.json` beside it, it reports
  HoltTest2's `ubuntu24-luks` and `ubuntu24-uefi`, which have no corpus
  dump at e79a453, as before the port.)
- Real `mkfs` (Debian 13's xfsprogs 6.13.0, e2fsprogs 1.47.2, btrfs-progs
  6.14) on image files, with the argv load builds for the edited layout,
  then format's read-back: fedora44, void-musl and endeavouros onto XFS;
  debian13, tumbleweed, fedora44 and rocky10 onto ext4; endeavouros onto
  btrfs: all read back as asked.
- shellcheck: `backshot1`, `bs1-restore` and `bs1-verify` have the same
  findings as before; `bs1-manifest --compare --reformatted` checked on
  hand-made manifests (rewritten fstab, lost btrfs.* xattrs, mke2fs's
  lost+found and a left-out snapper snapshot are expected; without the
  flag, each is unexpected).

### Review fixes (2026-11-14)

- `bko-holt rewrite` takes an fstab it rewrote already (the mount has the
  new type's line, none of the old's) as done, so the boot stage can run
  again (`--only boot`, or `--through close` after it stopped); before, it
  stopped with "no btrfs line for / in it".
- `bs1-restore --reformat`: the diff after the restore lists every file
  under what the restore left out (load's `restore.globs`: a reformatted
  btrfs's snapshots and aliases) as removed; those are taken off the count
  of differences, and the globs kept in the attempt's directory.  Without
  it, any snapper source (CachyOS, Tumbleweed) would have stopped there.

### Round trips of the ported code (2026-11-14)

main 9718f04's static musl bko-holt, rustic-bko 0.11.4+backotter.11,
`ht1.sh` with `HT_REFORMAT` (testvm/holttest1d/README.md), run `rfmt1`, in
RAM on Cinqcent, Copland and Cage.  Each restored disk booted by itself,
with the source's boot state and health; every refusal, the dry run and
each stage passed.  RESULT is FAIL in each, as judge.py isn't taught the
change, and every DIFF is the change's:

| Source | Onto | Boot | What differs, all of it expected |
|---|---|---|---|
| `debian13` (btrfs `@rootfs`) | `/=ext4` | GRUB BIOS, initramfs-tools | fstab and the kernel command line (`rootflags=subvol=` gone); the fs probe; mke2fs's `lost+found`; NOCOW (`C`) gone from the journal, ext4's `htree` index flag on big directories.  Both initrds have `ext4.ko` |
| `fedora44` (btrfs `root`, `home`) | `/=xfs` | GRUB BIOS, BLS, dracut | fstab (`/home` folded), `/etc/kernel/cmdline` and the four BLS entries; NOCOW and btrfs's no-compress flag gone; XFS's `trusted.SGI_ACL_*` beside the same POSIX ACLs.  Four initramfs images with `xfs.ko` |
| `tumbleweed` (btrfs, 8 subvolumes, snapper: 7 snapshots after `prep/tumbleweed-snapper.sh`'s rollback) | `/=ext4` | GRUB BIOS, dracut | the snapshots and the root's alias `.snapshots/8/snapshot` left out (290,964 files, all under `.snapshots/`; the hard-link groups differ only by those paths), so GRUB's snapshot menu is gone; NOCOW gone from `/var`; `lost+found`.  snapper's configs and timers listed as left over |
| `endeavouros` (ext4) | `/=xfs` | systemd-boot, dracut via kernel-install, UEFI | fstab (pass 0), the fs probe, `trusted.SGI_ACL_*`.  Both initrds have `xfs.ko` |

The same build without `--reformat` (`debian13`, run `rfmt-plain`, Cage):
PASS, 0 differences.

BackShot1 in QEMU (bs1-qemu.py, the 20261112 `--test` image, on Copland):

- bs1-restore's count of differences after the restore was wrong twice,
  both fixed now.  rustic-bko .11 doesn't count the top directory as added,
  so the one added directory the count took for it was mke2fs's
  `lost+found`, which `--reformat` then took off again: Manjaro `/=ext4`
  stopped on "-1 files differ".  And the `Symlinks:` totals line has no
  space before its colon, so its fields were off by one and symlinks were
  never counted: Tumbleweed's 1,303 left-out snapshot symlinks came off the
  count without having been on it ("-1303").  bs1-restore now takes an
  added entry for the top directory only when the diff names fewer than it
  counts, and reads the symlink totals too.
- Tumbleweed (BIOS, `--bios --virtio`) `/=ext4`: restore PASS (67,318
  left-out snapshot entries and `lost+found` taken off, 0 differences), the
  restored system boots and answers, / is ext4, the package list and units
  as before.  bs1-verify fails on one check only: four `/boot` files'
  SELinux labels, which the boot stage's `setfiles` sets to the policy's
  (tumbleweed's update left them `modules_object_t` and `usr_t`); this
  happens without `--reformat` too, and judge.py excuses it, but
  bs1-manifest's comparison doesn't yet.  Every reformat difference
  (30,293 missing snapshot files, 18 added, 362 changed) was expected.
- Manjaro `/=ext4`: restore PASS, but the restored boot reaches OVMF's
  shell; so does the same run without `--reformat`.  The guest has no NVRAM
  of its own: the source boots from OVMF's automatic NVMe entry, which is
  gone once the rescue boot is over, and the restore has no entry to make.
  A harness matter, not this feature's.

### Judged by judge.py (2026-11-14)

The round trips above ended "RESULT: FAIL" only because judge.py didn't know
the change; and two of the BackShot1 rehearsals failed on the harness, not
the restore.  Fixed:

- **judge.py `--reformat MOUNT=TYPE`**, both forms, which ht1.sh passes from
  `HT_REFORMAT`.  The dump form (`reformat_expect`) edits the source's dump
  into what load predicts, as reformat.rs and rewrite.rs work it out: the
  filesystem's record compared by type, UUID, usage and label only (XFS's
  label cut to 12 bytes); its btrfs record gone; every other fstab mount of
  it folded (fstab, mounts, `mounted_at`, backup sources); MOUNT's fstab entry
  with the new type, the generic options and the pass (1 for an ext4 `/`, 2
  for another ext4, else 0); its mounts' root `/`; and for `/`, every kernel
  command line in the dump through rewrite.rs's `cmdline` (`rootflags=` cut to
  its generic options, `rootfstype=`), and GRUB's btrfs snapshot entries left
  out (openSUSE's first entry is snapper's, with no kernel, so the dump's
  default isn't compared there).  Everything else is compared as ever, and a
  wrong type, a new UUID, a kept `subvol=`, a mount not folded or a wrong pass
  is a DIFF (judge-test.py's Reformat and FsReformat, each case with a twin).
  The fs form (`reformat_why`) allows rewrite.rs's files, mke2fs's
  `lost+found` at MOUNT onto ext4, the snapper snapshots left out (and the
  hard-link groups without their paths), btrfs's NOCOW and compression flags
  gone (nothing gained), and onto XFS its `trusted.SGI_ACL_*` copies beside
  the same POSIX ACLs.
- **ht1.sh's btrfs read-only snapshot check** takes a snapshot missing after
  the restore as left out under `HT_REFORMAT`.
- **bs1-manifest** allows a file under `/boot` whose extended attributes differ
  in its SELinux label only: the boot stage's `setfiles` (judge.py's
  BOOT_STAGE already did).  Tumbleweed's one failing bs1-verify check.
- **bs1-qemu.py**: a source that booted through the firmware's own entry for
  the disk (BootCurrent with no `HD()` node: Manjaro, with no NVRAM of its
  own) has nothing for load to recreate, and its restored boot now gets the
  disk first as the source's did (`bootindex`), so OVMF takes the ESP's
  removable-media loader, which load renews.  Before, the rescue boot's
  `bootindex` on the stick had dropped the disk's automatic entry, and
  OVMF's shell came ahead of the one it made anew.  Real firmware boots the
  removable path by itself (bs1-restore: "the firmware will choose").

Reruns with main's bko-holt (dcd04db, holt/ as 5744ff0; static musl, Rust
1.94.1, sha256 25a4812b…) and the same rustic-bko, run `rfmt2`:

| Run | Host | Result |
|---|---|---|
| HoltTest1 `debian13` `/=ext4` | Cinqcent | PASS (0 differences, 16 ignored; fsmeta 0, 353 from the boot stage) |
| HoltTest1 `fedora44` `/=xfs` | Copland | PASS (0 differences, 16 ignored) |
| HoltTest1 `tumbleweed` `/=ext4`, `prep/tumbleweed-snapper.sh` | Cage | PASS (six snapshots left out; 0 differences, 10 ignored) |
| HoltTest1 `endeavouros` `/=xfs` | Cinqcent | PASS (0 differences, 6 ignored; fsmeta 0, 27 from the boot stage) |
| bs1-qemu.py Tumbleweed `/=ext4` `--bios --virtio` | Cage | PASS (bs1-verify: 6 `/boot` relabels, none unexpected) |
| bs1-qemu.py Manjaro `/=ext4` | Copland | PASS (the restored disk booted its removable-media loader) |
| bs1-qemu.py Manjaro, no `--reformat` | Copland | PASS (the same way) |
