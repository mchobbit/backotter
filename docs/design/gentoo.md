# Gentoo Linux support

2026-11-12. The user asked for Gentoo support once Gentoo was installed on
the Thinkpad, the BackShot1 machine: GPT, a 1 GiB ESP at /efi, 4 GiB swap,
LVM `vg0` with / on ext4 and /home on XFS, OpenRC, glibc, profile
`default/linux/amd64/23.0`, binary packages, `gentoo-kernel-bin` with
`installkernel[dracut,grub]`, and GRUB as `gentoo`. The user approved this spec on
2026-11-12 with every recommended decision (the last section).

**Conventions.** est. marks an estimate. *Check* marks a fact the docs don't
state, to be settled on the first guest before the code that depends on it.
Gentoo's facts come from the Thinkpad itself (read over BackShot1's pinned
key, nothing written: its install.conf, emerge.log, /boot, NVRAM and
`grubx64.efi`), the autobuilds directory of distfiles.gentoo.org, the 23.0
x86-64 binhost's `Packages` index and the release key on the keyservers, all
read 2026-11-12. G1 (the `gentoo` guest) was built the same day, and its
*checks* are settled below: see "What G1 found". Code references are to
local `main` at 8f3398a (after Chimera C7 and the bsdtar rebuilds). The work was built on the branch
claude/backshot2-alpine-fc847f, which merges main, after it took in 8f3398a.

## Summary

- Gentoo Linux becomes a supported system for bko-holt and both archivers
  (rustic-bko and restic), with two QEMU guests, a SticTest1 entry each, a
  HoltTest1 group, and one BackShot1 run on the Thinkpad as it is now.
- Most of it is already built for other systems: dracut (Fedora, Void),
  UEFI GRUB with `grub-mkconfig` (Arch, Void), OpenRC (Alpine), LVM and
  LUKS, an ESP at /efi (AerynOS, EndeavourOS), and Void's way of building a
  guest from a tarball.
- What's new is Portage: bko-holt has no reader for its package database
  (`/var/db/pkg`), so a Gentoo dump has no boot packages and no initramfs
  generator. The generator comes from installkernel's own config, not from
  the distribution. The guests are built from a stage3 and binary packages.
- Only dracut is supported, the Thinkpad's and both guests' generator;
  genkernel, ugrd, UKIs and shim are refused by name.
- ~4.75 d (est.), half of it the guests.

## Why

- **The Thinkpad runs it.** BackShot1 is the real-world test. Gentoo is what
  the machine now has, so bko has to dump, restore and boot it.
- **A system put together by its owner.** Every supported Linux so far has
  a distribution-chosen boot stack. On Gentoo the owner chooses it through
  USE flags and kernel choice. This is the first test where bko-holt has to
  read how the system boots instead of inferring it from the distribution.
- **A package database that isn't a package manager's file.** Portage keeps
  one directory per installed package. That tests whether `dump` reads the
  boot stack from evidence.

## What Gentoo is (as far as bko cares)

