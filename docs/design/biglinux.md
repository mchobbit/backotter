# BigLinux support

2026-11-14. The user approved this spec on 2026-11-14 with every
recommended decision (the last section). BigLinux is a Brazilian
desktop distribution built on Manjaro's stable branch. The task is to support
it the way EndeavourOS and Manjaro were supported (commit 6715f43), following
the shape of `chimera.md`, `gentoo.md` and `bazzite.md`.

**Conventions.** est. marks an estimate. *Check* marks a fact that neither the
sources nor the ISO's index state; it gets settled on the first guest, before
any code that depends on it. BigLinux's facts come from its own repositories
on github.com/biglinux, all read on 2026-11-14 at their default branches:

- `biglinux-livecd`, where the Calamares profile now lives
  (`usr/share/biglinux/calamares-profiles/biglinux/`, plus the
  `btrfs-fix` and `grubcfg-fix` modules and `biglinux-install-setup.sh`);
- `iso-profiles` (`biglinux/kde/`, the common root overlay and
  `build-iso/`);
- `biglinux-name`, `faster-mkinitcpio`, `grub-btrfs-timeshift`,
  `big-first-boot` and `biglinux-hibernate-in-swapfile-btrfs`;
- the ISO index and package list at iso.biglinux.com.br.

Code references are to local `main` at 6a78448.

## Summary

- BigLinux becomes a supported system for bko-holt and both archivers, with
  one QEMU guest, `biglinux`. It is built by `make-arch.sh --live` from
  BigLinux's own ISO and follows its Calamares defaults. It sits beside
  `manjaro` in HoltTest1All and in Test2's family 2A.
- Most of what it needs already exists. It is Manjaro underneath: pacman,
  mkinitcpio, UEFI GRUB with its ESP at /boot/efi, and btrfs `@`/`@home`.
  bko-holt already restores the `manjaro` guest.
- What is new is BigLinux's own layer, which no supported system has yet:
  - **Timeshift in btrfs mode.** `big-first-boot` turns it on at the first
    boot and takes daily snapshots. They are subvolumes at the top of the
    file system, outside every mount, so they are not in the backup, and
    today's `load` would make each one again as an *empty* snapshot of `@`.
  - **grub-btrfs**, which runs `41_snapshots-btrfs` inside `grub-mkconfig`.
  - **`compress-force=zstd`**, with /boot and /usr/share/grub rewritten
    uncompressed by the installer's `btrfs-fix`.
  - A mixed-case GRUB id, `EFI/BigLinux`.
- ~2.1 d (est.) without BackShot1, ~2.6 d with it.

## Why

- **It is the main pacman distribution with Timeshift's btrfs mode on by
  default.** Manjaro, EndeavourOS and CachyOS leave snapshots off, or use
  snapper. A real BigLinux machine has Timeshift snapshots within a day of
  being installed. So what `load` does with snapshots it can't restore stops
  being a corner case.
- **Low cost.** It is the third `--live` guest. Every piece but the
  Timeshift handling copies Manjaro's.

## What BigLinux is (as far as bko cares)

