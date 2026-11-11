# Bazzite and ostree support (approved)

2026-11-11. Came out of "Let's do BackShot1": the Thinkpad now runs Bazzite 44,
and `backshot1 preflight` stops with "/ isn't on nvme0n1". bko-holt has no
ostree support at all; nothing in holt/, docs/ or testvm/ mentions ostree,
composefs, rpm-ostree, bootc or bootupd.

B1 (the `kinoite44` guest) was built the same day, and its *checks* are
settled below: see "What B1 found". B2 (`dump`) followed: see "What B2
found".

**Conventions.** est. marks an estimate. *Check* marks a fact not yet seen, to be
settled on the first guest (B1) or on the Thinkpad before the code that depends
on it. The facts come from reading holt/src, testvm/ and the rustic_core fork,
and from read-only commands run on the Thinkpad over SSH on 2026-11-11.

## Summary

- bko-holt learns ostree systems: Bazzite 44 first, and Fedora's own atomic
  desktops (Silverblue, Kinoite) with it, since they share the layout.
- The backup is the **physical** root (btrfs subvolume `root`, which holds
  `ostree/repo` and the deployment checkouts) plus `/boot`, the ESP and the
  `home` and `var` subvolumes, laid out the way fstab mounts them. The
  composefs `/` is never walked: it's rebuilt at boot from what's restored.
- rustic-bko already keeps what ostree needs: hardlinks between regular files,
  the immutable flag on deployment roots, and `security.selinux` xattrs.
  Hardlinked symlinks come back as separate symlinks, which ostree doesn't
  mind. fs-verity it doesn't keep, but the Thinkpad doesn't use it.
- The boot stage on ostree regenerates nothing: no initramfs, no
  `grub2-mkconfig`, no `grub2-install`, no chroot. It checks the restored BLS
  entries against the deployments, recreates the NVRAM entry, writes its log,
  and runs the generic stage's tail (atimes, read-only subvolumes). With the
  source's UUIDs kept, every restored boot file is already right.
- One new guest, `kinoite44`, built by the existing Anaconda path
  (`make-fedora.sh --ostree`) and given a second deployment like the
  Thinkpad's; then BackShot1 on the Thinkpad, with both deployments booted.
- ~7.85 d (est.).

## Why

- **The Thinkpad runs it.** BackShot1 can't run until bko-holt can back up and
  restore Bazzite.
- **A root that isn't a block device.** `/` is an overlay named `composefs`.
  Everything that asks "where does / live" (BackShot1, the restored-boot
  probe in bs1-qemu.py) gets it wrong, and so would any real user's script.
- **A layout no guest has:** `/etc` inside a deployment directory, `/usr` as
  hardlinks into a content-addressed repository, a read-only sysroot,
  bootupd's static GRUB config, and an initramfs that belongs to the image.
  Each one breaks an assumption the generic Linux path makes.
- **Fedora atomic is common.** Silverblue, Kinoite, Bluefin and Aurora share
  it. Silverblue and Kinoite keep `ID=fedora VERSION_ID=44`, so `dump` lets
  them through today and then handles their layout wrongly. That is worse than
  a refusal.

## What an ostree system is (the Thinkpad)

ThinkPad X13 Gen 1, 7.4 GiB RAM (MemTotal 7,769,800 kB), Secure Boot off.
Bazzite 44.20261012 (Kinoite base), kernel 7.2.9-ogc3.1.fc44; ostree 2026.4,
rpm-ostree 2026.3, bootc 1.16.13, bootupd 0.3.2, composefs 1.0.8. SELinux
enforcing.

| What | Where | Notes |
|---|---|---|
| os-release | `/usr/lib/os-release` (`/etc/os-release` links to it) | `ID=bazzite ID_LIKE=fedora VERSION_ID=44 VARIANT_ID=bazzite` |
| Disk | nvme0n1, GPT, 238.5G | no LUKS, no LVM; swap is zram only; `/etc/crypttab` empty |
| ESP | p1, 600M vfat `F351-BAAB`, at `/boot/efi` | shim + GRUB in `EFI/fedora`, fallback in `EFI/BOOT`; 14M used |
| /boot | p2, 2G ext4 `bazzite_xboot`, `d31c0262-...` | `loader -> loader.1`, BLS `ostree-1.conf` and `ostree-2.conf`; kernel and initramfs in `ostree/default-<bootcsum>/`; bootupd's static `grub2/grub.cfg`; `bootupd-state.json`; 275M |
| btrfs | p3, 235.9G, `bazzite`, `1ee72bd4-...` | subvolumes `root` (256), `home` (257), `var` (258), all top level; default subvolume 5; no snapshots; 11.6 GiB used |
| `/` | composefs overlay, ro | lower is the deployment's `.ostree.cfs` (EROFS), data from `/sysroot/ostree/repo/objects`; no loop device (`losetup -a` empty) |
| `/sysroot` | subvol `root`, mounted ro | `ostree/{repo,deploy,boot.1}` plus empty mount points `boot dev home proc root run sys tmp var` |
| `/etc` | bind of `root:/ostree/deploy/default/deploy/<csum>.1/etc`, rw | every deployment has its own etc; 54M |
| `/var` | subvol `var`, rw | 7.3G, of which flatpak 7.2G; the stateroot's own var (`ostree/deploy/default/var`) holds only `.ostree-selabeled` and `tmp/` |
| `/var/home` | subvol `home` | fstab says `/home`; `/home -> var/home` in the image; 238M |
| fstab | in the deployment's etc | `/ btrfs subvol=root,compress=zstd:1,ro`; `/boot`; `/boot/efi`; `/home subvol=home`; `/var subvol=var` |
| Deployments | `default` stateroot | `be4ed1ff...e0d.1` (booted) and `.0` (rollback): the same commit, differing in kargs (`bluetooth.disable_ertm=1`) and /etc |
| Repository | `/sysroot/ostree/repo`, `mode=bare`, 11G | 193,564 `.file` objects, 17,493 of them symlinks; 176,055 regular files with links>1 (mostly 3: repo, deploy .0, deploy .1). Config: `[sysroot] readonly=true bootloader=none`, so ostree never writes GRUB's config |
| composefs | `prepare-root.conf`: `enabled = yes`, `readonly = true` | journal: "mounted successfully (verity=false)"; no fs-verity on objects |
| Immutable | `chattr +i` on the two deployment roots only | |
| Image | `ostree-image-signed:docker://ghcr.io/ublue-os/bazzite:stable` | `:stable` moves to the next Fedora major when Bazzite does; no layered packages, no overrides, no client-side initramfs, nothing staged |
| Updates | `uupd.timer`: `OnCalendar=04:00`, `RandomizedDelaySec=15m` | LAST was 08:17 today, outside that window, so it catches up (*check* `systemctl cat uupd.timer` for `Persistent=` and any other trigger). `bootloader-update.service` enabled: bootupd rewrites the ESP from `/usr/lib/bootupd/updates` at boot when `bootupd-state.json` is older. `rpm-ostreed-automatic` and `bootc-fetch-apply-updates` disabled |
| GRUB env | `/boot/grub2/grubenv` | `boot_success=1`, no `boot_counter`. *Check* whether greenboot is installed |
| NVRAM | Boot0009 "Bazzite", `HD(1,GPT,e53caff6-...)\EFI\fedora\shimx64.efi` | many stale entries for disks long gone |

How it boots: shim, then GRUB from `EFI/fedora`, whose stub `grub.cfg` finds
/boot by `BOOT_UUID` (in `bootuuid.cfg`, on both the ESP and /boot) and loads
bootupd's `/boot/grub2/grub.cfg`. That runs `blscfg` over
`/boot/loader/entries`. Each entry's `ostree=/ostree/boot.1/default/<bootcsum>/N`
resolves through relative symlinks (`boot.1 -> boot.1.1`, `N -> ../../../deploy/default/deploy/<csum>.S`)
to a deployment. In the initramfs, `ostree-prepare-root` mounts that
deployment's `.ostree.cfs` as `/`, the physical root at `/sysroot`, binds the
deployment's `etc`, and fstab mounts `var` and `home` after it.