| | Gentoo | Nearest supported system |
|---|---|---|
| Release | Rolling; weekly stage3 autobuilds dated like `20261108T170107Z`, about two months kept | Arch, Void |
| os-release | `ID=gentoo`, `NAME='Gentoo'`, `VERSION_ID='2.18'` (single quotes; the version is baselayout's, not a release); no ID_LIKE | AerynOS, Chimera (version `*`) |
| libc, toolchain | glibc and GCC (the Thinkpad); musl profiles exist | Arch |
| Userland | GNU coreutils; 23.0 is merged-usr (/bin, /sbin, /lib link into /usr); `/bin/sh` is bash | Arch |
| Init | OpenRC 0.63 with sysvinit (the Thinkpad), or systemd; `rc-update add X RUNLEVEL` | Alpine |
| Packages | Portage, `/var/db/pkg/CATEGORY/PF/` (files `PF`, `CATEGORY`, `USE`, `CHOST`...); binpkgs from the 23.0 binhost, gpkg signatures checked | none |
| Kernels | `gentoo-kernel-bin` (prebuilt, installed by its ebuild), `gentoo-kernel`, `gentoo-sources` | |
| Initramfs | installkernel's `initrd_generator` (`dracut` on the Thinkpad), or genkernel, ugrd, none; UKIs through `uki_generator` | Fedora, Void (dracut) |
| installkernel | `/usr/lib/kernel/install.conf` (overridden by `/etc/kernel/install.conf`): `layout=grub`, `initrd_generator=dracut`, `uki_generator=none` on the Thinkpad | |
| Kernel files | `/boot/vmlinuz-KV`, `initramfs-KV.img`, `config-KV`, `System.map-KV`, each with a `.old` copy after a reinstall; KV `6.18.63-gentoo-dist-bin` | Fedora |
| GRUB | `sys-boot/grub` (`GRUB_PLATFORMS`); `grub-mkconfig -o /boot/grub/grub.cfg` run by installkernel's grub plugin; 2.16 defaults `GRUB_DISABLE_OS_PROBER=true` | Arch |
| LUKS, LVM | `cryptsetup`; `lvm2[lvm]` for the `lvm` tools and the OpenRC `lvm` boot service | Void |
| ZFS | `sys-fs/zfs` and `zfs-kmod`, built from source (not on the binhost) | `debian13-zfs` |
| Install | unpack a stage3 (`--xattrs-include='*.*' --numeric-owner`), chroot, `emerge-webrsync`, emerge a kernel and loader | Void's ROOTFS tarball |
| Images | `stage3-amd64-{openrc,systemd}-DATE.tar.xz` with a `.asc`, signed by the Release Engineering key `13EBBDBEDE7A12775DFDB1BABB572E0E2D182910` (subkey `534E4209AB49EEE1C19D96162C44695DB9F6043D`, expires 2028-07-01) | Alpine (gpgv) |

## What already works, and what doesn't

**bko-holt `dump`** (runs live on the source):

- The OS check (`holt/src/osrel.rs` `SUPPORTED`) refuses it: there's no
  `gentoo` row. It needs version `*`. The parser already takes single quotes.
- `installed_packages()` (`holt/src/boot.rs`) reads dpkg, pacman, rpm, apk
  and xbps, and returns nothing on Gentoo. A Portage reader is needed. It
  walks `/var/db/pkg/*/*` and splits `PF` with a hand-written version-suffix
  parser (holt has no dependencies, so no regex crate), scanning from the
  end: `-rN`, then `_alpha|_beta|_pre|_rc|_p` suffixes, then the
  `N(.N)*[a-z]?` version. Unit tests cover `font-adobe-100dpi-1.0.4`,
  `_p`/`_rc` suffixes and `-rN`. The tuple's third field is the package's
  `CHOST` file, empty when absent (the vdb has no arch). It runs first when
  `/etc/gentoo-release` exists, because a Gentoo can have dpkg or rpm
  emerged as tools.
- `PACKAGES` lacks Gentoo's names: `gentoo-kernel-bin`, `gentoo-kernel`,
  `gentoo-sources`, `installkernel`, `genkernel`, `ugrd`, `systemd-utils`,
  `zfs-kmod`. `grub`, `dracut`, `lvm2`, `efibootmgr`, `cryptsetup`,
  `xfsprogs`, `e2fsprogs` and `dosfstools` are there already, by bare name.
  `testvm/update/fingerprint.py` mirrors the list, and a test enforces it.
  `grub` isn't in `detect_loader`'s UEFI list, so `loader.package` is null,
  as on Arch: harmless.
- The initramfs generator is read, in this order:
  1. installkernel's `install.conf` (`/etc/kernel`, then `/usr/lib/kernel`):
     `uki_generator` other than `none` is recorded and refused by load;
     `initrd_generator` gives `dracut`, `ugrd` or `none`; `layout` says
     where the images are (`grub` or `compat` is /boot; anything else is
     refused).
  2. Without installkernel: `none` if /boot has no `initramfs-*`, else
     `genkernel` if genkernel is installed, else `dracut`.
     `gentoo-kernel-bin`'s `USE=initramfs` is a second clue.
- `kernels_json()` already reads `vmlinuz-KV` and `initramfs-KV.img`, but it
  takes installkernel's `.old` copies as kernels `KV.old` and `KV.img.old`.
  They come out of the kernel list, and the plan notes them: grub-mkconfig
  lists them, and the boot stage doesn't rebuild them.
- The ESP at /efi is already an ESP, both by mount point and by type GUID
  (`report.rs`). LVM comes from on-disk metadata and needs no tools.

**bko-holt `load`:**

- UEFI GRUB already does the right thing:
  `grub-install --target=x86_64-efi --efi-directory=/efi --bootloader-id=gentoo --no-nvram`
  (the id from BootCurrent `\EFI\gentoo\grubx64.efi`), then `grub-mkconfig
  -o /boot/grub/grub.cfg` and `efi_entries()` for the NVRAM. The
  grub-install matters: the Thinkpad's `grubx64.efi` has
  `(lvmid/VG-UUID/LV-UUID)/boot/grub` built in, and this re-embeds it.
- Initramfs: the generic dracut loop (`dracut --force --kver K`) runs in the
  chroot, as for Fedora. Left to itself, dracut 112 writes
  `/efi/MACHINE-ID/K/initrd` when both `/efi/loader/entries` and
  `/efi/MACHINE-ID` exist (and likewise under /boot), else
  `/lib/modules/K/initrd` if that exists, else `/boot/initramfs-K.img`. The
  Thinkpad has none of the first two (`/lib/modules/K` has `vmlinuz`, no
  `initrd`), but on Gentoo the loop names the file anyway:
  `dracut --force /boot/initramfs-K.img K`. Images are host-only
  (`01-gentoo.conf`), built with /dev, /proc, /sys and /run bound.
- `initramfs()`'s fallback (`_ =>`, `update-initramfs -u -k all`) is a hard
  failure on Gentoo. Gentoo gets its own arms: `dracut` as above; `none`
  runs nothing and notes it (a unit test, no guest: untested on a real
  boot); `genkernel`, `ugrd`, a UKI generator, a non-grub layout and a
  kernel with `/lib/modules/K/initrd` are refused, each by name.
- Secure Boot through shim: the UEFI GRUB arm would write an unsigned GRUB
  over a signed one. On Gentoo with `via_shim`, it refuses.
- `remake_grub_cfg()`'s os-prober bind of `/usr/bin/true` is fine on 23.0's
  merged usr. BIOS GRUB's arm pushes a debconf note, but no Gentoo guest
  uses BIOS GRUB.
- Init system: `load` doesn't touch services. OpenRC's `lvm` service in the
  boot runlevel comes back with `/etc/runlevels`.

**BackShot1** (`testvm/realworld/backshot1`, on main):

| Script | Line | Assumption | Fix |
|---|---|---|---|
| bs1-adopt | 50-74, 169-204 | `pkg_install` takes one argument per distro, eight of them | a ninth, GENTOO, tested before the others by os-release (`emerge --getbinpkg --noreplace`), and a GENTOO argument on each call: `net-misc/openssh` (169), `dev-lang/python` (174), `sys-apps/util-linux` (176), `"net-dns/avahi sys-apps/dbus"` (204) |
| bs1-adopt | 180-182 | efibootmgr only on Chimera | the same test on Gentoo, `emerge --getbinpkg --noreplace sys-boot/efibootmgr` |
| bs1-adopt | 39-44 | init detection | none: `/proc/1/comm` is `init` and `rc-service` exists, so `openrc`; `sshd` and `avahi-daemon` are the Thinkpad's names |
| bs1-common | 183 | `package_list` knows no Portage | a branch first on `/etc/gentoo-release`: `/var/db/pkg/*/*` as `CAT/PF`, sorted |
| bs1-backup, bs1-list | | df's options | none: GNU df |
| bs1-manifest | 50 | expected changes | none: `efi/*`, `boot/initramfs-*` and `boot/grub/*` are expected already |
| bs1-qemu.py | | the guest's layout | none: it boots the guest's own disk under OVMF, as for `chimera-uefi` |

On the Thinkpad and the `gentoo` guest nothing is emerged: Portage needs
Python, util-linux is in @system, and sshd and avahi are installed (the
guest's build adds avahi, with the Thinkpad's
`-gtk -qt5 -qt6 -python -mono -introspection`). An emerge would need the
synced tree and the binhost, and would write `/var/db/pkg`,
`/var/lib/portage/world`, `/var/cache/binpkgs` and `emerge.log`, all before
phase1's backup, so bs1-manifest sees them as source state. The real test
is `bs1-qemu.py --virtio` on the `gentoo` guest before the Thinkpad.

**SticTest1:** the archivers are static binaries. Each guest gets
`check.sh`, `fixture.sh`, `inner.sh` and `judge.py` from `rocky10-lvm`'s
(glibc, GNU, bash, a scratch volume group made like the guest's), with
`fatattr.py` from `chimera-uefi` for the ESP, adapted to ext4 / and XFS
/home. `gentoo` adds the cross-filesystem case the 2026-11-10 rustic-bko
leftovers record as missing: a backup of the ext4 fixture restored onto an
XFS volume, judged without the ext4-only attributes.
`test2/stic/check-manifests.py`'s count goes from 45 to 47.

**The rescue image:** no change ([[one-kernel-rescue]]).

## Guests

Built by `make-bootstrap.sh`, as for Void and Chimera. From the Arch ISO's
live system it unpacks a stage3 into the new root with
`bsdtar -xpf - --numeric-owner`, as `void.sh` and `chimera.sh` now do, so the
file capabilities stay. The build fails if iputils `ping` has no
`cap_net_raw`, after the unpack and again before `finish`. In the chroot it
runs `emerge-webrsync`, then emerges the kernel, the boot loader and the
services. Every input is checked:

- **Stage3:** pinned by date (`GENTOO_STAGE3=20261108T170107Z`) and checked
  with `gpgv` against the Release Engineering key, pinned by fingerprint
  (`GENTOO_KEY`), as Alpine's is. The tarballs and `.asc`s are also kept in
  `/bkoscratch/rebuild-inputs/GUEST` on both hosts, since Gentoo prunes
  autobuilds after about two months.
- **The tree:** `emerge-webrsync` checks the snapshot's signature with the
  key stage3 ships. It isn't pinned, because the binhost follows the live
  tree; `metadata/timestamp.chk` is recorded in WORK_DIR.
- **Binary packages:** `FEATURES="getbinpkg binpkg-request-signature"`,
  checked against `getuto`'s keyring. The build runs `emerge -p` and fails
  on any `[ebuild` line outside an allowlist of source builds. The
  Thinkpad's emerge.log gives it: `lvm2` (USE=lvm, ~2 min), `grub`
  (`GRUB_PLATFORMS="efi-64"`, ~90 s), `dracut`, `installkernel` and
  `gentoo-kernel-bin` (its ebuild installs a prebuilt kernel). *Check* the
  systemd stage3's `lvm2` and `cryptsetup`.
- **Configuration:** the Thinkpad's `package.use`, `make.conf` features and
  dracut defaults, plus a serial console (`GRUB_CMDLINE_LINUX`,
  `agetty.ttyS0`). `/var/cache/binpkgs` and `distfiles` are emptied before
  `finish`; the tree stays, as on the Thinkpad.
- **A new layout** in `bootstrap/common.sh`: `LAYOUT=efi-lvm`, the
  Thinkpad's in small. GPT, a 1 GiB ESP at **/efi**, 2 GiB swap, and a PV
  with `vg0`: `root` 10G ext4 and `home` 3G XFS, ~4 GiB free. That needs
  `DISK_SIZE=20G` in make-bootstrap's `case` (16G leaves a PV of ~12.99
  GiB). `mount_boot` gets an /efi branch, and /home gets mounted.

| Guest | Firmware, loader | Layout | What it covers |
|---|---|---|---|
| `gentoo` | UEFI, GRUB x86_64-efi, id `gentoo` | `efi-lvm` | the Thinkpad's setup: OpenRC, glibc, 23.0, `gentoo-kernel-bin`, `installkernel[dracut,grub]`, `lvm2[lvm]`; BackShot1's rehearsal guest |
| `gentoo-systemd` | UEFI, GRUB x86_64-efi | `luks-esp`: ESP as /boot, / ext4 on LVM on LUKS2, keyfile in the initramfs as on `chimera-uefi` | systemd's dracut with its crypt module, `rd.luks` on the command line, a host-only image rebuilt in a chroot over LUKS; HoltTest1's oneshot candidate |

Each one gets: `rebuild-one.sh` lines, the SticTest1 entry,
`holttest1d/ram-sizes.tsv` rows (est. D 7, MEM 4 until a run measures it),
`classes.json` entries, both hosts, vm-update and a GuestTest1 grade.
vm-update's Portage branch: `in-guest.sh`'s `# t2-section: Linux.portage`
(`emerge --sync`, then `emerge -puDN @world` through the same allowlist,
then `emerge -uDNg @world`, `emerge --depclean`, caches emptied); a source
build outside the allowlist fails the update and names the package, and an
allowlisted one costs ~2-5 min. Also `pkgset.sh`'s `base pm portage`,
`fingerprint.py`'s `LINUX_KINDS`, and the `vm-update` header. GuestTest2
needs a `portage` section in `guest/known-lines.json`, plus `S-portage` and
`K-gentoo` rules in `t2lib/guest_judge.py` (`SOURCE_RULES`, `KERNELS_MAX`).

## What G1 found

Settled on the `gentoo` guest (stage3 `20261108T170107Z`, the tree of
`Thu, 12 Nov 2026 00:45:00 +0000` from emerge-webrsync's snapshot
`gentoo-20261111`, Portage 3.0.83, OpenRC 0.63.3, dracut 112,
`gentoo-kernel-bin` 6.18.63, GRUB 2.14-r5), 2026-11-12, and on the Thinkpad
read again the same day:

- **os-release:** as the table has it, the same on both: `ID='gentoo'`,
  `VERSION_ID='2.18'`, single quotes, no `ID_LIKE`.
- **The vdb:** `/var/db/pkg/CATEGORY/PF/` with `PF`, `CATEGORY`, `CHOST`
  (`x86_64-pc-linux-gnu`), `SLOT`, `USE`, `BUILD_TIME` and the rest; no `PN`
  or `PV` files, so the name/version split is needed. A package merged
  from a binary package also has `BINPKGMD5` (and `BUILD_ID`); one built
  from source has neither. Bare names do repeat outside the boot stack:
  `acct-group/avahi`, `acct-user/avahi` and `net-dns/avahi` are all `avahi`.
- **Kernel files:** `vmlinuz-KV`, `initramfs-KV.img`, `config-KV` and
  `System.map-KV` in /boot, KV `6.18.63-gentoo-dist-bin`. A fresh install has
  no `.old` copies; the Thinkpad's come from `emerge --config
  gentoo-kernel-bin`, which reinstalled the same KV.
- **installkernel:** only `/usr/lib/kernel/install.conf` (`layout=grub`,
  `initrd_generator=dracut`, `uki_generator=none`), no
  `/etc/kernel/install.conf`, on both.
- **`/lib/modules/KV`:** `vmlinuz`, `System.map`, `config`, `build` and
  `source` are links into `/usr/src/linux-KV`; no `initrd`, on both.
- **dracut:** 112 on both, `01-gentoo.conf` host-only (`hostonly_mode=sloppy`);
  the guest's image has the `lvm` module.
- **Services:** `sshd`, `dhcpcd`, `dbus`, `avahi-daemon` in default, `lvm` in
  boot (the Thinkpad's names); the guest adds `agetty.ttyS0`.
- **Source builds:** on both, `lvm2`, `grub`, `dracut`, `installkernel`,
  `gentoo-kernel-bin`, and also `net-dns/avahi` (the Thinkpad's USE flags)
  and `virtual/dist-kernel`; the Thinkpad's firmware and microcode too,
  which the guest leaves out. The first `vm-update --try` stopped on
  `app-doc/info-manual-31.1`, which the binhost lagged the tree on (it had
  30.2), and named it, as the Risks section has it; it joined the allowlist
  (`SOURCE_OK`, 8 names). Its second run built `grub-2.16-r1` from source
  (stable in the tree since the guest's snapshot: the Thinkpad has it).
- **Build time:** the builder took 579 s on Chopin, and the update 283 s
  with an overlay of 548 MiB. The image is 6.42 GiB on disk, more than the
  estimate: the deleted downloads and build trees still hold clusters
  until vm-update's first trim.
- **File capabilities:** none. The stage3 has no extended attributes at
  all, and its iputils (`USE=suid`, as the Thinkpad's binary package) makes
  `ping` setuid root, 4755, with no `cap_net_raw`. Nothing on the Thinkpad
  has a file capability. So the build's cap check, as the Guests section
  has it, can't pass: it is left out of `bootstrap/gentoo.sh`, pending the
  user's ruling.
- **The chroot:** neither arch-chroot (getuto's `gpg --lsign-key` of a
  subkey failed under it) nor the handbook's binds (vg0/root was still in use
  after `umount -R`, so `vgchange -a n` failed), but arch-chroot's file
  systems mounted once and a plain `chroot`.

## What G3 found

Settled on the `gentoo-systemd` guest (stage3
`stage3-amd64-systemd-20261108T170107Z`, the same tree, systemd 261.2,
dracut 112, `installkernel` 70-r1 with USE=systemd), 2026-11-12:

- **The systemd stage3's `lvm2` and `cryptsetup`:** both from the binhost
  (`lvm2` 2.03.39 with USE=lvm, `cryptsetup` 2.8.8). What isn't is
  `sys-apps/systemd` itself: the binhost builds it without USE=cryptsetup,
  so no `systemd-cryptsetup` for the initramfs. The build re-emerges it with
  USE=cryptsetup from source (a few minutes), and `SOURCE_OK` (both copies)
  gains `sys-apps/systemd`.
- **Kernel names under systemd:** with USE=systemd, installkernel hands the
  kernel to systemd's `kernel-install`, and its `90-compat.install` (layout
  `grub`, read from the pinned `/etc/kernel/install.conf`) writes
  `/boot/kernel-KV` and `/boot/initramfs-KV.img`, with no `config-KV` or
  `System.map-KV`. grub-mkconfig finds `kernel-*` (its paths are the ESP's:
  `/kernel-KV`). No `/boot/loader/entries` or `/boot/MACHINE-ID`. `dump`
  now reads `kernel-KV` as a kernel (and `kernel-KV.old` as a copy); GuestTest2's
  K-gentoo lists both names.
- **The initramfs:** host-only, with dracut's `crypt`, `systemd` and `lvm`
  modules, `/etc/crypttab` and `systemd-cryptsetup`; the key file comes in by
  `install_items` (`/etc/dracut.conf.d/10-backotter-luks.conf`).
  `rd.luks.uuid` is on GRUB's command line; crypttab names the volume
  `cryptroot`. The guest boots alone, unlocks with the key file, and comes up
  with no failed units.
- **Build time and size:** the builder took 707 s on Chopin; the image is
  6.7 GiB on disk.
- **vm-update --try:** ok on the third run (372 s, overlay 566 MiB, 8
  packages), after two stops that named their package, as the Risks section
  has it: `net-libs/nghttp2` (curl's new USE=http2 pulled it in; the binhost
  has it only with USE=-systemd) and `net-misc/rsync` (3.5.2-r1, which the
  binhost lagged the tree on). Both joined `SOURCE_OK`. Every --try (both guests)
  also re-emerges iputils with USE=filecaps newly on (`[binary Rg]
  ... USE="filecaps*"`), so the first real update may give `ping` its
  cap: unchecked.
- **GuestTest1 grades** (2026-11-12): both solid on every column but
  SticTest1's, which has no check until G4, so "not covered by SticTest1",
  as debian13-stack and rocky10-lvm once were.
  - `gentoo`: boots alone on both hosts, SSH; the same SHA-256 on both
    (`25f2f32d...`); `vm-update --try` ok on Chopin (515 s, overlay 539 MiB,
    7 packages). Two runs on Cinqcent stopped on the network: a mirror's
    Manifest (`lastreplicate`) and the guest's DNS during a binpkg fetch.
  - `gentoo-systemd`: boots alone on both hosts (systemd running, no failed
    unit, / unlocked by its key file), SSH; the same SHA-256 on both
    (`25552f4e...`); `vm-update --try` ok on Chopin.
  - Neither has an image-state yet (never really updated), and `ping` is
    setuid, with no file capability, as on the Thinkpad (G1's open item).

## What G4 and G5 found

2026-11-12, with rel6's restic-bko and rustic-bko (the shipped `bko-bin`)
and bko-holt 7760c9e7c497 (395e643's holt/, built on Chopin):

- **SticTest1** (`testvm/stictest1/gentoo`, `gentoo-systemd`): rocky10-lvm's
  design, with chimera-uefi's `fatattr.py`; one `inner.sh`, `fixture.sh` and
  `judge.py` for both, which read the layout from the guest (the ESP's mount
  point, /home on XFS, LUKS under /). The scratch disk is laid out like the
  guest's (GPT, ESPs of its size, gentoo's swap partition, the LVM PV,
  gentoo-systemd's LUKS2 made with the guest's header parameters), with an
  ext4 LV like /, gentoo's XFS LV like /home and an ESP like /efi or /boot
  mounted inside the backed-up path. Runs `g4f` on both hosts: `gentoo` 35
  PASS, 3 LIMIT, 0 FAIL (~270 s); `gentoo-systemd` 29, 3, 0 (~205 s). The
  LIMITs (birth times, symlinks' atimes on relatime, whole-disk) are classed
  in `limits.json`.
- **The cross-file-system case** (`gentoo`): each tool's snapshot of the ext4
  tree restored onto a stack whose / is XFS. Every need holds but what ext4
  holds and XFS can't: the inode flags D, j and T (each tool warns "not
  supported" and goes on); a character device 511:1048575, which XFS's
  14/18-bit device numbers can't encode (restic says "ignoring error ...
  mknod: invalid argument" and exits 1; rustic-bko a `[WARN]` and exits 0,
  as without `--strict-exit`); and XFS's `trusted.SGI_ACL_*` twins of the
  ACLs. The judge allows exactly those.
- **Guest facts:** Gentoo's util-linux has no `setpriv` (USE=caps is off),
  so the non-root run takes `setpriv.py`; `gentoo` has no
  `/etc/lvm/backup` or `archive` until an LVM command runs on it (its VG was
  made from the Arch ISO); `gentoo-systemd` has `/etc/runlevels` too, and no
  xfsprogs (its work disk is ext4).
- **HoltTest1 1G:** the first run, `g5-1` (Chopin), failed only fsmeta: the
  boot stage's dracut writes `/boot/initramfs-K.img` 0600, where
  installkernel had left it 0644. judge.py allows that mode on a Gentoo
  kernel's initramfs (a name with `-gentoo`), with judge-test cases. Then
  `gentoo` passed as `g5-3` (Cinqcent) and `g5-4` (Chopin), and
  `gentoo-systemd` as `g5-2` (Cinqcent) and `g5-5` (Chopin): every load
  stage, the boot alone, 0 fsmeta differences (393 and 391 paths from the
  boot stage) and the dumps. ht1.sh now checks GRUB's embedded lvmid:
  on `gentoo`, `EFI/gentoo/grubx64.efi` and `EFI/BOOT/BOOTX64.EFI` both name
  the restored vg0/root by `lvs -o vg_uuid,lv_uuid`, which are the source's
  (vgcfgrestore keeps them). `gentoo-systemd`'s GRUB, on an ESP at /boot,
  embeds none. RAM-disk peaks 5.4 GiB for both: `ram-sizes.tsv` D 7, MEM 4.
- **GuestTest1:** both guests are now covered by SticTest1 (0 FAIL on both
  hosts); the open items are G1's (ping's cap, no image-state yet).

## What G6 found

2026-11-12, `bs1-qemu.py --virtio` on `gentoo` (bko-holt 7760c9e7c497,
the shipped rustic-bko, the pinned Test2 rescue image `rescuer.img`
4fd4186a...):

- **bs1-adopt** finds OpenRC (`/proc/1/comm` is `init`, `rc-service`
  exists) and emerges nothing: sshd, Python, util-linux, avahi, dbus and
  efibootmgr are all there, as on the Thinkpad (read the same day: the same
  commands, no `EMERGE_DEFAULT_OPTS`, sshd_config's own
  `PermitRootLogin prohibit-password`, `/etc/conf.d/hostname`).
- **The first runs** (`g6-1` Chopin, `g6-2` Cinqcent) failed verify on one
  file: `usr/libexec/rc/console/keymap`, the same content with a new mtime.
  OpenRC's `save-keymaps` (in the boot runlevel, the Thinkpad's too) writes
  the console keymap there at every boot. bs1-manifest's `REMADE` now has
  `usr/libexec/rc/console/*` (content, owner and mode must stay; the mtime
  may move), which covers `save-termencoding`'s `unicode` as well.
- **The lvmid check** is BackShot1's own now: phase 1 writes the baseline's
  `lvmid.txt` (each id an EFI binary on a mounted vfat embeds, and
  /boot/grub's LV by `lvs -o vg_uuid,lv_uuid`), and verify checks the
  restored system's (`lvmid-after.txt`) whenever either side has one, as
  ht1.sh does. On `gentoo` both `EFI/gentoo/grubx64.efi` and
  `EFI/BOOT/BOOTX64.EFI` embed vg0/root's, before and after.
- **Then `g6-3` (Chopin, 311 s) and `g6-4` (Cinqcent, 338 s) PASS**: 25
  checks each, 261,568 files compared, 0 unexpected, the lvmid check ok in
  2 EFI binaries, the guest's image unchanged. The firmware booted OVMF's
  entry for the disk (the run starts from blank variables, so the guest has
  no `gentoo` entry): the Thinkpad's own NVRAM is the test of that. RAM-disk
  peak 5.4 GiB.
- **`gentoo-systemd` too** (not asked for: systemd, LUKS by key file): `g6-5`
  (Chopin, 392 s) and `g6-6` (Cinqcent, 376 s) PASS, 264,576 files, 0
  unexpected, no lvmid (its GRUB is on an ESP at /boot). That guest has no
  avahi, so bs1-adopt emerged `net-dns/avahi` and `sys-apps/dbus`: avahi
  built from source (the binhost's doesn't have the guest's USE flags),
  about 80 s (est., from the run's length against `gentoo`'s). It wrote the
  vdb before phase 1, so the manifest saw it as source state, as
  "What already works" says of an emerge.

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| G1 | `make-bootstrap.sh gentoo` and `bootstrap/gentoo.sh`, `LAYOUT=efi-lvm` and its DISK_SIZE, the cap check, `rebuild-one.sh`, the rebuild inputs, vm-update's Portage branch. Settle every *check* on it: os-release, the vdb's files and `CHOST`, kernel names and `.old` copies, `install.conf`, no `/lib/modules/K/initrd`, dracut's version, the service names, the source builds and the build time. Done when it is built on Chopin, copied to Cinqcent with the same SHA-256, boots alone, ping has its cap, and `vm-update --try` is ok. **Done 2026-11-12 except the ping cap:** built in 579 s, SHA-256 25f2f32d… on both hosts, boots alone on both, `--try` ok; ping is setuid root with no cap (Gentoo's USE=suid, as on the Thinkpad), the cap check waits on the user's ruling ("What G1 found") | 1.25 d |
| G2 | bko-holt: the `SUPPORTED` row (doc comment, a unit test from the guest's os-release, an `ID_LIKE=gentoo` refusal, `supported_names()`); the Portage reader and its tests; `PACKAGES` and `fingerprint.py`; the `.old` note; generator detection; `initramfs()`'s Gentoo arms and refusals; the shim refusal; a dump warning when fstab names a vfat /efi or /boot/efi that isn't mounted, with a unit test; a Thinkpad-shaped fake tree in `holt/src/scenarios.rs`. Done when `cargo test` passes and the scenario's plan has `dracut --force /boot/initramfs-K.img K`, the /efi `grub-install` and the `.old` note. **Done 2026-11-12** (546c65e, review fix bbc8d6b) | 1 d |
| G2b | `gentoo`'s golden plan (`holt/tests/corpus/gentoo/`, `index.tsv`, the corpus README) from a `bko-holt dump` of the guest; the Test2 registrations (`classes.json` with `uefi`, `holt/spec.json`, `guest/spec.json`, `estimates.tsv`, `t2lib/plan.py`'s counts, `p2/world.py` and the re-blessed goldens); the GuestTest2 rules. Done when two corpus builds have the same sha256, leak scans are 0 on both hosts and the Mac, no other plan changes, and Test2's own tests pass. **Done 2026-11-12** (642a0ed) | 0.5 d |
| G3 | `gentoo-systemd`: the build (keyfile, `rd.luks`, installkernel's layout pinned to `grub`), its golden plan, Test2 registrations and re-blessed goldens, `rebuild-one.sh` lines. Done as G1 and G2b are, plus a GuestTest1 grade for both guests. **Done 2026-11-12** (395e643; "What G3 found") | 0.75 d |
| G4 | SticTest1 entries and runs for both guests (rustic-bko and restic), the cross-filesystem case, `stic/manifests.json`, `limits.json` and check-manifests' 47. Done when PASS/LIMIT/FAIL counts on both hosts for both archivers show 0 FAIL. **Done 2026-11-12:** runs `g4f`, `gentoo` 35/3/0 and `gentoo-systemd` 29/3/0 on both hosts ("What G4 and G5 found") | 0.35 d |
| G5 | The HoltTest1 group (Decision 5) and its runs; judge.py rules for `efi/EFI/gentoo` and `boot/EFI/gentoo` (grub-install's loader), with judge-test.py cases, and any path G1's chroot runs show no rule covers. Done when every load stage passes on both hosts, the guest boots alone, there are 0 fsmeta differences, the embedded lvmid equals the restored `lvs -o vg_uuid,lv_uuid` of vg0/root, and the RAM-disk peaks are in `ram-sizes.tsv`. **Done 2026-11-12:** 1G; `gentoo` runs `g5-3` and `g5-4`, `gentoo-systemd` `g5-2` and `g5-5`, all PASS | 0.35 d |
| G6 | BackShot1: the fixes in the table; `bs1-qemu.py --virtio` rehearsals on `gentoo` on both hosts, with the same lvmid check; then a run on the Thinkpad. Done when phase1, plan, phase2 and verify PASS with 0 unexpected, the Thinkpad boots `gentoo` from NVRAM, and its `grubx64.efi`'s lvmid equals the restored vg0/root's UUIDs. **Fixes and rehearsals done 2026-11-12:** `g6-3` (Chopin) and `g6-4` (Cinqcent) PASS ("What G6 found"); the Thinkpad run is still to do | 0.5 d |
| G7 | Docs: holt/README.md's supported list and "tested by" guests, `osrel.rs`'s comment, the READMEs of HoltTest1 (its group table and HoltTest1All's members), BackShot1, SticTest1 and Test2, `docs/design/layload.md`'s boot stage (it still says only `update-initramfs`), and memory (the HoltTest1 index line and holttest1d.md, if 1G). **Done 2026-11-12**, memory included: holt/README.md's list and tests, `osrel.rs`'s comment, HoltTest1's 1G row (G5) and HoltTest1All's members, BackShot1's README (Gentoo, the lvmid check, `REMADE`), SticTest1's (G4) and Test2's (G2b), layload.md's boot stage (each generator, Gentoo's arms, the lvmid) | 0.1 d |
| | *≈ 4.75 d in all* | |

Each piece was committed on the branch when its tests passed, and the branch
merges into main. Nothing is pushed until
the user says so.

**Costs** (est.):

| # | Host time | Disk | The user's time |
|---|---|---|---|
| G1 | a 15-22 min build on Chopin (WORK_DIR in /mnt/bko-ram, `--reserve 8 4`), the copy | qcow2 3.5-4.5 GiB a host; stage3 and `.asc` ~0.3 GiB a host in rebuild-inputs | none |
| G3 | the same again, so both builds are 30-45 min on Chopin, plus the copy | the same; ~17 GiB in all for 2 guests x 2 hosts, with the inputs | none |
| G4, G5 | ~1 h of queue a host; at D 7 a run fits a 30G RAM disk beside two others | an update overlay (~0.5-1.5 GiB, beside the image until V1) | none |
| G6 | rehearsals ~30 min a host; the Thinkpad run ~45-75 min, mostly a 64G ext4 root's and 64G XFS /home's used data and the tree's ~150k files | the backup on `mybig512` | ~5 min: boot the stick if BootNext misses, click Done |

## Risks

- **Binary-package coverage moves.** A rebuild or update may need a source
  build the allowlist doesn't have. It stops and names the package; it is
  added to the allowlist, or its USE flags are changed to match a binpkg.
  A rebuild gets newer packages than the last ([[images-take-os-updates]]).
- **Stage3 pruning.** A guest whose rebuild inputs were lost can't be
  rebuilt at its pinned date. The fallback is the newest stage3, with a new
  pin.
- **The LUKS keyfile under systemd's dracut.** If `gentoo-systemd`'s
  host-only rebuild leaves the keyfile or the crypt module out, G3 finds it
  first; `gentoo` and the Thinkpad don't wait on it.
- **Build time.** `-smp 8` for the lvm2 and grub builds is the first remedy.

## Out of scope

- **genkernel, ugrd, UKIs** (`uki_generator`), **shim**, and installkernel
  layouts other than `grub`/`compat` (systemd-boot's `bls` among them):
  refused by `load` with a message naming them.
- **Hand-built kernels without an initramfs:** generator `none`, no
  initramfs step and a note, with a unit test and a scenario; no guest, so
  untested on a real boot.
- **musl profiles:** nothing in dump or load depends on libc, and Void and
  Chimera cover musl, but there is no Gentoo musl guest.
- **ZFS:** neither `zfs` nor `zfs-kmod` is on the binhost, and the Thinkpad
  isn't on ZFS. A later `gentoo-zfs` is its own piece (est. +0.75 d).
- **Split-usr profiles:** not claimed; load stops at the os-prober bind.
- Funtoo, Calculate and other `ID_LIKE=gentoo` systems: refused.
- BackShot2 cases on Gentoo: later, through BackShot2All. Changes to the
  rescue image.

## Decisions for the user

Approved 2026-11-12: the user chose every recommendation below.

1. **Guests:** `gentoo` plus a second that covers what the Thinkpad
   doesn't. `gentoo-systemd` as UEFI with LVM on LUKS (recommended: systemd's
   dracut, the crypt module and `rd.luks`, the riskiest untested path, for
   0.75 d); or `gentoo-systemd` as BIOS + MBR, the generic path other guests
   cover (and the debconf note would then need limiting to Debian's family,
   which changes Arch's and others' plans); or a genkernel guest, which
   turns genkernel's refusal into support (est. +0.25 d over the
   recommendation); or only `gentoo`.
2. **The boot stage on Gentoo:** bko-holt's own steps, first
   `dracut --force /boot/initramfs-K.img K` for each kernel, then
   `grub-install` and `grub-mkconfig` (recommended: the same steps as Fedora
   and Void, with no Portage or Python in the chroot). Or run Gentoo's own
   `installkernel KV ...` in the chroot. That also remakes the microcode
   images, but it renames the restored images to `.old`, so every restore
   adds a stale boot entry.
3. **Initramfs setups other than dracut:** refused by name until a guest
   tests them (recommended: only what a test runs is claimed); or genkernel
   and ugrd keep the restored image with a note, untested.
4. **Package names in the dump:** bare names (`grub`, `dracut`, `lvm2`;
   recommended: `PACKAGES` and every `has()` work as they are, and no
   boot-stack name repeats across categories); or `category/name`, which
   needs a Gentoo-aware lookup.
5. **HoltTest1's group:** a new **1G** (Gentoo) (recommended: it is the
   Thinkpad's system, worth running by name after a Gentoo change); or join
   **1Obscure**, as Chimera did.
6. **The update-initramfs fallback:** replaced by Gentoo's arms on Gentoo
   only (recommended: no other golden plan changes); or a refusal for every
   system outside Debian's family, which is its own change and a check of
   every plan.
7. **The Thinkpad's install for BackShot1:** the one it has now
   (recommended: the user's own install, which the `gentoo` guest copies;
   `gentoo-systemd` covers LUKS); or a reinstall with LUKS or ZFS (est.
   +0.5 d).
8. **A supported system** in bko-holt's `SUPPORTED`, named in its messages
   (recommended); or test guests only, with `dump` still refusing it outside
   the tests.
