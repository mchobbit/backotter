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
| 1F | Fedora, Rocky Linux | `fedora44`, `fedora44-uefi`, `rocky10`, `rocky10-lvm` | the Fedora guests pass; Rocky not run |
| 1A | Arch, Alpine, AerynOS, Artix (dinit, s6) | `arch`, `alpine324`, `aerynos`, `artix-dinit`, `artix-s6` | `arch` passes; the rest not run |
| 1C | CachyOS, MX Linux | `cachyos`, `mx25` | not run |
| 1V | Void Linux (musl) | `void-musl` | not run |
| 1Obscure | Pop!_OS, NixOS, Omarchy, Sculpt OS, Redox, openSUSE Tumbleweed, Debian 12 | `pop24`, `nixos2605`, `omarchy`, `sculpt2610`, `redox`, `tumbleweed`, `debian12` | not run |
| 1H | Haiku | `haiku6` | not run |
| 1I | illumos | `omnios58` | not run |
| 1BSD | FreeBSD, OpenBSD, NetBSD, GhostBSD, HardenedBSD, DragonFly BSD, AlpineBSD | `freebsd15`, `freebsd15-ufs`, `openbsd8`, `netbsd11`, `ghostbsd26`, `hardenedbsd15`, `dragonfly6`, `alpinebsd` | not run |

What the ones not run need, as far as is known before running them:

- **Supported systems:** bko-holt's list (`holt/src/osrel.rs`) lacks Alpine,
  AerynOS, Artix, CachyOS, MX, Void, Pop!_OS, NixOS, Rocky, openSUSE and
  Debian 12.
- **Filesystems:** JFS (`cachyos`'s and `artix-dinit`'s roots) isn't probed
  or made.
- **Boot tools:** Alpine's mkinitfs, AerynOS's blsforme, Pop!_OS's
  kernelstub, NixOS's nixos-rebuild, Void's and openSUSE's own; `load`
  knows update-initramfs, mkinitcpio and dracut, GRUB and `bootctl install`.
- **Init systems:** `ht1.sh`'s boot-state check uses systemctl; OpenRC,
  runit, dinit, s6 and MX's sysvinit need their own.
