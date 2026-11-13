# BackShot2 and BackShot2Alpine: restores onto layouts that drifted (approved)

2026-11-10. Approved by the user the same day, with the recommended decisions;
nothing is built yet. BackShot2 is the user's design (their description,
condensed in "What BackShot2 is"); this spec fills it in and makes
BackShot2Alpine, the user's other new test group, its first milestone.
BackShot2Alpine was first called BackShotAlpine; the user renamed it.

**Conventions.** est. marks an estimate. A **case** is one drifted layout plus
everything needed to run it again. **Tier** numbers run cheapest first.
`layload.md` is `docs/design/layload.md`; `test2.md` is `docs/design/test2.md`.

## Summary

- BackShot2 takes a real layout (a test guest's dump, or a BackShot1 backup's
  `layout.json`), changes it in small, seeded, random ways a real machine could
  have drifted into, and checks that `bko-holt load` builds it, restores onto it
  and boots it.
- Every change obeys three rules: the data still fits; the source OS's own tools
  could have made it; nothing on the restored system needs editing.
  So a failure means a bko-holt (or rustic-bko) bug, not an impossible test.
- Four tiers: plan only (no VM); QEMU; QEMU with real UEFI boot entries; the
  Thinkpad. Each case is a file of exact edits, so it replays without
  randomness. A failure shrinks to the smallest edit list that still fails,
  and joins a corpus that reruns on every new bko-holt or rustic-bko.
- **BackShot2Alpine is milestone 1:** the same machinery, with Alpine as the only
  source. Alpine restores in seconds and boots in ~30 s, so the QEMU tier runs
  several times more cases an hour than on other systems (est.). One OS also
  means one set of tools and one initramfs builder, so a difference between two
  cases can only come from the layout, the options or the files.
- BackShot2Alpine adds two things BackShot2's description leaves out:
  - **content drift**, for rustic-bko: a seeded tree of awkward files planted before
    the backup;
  - **an option sweep** of load's flags.
- Build: ~7 days for milestone 1, ~3 more for the other Linux sources (est.).
  The generator remains the uncertain part.

## Why

1. **Real disks drift; the corpus doesn't.** The 45 corpus dumps are fresh
   installs: aligned partitions, numbered in order, one LV segment each, the
   installer's mkfs defaults. A machine that's been resized, had partitions
   deleted and added, or an LV extended, has none of that. All four load fixes
   since bring-up touched commands.rs (test2.md, HoltTest2 scope 1), the code
   that turns a layout's numbers into commands, which drift exercises most.
2. **Hand-written variants run out.** `+bigger`, `+degraded` and `--fit-largest`
   cover the cases someone thought of. A generator covers the ones nobody
   thought of, and the shrinker turns them into a readable minimal case.
