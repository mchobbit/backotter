# RustAudit1 holt delta c4917fc..689904ed5ac8

2026-11-24. The bko-holt half of RustAudit1 (plan docs/design/rust-audit.md, D1-D12 as recommended), re-run on what changed in holt since the first audit (H1-H10 at c4917fc, 2026-11-20). Four review units (HD1-HD4), each finding read a second time unless S3; a recheck of the 80 open S1/S2 H rows; and the mechanical lint run. The rows are HD1-01.. in testvm/audit/rust/findings.tsv, after the header line "# RustAudit1 holt delta c4917fc..689904ed5ac8". Read-only audit: no holt file was changed.

## Counts

Severities are the second reader's; S3 rows are lint-named and were not second-read.

| Unit | Scope | S1 | S2 | S3 | Refuted | Rows |
|---|---|---|---|---|---|---|
| HD1 | load/commands.rs, exec.rs, grubprefix.rs, luks1digest.rs, codes.rs | 1 | 6 | 1 | 0 | 8 |
| HD2 | load/jsonout.rs, plan.rs, mod.rs, target.rs, writetable.rs, status.rs, shared.rs, rawvol.rs, layout.rs, main.rs, store.rs | 1 | 3 | 2 | 1 | 7 |
| HD3 | load/fit.rs, rewrite.rs, reformat.rs, distro modules, other/* | 2 | 4 | 1 | 0 | 7 |
| HD4 | lzma.rs, sys.rs, bytes.rs, recovery.rs, redact.rs, bugreport.rs, boot.rs, luksheader.rs, probe/luks.rs, probe/zfs.rs, report.rs | 1 | 4 | 1 | 0 | 6 |
| All | | 5 | 17 | 5 | 1 | 28 |

## Confirmed S1 and S2

- **HD1-01** S1 `holt/src/load/exec.rs:30`: Cmd::private hides lilo.conf (password=) only in the JSON plan; report-bug still carries it in the text plan, load.log and the layout's boot.lilo options (fix redact/bugreport first; text-mode stdout is a spec question)
- **HD2-01** S1 `holt/src/load/plan.rs:256`: disks[].target.writes by argv string match: on multi-target plans md/LUKS/LVM/ZFS format stages are left out; names_node over-matches (nvme0n1 vs nvme0n10p1, sda vs sdab)
- **HD3-01** S1 `holt/src/other/geli.rs:452`: a GELI provider keyed_by "both" is planned under --json as temporary with no key_needed; boot then prompts on /dev/tty, fails, and the temporary key (the provider's only key) is removed
- **HD3-02** S1 `holt/src/other/load.rs:55`: wedge_clashes misses two target GPT partitions with the same label: both get NAME=label, and the second is never formatted or restored
- **HD4-01** S1 `holt/src/redact.rs:639`: scan never learns users from /var/home/NAME (ostree) or /usr/home/NAME (FreeBSD 13), so their names stay in public bug reports
- **HD1-02** S2 `holt/src/load/commands.rs:5571`: close's quoted namespace sweep matches the raw target against mountinfo's \040-escaped paths, so a target with whitespace never unmounts other namespaces' copies (qubes.rs:149 too)
- **HD1-03** S2 `holt/src/load/commands.rs:4592`: a crypttab key path with `..` is refused both without and with --key-file; the first message sends the user to the option that can't help
- **HD1-04** S2 `holt/src/load/grubprefix.rs:89`: module_prefix's `p + 8 <= end` is unchecked after p = raw + off; an offset near usize::MAX panics in debug, wraps in release
- **HD1-05** S2 `holt/src/load/commands.rs:3502`: fat_time_option's abs() and negation overflow on sys_tz_minuteswest = i64::MIN
- **HD1-06** S2 `holt/src/load/commands.rs:234`: KeyAsk.kind and .unlock are closed &'static str sets compared as strings (doc already lacks "zfs"); enums wanted
- **HD1-07** S2 `holt/src/load/commands.rs:2203`: RUN_DIR/keys naming and key-path rules are spelled in several places (new_key, ds_props, restored_key_files, keystores)
- **HD2-02** S2 `holt/src/load/jsonout.rs:707`: a refused --repo URL with a password still reaches the plan doc's args, stages and text, and last-load.json
- **HD2-04** S2 `holt/src/load/target.rs:560`: table_record_text reads serial/wwid only from Linux sysfs; off Linux the erase gate binds a target by node and size alone (jsonout::kernel_identity already knows better)
- **HD2-05** S2 `holt/src/load/mod.rs:214`: load --help and README omit --json, --expect-plan, BKO_HOLT_NO_COMMIT and exit 2's new meanings
- **HD3-03** S2 `holt/src/load/fit.rs:692`: verdict's FitOverPct arm turns every other refusal (member, swap, no usage, unsupported fs) into data_wont_fit
- **HD3-04** S2 `holt/src/other/load.rs:781`: FreeBSD/DragonFly/illumos close lists mount points by awk field split, so a target with a space unmounts nothing and close reports done
- **HD3-05** S2 `holt/src/load/fit.rs:616`: the bigger loop breaks at the first pct with no change; a largest partition under 100 MiB gets fit_pct null
- **HD3-06** S2 `holt/src/other/load.rs:339`: safe_run_token (x3) and the absolute/no-NUL/no-.. path predicate (x3) are copies
- **HD4-02** S2 `holt/src/report.rs:1514 (sys.rs:397-400, 512-553)`: 64-bit Linux statvfs on every layout mount has no time limit; a dead hard NFS mount hangs dump and report-bug
- **HD4-03** S2 `holt/src/bugreport.rs:287-296`: write_report's fallback open follows a symlink at PATH, truncates the target and chmods it 0600; the comment contradicts the code
- **HD4-04** S2 `holt/src/probe/zfs.rs:285`: native nvlist string arrays advance by the lossy String's length, so elements after invalid UTF-8 are misread
- **HD4-05** S2 `holt/src/recovery.rs:276 (bytes.rs:539)`: checksum's sha256 heap-copies the recovery key's entropy and frees it unwiped

## Refuted and S3

- HD2-03 `holt/src/load/plan.rs:280` refuted (S3): the softraid newline check under --json is what holt-json.md's Secrets rule 4 asks for; only the reason text is loose.
- HD1-08 `holt/src/load/commands.rs:3858` S3: 7 clippy::missing_docs_in_private_items (restriction; rustdoc: a doc comment belongs to the next item)
- HD2-06 `holt/src/store.rs:529` S3: 7 rustdoc book / RFC 1574 (summary line, doc on the item it documents); clippy::too_long_first_doc_paragraph
- HD2-07 `holt/src/load/target.rs:787` S3: 2 clippy::arithmetic_side_effects (parser-module policy); checklist 2 (arithmetic on input checked)
- HD3-07 `holt/src/other/load.rs:328` S3: 7 checklist 7 (nothing stale; doc on the right item); clippy::missing_docs_in_private_items
- HD4-06 `holt/src/lzma.rs:140` S3: clippy::missing_errors_doc, clippy::cast_possible_truncation

## Changes output: needs the user (rule 5)

These fixes would change holt's JSON, plans, messages or exit codes, so they wait for the user's say-so.

- HD1-01 S1 `holt/src/load/exec.rs:30`
- HD1-02 S2 `holt/src/load/commands.rs:5571`
- HD1-03 S2 `holt/src/load/commands.rs:4592`
- HD2-01 S1 `holt/src/load/plan.rs:256`
- HD2-02 S2 `holt/src/load/jsonout.rs:707`
- HD2-05 S2 `holt/src/load/mod.rs:214`
- HD3-01 S1 `holt/src/other/geli.rs:452`
- HD3-02 S1 `holt/src/other/load.rs:55`
- HD3-03 S2 `holt/src/load/fit.rs:692`
- HD3-04 S2 `holt/src/other/load.rs:781`
- HD3-05 S2 `holt/src/load/fit.rs:616`
- HD4-01 S1 `holt/src/redact.rs:639`
- HD4-03 S2 `holt/src/bugreport.rs:287-296`
- HD4-04 S2 `holt/src/probe/zfs.rs:285`

Two rechecked H rows were restated and also change output: H3-07 (load's gate accepts Qubes < 4.3 and ostree Fedora != 44) and H6-01 (now the same mountinfo-escape gap as HD1-02).

## Rechecks of the open S1/S2 H rows

| Fixed | Gone | Changed (restated) | Still present | Total |
|---|---|---|---|---|
| 17 | 0 | 4 | 59 | 80 |

- Fixed: H1-02 (9321085), H1-04 (a9de440), H3-01 (a898595), H4-01 (a898595), H5-01 (a898595), H5-02 (a898595), H5-03 (a9de440), H6-02 (a9de440), H6-03 (a9de440), H7-01 (a898595), H7-02 (a898595), H7-04 (094deef), H8-09 (a9de440), H9-01 (a898595), H9-02 (a898595), H9-03 (a898595), H9-07 (a898595).
- Restated (still open, the state column carries the new text): H3-07, H6-01, H6-04, H7-10. H6-01 and H6-04: the quoting is done, close's mountinfo matching and the lack of one sh helper are left; H7-10: one char-boundary panic left in guid_bytes; H3-07: Slackware part gone, Qubes/ostree part left.
- Still present: H1-03, H1-05, H1-07, H2-02, H2-04, H2-05, H2-06, H2-07, H3-02, H3-03, H3-04, H3-05, H3-06, H3-08, H3-09, H3-10, H4-02, H4-03, H4-04, H4-05, H4-06, H4-07, H4-08, H4-09, H5-04, H6-05, H6-06, H6-07, H6-08, H7-03, H7-05, H7-06, H7-07, H7-08, H7-09, H7-11, H7-12, H7-13, H7-14, H8-04, H8-05, H8-06, H8-10, H8-11, H9-05, H9-06, H9-08, H9-09, H9-10, H9-11, H9-12, H9-13, H9-14, H9-15, H10-01, H10-02, H10-03, H10-04, H10-05.
- Ledger states were wrong before this pass for the fixed rows (H3-01, H4-01, H5-01, H5-02 and others landed in a898595/a9de440 but were still "open").

## Lint run

The report as the lint unit gave it:

RustAudit1 mechanical half, holt at 689904ed5ac8. The run completed: exit 0, 134 s on the host (153 s for the job), every step ran to its end. I did not use --baseline, so baseline.txt is unchanged.

Run dir (Mac): /var/folders/n1/h355qcfd0hd25991yz7kcjgh0000gn/T/bko-audit/rust/20261124T170712 (summary.txt, filtered/, raw/). Host: m@chopin.local:~/backotter-work/bko-audit/rustlint/20261124T170712, log ~/backotter-work/bko-audit/logs/rustlint-20261124T170712.log. My copy of the console log: /private/tmp/claude-501/-Users-mchobbit-git-backotter/9c6ff995-f612-4baa-b5c1-3c9129e1e3ea/scratchpad/rustlint-h.log
The scope file was made for this run: 73487 lines ours in 104 files, up from 63495 in 97 files.

COUNTS ON OUR LINES (baseline c4917fc -> now)
- fmt (any): 3772 -> 4571 hunks (+799). Top files: load/commands.rs 598, scenarios.rs 596, boot.rs 210, load/fit.rs 178.
- clippy linux: 3123 -> 3678 (+555); musl: 3123 -> 3678 (+555)
- clippy freebsd: 3120 -> 3656 (+536); illumos: 3120 -> 3656 (+536)
- clippy openbsd: 3192 -> 3733 (+541); haiku: 3192 -> 3733 (+541)
- clippy-parser linux: 345 -> 342 (arithmetic_side_effects 213 -> 211, indexing_slicing 99 -> 100, cast_possible_truncation 31 -> 31, unwrap_used 2 -> 0)
- doc linux: 7 -> 8; check-1.85.1: 0 -> 0. Errors: 0 everywhere.
- Changes inside the linux clippy count: doc_markdown 897 -> 1182, unreachable_pub 66 -> 315, missing_docs 786 -> 656. Groups: pedantic 2170 -> 2586, rustc 901 -> 1036, complexity 15 -> 17; style 6, perf 2 and suspicious 1 are unchanged.
- Not run this time: aarch64-musl, netbsd, dragonfly (not in --targets) and --tests.

(a) DEFAULT CLIPPY GROUPS: 26 hits per Linux target, 31 unique lines across the six targets, all warnings. I classified each line with git blame at 689904ed5ac8, checking whether the blamed commit is an ancestor of c4917fc.
NEW since c4917fc (5; none is in the ledger):
- src/load/fit.rs:1317 ptr_arg [style]. `free_runs(count, used: &mut Vec<Run>)` only sorts and iterates `used`, so it could take `&mut [Run]`. S3, changes_output=false.
- src/report.rs:1215 manual_contains [perf]. `keystore_mounts.iter().any(|&m| m == p)` should be `.contains(&p)`. S3, false.
- src/other/load.rs:79 nonminimal_bool [complexity]. `!(n.starts_with("dk") && n[2..]...)` can be simplified. The `n[2..]` slice is safe because it comes after `starts_with("dk")`. S3, false.
- src/load/rawvol.rs:489 useless_format [complexity], in the #[cfg(test)] module (starts at :408). `format!` has no arguments. S3, false.
- tests/plan_json.rs:443 collapsible_match [style], nightly only (openbsd, haiku). The file is new. S3, false.
Pre-existing, per blame from commits before c4917fc. The ledger covers these through the per-unit lint-class rows (H*-"Mechanical lint classes"):
- type_complexity [complexity], 9 hits: abroot.rs:310 and :379, boot.rs:1770, load/shared.rs:595, other/freebsd.rs:134 and :178, other/geli.rs:1077, other/load.rs:187 and :231.
- collapsible_if [style]: redact.rs:585, :623, :1278.
- unnecessary_map_or [style]: load/commands.rs:2151, :3427.
- expect_fun_call [perf]: load/commands.rs:7856.
- redundant_locals [suspicious]: load/writetable.rs:189.
- useless_conversion [complexity]: load/writetable.rs:370, other/offline.rs:69.
- too_many_arguments [complexity]: other/mod.rs:366 (8/7).
- useless_format [complexity]: scenarios.rs:4828.
- map_identity [complexity]: scenarios.rs:8078.
- nonminimal_bool [complexity]: report.rs:1562.
- Nightly only (openbsd, haiku): manual_checked_ops other/labelled.rs:98, some_filter probe/ext.rs:207, unnecessary_sort_by redact.rs:862.

(b) UNSAFE LINTS: there are 0 hits for undocumented_unsafe_blocks, multiple_unsafe_ops_per_block and unsafe_op_in_unsafe_fn on all six targets.

(c) PARSER PASS IN FILES CHANGED SINCE c4917fc: 144 hits in the changed parser files (bytes.rs, probe/luks.rs, probe/zfs.rs, other/geli.rs, other/softraid.rs). luksheader.rs changed but has 0 hits. Of the 144, 9 are on lines changed since c4917fc. I read each one, and all are bounded, so there is no new finding:
- src/bytes.rs:32:32 arithmetic_side_effects. `len - max` in read_tail, under `if len > max`.
- src/probe/luks.rs:32:52 arithmetic_side_effects. `212 + 48 * i` with i < 8, a constant range.
- src/probe/zfs.rs:197:24 arithmetic_side_effects. `16.saturating_add(name_len) + 7`, where name_len is a u16, so it is at most 65558.
- src/probe/zfs.rs:201:40 and 201:52 arithmetic_side_effects. `start + 16 + name_len`. start <= b.len(), because `end` is checked at :196, and name_len <= 65535.
- src/probe/zfs.rs:204:21 arithmetic_side_effects. `start + voff <= end`, because `size >= voff` is checked at :198.
- src/probe/zfs.rs:256:51 indexing_slicing and 256:61 arithmetic_side_effects. `self.b[v..v + nelem]` after `check_end(v, nelem)`.
- src/probe/zfs.rs:281:33 arithmetic_side_effects. `v + ptrs` after `check_end(v, ptrs)`.
All other parser hits in those files are on lines from before c4917fc, which H1-12, H2-03 and H2-08 already cover.

(d) RUSTDOC (8 warnings) AND check-1.85.1 (0 errors; the 1.85.1 check finished cleanly)
NEW:
- src/load/codes.rs:357:54 rustdoc::invalid_html_tags, "unclosed HTML tag `String`". The doc line is "has_text, for a list's callers that had a Vec<String>". Fix: put `Vec<String>` in backticks. S3, false. The file is new since c4917fc, so this is not in the ledger.
Pre-existing (in the baseline's 7):
- src/boot.rs:148:45, 148:54, 149:5, 151:57 invalid_html_tags (Package, Name, Name, Architecture). Ledger H4-11.
- src/other/softraid.rs:53:5 invalid_html_tags `sd0a`. In the ledger.
- src/recovery.rs:22:6 bare_urls. In the ledger.
- src/load/mod.rs:143:22 broken_intra_doc_links "unresolved link to `,NAME`", from the doc text "--pools NAME[,NAME]". This was in the baseline's count for load/mod.rs. I found no explicit ledger row for it at this line; H7-18's mechanical rustdoc class may cover it.

SUMMARY FOR THE CALLER: the run found no S1 or S2 at 689904ed5ac8. New hits are 5 default-group clippy hits (S3, lint-named) and 1 rustdoc warning (S3). None would change output (changes_output=false). There are no unsafe-lint hits, no parser-pass hits on new lines that could panic or wrap, and no MSRV 1.85.1 errors.

