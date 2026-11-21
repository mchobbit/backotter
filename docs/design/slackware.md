# Slackware support

2026-11-14. Asked for by the user: support Slackware 15, the oldest
maintained Linux distribution, as AnduinOS, KDE neon, elementary, PikaOS,
BigLinux and Garuda were added (1edb4b5). **Status: approved 2026-11-14 by
the user with every recommended decision (the last section). 2026-11-18:
S1, S2, S4 and S7 done and the S5/S6 rigs registered; the user's runs of
SticTest1, HoltTest1 1Obscure and vm-update (S3), and S8, are left (the
user runs the tests later).**

**Conventions.** est. marks an estimate. *Check* marks a fact the sources
don't settle. It is settled on the first guest (S1), before the code that
depends on it. Slackware's facts come from what is known of 15.0 and its
`patches/` tree, the official UPGRADE notes and CHANGES_AND_HINTS, and a web
search on 2026-11-14 for a 15.1 release (none found). On 2026-11-14 15.0,
released 2022-02-02, is still the newest stable release; -current names its
packages 15.1 but has no release. Nothing has been downloaded or installed
yet. Code references are to local `main` at 1edb4b5.

## Summary

- Slackware 15.0 becomes a supported system for bko-holt and both
  archivers. It gets two QEMU guests (`slackware15` and
  `slackware15-uefi`), a SticTest1 entry each, a place in HoltTest1
  **1Obscure**, golden plans and the Test2 registrations. BackShot1 on the
  Thinkpad is an optional last step.
- It is unlike every supported Linux in four ways at once:
  - **no package database bko-holt reads**: pkgtools keeps one text file
    per package in `/var/lib/pkgtools/packages`;
  - **LILO and ELILO**, Slackware's own boot loaders, which bko-holt has no
    arm for;
  - **no initrd by default**: the `huge` kernel has its drivers built in,
    and boots from `root=/dev/X` with no initramfs at all;
  - **BSD-style rc init** (sysvinit plus `/etc/rc.d/rc.*`): a service is on
    when its script is executable.
- Slackware's `setup` has no answer file. The guests are built the way
  `setup` builds a system: `installpkg --root` of the official package
  series into the new disk, then the same config files `setup`'s scripts
  write. This runs from make-bootstrap.sh's Arch live system, as Gentoo,
  Void and Chimera do.
- Most of the rest exists already: ext4 and XFS, MBR and GPT, an ESP at
  `/boot/efi`, efibootmgr entries, ESP-files-only loaders (Limine and
  rEFInd's arms), and "no initramfs generator" (Gentoo's `none`).
