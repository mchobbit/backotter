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
- `bko-holt recovery-key-file OUT` turns Ubuntu Core's recovery key, read
  from standard input, into the key file `cryptsetup open --key-file` and
  `load --key-file NAME=OUT` take (`src/snapcore.rs`).
- `bko-holt stick --json` reads the rescue stick's config partition (its
  machines' `system.json`) off the disk the system booted from, without
  mounting it, for bko-rescue (`src/stick.rs`, `src/fatfs.rs`;
  docs/design/holt-json.md, "bko-holt-stick").
- `bko-holt stick --copy INSTALL_ID OUT --json` reads it the same way (that
  machine's system.json only) and writes its disk layout to a new private
  file OUT for `load`: the `bko-holt dump` its system.json carries as
  `layout`, parsed from the bytes whose `.sha256` it checked and printed as
  dump prints it (`src/stick.rs`; holt-json.md, "bko-holt-stick-copy").
- `bko-holt --version --json` lists the dump schemas `load` reads
  (`dump_schemas`), which an image build copies into the stick's marker.
  `stick::schema_warnings` is the backup tool's write-time check against
  that marker: system.json's schema and its layout's (holt-json.md, open
  question 11).  `stick::room_check` is its room half: a system.json over
  `SYSTEM_MAX` (64 MiB), or one the partition can't take, is refused, and
  one that leaves no room for the machine's restore logs warns (open
  question 13; stick-config.md, "Room on the partition").
  `stick::stick_write` is what the backup tool calls: the whole file; else
  one without its layout (`layout_left_out` says why), so the stick still
  finds the backup, with the machine's older copies removed first when
  only that makes room; else nothing, the older copies removed (open
  question 16).
- `bko-holt recovery-words OUT --json` checks a BackOtter recovery key, its
  words on standard input, and writes the repository password it stands
  for to a new private file OUT, for bko-rescue (`src/recoverycmd.rs`;
  holt-json.md, "bko-holt-recovery").
- `bko-holt luks-headers LAYOUT DIR --repo REPO --password-file FILE
  [--snapshot ID] [--json]` fetches the LUKS2 headers a dump kept
  (`storage.luks_headers`) from the backup, where the backup tool keeps
  them (`/.bko-holt/luks-headers/FILE` in the snapshot), into a new 0700
  DIR, each 0600 and checked against the dump's size and SHA-256, for
  `load --luks-header-dir DIR` (`src/luksfetch.rs`; holt-json.md,
  "bko-holt-luks-headers", open question 12).  load's restore leaves that
  directory and its files out of every tree, whatever the layout kept,
  and `/.bko-holt` when no raw
  volume is under it.
- `bko-holt snapshot-layout OUT --repo REPO --password-file FILE
  [--snapshot ID] [--install-id ID] [--json]` writes the disk layout a
  snapshot's own system.json carries (`/.bko-holt/system.json` in the
  snapshot, where the backup tool keeps it) to a new private OUT for load,
  checked as `stick --copy` checks the stick's.  A snapshot from before the
  backup tool kept one is `no_system_json`, so bko-rescue falls back to the
  stick's copy for it only (`src/snaplayout.rs`; holt-json.md,
  "bko-holt-snapshot-layout", open question 15).  load's restore leaves
  `/.bko-holt/system.json` out of every tree.

```
sudo bko-holt dump > layout.json
bko-holt dump --compact | jq .storage.topology

bko-holt load layout.json --map vda=/dev/sdb                  # plan: writes nothing
bko-holt load layout.json --only status --map vda=/dev/sdb
bko-holt load layout.json --map vda=/dev/sdb --erase /dev/sdb \
    --repo /srv/backup --password-file /run/pw --through close --commit
bko-holt load layout.json --map vda=/dev/sdb --fit-largest 5   # onto a smaller disk
```

`dump` refuses to run (exit 3) on a system it doesn't support, and `load`
refuses a layout from one.  The list (`src/osrel.rs`, by os-release's `ID` and
`VERSION_ID`, or uname's where there's no os-release) is:

