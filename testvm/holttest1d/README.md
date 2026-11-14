# HoltTest1D: a whole-machine round trip of bko-holt on Debian 13

Status: 2026-11-07.  The plan below is the goal.  What runs today is
`ht1.sh`, an interim driver with its own rescuers; see "Running it today".
It runs HoltTest1D and its siblings, the same test on other systems; see
"The HoltTest1 family".  Runs work in RAM under a reservation, and a pass
keeps only its records (decision 10 of `docs/design/test2.md`, approved
2026-11-07); see "Where a run works, and what it keeps".

HoltTest1D checks that bko-holt can describe a Debian 13 machine and build it
again on blank disks, well enough that a rustic-bko restore onto them boots and
is the same machine.  It covers the disk layout, partition tables, RAID,
encryption, volume management, pools, filesystems with their features, and the
boot loader.  It is the "whole machines" layer of `docs/design/layload.md`'s
Testing section.

`testvm/compare` already does a round trip like this, but its Python
`recreate()` reads the layout off the source's disks directly.  HoltTest1D
gets the layout only from `bko-holt dump`'s JSON, as a real bare-metal
restore must.

## The HoltTest1 family

Each test is HoltTest1D, the same steps and verdicts, with other guests as the
source.  The user named them; a run of one runs `ht1.sh` on each of its
guests.  A guest passes only when every check passes.  Status is as of
2026-11-05, with the Debian (BIOS) and Ubuntu (UEFI) rescuers of
`make-rescuer.sh`.

