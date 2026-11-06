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
| 1U | Ubuntu | `ubuntu24`, `ubuntu26-uefi`, `ubuntu26-zfsmirror` | passes |
| 1Z | Zorin OS | `zorin18` | passes |
| 1F | Fedora, Rocky Linux | `fedora44`, `fedora44-uefi`, `rocky10`, `rocky10-lvm` | passes |
| 1A | Arch, Alpine, AerynOS, Artix (dinit, s6) | `arch`, `alpine324`, `aerynos`, `artix-dinit`, `artix-s6` | passes |
| 1C | CachyOS, MX Linux | `cachyos`, `mx25` | passes |
| 1V | Void Linux (musl) | `void-musl` | passes |
| 1Obscure | Pop!_OS, NixOS, Omarchy, Sculpt OS, Redox, openSUSE Tumbleweed, Debian 12 | `pop24`, `nixos2605`, `omarchy`, `sculpt2610`, `redox`, `tumbleweed`, `debian12` | passes |
| 1H | Haiku | `haiku6` | passes (but for a known rustic-bko limit: the volume root's mtime) |
| 1I | illumos | `omnios58` | passes |
| 1BSD | FreeBSD, OpenBSD, NetBSD, GhostBSD, HardenedBSD, DragonFly BSD, AlpineBSD | `freebsd15`, `freebsd15-ufs`, `openbsd8`, `netbsd11`, `ghostbsd26`, `hardenedbsd15`, `dragonfly6`, `alpinebsd` | passes |

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
too: the source's disk attached, mounted read-only from the layout, backed up
into the rescuer's own disk (`R/rescuer.qcow2`, kept from backup to verify).
The file check is mtree(8)'s (a spec of the source, sockets left out, checked
against the restored tree), or on Haiku and illumos GNU find's listing and
every file's SHA-256.  The boot state is the enabled services that say they
aren't running (FreeBSD's `service -e`, OpenBSD's `rcctl ls failed`,
illumos' `svcs`).

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

It needs `/bkoscratch/holttest1d/bin/` with `bko-holt`, `rustic-bko` and
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
by exactly that much.  A full HoltTest1D is six runs: `debian13`,
`debian13-zfs` and `debian13-stack`, `debian13-stack` with `bigger`, and
`debian13` with `oneshot` on each host.  Never run two copies of
`debian13-stack` on one host at once, nor beside a `compare.py` of it:
`vm-mount` refuses a second `debian13-stack-vg`, since LVM's names are the
host's.

Each run also checks, as the plan asks:

- a decoy disk after the targets, holding an ext4 filesystem, is refused
  without `--erase` (for not being blank) and still holds it at the end;
- the refusals and the dry run leave every target's and the decoy's first
  and last MiB as they were;
- the restored machine's health: `findmnt --verify`, every fstab mount
  mounted, swap on, `zpool status -x`, md arrays clean or active, every
  crypttab volume open, and load's log kept.  The same checks run on the
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
