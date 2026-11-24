# Scenarios for fake-bko-holt

Each directory is a scenario, `FAKE_BKO_HOLT_SCENARIO`; each of its
subdirectories is one call (see `fake-bko-holt`'s header for the files).
The events are holt-json.md's `bko-holt-events`, schema 1, as a dry run
writes them (`commit` false, `dry_run` true, each `cmd` with `would: true`
and no `cmd_done`, each `stage_done` unchecked): the demo follows nothing
else.  They are hand-written (RT10h) and pass `holt_schema.py`, the checker
bko-holt's own documents pass; RT10j records `demo` again from a real dry
run.  A call with a file named `invalid` is wrong on purpose: the checker
must fail it, for the reason in that file.

The argv is a real one's (holt-json.md, "demo.rs and the fake"): `--repo`
and `--password-file`, since a real bko-holt refuses `--through close`
without `--repo`.

| Scenario | What `--demo`'s restore page sees |
|---|---|
| `demo` | every stage would run, boot and close too, `end` 0: Done, as a dry run |
| `demo-fail` | the boot stage's `failed` (`cmd_failed`, grub-install, its tail), a stderr, `end` 1, exit 1 |
| `demo-slow` | stops in the restore stage for 60 s, its bar moving, for Cancel and signals |
| `demo-schema` | a `start` of schema 2, then waits: bko-rescue must stop it (`invalid`) |
| `demo-blocked` | `problems` (`not_root`, `key_needed`), `end` 4, exit 4: nothing ran |
| `demo-refused` | a `bko-holt-error` document as the whole of stdout (`layout_unreadable`), exit 2 |
| `demo-stopped` | `stopping`, then `end` with `stopped` true, exit 5: bko-holt stopped on request |

Two parts of these a real dry run never writes, kept so the demo's screens
can be seen: `demo-slow`'s progress (a dry run runs no archiver) and
`demo-fail`'s failure (a dry run runs no command, so it fails only on an
internal error).  RT12's tests get `--commit` runs recorded in a guest.
