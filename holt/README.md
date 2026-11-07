# bko-holt

One binary with two halves, for a system's disks (Linux, the BSDs, illumos,
Haiku; Redox and Sculpt OS from Linux):

- `bko-holt dump` writes a JSON description of the disk layout and boot setup
  to standard output: the `storage` and `boot` sections that
  `docs/design/stick-config.md` leaves to their own design doc.
- `bko-holt load` rebuilds the disks from that JSON, in stages, and has the
  archiver (rustic-bko) restore the files into them
  (`docs/design/layload.md`).
- `bko-holt report-bug` gathers what a bug report needs into one redacted
  file (see "Reporting a bug" below).

```
sudo bko-holt dump > layout.json
bko-holt dump --compact | jq .storage.topology

bko-holt load layout.json --map vda=/dev/sdb                  # plan: writes nothing
bko-holt load layout.json --only status --map vda=/dev/sdb
bko-holt load layout.json --map vda=/dev/sdb --erase /dev/sdb \
    --repo /srv/backup --password-file /run/pw --through close --commit
```

`dump` refuses to run (exit 3) on a system it doesn't support, and `load`
refuses a layout from one.  The list (`src/osrel.rs`, by os-release's `ID` and
`VERSION_ID`, or uname's where there's no os-release) is:

- Linux: Debian 12 and 13, Devuan 6, Ubuntu 24.04 and 26.04, Linux Mint 22,
  Pop!_OS 24.04, Zorin OS 18, Arch Linux, CachyOS, Artix Linux, Omarchy,
  Alpine Linux 3, AerynOS, Void Linux, Fedora 44, Rocky Linux 10, openSUSE
  Tumbleweed and NixOS 26.05;
- FreeBSD 14 and 15, HardenedBSD 15, GhostBSD 26 (`src/other/freebsd.rs`),
  NetBSD 11, AlpineBSD 1 and OpenBSD 8 (`labelled.rs`), DragonFly 6
  (`dfly.rs`), OmniOS (illumos, `illumos.rs`) and Haiku (`haiku.rs`): built
  for and run on each system itself;
- Redox and Sculpt OS (`offline.rs`), which can't run it: `dump --disk DEV`
  reads them off their disks from Linux, and `load` rebuilds them from Linux.

HoltTest1All round-trips each whole machine (`testvm/holttest1d/README.md`
has which pass).  Build it static (`RUSTFLAGS="-C target-feature=+crt-static"
cargo build --release --target x86_64-unknown-linux-gnu`) to run it on musl
systems such as Alpine and Void too; on the other systems, build it there
(`testvm/holttest1d/build-holt.sh PLATFORM`: it needs nothing but Rust 1.85).
Run both as root: partition tables, superblocks and LVM/ZFS labels are read
from the devices themselves.  As another user `dump` falls back to udev's
database and says so under `warnings`; `load` won't plan from such a dump.

On a system other than Linux, `dump` reads the disks through the system's
own names for them (kern.disks, hw.disknames, diskinfo, /dev/disk) and its
own tools for what only they know (dumpfs -m's newfs command, disklabel's and
disklabel64's text, GEOM labels, HAMMER2's PFSs, wedge names), and keeps the
boot areas that no file holds byte for byte (the MBR's code, FreeBSD's
freebsd-boot, a BIOS boot partition, an ESP nothing mounts).  `load` writes
the partition table itself (`bko-holt write-table`: GUIDs, names and
attributes exactly, and those boot areas), then gives each system's tools
what they printed (disklabel -R, disklabel64 -R -B, newfs, newfs_hammer2,
mkfs -t bfs, zpool create), installs boot blocks from the rescue system's own
(installboot, makebootable), and restores with rustic-bko built for that
system.

bko-holt never copies file data itself.  `load`'s `restore` stage runs
rustic-bko; without `--repo`, run `--through mount`, restore by hand, then
`--only boot`.

## What dump reports

- `host`, `os`, `firmware`: hostname, DMI, kernel, BIOS or UEFI, Secure Boot.
- `storage.topology`: the device stack as a tree, for people to read.
- `storage.disks`, `md_arrays`, `dm_devices`, `loop_devices`,
  `other_devices`: every block device with its size, sector sizes, model and
  serial, `/dev/disk` links, holders and slaves; its partition table read off
  the disk (MBR with logical partitions, or GPT with both headers' CRCs, the
  backup's position, and protective/hybrid MBR); each partition merged with
  the kernel's view; the MBR's boot code (GRUB 2's boot.img and whether its
  core.img is in place); and what each device holds (`content`).
- `content` is parsed from the superblocks: ext2/3/4 (feature names as
  dumpe2fs prints them, and the RAID stride and stripe width), XFS (as
  mkfs.xfs/xfs_db name them, and external log devices), btrfs, FAT, exFAT,
  NTFS, JFS, ISO9660, swap (and hibernation images), LUKS1/2 (cipher, sector
  size, KDF, data offset; never salts or digests), LVM PVs, md 0.90/1.x
  members, ZFS vdevs.
- `storage.lvm`: each volume group from its PVs' metadata (extents, PVs,
  LVs and their segments), tied to the devices.
- `storage.zfs`: pools from their vdev labels (works for exported pools),
  `/etc/zfs/zpool.cache`, and, for imported pools, properties (and those set
  locally), features, the whole vdev tree (log, cache, spare, special and
  dedup vdevs too) and datasets with every local and received property.
- `storage.btrfs`: per filesystem its devices, allocation profiles,
  features, subvolumes (read-only flag and properties), the default
  subvolume, and quotas with their qgroups.
- `storage.xfs_external_logs`: which device holds each XFS external log.
- `storage.mounts`, `swaps`, `fstab`, `crypttab`, `mdadm_conf`, each entry
  resolved to the device it names.
