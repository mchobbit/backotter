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
GENERATOR = "2"