- Linux: Debian 12 and 13 (Proxmox VE 9 on it, ZFS root; TrueNAS CE 25.10 on 12, its boot environments), Devuan 6, Ubuntu 24.04 and 26.04, Linux Mint 22,
  Pop!_OS 24.04, Vanilla OS 3, Zorin OS 18, KDE neon 24.04, AnduinOS 2, elementary OS 8, Arch Linux, CachyOS, Artix Linux, Omarchy,
  EndeavourOS, Manjaro, Garuda Linux, BigLinux, Alpine Linux 3, AerynOS, Solus 4, Void Linux (also on a ZFS root with ZFSBootMenu, its boot environments), Chimera Linux,
  Gentoo Linux, PikaOS 4, Kali Linux, Fedora 44 and 45, Bazzite 44, Rocky Linux 10, openSUSE
  Tumbleweed, openSUSE Leap 16, openSUSE MicroOS and Aeon (transactional-update, any snapshot: their own boot arm,
  docs/design/aeon.md), NixOS 26.05 and Guix System (no VERSION_ID; its store GRUB-less, docs/design/guix.md),
  Slackware 15 (and -current, by its VERSION_CODENAME, at VERSION_ID 15.0 or
  16.0 and on x86_64 only; any other development tree refused,
  docs/design/slackware.md), Qubes OS 4.3 (dom0, `src/qubes.rs`; 4.2 and
  older refused) and Ubuntu Core 24 (`src/snapcore.rs`);
- ostree (image-mode) systems among them: Bazzite 44, and Fedora's atomic
  desktops (Silverblue and Kinoite 44, which say `fedora 44`; not 45), told apart
  by ostree's own markers (`src/ostree.rs`).  `load` version 1 refuses
  some of their layouts: BIOS boot, LUKS, LVM, no separate /boot, composefs
  `verity` or `signed` ("What load does" below);
- ABRoot v2 (Vanilla OS 3, `src/abroot.rs`): two read-only roots on LVM
  thin LVs, the init LV with both roots' kernels, /var and /etc's overlay.
  `dump` reads the root that isn't running and the init LV through its own
  read-only mounts and lists them in `storage.backup_sources` with the
  mount the backup must make (`from: null`); `load` makes the thin pool by
  `lvcreate` and regenerates nothing at boot.  It refuses ABRoot v1, layouts
  without thin provisioning, BIOS boot and a dump taken during an upgrade
  (docs/design/vanillaos.md);
- FreeBSD 14 and 15, HardenedBSD 15, GhostBSD 26 (`src/other/freebsd.rs`),
  NetBSD 11, AlpineBSD 1 and OpenBSD 8 (`labelled.rs`), DragonFly 6
  (`dfly.rs`), OmniOS and OpenIndiana Hipster (illumos, `illumos.rs`;
  OpenIndiana has no os-release, so its `/etc/release` stands in) and Haiku
  (`haiku.rs`): built for and run on each system itself;
- Redox and Sculpt OS (`offline.rs`), which can't run it: `dump --disk DEV`
  reads them off their disks from Linux, and `load` rebuilds them from Linux.

