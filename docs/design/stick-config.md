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
| 3 | FAT16, volume label `BACKOTTER`, writable  | 256 MiB |

GPT type "Microsoft basic data", so Linux, macOS and Windows all mount it and a
user can look at the config from any machine.

That's the Linux image.  The BSD, illumos and Haiku images (`make-img-bsd.sh`,
built inside a VM of each system, the BSDs' and Haiku's from their official
media) carry the
same FAT16 partition, made by the same `marker.py`, wherever their own layout
leaves a slot:

| Platform  | Table | Config partition | Root, read-only                 |
|-----------|-------|------------------|---------------------------------|
| linux     | GPT   | 3                | ISO9660 (casper, overlay in RAM) |
| freebsd   | MBR   | slice 3          | UFS2 in a BSD label (slice 2)    |
| netbsd    | GPT   | 3                | FFSv2                            |
| openbsd   | MBR   | 2 (entry 4 is OpenBSD's own) | FFS in OpenBSD's disklabel |
| dragonfly | MBR   | slice 3          | UFS in a disklabel64 (slice 2)   |
| illumos   | GPT   | 4 (illumos slice s3) | UFS RAM disk, loaded from UFS partition 3 |
| haiku     | MBR   | 3                | BFS (partition 1), **read-write** |

None of the BSD, illumos or Haiku images boot with Secure Boot on: their EFI
loaders aren't signed.  Haiku's root is the one that isn't read-only: Haiku
puts a write overlay over its boot volume only on read-only media, so its
image's BFS is mounted read-write, as Haiku's own live USB sticks are (the
system itself is packagefs, read-only; see stick/haiku/README.md).  The illumos image is OmniOS's own USB layout (ESP, boot,
root) with the config partition after it; its root is a copy in RAM, so
nothing on the stick but the config partition is ever mounted.  Below, "the config partition" is that partition whatever its number;
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
/backotter/reports/bko-report-<date-time>.txt  bko-rescue's bug reports (and -UNREDACTED)
/backotter/bin/{bko-holt,rustic-bko}     Linux image only
/backotter/realworld/<date-time>/...     Linux image only; bko-realworld-1a's logs
/backotter/authorized_keys               Linux image only; optional, see below
/README.txt        what this partition is, for someone who finds it in a file manager
```

Every image's partition is 256 MiB: the Linux image's from the start, to
hold the two programs and those logs; the BSDs', illumos' and Haiku's since
2026-11-26 (32 MiB before; holt-json.md's open question 13).  It holds a
system.json of up to 64 MiB, the one before it, and each machine's restore
logs ("Room on the partition" below).  The GRUB entries pass `nopersistent`, or casper would
add a partition in the stick's free space on first boot, moving the backup GPT.

`authorized_keys` is for driving a restore from another machine.  With it,
`key-access` starts sshd at boot and lets those public keys in as `backotter`,
with passwordless sudo.  Without it sshd stays off until someone turns on remote
access at the console.  `stick/add-ssh-key.sh` writes it, to an image or a stick;
release images don't have one.  Its `--host-key` adds
`/backotter/ssh_host_ed25519_key`, so a test bench can pin the stick's identity
across boots.  That's a private key in the open, and for test benches only.

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
  "dump_schemas": [1],
  "config_partition": {"number": 3, "sectors": 524288, "label": "BACKOTTER",
                       "fat_serial": "3F2A-9C71"}
}
```

- `image_id` is a fresh UUID per build.  It ties the config partition to the
  image it was built with (see `stick.json`).
- `platform` is the family of systems the image restores: `linux`,
  `freebsd`, `netbsd`, `openbsd`, `dragonfly`, `illumos` and `haiku`.  It's the
  same word as in the file name (see "Image file names"), but the backup tool
  reads it from here, since a file can be renamed.  `base` is what the image
  happens to be built from (`ubuntu` 26.04.1, `freebsd` 15.1-RELEASE, ...).