- ~4.25 d (est.), plus an optional BackShot1 (~0.5 d and about an hour of
  the user's time).

## Why

- **A boot stack bko can't infer from anything it knows.** LILO writes the
  kernel's block list into the boot sector, so after a restore moves the
  kernel's blocks, LILO **must** be run again or the machine doesn't boot.
  This is the first BIOS loader where "the files came back" isn't enough,
  and the first that isn't GRUB.
- **A kernel with no initramfs, on a mainstream path.** Gentoo's `none` is
  the owner's choice. On Slackware it is the default. The kernel can't
  resolve `root=UUID=` without an initramfs (`PARTUUID=` works), so what
  `setup` writes is a device name. The restore has to keep device names
  valid, or say that they aren't.
- **Services kept by mode bits alone.** `chmod -x /etc/rc.d/rc.sshd` is
  how a service is turned off. The archivers already keep modes. The
  HoltTest1 boot check has to read a service list that isn't systemd's,
  OpenRC's or dinit's.

## What Slackware is (as far as bko cares)

| | Slackware 15.0 (x86_64) | Nearest supported system |
|---|---|---|
| Release | 15.0, 2022-02-02. Security and bug fixes since then are in `patches/packages/` (including kernel updates: 15.0 shipped 5.15.19, and patches carry newer 5.15.y). -current is the development tree, with package names at 15.1 but no release | Debian 12 (an old stable) |
| os-release | `NAME=Slackware`, `ID=slackware`, `VERSION_ID="15.0"`, `VERSION="15.0"`, `PRETTY_NAME="Slackware 15.0 x86_64"`, `VERSION_CODENAME=stable`, no ID_LIKE. -current says `VERSION="15.0+"` and `VERSION_CODENAME=current` (*check* both, and -current's VERSION_ID) | Gentoo (no ID_LIKE) |
| Packages | pkgtools: `installpkg`, `upgradepkg`, `removepkg`, with **no dependency resolution**: a full install is the supported one. Each installed package is a file `/var/lib/pkgtools/packages/NAME-VERSION-ARCH-BUILD` (`/var/log/packages` links to it), with `PACKAGE NAME:`, sizes, a description and the file list. `slackpkg` downloads and upgrades from one mirror, checking GPG signatures | Portage (one entry per package) |
| Package set | Series `a` (base), `ap`, `d` (development: perl, python3), `e`, `f`, `k` (kernel source), `kde`, `l` (libraries), `n` (network), `t`, `tcl`, `x`, `xap`, `xfce`, `y`. A full install is ~15 GB | none |
| Init | sysvinit with `/etc/inittab` (default runlevel 3) and BSD-style scripts: `rc.S` (single-user setup), `rc.M` (multi-user), `rc.inet1` (network, `rc.inet1.conf`), `rc.local`; `rc.M` starts each `rc.X` that is executable (`rc.sshd`, `rc.syslog`, ...). eudev and elogind, no systemd | Devuan (sysvinit), Alpine (OpenRC) |
| Kernels | `kernel-huge` (drivers built in: boots with no initrd; the default `setup` installs) and `kernel-generic` (modular: needs an initrd), as `/boot/vmlinuz-huge-5.15.x` and `vmlinuz-generic-5.15.x` with links `/boot/vmlinuz`, `vmlinuz-huge` and `vmlinuz-generic`. `kernel-modules` in `/lib/modules/5.15.x` | Gentoo `gentoo-kernel-bin` |
| Initramfs | None by default. For `generic`: `mkinitrd` (Slackware's own, busybox-based) writes `/boot/initrd.gz` from `/boot/initrd-tree/`. Its options come from `/etc/mkinitrd.conf` if the owner wrote one, or from `/usr/share/mkinitrd/mkinitrd_command_generator.sh`. The tree keeps the last command line it was built with (`/boot/initrd-tree/command_line`, *check*). The UPGRADE notes name `geninitrd` (*check* whether 15.0's patched mkinitrd has it, or only -current) | Gentoo (`none`); no supported generator |
| Boot loader, BIOS | **LILO** 24.2. `liloconfig` (simple or expert) writes `/etc/lilo.conf` (`boot = /dev/sda` for the MBR, `image = /boot/vmlinuz`, `root = /dev/sda2`, `read-only`, `append=`, `compact`, `lba32`), then `lilo` writes the boot sector and `/boot/map`. **The map holds the kernel's sector list**: any restore that moves `/boot/vmlinuz*`, or a kernel upgrade, needs `lilo` run again | none (GRUB's i386-pc is an install step too, but reads files) |
| Boot loader, UEFI | **ELILO** 3.16. `eliloconfig` copies `elilo.efi`, the kernel (as `vmlinuz`) and the initrd (as `initrd.gz`, if any) into **`EFI/Slackware/`** on the ESP. It writes `elilo.conf` there (`image=vmlinuz`, `label=vmlinuz`, `read-only`, `append="root=/dev/sda2 vga=normal ro"`) and makes an NVRAM entry `Slackware` with efibootmgr. The kernel ELILO boots is **the ESP's copy, not `/boot`'s**. There is no fallback `EFI/BOOT` copy (*check*). No Secure Boot (no shim) | Limine, rEFInd (files on the ESP) |
| GRUB | grub 2.06 is in series `a`, but `setup` never installs it. Users who want it run `grub-install`/`grub-mkconfig` themselves | Arch's arm |
| Install | `setup` on the install ISO (a busybox ramdisk running dialog scripts): `cfdisk`/`fdisk`, choose swap and partitions (ext4 the default; xfs, btrfs, jfs, f2fs, reiserfs offered), choose the series, then `installpkg` from the media into `/mnt` with tag files. Then `liloconfig` or `eliloconfig`, `netconfig`, `timeconfig`, the services menu and `passwd`. **No answer file.** Tag files (`tagfile` per series: ADD, REC, OPT, SKP) only cover package choice | Gentoo (the owner installs by hand) |
| fstab | **By device name** (`/dev/sda2 / ext4 defaults 1 1`), as `setup` writes it, plus `devpts`, `proc`, `tmpfs` lines. The ESP is `/dev/sda1 /boot/efi vfat defaults 1 0` | none (all others write UUID=, LABEL= or PARTUUID=) |
| Network | `rc.inet1.conf`, `USE_DHCP[0]="yes"` on eth0 with dhcpcd. NetworkManager (series n) is off unless `rc.networkmanager` is executable | Void's dhcpcd |
| sshd | openssh in series n; `rc.sshd` executable by default; root login by key (`PermitRootLogin prohibit-password`, openssh's default) | |
| Image | `slackware64-15.0-install-dvd.iso` (~3.6 GB) with `.asc`, signed by the Slackware Linux Project key (`security@slackware.com`, key ID 40102233). The same tree is on every mirror as `slackware64-15.0/`, with `CHECKSUMS.md5` and its `.asc` and a `.txz.asc` per package | Gentoo (stage3 + gpgv) |

## What already works, and what doesn't

**bko-holt `dump`:**

- `osrel.rs` `SUPPORTED` has no `slackware` row. It needs
  `("slackware", "15", "Slackware 15")` with a unit test from the guest's
  os-release, and a refusal of -current (Decision 7). The row matches on the
  major version, so a 15.1 release would be accepted silently. The row's
  comment says that 15.1 is checked before it's claimed, as PikaOS's does for
  5.
- **Packages:** `installed_packages()` (`boot.rs:186`) has dpkg, pacman,
  rpm, apk, xbps and Portage readers, and nothing for pkgtools. New:
  `pkgtools_packages()`. It lists `/var/lib/pkgtools/packages` (or
  `/var/log/packages` when that is a directory: before 15.0), and splits
  each name at its last three dashes into name, version, arch and build
  (`kernel-huge-5.15.161-x86_64-1` gives name `kernel-huge`, version
  `5.15.161`, arch `x86_64`, build `1`; version strings carry no dash).
  `PACKAGES` gains `lilo`, `elilo`, `mkinitrd`, `kernel-huge`,
  `kernel-generic`, `kernel-modules`, `efibootmgr`, `grub` and `slackpkg`.
- **Initramfs generator:** with none of the known generators installed it
  comes out unknown. New, for `ID=slackware`: `none` when no kernel in /boot
  has an initrd named in the loader's config and `/boot/initrd.gz` is
  missing. `mkinitrd` when there is one, with the evidence (the loader
  config's `initrd=` lines, `/etc/mkinitrd.conf` present or not, and
  `/boot/initrd-tree/command_line`), as Gentoo's `evidence` list is kept.
- **Loader:** `detect_loader` finds GRUB, systemd-boot, rEFInd, Limine and
  syslinux, by BootCurrent's file or by boot code in the MBR. New:
  - `lilo`, from the MBR's boot code (bootcode.rs already names boot code;
    LILO's signature `LILO` at offset 6, *check*) and `/etc/lilo.conf`;
  - `elilo`, from BootCurrent's `\EFI\Slackware\elilo.efi` or the ESP's
    `EFI/*/elilo.efi`.
  - `boot.lilo` records `lilo.conf` parsed (boot=, each image=, root=,
    initrd=, append=, map=) and whether `/boot/map` exists.
  - `boot.elilo` records the ESP directory, `elilo.conf` parsed the same
    way, and for each image= whether it is byte-identical to a `/boot`
    kernel (and which one). That is the kernel the machine really boots.
- **Init:** `init` is read from the files, as for Alpine, Void and Chimera.
  New value `slackware-rc` when `/etc/rc.d/rc.M` and `/etc/inittab` exist
  and `/sbin/init` is sysvinit's. Beside it, `rc_enabled` lists the
  executable `/etc/rc.d/rc.*`, for the plan and HoltTest1.
- **fstab by device name:** the dump has the device name and what it was
  then. *Check* that the mount stage (`commands.rs:2258`) maps
  `/dev/vda2` to the layout's partition, since other systems' fstabs all
  name a UUID, LABEL or PARTUUID. If it doesn't, that is S2's first fix: a
  `/dev/` source resolves by the dump's own record of that device.

**bko-holt `load`:**

- **No initrd (`none`):** nothing is made, as Gentoo's `none` (the note
  wording shared). New: because the kernel finds / by name, the plan
  checks `root=` in `lilo.conf` or `elilo.conf` and each fstab `/dev/` name
  against the target's devices. When the target disk keeps the source's
  name (vda to vda, as in HoltTest1), it is a note. When it doesn't (sda to
  nvme0n1), it is a problem: Decision 5 says whether load rewrites them.
  A `noauto` fstab line on a device that isn't one of the source's disks
  or partitions (the stock `/dev/fd0 /mnt/floppy`, a CD drive) isn't
  checked: it isn't mounted at boot, the line is kept as it is and the
  mount stage notes it (2026-11-18; a `nofail` line is still checked).