3. **BackShot1 found real bugs on real machines** (Tumbleweed's shim fallback,
   Debian 12's mawk), but each run needs an install and covers one layout.
   BackShot2 multiplies one install into thousands of layouts.
4. **On one OS, a failure means BackOtter.** Across distros, a failure may come
   from the distro (its initramfs, its loader, its tools). On Alpine alone it
   can't, which is the point of BackShot2Alpine.

## What BackShot2 is

The user's description, condensed:

- **Input:** a real layout, either a test guest's or a backup from BackShot1's
  library.
- **The changes:**
  - gaps between partitions;
  - odd alignment;
  - out-of-order numbering;
  - resized partitions;
  - LVM split into pieces;
  - different LUKS header and filesystem geometry;
  - smaller or bigger target disks.
- **The rules:** each change keeps the layout valid. The data must still fit,
  the OS's own tools must have been able to make it, and nothing on the
  restored system has to be edited.
- **Phase 1:** Linux only, keeping each system's basic shape. Shape changes come
  later: `/home` moved to its own partition, encryption added or removed,
  UEFI and BIOS swapped.
- **Tiers:**
  1. plan only, thousands of cases an hour;
  2. QEMU guests, about 50 cases an hour across both hosts, sometimes with a
     second backup and restore of the changed system;
  3. cases that touch the ESP, in QEMU with real boot entries;
  4. the Thinkpad, for a handful of cases that already passed in QEMU.
- **Replay and corpus:** each case is a file listing exactly what changed. A
  failure is shrunk to the smallest change that still fails, and kept in a
  corpus that reruns on every new build.
- **The user's estimate:** about 8.5 days to build, with the generator the
  uncertain part.

## Why `load` is ready for it

`load` rebuilds a layout from the dump's own numbers, with nothing chosen for it,
so a drifted dump is a fair test (layload.md, "partition", "Feature sets"):

| Drift | What load uses | Where |
|---|---|---|
| partition starts, sizes, numbers, gaps | the exact sectors and entry count, through an sfdisk script; then a check that the kernel agrees | `partition` |
| LV segments | the VG's metadata through `pvcreate --restorefile` and `vgcfgrestore` (linear and striped segments) | commands.rs, the LVM `assemble` |
| LUKS geometry | `--offset`, `--sector-size`, `--luks2-metadata-size`, `--luks2-keyslots-size` | commands.rs `luks()` |
| filesystem geometry | ext4 `-b -I -N -G`, XFS AG size and count, log, sector and inode size, btrfs node and sector size, FAT cluster size | `format` |
| target disk size | exact size, or `--fit-largest PCT` | fit.rs |

`load` also reads back every filesystem it makes, and its features must be
exactly the recorded ones. So most drift bugs fail loudly inside `load`.
The rest show at the judge, at the file comparison, or at boot.

## A case

A JSON file, `testvm/backshot2/cases/NAME.case`:

```json
{"source": "alpine324-uefi@gen7", "seed": 4217, "generator": "1",
 "edits": [
   {"op": "gap_before", "partition": 3, "sectors": 2049},
   {"op": "renumber", "from": 3, "to": 5},
   {"op": "resize", "partition": 5, "by_sectors": -409600},
   {"op": "xfs_agcount", "fs": "vda5", "value": 7}],
 "target": {"vda": {"size_bytes": 17179869184}},
 "load_options": ["--fit-largest", "10"],
 "content": {"seed": 4217, "recipe": "alpine-small-1"}}
```

- **`source`** names a dump and the backup that goes with it: a guest
  generation, or a BackShot1 run (`run:alpine-3.24.4-20261110-184526`).
- **`edits`** is the whole change, in order. Applying them to the source dump is
  a pure function, so the drifted layout JSON is always rebuilt, never stored.
- **`seed` and `generator`** record how the case was made. Replay uses only
  `edits`, so a later generator version can't change an old case.
- **`target`** and **`load_options`** say what to restore onto, and how.
- **`content`**, BackShot2Alpine only, names the content drift (below).

## The generator

The generator draws edits until a budget runs out (1 to 6 edits, est.), checking
the three rules after each one. A draw that breaks a rule is discarded, not run.

**Edits, phase 1:**

| Edit | Values drawn | Notes |
|---|---|---|
| `gap_before P` | 1 sector to 64 MiB | moves P and everything after it; the disk must still hold them |
| `align P` | start moved off 1 MiB alignment, to any multiple of the logical sector size | |
| `renumber P to N` | a free entry number | GPT only; MBR primaries 1 to 4; logicals keep their EBR chain |
| `resize P` | ±1 sector to ±25% | grows into a gap or the free tail; shrinks keep the data |
| `swap_order P Q` | two adjacent partitions exchange places on disk | numbers kept: the table stops being in disk order |
| `lv_split LV` | 2 to 4 linear segments, scattered over the PV's free extents | needs free extents (the generator can grow the PV's partition first) |
| `lv_resize LV` | ±extents within the VG's free space | |
| `luks_geometry` | offset, LUKS2 metadata and keyslots sizes from cryptsetup's allowed set; sector size 512 or 4096 | |
| `ext4_geometry` | block size 1k, 2k, 4k; inode size 128 to 1024; inode count; flex group size | |
| `xfs_geometry` | AG count and size; log size; inode size | |
| `btrfs_geometry` | node size 4k to 64k | |
| `fat_geometry` | cluster size; FAT12/16/32 where the size allows | the ESP: tier 3 |
| `target_size` | ±1 sector to ±25% of the disk | a smaller disk adds `--fit-largest` when needed |

**The rules, as checks:**

1. **The data fits.** Each filesystem's used bytes, from the dump, times 1.1,
   plus the new geometry's metadata overhead (est. per type), must fit its new size.
   LVs and LUKS payloads likewise.
