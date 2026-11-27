# Mageia support

2026-11-27. Prepared ahead of a request to support Mageia, the independent
RPM distribution that descends from Mandriva, with its own installer (DrakX),
package tool (urpmi) and security tool (msec). It is added the same way as
Solus, Leap 16 and PikaOS. **Status: draft for the user's approval.** Nothing
is built. No repo file but this one has changed, and nothing has run on a farm
host, the Thinkpad (the A100), mybig512 or the rescue stick.

**Conventions.** est. marks an estimate. *Check* marks a fact the sources
leave open. The first guest (M2) settles each one before any code that
depends on it is written. Mageia's facts come from its own sources, read
2026-11-27:

- blog.mageia.org ("The power of 10", 2026-06-30) and mageia.org/en/support;
- the mirror tree `ftp.fau.de/mirrors/Mageia/` (`iso/10/`,
  `distrib/10/x86_64/` with release-notes.html, `isolinux/` and
  `media/media_info/`);
- the drakx and drakx-net gitweb at master (NEWS 18.85; the ISO ships
  `drakx-installer-stage2-18.79`, so master may be slightly newer);
- the Mageia 10 RPMs themselves (kernel-desktop, dracut, grub2, grub2-efi,
  bootloader-utils, drakxtools-backend, mageia-release-common, msec,
  initscripts), unpacked in the session scratchpad;
- Mageia's docker `mkimage.sh` and osinfo-db draft MR !741;
- the download page mageia.org/en/downloads/get/?q=Mageia-10-x86_64.iso,
  which prints the release key's fingerprint;
- drakx-net's `lib/network/shorewall.pm` at gitweb master.

wiki.mageia.org sits behind a bot wall, so anything only the wiki says is a
*Check*. The raw notes, with a URL and drakx `file:line` for each fact, are in
`/Users/mchobbit/Library/Application Support/Claude/side-session-notes/local_c8b25908-9a52-4201-b71a-aae7acb962c0/mageia-facts.md`.
In that file and below, "fsedit.pm:24" means drakx's
`perl-install/fsedit.pm` at master. A cite with an RPM's name ("dracut
`usr/bin/dracut:1241`", "dxb `bootloader.pm:2161`" for drakxtools-backend's
`/usr/lib/libDrakX/`, "msec `network.py:76`") means that file in the Mageia 10
RPM, unpacked in the session scratchpad (temporary). No ISO has been
downloaded. Code references are to local `main` at d4aea22 plus the
uncommitted tree of the same day.

**Revised 2026-11-27** after a code review and a facts review: dracut's
default name is settled (it is `initrd-K.img`, so the initramfs arms are
dropped); the guest keeps its own NVRAM; the BIOS `install.sh` gap, the
GuestTest2 collector, the HoltTest1 judge rules, msec's sshd_config write,
drakx's numeric `security` and the profile's USB medium are corrected.

## Summary

- **Mageia 10 becomes a supported system** for bko-holt and both archivers.
  It gets:
  - one QEMU guest, `mageia10`, and, if Decision 4 is approved, a second,
    `mageia10-luks`;
  - a SticTest1 entry for each guest;
  - a place in HoltTest1 **1Obscure**;
  - a golden plan and the Test2 registrations;
  - BackShot1 on the A100.
- **Mageia 10, not 9.** Mageia 10 was released on 2026-06-30 and is supported
  until 2028-03-31. Mageia 9 reached EOL on 2026-09-30, so its repositories
  are frozen and a `mageia9` guest would fail vm-update and GuestTest2's
  source rule. The guests are named `mageia10*` (Decision 1).
- **Today `bko-holt dump` refuses Mageia.** There is no `mageia` row in
  `SUPPORTED` (holt/src/osrel.rs:166), and `ID_LIKE="mandriva fedora"` counts
  for nothing (holt/src/osrel.rs:332). That is by design: Mageia only looks
  like Fedora. Its initrd is `initrd-K.img`, it has no BLS entries, its ESP is
  at `/boot/EFI`, it has no shim and SELinux is off.
- **Most of the pieces already exist:**
  - GRUB 2 under `grub2-` names in `/boot/grub2`, as on Fedora and Rocky;
  - dracut;
  - rpm with its database in `/var/lib/rpm`;
  - ext4 on GPT with an ESP in fstab;
  - efibootmgr entries;
  - LUKS2 with a plain `/boot`.
- **Already works, settled by the RPMs:** load's plain dracut rebuild.
  Mageia's dracut 109 is patched to write `/boot/initrd-K.img` by default
  (dracut `usr/bin/dracut:1241-1244, 1265-1268`), and its lsinitrd falls back
  to that name (`usr/bin/lsinitrd:140-143`). So `DRACUT_EACH_KERNEL`
  (holt/src/load/commands.rs:944-955, chosen at :7301) remakes the right file
  and keeps its `--add-drivers` and resume.
- **Four real gaps in bko-holt:**
  1. the `SUPPORTED` row;
  2. dump doesn't see `initrd-K.img` and lists the flavour symlinks
     (`vmlinuz-desktop`) as kernels (holt/src/boot.rs:793-821). That is
     cosmetic for load now; it matters for the record and for K-mga;
  3. load's UEFI `/boot/grub2` arm assumes a signed Fedora GRUB behind shim
     and keeps it as restored (holt/src/load/commands.rs:6387-6395). Mageia's
     grubx64.efi is built on the machine, embeds /boot's location (by UUID or
     partition, *Check*) and file-system module, and has no shim;
  4. drakx's `/boot/grub2/install.sh` names the BIOS install disk, and
     grub2-common's scriptlet re-runs it on a GRUB update. load restores it
     unchanged, so after a restore to a differently named disk, a later
     update installs GRUB to the wrong device (Decision 11).
- **Three traps in the test harness and BackShot1:**
  - In an automatic install at security level 1 or higher, DrakX turns on
    shorewall with no ports open (install/steps.pm:814-824), which blocks
    sshd.
  - The guest boots only through its NVRAM entry: there is no
    `EFI/BOOT/BOOTX64.EFI`, so vm-run's fresh variable store boots nothing.
    The guest keeps its install's `uefi-vars.fd`, as void-zbm does.
  - msec re-applies its settings every hour, and appends a `PermitRootLogin`
    line to sshd_config when it finds none it knows.
- ~5.2 d (est.), plus BackShot1 on the A100 (~0.5 d and about an hour of the
  user's time).

## Why

- **DistroWatch #32** (*Check*: not from Mageia's sources), and the largest
  independent RPM system bko doesn't cover. None of the covered systems is
  related to it: Fedora, Rocky, openSUSE and Qubes each have their own
  lineage, and there is no Mandriva, OpenMandriva or ROSA code anywhere in
  the repo. `git grep -i mageia` finds only rEFInd's `os_mageia.png` icons on
  PikaOS's ESP (holt/tests/corpus/pikaos4/src.json:849, 1345, 1838, 2334;
  pikaos4-luks/src.json:975, 1471) and the untracked PCLinuxOS draft
  (docs/design/pclinuxos.md:151, 786: Mageia refused there by ID).
- **An RPM system that breaks Fedora's assumptions.** Its `ID_LIKE` names
  `fedora`, but its boot side isn't Fedora's:
  - the initrd is `initrd-K.img`, made by installkernel and bootloader-config,
    not kernel-install;
  - GRUB has no BLS entries and is built locally, without shim;
  - the ESP is at `/boot/EFI`.

  Supporting Mageia tests that bko-holt keys on what is on disk, not on the
  family name.
- **A third installer lineage among the RPM systems** (DrakX, after Anaconda
  and Agama), with its own layout rules: MBR on BIOS for disks under 2 TiB,
  and a warning for an encrypted `/` without a plain `/boot`.

## What Mageia is (as far as bko cares)

