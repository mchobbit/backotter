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
  `raw-write`, `write-table`, `rewrite`, `fat-set`, `clear-restricting-flags`).  It dispatches on the first argument alone
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
| `bko-holt stick --copy INSTALL_ID OUT --json` (open question 8) | `bko-holt-stick-copy` | 0, 1, 2 |
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
 "documents": {"plan": [1], "status": [1], "events": [1], "stick": [1], "recovery": [1], "stick_copy": [1], "luks_headers": [1], "error": [1]},
 "dump_schemas": [1],
 "features": ["no_commit_env", "stop", "progress", "candidates", "sizes",
              "open_stage", "fit_per_disk"],
 "platform": {"os": "linux", "arch": "x86_64"},
 "run_dir": "/run/bko-holt", "no_commit": false}
```

- `features` names what this bko-holt has of the later steps, so a screen
  that needs `fit_per_disk` or `open_stage` can say "not on this stick"
  instead of failing.  `no_commit` is whether `BKO_HOLT_NO_COMMIT` is in
  force for this process.
- `dump_schemas` lists the dump schemas this bko-holt's `load` reads
  (`layout.rs`'s `DUMP_SCHEMAS`).  An image build copies it into the
  stick's marker (`marker.py --holt-api`), so the backup tool can warn
  when a stick can't read a machine's dump (open question 11).  A
  bko-holt from before 2026-11-26 has no `dump_schemas`, and reads dump
  schema 1 only.

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
- A `password=` item's value (lilo.conf's, global or an image's, which
  the dump keeps in `boot.lilo`'s options for the copy) is `<password>`
  in everything load prints, in either mode (the text plan, a dry run's
  copy of lilo.conf, a Problem that quotes an item, standard error, every
  document and event) and in the log, text mode's included
  (`redact::scrub_passwords`, RustAudit1 HD1-01).  The dump and the file
  load writes keep it as it is.  A text plan with no password in it is
  unchanged.

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
`report-bug`), `out_unsafe` (`recovery-words`' or `stick --copy`'s OUT
isn't a new file in a private directory, RT10p), and `stick --copy`'s
refusals, exit 1: `config_not_ok`, `no_such_machine`, `two_machines`,
`machine_not_ok`, `no_layout`, `layout_refused` (the copy's own check)
and `copy_failed` ("bko-holt-stick-copy"), and `luks-headers`' refusals,
exit 1: `header_fetch_failed`, `header_mismatch`, and the layout's and
`copy_failed` ("bko-holt-luks-headers").  `text` is the line text mode prints on stderr, without the
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
  with `--through close`.  A stage is a target's where one of its steps
  makes something on a device that sits on the target's source disk, as
  the plan's own device graph says (an md array, LUKS, LVM or a pool on
  several disks is each one's), never by matching names in its argv.
  Review's ERASED list is the targets with
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
nowhere: read through the whole disk's device at the partition's offset by
a FAT reader of bko-holt's own (`src/fatfs.rs`: the dump's `probe/fat.rs`
reads boot sectors only, the *Check* settled at RT10k).

```json
{"format": "bko-holt-stick", "schema": 1, "version": "0.3.0", "generated": "…", "dry_run": false,
 "boot_disk": {"node": "sdc", "found_by": "cdrom_mount"},
 "config": {"state": "ok", "partition": 3, "label": "BACKOTTER", "why": null, "dev": "/dev/sdc3"},
 "image": {"image_id": "6f1c…", "platform": "linux", "built": "…", "system_schemas": [1], "dump_schemas": [1]},
 "stick": {"stick_id": "…", "name": null},
 "here": {"dmi_uuid": "5a6b…"},
 "machines": [
   {"install_id": "0b7e…", "path": "backotter/machines/0b7e…/system.json",
    "state": "ok", "why": null, "schema": 1, "known_schema": true,
    "hostname": "lovelace", "os": {"id": "debian", "version_id": "13"},
    "written": "2026-11-22T01:00:11Z", "sequence": 42, "dmi_uuid": "4c4c4544-…",
    "this_computer": false,
    "repository": {"kind": "sftp", "url": "sftp://nas.local/backotter/lovelace"},
    "repository_id": "a3f9…", "keyboard": null, "time": null,
    "password_hint": null}
 ],
 "notes": [{"code": "settings_ignored", "text": "backotter/settings.toml isn't read yet …"}]}
```

- **The boot disk** (`found_by`): Linux, the disk casper mounted at
  /cdrom (`cdrom_mount`, mountinfo, then sysfs for partition N's start and
  size, as `stick/linux/config-checks.sh`); FreeBSD, NetBSD, OpenBSD and
  DragonFly, the disk the root file system is on (`root_mount`: a GEOM
  label through `glabel status`, a wedge through `dkctl`, a DUID through
  `hw.disknames`, a devfs alias through its link or its device number);
  Haiku, /boot's (`boot_mount`); illumos, whose root is in RAM, the disk
  whose partition N has the marker's size and, in its first sector, its
  label and serial (`marker_scan`), and two such disks are `two_sticks`.
  Partition N is the table's entry N (MBR slot N-1).  The marker is
  `/cdrom/.disk/backotter.json` on Linux, `/boot/.disk/…` on Haiku,
  `/.disk/…` elsewhere.
- `config.state`: `ok`; `missing` (no marker: not started from a stick;
  no partition N: a file-copying writer, or a CD); `damaged` (partition N
  of another size, not FAT, another FAT serial or label, stick.json
  missing, not JSON or for another image: repartitioned, reformatted or
  damaged); `two_sticks` (illumos: two disks match the marker);
  `unreadable` (not root, or the disk can't be opened).  `why` says which,
  in words, null with `ok`.  Not `ok`: `stick` is null and `machines`
  empty.  `dev` is partition N's own device where the system names one
  (`/dev/sdc3`, `/dev/da0s3`, `/dev/dk2`, `/dev/sd0i`,
  `/dev/dsk/c2t0d0s3`, `/dev/disk/usb/0/0/2`), for bko-rescue's restore
  logs; null when it can't tell.
- `machines[]`, one per directory of `backotter/machines/` named by an
  install ID (a UUID: any other directory is left out, by count in the
  note `machines_not_install_ids`, its name never in a path), sorted by
  name, at most 64 (more: note `machines_over_limit`).  `state`: `ok`,
  `checksum_bad` (the file doesn't match its `.sha256`, or that isn't
  `sha256sum`'s line), `checksum_missing`, `schema_unknown` (a schema
  this bko-holt or the image's `system_schemas` doesn't read),
  `unreadable` (missing, over 64 MiB, not JSON, not `backotter-system`,
  its `machine.install_id` missing or not its directory's (named in `why`
only when it is a UUID), a string field over 4 KiB
  or a `keyboard`/`time` section over 16 KiB); any but `ok` can't be
  chosen, and `why` says why.  A damaged copy that still parses gives its
  fields (the hostname it names), so the screen can name it.
  `this_computer` compares `dmi_uuid` with `here.dmi_uuid` (case aside),
  when that names one computer: not a firmware placeholder (all zeros,
  all Fs, not a UUID, or `stick.rs`'s `DMI_UUID_PLACEHOLDERS`), and only
  when one row matches; two or more (cloned machines, or one reinstalled
  under a new install ID) are none, with the note
  `this_computer_ambiguous`, so nothing wrong is preselected.
  `here.dmi_uuid` is Linux's (sysfs, as read), null elsewhere, as the
  plan's `here`, so off Linux no row is "this computer" yet.
- `keyboard` and `time` are system.json's top-level sections of those
  names passed on as they are, null until the backup tool writes them
  (RT10l's dump sections): the screens then say US English and UTC.
  `password_hint` is system.json's top-level `password_hint`
  (rescue-screens' open question 2; the key is `stick.rs`'s `HINT_KEY`).
- `settings.toml` overrides need a TOML reader in bko-holt (open
  question 4).  Until that's decided, `stick --json` ignores
  `settings.toml` and says so in a `notes` entry (`settings_ignored`):
  `stick.name` is null (the name is settings.toml's) and no
  `repository_url` override is applied (decided at RT10k, as written here).
- Exit 0 when `config.state` is `ok`, 1 when not (the document says why),
  2 for bad arguments (`bko-holt-error`).  Allowed under
  `BKO_HOLT_NO_COMMIT` (it reads only).  `--sysroot DIR` reads a Linux
  tree instead, for tests (as dump's).
- The layout itself isn't in the document: `stick --copy` writes it to a
  file for load (below; open question 8, answered).
- `image` is what the marker says: `system_schemas`, and `dump_schemas`
  (the dump schemas the image's bko-holt load reads, open question 11),
  null when the marker is from before markers named them.  Such an image
  reads dump schema 1 only (`stick.rs`'s `DUMP_SCHEMAS_UNNAMED`).
  Informational: the running bko-holt is the image's, and `stick --copy`
  refuses a layout it can't read (`layout_refused`).
- `password_hint` is text the user wrote and may name things a report
  shouldn't hold: bko-rescue gives it to report-bug as text to strip
  (`--strip-file`, RT10i: a file of strings the Redactor replaces as
  "other identifiers"), and never writes it into its own log or a stick
  document.

### `bko-holt-stick-copy` (open question 8)

`bko-holt stick --copy INSTALL_ID OUT --json` (`--sysroot DIR` as
`stick`'s): the disk layout machine INSTALL_ID's system.json carries,
written to OUT for `load`.  It finds the config partition as `stick
--json` does, but reads and judges only the directories named by the ID
(the other machines are counted, not read).  It keeps the bytes of that
system.json, checks its `.sha256` on them, and parses the dump from those
same bytes: the stick isn't read again between the check and the write.

```json
{"format": "bko-holt-stick-copy", "schema": 1, "version": "0.3.0", "generated": "…", "dry_run": false,
 "install_id": "0b7e…", "path": "backotter/machines/0b7e…/system.json",
 "out": "/run/bko-rescue/s-1-0/layout.json", "bytes": 55497,
 "sha256": "…", "system_sha256": "…", "luks_headers": 0}
