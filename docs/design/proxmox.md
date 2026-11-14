# Proxmox VE support

2026-11-14. Asked for by the user: support Proxmox VE 9, the Debian-based
virtualisation host, the way PikaOS, AnduinOS and KDE neon were added. The
user approved this spec on 2026-11-14 with every recommended decision (the
last section).

**Conventions.** est. marks an estimate. *Check* marks a fact the sources
don't settle, to be settled on the first guest (P1) before the code that
depends on it. Proxmox's facts come from its press release and downloads page
for 9.2 (seen through a web search, 2026-11-14: Proxmox VE 9.2, released
2026-05-21, Debian 13.5, Linux 7.0, ZFS 2.4, QEMU 11.0, ISO `9.2-1` ~1.7 GB
with a SHA-256 published beside it), its wiki's "Automated Installation"
page, and what is known of `proxmox-boot-tool` (package
`proxmox-kernel-helper`) and the installer's ZFS layout from 7.x and 8.x.
Nothing has been downloaded or installed yet. Code references are to local
`main` at 1edb4b5 plus the uncommitted tree.

## Summary

- Proxmox VE 9 becomes a supported system for bko-holt and both archivers,
  with two UEFI QEMU guests on ZFS root (`proxmox9`, one disk;
  `proxmox9-mirror`, a two-disk mirror with an ESP on each disk), a SticTest1
  entry each, a place in HoltTest1 (**1D**, Decision 6) and the Test2
  registrations.
- It **is** Debian 13 to `osrel.rs`: Proxmox ships Debian's `base-files`, so
  `/etc/os-release` says `ID=debian`, `VERSION_ID="13"` (*check*). dump
  already accepts it. What it isn't is Debian's boot stack: its own kernel
  (`proxmox-kernel-K-pve-signed`), and on ZFS root **`proxmox-boot-tool`**,
  which owns one or more **unmounted** ESPs, listed by volume ID in
  `/etc/kernel/proxmox-boot-uuids`, and copies every kernel and initrd onto
  each of them.
- That is the whole problem: today's dump finds ESPs only in `mountinfo`
  (`report.rs:1276`), so on Proxmox `boot.esps` is empty, the ESPs aren't
  backup sources, and load would format them and leave them empty. The
  restored system wouldn't boot.
