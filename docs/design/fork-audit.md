# ForkAudit1: an audit of rustic-bko's patches, and only those

Status: proposed 2026-11-22, not run.  Replaces RustAudit1 for now: holt is
out of scope (the user, 2026-11-22: "skip it, and audit rustic-bko's patches
only").  restic-bko (Go) is out of scope as well.

## What is audited

rustic-bko is built from two forks, each on branch `backotter`:

| Fork | Upstream base | Our commits | Our diff |
|---|---|---|---|
| `~/git/rustic_core` | `rustic_core-v0.13.0` (1030be4) | 97 `backotter:` commits, plus 20 upstream fixes we cherry-picked | 100 files, +28,127 / -544 lines |
| `~/git/rustic` | `v0.11.4` (6b96f97) | 64 `backotter:` commits | 28 files, +1,119 / -86 lines |

The +28,127 lines in rustic_core break down as:
- about 20,200 in `crates/core/src`;
- about 1,800 in our tests;
- 4,500 in vendored walkdir, of which our patch is roughly 500 lines;
- 1,500 in `BACKOTTER.md`.

**"Ours" means our modifications, never upstream's code.** A line counts as ours if `git blame -w -M -C` gives it to a `backotter:` commit; a file such a commit added counts whole. For walkdir, ours is our diff from crates.io 2.5.0. `testvm/audit/rust-scope.sh` already computes this. Its last scope (`testvm/audit/rust/scope-rustic_core.txt`) was taken at 695d676, so F0 below redoes it at today's heads.

Every tool's output and every reviewer's finding is filtered to these lines. A finding on an upstream line is closed as "upstream", not fixed.

The cherry-picked upstream fixes are not ours either. We only check that they still apply cleanly and are not undone by a hunk of ours.

## Where we start: RustAudit1's fork pass (2026-11-20)

RustAudit1's review step RA4-RA5 already read these forks once, in units R1-R9, at rustic_core 695d676. Its findings are in `testvm/audit/rust/findings.tsv`:

| | Fixed | Open | Changes output | Other |
|---|---|---|---|---|
| S1 | 2 | 0 | 2 | |
| S2 | 6 | 20 | 12 | |
| S3 | 1 | 79 | 5 | 1 deferred |

So ForkAudit1 is not a first read. It does three jobs:

1. **Finish that pass.** Fix its open S2s and its S3s on our lines. Bring the 14 output-changing S1/S2 findings to you as decisions.
2. **Read what it never read:**
   - the 19 commits since 695d676, about +1,200 lines (PATH_MAX, the adaptive longpath, the osattrs fixes, the re-audit fixes);
   - per-OS code that no tool has compiled or linted: the BSD, illumos and Haiku files build only on their own OS, and RustAudit1 linted on Linux only.
3. **Go deeper where High reading is weakest**, with tools that run the code rather than read it: Miri, sanitizers, cross-target clippy, malformed-input tests and a parity test.

## Steps

### F0: scope at today's heads (one High agent)

- Rerun `rust-scope.sh` at rustic_core ead8a63 and rustic 3853eb3 (the `.22` version commits, local and unpushed), plus the walkdir diff.
- Write `scope-*.txt` and a per-file table: our lines, and which unit reads them.
- List every `backotter:` commit with the reason its message gives. F9 uses this list.

### F1: tools, per target (farm, via pick-host)

- `cargo clippy --all-targets` under upstream's workspace lints, on every target we ship:
  - Linux natively on Chopin;
  - FreeBSD, NetBSD, OpenBSD, DragonFly, illumos/OmniOS and Haiku inside the build guests build-round already uses.
- `cargo doc` with `-D rustdoc::broken_intra_doc_links`.
- `rustfmt --check` on our files only.
- Output goes through `rust-filter.py`, keeping our lines only.
- This is the step RustAudit1 never ran for six of our seven targets.

### F2: dynamic checks (farm)

- **Miri** (nightly on Chopin) on rustic_core's unit tests that touch our code and run without FFI: sparse-map maths, attribute encoding and decoding, restore planning, the longpath splitter, ignore mapping. Tests that make FFI calls are skipped, and the skips are listed.
- **AddressSanitizer and UndefinedBehaviorSanitizer** (`-Zsanitizer`) on Linux, over our integration tests (restore, merge, osattrs on tmpfs/ext4/xfs/btrfs).
- **Malformed-input tests** for every decoder of ours that reads bytes another machine wrote:
  - generic attributes;
  - Haiku attribute blobs;
  - illumos xattr metadata, sysattrs and ACLs;
  - BSD extattr lists;
  - the sparse map.

  Each is truncated at every length and bit-flipped, and must return an error and never panic. They go in as ordinary `#[cfg(test)]` tests, with no fuzzing crate, so they stay in the fork.
- **Parity test:** restic-bko and rustic-bko back up the same tree and restore each other's snapshots, as SticTest2's four directions do. It runs on each OS's guest and feeds F6.

### F3-F9: review units (High agents, read-only)

Each unit is read whole by one reviewer. The reviewer gets:
- the F0 scope and the F1/F2 output for that unit;
- RustAudit1's checklist, its bar and its "Our rules that win";
- the earlier R-findings for those files.

The reviewer confirms each earlier finding still stands, or closes it, and adds new findings. It changes nothing.

| Unit | What | Focus |
|---|---|---|
| F3 | `osattrs/{linux,sparse}.rs`, `osattrs.rs`, `node/generic_attributes.rs`, our hunks in `node.rs` and `node/modification.rs` | Linux FFI (ioctls, FIEMAP, xattrs), struct layouts against glibc and musl headers, the attribute names restic-bko must agree on |
| F4 | BSD `osattrs/*` (freebsd, freebsd_acl, freebsd_netbsd, netbsd, openbsd, dragonfly, bsd_held, bsd_flags, bsd_extattr) | each struct and constant checked against that OS's headers, citing file and version; flags and ACL maps |
| F5 | illumos `osattrs/*` (illumos, illumos_sysattr, illumos_acl, illumos_idmap, illumos_xattrmeta, illumos_project), Haiku `haiku{,_attrs}.rs` | hand-declared structs and dirent `d_name` (RustAudit1's S1 was here); the extended-attribute fd dance |
| F6 | `commands/restore.rs` (our lines), `restore/{exact,files_writer}.rs` | concurrency and ordering, error paths, partial restores, the restic interop the parity test feeds |
| F7 | `backend/local_destination.rs`, vendored walkdir (`longpath.rs`, `noatime.rs`, `dent.rs`, `lib.rs`, `util.rs`, `error.rs`) | PATH_MAX, symlink-depth and TMPDIR handling, put-backs, races on paths |
| F8 | every other hunk in rustic_core: ignore and its mapper, decrypt, blob tree and packer, chunker, prune, forget, merge, repair, cache, the backend crate's `local.rs` and `opendal.rs` | small hunks, each read with its upstream context; no hunk that could stop stock restic or rustic reading the repository |
| F9 | rustic's 28 files, both `BACKOTTER.md`s, and the commit list from F0 | each patch against the reason its commit gives: still needed, or fixed upstream since 0.13.0/0.11.4 and droppable? Docs checked against the code |

### F10: second read

Every new or reconfirmed S1 and S2 goes to a second reader, who tries to refute it from the code and the failure case, and checks whether one of our rules wins. Lint S3s need no second read.

### F11: fixes (High agents, one finding or a tight group per commit)

- **Order:** S1, then S2, then the S3s on our lines.
- **Commit messages** name the ledger id.
- **Output changes:** a fix that changes output, file attributes on restore or the repository format goes to you first. That includes the 14 already marked "changes output".
- **Rebasability:** every fix keeps the fork rebasable. Our code stays in our files, upstream lines are not reformatted, and hunks in upstream files stay small.

### F12: proof (farm)

Each fix is proved by:
- `testvm/audit/fork-tests.sh` against stock: no failure that only the fork has;
- a `+backotter.N` version bump and a rustic `Cargo.lock` pin;
- the build round on every OS;
- SticTest2 for the OS families the commit touches, plus the four-direction cross-restore;
- BackShot2's PATH_MAX replay when F7's files change.

Then one new bin round, rel13, carrying all of it.

## Estimate

- **Agents:** about 7 reviewers plus 15-25 second reads, then roughly 30-50 fix commits.
- **Review time:** about half a day of wall time in parallel, once F1 and F2 have run.
- **F1 and F2 time:** half a day on the farm, mostly the six guest lint runs and Miri.
- **Fixes and retests:** one to two days, mostly waiting on build rounds and SticTest2.
- **Cost driver:** reading about 21,000 of our own lines, a quarter of RustAudit1's.

## Decisions

- **D1: re-read or reuse?** Recommended: the reviewers start from the R-findings but read every file whole again. The first pass predates 19 commits, and it read the BSD, illumos and Haiku code with no compiler behind it. Alternative: read only what changed since 695d676, which is cheaper and leaves the old blind spots.
- **D2: dynamic tools.** Recommended: all of F2. Miri and the sanitizers need the nightly toolchain, which is only on Chopin. Alternative: F1 only, which finds no undefined behaviour at run time.
- **D3: the 14 output-changing findings.** Recommended: put them to you one by one once F10 has confirmed them, each with its effect on restic-bko parity. Alternative: decide them as a batch.
- **D4: dropping obsolete patches.** Recommended: F9 lists the candidates, and dropping one is a separate decision for you, never part of this audit's fixes.
- **D5: effort.** Decided by the user on 2026-11-22. Everything runs at High, except F10's second reads of findings in F3-F5 (unsafe FFI) and F6 (restore concurrency). Those run at Extreme (`effort: 'max'`), because confirming one means checking each layout against the OS headers, or each thread interleaving. That is about 10-15 agents.
- **D6: the vehicle.** Recommended: one workflow, with F0-F2 on the farm, then F3-F9 in parallel, then F10. F11 runs as one High agent per finding group. F12 is scripted.

## Risks

- **Version and timing:** fixes land on `.22` and later while rel11 is what is promoted. The new round, rel13, is built and tested once, at the end.
- **Upstream releases:** a 0.13.x or 0.11.x release during the audit means a rebase first, so F0 is redone at the rebased heads.
- **Per-OS lint runs** need the build guests booted, and compete with Test2 for the farm. They run when `t2 batch status` is idle.
