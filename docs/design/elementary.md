# elementary OS support (approved)

2026-11-14. The task: add elementary OS the way Chimera, Gentoo and Bazzite
were added, at the size of the EndeavourOS/Manjaro change (6715f43). A
sibling session is adding AnduinOS (`docs/design/anduinos.md`), also
Ubuntu-based; "Shared with AnduinOS" says which lines both touch.

**Conventions.** est. marks an estimate. *Check* marks a fact the sources
read so far don't settle; E1 settles each on the first guest before any
code depends on it. The installer facts come from the elementary/installer
and pop-os/distinst sources on GitHub (`main`/`master`, read 2026-11-14:
`daemon/DistinstBackend.vala`, `src/Views/EncryptView.vala`,
`PartitioningView.vala`, distinst's `src/distribution/debian.rs`), and the
release facts from elementary.io and its install docs. Code references are
to local `main` at 6a78448.

## Summary

- elementary OS 8 (8.1.1 "Circe", Ubuntu 24.04.3 base) becomes a supported
  system for bko-holt and both archivers. It gets two UEFI guests, a
  SticTest1 entry each, places in HoltTest1U and Test2, and an optional
  BackShot1 run on the Thinkpad.
- Nearly all of it is built already. It boots like `ubuntu26-uefi` (UEFI
  GRUB through shim, `update-initramfs`, `update-grub`). It encrypts like
  `ubuntu26-luks` (ESP, /boot ext4, LVM on LUKS2, the passphrase typed on
  the serial console). Its guests are built from the live ISO, the way
  `mint22`, `zorin18` and `pop24` are.
- bko-holt needs a `SUPPORTED` row, `elementary` in `update_grub()`'s
  Debian-family list, and the redaction names. No new boot path.
- What's new is the guest layout. elementary's installer, unlike any other
  supported desktop's, puts / and swap on **LVM even without encryption**
  (VG `data`, LVs `root` and a 4 GiB `swap`). `make-from-live.sh` gets
  that layout and its encrypted twin.
- ~2.75 d (est.), plus 0.5 d for the optional BackShot1.

## Why

- **A popular Ubuntu derivative not yet covered.** Mint, Zorin and Pop!_OS
  are. elementary is the next most installed, and the only one with its own
  installer backend (distinst), so its disks don't look like Ubuntu's.
- **LVM as the default, not an option.** Every plain desktop install
  becomes an LVM restore. Until now only `rocky10-lvm`, `gentoo` and the
  LUKS guests tested LVM, and none of them is a desktop default.
- **A generic VG name.** `data` is the name every elementary and Pop!_OS
  LVM install gets. It tests load's same-name-VG refusal on a name users
  really have.

## What elementary OS is (as far as bko cares)

| | elementary OS 8.1 | Nearest supported system |
|---|---|---|
| Release | 8.1 2025-12-22, 8.1.1 2026-02-26, monthly updates; OS 9 is next, no date. ISO `elementaryos-8.1-stable-amd64.20260219.iso` (3.3 GB), SHA-256 `bda93040d08c05911fb159f8150bf8f4ef2db6567ef6e2acd197cb6f395d3446` on elementary.io's docs page (no signature) | Zorin OS 18 |
| Base | Ubuntu 24.04.3 LTS (noble), HWE kernel 6.14 | `ubuntu24` |
| os-release | *Check:* `ID=elementary`, `ID_LIKE=ubuntu` (perhaps `"ubuntu debian"`), `VERSION_ID="8.1"`, `UBUNTU_CODENAME=noble`. distinst matches `NAME="elementary OS"` | Zorin, Mint |
| Installer | `io.elementary.installer` (GTK, GUI only), with System76's distinst as its backend: unpack `casper/filesystem.squashfs`, purge `filesystem.manifest-remove`, install the boot packages | Pop!_OS (distinst too) |
| Default layout, UEFI | GPT: ESP 278 MB FAT32 at **/boot/efi**; the rest one LVM PV, VG `data`: LV `root` ext4 /, LV `swap` 4 GiB last. No separate /boot | none: LVM without LUKS is new for a desktop |
| Encrypted (UEFI) | ESP 278 MB; **/boot ext4 1 GiB**; LUKS PV `cryptdata` holding VG `data` (`root`, `swap`). Swap is encrypted by the same LUKS (no random-key cryptswap). One passphrase, checked by pwquality; no recovery key. *Check:* LUKS2 (cryptsetup's default), the crypttab line, the names after `generate_unique_id()` | `ubuntu26-luks` |
| BIOS | MBR: /boot ext4 1302 MB, then LVM (LUKS if asked) | `mint22` (BIOS GRUB) |
| Boot loader | GRUB: on UEFI `grub-efi-amd64`, `grub-efi-amd64-signed`, `shim-signed`, `mokutil`; on BIOS `grub-pc`. Secure Boot is expected to work through Ubuntu's shim (*check*). *Check:* the `\EFI\<id>\` name (`ubuntu`, since the signed GRUB's prefix is fixed, or `elementary`) | `ubuntu26-uefi` |
| Initramfs | initramfs-tools (`update-initramfs`), cryptsetup-initramfs on LUKS (*check*) | Ubuntu |
| Init, packages | systemd; apt with `elementary.sources` and the elementary PPAs (*check*); AppCenter apps are Flatpaks | Ubuntu |
| File systems | ext4 only by default (manual partitioning offers others) | |

## What already works, and what doesn't

**bko-holt `dump`:**

- `osrel.rs` `SUPPORTED` refuses it: no `elementary` row. It needs
  `("elementary", "8", "elementary OS 8")`, a unit test from E1's
  os-release, and the `supported_names()` message test updated. ID_LIKE
  doesn't count (osrel's rule), so nothing else changes.
