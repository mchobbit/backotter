# ZFSBootMenu support (Void Linux)

2026-11-14. Asked for by the user: a Void Linux guest that boots through
ZFSBootMenu (ZBM), built the way ZBM's own Void guide does it, so that
bko-holt is tested on a ZFS root where **the boot loader chooses among boot
environments (BEs)**. No other guest has this restore path. The user
approved this spec on 2026-11-14 with every recommended decision (the last
section).

**Conventions.** est. marks an estimate. *Check* marks a fact the sources
don't settle. It is settled on the first guest (Z1), before the code that
depends on it is written. ZBM facts come from its Void Linux UEFI guide
(docs.zfsbootmenu.org, guides/void-linux/uefi, for ZBM 3.1.0), its
`zfsbootmenu(7)` page, and the v3.1.0 GitHub release (2026-01-01, "built
with OpenZFS 2.4.0 and Linux 6.6, 6.12 and 6.18"). All were read on
2026-11-14. Nothing has been downloaded or installed yet. Code references
are to local `main` at d5e8686 plus the uncommitted tree.

## Summary

- Two new UEFI QEMU guests on a ZFS root booted by ZBM, both glibc Void:
  `void-zbm` (unencrypted, two BEs) and `void-zbm-enc` (the same with
  native encryption and a passphrase, Decision 2). Both run on **Chopin and
  Cinqcent only**, the hosts with OpenZFS. They join HoltTest1 **1V**
  beside `void-musl`. Test2 registrations go in the batched pass.
- **Where ZBM differs from every current guest:**
  - The ESP holds only ZBM's EFI image. ZBM is a Linux kernel plus an
    initramfs with OpenZFS.
  - The kernels and initramfs files live in **each BE's own `/boot`**,
    inside the pool. There is no separate `/boot` and no `grub.cfg`.
  - ZBM imports the pool and reads the pool's `bootfs`.
  - ZBM lists every dataset with `mountpoint=/` as a BE. For the chosen BE
    it reads the `org.zfsbootmenu:*` user properties and kexecs the newest
    kernel in that BE's `/boot`.
- **What bko-holt already does right:**
  - The ESP is in fstab at `/boot/efi`, so its files (the ZBM image)
    already come back with the restore.
  - The dump records every dataset property set locally, user properties
    included (`report.rs:546`). So `org.zfsbootmenu:commandline` and
    `keysource` come back with `zfs create -o`.
  - Load sets `bootfs` again (`commands.rs:2504`).
  - `efi_entries()` remakes NVRAM entries by label and loader path.
- **What it gets wrong:**
  - An **inactive BE** (`canmount=noauto`, not mounted) is created but
    never mounted or filled (`commands.rs:2635`). It comes back empty and
    can't boot.
  - The loader isn't recognised: `\EFI\ZBM\VMLINUZ.EFI` is not GRUB.
  - The boot stage would try to install GRUB.
  - An encryption root whose `keylocation=file://` points into the BE
    itself has no path in load.
- **The fix** (Decisions 3-6):
  - dump detects `zfsbootmenu`, records the BEs, and adds each inactive BE
    as a **hinted backup source**, mounted read-only by the backup. This is
    the mechanism ABRoot's `/part-future` already uses.
  - load mounts each inactive BE and restores it, then runs a new
    `zfsbootmenu` boot arm. That arm installs no loader. It rebuilds the
    default BE's initramfs and makes the two NVRAM entries.
- About 4.1 d (est.), on Chopin and Cinqcent. The user's time is only the
  OKs.

## Why

- **The loader is outside the OS.** Every Linux guest today boots a loader
  that the restored system reinstalls or regenerates: GRUB, systemd-boot,
  proxmox-boot-tool, rEFInd, Limine. ZBM is a standalone image and the OS
  doesn't manage it. It must come back exactly, and what it reads must be
  true on the copy: pool name, `bootfs`, user properties, each BE's
  `/boot`, `/etc/hostid`, and the key.
- **More than one bootable root in one pool.** OpenIndiana and OmniOS have
  BEs too, but they restore only the booted one (openindiana.md, Decision
  3). Here the user asked that every BE comes back bootable. No current
  guest tests that.
- **Native encryption that ZBM unlocks.** The ZFS Thinkpad runs used
  `keylocation=prompt` in the OS's own initramfs. Under ZBM the passphrase
  is asked by ZBM. Then the BE's initramfs unlocks again with a key file
  stored *inside the encrypted BE* (`/etc/zfs/zroot.key`). That is a
  chicken-and-egg problem for load.
- **ZBM is common among ZFS-on-root users** of Void, Arch, Debian and
  Alpine. A Void guest following ZBM's own guide is the reference setup.

## What the guide does (and what the guests keep)

| Step | ZBM's Void UEFI guide | `void-zbm` |
|---|---|---|
| Live system | hrmpf, a Void-based rescue ISO | **the build host itself**, as `make-chimera-zfs.sh` does (Decision 1) |
| Host ID | `zgenhostid -f 0x00bab10c`, copied to `/etc/hostid` | `zgenhostid` with a random ID per build; `/etc/hostid` copied in. (Z1: the guide's 0x00bab10c is not only a placeholder: ZBM's release image passes `spl.spl_hostid=0x00bab10c` to every BE. See "What Z1 found".) |
| Disk | GPT: p1 ESP `1m:+512m` type `ef00`; p2 pool `0:-10m` type `bf00` | the same on a 16G disk (`/dev/vda1`, `vda2`) |
| Pool | `zpool create -f -o ashift=12 -O compression=lz4 -O acltype=posixacl -O xattr=sa -O relatime=on -o autotrim=on -o compatibility=openzfs-2.3-linux -m none zroot DEV` | **as written**. `openzfs-2.3-linux` matters here: the hosts' OpenZFS 2.3.9 can then import and write the pool (vm-mount, the build), the rescue's 2.4.1 too, and ZBM 3.1.0's 2.4.0 too |
| Encrypted | adds `-O encryption=aes-256-gcm -O keylocation=file:///etc/zfs/zroot.key -O keyformat=passphrase`; `/etc/zfs/zroot.key` (mode 000) holds the passphrase; copied into the BE; dracut `install_items+=" /etc/zfs/zroot.key "`; `zfs set org.zfsbootmenu:keysource="zroot/ROOT/${ID}" zroot` | `void-zbm-enc`, as written; the passphrase random per build, in `vms/void-zbm-enc/zfs-passphrase` (mode 600, no newline), never in the repo |
| Datasets | `zroot/ROOT` (`mountpoint=none`); `zroot/ROOT/void` (`mountpoint=/`, `canmount=noauto`); `zroot/home` (`/home`); `zpool set bootfs=zroot/ROOT/void zroot` | the same, plus **a second BE** (below) |
| Install | `XBPS_ARCH=x86_64 xbps-install -S -R MIRROR -r /mnt base-system` (glibc assumed; "ZFSBootMenu does not require glibc") | Void's **glibc** ROOTFS tarball (`VOID_DATE` 20250202), checked with minisign against `make-bootstrap.sh`'s `VOID_KEY`, then `xbps-install -Syu` and `base-system` in the chroot (Decision 4) |
| Chroot config | `rc.conf` keymap and clock; glibc locales; `passwd`; dracut `zol.conf`: `nofsck="yes"`, `add_dracutmodules+=" zfs "`, `omit_dracutmodules+=" btrfs "`; `xbps-install zfs` (DKMS) | as written, plus what every guest has: the `tester` user, root's SSH key, `dhcpcd`, `sshd`, `agetty-ttyS0`, the ARC capped at 512 MiB |
| ZBM properties | `zfs set org.zfsbootmenu:commandline="quiet" zroot/ROOT` (inherited by every BE) | `"loglevel=4 console=tty0 console=ttyS0,115200n8"` on `zroot/ROOT`, so the BE's kernel talks on the serial port. Set **from the host**, not the chroot (below) |
| ESP | `mkfs.vfat -F32`; fstab `UUID=… /boot/efi vfat defaults 0 0` | as written |
| ZBM image | **A:** `curl -o /boot/efi/EFI/ZBM/VMLINUZ.EFI -L https://get.zfsbootmenu.org/efi`, copied to `VMLINUZ-BACKUP.EFI`. **B:** `xbps-install zfsbootmenu systemd-boot-efistub`, `config.yaml`, `generate-zbm` | **A**, with the v3.1.0 release EFI pinned by SHA-256 and checked against the release's `sha256.txt` and `sha256.sig` (Decision 5). Its embedded command line gains `console=ttyS0,115200n8 zbm.timeout=5` through the release's own `zbm-kcl` tool |
| NVRAM | `efibootmgr -c -d DISK -p 1 -L "ZFSBootMenu (Backup)" -l '\EFI\ZBM\VMLINUZ-BACKUP.EFI'`, then the same for `"ZFSBootMenu"` / `VMLINUZ.EFI` (the last one made comes first in BootOrder); no `-u` | as written, in the build's own OVMF variables (`vms/GUEST/OVMF_VARS.fd`); no `EFI/BOOT/BOOTX64.EFI` fallback (Decision 7) |
| zpool.cache | none | none |

**The second BE.** The guide makes one BE. A BE test needs at least two, so
the builder makes them the way a careful user does around an upgrade:

1. It installs `zroot/ROOT/void` with Void's default kernel series
   (`linux`, **6.18**: Z1 disproved 6.12) and snapshots it as
   `@pre-mainline`.
2. It clones that snapshot as **`zroot/ROOT/void-6.18`** (Z1: named after
   the default series, so not `void-6.12`) (`mountpoint=/`,
   `canmount=noauto`, as the guide says every BE needs). It sets a local
   `org.zfsbootmenu:commandline="%{parent} bko.be=void-6.18"` on it. That
   word shows on the BE's `/proc/cmdline`, and it tests a local user
   property with ZBM's `%{parent}`.
3. It installs a newer series in `zroot/ROOT/void` only: **`linux7.2`**
   (Z1: `linux-mainline` is 7.3, past zfs 2.4.4's `Linux-Maximum` 7.2, and
   its DKMS build fails; the builder takes `linux-mainline` only when zfs
   supports its series), so the default BE's newest kernel differs from
   the clone's. ZBM boots the newest kernel in a BE's `/boot`, so the BE
   that boots is visible from `uname -r` alone.
4. It writes a marker file `/etc/bko-be` in each BE, naming that BE.

The result is two BEs, one the bootfs and one a clone of a snapshot of it.
Both carry `/boot/vmlinuz-*` and `/boot/initramfs-*.img`, built by
dracut through `xbps-reconfigure`. The snapshot `@pre-mainline` stays as
the clone's origin.

**Built from the host, like `chimera-zfs`.** The builder is a new
`testvm/make-void-zfs.sh WORK_DIR NAME [--encrypt]`, run as root, from
`make-chimera-zfs.sh`'s pattern:

- a sparse raw disk on a loop device;
- the pool made by the host's OpenZFS under a temporary in-core name
  (`zpool create -t`);
- the tarball unpacked with bsdtar, which keeps file capabilities;
- `xbps-install` run in a chroot with a `/dev` of its own;
- **no `zpool` or `zfs` command in the chroot.** Void's newer userland
  doesn't run against the host's module. So `zfs set`, `zfs snapshot` and
  `zfs clone` run from the host;
- the pool exported at the end, then converted to qcow2.

DKMS builds the `zfs` module for both kernels in the chroot (*check* it
builds for a kernel that isn't running, as on Debian). Then one boot under
`vm-run --uefi --no-secure-boot` with the build's variables proves that ZBM
finds the pool and boots `void` on its own, and that SSH works. For the enc
guest, `console-boot.py` types the passphrase at ZBM's prompt. The image
gets a `fresh-install` snapshot.

## What bko-holt does today, and what changes

**dump:**

- `osrel.rs`: the `("void", "", "Void Linux")` row already accepts glibc
  Void. `boot.rs` already reads xbps's package database.
- **The loader.** `detect_loader` decides from BootCurrent's file and from
  what the ESPs hold. `\EFI\ZBM\VMLINUZ.EFI` matches nothing today
  (*check* what it says now: probably `null`, or GRUB from the package
  list). New:
  - `name = "zfsbootmenu"` when the booted file, or any `EFI/**/*.EFI` on
    an ESP, is a PE image with `.osrel` and `.cmdline` sections whose
    `.osrel` says ZFSBootMenu. **Disproved in Z2 (2026-11-14):** the 3.1.0
    release image's sections are `.text .rodata .data .sbat .sdmagic
    .reloc .osrel .cmdline .initrd .linux` (systemd-stub 256.6), but its
    `.osrel` is the build container's os-release (`PRETTY_NAME="Void
    Linux"`; generate-zbm copies the host's too), its `.cmdline` is
    `quiet loglevel=0 nomodeset`, and the initramfs is zstd: no plain byte
    says ZFSBootMenu. So dump takes a unified kernel image (`.linux` and
    `.initrd`) for ZBM when its `.osrel` names ZBM, or it sits in an
    `EFI/ZBM` (`zbm`, `zfsbootmenu`) directory or is named
    `zfsbootmenu*`, or its command line has a `zbm.` argument, or `/` is a
    ZFS BE and its command line has no `root=`, or it is a byte copy of
    one (`holt/src/zfsbootmenu.rs`);
  - a new **`boot.zfsbootmenu`** object holding:
    - each ZBM image's ESP path, size, SHA-256, version (from `.osrel`,
      so none for the release image: its SHA-256, kernel `6.12.63_1` and
      stub identify it) and embedded command line (read from the PE
      section in Rust, no `objcopy`);
    - the pools it would import;
    - **`bes`**: every dataset with `mountpoint=/` (and
      `org.zfsbootmenu:active` not `off`, or `mountpoint=legacy` with it
      `on`, as `zfsbootmenu(7)` says). For each BE: whether it is the
      bootfs, whether it is mounted, its `origin`, and the kernels and
      initramfs files in its `/boot` (read from the BE's mount, below).
- **Inactive BEs become backup sources** (Decision 3). For each BE that
  isn't mounted, `storage.backup_sources` gets an entry at
  **`/run/bko-be/NAME`** (NAME the BE's last component), with a hint that
  the backup mounts it with `mount -t zfs -o ro,zfsutil DATASET PATH` and
  unmounts it after. This is the hint ABRoot's `FUTURE_AT` already uses
  (`abroot.rs:457`). It isn't a new mechanism in bko. The dump mounts each
  BE the same way to list its `/boot`, then unmounts it at once.
  - Its datasets below the BE (none in these guests) would follow with
    their own entries.
  - A BE on an encryption root whose key isn't loaded can't be mounted. The
    dump warns, and the BE gets today's empty-dataset treatment.
- `PACKAGES` (`boot.rs`): `zfsbootmenu` (option B's package, so a
  package-built ZBM is named too), `zfs`, `dracut`, `linux-mainline`.
  `fingerprint.py` mirrors them.
- Snapshots (here `@pre-mainline`) are recorded as today and not backed up
  (as on every ZFS guest).

**load:**

- **Format.** `zpool create` with the source's features and its
  `compatibility=openzfs-2.3-linux`, then `zpool reguid -g`, as on every
  ZFS guest. Datasets in order with their local properties, user
  properties included: `org.zfsbootmenu:commandline` on `zroot/ROOT` and
  on the clone, `keysource` on `zroot`. *Check* that a property name with a
  `:` passes `DS_PROPS_SKIP` and the quoting untouched, and that `%{parent}`
  is passed literally. Then `zpool set bootfs=zroot/ROOT/void` (exists).
- **The clone.** Decision 3's recommendation restores `void-6.18` (Z1's name) as a
  **plain dataset**, not a clone. Its origin snapshot isn't in the backup.
  This is the precedent judge.py's `origin` rule already allows. A note
  says so. Space use is that of a full copy (~1.2 GiB here). The
  alternative keeps the clone (Decision 3, option b).
- **Mount and restore.** After today's mounts:
  - each inactive BE is mounted at `TARGET/run/bko-be/NAME` with `zfs mount
    -o zfsutil`, or `mount -t zfs` for a `canmount=noauto` dataset whose
    mountpoint is `/`, *check* which one works;
  - the archiver restores the whole tree, the BEs into their mounts;
  - the BEs are unmounted, and `TARGET/run/bko-be` is removed from the
    default BE. `/run` is a tmpfs at boot, but the directory must not be
    left in the dataset.
- **Encryption** (`void-zbm-enc`). The source's root is `zroot` with
  `keyformat=passphrase` and `keylocation=file:///etc/zfs/zroot.key`. That
  file lives in the encrypted BE, so it can't be read before the pool
  exists. Load takes the passphrase from `--key-file zroot=FILE`, as the
  ZFS Thinkpad runs did. It creates the pool with
  `keylocation=file://FILE` and sets `keylocation` back to the source's
  value after the restore (`commands.rs:1572-1590` already does this for
  prompt; extended to `file://`).
  - **New check:** after the restore, the target's `/etc/zfs/zroot.key`
    must be byte-equal to FILE. If not, it's a problem: the BE's initramfs
    would carry a key that doesn't open the pool.
  - Without `--key-file`, the plan has a problem naming the source's key
    path.
- **Boot stage: a new `zfsbootmenu` arm**, before the GRUB arms (Decision
  6). In the default BE's chroot it runs:
  1. **no loader install.** The ZBM images came back with the ESP
     (fstab's `/boot/efi`). A note gives their versions and SHA-256, which
     the plan compares with the dump's;
  2. the initramfs of the **default BE only**, as on `void-musl` today
     (`xbps-reconfigure -f linuxN`, for each kernel package in its
     `/boot`). This takes the restored `/etc/hostid` and, on the enc
     guest, the restored key file;
  3. the inactive BEs' `/boot` **stays as restored** (Decision 6). Their
     initramfs files hold the same `/etc/hostid` (load writes it back), and
     ZBM imports by name, so they still boot. HoltTest1 proves it by
     booting each one;
  4. `efi_entries()` makes "ZFSBootMenu (Backup)" and "ZFSBootMenu" again,
     in the source's order. *Check* that the dump records no optional data
     on them (the guide uses no `-u`). If a source had some, `efi_entries`
     must pass it with `-u`, a general fix, since it drops it today
     (`commands.rs:3673`).
- `--removable` / `--no-nvram`: nothing is written beyond what the ESP
  holds. A plan note says a copy with fresh NVRAM and no
  `EFI/BOOT/BOOTX64.EFI` won't boot by itself, as on the source.
- Refused in the plan, by name: a `zfsbootmenu` source whose pool is
  missing from the layout, a bootfs missing from the datasets, a ZBM image
  missing from the restored ESP's listing. `zbm.prefer` and `.kcl` files
  aren't refused: they come back as files and properties.

**`redact.rs`:** the guests' host names join `GENERIC_HOSTS`. The pool
`zroot` and BE names are generic. The enc guest's key file content is never
in a dump (the dump lists `/boot`, not `/etc/zfs`). *Check* that the leak
scan covers `zfs-passphrase`.

**The rescue image:** no change ([[one-kernel-rescue]]). OpenZFS 2.4.1
creates a pool with `compatibility=openzfs-2.3-linux` and its feature set.
The rescuer doesn't need ZBM.

## Guests

| Guest | Layout | What it covers |
|---|---|---|
| `void-zbm` | 16G, GPT: ESP 512 MiB, `zroot` (rest − 10 MiB); BEs `zroot/ROOT/void` (bootfs, `linux` 6.18 + `linux7.2`; Z1) and `zroot/ROOT/void-6.18` (clone, `linux` only, its own commandline word); `zroot/home` | ZBM detected and recorded; the ESP's ZBM images byte for byte; `bootfs` and the user properties; **both BEs restored and each one booted** from ZBM's menu |
| `void-zbm-enc` | the same, `zroot` an encryption root (aes-256-gcm, passphrase, `keylocation=file:///etc/zfs/zroot.key`), `keysource=zroot/ROOT/void` on `zroot`, the key in each BE's initramfs | the passphrase taken by `--key-file` and checked against the restored key file; ZBM's prompt answered on the serial console; `keysource` restored; one prompt a boot |

Both use `MEMORY=3072` (ZBM's initramfs plus the ARC; *check* that 2048 is
too little), 2 vCPUs, and `vm-run-options` `--uefi --no-secure-boot
--guest-vars uefi-vars.fd` (Z1: vm-run's `--guest-vars`; MEMORY 2048).
Rebuild inputs go in `/bkoscratch/rebuild-inputs/void-zbm/` on Chopin and
Cinqcent: the glibc ROOTFS with its `sha256sum.txt` and `.sig`, ZBM's
release EFI, `sha256.txt`, `sha256.sig` and `zbm-kcl`. `void-zbm-enc`
reads them from there. Each guest gets:

- `rebuild-one.sh` lines (root builder, like `chimera-zfs`);
- `holttest1d/ram-sizes.tsv` rows (est. D 6, MEM 3);
- both hosts in `vm-store` publish, and `needs_host_zfs`;
- a GuestTest1 grade;
- the README tables in `make-bootstrap.sh`'s family. `make-void-zfs.sh`
  documents itself like `make-chimera-zfs.sh`.

**glibc, not musl** (Decision 4). The guide's default is glibc. The ZFS
path is the same on both libcs: DKMS, dracut, and a ZBM image with its own
libc. `void-musl` already covers musl Void. A glibc Void guest adds
`glibc-locales` and the glibc ROOTFS, which no guest has today. Making
these guests musl would test musl twice and glibc Void never.

**vm-update:** `in-guest.sh`'s `Linux.xbps` section works on the default
BE. A new kernel triggers dracut and DKMS there. The clone is not updated,
as on a real machine. `KERNELS_MAX` in `guest_judge.py` counts the default
BE's `/boot` only. On `void-zbm-enc`, vm-update's boots need the
passphrase typed. *Check* that it takes `console-boot.py` as
`pikaos4-luks`'s update does. If not, that's a small vm-update change in
Z5. `fingerprint.py`'s `LINUX_KINDS` and `pkgset.sh` name both guests.

**vm-mount:** today it mounts the bootfs's tree. For the judge it learns
`--be NAME`: it mounts `zroot/ROOT/NAME` read-only as the tree's root, so
HoltTest1 can compare each BE. On the enc guest it loads the key from
`vms/void-zbm-enc/zfs-passphrase` (`zfs load-key -L file://…`), read-only.

## Tests

**SticTest1** (Decision 8): `testvm/stictest1/void-zbm` only. Its
`inner.sh`, `fixture.sh` and `judge.py` come from `chimera-zfs`'s: a scratch
pool made like `zroot`, with the same `compatibility`, datasets and
properties. Both archivers, both hosts. The enc guest adds nothing for the
archivers, since they see plaintext files. `manifests.json`, `limits.json`
and `check-manifests.py`'s count go up by one. Whoever lands last sets the
count to the entries present.

**HoltTest1 1V** (Decision 9): both guests join `void-musl`, and 1V grows
from one run to three. The README's 1V row becomes "Void Linux (musl;
glibc on ZFSBootMenu)". Runs are on Chopin and Cinqcent, in /mnt/bko-ram
under `cmp-run.sh --reserve 8 3` (est.), finished guests moved to disk.
`void-musl`'s cells stay on all four hosts. ht1.sh and judge.py change:

- **Every BE compared.** `vm-mount --be` on source and copy, for each BE in
  `boot.zfsbootmenu.bes`. The fsmeta comparison is as for `/`. The clone's
  `origin` is allowed to be `-` on the copy (decision 3a). The default BE's
  `boot/initramfs-*` is allowed to differ (rebuilt). **The inactive BE's
  initramfs must be byte-identical** (not rebuilt).
- **The ESP:** `EFI/ZBM/*.EFI` byte-identical (restored, not rebuilt).
- **The pool:** `bootfs`, `compatibility`, every `org.zfsbootmenu:*`
  property at the same dataset with the same value and source
  (local/inherited); `keysource` and `keylocation` on the enc guest.
- **Boot stage:**
  1. The copy boots alone through the source's NVRAM entries (as
     `proxmox9`), with no keys sent. ZBM's countdown ends and it boots
     `void`. The check: `/etc/bko-be` says `void`, `uname -r` is the
     newer kernel (7.2 in Z1), and `/proc/cmdline` has `root=zfs:zroot/ROOT/void`.
  2. **Then each other BE**, picked from ZBM's menu on the serial console
     by `console-keys.py` (Vanilla OS's tool for GRUB): Escape during the
     countdown, then down-arrow to the BE's line, then Enter. *Check* ZBM's
     serial rendering and key handling (fzf). The check: `/etc/bko-be`
     says `void-6.18`, `uname -r` is 6.18, and `/proc/cmdline` has
     `bko.be=void-6.18` (Z1's names; Z1 settled the keys, below).
  3. On `void-zbm-enc`, `console-boot.py` types the passphrase at ZBM's
     prompt first, on every boot. The judge also checks that no second
     prompt came: the BE's initramfs opened the pool with its key file.
  4. Each boot runs under `-snapshot`, so the copy's disk doesn't change
     between them.
- `ram-sizes.tsv` rows measured in Z7.

**Test2:** golden plans `holt/tests/corpus/void-zbm/` and `void-zbm-enc/`
(`src.json`, `plan.txt`, `index.tsv` rows, the corpus README). They come
from `bko-holt dump` of each guest, made by this session. The enc plan is
made with `--key-file` pointing at a fixed test path. Registrations go in
the siblings' **one batched Test2 pass**:
- `classes.json` (the V family, `uefi`, `needs_host_zfs`, and `console` for
  the enc guest's typed passphrase);
- `holt/spec.json`, `guest/spec.json`, `estimates.tsv`;
- `t2lib/plan.py`'s counts, `tests/fixtures/p2/world.py`;
- `rows_holt.py`'s per-BE boot rows if the batched pass's shape needs them;
- re-blessed goldens.

It is done when:
- two corpus builds have the same sha256;
- leak scans are 0 on both hosts and the Mac;
- no other plan changes;
- Test2's own tests pass.

**BackShot1:** not proposed. The Thinkpad on Void with ZBM is plausible and
would test real NVRAM, but it would mostly repeat `void-zbm`. It could come
later, through BackShot1All.

## Shared lines (with TrueNAS, Aeon/MicroOS, Ubuntu Core, Qubes)

Each session re-reads its own rows first and appends a line to the shared
notes before editing:
- `boot.rs` `PACKAGES` and `fingerprint.py`;
- `redact.rs` `GENERIC_HOSTS`;
- `classes.json`;
- `check-manifests.py`'s count;
- `detect_loader`. **TrueNAS SCALE** also boots ZFS BEs, through GRUB.
  Its session may touch the same BE code in load (`commands.rs:2620-2640`).
  One edit at a time, and the BE mounting is written so both use it.

Its own work: `boot.zfsbootmenu`, the hinted BE sources, load's BE
restore and `zfsbootmenu` arm, the key-file check, `vm-mount --be`, and
`ht1.sh`'s per-BE boot.

## What Z1 found

**The inputs** (2026-11-14, with the user's OK), in
`/bkoscratch/rebuild-inputs/void-zbm/` on Chopin, read-only. Cinqcent gets
a copy once it is back from its NVMe install.

| File | Bytes | SHA-256 | Checked by |
|---|---|---|---|
| `void-x86_64-ROOTFS-20250202.tar.xz` | 48,895,416 | `3f48e6673ac5907a897d913c97eb96edbfb230162731b4016562c51b3b8f1876` | `sha256sum.txt`, whose `sha256sum.sig` minisign verifies against `make-bootstrap.sh`'s `VOID_KEY` ("only valid for images with date 20250202") |
| `zfsbootmenu-release-x86_64-v3.1.0-linux6.12.EFI` | 64,871,424 | `131d55c7b385df8cd319d267b6aa15d97f8c32c94f5149bd906556715fadf4e7` | `sha256.txt`, whose `sha256.sig` is a **signify** signature, verified with `signify-openbsd -V` against `zfsbootmenu.pub` |
| `zfsbootmenu.pub` | 111 | key `RWSUawlGznCLF8NxEb1EyrGpP2eIR9l7JIZgLzCdv+64uBDvfXNJm+cF` | fetched from the repository's `releng/keys/zfsbootmenu.pub` at tag v3.1.0, the same bytes as on master; to be pinned in the builder |
| `zbm-kcl` | 12,138 | `16edae3eee5df9a0b133734bc4d8a8cb68ca73ac30f98cb21cea3212b051ff01` | **not in `sha256.txt`**: pinned by SHA-256 alone, and read before the builder runs it |

`get.zfsbootmenu.org/efi` (the guide's URL) redirects to this
`linux6.12` release EFI. So the guide's option A is exactly this file.

**The build** (2026-11-14, Chopin). `testvm/make-void-zfs.sh` built
`void-zbm` through `rebuild-one.sh` in 1037 s, with no package cache. The
qcow2 is 2,746,024,014 bytes, SHA-256
`108c859f1524632dca63f3dd0e8139ff73aeb4efcc0823fa2b5696dc88a98244`, in
`~/backotter-work/vms/void-zbm` on Chopin. The pool holds 1.78G. The
clone holds 56K of its own.

**Kernel series** (*check* settled; the spec's guesses were wrong):
- Void's `linux` is **6.18** now (`linux6.18-6.18.63_1`), not 6.12.
- `linux-mainline` is **7.3** (`linux7.3-7.3.4_1`). Void also has
  `linux7.2`.
- Void's `zfs` is 2.4.4 (DKMS). Its `META` says `Linux-Maximum: 7.2`.
  DKMS fails for 7.3 ("Building DKMS module: zfs-2.4.4... FAILED!"). dracut
  then makes the 7.3 initramfs anyway, without zfs. ZBM would boot that
  kernel, as the newest, and it can't mount `/`.
- So the builder installs **`linux7.2`** (7.2.15_1) in `void`. It takes
  `linux-mainline` only when that series is within zfs's `Linux-Maximum`,
  else the newest series that is. A Void ZFS user must make the same choice.
- The clone is named after the default series: **`zroot/ROOT/void-6.18`**,
  not `void-6.12`, with `bko.be=void-6.18`. The builder derives the name
  from `linux`'s dependency. Its `uname -r` is 6.18.63_1. `void`'s is
  7.2.15_1.
- `base-system` has no kernel headers. The builder installs
  `linux-headers`, and `linux7.2-headers` with `linux7.2`. Without them
  DKMS builds nothing.

**DKMS for both kernels** (*check* settled): yes, in the chroot under
Chopin's kernel. Void's kernel hook `10-dkms` builds for the kernel being
configured, not for `uname -r`. `dkms status` in the guest lists zfs 2.4.4
installed for 6.18.63_1 and 7.2.15_1. The module is
`/usr/lib/modules/KVER/extra/zfs.ko`, uncompressed (DKMS wants `zstd`,
which `base-system` lacks). In the guest, with 2048 MB, a forced `dkms
build` for 7.2 takes 316 s and leaves at least 1.0 GiB available.

**The initramfs** (*check* settled). dracut 112 (`zol.conf` as the guide),
not host-only. Each image is about 67 MB, gzip, after an early-microcode
cpio. Each has `etc/hostid` (the guest's) and `zfs.ko`. Neither has
`etc/zfs/zpool.cache`. Neither holds the pool's GUID, in decimal or hex
(checked in both unpacked images). dracut's zfs module runs `zpool` while
it builds; in the chroot (no `/dev/zfs`) that prints "/dev/zfs and
/proc/self/mounts are required." and goes on. So Decision 6 holds today: a
`zpool reguid` breaks nothing the inactive BE's initramfs holds.

**ZBM's PE image** (*check* settled):
- Sections: `.text`, `.rodata`, `.data`, `.sbat`, `.sdmagic`, `.reloc`,
  `.osrel`, `.cmdline`, `.initrd` (zstd cpio, 50.8 MB), `.linux` (13.4 MB).
  The stub is systemd-stub 256.6 (`.sdmagic`; `.sbat` names
  `systemd-stub.void`).
- **`.osrel` does not say ZFSBootMenu.** It is the build container's Void
  `os-release` (`NAME="Void"`, `ID="void"`). ZBM's identity is in the
  `.initrd`: `etc/zbm-release` and `etc/os-release` say
  `NAME="ZFSBootMenu"`, `ID="zfsbootmenu"`, `VERSION="3.1.0"`,
  `BUILD_ID="2026-01-01"`; `etc/zbm-commit-hash` says `v3.1.0`. So the
  name and version are readable only from the `.initrd` (zstd, then cpio),
  as Z2 also found. Its kernel is 6.12.63_1.
- The release's `.cmdline` is `quiet loglevel=0 nomodeset` (26 bytes, no
  NUL). 4096 bytes lie between it and `.initrd`.
- After `zbm-kcl -a console=ttyS0,115200n8 -a zbm.timeout=5` it is `quiet
  loglevel=0 nomodeset console=ttyS0,115200n8 zbm.timeout=5` plus a NUL
  (64 bytes). The file size stays 64,871,424.
- objcopy stamps the PE header's time and checksum (offsets 0x88 and
  0xd8). So two runs gave two SHA-256s. With `SOURCE_DATE_EPOCH=1767225600`
  (the release's BUILD_ID) the image is the same on every build:
  `d4c2af93db661730e74d923c712a699c3d9f05681329c4db4d3ca2bfb6cdf84b`. The
  ESP's `VMLINUZ.EFI` and `VMLINUZ-BACKUP.EFI` are that image.

**NVRAM after the build** (*check* settled). efibootmgr 18, run in the
guest by the first boot, as the guide runs it:
- `BootOrder: 0008,0007,0000,0001,0002,0003,0004,0005,0006`.
- `Boot0008* ZFSBootMenu HD(1,GPT,…,0x800,0x100000)/\EFI\ZBM\VMLINUZ.EFI`
  and `Boot0007* ZFSBootMenu (Backup)` (`VMLINUZ-BACKUP.EFI`). **No
  optional data** on either. OVMF's own entries are 0000 UiApp, 0001 the
  disk, 0002-0005 PXE and HTTP, 0006 the shell; 0001-0005 carry 16 bytes of
  it.
- The check boot came through `BootCurrent: 0008`.
- How the entries are made: the first boot reaches ZBM through a temporary
  copy at `EFI/BOOT/BOOTX64.EFI`. A one-shot `rc.local` hook in `void` runs
  the guide's two `efibootmgr -c` commands, removes `EFI/BOOT` and powers
  off. The ESP then holds only `EFI/ZBM` (Decision 7). The entries live in
  `vms/void-zbm/uefi-vars.fd`.

**A fresh variable store doesn't boot it** (as Decision 7 says). OVMF tries
the disk ("UEFI Misc Device … Not Found"), PXE and HTTP, then its shell.
But **vm-run, vm-update, compare.py and rebuild-one.sh's boot check all use
a fresh store**; only `ht1.sh` and `bs1-qemu.py` boot a copy of
`uefi-vars.fd`. So with `vm-run-options` `--uefi --no-secure-boot`,
`vm-update` and `rebuild-one.sh`'s boot check can't boot `void-zbm`
(rebuild-one's boot was seen to fail so). This spec missed it. `vm-run
--uefi-vars uefi-vars.fd` in `vm-run-options` would write the store in
place and lock it against a second VM. **Fixed (the user chose the vm-run change, 2026-11-14):** vm-run's new
`--guest-vars FILE` boots a copy of FILE with `--ram` or `--snapshot`
(FILE only read), writes it in place only for a writable boot of the image
beside it, and loses to an explicit `--uefi-vars`. A relative FILE is
looked up in the working directory, beside IMAGE, then along IMAGE's
backing chain. `vm-run-options` is `--uefi --no-secure-boot --guest-vars
uefi-vars.fd`. vm-lib.sh, compare.py (`source_hardware()`: source boots
only), rebuild-one.sh's boot check and ht1.sh (source boots only, never
restored ones) take it. Restored disks in compare.py and BackShot2 tier 1
get a fresh store, so they don't boot `void-zbm` without restored NVRAM:
that is honest, and expected. HoltTest1's restore still runs
`--no-nvram` and boots the copy on the source's store (Decision 7).

**ZBM over the serial console** (*check* settled):
- The countdown shows on ttyS0: "[RETURN] boot now [ESCAPE] boot menu",
  "Booting zroot/ROOT/void in 5 seconds", then "Booting
  /boot/vmlinuz-7.2.15_1 for zroot/ROOT/void ...". Alone it boots `void`.
- The fzf menu draws on ttyS0 and takes keys from it: "[ Boot
  Environments ] … Sorted by: Name", `zroot/ROOT/void` then
  `zroot/ROOT/void-6.18`, the cursor on `void` ("(default, r/o) -
  vmlinuz-7.2.15_1").
- `console-keys.py` with Escape, 3.5 s of nothing, Down (`\x1b[B`), 1 s,
  Enter booted `void-6.18`: `/etc/bko-be` said `void-6.18`, `uname -r`
  6.18.63_1.
- console-keys.py's PATTERN must not span ZBM's colour codes. "Booting
  zroot/ROOT/void in [0-9] seconds" matched the log afterwards but not
  live: an escape code split across two reads. "seconds" works.

**/proc/cmdline** (*check* settled):
- `void`: `root=zfs:zroot/ROOT/void loglevel=4 console=tty0
  console=ttyS0,115200n8 spl.spl_hostid=0x00bab10c`.
- `void-6.18`: `root=zfs:zroot/ROOT/void-6.18 loglevel=4 console=tty0
  console=ttyS0,115200n8 bko.be=void-6.18 spl.spl_hostid=0x00bab10c`. So
  `%{parent}` expands to `zroot/ROOT`'s value.
- **`spl.spl_hostid` is ZBM's 0x00bab10c**, on every boot. It is not the
  guest's `/etc/hostid` (0x1e3c938e in this build, random), nor Chopin's
  (0x355d4da0), which made and exported the pool. 0x00bab10c is the
  guide's `zgenhostid -f 0x00bab10c`. So that value is not just a
  placeholder (as "What the guide does" says): it is the hostid ZBM's
  release image hands every BE. With a random `/etc/hostid`, the guest's
  ZFS module runs as 0x00bab10c while `hostid` says 0x1e3c938e. It boots
  and imports. Whether the builder should use the guide's 0x00bab10c
  instead is for the user; the builder keeps the random one, as approved.

**MEMORY** (*check* settled): **2048 is enough**. With 2048 MB it boots
both BEs, and the forced DKMS build above leaves at least 1.0 GiB
available. `MEMORY=2048`, and `vm-run-options` has no `--memory`.

**The rest of the guest.** `zroot`'s `bootfs`, `compatibility`
(`openzfs-2.3-linux`), `autotrim` and `ashift` are local; `cachefile` is
`-`. The ARC max is 536870912. `iputils-ping` keeps `cap_net_raw=p`.
`tester` is in `wheel`. dhcpcd, sshd and the agetty on ttyS0 run (Void's
tty1-6 too). The guest's OpenZFS is 2.4.4 on a pool it never upgrades.

**vm-update --try** (*done*, with a workaround). With `vm-run-options`
`--uefi --no-secure-boot --uefi-vars COPY` (a throwaway copy of
`uefi-vars.fd`), `vm-update --try --hosts ''` booted it, updated (nothing
was newer), rebooted into 7.2.15_1 and said "ok ... nothing to merge" in
70 s. Since the `--guest-vars` change, `vm-update --try --hosts ''` is
ok with no workaround, and `uefi-vars.fd` (sha256 `e5b37614…2f83`) and the
qcow2 are unchanged after it.

**vm-mount.** `vm-mount --ro` on Chopin (OpenZFS 2.3.9) imports the pool
read-only and mounts `zroot/ROOT/void` at the root, `zroot/home` and the
ESP beneath it. It takes the first dataset by name with `mountpoint=/`
and `canmount` not `off`, not the `bootfs`. Here that is `void` either
way ("void" sorts before "void-6.18").

**The build's own costs.** About 800 MB of packages per build (the mirror
gave 0.2-1.7 MB/s). `repo-default` once failed to resolve: `xbps-install
-S` then went on with the tarball's own stale index and exited 0, so the
builder checks the sync for errors and retries each xbps command 3 times.
`BKO_XBPS_CACHE` keeps the packages on the host between builds.

## What Z5 found

(2026-11-16, Chopin, under `cmp-run.sh --reserve`, in /mnt/bko-ram.)

- **The dump.** The static bko-holt `c6f179fb…` (d23c9ab) dumped the
  live `void-zbm-enc`, booted by its own vm-run-options under `--snapshot`
  with `console-boot.py` typing `zfs-passphrase` at ZBM's prompt (SSH up
  in about 16 s). The qcow2 (`b5b62a16…`) and `uefi-vars.fd`
  (`5c43137b…`) have the same sha256 before and after. The dump has the
  loader `zfsbootmenu`, `boot.zfsbootmenu` with both images (the same
  `d4c2af93…` as `void-zbm`'s), both BEs (each read, none locked), the
  hinted `/run/bko-be/void-6.18`, both NVRAM entries, `zpool_cache` null,
  and `zroot`'s `encryption=aes-256-gcm`, `keyformat=passphrase`,
  `keylocation=file:///etc/zfs/zroot.key`, `encryptionroot` and
  `org.zfsbootmenu:keysource=zroot/ROOT/void`. No key material: no
  `pbkdf2*` property, and the passphrase's bytes are in none of the raw
  dump, src.json, plan.txt or the index row (as is, UTF-16LE, hex). No
  warnings.
- **The golden plan** (`holt/tests/corpus/void-zbm-enc`, 133 lines) is as
  the Load section says: `zpool create` with `encryption`, `keyformat`,
  `keylocation=file:///srv/bko-keys/zroot` (golden_plans.rs's
  `--key-file`) and the `keysource`, `cachefile=none`; both BEs, the clone
  as a full copy; after the restore, `cmp` of the key file against the
  restored one, then `keylocation` set back; dracut in the default BE only;
  no loader install; both NVRAM entries in the source's order. redact.rs
  names the key file by a pseudonym, `/etc/zfs/keyfile1.key`, in both
  the check and the `keylocation`.
- Two corpus builds gave the same bytes (src.json `fff7adb5…`). Leak
  scans: 0 findings on Chopin (134 of 139 strings, the passphrase among
  them, and the raw dump's identifiers), Cinqcent (110 of 116) and the Mac.
  Lib tests (600) and golden_plans pass from the Mac's tree; void-zbm-enc
  is the only new plan. vanilla3's plan already differed by one note line
  at HEAD before this (the thin pool's `data_used_bytes` note from Qubes's
  Q2), not from Z5; its re-bless is left to its owner.
- **vm-update --try --hosts '' void-zbm-enc** is ok with no change:
  vm-update's prompt pattern takes ZBM's "Enter passphrase" and types
  `zfs-passphrase`; it rebooted into 7.2.15_1 ("would merge: 2
  package(s) changed"). **vm-mount --ro --key zfs-passphrase** mounts
  `void`, `home`, the ESP and `void-6.18` at `/run/bko-be/void-6.18`, and
  vm-umount exports the pool again. The sha256s are unchanged after both.
- Not done here: the Test2 registrations (the batched pass). Cinqcent's
  own-strings leak scan of `void-zbm`, owed since Z4, was done in Z7's
  prep (116 strings, 0 findings).

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| Z1 | Fetch Void's glibc ROOTFS (20250202, its `sha256sum.txt` and `.sig`) and ZBM v3.1.0's release EFI, `sha256.txt`, `sha256.sig` and `zbm-kcl` into rebuild-inputs on Chopin (the user's OK first); pin their SHA-256 and settle how `sha256.sig` is checked. Write `testvm/make-void-zfs.sh`, build `void-zbm`, add the `rebuild-one.sh` line, copy to Cinqcent (same SHA-256), run `vm-update --try`, and add `vm-mount --ro` and `--be`. Settle every *check*: kernel series, DKMS for both kernels, ZBM's PE sections, ZBM over serial and its menu keys, `/proc/cmdline`'s root= form, NVRAM order, MEMORY. Write "What Z1 found" here. Done when it boots alone on both hosts into `void`, the menu boots `void-6.18` (Z1's name for the clone), SSH works and `vm-update --try` is ok | 1 d |
| Z2 | bko-holt dump (High-effort subagent): `zfsbootmenu` in `detect_loader` (PE section reader), `boot.zfsbootmenu` (images, cmdline, BEs, each BE's `/boot` via a private ro mount), inactive BEs as hinted backup sources at `/run/bko-be/NAME`, `PACKAGES`, `fingerprint.py`, `redact.rs`; a ZBM fake tree (two BEs, with and without encryption) in `scenarios.rs`. Done when `cargo test` passes and the scenario's dump has both BEs, one hinted source, and the loader named | 0.6 d |
| Z3 | bko-holt load (High-effort subagent): user properties through `zfs create -o` (checked), each inactive BE mounted under `TARGET/run/bko-be` and unmounted after, `run/bko-be` removed, the clone note, the `zfsbootmenu` arm (no loader, default BE's initramfs, `efi_entries` with `-u` if optional data), `--key-file` with `file://` keylocations and the key-file equality check, the refusals; `layload.md`'s boot stage. Done when `cargo test` passes and the scenario's plan mounts and restores both BEs, has no `grub-install`, and remakes both NVRAM entries | 0.8 d |
| Z4 | `void-zbm`'s golden plan (`holt/tests/corpus/void-zbm`, `index.tsv` row); Test2 registrations to the batched pass. **Done 2026-11-14** (uncommitted): run `z4`, static bko-holt `8f5e153b…` dumped the live guest on Chopin (qcow2 and `uefi-vars.fd` unchanged); loader `zfsbootmenu`, both BEs, both images, the hinted `/run/bko-be/void-6.18` and both NVRAM entries in the dump. Two fixes: redact.rs's hostid pseudonym (0x00bab10c mapped back upper-case), and `openzfs-2.3-linux` in golden_plans' fake rescue. Two builds same sha256 (src.json `7c1de82a…`); leak scans 0 on Chopin and the Mac, and on Cinqcent with its own strings (116, 0 findings) in Z7's prep, 2026-11-15; lib tests and golden_plans as before. Test2 registrations still to the batched pass | 0.25 d |
| Z5 | `void-zbm-enc`: `--encrypt` in the builder, the passphrase per build, `console-boot.py` at ZBM's prompt (build boot, vm-update), copied to Cinqcent, its golden plan and `index.tsv` row | 0.5 d |
| Z6 | SticTest1 `void-zbm`, both archivers, both hosts, 0 FAIL | 0.3 d |
| Z7 | HoltTest1 1V on Chopin and Cinqcent: ht1.sh's per-BE judge and per-BE boot through `console-keys.py`, the passphrase on the enc guest, RAM peaks in `ram-sizes.tsv`. Done when all four cells PASS with 0 fsmeta and 0 dump differences and every BE booted. **Done 2026-11-15:** all four cells PASS (void-zbm z7a, void-zbm-enc z7b on Chopin and Cinqcent; void-musl z7a on all four hosts), 0 fsmeta and 0 dump differences, both BEs booted on source and copy, checked again by a skeptic pass; bko-holt c6f179fb from d23c9ab via HT_HOLT. z7a's enc FAIL was console-keys.py waiting for the prompt before the countdown (ZBM asks after it). Known weakness (fixed 2026-11-16, z7c): the judge counted only ZBM's "enter passphrase", not the initramfs's own "Encrypted ZFS password for zroot: [1/5]:" line, which the dracut zfs module prints before its `zfs load-key` (answered by the key file, nothing typed) on every enc boot. `judge.py zbm-boot` (judge-test.py `ZfsBootMenuBoot`) now splits each boot's console at ZBM's "Booting .../vmlinuz-KVER for DATASET": ZBM's prompt only before it; after it nothing typed, no prompt console-boot.py would answer, the initramfs's load-key exactly once at [1/5] and no retry ([2/5]: the key file didn't open the pool), none on the plain guest; and ZBM_STATE's `key` line from the booted guest: /'s encryption root with a `file://` keylocation, keystatus available, and the file in lsinitrd's listing of that kernel's initramfs (`key -` on the plain guest). `ram-sizes.tsv`'s two rows are now Z7's peaks | 0.5 d |
| Z8 | Docs: holt/README.md's supported list ("Void Linux, also on ZFS with ZFSBootMenu"), HoltTest1's 1V row and HoltTest1All, SticTest1's README, GuestTest1 grades; memory (holttest1d index, a zfsbootmenu-support note) | 0.15 d |
| | *≈ 4.1 d* | |

Z2 can start beside Z1 from the facts above. Z3 needs Z2. Z4 waits for
Z1's dump and Z2-Z3. Z5 can run beside Z3. Each piece is committed on main
when its tests pass and the user says to commit. Nothing is pushed until
the user says so. Source-code changes go to High-effort subagents
([[effort-max-when-it-pays]]).

**Costs** (est.):
- Z1 and Z5: a 20-30 min build each per host, under `--reserve 8 3`,
  dominated by DKMS for two kernels.
- qcow2: ~2.5 GiB per guest per host, and Cinqcent is at ~69%.
- Inputs: ~0.4 GB per host.
- Z6 and Z7: about 1 h of queue per host.

## Risks

- **ZBM's menu over serial.** ZBM draws its menu with fzf. If its serial
  output or keys don't work under `console-keys.py`, picking the second BE
  needs another route. The fallback is an NVRAM entry per BE with `-u
  "zbm.prefer=zroot/ROOT/void-6.12!"`, a supported 3.1.0 option. But that
  tests ZBM's command line, not its menu.
- **DKMS against a kernel that isn't running**, in a chroot under the
  host's kernel. Void's `zfs` package builds for every installed kernel by
  default (*check*). If it doesn't, the builder runs `dkms install -k`
  itself.
- **A BE that can't be mounted at dump time** (locked key, a damaged
  dataset): it comes back empty, with a warning in the dump and a note in
  the plan. That is today's behaviour, made visible.
- **Restored-not-rebuilt initramfs** in inactive BEs (Decision 6): if a
  future dracut zfs module bakes in the pool GUID (it doesn't today; ZBM
  imports by name, *check*), `zpool reguid` would break them. HoltTest1's
  per-BE boot catches it. The fix is option b, a rebuild in each BE.
- **ZBM's signature.** The release's `sha256.sig` tool and key aren't
  stated on the release page (*check* in Z1: signify or minisign, key in
  the zbm-dev repository). If it can't be checked, the EFI is pinned by
  SHA-256 alone, with the user's OK.
- **Shared BE code with TrueNAS.** Two sessions changing how load treats
  `canmount=noauto` root datasets at the same time. See "Shared lines".

## Out of scope

- ZBM in BIOS mode (syslinux) and through rEFInd (the guide's
  alternative); Secure Boot (the release EFI isn't signed for it).
- Restoring ZFS snapshots (none are, on any guest), and with them ZBM's
  snapshot and rollback menu. The clone's origin snapshot doesn't come
  back (Decision 3).
- ZBM on Arch, Debian, Alpine or Chimera. The bko-holt work is generic
  (loader, BEs, key files). Other distros get it when a guest tests them.
- `generate-zbm` images (option B), beyond naming the `zfsbootmenu`
  package. A package-built image comes back as files like the release one.
- Encryption roots other than the pool's root dataset, and `keyformat=raw`
  or `hex` under ZBM.

## Decisions for the user

1. **How the guests are built:** from the host, as `make-chimera-zfs.sh`
   does: the pool made by the host's OpenZFS with the guide's
   `openzfs-2.3-linux` compatibility, Void's signed ROOTFS tarball, xbps in
   a chroot, and the guide's steps otherwise as written (recommended:
   rebuildable, no live ISO, the hosts can import the pool); or boot hrmpf
   and run the guide inside it, verbatim (+0.4 d; a third-party ISO to pin,
   and hrmpf's ZFS makes the pool).
2. **A native-encryption variant:** yes, **`void-zbm-enc`**, with a random
   passphrase per build in `vms/void-zbm-enc/zfs-passphrase`, typed at
   ZBM's prompt by `console-boot.py`, and given to load by `--key-file`
   (recommended: ZBM's prompt, `keysource` and the key file inside the BE
   are the parts of this path most likely to break, and the passphrase stays
   out of the repo); or no variant (−0.5 d); or the variant with
   `keyformat=hex` and the key baked into a `generate-zbm` image, so no
   typing (unattended, but not the guide's setup).
3. **Inactive BEs:** (a) back up each one as a hinted source at
   `/run/bko-be/NAME` and restore it as a **plain dataset**, its origin
   gone, with a note (recommended: every BE boots, and it reuses ABRoot's
   mechanism); or (b) the same, but recreate the clone. Restore the
   origin's BE first, snapshot it, clone it, and restore the BE over the
   clone with the archiver's `--delete`. This keeps shared blocks and an
   `origin` (+0.5 d; both archivers' `--delete` must behave in a ZFS
   clone). Or (c) OpenIndiana's rule, inactive BEs empty (against this
   task).
4. **libc:** **glibc** (recommended: the guide's default, and no glibc Void
   guest exists; `void-musl` covers musl); or musl; or one of each (+0.5 d).
5. **The ZBM image:** the guide's option A, the **v3.1.0 release EFI**
   pinned by SHA-256 and checked against its `sha256.txt` and `.sig`, with
   `zbm-kcl` adding the serial console (recommended: what most guide
   readers run, and it is the same bytes on every rebuild); or option B,
   `xbps-install zfsbootmenu` and `generate-zbm` (checked by xbps, built
   against Void's own ZFS, but it changes with every rebuild).
6. **Initramfs of inactive BEs on restore:** keep them as restored, and
   rebuild only the default BE's (recommended: as on the source; the
   per-BE boot proves they work); or rebuild each BE's in its own chroot
   (+0.2 d; closer to a fresh install, further from the source).
7. **Fallback loader:** none, as the guide makes none, so HoltTest1 boots
   the copy through the source's NVRAM entries (recommended); or the
   builder also copies ZBM to `EFI/BOOT/BOOTX64.EFI`, so a fresh-NVRAM
   boot works too (not the guide's layout).
8. **SticTest1:** `void-zbm` only (recommended: encryption is invisible to
   the archivers); or both guests (+0.15 d).
9. **HoltTest1's word:** **1V** (recommended: they are Void, and 1V is one
   run today, growing to three); or 1Obscure; or a new word 1ZBM.

Sources read 2026-11-14:
- ZFSBootMenu's "Void Linux (UEFI)" guide (docs.zfsbootmenu.org/en/latest/guides/void-linux/uefi.html);
- `zfsbootmenu(7)` (docs.zfsbootmenu.org/en/latest/man/zfsbootmenu.7.html);
- the v3.1.0 release (github.com/zbm-dev/zfsbootmenu/releases/tag/v3.1.0).