| Test | Systems | Guests | Status |
|---|---|---|---|
| 1D | Debian 13, Proxmox VE 9 | `debian13`, `debian13-zfs`, `debian13-stack`, `proxmox9`, `proxmox9-mirror` | passes (the Proxmox guests on Chopin and Cinqcent only, 2026-11-14: `proxmox9` runs `pve6-10` Chopin and `pve6-12` Cinqcent, `proxmox9-mirror` `pve6-11` Chopin and `pve6-13` Cinqcent, the mirror's disks also booted alone; the ESPs judged by content, `judge.py esp`; docs/design/proxmox.md) |
| 1M | Linux Mint | `mint22` | passes |
| 1U | Ubuntu, KDE neon, AnduinOS | `ubuntu24`, `ubuntu24-uefi`, `ubuntu24-luks`, `ubuntu26-uefi`, `ubuntu26-zfsmirror`, `ubuntu26-raid` (and `degraded`), `ubuntu26-luks`, `neon24`, `anduinos2` | passes (`neon24`: run `ht1u` on both hosts, 2026-11-14; `anduinos2`: runs `a5-3` Chopin and `a5-4` Cinqcent, 2026-11-14, under Secure Boot with its MOK-signed GRUB kept) |
| 1Z | Zorin OS | `zorin18` | passes |
| 1F | Fedora, Rocky Linux, Fedora Kinoite (ostree, Bazzite's base) | `fedora44`, `fedora44-uefi`, `fedora44-raid`, `rocky10`, `rocky10-lvm`, `rocky10-raid5`, `kinoite44` | passes (`fedora44-raid` and `rocky10-raid5`: run `raid022`; `kinoite44`: run `bzb6a`; both below) |
| 1A | Arch, Alpine, AerynOS, Artix (dinit, s6) | `arch`, `alpine324`, `aerynos`, `artix-dinit`, `artix-s6` | passes |
| 1C | CachyOS, MX Linux | `cachyos`, `mx25`, `cachyos-zfs` | `cachyos` and `mx25` pass; `cachyos-zfs` (UEFI, Limine, / on ZFS; `make-cachyos-zfs.sh`, 2026-11-13) not run yet: it needs a host with OpenZFS (`needs_host_zfs`) |
| (1All only) | EndeavourOS, Manjaro, Garuda Linux, BigLinux | `endeavouros`, `endeavouros-grub`, `manjaro`, `garuda`, `biglinux` (`make-arch.sh --live`: each one's own live ISO, with its installer's defaults) | passes on both hosts (runs `eosmj1` to `eosmj5`; `garuda`: `ga4-4` Chopin and `ga4-5` Cinqcent, 2026-11-14, with snap-pac's 4 snapshots restored as shared ones; it needs rustic-bko 0.11.4+backotter.11 or later, since rel6's .9 restores the first shared snapshot wrong: `HT_ROUND=t2r-58a6955-3bb01f1-99b9b56` until a shipped set has it; `biglinux`: `bl4-2` Chopin and `bl4-3` Cinqcent, 2026-11-14, with `HT_SOURCE_PREP=prep/biglinux-timeshift.sh`'s 3 Timeshift snapshots left out) |
| 1V | Void Linux (musl) | `void-musl` | passes |
| 1G | Gentoo Linux | `gentoo`, `gentoo-systemd` | passes on both hosts (2026-11-12, bko-holt 7760c9e7c497 and rel6's rustic-bko: `gentoo` runs `g5-3` Cinqcent and `g5-4` Chopin, with GRUB's embedded lvmid that of the restored vg0/root; `gentoo-systemd` `g5-2` Cinqcent and `g5-5` Chopin) |
| 1Obscure | Pop!_OS, NixOS, Guix System, Omarchy, Sculpt OS, Redox, openSUSE Tumbleweed, Debian 12, Chimera Linux, PikaOS, Kali Linux, Vanilla OS | `pop24`, `nixos2605`, `guix15`, `guix15-bios`, `omarchy`, `sculpt2610`, `redox`, `tumbleweed`, `debian12`, `chimera`, `chimera-uefi`, `chimera-zfs`, `pikaos4`, `pikaos4-luks`, `kali`, `vanilla3` | passes (the Chimera guests on both hosts, 2026-11-12: `chimera` runs `c5-1` Cinqcent and `c5-2` Chopin, `chimera-uefi` `c5-3` Cinqcent and `c5-4` Chopin, `chimera-zfs` `c5-5` Chopin and `c5-6` Cinqcent; the PikaOS guests on both hosts, 2026-11-14: `pikaos4` runs `p6-4` Chopin and `p6-5` Cinqcent, `pikaos4-luks` `p6-6` Chopin and `p6-7` Cinqcent, booted alone from the EFI/BOOT fallback, `pikaos4-luks` with its passphrase typed by `console-boot.py`; rEFInd's ESP files and `refind_linux.conf` byte-identical, docs/design/pikaos.md; `kali` on both hosts, 2026-11-14: runs `k5-1` Chopin and `k5-2` Cinqcent, booted alone from the EFI/BOOT fallback with fresh OVMF variables, docs/design/kali.md; `vanilla3` on both hosts, 2026-11-14: runs `v7-1` Chopin and `v7-2` Cinqcent, on /bkoscratch ("The ABRoot guest" below), both roots booted alone, the other picked on GRUB's serial menu by `console-keys.py`, docs/design/vanillaos.md; the Guix guests on both hosts, 2026-11-14: `guix15` runs `gx6-2` Chopin and `gx6-4` Cinqcent (UEFI, booted alone through Guix's NVRAM entry, read from efivarfs by `nvboot-efivars.sh`: Guix has no efibootmgr; `gx6-3` Cinqcent FAILed once on one 4 KiB hole rustic-bko didn't keep in a hard-linked store file, not seen again), `guix15-bios` `gx6-5` Chopin and `gx6-6` Cinqcent (BIOS, GRUB written by `grub-bios-setup` from the source's own images); with the previous generation booted too, picked on GRUB's serial menu ("GNU system, old configurations...") on the source and on the restored disk alone: `guix15` `gx6-14` Chopin and `gx6-16` Cinqcent, `guix15-bios` `gx6-15` Chopin and `gx6-17` Cinqcent, docs/design/guix.md) |
| 1H | Haiku | `haiku6` | passes (run `haiku024`, rustic-bko 0.11.4+backotter.7, which restores the volume root's metadata: Tracker's empty `trash` had made a new BFS volume look used) |
| 1I | illumos | `omnios58`, `omnios58-mirror` | both pass (`omnios58-mirror`: run `raid6`, the restored `rpool` ONLINE on both disks) |
| 1BSD | FreeBSD, OpenBSD, NetBSD, GhostBSD, HardenedBSD, DragonFly BSD, AlpineBSD | `freebsd15`, `freebsd15-ufs`, `freebsd15-mirror`, `freebsd15-geli`, `openbsd8`, `netbsd11`, `ghostbsd26`, `hardenedbsd15`, `dragonfly6`, `alpinebsd` | passes; `freebsd15-geli` (root pool on GELI, docs/design/geli.md) not run yet: it waits for bko-holt's GELI support (G2, G3) |

**Cinqcent's old runs.** On 2026-11-13 every run dir and run log in
Cinqcent's `/bkoscratch/holttest1d` moved to
`chopin:/bkoscratch/archive/cinqcent-20261113/holttest1d/`, among them
`raid022`, `bzb6a`, `lk1`, `chim3`, `r4all` and the `t2h-*` runs. `bin/`,
`rescuer/`, `queue/`, `ht1-queue.sh`, `peaks.tsv` and the `build-holt-*`
and rescuer build logs stayed. Their qcow2 overlays were flattened first,
so they don't need Cinqcent's guests. The omnios `rescuer.qcow2` and
`rescuer-try.qcow2` overlays in `raid1-firsttry`, `raid3` and `raid5` were
already unreadable, because their backing file
`rescuer/omnios58-reguid.qcow2` had been deleted.

**The RAID guests** (2026-11-07, run `raid1`; `fedora44-raid` and
`rocky10-raid5` again in run `raid022` on Cinqcent, in RAM, with bko-holt
0.2.2), each with a `fresh-install` snapshot on every disk:

- `fedora44-raid` (`make-fedora.sh --uefi --raid`): two disks, /boot/efi
  on md RAID1 with metadata 1.0, /boot on md RAID1, / on btrfs RAID1
  (data and metadata).  Passes.  In `raid1` it failed at `assemble`:
  `load` passed metadata 1.0 `--data-offset=0K`, which mdadm refuses
  (`invalid data-offset: 0K`); fixed in 78018db.  A btrfs RAID1 member's
  own device UUID is new (`mkfs.btrfs --device-uuid` sets it only for a
  single device), and `judge.py` ignores it.
- `ubuntu26-raid` (`make-ubuntu.sh --uefi --raid`): four disks, an ESP on
  each (GRUB kept on all four), md RAID10 (near-2) holding LVM with / ext4
  and swap.  Passes, and passes `degraded`.
- `rocky10-raid5` (`make-rocky.sh --raid5`): three disks, BIOS, /boot on md
  RAID1 across all three, md RAID5 (left-symmetric, internal bitmap) holding
  LVM with / XFS and swap.  Passes.  In `raid1` it failed the dump check:
  `load`'s mkfs.xfs gave `agcount` but neither the data size nor the AG
  size, so the rescuer's xfsprogs made another geometry (AG 458496 blocks,
  not 458368) and filled the LV, where the source's XFS stopped 4 MiB short
  of its end; fixed in 78018db.  Its first try in `raid022` stopped at the
  backup's read-only mount: Cinqcent's udev assembled the RAID5 from
  `vm-mount`'s copy-on-write members and its LVM activated the group on it,
  so the array was busy.  `vm-mount` now takes down what the host's LVM
  activated before stopping such an array, for RAID5 and 6 as it already
  did for RAID1 and 10.

For these, `vm-mount --ro` reads a RAID10 of two near copies as a RAID0 of
one member of each pair (dm's striped target, no md: the hosts' Linux 6.12
won't take a superblock Linux 7 wrote), and a RAID5 through throwaway dm
snapshots of its members, assembled read-only.  `judge.py` calls a named
md array (`/dev/md/boot`) by its name, not its kernel number (md126, md127
are handed out in the order the arrays come up at boot), and ignores an md
member's device number (mdadm numbers members in order; a RAID5 made
without `--assume-clean` numbers its last member after the others).

**The encrypted LVM guest** (2026-11-10, run `lk1` on Cinqcent, in RAM,
bko-holt 0.2.4): `ubuntu26-luks` (`make-ubuntu.sh --uefi --luks`) is what an
Ubuntu install with "Encrypt the LVM group with LUKS" ticked lays out,
Subiquity's guided LVM: GPT, an ESP, /boot (ext4), and LUKS2 holding
`ubuntu-vg`, whose 10 GiB `ubuntu-lv` is / (ext4), 3.5 GiB of the group left
free; a swap file.  Its initramfs asks for `console-passphrase` on the
serial console.  Passes: `load` recreates the LUKS2 header with the
source's cipher, key derivation and offset, opens it as `dm_crypt-0` (the
crypttab name), restores the group with `vgcfgrestore`, and the restored
disk boots with the passphrase typed.

**The ostree guest** (2026-11-11, run `bzb6a` on both hosts, in RAM,
B6's static bko-holt from main, `HT_HOLT`): `kinoite44`
(`make-fedora.sh --ostree`; docs/design/bazzite.md) is Fedora Kinoite 44,
an image-mode system: UEFI with Secure Boot off, GPT, an ESP, ext4 /boot
with bootupd's static GRUB config, and btrfs subvolumes `root` (ostree's
repository and deployments under a composefs `/`), `home` and `var`.  It
has two deployments of one commit, `.1` (the default, with the karg
`bko.rollback=1` and an `/etc/bko-deployment`) and `.0` (the rollback).
ht1.sh boots both, on the source and on the restored disk:

- **The source's baseline.**  On a source with `/run/ostree-booted`, the
  source phase first wants exactly two BLS entries.  It then boots the
  rollback entry and the default again, and only then dumps.  Each boot
  writes the booted deployment (`CHECKSUM.SERIAL`), `/proc/cmdline`,
  `/etc/bko-deployment`, the root's type and source, SELinux's mode and
  `ostree admin status` (`logs/source-ostree.txt`,
  `source-ostree-rollback.txt`), and the rollback's boot state.  The
  rollback must boot another deployment, the default must show the same
  as its first boot, and grubenv must be byte for byte as it was.
- **The restored disk's two boots.**  After the default boot's checks and
  its dump, the rollback entry boots in the same QEMU.  Each boot must
  show exactly what the source's did, and the rollback's boot state may
  have no line the source's lacked.
- **How the rollback is picked.**  bootupd's `grub.cfg` loads grubenv but
  never reads `next_entry`, so `grub2-reboot` does nothing there.  ht1.sh
  sets `default` in grubenv instead, to the ID of the one entry whose
  `ostree=` leads to the deployment not booted (`ostree-1`, the BLS file's
  name).  `grub2-editenv unset default` in the rollback boot then puts
  grubenv back as it was.
- **What the file check allows.**  `judge.py fs` lets a hardlinked
  symlink come back as separate symlinks, since both archivers relink
  regular files only: 95,521 paths, ostree's symlink objects and their
  links in each deployment.  It also lets btrfs's own no-compress flag
  through: 7,159 files restored onto fstab's `compress=zstd:1` by the
  Ubuntu rescuer's kernel (7.0) gained it.  The regular objects keep their
  links.
- **Results.**  It passes on both hosts: 0 file differences, 0 dump
  differences, all of load's stages, and both restored boots the
  source's.  The RAM-disk peak was 11.8 GiB (Chopin; Cinqcent 11.5) in
  the rebuild phase, and the run took 645 s.  So ram-sizes.tsv's D is 14,
  one of ht1-queue.sh's big guests.
- **bko-holt.**  bin/'s bko-holt (0.2.4 of 2026-11-07) has no ostree
  support, so until bin/ is refreshed a run of `kinoite44` needs
  `HT_HOLT` or `HT_HOLT_BIN` naming a build from main.

**The ABRoot guest** (2026-11-14, runs `v7-1` Chopin and `v7-2` Cinqcent,
on /bkoscratch, a static bko-holt from main's tree, `HT_HOLT`): `vanilla3`
(`make-vanilla.sh`; docs/design/vanillaos.md) is Vanilla OS 3: UEFI with
Secure Boot on, two btrfs roots `vos-a` and `vos-b` on LVM thin LVs, an
`init` LV with each root's kernel, initramfs and `abroot.cfg`, and a master
grub.cfg whose literal `set default=1` boots B ("Current State (B)"); its
entry 0, "Previous State (A)", boots A.  ht1.sh boots both roots, on the
source and on the restored disk, each boot alone:

- **The source's baseline.**  On a source with
  `/usr/share/abroot/abroot.json` and `abroot`, the source phase boots its
  default, then the other entry, then its default again, and only then
  dumps.  Each boot records the root `abroot status` calls present and
  the one it marks ✓ (it never says "previous"), the booted root's label,
  its `/abimage.abr` digest, `/etc/bko-root`, `/proc/cmdline` and the
  master grub.cfg's and grub.cfg.future's sha256 (`logs/source-abroot.txt`,
  `source-abroot-other.txt`); the other boot's boot state too.  The other
  entry must boot the other root, the default must show the same as its
  first boot, and no boot may change grub.cfg.
- **How the other root is picked.**  The master grub.cfg reads `default`
  literally, so `grub-reboot` would do nothing: vm_up's `GRUB_PICK` runs the
  guest under `testvm/vanilla/console-keys.py`, which types the arrow
  toward the other entry twice and Enter once its title shows on GRUB's
  serial menu.
- **mount-sys.**  ABRoot's init hook mounts /var and /etc's overlay and
  repairs the root's integrity when it finds it broken; it prints to the
  console only (before systemd).  Each boot's console log must have its
  success line and no repair, and its journal no ABRoot repair.
- **The restored disk's two boots.**  After the default boot's checks and
  its dump, the other entry boots in a fresh `--snapshot` QEMU.  Each must
  show exactly what the source's did (so grub.cfg unwritten), and its boot
  state may have no line the source's lacked.  `FLAKY_UNITS` leaves out
  `systemd-networkd-wait-online.service`, which fails on some boots of the
  guest and not others (`logs/bootstate-raw.txt` has each boot as it was).
- **What the file check holds.**  `judge.py fs` gives no boot-stage excuse
  to the master grub.cfg and grub.cfg.future, init's `abroot.cfg`s,
  kernels, initramfs, configs and System.maps, or EFI/vanilla and EFI/BOOT
  (24 paths), and wants them in the source's record: load regenerates
  nothing on ABRoot.  The dump check compares device-mapper devices by
  name when the restored machine numbers them otherwise (it activated
  vos-var first on Cinqcent), and lets the thin pool's LVs, made anew by
  lvcreate, have new UUIDs, the rescuer's host and their own place on
  the PV.
- **Results.**  Both pass: 0 file differences, 0 dump differences, all of
  load's stages, both restored boots the source's.  The run's peak was
  27.0 GiB on /bkoscratch (`HT_RAM=0` under `cmp-run.sh --reserve 0 5`),
  in rebuild, so ram-sizes.tsv's D is 30, over every host's RAM-disk
  ceiling: ht1-queue.sh runs it on /bkoscratch with `DISK_OVER=1` and
  leaves it queued without.  By hand:

      cd ~/backotter-work && WEAR_TAG=ht1:RUN:vanilla3 HT_PORT=2690 HT_RAM=0 setsid -f ./cmp-run.sh --reserve 0 5 \
        sudo -E testvm/holttest1d/ht1.sh vanilla3 RUN >/bkoscratch/holttest1d/RUN-vanilla3.log 2>&1 </dev/null
- **bko-holt.**  bin/'s bko-holt has no ABRoot support (docs/design/
  vanillaos.md, V2-V4), so until bin/ is refreshed a run needs `HT_HOLT`
  or `HT_HOLT_BIN` naming a build from main.

**HoltTest1All** (the user's definition, 2026-11-05): "like HoltTest1D
except that HoltTest1All targets every single guest OS we have": every guest
in `vms/` on the test hosts, 39 as of 2026-11-05 (the same set on Chopin and
Cinqcent), each with `ht1.sh`.  It is the union of the tests above plus the
guests no other test names yet: `devuan6`, `ubuntu26-zfs` and `freebsd14`.
A guest added since joins it with its group: among them 1G's `gentoo` and
`gentoo-systemd` (2026-11-12, docs/design/gentoo.md), 1Obscure's three
Chimera guests, 1F's `kinoite44`, 1C's `cachyos-zfs` and 1U's `neon24` and
`anduinos2` (docs/design/anduinos.md), and 1Obscure's `pikaos4` and
`pikaos4-luks` (2026-11-14, docs/design/pikaos.md), 1D's `proxmox9` and `proxmox9-mirror` (2026-11-14, docs/design/proxmox.md) and `kali` (2026-11-14, docs/design/kali.md) and `vanilla3` (2026-11-14, docs/design/vanillaos.md; on /bkoscratch: a queue needs `DISK_OVER=1`), and 1Obscure's `guix15` and `guix15-bios` (2026-11-14, docs/design/guix.md), and `garuda` and
`biglinux` beside EndeavourOS and Manjaro (2026-11-14, docs/design/garuda.md
and docs/design/biglinux.md).
Run `r1all` (2026-11-05): 16 pass, the 15 above plus `mx25` (MX 25 reports
`ID=debian`, `VERSION_ID=13`).  The rest fail where expected: dump's OS gate
(cachyos, devuan6, void-musl, nixos2605, tumbleweed, rocky10, rocky10-lvm,
pop24, debian12, ghostbsd26), a Linux binary on a BSD or illumos (freebsd14,
freebsd15, hardenedbsd15, netbsd11, openbsd8, dragonfly6, alpinebsd,
omnios58), no SSH (redox, sculpt2610, haiku6, and omarchy, whose LUKS
passphrase is typed at Plymouth), and `ubuntu26-zfs`, whose /boot is a
second pool (`bpool`) that `vm-mount` doesn't import, so the backup lacked
/boot and load's boot stage failed in dracut.

By 2026-11-06 every one of the 39 had passed (runs `r1all` to `r31`, the last
`omnios58` in `r31`).  Some of those passes were on builds before later
changes; the confirming full run `r32` was stopped when Chopin's /bkoscratch
filled.

**Run `r4all`** (2026-11-08, the confirming run, on one build: bko-holt 0.2.4
bef913e on every platform, the shipped rustic-bko of round rel4 with Haiku's
0.11.4+backotter.7, testvm at 6fff54b and then 7a322d1): every guest in
`vms/` but the build VM `freebsd15-builder`, 47, plus `ubuntu26-raid` and
`debian13-stack` `+degraded`, through ht1-queue.sh on both hosts in RAM
(Cinqcent 3 at a time, Chopin 2), in 55 minutes and 210 guest-minutes,
keeping 0.43 GB of records.  All pass but `freebsd15-geli`:
`fedora44-raid` failed first on Chopin ("no partition holds /etc/fstab":
the kernel still had its btrfs's second member at the nbd path of the day
before) and passed again after 7a322d1; `freebsd15-geli` waits at the
loader's GELI passphrase prompt, which ht1-bsd.sh can't type yet (ht1.sh's
console-passphrase is Linux's only), and was aborted in its source phase.

## Systems other than Linux: ht1-bsd.sh and ht1-offline.sh

`ht1.sh` hands a guest that isn't Linux to a driver of its own; the steps and
verdicts are HoltTest1D's.

**ht1-bsd.sh** (FreeBSD, HardenedBSD, GhostBSD, NetBSD, AlpineBSD, OpenBSD,
DragonFly, illumos, Haiku) runs bko-holt built for the guest's own system
(`build-holt.sh PLATFORM`: natively, in a throwaway copy of a guest of that
system, into `bin/PLATFORM/bko-holt`) and the shipped set's rustic-bko for
it (`~/backotter-work/bko-bin/PLATFORM/rustic-bko`, checked against the
set's `SHA256SUMS`; `HT_BKO_BIN`, `HT_ROUND` or `HT_RUSTIC` picks another,
as for Linux).  The rescuer is a throwaway copy of a guest of the same
system, whose own disk comes first:

| Systems | Rescuer | Notes |
|---|---|---|
| FreeBSD, HardenedBSD, GhostBSD | `freebsd15-ufs` | UFS: nothing of its own clashes with a ZFS source's `zroot` |
| NetBSD, AlpineBSD | `netbsd11` | |
| OpenBSD | `openbsd8`, with a disklabel UID of its own | made once (`rescuer/openbsd8-newduid.qcow2`): two disks with one DUID and OpenBSD mounts neither |
| DragonFly | `dragonfly6` | its fstab names its own disk (`vbd0s1d@ROOT`) |
| illumos | `omnios58` | its pool is `rpool` too: `load` makes the target's under another name until close (`zpool create -t`), and the script imports the source's as `ht1src` |
| Haiku | `haiku6` | logs in as `user` (Haiku's uid 0); its mount_server is stopped, or it mounts every BFS volume it sees |

Linux can't read most of these filesystems, so the rescuer does the backup
too: the source's disks attached, mounted read-only from the layout, backed up
into the rescuer's own disk (`R/rescuer.qcow2`, kept from backup to verify).
The file check is mtree(8)'s (a spec of the source, sockets left out, checked
against the restored tree), or on Haiku and illumos GNU find's listing and
every file's SHA-256.  The boot state is the enabled services that say they
aren't running (FreeBSD's `service -e`, OpenBSD's `rcctl ls failed`,
illumos' `svcs`).

The guest's disks are its image and its `vm-run-options`' `--disk` ones
(named relative to its directory), as `ht1.sh` takes them, and each goes
through every phase: attached in that order after the rescuer's own disk for
the backup, one blank target each (then the decoy) for the rebuild, mapped
with a `--map` and an `--erase` apiece, and all of them for verify's mount
and the restored boot.  A ZFS pool on more than one disk must import with
every one of them ONLINE, from the source's disks and from the restored ones.
The multi-disk guests are `freebsd15-mirror` (`make-freebsd.sh --zfs-mirror`:
`zroot` a mirror of two disks, each with its own gptzfsboot and swap
partition) and `omnios58-mirror` (`make-omnios.sh --rpool-mirror`: `rpool`
attached to a second whole disk, the loader on both); either disk of each
boots alone.

`freebsd15-geli` (docs/design/geli.md) has its root pool `zroot` on the GELI
provider `vtbd0p3.eli`, made with `-b -g` and a passphrase, which the loader
asks for on the serial console.  When the guest's directory has
`console-passphrase`, its source and restored disks boot through
`testvm/console-boot.py`, which logs the console where `--serial-log` would
and types the passphrase at each prompt.  The rescuer's loader may ask too (it
probes every disk's partitions; not yet seen), so the backup's and the
verify's rescuers boot the same way when the layout has a GELI provider; the
rebuild's targets are blank.
In the rescuer the passphrase is `/tmp/ht1-geli/passphrase`, mode 600 and
without its final newline, in a tmpfs mounted there for it (a default
install's `/tmp` is on its disk), removed before the rescuer goes down.  The mount
script attaches each provider read-only first (`geli attach -r -j`; one
already attached is left as it is), and the unmount script detaches it after
the pool's export.  `load` gets `--key-file vtbd0p3=/tmp/ht1-geli/passphrase`,
so the restored provider has the source's passphrase, and the restored boot
answering SSH means the loader took it.  mtree may see `/var/backups/*.eli`
(and `/var/backups` itself) change on such a guest only: load writes the
metadata backup again for the new provider (the design's D5).  judge.py
compares every field of the `geli` record, judges the probe inside it as a
partition's content, and lets `provsize` differ only where it is the
partition's size on both sides.  ht1-bsd.sh's `geli_provs` and judge.py's
`geli_records` are the two readers of the dump's GELI records.

On illumos a root mirror finds its other half by the physical path (the PCI
slot) in its label before the device id, so the disks keep their slots
(`vm-run --disk-slots`) and serials (`--disk-serials`) from the machine to
the rescuer.  A real rescue stick is on USB and takes no disk's slot: the
rebuild's rescuer has its own disk in slot 16.  Only the rebuild's, for the
rescuer's own `rpool` names slot 4 too, and with a real `rpool` there (the
source's, the restored one) the rescuer boots that one.  The verify's
rescuer, whose import is read-write, runs on `--snapshot`, so its slots don't
reach the restored labels.

**ht1-offline.sh** (Redox, Sculpt OS) is for a system that can't run
bko-holt: Redox's sshd serves no logins, and Sculpt has no shell.  The
dumps are `bko-holt dump --disk /dev/vdb`, in the Linux rescuer with the
guest's disk attached; the backup and the file check mount the root
filesystem on the host, from a raw throwaway copy (RedoxFS through its FUSE
driver from `~/backotter-work/tools/redox`, so Redox runs where those tools
are: Cinqcent, Copland and Cage, not Chopin; Test2 pins it to Cinqcent,
`classes.json` `holt.pins`;
Sculpt's GENODE ext2 read-only); a boot is checked by logging in as root on
Redox's serial console (`redox/console.py`), and by `sculpt-check.sh` for
Sculpt.  The rebuild is `ht1.sh`'s, in the Linux rescuer.  It takes
`HT_VARIANT=refuse` as well as `bigger` and `oneshot`: see "The fitting
variants".

Other tools these runs need:

- `make-bcachefs-tools.sh` builds bcachefs-tools 1.13 (NixOS's root) for
  the Debian rescuer, which lacks them: the 6.12 kernel has bcachefs, Debian
  13 dropped the tools, and newer tools format a version 6.12 can't mount.
- A guest whose LUKS passphrase is typed at Plymouth (Omarchy's
  `luks-passphrase`) gets it typed on its keyboard through QEMU's monitor
  (`omarchy/type-luks-passphrase.py`), as compare.py does.

## Running it today: ht1.sh

`ht1.sh GUEST RUN [PHASE...]`, run as root on a test host under a
reservation of the RAM disk in cmp-run.sh's ledger (as `m`, which owns the
ledger's lock files), sized by `ram-size.sh`:

    cd ~/backotter-work && read -r _ D M _ < <(testvm/holttest1d/ram-size.sh mint22) &&
      WEAR_TAG=ht1:ht1m:mint22 HT_PORT=2690 setsid -f ./cmp-run.sh --reserve "$D" "$M" \
        sudo -E testvm/holttest1d/ht1.sh mint22 ht1m >/bkoscratch/holttest1d/ht1m-mint22.log 2>&1 </dev/null

`HT_PORT` is the guests' SSH port.  By-hand runs use 2690-2699, which
ht1-queue.sh never hands out (2690 is ht1.sh's default, 2698 and 2699
ht1-debug.sh's): give each by-hand run on a host a port of its own.  A run
refuses to start on a port that has a listener or another run's QEMU
forwarding it, and stops only its own VMs (the ones it started, with disks
in its directory: see "Where a run works, and what it keeps"), but it
holds the port only while one of its VMs is up: a second run started on
the same port during the first's backup would answer the first's ssh
later.
`WEAR_TAG` (`ht1:RUN:GUEST`, or `GUEST-VARIANT` for a variant) names the
run in the wear log; set it per command, not exported.  The run works in
RAM and ends up in `/bkoscratch/holttest1d/ht1m/mint22/` (see "Where a run
works, and what it keeps"); for a queue of guests, see "Queues:
ht1-queue.sh".

It needs `/bkoscratch/holttest1d/bin/` with `bko-holt` and the
`f2fs-tools_*.deb` and `jfsutils_*.deb` (installed in a rescuer that lacks
those tools), and the rescuers that `make-rescuer.sh` builds (see below).
A run whose phases would use the debs (backup, rebuild) or the rescuer
(rebuild) refuses to start without them (exit 2), saying how to get them:
there is no fallback to the plain `vms/debian13` guest any more
(`HT_RESCUER_IMG=~/backotter-work/vms/debian13/debian13.qcow2` asks for it);
`+img` needs neither.  Chopin and Cinqcent have both; a new host gets them
by the provisioning steps in testvm/HOSTS.md (Copland and Cage have
neither yet).  ht1.sh copies
bko-holt (or `HT_HOLT`'s) as the run starts, so its dumps and its load are
one build's even if bin/'s is replaced while it runs; ht1-bsd.sh and
ht1-offline.sh still take it from bin/ (or `HT_HOLT`) at each phase, so
leave bin/'s alone while one runs.  `HT_HOLT_BIN=DIR` takes bko-holt from
another set laid out as bin/ is (`DIR/bko-holt`, `DIR/PLATFORM/bko-holt`):
HoltTest2's builds, one directory per code digest in
`/bkoscratch/test2/holt-bin/CODE/` (with its `SHA256SUMS` and `BUILD-INFO`),
which never change once built; a run refuses when the set lacks its
platform's binary.  bin/'s debs and bcachefs stay bin/'s.  `build-holt.sh
linux` builds the Linux one on the host (static glibc, as ht1-debug.sh
does, cargo's target/ in `TMPDIR`), and `build-holt.sh --reserved RUN
PLATFORM` any platform under a reservation of its own (HoltTest2's, its
header says how).  rustic-bko is the shipped set's
(`~/backotter-work/bko-bin/linux/rustic-bko`, checked against its
`SHA256SUMS`); `HT_ROUND=NAME` takes round NAME's instead, and
`HT_RUSTIC=FILE` a build of your own, unchecked.  No driver reads bin/'s
`rustic-bko` any more: since 2026-11-07 it is a copy of the shipped one,
which the images carry, and the patched build Linux runs used before is
kept as `bin/rustic-bko.backotter4` (for `HT_RUSTIC`).  The guest's other
disks and firmware come from its `vm-run-options`; a guest with a
`console-passphrase` or `zfs-passphrase` has it typed at each boot
(`testvm/console-boot.py`), given to `vm-mount --key`, and given to
`load --key-file` for each LUKS volume and passphrase-keyed ZFS root.  The
phases are `source`, `backup`, `rebuild` and `verify`; each needs the one
before it, and the run stops at the first that fails.  After a bko-holt fix,
`rebuild verify` is enough, unless the fix changed what `dump` writes.
That works on a failure's state, which comes back into RAM from
/bkoscratch for it, but not on a pass's records, which keep no disks or
repository: run those from `source`.

`HT_SOURCE_PREP=FILE` runs a script of the test's as root in the source,
in its throwaway overlay, before the dump, and reboots the source when its
last line is `REBOOT`.  `prep/tumbleweed-snapper.sh` gives tumbleweed four
snapper snapshots of a changing root and a `snapper rollback`, for load's
shared btrfs snapshots (docs/design/btrfs-snapshots.md; run `bkoexact` on
cinqcent, 2026-11-10: PASS).

The plan's variants are chosen with `HT_VARIANT`, each in a directory of
its own (`RUN/GUEST-VARIANT`): `oneshot` runs the rebuild as one
`--through close --commit`, and `bigger` rebuilds onto disks 1 GiB bigger
than the source's, judged with `judge.py --grow`, which lets only a disk's
size, GPT's last usable LBA and backup header and the protective MBR move
by exactly that much.  `smaller`, `fitbigger`, `nearfull` and `refuse` are
"The fitting variants" below, for `load --fit-largest`.  `img` (Test2's
ImgTest2 panel; RAM only) rebuilds from the pinned BackOtter rescue image,
`t2-host tool img pin`'s, instead of the rescuer, its own bko-holt and
rustic-bko doing the load ("The rescue image as rescuer: +img"; ht1-bsd.sh
takes it too).  `degraded` runs the source with its last disk
detached, a RAID member's, so its arrays are degraded when it is dumped
and backed up; `load` recreates them with `missing` slots onto one disk
fewer, and the restored machine must boot degraded with the same slots
empty (`/proc/mdstat`'s `[UU_]`), judged with `judge.py --degraded`.  It
runs on the guests whose arrays survive the loss of their last disk:
`debian13-stack` and `ubuntu26-raid`.  A full HoltTest1D is six runs:
`debian13`, `debian13-zfs` and `debian13-stack`, `debian13-stack` with
`bigger`, and `debian13` with `oneshot` on each host, and since 2026-11-14
`proxmox9` and `proxmox9-mirror` on each ZFS host (ten in all).  Guests whose
volume groups share a name (`ubuntu26-raid`, `ubuntu26-luks` and `ubuntu24-luks`, all
`ubuntu-vg` as Ubuntu's installer names it) may run on one host at once:
`vm-mount` sets their logical volumes up under names of its own, not LVM's
host-wide ones (run `vg2`, 2026-11-10: both PASS side by side).  Don't run
one guest twice on a host under two RUN names: both
mount at `/mnt/ht1-src-GUEST` and `/mnt/ht1-dst-GUEST` (`GUEST-VARIANT`
for a variant), and while one's mount is up `vm-mount` refuses the
other's, a FAIL that isn't bko-holt's.  ht1-queue.sh holds a guest back
while a run of it is on the host, its own or by hand (a ledger row of
ht1.sh or a place in cmp-run.sh's line, or a live driver in
`/mnt/bko-ram/holttest1d` or `/bkoscratch/holttest1d`); a run by hand
doesn't wait for the queue's.

Each run also checks, as the plan asks:

- a decoy disk after the targets, holding an ext4 filesystem, is refused
  without `--erase` (for not being blank) and still holds it at the end;
- the refusals and the dry run leave every target's and the decoy's first
  and last MiB as they were;
- the restored machine's health: `findmnt --verify`, every fstab mount
  mounted, swap on, `zpool status -x`, md arrays clean or active, every
  crypttab volume open (in the `degraded` variant an array may be
  degraded too), and load's log kept.  The same checks run on the
  source; one that fails there too isn't counted.
- on an ostree source, its two deployments booted in turn, on the source
  first and then on the restored disk, which must show what the source's
  boots did ("The ostree guest" above).
- on an ABRoot source, its two roots booted in turn, the other picked on
  GRUB's serial menu, on the source first and then on the restored disk,
  which must show what the source's boots did, grub.cfg unwritten and no
  integrity repair by mount-sys ("The ABRoot guest" above).
- GRUB's embedded LVM ids: every EFI binary on the restored machine's
  mounted vfat file systems that names its prefix by
  `(lvmid/VG-UUID/LV-UUID)` (GRUB's core image when /boot/grub is on an
  LV, as on `gentoo`: load's grub-install re-embeds it) must name the LV
  that holds /boot/grub, by `lvs -o vg_uuid,lv_uuid`; the report says
  whether those are the source's UUIDs too.  A machine with no such
  binary has no line (`logs/restored-lvmid.txt`, `source-lvmid.txt`).
- a guest whose `vm-run-options` enroll a MOK (`--mok`: `anduinos2`, whose
  GRUB is signed on the machine with its own key) boots, source and
  restored disk alike, under Secure Boot with that MOK in shim's MokList.
  Every `grubx64.efi` on the restored disk's vfat file systems must verify
  with `sbverify --cert` against the source's `vms/GUEST/GUEST-mok.der`
  (`logs/restored-sbverify.txt`), and `mokutil --sb-state` in the booted
  restored guest must say `SecureBoot enabled` (`logs/restored-sbstate.txt`,
  the source's beside it).  `judge.py fs` holds AnduinOS's shim, MokManager
  and GRUB in `EFI/AnduinOS` and `EFI/BOOT` to the source's bytes.
- btrfs read-only snapshots (AnduinOS's baselines, snapper's): each of the
  source's (`btrfs subvolume list -r`, reached through a mount) must come
  back read-only, its `btrfs filesystem du` exclusive bytes at most twice
  the source's plus 64 MiB, not counting its own initramfs images, whose
  copies in the origin the boot stage rebuilt (`logs/source-snaps.txt`,
  `restored-snaps.txt`, `snaps-judge.txt`).
- AnduinOS's Disk Snapshots Manager: `status --json` and `list --json`
  with the subvolumes' UUIDs, on the source and the restored guest
  (`logs/source-snapmgr.txt`, `restored-snapmgr.txt`), and a `note:` line
  saying how many of the UUIDs its records name are on the restored file
  system.  Recorded, never judged.

It differs from the plan above in these ways:

- **The rescuer** is a throwaway overlay of a rescuer image, driven over
  SSH, not BackOtter's rescue `.img`.  `make-rescuer.sh` builds
  `/bkoscratch/holttest1d/rescuer/rescuer.qcow2` from the `debian13` guest
  (BIOS; OpenZFS 2.4 from trixie-backports, every mkfs, nothing of its own
  imported), and with `--uefi`, `rescuer-uefi.qcow2` from `ubuntu26-uefi`
  (UEFI; Ubuntu's OpenZFS 2.4).  A UEFI source is rebuilt from the UEFI
  one, as the stick boots in the machine's firmware mode: grub-mkconfig
  writes a UEFI grub.cfg only there.  Build them on one host, copy them
  to the other.  `HT_VARIANT=img` rebuilds from BackOtter's rescue `.img`
  instead, its `--test` build driven through its serial console: see "The
  rescue image as rescuer: +img".
- **The share** is 9p, read-only, mounted at `/mnt/share` in the rescuer.
- **bko-holt is built static** (`+crt-static`), so one binary dumps glibc and
  musl (Alpine) systems alike.
- **Known limits, not counted:** an F2FS superblock records the f2fs-tools
  release that made it, so a source made by a newer mkfs.f2fs (AerynOS's
  1.17) comes back with the rescuer's (1.16); XFS's allocated-inode count
  is usage.  `judge.py` names both when they differ.
- **The file check** records fsmeta of the source (mounted read-only) and
  of the restored disk, and `judge.py fs` compares them.  Only what `load`'s
  boot stage writes by design may differ (`BOOT_STAGE`): `/boot/grub` (or
  `grub2`), the ESPs' loaders, the rebuilt initramfs files and BLS
  entries, `zpool.cache`, `/var/log/bko-holt`, dracut's and os-prober's
  state, and the temporary directories.  `judge.py` lists each with its
  reason, and prints how many paths each one covered.
- **The dump check** is `judge.py SRC.json DST.json`.  `IGNORED` names
  what can't be kept or changes at every boot, each with its reason: ZFS
  vdev GUIDs and object numbers, btrfs subvolume UUIDs, md members'
  device UUIDs, LVM's last-writer fields, mount order, usage, zram.
- `vm-run` takes its SSH port from `SSH_PORT`; `ht1.sh` sets it from
  `HT_PORT`.

## The fitting variants

`load --fit-largest PCT` lets a target disk a little smaller or bigger than
its source be used, by contracting or expanding the largest partition of
each disk by at most PCT percent (`docs/design/layload.md`, the fitting
section).  Four `HT_VARIANT` values test it, each in its own
`RUN/GUEST-VARIANT` directory as the others are.  All four make their
targets a gigabyte away from the source's disks, every disk, as `bigger`
does, and give `load --fit-largest 10%`; none of them needs more room than
`bigger` does, except `nearfull`, which fills the source.

- **`smaller`**: targets 1 GiB smaller, the whole rebuild, judged with
  `judge.py --fit -1073741824 --fit-pct 10`.  Before anything is erased it
  also checks that `plan` **without** the flag still refuses the smaller
  target (naming the size, as it always did), that `plan --fit-largest 1%`
  refuses when a gigabyte is more than 1% of the guest's largest partition
  (skipped, with a line saying so, for a guest whose largest partition is
  100 GiB or more), and that the target images are byte for byte what they
  were after those plans, by their md5 on the host.
- **`fitbigger`**: targets 1 GiB bigger, the whole rebuild, judged the same
  way with `+1073741824`: the largest partition grows into the room, or,
  for LVM, the PV grows and the group gains free extents and no LV changes.
- **`nearfull`**: `smaller`'s targets, but the source phase fills `/` in
  the source overlay with random files under `/var/tmp` (nothing of the
  system is deleted) until `df` shows under 900 MiB free, then dumps it.
  `plan --fit-largest 10%` must then refuse, naming what the filesystem
  holds and has free, and the targets must be untouched.  A pass is the
  refusal: there is no backup, restore or verify phase.
- **`refuse`**: `smaller`'s targets for a guest whose largest partition is
  a stack `--fit-largest` won't resize.  `plan` must refuse naming the
  stack, and the targets must be untouched; no backup or restore either.
  Its guests are the md ones (`debian13-stack`, `ubuntu26-raid`,
  `rocky10-raid5`), the ZFS ones (`debian13-zfs`, `ubuntu24`,
  `ubuntu26-zfs`, `ubuntu26-zfsmirror`) and `fedora44-raid`, whose btrfs
  spans two disks.  `ht1-offline.sh` takes `refuse` too, for `redox` and
  `sculpt2610`: a layout of another family keeps its own partition table,
  which `--fit-largest` doesn't rewrite, so a smaller target stays refused
  there.  (`ht1-bsd.sh` doesn't: its rescuer is built in the backup phase,
  so a BSD refusal would have to back the guest up first.)

`judge.py --fit BYTES [--fit-pct PCT]` works the new geometry out from the
source dump alone, as `load` works it out from the same numbers: the last
usable LBA the target has, the overrun past it rounded up to a whole MiB
(or, for a bigger target, the room rounded down and capped by PCT), and the
largest partition of each disk.  Anything load would have refused outright
is reported rather than judged: an exact tie for largest, an MBR logical
partition, a shrink past PCT, a stack it won't resize.  Then every size
that follows: the partition and the ones after it, the filesystem's own size and
block count, a mkfs's geometry (XFS's AGs, ext's inode count, F2FS's
segments), LUKS's payload and what is inside it, and LVM's PV size, extent
count and the one LV a shrink takes the rest from.  Each of those must come
back at exactly the value judge.py worked out, so a run that fitted nothing
fails as surely as one that fitted the wrong partition.  Everything else
must match as it must without the flag: every UUID, label, type, mount
option, and the other partitions' sizes.  A filesystem the source left short
of its container's end (JFS's aggregate) is its mkfs's business, and
judge.py says so rather than expecting a figure.  `judge.py --fit-plan
BYTES [--fit-pct PCT] LAYOUT.json` prints what the fit comes to on a layout
(`DISK LARGEST AMOUNT LARGEST_BYTES HOLDS` per disk, then its problems),
which is what the drivers check their own run against.  A disk with no
partition to fit (a zvol, a guest's `/dev/ram0`) prints `-` in those
columns, which is no stack for the `refuse` check to look for in the
refusal; `judge-test.py` (`python3 -I judge-test.py`, no host and no VM)
holds the mode's cases, the drivers' reading of a plan among them.

Which guests suit which variant comes from the largest partition of each
disk: `smaller` and `fitbigger` want a stack load can remake, which is
every plain-filesystem guest (ext2/3/4, XFS, btrfs on one device, F2FS,
JFS, NILFS2), `omarchy` (LUKS on a plain partition, btrfs inside) and
`rocky10-lvm` (a PV with one group, `/` XFS and swap).  `nearfull` wants a
guest that is nearly full already, since the fill is written into the RAM
disk: `pop24` (3.0 GiB free), `omarchy` (3.8), `mint22` (4.2).
`ram-size.sh` adds 4 GiB to `nearfull`'s D for it; give the queue line its
own `=D` if that isn't enough.

## The rescue image as rescuer: +img

`HT_VARIANT=img` (built 2026-11-10, for Test2's ImgTest2) runs the rebuild
in BackOtter's own rescue image of the guest's system, its `--test` build,
instead of the rescuer: ht1.sh for Linux, ht1-bsd.sh for FreeBSD, NetBSD,
OpenBSD, DragonFly, illumos and Haiku.  It is the restore a user gets from
the stick, onto an old target: the panel is `holt.img_panel` in
`testvm/test2/classes.json`, 12 guests (`debian12`, `ubuntu24`,
`rocky10-raid5`, `nixos2605`, `debian13-stack` and `aerynos` for Linux;
`freebsd14`, `netbsd11`, `openbsd8`, `dragonfly6`, `omnios58` and
`haiku6`), each in `RUN/GUEST-img`.

- **The pin.** The run takes the image from
  `$T2_HOST_ROOT/img-pin/PLATFORM/` (default `/bkoscratch/test2`), which
  `t2-host tool img pin PLATFORM` fills: `rescuer.img`, a hard link of the
  `--test` image (or a reflink copy), its sha256, and the image's own
  bko-holt, rustic-bko and restic-bko, checked against the image's
  `bko-bin.sha256`.  A run refuses with no pin or with `bins-stale` there,
  and the rebuild checks that the pin is still the one the run started
  with (a re-pin during the run is a harness FAIL).  HoltTest2 pins before
  it runs `+img` cells and builds no bko-holt for them; by hand, pin first.
- **Its binaries.** The load runs the image's own
  `/usr/local/bin/bko-holt` and `/usr/local/bin/rustic-bko`
  (`/boot/system/non-packaged/bin/` on Haiku), as a user's would; source
  and backup take the pin's copies of the same two, so the dumps and the
  load are one build.  On Linux nothing is installed in the image: the
  setup checks every tool load may run (each `mkfs.*`, `zpool`, `zfs`,
  `mdadm`, `cryptsetup`, `lvm`, `sgdisk`), and one missing is a FAIL.
- **The boot.** The image is the first disk, through a 16 GiB throwaway
  overlay, in the source's firmware mode (`--uefi`, and `--no-secure-boot`
  where the guest has it), as a stick boots in the machine's own;
  ht1-bsd.sh adds the rescuer guest's options (omnios58's
  `--legacy-virtio-net`, haiku6's `--disk-bus sata`).
  `img-boot.py LOG PLATFORM PUBKEY -- vm-run ...` runs it with the serial
  console on its own stdin: at each `Choose [1-4]: ` it picks the shell,
  types the run's public key into `/tmp/bko-k` and starts `sshd` with
  command-line options (`AuthorizedKeysFile`, `PermitRootLogin
  prohibit-password`, `AllowUsers`, no passwords), which win over the
  image's own settings.  Nothing in the image file changes.  The menu, the
  prompt and the typing rates are `stick/boot-test.py`'s.
  `img-boot.d/PLATFORM.json` holds what differs: FreeBSD turns its line
  editor off first, DragonFly is typed at 960 bytes a second, illumos adds
  `/usr/lib/ssh` to the PATH and marks root `NP` (not locked) in the
  RAM disk's `/etc/shadow`, and Haiku logs in as `user` on SATA disks.
- **The key** is made for the run in R (`R/img-key`), deleted at the
  rebuild's end and in `ht_release`, and never moved to K: so `+img` runs
  only in the RAM disk (`HT_RAM=1` under a reservation) and refuses
  `HT_KEEP=1` and `HT_CLEAN=0`.  No image carries a key.
- **The repository.** Linux mounts the share over 9p, as its other runs
  do.  On the other systems backup and verify keep the rescuer guest: its
  disk holding the repository (through a throwaway overlay) is the image's
  last disk, after the decoy, mounted read-only at `/tmp/bko-repo`.  On
  illumos it keeps its serial, `bkoresc`, and the image's disk is `bkoimg`:
  illumos opens a pool's whole-disk vdev by the devid its label records.
- **Queues and sizes.** A todo line `GUEST+img` runs it; `ram-size.sh`
  sizes it as its guest.

First runs (2026-11-10 and 11-11, Test2's test worlds): `debian12`,
`debian13-stack`, `aerynos`, `ubuntu24` and `rocky10-raid5` pass on the
Linux image.  `nixos2605` failed: load made its bcachefs with the image's
tools at on-disk version 1.39 where the source's was 1.13, and the
restored disk booted but the host's `vm-mount` couldn't read it in verify.
Load now formats at the source's `version_min` (`--version=1037`, e2b31ad),
and on the image of 2026-11-11 built with that bko-holt `nixos2605` passes
on both hosts.
On the other systems' images of 2026-11-07 only `haiku6` passed.  The
rest failed on defects that were fixed, not classed: the read-only roots
(`mkdir: /mnt/bko-target: Read-only file system`: the builders now ship
that directory, and FreeBSD's a tmpfs on `/mnt` for `zpool create -R`),
NetBSD refusing bko-holt's second open of a block device (bko-holt keeps
the first open), illumos importing the image's own disk as the pool (the
serials above), and `refuse-root` let through on FreeBSD, NetBSD and
DragonFly (bko-holt now knows the stick by its config partition's
label).  On images rebuilt with those fixes, `freebsd14`, `netbsd11`,
`dragonfly6`, `omnios58` and `haiku6` pass; `openbsd8` fails, rustic-bko
killed out of swap during the restore (the image's VM has 3 GiB and no
swap).

## Where a run works, and what it keeps

All three drivers write the same way, through `ht1-lib.sh`:

- **R, where the run works.** Under a reservation of the RAM disk of at
  least 1 GiB (`cmp-run.sh --reserve D M`, the ledger row of one of the
  driver's ancestors), R is `/mnt/bko-ram/holttest1d/RUN/GUEST/`, and
  TMPDIR is `R/tmp/`: QEMU's `-snapshot` overlays and vm-mount's COW files
  are counted with the run and go with it.  Without a reservation the run
  works on /bkoscratch (R is K) and its report says so in a `note:` line.
  `HT_RAM=1` refuses to start without one; `HT_RAM=0` works on /bkoscratch
  whatever is reserved.
- **K, where the run ends up**, is `/bkoscratch/holttest1d/RUN/GUEST/`
  (`GUEST-VARIANT` for a variant).  At its end, still inside its
  reservation, the run stops its VMs, mounts and loop devices, kills
  whatever else holds a file in R (a shell `cd`'d into it, a `tail -f` of
  its report: watch the caller's log, or K, instead), is copied to K and is
  removed from RAM, so nothing of it outlives the reservation.  The
  caller's log says `moving R to K (N GiB)` as the copy starts and `the
  run is in K` once it is there.  If the copy fails (a full /bkoscratch),
  only the records reach K, kept.txt says what was lost, and R is deleted
  all the same.  An earlier attempt of
  the same RUN and guest becomes `K.1`, `K.2`, … in order, so K is always
  the latest (on /bkoscratch, a run from `source` renames it as it starts;
  a rerun from a later phase goes on in K); files a rerun left as they were
  are hard links to the attempt before.  Analyses that glob `RUN/*/` should
  skip the `*.N` ones.
- **The run's own processes** are the ones it started: their environment
  holds its TMPDIR (`R/tmp`, which vm-run and vm-mount keep, and the QEMU
  and qemu-nbd they start inherit), or, for a QEMU, it forwards the run's
  port (`HT_PORT`; for ht1-debug.sh's run HT_PORT + 1 too, its build
  guest's).  Only those, with a file in R, are the run's VMs: the ones it
  powers off and kills between phases, at an overrun and at its end, the
  ones a queue's abort and heat pause signal, and the qemu-nbd whose
  vm-mount it takes down.  On /bkoscratch, where R is K, that is all it
  takes down, with the mounts its driver made: a VM, `vm-mount`, mount,
  loop device or shell that someone else started on K's files (a `vm-run
  --snapshot` of the last attempt's `dst-0.qcow2`, say, whose command line
  still names K after a run from `source` renames that attempt K.N) is
  never signalled, unmounted or detached, and nothing is killed for
  holding K.  A VM that an earlier attempt left up on K's files (the
  rescuer of an `HT_KEEP=1`, a dead driver's) has K's TMPDIR too: a rerun
  from a later phase, which goes on in K, takes it for its own (a rebuild
  stops a kept rescuer first), and a run from `source` refuses until it is
  stopped.  In RAM, R is the run's alone: at its end, once its own are
  down, whatever still holds R is killed too, but for the qemu-nbd of a
  `vm-mount` someone else made of R's files (`sudo vm-mount --ro
  R/src-0.qcow2 /mnt/look`), which serves a mounted disk: with it killed,
  a pool on that disk would suspend, and everything touching ZFS on the
  host with it.  That vm-mount stays up, the caller's log says `R is
  still held: vm-mount:/mnt/look (qemu-nbd P): …`, and the files of R it
  has open stay in the RAM disk, unreserved, until its `vm-umount`.
- **What it keeps.** A pass that ran verify keeps only its records, and so
  do an overrun and a run that died (both below).  Anything else keeps
  everything: a FAIL, a pass without verify (`source backup`, say), and a
  run that ended early or by a signal, whose report then ends `RESULT: FAIL
  (…; ended … before its RESULT, exit N)`.  `logs/kept.txt` says which:
  `pass:`, `overrun:`, `swept:` or `lost: records only …`, or `fail:
  everything kept …`.

  | Driver | Records kept | Deleted | Compressed (zstd, `.zst`) |
  |---|---|---|---|
  | `ht1.sh` | `report.txt`, `logs/`, `dumps/src.json`, `dumps/dst.json` | `share/` (repository, password, binaries, debs, layout.json, passphrase), `src-*.qcow2`, `dst-*.qcow2`, `decoy.qcow2`, `rescuer.qcow2`, `rustic-bko`, `tmp/` | `src-meta.json`, `dst-meta.json` |
  | `ht1-bsd.sh` | `report.txt`, `logs/`, `dumps/src.json`, `dumps/restored.json` | `src-*.qcow2`, `dst-*.qcow2`, `rescuer.qcow2` (with the repository), `rescuer-try.qcow2`, `decoy.qcow2`, `prep-boot.qcow2`, `rustic-bko`, `repo-password`, `tmp/` | `src.listing`, `dst.listing` (Haiku and illumos) |
  | `ht1-offline.sh` | `report.txt`, `logs/`, `dumps/src.json`, `dumps/dst.json`, `check-*/` (the screenshot, vm-run-options) | `share/`, `src-0.qcow2`, `dst-0.qcow2`, `decoy.qcow2`, `rescuer*.qcow2`, `boot-*.qcow2`, `console-*.sock`, `tmp-*`, `check-*/sculpt-check.qcow2`, `rustic-bko`, `tmp/` | `src-meta.json`, `dst-meta.json` |

  That is about 0.3 GiB for a whole HoltTest1All.  `judge.py` reads the
  `.zst` files as they are, for a re-judge.  load's plans stay in logs/
  (`rebuild-refuse-*.out`, `rebuild-dry-partition.out`), and the plan with
  the real mapping can be made again offline from `dumps/src.json`.
- **The overrun stop.** In RAM the run's use is checked against its
  reservation every 5 s.  When it is over by more than 1 GiB or 10 %,
  whichever is more, or over it at all while /mnt/bko-ram has under 1 GiB
  free, or the reservation has ended (its ledger row's holder is gone,
  twice in a row, so cmp-run.sh hands the room out again), the run is
  stopped: its own QEMU is killed, the driver and what it runs get TERM (a
  vm-mount or vm-umount at work finishes first), and the report ends
  `RESULT: FAIL (…; harness: over its reservation: …)` (or `the RAM disk
  has under 1 GiB free (…), the run over its reservation …`, or `its
  reservation ended`).  That is the harness's stop, not a check, and the
  run keeps its records only: for its size, reserve more (or raise the
  guest's row in `ram-sizes.tsv`) and run it again.
- **A full RAM disk** doesn't stop a run within its reservation: the
  room is others' (cmp-run.sh admits reservations up to the disk's size
  less 1 GiB, and a run may pass its own by the margin above before it is
  stopped).  The run goes on, and its report says `note: the RAM disk had
  under 1 GiB free; this run was within its reservation`.  Should the disk
  then fill up, the run's ENOSPC is an ordinary FAIL, which keeps
  everything.
- **A run that died** (SIGKILL, out of memory) leaves R in RAM, unreserved
  (a host crash takes the RAM disk with it).
  `cd ~/backotter-work && sudo testvm/holttest1d/ht1-lib.sh --sweep` moves
  the records of every such run to K (`swept: records only (the run died;
  pid P)`) and deletes the rest of it; ht1-queue.sh runs it at its start
  and on every loop.  A new run of the same RUN and guest from `source`
  sweeps that R first (its note: line says so), so the dead attempt's
  records become the K.N before it; a rerun from a later phase goes on with
  the dead run's state, as with a failure's.
  It sweeps `HT_WORK` only, paired with its `HT_RECORDS`: the defaults, or
  a test's `/mnt/bko-ram/NAME` and `/bkoscratch/NAME`.
  It takes only a run ht1-lib.sh made (R has `.ht/`) whose lock is free,
  so it leaves alone a run that is still alive, one of an older ht1.sh (no
  `.ht/`: ht1-queue.sh logs it as a leftover), one left on purpose
  (`HT_CLEAN=0`, `HT_KEEP=1`) while its reservation lasts, and
  ht1-debug.sh's `debug/` and `debug-state/`.
- **A rerun from a later phase** (`rebuild verify` after a fix) on a
  failure's state copies it back from K into RAM, all but what the rerun
  makes again and the disks only a rebuild uses (the rescuer's, the
  decoy), and says so (`note: this run's state came from the attempt
  before it (K until this run moves in, then K.N)`).  ht1.sh's copy keeps
  the source's disks and the repository, so the attempt it leaves in K can
  be rerun again, from the same phase or an earlier one.  ht1-bsd.sh's and
  ht1-offline.sh's leave the source's disks out (ht1-offline.sh's verify
  the repository too): their attempt reruns from the same phase or a later
  one, else from `source`.  On a record that kept its records only (a
  pass, an overrun, a swept or lost run) it refuses: run from `source`.
- **Refusals** happen before any phase, exit 2 and leave nothing behind:
  an unknown phase; a RUN or guest name that isn't plain
  (`[A-Za-z0-9._-]`), and the RUNs `debug` (unless from ht1-debug.sh) and
  `debug-state`; `HT_RAM` other than 0, 1 or unset; in RAM, no
  `/mnt/bko-ram`, no reservation (`HT_RAM=1`), or `HT_CLEAN=0` or
  `HT_KEEP=1` without `HT_RESERVE_PID`; the same RUN and guest running on
  the host already (its lock held, or R or K in use by a live run); an
  `HT_PORT` that isn't a port, or one with a listener or another run's
  QEMU forwarding it (a rescuer the run's own `HT_KEEP` left up is its
  own); on /bkoscratch, a run from `source` while a VM of an earlier
  attempt is up on K's files; a rustic-bko that is missing (from its
  set, or `HT_RUSTIC`'s file), not in the set's `SHA256SUMS`, or doesn't
  match it; a rerun on a
  records-only record; `HT_LIMIT_KIB` or `HT_FREE_KIB` (ht1-lib-test.sh's
  stand-ins for the limit and the RAM disk's free space) without a test's
  own `HT_WORK`; and
  the drivers' own (a bad `HT_VARIANT`, `degraded` on one disk, redox
  without its tools).  A dead run's R that a refused run had taken stays
  as it was, for the sweep.
- **`HT_CLEAN=0`** leaves R exactly as the run ends: nothing deleted,
  compressed or moved, and no peaks row.  In RAM it needs `HT_RESERVE_PID`,
  the ledger PID of a reservation that outlives the run (ht1-debug.sh's),
  and so does `HT_KEEP=1`.

- **Test2's ledger row.** At its very end, once K is in place (and for a
  refusal after the run began, a rustic-bko that doesn't match its set,
  say), `ht_end` runs the copy's own `testvm/test2/t2-host row holt` (as
  `T2_WORK=W timeout -k 5 30 python3 -B T/test2/t2-host row holt --k K ...`),
  which reads K's records (report.txt, `logs/manifest.txt`, `phases.tsv`,
  `kept.txt`) and this host's facts and appends one row to
  `T2_HOST_ROOT/ledger/HOST.jsonl` (default `/bkoscratch/test2`): the
  cell (`holt:GUEST[+VARIANT]`), its verdict (PASS, FAIL, HARNESS for a
  refusal, an overrun or no RESULT line, LOST for a run ended by a signal
  or whose records couldn't be copied), the per-check needs, the inputs it
  was keyed on, the tree's commit (a copy's `T2-COPY.json`, else the
  synced tree's `.backotter-commit`) and the wear tag.  It is best effort:
  at most 35 s, nothing written into R or K, and one `note:` line in the
  caller's log, `note: Test2 ledger row ID` or why there is none (no
  `t2-host` in that testvm/, its exit, or `took over 30 s`); the exit code,
  what is kept and the move are the same with it or without it.  A run
  that `HT_CLEAN=0` leaves in place, ht1-debug.sh's (RUN `debug`), a
  refusal before the run began and a run that died (swept) get no row
  here; HoltTest2 writes those itself, from its queue's done rows.  Under
  HoltTest2's queue (`T2_CELLS`, `T2_RUN`) the row carries the plan's rule
  and key and counts as a PASS only when the key it measures is the
  plan's; a HoltTest1 run's row says rule `test1`, and its origin (the
  writer's idempotency id) adds the run's start, so a rerun of the same RUN
  and guest by hand is a row of its own.  A row never counts when the run
  had a debugging or test setting (`HT_QUICK`, `HT_THROUGH`, `HT_KEEP`,
  `HT_FAIL_AT`, `HT_RESCUER`, `HT_RESCUER_IMG`, `HT_LIMIT_KIB`,
  `HT_FREE_KIB`), ran fewer than the four phases (or its report doesn't
  say which), was seeded from K, used an `HT_HOLT_BIN` whose `SHA256SUMS`
  doesn't vouch for its bko-holt, or when an input changed while it ran (a
  file its manifest hashed, its guest's image, the tree's commit).  A run
  stopped over its reservation only after its RESULT line keeps that
  verdict, with a note.
- **The self-test**, `ht1-lib-test.sh` (as root under `cmp-run.sh
  --reserve 1 1`), runs the lib's cases with fake phases in
  `/mnt/bko-ram/NAME` and `/bkoscratch/NAME` (`HT1_TEST_NAME`, default
  `ht1-lib-test`; another refuses a NAME whose directories hold anything),
  its ledger rows in its own fixtures, never the default ledger.  Its last
  case needs no RAM disk: that the `HT_VARIANT` names in the drivers, in
  `ht1-queue.sh`'s line pattern and in `ram-size.sh` are one list, and that
  a name none of them has stops a run before it makes anything.
- **judge.py's own self-test**, `judge-test.py` (`python3 -I
  judge-test.py`, or `python3 -m unittest discover`), over hand-made dumps:
  the `--fit` mode's cases, where a wrong partition, a wrong amount, a
  changed UUID or a fit that didn't happen must all fail.

Each run also records, in `logs/`:

- `manifest.txt`: the host, kernel and QEMU; the mode, R, K and the
  reservation; every `HT_` variable; the commit of the testvm/ copy it ran
  from (a private copy's `COMMIT`, else the `.backotter-commit` that
  `sync-hosts.sh` writes); the sha256 of bko-holt (and, for
  ht1.sh, of the run's copy of it in the share), rustic-bko, the driver,
  ht1-lib.sh, judge.py, fsmeta.py, vm-run, vm-mount and the debs and
  bcachefs; the rescuer image and the guest's disks (size, mtime, inode,
  and the rescuer's `.sha256`).  The report's `binaries:` line has
  bko-holt's and rustic-bko's sums, and where rustic-bko came from.
  ht1.sh's report also has a `note:` line naming any setting for debugging
  or tests that is set (`HT_QUICK`, `HT_THROUGH`, `HT_KEEP`, `HT_FAIL_AT`,
  `HT_RESCUER`, `HT_RESCUER_IMG`), since `sudo -E` passes the caller's on.
- `phases.tsv`: a row per phase per invocation, with its start, seconds,
  peak RAM-disk use (R's blocks and QEMU's unlinked overlays in R, sampled
  every 5 s and at the phase's end), the job's peak anonymous memory, the
  reservation, the mode and the FAILs so far.  The report's `RAM disk:`
  line has the run's peak against its reservation.

`/bkoscratch/holttest1d/peaks.tsv`, one per host, gets a row per run that
ends with `HT_CLEAN=1`: the run, guest, phases, whether its state came from
K, exit, result, mode, reservation and peaks.  `ram-size.sh
GUEST[+VARIANT]...` prints `GUEST D M FROM` for `--reserve D M`.  Its D
counts only the guest's whole passes there (all four phases, not from K):
with two or more, D is 1.10 × the largest peak of the last three, plus
0.25 GiB, rounded up; with fewer, it is never below the guest's row in
`ram-sizes.tsv` (r3all's sizes; the BSD rows are estimates), and a guest
without a row gets a default.  A variant without a row of its own has its
guest's, and `+nearfull` 4 GiB on top, for the free space that variant
writes away in the source overlay.  Its header has the details.

## Queues: ht1-queue.sh

`ht1-queue.sh RUN BASEPORT MAXJOBS [QHOST]`, run as `m` on each test host,
runs ht1.sh on the guests in QHOST's
`/bkoscratch/holttest1d/queue/RUN/todo` (default: this host's), at most
MAXJOBS at once on this host, each under its own reservation:

    cd ~/backotter-work && mkdir -p /bkoscratch/holttest1d/queue/r4all &&     # Chopin, first: the todo
      testvm/holttest1d/ht1-queue.sh --order GUEST... >/bkoscratch/holttest1d/queue/r4all/todo
    cd ~/backotter-work && setsid -f testvm/holttest1d/ht1-queue.sh r4all 2600 3 \
      </dev/null >/bkoscratch/holttest1d/r4all-runner-chopin.out 2>&1                # Chopin
    cd ~/backotter-work && setsid -f testvm/holttest1d/ht1-queue.sh r4all 2600 3 chopin.local \
      </dev/null >/bkoscratch/holttest1d/r4all-runner-cinqcent.out 2>&1              # Cinqcent

Any other host of testvm/hosts.json runs a runner the same way, with the
queue host's name (`chopin.local` here) as QHOST; it takes only the lines it
can run ("What a host can't run", below).

A runner started before its todo exists finds nothing to do and ends
(`queue done`).  One that refuses to start (exit 2: an `HT_` setting, run
as root, no RAM disk) says why in its `.out` file only: its log,
`queue/RUN/runner-HOST.log`, begins once it runs.

- **todo** has a line per guest, `GUEST[+VARIANT][=D][%M]` (`#` starts a
  comment): `+bigger`, `+oneshot`, `+degraded`, `+img`, `+smaller`,
  `+fitbigger`, `+nearfull` or `+refuse` is `HT_VARIANT`, and `=D`
  and `%M` override ram-size.sh's sizes.  `ht1-queue.sh --order GUEST...`
  prints guests biggest first, for writing it.  Lines are taken from the
  top; a line that doesn't parse goes to `todo.bad`, and each line taken to
  `todo.taken`, with the host that took it.  A line waits while the same
  guest (a variant of it included) runs on the host: both would mount at
  `/mnt/ht1-src-GUEST` and `/mnt/ht1-dst-GUEST`, and vm-mount refuses the
  second (its own LV names make LVM's no matter, but for a thin, cache,
  RAID or snapshot group, which LVM still activates under its names).
- **Reservations.** Each job runs under `cmp-run.sh --reserve D M`, with
  `HT_RAM=1`, so a job waits for its room rather than run on disk, and only
  one job of ours waits in the ledger's line at a time; one that has waited
  30 minutes gets a warning in the runner's log, every 30 minutes, but
  keeps its place.  Guests whose D is `BIG_GIB` (13) or more run one at a
  time per host.  `HT_RAM=0` on the runner reserves nothing and works on
  /bkoscratch.
- **Ports.** The queue hands out 2600-2689 on every host (below each host's
  guest-slot range, testvm/HOSTS.md), and by-hand runs take 2690-2699;
  BASEPORT is always 2600 (the runner refuses another).  A
  job gets the first free port from BASEPORT on, held by a lock in
  `/tmp/bko-ht1-ports/` for its life; a port another program listens on,
  or a QEMU forwards, is skipped.
- **Binaries and settings.** `HT_HOLT`, `HT_HOLT_BIN`, `HT_BKO_BIN`, `HT_ROUND`,
  `HT_RUSTIC` and `HT_RAM` reach every job, and so do `HT_WORK` and
  `HT_RECORDS` (tests only: `/mnt/bko-ram/NAME` and `/bkoscratch/NAME`,
  one NAME); the runner logs them at its start.  It refuses to start while
  any other `HT_` variable is set, a debugging or test setting that would
  change every job's run (`HT_QUICK`, `HT_THROUGH`, `HT_KEEP`, `HT_CLEAN`,
  `HT_PORT`, `HT_LIMIT_KIB` and the like).  If rustic-bko's set changes
  during the run (or `HT_HOLT_BIN`'s `SHA256SUMS`, when it has one), it
  starts nothing more and a job still waiting in line leaves it: restart
  the queue to go on.  HoltTest2's `T2_RUN`, `T2_CELLS` and `T2_HOST_ROOT`
  reach the jobs too (their ledger rows), and the runner's `env:` line
  shows them.  It starts nothing more either
  after 3 jobs in a row that failed within 60 s, or a line `ram-size.sh`
  can't size.
- **Done.** `queue/RUN/done` (on the runner's host) has a row per job: its
  host, guest, exit, D, M, port, and its seconds waiting and running.  The
  runner logs to `queue/RUN/runner-HOST.log`, each guest to
  `/bkoscratch/holttest1d/RUN-GUEST.log`.  A failure isn't rerun by
  itself.
- **HoltTest2's queues** are these (testvm/test2/t2lib/holt.py): RUN is
  the run id (`t2h-YYYYMMDDTHHMM-XXXX`), its rerun's `RUN-r2`; the todo is
  on the first host, the canaries first, then the guests whose golden plan
  changed, then the rest, each part biggest first; a runner on every host
  from the run's private copy, with `HT_HOLT_BIN` its build, `HT_RAM=1`,
  `ONLY_ELSEWHERE` from the plan's pins and UEFI rescuers, and its log in
  `T2_HOST_ROOT/runs/RUN/runner-RUN.out`.  A stop rule touches `stop` on
  every host of the run, or takes one system's lines out of the todo
  (`todo.stopped`;
  redox and sculpt2610 are systems of their own there).  Only a FAIL goes
  into `RUN-r2`: a refusal, a signal or a sweep says nothing of the guest,
  and no rerun starts after `stop`, `abort` or a stop rule.  A job whose
  hook left no row gets one from its K's records (`t2-host row holt`), or,
  with none there, a HARNESS or LOST one.
- **Stop and abort** (on the runner's host): `touch queue/RUN/stop` starts
  nothing more; `queue/RUN/abort` also ends the running jobs, with TERM,
  once, to each job's driver and what it started (a vm-mount or vm-umount
  at work finishes first), and to its own QEMU (on its port, or with its
  TMPDIR, read through `sudo -n`), so each keeps its state on
  /bkoscratch.  Both files stay until removed: remove them before starting
  that RUN's runner again, which would otherwise end at once.
  `queue/RUN/max-HOST` holds a new MAXJOBS for HOST.  Nothing starts at
  90 °C or more, or with less than `MINFREE_GB` (80) free on /bkoscratch;
  above 95 °C the running jobs pause (SIGSTOP) until it's back under
  85 °C.  A runner that ends (TERM, INT, HUP) resumes the jobs it paused;
  one killed with -9 while they are paused leaves them stopped (state T in
  `ps`), holding their reservations: `sudo kill -CONT` each job's driver,
  everything under it, and its QEMU.
- **ONLY_ELSEWHERE** (guests, space-separated) are left in todo for the
  other host, and `redox` is too wherever `~/backotter-work/tools/redox` is
  missing (Chopin).  `openbsd8` and `dragonfly6` each need a one-time
  rescuer prep, which exists on one host only, openbsd8's on Chopin and
  dragonfly6's on Cinqcent: give `ONLY_ELSEWHERE=dragonfly6` to Chopin's
  runner and `ONLY_ELSEWHERE=openbsd8` to Cinqcent's, or each host makes
  its own.
- **What a host can't run.**  Before each take the runner reads the todo
  again and leaves queued, for another host, every line this host can never
  run: one whose D or M is over its ceiling (`cmp-run.sh --ceiling`); one
  whose guest has no image here (`~/backotter-work/vms/GUEST/*.qcow2`; a
  small host's cache holds the published guests only); a guest of
  `NEEDS_ZFS` (default `classes.json` `holt.needs_host_zfs`: chimera-zfs,
  debian13-zfs, ubuntu24, ubuntu26-zfs, ubuntu26-zfsmirror), every variant,
  on a host without OpenZFS's `zpool`; a Linux line (not `+img`) without
  the rescuer or bin/'s f2fs-tools and jfsutils debs; a Redox or Sculpt
  line without the rescuer.  The runner logs that list when it changes,
  and its start line records the ceiling, zpool, rescuer and debs.  These
  are the checks Test2's placement makes from the host's facts
  (testvm/HOSTS.md).  A line taken that turns out so anyway (the todo
  changed, or cmp-run.sh refused its `--reserve` as one that can never fit
  here) goes back on top of the todo instead of running, and never counts
  toward the 3-quick-failures stop; such a refusal also keeps that line off
  this host for the rest of the run.  `ht1-queue.sh --putback Q LINE HOST`
  is that step by hand (the queue host's half): LINE goes back on top and
  its `todo.taken` row goes, unless a Test2 stop took the guest's lines out
  (`@STOPPED`).  The skip list the runner hands `--pop` holds
  `ONLY_ELSEWHERE`'s guests as bare names (every line of the guest) and
  these lines as `=LINE` (that line only; a missing image skips the whole
  guest).  `NEEDS_ZFS` (space-separated) replaces the class table's list.

## Debugging bko-holt: ht1-debug.sh

For a fix that takes many tries (a RAID, LVM or ZFS layout load gets
wrong), `ht1-debug.sh` runs HoltTest1's phases for one guest entirely in
the RAM disk (`/mnt/bko-ram/holttest1d/debug/GUEST`), makes the backup
once, and rebuilds from a fresh rescuer and blank targets on each try with
the bko-holt just built.  Run as m, from the testvm/ copy with your changes:

    ht1-debug.sh GUEST setup          # reserve room in cmp-run.sh's ledger; dump, back up
    ht1-debug.sh GUEST build SRC      # bko-holt from SRC (holt/)
    ht1-debug.sh GUEST again [STAGE]  # rebuild through STAGE, no safety checks; rescuer kept up
    ht1-debug.sh GUEST ssh [CMD]      # look inside that rescuer
    ht1-debug.sh GUEST verify         # HoltTest1's verify of the last full rebuild
    ht1-debug.sh GUEST done           # stop the VMs, free the room, delete the run

`full` is a rebuild with the safety checks, `status` where things stand,
with each phase's time and RAM-disk peak (`logs/phases.tsv`).  setup
reserves the run's room from `ram-size.sh` plus 2 GiB for the build (or
the GIB it is given).  The run stays in RAM as it is between calls
(`HT_CLEAN=0`, with `HT_RESERVE_PID` naming setup's reservation): nothing
of it is deleted, or moved to /bkoscratch, until `done` deletes it (`done`
refuses while a step is still at work).  Linux bko-holt builds on the host
(cargo's target/ in RAM); the others in a build guest that
`build-holt.sh --keep` keeps up between builds, so cargo builds only what
changed.
`HT_PORT` (default 2698; take one in 2690-2698, since the build guest's is
HT_PORT + 1 and both must stay in by-hand runs' 2690-2699) keeps two
debugging sessions on one host apart; the run's
VMs are found by their disks in its directory, never by the port.
Underneath, ht1.sh and ht1-bsd.sh take `HT_RAM`, `HT_CLEAN`,
`HT_RESERVE_PID`, `HT_HOLT`, `HT_QUICK`, `HT_THROUGH` and `HT_KEEP` (their
headers).  Measured 2026-11-07: rocky10-raid5 (md RAID5 under LVM) setup 43 s, build
19 s, `again partition` 42 s, a whole rebuild 91 s, verify 38 s;
freebsd15-mirror setup 142 s, a cold build 51 s, an incremental one 30 s, a
whole rebuild 38 s, verify 81 s.  A HoltTest1 run of one guest is 15-25
minutes.  Confirm a fix with a normal run after.

## Before it can run

1. bko-holt `load`'s writing stages, `partition` through `close` (with
   `restore`).  `plan` and `status` exist, and `plan` already prints every
   command the others will run.
2. `vm-run --uefi-vars FILE`, so the rescue VM and the restored boot share one
   UEFI variable store.  Today every run gets a fresh copy.
3. ~~9p in the rescue image's kernel (`9pnet_virtio`, `9p`)~~: there, and
   `+img` mounts the share with it on Linux (2026-11-10).  ht1-bsd.sh's
   `+img` runs take the repository on a disk instead, as its other runs do:
   the rescuer guest's, attached after the decoy and mounted read-only at
   `/tmp/bko-repo`.
4. ~~A builder for `debian13-stack`~~: done, `testvm/make-debian-stack.sh`
   (built from the host, since Debian's installer can't preseed LUKS on md),
   with an entry in `rebuild-one.sh`; built on Cinqcent and copied
   byte-identical to Chopin.  Its initramfs asks for the LUKS passphrase on
   the serial console; `vms/debian13-stack/console-passphrase` holds it.
5. The driver, `testvm/holttest1d/check.sh GUEST [--bigger]`, with
   `restore.sh` and `judge.py` beside it.  It reuses `testvm/compare`'s
   `torture.sh`, `fsmeta.py`, `boot-state.sh` and its mount helpers.

## Not in HoltTest1D

- The other systems: see "The HoltTest1 family".
- `--new-uuids`; disks in a different number, which is version 2 of
  `load`.  Smaller disks only with `--fit-largest` ("The fitting variants").
- Restoring onto the machine the stick runs on, or onto real hardware.
- restic-bko: the test restores with rustic-bko only, as asked.  Adding
  restic-bko later means one more pass of steps 2 and 3.
