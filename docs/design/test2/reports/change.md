# Change frequency and the testing that followed each change (researcher: change)

Scope: all of backotter `main` (187 commits from 10-29 to 11-07, 175 of them non-merge) and each fork's `backotter` branch above its upstream tag. "Lines" means lines added plus deleted per commit, counted only inside the area (`git log --numstat`). Commit times are +0100; run times are UTC.

Path shorthands:
- **[B]** = `/private/tmp/claude-501/-Users-mchobbit-git-backotter/b43d6ef3-8bbc-4dc4-9768-d915a92c69e4/scratchpad` (the archiver session's reports)
- **[G]** = `…/39d24434-6f21-44ae-8f2e-b828b29e8fb2/scratchpad` (the GuestTest1 session)
- **[W]** = my working files, listed at the end

## (a) How often each part changes

| Area | Commits | Median lines (max) | Shape |
|---|---|---|---|
| restic-bko | 58 own, 5 upstream picks | 126 (1826) | 55 of the 58 landed 10-30..11-03. On 11-06: +.4 (preallocation rule) and +.5 (the picks) |
| rustic_core-bko | 56 own, 19 picks | 139 (3719) | 47 by 11-03. Upstream port on 11-05; preallocation rule and fs_type on 11-05/06 |
| rustic-bko | 38 own, 1 pick | 20 (178) | 28 touch Cargo.lock (mostly rustic_core pin bumps); only 9 touch src/ |
| holt, all of it | 17, all 11-05..11-07 | — | Four bulk drops: dd01484 (10.9k lines), b87fb09 (4.2k), 38393b8 (4.7k), b2f2535 (2.4k). Fixes are 5–32 lines (1626714, 2e6c1b5, 78018db, 3671e96) |
| · dump core (main, sys, report, blockdev…) | 10 | 31 (4794) | |
| · probe/ | 6 | 124 (3233) | |
| · load/ | 13 | 64 (3181) | `commands.rs` changed in 11 of them; every stage is in that file |
| · boot (boot.rs, bootcode, efi) | 6 | 61 (1654) | |
| · other/ (non-Linux and offline) | 2 | 1165 (2239) | 38393b8 and ae2ffd3 only |
| · osrel.rs | 7 | 30 (172) | one per OS family added |
| · version bumps | 3 | 4 | 0.2.0, 0.2.1, 0.2.2 |
| stick rescue images | 32 | 34–69 | bursts on 10-30 (11 commits) and 11-06 (7) |
| stick archiver build scripts | 18 | 15 (556) | |
| testvm/compare | 61 | 60 (2240) | the busiest area |
| testvm/stictest1 | 20 | 46 (10540) | includes two bulk imports |
| testvm/holttest1d | 18 | 94 (1225) | five judge.py rules of 1–3 lines each |
| harness core (vm-run, vm-mount, vm-lib…) | 28 | 28 (292) | |
| guest builders | 28 | 96 (1890) | 31 guests rebuilt on 11-02 (dates of `vms/*/build.log`); about 10 new guests on 11-05; 5 RAID/mirror guests on 11-07 |
| guest updates (update/, vm-update) | 19 | 30 (1058) | 131 merged updates across 40 guests (`image-state` `updates` counter, 1–5 per guest). Fleet-wide rounds on 11-03 (two), 11-04 (u3, named in 9ffba43) and 11-06 08:52–09:30Z (from `image-state` mtimes) |
| hosts | 2 reinstalls | — | Cinqcent 11-03, Chopin 11-06. Both now run kernel 6.12.115+deb13.1, ZFS 2.3.9, QEMU 10.0.13, OVMF 2025.02-8+deb13u1 |

Fork code went quiet after 11-03. bko-holt is now where the code changes; testvm/compare and testvm/stictest1 are where the harness changes.

## (b) What testing followed each kind of change, and what it found

| Change | Tests that followed | Found |
|---|---|---|
| Fork patches, 10-30..11-02 | run-all.sh fix rounds (`chopin:~/backotter-work/round4`..`round23`, platforms picked by affected.sh); SticTest1 r22 then r23 | Yes. Tool defects through r22, e.g. nixos2605's bcachefs casefold and 512-byte holes ([B]/b2/STICTEST1-R23-REPORT.md) |
| Fork "fresh target" rules, 11-03 | st1a full pass (68 min), st1b/c targeted, st1d full pass (58 min) | No tool defect; 4 harness or infra defects ([B]/st1round/REPORT.md) |
| No fork change at all | st2a (38 min) and rel1 (42 min) full passes. st2a "reused every binary from the cache", with hashes equal to st1a's | No tool defect; 2 flakes |
| rustic_core upstream port, 11-05 | fork-tests (~95 s); st1-rel2 (32 guests); gt1-rel2 (31 guests, same binaries, 3 h later) | Nothing. gt1-rel2 matches st1-rel2 on all 31 guests (exit code, PASS and FAIL counts) ([W]/st1-runs.tsv) |
| btrfs/bcachefs preallocation rule (ed28fe0, b74535444) | HoltTest1 found it (omarchy ran out of space, runs r3l–r6), not SticTest1. Then restic's TestPreallocate, and st1-rel3 (11 guests on Cinqcent; the round was stopped) | rel3: freebsd14's rustic-bko was linked against FreeBSD 15's libc (fixed in 22a5089). Found because freebsd14 was a new guest |
| Upstream fix picks (restic +.5: key/config length, swift, ParseDuration, forget, prune; rustic_core opendal; rustic docs) | st1-rel4 on 39 guests (~85 min, run beside HoltTest1All); all 7 rescue images rebuilt | Nothing. Each guest matched its previous passing run ([B]/rel4st1/REPORT.md) |
| bko-holt development, 11-05/06 | 363 HoltTest1 guest-runs in 76 run directories ([W]/ht1-runs.tsv). Family-by-family dev loops, then 4 full sweeps (160 guest-runs). cargo test grew from 195 to 208 tests | Dev loops: yes, constantly. Sweeps: r1all passed 16 of 39 (expected gaps); r32 was stopped when the disk filled; r2all found the udev race at `close` (fixed in 1626714 and 2e6c1b5) and the GELI gap; r3all passed 40 of 40 and found nothing |
| New RAID/mirror guests, 11-07 | raid1–raid6, plus a 4-guest regression run | Yes: 78018db, 3671e96, 5426d2e |
| Rescue-image content and bko-holt version bumps | `img-one.sh` rebuilds all 7 images and boot-tests them (Linux in 4 modes with 35 checks, the others BIOS and UEFI with 20–24 checks). The checks include `bko-holt --version` but no load. The Linux image was built at least 4 times between 11-05 and 11-07 (`images/linux/prev-*`) | All passed. No test has ever run a load from a real rescue image |
| New guests, 11-05 | Targeted SticTest1 runs gt1-fix, -fix4, -fix4b, -fix89, -reg (2–5 guests each) | Yes: adjustments to new checks, and the freebsd14 libc problem |
| The guest fleet as a whole | GuestTest1 #1 (11-05) and #2 (11-06/07). #2 reused rel4's SticTest1 results instead of running its own round ([G]/gt2/REPORT.md) | Guest defects both times, found in the `--try` logs; fewer the second time |
| Guest OS updates | 11-03: `rerun-u2` ran 15 guests with round 23's binaries held fixed, so that "a FAIL is the guest update's doing". 5 exited 1; rerun with roundab1's binaries, 4 of those passed. 11-06: r2all, rel4 and GuestTest1 #2 happened to run soon after. 11-07: st1-rel4g (3 guests) and HoltTest1 r4u/r5n | 11-06: nothing attributable to the update. 11-07: nixos2605 exposed a judge.py defect (fixed in 138097c) |
| Harness changes | 39c9416 (touched 30 checks) counted st2a as its test. rel4n ran only the 2 new checks. Two judge.py rules (4218f97, 37e0f63) were followed by a whole r3all | Harness faults showed up in the harness's own runs |

**What found defects since 11-03:** every tool or build defect came from a new guest or layout (freebsd14; the RAID/mirror guests) or from a new kind of test (the whole-machine restore; the first sweep on one committed bko-holt). Re-running unchanged guests found only flakes and harness faults.

**Full re-runs whose inputs matched the previous pass:**
- SticTest1 st1d, st2a, rel1 and gt1-rel2: about 2 h 55 min of full passes (an estimate, from the reported durations).
- HoltTest1 r3all's 11 non-Linux guests ran the same bko-holt build (ae2ffd3, which reports itself as 0.2.0) as in r2all. Their images were last updated at 08:52–09:30Z, before r2all ran them; haiku6's update at 11:28Z is borderline. That is 63 of r3all's 191 slot-minutes (from the queue runner logs), and only judge.py changed in between.

## (c) Change-to-test mappings

### `testvm/compare/affected.sh`

**Its rules:**
- Its output is run-all.sh's 7 platforms (line 42). Its list of tested OSes has no Haiku (line 43).
- Docs and CI files map to nothing (lines 138–139).
- restic: test and doc files map to nothing. A `.go` or `.s` file is judged by its `//go:build` line and file name, per OS, on amd64 with CGO off (lines 87–132); a file every tested OS builds maps to all platforms. Any other file maps to all (line 151).
- rustic_core: `osattrs/NAME.rs` maps by its name (lines 157–169); anything else maps to all (line 170).
- rustic: `build-backotter.sh` maps to Linux; `src/` and `Cargo.*` map to all (lines 172–178).
- Harness: each testvm/compare script maps to its platform (lines 182–202). The rest of stick/, testvm/ and docs/ maps to nothing (205–206), and so does holt/ (207).

**What it gets right:** it defaults to testing everything when unsure, reads real build constraints, works on uncommitted ranges, and explains itself with `--explain`.

**Measured:** it picks all 7 platforms for 44 of 58 own restic commits, 48 of 56 rustic_core commits and 31 of 38 rustic commits. The rel3→rel4 ranges also pick all. It picks nothing for 106 of 175 backotter commits.

**What it misses:**
1. It names run-all.sh platforms. drive.py, the HoltTest1 drivers and GuestTest1 consume none of its output.
2. Haiku: `haiku.rs` and `haiku_attrs.rs` fall through to line 165 ("all Unix"), so they pick all 7 platforms and none of them is Haiku (checked with `--explain` on 5eba94f). `stick/haiku/build-rustic.sh` maps to nothing.
3. It has no filesystem axis. The preallocation rule maps to all platforms, but only 6 btrfs guests and 1 bcachefs guest exercise it.
4. A version-string bump (`global.go` in 5253b20ac and b74535444) maps to all.
5. A Cargo.lock pin bump maps to all; it never reads the rustic_core range being pinned.
6. Files every SticTest1 check depends on map to nothing: `cmp-run.sh` (used by 41 checks), `guest-slot.sh` (40), each guest's own `GUEST/` scripts, `vm-mount` and `console-boot.py`. `fsmeta.py` maps to "linux zfsguests", though 24 Linux guests use it.
7. holt/, holttest1d/, the image scripts, update/ and vm-update map to nothing. Guest images, hosts and rescuers are invisible to it.

### drive.py

- Usage is `ROUND [GUEST…]`. The default is every directory with a check.sh, minus `--exclude` (lines 10–11); `--first` sets the order.
- History is used only to order guests by duration. A guest already started under a mark is never rerun.
- It already knows each guest's binary OS: 28 Linux, 7 FreeBSD-family, 2 NetBSD-family, and one each OpenBSD, DragonFly, illumos and Haiku. Haiku reads its repository with Linux's restic-bko (line 111).
- Before a round it compares the hosts' check scripts with the Mac's by md5 (lines 338, 582). Nothing else about inputs is checked.

### HoltTest1 families

- `testvm/holttest1d/README.md` lines 28–41 are a hand-kept map from OS family to guests. Development commits name the family test that confirmed them (3b74f70: 1M; a0c5852: 1A).
- Runs are fed from a hand-written `queue/RUN/todo`. `ht1-queue.sh` exists only on the hosts, not in the repo.
- README line 178 gives a manual phase rule: after a bko-holt fix "`rebuild verify` is enough, unless the fix changed what `dump` writes". `ht1.sh` lines 9–10 reuse earlier phases.

### Input-aware pieces that already exist

- **build-round.sh's digest store:** each binary's `NAME.digest` lists its sources, toolchain, profile, build scripts, and the build image's size and mtime. Builds with an unchanged digest show as "reused".
- **compare.py's `base` record:** base disks by path, size and mtime; an earlier run's tool results are kept only for the same disks (compare.py lines 1552–1557, 1695–1705).
- **testvm/audit:** `audited-heads` plus `to-audit.sh` keep a per-fork ledger of the last audited commit.
- **run-all.sh's `EXPECT_*`:** it refuses binaries that aren't the expected commits.

### Mappings that don't exist

- **bko-holt changes → guests.** A feature index would be cheap to build: the 40 r3all dumps (2.9 MB) give each guest's filesystems (XFS on 5 guests, btrfs 6, ZFS 8, vfat 7), md (2), LVM (2), dm-crypt (3), UEFI (8), Secure Boot (4), boot loader and OS id. With git's Rust diff driver, hunk headers name the changed load function: mount 6 times, format, boot and pool 4 each, luks and restore 3 each. The repo has no `.gitattributes`, so most hunks read `impl Builder<'_>` instead.
- **Rescue-image changes → tests:** only the boot test exists.
- **Guest update or host change → tests:** nothing. vm-update's `image-state` generation is read by no test driver.
- **Shared helper → guests:** computable by grep. cmp-run.sh 41 guests, guest-slot.sh 40, rt-common.sh 38, vm-lib.sh 38, vm-run 33, fsmeta.py 24, torture.sh 17, nonroot-rt.sh 10.

## (d) What each run depends on, and whether it's recorded

| Input | SticTest1 | HoltTest1 | GuestTest1 | Image build |
|---|---|---|---|---|
| Archivers | The `bin` path only. The round directory holds commit files, SHA256SUMS and `.digest` files | Not recorded. `bin/rustic-bko` is a fast build from 11-05 22:39 (34,190,688 B), not the shipped rel4 binary (27,735,200 B) | Taken from SticTest1 | Recorded |
| bko-holt | — | Only the dump's `tool.version`. Version 0.2.0 covered 7 commits. `bin/bko-holt` is overwritten in place (latest at 11-07 10:09) | — | Recorded (holt-bin COMMIT) |
| Check scripts | Only in the Mac's driver log, and only with `--private-copy` | Not recorded | Not recorded | stick/ commit not recorded |
| Shared helpers | Not recorded; they come from the synced tree, which can hold uncommitted files | Not recorded | Not recorded | — |
| Guest image | Not recorded (`image-state` exists but isn't linked) | Not recorded | Recorded (`--status`) | — |
| Rescuer | — | Not recorded (`.sha256` files sit beside the images) | — | — |
| Host kernel, ZFS, QEMU | Not recorded | Not recorded (only incidentally, in the guest's DMI) | Not recorded | Only the Linux kernel |
| Upstream mirrors | — | — | `update.log`, overwritten by each `--try` | — |

Every input a skip rule would need can be computed today, but none of them is stored with the result.

## Levers

1. Key test results the way build-round.sh keys builds: binary digests, image sha256, check and helper hashes, host versions and rescuer hash.
2. When only judge.py changes, re-judge the stored dumps and fsmeta files instead of re-running the VMs. Each r3all guest kept `src-meta.json` and `dst-meta.json`, about 118 MB each.
3. Select by filesystem and by function, using the dump feature index.
4. Spend VM time on new guests, layouts and test kinds; those found every defect since 11-03.
5. HoltTest1 never tests the shipped rustic-bko or a rescue image.
6. For context: the hosts' SSDs take about 1.3 TB of writes a day each (5d1b66f).

## Gaps in this evidence

- The pre-11-04 SticTest1 and run-all.sh rounds use older drivers and aren't indexed; their counts come from the reports.
- Timezones were converted by hand (commit times +0100, host files CET, runs UTC). Times may slip by an hour.
- The 11-06 update round is inferred from `image-state` mtimes; the update logs are overwritten by later `--try` runs.
- The 5 failures in `rerun-u2` were not traced to a cause.
- Some `results.json` files have no PASS count, so comparisons between rounds rest on exit codes where counts are missing.
- I did not check whether the difference between HoltTest1's rustic-bko and the shipped one matters.
- I did not measure write volume per test kind; the wear log only started on 11-07.
- The line counts include bulk imports and renames.

Working files ([W] = `/private/tmp/claude-501/-Users-mchobbit-git-backotter/6e7b60cc-f873-4450-892c-1b053e7a5606/scratchpad/t2/change/`):
- `[W]/st1-runs.tsv` (199 SticTest1 guest-runs since 11-04)
- `[W]/ht1-runs.tsv` (363 HoltTest1 guest-runs)
- `[W]/bko-numstat.txt`, `[W]/restic-numstat.txt`, `[W]/rustic_core-numstat.txt`, `[W]/rustic-numstat.txt`
- `[W]/affected-harness.tsv`
- `[W]/r3all-runner.log`
- `[W]/dumps/` (the 40 r3all source dumps)