- Packages: dpkg is read already, and the boot names (`grub-efi-amd64`,
  `shim-signed`, `linux-image-*`, `initramfs-tools`, `cryptsetup`,
  `lvm2`) are in `PACKAGES`. Nothing to add, unless E1 finds an
  elementary-only kernel or loader package.
- Boot loader, `via_shim`, the ESP at /boot/efi, LVM and LUKS: all as on
  `ubuntu26-uefi` and `ubuntu26-luks`.
- `redact.rs`: add `elementary` to the kept words (140) and `SYSTEMS`
  (179, the length 12 goes to 13), so ISO and volume names like
  `elementaryos-8.1-stable` are kept, not hashed. `data` is a
  kept word already (149); `cryptdata` isn't, so it is added too.

**bko-holt `load`:**

- `update_grub()` (`load/commands.rs:2650`) runs `update-grub` only for
  `debian | ubuntu | linuxmint | zorin`. elementary would fall through to
  `grub-mkconfig -o /boot/grub/grub.cfg`. That works too, but it isn't what
  the system's own kernel hooks run. Add `elementary` (and AnduinOS's id;
  see below).
- UEFI GRUB: the generic arm runs `grub-install --target=x86_64-efi
  --efi-directory=/boot/efi --bootloader-id=ID --no-nvram`, with ID from
  BootCurrent's `\EFI\ID\shimx64.efi`. On Ubuntu's GRUB that reinstalls
  shim and the signed GRUB, which `ubuntu26-uefi` already passes. A unit
  test of `bootloader_id()` with an elementary entry is added when E1 knows
  the directory's name.
- Initramfs: the Debian fallback `update-initramfs -u -k all` is right.
  On the LUKS guest it must pick up the restored crypttab's new LUKS UUID,
  as on `ubuntu26-luks` (passes).
- LVM: load already restores VG/LV names and UUIDs. It refuses a plan when
  a VG of the same name sits on a non-target disk (ubuntu26-luks's fix). A
  rescue system with no `data` VG is unaffected. A user restoring onto a
  machine whose other disk holds an elementary or Pop!_OS install would be
  refused, with the existing message. Out of scope to rename (see "Out of
  scope").

**BackShot1** (`testvm/realworld/backshot1`): *check* only. `bs1-adopt`
reads `ID` and installs with apt for any dpkg system. `bs1-common`'s
package list reads dpkg. `bs1-manifest` already expects `boot/initrd.img-*`,
`boot/grub/*` and `boot/efi/*`. `bs1-luks` unlocks LUKS from the key file on
mybig512 (CachyOS ran it 2026-11-14). E8 finds any gap in a
`bs1-qemu.py --virtio` rehearsal on `elementary8-luks` first.

**SticTest1:** the archivers are static. Each guest gets
`testvm/stictest1/GUEST/` from `ubuntu26-uefi`'s (`check.sh`, `inner.sh`,
`inv.sh`), with its fixture volume group made like the guest's (an LV in
`data`, which has no free space by default: the fixture uses a scratch
disk, as `rocky10-lvm` does). Plus `stic/manifests.json`, `limits.json`,
and check-manifests' count +2.