## What works today, and what breaks

**`bko-holt dump`** (live on the source):

- **OS check refuses Bazzite.** `SUPPORTED` (osrel.rs:77) matches ID plus the
  major VERSION_ID, and derivatives don't count by `ID_LIKE` (osrel.rs:129, with
  tests). `ID=bazzite` fails. os-release itself is read fine: the
  `/etc/os-release` symlink resolves on the running system.
- **The physical root is missing.** `is_layout_mount` drops `overlay` as a
  pseudo filesystem (mounts.rs:67-70). That's right for composefs, but then
  `storage.mounts` has no `/` at all, and fstab's `/` (subvol `root`) shows up
  only as `/sysroot`.
- **Aliases count as mounts.** The `/etc` bind (FSROOT
  `/root/ostree/deploy/.../etc`) and the stateroot-var bind are listed beside
  `/sysroot`, though they're subdirectories of the same subvolume.
- **btrfs properties can go missing** (a generic bug, ostree just shows it).
  `btrfs_reachable_path` (report.rs:840) matches mounts by `subvol=` and ignores
  FSROOT. The `/etc` bind carries `subvol=/root`, so if it comes before
  `/sysroot` in mountinfo, subvolume `root` resolves to `/etc`,
  `btrfs property get -t s /etc` fails, and `root`'s ro and compression
  properties are lost. `mounted_subvolumes` lists `/etc` as a mount of `root`
  too. *Check* the order on the Thinkpad (`/etc` has mount ID 46, `/sysroot` 50).
  On kinoite44 (B1) `/etc` (48) comes before `/sysroot` (52) in mountinfo,
  so the guest shows the bug.
- **No kernels.** `kernels_json` (boot.rs:532) lists `/boot`'s top level only;
  ostree's are in `/boot/ostree/<stateroot>-<bootcsum>/`.
- **Wrong initramfs generator.** `initramfs_json` (boot.rs:594) says `dracut`
  because the dracut rpm is installed. On ostree the initramfs is the image's.
- **GRUB looks ordinary.** `grub_json` takes `/boot/grub2`; `detect_loader`
  gives `grub` via shim from BootCurrent. `load` would take the Fedora branch.
- **BLS entries reported as systemd-boot.** `systemd_boot_json(sys, "/boot")`
  runs whenever no ESP has `loader/` (boot.rs:761), so ostree's entries fill
  `boot.systemd_boot` on a GRUB system. `loader.name` is still `grub`.
- **Fedora's loader packages aren't named.** The UEFI GRUB lookup (boot.rs:713)
  knows Debian's and Chimera's names, not `grub2-efi-x64` or `shim-x64`. Not
  ostree's fault: Fedora 44 has the same gap.
- **Works as is:** packages (`rpm -qa`, since `/usr/lib/sysimage/rpm` exists),
  fstab and crypttab (read through the `/etc` bind), the cmdline (`ostree=`
  sits in `raw` and `words`), efibootmgr, `--only-disk` (no loop device here).

**`bko-holt load`:**

- **The mount stage mounts / read-only.** `mount_options` (commands.rs:1959)
  keeps `ro`, and fstab's `/` has it, so the restore fails. Dropping `ro` for
  the stage's own mounts is right for every system. One side effect on
  non-ostree systems whose fstab `/` has `ro`: a host-only dracut run in the
  boot stage may record `rw` among the root's flags instead of `ro`. Harmless,
  since fstab remounts `/` at boot, but plans may show it.
- **The rest of the mount stage is right.** It mounts fstab's `/` (subvol
  `root`, the physical root) at the target, then `/boot`, `/boot/efi`, `/home`
  (subvol `home`) and `/var` (subvol `var`) beneath it. The mount points
  already exist in subvol `root`. That tree is the convention the design below
  builds on.
- **The gate refuses.** Boot needs `restored_marker()` (other/mod.rs:195),
  which is `etc/fstab` for Linux, and exec.rs:93 and status.rs:141 each test it
  with one `sys.exists()`. The physical root has no `etc/fstab`.
- **Every chroot fails.** The physical root has no `/bin/sh`, `/usr` or `/lib`
  (it does have empty `dev proc sys run`, so the rbinds mount), and every
  `chroot {t}` exits 127. A chroot into a deployment would run, but is
  harmful: its `/usr` files are hardlinks to repository objects, whose
  checksums cover content, mode, owner and `security.selinux`. dracut writing
  into `/usr/lib/modules` or `setfiles` over `/usr` would corrupt objects
  silently.
- **Initramfs:** the dracut loop finds no `/lib/modules` and fails. Rebuilding
  the real one would break its `bootcsum` naming anyway.
- **GRUB:** `grub2-mkconfig` (commands.rs:2339-2344) would replace bootupd's
  static `grub.cfg` (and fails in the physical root). No `grub2-install` runs on
  UEFI Fedora today; shim and GRUB come back with the ESP. That part fits.
- **NVRAM:** `efi_entries` recreates `\EFI\fedora\shimx64.efi` with the dump's
  label. Right as is.
- **Log and SELinux:** `{t}/var/log/bko-holt` is right on the Thinkpad,
  because subvol `var` is mounted at `{t}/var`. Without a var subvolume it
  would land in the physical root's empty `var/`, not the stateroot's. The
  `setfiles` chroot fails.
- **LUKS and the LVM devices file** use `{t}/etc/...`, which on ostree is
  `{t}/ostree/deploy/S/deploy/<csum>.N/etc/...`. The Thinkpad has neither.
- **Atimes:** they still work, across the 11G repository and checkouts.
  `touch -a` on files inside a `+i` directory works: the flag blocks entry
  changes, not the files' inodes.

**rustic-bko** (the fork, as `load` and BackShot1 use it):

- **Hardlinks between regular files are kept.** Files with nlink>1 get a
  device ID in the backup (mapper.rs:276-277), and restore relinks by
  (device, inode), but only for `NodeType::File` (`hardlink_key`,
  restore.rs:1192-1200). The repository's regular objects and the checkouts
  are on one subvolume, so they come back linked. The first path in tree order
  is the one written, then linked: `ostree/deploy` sorts before `ostree/repo`.