- **mkinitrd:** the image is remade for each kernel that had one, by the
  source's own command (`command_line`, with `-k K` for each kernel), or
  by `mkinitrd -F` when `/etc/mkinitrd.conf` exists. Then, on ELILO, it is
  copied to the ESP as eliloconfig copies it. If the command can't be found,
  the restored `initrd.gz` stays, with a note (Gentoo's "the restored one
  stays").
- **LILO (BIOS), a new arm:** `chroot /mnt/bko-target lilo -v`, after the
  initrd step. It needs `/dev`, `/proc` and `/sys` in the chroot (the boot
  stage binds them already) and `boot=` naming the target disk. `lilo -t`
  (test only) runs first, so a bad `lilo.conf` stops the stage before the
  MBR is written. LILO writes the MBR itself, so the partition stage's
  zeroed first 4 MiB doesn't matter. A `lilo.conf` with `boot=` a
  partition (LILO in a partition's boot sector with another MBR) is
  refused by name in this release.
- **ELILO (UEFI), a new arm:** `elilo.efi`, `elilo.conf`, the kernel and
  the initrd come back with the ESP. Nothing is installed. After a mkinitrd
  step the new `initrd.gz` is copied over the ESP's, and the boot entry is
  made (`efi_entries()`, label `Slackware`, loader
  `\EFI\Slackware\elilo.efi`). When the ESP's kernel isn't byte-identical
  to any `/boot` kernel (the source was upgraded and eliloconfig not run),
  it is a note: the restored machine boots the same old kernel the source
  did.
- **GRUB on Slackware:** the generic `grub-mkconfig` arm, with a unit test
  and no guest (Decision 3).
- `--removable` on ELILO: the ESP's `EFI/Slackware/` is also copied to
  `EFI/BOOT/` with `elilo.efi` as `BOOTX64.EFI`, as NixOS's
  `nixos_removable` does. *Check* that ELILO finds `elilo.conf` beside
  itself under that name.

**The rescue image:** no change ([[one-kernel-rescue]]). `lilo`,
`mkinitrd` and `efibootmgr` run from the restored system in the chroot, and
the rescue image already has e2fsprogs, xfsprogs and dosfstools.

## Guests

Two guests (Decision 1), each matching a path `setup` really takes:

| | `slackware15` | `slackware15-uefi` |
|---|---|---|
| Firmware, table | BIOS, MBR | UEFI (no Secure Boot), GPT |
| Partitions | `vda1` swap 2 GiB, `vda2` / ext4 the rest | `vda1` ESP 512 MiB at `/boot/efi`, `vda2` swap 2 GiB, `vda3` / **XFS** the rest |
| Loader | **LILO** in the MBR (`liloconfig`'s simple mode's `lilo.conf`) | **ELILO** in `EFI/Slackware` (eliloconfig's files) |
| Kernel | `huge`, **no initrd** | `generic` with **mkinitrd** (`xfs`, `virtio_blk` from the command generator) |
| fstab, root= | `/dev/vda2`, `/dev/vda1` (device names) | the same, by device name |
| Disk | 20 GiB qcow2 | 20 GiB qcow2 |

The first is a stock Slackware install. The second adds what's new for
UEFI (ELILO, a kernel copied to the ESP, mkinitrd) and XFS, whose module has
to be in the initrd. Both have the same package set, as 2 GiB RAM, a serial
console (`append="console=tty0 console=ttyS0,115200n8"` and an
`s0:12345:respawn:/sbin/agetty ttyS0 115200 vt100` inittab line), DHCP on
eth0 and `rc.sshd` on.

**How they are built** (Decision 2): `make-bootstrap.sh slackware` and
`slackware-uefi` with a new `testvm/bootstrap/slackware.sh`, run from the
Arch live ISO under serial control, as for Gentoo:

1. `bootstrap/common.sh` partitions and makes file systems. New LAYOUTs:
   `mbr-swap-root` and `efi-swap-root`, or the existing ones if they fit
   (*check* `classic` and the UEFI one). Then it mounts `/mnt`.
2. The package tree: `slackware64-15.0/slackware64/{a,ap,d,l,n}` from one
   pinned official mirror (or from the DVD ISO in
   `/bkoscratch/rebuild-inputs/slackware15/`, Decision 4). The host fetches
   it before boot, checks it with `gpgv` against Slackware's key
   (`CHECKSUMS.md5.asc`, then each file's MD5 from it), and hands it to the
   VM on a second disk or by HTTP from 10.0.2.2.
3. Slackware's own pkgtools from `a/pkgtools-*.txz`, unpacked into a temp
   directory. Each package in series order goes in with
   `ROOT=/mnt installpkg --terse`, as `setup` runs it. That includes each
   package's `install/doinst.sh`, run relative to `/mnt`, as `setup`
   does. **Series `a`, `ap`, `d`, `l`, `n` in full** (Decision 2's package
   set), less `kernel-generic` on `slackware15` and `kernel-huge` on
   `-uefi`. That is about 5 GiB installed (est.) and no X.
4. In a chroot: what `setup`'s scripts would write (`/etc/fstab`,
   `rc.inet1.conf`, the hostname, `localtime`, `rc.keymap` left off,
   root's password and `authorized_keys`, the serial getty, `rc.sshd` +x).
   Then `lilo.conf` and `lilo`, or `mkinitrd` from the command generator's
   output, then eliloconfig's copies, `elilo.conf` and efibootmgr
   (`eliloconfig` itself is a dialog script: its file and entry are
   reproduced, and S1 diffs the result against a real `eliloconfig` run in
   the chroot).
5. The first boot: SSH up, `/etc/slackpkg/mirrors` set to the pinned
   mirror's 15.0 tree, `slackpkg update gpg`, then the qcow2 snapshot
   `fresh-install`. The 15.0 patches come in by vm-update afterwards
   ([[images-take-os-updates]]), so the image isn't the bare 2022 release.

Each piece as for the others: `rebuild-one.sh` lines (`slackware15)` and
`slackware15-uefi)`), the inputs pinned in `/bkoscratch/rebuild-inputs/`
on both hosts, `holttest1d/ram-sizes.tsv` (est. D 8, MEM 4 until
measured), `vm-store` publish to both hosts, a GuestTest1 grade. The build
runs in /mnt/bko-ram under `cmp-run.sh --reserve 8 4` (WEAR_TAG
`build:slackware15`). Finished guests move to disk at once, because Chopin's
RAM disk is contended.

**vm-update** (`# t2-section: Linux.slackpkg`, new, before the generic
Linux branches since a full Slackware has rpm as a tool):

- `slackpkg -batch=on -default_answer=y update` (gpg once at build), then
  `upgrade-all`. *Check* the flag syntax against 15.0's slackpkg man page.
- slackpkg's own "run lilo?" prompt is skipped in batch mode. So when a
  kernel package changed, in-guest.sh does what Slackware's UPGRADE notes
  say: on LILO, `lilo`; on ELILO, it remakes the initrd by the recorded
  command (generic), copies the kernel and initrd to `EFI/Slackware/`, and
  updates `elilo.conf`'s image if the name changed. The reboot vm-update
  already does then proves it.
- `upgradepkg` replaces a kernel in place, which removes the running
  kernel's modules. That's harmless here because the guest reboots straight
  after, and it's the reason the kernel step can't be left for later.
- `.new` config files are left as `slackpkg` leaves them in batch mode,
  and listed in the log. `new-config` isn't run.
- `fingerprint.py`'s `LINUX_KINDS` and the `vm-update` header name it.
  Without slackpkg the branch says `no package manager found`, as it does
  today.

## What S1 found

Settled on the guests, 2026-11-14 to 2026-11-18:

- os-release as assumed (`ID=slackware`, `VERSION_ID=15.0`); eth0 by DHCP
  from rc.inet1; runlevel 3; SSH in about 25 s on both guests.
- **LILO's root= is a number.** lilo stores `root = /dev/vda2` as the
  device's number under the kernel lilo runs on (lilo 24.2 bsect.c,
  `dev_number`), and virtio_blk's major is dynamic: 254 under the Arch live
  system and the rescue, 253 under the huge kernel, which then panics on
  `unknown-block(254,2)`.  The installer and bko-holt load both run lilo
  with `-C` on a copy of lilo.conf whose append= ends in `root=/dev/vda2`
  (the kernel takes the last root=, by name); lilo.conf itself is
  liloconfig's.  A user's later `lilo` writes the stock numeric form again,
  right for that kernel.
- **The live system's mkfs is too new.** e2fsprogs 1.47's ext4 has
  orphan_file, which Slackware's e2fsck 1.46.5 refuses at boot; xfsprogs
  6's XFS has nrext64 and exchange, which 5.15 won't mount.  The installer
  makes / with the tree's own mke2fs.conf and xfsprogs' `lts_5.10.conf`
  profile (xfsprogs 5.13's defaults).  bko-holt load recreates the
  source's feature sets (HoltTest1 will show it for these two).
- **installpkg --root doesn't chroot doinst.sh**, so a package's
  `if [ -x /usr/...]` step found the live system's tool or none:
  ca-certificates' bundle was never made (no https, so no slackpkg).  The
  installer runs setup's four non-interactive `var/log/setup` scripts
  (desktop and MIME databases, icon cache, CA certificates) from the new
  root, as SeTconfig does.
- **Slackware's key.** `slackpkg update gpg` hides its failures and pings
  www.slackware.com first (QEMU's user network passes no ICMP, and its
  fallback question reads stdin); the installer imports the key the builder
  checked by fingerprint, and in-guest.sh fetches the mirror's GPG-KEY
  when it is missing, held to EC5649DA401E22ABFA6736EF6A4463C040102233.
  Slackware's `gpg` is GnuPG 1.4 (`gpg2` is 2.2).
- The stock fstab's noauto `/dev/fd0` resolves to a real floppy on the
  i440fx guest; load no longer checks a noauto line off the source's disks.
- vm-mount maps fstab's `/dev/vdXN` to partition N of the image's disk X
  (it skipped them, and slackware15-uefi's ESP with them).

## Tests

**SticTest1** (`testvm/stictest1/slackware15`, `slackware15-uefi`):

- `slackware15` from an ext4 fixture (debian12's) with a scratch disk laid
  out like the guest's (MBR, swap, ext4).
- `slackware15-uefi` from an XFS + ESP fixture (gentoo's XFS and
  chimera-uefi's `fatattr.py`).
- Real examples in both: the `rc.*` scripts with their exec bits (on and
  off: what turns a service on), `lilo.conf` and `/boot/map`, or
  `EFI/Slackware/` and `/boot/initrd-tree`, `/var/lib/pkgtools/packages`,
  set-id binaries, shadow.
- Both archivers. `stic/manifests.json`, `limits.json`, and
  check-manifests' count goes up by two.

**HoltTest1:** both guests join **1Obscure** (Decision 6). That means
`classes.json`'s `H.Obscure`, the README's 1Obscure row and HoltTest1All's
members.

- ht1.sh's boot-state check (`ht1.sh:368`) gets a fourth arm, for
  `/etc/rc.d/rc.M` without systemd, OpenRC or dinit (also in
  `testvm/compare/boot-state.sh`). It records `runlevel` (expects `N 3`),
  the executable `rc.*` list, and that sshd, syslogd and dhcpcd are
  running (`pgrep -x`). All three must match the source's.
- judge.py allows what the boot stage rewrites:
  - `/boot/map` on LILO;
  - `/boot/initrd.gz`, `/boot/initrd-tree/` and the ESP's `initrd.gz` on
    mkinitrd.
- judge.py **requires** the ESP's `elilo.efi`, `elilo.conf` and `vmlinuz`
  byte-identical to the source's.
- A new check on `slackware15`: the restored MBR's boot code is LILO's,
  and the machine boots from it alone. That proves `lilo` ran, since the
  kernel's sectors moved.

**Test2:**

- Golden plans in `holt/tests/corpus/slackware15/` and
  `slackware15-uefi/` (`src.json`, `plan.txt`, `index.tsv`) from live
  dumps.
- `classes.json` (`H.Obscure`; `slackware15-uefi` in `uefi`) and
  `holt/spec.json` (`{"kind": "linux", "firmware": ...}`).
- `guest/spec.json`: pm `slackpkg`, with new rules `S-slackpkg`, `L-lilo`,
  `L-elilo` and `K-slackware`. K-slackware means each kernel in
  `/boot/vmlinuz-*` is the one the loader boots: ELILO's ESP copy
  byte-identical to the newest, or LILO's map newer than the newest kernel.
  The harness is `make-bootstrap.sh`, `bootstrap/**`, in-guest.sh's
  `#Linux.slackpkg`, boot-state.sh and new-guest.sh.
- `known-lines.json#slackpkg`, `estimates.tsv`, `t2lib/plan.py`'s counts
  (+2 sources, +2 guests), `tests/fixtures/p2/world.py` and the re-blessed
  goldens.
- Done when two corpus builds have the same sha256, leak scans are 0 on
  both hosts and the Mac, no other plan changes, and Test2's own tests
  pass. The registrations are batched with the sibling sessions' if they
  are done at the same time (as PikaOS's were).

**BackShot1** (optional, S8): the Thinkpad reinstalled with Slackware 15.0
from the DVD (UEFI, ELILO), then the usual run. bs1-adopt needs a
Slackware arm (slackpkg, `rc.sshd`, no avahi by default: *check* mDNS, or
reach it by address). `bs1-common`'s `package_list` reads pkgtools.

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| S1 | Fetch the 15.0 tree (series a, ap, d, l, n; ~2.5 GB est.) or the DVD ISO into rebuild-inputs on Chopin, with the user's OK (Decision 4), and check it against Slackware's key. Write `bootstrap/slackware.sh` and the two make-bootstrap.sh cases. Build both guests in /mnt/bko-ram, move them to disk, copy to Cinqcent (the same SHA-256), `rebuild-one.sh`. Settle every *check* and write "What S1 found" here. Done when both boot alone on both hosts (LILO from the MBR, ELILO from NVRAM) and SSH answers | 1.25 d |
| S2 | bko-holt (High-effort subagent): `SUPPORTED` and tests (15.0 accepted, -current and 14.2 refused); `pkgtools_packages()` and `PACKAGES`; the `none`/`mkinitrd` generator with evidence; `detect_loader`'s `lilo` and `elilo` with `boot.lilo`/`boot.elilo`; `init` = `slackware-rc` and `rc_enabled`; fstab `/dev/` sources in the mount stage; load's LILO arm (`lilo -t`, `lilo -v`), ELILO arm (keep, copy the initrd, entry, `--removable`), mkinitrd replay, the root=/device-name check; fake trees for both guests in `scenarios.rs`. Done when `cargo test` passes and both scenarios' plans have the right boot stage, with no grub-* | 1.25 d |
| S3 | vm-update: `Linux.slackpkg` in in-guest.sh with the kernel step; `fingerprint.py`; `vm-update --try` on both guests on Chopin, then a real update ([[update-guests-not-copy]]: in a `--keep` overlay, shipping the overlay) | 0.35 d |
| S4 | Golden plans and the Test2 registrations (as AnduinOS's A3). **Registrations done 2026-11-18** (HoltTest2 sources in 75bd471; GuestTest2 entries, limits.json for the stock fstab's /dev/fd0 line and the second devpts, estimates as est., fixtures from the before hook on Chopin). **Golden plans done 2026-11-18**: live dumps (run `s4`, Chopin, of the 2026-11-15 images, since rebuilt with only the CA-bundle and GPG-key fixes), two identical corpus builds, scans 0 on chopin, cinqcent and the Mac, no other plan changed; each plan lists the device-name Problems of Decision 5 against the corpus's sdq, and slackware15's also the stock `/dev/fd0` line (gone since 2026-11-18: a noauto line off the source's disks isn't checked) | 0.4 d |
| S5 | SticTest1 entries and runs, both archivers, both hosts, 0 FAIL | 0.4 d |
| S6 | HoltTest1 1Obscure runs on both hosts: the boot-state arm, the judge rules, the LILO and ELILO checks; RAM peaks in `ram-sizes.tsv`; GuestTest1 grades | 0.5 d |
| S7 | Docs: holt/README.md's supported list, `osrel.rs`'s comment, the 1Obscure row and HoltTest1All, the SticTest1 and Test2 READMEs, `layload.md`'s boot stage (LILO and ELILO arms), memory (holttest1d index, a slackware-support note) | 0.1 d |
| S8 | *Optional, later:* BackShot1 on the Thinkpad | 0.5 d + ~1 h of the user's time |
| | *≈ 4.25 d without S8* | |

Each piece is committed on main when its tests pass and the user says to
commit. Nothing is pushed until the user says so. S2 touches `boot.rs`,
`commands.rs` and `scenarios.rs`. Seven sibling sessions are adding
distributions to the same files today, so S2 adds its own arms and tests and
keeps away from shared lines where it can. S4 touches `classes.json` and the
p2 goldens, which every sibling also touches: it waits for, or joins, their
batched pass.

**Costs** (est.): S1 two ~30 min builds, with the tree ~2.5 GB and two
qcow2s of ~6 GiB a host. S5 and S6 about 1.5 h of queue a host. None of the
user's time but the OKs, until S8.

## Risks

- **Device names everywhere.** `setup` writes `/dev/sdX` names in fstab
  and `root=`, and the no-initrd kernel can't use UUIDs. A restore to a
  machine whose disk has another name (SATA to NVMe) doesn't boot unless
  they are rewritten (Decision 5). HoltTest1 restores vda to vda, so it
  can't catch this. A scenario test covers it.
- **The bootstrap isn't `setup`.** The package installs are `setup`'s own
  (`installpkg`, the same tag order), but the config files are written by
  bko's script. S1 compares them with a real `liloconfig` and
  `eliloconfig` run and records the differences.
- **ELILO is unmaintained upstream**, and -current may move UEFI to GRUB.
  That is fine for 15.0. A 15.1 is a new spec check, not a silent
  acceptance.
- **LILO is fragile by design.** If `lilo` fails in the chroot (a missing
  `/dev` node, an odd `boot=`), the restored disk doesn't boot. `lilo -t`
  first, a refusal for partition boot sectors, and HoltTest1's boot-alone
  check are the guard.
- **slackpkg batch mode** skips its own kernel prompts. If in-guest.sh's
  kernel step is wrong, the guest boots the old kernel (ELILO) or nothing
  (LILO), and vm-update's reboot catches it.

## Out of scope

- Slackware -current and 14.2 (refused by version), 32-bit `slackware`,
  ARM (`slackwarearm`).
- Derivatives (Salix, Slackel, Porteus, Slax): other IDs, refused.
- Third-party package tools (slackpkg+, sbopkg, slpkg, SlackBuilds) beyond
  what pkgtools records.
- LILO in a partition boot sector, LILO on UEFI, LUKS (`mkinitrd -C`) and
  LVM roots: a later spec if a user needs them.
- A full install (X, KDE, Xfce): series x, kde, xfce, xap, e, f, t, tcl, y
  and k aren't in the guests.
- Changes to the rescue image.

## Decisions for the user

1. **Guests:** two, `slackware15` (BIOS, LILO, huge kernel, no initrd,
   ext4) and `slackware15-uefi` (UEFI, ELILO, generic kernel with mkinitrd,
   XFS) (recommended: those are `setup`'s two paths, and only the second
   tests mkinitrd and the kernel copied to the ESP). Or one, `slackware15`
   only (BIOS, no initrd: about 1 d less, and UEFI isn't covered). Or one
   UEFI guest with the huge kernel (ELILO, no initrd: no LILO, no mkinitrd).
2. **How they're built, and with what:** `make-bootstrap.sh` from the Arch
   live ISO with Slackware's own `installpkg --root` of series a, ap, d, l
   and n, and the config files `setup` writes (recommended: rebuildable,
   unattended, uses the existing plumbing, and `installpkg` is what `setup`
   runs). Or boot the Slackware DVD's own installer ramdisk and drive a
   script on its serial console (closer to `setup`, but its ramdisk has no
   SSH and fewer tools, est. +0.5 d). Or a full install of every series
   (~15 GB: bigger images and slower HoltTest1 runs).
3. **Boot loader:** **LILO on BIOS and ELILO on UEFI**, the loaders
   `setup` installs (recommended: that is what Slackware machines boot
   with, and GRUB is in series `a` only as an option the owner sets up by
   hand). GRUB on Slackware takes the generic `grub-mkconfig` arm with a
   unit test and no guest. Or GRUB only, so bko-holt needs no new loader
   arms (cheaper, about 0.5 d less, but it tests a setup almost no
   Slackware user has and leaves LILO and ELILO machines unsupported).
4. **The packages:** download the 15.0 tree's five series (~2.5 GB est.)
   from one official mirror into Chopin's rebuild-inputs, check them with
   `gpgv` against Slackware's key, and copy them to Cinqcent (recommended:
   the tree is signed, frozen since 2022, and only what's needed). Or
   the 3.6 GB DVD ISO with its `.asc`. Or the user downloads and checks it.
5. **Device names on other hardware:** a plan problem when the target's
   disk names differ from those in `root=` and fstab, so load stops before
   writing anything (recommended for now: honest, and HoltTest1 never
   meets it). Or load rewrites `root=` in `lilo.conf`/`elilo.conf` and the
   fstab `/dev/` names to the target's (est. +0.3 d; a restored system's
   config files then differ from the backup's). Or rewrite them to
   `PARTUUID=`, which the huge kernel can resolve and bko-holt keeps (est.
   +0.3 d; that changes the system more than the user did).
6. **HoltTest1's group:** **1Obscure** (recommended: Slackware shares a
   boot stack with nothing else, and 1Obscure is the long tail). Or a new
   word **1S** for Slackware alone.
7. **Versions:** 15.0 only, with -current (`VERSION="15.0+"` or
   `VERSION_CODENAME=current`, *check*) and 14.2 refused (recommended:
   15.0 is the only release). Or -current too (a rolling tree whose loader
   and initrd tools are changing: a guest of its own, est. +0.75 d).
8. **BackShot1 on the Thinkpad (S8):** later, as its own step after S1-S7
   pass (recommended: it needs the Thinkpad reinstalled by hand, about an
   hour of the user's time, and the Thinkpad is in use for BackShot1All).
   Or now, or not at all.
