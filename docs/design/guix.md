# GNU Guix System support

2026-11-14. Asked for by the user: support GNU Guix System, one of the eight
distributions added in parallel sessions (Proxmox, Guix, openSUSE Leap 16,
Vanilla OS, Slackware, Solus, OpenIndiana, Kali), the way AnduinOS, KDE
neon, elementary, PikaOS, BigLinux and Garuda were added. **Status: approved 2026-11-14 by the user with every recommended decision
(the last section). GX1-GX5 done 2026-11-14 (uncommitted); BIOS GRUB is
written by `grub-bios-setup` from the source's own images, not from the
store ("What GX1-GX5 found").**

**Conventions.** est. marks an estimate. *Check* marks a fact the sources
don't settle, to be settled on the first guest (GX1) before the code that
depends on it. The steps are **GX1-GX8** (Gentoo already has G1-G7).
Guix's facts come from the GNU Guix 1.5.0 release (announced on the Guix
blog 2026-01-23, the first release in three years) and the Guix reference
manual (System Installation, Manual Installation, Bootloader Configuration,
File Systems, Mapped Devices, Shepherd Services, Invoking `guix system`).
Nothing has been downloaded or installed yet. Code references are to local
`main` at 1edb4b5.

## Summary

- Guix System 1.5.0, kept current by `guix pull` and `guix system
  reconfigure`, becomes a supported system for bko-holt and both archivers,
  with two QEMU guests (`guix15`, UEFI, and `guix15-bios`, BIOS on Btrfs
  subvolumes), a SticTest1 entry each, a place in HoltTest1 **1Obscure**
  beside NixOS, golden plans and the Test2 registrations. BackShot1 on the
  Thinkpad is optional and later.
- It is NixOS's model with different names. The system is a store
  (`/gnu/store`, not `/nix/store`), the generations are profiles
  (`/var/guix/profiles/system-N-link`), the current one is
  `/run/current-system`, and the tools are in
  `/run/current-system/profile/{bin,sbin}`, not `.../sw`. There is no
  `/usr` and no package database: only `/bin/sh` and `/usr/bin/env` live
  outside the store.
- Two things are new to bko:
  - **GRUB reads the kernel and initrd straight from the store.** NixOS
    copies them to `/boot/kernels`; Guix doesn't. Each `menuentry` in the
    generated `grub.cfg` names `/gnu/store/...-linux-libre-K/bzImage` and
    `/gnu/store/...-raw-initrd/initrd.cpio.gz`, after a `search` by
    file-system UUID or label. A restore that keeps the root's UUID (and the
    subvolume, on Btrfs) keeps every menu entry valid. Nothing is
    rebuilt.
  - **Shepherd** is the init, not systemd, OpenRC, dinit or s6. bko-holt
    load never starts services, but HoltTest1's boot check and reboot do
    (`systemctl` today), and they need a `herd` arm.
- Most of the rest is already built for NixOS: the system tools on the
  dump's path (`sys.rs:43`), "nothing runs in the chroot" on UEFI
  (`load/commands.rs:2707-2719`), BIOS GRUB installed from the store's own
  GRUB (`:2654-2660`), the skipped SELinux step (`:2784`), `/etc` files that
  are links into the store (`vm-mount:442`, `vm-chroot:40`), and
  `nixos_removable`.
- ~3.65 d (est.) without BackShot1, ~4.15 d with it.

## Why

- **A second store-based system.** NixOS's support hard-codes
  `id == "nixos"` in five places, plus `/run/current-system/sw` and
  `/nix/store`. Guix would be a sixth case beside each of these. GX2 turns
  them into one "store system" predicate with per-system paths, so a third
  such system costs a table row, not another round of edits.
- **A kernel on the root file system, read by GRUB.** No supported Linux
  system boots a kernel that lives only on `/` and outside `/boot`. bko-holt
  must say so in the plan, and keep `grub.cfg`'s searches valid.
- **A new init.** Shepherd is the fifth init family in HoltTest1.
- **A store deduplicated by hard links.** guix-daemon deduplicates by
  default: identical store files are hard links through
  `/gnu/store/.links`. A minimal system has tens of thousands of inodes
  with nlink ≥ 2 (*check* the count). NixOS leaves `auto-optimise-store`
  off, so nixos2605 never tested this at scale. It is Bazzite's case
  (docs/design/bazzite.md, "Hardlinks") in another tree.

## What Guix System is (as far as bko cares)