2. **The source OS's tools could make it.**
   - A table per tool and version gives the allowed values: the dump records
     `e2fsprogs`, `xfsprogs`, `btrfs-progs`, `cryptsetup`, `lvm2`, `util-linux`.
   - Where the tool has a dry run, tier 0 runs it on a scratch file of the
     right size, using the version from the source guest: `mke2fs -n`,
     `mkfs.xfs -N`, `sfdisk --no-act`.
   - A value the source's own tool refuses makes the case **INVALID**: a
     generator bug, reported apart, never a bko-holt FAIL.
3. **Nothing needs editing.**
   - No edit touches an identifier: UUIDs, partition GUIDs, labels, names, the
     disk GUID.
   - `renumber` and `swap_order` are refused for a partition the source names by
     number or device path anywhere the dump records. That covers fstab,
     crypttab, the kernel command line and `/etc/kernel/cmdline`, `resume=`,
     `grub.cfg` (`root=/dev/vda3`, `(hd0,gpt3)`), and loader entries.
     Tumbleweed's `root=/dev/nvme0n1p2` would rule out renumbering partition 2.
   - A GRUB with `/boot` on an LV or on a partition it locates by number
     (`set root=(hd0,gpt2)` without `search`) is treated the same way.

## Oracles, and what a verdict means

| Check | Tier | Fails when |
|---|---|---|
| plan invariants (holt's existing ones) and `load plan` exit 0 | 0 | load can't plan a valid layout |
| `judge.py --expect DRIFTED.json RESTORED.json` (new mode) | 1–3 | the restored dump differs from the drifted layout, not from the source |
| `bs1-manifest --compare` and fsmeta | 1–3 | a file, owner, mode, mtime or xattr differs outside the known boot changes |
| boot and `bs1-verify`'s checks | 1–3 | it doesn't boot, or a unit fails that didn't before |
| the second backup and restore (sometimes) | 1 | the restored system can't be backed up and restored again in turn |

**Verdicts:**
- **PASS.**
- **FAIL:** a bko-holt or rustic-bko bug, shrunk and added to the corpus.
- **INVALID:** the generator broke a rule; fix the generator.
- **HARNESS:** QEMU, SSH or the host, rerun once; Test2's flake rules apply
  (test2.md, "Every run").

## Tiers

| Tier | What runs | Rate, Alpine (est.) | Rate, other Linux (est.) |
|---|---|---|---|
| 0 | apply the edits, the rules, the dry runs, `load plan`, invariants; on a host, no VM | ~3,000/h per host | the same |
| 1 | QEMU: rescue VM, `load --commit` onto a RAM-backed disk of the target's size, restore, judge, boot, verify | ~60/h per host (3 slots × ~3 min) | ~25/h per host (the user's 50 across both) |
| 2 | tier 1 with OVMF and its NVRAM kept across boots, as `bs1-qemu.py` does; for cases that touch the ESP, the boot entries or `--no-nvram`/`--removable` | ~20/h per host | ~10/h per host |
| 3 | the Thinkpad through BackShot1's scripts, for cases that passed tier 2 | ~10/h (5 min a cycle, mostly reboots) | ~2–4/h |

- **Disks:** tiers 1 and 2 run in RAM under `cmp-run.sh --reserve`, with WEAR_TAG
  `bs2` (work-in-ram.md). Alpine's disks are ~1 GiB of data in a sparse image of
  the target's size, so its SSD writes stay at ~0.
