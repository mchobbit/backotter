# Fork audit tools

Checks of BackOtter's restic and rustic forks against the stock releases
they are based on: the stock test suites run once and kept (the baseline),
the forks' suites run against it with warm build caches, and a record of how
far the code audits have read, so that the next one reads only what is new.
Everything runs from the Mac; the tools copy what they need to the hosts
themselves (never into `~/backotter-work/testvm` or `stick`, which belong to
`sync-hosts.sh`).

| Script | What it does |
|---|---|
| `stock-baseline.sh [--force] [--restic-host H] [--host H] [NAME ...]` | Builds and tests stock restic 0.19.1, rustic_core 0.13.0 and rustic 0.11.4 (`audit-lib.sh`'s `STOCK`: the forks' merge-bases with upstream) once, keeps the results on the host, copies them to the Mac and writes `stock-baseline.txt`; a baseline already there is skipped unless `--force` |
| `fork-tests.sh [--force] [--restic-host H] [--rustic-host H] [NAME[=REV] ...]` | Builds and tests the forks (default: the Mac's `backotter` branches) the same way and compares them test by test with the baseline: fork-only failures (with their output), failing build targets, skips of tests stock passes, tests missing, tests added, stock's known failures; exit 1 on any fork-only failure, 2 if a run didn't complete |
| `to-audit.sh [NAME ...]`, `to-audit.sh --mark [--what TEXT] [NAME[=REV] ...]` | Per fork, the commits and diffstat from its last audited commit (`audited-heads`) to its branch head; after a rebase, the range-diff instead.  `--mark` moves the lines on after an audit (commit `audited-heads` then) |
| `audit-host.sh NAME:SRC:OUT[:CORE] ...` | The host part: builds and tests the trees one after another (its header lists the exact commands and what `OUT` holds) |
| `results.py` | Parses `go test -json` and `cargo test`'s output into per-test rows (`results.tsv`), and compares a fork's with stock's |
| `audit-lib.sh` | What the two Mac scripts share: the stock versions, the hosts' layout, copying, launching and waiting |
| `audited-heads` | The last audited commit of each fork, its date and what audit |
| `stock-baseline.txt` | The baseline's summary: versions, commits, toolchains, step times, counts, stock's own failures by name |

## Guest tools audit

`guest-tools.sh` lists the tools on each test guest and says what changed
since the last run: a check that a test's tools (`findmnt`, `wipefs`,
`getfacl`, `zpool`...) are still there, re-runnable after `vm-update` or a
rebuild.  It runs on Chopin, from `~/backotter-work/testvm/audit/`:

```
guest-tools.sh [-j JOBS] [-n] [GUEST ...]     # default: every guest, 3 at once
guest-tools.sh --diff OLD.txt NEW.txt         # two listings' +/- tools
```

- Each guest boots as a throwaway copy (`compare/guest-run.sh`, `--ram`)
  under `cmp-run.sh --reserve 1 5` (`RESERVE="D M"` changes it),
  `WEAR_TAG=audit:gt-YYYYMMDD:GUEST`, and runs `probe.sh`, which prints an
  `exe DIR NAME` line per executable on the usual bin dirs, `os` lines and
  `end`.  Ports 2700-2779 only, one set per job slot.
- omarchy and ubuntu26-zfsmirror (a `luks-passphrase` or `zfs-passphrase`
  file) are read instead: `sudo vm-mount --ro --key FILE`, `probe.sh` under
  `chroot`, `vm-umount`.
- Never probed: sculpt2610, redox, freebsd15-builder, haiku6*, iso, old.
- Out: `~/backotter-work/audit/guest-tools/YYYYMMDD/GUEST.txt` (the
  listing) and `GUEST.log`; a listing that doesn't end with `end` (or has no
  `exe` line) is a failed probe, kept as `GUEST.failed.txt` and never
  compared.  The report gives each guest's tool count and the `+`/`-` tools
  against its newest listing in an earlier dated dir; exit 1 if any probe
  failed.

## What runs where

restic (Go) runs on Chopin; rustic_core, then rustic, on Cinqcent; the two
hosts at once.  `--host`/`--rustic-host chopin` moves the Rust part to
Chopin when Cinqcent can't.  Commands follow the projects' CI
(`.github/workflows/ci.yml`):

