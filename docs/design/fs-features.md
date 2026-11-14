# File-system feature sets on restore: audit and plan (2026-11-14)

Status: **approved 2026-11-14 as recommended (F1–F3; the read-back is printed in plans; rocky10-raid5+img before the fix).** Being built.

The rule (memory `one-kernel-rescue`): the Linux rescue image keeps one newest
kernel and OpenZFS, and restore makes each file system with the source's feature
set, so an older distro still mounts and boots what the image's newer mkfs made.

This audit compares what `bko-holt dump` records (`holt/src/probe/*.rs`) with
what `bko-holt load` passes to mkfs (`holt/src/load/commands.rs`, format and
assemble), and checks the result against the **real rescue image's tools**
(Linux image dev.20261112.5b2fe9d: kernel 7.0.0, e2fsprogs 1.47.2, xfsprogs
6.18.0, btrfs-progs 6.17.1, f2fs-tools 1.16.0, nilfs-tools 2.2.11, jfsutils
1.1.15, dosfstools 4.2, bcachefs-tools 1.39.7, OpenZFS 2.4.1, cryptsetup 2.8.4,
lvm2 2.03.31, mdadm 4.5).  I ran those tools from the image's squashfs in a
chroot on Chopin with the golden plans' exact options, and read the results
with dumpe2fs, xfs_db, `btrfs inspect-internal dump-super` and dump.f2fs.

## Verdict in one paragraph

The earlier note is mostly stale.  load already makes ext2/3/4, btrfs, NILFS2,
FAT, bcachefs and ZFS with every feature on or off as the source had it, and
F2FS, JFS and swap have no default that changes their on-disk format today.
There are two real gaps:

1. **XFS (needs a fix):** load names `exchange`, `parent` and `metadir` only
   when the source has them, and otherwise leaves them to the image's
   mkfs.xfs.  xfsprogs 6.18's own `lts_6.18.conf` says its defaults are
   `exchange=1` and `parent=1`.  Under Chopin's 6.12 kernel mkfs.xfs left them
   off, which suggests it picks its defaults by the running kernel.  If so, the
   image's 7.0 kernel turns them on.  rocky10, rocky10-lvm, rocky10-raid5 and
   debian13-stack's /srv have neither.  Rocky 10 keeps /boot on XFS, so its
   GRUB would have to read an XFS with incompat bits it may not know.  This
   isn't proven yet: no rocky10 or debian13-stack load has ever run from the
   real image (the test2 ledger has `+img` rows only for nixos2605, both
   PASS).  Every other run used the BIOS rescuer, a Debian 13 copy with
   Debian's tools and kernel, which never shows the problem.
2. **The safety net in the design doesn't exist:** layload.md says load reads
   each new file system back with dump's parser and stops if its features
   differ from the JSON ("the real guarantee").  load doesn't do that: status
   checks only content type and UUID.  The only feature comparison today is
   the HoltTest1 judge, after the fact, in tests.  Nothing stops a real
   restore when a future mkfs default slips through, and mkfs.f2fs can't even
   turn a feature off (`-O` has no `^`).  plan's check 4 ("the stick's tools
   can recreate every feature") isn't in plan.rs either.

## Per-file-system table

R = recorded by dump, P = passed by load.  "Left to defaults" is what the
image's mkfs chooses.  "Risk" is whether that default can make the restored
file system unmountable or unbootable on the source's distro.

