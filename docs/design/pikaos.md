# PikaOS support

2026-11-14. Asked for by the user: support PikaOS, the gaming-focused
distribution, the way EndeavourOS/Manjaro (6715f43), Chimera, Gentoo and
Bazzite were added. The user approved this spec on 2026-11-14 with every
recommended decision (the last section).

**Conventions.** est. marks an estimate. *Check* marks a fact the sources
don't settle, to be settled on the first guest (P1) before the code that
depends on it. PikaOS's facts come from its own pages and source, read
2026-11-14: pika-os.com (the editions and ISO links), wiki.pika-os.com (home,
Installation Basics), and its Gitea at git.pika-os.com:
`general-packages/base-files` (`etc/os-release`), `general-packages/pikainstall`
(README, `docs/compat-matrix.md`, `src/install_boot.zig`,
`pika-install-chroot.sh`; last change 2026-11-12),
`custom-gui-packages/pika-installer-gtk4` (`script_gen/auto_*.rs`, 2026-10-31),
`boot-packages/booster` (its `update-initramfs`, `booster-reassert`,
`postinst`, Makefile; 2026-10-30), `boot-packages/refind` and
`kernel-packages/kernel-pika` (7.3.3, 2026-11-08). Nothing has been
downloaded or installed yet. Code references are to local `main` at 1b980cd.

## Summary