**The rescue image:** no change ([[one-kernel-rescue]]): its kernel has
LVM, dm-crypt and ext4.

## Guests

Built by `make-from-live.sh elementary ...`, as `zorin18` is: it copies
the live system's squashfs, purges the remove list, adds openssh-server,
writes fstab and the users, then reinstalls the boot loader. That is what
distinst does. The GUI installer can't run unattended. A new `--layout
distinst` (the default for `elementary`) gives the installer's layout
instead of make-from-live's fixed three partitions. With `--luks`, it gives
the encrypted layout. Changes in `make-from-live.sh`:

- `DISTRO=elementary` (casper family, UEFI, GRUB through shim). This is the
  first UEFI GRUB guest from this script. Today's UEFI branch is Pop!_OS's
  systemd-boot, and it dies if the remove list holds UEFI boot packages. On
  elementary they must stay (*check* whether the list names them; if so, they
  come out of the purge). The boot step:
  `grub-install --target=x86_64-efi --efi-directory=/boot/efi --uefi-secure-boot`
  with distinst's bootloader id (*check*), `update-initramfs -c -k all`,
  `update-grub`, then `efibootmgr` entries made by QEMU's OVMF on first
  boot (the fallback `EFI/BOOT/BOOTX64.EFI` is shim, so OVMF finds it).
- Layout `distinst`: GPT, ESP 278 MB (`mkfs.vfat -F 32`), PV on the rest,
  `vgcreate data`, `lvcreate -L 4G -n swap`, `root` on the rest, ext4.
  fstab as distinst writes it (*check*: `/dev/mapper/data-root` or UUIDs).
  `lvm2` comes from the image. DISK_SIZE stays 16G (root ~11.7 GiB).
- `--luks`: ESP, /boot ext4 1 GiB, `cryptsetup luksFormat --type luks2`
  (the generated password is kept in WORK_DIR as `luks-password`, never in
  the repo), opened as `cryptdata`, the VG inside. crypttab
  `cryptdata UUID=... none luks`, with `cryptsetup-initramfs`. It boots
  under `testvm/console-boot.py`, typing the passphrase on the serial
  console, as `ubuntu26-luks` does.
- A clean-up for leftovers: the `data` VG and `cryptdata` mapping of a
  failed build. `rebuild-one.sh`'s `leftovers()` matches `bkos*` dm names
  only, so the build names them `bkoel-data` and `bkoel-cryptdata` while
  building. It renames the VG to `data` with `vgrename` before converting,
  so the guest has the installer's names. *Check* that a second build on
  the same host, with the first's image mounted by vm-mount, doesn't clash
  (vm-mount names LVs itself since 2026-11-10).

| Guest | Firmware, loader | Layout | What it covers |
|---|---|---|---|
| `elementary8` | UEFI with Secure Boot, shim + signed GRUB | `distinst`: ESP /boot/efi, VG `data` (root ext4, swap) | the default install; LVM with no LUKS on a desktop; `update-grub` on a new id |
| `elementary8-luks` | the same | `distinst --luks`: ESP, /boot ext4, LUKS2 `cryptdata` with VG `data` | the installer's encryption option; encrypted swap in the VG; BackShot1's rehearsal guest |

Each one gets the following:
- `rebuild-one.sh` lines, and the ISO in the `case ... no ISO` check;
- the ISO in `/bkoscratch/rebuild-inputs/GUEST` on Chopin, checked against
  elementary.io's SHA-256 (a 3.3 GB download, asked before E1);
- `vm-run-options` `--uefi` (Secure Boot on; *check* that shim boots under
  the farm's OVMF keys, else `--no-secure-boot` like `pop24`);
- `holttest1d/ram-sizes.tsv` rows (est. D 12, MEM 4, until a run measures);
- the four hosts via image-store publish;
- vm-update `--try`, and a GuestTest1 grade.

vm-update: `in-guest.sh`'s Linux.apt section already updates Ubuntu
derivatives. *Check* the elementary PPAs answer, and that nothing asks
(elementary's `io.elementary.settings-daemon` holds no apt locks under
multi-user.target). Flatpaks are left alone, as on every guest.

## Shared with AnduinOS

The sibling session's AnduinOS work touches the same places. To avoid
editing the same lines at once:

| File | Shared line | How |
|---|---|---|
| `holt/src/osrel.rs` | `SUPPORTED` after Zorin; the doc comment; the `supported_names()` test string | each adds its own row; whoever lands second rebases onto the first's line |
| `holt/src/load/commands.rs:2650` | `update_grub()`'s id list | one edit for every sibling, agreed with the KDE neon session (`docs/design/kde-neon.md`, its decision 6): the ID list gains `neon`, `elementary` and `anduinos` together, made by whichever session's holt step runs first; ID_LIKE is the alternative in both specs. Decision 5 |
| `testvm/make-from-live.sh` UEFI branch | UEFI GRUB through shim | one shared path with a per-distro bootloader id (neon's decision 7), written by whoever gets there first; elementary's `distinst` layout and `--luks` go on top of it |
| `holt/src/redact.rs:140, :179` | kept words and `SYSTEMS` | the same: one edit for both |
| `testvm/make-from-live.sh` | the `DISTRO` case and usage | each its own case arm; the `distinst` layout is elementary's only |
| `testvm/rebuild-one.sh` | recipes and the ISO check (108) | own lines; line 108 edited once for both |
| `testvm/test2/classes.json` | `U` family, `uefi` list | own entries; edited one at a time |
| `testvm/holttest1d/README.md` | the 1U row | one edit, if both join 1U |

A note in the shared notes folder says the same and records who has which
line. Also, **the working tree has uncommitted Test2 changes** (`classes.json`,
`plan.py`, goldens, the corpus index; another session's cachyos-zfs work).
E4 waits until they are committed, then re-blesses on top.

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| E1 | The ISO into rebuild-inputs on Chopin (checked); `make-from-live.sh elementary --layout distinst` and `elementary8` built on Chopin (WORK_DIR in /mnt/bko-ram, `cmp-run.sh --reserve 20 4`). Settle every *check*: os-release, the remove list and boot packages, the EFI id and NVRAM label, the fstab and crypttab forms, `generate_unique_id()`'s names, Secure Boot under OVMF, the PPAs. Done when it boots alone, `vm-update --try` is ok, and Cinqcent's copy has the same SHA-256 | 0.75 d |
| E2 | bko-holt: the `SUPPORTED` row and its tests, `update_grub()`'s id, the redaction words, a `bootloader_id()` test with E1's entry; a fake elementary tree in `holt/src/scenarios.rs` (LVM VG `data` on GPT with a /boot/efi ESP). Done when `cargo test` passes and the scenario's plan has `update-grub`, the shim-path `grub-install` and the `data` VG | 0.35 d |
| E3 | `elementary8-luks` (`--luks`): the build, console-boot harness, `rebuild-one.sh`. Done as E1, plus a boot that unlocks by the typed passphrase | 0.5 d |
| E4 | Golden plans (`holt/tests/corpus/elementary8{,-luks}/`, `index.tsv`, corpus README) from `bko-holt dump` of each guest; Test2 registrations (`classes.json` `U` and `uefi`, `holt/spec.json` with the LUKS guest's harness, `guest/spec.json`, `estimates.tsv`, `t2lib/plan.py`'s counts, `p2/world.py`, re-blessed goldens). Done when two corpus builds match, leak scans are 0, no other plan changes, Test2's tests pass | 0.4 d |
| E5 | SticTest1 entries and runs for both guests and both archivers; manifests, limits, the count. Done at 0 FAIL on Chopin and Cinqcent | 0.3 d |
| E6 | HoltTest1U runs of both on both hosts (any `judge.py` rule E1's chroot shows missing, with a judge-test case); `ram-sizes.tsv` measured. Done when every stage passes, the guest boots alone (the LUKS one by passphrase), 0 fsmeta differences, and the restored `data` VG's UUID equals the source's | 0.35 d |
| E7 | Docs: holt/README.md's list, `osrel.rs`'s comment, HoltTest1's 1U row and HoltTest1All, SticTest1's and Test2's READMEs, this spec's "What E1 found", memory (holttest1d.md's 1U line) | 0.1 d |
| E8 | *Optional, later:* BackShot1. `bs1-qemu.py --virtio` on `elementary8-luks` on both hosts, then the user installs elementary on the Thinkpad (encrypted, the installer's defaults) and runs BackShot1 with the key-file unlock. Done when phase1, plan, phase2 and verify PASS with 0 unexpected and it boots `elementary` from NVRAM | 0.5 d + ~30 min of the user's (the install) |
| | *≈ 2.75 d, 3.25 d with E8* | |

Source-code changes (E2, and the scripts in E1, E3) are done by
High-effort subagents. Each piece is committed on main when its tests pass
and the user asks. Nothing is pushed until the user says so.

**Costs** (est.):

| # | Host time | Disk | The user's time |
|---|---|---|---|
| E1 | the 3.3 GB download; a ~15 min build (unsquashfs and apt) | ISO 3.3 GB a host in rebuild-inputs; qcow2 ~6-7 GiB a host | approve the download |
| E3 | ~15 min | the same again | none |
| E5, E6 | ~1.5 h of queue a host, D ~12 a run | update overlays | none |
| E8 | rehearsals ~30 min a host; the Thinkpad run ~45 min | the backup on `mybig512` | the install, ~30 min; boot the stick if BootNext misses |

## Risks

- **Reproducing distinst, not running it.** The guest has the installer's
  layout and packages but not every step distinst takes (its locale,
  keyboard and `oem` handling). If one matters to a restore, E1 shows it:
  compare with a real install made by hand once in a VM (~30 min, the user's
  or by VNC).
- **Secure Boot under OVMF.** If shim won't boot under the farm's enrolled
  keys, the guests run `--no-secure-boot`. They then test less than a real
  machine, but no less than `pop24`.
- **The `data` name.** Two elementary guests restored at once on one host
  must not see each other's `data` VG. vm-mount and load handle duplicate
  names now. E6 runs them one after the other on the first try.
- **Monthly point updates** change the ISO's name. The recipe pins the
  20260219 ISO; a newer one is a new pin, as for Zorin.

## Out of scope

- **BIOS installs** (MBR, /boot then LVM): the same loader path as
  `mint22`, untested on elementary. A third guest is +0.4 d.
- **Manual partitioning** (btrfs, no LVM, other file systems).
- **arm64** (8.1's new UEFI ARM image): the farm is x86-64.
- **Renaming a clashing `data` VG at restore:** load still refuses, as
  ruled 2026-11-10.
- **OS 9:** a new `SUPPORTED` row when it ships.
- BackShot2 cases on elementary.

## Decisions for the user

Approved 2026-11-14: the user chose every recommendation below.

1. **How the guests are built:** `make-from-live.sh` reproducing distinst's
   layout (recommended: the way Mint, Zorin and Pop!_OS are built, fast
   and unattended). Or run distinst itself in a live-ISO VM, if E1 finds
   its CLI on the ISO: truer to the installer (its own unique-id names and
   package steps), est. +0.5 d, and a slower, more fragile rebuild. Or the
   GUI installer by hand once, then frozen. That isn't rebuildable
   ([[update-guests-not-copy]] still works).
2. **Guests:** `elementary8` and `elementary8-luks` (recommended: the
   default install and its one offered option). Or `elementary8` only
   (−0.5 d, and BackShot1's rehearsal would use `ubuntu26-luks`). Or both
   plus a BIOS guest (+0.4 d).
3. **HoltTest1 group:** join **1U** (recommended: it boots and encrypts
   as Ubuntu does, and 1U already holds `ubuntu26-luks`). Or a new **1E**,
   as Mint (1M) and Zorin (1Z) have, to run it by name.
4. **Secure Boot on the guests:** on (recommended: shim is the installer's
   default, and E1 falls back with a note if OVMF refuses it), or off as
   `pop24` is.
5. **The `update_grub()` list:** add the siblings' ids (`elementary`,
   `anduinos`, `neon`) to the Debian-family match (recommended: one line,
   no other plan changes). Or decide by ID_LIKE containing
   `ubuntu`/`debian`, as the KDE neon spec proposes. That covers future
   derivatives, but costs more. Today's dumps don't record `id_like`
   (`os` has `id`, `version_id`, `pretty_name`...), so dump must start
   writing it, and every Linux corpus `src.json` gains the field.
   `devuan6`'s plan changes from `grub-mkconfig` to `update-grub` (Devuan
   says `ID_LIKE=debian`), and so do any other GRUB systems that are
   ID_LIKE debian but not on the list. Either is fine for elementary.
   The one rule is that the sibling sessions do it once, the same way.
6. **A supported system** in bko-holt's `SUPPORTED`, named in messages
   (recommended), or test guests only.
7. **BackShot1 on the Thinkpad (E8):** later, after E1-E7, with an
   encrypted default install (recommended: it tests `bs1-luks` on a second
   distribution). Or skip it; the Thinkpad stays on CachyOS.