| | Mageia 10 | Nearest supported system |
|---|---|---|
| Release | 10, 2026-06-30; EOL 2028-03-31 (at least 18 months, or 3 months after the next release). 9 has been EOL since 2026-09-30. Upgrading 9 to 10 is supported; upgrading with a Live image is not (the release notes' "Not supported" list, which also rules out 8 to 10 and BIOS to UEFI). The support page's "3 months after the next release" line for 9 is stale | Rocky (point releases) |
| os-release | `ID=mageia`, `VERSION_ID=10` (an integer, no minor), `ID_LIKE="mandriva fedora"`, `PRETTY_NAME="Mageia 10"`, no `VERSION_CODENAME`. `/etc/os-release` links to `../usr/lib/os-release` (mageia-release-common-10-4). Also `/etc/mageia-release`, `/etc/version` and `/etc/product.id`, plus the Mandriva and Red Hat compatibility names | — |
| Media | Classic DrakX DVD `Mageia-10-x86_64.iso`: 5,697,957,888 bytes (5.7 GB, 5.3 GiB), sha512 `e02d5930…a26fd270`. Live Plasma, GNOME and Xfce: 4.6-5.2 GB (decimal). Netinstall: 114 MB, in a tree rebuilt with updates (VERSION "Mageia 10 Official-x86_64-Download 20261126"), so pin it by hash. Each ISO under `iso/10/` has `.md5`, `.sha3` and `.sha512`, each with a **`.gpg` that is an inline-signed message, not a detached signature**. The netinstall ISOs and `distrib/10/x86_64/install/images/*.img` have the sums but **no `.gpg`**: only HTTPS and a pinned hash protect them. Check a signed sum with `gpgv --keyring ./rel.gpg --output - X.sha512.gpg` (prints the sum, exit 0; tested), check the literal file name in it, then `sha512sum -c`. The download page's own `gpg --verify X.sha512.gpg X.sha512` is the detached form and wrong. The ISO key is "Mageia Release" B21076A0CBE4D93D66A9D08D835E41F4EDCA7A90, printed on the download page; it gave a good signature, made 2026-06-28, on the classic sha512. The package key is "Mageia Packages" 00EDB89585B012A8916F0DF8B742FA8B80420F66 | Solus (signed sums), Rocky (gpgv plus a pinned fingerprint, make-rocky.sh:89-94) |
| ISO boot | `isolinux/x86_64/vmlinuz` and `isolinux/x86_64/all.rdz`; UEFI boots from `isolinux/efiboot.img` | — |
| Installer | DrakX (Perl). Unattended by `kickstart=FILE` (the same option as `auto_install=`, install/install2.pm:713): a Perl hash `auto_inst.cfg.pl`. `kickstart=floppy` reads `auto_inst.cfg` or `.pl` from `removable_media__early_in_install()`: USB keys (`isKeyUsb`) or `media_type eq 'fd'` devices, trying ext4, vfat, then ntfs-3g (install/any.pm:1083-1102, 1373-1381). A virtio disk isn't one. `automatic=method:cdrom` answers stage 1, and the stock isolinux.cfg already passes it; stage 1 also takes `method:disk,…` and `method:http,network:dhcp,…` (techinfos.txt:9-21, 31, 74-80). Every install leaves a template in `/root/drakx/auto_inst.cfg.pl` (install/any.pm:930, 943-994). Its keys are at install/any.pm:957, plus `default_packages` (:947) and `enabled_media` (:967, used at :536-539). **The template has no LUKS key**: partitions keep only `fs_type hd level mntpoint options parts size VG_name` (install/any.pm:948-953), and the key goes to cryptsetup through a private `/tmp/.dmcrypt_key-PID` (fs/dmcrypt.pm:106-109). It does keep root's and the users' **password hashes**; only `oldu oldg password password2` are dropped (install/any.pm:970-974). A serial-only install (`console=ttyS0,115200n8 text`) is a *Check*. DrakX has a remote X display (`display=HOST:N`, install2.pm's parse_args) but no VNC or SSH mode (*Check*) | Rocky (kickstart), Leap (Agama JSON) |
| Layout | ext4 by default (fs/type.pm:305); btrfs, xfs, f2fs, jfs and nilfs2 are offered. btrfs gets no subvolumes, as far as the code shows (*Check*). The "simple" layout is `/` plus swap of at most 4 GB, and a separate `/home` only on disks of 51,200 MB or more (fsedit.pm:24-28). **The ESP is at `/boot/EFI`**, 100-300 MB, made with `pt_type => 0xef`, the EFI System type (fsedit.pm:58-63, 61). The table is GPT on UEFI or disks over 2 TiB, else MBR (partition_table.pm:256-261); BIOS on GPT gets a BIOS_GRUB partition (fsedit.pm:575-590). LVM is offered but not the default | Fedora (GRUB 2, an ESP in fstab) |
| Encryption | `cryptsetup --cipher=aes-xts-benbi --key-size=512 luksFormat` with no `--type`, so probably LUKS2 with argon2id on cryptsetup 2.8.3 (fs/dmcrypt.pm:108; the compiled-in default type is a *Check* with luksDump). An encrypted `/` without a separate `/boot` gets a `cdie` (fsedit.pm:472-475), a warning the user may be allowed to override (*Check*); an override gives a LUKS2 `/` behind `GRUB_ENABLE_CRYPTODISK=y` that GRUB 2.12 can't open if it is argon2id. Only an encrypted `/boot` is a hard `die` (:477-479). The release notes' encrypted-upgrade section describes LVM-on-LUKS with "/boot outside of the encrypted LVM" (*Check* that diskdrake offers it). drakx writes `/etc/crypttab` (fs/dmcrypt.pm:97-100) | Mint, Pop (plain /boot, LUKS2 root) |
| Boot loader | grub2 and grub2-efi 2.12-15. The config is `/boot/grub2/grub.cfg`, made by `grub2-mkconfig` (bootloader.pm:2141-2146); `update-grub` is a wrapper for it. No BLS. `/etc/default/grub` has `ENABLE_CRYPTODISK=y`, and drakx adds `GRUB_DEFAULT=saved` and `GRUB_SAVEDEFAULT=true` (bootloader.pm:2082-2110). The ESP holds `EFI/mageia/grubx64.efi` (bootloader.pm:304); `EFI/BOOT/BOOTX64.EFI` exists only with `--removable` (:304-305), and there is no shim fallback (fbx64). drakx writes `/boot/grub2/install.sh` (dxb `bootloader.pm:2161-2177`): on BIOS `grub2-install DEVICE`; on UEFI plain `grub2-install` (or `--removable`, or `--bootloader-id=tmp --no-nvram` with no ESP). The UEFI default has no `--no-nvram`, so it makes the NVRAM entry; the ID `mageia` comes from `GRUB_DISTRIBUTOR=Mageia`. **grub2-common's scriptlet re-runs install.sh on a GRUB update** (`[ "$LOADER" = "GRUB2" ] && /boot/grub2/install.sh`, when `/boot/grub2/updtrans` exists; grub2-common-2.12-15's rpm header). Tool names are `grub2-*` plus `update-grub`/`update-grub2`: **no `grub-install`** (grub2-common `usr/sbin`, `usr/bin`). 10_linux searches `initrd.img-V`, then `initrd-V.img`, before `initramfs-V.img` (grub2-common `etc/grub.d/10_linux:262-265`). Fedora's patches are in: `gen_grub_cfgstub` (an ESP stub grub.cfg pointing at /boot/grub2 by UUID), `grub2-set-bootflag`, `grub2-get-kernel-settings`, `08_fallback_counting`, `10_reset_boot_success`, `12_menu_auto_hide`, `14_menu_show_once`, `grub2-systemd-integration.service`. Whether DrakX writes an `EFI/mageia/grub.cfg` stub is a *Check* (M2). rEFInd 0.14.2 is an installer option | Fedora (`/boot/grub2`), minus shim |
| Secure Boot | **None.** There is no shim or mokutil in core, and the kernel has no module signing and no lockdown. OVMF and the A100 must run with Secure Boot off | PikaOS (`--no-secure-boot`) |
| Kernel | kernel-desktop (the default; drakx always picks it on x86_64, install/pkgs.pm:153-181), kernel-server and kernel-linus, each with a `-latest` metapackage. 6.18.35 at release, 6.18.60 in updates. `/boot/vmlinuz-6.18.35-desktop-1.mga10`, `initrd-6.18.35-desktop-1.mga10.img`, `config-…`, `System.map-…` and `symvers-….xz`, plus the symlinks `vmlinuz`, `initrd.img`, `vmlinuz-desktop` and `initrd-desktop.img`; the kernel %post makes them only `if [ -e initrd-K.img ]`. Config: no module signing, no lockdown; `CONFIG_EFIVAR_FS=m`, `VIRTIO_BLK=m`, `VFAT_FS=m`, as on Leap, harmless with a generic image | Leap (`initrd-K`) |
| Initramfs | dracut 109; `mkinitrd` is a link to dracut. The kernel's %post runs `/sbin/installkernel` (bootloader-utils), which runs `bootloader-config`, which runs `mkinitrd -f /boot/initrd-K.img K` (bootloader.pm:184-212) and adds the entry. **dracut itself defaults to `/boot/initrd-K.img`**: Mageia's patch sets `initrdname="initrd-${kernel}.img"` (dracut `usr/bin/dracut:1241-1244`) and uses `/boot/$initrdname` when `/boot/vmlinuz-K` exists and there is no BLS `loader/entries` and no `/lib/modules/K/initrd` (:1265-1268). `lsinitrd -k K` tries `initramfs-K.img` first, then `initrd-K.img` (`usr/bin/lsinitrd:140-143`), so a stray `initramfs-K.img` would win. **Generic, not host-only**: `hostonly=${hostonly-}` (dracut:1375), `/etc/dracut.conf` and `/usr/lib/dracut/dracut.conf.d` empty, bootloader-utils' `INITRDOPTS=""`. With swap, drakx writes **`/etc/dracut.conf.d/51-mageia-resume.conf`** (`add_device+=" <swap> "`) and adds `resume=<swap>` to the command line, the swap usually by `UUID=` (dxb `any.pm:169-178`; wild_device.pm:85-93) | Gentoo (dracut), Leap (`initrd-K`) |
| Packages | rpm 4.20.1, sqlite, in **`/var/lib/rpm`** (not `/usr/lib/sysimage`). **urpmi** 8.13x is the primary tool (`/etc/urpmi/urpmi.cfg`, `/var/lib/urpmi`). **dnf5** 5.4 is installed beside it on fresh installs, with the core release and updates repos enabled (mageia-repos-10-2). Whether a `dnf` command (not only `dnf5`) exists is a *Check*. A DVD install leaves urpmi with only the DVD medium. Mageia's own `remove-old-kernels` 1.0.6 is in core and installed by default (rpmsrate:674, CAT_SYSTEM level 5). Core has `lib64nss-mdns2` 0.15.1 (+ `nss-mdns-data`), `avahi` 0.8, `openssh-server` 10.0p1, `python3` 3.13.13, `efibootmgr` 18, `shorewall` 5.2.8, `shorewall-ipv6` and `firewalld` 2.4.0 (core.txt) | Rocky (rpm) |
| Init and security | systemd 258.7, usr-merged. SELinux is built in, but `CONFIG_LSM="yama,landlock,bpf"` leaves it off; there is no AppArmor. Presets enable sshd, chronyd, NetworkManager, dhcpcd, avahi-daemon, firewalld and shorewall (whichever is installed), crond, `plocate-updatedb.timer`, `dnf-makecache.timer` and rpmdb-rebuild. The "SSH Server" group is selected by default (compssUsers.pl:211-215). Classic installs use **shorewall** (with mandi-ifw); firewalld with manafirewall is the newer option | — |
| msec | Security level "standard" (security/level.pm:8-25). `msec.service` runs at boot; `/etc/cron.hourly/msec` re-applies settings every hour; the daily and weekly checks write `/var/log/security*` and `/var/log/msec.log`. `CHECK_PERMS_ENFORCE=no` at "standard", so it reports file modes but doesn't change them; the only `force` entries in perm.standard are under /var/log. drakx's `security` is numeric: 0 none, 1 standard, 2 secure (security/level.pm:8-14, 39-45). `ALLOW_REMOTE_ROOT_LOGIN=without-password`, and **msec writes `/etc/ssh/sshd_config`**: its regex knows only `PermitRootLogin (no\|yes\|without-password\|forced-commands-only)` (msec `plugins/network.py:76`); with none of those uncommented in the main file (adopt's `prohibit-password`, or a drop-in), it appends `PermitRootLogin without-password` at the end (network.py:120-141; libmsec.py:535 `at_end_if_not_found=1`), at boot and hourly, once. sshd's first match keeps an earlier line, so login works, but the file changes | — |
| Per-boot churn | `mandriva-everytime.service` (its script `/usr/libexec/mageia_everytime`, initscripts) rewrites `/etc/issue` when `SECURE_LEVEL<4`, and `/etc/issue.net` only when `SECURE_LEVEL<3`; the default is 3 when `/etc/sysconfig/msec` doesn't set it, so issue.net is probably not rewritten (*Check*). It also creates `/etc/resolv.conf` if missing, runs Globetrotter's `hwprofile` or `set-netprofile` if present, and runs harddrake's hardware check. **On new hardware harddrake can reconfigure X/KMS, the kernel command line and modules**, and shows dialogs with a 25 s timeout. It writes `/etc/sysconfig/harddrake2/{previous_hw,kernel,xorg}`. `mandriva-save-dmesg` rotates `/var/log/dmesg` | — |

## What already works, and what doesn't

**bko-holt `dump`:**

- **Distro gate: a gap.** `supported()` matches the ID plus the VERSION_ID
  major (holt/src/osrel.rs:333-339). A Mageia dump today stops with "this is
  Mageia 10 (ID=mageia, VERSION_ID=10), and bko-holt dump supports only …"
  (holt/src/osrel.rs:405-414); load refuses too
  (holt/src/load/layout.rs:242-256). The fix is a
  `("mageia", "10", "Mageia 10")` row, by major, as Solus and Leap have
  (holt/src/osrel.rs:192-197, 222-226).
  - Tests: 10 is accepted; 9 and 11 are refused; an `ID=openmandriva`
    and an `ID_LIKE=mageia` lookalike are refused.
  - The README list must be updated too (holt/README.md:79-88). Otherwise
    tests/readme.rs fails.
  - No code acts on `ID_LIKE`: its only use is redaction
    (holt/src/redact.rs:619), and a grep of testvm's scripts finds no branch
    on it either.
- **GRUB dir: works.** `grub_json` takes `/boot/grub2` when `/boot/grub` is
  absent (holt/src/boot.rs:233-238).
- **Loader: works.** On UEFI, `\EFI\mageia\grubx64.efi` gives `grub` by its
  name (holt/src/boot.rs:1249), and so does an ESP holding `/grubx64.efi`
  (holt/src/boot.rs:1316-1318). With no Mok variables and no "SHIM" in the path,
  `via_shim` is false. On BIOS, an MBR classed `grub2` gives `grub`
  (holt/src/boot.rs:1354-1357). *Check* that Mageia's boot.img is classed
  `grub2`.
- **ESP at `/boot/EFI`: works.** drakx makes it with `pt_type => 0xef`
  (fsedit.pm:61); that this maps to the GPT ESP GUID on a GPT disk is nearly
  certain, still a *Check* from an install. `mounted_esps` takes any vfat
  mount of an ESP-type partition (holt/src/report.rs:1097-1110).
- **`loader.package`: cosmetic.** It comes out null, because Mageia's
  `grub2-efi` isn't in `grub_efi_packages` (holt/src/arch.rs:89-97) and
  `grub2` isn't in `grub_bios_packages` (holt/src/arch.rs:98).
  - Fedora, Rocky and Leap already have it null in the corpus. Its only
    reader in load is `grub_install_is_debians`
    (holt/src/load/commands.rs:7620-7631).
  - Adding `grub2-efi`, a name only Mageia uses, is safe. Adding `grub2` to
    the BIOS list would change openSUSE BIOS dumps, so it isn't proposed.
- **boot.packages fingerprint: a small gap.** `grub2`, `grub2-common`,
  `dracut`, lvm2, cryptsetup and efibootmgr are already listed
  (holt/src/boot/packages.rs:403-503). Add only names no other guest has:
  `grub2-efi`, `bootloader-utils`, `kernel-desktop-latest` and
  `kernel-server-latest`. `has_package` is an exact match
  (holt/src/boot/packages.rs:33-41), so versioned kernel package names can
  only be fingerprinted through the `-latest` metapackages.
- **Packages: works.** `installed_packages` reaches
  `/var/lib/rpm` and runs `rpm -qa`
  (holt/src/boot/packages.rs:106-124, 360-376).
- **Generator: works.** dracut is detected from the installed package
  (holt/src/boot.rs:1040-1080). The prebuilt test looks for
  `/usr/lib/kernel/*.initrd` (holt/src/boot.rs:1053-1060), which Mageia
  doesn't have (*Check*).
- **Kernels: a gap.** `kernels_json` takes versions from the prefixes
  vmlinuz-, kernel-, initrd.img-, config-, System.map-, initramfs- and
  booster.img- (holt/src/boot.rs:793-801). It looks for the initrd only as
  `initrd.img-V`, `initramfs-V.img`, `initramfs-V` or `booster.img-V`
  (holt/src/boot.rs:817-821). On Mageia:
  - every kernel gets `initrd_bytes: null`;
  - `vmlinuz-desktop` becomes a kernel "desktop" with `vmlinuz_bytes` set
    (holt/src/sys.rs:428-433 follows the link) and `modules_installed:
    false`.

  That is harmless to load: `reformat::oldest_kernel` skips what doesn't
  parse (holt/src/load/reformat.rs:261-271), and `DRACUT_EACH_KERNEL` walks
  `/lib/modules/*`, not the dump's list (holt/src/load/commands.rs:944-955).
  It makes the record wrong, and K-mga and the golden read it. It is the
  same class (bogus kernel versions) as RustAudit's H4-04
  (testvm/audit/rust/findings.tsv:62, about `initrd.img-V.dpkg-bak`) and
  Slackware's "huge"; the symlink rule fixes "huge", not H4-04 itself. Fix
  (Decision 6):
  - look up `/boot/initrd-{v}.img`;
  - don't count a `vmlinuz-X` that is a symlink to another listed kernel's
    file as a kernel. Land this with the lookup, or before it: with the
    lookup alone, the pseudo-kernel "desktop" gets `initrd_bytes` through
    the `initrd-desktop.img` link (holt/src/sys.rs:428-433 follows links),
    and any per-kernel loop like Gentoo's (holt/src/load/commands.rs:7397-7409)
    would skip it only because `modules_installed` is false, with a
    misleading "no /lib/modules/desktop" note.
- **Redaction: cosmetic.**
  - `release_label`'s SYSTEMS (holt/src/redact.rs:414-436) lacks
    `mageia`, so a leftover ISO label would be pseudonymised.
  - `GENERIC_HOSTS` (holt/src/redact.rs:185-233, `[&str; 42]`) may need
    DrakX's default hostname (*Check*; possibly `localhost`, already
    listed). It lists some guest hostnames (`solus49`, `void-zbm`) and not
    others (`rocky10`); whether `mageia10` and `mageia10-luks` need adding
    is a *Check* for M6's leak scan.