HoltTest1All round-trips each whole machine (`testvm/holttest1d/README.md`
has which pass; Bazzite's is `kinoite44`, its Kinoite base, and BackShot1
is its test on real hardware; Chimera's are `chimera`, `chimera-uefi` and
`chimera-zfs` in HoltTest1Obscure, and BackShot1 ran it on the Thinkpad with
root on ZFS; Gentoo's are `gentoo`, the Thinkpad's OpenRC install in small,
and `gentoo-systemd`, LVM on LUKS, in HoltTest1G, and BackShot1 is its test
on the Thinkpad itself; Solus's is `solus49` in HoltTest1Obscure, ext4 under Secure Boot with its ESP at /boot and out of fstab: `dump` refuses until the ESP is mounted (`clr-boot-manager mount-boot`), `load` keeps clr-boot-manager's shim, systemd-boot, kernels and entries from the restored ESP and makes only the NVRAM entry; Slackware's are `slackware15`, LILO in the MBR with the huge kernel and no initrd, and `slackware15-uefi`, ELILO in `EFI/Slackware` with mkinitrd's initrd on XFS, in HoltTest1Obscure (not run yet; golden plans from live dumps): `load` reruns `lilo` on a copy of lilo.conf with root= by name, or remakes the initrd and copies it to the ESP, and restores onto disks that keep the source's device names (Slackware -current too, by its VERSION_CODENAME, with geninitrd's `/boot/initrd-VERSION.img`: the Thinkpad's pinned snapshot, `bs1-slackware-current` in the corpus); AnduinOS's is `anduinos2` in HoltTest1U, Btrfs
under Secure Boot, whose MOK-signed GRUB `load` keeps from the restored ESP;
KDE neon's is `neon24` in HoltTest1U, ext4 under Secure Boot through shim in
`EFI/neon`: `load` runs `grub-install --bootloader-id=neon` and `update-grub`
(BackShot1 passed on the Thinkpad, installed by `neon-bootstrap.sh`);
elementary OS's are `elementary8`, distinst's layout (ext4 / on LVM, VG
`data`) under Secure Boot through shim in `EFI/ubuntu`, and
`elementary8-luks`, the same LVM on LUKS2 with an ext4 /boot, in HoltTest1U:
`load` runs `grub-install --bootloader-id=ubuntu` and `update-grub`
(registered, not yet run to a PASS; docs/design/elementary.md);
PikaOS's are `pikaos4`, XFS with rEFInd's NVRAM entry, and `pikaos4-luks`,
Btrfs on LUKS2 booted from the fallback path, in HoltTest1Obscure: `load`
rebuilds booster's initramfs and keeps rEFInd's files; Kali's is `kali` in HoltTest1Obscure, UEFI GRUB with no shim on ext4, which `load` reinstalls as Debian's; Proxmox VE's are `proxmox9`, ZFS root on one disk, and `proxmox9-mirror`, a mirror with an ESP on each disk, in HoltTest1D: `dump` reads proxmox-boot-tool's unmounted ESPs, `load` formats them with their volume IDs and has `proxmox-boot-tool init` and `refresh` rebuild them; TrueNAS CE's are `truenas25`, UEFI with three boot environments, and `truenas25-mirror`, BIOS on a mirrored `boot-pool` beside a data pool `load` leaves alone, in HoltTest1D: `dump` backs up the inactive boot environments too, `load` recreates every one read-only as it was and reinstalls TrueNAS's GRUB on each disk and ESP; Garuda's is `garuda`,
beside EndeavourOS and Manjaro, GRUB on btrfs with dracut through
garuda-dracut-support, whose `dracut-rebuild` `load` runs, and snap-pac's
snapper snapshots restored as shared ones; BigLinux's is `biglinux`, beside
them too, GRUB under its own `EFI/BigLinux` id on btrfs, whose Timeshift
snapshots at the top level are outside the backup and not made, and whose
/boot `load` leaves uncompressed, as BigLinux's installer does; Vanilla
OS's is `vanilla3` in HoltTest1Obscure, ABRoot on a thin pool under Secure
Boot, whose restored disk boots root B by default and A from GRUB's
"Previous State (A)", each as the source did; openSUSE Leap 16's is
`leap16` in HoltTest1Obscure beside `tumbleweed`, Btrfs with snapper's
snapshots and SELinux enforcing, under Secure Boot through SUSE's shim in
`EFI/opensuse`: `load` keeps the restored ESP's shim and signed GRUB, runs
dracut and `grub2-mkconfig` and makes the NVRAM entry; Void's are
`void-musl` and, on a ZFS root booted by ZFSBootMenu with two boot
environments, glibc's `void-zbm` and `void-zbm-enc`, the latter under
native encryption with its key file in each initramfs, in HoltTest1V:
`dump` backs up the inactive boot environment too, `load` recreates both
(the clone as a full copy), takes the passphrase by `--key-file`, rebuilds
the default one's initramfs, installs no loader and makes both NVRAM
entries; docs/design/zfsbootmenu.md).  Build it static
(`RUSTFLAGS="-C target-feature=+crt-static" cargo build --release --target
x86_64-unknown-linux-gnu`) to run it on musl systems such as Alpine and Void
too; on the other systems, build it there
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
rustic-bko with `RUSTIC_STRICT_EXIT=true` (its `--strict-exit`, from
0.11.4+backotter.8; an older one ignores the variable), so a restore that
couldn't set metadata or make a special file exits 1 and fails the stage,
as restic-bko's would; without `--repo`, run `--through mount`, restore by
hand, then `--only boot`.

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
- `storage.softraid_volumes` (OpenBSD): each softraid volume `bioctl
  softraid0` lists, as a virtual disk with a disk's MBR, disklabel and
  partitions, and under `softraid` its level, chunks, what unlocks a CRYPTO
  one (passphrase or key disk) and its KDF rounds, read off the chunk's
  softraid metadata; its chunk's disklabel partition says
  `softraid_volume` (`src/other/softraid.rs`).
- `storage.zfs`: pools from their vdev labels (works for exported pools),
  `/etc/zfs/zpool.cache`, and, for imported pools, properties (and those set
  locally), features, the whole vdev tree (log, cache, spare, special and
  dedup vdevs too) and datasets with every local and received property.