- **Done once, not per case** (2026-11-13, testvm/backshot2/README.md "What a
  case reuses"; est., not yet measured): a case's phases are HoltTest1's
  source, backup, rebuild and verify, and what is the same for every case of
  a source is done once per host. The source's boot, dump, boot state and
  health come from its preparation (ht1.sh's own source phase, run once per
  generation, bko-holt and harness); a case with no content drift and one
  snapshot copies the preparation's backup, since nothing else of a case
  changes what is backed up; the rescuer resumes from a state saved once per
  rescuer, firmware, QEMU and host, its targets hot-plugged with the names a
  cold boot gives them; QEMU quits once the targets are synced and let go of.
  The restored disk's boot stays a boot of its own. Tier 1 goes from ~110 s
  a case to ~65 s (est.). Each is switchable (`BS2_REUSE_SOURCE`,
  `BS2_REUSE_BACKUP`, `BS2_RESCUER_STATE`, `BS2_FAST_DOWN`), and falls back
  to the case's own work, with a note, wherever it can't serve.
- **Tier 3:** each Alpine cycle writes ~0.5 GB to the Thinkpad's NVMe.

## Replay, shrinking and the corpus

- **`bs2 replay CASE [--tier N]`** reruns one case exactly.
- **`bs2 shrink CASE`**, on a FAIL:
  1. delta-debug the edit list: drop halves, then single edits, while it still
     fails at the same tier with the same failing check;
  2. move each remaining numeric value halfway back towards the source's,
     while it still fails;
  3. save the result as `cases/fail-NNN-SHORTNAME.case`, with the failing check
     and log excerpt beside it.
- **Corpus:** every shrunk failure, and a fixed sample of passes (~50 per
  source, est.), rerun at tier 0 on every bko-holt build, and at tier 1 when
  Test2's ledger key changes: the bko-holt or rustic-bko sha256, or the guest
  generation. A case fixed by a commit stays in the corpus as a regression test.

## BackShot2Alpine: milestone 1

**Sources.**

| Source | Firmware, boot | Storage | Exists |
|---|---|---|---|
| `alpine324` | BIOS, GRUB | XFS root on a partition | yes (`testvm/bootstrap/alpine.sh`) |
| `alpine324-uefi` | UEFI, GRUB | ext4 root, ESP, swap | new, from the same bootstrap |
| `alpine324-lvm` | UEFI, GRUB | LVM: root and swap LVs | new |
| `alpine324-luks` | UEFI, GRUB | LUKS2, then LVM | new; key file, no typed passphrase |
| `run:alpine-3.24.4-20261110-184526` | the Thinkpad's UEFI, GRUB on the fallback only | ext4, ESP, swap | yes, BackShot1's library |

**Alpine's own rule.** mkinitfs includes a feature only if
`/etc/mkinitfs/mkinitfs.conf` lists it, and `load` regenerates the initramfs
with the source's list. So a case may change a geometry, but never what needs
a module the source's list lacks. That keeps the third rule.

**Content drift, for rustic-bko.**
- **What it is:** before the backup, a seeded generator plants a tree under
  `/srv/bs2`. The tree's recipe and seed are in the case, so the same tree comes
  back on replay.
- **What it plants:**
  - sparse files, with holes at chunk boundaries;
  - hard links across directories;
  - files of 0, 1, chunk−1, chunk and chunk+1 bytes, and one large file;
  - FIFOs and device nodes;
  - symlinks: long, dangling, relative and absolute;
  - names of 255 bytes, names that aren't valid UTF-8, names with newlines;
  - deep trees, past 4,096 bytes of path;
  - ACLs, capabilities and `user.*` xattrs;
  - `chattr +i` and `+a`, where the filesystem supports them;
  - mtimes before 1970 and after 2038, where the filesystem stores them.
- **Rules:**
  - only what the source filesystem can hold (no xattrs on vfat, no +i on
    FAT);
  - only what rustic-bko documents as kept;
  - and the tree must fit (rule 1).
- **Oracle:** `bs1-manifest` and fsmeta, as for every case.
- **Shrinking:** shrinks the tree as well as the edits, down to the smallest set
  of files that still fails.
- **Who it goes to:** findings in rustic-bko go to the Restic & Rustic session,
  the way SticTest1's do.

**Option sweep.**
- **Flags:** each case draws load's flags as well:
  - `--new-uuids`;
  - `--no-nvram` (tier 2 or 3);
  - `--removable`;
  - `--fit-largest PCT`, with a target size that needs it;
  - `--snapshot` of an older backup (the source then has two or three, with
    changes between them);
  - `--reformat MOUNT=TYPE`, once that branch is merged.
- **Verify must know each flag's intended effect:**
  - with `--new-uuids`, fstab, crypttab and the swap UUIDs change, and the
    judge compares identifiers by mapping, not equality;
  - with `--no-nvram`, the firmware boots the fallback, as AerynOS's does;
  - with `--reformat`, the filesystem type and its options change, as
    `reformat.md` lists (on `claude/holt-reformat`, not yet on main).

**Repetition.** `bs2 soak CASE N` runs one case N times at tier 1, for races:
20 of 20, as HoltTest2's `+close` does. It's reported as N of 20, since
that only bounds a race below ~15% (test2.md).

**Phase 1's end for BackShot2Alpine:**
- all five sources pass a seed run of 1,000 cases at tier 0 and 200 at tier 1;
- 30 cases pass tier 2 and 5 pass tier 3;
- every FAIL found is shrunk and in the corpus.

## Leftovers: ordered restores onto one disk

**Why.** The user worries that what an old layout leaves on a disk (an LVM
PV, a LUKS header, md or ZFS labels, another filesystem's superblock)
confuses `load` or the restored system.  Today `load` runs `wipefs --all`
on the whole disk before it partitions; wipefs erases only the magic bytes
it recognizes on that device, not what's inside the old partitions.  A
pending `load` change zeroes the disk's first 4 MiB and last 1 MiB and the
first 4 MiB of each new partition before mkfs or pvcreate; this set shows
whether that's needed, and what it misses.

**What.** One disk, steps in a fixed order (`bs2lib/leftovers.py`'s
`STEPS`).  Each step is a tier 1 run whose target is the disk the step before
left, exactly: nothing wipes it between steps but `load` (and the step's
seed).  The set stops at the first step that doesn't pass, since the steps
after it would start on a disk it didn't mean.

| Step | Restores | Onto (the disk holds) | Lined up |
|---|---|---|---|
| L1 | `alpine324-uefi` (plain: ESP, swap, ext4 root) | a blank disk | (none) |
| L2 | `alpine324-lvm`, PV moved | L1's layout | the PV starts where L1's ext4 root started |
| L3 | `alpine324-uefi` | L2's (VG vg0) | the root starts where L2's PV started |
| L4 | `alpine324-luks`, LUKS moved | L3's | the LUKS2 header where L3's root started |
| L5 | `alpine324-uefi` | L4's | the root starts on the old LUKS2 header (and its copy 16 KiB in) |
| L6 | `alpine324-uefi` | seeded md RAID1, metadata 1.2, ext4 on it | the root starts at the md partition's start |
| L7 | `alpine324-uefi` | seeded md RAID1, metadata 1.0 (superblock at the end) | start and end the same |
| L8 | `alpine324-uefi` | a seeded ZFS pool, exported (labels at both ends) | start and end the same |
| L9 | `alpine324-uefi` | seeded btrfs | ext4 at btrfs's start |

L1-L3 are the user's trio: non-LVM, LVM, non-LVM, the same distro.

**Lining up on purpose.** The moved PV and LUKS partitions are shrunk and
moved (a `resize`, then a `gap_before`) to begin and end exactly where the
plain layout's root does, sector 5244928 to the disk's last MiB.
`leftovers.aligned()` checks every step against the disk it starts on, on the
real dumps, when the set is generated: this step's partition N must begin at
the sector where a layer holding the named signature begins (the step
before's partition, or the seed's).  A drift (a source rebuilt with other
sizes) refuses the set; it never runs misaligned.  Each case records
`chain.aligned` (`partition`, `on`, `from`, `sector`).

**Seeds.** No Alpine source has md, ZFS or btrfs.  For those, the rescuer
writes a stale layer first (`leftovers-probe.sh seed`): a new GPT with one
partition the size and place of the next root (the old partitions' content
stays, as a reinstall's partitioner would leave it), the layer on it (mdadm
`--create` RAID1 with one member missing and ext4 on it; `zpool create` then
`export`; `mkfs.btrfs`), then the rescuer reboots, so it meets the layer the
way a booting machine would (udev's md assembly, LVM autoactivation,
zfs-import).

**What each step records** (`note: leftovers:` lines in the report, the
`leftovers` field of `RUN/leftovers.jsonl`):
- the rescuer at boot, before load: active LVs, md arrays, open dm
  devices, imported and importable pools, holders of the target's partitions;
- after load's close: every signature `wipefs` finds on each new partition.
  An old one is a note (STALE).  A `blkid -p` type other than the layout's
  (WRONG), or blkid unable to choose (AMBIVALENT), is a FAIL.  The FAIL is
  given in the verify phase, so the restored disk's boot is still tried;
- the restored system at boot: md arrays, dm devices, each partition's type;
- and HoltTest1's own checks: load refusing (a busy target), the boot, and
  judge.py, whose dump comparison sees an assembled stale array or VG.

**Results.**

*Before the zeroing change* (bko-holt 220c013, 0.2.5; chopin, 2026-11-13):
- Strict run `lo-before-220c013`: L1 and L2 PASS.  **L3 FAIL**: the
  rescuer's LVM autoactivated the old `vg0` at boot (root and swap LVs
  active on vdb2), and load refused, "the target is in use (vdb2 is held by
  dm-0, dm-1); refused even with --erase".  The old PV's label was intact
  at 0x218.  The set stopped there, as it should.
