# RustAudit1: our Rust against the Rust community's best practice

Status: approved 2026-11-20, with every recommendation (D1-D12).  Not run.

A plan to audit, and then fix, every line of Rust BackOtter owns: all of
bko-holt (`holt/`), and our patches to rustic_core, rustic and the vendored
walkdir on the `backotter` branches of the forks, not the upstream code under
them.  The bar is what the Rust project and its best-known writers set down
(the list is under "The bar"), weighed against the few rules of our own that
win over it (under "Our rules that win").  The code and its docs both.

**Conventions.** RA0-RA9 are the steps, D1-D12 the decisions (each
approved as recommended).  *Check* marks
a fact to settle at the step named.  est. marks an estimate.  Figures are from
main at 4099b5c (holt) and the forks' `backotter` heads (rustic_core 695d676
plus the unpushed 3fd3921, rustic 2c3b237), read 2026-11-20; the clippy
baselines below were run on Chopin that day, from `git archive` copies under
`/bkoscratch/rust-audit-baseline/`, and changed nothing.

## Summary

- **Three phases.**  First tools: rustfmt, clippy, rustdoc and the compiler's
  lints, run per target, with a lint policy committed to each crate (RA1-RA3).
  Then people: ~19 review units, each read by a High-effort reviewer against
  one checklist, every finding of severity 1 or 2 checked by a second reader
  before anyone fixes it (RA4-RA5).  Then fixes, in small commits, each one
  proved not to change behaviour by the tests we already have (RA6-RA8).
- **"Ours" in the forks is made exact, not guessed:** a line is ours when
  `git blame` gives a commit whose subject starts `backotter:`; the 19
  upstream fixes cherry-picked onto rustic_core are not ours.  walkdir's
  patch is our diff from the crates.io 2.5.0 source.  Every tool's output is
  filtered to our lines before anyone reads it (RA0).
- **Behaviour must not move.**  holt's corpus dumps and golden load plans stay
  byte-identical through every refactor commit; the forks keep passing
  `testvm/audit/fork-tests.sh` against stock with no fork-only failure, and
  restic-bko and rustic-bko still restore each other's snapshots.
- **The biggest risks found so far, to look at first:** holt's hand-declared
  `struct statvfs` (written for glibc, compiled for musl builds too, *Check*);
  ~130 `unsafe` sites in our rustic_core code under a file-wide
  `#![allow(unsafe_code)]` (upstream forbids unsafe; we changed `forbid` to
  `deny`); holt's parsers of on-disk formats, which read disks that may be
  damaged, and where clippy counts ~1,850 indexing or slicing sites that can
  panic across the crate (tests included); and the shell commands `bko-holt load` writes as strings.

## The bar

What "best practice as the experts define it" means here, so that every
reviewer holds the same one:

| Area | Source |
|---|---|
| Formatting | The Rust Style Guide, as `rustfmt` applies it by default |
| Lints | Clippy's default groups (correctness, suspicious, style, complexity, perf) as errors; `pedantic` read finding by finding; a few `restriction` lints chosen per module (D3) |
| API design | The Rust API Guidelines checklist (naming C-CASE/C-CONV/C-GETTER, common traits C-COMMON-TRAITS, errors C-GOOD-ERR, type safety C-NEWTYPE/C-CUSTOM-TYPE, C-DEBUG, future-proofing C-STRUCT-PRIVATE) |
| Docs | The rustdoc book's "How to write documentation" and RFC 1574: a one-line summary, then `# Errors`, `# Panics`, `# Safety` where they apply, intra-doc links, a `//!` per module |
| Errors | The API Guidelines' C-GOOD-ERR, the std `Error` trait, and the Book's ch. 9: panics only for bugs, never for bad input |
| Unsafe and FFI | The Rustonomicon and the std docs: every `unsafe` block with a `// SAFETY:` comment, every `unsafe fn` with `# Safety`, the smallest unsafe scope, `unsafe_op_in_unsafe_fn`, FFI struct layouts proved against the platform's headers |
| Editions and Cargo | The Edition Guide (2024), the Cargo book (`rust-version`, the `[lints]` table) |
| Broader judgement | Effective Rust (Drysdale) and the Rust Design Patterns book, for what the lints can't see: ownership and borrowing idioms, iterator use, newtypes, module layout, dead code |
| rustic_core and rustic | Upstream's own bar on top: its workspace lints (already pedantic + nursery as warnings), `rustfmt`, `dprint.json`, `deny.toml` |

