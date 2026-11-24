# bko-holt's JSON protocol: what bko-rescue reads and how it drives load

Status: approved 2026-11-24 by the user, with every recommendation
(J1-J21), after two reviews (one against holt/src and the rustic fork, one
against the screens and the dry run's safety).  The spec `rescue-tui.md`'s D12 asks for
before RT10's code.  Nothing in it is built.

bko-rescue (the stick's restore program, `rescue/`) runs bko-holt as an
administrator would and reads JSON from it, never linking its code
(`ui.md`, "bko-holt stays a CLI").  This document fixes that interface:
the commands and flags, every JSON document and event, how secrets travel,
the stable problem and failure codes, the dry-run lock, how each screen of
`rescue-screens.md` is fed, and the holt changes that need, in steps
RT10a to RT10p.

**Conventions.**  est. marks an estimate.  *Check* marks a fact the code
doesn't settle, to be settled at the step named.  J1-J21 are the decisions
left to the user, each with a recommendation.  Code references are to main
at d001100 plus the uncommitted tree, read 2026-11-24.  "The screens" is
`rescue-screens.md`; "the demo" is `rescue/src/demo.rs` with
`testvm/rescue/fake-bko-holt`.

## Summary

- **One flag, `--json`, on `load` and three new commands.**  `load ...
  --json` writes one document (plan, status) or a stream of events (the
  writing stages) on standard output, nothing else.  `bko-holt --version
  --json` says which schemas this bko-holt speaks (J1); `bko-holt stick
  --json` reads the stick's config partition (D11); `bko-holt
  recovery-words` checks a typed recovery key (J21).  Without `--json`
  nothing changes: same text, same exit statuses, same prompts.
- **Seven document formats,** each with `format` and `schema`:
  `bko-holt-api`, `bko-holt-plan`, `bko-holt-status`, `bko-holt-events`
  (NDJSON), `bko-holt-stick`, `bko-holt-recovery`, and `bko-holt-error`
  for a run that fails before its document.  The events keep the
  provisional form RT8's demo reads (`start` ... `end`), with fields added;
  demo.rs's changes are listed under "Compatibility".
- **No prompts under `--json`.**  Every key a command would ask for is a
  `key_needed` problem, a missing repository password `password_needed`,
  every command's standard input is `/dev/null`, and load runs with no
  controlling terminal.  Secrets go as they do today, in 0600 files in a
  0700 directory in RAM, named by `--key-file` and `--password-file`;
  under `--json` bko-holt checks those files' modes and owners and refuses
  loose ones (J3).
- **Never over the old system without `--erase`** (J19).  Found in the
  code: a target carrying the layout's own identifiers (the machine's own
  disk on a same-machine restore) needs no `--erase` today, so `--commit`
  alone can luksFormat it or restore into its old filesystems.  Under
  `--json`, a plan or a run refuses any target that isn't blank unless it
  is in `--erase` or this session's partition record shows load wrote it.  The
  plan lists, per target, what the run would wipe and make.
- **The run is the plan reviewed** (J20): the plan carries a hash of its
  commands and the disks' identities; the writing run takes it with
  `--expect-plan` and refuses (`plan_changed`) when anything moved.
- **Stable codes** on every problem, note, refusal and failure, beside
  today's text.  The codes the screens act on are listed here; the rest
  are `other` with their text, and block like any problem (J4).  Every
  unknown value of an enum a screen acts on fails safe.
- **Disks of another size:** each disk carries a size verdict per source
  disk, with fit's own figures and fit's own decision on whether the data
  fits.  The 10 % offer is the UI's rule (J5).
- **Dry run:** `BKO_HOLT_NO_COMMIT=1` makes every command of bko-holt that
  writes to a disk refuse `--commit` (exit 2, a fixed message) before it
  reads the layout.  Two dry-run gaps found in the code: load without
  `--commit` stops at `boot` (no restored tree), and a writing run on a
  used disk stops at `not_blank` because `--erase` is never passed (J6,
  J11).
- **Stopping:** under `--json`, SIGTERM is honoured between commands and
  passed to the archiver's process group as SIGINT; `end` says `stopped`,
  exit 5 (J7).
- **The copy and resume:** today `restore` counts as done once `/etc/fstab`
  exists, so a stopped copy would be skipped by `--through` and booted.
  Under `--json`, restore is done only with a mark written after the
  stage's last command (J10).  `--only assemble` makes containers anew, so
  "open what exists" needs a non-creating `open` stage (J14, RT10o); until
  it lands, and whenever temporary keys were lost to a reboot, Resume
  offers only Start again.
- **The dump gains** `keyboard` and `time` sections and every platform's
  mount sizes, so the stick can offer the machine's layout and show its
  times (J15).
- **Size:** RT10's core (RT10a-RT10j) is ~8 d est., against the 3-4 d
  `rescue-tui.md` counted; with D11's `stick --json`, the dump's new
  sections, per-disk fit, the `open` stage and the recovery-words command
  (RT10k-RT10p) ~16 d.  The core is what RT12 needs; the rest can follow,
  and the screens already say what they show until then.

## What bko-holt does today

Read from the code, so the protocol is built on what's there.

### The commands

- `main.rs`: `dump`, `load`, `report-bug`, `recovery-key-file`, `-V`,
  `--license`, and hidden helpers the stages run (`snapcore-boot-areas`,
  `md-lbs`, `umount-tree`, `efi-dedupe`, `grub-prefix`, `luks1-digest`,
  `raw-write`, `write-table`, `rewrite`).  It dispatches on the first argument alone
  (`-V|--version` included).  `dump` already writes JSON (`format`
  `bko-holt`, `schema` 1, `report.rs`); its exit 3 means unsupported.
- `load/mod.rs`: `Options`, `parse_args`, `Stage` (plan, status, partition,
  assemble, format, mount, restore, boot, close).  Neither `--through` nor
  `--only` is `plan`; `--only status` is `status`; `--through STAGE` runs
  the writing stages from `partition` to STAGE; `--only STAGE` runs one.
  Exit statuses: 0 done, 1 a stage failed, 2 bad arguments (a layout that
  can't be read is 2 too), 3 a layout load can't take, 4 plan problems.
- `load::run` returns `(code, stdout, stderr)` as `String`s; `main_inner`
  prints stdout once the run is over.  `plan::run`, `status::run` and
  `exec::run` build those strings.  Nothing is shown while a run goes on.
- `plan::check` makes every check: `target::prepare` (scan, map, fit,
  holders), `commands::build`, the archiver, tools, LVM, thin, ZFS
  features, a report-bug layout, not root.  Problems and notes are
  `Vec<String>`, about 250 problem sites in all (85 in `commands.rs`, 24 in
  `target.rs`, 17 in `reformat.rs`, the rest across the families).
- `status::rows` gives `(Stage, State, why)` per stage, `State` one of done,
  partly, not done, nothing to do, unknown, and `next`.  `restore` counts as
  done once `/etc/fstab` and `/etc/os-release` (or the family's marker)
  exist in the mounted tree: a stopped copy has them too, and so has the
  old system on its own disk.  `boot` counts by a mark in RUN_DIR
  (`boot.done`), written after the stage's last command.
- `exec::run`: the same checks first (exit 4 on any problem, nothing
  written); `--commit` refused under `--sysroot` or not root (exit 1);
  `--through` skips a stage status shows done or nothing to do (no skip
  after a partition stage on an `--erase` disk); `--only` does what it's
  told, every time.  Without `--commit` each stage prints its commands.
  With it, each command runs through `run_cmd` (with `LC_ALL=C` added to
  its environment, in load's own process group, the terminal's stdin when
  the step gives none), its stdout and stderr collected and logged to
  `RUN_DIR/log/load.log`, which `boot` copies into the restored tree's
  `/var/log/bko-holt`; a failure ends the run with the last 5 lines of the
  command's stderr.  After each stage the disks are read back and must
  show it done; a dry run's stages aren't read back.
- `plan::run` prints every stage's commands even when there are problems;
  `exec::run` stops at the problems before printing any (exit 4).
- The restore stage isn't only the archiver: after it come the shared
  btrfs snapshots (placeholders deleted, `btrfs property set ... ro
  true`), `thin_restore` (raw volumes, LV snapshots) and the atimes'
  "before" capture that `boot` replays.  The archiver is often not load's
  direct child: the shared snapshots run it inside `sh -c` scripts, some
  of them only on one branch of an `if`, and FreeBSD's msdosfs roots wrap
  it in `sh -c '"$@" 2>err ...'`.
- `report-bug` (`bugreport.rs`): every load, plan and status included,
  records itself in `RUN_DIR/last-load.json` (args, exit, stderr tail),
  overwriting the last; report-bug redacts
  by `redact.rs`'s `Kind`s; `--no-redact`, `--stderr FILE`, `-- LOAD-ARGS`.
  `ISSUES` is `/issues/new/choose`.

### Every question, prompt and passphrase read

- **cryptsetup** without `--key-file`: `luksFormat` asks for the new
  passphrase (once: `--batch-mode` turns off its verify) and `open` asks
  again, on the terminal load inherits
  (`run_cmd`: no stdin given means the terminal's).  A container whose
  crypttab names a key file gets a temporary random key instead
  (`RUN_DIR/keys/NAME`), replaced in `boot`.
- **GELI** without a key file: `geli.rs`'s `prompt` runs `sh -c` reading
  `/dev/tty` with echo off, into `RUN_DIR/keys/SRC.pass` (asked once).
  It reads `/dev/tty` itself, so a null stdin doesn't stop it.
- **softraid** without `--key-file`: `bioctl` asks for the new passphrase,
  twice.
- **ZFS** `keyformat=passphrase` without `--key-file`: already a plan
  problem ("give its new key with --key-file").
- **A kept LUKS2 header** (Ubuntu Core, `--keep-luks-header`) without a key:
  a plan problem.  `recovery-key-file` reads the recovery key on stdin.
- **rustic-bko** without `--password-file`: asks for the repository
  password, on `/dev/tty`; an sftp repository's ssh may ask about a host
  key there too.
- **No confirmations.**  `--erase DEV` is the only one, on the command line
  (`layload.md`, "Safety", rule 2).  load never asks yes or no.

### `--commit`, `--erase` and the dry run

- `--commit` lets the writing stages write; without it they print.
- `--erase DEV` names a target that isn't blank, or is held (LVM, LUKS,
  md, a pool), as one load may wipe; it's resolved like `--map`'s
  targets, and one that isn't a target is a problem.
- **Found:** without `--commit`, `exec::run` still refuses `boot` when the
  target tree holds no restored system (the check comes before the commit
  test), so `load --through close` without `--commit` on a fresh machine
  ends at `boot` with exit 1.  A dry run can't tick through `boot` and
  `close` as `rescue-tui.md`'s "Dry run" says (J11).
- **Found:** without `--erase`, a target that isn't blank is a problem
  (exit 4), and load without `--commit` stops at the problems before
  printing a command.  So a dry run on a machine's used disk, with
  `--erase` never passed (`rescue-tui.md`, "Dry run"), stops at
  `not_blank` and prints no commands (J6).  (The plan itself still prints
  them, under its problems.)
- **Found: a target with the layout's own identifiers needs no
  `--erase`.**  `check_target` raises neither `held` nor `not_blank` when
  `TargetDisk::is_ours` (the source's table GUID or MBR signature, or only
  the layout's UUIDs), and without `--erase` `partition_counts_done` is the
  table check alone.  The source machine's own disk as found is "ours" as
  much as a disk an earlier stage wrote: only the `RUN_DIR/partitioned/`
  records tell the two apart, and a reboot forgets them.  So on a
  same-machine restore, `--through close --commit` without `--erase`
  skips `partition`, runs `cryptsetup luksFormat --batch-mode` over the
  user's own container (the mapper isn't open), or, for a plain disk,
  skips `format` (the UUIDs match) and `restore` (the old `/etc/fstab` is
  there) and runs `boot` on the old system.  The test
  `the_sources_own_disk_is_let_go_of_too` asserts the "neither refused
  nor let go of" half.  Text mode keeps it (Principle 1); under `--json`
  J19 closes it.
- **Found: `--through` would skip a half-done copy.**  The restore row is
  done once `/etc/fstab` and `/etc/os-release` exist, and `--through` skips
  a done stage, so after a stop or an archiver failure a "Go on" or "Try
  again" with `--through close` would skip `restore` and boot a partial
  tree (J10).
- **Found: two commands write wherever they're told.**
  `recovery-key-file OUT` opens OUT with create and truncate, so OUT a
  device would take the key's 32 bytes; `report-bug -o DEV` writes a
  device as it is, by design.  Neither is a `--commit`.

### What the dump records that the screens want

- `host.hostname`, `host.machine_id`, `host.dmi.*` (`product_uuid` is
  `system.json`'s `dmi.system_uuid`), `os`, `firmware`, `generated` (UTC),
  `tool.version`.
- `storage.mounts`: `size_bytes`, `used_bytes`, `available_bytes` on Linux
  (statvfs, `df -Pk` on 32-bit).  The BSDs, illumos and Haiku record mounts
  with no size (`holt/src/other/`).  ZFS pool `allocated`/`free` are left
  out.  fit falls back to an ext superblock's free blocks.
- No keyboard: nothing in `holt/src` reads `vconsole.conf`,
  `/etc/default/keyboard` or any keymap file.
- No time zone: only `storage.sys_tz_minuteswest` (Linux's `sys_tz`, for
  FAT's `time_offset`).
- `load` reads the dump by `format` and `schema` (exactly 1) and by path, so
  new top-level keys are ignored (`layout.rs` `from_json`).

### rustic-bko's `--json-progress`, as bko-holt would see it

From the fork (`~/git/rustic`, 0.12.0+backotter.4,
`src/config/progress_options.rs`, `src/commands/restore.rs`):

- `--json-progress` (or `RUSTIC_JSON_PROGRESS`), which conflicts with
  `--no-progress` (load passes `--no-progress` today).  Only byte-counting
  progress bars are shown; counters are hidden.
- On standard output, one object a line, every `--progress-interval`
  (default 10 s for logging): `{"message_type":"status",
  "seconds_elapsed","seconds_remaining"?, "percent_done"?, "total_bytes"?,
  "bytes_done"?, "error_count"}`.  No phase name, no schema, no file count.
- Errors don't come there.  `JsonProgress::error` writes
  `{"message_type":"error", ...}` on standard **error**, and only the
  backup calls it (rustic_core `archiver.rs`): a restore never does, so
  its `error_count` stays 0.  A restore's problems are `warn!` lines on
  standard error, `[WARN] restore PATH: ...` (rustic_core
  `commands/restore.rs`), the lines the msdosfs wrapper already reads.
- Restore also prints text on standard output: `Files:  N to restore, ...`,
  `Dirs: ...`, `restore done.`.  Its log (`info!`) is on standard error.
- A restore stage may run the archiver many times (one per shared btrfs
  snapshot), each with its own progress from 0.

### Signals and locks

None.  bko-holt installs no signal handler and no panic hook: SIGTERM ends
it at once, leaving a command (mkfs, the archiver) running or killed with
it.  Its children share its process group, so a terminal's Ctrl-C reaches
them all, and SIGINT to load alone reaches none (an `sh -c` wrapper
doesn't pass it on).  Nothing stops two loads running at once on the same
RUN_DIR.

## Principles

1. **The text CLI doesn't change.**  OtterGrind1, HoltTest, BackShot and
   the Test2 families run bko-holt in text mode; their output, exit
   statuses and prompts stay as they are.  Everything new is under
   `--json`, a new command, or `BKO_HOLT_NO_COMMIT`, which no test rig
   sets.  Golden plans (`holt/tests/golden_plans.rs`, the corpus) are the
   check.
2. **The JSON run is the text run.**  `plan --json` is `plan::check`'s result
   in another form, and `load --json` runs the same commands.  Every way a
   `--json` run differs from the text run with the same arguments is in
   one list, "What differs under `--json`" below, and nowhere else; a
   difference not on it is a bug.
3. **Restore fidelity first.**  Nothing in the protocol changes what is
   restored: no mark is written into the restored tree, no file mode or
   owner of the restored system is touched, the restored
   `/var/log/bko-holt` holds what a text run's would (but the argv line),
   and the size choices (fit, grow) are the user's, shown with figures,
   never a default that loses the source's geometry silently.
4. **Secrets never travel in the protocol,** in argv, in the environment,
   in a document, an event or the log: key files by path only, a
   `private` write's text never, a repository URL with a password refused.
5. **Every document is complete or absent.**  A run under `--json` writes
   exactly one document, or one event stream from `start` to `end`, or one
   `bko-holt-error`; never a mix, never text.
6. **Fail closed.**  Whatever a reader doesn't know (a code, an enum value,
   a field it needs that's missing) stops the action it would lead to; it
   never skips a question or counts a stage done.
7. **Against a buggy front end, not a hostile one.**  bko-rescue runs as
   root and could `dd` a disk itself; bko-holt's checks (the erase gate,
   the plan hash, the dry-run lock, the key-file checks) are there so a
   bug in bko-rescue can't turn into a wiped disk, not to stop a front end
   that means harm.

### What differs under `--json`

The complete list (Principle 2).  Each is under `--json` only; text mode
keeps today's behaviour.

1. **The archiver's argv:** `--json-progress --progress-interval 1s` in
   place of `--no-progress`.
2. **No terminal:** every command's stdin is `/dev/null`, and load is
   started without a controlling terminal (bko-rescue's job: a new
   session, `setsid` or `systemd-run`), so `/dev/tty` can't be opened and a
   prompt fails at once.  The commands that would prompt are plan problems
   instead (`key_needed`, `password_needed`; GELI's `/dev/tty` prompt is
   never built).
3. **More plan problems,** each blocking: `key_needed`,
   `password_needed`, `key_file_unsafe`, `repo_has_password`,
   `erase_required`, `remake_after_restore`, `plan_changed`.
4. **Children in their own process group** each (`CommandExt::
   process_group(0)`), so the stop can signal the archiver inside an `sh
   -c` (J7).
5. **The restore stage is done only with its mark** (`restore.done`), in
   status's row and in `--through`'s skip (J10).  The copy marks are
   written in text mode too, where nothing reads them; under `--json` a
   mark that can't be written fails the run (exit 1), where text mode
   goes on without it.  A partition stage that runs removes both marks in
   either mode, and under `--json` `boot.done` as well (J10).
6. **A dry run's `boot` and `close`** print their commands instead of
   refusing `boot` for want of a restored tree (J11).
7. **The log** leaves out the archiver's progress lines (the last one
   kept), so the restored `/var/log/bko-holt` matches a text run's; a
   `private` write's text is logged as its size and SHA-256 only.
8. **The run records:** plan and status runs don't overwrite
   `RUN_DIR/last-load.json`; writing runs do, as now.
9. **SIGTERM is caught** (J7), and the core-dump limit is 0.
10. **The lock** `RUN_DIR/load.lock` is taken by writing runs (J8).

## Commands and flags

| Command | Writes on stdout | Exit statuses |
|---|---|---|
| `bko-holt --version --json` | `bko-holt-api` | 0 |
| `bko-holt load LAYOUT [--through plan] ... --json` | `bko-holt-plan` | 0, 2, 3, 4 |
| `bko-holt load LAYOUT --only status ... --json` | `bko-holt-status` | 0, 2, 3, 4 |
| `bko-holt load LAYOUT --through STAGE\|--only STAGE ... --json` | `bko-holt-events` | 0, 1, 2, 3, 4, 5 |
| `bko-holt load LAYOUT --only open ... --json` (RT10o) | `bko-holt-events` | as above |
| `bko-holt stick --json` (D11, RT10k) | `bko-holt-stick` | 0, 1, 2 |
| `bko-holt recovery-words OUT --json` (J21, RT10p) | `bko-holt-recovery` | 0, 1, 2 |
| `bko-holt report-bug ... --attach FILE` (RT10i) | text, as today | as today |

- **Where `--json` goes.**  For `load`, anywhere among load's arguments,
  like its other flags.  So that a bad argument is reported as a
  `bko-holt-error` too, a first pass walks load's argv with parse_args's
  own table of which flags take a value, so `--snapshot --json` names a
  snapshot called `--json` and isn't the flag; only a `--json` in a flag's
  place counts.  For the version, exactly `--version --json` or `-V
  --json`, handled in main.rs's `-V|--version` arm (main.rs dispatches on
  the first argument).  `stick` and `recovery-words` take it after the
  command.
- `--json` on `dump` is refused (exit 2, `json_not_here`): dump writes JSON
  already.  On `report-bug` it is refused the same way: the report is text
  for people.
- `--help` with `--json` prints the text help, exit 0: it is for people.
- New flags under this spec: `--json` (load), `--version --json`, `stick
  --json` (D11), `recovery-words` (J21), `--expect-plan SHA256` (J20),
  `--only open` (J14), `--fit SRC=PCT|fill` (J16), `report-bug --attach
  FILE`.  No flag changes meaning in text mode.

### Standard input, output and error

- **stdin:** never read by `load` under `--json`.  Every command load runs
  gets `/dev/null` as its standard input (today: the terminal's when the
  step has none), so a prompt nobody sees fails at once instead of
  hanging.  That alone doesn't stop a program that opens `/dev/tty` itself
  (GELI's prompt, rustic-bko's password prompt, ssh's host-key question):
  what does is that bko-rescue starts load in a new session with no
  controlling terminal (`systemd-run`, `setsid`), the dry run's direct
  child included.  *Check* at RT10f that rustic-bko's sftp passes ssh
  `BatchMode=yes`, so an unknown host key fails rather than waits.
  (`recovery-words` reads its words on stdin: a pipe from bko-rescue.)
- **stdout:** the document or the events, UTF-8, nothing else.  Children's
  output never reaches it: it goes to the log (`--commit`) or nowhere.
  Writes ignore errors (a full RAM disk, a reader gone), as the log's do:
  never `print!`, which panics on a closed stdout.
- **stderr:** what text mode would print there (warnings, the report-bug
  hint, `bko-holt load: ...` lines), for the Raw output view.  Never
  parsed: everything the screens act on is in the document.

### Exit statuses

Unchanged, plus one under `--json` only:

| Exit | Meaning | Under `--json` |
|---|---|---|
| 0 | done (plan: no problems) | as text |
| 1 | a stage failed; `--commit` not allowed here; another load is running (J8) | `failed`, then `end` |
| 2 | bad arguments, an unreadable layout, `BKO_HOLT_NO_COMMIT` refusing `--commit` | `bko-holt-error` |
| 3 | a layout load can't take (not JSON, another format or schema, unsupported OS, another architecture) | `bko-holt-error` |
| 4 | plan problems: nothing written | the plan with `problems`; for a writing run, `start`, `problems`, `end` |
| 5 | stopped on request (SIGTERM, J7) | `end` with `stopped: true` |
| 101 | bko-holt panicked (Rust's own status) | `failed` with `internal`, then `end` (the panic hook, new under `--json`) |

## Versions and schemas

- **How bko-rescue finds the version** (J1): it runs `bko-holt --version
  --json` once at start.  A bko-holt from before RT10 prints `bko-holt
  0.2.6` and exits 0 to `-V`, and to `--version --json` prints the same
  text (main.rs looks at the first argument only): not JSON, so
  bko-rescue says this stick's bko-holt is too old for it and offers the
  shell.  That matters because `rescue-bin` and `holt-bin` are pinned
  separately (D5).  bko-rescue resolves one absolute path to bko-holt at
  start and uses it for the version check and every later run
  (`systemd-run`'s `PATH` isn't the caller's, and `sudo bko-rescue
  --dry-run` runs on installed systems with their own `PATH`), and checks
  that every document's and `start`'s `version` equals the api
  document's.
- **Schemas:** every document carries `format` and `schema` (an integer).
  A schema goes up only when a field is removed, renamed or changes
  meaning.  Adding a field, an event, a code, or a value to an enum
  doesn't bump it; readers skip fields and events they don't know, and
  every unknown value they would act on fails closed (Principle 6):
  - a problem code blocks the restore (its text shown);
  - a `state` counts as not done;
  - a `keys[].unlock` kind blocks (never skips asking);
  - a `sizes[].verdict` can't be taken;
  - a `refused` code refuses the disk;
  - a command `kind` is shown as its text and counts as writing;
  - a `skip.state` is shown as skipped for an unknown reason, and the
    run's result is then not "Done" without a status read;
  - a `failed.code` gets the general help text.
- **Negotiation** (J2): bko-holt writes exactly one schema per format in
  version 1 of the protocol, and lists it in the api document.  bko-rescue
  checks before its first plan that every format it reads has a schema it
  knows, and again on every document (`start` included: the demo already
  does).  A mismatch stops it before `--commit` can be passed.  When a
  schema 2 exists, `--json=2` asks for it; plain `--json` means the oldest
  still written, so an older bko-rescue keeps working.

### `bko-holt-api`

```json
{"format": "bko-holt-api", "schema": 1, "version": "0.3.0",
 "documents": {"plan": [1], "status": [1], "events": [1], "stick": [1], "error": [1]},
 "features": ["no_commit_env", "stop", "progress", "candidates", "sizes",
              "open_stage", "fit_per_disk"],
 "platform": {"os": "linux", "arch": "x86_64"},
 "run_dir": "/run/bko-holt", "no_commit": false}
```

- `features` names what this bko-holt has of the later steps, so a screen
  that needs `fit_per_disk` or `open_stage` can say "not on this stick"
  instead of failing.  `no_commit` is whether `BKO_HOLT_NO_COMMIT` is in
  force for this process.

## The documents

Common rules:

- Every top-level document has `format`, `schema`, `version` (bko-holt's),
  `generated` (UTC, RFC 3339 with `Z`, as the dump's) and `dry_run` (true
  when `BKO_HOLT_NO_COMMIT` is in force).
- Sizes are bytes, as integers.  Kernel names (`sdb`) and paths
  (`/dev/disk/by-id/...`) are strings.  A field that isn't known is `null`,
  never left out, except where marked "optional".
- Every string that came from a disk or a program (models, labels, a
  command's stderr) is passed as it is, JSON-escaped.  bko-rescue strips
  control characters before showing any of it (`rescue-tui.md`,
  "Security"); bko-holt doesn't, so the log and the report see what was
  there.
- Every document goes through `redact::scrub_recovery_keys`, as text plans
  do.
- Hard limits for readers, `rescue-tui.md`'s ("Security"): a plan at most
  8 MiB, a status 64 KiB, an event line 64 KiB (`EVENT_LINE_MAX`, the
  demo's).  bko-holt keeps under them: in events, any argv element or
  `stdin` over 16 KiB (the shared snapshots' `sh -c` scripts can be
  bigger) is given as `{"bytes": N, "sha256": "…"}` in its place, the full
  text being in the plan and the log; a test over the corpus checks the
  largest plan stays under 8 MiB.
- A `private` write (`Cmd::private`: LILO's copy of `lilo.conf`, which can
  hold `password=`) never has its text in a document, an event or, under
  `--json`, the log: `stdin` is null and `stdin_bytes` and `stdin_sha256`
  stand for it; the plan's `text` shows `# (N bytes, private: not
  shown)` in its heredoc's place.

### `bko-holt-error`

The whole of stdout when a run can't produce its document: bad arguments,
an unreadable or refused layout, `BKO_HOLT_NO_COMMIT`, an unknown schema
asked for.

```json
{"format": "bko-holt-error", "schema": 1, "version": "0.3.0",
 "generated": "2026-11-24T10:00:00Z", "dry_run": true,
 "exit": 2, "code": "no_commit",
 "text": "bko-holt load: BKO_HOLT_NO_COMMIT=1 is set (a dry run), so --commit is refused; nothing was written to any disk"}
```

`code` is one of `bad_args`, `no_commit`, `layout_unreadable`,
`layout_not_json`, `layout_refused` (another format or schema, an
unsupported OS, a dump made without root), `arch_mismatch`,
`schema_unknown` (a `--json=N` this bko-holt doesn't write),
`json_not_here` (`--json` on a command that doesn't take it: `dump`,
`report-bug`).  `text` is the line text mode prints on stderr, without the
usage that follows it.  A reader following an events file must take a
first line with `format` `bko-holt-error` as the whole stream and show its
`text` (the demo fails it today as "an event with no \"event\"").

### `bko-holt-plan`

`load LAYOUT --json` with any of load's flags, `--commit` and `--erase`
included (they change nothing a plan does but `--erase`'s checks).  The
same checks as `plan::check`, in one document.

```json
{"format": "bko-holt-plan", "schema": 1, "version": "0.3.0",
 "generated": "2026-11-24T10:00:00Z", "dry_run": false,
 "ok": false, "exit": 4, "plan_sha256": "51d0…",
 "layout": {"path": "/run/bko-rescue/layout.json", "sha256": "9f2c…"},
 "args": ["load", "/run/bko-rescue/layout.json", "--map", "sda=/dev/disk/by-id/nvme-CT1000P3PSSD8_2318E6F3A9C1", "…", "--json"],
 "source": {"hostname": "lovelace", "os": {"id": "debian", "version_id": "13", "name": "Debian GNU/Linux 13 (trixie)"},
            "family": "linux", "arch": "x86_64", "boot_mode": "uefi", "loader": "grub",
            "generated": "2026-11-22T01:00:11Z", "dmi_uuid": "4c4c4544-…",
            "reformat_possible": true},
 "here": {"dmi_uuid": "5a6b…", "boot_mode": "uefi", "efivars": true, "arch": "x86_64",
          "privileged": true, "same_machine": false},
 "disks": [
   {"source": {"name": "sda", "size_bytes": 500107862016, "model": "Samsung SSD 870 EVO 500GB",
               "serial": "S6PNNX0T123456A", "wwid": "eui.…", "sector": 512, "table": "gpt",
               "mounts": ["/", "/boot/efi"],
               "largest": {"partition": "sda3", "size_bytes": 499000000000, "holds": "LUKS2, ext4",
                           "mount": "/", "used_bytes": 41700000000}},
    "target": {"node": "nvme0n1", "path": "/dev/nvme0n1",
               "by_id": "/dev/disk/by-id/nvme-CT1000P3PSSD8_2318E6F3A9C1",
               "size_bytes": 1000204886016, "model": "CT1000P3PSSD8", "serial": "2318E6F3A9C1",
               "wwid": "eui.…", "sector": 512, "transport": "nvme", "removable": false,
               "matched_by": "map", "same_identity": false, "blank": true, "contents": [],
               "ours": false, "recorded": false, "erase": true,
               "will_erase": true, "writes": ["partition", "assemble", "format"],
               "size": {"verdict": "bigger", "change_pct": 107, "…": "see Disks of another size"}}}
 ],
 "candidates": [
   {"node": "nvme0n1", "target_of": "sda", "…": "as below, and as disks[].target"},
   {"node": "sdc", "path": "/dev/sdc", "by_id": "/dev/disk/by-id/usb-SanDisk_Ultra_4C53…",
    "size_bytes": 32015679488, "model": "SanDisk Ultra", "serial": "4C530001…", "wwid": null,
    "serial_shared": false, "sector": 512, "transport": "usb", "removable": true, "target_of": null,
    "ours": false,
    "blank": false, "contents": [{"dev": "sdc3", "type": "vfat", "label": "BACKOTTER", "size_bytes": 268435456, "used_bytes": null}],
    "refused": {"code": "boot_disk", "text": "sdc is the disk this system booted from; never a target"},
    "erase_needed": false, "sizes": []},
   {"node": "sdb", "path": "/dev/sdb", "by_id": "/dev/disk/by-id/ata-KINGSTON_SA400S37480G_50026B77…",
    "size_bytes": 480103981056, "model": "KINGSTON SA400S37480G", "serial": "50026B7784A1B2C3",
    "wwid": null, "serial_shared": false, "sector": 512, "transport": "sata", "removable": false,
    "target_of": null, "ours": false,
    "blank": false, "contents": [{"dev": "sdb1", "type": "ext4", "label": "photos", "size_bytes": 480102000000, "used_bytes": 212000000000}],
    "refused": null, "erase_needed": true,
    "sizes": [{"source": "sda", "verdict": "smaller", "fit_pct": 5, "change_pct": 4.3,
               "partition": "sda3", "mount": "/", "old_bytes": 499000000000, "new_bytes": 477500000000,
               "used_bytes": 41700000000, "margin_bytes": 2085000000, "needed_bytes": 43785000000,
               "most_bytes": 477500000000, "data_fits": true, "reason": null, "why": null}]}
 ],
 "erase": ["/dev/disk/by-id/nvme-CT1000P3PSSD8_2318E6F3A9C1"],
 "keys": [
   {"group": "cryptroot", "kind": "luks", "names": ["cryptroot", "cryptsrv"],
    "devices": ["sda3", "sdb1"], "holds": ["/", "/srv"],
    "unlock": "passphrase", "given": false, "new_ok": true,
    "min_bytes": 1, "max_bytes": 512, "shared_at_boot": true}
 ],
 "tokens": [{"container": "cryptroot", "kind": "systemd-tpm2"}],
 "reformat": [{"mount": "/srv", "type": "ext4", "to": {"xfs": null, "btrfs": null}},
              {"mount": "/", "type": "ext4", "to": {"xfs": {"code": "reformat_refused", "reason": "grub_reads", "text": "…"}}}],
 "fit": [],
 "problems": [
   {"code": "key_needed", "text": "cryptroot: give its new passphrase with --key-file cryptroot=FILE", "name": "cryptroot", "group": "cryptroot"}
 ],
 "notes": [
   {"code": "target_bigger", "text": "sda -> /dev/nvme0n1: the target is bigger; the extra 500 GB stays unused",
    "disk": "sda", "target": "nvme0n1", "bytes": 500097024000}
 ],
 "stages": [
   {"name": "partition", "commands": [
     {"kind": "run", "argv": ["wipefs", "--all", "/dev/nvme0n1"], "env": [["LC_ALL", "C"]], "why": "sda -> /dev/nvme0n1: clear the old table and signatures"},
     {"kind": "run", "argv": ["sfdisk", "--wipe", "always", "/dev/nvme0n1"], "env": [["LC_ALL", "C"]], "why": "…",
      "stdin": "label: gpt\n…", "stdin_bytes": 412, "private": false}
   ]},
   {"name": "assemble", "commands": []}
 ],
 "text": "# bko-holt load plan for /run/bko-rescue/layout.json\n…"}
```

Field by field:

- `ok`, `exit`: no problems, and the exit status the run ends with (0 or
  4).
- `plan_sha256` (J20): SHA-256 over a canonical form of what the run would
  do and to what: `stages` (every command's kind, argv, env, path, dev,
  and stdin's SHA-256), and each target's node, `by_id`, size, kernel
  serial and WWID (as the partition record reads them from sysfs).  Not
  over `generated`, `text`, notes or candidates.  The writing run is
  given it as `--expect-plan` and recomputes it before its first command.
- `args`: the argument list as given, so Review can show it and Restore
  can be checked to be the same list with `--through close --commit`
  and `--expect-plan` added (`rescue-screens.md`, "The full argument
  list").  Paths only, no secrets (J3); a `--repo` URL with a user's
  password in it is refused under `--json` (`repo_has_password`), so it
  never reaches `args`, the log or `last-load.json`.
- `source`: from the dump.  `family` is `linux`, or `crate::other`'s
  family name (`freebsd`, `openbsd`, `illumos`, `haiku`, ...).
  `reformat_possible` is false where `--reformat` isn't built (not Linux;
  ostree, ABRoot, NixOS, Guix), so Review doesn't draw < Advanced >.
- `here`: this machine.  `dmi_uuid` read as the dump reads it;
  `same_machine` when it equals the source's.  `efivars`: UEFI variables
  are writable here (Failure's "Install without an entry" text, and the
  BIOS-mode advice).  *Check* the non-Linux readings at RT10c.
- `disks`: one per used source disk, in the dump's order.  `source.mounts`
  lists the mount points on it; `largest` is the partition `--fit-largest`
  would change (fit.rs's choice), with what it holds and the space used,
  or null where fit can't choose one.  `target` is null when the disk has
  none.  `matched_by` is `map`, `wwid` or `serial` (how map_disks found
  it), and `same_identity` whether the target is the source disk itself
  by WWID, or serial and model (target.rs `same_identity`), whatever
  matched it, so the overview marks disks matched by identity.  `erase`:
  named with `--erase`.  `contents`: what's on it now (below).  `size`: the
  size verdict for this pair (below), or null for the same size.
- `ours`: the target carries this layout's own identifiers
  (`TargetDisk::is_ours`).  True for a disk an earlier stage wrote and
  for the source's own disk as found alike; never read alone as "load's
  work" (J19).
- `recorded`: this session's partition record
  (`RUN_DIR/partitioned/NODE`) shows load wrote this layout's table there
  (`target::load_wrote`).  A reboot forgets it.
- `will_erase`, `writes` (J19): whether this run's `partition` stage wipes
  the target, and which stages would make something anew on it
  (`partition`, `assemble`'s creates, `format`), as exec would run them
  with `--through close`.  Review's ERASED list is the targets with
  `will_erase`, taken from here, never worked out by bko-rescue.
- `candidates`: every whole disk here, the plan's targets included (on
  the same machine, map_disks matches the old disks by WWID or serial
  without `--map`, and they must still be listed and sized).  `target_of`
  names the source disk a candidate is the target of in this plan, or
  null.  `serial_shared`: another disk here reports the same serial (some
  USB bridges give one serial to every disk), so a typed serial can't
  tell them apart and Review asks for the size and model too.
  `refused` is null, or a code and text:
  `boot_disk` (the stick), `running_root`, `in_use` (mounted or swap, so
  refused even with `--erase`), `backup_drive` (holds the mount the
  local `--repo` is under: new, a special case of `in_use` with its own
  words), `read_only`, `sector_size` (against every source disk).  A held
  disk or one that isn't blank isn't refused: `erase_needed` says choosing
  it means `--erase`.  Under J19 that is every disk that isn't blank and
  has no record, `ours` included.  `sizes` has one verdict per used source disk, so
  the disk list for old disk 1 reads its row from `source: "sda"`.  Size
  is never a disk-level refusal: a disk too small for one old disk may
  suit another.
- `contents`: each signature the probes find, structured
  (`target::signatures_of` already reads them as text): `dev`, `type`,
  `label`, `size_bytes`, and `used_bytes` where the superblock says
  without mounting (ext's free blocks; XFS's and btrfs's counters,
  *Check* at RT10c which probes read them), else null (J13).
  bko-holt never mounts a candidate.
- `by_id`: on Linux, the disk's most stable `/dev/disk/by-id` link (`wwn-`
  first, then the transport's with the serial); bko-rescue passes it to
  `--map` and `--erase`, so a kernel name that moves between runs can't
  move a mapping.  Null where there is none.  *Check* at RT10c what each
  other platform offers (`/dev/diskid/`, `/dev/gpt/`, illumos's
  `c0t…d0` names, which are already stable); `path` is used there.
- `erase`: the targets named with `--erase`, as given.
- `keys`: one entry per question a person answers, grouped as `layload.md`,
  "Keys follow the original system", says: containers sharing one
  passphrase at boot (crypttab `keyscript=decrypt_keyctl` with one key
  name) are one group; every other container or encryption root is its
  own.  *Check* at RT10c: today's code makes no groups, so this is new
  grouping, with each name still taking its own `--key-file`.  Fields:
  `kind` (`luks`, `luks_kept_header`, `zfs`, `geli`, `softraid`), `names`
  (each a NAME `--key-file NAME=FILE` takes), `devices` (source names),
  `holds` (mount points under it), `unlock`:
  - `passphrase`: a new passphrase to ask for;
  - `old_passphrase`: only a key the container already has opens it
    (a kept header: its passphrase or, on Ubuntu Core, the recovery key
    through `recovery-key-file`); `new_ok` is then false;
  - `temporary`: the source unlocked it with a key file the restore
    brings back; load makes a temporary key itself, nothing to ask;
  - `key_file_elsewhere`: the source's key file is on another device or
    outside the tree; a key file must be given (`key_file_unrestorable`).

  `given`: a `--key-file` covers it.  `min_bytes` and `max_bytes`: what the
  tool takes and what the restored system's start-up prompt can take back
  (ZFS passphrases 8 to 512; cryptsetup reads a key file of up to 8 MiB,
  but its prompt takes 512 characters, so 512; *Check* GELI's and
  bioctl's at RT10c); bko-rescue's own cap stays 1 KiB.
  `shared_at_boot`: the source asked once for the group.
- `tokens`: TPM2, FIDO2, clevis and other LUKS2 tokens and ZFS or GELI
  equivalents not restored, to enrol again (Done screen).
- `reformat`: for each mount point `--reformat` could name, the types it
  could become and, per type, null (allowed) or the refusal it would get,
  each worked out by `reformat::work_out` as a trial (pure, no disk read).
  Advanced shows "can't change" and the reason without a plan per press.
  Empty where `reformat_possible` is false.
- `fit`: with `--fit-largest` or `--fit` (J16), per disk what
  `Fit::report` prints, structured: disk, target, old and new size,
  partition, old and new sectors, moved partitions, and the data-fit
  figures.  Review's "Changes:" lines come from here.
- `problems`, `notes`: below ("Codes").
- `stages`: what the writing stages would run, in order, from
  `Built.stages`.  Each command's `kind`: `run` (argv, env, optional
  stdin), `write` (a file load writes: `path`, its text as `stdin`),
  `check` (a read-back load makes in-process: `dev`), `note` (`why` only).
  `env` lists `LC_ALL=C` first, which run_cmd adds to every command, then
  the step's own.  `private`: a write whose text is never shown (above).
  `archiver`: true on each command that runs the archiver, directly or
  inside a script.  The argv is the `--json` run's (`--json-progress` in
  the archiver's).
- `text` (J18): what text mode's `bko-holt load` prints for these same
  arguments without `--json`, byte for byte, so Commands (`c`) shows what
  an administrator's plan would print, without a second run.  So it has
  the archiver's `--no-progress` (the run's argv is in `stages`), none of
  the `--json`-only problems (they are in `problems`), and a `private`
  write's text replaced as above.  The golden JSON check holds `text`
  equal to `plan.txt` with only that replacement.

### `bko-holt-status`

`load LAYOUT --only status --json` with the run's `--map` (and keys, so
names resolve): `status::rows`, structured.

```json
{"format": "bko-holt-status", "schema": 1, "version": "0.3.0",
 "generated": "2026-11-24T11:20:00Z", "dry_run": false, "exit": 0,
 "target": "/mnt/bko-target",
 "stages": [
   {"name": "partition", "state": "done", "why": ""},
   {"name": "assemble", "state": "not_done", "why": "missing: /dev/mapper/cryptroot"},
   {"name": "format", "state": "unknown", "why": ""},
   {"name": "mount", "state": "not_done", "why": "not mounted: /mnt/bko-target"},
   {"name": "restore", "state": "unknown", "why": "the tree isn't mounted, so this can't be seen"},
   {"name": "boot", "state": "unknown", "why": "the tree isn't mounted, so this can't be seen"},
   {"name": "close", "state": "not_done", "why": "not made: …"}
 ],
 "next": "assemble", "all_done": false,
 "marks": {"partitioned": ["nvme0n1"], "restore_started": true, "restore_complete": false, "boot": false},
 "targets": [{"source": "sda", "node": "nvme0n1", "ours": true, "same_identity": false, "recorded": true}],
 "temporary_keys": [{"name": "cryptsrv", "kept": true}],
 "resume": {"go_on": false, "why": ["assemble would make cryptroot anew: the open stage isn't in this bko-holt"]},
 "open": {"containers": [{"source": "dm-1", "node": "/dev/mapper/cryptsrv"}], "pools": [], "mounted": []},
 "problems": []}
```

- `state`: `done`, `partly`, `not_done`, `nothing`, `unknown` (the words
  of `State::word`, snake case).  `why` as text mode prints it.
- `next`: the stage text mode names after `next:`, or null with
  `all_done` true ("nothing; every stage is done").
- `marks`: what this RAM session recorded: the partition stage's table
  records, the copy's start and complete marks (new, J10) and
  `boot.done`.  All are in RUN_DIR, so a reboot forgets them.
- `targets`: per target, `ours` (`TargetDisk::is_ours`), `same_identity`
  and `recorded`, as the plan's.  `ours` alone is no evidence of load's
  work: the source machine's own disk is "ours" too.
- `temporary_keys`: the containers whose only key is one load made
  (`RUN_DIR/keys/NAME`, `unlock: temporary`), and whether that file is
  still there.  A reboot loses them; until `boot` has added the restored
  key file, those containers can't be opened again.
- `resume` (J14): whether "Go on" can be offered, with every reason it
  can't.  `go_on` is true only when every target is `recorded`, or (with
  the `open` stage, RT10o) `ours` and not `same_identity`; and no
  temporary key is lost before `boot` ran; and no stage `--through` would
  run next makes something anew over this session's restore
  (`remake_after_restore`).  Otherwise the Resume screen offers only Start
  again: `--only close` if anything is open, then `--only partition` with
  every target in `--erase` (it wipes, and clears the copy marks), then
  `--through close`, `--erase` kept.  bko-rescue never works this out
  itself.
- `open`: what of the layout is open now (containers, imported pools,
  mounts under the target), for Resume and for "Shell" on Failure.
  `containers`: `{source, node}` objects, each device assemble makes
  (its name in the layout, `dm-1`) and the node it's open as here
  (`/dev/mapper/cryptsrv`); `pools` and `mounted`: strings (pool names,
  mount points).
- `problems`: the mapping problems status reports today (not-blank left
  out, as now); with any, exit 4 and `stages` empty, as text mode prints
  no rows.
- Restore's row (J10): under `--json`, `done` only when the tree shows it
  as today (fstab and os-release, or the family's marker) **and**
  `restore.done` is there; with the tree's files and no mark it is
  `partly`, why "the copy didn't finish in this session" (`marks` says
  whether it started).  `--through` under `--json`
  skips by this same row, so a stopped or failed copy always runs again.
  Text mode's row is unchanged.

### `bko-holt-events`

`load LAYOUT --through STAGE|--only STAGE ... --json`.  One object a line,
each written and flushed as it happens.  The order: `start` first; per
stage `stage` or `skip`; inside a stage `cmd`, then `progress` and
`warning` while it runs, then `cmd_done`; `stage_done` or `failed`; `end`
last.

```text
{"event":"start","format":"bko-holt-events","schema":1,"version":"0.3.0","generated":"2026-11-24T11:21:00Z","pid":4711,"commit":true,"dry_run":false,"stages":["partition","assemble","format","mount","restore","boot","close"],"stage_commands":{"partition":6,"assemble":4,"format":7,"mount":9,"restore":3,"boot":21,"close":8},"plan_sha256":"51d0…","log":"/run/bko-holt/log/load.log","layout":{"hostname":"lovelace","generated":"2026-11-22T01:00:11Z"}}
{"event":"stage","stage":"partition","commands":6}
{"event":"cmd","stage":"partition","n":1,"of":6,"kind":"run","argv":["wipefs","--all","/dev/nvme0n1"],"env":[["LC_ALL","C"]],"why":"sda -> /dev/nvme0n1: clear the old table and signatures"}
{"event":"cmd_done","stage":"partition","n":1,"status":0,"signal":null,"secs":0.04}
{"event":"stage_done","stage":"partition","secs":2.1,"checked":true}
{"event":"skip","stage":"assemble","state":"nothing","why":""}
{"event":"stage","stage":"restore","commands":3}
{"event":"cmd","stage":"restore","n":3,"of":3,"kind":"run","argv":["rustic-bko","--repository","sftp://nas.local/backotter/lovelace","--password-file","/run/bko-rescue/k/1","--json-progress","--progress-interval","1s","restore","--numeric-id","a7f3c901","/mnt/bko-target"],"env":[["LC_ALL","C"],["RUSTIC_STRICT_EXIT","true"]],"why":"snapshot a7f3c901 of sftp://…","archiver":{"part":1,"parts":1}}
{"event":"progress","stage":"restore","n":3,"part":1,"parts":1,"bytes":4113000448,"total_bytes":61203000000,"secs":82,"errors":0}
{"event":"warning","stage":"restore","n":3,"source":"archiver","text":"restore /var/lib/x: setting extended attributes failed: …","item":"/var/lib/x"}
{"event":"cmd_done","stage":"restore","n":3,"status":0,"signal":null,"secs":1042.7}
{"event":"stage_done","stage":"restore","secs":1043.0,"checked":true}
{"event":"failed","stage":"boot","n":7,"code":"cmd_failed","program":"grub-install","argv":["grub-install","…"],"status":1,"signal":null,"text":"grub-install failed (exit status: 1):\n    EFI variables are not supported on this system.","tail":["EFI variables are not supported on this system."],"log":"/run/bko-holt/log/load.log"}
{"event":"end","exit":1,"stopped":false,"secs":1101.4}
```

Every event has `event`.  The rest:

| Event | Fields | Notes |
|---|---|---|
| `start` | `format`, `schema`, `version`, `generated`, `pid`, `commit`, `dry_run`, `stages` (names, strings, as the demo reads them), `stage_commands` (name to count), `plan_sha256` (this run's, J20), `log` (path, or null without `--commit`), `layout` (`hostname`, `generated`) | Always first.  `pid` is load's own, for a restarted bko-rescue to find it alive; the stop request takes the pid from the lock (J8).  In a dry run bko-rescue stops at once unless `dry_run` is true and `commit` false. |
| `problems` | `problems` (as the plan's), `exit` 4 | Only when the checks found problems: nothing ran.  Then `end`. |
| `stage` | `stage`, `commands` | A stage begins. |
| `skip` | `stage`, `state` (`done`, `nothing`), `why` | `--through` skipped it as text mode does ("done already"), by status's rows as `--json` reads them (restore needs its mark, J10). |
| `cmd` | `stage`, `n`, `of` (within the stage), `kind`, `argv`, `env` (pairs, `LC_ALL=C` first), `why`, `path` (write), `dev` (check), `stdin_bytes`, `private`, `archiver` (`part`, `parts`, on a command tagged as running the archiver) | Without `--commit`, the same with `would: true` (false with it), and no `cmd_done`.  Long argv elements as in "Common rules". |
| `progress` | `stage`, `n`, `part`, `parts`, `bytes`, `total_bytes` (null until known), `secs`, `errors` | From the archiver's `status` lines on its stdout (through an `sh -c` too), at most once a second (the last one always).  `errors` counts the `warning` events of this command so far (rustic's own `error_count` is 0 in a restore). |
| `warning` | `stage`, `n`, `source` (`archiver`, `cmd`), `text`, `item` (archiver only) | The archiver's `[WARN] restore PATH: ...` lines on its stderr (colour codes stripped, `item` the PATH); a `warns` command's `warning:` lines, which text mode prints on stderr. |
| `cmd_done` | `stage`, `n`, `status` (exit code, or null), `signal` (or null), `secs` | |
| `stage_done` | `stage`, `secs`, `checked` | `checked`: after the read-back showed it done; false in a dry run, which reads nothing back. |
| `stopping` | `signal` | SIGTERM was taken; the run ends after the command now running (J7). |
| `failed` | `stage`, `n` (or null), `code`, `program`, `argv` (both null when no step failed: the lock, a mark), `status`, `signal`, `text` (as text mode's stderr line), `tail` (the last 5 lines of the command's stderr, as now), `log` | Then `end`.  Codes below. |
| `end` | `exit`, `stopped`, `secs` | Always last, also after a failure, a stop, problems or a panic (a panic hook writes `failed` with `internal`, then `end` with 101). |

- Only SIGKILL, power loss or a write error on stdout can leave a stream
  without `end`; bko-rescue then reads the exit status (Linux:
  `systemctl show -p ExecMainStatus`; elsewhere the wait or the run's lock)
  and says "bko-holt ended without its end event", as the demo does.
- A `progress` event is at most ~200 bytes, at most one a second: a
  20-hour copy writes ~14 MiB of events.  *Check* at RT10f against the
  stick's RAM (rescue-tui's follower reads 4 MiB a tick).
- `n`/`of` count within the stage, not the run: the stages a `--through`
  run skips are known only when reached (status is read per stage).  The
  fake's run-wide `n`/`of` change with it (a meaning change at schema 1,
  allowed while the stream is provisional).
- `archiver.parts` is the number of commands in the stage tagged as
  running the archiver: an upper bound, since a shared snapshot's script
  runs it on one branch only; a part that doesn't run it shows no
  progress, and the next part's `progress` restarts the bar.

### `bko-holt-stick` (D11, RT10k)

`bko-holt stick --json`: the config partition of the disk this system
booted from, read the way `stick-config.md`'s "Finding the config at
boot" says (the boot disk only, never a scan for the label), mounted
nowhere (read through the FAT code bko-holt has, *Check* that it reads
directories and files, not only boot sectors, at RT10k).

```json
{"format": "bko-holt-stick", "schema": 1, "version": "0.3.0", "generated": "…", "dry_run": false,
 "boot_disk": {"node": "sdc", "found_by": "cdrom_mount"},
 "config": {"state": "ok", "partition": 3, "label": "BACKOTTER", "why": null},
 "stick": {"stick_id": "…", "name": "drawer stick"},
 "here": {"dmi_uuid": "5a6b…"},
 "machines": [
   {"install_id": "0b7e…", "path": "backotter/machines/0b7e…/system.json",
    "state": "ok", "why": null, "schema": 1, "known_schema": true,
    "hostname": "lovelace", "os": {"id": "debian", "version_id": "13"},
    "written": "2026-11-22T01:00:11Z", "sequence": 42, "dmi_uuid": "4c4c4544-…",
    "this_computer": false,
    "repository": {"kind": "sftp", "url": "sftp://nas.local/backotter/lovelace"},
    "repository_id": "a3f9…", "keyboard": {"…": "the dump's section"}, "time": {"…": "the dump's section"},
    "password_hint": null}
 ]}
```

- `config.state`: `ok`, `missing`, `damaged` (fsck-level), `two_sticks`
  (illumos: two disks match the marker).  `machines[].state`: `ok`,
  `checksum_bad`, `checksum_missing`, `schema_unknown`, `unreadable`; any
  but `ok` can't be chosen.
- `settings.toml` overrides need a TOML reader in bko-holt (rescue-tui's
  open question 4).  Until that's decided, `stick --json` ignores
  `settings.toml` and says so in a `notes` entry.
- The layout itself isn't in the document: bko-rescue copies
  `system.json` to `/run/bko-rescue/layout.json` for load (*Check* how
  `system.json` embeds the dump: the backup tool isn't built).
- `password_hint` is text the user wrote and may name things a report
  shouldn't hold: bko-rescue gives it to report-bug as text to strip
  (`--strip-file`, RT10i: a file of strings the Redactor replaces as
  "other identifiers"), and never writes it into its own log or a stick
  document.

### `bko-holt-recovery` (J21, RT10p)

`bko-holt recovery-words OUT --json`, the typed words on stdin (a pipe
from bko-rescue, at most 1 KiB): the recovery key screen's check, by
`recovery.rs`'s `check`, and the repository password it stands for, by
`recovery::password` (a compatibility contract that stays in holt).

```json
{"format": "bko-holt-recovery", "schema": 1, "version": "0.3.0", "generated": "…", "dry_run": false,
 "ok": false, "words": 12, "unknown": [{"position": 7, "suggest": ["absorb", "abstract"]}],
 "checksum_ok": null, "written": false}
```

- With `ok`, the password goes to OUT: a new file (`O_CREAT|O_EXCL`,
  0600) in a directory of bko-holt's effective user with no group or other
  bits (bko-rescue's `k/`), no trailing newline; `written` true.  Without
  `ok`, nothing is written.  The words and the password never appear in
  the document.  `suggest` is at most three list words, by edit distance,
  only for a word not on the list.
- Allowed under `BKO_HOLT_NO_COMMIT` (it writes no disk).
- Ubuntu Core's `old_passphrase` path is `recovery-key-file OUT` as it is:
  the key on its stdin, a pipe from bko-rescue, and OUT a new file in the
  same `k/` directory; under `BKO_HOLT_NO_COMMIT` it refuses an OUT that
  exists (below).

## Secrets

Passphrases, key files and the repository password never appear in argv,
in the environment, in a document or in an event.  Only their paths do.

**How they travel** (J3, recommended):

1. bko-rescue makes a new directory in RAM for the run, mode 0700, its
   own (`/run/bko-rescue/k/` on Linux, `/var/run/bko-rescue/k/` on the
   BSDs and illumos; Haiku: a RAM file system it mounts, open question 1),
   checking the directory is new and its own, not one already there
   (RT8's finding on `secret::ram_dir`).
2. Each secret goes to a file there: created with `O_CREAT|O_EXCL`, mode
   0600, the bytes as typed, no trailing newline, at most 1 KiB, never
   empty.  One file per group; the group's names all point to it
   (`--key-file cryptroot=/run/bko-rescue/k/0 --key-file
   cryptsrv=/run/bko-rescue/k/0`).
3. bko-rescue passes `--key-file NAME=PATH` and `--password-file PATH`.
4. **Under `--json`, bko-holt checks each such file** among the plan's
   checks (after the arguments and the layout are read, before any
   command): an absolute path, a regular file (lstat, not a symlink),
   owned by bko-holt's effective user, no group or other permission bits,
   in a directory owned by that user with no group or other bits, not
   empty, at most 8 MiB, and for a group whose `unlock` is a passphrase,
   not ending in a newline (cryptsetup takes it as part of the passphrase;
   bioctl's copy adds its own).  The newline check reads the last byte
   alone (`pread` at size - 1), so bko-holt never holds a key.  A file
   that fails is a problem, `key_file_unsafe`, naming the check, not the
   content.  Text mode keeps today's behaviour (no check).
5. bko-holt reads none of them itself, except the copies it already makes
   (softraid's `bioctl -p` copy, GELI's): they stay in `RUN_DIR/keys`,
   0600, and are removed by the stages that remove them today.
6. **Wiping:** bko-rescue keeps the files for the whole restore (a resume
   or Try again runs load several times) and removes them when the session
   ends: Done, power off, the shell's exit, Cancel, a caught signal or a
   panic (rescue-tui's `Secret` and the demo's Run already do this).  Each
   is overwritten with zeros to its length, then unlinked; the directory
   last.  A crash leaves them in RAM, which a reboot clears; bko-rescue
   removes a stale directory of its own at start.
7. **bko-holt's own temporary keys** (`RUN_DIR/keys/NAME`, made for
   containers the source unlocked by key file) are the only key those
   containers have until `boot` adds the restored key file.  Nothing in
   this spec removes them early: a failed run keeps them, so a Try again
   can open what the first run made.  A reboot loses them (RUN_DIR is a
   tmpfs), so status lists them (`temporary_keys`) and Resume then offers
   only Start again (J14).
8. **The environment:** bko-rescue starts bko-holt with a small, known
   environment (`PATH`, `LANG`/`LC_ALL`, `BKO_HOLT_NO_COMMIT` when
   dry-running), never its own, so a `RUSTIC_PASSWORD` set somewhere can't
   reach the archiver.  Through `systemd-run` that means `--setenv` for
   each (a unit doesn't inherit the caller's environment).  bko-holt
   itself doesn't scrub the environment (an administrator may rely on it
   in text mode).
9. **The repository URL:** a `--repo` with a password in it
   (`rest:https://user:pass@…`, `sftp://user:pass@…`) is refused under
   `--json`, `repo_has_password`: the password goes in the password file.
10. **Core dumps:** under `--json` bko-holt sets `RLIMIT_CORE` to 0 for
    itself and its children (cryptsetup and the archiver hold keys), and
    bko-rescue's unit has `-p LimitCORE=0`.
11. **Files written where they're told** (`recovery-key-file OUT`,
    `report-bug -o OUT`): bko-rescue always names a new file in its `k/`
    or its run directory; under `BKO_HOLT_NO_COMMIT` both refuse an OUT
    that exists (`O_EXCL` only), so a device can't be one.  Without the
    variable, text mode is as today.

