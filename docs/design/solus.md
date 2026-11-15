# Solus support

2026-11-14. Requested by the user as one of eight distributions added in
parallel: support Solus, the independent desktop distribution with eopkg and
clr-boot-manager. It is added the same way as AnduinOS, KDE neon, elementary,
PikaOS, BigLinux and Garuda (commit 1edb4b5). **Status: approved 2026-11-14
by the user with every recommended decision (the last section). S1-S6 built
2026-11-14: SticTest1 and HoltTest1 PASS on Chopin and Cinqcent; the Test2
registrations wait for the batched pass; S7 (BackShot1) not started.**

**Conventions.** est. marks an estimate. *Check* marks a fact the public
sources leave open. The first guest (S1) settles each one before any code that
depends on it is written. Solus's facts come from its own sources, read
2026-11-14:

- getsol.us (the 4.9 release post and the download page);
- help.getsol.us (Secure Boot, system rescue, eopkg);
- github.com/getsolus/packages: the `calamares` package's config and patches
  0003/0004, `clr-boot-manager`, `eopkg`, `usysconf`, `linux-current`,
  `linux-lts`;
- the live Polaris and Unstable `eopkg-index.xml.xz`.

The raw notes, with a URL for each fact, are in the session notes folder as
`solus-facts.md`. No ISO has been opened. Code references are to local `main`
at 1edb4b5, plus the uncommitted tree of the same day.

## Summary

- Solus 4.9 becomes a supported system for bko-holt and both archivers. It
  gets:
  - one QEMU guest, `solus49`;
  - a SticTest1 entry;
  - a place in HoltTest1 **1Obscure**;
  - a golden plan and the Test2 registrations.

  BackShot1 on the Thinkpad is an optional last step.
- It is the first supported system whose ESP is **not in fstab**. The installer
  mounts the ESP at `/boot`, and only clr-boot-manager (CBM) mounts it, for as
  long as an update takes. Today `bko-holt dump` of a booted Solus would record
  no ESP and give no warning. The warning that exists (`unmounted_esp_warns`)
  is keyed on fstab, which has no ESP line here. A backup would then have none
  of the kernels, the loader or its entries. This is the one real gap.
- The boot stack is **shim and systemd-boot, both under non-standard names**,
  in `EFI/com.solus-project/`:
  - Fedora's Microsoft-signed shim is `bootloaderx64.efi`;
  - systemd-boot, renamed, is `grubx64.efi` (shim loads that name);
  - MokManager is `mmx64.efi`;
  - CBM copies each kernel and initrd beside them;
  - the entries are in `loader/entries/Solus-*.conf`.

  `detect_loader` keys shim on `shimx64.efi` and would most likely call this
  GRUB without shim. `load`'s generic systemd-boot arm then runs
  `bootctl install`, which would put an unsigned systemd-boot in place.
