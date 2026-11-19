# Spec: `t2 batch` — unattended batches the user starts and a session reads

Status: **APPROVED 2026-11-15** with every recommendation, except decision 2,
which the user changed: entries run in parallel from the first version, and
the batch divides the work across the four hosts (§4.5). Nothing built.
Hosts: chopin, cinqcent (big), copland, cage (small), all amd64, all
`default: true` since 2026-11-14. Bigfoot and czerny are out of scope and the
batch refuses to name them. (Later: the batch takes the table's default hosts,
choubox among them since 2026-11-17 and czerny since 2026-11-19; it refuses
only Bigfoot, a reserved row and a non-amd64 host.)

## 0. Goal

The long test words take hours. Today a session starts one and then babysits
it: polling run dirs, re-reading output, retrying when a family lock is held,
re-planning after a host hiccup. That is where the tokens go, and the session
must stay alive for all of it.

What the user wants instead:

1. They start a batch themselves, from the MacBook, with one command.
2. It drives the four farm hosts to the end without prompting, keeps going
   after failures, and never collides with a session's own work.
3. It leaves output a later session can read cheaply: a short summary first,
   full logs only where something failed, and a diff against the previous
   batch so "new" is separated from "known".
4. An interruption — a closed lid, a dropped Wi-Fi link, a rebooted host —
   costs each entry in flight its progress on at most one cell, not the
   whole run.
5. No host idles while the batch has work it could run there, and no host
   ever runs past its limits (Chopin's cooler is the weakest of the four).

## Non-goals

- **Not a second arbiter.** The batch decides which entries run at once and
  on which hosts (§4.5), but every VM job still goes through `cmp-run.sh`'s
  first-come line on its host, which stays the only thing that holds room,
  and within the hosts an entry is given, its cells are still placed by
  `t2`'s planner (or `ht1-farm.sh`'s, `drive.py`'s, `bs2`'s) as today.
- **Not a new test word.** A batch is a list of existing words and queues. It
  decides nothing about what a word runs.
- **Not a new verdict.** Verdicts come from the existing judges and the
  ledger. The batch reports and groups them; it never re-judges.
- **No hardware steps.** BackShot1, BackShot2's tier 3 and anything else that
  wants a person at the ThinkPad stay outside a batch (§4.4).
- **Not a cron job.** The user starts a batch. Scheduling it is a later
  question (open 3).
- **No second ledger, no second classification store.** "Known failure" means
  "the previous batch had it too" (§7.3), not a new file to curate.

## 1. Facts this spec rests on

Gathered 2026-11-15 by reading the code; the file references are the places a
later reader should check if this spec and the code disagree.

1. **`t2` must run on the Mac.** `stick/build-round.sh` records that the Mac
   is the git authority: it checks `~/git/restic`, `rustic_core` and `rustic`
   (`BKO_GIT`) are clean, takes their HEADs and source digests, and *syncs the
   trees* to each host's SRC. A host has no fork checkout. `t2lib/keys.py`
   keys cells off those HEADs and digests. So the driving process lives on the
   Mac, or the git authority has to move with it (decision 1).
2. **Everything heavy is already detached on the hosts.** `hostrun.detached()`
   (`t2lib/hostrun.py:213-225`) starts remote work under `setsid -f` with a
   PID handshake; `drive.py` (`testvm/stictest1/drive.py:525-542`) and
   `ht1-farm.sh:275-283` do the same by hand. The driver's death does not stop
   a cell.
3. **Resume exists, per run id.** `t2 resume RUN` (`t2lib/run.py:1336-1395`)
   re-plans, re-runs preflight, rebuilds `RunState` from `cells-HOST.json` and
   calls the family's `resume()`. `report.txt`'s absence is the "unfinished"
   marker (`unfinished_runs`, `run.py:211-237`, `UNFINISHED_S = 12 h`).
   **`gate` has no `resume()`** and `run.resume` refuses it
   (`run.py:1354-1356`) — the one real hole (decision 7).
4. **The family lock fails fast.** `FamilyLock.acquire` waits 1 s and raises
   `Busy` (`run.py:140-157`); `family_locks(word)` (`run.py:176-185`) is the
   conflict matrix, with the non-obvious edges `img → {img, holt}` and
   `GuestTest2 update → everything`. `held()` tests without holding.
5. **One word per family at a time, and `bko.py` is the precedent for
   several.** `BkoTest2`/`SweepTest2` take every lock once, share one
   `RunGuards`, one copy and one stamp, and run stic, holt and guest in
   parallel threads (`t2lib/bko.py:88-95`), with children calling
   `run.lifecycle(..., shared=)` so their lifecycles take no locks.
6. **Verdicts and their vocabulary are fixed.**
   `VERDICTS = (PASS, FAIL, FLAKY-FAIL, HARNESS, LOST)`,
   `CLASSES = (pass, fail, harness, lost, known-flake)`
   (`t2lib/__init__.py:42-44`), legal pairings in
   `ledger.VERDICT_CLASSES`. A row carries `verdict`, `class`, `needs`,
   `counts`, `fails` (≤ 20 lines, each ≤ 200 chars), `rule`, `host`, `gm`,
   `wall_s`, `valid`, `committed`, `origin`. `origin` is the idempotence key.
7. **There is no machine-readable report today.** `t2lib/report.py` writes
   plain text; `render_composite` (`report.py:429`) is the nearest thing to a
   batch summary. The only TSV in the system, `estimates.tsv`, is an input.
8. **Nothing in `t2` reads a terminal.** No `input()`, `getpass` or `isatty`;
   every ssh is `BatchMode=yes`, every privileged step `sudo -n`; guest
   passphrases are files typed by the harness through QEMU
   (`keys.py:105` `GUEST_FILES`). Unattended operation needs no new plumbing —
   only the refusals listed in §4.4.
9. **A sleeping Mac looks like a dead host to `t2`.**
   `hostjob.reattach_job` (`hostjob.py:302-377`) gives up after
   `UNKNOWN_MAX = 20` unanswered polls (`hostjob.py:183`) and reports
   `still_running`; `audit-lib.sh:117-135` has the right rule written down
   ("ssh exit 255 is no answer, not ended") but `t2`'s patience is a constant,
   not a knob (B4).
10. **Per-host limits come from one table.** `testvm/hosts.json`:
    small hosts `stic_slots 2`, `holt_jobs 1`, `base_gib 4`, `bs2_slots 2`;
    big hosts `bs2_slots 5`, `holt_disk_mode true`. `cmp-run.sh` holds the
    heat gate (CPU 95 C, NVMe 75 C) and the RAM-disk ceiling. The batch reads
    the table and changes none of it.
11. **An outer reservation deadlocks nested ones.** `bs2 shrink` must not run
    under `cmp-run.sh --reserve`, because each of its tier-1 runs takes its own
    (`testvm/backshot2/README.md:247-252`). The batch wraps nothing.
12. **Stale guest generations take a cell out of the whole farm**, not just
    one host (`docs/design/four-host-split.md` §3). That belongs in the
    batch's preflight (§6.1).

## 2. Shape

```
  the user, on the Mac                    the four hosts
  ────────────────────────                ──────────────────────────────────
  t2 batch batches/carpetbeater.batch
    │
    ├── parse + profile + schedule + preflight   (refuse here, not in hour three)
    ├── beacon: a batch is running  ───▶  HOST_ROOT/batch-running.json
    │
    └── event loop (an entry ends, a lock frees, a 60 s tick):
          batchsched.decide()       ◀───  pools, shares, locks, sessions' runs,
          │                               cmp-run.sh --free (pick-host's probe)
          ├── start entry E on hosts S: a child `t2 WORD --hosts S` (or ht1-farm,
          │     drive.py, bs2, cmd) ───▶  detached cells, cmp-run.sh's line
          │                               ledger rows, run dirs, ht1 queues
          ├── grow an ht1 entry onto a freed host
          ├── sentinels: placed/started/ended, per entry
          └── on interruption: adopt live children, t2 resume RUN for the rest
    │
    ├── summary.tsv, logs of non-PASS cells, batch.log
    └── ANALYSE.md  ───▶  a later session reads this one file first
```

