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
as bko-rescue sends it, each word completed) and, for the good words,
writing the password to OUT (its `out`); the fake watches that OUT is
removed as it watches key files.

| Scenario | What the Machine screen sees |
|---|---|
| `stick-three` | lovelace (Debian 13, a password hint), babbage (Fedora 45, rest:), hopper (FreeBSD 15.0); none from this computer |
| `stick-this-computer` | the same, lovelace backed up from this computer |
| `stick-one` | lovelace alone, on a new computer |
| `stick-failing` | lovelace ok; babbage `checksum_bad`, hopper `schema_unknown`, turing `checksum_missing` |
| `stick-empty` | the config partition, no machines yet |
| `stick-missing` | no partition 3 (a file-copying writer): `missing`, exit 1 |
| `stick-reformatted` | another FAT serial: `damaged`, exit 1 |
| `stick-invalid` | hand-written, wrong on purpose (`invalid`): a missing config with machines, a recovery verdict `ok` with a problem |

The recovery calls (`stick-three`, `stick-this-computer`): `good` (exit 0,
OUT written), `unknown` (word 9 `ribbx`, suggest ribbon), `checksum` (two
words swapped), `count` (11 words), `short` (word 11 `tha`: thank,
that).  illumos' `two_sticks` can't be recorded through a Linux tree: its
document is the unit test's golden, `holt/tests/fixtures/stick-json/two-sticks.json`.