- `zfs.features` is OpenZFS's own list for that release
  (`/usr/share/zfs/compatibility.d/openzfs-2.4`).  It and `kernel` let the
  backup tool warn when the stick is too old for the machine (see "Is this
  stick good enough?").  `zfs` is null on an image without ZFS.  illumos' ZFS
  isn't OpenZFS and has no version of its own: its `version` is
  `illumos-<OmniOS release>` (`illumos-r151058`), and its features are what
  that system's `zpool upgrade -v` lists.
- `system_schemas` lists the `system.json` schema versions the stick's restore
  code can read.
- `dump_schemas` lists the dump schemas (the `bko-holt dump` in system.json's
  `layout`) the image's bko-holt load reads.  The image build takes it from
  that bko-holt itself: `marker.py --holt-api` reads its `bko-holt --version
  --json`.  A marker without it is from before 2026-11-26, and its image
  reads dump schema 1 only; so does a bko-holt whose api document has none.
  An image build refuses such a bko-holt (its image would fail the boot
  test): refresh holt-bin first.  A Linux image takes the api document from
  the layer build, which runs the layer's bko-holt in its chroot
  (`ubuntu-server-minimal.backotter.holt-api.json`); the other platforms run
  theirs in the build VM.  Each image's boot test checks the marker's list
  against its bko-holt's (`dump-schemas`); none on both sides passes only
  for a marker with no `dump_schemas` (an image from before them).
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

- `platform` is `linux`, `freebsd`, `netbsd`, `openbsd`, `dragonfly`,
  `illumos` or `haiku`, as in the marker.  One Linux
  image covers every Linux distribution, so the name doesn't say which distro
  or Ubuntu release it's built from; the marker does.
- `version` is BackOtter's, one number for all platforms: the backup tool and
  the stick are matched by schema, and the schemas are shared.  Until the first
  release it's `dev.DATE.COMMIT`, for example `dev.20261030.7230d49`, with
  `.dirty` added when `stick/` has uncommitted changes.  A copy of `stick/` on a
  test host isn't a git checkout: there COMMIT comes from the
  `.backotter-commit` that `testvm/sync-hosts.sh` writes.
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

This document fixes the envelope.  The disks and the boot are recorded in
`layout`, the machine's `bko-holt dump` (below), the one record of the
disks and the boot, the pools' features included.  Schema 1 has no
`storage` or `boot` section of its own (approved 2026-11-26 by the user,
holt-json.md's open question 10).  `backup` depends on the backup engine, still to be
chosen (restic or borg).

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
  "backup": {
    "engine": "restic",
    "repository": {"kind": "sftp", "url": "sftp://nas.local/backotter/lovelace"},
    "repository_id": "a3f9…",
    "credential": {"kind": "prompt"}
  },
  "requirements": {
    "zfs_features": ["encryption", "draid"],
    "kernel_modules": ["nvme", "e1000e", "iwlwifi"]
  },
  "layout": {"format": "bko-holt", "schema": 1, "…": "the bko-holt dump of this machine, whole"}
}
```

- `layout` is the machine's disk layout: the whole `bko-holt dump`
  document, as a JSON object.  The restore reads it with `bko-holt stick
  --copy INSTALL_ID OUT --json`, which checks the file's `.sha256` and
  writes this member to OUT for `bko-holt load` (holt-json.md,
  "bko-holt-stick-copy").  OUT is printed again as dump prints it: the
  same JSON, and the same bytes only when `layout` is held as dump
  printed it.  Approved 2026-11-26 by the user (holt-json.md's open questions 8
  and 10).  A system.json
  without `layout` (none is written yet) gives the restore no layout from
  the stick.  A dump that kept LUKS2 headers has them in the backup, not
  on the stick (below, "LUKS2 headers in the backup"; holt-json.md's open
  question 12, built 2026-11-26).

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
- The dump in `layout` records, per pool, its feature list and its
  `compatibility` property (`grub2` on Ubuntu's `bpool`, for example), in its
  own `storage`.
- `system.json.sha256` is in `sha256sum` format.  Cheap sticks get corrupted
  when yanked, and a damaged config should be reported, not half-used.

### LUKS2 headers in the backup

A dump made with `--keep-luks-header DEV` (an Ubuntu Core's encrypted
partitions always) keeps each LUKS2 header as a file, its size and SHA-256
in `layout.storage.luks_headers`, and load writes it back: the volume key,
the keyslots and the tokens (a TPM's, a recovery key's) come back, so what
opened the disk opens it again.  A header backup is 16 MiB or more by
default, and key material: it goes in the repository (encrypted, no size
limit), never on the stick (approved 2026-11-26 by the user, holt-json.md's
open question 12).

What the backup tool must do (not built: it comes with the backup tool):

1. Run `bko-holt dump --luks-header-dir D`, D a new directory of its own,
   mode 0700, in RAM (dump's default, `/run/bko-holt/luks-headers`, is in
   RAM too).  The dump names each file (`luks2-header-UUID.img`).
2. Back up those files in the same snapshot as the system's files, at
   `/.bko-holt/luks-headers/FILE` (`luksfetch::REPO_DIR` in bko-holt).
   Same snapshot: the headers and the layout are from one moment.  With a
   `SNAPSHOT:SUBPATH` backup they go under SUBPATH, where `luks-headers`
   reads them.  However it stages them, the backup tool writes nothing to
   the system's disks: no mount point is made, or left, on the backed-up
   root, and a read-only root (Ubuntu Core's base snap) works.  How it
   stages them is holt-json.md's open question 14 (*recommended*: a
   staging root in RAM in a private mount namespace, D bind-mounted at
   `S/.bko-holt/luks-headers`, S backed up with `--as-path /`).  load's
   restore leaves `/.bko-holt/luks-headers`, its files, and `/.bko-holt`
   when no raw volume is under it out of the tree, so not even a bare
   mount point comes back.
3. Read them back from the new snapshot with `bko-holt luks-headers
   LAYOUT DIR --repo REPO --password-file FILE --snapshot ID` (DIR a new
   private directory): exit 0 means the snapshot holds every header the
   layout names, each its size and SHA-256.  Anything else is a failed
   backup of that machine: the restore couldn't keep the disks' keys.
   Then wipe D and DIR (each file zeroed, then removed).
4. Only then write system.json (with that `layout`) into the backup and to
   the stick, as below.

At the restore, bko-rescue fetches them once the repository's password is
in and the snapshot chosen, before the first plan: `bko-holt luks-headers`
into `h/` in its session's directory (0700, each file 0600), then every
`load` gets `--luks-header-dir` that directory.  Each file is checked
against the layout's size and SHA-256.  The stick's layout is the newest
backup's, so an older snapshot whose headers differ is refused
(`header_mismatch`) until "Which copy wins" below is built (holt-json.md's
open question 15).  load's restore leaves `/.bko-holt/luks-headers` and its
files out of the tree (and `/.bko-holt`, with no raw volume under it), so
the headers never land on the restored disks.  A layout that kept headers needs a bko-rescue and a
bko-holt from 2026-11-26 or later: an older bko-holt's `stick --copy`
refuses it (`layout_refused`).  So every image's marker.py refuses a
bko-holt whose api `features` lack `luks_headers` (3100b25 or later).

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

### Room on the partition

A system.json is read up to 64 MiB (bko-holt's `SYSTEM_MAX`), and each
machine's restore logs may take 22 MiB: the 10 newest of 2 MiB, and the
one being written.  So before step 2, with the partition mounted, the
backup tool calls `room_check(marker, len, room)` in bko-holt's
`src/stick.rs` (holt-json.md's open question 13, approved 2026-11-26):

- `len` is the new system.json's size in bytes.
- `room` is what the mounted partition says: `free` (statvfs `f_bavail`
  times `f_frsize`), `cluster` (`f_frsize`), whether this machine's folder
  is there (`folder`), the sizes of its `system.json` and
  `system.prev.json` (None where one is missing), whether
  `system.prev.json.sha256` is there (`prev_sum`), the size of each file
  in its `restore-logs/` (None when that folder isn't there), and whether
  other machines have folders on the stick (`others`).  A
  `system.json.new` left by a write that failed is removed first.
- It refuses `system_too_big`, a system.json over `SYSTEM_MAX`: a stick's
  bko-holt wouldn't read it.  It refuses `stick_full` when the file and
  its `.sha256` don't fit: step 2 makes the machine's folder if it isn't
  there, writes the new file while the old two stand, and frees the old
  `system.prev.json` (and its `.sha256`, if there) only after.  On a
  refusal the tool writes nothing to the stick (the backup has its copy,
  written first), says the refusal's words, and the backup itself still
  succeeds.  The approved text said the tool warns when the partition
  can't hold the file; it refuses, since that write would fail anyway.
  What the stick then holds for the machine is holt-json.md's open
  question 16.
- It warns `stick_room_short` when, once written, the partition can't
  also hold the machine's 22 MiB of logs (less what its logs take now,
  plus a cluster for `restore-logs/` when it isn't there).  The tool
  writes, and says the warning's words, as for "Is this stick good
  enough?".
- Each file and folder is counted in whole clusters, each `.sha256` as
  one.  The words round the room down and the need up to 0.1 MiB, so the
  two never print equal.  They say to remove other machines' folders only
  when there are some; else to free room on the partition (old bug
  reports can go).  On an older image's smaller partition they say to
  write a newer image, by its size (256 MiB), not its date.
- bko-rescue's bug reports (`backotter/reports`) take room from the same
  partition and are never removed: holt-json.md's open question 17.

## Finding the config at boot

On the stick, the restore code looks only at the disk it booted from:

1. The medium casper mounted at `/cdrom` gives the boot disk: `findmnt`'s
   SOURCE, then `lsblk`'s PKNAME, or SOURCE itself if casper mounted the whole
   disk (the hybrid image is valid ISO9660 from sector 0 too).  Its
   `/.disk/backotter.json` gives the config partition's number.  The BSD
   images find the boot disk from the device their root is mounted from, the
   Haiku image from the device of `/boot` (its marker is
   `/boot/.disk/backotter.json`).
   illumos' root is a RAM disk, on no disk: there the boot disk is the one
   whose partition of the marker's number has the marker's size, and whose
   first sector has its FAT label and volume serial (random per build, so a
   disk of the machine being rescued doesn't carry it by chance).  Only that
   sector of each disk is read; nothing is mounted to find it.  Every stick
   written from one image matches, so if two do, it can't tell which one it
   booted from: it says so and asks for the other to be removed, rather than
   pick one.
2. It never scans other disks for a `BACKOTTER` label: a second stick, or a
   disk of the machine being rescued, could carry one.
3. The config partition is read through bko-holt, mounted nowhere (`bko-holt
   stick --json`, and `stick --copy` for the layout), and mounted read-write
   only to save a restore log.
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
capped at 2 MiB compressed, keeping the 10 newest.  The new log is written
before the oldest is removed, so a machine's logs take up to 22 MiB.  The
backup tool warns when the partition can't hold them (holt-json.md's open
question 13, "Room on the partition" above).  It's also copied to the
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
- the schema must be in `system_schemas`, and `layout`'s schema (the dump's)
  in `dump_schemas`, or that stick's restore refuses it (holt-json.md's open
  question 11, built 2026-11-26).  A marker with no `dump_schemas` counts as
  `[1]`.  The check is `schema_warnings(marker, system)` in bko-holt's
  `src/stick.rs` (codes `system_schema_unread` and `dump_schema_unread`,
  each with its words): the backup tool calls it before each write.  A
  `layout` that isn't an object gets no dump warning, and the dump's
  `format` isn't checked here (the restore refuses a foreign one);
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
one with exactly the features the dump's `storage` recorded, starting from none:
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
- The restore side's config reader is bko-holt's (`stick --json`, and
  `stick --copy` for a machine's layout); bko-rescue writes the restore logs.
- The schema half of "Is this stick good enough?" is built, as a function
  for the backup tool (`stick.rs`'s `schema_warnings`); the marker carries
  `dump_schemas` from the image's own bko-holt.
- "Room on the partition" is built, as a function for the backup tool
  (`stick.rs`'s `room_check`), and every image's build script makes a
  256 MiB partition (marker.py's `CONFIG_MIB`; each BSD, illumos and Haiku
  `build.sh` checks config.img has its `CONFIG_SECTORS`).  The images in
  use are older: their next build gets it.
- The restore side of "LUKS2 headers in the backup": `bko-holt
  luks-headers` (`src/luksfetch.rs`, also the backup tool's read-back),
  `stick --copy`'s `luks_headers` count, load's restore leaving the
  headers out of the tree, and bko-rescue's fetch before the first plan.
- Not built: the backup tool's stick module (and so no system.json carries a
  `layout` yet), its keeping of the LUKS2 headers in the snapshot (steps
  1 to 4 above), its call of `room_check`, the rest of the write-time check (ZFS features, kernel,
  bcachefs), and `backotter stick repair`.  They come with the backup
  tool.

## Decisions (2026-10-29)

1. The config partition is 32 MiB, FAT16 (FAT32 needs about 33 MiB or more).
   The marker records the size, so a later image can change it.  Changed
   2026-11-26: 256 MiB on every image, still FAT16 (holt-json.md's open
   question 13).
2. Restore logs go on the stick, capped as above, and to the repository.
3. Several machines per stick are supported from schema 1.
4. No key file on the stick; `credential.kind` leaves room for one.
5. `backotter stick repair`, not a reset: fsck first, reformat only on failure.