| | BigLinux | Nearest supported system |
|---|---|---|
| Release | Rolling, on Manjaro stable with BigLinux's own repos (`packages.biglinux.com.br`: biglinux-stable, -testing, -update). ISOs are dated, built with three kernels each; the newest is **2026-10-03** (`k612`, `k618`, `k72`; 5.1 GiB), with `.md5`, `.pkgs` and `.torrent` files but **no signature** | Manjaro |
| os-release | Manjaro's, rewritten by `biglinux-name`'s pacman hook (it runs on `filesystem`, `lsb-release` and `manjaro-release` upgrades): `ID="biglinux"`, `NAME="BigLinux based on Manjaro Linux"`. `ID_LIKE=arch` and `BUILD_ID=rolling` are left as they are; no VERSION_ID (*check*) | Manjaro, EndeavourOS (empty version) |
| Installer | Calamares 3.3.14 (Manjaro's build) with BigLinux's profile, copied over `/etc/calamares` at launch by `calamares-biglinux` | Manjaro |
| Disk | Manual partitioning is the default (`initialPartitioningChoice: none`). Defaults: btrfs, ESP at `/boot/efi` (Calamares's default size, 300 MiB, *check*), **no swap** (`userSwapChoices: none`; zram from `biglinux-systemd-swap`), LUKS2 if asked | Manjaro (300 MiB ESP at /boot/efi) |
| btrfs | Subvolumes `@` → `/` and `@home` → `/home` only (no `@cache`/`@log`, which Timeshift needs); options `defaults,noatime,compress-force=zstd`, plus `discard=async` on an SSD or `autodefrag` on a disk. After the install `btrfs-fix` runs `btrfs filesystem defragment --nocomp -r` on `/boot` and `/usr/share/grub` | Manjaro (four subvolumes, `compress=zstd:1`) |
| Image | Calamares's `unpackfs` names only `manjaro/x86_64/rootfs.sfs`, but `build-iso.sh` cleans a desktopfs too (*check*: the ISO's `.sfs` files, and the `unpackfs.conf` inside its live image) | Manjaro (rootfs + desktopfs) |
| Initramfs | mkinitcpio 41 with `HOOKS="base systemd autodetect kms modconf plymouth block filesystems fsck"` and zstd. `faster-mkinitcpio` cuts each preset to `('default')`, so there is no fallback image | Manjaro (mkinitcpio, busybox hooks) |
| Kernel | Manjaro's `linux618` (6.18.49 on the k618 ISO): `/boot/vmlinuz-6.18-x86_64`, `initramfs-6.18-x86_64.img` | Manjaro |
| Loader | GRUB 2.14 x86_64-efi. Calamares's `bootloader` module with BigLinux's branding `bootloaderEntryName: BigLinux` gives `EFI/BigLinux/grubx64.efi`, plus the fallback `EFI/boot/bootx64.efi` (*check*). `GRUB_DISTRIBUTOR="BigLinux"`, `GRUB_DEFAULT=saved`, `GRUB_DISABLE_OS_PROBER=false`, a theme. Manjaro's `install-grub` hook reinstalls GRUB on every grub upgrade, and `grub-btrfs-timeshift`'s `99-grub-efi-fallback` copies the new loader over the fallback | Manjaro |
| Snapshots | `timeshift` 25.12 and `grub-btrfs` 4.14 with `grub-btrfsd --timeshift-auto`. At the first boot, `big-first-boot` runs `timeshift --btrfs`, turns on `schedule_daily`, sets `btrfs_use_qgroup` to false and runs `timeshift --check` (which takes the first snapshot, *check*). Snapshots go in the top-level volume at `timeshift-btrfs/snapshots/DATE/@`, beside an `info.json` | Tumbleweed (snapper, inside `/.snapshots`, so in the backup) |
| Swap | zram (`biglinux-systemd-swap`, *check* whether swapfc can add files under `/swapfile`). A hibernate swapfile `/swapfile/hibernate` is made only for hibernation and removed afterwards | none |
| Drivers | Calamares runs `big-driver-manager-cli gpu install` outside the chroot (mhwd underneath) | Manjaro (mhwd skipped) |
| Keyrings | `archlinux`, `manjaro`, `biglinux` (`biglinux-keyring`); `SigLevel = Required DatabaseNever` | Manjaro |
| Services | bluetooth, cronie, ModemManager, NetworkManager, cups, fstrim.timer, pkgfile-update.timer, ufw, nmb, smb, dnsmasq, cups-browsed, sddm; `pacman-init` masked; `big-first-boot` (once, then it removes itself), `grub-btrfsd` | Manjaro |

## What already works, and what doesn't

**bko-holt `dump`:**

- `holt/src/osrel.rs` `SUPPORTED` has no `biglinux` row, so the OS check
  refuses it. It needs `("biglinux", "", "BigLinux")` in the Arch-based
  block, plus a unit test from the guest's real os-release. `ID_LIKE=arch`
  isn't used to decide support, and stays unused: the row is matched by ID.
- `installed_packages()` reads pacman already. `PACKAGES` (`boot.rs:225`)
  gains the boot-relevant names a BigLinux dump has that no list holds:
  `install-grub`, `update-grub`, `grub-btrfs`, `timeshift`,
  `grub-btrfs-timeshift`, `faster-mkinitcpio` and `plymouth`. The guest's
  dump settles the final list. `testvm/update/fingerprint.py` mirrors it,
  as a test enforces.
- The btrfs reader already records every subvolume with its `parent_uuid`,
  so Timeshift's snapshots are in the layout. Nothing new is needed here.

**bko-holt `load`:**

- **Initramfs:** `mkinitcpio -P` (`commands.rs` `initramfs()`) already runs
  for every pacman system. BigLinux's single `default` preset just builds
  one image. Nothing changes.
- **GRUB:** the `("grub", true)` arm runs `grub-install
  --efi-directory=/boot/efi --bootloader-id=ID --no-nvram`, then
  `grub-mkconfig`. `bootloader_id()` (`commands.rs:2840`) **lowercases** the
  id it finds, so BigLinux would get `--bootloader-id=biglinux`. FAT ignores
  case, so the files still land in `EFI/BigLinux`. But the plan would no
  longer say what the installer did, and `grub-install` writes a
  `BigLinux`/`biglinux` mix depending on what already exists. The fix keeps
  the source's spelling (it compares without case, and returns the
  original). Every golden plan's id is lowercase already (`chimera`,
  `debian`, `endeavouros`, `gentoo`, `manjaro`, `ubuntu`), so no other plan
  changes.
