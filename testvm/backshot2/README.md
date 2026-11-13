# BackShot2 and BackShot2Alpine

Restores onto layouts that drifted: `docs/design/backshot2.md` (approved
2026-11-10) is the design; this directory builds its milestone 1,
BackShot2Alpine (pieces B1-B9; B10, the Thinkpad's tier 3, isn't built).

## Running it

From the Mac, in a checkout of this branch:

    testvm/backshot2/bs2 BackShot2Alpine [N] [plan]   # N cases per source (200), tiers 0-2, then the corpus
    testvm/backshot2/bs2 BackShot2 corpus [--tier 1]
    testvm/backshot2/bs2 replay|shrink CASE [--tier N]
    testvm/backshot2/bs2 soak CASE N
    testvm/backshot2/bs2 status RUN

`bs2` rsyncs `testvm/` and `holt/` to `~/backotter-work/bs2-wt` on both
hosts (never the shared `testvm/`) and runs `bs2-host` there.  Chopin runs
`alpine324` and `alpine324-uefi`, Cinqcent `alpine324-lvm` and
`alpine324-luks`.  Each host needs, once:

- `/bkoscratch/backshot2/bin/`: `bko-holt` (the build under test: a static
  build of this tree's holt, `build-holt.sh linux`), `bs2_plan` (`cargo test
  --release --no-run --test bs2_plan`'s binary) and `caps.json` (`bs2-host caps`);
- each source prepared: `sudo -E bs2-host source SOURCE` (its live dump and
  a copy of its root, for tier 0's dry runs; again after an image update).

## The pieces

| Piece | Where |
|---|---|
| B1 case format, pure edits, replay | `bs2lib/case.py`, `bs2lib/edits.py` (`apply`) |
| B2 generator and the three rules | `bs2lib/generate.py`, `bs2lib/rules.py`, `bs2lib/tables.py`, `bs2lib/geometry.py` |
| B3 tier 0 | `bs2lib/tier0.py`, `holt/tests/bs2_plan.rs` (golden_plans.rs's invariants, now `holt/tests/common/invariants.rs`) |
| B4 `judge.py --expect` | `testvm/holttest1d/judge.py` |
| B5 tiers 1 and 2 | `bs2lib/tier1.py`, `ht1-bs2.sh` (ht1.sh's `HT_VARIANT=bs2` hooks), `vm-run --uefi-vars` |
| B6 shrinker, corpus | `bs2lib/shrink.py`, `cases/` |
| B7 the Alpine sources | `testvm/make-bootstrap.sh alpine-uefi|alpine-lvm|alpine-luks` |
| B8 content drift | `bs2lib/content.py` |
| B9 option sweep | `bs2lib/sweep.py`; judge.py `--new-uuids`, `--removable`, `--nvram-kept`; bs1-restore and bs1-verify (below) |
| ledger | `bs2lib/ledger.py`: Test2's ledger, family `bs2` |

Unit tests: `python3 -m unittest discover -s testvm/backshot2/tests`.

## A case's life

1. **Drawn** on a host, from the source's real dump (it holds identifiers, so
   it stays there; a case holds only numbers, names and edits).  Each edit's
   derived numbers are worked out once, by `geometry.py`'s models, and
   stored in its `sets`: replay never runs a model.
2. **Tier 0**: `load plan` of the drifted layout onto the case's targets with
   its options, golden_plans.rs's five invariants, then the plan's sfdisk,
   mke2fs and mkfs.xfs run in the source's own tools (a chroot of a copy of
   its root) on sparse files in RAM, read back, and compared with the layout.
   A mismatch is classified by running the generator's own command for the
   same values: if that matches, load's command is wrong (FAIL); if not, the
   model is (INVALID).
3. **Tier 1** (tier 0 passes only): HoltTest1's four phases through ht1.sh,
   with the edits applied to the fresh dump, content planted read-write in
   the source's overlay before the backup, targets of the case's sizes, its
   load options, and `judge.py --expect` against the drifted layout.
   `HT_QUICK=1`: HoltTest1's refusal checks are its own, not rerun here.
4. **Tier 2** (UEFI sources, cases that touch an ESP or the NVRAM): tier 1
   with one OVMF variable store for the rescuer and the restored boot, and
   no `--no-nvram` unless the case has it.
5. A FAIL is shrunk (`bs2-host shrink`) and copied into `cases/` as
   `fail-NNN-NAME.case`, with its check and excerpt; "finding" says whose.
   Run the shrink itself without `cmp-run.sh --reserve`: each of its
   tier-1 runs takes its own reservation, so an outer one can deadlock
   it. The shrinker keeps a candidate that fails the same check, so a
   check must name one cause: a case that plants no deep tree gets a
   note, not "the deep tree differs".

## Decisions made inside the spec

- **Known findings are steered around** (`generate.KNOWN`), so the draws can
  find new ones; each stays in `cases/`, and comes back into the draws once
  fixed.
- **The deep tree** of the content drift is planted just under PATH_MAX
  (recipe `alpine-small-2`) until rustic-bko keeps paths past it (fail-001);
  `alpine-small-1`, past it, is the corpus case.  fsmeta.py walks by path, so
  it skips `srv/bs2/deep0` and `bs2-host deep` compares that subtree by
  directory fds.
- **`--new-uuids`** is drawn only when the build plans it (`caps.json`):
  today's load refuses it ("isn't built yet").  judge.py's `--new-uuids` mode
  is built and tested for when it is.
- **`fat_geometry`** isn't drawn: the spec puts it at tier 3.  An ESP that
  moves keeps its FAT; its hidden sectors follow its start where they
  recorded it (0 stays 0), as load moves them.
- **A fitted ext filesystem** is expected as resize2fs would leave it
  (the same blocks and inodes per group, fewer or more groups), at tier 0 too.
- **Moving partitions a UEFI boot entry names** (by start and number) is
  refused in phase 1; the QEMU sources have none such.
- **MBR renumbering** changes the partition's PARTUUID (signature and
  number), so rule 3 refuses it wherever the source names that PARTUUID.
- **Logical and extended partitions** aren't moved, renumbered or resized.
- **Alpine's own rule** holds by construction: no edit changes a
  filesystem's type or adds a stack layer, so mkinitfs's features still fit.
- **Tier 1's "second backup and restore, sometimes"** isn't built yet.
- **The Thinkpad's run** (`run:alpine-3.24.4-20261110-184526`) needs its
  layout.json and repository copied from mybig512 to a host; not done in
  this build (tier 0 and 1 cover the four QEMU sources).
