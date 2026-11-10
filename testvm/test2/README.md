# Test2: targeted tests for BackOtter

Test2 runs a check only when one of its inputs changed, and only where the change
reaches (`docs/design/test2.md`).  A ledger keys every result by its inputs; a
planner compares the inputs now with the last PASS and picks what to run.  This
directory holds the words GateTest2, SticTest2, HoltTest2, GuestTest2, ImgTest2,
BkoTest2 and SweepTest2, the ledger, the planner, the runners and their specs.

Everything runs from the Mac: `testvm/test2/t2`.  The hosts run the cells, from a
private copy of this checkout (never from the synced `~/backotter-work/testvm`).

## The words

| Word | What it runs | When nothing changed |
|---|---|---|
| `GateTest2 [ROUND] [LAYER...] [stale]` | the due gate layers (no VMs) | prints one line: nothing due |
| `GateTest2All [ROUND]` | every layer, fresh | runs them all anyway |
| `SticTest2 [ROUND] [GUEST...] [stale]` | the due SticTest checks on ROUND's binaries | one line |
| `SticTest2All [ROUND]` | all 41 checks, fresh (SticTest1's scope) | runs them all anyway |
| `HoltTest2 [GUEST[+VARIANT]...] [stale] [+bigger] [+oneshot] [+degraded] [+img]` | the due HoltTest cells | one line |
| `HoltTest2X [X...]`, X in D A M U Z F C V BSD H I Obscure | that family's guests, fresh (HoltTest1's letters) | runs them anyway |
| `HoltTest2All` | all 49 sources and their 9 variant cells, fresh (no `+img` cells) | runs them all anyway |
| `HoltTest2 G+img`, `HoltTest2 +img` | those cells fresh, a real rescue image as rescuer; `+img` alone: all 12 | runs them anyway |
| `GuestTest2 [GUEST...] [stale]` | the due guests, or those guests fresh: identity, image checks, a `--try` in RAM, the checks after it, the log sweep | one line |
| `GuestTest2All` | the graded audit: all 51 guests fresh, sha256 identity | runs them all anyway |
| `GuestTest2 update [GUEST...]` | the real update round (merges); never with `stale` or All | a bare round needs a `--try` PASS within 7 days |
| `ImgTest2 [PLAT...] [release] [stale]` | images built since their PASS and owed panels, or those platforms fresh; `release` requires the panel | one line |
| `ImgTest2All [release]` | every current image, its panel included | runs them all anyway |
| `BkoTest2 [ROUND] [stale]` | every family's due cells in one queue; names and All are refused | one line per family |
| `SweepTest2 [ROUND]` | every All form but ImgTest2's, fresh; names, `stale` and All are refused | runs them all anyway |

`WORD All` is the same as `WORDAll`, and `HoltTest2 D` or `HoltTest2 2D` the same as
`HoltTest2D`; `HoltTest2 --holt-bin DIR` tests a ready bko-holt set (DIR/bko-holt,
DIR/PLAT/bko-holt and DIR/SHA256SUMS, on every host in use) instead of building one:
its rows count only where the binaries are equal (R14).

- Add `plan` to any form (or say `t2 plan WORD ...`) to see the selection, the reasons
  and the estimates (gm, wall, GB) without starting or writing anything.
- Guest names and family letters run those cells fresh, and nothing else.  `stale`
  adds the soft-stale cells.  A bare run with due cells takes the 2 stalest
  soft-stale (or canary-verified) cells along beside it.
- A variant word adds that variant's cell for each selected guest it applies to;
  alone it selects every cell of that variant, plus the bare word's due cells.
