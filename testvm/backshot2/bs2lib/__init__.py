"""bs2lib: BackShot2's library (docs/design/backshot2.md), imported by
testvm/backshot2/bs2 on the Mac and testvm/backshot2/bs2-host on the KVM
test hosts.  Stdlib only; Python 3.9 or newer.

- dumpx.py     reading and walking a bko-holt dump: disks, partitions,
               devices, what sits on what, used bytes
- geometry.py  models of what each source tool makes from its options
               (mke2fs, mkfs.xfs, mkfs.fat, mkfs.btrfs, cryptsetup, LVM)
- tables.py    rule 2's tables: the values each tool and version allows
- edits.py     the edits, and applying a list of them to a dump (B1: a pure
               function, so a drifted layout is always rebuilt, never stored)
- rules.py     the three rules, as checks over a drifted layout
- case.py      the case file: reading, writing, its hash, replay
- generate.py  the seeded generator (B2)
- content.py   content drift: recipes and the planter (B8)
- sweep.py     the option sweep: load's flags per case (B9)
- shrink.py    the shrinker (B6)
- tier0.py     the tier 0 runner (B3), run on a host
- families/    what is a source's own: its family and traits, merged by
               families.hooks() (docs/design/bs2-all.md, "The family
               interface (A1)")
"""

# The generator's version, recorded in every case it makes.  2 (2026-11-11):
# swap sizes in whole pages, XFS's 4 KiB sectors set the log's, renumbered
# partitions kept in number order, the MBR gap follows the first partition.
# 3 (2026-11-17): fail-004 and fail-005 fixed in bko-holt and out of
# generate.KNOWN, so 1 KiB ext blocks and targets that shrink an ext past
# its inodes' groups are drawn again.  4 (2026-11-18): a geometry edit or
# resize carries the filesystem's free inodes and blocks onto its new
# totals (edits.usage_kept), and rule 1 holds a fitted ext's inodes in use
# to load's own check (fitcalc.ext_inodes_after): bs2a-20261118-1757.
# 5 (2026-11-21): fail-001, fail-002 and fail-003 fixed (rustic_core
# 6b1e432, bko-holt e8986b2) and their steers lifted: Alpine's deep tree
# goes past PATH_MAX (alpine-small-1), /boot's partition is renumbered on a
# UEFI source that boots from the fallback loader, and --removable is drawn
# for Alpine and upstream UEFI GRUB again.  6 (2026-11-24, A7): content
# drift on every Linux source, a recipe per filesystem (content.RECIPES:
# ext, xfs, btrfs, f2fs, jfs, nilfs2, bcachefs; families/linux.py choose()),
# so generic Linux's and NixOS's cases change; Alpine's and Guix's draws
# are GENERATOR 5's but for the "generator" field.  7 (2026-11-25): Ubuntu
# Core's gadget-fixed partitions keep their size (families/snapcore.py's
# quirk: only ubuntu-data may change size), so uc24's and uc24-fde's cases
# change; every other source's are GENERATOR 6's but for the field.
# 8 (2026-11-25): an xfs_geometry log is kept within mkfs.xfs's limit
# (geometry.xfs_log_max_blocks: 2 GiB - 10 MiB, 2^20 blocks) and redrawn
# over it, so a case on an XFS source that drew a longer log changes
# (slackware15-uefi-5125fd5f9417d3ee's 2139430912 bytes: "Value is too
# large"); every other case is GENERATOR 7's but for the field.
# 9 (2026-11-26): the sweep draws --reformat MOUNT=TYPE (decision 4,
# approved 2026-11-26) in sweep.REFORMAT_P of the cases, where load is meant
# to take one (sweep.reformat_choices) and the case has no --fit-largest or
# --new-uuids, from a stream of its own ("SOURCE:SEED:reformat"): a case
# that draws one is GENERATOR 8's with "--reformat MOUNT=TYPE" after its
# other options, every other case is GENERATOR 8's but for the field.
GENERATOR = "9"
