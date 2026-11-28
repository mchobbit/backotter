# Lessons from Clonezilla: what BackOtter could take from it

2026-11-27. Proposed, not approved. Nothing is built. This spec compares
BackOtter (bko) with Clonezilla, keeps the features of Clonezilla's that
would help bko most, and plans them. It ranks them by what they do for
restore fidelity and safety first, then automation, then convenience.

**Conventions.** est. marks an estimate. *Check* marks a fact to settle
before the code that depends on it. [inferred] marks a claim from general
knowledge or reasoning, not read in the repo. Code references are to local
`main` at e779ea6 plus the uncommitted tree, read 2026-11-27. That tree is
live: another session is building GNOME OS's GN3 right now, so
`holt/src/rawpart.rs` is new and untracked, and line numbers in
`report.rs` and `load/rawvol.rs` move. Functions are named beside line
numbers for that reason. Clonezilla's option names come from its `ocs-sr`
manual page (clonezilla.org, read 2026-11-27); everything else about
Clonezilla is from general knowledge. CL1-CL9 are the features, D1-D12 the
decisions, Q1-Q5 the open questions.

## Summary

- **Clonezilla images partitions; bko backs up files and rebuilds the
  layout.** So most of Clonezilla's features are about images, and bko
  already has better answers for many of them: content-addressed, encrypted
  repositories; exact identifiers; NVRAM entries; fitting a smaller disk;
  a dry run; a typed serial before an erase. Those are listed under
  "Already in bko" with evidence, and dropped.
- **The biggest gap is data that comes back empty without anyone being
  told.** NTFS and exFAT partitions are left empty (with a note). An
  unrecognised partition (BitLocker, a vendor recovery partition) is left
  empty with no note at all. An unmounted filesystem is made empty. zvols
  are made empty. CL1 makes every one of these a named, coded notice at
  backup time and restore time. CL2 keeps the foreign ones as raw images.
- **CL2 can build on GN3.** GN3's `rawpart.rs` already takes GPT
  partitions as sparse raw images, stages them, and writes them back with a
  SHA-256 check. Its "unmade" rule, which would take any partition load
  can't make, exists but is off by default. CL2 turns it into a reviewed
  "foreign partition" rule, adds used-blocks-only images for NTFS, exFAT
  and FAT, moves the SHA-256 to staging time for big partitions, and covers
  MBR disks.
- **CL3: check a backup is restorable when it is made, not when it is
  needed.** bko can already plan a dump against fake blank disks offline
  (`bugreport::check_plan`). Run that on the snapshot's own system.json
  right after the backup, with the repository check and the header
  read-back. Clonezilla checks an image after saving it by default.
- Then: the MBR's boot code and the gap after it kept byte for byte (CL4),
  a read-back of the restored files (CL5), the run as a command line at
  the end of a TUI restore (CL6), an unattended restore from a recipe on
  the stick (CL7), a hardware inventory with the dump (CL9). An offline
  backup from the stick (CL8) is real value but belongs in the backup
  tool's spec, which doesn't exist yet.
- **Something to settle first** (CL1, *Check*): a Linux-mounted NTFS is a
  backup source like any other mount (`report/sources.rs:71-133`), so its
  files are in the backup, but load leaves its partition empty
  (`load/commands.rs:4232-4234`). Where those files land at restore is
  unverified. They may be written into the root filesystem under the old
  mount point.

## Why

Clonezilla has been the default free tool for "image this disk, put it
back" for two decades. People who try bko will compare. Two of its habits
matter for bko's own goals. It never leaves a partition behind: anything it
can't read as a filesystem, it copies with dd. And it checks an image after
saving it. bko's rule is restore fidelity first (exact metadata). A
partition that silently comes back empty is the worst failure of that rule,
and a backup that can't be restored, found only on the day of need, is the
second worst.

## Already in bko (dropped, with evidence)