```

- What OUT holds: system.json's top-level `layout`, the whole `bko-holt
  dump` document (stick-config.md's system.json; a default chosen here,
  open question 10), printed again as dump prints it (json.rs's
  `pretty`).  It is the same JSON as the checked bytes, not always the
  same bytes: OUT is byte for byte the dump only when system.json holds
  it as dump printed it.  So OUT is a dump, and `load` takes it as it
  takes any: load, text mode and `report-bug` are unchanged.  `bytes` and
  `sha256` are OUT's, `system_sha256` the checked system.json's.
- Its own check: the `layout` must be a document `load`'s
  `Layout::from_json` takes (format `bko-holt`, a schema it reads, a
  supported OS, a dump made as root) before OUT is written.  A layout
  that kept LUKS2 headers (`storage.luks_headers` not empty: an Ubuntu
  Core's encryption, or `dump --keep-luks-header`) is copied, and
  `luks_headers` counts them: bko-rescue then fetches them from the
  backup ("bko-holt-luks-headers", open question 12).  One whose list
  couldn't be fetched (a file name dump never makes, two the same, no
  SHA-256, a size no header has: `luksfetch::entries`) is
  `layout_refused`, said at the copy, not at the first plan.
- The machine: INSTALL_ID must be a UUID (else exit 2, `bad_args`, not
  echoed), matched case aside among the machine directories `stick
  --json` would list.  `--copy` takes its two values whatever they are:
  `--copy ID -h --json` is `bad_args`, not the help.
- OUT is checked before the stick is read, as `recovery-words`' OUT
  (`recoverycmd::out_unsafe`): absolute, not there yet, in a real
  directory of bko-holt's effective user with no group or other bits;
  else exit 2, `out_unsafe`.  It is written `O_CREAT|O_EXCL`, mode 0600,
  synced; a write that fails after OUT was made is zeroed and removed.
- Refusals are `bko-holt-error` documents, exit 1: `config_not_ok` (the
  config partition isn't `ok`: its state and why), `no_such_machine`,
  `two_machines` (two directories named by the ID, case aside),
  `machine_not_ok` (its row isn't `ok`: `checksum_bad`,
  `checksum_missing`, `schema_unknown`, `unreadable`, with its why),
  `no_layout` (no `layout`, or one that isn't a document: a system.json
  of today; one the backup tool wrote without its layout for the stick's
  room says so in the words, from its `layout_left_out`, open question
  16), `layout_refused` (load wouldn't take it, or its kept LUKS
  headers can't be fetched), `copy_failed` (OUT couldn't be written: nothing left there,
  or, when a file appeared there after the check, that file left as it
  is).
- Exit 0 when OUT is written, 1 refused, 2 bad arguments.  Read-only
  toward every disk; allowed under `BKO_HOLT_NO_COMMIT`.  Without
  `--json` it refuses (exit 2), as `stick` does.
- The layout names hosts, disks and paths as a dump does; no secret is in
  it (stick-config.md: no secret ever goes in system.json).  bko-rescue
  keeps OUT in the session's 0700 directory, removed with it.

### `bko-holt-luks-headers` (open question 12)

`bko-holt luks-headers LAYOUT DIR --repo REPO --password-file FILE
[--snapshot ID] [--archiver PROGRAM] --json`: the LUKS2 headers the dump
LAYOUT kept (`storage.luks_headers`: `dump --keep-luks-header`, and an
Ubuntu Core's encrypted partitions always), fetched from the backup into
DIR for `load LAYOUT --luks-header-dir DIR` (`src/luksfetch.rs`).

```json
{"format": "bko-holt-luks-headers", "schema": 1, "version": "0.2.6", "generated": "…", "dry_run": false,
 "layout": "/run/bko-rescue/s-1-0/layout.json", "dir": "/run/bko-rescue/s-1-0/h",
 "snapshot": "a7f3c901", "repo_dir": "/.bko-holt/luks-headers",
 "headers": [{"device": "vda5", "label": "ubuntu-data-enc",
   "file": "luks2-header-bc000000-0000-4000-8000-00000000000c.img",
   "path": "/run/bko-rescue/s-1-0/h/luks2-header-bc000000-0000-4000-8000-00000000000c.img",
   "from": "a7f3c901:/.bko-holt/luks-headers/luks2-header-bc000000-0000-4000-8000-00000000000c.img",
   "bytes": 16777216, "sha256": "…"}]}
