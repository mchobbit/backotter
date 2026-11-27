# PCLinuxOS support

**Status: Draft 2026-11-27, awaiting the user's approval; nothing built.**

This spec adds PCLinuxOS to bko-holt, to both archivers' tests and to the
QEMU lanes, the way Solus, Slackware and PikaOS were added
(docs/design/solus.md, slackware.md, pikaos.md). BackShot1 on the A100 is
the last step and is optional.

**Conventions.**

- est. marks an estimate.
- **Verified** marks a fact read from an upstream source, with its URL, or
  from a package's own files.
- *Inferred* marks a conclusion drawn from those facts that no source
  states.
- *Check* marks something only a booted ISO or a guest can settle. PC1
  settles each one before any code that depends on it is written.

**Sources, read 2026-11-27.**

- www.pclinuxos.com, its forum and its KnowledgeBase sit behind a
  Cloudflare bot challenge (HTTP 403). Nothing was read from them, and no
  attempt was made to get past the challenge.
- The facts below come from these sources instead:
  - DistroWatch (https://distrowatch.com/table.php?distribution=pclinuxos,
    and its `&pkglist=true&version=2026.11` page);
  - the NLUUG master mirror (https://ftp.nluug.nl/os/Linux/distr/pclinuxos/,
    called MIRROR below) and the freedif mirror
    (https://mirror.freedif.org/PCLinuxOS/);
  - PCLinuxOS Magazine (pclosmag.com);
  - the repo's repodata;
  - a few RPMs from `MIRROR/apt/pclinuxos/64bit/RPMS.x86_64/`, unpacked and
    read as text, never run: mylive-install 1.0.26, mkinitrd 6.0.93,
    bootloader-utils 1.16, drakxtools-backend 17.20, initscripts 9.27,
    rpm 6.1.0, mylivecd 0.9.14, pclinuxos-release 2026, dnf-sources-list 2.0
    and the kernel-6.18.65-pclos1 header.
- The raw notes and unpacked files are in this session's scratchpad
  (`pclos/x/`, `pclos/md/`). No ISO has been downloaded or booted.
- Code references are to local `main` at d4aea22.

**The briefing was out of date.** The request described PCLinuxOS as
"apt-rpm, draklive-install". In 2026 neither is true, and much of this
spec follows from that:

- **The package manager is DNF5.** apt-rpm and Synaptic are retired.
- **The installer is mylive-install**, an LMDE live-installer fork, since
  2023.07.
- **The initramfs is made by Red Hat's old nash-based mkinitrd**, not dracut.
- **The installer offers no LUKS and no LVM.**
- *Inferred:* **there is no Secure Boot support.** No shim or mokutil is
  shipped; no upstream page says so either way (see Open questions).
- **The ESP is mounted at `/boot/EFI`** (mylive-install's
  `frontend/partitioning.py:26`, `EFI_MOUNT_POINT = '/boot/EFI'`).

## Summary

**What PCLinuxOS gets.** PCLinuxOS (rolling; the ISOs are numbered 2026.11)
becomes a supported system for bko-holt and both archivers, with:

- two QEMU guests, `pclinuxos` (UEFI) and `pclinuxos-bios` (BIOS);
- a SticTest1 entry for each;
- a place in HoltTest1 **1Obscure**;
- golden plans and the Test2 registrations.

BackShot1 on the A100 (PC8) is an optional later step. It needs the A100
reinstalled from the rescue stick by a new `pclinuxos-bootstrap.sh`.

**What it brings that no supported system has:**

- **A third initramfs generator lineage.** Red Hat's mkinitrd 6.0.93,
  with `nash` as the image's init, writes `/boot/initrd-K.img` and is driven
  by `installkernel` and `bootloader-config`. bko-holt finds no generator
  today. Its `_` fallback (`load/commands.rs:7332`) would then run
  `update-initramfs`, which doesn't exist on PCLinuxOS, so the boot stage
  would fail.
- **GRUB 2 in `/boot/grub2` with no shim.** Today the UEFI arm for
  `/boot/grub2` (`commands.rs:6387`) assumes shim, as on Fedora, Rocky and
  openSUSE. It keeps the restored `grubx64.efi` and remakes only grub.cfg.
  That leaves an image with an old embedded prefix: the BackShot2 fail-002
  class of bug (the generic arm's comment, `commands.rs:6448-6457`; Guix's
  store-prefix patcher, `grubprefix.rs`, is the same class). Qubes 4.3 also
  boots GRUB 2 from `/boot/grub2` without shim, but it has its own arm
  ahead of this one (`commands.rs:6382-6385`).
- **An RPM system on sysvinit with chkconfig.** No supported system
  combines these. holt itself is init-agnostic. bs1-adopt,
  `compare/boot-state.sh` and ht1.sh's boot-state check know Debian's
  sysv-rc and Slackware's BSD-style rc, not Mandriva's `rc.d` with
  chkconfig.

**What already works:**

- `rpm -qa` for the package list (holt and bs1-common);
- `grub2-install`/`grub2-mkconfig` naming by `/boot/grub2`;
- BIOS `grub2-install --target=i386-pc`;
- NVRAM entries from `efi_entries()`;
- the readback's `initrd-*` listing;
- the `boot/initrd-*` and `boot/grub2/*` lines in bs1-manifest.

**Cost:** about 3.7 d (est.) without BackShot1. BackShot1 (PC8) adds
about 0.75 d and an hour of the user's time (≈ 4.45 d in all).

## Why

- **A generator whose images aren't dracut's.** nash images have no
  `lsinitrd`, no hooks and no `rd.*` options. Supporting them also covers
  any later RHEL-5/6-lineage generator, and it keeps the `_` fallback from
  running Debian's tool on a system that isn't Debian.
- **The non-shim `/boot/grub2` UEFI case.** It is a latent bug today: any
  GRUB 2 system that installs with plain `grub2-install` and no shim gets
  a restored loader that may not match its new grub.cfg. PCLinuxOS is the
  first supported system that reaches the `/boot/grub2` shim arm without
  shim (Qubes, the other non-shim `/boot/grub2` system, is caught by its
  own arm first).
- **A Mandriva-lineage system.** It carries rc.sysinit's boot-time
  rewrites (`/etc/mtab` as a regular file, `/etc/issue`, speedboot). The
  BackShot1 manifest and the boot-state checks have to learn them.
- **Popularity.** It is DistroWatch rank 27 over six months (33 over 12
  months, 40 over 4 weeks), and still has an active user base with its own
  magazine.

## What PCLinuxOS is (as far as bko cares)

| | PCLinuxOS (2026.11 ISOs, rolling repo) | Nearest supported system |
|---|---|---|
| Release | **Verified.** Rolling repos, x86_64 only, with an ISO refresh every 1-4 months (*inferred* from the dates). Current ISOs, all named 2026.11 (MIRROR `iso/`): `pclinuxos64-kde-2026.11.iso` (4.0 GiB) and `-kde-darkstar-` (3.1 GiB), both dated 2026-11-21; `-MATE-` (3.7 GiB) and `-XFCE-` (3.5 GiB), both dated 2026-11-01. Checksums are **MD5 only**, one file per ISO named without `.iso` (`iso/pclinuxos64-kde-2026.11.md5sum`; `...iso.md5sum` is a 404; KDE's value is in Decision 6), with no SHA256 file and no GPG signature | Arch (rolling), Slackware -current |
| os-release | **Verified** (pclinuxos-release-2026): `ID=pclinuxos`, `ID_LIKE="mandriva"`, `VERSION_ID=2026`. Also `/etc/pclinuxos-release` ("PCLinuxOS release 2026 (PCLinuxOS) for x86_64"), plus Mandriva-compatible `mandriva-release`, `mandrake-release`, `redhat-release` and `/etc/sysconfig/system`. *Inferred:* VERSION_ID is the release package's year, so it changes to 2027 through an ordinary update | Arch (no version that means a stack) |
| Packages | **Verified.** rpm 6.1.0 with the **ndb** backend in **`/var/lib/rpm`** (`Packages.db`), not `/usr/lib/sysimage`. **DNF5**, with the "DNF Package Manager" GUI, has been the default since the 2025.08 ISOs (https://pclosmag.com/html/Issues/202510/page01.html, which gives no version; 5.4.8 is the repodata's). apt-rpm and Synaptic are retired (https://pclosmag.com/html/Issues/202605/page05.html, https://pclosmag.com/html/Issues/202606/page01.html); the apt pkglists stopped on 2026-08-06, and the 2026.11 ISO has neither. Repos are in **`/etc/repo.d/default.repo`** (not yum.repos.d): `type=rpm-md`, **`gpgcheck=0`**, sections `x86_64`, `kde6`, `mate`, `xfce4` on. Updates are `dnf check-update; dnf distro-sync`. Release tags look like `-1pclos2026`. Library packages use Mandriva's `lib64` names (`lib64nss-mdns2`) | Fedora (rpm + dnf), with other names and no signatures |
| Init | **Verified.** sysvinit 3.18, initscripts 9.27 Mandriva-style (`/etc/rc.d/rc.sysinit`, `/etc/rc.d/init.d/*`, `mandrake_everytime`, `mandrake_firstime`), chkconfig 1.3.37 (`chkconfig X on`, `service X start`). udev 262 without systemd, ConsoleKit 1.2.7, **no elogind** | Devuan and MX (sysvinit), but with chkconfig instead of `update-rc.d` |
| Kernel | **Verified.** The version is in the package *name*: `kernel-6.18.65-pclos1` installs `/boot/vmlinuz-6.18.65-pclos1`, `System.map-`, `config-`. `kernel-latest` is a virtual package, and the 6.12.y series is the alternative. The ISO has 6.18.60, the repo 6.18.65. The kernel's `%post` runs `depmod` and then `/sbin/installkernel V` | none (dnf's installonly is never involved) |
| Initramfs | **Verified** (mkinitrd-6.0.93 source). Red Hat **mkinitrd 6.0.93**, nash-based (the image's init is `#!/bin/nash`; nash is on the ISO). It has no dracut code. Its config is `/etc/sysconfig/mkinitrd`. The chain on a kernel install is `installkernel` → `bootloader-config --kernel-version V --initrd-options "$INITRDOPTS"` → drakx's `bootloader::mkinitrd` (`bootloader.pm:129`) → `mkinitrd -f /boot/initrd-V.img V [opts]`, options last, then `add_boot_splash` (a no-op unless the entry sets `vga`). `INITRDOPTS` (`/etc/sysconfig/installkernel`) is `--with nvme_rdma --with nvme_fcloop --with nvme_loop --with nvmet_fc --with nvmet_rdma --with nvmet --with nvmem_core`: NVMe-over-fabrics and target modules, and nvmem_core, which isn't NVMe. **The PCIe `nvme` and `nvme_core` drivers are not in it**; they land in an image only if mkinitrd's probe of `/sys` finds the root on them. A whole rebuild is `bootloader-config --action rebuild-initrds`, which the installer uses: `bootloader::rebuild_initrds` (`bootloader.pm:918`) loops over the grub entries, renames each image to `initrd-V.img.old` (`rebuild_initrd`, :176) and calls `bootloader::mkinitrd` with *the entry's own* `initrd_options`, not INITRDOPTS. It writes no loader configuration. So the installer's images are built without INITRDOPTS, a later kernel's with them, and an installed system keeps `initrd-V.img.old` files. The image is a newc cpio, gzip -9 (mkinitrd:1903-1906, `compress=1` at :81). dracut 112 is in the repo but **not on the ISO** | none. Slackware's `mkinitrd` is another program with the same name |
| LUKS in the initrd | **Verified** (mkinitrd ~549-590, ~1763-1800). It finds dm-crypt slaves of the root and checks them with `cryptsetup isLuks`. It emits `cryptsetup luksOpen` with `plymouth ask-for-password`, taking names from `/etc/crypttab`. It handles crypt on partitions, RAID and LVM. **Key files are ignored for the root**: it always asks. Non-root crypttab entries are opened by rc.sysinit's `init_crypto`, which does honour key files. cryptsetup 2.6.1 is on the ISO; lvm2 is in the repo only | Alpine mkinitfs (asks; family A) |
| Boot loader | **Verified.** grub2 and grub2-efi 2.14 (both on the ISO), os-prober, efibootmgr 0.12. Files are in `/boot/grub2/` (`grub.cfg`, `custom.cfg`), `/etc/default/grub`, `/etc/grub.d/` (`00_header` ... `41_custom`). **No shim, no mokutil**, and the live `bootx64.efi` is unsigned (`grub2-mkimage`). The ESP is mounted at **`/boot/EFI`**: mylive-install's `frontend/partitioning.py:26` (`EFI_MOUNT_POINT`), the fstab line it writes (`installer.py:570`, `/boot/EFI  vfat defaults 0 1`) and `configs/distribution/pclinuxos.yaml`'s `additional_efi_mountpoints`. drakx agrees: the EFI image is at **`/boot/EFI/EFI/pclinuxos/grubx64.efi`** (drakxtools-backend `bootloader.pm:263`), so the bootloader-id is `pclinuxos`. The magazine's installer article (https://pclosmag.com/html/Issues/202309/page06.html) says "/boot/efi" in its prose; the code is taken over it. `/boot/EFI` is a directory on the ext4 root, so its case matters; only the names inside the ESP are case-insensitive. *Inferred:* this grub2 is patched to look for `/boot/EFI` (the installer passes no `--efi-directory`, and upstream grub-install probes `BOOTDIR/efi`, then `BOOTDIR`), and Secure Boot has to be off. *Check:* both, and whether an `EFI/BOOT/BOOTX64.EFI` fallback is written | Fedora's grub2 layout, without its shim |
| Install | **Verified** (mylive-install 1.0.26; https://pclosmag.com/html/Issues/202309/page06.html). A GTK fork of the LMDE live-installer, run from the live session. There is **no unattended mode** (`main.py`: `--test --expert --version --welcome --fullscreen`; `tui_interface.py` exists but nothing calls it). **No LUKS, no LVM**: `config.yaml` has them commented out, and `initramfs_systems/initrd.yaml` says "luks not supported yet". Automated layout: ESP vfat 300 MB at `/boot/EFI`, an optional ext4 `/boot` of 1 GB, swap of 1024 MB, ext4 `/`, and an ext4 `/home` only on disks of 100 GB or more. Manual mode offers every `mkfs.*` on the live system: ext4, xfs, btrfs, f2fs, jfs, reiserfs. The user's password becomes root's too | Mint and MX (live copy then chroot steps) |
| What the installer does | **Verified** (`installer.py`; the distribution's commands are `configs/distribution/pclinuxos.yaml`'s `grub_prepare`, `grub_installation_efi`, `grub_installation_legacy` and `rebuild_initrds`), in order: (1) `rsync --archive --no-D --acls --hard-links --xattrs /union/* /target/` (`installer.py:123-146`), excluding `dev/* proc/* sys/* tmp/* run/* var/run/user/* mnt/* media/* lost+found source target` plus config's `exclude_dirs` (default `data/user/*`; var/run is on disk here, its comment says), with `unsquashfs /dev/loop0` as the fallback; (2) bind-mount dev, proc, sys, run, efivars; (3) fstab by `UUID=`; (4) `/etc/hostname`, `/etc/hosts`; (5) `locale.gen`; (6) `/etc/sysconfig/clock`; (7) `00-keyboard.conf`; (8) `scripts/cleanup.sh` (`grub_prepare`), which removes `/var/lib/speedboot/status` and writes `HOSTNAME=` in `/etc/sysconfig/network`; (9) `chroot /target grub2-install`, with no arguments on EFI and the disk on BIOS; (10) `/boot/grub2/install.sh`; (11) `resume=` in `/etc/default/grub`; (12) `grub2-mkconfig -o /boot/grub2/grub.cfg`, then `do_configure_grub` runs it again until `do_check_grub` passes, up to 5 retries (`installer.py:889-901`); (13) `bootloader-config --action rebuild-initrds`; (14) `chkconfig X on` for avahi-daemon, atd, cups, cups-browsed, crond, rsyslog, xinetd, vnstat, update-notifier | `make-from-live.sh`'s steps (Mint, MX and the rest) |
| Live media | **Verified** (mylivecd's `linuxrc`). `/livecd.sqfs` at the ISO root. BIOS boots through `isolinux/`, EFI through `isolinux/efiboot.img` and `/EFI/BOOT/bootx64.efi`. At run time it mounts `/initrd/cdrom` and `/initrd/loopfs`, with an overlay at `/union`. Boot options include `livecd=` and `unionfs=no` | MX's `/antiX/linuxfs` |
| sshd | **Verified.** openssh-server 9.8p1 is in the repo but **not on the ISO** (only openssh and openssh-clients are), with `/etc/rc.d/init.d/sshd` and `sshd_config.d/50-pclinuxos.conf` | Slackware (needs adding) |
| Network, mDNS | **Verified.** NetworkManager 1.58 (an init script), the legacy `network` service, dhcpcd. initscripts' `network-scripts` honour `HWADDR=` in `ifcfg-*` files. avahi 0.8 and `lib64nss-mdns2` are on the ISO, and the installer turns avahi-daemon on. ufw 0.36 is on the ISO. *Check:* whether `nsswitch.conf` has `mdns4_minimal`, whether ufw is on, and whether the image writes MAC-keyed names (`70-persistent-net.rules`, `ifcfg-*` with `HWADDR=`) | MX (NetworkManager, ufw) |
| Boot-time rewrites | **Verified** (rc.sysinit, mandrake_everytime). **`/etc/mtab` is a regular file**, truncated and refilled by `mount -f` on every boot. `/etc/issue` and `issue.net` are rewritten when `REWRITEISSUE=rewrite`. `/var/lib/random-seed`, `utmp`, `wtmp` and `btmp`, `/var/lock` and `/var/run` are rewritten too. `/var/lib/speedboot/status` tracks the booted uname. `/etc/rc.d/init.d/rpmdb-rc` (off by default) would rebuild the rpmdb and rewrite `/usr/lib/rpm/macros`, which rpm's own `%post` edits with `sed -i`, so `rpm -V rpm` already shows it as changed | Slackware's `rc.M` list in bs1-manifest |
| MAC | **Verified.** No AppArmor, and a SELinux library without a policy. *Inferred:* no MAC is active | Arch |
| File systems | **Verified** (ISO list). e2fsprogs 1.47.4, xfsprogs 7.2.1, btrfs-progs 5.9 (old), f2fs-tools, jfsutils, reiserfsprogs, mdadm 3.4, dosfstools 3.0.28 | |

## What already works, and what doesn't

**bko-holt `dump`:**

- **osrel.** `SUPPORTED` (`holt/src/osrel.rs:166`) has no row, and
  `supported()` ignores `ID_LIKE`, so dump refuses with "this is PCLinuxOS
  and bko-holt dump supports only ...". New: `("pclinuxos", "*",
  "PCLinuxOS")` (Decision 4), with a test built from the real os-release.
  Refusal tests are kept for `mageia`, `openmandriva` and an unknown ID
  that has `ID_LIKE=mandriva`. The row's comment says why the version is
  `*`.
- **Packages.** `installed_packages()` (`boot/packages.rs:360`) already
  reads `/var/lib/rpm` through `rpm -qa` on the live source. *Check:* that
  rpm 6 with ndb answers the query form `rpm_packages()` uses
  (`packages.rs:108`).
  - `PACKAGES` (`packages.rs:403`) gains `nash`, `bootloader-utils`,
    `drakxtools-backend` and `grub2-efi`: only names no other guest has, so
    no other fingerprint changes (the file's rule, in its BigLinux and
    ARM64 comments; PC2 checks each against the corpus). It already has
    `grub2`, `efibootmgr` (:419) and `mkinitrd` (:564, Slackware's).
    `dnf` is left out: it would add a `boot.packages` key to every Fedora,
    Rocky and Qubes dump.
  - Kernels are named `kernel-VERSION-pclosN`, so they are matched by a
    pattern, not a name. *Check* how `PACKAGES` matches today. If it only
    matches exact names, the kernel packages are left out of `boot.packages`
    and are read from `/boot` instead, as now.
  - `arch.rs:89-105` gains `grub2-efi` (EFI) and `grub2` (BIOS, because
    PCLinuxOS's grub2 carries i386-pc) for PCLinuxOS only, so that
    `loader.package` isn't null. This only affects how the dump describes
    the loader.
- **The initramfs generator.** `initramfs_json_esps`
  (`boot.rs:1028-1111`) finds none, because neither mkinitcpio, booster,
  dracut nor initramfs-tools is installed. New: `mkinitrd-rh` (Decision 2
  names it). It must never be called plain `mkinitrd`: that is Slackware's
  generator (the slackware15-uefi corpus dump already lists `mkinitrd` in
  `boot.packages`), load's Slackware step matches it (`commands.rs:9587ff`),
  and `rewrite.rs:864` sends `"mkinitrd" | "geninitrd"` to
  `check_slackware_initrd`.
  - **When:** the `mkinitrd` package is installed and `/sbin/mkinitrd` is
    the shell script that writes a nash init (its first lines, *check*).
    The test comes after the `is_slackware` dispatch (`boot.rs:1037-1043`),
    whose package is also named `mkinitrd`, and before dracut, because a
    user can install dracut from the repo and still boot mkinitrd's images
    (`bootloader-config` calls `mkinitrd` by name).
  - **Evidence:** `/etc/sysconfig/mkinitrd`, `INITRDOPTS` from
    `/etc/sysconfig/installkernel`, and each kernel's image.
  - **If dracut is also installed:** the evidence says which tool made the
    images (a nash init or a dracut one), and holt goes by that.
- **Kernel images.** `kernels_json` (`boot.rs:817-821`) knows
  `initrd.img-V`, `initramfs-V[.img]` and `booster.img-V`. New:
  `initrd-V.img`, so `initrd_bytes` isn't null. The `.old` list
  (`boot.rs:766-778`) has no `initrd-` prefix, so the `initrd-V.img.old`
  files that rebuild-initrds leaves would be read as kernels' files; it
  gains `initrd-`. The `/boot/vmlinuz` and
  `/boot/initrd.img` links that installkernel makes (*check*) are already
  recorded (`boot.rs:858-866`).
- **The loader.** `grub_json` sets `boot.grub.dir` = `/boot/grub2`, because
  `/boot/grub` is absent. EFI on the A100: BootCurrent gives
  `\EFI\pclinuxos\grubx64.efi`. EFI on the `pclinuxos` guest, which has no
  NVRAM entry of its own (see Guests): BootCurrent is the firmware's entry
  for the disk, through `EFI/BOOT/BOOTX64.EFI`, a copy of `grubx64.efi`.
  Either way `via_shim` is false and the ESP is `mounted_at` `/boot/EFI`
  from fstab. BIOS: GRUB's boot.img in the MBR. All of this works today
  (*check* on the guests' dumps).
- **Redaction.** The installer's default hostname (*check*: probably
  `localhost` or `pclinuxos`) goes into `GENERIC_HOSTS`
  (`redact.rs:185`, whose fixed count goes up by one), and the live user's
  name too if it lands in the dump.

**bko-holt `load`:**

- **osrel at load** (`load/layout.rs:244`): the same row covers it.
- **The initramfs, a new arm for `mkinitrd-rh`** in `initramfs()`
  (`commands.rs:7280`), before the `_` fallback.
  - For each `/lib/modules/K` with a `kernel/` directory that had an image,
    load runs `chroot T sh -c '. /etc/sysconfig/installkernel;
    mkinitrd -f /boot/initrd-K.img K $INITRDOPTS'`, options last as
    `bootloader::mkinitrd` passes them. That is the image a kernel update
    makes (installkernel's chain). It is a choice, not what the installer
    does: the installer's rebuild-initrds passes each grub entry's own
    `initrd_options` and not INITRDOPTS (Decision 2). Load doesn't run
    `bootloader-config --action rebuild-initrds`: it writes no loader
    configuration, but it picks kernels from the grub entries, not from
    `/lib/modules`, goes through drakx's Perl, and leaves an `.old` copy
    of each image.
  - If PC1 or the PC8 rehearsal finds that the probe leaves `nvme` out on
    an NVMe root, the command gains `--with nvme` (`nvme_core` comes in as
    its dependency, *check*).
  - mkinitrd reads the root's type and device from the target's
    `/etc/fstab`, so it must run after fstab is rewritten. *Check* that
    the boot stage's order already guarantees this. It does for dracut on
    `--reformat`.
  - It finds the root's storage drivers from `/sys` in the chroot. That
    is the target machine, as for dracut's host-only images.
- **The fallback.** The `_` arm (`commands.rs:7332`) runs
  `update-initramfs` whatever the distribution. New: it runs only when
  `update-initramfs` exists in the target. Otherwise the plan has a
  problem ("no initramfs generator bko-holt knows; the restored images
  stay"), not a boot stage that fails half-way. That change is for every
  system, and the existing corpus plans show whether it changes any of
  them (they mustn't change).
- **Readback.** `rewrite --check` lists `initrd-*` images
  (`initramfs_images`, `load/rewrite.rs:931-934`) and reads their modules
  with the generator's lister: `lsinitrd`, `lsinitcpio` or `lsinitramfs`
  (`rewrite.rs:874-877`), or `gzip -dc | cpio -it` for Slackware's
  (`check_slackware_initrd`, `rewrite.rs:1014ff`). New for `mkinitrd-rh`:
  the same `gzip -dc | cpio -it` with the restored system's tools, since
  mkinitrd 6 writes a gzip newc cpio (**Verified**, mkinitrd:1903-1906).
  The root's filesystem module and the controller's driver (on the A100,
  `nvme` and `nvme_core`) must be in it. `initramfs_images` matches any
  `initrd-*`, so it skips `*.old` too.
- **The `/boot/grub2` UEFI arm** (`commands.rs:6387`) gets a guard keyed
  on `os.id == "pclinuxos"` (an `is_pclinuxos()`, like `is_qubes()`), so
  PCLinuxOS alone falls through to the generic arm. Shim systems (Fedora,
  Rocky, openSUSE) keep today's behaviour: keep the ESP's images and remake
  only grub.cfg. The guard is not `boot.loader.via_shim`: that says how the
  source *booted* (a boot file named SHIM, MOK variables, or a fallback
  copy of shim; `boot.rs:1209-1313`, :1410), not that shim is installed. A
  Fedora machine whose firmware entry points at `grubx64.efi` directly,
  with Secure Boot off and no MOK variables, has `via_shim=false`, and a
  `via_shim` guard would send it to `grub2-install --target=x86_64-efi`,
  which the arm's comment says Fedora refuses. Every existing corpus plan
  must stay unchanged, and a scenario covers that Fedora case.
- **The generic UEFI GRUB arm** (`commands.rs:6420-6433`) hardcodes
  `grub-install`. New: the name comes from `grub_tool("install")`
  (`commands.rs:7224`), so a `/boot/grub2` system gets `grub2-install
  --target=x86_64-efi --efi-directory=/boot/EFI --bootloader-id=pclinuxos
  --no-nvram`. `--efi-directory` comes from `mounted_at`, and the id from
  `bootloader_id()` (`commands.rs:7958`). `efi_entries()` then makes the
  NVRAM entry, as it does everywhere. `--removable` copies the image
  itself, as for non-Debian GRUB (`commands.rs:7669`).
- **BIOS.** `grub_bios_install` (`commands.rs:7203`) already runs
  `grub2-install --target=i386-pc DISK` for `/boot/grub2`.
- **grub.cfg.** `update_grub()` (`commands.rs:6979`) already runs
  `grub2-mkconfig -o /boot/grub2/grub.cfg` with os-prober off, for any ID
  not in `DEBIAN_GRUB_IDS`. PCLinuxOS stays out of that list (it has an
  `update-grub` too, but `grub2-mkconfig` is what the installer runs).
- **Init.** Nothing is needed. holt restores sysvinit systems (Devuan, MX,
  Slackware) with no init code.
- **LUKS, LVM and md roots** (not from the installer, Decision 3). If a
  source has a LUKS root, mkinitrd in the chroot maps the open dm-crypt
  device back to a crypttab name. *Check* that the name the rescue opens
  it under is the crypttab name, or that mkinitrd's `UUID=${name#luks-}`
  fallback lands on the right UUID. mkinitrd also assembles LVM and md
  roots (lvm2 is in the repo, mdadm 3.4 on the ISO), and no guest proves
  either. Until a scenario and a guest prove them, a root on LUKS, LVM or
  md under `mkinitrd-rh` is a plan problem, refused by name, not restored
  half-right: "PCLinuxOS's root is on LUKS (or LVM, or md RAID); bko-holt
  has no tested way to rebuild a mkinitrd (nash) image that opens it, so
  it won't restore this machine". Each gets a scenario. Other mount points
  on LVM or md (not the root) are brought up by rc.sysinit, not the
  initrd, and are not refused.
- **The rescue image:** no change ([[one-kernel-rescue]]). mkinitrd, nash,
  grub2-install and efibootmgr run from the restored system in the chroot.

**Test tooling:**

- `testvm/update/pkgset.sh:177-189` takes the rpm branch when dnf is
  present and prints `base pm rpm` (:184-187), so it works as it is (the
  apt-rpm worry in the briefing no longer applies). `fingerprint.py`'s
  `LINUX_KINDS` (`update/fingerprint.py:99`) is keyed by that kind and
  already has `"rpm"`, so fingerprint.py needs no change. *Check* on PC3's
  `--try`.
- `update/in-guest.sh`'s `Linux.dnf` section (`in-guest.sh:1321-1340`)
  runs Fedora's steps: `dnf -y upgrade --refresh`, plus Fedora-specific
  `mcelog` and `tpm2-tools` lines. New: a `# t2-section: Linux.pclos`
  section, matched by `ID=pclinuxos` before the dnf one:
  - `dnf -y check-update; dnf -y distro-sync`, as PCLinuxOS says to run
    updates. `dnf check-update` exits 100 when updates exist, so it runs
    outside `set -e` and `|| exit 1`, with 0 and 100 both taken as success;
  - `kernel-latest`'s newest kernel installed by name;
  - kernels beyond the newest two removed. They are separate package
    names (`kernel-V-pclosN`), so dnf's installonly limit never removes
    them. The section sorts them by version (`sort -V` on V), keeps the
    newest two and never removes the running one (`uname -r`);
  - a check that each remaining `vmlinuz-K` has an `initrd-K.img`.
- `compare/boot-state.sh:40-43` picks sysv-rc by `/etc/rcS.d` and
  Slackware's BSD-style rc (`slackware-rc`) by `rc.M` and
  `slackware-version`; neither fits. ht1.sh's BOOTSTATE (`ht1.sh:637-667`)
  has a `slackware-rc` arm (:661-664) and nothing for Mandriva's rc.d.
  New, both places, modelled on `slackware-rc`: an `rc.d-chkconfig` arm,
  ahead of the sysv-rc test, chosen when `/etc/rc.d/rc.sysinit` and
  `/etc/pclinuxos-release` exist and `chkconfig` does too. It keeps
  BOOTSTATE's contract:
  - line 1, `rc.d-chkconfig runlevel N`;
  - then, sorted, one `down NAME` per service `chkconfig --list` has on at
    that runlevel whose `/var/lock/subsys/NAME` is missing (or, for a
    service that keeps none, whose daemon `pgrep -x` doesn't find).

  A restored machine may have no line its source lacked.

## Guests

Two guests (Decision 1), both from the KDE ISO (Decision 6), and both
booted to runlevel 3 with a ttyS0 getty. The desktop stays installed.

| | `pclinuxos` | `pclinuxos-bios` |
|---|---|---|
| Firmware, table | UEFI (OVMF, no Secure Boot: `vm-run-options` `--uefi --no-secure-boot`), GPT | BIOS, MBR |
| Partitions | The installer's Automated layout: ESP vfat 300 MB at **`/boot/EFI`**, swap 1024 MB, `/` ext4 the rest (the disk is under 100 GB, so no `/home`) | What Manual mode gives a user who asks for it: `/boot` ext4 1 GiB, swap 1024 MB, `/` **XFS** the rest |
| Loader | grub2-efi in `EFI/pclinuxos/` and again at the fallback `EFI/BOOT/BOOTX64.EFI`, with **no NVRAM entry**: the install VM boots by BIOS, so the guest boots through the firmware's entry for the disk, as `gentoo` and `slackware15-uefi` do | grub2 i386-pc in the MBR |
| Initramfs | mkinitrd-rh, `initrd-K.img` (ext4, virtio_blk as the probe finds it; INITRDOPTS adds only NVMe-oF and target modules) | mkinitrd-rh, with XFS's module in the image |
| Disk, RAM | 32 GiB qcow2 (`DISK_SIZE=32G` in the case; make-bootstrap.sh's default is 16 GiB, :115), 4 GiB RAM | the same |

The first is a default install. The second adds what's left: BIOS GRUB, a
separate `/boot` and a root module that is a loadable module in the image.

**How they are built** (Decision 1):

1. **Recommended: one install script that replays `installer.py`, used
   for the guests and the A100 alike.**
   - The script is `testvm/pclinuxos/install.sh TARGET_DISK LAYOUT
     SQFS ...`. It does `installer.py`'s steps 1-14 (in the table above),
     in the same order and with the same files:
     - it unsquashes `livecd.sqfs` onto the target, as the installer's
       fallback does, then empties the installer's own exclude list
       (`installer.py:125-130`, copied as it is, with `data/user/*`), so the
       tree is what the rsync path leaves;
     - it writes fstab by UUID, `/etc/hostname`, `/etc/hosts`,
       `/etc/sysconfig/clock`, `/etc/sysconfig/network`'s `HOSTNAME=` and
       the keyboard file;
     - it runs `cleanup.sh` from the ISO's own mylive-install, unchanged;
     - it runs `grub2-install`, `grub2-mkconfig` and `bootloader-config
       --action rebuild-initrds` in the chroot;
     - it runs the same `chkconfig ... on` list.
   - **The EFI step differs by where it runs.** On the A100 (from the
     stick, with efivarfs bound) it is the installer's bare
     `grub2-install`, which writes the NVRAM entry. Under make-bootstrap
     the install VM is `-machine pc` without OVMF (`make-bootstrap.sh:
     504-512`): no `/sys/firmware/efi`, so a bare `grub2-install` would
     pick i386-pc or fail. There `install.sh` runs `grub2-install
     --target=x86_64-efi --efi-directory=/boot/EFI --bootloader-id=pclinuxos
     --no-nvram`, then the same with `--removable` for the fallback, as
     `bootstrap/gentoo.sh:262-281` and `chimera.sh:112-115` do. (Running
     the install under OVMF instead, as make-bootstrap's arm64 path does at
     :496-502, and keeping `uefi-vars.fd`, would give the guest its NVRAM
     entry, but it is a new x86 path in make-bootstrap for one guest.)
   - Then it does what every bko guest gets:
     - openssh-server from pinned RPMs (Decision 5), and `chkconfig sshd
       on`;
     - `initdefault` 3 and an `s0:2345:respawn:/sbin/agetty ttyS0 115200
       vt100` line in `/etc/inittab`;
     - `console=ttyS0,115200n8` in `GRUB_CMDLINE_LINUX`;
     - root's generated password and key, and the `tester` user;
     - `BACKOTTER-INSTALL-OK` at the end.
   - It is run by `testvm/make-bootstrap.sh pclinuxos` and `pclinuxos-bios`
     from the Arch live ISO under serial control, as Slackware's and
     Gentoo's are, through a `testvm/bootstrap/pclinuxos.sh` that calls
     `testvm/pclinuxos/install.sh`, with the PCLinuxOS ISO on a second CD.
     The build runs mkinitrd inside a VM, so its probe of `/sys` sees a
     virtio disk, as the guest has.
   - PC1 also does **one GUI install by hand** of each layout in QEMU,
     through mylive-install. It diffs both trees (file list, modes, the
     files steps 3-14 write) against the script's result and records the
     differences here, as Slackware's S1 did with `liloconfig`. That GUI
     image is thrown away afterwards.
2. **Extend `testvm/make-from-live.sh`**, the repo's live-ISO replay
   (Mint, Zorin, Pop!_OS, elementary, neon, MX): it unsquashes the live
   image onto a loop disk on the host and runs the installer's chroot
   steps there. It is the nearest model, but not recommended here:
   - mkinitrd 6 probes `/sys` in the chroot, which on the build host is a
     loop device, not the guest's virtio disk, so the images would be built
     for the wrong storage;
   - its DISTRO cases are casper and antiX live systems with apt, and its
     extra packages come from the online archive;
   - it can't run from the rescue stick, so the A100 would need `install.sh`
     anyway.
3. **Drive mylive-install's own engine in the live session.** The live
   session is booted with `console=ttyS0`. Then, from a serial login,
   `installer.py`'s engine runs headless with a `Setup` object built by a
   fetched Python script, as `make-pikaos.sh` runs pikainstall. This is
   the real installer. But it depends on a serial login existing in the
   live session (*check*), and on an internal API that changes with each
   mylive-install release. It also can't run from the rescue stick, so the
   A100 would need path 1 anyway. est. +0.5 d on the 3.7 d total.
4. **QEMU monitor `sendkey` through the GTK installer** (Guix's
   `console-type.py` pattern). This is the most fragile option and isn't
   recommended.

Each guest gets:

- `rebuild-one.sh` lines (recipe and ISO list, `rebuild-one.sh:188-191`,
  `259`);
- the ISO, its `.md5sum` and the openssh RPMs in
  `/bkoscratch/rebuild-inputs/pclinuxos/` on each host that builds, with
  SHA-256s pinned in the script;
- `holttest1d/ram-sizes.tsv` rows;
- a vm-store publish;
- a GuestTest1 grade.

Builds run in `/mnt/bko-ram` under `cmp-run.sh` reservations, picked by
`testvm/pick-host` ([[split-chopin-cinqcent]], [[work-in-ram]]).

**vm-update:** in-guest.sh's new `Linux.pclos` section (see "Test
tooling" above) and the `vm-update` header; fingerprint.py needs nothing.
Updates run in a `--keep` overlay and ship that overlay
([[update-guests-not-copy]]), if Decision 10 says to take them.
GuestTest2 gets:

- a `K-pclos` rule in `t2lib/guest_judge.py`: each `vmlinuz-K` has an
  `initrd-K.img` whose init is nash, the newest kernel is the default
  entry in grub.cfg, and there are at most two kernels (`"K-pclos": 2` in
  `KERNELS_MAX`, `guest_judge.py:28-31`);
- an `S-dnf-pclos` rule: `/etc/repo.d/default.repo` sections and no
  `apt`;
- the input both read, in `test2/guest/check-image.sh`, which is all the
  K-* and S-* rules see: `has_rule S-dnf-pclos && emit
  /etc/repo.d/default.repo` beside the S-dnf line (:17), and for K-pclos
  each `vmlinuz-K` with its `initrd-K.img` and the image's first init
  bytes, marking one without an image `no-initrd`, as K-anduinos and
  K-cbm do;
- `guest/known-lines.json#pclos` lines from PC3's `--try` logs.

## Tests

**SticTest1** (`testvm/stictest1/pclinuxos`, `pclinuxos-bios`): each
directory has its own identical copies of `inner.sh`, `fixture.sh`,
`judge.py` and `check.sh`, plus the helpers they import, as `pikaos4` and
`pikaos4-luks` each carry `fatattr.py`, `gpt.py` and `bkoshim.py`.
check-manifests requires `testvm/stictest1/GUEST/**` in each check's
harness (R5 (b), `test2/stic/check-manifests.py:17`). The scripts read the
layout from the guest, as Gentoo's and PikaOS's do.

- `pclinuxos`: an ext4 fixture plus an ESP with chimera-uefi's
  `fatattr.py`. The ESP is mounted at `/boot/EFI`, a directory on the ext4
  root whose case matters; only the ESP's own `EFI/` is on FAT.
- `pclinuxos-bios`: gentoo's XFS fixture with an ext4 `/boot`.
- Real examples in both:
  - `/etc/mtab` as a regular file;
  - `/etc/rc.d/rc[0-6].d` links;
  - `/var/lib/rpm/Packages.db`;
  - `/boot/initrd-K.img`;
  - `/boot/grub2/i386-pc` or `EFI/pclinuxos`;
  - set-id binaries and shadow.
- Both archivers, on both hosts. `stic/manifests.json`, `limits.json`, and
  `check-manifests.py`'s count goes up by two.

**HoltTest1:** both guests join **1Obscure** (Decision 7). That means:

- `classes.json`'s `H.Obscure` and `uefi` (for `pclinuxos`);
- the holttest1d README's 1Obscure row;
- `batches/holttest1all.batch`'s `h1obscure` line, which already lists
  25 guests (:13), and the `estimates.tsv` rows for the two new runs; PC6
  records HoltTest1All's added time there (est. +2 × 25 min of queue).

Its checks:

- **Boot state:** the new `rc.d-chkconfig` boot-state arm.
- **What judge.py allows,** because the boot stage rewrites them:
  - `boot/initrd-*.img` (new content);
  - on `pclinuxos`, the ESP's `EFI/pclinuxos/grubx64.efi`
    (`grub2-install` writes it anew);
  - `boot/grub2/grub.cfg`;
  - `boot/grub2/i386-pc/*` and `core.img` on BIOS;
  - `var/log/bko-holt`;
  - the rc.sysinit list: `etc/mtab`, `etc/issue*`, `var/lib/random-seed`,
    `var/lib/speedboot/status`.
- **Boot alone:** each restored disk boots by itself. The `pclinuxos`
  source had no NVRAM entry of its own (see Guests), so its restored disk
  boots as the source did, through the ESP's fallback loader, which load
  renews with `--removable`, and ht1.sh's NVBOOT records the entry it
  booted through. (bs1-qemu.py treats such a source the same way: a fresh
  store and no entry of its own, its `manjaro` case, `bs1-qemu.py:15-25`.)
  On
  `pclinuxos`, the restored `grubx64.efi`'s embedded prefix (and its
  fallback copy's) must name the restored `/boot`'s filesystem. That is
  the test that the `is_pclinuxos()` guard is right.
- **Reformat:** one 1Obscure case runs `pclinuxos-bios` with
  `--reformat /=ext4`. It proves that mkinitrd reads the rewritten fstab
  and puts ext4's module in the image.

**Test2:**

- **Golden plans:** `holt/tests/corpus/pclinuxos/` and `pclinuxos-bios/`
  (`src.json`, `plan.txt`, `plan.json`, `index.tsv` rows), from live dumps
  of a PASS HoltTest1 run (`collect-dumps.sh --sources`), following the
  corpus README's "Adding a source".
- **Registrations:**
  - `holt/spec.json` (`{"kind": "linux", "firmware": ...}`; `pclinuxos`
    carries `"harness": ["testvm/holttest1d/nvboot-efivars.sh"]`, as the
    UEFI Linux entries do at `holt/spec.json:157-161`);
  - `guest/spec.json` (pm `dnf-pclos`, rules `S-dnf-pclos`, `L-grub`,
    `K-pclos`, `B-lin`, `M-lin`; the harness is `make-bootstrap.sh`,
    `testvm/bootstrap/**` (as the slackware15 entry has it,
    `guest/spec.json:2182ff`), `testvm/pclinuxos/**`,
    `in-guest.sh#Linux.pclos`, `check-image.sh`, `boot-state.sh`,
    `new-guest.sh`);
  - `estimates.tsv`;
  - `t2lib/plan.py`'s counts (+2 sources, +2 guests);
  - `tests/fixtures/p2/world.py`;
  - the re-blessed p2 goldens.
- **Done when:**
  - two corpus builds give the same sha256;
  - `leakscan.py` finds 0 on both hosts and on the Mac;
  - no other plan changes. This matters most for the `/boot/grub2` arm's
    guard and the fallback change: Fedora's, Rocky's and the openSUSE plans must stay
    byte-identical;
  - Test2's own tests pass.

**Scenarios** (`holt/src/scenarios.rs`): fake trees for both guests, plus
these cases:

- a PCLinuxOS tree with dracut installed beside mkinitrd and nash images
  (it must still say `mkinitrd-rh`);
- a PCLinuxOS UEFI tree (it must run `grub2-install`);
- a Fedora shim tree (its plan must be unchanged);
- a Fedora tree that booted `grubx64.efi` straight from its firmware
  entry, with no MOK variables, so `via_shim=false` (its plan must be
  unchanged too: the guard is the os.id, not `via_shim`);
- a PCLinuxOS tree with `initrd-K.img.old` files (they are neither
  kernels' images nor read back);
- an RPM tree with no generator (a plan problem, not `update-initramfs`);
- PCLinuxOS trees with a LUKS, an LVM and an md root (each refused by
  name, Decision 3).

## BackShot1 on the A100 (PC8, optional)

The A100 is reinstalled with PCLinuxOS from the rescue stick, with no GUI
and nothing typed at the console. It uses the same `install.sh` as the
guests, so the guests rehearse the A100's install. Secure Boot must be off
in the A100's firmware, which the user sets when the run is due, as for
KDE neon. PCLinuxOS has no shim.

    sudo bash /mnt/bs1-backup/backshot1/pclinuxos/pclinuxos-bootstrap.sh SERIAL \
        /mnt/bs1-backup/backshot1/pclinuxos/pclinuxos64-kde-2026.11.iso \
        /mnt/bs1-backup/backshot1/keys thinkpad

**What `testvm/realworld/pclinuxos-bootstrap.sh` does**, modelled on
`neon-bootstrap.sh`:

1. It checks the keys (`authorized_keys`, `ssh_host_ed25519_key{,.pub}`)
   and the tools: `unsquashfs` or a squashfs loop mount on the stick
   (*check* which the rescue image has).
2. It checks the ISO's SHA-256 against the value pinned in the script, and
   the pinned openssh RPMs in `backshot1/pclinuxos/rpms/`.
3. It requires exactly one disk with that serial (`lsblk -dnpo
   NAME,SERIAL`), never a USB disk. Then it erases that disk.
4. It runs `install.sh` with the installer's Automated layout on the A100's
   512 GB PM9C1a. That disk is over 100 GB, so it gets the installer's
   separate ext4 `/home` too. This is what a real user gets.
5. It runs the chroot steps, with `efivarfs` bound. Then:
   - the installer's bare `grub2-install` makes `EFI/pclinuxos` and its
     NVRAM entry, and the script checks both with `efibootmgr -v` (the
     explicit `--no-nvram` form is for make-bootstrap only);
   - `mkinitrd`'s image is checked for `nvme` and `nvme_core`;
   - os-prober stays off through a grub.d drop-in, so the stick and
     mybig512 stay out of grub.cfg.
6. Identity: hostname `thinkpad` in `/etc/hostname` and in
   `/etc/sysconfig/network`. Root logs in by key only. The fixed host key
   is installed with mode 600 and owner 0, and the `tester` user gets a
   random password kept in `/root`.
7. It leaves the default runlevel at 5 with the display manager, as a real
   install has it. No one logs in during the cycle.
8. It tidies up, checks the host key again with `cmp`, and prints
   `BKO-PCLINUXOS-INSTALL-OK`.

There is also `pclinuxos-bootstrap-qemu.sh`, a rehearsal like
`neon-bootstrap-qemu.sh`:

- a test rescue image, a blank NVMe with a fake serial, and the ISO and
  keys on a USB drive laid out like mybig512, under OVMF;
- then the disk is booted alone from its NVRAM entry;
- it asserts SSH and the host key, and that `grub.cfg` names no other
  disk's system.

The README gets a section, "PCLinuxOS: mkinitrd and grub2, from the stick".

**Changes BackShot1 needs (est. 0.3 d of PC8):**

- **`bs1-adopt` `pkg_install`** (`bs1-adopt:160-191`): no change.
  PCLinuxOS reaches the existing `command -v dnf` arm, which runs `dnf
  install -y` with the Fedora column, and those are PCLinuxOS's names too:
  `openssh-server`, `python3`, `util-linux`, `avahi` (*check* each in the
  repo in PC1). `lib64nss-mdns2` is already on the ISO. A 12th column would
  touch every call site (:648, :661, :663, :707); only if PC1 finds a name
  that differs does a `distro == pclinuxos` branch go ahead of the dnf
  arm. The one gap is `install_tools`' efibootmgr `case $distro`
  (`bs1-adopt:674-680`), which has no dnf fallback: it gets a
  `pclinuxos) dnf install -y efibootmgr ;;` line.
- **`start_service`'s sysv arm** (`bs1-adopt:440-444`). Today it runs
  `update-rc.d ... || true`, which silently fails here, so a service
  starts but isn't enabled at boot. New: `chkconfig --add NAME;
  chkconfig NAME on` when `chkconfig` exists and `update-rc.d` doesn't,
  then `service NAME restart`. *Check* that `/etc/init.d` links to
  `/etc/rc.d/init.d` and that `service` exists. The names are already in
  the lists: `sshd`, `messagebus`, `avahi-daemon`.
- **mDNS.** If `nsswitch.conf` lacks `mdns4_minimal` (*check*), it gets
  the line Slackware's `slk_mdns` writes (`bs1-adopt:731-743`).
- **Hostname.** `set_hostname` (`bs1-adopt:494-511`) also writes
  `HOSTNAME=` in `/etc/sysconfig/network`, as it does for OpenRC's
  `conf.d/hostname`.
- **Firewall.** `open_firewall` already handles an active ufw. *Check*
  that it isn't on by default.
- **Sleep.** There is no elogind, so `no_sleep` and `hold_awake` do
  nothing. *Check* whether SDDM's greeter, or ConsoleKit and upower,
  suspends an A100 that no one has logged in to. If it does, bs1-adopt
  writes a system-wide PowerDevil default with suspend off, listed in
  bs1-manifest.
- **`bs1-common`.**
  - `package_list` already uses `rpm -qa` (`bs1-common:489`).
  - `system_settled` (`bs1-verify:87-105`) gets a PCLinuxOS arm that waits
    for `/etc/rc.d/rc` to end, as `rc_m_running` waits for Slackware's
    `rc.M`. It knows the end by `/var/lock/subsys/local` (*check*).
  - `failed_units` stays empty on sysvinit.
- **`bs1-manifest`.** It gets `PCLINUXOS_SOURCE="etc/pclinuxos-release"`
  with `PCLINUXOS_REMADE` and `PCLINUXOS_EXPECTED`, gated like Slackware's
  (`bs1-manifest:287-290`, `866-871`). They hold only what the generic
  `EXPECTED` doesn't already cover: `etc/mtab` (:79), `var/lib/*` (so
  `random-seed` and `speedboot/status`), `var/lock/*` (:91) and
  `boot/EFI/*` (:154, so `EFI/pclinuxos/grubx64.efi`) are there. The lists
  come from a plain reboot test on the guest, then the A100. The likely
  new entries are:
  - `etc/issue`, `etc/issue.net`;
  - `usr/lib/rpm/macros`, if rpmdb-rc is on;
  - `etc/sysconfig/network-scripts/ifcfg-*` and
    `etc/udev/rules.d/70-persistent-net.rules`, if PC1 finds the image
    writes them (see Risks).
- **LUKS:** none on the A100 (Decision 3). The installer offers none, so
  a real PCLinuxOS user has none.

**The run:** the usual cycle (phase1, plan, phase2, verify) with
`backshot1`. Done when:

- every phase passes with 0 unexpected;
- the package list is unchanged (no update runs during the cycle; a kernel
  update would change package *names*, `kernel-V-pclosN`, not only
  versions, and holt's package check reads them the same way);
- the A100 boots `EFI/pclinuxos/grubx64.efi` from its recreated NVRAM
  entry;
- the boot-state arm matches the source.

Before the A100 is touched, `bs1-qemu.py` must pass on `pclinuxos` on two
hosts, with its default NVMe internal disk, not `--virtio` (which is for a
guest whose initramfs has no NVMe driver, `bs1-qemu.py:314`, :327-330).
The restored image must hold `nvme` and `nvme_core`, since INITRDOPTS
doesn't add them. The source must boot from NVMe first, and the guest's
images were probed on virtio, so they likely lack `nvme`: the rehearsal's
source gets an NVMe-capable image first (`mkinitrd --with nvme` in its
throwaway overlay, or the disk `pclinuxos-bootstrap-qemu.sh` installed on
its NVMe). *Check* in PC8 which of the two bs1-qemu.py takes; the restored
image is then made by load's probe alone, which is what the A100 needs. The run waits for a slot when the A100 isn't
needed for BackShot1All.

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| PC1 | **Download and settle every *check*.** Fetch the KDE ISO (4.0 GiB) and its `.md5sum` from NLUUG into `/bkoscratch/rebuild-inputs/pclinuxos/` on Chopin, after the user's OK. Check the MD5 against both mirrors, then pin the ISO's SHA-256. Read it offline: `livecd.sqfs`'s os-release, `/sbin/mkinitrd`'s head, `/etc/sysconfig/{mkinitrd,installkernel}`, mylive-install's `cleanup.sh`, `nsswitch.conf`, ufw's chkconfig state, `/etc/machine-id` in the image, the default hostname, `/etc/init.d`, the default runlevel, MAC-keyed network names (`70-persistent-net.rules`, `HWADDR=` in `ifcfg-*`), and whether grub2 is patched to find `/boot/EFI`. Boot it once in QEMU and do the two GUI installs (one per layout), with an EFI-fallback check on the UEFI one; confirm the GUI's ESP mount point is `/boot/EFI` (not the magazine's `/boot/efi`) and list each image's modules (whether `nvme` and `nvme_core` are in, and whether the grub entries carry `initrd_options`). Fetch the openssh-server RPMs and their dependencies, pinning their SHA-256s. Write `testvm/pclinuxos/install.sh` and the two `make-bootstrap.sh` cases. Build both guests, diff them against the GUI installs, and copy them to a second host (with the same SHA-256s). Then `rebuild-one.sh`. Write "What PC1 found" here. **Done when** both guests boot alone on two hosts and SSH answers | 1.25 d (+0.5 d on Decision 1's path 3) |
| PC2 | **bko-holt, by a High-effort subagent** ([[effort-max-when-it-pays]]). The `SUPPORTED` row and the refusal tests; `PACKAGES` and `arch.rs`; the `mkinitrd-rh` generator with evidence; `initrd-V.img` and the `initrd-` `.old` prefix in `kernels_json`, and `*.old` skipped by `initramfs_images`; load's `mkinitrd-rh` arm; the guarded `_` fallback; the `is_pclinuxos()` guard on the `/boot/grub2` arm and `grub_tool("install")` in the generic arm; the nash readback; the LUKS, LVM and md root refusals; `GENERIC_HOSTS`; the scenarios above. **Done when** `cargo test` passes, both scenarios' plans have one `mkinitrd -f` per kernel and `grub2-install` (and no `update-initramfs` or `grub-install`), and every existing corpus plan is unchanged | 0.75 d |
| PC3 | **vm-update and GuestTest2 input.** `Linux.pclos` in in-guest.sh; check-image.sh's emit lines for `S-dnf-pclos` and `K-pclos`, and `"K-pclos": 2` in `KERNELS_MAX`; then `vm-update --try` on both guests, then (if Decision 10 says so) a real update in a `--keep` overlay that is shipped | 0.35 d |
| PC4 | **Golden plans and Test2 registrations,** as listed under Tests. The registrations wait for, or join, the sibling sessions' batched pass on `classes.json` and the p2 goldens | 0.4 d |
| PC5 | **SticTest1** entries and runs: both guests, both archivers, two hosts, 0 FAIL | 0.35 d |
| PC6 | **HoltTest1** 1Obscure runs on two hosts: the boot-state arm, the judge rules, the boot-alone (through the fallback) and GRUB-prefix checks, the `--reformat /=ext4` case, RAM peaks in `ram-sizes.tsv`, GuestTest1 grades | 0.5 d |
| PC7 | **Docs.** holt/README.md's supported list (`holt/tests/readme.rs` checks it), `osrel.rs`'s comment, `layload.md`'s boot stage (the mkinitrd-rh arm, the non-shim grub2 arm), the 1Obscure row and HoltTest1All, the SticTest1 and Test2 READMEs, and memory (the holttest1d index, a pclinuxos-support note) | 0.1 d |
| PC8 | ***Optional, later:* BackShot1 on the A100.** `pclinuxos-bootstrap.sh` and its QEMU rehearsal, the bs1-adopt, bs1-verify and bs1-manifest changes, `bs1-qemu.py` (default NVMe, image checked for `nvme` and `nvme_core`) on two hosts, then the run | 0.75 d + ~1 h of the user's time |
| | *≈ 3.7 d without PC8, ≈ 4.45 d with it* | |

**Order and commits.**

- PC2 can start beside PC1 from the source facts above. Its golden plans
  (PC4) wait for PC1's dumps.
- Each piece is committed on main ([[work-on-main]]) when its tests pass
  and the user says to commit. Nothing is pushed until the user says so.

**Costs** (est.):

- **PC1:** a 4 GiB download and two ~20 min builds. The qcow2s are about
  10 GiB each, per host.
- **PC5 and PC6:** about 1.5 h of queue per host.
- **The user's time:** none before PC8, except the OKs: the download, the
  decisions, the commits.

## Risks

- **No signatures anywhere.**
  - The ISOs have only an MD5.
  - The repos are `gpgcheck=0`, and the repodata is unsigned.
  - The first download is trusted through TLS to NLUUG and through the MD5
    matching on two mirrors. Every later rebuild is held to the pinned
    SHA-256s.
  - Updating a guest ([[images-take-os-updates]]) pulls unsigned packages
    over HTTPS. That is how every PCLinuxOS user updates, but it is weaker
    than any other supported system. Decision 10 asks whether to take
    them.
- **The install is a replay, not the installer.** `install.sh` copies
  `installer.py`'s steps. A mylive-install release that changes a step
  (LUKS turned on, say) drifts from it silently. PC1's diff against a GUI
  install is the guard, repeated when a new ISO is pinned.
- **mkinitrd in the rescue chroot.** mkinitrd 6.0.93 is from about 2009
  and probes `/sys` and `/proc` for the root's drivers. If it misses the
  restored disk's controller or the root filesystem's module, the
  restored system won't find its root. This matters most on the A100's
  NVMe: `INITRDOPTS`'s `--with` list names only NVMe-over-fabrics and
  target modules (and nvmem_core, which isn't NVMe), not the PCIe `nvme`
  and `nvme_core` drivers, so they come only from the probe of `/sys` in
  the chroot. The guests are virtio, so HoltTest1 can't show it; PC1 lists
  each image's modules, and the PC8 rehearsal (NVMe) and the A100's
  install and readback check for both. The remedy is `--with nvme` in
  load's command (see load's `mkinitrd-rh` arm).
- **The `/boot/grub2` arm's guard and the fallback change touch shared
  boot-stage code.** The guard is keyed on `os.id`, so no other system's
  plan can change through it; the fallback change applies to every
  system. Both are guarded by every existing corpus plan staying
  byte-identical, by the Fedora `via_shim=false` scenario, and by
  HoltTest1's existing Fedora and openSUSE runs.
- **MAC-keyed network names.** Mandriva-lineage systems have written
  `70-persistent-net.rules` and `ifcfg-*` files with `HWADDR=` (initscripts'
  network-scripts honour it). A restore onto a VM with another MAC
  (HoltTest1) could rename eth0 to eth1 and lose the network and SSH; on
  the A100 it would add churn lines. *Check* in PC1 whether the image
  writes either. If it does, the guests' restores are checked for a
  working interface, and bs1-manifest lists the files.
- **A shared machine-id.** If `livecd.sqfs` carries `/etc/machine-id`
  or `/var/lib/dbus/machine-id`, every install from it shares one.
  *Check* in PC1. If so, `install.sh` empties them, as the installer may
  do.
- **ISO churn.** ISOs are replaced every 1-4 months, and old ones leave
  the mirrors. The rebuild inputs keep ours. A lost ISO means the newest
  one, a new pin and a new PC1 diff.
- **Sleep on the A100.** With no elogind, BackShot1's sleep hold does
  nothing. A suspend in the middle of a run fails it. *Check* this first,
  in the rehearsal.
- **VERSION_ID is a year.** It changes to 2027 through an update, which is
  why the row is `*` (Decision 4). A row pinned to 2026 would start
  refusing every updated machine on the first update of 2027.

## Out of scope

- **LUKS, LVM and md RAID roots,** which the installer doesn't offer. A
  bs1-luks family for mkinitrd's nash init is also out: it would need a
  key fetch in an image that has no hooks, and holt's remake would lose
  it. A PCLinuxOS root on any of the three is refused by name
  (Decision 3).
- **dracut as PCLinuxOS's generator.** It is in the repo but not on the
  ISO. If a user switches to it, dump sees dracut's images and the plain
  dracut arm applies. That is *check*ed by a scenario only, with no guest.
- **Secure Boot.** There is no shim to sign with (*inferred*, see Open
  questions).
- **Btrfs, f2fs, JFS and ReiserFS roots** from Manual mode. Btrfs and f2fs
  are generic. JFS and ReiserFS aren't in the rescue image's tools, and
  nothing is promised for them.
- **The MATE, Xfce and Darkstar ISOs.** They use the same base, so the
  desktop makes no difference to bko.
- **Community spins,** and Mageia and OpenMandriva. Those have other IDs
  and are refused.
- **apt-rpm systems** that were never moved to DNF. They would still
  restore, since holt reads `rpm -qa`, but vm-update and bs1-adopt support
  DNF only.
- **BackShot2 cases on PCLinuxOS:** later, through BackShot2All.
- **Changes to the rescue image.**

## Decisions for the user

1. **How the guests (and the A100) are installed.**
   - **Recommended: one `testvm/pclinuxos/install.sh`** that replays
     mylive-install's `installer.py` steps from `livecd.sqfs`. It runs from
     the Arch ISO for the guests (`make-bootstrap.sh`) and from the stick
     for the A100. PC1 diffs it once against a GUI install of each layout.
     It is rebuildable and unattended, one script covers both lanes, and
     the GUI has no answer file. Under make-bootstrap (a BIOS VM) its EFI
     step is the explicit `--no-nvram` install plus the `--removable`
     fallback, so the `pclinuxos` guest has no NVRAM entry of its own; on
     the A100 it is the installer's bare `grub2-install`.
   - Or extend `make-from-live.sh` (the nearest model; not recommended:
     mkinitrd would probe the build host's loop device, its cases are
     apt-based, and the stick would still need `install.sh`).
   - Or drive mylive-install's own engine headless in the live session
     (closer to the real installer; +0.5 d on the 3.7 d total; it relies
     on an internal API and a serial login, and the stick still needs the
     replay).
   - Or `sendkey` through the GTK installer (fragile; not recommended).
2. **The initramfs in load.**
   - **Recommended:** a new generator `mkinitrd-rh`, and per kernel
     `mkinitrd -f /boot/initrd-K.img K $INITRDOPTS` with installkernel's
     options (plus `--with nvme` if PC1 or PC8 shows the probe misses it).
     That is the image a kernel update makes through installkernel, built
     from `/lib/modules`, explicit and logged. It is a choice, not a copy
     of the installer: the installer's images are built by rebuild-initrds
     with each grub entry's `initrd_options` and without INITRDOPTS, whose
     modules are NVMe-over-fabrics ones a restored root never needs.
   - Or `bootloader-config --action rebuild-initrds`, as the installer
     runs it. That is one command, and it writes no loader configuration
     (`bootloader.pm:918`), but it picks kernels from the grub entries,
     goes through drakx's Perl and leaves an `initrd-K.img.old` beside each
     image.
   - Or keep the restored images unless something they were made from
     changes, as Slackware's do since 2026-11-25. That is fewer moving
     parts, but needs its own rules; it could come later.
3. **LUKS, LVM and md roots.**
   - **Recommended:** none. Guests, the A100 and holt all refuse a
     PCLinuxOS root on LUKS, LVM or md RAID by name (the message is under
     load's "LUKS, LVM and md roots"), with a scenario each, because the
     installer offers none of them and mkinitrd's mapping and assembly in
     the chroot are unproven.
   - Or a third guest, `pclinuxos-luks`, made by `install.sh` (not the
     installer), with the passphrase typed by `console-boot.py`, and
     BackShot1 family A if wanted. That is est. +0.75 d on the 3.7 d
     total, for a layout few PCLinuxOS users have. LVM and md would each
     need a guest of their own the same way.
4. **The supported version.**
   - **Recommended:** `("pclinuxos", "*", "PCLinuxOS")`. It is rolling,
     and VERSION_ID is the release package's year, which an update moves.
   - Or `"2026"`, so that 2027 is checked before it is claimed, as
     PikaOS's 5 is. That refuses every updated machine from the first
     update of 2027 until a row is added.
5. **openssh-server** (not on the ISO).
   - **Recommended:** fetch it and its dependencies once on Chopin, pin
     their SHA-256s in `install.sh`, and keep them in rebuild-inputs (and
     in `backshot1/pclinuxos/rpms/` on mybig512, copied by the user). The
     install is then offline and reproducible, and nothing unsigned is
     fetched at install time.
   - Or `dnf install openssh-server` from the chroot at install time. That
     is simpler, but each build gets whatever the mirror has that day.
6. **The ISO.**
   - **Recommended:** `pclinuxos64-kde-2026.11.iso` (4.0 GiB, MD5
     `488ae2664b13c40a88fdfe4d298cc079`), from NLUUG, checked against
     freedif's `.md5sum` too, with its SHA-256 pinned on first download.
     It is the flagship edition, and the A100 should run what most users
     run.
   - Or KDE Darkstar (3.1 GiB, the smallest).
   - Or Xfce (3.5 GiB).
7. **HoltTest1's group.**
   - **Recommended:** **1Obscure**, as Solus and Slackware did. The boot
     stack is shared with nothing else.
   - Or a new word, **1PC**.
8. **Guests.**
   - **Recommended:** two, `pclinuxos` (UEFI, the Automated layout) and
     `pclinuxos-bios` (BIOS, `/boot` plus an XFS root). Only both cover
     both grub2 targets and a root module the image has to carry.
   - Or `pclinuxos` only (about 0.5 d less, and BIOS isn't covered).
9. **BackShot1 on the A100 (PC8).**
   - **Recommended:** later, as its own step after PC1-PC7 pass and when
     the A100 isn't needed for BackShot1All. It needs Secure Boot off and
     about an hour of the user's time.
   - Or now, with PC1.
   - Or not at all.
10. **OS updates for the guests**, which are unsigned (`gpgcheck=0`,
    unsigned repodata, over HTTPS; see Risks).
    - **Recommended:** take them, as [[images-take-os-updates]] says images
      do and as every PCLinuxOS user does: `Linux.pclos` in a `--keep`
      overlay, shipped by vm-update. Each update's package list is in the
      guest's fingerprint, so what came in is on record, and HoltTest1 then
      tests the kernels and mkinitrd a real user has.
    - Or freeze the guests at the ISO plus the pinned openssh RPMs: nothing
      unsigned is fetched after the first, pinned download, but the guests
      drift from what users run and GuestTest2's K-pclos never sees a
      kernel update.
    - Or pin a dated snapshot of the repo in rebuild-inputs and update
      from that: reproducible, but a snapshot to keep and refresh by hand.

## Open questions

These are for PC1 to settle on the ISO; none needs the user:

- Does the live session give a serial login (path 3 of Decision 1), and
  under which user?
- Does mylive-install remove any live-only packages (mylive-install,
  mylivecd), or does an installed system keep them? `install.sh` does
  whatever the installer does.
- Does `grub2-install` with no `--efi-directory` find `/boot/EFI` (a
  patch to upstream's `BOOTDIR/efi` probe), write an
  `EFI/BOOT/BOOTX64.EFI` fallback, and make the NVRAM entry itself? The
  A100's install keeps whichever behaviour PCLinuxOS has.
- Does installkernel make `/boot/vmlinuz` and `/boot/initrd.img` links?
  (The image format is settled: a gzip -9 newc cpio, mkinitrd:1903-1906.)
- Do the installer's images hold `nvme` and `nvme_core` when the disk is
  NVMe, and do the grub entries carry `initrd_options`?
- Does the image write `70-persistent-net.rules` or `ifcfg-*` files with
  `HWADDR=`?
- Is `/etc/init.d` a link, is there a `service` command, what is the
  default runlevel, and which display manager does it use?
- Does `nsswitch.conf` have `mdns4_minimal`? Is ufw on?
- What is the installer's default hostname, for `GENERIC_HOSTS`?
- Does `rpm -qa --qf` on rpm 6.1 with ndb match the format that
  `rpm_packages()` and bs1-common read?
- Does anything (upstream's forum or KnowledgeBase, behind Cloudflare)
  say Secure Boot is unsupported? So far the only evidence is that no shim
  ships. If the user can read the forum in a browser, one look would
  settle it.
