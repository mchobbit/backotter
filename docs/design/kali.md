# Kali Linux support

2026-11-14. Asked for by the user: support Kali Linux, the Debian-testing
based security distribution, as one of the eight distros added in parallel
(with Proxmox, Guix, openSUSE Leap 16, Vanilla OS, Slackware, Solus and
OpenIndiana), the way AnduinOS, KDE neon, elementary, PikaOS, BigLinux and
Garuda were added (1edb4b5). **Status: approved 2026-11-14 by the user
with every recommended decision (the last section).**

**Conventions.** est. marks an estimate. *Check* marks a fact the public
sources don't settle, to be settled on the first guest (K1) before the code
that depends on it. Kali's facts come from its own pages, read 2026-11-14:
cdimage.kali.org/current/ (the 2026.3 listing), kali.org/blog (the 2026.3
release post, 2026-10-26), kali.org/docs (download-official-kali-linux-images,
kali-preseeding, installation/hard-disk-install, general-use/metapackages,
policy/kali-linux-relationship-with-debian). The kali-preseed-examples
repository on GitLab gave no readable content, and nothing has been
downloaded or installed yet. Code references are to local `main` at 1edb4b5.

## Summary

- Kali Linux becomes a supported system for bko-holt and both archivers,
  with one small QEMU guest (`kali`), a SticTest1 entry, a place in
  HoltTest1 **1Obscure**, a golden plan and the Test2 registrations.
- Underneath, it is Debian testing with Kali's own archive: Debian's
  installer (d-i, preseedable), apt and dpkg, systemd, initramfs-tools, GRUB
  from `update-grub`. Almost everything bko-holt needs is already there
  for Debian 13. What's new is small: an os-release row for a rolling
  release, `kali` in the `update-grub` list, a UEFI guest built by
  `make-debian.sh` with Kali's ISO, and Secure Boot off, since Kali's kernel
  is unsigned.
- No LUKS variant for now (Decision 3). The guest is headless
  (`kali-linux-core`, no desktop or tool metapackages), so it stays as small
  as `debian13`.
- ~1.75 d (est.), all on the farm; no time of the user's beyond the OKs.

## What Kali is (as far as bko cares)

| | Kali 2026.3 | Nearest supported system |
|---|---|---|
| Release | Rolling (`kali-rolling`), snapshotted four times a year as installer images; 2026.3 out 2026-10-26 (images dated 2026-10-09) | Debian 13 for the stack, Arch for "rolling" |
| os-release | Expected `ID=kali`, `ID_LIKE=debian`, `VERSION_ID="2026.3"`, `VERSION_CODENAME=kali-rolling`. None of these is on Kali's pages: *check* on K1 (if `VERSION_ID` is a year.release, the row is `"*"`, as AerynOS's) | AerynOS (dated VERSION_ID), Devuan (ID_LIKE=debian, refused as Debian) |
| Packages | apt and dpkg; Kali's archive `http.kali.org/kali` (suite *check*: `kali-rolling`), not Debian's | Debian |
| Init | systemd | Debian 13 |
| Kernel | Kali's own build, 7.1.5 in 2026.3; package `linux-image-amd64` *check*. **Unsigned**: "The Kali Linux kernel is not signed and will not be recognized by Secure Boot" (hard-disk-install) | Debian 13 |
| Initramfs | initramfs-tools (*check*) | Debian 13 |
| Boot loader | GRUB 2 via Debian's grub-installer; UEFI directory expected `EFI/kali` (*check*), and whether `shim-signed` is installed anyway (d-i adds it on amd64 UEFI for Debian; *check* for Kali) | Debian 13 (`debian13-stack`: UEFI, shim, GRUB) |
| Install | d-i, text and graphical; preseed works "being based off of Debian" (kali-preseeding). The software step lists Kali's metapackages (`kali-desktop-xfce`, `kali-linux-default`, ...) instead of Debian's tasksel tasks; how to preseed it *check* | Debian, Devuan (`make-debian.sh`) |
| Default layout | "Guided - use entire disk", all files in one partition: on UEFI a GPT with an ESP, ext4 `/` and swap (sizes *check*). Guided "encrypted LVM" (LUKS under LVM) is offered | `ubuntu26-uefi` (ext4, UEFI) |
| Images | `kali-linux-2026.3-installer-amd64.iso` (4.7 GiB), `kali-linux-2026.3-installer-netinst-amd64.iso` (712 MiB), `...-installer-purple-amd64.iso` (4.6 GiB); `SHA256SUMS` with a detached `SHA256SUMS.gpg`, signed by "Kali Linux Archive Automatic Signing Key (2025)", fingerprint `827C 8569 F251 8CC6 77FE CA1A ED65 462E C8D5 E4C5` (rsa4096, expires 2028-04-17), from `archive.kali.org/archive-key.asc` or keys.openpgp.org. Whether that key signed 2026.3's sums *check* | Debian (signed sums, key fetched by fingerprint) |

