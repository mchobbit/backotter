# AnduinOS support

2026-11-14. Asked for by the user: support AnduinOS, the Ubuntu-based
desktop distribution, the way EndeavourOS/Manjaro (6715f43), Chimera, Gentoo
and Bazzite were added. **Status: approved 2026-11-14 by the user with every recommended decision
(the last section).**

**Conventions.** est. marks an estimate. *Check* marks a fact the public
sources don't settle, to be settled on the first guest (A1) before the code
that depends on it. AnduinOS's facts come from its own pages and source,
read 2026-11-14: anduinos.com (home, HistoryBuilds, thankyou), docs.anduinos.com
(Release Notes 2.0.5, Secure-Boot-Signing-Architecture, Install From USB,
System Requirements, Disk Snapshots Manager), github.com/AiursoftWeb/AnduinOS-2
(build.sh, README), github.com/AiursoftWeb/AnduinOS-Packages (README,
`anduinos-installer-beta/BTRFS-DESIGN.md`, `anduinos-grub`,
`anduinos-secureboot-toolkit`) and the 1.4 branch's `upgrade_14_to_20.sh`.
Nothing has been downloaded or installed yet. Code references are to local
`main` at 6a78448.

## Summary

- AnduinOS 2 becomes a supported system for bko-holt and both archivers,
  with one QEMU guest (`anduinos2`), a SticTest1 entry, a place in
  HoltTest1 **1U**, a golden plan and the Test2 registrations; BackShot1 on
  the Thinkpad is an optional last step.
- It is not "Ubuntu with a theme". 2.0 swapped out Ubuntu's boot and install
  stack: **dracut** in place of initramfs-tools, its **own installer**
  (not Ubiquity, Calamares or subiquity, and no casper live system),
  **Btrfs subvolumes** with its own snapshot manager by default, and
  **its own GRUB, signed at install time with a per-machine MOK** behind
  Ubuntu's shim. So `make-from-live.sh`'s casper path (Mint, Zorin, Pop)
  doesn't build it, and bko-holt's Ubuntu UEFI arm (`grub-install`) would
  overwrite the MOK-signed GRUB.