- restic: `go build ./cmd/restic`, `go vet ./...`,
  `go test -count=1 -json -p 8 ./...`, and `go build ./cmd/restic` with
  `CGO_ENABLED=0` for windows/amd64 darwin/arm64 freebsd/amd64 openbsd/amd64
  netbsd/amd64 solaris/amd64 aix/ppc64 linux/386 linux/arm linux/s390x
  dragonfly/amd64.  Go is `~/sdk/go1.25.10` (go.mod's toolchain line),
  `GOTOOLCHAIN=local`.
- rustic_core: `cargo test --all-targets --all-features --workspace
  --examples --no-fail-fast`, and the same with `--doc`.
- rustic: `cargo test --locked --all-targets --features release --workspace
  --no-fail-fast`, and `--doc`.  Stock takes rustic_core 0.13.0 from
  crates.io by its own Cargo.lock (the release of rustic_core's 1030be4, the
  rustic_core fork's merge-base); the fork is pointed at the rustic_core fork
  tree of the same run with `stick/prep-rustic-src.sh`, as `build-round.sh`
  builds rustic-bko.
- Rust 1.94.1, Chopin's: on Cinqcent, whose default stays 1.85.1 (stock
  rustic needs 1.91), rustup's toolchain 1.94.1 (`rustup toolchain install
  1.94.1 --profile minimal`, done 2026-11-04) through `RUSTUP_TOOLCHAIN`.
  The dev profile without debug info, not CI's `-r` (fat LTO for every
  test binary); doc tests are not in CI but are run.

Each host's run is one detached job (`setsid`, its log and exit in
`~/backotter-work/bko-audit/logs/TAG.log` and `TAG.exit`; a TAG still
running isn't started again, the script waits on it), and goes through the
shared ledger: `cmp-run.sh --reserve 6 12` (6 GiB of RAM disk for the
tests' temporary files, 12 GiB of memory; `RESERVE="D M"` changes it),
which waits for room and for the CPU to be under 95 °C, and runs at
`nice -n 10` with 8 jobs (`-p 8`, `-j 8`, 8 test threads).

## Where things live

On each host:

| Path | What |
|---|---|
| `~/backotter-work/bko-audit/bin/` | `audit-host.sh`, `results.py`, `prep-rustic-src.sh`, copied by the Mac scripts |
| `~/backotter-work/bko-audit/stock/NAME-VERSION/` | the baseline: `results.tsv`, `details.json`, `info.txt` (commit, toolchains, rustic_core used), `steps.tsv` (step times), raw logs, `bin/` (restic; rustic's debug build) |
| `~/backotter-work/bko-audit/fork/NAME-COMMIT/` | a fork run, the same (`rustic-COMMIT+core-COMMIT`) |
| `~/backotter-work/bko-audit/logs/` | the detached jobs' logs, pid and exit files |
| `/bkoscratch/bko-audit/src/NAME-COMMIT/` | source trees, `git archive` of the Mac's `~/git/NAME`, reused for the same commit; `rustic-COMMIT+core-COMMIT/` the fork rustic pointed at that rustic_core |
| `/bkoscratch/bko-audit/cache/gocache`, `gomod` | `GOCACHE`, `GOMODCACHE` |
| `/bkoscratch/bko-audit/cache/cargo-home` | `CARGO_HOME`: crates.io registry and git checkouts |
| `/bkoscratch/bko-audit/cache/target/NAME` | `CARGO_TARGET_DIR` per project, shared by stock and fork, so the fork reuses stock's compiled dependencies; emptied first when over 60 GiB.  A job holds the target's lock while it runs, and when the target's last job built other trees, this job's trees get their files' times set to now first: cargo finds a crate fresh by its files' times, wherever its tree is, and `git archive` dates them to the commit, so an older tree would otherwise be tested with the binaries a newer one built |
| `/mnt/bko-ram/bko-audit/JOB` | the tests' `TMPDIR`, removed after the job |

The caches are on `/bkoscratch`, not the RAM disk, and nothing cleans them.

On the Mac, `BKO_AUDIT_LOCAL` (default `${TMPDIR:-/tmp}/bko-audit`) holds the
copies: `stock-baseline/NAME-VERSION/` and `fork-tests/` (results without
`bin/`; `NAME.report` the comparison).  `fork-tests.sh` fetches a missing
baseline from the hosts.  Results here or on a host count only when their
`info.txt` names the commit asked for.

## How long

Measured 2026-11-04:

| Run | Wall | Chopin (restic) | Cinqcent (rustic_core, rustic) |
|---|---|---|---|
| `stock-baseline.sh`, caches empty | 12 min | 529 s: mod download 25, build 40, vet 13, test 37, the 11 cross builds about 40 each; after 2.5 min waiting in the ledger (CPU over 95 °C from another session's build) | 613 s: rustic_core 312 (`cargo test` 308, `--doc` 4), rustic 288 |
| `stock-baseline.sh --force`, warm | about 1 min of work | 45 s (test 21, cross builds 1-3 each) | rustic_core 40 s, rustic 12 s |
| `fork-tests.sh`, new fork trees, caches warm from stock | 8.8 min, 7 of them waiting in the ledger | 100 s (test 29, cross builds about 6 each) | 183 s: rustic_core 80, rustic 100 (prep-rustic-src 11, `cargo test` 88: the fork's rustic_core and rustic compiled) |
| `fork-tests.sh --force`, same commits, warm | 97 s | 92 s (63 s of steps) | 62 s: rustic_core 45, rustic 8 |

A fork commit later on changes only its own crates (and restic's packages):
expect about the third row without the wait.  A Rust run after one of
other trees in the same target (stock after fork, or another fork commit)
recompiles the project's own crates, as the third row does.  The ledger wait is
whatever the host is doing; `cmp-run.sh` holds a job back while the CPU is
over 95 °C.

## What the environment changes

- restic's `TestStdinFromCommand` and `TestStdinFromCommandNoOutput` run
  `python`, which Debian names python3: `audit-host.sh` puts a `python`
  link to python3 at the end of `PATH` (`bin/pyshim`), as CI has one.
- `RUST_BACKTRACE` is unset: with it, rustic_core's insta snapshots of
  errors (`tests/errors.rs`, `test_check` cases 3 and 4) fail on stock too.
- Tests run as user m, without root: the fork's tests that mount file
  systems or set restricting flags skip (15 restic tests, listed as added
  and skipped).  GateTest2 runs those as root on their own
  (`gate:fork-root:restic`, `testvm/test2/gate/fork-root.sh`): only the ones
  `testvm/test2/gate/restic-root-tests.txt` lists, each reviewed (14 at
  restic 5253b20), compiled as m with these caches and run as root with
  their temporary files in a reserved RAM directory.

## Fork-only failures, 2026-11-04

`fork-tests.sh` on restic d2112d619, rustic_core 6e8b009, rustic f989942:
restic and rustic_core none; rustic one,
`tests/backup_restore.rs :: test_backup_and_check_passes` (the second,
unchanged backup must add 0 B; it adds 235-983 B, "Dirs: 0 new, 5 changed").
It passes alone (`RUST_TEST_THREADS=1`) and fails with the other tests of
the binary running beside it, on tmpfs and on XFS (noatime) alike: the
changed directory is the backup path's parent, the shared `TMPDIR`, in
which the other tests make and remove their temporary directories.
rustic-bko gives the parents of a backup path nodes with their real
metadata (rustic_core's `missing_dirs_get_their_own_nodes`); stock's are
synthetic and never change.  The fork-harm audit's own run of the same suite
on Chopin failed the same test.

GateTest2 classes this one as known (`testvm/test2/gate/baseline.json`,
a LIMIT with its reason): it still fails at the heads of 2026-11-08,
rustic 6ccec58 with rustic_core 96596bb (`fork-tests.sh --force` on
Cinqcent; rustic_core itself has no fork-only failure there).  Any other
fork-only failure fails the gate.

## GateTest2's fork layers

`testvm/test2/t2lib/gate.py` runs `fork-tests.sh --force NAME...` for its
layers `gate:fork:restic`, `gate:fork:rustic_core` and `gate:fork:rustic`
(one job per host, as by hand), with a tag and results of its own:

```
BKO_AUDIT_TAG=RUN BKO_WEAR_TAG=gate:RUN:fork@HOST \
    BKO_AUDIT_LOCAL=~/backotter-work/test2/runs/RUN/audit fork-tests.sh --force --restic-host H restic=COMMIT
```

with RUN the run id (`t2g-20261108T0315-4f2a`; SticTest2's `t2s-...` when it
runs these layers, `RUN-r2` for the rerun), one such run per host, NAME=COMMIT
the `backotter` heads of BKO_GIT (the layers' keys).

- `BKO_AUDIT_TAG` makes each host's job `RUN-HOST` (by hand it is
  `fork-HOST`), so a job another session's `fork-tests.sh` has running is
  never taken for this run's (`launch` waits on a TAG already running), and
  puts the tools in `~/backotter-work/bko-audit/bin-RUN` instead of the
  shared `bin/` (gate.py removes it afterwards).
- `BKO_WEAR_TAG` is the jobs' `WEAR_TAG` in the wear log (by hand
  `audit:TAG`).
- `--force`: results for the same commits are built and tested again, so a
  rerun after a FAIL, or after a change to these scripts, really runs.
- Each layer is judged from `NAME.report` against `baseline.json`: a
  fork-only test failure or failing build target that it lists is a LIMIT
  with its reason; any other one fails the layer.

The defaults stay as they are for use by hand.

## A new upstream release

After a fork is rebased onto a new release: update `STOCK` in
`audit-lib.sh` (version and merge-base commit), run `stock-baseline.sh NAME`
(the old baseline stays beside it), and commit the new
`stock-baseline.txt`; `fork-tests.sh` warns when a fork isn't based on its
`STOCK` commit.  `to-audit.sh` then shows the range-diff of the fork's
commits and the upstream range taken in.