Kali Purple (the defensive edition) is the same archive and installer with
other metapackages. *Check* whether its os-release differs; if it says
`ID=kali`, it's supported along with Kali, with no guest of its own.

## What K1 found (the ISO, read offline)

`kali-linux-2026.3-installer-netinst-amd64.iso`, 746,586,112 bytes, in
`/bkoscratch/rebuild-inputs/kali/` on Chopin with `SHA256SUMS`,
`SHA256SUMS.gpg` and `kali-signing-key.gpg` (fetched from keys.openpgp.org
by the full fingerprint): `gpgv` "Good signature from Kali Linux Archive
Automatic Signing Key (2025)", made 2026-10-20 by key
`827C8569F2518CC677FECA1AED65462EC8D5E4C5`, and the ISO matches its line.
`.disk/info`: `Kali GNU/Linux 2026.3rc2 "Kali-last-snapshot" - Official
amd64 NETINST with firmware 20261009-16:28`. Read 2026-11-14:

- **os-release** (`base-files_2026.3.1`, `/usr/lib/os-release`):
  `ID=kali`, `ID_LIKE=debian`, `VERSION_ID="2026.3"`, `VERSION="2026.3"`,
  `VERSION_CODENAME=kali-rolling`, `NAME="Kali GNU/Linux"`,
  `PRETTY_NAME="Kali GNU/Linux Rolling"`. So the row is
  `("kali", "*", "Kali Linux")`, as the spec says.
- **Installer:** `/install.amd/vmlinuz`, `/install.amd/initrd.gz` (and
  `gtk/`). The boot menu passes `net.ifnames=0
  preseed/file=/cdrom/simple-cdd/default.preseed simple-cdd/profiles=kali
  desktop=xfce`. The initrd has its own `/preseed.cfg` (Kali's mirror
  `http.kali.org/kali`, `kali-rolling`, contrib and non-free on, no
  security or updates services, `pkgsel/upgrade full-upgrade`,
  `passwd/root-login false`, eatmydata). make-debian.sh's appended cpio
  replaces that file, so the Kali arm supplies the mirror answers itself.
- **Kali's simple-cdd profile** (`/simple-cdd/kali.preseed`, loaded from
  the CD after the initrd's preseed, so its answers win) repeats the
  mirror and **`passwd/root-login false`**. So root's password and key go
  in through `late_command`, and the installer makes a user `tester`.
  `kali.postinst` writes `/etc/apt/sources.list.d/kali.sources` (deb822,
  `http://http.kali.org/kali/ kali-rolling main contrib non-free
  non-free-firmware`), switches every user and root to zsh unless
  `nozsh` is on the installer's command line (the guest passes
  `nozsh`, so the farm's ssh harness keeps bash), adds the users to Kali's
  groups, and in a VM purges the microcode and graphics firmware.
- **tasksel** (`3.90+kali1`): the tasks are `core` (`kali-linux-core`,
  always installed, hidden), `standard`, `firmware` (preselected),
  `desktop` and `desktop-{xfce,gnome,kde}`, `meta-*` (top10, default,
  large, everything, purple) and `tools-*`. The guest answers
  `tasksel/first` with `standard` alone, plus `pkgsel/include
  openssh-server` (Kali has no ssh-server task).
- **Boot packages on the CD:** `grub-efi-amd64{,-bin,-unsigned}
  2.14-2+kali1`, `linux-image-amd64` 7.1.5-1kali1 (`7.1.5+kali-amd64`),
  `initramfs-tools 0.151`, `cryptsetup-initramfs`. **No `shim-signed`**
  and no `grub-efi-amd64-signed`: Secure Boot off settled; load's plain
  Debian UEFI arm applies.
- **Change from the spec:** `grub-installer/force-efi-extra-removable`
  is **true**, not false. vm-run boots every UEFI guest with a fresh
  OVMF variable store, so no NVRAM entry outlives a run, and the guest
  boots from `EFI/BOOT/BOOTX64.EFI` as the other UEFI guests do. A real
  PC's install keeps its `EFI/kali` entry instead. load's
  `bootloader_id()` still finds `EFI/kali` from the ESP's `grubx64.efi`.

## What K1 found (the guest)

Built 2026-11-14 on Chopin by `rebuild-one.sh kali` (`make-debian.sh
--kali 2026.3 ext4`), from a private copy of testvm/ in
`/bkoscratch/rebuild-kali`, its vms/ in /mnt/bko-ram under `cmp-run.sh
--reserve 4 3` (WEAR_TAG `build:kali`). It worked the first time: the
install took 762 s and the boot check gave SSH, `running`, no failed
units. The guest is in `vms/kali` on Chopin and Cinqcent: qcow2 4.0 GiB
with the `fresh-install` snapshot, SHA-256
`5913301f1ea62daf9268a38d902351e68e2f507c7cf3a13066aa4d4e5ad98ad6` on
both. The ISO, its sums and the key are in `/bkoscratch/rebuild-inputs/kali/`
on both hosts.