| FS | Features | Geometry and identity | Left to the image's defaults | Risk |
|---|---|---|---|---|
| ext2/3/4 | R: all three masks, unknown bits named `FEATURE_*`.  P: `-O none,<every settable feature>`, so orphan_file, metadata_csum_seed, large_dir and so on follow the source.  Tested: the image's mke2fs gives exactly the listed set, because `-O none` comes after mke2fs.conf | R+P: block, inode size, inode count, blocks per group, flex_bg size, hash seed, RAID stride and width, descriptor size, reserved GDT (`resize=`), UUID, label, exact size | default hash algorithm (half_md4), default mount options (acl,user_xattr), journal size, `-m` (set later by count) | **none**: the hash and mount options are no on-disk feature an old e2fsck, kernel or GRUB 2.06 refuses |
| XFS | R: all version, features2, ro_compat, incompat and log_incompat bits.  P: crc, finobt, rmapbt, reflink, inobtcount, bigtime, sparse, nrext64 and ftype as 0 or 1; **exchange, parent and metadir only when on** | R+P: block, sector, inode and dir block size, data size and AG size, stripe unit and width, log size, version and su, lazy-count, external log, UUID, label | **exchange, parent, metadir** (6.18's LTS defaults: exchange=1, parent=1, metadir=0); imaxpct (recorded, not passed); `autofsck` | **yes**: gap 1 |
| btrfs | R: incompat and compat_ro.  P: all nine choosable features, each `F` or `^F` (block-group-tree, squota, raid1c34 and so on).  Tested: incompat 0x361 and compat_ro 0x3, as asked | R+P: csum, node and sector size, data and metadata profiles, mixed, fs and device UUID, label | raid-stripe-tree (in EXPERIMENTAL builds only; the image's isn't one) | none today |
| F2FS | R: feature word, unknown bits named.  P: `-O` with the source's settable features.  mkfs.f2fs can't turn one off | R+P: UUID, label, casefold encoding, segments per section, sections per zone | everything not named.  Tested: 1.16.0 with no `-O` writes feature word 0 | none today.  A later f2fs-tools default would slip through with no way to say no: gap 2 catches it |
| NILFS2 | R+P: every feature `F` or `^F` | R+P: block size, blocks per segment, reserved %, label; UUID by nilfs-tune | none | none |
| JFS | no feature flags to set | P: label; UUID by jfs_tune, superblock version put back to 1 | journal size (not recorded) | none: JFS's format hasn't changed since jfsutils 1.1 |
| FAT | n/a | R+P: FAT12/16/32 (`-F`), sector size, cluster size, reserved sectors, FAT count, root entries, hidden sectors, media byte, volume ID, label.  ESPs written byte for byte where `esp_image` says so | drive number, OEM name, boot code (nothing reads them) | none |
| bcachefs | R: version and version_min.  P: `--version=` the source's version_min (e2b31ad) | R+P: UUID, label, block and btree node size, checksums, compression, str_hash, acl, replicas | other options at the source's version's defaults | none: nixos2605+img PASS on Chopin and Cinqcent, 2026-11-11 |
| ZFS pool | R: every feature's state, every local property (`compatibility` included).  P: `zpool create -d` plus `feature@F=enabled` for the source's enabled and active ones only; local properties as `-o` | R+P: vdev tree, ashift (from labels when it's 0), GUID by reguid | **pool `version` is skipped**: a legacy pool (v28, no feature flags) comes back as v5000 with no features | only for legacy-version pools, which nothing in the corpus has (gap 3, small) |
| ZFS datasets | P: every local and received property, encryption roots with keyformat | | ZPL version (5 since 0.6) | none |
| swap | n/a | R+P: UUID, label, page size | version (only v1 exists) | none |
| LUKS1 | P: type, cipher, key size, hash, offset, UUID | | iterations benchmarked anew | none |
| LUKS2 | P: cipher, key size, sector size, offset, PBKDF and its memory, threads and iterations, hash, metadata and keyslot area sizes, label, subsystem, UUID | | `--integrity` (not recorded; plan doesn't refuse it) | none for old cryptsetup.  An integrity-protected source would come back without integrity: note it, out of scope |
| LVM | P: the source's own metadata text by `pvcreate --restorefile` and `vgcfgrestore` (pe_start, segments, every LV) | | metadata area size | none: old LVM reads it |
| mdadm | P: metadata version, level, chunk, layout, data offset (1.1/1.2), bitmap on or off, UUID, name, homehost | | bitmap chunk, consistency policy | none |

## Plan (once approved)

Source-code changes go to High-effort subagents.  Each one touches only the
lines named here.

**F1. XFS: name every feature the image's mkfs.xfs knows, on or off.**
`mkfs_xfs()` passes `-i exchange=0|1`, `-n parent=0|1` and `-m metadir=0|1`
always, and imaxpct (`-d maxpct=`) as recorded.  Before I commit it, I'll check
that the Debian 13 BIOS rescuer's xfsprogs 6.13 takes `metadir=0`.  If it
doesn't, metadir stays "only when on" and is checked by F2.  Unit test in
commands.rs.  Golden plans: every XFS source's plan changes (`T2_GOLDEN=bless`
after the diff is read): alpine324, chimera, debian13-stack, fedora44-raid,
fedora44-uefi, gentoo, rocky10, rocky10-lvm, rocky10-raid5.

**F2. The read-back the design promises.**  In `format`, right after each
mkfs (and nilfs-tune, jfs_tune, mkswap), load reads the device with dump's own
probe and compares its feature list (and type, UUID, label, block size) with
the JSON's.  It ignores bits that record state (`needs_recovery`,
`orphan_present`, XFS `needsrepair`, F2FS's version stamp, as the HoltTest1
judge does), and stops the stage with a "made with F, the source didn't have
it" message.  It's an in-process check, not a printed command, so plans
change only by one `status`-style line per file system.  That touches all 56
golden plans, and HoltTest2 then runs every cell.  plan's check 4 (the image's
tools know every recorded feature: an unknown `FEATURE_*` or `incompat_bitN`
is refused) lands with it.  Unit tests on sample images (probe's `sample`
modules), one per file system.