**bko-holt `load`:**

- **Partitions, LUKS, LVM, fstab and crypttab: works.** None of this is keyed
  on the distro. `crypttab_in_initramfs` is just whether /etc/crypttab exists
  (holt/src/boot.rs:1100).
- **SELinux: works** (no-ops). The relabel steps are gated on
  `/etc/selinux/config` (holt/src/load/commands.rs:5092, 6648), which a
  default Mageia lacks (*Check*).
- **BIOS: works, as on Fedora and Rocky.** The `("grub", false)` arm
  (holt/src/load/commands.rs:6344-6361) runs `grub2-install --target=i386-pc
  DISK`. The `grub2-` names come from `grub_tool`, keyed on `/boot/grub2`
  (holt/src/load/commands.rs:7224-7231). Mageia isn't in `DEBIAN_GRUB_IDS`
  (holt/src/load/commands.rs:19-28), so it gets `grub2-mkconfig -o
  /boot/grub2/grub.cfg` (holt/src/load/commands.rs:6980-6997). The goldens
  for this shape are tests/corpus/fedora44/plan.txt:87,90 and
  rocky10/plan.txt:76,79. The install disk comes from MBR detection.
- **BIOS `install.sh`: a gap** (Decision 11). load restores
  `/boot/grub2/install.sh` (`grub2-install /dev/sdX`, dxb
  `bootloader.pm:2161-2177`) unchanged, and grub2-common's scriptlet re-runs
  it on a GRUB update. After a restore to a differently named disk (sda to
  nvme0n1), a later update installs to the source's device name. It is the
  class of the debconf `grub-pc/install_devices` note
  (holt/src/load/commands.rs:6352-6359). On UEFI, install.sh names no device
  (plain `grub2-install`), so it needs nothing.
