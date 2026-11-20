# Spec: a growing RAM disk reservation for GuestTest2's first attempt

Status: **PROPOSED 2026-11-20**, not approved, nothing built.

## 0. The problem

A GuestTest2 job runs `vm-update` under `cmp-run.sh --reserve D M`. D is
`guest.reserve_for`: 1.1 x the largest RAM peak of the guest's last 3 measured
rows + 0.25 GiB. `vm-update`'s watcher (`VM._watch`, `overrun_why`) stops the VM
once its overlays pass D + max(1, D/10) GiB. Since ecf84e4 the rerun after an
overrun reserves `rerun_d`: the larger of 2 x D and 2 x the peak, on a host that
fits it.

The first attempt can only reserve from history. A surprise big update (pop24's
point releases, a new kernel series, a Fedora mass rebuild) is bigger than
anything in the last 3 rows. That attempt downloads and installs for 10 to 30
minutes, gets stopped at the limit, and the rerun does all of it again. The
overrun feeds no stop rule, so the run still gives the right answer. What it
costs is one attempt's wall time and its writes, every time an update gets
bigger.

## 1. The idea

Let a running job ask for more RAM disk before it reaches its limit, while the
host has room to give. The attempt keeps going instead of being stopped and
rerun. When there is no room, the job overruns as it does now, and `rerun_d`
still applies.

## 2. Pieces

1. **`cmp-run.sh --grow DISK_GIB`** (new). It finds the reservation of the
   nearest ancestor that holds one, the same way `vm-update`'s `reserve_row`
   does. Under the ledger lock it runs `prune`, then works out the room as a
   new `--reserve` would get it after those in line (`budget`, then
   `after_line` for a PID that isn't in line). If DISK_GIB minus the row's own
   DISK fits that room, and DISK_GIB is within `ceiling()`, it rewrites the
   row's DISK in place (`rewrite`, the same inode) and exits 0. Otherwise it
   exits 1 at once. It never waits and never takes a place in line, so it
   can't take room that a waiter was offered first. MEM doesn't change: the
   static memory rule already counts the whole tmpfs as full.
2. **`vm-update`'s watcher.** When `VM_UPDATE_GROW_MAX=GIB` is set and the
   overlays pass 0.8 x the reservation, it calls
   `cmp-run.sh --grow min(GROW_MAX, max(2 x reserved, 1.5 x kib))`, at most once
   every 60 s. If that works, `self.reserved` and the limit move up and it notes
   `grew: D -> D2 GiB`. If it fails, it notes that once and carries on, and
   `overrun_why` stops the VM at the old limit as it does today. The result gets
   `reserved_gib` (the last figure) and `grown_from_gib`.
3. **`guest.py`.** `job_env` passes `VM_UPDATE_GROW_MAX` = min(`ram_gib_max`,
   the host's ceiling from facts) for try and probe modes. An update round
   doesn't get it: it is the real merge, and it keeps the strict limit.
   `reserve_for` needs no change, because the row's peak is real now, not a
   floor. A row that grew isn't `_cut_short`, so its peak counts as measured.
4. **The report.** `reserve_notes` lists the jobs that grew (guest, D -> D2,
   peak), so a D that keeps growing is visible before it becomes the next
   default.

## 3. Interactions

- **Backfill and the line.** A grow only takes room that `after_line` would
  give to someone with no place in line, so a waiter that was held back for
  keeps its room. A grow can make a waiter wait longer, just as a job that
  started before it would.
- **Other tmpfs users** (compare.py, holt RAM work, other sessions'). The live
  checks in `budget()` (df, MemAvailable) count them, as they do for any
  `--reserve`.
- **The watcher's 1 GiB-free rule** still stops a run that is over its
  reservation while the RAM disk is nearly full, grown or not.
- **Version skew.** `hostjob` runs the synced tree's `~/backotter-work/cmp-run.sh`,
  and `vm-update` runs from the run's copy. An older `cmp-run.sh` takes `--grow`
  as a TAG, fails its SLOTS check, and exits 1 before it writes anything but
  the ledger files. So the grow fails, and the job overruns as it does today.
- **Other families.** HoltTest2 and SticTest2 reserve through the same ledger
  but never call `--grow`, so nothing changes for them.

## 4. Tests

- `compare/cmp-run-test.sh` (`CMP_LEDGER_DIR`, on a host):
  - a grow that fits rewrites only its own row;
  - a grow that doesn't fit, or would take a waiter's offered room, exits 1
    and leaves the ledger as it was;
  - a grow over the ceiling is refused;
  - a grow without an ancestor's row is refused.
- `tests/test_vm_update.py`: the watcher with a fake grow command
  (`VM_UPDATE_GROW_CMD`, tests only):
  - it grows at 0.8 x and the limit follows;
  - when the grow fails, it overruns at the old limit;
  - it calls at most once a minute.
- `tests/test_guest.py`:
  - `VM_UPDATE_GROW_MAX` is passed for try and probe, and not in an update
    round;
  - a grown row's peak counts as measured in `reserve_for`.
- On the farm: one GuestTest2 of pop24 with its D forced low
  (`T2_GUEST_D` for tests, or an edited ledger copy). It should grow and PASS
  on its first attempt.

## 5. Cost and risk

About 40 lines of bash, 40 of Python in `vm-update` and 15 in `guest.py`,
plus tests. That is roughly half a day with a host for `cmp-run-test.sh`.

The risk is in `cmp-run.sh`. Every family uses it, and so do other sessions'
jobs, at the same time. A wrong rewrite of `.reserve` would break every
reservation on that host. So it has to go through `cmp-run-test.sh` on a host
before it is synced, and get synced while `t2 batch status` shows nothing
running.

## 6. Decisions for the user

1. Build it, or live with one lost attempt per surprise update. *Recommend:*
   build it. It is the only remaining reason for a first-attempt overrun, and
   each one costs 10 to 30 minutes.
2. The grow step: 2 x D (as `rerun_d`), or the measured kib x 1.5.
   *Recommend:* the larger of the two, within `GROW_MAX`.
3. Should an update round grow too? *Recommend:* no. A real merge keeps the
   strict limit, and its overrun is what the user wants to see.
