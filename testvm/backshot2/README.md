# BackShot2 and BackShot2Alpine

Restores onto layouts that drifted: `docs/design/backshot2.md` (approved
2026-11-10) is the design; this directory builds its milestone 1,
BackShot2Alpine (pieces B1-B9; B10, the Thinkpad's tier 3, isn't built).

## Running it

From the Mac, in a checkout of this branch:

    testvm/backshot2/bs2 BackShot2Alpine [N] [plan]   # N cases per source (200), tiers 0-2, then the corpus
    testvm/backshot2/bs2 BackShot2 SOURCE... [N] [plan] [--sweep N] [--tier2 N]   # any built source (bs2lib/sources.py)
    testvm/backshot2/bs2 BackShot2All [plan]           # every built source, fresh ("BackShot2All" below)
    testvm/backshot2/bs2 BackShot2All corpus|leftovers|report RUN [--json]
    testvm/backshot2/bs2 BackShot2 corpus [--tier 1]
    testvm/backshot2/bs2 replay|shrink CASE [--tier N]
    testvm/backshot2/bs2 soak CASE N
    testvm/backshot2/bs2 status RUN
    testvm/backshot2/bs2 bin FROM                     # FROM's /bkoscratch/backshot2/bin to the other hosts

`bs2` rsyncs `testvm/` and `holt/` to `~/backotter-work/bs2-wt` on the hosts
it uses (their ssh from testvm/hosts.json; never the shared `testvm/`) and
runs `bs2-host` there.  A BackShot2 run uses every enabled host that holds
the sources (`--hosts` narrows it): chopin, cinqcent and copland hold all
four, cage only `alpine324`.

How a run spreads.  Each source has a lead: its `HOME` host (chopin for
`alpine324` and `alpine324-uefi`, cinqcent for `alpine324-lvm` and
`alpine324-luks`) when that is in the run, else the first that holds it.
The lead draws the source's tier 1 and 2 cases (`bs2-host enqueue --to`),
exactly as one host alone would, and runs its tier 0 sweep.  bs2 then
spreads the cases (`split()`), not the sources: each goes to the host
holding its source that would finish it soonest for its slots, so the
hosts finish about together, and its case file is copied there from the
lead.  Each host runs its own `queue.txt` on `bs2_slots` slots
(testvm/hosts.json: big hosts 5, unmeasured; small hosts 2).  `bs2 status
RUN` adds each host's rows up.  `plan` prints each host's share and hours.

Before the cases are drawn, bs2 checks each host (the rescuers and their
keys, ht1.sh's debs, rustic-bko, and `/bkoscratch/backshot2/bin/` alike on
every host; it stops, naming what is missing).  Then each host goes on by
itself (`start_hosts()`): it prepares the sources it holds (`bs2-host
source`, as root under `cmp-run.sh --reserve 2 3`, port 2949; nothing to do
when ready); a lead draws its sources' cases; and the host starts its queue
as soon as the cases it will run are drawn and copied to it, not once every
lead has drawn every source's.  Its share is the split of the planned cases
(what `plan` prints), each filled with the drawn case of its place in its
list: the same split as when the run split the drawn cases after the last
draw.  The leads draw, and run their tier 0 sweeps, as root outside the
reservations (`light()`, still in a scope with its wear-log line): tier 0
is planning, no VM, and uses one CPU, 50-150 MB and a few sparse scratch
files in RAM (the wear log, 2026-11-17/18); under a `--reserve 2 2` on
2026-11-18, chopin's draws waited ~1h45m behind other jobs for room they
never used, and every queue with them.  A host whose generation of a source
differs from the lead's, or that anything else fails for (its preparing, a
lead's preparing or drawing, the copy), isn't started; the others run, and
bs2 exits 1 naming it and why.

Ports 2943-2949 on each host: the queue's slots 2943-2948 (at most 6), a
source's preparing and a tier 1 by hand 2949.

Each host needs, once:

- `/bkoscratch/backshot2/bin/`: `bko-holt` (the build under test: a static
  build of this tree's holt, `build-holt.sh linux`), `bs2_plan` (`cargo test
  --release --no-run --test bs2_plan`'s binary) and `caps.json` (`bs2-host caps`);
  the same on every host (`bs2 bin FROM` copies one host's to the others);
- ht1.sh's (`/bkoscratch/holttest1d/bin/`'s debs, the rescuers).

Times (`SECS` in bs2): tier 1 measured ~155 s a case (2026-11-11, cinqcent,
400 cases on 3 slots: 6.1 h; source 21 s, backup 4-14, rebuild ~95, verify
~26), ~50 s of it the rescuer's apt of the bcachefs debs and 5 s polls;
ht1.sh now installs bcachefs only for a layout that has one, and polls every
second, so ~110 s est.; with what a case reuses (below) ~65 s est., tier 2
~95 s est.  Neither is measured yet.

## What a case reuses

Work that is the same for every case of a source is done once
(`bs2lib/reuse.py` has the rules, `ht1-bs2.sh` the hooks).  Each is on
unless its variable is 0 (set on the Mac, `bs2` passes it on to the hosts),
and a case that can't take it does its own, with a `note:` line saying why:

| Variable | What | Saves (est.) |
|---|---|---|
| `BS2_REUSE_SOURCE` | the source phase takes the preparation: an overlay on its shut-down overlays, its dump, boot state, lvmid, health and serial log | ~19 s |
| `BS2_REUSE_BACKUP` | a case with no content drift and one snapshot copies the preparation's backup (and fsmeta record) into its share | ~3.5 s on ~27% of cases |
| `BS2_RESCUER_STATE` | the rescuer resumes from a saved state; targets and decoy hot-plugged | ~13 s |
| `BS2_FAST_DOWN` | QEMU quits once synced, not a guest poweroff waited for (the rescuer, and the restored boot) | ~8 s + ~2.5 s |

- **The preparation** (`bs2-host source`, which bs2 runs on every host
  before a run): ht1.sh's own source and backup phases on the source, with
  `BS2_PREPARE=1` (no case: no edits, no content, one snapshot), on
  /bkoscratch in `sources/SOURCE/preps/NAME` (`prep` points at the current
  one; the 3 newest stay).  Its `prep.json` keys it: the generation, the
  bko-holt (the dump is bko-holt's), ht1.sh, ht1-lib.sh, ht1-bs2.sh, vm-run,
  vm-mount and fsmeta.py, vm-run-options, the image files' size, mtime and
  inode, and the backup's rustic-bko.  `bs2-host source` makes a new one
  when any of them changed (and so redoes every older preparation); a case
  that finds it stale boots the source itself.  Its dump is the generator's
  `src.json` too, so cases are drawn from the dump tier 1 uses.  A tier 2
  case takes it only when its source boots with `--no-secure-boot` already
  (all of the Alpine sources do).
- **The backup** is the same for every case that changes nothing backed
  up: from the code, only `content` (planted read-write into the source
  before the backup, `bs2-host plant`, from the case's seed and drifted
  layout) and `snapshots` (N backups with `bs2-host change` between) do;
  the edits, targets, load options, tier and leftovers chain reach load,
  not the backup (`bs2-host apply` prints `BS2_SHARE_BACKUP`).  The copy is
  the case's: the rescuer mounts the share read-only, and load restores
  `latest:` the preparation's mount point (`bs2-snap-path`).
- **The rescuer's state**: the first case on a host that finds none for its
  key (`bs2-host rescuer-key`: rescuer image and its .sha256, firmware
  options, memory, QEMU, OVMF, host kernel and CPU microcode, vm-run,
  ht1-bs2.sh, bs2-qmp.py) boots the rescuer with no disk of a case's and a
  vmgenid device (and on q35 six PCIe root ports, in tier 2's slots 3-8,
  a blank stand-in behind each at the boot, so that the firmware makes the
  options a cold boot makes for the targets there, unplugged once it is
  up), waits for its boot to settle, stops its timers (a resume jumps its clock: they would fire
  mid-load), and saves RAM and devices with QMP `migrate` to
  `/bkoscratch/backshot2/rescuer-state/HASH/` (its disk as an overlay, its
  NVRAM beside it); a newer state of the same rescuer and firmware replaces
  the older.  Each case then resumes it (`-incoming`, an overlay on its
  disk, a copy of its NVRAM: tier 2's `vars.fd`, which the restored boot
  keeps), sets its clock, feeds its RNG the host's bytes and reseeds it,
  hot-plugs each target then the decoy, waiting for each, and checks the
  kernel named them vdb.. with the sizes a cold boot would show.  The share
  isn't mounted when saved (9p blocks migration while mounted) and is
  mounted by the setup as ever.  The leftovers set always boots cold.
- **Quitting**: after load's close, if no target or the decoy has a holder,
  mount, swap or imported pool, the guest syncs and QEMU gets TERM, its own
  clean exit, which drains and flushes its disks (qcow2's metadata too)
  before it exits; anything still holding a target: a poweroff as ever.

A first run should compare: the same cases with all four at 0 and at 1
(identical verdicts expected), the `note: rescuer:` and `note: source:`
lines (how often each falls back), and phases.tsv's seconds.

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
load), both on chopin, records in `/bkoscratch/backshot2/lo-before-*-t1/`;
after the zeroing change (8b7e177), `lo-after-8b7e177` and
`lo-after-8b7e177-rel`: the same results, step for step.  With load
releasing holders under `--erase` (e452150), the strict `lo-strict-e452150`
(on cinqcent): 9 of 9 PASS, the VG at L3 and md127 at L6/L7 let go of by
load itself.

## The pieces

| Piece | Where |
|---|---|
| B1 case format, pure edits, replay | `bs2lib/case.py`, `bs2lib/edits.py` (`apply`) |
| B2 generator and the three rules | `bs2lib/generate.py`, `bs2lib/rules.py`, `bs2lib/tables.py`, `bs2lib/geometry.py` |
| B3 tier 0 | `bs2lib/tier0.py`, `holt/tests/bs2_plan.rs` (golden_plans.rs's invariants, now `holt/tests/common/invariants.rs`) |
| B4 `judge.py --expect` | `testvm/holttest1d/judge.py` |
| B5 tiers 1 and 2 | `bs2lib/tier1.py`, `ht1-bs2.sh` (ht1.sh's `HT_VARIANT=bs2` hooks), `vm-run --uefi-vars`; what a case reuses: `bs2lib/reuse.py`, `bs2-qmp.py` |
| B6 shrinker, corpus | `bs2lib/shrink.py`, `cases/` |
| B7 the Alpine sources | `testvm/make-bootstrap.sh alpine-uefi|alpine-lvm|alpine-luks` |
| B8 content drift | `bs2lib/content.py` |
| B9 option sweep | `bs2lib/sweep.py`; judge.py `--new-uuids`, `--removable`, `--nvram-kept`, `--disk-ports`; bs1-restore and bs1-verify (below) |
| ledger | `bs2lib/ledger.py`: Test2's ledger, family `bs2` |
| A1 the family interface (BackShot2All) | `bs2lib/families/` (registry `__init__.py`, `base.py`, one module per family or trait); `tests/test_families.py` (registry, goldens `tests/golden/FAMILY.json`, smoke over every Linux corpus dump), `tests/test_family_*.py` |
| A2 every built Linux source at tiers 1 and 2 | `bs2lib/sources.py` (the source table), `bs2lib/room.py` (`scratch()`: RAM or SSD), bs2-host's queue (the SSD lane), `tests/test_bs2_sources.py`, `tests/test_bs2_placement.py` |
| A7 content recipes per filesystem | `bs2lib/content.py`, `bs2lib/families/linux.py` `choose()`, `tests/test_family_content.py` |
| A9 the BackShot2All word | `bs2` (`BackShot2All`), `bs2lib/allrun.py` (counts, SKIPPED, report, verdict), `tests/test_bs2_all.py` |

Unit tests: `python3 -m unittest discover -s testvm/backshot2/tests`
(~80 s: the smoke test draws 300 cases from each Linux corpus dump).

## Families

What is a source's own, not generic, comes from its family and traits in
`bs2lib/families/` (`docs/design/bs2-all.md`, "The family interface (A1)",
is the contract every lane builds to).  A **family** is the OS and distro,
one per source, the most specific match winning (`alpine`, `nixos`, `guix`, and
`linux` for the rest of Linux); a **trait** is something the dump records
that sources share (`dracut`, `mkinitcpio`, `booster`, `initramfs-tools`,
`systemd-boot` with BLS entries, `limine`, `refind`, `ostree`, `snapper`,
`grub_upstream`, `proxmox-boot`).  Each module's docstring says what it
refuses and why.  `families.hooks(source, dump)` merges them, once per
case: rule 2's tables and versions, the quirks run after the three rules,
rule 3's texts and patterns, the content recipe (per filesystem since A7;
Alpine and Guix have their own) and where `/srv` really is (`srv(dump)`:
ABRoot's content lands in `/var/srv`),
the sweep, the steer around known findings (`fail-003`'s for Alpine and
every upstream UEFI GRUB, which runs only while `generate.KNOWN` names it:
fixed since GENERATOR 5), and tier 0's mode (`chroot`; NixOS `plan`).  A
source with no family (the BSDs, illumos, Haiku, Redox, Sculpt OS) is
`families.skipped()`: `PLANNED` names the piece that builds it.  Through
`GENERATOR` 2, Alpine's cases were byte-identical to those drawn before the
interface (`tests/golden/alpine.json`); each family's golden is rewritten
only with a new `GENERATOR` (3, 2026-11-17: fail-004 and fail-005 back in
the draws; 4, 2026-11-18: geometry edits and resizes keep the free inodes
and blocks, and rule 1 checks a fitted ext's inodes as load does; 5,
2026-11-21: fail-001, fail-002 and fail-003 fixed and back in the draws;
6, 2026-11-24 (A7): content drift on every Linux source, the recipe
following the filesystem holding `/srv/bs2` (`content.RECIPES`: `ext-1`,
`xfs-1`, `btrfs-1`, `f2fs-1`, `jfs-1`, `nilfs2-1`, `bcachefs-1`, and
`ext-1-32bit`/`xfs-1-32bit` for an ext without extra_isize or an XFS
without bigtime; `families/linux.py` `choose()`), None for ZFS (A3),
ostree (kinoite44), Ubuntu Core and a `/srv` too small; Alpine's and
Guix's draws unchanged apart from the generator field.  `plant()` and
`change()` walk to `/srv/bs2` by fd, following only relative links that
stay inside the source, so nothing is ever planted on the host).


Guix System (`guix15`, `guix15-bios`) has a family of its own since
2026-11-24 (`guix.py`; it was generic Linux before): its swap and its older
generations' file systems aren't in the dump, so neither is renumbered;
rule 3 reads every generation's boot parameters; content recipe
`guix-small-1` (on Btrfs too); tier 0's dry runs use the system profile's
tools (`tier0.PATH`).  It runs from the Mac as

    BS2_WT=bs2guix-wt testvm/backshot2/bs2 --hosts chopin,cinqcent BackShot2 guix15 guix15-bios 10 --sweep 200 --tier2 3

(`BS2_WT` names the hosts' copy, so two sessions' runs never share one;
`--sweep` and `--tier2` set a source's tier 0 and tier 2 counts; tier 2 is
for the UEFI sources, bs2's `UEFI`; each case reserves its source's room,
`bs2lib/room.py`: HoltTest1's D 7 and 6, MEM 4, so only a host whose
`cmp-run.sh --ceiling` holds that takes them: on 2026-11-24 chopin and
cinqcent (29.0 23.2), copland, cage and czerny (9.0 10.1), not choubox
(3.0 6.0), which bs2's `held()` leaves out for these sources, saying so,
though its cache has both guests; `BS2_HOLT=PATH` runs another bko-holt on
the hosts).  A queued case whose `cmp-run.sh` fails before the case writes
its row (a reservation refused) gets a HARNESS row in `tierN-queue.jsonl`
from the queue, so `bs2 status` counts it rather than losing it.  Its first run,
bs2a-20261124-1028, found fail-006 (load kept grubx64.efi's "(,gpt3)"
prefix after / was renumbered: bko-holt), fail-007 and fail-008 (the
harness's model of a moved BIOS boot partition, and of btrfs's symlink
limit and big_metadata at a 4 KiB node), all fixed; the second,
bs2a-20261124-1111 (`BS2_HOLT` the fix's build, 20 tier 1 a source, 5 tier
2): tier 0 229 + 220 PASS, tier 1 guix15 20/20 (three renumbering /),
guix15-bios 20/20, tier 2 guix15 5/5.  Measured, on contended big hosts: a
guix15 case ~235 s at tier 1 and ~230 s at tier 2, a guix15-bios case
~153 s; peaks 5.3-5.8 GiB of RAM disk (guix15) and 4.6-4.8 (guix15-bios),
within room.py's 7 and 6.

`guix15-luks` (Guix's own encrypted layout: LUKS1 `cryptroot` holding the
Btrfs, GRUB opening it; docs/design/guix.md "guix15-luks") is not one of
bs2's sources yet, on purpose (2026-11-24).  Taking it is real new work,
not a list entry: bs2's LUKS model is LUKS2's only (`edits.LuksGeometry`
and `geometry.luks2` refuse LUKS1, so its header could never drift, and
nothing models a LUKS1 payload offset or what load's new `luks1-digest`
step keeps); the Guix family's fixtures and golden (`tests/corpus_cases.py`
AS_RUN, `tests/golden/guix.json`) would need its third shape; HOME's
assignment would change (Test2's `test_split_hosts` holds it); and its
tier 0 to 2 runs (each boot typing the passphrase at GRUB and the initrd)
would need their first round.  About half a day, for a later lane.
HoltTest1 and HoltTest2 cover its restore meanwhile.

Two more traits came with Vanilla OS (`vanilla3`): `abroot` (the master
grub.cfg's entries and each root's abroot.cfg as rule 3 texts; load
restores both roots, the init LV and /boot as they were) and `lvm-thin`
(any thin pool: no `lv_split` or `lv_resize` of the data, metadata and
spare LVs, which load's lvcreate sizes and places itself).  A guest new to
the corpus joins its family's golden as one more entry under `guests` and
`dumps`, drawn by the same `GENERATOR`, the others' left as they were.

## BackShot2All

`docs/design/bs2-all.md` is the plan; A2, A7 and A9 (the Linux lane) are
built, A3-A6 and A8 not.

**Sources** (`bs2lib/sources.py`, read at run time): every guest of
`holt/tests/corpus/index.tsv` with a `src.json`, amd64 only.  `classify()`
says "built" (bs2 runs it: today 54, every one-disk Linux source with a
family, not ZFS or md) or SKIPPED with the piece that builds it: A3 (ZFS,
md, more than one disk: derived from the dump), A4 (the BSDs), A5
(illumos), A6 (Haiku, Redox, Sculpt OS), A8 (BackShot1's real machines),
"later" (`EXCLUDE`: guix15-luks, LUKS1; uc24-fde, not a HoltTest2 source).
`uefi()` comes from the dump (tier 2 for those only, on the cases that
touch an ESP or the NVRAM, `case.touches_esp`); `secs(source, tier)` is a
case's seconds: measured for Alpine's four and Guix's two, else 0.7 of
HoltTest1's `wall_s` (`testvm/test2/estimates.tsv`), at least 120 s, est.
Alpine's and Guix's HOME stay as they were; the rest are dealt over the
lead hosts after them.

**RAM or SSD** (`room.scratch()`): a source's cases work in the RAM disk on
the hosts whose `cmp-run.sh --ceiling` holds its room (HoltTest1's D and
MEM, `holttest1d/ram-sizes.tsv`), as ever.  Only a source over every RAM
ceiling of the run (vanilla3, D 30 GiB) goes to `/bkoscratch`: on hosts
with `holt_disk_mode` true (chopin and cinqcent), `cmp-run.sh --reserve-ssd
D M`, `HT_RAM=0`, its rows `"scratch": "ssd"`, and one such case at a time
per host (the SSD lane, a flock on `/bkoscratch/backshot2/ssd.lock`: the
queue takes the earliest SSD case whenever the lane is free and RAM cases
on its other slots; replay, soak and the corpus take the lane before their
reservation, `bs2-host lane`).  A vanilla3 case writes up to ~29 GB.

**The word**: `bs2 BackShot2All [plan]` runs, fresh (the ledger excuses no
case), as `bs2all-YYYYMMDD-HHMMSS`, every built source a host holds: 1,000
tier 0, 20 tier 1 and 5 tier 2 (UEFI) a source, Alpine's four as
BackShot2Alpine (200 tier 1, 30 tier 2 shared); `RUN/bs2all.json` on each
host records what it planned and SKIPPED.  Unbuilt sources and tier 3 are
SKIPPED by piece; a built source no host of the run holds is SKIPPED "not
on any host in this run".  The corpus and the leftovers set aren't chained
(both take port 2949): once the queues are done, run `bs2 BackShot2All
corpus RUN` (RUN-corpus), then `bs2 BackShot2All leftovers RUN` (RUN-lo).
`bs2 BackShot2All report RUN [--json]` gathers every host's rows and says
FAIL (any FAIL, the corpus's and leftovers' too), INCOMPLETE (rows short of
the plan, a host that didn't answer, HARNESS, INVALID or UNSUPPORTED rows,
the corpus or leftovers not run), PARTIAL PASS (all passed, something
SKIPPED: every run until A3-A6 and A8 are in) or PASS.  Its rows in Test2's
ledger say word `BackShot2All` (`BS2_WORD`, passed on like `BS2_HOLT`);
BackShot2's say `BackShot2`.

First runs (2026-11-24, `BS2_WT=bs2all-wt`): bs2a-20261124-1738, nine
sources on five hosts (debian13, fedora44-uefi, arch, rocky10, mint22,
cachyos, nixos2605, zorin18, aerynos; 2 tier 1, 1 tier 2 a UEFI source):
tier 0 653/653, tier 1 18/18 PASS (content on ext-1, xfs-1, btrfs-1,
f2fs-1, jfs-1, bcachefs-1), tier 2 2 FAIL on by-path links alone, fixed in
judge.py `--disk-ports` (d1c0974), PASS on replay; bs2a-20261124-1754,
vanilla3 on chopin's SSD: tier 1 PASS, 810 s.  Measured tier 1 ran 1.06
times `secs()`'s estimates.

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
   with the edits applied to the source's dump (the preparation's: "What a
   case reuses"), content planted read-write in an overlay of the source
   before the backup, targets of the case's sizes, its load options, and
   `judge.py --expect` against the drifted layout.
   `HT_QUICK=1`: HoltTest1's refusal checks are its own, not rerun here.
   ht1.sh's `RESULT: PASS` is a PASS only when every phase its header
   names began (`== P`) and ended (its row in `logs/phases.tsv`); short of
   that the case is HARNESS, "phase P never ran (log ends: ...)": bash
   abandons ht1.sh's whole phase loop on an expansion error in a hook, and
   ht1.sh then says PASS for phases it never ran (bs2a-20261114-0655).  A
   preparation's run (`bs2-host source`) is held to the same.
4. **Tier 2** (UEFI sources, cases that touch an ESP or the NVRAM): tier 1
   with one OVMF variable store for the rescuer and the restored boot, and
   no `--no-nvram` unless the case has it.  It is one machine: each target
   sits behind a PCIe root port of its own from pcie.0's slot 3 on (`vm-run
   --disk-ports`), the decoy behind the next, in the rescuer's boot and
   (the targets alone) the restored boot, and the rescuer's own disk on
   pcie.0 after the ports (still vda).  OVMF's option for a disk is its
   device path and "UEFI Misc Device", " N" after it for the Nth disk it
   finds; an option no disk matches is deleted, and a new one goes after
   "EFI Internal Shell", which never returns.  So a source that boots from
   its ESP's removable path (no entry of its own: load writes none) boots
   through the option the rescuer's boot made for its disk, as a real
   machine's internal disk keeps its own, only with the same path and rank
   in both boots: the firmware walks a port's bus at the port, before the
   rescuer's disk, which Linux probes first (bs2a-20261114-0655: with the
   target on pcie.0 after the rescuer's disk, the restored boot ended in
   the shell).  The source was dumped with its disks on pcie.0, so the
   restored dump's `/dev/disk/by-path/` links name other PCI addresses:
   tier 2 judges with `--nvram-kept --disk-ports`, and `--disk-ports`
   lets a by-path link differ in its leading `pci-`/`virtio-pci-` address
   alone (the rest of the link the source's, one new address for each old
   one); any other link change is a DIFF (bs2a-20261124-1738).
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
- **The deep tree** of the content drift goes past PATH_MAX (recipe
  `alpine-small-1`, `bs2lib/families/alpine.py`) since GENERATOR 5:
  rustic-bko keeps such paths since rustic_core 6b1e432 (0.13.0+backotter.17,
  fail-001), so a run needs bko-bin's rustic-bko at least that new.
  Through GENERATOR 4 it was planted just under PATH_MAX (`alpine-small-2`).
  fsmeta.py walks by path, so
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
  filesystem's type or adds a stack layer, so mkinitfs's features still fit
  (the `mkinitfs-layers` quirk checks it, as each initramfs trait does).
- **Tier 1's "second backup and restore, sometimes"** isn't built yet.
- **The Thinkpad's run** (`run:alpine-3.24.4-20261110-184526`) needs its
  layout.json and repository copied from mybig512 to a host; not done in
  this build (tier 0 and 1 cover the four QEMU sources).
