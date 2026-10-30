# One generic stick, per-machine config on it

Status: draft for discussion. Nothing here is built yet.

## Goal

Build the rescue image once per Ubuntu point release, not once per machine or per
backup.  Everything machine-specific lives in a small writable partition on the
stick, which the backup tool updates in place whenever the stick is plugged in.

Non-goals: the backup tool never partitions or rewrites the stick, formats
only the config partition and only to repair it, and never stores secrets on it.

## Where the config lives

The stick is the image written raw (`dd`, Etcher, GNOME Disks).  Today the image
has two GPT partitions:

| # | Contents                 | Size   |
|---|--------------------------|--------|
| 1 | ISO9660 (read-only)      | 847 MB |
| 2 | EFI system, FAT          | 5 MB   |

`make-img.sh` will append a third:

| # | Contents                                   | Size   |
|---|--------------------------------------------|--------|
| 3 | FAT16, volume label `BACKOTTER`, writable  | 32 MiB |

GPT type "Microsoft basic data", so Linux, macOS and Windows all mount it and a
user can look at the config from any machine.

That's the Linux image.  The BSD images (`make-img-bsd.sh`, built inside a VM
of each BSD from its official media) carry the same FAT16 partition, made by
the same `marker.py`, wherever their own layout leaves a slot:

| Platform  | Table | Config partition | Root, read-only                 |
|-----------|-------|------------------|---------------------------------|
| linux     | GPT   | 3                | ISO9660 (casper, overlay in RAM) |
| freebsd   | MBR   | slice 3          | UFS2 in a BSD label (slice 2)    |
| netbsd    | GPT   | 3                | FFSv2                            |
| openbsd   | MBR   | 2 (entry 4 is OpenBSD's own) | FFS in OpenBSD's disklabel |
| dragonfly | MBR   | slice 3          | UFS in a disklabel64 (slice 2)   |

None of the BSD images boot with Secure Boot on: their EFI loaders aren't
signed.  Below, "the config partition" is that partition whatever its number;
the marker's `config_partition.number` says which.  Because the partition is
part of the image, every raw write of the image creates it; nothing has to edit
a partition table afterwards (the image's GPT is already awkward: its backup
header sits at the end of the image, not the end of the stick).

Writers that copy files instead of the raw image (Rufus in ISO mode, Ventoy)
won't create the config partition.  Both the backup tool and the stick detect
this and say so; such a stick still boots and can restore if the user types in
the backup location by hand.

### Layout of the config partition

```
/backotter/stick.json
/backotter/settings.toml
/backotter/machines/<install-id>/system.json
/backotter/machines/<install-id>/system.json.sha256
/backotter/machines/<install-id>/system.prev.json
/backotter/machines/<install-id>/system.prev.json.sha256
/backotter/machines/<install-id>/restore-logs/<timestamp>.log
/README.txt        what this partition is, for someone who finds it in a file manager
```

There is no index of machines; the directory listing is the index, so nothing
can disagree with it.

## The image marker, inside the image

`make-img.sh` adds `/.disk/backotter.json` to the ISO9660 filesystem (and to
`md5sum.txt`); the BSD images have it at the same path in their root file
system.  It's read-only, so it describes the image, not the stick:

```json
{
  "format": "backotter-image",
  "schema": 1,
  "image_id": "6f1c2c1e-5b0e-4a51-9d8e-2a3c8f4e7b10",
  "built": "2026-10-29T17:12:39Z",
  "platform": "linux",
  "base": {"os": "ubuntu", "release": "26.04.1"},
  "kernel": "7.0.0-30-generic",
  "zfs": {"version": "2.4.1", "features": ["allocation_classes", "..."]},
  "system_schemas": [1],
  "config_partition": {"number": 3, "sectors": 65536, "label": "BACKOTTER",
                       "fat_serial": "3F2A-9C71"}
}
```

- `image_id` is a fresh UUID per build.  It ties the config partition to the
  image it was built with (see `stick.json`).
- `platform` is the family of systems the image restores: `linux`,
  `freebsd`, `netbsd`, `openbsd`, `dragonfly`, and later `illumos`.  It's the
  same word as in the file name (see "Image file names"), but the backup tool
  reads it from here, since a file can be renamed.  `base` is what the image
  happens to be built from (`ubuntu` 26.04.1, `freebsd` 15.1-RELEASE, ...).
- `zfs.features` is OpenZFS's own list for that release
  (`/usr/share/zfs/compatibility.d/openzfs-2.4`).  It and `kernel` let the
  backup tool warn when the stick is too old for the machine (see "Is this
  stick good enough?").  `zfs` is null on an image without OpenZFS.
- `system_schemas` lists the `system.json` schema versions the stick's restore
  code can read.
- `config_partition` describes the config partition as built: its number, size,
  FAT label, and FAT volume serial (random per build, in the form `blkid`
  shows). A stick that was repartitioned or reformatted afterwards no longer
  matches and is refused.  The start sector isn't recorded: the marker is inside
  the image, and adding it moves the partitions that follow.
- There's no BackOtter version field yet, because BackOtter has no releases.
  It comes with the first one.

The ISO9660 volume ID stays Ubuntu's.  GRUB and casper may look for it, and a
marker file is enough to recognise the stick.

## Image file names

```
backotter-live-<platform>-<version>-<arch>.img
backotter-live-linux-0.1.0-amd64.img
backotter-live-freebsd-0.1.0-amd64.img
backotter-live-illumos-0.1.0-amd64.img
```

- `platform` is `linux`, `freebsd`, `netbsd`, `openbsd` or `dragonfly` (later
  `illumos`), as in the marker.  One Linux
  image covers every Linux distribution, so the name doesn't say which distro
  or Ubuntu release it's built from; the marker does.
- `version` is BackOtter's, one number for all platforms: the backup tool and
  the stick are matched by schema, and the schemas are shared.  Until the first
  release it's `dev.DATE.COMMIT`, for example `dev.20261030.7230d49`, with
  `.dirty` added when `stick/` has uncommitted changes.
- `arch` is `amd64` or `arm64`, the names Debian and FreeBSD both use.
- All lowercase, and no `-` inside a field, so a name splits on `-` into a
  fixed prefix and three fields.  `live` leaves room for other kinds of image
  (`backotter-netboot-...`).

- `.img` on every platform: the image is for writing raw to a USB stick, not
  for burning to a disc.  The Linux one is still a hybrid ISO9660 image inside,
  so it also boots as a CD, as a BMC's virtual media may present it.

`make-img.sh` picks this name when it isn't given a file to write.

## stick.json

Shipped in the image with `stick_id` set to null:

```json
{
  "format": "backotter-stick",
  "schema": 1,
  "image_id": "6f1c2c1e-5b0e-4a51-9d8e-2a3c8f4e7b10",
  "stick_id": null
}
```

Every stick written from one image is identical, so the backup tool sets
`stick_id` to a new UUID the first time it writes to a stick.  After that, two
sticks from the same image can be told apart, and the backup tool can remember
which stick it last updated for each machine.

## settings.toml, and editing by hand

The stick is meant to be written once and reused for years: the backup tool
replaces files on the config partition in place, and a person can open the
partition from Linux, macOS or Windows and edit it with any text editor.  The
files are split by who writes them:

- `stick.json` and everything under `machines/` are written by BackOtter only,
  as JSON.  `system.json` has a checksum, so a damaged or hand-edited copy is
  reported instead of used.
- `settings.toml` is for people, and has no checksum.  It's TOML rather than
  JSON because TOML has comments, so the file can explain each setting where
  it's edited, and it forgives more (no quotes around keys, no commas between
  lines).  Shipped in the image as:

```toml
# BackOtter rescue stick: settings you may edit.
#
# Lines starting with # are comments.  Save as plain text.  BackOtter only
# reads this file and never rewrites it, so your comments stay as you leave
# them.  Eject the stick before unplugging it.

format = "backotter-settings"
schema = 1

# A name for this stick, to tell it apart from others.  Remove the # to use it.
# name = "drawer stick"

# Overrides for one machine.  Copy the two lines below once per machine,
# remove the #s, and put in the machine's install ID: the name of its
# directory under backotter/machines/.
#
# [machines."0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90"]
# repository_url = "sftp://nas.local/backotter/lovelace"
```

`name` is a label for the stick ("drawer stick", "offsite"); unset means no
name, since TOML has no null.  `machines` maps an install ID to overrides for
that machine; schema 1 has one, `repository_url`, for when the NAS has moved
since the last backup.

Readers use Python's `tomllib` (3.11 and later).  They strip a UTF-8
byte-order mark first, since some Windows editors write one and TOML doesn't
allow it.  The stick checks the file against the schema and reports what it
can't read, with the line number `tomllib` gives, instead of guessing; an
unknown key is a warning, not an error, so a newer stick's settings don't
stop an older one.

BackOtter never rewrites `settings.toml`: Python can't write TOML without an
extra library, and rewriting would lose the user's comments.  A future
`backotter stick write --name` may fill in `name` when it writes a fresh
stick, from the shipped template.

Anything else on the partition is ignored; macOS leaves `._*` files and
`.fseventsd/` behind.

## system.json

One per machine, under that machine's install ID.  The install ID is a UUID
BackOtter creates on first run and keeps in `/var/lib/backotter/install-id`.
It's not `/etc/machine-id`, which is copied along when someone clones a VM
template, and not the DMI UUID, which changes when the hardware is replaced:
the replacement machine is exactly the case BackOtter exists for.

This document fixes the envelope.  The `storage` and `boot` sections are the
system description, which gets its own design doc.  `backup` depends on the
backup engine, still to be chosen (restic or borg).

```json
{
  "format": "backotter-system",
  "schema": 1,
  "sequence": 42,
  "written": "2026-10-29T21:14:03Z",
  "written_by": "backotter 0.1.0",
  "machine": {
    "install_id": "0b7e…",
    "hostname": "lovelace",
    "dmi": {"system_uuid": "4c4c4544-…", "vendor": "LENOVO", "product": "21CB"}
  },
  "os": {"id": "ubuntu", "version_id": "26.04", "kernel": "7.0.0-30-generic",
         "boot_mode": "uefi", "secure_boot": true},
  "storage": {"…": "system description: disks, partition tables, md, LVM, LUKS, ZFS, filesystems, mounts"},
  "boot": {"…": "loader, ESP, EFI boot entries"},
  "backup": {
    "engine": "restic",
    "repository": {"kind": "sftp", "url": "sftp://nas.local/backotter/lovelace"},
    "repository_id": "a3f9…",
    "credential": {"kind": "prompt"}
  },
  "requirements": {
    "zfs_features": ["encryption", "draid"],
    "kernel_modules": ["nvme", "e1000e", "iwlwifi"]
  }
}
```

- `sequence` goes up by one with each backup of that machine, so "which copy is
  newer" never depends on clocks.
- `repository_id` lets the stick confirm it reached the right repository and
  not just one at the same URL.
- `credential.kind` is `prompt` in schema 1: the stick asks for the repository
  password at restore time.  No secret ever goes in this file.  `kind` is an
  enum on purpose: a later schema could add an opt-in `keyfile` for unattended
  restores, and a stick that doesn't know the value refuses it.  Nothing like
  that is planned; network boot suits unattended restores better than a stick.
- `requirements` is what the stick must support to restore this machine.
  `zfs_features` lists every feature enabled or active on any of the machine's
  pools, not only the ones in use, so the stick can recreate each pool exactly
  (see "A stick newer than the machine").
- The `storage` section records, per pool, its feature list and its
  `compatibility` property (`grub2` on Ubuntu's `bpool`, for example), whatever
  else its own design doc puts there.
- `system.json.sha256` is in `sha256sum` format.  Cheap sticks get corrupted
  when yanked, and a damaged config should be reported, not half-used.

### Which copy wins

The backup tool writes `system.json` into the backup itself first, and only
then to the stick.  So the stick's copy is never newer than the newest backup.
At restore time:

1. Read the stick's copy and use its `backup` section to find the repository.
2. If the repository is reachable and its `repository_id` matches, use the
   `system.json` stored with the backup being restored (the newest one, unless
   the user picks another).
3. If not, restore from the stick's copy, and say how old it is (`written`,
   `sequence`).

## Finding the stick at backup time

The backup tool writes to a stick only if all of these hold:

1. It's a whole disk whose image file system (partition 1's ISO9660 on the
   Linux image; the root UFS/FFS on a BSD image, see the table above) contains a
   readable `/.disk/backotter.json` (mounted read-only, `nosuid,nodev,noexec`).
