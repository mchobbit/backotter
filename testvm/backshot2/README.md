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

`bs2` rsyncs `testvm/` and `holt/` to `~/backotter-work/bs2-wt` on its two
hosts, Chopin and Cinqcent (their ssh from testvm/hosts.json; never the
shared `testvm/`) and runs `bs2-host` there.  Chopin runs
`alpine324` and `alpine324-uefi`, Cinqcent `alpine324-lvm` and
`alpine324-luks` (bs2's `HOME`; the small hosts' caches hold only
`alpine324`, the other three aren't published).  It uses ports 2943-2949
(`bs2-host --ports`) on each host it runs on.  Each host needs, once:

- `/bkoscratch/backshot2/bin/`: `bko-holt` (the build under test: a static
  build of this tree's holt, `build-holt.sh linux`), `bs2_plan` (`cargo test
  --release --no-run --test bs2_plan`'s binary) and `caps.json` (`bs2-host caps`);
- each source prepared: `sudo -E bs2-host source SOURCE` (its live dump and
  a copy of its root, for tier 0's dry runs; again after an image update).

## The leftovers set

    testvm/backshot2/bs2 leftovers [SET] [plan]
    bs2-host leftovers [SET] --run RUN [--port P] [--from N] [--keep] [--plan]   # on a host, as m

Restores in a fixed order onto ONE disk (spec: "Leftovers"): the user's
trio first (alpine324-uefi, alpine324-lvm, alpine324-uefi), then LUKS,
md 1.2, md 1.0, ZFS and btrfs, each followed by the plain layout.  It
runs on one host that has all three UEFI sources (chopin by default), one
step at a time, each a tier 1 (`bs2-host tier1`) under its own
`cmp-run.sh --reserve 6 3`.  Each step's restored disk is copied, as load
left it, to `/mnt/bko-ram/backshot2/leftovers-RUN/STEP.qcow2` and is the
next step's target (`BS2_CHAIN_IN`, `BS2_CHAIN_OUT`, read by ht1-bs2.sh's
`bs2_target_make` and `bs2_chain_save`); only the disk the next step needs
is kept (`--keep`: all of them).  The set stops at the first step that
doesn't pass and keeps that step's input, so `--run RUN --from N` reruns it.
`BS2_HOLT` picks the bko-holt (as for every tier).

- `bs2lib/leftovers.py`: the steps, the edits that line the PV and LUKS
  partitions up with the plain root (sector 5244928), and `aligned()`, the
  explicit check that each step's partition begins where the disk's old
  layer begins; generation refuses a set that doesn't line up.
- `leftovers-probe.sh`: the seeds (md12, md10, zfs, btrfs), and the probes
  of the rescuer at boot, of the new partitions after load (wipefs, blkid
  -p), and of the restored system.
- ht1.sh's hooks for it (bs2 only): `bs2_target_make`, `bs2_before_load`,
  `bs2_after_load`, `bs2_chain_save`, `bs2_verify_begin`,
  `bs2_restored_probe`.  They do nothing unless `BS2_CHAIN_OUT` is set.
- Results: `/bkoscratch/backshot2/runs/RUN/leftovers.jsonl`, a row per step
  (verdict, the alignment, the seed, every `note: leftovers:` line); not in
  Test2's ledger (`BS2_NO_LEDGER`), since these aren't the generator's draws.

Runs so far (spec "Leftovers", Results): `lo-before-220c013` (strict:
FAIL at L3, a VG the rescuer autoactivated holds the target) and
`lo-before-220c013-rel` (`--release`: 9 of 9 PASS, no old signature after
load), both on chopin, records in `/bkoscratch/backshot2/lo-before-*-t1/`.

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