- **grub-btrfs inside the chroot:** `grub-mkconfig` runs
  `/etc/grub.d/41_snapshots-btrfs`. It calls `btrfs subvolume list` on `/`
  and may exit non-zero when it finds no snapshots, or when `/run` isn't the
  restored system's (*check*). No supported system has had grub-btrfs
  before. If it fails, `remake_grub_cfg()` runs it with
  `GRUB_BTRFS_ENABLED=false`, the same way it already binds `/usr/bin/true`
  over os-prober, and adds a note. `grub-btrfsd` rebuilds the submenu on
  the first boot anyway.
- **Compression of /boot:** the restore writes `/boot` and `/usr/share/grub`
  through a `compress-force=zstd` mount, and the boot stage's mkinitcpio
  does too, so both end up compressed. The installer had deliberately
  left them uncompressed. GRUB 2.14 reads zstd, so it boots either way
  (*check*), but the restored files are no longer laid out as the source's.
  On BigLinux, the boot stage ends with the installer's own `btrfs-fix`
  step: `btrfs filesystem defragment --nocomp -r` on those two directories
  of the target, run after the initramfs and the grub.cfg are written. The
  step keys on os ID `biglinux` and on a btrfs `/boot`, so no other golden
  plan changes.
- **Timeshift's snapshots** (the one real design point; Decision 3).
  `format` makes every subvolume in the layout. A Timeshift snapshot
  (`timeshift-btrfs/snapshots/2026-11-14_10-00-01/@`, parent `@`) is made as
  a snapshot of `@`, which is still empty at that point. It has no backup
  path (`load/shared.rs` "Arch's @snapshots unmounted", in its unit test),
  so it stays empty, and its directory has no `info.json`. What a restored
  system would then have:
  - Timeshift lists the snapshot, or doesn't, depending on how it treats a
    missing `info.json` (*check*). If it does list it, **restoring that
    snapshot replaces `/` with an empty tree**.
  - grub-btrfs skips a snapshot with no kernel (*check*).
  - The empty subvolumes take no room, but they look like restore points.

  The recommendation is to leave them out. `dump` marks a subvolume as a
  Timeshift snapshot when it matches `timeshift-btrfs/snapshots/*/@*` at the
  top level, isn't mounted and has no backup path. `load` doesn't make
  those, and it doesn't make their `timeshift-btrfs/snapshots/DATE`
  directories either. It notes "N Timeshift snapshots (DATES) are not in
  the backup; not made". `/etc/timeshift/timeshift.json` comes back as it
  was, so Timeshift keeps its schedule and takes a new snapshot on the next
  day it is due. The rule is generic, because Mint or Manjaro with
  Timeshift in btrfs mode have the same snapshots, but it touches only
  layouts that have such subvolumes. No golden plan has one today.

**SticTest1 / SticTest2:** the archivers are static binaries, and
`compress-force=zstd` is a mount option they don't see. `manjaro` and the
EndeavourOS guests have no SticTest1 entry; Test2's report already says
"stic: no check for manjaro". The recommendation is the same for
`biglinux`: no SticTest1 entry for now (Decision 5), and a GuestTest1 grade
of "not covered by SticTest1". SticTest2 needs nothing, because it runs
the existing SticTest1 checks.

**BackShot1** (`testvm/realworld/backshot1`): `bs1-adopt` already handles
pacman systems and an empty VERSION_ID (Manjaro, row 6 of `bs1-all.md`), so
no script changes are expected. `bs1-manifest`'s expected changes already
cover `boot/initramfs-*` and `boot/grub/*`. Two things are new. The
manifest has to expect Timeshift's top-level snapshots to be gone (they
were never in the backup), and a `grub-btrfs.cfg` rewritten in `/boot/grub`.
A BigLinux install on LUKS would use mkinitcpio's `sd-encrypt` (systemd
hooks), which `bs1-luks-unattended.md`'s family E (busybox `encrypt`)
doesn't cover. That stays out of scope.