The batch is a **supervisor over existing runs** that decides which run at
once and where. It owns ordering, entry placement on hosts, sentinels, log
collection and the summary. It owns no cell placement inside an entry and no
judging.

## 3. The batch file

Plain text, one entry per line, `#` comments, blank lines ignored, UTF-8, LF.
Saved files live in `testvm/test2/batches/*.batch` and are committed like any
other spec data.

### 3.1 Grammar

```
line     := comment | blank | directive | manual | entry
directive:= "set" KEY VALUE
          | "include" PATH                  # relative to this file, depth ≤ 2
manual   := "manual" TEXT                   # printed, never run
entry    := ["serial"] KIND ARG... [OPT...]  # serial: runs alone (§4.5)
KIND     := t2 | ht1 | stic1 | bs2 | cmd
OPT      := KEY=VALUE                       # id, hosts, after, est, retry, rerun, why
```

`hosts=H,H` pins the entry to exactly those hosts: the batch never splits
it. `after=ID[,ID]` starts it only once those entries ended (a cycle, or an
unknown id, is refused at parse time). `est=DURATION` (`90m`, `2h`) is the
entry's wall-time estimate when nothing better exists (§4.5).

Entry kinds, and what each wraps:

| Kind | Form | Runs | Verdict from |
|---|---|---|---|
| `t2` | `t2 WORD [ARG...]` | a child `t2 WORD --hosts S` (§4.2), any word `t2` accepts | the run's exit + its ledger rows |
| `ht1` | `ht1 RUN GUEST...` | `testvm/holttest1d/ht1-farm.sh` | the queue's `done` rows + `holt:` ledger rows |
| `stic1` | `stic1 ROUND [GUEST...]` | `testvm/stictest1/drive.py --private-copy` | `results.tsv` + `stic:` ledger rows |
| `bs2` | `bs2 WORD [ARG...]` | `testvm/backshot2/bs2` | `bs2 status RUN` + `bs2:` ledger rows |
| `cmd` | `cmd NAME -- COMMAND...` | the command, on the Mac, stdin `/dev/null` | exit 0 is PASS, anything else FAIL |

`cmd` is the escape hatch, with a deliberately stupid judge and a loud name in
the summary, so nobody mistakes it for a judged cell. It defaults to
`rerun=no`: after an interruption the entry is `INCOMPLETE`, not re-run,
because re-running an arbitrary command may not be safe. `rerun=yes` opts in.
A `cmd` takes no host capacity unless it says `hosts=H,H`, which claims every
pool on those hosts for its life (§4.5): a command that works the farm must
say where.

Settings (`set`), with defaults:

| Key | Default | Meaning |
|---|---|---|
| `mode` | `parallel` | `serial`: every entry runs alone, in file order (the debugging form) |
| `round` | none | the round every `t2`/`stic1` entry takes unless it names one |
| `hosts` | the table's default hosts | the hosts the batch divides its entries over (§4.5); `bigfoot` and a disabled row are refused |
| `retry` | `0` | per entry, how many times a *harness-shaped* failure re-queues the entry (§6.5) |
| `lock-wait` | `4h` | how long an entry waits for its family locks before it is `BLOCKED` |
| `patience` | `2h` | how long "the Mac went away" is tolerated before a cell counts as lost (B4) |
| `log-fetch` | `non-pass` | `non-pass`, `all` or `none` (§5.3) |
| `prev` | newest earlier batch of the same file | the baseline for the diff |

### 3.2 Entry ids

Resume needs a name that does not move when the file is edited above the
entry. The id is `NNN-KIND-SLUG`: the entry's 1-based position, its kind, and
its arguments slugified to `[a-z0-9+._-]` and cut at 40 characters —
`003-t2-sweeptest2-rel6`. `id=NAME` overrides it. Two entries that slugify
alike are refused at parse time; so is an `id` that is not unique. Editing a
batch file between a run and its resume is refused: the batch's frozen
`batch.json` is compared with the file by digest, and a mismatch says which
line changed and offers `--reparse` (which drops state for every entry whose
id or arguments moved, and keeps the rest).

### 3.3 Example — `batches/carpetbeater.batch`

```
# CarpetBeater's unattended part, twice a month.
# The suite is the user's (memory: carpetbeater).  Step 1 (refresh) and the
# ThinkPad block (step 5) are manual: see decisions 8 and 9.
set round rel6
# No `set hosts`: the table's default hosts, every one of them.
set retry 1

manual  step 1: vm-update round, then rebuild the rescue images
t2      GateTest2All rel6       why="step 2: code tests, the fork suites"
t2      SweepTest2 rel6         why="step 3: BkoTest2All, part 1"
t2      ImgTest2All release     why="step 3: BkoTest2All, part 2"
bs2     BackShot2Alpine 200     why="step 4"
manual  step 5: the ThinkPad block, BackShot1 then BackShot2Alpine's cases
```

### 3.4 How the named suites map

| Suite | File | Entries |
|---|---|---|
| BkoTest2All | `batches/bkotest2all.batch` | `t2 SweepTest2 ROUND`, `t2 ImgTest2All` |
| CarpetBeater | `batches/carpetbeater.batch` | §3.3 |
| BkoTest1All | `batches/bkotest1all.batch` | `stic1 ROUND`, `ht1 HoltTest1All` |
| HoltTest1All | `batches/holttest1all.batch` | one `ht1` entry per family letter, biggest guests first |
| SticTest2All | `batches/stictest2all.batch` | `t2 SticTest2All ROUND` |
| GuestTest2 | `batches/guesttest2.batch` | `t2 GuestTest2All` (never `update`: decision 8) |
| BackShot2Alpine | `batches/backshot2alpine.batch` | `bs2 BackShot2Alpine 200`, tier 3 as `manual` |
| the due round | `batches/due.batch` | `t2 BkoTest2 ROUND` alone |

Each file opens with a comment naming what defines the suite, so the
definition stays in one place and the batch file is only its spelling.

## 4. The CLI

```
t2 batch FILE [plan] [--hosts H,H] [--prev BATCH] [--id NAME]
              [--dirty-ok] [--allow-update] [--reparse]
t2 batch resume BATCH
t2 batch status
t2 batch report BATCH [--prev BATCH] [--json]
t2 batch summary BATCH            # build summary.tsv again from the ledger
t2 batch log BATCH CELL [--tail N] [--head N]
t2 batch ls [--last N]
```

- `t2 batch FILE` with an unfinished batch of that file continues it (§6.2);
  `--id NAME` starts a separate one deliberately.
- `plan` prints the schedule — each entry's hosts, pools, predicted start and
  end, and why it waits — the manual checklist, the refusals, the predicted
  finish and what `mode serial` would take, and runs nothing: the same
  contract as `t2 WORD plan`. It probes facts as `t2 WORD plan` does, never
  `cmp-run.sh --free` (the plan counts on no live join, §4.5).
  `t2 batch status` prints the same table for a running batch, re-predicted
  from now.