- **UEFI GRUB: a gap.** Mageia lands in
  `("grub", true) if self.grub_dir() == "/boot/grub2"`
  (holt/src/load/commands.rs:6387-6395). That arm runs no grub2-install and
  only remakes grub.cfg. Its note says "through shim (Fedora, Rocky,
  openSUSE)", which is wrong for Mageia. Mageia's grubx64.efi is
  grub2-install's own core image, so:
  - (a) its embedded prefix names /boot by UUID (grub2-install's strings
    include `search.fs_uuid %s root`, *Check*) or by partition number, the
    Guix problem that `bko-holt grub-prefix` fixes
    (holt/src/load/grubprefix.rs:1-24). A `--reformat` (new UUID) or a
    layout that renumbers /boot breaks it. If DrakX writes a
    `gen_grub_cfgstub` stub `EFI/mageia/grub.cfg` (*Check*, M2), the stub's
    UUID is what goes stale instead;
  - (b) it embeds only the module for the source /boot's file system, so a
    `--reformat` of /boot to another type leaves GRUB unable to read
    `/boot/grub2`.

  The generic arm (holt/src/load/commands.rs:6421-6437) would fix both, but
  it hard-codes `"grub-install"` (:6432-6433) instead of
  `grub_tool("install")`, and Mageia has no `grub-install` name (grub2-common
  ships only `grub2-*` plus `update-grub`/`update-grub2`), so it would fail.
  The fix is a Mageia arm (Decision 5), keyed on `os.id == mageia` so that no
  Fedora, Rocky or Leap golden moves:
  - run `chroot T grub2-install --target=x86_64-efi
    --efi-directory=<the ESP's mounted_at, /boot/EFI> --bootloader-id=mageia
    --no-nvram`;
  - refresh `EFI/BOOT/BOOTX64.EFI` the same way when
    `fallback_is_grub_copy` says it is GRUB's
    (holt/src/load/commands.rs:7633-7640);
  - then `grub2-mkconfig`;
  - then `efi_entries` makes the NVRAM entry
    (holt/src/load/commands.rs:8034-8180). `bootloader_id` already takes
    "mageia" from BootCurrent or from the ESP's grubx64.efi
    (holt/src/load/commands.rs:7958-8030).
- **Initramfs: works.** Plain dracut takes `DRACUT_EACH_KERNEL`
  (holt/src/load/commands.rs:944-955, chosen at :7301). It runs
  `lsinitrd -k K` and then `dracut --force --kver K`, both by dracut's
  default name, which on Mageia is `/boot/initrd-K.img` (dracut
  `usr/bin/dracut:1241-1244, 1265-1268`; lsinitrd's fallback at
  `usr/bin/lsinitrd:140-143`). So the restored image's `--add-drivers` and
  resume carry over (the Leap efivarfs lesson,
  holt/src/load/commands.rs:910-943), and the remade file is the one
  10_linux and the `initrd.img` symlink name. One caveat: if an
  `initramfs-K.img` ever exists beside it, lsinitrd reads that one. No
  Gentoo-style `DRACUT_ONE_IMAGE` arm (holt/src/load/commands.rs:964-972,
  `gentoo_initramfs` from :7359) is needed.
- **Swap UUID and `51-mageia-resume.conf`: works.** load keeps swap's UUID
  (`mkswap -U`, holt/src/load/commands.rs:3886-3890), so drakx's
  `add_device+=` and `resume=UUID=` stay valid. *Check* that any new-UUID
  path in load (if one exists) rewrites this conf.d file and the command
  line together.
- **`--reformat` image check: works.** `initramfs_images` already takes
  `initrd-*` (holt/src/load/rewrite.rs:928-935). The `initrd-desktop.img`
  symlink is skipped: `is_file` uses `symlink_metadata`
  (holt/src/load/rewrite.rs:450-451).

**The rescue image:** no change ([[one-kernel-rescue]]). The boot stage runs
the target's own grub2-install, dracut and grub2-mkconfig in the chroot, and
the stick's efibootmgr.

## Guests

`vm-run-options` is **`--uefi --no-secure-boot --guest-vars uefi-vars.fd`**,
as void-zbm writes it (testvm/make-void-zfs.sh:431). `--no-secure-boot`
(testvm/vm-run:21-26) because Mageia has no signed shim. `--guest-vars`
because vm-run otherwise gives every run a fresh copy of the variable store
(testvm/vm-run:27-48, 523-525), and with no boot entry OVMF boots only
`EFI/BOOT/BOOTX64.EFI`, which a default DrakX UEFI install doesn't write
(install.sh has no `--removable`, dxb `bootloader.pm:2161-2177`; `:304-305`).
Without it, every boot after the install (SticTest1, GuestTest, vm-update,
bs1-qemu's fallback) finds no loader. ht1.sh already boots the source and
the restored disk on copies of `vms/$G/uefi-vars.fd` under
`--no-secure-boot` (testvm/holttest1d/ht1.sh:354-380), and bs1-qemu.py's
`own_nvram` does the same (testvm/realworld/backshot1/bs1-qemu.py:294-304).
The other way, a postInstall `grub2-install --removable`, would change the
source tree away from DrakX's real layout, so it isn't proposed.

| Guest | Layout | What it covers |
|---|---|---|
| `mageia10` | DrakX's defaults on a 32 GiB disk under UEFI: GPT, a ~300 MB ESP at `/boot/EFI`, ext4 `/`, swap of at most 4 GB (so `51-mageia-resume.conf` and `resume=`), and no `/home` (the disk is under 51,200 MB). kernel-desktop, GRUB 2 with an NVRAM entry in the kept `uefi-vars.fd`, shorewall on with 22/tcp open | the default install; `/boot/EFI`; grub2-install rebuilt in load; dracut writing `initrd-K.img` by default; urpmi; msec |
| `mageia10-luks` (Decision 4) | The same, with `/` on DrakX's LUKS2 (`aes-xts-benbi`), a plain ext4 `/boot`, and crypttab as DrakX writes it. LVM-on-LUKS with /boot outside, the layout the release notes describe, is the alternative (*Check* diskdrake) | dracut's systemd-cryptsetup unlock; plain `/boot` beside a LUKS root; BackShot1 family S's rehearsal guest. The passphrase is typed over serial by `console-boot.py` from `vms/mageia10-luks/console-passphrase`. Its `PROMPT` (testvm/console-boot.py:21) matches systemd's "Please enter passphrase for disk". Plymouth must be off (`rd.plymouth=0` or `plymouth.enable=0`, *Check*); [[front-load-manual-tests]] puts its runs first |

The ISO is the classic DVD (Decision 2), with its `.sha512`, the `.gpg` and
the release key, kept in Chopin's `/bkoscratch/rebuild-inputs/mageia10/`.
`rebuild-on` copies them to the build host (testvm/rebuild-on:7-23), never
whole images ([[update-guests-not-copy]]). The script pins the ISO's sha512
and the key's fingerprint (checked separately: gpgv exits 0 for any good
signature in its keyring), then runs `gpgv --keyring ./rel.gpg --output -
X.sha512.gpg`, since the `.gpg` isn't detached, checks that the literal file
name in the message is the expected `.sha512`, and runs `sha512sum -c`
(the shape of testvm/make-rocky.sh:89-94).

**How it is built** (Decision 2). The recommendation is DrakX's own
auto_install from the classic DVD: `testvm/make-mageia.sh FLAVOR ISO
WORK_DIR`, with `testvm/mageia/auto_inst.cfg.pl.in` and a post-install
script. Its template is make-rocky.sh, with make-fedora.sh's UEFI half:

- check the signed sums with gpgv as above (as testvm/make-rocky.sh:89-94);
- extract `isolinux/x86_64/vmlinuz` and `all.rdz` with xorriso
  (make-rocky.sh:102);
- fill the profile with `@HOSTNAME@`, `@ROOT_PASSWORD_HASH@` and
  `@AUTHORIZED_KEY@` (make-rocky.sh:119), then check its syntax with
  `perl -cw`, as make-opensuse.sh checks its XML (:267);
- put it on a whole-disk ("superfloppy") FAT image made with `mformat -i`
  and `mcopy` (make-qubes.sh:35-38), which drakx reads through its raw_hds
  branch, and attach it as a **USB key**: `-device qemu-xhci,id=xhci
  -device usb-storage,bus=xhci.0,drive=stick,removable=on`, as
  make-opensuse.sh:382-383 attaches its stick (*Check* what `isKeyUsb`
  tests). Not as a virtio disk, as make-qubes.sh does: drakx wouldn't look
  there. A floppy on q35 would need an explicit `isa-fdc` (*Check*). Boot
  `-kernel … -initrd … -append "automatic=method:cdrom kickstart=floppy text
  console=ttyS0,115200n8"` with `-no-reboot` (make-rocky.sh:163-164).
  Fetching the profile over HTTP from 10.0.2.2 is a *Check*: the file is
  relative to the stage2 medium (install/any.pm:1117-1119);
- boot OVMF code with a **kept** variable store, written beside the disk as
  `uefi-vars.fd` (as make-opensuse.sh:380 keeps its store with
  `--uefi-vars uefi-vars.fd`), not make-fedora.sh's throwaway one
  (make-fedora.sh:262-270): grub2-install's NVRAM entry is the only boot
  path. Write `vm-run-options` with `--guest-vars uefi-vars.fd` (shape at
  make-fedora.sh:306-309), check that the disk isn't nearly empty, then
  take the snapshot `fresh-install` (:351).

The profile's keys are taken from a hand install's
`/root/drakx/auto_inst.cfg.pl` (install/any.pm:943-994; the exact names are a
*Check*, since the wiki was unreadable):

- `partitioning => { clearall => 1, auto_allocate => 1 }`;
- **`security => 1`** ("standard"), kept as the user would get it. The key
  is numeric (security/level.pm:8-14, 39-45): the string `'standard'`
  numifies to 0, so `configure_firewall`'s `$o->{security} >= 1` is false
  (install/steps.pm:818) and `security::level::set` gives msec level
  "none";
- **`firewall_ports => '22/tcp'`** (space-separated, e.g. `'22/tcp
  5353/udp'`): without it shorewall opens nothing in auto mode at level 1 or
  higher (install/steps.pm:814-824; drakx-net drakfirewall.pm:217-231);
- **an explicit package selection** (`compssUsersChoice` or
  `default_packages`, install/any.pm:947; names are a *Check*): without it
  the auto install takes whatever the defaults give (*Check*; osinfo-db MR
  !741 lists compssUsersChoice as an open TODO);