- `--release` run `lo-before-220c013-rel`: all nine PASS.  At boot the
  rescuer had activated vg0 (L3) and assembled the md array as md127 (L6,
  L7, both metadata versions), holding the target's partition the same way.
  The ZFS pool was importable but not imported (L8).  The LUKS header (L5)
  and btrfs (L9) were left inert.  After load's close, `wipefs` found no
  old signature on any new partition, `blkid -p` gave the layout's type on
  each, and every restored system booted with the layout's types and no
  stale md, dm or VG.
- So without the change, mkfs, pvcreate and cryptsetup cleared the
  leftovers they overwrote.  What broke was the *busy* target: a
  stacked layer the rescue system started at boot.  Zeroing can't help
  there, since load refuses before it writes.  Reported to the holt session
  (side-session notes, bs2-leftovers.md), not fixed here.  L6 and L7 weren't
  run strictly: md127 held vdb1 as vg0 held vdb2 at L3, and load's holder
  check refuses that.

*After the zeroing change* (bko-holt 8b7e177: each erased disk's first
4 MiB and last 1 MiB, and each new partition's first 4 MiB and last 1 MiB,
zeroed before sfdisk; chopin, 2026-11-13):
- Strict run `lo-after-8b7e177`: the same as before.  L1 and L2 PASS, **L3
  FAIL** with the same refusal: the rescuer autoactivated vg0 at boot, and
  load refuses before it writes, so the zeroing never runs.