| | Guix System 1.5.0 | Nearest supported system |
|---|---|---|
| Release | 1.5.0, 2026-01-23; between releases a rolling channel (`guix pull` to any commit). The installed system is whatever its last `reconfigure` built | NixOS 26.05 (pinned nixpkgs) |
| os-release | `ID=guix`, `NAME="Guix System"`, `PRETTY_NAME="Guix System"`, and, as far as the sources say, **no `VERSION_ID`** (*check*). `/etc/os-release` is a link into the store | Arch (`""`), Chimera (`*`) |
| Libre kernel | **linux-libre** only: no firmware blobs (nonguix is a third-party channel). virtio needs none, so the guests are fine. The Thinkpad's Wi-Fi isn't, but the rescue is wired anyway ([[rescue-wired-only]]) | none |
| Packages | No database. The system's packages are in the generation's `profile/manifest` (an S-expression: name, version, output, store path). `guix package -p /run/current-system/profile -I` lists them on a running system. In an offline dump, `/run` is empty, so it goes through `/var/guix/profiles/system` (*check* the link chain) | NixOS (the store closure) |
| Store | `/gnu/store`, made read-only by a bind mount at boot. The daemon's database is `/var/guix/db/db.sqlite`. Store files have mtime 1 (1970-01-01 00:00:01), their modes have no write bit, and they are deduplicated by hard links in `/gnu/store/.links` | `/nix/store` |
| Generations | `/var/guix/profiles/system` → `system-N-link` → `/gnu/store/...-system`. Each has `kernel`, `initrd`, `parameters` (an S-expression: kernel arguments, boot loader, root device) and `profile`. `guix system list-generations`, `roll-back`, `switch-generation`, `delete-generations` | NixOS's `system-N-link` |
| Init | **GNU Shepherd** (1.0.x; *check* the version 1.5.0 ships), PID 1, written in Guile. `herd status` lists the services as started, stopped or failed. Services come from `operating-system`'s `services` field. `reboot` and `halt` are Shepherd's own. No `/run/systemd`, no `systemctl`. `%base-services` gives an agetty on the kernel's `console=` device, so `console=ttyS0` gives a serial login | Artix dinit/s6 (a non-systemd arm in ht1) |
| Kernel and initrd | Built into the store. The initrd is Guix's own: a Guile `raw-initrd` or `base-initrd`, which mounts `/`, then `/gnu/store` and the rest from `file-systems`, and loads `gnu.load=`. No mkinitcpio, dracut or initramfs-tools | NixOS ("nothing to rebuild") |
| Kernel arguments | `--root=<UUID or label>`, `--system=/gnu/store/...-system`, `--load=/gnu/store/...-system/boot`, plus the config's `kernel-arguments`. Older releases also write `gnu.system=` and `gnu.load=` (*check* 1.5.0's) | none |
| Boot loader | `grub-bootloader` (BIOS, `grub-install` from the store onto the MBR) or `grub-efi-bootloader` (`grub-install --efi-directory=/boot/efi --bootloader-id=Guix`, *check* the id), or `grub-efi-removable-bootloader`. Also `extlinux`, `u-boot` and `depthcharge` on other boards: out of scope | NixOS GRUB (store GRUB) |
| grub.cfg | `/boot/grub/grub.cfg` (a link into the store? *check*), written by `install-bootloader` on each `system init`, `reconfigure` and `switch-generation`. The first `menuentry` is the current generation ("GNU with Linux-Libre K"). A `submenu "GNU system, old configurations..."` lists every other generation by number and date. Each entry does `search --fs-uuid --set UUID` (root identified by UUID), `--label` (by label), or `--file` (by device name; *check* which form each `file-system` spelling gives). Then come `linux .../bzImage --root=... --system=... --load=...` and `initrd .../initrd.cpio.gz`. On a Btrfs subvolume root, the store paths carry the subvolume's prefix (`/@/gnu/store/...`) | NixOS (BIOS), but with the kernel paths in the store |
| /etc | Mostly links into `/etc/static` → `/gnu/store/...-etc`. `/etc/fstab` is generated from `file-systems` (*check* that it exists, which entries it has, and that the root is among them). `/etc/config.scm` is ours, by convention | NixOS |
| Users | `guixbuilder01`…`10` in group `guixbuild` (the daemon's build users); `/etc/passwd` is rebuilt at activation from the config | NixOS (`nixbld`) |
| Install | The installation ISO (`guix-system-install-1.5.0.x86_64-linux.iso`, with a GPG `.sig`) runs the TUI installer on tty1 and a passwordless root shell on the other ttys. The manual's "manual installation" is partition, mount on `/mnt`, `herd start cow-store /mnt`, write `/mnt/etc/config.scm`, then `guix system init /mnt/etc/config.scm /mnt` (substitutes from `ci.guix.gnu.org` and `bordeaux.guix.gnu.org`). The binary tarball `guix-binary-1.5.0.x86_64-linux.tar.xz` (with `.sig`) puts the same tools on another distribution's live system | NixOS (`nixos-install` from a live system) |
| Firmware | BIOS and UEFI; Secure Boot unsupported (Guix's GRUB isn't signed) | |

## What GX1-GX5 found (2026-11-14)

**The ISO.** `guix-system-install-1.5.0.x86_64-linux.iso`, 1,188,261,888
bytes, SHA-256 `107e0a8082f03a10b15c1fb9383d2d752c1cdeda41b8db575a15550e1c2d8b4a`
(pinned in `make-guix.sh`); its `.sig` is a good signature by Efraim
Flashner's key `A28BF40C3E551372662D14F741AAE7DCCA3D8351` from GNU's keyring
(`gnu-keyring.gpg`, kept beside it in `/bkoscratch/rebuild-inputs/guix15/`
on Chopin and Cinqcent). Read offline:

- os-release: `ID=guix`, `NAME="Guix System"`, **no `VERSION_ID`**
  (Decision 5's `""` holds). `/etc/fstab` exists, generated, a link into
  the store.
- grub.cfg's entries boot with **`root=`, `gnu.system=`, `gnu.load=`**
  (not `--root=`); a top-level `search --fs-uuid`/`--label` before the
  entries, and a commented `# search --file` line for the locales.
- `parameters` is `(boot-parameters (version 1) ...)`; its
  `kernel-arguments` hold only the configuration's own; the root is
  `root-device`.
- Shepherd 1.0.9; the profile has wget, sfdisk, parted, mkfs.*, btrfs,
  cryptsetup, `grub-install` (package **`grub-pc-2.12`**, manifest name
  `grub-pc`), `halt` and `reboot` but **no `poweroff`** (vm-update,
  vm-lib.sh's `vm_halt` and ht1.sh's `vm_down` fall back to `halt`).
- **No serial login:** the installation image's Shepherd has
  `term-tty1`-`tty6` only (the installer on tty1, the manual on tty2,
  mingetty root logins with no password on tty3-tty6), no `term-console`.
  So Decision 1's path 1 runs as: QEMU boots the ISO's kernel and initrd
  with `console=ttyS0` for the kernel's messages, and
  `guix/console-type.py` logs in on tty3 by the QEMU monitor's `sendkey`,
  confirms by a marker echoed to `/dev/ttyS0`, and types the line that
  fetches `guix/install.sh`, its output sent to the serial console.
- Without a virtio RNG the image's activation sat for minutes at
  `ssh-keygen`'s "gathering entropy": the install VM has one.

**The guests** (`make-guix.sh FLAVOR WORK_DIR`; `guix system init` with
1.5.0's substitutes in ~5 min, the whole build ~6 min; 1.4 GiB store, 1.8-1.9
GiB qcow2; built on /bkoscratch with a 1 GiB RAM-disk reservation, the RAM
disks being full):

| | `guix15` | `guix15-bios` |
|---|---|---|
| qcow2 SHA-256 (both hosts) | `ede38aaa...0c8e` | `91d722e2...f9df` |
| Layout | GPT: ESP vfat `/boot/efi`, swap, / ext4 | GPT: BIOS boot, swap, Btrfs `guixroot` with `@` (256), `@home` (257), `@gnu` (258) |
| grub.cfg | `search --fs-uuid --set U`; `linux /gnu/store/...-linux-libre-6.17.12/bzImage root=U gnu.system=... gnu.load=... console=tty0 console=ttyS0,115200n8` | `search --label --set guixroot`; paths `/@gnu/store/...`; **`root=guixroot`** (the bare label) |
| Boot loader | `EFI/Guix/grubx64.efi` (+ our `EFI/BOOT/BOOTX64.EFI` copy); NVRAM `Boot0001 "Guix"` in `uefi-vars.fd` (grub-install wrote it: no efibootmgr on the image or the guest) | GRUB in the MBR and BIOS boot partition; `/boot/grub/i386-pc` holds **`boot.img` and `core.img`** |
| fstab | root and ESP by UUID | the three subvolumes by label, **no swap line** (swap by UUID in config.scm only) |
| `/gnu/store` | `ro,noatime` bind of `/gnu/store` | `ro,noatime` bind of `@gnu`'s `/store` |

- **No GRUB in the installed store.** `guix system init` ran the
  installation image's `grub-install`, and a reconfigure's GRUB is
  garbage. So Decision-time "BIOS GRUB from the store" can't work on
  Guix: **load runs the rescue's `grub-bios-setup --directory
  /boot/grub/i386-pc`** on the restored source's own `boot.img` and
  `core.img` (the rescue's Ubuntu 26.04 base has it), which writes Guix's
  own GRUB at the new disk's sector addresses; a dump without them is a
  plan problem. NixOS's arm is unchanged.
- Shepherd's `herd status` sections: `Started:`, `Stopped:`, `Failed:`,
  `Running timers:`, `One-shot:`, one ` X NAME` line each.
- 28,503 store files with nlink > 1 (deduplication), as expected.
- `compress=zstd` on `@gnu`'s fstab line has no effect (Btrfs takes the
  first mount's options); kept as the configuration says.
- **vm-update:** the first `guix system reconfigure` after `guix pull`
  aborts as a "downgrade": 1.5.0's commit `d58da8a` is on its release
  branch, not an ancestor of master's. `in-guest.sh` passes
  `--allow-downgrades` (the pull just took the head).

**GX3 (golden plans):** `holt/tests/corpus/guix15` and `guix15-bios`, from
dumps of the live guests (run `gx3`, Chopin, a static bko-holt 0.2.6 from
this tree), the same bytes from two builds, 0 leak findings; both plans
have no problems. `guix15`'s boot stage runs nothing in the chroot and
makes the `\EFI\Guix\grubx64.efi` entry; `guix15-bios`'s is one
`grub-bios-setup`, with three subvolumes and the swap from the partition
table.

**GX5 (SticTest1):** `testvm/stictest1/guix15` and `guix15-bios` (one
`inner.sh`), both archivers, both hosts: guix15 23 PASS / 5 LIMIT / 0
FAIL, guix15-bios 24/5/0 (run `gx5b`). A store-like tree (mtime 1,
0444/0555, a `.links` of 1285 entries, 4285 linked files) and a 200 MiB
copy of the real store came back with every link count, inode grouping,
mtime and mode, with restic-bko too. Guix has no python3, getfattr,
setfacl, setcap, setpriv or runuser: sockets are made by guile, the
non-root run is `chroot --userspec`.

## What already works, and what doesn't

**bko-holt `dump`:**

- `osrel.rs` `SUPPORTED` (`:121` is NixOS's row) has no `guix` row, so
  dump refuses it. It needs `("guix", "", "Guix System")` if there is no
  `VERSION_ID` (or `"*"` for a future one, as Chimera: Decision 5). The
  `refuses_other_systems()` list string and the doc comment get a line too.
- `sys.rs:43` `SYSTEM_PATH` has `/run/current-system/sw/{sbin,bin}`
  (NixOS), not Guix's `/run/current-system/profile/{sbin,bin}`. Without it
  a Guix dump finds no `btrfs`, `cryptsetup` or `efibootmgr`, which was
  NixOS's subvolume bug (a8d84e1). Add both. Also check
  `/run/setuid-programs`, which nothing needs. HoltTest1's dumps run on
  the source as well as in the rescuer (`--disk`), so both paths matter.
- `boot.rs:186` `installed_packages()` returns nothing on Guix. That's not
  fatal (NixOS's dump has the same gap), but `PACKAGES` drives the plan's
  notes and the judge. Proposed: a `guix_packages()` reading the current
  generation's `profile/manifest`, by `/var/guix/profiles/system` resolved
  under the dump's root, never `/run`. It needs a small S-expression reader
  for its entries, which are positional: `("grub" "2.12" "out"
  "/gnu/store/...-grub-2.12" (propagated-inputs ...) ...)` (*check* the
  manifest version 1.5.0 writes). `PACKAGES` gains
  Guix's names: `grub`, `grub-efi`, `linux-libre`, `cryptsetup`,
  `btrfs-progs`, `e2fsprogs`, `efibootmgr`, `shepherd`. `fingerprint.py`
  mirrors them.
- `kernels_json()` knows `/boot/vmlinuz-*`. Guix has none. Proposed: on a
  store system, record each generation's number, kernel store path,
  initrd store path and kernel arguments from its `parameters`, so the
  plan can name them and the judge can check they came back. This also
  covers NixOS, which records nothing like it today.
- `detect_loader()`: BIOS GRUB from the MBR works as for NixOS. For UEFI,
  `EFI/Guix/grubx64.efi` (*check*) is GRUB by the ESP check that already
  knows `grubx64.efi`. Record `/boot/grub/grub.cfg`'s `search` lines (the
  kind, UUID or label) for the plan's check below.
- Btrfs subvolumes: the subvolume list now works on NixOS, given the tools
  on the path. The `fstab` `subvol=` fallback (a8d84e1) needs Guix's
  `/etc/fstab` (*check*).
- The read-only `/gnu/store` bind mount shows in a live dump's mount table
  as a second mount of the root file system. It must be recorded as a bind
  mount and never planned as a file system (*check* what `mounts.rs` makes
  of it; NixOS has the same mount on `/nix/store`, so it probably already
  works).

**bko-holt `load`:**

- **Initramfs:** nothing to rebuild. Each generation's initrd is a store
  item, restored with `/`. This is the NixOS note
  (`load/commands.rs:2639-2640`) with Guix's wording.
- **UEFI GRUB:** nothing runs in the chroot. The chroot has no
  `/run/current-system`, and nothing needs to run: `grubx64.efi` (whose
  core finds `/boot/grub` by the root's UUID, which `mkfs -U` keeps)
  comes back with the ESP, and `grub.cfg` and the store's kernels come
  back with `/`. Only the NVRAM entry is made (`efi_entries`), plus
  `nixos_removable`'s copy to `EFI/BOOT/BOOTX64.EFI` under `--removable`.
  This is NixOS's arm (`:2716`).
- **BIOS GRUB:** the boot code is installed from the store's own GRUB, as
  NixOS's arm does (`:2660`). The changes are the path,
  `/gnu/store/*-grub-2.*/sbin/grub-install` (`sbin`, not `bin`; *check*),
  and the choice of GRUB: the one the current generation's boot loader
  names, not the newest in the store. Guix can hold several GRUB builds,
  and `grub-efi` has no `i386-pc`. The current generation's `parameters`
  or provenance names it (*check*). Failing that, the newest one with
  `lib/grub/i386-pc`, as NixOS does. `grub.cfg` stays as restored.
- **A check, not a command:** every `search --fs-uuid`/`--label` in the
  restored `grub.cfg` must name a file system the layout will make with
  that UUID or label. A `search --file` entry is fine as long as the path
  exists. One that doesn't is a problem in the plan, as PikaOS's
  `check_refind_uuids()` does for `refind_linux.conf`. Also each
  generation's `--root=` argument, for the same reason.
- The SELinux `setfiles` step (`:2784`) is skipped for Guix, as for NixOS.
  The atimes and LVM-devices steps are harmless: `chroot $t sh -c` works
  because Guix has `/bin/sh`.
- `guix system reconfigure` and `switch-generation` are never run by load.
  They need the daemon, and they would reinstall GRUB onto the source's
  device names, which is NixOS's `switch-to-configuration` problem.

**The archivers:**

- **Hard links:** rustic-bko restores regular-file hard links by (device,
  inode) (docs/design/bazzite.md). restic's restorer has a hardlink index
  too (*check* that a whole-store restore comes back linked, by comparing
  `find /gnu/store -type f -links +1 | wc -l` before and after). A restore
  that breaks them still boots: the daemon tolerates `.links` entries with
  nlink 1 (*check*). But the store would grow by the deduplicated bytes,
  and SticTest1's node-parity check would show it. Memory: one map entry
  per linked inode, est. 50-100 k on a minimal system. That's far below
  Bazzite's ~240 k, but [[openbsd-restore-oom]] is a reminder to measure
  it in the rescuer.
- **mtime 1, read-only modes:** every store file has mtime 1 and modes
  0444/0555, and the directories are 0555. Both archivers already restore
  mtimes exactly and set directory modes after their contents (NixOS's
  store passes). SticTest1's fixture adds such a tree anyway, since it's
  every file on the system here.
- `/var/guix/db/db.sqlite` must match the store. A dump of a stopped or
  offline system (HoltTest1's `--disk` dump, BackShot1's phase1) is
  consistent. A live backup while the daemon writes isn't, which is the
  same as any database.

**`redact.rs`:** `guix` joins `release_label()`'s `SYSTEMS` (the ISO's
volume label is `Guix_image`, *check*), and `gnu` and `guix` join
`GENERIC_HOSTS` if the installer suggests either (*check*; our guests set
their own hostnames). Our partition labels are generic (`esp`, `root`,
`swap`) and need nothing. These are shared lines ("Shared lines").

**The rescue image:** no change ([[one-kernel-rescue]]). linux-libre
doesn't matter to it, since the rescue's own kernel runs the restore.

**bko-holt on Guix itself:** HoltTest1 runs the static (`+crt-static`)
bko-holt. Guix has no `/lib64/ld-linux-x86-64.so.2`, so a dynamic glibc
binary won't start. A static one will (*check*: a static glibc binary that
does NSS lookups for names looks for `nss` modules it won't find; bko-holt
reads `/etc/passwd` itself, *check*). If it doesn't run, the fallback is
the static musl build NixOS's BackShot1 got (`--target
x86_64-unknown-linux-musl`, [[nixos-backshot1]]). **No musl build is
proposed unless GX1 shows it's needed** (Decision 7).

## Guests

Two (Decision 2). Both are built from the 1.5.0 installation ISO
(Decision 1), then moved to the current channel by `vm-update` (GX1's
`--try`), so the guest has two generations from the start: the install
and the update. The ISO, its `.sig` and the signer's key fingerprint go in
`/bkoscratch/rebuild-inputs/GUEST/`, with the SHA-256 pinned in the script
on first download. GPG checks the `.sig` against a pinned fingerprint (the
release signer's, *check*), the way Mint's signed sums are checked.

| Guest | Layout | What it covers |
|---|---|---|
| `guix15` | UEFI (`--uefi --no-secure-boot`), GPT: 512 MiB ESP at `/boot/efi`, 2 GiB swap, / ext4 by UUID. `grub-efi-bootloader`. Two generations | the manual's default layout: UEFI GRUB kept from the ESP, `grub.cfg`'s `search --fs-uuid`, store kernels, Shepherd, the hard-linked store |
| `guix15-bios` | BIOS, GPT with a 1 MiB BIOS boot partition, 2 GiB swap, / **Btrfs** with subvolumes `@` (/), `@home`, `@gnu` (`/gnu`, `compress=zstd`). The root is identified by label, `grub-bootloader` on `/dev/vda`. Two generations | the store GRUB's `grub-install` on BIOS, `search --label`, subvolume-prefixed store paths, the subvolume list from `/run/current-system/profile/sbin/btrfs`, `/gnu` on its own subvolume (the read-only store bind mount over it) |

How they are built (Decision 1):

1. **Guix's own installation ISO, unattended over the serial console**
   (recommended). A new `testvm/make-guix.sh FLAVOR ISO WORK_DIR` boots
   the ISO under QEMU (OVMF for `guix15`, SeaBIOS for `guix15-bios`;
   `MEMORY=4096`; `DISK_SIZE=20G`). It uses the ISO's own kernel and initrd
   (paths read from the ISO's `grub.cfg`), with that entry's arguments plus
   `console=ttyS0,115200n8`. `%base-services`' agetty then gives a root
   login on ttyS0 with an empty password (*check*). `console-boot.py` logs
   in and runs `curl http://10.0.2.2:PORT/install.sh | sh` (*check* that
   curl or wget is on the image; else Guile's `(web client)`). A new
   `testvm/guix/install.sh` partitions, makes the file systems (and the
   subvolumes), mounts on `/mnt`, `herd start cow-store /mnt`, and writes
   `/mnt/etc/config.scm` from a template (`testvm/guix/config-FLAVOR.scm`).
   The template has: the layout, the boot loader, `openssh-service-type`
   with root's key and `PermitRootLogin prohibit-password`,
   `kernel-arguments` with `console=ttyS0,115200n8`, the host name, a
   `tester` user, `dhcp-client-service-type`, and swap. Then `guix system
   init /mnt/etc/config.scm /mnt`, then BACKOTTER-INSTALL-OK, as
   `arch/install-live.sh` does. That gives the manual's own path, the
   release's Guix, cow-store (so the closure is copied to the target, not
   held in the live system's RAM), and the boot loader as Guix installs
   it. est. 20-40 min per build, mostly substitute downloads (~1.5-2.5 GiB,
   *check*).
2. **`make-bootstrap.sh guix` from the Arch live ISO**, as nixos2605 is
   (the fallback). The binary tarball unpacked on the live system, its
   build users and daemon, the substitute keys authorized, then `guix
   system init`. The store either sits in the live system's tmpfs (≥6 GiB
   RAM) or on a bind mount of the target, a hand-made cow-store. That's
   more of Guix's setup to copy by hand, but no console to drive. +0.25 d.
3. **The TUI installer by hand once**, kept as a pinned image: not
   rebuildable by `rebuild-one.sh`, so not recommended.

Each guest gets `rebuild-one.sh` lines (and the ISO check), the rebuild
inputs on both hosts, `holttest1d/ram-sizes.tsv` rows (est. D 6-8, MEM 4;
measured in GX6), `classes.json` entries (`uefi`/`bios` and the
`Obscure` group, in the batched Test2 pass), both hosts by `vm-store`
publish ([[four-host-farm]]: Guix needs no OpenZFS, so all four hosts can
run it), and a GuestTest1 grade.

**vm-update:** a new `# t2-section: Guix` in `update/in-guest.sh`, before
the other package managers, as NixOS's is:

- `guix pull`, then `guix system reconfigure /etc/config.scm`.
- `guix system delete-generations` keeps the newest two, then `guix gc`.
- `herd restart` isn't needed; the next boot runs the new generation.

`guix pull` builds Guix itself from the channel (est. 10-20 min and
~1 GiB on the first pull, less from substitutes, *check*). So an update
costs more than NixOS's. `trim.sh` should allow for a large overlay, as it
does for nixos2605. Other updates:

- **Pin or not:** Decision 6. Recommended: no pin, the channel's head, as
  every rolling guest. A broken head stops the update and names it, as
  Gentoo's source builds do.
- `pkgset.sh`: `guix-pkg NAME VERSION` lines from the system profile's
  manifest, and `guix-system PATH`.
- `fingerprint.py`'s `LINUX_KINDS`: `guix`.
- GuestTest2: a store-kernel rule in `t2lib/guest_judge.py` (no
  `/boot/vmlinuz`; the kernels are the generations'), and
  `guest/known-lines.json` lines for Guix's `/etc/static` links.

## Tests

**SticTest1** (`testvm/stictest1/guix15`, `guix15-bios`): one `inner.sh`
and `check.sh` for both, from nixos2605's, reading the layout from the
guest. The scratch disk is laid out like the guest's: ext4, or Btrfs with
the three subvolumes. Its fixture adds a store-like tree with the
following, and the judge checks link counts, mtimes and modes after each
restore:

- files with mtime 1
- 0444/0555 modes and 0555 directories
- a `.links` directory hard-linking a few thousand files

It runs with both archivers on both hosts. `stic/manifests.json`,
`limits.json` and `check-manifests.py`'s count go up by two (51 today;
whoever lands last sets the number).

**HoltTest1:** both guests join **1Obscure** (Decision 4): `classes.json`'s
group, the README's row and HoltTest1All's members. Changes to `ht1.sh`:

- `BOOTSTATE`: a Shepherd arm before `unknown init`, when `herd` is on the
  path. It prints `shepherd`, then `down NAME` for each service that is
  stopped and `failed NAME` for each that failed, sorted. The source's
  lines excuse the restored ones, as the other arms' do (*check* `herd
  status`'s format in the version 1.5.0 ships, and whether `herd status
  --format` or similar is steadier to parse).
- `vm_reboot`: `systemctl reboot || reboot`. Shepherd's `reboot` is the
  command on Guix (*check* how the OpenRC and dinit guests reboot today;
  if they already fall back, nothing changes).
- `HEALTH`: `findmnt`, `mountpoint` and `awk` are in Guix's
  `%base-packages` (util-linux, gawk). It reads `/etc/fstab` (*check* that
  it lists the swap, since "no swap" fails).
- The boot stage boots the restored disk alone: on `guix15` from the
  recreated NVRAM entry, on `guix15-bios` from the MBR.
- **The older generation boots too:** after the default one passes, `guix
  system switch-generation N-1 && reboot`, the same checks, then
  `switch-generation N && reboot`. This is the NixOS rollback check
  (`systemctl reboot --boot-loader-entry=nixos-generation-2.conf` there).
  It runs on the restored system, not by load (*check* that
  `switch-generation` rewrites only `grub.cfg` and doesn't run
  `grub-install` on the BIOS guest; if it does, that's fine on the
  restored disk, whose device name is the same).

`judge.py`: Guix's boot stage writes nothing but `var/log/bko-holt`, so
there are no new `BOOT_STAGE` excuses. `boot/grub/grub.cfg`,
`boot/efi/EFI/Guix/*`, and every file under `gnu/store` and
`var/guix/profiles` must be byte-identical to the source's, which tests
that nothing was regenerated. The store's link counts are checked as
SticTest1's are.

**Test2:** golden plans in `holt/tests/corpus/guix15/` and `guix15-bios/`
(`src.json`, `plan.txt`, their `index.tsv` rows, the corpus README line)
from `bko-holt dump` of each guest. These are made by this work. The
registrations wait for the sibling sessions' **one batched Test2 pass**,
as PikaOS's did: `classes.json` (`uefi`/`bios` lists, `Obscure`),
`holt/spec.json`, `guest/spec.json`, `estimates.tsv`, `t2lib/plan.py`'s
counts, `tests/fixtures/p2/world.py`, and the re-blessed goldens. Done when
two corpus builds have the same sha256, the leak scans are 0 on both
hosts and the Mac, no other plan changes, and Test2's own tests pass. A
holt fake tree in `holt/src/scenarios.rs` (a Guix root with a store,
generations, a `grub.cfg` with store paths) covers the plan in `cargo
test`.

**BackShot1** (optional, later, GX8): the Thinkpad reinstalled with Guix
from the TUI installer. It needs a wired NIC that linux-libre drives
(*check* the Thinkpad's Ethernet). Expected fixes, est.:

- `bs1-adopt`: a Shepherd arm (openssh and avahi are added to
  `/etc/config.scm` and reconfigured, not installed with a package
  manager).
- `bs1-common`'s `package_list`: Guix's manifest.
- BackShot1's own bko-holt: static, as above.
- Rollback check: boot an older generation from GRUB's "old
  configurations" submenu.

A `bs1-qemu.py --virtio` rehearsal on `guix15` on both hosts comes first.

## Shared lines (with the seven sibling sessions)

Per the shared notes folder's `elementary-shared-lines.md`:

- Each system its own row in `osrel.rs` `SUPPORTED` (the second to land
  rebases).
- `redact.rs`'s `GENERIC_HOSTS` and `SYSTEMS` are edited once per session,
  re-read first.
- `classes.json`, the holttest1d README's 1Obscure row and HoltTest1All,
  and `check-manifests.py`'s count are edited one session at a time.

Guix also touches lines nobody else does: `sys.rs`'s `SYSTEM_PATH`,
`load/commands.rs`'s NixOS arms (made into "store system" arms), and
`ht1.sh`'s `BOOTSTATE` and `vm_reboot`. ht1.sh is shared with every
guest, so its edits are additions, re-read right before. Guix doesn't
touch `update_grub()`'s list or `make-from-live.sh`. Before editing any
shared line, append to that note who holds which line.

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| GX1 | Fetch the 1.5.0 installation ISO (~1.5 GB, *check*), its `.sig` and the signer's key into rebuild-inputs on Chopin (the user's OK first); verify and pin the SHA-256. Read it offline: os-release, the `grub.cfg` boot entry and kernel arguments, Shepherd's version, the serial agetty, curl or wget, the volume label. Then `make-guix.sh guix15` by Decision 1's path, `rebuild-one.sh`, the `vm-update` Guix section (`--try`: generation 2), and a copy to Cinqcent (the same SHA-256). Settle every *check* that the guest can answer (`/etc/fstab`, `grub.cfg`'s `search` form, `parameters`, the GRUB store paths, hard-link count, `herd status`'s format, whether the static bko-holt runs) and write "What GX1 found" here. Done when it boots alone on both hosts, SSH works, two generations are listed, and `vm-update --try` is ok | 1.25 d (+0.25 d on path 2) |
| GX2 | bko-holt (High-effort subagent): the "store system" predicate with NixOS's and Guix's paths (`SYSTEM_PATH`, the store, the profile root), with NixOS's plans unchanged (its golden plan byte-identical). Also: `SUPPORTED`'s row and tests; `guix_packages()` from the manifest; `PACKAGES` and `fingerprint.py`; generations in the dump; the UEFI and BIOS GRUB arms (the generation's GRUB, `sbin/grub-install`); the `grub.cfg` search and `--root=` check; the SELinux skip; `redact.rs`'s words; and a Guix fake tree in `scenarios.rs`. Done when `cargo test` passes, and the scenario's plan has no initramfs command, no chroot `grub-install` on UEFI, one store `grub-install` per MBR on BIOS, the notes, and the NVRAM entry | 0.75 d |
| GX3 | `guix15`'s golden plan (`holt/tests/corpus/guix15`, its `index.tsv` row); Test2 registrations to the batched pass | 0.3 d |
| GX4 | `guix15-bios`: the build (BIOS, Btrfs subvolumes, label), `rebuild-one.sh`, `vm-update --try`, its golden plan and `index.tsv` row. Done as GX1 and GX3 are | 0.4 d |
| GX5 | SticTest1 entries (the store-like fixture) and runs: both guests, both archivers, both hosts, 0 FAIL; the hard-link counts kept | 0.35 d |
| GX6 | `ht1.sh`'s Shepherd `BOOTSTATE` arm and `vm_reboot` fallback (with `judge-test.py`/shell tests where ht1 has them), and judge.py's byte-identical store and GRUB check. Then HoltTest1 runs (1Obscure) on both hosts: the boot alone, the older generation's boot, RAM peaks in `ram-sizes.tsv` | 0.5 d |
| GX7 | Docs: holt/README.md's supported list, `osrel.rs`'s comment, HoltTest1's 1Obscure row and HoltTest1All, SticTest1's README, `layload.md`'s boot stage (the store system arms), memory (holttest1d index, a guix-support note) | 0.1 d |
| GX8 | *Optional, later:* BackShot1 on the Thinkpad, with the fixes above, the rehearsals, then the run. Done when phase1, plan, phase2 and verify PASS with 0 unexpected, and the current and an older generation boot | 0.5 d + ~1 h of the user's time |
| | *≈ 3.65 d without GX8 (+0.25 d on path 2), ≈ 4.15 d with it* | |

GX2 can start beside GX1 from the facts above, but its golden plan (GX3)
waits for GX1's dump. Each piece is committed on main when its tests pass
and the user says to commit. Nothing is pushed until the user says so.
Source-code changes go to High-effort subagents
([[effort-max-when-it-pays]]). Heavy work runs in `/mnt/bko-ram` under
`cmp-run.sh` reservations (WEAR_TAG `build:guix15` etc.), with finished
guests moved to disk, since Chopin's RAM disk is contended.

**Costs** (est.):

- GX1 and GX4: a 20-40 min build each (`--reserve 8 4`), plus the first
  `vm-update` (10-30 min: `guix pull`).
- Disk: a qcow2 of ~4-6 GiB per guest per host, and the ISO ~1.5 GB per
  host.
- GX5 and GX6: about 1 h of queue per host.
- The user's time: none but the OKs, until GX8.

## Risks

- **Substitute servers.** The install and every update download from
  `ci.guix.gnu.org`/`bordeaux.guix.gnu.org`. If they're slow or missing a
  substitute, Guix builds from source (hours). The rebuild inputs can't
  hold a store. A failing build stops and names the package.
- **`guix pull`'s cost.** Each update first builds Guix itself, so a
  CarpetBeater refresh of these two guests costs more than any other
  Linux guest's (est. 20-40 min each).
- **The serial console path.** If the ISO's agetty doesn't come up on
  ttyS0, or root needs a password there, Decision 1 falls to path 2
  (+0.25 d).
- **Hard links under restic.** If restic restores the store unlinked, the
  guest still boots, but it's larger and SticTest1 marks it. That would be
  a LIMIT for restic, like the courtesy rules in
  [[cross-tool-compat-courtesy]], not a bko-holt failure (bko-holt uses
  rustic-bko).
- **Refactoring NixOS's arms.** Turning `id == "nixos"` into a store-system
  predicate could change nixos2605's plan. GX2's done-condition is
  nixos2605's golden plan byte-identical, plus a HoltTest1 rerun of
  nixos2605 on one host in GX6.
- **A future `VERSION_ID`.** If Guix starts writing one, `""` would refuse
  it. `"*"` accepts any (Decision 5).

## Out of scope

- LUKS. On Guix, GRUB itself unlocks the root, because `/boot` is on it:
  two typed passphrases, LUKS2 limited to PBKDF2. It's a later
  `guix15-luks` if wanted (Decision 3), with
  [[front-load-manual-tests]].
- Secure Boot (unsupported by Guix), nonguix's kernel and firmware, and
  the Hurd.
- Boot loaders other than GRUB (extlinux, U-Boot, depthcharge), and arm64
  and RISC-V.
- Guix the package manager on another distribution: a `/gnu/store` on,
  say, Debian is just files there, and already backed up as such. Only
  Guix System is claimed.
- Home profiles (`guix home`) and per-user profiles: files in `/var/guix`
  and `~`, restored as files, with nothing special.
- BackShot2 cases on Guix: later, through BackShot2All.

## Decisions for the user

1. **How the guests are built:**
   - **Guix's installation ISO, unattended over the serial console**, with
     `herd start cow-store` and `guix system init` (recommended: the
     manual's own path and the release's own tools).
   - Or **`make-bootstrap.sh guix` from the Arch live ISO** with the
     binary tarball, as nixos2605 (+0.25 d: the daemon and cow-store set
     up by hand).
   - Or **one TUI install kept as a pinned image** (not rebuildable).
2. **Guests:**
   - **`guix15` (UEFI, ext4) plus `guix15-bios` (BIOS, Btrfs subvolumes
     with `/gnu` on its own, root by label)** (recommended: the second
     covers the store GRUB's `grub-install`, `search --label`,
     subvolume-prefixed store paths and the Btrfs tools on the profile
     path, for 0.4 d).
   - Or only `guix15`.
3. **LUKS:**
   - **Not now** (recommended: GRUB-unlocked LUKS needs two typed
     passphrases and its own guest; nothing else in this spec needs it).
   - Or add `guix15-luks` now (+0.5 d).
4. **HoltTest1's group:**
   - **Join 1Obscure, beside NixOS** (recommended).
   - Or a new **1X** ("1N" for NixOS and Guix, moving nixos2605 too).
5. **The supported version:**
   - **`("guix", "", "Guix System")`** if GX1 confirms there is no
     `VERSION_ID` (recommended: like Arch and Void; a release that starts
     writing one is checked before it's claimed).
   - Or `"*"`, as Chimera and AerynOS.
6. **vm-update's channel:**
   - **The channel's head, unpinned**, as every rolling guest
     (recommended, [[images-take-os-updates]]).
   - Or a pinned commit, moved by hand (steadier, but stale).
7. **A static musl bko-holt:**
   - **Only if GX1 shows the static glibc build fails on Guix**
     (recommended: HoltTest1's static bko-holt probably runs as is).
   - Or build musl from the start, as NixOS's BackShot1 did.
8. **NixOS's arms:**
   - **Generalized into one store-system arm**, with nixos2605's golden
     plan unchanged as the guard (recommended: one code path, and a third
     such system is a table row).
   - Or Guix arms beside NixOS's, `id == "guix"` everywhere (smaller diff,
     two copies).
9. **BackShot1:**
   - **Later and optional, after the guests pass** (recommended: it needs
     a Thinkpad reinstall and Ethernet that linux-libre drives).
   - Or now, with GX1.
   - Or not at all.