- `boot`: which loader boots the machine and why (`loader.evidence`), the
  UEFI boot entries with device paths decoded, tied to partitions and kept
  raw (`load_option_hex`), the ESPs' files, GRUB's config, grubenv,
  /etc/default/grub and debconf answers, systemd-boot's entries, kernels and
  initramfs, the kernel command line, and the versions of the packages
  behind all of it.

## What load does

`bko-holt load --help` lists the stages and flags.  `plan` (the default)
makes every check and prints every command the writing stages would run;
`status` says what's on the target disks now, read from the disks, and which
stage is next.  The writing stages, `partition`, `assemble`, `format`,
`mount`, `restore`, `boot` and `close`, run those commands with `--commit`
(without it they print them), and read the disks back after each stage to
see it took.  `--through close` with `--repo` is a whole restore.

## Reporting a bug

```
sudo bko-holt report-bug                       # the last load run, found by itself
sudo bko-holt report-bug --stderr errors.txt   # with an archiver's saved error output
sudo bko-holt report-bug -- layout.json --map vda=/dev/sdb   # a load run of your choosing
```

writes `bko-report-DATE.txt` to attach to an issue
(https://github.com/mchobbit/backotter/issues/new/choose): the versions of
bko-holt, restic-bko, rustic-bko, the image (`/.disk/backotter.json`), the
OS and kernel; this system's dump; the last `load` run, which `load` records
in `/run/bko-holt/last-load.json` (command line, exit status, error output),
with the layout it loaded, its plan and Problems and the target disks'
status; and `/run/bko-holt/log/load.log` and `/var/log/bko-holt/`.  `load`
and `dump` point at it when they fail.

It's redacted unless `--no-redact` (`src/redact.rs`).  Every hostname,
machine-id, UUID and GUID (ZFS's too), serial number and WWID, label, user
name (from /home and Ubuntu's USERDATA datasets), key-file path, MAC address,
MBR disk signature, hostid, repository and password-file path gets a
pseudonym, the same one in every form it takes: upper and lower case, with
and without dashes, md's colon form, device-mapper's doubled dashes, GPT's
mixed-endian bytes inside EFI load options and boot code.  Sizes, offsets,
feature flags, partition and filesystem types, kernel device names, models,
pool, VG and LV names and the topology stay, and so do names that identify
no one (installers' default hostnames, labels such as EFI or swap0); the
command prints both lists.  Compressed parts of boot code (GRUB's core.img)
are left as they are.

The plan in the report is the redacted layout's own, checked against the
original's: mapped back through the pseudonyms it must be the same, byte for
byte.  `src/scenarios.rs` checks that for every fake system, and
`tests/redact_dumps.rs` for real dumps:

```
BKO_HOLT_DUMPS=/bkoscratch/holttest1d/r3all cargo test --test redact_dumps -- --nocapture
```

## Building

No dependencies; builds offline with Debian 13's own cargo (1.85):

```
cargo build --release      # target/release/bko-holt
```

## Tests

On a Debian 13 machine (the tests say so and fail anywhere else):

```
cargo test
```

- Unit tests (`src/`): every parser on synthetic bytes, on real data from
  BackOtter's hosts (Chopin's EFI variables, the debian13-zfs guest's
  `zpool.cache` and vdev label in `tests/fixtures/`), and whole fake Debian 13
  systems (`src/scenarios.rs`: BIOS/MBR/btrfs, UEFI/GPT/shim, LUKS+LVM, md
  RAID1, ZFS, unprivileged).  `src/load/`: argument parsing, the layout
  contract, disk mapping and its refusals, and the commands each stage
  would run, on fake rescue systems.
- `tests/realtools.rs`: images made by mkfs.*, mkswap, cryptsetup, sfdisk
  and sgdisk, read by both bko-holt and blkid, dumpe2fs, xfs_db, btrfs,
  cryptsetup and sfdisk.  A missing tool fails its test
  (`BKO_HOLT_ALLOW_MISSING_TOOLS=1` skips instead).  Needs util-linux,
  e2fsprogs, xfsprogs, btrfs-progs, dosfstools, exfatprogs, ntfs-3g,
  jfsutils, cryptsetup-bin, gdisk and xorriso.
- `tests/cli.rs`: `dump`'s arguments, exit codes, the Debian 13 gate, and a
  dump of the machine running the tests checked against `lsblk` (run it as
  root too: then our own superblock reading is what's checked).
- `tests/load_cli.rs`: `load`'s arguments and exit codes.
- `src/redact.rs`, `src/bugreport.rs`: the redaction, and the report.
  `tests/redact_dumps.rs` checks real dumps when BKO_HOLT_DUMPS names a
  directory of them (each `src.json` under it; BKO_HOLT_DUMPS_ALL=1 takes
  every `*.json`).
- `tests/root_stack.rs` (ignored by default): builds md RAID1 + LUKS2 + LVM
  + ext4/XFS and a ZFS mirror on loop devices and checks the dump against
  mdadm, cryptsetup, lvs and zpool.  It changes the running system, so run
  it as root in a throwaway guest, for example a `vm-run --snapshot` of
  testvm's debian13-zfs, with the test binaries copied in:

  ```
  BKO_HOLT_BIN=/root/bko-holt ./root_stack-* --ignored --test-threads 1
  ```

  `BKO_HOLT_BIN` also lets `tests/cli.rs` and `tests/load_cli.rs` run
  outside the build tree.

`BKO_HOLT_TEST_TMP` moves the tests' scratch files (default `$TMPDIR`);
`SOURCE_DATE_EPOCH` fixes the report's timestamp.

Whole machines are tested by `testvm/holttest1d/` (HoltTest1D).