- **Keyboard passphrases:** Omarchy's LUKS prompt is Plymouth's, typed on the
  keyboard (compare.py's `type-luks-passphrase.py`), not on the console.
- **Not Linux (1H, 1I, 1BSD, Sculpt, Redox):** bko-holt reads Linux's /sys,
  /proc and udev and is a Linux binary.  Each needs a `dump` and `load` of
  its own, a rescuer of its own kind (`stick/make-img-bsd.sh` builds
  BackOtter's BSD, illumos and Haiku images), and `ht1.sh`'s Linux-only
  parts (`vm-mount`, `fsmeta.py`) replaced, as `testvm/compare/freebsd/`
  does for compare.py.

## The source guests

The two Debian 13 guests there are today both use BIOS + MBR on one disk.
That leaves GPT, UEFI, md, LUKS, LVM and XFS untested, so HoltTest1D adds a
third guest:

| Guest | Firmware, table | Layout | Exists |
|---|---|---|---|
| `debian13` | BIOS, MBR | /boot ext4, swap, / btrfs | yes |
| `debian13-zfs` | BIOS, MBR | /boot ext4, swap, ZFS `rpool` with the OpenZFS guide's datasets | yes |
| `debian13-stack` | UEFI (Secure Boot), GPT, two disks | ESP on each disk; md RAID1 /boot; LUKS2 on md RAID1, then LVM with / ext4, /srv XFS and swap | yes (`make-debian-stack.sh`) |

`debian13-stack` also runs a second time onto disks 1 GiB bigger than its own,
to check that the GPT backup header moves to the new end of the disk.

## Where the temporary backup goes

On the host's scratch disk, beside everything else the run makes:

```
/bkoscratch/holttest1d/RUN/GUEST/       RUN = UTC start time, e.g. 20261105T1830Z
    share/                  shared read-only into the rescue VM (9p, tag bkoshare)
        repo/               the rustic-bko repository: the temporary backup
        repo-password       random, made for this run
        layout.json         bko-holt dump's output from the source
        bko-holt            the build under test
        rustic-bko          the archiver under test
        restore.sh          the steps the rescue VM runs
        keys/               --key-file passphrases (debian13-stack)
    src.qcow2               throwaway overlay on the guest's own image
    dst-0.qcow2 ...         the blank target disks
    efivars.fd              the rescue VM's UEFI variables, kept for the restored boot
    logs/  dumps/  report.json
```

This location is chosen because:

- **It's on the right disk.** `/bkoscratch` is the SK900 on both hosts, where
  heavy writes belong.  It is not Chopin's hot P3 root, and not the
  `/mnt/bko-ram` tmpfs, since disk comes before RAM as of 2026-11-04.  A
  16 GiB guest needs roughly 10 GiB of scratch space at peak; there are
  197 GiB free on Chopin.
- **Neither guest sees it as a disk.** If the repository were on a virtual
  disk attached to the source, it would show up in the source's dump and
  become part of the layout to rebuild.  If it were on the target, `load`
  would erase it.  In the rescue VM it is a read-only 9p share, so neither
  the restore nor `load` can damage it.
- **Cleanup is one directory.** On a pass, the run deletes `RUN/GUEST/`
  except `logs/`, `dumps/` and `report.json`.  On a failure, it keeps all of
  it.

## One run, per guest

**0. Before starting**
- The host is healthy: temperatures, free RAM, and nothing of ours left over.
- Scratch space and RAM are reserved through `cmp-run.sh --reserve`.
- bko-holt is built with `--release` on the host, using Debian 13's cargo.
- rustic-bko is the newest build in `~/backotter-work/rustic/dist`.
- The rescue image is the newest `backotter-live-linux-*.img` built with
  `make-img.sh --test`.

**1. Source**
- `src.qcow2`, a throwaway overlay, is created on the guest's image.  The
  guest's own image is never written.
- `torture.sh` puts its hard-to-preserve files into the overlay, as
  `compare.py` does.
- The source boots and `boot-state.sh` records its baseline.
- `bko-holt dump` runs inside the guest over SSH and writes
  `dumps/src.json`, which is also `share/layout.json`.  It runs on the live
  system because it needs the live mounts, efivars and debconf.
- The source shuts down cleanly.

**2. Backup**
- The host mounts `src` read-only, as `compare.py` does.
- `rustic-bko backup --set-atime yes` backs up `/` and every filesystem
  mounted below it into `share/repo`.
- The backup is offline, so the tree can't change while it's being read, and
  the layout dump has already captured what only the live system knows.

**3. Rebuild, from the rescue image**
- A VM boots the rescue `.img`. It has the same firmware as the source, blank
  target disks (same count, same logical sector size, each the same size or
  bigger), one small decoy disk that already holds a filesystem, and the
  share.
- The harness drives it on the serial console, as `boot-test.py` does, and
  runs `share/restore.sh`.
- **Safety checks first; each must refuse, and leave the disks untouched** (first and last MiB unchanged):
  - `load` without `--map`: exit 4, listing source and target disks;
  - `--through partition` without `--commit`: plan only, nothing written;
  - `--map` onto the decoy without `--erase`: refused;
  - `--map` onto the disk the rescue image booted from: refused even with
    `--erase`.
- **The rebuild**, one stage at a time, so a failure names its stage:
  - `load --map SRC=DST... --erase DST... --commit --only STAGE` for
    `partition`, `assemble`, `format`, `mount`, `restore`, `boot` and
    `close`, in that order;
  - `restore` gets `--repo share/repo --password-file share/repo-password
    --archiver share/rustic-bko`, and runs rustic-bko itself;
  - `close` exports the pools and closes the volumes;
  - after each stage, `status` must report that stage as done and name the
    next one.
- Once per host, `debian13` also runs the whole rebuild as one command,
  `--through close --commit` with the same flags, as an administrator would.
- Any stage's exit code other than 0 stops the run there.  The stage's name
  goes in the report.
- The VM powers off.  `efivars.fd` keeps the boot entry that the `boot`
  stage made.

**4. The restored machine boots by itself**
- It boots from the target disks alone, without the rescue image, with the
  source's firmware and `efivars.fd`.
- It is reached over SSH with the guest's own key.  Its host keys were
  restored too, so the known_hosts entry must match.
- `boot-state.sh` runs.  A service that failed here but not in the source's
  baseline is a failure.
- Health checks, each of which must pass:
  - `findmnt --verify`;
  - every fstab mount is mounted;
  - swap is on;
  - `zpool status -x` is healthy and `rpool` was imported at boot without
    help;
  - `mdadm --detail` shows clean arrays;
  - `cryptsetup status` shows the volume open;
  - `/var/log/bko-holt/` holds `load`'s logs.
- `bko-holt dump` writes `dumps/dst.json`.
- The machine shuts down cleanly.

**5. Judge**
- **The two dumps agree.** `judge.py` compares `src.json` and `dst.json`
  field by field: every partition table and partition identifier, type,
  start, size and attribute; every md, LUKS, LVM and ZFS identifier and
  setting; every filesystem's UUID, label, features and geometry; fstab,
  crypttab, the boot loader and its configuration.
  - It ignores only a written list: things that change on every boot or are
    QEMU's (timestamps, usage, serials and by-id links, the running kernel,
    BootCurrent, EFI entry numbers), and what the design's Identity section
    says can't be kept (ZFS vdev GUIDs, new LUKS key slots).
  - Every ignored field is named in the report, so the list can't grow
    silently.
- **The files agree.** `fsmeta.py` compares every file's metadata between
  `src` and the restored disks, both mounted read-only.
- **Boot state agrees,** as in step 4.

## Verdicts and the report

Each guest's verdict is one of:

- **pass**: every check above held;
- **fail**: names the stage or check, and classes the cause like SticTest1:
  - a bko-holt bug goes in the queue;
  - a harness bug, the harness is fixed;
  - a Debian, QEMU or OVMF limit is recorded;
- **blocked**: something in "Before it can run" is missing.

`report.json`, with a Markdown table built from it, has one row per guest and
variant, with:

- the verdict;
- the layout in one line;
- each stage's time;
- the dump differences: counted, with each one listed;
- the fsmeta differences;
- services that failed only after the restore;
- the scratch space used at peak.

## Hosts

The guests are split as SticTest1 splits them:

- **Chopin:** `debian13-zfs`. It keeps the bcachefs-free ZFS work it already
  does, and boost stays off.
- **Cinqcent:** `debian13`, then both `debian13-stack` variants.

Each run has its own names, prefixed `ht1d`: mount points, the temporary pool
name, md and LUKS names.  SSH ports are 2260–2269 on Chopin and 2960–2969 on
Cinqcent, clear of `compare.py`'s 2230+N.  Never touch another session's VMs,
ports or pools.  Scripts are synced with `testvm/sync-hosts.sh` from main
only, and nothing syncs while a run is going.

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