| Clonezilla | bko today | Verdict |
|---|---|---|
| Image checksums at save, checked at restore (`-gm`, `-gs`, `-cm`, `-cs`) | The repository is content-addressed and authenticated; `check --read-data` reads it all. GN3's raw partitions carry a SHA-256 that `raw-write --sha256` checks (`load/rawvol.rs`, `rawpart_restore`, :223) | Have. CL3 adds the routine check; CL5 adds the files read-back (`-cmf`'s idea) |
| Encrypted images (`-enc`) | Every restic/rustic repository is encrypted | Have |
| Restore to a smaller disk (`-icds`, `-k1` proportional) | `--fit-largest PCT`, 1 to 50 %, the largest partition only, refused unless the dump shows the data fits (`load/fit.rs:1-12`, holt/README.md "What load does") | Have, Linux layouts only. CL2 changes one refusal (fit.rs:1129-1138) |
| EFI NVRAM entries updated after restore (on by default; `-iefi` skips) | `efi_entries` recreates each recorded entry on a restored partition and restores `BootOrder`, leaving other systems' entries alone (`load/commands.rs:8034`; layload.md:465-486); `--no-nvram --removable` for none | Have on Linux. FreeBSD writes none (freebsd-bs1.md, Risks); out of scope here |
| Destructive-step confirmation | CLI: a target that isn't blank needs `--erase DEV` (`load/target.rs:1285-1335`, `erase_gate`); the plan names each source disk by size, model and serial (`TargetDisk::describe`, target.rs:81-94; plan.rs:725). TUI: Review asks for the end of each target's serial typed, or its size and kernel name when the serial isn't unique (rescue-tui.md:1512-1516, `review::confirm_by`) | Have. One small tweak in CL1: the `erase_required` text uses `describe()` too |
| Check the image before restoring (`-scr` skips) | `plan` is the default stage; every writing stage reads the disks back; the TUI has `--dry-run` with two locks (rescue-tui.md:805-835) | Have |
| Network repository (NFS, Samba, SSH) | Designed: the Network screen and remote repositories, wired only by the user's decision of 2026-11-08 (rescue-screens.md:337-378) | Have (designed). Wi-Fi stays out |
| Logs kept with the image | Restore: Done writes the log to the stick and to the repository (rescue-screens.md:1446-1486). Backup: the backup tool isn't built | Have for restore; the backup side goes in the backup tool's spec |
| Partition table, GUIDs, labels | Exact, from the dump (`load/writetable.rs:1-7`) | Have, better than Clonezilla's sfdisk/sgdisk files |

**Out of scope:** multicast and restore to many machines at once
(Clonezilla SE, DRBL); a disk-to-disk clone with no repository
(`ocs-onthefly`), since a local repository on a USB drive does the same in
two passes and keeps a backup; bad-sector rescue (`-rescue`), which needs
CL8 first.

## The features

### CL1. Nothing comes back empty without a word

- **Clonezilla:** with `-q2`, partclone, then partimage, then dd: every
  partition comes back, as a filesystem image or byte for byte.
- **bko today:**
  - NTFS and exFAT: probed (`probe/fat.rs:15-21`), then left empty with a
    plain note (`load/commands.rs:4232-4234`).
  - A partition with no recognised content (BitLocker, a vendor recovery
    partition, Microsoft reserved, a Chromebook or Android partition):
    `leaves()` skips devices with null content (`load/commands.rs:3913-3925`),
    so the partition is made, with its GUIDs, and nothing is said. There is
    no BitLocker probe anywhere (`-FVE-FS-` is in no file).
  - A filesystem load can make but that wasn't mounted: not a backup source
    (`report/sources.rs:71-133` takes mounts only), so it is made empty.
    *Check* that no note says so; none was found in load/.
  - zvols: "made empty; archivers back up files, not zvols", a note
    (`load/commands.rs:5016-5030`, `make_dataset`).
  - LVs: already raw when unmounted and holding nothing load makes
    (`qubes.rs`, `raw_volumes`, for every system, not only Qubes).
  - A Linux-mounted NTFS (`ntfs3`, `fuseblk`): its files are backed up, its
    partition left empty. *Check* what the mount and restore stages do with
    those files.
- **Gap:** the user learns about lost data from a free-text note at
  restore time, or never.