- PikaOS 4 ("Nest") becomes a supported system for bko-holt and both
  archivers, with two QEMU guests, a SticTest1 entry each, a place in
  HoltTest1 and the Test2 registrations; BackShot1 on the Thinkpad is an
  optional later step. **P1-P7 done 2026-11-14** (uncommitted); the Test2
  registrations done 2026-11-14 (both guests alone, as vanilla3's). **P8
  (BackShot1) done 2026-11-14:** PASS on the Thinkpad ("What P8 found").
- It is **not** Debian's boot stack, and not Ubuntu's: it is Debian sid
  with its own kernel (`kernel-pika`, 7.3), **rEFInd** as the only boot
  loader, and **booster** as the initramfs generator. bko-holt has neither:
  `dump` would find no generator, and `load` refuses rEFInd outright
  ("boot loader refind isn't one bko-holt load can install").
- It is not a bcachefs system: the installer offers XFS (the default since
  April 2026), ext4 and Btrfs, never bcachefs or ZFS, so the nixos2605
  on-disk-version problem doesn't arise.
- Its ISOs are built for **x86-64-v3** (AVX2). All four hosts have it and
  run guests with `-cpu host`; the Thinkpad's CPU is a *check* for BackShot1.
- Most of what it needs exists: dpkg and apt, Btrfs subvolumes, LUKS2 by
  UUID, ESP at /boot/efi, efibootmgr entries, a loader whose files come back
  with the ESP (Limine's arm).
- ~3.75 d (est.), plus an optional BackShot1 (~0.5 d and about an hour of
  the user's time).

## Why

- **A second boot loader that is only files.** rEFInd reads the kernel
  options from `/boot/refind_linux.conf` and finds the kernels itself. Like
  Limine, nothing needs reinstalling, but bko-holt must say so, and keep the
  options valid (`root=UUID=`, `rd.luks.name=UUID=`).
- **A new initramfs generator.** booster (Go, host-only by default) is
  neither dracut nor initramfs-tools, though it installs its own
  `update-initramfs` over Debian's.
- **A /boot that is an XBOOTLDR partition** (GPT name `linux-boot`, the
  `bls_boot` flag) with no Boot Loader Specification entries. Detection
  mustn't take it for systemd-boot's.

## What PikaOS is (as far as bko cares)

| | PikaOS 4 | Nearest supported system |
|---|---|---|
| Release | Rolling on Debian sid ("Fast, up to date and rolling"); ISOs are dated snapshots of 4.0 "Nest", the newest **26.08.20-4** (2026-08-20). Editions GNOME, KDE, Hyprland, Niri, COSMIC, each standard or NVIDIA | Debian 13, Arch (rolling) |
| os-release | `ID=pika`, `ID_LIKE=debian`, `VERSION_ID="4"`, `VERSION="4"`, `VERSION_CODENAME=nest`, `DEBIAN_CODENAME=sid`, `NAME="PikaOS"`, `PRETTY_NAME="PikaOS 4"` (base-files' source; *check* the installed one) | Devuan (`ID_LIKE=debian`) |
| CPU | Packages built `-O3`, LTO, **x86-64-v3** (`amd64-v3` in every ISO name); i386 builds exist for multiarch only | none |
| Packages | apt and dpkg; Pika's own archive over sid; `pikman` is archived; Flatpak for apps | Debian |
| Init | systemd (`/etc/default/keyboard` linked to `/etc/vconsole.conf` by Pika's systemd) | Debian |
| Kernel | `kernel-pika` metapackage (and `kernel-pika-runtime`) on `linux-image-7.3.3-pikaos` and its headers; it Conflicts with Debian's `linux-image-amd64`. Files `/boot/vmlinuz-7.3.3-pikaos` (*check*) | none |
| Initramfs | **booster** 0.12.1 (Pika's fork, `ferrreo/booster`): `/etc/booster.yaml` (the installer writes `vconsole: true`, `extra_files: busybox`, `enable_lvm: true`, `modules`/`modules_force_load: usbhid,hid_generic`, `compression: lz4`, `strip: true`, `enable_plymouth`). Its package copies `/usr/lib/booster/update-initramfs` over `/usr/sbin/update-initramfs`, which runs `booster build --force --kernel-version K /boot/booster.img-K`; an apt hook and a timer (`booster-reassert`, every 4 h) put it back if initramfs-tools overwrites it, delete `/boot/initrd.img-*`, and regenerate when no `booster.img-*` exists. Backups `booster.img-K.dpkg-bak` / `.bak` | none (Debian's `update-initramfs` name only) |
| Boot loader | **rEFInd** (Pika's build, with `pika-refind-theme`): `refind-install`, then `refind_x64.efi` and the ext4 driver copied to `EFI/refind/`, with `refind.conf` including `refind-dark/refind-dark.conf`; `refind-mkdefault` puts it first in BootOrder. The installer's "BOOTX64 mode" (for MSI boards) uses `refind-install --usedefault`: fallback path only (`EFI/BOOT/`, `bootx64.efi`), no NVRAM entry. Outside BOOTX64 mode the installer writes no `EFI/BOOT` copy (P1 found; our `pikaos4` build adds one, so the guest also boots from the fallback). Kernel options in `/boot/refind_linux.conf`: three lines (standard, logging, safe graphics), each `[rd.luks.name=UUID=crypt_root rd.luks.options=discard] nowatchdog [nvidia-drm.modeset=1] rootflags=OPTS root=UUID=U [quiet splash]` | Limine (files on the ESP) |
| Secure Boot | Off: the wiki says to disable Secure Boot and CSM. UEFI only. (`sbctl-booster-extra` exists for users who sign with their own keys: out of scope) | |
| Install | `pika-installer-gtk4` (GTK) or `pikainstall-tui` over the **`pikainstall`** backend (Zig, 2026-11): an answers file (schema 1, `target.disk`, `--apply`, `--dry`), which "requires an explicit unused drive" | none so far |
| Default layout | GPT, by the GTK front end's own partition script (the one the 26.08.20-4 ISO runs, P1): **~500 MiB ESP at /boot/efi** (GPT name `linux-efi`, 1-500 MiB, no label); **1000 MiB ext4 /boot** (`linux-boot`, XBOOTLDR type, file-system label **`PikaOS`**); **/ XFS** on the rest (`linux-root`, no label), or Btrfs `@`/`@home` on LUKS2 `crypt_root`; optional /home partition (`linux-home`); no swap partition unless asked: **zram** (`zram-tools`, `ALGO=zstd PERCENT=100`). The Zig backend's (Gitea's head, not on that ISO) (`pikainstall apply`, `storage_prepare.zig`) is the same shape with GPT names `ESP`, `boot`, `root`, `home`, a 512 MiB ESP (1-513 MiB) and labels `EFI`, `PikaBoot`, `PikaRoot`, `PikaHome` | Fedora (ext4 /boot), Gentoo (XFS) |
| Btrfs layout | the same, / Btrfs with subvolumes `@` (/) and `@home`; no compression option; `refind-btrfs` and `apt-btrfs-snapper` packages exist (*check* whether installed by default) | Tumbleweed, CachyOS |
| LUKS | LUKS2 on / (`crypt_root`) and /home (`crypt_home`), no LVM; the passphrase is typed at boot (booster); `/etc/crypttab` for /home, `rd.luks.name` for / | `ubuntu26-luks` (LVM on LUKS) |
| Image | `https://iso.pika-os.com/PikaOS-Nest-GNOME-4.0-amd64-v3-26.08.20-4.iso` and a **`.md5` only**: no SHA-256, no signature. The host is behind bunny.net's bot check (the directory index answers 403 to a browser) | Mint (signed sums) |
| First boot | user `pikaos`/`pikaos` (UID 1001) with a first-setup wizard, unless the answers file creates the user (`PIKA_USER_MODE=user`) | |

## What already works, and what doesn't

**bko-holt `dump`:**

- `osrel.rs` `SUPPORTED` has no `pika` row: dump refuses it (its
  `ID_LIKE=debian` doesn't count, rightly). It needs `("pika", "4",
  "PikaOS 4")` (Decision 5).
- `installed_packages()` reads dpkg: works.
- `PACKAGES` (`boot.rs:214-237`) lacks `booster`, `kernel-pika`,
  `kernel-pika-runtime`, `pika-refind-theme`, `refind-btrfs`,
  `zram-tools` and the `linux-image-*-pikaos` names (*check* how
  `PACKAGES` takes a versioned kernel package; Debian's are listed by
  metapackage). `refind` is there. `testvm/update/fingerprint.py` mirrors
  the list.
- `initramfs_json()` (`boot.rs:758`) has no booster: on PikaOS it finds
  nothing, or `initramfs-tools` if that is installed too (*check*; booster's
  reassert suggests it can be). Needed: `booster` first when the package is
  installed, recording `/etc/booster.yaml`'s keys bko cares about
  (`universal`, `enable_lvm`, `modules`, `modules_force_load`,
  `compression`).
- `kernels_json()` (`boot.rs:606-620`) knows `initrd.img-V` and
  `initramfs-V(.img)`, not `booster.img-V`. It must read that as V's
  initramfs, and set aside `booster.img-V.dpkg-bak` and `.bak` the way it
  sets aside Gentoo's `.old` copies.
- `detect_loader()` (`boot.rs:815`) names rEFInd only from BootCurrent's
  path (`\EFI\refind\refind_x64.efi`). When the firmware booted the
  fallback (`\EFI\BOOT\bootx64.efi`: "BOOTX64 mode", OVMF without an entry,
  as in every QEMU guest whose vars start blank), it finds nothing, because
  the ESP check knows only grubx64.efi, systemd-bootx64.efi and Limine.
  Needed: an ESP holding `refind_x64.efi`, or a `refind.conf` beside a
  `bootx64.efi`, is rEFInd. Also record `/boot/refind_linux.conf` (path
  and lines), for the plan's note and the judge.
- /boot on an XBOOTLDR partition with no `/boot/loader/entries`:
  `boot.rs:936` takes an XBOOTLDR /boot as systemd-boot's entries only when
  they exist (*check* on the guest's dump).
- zram swap is not a disk device: nothing to do (as CachyOS).

**bko-holt `load`:**

- **Initramfs:** `initramfs()` (`load/commands.rs:2683`) falls to
  `update-initramfs -u -k all`. That happens to be booster's wrapper on
  PikaOS, but its `-u` path calls `dpkg-trigger` when run from a
  maintainer script and keeps `.dpkg-bak` copies: not a command to rely on.
  A `booster` arm instead runs, in the chroot, for each kernel in
  `/lib/modules` with a `kernel` directory (the dracut loop's guard),
  `booster build --force --kernel-version K /boot/booster.img-K`, which is
  what the wrapper itself runs. booster is host-only by default
  (`universal: false`): it picks the modules for the machine it runs on,
  from `/sys` (bound into the chroot, so the rescue system's view of the
  same hardware) (*check*: that a host-only image built in the rescue
  chroot boots the restored system; Decision 3's fallback is
  `--universal`).
- **Boot loader:** a `("refind", true)` arm, as Limine's: rEFInd's
  binaries, drivers, theme and `refind.conf` (in `EFI/refind` and
  `EFI/BOOT`) come back with the ESP, and `refind_linux.conf` with /boot.
  Its options stay right because the restore keeps every UUID it names:
  the file systems' (`mkfs -U`) and LUKS's (`luksFormat --uuid`). So
  nothing is installed; a note says so, and `efi_entries()` recreates the
  NVRAM entry ("rEFInd Boot Manager", `\EFI\refind\refind_x64.efi`) when
  the source had one. With `--removable`, `EFI/BOOT/bootx64.efi` is already
  rEFInd. A `refind_linux.conf` that names a UUID the layout doesn't have
  is a problem in the plan, not a surprise at boot. `refind-install` isn't
  run (Decision 4): it rewrites `refind.conf`, drops the theme include and
  makes its own NVRAM entry.
- rEFInd through shim: PikaOS doesn't use it; the arm refuses `via_shim`
  by name, as Gentoo's does.
- Nothing in load is keyed on `ID=debian` that PikaOS would wrongly take:
  `update_grub()`'s list is for GRUB, which PikaOS doesn't have, and the
  debconf note is BIOS GRUB's.

**`redact.rs`:** the installer's GPT names `linux-efi`, `linux-boot`,
`linux-root`, `linux-home` would be pseudonymised; they join
`GENERIC_LABELS`. `pikaos` joins `GENERIC_HOSTS` if it is the installer's
hostname (*check*), and `pikaos` joins `release_label()`'s `SYSTEMS` (the
ISO's volume label, *check*). These lines are shared with the AnduinOS and
elementary OS sessions (see "Shared lines").

**The rescue image:** no change ([[one-kernel-rescue]]). It runs on the
machine, so x86-64-v3 doesn't matter to it; only the chroot's commands
(booster, Pika's binaries) need it, and they run on the same CPU.

## Guests

Two, both UEFI, Secure Boot off (`vm-run-options`: `--uefi
--no-secure-boot`), from the GNOME standard ISO (Decision 7), with the ISO
and its `.md5` in `/bkoscratch/rebuild-inputs/GUEST/` on every host that
builds, and a SHA-256 pinned in the script on first download (the `.md5`
is checked too, but proves only the transfer).

| Guest | Layout | What it covers |
|---|---|---|
| `pikaos4` | The installer's defaults: GPT, 500 MiB ESP at /boot/efi, 1000 MiB ext4 /boot (XBOOTLDR, label `PikaOS`), / XFS, zram, rEFInd with an NVRAM entry, plus an `EFI/BOOT` copy our build adds (the installer doesn't outside BOOTX64 mode) | booster's host-only rebuild, rEFInd kept from the ESP, `refind_linux.conf`'s `root=UUID=`, the XBOOTLDR /boot; the default install |
| `pikaos4-luks` | The installer's Btrfs + encryption choice: the same ESP and /boot, / Btrfs `@` and `@home` on LUKS2 `crypt_root`; "BOOTX64 mode" (fallback path only, no NVRAM entry) | booster unlocking LUKS from `rd.luks.name`; Btrfs subvolumes; the fallback-only detection. The passphrase is typed over the serial console at boot, by `console-boot.py` from `vms/pikaos4-luks/console-passphrase` (booster prompts on ttyS0, P4; [[front-load-manual-tests]] puts its runs first) |

How they are built (Decision 1):

1. **pikainstall's own backend, unattended** (recommended): a new
   `testvm/make-pikaos.sh FLAVOR ISO WORK_DIR` boots the PikaOS ISO under
   QEMU (`-cpu host`, OVMF, `DISK_SIZE=32G`: desktops need 26 GiB of root
   plus swap, `MEMORY=4096`), with a second CD holding an answers file
   (`target.disk` = the blank disk, `storage` = XFS or Btrfs+LUKS2, user
   mode `user`, no NVIDIA, no extras) and a post-install script. It logs in
   on the serial console as the live user (`pikaos`/`pikaos`, from the
   wiki), and runs `pikainstall --answers ... --apply` (*check* the exact
   invocation and that the GNOME ISO carries the Zig `pikainstall`, not
   only the GTK front end; the README's text mode is the Minimal media's).
   The post-install script, in the target's chroot, does what every
   `--live` guest gets: text-mode boot with a serial console (an extra
   `console=ttyS0,115200n8` on `refind_linux.conf`'s lines), root's
   generated password and SSH key, `openssh-server` (*check* it's on the
   ISO), the `tester` user. Prints BACKOTTER-INSTALL-OK, as
   `arch/install-live.sh` does. That gives the installer's real layout,
   booster config, rEFInd files and theme, and its NVRAM entry.
2. **`make-from-live.sh pika ...`, by hand** (the fallback): a new `pika`
   family that does what `auto_xfs.rs` and `pika-install-chroot.sh` do:
   parted, mkfs, the live image copied, `genfstab -U`, `refind-install`,
   the theme, `/etc/booster.yaml`, `refind_linux.conf`, `update-initramfs
   -c -k all`. Closer to a copy of the installer than the installer: est.
   +0.5 d and more drift.
3. **By hand once through the GUI**, kept as a pinned image: not
   rebuildable by `rebuild-one.sh`, so not recommended.

Each guest gets: `rebuild-one.sh` lines (and its ISO check), the rebuild
inputs on both hosts, `holttest1d/ram-sizes.tsv` rows (measured in P6: `pikaos4` D 17,
`pikaos4-luks` D 16, MEM 4; peaks ~14 GiB, not the D 8 first estimated), `classes.json` entries (done 2026-11-14), both hosts by `vm-store` publish,
a GuestTest1 grade.

**vm-update:** `in-guest.sh`'s `# t2-section: Linux.apt` works as on Debian
(`apt full-upgrade` against Pika's archive), with two guards (*check* each
on P1's `--try`): booster's apt hook (`99booster-reassert`) runs after
every dpkg run and must leave `booster.img-*` in place; and a new kernel
(`kernel-pika` moves often: 7.3.1 to 7.3.3 in a week) adds a
`linux-image-K-pikaos` beside the old, so the update removes kernels beyond
the newest two as the Debian branch does. Sid churn is the main risk
(Risks). `fingerprint.py`'s `LINUX_KINDS`, `pkgset.sh` and the
`vm-update` header name it. GuestTest2: a `K-pika` rule in
`t2lib/guest_judge.py` (`KERNELS_MAX`, `booster.img-K` names) and
`guest/known-lines.json` lines for Pika's apt sources.

## Tests

**SticTest1** (`testvm/stictest1/pikaos4`, `pikaos4-luks`): one `inner.sh`,
`fixture.sh` and `judge.py` for both, reading the layout from the guest, as
Gentoo's do. `pikaos4` from `gentoo`'s (XFS, an ext4 volume, an ESP with
`fatattr.py`), without LVM; `pikaos4-luks` from `tumbleweed`'s (Btrfs
subvolumes) with `gentoo-systemd`'s LUKS2 made with the guest's header
parameters. The scratch disk is laid out like the guest's: GPT with the
installer's partition names and types (XBOOTLDR included). Both archivers,
both hosts. `stic/manifests.json`, `limits.json`, and
`check-manifests.py`'s count up by two (47 today; the sibling sessions add
theirs: whoever lands last sets the number).

**HoltTest1:** both guests join **1Obscure** (Decision 6): `classes.json`'s
group, the README's row and HoltTest1All's members. `pikaos4-luks`'s runs
need the typed passphrase at the boot stage. judge.py allows what the boot
stage rewrites: `boot/booster.img-*` (content and mode; booster writes the
file anew), and `var/log/bko-holt`. It **requires** `boot/refind_linux.conf`
and every file under `boot/efi/EFI/refind` and `boot/efi/EFI/BOOT` to be
byte-identical to the source's: that is the test that the loader was kept,
not reinstalled. ht1.sh's boot stage boots the restored disk alone: on
`pikaos4` from the recreated NVRAM entry, on `pikaos4-luks` from the
fallback.

**Test2:** golden plans in `holt/tests/corpus/pikaos4/` and
`pikaos4-luks/` (`src.json`, `plan.txt`, `index.tsv`, the corpus README)
from `bko-holt dump` of each guest; `classes.json` (`uefi`, the group),
`holt/spec.json`, `guest/spec.json`, `estimates.tsv`, `t2lib/plan.py`'s
counts, `tests/fixtures/p2/world.py` and the re-blessed goldens. Done when
two corpus builds have the same sha256, leak scans are 0 on both hosts and
the Mac, no other plan changes, and Test2's own tests pass. The Test2 files
carry uncommitted cachyos-zfs work today; this waits until it is committed.
**As built (2026-11-14):** the corpus entries (`holt/tests/corpus/pikaos4`,
`pikaos4-luks`, their `index.tsv` rows) are made, and the registrations are
done (2026-11-14, both guests alone, as vanilla3's, ahead of the batched
pass): `classes.json` (`holt.families.Obscure`, `holt.uefi`),
`holt/spec.json` (UEFI; `pikaos4-luks` with `console-boot.py`),
`guest/spec.json` (pm apt, `S-apt`, `L-none`, `K-pika`, `B-lin`, `M-lin`,
fw uefi, `ram_gib` 10 from the --try's 8.7 GiB peak), `guest/limits.json`
(`image:sources`' "no -security suite": Pika's one suite `pika` is
rolling), the `K-pika` rule (`guest_judge.py`, `check-image.sh`'s
`no-booster` mark, `check-after.sh`), 14 apt/dpkg patterns in
`guest/known-lines.json` from the two --try logs, `estimates.tsv` (holt
from P6's `p6-4`/`p6-6` phases, guest from the --try), `t2lib/plan.py`'s
and the README's counts, `world.py`, the tests' counts and the re-blessed
p2 goldens.

**BackShot1** (optional, P8): the Thinkpad reinstalled with PikaOS from the
GUI installer, defaults (XFS), *check* first that its CPU is x86-64-v3.
Fixes, est.: `bs1-adopt` none for init (systemd), apt as on Debian
(`openssh-server`, `avahi-daemon`, `efibootmgr` installed if missing:
*check* what the GNOME install has); `bs1-common`'s `package_list` none
(dpkg); a hold of `booster-reassert.timer` during the backup, as snapper's
timers are held; `bs1-manifest` `EXPECTED` gains `boot/booster.img-*`;
`refind_linux.conf` and the ESP's rEFInd files must be unchanged. A
`bs1-qemu.py --virtio` rehearsal on `pikaos4` on both hosts first. LUKS on
the Thinkpad isn't proposed: booster has no key-file-on-USB unlock like
[[bs1-luks-unattended]]'s (*check*).

## Shared lines (with AnduinOS, elementary OS, KDE neon, BigLinux, Garuda)

Per the elementary session's note in the shared notes folder: each system
its own row in `osrel.rs` `SUPPORTED` (the second to land rebases);
`redact.rs`'s `GENERIC_LABELS`, `GENERIC_HOSTS` and `SYSTEMS` edited once
per session, re-read first; `classes.json`, the holttest1d README's group
rows and `check-manifests.py`'s count one session at a time. PikaOS
touches neither `update_grub()`'s list nor `make-from-live.sh` (unless
Decision 1 falls to path 2, then its own `pika` arm). Before editing any of
these, append to that note who holds which line.

## What P1 found (the ISO, read offline)

`PikaOS-Nest-GNOME-4.0-amd64-v3-26.08.20-4.iso`, 3,049,144,320 bytes, MD5
`0efa544f...` as published, SHA-256
`ce26e89ee468ae146e2c7cefc6fd3a022face03316ccfa58caadb3090019a1ea` (pinned),
volume id `PGN 26.08.20 4`, read 2026-11-14 on Chopin. The CDN gives about
0.2 MB/s a connection and about 1 MiB/s an address: 16 ranged connections
took 20 min.

- **Boot:** the ISO holds only `/live/filesystem.squashfs` and an El Torito
  EFI image with rEFInd (`EFI/BOOT/BOOTX64.EFI`), the kernel `EFI/VMLINUZ`
  and booster's initrd `EFI/INITRD`; options `boot=live booster.loadcdrom
  booster.skiproot [quiet splash]`. UEFI only.
- **os-release** as the spec's table: `ID=pika`, `ID_LIKE=debian`,
  `VERSION_ID="4"`. Hostname `pikaos` (copied into the installed system:
  real installs are called `pikaos`).
- **Packages:** `kernel-pika` 7.1.8 (`/boot/vmlinuz-7.1.8-pikaos`,
  `booster.img-7.1.8-pikaos`, `config-`, `System.map-`); booster
  0.12.1 **and initramfs-tools 0.151** both installed; refind 0.14.2;
  systemd 261.2; openssh-server (ssh.service enabled in the live system),
  avahi-daemon, efibootmgr, zram-tools, xfsprogs, btrfs-progs, cryptsetup.
  The live `booster.yaml` is `universal: true`; the installer writes a
  host-only one (no `universal`) into the target.
- **The installer is older than the Gitea head:** `pika-installer-gtk4`
  2.0.5 with `pikainstall` 2.0.5 (Python), not the Zig `pikainstall apply
  --answers`. The GTK front end partitions by its own script (`auto_xfs.rs`'s:
  `linux-efi` 1-500 MiB vfat, `linux-boot` 500-1500 MiB ext4, `linux-root`
  XFS; Btrfs `@`/`@home`; LUKS `crypt_root`), mounts at
  `/media/pikaos/installation`, then runs `pikainstall -r PATH -l LOCALE -k
  KEYMAP -kl LAYOUT -t TZ -swt TYPE -sws SIZE -rfs TIMEOUT -rfb BOOL`. The
  backend unsquashes the image, sets `bls_boot` on /boot and `esp` on the
  ESP, writes `refind_linux.conf` (`nowatchdog nvidia-drm.modeset=1
  rootflags=... root=UUID=U`; on LUKS `rd.luks.name=UUID=crypt_root
  rd.luks.options=discard ... root=/dev/mapper/crypt_root`) and crypttab
  (`crypt_root UUID=U none luks,discard`), runs `refind-install --root` (or
  `--usedefault` in BOOTX64 mode), copies the ext4 driver, writes
  `booster.yaml`, and in the chroot enables zram, installs the theme and
  booster, `refind-mkdefault`, removes the installer, and makes the
  first-setup user `pikaos`/`pikaos` (UID 1001).
- So Decision 1's path is the ISO's own: the build runs the GTK front
  end's partition script for the chosen layout, then the ISO's
  `pikainstall` with the front end's flags, over SSH into the live system
  (root's key by an SMBIOS credential, as `cachyos-zfs`).

## What P2-P6 found

2026-11-14, nothing committed yet:

- **bko-holt (P2):** `("pika", "4", "PikaOS 4")` in `SUPPORTED`. dump
  names booster when its package is installed (initramfs-tools is too) and
  records `booster.yaml`'s keys under `boot.initramfs.booster`; it reads
  `booster.img-K` as K's initramfs and sets the `.dpkg-bak`/`.bak` copies
  aside; rEFInd comes from BootCurrent's path or the ESP's files, and
  `boot.refind.linux_conf` records `refind_linux.conf`. load runs `booster
  build --force --kernel-version K /boot/booster.img-K` per kernel
  (host-only, in the chroot), keeps rEFInd's ESP files and
  `refind_linux.conf` (nothing installed; only an NVRAM entry, if the
  source had one), refuses rEFInd via shim, and makes it a plan problem
  when `refind_linux.conf` names a `root=UUID=` or `rd.luks.name` UUID the
  layout lacks. `redact.rs` keeps `linux-efi`/`-boot`/`-root`/`-home`,
  `PikaBoot`/`Root`/`Home`, the `PikaOS` label, the `pikaos` host and the
  release word.
- **The guests (P1, P4):** built from
  `PikaOS-Nest-GNOME-4.0-amd64-v3-26.08.20-4.iso` (SHA-256 ce26e89e...,
  in `/bkoscratch/rebuild-inputs/pikaos4` on both hosts) by the ISO's own
  GTK partition script and `pikainstall`, over SSH (an SMBIOS key
  credential). `pikaos4`'s installer makes the rEFInd NVRAM entry but no
  `EFI/BOOT` copy; our build adds that copy. `pikaos4-luks` is BOOTX64
  mode (`EFI/BOOT` only, no NVRAM entry); booster prompts for the
  passphrase on ttyS0 and `console-boot.py` types it from
  `vms/pikaos4-luks/console-passphrase`. The ISO's 7.1.8 kernel oopsed once
  in four boots while loading modules, so `make-pikaos.sh` retries a boot
  once on an Oops or BUG before SSH, and `--first-boot-only` finishes an
  existing WORK_DIR. qcow2 SHA-256 `pikaos4` 77cca138..., `pikaos4-luks`
  6a083b8c..., the same on Chopin and Cinqcent. `vm-update --try` ok on
  both (kernel 7.1.8 to 7.3.3, ~720 s; booster's apt hook leaves the images,
  no `in-guest.sh` change). Builds ~3 min (`pikaos4`) and a ~5 min install
  (`pikaos4-luks`) once RAM is free; the CDN throttles, so the ISO came in
  16 ranged connections (~20 min).
- **SticTest1 (P5):** `testvm/stictest1/pikaos4` and `pikaos4-luks`, shared
  scripts derived from `gentoo`'s, `tumbleweed`'s and `gentoo-systemd`'s.
  `pikaos4` 33 PASS, 3 LIMIT, 0 FAIL (`pk5d`, ~300 s); `pikaos4-luks` 34, 4,
  0 (`pk5c`, ~400 s); both hosts, both archivers. check-manifests' count
  51.
- **HoltTest1 (P6, 1Obscure):** `pikaos4` PASS `p6-4` (Chopin) and `p6-5`
  (Cinqcent), `pikaos4-luks` `p6-6` (Chopin) and `p6-7` (Cinqcent): every
  load stage, the boot alone from the `EFI/BOOT` fallback (`pikaos4-luks`
  with the typed passphrase), 0 fsmeta differences, rEFInd's files and
  `refind_linux.conf` byte-identical (judge.py's rEFInd rule), 0 dump
  differences. judge.py also allows the `booster.img-K` rewrites and a
  zram swap-size drift. `ram-sizes.tsv`: `pikaos4` D 17, `pikaos4-luks` D
  16, MEM 4 (peaks ~14 GiB). The plan's `efibootmgr` rEFInd line was
  checked on its own on a rescuer boot (Cinqcent).

## What P8 found

2026-11-14, BackShot1 on the Thinkpad (i5-10210U: AVX2, so x86-64-v3;
Secure Boot off), from Guix System (its backup `guix-20261114-114854` is
in the library). Nothing committed.

- **The install, from the rescue stick:** `testvm/realworld/pika-bootstrap.sh
  SERIAL ISO INSTALL_SH KEYS_DIR HOSTNAME` (modelled on
  `guix-bootstrap.sh`), with the ISO on `mybig512` in `iso/pikaos4/` (its
  SHA-256 checked against the pin) and the scripts in `backshot1/pika/`.
  It erases the disk by serial only, loop-mounts the ISO and its
  `live/filesystem.squashfs` under a tmpfs overlay, binds `live/` at
  `/live` (where pikainstall unsquashes from; the squashfs carries
  `/cdrom/pool` itself), and `/dev`, `/proc`, `/sys` with efivarfs and the
  rescue's udev database, then runs `testvm/pika/install.sh` in that chroot
  with `PIKA_DISK=nvme0n1 PIKA_METAL=1`. install.sh is the guest's, with
  those two settings from the environment (the guest build is unchanged):
  `PIKA_METAL` leaves out the serial console, text-mode boot, the `tester`
  user, the `EFI/BOOT` copy and root's password. Then the fixed host key
  and BackShot1's `authorized_keys` go in. install.sh took 43 s; the whole
  bootstrap about 1.5 min.
- **What the installer made, as on `pikaos4`:** GPT `linux-efi` (vfat,
  ESP flag), `linux-boot` (ext4 `PikaOS`, `bls_boot`), `linux-root` XFS
  (exchange, parent pointers; the rescue's 7.0 kernel mounts it); zram;
  booster's image; rEFInd in `EFI/refind` with Pika's theme and its NVRAM
  entry, Boot0028 "rEFInd Boot Manager", first in BootOrder (by
  refind-install: `refind-mkdefault` in the target's chroot says "EFI
  variables are not supported", as in the guest build). rEFInd also keeps
  `EFI/refind/vars/PreviousBoot` and an `EFI/tools` directory on the ESP.
  The first boot came up from that entry (BootCurrent 0028), as
  `thinkpad` with sshd, avahi, python3 and efibootmgr already there:
  `bs1-adopt` installed nothing. `booster-reassert.timer` is installed but
  disabled. The ISO ships one set of SSH host keys for every install
  (`bs1-adopt` replaces them, as everywhere).
- **The first bootstrap's teardown failed** (the install itself was
  fine): `/run` is shared, so the chroot's mounts were copied into
  udevd's, logind's, resolved's, networkd's and chronyd's own namespaces,
  and stayed there, keeping the ISO busy. The script now makes its work
  directory private before mounting anything under it; the second run
  (the install the backup is of) tore down cleanly.
- **BackShot1 changes:** `bs1-common`'s held timers gain
  `booster-reassert`; `bs1-manifest`'s `EXPECTED` gains
  `boot/booster.img-*`, and a new `KEPT` list (`boot/efi/EFI/refind/*`)
  overrides `boot/efi/*`: each rEFInd file must come back with its size and
  content, none missing or added; `bs1-adopt` installs efibootmgr by apt
  on PikaOS if it is missing. `refind_linux.conf` on /boot was already
  compared in full.
- **bko-holt:** a static-pie glibc build of the working tree (main a435027
  plus the uncommitted P2 work), 0.2.6, SHA-256 `6ed95fe1...afdda`, staged
  in `backshot1/bin/` (the earlier one in `bin.prev-20261114-pika`);
  rustic-bko 0.11.4+backotter.11 unchanged. No source change was needed.
- **The run: PASS,** `pika-4-20261114-134728`, restore `20261114-135316`.
  Preflight OK, `UNLOCK=none`, three sources (`/` XFS, `/boot` ext4,
  `/boot/efi`). Phase 1: 264,307 files, 9.1 GiB (4.0 GiB stored), backup
  88 s, `check --read-data` 37 s, 3 min in all. Plan PASS: XFS and ext4 made
  with the source's features and UUIDs, one `booster build` per kernel in
  the chroot, the rEFInd note ("nothing to install"), `efi-dedupe` and one
  `efibootmgr --create` for rEFInd. Phase 2 PASS in about 4 min. The
  restored system booted from Boot0028 (the old entry deleted, the new one
  took its number and its place at the front of BootOrder), with the
  host-only booster image built in the rescue's chroot, and again from
  BootOrder alone after a plain reboot.
- **The first verify failed** on 8 files with only a new mtime, written at
  every boot by PikaOS's services: tuned's `etc/tuned/{active_profile,
  post_loaded_profile,ppd_base_profile,profile_mode}` and
  `etc/modprobe.d/tuned.conf`, supergfxd's `etc/supergfxd.conf` and
  `etc/modprobe.d/supergfxd.conf`, and `usr/bin/steamdeps`
  (`steam-repos-fix.service` copies it there). They joined `REMADE` (all
  but the mtime compared); then **verify PASS**, 275,688 files compared, 0
  unexpected. `refind_linux.conf`, `refind.conf` and `refind_x64.efi`
  came back with the same content and mtime, and every file in
  `EFI/refind` passed `KEPT`.
- **Not done:** the QEMU rehearsal of the bootstrap (a rescuer and a blank
  NVMe on Chopin) waited 27 min for room and was cancelled once the real
  install had worked; the `bs1-qemu.py --virtio` rehearsal on `pikaos4`
  wasn't run either. The real run went first, as Guix's did.

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| P1 | Fetch the GNOME ISO (size *check*, ~3-4 GB) and `.md5` into rebuild-inputs on Chopin (the user's OK first; if bunny.net's check refuses a plain download, the user fetches it in a browser), pin its SHA-256. Read the live system offline: os-release, the kernel and booster packages, `/etc/booster.yaml`, `pikainstall`'s CLI and answers schema, whether initramfs-tools is installed, the hostname, the ISO label. Then `make-pikaos.sh pikaos4` by Decision 1's path, `rebuild-one.sh`, copy to Cinqcent (the same SHA-256), vm-update's guards. Settle every *check* and write "What P1 found" here. Done when it boots alone on both hosts, SSH works, and `vm-update --try` is ok. **Done 2026-11-14:** `testvm/make-pikaos.sh FLAVOR` and `testvm/pika/install.sh`, the ISO's GTK 2.0.5 partition script and `pikainstall` 2.0.5 over SSH; ~3 min build; qcow2 SHA-256 77cca138... on both hosts; boots alone on both; `vm-update --try` ok on both ("What P1 found", "What P2-P6 found") | 1.25 d (+0.5 d on path 2) |
| P2 | bko-holt (High-effort subagent): the `SUPPORTED` row and tests (`pika` 4 accepted, another `ID_LIKE=debian` refused); `PACKAGES` and `fingerprint.py`; booster detection and `booster.yaml`'s keys; `booster.img-K` in `kernels_json` with the `.dpkg-bak`/`.bak` copies set aside; rEFInd from the ESP's files; `refind_linux.conf` recorded; load's booster arm and `("refind", true)` arm (the via_shim refusal, the UUID check of `refind_linux.conf`); `redact.rs`'s words; a PikaOS fake tree in `holt/src/scenarios.rs`. Done when `cargo test` passes and the scenario's plan has one `booster build` per kernel, no `refind-install`, no `update-initramfs`, the rEFInd note and the NVRAM entry. **Done 2026-11-14** (uncommitted): `SUPPORTED`'s row, booster's detection and load arm, `booster.img-K`, rEFInd from BootCurrent or the ESP's files, the kept-rEFInd arm and its refusals, `redact.rs`'s words | 0.75 d |
| P3 | `pikaos4`'s golden plan and Test2 registrations (as Gentoo's G2b). **Done 2026-11-14** for the golden plan (`holt/tests/corpus/pikaos4`, its `index.tsv` row); the Test2 registrations done 2026-11-14 (with `pikaos4-luks`'s, as vanilla3's) | 0.4 d |
| P4 | `pikaos4-luks`: the build (Btrfs, LUKS2, BOOTX64 mode), the typed passphrase, its golden plan and Test2 lines, `rebuild-one.sh`. Done as P1 and P3 are. **Done 2026-11-14:** ~5 min install, qcow2 6a083b8c... on both hosts, the passphrase typed by `console-boot.py`, golden plan `pikaos4-luks` and its `index.tsv` row; Test2 lines done 2026-11-14, as P3's | 0.5 d |
| P5 | SticTest1 entries and runs, both guests, both archivers, both hosts, 0 FAIL. **Done 2026-11-14:** `pikaos4` 33 PASS/3 LIMIT/0 FAIL (`pk5d`), `pikaos4-luks` 34/4/0 (`pk5c`), both hosts and archivers; check-manifests' count 51 | 0.35 d |
| P6 | HoltTest1 runs (1Obscure) on both hosts with the judge rules and the byte-identical rEFInd check; the boot alone; RAM peaks in `ram-sizes.tsv`. **Done 2026-11-14:** 1Obscure; `pikaos4` `p6-4` (Chopin) and `p6-5` (Cinqcent), `pikaos4-luks` `p6-6` (Chopin) and `p6-7` (Cinqcent), all PASS | 0.4 d |
| P7 | Docs: holt/README.md's supported list, `osrel.rs`'s comment, HoltTest1's 1Obscure row and HoltTest1All, SticTest1's and Test2's READMEs, `layload.md`'s boot stage (booster, the kept rEFInd), memory (holttest1d index, a pikaos-support note). **Done 2026-11-14:** holt/README.md's list and tests, HoltTest1's 1Obscure row and HoltTest1All's members, SticTest1's README, layload.md's boot stage (booster, the kept rEFInd); `osrel.rs`'s comment was P2's; Test2's README counts done 2026-11-14 with the registrations; memory is the session's | 0.1 d |
| P8 | *Optional, later:* BackShot1 on the Thinkpad: the fixes above, the rehearsals, then the run. Done when phase1, plan, phase2 and verify PASS with 0 unexpected and the Thinkpad boots rEFInd from NVRAM. **Done 2026-11-14** (uncommitted): PikaOS installed from the rescue stick by `testvm/realworld/pika-bootstrap.sh`, run `pika-4-20261114-134728` PASS in every phase, 0 unexpected, rEFInd booted from its recreated NVRAM entry; no bko-holt change; the QEMU rehearsals not run ("What P8 found") | 0.5 d + ~1 h of the user's time (none taken) |
| | *≈ 3.75 d without P8, ≈ 4.25 d with it* | |

P2 can start beside P1 from the source facts above, but its golden plan
(P3) waits for P1's dump. Each piece is committed on main when its tests
pass and the user says to commit; nothing is pushed until the user says so.
Source-code changes go to High-effort subagents
([[effort-max-when-it-pays]]).

**Costs** (est.): P1 and P4 a 20-40 min build each on Chopin
(`--reserve 12 4` in /mnt/bko-ram: the 32G disk is sparse, the install
writes ~10 GiB), the copy; qcow2 ~8-10 GiB a guest a host, the ISO ~3-4 GB
a host. P5 and P6 about 1 h of queue a host. None of the user's time but
the OKs (and, for `pikaos4-luks`, nothing typed by hand: the script types
it), until P8.

## Risks

- **The download.** iso.pika-os.com is behind bunny.net's bot check, and
  only an MD5 is published. A refused download needs the user's browser;
  the pinned SHA-256 guards every later rebuild, not the first download.
- **The unattended install.** pikainstall's answers path is two weeks old
  and its README speaks of the Minimal media. If the GNOME ISO's backend
  won't run headless, path 2 costs +0.5 d.
- **booster's host-only image in the rescue chroot.** If it misses a
  module (the restored disk's controller, XFS), the restored system won't
  mount its root. HoltTest1 shows it; the remedy is `--universal` for that
  build, at a larger image (Decision 3).
- **Sid churn.** A vm-update may pull a broken sid package or a kernel
  whose DKMS build fails; it stops and names it, as Gentoo's source builds
  do. A rebuild gets newer packages than the last
  ([[images-take-os-updates]]).
- **ISO pruning.** PikaOS keeps few old ISOs; the rebuild inputs keep
  ours. A lost ISO means the newest one and a new pin.
- **x86-64-v3.** A host or Thinkpad without AVX2 can't run the guest's or
  the system's binaries. All four hosts have it.

## Out of scope

- Secure Boot (PikaOS tells users to turn it off), sbctl-signed setups,
  shim.
- The NVIDIA ISOs and `nvidia-drm.modeset=1` (no GPU passthrough).
- Swap partitions and swap files (zram only, the default; a swap
  partition is generic and already supported).
- ext4 root (generic, covered by many guests), a separate /home partition
  (generic), `apt-btrfs-snapper`'s snapshots (unless P4 finds them on by
  default: then they are handled as snapper's are).
- rEFInd on other systems, and systemd-boot or GRUB on PikaOS: not claimed.
- PikaOS 3 and older (Ubuntu-based, a different stack); arm64 builds.
- BackShot2 cases on PikaOS: later, through BackShot2All.

## Decisions for the user

1. **How the guests are built:** pikainstall's own backend from the GNOME
   ISO, unattended over the serial console with an answers file
   (recommended: the installer's real layout, booster config and rEFInd
   setup); or a `pika` family in `make-from-live.sh` that copies the
   installer's steps by hand (+0.5 d, drifts); or one GUI install kept as
   a pinned image (not rebuildable).
2. **Guests:** `pikaos4` (defaults: XFS, rEFInd entry) plus
   `pikaos4-luks` (Btrfs on LUKS2, BOOTX64 mode) (recommended: the second
   covers booster's LUKS unlock, Btrfs and fallback-only detection for
   0.5 d); or `pikaos4` plus a Btrfs guest without LUKS (no typed
   passphrase, less coverage); or only `pikaos4`.
3. **booster in load:** `booster build --force --kernel-version K
   /boot/booster.img-K` per kernel, host-only as the source's were
   (recommended: the wrapper's own command, without its dpkg-trigger and
   backup paths); or Pika's `update-initramfs -c -k all` wrapper as is;
   or always `--universal` (always boots, larger image, differs from the
   source's).
4. **rEFInd in load:** keep the restored ESP files and `refind_linux.conf`,
   make only the NVRAM entry (recommended: as Limine; nothing drifts from
   the source); or run `refind-install` in the chroot (rewrites
   `refind.conf`, loses the theme include, new NVRAM entry).
5. **The supported version:** `pika` with VERSION_ID `4` (recommended: a
   PikaOS 5 is checked before it is claimed); or `*`, as for rolling
   systems that number snapshots.
6. **HoltTest1's group:** join **1Obscure** (recommended: as Chimera did;
   PikaOS isn't on the Thinkpad); or a new **1P**.
7. **The ISO:** the GNOME standard (non-NVIDIA) edition, `26.08.20-4`,
   SHA-256 pinned on first download (recommended: the first-listed
   edition; the desktop doesn't matter to bko and boots to text mode); or
   the smallest edition, whichever that is (*check* sizes).
8. **BackShot1:** later and optional, after the guests pass (recommended:
   it needs a Thinkpad reinstall and an x86-64-v3 check); or now, with
   P1; or not at all.