2. Partition `config_partition.number` exists with exactly the recorded size,
   is FAT with the recorded label and serial, and holds `/backotter/stick.json`
   whose `image_id` matches the marker's.
3. Nothing on the disk is in use: no partition mounted (except by the tool
   itself), no holders in sysfs (md, LVM, LUKS, device-mapper), not part of a
   ZFS pool, and not the disk holding `/`, `/boot` or the ESP in use.
4. It's removable or on USB (`lsblk` RM=1 or TRAN=usb).  Otherwise the tool
   refuses unless given `--allow-fixed-disk /dev/X` naming that exact device,
   which tests in QEMU need.
5. Exactly one disk passes.  If several do, the tool lists them (`stick_id`,
   `name`, size, serial) and needs `--stick`.  It never picks one itself.

If 1 passes but 2 doesn't, the message depends on why: no partition with the marker's number at all
means "written with a file-copying tool, write the image raw"; a partition
that's there but different means "repartitioned or reformatted since writing".

### How it writes

1. Mount the config partition with `-o flush` (and the same
   `nosuid,nodev,noexec`).
2. Write `system.json.new`, fsync it, rename the current file to
   `system.prev.json` (with its `.sha256`), rename `.new` into place, write the
   new `.sha256`, fsync the directory.
3. Unmount, mount again read-only, check the sha256, unmount.