**The rescue image:** no change ([[one-kernel-rescue]]).

## The guest

`make-arch.sh --live biglinux ISO 2026.10.01 WORK_DIR`. As for `manjaro`, it
boots the Arch ISO with BigLinux's ISO as the second CD. `install-live.sh`
gets a `biglinux` branch that does what BigLinux's Calamares does on a blank
disk, offline:

- **Disk:** GPT; a 300 MiB ESP at `/boot/efi` (`defaults,umask=0077`); the
  rest btrfs, with `@` and `@home` mounted
  `defaults,noatime,compress-force=zstd`, plus `discard=async`, because the
  guest's virtio disk is non-rotational (*check*
  `/sys/block/vda/queue/rotational`; with `1`, `autodefrag` instead, as
  Calamares would choose). No swap. 24 GiB, as for the other `--live`
  guests (*check* the used space after install: above 60 %, 32 GiB).
- **Image:** the `.sfs` layers the ISO's own `unpackfs.conf` names, read
  from the live image, not assumed.
- **The chroot steps,** in Calamares's order: machine id, fstab
  (`subvol=/@` and `subvol=/@home`), locale, `pacman-key --init` and
  `--populate archlinux manjaro biglinux`, `mkinitcpio -P`, the services from
  `services.conf` (except the display managers, and with `multi-user.target`
  as the default, since the guest runs in text mode like `manjaro`),
  `pacman-init` masked, `grub-install --bootloader-id=BigLinux` plus the
  fallback `EFI/boot/bootx64.efi`, `biglinux-install-setup.sh`'s GRUB edits
  (`GRUB_SAVEDEFAULT=false`, `GRUB_EARLY_INITRD_LINUX_STOCK=''`), the serial
  console on `GRUB_CMDLINE_LINUX_DEFAULT`, `grub-mkconfig`, then btrfs-fix's
  `defragment --nocomp`. The drivers step is skipped, as mhwd is for
  `manjaro`. The live user and live packages aren't in `rootfs.sfs`
  (*check*).
- **`big-first-boot` stays enabled**, as on a real install. It runs at the
  guest's first boot, which is its first vm-update. There it turns on
  Timeshift and grub-btrfsd, takes the first snapshot, and runs
  `pacman -R` on the VM tools it doesn't need. `in-guest.sh` must wait for
  `big-first-boot.service` to finish before it runs pacman, or the two
  fight over `db.lck`.
- **The checks at the end:** os-release says `biglinux`, grub.cfg names
  `subvol=/@`, `/boot/initramfs-*-x86_64.img` exists, there is no
  `-fallback.img`, and `EFI/BigLinux/grubx64.efi` exists.

**Inputs:** `biglinux_2026-10-03_k618.iso` (5,466,023,936 bytes, MD5
`3df976984b1255aa91683e570d4c154e`) with its `.md5` and `.pkgs`, in
`/bkoscratch/rebuild-inputs/biglinux/` on Chopin and Cinqcent. BigLinux
publishes no signature, so the build checks the MD5 and also a SHA-256
that BL1 writes there and records in `rebuild-one.sh`'s comment. A rebuild
then refuses any other file (Decision 2).

The guest gets the usual pieces: a `rebuild-one.sh` line, the
`make-arch.sh` `--live` list, `vm-run-options` `--uefi --no-secure-boot`,
`ram-sizes.tsv` (est. D 20, MEM 4 until a run measures it; EndeavourOS
took 17), and both hosts (built on Chopin, copied to Cinqcent with the
same SHA-256, [[split-chopin-cinqcent]]). The small hosts get it through
the image store's next publish, not by hand. Then vm-update (`in-guest.sh`:
`biglinux-keyring` in the keyrings line, and the wait for big-first-boot),
and a GuestTest1 grade.

**HoltTest1** needs one Timeshift snapshot on the source every time. The
first boot ought to take one (*check*), but a run shouldn't depend on it.
So, following `prep/tumbleweed-snapper.sh`, `prep/biglinux-timeshift.sh`
(`HT_SOURCE_PREP`) runs `timeshift --create --comments bko-ht1` twice
before the backup.

## Test2 and HoltTest1 placement

- **HoltTest1:** the "(1All only)" row with EndeavourOS and Manjaro
  (`testvm/holttest1d/README.md`) becomes "EndeavourOS, Manjaro, BigLinux".
  Recommended (Decision 4); a 1A run doesn't take it.