```

- Where they are: in the backup, in the same snapshot as the system's
  files, at `/.bko-holt/luks-headers/FILE` (`luksfetch::REPO_DIR`, beside
  Qubes's `/.bko-holt/raw`), FILE the name dump gave it.  The backup tool
  puts them there (stick-config.md, "LUKS2 headers in the backup"); the
  repository is encrypted.  load's restore leaves that directory out of
  the tree (`!/.bko-holt/luks-headers/**`, `!/.bko-holt/luks-headers`, and
  `!/.bko-holt` when no raw volume is under it, in its glob file, whenever
  the layout kept a header): key material never lands on the restored
  disks, nor the backup tool's directories.
- How: for each header, the archiver's `dump` of that one file, read-only
  toward the repository and every disk: `rustic-bko --repository REPO
  --password-file FILE --no-progress dump SNAPSHOT:/.bko-holt/luks-headers/FILE`
  (restic-bko's takes `SNAPSHOT PATH`; a `SNAPSHOT:SUBPATH` snapshot puts
  SUBPATH first, as load's raw volumes do).  `from` is what the dump was
  given.  At most the dump's `bytes` and one more are read (256 MiB when a
  dump from before `bytes` has none), into a buffer of at most 1 MiB at
  first that grows by copying, each old copy zeroed; the last is zeroed
  after.  An allocation that fails is `header_fetch_failed`, not an abort.
- The check, each file before it is kept (`luksfetch::check_bytes`, the
  shared check): the dump's `bytes`, then its `sha256`.  A header that
  isn't the dump's (another snapshot's: the stick's layout is the newest
  backup's) is `header_mismatch`; one the archiver couldn't read (no such
  file, a wrong password, the repository not there) is
  `header_fetch_failed`, with the last line the archiver said (a URL's
  password redacted).
- DIR is checked before anything is read, as `stick --copy`'s OUT
  (`recoverycmd::out_unsafe`): absolute, not there yet, in a real
  directory of bko-holt's effective user with no group or other bits;
  else exit 2, `out_unsafe`.  It is made 0700, each header in it
  `O_CREAT|O_EXCL`, 0600, synced.  Any failure leaves nothing: what was
  written is zeroed and removed, and DIR too.  A layout that kept none:
  exit 0, DIR made, empty.
- Under `--json` the password file is required (nothing asks) and
  checked as load checks key files ("Secrets" 4); a `--repo` with a
  password in it is refused (exit 2, `bad_args`).  The layout must be one
  load takes (`layout_unreadable`, `layout_not_json`, `layout_refused`,
  exit 1), and its list one that can be fetched (`layout_refused`).
  Without `--json` it is the same, a line a header on stdout (a restore
  by hand), and the archiver may ask for the password.
- Exit 0 when every header is in DIR, 1 refused or failed (nothing left),
  2 bad arguments.  It writes no disk: allowed under `BKO_HOLT_NO_COMMIT`.
  `RLIMIT_CORE` is 0 for it and the archiver.
- The backup tool runs the same command on the snapshot it just made, as
  its read-back: the headers that snapshot holds are the ones its layout
  names.

### `bko-holt-recovery` (J21, RT10p)

`bko-holt recovery-words OUT --json`, the typed words on stdin (a pipe
from bko-rescue, at most 1 KiB): the recovery key screen's check, by
`recovery.rs`'s `check`, and the repository password it stands for, by
`recovery::password` (a compatibility contract that stays in holt).

```json
{"format": "bko-holt-recovery", "schema": 1, "version": "0.3.0", "generated": "…", "dry_run": false,
 "ok": false, "words": 12, "expected": 12, "problem": "unknown_words",
 "unknown": [{"position": 7, "short": false, "suggest": ["ribbon"]}],
 "checksum_ok": null, "written": false, "error": null}
```

- `problem`: null with `ok`, else `unknown_words` (`unknown` lists them:
  the position among the words typed, from 1; `short`, fewer than 4
  letters and the start of several words; `suggest`), `count` (all on the
  list, but `words` isn't `expected`), `checksum` (twelve list words that
  don't add up: `checksum_ok` false).  `checksum_ok` is true with `ok`,
  null when the words never got that far.
- With `ok`, the password goes to OUT: a new file (`O_CREAT|O_EXCL`,
  0600) in a directory of bko-holt's effective user with no group or other
  bits (bko-rescue's `k/`), no trailing newline; `written` true.  Without
  `ok`, nothing is written.  The words and the password never appear in
  the document.  `suggest` is at most three list words, by edit distance,
  only for a word not on the list.  A write that fails after `ok` is exit
  1 with `written` false and `error` saying why (the file removed).
- OUT is checked before a word is read (an OUT that isn't absolute isn't
  echoed: it may be a word typed in its place): an OUT that isn't absolute, is
  there already, or is in a directory that isn't a real one of ours with
  no group or other bits is exit 2, `bko-holt-error` code `out_unsafe`.
  A terminal on stdin, or more than 1 KiB, is exit 2, `bad_args`.
- Exit 0: `ok` and written; 1: not a recovery key (or the write failed);
  2: bad arguments.  Without `--json` it refuses (exit 2): the hand tool is
  `bko-recovery-words`, which isn't shipped.
- Allowed under `BKO_HOLT_NO_COMMIT` (it writes no disk).  It sets
  RLIMIT_CORE to 0 before it reads a word (Secrets, item 10), and reads
  them unbuffered into one buffer it wipes.
- `suggest` names list words near a mistyped one, so it gives most of a
  word of the key away: bko-rescue keeps the recovery document out of its
  logs and bug reports, as it does the words.
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
12. **The LUKS2 headers a dump kept** are key material too: with an old
    passphrase, a header opens its volume.  `luks-headers` writes them
    only to a new 0700 DIR, each 0600 (open question 12); bko-rescue's DIR
    is `h/` in its session's directory, and it wipes them as it wipes the
    key files (each zeroed to its length, then unlinked; the session's
    wipe goes into `h/`, and removes a link there without following it).
    Another snapshot fetches them again, the old ones wiped first.  load's
    restore never writes the backup's copies into the restored tree.

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
  it into a report.  A `password=` item's value is `<password>` wherever
  it is in a report (the layout's and the dump's `boot.lilo` options, the
  plan, a log an older bko-holt wrote), `--no-redact` or not (RustAudit1
  HD1-01).

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
| `src/stick.rs` (new) | `stick --json` (RT10k); `stick --copy` (open question 8) |

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

**RT10j's review (2026-11-24).**  Re-run on the five (run rt10jr2,
openbsd8-crypt rt10jr3) with bko-holt built from main after RustAudit1's
commits, and slackware15 for a private write:

- **The `rec-*` scenarios replay now.**  As first recorded, the fake (first
  match wins) answered every `status --json` with the first stage's, and
  each stage's `--expect-plan` was a plan the recording left out, so a
  staged replay met exit 98 from the partition stage on.  `ht1-json.py
  record` keeps each stage's `plan --json` before it; the fake's
  `FAKE_BKO_HOLT_STATE` answers one argv in turn by each recording of it;
  `ht1-json.py replay` walks a scenario as a staging client would, each
  `--expect-plan` the hash of the plan just before, and record and
  ht1-json-test.py replay every one (26 calls each).  RT12's own flow (one
  plan, one `--through close --commit` run) and a real `failed` or
  `stopping` stream are still unrecorded: RT12's.
- **The decoy under `--json`:** the json pass maps it, without `--erase`,
  in the first target's place (`load ... --through close --json` under
  `BKO_HOLT_NO_COMMIT=1`): start, `problems` with its `not_blank`, `end` 4,
  no command, the images unchanged.  Each `rec-*` has it (`refuse-decoy`).
- **No PASS without the `--json` checks:** `HT_KEEP=1`, or a verify alone
  after a json pass that didn't pass, was a PASS with none of them run; a
  json run now FAILs unless `logs/json/rebuilt` (a rebuild phase) and
  `logs/json/verified` (a verify phase) are there.
- **logcmp** holds the json log to the archiver's last status line (no
  more status lines than archiver runs) and, given the writing runs'
  events, to a size-and-SHA-256 line for each write they mark `private`.
- **A holt bug, fixed** (`target::identity_on`): RustAudit1 HD2-04
  (a4e9714) named an off-Linux target in its partition record by the
  scan's serial, which on OpenBSD and NetBSD is the disklabel's DUID, the
  one the partition stage writes, so openbsd8-crypt's text run stopped at
  partition ("no record that load wrote it").  The record leaves the DUID
  out there, as before; the plan hash keeps it (the question above).

### What RT10k and RT10p found

Built 2026-11-25: `bko-holt stick --json` (`src/stick.rs`, a read-only FAT
reader `src/fatfs.rs`, `other::load::raw_disks` taken out of `scan` for
it) and `bko-holt recovery-words OUT --json` (`src/recoverycmd.rs`);
`stick` and `recovery` in the api document's `documents`, `stick` and
`recovery_words` in its `features`, the error code `out_unsafe`.  Tested
by unit tests (FAT16 images with long names, chains that loop or leave
the volume, every config and machine state, FreeBSD's root by its GEOM
label, illumos' marker scan and two sticks, the device names off Linux;
the recovery verdicts on BIP39's vector, OUT's checks), goldens in
`holt/tests/fixtures/stick-json` and `recovery-json`, `tests/json_cli.rs`
(both as programs, under `BKO_HOLT_NO_COMMIT` too), the gate's cargo test
and golden plans (110 plans, 0 differ) on cinqcent, and holt_schema.py
(the two documents added) over the corpus, the fixtures and every
scenario.  The fake answers `recovery-words` by the words on its stdin and
writes OUT as bko-holt does (its `stdin` and `out` files); the `stick-*`
scenarios are recorded by `testvm/rescue/record-stick.py` from a real
bko-holt on config partitions `stick/marker.py` made (256 MiB FAT16, as the
Linux image's) with machines put on by mtools, for RT11.

- **The Check settled:** `probe/fat.rs` reads boot sectors only; the
  directories and files are fatfs.rs's (FAT12, 16 and 32, long names by
  their checksum, every cluster and size checked, each read limited).
- **Decided here** (each in one place, for the user to change): exit 1
  when the config isn't `ok`; a fifth `config.state`, `unreadable` (not
  root, a disk that won't open); `config.dev` and `image` added;
  `password_hint`, `keyboard` and `time` read from system.json's top level
  (`stick.rs`'s `HINT_KEY`; the sections null until the backup tool
  writes them); at most 64 machines, 64 MiB a system.json, 4 KiB a field,
  16 KiB a section (`stick.rs`'s consts); a damaged copy that parses still
  names itself; recovery's `problem`, `expected`, `short` and `error`; at
  most 3 suggestions (`recoverycmd.rs`'s `SUGGEST_MAX`); without `--json`
  both commands refuse (no text form).
- **Found, not fixed (for RT11, RT12 and the backup tool's spec):**
  - *The layout's copy* (answered 2026-11-26: open question 8, "What
    stick --copy found").  "bko-rescue copies system.json to
    /run/bko-rescue/layout.json", but D11 put the FAT reading in bko-holt
    so bko-rescue needn't: it can mount `config.dev` read-only (it mounts
    it read-write for restore logs anyway) and copy the file, which then
    isn't the bytes bko-holt checksummed; or `stick` grows a `--copy
    INSTALL_ID OUT` that writes the checked bytes to a new private file.
    And `backotter-system` isn't a layout load takes (`format` `bko-holt`):
    how system.json carries the dump is the backup tool's to say.  RT12
    needs both answered.
  - *The recovery key screen's completion.*  The screen fills in a word
    after four letters and names a word that can't be on the list as it is
    typed: that needs BIP39's list in bko-rescue, which J21 kept in holt.
    The list isn't secret: `recovery-words --list` (the 2048 words on
    stdout), or `bip39-english.txt` taken by `include_str!` (D2 amended for
    a data file).  The check itself stays here.
  - *This computer off Linux.*  `here.dmi_uuid` is Linux's only, as the
    plan's: FreeBSD's and DragonFly's `kenv smbios.system.uuid`, NetBSD's
    `machdep.dmi.system-uuid`, OpenBSD's `hw.uuid` and illumos' `smbios
    -t 1` would give it; until then no row is "backed up from this
    computer" off Linux.
  - *The image boot-test* the Steps table names isn't run: no image has
    this bko-holt yet (the next image round's boot-test can run `bko-holt
    stick --json` in `stick/*/config-checks.sh` beside its checks).
    marker.py's partition with mtools' long names stood in.

### What the RT10k and RT10p review found

Reviewed 2026-11-25 (8003d22, 05e1a65); fixed in "review fixes for
RT10k+p":

- **Fixed:** `recovery-words --json` sets RLIMIT_CORE to 0 itself (it
  relied on bko-rescue's unit's LimitCORE), reads standard input through
  a copy of descriptor 0 into a buffer sized once (Stdin's own buffer and
  a growing Vec left copies of the words), and `near`'s copies of a typed
  word are wiped; a single word given as OUT is refused without being
  echoed.  `stick`: a machine directory not named by a UUID is left out
  (note `machines_not_install_ids`; a crafted `../../etc` reached
  `path`), a system.json without `machine.install_id` is `unreadable`,
  and a mismatched one is named only when it's a UUID (a 200 KB one made
  a 202 KB document); "this computer" ignores firmware placeholder UUIDs
  and is none when several rows match (`this_computer_ambiguous`);
  illumos' marker scan reads the serial and label at FAT32's offsets too
  (`fatfs::boot_ids`) and compares the label case aside, as
  `check_identity` does.  Tests: FAT12 and FAT32 images (a root chain,
  clusters past 65535), the directory and install-ID cases (golden
  `not-install-ids`), placeholders and two matches; holt_schema.py checks
  a machine's install_id is a UUID, its path under it, and at most one
  `this_computer`; the fake takes a word's first 4 letters or more as
  bko-holt does; a recorded `stick-two-here`.
- **Left:** the plan's `here.same_machine` has the same placeholder
  weakness (text mode's plan isn't touched by it; for RT12); the test
  image builders and samples stay compiled into the library, as
  json_cli.rs needs them (`#[doc(hidden)]`); open questions 8 and 9 (the
  layout's copy, the word list) block RT12 and RT11's Recovery key
  screen (8 answered 2026-11-26: "What stick --copy found").

### What stick --copy found

Built 2026-11-26 (open question 8, approved that day by the user): `bko-holt
stick --copy INSTALL_ID OUT --json` (`src/stick.rs`'s `copy_layout`; the
scan keeps the asked machine's system.json bytes as it judges them),
`stick_copy` in the api document's `documents` and `features`, six new
error codes, and load's refusal of a `backotter-system` document naming
`stick --copy`.  bko-rescue's layout comes from it (rescue-tui.md, "What
stick --copy changed").  Tested by unit tests (the machine asked for of
two, case aside; no such machine; two directories of one ID; a checksum
that doesn't match; no .sha256; not JSON; no layout; a layout that isn't a
document; one load refuses; the config missing, reformatted, not root; OUT
there already and in no directory; `--json` alone keeps no bytes), goldens
in `holt/tests/fixtures/stick-copy-json`, `tests/json_cli.rs` (as a program,
under `BKO_HOLT_NO_COMMIT` too; OUT's checks; load's plan of OUT the same as
of the dump itself, `--json` and text), holt_schema.py, and the gate on
cinqcent (cargo test 954 passed, 0 failed, 4 ignored; 110 golden plans, 0
differ; redact-corpus passed).  `testvm/rescue/record-stick.py` records `stick-layouts` (its
lovelace carries holt/tests/corpus/debian13-stack's dump; babbage none;
hopper a wrong checksum; and a machine not there) and stick-three's
`08-copy` (no layout).  OUT came out byte for byte the corpus dump.  Then
`real-dry-run.py` ran in debian13-stack's HoltTest rescuer on chopin with a
real stick image (marker.py's config partition on a loop device, its
medium at /cdrom): `stick --json` found it, `stick --copy` wrote the
share's layout byte for byte, the dry run would have run 92 commands, and
every disk and the stick image kept its md5.