- `--release` run `lo-after-8b7e177-rel`: all nine PASS, with the same notes
  as before, line for line.

*So:* in this set the zeroing change made no difference that could be seen.
Without it, nothing stale survived load and no type was wrong, so it is
defence in depth (on a disk or layer this set doesn't cover, it may matter).
What did matter is unchanged by it: a layer the rescue system starts at
boot (an LVM VG, an md array) keeps load from using the disk.  Whether
load should release such holders with `--erase` was the user's call
(layload.md's Safety rule); the user said yes (2026-11-13), and load now
lets go of them with `--erase` (layload.md, Safety, rule 4).  

*After load releases holders* (bko-holt e452150: 4b2b4d2 and e452150, with
`--erase` load lets go of what the running system started on the erased
disks, top down; cinqcent, since chopin was down, 2026-11-13).  Strict run
`lo-strict-e452150`, no `--release`: **all nine PASS**.
- L3: the rescuer had autoactivated vg0 (vg0/root, vg0/swap, holders dm-0
  and dm-1 on vdb2).  Load deactivated it itself (`vgchange --devicesfile ''
  --devices /dev/vdb2 --activate n vg0`), then wiped and restored.
- L6 and L7: the rescuer had assembled md127 from the seeded member.  Load
  stopped it (`mdadm --stop /dev/md127`).
- L8: the pool was only importable, not imported, so there was nothing to
  release.
- Every step: no old signature on the new partitions after load, the
  layout's types (vfat, swap, ext4) in the restored system, and every
  restored system booted.  The notes match the `--release` runs'.

Records: `cinqcent:/bkoscratch/backshot2/lo-strict-e452150-t1/`.

**Not built.** ext4 under btrfs (btrfs restored over ext4): no Alpine source
restores btrfs, and an edit never changes a filesystem's type (Alpine's own
rule).  md or ZFS as the *restored* layout: no Alpine source has them.  The
rescuer is HoltTest1's Debian 13 one (`zfsutils-linux`, `mdadm`, `lvm2`), not
BackOtter's rescue image, whose udev and autoactivation may differ.

## Words

Run by `testvm/backshot2/bs2` on the Mac, `bs2-host` on the hosts, the way t2 is:

| The user says | What runs |
|---|---|
| `BackShot2Alpine [N]` | N new cases (default 200) from the Alpine sources, through the tiers each needs, then the corpus |
| `BackShot2 [SOURCE...] [N]` | the same over the named, or all, Linux sources |
| `BackShot2 corpus` | the corpus alone |
| `BackShot2All` | reserved for all of BackShot2, fresh, later |
| `bs2 replay CASE`, `bs2 shrink CASE`, `bs2 soak CASE N` | as above |
| `bs2 leftovers` | the leftovers set, its steps in order on one disk (above) |
| any form with `plan` | the cases it would draw, their tiers, est. time and GB; nothing runs |

The results go into Test2's ledger (test2.md, L1), keyed by case hash, bko-holt
and rustic-bko sha256, and source generation, so an unchanged case doesn't rerun.

## Pieces to build (ordered, est.)

| # | Piece | Effort |
|---|---|---|
| B1 | Case format, edit application (a pure function over the dump), replay | 0.5 d |
| B2 | Generator: partition edits, rule checks 1 and 3, then LVM, LUKS and filesystem geometry with rule 2's tables | 2 d |
| B3 | Tier 0 runner: dry runs in the source's tool versions, `load plan`, invariants | 0.5 d |
| B4 | `judge.py --expect` | 0.5 d |
| B5 | Tier 1 and 2 runners on the hosts: reuse ht1.sh's rescue VM and `bs1-qemu.py`'s NVRAM handling, under `cmp-run.sh` | 1 d |
| B6 | Shrinker and corpus | 0.5 d |
| B7 | The Alpine sources: `alpine324-uefi`, `-lvm`, `-luks` from bootstrap | 0.5 d |
| B8 | Content drift: recipe, planter, shrinking | 1 d |
| B9 | Option sweep: bs1-restore, bs1-verify and judge.py learn each flag's effect | 0.5 d |
| | *Milestone 1, BackShot2Alpine ≈ 7 d* | |
| B10 | Tier 3 glue: a case onto the Thinkpad through backshot1 | 0.5 d |
| B11 | Rule 2 tables and source quirks for the other Linux sources: dracut, systemd-boot, shim, snapper (the refusals in `reformat.md`, on `claude/holt-reformat`, as a model) | 2 d |
| B12 | Their tier 1 and 2 runs, and the corpus over 45 sources | 0.5 d |
| | *BackShot2 phase 1 ≈ 10 d in all* | |

The user's 8.5 days covered BackShot2 alone. Content drift (B8), the option
sweep (B9) and the extra Alpine sources (B7) are BackShot2Alpine's ~2 days on top,
for ~10 days in all (est.).

## Risks

- **The generator makes impossible cases.** INVALID is its own verdict, and tier 0's
  dry runs use the source's own tool versions. A generator release isn't trusted
  until 1,000 tier-0 cases give no INVALID.
- **A host's tools stand in for the source's.**
  - The dry runs run inside the source guest's tools (vm-mount, or a chroot of
    its image), never the host's.
  - The rescue image's tools are what `load` uses, as on a real restore.
  - A refusal by the rescue's tools, of a value the source's tools accept, is a
    real finding (layload.md, "Newer tools than the machine").