- ROUND is `~/backotter-work/roundROUND/bin` on both hosts.  Without it, SticTest2
  takes the round at the fork heads (BKO_GIT's `backotter` HEADs), or builds it as
  `t2r-R7-C7-U7`; GateTest2 takes that round, else bko-bin.
- Refused (exit 2): `+close` (waits for H3), an image with a binary that has no PASS at
  its digest (`ImgTest2` names the binary and the command; a bare run runs its other due
  images first, then exits 2), freebsd15-ufs
  (the FreeBSD rescuer) and freebsd15-geli (no GELI in holt) as HoltTest sources,
  unknown names, a ROUND missing on a host in use, a second run of a family while
  one runs (its run id, PID and start are named).

Examples:

    testvm/test2/t2 SticTest2 rel4 plan
    testvm/test2/t2 HoltTest2D plan
    testvm/test2/t2 GateTest2 holt fork --hosts chopin
    testvm/test2/t2 SticTest2 rel4 debian13 freebsd15
    testvm/test2/t2 explain stic:debian13
    testvm/test2/t2 resume t2s-20261108T0315-4f2a

Other commands: `t2 pull` (each host's ledger mirrored, then the wear join),
`t2 ledger show [--cell C] [--last N] [--json]`, `t2 explain CELL` (its inputs
against its last PASS, group by group), `t2 copy [--hosts H,H] [--gc]`,
`t2 resume RUN` (on the run's own hosts and cells; preflight runs again; `t2b-` and
`t2w-` resume the composite), `t2 guest-lines RUN [--show [GUEST]] [--accept GUEST...|--accept-all]`
(a GuestTest2 run's new log lines; accepting adds them to the Mac's baseline and writes a
`class` row, refused unless `logs` is the row's only unmet need), `t2 guest-fingerprints
[GUEST...] [--reproject]` (probes guests with no fingerprint at their generation; never
inside BkoTest2), `t2 self-test`, and the test-only `t2 ledger seed-current`
(`--family guest` sets `extra.try_ran`, `--family img` seeds PASSes at the images' binaries' digests).  Options anywhere:
`--hosts H,H` (default chopin,cinqcent; each host once), `--json` (the plan as
JSON), `--universe CELL,...` (test only: the word's cells cut down to these).  Every
plan and run pulls the `--hosts` and every host with a mirror in `MAC_ROOT/ledger`,
since the merged view reads them all; a mirror that wasn't refreshed is said so.

Exit codes: 0 PASS, nothing due, or a plan printed; 1 a FAIL or FLAKY-FAIL, or a
stop rule stopped the run; 2 refused (a word's named cells can't run, an image a binary
keeps from running, even after the due ones finish, a bare `GuestTest2 update` with a guest's
`--try` too old); 3 an internal error.

## How a plan decides

A cell is one SticTest check (`stic:GUEST`), HoltTest guest or variant
(`holt:GUEST[+VARIANT]`) or gate layer (`gate:LAYER[@HOST]`).  Its key hashes its
hard inputs: binaries, harness, judges, the guest (its image-state generation
stands in for a fingerprint until V1), cross-guest inputs, the rescuer and, for
host-side checks, host versions.  Plumbing (the queue, vm-run, vm-lib.sh) and soft
host versions are digested beside the key but never in it.  The keys and their
inputs come from the specs `stic/manifests.json`, `holt/spec.json` and
`gate/layers.json`, through `t2lib/keys.py`.

For each cell, in order (`t2lib/plan.py`):

1. A name, family letter or `All` selects it (`fresh:names`, `fresh:family:X`,
   `fresh:variant:V`, `fresh:All`).
2. Rows at its current key: the newest FAIL or FLAKY-FAIL makes it due (`failed`).
   Otherwise its newest PASS there stands, unless a file no spec names changed
   since (`changed:unmapped`), the plumbing changed (a canary is due,
   `canary:plumbing`; the others wait, `awaiting-canaries`, until every runnable
   canary passed with it, `canary-verified`), or the host versions changed (a canary
   is due, `canary:host`; the others are soft-stale).  Else it is current.
3. No PASS ever: due (`new`, `holt:new` with every variant of a new guest; a PASS
   at its key from an uncommitted copy shows as `pass-uncommitted`).
4. Changed since its last PASS: its family's rule selects it (below), or a
   bin-only change is carried (a `carried` row, `carry:...`), or it is soft-stale.

A row counts only if it is valid and committed: its copy was the commit, or HEAD
now has every uncommitted path of it with the same content.  So the PASS of a fix
run from an uncommitted copy counts once the same diff is committed.

**SticTest2.** Any changed hard group selects the cell (`changed:bin`,
`changed:harness`, ...), but a bin-only change that the carry rule covers, or a
judge-only change while `policy.judge_change` is `soft-stale`.  Every Linux check
runs for itself (decision 1).  The non-Linux classes B2, B3 and B4 run their
stalest eligible member (`class:B2`) when their members' changes are identical
(the same inputs with the same new values) and their last need sets equal; the
others are class-pending, and get a `class` row when it passes, or run themselves
(`fanout:B2`) when it fails.

**HoltTest2.** bko-holt keys as `code:CODE`, built or not.  A version-only holt
change is carried; a change only to `holt.plan_visible` (commands.rs) runs the
guests whose golden plan changed, their variants when the diff touches partition,
assemble or close, and the degraded cells, and carries the rest (`carry:plan-diff`);
before the plan diff is known (no blessed plan change, no gate:holt row at the
current holt key) all 49 run (`holt:code`), as for any other code change (the stand-in
for M1's module map).  A rustic-bko-only change runs that platform's rustic panel and
carries the rest; a rescuer change runs its rescuer panel; a guest's new generation
runs it and its variants; a driver change runs that driver's guests; a judge change
follows `policy.judge_change`.

**The carry rule** (`t2lib/carry.py`), for each changed binary: version-only (the
forks' diff changes only `*.md` and version lines), affected-v1
(`testvm/compare/affected.sh` names platforms without the binary's OS, and its
`.digest` lines other than the sources are unchanged; never for Haiku), or
dfly-image (DragonFly's build image alone moved).  A binary that changed while its
source commits didn't is never carried, but under dfly-image.

## The class table: classes.json

`stic.classes`, `stic.alone`, `stic.canaries` (9, one per run style),
`holt.families` (decision 11; devuan6 in none), `holt.family_variants`,
`holt.variants`, `holt.pins`, `holt.uefi` (R17: a UEFI source keys only on hosts with
rescuer-uefi.qcow2), `holt.canaries`, `holt.rescuer_panel`, `holt.rustic_panel`,
`holt.plan_visible`, and `policy`: `judge_change`, `beside`, `budget` (warn only
while `bind_from` is null or a later date; it binds from that date), `stop`, `sweep` (due every 7 days until
`shadow_days` after the first complete SweepTest2, then every 14), `guest` (`run`: the 7-day `--try`, RAM sizes,
failed-state caps; `judge`: free-space limits, a judge input), `watch` and `unmapped_ignore` (R5).  `t2 self-test` checks it.

## Estimates: estimates.tsv

A cell's estimate is the median of its last 3 PASS-like run rows (gm, wall, wear),
else its row in `estimates.tsv`, else the SticTest README's estimates, else 5 gm.
The file was seeded on 2026-11-08 from `docs/design/test2/data/`: SticTest medians
of every exit-0 run in st1_marks.tsv (281.9 gm for the 41), HoltTest r3all's
seconds (the RAID and mirror guests and the variants take test2.md's figures, ~247
gm for HoltTest2All), the gate from test2.md's GateTest2 cost; every guest runs in
RAM, so GB is 0 (`gb_disk` is what a run on /bkoscratch would write).  Wall time:
SticTest2 by drive.py's queue simulation, HoltTest2 by packing at 3 jobs a host
(guests of 13 GiB or more one at a time), GateTest2 the fast layers in sequence
plus the longest slow layer per host.

## Runs

`t2 WORD` without `plan` (`t2lib/run.py`): the family's lock
(`MAC_ROOT/runs/.lock-FAMILY`), pull, facts, a pull again under the lock, preflight
(refused at Tctl 90 C, no /mnt/bko-ram, m not in kvm, /bkoscratch under 80 GB free),
the plan (`MAC_ROOT/runs/RUN/plan.json`), the private copy on each host, then the
family runner (`t2lib/gate.py`, `stic.py`, `holt.py`), a pull, the derived rows
(carried and class, in `MAC_ROOT/ledger/mac.jsonl`) and the report
(`MAC_ROOT/runs/RUN/report.txt`, also printed).  A bare run with nothing due only
writes its carried rows.  Run ids: `t2g-`, `t2s-`, `t2h-` and `YYYYMMDDTHHMM-XXXX`.

Unfinished runs and leftovers.  A run that began host work (a `cells-HOST.json`) and
has no report.txt, changed in the last 12 hours, refuses a new run of its family:
its lock went with the Mac process, but its checks or queue runners may still run.
`t2 resume RUN` finishes it; a report.txt in its run dir sets it aside.  A child of an
unfinished BkoTest2 or SweepTest2 (`t2s-S` under `t2b-S`, and `t2h-S-img`) is resumed only
through its composite: `t2 resume t2b-S` (alone it would run outside the composite's
run-wide stops), and the refusals name the composite.  Ctrl-C, TERM or HUP on a composite
trips its guards and waits for the children (exit 130; `t2 resume` goes on).  `--universe`
on BkoTest2 and SweepTest2 needs a test `T2_MAC_ROOT`; it also cuts the stic and holt
children's gate steps to its `gate:` cells (`options.gate_universe`, test only), and a cut
sweep's sweep.json never counts in the default root.  Preflight
removes only a dead run's own leftovers (its made.json paths): never a run holding a
lock or unfinished as above, and nothing of a run any of whose recorded PIDs still
runs or can't be checked (an ssh that fails is no answer).

Derived rows come from the runner's last plan (after its re-plans), for cells this
run didn't run.  A carried or class row goes to a host's key only when that host's
change against the cell's last PASS is the change the plan judged, and none of its
inputs is pending, img:unknown or missing; so a round not built yet derives nothing.
A class row needs the representative's decisive row (final_row, else its last row):
a valid PASS from a committed copy at its planned key.  No carried row is written
after a failed fast gate or a stop-run.  The run's exit is 1 unless every due cell of
the final plan has a valid PASS or a derived row.

A runner gets `ctx.run`, a `RunState`: `run`, `word`, `family`, `mac_dir`,
`host_root`, `hosts`, `copy` (per host: id, path, commit, dirty), `stops` (the stop
rules: `feed(result)`, `feed_host(host, swap_out_bytes, ours_bytes)`, `may_start(os)`),
`flakes`, `limits`, `write_cells(plan, attempt, name, reruns)` (cells.json or
cells-r2.json on every host, atomically, with each host's planned keys),
`replan(facts, rows)` (after a build or the fast holt gate: the plan again, with the
cells added and dropped), `host_run_dir(host)`, `record_made(host, path, pid)` and
`log(text)`.  `run.rerun_map()` and `run.fanout_due()` make the r2 round's cells (a
FAIL reruns once as attempt 2; a known flake's PASS then fails nothing, any other
PASS is a FLAKY-FAIL; a failed representative fans out to its class).

The stop rules: a failed fast gate starts no guest of its family; a failed slow
gate starts nothing new; one need failing on 2 guests of an OS stops that OS; 2
harness-class results stop the run (known flakes aside); over 1 GiB swapped out, or
this run's jobs writing over twice the budget (their wear tags, never other
sessions' writes), start nothing new.

The report has a line per cell (ran or skipped, verdict, rule, why, gm, GB, host,
row), the stops, the rows written, budget warnings (2 GB per host for this run's jobs
by their wear tags; 50 GB per host for the day's Test2 rows plus this run's), the sweep
line, and last the wear line per host and disk (diskstats window, our jobs by wear tag, today by SMART;
never a serial).

## Roots

| Root | Default | Override |
|---|---|---|
| MAC_ROOT | `~/backotter-work/test2` on the Mac | `T2_MAC_ROOT` |
| HOST_ROOT | `/bkoscratch/test2` on each host | `T2_HOST_ROOT` |
| COPY_ROOT | `~/backotter-work/t2/copy` on each host | `T2_COPY_ROOT` |
| BKO_GIT | `~/git` on the Mac (restic, rustic_core, rustic) | `BKO_GIT` |

Tests set all of them; `t2 ledger seed-current` refuses the default MAC_ROOT.

## Tests

    testvm/test2/t2 self-test        every tests/test_*.py, drive.py --self-test, check-manifests.py
                                     over the three specs, leakscan over the corpus and the fixtures,
                                     and the class table's check
    cd testvm/test2 && python3 -B -m unittest tests.test_words tests.test_plan tests.test_carry tests.test_report

They need no host, and run under `/usr/bin/python3` (3.9) and `python3` (3.14).
P2's run in a synthetic world (`tests/fixtures/p2/world.py`: a temporary checkout
with every cell, fixture forks for BKO_GIT, both hosts' facts and seeded rows).
`T2_GOLDEN=bless` rewrites the plan form's golden files in
`tests/fixtures/p2/golden/`.

## Stand-ins until later pieces

Until M1 affected.sh v1 serves the carry rule and any holt code change outside
`plan_visible` reaches all 49 sources; until G2 a judge change makes cells due
(`policy.judge_change`); until H3 `+close` is refused, variants run as runs of their
own and `+img` runs as its own job.  G3, C1, S1 and R1 are later still.

## GuestTest2 and ImgTest2 in the plan

A guest's row with `extra.to_class_only` (a FAIL whose only causes are new log lines or
standing LIMITs nobody has classed) makes the cell `to-class`: reported, exit 1, never
due and never rerun, until `t2 guest-lines --accept` or a `guest/limits.json` entry
classes it.  A `--try` cell whose last `--try` is over 7 days old is due
(`guest:7day`).  A guest's merged update isn't a PASS: the merged image hasn't booted.
Stic and holt cells key a guest's fingerprint (`vms/G/image-fingerprint.json`) when it
speaks for the current generation, else the generation; a new generation at the same
fingerprint is soft-stale and runs no canary, and the first plan after a fingerprint
appears writes `carry:fingerprint` rows.  An image is due when its binaries moved
(`img:new`) or its panel is owed (`img:panel-owed`); a harness change alone is soft-stale.
Guest and img cells need a PASS at every reachable host's key.