- Most of what it needs is already built: dpkg, dracut (Fedora, Void,
  EndeavourOS), Btrfs subvolumes and snapshots sharing extents
  (`--clone-from`, Tumbleweed, CachyOS), UEFI through shim kept from the
  restored ESP (Fedora's arm), `update-grub`.
- ~3.25 d (est.), plus an optional BackShot1 run (~0.5 d and about an hour of
  the user's time to install AnduinOS on the Thinkpad).

## What AnduinOS is (as far as bko cares)

| | AnduinOS 2.0.5 | Nearest supported system |
|---|---|---|
| Release | 2.0 LTS ("resolute"), on Ubuntu 26.04's archive; 2.0 out 2026-06-30, 2.0.5 in October 2026 (the site says 2026-10-03, news 2026-10-25); support to April 2031. 1.1 (on 24.04, casper, Ubuntu's stack) is still supported to 2029; 1.2-1.4 are end of life | Ubuntu 26.04 |
| os-release | `ID=anduinos`, `ID_LIKE=debian` (**not** `ubuntu`), `VERSION_ID="2.0.0"`, `VERSION_CODENAME=resolute`, `UBUNTU_CODENAME=resolute`, from `base-files` (epoch 1, outranks Ubuntu's). Sample from `upgrade_14_to_20.sh`; *check* 2.0.5's | Zorin (`ID_LIKE="ubuntu debian"`) |
| Packages | apt and dpkg; Ubuntu's archive plus its own `packages.anduinos.com` ("Apkg", 56 packages); no snap; Flatpak for apps | Ubuntu |
| Init | systemd | Ubuntu |
| Kernel | Ubuntu's signed kernel (7.0); package name *check* | Ubuntu 26.04 |
| Initramfs | **dracut only**: initramfs-tools is removed. Image name (`/boot/initrd.img-K` as Ubuntu names it, or dracut's own `initramfs-K.img`) *check* | Fedora, Void (dracut) |
| Boot loader | **`anduinos-grub`**: Ubuntu's GRUB patch set rebuilt, shipped unsigned at `/usr/lib/anduinos-grub/grubx64.efi`, signed on the machine with a MOK (`/var/lib/shim-signed/mok/MOK.priv`, `MOK.der`, 0600 root) and put on the ESP behind Ubuntu's signed shim (`anduinos-shim-signed`) and MokManager by `anduinos-secureboot-toolkit`; a package trigger redeploys it on updates, and "a signing failure must not deploy an unsigned replacement". ESP directory `EFI/AnduinOS` (capital A). Existing pre-2.0.5 installs keep Ubuntu's signed GRUB. The first boot asks MokManager to enroll the key (password 123456). File names on the ESP and the fallback `EFI/BOOT` *check* | Fedora (shim, kept from the ESP), Ubuntu |
| Firmware | UEFI on amd64 and arm64; BIOS on amd64 too (*check* its layout) | |
| Install | Interactive native installer (`anduinos-installer-beta`, Python `installer_core`, "declarative"); no documented unattended mode (*check* whether `installer_core` takes a plan file) | none |
| Layout (Btrfs, the default) | GPT; FAT ESP at **/boot/efi** (size *check*); a **swap partition** (RAM + 1 GiB, or RAM/2 up to 64 GiB, at least 2 GiB) plus zram; one Btrfs with subvolumes `@root` → `/` (with `/boot` inside it), `@home`, `@log` → `/var/log`, `@snapshots` → `/.snapshots`, `@containers` → `/var/lib/containers`, `@libvirt` → `/var/lib/libvirt/images`; compression is file-system wide (options *check*). No LUKS or LVM in release one | CachyOS, Tumbleweed (Btrfs subvolumes) |
| Layout (ext4) | Offered; no snapshot manager (*check* its partitions) | Ubuntu |
| Snapshots | `anduinos-btrfs-snapshots-manager` (its own, not snapper): installer-made "New OS" baselines for System and Home, a snapshot before every dpkg transaction, automatic ones paused under 40 GiB free; Factory Reset rolls back to the baseline. Read-only or not, and whether GRUB lists them, *check* | snapper (Tumbleweed, CachyOS) |
| Image | `https://cf.anduinos.com/AnduinOS-2.0.5-amd64.iso` (~2.4 GB), with `AnduinOS-2.0.5-amd64.sha256` on the same host; **no GPG signature** | Mint (signed sums) |

## What A1 found (the ISO, read offline)

`AnduinOS-2.0.5-amd64.iso`, 2,364,674,048 bytes, SHA-256
`4b5c1778bbf54be7fc41202af576072ebf0931e776c35ce1b5795deb7169e810`, the
same as cf.anduinos.com's `.sha256` (pinned, Decision 4); in
`/bkoscratch/rebuild-inputs/anduinos2/` on Chopin. Read 2026-11-14 from its
`LiveOS/rootfs.squashfs`:

- **os-release:** `ID=anduinos`, `ID_LIKE=debian`, `VERSION_ID="2.0.5"`,
  `VERSION="2.0.5 (Resolute Ringtail)"`, `VERSION_CODENAME=resolute`,
  `UBUNTU_CODENAME=resolute`; `/etc/os-release` links to
  `/usr/lib/os-release`.
- **Kernel and initramfs:** `linux-generic-hwe-26.04` (7.0.0-38-generic);
  dracut 110-11ubuntu0.1, whose `01-debian.conf` sets
  `initrdname="initrd.img-${kernel}"`, so the generic loop
  (`dracut --force --kver K`) writes `/boot/initrd.img-K`: no
  AnduinOS-specific initramfs arm is needed. `/boot` has Ubuntu's
  `vmlinuz`/`initrd.img` links and `.old` links.
- **GRUB:** `anduinos-grub` 2.0.5+repack1-2 is the only GRUB package: no
  `grub-efi-amd64-signed`, no `grub-pc`. Its `grub-install` is GRUB's own
  binary, so running it would write an unsigned image. Signing goes through
  `/usr/libexec/anduinos-grub-update` (Python, `anduinos_secureboot.grub`;
  the installer runs it with `first-install`). The installer's boot step
  runs `grub-install` and then deploys shim, MokManager and the signed GRUB
  into `EFI/AnduinOS`, and for an erase-disk install also into `EFI/BOOT`
  (`BOOTX64.EFI` = shim, `grubx64.efi`, `mmx64.efi`, no `fbx64.efi`). This
  confirms Decision 3: `load` must not run `grub-install`.
- **MOK:** `/var/lib/shim-signed/mok/MOK.priv` (0600) and `MOK.der`. They
  are made and GRUB is signed **even with Secure Boot off**; only with it
  on is `mokutil --import` queued (password 123456).
- **The installer** (`anduinos-installer-beta` 2.0.5-10, Python
  `installer_core`, schema 18): `anduinos-installer-executor` takes one
  JSON `InstallPlan` on stdin, and `installer_core.planning.build_plan()`
  is what the GUI calls for "erase disk". So it can run unattended through
  its own planner and executor: Decision 1's path 1. Defaults: ESP 1024
  MiB at `/boot/efi`; swap from `calculate_swap_sizing`; zram lz4 50%;
  Btrfs `defaults,subvol=@X,compress=zstd:3,noatime` for `@root`, `@home`,
  `@log`, `@snapshots`, `@containers` and `@libvirt`.
- **Snapshot manager** (`anduinos-btrfs-snapshots-manager` 2.0.5-3, a
  compiled helper over D-Bus, CLI `anduinos-btrfs-snapshots-manager-cli`):
  - apt hook: a snapshot before every dpkg run, under a 40 GiB free-space
    floor.
  - Scheduler timer: System snapshots every 24 h, Home every 2 h.
  - GRUB snippets `01_..._env` and `09_...` (snapshot boot entries), and a
  dracut module `91anduinos-btrfs-snapshots-manager`.
  - State in `/var/lib/anduinos-btrfs-snapshots-manager` and
    `/etc/anduinos-btrfs-snapshots-manager`.
  - Its records name snapshots by **Btrfs subvolume UUID** (`snapshot_uuid`,
    `snapshot_parent_uuid`), the file system by `root_filesystem_uuid`, and
    carry SHA-256s of the kernel, initramfs, dpkg status and MOK
    certificate.
  - A restore makes new subvolumes, whose UUIDs differ, so Decision 6's case
    holds (by UUID, not by ID). The plan note stays; A5 records what
    `status`/`list` say after a restore.
- **Other:** systemd 259.9, openssh-server installed (the installer enables
  `ssh.service`), btrfs-progs 6.17.1, efibootmgr, mokutil 0.7.2,
  sbsigntool. The live boot is dracut's dmsquash-live (`root=live:CDLABEL=AOS_LIVE`), with
  `systemd-run-generator`, curl and wget on board.

## What A1 found (the guest)

Built 2026-11-14 on Chopin by Decision 1's path 1:
`testvm/make-anduinos.sh ISO WORK_DIR anduinos2` (with `testvm/anduinos/install.sh`,
run in the live system, and `anduinos/plan.py`), in /mnt/bko-ram under
`cmp-run.sh --reserve 10 5` (WEAR_TAG `build:anduinos2`): 194 s in all, the
install 130 s, the first boot ~50 s. `rebuild-one.sh anduinos2` has the recipe.
The guest is in `vms/anduinos2` on Chopin and Cinqcent (a full folder there),
qcow2 3.8 GiB, SHA-256
`5003964c34e1fd9b1b3cb4b316321042bb0d4399f98497a8f1f540256991f8d0` on both.

- **How it is driven:** QEMU boots the ISO's `/LiveOS/vmlinuz` and `initrd`
  with the first menu entry's options less `quiet splash`, plus
  `systemd.unit=multi-user.target systemd.run="..." systemd.wants=kernel-command-line.service`
  (systemd.run= alone would boot into the service, with no network) and
  `systemd.mask=serial-getty@ttyS0.service` (the getty's vhangup cut off a
  script's open fd on ttyS0). The command fetches install.sh from 10.0.2.2.
  plan.py builds the plan as frontend.py's erase-disk path does (main.py's
  default state; Btrfs, `balanced`, the installer's swap, en_US.UTF-8,
  America/New_York, `us`, host `anduinos2`, user `tester` with an
  `openssl passwd -6` hash, `install_updates` off; the disk from the GUI's
  `probe_storage_inventory`, `bind_disk_topology`, `probe_platform`), and
  `anduinos-installer-executor` runs it (RELEASE policy): 135 progress
  units, every step `succeeded` or `skipped`; the events are in the serial
  log and `facts/executor.jsonl`. The facts go out as a tar on a second
  virtio disk.
- **Firmware at install:** OVMF's Secure Boot build (SMM) with an empty
  store, so the installer sees Secure Boot `disabled`, as on a PC with it off
  (OVMF_CODE_4M.fd without SMM gives `unsupported by firmware`). The MOK is
  made and GRUB signed all the same; nothing is queued for MokManager.
- **Layout:** GPT, `vda1` 2 MiB **BIOS boot** (`bios-boot`; the installer also
  runs `grub-install --target=i386-pc /dev/vda` on a UEFI install: the disk
  boots both ways), `vda2` **1 GiB ESP** (`efi-system`, label `ANDUIN_EFI`)
  at /boot/efi, `vda3` **2 GiB swap** (`AnduinOS-swap`; with 4 GiB of RAM at
  install, so RAM/2 with a 2 GiB floor), `vda4` the rest, 22 GiB Btrfs
  (label `AnduinOS`). zram: lz4, 962 MiB at 2 GiB RAM. fstab: the six
  subvolumes with `defaults,subvol=@X,compress=zstd:3,noatime` by the Btrfs
  UUID, the ESP `defaults,noatime 0 1`, swap `sw,pri=10`. 19 GiB free on /.
- **Subvolumes** (`btrfs subvolume list`): 256 `@root`, 257 `@home`, 258
  `@log`, 259 `@snapshots`, 260 `@containers`, 261 `@libvirt` (all top level
  5), and two **read-only** snapshots under `@snapshots`:
  262 `@snapshots/anduinos-btrfs-snapshots-manager/deployments/<id>/root`
  (parent `@root`) and 263
  `@snapshots/anduinos-btrfs-snapshots-manager/personal/snapshots/<id>/home`
  (parent `@home`). The records are JSON in
  `@snapshots/anduinos-btrfs-snapshots-manager/{metadata,history}`
  (`system-lineage.json`), not in /var/lib.
- **ESP** (shim and MokManager the same on every install; GRUB is per machine):

  | file | sha256 |
  |---|---|
  | `EFI/AnduinOS/shimx64.efi`, `EFI/BOOT/BOOTX64.EFI` | `4c89145e958cf592a6f16552eadf112ef2c1c525e2435c2761e6a99fa88188b3` |
  | `EFI/AnduinOS/mmx64.efi`, `EFI/BOOT/mmx64.efi` | `5f9fc41db3dfe3b581fc20cae696af82c5ca21e96071ec9c4c28ee4a15f989d8` |
  | `EFI/AnduinOS/grubx64.efi`, `EFI/BOOT/grubx64.efi` (MOK-signed) | `95b1f0bd8f54c79f9e3cae0e72b22ba9a44779ca524a0d0f787636adfae371a3` |
  | `EFI/AnduinOS/grub.cfg`, `EFI/BOOT/grub.cfg` | `ccc8fe60ac4c35ea53bbaa80709a8c970d2fc7f01abb5645a24e57c987e0dd84` |
  | `EFI/AnduinOS/BOOTX64.CSV` | `5fed779c79530addd072cdd7a0a4bdd73d4868821a233ba02eccd5b8fb7c8552` |

  No `fbx64.efi`, no CSV in `EFI/BOOT`. The stub `grub.cfg` is
  `search.fs_uuid <Btrfs UUID> root`, `set prefix=($root)'/@root/boot/grub'`,
  `configfile`: by UUID, `@root` by path (the *check* holds). shim is signed
  by Microsoft's UEFI CA 2011 and carries Canonical's CA; `grubx64.efi` by the
  MOK (`sbverify --cert MOK.der` OK). The installer runs
  `grub-install --target=x86_64-efi ... --bootloader-id=AnduinOS --no-nvram
  --no-extra-removable --uefi-secure-boot`, then the toolkit's signed
  deploy, then `efibootmgr --create ... --label AnduinOS --loader
  '\EFI\AnduinOS\shimx64.efi'`.
- **MOK:** `CN=AnduinOS Unified Boot and Module Signing` (self-signed,
  RSA 3072, 10 years; also the DKMS key), `MOK.der` 0644 and `MOK.priv` 0600
  in /var/lib/shim-signed/mok; this guest's SHA-256
  `b39746963dd168d04d4fc5ed5dfb42fb656f1725d621c12de44ebb8ed1f7e3a4`
  (`vms/anduinos2/anduinos2-mok.der`), the same the manager's records carry
  as `mok_certificate_sha256`.
- **Secure Boot on, alone:** `vm-run --mok CERT` (new) adds the certificate
  to shim's MokList (`virt-fw-vars --add-mok 605dab50-e046-4300-abb6-3dd810dd8b23`)
  in each run's fresh copy of OVMF_VARS_4M.ms.fd; `vm-run-options` is
  `--uefi --mok BASE64` (the DER inline, so it holds wherever the disk is
  booted from: ht1's restored copy too). python3-virt-firmware and
  sbsigntool are installed on all four hosts. First boot and a boot on each
  host: `mokutil --sb-state` = `SecureBoot enabled`; `--list-enrolled` =
  Canonical's Master CA (shim's vendor key, mirrored in MokListRT) and the
  AnduinOS MOK; SSH ~50 s after power-on, `is-system-running` = running.
  With a fresh store there is no NVRAM entry, so **BootCurrent is OVMF's
  removable-media entry ("UEFI Misc Device"), not `\EFI\AnduinOS\shimx64.efi`**:
  A2's `detect_loader` can't find `via_shim` from BootCurrent on this guest
  (it would on the Thinkpad, which keeps its entry).
- **Boot files:** `/boot/initrd.img-7.0.0-38-generic` **mode 0600** (dracut),
  `vmlinuz-7.0.0-38-generic` 0600 too, Ubuntu's links and `.old` links.
  The kernel line: `root=UUID=... ro rootflags=subvol=@root console=tty0
  console=ttyS0,115200n8 preempt=full plymouth.ignore-serial-consoles` (the
  last two from `/etc/default/grub.d/99-anduinos-desktop.cfg`).
  `/boot/grub/grub.cfg` has the manager's `01_` and `09_` sections **empty**:
  GRUB lists no snapshots.
- **What the build adds** (install.sh, in a chroot from the live system):
  root's password (tester's hash) and authorized_keys; `ssh.service`
  enabled; `multi-user.target`; `GRUB_CMDLINE_LINUX` the serial console,
  `GRUB_CMDLINE_LINUX_DEFAULT` empty, `update-grub`; the manager's
  `anduinos-btrfs-snapshots-manager-scheduler.timer` disabled. sshd:
  `permitrootlogin prohibit-password` (Ubuntu's default),
  `passwordauthentication yes`.
- **Snapshot manager offline:** `status --json` works on the booted guest
  (not from the live system: it asks its D-Bus helper): `available`, one
  deployment, kind `factory`, title "New OS", reason "Initial AnduinOS
  installation", `pinned`, `state` ready, keyed by `snapshot_uuid` /
  `snapshot_parent_uuid`, plus a factory Home snapshot; `issues` empty.
  `personal-list --json` lists the Home one. The apt hook
  (`/etc/apt/apt.conf.d/90-anduinos-btrfs-snapshots-manager`,
  DPkg::Pre-/Post-Invoke) stays; with 19 GiB free the guest is under its
  40 GiB floor (`[apt] minimum_free_space_gib`). vm-update's in-guest.sh runs apt with a copy of apt.conf.d whose
  copy of that file keeps only the NeverAutoRemove line (as it already drops
  PackageKit's hook). The policy file is
  `/etc/anduinos-btrfs-snapshots-manager/apt-snapshots.toml`, and the hook
  reads `ANDUINOS_BTRFS_SNAPSHOTS_MANAGER_APT_POLICY` (untried).
- **vm-update --try** (Chopin, 2026-11-14, ~7 min): ok. apt from
  archive.ubuntu.com (https, the installer's mirror step chose it) and
  packages.anduinos.com (`resolute-addon`, `resolute-webapps`); "apt runs
  without the snapshot manager's pre- and post-dpkg hooks"; 82 packages, the
  kernel 7.0.0-38 to 7.0.0-43 (dracut wrote `/boot/initrd.img-7.0.0-43-generic`),
  no anduinos-* package newer; the reboot came up under Secure Boot on the new
  kernel; the host mounts it read-only (7 file systems); overlay 2144 MiB
  thrown away. No `in-guest: step:` was needed.

*Checks* that came out otherwise than the spec says:

- The installer does **not** enable `ssh.service`: with the GUI's default
  (no "SSH password login") its `provision-remote-access` step disables
  `ssh.service` and `ssh.socket`; ticked, it enables `ssh.socket`, a
  password-login drop-in and `ufw allow OpenSSH` (ufw is off by default).
  openssh-server is installed either way. For BackShot1 (A7): enable ssh
  before phase 1.
- A UEFI install is also a BIOS install: the 2 MiB BIOS boot partition and
  `grub-install --target=i386-pc` (the spec's "BIOS layout" *check*: the
  same disk layout). A restore of the ESP alone doesn't cover the BIOS boot
  partition's core.img; A2 may need to say what `load` does with it.
- The baselines are two snapshots (System and Home), both read-only, in the
  manager's own tree under `@snapshots`, not `@snapshots/<n>/snapshot` as
  snapper's.
- The 40 GiB floor is in the apt hook's own policy (`[apt]` in
  `apt-snapshots.toml`), so on a 25 GiB disk the pre-dpkg snapshot should
  never fire (the image keeps the baselines either way: in-guest.sh drops
  the hook).

## What A3 found (the golden plan)

- **The dump** (2026-11-14, run `a3`): `bko-holt dump` of the live guest on
  Cinqcent (Chopin's queue was full), bko-holt 0.2.6 built from this tree
  (static glibc, Rust 1.94.1), under the guest's own vm-run-options (Secure
  Boot on: `mokutil --sb-state` enabled) and `vm-run --snapshot`; the qcow2's
  SHA-256 the same before and after. No warnings. `boot.loader`: grub,
  `via_shim`, `anduinos-grub`, `anduinos-shim-signed`, `mok` with A1's
  fingerprint; with BootCurrent OVMF's "UEFI Misc Device" (a device path, no
  file) it was shim's MOK variables that showed shim. dracut is the
  generator; the six subvolumes and the two read-only baselines, with their
  parent UUIDs.
- **The corpus entry** `holt/tests/corpus/anduinos2/`: 29 identifiers,
  `check_plan` ok on the raw dump and the corpus dump, nothing scrubbed or
  filled (GRUB's own boot code isn't a blob); two builds the same
  (src.json `7bc92d23...`, index.tsv `e881be2f...`); leakscan 0 findings on
  Cinqcent (81 strings and the raw dump's identifiers), Chopin (80) and the
  Mac (git e-mail), the plan too. `golden_plans` report: anduinos2 new,
  nothing else changed, no invariant broken.
- **The plan** (155 lines): the four partitions by sfdisk with the source's
  GUIDs; vfat, swap and btrfs (label, features) and the six subvolumes; the
  restore with the baselines' contents left out, then each baseline a
  snapshot of its parent (`@root`, `@home`) made exact by `restore --exact
  --clone-from`, read-only again, IDs 262 and 263 checked. Boot stage: the
  dracut loop, os-prober off, `update-grub`; no `grub-install` (since A2b,
  the i386-pc one, below; never an x86_64-efi one), no `efibootmgr` (the
  source booted the fallback path, so there is no entry to copy, and the restored ESP's `EFI/BOOT/BOOTX64.EFI` is shim); the MOK note
  and the Disk Snapshots Manager note.
- **The BIOS boot partition was dropped silently** (fixed by A2b). The
  dump has the BIOS install (`boot_code` grub2 with its core in a partition
  and present; `boot.grub.installed_platforms` i386-pc and x86_64-efi), but
  the plan remade vda1 with its type, GUID and size and left it empty (the
  partition stage zeroes the disk's first 4 MiB, which hold it, and the
  MBR's boot code), and nothing wrote boot.img or core.img again: the
  restored disk booted by UEFI only, and no note said so. A2b: on the
  AnduinOS arm, when the dump's disk has a BIOS boot partition and
  `boot_code` says GRUB's core is in it, load also runs `chroot
  /mnt/bko-target grub-install --target=i386-pc /dev/X` before
  `update-grub`, as the installer does. The i386-pc target writes only the
  MBR, the BIOS boot partition and `/boot/grub/i386-pc`, never the ESP, so
  the MOK-signed GRUB stays as restored. A BIOS boot partition with no core
  recorded gets a note that the disk boots by UEFI only. Keeping the bytes
  instead isn't possible: dumps carry only third-party boot code, and
  core.img isn't in the backup.
- **Test2:** `anduinos2` is a HoltTest2 source in 1U, `uefi`, with entries in
  `holt/spec.json` and `guest/spec.json` (apt, S-apt, L-grub, the new
  K-anduinos, `uefi-sb`, a new guest); its estimates (holt as
  ubuntu26-uefi's 5.63 gm, guest 9 gm from A1's `--try`) until measured;
  counts 56 -> 57 sources and 58 -> 59 guests in plan.py, the README and the
  tests; the p2 world and goldens. GuestTest2's **K-anduinos**: KERNELS_MAX
  3, Debian's running-against-installed after-check, and check-image.sh lists
  each `/boot/vmlinuz-K` with a `no-initrd` mark when dracut's
  `/boot/initrd.img-K` is missing (a FAIL). It tests names with `test -e`
  only, since both files are mode 0600. known-lines.json needs nothing:
  Apkg's `Get:`/`Hit:` lines are apt's, and the rest of A1's update log
  (dracut's and GRUB's chatter, in-guest's hook line) is for the guest's
  baseline on its first GuestTest2.
- **scenarios.rs:** the fake tree has the 2 MiB BIOS boot partition with
  GRUB's boot.img and core (ESP now partition 2) and the manager's two
  baselines (`deployments/<id>/root`, `personal/snapshots/<id>/home`) in
  place of A2's `@snapshots/system-new-os`. The test now also checks the
  dump's BIOS boot evidence and both baselines' `--clone-from` restores.

## What A4 found (SticTest1)

2026-11-14, with rel6's restic-bko 0.19.1+backotter.5 and rustic-bko
0.11.4+backotter.9 (the shipped `bko-bin/linux`, the same SHA-256 on both
hosts):

- **The check** (`testvm/stictest1/anduinos2`): tumbleweed's Btrfs fixture
  with gentoo's layout and chimera-uefi's `fatattr.py`; tumbleweed's
  `bkoshim.py` stands in for setfattr (the guest has no attr package). vdb is
  laid out from the guest's own `sfdisk -d` (BIOS boot 2 MiB, the 1 GiB ESP,
  2 GiB swap, Btrfs the rest: the same starts, sizes, types and names), the
  Btrfs has the guest's six subvolumes in its ID order and the same
  superblock features (incompat 0x361, less the compression bits that use
  sets), and everything is mounted with the guest's fstab options:
  `@root` like /, the other five and the ESP inside the backed-up path.
  The read-only snapshots are made at the guest's own paths
  (`deployments/<id>/root` of `@root`, `personal/snapshots/<id>/home` of
  `@home`), next to copies of the manager's records. Each of the four
  restore targets is a loop disk image with the same layout, mounted before
  the restore. Real examples: set-id and capability binaries, shadow, fstab,
  `/etc/default/grub`, `/boot/grub`, `MOK.der` (not the private key), the
  manager's config, tester's home, the journal with its ACLs and C flag, and
  the ESP's shim, MOK-signed GRUB and MokManager with their FAT attributes.
- **Runs `a4q` (Cinqcent) and `a4c` (Chopin):** 38 PASS, 6 LIMIT, 0 FAIL on
  each. One run covers both archivers (restic->restic, rustic->rustic,
  restic->rustic, rustic->restic, both restoring over existing files, each
  checking the other's repository, the non-root run). The check takes
  ~385 s on Cinqcent and ~425 s on Chopin (the Chopin run also waited
  12 min in cmp-run.sh's line). It uses ~0.7 GiB of the RAM disk
  (`--reserve 2 6`). The README estimate is 400 s.
- **What holds:** the six subvolumes each restore onto the target's own
  subvolume with no difference (all 4 directions), and so does the ESP (377
  paths, FAT attributes the same). Under `compress=zstd:3` the Btrfs
  per-file options survive: the compression property, c, m, C and the
  inheritance of C. The hard links in the tree, in its snapshot copy and in
  `@home` share inode numbers across subvolumes (1001 on both `@root` and
  the snapshot), and each group still restores as its own: both tools key
  hard links by device and inode. The snapshots are node-identical, apart
  from the stub below.
- **The LIMITs** (classed in `limits.json`): birth times; project IDs (Btrfs
  and vfat have none); SELinux labels (AppArmor); whole-disk needs
  (HoltTest1's); the read-only snapshots come back as plain directories
  with their own extents. For example, the source's `root` snapshot has
  102 MB total, 0 exclusive; the restored copy has 102 MB exclusive. Their
  contents and metadata are checked, and A5 checks that bko-holt brings
  back the subvolume, the ro flag and the sharing. The last LIMIT is new:
  where a nested subvolume sits inside a snapshot, Btrfs puts an empty stub
  directory (inode 2) and gives it the time it reads the inode in. Its
  mtime and ctime therefore move between two stats, and between the two
  backups' nodes. This failed the first trial (`a4t1`) on 6 needs. The
  judge now leaves out the stub's times, and only the stub's.
- **Registered:** `stic/manifests.json` (with `tumbleweed/bkoshim.py` in its
  harness), `limits.json`, check-manifests' count (now 50: ubuntu24-uefi
  and pikaos4 joined from other sessions on the same day),
  `tests/test_stic.py`'s counts, and the README's estimate. The plan's
  rel4 count and the p2 goldens are A3's, and the README's host table is
  A6's.

## What A5 found (HoltTest1)

2026-11-14, bko-holt 0.2.6 built from this tree with A2 and A2b (static
glibc, Rust 1.94.1, in Chopin's warm dir `/bkoscratch/holt-warm/anduinos`;
SHA-256 `65e2cffe...`, the same file on both hosts at
`/bkoscratch/holttest1d/a5-bin/bko-holt`, given as `HT_HOLT`), rel6's
shipped rustic-bko, the guest's image `5003964c...` on both hosts.

- **Runs `a5-3` (Chopin) and `a5-4` (Cinqcent): PASS**, 26 checks each:
  every load stage (the refusals, the dry run, partition through close),
  the restored disk booting alone under the guest's own vm-run-options
  (`--uefi --mok`: Secure Boot on, the MOK in shim's MokList), boot state
  `running` as the source's, health, 0 fsmeta differences beyond the rules
  (365 and 363 paths from the boot stage), 0 dump differences (795 fields,
  36 ignored; the BIOS boot partition's `boot_code` grub2 with its core
  present, as the source's, so A2b's i386-pc install holds). Source 65 s,
  backup ~95 s, rebuild ~195 s, verify ~150 s.
- **The kept ESP:** judge.py holds `EFI/AnduinOS/{shimx64,grubx64,mmx64}.efi`
  and `EFI/BOOT/{BOOTX64.EFI,grubx64.efi,mmx64.efi}` to the source's bytes
  (no boot-stage rule excuses them on a source with AnduinOS's shim, and
  EFI/AnduinOS's three must be in the source's record): all six identical.
  ht1.sh's new check: both restored `grubx64.efi` (EFI/AnduinOS and
  EFI/BOOT) give "Signature verification OK" with `sbverify --cert`
  against `vms/anduinos2/anduinos2-mok.der`; in the booted restored guest
  `mokutil --sb-state` says `SecureBoot enabled` and `--test-key MOK.der`
  "is already enrolled". update-grub left the `grub.cfg` stubs and
  `BOOTX64.CSV` alone (no rule needed).
- **What the boot stage rewrote:** `/boot/grub` (grub.cfg, `i386-pc/`,
  fonts, locale: the existing rule), dracut's `/boot/initrd.img-K` (0600 on
  the source and 0600 after: no mode rule needed, unlike Gentoo's), and
  dracut's `var/lib/dracut/console-setup-dir`. One new rule: the source's
  own dracut run had left console-setup's mktemp file
  `etc/console-setup/tmpkbd.XXXXXX` there, and the boot stage's run remakes
  the directory without it (`missing` in `a5-1` and `a5-2`). btrfs's
  no-compress flag on 8 files of the root baseline is the existing rule's.
- **The baselines** (ht1.sh's new snapshot check, `btrfs filesystem du`):
  both come back read-only with IDs 262 and 263, parented on the restored
  `@root` and `@home`. The System baseline is 4961 MiB with 94.3 MiB
  exclusive (source: 0.4 MiB), and 86.6 MiB of that is its own
  `initrd.img-7.0.0-38-generic`, whose copy in `@root` the boot stage
  rebuilt; the rest, 7.7 MiB, is metadata and rewritten files: the extents
  are shared. Home's holds no data extents on either side. The check
  allows twice the source's exclusive plus 64 MiB, less the snapshot's own
  initramfs images (the first try counted the initrd and failed at
  94.3 MiB).
- **The Disk Snapshots Manager after the restore** (recorded, Decision 6):
  `status --json` says `available`, `issues` empty and every record
  `ready`, and `list --json` the "New OS" deployment, but they still name
  the source's subvolume UUIDs (`snapshot_uuid`,
  `snapshot_parent_uuid`: 4 in status, 2 in list), none of which is on
  the restored file system: the restored baselines have new UUIDs. So the
  manager doesn't notice that its records are stale, and a Factory Reset
  would look for subvolumes that aren't there. The plan's note stands;
  Decision 6's load step (rewriting the records to the new UUIDs) isn't
  built.
- **First tries** `a5-1` (Chopin) and `a5-2` (Cinqcent), at the estimate's
  D 8: both failed on the tmpkbd file and the initrd in the snapshot check
  (since fixed above), and both went over their reservation (8.3 and
  8.4 GiB). `a5-1` also lost its restored dump at 4 KiB while the RAM disk
  had under 1 GiB free (the harness's, not bko-holt's; `a5-2`'s dumps
  agreed). They keep everything in `/bkoscratch/holttest1d/a5-{1,2}`.
- **RAM:** peak 8.3 GiB (Chopin) and 8.0 (Cinqcent), in rebuild; memory
  2.7 GiB anon. `ram-sizes.tsv` D 10, MEM 4.
- **GuestTest1 grade: solid.** Boots alone on both hosts under Secure Boot
  with its MOK (SSH in ~45 s), the same SHA-256 on both (`5003964c...`),
  `vm-update --try` ok on Chopin (A1), SticTest1 0 FAIL on both hosts
  (A4), HoltTest1 PASS on both. Open item: no real update yet (no
  image-state; the image is still on 7.0.0-38, and `--try` would change 82
  packages).

## What A7 found (BackShot1 on the Thinkpad)

2026-11-14. The Thinkpad (X13, 8 GB, NVMe Micron 256 GB serial 214532A60C34,
Secure Boot off) was reinstalled with AnduinOS 2.0.5 **without the user and
without the GUI**: `testvm/realworld/anduinos-bootstrap.sh` (as root on the
rescue stick: `SERIAL ISO KEYS_DIR [HOSTNAME [TIMEZONE]]`) runs A1's
`plan.py` and `anduinos-installer-executor` in a chroot of the ISO's live
root. KDE neon was on the Thinkpad before (its run `neon-24.04-20261114-141609`
is in the library, untouched). Neither QEMU rehearsal ran (the
bootstrap's, `anduinos-bootstrap-qemu.sh`, and `bs1-qemu.py --virtio` on
`anduinos2`): every host's cmp-run.sh line was 20-50 jobs long and
anduinos2 was under vm-update, so the real run went first, on the
coordinator's word.

- **The chroot the executor needs:** the live root (`LiveOS/rootfs.squashfs`)
  under a tmpfs overlay; `/cdrom` and `/run/anduinos-live/rootfs.squashfs`
  laid out as the live system's dracut module (`95anduinos-live-layers`)
  lays them out; /dev, /proc, /sys (efivarfs) from the stick, a /run of its
  own with the stick's udev database, the stick's resolv.conf. Two of the
  installer's own checks shaped it: its preflight refuses to run unless the
  live media's mount (`/cdrom`) traces to a **disk** (lsblk type disk or rom;
  a loop device "fails closed"), and its media check hashes `/cdrom` by
  `md5sum.txt` and wants the source image to be the same inode as
  `/cdrom/LiveOS/rootfs.squashfs`. So the script copies the ISO's files once
  to `iso/anduinos2/AnduinOS-2.0.5-amd64.tree/` on mybig512 and binds that.
  Its `mount --make-rprivate /` (the executor's own mount namespace) works
  in a chroot whose root is a mount point.
- **The run:** the ISO (SHA-256 the pinned one) streamed to
  `mybig512/iso/anduinos2/`; the bootstrap 2 min in all (the executor 106 s,
  58 steps, every one `succeeded` or `skipped`), user `bko` (password in
  `backshot1/keys/anduinos-password`), host `thinkpad`, Europe/Paris. The
  installer saw `Secure Boot: disabled`, made the MOK and signed GRUB all the
  same, enabled the legacy BIOS GRUB and the EFI/BOOT fallback, and made
  **Boot002A "AnduinOS"** (`\EFI\AnduinOS\shimx64.efi`), first in BootOrder.
  Layout: 2 MiB BIOS boot, 1 GiB ESP, **9 GiB swap** (RAM + 1 GiB), Btrfs
  228 GiB with the six subvolumes and the two read-only baselines (262, 263);
  zram 3.7 GiB. Then the script gave root BackShot1's key and the fixed host
  key and enabled `ssh.service` (the installer had disabled it) and
  avahi-daemon (already on). The first boot came from Boot002A by itself;
  `thinkpad.local` answered at once.
- **On the first boot**, `anduinos-dracut-migration.service` (its timer is
  enabled on a fresh install) ran 30 s after boot: "legacy generator is
  absent; validating", rebuilt the initramfs **host-only** (90.8 MB ->
  41.8 MB), wrote `/var/lib/anduinos-dracut-migration/complete` and
  disabled its own timer. It doesn't run again; the next boot's
  `anduinos-dracut-confirm-boot` writes `boot-confirmed` there. Both under
  `var/lib/*`, so nothing for the manifest.
- **`bs1-adopt` installed nothing** (openssh-server, avahi-daemon, python3,
  efibootmgr, btrfs-progs all there; ufw off). No reboot needed (no LUKS).
- **BackShot1: PASS** (run `anduinos-2.0.5-20261114-153639`, restore
  `20261114-154247`), bko-holt 0.2.6 static from main 66c4cd9 plus the
  working tree (`/bkoscratch/holttest1d/a7-bin/bko-holt` on Chopin,
  SHA-256 `8f5e153b...`), rustic-bko 0.11.4+backotter.11 (the drive's, as
  neon's and Pika's runs used).
  - Preflight OK, `UNLOCK=none`; seven mounts backed up (`/`, `/.snapshots`,
    the ESP, `/home`, `/var/lib/containers`, `/var/lib/libvirt/images`,
    `/var/log`).
  - Phase 1: 2 min 41 s; "stopped
    anduinos-btrfs-snapshots-manager-scheduler.timer for this boot";
    558,218 files, 12.2 GiB (2.4 GiB added), `check --read-data` OK.
  - Plan: as the golden plan (A3): the four partitions, the six subvolumes,
    both baselines by `--clone-from` and set read-only again, the dracut
    loop, `grub-install --target=i386-pc /dev/nvme0n1` (A2b), `update-grub`,
    one `efibootmgr --create ... --label AnduinOS`, no x86_64-efi
    grub-install; the MOK note (fingerprint `f4f05036...`, this machine's
    MOK) and the Disk Snapshots Manager note.
  - Phase 2: 4 min 17 s (restore 110 s, compare 93 s with 0 differences,
    boot stage 24 s). The entry came back as Boot002A again (5744ff0 deleted
    the old copy first), BootNext 002A; the firmware has 44 entries after the run.
  - Verify: the restored system booted Boot002A by itself; every check ok;
    592,941 files compared: missing 22, added 27, changed 394, xattrs 1,
    **0 unexpected**. All a boot's or the boot stage's: `boot/grub`
    (`i386-pc/` 310 files, locale, grub.cfg), the initramfs (rebuilt on the
    stick: 41.8 -> 44.3 MB), `var/tmp/systemd-private-*`, `var/lib/*`,
    `var/log/*`, and CUPS's files for a network printer it found on the LAN
    (`etc/cups/*`). The six MOK-signed loaders were held to their bytes and
    came back identical (shim `4c89145e...` and MokManager `5f9fc41d...` as
    on the guest; this machine's GRUB `f392b0ba...`); the `grub.cfg` stubs
    and `BOOTX64.CSV` unchanged too.
  - No snapshot came or went between the backup and verify (the scheduler
    was held in phase 1 and verify, and its 2-min-after-boot run on the
    restored system made none), so the manifest's Disk Snapshots Manager rule
    (below) didn't come into play.
- **The Disk Snapshots Manager after the restore** (recorded, Decision 6):
  `status --json` `available: true`, `issues` empty, one deployment, the
  factory "New OS", `ready`, pinned; `list --json` the same. As on the guest
  (A5), its records still name the source's subvolume UUIDs: 2
  `snapshot_uuid` and 2 `snapshot_parent_uuid` in status, 1 and 1 in list,
  **none of them on the restored file system** (the restored baselines are
  `f305d10f...` and `31a5502e...`, parented on the restored `@root` and
  `@home`). The file system's UUID is kept (`4ef90856...`).
- **BackShot1's changes** (`testvm/realworld/backshot1`):
  - `bs1-common`: `SNAPSHOTTERS` gains
    `anduinos-btrfs-snapshots-manager-scheduler` (hourly, and 2 min after
    each boot; System snapshots every 24 h, Home every 2 h, and pruning).
    The apt hook needs no hold: BackShot1 never runs dpkg.
  - `bs1-manifest`: with a baseline that has `EFI/AnduinOS/shimx64.efi`,
    shim, MokManager and GRUB in EFI/AnduinOS and EFI/BOOT are `KEPT`
    (byte-identical, none missing or added; the PikaOS session's mechanism);
    a manager snapshot (`deployments/ID`, `personal/snapshots/ID`) only one
    side has is expected, as snapper's, and the manager's records (`metadata/`,
    `history/`, `personal/metadata/`, locks) then too, only then. The dracut
    images were already expected (`boot/initrd.img-*`).
  - `bs1-adopt`: nothing.
- **Left on the Thinkpad:** the restored AnduinOS 2.0.5, booted from
  Boot002A, the scheduler timer started again (verify stops it for its
  boot), mybig512 mounted at `/mnt/bs1-backup`, the stick still in.
- **Not done:** both QEMU rehearsals (the scripts are ready:
  `anduinos-bootstrap-qemu.sh TEST_IMG ISO WORK_DIR [PORT]`, and
  `bs1-qemu.py --virtio` on anduinos2).

## What already works, and what doesn't

**bko-holt `dump`:**

- `osrel.rs` `SUPPORTED` refuses it: no `anduinos` row, and ID_LIKE doesn't
  count. It needs `("anduinos", "2", "AnduinOS 2")`, with a unit test from
  the guest's os-release and a refusal of `anduinos 1.x` (see Decision 2).
- Packages: dpkg is read already. `PACKAGES` (`boot.rs`) and
  `testvm/update/fingerprint.py` gain `anduinos-grub`, `anduinos-shim-signed`,
  `anduinos-secureboot-toolkit` and `anduinos-btrfs-snapshots-manager`.
- The initramfs generator comes out `dracut` already (`has("dracut")`, no
  initramfs-tools).
- The loader: `detect_loader` finds GRUB through shim from BootCurrent
  (`\EFI\AnduinOS\shimx64.efi`, *check* the name) and sets `via_shim`.
  New: `boot.loader.mok` records whether the ESP's GRUB is AnduinOS's
  MOK-signed one (`anduinos-grub` installed and
  `/var/lib/shim-signed/mok/MOK.der` present), with the MOK's SHA-256
  fingerprint, so `load` and the plan can say which key the restored
  machine's firmware must hold.
- `bootloader_id()` keeps the case: `AnduinOS`, not `anduinos`.
- Btrfs: subvolumes, their mount points and snapshots under
  `@snapshots` are dumped as Tumbleweed's and CachyOS's are; read-only
  snapshots keep their flag.

**bko-holt `load`:**

- Initramfs: the generic dracut loop (`dracut --force --kver K` in the
  chroot). If A1 finds Ubuntu's name (`/boot/initrd.img-K`) and the loop's
  default doesn't give it, AnduinOS gets Gentoo's treatment: the file named
  on the command line (`dracut --force /boot/initrd.img-K K`). Either way a
  unit test pins it.
- **UEFI GRUB:** the Ubuntu arm runs `grub-install --target=x86_64-efi
  ... --bootloader-id=AnduinOS`. On AnduinOS that writes an unsigned (or
  Ubuntu-signed) GRUB where the MOK-signed one was, which breaks Secure Boot,
  and the toolkit's `grub-multi-install` hook only accepts its own target.
  New arm, for `os.id == anduinos` with `via_shim`: **no grub-install**.
  Shim, MokManager and the MOK-signed GRUB come back with the ESP; only
  `update-grub` runs (with os-prober bound off) and the NVRAM entry is made
  (`efi_entries()`), as Fedora's arm does (Decision 3). The plan notes the
  MOK fingerprint and that on other hardware the key must be enrolled
  again (`mokutil --import /var/lib/shim-signed/mok/MOK.der`, typed at
  MokManager on the next boot) or Secure Boot turned off.
- The restored GRUB finds `/boot` through the ESP's `grub.cfg` stub, by the
  Btrfs file system's UUID, which `format` keeps (as for NixOS and Fedora).
  *Check* that the stub searches by UUID and names `@root` by path.
- `update_grub()`: `anduinos` joins `debian | ubuntu | linuxmint | zorin`
  (Ubuntu's `update-grub` is kept by anduinos-grub). The KDE neon spec
  (another session, not built yet) adds `neon`, `elementary` and `anduinos`
  to this list in one edit; whichever lands first adds `anduinos`, and the
  other doesn't repeat it. The list stays keyed on ID: dumps don't record
  ID_LIKE. The AnduinOS shim arm is keyed on `os.id` too.
- Without shim (Secure Boot off at install, or BIOS): the Ubuntu arms as they
  are, with a unit test each. A pre-2.0.5 install on Ubuntu's signed GRUB
  (`grub-efi-amd64-signed`, no `anduinos-grub`) takes Ubuntu's arm.
- Snapshots: `--clone-from` keeps their extents shared, as on CachyOS.
  Nothing in the boot stage runs dpkg, so the manager's pre-dpkg hook
  doesn't fire in the chroot. *Check* that the manager's baseline records
  (it names its "New OS" snapshots somewhere) survive a restore by subvolume
  ID or by path: a restore makes new subvolume IDs. If it keys on IDs, the
  plan notes that Factory Reset needs re-pointing (Decision 6).

**The rescue image:** no change ([[one-kernel-rescue]]): it already has
btrfs-progs, dosfstools and dracut runs in the chroot.

## Guest

One guest, `anduinos2`: UEFI with **Secure Boot on**, the default Btrfs
layout, the installer's snapshots, the MOK-signed GRUB. It is the only one
that tests something new; ext4 and BIOS are Ubuntu's paths, which 1U covers.

How it is built (Decision 1):

1. **The installer's own backend, unattended** (recommended, if A1 finds
   that `installer_core` takes a plan file or arguments): boot the ISO in
   QEMU under `vm-run --uefi --no-secure-boot`, reach the live system over
   the serial console or SSH as `make-arch.sh --live` does for EndeavourOS,
   and run the backend with a plan for "erase disk, Btrfs". That gives the
   real subvolumes, swap, baselines and the MOK signing, as the user's
   installer would.
2. **`make-from-live.sh anduinos btrfs ...`, by hand** (the fallback): a
   new `dracut` live family beside casper and antix that unpacks the ISO's
   SquashFS, lays out BTRFS-DESIGN's subvolumes and swap partition, removes
   the installer and live packages, writes fstab, runs dracut, and runs the
   toolkit to make a MOK, sign GRUB and deploy it. The snapshot baselines
   are made with the manager's own command. Closer to a copy of the
   installer than the installer: est. +0.5 d and more drift risk.
3. **By hand once through the GUI**, kept as a pinned image: cheapest now,
   but not rebuildable (`rebuild-one.sh` can't make it), so not
   recommended.

Secure Boot in the guest: the MOK has to be enrolled for the guest to boot
alone. The build enrolls it into the guest's own OVMF variable store
(`virt-fw-vars --add-mok` from python3-virt-firmware, *check* it is on both
hosts, or answer MokManager once over the console with the 123456 password,
as `omarchy/type-luks-passphrase.py` types), and writes `vm-run-options`
(`--uefi --uefi-vars anduinos2.vars`). HoltTest1's boot stage then proves
the restored ESP still boots under Secure Boot with the same MOK.

Each piece as for the others: the ISO and its `.sha256` in
`/bkoscratch/rebuild-inputs/anduinos2/` on both hosts (the build checks the
SHA-256 pinned in the script, recorded on first download, Decision 4),
`rebuild-one.sh` (`anduinos2)` line and the ISO check), `DISK_SIZE=25G` (the
installer's minimum is 25 GiB; *check* that the snapshot pause at 40 GiB free
is harmless), `holttest1d/ram-sizes.tsv` (est. D 8, MEM 4 until measured),
both hosts by `vm-store` publish, a GuestTest1 grade.

**vm-update:** apt works as on Ubuntu, plus the Apkg repository. The
manager's pre-dpkg snapshot would add a snapshot to the image at every
update; `in-guest.sh` (`# t2-section: Linux.apt`) pauses automatic snapshots
for the update and deletes any it made, so the image keeps the installer's
baselines only (Decision 5). `fingerprint.py`'s `LINUX_KINDS` and the
`vm-update` header name it. GuestTest2: a `K-anduinos` rule in
`t2lib/guest_judge.py` (`KERNELS_MAX`, dracut image names) and
`guest/known-lines.json` lines for the Apkg repository.

## Tests

**SticTest1** (`testvm/stictest1/anduinos2`): `check.sh`, `inner.sh`,
`fixture.sh` and `judge.py` from `tumbleweed`'s (Btrfs subvolumes and
read-only snapshots) with `chimera-uefi`'s `fatattr.py` for the ESP at
`/boot/efi`, and a scratch disk laid out like the guest's (GPT, an ESP, a
swap partition, a Btrfs with the six subvolumes and a read-only snapshot,
compression as the guest's). Both archivers. `stic/manifests.json`,
`limits.json`, and `check-manifests.py`'s count goes up by one (47 today).

**HoltTest1:** `anduinos2` joins **1U** (Decision 7): `classes.json`'s
`H.U`, the README's 1U row and HoltTest1All's members. judge.py: allow what
the boot stage rewrites (`boot/grub/grub.cfg`, `boot/efi/EFI/AnduinOS/grub.cfg`
if update-grub touches it, the dracut images with their mode, as Gentoo's
0600 needed), and **require** `EFI/AnduinOS/{shimx64,grubx64,mmx64}.efi`
byte-identical to the source's: that is the test that the MOK-signed GRUB
was kept. ht1.sh's boot stage runs under the guest's `vm-run-options`
(Secure Boot on, the enrolled vars), and a new check reads the restored
GRUB's signature (`sbverify --cert MOK.der`) against the source's MOK.
Snapshots: the restored `@snapshots` keep their read-only flag and shared
extents (the exclusive sizes, as `bkoexact` checked on Tumbleweed).

**Test2:** a golden plan in `holt/tests/corpus/anduinos2/` (`src.json`,
`plan.txt`, `index.tsv`, the corpus README) from a `bko-holt dump` of the
guest; `classes.json` (`uefi`, `H.U`), `holt/spec.json`, `guest/spec.json`,
`estimates.tsv`, `t2lib/plan.py`'s counts, `tests/fixtures/p2/world.py` and
the re-blessed goldens. Done when two corpus builds have the same sha256,
leak scans are 0 on both hosts and the Mac, no other plan changes, and
Test2's own tests pass.

**BackShot1** (optional, A7): the Thinkpad reinstalled with AnduinOS 2.0.5
from the GUI installer (Btrfs, Secure Boot as the Thinkpad has it), then the
usual run. Fixes, est.: `bs1-adopt` none (apt, systemd, `ssh` and
`avahi-daemon` as on Ubuntu; *check* AnduinOS ships openssh-server, else
`apt install` before phase 1); `bs1-common`'s `package_list` none (dpkg);
a hold of the snapshot manager's timers during the backup, as 93978fd holds
snapper's; `bs1-manifest` EXPECTED gains the dracut images and
`EFI/AnduinOS/grub.cfg`, and the MOK-signed files must be unchanged. A
`bs1-qemu.py --virtio` rehearsal on `anduinos2` on both hosts first.

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| A1 | Fetch the ISO and `.sha256` into rebuild-inputs on Chopin (2.4 GB; the user's OK, Decision 4); read the live system offline (os-release, the kernel package, dracut's config and image names, `installer_core` for an unattended path, the toolkit's commands, anduinos-grub's `postinst`/`grub-multi-install`, the ESP files). Then build `anduinos2` by Decision 1's path, enroll the MOK, `rebuild-one.sh`, copy to Cinqcent (the same SHA-256), and vm-update's AnduinOS handling. Settle every *check* and write "What A1 found" here. Done when it boots alone with Secure Boot on, on both hosts, and `vm-update --try` is ok | 1.25 d (+0.5 d on path 2) |
| A2 | bko-holt (High-effort subagent): the `SUPPORTED` row and tests (2.x accepted, 1.x and `ID_LIKE=debian` lookalikes refused); `PACKAGES` and `fingerprint.py`; `boot.loader.mok`; the AnduinOS UEFI arm (no grub-install, update-grub, entries, the MOK note); `update_grub()`'s list; the dracut image name if needed; a Btrfs + shim fake tree in `scenarios.rs`. Done when `cargo test` passes and the scenario's plan has no `grub-install`, has `update-grub`, the dracut step and the MOK note. **Done 2026-11-14** (uncommitted): lib tests 391 pass. The arm keeps the ESP when os.id is anduinos and either `boot.loader.mok` is in the dump, or the system booted through shim with `anduinos-grub` installed; Ubuntu's pre-2.0.5 signed GRUB takes Ubuntu's arm. `EFI/BOOT/BOOTX64.EFI` counts as shim when it is a byte copy of a `shimx64.efi`. `--removable` is ignored on the arm, as on Fedora's. A pure-Rust SHA-256 (`bytes.rs`) gives the MOK fingerprint. No golden plan changed: `golden_plans` fails only on `ubuntu24-luks` and `ubuntu24-uefi`, which have no corpus dump yet; that failure was already there before A2 | 0.75 d |
| A2b | A2's follow-up from A3 (`commands.rs`): AnduinOS's UEFI install is a BIOS install too, so on the AnduinOS arm each disk whose dump has GRUB's core in its BIOS boot partition (`boot_code` grub2, `grub_core_in` partition, `grub_core_present`) also gets `chroot /mnt/bko-target grub-install --target=i386-pc /dev/X`, before `update-grub`; a disk with a BIOS boot partition and no core recorded gets a note that it boots by UEFI only. The MOK note now says `grub-install --target=x86_64-efi isn't run`. **Done 2026-11-14** (uncommitted): lib tests 404 pass (new `anduinos_bios_install_too`; scenarios' `anduinos_guest` and `anduinos_fallback_boot` check the i386-pc install and no x86_64-efi one); anduinos2's golden plan re-blessed (header note and boot stage, 4 lines), no other plan changed; `golden_plans` fails only on `ubuntu24-luks` and `ubuntu24-uefi`, as before | 0.1 d |
| A3 | The golden plan and Test2 registrations (as Gentoo's G2b). **Done 2026-11-14** (uncommitted; "What A3 found"): `holt/tests/corpus/anduinos2/` from a live dump on Cinqcent (run `a3`, bko-holt 0.2.6), two builds the same SHA-256, leak scans 0 on Cinqcent, Chopin and the Mac; its plan has no grub-install, has update-grub, the dracut loop, the MOK and Disk Snapshots Manager notes, the six subvolumes and both baselines by `--clone-from`; it drops the BIOS boot partition's GRUB silently (a follow-up is proposed). 1U, `uefi`, the specs, estimates, counts 57 sources and 59 guests, K-anduinos, the p2 goldens | 0.4 d |
| A4 | SticTest1 entry and runs, both archivers, both hosts, 0 FAIL. **Done 2026-11-14** (uncommitted): runs `a4q` (Cinqcent) and `a4c` (Chopin), 38 PASS, 6 LIMIT, 0 FAIL on each, ~385 s and ~425 s ("What A4 found") | 0.35 d |
| A5 | HoltTest1 1U runs on both hosts with the judge rules, the byte-identical ESP check and the MOK signature check; the boot alone under Secure Boot; RAM peaks in `ram-sizes.tsv`. **Done 2026-11-14** (uncommitted; "What A5 found"): runs `a5-3` (Chopin) and `a5-4` (Cinqcent) PASS, 26 checks each: every load stage, the boot alone under Secure Boot (`mokutil --sb-state` enabled), 0 fsmeta and 0 dump differences, the six shim/MokManager/GRUB files byte-identical, both restored `grubx64.efi` verify against the MOK, the baselines read-only with shared extents; the snapshot manager's records still name the source's UUIDs (recorded). judge.py: the AnduinOS kept-ESP rule and dracut's `tmpkbd` file; RAM peak 8.3 GiB, D 10; GuestTest1 solid | 0.4 d |
| A6 | Docs: holt/README.md's supported list, `osrel.rs`'s comment, HoltTest1's 1U row and HoltTest1All, SticTest1's and Test2's READMEs, `layload.md`'s boot stage (the kept-ESP arm), memory (holttest1d index, an anduinos-support note). **Done 2026-11-14** (uncommitted) | 0.1 d |
| A7 | BackShot1 on the Thinkpad: the fixes above, the rehearsals, then the run. Done when phase1, plan, phase2 and verify PASS with 0 unexpected and the Thinkpad boots `AnduinOS` from NVRAM under its own Secure Boot setting. **Done 2026-11-14** (uncommitted; "What A7 found"): installed from the stick by `realworld/anduinos-bootstrap.sh` (the installer's own executor in a chroot, no GUI, none of the user's time); run `anduinos-2.0.5-20261114-153639` PASS, 0 unexpected, booted Boot002A "AnduinOS" with Secure Boot off; the MOK-signed loaders byte-identical; the manager's records still name the source's UUIDs. Not done: the two QEMU rehearsals (queues full) | 0.5 d |
| | *≈ 3.25 d without A7, ≈ 3.75 d with it* | |

Each piece is committed on main when its tests pass and the user says to
commit; nothing is pushed until the user says so. A3 and A5 touch
`classes.json`, the Test2 goldens and `holt/tests/corpus/`, which have
uncommitted cachyos-zfs work from another session on main today: they wait
until that is committed, or go in as a separate hunk.

**Costs** (est.): A1 a 20-40 min build on Chopin (`--reserve 10 4` in
/mnt/bko-ram), the copy; qcow2 ~6-8 GiB a host, the ISO 2.4 GB a host.
A4 and A5 about 1 h of queue a host. No time of the user's but the OKs,
until A7.

## Risks

- **No unattended installer.** If `installer_core` has no plan input, the
  guest is built by path 2 and may drift from what the installer does
  (subvolume options, swap, the baselines). A1 compares path 2's result to
  BTRFS-DESIGN.md and records the differences.
- **The ISO has no signature**, only a SHA-256 from the same CDN. It's
  pinned on first download (trust on first use).
- **The boot stack is new and moving** (2.0.5 introduced anduinos-grub).
  A later point release may change file names, the ESP directory or the
  toolkit. Each rebuild takes newer packages ([[images-take-os-updates]]),
  and HoltTest1's byte-identical check catches a change.
- **Keeping the ESP on new hardware**: fine on the same machine. On other
  hardware with Secure Boot on, the restored system won't boot until the MOK
  is enrolled. The plan says so, but bko can't do it unattended.
- **Snapshot-manager state by subvolume ID** (Decision 6), if A1 finds it.

## Out of scope

- AnduinOS 1.x (Decision 2): casper, initramfs-tools and Ubuntu's signed
  GRUB, the Ubuntu 24.04 path; refused by version.
- ext4 and BIOS installs: Ubuntu's arms apply, with unit tests but no guest.
- arm64.
- LUKS and TPM unlock: not in AnduinOS 2.0.5.
- Re-signing GRUB in the rescue system or enrolling a MOK from bko.
- BackShot2 cases on AnduinOS. Changes to the rescue image.

## Decisions for the user

1. **How the guest is built:** the installer's own backend driven
   unattended from the live ISO if A1 finds a way (recommended: the real
   layout, baselines and MOK signing); else `make-from-live.sh`'s new
   dracut family reproducing BTRFS-DESIGN (+0.5 d); or a hand install kept
   as a pinned, non-rebuildable image.
2. **Versions:** 2.x only, refusing 1.x (recommended: 2.0 is the LTS with
   the new stack, and 1.1 is Ubuntu 24.04 in all that bko touches); or also
   1.1 (`("anduinos", "1", ...)`, a casper guest by make-from-live.sh,
   est. +0.75 d).
3. **The boot stage with the MOK-signed GRUB:** keep the restored ESP's
   shim, MokManager and GRUB, and only run `update-grub` and make the NVRAM
   entry (recommended: Fedora's way; the MOK key and the firmware's MokList
   are the same on the same machine, and nothing unsigned is written); or
   run the toolkit's own repair in the chroot to re-sign and redeploy
   (follows the distribution, but needs efivars and the toolkit to behave in
   a chroot, and changes the ESP's files every restore); or refuse AnduinOS
   through shim, as Gentoo through shim is refused.
4. **The ISO:** download `AnduinOS-2.0.5-amd64.iso` (~2.4 GB) and its
   `.sha256` from cf.anduinos.com to Chopin's rebuild-inputs and pin the
   SHA-256 in the build script on first use (recommended; there is no
   signature to check); or the user downloads and checks it.
5. **Snapshots in the image:** the installer's baselines only, with
   automatic and pre-dpkg snapshots paused and pruned during vm-update
   (recommended: the image stays small and stable, and the restore still
   handles read-only snapshots with shared extents); or let them pile up as
   on a real desktop, which grows the image and every HoltTest1 run.
6. **If the snapshot manager keys its baselines by subvolume ID:** a plan
   note that Factory Reset needs re-pointing after a restore (recommended
   until a user needs it); or a load step that rewrites its records to the
   new IDs (est. +0.25 d).
7. **HoltTest1's group:** **1U** (recommended: an Ubuntu derivative, as
   Ubuntu's guests are run together); or a new **1An**; or **1Obscure**.
8. **BackShot1 on the Thinkpad (A7):** later, as its own step after A1-A6
   pass (recommended: it needs the Thinkpad reinstalled from the GUI
   installer, about an hour of the user's time, and the Thinkpad is in use
   for BackShot1All); or now; or not at all.