```
$ t2 batch batches/carpetbeater.batch plan
batch carpetbeater  hosts chopin cinqcent copland cage  mode parallel  round rel6
manual  step 1: vm-update round, then rebuild the rescue images
entry                        hosts                  pools           start  end    wall  waits for
003-t2-sweeptest2-rel6       chopin cinqcent copl…  stic holt gate  +0:00  +6:10  6:10  -
005-bs2-backshot2alpine-200  chopin cinqcent copl…  bs2             +6:10  +9:05  2:55  shares: 003 fills every host
004-t2-imgtest2all-release   chopin cinqcent copl…  holt            +6:10  +8:20  2:10  locks img,holt: 003
002-t2-gatetest2all-rel6     chopin cinqcent copl…  gate            +6:10  +7:15  1:05  lock gate: 003
manual  step 5: the ThinkPad block, BackShot1 then BackShot2Alpine's cases
busy    chopin 9:05  cinqcent 9:05  copland 9:05  cage 9:05
finish  +9:05 (13:20Z if started now); mode serial: +12:20
```

  Rows are in start order; `wall` is the estimate on those hosts, with its
  source (`history`, `tsv`, `est=`, `default`) in `--json`.
- Exit codes follow `t2`'s: **0** every entry PASS or nothing due; **1** a
  FAIL, FLAKY-FAIL, to-class, INCOMPLETE or BLOCKED entry; **2** refused
  (parse error, preflight, a manual-only step left undeclared, a dirty tree);
  **3** internal; **130** interrupted.

### 4.1 Why a `t2` subcommand

Everything the batch needs is in `t2lib`: the locks, the planner, the host
table, the ledger, the estimates, the report. A separate tool would copy them
or import them anyway. New code, with these boundaries:

| Module | Pure? | Owns |
|---|---|---|
| `t2lib/batchfile.py` | yes, no I/O (includes read through a passed `read`) | `parse(text, path, read) -> Batch`; `Entry` (id, kind, args, opts, serial, after, pos, line); `refusals(batch, table) -> [str]`; `locks_of(entry) -> set` (`words` + `run.family_locks`); `pools_of(entry) -> set`; `lock_matrix(entries) -> {(id, id): reason}` |
| `t2lib/batchsched.py` | yes, no I/O, imports neither `hostrun` nor `hostjob` | `HostCap`, `capacity(table, facts) -> {host: HostCap}`; `Profile`; `candidate_sets(p, cap)`; `admissible(p, hosts, cap, busy, live=None) -> (ok, why)`; `simulate(profiles, cap, running, now) -> Schedule`; `decide(profiles, cap, running, sessions, live, now) -> [Start \| Grow \| Wait]` |
| `t2lib/batch.py` | no: the supervisor | `profiles(batch, ctx) -> [Profile]` (plans and estimates, §4.5); `session_claims(mac_root)`; `live_probe(hosts)` (pick-host's `probe_hosts`/`rank`, loaded by path as pick-host loads `hosts.py`); `drive(bdir)`, the event loop; `spawn(entry, hosts)`; `discover_run(entry, pid)`; `next_action(state, entry)` (pure, §6.2); the beacon, the driver lock, signals |
| `t2lib/batchreport.py` | yes | `summary_rows(...)`, `diff(rows, prev)`, `render(...)` (§5.2, §7) |

`t2`'s `COMMANDS` dict gains one entry. The scheduler is a pure function of
profiles, capacity and state, so every placement rule is unit-tested with a
fake host table and fake estimates (§11.1).

### 4.2 Running a `t2` entry

As a child process, one per entry in flight: `t2 WORD ARGS --hosts S`, with
stdin `/dev/null`, its own `entry.log`, `start_new_session=True` (a
terminal's Ctrl-C reaches the batch, not its children) and the batch's
patience in `T2_UNKNOWN_MAX` (B4). In process was right for one entry at a
time; with several, `bko.py`'s threads would share signal handlers, `out`,
and module state across unrelated words, and a child is what resume can
adopt (§6.2).

1. `decide()` starts an entry only when no name in its locks is `held()` —
   the scheduler keeps the batch's own entries apart by the lock matrix, and a
   session's lock just holds the entry back (it counts toward `lock-wait`;
   waiting behind the batch's own entry does not).
2. The child's `run.lifecycle` takes its own locks, as any `t2` run does. A
   session that took one in between makes the child exit refused (`Busy`)
   before any run dir exists: the batch drops the entry's `placed`, logs whom
   it lost to, and schedules it again — **the person always wins**.
3. The run id is found without touching `run.py`: while the child holds its
   family lock (`.lock-NAME.json` names its PID), the one new run dir of that
   family's prefix is its run (`discover_run`). `entries/ID/run` is written
   then, and `started` with it, before any host work begins.
4. `RunGuards` stay per entry, in the child: one entry's stops never stop
   another. A batch-wide guard is open 2.

`ht1`, `stic1`, `bs2` and `cmd` are children the same way, started with the
hosts `decide()` gave them (`--hosts S` for all three tools).

### 4.3 Interruption by signal

TERM, HUP or Ctrl-C: stop starting and growing entries, send TERM **once** to
each `t2` child (its own Ctrl-C handling: trip its guards, start no cell,
leave detached host work alone), wait up to 60 s for them, write the beacon
off, log the reason, exit 130. `ht1`, `stic1` and `bs2` children are left
alone: their work is host-autonomous. A child still running when the batch
exits is adopted by `t2 batch resume` (§6.2). A second signal does the same —
**never a kill, never `pkill`**. The host jobs end by themselves or are swept
by their own machinery (`ht1-lib.sh --sweep`, `t2`'s leftover rules, which
keep everything of a run whose PIDs cannot be checked).

### 4.4 What a batch refuses to run

Declared manual steps are printed and skipped. These are *refused*, with the
entry named and the reason, because running them unattended would hang or
surprise:

| Refused | Why | Way round |
|---|---|---|
| `backshot1` anything | real hardware, a person at the ThinkPad | `manual` line |
| `bs2` tier 3 | ThinkPad cases (`docs/design/bs2-all.md`) | `manual` line |
| `t2 GuestTest2 update` | holds `vm-update`'s fleet lock through a 30 s heartbeat whose holder exits after 120 s of silence (`guest.py:647-654`): a Mac sleep loses the fleet lock mid-update | `--allow-update`, lid open |
| `t2 guest-lines --accept` | classing log lines is the person's judgement | by hand, after |
| any entry naming `bigfoot` or a `"enabled": false` row | Bigfoot is the user's machine, only Chopin talks to it, and only 00:30-07:30 | — |
| `--universe` | test-only, refused in the real root | — |
| a `cmd` whose command reads a tty, or contains `sudo` without `-n` | would prompt | fix the command |
| two `ht1` entries with the same RUN | one queue would serve both | rename one |
| a `t2` entry that may build bko-holt (HoltTest2 unless all its cells are `+img`, BkoTest2, SweepTest2) with no big build host among its hosts | HoltTest2 stops at once (§6.1 item 8) | add chopin or cinqcent to its hosts, or (HoltTest2) `--holt-bin DIR` |

`t2 GuestTest2 update`, when allowed, is always `serial`: it changes guests
under any `ht1`, `stic1` or `bs2` entry, which no lock of `t2`'s covers.

### 4.5 Running entries at once: the scheduler

**Pools.** Each host's capacity is a set of pools, sized from
`testvm/hosts.json` through `hosts.limits()`:

| Pool | Slots on a host | Taken by |
|---|---|---|
| `stic` | `stic_slots` (big 3, small 2) | `t2` SticTest2 words, BkoTest2/SweepTest2, `stic1` |
| `holt` | `holt_jobs` (big 3, small 1) | `t2` HoltTest2, GuestTest2, ImgTest2 words, BkoTest2/SweepTest2, `ht1` |
| `bs2` | `bs2_slots` (big 5, small 2) | `bs2` |
| `gate` | 1 | `t2` GateTest2 words, BkoTest2/SweepTest2 |
| all four | — | `cmd hosts=`, `serial` entries |

An entry placed on a host takes **all** of each pool it uses there, because
its runner is started with the table's figure (`drive.py --slots`,
`ht1-queue.sh` MAXJOBS, `bs2-host queue --slots`). So a pool on a host has
one owner at a time, the per-host limits cannot be exceeded by adding entries
up, and the ports each runner owns (`stic_ports`, the holt slot range, bs2's
2943-2948) never collide.

**Shares.** Pools are not independent: they share the host's RAM disk and
memory. An entry's share of host H is `min(ceiling(H), Σ)` over its pools on
H of the `n` largest single-job reserves `(DISK, MEM)` among its jobs that can
run on H, `n` the pool's slots; `ceiling(H)` is `facts.ceiling()`, the host's
`cmp-run.sh --ceiling`. Reserves come from where the runners take them:
`drive.py`'s specs (stic), `ram-sizes.tsv`'s D (holt, ht1), the guest spec's
`ram_gib` (GuestTest2), bs2's `(2, 3)`, the gate spec's reserves. An entry
joins a host only when the shares of everything there, its own included, fit
the ceiling on both axes — or when it would be alone there (its own
parallelism is `t2`'s business, as today). A host whose ceiling is unknown
takes one entry at a time.

**Live join.** At run time only, a host whose shares do not fit may still
take an entry whose pools are free there, when pick-host's read-only probe
shows its `cmp-run.sh` line empty (`waiting 0`), its `after` room at least the
entry's largest single-job reserve there, and pick-host's load per core —
with Chopin's `LOAD_PENALTY` — under 1.0. This uses the slack a long word
leaves once one of its families ends. `cmp-run.sh --reserve` still admits
every job; the plan never counts on a live join, so it can only make a batch
finish earlier than predicted.

**Order.** Ready entries are taken longest first (LPT: the predicted wall on
the batch's hosts, file position breaking ties), so the long entries start
early and the short ones fill the end and the farm finishes together. An
entry is ready when its `after` entries ended, no `serial` barrier is in the
way (a `serial` entry waits for everything above it, runs alone, and holds
back everything below it), and its locks conflict with no running entry's
(the lock matrix, computed at parse time from `run.family_locks`; its
non-obvious edges `img → {img, holt}` and `update → everything` included).
Two `t2` entries whose plans would build the same round (`plan.set.pending`)
also conflict: `build-round.sh`'s tree flock would serialize them anyway, and
the second would sit on its pools meanwhile.

**Placement.** For each ready entry in that order, `decide()` weighs
candidate host sets:

- `hosts=`: that set only, never split.
- `ht1`, `stic1`, `bs2`: any subset of the batch's hosts.
- `t2` SticTest2, HoltTest2, GuestTest2 and ImgTest2 words: the batch's
  hosts, the big ones, the small ones, and each "all but one" — at most seven,
  each planned once with `--hosts S` at profile time, the plans kept in
  `batch.json`. Never GateTest2 (its `loop:…@HOST` cells are per host) and
  never BkoTest2/SweepTest2 (one stamp, one copy, every family): those take
  the batch's hosts or wait.

A set is **feasible** when every cell or guest the batch's full set could run
still has a host in it (`classes.json` pins, ceilings, caps, images, arch; for
`t2`, the subset's plan has no not-runnable due cell the full plan places).
It is **admissible** when, on each of its hosts, the entry's pools have no
owner (a batch entry or a session's run, §8) and the shares fit (or a live
join holds). Of the admissible sets, the one with the earliest predicted end
wins; ties go to the set without Chopin, then the bigger set, then the
table's order. The entry **starts** there when that end is no later than
waiting for its best set would give, *or* no later than the batch's current
predicted finish — so a free host gets work whenever that does not push the
finish later, and is left free only when every way of using it would. Else
the entry waits, with the reason `plan` and `status` print.

**A run's hosts are fixed when it starts** (`t2 resume`, its run id and its
ledger rows key off them). The one exception is `ht1`, whose queue is
farm-wide by design: when a host's `holt` pool frees after the ready entries
are placed and an `ht1` entry's queue still has lines that host could take,
the batch starts one more runner there (`ht1-farm.sh RUN --join --hosts H`,
the per-host command `ht1-farm.sh` already issues), recorded in `placed`.

**Prediction.** `simulate()` runs the same rules forward over predicted ends —
a running entry ends at `max(start + wall, now + 60 s)`, an `ht1` that grew is
re-packed over its remaining guests with `estimates.pack` — and returns each
entry's hosts, start and end and the batch's finish. It is what `plan` prints,
what `status` re-prints, and the "predicted finish" `decide()` compares with.
Pure and deterministic: the same inputs give the same schedule.

**Estimates.** `t2` entries take their plan's `totals.wall_s` on each
candidate set (`estimates.pack`/`stic_wall`, as `t2 WORD plan`). `ht1` and
`stic1` entries take the same per-cell estimates (`holt:G`, `stic:G` history,
then `estimates.tsv`) packed over the set by `estimates.pack` and
`estimates.stic_wall`; `bs2` its own `split()`/`wall()` (loaded by path);
`cmd` its `est=`, else the median wall of its id in the last three batches of
the file, else 5 min. Every source is named in `--json`.

**Chopin.** Its limits are never exceeded because pools are owned whole and
shares fit its ceiling; on top of that, ties go against it and a live join
there needs its penalized load under 1.0. `cmp-run.sh`'s heat gate (CPU 95 C,
NVMe 75 C) still holds every job's start.

## 5. Output

### 5.1 Layout

One directory per batch, under a new root beside the runs so that
`RUN_RE` and `unfinished_runs()` never see it:

```
MAC_ROOT/batches/
  .running                       the beacon, while a batch runs
  bt-20261115T0312-9ab1/
    batch.json                   the parsed batch, frozen: entries, ids, settings, file digest, profiles
    batch.log                    append-only "UTC text" timeline, each start and grow with its reason
    state.json                   per entry: status, hosts, run, started, ended, exit, attempts, predicted end
    schedule.txt                 the last `simulate()` table, rewritten at every decision
    summary.tsv                  one row per cell (§5.2)
    report.txt                   what `t2 batch report` printed at the end
    ANALYSE.md                   the procedure plus this batch's numbers (§7)
    prev                         the baseline batch id
    entries/003-t2-sweeptest2-rel6/
      placed                     the claim: hosts, pools, shares, predicted end; `grow` lines appended
      child                      the child's PID and start time (ps lstart), for adoption
      started  ended  exit       sentinels, epoch seconds
      run                        the t2 run id, or the ht1/bs2 RUN
      entry.log                  the supervisor's log for this entry
      report.txt                 the run's own report, copied
    logs/003-t2-sweeptest2-rel6/holt-vanilla3+bigger/
                                 fetched only for non-PASS cells (§5.3)
```

Batch ids are `bt-YYYYMMDDTHHMM-XXXX`, the same stamp shape as a run id with
its own prefix, so the two never collide.

### 5.2 `summary.tsv`

Tab-separated, one header line starting `#`, one row per cell, plus one row
per entry that produced no cells (cell `-`). Every field is one line with no
tabs; `reason` is the decisive row's first `fails` line, cut at 160
characters.

```
#entry	cell	verdict	class	since	host	attempt	gm	wall_s	run	row	log
002-t2-gatetest2all-rel6	gate:holt	PASS	pass	same	chopin	1	3.1	188	t2g-20261115T0315-11af	r/8f21c0	-
002-t2-gatetest2all-rel6	gate:fork:root:restic	FAIL	fail	new	cinqcent	2	1.0	61	t2g-20261115T0315-11af	r/8f2a44	logs/002/gate-fork-root-restic
003-t2-sweeptest2-rel6	stic:debian13	PASS	pass	same	copland	1	6.0	402	t2s-20261115T0401-3cd2	r/8f3b90	-
003-t2-sweeptest2-rel6	holt:vanilla3+bigger	FAIL	fail	known	chopin	2	9.0	1840	t2h-20261115T0401-3cd2	r/8f4012	logs/003/holt-vanilla3+bigger
003-t2-sweeptest2-rel6	holt:kali	FAIL	fail	new	cinqcent	2	7.4	1502	t2h-20261115T0401-3cd2	r/8f4119	logs/003/holt-kali
003-t2-sweeptest2-rel6	guest:neon24	LOST	lost	new	cage	1	-	-	t2v-20261115T0401-3cd2	r/8f41f0	logs/003/guest-neon24
005-bs2-backshot2alpine-200	bs2:fail-006-lvm-gap	FAIL	fail	new	copland	1	0.4	96	bs2-20261115T0730-7a1c	r/8f52aa	logs/005/bs2-fail-006-lvm-gap
006-cmd-rebuild-img	-	PASS	cmd	same	mac	1	-	311	-	-	entries/006-cmd-rebuild-img/entry.log
```

`class` is the ledger's class, which is the axis that matters most: `fail` is
a finding, `harness` and `lost` are the farm's own trouble, `known-flake` is
noise. `since` is the diff against `prev`: `new`, `known` (the baseline had
this cell failing with the same reason head), `fixed` (the baseline failed and
this one passed — emitted as a row so a fix is visible) or `same`.

`row` is the ledger row id, so any claim in an analysis can be traced with
`t2 ledger show --cell C`.

### 5.3 Logs

`log-fetch non-pass` (the default) fetches, per non-PASS cell, that cell's own
records from the host that ran it: at most **8 MiB and 50 files** per cell,
head and tail kept when a file is bigger, and a `FETCH.txt` saying what was
left behind and the host path to find it. A PASS keeps no fetched log; its
host path is still in `batch.log`, and the hosts keep their own records
(`ht1`'s `kept.txt` rules: a pass keeps records only, a FAIL keeps
everything).

Rationale: HoltTest1All keeps about 0.3 GiB on the hosts for a clean pass and
far more with failures. Copying all of it to the Mac every batch buys nothing
— an analysis reads failures. `--log-fetch all` exists for the one batch where
someone wants everything.

## 6. Resume, interruption, and what a wake costs

### 6.1 Preflight, once, before anything starts

Refuse with every unmet item listed (exit 2), never a part-run batch:

1. The checkout is `main` and clean, unless `--dirty-ok`. A PASS from an
   uncommitted copy does not count until the same diff is committed
   (`README.md` "A row counts only if it is valid and committed"), so a dirty
   tree means hours whose results are thrown away. `--dirty-ok` prints that
   warning and goes on.
2. Every host in `hosts` answers, and its `testvm/.backotter-commit` equals
   HEAD. The batch **checks and refuses; it never syncs** — a sync mid-run
   hands a running check newer helpers
   (`testvm/stictest1/README.md:56-60`). The refusal prints the exact
   `testvm/sync-hosts.sh chopin cinqcent copland cage` to run, with the hosts
   named, never the bare default (which would reach Bigfoot).
3. No family lock held, no unfinished run of any family the batch touches
   (`unfinished_runs`), no other batch running (the beacon). On a resume the
   batch's own runs and its live children's locks are excluded: they are what
   it resumes.
4. `t2`'s own preflight on every host: `/mnt/bko-ram` present, `m` in `kvm`,
   `/bkoscratch` over 80 GB free, Tctl under 90 C.
5. `vm-store status --farm` shows no stale generation on a cache host — one
   stale link fails a guest's identity on *every* host
   (`four-host-split.md` §3).
6. The Mac has room for the fetched logs: 5 GB free under `MAC_ROOT`.
7. Every entry parses, every id is unique, no refused entry (§4.4), and every
   `t2` word is one `words.py` accepts — checked by planning each word.
8. Every entry that may build bko-holt linux (`t2 HoltTest2` without
   `--holt-bin`, unless its cells are all `+img` ones — `+img` alone or only
   `NAME+img` words, which run the image's own bko-holt — and `t2
   BkoTest2`/`SweepTest2` for their holt child; not `ht1`, which takes the
   promoted holt-bin) has a big build host (role
   `build`, class `big` in `testvm/hosts.json`) among its hosts (`hosts=`,
   else the batch's). The predicate is HoltTest2's own stop rule's
   (`holt.big_build_hosts`), so the two can't drift; the refusal names the
   entry, its hosts and the remedies: add a big build host, or give the
   HoltTest2 `--holt-bin DIR`. The scheduler, likewise, never places such an
   entry on a candidate set without one (`bt-20261115T1132-e4ca`: HoltTest2 on
   copland alone stopped 9 s in).

### 6.2 Resume

The batch is **re-entrant at entry granularity**, with several entries in
flight. `t2 batch FILE` again, or `t2 batch resume BATCH`, continues. First,
every entry with `placed` and no `ended` gets its action below, all of them
at once; their claims (hosts, pools, shares, `ht1` grows) are re-asserted
before `decide()` starts anything new, so a resumed run keeps its hosts and
new entries fill around it. Then the scheduler carries on from the profiles
in `batch.json` (unstarted `t2` entries are re-planned, since the ledger
moved):

| Entry state | What resume does |
|---|---|
| `ended` present | nothing; never re-run |
| `child` alive (PID and start time match `ps`) | adopt it: wait for it as if the batch had never gone; nothing re-run |
| `placed`, no `started`, child gone | the start never reached a run (a lost lock race, a crash while planning): drop `placed`, schedule it again |
| `started`, no `ended`, child gone, `run` recorded, family has `resume()` | `t2 resume RUN` as a child — the same cells, the same hosts, preflight again |
| `started`, no `ended`, family is `gate` | `INCOMPLETE`: write the stub `report.txt` that sets the run aside (`bko.ended_early`'s precedent), then re-queue once if `retry` allows (decision 7) |
| `started`, `ht1` | start the farm runners again with the same RUN on `placed`'s hosts and grows; the `todo` survives and already-taken lines are not redone |
| `started`, `stic1` | the same mark again; `drive.py` adopts checks that ran (its `run.out.start` sentinel) and writes the rows still missing |
| `started`, `bs2` | the same word again; `bs2` has its own per-case state |
| `started`, `cmd`, `rerun=no` | `INCOMPLETE`, and on to the next |
| never started | run it |

The `started` and later rows mean "and its child is gone". `placed`,
`started` and `ended` are created with `O_CREAT|O_EXCL` — the same
idempotency token `drive.py` gets from `set -C` — so two drivers cannot both
claim an entry, and the sentinel is the state, not a flag the user passes.
`next_action(state, entry)` is the table as a pure function.

A second driver is refused anyway: the batch holds `flock` on
`MAC_ROOT/batches/.lock` for its whole life, non-blocking, naming the holder's
batch, PID and start.

### 6.3 A host reboot

The host's in-flight cells leave no ledger row. `t2 resume` reattaches
through `hostjob.reattach_job`, finds the job gone and backfills a `LOST` row
(`rows_holt.backfill_row` already does this for holt). The cell shows `LOST`
with class `lost` in the summary, which the analysis reads as the farm's
trouble, not a finding. `retry` (§6.5) can re-queue the entry.

### 6.4 A sleeping Mac — the decisive robustness question

`caffeinate` does **not** keep a MacBook awake through a closed lid; only
`pmset disablesleep` does, and that is a system setting the user changes, not
the batch. So a closed lid must be survivable, not prevented.

Sleep does not kill the driver: the process is suspended and its ssh sockets
die. On wake, three things must hold for the batch to carry on by itself:

1. **The driver tolerates "no answer".** Every ssh in the supervisor treats
   exit 255 as no answer, never as ended (`audit-lib.sh:117-135`'s rule),
   retries with backoff, and only calls a cell lost after `patience` (default
   2 h).
2. **`t2`'s own patience is a knob.** `hostjob`'s `UNKNOWN_MAX = 20`
   (`hostjob.py:183`) makes a long silence look like a dead job. B4 turns the
   constant into `T2_UNKNOWN_MAX` / a `HostIO` field, which `t2 batch` sets
   from `patience`; a session's default does not change. This is the one
   change the batch needs inside `t2`, and it is small and testable.
3. **ssh keeps its own counsel.** `ServerAliveInterval=15` is already in
   `hostrun.py:36`; the batch adds `ServerAliveCountMax=4` and
   `ConnectionAttempts=3` so a brief Wi-Fi drop costs a retry, not a cell.

With those three, a sleep is a long pause: host work continues under
`setsid` and `cmp-run.sh`, and the driver and its children pick the thread
back up. What a sleep still costs is **throughput**: a batch is driven, so an
entry that ends while the Mac sleeps does not start the next one, and no
`ht1` grows. With several entries in flight that costs less than it did
serially — every entry in flight keeps its hosts busy — and a host that frees
mid-sleep idles until the wake. That is the real price of a Mac-side driver
(decision 1); the host-autonomous entries (`ht1`, `bs2`) keep hosts busy for
hours without the Mac, and LPT already starts them early.

A **keeper** covers the case where the driver itself dies (a crash, a
terminal closed, an OS update): `testvm/test2/batch-keeper.sh BATCH`, a
`while :; do t2 batch resume BATCH; sleep 60; done` with a log, documented and
offered, run by the user in a second terminal or as a launchd agent. Not
installed by the batch.

### 6.5 Retries, and what is never retried

`retry N` re-queues an entry **behind every unstarted entry** (LPT then
places it like any other), at most N times, and only when the failure is
shaped like the farm's trouble rather than a finding:

- the entry exited 3 (internal error), or
- a host stopped answering for longer than `patience`, or
- at least half the entry's non-PASS cells are class `harness` or `lost`.

A `fail`-class verdict is **never** retried. `t2` already reruns a FAIL once
inside a run (attempt 2, with `FLAKY-FAIL` when the rerun passes); a second
layer of retries on top would hide real failures and double the wear. A
`BLOCKED` entry (`lock-wait` ran out on a *session's* lock; waiting behind
the batch's own entries never counts) is re-queued once at the end of the
batch regardless of `retry`, because by then the session that held the lock is
probably done.

## 7. Analysis: the procedure a later session follows

The point of the output layout is that the first read answers most of the
question. The procedure lives in **`testvm/test2/BATCH.md`, section
"Analysing a batch"**, and a copy of it, with this batch's own numbers filled
in, is written to the batch dir as `ANALYSE.md` when the batch ends. A session
reads that one file first.

### 7.1 The steps

1. **`t2 batch report BATCH`** — one screen, capped at 120 lines:
   - the batch, its file, its hosts, start and end, wall time;
   - per entry: status, exit, cells by verdict, wall time;
   - counts by verdict and by class;
   - the stops that fired, and any BLOCKED or INCOMPLETE entry;
   - **failing cells grouped** by `(class, reason head)` — the first 60
     characters of the decisive row's first `fails` line — each group with its
     count, its cells, its hosts and its `since`;
   - the diff against `prev`: new, fixed, still failing, with counts.
   For most batches this is the whole analysis.
2. **Read logs per group, not per cell.** For each group whose class is
   `fail` and whose `since` is `new`, read one representative:
   `t2 batch log BATCH CELL --tail 200`. Stop at **10 groups**; if there are
   more, say so and report the biggest ten.
3. **Triage `harness` and `lost` by reason alone.** They are the farm's
   trouble. Read a log only when three or more cells share one reason head,
   or when they outnumber the `fail` cells.
4. **Known failures are the baseline's.** `since=known` means `prev` had that
   cell failing with the same reason head. Group them, name them, do not open
   them unless the user asks.
5. **Report**: one line per group — class, reason, how many cells, which
   hosts, new or known, and the one next step. Name `row` ids for anything
   claimed. Then the entries that did not finish, and the manual steps the
   batch printed but did not run.

### 7.2 The token budget, stated in `BATCH.md`

One report (≤ 120 lines), at most 10 cell logs at ≤ 400 lines each, no
`summary.tsv` read in full unless the report is unclear (it is in the dir, and
`awk` on it beats reading it). That is the whole first pass, and it is the
same cost whether the batch ran 12 cells or 400.

### 7.3 "Known" without a new store

No new classification file. `since` comes from `prev`'s `summary.tsv`, which
is the previous batch of the same file. The existing stores keep their jobs:
`guest/limits.json` and `stic/limits.json` class LIMITs,
`classes.json policy` holds the flakes, `t2 guest-lines --accept` classes new
log lines. A failure that should stop being reported goes into one of those,
by hand, as it does today.

## 8. Coordination with sessions

The batch never blocks a session and never kills anything. Running several
entries at once, it holds more of the farm at a time than a serial batch
would, so it cooperates in five ways:

1. **The same locks.** A `t2` entry waits for the family locks a session takes
   (§4.2) and loses every race for one; a session's `t2` run gets the
   ordinary "busy" refusal naming the batch child's run id, PID and start —
   which it already understands. A batch child takes its locks only when it
   starts and holds them only while its word runs, so a session can get in
   between any two of the batch's entries of a family.
2. **Sessions' runs are claims.** Every run holding a lock that is not the
   batch's (`held_runs`) owns, on the hosts it uses (`run_hosts`), the pools
   its family uses (§4.5). `decide()` never places a batch entry in a pool a
   session's run owns, so a session's HoltTest2 on Cinqcent keeps the
   batch's `holt` entries off Cinqcent until it ends. Work a session starts
   outside `t2` shows up only in `cmp-run.sh`'s line, which bars a live join
   there (`waiting` > 0) and otherwise just makes the batch's jobs queue.
3. **The beacon.** `MAC_ROOT/batches/.running` holds
   `{batch, file, pid, started, hosts, running: [{entry, hosts, pools, eta}],
   entries_left, eta}`, refreshed every 60 s and at every start, and removed
   at the end (and ignored when its PID is gone). The same file is written to
   each host as `HOST_ROOT/batch-running.json`, so a host-side script or a
   session that only has ssh can see it.
4. **A line of output, where a session already looks.** `t2` (any word),
   `t2 batch status` and `pick-host` print one line when the beacon is live,
   naming what the batch holds: `batch bt-20261115T0312-9ab1 (3 of 6 in
   flight, ETA 04:10Z) holds stic,holt on chopin,cinqcent and bs2 on copland;
   cage is free`. Advisory, never a refusal — the hosts are shared,
   `cmp-run.sh`'s line is the real arbiter, and a person's work should be
   able to jump in.
5. **One line in the docs.** `testvm/HOSTS.md` and
   `testvm/test2/README.md` gain "check `t2 batch status` before starting
   heavy work", and an auto-memory rule says the same (decision 10).

## 9. Decisions

Each with my recommendation. **The user approved every recommendation on
2026-11-15 (decisions 1 and 3-10 as written), and changed decision 2.**

1. **Where the driver runs.** *Recommend: the Mac*, under
   `caffeinate -dimsu` when the lid stays open, sleep-tolerant and resumable
   when it does not (§6.4), with `batch-keeper.sh` offered for crashes.
   Reason: `t2` needs the Mac's git authority (fact 1). Moving the driver to
   Copland means putting the three fork clones there, a second `MAC_ROOT`, and
   a second place where derived ledger rows are written — a split source of
   truth for the sake of one machine's sleep setting. The cost of my
   recommendation is honest and bounded: a closed lid pauses *driving*, so the
   farm can idle overnight between entries. If that is unacceptable, the next
   cheapest fix is not a host driver but `sudo pmset disablesleep 1` for the
   batch window, which is the user's to decide and to undo.
2. **Parallel entries — user: parallel now.** I recommended one entry at a
   time, with parallel blocks later (B8). The user's call, 2026-11-15:
   "Parallels now, please. Divide the work efficiently between the hosts."
   So entries run at once from the first version, under the scheduler of
   §4.5: the lock matrix (computed at parse time, enforced at run time,
   sessions still win), per-host pools and RAM shares from `hosts.json` and
   the hosts' ceilings with `cmp-run.sh --reserve` the real arbiter, LPT
   order from the estimates so the farm finishes together, placement of every
   kind on hosts with free capacity, `--hosts` subsets for `t2` words only
   where that is how two can overlap, and no host idle while work it could
   run would not push the finish later. `serial` stays as an opt-in, per
   entry or as `set mode serial`.
3. **Logs: fetch only non-PASS cells** (§5.3), capped at 8 MiB and 50 files
   per cell. *Recommend yes.* The alternative (fetch everything) costs
   gigabytes a batch and buys nothing an analysis reads.
4. **`since` in `summary.tsv`.** *Recommend yes*: the diff against the
   previous batch goes in the summary rather than a separate file, because
   "new vs known" is the first thing anyone asks and putting it in the first
   read is the whole token saving.
5. **No batch-wide stop rule.** *Recommend: per-entry guards only* (one
   entry's stops do not stop the batch), plus a single safety valve: the batch
   stops starting entries if **three entries in a row** (by end time) end
   with no PASS at all, which is what a broken farm looks like; entries in
   flight run on. Open 2 is whether that number is right.
6. **Retries only for harness-shaped failures** (§6.5), never for a `fail`
   verdict. *Recommend yes.*
7. **Gate's missing `resume()`.** *Recommend: handle it in the batch for now*
   — a stub `report.txt`, `INCOMPLETE`, re-queued once — and keep "give
   `gate` a `resume()`" as a later piece outside this spec. Writing it now
   would be the largest single part of the work and gate entries are short
   (minutes), so a re-run is cheap.
8. **`GuestTest2 update` refused by default** (fact in §4.4: the fleet lock's
   120-second heartbeat does not survive a sleep). *Recommend yes*, with
   `--allow-update` for a lid-open batch.
9. **CarpetBeater's step 1 (refresh) stays manual** for now, because it is a
   `GuestTest2 update` round plus an image rebuild, and both want someone
   nearby the first few times. *Recommend yes*, and revisit once a few
   batches have run clean.
10. **An auto-memory rule for "check `t2 batch status` first".** *Recommend
    yes* — the four-host-split post-mortem showed that what sessions read is
    what they do — but memory is the user's, so it is their call, not mine.

## 10. Open

1. The scheduler's quality is the estimates'. `ht1` and `stic1` borrow the
   `holt:`/`stic:` cell estimates, which assume Test2's harness, and `bs2`'s
   `wall()` is unmeasured on the big hosts' 5 slots. The first few batches'
   `state.json` walls (predicted against actual, per entry) say how far off
   they are; a per-kind correction factor is the cheap fix if they are.
2. The "three entries with no PASS" valve in decision 5 is a guess. One real
   CarpetBeater will say whether it is right.
3. Scheduling a batch (a launchd agent, "every other Saturday") is left out
   deliberately. CarpetBeater is twice a month, and the user starting it is
   also the user deciding the ThinkPad is free.
4. A batch of several files (`t2 batch a.batch b.batch`) is not specified.
   `include` covers the composition cases seen so far.
5. Whether `t2 batch report --json` should be the thing a session reads
   instead of the text report. The text is capped and grouped; JSON would be
   larger for the same facts. Left as an option, unused by the procedure.

## 11. Tests for the runner itself

The batch runner is test infrastructure, so it gets the same treatment the
rest of Test2 gets: a pure parser, golden-file reports, and a world it can run
in without touching a host.

### 11.1 Unit tests — `testvm/test2/tests/test_batch.py`

Run by `t2 self-test` and by
`cd testvm/test2 && python3 -B -m unittest tests.test_batch`, under both
`/usr/bin/python3` (3.9) and `python3` (3.14), with no host.

- **Parser**: every kind and directive; `include` and its depth limit; ids and
  their slugs; duplicate ids and duplicate slugs refused; unknown kind, key,
  option; a `t2` word `words.py` rejects; `bigfoot` and a disabled row
  refused; each row of §4.4's refusal table; the file-digest mismatch and
  `--reparse`.
- **Plan**: the schedule table, the predicted finish and the `mode serial`
  figure for a fixture batch; the manual checklist; golden files under
  `tests/fixtures/batch/golden/`, blessed with `T2_GOLDEN=bless` as the plan
  form's are.
- **Family-lock matrix**: every pair of words, including `img → {img, holt}`,
  `GuestTest2 update → everything` (and its implied `serial`), composites
  against every family, and two `t2` entries building the same round.
- **Summary**: built from fixture `plan.json`s and fixture ledger rows —
  every verdict and class, an invalid row, an uncommitted PASS, a `to-class`
  cell, a cell with no row (LOST), a `cmd` entry, a cell whose `fails` line is
  longer than 160 characters, a reason containing a tab.
- **Diff**: `new`, `known`, `fixed`, `same`, a cell absent from the baseline,
  a baseline with a different host for the same cell.
- **Report**: grouping by `(class, reason head)`, the 120-line cap, the group
  cap; golden files.
- **Resume decisions**: every row of §6.2's table, from state fixtures, as a
  pure function (`next_action(state, entry) -> action`) so it is testable
  without running anything.
- **Retry**: each shape in §6.5, and that a `fail` verdict never retries.

### 11.2 Scheduler unit tests — `testvm/test2/tests/test_batchsched.py`

`batchsched` is pure, so every rule of §4.5 is a table test over fake inputs:
a fixture host table through `BKO_HOSTS_JSON` (two big, two small, a
disabled row, a Bigfoot row; one variant with a different `holt_jobs`),
fake ceilings, fake `Profile`s with fake walls per host set, fake running
and session claims, and a fake live probe. No `t2` plan, no ssh.

- **Limits, as invariants**: over fixture batches and a seeded random batch
  generator, at every instant of `simulate()` each pool on each host has at
  most one owner, no pool's slots exceed the table's, the shares on a host fit
  its ceiling unless one entry is alone there, a host of unknown ceiling holds
  one entry, and nothing lands on Bigfoot or a disabled row.
- **LPT**: entries start longest first, file position breaking ties; on the
  fixtures the makespan is within 4/3 of the lower bound (the longest entry,
  or the total work over the hosts), and below `mode serial`'s.
- **No idle host**: a host frees while a splittable entry is ready — it
  starts there when that does not push the predicted finish later, and waits
  when it would (the fixture where one small host would take a big word for
  ten hours).
- **Splits**: `ht1`/`stic1`/`bs2` take any feasible subset; `t2` words only
  the seven candidates, and only when feasible (a pinned cell, a big-only
  guest, an `-arm64` guest make a subset infeasible); GateTest2 and
  composites never split; `hosts=` is never split.
- **Chopin**: ties go against it; a live join there is refused under its
  penalized load and allowed on another host with the same figures.
- **Live join**: allowed only with an empty line, enough `after` room and low
  load; never counted on by `simulate()`.
- **Sessions**: a session's run holding `holt` on cinqcent keeps every `holt`
  entry off cinqcent and none of the others; a session's lock holds an entry
  back and counts toward `lock-wait`, the batch's own never does.
- **Serial and order**: a `serial` entry is a barrier and runs alone;
  `mode serial` reproduces file order one at a time; `after=` is honoured and
  a cycle is refused at parse time.
- **Growth**: an `ht1` entry grows onto a host whose `holt` pool frees after
  the ready entries are placed, never before them; its re-packed prediction.
- **Resume**: re-asserted claims come before any new start; an adopted child
  keeps its hosts; a lost lock race returns the entry to ready with no
  `started`.
- **Determinism**: the same inputs give the same schedule, byte for byte.

### 11.3 End-to-end, no hosts

`tests/test_batch_e2e.py`, with `T2_MAC_ROOT` in a temporary dir, a fixture
host table, no facts probe (a stub facts source) and two kinds of entry:
`cmd` entries (`true`, `false`, `sleep 5`, some with `hosts=` claims) and `t2`
entries run through a stub `t2` (`T2_BATCH_T2=` a fixture script that takes
its family lock, makes a run dir and sleeps):

- a clean batch: statuses, sentinels, `summary.tsv`, exit code;
- concurrency: two `cmd`s claiming disjoint hosts overlap in time, two
  claiming the same host do not, a `serial` one overlaps nothing; the actual
  start order matches `plan`'s;
- run discovery: the stub's run id lands in `entries/ID/run`; a stub started
  while a fixture "session" holds its lock exits refused, and the entry goes
  back to ready and runs once the lock frees;
- TERM with two entries in flight, then `t2 batch resume`: finished entries
  are not re-run, a still-running stub child is adopted, an interrupted
  `cmd` is `INCOMPLETE`, the rest run;
- two drivers at once: the second is refused, naming the first;
- a `cmd` that writes to its stdin: it gets EOF, nothing hangs;
- the beacon appears, lists every entry in flight with its hosts, refreshes,
  is removed, and is ignored when its PID is gone.

### 11.4 On the farm

- `batches/smoke.batch`: `set hosts copland,cage,cinqcent`;
  `t2 GateTest2 selftest hosts=cage`,
  `t2 HoltTest2 alpine324 hosts=copland,cage,cinqcent`,
  `stic1 rel9 alpine324 hosts=copland,cage`, `cmd true -- true`. Cinqcent is
  there so HoltTest2 can build bko-holt (§6.1 item 8); its cell may run on
  any of copland, cage, cinqcent; the other entries stay on copland and cage.
  Runs in under an hour, overlaps at least two entries, and exercises every
  mechanism. Done when it passes twice, its `summary.tsv` matches the runs'
  own reports, each entry ran on the hosts `plan` predicted, and no host's
  `ht1-queue.sh`/`drive.py`/`cmp-run.sh` logs show more jobs at once than
  its table limits.
- **The lid rehearsal**, which is the point of the whole design: start
  `smoke.batch`, close the lid during the holt cell, open it ten minutes
  later, and confirm the batch carries on and the cell gets a real verdict —
  not `LOST`. Repeat with Wi-Fi off for five minutes.
- **A reboot rehearsal**: reboot `cage` during a holt cell; confirm the cell
  shows `LOST` with class `lost`, the entry finishes, and `retry 1` re-queues
  it.
- **A lock rehearsal**: start a session's `t2 HoltTest2 alpine324` while a
  batch waits for the holt lock; confirm the session gets it, the batch waits
  and logs the wait, and neither run is harmed.
- **A spread rehearsal**: `batches/bkotest1all.batch` on all four hosts —
  `stic1` and `ht1` side by side, `ht1` growing onto the hosts `stic1`
  frees. Confirm no host idles while the queue has lines it could take,
  Chopin's temperatures stay under `cmp-run.sh`'s gate, and the predicted
  finish was within 20 % of the actual.
- `t2 batch batches/carpetbeater.batch plan` prints a sane plan before the
  first real CarpetBeater runs as a batch.

## 12. Phases

Each piece is done when its tests pass and its part of §11 is green. Figures
are rough.

| Piece | What | Done when |
|---|---|---|
| **B1** | `t2lib/batchfile.py`: the parser, ids, settings (`mode` too), `include`, `serial`, `after=`, `est=`, the refusal table, the lock matrix, `pools_of`. No execution. (~1 d) | §11.1's parser and lock-matrix tests pass |
| **B2** | `t2lib/batchsched.py`: pools, shares, candidate sets, admission, LPT, `simulate()`, `decide()`, growth; `batch.profiles()` (each `t2` word planned per candidate set, the other kinds' estimates); `t2 batch FILE plan` with the schedule table. (~2 d) | §11.2 passes; `plan` prints a schedule for every saved suite file in `batches/` |
| **B3** | `t2lib/batch.py`'s supervisor: the batch dir, `batch.json`, `placed`/`child`/sentinels, `state.json`, `schedule.txt`, `batch.log`, the driver lock, the beacon, signals; the event loop over `decide()`; `t2` entries as children with run discovery; session claims; the live probe; adoption and `t2 batch resume`, `status`, `ls`. (~2.5 d) | §11.3 passes; `smoke.batch`'s `t2` entries run side by side and resume |
| **B4** | Sleep and drop tolerance: `T2_UNKNOWN_MAX`/`HostIO` patience, the extra ssh options, the supervisor's 255-is-no-answer retries, `batch-keeper.sh`. (~1 d) | the lid and Wi-Fi rehearsals in §11.4 pass; a session's default patience is unchanged |
| **B5** | `t2lib/batchreport.py`: `summary.tsv`, the log fetch with its caps, the diff against `prev`, `t2 batch report`/`summary`/`log`. (~2 d) | §11.1's summary, diff and report tests pass; a real batch's summary matches its runs' reports cell for cell |
| **B6** | The other kinds: `ht1` (via `ht1-farm.sh`, with `--join` for growth), `stic1` (via `drive.py --private-copy`), `bs2`, `cmd` with `hosts=` claims, each with its resume rule and its verdict source. (~2 d) | each kind runs, overlaps and resumes in a real batch; `bkotest1all.batch` and `backshot2alpine.batch` run |
| **B7** | `testvm/test2/BATCH.md` with the analysis section and the token budget; `ANALYSE.md` written at finish; the saved suite files in `batches/`; the lines in `README.md` and `HOSTS.md`; the auto-memory rule (decision 10). (~0.5 d) | a session asked to analyse a finished batch reads `ANALYSE.md` first and reports from it |
| **B8** | The farm rehearsals of §11.4, then the first real CarpetBeater as a batch, watched. (~1 d plus the batch's own hours) | CarpetBeater runs as a batch end to end with no per-host limit exceeded, and its analysis costs one report plus a handful of logs |

B1-B5 are the spec's core, parallel scheduling included, and stand alone:
with them, every Test2 word can be batched across the four hosts, resumed
and analysed. B6 widens it to the other suites, B7 is what makes the
analysis cheap, B8 proves it.

Shared files the work touches, each a small separable hunk recorded in
`testvm/test2/.batch-build-hunks.txt`: `t2` (one `COMMANDS` entry),
`hostjob.py` (`T2_UNKNOWN_MAX`), `pick-host` (the beacon line),
`ht1-farm.sh` (`--join`), `README.md` and `HOSTS.md` (one line each).
`run.py` is not touched: run discovery reads the lock files.