- `users` with the `tester` user;
- `postInstall`, which sets root's key and generated password, adds
  `console=ttyS0,115200n8` to `GRUB_CMDLINE_LINUX`, sets text-mode boot
  and reruns `grub2-mkconfig`, replaces the DVD medium with online ones
  (`urpmi.removemedia -a; urpmi.addmedia --distrib --mirrorlist
  <mirrorlist>`, or the same in M2's first vm-update) so that
  `urpmi.update -a` and `S-mga` work, then echoes BACKOTTER-INSTALL-OK to
  ttyS0 (as testvm/make-solus.sh:185 checks);
- `autoExitInstall => 1`.

The other paths:

- **Fallback:** Mageia's Live ISO booted with a `systemd.run=` install.sh,
  as make-solus.sh does (:115, :141-151, :170-185). install.sh lays out
  DrakX's partitions by hand, then runs `urpmi --root` or draklive-install's
  copy, grub2-install and dracut. This is a copy of the installer, not the
  installer.
- **For `mageia10-luks`**, since auto_install can't take a LUKS key
  (install/any.pm:948-953): install.sh's LUKS path. It is the same script that
  BackShot1's `mageia-bootstrap.sh` mirrors, so one script serves both
  (Decision 4).
- **Not recommended:** `make-bootstrap.sh` (an Arch live system; dnf from
  Arch's extra repo is untested) and a hand install kept as a pinned image.

Each guest then gets:

- `testvm/rebuild-one.sh` lines: the recipe as at :188-191 (solus49), the
  ISO-check list at :259, and `shares()` (:43-49) so that `-luks` reads
  `mageia10`'s inputs;
- a `hosts.json` `cache_skip` on choubox if it is over choubox's limits
  (as at :111);
- `ram-sizes.tsv` rows: first est. D 16, MEM 4, as solus49 and pikaos4
  measured, not D 8;
- `vm-store` publish from Chopin only (testvm/rebuild-on:32-37), early, so
  the small hosts can take its cells ([[image-store-bigfoot]]; HOSTS.md:413-416);
- a GuestTest1 grade.

The builds run under `testvm/rebuild-on build mageia10 --reserve 16 4` on a
build host picked by `pick-host` ([[split-chopin-cinqcent]]), in
`/mnt/bko-ram` ([[work-in-ram]]).

**vm-update** (Decision 3): a new `# t2-section: Linux.urpmi` in
testvm/update/in-guest.sh, chosen by `ID=mageia` before the `Linux.dnf` arm
(:1321-1339). Today that arm would catch Mageia first, since dnf5 is
installed. The section runs:

- `urpmi.update -a`, then `urpmi --auto --auto-update`;
- Mageia's own `remove-old-kernels` (1.0.6, in core and installed by
  default, rpmsrate:674) with a keep-2 setting (its flags and config are a
  *Check*), rather than a hand-written removal pass. It must never remove
  the `-latest` metapackages or the running kernel (*Check*);
- `urpmi --clean`.

Also:

- testvm/update/pkgset.sh:185 already lists Mageia as `rpm` (`has dnf`);
  add `|| has urpmi`, so it doesn't depend on dnf being installed;
- fingerprint.py's `LINUX_KINDS` already has `rpm` (:99); add the boot
  packages named above;
- `vm-update --try` first, then `--ship` on a cache host, with Chopin
  committing and publishing ([[update-guests-not-copy]]).

## Tests

**SticTest1** (`testvm/stictest1/mageia10`, and `mageia10-luks` if built):

- `check.sh`, `inner.sh`, `judge.py` and `fixture.sh` come from `rocky10`'s
  (rpm, GRUB 2, ext4), with `fatattr.py` from `leap16` or `solus49` for the
  ESP, and `pikaos4-luks`'s LUKS2 made with the guest's header parameters
  for the `-luks` entry;
- the scratch disk is laid out from the guest's own `sfdisk -d`, with the
  ESP at `tree/boot/EFI` inside the backed-up path;
- real examples: `/boot`'s kernels, `initrd-K.img` and the four symlinks,
  `/boot/grub2`, the ESP's `EFI/mageia`, `/var/lib/rpm`'s sqlite database,
  `/var/lib/urpmi`, `/etc/security/msec`, `/etc/shorewall`,
  `/etc/dracut.conf.d/51-mageia-resume.conf` and `/boot/grub2/install.sh`;
- both archivers. Run on the hosts `pick-host` gives, not "both hosts" as a
  rule ([[split-chopin-cinqcent]]);
- registered in `stic/manifests.json` (as at :1299), `limits.json` (:428),
  `tests/test_stic.py:73`'s count (82 today, +1 or +2: re-read first), and
  `estimates.tsv` (as `stic:solus49` at :58, est. 400 s);
- SticTest1's README: the host table (:75) and the estimates line (:193).

**HoltTest1:** both guests join **1Obscure** (Decision 7). That means
`classes.json`'s `holt.families.Obscure` (:46-48) and `holt.uefi` (:68-70),
the holttest1d README's row (:42) with a Mageia paragraph, and
`batches/holttest1all.batch:13`.

- **judge.py:** the ESP tree is `boot/EFI/EFI/mageia/…`, which none of the
  existing lower-case `^(boot/)?efi/EFI` rules cover. A `MAGEIA_KEPT`
  pattern shaped like `OPENSUSE_KEPT` (testvm/holttest1d/judge.py:366-377),
  not `SOLUS_KEPT` (:358): SOLUS_KEPT holds the whole ESP because nothing on
  it is rewritten, while Mageia's grubx64.efi is. Detection beside opensuse
  and solus (:1056-1059).
  - **Allowed to change (`_WRITTEN`, judge.py:247):**
    `boot/EFI/EFI/mageia/grubx64.efi` and `boot/EFI/EFI/BOOT/BOOTX64.EFI`
    when it is a GRUB copy (a new MAGEIA rule); all of `boot/grub2`, which
    BOOT_STAGE already excuses (`^boot/grub2?(/|$)`, judge.py:249), because
    grub2-install copies every module, `.lst`, font and locale again;
    `boot/initrd-*` (judge.py:251, which already covers
    `initrd-desktop.img`, link included). For `grubenv`, compare bytes, not
    mtime: GRUB's save_env (`GRUB_SAVEDEFAULT=true`) rewrites its block in
    place, the same bytes when the same entry boots (*Check* in M2).
  - **Byte-identical to the source:** the rest of `boot/EFI/` (MAGEIA_KEPT;
    a `gen_grub_cfgstub` stub `EFI/mageia/grub.cfg`, if M2 finds one, is
    excused like openSUSE's stub), the `vmlinuz`, `initrd.img` and
    `vmlinuz-desktop` symlinks, and `etc/dracut.conf.d/51-mageia-resume.conf`
    with the rest of `/etc`. Holding `initrd-desktop.img` byte for byte
    would need an explicit rule that wins over :251; not proposed.
  - Unit tests go in judge-test.py, as at :1846 for solus.
- **The restored disk boots alone** under `--uefi --no-secure-boot`, on a
  fresh copy of `vms/mageia10/uefi-vars.fd` (ht1.sh:354-380), whose Mageia
  entry finds the restored ESP by its partition GUID (*Check* in M5 that
  load keeps it, and that ht1's `GV_ENTRY` sees the entry). load's own
  efibootmgr entry is written in the rescuer's store; the plan shows it,
  and bs1-qemu and BackShot1 prove it. BootCurrent must be
  `\EFI\mageia\grubx64.efi`, and the running initrd must be the remade
  `initrd-K.img`: its mtime is after the load, and `lsinitrd` shows its
  modules.
- **`--reformat` variant (optional):** `reformat-ext4` (classes.json:61) of
  `/` only shows the GRUB rebuild for a new UUID; `reformat-xfs` (:62) would
  show the file-system module too. Either adds a sixth-to-seventh
  `+reformat-TYPE` cell: `tests/test_plan.py:32` (`N_HOLT = 103`, "6
  +reformat-TYPE cells") and :1700 (the reformat count of 6) change.
- Rows in `ram-sizes.tsv` (:113 shape) and in `estimates.tsv`'s holt rows
  (as at :144).

**Test2** (in the batched registration pass):

- **The golden plan:** `holt/tests/corpus/mageia10/` (`src.json`,
  `plan.json`, `plan.txt`) and an `index.tsv` row (as at :77, result
  `dump`), from `bko-holt dump` of the live guest under `vm-run --snapshot`,
  as pikaos4, leap16 and solus49 were made. The procedure is
  holt/tests/corpus/README.md:1018-1055:
  - the guest is a HoltTest2 source first;
  - `collect-dumps.sh --sources`;
  - `corpus_build` twice, with the same sha256s;
  - `--scan` with 0 findings, then `leakscan.py` on the Mac;
  - merge the `index.tsv` row, then `report` (only mageia10
    `"new":true`), then `bless`.

  The plan must show `grub2-install --target=x86_64-efi
  --efi-directory=/boot/EFI --bootloader-id=mageia --no-nvram`,
  `DRACUT_EACH_KERNEL` (whose `dracut --force --kver K` writes
  `/boot/initrd-K.img` on Mageia), `grub2-mkconfig` and an efibootmgr
  entry, and **no `initramfs-` name**; the src.json shows `initrd_bytes` set
  and no "desktop" kernel. A `mageia()` scenario in
  holt/src/scenarios.rs (as `solus()` at :12709) covers UEFI, BIOS on MBR,
  and the vmlinuz-desktop symlink.
- **Registrations:**
  - `holt/spec.json`: `{"kind": "linux", "firmware": "uefi"}`, and the
    `-luks` entry with `testvm/console-boot.py` in its harness (as at :153);
  - `guest/spec.json` (as at :2155): pm `urpmi`; harness `make-mageia.sh`,
    `mageia/**`, `in-guest.sh#Linux.common`, `in-guest.sh#Linux.urpmi`,
    `compare/boot-state.sh` and `new-guest.sh`; checks `S-mga`, `L-grub`,
    `K-mga`, `B-lin`, `M-lin` and fw uefi;
  - `estimates.tsv`;
  - the source and guest counts: `t2lib/plan.py:2935-2936` (88),
    `tests/test_plan.py:32` (`N_HOLT = 103`) and :1698,
    `tests/test_holt.py:709` (93), and the README's "88 sources"
    (testvm/test2/README.md:24, 190, 524);
  - `tests/fixtures/p2/world.py` (:36-76): `GUESTS`, `UEFI`, `PM` with a
    new `urpmi` key, `NEW_GUESTS` (:48, or a `|=` addition as at :69, as
    leap16 and solus49 got) with mageia10 (and `-luks`), and `urpmi` in
    `LINUX_PMS` (:76);
  - the p2 goldens, re-blessed with only Mageia's lines.
- **GuestTest2's new rules**, in two places: the judge in
  `t2lib/guest_judge.py`, and the collector arms in
  `testvm/test2/guest/check-image.sh`, which gathers each rule's data per
  rule (S-* at :16-44, e.g. `has_rule S-eopkg`; K-* at :253-300, e.g.
  K-pika's vmlinuz/booster pairs). `s_dnf` knows only Fedora's and Rocky's
  repo files (guest_judge.py:330-348; check-image.sh:17).
  - **`S-mga`:** the collector emits `/etc/urpmi/urpmi.cfg` and
    `/etc/yum.repos.d/mageia*.repo`. The judge wants online Core Release
    and Core Updates media in urpmi.cfg from a Mageia mirror (not the DVD),
    and the dnf repo files agreeing. It goes in `SOURCE_RULES`
    (guest_judge.py:623-626).
  - **`K-mga`:** the collector lists `/boot/vmlinuz-K` without the flavour
    symlinks (skip `-L`, e.g. `vmlinuz-desktop`) and tests `-e
    /boot/initrd-K.img`, printing `K no-initrd` as K-pika does. The judge:
    `KERNELS_MAX` 2 after vm-update's pruning (guest_judge.py:28-31); the
    running kernel is the newest installed; every `vmlinuz-K` has its
    `initrd-K.img`; the `initrd.img` and `vmlinuz` symlinks point at the
    newest.
  - `known-lines.json`'s `urpmi` lines come from M2's install and `--try`
    logs.

Done when two corpus builds have the same sha256s, the leak scans are 0 on the
hosts and the Mac, no other plan changes, and Test2's own tests pass. The
shared count lines, `classes.json`, the README rows, `world.py` and the
goldens are touched by other sessions: re-read main before each hunk.

**Corpus caution.** A Mageia `ID_LIKE` must never become a branch anywhere
(holt or testvm), since it names `fedora`. The SUPPORTED tests pin that.

## BackShot1 on the A100

The A100 is BackShot1's `thinkpad` (testvm/realworld/backshot1/thinkpad.conf:2-4;
`HOST=thinkpad.local`, backshot1:40, so avahi must publish). **Secure Boot
must be off in its firmware** for Mageia. That is the person's step; it is
put back afterwards for the other BackShot1All rows that need it.

**bs1-adopt** (testvm/realworld/backshot1/bs1-adopt):

- **Init, sleep, services: work.** PID 1 is systemd (:139-153), the
  logind sleep drop-in applies (:65-87), and `start_service ssh sshd`
  finds `sshd.service` (:921).
- **Packages: a gap.** `pkg_install` (:159-193) has no Mageia column. The
  Fedora names it would pass (openssh-server, python3, util-linux and
  avahi: :648, :661, :663, :707) probably exist unchanged on Mageia (core
  has all four). The real risk is whether a `dnf` command exists (*Check*:
  dnf5 may install only `dnf5`) and has online repos; without one, Mageia
  falls to "no package manager I know" (:191). nss-mdns isn't at stake:
  only Slackware's column lists it (:707); Mageia's is `lib64nss-mdns2`
  (core.txt), not needed for adopt. The fix:
  - a 12th MAGEIA column, and `elif [[ $distro == mageia ]]; then urpmi_add
    ${12}` before the apt and dnf arms, as for Solus (:177);
  - `urpmi_add` runs `urpmi --auto "$@" || { urpmi.update -a && urpmi
    --auto "$@"; }`;
  - names at the call sites (:648, :661, :663, :707): `openssh-server`,
    `python3`, `util-linux`, `avahi`;
  - **efibootmgr separately:** it isn't a pkg_install column but a `case
    $distro` with no default (:674-680), so Mageia needs its own `mageia)
    urpmi_add efibootmgr ;;` arm;
  - first, a check that urpmi has an online medium (`urpmq --list-media
    active`). A DVD install made offline has only the DVD, and adopt then
    stops and names `urpmi.addmedia --distrib --mirrorlist`.
- **Firewall: a gap.** `open_firewall` knows firewalld and ufw only
  (:748-761). A shorewall arm is needed: when `shorewall status` succeeds,
  adopt writes its own rules file (`# BackShot1`: ACCEPT net fw tcp 22 and
  udp 5353; the firewall zone is `fw`), INCLUDEs it from
  `/etc/shorewall/rules`, then runs `shorewall check && shorewall restart`,
  and the same for shorewall6.
  - drakx-net writes `zones`, `interfaces`, `policy` (`net all DROP info`,
    `all all REJECT`) and `rules.drakx` (0600, the ports). It writes `rules`
    only when it lacks `INCLUDE rules.drakx`, and then adds only that line;
    other lines are kept, and its interactive prompt defaults to keep
    (drakx-net shorewall.pm:149-226, 156-175). So adopt's INCLUDE survives
    drakfirewall; adopt must not write into `rules.drakx`.
  - `interfaces` names the NIC (`net enp1s0 detect`): see Risks.
  - On a firewalld install the existing arm already covers it.
- **Root login: works, but msec edits sshd_config.** `allow_root_key_login`
  (:688-703) adds `PermitRootLogin prohibit-password`, at the top of the
  main file or in a drop-in. msec's regex doesn't know `prohibit-password`
  (msec `plugins/network.py:76`), so at the next boot or hourly run it
  appends `PermitRootLogin without-password` to the main file (once).
  sshd's first match keeps adopt's value, but sshd_config differs from
  adopt's. Fix: on Mageia, adopt (and mageia-bootstrap) writes
  `PermitRootLogin without-password` in the main file, a value msec
  recognises and leaves alone (*Check* that OpenSSH 10.0 still takes the
  alias). The alternative is to accept the one-time append, but before the
  backup.
- **Hostname: a *Check*.** `hostnamectl` (:499-500) may be undone if
  initscripts or drakx-net still apply `HOSTNAME=` from
  `/etc/sysconfig/network`. If so, adopt sets that file too.

**LUKS: family S** (bs1-luks; [[bs1-luks-unattended]]). `lk_generator`
gives `dracut` (bs1-luks:207-222), and `lk_dracut_systemd` gives S
(:1389-1397) if the initrd has systemd (*Check*: `lsinitrd -m`). That means a
crypttab key field `/backshot1/keys/disk-passphrase:LABEL=mybig512` with
`keyfile-timeout=30s`, and the drop-in `/etc/dracut.conf.d/90-backshot1.conf`
(:1727-1733). Gaps:

- **`lk_current_image`** (:318-332) knows `initramfs-K.img`,
  `initrd.img-K` and `initrd-K`, but **not `/boot/initrd-K.img`**. Add it at
  :327. Without it, the systemd test falls back to a guess (:478-483).
- **`lk_remake`'s dracut loop works** (:250-255): its plain `dracut --force
  --kver K` writes `/boot/initrd-K.img` on Mageia (dracut
  `usr/bin/dracut:1241-1268`). No Gentoo-style arm.
- **GRUB on a plain /boot:** DrakX warns without one (a `cdie`,
  fsedit.pm:472-475), and both BackShot1 layouts have one, so
  `lk_grub_luks` (:1047-1056) keeps the install hands-free.
- **The command line:** if drakboot writes `rd.luks.options=` or
  `rd.luks.key=`, the install becomes family A (:1430-1447). *Check*.
- **Generic (not host-only) dracut:** then crypttab isn't in the image, and
  adopt's `install_items` path adds it (:1638-1670).
- **Typed passphrases** use the vconsole keymap that dracut includes.
  Keep the A100's install on a US layout, or type the passphrase in the
  installed layout.
- **Phase 1's cross-check** of the generator against the dump's
  `boot.initramfs.generator` (bs1-backup's `unlock_check`, :124-134) needs the dump to say
  `dracut`, which it does.

**bs1-manifest.** Most of the churn is covered already:

- `var/lib/*`, `var/cache/*` and `var/log/*` are expected (:87);
- so are `boot/initrd-*` and `boot/grub2/*` (:71), and `boot/EFI/*` (:154,
  the ESP as a whole);
- `boot/vmlinuz-*` and `etc/ld.so.cache` are REMADE.

New entries only once a plain reboot of the restored system shows them, as
BigLinux and Zorin were done:

- `etc/issue` (mandriva-everytime, when `SECURE_LEVEL<4`; the same
  hardware gives the same text, so probably REMADE). `etc/issue.net` is
  probably not rewritten (only when `SECURE_LEVEL<3`, default 3; *Check*
  `/etc/sysconfig/msec`). `etc/resolv.conf` only if it was missing;
- `etc/ssh/sshd_config`, if msec's append (see adopt) happened after the
  backup;
- `etc/sysconfig/harddrake2/*`;
- msec's `/etc/security/msec/*`, if its runs rewrite them.

`package_list` (bs1-common:478-499 reaches `rpm -qa`), `failed_units` and
`hold_snapshotters` need no change. Mageia has no snapshotter; msec runs from
cron and mgaapplet writes only under `var/`. The bs1-all.md row's "Held
besides sleep" column reads "none (msec cron, dnf-makecache: var only)".

**The install, remotely from the rescue stick** (Decision 8). The
recommendation is `testvm/realworld/mageia-bootstrap.sh [--luks] [--detach]
SERIAL INPUTS_DIR KEYS_DIR HOSTNAME`, modelled on pika-bootstrap.sh:

- **Where it runs.** It loop-mounts the Mageia Live Xfce ISO and chroots
  into its squashfs (path *Check*, e.g. `loopbacks/distrib-lzma.sqfs`),
  overlaid on a tmpfs. Everything is mounted under a private
  `/run/bko-mageia` made `--make-private` (pika-bootstrap.sh:112-138), never
  on `/mnt` itself (the d9c9f0e lesson; slackware-bootstrap.sh:144-152). The
  disk is taken by serial only and refused if it is USB or mounted
  (pika-bootstrap.sh:92-110).
- **The layout** is DrakX's own: GPT, a 300 MB ESP at `/boot/EFI` and ext4
  `/` plus swap. With `--luks`, a plain ext4 `/boot` and a LUKS2 `/` made
  with `--key-file` from a copy in the chroot's `/run`. The passphrase gets
  pika's checks (no final newline, printable ASCII; :79-86, :140-149) and an
  EXIT trap, and is never on a command line or in a log.
- **The packages.** From inside the live chroot, Mageia's own urpmi:
  `urpmi.addmedia --urpmi-root T --distrib --mirrorlist`, then `urpmi --root
  T --auto basesystem kernel-desktop-latest grub2-efi dracut cryptsetup
  openssh-server avahi shorewall msec …`. GPG checks stay on, and the cache
  lives on the drive (`<drive>/backshot1/mageia/`, as void and slackware
  keep theirs).
- **The boot side,** in a chroot of the target:
  `dracut -f /boot/initrd-K.img K`, `grub2-install --target=x86_64-efi
  --efi-directory=/boot/EFI --bootloader-id=mageia` and `grub2-mkconfig -o
  /boot/grub2/grub.cfg`, and stale NVRAM entries deleted, as slackware's
  script does. It also writes what DrakX would: `/boot/grub2/install.sh`
  (plain `grub2-install`), `51-mageia-resume.conf` and `resume=` for the
  swap, so the source looks like a DrakX install.
- **Set at install time,** so that adopt over SSH works at once: the fixed
  host key, root's `authorized_keys` with root's password locked, sshd and
  avahi enabled, the shorewall rule (22/tcp **and 5353/udp**, so avahi is
  reachable) and `PermitRootLogin without-password` in the main
  sshd_config (msec's value). It prints
  `BKO-MAGEIA-INSTALL-OK`. `--detach` runs it under `setsid nohup` with
  state in `/run/bko-mageia/` (void-zbm-bootstrap.sh:128-161), since SSH
  drops have happened before ([[pikaos-luks-backshot1]]).
- **Inputs** are pinned by hash and their signatures checked on a farm host
  (README.md:618-640).

The fallback is a DrakX GUI install by the person and adopt at the console,
about an hour of their time, as Solus, Leap and Mint were done.

**Real DrakX, console-free (an alternative).** stage1 takes
`automatic=method:disk,disk:…,partition:…,directory:…` or
`method:http,network:dhcp,server:…,directory:…` (techinfos.txt:9-21, 31,
74-80); the updated `distrib/10/x86_64` tree on a mirror has
`install/stage2/mdkinst.sqfs`; and `kickstart=floppy` reads any USB key, so
mybig512 could hold the profile. Kexec-ing `isolinux/x86_64/vmlinuz` and
`all.rdz` from the rescue stick would then install with DrakX itself over
SSH. But the stick has no kexec-tools (no hit under stick/), so that is a
stick change, out of scope here (Decision 8). The profile wouldn't keep a
LUKS passphrase (install/any.pm:948-953; fs/dmcrypt.pm:106-109), but
`/root/drakx/auto_inst.cfg.pl` keeps root's and the users' password hashes
(install/any.pm:970-974): the install would remove it, or adopt would.

**QEMU rehearsals, before the A100:**

- `bs1-qemu.py --virtio` on `mageia10`, and `--luks-typed --luks-off` on
  `mageia10-luks` (testvm/realworld/backshot1/bs1-qemu.py:44-57, 309-324).
  It has no guest list, so no change is needed. `--virtio` only if the
  generic initrd lacks NVMe, which it shouldn't.
- `mageia-bootstrap-qemu.sh [--luks]`, modelled on pika-bootstrap-qemu.sh.
  It boots the rescue image with a blank NVMe (serial BKOMGATEST), a USB
  drive holding the inputs and keys, and fresh OVMF vars without Secure
  Boot. Then it checks:
  - no mount left in any namespace and no loop device;
  - the disk boots alone from NVRAM to SSH, with `ID=mageia`, no failed
    unit and BootCurrent `\EFI\mageia\grubx64.efi`;
  - with `--luks`: the typed, drive and no-drive boots, and the passphrase
    in no process's cmdline or environ and in no file under `/etc`,
    `/root`, `/var/log` or `/boot`.

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| M1 | Fetch the classic DVD (5.7 GB, 5.3 GiB), its `.sha512`, `.sha512.gpg` and the release key into Chopin's `/bkoscratch/rebuild-inputs/mageia10/` (Decision 9), and check them with `gpgv --output -`, the file name in the message, the pinned fingerprint and `sha512sum -c`. Read the ISO offline: stage2's `install/any.pm` and `steps.pm` for the auto_inst key names (package selection, `firewall_ports`, `enabled_media`); isolinux.cfg; what `isKeyUsb` tests; whether `/boot/grub2/updtrans` is made by grub2's %pre on every update (so whether install.sh really re-runs, Decision 11); `remove-old-kernels`' flags; the Live ISO's squashfs path. (Settled by the RPMs already: dracut's default name, 10_linux's order, no `grub-install`.) Write what M1 found here | 0.25 d |
| M2 | `make-mageia.sh` (kept `uefi-vars.fd`, the profile on a USB key), `mageia/auto_inst.cfg.pl.in` and the post-install script; build `mageia10` by `rebuild-on build` (`--reserve 16 4`, WEAR_TAG `build:mageia10`); `rebuild-one.sh` lines; `Linux.urpmi` and the pkgset.sh line; `vm-update --try`; publish from Chopin. Settle every guest *Check* (os-release, `/boot`'s names, `lsinitrd -m`, shorewall, msec's sshd_config append, the ESP's type and tree, **whether DrakX writes `EFI/BOOT/BOOTX64.EFI` anyway**, **an `EFI/mageia/grub.cfg` stub** and the prefix (UUID or partition), `grubenv` by bytes, the urpmi media, `/root/drakx`, the hostname) and write "What M2 found". Done when the guest boots alone under `--uefi --no-secure-boot --guest-vars uefi-vars.fd` and `--try` is ok | 1.0 d |
| M3 | bko-holt, by a High-effort subagent: <br>• the `SUPPORTED` row, its tests and the README;<br>• `kernels_json`'s flavour-symlink rule, then (or with it) the `initrd-{v}.img` lookup;<br>• the Mageia UEFI GRUB arm (`grub2-install --no-nvram`, the fallback refresh, the note), keyed on `os.id`;<br>• the BIOS `install.sh` handling of Decision 11;<br>• `grub2-efi` in `grub_efi_packages`;<br>• the `PACKAGES` names;<br>• `mageia` in `release_label`'s SYSTEMS;<br>• a `mageia()` scenario for UEFI, BIOS on MBR (with install.sh), and LUKS with a plain /boot.<br>Done when `cargo test` passes and no other golden moves | 0.75 d |
| M4 | SticTest1 `mageia10`: both archivers, on the hosts `pick-host` gives, 0 FAIL; registrations and counts | 0.35 d |
| M5 | HoltTest1 1Obscure for `mageia10`: `MAGEIA_KEPT` (OPENSUSE_KEPT-shaped) and the grubx64/BOOTX64 `_WRITTEN` rule with judge tests, the restored disk alone on the guest's NVRAM copy (BootCurrent, the remade initrd), optionally a `reformat-ext4` or `reformat-xfs` run (then `test_plan.py:32` and :1700), RAM peaks into `ram-sizes.tsv`, the GuestTest1 grade | 0.5 d |
| M6 | Test2: the golden plan from a live dump; `classes.json`, the specs, estimates, counts, `world.py` (`GUESTS`, `UEFI`, `PM`, `NEW_GUESTS`, `LINUX_PMS`); `S-mga` and `K-mga` in guest_judge.py **and their collector arms in check-image.sh**; the leak scan's GENERIC_HOSTS *Check*; `known-lines.json`; the p2 goldens re-blessed with Mageia's lines only | 0.55 d |
| M7 | `mageia10-luks` (Decision 4): its build path, SticTest1 entry, HoltTest1 with `console-boot.py` (runs first), golden plan and registrations | 0.75 d |
| M8 | BackShot1 plumbing: `bs1-adopt` (MAGEIA column, `urpmi_add`, the `mageia)` efibootmgr arm, the media check, the shorewall arm, `PermitRootLogin without-password`, hostname if M2 shows it needed); `bs1-luks` (`initrd-K.img` in `lk_current_image`); `mageia-bootstrap.sh` and `-qemu.sh`; rehearsals with `bs1-qemu.py` on both guests and the bootstrap in QEMU, plain and `--luks` | 0.95 d |
| M9 | BackShot1 on the A100: Secure Boot off (the person), the remote install with `--luks`, adopt (family S), the run, `bs1-manifest` entries from the restored reboot, Secure Boot back on | 0.5 d + ~1 h of the user's time |
| M10 | Docs: holt/README.md's list, `osrel.rs`'s comment, HoltTest1's 1Obscure row and HoltTest1All, SticTest1's and Test2's READMEs, `layload.md`'s boot stage (the Mageia arms), BackShot1's README section "Mageia: DrakX, msec and shorewall" and the bs1-all.md row; memory (a mageia-support note) | 0.1 d |
| | *≈ 5.2 d without M9, ≈ 5.7 d with it; ≈ 4.45 d without M7* | |

Each piece is committed on main when its tests pass and the user says to
commit ([[work-on-main]]). Nothing is pushed until the user says so.

**Costs** (est.):

- **Downloads:** 5.7 GB (5.3 GiB) for the DVD, plus about 4.6 GB for the
  Live Xfce ISO (only for M8's bootstrap).
- **Builds:** 30-45 min each (DrakX installs from the DVD, about 1,500
  packages for a desktop).
- **Disk:** the qcow2 is ~8-10 GiB per cache host.
- **Queue time:** M4 and M5 about 1 h per host.
- **The user's time:** only the OKs, until M9.

## Risks

- **DrakX headless.** Serial-only stage 1 and stage 2 under `text` are a
  *Check*. If DrakX won't run without a screen, M2 falls to the Live ISO
  `install.sh` path (est. +0.5 d), which reproduces DrakX's layout rather
  than running DrakX.
- **The shorewall trap.** An auto_install at `security => 1` without
  `firewall_ports` gives a guest that boots fine and refuses SSH. The trap
  fires only at level 1 or higher (install/steps.pm:818); a string
  `'standard'` would dodge it by turning msec off, which is worse. M2's
  build waits for BACKOTTER-INSTALL-OK on serial and then checks SSH, so the
  failure is named rather than timed out.
- **The NVRAM trap.** The guest has no fallback loader, so any boot on a
  fresh variable store finds nothing. `--guest-vars uefi-vars.fd` covers
  vm-run; a harness path that brings its own fresh store (BackShot2's
  shared store, not in scope) would need the entry made first.
- **dracut's default name (settled).** Mageia's dracut writes
  `/boot/initrd-K.img` by default, so load's and bs1-luks's plain loops
  remake the right file. What remains: lsinitrd prefers an
  `initramfs-K.img` if one ever appears beside it, and a future dracut
  update could drop Mageia's patch. HoltTest1 checks which file booted, so
  either would show.
- **harddrake and shorewall on new hardware.** A restore to other hardware
  may reconfigure X, KMS and the kernel command line at its first boot, and
  show 25 s dialogs. And shorewall's `interfaces` names the source's NIC
  (`net enp1s0 detect`, drakx-net shorewall.pm): a NIC with another name is
  in no zone, so SSH is probably blocked (*Check*). bko-holt doesn't change
  either, since that is Mageia's own behaviour ([[restore-fidelity-first]]);
  load's plan notes both. BackShot1 restores onto the same A100, so it
  should find nothing.
- **GRUB updates after a restore.** On BIOS, grub2-common re-runs
  `/boot/grub2/install.sh`, which names the source's disk (Decision 11).
- **msec.** It reports file modes (`CHECK_PERMS_ENFORCE=no` at "standard")
  and re-applies its settings hourly. It appends `PermitRootLogin
  without-password` to sshd_config when none of its known values is there,
  so adopt writes that value itself; any other file it rewrites gains a
  manifest entry. A system at the "secure" level isn't covered by any
  guest.
- **urpmi and dnf on one rpmdb.** `dnf-makecache.timer` is known to crash
  urpmi during release upgrades (mga#25072). vm-update uses urpmi alone; a
  9-to-10 style release upgrade isn't in scope.
- **Mageia 11.** When it comes (est. 2027-28), the row is by major, so 11 is
  refused until a guest shows it. Mageia 10 stays supported until three
  months after 11.

## Out of scope

- Mageia 9: EOL, no guest. It is refused with the usual message.
- i686, aarch64 and armv7hl.
- BIOS guests: BIOS on MBR is covered by a scenario only, and BIOS on GPT
  with BIOS_GRUB not at all (Decision 10).
- rEFInd as Mageia's loader (an installer option). Load's rEFInd arm from
  PikaOS may already keep it, untested.
- btrfs, xfs and LVM roots (generic code, no Mageia guest).
- msec's "secure" level. Legacy loop-AES `encrypted` mount options, which
  DrakX still offers for non-system mounts (fs/format.pm:197, 253).
- Live-ISO installs as a separate guest: draklive-install gives DrakX's
  layout.
- Running `bootloader-config` or `installkernel` in load.
- BackShot2 cases on Mageia; changes to the rescue image or the stick.

## Decisions for the user

1. **Which release:** Mageia 10 only, with guests `mageia10*` and a
   `SUPPORTED` row for 10 (recommended: 9 is EOL since 2026-09-30, and its
   frozen repositories would fail vm-update and GuestTest2); or also accept
   9 without a guest (untested); or `mageia9` guests (not recommended).
2. **How the guest is built:**
   - DrakX's own auto_install from the classic DVD, with the profile on a
     floppy image and the serial console (recommended: the real installer,
     as Rocky's kickstart and Leap's Agama are);
   - or the Live ISO's `install.sh`, make-solus style (+0.5 d, drift from
     DrakX);
   - or a dnf/urpmi `--installroot` bootstrap from another system (not
     the installer; not recommended).
3. **vm-update's tool:** a new `Linux.urpmi` section, chosen by `ID=mageia`,
   that prunes kernels beyond two with Mageia's own `remove-old-kernels`
   (recommended: urpmi is Mageia's primary tool and what mgaapplet runs);
   or the existing `Linux.dnf` arm, since
   dnf5 is installed (less code, but not what Mageia's users run, and
   `s_dnf` still needs a Mageia branch).
4. **`mageia10-luks`:** yes, built by install.sh's LUKS path shared with
   `mageia-bootstrap.sh`, since the auto_install profile can't carry a LUKS
   key (install/any.pm:948-953), unless M1 finds another key that does
   (recommended: it is BackShot1 family S's rehearsal guest, and LUKS2 with
   a plain /boot is how DrakX encrypts without a warning; est. +0.75 d); or
   LVM-on-LUKS with /boot outside, the release notes' layout (*Check*
   diskdrake); or later; or not at all.
5. **The UEFI boot stage:**
   - a Mageia arm that runs `grub2-install --target=x86_64-efi
     --efi-directory=<mounted_at> --bootloader-id=mageia --no-nvram` (and
     the fallback refresh), keyed on `os.id == mageia` (recommended:
     Mageia's GRUB isn't signed, so nothing is lost; it fixes the
     partition-number prefix and a `--reformat` of /boot, and moves no other
     golden);
   - or keep the restored grubx64.efi as Fedora's arm does, plus
     `bko-holt grub-prefix` (fewer commands; still wrong after a
     file-system change to /boot);
   - or key the arm on "no shim on the ESP" rather than on the ID (more
     general, but it could change Fedora or Leap plans with Secure Boot off).
6. **Dump's kernels (load's initramfs needs nothing new):**
   - a generic rule: a `vmlinuz-X` that is a symlink to another kernel's
     file isn't counted as a kernel, landed with or before an
     `initrd-{v}.img` lookup; load keeps `DRACUT_EACH_KERNEL`, which already
     writes `/boot/initrd-K.img` on Mageia (recommended: the symlink rule
     also fixes Slackware's "huge" in new dumps, the H4-04 class though not
     H4-04 itself; Leap's and Slackware's dumps already have `initrd_bytes:
     null` (corpus leap16/src.json:1251, slackware15/src.json:718) and no
     existing golden reads it);
   - or both keyed on `ID=mageia` only (narrower);
   - or `bootloader-config --action rebuild-initrds` in the chroot
     (Mageia's own tool, but it also edits boot entries, and it would lose
     the `--add-drivers`/resume carry-over that `DRACUT_EACH_KERNEL` has).
7. **HoltTest1's group:** **1Obscure** (recommended: no family it belongs
   to; Leap and Solus went there too); or **1F** beside Fedora and Rocky
   (rpm, but a different boot side; classes.json:41); or a new group.
8. **BackShot1's install on the A100:**
   - `mageia-bootstrap.sh` from the rescue stick over SSH, in a chroot of
     the Live ISO with Mageia's own urpmi, rehearsed in QEMU first
     (recommended: hands-free, repeatable for BackShot1All, and the same
     LUKS handling as pika-bootstrap.sh; est. inside M8);
   - or a DrakX GUI install by the person and adopt at the console (about
     an hour of the user's time per install, the real installer);
   - or real DrakX kexec'd from the stick over SSH (`automatic=method:http`
     or `method:disk`, the profile on mybig512): the real installer and
     console-free, but it needs kexec-tools on the stick, a stick change
     outside this spec, and the profile's password hashes removed after;
   - or a dnf `--installroot` from the stick (needs a stick image change;
     not recommended).
9. **The ISOs:** download the classic DVD (5.7 GB, 5.3 GiB) and, for M8,
   the Live Xfce ISO (~4.6 GB), with their sums and `.gpg`, verified with
   gpgv against the Mageia Release key
   B21076A0CBE4D93D66A9D08D835E41F4EDCA7A90 (as the download page prints
   it) and pinned in the scripts (recommended); or the user downloads and
   checks them. The netinstall isn't used: it has no `.gpg`.
10. **BIOS:** no BIOS guest, BIOS on MBR covered by a scenario only
    (recommended: load's BIOS path is Fedora's, already tested, and DrakX's
    MBR default needs nothing new beyond Decision 11); or a `mageia10-bios`
    guest (est. +0.5 d).
11. **BIOS `install.sh` after a restore:**
    - load rewrites the device in `/boot/grub2/install.sh` to the disk it
      ran `grub2-install --target=i386-pc` on, when the file names a
      `/dev/` path that differs, keyed on `os.id == mageia`, with a plan
      note; judge and bs1-manifest expect the change (recommended: the
      restored machine's next GRUB update then installs where it booted
      from, which is load's job, not a hardening);
    - or leave it and add a note, as the debconf `grub-pc/install_devices`
      note does (holt/src/load/commands.rs:6352-6359) (less code; the next
      GRUB update on renamed hardware writes to the wrong disk or fails);
    - either way only if M1 confirms the scriptlet re-runs it on updates.