## Finding the config at boot

On the stick, the restore code looks only at the disk it booted from:

1. The medium casper mounted at `/cdrom` gives the boot disk: `findmnt`'s
   SOURCE, then `lsblk`'s PKNAME, or SOURCE itself if casper mounted the whole
   disk (the hybrid image is valid ISO9660 from sector 0 too).  Its
   `/.disk/backotter.json` gives the config partition's number.
2. It never scans other disks for a `BACKOTTER` label: a second stick, or a
   disk of the machine being rescued, could carry one.
3. The config partition is mounted read-only, and read-write only to save a
   restore log.
4. If there's one machine directory, it's proposed.  If there are several, the
   one whose `dmi.system_uuid` matches this machine is preselected, otherwise
   the user chooses.  Before anything is written, a confirmation screen shows
   the hostname, the date of the backup being restored, and every disk that
   will be overwritten (model, serial, size).  Restoring is destructive, and
   with several machines on one stick, picking the wrong one is the real risk.
5. If the checksum fails, the schema isn't in `system_schemas`, or the config
   partition is missing, the stick says so and offers to continue by typing the
   backup location by hand.

### Restore logs

When a restore fails, the repository may be unreachable, the machine's disks
half-written, and the stick's root filesystem is in RAM: the config partition is
the only storage left that survives a reboot.  So every restore, successful or
not, writes `restore-logs/<UTC timestamp>.log.gz` under its machine directory,
capped at 2 MiB compressed, keeping the 10 newest.  It's also copied to the
repository when that's reachable.  Logs name hosts, devices and paths, never
passwords.