Why not a file descriptor or a pipe: `load` runs detached (`systemd-run`
on Linux, `setsid` elsewhere: `ui.md`, "The restore outlives the UI"),
and `systemd-run` hands a service no descriptors but its standard
streams (*Check* `--pipe`'s behaviour with a detached unit); every tool
load runs (cryptsetup, geli, bioctl, zpool's `keylocation=file://`,
rustic-bko) wants a file anyway, so bko-holt would write the same file
itself; and a resume needs the secret again in a later process.  J3 lists
`--key-fd` as the alternative.

## Codes

Problems, notes, refusals and failures each get a stable `code`, beside
the text they have today (unchanged, so text mode's output is the same).
In the code, `problems: Vec<String>` becomes `Vec<Problem>` (`code`,
`text`, subject fields), with `Display` giving the text and a
`From<String>` giving `other`, so the ~250 sites move over one by one and
every untouched site still compiles and prints the same (J4).  The codes
live in one file, `holt/src/load/codes.rs`, and a test checks this table
lists every one.

**Subject fields,** when the problem is about one: `disk` (source),
`target` (node), `name` (a key name), `group`, `mount`, `program`,
`stage`, and the figures named below.

### Plan problems (exit 4)

| Code | From | Fields | Screen and fix |
|---|---|---|---|
| `map_unknown_source` | target.rs `map_disks` | `disk` | internal: bko-rescue built a bad `--map` |
| `no_such_disk` | target.rs `map_disks` | `disk`, `target` | the disk went away: back to Disks |
| `not_mapped` | target.rs `map_disks` | `disk` | back to Disks |
| `target_twice` | target.rs `map_disks` | `disk`, `target` | back to Disks |
| `boot_disk` | target.rs `check_target` | `disk`, `target` | another disk |
| `running_root` | target.rs `check_target` | `disk`, `target` | another disk |
| `target_too_small` | target.rs `check_target` (smaller, no fit flag) | `disk`, `target`, `target_bytes`, `source_bytes` | the size offer (`sizes`) |
| `sector_size` | target.rs `check_target` | `disk`, `target`, `here`, `source` | another disk |
| `read_only` | target.rs `check_target` | `disk`, `target` | another disk |
| `in_use` | target.rs `check_target` (mounted, swap) | `disk`, `target`, `uses` | another disk, or the shell |
| `held` | target.rs `check_target` (without `--erase`) | `disk`, `target`, `uses` | `--erase` (Review's typed serial) |
| `not_blank` | target.rs `check_target` | `disk`, `target`, `contents` | `--erase` (Review's typed serial) |
| `held_unreleasable` | target.rs `release_holders` | `disk`, `target`, `why` | the shell |
| `ours_busy` | target.rs `release_holders` ("load's own is on it") | `disk`, `target` | `--only close` first (Try again offers it) |
| `fitted_other_pct` | target.rs `fitted_already` (a table this layout's fit wrote with another percentage) | `disk`, `target`, `partition`, `bytes` | the same `--fit-largest` as before, or `--erase` to start again |
| `erase_not_target` | target.rs `map_disks` | `target` | internal |
| `duplicate_vg` | target.rs `map_disks` | `vg`, `dev`, `disks` | disconnect that disk, or the shell |
| `pool_left_out` | target.rs `left_out_pool_problems` | `disk`, `pool` | another disk |
| `wedge_clash` | other/load.rs | `disk`, `target` | the shell |
| `layout_inconsistent` | sizes.rs | `dev` | report |
| `fit_refused` | fit.rs `one` (about 50 refusal sites) | `disk`, `target`, `reason` (`other_family`, `no_table`, `no_partition`, `tied`, `logical`, `moves_bytes`, `fat_start`, `mbr_reach`, `table_too_small`, `inodes`, `xfs_ag`, `kept_luks_header`, `swap`, `member` (an md or ZFS member), `multi_device` (btrfs), `fs_unsupported`, `no_usage`, `other`); an unknown or `other` reason shows the text | another disk |
| `fit_over_pct` | fit.rs `one` | `disk`, `target`, `needed_pct`, `given_pct` | a second plan with the needed percentage |
| `data_wont_fit` | fit.rs `data_fits` (new figures) | `disk`, `target`, `dev`, `mount`, `used_bytes`, `margin_bytes`, `needed_bytes`, `most_bytes` | another disk |
| `data_unknown` | fit.rs `data_fits` | `disk`, `dev` | another disk (where the space used comes from) |
| `key_needed` | new, `--json` only (cryptsetup, GELI, bioctl with no key); ZFS's existing one | `name`, `group`, `kind` | Passphrases |
| `kept_header_key_needed` | commands.rs `luks_keep_header` | `name` | Passphrases, "the passphrase this disk had" |
| `key_file_unrestorable` | commands.rs `needs_key_file` | `name`, `key_file` | Passphrases (a key must be given) |
| `key_name_unusable` | commands.rs (temp key name) | `name` | report |
| `key_file_unsafe` | new, `--json` only, among the plan's checks | `name`, `path`, `check` | internal (bko-rescue's files) |
| `password_needed` | new, `--json` only (`--repo` without `--password-file`) | | Repository |
| `repo_has_password` | new, `--json` only | | internal |
| `erase_required` | new, `--json` only (J19): a target that isn't blank, not in `--erase`, no record of this session | `disk`, `target`, `ours`, `same_identity` | Review with the disk in the ERASED list, or Start again |
| `remake_after_restore` | new, `--json` only (J19): `assemble` or `format` would run again on a target this session's restore has begun on, with no `partition` in the same run wiping it first | `disk`, `target`, `stage` | Resume's Start again, or `--only open` (RT10o) |
| `plan_changed` | new, `--expect-plan` (J20): the run's `plan_sha256` isn't the one given | `expected`, `got`, `targets` (the disks whose identity moved) | back to Disks: a new plan and Review |
| `archiver_missing` | plan.rs `archiver_problem` | `program` | report |
| `archiver_not_bko` | plan.rs `archiver_problem` | `program`, `version` | report |
| `tool_missing` | plan.rs `missing_tools` | `program`, `stage` | report (a newer image) |
| `lvm_too_old` | plan.rs `lvm_problem` | `version` | a newer image |
| `thin_unsupported` | plan.rs `thin_problems` | `why` | a newer image |
| `zfs_feature_unknown` | plan.rs `zfs_problems` | `pool`, `features` | a newer image |
| `zfs_compat_missing` | plan.rs `zfs_problems` | `pool`, `file` | a newer image |
| `reformat_refused` | reformat.rs | `mount`, `type`, `reason` (`not_linux`, `own_tools`, `grub_reads`, `not_convertible`, `same_type`, `multi_device`, `mounted_elsewhere`, `no_fsck`, `old_kernel`, `hibernation`, `no_uuid`, `twice`, `edited`, `other`: the mount isn't one --reformat finds, never offered by bko-rescue) | Advanced |
| `from_bug_report` | plan.rs | | restore from the dump itself |
| `not_root` | plan.rs | | internal |
| `unsupported` | commands.rs and the families: a layout feature load doesn't build | `what` | report |
| `other` | every site not yet coded | | its text; blocks; Try again, Shell, Report |

### Notes

`target_bigger` (`disk`, `target`, `bytes`), `fit_change` (with `fit`'s
figures), `fit_grow_elsewhere` (a shrink's percentage growing another
disk: the screens' extra line), `pool_renamed` (`pool`, `as`),
`partition_runs` (`--erase` and no record: the disk is let go of and
wiped), `release` (what holds a target is stopped first), `source_empty`
(`disk`), `luks_pbkdf`, `reformat` (`mount`, `from`, `to`), `token`
(a token not restored), `stays_attached` (softraid), and `other`.  Done's
"after the first start" lines come from `token`, `pool_renamed`,
`reformat` and the `tokens` list.

### Failures (`failed.code`, exit 1)

| Code | When | Screen |
|---|---|---|
| `cmd_failed` | a command exited non-zero | its program's help text, else the general one |
| `wrong_passphrase` | `cryptsetup open` exits 2 in `assemble` or `open` (*Check* GELI's `attach` and bioctl's codes at RT10f) | ask again |
| `archiver_failed` | the archiver exited non-zero; `tail` has its stderr (its `restore --help` check, which only a newer archiver passes, is `cmd_failed`) | Try again (the copy runs again) |
| `efi_vars` | a step that writes UEFI variables (`efibootmgr`, or a `grub-install` for an `*-efi` target without `--no-nvram`) failed and `here.efivars` is false | Install without an entry |
| `readback_failed` | a read-back found the device not as the source's | report |
| `not_done_after` | a stage ran but the disks don't show it done | report |
| `no_restored_tree` | `boot` with no restored system in the tree (`--commit`) | Try again from Restore |
| `commit_refused` | `--commit` under `--sysroot` or not as root | internal |
| `write_failed` | a file load writes (a record, `boot.done`, a step's file) | report |
| `lock_held` | another load holds the lock (J8); a `start`, then this, then `end` | wait, or the shell |
| `internal` | a panic | report |

The program's help texts (rescue-screens' "A stage's command failed")
stay in bko-rescue's `app/strings.rs`, keyed by `failed.program` and
`failed.stage`; bko-holt gives the facts, not the advice (rescue-screens'
open question 7).

## Disks of another size

The screens decided (2026-11-24): up to 10 % smaller or bigger is an offer;
a bigger shrink the user decides, when the data fits; a disk the data
doesn't fit can't be taken; growing has no upper limit.  bko-holt gives
the facts for that per (source disk, candidate) pair; the 10 % line is the
screens' policy and lives in bko-rescue (J5).

For each pair, `sizes[]` (and `disks[].target.size`) holds, every figure
fit's own (one rule, fit's, so the stick never does its own sums):

| Field | Meaning |
|---|---|
| `verdict` | `same`; `smaller_slack` (only unused space is lost: `--fit-largest 1` suffices, no partition changes); `smaller` (the largest partition shrinks, data fits); `bigger` (the extra stays free without a flag); `data_wont_fit`; `cant_resize` (fit can't change this layout: `reason` and `why` say, from `fit_refused`'s); `beyond_limit` (fits by the data, but the percentage is over what the flag takes: `FIT_MAX`, 50, until RT10n's `--fit` takes 99) |
| `fit_pct` | the smallest whole percentage `--fit-largest` (or `--fit SRC=PCT`) that makes the fit succeed, for a smaller disk; for a bigger one, the percentage that would use the whole extra; null where none does |
| `change_pct` | how much the largest partition changes, as a percentage of its size (one decimal): the figure the screens show ("/ by 4.3 %") and compare with 10 % |
| `partition`, `mount` | the largest partition and what's mounted on it (or on the file system inside it) |
| `old_bytes`, `new_bytes` | its size now and with `fit_pct` |
| `used_bytes` | the space used at the backup, from the dump (null where the dump has none) |
| `margin_bytes` | the room fit wants beside the data (today max(1 GiB, 5 % of the space used): `SPARE_BYTES`, `SPARE_PCT`) |
| `needed_bytes` | the least size fit lets the file system shrink to, in fit's own terms: fit passes a shrink when its `free` (the mount's statvfs size less used, else the superblock's free blocks) is at least the shrink plus the margin, and the shrink is taken from the file system's own size, so this is that size less `free`, plus `margin_bytes`.  statvfs's size leaves out the file system's metadata, so it comes out a little above `used_bytes` + `margin_bytes`; it is the figure that decides |
| `most_bytes` | the most the file system could get on this disk: the disk less the other partitions as they are, rounded down to fit's MiB, less what its container takes (LUKS header, LVM metadata) |
| `data_fits` | fit's own data test (the `data_fits` fit::one calls) on the shrink this disk needs, whatever the percentage: so `beyond_limit` has it true and `data_wont_fit` false.  Null when fit has no usage (`reason` `no_usage`).  A unit test holds it equal to `needed_bytes <= most_bytes` over the corpus |
| `reason` | for `cant_resize` and `data_wont_fit`, `fit_refused`'s reason code (or `data`), else null |
| `grow` | for a bigger disk: `{"to_bytes": …, "possible": true}` with `--fit SRC=fill` (J16), or `possible: false` and `why` (ZFS, another family, an MBR entry's reach) |
| `why` | a sentence, bko-holt's, for `cant_resize`, `data_wont_fit` and `beyond_limit` |

- Worked out by calling `fit::one` (private today: made `pub(crate)`)
  for the single pair at increasing percentages, 1 to `FIT_MAX` (50; 99
  after RT10n), not `fit::fit`, which clones the whole dump per call.  It
  is a pure function of the dump and a size, so the figures are fit's own
  and the plan that follows agrees.  `most_bytes` and `margin_bytes` need
  fit to return figures it computes and drops today (`DataFit` has `used`,
  `free`, `need`, `shrink`).
- Without the flag, a smaller target is still the problem
  `target_too_small` once mapped; the verdict says before mapping whether
  a second plan with `fit_pct` would pass.
- The margin (rescue-screens' open question 15 proposes 10 % plus 2 GiB):
  whichever rule is chosen is fit's, so the stick shows bko-holt's verdict
  and never its own sums.  Changing it changes text-mode plans, so it
  waits until after OtterGrind1 (J5).
- Mount sizes on the BSDs, illumos and Haiku, and ZFS's pool figures, are
  new in the dump (RT10m); until then those verdicts are `cant_resize`
  for a smaller disk, as fit refuses today, and `bigger` (kept) for a
  bigger one.
- Per disk and grow-to-fill are new flags (J16): `--fit SRC=PCT` fits one
  disk only, so a shrink on one disk no longer grows another's largest
  partition; `--fit SRC=fill` grows the largest to fill the disk, no upper
  limit; `--fit` takes 1 to 99, data deciding.  `--fit-largest PCT` keeps
  its meaning and its 1-50 range.
- RT10n must extend `fit::wrote` with them.  It searches 1 to `FIT_MAX`
  today, and it backs `fitted_already`, the guard that stops a later run
  writing over a fitted table without `--erase`: a table written at 51 to
  99 %, with `fill` or with one disk's own percentage would otherwise not
  be recognised.  `wrote` learns the per-disk percentage, `fill`, and 1 to
  99, and the partition record (`table_record_text`) names the flag that
  wrote it, so a resume passes the same one.

## Stopping, resuming, the copy marks and the erase gate

### Stop (J7)

- Under `--json`, load installs a SIGTERM handler (`signal(2)` through
  FFI, setting an atomic flag; *Check* SIGTERM's number on every platform
  against rescue's FFI header test, Haiku's especially).  Text mode keeps
  the default action.
- Under `--json` each command runs in a process group of its own
  (`CommandExt::process_group(0)`, so its pgid is its pid).  Text mode
  keeps them in load's, so a terminal's Ctrl-C still reaches them.
- `Cmd` gains an `archiver` tag, set by the builders on each command that
  runs the archiver: the restore itself, the msdosfs wrapper, and each
  shared snapshot's `sh -c` script.
- The flag is checked before each command.  While a command tagged
  `archiver` runs, load sends SIGINT to its process group (`kill(-pgid)`),
  which reaches rustic-bko inside an `sh -c` too; rustic-bko then ends
  within seconds (*Check* at RT10g that it exits non-zero and leaves
  nothing half-written it wouldn't redo, and that the wrapper scripts
  pass its status on).  Other commands are left to finish (an
  interrupted mkfs or sfdisk helps nobody).
- Then `stopping`, the stage's `failed` is not written, and `end` with
  `stopped: true`, exit 5.  No later stage runs, `close` included: the
  tree stays as it was, for the Resume offer or the shell.  The restore
  mark isn't written, so the copy counts as not done (J10).
- **How bko-rescue stops it:** SIGTERM to load's pid as the lock names it
  (pid and start time, against a reused pid), never to a process group
  and never `systemctl stop`.  The transient unit is started with `-p
  KillMode=mixed` (a stop or a shutdown sends SIGTERM to load alone, and
  SIGKILL to the rest only after the timeout) and `-p
  TimeoutStopSec=300` (*Check* at RT10g against a large restore's
  SIGINT-to-exit time), `-p LimitCORE=0`, and the environment by
  `--setenv`.  The default `KillMode=control-group` would SIGTERM mkfs
  and the archiver directly on a shutdown.  Elsewhere `setsid` gives the
  same: no one signals load's children but load.

### One load at a time (J8)

Under `--json`, a writing run takes `RUN_DIR/load.lock` (`O_EXCL`, its pid
and start time inside, removed at `end`; a lock whose pid is gone, or
alive with another start time, is taken over).  A second writing run
writes `start`, `failed` with `lock_held` and the holder's pid, and `end`.
A restarted bko-rescue reads the lock to know a load is still running and
which events file to follow (it keeps that path in its own
`/run/bko-rescue/run.json`).  Plan and status don't take it.  A text-mode
`--commit` run doesn't take it either, but refuses while a live lock is
held (exit 1, "another bko-holt load is running: pid N"), since the Shell
is one key away from a detached JSON load; no test rig makes a lock, so
the rigs see nothing new.

### The copy marks (J10)

- When `restore` starts, load removes `RUN_DIR/restore.done` and writes
  `RUN_DIR/restore.started` (beside `boot.done`), naming the layout (host
  and dump time) and the targets.  A `partition` stage that runs removes
  both before its first command (the disks are new again), and under
  `--json` `boot.done` too.  The marks are written in text mode as well
  (in RAM, read by nothing there), so a JSON run after a text run sees the
  truth.
- `restore.done`, with the same text, is written where `boot.done` is:
  after the stage's last command (the shared snapshots' read-only flags,
  `thin_restore`'s raw volumes and LV snapshots, the atimes' capture all
  come after the archiver) and after the stage's read-back.  Neither is
  ever written into the restored tree (Principle 3).
- Under `--json`, status's restore row is done only with `restore.done`
  (see `bko-holt-status`), and `--through`'s skip reads that row, so a
  stopped, failed or half-done copy always runs again.  A reboot forgets
  both marks, so after one the copy always runs again: the safe answer.
- Text mode's row and skip stay as they are (J10 says when that changes).

### Never over the old system (J19)

Under `--json`, as plan problems: in the plan, and in every writing run,
with `--commit` or without (so the plan and a dry run show what the real
run would refuse):

- **The erase gate:** every target that isn't blank must be named in
  `--erase`, or have this session's partition record
  (`target::load_wrote`).  `TargetDisk::is_ours` is no exception: the
  source machine's own disk carries the layout's identifiers too.  A
  target that fails is `erase_required`, exit 4, nothing written.  So a
  same-machine restore always wipes the old disk, as layload.md's
  "Safety" rule 2 means, and the old filesystems' files never mix with the
  restored ones.
- **No remaking under a restore:** once `restore.started` is there for a
  target, a run that would run `assemble`'s creating commands or `format`
  on it without this same run's `partition` stage wiping it first
  (`--only assemble`, `--only format`, or `--through` after `--only
  close` left the containers closed: with this session's record,
  `--through` skips `partition` even with `--erase`) is
  `remake_after_restore`.  The ways on are `--only open` (RT10o) and
  `--only mount`, or Start again (`--only partition` with `--erase`, then
  `--through`), which wipes first.
- The plan shows both ahead (`will_erase`, `writes`, and the problems), so
  Review's ERASED list is bko-holt's.
- Text mode is unchanged now: an administrator's `--commit` without
  `--erase` onto the source's own disk behaves as today.  J19 says when
  text mode gets the gate.

### Resume after a reboot: the `open` stage (J14)

`--only assemble --commit` runs assemble's commands as for new disks: on
disks the first run made, that's a new `luksFormat`, `mdadm --create`,
`pvcreate`, `zpool create`, losing what the first run restored.  After a
reboot nothing is open, so assemble's row is not done and `--through`
would do the same.  J19's gate refuses both under `--json`; Resume's "open
what exists" needs a stage that only opens:

- `open`, not in `Stage::WRITING` (so `--through` never runs it), run as
  `--only open --commit`: for each container, array, volume group and
  pool status finds made (by the layout's UUIDs), only opening it:
  `cryptsetup open`, `geli attach`, `bioctl -c C` with the passphrase
  file, `mdadm --assemble`, `vgchange -ay` with `--devices`, `zpool import
  -N -R TARGET`.  Nothing is made, formatted or wiped; a device status
  doesn't find as the layout's is left alone and named.
- Only LUKS has an opening command of its own in assemble today; md
  (`mdadm --create`), the pools (`zpool create`), the VGs, GELI and
  softraid are each made in one step, so `mdadm --assemble`, `zpool
  import`, `vgchange -ay --devices`, `geli attach` and bioctl's opening
  are new builders, each with its read-back.
- Then `--only mount --commit` (mount's commands are mkdir and mount of
  file systems that exist), and `status --json` sees the tree.
- Not after a reboot when a container's only key was a temporary one
  (status's `temporary_keys` with `kept` false and `boot` not done): it
  can't be opened, and Resume offers only Start again.  Nor when every
  target is `ours` and `same_identity` with no record: the old system's
  own disk and load's work can't be told apart (`resume.go_on` false).
- *Check* at RT10o each family's (softraid's volume, GELI's providers,
  Ubuntu Core's kept headers).
- Until RT10o lands, `resume.go_on` is true only within the session that
  made the disks (every target `recorded`), and after a reboot Resume
  offers Start again alone.

## The dump's new facts (J15)

### `keyboard`

The shape rescue-screens' item 6 draws (`console`, `boot_prompt`, `xkb`,
`desktop`, `at_setup`, `canonical`, `notes`), as a new top-level section,
with every field nullable.  Read by `dump` from the files that item
lists, per platform, only files that are there, nothing run but
`svcprop` on illumos (*Check*).  `desktop` reads users' layouts only with
`--keyboard-desktop` (open question 3: new paths under `/home`), and never
writes a user name.  `at_setup` is the backup tool's to fill, not
dump's.  Haiku's binary `Key_map`: null in version 1.

### `time`

Rescue-screens' item 7 (`zone`, `zone_from`, `zone_file`, `posix_rule`,
`utc_offset_seconds`, `abbreviation`, `measured_at`, `rtc`, `rtc_from`).
`posix_rule` is the TZif file's footer, which bko-holt reads itself (no
zoneinfo needed on the stick); `content-match` compares a copied
`/etc/localtime` with the zoneinfo tree, `zone1970.tab` names first.
`storage.sys_tz_minuteswest` stays as it is, for FAT.

### Mount sizes everywhere

`size_bytes`, `used_bytes`, `available_bytes` for each mount on the BSDs
(`statfs`), illumos (`statvfs`) and Haiku (`df`, *Check* its output), as
Linux has them, and each imported pool's `size`, `allocated`, `free` in
`storage.zfs.tools.imported_pools` (`zpool list -Hp`).  Reading only.

### Schema

The dump stays `bko-holt` schema 1: the sections are new keys, which
`load` ignores (it reads by path and checks `schema == 1` exactly, so a
bump would make every older stick refuse a newer dump).  HoltTest's
judge (`testvm/holttest1d/judge.py`) compares two dumps field by field
outside its `IGNORED` list, which covers `used_bytes`, `available_bytes`
and `free_blocks` but not a bare `free` or `allocated`.  What must go into
`IGNORED` in the same commit as the dump change: `time.measured_at`,
`time.utc_offset_seconds` and `time.abbreviation` (RT10l), and the
imported pools' `allocated` and `free` (RT10m), which differ between the
source's dump and the restored machine's.  (*Check* at RT10l; judge.py is
shared with running work.)

## The dry run, end to end

### `BKO_HOLT_NO_COMMIT`

- **In force** when the variable is set and not empty and not `0` (J12:
  any other value counts as set, so a typo can't turn the lock off).
- **What it refuses:** `--commit` on `load`, whatever else is on the line,
  checked after `--help` and before the layout is read.  Exit 2, and on
  stderr (or as `bko-holt-error` under `--json`, code `no_commit`):

  ```text
  bko-holt load: BKO_HOLT_NO_COMMIT=1 is set (a dry run), so --commit is refused; nothing was written to any disk
  ```

  (the value as found replaces `1`; the run is still recorded in
  `RUN_DIR/last-load.json`, in RAM, hence "to any disk").  The hidden
  helpers that write (`raw-write`, `write-table`, `rewrite`, `md-lbs`,
  `snapcore-boot-areas`, `umount-tree`, `efi-dedupe`, `grub-prefix`)
  refuse to run at all under it, exit 2, `bko-holt HELPER:
  BKO_HOLT_NO_COMMIT is set (a dry run), so nothing is written`: load
  never runs them without `--commit`, so only a bug or a hand would.
- **What it narrows:** `recovery-key-file OUT` and `report-bug -o OUT`
  write only a new file under it (`O_EXCL`; an OUT that exists, a device
  included, is refused, exit 2), so neither can reach a disk.
- **What it doesn't refuse:** `plan`, `status`, the writing stages without
  `--commit` (which print), `dump`, `report-bug` to a new file or stdout,
  `recovery-key-file` and `recovery-words` to a new file, `stick --json`.
  `--erase` is allowed without `--commit`, since it writes nothing then
  (J6; D14's lock 1 is the other half of that decision).
- It is in every document's `dry_run` and the api's `no_commit`, so
  bko-rescue can check the lock took before it shows the dry-run banner.
- The fake already refuses `--commit` under `=1`; it changes to the rule
  above (any non-empty value but `0`) and to the message.

### The flow

1. bko-rescue `--dry-run` resolves bko-holt's absolute path, sets
   `BKO_HOLT_NO_COMMIT=1` in the small environment it gives bko-holt, and
   checks `--version --json` (same path, same environment) says
   `no_commit: true`.
2. Machine, Repository, Snapshot: as for a restore (read only).
3. Disks: `plan --json` without `--map`; the real candidates and refusals.
4. Passphrases: asked, written as key files as for a restore (so
   `key_needed` goes, and the files are practised).
5. Review: `plan --json` with the full argument list, `--erase` included
   for every target that isn't blank (J6, J19) but never `--commit`; the
   serials are asked and checked as for a restore.  The button reads <
   Show what would happen >.
6. "Restore": `load ... --through close --json` without `--commit`, run as
   a **direct child** in a new session (`setsid`), not detached: nothing
   needs to outlive the UI, and the child gets exactly the environment of
   step 1 (a `systemd-run` unit wouldn't, without `--setenv`).  bko-rescue
   stops at once unless `start.dry_run` is true and `start.commit` false.
   The events are `cmd` with `would: true` per command, `stage_done` with
   `checked: false` per stage, `boot` and `close` included (J11), and `end`
   0.  The archiver isn't run.
7. Done says nothing was written.

D14 counted on "`--erase` is never passed" to keep used disks safe; the
code shows that never held for a disk with the layout's identifiers (the
machine's own), which needs no `--erase` today.  With J19, under `--json`
`--erase` is the one flag that lets a used disk be written, in a restore
and a dry run alike.  The dry run passes it (J6) and keeps both of D14's
locks on `--commit`, so a used disk is written only if both locks fail at
once.

## Bug reports and redaction

- `report-bug --attach FILE` (new, repeatable): a file of bko-rescue's,
  included and redacted as `--stderr`'s are (text, the same Redactor, the
  same pseudonyms).  bko-rescue attaches the events file, the last plan
  and status documents, and its own log.  Each is capped as `--stderr`
  is (the last 3000 lines).
- The Collector learns identifiers from the plan document too (new): the
  candidates' and targets' models' serials, WWIDs, `by_id` links and
  labels are this machine's, which the dump doesn't hold, so without
  this a report would show them.  *Check* at RT10i what report-bug's
  target-disk status already collects.
- `report-bug --strip-file FILE` (new): a file of strings, one a line,
  the Redactor replaces wherever they appear, as "other identifiers"
  (in values and text, never in a JSON section's object keys, which are
  bko-holt's own words: a hint "boot" mustn't leave the layout no
  layout; the summary counts them on a line of their own).
  bko-rescue writes it in its `k/` directory with the repository
  password hint and anything else the user typed that isn't a secret
  (a location name), and removes it with the keys.
- **The failed run is the one reported.**  Every load records itself in
  `last-load.json`, plan and status included, so the `status --json`
  bko-rescue runs right after a failure would make report-bug report the
  status run.  Under `--json`, plan and status runs don't record
  themselves; writing runs do, as now (text mode unchanged).
- A recorded load run whose args hold `--json` is re-run by report-bug in
  text mode (it strips `--json` and `--expect-plan`), so the report's plan
  and status read as they do now.
- `ISSUES` becomes `https://github.com/mchobbit/backotter/issues`
  (rescue-screens' item 5), in report-bug's help, its last line and the
  report hint, together.
- `--no-redact`'s first line names every `Kind`'s plural (hostnames,
  machine-ids, UUIDs and GUIDs, serial numbers and WWIDs, labels, user
  names, key-file paths, MAC addresses, other identifiers, repositories and
  password-file paths), from `Kind::plural`, so it can't drift.
- Key files' contents never enter a report: report-bug reads none of the
  paths it redacts.  A `private` write's text is in the plan document and
  the log only as its size and SHA-256 (Common rules), so neither carries
  it into a report.

## What each screen needs, and where it comes from

| Screen (rescue-screens.md) | Needs | From | Step |
|---|---|---|---|
| 1. Machine | machines, hostname, OS, written, sequence, DMI UUID, checksum state, this computer | `stick --json` | RT10k (D11) |
| 1. Machine, times | each machine's zone | dump `time`, via `stick --json` | RT10l |
| Type a location | nothing of bko-holt's | | |
| 3. Repository, keyboard offer | the layout at set-up and at the console | dump `keyboard` (`at_setup`, `canonical`) | RT10l |
| 3. Repository, password hint | `password_hint` | `system.json` (rescue-screens' open question 2), passed through by `stick --json`; kept out of reports by `--strip-file` | RT10k, RT10i |
| Recovery key | the words checked, unknown words' positions, the password written | `recovery-words OUT --json`; Ubuntu Core's kept header: `recovery-key-file OUT` | RT10p |
| 4. Snapshot | the zone, POSIX rule | dump `time` | RT10l |
| 5. Disks overview | old disks, matched by identity, candidates (targets included), refusals, contents | plan `disks`, `candidates` (`target_of`, `refused`, `contents`, `erase_needed`, `serial_shared`) | RT10c |
| One disk | per pair: a little smaller or bigger, much smaller, too small, much bigger | `candidates[].sizes` | RT10d |
| Too small for the data | used, needed, most | `sizes`' figures; `data_wont_fit` | RT10d |
| Where the space used comes from | mount sizes on every platform, pool figures | dump mounts | RT10m |
| A much bigger disk: grow | grow to fill | `--fit SRC=fill`, `sizes[].grow` | RT10n |
| With a fix: a little smaller | the new sizes, another disk growing too | second plan's `fit`, note `fit_grow_elsewhere`; per disk with `--fit` | RT10d, RT10n |
| 6. Passphrases | groups, shared names, kept header, temporary keys, limits | plan `keys`, `tokens`; `key_needed` | RT10c |
| The keyboard at start-up | the boot prompt's layout | dump `keyboard.boot_prompt` | RT10l |
| 7. Resume offer | whether Go on is safe and why not, what's open, the copy marks | status `resume`, `targets`, `temporary_keys`, `open`, `marks`; the `open` stage | RT10e, RT10o |
| 8. Review | source, this computer, the ERASED list, changes, problems; the run bound to what was reviewed | plan `source`, `here`, `disks[].target.will_erase`/`writes`, `fit`, `notes`, `problems`, `plan_sha256` (`--expect-plan`) | RT10c |
| Commands | the text plan | plan `text` | RT10c |
| Advanced: another filesystem | per mount, which types and why not | plan `reformat`; `reformat_refused` | RT10c |
| 9. Restore | stages, commands, progress, log | events | RT10f |
| Cancel? | stop between commands, archiver stopped cleanly | SIGTERM to the lock's pid, `stopping`, `end.stopped` | RT10g |
| 10a. Done | tokens, renamed pools, reformats | plan `tokens`, `notes` | RT10c |
| 10b. Failure | stage, error in full, log, try again | `failed`; status `next`; `--only` | RT10f, RT10e |
| A stage's command failed | program, exit, BIOS or UEFI | `failed.program`, `failed.code` `efi_vars`, plan `here.efivars` | RT10f |
| The bug report | report with the run's files, the issue link | `report-bug --attach`; `ISSUES` | RT10i |
| Dry run banner | the lock in force | `dry_run`, api `no_commit`, `start.dry_run` | RT10a |

### Gaps bko-holt newly provides

From rescue-screens' "What the screens need from bko-holt", item by item:

1. A stop request: SIGTERM, between commands, SIGINT to the archiver,
   `end.stopped` (RT10g).
2. A copy-complete mark: `RUN_DIR/restore.done`, `marks.restore_complete`
   (RT10e); not on the target (Principle 3).
3. Safe re-runs of assemble and mount: not today (`--only assemble`
   makes anew, and after a reboot `--through` does too); refused under
   `--json` by J19's gate (RT10c), with the `open` stage to follow
   (RT10o).
4. Codes: `target_too_small`, `data_wont_fit` with figures, every code
   above; `keys[].new_ok` for a kept header (RT10b-RT10d).
5. `ISSUES` to `/issues`; `--no-redact`'s first line naming every kind
   (RT10i).
6. The keyboard layout in the dump (RT10l).
7. The time zone in the dump (RT10l).
8. A disk of another size: grow beyond 50 % (`--fit SRC=fill`), per
   disk (`--fit SRC=PCT`), a shrink decided by the data (`--fit` takes up
   to 99), `data_wont_fit` with figures, "the most" before a second plan
   (`sizes`), space used on every platform (RT10d, RT10m, RT10n).

Also new, found here: the erase gate (J19), the plan hash (J20), the
recovery key check (J21), the dry run's `boot`/`close` (J11), `--erase` in
a dry run (J6), no prompts under `--json` (GELI reads `/dev/tty` itself),
the lock (J8), candidates' contents (J13), Advanced's per-mount refusals
up front.

## Compatibility

### The text CLI

Unchanged, by construction:

- New behaviour sits behind `--json`, `--version --json`, `stick`,
  `recovery-words`, `--only open`, `--fit`, `--attach`, `--strip-file`,
  `--expect-plan`, or `BKO_HOLT_NO_COMMIT`.  None is used by OtterGrind1,
  HoltTest, BackShot or Test2.  The one text-mode change is J8's: a
  `--commit` run refuses while a live `load.lock` is held, which only a
  `--json` run makes.
- The sink refactor of `exec::run` (`ui.md`: the stages write to a sink)
  keeps a text sink that builds the same `String` the code builds today,
  printed at the end as now; printing text as it goes is a separate,
  later change (J9), so no stdout/stderr interleaving moves in RT10.
- `Problem`'s `Display` is today's string, so every text plan, status and
  error line is byte for byte the same; the golden plans
  (`holt/tests/golden_plans.rs`, `tests/corpus/*/plan*.txt`) and
  `load_cli.rs` must pass unchanged at every RT10 step.
- The dump grows two sections and the other platforms' mount sizes
  (RT10l, RT10m): new keys only; judge.py's `IGNORED` takes the time and
  pool fields that vary.  That is the one RT10 change a text-mode test
  sees, and it lands after OtterGrind1's freeze.
- The text-mode exposure J19 closes under `--json` (`--commit` without
  `--erase` onto the source's own disk) stays in text mode until the user
  decides otherwise (J19).

### demo.rs and the fake

The demo's provisional stream is close to this one.  The changes, all in
RT10h:

- **demo.rs `Follow`:**
  1. `cmd`'s `n`/`of` are within the stage: the "(n of of)" text says
     "command n of of in this stage", or uses `start.stage_commands` for
     a run-wide count.
  2. `cmd` with `would: true` (no `cmd_done`) and `stage_done` with
     `checked: false`: shown as "would run" ticks, the dry run's form.
  3. `progress` has no `files`: the figures line leaves the file count out
     (J17); `part`/`parts` restart the bar per archiver run ("part 2 of
     21").
  4. `failed` gains `code`, `program`, `tail`; `end` gains `stopped`; new
     `stopping`, `warning`, `problems` (shown, the run blocked).
  5. `end` with exit 5 reads as stopped, and 101 as bko-holt's own
     failure ("bko-holt stopped on an internal error: report it"), not as
     a stage's failure.
  6. A first line that is a `bko-holt-error` document (no `event`, its
     `format` `bko-holt-error`) is the whole stream: shown as the refusal
     it is (exit 2 under `BKO_HOLT_NO_COMMIT`, exit 3), not failed as "an
     event with no \"event\"".
  7. `start` is checked as now (format, schema, `commit`), and also
     `dry_run`: the demo stops unless it is true, as RT12's dry run will;
     `pid` kept for Cancel once RT12 detaches load.
  8. Unknown events are skipped already; unknown fields too; unknown
     values fail closed (Principle 6).
- **The demo's argv** (`ARGS`): a real bko-holt refuses `--through close`
  without `--repo` ("restore needs --repo"), so the fake's scenarios gain
  `--repo` and `--password-file`, as RT12's real command lines have them.
  Until RT12 runs the real one, only the fake sees them.  `--demo --holt
  REAL` run as a user, not root, stops at `not_root` (exit 4, a
  `problems` event): the demo shows it as the run blocked.
- **The fake's scenarios** (`testvm/rescue/scenarios/`): today's four
  are hand-written with `commit: false` and `cmd_done` statuses, which a
  real run without `--commit` never writes, and run-wide `n`/`of`.  The
  fake's scenarios and the GOOD test in demo.rs change together.  After
  RT10f they are re-recorded from real runs: `demo` from a dry run
  (`would` ticks), and for RT12's tests, `--commit` runs recorded in a
  QEMU guest (`HT_VARIANT=json`, below), `demo-fail` from a run made to
  fail at `boot`, `demo-slow` from one stopped by SIGTERM.  The demo keeps
  refusing `commit: true`, so the recorded commit runs are RT12's, not
  the demo's.  `demo-schema` stays hand-written.
- **The fake's checks:** `BKO_HOLT_NO_COMMIT` as above (any non-empty
  value but `0`), with bko-holt's message; key files as now (its rules are
  the stricter bko-rescue side of J3's).

## Testing

- **Unit tests in holt** (`cargo test`, on the fake rescue systems
  `testutil::Tree` and `FakeRunner` give): each document for the sample
  layouts; every `Problem` site that gets a code has a test asserting the
  code and that its text is unchanged; the size verdicts against fit's own
  results for the same pairs; `--json` argument errors as
  `bko-holt-error`; `BKO_HOLT_NO_COMMIT`'s values, message and exit,
  for load and each helper; the key-file checks; the event sink's order
  (`start` first, `end` last, on success, failure, problems, stop and a
  panic) with a fake archiver script that writes rustic's status lines on
  stdout and its `[WARN] restore` lines on stderr, run directly and inside
  an `sh -c` wrapper (the stop's SIGINT must reach it there); the lock and
  text mode's refusal beside it; the erase gate on the source's own disk
  (`is_ours`, `same_identity`, no record: `erase_required`), on a recorded
  disk (passes) and after `restore.started` (`remake_after_restore`); the
  copy marks (a stop or a failure after the archiver leaves no
  `restore.done`, and `--through` then runs restore again); the plan hash
  (a disk's serial or size changed between plan and run: `plan_changed`);
  the log under `--json` (no progress lines, no `private` text).
- **Golden JSON:** beside each `tests/corpus/*/plan.txt`, a `plan.json`
  from the same arguments plus `--json`, compared after replacing
  `generated` and `version`; and `status.json` for the sample layouts.
  The text goldens stay as they are and must not change.  The corpus runs
  give `--repo` without `--password-file` and no `--key-file`, so most
  `plan.json`s have `ok: false` with `password_needed` and `key_needed`:
  blessed as they are, since that is what a `--json` run of those
  arguments is.  `text` in each `plan.json` must equal its `plan.txt`
  byte for byte, but for a `private` write's replaced text (J18).
- **A schema check shared by both sides:** a small checker in
  `testvm/rescue/holt_schema.py` (Python's stdlib only) that knows every
  document's and event's required fields and types.  It runs over the
  golden JSON, over the fake's scenario files, and over every recorded
  run, so the fake can't drift from bko-holt.
- **The fake kept in step:** its scenarios are recordings (above), each
  with the bko-holt version it came from; a test fails when a scenario's
  `start.version` is older than the protocol's schema allows.
- **`HT_VARIANT=json`** in HoltTest1 (`ht1.sh`, `ht1-bsd.sh`): the
  rebuild runs as today, then again in a second throwaway overlay with
  the same arguments and `--json`, events to a file; the judge's verdict
  must be the same, the events must pass the schema check and end with
  `end` 0, the commands in the events must be the text run's (but the
  archiver's flag), and the restored `/var/log/bko-holt` must be the text
  run's but its argv lines.  The `--json` run adds `--erase` for any
  target that isn't blank (J19); its key files are the text run's, and
  a guest whose text run types a passphrase gets a key file in both runs.  One guest per family: debian13 (LUKS, UEFI),
  freebsd15-geli, an OpenBSD softraid guest (openbsd8-crypt, settled at
  RT10j; debian13 was debian13-stack, below), omnios58 (ZFS), haiku.  A
  `BKO_HOLT_NO_COMMIT=1` dry run of each too: it must write nothing (the
  overlay's disk images compared before and after) and end 0.
- **HoltTest2 and OtterGrind1:** unchanged; RT10 lands in small commits,
  each with the text goldens passing, and none during a freeze run.

## Code changes in holt

| File | Change |
|---|---|
| `src/main.rs` | `--version --json` in the `-V\|--version` arm; `stick`; `recovery-words`; `--json` refused on dump and report-bug; NO_COMMIT for the helpers; the panic hook under `--json` |
| `src/load/mod.rs` | `--json` (found by parse_args's own flag table), `--expect-plan`, `--only open`, `--fit`; `Options.json`; NO_COMMIT check; the error document; no `last-load.json` for plan and status under `--json` |
| `src/load/codes.rs` (new) | `Problem`, `Note`, `Refusal`, `Failure` and their codes |
| `src/load/jsonout.rs` (new) | the documents and the event sink (json.rs's `Obj`); `plan_sha256` |
| `src/load/plan.rs` | `check` returns coded problems; plan document; `text`; `repo_has_password`; the key-file checks |
| `src/load/target.rs` | candidates (targets included), contents, `by_id`, `serial_shared`, `ours`/`recorded`/`will_erase`/`writes`, refusals as codes, `backup_drive`; the erase gate (J19) |
| `src/load/fit.rs` | `one` made `pub(crate)`; figures out (`needed`, `margin`, `most`); the pair search; per disk and fill, and `wrote` for them (RT10n) |
| `src/load/status.rs` | status document; `marks`, `targets`, `temporary_keys`, `resume`; restore's row with its mark under `--json`; `open` |
| `src/load/exec.rs` | the sink; streaming children (stdout and stderr read on threads); `/dev/null` stdin, a process group per child, `RLIMIT_CORE` 0 under `--json`; the archiver's progress and warnings; the log's filter; SIGTERM; the lock (and text mode's refusal beside it); `restore.started`/`restore.done`; `--expect-plan`; dry-run `boot` |
| `src/load/commands.rs`, `src/other/{geli,softraid}.rs` | `key_needed` under `--json`; the archiver's flag and tag; key groups; `open`'s commands (RT10o) |
| `src/sys.rs` | `signal`, `kill`, `setrlimit` FFI |
| `src/report.rs`, `src/other/*.rs` | `keyboard`, `time`, mount sizes (RT10l, RT10m) |
| `src/bugreport.rs`, `src/redact.rs` | `--attach`, `--strip-file`; the Collector on plan documents; `ISSUES`; `--no-redact`'s line; `-o` new-file-only under NO_COMMIT |
| `src/snapcore.rs` | `recovery-key-file` new-file-only under NO_COMMIT |
| `src/recovery.rs`, `src/recoverycmd.rs` (new) | `recovery-words OUT --json` (RT10p) |
| `src/stick.rs` (new) | `stick --json` (RT10k) |

json.rs's writer (`Obj`, `compact`) already does what the documents
need; `compact()` gives one line per event.

## Steps

| | What it builds | Files | Tested by | est. | Needs |
|---|---|---|---|---|---|
| RT10a | `--json` parsing (parse_args's flag table), `bko-holt-error`, `--version --json`, `BKO_HOLT_NO_COMMIT` (load, helpers, the new-file-only writers), stdout discipline | main.rs, load/mod.rs, jsonout.rs, snapcore.rs, bugreport.rs | unit tests; text goldens unchanged | 0.5 d | this spec approved |
| RT10b | `Problem`/`Note` with codes: the table's codes, the rest `other` | codes.rs, plan.rs, target.rs, fit.rs, reformat.rs, commands.rs | a test per coded site; goldens unchanged | 1 d | RT10a |
| RT10c | plan document: source, here, disks (`ours`, `recorded`, `same_identity`, `will_erase`, `writes`), candidates (targets included, refusals, contents, `by_id`, `serial_shared`), erase, keys (groups), tokens, reformat options, stages (`archiver` tag, `private`), `text`, `plan_sha256` and `--expect-plan`; `key_needed`, `password_needed`, `key_file_unsafe`, `repo_has_password` and the erase gate (J19) under `--json` | plan.rs, target.rs, commands.rs, geli.rs, softraid.rs, jsonout.rs, exec.rs | golden `plan.json`; schema check; the gate's tests | 1.75 d | RT10b |
| RT10d | size verdicts per pair, fit's figures, `data_wont_fit`, `target_too_small` | fit.rs, target.rs | verdicts against fit's plans; goldens | 0.75 d | RT10c |
| RT10e | status document; `restore.started`/`restore.done` after the stage's last command; restore's row and `--through`'s skip by the mark under `--json`; `targets`, `temporary_keys`, `resume`, `open`; `remake_after_restore` | status.rs, exec.rs | unit tests; golden `status.json` | 0.5 d | RT10c |
| RT10f | the event sink: streamed children, `/dev/null` stdin, `RLIMIT_CORE`, the archiver's status lines read and re-emitted, its `[WARN]` lines as warnings, the log's filter and `private` elision, long argv bounded, failure codes, dry-run `boot`/`close`, the panic hook | exec.rs, commands.rs | sink tests with a fake archiver, direct and under `sh -c`; order on every path | 1.25 d | RT10c |
| RT10g | SIGTERM stop, a process group per child, SIGINT to the archiver's group, exit 5, the lock and text mode's refusal beside it; the unit's `KillMode=mixed` written down for RT12 | exec.rs, sys.rs | unit tests; a stopped run in a guest, under `systemd-run` | 0.75 d | RT10f |
| RT10h | the shared schema checker; the fake's NO_COMMIT rule; demo.rs's eight `Follow` changes; the scenarios' argv | testvm/rescue/, rescue/src/demo.rs | demo-test.py, pty-test.py; the checker on goldens and scenarios | 0.5 d | RT10f |
| RT10i | report-bug `--attach`, `--strip-file`, the Collector on plans, `last-load.json` only for writing runs under `--json`, `ISSUES`, `--no-redact`'s line | bugreport.rs, redact.rs, load/mod.rs | redact tests (`redact_dumps.rs`) | 0.5 d | RT10c |
| RT10j | `HT_VARIANT=json` and the dry-run check in HoltTest1; recorded runs into the fake's scenarios | ht1.sh, ht1-bsd.sh, scenarios | five guests, both runs | 0.5 d + runs | RT10g, RT10h; pick-host slots |
| | *Core, what RT12 needs: ≈ 8 d* | | | | |
| RT10k | `stick --json` (D11) | stick.rs, main.rs | fake config partitions; one image boot-test | 1 d | RT10a; the TOML question |
| RT10l | dump `keyboard` and `time`, every platform; judge.py's `IGNORED` | report.rs, other/*.rs, judge.py | fixtures per platform; HoltTest1 on one guest per family | 1.5 d | after OtterGrind1's freeze |
| RT10m | mount sizes on the BSDs, illumos, Haiku; pool figures; judge.py's `IGNORED` for them | other/*.rs, report.rs, judge.py | fixtures; guests | 0.75 d | as RT10l |
| RT10n | `--fit SRC=PCT\|fill`, up to 99, data deciding; `fit::wrote` and the record for them; `sizes[].grow`; layload.md's fit section | fit.rs, target.rs, mod.rs, layload.md | fit's tests, `fitted_already` at 51-99 % and fill; HoltTest1 `HT_VARIANT=bigger` with fill | 1.75 d | RT10d; J16 |
| RT10o | the `open` stage: new opening builders for md, LVM, ZFS, GELI, softraid, each with its read-back | commands.rs, other/*.rs, status.rs | unit tests; a resume after a reboot in a guest per family | 2.5 d | RT10e; J14 |
| RT10p | `recovery-words OUT --json` (J21) | recovery.rs, recoverycmd.rs, main.rs | unit tests on recovery.rs's vectors; NO_COMMIT | 0.5 d | RT10a; J21; needed by RT11's Repository screen |
| | *All of RT10 ≈ 16 d* | | | | |

Source changes go through High-effort subagents, each step committed on
main when its tests pass, nothing pushed until the user says.  holt is
edited by other sessions every day: each brief names the functions it may
change.

### What RT10f and RT10g found

Built 2026-11-24 (exec.rs's run_cmd_json, the event sink, the lock and the
stop; sys.rs's signal, kill and setrlimit).  Tested by holt's unit tests
(a fake archiver writing rustic-bko's status and `[WARN] restore` lines,
direct and under `sh -c`, stopped by SIGINT to its group) and in a
debian13 rescuer (ht1-debug.sh): a `BKO_HOLT_NO_COMMIT=1` dry run with
`--erase` (26 `would` commands, boot and close included, vdb's first and
last MiB unchanged, end 0); a `--through close --commit --json
--expect-plan` run under `systemd-run -p KillMode=mixed -p
TimeoutStopSec=300 -p LimitCORE=0`, stopped by SIGTERM to the lock's pid
mid-copy; the same resumed to its end; and an uninterrupted `--json` run,
which HoltTest1's verify passes (fsmeta, boot, dumps).

- **The Checks settled.**  rustic-bko's sftp: opendal's sftp service runs
  ssh through openssh 0.11.6, whose master is started with `-o
  BatchMode=yes`, and its default known-hosts strategy is Strict
  (`StrictHostKeyChecking=yes`): an unknown host key fails, never waits.
  SIGINT to the archiver's group: rustic-bko (strict exit) exits 130 in
  0.45 s mid-restore; `end` 5, `ExecMainStatus=5`, no `restore.done`, the
  lock gone; TimeoutStopSec=300 is far above it.  SIGTERM is 15 and SIGINT
  2 on every platform (Haiku's per RT1); RLIMIT_CORE is 4, Haiku's 0.
  `--json-progress` beside `RUSTIC_STRICT_EXIT`: one status line a second
  from the first, `total_bytes` known at once; the log keeps one per
  archiver run.  `--glob-file` wasn't on debian13's restore (no btrfs
  snapshots): RT10j's guests have it.  A 20-hour copy's ~14 MiB of
  events fits the stick's RAM (rescue-tui's follower reads 4 MiB a
  tick); bko-holt keeps only the last status line's text in memory.
- **Decided:** the lock is taken by `--commit` runs only (a run without it
  writes nothing, and a dry run is bko-rescue's direct child); it is a
  line of JSON, `{"pid": N, "start": "S"}` (`start` Linux's
  /proc/PID/stat starttime, illumos's /proc/PID/psinfo `pr_start` (`ps`'s
  stime turns into a date after a day), the BSDs' `ps -o lstart` under
  `LC_ALL=C TZ=UTC` and no other environment, null on Haiku, where a lock
  older than the boot (`system_time()`) is stale), taken before the checks,
  so a second run's `failed` is `lock_held` whatever its plan would say,
  and let go just before `end`.  A takeover of a stale lock holds
  `load.lock.takeover` (`O_EXCL`; one older than 10 s is a dead run's)
  from its second look until its own lock is written, so two runs can't
  both take it over; a panic kills the running command's group before
  letting the lock go (review of 5aa58b0).  A stop asked for while a step that isn't
  the archiver's runs lets it end; if that step then fails, the run still
  ends `stopped` (the failure is in the log), never `failed`.  A stop
  asked for during the last step of the last stage comes too late: the
  run ends 0.  `cmd_done.status` is 0 for a `run` step, null for a
  `write`, `check` or `note`; on a failure, `cmd_done` comes before
  `failed`.  `failed.text` is the error as text mode words it after
  `bko-holt load: STAGE: `; `failed.program` is the step's program past
  a leading `chroot DIR` or `env A=B`, and `efi_vars` is a step that
  writes UEFI variables (efibootmgr, or grub-install or grub2-install for
  an `*-efi` target without `--no-nvram`; load's own all pass it) on a
  machine whose `here.efivars` is false: a BIOS grub-install's failure is
  `cmd_failed` (review of 5aa58b0).  `wrong_passphrase` is cryptsetup open's exit
  2 in assemble only: GELI's attach and bioctl's open there always use
  the key just made.  Every child under `--json` also gets SIGINT at its
  default (pre_exec), so a load started where SIGINT is ignored still
  stops its archiver.  The panic hook writes `start` itself when the
  panic came before it, and only for the main thread.
- **Found, not RT10's:** a copy that runs again after a stop or a failed
  archiver (J10's "always runs again"), text mode the same, leaves the
  restore root's own atime and mtime unlike the source's (fsmeta's
  `.`): rustic-bko restoring into a part-restored tree.  Resume and Try
  again inherit it; the boot, the dumps and every other file agree.

### What RT10h and RT10i found

Built 2026-11-24: `testvm/rescue/holt_schema.py`, the fake's rule and
scenarios, demo.rs's `Follow` (all eight changes) and its argv; report-bug's
`--attach` and `--strip-file`, the Collector on bko-holt documents,
`last-load.json` only for writing runs under `--json`, `ISSUES`, the
`--no-redact` line.  Tested by holt's cargo test and golden plans (text
goldens unchanged), redact_dumps over the corpus (its 98 plan documents
too), bko-rescue's cargo test, pty-test.py and demo-test.py.

- **The Check settled.**  report-bug's own `bko-holt dump` of the rescue
  system already lists every disk there (serials, WWIDs, partition labels
  and UUIDs), and the Collector learns it, so a plan document adds little
  when that dump works; it covers a dump that failed and the serials only
  a `by_id` link names.  An attached bko-holt document, and each event of
  an attached events file but `progress`, is learned with the dump's own
  key rules (`serial`, `wwid`, `label`, `hostname`); models are kept, as
  the summary says.
- **Decided:** `--strip-file` strings are replaced anywhere, inside words
  too, as other identifiers (`stripped1`...), never shown on the terminal;
  a line under 3 characters is left (a note says so); the flag is
  repeatable, and ignored under `--no-redact`, whose report replaces
  nothing.  `--no-redact`'s line takes each `Kind::plural` up to its
  parenthesis ("other identifiers").  A `--json` run whose arguments don't
  parse still records itself (no plan or status ran).  report-bug's re-run
  walks load's value flags, so `--snapshot --json` keeps its value.
  `ISSUES` changes the report hint text mode prints after a fatal error
  too, as this spec says ("together"): no test or rig reads it.
- **The demo** passes `--repo DEMO_REPO` (fixed: a typed address may hold
  a password) and `--password-file`, the password typed on its page or a
  fixed one when none was; it stops a run whose `start` doesn't say
  `commit` false and `dry_run` true (either left out included), or once a
  `cmd` comes without `would: true`.
- **The scenarios** are hand-written dry runs that pass the checker, three
  new (`demo-blocked`, `demo-refused`, `demo-stopped`).  Two parts no real
  dry run writes are kept so the screens can be seen: `demo-slow`'s
  progress and `demo-fail`'s failure.  Found: RT10j's plan to record
  `demo-fail` and `demo-slow` from `--commit` runs meets the demo's refusal
  of `commit: true`; those recordings can only be RT12's.
- **The checker** takes 0.2.6 as the first version that writes schema 1
  (bko-holt's version didn't change with RT10, so only `--version --json`
  tells a pre-RT10 one); a golden's `VERSION`, `GENERATED` and
  `PLAN_SHA256` stand-ins; `removable` null (no sysfs file).  Run on the
  built binary's own api, error, plan and status documents and a problems
  stream; a dry run's `would` stream needs root (as a user, `not_root`).
- Clippy runs only on Chopin (1.94; the farm's 1.85.1 has none): `-D
  warnings` fails on lints already there (29 in holt, 11 in bko-rescue,
  mostly `collapsible_if`), none in RT10h's or RT10i's lines.

### What RT10j found

Built 2026-11-24: `HT_VARIANT=json` in ht1.sh and ht1-bsd.sh (their
`# t2-only: json` blocks, `testvm/holttest1d/ht1-json.sh` the second pass,
`ht1-json.py` its checks, README "The --json rebuild"), and the fake's
scenarios recorded from it.  No holt change: bko-holt at 3289bc3 (built
for linux, freebsd, openbsd, illumos and haiku) passed every guest.
Tested by ht1-json-test.py, ht1-bsd-test.sh (78 cases), `t2 self-test`
(but test_gate's gate:holt row budget, which fails at 3289bc3 too), the
inputs of all 99 holt cells compared before and after (none moved), and
one whole run of each of the five, both runs, every check PASS: debian13-stack, freebsd15-geli,
openbsd8-crypt, omnios58, haiku6 (run rt10j2, on chopin, cinqcent,
copland, cage and czerny); a plain debian13 and freebsd15 with the new
drivers PASS as before (no json line).  bko-rescue's cargo test, demo-test.py
and pty-test.py on czerny, and holt_schema.py over the corpus, the
fixtures and every scenario (199 files), pass.

- **The Check settled.**  The OpenBSD softraid guest is
  `openbsd8-crypt`.  `debian13` is BIOS with no LUKS: the Debian 13 guest
  with LUKS and UEFI is `debian13-stack` (LUKS on md RAID, LVM, two
  disks, UEFI), which stood in.
- **Decided:**
  - The `--json` run is staged as the text run is, with its arguments
    and `--json`: before each stage its own `plan --json` (`ok`), then the
    stage with `--expect-plan` of that plan's hash, then `status --json`
    (the stage done, as the text run's check).  Before the stages,
    under `BKO_HOLT_NO_COMMIT=1`: `--version --json` (`no_commit`),
    `--commit --json` refused (exit 2, `no_commit`), the whole run's
    `plan --json`, and the dry run (`--through close --json`): ends 0,
    every command `would`, boot and close among them (J11), the same
    commands as the writing runs then ran, and the targets' and the
    decoy's qcow2 images (md5 on the host after a sync) unchanged.
  - "Its key files are the text run's": in both runs the passphrase file
    and the repository's password are copies in a 0700 directory of the
    rescuer (`/run/ht1-keys`, `ROOT/ht1-keys`), each 0600: the share's
    are 0644 on 9p, which J3's checks refuse, and the same path in both
    keeps the commands alike.  GELI's and softraid's (`/tmp/ht1-geli`,
    a 0700 tmpfs or mfs) passed as they were.  The targets are blank and
    the text run already gives `--erase` for each, so J19 asked for
    nothing more.
  - "The commands in the events must be the text run's": each command
    the events ran, rendered as `Cmd::shell` logs it (the archiver's
    `--json-progress --progress-interval 1s` as `--no-progress`), is in
    the text run's load.log in order (notes and read-backs not looked
    for), and the `--json` run's load.log is the text run's.
  - "The restored `/var/log/bko-holt` must be the text run's but its
    argv lines": read from the booted restored system; but also for the
    archiver's flag and the one status line the log keeps (What differs
    1 and 7), a private write by its size and SHA-256 (checked against
    the text log's text), and output that differs between any two runs,
    text against text too: dd's time and rate, and umount-tree's lines
    naming the rescuer's own processes by pid and mount namespace.
  - "The judge's verdict must be the same": judge.py's DIFF lines and
    its count of differences (and judge.py fs's on Linux).  What it
    ignored, a boot's own fields, may differ: omnios58's text run ignored
    45 fields, its `--json` run 43, with 0 differences each.
  - No Test2 cell is a json one: the drivers' json lines are `# t2-only:
    json` blocks, the json files are `kinds.linux/bsd.variants.json`'s
    harness in holt/spec.json (not in `variants_run`) and in
    policy.unmapped_ignore, so no cell is re-keyed or made due.
- **Found, not fixed (for RT10o and RT12):** on OpenBSD the plan's hash
  changes after the partition stage.  The target's "kernel serial" there
  is its disklabel's DUID (`other/labelled.rs`: `RawDisk.serial` from
  `hw.disknames`), and the partition stage writes the disklabel, the
  source's DUID with it: openbsd8-crypt's whole-run plan before partition
  (a47ed4b4...) isn't the restore stage's plan of the same arguments
  after it (4af3f45e...), sd1's serial null then `52fcb0b7cd1d05e5`.
  RT12's flow (one plan, then one `--through close --commit
  --expect-plan` run) isn't touched: the run checks the hash before its
  first command.  A Resume that passed the first plan's hash to a later
  run would meet `plan_changed`, so it must re-plan there (or the hash
  leave a DUID out off Linux: "as the partition record reads them from
  sysfs" has no answer there; the user's call).  NetBSD reads its disks
  through the same code (untested); Linux, FreeBSD (geom's ident),
  illumos and Haiku kept one hash from partition to close.
- **The scenarios:** `demo` is debian13-stack's recorded dry run (108
  events, 92 commands; delay 0.05 as before, so demo-test sees the
  stages run), the demo's argv kept; demo-test's count and its broken-copy
  cases follow it, and its three `7-done` screens are re-blessed.  Each
  guest's whole run is `rec-GUEST` (18 calls: version, the refusal, the
  plan, the dry run, seven stages and their status documents; 1 MB in
  all), for RT12's tests; the checker reads them all.  `demo-fail` and
  `demo-slow` stay hand-written (the demo refuses `commit: true`).

## Decisions

- **J1. How bko-rescue finds bko-holt's protocol.**  `bko-holt --version
  --json` (*recommended*: `-V` exists, an old bko-holt answers it with
  plain text, which reads as "too old").  Or a new `bko-holt api --json`,
  which an old one answers with exit 2.
- **J2. Choosing a schema.**  One schema per format in version 1, listed
  by the api document; `--json=N` only once a schema 2 exists
  (*recommended*).  Or `--schema N` from the start.
- **J3. How secrets travel.**  Files, as today, 0600 in a fresh 0700 RAM
  directory, and under `--json` bko-holt refuses loose ones
  (`key_file_unsafe`) (*recommended*: no new mechanism, the commands
  shown are an administrator's, it works detached and across resumes).
  Or `--key-fd NAME=FD` (no file of bko-rescue's, but `systemd-run`
  passes no descriptors and bko-holt would write the same file itself).
  Or one secrets document on stdin.
- **J4. Coding the problems.**  A `Problem` type whose unconverted sites
  are `other`, the table's codes done in RT10b and the rest as they're
  touched (*recommended*: text unchanged, small commits).  Or every site
  coded at once (~250 sites, ~2 d more).
- **J5. Who owns the size rules.**  bko-holt gives the figures and whether
  the data fits by fit's margin; the 10 % offer line is bko-rescue's; the
  margin stays fit's (max(1 GiB, 5 %)) until after OtterGrind1, then
  rescue-screens' open question 15 settles one rule, in fit.rs
  (*recommended*: changing the margin now changes text-mode plans).  Or
  adopt 10 % + 2 GiB in fit now.  Or put the 10 % line in bko-holt too.
- **J6. `--erase` in a dry run.**  Allowed under `BKO_HOLT_NO_COMMIT`, and
  D14's lock 1 amended so `holt_args.rs` may add `--erase` but never
  `--commit` in dry-run mode; the dry run's load runs as a direct child in
  a new session, never detached (*recommended*: without `--commit` it
  writes nothing, and the dry run then walks every command on a used disk,
  which J19 makes need `--erase` even on the machine's own disk).  Or D14
  as written: a dry run's writing run onto a used disk stops at
  `not_blank` or `erase_required` and ticks through nothing (the plan
  still lists the commands).  Or bko-holt refuses `--erase` too under the
  variable.
- **J7. Stopping.**  A SIGTERM handler under `--json` only; each child in
  its own process group under `--json`, so SIGINT reaches the archiver's
  group inside an `sh -c`; exit 5 with `end.stopped`; the unit started
  with `KillMode=mixed` and stopped only by SIGTERM to the lock's pid
  (*recommended*).  Or the handler and the groups in text mode too (a
  text-mode change: Ctrl-C would then reach only load).  Or exit 1 with
  `stopped`.
- **J8. One load at a time.**  `RUN_DIR/load.lock` taken by writing runs
  under `--json`, and a text-mode `--commit` refusing while a live one is
  held (*recommended*: the Shell is one key away from a detached load, and
  no rig makes a lock).  Or the lock under `--json` alone, text mode
  ignoring it.  Or text mode taking it too.  Or none (bko-rescue's own
  record only).
- **J9. Text printed as it goes** (ui.md's "worth doing on its own").  Not
  in RT10 (*recommended*: it moves stdout against stderr in every
  text-mode test); a separate change after OtterGrind1.  Or in RT10f.
- **J10. The copy marks.**  `restore.started` and `restore.done` in
  RUN_DIR only, `restore.done` written after the stage's last command and
  read-back; under `--json` status's restore row and `--through`'s skip
  need it, so a stopped or failed copy always runs again; text mode
  unchanged until after OtterGrind1, then the same (*recommended*).  Or
  the mark reported only, with bko-rescue chaining `--only` runs after a
  copy has begun (it can't change `--through`'s skip).  Or a mark on the
  target (in the restored tree: against restore fidelity).  Or change
  text mode now.
- **J11. The dry run's `boot` and `close`.**  Under `--json` without
  `--commit`, the restored-tree check (a `--commit` check in all but
  place) is skipped and both stages print their commands as `would`, as
  the plan does, and the run ends 0 (*recommended*: their commands are
  built from the layout, not the tree, so the dry run shows all of it).
  Or both are `skip` with a `would_need_restore` state, hiding commands
  the plan shows.  Or fix text mode too (a dry `--through close` exits 0
  instead of 1: a text-mode change).
- **J12. `BKO_HOLT_NO_COMMIT`'s values.**  Any non-empty value but `0`
  locks (*recommended*: a typo can't unlock).  Or exactly `1`, as the
  fake checks today.
- **J13. Candidates' contents.**  bko-holt reports each disk's signatures,
  structured, with the space used only where a superblock says, never
  mounting (*recommended*: one place reads disks).  Or bko-rescue runs
  `lsblk`, `blkid` and a read-only mount itself.
- **J14. Resume after a reboot.**  A non-creating `open` stage (RT10o,
  ~2.5 d: new opening builders for every family), with status's `resume`
  deciding when Go on is offered (no lost temporary key, not the old
  system's own disk unrecorded); until it lands, Go on only within the
  session that made the disks and Start again after a reboot, J19's gate
  making either safe (*recommended*).  Or version 1 never gets `open`:
  Start again after every reboot.  Or `open` moved into the core, ahead
  of RT12 (+2.5 d before the screens).
- **J15. The dump's keyboard and time.**  New top-level sections, dump
  schema still 1, judge.py's `IGNORED` updated, after OtterGrind1
  (*recommended*).  Or a schema bump (every older stick then refuses new
  dumps).  And: read users' desktop layouts only with a flag (as
  specified), always, or never.
- **J16. Per disk and grow to fill.**  A new `--fit SRC=PCT|fill`, 1 to 99,
  data deciding, `--fit-largest` kept as it is (*recommended*: no
  text-mode change).  Or raise `FIT_MAX` to 99 and add `--grow-largest
  DISK`.
- **J17. The file count during the copy.**  None in version 1: rustic-bko's
  JSON has none, and the screens drop "N files" (*recommended*).  Or
  bko-holt parses rustic-bko's `Files: N to restore` line.  Or a fork
  patch adding files to its JSON status.
- **J18. The text plan inside the plan document.**  Yes, as `text`, byte
  for byte text mode's plan for the same arguments (its `--no-progress`,
  without the `--json`-only problems), but a `private` write's text
  (*recommended*: Commands needs no second run and can't show another
  plan, and the golden check is exact).  Or `text` rendered from the
  `--json` run (its archiver flag and extra problems), compared with
  `plan.txt` after normalising.  Or bko-rescue runs the text plan again
  for `c`.
- **J19. Never over the old system without `--erase`.**  Under `--json`
  (plan and writing runs alike), every target that isn't blank needs `--erase` unless
  this session's partition record shows load wrote it (`erase_required`),
  and nothing is made anew under a begun restore unless the same run wipes
  first (`remake_after_restore`); the plan shows `will_erase` and `writes`;
  text mode unchanged until after OtterGrind1, then the user decides
  whether it gets the same gate (*recommended*: today `--commit` alone can
  luksFormat the machine's own disk or restore into its old filesystems,
  and the screens' Resume path passes no `--erase`).  Or the gate in text
  mode now too (changes what admins' and rigs' runs onto the source's own
  disk accept).  Or no gate: bko-rescue always passes `--erase` for an
  `ours` disk itself (the safety then rests on the UI alone).
- **J20. Binding the run to what was reviewed.**  `plan_sha256` over the
  commands and the targets' identities, passed back as `--expect-plan`;
  a change is `plan_changed` (*recommended*: covers a layout file, a
  snapshot or a disk that changed between Review and Restore, and a
  kernel name that moved off Linux, which settles open question 2).  Or
  `--erase DEV=SERIAL` with holt rechecking each erased disk's serial and
  size (`target_changed`; disks only).  Or nothing beyond the typed
  serial in bko-rescue.
- **J21. The recovery key's check.**  A new `bko-holt recovery-words OUT
  --json`, the words on stdin, the password written to a new 0600 file,
  a verdict document with positions and suggestions (*recommended*: the
  BIP39 check and the password's form are a compatibility contract that
  stays in one place, holt's `recovery.rs`).  Or amend D2 so bko-rescue
  builds `recovery.rs` and `bip39-english.txt` by `#[path]` as it does
  json.rs.  Or ship `bko-recovery-words` on the images and run it.

## Risks

- **RT10 is about four times what rescue-tui.md counted** (3-4 d, +1 for
  D11).  The core is ~8 d; the rest can follow RT12, with the screens'
  "until then" forms.
- **The erase gate is a new refusal** on the path every restore takes: a
  wrong reading of "blank" or of the record would refuse a restore that
  should run.  It refuses (exit 4, nothing written), never wipes, and its
  tests cover the recorded, unrecorded and blank cases on every family's
  fake system.
- **Process groups under `--json`** change who a signal reaches; a child
  that forks its own helpers (mkfs.btrfs, the archiver's ssh) is in its
  group too, which is what the stop wants.  Text mode is untouched.
- **holt is busy.**  OtterGrind1, its fixers and other sessions edit
  `commands.rs`, `target.rs` and `fit.rs` daily; RT10b's sweep touches
  many of their lines.  Small commits, none during a freeze run, and the
  golden plans as the guard.
- **Streaming children** means reading a child's stdout and stderr at
  once (a thread each) where `wait_with_output` did it for us: a deadlock
  or lost output there fails a stage.  Tests with a child that fills both
  pipes.
- **rustic-bko's progress** isn't versioned: a fork rebase may change its
  JSON.  bko-holt parses it defensively (a line it can't read goes to the
  log, never fails the stage) and a fork test pins the fields
  (`fork-audit.md`'s round).  `--json-progress` with the strict-exit
  variable and `--glob-file`: *Check* at RT10f on a real restore.
- **A changed command line under `--json`** (the archiver's flag) is a
  second code path; HT_VARIANT=json is what keeps it honest.
- **Key groups are new behaviour** in what is asked: a wrong grouping
  asks once for two containers that should differ.  Grouped only where
  crypttab says so, and each name still takes its own `--key-file`.
- **The dump change** (keyboard, time, mount sizes) touches every
  platform's dump and judge.py, which running HoltTest rounds read.

## Open questions

1. **Secrets on Haiku:** `/var/run` isn't in RAM there (ui.md's open
   question 4).  A RAM file system bko-rescue mounts (*Check* Haiku has
   one: `ramfs`), or the repository password passed another way?  Haiku
   has no LUKS or ZFS keys, only that password.
2. **Stable disk names off Linux** for `--map`/`--erase`: `by_id` is null
   there and `path` is used.  J20's plan hash covers it (a kernel name
   that moved changes the hash's serials and sizes, and the run is
   `plan_changed`); open only if J20 isn't taken.
3. **Desktop layouts** in the dump: read `kxkbrc` and dconf under
   `/home` (rescue-screens' open question 12)?  Specified behind a flag.
4. **`stick --json` and `settings.toml`:** a TOML-subset reader in
   bko-holt (rescue-tui's open question 4), or settings in JSON?
5. **A dry run's archiver:** run `rustic-bko restore --dry-run` in the dry
   run's `restore` stage, so the dry run shows the real file count and
   size (it reads the repository only)?  Not specified; it would make the
   dry run's Restore screen show figures.
6. **Signals other than SIGTERM** under `--json`: SIGHUP (a detached load
   has no terminal to lose) and SIGINT left at their defaults?
7. **The events file's size** on a 20-hour copy (~14 MiB): rotate, or
   keep progress to one event every 5 s past an hour?