- **Hardlinked symlinks come back as separate symlinks.** A bare repository
  stores symlink objects and hardlinks them into every deployment (17,493
  symlink objects; the deployment's `bin -> usr/bin` has link count 5). They
  restore with link count 1. Harmless: composefs and `ostree fsck` look at the
  target and the xattrs, not the link count, and the space is a few MB. Every
  link-count check below covers `-type f` only.
- **The immutable flag is kept**, and set late (osattrs/linux.rs:31-50), so the
  deployment roots get `+i` after their contents.
- **xattrs are kept**: `security.selinux` on every object, `security.capability`
  and `user.component` on a few.
- **fs-verity is not** (linux.rs:33, on purpose). Irrelevant with
  `enabled = yes` and no `ex-fsverity` in the repository config; fatal with
  `verity` or `signed`.
- *Check* the hardlink map's memory in the rescue image. The map is keyed per
  inode (`BTreeMap<HardlinkKey, PathBuf>`): about 176k inodes on subvol `root`
  and about 60k on subvol `var` (the flatpak repository's 288,989 paths with
  links>1, est. from its link-count histogram), so ~240k entries on a 7.4 GiB
  machine. rustic-bko's restore memory is already a known problem on small
  machines ([[openbsd-restore-oom]]).

**BackShot1** (`testvm/realworld/backshot1`):

| Script | Line | What breaks | Fix |
|---|---|---|---|
| bs1-backup | 24 | `on_disk "$(findmnt -nro SOURCE /)"`: `/` is `composefs`, so on_disk falls to its ZFS branch and fails. The stop the user saw | on ostree, test the mount matching fstab's `/` (`/sysroot`) |
| bs1-verify | 81 | the same check | the same fix |
| bs1-common | 86-95 | `internal_mounts` drops `/` (overlay isn't in REAL_FS) and keeps every btrfs mount whatever its FSROOT, so the `/etc` and stateroot-var binds go in: the same files twice | read `storage.backup_sources` from layout.json (below) |
| bs1-common | 110-123 | `make_tree` binds each mount at `$SRC$target`. With no `/`, `/boot` goes to a directory that doesn't exist; `/sysroot` lands at `sysroot/`, and `/var/home` at `var/home` instead of fstab's `home` | build the tree in fstab's terms (below) |
| bs1-common | 19 | `SRC=/mnt/bs1-src` is both the tree's mount point and the snapshot's name, but `/mnt -> var/mnt`, and rustic canonicalises sources, so the snapshot's path would be `/var/mnt/bs1-src` | split it: `TREE=/run/bs1-src` (where the tree is mounted) and `SNAP=/mnt/bs1-src` (the snapshot's path) |
| bs1-backup | 50-57 | adds `df` per mount; three mounts of one btrfs count it three times (already true on btrfs guests) | count each device once |
| bs1-manifest | 31-58, 66-109 | EXPECTED and REMADE are logical paths (`etc/*`); /etc is at `ostree/deploy/*/deploy/*/etc/`. Every path is hashed again per link, so the repository is read three times | the mapping rule below; cache digests by (dev, inode); add nlink and flags columns for regular files under `ostree/` |
| bs1-adopt | 43-56 | `pkg_install` would run dnf against a read-only `/usr` | an ostree branch that never installs: it names what's missing, fails only when a required tool is missing, and leaves `rpm-ostree install` and the reboot to the user |
| bs1-qemu.py | 259, 128 | the restored-boot probe asks `/`'s source; the internal overlay is a fixed 32G without `--smaller` | the `/sysroot` case; size from the guest |

The `SRC` split, use by use: `TREE` in `make_tree` and `drop_tree`
(bs1-common 112-123), bs1-backup's `findmnt -R` (61) and manifest (84), and
bs1-verify's manifest (100); bs1-backup's `rustic backup` (91) takes `TREE`
with `--as-path "$SNAP"`; `SNAP` in bs1-restore's `--snapshot latest:` (110)
and `diff latest:` (164). The fork's backup has `--as-path` (one source only,
which is what bs1 passes). *Check* on kinoite44 that a backup of
`/run/bs1-src --as-path /mnt/bs1-src` lists as `latest:/mnt/bs1-src` and that
`--set-atime` still applies.

Adopt has already run on the Thinkpad (its `00-backshot1.conf` and host key
show in `ostree admin config-diff` of the booted `.1`), so the tools it needs
are there or were found. *Check* whether `.0`'s etc has them too (`diff -r`
of the two deployments' `etc/ssh`, `etc/hostname` and
`etc/systemd/system/multi-user.target.wants`): if adopt ran after `.1` was
made, the restored rollback boots with a different host key and hostname, and
can't be driven over SSH (decision 6). bs1-to-stick and bs1-restore need
nothing beyond the `SNAP` name: they write EFI variables, wipe, load and diff,
as long as `load` mounts the target in the same shape the backup has.

**SticTest1, HoltTest1, the guest tools:**

- **vm-mount** looks for `etc/fstab` at the btrfs top level or one level down
  (vm-mount:520-535). On ostree it's five down. HoltTest1's backup
  (ht1.sh:431-434), vm-chroot and compare.py all go through vm-mount.
- **compare.py** expects logical paths in its expected-difference lists, the
  same problem as bs1-manifest. Of the probes that ask for `/`'s source, only
  bs1-qemu.py:259 runs against an ostree guest (ht1.sh's `findmnt --verify`
  and compare.py's `findmnt -R` of its own mount point don't).
- **vm-update** takes `has dnf` and runs `dnf -y upgrade` (in-guest.sh:309-317),
  which can't work.
- **SticTest1:** backs up and restores trees; nothing ostree-specific, but no
  guest has hardlinked repositories or `+i` directories yet.

## The design

### One tree convention: fstab's

Every tool that backs up or mounts an ostree system builds the same tree,
**the one `load`'s mount stage already makes from fstab**:

| Path in the tree | Read from (running system) | Filesystem |
|---|---|---|
| `/` | `/sysroot` | btrfs subvol `root` |
| `/boot` | `/boot` | ext4 |
| `/boot/efi` | `/boot/efi` | vfat |
| `/home` | `/var/home` | btrfs subvol `home` |
| `/var` | `/var` | btrfs subvol `var` |

Each fstab entry is matched to its running mount by (device, `subvol=`), not
by target path, because the targets differ (`/` vs `/sysroot`, `/home` vs
`/var/home`). A mount whose FSROOT is a strict subdirectory of another
included mount of the same filesystem is an alias and left out (`/etc`, the
stateroot var). Binds are read-only and non-recursive, so `/var`'s `home`
mount point comes through empty and subvol `home` fills `/home`.

The tree holds `ostree/repo`, both deployments (their `etc`, `.ostree.cfs`,
`backing/`), the `boot.1` symlink chain, `/boot` with `loader -> loader.1`,
the ESP, and the two other subvolumes. Raw, both deployments: the rollback
costs ~98M on top of the shared objects. Nothing is re-pulled from the
registry. bs1-restore's `diff latest:/mnt/bs1-src` against `/mnt/bko-target`
then compares like with like.

**Who builds it** (decision 4): on the running source, `dump` writes the list
as a new `storage.backup_sources`, each entry the path in the backup, the path
to read from, and the device; BackShot1 reads it from layout.json instead of
guessing with findmnt. On a disk, `load` and vm-mount both start from the
deployment's fstab (load through `btrfs_plans`, as today). That is two
implementations of one rule, so a corpus test ties them: for every corpus
dump, `backup_sources`' backup paths equal the mount points load plans from
fstab. On a non-ostree system `backup_sources` is the existing mount list, so
nothing changes there.

### Detection

ostree is recognised by its own markers, not by os-release: `/run/ostree-booted`
exists, or `/`'s source is `composefs`, or the cmdline has `ostree=`. That
covers Silverblue and Kinoite, which pass the OS check as `fedora 44`.

`SUPPORTED` gets `("bazzite", "44", "Bazzite 44")`. Bluefin and Aurora get rows
only once their os-release is seen (*check*: `ID=bluefin`/`aurora`, or
`fedora`). `ID_LIKE` stays ignored; it would admit every Fedora remix. A
`bazzite 45` row comes only with a guest or a run that shows it (decision 5).

### `dump`

- `boot.ostree`, a new section (holt/src/ostree.rs): booted (from
  `/run/ostree-booted`), the `ostree=` karg, the stateroot, the booted
  deployment's checksum and serial, every deployment's path, `/boot/loader`'s
  target, the BLS entries' file names and `options` lines, `prepare-root.conf`'s
  composefs and readonly settings (`/usr/lib/ostree` and `/etc/ostree`), the
  repository config's `core.mode`, `sysroot.readonly`, `sysroot.bootloader`
  and any `[ex-fsverity]` section, whether `/boot/bootupd-state.json` exists,
  and grubenv's keys (`boot_success`, `boot_counter`, `next_entry`).
  Optionally `bootc status --json` for the image reference. `load` re-derives
  the same facts from the restored tree and cross-checks.