- **Design:**
  - CL1a. One coded Note, `not_restored`, for every device whose data
    won't come back: fields `device`, `what` (`ntfs`, `exfat`, `bitlocker`,
    `unknown`, `unmounted_fs`, `zvol`), `bytes`, `label`, and `raw` (true
    once CL2 takes it). The plan prints it as a "Not restored" block; the
    TUI's Review shows it in words before the serials are typed
    (holt-json.md's Notes list gains the code).
  - CL1b. The same list at backup time: `bko-holt dump` puts it in the
    dump as `storage.not_backed_up` (the device, why, size), and prints one
    warning line each. bs1-backup and the backup tool show it. A source
    the user accepts ("that partition is scratch") is named in the backup
    tool's config, later.
  - CL1c. A BitLocker probe: the `-FVE-FS-` signature at offset 3 of the
    boot sector [inferred: BitLocker's volume header], content type
    `bitlocker`, version, no keys read. Also `FVE`'s "To Go" variant for
    removable drives [inferred].
  - CL1d. Settle the mounted-NTFS case: the restore must not write a
    foreign filesystem's files into the root. Either leave that path out of
    the restore (a `!` glob, as `raw_globs` does) with a `not_restored`
    note, or, with CL2, take the partition raw and leave its files out.
  - CL1e. `erase_required`'s text names the target by `describe()`
    (size, model, serial), as the plan's disk lines do.
- **Platforms:** all. `format()` and `leaves()` are shared; the BSD,
  illumos and Haiku leaves come from `other/` (*Check* that
  `label_partitions` and softraid leaves reach the same note).
- **Effort:** S, 1 d est.
- **Tests:** unit tests per `what`; GateTest2's golden plans (the 45
  recorded dumps through `check_plan`) gain the block where it applies; a
  synthetic layout with each case; HoltTest2 on `debian13-stack` with an
  extra unmounted ext4 and an NTFS made by `mkntfs` (ntfs-3g) in the
  guest, both named in the plan and the dump.

### CL2. Foreign partitions kept as raw images

- **Clonezilla:** ntfsclone or partclone images only the used blocks of
  NTFS, FAT, exFAT, HFS+ and the like; dd copies anything else whole.
  That is how a Windows dual-boot machine comes back whole.
- **bko today:**
  - GN3's `rawpart.rs` (untracked, in progress): a GPT partition is taken
    raw by a `Rule` (`rawpart.rs:103-124`). `Rule::GENERIC` takes the
    Discoverable Partitions /usr types; its `unmade` switch (a partition
    holding nothing load makes, not mounted read-write) exists but is off
    (`rawpart.rs:110`, `rule_for`:177). GPT only (`mark_with`, :227).
    The SHA-256 is read off the whole partition at dump time
    (`sha256_of`, :196). Staging copies it sparse, a 4 KiB zero block a
    hole (`copy_sparse`, :363), to `/.bko-holt/rawpart/<PARTUUID>.img`
    (`RAWPART_DIR`, :36), checked against the dump's size and SHA-256
    (`stage`, :410).
  - load: `format` skips it (`is_raw_volume`, rawvol.rs:152); a size that
    differs from the dump's is refused (`rawpart_problems`, :179); the
    restore stage writes it with `raw-write --size --sha256`, only the
    chunks that differ (`rawpart_restore`, :223); `--fit-largest` refuses
    when the largest partition is raw (fit.rs:1129-1138).
  - Small raw areas live in the dump itself: a BSD or offline ESP up to 64
    MiB (`other/mod.rs:427-481`, `esp_image`), boot areas up to 2 MiB
    (`image_b64`, :418).
- **Gap:** no rule takes Windows' or other systems' partitions; a 500 GB
  NTFS would be read whole twice per backup (the dump's SHA-256 and the
  staging); MBR disks aren't covered; a dual-boot disk where NTFS is the
  largest partition can't be fitted.
- **Design:**
  - CL2a. **A "foreign" rule** in `rawpart.rs`, generic and on by default
    (D2): a partition, not mounted read-write, holding `ntfs`, `exfat`,
    `bitlocker`, or content bko doesn't recognise, or of a Windows
    recovery, Microsoft reserved or OEM type, or an unmounted FAT that
    isn't an ESP this layout restores by files. Left out, as today: a BIOS
    boot partition (`other/offline.rs:19`, `BIOS_BOOT`), freebsd-boot,
    the Solaris reserved partition of a whole-disk pool, swap, anything
    whose content load makes (`MADE`), and an empty partition (all zeros
    in its first and last MiB: it gets a `not_restored` with `what: empty`
    and nothing is staged). Unmounted filesystems load *can* make stay CL1
    notices unless D2's alternative is chosen.
  - CL2b. **Used blocks only** (D4) for NTFS, exFAT and FAT: bko-holt
    reads the allocation bitmap (NTFS's `$Bitmap` through MFT record 6's
    data runs; exFAT's allocation bitmap; the FAT), and staging writes
    only allocated clusters, free ones as holes. The image is the
    partition with free clusters read as zeros; restore writes it as any
    raw image. Its SHA-256 is of that view. Free clusters on the target
    hold whatever they hold (NTFS doesn't read them) [inferred].
    BitLocker and unknown content: the whole partition, sparse, as GN3
    does. restic and rustic dedup unchanged chunks between backups, so the
    repository grows by what changed [inferred from content-defined
    chunking], but the read is still the used bytes every time.
  - CL2c. **The SHA-256 at staging** (D5): above 1 GiB, dump records
    `sha256: null, hashed_at: "stage"` and the size; staging hashes what it
    copies and writes the SHA-256 into the layout the backup tool puts in
    the snapshot's system.json (`snaplayout::SYSTEM_PATH`). load takes the
    snapshot's layout (holt-json.md, `snapshot-layout`), so it has the
    SHA-256. `rawpart_problems` accepts `hashed_at: stage` only with a
    layout from a snapshot; from the stick's copy alone it refuses, as
    today for a missing SHA-256.
  - CL2d. **The cap** (D3): a foreign partition whose used bytes are over
    64 GiB est. is marked `over_cap`, listed by CL1b, and staged only when
    the backup tool's config or `rawpart-stage --all` says so.
  - CL2e. **Staging by disk offset**, not `/dev/disk/by-partuuid`: read
    the dump's disk at the partition's start and size. Then it works on
    FreeBSD (whose `gptid` labels are off by default [inferred]) and every
    other platform, and on MBR disks.
  - CL2f. **MBR disks** (D7), second: key the image by the MBR's disk
    signature and partition number (`rawpart/mbr-<sig>-<n>.img`). On MBR,
    Windows' BCD names partitions by disk signature and byte offset
    [inferred], so a raw partition on MBR keeps its start sector: a fit
    that moves it is refused.
  - CL2g. **Different-size disk** (D6): fit takes the largest partition
    that isn't raw, not "the largest, or refuse" (fit.rs:1129-1138). A raw
    partition keeps its size. No `ntfsresize`: the user extends it in
    Windows if they want.
  - CL2h. **Dual-boot boot order:** nothing new. The ESP comes back by
    files with `EFI/Microsoft`, the partition GUIDs and disk GUID are the
    source's, so `efi_entries` recreates "Windows Boot Manager" and
    `BootOrder` as recorded (layload.md:465-486). BCD on GPT names
    partitions by GUID [inferred], which holt keeps. A Windows install on
    a disk of its own: `--only-disk` leaves it out and nothing touches it,
    the safest answer, and the plan says so.
  - CL2i. **BitLocker:** the image is ciphertext, restored byte for byte.
    Windows may still ask for the recovery key on the first boot after a
    restore if the TPM's measurements changed [inferred]. Dump warns at
    backup time: "have this volume's BitLocker recovery key", and so do
    the plan and Done.
  - The archiver rule holds: images go in the same snapshot, by the same
    archiver, as the files; nothing mixes restic and rustic repositories.
- **Platforms:** Linux first (the dual-boot case). FreeBSD next, through
  CL2e. illumos and Haiku get it from the same code; not tested before a
  user asks. Haiku: rustic-bko only, as always.
- **Effort:** L, 5.5 d est. (rule and probe 0.5, bitmap readers 2,
  staging and SHA move 1, fit 0.25, MBR 1, tests 0.75 plus the Windows
  guest's build in D12).
- **Tests:** unit tests of each bitmap reader on images made by `mkntfs`,
  `mkfs.exfat`, `mkfs.fat` with files written and deleted; a fuzz of the
  parsers (they read untrusted bytes: the `#![warn(clippy::indexing_slicing,
  ...)]` header of `probe/fat.rs`); GateTest2's slow part gains a raw
  round trip on loop devices; HoltTest2 on a new guest `win11-debian13`
  (D12), UEFI, GPT, Windows 11 evaluation plus Debian 13, with a
  `+bitlocker` variant on swtpm: Windows must boot after the restore, by
  its NVRAM entry, with `chkdsk /scan` clean, and its partitions' SHA-256
  of the used view equal; BackShot1 on the A100 only if D12 says so.

### CL3. A restorability check after every backup

- **Clonezilla:** after saving, it checks the image can be restored
  (on by default; `-sc`/`-scs` skips it).
- **bko today:** the backup tool isn't built. It is meant to read back the
  snapshot's LUKS headers (holt-json.md:1111-1112). `check_plan`
  (`bugreport.rs:1098`, doc at :1086) already plans a dump against a fake
  rescue system with a blank disk of each size, offline, using
  `testutil::Tree`, which ships (`lib.rs:45`, a `pub mod`). bs1-backup
  runs its own checks.
- **Gap:** a backup that would be refused, or would lose a partition, is
  found at restore time.
- **Design:** `bko-holt check-backup --repo REPO --password-file FILE
  --snapshot ID [--archiver PROGRAM] [--read-data PCT] --json`. It writes
  no disk (allowed under `BKO_HOLT_NO_COMMIT`). Steps:
  1. `snapshot-layout` of the new snapshot: its system.json is there and
     readable (`no_system_json` is a failure).
  2. `luks-headers` into a 0700 tmp dir, checked, then zeroed and removed.
  3. The plan of that layout on fake blank disks of the source's sizes
     (`check_plan`'s machinery, without the redaction): exit 4 or any
     Problem is a failure; every `not_restored` (CL1) is listed.
  4. Every backup source path is in the snapshot and not empty (the
     archiver's `ls` of each path, one level).
  5. The archiver's `check` (metadata), and, with `--read-data PCT`, a
     rotating subset (`--read-data-subset`).
  6. Raw images: present with the dump's size; with `--read-data`, the
     archiver's `dump` of each through SHA-256 against the layout's.
  Result: one JSON document and a line per step. The backup tool runs it
  by default (D9); bs1-backup calls it after its backup.
- **Platforms:** all. *Check* that `check_plan`'s fake tree (Linux-shaped
  `/proc`) plans BSD, illumos and Haiku dumps; `scenarios.rs:48` runs it
  over the scenarios, which may include them.
- **Effort:** M, 2 d est.
- **Tests:** unit tests for each step's failure (no system.json, a wrong
  header, a plan with a Problem, an empty source); SticTest2 for the
  archivers' `ls`, `check` and `dump` on both archivers; HoltTest2 calls it
  after each guest's backup (a failure fails the cell); BackShot1's
  bs1-backup on the A100.

### CL4. The MBR's boot code and the gap after it, byte for byte

- **Clonezilla:** saves the MBR and, with `-j2`, the data between the MBR
  and the first partition, and writes them back.
- **bko today:** on Linux the dump keeps the first 16 bytes of the MBR
  (`first_bytes`) and counts the gap's non-zero sectors
  (`bootcode.rs:158-170`, `post_mbr_gap`), but not their bytes. On BIOS,
  load reinstalls GRUB only, and only when the dump shows GRUB
  (`load/commands.rs:8076-8085`, `grub_bios_devices`). writetable writes
  `boot_code.code_b64` when the dump has it (`load/writetable.rs:6`,
  :101), which only the BSD and offline paths record (`other/mod.rs:159`,
  `other/freebsd.rs:446`). A Windows MBR, Syslinux, or a vendor loader in
  the gap on a Linux dump comes back as zeros.
- **Design:** the Linux dump records `code_b64` (the 440 bytes) for every
  MBR, and `gap_b64` (the gap up to the first partition, at most 1 MiB,
  trailing zeros cut, as `image_b64` does) when it has non-zero sectors.
  load writes them back when the loader isn't one it reinstalls (GRUB 2 on
  BIOS), before the boot stage, and the plan says so. With GRUB, GRUB's
  install wins, as today. GPT disks: the area between the entry array and
  the first partition is kept the same way, under the same cap.
- **Platforms:** Linux (the others keep their boot areas already).
- **Effort:** S, 0.75 d est.
- **Tests:** unit tests with a Windows MBR and a Syslinux MBR sample;
  redaction (`redact.rs` treats `*_b64` as data: *Check* the gap can't
  hold an identifier the redactor misses, or omit it from reports as
  `esp_image` is, `bugreport.rs:2193-2197`); HoltTest2 on `debian12`
  (BIOS, GRUB: no change) and a BIOS Syslinux layout made in a guest.

### CL5. Read the restored files back

- **Clonezilla:** with `-gmf` at save and `-cmf` at restore, it checks the
  restored files' checksums.
- **bko today:** the archiver checks each blob as it reads it, and each
  stage reads the disk layout back, but nothing reads the restored files
  back from the target. USB bridges and cheap SSDs that lie about writes
  are a known farm problem (the GMK and Bigfoot bridge notes).
- **Design:** `load --verify` (opt-in, D10): after the restore stage and
  `sync`, drop the caches and have the archiver compare the target tree
  with the snapshot by content: restic-bko's `restore --verify` [inferred:
  upstream restic has it]; rustic-bko's equivalent *Check* (a `--dry-run`
  restore with `verify-existing` [inferred]). Raw images: re-read the
  written ranges and hash them against the SHA-256. A mismatch is a stage
  failure with the file list. The TUI shows it as "Check the copy" on
  Review, off by default; BackShot1 and HoltTest2 turn it on.
- **Platforms:** all, per archiver.
- **Effort:** M, 1.5 d est. (more if rustic-bko needs a patch: ForkAudit
  rules apply).
- **Tests:** a planted corruption (a byte flipped on the target between
  restore and verify) found in a loop-device test; HoltTest2 with
  `--verify` on two guests per archiver; SticTest2 if a fork patch is
  needed.

### CL6. The run as a command line at the end of a TUI restore

- **Clonezilla:** at the end of an interactive run it prints the `ocs-sr`
  command that would repeat it, for scripts and boot parameters.
- **bko today:** Review shows every shell command the plan will run
  (rescue-screens.md:1265-1301) and one argument list feeds plan and
  restore (rescue-screens.md:1235-1262, `holt_args.rs`), but the user is
  never given the bko-holt commands themselves.
- **Design:** Done and Failure gain `c`: "Run this again by hand", a short
  shell script: `bko-holt snapshot-layout`, `bko-holt luks-headers`, then
  `bko-holt load` with the same `--map` and `--erase` by
  `/dev/disk/by-id`, `--snapshot`, `--fit-largest`, `--reformat`, and
  `--through close --commit`. Secrets stay out: `--password-file` and
  each `--key-file` are placeholders the user fills (`/path/to/...`).
  `--expect-plan` stays, so a rerun on changed disks stops at the plan. It
  is saved beside the log (`restore-logs/<time>.sh`) and is the input CL7
  takes.
- **Platforms:** all seven rescue platforms (the TUI is the same).
- **Effort:** S, 0.75 d est.
- **Tests:** a unit test that the script, run by `fake-bko-holt`, makes
  the same argv as the TUI did; no secret in it (scanned for the session's
  key-file paths and the password); app-test.py's Done golden screen.

### CL7. An unattended restore from a recipe on the stick

- **Clonezilla:** boot parameters (`ocs_live_run`, `ocs_live_batch`,
  `ocs_prerun`, `ocs_postrun`, `ocs_repository`) run a whole restore with
  no one at the keyboard, from a stick or PXE; `-batch` skips every
  prompt and is marked dangerous.
- **bko today:** hands-free restores exist, but driven over SSH from the
  Mac (testvm/realworld/backshot1: `bs1-restore`, key-access on the
  stick, the drive unlock key file on the backup drive for the LUKS
  families). The stick itself can't run one alone. stick-config.md keeps
  secrets off the stick and calls unattended restores unplanned
  (stick-config.md:345-349).
- **Design** (D8):
  - A recipe, `backotter/machines/<install-id>/restore.recipe` on the
    config partition: CL6's arguments, the target disks by serial, the
    snapshot (an ID, or `latest`), and where the secrets are, as paths on
    the local repository drive (as bs1-luks's key file on mybig512). No
    secret on the stick.
  - A boot entry "BackOtter: run the saved restore", never the default.
    It is chosen at the boot menu, or once by `BootNext` (as bs1-to-stick
    does).
  - Safety: it runs only if every target's serial matches the recipe and
    the plan is the recipe's `--expect-plan`; it shows a 60 s countdown
    with the disks named, any key stops it; it disarms itself on the
    config partition before the first write (one-shot), so a stick left in
    the machine can't wipe it twice.
  - Without a local drive holding the secrets it asks for them once at
    the console and then runs (semi-attended).
  - Network boot (stick-config.md's `backotter-netboot-...`) can take the
    same recipe later.
- **Platforms:** Linux first (GRUB entry, `/proc/cmdline`), FreeBSD
  second (loader menu, `kenv`), the others when asked.
- **Effort:** L, 3.5 d est.
- **Tests:** ImgTest2's `+img` panel gains an unattended cell: the image
  boots in QEMU with a recipe and a second virtual drive holding the
  repository and key file, restores and powers off with nothing typed;
  refusal cells for a wrong serial, a changed plan, a disarmed recipe;
  then one BackShot1 cycle on the A100 with the stick alone (no SSH
  driving after to-stick).

### CL8. An offline backup from the stick

- **Clonezilla:** its main mode. The machine is booted from the stick, so
  nothing is running, the copy is quiescent, and a machine that won't boot
  can still be saved.
- **bko today:** backups are of the running system. `dump --disk` reads a
  system off its disks from a Linux rescue, but only for Redox and Sculpt
  (`other/offline.rs:1-9`; `main.rs` dump usage, "Linux only").
- **Gap:** a machine that no longer boots can't be backed up by bko.
- **Design:** deferred to the backup tool's spec (D11). What it needs: an
  offline Linux dump (unlock, assemble read-only, import pools
  read-only, mount read-only, read fstab, crypttab, boot config from the
  mounted root; NVRAM from the stick, which is the same machine), the
  archiver run on that tree, the stick writable to a local repository
  drive, and a "the disk is failing" path (read errors listed, not fatal).
- **Effort:** L, 5 d or more est.; not in this plan's total.

### CL9. A hardware inventory with the dump

- **Clonezilla:** saves `lshw`, `dmidecode` and `lspci` output in the
  image directory.
- **bko today:** the dump keeps ten DMI fields (`report.rs:852-876`,
  `host_json`), and load compares DMI UUIDs to say "this computer"
  (`load/jsonout.rs:528-537`). No NICs, no PCI list, no CPU or memory.
- **Design:** `host.hardware`: CPU model and count, memory size, every
  NIC (kernel name, MAC, driver, PCI ID), the PCI list (IDs and driver),
  storage controllers. The plan adds notes when the target machine
  differs: another DMI UUID, a NIC whose MAC the restored system's
  network config names (NetworkManager, netplan, systemd-networkd, udev
  persistent-net), a storage driver the source's initramfs may lack
  [inferred: a name check, not a proof]. MACs are already redacted by
  report-bug.
- **Platforms:** Linux (`/sys`), FreeBSD (`pciconf -lv`, `ifconfig`),
  illumos (`prtconf`, `dladm`), Haiku (`listdev`), in that order.
- **Effort:** S-M, 1 d est.
- **Tests:** unit tests on fake `/sys` trees; the redaction proof
  (`check_plan`) on dumps with the new section; HoltTest2's dump judge
  ignores `host.hardware` when the guest's virtual NIC changes.

## Decisions

- **D1. Scope and order.** CL1, CL2, CL3, CL4 first; CL5, CL6 next; CL7
  and CL9 after; CL8 into the backup tool's spec. *Recommend:* yes. Or
  CL3 first, since it pays on every backup and needs no new format.
- **D2. Which partitions CL2 takes raw.** The foreign rule of CL2a, on by
  default (*recommended*: it closes the dual-boot gap and anything bko
  doesn't know, and leaves alone what load already makes). Alternatives:
  also take unmounted filesystems load can make (an ext4 not in fstab)
  raw, which costs a read of each at backup; or only partitions the user
  names (`--raw PARTUUID`), which keeps backups small but repeats today's
  silent loss for anyone who doesn't know to ask.
- **D3. The cap.** 64 GiB of used bytes per partition est. by default, over
  it only when the config says so (*recommended*: a big Windows C: is a
  long first backup; the user should choose it). Or no cap; or 16 GiB.
- **D4. Used blocks only.** bko-holt's own bitmap readers for NTFS, exFAT
  and FAT, in Rust, no crates (*recommended*: one tool on every platform,
  no ntfs-3g on the stick, the image still a plain byte image that
  `raw-write` restores). Or ntfsclone/partclone on Linux (proven, but
  Linux only, its own image format, and a second restore path); or whole
  partitions only (simplest, reads every byte at every backup).
- **D5. When the SHA-256 is taken.** At staging for partitions over 1 GiB,
  written into the snapshot's system.json (*recommended*: one read, not
  two). Or GN3's dump-time SHA-256 for all (two full reads per backup,
  and a slow `dump`).
- **D6. Another disk size.** Raw partitions keep their size; fit takes the
  largest partition that isn't raw (*recommended*). Or grow NTFS with
  `ntfsresize` on a bigger disk (Linux only, changes the image).
- **D7. MBR disks.** GPT first, MBR as CL2f in the same feature, a second
  step (*recommended*: Windows 11 needs GPT; older dual-boot laptops are
  MBR). Or GPT only.
- **D8. Unattended restore.** The recipe of CL7: no secret on the stick,
  secrets on the local repository drive, a non-default boot entry,
  serial-pinned targets, one-shot, a countdown (*recommended*). Or keep
  hands-free restores SSH-driven only, as BackShot1 is; or netboot only.
- **D9. The check after a backup.** Steps 1-5 on by default, without
  `--read-data`; `--read-data 5` once a week est.; raw images re-hashed
  only then (*recommended*, as Clonezilla checks by default). Or all of
  it opt-in.
- **D10. The read-back after a restore.** Opt-in in the TUI and the CLI,
  on in BackShot1 and HoltTest2 (*recommended*: it doubles the restore's
  reading time). Or on by default.
- **D11. The offline backup.** Into the backup tool's spec
  (*recommended*). Or `dump --disk` for Linux systems now, ahead of the
  backup tool.
- **D12. A Windows guest.** A QEMU Windows 11 evaluation guest dual-booted
  with Debian 13, built once and kept in vm-store, for HoltTest2's CL2
  cells (*recommended*; the evaluation edition is free for 90 days
  [inferred], which is enough for a test guest that is rebuilt per
  rehearsal round). Or synthetic NTFS, exFAT and BitLocker-header
  fixtures only (no proof that Windows boots after a restore); a
  dual-boot BackShot1 on the A100 needs a Windows install there and the
  user's yes (Q1).

## Steps

| | What | Done when | est. |
|---|---|---|---|
| CL1 | `not_restored` notes, `storage.not_backed_up`, the BitLocker probe, the mounted-NTFS fix, `erase_required` text (High subagent) | every case named in plan and dump; GateTest2 goldens updated; HoltTest2 `debian13-stack` with the extra partitions PASS | 1 d |
| CL3 | `bko-holt check-backup` (High subagent) | each step's failure unit-tested; HoltTest2 runs it after each backup; bs1-backup calls it | 2 d |
| CL4 | MBR code and gap kept and written back (High subagent) | unit tests; HoltTest2 `debian12` unchanged; the Syslinux BIOS layout boots after restore | 0.75 d |
| CL2a-e, g-i | the foreign rule, bitmap readers, staging by offset with the SHA-256, the cap, fit (High subagents, one per reader) | the readers' tests and fuzzing; the raw loop-device round trip in GateTest2; HoltTest2 `win11-debian13` and `+bitlocker` PASS on two pick-host hosts | 4.5 d |
| CL2f | MBR raw partitions | an MBR dual-boot layout round trip with start sectors kept | 1 d |
| W | the `win11-debian13` guest (D12), `make-windows.sh`, vm-store | boots both systems alone in OVMF; in vm-store | 1 d |
| CL5 | `load --verify` (High subagent; a fork patch if rustic-bko needs one) | the planted-corruption test; HoltTest2 with `--verify` on two guests per archiver | 1.5 d |
| CL6 | Done's "run this again" script (High subagent) | unit and pty tests; no secret in it | 0.75 d |
| CL7 | the recipe, the boot entry, the one-shot (High subagents) | ImgTest2 unattended cell and refusals PASS; one BackShot1 cycle on the A100 | 3.5 d |
| CL9 | `host.hardware` and the "other machine" notes | unit tests; redaction proof; HoltTest2 dump judge clean | 1 d |

CL1 comes first: CL2 and CL3 report through its notes. CL3 needs nothing
else. CL2 waits for GN3's `rawpart.rs` to land on main, and builds on it,
not beside it. W can run beside CL2's code. CL7 needs CL6. Every source
change goes through a High-effort subagent with tests, on main, committed
when the user asks; farm jobs go through pick-host. Total 17 d est.,
CL8 not counted.

## Test plan

- **cargo test** for every unit; the parsers fuzzed (CL2b, CL1c).
- **GateTest2:** the golden plans gain the "Not restored" block (CL1); the
  slow part gains the raw round trip on loop devices in /mnt/bko-ram
  (CL2).
- **SticTest2:** the archivers' `ls`, `check`, `dump` and verify paths
  used by CL3 and CL5, on both archivers and every platform's binary.
- **HoltTest2:** CL1's extra partitions on `debian13-stack`; CL2 on
  `win11-debian13` and `+bitlocker`; CL3 after every cell's backup; CL4 on
  `debian12` and a BIOS Syslinux layout; CL5 with `--verify`; CL9's
  judge rule. Two pick-host hosts for each new cell.
- **ImgTest2 `+img`:** CL7's unattended cell on the Linux image, then
  FreeBSD's.
- **Rescue tests:** `app-test.py`, `pty-test.py` and the golden screens for
  CL1's Review text, CL5's checkbox and CL6's script; `HT_VARIANT=rescue`
  (RT14) once built.
- **BackShot1 on the A100:** bs1-backup with CL3; one cycle with CL5's
  `--verify`; one CL7 cycle with the stick alone; a dual-boot cycle only
  after Q1.
- **BackShot2:** its seeded layout drift gains an unmounted partition and
  a foreign one, which CL1 must name and CL2 must bring back.

## Risks

- **Raw images are big.** A first backup of a full Windows partition is
  slow and large. D3's cap and CL2b's used-blocks reading bound it;
  CL1b says what was left out.
- **Our own NTFS bitmap reader is new parsing of untrusted bytes.** A bug
  that marks a used cluster free loses data silently. Mitigations: the
  image's SHA-256 is of the same view at staging and at restore; the
  Windows guest's `chkdsk /scan` after restore; a full-copy fallback when
  the bitmap doesn't parse cleanly (a dirty volume, an unknown NTFS
  version), never a guess.
- **A hibernated or "Fast Startup" Windows.** The NTFS is dirty but
  quiescent; a byte image keeps it as it was, `hiberfil.sys` included, so
  Windows resumes as it would have [inferred]. The bitmap reader must
  then take the volume as dirty and copy it whole (above).
- **BitLocker's recovery key.** A restored BitLocker volume may ask for it.
  Dump, plan and Done all say so; bko never reads or stores the key.
- **Unattended wipes.** CL7 can erase a disk with no one there. The
  serial match, the plan hash, the countdown, the non-default entry and
  the one-shot disarm are each a separate lock; ImgTest2 tests each
  refusal.
- **GN3 is in progress.** CL2 changes `rawpart.rs`'s rule, SHA-256 timing
  and staging. Building it before GN3 lands would collide with that
  session's work; CL2 waits.
- **The mounted-NTFS question** (CL1d) may turn out to be a live bug in
  today's restores. If so, it is fixed in CL1 before anything else here.

## Open questions

- **Q1.** Is there a dual-boot machine to protect: the A100, another
  laptop, a farm host? This decides whether CL2 is first or later, and
  whether a dual-boot BackShot1 is worth a Windows install on the A100.
- **Q2.** Should an unmounted filesystem load can make (a spare ext4) be
  taken raw (D2's first alternative) or only named (CL1)?
- **Q3.** Is unattended restore (CL7) wanted at all, or are SSH-driven
  hands-free runs enough? stick-config.md said "nothing like that is
  planned" (:349).
- **Q4.** Should the offline backup (CL8) move ahead of the backup tool,
  as a way to save machines that no longer boot?
- **Q5.** For CL9: is a restore onto different hardware a goal, or only
  the same machine? It sets how far the "other machine" notes go.

## Recommended order

1. CL1, nothing comes back empty without a word (fidelity, safety; S).
2. CL3, the check after every backup (safety; M).
3. CL4, the MBR's code and gap (fidelity; S).
4. CL2, foreign partitions as raw images, after GN3 lands (fidelity; L).
5. CL5, the read-back after a restore (safety; M).
6. CL6, the run as a command line (automation; S).
7. CL7, the unattended restore (automation; L).
8. CL9, the hardware inventory (convenience; S-M).
9. CL8, the offline backup, in the backup tool's spec.

| | Feature | Clonezilla | Benefit | Effort | Platforms |
|---|---|---|---|---|---|
| CL1 | Nothing empty without a word | every partition comes back | fidelity, safety | S | all |
| CL3 | Check after backup | `-sc` (on by default) | safety | M | all |
| CL4 | MBR code and gap | MBR, `-j2` | fidelity | S | Linux |
| CL2 | Foreign partitions raw | partclone, ntfsclone, dd | fidelity | L | Linux, then FreeBSD |
| CL5 | Read-back after restore | `-gmf`/`-cmf` | safety | M | all |
| CL6 | Run as a command line | prints its `ocs-sr` line | automation | S | all |
| CL7 | Unattended restore | `ocs_live_run`, `-batch` | automation | L | Linux, then FreeBSD |
| CL9 | Hardware inventory | lshw, dmidecode | convenience | S-M | Linux first |
| CL8 | Offline backup | its main mode | rescue | L | deferred |