**F3. ZFS legacy pool version.**  When the source pool's `version` isn't
5000 or `-`, pass `-o version=N` and no feature flags.  Unit test only (no
source in the corpus has one).

**Not fixing** (no compatibility effect): ext4's default hash, JFS journal
size, LUKS2 integrity (noted in plan's output instead), btrfs raid-stripe-tree.

## Proof: round trips from the real image

`+img` uses the image's own bko-holt, and the images carry 0.2.4.  So F1–F3
reach an `+img` run only through the next image build.  The rescue-images
session (bump-bko-holt-and-rebuild) plans one rebuild of all
seven images with bko-holt 0.2.6, after rel7.  F1–F3 should land in main
before its holt-bin promote, so that one rebuild carries them.

1. **Now, before any fix, from today's image:** `HoltTest2 rocky10-raid5+img`
   on Chopin.  It shows whether the image's mkfs.xfs turns on exchange and
   parent (the judge reports the restored dump's extra features) and whether
   Rocky's GRUB then boots /boot.  One run, about 25 minutes.
2. **After the rebuild, on Chopin and Cinqcent under cmp-run.sh:**
   `HoltTest2 +img` (the Linux panel: debian12 ext4 onto 6.1 and GRUB 2.06,
   ubuntu24 ext4 and OpenZFS 2.2.2, rocky10-raid5 XFS on md, nixos2605
   bcachefs, debian13-stack md+LUKS+LVM+XFS, aerynos f2fs).  Also the file
   systems the panel lacks, through HoltTest1's `img` mode by hand if it takes
   guests outside the panel (to check): zorin18 (NILFS2), cachyos (JFS),
   alpine324 (ext4+XFS onto Alpine's kernel), mx25 (ext2+ext4), rocky10.
   Pass means the judge's feature comparison is clean and each restored guest
   boots its own kernel and mounts everything.
3. The HoltTest2 cells the golden-plan changes make due (all 56 once F2
   lands), as the normal HoltTest2 round.

**Step 1 result (2026-11-14, run fsf0 on Chopin, today's image dev.20261112.5b2fe9d):
PASS.**  The image's mkfs.xfs 6.18 under its 7.0 kernel left exchange, parent and
metadir off: both of rocky10-raid5's XFS (/boot on md, / on LVM on md) came back
with exactly the source's features, and Rocky's GRUB booted it.  So gap 1 hasn't
bitten today's image.  `lts_6.18.conf` lists what `-c options=` turns on, not what
mkfs does by default.  F1 still lands, because xfsprogs' for-next carries "mkfs:
enable new features by default" (parent pointers and exchange-range).  The next
xfsprogs the image takes would then turn them on for every source without them.

## Coordination and docs

- `--mkfs-from` (the compare.py --mkfs-from session): retiring it is
  fine.  This audit and F2's read-back are the remaining coverage.
- layload.md: "Newer tools than the machine" and "Feature sets" rewritten to
  match the code (XFS's named-always list, F2's read-back, the F2FS and JFS
  limits, ZFS version), plus a pointer here.
- Memory `one-kernel-rescue`: status line updated with the result.

## Decisions for the user

1. Approve F1–F3 as above?
2. F2 changes all 56 golden plans, and HoltTest2 then runs every cell (about
   a HoltTest2All).  Alternatively the read-back runs but prints nothing in
   the plan, and only the 9 XFS plans change.  *Recommend:* print it.  The
   plan should show every check load makes, and the image rebuild re-runs the
   cells anyway.
3. Run step 1 (rocky10-raid5+img from today's image) now, before the fix?
   *Recommend:* yes.  It proves the gap is real rather than inferred.