- **Test2:** `classes.json` `families.A` and `uefi`; `holt/spec.json`
  (`{"kind": "linux", "firmware": "uefi"}`); `guest/spec.json` copied from
  `manjaro`'s (`S-pac`, `L-grub`, `K-pac`, `B-lin`, `M-lin`,
  `kernels_max` 3, `established: false`); `estimates.tsv` (`guest:biglinux`
  3.3, `holt:biglinux` 4.0, as Manjaro's, until runs measure them);
  `t2lib/plan.py`'s counts; `tests/fixtures/p2/world.py` (`NEW_GUESTS`,
  the pacman list); and the re-blessed goldens.
- **Golden plan:** `holt/tests/corpus/biglinux/` (`src.json` from a
  `bko-holt dump` of the guest after its first update, so with a Timeshift
  snapshot, then `plan.txt`), plus `index.tsv` and the corpus README. Done
  when two corpus builds have the same sha256, leak scans are 0, and no
  other plan changes.
- **judge.py** (`BOOT_STAGE`): the GRUB rule `^boot/efi[^/]*/EFI(/|$)`
  already covers `EFI/BigLinux`. New rules: `boot/grub/grub-btrfs.cfg`,
  written by grub-mkconfig's `41_snapshots-btrfs`, and the dump comparison
  allowing exactly the Timeshift snapshots the plan said it would leave out
  (their subvolumes and `timeshift-btrfs/snapshots/DATE` directories). Each
  rule gets a `judge-test.py` case. Any other path the runs show
  uncovered is added with its reason, as G5 did for Gentoo.

## Sharing the work with the other sessions

The AnduinOS and elementary OS sessions are adding distros right now, in
the same checkout, and their work touches the same lines. In particular
`osrel.rs`'s `supported_names` assert string is a single line every distro
edits. A cachyos-zfs session has uncommitted Test2 files in the tree
(`classes.json`, the specs, the goldens, `plan.py`). So:

- Every edit to a shared file is an exact-string Edit, made after
  `git diff FILE`. Nothing is written over as a whole file.
- BL2's bko-holt change goes in when `osrel.rs`, `boot.rs` and
  `commands.rs` have no one else's uncommitted edits, or else it adds to
  them, and its commit takes only BigLinux's hunks (`git add -p`).
- BL3's Test2 registration comes last, after the others have committed
  theirs, because re-blessed goldens can't be split by hunk.
- `side-session-notes/.../biglinux-files.md` lists the files and lines.
- Host work goes through `cmp-run.sh` reservations. Nothing is killed, and
  no guest outside this work is touched.

## What BL1 found

**From the ISO** (`biglinux_2026-10-03_k618.iso`, mounted read-only on
Chopin, 2026-11-14). It was downloaded separately on Chopin and on
Cinqcent. The MD5 checked on both, and both copies have SHA-256
`1d12186abab0a2cb33ee9f0edd30140cd8ed842aa081e90ad4f0e3d050e6ae90`.

- **Layers:** `biglinux/x86_64/` (not `manjaro/`) holds `rootfs.sfs`
  (2.7G), `desktopfs.sfs` (748M), `mhwdfs.sfs` (1.5G, drivers) and
  `livefs.sfs` (22M). The profile's own `unpackfs.conf` names
  `manjaro/x86_64/rootfs.sfs` only, which isn't on the ISO.
  BigLinux's GTK pre-installer (`calamares/src/services/install_service.py`
  `_configure_unpack_settings`) overwrites it before Calamares starts with
  `rootfs.sfs` **and** `desktopfs.sfs` from the folder it detects. So the
  guest copies those two. The live user and the live packages are in
  `livefs`; the drivers are in `mhwdfs`, which `mhwdcfg` uses.
- **Calamares** is 3.4.2 (not 3.3.14). The shipped profile's exec sequence
  has `mhwdcfg` (no `shellprocess@drivers`) and is otherwise the one in the
  table. `initialPartitioningChoice: none`, default btrfs (the
  pre-installer offers ext4 too). The ESP size isn't set, so Calamares's
  `recommendedSize: 300MiB` applies. The `mount.conf` subvolumes and
  options are as the table says.
- **os-release** is exactly the spec's: `ID="biglinux"`, `ID_LIKE=arch`,
  `BUILD_ID=rolling`, no VERSION_ID.
