# TrueNAS Community Edition support

2026-11-14. Asked for by the user: support TrueNAS Community Edition (formerly
TrueNAS SCALE), the Debian-based ZFS storage appliance, the way Proxmox VE 9
was added. Proxmox, a Debian ZFS-root system, is the closest precedent.
The user approved this spec on 2026-11-14 with every recommended decision
(the last section).

**Conventions.** est. marks an estimate. *Check* marks a fact the sources
don't settle, to be settled on the first guest (T1) before the code that
depends on it. TrueNAS's facts come from its own source code, read through
`gh api` on 2026-11-14:

- `truenas/truenas-installer` (master): `install.py` (partitions and
  `zpool create`), `__main__.py`, `server/` (the installer's JSON-RPC API);
- `truenas/scale-build` branch `release/25.10.8`: `truenas_install/__main__.py`
  and `fhs.py` (the boot environment's datasets), `scripts/grub.cfg` (the
  ISO's menu), `conf/cd-files/` (the live ISO's extras);
- `truenas/middleware` `release/25.10.8`: `src/freenas/etc/grub.d/10_truenas_linux`
  and `usr/local/bin/truenas-initrd.py`.

Release status comes from a web search (2026-11-14): 25.10 "Goldeye" is the
stable line (25.10.6 on 2026-08-12, 25.10.7 listed 2026-09-02; scale-build
has a `release/25.10.8` branch, so 25.10.8 is probably out), on **Debian 12**
with Linux 6.6 (TrueNAS's February 2026 feature table; T1 found 6.12 on
25.10.6-25.10.8); **TrueNAS 26** was in
beta (26.0.0-BETA.3, about 2026-08-20), kernel 6.18, release planned for Q3,
no stable release found. Nothing has been downloaded or installed. Code
references are to local `main` at d5e8686 plus the uncommitted tree.

## Summary

- TrueNAS Community Edition **25.10** (the newest stable on T1's day, Decision
  8) becomes a supported system for bko-holt and both archivers. There are two
  QEMU guests: `truenas25`, UEFI, one boot disk; and `truenas25-mirror`, BIOS,
  a two-disk mirrored `boot-pool` plus a one-disk data pool that the restore
  leaves alone (Decision 6). Each guest holds **three boot environments**,
  made by real updates. Each gets a SticTest1 entry (Decision 10), a place in
  HoltTest1 (**1D**, Decision 9) and the Test2 registrations. Both run only on
  Chopin and Cinqcent.
- To `osrel.rs` it **is** Debian 12 (T1: TrueNAS keeps Debian's
  `/etc/os-release`), so dump already accepts it. It differs from Debian in
  four ways that matter:
  1. **More than one root.** `boot-pool/ROOT/<version>` is one boot
     environment (BE) per install or update. Each BE is a root dataset plus
     15 child datasets (`usr`, `etc`, `var`, `data`, …). Only the active BE
     (`bootfs`) is mounted. Today's dump backs up the mounted BE only, and
     load would bring the others back as **empty datasets**. GRUB would still
     list them, and they would fail to boot.
  2. **A read-only root.** The BE's root dataset and its `usr`, `opt` and
     `conf` children are `readonly=on`. Today's load creates datasets with
     their local properties, so the restore would fail writing into them.
  3. **An ESP that nothing mounts.** The ESP is like Proxmox's, but TrueNAS
     uses no `proxmox-boot-tool`. `truenas_install` mounts the ESP only to run
     `grub-install`.
  4. **A GRUB script that lists every BE.** `10_truenas_linux` builds one
     menu entry per dataset under `boot-pool/ROOT`, with the pool name
     **hard-coded**. Each entry takes its kernel version from the BE's user
     property `truenas:kernel_version`.
- The fix (Decisions 2-5):
  - **dump** lists every inactive BE in `storage.backup_sources`, each with
    the read-only mount a backup makes for it. This is ABRoot's mechanism for
    Vanilla OS's other root (`report.rs` `BackupSource.mount`).
  - **load** recreates every BE with its user properties and fills each one
    from the archive. It holds `readonly=on` back until the boot stage is
    done, keeps `bootfs`, formats the ESP with its volume ID, and runs
    TrueNAS's own `grub-install` and `update-grub` steps.
- The **middleware config database** `/data/freenas-v1.db` (SQLite) and its
  key `/data/pwenc_secret` sit in each BE's `data` dataset, so they come back
  with their BE. The judge checks them byte for byte and with SQLite's
  integrity check.
- ~5 d (est.), all on Chopin and Cinqcent, none of the user's time but the OKs
  and the downloads.

## Why

- **Boot environments are the restore path no guest tests.** Every ZFS guest
  today has one root. OmniOS and OpenIndiana have beadm BEs, but load brings
  their inactive BEs back empty, as an accepted limit (openindiana.md,
  Decision 3). TrueNAS shows every BE in its UI and in GRUB, and users
  roll back to them after a bad update. Bringing them back empty would
  silently break a feature users rely on.
- **A read-only root on ZFS.** Bazzite and ABRoot are read-only roots on
  btrfs and ext4. Nothing tests `readonly=on` datasets.
- **The most-used ZFS appliance.** When its boot SSD dies, its users reach
  for a whole-machine restore. The data pool is usually fine.
- **The ZFSBootMenu-on-Void session** has zroot/ROOT/* BEs too. The
  inactive-BE backup sources (Decision 2) should be shared code, not built
  twice ("Shared lines").

## What TrueNAS 25.10 is (as far as bko cares)

| | TrueNAS CE 25.10 | Nearest supported system |
|---|---|---|
| Release | 25.10 "Goldeye", point releases about monthly (25.10.6 2026-08-12, 25.10.7 ~2026-09-02, 25.10.8 probably by now); ISO `TrueNAS-25.10.N.iso` ~2 GB with a `.sha256` beside it (*check* for a signed one); offline update files `TrueNAS-25.10.N.update` (a squashfs holding `rootfs.squashfs` and `manifest.json`) | Debian 12 |
| os-release | Debian 12's (`ID=debian`, `VERSION_ID="12"`; T1). The product is in **`/etc/version`** (e.g. `25.10.8`, with no newline at its end), which the installer reads too, and in `/data/manifest.json` | `debian12` |
| Kernel | TrueNAS's own build, **6.12** on 25.10.8 (`/boot/vmlinuz-6.12.113-production+truenas`; T1), initramfs-tools driven by `truenas-initrd.py` | none |
| ZFS | OpenZFS 2.3.9 on 25.10.8 (T1) from TrueNAS's build, in the kernel package | `debian13-zfs` (2.3) |
| Disk (installer) | Each boot disk is GPT: p1 **BIOS boot** 1 MiB (EF02, `-A1:set:2`), p2 **ESP 512 MiB** (EF00), p3 **ZFS** for the rest (BF01). The protective MBR has the `pmbr_boot` flag on legacy-BIOS installs (`set_pmbr`). No swap partition (*check*: 24.04 and later) | `proxmox9` (the same three partitions) |
| `boot-pool` | `zpool create -f -o ashift=12 -o cachefile=none -o compatibility=grub2 -O acltype=off -O canmount=off -O compression=on -O devices=off -O mountpoint=none -O normalization=formD -O relatime=on -O xattr=sa boot-pool [mirror] p3…`. The name is **fixed** (`BOOT_POOL`, and `10_truenas_linux`'s `roots_dataset="boot-pool/ROOT"`). `compatibility=grub2` limits it to GRUB's feature set. `/etc/hostid` comes from `zgenhostid` in the installer | `debian13-zfs` |
| Datasets | `boot-pool/ROOT` (`canmount=off`; `org.zectl:bootloader=grub`), `boot-pool/grub` (`canmount=off`, `mountpoint=legacy`, mounted at `/boot/grub` by every BE through fstab; T1) | none |
| A BE | `boot-pool/ROOT/<version>` (`-1`, `-2` appended on a clash): `mountpoint=legacy`, **`readonly=on`**, `truenas:kernel_version=K`, `zectl:keep=False`, a `@pristine` snapshot. Its 15 children (`fhs.py`): `audit`, `conf` (RO), `data`, `mnt`, `etc`, `home`, `opt` (RO), `root`, `usr` (RO), `var`, `var/ca-certificates` (at `/var/local/ca-certificates`), `var/lib`, `var/lib/incus`, `var/log`, `var/log/journal`. Each child has `canmount=noauto`, `mountpoint=/NAME`, its own `setuid`/`exec`/`devices`/`acltype`/`atime`, and `org.zectl:bootloader=""`. `conf`, `etc`, `opt`, `usr`, `var` and `var/lib` have `@pristine`. The initramfs mounts the bootfs BE's root and children (*check*: by `truenas-initrd`'s hook or zfs-initramfs) | Bazzite (read-only root), OmniOS (BEs) |
| An update | `truenas_install` with `old_root` set makes a new BE from `rootfs.squashfs`. **`audit`, `data`, `home`, `root`, `var/log` are `zfs clone`s** of the old BE's `@install-DATE` snapshots, so the config DB and logs carry over. The others are fresh. It sets `bootfs` to the new BE, then runs `truenas-grub.py`, `truenas-initrd.py -f`, `update-grub` and `configure_fips`. It reruns `grub-install` only if GRUB's version changed | none |
| GRUB | Both firmwares on every install: `grub-install --target=i386-pc /dev/DISK` on each disk, and on UEFI `grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=debian` with the ESP mounted only for the command, a copy to `EFI/boot/bootx64.efi`, and one `efibootmgr -c` entry on a fresh install. `grub.cfg` is on `boot-pool/grub`, shared by every BE. `10_truenas_linux` lists the active BE first, then the others newest first (`zfs list -d 1 boot-pool/ROOT`), each `root=ZFS=boot-pool/ROOT/V`, kernel from `truenas:kernel_version` | `debian13-zfs` (GRUB on ZFS) |
| The middleware's state | `/data/freenas-v1.db` (SQLite, written by `middlewared`), `/data/pwenc_secret` (the key for the DB's encrypted fields), `/data/user-services.json`, `/data/subsystems`. `/data` is the BE's `data` child, mode 700 inside. `/conf/truenas_root_ds.json` lists the BE's datasets with their **GUIDs** (*check* what reads it) | Proxmox's `config.db` |
| The system dataset | `.system` lives on `boot-pool` when there is no data pool, and moves to the first data pool when one is made: `POOL/.system` with `cores`, `nfs`, `samba4`, `configs-UUID`, `netdata-UUID` and `vm` on 25.10 (T1), `mountpoint=legacy`, mounted by the middleware at `/var/db/system` | none |
| Packages | **apt is disabled**: `/usr` is read-only and the package tools refuse to run (`install-dev-tools` is a developer escape hatch). Updates only make new BEs, through the UI, `midclt call update.…` or an offline `.update` file | none so far |
| Install | Boot the ISO (GRUB entries "Start TrueNAS Installation" and "… (115200 baud)"; the serial entry puts `console=ttyS0,115200n8` on the live system). The live ISO's root `.bash_profile` runs the **TUI** (`python3 -m truenas_installer`). The package also ships `truenas-installer.service` (`… --server`, a **JSON-RPC 2.0 websocket** on 127.0.0.1:8080, behind nginx on port 80 at `/ws`, announced over Avahi as `_truenas_installer._tcp`, for TrueNAS Connect's "adoption") with methods `adopt`, `authenticate`, `list_disks`, `install`, `reboot`, … (enabled on 25.10's ISO; T1 installs through it) | none |

## What T1 found (2026-11-14)

Settled on `truenas25` (25.10.6 installed, updated to 25.10.7 and 25.10.8) on
Chopin; the qcow2 copied to Cinqcent has the same SHA-256.

- **The install is unattended through the installer's own API.** The live
  ISO's `truenas-installer.service` is enabled and answers JSON-RPC at
  `ws://…:80/ws` (nginx in front of 127.0.0.1:8080). `make-truenas.sh` boots
  the ISO's `/vmlinuz` and `/initrd.img` with `-kernel` (`boot=live
  toram=filesystem.squashfs`, serial console), calls `list_disks`, `install`
  (`disks`, `set_pmbr`, `authentication` for `truenas_admin`) and `shutdown`.
  An install takes about 130-160 s; the whole UEFI build with two updates is
  about 25 min.
- **First boot by the middleware API** (`wss://…/api/current`,
  `auth.login_ex`): `truenas_admin`'s SSH key and passwordless sudo, SSH on,
  the serial console (`system.advanced.update`). `root`'s shell is **zsh**.
- **Updates** are `update.manual` jobs on an offline `.update` file copied to
  `/root`, then `system.reboot`. Each made a new BE with the expected 15
  children; `audit`, `data`, `home`, `root` and `var/log` are clones of the
  previous BE's `@install-*` snapshots. `boot.environment.keep` on the oldest
  BE sets `zectl:keep`. GRUB lists all three BEs.
- **Kernel and ZFS:** `6.12.113-production+truenas`, `zfs-2.3.9-1`, os-release
  Debian 12 (not 6.6, as the spec first said).
- **fstab** holds one line, `boot-pool/grub /boot/grub zfs relatime,defaults
  0 0`; the BE's root comes from `root=ZFS=boot-pool/ROOT/V` and its children
  are mounted by the initramfs.
- **`.system`** (no data pool) is `boot-pool/.system` with `configs-UUID`,
  `cores` (1 G quota), `netdata-UUID` (`canmount=noauto`, mounted at
  `/var/db/system/netdata`), `nfs`, `samba4` and `vm`, all legacy under
  `/var/db/system`; `cores` is mounted a second time at
  `/var/lib/systemd/coredump`.
- **The ESP** is p2, FAT32 with one sector per cluster (`mkdosfs -F 32 -s 1
  -n EFI`), label `EFI`, never mounted. `grub-install` always runs with
  `--no-nvram`; the installer makes the NVRAM entry itself, `TrueNAS-0`.
- **`vm-mount --ro` doesn't work** as it stood ("no partition holds
  /etc/fstab"): the root is the `bootfs` BE and fstab doesn't name it. T6
  teaches it the bootfs rule.
- **No `vm-update`:** apt is off, so each guest carries `no-vm-update` and a
  newer release means a rebuild (`testvm/truenas-update --check`, T7).

## What T5 and T6 found (2026-11-14)

- **SticTest1 (T5):** truenas25 gives 53 PASS, 7 LIMIT, 0 FAIL for both
  archivers on Chopin and Cinqcent (run tn5d, roundrel8). The scratch trees
  hold `readonly=on` back as load does. One LIMIT is new: static-musl
  rustic-bko warns `EIO` looking up uids and gids with no name, because
  TrueNAS's nscd runs with its passwd and group caches off. The backup still
  exits 0 and restores by number.
- **HoltTest1 (T6):** all four 1D cells PASS (truenas25 tn6-8 Chopin and
  tn6-7 Cinqcent, truenas25-mirror tn6-6 and tn6-5) with 0 fsmeta and 0 dump
  differences. Every BE comes back with its tree, properties and `bootfs`;
  the ESPs match byte for byte; the config DB passes SQLite's integrity
  check; the oldest BE activates and boots; each mirror disk boots alone,
  and `tank` is untouched.
- Two load bugs were fixed on the way. Status called the tree unmounted once
  the boot stage had unmounted the inactive BEs. `grub-install`'s
  `mkdir -p T/boot/efi` left an empty `/boot/efi`; it is now removed when
  load made it.
- The mirror build needed disk serials: TrueNAS refuses a pool on disks
  with no serial, and QEMU 10 takes `serial=` only on `-device`.

## What already works, and what doesn't

**bko-holt `dump`:**

- `osrel.rs`: the `("debian", "12", "Debian 12")` row (line 151) accepts it
  (*check*). Recommended (Decision 7): dump records `os.product = "TrueNAS
  25.10.8"` from `/etc/version` when `/data/freenas-v1.db` and
  `/usr/local/bin/truenas-grub.py` exist. The plan header and the report
  then name the product, and load refuses a TrueNAS source whose major isn't
  25.10. Proxmox does the same with `pve-manager`.
- **ZFS** (`zfs_json`) already records `boot-pool`'s features, properties
  (`compatibility=grub2`, `bootfs`) and every dataset with its local
  properties, user properties (`truenas:kernel_version`, `zectl:keep`,
  `org.zectl:bootloader`) and `origin`. `debian13-zfs`'s plan shows user
  properties restored (`com.sun:auto-snapshot`); *check* that `origin` is
  recorded.
- **The active BE**: `/` is the legacy-mounted `boot-pool/ROOT/V`. The 15
  children are mounted at their own paths. `/boot/grub` is
  `boot-pool/grub` (legacy) and `/var/db/system` is `.system` (legacy). All
  are in mountinfo, so all should be backup sources already (*check*:
  legacy datasets outside fstab).
- **Needed: the inactive BEs as backup sources** (Decision 2). When the pool
  is `boot-pool` and holds `ROOT/*` datasets besides `bootfs`, each other BE
  `V` joins `storage.backup_sources`:
  - at path `/run/bko-be/V` for the BE's root (the ZFSBootMenu spec's
    convention; `/run/bko-be/POOL/V` if two pools share a BE name), and
    `/run/bko-be/V/NAME` for each child (`var/ca-certificates` at its
    `mountpoint`, under the same prefix);
  - with no `from`, and a `mount` hint `{type: zfs, source:
    boot-pool/ROOT/V[/NAME], options: "ro,zfsutil,nodev,nosuid,noexec"}`.
    `zfsutil` is needed because the children have non-legacy mount points;
    *check* that `mount -t zfs -o zfsutil` takes a `canmount=noauto` dataset
    whose mountpoint differs from the target. This is the same way
    `truenas_install` mounts BEs (`mount -t zfs DS DIR`);
  - with a `note`: "TrueNAS boot environment V (not booted): a backup mounts
    it read-only here".

  dump also mounts each BE once read-only in its run directory, as ABRoot's
  dump does, to read its `/etc/version` and kernel names into a new
  `boot.truenas` object: `{bes: [{name, version, kernel, keep, active,
  origin}], esps: [...], grub_devices: [...]}`. Then it unmounts it. A BE
  whose `/etc/version` can't be read is recorded as such and still listed.
  The run directory is `/run/bko-holt` and `/run` is a tmpfs, so a backup's
  mount points are never written to the source's datasets.
- **The ESP(s)**: not mounted, so `boot.esps` is `[]` today (the Proxmox
  finding, `report.rs:1276`). Proxmox added a private read-only ESP mount
  for the partitions named in `proxmox-boot-uuids`. TrueNAS has no such list,
  so the ESPs are **p2 of each `boot-pool` vdev's disk with the EF00 type
  GUID**, which is how `truenas_install` itself finds them
  (`get_partition_guid`). They go into `boot.esps` with `mounted_at: null`
  and their contents (Proxmox's code path, a second caller).
- `PACKAGES` (`boot.rs:214`): TrueNAS has a dpkg database (*check*). The
  rows that matter are `grub-pc`/`grub-efi-amd64-bin`, `zfs-initramfs` (or
  TrueNAS's equivalent) and the TrueNAS middleware package (*check* its
  name, e.g. `truenas`, `middlewared`). `fingerprint.py` mirrors them.
- **Free text** (`redact.rs`): the hostname (`truenas`, the default) joins
  `GENERIC_HOSTS`, and `truenas` joins `SYSTEMS`. The `freenas-v1.db`
  content is never in a dump.

**bko-holt `load`:**

- **Partition and format:** each disk's three partitions come back with the
  source's GUIDs and type codes. The BIOS boot partition keeps its
  attribute bit 2 (*check* that load copies GPT attributes), and the PMBR
  `pmbr_boot` flag is kept (*check*). Each ESP is made with `mkfs.fat -F 32
  -i ID`, as Proxmox's are.
- **Assemble:** `zpool create … -o compatibility=grub2 … boot-pool [mirror]
  …` with the source's features, then `zpool reguid -g` (the source's GUID,
  which GRUB's `search` would use). The plan's `zfs_problems` checks that the
  image has the `grub2` compatibility file. The pool **must keep the name
  `boot-pool`**: if `zname()` would rename it (a clash in the rescuer), the
  plan refuses with a problem, since `10_truenas_linux` hard-codes the name.
- **Datasets (Decision 3):** every BE and child is created with its local
  properties and user properties **except `readonly`**, which is held
  back. `origin` isn't recreated: each clone (`data`, `audit`, `home`,
  `root`, `var/log` of every BE after the first) becomes a plain dataset,
  with a note: "TrueNAS: BE V's data/audit/home/root/var/log were clones of
  V-1's; they come back as full copies (a few MB each)". `@pristine` and
  `@install-*` snapshots aren't remade (Decision 4).
- **Mount and restore:**
  - the active BE as today: `/` by the legacy-bootfs branch (OmniOS's,
    `commands.rs:2648`), its children by `zfs mount`;
  - `boot-pool/grub` at `/boot/grub`, and `.system` at `/var/db/system` if
    it's on `boot-pool`. These are legacy datasets that were mounted at dump
    time, which today's loop skips (`!mp.starts_with('/')`, line 2635). A new
    rule mounts a legacy dataset with `mount -t zfs` at the path the dump saw
    it mounted, when that path is a backup source;
  - each inactive BE with its children at `T/run/bko-be/V/...` (`mount
    -t zfs -o zfsutil`). The archiver restores each one from its backup
    source of the same path. The mount points are removed after the
    unmount, so the restored root's `/run` is left as it was.
- **Boot stage, a new `truenas` arm** (Decision 5), in the active BE's
  chroot with `/boot/grub` mounted. It does what `truenas_install` does on a
  fresh install, minus the steps that only matter when unpacking:
  1. `zpool set bootfs=boot-pool/ROOT/V` (already planned, line 2505);
  2. no `update-initramfs`: the initrds come back with each BE. Nothing in
     them names a disk, since `cachefile=none` and the pool is imported by
     name. *Check* on T6 that the restored initrd boots, before relying on
     it. If it doesn't, the fallback is `truenas-initrd.py -f /` for the
     active BE only;
  3. `grub-install --target=i386-pc /dev/DISK` for each `boot-pool` disk
     (both firmwares, as the source has them, whatever the rescue system
     booted with);
  4. on each disk with an ESP: mount it at `/boot/efi`, run `grub-install
     --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=debian
     --recheck --no-floppy --no-nvram` (always, as `truenas_install` does; NVRAM is `efi_entries()`'s), copy
     `EFI/debian/grubx64.efi` to `EFI/boot/bootx64.efi`, then unmount;
  5. `update-grub`. `10_truenas_linux` runs `zpool get bootfs boot-pool` and
     `zfs list -d 1 boot-pool/ROOT` in the chroot (the pool is imported
     there, under `-R`; *check* that `make_system_path_relative_to_its_root`
     and grub-probe work under the altroot, as they do for `debian13-zfs`),
     so every restored BE gets its menu entry back;
  6. `efi_entries()` remakes the source's `debian` NVRAM entry, as for
     Debian;
  7. **then** `zfs set readonly=on` on each dataset whose source had it
     local, before the final `zpool export`;
  8. `/etc/hostid` is the source's, from the restored `etc` (*check* that
     TrueNAS's initramfs imports a pool last used under the rescuer's
     hostid; Debian's zfs-initramfs needs `zpool import -f` or a matching
     hostid. `debian13-zfs` passes, so the same mechanism should cover it);
  9. a note: "TrueNAS: N boot environments restored (active V); GRUB
     reinstalled on D disk(s), its menu rebuilt".
- **Secure Boot:** TrueNAS 25.10 doesn't support it (*check*), so it's out of
  scope.
- **The data pool** (Decision 6): a pool other than `boot-pool` on disks of
  its own. By default it is outside the boot-drive restore. dump records it
  as today. load, given `--pools boot-pool`, plans only the boot pool's
  disks, says the data pool's disks are left untouched, and refuses a target
  that would overwrite them. *Check* what selection flag load has today
  (`--disks`, `--only`). If none, this piece is +0.3 d. On the restored
  system, the middleware imports the data pool from its DB entry by GUID, as
  after a reinstall plus a config restore.

**The rescue image:** no change ([[one-kernel-rescue]]). Its OpenZFS is 2.4 and
creates `compatibility=grub2` pools. The `grub2` file ships with OpenZFS
(*check* it's in the image).

## Guests

Two guests (Decision 6), `-cpu host`, 2 vCPUs, **`MEMORY=8192`** at install
(the installer asks for 8 GiB, *check*) and **6144** in the tests. That
shares Chopin's RAM; *check* that the middleware runs in 4 GiB, and if so
the tests use 4096. Both come from the newest 25.10.N ISO **and its two
predecessors' offline update files** (Decision 8), pinned by SHA-256 in
the script, kept in `/bkoscratch/rebuild-inputs/GUEST/` on Chopin and
Cinqcent (~2 GB ISO + 2 × ~1.6 GB updates a host, est.).

| Guest | Firmware and disks | What it covers |
|---|---|---|
| `truenas25` | UEFI, no Secure Boot; one 32G disk (BIOS boot, ESP, `boot-pool`); no data pool, so `.system` is on `boot-pool` | **three BEs** (installed N-2, updated to N-1, then N; the active is N; N-2 has `zectl:keep=True` set through the UI's "keep" call), the read-only root, the ESP rebuilt by `grub-install`, `.system` on the boot pool, the config DB |
| `truenas25-mirror` | BIOS (SeaBIOS); two 32G boot disks, `boot-pool` a **mirror**, plus a 4G disk with data pool `tank` (one dataset, a few files, an SMB share in the DB) | the mirror, `grub-install` i386-pc on both disks, **two BEs** (N-1, N), `.system` on `tank` (not in the boot-drive restore), the data pool left alone and re-imported by the restored config |

How they are built (Decision 1):

1. **The installer's JSON-RPC API** (recommended, *check* it runs on the
   25.10 ISO). A new `testvm/make-truenas.sh FLAVOR WORK_DIR`:
   - boots the ISO with the serial GRUB entry. A QEMU `-kernel/-initrd`
     boot from the ISO's files, with the same command line plus
     `console=ttyS0`, is the fallback if the menu needs a key. QEMU's user
     network forwards a host port to the guest's 80;
   - talks to `ws://127.0.0.1:PORT/ws` with a small Python client in the
     repo (`testvm/truenas/install.py`, stdlib only, a minimal websocket
     framing). It calls `adopt`, then `list_disks`, then `install {disks:
     ["vda"], set_pmbr: false, authentication: {username: "truenas_admin",
     password: P}}` for `truenas25`, or `{disks: ["vda","vdb"], set_pmbr:
     true, …}` for the mirror, then `reboot`. P is generated per build and
     kept only in `vms/GUEST/` (never in the repo);
   - then boots the disk and configures it through the **middleware's API**
     (`wss://…/api/current`, JSON-RPC 2.0, `auth.login_ex` as
     `truenas_admin`; *check* the 25.10 method names): SSH enabled with
     password login off, `vms/GUEST/id_ed25519.pub` for `truenas_admin` and
     root (*check* that 25.10 allows root SSH keys), the serial console on
     (`system.advanced.update {serialconsole: true}`), a `tester` user. On
     the mirror it also makes `tank` and its share;
   - copies the N-1 and N `.update` files in over SSH. For each it calls the
     manual-update method (*check*: `update.manual` or `update.file`) and
     reboots, which makes a real BE through `truenas_install`. Then it marks
     the oldest BE keep (`boot.environment.keep`, *check*);
   - checks `boot.environment.query` (three BEs, or two, the newest active),
     SSH, the version, the serial log, and converts to qcow2.

   This uses TrueNAS's own installer and updater, so the partitions, pool,
   BEs, clones and GRUB are the real ones. Every step is an API call with
   a JSON answer, with no screen scraping.
2. **The installer's TUI over the serial console**, driven by an
   expect-style Python script reading ttyS0 (Haiku's `drive.py` drives
   QEMU's HMP; this one would read and write the serial port). It reaches
   the same install, but every installer release can move the dialog's
   prompts. It's the fallback if the API isn't running on the ISO (+0.3 d).
3. **Bypass the installer**: in a throwaway Debian 12 helper VM with ZFS, run
   `install.py`'s `sgdisk` and `zpool create` lines, then `truenas_install`
   from the mounted `.update` file. It works without the ISO's live system,
   but we replicate the installer, and the first boot's setup still needs
   the middleware's API. Not recommended.

Each guest gets:
- `rebuild-one.sh` lines and the ISO/update checks;
- the rebuild inputs on both hosts;
- `ram-sizes.tsv` rows (est. D 14, MEM 6; measured in T6);
- `classes.json` entries (in the batched Test2 pass);
- `vm-store` publish to Chopin and Cinqcent only;
- a GuestTest1 grade.

**vm-update can't update these guests**: apt is off. `in-guest.sh` refuses a
TrueNAS guest by name. The guests' "OS updates"
([[images-take-os-updates]]) come from a **rebuild** with the newer
`.update` file, which also makes a new BE. A `testvm/truenas-update --check`
compares the guest's `/etc/version` with the newest 25.10.N listed on
TrueNAS's update server, as `sculpt-update` does for Sculpt. The rebuild
keeps three BEs (N-2, N-1, N), so the guest doesn't grow.

**Host ZFS:** HoltTest1 mounts source and copy with `vm-mount --ro`. The pool
is `compatibility=grub2`, so Chopin's and Cinqcent's OpenZFS 2.3.9 can import
it. `vm-mount` needs two changes (*check* on T1):
- mount only the **bootfs** BE at `/`, with its children;
- mount the other BEs under `MP/.be/V`, read-only.

Today every `canmount=noauto` child of every BE claims the same mount points
(`/usr`, `/etc`, …), so a plain import would mount whichever came first.
Proxmox's `mount_renamed` is where this goes. Both guests go in
`needs_host_zfs`.

## Tests

**SticTest1** (Decision 10: `truenas25` only). The archivers back up and
restore a fixture inside the guest, which a second, mirrored guest wouldn't
change. One `inner.sh`, `fixture.sh` and `judge.py` from `proxmox9`'s:
- a scratch pool made like `boot-pool` (`compatibility=grub2`, its
  properties);
- datasets with TrueNAS's per-child options, including a `readonly=on` one
  that the restore must leave read-only and complete;
- the fixture's files, with `bkoshim.py` for setfattr and setfacl, since the
  guest can't install `attr` or `acl`.

The guest's own files in the fixture: `/data/freenas-v1.db` and
`pwenc_secret`, `/conf/truenas_root_ds.json`, `/etc/version`. Static
`rustic-bko` and `restic` run from `/root` (exec allowed, `nosuid`). Both
archivers, both hosts. `manifests.json`, `limits.json` and
`check-manifests.py`'s count go up by one (whoever lands last sets the
number to the entries present).

**HoltTest1** (Decision 9: **1D**). Both guests join 1D beside the Debian and
Proxmox guests. They are Debian-based ZFS-root systems with the same ZFS-host
needs, and 1D becomes twelve runs. That means `classes.json`'s D group, the
README's 1D row ("Debian 13, Proxmox VE 9, TrueNAS 25.10") and HoltTest1All.
They run on **Chopin and Cinqcent** each, in /mnt/bko-ram under `cmp-run.sh`
reservations (est. `--reserve 14 6`), and finished guests are moved to disk.

judge.py and ht1.sh:
- **every BE** is compared by tree, the inactive ones from vm-mount's
  `MP/.be/V`. Each BE's dataset list, local and user properties
  (`truenas:kernel_version`, `zectl:keep`, `readonly`) and `bootfs` must
  equal the source's. `origin` and snapshots are allowed to differ, as
  Decision 4 says;
- the ESP by content, as for Proxmox: GRUB's files are rebuilt and may
  differ in bytes, but the same set of paths must be there;
  `EFI/boot/bootx64.efi` must equal `EFI/debian/grubx64.efi`;
- `/boot/grub/grub.cfg`: the same menu entries (titles and `root=ZFS=…`) as
  the source's, in the same order;
- `/data/freenas-v1.db` byte-identical before boot. After boot it may change
  (the middleware writes on start), but `PRAGMA integrity_check` must say
  `ok`. `/data/pwenc_secret` must be byte-identical;
- **the boot stage** boots the copy, waits for the middleware, and checks
  that `boot.environment.query` lists every source BE with the active one
  marked and that `system.version` is N. It then activates the **oldest** BE
  (`boot.environment.activate`), reboots, and checks that `/etc/version`
  says N-2. That boot runs under `-snapshot`, so the copy isn't changed. On
  the mirror it also boots each disk alone (Proxmox's `boot_each_disk`; the
  pool comes up DEGRADED) and checks that `tank` imported and its share is
  there;
- `ram-sizes.tsv`: rows measured in T6.

**Test2:** golden plans in `holt/tests/corpus/truenas25/` and
`truenas25-mirror/` (`src.json`, `plan.txt`, `index.tsv` rows, a README
paragraph), from `bko-holt dump` of each guest, made by this session. The
registrations go in the sibling sessions' **one batched Test2 pass**:
- `classes.json`: the D family, `uefi` for `truenas25`, `bios` for the
  mirror, both in `needs_host_zfs`;
- `holt/spec.json`, `guest/spec.json`, `estimates.tsv`;
- `t2lib/plan.py`'s counts and `tests/fixtures/p2/world.py`;
- GuestTest2: a `t2lib/guest_judge.py` rule that a TrueNAS guest has
  apt off and no `KERNELS_MAX` check (each BE has one kernel);
- `guest/known-lines.json` lines for the middleware's mounts;
- re-blessed goldens.

It's done when two corpus builds have the same sha256, leak scans are 0 on
both hosts and the Mac, no other plan changes, and Test2's own tests pass.

**BackShot1:** not proposed. A TrueNAS laptop isn't a real use. It could be
offered later through BackShot1All.

## Shared lines (with Aeon/MicroOS, ZFSBootMenu-on-Void, Ubuntu Core, Qubes, and the 2026-11-14 batch)

Re-read each session's own rows first, and append a line to the shared notes
before editing:
- `report.rs` `backup_sources` (the inactive-BE sources). **The ZFSBootMenu
  session** has BEs too: one function that lists a pool's non-booted
  `ROOT/*` datasets as backup sources, owned by whichever session lands
  first, with the other calling it;
- Proxmox's ESP discovery code (a second caller, no change to its output);
- `commands.rs`' ZFS mount loop (the legacy-dataset rule) and its dataset
  create (`readonly` held back; Aeon's read-only btrfs root is a different
  code path);
- `boot.rs` `PACKAGES` and `fingerprint.py`;
- `redact.rs` `GENERIC_HOSTS` and `SYSTEMS`;
- `classes.json`, the holttest1d README's 1D row (Proxmox's, one edit at a
  time), and `check-manifests.py`'s count;
- `vm-mount`'s ZFS mounting (Proxmox's `mount_renamed`; ZFSBootMenu's BEs
  will need the same bootfs-only rule).

TrueNAS's own: `boot.truenas`, load's `truenas` boot arm, the `boot-pool`
name check, `make-truenas.sh` and `testvm/truenas/`.

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| T1 | Fetch the newest 25.10.N ISO and the N-1, N-2 `.update` files (or the N-2 ISO and N-1, N updates; the user's OK first) into rebuild-inputs on Chopin, then check and pin their SHA-256. `make-truenas.sh truenas25` by Decision 1, `testvm/truenas/install.py` and `api.py`, `rebuild-one.sh`, and a copy to Cinqcent (the same SHA-256). Then `vm-mount --ro` with the bootfs-only rule. Settle every *check* (os-release, kernel names, the API on the ISO, the update method, how the BE's children get mounted, fstab, `.system`, RAM floor, dpkg rows, `origin`) and write "What T1 found" here. Done when it boots alone on both hosts with three BEs, SSH works and `boot.environment.query` is right | 1.5 d (+0.3 d on path 2) |
| T2 | bko-holt (High-effort subagent): **dump** gets the inactive BEs as backup sources with mount hints, `boot.truenas` (BEs, versions, kernels, keep, origin; ESPs by p2's type GUID with Proxmox's ro read), `os.product` from `/etc/version`, legacy mounts as sources, `PACKAGES`, `fingerprint.py` and `redact.rs`. **load** gets `readonly` held back and set at the end, plain datasets for clones with the note, legacy datasets mounted at their dumped paths, inactive BEs mounted under `T/run/bko-be/V`, the `boot-pool` name check, the 25.10 gate, the `truenas` boot arm (grub-install both targets per disk, the ESP's fallback copy, `update-grub`) and the data-pool exclusion. Add a TrueNAS fake tree (one disk with three BEs; a mirror with two BEs and a data pool) in `scenarios.rs`. Done when `cargo test` passes and the scenario's plan creates every BE, mounts each for its restore, has no `readonly=on` before the boot stage and one `zfs set readonly=on` per RO dataset after it, and runs `update-grub` once | 1.5 d |
| T3 | `truenas25`'s golden plan and `index.tsv` row; the Test2 registrations go to the batched pass | 0.3 d |
| T4 | `truenas25-mirror`: the build (BIOS, a two-disk boot mirror, `tank`, two BEs), its golden plan and `index.tsv` row, `rebuild-one.sh`. Done as T1 and T3 | 0.5 d |
| T5 | SticTest1 entry and runs for `truenas25`, both archivers, both hosts, 0 FAIL | 0.3 d |
| T6 | HoltTest1 (1D) on Chopin and Cinqcent with the judge rules above: every BE, the ESP, grub.cfg's entries, the DB, the boot, the old-BE boot, and the mirror's per-disk boots with `tank`. RAM peaks go in `ram-sizes.tsv`. Done when all four runs PASS with 0 fsmeta and 0 dump differences | 0.75 d |
| T7 | `testvm/truenas-update --check`, and `in-guest.sh`'s refusal | 0.15 d |
| T8 | Docs: holt/README.md's supported list ("TrueNAS CE 25.10, its boot environments"), `layload.md`'s boot stage (the truenas arm, the held-back `readonly`, inactive BEs), HoltTest1's 1D row and HoltTest1All, SticTest1's README; memory (holttest1d index, a truenas-support note) | 0.1 d |
| | *≈ 5.1 d* | |

T2 can start beside T1 from the facts above, but its golden plan (T3) waits
for T1's dump. Each piece is committed on main when its tests pass and the
user says to commit. Nothing is pushed until the user says so. Source-code
changes go to High-effort subagents ([[effort-max-when-it-pays]]).

**Costs** (est.):
- T1 and T4: a 30-45 min build each on Chopin (`--reserve 14 8`: one
  install plus two updates and their reboots), then the copy;
- qcow2: ~8-12 GiB a guest a host (three BEs at 2-3 GiB each);
- the inputs: ~5 GB a host;
- Cinqcent is the fullest (~69%), so its copies go straight to `vm-store`'s
  disk, not kept twice;
- T5 and T6: about 2 h of queue a host.

## Risks

- **The installer's API may not run on the ISO**, or may need TrueNAS
  Connect's adoption flow in a way that blocks a plain `install`. T1 settles
  it on day one, and path 2 (the serial TUI) is the fallback.
- **`update-grub` under load's altroot.** `10_truenas_linux` calls `zfs` and
  `zpool` and resolves `/` through grub-probe. If it misbehaves in the
  rescuer's chroot, the fallback is to restore `boot-pool/grub`'s
  `grub.cfg` as backed up (it's a backup source anyway), which is right as
  long as the pool, BE names and kernels are unchanged, and they are.
- **The restored initrds.** They come back byte for byte. If TrueNAS's
  initramfs keys on something load changes (the pool GUID is kept; device
  paths aren't used), the active BE wouldn't boot. T6's boot shows it, and
  `truenas-initrd.py -f` is the fallback.
- **The config DB is written while the backup runs.** `middlewared` writes
  `freenas-v1.db` on events, and an idle appliance seldom does. The judge
  checks integrity after boot. A busy system isn't claimed. A pre-backup
  hook that runs `sqlite3 .backup` is a later piece if the user wants it.
- **Archive size.** Three BEs at 2-5 GB each. Chunk dedup helps between
  point releases, but most binaries change. T5 and T6 record the archive
  sizes.
- **Nothing outside the archive needs the `@pristine` snapshots** (Decision
  4). If T1 finds a middleware check that reads them, load remakes
  `@pristine` after each restore. That snapshot is of the restored state,
  not the factory one, and the plan says so.
- **TrueNAS 26** may be released during the work. Decision 8 pins 25.10, so
  26 is a later piece. It may move to Debian 13 and a new kernel, and
  needs its own T1.

## Out of scope

- Restoring data pools (the data pool is left alone, Decision 6); apps
  (`ix-apps` datasets, Docker), VMs and Incus instances on a data pool;
  replication; HA (Enterprise).
- TrueNAS CORE (FreeBSD) and the `freenas-boot` pool name (the GRUB script
  still knows it); TrueNAS before 25.04, whose BE layout was different
  (24.04's split into child datasets *check*); TrueNAS 26 until its own
  spec piece.
- Secure Boot; an arm64 build.
- A boot-pool on USB sticks with `copies=2` (it's only properties: probably
  works, unclaimed).

## Decisions for the user

1. **How the guests are built:** the installer's own JSON-RPC API, then
   the middleware's API for setup and real `.update` files for the BEs
   (recommended: TrueNAS's own installer and updater, no screen scraping);
   or the serial TUI driven by an expect script (+0.3 d, fragile across
   releases); or `install.py` and `truenas_install` replayed in a helper VM
   (our replica of the installer).
2. **The inactive BEs:** backed up as extra backup sources with read-only
   mount hints, and restored into their own datasets (recommended: every BE
   comes back bootable, as GRUB and the UI expect, by ABRoot's existing
   mechanism); or recreated empty, as OmniOS and OpenIndiana do (they'd be
   listed but wouldn't boot); or not recreated at all, so only the active BE
   comes back (GRUB and the UI would stop listing them, which fails this
   guest's purpose).
3. **The read-only datasets:** created writable, restored, then `zfs set
   readonly=on` after the boot stage (recommended); or restored with
   `readonly` dropped and a note (simpler, but it leaves `/usr` writable,
   which TrueNAS's updates and integrity checks don't expect).
4. **Clones and snapshots:** clones come back as plain datasets and
   `@pristine`/`@install-*` aren't remade, each with a note (recommended:
   bko restores files, not ZFS history; the cost is a few MB a BE); or
   remake the clone chain from snapshots (only if T1 shows something needs
   it, +0.5 d).
5. **The boot stage:** reinstall GRUB the way `truenas_install` does (both
   targets on every disk, the ESP's fallback copy) and rebuild the menu with
   `update-grub`, keeping the restored initrds (recommended: the owner's own
   steps); or also rebuild the active BE's initrd with `truenas-initrd.py -f`
   (slower, and it changes a file the judge would otherwise compare byte for
   byte); or restore `grub.cfg` and the ESP byte for byte, with no tools run.
6. **Guests and the data pool:** two guests, `truenas25` (UEFI, one disk,
   three BEs) and `truenas25-mirror` (BIOS, a boot mirror, two BEs, plus a
   data pool `tank` left untouched by the restore and re-imported by the
   restored config) (recommended: the restore TrueNAS users actually need,
   a new boot drive with the data pool kept, and both firmwares between the
   two guests); or no data pool (−0.2 d, but `.system` then never moves off
   the boot pool); or also restore the data pool (outside a boot-drive
   restore, +0.5 d).
7. **The product gate:** dump records `os.product` from `/etc/version`, and
   load refuses a TrueNAS source that isn't 25.10 (recommended); or no
   gate, so the Debian 12 row alone decides.
8. **The release:** the newest 25.10.N on T1's day, its two predecessors for
   the older BEs, all SHA-256 pinned (recommended: the stable Community
   Edition); or TrueNAS 26 if it is stable by then (newer, Debian base
   unknown, a new spec check); or 26's beta (not a release).
9. **HoltTest1's word:** **1D** (recommended: Debian-based ZFS-root
   appliances beside Proxmox, with the same ZFS-host needs; 1D becomes
   twelve runs); or **1Obscure** (where `debian12` is); or a new word
   **1T** (TrueNAS, runnable alone).
10. **SticTest1:** `truenas25` only (recommended: the mirror adds nothing
    for the archivers); or both guests, as Proxmox has.
