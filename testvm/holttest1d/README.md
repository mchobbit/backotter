# HoltTest1D: a whole-machine round trip of bko-holt on Debian 13

Status: 2026-11-05.  The plan below is the goal.  What runs today is
`ht1.sh`, an interim driver with its own rescuers; see "Running it today".
It runs HoltTest1D and its siblings, the same test on other systems; see
"The HoltTest1 family".

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
| 1D | Debian 13 | `debian13`, `debian13-zfs`, `debian13-stack` | passes |
| 1M | Linux Mint | `mint22` | passes |
| 1U | Ubuntu | `ubuntu24`, `ubuntu26-uefi`, `ubuntu26-zfsmirror`, `ubuntu26-raid` (and `degraded`) | passes |
| 1Z | Zorin OS | `zorin18` | passes |
| 1F | Fedora, Rocky Linux | `fedora44`, `fedora44-uefi`, `fedora44-raid`, `rocky10`, `rocky10-lvm`, `rocky10-raid5` | passes (`fedora44-raid` and `rocky10-raid5`: run `raid022`, below) |
| 1A | Arch, Alpine, AerynOS, Artix (dinit, s6) | `arch`, `alpine324`, `aerynos`, `artix-dinit`, `artix-s6` | passes |
| 1C | CachyOS, MX Linux | `cachyos`, `mx25` | passes |
| 1V | Void Linux (musl) | `void-musl` | passes |
| 1Obscure | Pop!_OS, NixOS, Omarchy, Sculpt OS, Redox, openSUSE Tumbleweed, Debian 12 | `pop24`, `nixos2605`, `omarchy`, `sculpt2610`, `redox`, `tumbleweed`, `debian12` | passes |
| 1H | Haiku | `haiku6` | passes (but for a known rustic-bko limit: the volume root's mtime) |
| 1I | illumos | `omnios58`, `omnios58-mirror` | both pass (`omnios58-mirror`: run `raid6`, the restored `rpool` ONLINE on both disks) |
| 1BSD | FreeBSD, OpenBSD, NetBSD, GhostBSD, HardenedBSD, DragonFly BSD, AlpineBSD | `freebsd15`, `freebsd15-ufs`, `freebsd15-mirror`, `openbsd8`, `netbsd11`, `ghostbsd26`, `hardenedbsd15`, `dragonfly6`, `alpinebsd` | passes |

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

**HoltTest1All** (the user's definition, 2026-11-05): "like HoltTest1D
except that HoltTest1All targets every single guest OS we have": every guest
in `vms/` on the test hosts, 39 as of 2026-11-05 (the same set on Chopin and
Cinqcent), each with `ht1.sh`.  It is the union of the tests above plus the
guests no other test names yet: `devuan6`, `ubuntu26-zfs` and `freebsd14`.
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
filled, and is to be rerun from the start on one committed bko-holt.

## Systems other than Linux: ht1-bsd.sh and ht1-offline.sh

`ht1.sh` hands a guest that isn't Linux to a driver of its own; the steps and
verdicts are HoltTest1D's.

**ht1-bsd.sh** (FreeBSD, HardenedBSD, GhostBSD, NetBSD, AlpineBSD, OpenBSD,
DragonFly, illumos, Haiku) runs bko-holt built for the guest's own system
(`build-holt.sh PLATFORM`: natively, in a throwaway copy of a guest of that
system, into `bin/PLATFORM/bko-holt`) and its rustic-bko
(`~/backotter-work/bko-bin/PLATFORM/`).  The rescuer is a throwaway copy of
a guest of the same system, whose own disk comes first:

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
driver from `~/backotter-work/tools/redox`, so Redox runs on Cinqcent;
Sculpt's GENODE ext2 read-only); a boot is checked by logging in as root on
Redox's serial console (`redox/console.py`), and by `sculpt-check.sh` for
Sculpt.  The rebuild is `ht1.sh`'s, in the Linux rescuer.

Other tools these runs need:

- `make-bcachefs-tools.sh` builds bcachefs-tools 1.13 (NixOS's root) for
  the Debian rescuer, which lacks them: the 6.12 kernel has bcachefs, Debian
  13 dropped the tools, and newer tools format a version 6.12 can't mount.
- A guest whose LUKS passphrase is typed at Plymouth (Omarchy's
  `luks-passphrase`) gets it typed on its keyboard through QEMU's monitor
  (`omarchy/type-luks-passphrase.py`), as compare.py does.

## Running it today: ht1.sh

`ht1.sh GUEST RUN [PHASE...]`, run as root on a test host through the
reservation queue (as `m`, which owns the queue's lock files):

    cd ~/backotter-work && HT_PORT=2970 setsid -f ./cmp-run.sh --reserve 0 4 \
        sudo -E testvm/holttest1d/ht1.sh mint22 ht1m >/bkoscratch/holttest1d/ht1m.log 2>&1 </dev/null

It needs `/bkoscratch/holttest1d/bin/` with `bko-holt`, `rustic-bko` (since
2026-11-07 a copy of the shipped `~/backotter-work/bko-bin/linux/rustic-bko`,
which the images carry; the patched build Linux runs used before is kept as
`rustic-bko.backotter4`) and
`f2fs-tools_*.deb` (installed in a rescuer that lacks the f2fs tools), and
the rescuers that `make-rescuer.sh` builds (see below).  The guest's other
disks and firmware come from its `vm-run-options`; a guest with a
`console-passphrase` or `zfs-passphrase` has it typed at each boot
(`testvm/console-boot.py`), given to `vm-mount --key`, and given to
`load --key-file` for each LUKS volume and passphrase-keyed ZFS root.  The
phases are `source`, `backup`, `rebuild` and `verify`; each needs the one
before it, and the run stops at the first that fails.  After a bko-holt fix,
`rebuild verify` is enough, unless the fix changed what `dump` writes.

The plan's variants are chosen with `HT_VARIANT`, each in a directory of
its own (`RUN/GUEST-VARIANT`): `oneshot` runs the rebuild as one
`--through close --commit`, and `bigger` rebuilds onto disks 1 GiB bigger
than the source's, judged with `judge.py --grow`, which lets only a disk's
size, GPT's last usable LBA and backup header and the protective MBR move
by exactly that much.  `degraded` runs the source with its last disk
detached, a RAID member's, so its arrays are degraded when it is dumped
and backed up; `load` recreates them with `missing` slots onto one disk
fewer, and the restored machine must boot degraded with the same slots
empty (`/proc/mdstat`'s `[UU_]`), judged with `judge.py --degraded`.  It
runs on the guests whose arrays survive the loss of their last disk:
`debian13-stack` and `ubuntu26-raid`.  A full HoltTest1D is six runs:
`debian13`, `debian13-zfs` and `debian13-stack`, `debian13-stack` with
`bigger`, and `debian13` with `oneshot` on each host.  Never run two copies
of `debian13-stack` on one host at once, nor beside a `compare.py` of it:
`vm-mount` refuses a second `debian13-stack-vg`, since LVM's names are the
host's (nor two of `ubuntu26-raid`, for its `ubuntu-vg`).

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

It differs from the plan above in these ways:

- **The rescuer** is a throwaway overlay of a rescuer image, driven over
  SSH, not BackOtter's rescue `.img`.  `make-rescuer.sh` builds
  `/bkoscratch/holttest1d/rescuer/rescuer.qcow2` from the `debian13` guest
  (BIOS; OpenZFS 2.4 from trixie-backports, every mkfs, nothing of its own
  imported), and with `--uefi`, `rescuer-uefi.qcow2` from `ubuntu26-uefi`
  (UEFI; Ubuntu's OpenZFS 2.4).  A UEFI source is rebuilt from the UEFI
  one, as the stick boots in the machine's firmware mode: grub-mkconfig
  writes a UEFI grub.cfg only there.  Build them on one host, copy them
  to the other.  The `.img` needs bko-holt in the stick layer, an SSH key
  (or the serial console) for `make-img.sh --test` images, and f2fs-tools,
  nilfs-tools and 9p in the image.
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

`full` is a rebuild with the safety checks, `status` where things stand.
Linux bko-holt builds on the host (cargo's target/ in RAM); the others in a
build guest that `build-holt.sh --keep` keeps up between builds, so cargo
builds only what changed.  `HT_PORT` (default 2991; the build guest
HT_PORT + 1) keeps two debugging sessions on one host apart.  Underneath,
ht1.sh and ht1-bsd.sh take `HT_RAM`, `HT_HOLT`, `HT_QUICK`, `HT_THROUGH`
and `HT_KEEP` (their headers).  Measured 2026-11-07: rocky10-raid5 (md
RAID5 under LVM) setup 43 s, build 19 s, `again partition` 42 s, a whole
rebuild 91 s, verify 38 s; freebsd15-mirror setup 142 s, a cold build 51 s,
an incremental one 30 s, a whole rebuild 38 s, verify 81 s.  A HoltTest1 run
of one guest is 15-25 minutes.  Confirm a fix with a normal run after.

## Before it can run

1. bko-holt `load`'s writing stages, `partition` through `close` (with
   `restore`).  `plan` and `status` exist, and `plan` already prints every
   command the others will run.
2. `vm-run --uefi-vars FILE`, so the rescue VM and the restored boot share one
   UEFI variable store.  Today every run gets a fresh copy.
3. 9p in the rescue image's kernel (`9pnet_virtio`, `9p`).  If it's missing,
   the share becomes a small read-only virtio disk with serial `bko-share`.
   `load` must then be given that disk as off-limits.
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
- `--new-uuids`; disks smaller than the source, or in a different number,
  which is version 2 of `load`.
- Restoring onto the machine the stick runs on, or onto real hardware.
- restic-bko: the test restores with rustic-bko only, as asked.  Adding
  restic-bko later means one more pass of steps 2 and 3.