## Repairing the config partition

Cheap sticks yanked mid-write are how FAT gets corrupted.  `backotter stick
repair` makes the same checks as a write (marker present, the config partition's
number and size as recorded) and runs `fsck.vfat -a` on the config partition.
Only if that fails, and only after the user confirms, it reformats the config
partition with the recorded label and serial and writes `stick.json` again,
keeping `stick_id` if it could still be read.  It is the one command that
formats anything, and it never touches the rest of the stick.

## Is this stick good enough?

A generic stick can sit in a drawer for years.  At each write, the backup tool
compares `system.json`'s `requirements` with the marker:

- every `zfs_features` entry must be in the marker's `zfs.features`, or the
  stick can't import the pool;
- the schema must be in `system_schemas`;
- for kernel modules, the backup tool can't check the stick's module list from
  the marker alone; schema 1 only warns when the machine runs a newer kernel
  series than the stick (say 7.2 against 7.0);
- a machine with a bcachefs file system can be restored from the Linux image
  only with Secure Boot off: the image's bcachefs module is built by DKMS and
  isn't signed.  The tool warns when the machine has bcachefs and booted with
  Secure Boot on.

A failure is a loud warning, not an error: the config is still written, since
an old stick that can fetch the repository location is better than nothing.

### A stick newer than the machine

The check above only catches a stick that is too old.  A stick whose OpenZFS is
newer than the machine's (2.4 against Debian 12's 2.1, say) passes it, and is
the more dangerous case: a plain `zpool create` enables every feature the
stick knows, some of which go active at once (`spacemap_v2`, `log_spacemap`).
The restored machine's own, older OpenZFS then refuses to import its root pool,
and a restore that looked fine leaves a machine that won't boot.  The same
happens to a `bpool` created without `compatibility=grub2`: GRUB can't read it.

So the restore never creates a pool with the stick's defaults.  It creates each
one with exactly the features `storage` recorded, starting from none:
`zpool create -d -o feature@X=enabled …` for each, plus `-o compatibility=…`
when the pool had one.  A recorded feature the stick doesn't know is left off
(the write-time check already warned about it), and the restore says so; it
fails only when the pool's layout or properties can't do without it (`draid`,
`zstd_compress` for `compression=zstd`, `longname` for names over 255 bytes).
The stick never runs `zpool upgrade` on a pool, new or imported.

Importing a pool that already exists follows OpenZFS's own rules: an unknown
feature that is only enabled doesn't matter, an unknown active feature that is
only needed for writing allows a read-only import, and any other unknown active
feature means the stick can't import the pool at all.

## What's built

- `marker.py` makes the marker and the config partition's image (`mkfs.vfat` and
  mtools, so no root or loop mount) for every platform; `make-img.sh` appends
  it to the Linux image and adds `/.disk/backotter.json`, and
  `make-img-bsd.sh` hands both to a BSD's own `build.sh`.
- `boot-test.py` boots the image as a USB stick by default (`--media cdrom` for
  the old way), runs `stick/PLATFORM/checks.sh`, and checks from inside that the
  live system finds the config partition the way "Finding the config at boot"
  says, and can write to it.
- Not built: the backup tool's stick module, the restore side's config reader,
  restore logs, and `backotter stick repair`.  They come with the backup tool.

## Decisions (2026-10-29)

1. The config partition is 32 MiB, FAT16 (FAT32 needs about 33 MiB or more).
   The marker records the size, so a later image can change it.
2. Restore logs go on the stick, capped as above, and to the repository.
3. Several machines per stick are supported from schema 1.
4. No key file on the stick; `credential.kind` leaves room for one.
5. `backotter stick repair`, not a reset: fsck first, reformat only on failure.