- **Layout** (d-i's `atomic` recipe on GPT): `vda1` 883 MiB ESP (FAT32)
  at /boot/efi (`umask=0077`), `vda2` 14.3 GiB ext4 `/`
  (`errors=remount-ro`), `vda3` 858 MiB swap; all by UUID in fstab, plus
  d-i's `/media/cdrom0` and `cdrom1` lines. 1.9 GiB used.
- **ESP:** `EFI/kali/grubx64.efi` and `EFI/BOOT/BOOTX64.EFI` only (no shim,
  no MokManager).
- **Packages:** 390. `kali-linux-core` 2026.4.2, `kali-defaults` 2026.3.2,
  systemd 262-1, initramfs-tools 0.151, openssh-server 1:10.6p1-1, zsh
  installed. Kali's profile preseed sets `pkgsel/upgrade full-upgrade`, so the install
  already took kernel **7.2.15+kali-amd64**, with the CD's 7.1.5 kept beside
  it (two `initrd.img-*`): `/boot` holds two kernels from the start.
- **apt:** `/etc/apt/sources.list.d/kali.sources` from kali.postinst (deb822,
  kali-rolling, main contrib non-free non-free-firmware), and no
  `sources.list`.
- **Users:** root and tester both have `/bin/bash` (`nozsh` worked). sshd
  is enabled, with `PermitRootLogin prohibit-password` and
  `PasswordAuthentication yes`.
- **Kernel line:** `root=UUID=... ro console=tty0 console=ttyS0,115200n8
  quiet` (`net.ifnames=0` and `nozsh` stayed with the installer, as on a
  real install).
- **vm-update --try** (Chopin, ~70 s): ok. `Hit: http.kali.org/kali
  kali-rolling`, 0 upgraded; the reboot came up on 7.2.15; nothing to
  merge. `fingerprint.py` needs nothing: its kinds are package managers,
  and Kali's is dpkg.
- **Booted alone on Cinqcent** under its `vm-run-options` (`--uefi
  --no-secure-boot`): SSH at 22 s, `running`.
- Open: no `image-state` yet (no real update or `vm-store` publish has
  run), and no GuestTest1 grade until K4 and K5 pass.

## What K3 found (the corpus entry)

- **bko-holt** 0.2.6 from this tree with K2: static glibc, Rust 1.94.1,
  SHA-256 `f2eb3264...`, at `/bkoscratch/holttest1d/k-bin/bko-holt` on
  both hosts.
- **The dump** (run `k3`, Cinqcent, 2026-11-14T05:45:51Z; Chopin's queue
  was full): of the live guest under `vm-run --uefi --no-secure-boot
  --snapshot`, with no warnings. The qcow2's SHA-256 was the same before
  and after.
- **The entry** `holt/tests/corpus/kali/` (`src.json`, `plan.txt`, its
  `index.tsv` row in guest order, a README paragraph):
  - `check_plan` ok on the raw dump (`ok:14:107`) and on the corpus dump.
  - Nothing was scrubbed or filled.
  - Two builds gave the same bytes: src.json `ba10d040...` and the row
    `d6d5e00d...`.
  - Leak scans gave 0 findings on Cinqcent (92 strings), Chopin (91) and
    the Mac (git e-mail).
  - `golden_plans`: kali new (113 lines), nothing else changed. It fails
    only on ubuntu24-luks and ubuntu24-uefi, as before.
- **The plan:**
  - One GPT by sfdisk with the source's GUIDs.
  - `mkfs.fat -F 32` with the serial; `mke2fs -t ext4` with the features,
    UUID and hash seed; `mkswap -U`.
  - A plain restore.
  - Boot stage: `update-initramfs -u -k all` (both kernels), `grub-install
    --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=kali
    --no-nvram`, os-prober off, `update-grub`.
  - No `efibootmgr`: the source booted OVMF's "UEFI Misc Device" with no
    Kali entry. A PC's entry is K2's unit test.
  - A note that `/media/cdrom0` and `cdrom1` aren't restored.
- **shim is installed, but not on the ESP.** The installed system has
  `shim-signed` 1.51+16.1-2, `shim-unsigned`, `shim-helpers-amd64-signed`
  and `mokutil`, though the CD carries none of them: the install's
  full-upgrade pulled them in. GRUB is already 2.14-4+kali1. With no
  `grub-efi-amd64-signed` and no shim on the ESP, `via_shim` is false and
  load takes the plain arm. If an update brings in
  `grub-efi-amd64-signed`, the ESP may gain `EFI/kali/shimx64.efi`: the
  restore keeps it, and the shim arm is unit-tested (K2).
- **EFI/BOOT:** the source's `BOOTX64.EFI` is a byte copy of
  `EFI/kali/grubx64.efi`. It comes back with the ESP restore and isn't
  rewritten, since there is no `--removable`. The dump's debconf
  `force_efi_extra_removable` is true, but load doesn't act on it. The
  restored copy still boots: its embedded config searches for the ext4
  UUID, which format keeps.
- `/boot` has a System.map for 7.1.5 only (Kali ships none for 7.2.15).

## What K4 found (SticTest1)

2026-11-14, with rel6's restic-bko 0.19.1+backotter.5 and rustic-bko
0.11.4+backotter.9 (`bko-bin/linux`), from `/bkoscratch/kali-wt/testvm`,
`--reserve 3 5`, ~1 GiB of the RAM disk:

- **The check** (`testvm/stictest1/kali`, built from ubuntu26-uefi's):
  - vdb is laid out from the guest's own `sfdisk -d`, first and last LBA
    too (34 and the end; the first trial's 2048 failed the layout check).
  - The ext4 has the guest's features (256-byte inodes, 4K blocks, 64M
    journal) and is mounted `errors=remount-ro`. ubuntu26-uefi's
    casefold and project-quota ext4 is at `feat`.
  - The ESP is FAT32 with the guest's geometry, mounted `umask=0077` at
    `boot/efi`, and made on the host (the guest has no dosfstools, nor
    attr or acl: xattrs and ACLs are set from Python).
  - Real examples: 17 set-id programs, the account files, fstab, sudoers,
    `/etc/default/grub`, `/boot/grub`, `kali.sources`, both GRUB images on
    the ESP with their FAT attributes, tester's home, and the journal
    with its ACLs. The guest has no capability binaries; torture.sh's
    covers that need.
  - Rustic's "additional entries exist in destination" warning is
    accepted only for the target's own `lost+found` (1 directory, 0
    files).
- **Runs:** `k4q` (Cinqcent) **35 PASS, 4 LIMIT, 0 FAIL** in 324 s.
  `k4c` and `k4c2` (Chopin) **33 PASS, 4 LIMIT, 2 FAIL** in ~330 s. The
  LIMITs (`limits.json#kali`): birth times, SELinux labels (AppArmor), NFSv4
  ACLs, and whole-disk needs (HoltTest1's).
- **The open FAIL (Chopin, restic->rustic only, 2 of 2 runs; 0 of 2 on
  Cinqcent):**
  - The needs are "Sparse files" and "The guest's own files", both
    `holes lost: guest/usr/lib/modules/7.2.15+kali-amd64/vmlinuz`.
  - The file is the guest's kernel, 14,545,408 bytes, copied
    `--sparse=always`. Its only hole is the last 512 bytes, past its last
    full 4 KiB block (28,408 blocks).
  - rustic-bko restoring restic-bko's repository writes it fully
    allocated (28,416 blocks), though the snapshot marks it `unix.sparse`.
    The other three directions keep the hole.
  - That file alone, 50 times on fresh repositories (Chopin's ext4, the
    Kali guest's ext4, tmpfs), never lost it, so it seems to need the
    whole restore's parallel writers.
  - The likely place is rustic_core's restore choosing the non-sparse,
    preallocating path for that file (`fresh` or `sparse[i]` false). It
    isn't found yet in 3bb01f1's `restore.rs`.
  - It's a bug in the rustic fork, not in Kali's support, so the judge
    isn't told to accept it. K4 stays open until the fork is fixed (a
    follow-up of its own).
- **Registered:** `stic/manifests.json` (`host: ["pkg:dosfstools"]`),
  `limits.json`, `check-manifests.py`'s count 53, `tests/test_stic.py`'s
  counts, and the README's Chopin row and estimate (kali 330 s).
  `tests.test_stic` fails at the moment only on other sessions' checks
  that are still in progress (`oi2610`, `openbsd8-crypt` with no
  manifests, and no estimate for ubuntu24-uefi).

## What K5 found (HoltTest1)

2026-11-14. Inputs:

- bko-holt `f2eb3264...` (K3's), given as `HT_HOLT`.
- rel6's shipped rustic-bko.
- ht1.sh run from the private snapshot `/bkoscratch/kali-wt`, under
  `cmp-run.sh --reserve 4 4` (WEAR_TAG `ht1:k5:kali`).

Results:

- **Runs `k5-1` (Chopin) and `k5-2` (Cinqcent): PASS on the first try**,
  22 checks each:
  - every load stage (the refusals, the dry run, partition through
    close);
  - the restored disk booting alone under `--uefi --no-secure-boot` with
    fresh OVMF variables, boot state `running`, as the source's;
  - health: fstab, 2 mounts, swap;
  - 0 fsmeta differences (362 paths from the boot stage);
  - 0 dump differences (519 fields, 8 ignored).

  Times: source 23 s, backup 12-15 s (915 MB), rebuild 106-119 s,
  verify 33-36 s; ~3 min a run. `k5-1` also waited ~14 min in Chopin's
  reservation line. Records are in `/bkoscratch/holttest1d/k5-{1,2}/kali`.
  The guest's qcow2 was unchanged on both hosts.
- **No new judge rule:** what the boot stage rewrote is already covered:
  - `boot/grub` (352 paths);
  - both initrds (7.1.5 and 7.2.15);
  - `EFI/kali/grubx64.efi` (rewritten by `grub-install`, yet
    byte-identical to the source's: same UUIDs, same image);
  - `/boot`'s and `var/tmp`'s times;
  - `var/log/bko-holt`.
- **EFI/BOOT:** the restored `BOOTX64.EFI` is the source's (the same
  SHA-256 and mtime). Both the source and the restored disk boot it:
  OVMF's `Boot0001 "UEFI Misc Device"` is the removable path. load ran
  with `--no-nvram`, and there's no NVRAM-entry check, since the guest
  has no `uefi-vars.fd`.
- **RAM:** peaks 4.0 GiB (Chopin) and 4.1 GiB (Cinqcent), in rebuild;
  memory 2.7-2.8 GiB anon. `ram-sizes.tsv`: `kali`, D 5, MEM 4 (by the
  peak rule).
- **GuestTest1 grade: solid.**
  - It boots alone on both hosts (SSH in ~22 s) and has the same SHA-256
    on both.
  - `vm-update --try` is ok on Chopin.
  - HoltTest1 passes on both hosts.
  - SticTest1 0 FAIL on both is K4's (below).
  - Open: no real update yet (no image-state).

## What already works, and what doesn't

**bko-holt `dump`:**

- `osrel.rs` `SUPPORTED` refuses it: no `kali` row, and ID_LIKE doesn't
  count. It needs `("kali", "*", "Kali Linux")` (rolling; `"*"` if
  VERSION_ID is set, `""` if it isn't), with a unit test from K1's real
  os-release and a refusal of lookalikes (`ID=kali-remix`). Decision 2 asks
  whether to accept every snapshot or only the ones tested.
- Packages, kernels, initramfs-tools, GRUB, systemd, ext4, swap and the ESP
  are all read already, as on Debian 13. `PACKAGES` (`boot.rs`) and
  `testvm/update/fingerprint.py` need nothing unless K1 finds a Kali-only
  boot package (for example a `kali-grub-theme` or Kali's `grub-efi-amd64`
  variant).
- `redact.rs`: `kali` joins the release words (`SYSTEMS`) and, if K1's
  hostname or volume labels are Kali's defaults (`kali`), `GENERIC_HOSTS`,
  so leak scans don't flag the system's own name.

**bko-holt `load`:**

- `update_grub()` (`load/commands.rs:2850`) picks `update-grub` only for
  `debian | ubuntu | linuxmint | zorin | neon | anduinos`; Kali falls to
  `grub-mkconfig`, which works but differs from what Kali runs. `kali`
  joins the list. That line is shared: elementary and the other new
  Debian-family sessions edit it too, so it's done as one anchored edit,
  noted in the shared notes first.
- UEFI GRUB: `bootloader_id()` finds `EFI/kali` from the NVRAM entry or the
  ESP's `grubx64.efi`, and the Debian/Ubuntu UEFI arm runs `grub-install
  --target=x86_64-efi --bootloader-id=kali` and makes the entry. With shim
  (if K1 finds `shim-signed`), the arm already handles it, as on
  `debian13-stack`. With no shim, the plain arm. A unit test for each.
- BIOS: Debian's arm as it is (no guest; Decision 1).
- Initramfs: the existing Debian arm, `update-initramfs -u -k all`.

**The rescue image:** no change ([[one-kernel-rescue]]).

## Guest

One guest, `kali` (Decision 1): **UEFI, Secure Boot off** (`vm-run --uefi
--no-secure-boot`), the installer's own guided layout ("use entire disk",
all files in one partition: GPT, ESP, ext4 `/`, swap), headless:
`kali-linux-core` and `openssh-server`, with no desktop and no tool
metapackages. 2 GiB RAM, a 16 GiB sparse disk, as `debian13`. It's named
`kali`, not `kali2026`, because the release is rolling, as `arch` and
`omarchy` are; the image takes each rebuild's packages
([[images-take-os-updates]]).

It tests what's new with Kali (its os-release and archive, its GRUB on
UEFI with no Secure Boot, the guided default layout) and nothing that
other guests already cover: BIOS and Btrfs are `debian13`'s, LUKS+LVM is
`debian13-stack`'s and `ubuntu26-luks`'s.

### Unattended install with Kali's preseed

`make-debian.sh` gains a `--kali` arm beside `--devuan`, reusing all of
its flow: fetch, verify by fingerprint, extract the installer's kernel
and initrd, append `preseed.cfg` and `authorized_keys` as a second cpio,
install under QEMU, power off, snapshot `fresh-install`.

- **ISO** (Decision 4): the **netinst** ISO (712 MiB) with Kali's mirror,
  as Devuan's server ISO does. The full installer ISO is 4.7 GiB a host,
  and pinning the guest to its offline package set buys nothing on a rolling
  release. `SUMS=SHA256SUMS SIG=SHA256SUMS.gpg`,
  `url=https://cdimage.kali.org/kali-$version` (a fixed release directory,
  not `current/`), `key_fpr=827C8569F2518CC677FECA1AED65462EC8D5E4C5`,
  `keyserver=hkps://keys.openpgp.org`; gpgv checks the signature as for
  Debian. Kernel and initrd at `/install.amd/vmlinuz` and
  `/install.amd/initrd.gz` *check*.
- **Firmware:** the arm boots QEMU with `-machine q35` and OVMF (the
  non-Secure-Boot `OVMF_CODE_4M.fd`), and writes `vm-run-options`
  (`--uefi --no-secure-boot`). Today `make-debian.sh` only knows BIOS
  (`-machine pc`), so this is the one structural change. It's guarded by
  the arm, and the Debian and Devuan builds don't change.
- **Preseed:** `testvm/debian/preseed.cfg` stays the template (locale,
  keyboard, network naming, UTC, root's crypted password, the late command
  that installs root's key, power off). The Kali arm appends its own
  answers, which override the template's (d-i keeps the last answer), as
  the Devuan arm does:
  - the mirror: `apt-setup/use_mirror true`, `mirror/http/hostname
    http.kali.org`, `mirror/http/directory /kali`, suite `kali-rolling`
    (*check*);
  - the layout: `partman-partitioning/default_label gpt`, `partman-auto/method
    regular`, and the guided "all files in one partition" recipe
    (`partman-auto/choose_recipe select atomic`) instead of the template's
    `backotter` recipe. The template's `expert_recipe` has to be dropped or
    overridden for this. If an appended answer can't unset it, the arm
    uses a sed on the template instead (*check*);
  - the packages: Kali's software step left empty (no desktop), and
    `pkgsel/include kali-linux-core openssh-server` (the key Kali's step
    reads *check*: `tasksel/first` or Kali's own);
  - users: root login on with the template's hash (Kali turns root
    login off by default; the farm's harness logs in as root with the key).
    *Check* whether Kali's installer insists on a user; if it does,
    `passwd/username tester` with the same hash, as the AnduinOS guest
    has.
  - GRUB: `grub-installer/bootdev` for UEFI (the ESP, not `/dev/vda`), and
    `grub-installer/force-efi-extra-removable false`, so the guest boots
    from its NVRAM entry as an installed PC does (*check* the default).
- Settings to *check* on K1 and record in "What K1 found": the ESP and swap
  sizes, `/etc/apt/sources.list`, the GRUB command line, whether
  `shim-signed` is installed, and the apt `Get:`/`Hit:` lines of Kali's
  mirror for `known-lines.json`.

Each piece as for the others: the ISO and its sums in
`/bkoscratch/rebuild-inputs/kali/` on Chopin and Cinqcent, `rebuild-one.sh`
(`kali) cmd=(asm $T/make-debian.sh --kali 2026.3 ext4 "$W")` and the ISO
check), `holttest1d/ram-sizes.tsv` (est. D 4, MEM 4, as `debian13` until
measured), both hosts by `vm-store` publish, and a GuestTest1 grade.

**vm-update:** apt as on Debian, from Kali's mirror. `fingerprint.py`'s
`LINUX_KINDS` and the `vm-update` header name it. On a rolling release
an update can bring a new kernel each time: GuestTest2's kernel rule is
Debian's `K-deb` (KERNELS_MAX as Debian's; *check* Kali keeps old kernels
as Debian does, else a `K-kali`).

## Tests

**SticTest1** (`testvm/stictest1/kali`): `check.sh`, `inner.sh`,
`fixture.sh` and `judge.py` from `ubuntu26-uefi`'s (GPT, ESP at
`/boot/efi`, ext4 `/`, swap) with `chimera-uefi`'s `fatattr.py`, and a
scratch disk laid out from the guest's own `sfdisk -d`. Both archivers,
both hosts, 0 FAIL. `stic/manifests.json`, `limits.json`,
`check-manifests.py`'s count (set to the entries present then: other
sessions are adding theirs the same days), `tests/test_stic.py`'s counts.

**HoltTest1:** `kali` joins **1Obscure** (Decision 5): `classes.json`'s
`Obscure` family, the README's 1Obscure row and HoltTest1All's members.
1D stays "Debian 13" by name, and Kali isn't Debian 13: it's testing with
its own archive and kernel. PikaOS, which is Debian-sid based, went into
1Obscure for the same reason. judge.py: the existing GRUB and
initramfs-tools boot-stage rules should cover it (`boot/grub`,
`boot/initrd.img-*`, `boot/efi/EFI/kali/grubx64.efi` rewritten by
`grub-install`). ht1.sh's boot stage runs under the guest's
`vm-run-options` (UEFI, Secure Boot off) and the restored disk must boot
alone from its NVRAM entry.

**Test2** (one batched registration pass with the other seven new
distros, after the cachyos-zfs work in the tree lands; see the shared
notes):

- a golden plan in `holt/tests/corpus/kali/` (`src.json`, `plan.txt`, its
  `index.tsv` line, the corpus README line) from a live `bko-holt dump` of
  the guest;
- `classes.json`: `Obscure` and `uefi`;
- `holt/spec.json`: `"kali": {"kind": "linux", "firmware": "uefi"}`;
- `guest/spec.json`: `kind ssh`, `plat linux`, harness `make-debian.sh`,
  `testvm/debian/**`, `testvm/kali/**` if the arm needs its own files, and
  in-guest.sh's `Linux.common` and `Linux.apt`; checks `pm apt`, `S-apt`,
  `L-grub`, `K-deb` (or `K-kali`), `B-lin`, `M-lin`, `fw uefi`,
  `established false` until it has passed once;
- `estimates.tsv` (stic, holt and guest rows as `debian13`'s until
  measured), `t2lib/plan.py`'s source and guest counts (+1 each on whatever
  they are then: 62 holt sources today), `tests/fixtures/p2/world.py`, and
  the re-blessed goldens.

It's done when two corpus builds have the same SHA-256, the leak scans
give 0 on Chopin, Cinqcent and the Mac, no other plan changes, and Test2's
own tests pass.

**BackShot1** on the Thinkpad: not planned (Decision 6).

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| K1 | Fetch the netinst ISO, `SHA256SUMS` and `SHA256SUMS.gpg` into rebuild-inputs on Chopin (712 MiB; the user's OK, Decision 4); check the signature by fingerprint; read the installer offline (kernel and initrd paths, Kali's software-step template, `partman` recipes, grub-installer's defaults). Add `make-debian.sh --kali` (UEFI, the appended preseed) and build `kali` in /mnt/bko-ram under `cmp-run.sh --reserve 4 3` (WEAR_TAG `build:kali`); `rebuild-one.sh`; copy to Cinqcent (the same SHA-256); vm-update's Kali handling. Settle every *check* and write "What K1 found" here. Done when it boots alone on both hosts and `vm-update --try` is ok. **Done 2026-11-14** (uncommitted; "What K1 found") | 0.6 d |
| K2 | bko-holt (High-effort subagent): the `SUPPORTED` row and tests (K1's os-release accepted; lookalikes refused; Decision 2's version rule); `kali` in `update_grub()`'s list (one shared edit); `redact.rs` words; unit tests for the UEFI arm with and without shim and the BIOS arm, using `bootloader_id` = `kali`; a Kali fake tree in `scenarios.rs` if the plan differs from Debian's in more than the names. Done when `cargo test` passes and the scenario's plan has `update-grub` and `grub-install --bootloader-id=kali` | 0.25 d |
| K2 done | **2026-11-14** (uncommitted; tested on Chopin in `/bkoscratch/holt-warm/kali`). `osrel.rs`: the row `("kali", "*", "Kali Linux")`, test `kali()` (2026.3's real os-release, later and earlier snapshots, no VERSION_ID; `kali-remix`, `ID_LIKE="kali debian"` and Debian with `kali-rolling` refused). `commands.rs`: `kali` in `update_grub()`'s list, test `kali_boot_tools` (bootloader_id `kali` from the ESP alone and from NVRAM; the UEFI arm's `update-initramfs -u -k all`, `grub-install --bootloader-id=kali --no-nvram`, os-prober off, `update-grub`, `efibootmgr --label kali`; `--removable` and shim variants; BIOS takes Debian's i386-pc arm). `redact.rs`: `kali` in `GENERIC_HOSTS` and `SYSTEMS`. `scenarios.rs`: `kali_guest` (the plan is stage for stage the same as the same tree with Debian 13's os-release) and `kali_fallback_boot`. `PACKAGES` unchanged. Lib tests 412 pass; `golden_plans` fails only on freebsd15-geli, ubuntu24-luks and ubuntu24-uefi, as before; no plan changed. Restoring the source's ESP brings back `EFI/BOOT/BOOTX64.EFI`, and `grub-install` rewrites it only with `--removable`, as for Debian | |
| K3 | The golden plan and Test2 registrations, in the batched pass. Done as Test2's paragraph above says. **Corpus entry done 2026-11-14** (uncommitted; "What K3 found"); the Test2 lists wait for the batched pass | 0.3 d |
| K4 | SticTest1 entry and runs: both archivers, both hosts, 0 FAIL; `manifests.json`, `limits.json`, the counts, the README estimate. **Built and registered 2026-11-14; Cinqcent 0 FAIL, Chopin 2 FAIL** (a rustic-bko sparse-restore bug, "What K4 found"): open until the fork is fixed | 0.25 d |
| K5 | HoltTest1 1Obscure runs on both hosts, PASS: the boot alone under the guest's own options, 0 fsmeta and 0 dump differences beyond the rules; RAM peaks into `ram-sizes.tsv`; GuestTest1 grade. **Done 2026-11-14** (uncommitted): `k5-1`, `k5-2` PASS ("What K5 found") | 0.25 d |
| K6 | Docs: holt/README.md's supported list, `osrel.rs`'s comment, HoltTest1's 1Obscure row and HoltTest1All, SticTest1's and Test2's READMEs, `layload.md` only if a new arm appears, memory (holttest1d index, a kali-support note). **Done 2026-11-14** (uncommitted): holt/README.md, the holttest1d README's 1Obscure row and HoltTest1All, memory; `layload.md` unchanged (no new arm) | 0.1 d |
| | *≈ 1.75 d* | |

Each piece is committed on main when its tests pass and the user says to
commit; nothing is pushed until the user says so. K2's `update_grub()`
line, `osrel.rs`'s `SUPPORTED` and its supported-names test string,
`redact.rs`'s lists, `make-debian.sh`, `rebuild-one.sh`, `classes.json`'s
`Obscure` and `uefi`, the holttest1d README's 1Obscure row and
`check-manifests.py`'s count are shared with the sibling sessions. Before
each edit, re-read the file and note the hold in the shared notes folder.

**Costs** (est.): K1 a 15-30 min build on Chopin, the copy; qcow2 ~2-3 GiB
a host (headless), the ISO 712 MiB a host. K4 and K5 about 30 min of
queue a host each, RAM-first.

## Risks

- **Kali's software step isn't Debian's tasksel.** If `pkgsel/include`
  alone doesn't keep the desktop out, the install pulls in
  `kali-linux-default` (several GiB) or stops to ask. K1 reads the
  installer's templates offline before the first build, and the serial
  log shows any question the preseed didn't answer: the install times
  out instead of finishing.
- **Rolling.** Each rebuild takes a newer kernel, GRUB and initramfs-tools
  from kali-rolling, so a Debian-testing change (for example a GRUB or
  dracut switch) can land between CarpetBeaters. HoltTest1's judge and
  `osrel`'s test catch it; the guest is rebuilt from a fixed release ISO,
  so the installer itself doesn't move.
- **The mirror** is needed at build time (netinst). If http.kali.org is
  down, the build fails cleanly; the full ISO stays the fallback (Decision 4).
- **Kali's signing key** was made in 2025 and expires 2028-04-17. The
  build fetches it by full fingerprint, so a newer key means one line
  changes.

## Out of scope

- Secure Boot (Kali's kernel is unsigned).
- Desktops and the tool metapackages, Kali Purple as a guest, Kali NetHunter,
  ARM images, WSL, the prebuilt VM images (`-qemu-amd64.7z` and so on).
- A LUKS guest (Decision 3) and BIOS or Btrfs guests (Decision 1).
- BackShot1 and BackShot2 on Kali. Changes to the rescue image.

## Decisions for the user

1. **The guest:** one guest, `kali`, on UEFI with Secure Boot off and
   Kali's guided default layout (GPT, ESP, ext4, swap), headless
   (recommended: it covers what's Kali's own and nothing that `debian13`
   or `debian13-stack` already does); or also a BIOS one (Kali's other
   install path, est. +0.25 d); or Btrfs (Kali's manual-partitioning guide
   suggests it, but `debian13` covers d-i's Btrfs).
2. **Versions:** every `kali` snapshot (`"*"`, as AerynOS's and
   Tumbleweed's, recommended: it's rolling, and today's snapshot is
   tomorrow's after one `apt full-upgrade`); or only years tested
   (`2026.*`), refusing later ones until a guest is rebuilt.
3. **A LUKS variant (`kali-luks`, d-i's guided encrypted LVM):** not now
   (recommended: LUKS2 under LVM with initramfs-tools' cryptsetup is
   already restored on `debian13-stack` (Debian's own stack) and
   `ubuntu26-luks` (guided LVM-on-LUKS), so it would add a console-typed
   passphrase run and RAM-disk time for little new coverage); or add it
   now as a second guest, preseeded with `partman-auto/method crypto` and
   its passphrase typed by `console-boot.py` or the luks-keyfile path
   (est. +0.75 d).
4. **The ISO:** the netinst ISO (712 MiB) with Kali's mirror, verified by
   the signed `SHA256SUMS` and the key's full fingerprint (recommended:
   small, and offline pinning buys nothing on a rolling release); or the
   full installer ISO (4.7 GiB a host), offline as Debian's DVD build is
   (*check* it can install `kali-linux-core` with no mirror).
5. **HoltTest1's group:** **1Obscure** (recommended: Kali isn't Debian 13,
   and PikaOS, also Debian-unstable based, is there); or **1D**, run with
   the Debian guests; or a new **1K**.
6. **BackShot1 on the Thinkpad:** not planned (recommended: Kali's
   bare-metal path is Debian's, which a headless guest covers, and the
   Thinkpad is busy with BackShot1All); or later, as an optional K7
   (~0.5 d and about an hour of the user's time).