- **mkinitcpio:** `linux618.preset` has `PRESETS=('default')` and
  `/boot/initramfs-6.18-x86_64.img` only. Calamares's own `bootloader.conf`
  (manjaro-tools') still names a fallback image, which doesn't exist.
- **GRUB:** BigLinux's branding has `bootloaderEntryName: BigLinux`, so
  `EFI/BigLinux`. Manjaro's branding in the same image says `biglinux`,
  which is unused. `installEFIFallback: true`.
- **Services** enabled in rootfs: `big-first-boot`, `grub-btrfsd`,
  `auto-unlock-pacman`, `big-btrfs-balance`, `pre-systemd-swap`,
  `systemd-swap`, `big-mount` and others.
- **New, not in the spec's table:**
  - `big-btrfs-balance` runs `big-btrfs-fix-to-timeshift` at every boot,
    which means to `btrfs subvolume set-default` to `@`. The guest showed
    it doesn't (see below): the default stays 5.
  - `systemd-swap` 0.2.0 (`swap_mode=auto`) uses zram, and its swapFC can
    add swap files under `/swapfile`. On btrfs it makes `/swapfile` a
    **subvolume** (`chattr +C`, preallocated). *Check* on the guest after a
    vm-update: whether `@/swapfile` exists, and whether any swap file is
    active at backup time. If one is, the backup holds it like any file,
    and swapon after a restore needs NOCOW and no holes.
  - Timeshift's `default.json` has `do_first_run: true`, an empty
    `backup_device_uuid`, and everything off. `big-first-boot` turns on
    btrfs mode and `schedule_daily`. Whether `timeshift --check` then
    picks the device and takes a snapshot is still a *check*, settled at
    the guest's first boot.

**From the guest** (`biglinux`, built 2026-11-14 on Chopin, copied to
Cinqcent; qcow2 SHA-256
`edc7ac2f8dbd6d2bd0782a09b83995609872464a4f2bf50c21081870c4680312`,
5.7G, 24 GiB virtual; install 337 s; `vm-update --try` passes, 515
packages, kernel 6.18.49 → 6.18.55).

- **Unpacking:** desktopfs has an overlay whiteout where rootfs has a
  non-empty directory (`usr/share/plasma/avatars/photos`), so rsync exits
  23. Calamares's unpackfs treats 23 as success, and so does the guest. It
  uses unpackfs's own `rsync -aHAXSr --filter=-x trusted.overlay.*`, so no
  overlay xattrs land; 23 whiteout character devices land in
  `/usr/share/applications`, as on a real install.
- **mkinitcpio hooks:** Calamares's `initcpiocfg` rewrites them to `base
  udev autodetect microcode kms modconf block keyboard keymap consolefont
  plymouth filesystems` (busybox, no fsck on btrfs). The table's `systemd`
  hooks are the image's, which a real install doesn't keep.
- **GRUB:** `EFI/BigLinux/grubx64.efi` and `EFI/BOOT/BOOTx64.EFI`; grub.cfg
  has `rootflags=subvol=@` (no slash). The GRUB 2.16 upgrade's install-grub
  hook reinstalled both. `GRUB_DISABLE_OS_PROBER=false` in the image wins,
  so os-prober runs, harmlessly.
- **Disk:** virtio is rotational, so `autodefrag`, not `discard=async`; no
  tmpfs `/tmp`. Right after the install `/boot` and `/usr/share/grub` are
  uncompressed; after the update 16M of `/boot` is zstd, because updates
  rewrite it through `compress-force`. So a real machine's /boot is partly
  compressed anyway, and decision 7's defragment matches what BigLinux
  itself does once.
- **Default subvolume stays 5.** `big-btrfs-fix-to-timeshift` takes awk's
  `$7` from `btrfs subvolume list`, which is the top-level ID, so its
  `set-default` is a no-op.
- **Timeshift:** `big-first-boot` takes one snapshot at the first boot
  (`timeshift-btrfs/snapshots/DATE/@` with `info.json`, tagged daily), adds
  an hourly cron.d entry, then removes itself (and `virtualbox-guest-utils`;
  spice-vdagent stays). With `info.json` missing, `timeshift --list` shows
  nothing, grub-btrfs doesn't list it, and the hourly `timeshift --check`
  **deletes** it as "incomplete". So an empty snapshot made by today's
  `load` would be cleaned away within the hour; decision 3 stands.
- **Swap:** systemd-swap makes a top-level subvolume `@swapfile` with the
  fstab line `/swapfile btrfs subvol=/@swapfile,defaults,noatime 0 0`. At
  the first boot it writes a 512 MiB swap file and a 928 MiB
  `zram-writeback-1` (both NOCOW) into a plain directory `@/swapfile`,
  which `@swapfile` is then mounted over; from the second boot on it makes
  them in `@swapfile`, and the first boot's 1.4 GiB stays hidden in `@`.
  `/proc/swaps` shows zram1 and `/swapfile/1`. So at backup time a swap
  file is active in `@swapfile` (BL4 checks what the backup does with it
  and that swapon works after a restore); the hidden copy is outside the
  backup and is simply gone after a restore.
- **Space:** 5.83 of 23.71 GiB at the first boot, about 40% after the
  update; 24G stays.
- **grub-mkconfig in a chroot** (as the boot stage runs it) with a snapshot
  present: rc 0, and grub-btrfs writes its submenu. No chroot change is
  needed.
- **Fake package managers:** BigLinux ships `/usr/local/bin/{apt,apt-get,
  dnf,yum}` that only `exec pamac`, and an empty real `/usr/bin/rpm`.
  `testvm/update/in-guest.sh` and `pkgset.sh` now need `dpkg` for apt and
  skip a dnf that is `exec pamac`. `compare/compare.py:1556` still tests
  `command -v apt-get`; it matters only if setfacl/setfattr are missing.
- **Updates:** `auto-unlock-pacman` (`pacman-fix-keys`) runs at every boot
  and deletes `db.lck` whoever holds it, so in-guest.sh waits for it and
  for `big-first-boot`.

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| BL1 | Inputs on Chopin and Cinqcent: download `biglinux_2026-10-03_k618.iso` (5.1 GiB, iso.biglinux.com.br), check its MD5 and record its SHA-256. `install-live.sh`'s biglinux branch, `make-arch.sh --live biglinux`, `rebuild-one.sh`, and `in-guest.sh` (keyring, big-first-boot wait). Settle every *check*: the `.sfs` layers, os-release, the ESP's size, `EFI/BigLinux` and the fallback, rotational, used space, whether the first boot takes a Timeshift snapshot, and what Timeshift does with a snapshot missing its `info.json`. Done when it is built on Chopin, copied to Cinqcent with the same SHA-256, boots alone, and `vm-update --try` is ok with a Timeshift snapshot present afterwards | 0.75 d |
| BL2 | bko-holt, by a High-effort subagent: the `SUPPORTED` row and tests, `PACKAGES` and `fingerprint.py`, `bootloader_id()` keeping the source's case (a unit test with `\EFI\BigLinux\grubx64.efi`), the BigLinux `--nocomp` defragment step, grub-btrfs handled in `remake_grub_cfg()` if BL1 shows it fails in a chroot, Timeshift snapshots left out with a note (unit tests: two snapshots, an unrelated unmounted subvolume still made, a mounted one untouched), and a BigLinux-shaped scenario in `scenarios.rs`. Done when `cargo test` passes and the scenario's plan has `mkinitcpio -P`, `--bootloader-id=BigLinux`, the defragment and the Timeshift note, and no `timeshift-btrfs` subvolume create | 0.5 d |
| BL3 | The golden plan and Test2 registrations (above), after the other sessions have committed theirs. Done when two corpus builds agree, leak scans are 0 on both hosts and the Mac, no other plan changes, and Test2's own tests pass | 0.35 d |
| BL4 | HoltTest1 runs (1All only), `prep/biglinux-timeshift.sh`, the judge.py rules and `judge-test.py` cases, and `ram-sizes.tsv`. Done when every load stage passes on both hosts, the restored guest boots alone, there are 0 fsmeta differences, `timeshift --list` on the restored guest shows no snapshot and `btrfs subvolume list` none under `timeshift-btrfs`, `EFI/BigLinux/grubx64.efi` is the new one, `/boot`'s files are uncompressed (`compsize`), and the RAM-disk peak is recorded | 0.4 d |
| BL5 | Docs: holt/README.md's list, `osrel.rs`'s comment, HoltTest1's README row and HoltTest1All's members, the corpus README, `layload.md`'s boot stage (the defragment and Timeshift), and memory (the HoltTest1 index line) | 0.1 d |
| BL6 | *Optional, later:* BackShot1 on the Thinkpad. The user installs BigLinux from the ISO with Calamares's defaults (manual partitioning: ESP, btrfs). It runs for a day so Timeshift takes a snapshot, then `bs1-qemu.py --virtio` rehearsals on `biglinux` on both hosts, then the real run. Done when phase1, plan, phase2 and verify PASS with 0 unexpected, the Thinkpad boots `BigLinux` from NVRAM, and Timeshift lists no stale snapshot | 0.5 d + ~1 h of the user's time |
| | *≈ 2.1 d without BL6, ≈ 2.6 d with it* | |

Each piece is committed on `main` when its tests pass, and only when the
user asks ([[work-on-main]]). Nothing is pushed until the user says so.

**Costs** (est.):

| # | Host time | Disk | The user's time |
|---|---|---|---|
| BL1 | download ~10-30 min a host; a 20-30 min build on Chopin (WORK_DIR in /mnt/bko-ram, `--reserve 30 6`), the copy, a vm-update | ISO 5.1 GiB a host in rebuild-inputs; qcow2 7-9 GiB a host (KDE in full) | none |
| BL4 | ~1.5 h of queue a host at D 20 | an update overlay (~1-2 GiB) | none |
| BL6 | rehearsals ~40 min a host; the Thinkpad run ~45-75 min | the backup on `mybig512` | the install (~30 min), boot the stick, click Done |

## Risks

- **No signature.** BigLinux signs packages but not ISOs. The SHA-256 pin
  makes later rebuilds safe, but the first download is trusted only as far
  as HTTPS to iso.biglinux.com.br and the MD5 beside it. The torrent's
  piece hashes are a second source, if wanted.
- **ISOs are pruned.** The index keeps about two months of them, so the
  rebuild input has to stay on both hosts. The fallback is the newest ISO,
  with a new pin.
- **BigLinux-specific hooks during updates.** `biglinux-name` rewrites
  os-release on every `filesystem` upgrade. Between pacman's write and the
  hook, `ID=manjaro`, which a dump taken mid-update would refuse; nothing
  dumps mid-update. `install-grub` reinstalls GRUB on a grub upgrade during
  vm-update, which changes the image as any update does.
- **grub-btrfs in the chroot** may need more than a disable switch (its
  `--timeshift-auto` daemon config, `/run/timeshift`). BL1 runs
  `grub-mkconfig` in a chroot on the guest to find out before BL2.
- **The KDE image is large.** A full desktop makes the guest 2x Manjaro's
  minimal XFCE. If HoltTest1's peak passes ~24 GiB, the run goes to Chopin
  and Cinqcent only, never the small hosts.

## Out of scope

- BIOS installs and LUKS (`sd-encrypt`). Calamares offers both, but
  neither is a default. A `biglinux-luks` guest is its own piece (est.
  +0.5 d).
- Backing up Timeshift snapshots themselves (Decision 3's option C).
- BigCommunity and XivaStudio, BigLinux's sister editions from the same
  `iso-profiles` (other IDs; `xivastudio` ISOs are on the same index).
- The drivers step (`big-driver-manager-cli`), zram, and hibernation
  swapfiles. None of them is on the disk at backup time.

## Decisions for the user

1. **Which ISO:** `k618` (6.18 LTS, recommended: the long-lived kernel,
   the same series as the Thinkpad's Gentoo); `k72` (7.2, the newest); or
   `k612`.
2. **Trusting an unsigned ISO:** MD5 from the same server plus a SHA-256
   pin recorded at download (recommended); or also check the torrent's
   piece hashes (~+15 min); or wait for a signed ISO, which BigLinux
   doesn't publish.
3. **Timeshift snapshots on restore:** (A) left out, with a note naming
   them (recommended: nothing that looks like a restore point is empty,
   and the schedule takes new ones); (B) made empty, as today (unsafe: an
   empty restore point); (C) backed up and restored as shared snapshots,
   through `btrfs-snapshots.md`'s `--exact`/`--clone-from` path. C needs the
   backup to mount the top-level volume, a change to bko's backup scope,
   not just holt's (est. +1.5-2 d, its own spec later).
4. **HoltTest1 placement:** 1All only, beside EndeavourOS and Manjaro
   (recommended: the same family and the same reasons); or 1A, which makes
   every 1A run ~1.5 h longer; or its own **1B** group.
5. **SticTest1:** no entry, as for Manjaro (recommended: static archivers,
   and btrfs is covered by `tumbleweed` and `kinoite44`); or a `biglinux`
   entry built from `kinoite44`'s, with a `compress-force=zstd` scratch
   volume (est. +0.35 d).
6. **GRUB id case:** keep the source's spelling, `BigLinux` (recommended:
   no other plan changes, and the plan says what the installer did); or
   leave the lowercasing, which works because FAT ignores case.
7. **The /boot defragment:** do it on BigLinux, as its installer does
   (recommended); or leave the restored files compressed, which GRUB 2.14
   reads anyway.
8. **BackShot1 (BL6):** later, as its own step once BL1-BL5 pass
   (recommended; it needs a reinstall of the Thinkpad, which now runs
   another distro); or not at all.