- **Decided here** (defaults where the specs were open, each in one place):
  - system.json carries the dump whole, as its top-level `layout`
    (stick-config.md's system.json, where the backup tool's spec was
    open); OUT is that dump, printed as dump prints it.  Nothing in load,
    its text mode or report-bug changes: it is a dump.
  - The copy's own check is load's `Layout::from_json`; a layout it
    refuses is `layout_refused`, exit 1, and OUT isn't written.
  - Refusals after the arguments are exit 1 with the codes
    `config_not_ok`, `no_such_machine`, `two_machines`, `machine_not_ok`,
    `no_layout` and `copy_failed` (`jsonout.rs`'s `ErrorCode`).
  - The install ID is matched case aside (FAT keeps the case it was
    given); two directories that match are refused, not chosen between.
  - The document's fields: `install_id`, `path`, `out`, `bytes`,
    `sha256`, `system_sha256`.  No text form.
  - `load` given a system.json refuses it as before (`layout_refused`),
    its words now naming `stick --copy`.
- **Spec problems found (for the backup tool's spec):**
  - stick-config.md left `storage` and `boot` as the system description
    "with its own design doc", never written.  The dump has both, inside
    `layout`; the envelope's two stay placeholders.
  - A dump made with `--keep-luks-header` (Ubuntu Core's encrypted
    partitions always) needs its header files beside it ("load looks for
    them there"; `load --luks-header-dir`).  The stick carries none, so such
    a layout can't be restored from the stick's copy alone.  (Open
    question 12, built 2026-11-26: they are fetched from the backup.)
  - A system.json is read up to 64 MiB (`stick.rs`'s `SYSTEM_MAX`), a
    dump up to 1 GiB (`LAYOUT_MAX`), and the BSD images' config partition
    is 32 MiB: a large dump (ESP images) or several machines may not fit.
    (Open question 13, built 2026-11-26: 256 MiB on every image, and the
    backup tool's `room_check`.)
  - "Which copy wins" (the backup's own system.json when the repository is
    reached) isn't built: the stick's copy is the only layout bko-rescue
    has.
- **Review fixes** (2026-11-26, two reviews):
  - `stick --copy` reads only the asked machine's system.json; the other
    directories are counted, for `no_such_machine`'s words.  `stick
    --json` still reads them all (open question 13 on their size).
  - A layout that kept LUKS2 headers is `layout_refused`, said at the
    copy (open question 12).
  - `--copy`'s two values are never taken for the help.
  - `copy_failed` says "left as it is" when a file appeared at OUT
    after the check (O_EXCL), not "nothing left there".
  - The words on OUT: the same JSON as the checked bytes, printed again,
    not always the same bytes.
  - Open: where system.json carries the dump (question 10), the dump's
    schema in the marker (11), LUKS headers (12), room on the partition
    (13).
  - Not fixed, with why: the copy isn't tied to the scan the Machine
    screen showed (only the install ID is compared).  Both read the boot
    stick, and a stick swapped for another set up for the same machine
    in the middle of a session is unlikely.  A check would need
    `sequence` or `written` in the copy document.  A write that fails after OUT is made (zeroed and
    removed) has no test: it needs a full disk or an I/O error, and the
    code is recoverycmd's shared `write_new`.

### What the marker's dump_schemas built

Built 2026-11-26 (open question 11, approved that day by the user):

- bko-holt: `layout.rs`'s `DUMP_SCHEMAS` (`[1]`), which load's schema
  check now reads, and the api document's `dump_schemas`.  `stick --json`'s
  `image` gives the marker's `dump_schemas` (null when it has none).
- The write-time check: `stick.rs`'s `schema_warnings(marker, system)`,
  in the bko_holt library.  It gives `system_schema_unread` when
  system.json's schema isn't in the marker's `system_schemas`, and
  `dump_schema_unread` when `layout.schema` isn't in the marker's
  `dump_schemas`; each with words for the user.  The backup tool isn't
  built: this is the check it must call before each write, and it still
  writes after a warning (stick-config.md, "Is this stick good enough?").
  It has no command of its own; one comes with the backup tool if that
  isn't Rust.  Only a `layout` that is an object is a dump here (as for
  `stick --copy`, whose `no_layout` covers the rest), and only its
  `schema` is checked, not its `format`: load's `FORMATS` check refuses
  a foreign format at the restore (`layout_refused`).
- `marker.py --holt-api FILE` (required): the image's own `bko-holt
  --version --json`.  It refuses one with no `dump_schemas` (a bko-holt
  from before them), since that image would fail the boot check
  `dump-schemas`: refresh holt-bin first.  `--holt-api-unnamed-ok` takes
  it as reading dump schema 1; only `record-stick.py` passes it, for
  recordings of an older bko-holt.
- `build-backotter-layer.sh` runs the layer's bko-holt in its chroot
  (on a host of the layer's architecture) and writes
  `ubuntu-server-minimal.backotter.holt-api.json` beside the layer; it
  stops if that names no `dump_schemas`.  `make-img.sh` reads that file
  and runs nothing of the layer, so an arm64 image can still be made on
  an amd64 host (Chopin, as A4 did: Bigfoot has no mtools).  It checks
  the file's `platform.arch` is the image's.  A layer from before the file:
  make-img.sh runs its bko-holt, which needs a host of its architecture,
  and otherwise says to rebuild the layer.  `make-img-bsd.sh` runs
  `in/bko-holt` in the build VM, before the long assemble step.
  `record-stick.py` passes the bko-holt it records.
- Each platform's `checks.sh` has `dump-schemas`: the marker's list
  equals the running bko-holt's.  None on both sides passes only when the
  marker has no `dump_schemas` key: an image from before ae051fb, whose
  marker and bko-holt name none.  So the images in use today still pass
  ImgTest2All.  Linux, NetBSD and OpenBSD type it as one line;
  FreeBSD (`/tmp/bko-ds.sh`), DragonFly, illumos and Haiku (an arm of
  `/tmp/bko-checks.sh`) run it from a helper, so no typed line grows.
- **Defaults chosen** (each in one place): a marker or a bko-holt that
  names no dump schemas reads dump schema 1 only
  (`DUMP_SCHEMAS_UNNAMED` in `stick.rs` and `marker.py`; every bko-holt
  before this read only schema 1).  An image build refuses such a
  bko-holt (above).  `dump_schemas` in the api document and the marker is a
  list, like `system_schemas`.  The warning codes and words are
  `schema_warnings`'s.  The boot check passes none on both sides (an
  old image), and nothing else that differs.
- **Tested:** `stick.rs`'s unit tests (the marker's field, old markers,
  every warning and its words), the `stick-json` goldens re-blessed for
  the one new member, `jsonout.rs`'s and `tests/json_cli.rs`'s api
  checks, `testvm/test2/tests/test_marker.py` (marker.py's field, its
  fallback and refusals; every platform's boot check as `sh -c` runs it),
  `test_arm64_image.py` (make-img.sh with a bko-holt that prints its api
  document), holt_schema.py over the scenarios and goldens, and the holt
  gate on cinqcent (cargo test 956 passed, 0 failed, 4 ignored; 110
  golden plans, 0 differ; redact-corpus passed).
- **Not done:** no image was rebuilt.  The next image round must use a
  holt-bin with this bko-holt (img-refresh-holt.sh) so the marker's
  `dump_schemas` comes from bko-holt itself, and its boot tests then run
  `dump-schemas` on every platform.  Images built before it have no
  `dump_schemas`: `schema_warnings` reads them as `[1]`, and the boot
  check passes them as "marker none, bko-holt none".  The Linux layers
  must be rebuilt with this build-backotter-layer.sh (it writes the
  holt-api.json that make-img.sh reads); an arm64 layer's holt-api.json
  goes to the image host with its other inputs.
- **Review fixes** (2026-11-26, "holt, stick: review fixes for oq11"):
  make-img.sh no longer runs the layer's bko-holt (the layer's
  holt-api.json instead, above), so the arm64 image builds where A4
  built it; the boot check passes the images in use today; marker.py
  refuses a bko-holt with no `dump_schemas` at once, not at the boot
  test; the long check lines moved into helpers; a non-object `layout`
  gets no dump warning; load's refusal lists `DUMP_SCHEMAS`; a test ties
  dump's `SCHEMA` to `DUMP_SCHEMAS`; and a unit test pins
  `image.dump_schemas` as null for an old marker.
- **Spec problem:** `marker.py`'s `system_schemas` is still its own
  constant, not bko-holt's `stick.rs` `SYSTEM_SCHEMAS`.  The api document
  could carry both; left as it is.

### What the LUKS headers' fetch built

Built 2026-11-26 in 3100b25 (open question 12, approved that day by the user, the
recommendation):

- **Where they live:** in the backup, in the same snapshot as the system's
  files, at `/.bko-holt/luks-headers/FILE` (`luksfetch::REPO_DIR`), FILE
  the name dump gave each.  The snapshot doesn't hold them today: dump
  writes them to RAM (`/run/bko-holt/luks-headers`, or
  `--luks-header-dir`), never into a backup's tree, and HoltTest's
  uc24-fde and aeon runs copy them beside the layout in the share by hand.
  The backup tool must put them there (stick-config.md, "LUKS2 headers in
  the backup", steps 1 to 4); it isn't built.
- **bko-holt:** `bko-holt luks-headers LAYOUT DIR --repo REPO
  --password-file FILE [--snapshot ID] [--archiver PROGRAM] [--json]`
  (`src/luksfetch.rs`; "bko-holt-luks-headers").  bko-holt does the
  repository reads: it runs the archiver's `dump` of each file
  (`rawvol::dump_argv`, as load's raw volumes), checks it
  (`luksfetch::check_bytes`: size, then SHA-256; `luksfetch::entries`
  checks the list itself), and writes it to a new 0700 DIR, 0600 each.
  The same command is the backup tool's read-back of a new snapshot.
  `luks_headers` in the api's `documents` and `features`; two new error
  codes, `header_fetch_failed` and `header_mismatch`.
- **load:** the restore leaves `!/.bko-holt/luks-headers/**` out whenever
  the layout kept a header (its glob file, with its own `why`), so the
  backup's copies never land in the restored tree.  Since the review
  fixes, also `!/.bko-holt/luks-headers` and, with no raw volume under it,
  `!/.bko-holt` (below).  A missing header's
  problem now names `bko-holt luks-headers`.  `--luks-header-dir` is as it
  was.
- **stick --copy:** the interim refusal of a non-empty
  `storage.luks_headers` is gone.  The document has `luks_headers`, the
  count; a list that couldn't be fetched is still `layout_refused`.
- **bko-rescue:** rescue-tui.md, "What the LUKS headers' fetch changed".
- **Defaults chosen** (each in one place):
  - The path in the snapshot: `/.bko-holt/luks-headers/FILE`
    (`luksfetch::REPO_DIR`), the snapshot of the system itself, not a
    snapshot of its own.
  - The read: the archiver's `dump`, one file at a time, at most the
    dump's `bytes` plus one (`HEADER_MAX`, 256 MiB, without `bytes`).
  - DIR must be new (`recoverycmd::out_unsafe`), and any failure removes
    all of it; a layout that kept none gives an empty DIR, exit 0.
  - The document's fields: `layout`, `dir`, `snapshot`, `repo_dir`,
    `headers[]` (`device`, `label`, `file`, `path`, `from`, `bytes`,
    `sha256`).  `from` is what the archiver's dump was given.
  - bko-rescue's DIR: `h` in the session's directory (`layout::HEADERS_DIR`),
    its gauge's limit 600 s (`HEADERS_MS`).
  - The restore's exclusion only when the layout kept a header, so no
    other golden plan changes.
- **Tested:** `luksfetch.rs`'s unit tests (the list's checks, the size and
  SHA-256 check, the archiver's argv for rustic, a snapshot's subpath and
  restic, a fetch with a script archiver: 0700 and 0600, byte for byte;
  any failure leaving nothing; more than the dump kept; no archiver; none
  kept; rustic's and restic's error words), `stick.rs`'s (the count, a
  list that can't be fetched), `tests/json_cli.rs` (as a program, under
  `BKO_HOLT_NO_COMMIT`; uc24-fde's dump fetched, load's plan with DIR
  without a header problem and with the exclusion; every refusal), the
  stick-copy golden re-blessed for `luks_headers`, holt_schema.py, and the
  holt gate on cinqcent (cargo test 970 passed, 0 failed, 4 ignored; 110
  golden plans, 0 differ after the re-bless; redact-corpus passed).  One golden plan differs, uc24-fde,
  and is re-blessed on purpose: its restore stage now writes the glob file
  (`!/.bko-holt/luks-headers/**`) and passes `--glob-file` (7 lines of
  plan.txt, plan.json's restore commands).
- **Real proof:** `testvm/rescue/record-luks.py` on cinqcent, with this
  bko-holt and the farm's rustic-bko (0.12.0+backotter.4) and cryptsetup
  2.7.5: two LUKS2 test containers with uc24-fde's UUIDs, two backups of a
  tree holding their header backups at `/.bko-holt/luks-headers/`.  The
  newest snapshot's fetch wrote both headers (294912 bytes each) byte for
  byte, `cryptsetup luksDump` read each one's UUID; the older snapshot
  was `header_mismatch` and a wrong password `header_fetch_failed` (in
  rustic's words), nothing left; a restore with load's glob file brought
  back the tree without the headers.  That is the `luks-headers` scenario.
  No real uc24-fde round trip ran: HoltTest's json path doesn't run
  bko-rescue or a backup that holds the headers (its share holds them
  beside the layout), so a uc24-fde run would test nothing new here.
- **Not done:** the backup tool's part (stick-config.md's steps 1 to 4).
  No image was rebuilt: the next image round needs this bko-holt and this
  bko-rescue (holt-bin and rescue-bin), and nothing in the image build
  scripts changes.
- **Spec problems:**
  - "Which copy wins" isn't built, so the layout is always the stick's,
    the newest backup's.  Choosing an older snapshot whose headers
    differ is refused (`header_mismatch`), not restored with that
    snapshot's own layout and headers (open question 15).
  - HoltTest's kept-header runs (uc24-fde, aeon) still copy the headers
    beside the layout; they could be put in the backup at REPO_DIR and
    fetched, once a HoltTest json run goes through bko-rescue.
- **Review fixes** (2026-11-26, "holt, rescue, stick: review fixes for
  oq12"):
  - load's restore also leaves out `/.bko-holt/luks-headers` itself and,
    with no raw volume under it, `/.bko-holt`.  A rustic-bko glob leaves
    out only the path it names (`/**` only what is under it), so before
    this the restore made an empty `/.bko-holt/luks-headers` (0700) and
    `/.bko-holt` on the restored root.  Checked with rustic-bko
    0.12.0+backotter.4 on cinqcent, and by record-luks.py, which now lists
    directories too.  uc24-fde's golden plan is re-blessed for the two
    new lines.  judge.py tolerates a bare `.bko-holt/luks-headers`.
  - `luks-headers` in text mode with no `--password-file` leaves the
    archiver's standard error on the terminal, so restic-bko's password
    prompt shows (rustic-bko's goes to /dev/tty).
  - The read starts at most 1 MiB and grows by copying, each old copy
    zeroed; an allocation that fails is an error.  The error code comes
    from the kind of failure, not from words in the archiver's message.
    An archiver whose output couldn't be read is killed before the wait.
  - Images: marker.py refuses a bko-holt whose api `features` lack
    `luks_headers` (all platforms; build-backotter-layer.sh refuses it
    sooner on Linux).  No boot check: every image from before this would
    fail it, and marker.py already stops a new image without it.
  - The backup tool's staging of the headers (step 2) and older snapshots
    whose headers changed are open questions 14 and 15.
  - Tested: luksfetch's new unit tests (an old dump's big header read by
    growing, the cap, the code from the failure's kind, text mode's
    terminal standard error), json_cli (the three glob lines, restic's
    prompt shown), judge-test.py, test_marker.py and test_arm64_image.py,
    record-luks.py re-run on cinqcent (the `luks-headers` scenario
    re-recorded: a restore with load's glob file brought back `etc` and
    `etc/hostname` only), and the holt gate on cinqcent (cargo test 973
    passed, 0 failed, 4 ignored; 110 golden plans, 0 differ after
    uc24-fde's re-bless; redact-corpus passed).

### What room on the partition built

Built 2026-11-26 (open question 13, approved that day by the user, the
recommendation):

- **Images:** every image's config partition is 256 MiB, as the Linux
  image's was.  marker.py's `CONFIG_MIB` is 256 (it was 32, the BSDs',
  illumos' and Haiku's); make-img.sh passed 256 already.  Each `build.sh`
  that sizes the partition itself has the new size: DragonFly, Haiku,
  illumos and NetBSD `CONFIG_SECTORS=524288`, OpenBSD `CFG_SIZE=524288`
  (its root now starts at `CFG_START + CFG_SIZE`, 559104, 1 MiB aligned).
  FreeBSD's mkimg takes config.img's own size.  DragonFly and illumos now
  check config.img's size too, as the others did, and illumos' read-back
  compares all of it (`count` was 32 MiB).  Still FAT16 (it reaches
  2 GiB).  The boot checks read the size from the marker, so they need no
  change.
- **bko-holt:** `stick.rs`'s `room_check(marker, len, room)`, in the
  bko_holt library beside `schema_warnings`: the check the backup tool
  calls before it writes a machine's system.json (stick-config.md, "Room on
  the partition").  `Room` is what the mounted partition says (free bytes,
  cluster, the machine's system.json and system.prev.json sizes, each
  restore log's size).  It refuses `system_too_big` (over `SYSTEM_MAX`)
  and `stick_full` (the file and its `.sha256` can't be written), and
  warns `stick_room_short` (the machine's logs wouldn't fit after it).
  Each code has words for the user.  No command: the backup tool calls the
  function, as for `schema_warnings`; one comes with it if it isn't Rust.
- **Defaults chosen** (each in one place, `stick.rs`):
  - The logs' room is 11 logs of 2 MiB (`LOGS_KEPT` + 1 of `LOG_MAX`),
    22 MiB, less what the logs take now: bko-rescue copies the new log
    before it removes the oldest (`report.rs`), so 20 MiB isn't enough at
    that moment.  `LOG_MAX` and `LOGS_KEPT` copy bko-rescue's
    `LOG_GZ_MAX` and `LOGS_KEPT` (no shared crate); test_marker.py checks
    they agree.
  - The write as "How it writes" does it: the new file goes down while the
    old two stand (so the old prev's room doesn't count for it), then the
    old prev and its `.sha256` go.  Files count in whole clusters, each
    `.sha256` as one.  A `cluster` of 0 counts bytes.
  - A refusal leaves the stick as it was and the backup still succeeds
    (its own system.json is written first); a warning is written through,
    as the schema warnings are.  (Open question 16 changed what the stick
    holds after a refusal: "What the stick holds after a refused write
    built".)
  - A system.json the partition can't take is refused (`stick_full`),
    where the approved text said "warns": that write would fail anyway,
    and refusing first keeps the old copy whole.  This changes the
    approved behaviour; what the stick then holds is open question 16.
  - `SYSTEM_MAX` is this bko-holt's.  The marker doesn't say what an
    image's bko-holt reads; they are the same today.
  - `CONFIG_BYTES` (256 MiB) only decides whether the words say to write a
    newer image.
- **Tested:** `stick.rs`'s unit test `write_time_room_check` (each
  refusal and warning and its words, `SYSTEM_MAX` and a byte over, the
  write's order, the old prev freed only with a current file, cluster
  rounding of files and logs, ten logs present, a 0 cluster),
  `testvm/test2/tests/test_marker.py` (marker.py's 256 MiB marker and
  config.img; every build script's size equal to marker.py's; OpenBSD's
  alignment; bko-holt's and bko-rescue's log constants; 11 tests, OK on
  the Mac and on cinqcent), a real marker.py run on cinqcent (config.img
  268435456 bytes, FAT16, 4096-byte clusters, 65467 of them; fsck.fat
  clean), and the holt gate on cinqcent (cargo test 974 passed, 0 failed,
  4 ignored; 110 golden plans, 0 differ; redact-corpus passed).
- **Not done:** no image was rebuilt.  The next image round makes every
  image's partition 256 MiB: each image grows by 224 MiB (mostly zeros;
  the BSDs, illumos and Haiku), and its boot tests should show the marker's
  `sectors` 524288 and pass config-partition and config-rw.  Images in
  use keep 32 MiB, and `room_check` names that in its words.  The backup
  tool's call of `room_check` isn't built.  The image round should also
  check that OpenBSD boots from its moved root, that each build VM has
  room for the bigger config.img and image.img (FreeBSD's running builder
  most), that a RAM-root image reads a system.json near 64 MiB, and how
  long `stick --json` takes on one BSD image with a full 256 MiB
  partition (bko-rescue gives it `HOLT_MS`, 60 s).
- **Review fixes** ("holt, stick: review fixes for oq13"):
  - `Room` gained `folder`, `prev_sum`, `others`, and `logs` is None when
    `restore-logs/` isn't there.  A new machine's folder and its first
    `restore-logs/` each count a cluster; the old prev's `.sha256` is
    freed only when it is there.  A check that passed could otherwise
    leave a system.json with no `.sha256` (`checksum_missing`).
  - `stick_full`'s words give the room the failing step has: the free
    room while the old two stand, or the room once the oldest goes.
  - Sizes print to 0.1 MiB, room rounded down and need up, so "64.0 MiB,
    over the 64.0 MiB" can't happen.
  - The words say to write a newer image by its size (256 MiB), not by
    a date, and to remove other machines' folders only when there are
    some.
  - fatfs reads a run of adjacent clusters at once (up to 1 MiB), not a
    cluster at a time: on a BSD's or illumos' raw device a 64 MiB
    system.json was 16384 reads.  `stick --json` reads every machine's
    file, so this matters most there.  Not timed on an image yet.
  - Tested: `write_time_room_check` (folders, `prev_sum`, a first
    `restore-logs/`, each refusal's words, the rounding, `others`),
    fatfs's `adjacent_clusters_are_read_at_once`; the holt gate on
    cinqcent (cargo test 975 passed, 0 failed, 4 ignored; 110 golden
    plans, 0 differ; redact-corpus passed); test_marker.py 11 OK.
- **Spec problems:**
  - "warns when the partition can't hold it and its machine's 20 MiB of
    logs" is short by one log at the moment bko-rescue writes the
    eleventh; built as 22 MiB (above).
  - `SYSTEM_MAX` isn't in the marker, so a later bko-holt that reads less
    (or more) than an image's can't be told from the marker alone.
  - `stick --json` still reads every machine's system.json, one at a
    time: up to 256 MiB in all now, bounded by the partition, against
    bko-rescue's 60 s `HOLT_MS` (fatfs now reads them in 1 MiB runs).
  - bko-rescue keeps a log as `.log` where there is no gzip, uncapped by
    `LOG_GZ_MAX` (up to 32 MiB): the 22 MiB don't cover it.  Every image
    has gzip.

### What the stick holds after a refused write built

Built 2026-11-26 as far as it can be before the backup tool (open question
16, approved that day by the user, the recommendation):

- **bko-holt:** `stick.rs`'s `stick_write(marker, system, len, room)`, in
  the bko_holt library beside `room_check`: the backup tool's whole
  decision for one machine, as data.  `Whole { warnings }` when
  `room_check` takes the file.  Else `WithoutLayout { body, refused,
  warnings, text }`: `body` is the bytes to write in its place, system.json
  without `layout` and with `layout_left_out` (`without_layout`), when
  `room_check` takes those.  Else `RemoveOld { refused, bare, text }`:
  write nothing and remove the machine's older copies.  `text` is the
  words to say.  stick-config.md's "Room on the partition" says what the
  tool does with each.
- `room_check`'s `StickRefusal` now has the reason (`text`) and the
  remedy (`remedy`) apart, and no outcome: "nothing is written, and the
  stick keeps the copy it had" is no longer true.  `stick_room_short`'s
  words are as they were.
- `stick --copy`'s `no_layout` reads `layout_left_out` (only its two
  codes, and `bytes` when it is a number) and says why the layout isn't
  there, that the backup holds it, and that this stick can't restore the
  machine's disks until it comes from the backup.  Without
  `layout_left_out` the words are as they were.  The error code is the
  same, so no schema changed.
- **bko-rescue:** `layout_file` says whether bko-holt's refusal was
  `no_layout` (`layout::NoLayout`).  Then the Disks screen's problem says
  why (each backup records the layout; this stick holds no copy, and this
  bko-rescue can't take it from the backup yet) and what to do: restore
  with another stick that holds the layout; and, if bko-holt's words say
  the stick had no room, make room on this stick or write a newer image
  while the machine still runs, then back up again.  Every other reason (a typed address, a bad checksum) keeps the
  general words.  The title is as it was, so no golden screen changed.
- **Defaults chosen** (each in one place):
  - The member is `layout_left_out`, `{"code", "bytes"}` (`stick.rs`'s
    `LAYOUT_LEFT_OUT_KEY`), in the stick's copy only.
  - `body` is printed by `Value::pretty`, so its size is known before the
    check; the tool writes those bytes as they are (`stick_write`).
  - `RemoveOld` removes `system.json`, `system.prev.json`, their
    `.sha256` files and a `system.json.new`, then the folder only when
    nothing else is in it: `restore-logs/` stay (`StickWrite`'s doc).
  - After `WithoutLayout`, `system.prev.json` may keep an older layout.
    Nothing reads it at restore time, so it can't be planned onto disks.
  - The error code stays `no_layout`; the words carry the reason.
  - The words say "bko-rescue can't do yet" for the fetch from the backup
    (open question 15).  They change when that is built.
- **Tested:** `stick.rs`'s `write_time_stick_write` (each answer and its
  words, `body` without `layout` and with `layout_left_out`, the backup's
  location kept, the edge where `body` just fits or doesn't, a new machine
  with nothing to remove, a second `without_layout` replacing the member)
  and `copy_refuses_what_it_cant_take` (the two codes' words, an unknown
  code and a size that isn't a number not repeated; golden
  `stick-copy-json/layout-left-out.json`, new); `write_time_room_check`
  with the reasons and remedies apart.  bko-rescue's
  `a_layout_bko_holt_wont_copy_is_said_in_its_words` (stick-three's
  recorded `no_layout` gets the new words) and
  `a_layout_the_backup_tool_left_out_is_said_plainly` (the left-out words;
  `machine_not_ok` keeps the general words).  The holt gate on cinqcent:
  cargo test 976 passed, 0 failed, 4 ignored; 110 golden plans, 0 differ;
  redact-corpus passed.  bko-rescue on czerny (Rust 1.85.1): cargo build
  without warnings, cargo test 287 + 15 passed, abi/check.sh ok (49
  facts, 9 symbols), pty-test.py 0 failed, demo-test.py 383 ok, app-test.py
  0 failed, holt_schema.py 411 checked, 0 problems.  demo-test.py's first
  run had 1 failure, not captured; three runs after it had none.
- **Review fixes** ("holt, rescue: review fixes for oq16"):
  - `stick_write` checked `body` only beside the older copies, then
    removed them anyway.  Now, when `body` doesn't fit beside them, it
    checks again with them gone (their files and the old prev's
    `.sha256` freed, in clusters; the old system.json's `.sha256` isn't
    in `Room`, so it isn't counted).  If `body` fits then, the answer is
    `WithoutLayout` with `remove_old`: remove them first, then write
    `body`.  `RemoveOld` is only left when even that fails.  Whether the
    whole file should be tried there first is open question 19.
  - The words name only the copies that are there (system.json,
    system.prev.json, or both).
  - `stick --copy` doesn't say a size for a `system_too_big` at or under
    `SYSTEM_MAX` (only a hand-made file has one).
  - bko-rescue's what-to-do offers room only "if bko-holt's words say
    the stick had no room": `no_layout` also covers `system_too_big` and
    a system.json with no `layout` at all, and bko-rescue sees only the
    code.  Giving the reason as data is open question 18.
  - stick-config.md: "Room on the partition" opens with `stick_write`;
    the schema check takes `body` after `WithoutLayout` and nothing after
    `RemoveOld`; the removals end "fsync the directory as step 2 does,
    then unmount"; getting back to the whole file can take two backups.
  - Tested: `write_time_stick_write` (the removal first, its words and
    warnings, the edge where only the removal makes room, a lone
    system.prev.json named alone, `RemoveOld` naming a lone system.json,
    `schema_warnings` of `body` with no dump warning),
    `copy_refuses_what_it_cant_take` (a `system_too_big` under the
    limit), and bko-rescue's two no-layout tests (the conditional room
    words, a 70 MiB file left out).  The holt gate on cinqcent: cargo test
    976 passed, 0 failed, 4 ignored; 110 golden plans, 0 differ;
    redact-corpus passed.  bko-rescue on czerny (Rust 1.85.1): cargo build
    without warnings, cargo test 287 + 15 passed, abi/check.sh ok (49
    facts, 9 symbols), pty-test.py and app-test.py 0 failed, demo-test.py
    three runs of 383 ok, 0 failed (logs kept; the first build's one
    failure isn't reproduced), holt_schema.py 411 checked, 0 problems.
- **Waits on the backup tool:** calling `stick_write`, writing `body` and
  its `.sha256`, the removals of `RemoveOld` and of `remove_old`, and the
  schema check of what it writes.  bko-rescue's fetch of the layout from
  the backup is open question 15's.
- **Spec problems:**
  - `stick --json` doesn't say a machine has no layout, so bko-rescue says
    it only on Disks, after the password and the snapshot.  A `layout`
    field in each row would let the first screen say it.  Not built.
  - After `RemoveOld` with `restore-logs/` left, `stick --json` lists the
    folder as `unreadable` ("system.json is missing").  The words are
    true, but they read as damage.

### Bug reports on the stick built

Built 2026-11-26 as far as it can be before the backup tool (open question
17, approved that day by the user, the recommendation):

- **bko-rescue:** `report.rs` keeps the 5 newest reports
  (`REPORTS_KEPT`) in `backotter/reports`, each `bko-report-STAMP.txt`
  with its `bko-report-STAMP-UNREDACTED.txt` as one pair.  With the
  partition mounted, a save reads the room by statvfs on the mount point,
  lists the folder, names the pairs beyond the newest five
  (`reports_to_remove`), and checks the room (`report_room`).  It refuses
  a file over `REPORT_MAX` (64 MiB), one that doesn't fit beside the old
  pairs, and one whose save would leave the partition under
  `REPORT_ROOM_MIN`: `LOG_GZ_MAX` x (`LOGS_KEPT` + 1), 22 MiB, holt's
  `LOG_MAX` x (`LOGS_KEPT` + 1).  Then it copies, removes the older pairs
  and syncs the folder.  A refused report stays in RAM; "Report saved"
  says why and to copy the older reports off the stick on another
  computer.  When older pairs went, it says how many.  When both reports
  were made and one wasn't saved, it now says why too.  Restore logs are
  saved as before.
- **The FFI:** `ffi.rs`'s `free_room` calls statvfs into an 8 KiB buffer
  and reads only `f_frsize` and `f_bavail`, at each family's offsets
  (8 and 32; FreeBSD 64 and 0; NetBSD 16 and 48).  NetBSD links
  `__statvfs90`.  `abi.c` and `check.sh` check the two offsets, their
  sizes and that the struct fits the buffer (`room statvfs`, passed to the
  C side as `BKO_STATVFS_BUF`), and the symbol.
- **bko-holt:** `stick.rs` has bko-rescue's `REPORTS_KEPT` and `REPORT_MAX`
  (test_marker.py checks they match report.rs, and that `REPORT_ROOM_MIN`
  is made of the log constants).  `Room` gained `reports`, each report's
  size.  `room_check`'s words name copying the reports off, with the room
  they take, only when there are some; "old bug reports can go" is gone
  from the plain words.  `room_check` keeps no room for reports to come:
  bko-rescue's floor keeps them out of the logs' room.
- **Defaults chosen** (each in one place):
  - The room a save leaves counts the pairs it removes after the copy as
    freed; the file must fit beside them first (`report_room`'s doc).
  - The pair just written always stays, even when the clock made its
    stamp older than the others' (`reports_to_remove`'s doc).
  - Only names of the two forms count and go; other files in
    `backotter/reports` stay (`report_stamp`).
  - When statvfs fails, the copy goes ahead, as before the check: refusing
    would keep every report off the stick there (`save_report`'s doc).
  - A file over 64 MiB isn't copied (it was cut off before;
    `REPORT_MAX`'s doc).
  - `room_check` reserves nothing for reports (stick.rs's
    `REPORTS_KEPT` doc); its words give their room rounded up.
- **Commits:** dda939d (bko-rescue, test_marker.py).  The bko-holt half
  (stick.rs) rode along in ebf88e9, another session's lint commit, whose
  message says "No code change": it was staged with that session's
  files.  Its stick.rs diff is this change alone.
- **Tested:** stick.rs's `write_time_room_check` (the remedy with
  reports alone, with other folders and an older image, their room in
  clusters rounded up) and `write_time_stick_write` (the plain words).
  bko-rescue's `report_names`, `the_five_newest_pairs_stay` (a twin joins
  its pair, a clock that makes the new one oldest, many pairs),
  `the_room_a_save_leaves` (a first folder, the removal counted, the fit
  beside them, the cap, a 0 cluster), `a_report_is_saved_then_the_oldest_go`
  (on real files: refused leaves all, then one pair goes), and the app's
  `a_report_keeps_the_five_newest_pairs` (golden `app-report-saved-pruned`,
  new) and `a_report_that_would_leave_under_22_mib_stays_in_ram` (the
  edge a byte apart, a full stick, statvfs failing); ffi.rs's
  `statvfs_reads_the_room`.  The holt gate on cinqcent: cargo test 976
  passed, 0 failed, 4 ignored; 110 golden plans, 0 differ; redact-corpus
  passed.  bko-rescue on czerny (Rust 1.85.1): cargo build without
  warnings, cargo test 294 + 15 passed, abi/check.sh ok (54 facts, 10
  symbols), pty-test.py 117 ok, 0 failed, demo-test.py 383 ok, 0 failed,
  app-test.py 311 ok, 0 failed, holt_schema.py 411 checked, 0 problems.
  test_marker.py 11 OK on the Mac.  The header test in each build guest
  (build-holt.sh `HOLT_CRATE=rescue`): FreeBSD 14.5 54 facts, OpenBSD 8.0
  57, NetBSD 11.1 54, DragonFly 6.4 54, illumos (OmniOS) 51, Haiku beta6
  56, each with 10 symbols, all ok.  A first demo-test.py run had 2
  failures, not captured, while macOS `._` files were in the copied tree;
  with them removed, two runs had none.  A first NetBSD build on cinqcent had no `cc` (no build
  image there); on copland's it passed.
- **Waits on the backup tool:** filling `Room.reports` (the sizes of
  `backotter/reports/bko-report-*.txt`) and saying `room_check`'s words.
- **Spec problems:**
  - "5 pairs, each up to its cap" is 640 MiB at 64 MiB a file, more than
    the 256 MiB partition.  No reserve can hold that; the 22 MiB floor is
    what keeps the logs' room, and reports may fill the rest.
  - The floor is 22 MiB in bytes.  `room_check`'s need is in clusters,
    with one more for a first `restore-logs/`: a cluster apart on a new
    machine.
  - The floor doesn't count what the machine's logs hold already, as the
    approved text says.  A stick with ten logs needs only one more log's
    room.
  - A stick with more than five pairs from before this build loses all but
    the newest five at its next save, without asking.
  - The folder's own clusters aren't counted: each name in
    `backotter/reports` takes folder room.  The review measured it on a
    real FAT32 loop mount (256 MiB, 512 B clusters): 200 long names took
    45 clusters, about 22 KiB.  That is a cluster or two a save, far
    below the floor.
- **Review fixes** (0c7180d, "rescue: review fixes for oq17"):
  - The refusal words name the older reports to copy off only when there
    are some (a report other than the new one's own pair).  Else they say
    to copy files not needed off the partition.
  - The words say "the restore logs need 22.0 MiB there", not "a restore
    needs it for its log": 22 MiB is 11 logs' room.
  - A folder that can't be listed doesn't stop a save: the copy goes ahead
    unchecked and nothing is removed, as when statvfs fails.  An entry
    that can't be read is left out of the count and not removed.
  - When both reports fail for different reasons, both reasons show.
  - `copy_in`'s too-big words give the file's size, not the 64 MiB read.
  - `free_room`: a 0 `f_frsize` is the room unread (the copy goes ahead);
    an `f_bavail` over i64::MAX (a negative count cast, OpenBSD's FFS
    past its reserve) is 0 free; a 32-bit build reads no room.
  - stick-config.md: `Room.reports` counts exactly the two name forms
    bko-rescue keeps, and the backup tool never removes reports.
    rescue-screens.md's open question 9 is marked settled.
  - Declined: a clock-proof order of reports (none exists on FAT: file
    times come from the same clock; open question 20 asks); room_check's
    remedy keeping "free room on the stick's config partition" beside
    the named ways (open question 13's approved words: "else");
    counting the folder's clusters (above).
  - Tested on czerny (Rust 1.85.1): cargo build without warnings, cargo
    test 298 + 15 passed (new: `the_room_words_name_old_reports_only_when_there_are_some`,
    `a_folder_that_cant_be_listed_doesnt_stop_the_save`,
    `a_file_over_the_cap_is_refused_with_its_size`,
    `both_reasons_show_when_both_saves_fail`, and `room_of`'s cases in
    `statvfs_reads_the_room`), abi/check.sh ok (54 facts, 10 symbols),
    pty-test.py 117 ok, demo-test.py 383 ok, app-test.py 311 ok, 0
    failed each, holt_schema.py 411 checked, 0 problems.  test_marker.py
    11 OK on the Mac.  No holt code changed, so no holt gate; the header
    test in the BSD, illumos and Haiku guests wasn't rerun (no C fact
    changed).

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
   bko-holt (rescue-tui's open question 4), or settings in JSON?  Until
   then (RT10k, as specified): ignored, with the note `settings_ignored`;
   the stick's name is null and no `repository_url` override applies.
5. **A dry run's archiver:** run `rustic-bko restore --dry-run` in the dry
   run's `restore` stage, so the dry run shows the real file count and
   size (it reads the repository only)?  Not specified; it would make the
   dry run's Restore screen show figures.
6. **Signals other than SIGTERM** under `--json`: SIGHUP (a detached load
   has no terminal to lose) and SIGINT left at their defaults?
7. **The events file's size** on a 20-hour copy (~14 MiB): rotate, or
   keep progress to one event every 5 s past an hour?
8. **The layout's copy for load** (answered: approved 2026-11-26 by the
   user: stick --copy; built, "What stick --copy found"; was: blocks RT12,
   "What RT10k and RT10p found"): `bko-holt stick --copy INSTALL_ID OUT --json`, the system.json
   `stick` just checked written to a new 0600 file in bko-rescue's `k/`-like
   private directory, the bytes whose checksum it checked (*recommended*:
   no check-then-use gap, and bko-rescue still reads no FAT).  Or
   bko-rescue mounts `config.dev` read-only, copies the file and checks its
   `.sha256` itself (a SHA-256 in bko-rescue, D2).  Either way load can't
   take a `backotter-system` document until the backup tool's spec says
   how system.json carries the dump.  How it carries it was then chosen
   by the builder and approved by the user 2026-11-26: open question 10.
9. **The Recovery key screen's word list** (blocks RT11's completion: a
   word filled in after 4 letters, an unknown one named as it is typed):
   `bip39-english.txt` taken by `include_str!` from `holt/src` (D2 amended
   for a data file: one file, fixed by the compatibility contract, no
   call to fail) (*recommended*).  Or `bko-holt recovery-words --list`,
   the 2048 words on stdout, read once at start.  The check itself stays
   in holt either way.
10. **Where system.json carries the dump** (answered: approved
    2026-11-26 by the user, the recommendation: `layout` as built, the
    envelope's `storage` and `boot` dropped from schema 1; from the stick
    --copy review).  The
    builder put the whole `bko-holt dump` in a top-level `layout` member,
    and `stick --copy` writes that member.  stick-config.md's envelope
    also has `storage` and `boot`, meant for a system description that
    was never designed; the dump has its own of both.  *Recommended:*
    keep `layout` as built, and drop the envelope's `storage` and `boot`
    from schema 1, so the dump is the one record of the disks and the
    boot (the pools' features included).  Or keep `storage` and `boot` as
    the record and have `stick --copy` build a dump from them (a second
    format to keep in step with dump).  Until it is answered,
    stick-config.md says `layout` is the record and the two are unused.
11. **The dump's schema and the stick's age** (answered: approved 2026-11-26 by the user, the recommendation; built 2026-11-26, ae051fb, e9ef07b and "holt, stick: review fixes for oq11": "What the marker's dump_schemas built").
    The marker's `system_schemas` covers system.json's envelope only.  A
    backup machine whose bko-holt writes dump schema 2 passes the
    write-time check, and an old stick's `stick --copy` then refuses the
    layout (`layout_refused`).  *Recommended:* the marker gets
    `dump_schemas` (the dump schemas the image's bko-holt load reads),
    and the backup tool's write-time check warns when `layout.schema`
    isn't in it, as it does for `system_schemas`.  Or nothing: the
    restore says it, late.
12. **LUKS headers a dump kept** (answered: approved 2026-11-26 by the user, the recommendation; built 2026-11-26, 3100b25 and "holt-json, stick-config, rescue-tui: open question 12 built": "What the LUKS headers' fetch built").  A dump made
    with `--keep-luks-header` (an Ubuntu Core's encryption always) needs
    its header files beside it, and nothing puts them on the stick.
    Until it was built, `stick --copy` refused such a layout
    (`layout_refused`, in words that said so), so the restore stopped on
    Disks, not at the first plan.  *Recommended:* the backup keeps the
    header files in the repository (encrypted, no size limit; a header
    backup is 16 MiB or more), and bko-rescue fetches them into the
    session's directory once the password is in, before the first plan.
    Or the stick carries them beside system.json, each with its
    `.sha256`, and `stick --copy` copies them too (they fill a 32 MiB
    partition fast).
13. **Room on the config partition** (answered: approved 2026-11-26 by the user, the recommendation; built 2026-11-26, "stick, holt: open question 13 built" and "holt, stick: review fixes for oq13": "What room on the partition built").  A
    system.json is read up to 64 MiB (`stick.rs`'s `SYSTEM_MAX`), a dump
    may be up to 1 GiB (`LAYOUT_MAX`), the BSD images' config partition
    is 32 MiB, and each machine's restore logs may take 20 MiB.  A large
    dump (ESP images) or several machines won't fit.  *Recommended:* the
    backup tool refuses to write a system.json over `SYSTEM_MAX` and warns
    when the partition can't hold it and its machine's 20 MiB of logs;
    and every image's config partition is 256 MiB, as the Linux image's
    is.  Or a smaller cap for `layout`, with ESP images left out of the
    stick's copy.
14. **How the backup tool puts the headers in the snapshot** (from open
    question 12's review; answered: approved 2026-11-26 by the user, the recommendation; not built yet).  stick-config.md's step 2 needs
    `/.bko-holt/luks-headers` in the tree the backup reads.  A mount point
    made on the system's root stays on the live system (and in every
    later backup) unless it is removed again, and can't be made on a
    read-only root (Ubuntu Core's is the base snap).  With a
    `SNAPSHOT:SUBPATH` backup, the headers must be under SUBPATH, since
    `luks-headers` reads `SUBPATH/.bko-holt/luks-headers/FILE`.
    *Recommended:* a staging root in RAM, in a private mount namespace
    (`unshare -m`): a tmpfs S, the backed-up file systems bind-mounted
    read-only under it at their own paths, and the dump's header
    directory bind-mounted at `S/.bko-holt/luks-headers`; S is backed up
    with rustic-bko's `--as-path /` (as record-luks.py backs up its tree),
    so the snapshot's paths are the system's and nothing is written to
    its disks.  How restic-bko (no `--as-path`) does the same is part of
    this answer.  Or a mount point on the live root, made and removed by
    each backup (not on a read-only root).  Either way load leaves
    `/.bko-holt/luks-headers` and a `/.bko-holt` with no raw volume under
    it out of the restore (built), so a bare mount point that rode along
    never comes back.
15. **An older snapshot of a machine that kept headers** (from open
    question 12's review; answered: approved 2026-11-26 by the user, the recommendation; not built yet).  The stick's layout is the
    newest backup's ("Which copy wins" isn't built), and each fetched
    header is checked against it.  Once a header changes (a keyslot
    added, a passphrase changed, an Ubuntu Core reseal), every older
    snapshot is `header_mismatch`: only the newest can be restored, even
    when the newest backup is of a broken system.  *Recommended:* build
    "Which copy wins" step 2 for it with the backup tool: each snapshot
    holds its own system.json (stick-config.md already has the backup
    tool write it into the backup first), and when the chosen snapshot
    isn't the newest, bko-rescue fetches that snapshot's layout from it
    (checked as `stick --copy` checks the stick's) and its headers from
    the same snapshot.  Or keep the refusal (today's): its help says to
    choose the newest backup.
16. **What the stick holds after a refused write** (from open question
    13's review; answered: approved 2026-11-26 by the user, the recommendation; built 2026-11-26 as far as it can be before the backup tool, b121dbb, "holt-json, stick-config, rescue-tui: open question 16 built" and "holt, rescue: review fixes for oq16": "What the stick holds after a refused write built").  `room_check` refuses a system.json over
    `SYSTEM_MAX` (`system_too_big`) or one the partition can't take
    (`stick_full`; the approved text said "warns", see "What room on the
    partition built"), and the tool then writes nothing to the stick.  A
    new machine so gets no folder: its stick has no repository location
    and no layout, so the user must type the location, and even then
    there is no layout to restore.  A machine with an older copy keeps
    its older layout: a later restore of the newest snapshot plans it
    onto the old disks ("Which copy wins" step 2 isn't built), and only
    `written` says so.  That runs against restore fidelity first.
    *Recommended:* keep the refusal, and on it write the machine's
    system.json without `layout` (a few KiB; stick-config.md already
    allows one), so the stick still finds the backup and never plans
    onto an old layout; the backup's words say plainly that this stick
    can't restore the machine's disks until the layout comes from the
    backup (open question 15's step 2).  If even that doesn't fit, remove
    the machine's old system.json and system.prev.json from the stick and
    say so.  Or keep today's build: the old copy stays, and the words say
    plainly that the stick's copy is from `written` and older than the
    backup.
17. **Bug reports on the stick** (from open question 13's review;
    answered: approved 2026-11-26 by the user, the recommendation; built 2026-11-26 as far as it can be before the backup tool, dda939d, "rescue: keep the 5 newest bug reports, and the logs' room (open question 17)", its bko-holt half in ebf88e9, and 0c7180d, "rescue: review fixes for oq17": "Bug reports on the stick built").  bko-rescue saves `backotter/reports/bko-report-*.txt`
    (and an `-UNREDACTED` copy, read up to 64 MiB) on the config
    partition, and nothing removes them.  `room_check` keeps 22 MiB for
    each machine's restore logs, but reports saved later eat that same
    room with no warning, and a restore's log then stays in RAM.
    *Recommended:* bko-rescue keeps the 5 newest reports (each with its
    `-UNREDACTED` twin) and removes older ones after it writes a new one,
    as it does the logs; and before it saves one, it refuses when the
    save would leave the partition less than 22 MiB free, saying to copy
    old reports off.  Or leave them, and say in the Report screen's words
    that reports take the stick's room until removed.
18. **Why a stick holds no layout, as data** (from open question 16's
    review).  bko-rescue sees only bko-holt's `no_layout` code, after
    the password and the snapshot are chosen.  It can't tell
    `stick_full`, where room helps, from `system_too_big`, which no stick
    takes, or from a system.json with no `layout` at all.  So its
    what-to-do offers room only "if bko-holt's words say the stick had no
    room".  *Recommended:* each machine row of `stick --json` gets a
    `layout` member: `"present"`, `"left_out"` (with
    `layout_left_out`'s `code` and `bytes`, the codes checked as `stick
    --copy` does), or `"none"`.  bko-rescue then says it on the Machine
    screen, before the password, with advice for that code alone.  Or
    keep today's conditional words.
19. **The whole file once the older copies go** (from open question 16's
    review).  When even `body` doesn't fit beside the machine's older
    copies, `stick_write` removes them and writes `body`.  If the whole
    file would fit once they are gone, writing it keeps the layout on the
    stick, and nothing more is lost: the copies go either way.
    *Recommended:* try the whole file first there, then `body`, then
    `RemoveOld` (restore fidelity first).  Or keep `body` there, as the
    approved text says.
20. **A wrong clock and the newest reports** (from open question 17's
    review).  bko-rescue keeps the 5 newest report pairs by their stamp:
    the clock of the computer that saved each one.  On a computer whose
    clock is wrong, "newest" is wrong too.  A dead clock (2000-01-01)
    saves report A, which stays; ten minutes later report B removes A,
    not an older pair, and the screen says "the oldest was removed".  A
    clock set ahead (2099) makes its report outlast every later one,
    pushing out real ones.  FAT's file times come from the same clock,
    so no other order is safe.  *Recommended:* a save whose stamp isn't
    newer than every pair on the stick removes nothing, and "Report
    saved" says the clock looks wrong, so the older reports stayed; the
    22 MiB floor still keeps the logs' room, and its refusal says to copy
    reports off.  A pair from the future then stops the removal until it
    is copied off.  Or keep today's build: the new pair always stays, the
    others go by stamp.
