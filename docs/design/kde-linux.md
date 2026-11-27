# KDE Linux support (sysupdate, erofs `/usr` files, UKIs)

2026-11-27. Asked for by the user: prepare support for KDE Linux, KDE's own
immutable distribution: a read-only OS image, systemd-sysupdate with up to
four versions side by side, unified kernel images (UKIs) and systemd-boot.
It is written the way Vanilla OS (docs/design/vanillaos.md, ABRoot A/B
roots) and Bazzite (docs/design/bazzite.md, ostree) were, and borrows
PikaOS's (docs/design/pikaos.md) LUKS guest and bootstrap-from-the-stick
pattern. GNOME OS (docs/design/gnome-os.md, a sibling spec written the same
week) shares most of the new machinery; "Shared lines" says what is built
once. **Approved by the user 2026-11-27, every recommendation (the last section); building waits until the user says to proceed.**

**Conventions.** est. marks an estimate. Every KDE Linux fact carries where
it comes from:

- **[seen]**: read from the real image on Chopin on 2026-11-27,
  `kde-linux_202611270255.iso` (8,227,284,992 bytes, SHA-256
  `f3e253ae84c141aa6b2f07129b34d41b3715a17be3ba66254f0060d5b9332baa`,
  matching upstream's signed `SHA256SUMS`), loop-mounted read-only, never
  booted. The ISO and both `SHA256SUMS` files (with their `.gpg`) are kept
  in `chopin:/bkoscratch/rebuild-inputs/kdelinux/`.
- **[src]**: the `kde-linux/kde-linux` repository at master **645a4bd9**
  (2026-11-27), https://invent.kde.org/kde-linux/kde-linux, with a path in
  it; also `kde-linux/etc-mstack` and `kde-linux/kde-linux-sysupdated`.
  Where the image and the source were both read, they agree.
- **[doc]**: a page under https://linux.kde.org/docs/ (install,
  install-vm, background, updates, boot-failure-recovery, snapshots,
  override-usr, more-software), read 2026-11-27.
- *check*: not seen anywhere, assumed or inferred; settled on the first
  guest (K1) before the code that depends on it.