- The fix (Decisions 2-4): dump records `boot.proxmox_boot` (the UUID list,
  each ESP's partition and mode, read by a private read-only mount); load
  formats each ESP with its volume ID (so the UUID list stays true), then in
  the chroot runs `proxmox-boot-tool init` on each and `proxmox-boot-tool
  refresh` once: the tool that keeps them in sync on the source rebuilds
  them on the copy.
- `/etc/pve` is a FUSE mount (pmxcfs) of `/var/lib/pve-cluster/config.db`:
  the database comes back with `/`; the mount must not be taken for a backup
  source.
- ~3.9 d (est.), all on Chopin and Cinqcent (ZFS hosts), none of the user's
  time but the OKs.

## Why

- **ESPs nobody mounts.** No other supported system boots from file systems
  that aren't in fstab. bko-holt has to see them, and has to know not to
  restore them from the archive but to have their owner rebuild them.
- **More than one ESP, kept equal.** On a mirror the firmware may boot either
  disk; both must carry the same loader, kernels and entries, as on the
  source. Ubuntu's `ubuntu26-zfsmirror` is BIOS on md, so nothing tests this
  today.
- **The most-asked-for host OS not yet covered**, and the one whose users
  most often have ZFS root.

## What Proxmox VE 9 is (as far as bko cares)

| | Proxmox VE 9.2 | Nearest supported system |
|---|---|---|
| Release | 9.2 (2026-05-21), point releases about twice a year; 9.x on Debian 13 "trixie"; ISO `proxmox-ve_9.2-1.iso`, ~1.7 GB, SHA-256 on the downloads page (*check* for a signed `SHA256SUMS`) | Debian 13 |
| os-release | Debian's: `ID=debian`, `VERSION_ID="13"`, `PRETTY_NAME="Debian GNU/Linux 13 (trixie)"` (*check*). Proxmox is told apart by packages: `proxmox-ve`, `pve-manager` (whose version is the PVE release) | `debian13` |
| Kernel | `proxmox-default-kernel` → `proxmox-kernel-7.0` → `proxmox-kernel-7.0.x-y-pve-signed`; files `/boot/vmlinuz-7.0.x-y-pve`, `initrd.img-…-pve` (initramfs-tools) (*check* names). Conflicts with Debian's `linux-image-amd64` | none |
| ZFS | OpenZFS 2.4 in the kernel package, `zfsutils-linux` and `zfs-initramfs` from Proxmox's archive; `root=ZFS=rpool/ROOT/pve-1 boot=zfs` in `/etc/kernel/cmdline` | `ubuntu26-zfsmirror` (2.4 pool), `debian13-zfs` (2.3) |
| ZFS layout (installer) | Each disk GPT: p1 BIOS boot (1 MiB, EF02), p2 **ESP** (1 GiB vfat, unlabelled, *check* the size), p3 ZFS. Pool `rpool` (`raid0` one disk, `raid1` mirror; ashift 12; `compression=on`), datasets `rpool/ROOT` (`canmount=off`), `rpool/ROOT/pve-1` (`/`), `rpool/data` (VM and CT storage, `/rpool/data`), `rpool/var-lib-vz` (`/var/lib/vz`, *check* it's still made). No swap. `/etc/fstab` has only `proc` (*check*) | `debian13-zfs` |
| Boot (UEFI, no Secure Boot) | `proxmox-boot-tool` in **systemd-boot** mode: each ESP holds `EFI/systemd/systemd-bootx64.efi`, `EFI/BOOT/BOOTX64.EFI`, `loader/loader.conf`, `loader/entries/proxmox-K-pve.conf` and `EFI/proxmox/K-pve/{vmlinuz,initrd.img}`, written by `proxmox-boot-tool refresh` (run by the kernel's postinst and `/etc/initramfs/post-update.d/proxmox-boot-sync`). `bootctl install` made one NVRAM entry "Linux Boot Manager" (*check*: one, or one per ESP) | NixOS systemd-boot (kernels on the ESP) |
| Boot (BIOS, or Secure Boot) | `proxmox-boot-tool` in **GRUB** mode: on BIOS `grub-install --target=i386-pc` with `/boot/grub` on each ESP; with Secure Boot (8.1+) shim and signed GRUB on each ESP. A non-ZFS (ext4/XFS on LVM) install uses plain GRUB with `/boot/efi` mounted and no `proxmox-boot-tool` | `debian13` (GRUB) |
| `proxmox-boot-tool` state | `/etc/kernel/proxmox-boot-uuids` (one vfat volume ID a line, e.g. `1A2B-3C4D`), `/etc/kernel/cmdline`, optional `/etc/kernel/proxmox-boot-pin`, `/etc/kernel/proxmox-boot-manual-kernels`; it mounts each ESP under `/var/tmp/espmounts/UUID` only while it works | none |
| `/etc/pve` | **pmxcfs**, FUSE (`/dev/fuse on /etc/pve type fuse`), served by `pve-cluster.service` from `/var/lib/pve-cluster/config.db` (SQLite). Holds `storage.cfg`, `nodes/HOST/`, the cluster's SSL keys (`pve-root-ca.key`, `priv/`). Needs the hostname to resolve to a non-loopback address in `/etc/hosts` | none |
| Packages | apt, deb822 sources: `debian.sources`, `pve-enterprise.sources` (401 without a subscription), `ceph.sources`; the free `pve-no-subscription` repo is added by hand | Debian |
| Install | the ISO's installer (GUI, TUI, or **automated**: `answer.toml`, from the ISO, a partition labelled `PROXMOX-AIS`, or HTTP, after `proxmox-auto-install-assistant prepare-iso`) | none so far |

## What already works, and what doesn't

**bko-holt `dump`:**

- `osrel.rs`: the `("debian", "13", "Debian 13")` row accepts it (*check*
  the installed os-release). Recommended (Decision 5): dump also records
  `os.product = "Proxmox VE 9.2"` from `pve-manager`'s version, so the plan
  header and the report name it, and load refuses a `proxmox_boot` source
  whose `pve-manager` major isn't 9 (Proxmox 8 on Debian 12 has the same
  tool and probably works, but isn't claimed).
- **ESPs** (`report.rs:1276`): only vfat file systems in `mountinfo`. On
  Proxmox there are none, so `boot.esps` is `[]`, `boot.loader` comes only
  from BootCurrent (`\EFI\systemd\systemd-bootx64.efi`, systemd-boot, right)
  and `systemd_boot_json` finds no entries. Needed: a new
  `boot.proxmox_boot` object when `/etc/kernel/proxmox-boot-uuids` exists and
  `proxmox-kernel-helper` is installed:
  - `uuids`: the file's lines in order;
  - `esps`: for each UUID, the partition it resolves to (by-uuid, via
    `ctx.resolve`), and, from a **private read-only mount** (`mount -o
    ro,noexec,nodev` under `/run/bko-holt/esp-UUID`, unmounted at once; the
    way `proxmox-boot-tool` itself mounts them), its `mode` (`systemd-boot`
    if `EFI/systemd/systemd-bootx64.efi`, `grub-bios` if `grub/i386-pc`,
    `grub-efi` if `EFI/proxmox/grubx64.efi`; *check* the shim layout) and
    its `contents` as `boot.esps[].contents` lists them today;
  - `cmdline` (`/etc/kernel/cmdline`), `pin`, `manual_kernels`;
  - a UUID that resolves to nothing is recorded as such (`proxmox-boot-tool
    status` warns of it too) and load treats it as a plan problem.
  The same ESPs also go into `boot.esps` with `mounted_at: null` (Decision
  2), so the code that reads `boot.esps` (shim id, NVRAM entries, judge)
  sees them.
- `PACKAGES` (`boot.rs:214`) gains `proxmox-kernel-helper`,
  `proxmox-default-kernel`, `proxmox-ve`, `pve-manager`, `zfs-initramfs`
  (*check* how a versioned `proxmox-kernel-K-pve-signed` is taken, as
  PikaOS's `linux-image-K-pikaos` was); `testvm/update/fingerprint.py`
  mirrors it.
- `kernels_json()`: `/boot/vmlinuz-K-pve` and `initrd.img-K-pve` are
  Debian's names: works (*check*).
- **pmxcfs**: `/etc/pve` is `fuse`, source `/dev/fuse`. `mounts.rs:66`'s
  pseudo list has `fuse.lxcfs` (also on Proxmox: `/var/lib/lxcfs`) but not
  plain `fuse`. Needed: the pmxcfs mount (target `/etc/pve`, type `fuse`,
  `pve-cluster` installed) is skipped, with a note that its data is
  `/var/lib/pve-cluster/config.db` on `/`; a generic `fuse` stays as today
  (*check* what dump says of it now).
- ZFS: `zfs_json` already records `rpool`'s features, properties and
  datasets; `rpool/data`'s zvols (VM disks) are recorded.

**bko-holt `load`:**

- **Partition and format:** each disk's three partitions come back; the
  BIOS-boot partition is left empty (as on every GPT+GRUB-BIOS guest); each
  ESP is made by `mkfs_fat` (`commands.rs:1497`) with `-i` = its volume ID,
  so `/etc/kernel/proxmox-boot-uuids` stays true. *Check* that load formats
  a vfat partition nothing mounts (if it skips it today, the
  `proxmox_boot.esps` list makes it).
- **Assemble:** `zpool create … rpool [mirror] …` with the source's
  features, then `zpool reguid -g` (as `ubuntu26-zfsmirror`'s plan). The
  image's OpenZFS must know every 2.4 feature: `plan.rs`'s new
  `zfs_problems` (ZFS-features session) checks it. zvols are made empty with
  today's note; the note gains "Proxmox: VM disks in rpool/data are vzdump's
  or Proxmox Backup Server's to back up".
- **Restore:** the ESPs aren't backup sources and get nothing from the
  archive; `/etc/pve` is an empty directory (pmxcfs mounts over it);
  `config.db` comes back with `/`.
- **Boot stage, a new arm before `("systemd-boot", true)`** when the layout
  has `boot.proxmox_boot` (Decision 3). Today's arm would run `bootctl
  install --no-variables` against an ESP mounted nowhere, then
  `gen_systemd_boot`. Instead, in the chroot (with `/sys/firmware/efi/efivars`
  bound, as for bootctl today):
  1. `update-initramfs -u -k all` as for Debian (its post-update hook calls
     `proxmox-boot-tool refresh`, which finds the ESPs empty and
     uninitialised; *check* it fails soft, else the hook is held with
     `PROXMOX_BOOT_TOOL_SKIP`… *check* the knob, or the order is swapped);
  2. for each ESP in `uuids` order: `proxmox-boot-tool init
     /dev/disk/by-uuid/U` for `systemd-boot` mode, `… init … grub` for the
     GRUB modes (the mode is the source's, not the rescue system's
     firmware's: hence not `reinit`, which picks by `/sys/firmware/efi`);
  3. `proxmox-boot-tool refresh` once: kernels, initrds and entries on every
     ESP, the source's pin and manual kernels honoured;
  4. `/etc/kernel/proxmox-boot-uuids` written back as dumped (`init` adds a
     line per UUID; *check* it doesn't duplicate one already there);
  5. a note: "Proxmox: proxmox-boot-tool rebuilt N ESPs (systemd-boot);
     their old content isn't in the archive".
  `init` runs `bootctl install`, which makes its own "Linux Boot Manager"
  NVRAM entry; `efi_entries()` then recreates the source's. *Check* the
  result on P1: one entry per source entry, no duplicate (if one, the arm
  passes `--no-variables` through `SYSTEMD_RELAX_ESP_CHECKS`… or
  `efi_entries()` drops an entry identical to one already in NVRAM).
- With `--removable` / `--no-nvram`: `EFI/BOOT/BOOTX64.EFI` is written by
  `bootctl install` anyway: nothing more.
- Secure Boot (shim + GRUB through `proxmox-boot-tool`): the arm can run
  `init … grub` the same way, but no guest tests it: refused by name in the
  plan unless Decision 7 says otherwise.
- A Proxmox install on LVM-thin (ext4/XFS root, `/boot/efi` mounted, plain
  GRUB) has no `proxmox_boot` and takes today's Debian GRUB path; it isn't
  a guest (Out of scope).
- `update_grub()`'s id list: Proxmox is `debian` already; untouched.
- **ZFS boot:** `zfs-initramfs` imports `rpool` by name at boot; the plan
  exports it at the end and writes the source's `/etc/hostid` back, as for
  `debian13-zfs` (*check* the same lines appear).

**`redact.rs`:** the installer's ESPs and pool have no free-text labels.
The guests' hostname (`pve`, the installer's default, *check*) joins
`GENERIC_HOSTS`; `proxmox` joins `release_label()`'s `SYSTEMS` (the ISO
label `PVE`, *check*). `/etc/pve`'s keys aren't in a dump. These lines are
shared with the other new-distro sessions (see "Shared lines").

**The rescue image:** no change ([[one-kernel-rescue]]): the Linux image's
OpenZFS already creates 2.4 pools (`ubuntu26-zfsmirror`'s plan has
`physical_rewrite` and `block_cloning_endian`).

## Guests

Two, both UEFI, Secure Boot off (`--uefi --no-secure-boot`), ZFS root,
systemd-boot through `proxmox-boot-tool`, from `proxmox-ve_9.2-1.iso`
(Decision 8), its SHA-256 pinned in the script, the ISO and the prepared ISO
in `/bkoscratch/rebuild-inputs/GUEST/` on Chopin and Cinqcent. `-cpu host`,
`MEMORY=4096` (the installer asks for 2 GiB; ZFS's ARC wants more), 2
vCPUs, no nested virtualisation (no VMs are run).

| Guest | Layout | What it covers |
|---|---|---|
| `proxmox9` | one 24G disk (*check* the installer's minimum for ZFS), `raid0`: BIOS boot, 1 GiB ESP, `rpool`; one UUID in `proxmox-boot-uuids` | `boot.proxmox_boot` found and recorded, the ESP rebuilt by `proxmox-boot-tool`, ZFS 2.4 root, pmxcfs and `config.db`, `rpool/data` with one small zvol and one CT-style subvol dataset (Decision 9) |
| `proxmox9-mirror` | two 24G disks, `raid1`: the same partitions on each, `rpool` a mirror, **two ESPs**, two UUIDs | both ESPs rebuilt and **byte-equal in their kernels and entries**; HoltTest1's boot stage boots the restored pair, then each disk **alone** (the other detached, the pool degraded): the test that the sync was kept |

How they are built (Decision 1):

1. **The automated installer, answer file on a partition** (recommended).
   A new `testvm/make-proxmox.sh FLAVOR WORK_DIR`:
   - once per ISO: `proxmox-auto-install-assistant prepare-iso ISO
     --fetch-from partition` → `proxmox-ve_9.2-1-auto-from-partition.iso`,
     pinned too. The assistant is a Debian package in Proxmox's
     `pve-no-subscription` repo; it is fetched into a throwaway Debian 13
     chroot on Chopin, apt checking it against Proxmox's release key
     (`proxmox-release-trixie.gpg`, fingerprint pinned in the script), and
     run there. Nothing from it is installed on a host.
   - per build: a 16 MiB vfat image labelled `PROXMOX-AIS` holding
     `answer.toml`, attached as a second drive (removed after the install):
     ```toml
     [global]
     keyboard = "en-us"
     country = "us"
     fqdn = "pve.test"
     mailto = "root@pve.test"
     timezone = "UTC"
     root-password-hashed = "…"      # generated per build, in vms/GUEST only
     root-ssh-keys = ["…"]            # vms/GUEST/id_ed25519.pub
     reboot-mode = "power-off"        # QEMU exits when the install is done
     [network]
     source = "from-dhcp"
     [disk-setup]
     filesystem = "zfs"
     zfs.raid = "raid0"               # "raid1" for proxmox9-mirror
     disk-list = ["vda"]              # ["vda", "vdb"]
     [first-boot]
     source = "from-iso"              # *check* it works with fetch-from partition
     ordering = "fully-up"
     ```
     (key spellings per the wiki of the ISO's version: *check*, examples
     differ between `disk-list` and `disk_list`).
   - the first-boot script (in the repo, no secrets): deb822 sources to
     `pve-no-subscription` with `pve-enterprise.sources` and Ceph's
     enterprise source disabled; `console=ttyS0,115200n8` appended to
     `/etc/kernel/cmdline` and `proxmox-boot-tool refresh`; the `tester`
     user; `rpool/data/vm-100-disk-0` (a 64 MiB zvol) and
     `rpool/data/subvol-101-disk-0` (a file system dataset with a few
     files) as Decision 9 says; `openssh-server` is there by default; prints
     BACKOTTER-INSTALL-OK on ttyS0, then powers off.
   - then boots the disk alone, checks SSH and `proxmox-boot-tool status`
     (one, or two, ESPs "configured with: uefi"), and converts to qcow2.
   Gives the installer's real partitions, pool properties, datasets and
   `proxmox-boot-tool` state, and is rebuildable by `rebuild-one.sh`.
2. **From the host, as `make-debian-zfs.sh`**: debootstrap trixie onto a
   hand-made pool, then Proxmox's packages from its repo, then
   `proxmox-boot-tool format`/`init`. Proxmox documents this path ("Install
   Proxmox VE on Debian"), but not for ZFS root; the layout and properties
   would be ours, not the installer's. +0.5 d, drifts.
3. **One GUI install kept as a pinned image**: not rebuildable, not
   recommended.

Each guest gets: `rebuild-one.sh` lines (and the ISO check), the rebuild
inputs on both hosts, `holttest1d/ram-sizes.tsv` rows (est. D 12, MEM 4;
measured in P6), `classes.json` entries (in the batched Test2 pass), both
hosts by `vm-store` publish, a GuestTest1 grade.

**vm-update:** `in-guest.sh`'s `Linux.apt` section works as on Debian
against `pve-no-subscription` (*check* on P1's `--try`): a new
`proxmox-kernel-K-pve-signed` triggers `proxmox-boot-tool refresh`, which
updates every ESP; old kernels go by `apt autoremove` (Proxmox's
`proxmox-boot-tool kernel` keeps the running and the newest two, *check*).
`fingerprint.py`'s `LINUX_KINDS`, `pkgset.sh`, the `vm-update` header name
it. GuestTest2: a `K-pve` rule in `t2lib/guest_judge.py` (`KERNELS_MAX`)
and a new check that **every ESP in `proxmox-boot-uuids` holds the same
kernels and entries** (read with `vm-mount --ro`); `guest/known-lines.json`
lines for Proxmox's sources and pmxcfs's mount.

**Host ZFS:** HoltTest1 mounts source and copy on the host with `vm-mount
--ro`, whose `mount_zfs_root` needs a host `zpool` that can import a 2.4
pool read-only. `ubuntu26-zfsmirror` (2.4) passes on Chopin and Cinqcent, so
it can (*check* on P1 with `vm-mount --ro proxmox9`). Only these two hosts:
both guests go in `needs_host_zfs`; Cage and Copland never get their cells.

## Tests

**SticTest1** (`testvm/stictest1/proxmox9`, `proxmox9-mirror`): one
`inner.sh`, `fixture.sh` and `judge.py` for both, from `debian13-zfs`'s
(datasets, properties) and `ubuntu26-zfsmirror`'s (a mirror on the scratch
disks), the scratch pool made with the guest's own features. Both archivers,
both hosts. `stic/manifests.json`, `limits.json`, and `check-manifests.py`'s
count up by two (whoever lands last sets the number to the entries present).

**HoltTest1** (Decision 6: **1D**): both guests join 1D beside `debian13`,
`debian13-zfs`, `debian13-stack` (they are Debian 13 to bko-holt, and need
the same ZFS hosts as `debian13-zfs`); `classes.json`'s D group, the README's
1D row (Debian 13 and Proxmox VE 9), HoltTest1All's members. 1D grows from
six runs to ten. Runs on **Chopin and Cinqcent** each, in /mnt/bko-ram under
`cmp-run.sh` reservations (est. `--reserve 12 4`), finished guests moved to
disk. judge.py:
- the ESPs aren't backup sources, so `judge_fs` compares them after the
  boot stage by **content**, not bytes: on every ESP the same set of
  `EFI/proxmox/K-pve/*` and `loader/entries/proxmox-*.conf` as the source's
  ESP of the same UUID, `vmlinuz` byte-equal, `initrd.img` allowed to
  differ (rebuilt), `loader/random-seed` and `loader/entries.srel` allowed;
  and, on the mirror, both copy ESPs equal to each other;
- `/etc/kernel/proxmox-boot-uuids` byte-identical;
- `boot/initrd.img-*` as for Debian; `var/lib/pve-cluster/config.db*`
  allowed to differ in mtime/size only after the boot (pmxcfs writes on
  start); `/etc/pve`'s files compared after boot from the running copy (its
  `storage.cfg`, `nodes/pve/`, `priv/authkey.key` hash);
- the boot stage boots the copy alone; on `proxmox9-mirror` twice more, from
  each disk alone (ht1.sh's boot stage learns a `--boot-each-disk` for
  multi-ESP sources; the pool comes up DEGRADED and that is the pass).
- `ram-sizes.tsv`: rows measured in P6.

**Test2:** golden plans `holt/tests/corpus/proxmox9/` and
`proxmox9-mirror/` (`src.json`, `plan.txt`, `index.tsv` rows, the corpus
README) from `bko-holt dump` of each guest, made by this session.
Registrations, in the sibling sessions' **one batched Test2 pass** (as
PikaOS's): `classes.json` (the D family, `uefi`, `needs_host_zfs`),
`holt/spec.json`, `guest/spec.json`, `estimates.tsv`, `t2lib/plan.py`'s
counts, `tests/fixtures/p2/world.py`, re-blessed goldens. Done when two
corpus builds have the same sha256, leak scans are 0 on both hosts and the
Mac, no other plan changes, and Test2's own tests pass.

**BackShot1:** not proposed. The Thinkpad as a Proxmox host is plausible
but has one disk, so it would only repeat `proxmox9`; later, through
BackShot1All if the user wants it.

## Shared lines (with Guix, Leap 16, Vanilla OS, Slackware, Solus, OpenIndiana, Kali)

Each session's own rows, re-read first, and a line appended to the shared
notes before editing: `boot.rs` `PACKAGES` and `fingerprint.py`;
`mounts.rs`'s pseudo list (pmxcfs; Vanilla OS or Solus may touch it too);
`redact.rs` `GENERIC_HOSTS` and `SYSTEMS`; `classes.json`, the holttest1d
README's 1D row (**Kali is Debian-based too**: if its session also picks
1D, one edit at a time) and `check-manifests.py`'s count. Proxmox touches
neither `osrel.rs`'s `SUPPORTED` (Debian 13's row) nor `update_grub()`'s
list nor `make-from-live.sh`. Its own: `report.rs`'s ESP discovery, the new
`boot.proxmox_boot`, load's new arm, `ht1.sh`'s `--boot-each-disk`.

## What P1 found

2026-11-14, `proxmox9`'s first build on Chopin (`make-proxmox.sh`, 346 s:
165 s install, 90 s first boot), from `proxmox-ve_9.2-1.iso` (1,706,178,560
bytes, SHA-256 `4e88fe41...6c2c6c`, in Proxmox's `SHA256SUMS`; the `.asc`
holds two signatures, by the trixie key `24B30F06...0BFE778E` and the
bookworm key `F4E136C6...639E0C39`, both good). The assistant is
`proxmox-auto-install-assistant` 9.2.8 from `pve-no-subscription` (its
`.deb` checked through the signed trixie `InRelease`), unpacked, never
installed.

- **answer.toml:** 9.2.8's `validate-answer` takes the spec's sketch as
  written (kebab-case keys, `reboot-mode = "power-off"`, `[first-boot]
  source = "from-iso"`, `ordering = "fully-up"`). Underscore keys are
  deprecated: it warns, prints "Error: Found issues" and still exits 0, so
  the script reads its words. `--on-first-boot` embeds the script in the
  prepared ISO, so that ISO is named by the script's hash.
- **os-release:** Debian's, `ID=debian`, `VERSION_ID="13"`,
  `DEBIAN_VERSION_FULL=13.5`. `/etc/debian_version` 13.5.
- **Kernel:** `7.0.2-6-pve`, `/boot/vmlinuz-7.0.2-6-pve`,
  `initrd.img-7.0.2-6-pve` (initramfs-tools), memtest86+ on /boot and the
  ESP. `/boot/efi` exists, empty, not mounted; `/boot/grub` exists.
- **Disk:** GPT; p1 BIOS boot (sectors 34-2047, 1007 KiB), p2 **ESP 512
  MiB** (not 1 GiB), vfat, no label; p3 ZFS, the rest. `/etc/fstab` has
  only `proc`. No swap. `/etc/hostid` written.
- **ZFS:** `zfs-2.4.2-pve1`. `rpool`: ashift 12, autotrim off, no
  compatibility, every feature up to `physical_rewrite` enabled
  (`block_cloning`, `block_cloning_endian` active), `bootfs
  rpool/ROOT/pve-1`. Local properties: `rpool` `compression=on`,
  `atime=on`, `relatime=on`, `sync=standard`; `rpool/ROOT/pve-1`
  `mountpoint=/`, `acltype=posix`; `rpool/var-lib-vz` **exists**,
  `mountpoint=/var/lib/vz`. `rpool`, `rpool/ROOT` and `rpool/data` are
  mounted at their default paths (`canmount=on`). Ours:
  `rpool/data/vm-100-disk-0` (64 MiB zvol), `rpool/data/subvol-101-disk-0`
  (`acltype=posix`, `xattr=sa`, `refquota=1G`, files owned by 100000).
- **proxmox-boot-tool:** `/etc/kernel/proxmox-boot-uuids` is one line,
  `DC2E-D3A8`: the vfat ID **upper case** with its dash. No `pin`, no
  `manual-kernels`. `status`: "configured with: uefi (versions:
  7.0.2-6-pve)". Its post-update hook is `/etc/kernel/postinst.d/zz-proxmox-boot`
  (`refresh` runs it). The ESP holds `EFI/BOOT/BOOTX64.EFI` and
  `EFI/systemd/systemd-bootx64.efi` (the same 124,416 bytes),
  `EFI/memtest86+x64.efi` and `memtest86+x64.efi`, `EFI/Linux/` (empty),
  `EFI/proxmox/K/{vmlinuz,initrd.img}-K`, `loader/loader.conf` (`timeout
  3`, `default proxmox-*`), `loader/entries/proxmox-K.conf` and
  `memtest86+x64.efi.conf`, `loader/entries.srel`, `loader/keys/`,
  `loader/random-seed`. 91 MiB used.
- **NVRAM:** one entry, `Boot0001* Linux Boot Manager`
  `HD(2,GPT,…)/File(\EFI\systemd\systemd-bootx64.efi)`, first in BootOrder.
  With fresh variables the firmware boots the fallback `EFI/BOOT/BOOTX64.EFI`
  (systemd-boot too).
- **/etc/kernel/cmdline:** `root=ZFS=rpool/ROOT/pve-1 boot=zfs`, plus the
  installer's console words copied from its own command line. Booting the
  installer by QEMU's `-kernel`/`-initrd` (for a serial log) also gave
  `initrd=initrd`; `first-boot.sh` now drops it.
- **pmxcfs:** `/dev/fuse on /etc/pve type fuse
  (rw,nosuid,nodev,relatime,user_id=0,group_id=0,default_permissions,allow_other)`;
  `/var/lib/pve-cluster/config.db` with `-wal` and `-shm` beside it.
  `/var/lib/lxcfs` is `fuse.lxcfs`.
- **Host name:** the answer's `fqdn = "pve.test"` gives `/etc/hostname`
  `pve` and `/etc/hosts` `10.0.2.15 pve.test pve` (QEMU's user network
  address).
- **apt:** deb822 only: `debian.sources`, `pve-enterprise.sources`,
  `ceph.sources` (enterprise `ceph-squid`); `first-boot.sh` sets
  `Enabled: no` on the enterprise ones and writes `proxmox.sources`
  (`pve-no-subscription`); `apt-get update` is clean.

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| P1 | Fetch `proxmox-ve_9.2-1.iso` (or the newest 9.x then) into rebuild-inputs on Chopin (the user's OK first), check and pin its SHA-256; the assistant in a throwaway chroot, the prepared ISO pinned. `make-proxmox.sh proxmox9` by Decision 1, `rebuild-one.sh`, copy to Cinqcent (the same SHA-256), `vm-update --try`, `vm-mount --ro`. Settle every *check* (os-release, kernel names, partition sizes, datasets, NVRAM entries, pmxcfs's mount line, `init`'s UUID handling) and write "What P1 found" here. Done when it boots alone on both hosts, SSH works, `proxmox-boot-tool status` is clean and `vm-update --try` is ok | 1.25 d (+0.5 d on path 2) |
| P2 | bko-holt (High-effort subagent): `boot.proxmox_boot` (the UUID list, the read-only ESP mounts, modes, contents, cmdline), the ESPs in `boot.esps` with `mounted_at: null`, `os.product`, `PACKAGES` and `fingerprint.py`, pmxcfs skipped with its note; load's proxmox arm (format with volume IDs, `init` per ESP by mode, `refresh`, the UUID file, the NVRAM de-duplication), the PVE-9 gate, the GRUB/Secure Boot refusal, the zvol note; `redact.rs`'s words; a Proxmox fake tree (one and two ESPs) in `holt/src/scenarios.rs`. Done when `cargo test` passes and the scenario's plan has one `mkfs.fat -i` and one `proxmox-boot-tool init` per ESP, one `refresh`, no `bootctl install --no-variables`. **Done 2026-11-14** (uncommitted): `boot.rs` `proxmox_boot_json` (ESPs read by a private ro mount through `Sys`, modes, contents, `os.product`), `mounts.rs` `is_pmxcfs`, `commands.rs` `proxmox_boot_cmds` as the first loader arm (init per ESP, refresh, the UUID file written back, the refusals, `--no-nvram` through a private mount namespace, `efi_entries` skipping bootctl's own entry), `layout.rs` names the product, `redact.rs` words, `scenarios.rs` `proxmox(mirror)` and three tests; holt's tests pass but `golden_plans`' one pre-existing miss (ubuntu24-luks/-uefi cells without dumps) | 1 d |
| P3 | `proxmox9`'s golden plan (`holt/tests/corpus/proxmox9`, its `index.tsv` row); Test2 registrations to the batched pass | 0.3 d |
| P4 | `proxmox9-mirror`: the build (`raid1`, two disks), its golden plan and `index.tsv` row, `rebuild-one.sh`. Done as P1 and P3 | 0.4 d |
| P5 | SticTest1 entries and runs, both guests, both archivers, both hosts, 0 FAIL | 0.35 d |
| P6 | HoltTest1 runs (1D) on Chopin and Cinqcent with the judge rules above, ht1.sh's `--boot-each-disk`, the boot alone and per disk; RAM peaks in `ram-sizes.tsv`. Done when all four runs PASS with 0 fsmeta and 0 dump differences | 0.5 d |
| P7 | Docs: holt/README.md's supported list ("Debian 13, and Proxmox VE 9 on it") and its tests, `layload.md`'s boot stage (the proxmox-boot-tool arm), HoltTest1's 1D row and HoltTest1All, SticTest1's README, GuestTest's ESP-sync check; memory (holttest1d index, a proxmox-support note) | 0.1 d |
| | *≈ 3.9 d* | |

P2 can start beside P1 from the facts above, but its golden plan (P3) waits
for P1's dump. Each piece is committed on main when its tests pass and the
user says to commit; nothing is pushed until the user says so. Source-code
changes go to High-effort subagents ([[effort-max-when-it-pays]]).

**Costs** (est.): P1 and P4 a 15-30 min install each on Chopin
(`--reserve 12 4`; sparse disks, ~3 GiB written), the copy; qcow2 ~3-4 GiB
a guest a host (Proxmox has no desktop), the ISOs ~3.5 GB a host. P5 and P6
about 1.5 h of queue a host.

## Risks

- **Rebuilding instead of restoring the ESPs.** If `proxmox-boot-tool init`
  behaves differently in a chroot under a rescue kernel (it checks
  `/sys/firmware/efi`, the running kernel's version, `/proc/mounts`), the
  ESPs could come out without the source's kernels. HoltTest1's per-ESP
  content check and the boot-each-disk stage show it. The fallback
  (Decision 3's second option) is to dump the ESPs' files and copy them back
  byte for byte.
- **The automated installer's details** (key spellings, `[first-boot]` with
  a partition answer, `reboot-mode`) change between point releases. P1
  settles them for 9.2; a newer ISO may need the answer file touched.
- **ZFS 2.4 on the hosts.** If a host's OpenZFS can't import the pool
  read-only, its HoltTest1 cells can't run there. `ubuntu26-zfsmirror` says
  they can today.
- **pmxcfs while the backup runs.** `config.db` is SQLite written by a
  running daemon; on an idle single node it changes seldom, and the judge
  checks `/etc/pve` after boot. A busy cluster node isn't claimed (Out of
  scope).
- **The enterprise repo.** Without a subscription its 401 breaks `apt
  update`; the first-boot script turns it off. A future installer that adds
  another enterprise source would need the same.

## Out of scope

- Proxmox clusters (corosync, more than one node), Ceph, HA.
- Backing up VM and CT disks held as zvols (vzdump and Proxmox Backup
  Server do that); CT subvol datasets are files and come back.
- Proxmox on ext4/XFS with LVM-thin (generic Debian GRUB path; works or not
  as `debian13` does, unclaimed until a guest says so), on Btrfs, or on
  BIOS with `proxmox-boot-tool`'s GRUB mode (Decision 7).
- Secure Boot (shim + signed GRUB on the ESPs).
- Proxmox VE 8 and older, the arm64 ISO, Proxmox Backup Server and Mail
  Gateway.

## Decisions for the user

1. **How the guests are built:** the automated installer, `prepare-iso
   --fetch-from partition`, a per-build `PROXMOX-AIS` answer disk and a
   first-boot script (recommended: the installer's real layout and
   `proxmox-boot-tool` state, rebuildable); or Debian 13 plus Proxmox's
   packages from the host as `make-debian-zfs.sh` (+0.5 d, our layout); or
   one GUI install, pinned.
2. **How dump sees the ESPs:** a private read-only mount of each ESP in
   `proxmox-boot-uuids`, listed in `boot.proxmox_boot` and in `boot.esps`
   with `mounted_at: null` (recommended: the same contents record every ESP
   has, and `proxmox-boot-tool` mounts them the same way); or partition and
   UUID only, no contents (no mount, but the judge and the mode detection
   lose their source).
3. **How load restores the ESPs:** format with the volume ID, then
   `proxmox-boot-tool init` per ESP by the source's mode and one `refresh`
   (recommended: the owner rebuilds them, exactly as a kernel update would,
   and both ESPs come out in sync); or back up the ESPs' files (dump mounts
   them for the archiver) and restore them byte for byte (closer to the
   source, but kernels on the ESP then disagree with any initrd rebuilt in
   load, and the archive grows by ~200 MiB an ESP).
4. **Which modes:** `systemd-boot` (UEFI, no Secure Boot), tested; GRUB-BIOS
   and GRUB-EFI through `init … grub` written but refused in the plan until
   a guest tests them (recommended); or support GRUB-BIOS too, with a third
   guest `proxmox9-bios` (+0.4 d).
5. **The product gate:** dump records `os.product` from `pve-manager`; load
   refuses a `proxmox_boot` source whose PVE major isn't 9 (recommended);
   or no gate (Debian 13's row alone decides; PVE 8 would get a plan it was
   never tested with).
6. **HoltTest1's word:** **1D** (recommended: to bko-holt it is Debian 13,
   with the same ZFS-host needs as `debian13-zfs`; 1D becomes ten runs); or
   **1Obscure**; or a new word **1P** (Proxmox).
7. **Secure Boot:** out of scope (recommended); or in, as a third guest
   with shim and signed GRUB (+0.5 d).
8. **The ISO:** `proxmox-ve_9.2-1.iso`, or the newest 9.x on the day of P1,
   SHA-256 pinned (recommended); or pin 9.2-1 even if newer exists.
9. **Guest content in `rpool/data`:** one 64 MiB zvol and one CT-style
   subvol dataset made by the first-boot script (recommended: shows the
   zvol note and that a CT's files come back, without running a VM); or a
   real LXC container from a downloaded template (more realistic, a second
   download); or nothing.

Sources read 2026-11-14 (through web search): Proxmox's press release for
VE 9.2 (proxmox.com, about/press-releases/proxmox-virtual-environment-9-2),
its downloads page (9.2-1 ISO, SHA-256), the wiki's "Automated
Installation" (pve.proxmox.com/wiki/Automated_Installation), and the
Hetzner community tutorial "install-proxmox-unattended-hetzner"
(`reboot-mode = "power-off"`).
