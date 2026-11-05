# bko-laydump

Writes a JSON description of a Debian 13 system's disk layout and boot setup
to standard output: the material for the `storage` and `boot` sections that
`docs/design/stick-config.md` leaves to their own design doc.

```
sudo bko-laydump > layout.json
bko-laydump --compact | jq .storage.topology
```

It refuses to run (exit 3) on anything but Debian 13 (`ID=debian`,
`VERSION_ID=13` in os-release).  Run it as root: partition tables,
superblocks, LUKS headers and LVM/ZFS labels are read from the devices
themselves.  As another user it falls back to udev's database and says so
under `warnings`.

## What it reports

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
  dumpe2fs prints them), XFS (as mkfs.xfs/xfs_db name them), btrfs, FAT,
  exFAT, NTFS, JFS, ISO9660, swap (and hibernation images), LUKS1/2
  (cipher, sector size, KDF, data offset; never salts or digests), LVM PVs,
  md 0.90/1.x members, ZFS vdevs.
- `storage.lvm`: each volume group from its PVs' metadata (extents, PVs,
  LVs and their segments), tied to the devices.
- `storage.zfs`: pools from their vdev labels (works for exported pools),
  `/etc/zfs/zpool.cache`, and, for imported pools, properties, features and
  datasets from `zpool`/`zfs`.
- `storage.btrfs`: per filesystem its devices, allocation profiles,
  features, subvolumes and the default subvolume.
- `storage.mounts`, `swaps`, `fstab`, `crypttab`, `mdadm_conf`, each entry
  resolved to the device it names.
- `boot`: which loader boots the machine and why (`loader.evidence`), the
  UEFI boot entries with device paths decoded and tied to partitions, the
  ESPs' files, GRUB's config, grubenv, /etc/default/grub and debconf
  answers, systemd-boot's entries, kernels and initramfs, the kernel command
  line, and the versions of the packages behind all of it.

## Building

No dependencies; builds offline with Debian 13's own cargo (1.85):

```
cargo build --release      # target/release/bko-laydump
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
  RAID1, ZFS, unprivileged).
- `tests/realtools.rs`: images made by mkfs.*, mkswap, cryptsetup, sfdisk
  and sgdisk, read by both bko-laydump and blkid, dumpe2fs, xfs_db, btrfs,
  cryptsetup and sfdisk.  A missing tool fails its test
  (`BKO_LAYDUMP_ALLOW_MISSING_TOOLS=1` skips instead).  Needs util-linux,
  e2fsprogs, xfsprogs, btrfs-progs, dosfstools, exfatprogs, ntfs-3g,
  jfsutils, cryptsetup-bin, gdisk and xorriso.
- `tests/cli.rs`: arguments, exit codes, the Debian 13 gate, and a dump of
  the machine running the tests checked against `lsblk` (run it as root too:
  then our own superblock reading is what's checked).
- `tests/root_stack.rs` (ignored by default): builds md RAID1 + LUKS2 + LVM
  + ext4/XFS and a ZFS mirror on loop devices and checks the dump against
  mdadm, cryptsetup, lvs and zpool.  It changes the running system, so run
  it as root in a throwaway guest, for example a `vm-run --snapshot` of
  testvm's debian13-zfs, with the test binaries copied in:

  ```
  BKO_LAYDUMP_BIN=/root/bko-laydump ./root_stack-* --ignored --test-threads 1
  ```

  `BKO_LAYDUMP_BIN` also lets `tests/cli.rs` run outside the build tree.

`BKO_LAYDUMP_TEST_TMP` moves the tests' scratch files (default `$TMPDIR`);
`SOURCE_DATE_EPOCH` fixes the report's timestamp.