- `storage.btrfs`: per filesystem its devices, allocation profiles,
  features, subvolumes (read-only flag and properties), the default
  subvolume, and quotas with their qgroups.
- `storage.xfs_external_logs`: which device holds each XFS external log.
- `storage.mounts`, `swaps`, `fstab`, `crypttab`, `mdadm_conf`, each entry
  resolved to the device it names; a mount that is a directory of another
  mount of the same filesystem (a bind) says which (`alias_of`).
- `storage.sys_tz_minuteswest` (Linux, the running system only): the
  kernel's time zone, minutes west of UTC, as `hwclock --systz` set it.
  Linux's vfat keeps FAT's local-time stamps through it unless the mount
  says `tz=UTC` or `time_offset=`, so load mounts a restored vfat/msdos
  filesystem with the source mount's own `tz=`/`time_offset=`, else with
  `time_offset=` from this (`tz=UTC` when it is 0, whatever the rescue's
  own time zone), and its files come back with the source's on-disk
  stamps. An older dump without it: nothing added. fstab's `auto` counts
  when the source had vfat/msdos mounted there.
- `storage.backup_sources`: the filesystems a whole-system backup reads,
  each at the path fstab mounts it (`path`) and where the running system
  has it (`from`), aliases left out; `storage.physical_root` is the one at
  `/`.  On ostree `/` is read from /sysroot and `/home` from /var/home.
