# bko-holt

One binary with two halves, for a Linux system's disks:

- `bko-holt dump` writes a JSON description of the disk layout and boot setup
  to standard output: the `storage` and `boot` sections that
  `docs/design/stick-config.md` leaves to their own design doc.
- `bko-holt load` rebuilds the disks from that JSON, in stages, and has the
  archiver (rustic-bko) restore the files into them
  (`docs/design/layload.md`).

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
`VERSION_ID`) is Debian 13, Linux Mint 22, Ubuntu 24.04 and 26.04, Zorin OS 18,
Arch Linux, Fedora 44, Alpine Linux 3, Artix Linux and AerynOS: each passes a
whole-machine round trip (`testvm/holttest1d/README.md`).  Build it static
(`RUSTFLAGS="-C target-feature=+crt-static" cargo build --release --target
x86_64-unknown-linux-gnu`) to run it on musl systems such as Alpine too.  Run both as root: partition tables, superblocks, LUKS headers and
LVM/ZFS labels are read from the devices themselves.  As another user `dump`
falls back to udev's database and says so under `warnings`; `load` won't
plan from such a dump.

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

`bko-holt load --help` lists the stages and flags.  Built so far: `plan`
(the default: every check, and every command the writing stages would run)
and `status` (what's on the target disks now, read from the disks, and which
stage is next).  The writing stages, `partition`, `assemble`, `format`,
`mount`, `restore`, `boot` and `close`, are planned in full but refuse to run
until they're built.

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
