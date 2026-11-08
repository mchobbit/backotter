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
| 1D | Debian 13 | `debian13`, `debian13-zfs`, `debian13-stack` | passes |
| 1M | Linux Mint | `mint22` | passes |
| 1U | Ubuntu | `ubuntu24`, `ubuntu26-uefi`, `ubuntu26-zfsmirror`, `ubuntu26-raid` (and `degraded`) | passes |
| 1Z | Zorin OS | `zorin18` | passes |
| 1F | Fedora, Rocky Linux | `fedora44`, `fedora44-uefi`, `fedora44-raid`, `rocky10`, `rocky10-lvm`, `rocky10-raid5` | passes, but for `fedora44-raid` and `rocky10-raid5` (bko-holt defects, below) |
| 1A | Arch, Alpine, AerynOS, Artix (dinit, s6) | `arch`, `alpine324`, `aerynos`, `artix-dinit`, `artix-s6` | passes |
| 1C | CachyOS, MX Linux | `cachyos`, `mx25` | passes |
| 1V | Void Linux (musl) | `void-musl` | passes |
| 1Obscure | Pop!_OS, NixOS, Omarchy, Sculpt OS, Redox, openSUSE Tumbleweed, Debian 12 | `pop24`, `nixos2605`, `omarchy`, `sculpt2610`, `redox`, `tumbleweed`, `debian12` | passes |
| 1H | Haiku | `haiku6` | passes (but for a known rustic-bko limit: the volume root's mtime) |
| 1I | illumos | `omnios58`, `omnios58-mirror` | both pass (`omnios58-mirror`: run `raid6`, the restored `rpool` ONLINE on both disks) |
| 1BSD | FreeBSD, OpenBSD, NetBSD, GhostBSD, HardenedBSD, DragonFly BSD, AlpineBSD | `freebsd15`, `freebsd15-ufs`, `freebsd15-mirror`, `openbsd8`, `netbsd11`, `ghostbsd26`, `hardenedbsd15`, `dragonfly6`, `alpinebsd` | passes |

**The RAID guests** (2026-11-07, run `raid1`), each with a `fresh-install`
snapshot on every disk:

- `fedora44-raid` (`make-fedora.sh --uefi --raid`): two disks, /boot/efi
  on md RAID1 with metadata 1.0, /boot on md RAID1, / on btrfs RAID1
  (data and metadata).  Fails at `assemble`: `load` passes metadata 1.0
  `--data-offset=0K`, which mdadm refuses (`invalid data-offset: 0K`).
- `ubuntu26-raid` (`make-ubuntu.sh --uefi --raid`): four disks, an ESP on
  each (GRUB kept on all four), md RAID10 (near-2) holding LVM with / ext4
  and swap.  Passes, and passes `degraded`.
- `rocky10-raid5` (`make-rocky.sh --raid5`): three disks, BIOS, /boot on md
  RAID1 across all three, md RAID5 (left-symmetric, internal bitmap) holding
  LVM with / XFS and swap.  Fails the dump check: `load`'s mkfs.xfs gives
  `agcount` but neither the data size nor the AG size, so the rescuer's
  xfsprogs makes another geometry (AG 458496 blocks, not 458368) and fills
  the LV, where the source's XFS stopped 4 MiB short of its end.

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

It needs `/bkoscratch/holttest1d/bin/` with `bko-holt` and
`f2fs-tools_*.deb` (installed in a rescuer that lacks the f2fs tools), and
the rescuers that `make-rescuer.sh` builds (see below).  ht1.sh copies
bko-holt (or `HT_HOLT`'s) as the run starts, so its dumps and its load are
one build's even if bin/'s is replaced while it runs; ht1-bsd.sh and
ht1-offline.sh still take it from bin/ (or `HT_HOLT`) at each phase, so
leave bin/'s alone while one runs.  rustic-bko is the shipped set's
(`~/backotter-work/bko-bin/linux/rustic-bko`, checked against its
`SHA256SUMS`); `HT_ROUND=NAME` takes round NAME's instead, and
`HT_RUSTIC=FILE` a build of your own, unchecked.  The guest's other
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
of `debian13-stack` on one host at once, in any variant, nor beside a
`compare.py` of it: `vm-mount` refuses a second `debian13-stack-vg`, since
LVM's names are the host's (nor two of `ubuntu26-raid`, for its
`ubuntu-vg`).  Nor run one guest twice on a host under two RUN names: both
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
without a row gets a default.  Its header has the details.

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

A runner started before its todo exists finds nothing to do and ends
(`queue done`).  One that refuses to start (exit 2: an `HT_` setting, run
as root, no RAM disk) says why in its `.out` file only: its log,
`queue/RUN/runner-HOST.log`, begins once it runs.

- **todo** has a line per guest, `GUEST[+VARIANT][=D][%M]` (`#` starts a
  comment): `+bigger`, `+oneshot` or `+degraded` is `HT_VARIANT`, and `=D`
  and `%M` override ram-size.sh's sizes.  `ht1-queue.sh --order GUEST...`
  prints guests biggest first, for writing it.  Lines are taken from the
  top; a line that doesn't parse goes to `todo.bad`, and each line taken to
  `todo.taken`, with the host that took it.  A line waits while the same
  guest (a variant of it included) runs on the host: vm-mount refuses a
  second LVM volume group of one name.
- **Reservations.** Each job runs under `cmp-run.sh --reserve D M`, with
  `HT_RAM=1`, so a job waits for its room rather than run on disk, and only
  one job of ours waits in the ledger's line at a time; one that has waited
  30 minutes gets a warning in the runner's log, every 30 minutes, but
  keeps its place.  Guests whose D is `BIG_GIB` (13) or more run one at a
  time per host.  `HT_RAM=0` on the runner reserves nothing and works on
  /bkoscratch.
- **Ports.** The queue hands out 2600-2689 on both hosts, and by-hand runs
  take 2690-2699; BASEPORT is always 2600 (the runner refuses another).  A
  job gets the first free port from BASEPORT on, held by a lock in
  `/tmp/bko-ht1-ports/` for its life; a port another program listens on,
  or a QEMU forwards, is skipped.
- **Binaries and settings.** `HT_HOLT`, `HT_BKO_BIN`, `HT_ROUND`,
  `HT_RUSTIC` and `HT_RAM` reach every job, and so do `HT_WORK` and
  `HT_RECORDS` (tests only: `/mnt/bko-ram/NAME` and `/bkoscratch/NAME`,
  one NAME); the runner logs them at its start.  It refuses to start while
  any other `HT_` variable is set, a debugging or test setting that would
  change every job's run (`HT_QUICK`, `HT_THROUGH`, `HT_KEEP`, `HT_CLEAN`,
  `HT_PORT`, `HT_LIMIT_KIB` and the like).  If rustic-bko's set changes
  during the run, it starts nothing more and a job still waiting in line
  leaves it: restart the queue to go on.  It starts nothing more either
  after 3 jobs in a row that failed within 60 s, or a line `ram-size.sh`
  can't size.
- **Done.** `queue/RUN/done` (on the runner's host) has a row per job: its
  host, guest, exit, D, M, port, and its seconds waiting and running.  The
  runner logs to `queue/RUN/runner-HOST.log`, each guest to
  `/bkoscratch/holttest1d/RUN-GUEST.log`.  A failure isn't rerun by
  itself.
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