- **Flaky QEMU.** HARNESS reruns once; Test2's FLAKY-FAIL and stop rules apply.
- **Wear.** Tiers 1 and 2 run in RAM under reservations. Alpine stays at ~0 GB on
  the SSDs; other sources follow HoltTest2's budgets.
- **The Thinkpad.** Tier 3 runs only cases that passed tier 2, under BackShot1's
  standing permission and guards.

## Out of scope for phase 1

- **Shape changes:** `/home` on its own partition, adding or removing LUKS or
  LVM, UEFI to BIOS and back, GPT to MBR.
- **Non-Linux sources**, `other/*` layouts, ZFS pools and md arrays. Alpine has
  neither ZFS nor md, so md and ZFS drift waits for BackShot2's other sources.
- **Multi-disk layouts**, apart from target size per disk.
- **Edits that change an identifier.** `--new-uuids` is the only way identifiers
  change, and the judge knows it.

## Decisions for the user

1. **BackShot2Alpine as BackShot2's milestone 1**, sharing one generator, case
   format and corpus (recommended); or two tools.
2. **Sources:** the five above, including three new Alpine guests (recommended); or
   start with `alpine324` and the Thinkpad's run.
3. **Content drift in milestone 1** (recommended: it's the only part that tests
   rustic-bko); or later.
4. **`--reformat` in the sweep** only after `claude/holt-reformat` is merged into
   main (recommended); or merge it first, as part of B9.
5. **The words** `BackShot2Alpine` and `BackShot2` as above, results in Test2's
   ledger (recommended).
6. **Tier 3's budget:** 5 Thinkpad cases per BackShot2Alpine round (recommended),
   or another number.