## Our rules that win

These beat a generic recommendation; a finding that only asks to break one is
recorded and dropped, not fixed.

1. **holt has no dependencies** (its Cargo.toml says why: offline builds with
   Debian's own cargo).  No `thiserror`, `anyhow`, `serde`, `libc` or `nix`:
   their idioms are written by hand.  That includes holt's own JSON reader.
2. **holt's floor is Rust 1.85** (Debian 13).  Clippy runs with `msrv =
   "1.85"` so it suggests nothing newer.
3. **The forks stay rebasable onto each upstream release** (the forks'
   `BACKOTTER.md`):
   no reformatting or renaming of upstream lines, our code in our own files
   where it can be, hunks in upstream files kept small.  Upstream code is
   out of scope even where it breaks the bar.
4. **Repositories stay readable by stock restic and rustic**, and the two
   forks keep node parity and cross-restore (cross-tool compat is a courtesy,
   not byte-identical trees).
5. **Output doesn't change in a style fix.**  A finding that wants holt's JSON,
   its plans, its messages or its exit codes changed is a behaviour change: it
   goes to the user, not into a cleanup commit.

## What the code is

| Part | Size | Scope |
|---|---|---|
| bko-holt `src/` | 86 files, 58,875 lines (7,492 of them `scenarios.rs`, which is `#[cfg(test)]`) | all |
| bko-holt `tests/` | ~4,500 lines in 10 files, plus `tests/corpus`, `tests/fixtures` | all, with a lighter checklist |
| bko-holt docs | `holt/README.md` (358 lines), the rustdoc, `--help` text, `tests/**/README.md` | all (D1) |
| rustic_core, files we added | 23 files, ~14,600 lines: `backend/osattrs.rs` and `osattrs/*` (per OS), `node/generic_attributes.rs`, `commands/restore/{exact,files_writer}.rs`, `tests/integration/merge.rs` | all |
| rustic_core, our hunks in upstream files | ~50 files, ~6,900 added lines; the big ones `commands/restore.rs` (+2,623), `tests/integration/restore.rs` (+1,390), `backend/local_destination.rs` (+1,247), `backend/ignore.rs` and `ignore/mapper.rs` (+700) | our lines only |
| rustic_core `vendor/walkdir` | 2.5.0 plus our `longpath.rs`, `noatime.rs` and hunks in `dent.rs`, `lib.rs`, `util.rs` | our diff only |
| rustic | 60 commits, ~1,060 added lines over 28 files: `commands/{restore,backup,diff}.rs`, `application.rs`, `config/{logging,hooks}.rs`, tests | our lines only |
| fork docs | `BACKOTTER.md` in rustic_core (1,447 lines) and rustic (445), the vendored walkdir's README note, our rustdoc | all |

Out of scope: upstream code; `build-backotter.sh` and every other shell
script (not Rust; D1 can bring the one in rustic in); `docs/design/*.md`,
which are plans of their time, not docs of the code.

## The baseline so far

### bko-holt

Run on Chopin with rustc and clippy 1.94.1, `cargo clippy --all-targets`, no
lint config (holt has none today):

- **Default clippy: ~23 warnings**, nearly all style (9 `type_complexity`, 3
  collapsible `if`s, one 256-byte enum variant gap, one 8-argument function).
  The code is already clean where clippy is sure.
- **With `pedantic`, `nursery` and six restriction lints: ~9,000 warnings**,
  most of them restriction lints nobody turns on crate-wide:

| Lint (group) | Count | Reading |
|---|---|---|
| `arithmetic_side_effects` (restriction) | 1,222 | only matters where the numbers come from a disk |
| `indexing_slicing` (restriction) | 1,855 | the same: a panic on a damaged disk is a bug |
| `unwrap_used` / `expect_used` (restriction) | ~1,770 | all but ~25 in tests (`scenarios.rs` alone 422), where they are right; the ~25 to read |
| `doc_markdown` (pedantic) | 891 | mechanical: backticks round code in docs |
| `missing_docs` (rustc) | ~760 | 393 struct fields, 94 functions, 80 methods, 78 variants... on a lib whose 30 `pub mod`s exist for its own binary and tests (D6) |
| `unreadable_literal` (pedantic) | 333 | mechanical |
| `must_use_candidate` (pedantic) | 391 | usually allowed crate-wide |
| `use_self` (nursery) | 254 | mechanical |
| `too_long_first_doc_paragraph` (nursery) | 153 | docs |
| `format_push_string` (restriction) | 130 | small perf |
| `panic` (restriction) | 117 | tests included; the non-test ones to read |
| casts that may truncate or wrap | ~170 | to read where a value comes from a disk |
| `missing_errors_doc` (pedantic) | 34 | docs |

- **`unsafe`: 3 blocks**, all in `sys.rs` (`geteuid`, `statvfs`), each with a
  `SAFETY:` comment.  `statvfs`'s struct is declared by hand "as glibc's on
  64-bit Linux" under `cfg(all(target_os = "linux", target_pointer_width =
  "64"))`, which also takes in the static musl builds (NixOS, aarch64-musl):
  *Check* at RA4 that musl's layout is no larger and its fields line up.
- **Errors:** 63 functions return `Result<_, String>`; one error type exists.
  No `process::exit` in the library.
- **Size:** `load/commands.rs` is 7,349 lines, `boot.rs` 3,028, `load/fit.rs`
  2,405, `redact.rs` 2,015, `report.rs` 1,973.
- **Tests:** 716 `#[test]`s, no doctests.
- **Formatting:** unknown: Chopin's toolchains have no rustfmt component
  (`cargo fmt` fails); lines in the code run past 100 columns, so default
  rustfmt will rewrap much of it (RA1).