- Most of the rest is already built:
  - systemd-boot entries on the ESP, a kernel's initramfs that ships prebuilt
    and needs no rebuild (AerynOS's `prebuilt`);
  - shim with a MOK, which vm-run's `--mok` already handles (AnduinOS);
  - ext4 root;
  - plain GPT with partition GUIDs kept.
- What's new:
  - a fourth installed-package reader (eopkg's `/var/lib/eopkg`);
  - a Solus loader and ESP arm in dump and load;
  - vm-update's `Linux.eopkg` section;
  - GuestTest2's `S-eopkg`, `L-cbm` and `K-cbm` rules.
- ~3 d (est.), plus an optional BackShot1 run (~0.5 d, and about an hour of the
  user's time to install Solus on the Thinkpad).

## Why

- **An independent lineage.** Solus isn't Debian, Arch, Fedora or SUSE. It has
  its own package manager (eopkg, a PiSi descendant) and its own boot manager
  (clr-boot-manager, from Clear Linux). None of these is covered today.
- **The ESP as a mount-on-demand `/boot`.** This is a real layout that bko
  would silently back up incompletely today. Covering it also guards against
  any other system that keeps its ESP out of fstab.
- **A shim chain with renamed binaries.** It tests that bko recognises shim by
  what it is, not by its file name.

## What Solus is (as far as bko cares)

| | Solus 4.9 "Serenity" | Nearest supported system |
|---|---|---|
| Release | 4.9, 2026-04-18 (4.8 2025-11-29). A curated rolling release: the Polaris (stable) repository is synced from Unstable every Friday. `VERSION_ID` changes only with a new ISO | Tumbleweed (rolling), AerynOS |
| os-release | `ID=solus`, `VERSION_ID="4.9"`, `NAME="Solus"`, `PRETTY_NAME="Solus 4.9 Serenity"`, `VERSION_CODENAME=serenity`. **No `ID_LIKE`.** `/etc/os-release` links to `/usr/share/os-release/os-release` (made by tmpfiles). Exact 4.9 lines are a *check* | AerynOS (no family) |
| Editions | Budgie, GNOME, Plasma, Xfce (no MATE). The ISOs are 3.8-4.3 GB | |
| Image | `https://downloads.getsol.us/isos/2026-04-18/Solus-Xfce-Release-2026-04-18.iso`, 3,804,708,864 bytes, SHA-256 `a3085fa5c0171420e46e1554191fffe66e50dbea70c37e9d6cc4ec79fb557895`. A `.sha256sum` and **a detached GPG signature** (`.sha256sum.sign`), key `F5F6685CAF5559771D9CCB92618EB3600BD32D59` "Solus (Release & Engineering)" from `downloads.getsol.us/gpg/solus-releng-pub.gpg` | Mint (signed sums) |
| Live system | dracut dmsquash-live: Calamares's unpackfs mounts `/run/initramfs/live/LiveOS/squashfs.img` and copies the ext4 `LiveOS/rootfs.img` inside it. The volume label, the kernel and initrd paths, the `root=live:` line and the ISO's own loader (probably shim and systemd-boot on an embedded ESP, `SOLUSESP`) are *checks* | AnduinOS (dmsquash-live) |
| Installer | Calamares 3.4.2 with Solus patches. **No unattended mode** | Garuda, neon (Calamares) |
| Layout (UEFI, the default) | GPT. The ESP is mounted at **`/boot`** (2 GiB recommended, 500 MiB minimum, label "EFI"; whether that is the GPT name or the FAT label is a *check*) and is **left out of fstab** (a Solus patch). / is ext4 by default; btrfs (`@`, `@home`) and f2fs are offered. Swap: none, small or suspend (the default is a *check*), plus zram (`min(ram, 8192)` MB). LUKS2 on offer; no LVM | AerynOS (one ESP holds the kernels) |
| Layout (BIOS) | The ESP plus an 8 MiB `bios_grub` partition (partlabel `boot`), with GRUB installed through CBM's GRUB2 backend | |
| Kernel | `linux-current` (7.3.4-373 in Polaris today) and `linux-lts` (6.18.63-322). The files are in `/usr/lib64/kernel/` (`/usr/lib` links to `lib64`): `com.solus-project.current.7.3.4-373`, `initrd-com.solus-project.current.7.3.4-373`, `cmdline-7.3.4-373.current`, `config-…`, `System.map-…` | AerynOS (`/usr/lib/kernel`) |
| Initramfs | dracut, **run when the package is built**: a generic initrd ships in the kernel package, and nothing on the machine rebuilds it. Extra initrds go in `/etc/kernel/initrd.d`. Whether the generic one unlocks LUKS is a *check* | AerynOS (`prebuilt`) |
| Boot manager | **clr-boot-manager 3.7.0**. On UEFI it writes shim, systemd-boot and the kernels to the ESP. It finds the ESP by the `LoaderDevicePartUUID` variable, then by partlabel `ESP`, or uses `/boot` if something is already mounted there. It keeps the running kernel, the newest one and the last one that booted. The command line is merged from `cmdline-K`, `/usr/lib64/kernel/cmdline.d`, `/etc/kernel/cmdline` and `/etc/kernel/cmdline.d/*.conf`, less `cmdline-removal.d`; CBM adds `root=`, `rd.luks.uuid` and `rootflags`. The timeout is in `/etc/kernel/timeout`. Its NVRAM entry is "Solus Linux Bootloader". `--path` other than `/` is **image mode**: no EFI variables, and `EFI/BOOT/BOOTX64.EFI` is overwritten with plain systemd-boot, which breaks the fallback under Secure Boot. Other commands: `mount-boot`, `list-kernels`, `report-booted` | none |
| ESP (UEFI) | `EFI/com.solus-project/{bootloaderx64.efi (shim), grubx64.efi (systemd-boot), mmx64.efi, BOOTX64.CSV, kernel-com.solus-project.*, initrd-com.solus-project.*}`, `EFI/BOOT/BOOTX64.EFI` (shim), `loader/loader.conf`, `loader/entries/Solus-<type>-<ver>-<rel>.conf`, `solus-enroll-me.cer` at the root. No `EFI/systemd/` | AnduinOS (shim copy as fallback) |
| Secure Boot | Fedora's shim plus Solus's key, enrolled once in MokManager (`solus-enroll-me.cer`) | AnduinOS (`vm-run --mok`) |
| Packages | **eopkg 5.0.0**: Python 3, one nuitka-compiled binary. The DB is in `/var/lib/eopkg` and the cache in `/var/cache/eopkg`. The repository is `https://cdn.getsol.us/repo/polaris/eopkg-index.xml.xz`, named "Solus" by the installer. After every transaction eopkg runs **usysconf** (depmod, ldconfig, `clr-boot-manager update`, …); usysconf skips CBM in a chroot. The DB's on-disk format is a *check* | none |
| Init and security | systemd 261.5, NetworkManager. AppArmor is in the kernel's LSM list, but its userspace isn't installed; no SELinux. **openssh-server isn't on the ISO**; installing it enables sshd. The admin group is `wheel` | |
| What's next | Solus says it will move gradually to AerynOS's tooling (moss, blsforme), with no date set | AerynOS |

## What already works, and what doesn't

**bko-holt `dump`:**

- `osrel.rs` `SUPPORTED` has no `solus` row, so dump refuses it. It needs
  `("solus", "4", "Solus 4")`, with unit tests from the guest's os-release:
  4.9 is accepted, and a `solus`-ish lookalike and an unknown major are
  refused. The `supported_names` assert line is shared with every distro
  session: re-read it, then append.
- **Packages:** `installed_packages` reads dpkg, pacman, rpm, apk, xbps and
  Portage, but not eopkg, so a Solus dump would list no packages. That breaks
  `boot.packages`, the loader evidence and fingerprint.py. New:
  `eopkg_packages()` reads `/var/lib/eopkg/package/*/metadata.xml`. The layout
  is a *check*: PiSi keeps one `NAME-VER-REL-BUILD-ARCH` directory per package,
  each with `metadata.xml` and `files.xml`. The new arm goes after xbps, with a
  unit test from the guest's real files. `PACKAGES` and
  `testvm/update/fingerprint.py` gain `clr-boot-manager`, `shim` (Solus's name
  is a *check*), `systemd-boot`'s Solus package, `linux-current`,
  `linux-lts`, `dracut` and `usysconf`.
- **The ESP.** dump takes the ESP from mountinfo and fstab, so on Solus it
  finds neither (Decision 3). New: when `clr-boot-manager` is installed and
  `/boot` isn't mounted, dump finds the ESP as CBM does: by
  `LoaderDevicePartUUID` (efi.rs already reads it), then by partlabel `ESP`,
  then by the ESP GPT type on the root file system's disk. It then **refuses
  with a message**:

  > Solus keeps its ESP out of fstab, and nothing is mounted at /boot: run
  > `clr-boot-manager mount-boot` (or `mount /dev/X /boot`) and dump again.

  With the ESP mounted at /boot, the dump records it as `mounted_at: /boot`
  and `in_fstab: false`. The backup, which follows the dump, then has its
  files. A scenario in `scenarios.rs` covers both cases, beside
  `unmounted_esp_warns`.
- **The loader.** `detect_loader` has to see shim, and systemd-boot behind it,
  when the file names say otherwise:
  - BootCurrent's file `\EFI\com.solus-project\bootloaderx64.efi` is shim. It
    is recognised by its contents (an `.sbat` section naming `shim`; *check*
    what the dump can read cheaply), or by CBM's fixed names when
    `clr-boot-manager` is installed. CBM's names are the fallback.
  - The loader behind it is systemd-boot. The evidence is LoaderInfo (`systemd-boot
    261.5`), which systemd-boot sets itself, and `loader/entries`.
  - The result is `name: systemd-boot`, `via_shim: true`, and a new
    `boot.loader.manager: clr-boot-manager`.

  With a fresh OVMF store (no NVRAM entry), BootCurrent is OVMF's
  removable-media entry. Then `EFI/BOOT/BOOTX64.EFI`'s `same_as`
  `bootloaderx64.efi` carries it, as on `anduinos2`.
- **The initramfs:** `initramfs_json` calls it `prebuilt` when
  `/usr/lib/kernel` has `*.initrd`. On Solus the names are `initrd-com.solus-project.*`
  in `/usr/lib64/kernel`, so the test widens to that name, and a unit test
  pins it. `dracut` is installed, so without this change the generator would
  come out as `dracut`, and load would rebuild a host-only initrd that CBM
  never copies.
- **`bootloader_id()`:** `com.solus-project`.

**bko-holt `load`:**

- **Partitions and format:** the source's GPT partition GUIDs and names, the
  FAT serial and the ext4 UUID are kept already. They matter here:
  - CBM finds the ESP by `LoaderDevicePartUUID`, which is the ESP's partition
    GUID;
  - the entries' `root=UUID=` is the ext4's UUID.
- **Restore:** the ESP comes back from the backup like any file system, at
  `/boot` in the target. The restored fstab still has no ESP, which is right
  for Solus.
- **Boot stage, the new Solus arm** (Decision 4): `os.id == solus` with
  `boot.loader.manager == clr-boot-manager`. **No `bootctl install`, no
  rebuilt initramfs, no `clr-boot-manager update`.** Shim, MokManager,
  systemd-boot, every kernel and initrd CBM had copied, and the entries all
  come back with the ESP. They hold no machine identity beyond the UUIDs
  `format` keeps. Only the NVRAM entry is made (`efi_entries()`): "Solus Linux
  Bootloader", `\EFI\com.solus-project\bootloaderx64.efi`. The plan notes:
  - why CBM isn't run: in the rescue chroot `LoaderDevicePartUUID` names the
    rescue stick's ESP, and `--path` is image mode, which writes a plain
    systemd-boot over the shim fallback;
  - that on other hardware with Secure Boot on, Solus's key has to be
    enrolled again (`solus-enroll-me.cer` at MokManager);
  - that the next `eopkg up` runs `clr-boot-manager update` itself.
- **BIOS** (CBM's GRUB2 backend, a `bios_grub` partition): out of the guest's
  scope (Decision 2). Load refuses a Solus BIOS dump with a clear message
  rather than guessing. *Check* on S1 whether a 30-minute look at CBM's GRUB
  backend makes it cheap enough to cover.

**The rescue image:** no change ([[one-kernel-rescue]]). Nothing from Solus
runs in the rescue system; the boot stage runs only `efibootmgr`.

## Guest

One guest, `solus49`. It is what the installer gives by default on a PC:

- UEFI with **Secure Boot on** (Solus's MOK enrolled through `vm-run --mok`);
- GPT with the 2 GiB ESP at `/boot`, out of fstab;
- ext4 root;
- the installer's default swap (*check*), plus zram.

The Xfce edition is used: it is the smallest ISO, and the desktop doesn't
matter to bko. The guest boots to text mode. A btrfs or LUKS variant would test
nothing a Solus-specific code path doesn't already cover elsewhere (Decision 2).

**How it is built** (Decision 1):

1. **`make-solus.sh` with `testvm/solus/install.sh`, run in the live system
   under QEMU** (recommended). This is how AnduinOS and AerynOS are built, and
   EndeavourOS with `make-arch.sh --live`.
   - QEMU boots the ISO's kernel and initrd under OVMF (Secure Boot build, empty
     store) with `systemd.run=` fetching install.sh from 10.0.2.2.
   - install.sh replays Calamares's job sequence from the ISO's own config
     (`settings.conf`'s `exec` list):
     - partition (ESP 2 GiB, partlabel and FAT label as the installer sets
       them), mkfs;
     - unpackfs (`LiveOS/rootfs.img`);
     - machineid, fstab (without the ESP, as patch 0003/0004 does), locale,
       keyboard, users (`tester`, root);
     - removing the live packages (Calamares's `packages` module list);
     - `10_resume.conf`;
     - then **`clr-boot-manager update` in the target, from the live system**,
       which has real efivars. So CBM writes the ESP and its NVRAM entry
       exactly as the installer would.
   - On top, as for every guest: `eopkg it openssh-server` (with sshd
     enabled), root's key, the serial console in `/etc/kernel/cmdline.d`,
     `multi-user.target`.
2. **`make-from-live.sh solus ext4 …`, a new `dmsquash` family, on the
   host.** It would unpack `rootfs.img`, chroot, and run eopkg and CBM. It is
   not recommended:
   - CBM in a host chroot only works through `--path`, which is image mode:
     it writes no NVRAM entry and puts plain systemd-boot in
     `EFI/BOOT/BOOTX64.EFI`, so the guest wouldn't be what the installer makes;
   - make-from-live.sh is apt-, casper- and initramfs-tools-shaped
     throughout, and a Solus family would be a second script inside it;
   - est. +0.25 d and more drift.
3. **By hand once through the GUI**, kept as a pinned image. It is the
   cheapest now, but `rebuild-one.sh` can't rebuild it.

The ISO's internals (label, kernel path, the `root=live:` line, whether it
boots under OVMF's Secure Boot build) are S1's first reading.

**Pieces as for the others:**

- the ISO, `.sha256sum` and `.sha256sum.sign` in
  `/bkoscratch/rebuild-inputs/solus49/` on both hosts. The build checks the
  GPG signature against the pinned fingerprint, then the SHA-256 pinned in the
  script (Decision 5);
- `rebuild-one.sh` (a `solus49)` line, and the ISO check);
- `DISK_SIZE=20G`;
- `holttest1d/ram-sizes.tsv` (est. D 8, MEM 3 until measured);
- `vm-run-options` `--uefi --mok BASE64` (`solus-enroll-me.cer`);
- both hosts by `vm-store` publish, once the cutover is done;
- a GuestTest1 grade.

**vm-update** (Decision 6): a new `# t2-section: Linux.eopkg` in
`testvm/update/in-guest.sh`:

- `eopkg up -y`. usysconf then runs `clr-boot-manager update` on the booted
  guest, which mounts the ESP itself;
- then `eopkg dc` (delete the cache) and `eopkg rmo -y` (orphans; *check*
  that it doesn't remove anything the guest needs).

Also: fingerprint.py's `LINUX_KINDS` gains `eopkg`, and the `vm-update` header
names it. vm-update's read-only mount of the image has to mount the ESP at
`/boot` itself, since fstab won't. vm-mount mounts what the guest's fstab names
plus known ESPs (*check*); if it doesn't, vm-mount gains one rule: a GPT ESP
not in fstab on a Solus guest goes to `/boot`.

## What S1 found (the guest)

Built 2026-11-14 by Decision 1's path 1:
`testvm/make-solus.sh ISO WORK_DIR solus49`, with `testvm/solus/install.sh`
run in the live system. It took four builds; the fourth, on Cinqcent's disk
(`/bkoscratch/solus49-build`), is the guest. It took 1774 s (the install 29.6 min,
mostly `eopkg up`'s 633 packages at ~1 MB/s), and the first boot ~1 min. The
image was then booted once more with `vm-run --discard` for `fstrim -av`
(1.8 GiB of the ESP, 8.7 GiB of /: what eopkg replaced and deleted), and
"fresh-install" taken again: 15 GiB of qcow2 down to 9.4 GiB. make-solus.sh
and install.sh now trim before the snapshot themselves, so a rebuild comes out
the same way. The guest is in `vms/solus49` on Chopin and Cinqcent (a full
folder on each), SHA-256
`550217b555c7feff7a398d48fe9c2b17e4ee5702259a5ee1cd7d09a58d6cd0a3` on both.
`rebuild-one.sh solus49` has the recipe.

- **The ISO** (`SolusLiveXfce`, 3,804,708,864 bytes): `.sha256sum` signed by
  "Solus (Release & Engineering)", primary key
  `F5F6685CAF5559771D9CCB92618EB3600BD32D59` (subkey
  `BFE1968E724A46FB65FBA9F1734AAD02FC2F77E1` signs). make-solus.sh checks the
  signature with `solus-releng-pub.gpg` beside the ISO, then the pinned
  SHA-256. The live boot is dracut's dmsquash-live, `/boot/kernel` and
  `/boot/initrd.img`, `root=live:CDLABEL=SolusLiveXfce ro rd.luks=0 rd.md=0
  rd.live.overlay.overlayfs=1`, LiveOS/squashfs.img holding
  LiveOS/rootfs.img (ext4, 7.8 GiB used). It boots under OVMF's Secure Boot
  build with an empty store. The ISO's own systems: kernel 6.18.21-333,
  clr-boot-manager 3.6.0-39, eopkg 4.4.2-35, shim-signed 15.8-7, systemd 257.
- **Calamares 3.4.2's config** (`/usr/share/calamares`, not `/etc`): the
  sequence partition, mount, mountsquashfs, unpackfs, machineid, fstab,
  locale, keyboard, localecfg, postinstall (usysconf, vconsole's font,
  `eopkg rr Local`, `eopkg ar Solus …/polaris/eopkg-index.xml.xz`),
  removeliveos (the live branding packages, calamares, `/etc/sudoers.d`,
  `/etc/pam.d`), removeuser `live`, users, displaymanager, networkcfg,
  hwclock, bootloader (`efiBootLoader: clr-boot-manager`: `10_resume.conf`
  only with swap, then `clr-boot-manager update` in the target), umount.
  install.sh replays it (not displaymanager, networkcfg, hwclock).
- **The layout settled the checks:** Calamares's `PartitionActions.cpp` with
  `createHybridBootloaderLayout: true` makes a 2 MiB gap, the ESP (FAT32,
  2 GiB, **GPT name "EFI", no FAT label**), an **8 MiB BIOS boot partition**
  (unformatted, no name), then root (ext4, Linux filesystem type, no name).
  **No swap partition** (no `initialSwapChoice`, so "none"); zram is
  zram-generator's `min(ram, 8192)`. fstab holds `/` alone: Calamares's fstab
  module (Solus's patch) skips `/boot` on UEFI, "clr-boot-manager will mount
  it as needed".
- **What clr-boot-manager made** (after `eopkg up`, CBM 3.7):
  `EFI/BOOT/BOOTX64.EFI` (byte-equal to the shim), `EFI/BOOT/fbx64.efi`
  (shim's fallback: there is no `EFI/BOOT/grubx64.efi`),
  `EFI/com.solus-project/{bootloaderx64.efi, BOOTX64.CSV, grubx64.efi
  (systemd-boot 261.5), mmx64.efi, kernel-com.solus-project.current.V-R,
  initrd-com.solus-project.current.V-R}` for both the ISO's kernel and the
  updated one (7.3.4-373), `loader/loader.conf` (`default
  Solus-current-7.3.4-373.conf`), `loader/entries/Solus-current-V-R.conf`
  (`root=PARTUUID=` of the root partition, the serial console from
  `/etc/kernel/cmdline.d`), `solus-enroll-me.cer` (DER, "Solus Machine Owner
  Key", until 2032-12-08). No `EFI/systemd/`. NVRAM: "Solus Linux
  Bootloader" → `\EFI\com.solus-project\bootloaderx64.efi`. On vm-run's
  fresh store, OVMF boots the removable path, and shim's `fbx64.efi` reads
  BOOTX64.CSV and makes that entry itself.
- **What the first builds taught:**
  1. A stalled download ends `eopkg up` with "System error" (Chopin was busy;
     the live system's sched_ext scheduler, `scx_loader.service`'s "flash",
     stalled and was thrown out by the kernel). install.sh retries eopkg five
     times (its cache keeps what it fetched), and the live system masks
     scx_loader; the installed system keeps it on, as Solus ships it.
  2. Solus's presets end with `disable *` (99-default.preset), and usysconf
     applies them again after package changes, so `systemctl enable sshd`
     didn't last: install.sh adds `50-bko-sshd.preset` and checks sshd is
     enabled at the end.
  3. Solus has no mokutil unless asked: install.sh installs it with
     openssh-server, for the harnesses' `mokutil --sb-state`.
  4. The qcow2 reaches 16 GiB while it is built (the 633-package `eopkg up`),
     over a 10 GiB RAM reservation, and both hosts' RAM queues were long, so
     the guest was built on disk.
- **The installed system:** `eopkg up` from the ISO's 2026-04-18 state (633
  packages, ~25 min at ~1 MB/s); eopkg's DB stays
  `/var/lib/eopkg/package/NAME-VER-REL/` (944). `/usr/lib` is a link to
  `lib64`. LSMs `capability,landlock,lockdown,yama,bpf,apparmor`. The first
  boot alone under Secure Boot: kernel 7.3.4-373.current, `bootctl` says
  "Secure Boot: enabled (user)" and "systemd-boot 261.5", `/boot` not
  mounted, zram0 1.9 GiB.
- **vm-update:** `Linux.eopkg` runs `eopkg up -y` then `eopkg dc`. `eopkg
  rmo` is left out: nothing in a fresh guest needs it, and it could remove
  what openssh-server or mokutil pull in. `vm-update --try --hosts chopin,cinqcent
  solus49` (2026-11-14, Chopin): ok, "updated and rebooted (Linux
  7.3.4-373.current); nothing to merge; overlay 14 MiB thrown away": the
  guest was current, and the reboot came up under Secure Boot. vm-mount
  mounts the ESP at `/boot` itself (a Solus rule after its fstab loop).

## Tests

**SticTest1** (`testvm/stictest1/solus49`):

- the files come from `aerynos`'s check (an ESP holding the kernels) with
  `chimera-uefi`'s `fatattr.py`;
- the scratch disk is laid out from the guest's own `sfdisk -d`, with the ESP
  mounted at `/boot` inside the backed-up path;
- real examples: `/usr/lib64/kernel`'s files, `/etc/kernel/cmdline.d`, the
  ESP's shim, systemd-boot and entries with their FAT attributes, and
  `/var/lib/eopkg`'s DB;
- a stand-in for setfattr if the guest has no attr package (*check*);
- both archivers, both hosts;
- registered in `stic/manifests.json`, `limits.json`, check-manifests.py's
  count (re-read it: it sets the count to the entries present, 52 today) and
  `tests/test_stic.py`'s counts.

**HoltTest1:** `solus49` joins **1Obscure** (Decision 7). That means
`classes.json`'s `H.Obscure`, the README's 1Obscure row and HoltTest1All.

- ht1.sh's source prep runs `clr-boot-manager mount-boot` before the dump
  (the user's own step, written in the docs).
- judge.py **requires** these files to be byte-identical to the source's,
  since nothing in the boot stage may excuse them:
  - `boot/EFI/com.solus-project/*`;
  - `boot/EFI/BOOT/BOOTX64.EFI`;
  - `boot/loader/**`.

  The existing AerynOS rule (`^(boot/)?efi/EFI(/(BOOT|Boot|systemd|Linux)…`)
  must not match them. On Solus the ESP is `boot/`, not `boot/efi/`, so a
  `SOLUS_KEPT` set mirrors `ANDUINOS_KEPT`.
- A check reads the restored shim's signature, and systemd-boot's under
  Solus's key (`sbverify --cert solus-enroll-me.cer`, *check* the key format).
- The restored disk boots alone under the guest's `vm-run-options` (Secure Boot
  on). `bootctl status` or LoaderInfo must say systemd-boot, and
  `mokutil --sb-state` must say `SecureBoot enabled`.
- A second boot after `eopkg up` isn't part of HoltTest1. CBM's first run on
  the restored machine is covered by vm-update on the restored copy once,
  recorded in "What S5 found".

**Test2** (S3, in the batched registration pass agreed in the notes folder):

- a golden plan in `holt/tests/corpus/solus49/` (`src.json`, `plan.txt`, an
  `index.tsv` line, the corpus README) from a `bko-holt dump` of the guest;
- `classes.json` (`uefi`, `H.Obscure`), `holt/spec.json`
  (`{"kind": "linux", "firmware": "uefi"}`), `guest/spec.json` (harness
  `make-solus.sh`, `solus/**`, `in-guest.sh#Linux.common`,
  `in-guest.sh#Linux.eopkg`, `boot-state.sh`; checks `pm` eopkg, `S-eopkg`,
  `L-cbm`, `K-cbm`, `B-lin`, `M-lin`, `fw` uefi, `uefi-sb`);
- `estimates.tsv` (holt as aerynos's 5.98 gm, stic 2.2, guest 2.0 until
  measured);
- `t2lib/plan.py`'s counts (sources and guests +1 each: re-read, since other
  sessions move them), `tests/fixtures/p2/world.py` and the re-blessed
  goldens.

**GuestTest2's new rules** in `t2lib/guest_judge.py`:

- **`S-eopkg`:** the repository is Polaris
  (`cdn.getsol.us/repo/polaris`), and `eopkg lr` lists it enabled.
- **`L-cbm`:** the ESP has shim as `bootloaderx64.efi` and as the
  `EFI/BOOT/BOOTX64.EFI` fallback (byte-equal), systemd-boot as `grubx64.efi`,
  a `loader/entries/Solus-*.conf` for each kernel CBM keeps, and the entry's
  kernel and initrd present on the ESP.
- **`K-cbm`:** `KERNELS_MAX` 3 (CBM's running, newest and last-booted). The
  running kernel must be the newest installed of its own type (current or
  lts, as CBM picks its default) after the reboot. Every
  `/usr/lib64/kernel/com.solus-project.*` must have its
  `initrd-com.solus-project.*` (`no-initrd` is a FAIL).
- `known-lines.json`'s `eopkg` lines come from S1's update log.

Done when two corpus builds have the same sha256, the leak scans are 0 on both
hosts and the Mac, no other plan changes, and Test2's own tests pass.

**What S4 found** (2026-11-14): `stictest1/solus49` **PASS on both hosts**
(Cinqcent `s4solus-20261114-203739`, Chopin `s4solusc-20261114-204727`), 40
PASS and 4 LIMIT each (birth times, SELinux labels: the LSMs are AppArmor's
family, NFSv4 ACLs, the whole-disk needs), with roundrel8's restic-bko 0.19.1+backotter.7
and rustic-bko 0.11.4+backotter.13: 722 paths, 380 of them on the ESP mounted at
`tree/boot` with clr-boot-manager's own options (`…,codepage=437,iocharset=ascii,shortname=mixed,utf8,errors=remount-ro`),
every FAT attribute and the guest's sha256s kept. The first runs timed out
(30 min) in the node comparison: one `restic cat blob` a directory, and
eopkg's DB has 943 package directories, so the check takes a 32-directory
sample of `/var/lib/eopkg/package` (the boot-relevant ones named) beside the
rest of the DB. ~410 s a run, ~3.0 GiB of the RAM disk (`--reserve 4 5`).

**What S5 found** (2026-11-14): `solus49` HoltTest1 **PASS on both hosts**,
Cinqcent's run `s5-3` and Chopin's `s5-4` (27 PASS checks each), with a
static bko-holt 0.2.6 built from the working tree (`HT_HOLT`), no fix to
holt/ or the harness. `clr-boot-manager mount-boot` mounted the ESP on the
source and the restored system; the judge held the whole ESP, 20 paths of
`EFI/com.solus-project`, `EFI/BOOT`, `loader/` and `solus-enroll-me.cer`, to
the source's bytes (0 differences); `sbverify --cert solus49-mok.der` passed
on the restored systemd-boot; the restored disk booted alone under Secure Boot
("SecureBoot enabled", LoaderInfo "systemd-boot 261.5"); the dumps matched (0
differences of 535 fields). The runs load with `--no-nvram`, so the restored
disk booted from the `EFI/BOOT` fallback (shim's fbx64.efi makes the entry). The
RAM-disk peak is 14.1 GiB (in rebuild), so ram-sizes.tsv says D 16, MEM 4; a
first try at D 10 was stopped over its reservation.

**BackShot1** (optional, S7): the Thinkpad is reinstalled with Solus 4.9 from
the GUI installer (ext4, Secure Boot as the Thinkpad has it), then the usual
run. Fixes, est.:

- `bs1-adopt`: `eopkg it openssh-server avahi`, and `clr-boot-manager
  mount-boot` before phase 1;
- `bs1-common`'s `package_list`: eopkg (`eopkg li -i`);
- `bs1-manifest` EXPECTED: nothing the boot stage writes beyond NVRAM.

A `bs1-qemu.py --virtio` rehearsal on `solus49` on both hosts comes first.

**S7's plumbing** (2026-11-15, not run: no rehearsal, no Thinkpad step):
- `bs1-adopt`: a SOLUS package column, `eopkg it -y` (on failure `eopkg ur`,
  a whole-system `eopkg up -y`, then again: Solus takes no partial update),
  for openssh-server, avahi, and python3, util-linux and efibootmgr only if
  missing, as for the other systems. Before anything is installed it writes
  `/etc/systemd/system-preset/50-backshot1.preset` (enable sshd.service,
  avahi-daemon.service, avahi-daemon.socket), so usysconf's preset run keeps
  them on. No mokutil: BackShot1 never calls it.
- `bs1-common`: `mount_cbm_esp`, `clr-boot-manager mount-boot` when /boot
  isn't mounted, then /boot must be on the internal disk. `bs1-backup` runs it
  in the preflight and phase 1 before the coverage check and the dump, and
  `bs1-verify` as a check before its dump and file tree. `package_list` reads
  `/var/lib/eopkg/package`'s NAME-VER-REL directories, as bko-holt's
  `eopkg_packages` does, instead of `eopkg li -i`: each line then carries
  the version and release, and no tool's output format is involved.
- `bs1-manifest`: `SOLUS_KEPT`, HoltTest1's set (`boot/EFI/**`,
  `boot/loader/**`, `boot/solus-enroll-me.cer`) held by content, size and
  type, on a baseline with CBM's shim or a Solus kernel; such a baseline must
  hold SOLUS_REQUIRED and a kernel, an initrd and an entry on the ESP.
  `boot/loader/random-seed` is held for presence and type only
  (`SOLUS_PRESENT`): systemd-boot refreshes it at every boot, and BackShot1
  compares after the restored system's first boot. It must exist as a regular
  file, but its content, size and mtime may move. HoltTest1 compares the
  offline tree before any boot, so judge.py keeps holding its bytes.
- `bs1-qemu.py`: no change. It has no guest list, and its firmware has no
  Secure Boot, so `solus49`'s `--mok` goes unused. The guest's store isn't
  copied (no `--no-secure-boot`), and shim's fbx64.efi makes the entry.
- README: a Solus section. (with the other distro sessions)

Per the notes folder's `elementary-shared-lines.md` and `biglinux-files.md`:

- `osrel.rs`: Solus gets its own `SUPPORTED` row and appends to the
  `supported_names` assert line (re-read it first).
- `boot.rs` `PACKAGES` and fingerprint.py: own lines.
- `redact.rs`: `SYSTEMS` gains `solus`, plus `GENERIC_LABELS` (`EFI`, `ESP`)
  if leakscan flags them.
- `load/commands.rs`: the new Solus arm.
- `installed_packages`: a new arm.
- Neither `update_grub()`'s list nor `make-from-live.sh` is touched.

Test2 waits for the batched pass. A hold note goes in the notes folder before
each shared edit.

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| S1 | Fetch the Xfce ISO, `.sha256sum`, `.sha256sum.sign` and Solus's releng key into rebuild-inputs on Chopin (3.8 GB, Decision 5), and verify them. Read the ISO offline: os-release, the live boot (label, kernel, `root=live:`), Calamares's `settings.conf` and modules, the default swap choice, the ESP label, the eopkg DB layout, CBM's and shim's package names. Then write `make-solus.sh` and `solus/install.sh` by Decision 1's path and build `solus49` in /mnt/bko-ram under `cmp-run.sh --reserve 10 4` (WEAR_TAG `build:solus49`). Enroll the MOK, add the `rebuild-one.sh` line, copy to Cinqcent with the same SHA-256, write vm-update's `Linux.eopkg` and the vm-mount ESP rule, and run `vm-update --try`. Settle every *check* and write "What S1 found" here. Done when the guest boots alone under Secure Boot on both hosts and `--try` is ok | 1.0 d |
| S2 | bko-holt (High-effort subagent): <br>• the `SUPPORTED` row and its tests;<br>• `eopkg_packages()` and its test;<br>• `PACKAGES` and fingerprint.py;<br>• dump's CBM ESP finding and the refusal while `/boot` isn't mounted, with `in_fstab: false`;<br>• `detect_loader` for shim by content or CBM's names, and `boot.loader.manager`;<br>• `prebuilt` for `initrd-com.solus-project.*`;<br>• `bootloader_id`;<br>• load's Solus arm (no bootctl, only `efi_entries`, the notes) and the BIOS refusal;<br>• Solus fake trees in `scenarios.rs` (mounted, unmounted, the fallback-only BootCurrent).<br>Done when `cargo test` passes, and the scenario's plan has no `bootctl` and no dracut, but has the entry and the notes | 0.75 d |
| S3 | The golden plan from a live dump (after `mount-boot`), and the Test2 registrations: classes, specs, estimates, counts, `S-eopkg`/`L-cbm`/`K-cbm`, p2 goldens, in the batched pass. **Test2 registered 2026-11-15** (uncommitted, solus49 alone, ahead of the batched pass): classes.json (holt.families.Obscure, holt.uefi), holt/spec.json (UEFI, nvboot-efivars.sh), guest/spec.json (pm `eopkg`, S-eopkg, L-cbm, K-cbm, B-lin, M-lin, uefi-sb, harness make-solus.sh, solus/**, in-guest.sh#Linux.eopkg, boot-state.sh, new-guest.sh), known-lines.json's `eopkg` section (S1's install log and the `--try`'s update.log), estimates.tsv (stic 410 s from S4, holt as aerynos's with S5's 14.1 GiB RAM-disk peak as gb_disk, guest 2.0 gm), plan.py's and the README's counts (+1 source, +1 guest), world.py, the tests' counts, the p2 goldens re-blessed (only solus49's lines) | 0.4 d |
| S4 | SticTest1 entry and runs: both archivers, both hosts, 0 FAIL | 0.35 d |
| S5 | HoltTest1 1Obscure runs on both hosts with `SOLUS_KEPT`, the signature check, the boot alone under Secure Boot, RAM peaks in `ram-sizes.tsv`, one `vm-update --try` on the restored copy (CBM's first run after a restore), and the GuestTest1 grade | 0.4 d |
| S6 | Docs: holt/README.md's supported list (and the "ESP out of fstab" step for Solus users), `osrel.rs`'s comment, HoltTest1's 1Obscure row and HoltTest1All, SticTest1's and Test2's READMEs, `layload.md`'s boot stage (the CBM arm), memory (the holttest1d index, a solus-support note) | 0.1 d |
| S7 | *Optional, later:* BackShot1 on the Thinkpad: the fixes above, the rehearsals, then the run | 0.5 d + ~1 h of the user's time |
| | *≈ 3.0 d without S7, ≈ 3.5 d with it* | |

Each piece is committed on main when its tests pass and the user says to
commit. Nothing is pushed until the user says so.

**Costs** (est.):

- S1: a 15-30 min build on Chopin, then the copy. The qcow2 is ~5-7 GiB per
  host, and the ISO 3.8 GB per host.
- S4 and S5: about 1 h of queue per host.
- None of the user's time but the OKs, until S7.

## Risks

- **No unattended installer.** install.sh replays Calamares's modules, so it
  can drift from what Calamares does. S1 compares the result to the modules'
  config and records the differences. The CBM step is CBM itself, so the boot
  side is faithful.
- **The ESP out of fstab is a trap for users too.** A user who backs up a
  Solus machine with plain `bko backup` (not bko-holt) gets no kernels and no
  loader. S2's refusal covers bko-holt. Whether `bko backup` should warn too
  (a source on Solus with an unmounted ESP) is left as a follow-up, not
  in scope.
- **Shim recognised by contents.** If reading `.sbat` is costly or unreliable,
  CBM's fixed names are the fallback. A future CBM could rename them; the
  byte-identical check in HoltTest1 catches a change.
- **The move to AerynOS tooling.** If Solus switches to moss and blsforme,
  most of this changes. AerynOS's support is the landing place then. A rolling
  release changes under the guest weekly ([[images-take-os-updates]]).
- **CBM never runs in load.** A restored machine keeps the ESP's kernels until
  its next `eopkg up`. If a restored machine's `/usr/lib64/kernel` and ESP ever
  disagree (both are restored from one backup, so they shouldn't), it boots
  the ESP's. S5's `--try` on the restored copy proves CBM's first update works.

## Out of scope

- BIOS installs (refused with a message, Decision 2), btrfs and f2fs roots,
  LUKS. The code paths are shared with other guests; there are no Solus
  guests for them.
- Solus 4.x before Calamares (os-installer's LVM `SolusSystem` layout).
  Refused by layout only if it turns out to break; no guest.
- Running `clr-boot-manager` in the rescue system, or enrolling a MOK from bko.
- arm64; BackShot2 cases on Solus; changes to the rescue image.

## Decisions for the user

1. **How the guest is built:**
   - `make-solus.sh` running install.sh in the live ISO under QEMU, which
     replays Calamares's modules and runs CBM with real efivars
     (recommended: faithful boot side, rebuildable, as AnduinOS and AerynOS
     are built);
   - or `make-from-live.sh` with a new dmsquash family on the host. CBM's
     image mode then writes a different ESP; est. +0.25 d;
   - or a hand install kept as a pinned image.
2. **Which guests:** one, `solus49` (UEFI, Secure Boot on, ext4, the
   installer's defaults), with BIOS refused by load (recommended: BIOS goes
   through CBM's GRUB2 backend, which is new code for a rarely used path);
   or also a BIOS guest and CBM's GRUB arm (est. +0.75 d).
3. **The ESP out of fstab, at dump:**
   - dump refuses until `/boot` is mounted, and says the command
     (recommended: the backup that follows needs the files mounted too, and a
     refusal can't be missed);
   - or dump mounts it read-only itself for the dump and leaves it mounted
     for the backup (convenient, but bko-holt dump would change the system's
     mounts);
   - or a warning only, as for fstab ESPs (could be missed: the backup
     would lack the kernels).
4. **The boot stage:**
   - keep the restored ESP whole (shim, systemd-boot, kernels, entries) and
     make only the NVRAM entry (recommended: as Fedora's and AnduinOS's arms
     do; nothing unsigned is written);
   - or run `clr-boot-manager update` in the chroot. That needs `/boot`
     bound to the target's ESP and the rescue's `LoaderDevicePartUUID`
     hidden, and it risks image mode's plain systemd-boot on the fallback.
5. **The ISO:** download the Xfce 4.9 ISO (~3.8 GB) with its sums and
   signature, and verify it against Solus's releng key, pinning the
   fingerprint and the SHA-256 in the script (recommended); or the user
   downloads and checks it.
6. **vm-update:** `eopkg up -y`, with usysconf running CBM on the booted
   guest, then `eopkg dc`, so the image keeps CBM's three kernels and no
   package cache (recommended); or keep the cache.
7. **HoltTest1's group:** **1Obscure** (recommended: no family it belongs
   to, like AerynOS's lineage but not Arch); or 1A beside AerynOS (Solus is
   heading to AerynOS's tooling); or a new **1S**.
8. **BackShot1 on the Thinkpad (S7):** later, as its own step after S1-S6
   pass (recommended: it needs the Thinkpad reinstalled, about an hour of the
   user's time, and the Thinkpad is in use for BackShot1All); or now; or not
   at all.
