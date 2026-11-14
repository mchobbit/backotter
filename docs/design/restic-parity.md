# restic-bko parity with rustic-bko

The user, 2026-11-14: "Bring restic fork up to speed with rustic fork: ensure
that the forked restic has all the additional functionality that the forked
rustic has."

Forks compared (branch `backotter`, local clones in `~/git`):

- restic `58a695576` (0.19.1+backotter.6, 67 commits over v0.19.1)
- rustic `99b9b56` (0.11.4+backotter.11, 53 over v0.11.4)
- rustic_core `3bb01f1` (0.13.0+backotter.11)

Sources: each fork's `BACKOTTER.md` and commit log, `rustic/src/commands/restore.rs`,
`rustic_core/crates/core/src/commands/restore.rs`, `docs/design/btrfs-snapshots.md`,
`holt/src/load/commands.rs`, `testvm/compare/`.

## Gaps (restic-bko lacks it)

| # | rustic-bko feature | Where | restic-bko today | Who needs it |
|---|---|---|---|---|
| G1 | `restore --exact`: the destination is made exactly the node tree, writing only what differs. Implies `--delete` and verifying existing contents blob by blob (a same-size, same-mtime file with other contents is fixed). An existing regular file with more than one link is first replaced by a reflink clone of itself (`FICLONE` to a temp file, renamed over; removed where reflinks aren't supported), so no write reaches its other names. Metadata (owner, mode, times, xattrs incl. the SELinux label, ACLs, flags) is set only where it differs, so an exact restore over a matching tree moves no ctime; a blocking flag (`i`, `a`) comes off first and goes back if the node has it. Removes an SELinux label the node doesn't have. The destination itself always gets the subfolder's own metadata. | rustic_core 979248b, 2fcfef2, 7ce9438, 0ac038a (`commands/restore/exact.rs`); rustic 5bae0a6 | **Missing.** `--delete` and `--overwrite` exist; metadata is set on every node unconditionally; no hard-link breaking for in-place writes | bko-holt `load` for btrfs shared snapshots (`--btrfs-snapshots shared`, the default): refuses restic-bko today ("use rustic-bko 0.11.4+backotter.11") |
| G2 | `restore --clone-from PATH=DIR` (with `--exact` only): files whose content list the same snapshot's PATH has, restored at DIR and unchanged since, are cloned whole (`FICLONE`) from there and then given their own metadata; a failed clone falls back to writing. Never clones from inside the destination, nor a file onto itself. | rustic_core 979248b, 2fcfef2, 3f2f3b2; rustic 5bae0a6, a1ec9bf | **Missing** | bko-holt btrfs shared snapshots (Tumbleweed, CachyOS, Fedora snapper layouts) |
| G3 | `restore --target-metadata`: the destination gets the subfolder's own metadata, set last, even when it isn't empty (not fresh). | rustic fa75dd1 | **Missing** (restic-bko gives it only to a fresh target, rule shared with rustic) | bko-holt `load` where a btrfs alias is left out of the main restore |

With G1-G3 bko-holt's `--btrfs-snapshots shared` check can accept restic-bko
too (a follow-up in bko-holt, not in the fork).

## At parity (checked, nothing to do)

- **Node attributes**: the generic-attribute names in both forks' code are the
  same 28 (`linux.*`, `freebsd.*`, `netbsd.*`, `openbsd.*`, `dragonfly.*`,
  `illumos.*`, `unix.*`, `haiku.attributes`); BACKOTTER.md's shared rules
  (restic "Rules shared with rustic-bko") cover flags, ACLs, illumos system
  attributes and SIDs, ZFS/XFS/FAT/bcachefs options, project IDs, casefold,
  birth times, symlink times and modes, sparse marks and hole restore, device
  IDs for hard links only, sockets and doors skipped, av_quarantined, directories
  above the backed-up path, fresh restore targets, restores over immutable files,
  SELinux labels kept, per-xattr read failures, partial-restore parents. Most of
  rustic_core's list is rustic catching up with restic-bko ("as BackOtter's
  restic").
- **`--strict-exit`** (rustic 0.11.4+backotter.8): it makes rustic exit as restic
  already does (3 incomplete backup, 1 restore errors, 130 interrupt).
- **`rewrite` keeps atimes**, **`--numeric-id`**, **`backup --with-atime`**: restic's
  own behaviour, which rustic copied.
- **Restore memory bound** (rustic_core 3bb01f1, 256 MiB in flight): restic's
  restorer already runs `--connections` (default 5) pack workers and streams each
  pack; restic-bko was never OOM-killed in the 3 GiB OpenBSD image
  (memory `openbsd-restore-oom` is rustic-only).
- **SnapshotOrigin / stock-snapshot rules**: restic e0dc2e91e does the same
  (missing attributes unknown in a stock snapshot).

## Not applicable to restic (rustic-only by nature)

- Haiku support (Go has no Haiku port; Haiku has rustic only).
- Time-zone fallback without `/etc/localtime` (jiff warnings; Go is silent).
- zstd contexts / Rabin tables per thread, mimalloc, static musl build (Rust build
  and speed matters; restic is static Go already).
- `diff` phantom top directory (a regression of rustic-bko's own `LocalSource`).
- `--dry-run-warmup` (a rustic option restic lacks).
- `merge`'s program version (restic has no `merge`).
- The vendored walkdir, chunker hashing fix, file size = bytes read (rustic
  matching restic's behaviour).
- Upstream rustic_core fixes #544-#640 (rustic's own bugs).

## Plan

G1-G3 in the restic fork on `backotter` (internal/restorer, cmd/restic/cmd_restore.go),
with unit tests; version 0.19.1+backotter.7; BACKOTTER.md names the options as
shared with rustic-bko. Verify with `testvm/compare/exact-rt.sh` run against
restic-bko (it takes the archiver as its argument; it may need a restic mode),
plus a compare round on a Linux guest and a BSD guest for no regressions.

## Before / after

| # | Before (0.19.1+backotter.6, 58a695576) | After (0.19.1+backotter.7, aa72d65cc, unpushed) |
|---|---|---|
| G1 `--exact` | missing | done: 9ab06e826 (`internal/restorer/exact.go`, `internal/fs/selinux_*.go`) |
| G2 `--clone-from` | missing | done: 1bdeba30b (`internal/fs/clone*.go`) |
| G3 `--target-metadata` | missing | done: 9f560bdf1 |
| docs, version | | aa72d65cc (BACKOTTER.md, doc/050_restore.rst) |

Remaining differences from rustic-bko (small, accepted): `--clone-from`'s PATH
must be a directory (rustic also takes a file); `--exact` means `--overwrite
always` and refuses another `--overwrite`; owners are compared only as root;
the clone count is printed after the restore rather than before; a failed
SELinux label removal is one warning with a count. Shared by both tools, not
fixed: an exact restore re-links hard-link groups each run (their ctime moves),
and on a mount with atimes a kept symlink's times are set again.

Verified 2026-11-14:

- Unit tests on Chopin (go1.25.10): restorer 60 pass and fs 104 pass as a user;
  as root (loop btrfs, FICLONE, no ctime moves, SELinux) restorer 138 and fs 110
  pass. `go test ./...`: 50 packages ok; two cmd/restic stdin tests need `python`
  on PATH (pass with it). `go vet` clean for linux and the BSDs, illumos, darwin
  and windows.
- `testvm/compare/exact-rt.sh` (now runs with either tool): PASS with restic-bko
  .7 and with rustic-bko .12.
- Farm, with rustic-bko 0.11.4+backotter.12 (no Linux .11 binary exists), on Cage
  and Copland under cmp-run.sh: `run-all.sh --platforms linux` (ext4, btrfs, xfs,
  identity against .6) PASS 15/15; `--platforms sparse` (ext4) PASS;
  `run-guest-rt.sh` freebsd15, netbsd11 and openbsd8 PASS 4/4 directions each;
  `compare.py` debian13 all four directions boot, the only difference being the
  torture socket, which is expected.
- Not run: zfsguests, dragonfly, illumos, freebsd15-ufs, alpinebsd, nonroot
  (affected.sh names them all; for the next full round).

Follow-up: bko-holt `load` (`holt/src/load/commands.rs`, the `--btrfs-snapshots
shared` check) can accept restic-bko 0.19.1+backotter.7, mapping
`--clone-from`/`--exact`/`--target-metadata` into restic's command line.
