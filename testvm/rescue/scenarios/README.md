# Scenarios for fake-bko-holt

Each directory is a scenario, `FAKE_BKO_HOLT_SCENARIO`; each of its
subdirectories is one call (see `fake-bko-holt`'s header for the files).
The events follow ui.md's "`--json` for load", written by hand.  That
protocol is RT10's and isn't built, so these are provisional: record them
again from real runs once it lands.

| Scenario | What `--demo`'s restore page sees |
|---|---|
| `demo` | every stage done, `progress` in the restore stage, `end` 0 |
| `demo-fail` | the boot stage's `failed`, a stderr, `end` 1, exit 1 |
| `demo-slow` | stops in the restore stage for 60 s, for Cancel and signals |
| `demo-schema` | a `start` of schema 2, then waits: bko-rescue must stop it |