- `boot`: which loader boots the machine and why (`loader.evidence`), the
  UEFI boot entries with device paths decoded, tied to partitions and kept
  raw (`load_option_hex`), the ESPs' files, GRUB's config, grubenv,
  /etc/default/grub and debconf answers, systemd-boot's entries, kernels and
  initramfs, the kernel command line, and the versions of the packages
  behind all of it.  A systemd-boot entry (`boot.systemd_boot.entries`) is
  an object of its `key value` lines; its `options` lines are joined with
  spaces, as systemd-boot joins them, and when it has more than one
  `initrd` line `initrds` lists them all in order (`initrd` is the last
  one's, as before, often the initramfs after a microcode image).
- `boot.ostree`, on an ostree system (Fedora's atomic desktops, Bazzite):
  the booted deployment, every deployment, the loader entries and the
  deployment each leads to, composefs's and the repository's settings,
  bootupd's state file, grubenv's boot counting keys, and bootc's image.

## What load does

`bko-holt load --help` lists the stages and flags.  `plan` (the default)
makes every check and prints every command the writing stages would run;
`status` says what's on the target disks now, read from the disks, and which
stage is next.  The writing stages, `partition`, `assemble`, `format`,
`mount`, `restore`, `boot` and `close`, run those commands with `--commit`
(without it they print them), and read the disks back after each stage to
see it took.  `--through close` with `--repo` is a whole restore.

On an ostree system (a dump with `boot.ostree`) the tree is fstab's, as on
any other: the physical root at the target, with /boot, the ESP, /home and
/var beneath it (the mount stage leaves out fstab's `ro`, on every system).
Its boot stage regenerates nothing and runs no chroot: /boot, the ESP and
/ostree come back as they were.  It checks that each loader entry's kernel,
initramfs and deployment are in the restored tree, makes the UEFI entries,
writes its log with /var/log's SELinux label, and puts the atimes back
(`src/load/ostree.rs`).  Version 1 refuses composefs `verity` or `signed`,
required fs-verity, BIOS boot, LUKS, LVM, and an fstab without `/` or
without a separate /boot.

On OpenBSD, a root in a softraid CRYPTO volume (bioctl's full-disk
encryption) is made again on its chunk's new partition: `bioctl -c C` with
the source's KDF rounds and a new passphrase (asked for twice, or
`--key-file`), the volume's own MBR (`fdisk -iy`) and the source's
disklabel and DUID; its partitions are named by that DUID, and installboot
runs on the volume.  `--key-file NAME=FILE` (NAME: the dump's device, e.g.
sd1, or its DUID) is copied with mode 0600 and a final newline, as bioctl
wants.  Close leaves the volume attached (`bioctl -d` would clear the
bootable flag installboot set) and, if the target stays busy with no file
open, unmounts it with `umount -f`.  Other softraid levels and key-disk volumes are dumped,
and refused by `load` (`docs/design/openbsd-crypto.md`).

Each target disk must be at least as big as its source, with the same
logical sector size.  `--fit-largest PCT` takes one a little smaller or
bigger: per disk, the largest partition contracts or expands by at most PCT
percent (1 to 50) and everything else keeps its size, identifiers and all.
Nothing is resized in place; `load` remakes the filesystems anyway, so the
fit only chooses other numbers, once, before anything is erased, and `plan`
prints what it changed.  It's for Linux layouts, one filesystem, LUKS or LVM
on the partition, and it refuses the shrink unless the dump's own figures
show the data still fits with room to spare (`src/load/fit.rs`,
`docs/design/layload.md`).

## Reporting a bug

```
sudo bko-holt report-bug                       # the last load run, found by itself
sudo bko-holt report-bug --stderr errors.txt   # with an archiver's saved error output
sudo bko-holt report-bug -- layout.json --map vda=/dev/sdb   # a load run of your choosing
```

writes `bko-report-DATE.txt` to attach to an issue
(https://github.com/mchobbit/backotter/issues/new/choose): the versions of
bko-holt, restic-bko, rustic-bko, the image (`.disk/backotter.json` on the
image's root, its boot volume or, on Linux, `/cdrom`), the OS and kernel;
this system's dump; the last `load` run, which `load` records in
`/run/bko-holt/last-load.json` (`/var/run/bko-holt/` on systems other than
Linux; command line, exit status, error output), with the layout it loaded,
its plan and Problems and the target disks' status; and that directory's
`log/load.log` and `/var/log/bko-holt/`.  `load` and `dump` point at it when
they fail.

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

## Recovery keys

`src/recovery.rs` makes and reads a repository's recovery key: 12 words
from BIP39's English list (`src/bip39-english.txt`, bitcoin/bips' file as
it is), chosen at random (128 bits from the system's random source and
BIP39's 4-bit checksum).  The words, in full, lower case and one space
apart (`recovery::password`, a form that must never change), are the
password of a second repository key (`rustic key add`): no cryptography of
our own, and nothing derived from the passphrase.  `recovery::check` reads
them as people type them back (any case and spacing, the first 4 letters
of each word) and names what's wrong by position: an unknown word with
suggestions, the wrong count, or a checksum that doesn't fit.  Nothing uses
it yet.  `bko-recovery-words` tries it by hand; it is built with bko-holt
but not shipped (the images take `bko-holt` alone):

```
bko-recovery-words new
bko-recovery-words check aban abil able ...   # or the words on standard input
```

## Building

No dependencies; builds offline with Debian 13's own cargo (1.85):

```
cargo build --release      # target/release/bko-holt (and bko-recovery-words)
```

The shipped Linux binary is built on Chopin by
`testvm/holttest1d/build-holt.sh linux` with rustup's 1.94.1, named in
`RUSTUP_TOOLCHAIN` (`HOLT_RUST_TOOLCHAIN` changes it), never the host's
rustup default: that is the Rust the shipped builds have used, so a host
whose default changes (Chopin's reinstall reset it) still builds the same.
Holt itself still builds with 1.85 (`rust-version`).  Without that
toolchain it stops and says how to add it (`rustup toolchain install 1.94.1
--profile minimal`).  The other systems build with their own packaged Rust,
1.85 or later.

## Tests

On a Debian 13 machine (the tests say so and fail anywhere else):

```
cargo test
```

- Unit tests (`src/`): every parser on synthetic bytes, on real data from
  BackOtter's hosts (Chopin's EFI variables, the debian13-zfs guest's
  `zpool.cache` and vdev label in `tests/fixtures/`), and whole fake
  systems (`src/scenarios.rs`: a directory tree for each machine's /sys,
  /proc, /dev, /etc and /boot and image files for its disks, laid out as
  the supported systems' installers and BackOtter's test guests lay real
  ones out, through the full dump, redaction and plan: boot setups, stacks
  of LUKS, LVM, md, ZFS and btrfs, and the systems with a boot arm of their
  own, as the list of supported systems above has them).  `src/load/`: argument parsing, the layout
  contract, disk mapping and its refusals, `--fit-largest`'s arithmetic and
  refusals (`src/load/fit.rs`, over every scenario's layout as well), and the
  commands each stage would run, on fake rescue systems; ostree's
  boot-stage check and log labeller run on fake restored trees
  (`src/load/ostree.rs`).
- `tests/realtools.rs`: images made by mkfs.*, mkswap, cryptsetup, sfdisk
  and sgdisk, read by both bko-holt and blkid, dumpe2fs, xfs_db, btrfs,
  cryptsetup and sfdisk; and `--fit-largest`'s own numbers, by applying the
  sfdisk script and mkfs commands it plans to smaller images and reading the
  result back (f2fs and NILFS2 too, so it needs f2fs-tools and nilfs-tools).  A missing tool fails its test
  (`BKO_HOLT_ALLOW_MISSING_TOOLS=1` skips instead).  Needs util-linux,
  e2fsprogs, xfsprogs, btrfs-progs, dosfstools, exfatprogs, ntfs-3g,
  jfsutils, cryptsetup-bin, gdisk and xorriso.
- `tests/cli.rs`: `dump`'s arguments, exit codes, the Debian 13 gate, and a
  dump of the machine running the tests checked against `lsblk` (run it as
  root too: then our own superblock reading is what's checked).
- `tests/load_cli.rs`: `load`'s arguments and exit codes;
  `tests/json_cli.rs`: the `--json` protocol as a program (docs/design/holt-json.md).
- `tests/golden_plans.rs`: `load`'s plan of every dump in the corpus
  (`tests/corpus/GUEST/src.json`, redacted dumps of BackOtter's test guests)
  against the plan kept beside it (`plan.txt`), and five invariants each
  plan must keep (`tests/common/invariants.rs`).  A change to load that
  changes a plan fails it: `BKO_HOLT_GOLDEN=report
  BKO_HOLT_PLANS_OUT=DIR` writes the new plans and what differs to DIR,
  `BKO_HOLT_GOLDEN=bless` rewrites the plans that differ (on Linux, in the
  same commit as the change), and `tests/corpus/README.md` says how the
  corpus was made and how to read a difference.  Its tests are named
  `golden_*`: GateTest2 runs the rest with `cargo test -- --skip golden_`
  and these on their own, in report mode.  `tests/plan_json.rs` holds each plan's `--json`
  document (`plan.json`) the same way (`BKO_HOLT_JSON_GOLDEN=bless`).
- `tests/bs2_plan.rs`: BackShot2's tier 0 (docs/design/backshot2.md), each
  drifted layout planned and held to the same invariants; a no-op unless
  `BS2_PLAN_JOBS` names a file of jobs (results to `BS2_PLAN_OUT`).
- `tests/corpus_build.rs` (ignored by default): builds the corpus from raw
  dumps, `BKO_HOLT_CORPUS_RAW` to `BKO_HOLT_CORPUS_OUT`, on the host that
  holds them (`tests/corpus/README.md`, "Rebuilding it").
- `tests/readme.rs`: this README's list of supported systems against
  `src/osrel.rs`, and that it names each command.
- `src/recovery.rs`: BIP39's published test vectors (128-bit entropy), round
  trips, abbreviations, every problem by position, and what a one-word
  mistake can get past; `tests/recovery_cli.rs`: `bko-recovery-words`.
- `src/redact.rs`, `src/bugreport.rs`: the redaction, and the report.
  `tests/redact_dumps.rs` checks real dumps when BKO_HOLT_DUMPS names a
  directory of them (each `src.json` under it; BKO_HOLT_DUMPS_ALL=1 takes
  every `*.json`).
- `tests/root_stack.rs` (ignored by default): builds md RAID1 + LUKS2 + LVM
  + ext4/XFS and a ZFS mirror on loop devices and checks the dump against
  mdadm, cryptsetup, lvs and zpool.  It changes the running system, so run
  it as root in a throwaway guest, a `vm-run --snapshot --memory 3072` of
  testvm's debian13-zfs, with the test binary and `bko-holt` copied in and
  mdadm, cryptsetup, lvm2, xfsprogs and dosfstools installed (last passed
  2026-11-07, bko-holt 0.2.3):

  ```
  BKO_HOLT_BIN=/root/bko-holt ./root_stack-* --ignored --test-threads 1
  ```

  `BKO_HOLT_BIN` also lets `tests/cli.rs` and `tests/load_cli.rs` run
  outside the build tree.

`BKO_HOLT_TEST_TMP` moves the tests' scratch files (default `$TMPDIR`);
`SOURCE_DATE_EPOCH` fixes the report's timestamp.

Whole machines are tested by `testvm/holttest1d/` (HoltTest1D).

## License

GPL-3.0-or-later (`LICENSE`, a copy of the top-level one, so the crate builds
from `holt/` alone).  The binary carries the text: `bko-holt --license`.
