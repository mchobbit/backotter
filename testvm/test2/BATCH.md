# t2 batch: unattended batches, and how to read one

The design is `docs/design/batch.md` (approved 2026-11-15).  This file is the
user's and a session's working copy: how to start a batch, what it leaves, and
the procedure for analysing a finished one, with its token budget.

## Starting one (the user, on the Mac)

```
t2 batch batches/carpetbeater.batch plan      # the schedule: hosts, start, end, finish; runs nothing
t2 batch batches/carpetbeater.batch           # runs it; again later continues it
t2 batch status                               # what runs now, re-predicted from now
t2 batch resume BATCH                         # after a crash or a closed terminal
testvm/test2/batch-keeper.sh BATCH            # resumes it in a loop until it ends (a second terminal)
```

Check `t2 batch status` before starting heavy work by hand: a running batch
holds whole pools on its hosts (its beacon names them), and a session's own
`t2` run always wins its family lock.

The saved suites are in `batches/`: `carpetbeater`, `bkotest2all`,
`bkotest1all`, `holttest1all`, `backshot2alpine`, `smoke`.  Each opens with a
comment naming what defines the suite.

## What a finished batch leaves

`MAC_ROOT/batches/BATCH/` (MAC_ROOT is `~/backotter-work/test2`):

| File | What |
|---|---|
| `ANALYSE.md` | read this first: this batch's numbers, the logs worth reading, then the procedure below |
| `report.txt` | `t2 batch report BATCH`: one screen, at most 120 lines |
| `summary.tsv` | one row per cell: entry, cell, verdict, class, since, host, attempt, gm, wall_s, run, row, log, reason |
| `logs/ENTRY/CELL/` | the records of each non-PASS cell, fetched (at most 8 MiB and 50 files a cell; `FETCH.txt` says what was left on the host) |
| `batch.log` | the timeline: each start, grow, wait and end with its reason |
| `state.json`, `schedule.txt` | each entry's state; the last schedule |
| `entries/ID/` | the entry's sentinels, its `entry.log` and its run's own `report.txt` |

`since` is the diff against the previous batch of the same file (`prev`):
`new`, `known` (the baseline had the cell failing with the same reason head,
its first 60 characters), `fixed` (failing then, passing now) or `same`.
`class` is the ledger's: `fail` is a finding, `harness` and `lost` are the
farm's own trouble, `known-flake` is noise, `to-class` a log line waiting for a
person's class.  `row` is the ledger row id: `t2 ledger show --cell CELL`.

## Analysing a batch

A session asked to analyse a finished batch follows these steps, in order,
and stops as soon as the question is answered.  Read `ANALYSE.md` in the
batch dir first: it has this batch's numbers and names the logs worth reading.

1. **`t2 batch report <BATCH>`**: one screen, at most 120 lines.  The batch, its
   hosts, start, end and wall time; each entry's status, exit and cells by
   verdict; the counts by verdict and by class; the stops that fired and any
   entry that did not finish; the failing cells grouped by class and reason
   head, each group with its count, cells, hosts and `since`; and the diff
   against the previous batch.  For most batches this is the whole analysis.
2. **Read logs per group, not per cell.**  For each group whose class is
   `fail` and whose `since` is `new`, read one representative:
   `t2 batch log <BATCH> ENTRY:CELL --tail 200`.  Stop at **10 groups**; if
   there are more, say so and report the biggest ten.
3. **Triage `harness` and `lost` by reason alone.**  They are the farm's
   trouble.  Read a log only when three or more cells share one reason head,
   or when they outnumber the `fail` cells.
4. **Known failures are the baseline's.**  `since=known` means the previous
   batch had that cell failing with the same reason head.  Group them, name
   them, and do not open them unless the user asks.
5. **Report**: one line per group: class, reason, how many cells, which hosts,
   new or known, and the one next step.  Name `row` ids for anything claimed.
   Then the entries that did not finish, and the manual steps the batch
   printed but did not run.

**The token budget.**  One report (at most 120 lines), at most 10 cell logs at
at most 400 lines each (`t2 batch log` never prints more), and no
`summary.tsv` read in full unless the report is unclear: it is in the dir,
and `awk -F'\t' '$3 != "PASS"' summary.tsv` beats reading it.  That is the
whole first pass, and it costs the same whether the batch ran 12 cells or 400.

"Known" needs no new store: `since` comes from the previous batch's
`summary.tsv`.  A failure that should stop being reported goes, by hand, where
it always has: `guest/limits.json` or `stic/limits.json` (a LIMIT),
`classes.json policy` (a flake), `t2 guest-lines --accept` (a log line).

## The commands for the output

```
t2 batch report BATCH [--prev BATCH] [--json]   # the report (--prev: another baseline, this print only)
t2 batch summary BATCH [--no-fetch]             # summary.tsv again from the ledger (and the logs)
t2 batch log BATCH [ENTRY:]CELL [--tail N] [--head N] [--file NAME] [--list]
t2 batch ls [--last N]
```

`t2 batch log` prints the cell's verdict line, its `FETCH.txt`, then the main
file of its fetched records (by name: run.out, the verdict and judge files,
*.log), its last 200 lines by default; `--list` names every fetched file and
`--file NAME` picks one.

## The kinds of entry

| Kind | Runs | Verdict from | After an interruption |
|---|---|---|---|
| `t2 WORD` | a child `t2 WORD --hosts S` | the run's exit and its ledger rows | `t2 resume RUN` (gate: set aside, re-queued once) |
| `ht1 RUN GUEST...` | `ht1-farm.sh RUN-XXXX --hosts S`, then watches the queue | the ledger's `holt:` rows of the queue, else its done rows | the runners joined back (`ht1-farm.sh --join`) |
| `stic1 ROUND [GUEST...]` | `drive.py --private-copy --hosts S --logs-mark st1-ROUND-XXXX` | the ledger's `stic:` rows of the mark, else `results.tsv` | the same mark again (drive.py adopts what ran) |
| `bs2 WORD [ARG...]` | `bs2 --hosts S WORD`, then watches its run | the ledger's `bs2:` rows of the run | re-attached; a stopped queue restarted from its `queue.next` |
| `cmd NAME -- CMD` | CMD on the Mac, stdin /dev/null | exit 0 PASS, else FAIL | `INCOMPLETE` (`rerun=yes`: again) |

XXXX is the batch id's last four (`rN` added on a retry), so a batch never
meets an earlier one's queue or mark.  An `ht1` entry is the one whose hosts
can grow: when a host's holt pool frees, the batch adds it (`placed` gets a
grow line) and the entry's watcher starts a runner there with `--join`.
`ht1`, `stic1` and `bs2` are never signalled when the batch stops: their work
is on the hosts, and `t2 batch resume` picks their watchers back up.

A test `MAC_ROOT` (anything but `~/backotter-work/test2`) never runs the real
`ht1-farm.sh`, `drive.py` or `bs2`, which reach the farm: `T2_BATCH_HT1_FARM`,
`T2_BATCH_DRIVE` and `T2_BATCH_BS2` name their stubs, and without one the
batch refuses the entry.