When the sources disagree, the image wins, then the code, then the docs.
Some docs are known to be stale against RootFSv3 and etc-mstack, so the
spec is not to be "corrected" from them: background says "/home is a Btrfs
subvolume" and that "Btrfs snapshots of /etc are generated automatically",
and boot-failure-recovery's `/etc` rollback (`mv /etc /etc.broken; btrfs
subvolume snapshot /.etc.* /etc`) predates etc-mstack. In v3 `@system/home`
is a plain directory ([src] `subvol/main.py:255-259`; `rootfs-transition:27-36`
migrates a subvolume home away as v2-to-v3).
Code references to this repo are to local `main` at d450b2d. Nothing in the
repo was changed for this spec.

## Summary

- **KDE Linux is Alpha** ("KDE Linux is Alpha software", [doc] the home
  page), built daily, and upstream keeps only the **4 newest daily
  builds** plus any protected versions ([src] `upload-vacuum-v3/main.go:215-237`;
  `testing/` held 202611240254 to 202611270255 on 2026-11-27,
  [seen] the bucket listing). A build we test must be mirrored by us, or it
  is gone in four days. Decision 2 asks for the second version now.
- **It is not A/B partitions.** One btrfs partition (GPT type root-x86-64,
  optionally LUKS2) holds `@system` (mounted as `/`) and, at the btrfs top
  level, up to four **erofs image files** `kde-linux_<VER>.erofs`, one per
  version, each ~7.9 GB. The ESP holds one UKI per version in `EFI/Linux`.
  The initrd loop-mounts the erofs file whose version is baked into its own
  UKI as `/usr`. `/etc` is an overlay ("etc-mstack") whose upper is a btrfs
  subvolume per version. No fstab, no crypttab, no `root=`.
- **The backup is the btrfs top level plus the ESP.** The running system
  already mounts the top level at `/system` ([seen] the mount generator),
  so every image file, every `/etc` layer and `@system` with its nested
  subvolumes is under one real mount. The erofs `/usr` and the `/etc`
  overlay are views, dropped as aliases.
- **The boot stage regenerates nothing.** The UKIs (kernel, initrd and
  command line in one PE file), systemd-boot and `loader.conf` come back
  byte for byte with the ESP. The stage checks that every UKI's version has
  its image file, makes the NVRAM entry, and writes its log. No chroot: the
  restored tree has no `/usr` until the initrd mounts one.
- **Everything is found by GPT type, not UUID.** The GPT is restored exactly
  today (types, PARTUUIDs, names, attribute bits), so discovery keeps
  working.
- **Two guests,** `kdelinux` and `kdelinux-luks`, installed by our own
  script that replays the Calamares installer's steps, run from the
  **rescue stick image** in QEMU. The same script installs the A100 from
  the real stick for **BackShot1**, with a new drive-unlock **family U**
  (the drive's key passed by a UKI command-line addon).
- ~9.3 d (est.) with the LUKS guest and BackShot1; ~8.55 d if GNOME OS
  lands the shared sysupdate pieces first.

## Why

- **The fourth immutable design.** ostree has one root and a repository.
  ABRoot has two whole root filesystems. GNOME OS has two `/usr` block
  images on partitions. KDE Linux keeps its versions as **files** on the
  writable filesystem. No guest tests a system whose `/usr` is a loop
  mount of a file.
- **A version is a matched pair.** UKI `kde-linux_V*.efi` on the ESP and
  `/system/kde-linux_V.erofs` on btrfs. A restore that loses either half,
  or renames one, leaves a menu entry that cannot boot: on v3 a missing
  image file fails the initrd's `sysusr-usr.mount` (`What=/sysroot/system/kde-linux_${IMAGE_VERSION}.erofs`,
  required by `initrd-usr-fs.target`, [src] `kde-linux-mount-generator`;
  *check* on K1 whether that drops to the emergency shell or reboots).
  (`rootfs-transition`'s "does not exist on root partition" reboot is the
  v1/v2 migration path only; on v3 it exits at line 41 before that test.)
- **Nested btrfs subvolumes nobody mounts.** Each `/etc` layer, `@system`
  itself, `@system/etc`, `@system/.etc.<epoch>` (etc-mstack's migration
  snapshot), every `/home/<user>` and its snapper snapshots are
  subvolumes, none of them a mount point except `@system` and the top
  level. Tools that follow fstab or the mount table miss them.
- **Nothing can be rebuilt.** The initrd is inside the UKI; there is no
  package manager (pacman is deleted, [seen]); `sudo` is a shim for `run0`.
  Every tool BackShot1 needs must already be in the image.
- **KDE's own distribution.** neon's spec (docs/design/kde-neon.md) names
  KDE Linux as out of scope there, "nearer Bazzite's". This is that spec.

## What KDE Linux is (as far as bko cares)

| | KDE Linux `testing`, build 202611270255 | Nearest supported system |
|---|---|---|
| Status | Alpha since Akademy 2025 [doc background]; "85% of the way" to Beta (https://blogs.kde.org/2026/11/01/october-2026-in-kde-linux/). Daily builds, the last 4 kept [seen]. A parallel `testing-buildstream` edition on the FreeDesktop SDK base (~8.96 GB) is experimental; whether it replaces the Arch base is undecided (https://invent.kde.org/kde-linux/kde-linux/-/work_items/727) | Aeon (rolling, immutable) |
| os-release | `NAME="KDE Linux"`, `ID=kde-linux`, no `ID_LIKE`, `VERSION_ID=202611270255`, `IMAGE_ID=kde-linux`, `IMAGE_VERSION=202611270255`, `VARIANT_ID=testing`, `KDE_LINUX_SYSUPDATE_ENDPOINT=sysupdate/v2`, `DEFAULT_HOSTNAME="$-????"` [seen] the UKI's `.osrel`, equal to `/usr/lib/os-release` | AerynOS (rolling, `*`) |
| ISO | One file, both an El Torito ISO and a GPT disk (`systemd-repart --el-torito=true`). p1 `esp` 280M vfat (the live UKI only), p2 `KDELinuxLive` type root-x86-64 with GPT attribute bit 59 (growfs), an erofs **byte-identical to the update's `kde-linux_V_root-x86-64.erofs`** (both SHA-256 `ef8ad47d...`), p3 `KDELinuxLiveExt` erofs, the live sysext with Calamares 3.4.3 [seen] `sfdisk -d`, `blkid`, `sha256sum` | none |
| Signature | `SHA256SUMS.gpg`, EdDSA, key `15AB3EE61CE450CFED1407BF4121B5F4EAEB53CA` "KDE Linux <linux@kde.org>"; `gpgv` with the image's own `/usr/lib/systemd/import-pubring.pgp` says Good signature [seen]. That check is circular: the key comes from the image it checks (open question 3) | |
| Hardware | UEFI only, x86-64 only, ≥2 GB RAM, ≥50 GB disk, "a whole disk"; **Secure Boot must be off** ("until KDE Linux supports it", issue #3 open) [doc install]; a shim is in the image, unused [seen] (*check* its path on K1) | PikaOS (SB off) |
| Disk | GPT: ESP label `ESP`, recommended 4098M, minimum 1024M; one partition named `KDELinux`, type **root-x86-64** (`4f68bce3-e8cd-4db1-96e7-fbcaf984b709`), btrfs, the rest of the disk, minimum 20G. No swap partition (zram only) [seen] `partition.conf`. The installer's `subvol` module forces the type GUID with `sfdisk --part-type`, onto the LUKS partition when encrypted [seen] `subvol/main.py` | |
| btrfs top level | Mounted at **`/system`** (`subvol=/`, `rw,nodev,suid,exec,compress=zstd:-4`, mode 0700, unit `sysroot-system.mount`). Holds `kde-linux_<VER>.erofs` (1 to 4 regular files, read-only by sysupdate), `kde-linux_<VER>.erofs.caibx` (desync indexes), subvolume `@system`, the directory `etc.mstack.v/`, and maybe `@kde-linux_debug_<VER>` [seen] `kde-linux-mount-generator`, "RootFSv3" since 2026-08-09 [src] e41e0676 | none |
| `@system` | `/`, by `rootflags=subvol=@system` on the UKI's command line. `/var`, `/root`, `/opt/local` are plain directories in it. `@system/etc` is a subvolume (the installer's `/etc`; after the first boot a hidden pre-migration copy, kept). The first etc-mstack migration also makes **`@system/.etc.<epoch>`**, a read-write btrfs snapshot of it (one per migration) [src] `etc-mstack` `main.rs:9-12,309-330`. `@system/home` is a directory, each `/home/<user>` a **subvolume** (`BTRFS_SUBVOLUME_HOME=yes`) with snapper's `.snapshots` inside [seen] generator, `subvol/main.py`; [src] `/etc/default/useradd` | CachyOS, Tumbleweed (snapper) |
| `/usr` | `/system/kde-linux_${IMAGE_VERSION}.erofs` loop-mounted `ro,X-mount.subdir=usr` at `/sysusr/usr`, then bound onto `/sysroot/usr`. **IMAGE_VERSION comes from the initrd's own os-release**, so each UKI boots exactly one image file. No dm-verity, no `usrhash=`, no fs-verity: integrity is checked only at download [seen] generator | none |
| `/etc` | Overlay ("etc-mstack", landed 2026-11-16 [src] bad1b621): lower `etc.mstack.v/etc_<VER>.mstack/layer@0`, a symlink to `/sysusr/usr/share/factory/etc`; upper `.../rw/data`; work `.../rw/work`; options `redirect_dir=nofollow,uuid=on,metacopy=off,userxattr,index=off` [seen] generator. Each `etc_<VER>.mstack` is a btrfs subvolume snapshotted forward from its origin version, recorded in `etc.mstack.v/origin-for_<VER>.os-release` by a sysupdate notify hook. The first migration from a plain `/etc` snapshots it to `@system/.etc.<epoch>` and copies the differing files into the new mstack, keeping the originals [src] `etc-mstack` | Vanilla's `/etc` overlay (but `user.overlay.*`, not `trusted.overlay.*`) |
| Quotas | btrfs **simple quotas**, `btrfs quota enable --simple /system`, run by `kde-linux-btrfs.service` each boot and by the installer [seen] | none |
| Boot | systemd-boot 262.1 (`EFI/BOOT/BOOTX64.EFI` = `EFI/systemd/systemd-bootx64.efi`), `loader.conf` `timeout 7`, **Type #2 only** (no `loader/entries/*.conf`). The installer's `shellprocess@uki` (`dontChroot: false`) runs `cp -rfv /usr/share/factory/boot/* /boot/` then `bootctl install` in the target's chroot (NVRAM entry, `loader/random-seed`). ESP mounted at **`/boot`** by gpt-auto (no fstab), through `boot.automount` with an idle timeout (gpt-auto's way; *check* on K1) [seen] factory tree, `shellprocess@uki.conf` | Aeon (systemd-boot), but no sdbootutil |
| UKI | `EFI/Linux/kde-linux_<VER>.efi`, ~253 MB: systemd-stub 262.1, kernel `7.2.11-arch1-1`, mkinitcpio initrd (`HOOKS=(base systemd modconf kms keyboard block sd-encrypt filesystems fsck systemd-extension plymouth microcode sd-shutdown)`, no `autodetect`). Command line: `rw rootflags=subvol=@system,compress=zstd:-4,lazytime lsm=... quiet splash plymouth.ignore-serial-consoles loglevel=3` and more, **no `root=`, no `console=`** [seen] `objdump`, `.cmdline`, `.uname`, `.sdmagic`, the initrd's `etc/mkinitcpio.conf`. A 3 KB `erofs.addon.efi` is published per version as a "Mock artifact for upgrades", for older systems, but no transfer installs it; per-UKI addons appear only through the developer helper `addons.sh` (debug, bootchart) [src] `rebuild-efi:79-81`, `build.sh:151,240-241`, `addons.sh:7-29`, the three `.transfer` files. The factory boot tree carries an empty `EFI/Linux/kde-linux_V.efi.extra.d` (mode 0700, named without the counter, [src] `build.sh:145`), restored with the ESP | ZFSBootMenu's UKI (`read_uki`) |
| Boot counting | The installer's UKI is `kde-linux_V+3.efi`; sysupdate installs `+2-0` (`TriesLeft=2 TriesDone=0`) [seen] `60-esp.transfer`. A good boot is blessed by `kde-linux-bless-boot.service`, triggered by its `.path` unit on **either** `/run/kde-linux-bless-userspace` or `/run/user/*/kde-linux-bless-session`. The first is touched by `kde-linux-bless-userspace.service`, statically required by `systemd-bless-boot.service`, with `Requires=boot-complete.target display-manager.service user.slice`: so a boot is blessed once the display manager (plasmalogin) is up, no session needed. `auto-hide-bootloader hide` runs as the bless unit's `ExecStopPost` (`Conflicts=shutdown.target`), so `LoaderConfigTimeout` 0 is set **at the first clean shutdown**, and only if the bless succeeded and no other OS entry exists [src] `kde-linux-bless-*` units, `usr/lib/bless-userspace`, `usr/lib/auto-hide-bootloader`. *Check* that plasmalogin starts (and so blesses) on a headless guest under `multi-user.target` | Aeon (`+L-D.conf`) |
| Initrd | systemd, `systemd-gpt-auto-generator` (finds the root partition **on the disk the loader booted from**), `systemd-cryptsetup-generator` with tpm2 and fido2 token plugins, `kde-linux-mount-generator`, btrfs, erofs, overlay, loop, dm-crypt, nvme; empty `etc/fstab`, no crypttab [seen] | AerynOS (family B) |
| LUKS | Calamares' "Encrypt system" box, LUKS2 (`--sector-size 4096 --key-size 512 --hash sha512`, issue #503). Passphrase only: no TPM2, FIDO2 or recovery key enrolled; no `systemd-cryptenroll` anywhere in the repo [src]. Unlocked by gpt-auto at a Plymouth prompt (*check*). The prompt's keymap is the UKI's, so a non-US layout may fail (https://invent.kde.org/kde-linux/kde-linux/-/issues/303) | AerynOS |
| Updates | systemd-sysupdate, three transfers in `/usr/lib/sysupdate.d/`: the erofs file and its `.caibx` to `/system`, the UKI to `PathRelativeTo=boot` `/EFI/Linux`; `InstancesMax=4`, `ProtectVersion=%A`, GPG on [seen]. `kde-linux-sysupdated.socket` (enabled) is a local proxy that rebuilds the new erofs from chunks already on disk. **No timer**: updates are manual, by Discover or `updatectl update` (issue #375) [seen] presets end `disable *`. Rollback: pick an older UKI at the menu (hold Space) [doc boot-failure-recovery]. On 2026-10-29 an infrastructure change (the S3 storage move) broke delta updates for installs from before 26 October ("Protocol Error"); the doc's workaround is `systemctl stop kde-linux-sysupdated.socket`, then `updatectl vacuum && updatectl update` [doc updates] | uupd/abroot, but nothing automatic |
| Install | Calamares 3.4.3, exec sequence `partition, mount, subvol, machineid, displaymanager, hwclock, shellprocess@uki, umount`; **no users, fstab or bootloader module**. The user is made at first boot by **plasma-setup**; the installer leaves `/var/lib/kde-linux/installed-with-plasma-setup`. [seen] `settings.conf`. "Erase disk" is "the most reliable option"; others "may work" but are known to fail [doc install]. No unattended mode found [src] | |
| Access | root locked (`passwd --lock root`); pkexec removed, `/usr/bin/sudo` a symlink to the non-SUID `run0-sudo-shim` [src] `mkosi.finalize.d/40-core.sh.chroot:43-70`; `run0` and polkit; users get `wheel` and zsh. **sshd is in the image but not enabled**; avahi-daemon enabled, nss-mdns wired; NetworkManager [seen] presets, `/usr/bin/sshd` | GNOME OS (run0) |
| Software | Flatpak (seeded from `/usr/share/factory/var`), distrobox, toolbx, podman, docker, incus, systemd-sysext (`~/kde/usr`, [doc override-usr]); Homebrew and Nix documented. Present: btrfs, cryptsetup, snapper, rsync, python3, bootctl. Absent: pacman, zfs [seen]. efibootmgr, ukify and the addon stub: *check* | |

**A fresh install has one version.** Calamares' `subvol` copies the live
p2 erofs byte for byte to `kde-linux_${IMAGE_VERSION}.erofs` ([src]
`subvol/main.py` `_copy_image`, 262-292). The UKI comes separately:
`shellprocess@uki` copies the erofs's own `usr/share/factory/boot` tree,
whose UKI was named `kde-linux_V+3.efi` at build time ([src] `build.sh:60,186`),
not the ISO's live ESP (whose UKI has no counter). The other three "slots" are
empty until the first `updatectl update`. A dump of such a system is
faithful with one version; the guests are given a second (below).

## What works today, and what breaks

**`bko-holt dump`:**

- `osrel.rs` `SUPPORTED` (`holt/src/osrel.rs:~165-327`) has no
  `kde-linux` row, and `ID_LIKE` is ignored (`check_supported`,
  osrel.rs:350): dump refuses KDE Linux. That is the safe default.
- **`/usr` breaks the layout.** It is an erofs on `/dev/loopN`, and
  `PSEUDO` (`holt/src/mounts.rs:72-104`) has `overlay` and `squashfs` but
  not `erofs`. Dump would list it as a layout mount, and load would refuse
  it ("erofs isn't a filesystem bko-holt load can make",
  `load/commands.rs:4264-4270`). Loop devices already carry their
  `backing_file` (`blockdev.rs:99,233`), which here is
  `/system/kde-linux_V.erofs`.
- **The `/etc` overlay** is dropped as pseudo, which is right: its upper is
  under `/system`.
- **The top level at `/system` and `@system` at `/`** are both real btrfs
  mounts of the same filesystem. The nested subvolumes (each mstack,
  `@system/etc`, `@system/.etc.<epoch>`, each home, each snapshot) are
  listed by the btrfs probe like any subvolume (*check* on K1's dump). But
  the generic `backup_sources` (`holt/src/report/sources.rs:60-71`) emits
  `@system` at `/`, while this spec's `/` is `/system`; two mounts on one
  path are settled by "the later one wins", and `alias_of`
  (`mounts.rs:130-146`) drops neither, both being subvolume roots. K2
  replaces the generic entries on KDE Linux (below).
- **The ESP at `/boot`** is mounted by gpt-auto through `boot.automount`
  with an idle timeout. While mounted it is in the mount table and
  `esp_inventory` (`holt/src/boot.rs:311-346`) lists its files; once the
  timeout expires the table shows only `autofs` at `/boot`, which is in
  `PSEUDO` (`mounts.rs:72-104`), and nothing in holt/src touches `/boot` to
  trigger it (only `parttable.rs` mentions "automount", as a GPT bit), so
  `esp_inventory` and `backup_sources` would miss the ESP.
  `systemd_boot_json` (`boot.rs:443-520`) reads `loader.conf` and Type #1
  entries only: the UKIs are listed but not understood. `read_uki`
  (`holt/src/zfsbootmenu.rs:163-182`, the struct at 100-110) reads
  `.osrel`, `.cmdline`, the kernel version (from `.linux`'s bzImage
  header; there is no `.uname` field) and `.sdmagic`, but returns None for
  any PE without both `.linux` and `.initrd`, so it cannot read a
  command-line addon.
- **No ZFSBootMenu mix-up:** `zbm_why` (zfsbootmenu.rs:252-279) needs a
  ZFS boot environment for a UKI without `root=`.
- **Packages:** `/var/lib/pacman/local` is absent (the db is deleted),
  so the package list is empty, which is true. The loader is still found
  by `LoaderInfo` (`boot.rs:1288`).
- **Initramfs:** `initramfs_json` (`boot.rs:1028-1079`) picks the
  generator from the package list (`has_package`), which is empty here, so
  it reports none; it must say `prebuilt` (in the UKI). (bs1-luks's
  `lk_generator`, below, does go by command presence.)

**`bko-holt load`:**

- **Mount stage:** fstab-driven (`btrfs_plans`); there is no fstab. It
  needs a layout-driven tree, as ABRoot's `part-future` and `boot/init`.
- **Gate:** `restored_marker` (`holt/src/other/mod.rs:328-345`) defaults
  to `etc/fstab`, and `status.rs:382` tests `etc/fstab` and
  `etc/os-release`. Neither is at the top of the restored tree.
- **Boot stage:** the generic stage (`boot_stage`,
  `load/commands.rs:6079-6094`) chroots into the target, which has no
  usable `/usr` (an empty mount point), so every chroot fails. The
  `("systemd-boot", true)` arm (commands.rs:6521-6531) would run
  `bootctl install --no-variables` over the image's own systemd-boot.
- **GPT works as is:** `writetable.rs` and `parttable.rs:557-596` keep
  type GUIDs, PARTUUIDs, names and attribute bits 59/60/62/63, so
  gpt-auto finds the restored root and ESP.
- **LUKS:** the generic path makes a new header with `luksFormat`, the
  source's UUID and label. With a passphrase-only volume that loses
  nothing. It already passes the probed sector size and key size
  (`load/commands.rs:2310,2347,2350`; `probe/luks.rs:125,147`).
  There is no crypttab, so whatever in load expects one for the volume's
  name (`root`, gpt-auto's) needs a layout-driven path.
- **NVRAM:** `efi_entries` (commands.rs:8034) makes `Linux Boot Manager`
  `\EFI\systemd\systemd-bootx64.efi`. Right as is.
- **EFI variables** are recorded (`efi.rs:436-456`) but not restored:
  `LoaderConfigTimeout` (the hidden menu) and `LoaderSystemToken` are lost,
  both harmless (below). So is `LoaderEntryDefault`, which is not: if the
  user ran `bootctl set-default` (or a GUI did), the restored machine boots
  the newest version, not the recorded default (below).

**rustic-bko and restic-bko:**

- The erofs files are **regular files** of ~7.9 GB, read-only
  (`ReadOnly=yes` in the transfer: *check* whether that is a mode or the
  btrfs `+i`/read-only property). Content is exact either way.
- The `/etc` uppers use **`userxattr`**: whiteouts are char 0:0 and the
  overlay's marks are `user.overlay.*` xattrs, not `trusted.*`. Vanilla's
  V6 showed both archivers keep `trusted.overlay.*` and whiteouts; `user.*`
  is the easier namespace. *Check* in K6.
- **Nested subvolumes** have their own `st_dev`. A backup that stays on
  one filesystem by `st_dev` stops at each one. *Check* what bko's backup
  of `/system` does with them, and that load makes each as a subvolume
  before it fills it (load already makes every subvolume in the layout,
  `load/commands.rs:5891`).
- **Dedup between versions:** consecutive dailies share most chunks
  (sysupdated's delta depends on it), but the erofs is zstd-compressed in
  64K pclusters. *Check* how much rustic's content-defined chunks find.

**Guest tools, SticTest1, HoltTest1, BackShot1:** none knows erofs files,
Type #2 UKIs, sysupdate or a btrfs top level as the tree; `bs1-adopt`
would try `pacman -Sy` on a read-only `/usr`; `bs1-luks`'s `lk_generator`
(208-222) would pick mkinitcpio and "remake" an initrd nothing boots.

## The design

### One tree convention: the btrfs top level

Every tool that backs up or mounts a KDE Linux system builds the same tree:

| Path in the tree | What | Found by |
|---|---|---|
| `/` | the btrfs **top level** (subvolid 5), raw | the mount at `/system` (running); the root-x86-64 partition (offline) |
| `/@system/boot` | the ESP | the vfat mount at `/boot` (running); the ESP partition type on the same disk (offline) |

So `/@system/etc` is the installer's `/etc` subvolume, `/etc.mstack.v/`
holds the real `/etc` layers, `/@system/usr` is an empty mount point, and
`/kde-linux_V.erofs` is each image. Restoring this tree restores exactly
what the next boot composes. Nothing is read through the overlay or the
loop mount. The running `/` (`@system`) is not a second source: it is
inside the top level.

**Who builds it.** As for ostree and ABRoot: on the running source, `dump`
writes `storage.backup_sources` with those two entries (`/system` is
mounted, so `from` is a string, no mount hint needed). On a disk, load and
vm-mount derive the same tree from the layout. A corpus test ties the two.

**Subvolumes inside the tree.** Every nested subvolume is in the layout
(path, read-only flag, parent, `generation` for snapshot order); load makes
them in path order before it restores files into them, and makes snapper's
read-only snapshots through the existing snapshot sharing
(`load/shared.rs`), as CachyOS and Tumbleweed do (Decision 6).

**Simple quotas** need a fix in load's existing path, not a new line. Load
already passes the source's `simple_quota` feature to mkfs as `-O squota`
(`load/commands.rs:3560`; the probe, `report/btrfs.rs:156`), but
`btrfs_quota()` then runs a plain `btrfs quota enable` (about 4707), which
turns on full qgroups, and with shared snapshots (Decision 6) that enable
is deferred past the restore (`if quota && plan.shared.is_empty()`,
4681-4684), when simple quotas would never count the extents already
written. K3 makes `btrfs_quota` squota-aware: when the source has
`simple_quota`, rely on `mkfs -O squota` alone, never run a full `quota
enable`, never defer anything past the restore, and still replay
higher-level qgroups and limits if any. A unit test covers it.

### Detection

KDE Linux is recognised by markers, not os-release alone:
`/usr/lib/sysupdate.d/50-root-x86-64-erofs.transfer` with
`MatchPattern=kde-linux_@v.erofs`, and `/usr` a loop mount whose
`backing_file` is `<top>/kde-linux_<IMAGE_VERSION>.erofs`. Then
`boot.sysupdate` is written, with a `kdelinux` part. Version 1 supports
**RootFSv3 with etc-mstack** only: a top level holding `@system` and
`etc.mstack.v/`. Older layouts (`@kde-linux_<V>` subvolumes,
`@etc-overlay`, `@home`; migrated by `btrfs-migrator` at boot) are
refused, as is any `VARIANT_ID` other than `testing` (Decision 1).

`SUPPORTED` gets `("kde-linux", "*", "KDE Linux")`, rolling like
`aerynos`. The marker check is what makes it safe: a layout we haven't
seen is refused by name, not loaded by guess.

### `dump`

- **`boot.sysupdate`**, a new section in `holt/src/sysupdate.rs`, built
  once with GNOME OS (Shared lines): the transfer files (source pattern,
  target path, `InstancesMax`, `ProtectVersion`, `TriesLeft`), the booted
  version (`IMAGE_VERSION`), and per version found on disk:
  - the UKI: file name, counter (`+L` or `+L-D` or none), size, SHA-256,
    `read_uki`'s `.osrel` `IMAGE_VERSION`, `.cmdline`, kernel version,
    stub;
  - any per-UKI addons in `<uki>.extra.d/` (the empty dir included) and
    global ones in `loader/addons/` (name, size, SHA-256, `.cmdline`,
    `.sdmagic`), read by a small new PE-section reader, since `read_uki`
    refuses a PE without `.linux` and `.initrd`;
  - the `kdelinux` part: the image file's name, size and read-only state,
    the `.caibx` file, `etc_V.mstack` present, `origin-for_V.os-release`
    present and its origin, each `@system/.etc.<epoch>`, the debug
    subvolume if any.
  Hashing four 8 GB files at dump time costs minutes; dump records sizes
  and leaves the hashes to the manifest and HoltTest1's judge (*check* the
  time on K1).
- `half_versions`: a UKI with no image file, an image file with no UKI.
  A dump with one is warned about; load refuses it unless the half is
  not the booted version (an update was interrupted: a note, kept as is).
- `efi.rs`'s variables as today, plus `LoaderConfigTimeout`,
  `LoaderEntryOneShot`, `LoaderSystemToken` present or not
  (`LoaderEntryDefault` is already read, `efi.rs:454`).
- **The ESP automount:** on a gpt-auto system dump stats `/boot/` (and
  `/efi/`) before it reads mountinfo, so an expired `boot.automount` is
  mounted again, and warns if `/boot` is still `autofs`. K2's fake tree
  has an autofs-only `/boot` case.
- **mounts:** `erofs` joins `PSEUDO`; a loop mount whose backing file is
  inside a recorded filesystem is an alias of that file (also for
  `/sysusr/usr`). The debug sysext bind at `/var/lib/extensions/debug`
  is a bind, dropped as today.
- **`storage.backup_sources`:** the two entries above, with a `note` that
  `/` of the tree is `/system`. On KDE Linux they **replace** the generic
  entries (`report.rs:1269`) rather than join them as ABRoot's do
  (`report.rs:1322-1333`, which only adds paths not already there): a
  test checks `/` comes from `/system` and no `@system` entry exists.
- **initramfs:** `generator: prebuilt`, `in: uki`, for each UKI; the arm
  keys on `boot.sysupdate`/UKI presence, not on `/usr/lib/kernel/*.initrd`.
- **The loader:** systemd-boot via `LoaderInfo`, with `type2: true` and
  the UKI list; the presence of `boot.sysupdate.kdelinux` is what makes
  load take the KDE Linux path.
- **`redact.rs`:** `KDELinux`, `ESP`, `KDELinuxLive` labels and the host
  pattern `kde-linux` are generic words; `DEFAULT_HOSTNAME`'s `$-????`
  makes host names like `kde-linux-a1b2` (*check*), redacted as hosts.

### `load`

- **Assemble and format:** the generic GPT, LUKS and btrfs paths (`mkfs
  -O squota` from the source's features, the squota-aware `btrfs_quota`
  above), then the subvolumes in path order.
- **Mount stage, layout-driven:** the top level (`subvol=/`) at the
  target, then the ESP at `@system/boot` (*check* that load's mount stage
  accepts a mount point inside a subvolume path; ABRoot's `part-future`
  was the same shape).
- **Gate and status:** `restored_marker()` returns `&'static str`
  (`other/mod.rs:328`, as abroot's `marker()`, `load/abroot.rs:53`), so it
  cannot name `kde-linux_<V>.erofs`. On KDE Linux it returns the fixed
  **`etc.mstack.v`** (every supported layout has it: Detection refuses one
  without), and status.rs (375-385) gets the same branch; the booted
  version's image and UKI are tested by the boot stage's check script
  (step 3). No signature change.
- **The boot stage,** an early `if let Some(k) = kdelinux::of(..) { return
  self.kdelinux_boot(..) }` beside ostree, abroot, snapcore and
  transactional (commands.rs:6083-6094):
  1. The atimes listed `if-missing` first.
  2. A note: the ESP, the UKIs, `loader.conf`, the image files and the
     `/etc` layers are restored as they were; no initramfs, no `bootctl
     install`, no chroot.
  3. A consistency check, a shell test the plan shows: for each
     `EFI/Linux/kde-linux_*.efi`, its name without the counter gives V,
     and `<top>/kde-linux_V.erofs` exists with the dump's size; the
     booted version's UKI and image both exist; each `etc_V.mstack` the
     dump had exists, with its `rw/data`, and each `@system/.etc.<epoch>`
     the dump had is a subvolume; `layer@0` is the symlink to
     `/sysusr/usr/share/factory/etc`; `EFI/BOOT/BOOTX64.EFI` and
     `EFI/systemd/systemd-bootx64.efi` exist. A mismatch fails the stage.
     Counter names are kept as they were, with a note when the booted
     version's UKI still has one (backed up before it was blessed: the
     first boot counts it down again).
  4. `efibootmgr` entries as today, unless `--no-nvram`.
  5. The log into `{t}/@system/var/log/bko-holt`.
  6. The generic tail (`boot_tail`, commands.rs:6921, and
     `readonly_sets`, 6070-6076: the snapshots' read-only flags).
- **Not restored, said in a note:** `LoaderConfigTimeout` (the restored
  machine shows the menu for 7 s until its first clean shutdown after a
  blessed boot hides it again), `LoaderSystemToken` (systemd-boot then
  doesn't credit the restored `random-seed`; harmless, open question 5),
  and `LoaderEntryDefault`: when the dump has one, the plan says so in a
  note (the restored machine boots the newest version, not that entry) and
  bs1-sysupdate warns at preflight. Restoring it through efivarfs from the
  stick is open question 9.
- **Plan problems on KDE Linux, v1:** a layout other than RootFSv3 with
  etc-mstack; a `VARIANT_ID` other than `testing`; a half version that is
  the booted one; BIOS; `--new-uuids` and `--fit-largest` (nothing names
  a UUID, so both could work, but nothing has tested them: refused until
  a test says so); an active systemd-homed home (homed is enabled by
  preset, *check* whether plasma-setup uses it); an image file whose size
  doesn't fit the target.

### LUKS

Passphrase-only, one LUKS2 volume holding the btrfs, typed root-x86-64,
no crypttab. The generic LUKS path applies with the source's UUID, label
and parameters (sector size 4096, key size 512, sha512; already passed
from the probe, `load/commands.rs:2310,2347,2350`). The volume is
opened as `root` (gpt-auto's name) for the restore. No TPM token is
enrolled by the installer, so a new header loses nothing; a user who adds
a `systemd-tpm2` token later is the GNOME OS case (keep the header,
`dump --keep-luks-header`), which this spec doesn't test.

## Guests

Two, UEFI, **Secure Boot off** (`vm-run-options`: `--uefi
--no-secure-boot --memory 4096`), disk **64G** sparse (the docs ask 50 GB,
two versions take ~16.5 GB), the ESP at the installer's 4098M. Inputs in
`chopin:/bkoscratch/rebuild-inputs/kdelinux/`, shared by both guests.

| Guest | Layout | What it covers |
|---|---|---|
| `kdelinux` | Calamares' "Erase disk": ESP + btrfs `KDELinux` root-x86-64, two versions (V1 installed from the older mirrored sysupdate set, V2 by sysupdate from the newer set), a `tester` user with a home subvolume and one snapper snapshot | the tree, image files, Type #2 UKIs, counters, etc-mstack across an update, nested and read-only subvolumes, squota |
| `kdelinux-luks` | the same with "Encrypt system": LUKS2 under the btrfs, typed root-x86-64; one version | gpt-auto LUKS with no crypttab, the generic LUKS path, the typed passphrase; the A100's layout |

**How they are built (Decision 3):** one script,
`testvm/kdelinux/install.sh [--luks KEYFILE] DISK IMAGE_DIR HOSTNAME`,
replays the installer's steps [seen] `subvol/main.py`, `shellprocess@uki`.
**IMAGE_DIR** holds either an ISO or a sysupdate set
(`kde-linux_V.efi`, `kde-linux_V_root-x86-64.erofs`, `.caibx`,
`SHA256SUMS`, `SHA256SUMS.gpg`). The set is enough: the installed UKI
comes from the erofs's own `usr/share/factory/boot` (the build-time
`kde-linux_V+3.efi`), never from the ISO's live ESP, and the erofs is the
same bytes as the ISO's p2. So the mirror keeps sets, and an ISO is only a
convenience for the hand-run Calamares diff.

1. GPT: ESP 4098M vfat label `ESP`; the rest named `KDELinux`, type
   root-x86-64. With `--luks`, `cryptsetup luksFormat --type luks2
   --sector-size 4096 --key-size 512 --hash sha512 --key-file KEYFILE`
   on it, opened as `root`.
2. `mkfs.btrfs`, mount with `compress=zstd:-4`, `btrfs quota enable
   --simple`, subvolumes `@system` and `@system/etc`, the directories
   `boot proc sys dev run usr home` in `@system`.
3. The erofs copied byte for byte to `<top>/kde-linux_V.erofs`: from the
   set's `_root-x86-64.erofs`, or the ISO's p2 by its GPT offset with
   `dd`; its SHA-256 checked against the signed sysupdate `SHA256SUMS`.
4. `@system` mounted at a work dir, the erofs (`X-mount.subdir=usr`) at
   its `usr`, the ESP at its `boot`; `/dev`, `/proc`, `/sys`, `/run` and
   efivarfs bound in. Then the same split as the installer, whose
   `subvol/main.py` "runs outside the chroot" (line 5) with the live
   system's tools and `--root=` (339-366): from **outside**,
   `systemd-machine-id-setup --root`, `systemd-sysusers --root`,
   `systemctl --root preset-all` (and `--global`), `systemd-tmpfiles
   --root --exclude-prefix=/usr --create`. The installer's live system is
   the same image, so its tools match; the stick's systemd may be older
   (*check* on K1 that it accepts the image's sysusers, presets and
   tmpfiles, else run those three in the chroot, equivalent for these
   `--root`-aware tools). The installer's `systemd-nspawn -D root true`
   (328-331) only triggers base_filesystem_create; install.sh runs it the
   same way if the stick has nspawn, else `mkdir`s the same tree (*check*
   by the K1 diff). Only `shellprocess@uki`'s part runs **in a chroot**
   (`dontChroot: false`): `cp -rfv /usr/share/factory/boot/* /boot/`,
   `bootctl install`. Then `touch
   /var/lib/kde-linux/installed-with-plasma-setup`.
5. Our own steps, which every guest gets: `sshd.service` enabled, root's
   SSH key and a generated password (root is locked by the image: *check*
   that a key login works for it under `UsePAM yes`, else unlock it), the
   `tester` user, `multi-user.target` as the default (plasma-setup's
   first-boot GUI skipped: *check* how it is gated), and a **global
   command-line addon** `loader/addons/bko-console.addon.efi` adding
   `console=ttyS0,115200n8` (systemd-stub ≥255 reads global addons;
   *check*; built with ukify where it is available, see open question 4).
   Prints BACKOTTER-INSTALL-OK.

`testvm/make-kdelinux.sh [--iso-dir DIR] FLAVOR WORK_DIR` boots the
**rescue stick image** (the slim Linux image, which has `sgdisk`,
`cryptsetup`, `mkfs.btrfs`, `mkfs.vfat`; *check* the erofs module and
btrfs-progs' `--simple`) under QEMU with the blank disk and a second drive
holding the ISO and install.sh, runs install.sh over SSH, then boots the
disk alone by its NVRAM entry. That is exactly the A100's path (BackShot1
below), so the guest build rehearses it. The cost is drift from Calamares:
K1 does one GUI install by hand and diffs its GPT, subvolumes, `/etc`, ESP
and `/var/lib/kde-linux` against install.sh's.

**The second version.** The first boot runs as installed. Then the build
points a drop-in `/etc/sysupdate.d/*.transfer` copy at a local mirror of
V2's `sysupdate/v2/` files (served over QEMU's user network from the build
host, GPG kept on), runs `updatectl update`, reboots into V2, writes
`/etc/bko-version` in each version's mstack, and powers off. Both UKIs and
both image files exist, V2 is the default, and V1 boots by `bootctl
set-oneshot`. A boot is blessed once `display-manager.service`
(plasmalogin) is up, no session needed; *check* that plasmalogin starts on
the headless guest (its `Requires=` likely pulls it in under
`multi-user.target`): if not, the build blesses by hand with
`systemd-bless-boot good`, and the spec says so.

**Room** (est., *check* in K1): two image files 15.8 GB, two UKIs 0.5 GB,
`/var` with flatpak seed ~1-2 GB: ~18 GB written. HoltTest1's D ~40 GiB:
over the hosts' 29 GiB ceiling, so its runs go to `/bkoscratch` (`HT_RAM=0`,
`cmp-run.sh --reserve 0 5`), as vanilla3's do, on hosts with
`holt_disk_mode`. The guest keeps `InstancesMax=2` by an `/etc/sysupdate.d`
override so updates don't grow it to 32 GB (Decision 8).

**Guest tools:**

- **vm-mount, KDE Linux mode:** a GPT root-x86-64 partition holding
  `@system` and `etc.mstack.v` at its top level: mount the top level at MP
  and the ESP at `MP/@system/boot`. `vm-chroot` builds the composed view
  for commands: `@system` with the booted version's erofs on `usr` and the
  mstack overlay on `etc` (read-only under `--ro`).
- **vm-update:** the shared `# t2-section: Linux.sysupdate` in
  `testvm/update/in-guest.sh`, before Linux.common: `updatectl update
  </dev/null`, "nothing newer" is success, a step when a new version
  appears, after the reboot the booted version must be the newest and its
  UKI blessed. `pkgset.sh`'s `base pm sysupdate` (IMAGE_VERSION and the
  installed versions), `fingerprint.py`'s `LINUX_KINDS`, vm-update's
  header. A daily update is ~8 GB unless sysupdated's delta works on the
  guest (*check*).
- **GuestTest2:** `K-uki` (one UKI per image file, each `.osrel` version
  matched) in `t2lib/guest_judge.py`, with a `KERNELS_MAX["K-uki"]` entry
  (2, by Decision 8; 4 if upstream's count is kept; guest_judge.py:28-31), `check-image.sh` and
  `check-after.sh` (booted = newest), a `sysupdate` section in
  `guest/known-lines.json`.
- `rebuild-one.sh`: a `kdelinux|kdelinux-luks` line; the `.iso` is an
  input as is (`input_file()` treats `*.raw` as an output, not `.iso`).

## Tests

**SticTest1** (`testvm/stictest1/kdelinux`): a scratch btrfs laid out like
the guest's top level, with simple quotas on: `@system` with `@system/etc`
and a read-write snapshot `@system/.etc.<epoch>` of it, a home subvolume holding a read-only snapshot, an `etc.mstack.v` with two
mstack subvolumes (one snapshotted from the other), a `layer@0` symlink, an
upper holding a whiteout (char 0:0), an opaque directory
(`user.overlay.opaque=y`) and other `user.overlay.*` xattrs, a 1 GiB
read-only regular file standing for an image; and a FAT ESP with UKI-sized
files and `+3`/`+2-0` names. Checks: every subvolume back as a subvolume
with its read-only flag, the xattrs and whiteouts, the big file's bytes,
labels and UUIDs; both archivers, two hosts, 0 FAIL. Entries in
`stic/manifests.json` and `limits.json`, `check-manifests.py`'s count up by
one.

**HoltTest1** (Decision 9: 1Obscure): ht1.sh backs up through vm-mount's
tree, loads onto a blank disk, and boots the restored disk alone,
**twice**: the default (V2), then V1 by `bootctl set-oneshot
kde-linux_V1.efi` over SSH (no console needed). Each boot must report
IMAGE_VERSION, `/usr`'s backing file, `/etc/bko-version` and `/proc/cmdline`
equal to the source's for that version. judge.py's `KDELINUX_KEPT`
requires byte-identical: `BOOTX64.EFI`, `systemd-bootx64.efi`,
`loader.conf`, every UKI and addon (counter renames allowed, bytes not),
every image file and `.caibx`, every `etc_V.mstack/rw/data` tree and
`origin-for_*`, `@system/etc` and each `@system/.etc.<epoch>`; it allows
`var/log/bko-holt`, the journal, `random-seed` and the counter renames
each boot makes. The existing `*_KEPT` rules are keyed on system paths
(`boot/efi/...`, `etc/...`, judge.py:324-424), which the top-level tree
doesn't have, so the judge maps paths first: tree
`etc.mstack.v/etc_V.mstack/rw/data/X` is judged as `etc/X` (V the booted
version), `@system/boot/...` as the ESP, other `@system/X` as `X`. The
same mapping serves bs1-manifest. `etc/machine-id` is required
byte-identical: nothing regenerates it.

**Test2:** golden plan `holt/tests/corpus/kdelinux/{src.json,plan.txt}`
from the guest's dump (no chroot, no `bootctl`, the squota line, the
subvolumes in order), `classes.json` (`holt.families.Obscure`, `holt.uefi`),
`holt/spec.json`, `guest/spec.json` (`pm` `sysupdate`, `S-none`, `L-sd`,
`K-uki`, `fw` `uefi`), `estimates.tsv`, `plan.py`'s source count with the
README and tests, `tests/fixtures/p2/world.py`, the re-blessed p2 goldens.
Done when two corpus builds give the same sha256, leakscan finds 0, and
`t2 plan HoltTest2 kdelinux` shows the cell runnable.

**BackShot2:** a `sysupdate` trait in `testvm/backshot2/bs2lib/families/`
(shared with GNOME OS), later, not in this plan's estimate.

## BackShot1 (QEMU rehearsal, then the A100)

**The A100** (`thinkpad.conf`: Lenovo A100, Samsung PM9C1a 512 GB serial
`S7XMNF0Y787118`, onboard Ethernet, French keyboard) runs PikaOS on LUKS
today ([[pikaos-luks-backshot1]]). KDE Linux replaces it.

### The install, remotely from the rescue stick

The user's only console jobs: Secure Boot off in the firmware if it is on
(*check* from the stick with `mokutil --sb-state` or the `SecureBoot`
EFI variable; the stick itself boots either way), and booting the stick.
Then all over SSH:

1. The ISO, its `SHA256SUMS` and `.gpg` copied to
   `/mnt/bs1-backup/iso/kdelinux/` on mybig512 (8.2 GB; *check* room).
2. `sudo bash /mnt/bs1-backup/backshot1/kdelinux/kdelinux-bootstrap.sh
   --luks S7XMNF0Y787118 ISO KEYS_DIR thinkpad`: erases the disk with that
   serial and runs `install.sh --luks` with BackShot1's
   `keys/disk-passphrase` as the key file (no final newline, never
   printed), then installs the fixed host key and `authorized_keys`,
   enables sshd, sets the host name, and makes the NVRAM entry.
3. The disk boots by its NVRAM entry; the first boot asks the passphrase
   at Plymouth (attended once, typed where the US keys would be,
   issue #303), unless family U is set up in step 2 (below).

`kdelinux-bootstrap-qemu.sh [--luks] TEST_IMG ISO WORK_DIR [PORT]` is its
rehearsal, as `pika-bootstrap-qemu.sh`: a blank NVMe with the A100's
serial, the ISO on a USB drive laid out like mybig512, then the disk alone.
Since the guest build already runs install.sh from the stick image, this is
mostly the same script with BackShot1's keys.

The alternative is the user installing by hand from the live ISO with
Calamares and enabling sshd at the console (`run0 systemctl enable --now
sshd`): ~20 min of the user's time and a USB stick with the 8.2 GB ISO
(Ventoy is unsupported). Decision 10.

### Unlocking from the drive: a new family U

Family S edits crypttab and rebuilds the initrd: impossible, the initrd is
in the UKI. Family B (AerynOS) is the right **mechanism**: systemd in the
initrd reads `rd.luks.uuid=`, `rd.luks.key=UUID=PATH:LABEL=mybig512` and
`rd.luks.options=UUID=keyfile-timeout=30s` and takes the key from the drive
itself, falling back to the prompt. Only the **delivery** is new: B writes
the arguments into Type #1 entries through blsforme, and KDE Linux has
none. Family U puts them in a **global command-line addon**,
`loader/addons/bs1-luks.addon.efi` (Decision 11):

- it survives updates (a new UKI name gets it too), and holds no secret
  (a path and a label);
- with Secure Boot off, systemd-stub loads it unsigned (*check*);
- it comes back with the ESP, so the restored system unlocks from the
  drive with no change to the restore;
- `bs1-adopt --luks-off` deletes it.

**The risk is a unit-name collision.** gpt-auto opens the root LUKS
itself as `systemd-cryptsetup@root.service`. Family B's
`rd.luks.uuid=`/`rd.luks.key=UUID=` make cryptsetup-generator write a
second unit (`systemd-cryptsetup@luks-UUID.service`) for the same device;
`rd.luks.name=UUID=root` would give the same unit name as gpt-auto's, and
generators skip or refuse a duplicate unit file (bs1-luks:644-649 relies
on "the generator opens only volumes named", the opposite of gpt-auto).
Family U uses **one form, `rd.luks.uuid=` with `rd.luks.key=UUID=` and
`rd.luks.options=UUID=`**, as family B. The first *check* in K10's
rehearsal, on the guest before the A100: exactly one cryptsetup unit runs
for the root, and the journal shows it took the drive key. If gpt-auto's
unit wins and ignores the key, the fallback is `root=/dev/mapper/root`
plus `rd.luks.name=UUID=root` (gpt-auto then leaves the root alone), with
a *check* that `kde-linux-mount-generator` still finds the top level
without gpt-auto's root. Also *check* the initrd has `usb-storage`/`uas` (the `block` hook) and the drive's
filesystem module (XFS on mybig512; there is no `autodetect` hook, so
`filesystems` should carry them all). If either fails, the A100 run is
family A (attended, front-loaded, [[front-load-manual-tests]]).

### The run

1. **Preflight:** `bs1-common`'s `is_kdelinux`; the root-x86-64 partition
   on the serial's disk; `SUPPORTED`; a new `bs1-sysupdate` (shared with
   GNOME OS, modelled on `bs1-abroot`) refuses a running sysupdate job,
   a half version, a booted version that isn't the newest, or a booted
   UKI still carrying a counter (not yet blessed), and warns when
   `LoaderEntryDefault` is set (it is not restored).
2. **Updates held:** `UPDATERS` (bs1-common:180) gains
   `kde-linux-sysupdated.socket`, `kde-linux-sysupdated.service`,
   `systemd-sysupdate.service` and `.timer`, `systemd-sysupdate-reboot`
   (*check* the exact unit names on K1); Discover's notifier is the user's
   to leave alone. No timer is enabled by default, so only a manual update
   can move it.
3. **Adopt with no package manager:** `bs1-adopt`'s `pkg_install`
   (160-190) stops on KDE Linux as on ostree; every tool must be in the
   image (efibootmgr *check*; `bs1-efi` already makes entries through
   efivarfs alone). `/etc` is writable through the overlay, so the sleep
   drop-in, host name and sshd drop-in work.
4. **Baseline:** `kdelinux.txt`: per version the UKI name, counter and
   SHA-256, the image file's size and SHA-256, the mstack present;
   `loader.conf`; `LoaderEntryDefault`/`Selected`/`ConfigTimeout`;
   `/proc/cmdline`; IMAGE_VERSION; `btrfs subvolume list -p /system`;
   `btrfs qgroup show` presence.
5. **Dump, backup, to the stick, restore, boot:** as today, through
   `backup_sources` (`/system` and the ESP). Size: one image (~8 GB, two
   after an update) plus the user's files.
6. **Verify:** the manifest with `KDELINUX_KEPT` (UKIs with the counter
   stripped, image files, systemd-boot, the addon), the paths mapped as
   in HoltTest1 (the booted mstack's `rw/data` judged as `etc/`,
   `@system/boot` as the ESP), `random-seed` presence only,
   `etc/machine-id` byte-identical (nothing remakes it); the booted
   version equal to the baseline; every
   subvolume back with its read-only flag; `UNLOCK=drive` with family U.
7. **The older version** (only if the A100 has two, after one `updatectl
   update` before the backup): `bootctl set-oneshot`, reboot, check, then
   the default again.

`bs1-qemu.py` gets the A/B arm (the oneshot boot) and runs on
`kdelinux-luks` with `--luks-typed` and family U before the A100.

## Shared lines (with the other new-distro sessions)

- **GNOME OS** (docs/design/gnome-os.md, R11) builds the same pieces:
  `holt/src/sysupdate.rs` (transfers, versions, UKIs by `read_uki`, boot
  counters with `.efi` support in `bls_counter`,
  `load/transactional.rs:237-253`, which strips only `.conf`, so
  `kde-linux_V+3.efi` parses as no counter today; tests for `+3.efi`,
  `+2-0.efi` and `+0-3.efi`), the addon PE-section reader, the ESP-kept boot check, the
  `Linux.sysupdate` section, `K-uki` and `pm sysupdate`, `bs1-sysupdate`
  and the `UPDATERS` lines, the no-package-manager guard in bs1-adopt, the
  KEPT-with-rename UKI rule in bs1-manifest, the BackShot2 trait.
  Whichever session lands first builds them; the other rebases (Decision
  12). **Not shared:** GNOME OS's raw verity partitions (GN3) and TPM;
  KDE Linux's erofs files, etc-mstack, nested subvolumes, squota and
  family U.
- `osrel.rs` `SUPPORTED`: append, the second to land rebases;
  `redact.rs`, `classes.json`, the holttest1d README's 1Obscure row,
  `check-manifests.py`'s count and `plan.py`'s source count: one session
  at a time, re-read first.

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| K0 | **Now, the user's OK first:** mirror the older build's `sysupdate/v2/` set (`kde-linux_202611240254.efi`, `_root-x86-64.erofs`, `.erofs.caibx`, `SHA256SUMS`, `.gpg`; ~8.2 GB) into `chopin:/bkoscratch/rebuild-inputs/kdelinux/` before it ages out (2026-11-28, when a newer daily pushes it past the 4 newest, unless protected), and V2's sysupdate set (202611270255, ~8.2 GB) beside the ISO already there. The set is install.sh's input (no ISO needed for V1). Pin every SHA-256: the `.efi` and `.erofs` are checked against the signed `SHA256SUMS`; the `.erofs.caibx` is deliberately left out of it upstream ([src] `upload-to-storage.sh:40`, `build.sh:238-240`), so its hash is pinned by us only | 0.1 d (~1 h download) |
| K1 | `testvm/kdelinux/install.sh`, `make-kdelinux.sh` (the stick image in QEMU), the first boot, sshd, tester, the console addon, the second version from the mirror, both versions booted; `rebuild-one.sh`; a copy on a second host. One GUI install by hand, diffed against install.sh's. Settle every *check* (plasmalogin and the bless when headless, the ESP automount, a missing image file's failure mode, the stick's `--root` tools, root key login, plasma-setup's gate, ukify and global addons, nested subvolumes in a backup, `user.overlay.*`, dedup between versions, the read-only image files, room, unit names). Write "What K1 found" here. Done when it boots alone into both versions on two hosts, SSH works, and `vm-update --try` is ok | 1.5 d |
| K2 | dump (High subagent): the `SUPPORTED` row and test; `sysupdate.rs` (shared) and its `kdelinux` part; the addon PE-section reader; the ESP automount stat and warning; erofs in `PSEUDO` and loop aliases; `backup_sources` replacing the generic entries (and its test); `prebuilt` in UKI keyed on sysupdate; the refusals; `redact.rs`; a scenario fake tree (autofs `/boot` included); unit tests | 1.25 d |
| K3 | load (High): the squota-aware `btrfs_quota` (and its test), nested subvolumes in order, the layout-driven mount tree, the fixed `etc.mstack.v` marker and status, `kdelinux_boot` (note, check script, NVRAM, log, tail), `bls_counter` for `.efi` (tests), LUKS without crypttab, the `LoaderEntryDefault` note, the v1 plan problems; tests | 1.5 d |
| K4 | Guest tools: vm-mount's mode and vm-chroot's composed view, `Linux.sysupdate`, pkgset/fingerprint, `K-uki` with `KERNELS_MAX["K-uki"]`, compare.py's tree; tests | 0.75 d |
| K5 | `kdelinux-luks`: install.sh `--luks`, the typed passphrase (`console-passphrase`, through the console addon with Plymouth off, or QMP keys: *check*) | 0.5 d |
| K6 | SticTest1 `kdelinux`, both archivers, two hosts, 0 FAIL | 0.4 d |
| K7 | HoltTest1 1Obscure: both guests, the two-version boot by oneshot, judge.py's path mapping and `KDELINUX_KEPT`, `ram-sizes.tsv`; runs on two hosts | 0.75 d |
| K8 | Golden plans and the corpus test; Test2 registration in the batched pass | 0.4 d |
| K9 | Docs: holt/README.md, layload.md (the KDE Linux boot stage, squota), HoltTest1's row, SticTest1's README, BackShot1's README section, memory | 0.15 d |
| K10 | BackShot1 plumbing: `is_kdelinux`, `bs1-sysupdate` (shared), the adopt guard, the manifest rules, family U in bs1-luks, bs1-adopt and bs1-verify, `kdelinux-bootstrap.sh` and `-qemu.sh`, bs1-qemu.py's oneshot arm; family U's one-cryptsetup-unit check first, before family U is built out; rehearsals PASS on `kdelinux-luks` | 1.25 d |
| K11 | BackShot1 on the A100: install from the stick, adopt (family U), backup, restore, verify, the older version's boot | 0.75 d (+~15 min of the user's time) |
| | *≈ 9.3 d in all; ≈ 8.55 d if GNOME OS builds the shared pieces first; ≈ 7.3 d without K10-K11* | |

K2 and K3 can start beside K1 from the facts above; their golden plans
(K8) wait for K1's dump. Each piece is committed on main when its tests
pass and the user says to; nothing is pushed until the user says so.
Source-code changes go to High-effort subagents
([[effort-max-when-it-pays]]).

**Costs** (est.): K0 two 8 GB downloads at 2-5 MB/s (~1 h each);
K1 ~1 h of build on one host with `--reserve 24 4`, then ~18 GB of qcow2
per caching host; K6 and K7 about 2 h of queue per host on `/bkoscratch`.

## Risks

- **Alpha churn.** Three layouts so far (RootFSv1-v3, v3 since
  2026-08-09, [src] e41e0676) plus etc-mstack (2026-11-16, [src]
  bad1b621, eleven days ago). A BuildStream base may replace the Arch one.
  Verity is discussed, not committed:
  https://community.kde.org/KDE_Linux/Verity is a 2025 design discussion
  of dm-verity vs fs-verity (last edited 2025-06-05), not a plan; the
  wiki's https://community.kde.org/KDE_Linux page itself is now a stub
  pointing at invent (gnome-os.md:121-122 cites the Verity page as "KDE
  Linux plans verity"). Each
  change means a new detection arm or refusal, a guest rebuild and a new
  golden plan. The marker-based refusal keeps a changed system from being
  loaded by guess; it can't keep the guests current.
- **Four-day builds.** A rebuild from the mirror is reproducible; a
  `vm-update` always takes today's daily, and upstream changes can break
  updating: on 2026-10-29 the S3 storage move broke delta updates for
  installs from before 26 October ("Protocol Error"; the doc's workaround
  stops `kde-linux-sysupdated.socket`, then `updatectl vacuum && updatectl
  update`, [doc updates]). `Linux.sysupdate` treats a failed update as a
  step failure with the journal of `kde-linux-sysupdated`, and may retry
  once with the socket stopped (full download), the doc's own workaround.
- **Size.** 8 GB per version, up to 32 GB with four. Backups, the guest
  and HoltTest1 are big; runs go to `/bkoscratch`. If rustic dedups little
  between versions, every BackShot1 backup after an update grows by 8 GB.
- **Headless bless.** A boot is blessed once the display manager is up,
  no session needed; the risk is only that plasmalogin doesn't start on a
  headless guest. K1 checks it; if it doesn't, the guests get a bless
  step that real machines don't.
- **Family U is unproven.** It rests on exactly one cryptsetup unit (not
  gpt-auto's `systemd-cryptsetup@root.service` beside a second
  generator-made one) opening the root with the drive key, and on the
  initrd carrying USB storage and XFS. If not, the A100 run is attended.
- **Our installer, not Calamares.** install.sh can drift from `subvol`
  and `shellprocess@uki`. The K1 diff against one GUI install, and a
  re-read of `subvol/main.py` at each rebuild, limit it.
- **Trust in the image.** The GPG key is checked only against itself
  (open question 3). The pinned SHA-256 at least makes every rebuild use
  the same bytes.
- **Live backups during an update.** sysupdate writes an 8 GB file then a
  UKI; a backup between the two holds a half version. Dump records it,
  load refuses it when it is the booted one, and bs1-sysupdate refuses to
  start. HoltTest1 backs up offline.

## Out of scope

- `testing-buildstream`, RootFSv1/v2 layouts, systemd-homed homes,
  Secure Boot (unsupported upstream), ARM.
- TPM2-sealed LUKS (none by default); a dual-boot disk (unsupported
  upstream, "at your own risk").
- Re-fetching image files at restore instead of backing them up (Decision
  5).
- sysexts, distrobox/toolbx containers, Homebrew, Nix and Flatpaks beyond
  what a file backup keeps (their files, xattrs and subuid owners are kept
  like any).
- `--new-uuids` and `--fit-largest` on KDE Linux (v1).

## Decisions for the user

**Approved by the user 2026-11-27: every recommendation as written. No milestone (K0 included) starts until the user says to proceed.**

1. **What is supported:** `kde-linux` `*`, the `testing` edition with
   RootFSv3 and etc-mstack only, refusing every other layout by marker
   (recommended: the only edition offered, and the refusal keeps churn
   safe); or wait for Beta (nothing now; the layout may still move); or
   `testing-buildstream` too (+1 d, a second guest, an experimental base).
2. **Inputs, now:** mirror the 202611240254 and 202611270255 sysupdate
   sets (install.sh's input; no older ISO needed) into rebuild-inputs on
   Chopin today, ~16 GB, so the guest has two pinned versions
   (recommended: the older one leaves the 4 newest upstream tomorrow); or build from 202611270255 alone and
   take the second version from whatever daily upstream has when K1 runs
   (no reproducible rebuild of the two-version guest).
3. **How the guests are built:** `install.sh` replaying Calamares' steps,
   run from the rescue stick image in QEMU, the same script that installs
   the A100 (recommended: scriptable, rebuildable, rehearses BackShot1;
   one GUI install diffs it); or Calamares' GUI driven by QMP keys and
   screenshots (brittle, +0.5 d); or one GUI install kept as a pinned
   image (not rebuildable).
4. **The backup tree:** the btrfs top level at `/` plus the ESP at
   `/@system/boot`, read raw (recommended: one real mount already holds
   everything; nothing read through overlay or loop); or one source per
   subvolume (closer to today's code, but ~10 entries that change with
   every user and update).
5. **The image files:** all kept byte for byte (recommended: upstream
   deletes them after four days, so nothing else can restore them, and
   rollback keeps working); or only the booted version (smaller, the
   restored menu loses its rollback entries, and the plan must drop their
   UKIs too).
6. **Snapper's home snapshots:** kept as read-only snapshots through the
   existing snapshot sharing (recommended: as CachyOS and Tumbleweed);
   or left out (smaller; the user's snapshot history is lost).
7. **Guests:** `kdelinux` plus `kdelinux-luks` (recommended: the
   encryption box is the installer's one choice and the A100's layout);
   or `kdelinux` alone (-0.5 d).
8. **Versions kept on the guest:** `InstancesMax=2` by an
   `/etc/sysupdate.d` override (recommended: keeps the guest near 18 GB
   through updates); or upstream's 4 (faithful, up to ~34 GB).
9. **HoltTest1's group:** **1Obscure** (recommended: beside vanilla3 and
   aeon, and GNOME OS's recommendation); or a new **1Sysupdate** with
   GNOME OS's guests.
10. **BackShot1 on the A100:** yes, installed remotely from the rescue
    stick by `kdelinux-bootstrap.sh`, LUKS on, after the QEMU rehearsal
    (recommended: no console work but Secure Boot and the stick, and the
    same script as the guests); or installed by hand with Calamares and
    sshd enabled at the console (~20 min of the user's time, the real
    installer); or not now (-2 d).
11. **Drive unlock:** a new **family U**, family B's `rd.luks.uuid=`/
    `rd.luks.key=`/`rd.luks.options=` arguments in a global UKI addon
    (recommended: survives updates and the restore, holds no secret;
    proven first by a one-cryptsetup-unit check, with `root=/dev/mapper/root`
    plus `rd.luks.name=` as the named fallback); or per-UKI addons in `.extra.d` (lost at
    each update's new UKI); or attended, family A (the passphrase typed
    at each boot, US keys on a French keyboard).
12. **Shared code with GNOME OS:** build `sysupdate.rs` and the other
    shared pieces once, whichever session lands first (recommended: one
    UKI and boot-counter model); or each its own (faster to start, two
    copies to keep in step).

## Open questions

1. **Headless bless:** the bless needs only `display-manager.service`
   (plasmalogin) up, not a session; does plasmalogin start on a headless
   guest under `multi-user.target`, and if not, is blessing by hand in the
   guests honest enough? (K1)
2. **Root over SSH:** does a key login work for the locked root under
   Arch's `UsePAM yes`, or must install.sh unlock root with a generated
   password? (K1)
3. **The signing key:** where can `15AB3EE6...53CA` be checked other than
   in the image it signs? Tried: keys.openpgp.org has no key for it ("No
   key found", HTTP 404); the repository has no copy (the keyring is built
   from the CI-only `.secure_files/gpg.public.key`, [src]
   `mkosi.finalize.d/40-core.sh.chroot:11-18`); the install doc verifies
   by `SHA256SUMS` from https://kde-linux-images.s3.kde.org/ only, with no
   GPG step. Unchecked: a KDE web page, or asking the KDE sysadmin team.
   Until then the pinned SHA-256s are our only anchor.
4. **ukify and the addon stub:** are `ukify` and `addonx64.efi.stub` in
   the image? If not, the addons are built on Chopin (Debian's
   `systemd-ukify` and `systemd-boot-efi`) and copied, or the stick gets
   them (not in `REQUIRED_COMMANDS`, `stick/slim-base-layer.sh:74`).
5. **A new machine's `LoaderSystemToken`:** should the restore run
   `bootctl random-seed` (from the stick, against the restored ESP) so a
   restored machine doesn't share the source's seed with no token? (K3)
6. **plasma-setup on a restore:** the marker
   `/var/lib/kde-linux/installed-with-plasma-setup` comes back with
   `/var`; *check* that plasma-setup doesn't run again on the restored
   system when a user already exists.
7. **Secure Boot on the A100:** its state today is unrecorded here; if it
   is on, the user turns it off once before K11 (and back on for later
   runs that want it).
8. **Delta updates:** does `kde-linux-sysupdated` work inside the guest,
   or does every vm-update download the full 8 GB? (The 2026-10-29 notice
   shows the delta path is the fragile one: its workaround is a full
   download with the socket stopped.)
9. **`LoaderEntryDefault` on a restore:** v1 notes it and warns. Should
   load write it back through efivarfs from the stick, under the same
   `--no-nvram` gate as the boot entries, so a machine with a pinned
   default boots it after the restore? (K3)