- `storage`: name the physical root (fstab's `/` at `/sysroot`), mark aliases,
  and add `backup_sources`. (B2: `storage.physical_root`, `alias_of` on an
  alias's entry in `storage.mounts`, and `storage.backup_sources` entries of
  `path`, `from`, `device`, `fstype`, `source` and `subvol`; see "What B2
  found".)
- `kernels_json`: also `/boot/ostree/*/vmlinuz-*` and `initramfs-*.img`.
- `initramfs_json`: `prebuilt` on ostree, even with dracut installed.
- The BLS entries recorded as ostree's, not as systemd-boot's.
- `btrfs_reachable_path` and `mounted_subvolumes` skip mounts whose FSROOT
  isn't the subvolume's own root (generic fix).
- `grub2-efi-x64` and `shim-x64` in the UEFI package lookup (generic Fedora
  fix). (B2: both were already in `PACKAGES` and fingerprint.py's mirror;
  the gap was `detect_loader`'s GRUB list, which now ends with
  `grub2-efi-x64`, and the loader gets `shim_package`.)

Systems without bootupd (a Silverblue or Kinoite upgraded from a release
before it may lack `bootupd-state.json` and have `bootloader=auto` or `grub2`
with a `grub2-mkconfig`'d blscfg `grub.cfg`) are restored verbatim too: /boot
and the ESP come back as they were, whichever tool wrote them. Only the
bootupd layout is tested, so the plan says so in a note when
`bootupd-state.json` is missing (decision 2 has the stricter option).

### Hardlinks, flags, composefs, SELinux

- **Hardlinks:** regular files are kept by rustic-bko, as above; symlinks
  aren't, and nothing checks them. bs1-verify checks the regular files per
  subvolume: the multi-link inode count and the nlink histogram of
  `-type f` under `ostree/`, both equal to the baseline, plus nlink in the
  manifest's new column. btrfs space used is checked one-sided only (restored
  not more than ~5% above the baseline): broken links show as 3x, while
  compression makes a good restore smaller (compress is filesystem-wide, set
  by the first mount, so `/var` is compressed too).
- **Immutable flag:** restored late by rustic-bko. Nothing in the boot stage
  writes inside a deployment, so it doesn't get in the way. The baseline
  records `lsattr -d` of every deployment root, and verify compares it.
- **composefs:** nothing to do. `.ostree.cfs` is a regular file in each
  deployment, the objects are restored at the same paths, and with
  `verity=false` boot needs only their content. `load` refuses (a plan problem)
  when `prepare-root.conf` says `verity` or `signed`, or the repository config
  has `[ex-fsverity] required=true`: fs-verity can't come back through rustic,
  and `ostree fsck` and later pulls would complain. `maybe` gets a plan note.
  Re-enabling verity later would be `ostree admin post-copy --sysroot={t}` from
  the rescue system, or `ostree admin post-copy` on the restored system after
  boot (unverified).
- **SELinux:** every label comes back from the backup's `security.selinux`
  xattrs, which the rescue kernel stores as plain xattrs, as on Fedora today.
  Objects need them exactly, since the label is part of each checksum. No
  relabel, ever. The only new files are the log directory and its files,
  labelled by copying `var/log`'s `security.selinux` onto the directory and
  every file in it from the rescue system. The image has no `attr` package
  (B1: no `getfattr`/`setfattr` in the slim base, only `libattr1`), but it has
  python3, whose `os.getxattr`/`os.setxattr` do it with no image change.

### `load`

- **Mount stage:** drop `ro` from fstab's options for the stage's own mounts
  (every system; the dracut nuance above). compress=zstd:1 stays, so the
  restored data is compressed though the source's live data may not have
  been; harmless.
- **Gate and status:** on ostree, `restored_marker()` returns
  `ostree/repo/config` instead of `etc/fstab`. One path, so it keeps its
  `&'static str` and exec.rs and status.rs stay as they are. `boot/loader`
  pointing at `loader.0` or `loader.1` is left to the consistency check.
- **The boot stage**, an early `if self.ostree { return self.ostree_boot(); }`
  next to the non-Linux return (commands.rs:2248). Its order follows the
  generic path's:
  1. The atimes listed `if-missing` first (as commands.rs:2290 does), before
     anything reads the tree.
  2. A note: /boot, the ESP and `/ostree` are restored verbatim; no initramfs,
     no `grub2-mkconfig`, no `grub2-install`, no chroot.
  3. A consistency check, as a shell test the plan shows: for every
     `boot/loader/entries/ostree-*.conf`, its `linux` and `initrd` files exist
     under `{t}/boot`; its `ostree=` path resolves to a directory under
     `{t}/ostree/deploy/<stateroot>/deploy/`; and its `boot.N` matches
     `loader.N`. A live backup can catch /boot (ext4) and the root (btrfs) on
     either side of a deploy; this is where it shows. It fails the stage with
     the mismatch instead of leaving an unbootable disk. The reverse (every
     `deploy/*` has a BLS entry) is a warning only: an orphan checkout, from
     a backup taken while a deployment was staged, boots fine and goes at the
     next `ostree admin cleanup`.
  4. `efibootmgr` entries exactly as today, unless `--no-nvram`.
  5. The log into fstab's `/var` if it has one, else the stateroot's var, and
     labelled as above.
  6. The generic tail: atimes put back, then the read-only btrfs subvolumes
     set (commands.rs:2395-2420), so a Bazzite user's read-only snapshots
     (btrbk, snapper) come back read-only. The tail is factored out of
     `boot()` and called from both paths, so they can't drift.
- **Plan problems on ostree, v1:** composefs `verity` or `signed`, or
  `ex-fsverity` required; BIOS boot (bootupd's core.img sits in the BIOS-boot
  partition, which the Linux partition stage doesn't copy); LUKS of any kind
  and LVM (their files are in a deployment's etc; passphrase LUKS2 with
  `rd.luks.uuid` in the BLS options, and TPM2 or FIDO2 tokens from Bazzite's
  `ujust setup-luks-tpm-unlock`, are untested; a path map and a LUKS guest
  are a day, later); no fstab `/` (bootc `install to-disk`, Fedora CoreOS);
  no separate `/boot` in fstab (BLS paths are then `/boot/ostree/...` inside
  the physical root, which the consistency check doesn't resolve);
  `--new-uuids` (a global plan problem already).

When `--new-uuids` is built, the ostree rewrite set is plain text from the
host, no regeneration: the current `loader.N/entries` (`root=UUID=`,
`rd.luks.uuid=`, `resume=`), `bootuuid.cfg` on /boot and the ESP, fstab and
crypttab in every deployment's etc; never `/usr` or `/usr/etc`; and a refusal
if `/boot/ostree/initramfs-overlays/` or a client-side initramfs exists. About
a day on top of the generic work; not in this spec.

## Guests

**`kinoite44`**, by `make-fedora.sh --ostree IMAGE --name kinoite44 44-1.7`:
the same unattended Anaconda from `Fedora-Server-dvd-x86_64-44-1.7.iso`, with a
new `fedora/ks-ostree.cfg`. Anaconda pulls the image inside the guest over
QEMU's user network, so the hosts need no podman, skopeo or bootc (they have
none). `storage-ostree.ks` copies the Thinkpad: GPT, ESP 600M at `/boot/efi`,
ext4 `/boot` 2G, btrfs with subvolumes `root`, `home` and `var`. UEFI (OVMF),
disk 32G, memory 4096 (make-fedora.sh's 16G and 2048 are too small).

`ks-ostree.cfg` against ks.cfg:

- `ostreecontainer --url=quay.io/fedora/fedora-kinoite@sha256:DIGEST
  --no-signature-verification` in place of `cdrom`, and no `%packages`.
  DIGEST is the `:44` manifest list's, from the registry's HTTP API.
- Kept: `text`, `lang`, `keyboard`, `timezone`, `network ... --hostname`,
  `firstboot --disable`, `skipx`, `rootpw --iscrypted`,
  `sshkey --username=root` (it lands in `/var/roothome/.ssh`), `poweroff`.
- New: `services --enabled=sshd --disabled=mcelog` (the atomic desktops ship
  sshd disabled; mcelog fails at every boot on the hosts' AMD CPUs, and an
  image can't leave its package out as the Server guests do).
- Dropped: the `%post` that turns off the fedora-updates dnf repositories,
  which means nothing on ostree.
- *Check* whether Kinoite 44 starts a first-boot setup on SDDM despite
  `firstboot --disable` (KDE's own initial setup). Serial login and SSH don't
  need the desktop, so a wizard on the screen is harmless unless it holds
  sshd back.

The guest's origin is `ostree-unverified-registry:` (because of
`--no-signature-verification`), unlike the Thinkpad's `ostree-image-signed:`.
That changes what `rpm-ostree upgrade` verifies in vm-update, not the layout
bko restores. *Check* whether `quay.io/fedora/fedora-kinoite` is signed with a
policy the installer can use; if so, drop the flag.

**Two deployments, like the Thinkpad.** Anaconda leaves one. B1 then makes a
second exactly as the Thinkpad's differ (`fedora/ostree-boots.sh`, run by
make-fedora.sh over SSH): `rpm-ostree kargs
--append=bko.rollback=1`, a clean `systemctl reboot` (ostree-finalize-staged
writes the staged deployment only on a clean shutdown), and one `/etc` edit
in the new booted deployment (`/etc/bko-deployment` with its serial). So `.1`
boots by default, `.0` is the rollback, both the same commit, differing in
kargs and /etc, with `ostree-1.conf` and `ostree-2.conf`, `boot.N` with two
serials, and 3-link objects. The `fresh-install` snapshot is taken after that.
B5 and B6 assert two BLS entries before they start.

**Built once, on Chopin.** The `:44` tag's digest is resolved and recorded
beside the disk (`kinoite44.digest`), the install uses `@sha256:` of it, and
B1 then runs `bootc switch --mutate-in-place --transport registry
quay.io/fedora/fedora-kinoite:44` on the first boot, before the kargs, so later
updates follow the tag. A plain `bootc switch` (or `rpm-ostree rebase`) to the
same image under another reference stages a new deployment (B1: serial 2,
same commit), so it would have left three; `--mutate-in-place` rewrites the
booted deployment's origin and nothing else. It writes in `/sysroot`, so it
runs in a mount namespace of its own with `/sysroot` remounted rw. The finished
image is copied to Cinqcent once; after that, updates go Chopin first in a
`--keep` overlay that's shipped ([[update-guests-not-copy]],
[[split-chopin-cinqcent]]). An ostree upgrade writes GBs of new objects into
that overlay; vm-update runs `qcow2-punch.py` after it.

*Checks* to settle on it, before B2 (all settled in "What B1 found"):

- The F44 DVD's Anaconda accepts `ostreecontainer` with no `%packages`, and
  `btrfs ... --subvol --name=var` beside it.
- What Anaconda writes: fstab's `/` with `ro`, a `var` subvolume, the
  stateroot name (`default` or `fedora`; never hardcoded), mountinfo order of
  `/etc` and `/sysroot`, the repository config's `sysroot.bootloader`, and
  `bootupd-state.json`.
- `prepare-root.conf` says `enabled = yes`; objects have no fs-verity; the
  deployment roots have `+i`; the repository is `mode=bare`.
- Kinoite ships python3, avahi-daemon, rsync, efibootmgr, findmnt, lsblk; dnf
  present or not. **bs1-adopt passes on kinoite44 unattended** (bs1-qemu.py
  needs it): what's missing must be optional, or the guest's builder layers
  it. Layering changes the guest's shape (the Thinkpad has no layered
  packages), so adopt's ostree branch treats as optional whatever bs1 can do
  without.
- uupd is ublue's, not Kinoite's; *check* which update timers Kinoite has, and that none is
  enabled.
- **What changes at boot on ostree**, from data: boot kinoite44 twice, take a
  bs1-manifest of the fstab tree after each, and diff. The diff becomes
  bs1-manifest's and compare.py's ostree EXPECTED list. Candidates:
  `ostree/deploy/*/var/**`, `ostree/repo/tmp/**`, repository lock and cache
  files that `rpm-ostree status` or `bootc status` touch, `ostree/bootc/**`,
  `boot/grub2/grubenv`, `boot/bootupd-state.json` (if bootloader-update
  rewrites it).
- `Fedora-Server-dvd-x86_64-44-1.7.iso` on Cinqcent (`/bkoscratch/rebuild-inputs`,
  not looked at). Chopin has it.

**Room** (measured in B1). kinoite44's repository is 6.8 GiB (du), the two
checkouts 165 MiB beyond their links into it, /boot 263 MiB, the ESP 14 MiB,
/var 15 MiB, /home empty; btrfs uses 5.0 GiB in all (uncompressed: see
below), and the qcow2 is 5.9 GiB. HoltTest1's D is then est. ~9-10 GiB (the
source's overlay, a repository of est. ~3 GiB, the restored disk ~6 GiB). A
bs1-qemu.py rehearsal holds the guest's overlay (~1 GiB), the USB drive with
the repository (est. ~3-4 GiB), and the restored internal disk (~6 GiB): est.
peak ~11-12 GiB. Both run from /mnt/bko-ram under `cmp-run.sh --reserve`
(rehearsal: `--reserve 12 6`, `WEAR_TAG=bs1:kinoite44`), and wait for room
rather than fall back to /bkoscratch ([[work-in-ram]]). B6 and B8 measure the
peaks and replace these.

It gets: a `rebuild-one.sh` line, `--uefi --no-secure-boot --memory 4096` in
vm-run-options (Secure Boot off, as on the Thinkpad: with it on,
`bootloader-update.service` fails at every boot because OVMF's db lacks
Microsoft's 2023 UEFI CA, B1), `ram-sizes.tsv`
rows, a `stictest1/kinoite44` entry, a GuestTest1 grade, both hosts, and the
Test2 registration in B7.

Not built: a `bazzite44` guest (8-10 GB deployed; a bs1-qemu.py rehearsal would
need ~22-26 GB of the 30G RAM disk, so it can't share it). The Thinkpad is the
Bazzite test. Fedora CoreOS from its qcow2 would be cheap, but has xfs root,
labels instead of fstab, and no python3 or avahi: out of scope.

If Anaconda's `ostreecontainer` path on the Server DVD won't take the layout,
B1 falls back to the Kinoite ISO's own installer: ~3 GB more download, a
second kickstart, and est. +0.5 d. B1 decides that on its own and says so.

**Guest tools:**

- **vm-mount, ostree mode:** find the default deployment (the BLS entry with the
  highest `version` on /boot, else the single `ostree/deploy/*/deploy/*`), take
  its `etc/fstab`, and mount the fstab tree above at MP. HoltTest1 and
  vm-chroot follow.
- **The etc mapping**, one rule in one Python module
  (`testvm/ostree_paths.py`), imported by bs1-manifest and compare.py: only
  the booted (default) deployment's `ostree/deploy/S/deploy/C.N/etc/` maps to
  `etc/` for matching EXPECTED and REMADE (`etc/machine-id`, `etc/.updated`,
  `etc/selinux/*/active/*`). Every other deployment's etc keeps its own path
  and gets no exceptions: it must come back byte-identical. The ostree
  EXPECTED list (from the B1 diff) is in the same module.
- **vm-update:** an ostree branch before `has dnf`: `rpm-ostree upgrade`, the
  clean reboot vm-update already does, then `rpm-ostree cleanup -b -m` (base
  temp files and the metadata cache) and `qcow2-punch.py`. No `cleanup -r`:
  ostree keeps the booted deployment plus one rollback by itself, and the
  guest must keep two. After an upgrade the two are different commits, unlike
  the Thinkpad's; the `fresh-install` snapshot keeps the same-commit pair.
- **SticTest1's `kinoite44` check:** fedora44-uefi's btrfs checks, plus a repo
  object hardlinked into both deployments (nlink 3), the deployment root's
  `+i`, and `security.selinux` on bare objects, for rustic-bko and restic.
  *Check* what restic keeps of `+i` and of hardlinked symlinks; a difference
  is recorded, not fixed ([[cross-tool-compat-courtesy]]).
- **HoltTest1 and the bs1-qemu.py rehearsal boot the restored guest twice**:
  the default entry, then the rollback, picked with `grub2-reboot 1` over
  SSH (or the GRUB menu on serial). Each boot's deployment serial, kargs and
  `/etc/bko-deployment` must match the baseline.

## What B1 found

Settled on `kinoite44` (Fedora Kinoite 44.20261111.0, `:44` =
`sha256:d78c9d54...` (manifest list), amd64 manifest `sha256:c5e91226...`,
commit `10e6a37f...`; kernel 7.3.3-200.fc44, ostree 2026.4, rpm-ostree 2026.3,
bootc 1.17.1, bootupd 0.3.2, composefs 1.0.8), 2026-11-11. The checks the
Thinkpad has to answer (uupd's timer, greenboot, its mountinfo order, `.0`'s
etc, Bluefin's os-release) stay open; B1 doesn't touch the Thinkpad.

- **Anaconda takes the layout.** The Server DVD's installer accepts
  `ostreecontainer` with no `%packages` and `btrfs /var --subvol --name=var`
  beside it; no fallback to the Kinoite ISO was needed. The install, most of
  it the guest pulling 3.1 GB of layers, took ~11 min; the two boots after it
  ~2 min.
- **What Anaconda writes:** fstab's `/` is `subvol=root,compress=zstd:1,ro`
  ("Updated by bootc-fstab-edit.service"), `/home` and `/var` the `home` and
  `var` subvolumes, all by UUID. The subvolume IDs differ from the
  Thinkpad's: var 256, home 257, root 258 (Thinkpad: root 256, home 257,
  var 258). The stateroot is `default`. The kernel command line has
  `root=UUID= rootflags=subvol=root rw`, and nothing mounts with `compress`:
  the initramfs mounts the filesystem first without it, so the guest's data
  is uncompressed (*check* the Thinkpad's mountinfo for `compress=`).
- **mountinfo order:** `/etc` (ID 48) before `/sysroot` (52), then the
  stateroot var bind (53), `/var` (83), `/var/home` (150). So the B2 FSROOT
  bug shows on the guest.
- **Repository config:** `mode=bare`, `[sysroot] readonly=true`, and
  `bootloader=none` once `bootloader-update.service` has run (the first boot
  after the install writes it, with `bootupd-state.json`; Anaconda leaves no
  `bootloader=` key). Anaconda also leaves a `[remote "default"]` whose url is
  the install's `@sha256:` reference, with `gpg-verify=false`; nothing uses
  it. `bootupd-state.json` exists; `bootupctl status` says "At latest
  version". GRUB's `grub.cfg` is bootupd's static one, unchanged by later
  deployments.
- **composefs, verity, flags:** `/usr/lib/ostree/prepare-root.conf` says
  `enabled = yes`, `readonly = true`; there's no `/etc/ostree/prepare-root.conf`.
  No object has fs-verity (`lsattr` shows no `V`). Both deployment roots
  have `+i`.
- **Links:** 134,530 `.file` objects, 14,195 of them symlinks; 120,320 regular
  objects with links>1 (the hardlink map's entries on subvolume `root`, vs.
  the Thinkpad's ~176k). Regular files under `ostree/` by link count: 1:
  18,107, 3: 339,954, 5: 29,760, then a tail of odd counts (files a
  deployment holds more than once).
- **Two deployments:** after `make-fedora.sh`, `.1` (default, booted,
  `bko.rollback=1`, `/etc/bko-deployment`) and `.0` (rollback), one commit,
  both origins `ostree-unverified-registry:quay.io/fedora/fedora-kinoite:44`;
  `boot.1 -> boot.1.1`, whose `0` links to `.1` and `1` to `.0`; BLS
  `ostree-2.conf` (`version 2`, `ostree=.../0`, `bko.rollback=1`: the default,
  `.1`) and `ostree-1.conf` (`version 1`, `.0`), so vm-mount's "highest
  `version`" rule picks `.1`; one `/boot/ostree/default-<bootcsum>/` with
  `vmlinuz-` and `initramfs-*.img` for both.
  The two etcs differ in `bko-deployment`, `selinux/targeted/active/commit_num`
  and two semanage lock files.
- **Tools on Kinoite:** python3, avahi-daemon (enabled), rsync, efibootmgr,
  findmnt, lsblk, getfattr/setfattr, chattr/lsattr, bash, sha256sum, and dnf
  (dnf5) are all in the image; nothing needs layering. **bs1-adopt passes on
  kinoite44 unattended, as it is** (in a throwaway VM, with throwaway keys):
  it installs nothing, so `pkg_install` never meets the read-only `/usr`, and
  firewalld's FedoraWorkstation zone gets ssh and mdns. What it writes lands
  in the booted deployment's etc only (`ostree admin config-diff`: hostname,
  `ssh/sshd_config.d/00-backshot1.conf`, the host keys, sshd's enablement),
  so on the guest too the rollback `.0` keeps the old hostname and host key.
  root's `authorized_keys` is in `/var/roothome`, which both deployments
  share, so the BackShot1 key logs in to either. B8's ostree branch in
  bs1-adopt is then only a guard (never `pkg_install` on ostree).
- **Updates:** no updater is enabled. `rpm-ostreed-automatic.timer`,
  `bootc-fetch-apply-updates.timer`, `podman-auto-update.timer`,
  `fwupd-refresh.timer` and `systemd-sysupdate.timer` are disabled; uupd
  isn't there. Enabled and harmless: `rpm-ostree-countme.timer` (a weekly
  ping), `bootloader-update.service` (bootupd at boot; a no-op at the latest
  version), `flatpak-add-fedora-repos.service`.
- **First-boot setup:** Kinoite 44 has no SDDM (Plasma Login,
  `plasmalogin.conf.d`), and `skipx` makes the default target
  `multi-user.target`, so no display manager starts. `plasma-setup.service`
  (WantedBy multi-user) runs at every boot, writes the autologin setting
  `etc/plasmalogin.conf.d/99-plasma-setup.conf` and exits; it doesn't hold
  sshd back. The Thinkpad boots graphical; the guest doesn't.
- **Signatures:** the image has no sigstore attachment on quay.io
  (`sha256-<digest>.sig` is 404) and the guest's `policy.json` is
  `insecureAcceptAnything`, so `--no-signature-verification` stays.
- **Failed units:** `systemd-remount-fs.service` fails at every boot: it
  remounts `/` with fstab's options and the composefs overlay refuses ("No
  changes allowed in reconfigure"). The Thinkpad's fstab `/` is the same, so
  it likely fails there too (*check* in B8's baseline); HoltTest1 compares
  failed units between source and restore, so it needs no exception.
  `bootloader-update.service` fails only with Secure Boot on (above).
- **What changes at boot** (the fstab tree, `mount --bind -o ro` per entry
  at `/run/bko-tree`, bs1-manifest after a boot and again after a reboot in
  the same QEMU; 535,025 rows, 77 s each): besides what EXPECTED already
  covers (`var/tmp/*` (systemd's private dirs), `var/cache/*`, `var/lib/*`,
  `var/log/*`, and `boot/efi/NvVars`, which only OVMF writes), only the
  booted deployment's `etc/plasmalogin.conf.d/99-plasma-setup.conf`,
  `etc/tuned/{active_profile,bootcmdline,post_loaded_profile,profile_mode}`
  and `etc/systemd/system.control/**` (root's SSH login's slice settings)
  changed, all in mtime only. Through the etc mapping those are three REMADE
  patterns for `ostree_paths.py`: `etc/plasmalogin.conf.d/*`, `etc/tuned/*`,
  `etc/systemd/system.control/*`. Nothing changed under `ostree/repo`
  (`tmp/` included) or `ostree/bootc`, in `boot/grub2/grubenv` or in
  `boot/bootupd-state.json`, though `rpm-ostree status` and `bootc status`
  ran before each manifest. The rollback's etc didn't change.
- **The rescue image has no `attr`** (above): B3 labels with python3.
- **vm-update** must not run on kinoite44 before B4: its `has dnf` branch
  would run `dnf -y upgrade`, which fails on an image-mode system.

## What B2 found

`dump` built as "The design" says (holt/src/ostree.rs, report.rs, mounts.rs,
boot.rs, osrel.rs), 2026-11-11. Checked against the shipped bko-holt by
dumping booted guests with both: kinoite44 and fedora44-uefi on Chopin,
tumbleweed and debian13 on Cinqcent (`--ram`, thrown away after).

- **The FSROOT bug was real on kinoite44.** The shipped dump asked
  `btrfs property get` at `/etc`, so subvolume `root` had no `ro` at all,
  and `mounted_subvolumes` listed `/etc` and the stateroot var as mounts of
  `/root`. Now the mounts that count are those whose FSROOT is their
  `subvol=` (`Mount::is_subvolume_root`): `root` resolves to `/sysroot`,
  and `mounted_subvolumes` is `/sysroot`, `/var`, `/var/home`.
- **Elsewhere nothing moved.** On tumbleweed (nine subvolume mounts,
  snapper's among them), fedora44-uefi and debian13 the new dump differs from
  the shipped one only by the new keys (`physical_root`, `backup_sources`,
  and on Fedora `loader.package` `grub2-efi-x64` and `shim_package`
  `shim-x64`): `mounted_subvolumes` and every subvolume's properties are as
  they were. So update/fingerprint.py, test_vm_update.py,
  holttest1d/judge.py and backshot2's bs2lib see the same values as before
  on every guest without a subdirectory bind. bs2lib reads only
  `storage.mounts`' `target` and `device`, which stay; judge.py compares two
  dumps of one binary, and `backup_sources` is sorted by path, so it needs
  no rule. fingerprint.py's layout now leaves out `backup_sources`,
  `physical_root` and `alias_of` (worked out from the mounts and fstab it
  already has), so no guest's holt digest moves on its first dump by the
  new binary; test_vm_update.py has a test for it. That edit changes
  fingerprint.py's `rules`, so the next `t2` plan says to run
  `guest-fingerprints --reproject`, which leaves the stored digests as they
  are.
- **Aliases** (`mounts::alias_of`): a mount whose FSROOT is a strict
  subdirectory of another mount of the same filesystem; of several, the
  deepest; a btrfs subvolume's own root never, though the top level is
  mounted too. On kinoite44 `/etc` and `/sysroot/ostree/deploy/default/var`
  are aliases of `/sysroot`. They stay in `storage.mounts` (with
  `alias_of`), since `--fit-largest` and bs2lib read the mounts by device.
- **`backup_sources` on kinoite44:** `/` from `/sysroot`, `/boot`,
  `/boot/efi`, `/home` from `/var/home`, `/var`, the same paths as fstab's
  mount points (B7's corpus test is to hold that for every dump). Matching is
  by the device fstab's spec resolves to plus `subvol=` (or `subvolid=`;
  neither means the top level), and only on ostree; elsewhere each
  non-alias mount is its own path (`physical_root` is then `/`). When two
  mounts share a path, the later (the one on top) wins.
- **`boot.ostree`** has: `booted` (`/run/ostree-booted`), `karg`,
  `physical_root`, `stateroot`, `booted_deployment` (the karg resolved
  through `boot.1 -> boot.1.1` and `N -> ../../../deploy/...`, as a path
  from the physical root's top), `deployments` (stateroot, checksum,
  serial, path, booted, origin, has_etc), `boot_loader` (`loader.1`),
  `boot_links`, `entries` (each BLS file's title, version, linux, initrd,
  options, `ostree=` and the deployment it leads to), `prepare_root`
  (effective `composefs` and `readonly`, `/etc`'s file over `/usr/lib`'s,
  and which files there were), `repo` (`mode`, `sysroot_readonly`,
  `sysroot_bootloader`, `ex_fsverity` as an object or null),
  `bootupd_state`, `grubenv` (`boot_success`, `boot_counter`,
  `next_entry`) and `image` (from `bootc status --json`: image, transport,
  digest, version). On kinoite44 every value matched B1's checks; its
  grubenv has no keys at all, so all three are null.
- **Kernels:** `/boot/ostree/*/vmlinuz-V` with `initramfs-V.img`, each with
  its `dir`; `config` is false (the image keeps it in `/usr/lib/modules/V`).
  `initramfs.generator` is `prebuilt`, and `boot.systemd_boot` is null.
- **Detection** is by `/run/ostree-booted`, a composefs `/`, or `ostree=`
  in the cmdline; the `bazzite_ostree_guest` scenario drops the first and
  is still told by the other two.
- **Load untouched:** the golden plans don't move (they are planned from the
  stored src.json, and no plan reads `loader.package`), so B7's expected
  re-bless of fedora* plans "from the package-name fix" won't happen unless
  B7 also re-dumps those guests.

## BackShot1 on the Thinkpad

After the bs1 fixes (B8) and a `bs1-qemu.py` rehearsal on `kinoite44`:

1. **Preflight**, extended for ostree: the physical root on nvme0n1;
   os-release's ID and VERSION_ID in `SUPPORTED` (so a Bazzite that moved to
   45 stops here, not in dump); no staged or pending deployment
   (`rpm-ostree status --json`). A staged deployment's checkout and objects
   are already on disk; only the /etc merge and the bootloader swap wait for
   shutdown. A backup then would hold an orphan deploy directory with no BLS
   entry, which the restored system carries until `ostree admin cleanup`.
   Every subvolume in `btrfs subvolume list /sysroot` mounted, as an unmounted
   filesystem is refused today.
2. **Updates held off** (decision 5): `systemctl stop uupd.timer` for the run
   at least.
3. **Baseline**, adding `deployments.txt`: `ostree admin status` (deployments,
   order, pins), the booted checksum and serial, each loader entry's
   `options` line, `/proc/cmdline`, `getenforce`, the image reference and
   digest, `bootupctl status` and `bootupd-state.json`'s digest, grubenv's
   keys, `lsattr -d` of each deployment root, and per subvolume the
   multi-link inode count and nlink histogram of regular files.
4. **Dump and backup** as today, through `backup_sources`: ~19G (11G sysroot
   counted once, 7.3G var, 238M home, 275M /boot, 14M ESP) onto mybig512, which
   has 399G free. bs1-backup records `ostree admin status` and the
   `/boot/loader` target before and after the backup and fails the run if
   they moved.
5. **To the stick, restore, boot**, unchanged: `load --erase /dev/nvme0n1
   --through restore`, then `--through close`.
6. **Verify**, extended, in this order:
   1. Stop `uupd.timer` and `uupd.service` (runtime only), then fail if uupd
      ran since boot (journal) or `rpm-ostree status --json` shows a staged
      or new deployment against `deployments.txt`.
   2. The bs1-manifest comparison, before any ostree command can add
      `repo/tmp` rows, with the etc mapping above.
   3. The required ostree check: the booted deployment's checksum **and
      serial** match the baseline. Both deployments are the same commit, so
      only the serial, the kargs (`bluetooth.disable_ertm=1`) and /etc tell .1
      from .0, and booting the rollback by mistake would pass `rpm -qa`.
   4. The extra checks (decision 7): `/` is a composefs overlay and
      `/sysroot` is on nvme0n1; `getenforce` is Enforcing; `ostree admin
      status` equals the baseline (list, order, pins); each BLS `options` line
      is byte-equal; the nlink histogram, multi-link count and `lsattr -d`
      equal; btrfs space one-sided as above; `ostree fsck` (it re-checksums
      every object, mode, owner and xattr, so it proves the objects; the
      link check proves the checkouts still use them; est. minutes);
      `rpm-ostree status`; `bootupctl status` says "At latest version" and
      `bootupd-state.json` is unchanged (so a first-boot ESP rewrite by
      bootloader-update shows); grubenv has no `boot_counter`; and
      `/var/log/bko-holt`.
7. **The rollback boots** (decision 6): boot the restored `.0` once and check
   its serial, kargs and hostname against the baseline, then boot `.1` again.

Known, not fixed here: `efi_entries` adds a second "Bazzite" entry beside
Boot0009 when the old one survives the erase. It predates ostree.

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| B1 | `make-fedora.sh --ostree`, `ks-ostree.cfg`, `storage-ostree.ks`; the second deployment; `kinoite44` on Chopin, copied to Cinqcent; the two-boot manifest diff; settle the *checks* above. **Done 2026-11-11:** built on Chopin, copied to Cinqcent (same SHA-256), booted on both; see "What B1 found" | 0.75 d |
| B2 | `dump`: detection, `SUPPORTED` row, holt/src/ostree.rs and `boot.ostree`, `backup_sources` and aliases, kernels, `prebuilt`, the BLS label, the btrfs FSROOT fix, Fedora's loader packages; a scenarios.rs fake composefs tree (mountinfo, `/run/ostree-booted`, `/boot/ostree/*`, `prepare-root.conf`, the `/etc` bind); unit tests; check fingerprint.py, test_vm_update.py, holttest1d/judge.py and backshot2's bs2lib against the new `mounted_subvolumes` and properties. **Done 2026-11-11:** see "What B2 found" | 1.5 d |
| B3 | `load`: mount stage without `ro`, the ostree marker, `ostree_boot` (atimes listed, note, consistency check, NVRAM, log and labels, the shared tail), the v1 plan problems; unit tests | 1.5 d |
| B4 | vm-mount's ostree mode (HoltTest1, vm-chroot); `ostree_paths.py` for bs1-manifest and compare.py; vm-update's ostree branch | 1 d |
| B5 | SticTest1 `kinoite44` entry and runs, both archivers | 0.25 d |
| B6 | HoltTest1: `kinoite44` in 1F, `ram-sizes.tsv`, the rollback boot in the restored-boot check, runs on both hosts | 0.5 d |
| B7 | Golden plan and Test2: `kinoite44`'s dump from B6's PASS run through corpus_build into the corpus (index.tsv, src.json, plan.txt), the `backup_sources` corpus test; `classes.json` (holt families F and uefi), holt/spec.json and guest/spec.json, estimates.tsv, plan.py's source and guest counts (with README and tests), re-blessed p2 golden fixtures, fingerprint.py's mirror of the new package names; re-blessed fedora* golden plans from the package-name fix are expected | 0.5 d |
| B8 | BackShot1: the table's fixes, `TREE`/`SNAP`, the preflight, backup's before/after check, baseline and verify additions, the rollback boot; `bs1-qemu.py` rehearsal on `kinoite44`; README.md's ostree steps (updates held, the staged refusal, the rollback boot, the new verify outputs) | 1.25 d |
| B9 | BackShot1 on the Thinkpad's Bazzite | 0.5 d |
| B10 | Docs: README supported lists, HoltTest1's table, `osrel.rs`'s comment, memory | 0.1 d |
| | *≈ 7.85 d in all; ~7.6 d if the extra verify checks are cut (decision 7)* | |

Each piece is committed on main when its tests pass. Nothing is pushed until
the user says so.

## Risks

- **The hardlink map in the rescue image's RAM.** ~240k inodes in the map on a
  7.4 GiB machine. If restore runs out, B9 waits on the rustic_core fork, as
  OpenBSD's restore does.
- **mountinfo order and the btrfs FSROOT bug.** If the Thinkpad's dump is
  already wrong about `root`'s properties, B2 fixes it for every btrfs system
  with a subdirectory bind. Golden plans don't move (they're planned from the
  stored src.json), but every fresh dump's `mounted_subvolumes` and
  properties do, and update/fingerprint.py, test_vm_update.py,
  holttest1d/judge.py and backshot2's bs2lib read those. B2 checks them.
- **Bazzite moves to 45 during the build.** `:stable` rebases to the next
  Fedora major, and Fedora 45 is due around now (est.). Then dump refuses
  `bazzite 45`, kinoite44 no longer matches the source, and kernel, ostree and
  bootupd versions shift. Decision 5.
- **A live backup across a deploy.** uupd or a manual `rpm-ostree upgrade`
  during the backup leaves /boot and the root at different deployments, or
  an orphan checkout. The preflight's staged check, backup's before/after
  check and the boot stage's consistency check catch it; they don't prevent
  it.
- **The restored system updates itself.** It comes back with whatever the
  source had enabled. If uupd catches up after the restore's first boot, it
  stages a deployment and updates flatpaks while verify runs. Verify stops it
  first and fails if it already ran.
- **The registry moves.** `:44` and Bazzite's `:stable` are tags; a rebuilt
  `kinoite44` gets newer content. The recorded digest says what was built.
- **Greenboot.** bootupd's `grub.cfg` falls back to the rollback (`default=1`)
  only when grubenv has `boot_counter` set and `boot_success=0`. The
  Thinkpad's grubenv has `boot_success=1` and no counter (greenboot sets one
  on an update), so no automatic fallback happens after the restore. Dump
  records grubenv's keys; verify's serial check still guards against booting
  `.0` by hand or by greenboot.
- **No container image ref** in the Thinkpad's repository (installed from
  containers-storage; the commit lives through `ostree/1/1/x`). A faithful
  restore keeps that as it is; whether the next `bootc upgrade` minds is the
  source's question, not bko's. *Check* that the restored system upgrades like
  the source would, but don't let it fail the run.

## Out of scope

- Fedora CoreOS, bootc `install to-disk` systems, and anything without an fstab
  `/` (root from `root=UUID=` alone, /boot by label, ESP unmounted), or
  without a separate `/boot` (refused in v1).
- composefs `verity` or `signed`, and fs-verity on objects (refused in v1).
- BIOS boot on ostree (refused in v1).
- LUKS of any kind and LVM on ostree (refused in v1), including TPM2 and
  FIDO2 tokens; `--new-uuids` on ostree.
- A `bazzite44` guest; Bluefin and Aurora until their os-release is seen.
- Re-pulling the image from the registry instead of backing up the
  repository.
- bootc's native composefs backend (`composefs: null` on the Thinkpad).
- Changes to the rescue image beyond `attr`, if it lacks it.

## Decisions for the user

**Approved by the user on 2026-11-11, with every recommended option.**

1. **Guests:** `kinoite44` alone, with a second deployment like the Thinkpad's
   (recommended: Bazzite's base, fits the RAM disk; the Thinkpad is the
   Bazzite test); or a `bazzite44` guest too (8-10 GB, rehearses only from
   /bkoscratch); or also Fedora CoreOS from its qcow2 for HoltTest only (needs
   the out-of-scope fstab-less path first).
2. **Which ostree systems `dump` accepts:** any system with ostree's markers whose
   ID and version are supported (Silverblue and Kinoite 44 as `fedora 44`, plus
   a `bazzite 44` row), refusing what v1 doesn't cover (LUKS of any kind, LVM,
   no separate /boot, verity, BIOS) and restoring non-bootupd systems verbatim
   with a plan note (recommended); or the same but refusing systems without
   `bootupd-state.json`; or only Bazzite and `kinoite44`, with other ostree
   systems refused until tested.
3. **kinoite44's image on two hosts:** build once on Chopin from the `:44`
   tag's digest, record it, `bootc switch` to `:44`, copy the image to
   Cinqcent once and ship update overlays after (recommended: the hosts stay
   byte-identical, as for every guest); or build on each host from the same
   `@sha256:` digest (two ~3 GB pulls, the same result until either host
   updates).
4. **Who owns the backup list:** `dump`'s `storage.backup_sources` for
   BackShot1, with `load` and vm-mount reading fstab and a corpus test that
   the two agree (recommended: one rule, tested); or bs1 alone, in shell, with
   nothing tying it to load.
5. **Updates on the Thinkpad until BackShot1 is done:** you hold them
   (`systemctl disable --now uupd.timer`, or `ujust` if Bazzite has a toggle)
   from B1 until B9 passes, and re-enable them on the restored system after
   (recommended: Bazzite stays 44 while the code is built for it, the
   deployment list can't move, and the backup and restore have it disabled
   alike); or stop it for the run only and, if `:stable` moves to 45 first,
   add a `bazzite 45` row and a `kinoite45` guest (est. +0.75 d). Either way
   the preflight checks VERSION_ID, the staged refusal and backup's
   before/after check stay, and verify stops uupd first.
6. **The restored rollback in BackShot1's PASS:** boot `.0` once from the GRUB
   menu at the Thinkpad's console, with `grub2-reboot` used only in the
   rehearsal and HoltTest1 (recommended: nothing written on your machine but
   by GRUB itself); or `grub2-reboot 1` over SSH (writes `next_entry` to the
   restored grubenv, which GRUB clears on that boot); or not required, the
   default boot only. `rpm-ostree rollback` twice isn't offered: it rewrites
   the deployment order and the bootloader version on your machine. If the
   *check* finds `.0` lacks adopt's host key and hostname, the console is the
   only way, and that boot is checked by eye (serial, kargs and hostname from
   `rpm-ostree status` on screen).
7. **Extra ostree checks in bs1-verify,** beyond the required serial check:
   all of step 6.4 (recommended: each is a line or two, and together they show
   ostree booted the way it did before); or only `ostree fsck` and the
   hardlink and `+i` comparisons (est. -0.25 d).
8. **HoltTest1's group:** **1F** (recommended: it's Fedora); 1Obscure; or a new
   **1Ostree**.
