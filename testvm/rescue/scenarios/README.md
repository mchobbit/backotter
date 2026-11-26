# Scenarios for fake-bko-holt

Each directory is a scenario, `FAKE_BKO_HOLT_SCENARIO`; each of its
subdirectories is one call (see `fake-bko-holt`'s header for the files).
The `demo-*` scenarios' events are holt-json.md's `bko-holt-events`,
schema 1, as a dry run writes them (`commit` false, `dry_run` true, each
`cmd` with `would: true` and no `cmd_done`, each `stage_done` unchecked):
the demo follows nothing else.  `demo` is recorded (RT10j: debian13-stack's
dry run under `BKO_HOLT_NO_COMMIT=1`, as bko-holt 0.2.6 at 3289bc3 wrote
it), the other `demo-*` hand-written (RT10h); all pass `holt_schema.py`,
the checker bko-holt's own documents pass.  A call with a file named
`invalid` is wrong on purpose: the checker must fail it, for the reason in
that file.

The `rec-GUEST` scenarios are whole runs recorded by HoltTest1's
`HT_VARIANT=json` (testvm/holttest1d/README.md, "The --json rebuild") on
one guest a family by `ht1-json.py record` (each's `recorded.txt` says
from what and when), in the run's order: `--version --json` and `load
... --commit --json` refused under `BKO_HOLT_NO_COMMIT=1`, the decoy
mapped without `--erase` refused by the checks (`problems`, `end` 4), the
whole run's `plan --json`, the dry run, then for each stage its own `plan
--json`, the stage's `--commit --json --expect-plan` run with that plan's
hash, and `status --json` after it; key and password files' paths as
`*`.  The same argv is asked more than once there, the answer moving on
(each stage's plan, each status), so they replay only with
`FAKE_BKO_HOLT_STATE` set (fake-bko-holt's header): call by call in order,
as a client staging the run would, each `--expect-plan` the hash of the
plan document it got just before; `ht1-json.py replay` does that, and
ht1-json-test.py replays every one.  `rec-debian13-stack` (LUKS on md,
UEFI, LVM), `rec-freebsd15-geli`, `rec-openbsd8-crypt` (softraid CRYPTO),
`rec-omnios58` (ZFS), `rec-haiku6`; the checker reads them all.

They are a staged run's, not RT12's flow (one `plan --json`, then one
`--through close --commit --expect-plan` run), which no guest has recorded
yet, nor a real `failed` or `stopping` stream: RT12's tests stage the run
as these do, or record those in a guest of their own.

The argv is a real one's (holt-json.md, "demo.rs and the fake"): `--repo`
and `--password-file`, since a real bko-holt refuses `--through close`
without `--repo`.

| Scenario | What `--demo`'s restore page sees |
|---|---|
| `demo` | every stage would run, boot and close too, `end` 0: Done, as a dry run (recorded: 92 commands) |
| `demo-fail` | the boot stage's `failed` (`cmd_failed`, grub-install, its tail), a stderr, `end` 1, exit 1 |
| `demo-slow` | stops in the restore stage for 60 s, its bar moving, for Cancel and signals |
| `demo-schema` | a `start` of schema 2, then waits: bko-rescue must stop it (`invalid`) |
| `demo-blocked` | `problems` (`not_root`, `key_needed`), `end` 4, exit 4: nothing ran |
| `demo-refused` | a `bko-holt-error` document as the whole of stdout (`layout_unreadable`), exit 2 |
| `demo-stopped` | `stopping`, then `end` with `stopped` true, exit 5: bko-holt stopped on request |

Two parts of these a real dry run never writes, kept so the demo's screens
can be seen: `demo-slow`'s progress (a dry run runs no archiver) and
`demo-fail`'s failure (a dry run runs no command, so it fails only on an
internal error).  RT12's tests get the `--commit` runs recorded in a guest
(`rec-*`, staged), and a real refusal by the checks (each `rec-*`'s
`refuse-decoy`).

## The stick scenarios (RT11)

`stick-*` are recorded by `../record-stick.py` from a real bko-holt
(RT10k, RT10p): `--version --json`, then `stick --json` on a Linux tree
booted from a stick whose config partition `stick/marker.py` made (256
MiB FAT16, partition 3, as the Linux image's) with machines put on by
mtools; each `recorded.txt` says from what and when.  `stick-three` and
`stick-this-computer` also hold `recovery-words * --json` calls, answered
by the words on standard input (the call's `stdin`: BIP39's test vector,
as bko-rescue sends it, each word completed; the fake takes a word's first
4 letters or more for it, as bko-holt does) and, for the good words,
writing the password to OUT (its `out`); the fake watches that OUT is
removed as it watches key files.  `stick-three` and `stick-layouts` hold
`stick --copy ID * --json` calls (holt-json.md's open question 8), run
on the same tree: what one wrote to OUT is its `out` (the fake writes it
to argv[3] and doesn't watch it: bko-rescue keeps the layout for the
session).

| Scenario | What the Machine screen sees |
|---|---|
| `stick-three` | lovelace (Debian 13, a password hint), babbage (Fedora 45, rest:), hopper (FreeBSD 15.0); none from this computer |
| `stick-this-computer` | the same, lovelace backed up from this computer |
| `stick-one` | lovelace alone, on a new computer |
| `stick-two-here` | lovelace twice (reinstalled under a new install ID), both with this computer's DMI UUID: neither is "this computer", note `this_computer_ambiguous` |
| `stick-failing` | lovelace ok; babbage `checksum_bad`, hopper `schema_unknown`, turing `checksum_missing` |
| `stick-empty` | the config partition, no machines yet |
| `stick-missing` | no partition 3 (a file-copying writer): `missing`, exit 1 |
| `stick-reformatted` | another FAT serial: `damaged`, exit 1 |
| `stick-layouts` | lovelace's system.json carrying holt/tests/corpus/debian13-stack's dump as its `layout`, babbage's none, hopper's a wrong checksum: `03-copy` writes lovelace's layout (the corpus dump, byte for byte), `04-copy-no-layout` `no_layout`, `05-copy-checksum-bad` `machine_not_ok`, `06-copy-no-machine` `no_such_machine` (each exit 1) |
| `stick-invalid` | hand-written, wrong on purpose (`invalid`): a missing config with machines, a recovery verdict `ok` with a problem, two rows `this_computer`, a machine named `../../etc` |

`luks-headers` is recorded by `../record-luks.py` from a real bko-holt and
a real rustic-bko (holt-json.md's open question 12): a repository whose two
backups hold LUKS2 header backups of two small test containers at
`/.bko-holt/luks-headers/FILE`, and uc24-fde's dump with the newest
headers' sizes and SHA-256 as the layout.  `02-fetch` is the newest
snapshot (both headers fetched, checked byte for byte and by `cryptsetup
luksDump`), `03-mismatch` the older one (`header_mismatch`),
`04-wrong-password` rustic-bko's own refusal (`header_fetch_failed`); 02 and
03 match only the right password (their `password`).  The fake makes the
call's DIR and its headers (test bytes of each row's size, never a real
header) and writes the document with that DIR; it watches them as key
files.  recorded.txt says what and when.

The recovery calls (`stick-three`, `stick-this-computer`): `good` (exit 0,
OUT written), `unknown` (word 9 `ribbx`, suggest ribbon), `checksum` (two
words swapped), `count` (11 words), `short` (word 11 `tha`: thank,
that).  illumos' `two_sticks` can't be recorded through a Linux tree: its
document is the unit test's golden, `holt/tests/fixtures/stick-json/two-sticks.json`.

## The app's (RT11)

Hand-written by `../make-app-fixtures.py` for `../app-test.py`:

| Scenario | What it is |
|---|---|
| `app-three` | `stick-three`'s recorded calls (version, stick, recovery-words, stick --copy's refusal) and `report-bug`'s: its `out` is the report, written to `--output`'s file |
| `app-dry` | the same, its api document saying the dry-run lock took (`no_commit`, `dry_run` true) |

rustic-bko's answers are beside these, in `../rustic/NAME` (not here, so
holt_schema.py doesn't read them: they are rustic-bko's JSON, not
bko-holt's), for `../fake-rustic-bko`, which is fake-bko-holt with its own
scenario variable (`FAKE_BKO_RUSTIC_SCENARIO`).  Each answers `cat
config` and `snapshots --json` when the `--password-file` holds the right
password (a call's `password` file: `correct horse`, or the recovery key's
password, which `app-three`'s `recovery-words` writes), and rustic_core's
wrong-password error (C002, exit 1) otherwise: `lovelace` (its ID the
stick's, 14 backups), `mismatch` (another repository's ID), `shared`
(lovelace's and babbage's backups, for "Which machine?").

## The disks scenarios (RT12)

Written by `../make-disk-fixtures.py`: each answers bko-rescue's plan and
status calls, by the argv shape `src/app/holt_args.rs` builds, from Disks
to Review, and holds `03-copy`, `stick-layouts`' recorded `stick --copy`
of lovelace's layout, which Disks runs before its first plan (the Rust
tests' fake answers it too).  `stick-three`'s `08-copy` is the refusal
(`no_layout`): app-three's and app-dry's Disks show it.

The fakes match calls by argv only, so some answers don't belong
together.  The `app-rec-*` plans were recorded against the HoltTest
share's dump (its sha256 is in each plan's `layout`), and their
`03-copy/out` is the corpus dump.  The copy documents say `dry_run`
false and name the recording host's temp path in `out`, as recorded.
Every other document in a dry scenario says `dry_run` false too.
bko-rescue reads none of this: it takes `dry_run` from the api document
only, and checks a copy by its exit, schema, version, install ID and
`bytes`.

| Scenario | What Disks to Review see |
|---|---|
| `app-rec-debian13` | recorded: rec-debian13-stack's whole-run plan for every plan, its status after partition for every status (a resume: Go on) |
| `app-lovelace` | hand-written: lovelace's Samsung and WD Red; here a Crucial 1 TB (much bigger), the WD Red (the same disk), a Kingston 480 GB with photos (4.3 % smaller: the offer), the stick |
| `app-lovelace-dry` | the same, its api `no_commit` true |
| `app-lovelace-luks` | app-lovelace, the copy counting 2 kept LUKS2 headers (hand-made), luks-headers' recorded `02-fetch` (any snapshot) as `04-luks-headers`, and `--luks-header-dir *` in every load: the fetch before the first plan |
| `app-lovelace-small` | hand-written: a WDC 320 GB (38.7 % smaller: the user decides) and a Transcend 64 GB (the data doesn't fit) |
| `app-rec-restore` | built from rec-debian13-stack: a fresh status, then the Restore's one `--through close --commit` run, its stream the recorded stages' under one start and end, the copy's progress a second apart: Done |
| `app-rec-dry` | the same with app-dry's api, its run the recorded dry run (05-dry) as it is: the dry run's Done |
| `app-rec-notdry` | a dry run answered by a stream that commits (wrong on purpose): stopped at once |
| `app-rec-fail` | the run fails in boot (the mock's grub-install, `cmd_failed`, `end` 1); Try again's status, boot and close alone; Install without an entry's plan and runs |
| `app-rec-stop` | the copy waits 30 s, for Cancel and Stop; `stopped.events` is the stream the fake writes then |

The Restore's scenarios answer their calls in turn (`FAKE_BKO_HOLT_STATE`,
or the Rust tests' `in_turn`): Try again's status is another document
than the first one's.  RT12's flow isn't recorded by any guest, so its
streams are built from the staged run's recordings (each start's plan
hash the whole run's: every RT12 run carries `--repo`); the failure and
the stop are built, as the demo's are.
