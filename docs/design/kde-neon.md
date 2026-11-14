# KDE neon support (approved)

2026-11-14. Asked for by the session that adds Ubuntu- and Arch-based
desktop distributions (siblings: AnduinOS, elementary OS, BigLinux,
Garuda). KDE neon is Ubuntu 24.04 LTS with KDE's own, rolling Plasma, Gear
and Frameworks on top. This is a derivative-sized change, like EndeavourOS
and Manjaro (commit 6715f43), not a new family like Gentoo or Chimera.

**Conventions.** est. marks an estimate. *Check* marks a fact the sources
don't settle, to be settled on the first guest (N1, N3) before the code that
depends on it. Facts come from neon.kde.org/download and files.kde.org (the
ISO's name, size and date), neon's Calamares settings
(`invent.kde.org/neon/neon/calamares-settings`, desktop edition, HEAD), its
base-files fork (`neon/forks/base-files`, branch `Neon/release_noble`) and
settings repository (`neon/neon/settings`), the neon-commits list, KDE
Discuss and the KDE Linux alpha announcement, all read 2026-11-14. Code
references are to local `main` at 6a78448. The user approved this spec on
2026-11-14 with every recommended decision (the last section).
Status 2026-11-14: N1-N7 done (uncommitted); N8, BackShot1 on the
Thinkpad, not started.

## Summary

- KDE neon User Edition becomes a supported system for bko-holt and both
  archivers, with one QEMU guest, `neon24` (UEFI, Secure Boot, GPT, ESP at
  /boot/efi, ext4 root, a swap file, GRUB through shim), in HoltTest1U and
  Test2's U family. No SticTest1 entry (decision 5).
- Nearly everything is already built for Ubuntu: initramfs-tools
  (`update-initramfs`), Ubuntu's signed GRUB and shim, `update-grub`, apt.
- What's new is small: the os-release ID `neon`; `update-grub` chosen by
  an ID allowlist that leaves neon out (it would fall back to
  `grub-mkconfig`); the loader under `EFI/neon` while Ubuntu's signed GRUB
  also wants `EFI/ubuntu/grub.cfg`; and a `make-from-live.sh` path that does
  what neon's Calamares does.
- est. 1 to 1.5 days of work, 6 to 10 hours of farm time (one guest build,
  two HoltTest1 runs, one Test2 pass).

## Is neon still worth supporting?

Yes, for now, with a review point. Status on 2026-11-14:

- **Alive and current.** neon-user-desk-20261025-1825.iso (3.15 GiB,
  2026-10-25) ships Plasma 6.8.1, Gear 26.08.2 and Frameworks 6.31 on Ubuntu
  24.04. ISOs come every few weeks; the settings and base-files repositories
  had commits in October 2026.
- **Thinly staffed.** KDE's KDE Linux alpha announcement (2025-09-06) said
  neon isn't cancelled but has lost most of its developers and is held
  together by one volunteer, with no decision about its future. On
  2026-01-15 Nate Graham wrote that there are still no formal plans for KDE
  Linux to replace neon, though he personally expects it to. I found nothing
  later that changes that.
- **The 26.04 rebase is late.** Stable is still on noble; Unstable has an
  incomplete resolute archive, and some users already see a 26.04 upgrade
  prompt that fails. When it lands, os-release's VERSION_ID becomes 26.04
  (decision 2).
- **KDE Linux** (Arch-based, image-based and immutable, btrfs,
  systemd-boot, systemd-sysupdate) is alpha and meant for KDE developers. It
  isn't a neon replacement yet, and supporting it would be its own spec,
  nearer Bazzite's than this one. Out of scope here.

neon is still a common way to get the newest Plasma on an LTS base, and
supporting it costs about a day, mostly on pieces the Ubuntu siblings need
too. Review the support if KDE announces neon's end, or if the 26.04 rebase
hasn't shipped by mid-2027.

## What KDE neon is (as far as bko cares)

- **Release and ISO.** User Edition, desktop: `neon-user-desk-YYYYMMDD-HHMM.iso`
  under `files.kde.org/neon/images/desk/user/`, with a `.sig`. Current:
  `20261025-1825/neon-user-desk-20261025-1825.iso`, 3,377,463,296 bytes,
  signed by the neon ISO signing key 0x45F4C354638D1F29 (*check*: its full
  fingerprint against neon's own page before trusting it). A casper live
  system: `casper/filesystem.squashfs`, `casper/vmlinuz`. It goes in
  `/bkoscratch/rebuild-inputs/neon24/` (raw, as the other ISOs are).
- **Installer.** Calamares (since 2020), driven by `neon/calamares-settings`.
  Its exec sequence: partition, mount, unpackfs (the squashfs onto the
  target), machineid, locale, keyboard, luksbootkeyfile, users,
  displaymanager, networkcfg, hwclock; then `boot_deploy` (copies
  `/cdrom/casper/vmlinuz` to `/boot/vmlinuz-$(uname -r)`: the squashfs has
  no kernel image of its own, *check*), `boot_reconfigure`
  (`INITRD=No dpkg-reconfigure linux-image-…`), initramfscfg, initramfs,
  fstab, grubcfg (`GRUB_ENABLE_CRYPTODISK=true`), and on UEFI
  `apt install grub-efi-amd64-signed shim-signed`; bootloader; then on UEFI
  `mkdir EFI/ubuntu; cp EFI/neon/grub.cfg EFI/ubuntu/` (Ubuntu's signed GRUB
  looks for its config under EFI/ubuntu). Packages removed at the end:
  `calamares`, `neon-live`, `casper`, `^live-*`, and the file-system tools
  the install doesn't use (btrfs-progs, xfsprogs, reiserfsprogs).
- **Disk layout ("Erase disk", the default).** Calamares's defaults, since
  neon's `partition.conf` sets only `initialSwapChoice: file`,
  `enableLuksAutomatedPartitioning: true`, `drawNestedPartitions` and
  `swapPartitionName`: on UEFI, GPT with an ESP at `/boot/efi` (vfat,
  `umask=0077`; size Calamares's default, 300 MiB in 3.3, *check* the ISO's
  Calamares version) and the rest one ext4 `/`; a swap file `/swapfile`, not
  a partition; on BIOS, MBR with one ext4 `/`. "Encrypt system" puts / in
  LUKS (with /boot inside it, GRUB unlocking it, and a key file in the
  initramfs, `crypttab` `keyscript=/bin/cat`). No LVM, no btrfs by default.
- **Boot.** GRUB. UEFI: `grub-install --bootloader-id=neon` with the signed
  packages, so shim and GRUB in `EFI/neon/`, a boot entry "neon" pointing at
  `\EFI\neon\shimx64.efi`, and a `grub.cfg` copy in `EFI/ubuntu/`. Secure
  Boot works (Ubuntu's signed chain). BIOS: `grub-pc`.
- **Initramfs.** initramfs-tools (`update-initramfs`), as Ubuntu.
- **Init and packages.** systemd, apt/dpkg with neon's own archive
  (`archive.neon.kde.org/user noble main`) above Ubuntu's. Updates must be
  `full-upgrade` (Plasma updates add and remove packages); Discover and
  PackageKit do offline updates.
- **`/etc/os-release`** (neon's base-files fork, `Neon/release_noble`):

  ```
  PRETTY_NAME="KDE neon User Edition"
  NAME="KDE neon"
  VERSION_ID="24.04"
  VERSION="User Edition"
  VERSION_CODENAME=noble
  ID=neon
  ID_LIKE="ubuntu debian"
  UBUNTU_CODENAME=noble
  LOGO=start-here-kde-neon
  ```

  Two wrinkles. In 2025 an Ubuntu base-files update briefly left
  ID=ubuntu lines in the file (KDE bugs 499897, 500551, fixed). And since
  April 2026 the `Neon/rm-base-files` branch of neon's settings package
  drafts dropping the fork in favour of a `dpkg-divert` of `/etc/os-release`
  to `/etc/kde_neon/os-release`; it isn't merged. Either way bko-holt reads
  `/etc/os-release` first (`osrel.rs:61`), which is the neon file in both
  cases; and a neon system that reports ID=ubuntu 24.04 is already
  supported as Ubuntu.

## What already works, and what doesn't

**bko-holt**

- *Detection* (`holt/src/osrel.rs`): `SUPPORTED` has no `neon`, so
  `check_supported` refuses it today (derivatives don't count by ID_LIKE,
  `osrel.rs:150`). Add `("neon", "24", "KDE neon")` next to
  `("zorin", "18", …)`, its tests (the file above is accepted; `neon 22.04`
  and `neon 26.04` refused until decision 2 changes it), and the list in
  the refusal message's test (`osrel.rs:~235`). The doc comment above
  `SUPPORTED` and `holt/README.md`'s list gain KDE neon.
- *Dump* (`boot.rs`): nothing new expected. `installed_packages` already
  lists Ubuntu's boot packages (shim-signed, grub-efi-amd64-signed,
  initramfs-tools); *check* on the first dump that `boot.initramfs.generator`
  is initramfs-tools and `boot.loader` is GRUB on UEFI with the `EFI/neon`
  entry.
- *Load, grub.cfg* (`load/commands.rs:2650`, `update_grub`): picks
  `update-grub` only for os.id `debian | ubuntu | linuxmint | zorin`;
  anything else gets `grub-mkconfig -o /boot/grub/grub.cfg`. That would
  work on neon (update-grub is that command), but it's the wrong branch to
  rely on, and AnduinOS and elementary OS hit the same line. Decision 6:
  add `neon` to the list (and the siblings' IDs, in one edit). Choosing by
  ID_LIKE instead would need the dump to start recording `id_like` (no
  corpus `src.json` has it: the `os` object keeps id, version_id,
  version_codename, pretty_name, debian_version and the kernel's), every
  Linux corpus dump to gain the field, and it would move `devuan6` from
  grub-mkconfig to update-grub.
- *Load, loader* (`("grub", true)`, `commands.rs:2547`): `grub-install
  --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=neon
  --no-nvram`, the id from the layout's own boot entry (`bootloader_id`,
  `commands.rs:2840`). On Ubuntu, grub-install with the signed packages
  writes shim, signed GRUB and a stub grub.cfg into `EFI/neon`; the
  `EFI/ubuntu/grub.cfg` copy comes back with the restored ESP. *Check* on
  the first restore that the copy still points at the new / (it finds it by
  the root file system's UUID, which load keeps) and that the guest boots
  with Secure Boot on. If the copy is stale, load rewrites it from
  `EFI/neon/grub.cfg` for an os with `EFI/ubuntu/grub.cfg` but a loader
  elsewhere (est. 20 lines and a test, only if needed).
- *Initramfs*: `update-initramfs -u -k all`, as for Ubuntu. Nothing new.
- *Swap file*: `/swapfile`, as Ubuntu's `/swap.img` on `ubuntu24`, which
  passes HoltTest1. Nothing new expected.
- *LUKS* ("Encrypt system" with /boot inside the LUKS volume, GRUB
  unlocking it): not in the guest (decision 3), so not claimed. The
  ubuntu26-luks work covers LUKS on the Ubuntu base, but not GRUB's
  cryptodisk with /boot inside.

**testvm**

- *Guest build* (`testvm/make-from-live.sh`): its casper path does Mint,
  Zorin and Pop!_OS, BIOS with GRUB or (Pop!_OS) UEFI with systemd-boot.
  neon needs a third shape, UEFI with GRUB through shim, and Calamares's own
  steps: the kernel image copied from `casper/vmlinuz` and
  `dpkg-reconfigure linux-image`, `grub-efi-amd64-signed shim-signed`
  installed from the archive, `grub-install --bootloader-id=neon`, the
  `EFI/ubuntu/grub.cfg` copy, `/swapfile`, and the remove list from
  `packages.conf` (neon's ISO may have no `filesystem.manifest-remove`,
  *check*). New DISTRO `neon`; 24 GiB disk (the installed desktop is about
  10 GiB, *check*), 4 GiB RAM, `vm-run-options` `--uefi` (Secure Boot on,
  as `ubuntu26-uefi`). If the AnduinOS or elementary OS session adds a
  UEFI-GRUB path to this script first, neon uses it and adds only its own
  steps (decision 7).
- *Rebuild* (`testvm/rebuild-one.sh`): a `neon24)` line beside `zorin18`,
  and `neon24` in the "no ISO" check (`rebuild-one.sh:108`).
- *Updates* (`testvm/update/in-guest.sh`): its apt branch already does
  `dist-upgrade` (`in-guest.sh:327`) and handles PackageKit's hook and a
  left-over casper. *Check* on the first update that neon's offline-update
  machinery (Discover's `pk-offline-update`, `plasma-discover-notifier`)
  doesn't fire on the next boot; mask it as the guest's own apt jobs are
  masked if it does. Expected: no change, or a few lines.

**Tests**

- *SticTest1/SticTest2*: no entry (decision 5). The archivers see Ubuntu
  24.04's kernel, ext4 and vfat, which `ubuntu24` and `ubuntu26-uefi`
  already check; EndeavourOS and Manjaro set the precedent.
- *HoltTest1*: `neon24` in 1U (decision 4), on Chopin and Cinqcent
  (split rule), RAM-first under `cmp-run.sh --reserve` with a size from
  `ram-size.sh`, and its row in `ram-sizes.tsv`.
- *judge.py* (`testvm/holttest1d/judge.py`): grub-install's writes under
  `boot/efi/EFI/neon` are covered by the existing `^boot/efi[^/]*/EFI(/|$)`
  rule; update-initramfs and update-grub's by Ubuntu's. Expected: nothing
  new; add a rule only for what the first run shows (e.g. a rewritten
  `EFI/ubuntu/grub.cfg`).
- *Test2*: `testvm/test2/classes.json` (`holt.families.U`, `holt.uefi`),
  `estimates.tsv` (`guest:neon24`, `holt:neon24`; estimates from
  EndeavourOS's until measured), `guest/spec.json` and `holt/spec.json`
  entries, and the golden plans under `tests/fixtures/p2/golden/`
  regenerated from `world.py`, then `t2 self-test`. These files are being
  edited now (uncommitted, another session's cachyos-zfs work), and the
  four sibling distributions add rows to the same lists: decision 8.
- *BackShot1* (optional, later): the Thinkpad installed by hand with
  neon's Calamares "Erase disk" (and, as a second run, "Encrypt system",
  which BS1's unattended LUKS key file covers), then the usual
  `testvm/realworld/backshot1` round trip. It needs the user at the
  Thinkpad for the install; front-load it if run with BackShot1All.

## What N1 found

Read 2026-11-14 from `neon-user-desk-20261025-1825.iso` (good signature by
the neon ISO signing key, fingerprint `B74E A216 2376 765B EAC3 AE43 45F4
C354 638D 1F29`, from keyserver.ubuntu.com; ISO, `.sig` and the exported key
in `/bkoscratch/rebuild-inputs/neon24/` on Chopin). Volume id `neon user
20261025-18:49`.

- **os-release** is exactly as above. `/etc/os-release` is a symlink to
  `../usr/lib/os-release`, as on Ubuntu (the dpkg-divert draft isn't in it).
- **Kernel**: `linux-generic-hwe-24.04`, 7.0.0-38-generic. The squashfs's
  `/boot` has the initrd, config and System.map, but `vmlinuz-7.0.0-38-generic`
  is missing (`vmlinuz` dangles): Calamares's `boot_deploy` copy of
  `casper/vmlinuz` is needed.
- **Calamares** 3.4.2, `calamares-settings` git20260616. Its settings are
  at `/calamares/desktop/` in the image. The ESP's size isn't in neon's
  config; Calamares's built-in recommended size is used (300 MiB, taken as
  that). The swap file is made by the fstab module: `/swapfile`, **512 MiB**
  of zeros (not sparse), mode 600, `mkswap`ed (`create_swapfile`).
- **Bootloader module**: `efiBootLoader: grub`, `efiBootloaderId: neon`:
  `grub-install --target=x86_64-efi --bootloader-id=neon` (Ubuntu's
  grub-install uses shim by default when the signed packages are there),
  then the fallback `installEFIFallback` default: `EFI/neon/grubx64.efi`
  copied to `EFI/boot/bootx64.efi`. The guest differs here: with no NVRAM
  entry and Secure Boot on, OVMF needs shim as the fallback, so it gets
  `make-from-live.sh`'s shared one (shim as `EFI/BOOT/BOOTX64.EFI`, with
  `fbx64.efi` and `EFI/neon/BOOTX64.CSV`).
- **Already in the live image**: `grub-efi-amd64-signed`
  1.202.5+2.12-1ubuntu7.3, `shim-signed` 1.58, `grub-pc`, `initramfs-tools`,
  `plasma-discover` 6.8.1 and `packagekit`. Not in it: `openssh-server`.
- **Remove list**: `casper/filesystem.manifest-remove` exists (27 packages,
  among them firefox, language packs, cifs-utils, dmraid), but it's
  Ubiquity's convention; neon's Calamares removes only `calamares`,
  `neon-live`, `casper` and `^live-*` (packages.conf) and the unused file
  system tools btrfs-progs, reiserfsprogs and xfsprogs (neon-fs-cleanup),
  then autoremoves. The guest follows Calamares.

## What N3 found (the guest)

`neon24` built on the first try on Chopin (2026-11-14, 23 min under
`cmp-run.sh --reserve 1 4`, 7.8 GB qcow2), with the elementary OS session's
shared grub-shim path in `make-from-live.sh` and neon's own arms. Boot check
(`vm_boot --snapshot`): ID=neon 24.04, kernel 7.0.0-38-generic,
`SecureBoot enabled`, / on ext4, ESP 300 MiB, `/swapfile` 512 MiB in use, no
failed units. `EFI/neon` has shim, GRUB, mmx64, BOOTX64.CSV and grub.cfg;
`EFI/ubuntu/grub.cfg` is the same stub; `EFI/BOOT` has shim, fbx64 and
mmx64 (Ubuntu's grub-install wrote them all). On the first boot fbx64 made
`Boot0006* KDE neon User Edition` → `\EFI\neon\shimx64.efi`, first in
BootOrder.

Calamares 3.4.2's own Python modules (read off the image) differ from its
config files in three places, and the guest follows the code:

- The ESP is mounted with `defaults`: fstab.conf's `efiMountOptions` is no
  longer read; mount options come from the mount module, and neon sets none.
- No `GRUB_ENABLE_CRYPTODISK` for an unencrypted /: grubcfg's `defaults:`
  apply only with `overwrite`; it does set `GRUB_DISTRIBUTOR` to the
  branding's `${PRETTY_NAME}`, so the guest's is `'KDE neon User Edition'`.
- `grub-efi-amd64` isn't installed (it would replace `grub-pc`, which neon's
  installed system keeps beside `grub-efi-amd64-signed` and `-bin`).

The swap file's fstab line is Calamares's: `/swapfile swap swap defaults 0 0`.
Not reproduced: the locale helper (the guest keeps `C.UTF-8`; autoremove
takes language-pack-en), aptsources, nomodeset and the SSD tmpfs line.

## What N4 found (updates)

The first `vm-update` stopped: neon pins `python3-distupgrade`,
`ubuntu-release-upgrader-*` and `base-files` to `archive.neon.kde.org` at
Pin-Priority 1100 (`/etc/apt/preferences.d/50-neon-ubuntu-release-upgrader`,
`99-neon-base-files`), and the image held Ubuntu's newer release-upgrader,
so apt's "upgrade" of those three was a downgrade, which `-y` refuses.
`update/in-guest.sh` now adds `--allow-downgrades` to apt on `ID=neon` only.
The second run updated and merged on both hosts (kernel 7.0.0-43-generic).
Its fingerprint dump (the shipped bko-holt, without neon) exited 3, as for
an unsupported system; that goes once a bko-holt with N2 ships. The reboot
after the update came back to SSH with nothing masked; whether Discover's
offline updates ever fire wasn't looked at further (apt ran with PackageKit's
hook off, as on every apt guest).

## What N5 found (HoltTest1)

Run `ht1u`, 2026-11-14, on Chopin and Cinqcent, with a static bko-holt 0.2.6
built from this tree (`/bkoscratch/neon-build/holt/bko-holt`): **PASS on
both**, first try. The boot stage ran `update-initramfs -u -k all`,
`grub-install --target=x86_64-efi --efi-directory=/boot/efi
--bootloader-id=neon --no-nvram` and `env LANG=C update-grub`; fsmeta found 0
differences (302 from the boot stage, all under existing judge rules: the
ESP's loaders), the dumps 0 differences of 502 fields; the restored disk
booted by itself (vm-run's `--uefi`, Secure Boot on as for every `--uefi`
guest without `--no-secure-boot`), swap file on. No judge.py change. The
`EFI/ubuntu/grub.cfg` stub came back with the ESP and still names /'s UUID,
which load keeps, so the extra fix in "What already works" isn't needed.
RAM-disk peaks 13.5 GiB (Chopin) and 13.8 GiB (Cinqcent): `ram-sizes.tsv`
row D 16, M 4.

## Guests

| Guest | Firmware | Layout | Loader, initramfs | Built by |
|---|---|---|---|---|
| `neon24` | UEFI, Secure Boot | GPT: ESP /boot/efi (Calamares's size), ext4 /, /swapfile | GRUB via shim (`EFI/neon`, + `EFI/ubuntu/grub.cfg`), initramfs-tools | `make-from-live.sh neon ext4 neon-user-desk-20261025-1825.iso` |

One guest. A BIOS one adds nothing Zorin's doesn't, and the LUKS layout is
decision 3.

## Steps (ordered, est.)

- **N1** (1 h). Fetch the ISO and its `.sig` into
  `/bkoscratch/rebuild-inputs/neon24/` on Chopin (and Cinqcent, split
  rule), verify against neon's key, and read it without installing: the
  squashfs's `/etc/os-release`, `/boot` (is there a kernel image?),
  Calamares's version and partition defaults (ESP size), the manifest
  files. Settle the *checks* marked N1 here, in "What N1 found".
- **N2** (2 h, High subagent). bko-holt: `osrel.rs` row, doc comment and
  tests; `neon` in `update_grub`'s ID list (decision 6) with a test, plus
  the siblings' IDs if they're approved by then; `holt/README.md`. `cargo
  test` green. No commit until asked.
- **N3** (3 h, High subagent). `make-from-live.sh neon` (decision 7),
  `rebuild-one.sh` line; build `neon24` on Chopin with `rebuild-one.sh`,
  boot it, check SSH, Secure Boot state, `os-release`, the ESP's files and
  the NVRAM entry. Settle the N3 *checks*.
- **N4** (1 h). `vm-update` the guest on Chopin in a `--keep` overlay
  (update-guests rule), check the next boot is clean (offline updates),
  ship the overlay to Cinqcent. `in-guest.sh` change only if needed.
- **N5** (2 h farm time each host). HoltTest1 `neon24` on Chopin and
  Cinqcent, RAM-first, as a 1U run (`ht1.sh neon24 ht1u`). Fix what it
  finds (judge rules, the `EFI/ubuntu/grub.cfg` case); retest only the
  affected host. Record `ram-sizes.tsv`.
- **N6** (1.5 h). Test2 registration (decision 8): classes.json,
  estimates.tsv, the two spec.json entries, goldens, `t2 self-test`.
- **N7** (0.5 h). Docs: `testvm/holttest1d/README.md` (1U row and status),
  `holt/README.md`, the "What N1/N3/N5 found" sections here, memory note.
- **N8** (optional, later; 2 h plus the user's install). BackShot1 on the
  Thinkpad with neon, as above.

Total est. 11 h of work (N1-N7), about 1.5 days with farm waits; N8 apart.

## Risks

- **The 26.04 rebase lands mid-build.** The ISO would then be resolute and
  VERSION_ID 26.04. Mitigation: build from the pinned 20261025 ISO
  (rebuild-inputs keeps it) and follow up with decision 2's addition.
- **neon flips its os-release** (the dpkg-divert branch, or another
  base-files mix-up). Harmless for detection (neon file, or ID=ubuntu which
  is supported) as long as `/etc/os-release` is read first.
- **Ubuntu's signed GRUB finds the wrong grub.cfg** after a restore
  (`EFI/ubuntu/` stale against `EFI/neon/`). Caught by N5's boot check;
  the fix is small (above).
- **neon ends.** Then the `osrel.rs` row and guest are removed, as for any
  dropped system; nothing else depends on them.
- **Shared files with siblings** (`osrel.rs`, `commands.rs:update_grub`,
  `make-from-live.sh`, `rebuild-one.sh`, Test2's lists, the judge, the
  READMEs). Mitigated by decisions 6-8 and by editing one block at a time
  with a fresh read, never a whole-file rewrite.

## Out of scope

- KDE Linux (its own spec if wanted; image-based like Bazzite).
- neon's Testing, Unstable and Developer editions (same base, different
  archives; the os-release VARIANT differs, ID is the same, so they'd be
  accepted without being tested).
- neon on BIOS, and "Encrypt system" with /boot in LUKS, in the guests.
- The ARM (Pinebook) and Plasma Mobile / Bigscreen images.

## Decisions for the user

1. **Support neon?** Recommend **yes**, with the review point above (KDE
   announces its end, or no 26.04 rebase by mid-2027). Alternative: skip it
   and wait for KDE Linux.
2. **Which VERSION_IDs?** Recommend **24 only** now, and add 26 with a
   rebuilt `neon26` guest when a 26.04 User Edition ISO ships (every
   supported system has a passing whole-machine test). Alternative: accept
   26 now, untested, so that an early upgrader isn't refused.
3. **Which guests?** Recommend **one, `neon24`, UEFI with Secure Boot**,
   Calamares's default layout. Alternative: add `neon24-luks` ("Encrypt
   system": GRUB cryptodisk with /boot inside LUKS, which no guest has
   now), est. +0.5 day and a typed or key-file passphrase.
4. **HoltTest1 placement?** Recommend **1U** (Ubuntu-based, same boot
   tools). Alternative: its own 1K, or 1Obscure.
5. **SticTest1 entry?** Recommend **none** (Ubuntu's kernel and file
   systems, already checked; keeps SticTest2's cost flat), as for
   EndeavourOS and Manjaro. Alternative: a light entry copied from
   `ubuntu26-uefi`.
6. **update-grub for derivatives?** Recommend **the ID list**: add `neon`
   (with `elementary` and `anduinos` if their specs are approved), one
   session making the edit for all of them. No dump or corpus change, and
   devuan6's plan stays as it is. Alternative: choose by ID_LIKE (`debian`
   or `ubuntu`), which needs the dump to record `id_like`, every Linux
   corpus `src.json` to gain it, and moves devuan6 to update-grub; it saves
   a one-word edit per future derivative. Either way, the arms that choose
   the UEFI loader path (`grub-install` or keep the restored ESP) stay keyed
   on ID, because AnduinOS's GRUB is MOK-signed per machine and must not get
   Ubuntu's `grub-install`. The elementary OS spec lists the same choice
   (its decision 5); whichever the user picks applies to all three.
7. **make-from-live.sh's UEFI-GRUB path?** Recommend **shared**: this
   session writes the casper shim/GRUB UEFI path (with a per-distro
   bootloader id), whoever gets there first; elementary OS layers its own
   layout (distinst's LVM VG `data`, LUKS) on top.
   AnduinOS 2 has no casper live system (dracut, its own GRUB signing), so
   it won't use this path and won't touch it.
   neon's Calamares extras (kernel copy, `EFI/ubuntu` copy, swap file,
   remove list) stay under `distro == neon`.
8. **Test2 registration?** Recommend **one batched edit after the
   cachyos-zfs work in the tree is committed**, for neon and any sibling
   ready at the time, golden plans regenerated once. Alternative: each
   session edits and regenerates on its own, serially.