- **Toolchains on Chopin:** 1.85.1, 1.94.1 and nightly-2026-10-31 (no miri
  component); targets x86_64 gnu and musl, aarch64 musl.

### rustic_core

`cargo clippy --workspace --all-targets` on Linux at 1.94.1, under upstream's
own workspace lints (pedantic and nursery as warnings):

- **365 warnings, ~355 of them `doc_markdown`** (missing backticks), the
  rest a handful: three `unnecessary_wraps`, a collapsible `if`, a 110-line
  function, two doc-list indents.  131 are in files we added; the other 234
  sit in upstream files we changed, nearly all in `commands/restore.rs` (76),
  `local_destination.rs` (55) and our restore tests (43), so most are likely
  on our lines (RA0's filter settles it).  Upstream's own code is clean under
  these lints, so ours is the outlier.
- **This is Linux only.**  `osattrs/{freebsd,netbsd,openbsd,dragonfly,illumos,
  haiku}*.rs` compile only on their own OS; no lint has read them yet.
- **`unsafe`: ~130 sites** in our files, under a file-wide
  `#![allow(unsafe_code)]` in `backend/osattrs.rs`, after we changed the
  workspace's `unsafe_code = "forbid"` to `"deny"`.
- **Dependencies we added:** `libc`, `same-file`, `lockable`, and the
  vendored walkdir (as a `[patch.crates-io]`, which also reaches the `ignore`
  crate's walker).

## Steps

### RA0: the scope, as files (Mac, est. half a day)

A script, `testvm/audit/rust-scope.sh` (Mac-side, beside `to-audit.sh`),
writes per fork a list of `FILE:LINE` that are ours:

- rustic_core and rustic: `git blame -w -M -C --line-porcelain backotter`
  over every file the fork's diff from its stock tag touches, keeping lines
  whose commit's subject starts `backotter:`.  Added files are whole.
- walkdir: the diff from the crates.io `walkdir-2.5.0` source (from a host's
  cargo registry), not from our import commit, so that the import itself is
  checked to have been clean.
- holt: every file.

A filter, `testvm/audit/rust-filter.py`, keeps from any tool's
`--message-format=short` output only diagnostics on those lines.  Both are
tools, not product code: they go to a High agent like any other source change.

### RA1: toolchain (Chopin, needs D2)

`rustup component add rustfmt` for 1.94.1 and 1.85.1 on Chopin (it has none);
and for the cross-target lint runs `rustup target add` for
`x86_64-unknown-freebsd`, `x86_64-unknown-netbsd` and `x86_64-unknown-illumos`
(std ships for these three; clippy is a check build, so no linker is needed,
*Check* that rustic_core's C build scripts, zstd's say, don't need a cross C
compiler; if they do, those three run natively like the rest).  OpenBSD,
DragonFly and Haiku have no rustup std: their lint runs go on the native
guests and toolchains `stick/build-round.sh` already builds them with
(DragonFly's pinned nightly, `haiku6`), *Check* each has clippy.

### RA2: the mechanical baseline (farm, via pick-host)

Per crate and per target, run and keep (in `testvm/audit/rust/` on the Mac,
like `stock-baseline.txt`):

- `cargo fmt --check` (the diff size per file);
- `cargo clippy --all-targets` with the lint set of D3, at 1.94.1, filtered by
  RA0;
- `cargo doc --no-deps --document-private-items` warnings (broken intra-doc
  links, bare URLs, unclosed HTML), filtered;
- `cargo +1.85.1 check --all-targets` for holt (the floor holds);
- `cargo test`: for holt both toolchains; for the forks `fork-tests.sh`
  against the stock baseline it already keeps;
- for the forks, `cargo deny check` with upstream's `deny.toml` on the
  dependencies we added (`libc`, `same-file`, `lockable`, the vendored
  walkdir), *Check* cargo-deny is installable on Chopin (it's a host tool,
  not a dependency).

Targets: holt for every target `build-round.sh` builds it for (*Check* the
list there: Linux gnu/musl x86_64 and aarch64 at least, and the BSD,
illumos and Haiku families its `other/` module serves); rustic_core for
Linux, FreeBSD, NetBSD, illumos (cross) and OpenBSD, DragonFly, Haiku
(native), since each `osattrs/*.rs` only compiles on its own OS and a Linux
run sees none of them.

### RA3: lint policy, committed (one High agent, needs D3)

- **holt**: a `[lints]` table in `Cargo.toml` and a `clippy.toml` (`msrv =
  "1.85"`, `allow-unwrap-in-tests`, `allow-expect-in-tests`,
  `allow-indexing-slicing-in-tests`).  Rust: `unsafe_op_in_unsafe_fn`,
  `unreachable_pub`, `missing_docs` (pub items, after D6 shrinks them),
  `rust_2018_idioms`, `unused_qualifications`.  Clippy: the default groups as
  errors in the audit's own runs; `pedantic` as warnings with a short
  allow-list, each with its reason (`must_use_candidate`,
  `module_name_repetitions`, `similar_names`); `undocumented_unsafe_blocks`
  and `multiple_unsafe_ops_per_block`; and in the parser modules only
  (`probe/*`, `parttable`, `luksheader`, `mdsb`, `bootcode`, `bytes`,
  `other/{bsdlabel,geli,softraid,labelled}`), a module-level
  `#![warn(clippy::indexing_slicing, clippy::arithmetic_side_effects,
  clippy::cast_possible_truncation, clippy::unwrap_used)]`, because there a
  panic is a damaged disk crashing a restore.
- **rustic_core**: upstream's workspace lints as they are; the file-wide
  `#![allow(unsafe_code)]` in `osattrs.rs` narrowed to the FFI functions
  that need it, with `undocumented_unsafe_blocks` on in our files.
- **rustic**: upstream's lints as they are.

### RA4: review units (High agents, read-only; D11 picks the vehicle)

Each unit is read whole by one reviewer with: the RA0 scope, the RA2 output
for it, the checklist below, "The bar" and "Our rules that win".  It writes
findings to the ledger (RA5) and changes nothing.

| Unit | Files | Lines | Focus |
|---|---|---|---|
| H1 | holt `sys`, `blockdev`, `mounts`, `store`, `json`, `bytes`, `testutil` | ~3,500 | the FFI and `unsafe`, the JSON reader, process running |
| H2 | holt `parttable`, `probe/*`, `luksheader`, `mdsb`, `bootcode` | ~6,400 | parsers of untrusted bytes: panics, overflow, truncating casts |
| H3 | holt `report`, `osrel`, `efi`, `arch`, `lib`, `main`, `bin/` | ~4,500 | dump side, CLI, exit codes, help text |
| H4 | holt `boot` | 3,028 | one file, large functions |
| H5 | holt `redact`, `bugreport`, `recovery` | ~3,600 | privacy: what leaves the machine; key handling |
| H6 | holt `load/commands` | 7,349 | shell command strings: one quoting function, tested on hostile names; size and splitting |
| H7 | holt `load/{fit,plan,layout,target,shared,mod,status,exec,readback,writetable,rawvol,sizes,umounttree}` | ~8,900 | the planner's core |
| H8 | holt distro modules: `{,load/}{abroot,ostree,transactional,snapcore,qubes,truenas,zfs_be,zfsbootmenu}`, `load/{reformat,rewrite,release,efidedupe}` | ~10,300 | repetition across them, shared shapes |
| H9 | holt `other/*` | ~4,900 | BSD, illumos, Haiku sides |
| H10 | holt `tests/`, `scenarios.rs`, README, test READMEs | ~12,000 | test code idiom (light), docs against the code |
| R1 | rustic_core `osattrs.rs`, `node/generic_attributes.rs`, our hunks in `node.rs`, `node/modification.rs` | ~2,800 | the abstraction, the attribute names' contract with restic-bko |
| R2 | `osattrs/{linux,sparse}.rs` | ~4,050 | Linux FFI: ioctls, FIEMAP, xattrs; struct layouts |
| R3 | `osattrs/` BSD files (8) | ~1,900 | BSD FFI |
| R4 | `osattrs/` illumos (6) and Haiku (2) files | ~3,000 | illumos and Haiku FFI, hand-declared structs |
| R5 | `commands/restore.rs` (our lines), `restore/{exact,files_writer}.rs` | ~4,000 | restore's flow, concurrency, error paths |
| R6 | `backend/local_destination.rs` (our lines) | ~1,250 | filesystem writes, PATH_MAX, put-backs |
| R7 | every other hunk in rustic_core, and the tests we added | ~2,900 | small hunks, each read with its upstream context |
| R8 | the walkdir patch | ~500 | minimal and marked; licence files kept |
| R9 | rustic's hunks, both `BACKOTTER.md`s | ~1,060 + 1,890 docs | CLI, config, docs against the code |

The checklist, per item read:

1. **Soundness:** every `unsafe` justified, minimal, commented; FFI structs
   and constants checked against the platform headers (cite file and
   version); no UB on any target the cfgs admit.
2. **Panics:** none reachable from input (disk bytes, JSON, file names,
   command output, environment); `unwrap`/`expect` only on proven invariants,
   with the proof in the message; integer arithmetic on input checked or
   saturating, casts on input `try_from`.
3. **Errors:** errors carry what failed and where; no lost `source`; the
   caller never parses an error string to decide what to do (that's where a
   typed error pays, D4); `# Errors` in the docs.
4. **API and types:** API Guidelines naming; `Debug` on every type;
   newtypes or enums for stringly-typed values and `bool` parameters that
   aren't obvious at the call site; visibility no wider than needed.
5. **Idiom:** borrowing over cloning, iterators over index loops where it's
   clearer, `?` over `match` ladders, no needless allocation in loops, std
   over hand-rolled (within rule 1), early returns.
6. **Structure:** function length and nesting (clippy's
   `too_many_lines`/`cognitive_complexity` as a pointer, not a law); files
   past ~1,500 lines a split candidate along existing seams; duplication
   between modules.
7. **Docs:** summary line; `# Errors`/`# Panics`/`# Safety`; intra-doc links;
   comments that say why, not what; nothing stale (doc against code); module
   docs; README and `--help` match behaviour.
8. **Tests:** each fix-worthy finding names the test that would catch it;
   parsers have truncated and corrupted-input tests (D9).

### RA5: the ledger and the second read (High agents)

Findings go to `testvm/audit/rust/findings.tsv`: id, unit, file:line, rule
(checklist number and the bar's source), severity, summary, failure case,
fix sketch, verdict, state.  Severity:

- **S1**: unsound, a panic or wrong result on real input, a privacy leak, a
  shell-quoting hole;
- **S2**: error handling, API shape, structure, missing docs that a
  maintainer needs (`# Safety`, `# Errors`), duplicated logic;
- **S3**: style and doc polish a lint can name.

Every S1 and S2 is read again by a second High agent that tries to refute it
(the code, the failure case, whether one of our rules wins).  S3s from lints
need no second read.  Findings in upstream code, or that only ask to break
one of our rules, are closed as such.

### RA6: mechanical fixes (one commit each, in a quiet tree)

In this order, each its own commit with nothing else in it:

1. holt `cargo fmt` (D2), then `.git-blame-ignore-revs` naming it;
2. holt edition 2024 (D5): `cargo fix --edition`, then the edition bump, then
   `cargo fmt` again;
3. the RA3 lint policy;
4. machine-applicable lint fixes (`cargo clippy --fix`) a group at a time:
   `doc_markdown`, `unreadable_literal`, `use_self`, `redundant_closure`,
   `redundant_clone`...;
5. rustic_core: `cargo fmt` over our files only (upstream's are already
   formatted, *Check* at RA2 that the diff touches only our lines).

Each needs a clean `holt/` (or fork) tree: today's main has uncommitted
holt edits from other sessions (`load/commands.rs`, `scenarios.rs` and
others), so RA6.1 waits for them to land (D10).

### RA7: findings fixes (High agents, a unit at a time)

S1s first, then S2s, in unit order; one commit per finding or per tight
group, each message naming the ledger id.  Docs fixes (missing docs, `#
Errors`, `# Safety`, README drift) go with the code they describe.  A finding
that changes output (rule 5) goes to the user first.

### RA8: the proof each commit changes nothing (farm)

- holt: `cargo test` on 1.85.1 and 1.94.1 (the corpus round trips and
  `golden_plans.rs` are the byte-identical check); on RA6 commits also the
  corpus dumps and plans compared before and after; then HoltTest2's
  affected families, and HoltTest1 on the families an S1 fix touched.
- rustic_core and rustic: `fork-tests.sh` (no fork-only failures), a version
  bump `+backotter.N`, `cargo update -p rustic_core` in rustic, and the
  affected SticTest2 families plus a cross-restore check against restic-bko.
- Cross-target clippy and the native-guest builds again for any commit in
  per-OS code.

### RA9: keep it (one High agent)

`testvm/audit/rust-lint.sh`: RA2's runs as one command, exit non-zero on a
new warning in scope.  It joins CarpetBeater (D8) and the fork-rebase
checklist in `testvm/audit/README.md`; `audited-heads` gets a
`rust-practice audit` line per fork.

## Estimate

est. 19 reviewers and ~25-35 second reads at High, then ~40-80 fix commits
(most S3s mechanical, in RA6).  Review alone is about one day of wall time
in parallel; fixes and retests two to four days, mostly waiting on farm
runs.  Most of the cost is the reviewers reading ~85,000 lines.

## Decisions

- **D1: scope of docs.**  Recommended: holt's README, rustdoc, `--help`,
  test READMEs, both `BACKOTTER.md`s and our rustdoc in the forks; not
  `docs/design/`, not the shell scripts.  Alternative: also shellcheck
  `build-backotter.sh`.
- **D2: rustfmt and Chopin's toolchain.**  Recommended: add the rustfmt
  component on Chopin (and the three cross targets of RA1), and reformat
  holt to the default style in one commit.  Alternative: a `rustfmt.toml`
  with a wider `max_width` matching today's lines, less churn, less
  standard.
- **D3: the lint policy** as RA3 says: default clippy clean, pedantic read
  with a short allow-list, restriction lints in the parser modules only.
  Alternative: default clippy only (cheapest, misses the parser panics).
- **D4: errors.**  Recommended: keep `Result<_, String>` where the message
  only goes to the user (it is a CLI's library, and without `anyhow` a String
  is the honest stand-in), and add a typed error, by hand, wherever a caller
  matches on an error's text.  Alternative: typed errors everywhere (large,
  touches every module).
- **D5: holt to edition 2024.**  Recommended: yes, mechanical, 1.85 has it;
  it brings `unsafe extern` blocks and `unsafe_op_in_unsafe_fn` by default.
  Alternative: stay on 2021.
- **D6: holt's public surface.**  Recommended: every `pub` no wider than the
  binary and the integration tests need (`pub(crate)` elsewhere), so the
  compiler finds dead code and `missing_docs` asks only for what is used
  from outside.  Alternative: leave it, document all ~760 items.
- **D7: big refactors.**  Recommended: split giant files along their seams
  only when the reviewer shows the seam (no logic change, its own commit);
  record a typed model in place of `json::Value` lookups as a finding with a
  proposal, not done in this audit.  Alternative: do the typed model too.
- **D8: CarpetBeater.**  Recommended: `rust-lint.sh` in every CarpetBeater
  and before each fork rebase is pushed.  Alternative: by hand only.
- **D9: input tests for the parsers.**  Recommended: a test in holt's own
  `cargo test`, no dependencies: every corpus image's structures truncated at
  each length and bit-flipped, each parser required to return an error, never
  panic.  Alternative: `cargo-fuzz` in a separate crate outside holt, on
  Chopin's nightly (stronger, needs nightly and a dependency, never shipped).
- **D10: when.**  Recommended: RA0-RA5 now (read-only); RA6 on a quiet tree,
  when the other sessions' holt edits are committed and no `t2 batch`
  run needs holt; fixes before the next fork rebase.
- **D11: the vehicle.**  Recommended: one workflow for RA4-RA5 (19 reviewers
  then the second reads, all `effort: 'high'`), which needs the user to say
  "use a workflow"; RA6-RA9 as single High agents in order.  Alternative:
  RA4 as single agents, a few at a time (slower, cheaper per hour, same
  total).
- **D12: walkdir.**  Recommended: keep it vendored (small patch, our README
  says why); the audit checks the patch is minimal and every hunk marked
  `BackOtter:`.  Alternative: a walkdir fork with `[patch]`, like rustic_core.

## Risks

- **Reformatting and edition commits collide with other sessions' work** on
  holt: they need a tree with nothing uncommitted in `holt/` (D10).
- **Lint fixes that change behaviour**: `clippy --fix` is machine-applicable,
  not always meaning-preserving (`map_or` on side effects, float compares);
  RA8's byte-identical checks are the guard.
- **Per-OS code no Linux run sees**: rustic_core's osattrs for six OSes and
  holt's `other/`; a fix there without its native build is untested (RA8).
- **Rebase cost** grows with every upstream line we touch: rule 3.
- **Reviewer drift** toward taste: the checklist and "Our rules that win"
  are in every brief; S3s without a lint behind them are dropped.
