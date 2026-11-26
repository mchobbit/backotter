# Slackware support

2026-11-14. Asked for by the user: support Slackware 15, the oldest
maintained Linux distribution, as AnduinOS, KDE neon, elementary, PikaOS,
BigLinux and Garuda were added (1edb4b5). **Status: approved 2026-11-14 by
the user with every recommended decision (the last section). 2026-11-18:
S1, S2, S4 and S7 done and the S5/S6 rigs registered; the user's runs of
SticTest1, HoltTest1 1Obscure and vm-update (S3), and S8, are left (the
user runs the tests later). 2026-11-22: Decision 7 changed by the user:
-current is supported too, for the Thinkpad's pinned -current snapshot
("Slackware -current", below). 2026-11-24: Decision 5 changed by the user
to option (d): the source's device names are kept and judged by the name
each target disk has at boot (Decision 5).**

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
| os-release | `NAME=Slackware`, `ID=slackware`, `VERSION_ID="15.0"`, `VERSION="15.0"`, `PRETTY_NAME="Slackware 15.0 x86_64"`, `VERSION_CODENAME=stable`, no ID_LIKE. -current says `VERSION_CODENAME=current`: with `VERSION="15.0+"` and VERSION_ID 15.0 before aaa_base 16.0, `VERSION="16.0"`, `VERSION_ID=16.0` and `PRETTY_NAME="Slackware 15.0 x86_64 (post 15.0 -current)"` since (settled on the Thinkpad, 2026-11-22) | Gentoo (no ID_LIKE) |
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
  against the name its target disk will have when the restored machine
  boots, not its name in the rescue (Decision 5, option (d)): the same
  driver class (vd, sd, nvme, mmcblk; a partition by its disk) and the same
  place among that class's disks, counting only the plan's target disks
  (the rescue's own disk, never a target, is gone by then). mmcblk, xvd
  and hd disks are named by where they are attached (the host
  controller's index, the Xen vdev, the IDE slot), not as found, so they
  keep the name they have in the rescue (2026-11-24 review). vda restored to
  vdb under a rescue on vda boots as vda: a note. A class change (vda to
  sdq, sda to nvme0n1) or an order change (two disks swapped) is a
  problem that says which, and suggests a future opt-in (`--rename-devices`,
  not built). The names are never rewritten.
  A `noauto` fstab line on a device that isn't one of the source's disks
  or partitions (the stock `/dev/fd0 /mnt/floppy`, a CD drive) isn't
  checked: it isn't mounted at boot, the line is kept as it is and the
  mount stage notes it (2026-11-18; a `nofail` line is still checked).
- **mkinitrd:** the image is remade for each kernel that had one, by the
  source's own command (`command_line`, with `-k K` for each kernel), or
  by `mkinitrd -F` when `/etc/mkinitrd.conf` exists. Then, on ELILO, it is
  copied to the ESP as eliloconfig copies it. If the command can't be found,
  the restored `initrd.gz` stays, with a note (Gentoo's "the restored one
  stays"). Since 2026-11-25 the restored image also stays, byte for byte
  with its tree and the ESP's copies, when nothing it was made from
  changes: `command_line`'s `-k` is the kernels that came back with their
  modules (its `-m` is replayed as it is anyway), its `-r` and `-h` name
  the devices they name at boot (Decision 5), the root keeps its type (no
  `--reformat`), and load keeps the LVM, RAID and filesystem UUIDs and
  names (LUKS is refused). Remade, mkinitrd filled its tree's `/dev` from
  the rescue's disks (vdb by the rescue's numbers) and its closing bare
  `ldconfig` rewrote the system's `/etc/ld.so.cache` and
  `/var/cache/ldconfig/aux-cache` (BackShot2 tier 1, slackware15-uefi).
  When it is remade, the replay keeps the source's words in their order
  (only `-k` and `-o` are given; `-f` and `-m` too for a reformatted /,
  below), those two files and their directories'
  times are put back afterwards, and the tree's `/dev` is put back as the
  source's tree had it and the image packed from the tree again, as
  mkinitrd packs it (not with `-P`, a microcode image in front).
- **`--reformat /=TYPE` (2026-11-26):** the initrd's init mounts / only,
  as the type in its `/rootfs` (mkinitrd's `-f`), with the modules of `-m`
  (mkinitrd 1.4.11 adds their dependencies itself, by `modprobe
  --show-depends`: ext4's jbd2 and mbcache; a built-in module adds
  nothing). So a reformat of another mount keeps the image, and one of /
  remakes it by the source's command with two words changed and nothing
  else: `-f` becomes the new type (added at the end if there was none: the
  restored tree's `/rootfs` says the old one), and in `-m` each entry that
  is the old type's module becomes the new type's, in its place; the new
  one is added at the end when the old wasn't listed (`-m NEW` when there
  was no `-m`), which on 15.0's generic kernel (ext4, xfs and btrfs are
  modules) is needed and on the huge one (all built in) changes nothing.
  The old type's other modules the source listed (jbd2, mbcache) stay. So
  slackware15-uefi's `-f xfs ... -m virtio_...:xfs` is made as
  `-f ext4 ... -m virtio_...:ext4`. The huge kernel with no initrd
  (slackware15) needs nothing. Refused at plan time (reformat.rs, reason
  `other`), because load can't make the initrd right for the new type: a
  `mkinitrd -F` command, or no `command_line` (mkinitrd would read
  `/etc/mkinitrd.conf`'s ROOTFS and MODULE_LIST, which aren't rewritten);
  no kernel with modules; geninitrd with GENERATOR=mkinitrd or an override
  script (its command generator is fine: it probes the new filesystem);
  and a LILO or ELILO `append=`/`addappend=` with `rootfstype=`, `rootfs=`
  or `rootflags=` (lilo.conf and elilo.conf aren't rewritten). fstab is
  rewritten as on every reformat (rewrite.rs); `root=` by name stays.
  `bko-holt rewrite --check` reads each image back (`/boot/initrd.gz`,
  the ESP's `EFI/*/initrd.gz`, geninitrd's `initrd-VERSION.img`) with the
  restored system's gzip and cpio: its `/rootfs` must say the new type and
  it must hold the new type's module, or its kernel have it built in.
- **LILO (BIOS), a new arm:** `chroot /mnt/bko-target lilo -v`, after the
  initrd step. It needs `/dev`, `/proc` and `/sys` in the chroot (the boot
  stage binds them already) and `boot=` naming the target disk. `lilo -t`
  (test only) runs first, so a bad `lilo.conf` stops the stage before the
  MBR is written. LILO writes the MBR itself, so the partition stage's
  zeroed first 4 MiB doesn't matter. A `lilo.conf` with `boot=` a
  partition (LILO in a partition's boot sector with another MBR) is
  refused by name in this release. lilo opens `boot=`, `disk=`, `root=`
  (every one, global or an image's), `table=`, `raid-extra-boot=`'s disks
  and each `other=` section's device when it runs, in the rescue, where
  the source's names may be other disks (`/dev/vda` is the rescue's own
  in HoltTest2's rig): the copy of lilo.conf it runs on
  (`/etc/lilo.conf.bko-holt`, "What S1 found") names them as the rescue
  names the target's disks whenever they differ, while the kernel's
  `root=` by name in `append=` stays the source's. An image with
  `literal=` and no `root=` in it (its kernel takes the number lilo
  writes) is refused when its `root=` would be renamed. `other=` and
  `table=` are judged by their names at boot like `boot=`.
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
  right for that kernel.  Since 2026-11-24 (Decision 5, option (d))
  load's copy also names `boot=`, `disk=` and that image's `root=` as the
  rescue names the target's disks when they differ (`/dev/sdq` for the
  source's `/dev/vda`), since lilo opens them in the rescue; the
  `root=/dev/vda2` in append= stays the source's.  Since 2026-11-25 the
  copy's `root=` is the source's device number instead, as lilo.conf takes
  one (`root="0xfe02"`: bsect.c's `dev_number` can't stat it and reads it
  with `strtol(..., 0)`), so the map passes the kernel the source's
  `root=fe02`, not the rescue's `root=fe12` for `/dev/vdb2`.  It is read
  from the source's command line for the booted image's device, where LILO
  put it (the installer ran lilo under the Arch live kernel, so it isn't
  the huge kernel's 253), else the dump's devno.  lilo runs with
  `-S /dev/null`: it keeps no `/boot/boot.NNNN` backup of the boot sector,
  which it would name after boot='s number in the rescue (`boot.FE10`), a
  file the source doesn't have (`force-backup=` is taken before `backup=`,
  and `make_backup` writes nothing to a 1:3 device).
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
- **15.0's CA bundle is too old** (2026-11-25). ca-certificates 20211216
  lacks SSL.com TLS RSA Root CA 2022, where slackware.osuosl.org's chain
  now ends, so `slackpkg update` ("Error downloading") and vm-update's wget
  of GPG-KEY failed. make-bootstrap.sh installs `SLACKWARE_PATCHED`'s
  packages (ca-certificates only) from `patches/packages/`, the version
  CHECKSUMS.md5 lists, checked like the others, and the installer fetches
  the mirror's GPG-KEY with the new system's wget. The mirror keeps only
  a package's newest patch: a tree whose CHECKSUMS.md5 is older needs the
  mirror's (15.0's series lines in it are the same).
- **mkinitrd copies the live system's /dev nodes** (2026-11-25).
  slackware15-uefi's `/boot/initrd-tree/dev` block nodes had Arch's group
  numbers, unknown to Slackware. The installer gives them the groups
  Slackware's eudev rules give (cdrom for sr*, disk for the rest), then
  runs `mkinitrd` with no arguments to pack initrd.gz again.
- The stock fstab's noauto `/dev/fd0` resolves to a real floppy on the
  i440fx guest; load no longer checks a noauto line off the source's disks.
- vm-mount maps fstab's `/dev/vdXN` to partition N of the image's disk X
  (it skipped them, and slackware15-uefi's ESP with them).

## Slackware -current

2026-11-22. The Thinkpad runs Slackware64-current, pinned at the snapshot
`current-20261121` (testvm/realworld/slackware-current.pin; the tree on
mybig512 at `slackware-cache/current-20261121`, slackpkg's only mirror), and
the user asked for it to be supported for dump and load as 15.0 is. That
changes Decision 7 for -current only: 14.2, a 16.0 release and the
derivatives are still refused.

**What -current is, as found on the Thinkpad (read-only):**

- os-release: `ID=slackware`, `VERSION="16.0"`, `VERSION_ID=16.0`,
  `VERSION_CODENAME=current`, `PRETTY_NAME="Slackware 15.0 x86_64 (post
  15.0 -current)"`; `/etc/slackware-version` says `Slackware 15.0+`.
  aaa_base 16.0-3, pkgtools 16.0.
- No kernel-huge and no kernel-modules: `kernel-generic-7.3.5-x86_64-2`
  (modules bundled) as `/boot/vmlinuz-7.3.5`, with `vmlinuz` and
  `vmlinuz-generic` links to it, `System.map-7.3.5`, `config-7.3.5.x64`.
- The initrd is **geninitrd**'s (mkinitrd 1.4.11-81): `/usr/sbin/geninitrd`
  runs `/var/lib/pkgtools/setup/setup.01.mkinitrd`, which reads
  `/etc/default/geninitrd` (`KERNEL=/boot/vmlinuz-generic`,
  `GENERATOR=mkinitrd`, the two symlink switches, `AUTO_REMOVE_INITRD_TREE`,
  `AUTO_UPDATE_GRUB`, which the installer set to false) and, with
  `GENERATOR=mkinitrd` and no `/etc/mkinitrd.conf`, pipes
  `mkinitrd_command_generator.sh -k V -a "-L -R -o /boot/initrd-V.img"` into
  bash: the command it prints for the running system's root (fstab's) and
  storage drivers (from /sys). Then it makes `/boot/initrd-generic.img` and
  `/boot/initrd.gz` links to `/boot/initrd-7.3.5.img`, removes orphaned
  initrds and `/boot/initrd-tree` (so there is no `command_line` to replay),
  and runs the executable scripts in `/etc/geninitrd.d/pre-install` (DKMS)
  and `post-install` (`lilo-reinstall.sh`).
- dracut 112 is installed (series a) but unused (`GENERATOR=mkinitrd`).
- ELILO 3.16-19 as on 15.0: `EFI/Slackware/{elilo.efi,elilo.conf,vmlinuz,initrd.gz}`
  and the same in `EFI/BOOT` (`BOOTX64.EFI`), the ESP's kernel and initrd
  byte copies of `/boot/vmlinuz-7.3.5` and `/boot/initrd-7.3.5.img`;
  `append="root=/dev/nvme0n1p2 vga=normal ro"`; the NVRAM entry
  `Slackware` for `\EFI\Slackware\elilo.efi` is BootCurrent.
- fstab by device name (`/dev/nvme0n1p2 /`, `/dev/nvme0n1p1 /boot/efi`),
  plain ext4 root, vfat ESP, no swap.

**bko-holt dump:**

- `osrel::check_supported` takes `ID=slackware` with
  `VERSION_CODENAME=current` at VERSION_ID 15.x or 16.x (what -current's
  os-release has carried: `SLACKWARE_CURRENT_MAJORS`), and load's layout
  check takes the same dump by `os.version_codename`. A VERSION ending in
  `+` under any other codename, -current at another VERSION_ID, and a 16.0
  release (`VERSION_CODENAME=stable`) are refused. -current is taken on
  x86_64 only (`check_slackware_current_machine` in dump, `os.machine` in
  load): Slackware AArch64 is -current only and may say so the same way,
  but its boot was never looked at.
- `boot.slackware.kernels` reads `vmlinuz-VERSION` too, its flavor the
  `vmlinuz-FLAVOR` link to it (`generic`), else empty.
- `boot.initramfs.generator` is `geninitrd` when `/usr/sbin/geninitrd` is
  there and `/boot` has an image it made (a regular file
  `initrd-VERSION.img`), with `boot.initramfs.geninitrd`: its settings as
  setup.01.mkinitrd reads them, `runs` (`mkinitrd_command_generator.sh`,
  `mkinitrd` with a mkinitrd.conf, `dracut`, or `override` for an
  executable `GENINITRD_OVERRIDE_SCRIPT`), `auto_remove_initrd_tree`
  (`AUTO_REMOVE_INITRD_TREE`, default true) and `initrd_tree` (whether
  `/boot/initrd-tree` is there), mkinitrd.conf's LUKSDEV, ROOTDEV and
  RESUMEDEV when it is read, each image (file, version, kernel, whether
  `/lib/modules/VERSION` is there) and the initrd links. Without such an
  image the 15.0 arms (`none`, `mkinitrd`) read it as before, with an
  evidence line.
- `boot.elilo`'s `initrd_same_as` matches `/boot/initrd*.img` as well as
  `initrd*.gz`, so the ESP's copy is known to be geninitrd's image.

**bko-holt load** (the boot stage), as for 15.0's mkinitrd:

- For each geninitrd image whose kernel and modules came back, the command
  setup.01.mkinitrd would run for it, in the chroot: `chroot T /bin/bash -c
  'set -o pipefail; /usr/share/mkinitrd/mkinitrd_command_generator.sh -k V
  -a "-L -R -o /boot/initrd-V.img" | bash'`, or `chroot T /sbin/mkinitrd -F
  /etc/mkinitrd.conf -k V -o /boot/initrd-V.img` with a mkinitrd.conf. On
  the restore the generator sees the target's own root and drivers, as
  geninitrd would after a kernel upgrade. It reads / from fstab (the
  source's name) and probes it in the rescue (`blkid` for `-f`,
  `lvdisplay` and `cryptsetup` for `-L` and `-C`), so when the rescue names
  the target's root otherwise (option (d), 2026-11-24) it is given that
  name (`--rootdev`), and its command's `-r` is given back as fstab's
  before it runs; a command with no such `-r` fails the step. geninitrd itself isn't run: its
  DKMS and lilo scripts, orphan removal and update-grub aren't the
  restore's. The links came back with /boot and still name the image.
  After each, `rm -rf T/boot/initrd-tree`, as setup.01.mkinitrd removes
  the tree mkinitrd built, when `AUTO_REMOVE_INITRD_TREE` is true and the
  source had no tree (the Thinkpad's /boot has none).
- `GENERATOR=dracut`, an override script, or a kernel whose modules are
  gone: the restored image stays, with a note (15.0's "the restored
  initrd.gz stays" when mkinitrd's command can't be found). LUKS is
  refused, as on 15.0: mkinitrd.conf's LUKSDEV, or a dm-crypt device
  anywhere in the stack under fstab's `/` (through LVM, md and dm layers:
  README_CRYPT's `/dev/cryptvg/root`, LVM on LUKS, which the command
  generator would find and give `-C` for), or, when nothing resolves `/`
  to a device, `/` on /dev/mapper. The stack check holds on 15.0's
  mkinitrd arm too.
- ELILO as on 15.0: the remade image is copied over each ESP `initrd.gz`
  that was its copy (EFI/Slackware and EFI/BOOT), nothing is installed, and
  the NVRAM entries are made again (the source's `Slackware` among them).
- Device names: as Decision 5 (the problems when the target's disk will
  have another name at boot), with mkinitrd.conf's ROOTDEV and RESUMEDEV among them when
  geninitrd runs `mkinitrd -F`. The kernel has an initrd on -current, but
  mkinitrd's command takes fstab's `/dev/nvme0n1p2` as its root, so the
  names still have to hold; with an initrd (15.0's mkinitrd or geninitrd)
  the problem says the initrd mounts `/` by that name (mkinitrd's `-r`, or
  the kernel's root=), without one that the kernel finds it by root=.

**Tests:** `osrel`'s `slackware` test (16.0 and the older 15.0+ -current
accepted, a 16.0 release, -current at 17.0, `+` without the codename and a
derivative refused, -current on aarch64 refused); `boot`'s
`slackware_current_kernels`; the
`slackware_current_thinkpad` scenario (the Thinkpad in small: the dump's
fields, the plan's boot stage with the initrd-tree removal, and the
mkinitrd.conf, mkinitrd.conf ROOTDEV/RESUMEDEV on another disk, LUKSDEV,
LVM on LUKS (on both arms; plain LVM not refused), `/` on /dev/mapper,
AUTO_REMOVE_INITRD_TREE=false, a kept tree, dracut, override,
missing-modules, 15.0-style and other-disk variants); and the
corpus dump `bs1-slackware-current` (the Thinkpad's own `bko-holt dump
--only-disk nvme0n1`, 2026-11-22) with its golden plan. Building it found a
redaction gap, fixed in redact.rs first: the A100's PXE entries name its
NIC's MAC only in their device path's `MAC()` node, not in the description,
so it was never learned (bs1-ubuntu2610-zfsenc, made from the same machine
on 2026-11-18, had it in its `load_option_hex`; replaced there by hand with
the placeholder `bc0000000003` on 2026-11-22). The corpus dump predates
the `auto_remove_initrd_tree`, `initrd_tree` and `resumedev` fields; load
reads their absence as setup.01.mkinitrd's default and no tree, which is
the Thinkpad's. BackShot1 preflight on the
Thinkpad passes with this bko-holt (`UNLOCK=none`). Not yet run: a restore
(BackShot1 phase 2), so the generator's command in the restore's chroot is
untested on hardware.

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
  and `root=`, and the no-initrd kernel can't use UUIDs (mkinitrd 1.4.11's
  init ignores `root=PARTUUID=`). The names are restored as they are and
  judged by the name each disk has at boot (Decision 5, option (d)), so a
  restore to another kind of disk (SATA to NVMe) or in another order is
  refused, not repaired. The rule assumes the restored machine boots with
  only the target disks of that class, in the kernel's order: a disk of the
  same class that load doesn't know of (one left in the machine, not a
  target) would shift the names. Scenario tests cover the match, a class
  change and swapped disks.
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

- Slackware 14.2 and a 16.0 release (refused by version), -current at a
  VERSION_ID it hasn't carried, 32-bit `slackware`, ARM (`slackwarearm`).
  -current itself is supported since 2026-11-22 ("Slackware -current").
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
   **Changed 2026-11-24 by the user: option (d).** The source's names stay
   in the restored fstab, lilo.conf and elilo.conf, byte for byte, and are
   judged by the name each disk will have at boot, not in the rescue: a
   source disk and its target match when they are of the same driver class
   (vd, sd, nvme, mmcblk; partitions follow their disk) and in the same
   place among that class's disks, counting only the target machine's
   disks (the plan's targets: the rescue's own disk and any other disk that
   isn't a target are left out). A match is a note; a class or order change
   is a problem that says why and suggests a future opt-in
   (`--rename-devices`, not built). lilo's copy names `boot=`, `disk=` and
   the numbered `root=` as the rescue names the target's disks
   (`/etc/lilo.conf.bko-holt`); the user's lilo.conf stays as restored.
   `PARTUUID=` was ruled out: mkinitrd 1.4.11's init ignores
   `root=PARTUUID=`, and its busybox findfs lacks PARTUUID.
6. **HoltTest1's group:** **1Obscure** (recommended: Slackware shares a
   boot stack with nothing else, and 1Obscure is the long tail). Or a new
   word **1S** for Slackware alone.
7. **Versions:** 15.0 only, with -current (`VERSION="15.0+"` or
   `VERSION_CODENAME=current`, *check*) and 14.2 refused (recommended:
   15.0 is the only release). Or -current too (a rolling tree whose loader
   and initrd tools are changing: a guest of its own, est. +0.75 d).
   **Changed 2026-11-22 by the user:** -current is supported as well, by
   `VERSION_CODENAME=current`, for the Thinkpad's pinned snapshot, with no
   guest of its own ("Slackware -current").
8. **BackShot1 on the Thinkpad (S8):** later, as its own step after S1-S7
   pass (recommended: it needs the Thinkpad reinstalled by hand, about an
   hour of the user's time, and the Thinkpad is in use for BackShot1All).
   Or now, or not at all.
